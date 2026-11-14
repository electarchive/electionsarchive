import json
import os
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
from ea.sources.kr.nec import BASE, PARTIES_QUERY, REPORT, SIDO, _bindings, _get
from ea.store import DATA, connect

SOURCE = "kr.na"
DATES = (
    "19480510",
    "19500530",
    "19540520",
    "19580502",
    "19600729",
    "19631126",
    "19670608",
    "19710525",
    "19730227",
    "19781212",
    "19810325",
    "19850212",
    "19880426",
    "19920324",
    "19960411",
    "20000413",
    "20040415",
    "20080409",
    "20120411",
    "20160413",
    "20200415",
    "20240410",
)
OLD_FORMS = "19670608"
VOTING_PLACES = ("19710525", "20040415")
EMD = "20080409"
TAG = re.compile(r"<[^>]+>")


def _store(address: str, content: bytes, suffix: str, media: str) -> None:
    raw.store(SOURCE, address, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def _text(cell: str) -> str:
    return " ".join(TAG.sub(" ", cell.replace("&nbsp;", " ")).split())


def _old(day: str) -> bool:
    return day <= OLD_FORMS


def _selector(kind: str, day: str, city: str | None = None) -> str:
    query = {"electionId": "0000000000", "electionCode": day, "subElectionCode": "2"}
    if city is not None:
        query["cityCode"] = city
    suffix = "GuOld" if _old(day) else "Old"
    return f"{BASE}/bizcommon/selectbox/selectbox_{kind}CodeBySgJson_{suffix}.json?{urlencode(query)}"


def _json(url: str) -> list[dict[str, Any]]:
    if url not in raw.keys(SOURCE):
        _store(url, _get(url), ".json", "application/json")
    return json.loads(raw.cached(SOURCE, url).path.read_text())["jsonResult"]["body"]


def cities(day: str) -> list[tuple[str, str]]:
    return [(str(city["CODE"]), str(city["NAME"])) for city in _json(_selector("city", day))]


def towns(day: str, city: str) -> list[tuple[str, str]]:
    return [(str(town["CODE"]), str(town["NAME"])) for town in _json(_selector("town", day, city))]


MENUS = {
    "count": ("VC", "VCCP09", "vc/vccp09.jsp"),
    "place": ("VC", "VCCP08", "vc/vccp08.jsp"),
    "dong": ("VC", "VCCP04", "vc/vccp04.jsp"),
    "winners": ("EP", "EPEI01", "ep/epei01.jsp"),
}


def _form(kind: str, day: str, city: str, town: str = "-1") -> dict[str, str]:
    top, menu, page = MENUS[kind]
    if kind == "winners":
        statement = "EPEI01_#91" if _old(day) else "EPEI01_#1"
    elif kind == "count":
        statement = "VCCP09_#90" if _old(day) else "VCCP09_#1"
    else:
        statement = f"{menu}_#1"
    return {
        "electionId": "0000000000",
        "requestURI": f"/electioninfo/0000000000/{page}",
        "topMenuId": top,
        "secondMenuId": menu,
        "menuId": menu,
        "statementId": statement,
        "oldElectionType": "0" if _old(day) else "",
        "electionType": "2",
        "electionName": day,
        "electionCode": "2",
        "cityCode": city,
        "townCode": town,
        "sggCityCode": "-1",
        "sggTownCode": "-1",
    }


def key(kind: str, day: str, city: str, town: str = "-1") -> str:
    return f"{REPORT}?{urlencode(_form(kind, day, city, town))}"


def table(html: str) -> list[list[str]]:
    start = html.find('id="table01"')
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html[start : html.find("</table>", start)], re.DOTALL)
    return [[_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)] for row in rows]


def _numeric(cell: str) -> bool:
    return bool(re.fullmatch(r"-?[\d,]+", cell))


def _blank(kind: str, day: str, html: str) -> bool:
    if 'id="table01"' not in html:
        return True
    if kind in {"count", "winners"}:
        return False
    return any(
        len(row) > 6 and sum(_numeric(cell) for cell in row) >= 5 and not row[0] and not row[1] for row in table(html)
    )


def _job(job: tuple[str, str, str, str]) -> None:
    kind, day = job[:2]
    address = key(*job)
    if address in raw.keys(SOURCE) and not _blank(kind, day, raw.cached(SOURCE, address).path.read_text()):
        return
    for _ in range(30):
        content = _get(REPORT, _form(*job))
        if not _blank(kind, day, content.decode()):
            _store(address, content, ".html", "text/html")
            return
        time.sleep(3)
    raise ValueError(f"{job}: the report keeps coming back without unit names")


def jobs(days: tuple[str, ...] = DATES) -> list[tuple[str, str, str, str]]:
    found = []
    for day in days:
        for city, _ in cities(day):
            found.append(("winners", day, city, "-1"))
            found.append(("count", day, city, "-1"))
            if day >= VOTING_PLACES[0] and not _old(day):
                kind = "dong" if day >= EMD else "place"
                found.extend((kind, day, city, town) for town, _ in towns(day, city))
    return found


def fetch() -> None:
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(_job, jobs()))


PARSED = DATA / "parsed" / SOURCE
NUMBER = re.compile(r"^제(\d+)")
PROVINCES = {"5100": "50", "5200": "42", "5300": "45"}
Head = tuple[list[str], int]


def _sido(city: str) -> str:
    return PROVINCES.get(city, city[:2])


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _head(row: list[str], named: bool = False) -> tuple[str | None, Head] | None:
    if len(row) < 3 or "계" not in row or any(_numeric(cell) for cell in row):
        return None
    name = row[0] if named else None
    cells = [(index, cell) for index, cell in enumerate(row) if cell and cell != "계" and (index > 0 or not named)]
    if not cells:
        return None
    return name, ([cell for _, cell in cells], row.index("계") - cells[0][0])


def _data(row: list[str], head: Head) -> dict[str, Any]:
    names, slots = head
    leading = len(row) - 3 - slots
    return {
        "registered": _number(row[leading - 2]),
        "ballots": _number(row[leading - 1]),
        "votes": [_number(row[leading + index]) if row[leading + index] else 0 for index in range(len(names))],
        "valid": _number(row[-3]),
        "invalid": _number(row[-2]),
    }


def _numbers(row: list[str]) -> bool:
    return sum(_numeric(cell) for cell in row) >= 5


def _tickets(day: str, sido: str, sgg: str, head: Head) -> list[dict[str, Any]]:
    return [
        {"day": day, "sido": sido, "sgg": sgg, "ticket": order, "party": party, "name": person}
        for order, (party, person) in enumerate(map(_split_name, head[0]), 1)
    ]


def _split_name(text: str) -> tuple[str, str]:
    party, _, person = text.rpartition(" ")
    return party, person


def _counts(html: str) -> list[dict[str, Any]]:
    found = []
    head: tuple[str | None, Head] | None = None
    for row in table(html):
        if (candidate := _head(row, named=True)) is not None:
            head = candidate
        elif head is not None and head[0] is not None and _numbers(row):
            found.append({"sgg": head[0], "head": head[1], **_data(row, head[1])})
    return found


def _lane(label: str) -> int | None:
    if any(word in label for word in ("국외", "재외")):
        return 4
    if any(word in label for word in ("부재자", "거소", "선상")):
        return 3
    return None


def _check(where: str, total: dict[str, Any] | None, leaves: list[dict[str, Any]]) -> None:
    if total is None or not leaves:
        return
    sums = [sum(column) for column in zip(*(leaf["votes"] for leaf in leaves), strict=True)]
    if sums != total["votes"] or sum(leaf["invalid"] for leaf in leaves) != total["invalid"]:
        print(f"{where}: leaves {sums} != total {total['votes']}")


def _places(day: str, sido: str, town: str, html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results, tickets = [], []
    head: tuple[str | None, Head] | None = None
    block: str | None = None
    total: dict[str, Any] | None = None
    leaves: list[dict[str, Any]] = []
    seen: set[str] = set()

    def close() -> None:
        if block is None:
            return
        _check(f"{day} {sido} {town} {block}", total, leaves)
        if not leaves and total is not None:
            results.append({**total, "sgg": block, "town": town, "label": "", "kind": "part", "channel": 0})
        results.extend(leaves)

    for row in table(html):
        if (candidate := _head(row)) is not None:
            head = candidate
        elif head is not None and _numbers(row):
            label = row[1]
            if label == "계":
                close()
                block, leaves = row[0], []
                total = _data(row, head[1])
                if block not in seen:
                    seen.add(block)
                    tickets.extend(_tickets(day, sido, block, head[1]))
            elif label != "소계" and block is not None:
                lane = _lane(label)
                leaves.append(
                    {
                        **_data(row, head[1]),
                        "sgg": block,
                        "town": town,
                        "label": label,
                        "kind": "tupyogu" if lane is None else "special",
                        "channel": 0 if lane is None else lane,
                    }
                )
    close()
    return results, tickets


def _dongs(
    day: str, sido: str, town: str, html: str, names: dict[str, set[str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results, tickets = [], []
    blocks: list[tuple[Head, list[list[str]]]] = []
    for row in table(html):
        if (candidate := _head(row)) is not None:
            blocks.append((candidate[1], []))
        elif blocks and _numbers(row):
            blocks[-1][1].append(row)
    for head, rows in blocks:
        named = {sgg for cell in head[0] for sgg in names.get(cell, set())}
        if len(named) > 1:
            named = {sgg for sgg in named if os.path.commonprefix([sgg, town]) != "" and sgg[:2] == town[:2]}
        if len(named) != 1:
            raise ValueError(f"{day} {sido} {town}: candidates {head[0]} match constituencies {named}")
        sgg = next(iter(named))
        tickets.extend(_tickets(day, sido, sgg, head))
        total = _data(rows[0], head)
        leaves: list[dict[str, Any]] = []

        def add(row: list[str], kind: str, channel: int, label: str | None = None, head: Head = head) -> None:
            leaves.append(
                {
                    **_data(row, head),
                    "sgg": sgg,
                    "town": town,
                    "label": label or row[0],
                    "kind": kind,
                    "channel": channel,
                }
            )

        stage = 0
        groups: list[tuple[list[str], list[list[str]]]] = []
        for row in rows[1:]:
            label = row[0]
            if stage < 2:
                lane = 4 if label == "합계" else _lane(label)
                if label.startswith("잘못"):
                    add(row, "special", 0)
                    stage = 2
                elif lane is not None:
                    add(row, "special", lane, "국외부재자투표" if label == "합계" else label)
                else:
                    add(row, "emd", 0)
                    stage = 1
            elif label == "합계":
                groups.append((row, []))
            else:
                groups[-1][1].append(row)
        inside = False
        for row, children in groups:
            if children:
                inside = True
                for child in children:
                    add(child, "emd", 2)
            else:
                add(row, "special", 2, "관외사전투표" if inside else "관내사전투표")
                inside = True
        _check(f"{day} {sido} {town} {sgg}", total, leaves)
        results.extend(leaves)
    return results, tickets


def _winners(day: str, sido: str, html: str) -> list[dict[str, Any]]:
    found = []
    for row in table(html):
        if len(row) == 9 and re.search(r"[\d,]+ \(", row[8]):
            found.append(
                {
                    "day": day,
                    "sido": sido,
                    "sgg": row[0],
                    "party": row[1],
                    "name": re.sub(r"\s*\(.*\)$", "", row[2]),
                    "birth": row[4].split(" ")[0],
                    "votes": _number(row[8].split(" ")[0]),
                }
            )
    return found


def _canon(name: str) -> str:
    found = NUMBER.match(name)
    return found.group(1) if found else name


def parse() -> None:
    results: list[dict[str, Any]] = []
    tickets: list[dict[str, Any]] = []
    winners: list[dict[str, Any]] = []
    for day in DATES:
        for city, _ in cities(day):
            sido = _sido(city)
            elected = _winners(day, sido, raw.cached(SOURCE, key("winners", day, city)).path.read_text())
            winners.extend(elected)
            listing = _counts(raw.cached(SOURCE, key("count", day, city)).path.read_text()) if _old(day) else []
            if _old(day):
                display = {_canon(str(won["sgg"])): str(won["sgg"]) for won in elected}
                for found in listing:
                    sgg = display.get(_canon(found["sgg"]), found["sgg"])
                    row = {k: v for k, v in found.items() if k not in {"head", "sgg"}}
                    results.append(
                        {
                            **row,
                            "sgg": sgg,
                            "day": day,
                            "sido": sido,
                            "town": "",
                            "label": "",
                            "kind": "constituency",
                            "channel": 0,
                        }
                    )
                    tickets.extend(_tickets(day, sido, sgg, found["head"]))
                for won in elected:
                    won["sgg"] = display[_canon(str(won["sgg"]))]
                continue
            names: dict[str, set[str]] = {}
            for won in elected:
                names.setdefault(f"{won['party']} {won['name']}", set()).add(str(won["sgg"]))
            for town, town_name in towns(day, city) if day >= VOTING_PLACES[0] else []:
                html = raw.cached(SOURCE, key("dong" if day >= EMD else "place", day, city, town)).path.read_text()
                found, slate = (
                    _dongs(day, sido, town_name, html, names) if day >= EMD else _places(day, sido, town_name, html)
                )
                results.extend({**row, "day": day, "sido": sido} for row in found)
                tickets.extend(slate)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(tickets).unique().write_parquet(PARSED / "tickets.parquet")
    pl.DataFrame(winners).write_parquet(PARSED / "winners.parquet")
    print(pl.DataFrame(results).group_by("day").agg(pl.len(), pl.col("ballots").sum()).sort("day"))


VERSION = "1"
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "선거통계시스템: 역대선거 국회의원선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 국회의원선거 당선인 명부"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP08"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
LICENSE = "No license stated: public election statistics of the National Election Commission; facts published with attribution"
SPECIAL = {2: "aggregate", 3: "postal_district", 4: "abroad_mission"}
SYSTEM_NOTES = {
    "fptp": (
        "Each constituency elects one member, and the candidate with the most valid votes wins (National Assembly "
        "Election Act of 1948 and its successors, now the Public Official Election Act, art. 188)."
    ),
    "sntv": (
        "From 1973 to 1985 each constituency elected two members: every voter named one candidate, and the two "
        "candidates with the most valid votes won."
    ),
}
NOTE = (
    "Constituency elections only: the proportional seats chosen from 1988 on are not loaded here. Registered voters "
    "and ballots cast are not published for the elections of 1948–1960. Counts are as the National Election "
    "Commission prints them, including a few rows where ballots exceed registered voters."
)


def _slug_sido(code: str) -> str:
    return SIDO[code][2].lower().replace(" ", "-")


def _dup(rows: list[dict[str, Any]]) -> list[str]:
    seen: dict[tuple[object, ...], int] = {}
    labels = []
    for row in rows:
        where = (row["sgg"], row["town"], row["label"], row["channel"])
        seen[where] = seen.get(where, 0) + 1
        labels.append(str(row["label"]) + (f"#{seen[where]}" if seen[where] > 1 else ""))
    return labels


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
                "South Korea's National Assembly constituency elections 1948–2024 from the National Election "
                "Commission's statistics system: candidates and votes by constituency, and by polling district or "
                "neighborhood from 1971"
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
            notes="개표현황 by constituency for 1948–1967, by polling district for 1971–2004, and by neighborhood "
            "(읍면동) with early-voting and absentee rows for 2008–2024, one page per city, county, or district "
            "counting office; the rows of each constituency are checked against its printed total.",
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
            cs.artifact(raw.cached(SOURCE, address), winners_source if "EPEI01" in address else source)

        cs.add(
            "scheme",
            code="kr.constituency",
            polity_id=korea,
            kind="electoral_district",
            rank=3,
            noun="constituency",
            kinds=None,
            code_system=None,
        )
        sidos = {code: cs.area("kr.sido", code) for code in SIDO}
        units: dict[tuple[str, str], UUID] = {}

        def unit(
            scheme: str, code: str, parent: UUID | None, area: UUID, relation: str, kind: str = "standard"
        ) -> UUID:
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
                        area_relation=relation,
                        area_id=area,
                        kind=kind,
                        valid_from=None,
                    )
                    name(entity, code.rsplit("/", 1)[-1])
            return units[scheme, code]

        bodies = {
            "constituent": ("constituent_assembly", "제헌국회", "Constituent National Assembly"),
            "assembly": ("unicameral", "국회", "National Assembly"),
            "house": ("lower_house", "민의원", "House of Representatives"),
        }
        body_ids: dict[str, UUID] = {}
        for code, (kind, korean, english) in bodies.items():
            body_ids[code] = cs.key(f"{SOURCE}.body", code)
            cs.add("body", id=body_ids[code], polity_id=korea, kind=kind)
            name(body_ids[code], korean)
            name(body_ids[code], english, "en", "english")
        systems: dict[str, UUID] = {}
        for code, (rules, english) in {
            "fptp": ({"formula": "fptp"}, "National Assembly constituencies, plurality of the valid votes"),
            "sntv": (
                {"formula": "sntv"},
                "National Assembly two-member constituencies, single non-transferable vote",
            ),
        }.items():
            systems[code] = cs.key(f"{SOURCE}.electoral_system", code)
            cs.add("electoral_system", id=systems[code], polity_id=korea, rules=rules, rules_version=1)
            name(systems[code], english, "en", "english")
            cs.add("note", entity_id=systems[code], kind="methodology", body_md=SYSTEM_NOTES[code])

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
                key = ("wikidata", found[0]) if len(set(found)) == 1 else (f"{SOURCE}.party", f"{text}:{day}")
                entity = parties[text, day] = cs.key(*key)
                if entity in cs.minted and entity not in minted_parties:
                    minted_parties.add(entity)
                    cs.add("party", id=entity, kind="party", polity_id=korea)
                    name(entity, text)
                    english = next((e for item, e, _, _ in party_items.get(text, []) if key[1] == item and e), None)
                    if english:
                        name(entity, english, "en", "english")
            return parties[text, day]

        for number, day in enumerate(DATES, 1):
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            body = body_ids["constituent" if number == 1 else "house" if day == "19600729" else "assembly"]
            election = cs.key(f"{SOURCE}.election", day)
            cs.add(
                "election",
                id=election,
                polity_id=korea,
                kind="general",
                date_from=held,
                date_to=held,
                date_precision="day",
                status="held",
            )
            name(election, f"제{number}대 국회의원선거")
            name(election, f"{held.year} South Korean legislative election", "en", "english")
            path = f"kr/{held}-legislative"
            cs.add("slug", path=path, entity_id=election)
            elected = winners.filter(pl.col("day") == day)
            slate = tickets.filter(pl.col("day") == day).sort("sido", "sgg", "ticket")
            counted = results.filter(pl.col("day") == day)
            order: dict[str, int] = {}
            for sido, sgg in elected.select("sido", "sgg").unique(maintain_order=True).rows():
                order[sido] = order.get(sido, 0) + 1
                where = f"{day}:{sido}:{sgg}"
                rows = counted.filter((pl.col("sido") == sido) & (pl.col("sgg") == sgg))
                if rows.is_empty():
                    raise ValueError(f"{where}: no counted rows")
                won = elected.filter((pl.col("sido") == sido) & (pl.col("sgg") == sgg))
                magnitude = won.height
                contest = cs.key(f"{SOURCE}.contest", where)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    magnitude=magnitude,
                    electoral_system_id=systems["sntv" if magnitude > 1 else "fptp"],
                    default_result_kind="counted",
                )
                name(contest, sgg)
                contest_path = f"{path}/{_slug_sido(sido)}-{order[sido]}"
                cs.add("slug", path=contest_path, entity_id=contest)
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
                if number == 1:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=NOTE)
                options: list[UUID] = []
                names = slate.filter((pl.col("sido") == sido) & (pl.col("sgg") == sgg))
                if names.height != max(len(v) for v in rows["votes"].to_list()):
                    raise ValueError(f"{where}: {names.height} tickets for {rows['votes'][0]}")
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
                    victor = won.filter((pl.col("name") == person_name) & (pl.col("party") == party_name))
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if victor.height else "lost",
                        seats=1 if victor.height else None,
                    )
                    if victor.height:
                        total = sum(v[len(options) - 1] for v in rows["votes"].to_list())
                        if total != victor["votes"][0]:
                            raise ValueError(f"{where}: {person_name} declared {victor['votes'][0]}, rows sum {total}")
                if sum(1 for _ in won.iter_rows()) != magnitude:
                    raise ValueError(where)
                records = rows.to_dicts()
                labels = _dup(records)
                early = {
                    (record["town"], label)
                    for record, label in zip(records, labels, strict=True)
                    if record["channel"] == 2
                }
                top = unit("kr.sido", sido, None, sidos[sido], "equals")
                for record, label in zip(records, labels, strict=True):
                    channel = record["channel"]
                    if record["kind"] == "constituency":
                        place = unit("kr.constituency", f"{where}", top, sidos[sido], "within")
                    else:
                        town = re.sub(r"\((?:[^()]*)\)$", "", record["town"]).strip()
                        place = unit("kr.sigungu", f"{sido}/{town}", top, sidos[sido], "within")
                        if record["kind"] != "part":
                            place = unit(
                                "kr.tupyogu",
                                f"{day}/{sido}/{town}/{label}",
                                place,
                                sidos[sido],
                                "within",
                                SPECIAL.get(channel, "standard") if record["kind"] == "special" else "standard",
                            )
                    lane = 1 if channel == 0 and (record["town"], label) in early else channel
                    missing = held.year < 1963 and record["registered"] == 0 and record["ballots"] == 0
                    for measure, option, value in (
                        *(() if missing else ((2, None, record["registered"]), (3, None, record["ballots"]))),
                        (4, None, record["valid"]),
                        (5, None, record["invalid"]),
                        *((1, options[k], v) for k, v in enumerate(record["votes"])),
                    ):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=place,
                            option_id=option,
                            channel=lane,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
