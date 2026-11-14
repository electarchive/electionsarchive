import html
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
from ea.sources.kr.local import DATES, _old, _sido
from ea.sources.kr.na import _number, _numeric, table
from ea.sources.kr.nec import BASE, PARTIES_QUERY, REPORT, SIDO, _bindings, _get
from ea.store import DATA, connect

SOURCE = "kr.local_pr"
PARSED = DATA / "parsed" / SOURCE
ELECTION_CODE = "8"
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "선거통계시스템: 역대선거 지방선거 시·도의회의원선거(비례대표) 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 지방선거 시·도의회의원선거(비례대표) 당선인 명부"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP09"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
LICENSE = "No license stated: public election statistics of the National Election Commission; facts published with attribution"
VERSION = "1"
NOTE_UNCOUNTED = (
    "Until 2002 there was no separate party ballot: the proportional seats were allocated among the parties by their "
    "votes in the constituency elections, and the National Election Commission prints no party count for these seats. "
    "Only the parties' seats and the members elected from each party's list are recorded."
)
NOTE_COUNTED = (
    "Since 2002 each voter casts a separate party-list vote, and the seats go to the parties that reach 5 percent of "
    "the valid votes, in proportion to their votes by the largest remainder."
)


def _selector(day: str) -> str:
    suffix = "GuOld" if _old(day) else "Old"
    query = {"electionId": "0000000000", "electionCode": day, "subElectionCode": ELECTION_CODE}
    return f"{BASE}/bizcommon/selectbox/selectbox_cityCodeBySgJson_{suffix}.json?{urlencode(query)}"


def _form(kind: str, day: str, city: str) -> dict[str, str]:
    top, menu, page = ("EP", "EPEI01", "ep/epei01.jsp") if kind == "winners" else ("VC", "VCCP09", "vc/vccp09.jsp")
    if kind == "winners":
        statement = "EPEI01_#91" if _old(day) else "EPEI01_#1"
    else:
        statement = "VCCP09_#8"
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
        "electionCode": ELECTION_CODE,
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


ALIASES = {"강원도": "42", "전라북도": "45", "제주도": "49", "전남광주통합특별시": "29+46"}
MERGED = {"29+46": ("전남광주통합특별시", "Jeonnam-Gwangju Integrated Special City")}


def _code(sido: str) -> str:
    found = [code for code, (_, full, _) in SIDO.items() if full == sido]
    return found[0] if found else ALIASES[sido]


def _counts(day: str, city: str, text: str) -> list[dict[str, Any]]:
    head = text.partition("</thead>")[0]
    names = [re.sub(r"\s+", " ", html.unescape(name)).strip() for name in re.findall(r"<strong>(.*?)</strong>", head)]
    found = []
    for row in table(text):
        if len(row) < 6 or not row[0] or not _numeric(row[1]) or not names:
            continue
        if len(row) != 6 + len(names):
            raise ValueError(f"{day} {city}: {len(row)} cells for {len(names)} parties: {row}")
        votes = [_number(cell) for cell in row[3 : 3 + len(names)]]
        valid, invalid = _number(row[3 + len(names)]), _number(row[4 + len(names)])
        if sum(votes) != valid:
            raise ValueError(f"{day} {city} {row[0]}: party votes {sum(votes)} against valid {valid}")
        found.append(
            {
                "day": day,
                "city": city,
                "town": row[0],
                "registered": _number(row[1]),
                "ballots": _number(row[2]),
                "parties": names,
                "votes": votes,
                "valid": valid,
                "invalid": invalid,
            }
        )
    return found


def _winners(day: str, text: str) -> list[dict[str, Any]]:
    found = []
    for row in table(text)[1:]:
        if len(row) != 9 or not row[2].isdigit():
            continue
        found.append(
            {
                "day": day,
                "sido": row[0],
                "party": row[1],
                "rank": int(row[2]),
                "name": re.sub(r"\s*\(.*\)$", "", html.unescape(row[3])),
            }
        )
    return found


def parse() -> None:
    results, winners = [], []
    for day in DATES:
        for city, _ in cities(day):
            results.extend(_counts(day, city, raw.cached(SOURCE, key("count", day, city)).path.read_text()))
            winners.extend(_winners(day, raw.cached(SOURCE, key("winners", day, city)).path.read_text()))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    winners = pl.DataFrame(winners).unique(maintain_order=True)
    winners.write_parquet(PARSED / "winners.parquet")
    print(pl.DataFrame(winners).group_by("day").agg(pl.len(), pl.col("sido").n_unique().alias("sidos")).sort("day"))
    print(pl.DataFrame(results).group_by("day").agg(pl.len(), pl.col("city").n_unique().alias("sidos")).sort("day"))


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
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
                "South Korea's metropolitan and provincial council proportional elections 1995–2026 from the "
                "National Election Commission's statistics system: party votes by city, county, and district from "
                "2002, and the members elected from each party's list"
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
            notes="개표현황 by city, county, and district for each metropolitan city or province: registered voters, "
            "ballots cast, each party's votes, and invalid ballots; the parts are checked against the printed "
            "party votes and valid total.",
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

        systems: dict[bool, UUID] = {}
        for counted, (rules, english, text) in {
            False: (
                {"formula": "hare", "lists": {"order": "closed", "vacancy": "next_on_list"}},
                "Metropolitan and provincial council proportional seats, allocated by constituency votes",
                NOTE_UNCOUNTED,
            ),
            True: (
                {
                    "formula": "hare",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "percent": 5},
                },
                "Metropolitan and provincial council proportional seats, closed lists with a 5 percent threshold",
                NOTE_COUNTED,
            ),
        }.items():
            systems[counted] = cs.key(f"{SOURCE}.electoral_system", str(counted))
            cs.add("electoral_system", id=systems[counted], polity_id=korea, rules=rules, rules_version=1)
            name(systems[counted], english, "en", "english")
            cs.add("note", entity_id=systems[counted], kind="methodology", body_md=text)

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
                address = ("wikidata", found[0]) if len(set(found)) == 1 else ("kr.local.party", f"{text}:{day}")
                entity = parties[text, day] = cs.key(*address)
                if entity in cs.minted and entity not in minted_parties:
                    minted_parties.add(entity)
                    cs.add("party", id=entity, kind="party", polity_id=korea)
                    name(entity, text)
                    english = next((e for item, e, _, _ in party_items.get(text, []) if address[1] == item and e), None)
                    if english:
                        name(entity, english, "en", "english")
            return parties[text, day]

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
            counted = results.filter(pl.col("day") == day)
            has_count = not counted.is_empty()
            for sido_name in elected["sido"].unique(maintain_order=True):
                sido = _code(sido_name)
                where = f"{day}:{sido}:pr"
                won = elected.filter(pl.col("sido") == sido_name).sort("party", "rank", maintain_order=True)
                rows = counted.filter(pl.col("city").map_elements(_sido, return_dtype=pl.String).is_in(sido.split("+")))
                if has_count == rows.is_empty():
                    raise ValueError(f"{where}: count rows {rows.height} but the election has counts {has_count}")
                if sido not in bodies:
                    bodies[sido] = cs.key("kr.local.body", sido)
                    if bodies[sido] in cs.minted or "+" in sido:
                        cs.add(
                            "body",
                            id=bodies[sido],
                            polity_id=cs.polity(f"KR-{sido.split('+')[-1]}"),
                            kind="council",
                        )
                        korean, english = MERGED[sido] if "+" in sido else (f"{SIDO[sido][1]}", SIDO[sido][2])
                        name(bodies[sido], f"{korean}의회")
                        name(bodies[sido], f"{english} Council", "en", "english")
                totals = rows.filter(pl.col("town") == "합계").to_dicts()
                order = list(dict.fromkeys(label for total in totals for label in total["parties"]))
                if not has_count:
                    order = list(dict.fromkeys(won["party"]))
                seats = {label: won.filter(pl.col("party") == label).height for label in order}
                if set(won["party"]) - set(order):
                    raise ValueError(f"{where}: winners from {set(won['party']) - set(order)} not on the ballot")
                label_ko, label_en = MERGED[sido] if "+" in sido else (SIDO[sido][1], SIDO[sido][2])
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=bodies[sido],
                    magnitude=won.height,
                    electoral_system_id=systems[has_count],
                    default_result_kind="counted",
                )
                name(contest, f"{label_ko} 비례대표")
                name(contest, f"{label_en} Council, proportional seats", "en", "english")
                cs.add("slug", path=f"{path}/sido-council-pr/{label_en.lower().replace(' ', '-')}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", where)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source if has_count else winners_source,
                    is_default=True,
                    completeness=1 if has_count else 0,
                )
                options: dict[str, UUID] = {}
                for position, label in enumerate(order, 1):
                    options[label] = cs.key(f"{SOURCE}.option", f"{where}:{label}")
                    cs.add(
                        "contest_option",
                        id=options[label],
                        contest_id=contest,
                        kind="list",
                        party_id=party(label, day),
                        ballot_position=position,
                        ballot_label=label,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[label],
                        kind="won" if seats[label] else "lost",
                        seats=seats[label] or None,
                    )
                for row in won.iter_rows(named=True):
                    tag = f"{where}:{row['party']}:{row['rank']}"
                    person = cs.key(f"{SOURCE}.person", tag)
                    cs.add("person", id=person)
                    name(person, row["name"])
                    candidacy = cs.key(f"{SOURCE}.candidacy", tag)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=options[row["party"]],
                        role="list_member",
                        list_position=row["rank"],
                        ballot_name=row["name"],
                        is_independent=False,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[row["party"]],
                        candidacy_id=candidacy,
                        kind="elected",
                    )
                for total in totals:
                    base = _sido(total["city"])
                    top = unit("kr.sido", base, None, sidos[base], SIDO[base][1])
                    parts = [(top, total)]
                    seen: set[str] = set()
                    for record in rows.filter(
                        (pl.col("city") == total["city"]) & (pl.col("town") != "합계")
                    ).to_dicts():
                        town = re.sub(r"\((?:[^()]*)\)$", "", record["town"]).strip()
                        if town in seen:
                            raise ValueError(f"{where}: {town} appears twice")
                        seen.add(town)
                        parts.append((unit("kr.sigungu", f"{base}/{town}", top, sidos[base], town), record))
                    for field in ("registered", "ballots", "valid", "invalid"):
                        if sum(record[field] for _, record in parts[1:]) != total[field]:
                            print(f"{where}: parts' {field} do not add to {total[field]}")
                    if [
                        sum(column) for column in zip(*(record["votes"] for _, record in parts[1:]), strict=True)
                    ] != total["votes"]:
                        print(f"{where}: parts' party votes do not add to the total")
                    for target, record in parts:
                        for measure, option, value in (
                            (2, None, record["registered"]),
                            (3, None, record["ballots"]),
                            (4, None, record["valid"]),
                            (5, None, record["invalid"]),
                            *(
                                (1, options[label], v)
                                for label, v in zip(record["parties"], record["votes"], strict=True)
                            ),
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
