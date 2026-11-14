import html
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.kr.local import DATES as ALL_DATES
from ea.sources.kr.local import _old, _sido
from ea.sources.kr.na import _number, _numeric, table
from ea.sources.kr.nec import BASE, PARTIES_QUERY, REPORT, SIDO, _bindings, _get
from ea.store import DATA, connect

DATES = ALL_DATES[3:]
SOURCE = "kr.local_sgg_pr"
PARSED = DATA / "parsed" / SOURCE
ELECTION_CODE = "9"
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "선거통계시스템: 역대선거 지방선거 구·시·군의회의원선거(비례대표) 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 지방선거 구·시·군의회의원선거(비례대표) 당선인 명부"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP09"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
LICENSE = "No license stated: public election statistics of the National Election Commission; facts published with attribution"
VERSION = "1"
NOTE = (
    "Each voter casts a separate party-list vote, and the seats go to the parties that reach 3 percent of the valid "
    "votes, in proportion to their votes by the largest remainder (Public Official Election Act, arts. 190-2 and "
    "190-3). Contests whose lists did not outnumber the seats are recorded as elected without a count, as the "
    "National Election Commission lists them (무투표당선)."
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
        statement = "VCCP09_#9"
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


ALIASES = {"강원도": "42", "전라북도": "45", "제주도": "49"}


def _counts(day: str, city: str, text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    parties: list[str] = []
    gye = 0
    for row in table(text)[2:]:
        if len(row) < 6:
            continue
        if "계" in row and not _numeric(row[2]):
            gye = row.index("계")
            parties = row[4:gye]
            found.append({"day": day, "city": city, "contest": row[0], "parts": []})
            continue
        if not row[0] and row[1] and _numeric(row[2]):
            if not found or len(row) < gye + 3:
                raise ValueError(f"{day} {city}: stray row {row}")
            votes = [_number(cell) if label else 0 for label, cell in zip(parties, row[4:gye], strict=True)]
            valid, invalid = _number(row[gye]), _number(row[gye + 1])
            if sum(votes) != valid:
                raise ValueError(f"{day} {city} {row[1]}: party votes {sum(votes)} against valid {valid}")
            found[-1]["parts"].append(
                {
                    "town": row[1],
                    "registered": _number(row[2]),
                    "ballots": _number(row[3]),
                    "parties": [label for label in parties if label],
                    "votes": [v for label, v in zip(parties, votes, strict=True) if label],
                    "valid": valid,
                    "invalid": invalid,
                }
            )
    return found


def _winners(day: str, city: str, text: str) -> list[dict[str, Any]]:
    found = []
    for row in table(text)[1:]:
        if len(row) != 9 or not row[2].isdigit():
            continue
        found.append(
            {
                "day": day,
                "city": city,
                "contest": row[0],
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
            winners.extend(_winners(day, city, raw.cached(SOURCE, key("winners", day, city)).path.read_text()))
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "results.json").write_text(json.dumps(results, ensure_ascii=False))
    table_ = pl.DataFrame(winners).unique(maintain_order=True)
    table_.write_parquet(PARSED / "winners.parquet")
    print(table_.group_by("day").agg(pl.len(), pl.col("contest").n_unique().alias("contests")).sort("day"))
    print(pl.DataFrame({"day": [r["day"] for r in results]}).group_by("day").len().sort("day"))


def _town(name: str) -> str:
    return re.sub(r"\((?:[^()]*)\)$", "", name).strip()


def emit() -> Path:
    results = json.loads((PARSED / "results.json").read_text())
    winners = pl.read_parquet(PARSED / "winners.parquet")
    party_items: dict[str, list[tuple[str, str | None, str | None, str | None]]] = {}
    for row in _bindings(PARTIES_QUERY):
        party_items.setdefault(row["label"], []).append(
            (row["party"].rsplit("/", 1)[1], row.get("english"), row.get("start"), row.get("end"))
        )
    counted = {(r["day"], r["city"], r["contest"]): r["parts"] for r in results}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "South Korea's city, county, and district council proportional elections 2006–2026 from the "
                "National Election Commission's statistics system: party votes by city, county, and district, and "
                "the members elected from each party's list"
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

        system = cs.key(f"{SOURCE}.electoral_system", "pr")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=korea,
            rules={
                "formula": "hare",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "percent": 3},
            },
            rules_version=1,
        )
        name(
            system,
            "City, county, and district council proportional seats, closed lists with a 3 percent threshold",
            "en",
            "english",
        )
        cs.add("note", entity_id=system, kind="methodology", body_md=NOTE)

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

        for number, day in enumerate(ALL_DATES, 1):
            if day not in DATES:
                continue
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
            pairs = list(elected.select("city", "contest").unique(maintain_order=True).iter_rows())
            stripped = Counter((_sido(city), _town(contest_name)) for city, contest_name in pairs)
            for city, contest_name in pairs:
                base = _sido(city)
                label = _town(contest_name) if stripped[base, _town(contest_name)] == 1 else contest_name
                where = f"{day}:{base}/{label}:pr"
                won = elected.filter((pl.col("city") == city) & (pl.col("contest") == contest_name)).sort(
                    "party", "rank", maintain_order=True
                )
                parts = counted.get((day, city, contest_name), [])
                tacit = not parts
                body_key = (base, label)
                if body_key not in bodies:
                    bodies[body_key] = cs.key("kr.local.body", f"{base}/{label}")
                    if bodies[body_key] in cs.minted:
                        cs.add("body", id=bodies[body_key], polity_id=cs.polity(f"KR-{base}"), kind="council")
                        name(bodies[body_key], f"{label}의회")
                order = list(dict.fromkeys(won["party"]))
                if parts:
                    order = list(dict.fromkeys(p for part in parts for p in part["parties"]))
                    if set(won["party"]) - set(order):
                        raise ValueError(f"{where}: winners from {set(won['party']) - set(order)} not on the ballot")
                seats = {item: won.filter(pl.col("party") == item).height for item in order}
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=bodies[body_key],
                    magnitude=won.height,
                    electoral_system_id=system,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{label} 비례대표")
                cs.add("slug", path=f"{path}/sigungu-council-pr/{base}-{label}", entity_id=contest)
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
                    completeness=0 if tacit else 1,
                )
                options: dict[str, UUID] = {}
                for position, item in enumerate(order, 1):
                    options[item] = cs.key(f"{SOURCE}.option", f"{where}:{item}")
                    cs.add(
                        "contest_option",
                        id=options[item],
                        contest_id=contest,
                        kind="list",
                        party_id=party(item, day),
                        ballot_position=position,
                        ballot_label=item,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[item],
                        kind="won" if seats[item] else "lost",
                        seats=seats[item] or None,
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
                if tacit:
                    continue
                top = unit("kr.sido", base, None, sidos[base], SIDO[base][1])
                total = parts[0] if parts[0]["town"] == "소계" else None
                home = unit("kr.sigungu", f"{base}/{label}", top, sidos[base], label)
                placed = [(home, total or parts[0])]
                if total is not None or len(parts) > 1:
                    seen: set[str] = set()
                    for part in parts[1:]:
                        town = _town(part["town"])
                        if town in seen:
                            raise ValueError(f"{where}: {town} appears twice")
                        seen.add(town)
                        placed.append((unit("kr.sigungu", f"{base}/{town}", home, sidos[base], town), part))
                    for field in ("registered", "ballots", "valid", "invalid"):
                        if sum(part[field] for _, part in placed[1:]) != placed[0][1][field]:
                            print(f"{where}: parts' {field} do not add to {placed[0][1][field]}")
                for target, record in placed:
                    for measure, option, value in (
                        (2, None, record["registered"]),
                        (3, None, record["ballots"]),
                        (4, None, record["valid"]),
                        (5, None, record["invalid"]),
                        *((1, options[item], v) for item, v in zip(record["parties"], record["votes"], strict=True)),
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
