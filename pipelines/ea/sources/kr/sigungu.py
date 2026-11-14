import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.kr import local
from ea.sources.kr.na import _number, _numeric, table
from ea.sources.kr.nec import BASE, PARTIES_QUERY, REPORT, SIDO, _bindings, _get
from ea.store import DATA, connect

SOURCE = "kr.sigungu"
PARSED = DATA / "parsed" / SOURCE
DATES = local.DATES
ELECTION_CODE = "6"
SCHEME = "kr.sigungu_council_constituency"


def _form(kind: str, day: str, city: str) -> dict[str, str]:
    return {**local._form(kind, day, city), "electionCode": ELECTION_CODE}


def key(kind: str, day: str, city: str) -> str:
    return f"{REPORT}?{urlencode(_form(kind, day, city))}"


def cities(day: str) -> list[tuple[str, str]]:
    url = local._selector(day).replace(f"subElectionCode={local.ELECTION_CODE}", f"subElectionCode={ELECTION_CODE}")
    if url not in raw.keys(SOURCE):
        raw.store(SOURCE, url, _get(url), suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
    import json

    body = json.loads(raw.cached(SOURCE, url).path.read_text())["jsonResult"]["body"]
    return [(str(city["CODE"]), str(city["NAME"])) for city in body]


def _job(job: tuple[str, str, str]) -> None:
    address = key(*job)
    if address in raw.keys(SOURCE):
        return
    for _ in range(10):
        content = _get(REPORT, _form(*job))
        if 'id="table01"' in content.decode():
            raw.store(SOURCE, address, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
            return
        time.sleep(3)
    raise ValueError(f"{job}: no table")


def fetch() -> None:
    jobs = [(kind, day, city) for day in DATES for city, _ in cities(day) for kind in ("winners", "count")]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(_job, jobs))


def _counts(day: str, city: str, html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results, tickets = [], []
    name = ""
    head: list[str] = []
    slots = 0
    seen: set[tuple[str, str]] = set()
    for row in table(html):
        if len(row) > 6 and row[0] and not row[1] and not any(_numeric(cell) for cell in row[2:]) and "계" in row:
            name = row[0]
            valid = row.index("계")
            head = list(row[4:valid])
            slots = valid - 4
            continue
        if len(row) > 6 and not row[0] and row[1] and row[1] != "합계" and _numeric(row[2]) and head:
            valid = 4 + slots
            if len(row) < valid + 3 or not _numeric(row[valid]):
                raise ValueError(f"{day} {city} {name}: row {row}")
            slate = [index for index, cell in enumerate(head) if cell]
            if (row[1], name) in seen:
                raise ValueError(f"{day} {city} {row[1]} {name}: counted twice")
            seen.add((row[1], name))
            results.append(
                {
                    "day": day,
                    "city": city,
                    "town": row[1],
                    "sgg": name,
                    "registered": _number(row[2]),
                    "ballots": _number(row[3]),
                    "votes": [_number(row[4 + index]) if row[4 + index] else 0 for index in slate],
                    "valid": _number(row[valid]),
                    "invalid": _number(row[valid + 1]),
                }
            )
            tickets.extend(
                {"day": day, "city": city, "town": row[1], "sgg": name, "ticket": order, "party": party, "name": person}
                for order, (party, person) in enumerate((local._split(head[index]) for index in slate), 1)
            )
    return results, tickets


def _winners(day: str, city: str, html: str) -> list[dict[str, Any]]:
    found = []
    columns: dict[str, int] = {}
    for row in table(html):
        if "구시군명" in row:
            columns = {cell.split(" ")[0]: index for index, cell in enumerate(row)}
            continue
        if not columns or len(row) != len(columns):
            continue
        votes = row[columns["득표수"]]
        found.append(
            {
                "day": day,
                "city": city,
                "town": row[columns["구시군명"]],
                "sgg": row[columns["선거구명"]],
                "party": row[columns["정당명"]] if "정당명" in columns else "",
                "name": re.sub(r"\s*\(.*\)$", "", row[columns["성명"]]),
                "birth": row[columns["생년월일"]].split(" ")[0],
                "votes": None if votes == "무투표당선" else _number(votes.split(" ")[0]),
            }
        )
    return found


def parse() -> None:
    results, tickets, winners = [], [], []
    for day in DATES:
        for city, _ in cities(day):
            found, slate = _counts(day, city, raw.cached(SOURCE, key("count", day, city)).path.read_text())
            results.extend(found)
            tickets.extend(slate)
            winners.extend(_winners(day, city, raw.cached(SOURCE, key("winners", day, city)).path.read_text()))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(tickets).write_parquet(PARSED / "tickets.parquet")
    pl.DataFrame(winners).write_parquet(PARSED / "winners.parquet")
    print(
        pl.DataFrame(winners).group_by("day").agg(pl.len(), pl.col("votes").is_null().sum().alias("tacit")).sort("day")
    )
    print(pl.DataFrame(results).group_by("day").agg(pl.len(), pl.col("town").n_unique()).sort("day"))


VERSION = "1"
PUBLISHER = local.PUBLISHER
TITLE = "선거통계시스템: 역대선거 지방선거 구·시·군의회의원선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 지방선거 구·시·군의회의원선거 당선인 명부"
NOTE = (
    "Constituency seats only: the proportional seats are not loaded here. In the 1995, 1998, and 2002 elections each "
    "constituency (a neighborhood, township, or similar unit) elected one member without party labels on the ballot; "
    "from 2006, constituencies elect two to four members with party labels and single non-transferable votes (Public "
    "Official Election Act, art. 26). Constituencies whose candidates stood unopposed are recorded as elected without "
    "a count, as the National Election Commission lists them (무투표당선)."
)


def _municipality(town: str) -> str:
    town = re.sub(r"\(.*\)$", "", town)
    return re.sub(r"(?<=[시군구])[갑을병정무]$", "", town)


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    tickets = pl.read_parquet(PARSED / "tickets.parquet")
    winners = pl.read_parquet(PARSED / "winners.parquet")
    party_items: dict[str, list[tuple[str, str | None, str | None, str | None]]] = {}
    for row in _bindings(PARTIES_QUERY):
        party_items.setdefault(row["label"], []).append(
            (row["party"].rsplit("/", 1)[1], row.get("english"), row.get("start"), row.get("end"))
        )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "South Korea's city, county, and district council constituency elections 1995–2026 from the "
                "National Election Commission's statistics system: candidates and votes by constituency, with the "
                "winners' list for uncontested seats"
            ),
            polity="KR",
        )

        def name(entity: UUID, text: str, lang: str = "ko", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        korea = cs.polity("KR")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=local.LANDING,
            license=local.LICENSE,
            license_url=BASE,
            notes="개표현황 by constituency (선거구) with the registered voters, ballots cast, each candidate's votes, "
            "and invalid ballots, one page per metropolitan city or province.",
        )
        winners_source = cs.source(PUBLISHER, WINNERS_TITLE)
        cs.add(
            "source",
            id=winners_source,
            kind="official_body",
            publisher=PUBLISHER,
            title=WINNERS_TITLE,
            url=local.WINNERS_LANDING,
            license=local.LICENSE,
            license_url=BASE,
        )
        for address in raw.keys(SOURCE):
            if "electionInfo_report" in address:
                cs.artifact(raw.cached(SOURCE, address), winners_source if "EPEI01" in address else source)

        cs.add(
            "scheme",
            code=SCHEME,
            polity_id=korea,
            kind="electoral_district",
            rank=4,
            noun="constituency",
            kinds=None,
            code_system=None,
        )
        sidos = {code: cs.area("kr.sido", code) for code in SIDO}
        tops: dict[str, UUID] = {}
        for code in SIDO:
            tops[code] = cs.unit("kr.sido", code)

        systems: dict[str, UUID] = {}
        for code, (rules, english, text) in {
            "fptp": (
                {"formula": "fptp"},
                "City, county, and district council constituencies, plurality of the valid votes",
                (
                    "Each constituency elects one member, and the candidate with the most valid votes wins (Public "
                    "Official Election Act, art. 188)."
                ),
            ),
            "sntv": (
                {"formula": "sntv"},
                "City, county, and district council multi-member constituencies, single non-transferable vote",
                (
                    "Each voter names one candidate, and the candidates with the most valid votes fill the "
                    "constituency's seats."
                ),
            ),
        }.items():
            systems[code] = cs.key(f"{SOURCE}.electoral_system", code)
            cs.add("electoral_system", id=systems[code], polity_id=korea, rules=rules, rules_version=1)
            name(systems[code], english, "en", "english")
            cs.add("note", entity_id=systems[code], kind="methodology", body_md=text)
        heads = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'kr.sigungu_head.polity'"
            )
        }
        bodies: dict[tuple[str, str], UUID] = {}
        parties: dict[tuple[str, str], UUID] = {}
        minted_parties: set[UUID] = set()

        def party(text: str, day: str) -> UUID:
            if (text, day) not in parties:
                held = f"{day[:4]}-{day[4:6]}-{day[6:]}"
                found = [
                    item
                    for item, _, start, end in party_items.get(text, [])
                    if (start is None or start[:10] <= held) and (end is None or end[:10] >= held)
                ]
                address = ("wikidata", found[0]) if len(set(found)) == 1 else (f"{local.SOURCE}.party", f"{text}:{day}")
                entity = parties[text, day] = cs.key(*address)
                if entity in cs.minted and entity not in minted_parties:
                    minted_parties.add(entity)
                    cs.add("party", id=entity, kind="party", polity_id=korea)
                    name(entity, text)
                    english = next((e for item, e, _, _ in party_items.get(text, []) if address[1] == item and e), None)
                    if english:
                        name(entity, english, "en", "english")
            return parties[text, day]

        units: dict[str, UUID] = {}
        mismatched = 0
        noted = False
        for number, day in enumerate(DATES, 1):
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            election = cs.key("kr.local.election", day)
            if election in cs.minted:
                raise ValueError(f"{day}: the local election is not loaded")
            path = f"kr/{held}-local"
            elected = winners.filter(pl.col("day") == day)
            slate = tickets.filter(pl.col("day") == day)
            counted = results.filter(pl.col("day") == day)
            order: dict[str, int] = {}
            for city, town, sgg in elected.select("city", "town", "sgg").unique(maintain_order=True).rows():
                sido = local._sido(city)
                place = _municipality(town)
                where = f"{day}:{sido}:{town}:{sgg}"
                won = elected.filter((pl.col("city") == city) & (pl.col("town") == town) & (pl.col("sgg") == sgg))
                magnitude = won.height
                tacit = won["votes"][0] is None
                match = (pl.col("city") == city) & (pl.col("town") == town) & (pl.col("sgg") == sgg)
                rows = counted.filter(match)
                names = slate.filter(match).sort("ticket")
                if tacit != rows.is_empty() or tacit != names.is_empty() or rows.height > 1:
                    raise ValueError(f"{where}: tacit {tacit}, {rows.height} count rows, {names.height} candidates")
                if (sido, place) not in bodies:
                    polity = heads.get(f"{sido}/{place}") or cs.key(f"{SOURCE}.polity", f"{sido}:{place}")
                    if polity in cs.minted:
                        cs.add("polity", id=polity, kind="municipality", iso_code=None)
                        name(polity, place)
                        cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"KR-{sido}"))
                    body = bodies[sido, place] = cs.key(f"{SOURCE}.body", f"{sido}:{place}")
                    cs.add("body", id=body, polity_id=polity, kind="council")
                    name(body, f"{place}의회")
                order[sido] = order.get(sido, 0) + 1
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[sido, place],
                    magnitude=magnitude,
                    electoral_system_id=systems["sntv" if magnitude > 1 else "fptp"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                label = sgg if place in sgg else f"{place} {sgg}"
                name(contest, label)
                cs.add("slug", path=f"{path}/sigungu-council/{local._slug(sido)}-{order[sido]}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", where)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=winners_source if tacit else source,
                    is_default=True,
                    completeness=1,
                )
                if not noted:
                    noted = True
                    cs.add("note", entity_id=systems["fptp"], kind="caveat", body_md=NOTE)
                candidates = (
                    [(k, a, b) for k, (a, b) in enumerate(won.select("party", "name").rows(), 1)]
                    if tacit
                    else names.select("ticket", "party", "name").rows()
                )
                options: list[UUID] = []
                chosen = {
                    _elected(person, label, votes, candidates, rows)
                    for label, person, votes in won.select("party", "name", "votes").rows()
                }
                if len(chosen) != magnitude:
                    raise ValueError(f"{where}: {magnitude} winners match {chosen}")
                for ticket, party_name, person_name in candidates:
                    option = cs.key(f"{SOURCE}.option", f"{where}:{ticket}")
                    options.append(option)
                    independent = party_name == "무소속"
                    member = None if independent or not party_name else party(party_name, day)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=ticket,
                        ballot_label=person_name,
                    )
                    person = cs.key(f"{SOURCE}.person", f"{where}:{person_name}")
                    cs.add("person", id=person)
                    name(person, person_name)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{where}:{ticket}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=person_name,
                        member_of_party_id=member,
                        is_independent=independent,
                    )
                    victor = ticket in chosen
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if victor else "lost",
                        seats=1 if victor else None,
                    )
                    if victor and not tacit:
                        declared = won.filter(pl.col("name") == person_name)["votes"][0]
                        total = rows["votes"][0][ticket - 1]
                        if total != declared:
                            cs.add(
                                "note",
                                entity_id=result_set,
                                kind="caveat",
                                body_md=f"The winners' list prints {declared:,} votes for {person_name}; the "
                                f"count page prints {total:,}, which is the figure stored.",
                            )
                            mismatched += 1
                if tacit:
                    continue
                if where in units:
                    raise ValueError(f"{where}: repeated")
                target = units[where] = cs.unit(SCHEME, where)
                if target in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=target,
                        polity_id=korea,
                        scheme_code=SCHEME,
                        code=where,
                        parent_id=tops[sido],
                        area_relation="within",
                        area_id=sidos[sido],
                        kind="standard",
                        valid_from=None,
                    )
                    name(target, label)
                record = rows.to_dicts()[0]
                for measure, option, value in (
                    (2, None, record["registered"]),
                    (3, None, record["ballots"]),
                    (4, None, record["valid"]),
                    (5, None, record["invalid"]),
                    *((1, options[k], v) for k, v in enumerate(record["votes"])),
                ):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=target,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        print(f"{mismatched} winners whose votes differ between the winners' list and the count")
        return cs.write()


def _elected(
    person: str, party: str, votes: int | None, candidates: list[tuple[int, str, str]], rows: pl.DataFrame
) -> int:
    named = [ticket for ticket, _, name in candidates if name == person]
    if len(named) > 1:
        named = [ticket for ticket in named if candidates[ticket - 1][1] == party]
    if len(named) > 1 and not rows.is_empty():
        named = [ticket for ticket in named if rows["votes"][0][ticket - 1] == votes]
    if len(named) != 1:
        raise ValueError(f"{party} {person}: matches {named} among {candidates}")
    return named[0]
