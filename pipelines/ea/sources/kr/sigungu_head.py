import json
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

SOURCE = "kr.sigungu_head"
PARSED = DATA / "parsed" / SOURCE
DATES = local.DATES
CODE = "4"
VERSION = "1"
PUBLISHER = local.PUBLISHER
TITLE = "선거통계시스템: 역대선거 지방선거 구·시·군의 장선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 지방선거 구·시·군의 장선거 당선인 명부"
LANDING = local.LANDING
WINNERS_LANDING = local.WINNERS_LANDING
NOTE = (
    "Each city, county, or autonomous district elects its head by plurality of the valid votes (Public Official "
    "Election Act, art. 188). Contests where the only candidate stood unopposed are recorded as elected without a "
    "count, as the National Election Commission lists them (무투표당선)."
)


def key(kind: str, day: str, city: str) -> str:
    return f"{REPORT}?{urlencode(local._form(kind, day, city, CODE))}"


def _store(address: str, content: bytes, suffix: str, media: str) -> None:
    raw.store(SOURCE, address, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def cities(day: str) -> list[tuple[str, str]]:
    url = local._selector(day, CODE)
    if url not in raw.keys(SOURCE):
        _store(url, _get(url), ".json", "application/json")
    body = json.loads(raw.cached(SOURCE, url).path.read_text())["jsonResult"]["body"]
    return [(str(city["CODE"]), str(city["NAME"])) for city in body]


def _job(job: tuple[str, str, str]) -> None:
    address = key(*job)
    if address in raw.keys(SOURCE):
        return
    for _ in range(10):
        content = _get(REPORT, local._form(*job, CODE))
        if 'id="table01"' in content.decode():
            _store(address, content, ".html", "text/html")
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
    for row in table(html):
        if len(row) > 6 and row[0] and not row[1] and not any(_numeric(cell) for cell in row[2:]):
            name = row[0]
            valid = row.index("계")
            head = row[4:valid]
            slots = valid - 4
            continue
        if len(row) > 6 and not row[0] and row[1] and _numeric(row[2]) and head:
            valid = 4 + slots
            slate = [index for index, cell in enumerate(head) if cell]
            results.append(
                {
                    "day": day,
                    "city": city,
                    "sgg": name,
                    "town": row[1],
                    "registered": _number(row[2]),
                    "ballots": _number(row[3]),
                    "votes": [_number(row[4 + index]) if row[4 + index] else 0 for index in slate],
                    "valid": _number(row[valid]),
                    "invalid": _number(row[valid + 1]),
                }
            )
            tickets.extend(
                {"day": day, "city": city, "sgg": name, "ticket": order, "party": party, "name": person}
                for order, (party, person) in enumerate((local._split(head[index]) for index in slate), 1)
            )
            head = []
    return results, tickets


def _winners(day: str, city: str, html: str) -> list[dict[str, Any]]:
    found = []
    for row in table(html)[1:]:
        if len(row) != 9 or not row[0]:
            continue
        found.append(
            {
                "day": day,
                "city": city,
                "sgg": row[0],
                "party": row[1],
                "name": re.sub(r"\s*\(.*\)$", "", row[2]),
                "votes": None if row[8] == "무투표당선" else _number(row[8].split(" ")[0]),
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
    print(pl.DataFrame(results).group_by("day").agg(pl.len()).sort("day"))


def _title(town: str) -> str:
    return town + {"구": "청장", "시": "장", "군": "수"}.get(town[-1:], "장")


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
                "South Korea's city, county, and autonomous district head elections 1995–2026 from the National "
                "Election Commission's statistics system: candidates and votes by city, county, or district, with "
                "the winners' list for uncontested posts"
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
            url=LANDING,
            license=local.LICENSE,
            license_url=BASE,
            notes="개표현황 by city, county, or district (선거구) with the registered voters, ballots cast, each "
            "candidate's votes, and invalid ballots, one page per metropolitan city or province.",
        )
        winners_source = cs.source(PUBLISHER, WINNERS_TITLE)
        cs.add(
            "source",
            id=winners_source,
            kind="official_body",
            publisher=PUBLISHER,
            title=WINNERS_TITLE,
            url=WINNERS_LANDING,
            license=local.LICENSE,
            license_url=BASE,
        )
        for address in raw.keys(SOURCE):
            if "electionInfo_report" in address:
                cs.artifact(raw.cached(SOURCE, address), winners_source if "EPEI01" in address else source)

        sidos = {code: cs.area("kr.sido", code) for code in SIDO}
        units: dict[tuple[str, str], UUID] = {}

        def unit(scheme: str, code: str, parent: UUID | None, area: UUID, label: str) -> UUID:
            if (scheme, code) not in units:
                entity = units[scheme, code] = cs.unit(scheme, code)
                if entity in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=entity,
                        polity_id=korea,
                        scheme_code=scheme,
                        code=code,
                        parent_id=parent,
                        area_relation="equals" if scheme == "kr.sido" else "within",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(entity, label)
            return units[scheme, code]

        system = cs.key("kr.local.electoral_system", "fptp")
        polities: dict[str, UUID] = {}
        offices: dict[str, UUID] = {}

        def office(sido: str, town: str) -> UUID:
            code = f"{sido}/{town}"
            if code not in offices:
                polity = polities[code] = cs.key(f"{SOURCE}.polity", code)
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="municipality", iso_code=None)
                    name(polity, town)
                    cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"KR-{sido}"))
                offices[code] = cs.key(f"{SOURCE}.office", code)
                if offices[code] in cs.minted:
                    cs.add("office", id=offices[code], polity_id=polity, kind="mayor")
                    name(offices[code], _title(town))
            return offices[code]

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

        mismatched = 0
        for number, day in enumerate(DATES, 1):
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            election = cs.key("kr.local.election", day)
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=korea,
                    kind="local",
                    date_from=held,
                    date_to=held,
                    date_precision="day",
                    status="held",
                )
                name(election, f"제{number}회 전국동시지방선거")
                name(election, f"{held.year} South Korean local elections", "en", "english")
                cs.add("slug", path=f"kr/{held}-local", entity_id=election)
            path = f"kr/{held}-local"
            elected = winners.filter(pl.col("day") == day)
            slate = tickets.filter(pl.col("day") == day)
            counted = results.filter(pl.col("day") == day)
            order: dict[str, int] = {}
            for city, sgg in elected.select("city", "sgg").unique(maintain_order=True).rows():
                sido = local._sido(city)
                where = f"{day}:{sido}:{sgg}"
                town = re.sub(r"\((?:[^()]*)\)$", "", sgg).strip()
                won = elected.filter((pl.col("city") == city) & (pl.col("sgg") == sgg))
                if won.height != 1:
                    raise ValueError(f"{where}: {won.height} winners")
                tacit = won["votes"][0] is None
                rows = counted.filter((pl.col("city") == city) & (pl.col("sgg") == sgg))
                names = slate.filter((pl.col("city") == city) & (pl.col("sgg") == sgg)).sort("ticket")
                if tacit != rows.is_empty() or tacit != names.is_empty() or rows.height > 1:
                    raise ValueError(f"{where}: tacit {tacit}, {rows.height} count rows, {names.height} candidates")
                order[sido] = order.get(sido, 0) + 1
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office(sido, town),
                    magnitude=1,
                    electoral_system_id=system,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, _title(town) + "선거")
                cs.add("slug", path=f"{path}/sigungu-head/{local._slug(sido)}-{order[sido]}", entity_id=contest)
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
                if number == 1 and order[sido] == 1 and sido == "11":
                    cs.add("note", entity_id=contest, kind="caveat", body_md=NOTE)
                label, person_name = won.select("party", "name").row(0)
                candidates = [(1, label, person_name)] if tacit else names.select("ticket", "party", "name").rows()
                chosen = local._elected(person_name, label, candidates)
                options: list[UUID] = []
                for ticket, party_name, candidate in candidates:
                    option = cs.key(f"{SOURCE}.option", f"{where}:{ticket}")
                    options.append(option)
                    independent = party_name == "무소속"
                    member = None if independent else party(party_name, day)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=ticket,
                        ballot_label=candidate,
                    )
                    person = cs.key(f"{SOURCE}.person", f"{where}:{candidate}")
                    cs.add("person", id=person)
                    name(person, candidate)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{where}:{ticket}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        member_of_party_id=member,
                        is_independent=independent,
                    )
                    victor = ticket == chosen
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if victor else "lost",
                        seats=1 if victor else None,
                    )
                    if victor and not tacit:
                        declared = won["votes"][0]
                        total = rows["votes"][0][ticket - 1]
                        if total != declared:
                            cs.add(
                                "note",
                                entity_id=result_set,
                                kind="caveat",
                                body_md=f"The winners' list prints {declared:,} votes for {candidate}; the "
                                f"count page prints {total:,}, which is the figure stored.",
                            )
                            mismatched += 1
                if tacit:
                    continue
                top = unit("kr.sido", sido, None, sidos[sido], SIDO[sido][1])
                place = unit("kr.sigungu", f"{sido}/{town}", top, sidos[sido], town)
                record = rows.to_dicts()[0]
                if sum(record["votes"]) != record["valid"]:
                    print(f"{where}: votes do not add to {record['valid']}")
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
                        unit_id=place,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        print(f"{mismatched} winners whose votes differ between the winners' list and the count")
        return cs.write()
