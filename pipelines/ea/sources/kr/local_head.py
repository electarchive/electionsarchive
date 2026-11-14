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
from ea.sources.kr.na import _number, table
from ea.sources.kr.nec import BASE, PARTIES_QUERY, REPORT, SIDO, SIDO_CODES, _bindings, _get
from ea.store import connect

SOURCE = "kr.local_head"
PARSED = local.DATA / "parsed" / SOURCE
HEAD_CODE = "3"


def _form(kind: str, day: str, city: str) -> dict[str, str]:
    form = {**local._form(kind, day, city), "electionCode": HEAD_CODE}
    if kind == "winners":
        form["statementId"] = "EPEI01_#1"
    return form


def key(kind: str, day: str, city: str) -> str:
    return f"{REPORT}?{urlencode(_form(kind, day, city))}"


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


def _winner_city(day: str) -> str:
    return local.cities(day)[0][0]


def fetch() -> None:
    jobs = [("count", day, city) for day in local.DATES for city, _ in local.cities(day)]
    jobs += [("winners", day, _winner_city(day)) for day in local.DATES]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(_job, jobs))


def _candidates(html: str) -> list[tuple[str, str]]:
    start = html.index('id="table01"')
    head = html[start : html.index("</thead>", start)]
    found = []
    for text in re.findall(r"<strong>(.*?)</strong>", head, re.DOTALL):
        party, person = [part.strip() for part in text.split("<br>")]
        found.append((party, person.replace(" ", "")))
    return found


def _counts(day: str, city: str, html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slate = _candidates(html)
    width = 3 + len(slate) + 3
    results = []
    for row in table(html):
        if len(row) != width or not row[0] or not local._numeric(row[1]):
            continue
        results.append(
            {
                "day": day,
                "city": city,
                "town": row[0],
                "registered": _number(row[1]),
                "ballots": _number(row[2]),
                "votes": [_number(cell) for cell in row[3 : 3 + len(slate)]],
                "valid": _number(row[width - 3]),
                "invalid": _number(row[width - 2]),
            }
        )
    tickets = [
        {"day": day, "city": city, "ticket": order, "party": party, "name": person}
        for order, (party, person) in enumerate(slate, 1)
    ]
    return results, tickets


def _winners(day: str, html: str) -> list[dict[str, Any]]:
    found = []
    for row in table(html)[1:]:
        if len(row) != 9:
            continue
        found.append(
            {
                "day": day,
                "sido": row[0],
                "party": row[1],
                "name": re.sub(r"\s*\(.*\)$", "", row[2]),
                "birth": row[4].split(" ")[0],
                "votes": _number(row[8].split(" ")[0]),
            }
        )
    return found


def parse() -> None:
    results, tickets, winners = [], [], []
    for day in local.DATES:
        for city, _ in local.cities(day):
            found, slate = _counts(day, city, raw.cached(SOURCE, key("count", day, city)).path.read_text())
            results.extend(found)
            tickets.extend(slate)
        winners.extend(_winners(day, raw.cached(SOURCE, key("winners", day, _winner_city(day))).path.read_text()))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(tickets).write_parquet(PARSED / "tickets.parquet")
    pl.DataFrame(winners).write_parquet(PARSED / "winners.parquet")
    print(pl.DataFrame(winners).group_by("day").agg(pl.len()).sort("day"))
    print(pl.DataFrame(results).filter(pl.col("town") == "합계").group_by("day").agg(pl.len()).sort("day"))


VERSION = "1"
TITLE = "선거통계시스템: 역대선거 지방선거 시·도지사선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 지방선거 시·도지사선거 당선인 명부"
MERGED = "전남광주통합특별시"
MERGED_CODES = ("29", "46")
SYSTEM_NOTE = (
    "The candidate with the most valid votes is elected (Public Official Election Act, art. 187(1)); a candidate "
    "who stands unopposed is elected without a count (art. 190)."
)


def _head_sido(name: str) -> str:
    return "29+46" if name == MERGED else SIDO_CODES[name]


def _title(code: str) -> tuple[str, str, str]:
    if code == "29+46":
        return "mayor", f"{MERGED}장", "Mayor of Jeonnam-Gwangju Special City"
    _, full, english = SIDO[code]
    kind = "governor" if full.endswith("도") else "mayor"
    return (
        kind,
        f"{full}{'지사' if kind == 'governor' else '장'}",
        f"{'Governor' if kind == 'governor' else 'Mayor'} of {english}",
    )


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
                "South Korea's elections of the mayors of the special and metropolitan cities and the provincial "
                "governors 1995–2026 from the National Election Commission's statistics system: candidates and "
                "votes by city, county, and district, with the winners' list"
            ),
            polity="KR",
        )

        def name(entity: UUID, text: str, lang: str = "ko", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        korea = cs.polity("KR")
        source = cs.source(local.PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=local.PUBLISHER,
            title=TITLE,
            url=local.LANDING,
            license=local.LICENSE,
            license_url=BASE,
            notes="개표현황 by city, county, and district (구시군) with the registered voters, ballots cast, each "
            "candidate's votes, and invalid ballots, one page per metropolitan city or province; each page's parts "
            "are checked against its printed total and the winner's votes against the winners' list.",
        )
        winners_source = cs.source(local.PUBLISHER, WINNERS_TITLE)
        cs.add(
            "source",
            id=winners_source,
            kind="official_body",
            publisher=local.PUBLISHER,
            title=WINNERS_TITLE,
            url=local.WINNERS_LANDING,
            license=local.LICENSE,
            license_url=BASE,
        )
        for address in raw.keys(SOURCE):
            if "electionInfo_report" in address:
                cs.artifact(raw.cached(SOURCE, address), winners_source if "EPEI01" in address else source)

        scheme = "kr.sido_head_sigungu"
        cs.add(
            "scheme",
            code=scheme,
            polity_id=korea,
            kind="electoral_district",
            rank=4,
            noun="city, county, or district",
            kinds=None,
            code_system=None,
        )
        sidos = {code: cs.area("kr.sido", code) for code in SIDO}
        units: dict[str, UUID] = {}

        def unit(code: str, parent: UUID | None, area: UUID, label: str) -> UUID:
            if code not in units:
                entity = units[code] = cs.unit(scheme, code)
                if entity in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=entity,
                        polity_id=korea,
                        scheme_code=scheme,
                        code=code,
                        parent_id=parent,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(entity, label)
            return units[code]

        system = cs.key(f"{SOURCE}.electoral_system", "head")
        cs.add("electoral_system", id=system, polity_id=korea, rules={"formula": "fptp"}, rules_version=1)
        name(
            system,
            "Mayors and governors of the metropolitan cities and provinces, plurality of the valid votes",
            "en",
            "english",
        )
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        offices: dict[str, UUID] = {}
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

        for number, day in enumerate(local.DATES, 1):
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            election = cs.key("kr.local.election", day)
            if election in cs.minted:
                raise ValueError(f"{day}: the local election is not loaded")
            path = f"kr/{held}-local"
            elected = winners.filter(pl.col("day") == day)
            slate = tickets.filter(pl.col("day") == day)
            counted = results.filter(pl.col("day") == day)
            groups: dict[str, list[str]] = {}
            for city in counted["city"].unique(maintain_order=True):
                groups.setdefault(
                    "29+46" if day == "20260603" and city in ("2900", "4600") else local._sido(city), []
                ).append(city)
            for sido, cities in groups.items():
                won = elected.filter(pl.col("sido").map_elements(_head_sido, return_dtype=pl.String) == sido)
                if won.height != 1:
                    raise ValueError(f"{day} {sido}: {won.height} winners")
                names = slate.filter(pl.col("city") == cities[0]).sort("ticket")
                for other in cities[1:]:
                    if (
                        slate.filter(pl.col("city") == other).sort("ticket").select("party", "name").rows()
                        != names.select("party", "name").rows()
                    ):
                        raise ValueError(f"{day} {sido}: candidates differ between {cities}")
                where = f"{day}:{sido}"
                if sido not in offices:
                    kind, korean, english = _title(sido)
                    offices[sido] = cs.key(f"{SOURCE}.office", sido)
                    cs.add(
                        "office",
                        id=offices[sido],
                        polity_id=cs.polity(f"KR-{sido[:2]}"),
                        kind=kind,
                        body_id=None,
                    )
                    name(offices[sido], korean)
                    name(offices[sido], english, "en", "english")
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=offices[sido],
                    magnitude=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                _, korean, english = _title(sido)
                name(contest, f"{held.year} {korean}선거")
                name(contest, f"{held.year} {english} election", "en", "english")
                slug_name = "jeonnam-gwangju" if sido == "29+46" else _slug(sido)
                cs.add("slug", path=f"{path}/sido-head/{slug_name}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", where)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if sido == "29+46":
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="Gwangju and South Jeolla merged into the Jeonnam-Gwangju Special City for this "
                        "election. The National Election Commission prints one count page for each former area; the "
                        "totals here are their sum, which equals the votes the winners' list prints.",
                    )
                won_row = won.row(0, named=True)
                options: list[UUID] = []
                chosen = _elected(won_row["name"], won_row["party"], names.select("ticket", "party", "name").rows())
                for ticket, party_name, person_name in names.select("ticket", "party", "name").rows():
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
                    victor = ticket == chosen
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if victor else "lost",
                        seats=1 if victor else None,
                    )
                area = sidos["29" if sido == "29+46" else sido]
                rows = counted.filter(pl.col("city").is_in(cities))
                records = rows.to_dicts()
                totals = [record for record in records if record["town"] == "합계"]
                parts = [record for record in records if record["town"] != "합계"]
                if len(totals) != len(cities):
                    raise ValueError(f"{where}: {len(totals)} totals for {len(cities)} pages")
                total = {
                    field: sum(record[field] for record in totals)
                    for field in ("registered", "ballots", "valid", "invalid")
                }
                total["votes"] = [sum(column) for column in zip(*(record["votes"] for record in totals), strict=True)]
                for field in ("registered", "ballots", "valid", "invalid"):
                    if sum(part[field] for part in parts) != total[field]:
                        print(f"{where}: parts' {field} do not add to {total[field]}")
                if [sum(column) for column in zip(*(part["votes"] for part in parts), strict=True)] != total["votes"]:
                    print(f"{where}: parts' votes do not add to the total")
                if total["votes"][chosen - 1] != won_row["votes"]:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"The winners' list prints {won_row['votes']:,} votes for {won_row['name']}; the "
                        f"count page prints {total['votes'][chosen - 1]:,}, which is the figure stored.",
                    )
                    print(f"{where}: winners' list {won_row['votes']:,}, count {total['votes'][chosen - 1]:,}")
                top = unit(where, None, area, MERGED if sido == "29+46" else SIDO[sido][1])
                shown = [(top, total)]
                for part in parts:
                    label = part["town"]
                    code = f"{where}/{part['city']}/{label}"
                    shown.append((unit(code, top, area, label), part))
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
