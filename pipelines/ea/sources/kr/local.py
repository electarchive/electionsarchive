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
from ea.sources.kr.na import _number, _numeric, table
from ea.sources.kr.nec import BASE, PARTIES_QUERY, REPORT, SIDO, _bindings, _get
from ea.store import DATA, connect

SOURCE = "kr.local"
PARSED = DATA / "parsed" / SOURCE
DATES = (
    "19950627",
    "19980604",
    "20020613",
    "20060531",
    "20100602",
    "20140604",
    "20180613",
    "20220601",
    "20260603",
)
ELECTION_CODE = "5"


def _old(day: str) -> bool:
    return day <= "20020613"


def _selector(day: str, code: str = ELECTION_CODE) -> str:
    suffix = "GuOld" if _old(day) else "Old"
    query = {"electionId": "0000000000", "electionCode": day, "subElectionCode": code}
    return f"{BASE}/bizcommon/selectbox/selectbox_cityCodeBySgJson_{suffix}.json?{urlencode(query)}"


def _form(kind: str, day: str, city: str, code: str = ELECTION_CODE) -> dict[str, str]:
    top, menu, page = ("EP", "EPEI01", "ep/epei01.jsp") if kind == "winners" else ("VC", "VCCP09", "vc/vccp09.jsp")
    if kind == "winners":
        statement = "EPEI01_#91" if _old(day) else "EPEI01_#1"
    else:
        statement = "VCCP09_#2"
    return {
        "electionId": "0000000000",
        "requestURI": f"/electioninfo/0000000000/{page}",
        "topMenuId": top,
        "secondMenuId": menu,
        "menuId": menu,
        "statementId": statement,
        "oldElectionType": "0" if _old(day) else "",
        "electionType": "4",
        "electionName": day,
        "electionCode": code,
        "cityCode": city,
        "townCode": "-1",
        "sggCityCode": "-1",
        "sggTownCode": "-1",
    }


def key(kind: str, day: str, city: str) -> str:
    return f"{REPORT}?{urlencode(_form(kind, day, city))}"


def _store(address: str, content: bytes, suffix: str, media: str) -> None:
    raw.store(SOURCE, address, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def cities(day: str) -> list[tuple[str, str]]:
    url = _selector(day)
    if url not in raw.keys(SOURCE):
        _store(url, _get(url), ".json", "application/json")
    body = json.loads(raw.cached(SOURCE, url).path.read_text())["jsonResult"]["body"]
    return [(str(city["CODE"]), str(city["NAME"])) for city in body]


def _job(job: tuple[str, str, str]) -> None:
    address = key(*job)
    if address in raw.keys(SOURCE):
        return
    for _ in range(10):
        content = _get(REPORT, _form(*job))
        if 'id="table01"' in content.decode():
            _store(address, content, ".html", "text/html")
            return
        time.sleep(3)
    raise ValueError(f"{job}: no table")


def fetch() -> None:
    jobs = [(kind, day, city) for day in DATES for city, _ in cities(day) for kind in ("winners", "count")]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(_job, jobs))


def _split(text: str) -> tuple[str, str]:
    party, _, person = text.partition(" ")
    return (party, person.replace(" ", "")) if person else ("", party)


def _counts(day: str, city: str, html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results, tickets = [], []
    name = ""
    head: list[str] = []
    slots = 0
    for row in table(html):
        if len(row) > 6 and row[0] and not row[1] and not any(_numeric(cell) for cell in row[2:]):
            name = row[0]
            valid = row.index("계")
            head = [cell for cell in row[4:valid]]
            slots = valid - 4
            continue
        if len(row) > 6 and not row[0] and row[1] and _numeric(row[2]) and head:
            valid = 4 + slots
            names = [cell for cell in head if cell]
            if len(row) < valid + 3 or not _numeric(row[valid]):
                raise ValueError(f"{day} {city} {name}: row {row}")
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
            if row[1] == "합계":
                tickets.extend(
                    {"day": day, "city": city, "sgg": name, "ticket": order, "party": party, "name": person}
                    for order, (party, person) in enumerate(map(_split, names), 1)
                )
    return results, tickets


def _winners(day: str, city: str, html: str) -> list[dict[str, Any]]:
    found = []
    for row in table(html)[1:]:
        if len(row) != 10:
            continue
        votes = row[9]
        found.append(
            {
                "day": day,
                "city": city,
                "town": row[0],
                "sgg": row[1],
                "party": row[2],
                "name": re.sub(r"\s*\(.*\)$", "", row[3]),
                "birth": row[5].split(" ")[0],
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
    print(pl.DataFrame(tickets).group_by("day").agg(pl.col("sgg").n_unique()).sort("day"))


VERSION = "1"
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "선거통계시스템: 역대선거 지방선거 시·도의회의원선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 지방선거 시·도의회의원선거 당선인 명부"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP09"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
LICENSE = "No license stated: public election statistics of the National Election Commission; facts published with attribution"
PROVINCES = {"5100": "50", "5200": "42", "5300": "45"}
NOTE = (
    "Constituency seats only: each constituency elects one member by plurality of the valid votes (Public Official "
    "Election Act, art. 188). The proportional seats are not loaded here. Constituencies whose candidate stood "
    "unopposed are recorded as elected without a count, as the National Election Commission lists them (무투표당선)."
)


def _sido(city: str) -> str:
    return PROVINCES.get(city, city[:2])


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
                "South Korea's metropolitan and provincial council constituency elections 1995–2026 from the "
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
            url=LANDING,
            license=LICENSE,
            license_url=BASE,
            notes="개표현황 by constituency (선거구) with the registered voters, ballots cast, each candidate's votes, "
            "and invalid ballots, one page per metropolitan city or province; each constituency's parts are "
            "checked against its printed total.",
        )
        winners_source = cs.source(PUBLISHER, WINNERS_TITLE)
        cs.add(
            "source",
            id=winners_source,
            kind="official_body",
            publisher=PUBLISHER,
            title=WINNERS_TITLE,
            url=WINNERS_LANDING,
            license=LICENSE,
            license_url=BASE,
        )
        for address in raw.keys(SOURCE):
            if "electionInfo_report" in address:
                cs.artifact(raw.cached(SOURCE, address), winners_source if "EPEI01" in address else source)

        cs.add(
            "scheme",
            code="kr.sido_council_constituency",
            polity_id=korea,
            kind="electoral_district",
            rank=3,
            noun="constituency",
            kinds=None,
            code_system=None,
        )
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

        systems: dict[str, UUID] = {}
        for code, (rules, english, text) in {
            "fptp": (
                {"formula": "fptp"},
                "Metropolitan and provincial council constituencies, plurality of the valid votes",
                (
                    "Each constituency elects one member, and the candidate with the most valid votes wins (Public "
                    "Official Election Act, art. 188)."
                ),
            ),
            "sntv": (
                {"formula": "sntv"},
                "Metropolitan and provincial council multi-member constituencies, single non-transferable vote",
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
        bodies: dict[str, UUID] = {}
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
                address = ("wikidata", found[0]) if len(set(found)) == 1 else (f"{SOURCE}.party", f"{text}:{day}")
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
            path = f"kr/{held}-local"
            cs.add("slug", path=path, entity_id=election)
            elected = winners.filter(pl.col("day") == day)
            slate = tickets.filter(pl.col("day") == day)
            counted = results.filter(pl.col("day") == day)
            order: dict[str, int] = {}
            for city, sgg, town in elected.select("city", "sgg", "town").unique(maintain_order=True).rows():
                sido = _sido(city)
                where = f"{day}:{sido}:{sgg}"
                won = elected.filter((pl.col("city") == city) & (pl.col("sgg") == sgg))
                magnitude = won.height
                tacit = won["votes"][0] is None
                rows = counted.filter((pl.col("city") == city) & (pl.col("sgg") == sgg))
                names = slate.filter((pl.col("city") == city) & (pl.col("sgg") == sgg)).sort("ticket")
                if tacit != rows.is_empty() or tacit != names.is_empty():
                    raise ValueError(f"{where}: tacit {tacit}, {rows.height} count rows, {names.height} candidates")
                if sido not in bodies:
                    bodies[sido] = cs.key(f"{SOURCE}.body", sido)
                    cs.add("body", id=bodies[sido], polity_id=cs.polity(f"KR-{sido}"), kind="council")
                    name(bodies[sido], f"{SIDO[sido][1]}의회")
                    name(bodies[sido], f"{SIDO[sido][2]} Council", "en", "english")
                order[sido] = order.get(sido, 0) + 1
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[sido],
                    magnitude=magnitude,
                    electoral_system_id=systems["sntv" if magnitude > 1 else "fptp"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, sgg)
                cs.add("slug", path=f"{path}/sido-council/{_slug(sido)}-{order[sido]}", entity_id=contest)
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
                    cs.add("note", entity_id=systems["fptp"], kind="caveat", body_md=NOTE)
                candidates = (
                    [(k, a, b) for k, (a, b) in enumerate(won.select("party", "name").rows(), 1)]
                    if tacit
                    else names.select("ticket", "party", "name").rows()
                )
                options: list[UUID] = []
                chosen = {_elected(person, label, candidates) for label, person in won.select("party", "name").rows()}
                if len(chosen) != magnitude:
                    raise ValueError(f"{where}: {magnitude} winners match {chosen}")
                for ticket, party_name, person_name in candidates:
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
                        total = rows.filter(pl.col("town") == "합계")["votes"][0][ticket - 1]
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
                top = unit("kr.sido", sido, None, sidos[sido], SIDO[sido][1])
                place = unit("kr.sido_council_constituency", where, top, sidos[sido], sgg)
                records = rows.to_dicts()
                parts = [record for record in records if record["town"] != "합계"]
                total = next(record for record in records if record["town"] == "합계")
                if len(parts) > 1:
                    for field in ("registered", "ballots", "valid", "invalid"):
                        if sum(part[field] for part in parts) != total[field]:
                            print(f"{where}: parts' {field} do not add to {total[field]}")
                    if [sum(column) for column in zip(*(part["votes"] for part in parts), strict=True)] != total[
                        "votes"
                    ]:
                        print(f"{where}: parts' votes do not add to the total")
                shown = [(place, total)]
                if len(parts) > 1:
                    shown.extend(
                        (
                            unit(
                                "kr.sido_council_constituency",
                                f"{where}/{part['town']}",
                                place,
                                sidos[sido],
                                part["town"],
                            ),
                            part,
                        )
                        for part in parts
                    )
                for target, record in shown:
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


def _slug(sido: str) -> str:
    return SIDO[sido][2].lower().replace(" ", "-")


def _elected(person: str, party: str, candidates: list[tuple[int, str, str]]) -> int:
    named = [ticket for ticket, _, name in candidates if name == person]
    if len(named) != 1:
        named = [ticket for ticket, label, name in candidates if name == person and label == party]
    if len(named) != 1:
        raise ValueError(f"{party} {person}: matches {named} among {candidates}")
    return named[0]
