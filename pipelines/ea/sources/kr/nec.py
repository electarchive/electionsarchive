import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "kr.nec"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "http://info.nec.go.kr"
REPORT = f"{BASE}/electioninfo/electionInfo_report.xhtml"
DATES = (
    "19520805",
    "19560515",
    "19600315",
    "19631015",
    "19670503",
    "19710427",
    "19871216",
    "19921218",
    "19971218",
    "20021219",
    "20071219",
    "20121219",
    "20170509",
    "20220309",
    "20250603",
)


def _old(day: str) -> bool:
    return day <= "20021219"


def _menu(day: str) -> tuple[str, str]:
    if day <= "19670503":
        return "VCCP09", "VCCP09_#90"
    if _old(day):
        return "VCCP08", "VCCP08_#91"
    return "VCCP08", "VCCP08_#1"


def _selector(kind: str, day: str, city: str | None = None) -> str:
    suffix = "GuOld" if _old(day) else "Old"
    query = {"electionId": "0000000000", "electionCode": day, "subElectionCode": "1"}
    if city is not None:
        query["cityCode"] = city
    return f"{BASE}/bizcommon/selectbox/selectbox_{kind}CodeBySgJson_{suffix}.json?{urlencode(query)}"


def _form(day: str, city: str, town: str) -> dict[str, str]:
    menu, statement = _menu(day)
    return {
        "electionId": "0000000000",
        "requestURI": f"/electioninfo/0000000000/vc/{menu.lower()}.jsp",
        "topMenuId": "VC",
        "secondMenuId": menu,
        "menuId": menu,
        "statementId": statement,
        "oldElectionType": "0" if _old(day) else "",
        "electionType": "1",
        "electionName": day,
        "electionCode": "1",
        "cityCode": city,
        "townCode": town,
    }


def _report_key(day: str, city: str, town: str) -> str:
    return f"{REPORT}?{urlencode(_form(day, city, town))}"


def _ballot_form(day: str) -> dict[str, str]:
    return {
        **_form(day, "11" if _old(day) else "1100", "-1"),
        "requestURI": "/electioninfo/0000000000/vc/vccp09.jsp",
        "secondMenuId": "VCCP09",
        "menuId": "VCCP09",
        "statementId": "VCCP09_#90" if _old(day) else "VCCP09_#1",
    }


def _ballot_key(day: str) -> str:
    return f"{REPORT}?{urlencode(_ballot_form(day))}"


def _get(url: str, form: dict[str, str] | None = None) -> bytes:
    for attempt in range(6):
        try:
            response = (
                httpx.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
                if form is None
                else httpx.post(url, data=form, timeout=180, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE})
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError


def _store(key: str, content: bytes, suffix: str, media: str) -> None:
    raw.store(SOURCE, key, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def _json(url: str) -> list[dict[str, object]]:
    if url not in raw.keys(SOURCE):
        _store(url, _get(url), ".json", "application/json")
    return json.loads(raw.cached(SOURCE, url).path.read_text())["jsonResult"]["body"]


def _jobs(day: str) -> list[tuple[str, str, str]]:
    jobs = []
    for city in _json(_selector("city", day)):
        if _menu(day)[0] == "VCCP09":
            jobs.append((day, str(city["CODE"]), "-1"))
            continue
        for town in _json(_selector("town", day, str(city["CODE"]))):
            jobs.append((day, str(city["CODE"]), str(town["CODE"])))
    return jobs


def _blank(day: str, html: str) -> bool:
    if _menu(day)[0] == "VCCP09":
        return False
    try:
        _, rows = _table(html)
    except ValueError:
        return True
    lead = 2 if day >= "20170509" else 1
    return not rows or sum(1 for row in rows if not any(row[:lead])) > len(rows) / 2


def _report(job: tuple[str, str, str]) -> None:
    key = _report_key(*job)
    if key in raw.keys(SOURCE) and not _blank(job[0], raw.cached(SOURCE, key).path.read_text()):
        return
    for _ in range(40):
        content = _get(REPORT, _form(*job))
        if not _blank(job[0], content.decode()):
            _store(key, content, ".html", "text/html")
            return
        time.sleep(3)
    raise ValueError(f"{job}: the report keeps coming back without unit names")


def fetch() -> None:
    jobs = [job for day in DATES for job in _jobs(day)]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(_report, jobs))
    fetch_people()


LABELS = {
    "구시군명",
    "읍면동명",
    "투표구명",
    "선거인수",
    "투표수",
    "후보자별 득표수",
    "후보자별 득표율 (%)",
    "무효투표수",
    "기권자수",
    "계",
}
TOTALS = {"계", "합계", "소계"}
CHANNELS = {
    "거소투표": 3,
    "선상투표": 3,
    "거소·선상투표": 3,
    "부재자투표": 3,
    "국내부재자투표": 3,
    "관외사전투표": 2,
    "관내사전투표": 2,
    "재외투표": 4,
    "국외부재자투표": 4,
    "재외투표(공관)": 4,
    "국외부재자투표(공관)": 4,
}
TAG = re.compile(r"<[^>]+>")


def _text(cell: str) -> str:
    return " ".join(TAG.sub(" ", cell.replace("&nbsp;", " ")).split())


def _table(html: str) -> tuple[list[tuple[str, str]], list[list[str]]]:
    head, _, body = html.partition("<tbody>")
    head = head[head.rfind("<thead>") :]
    candidates = []
    for cell in re.findall(r"<th[^>]*>(.*?)</th>", head, re.DOTALL):
        if _text(cell).replace(" ", "") in {label.replace(" ", "") for label in LABELS}:
            continue
        parts = [_text(part) for part in re.split(r"<br\s*/?>", cell) if _text(part)]
        if parts and len(parts) != 2:
            raise ValueError(f"candidate header {parts}")
        candidates.append((parts[0], parts[1]) if parts else ("", ""))
    return candidates, _rows(body)


def _rows(html: str) -> list[list[str]]:
    body = html[: html.find("</tbody>")]
    rows = [
        [_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL)
    ]
    return [row for row in rows if row]


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def parse() -> None:
    results: list[dict[str, object]] = []
    tickets: list[dict[str, object]] = []
    for day in DATES:
        menu = _menu(day)[0]
        named = day >= "20170509"
        cities = {str(city["CODE"]): str(city["NAME"]) for city in _json(_selector("city", day))}
        ballot, _ = _table(raw.cached(SOURCE, _ballot_key(day)).path.read_text())
        seen: list[tuple[str, str]] | None = None
        for city, city_name in cities.items():
            towns = (
                [("-1", "")]
                if menu == "VCCP09"
                else [(str(town["CODE"]), str(town["NAME"])) for town in _json(_selector("town", day, city))]
            )
            for town, town_name in towns:
                candidates, rows = _table(raw.cached(SOURCE, _report_key(day, city, town)).path.read_text())
                if seen is None:
                    seen = candidates
                elif len(candidates) != len(seen) or any(
                    a != b and "" not in (a[1], b[1]) for a, b in zip(candidates, seen, strict=True)
                ):
                    raise ValueError(f"{day} {city} {town}: candidates {candidates} differ from {seen}")
                else:
                    seen = [b if a[1] == "" else a for a, b in zip(candidates, seen, strict=True)]
                width = len(ballot)
                extra = len(candidates) - width
                columns = [candidates.index(entry) for entry in ballot] if _old(day) else list(range(width))
                if extra < 0:
                    raise ValueError(f"{day} {city} {town}: header {candidates} against {ballot}")
                lead = 2 if named else 1
                total: list[int] | None = None
                leaves: list[list[int]] = []
                dong = ""
                for row in rows:
                    if not any(row[:lead]):
                        continue
                    if len(row) != lead + width + extra + 5:
                        if "조회된 자료가 없습니다" in row[0]:
                            break
                        raise ValueError(f"{day} {city} {town}: row {row}")
                    cells = [_number(cell) for cell in row[lead : lead + width + extra + 4]]
                    votes = cells[2 : 2 + width + extra]
                    if any(value for k, value in enumerate(votes) if k not in columns):
                        raise ValueError(f"{day} {city} {town}: votes off the ballot in {row}")
                    numbers = [*cells[:2], *(votes[k] for k in columns), *cells[2 + width + extra :]]
                    if named:
                        first, name = row[0], row[1]
                        if first in {"합계", "계"} and not name:
                            total = numbers
                            continue
                        if name in TOTALS:
                            dong = first
                            continue
                        if first:
                            dong, name = "", first
                    else:
                        name = row[0]
                        if name in TOTALS:
                            if name in {"합계", "계"}:
                                total = numbers
                            continue
                    if sum(numbers[2 : 2 + width]) != numbers[2 + width]:
                        print(f"{day} {city_name} {town_name} {name}: candidates sum to {sum(numbers[2 : 2 + width])}")
                    leaves.append(numbers)
                    unit_town = town_name if menu != "VCCP09" else name
                    results.append(
                        {
                            "day": day,
                            "sido": city_name,
                            "town": unit_town,
                            "dong": dong,
                            "unit": "" if menu == "VCCP09" else name,
                            "channel": CHANNELS.get(name, -1),
                            "registered": numbers[0],
                            "ballots": numbers[1],
                            "votes": numbers[2 : 2 + width],
                            "valid": numbers[2 + width],
                            "invalid": numbers[3 + width],
                        }
                    )
                if total is None:
                    if not leaves:
                        continue
                    raise ValueError(f"{day} {city} {town}: no total row")
                sums = [sum(column) for column in zip(*leaves, strict=True)]
                if sums != total:
                    print(f"{day} {city_name} {town_name}: leaves {sums} != total {total}")
        for number, (party, person) in enumerate(ballot, 1):
            tickets.append({"day": day, "ticket": number, "party": party, "name": person})
    frame = pl.DataFrame(results)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    pl.DataFrame(tickets).write_parquet(PARSED / "tickets.parquet")
    print(frame.group_by("day").agg(pl.len(), pl.col("registered").sum(), pl.col("ballots").sum()).sort("day"))


SPARQL = "https://query.wikidata.org/sparql"
CANDIDATES_QUERY = (
    "SELECT ?election ?date ?person ?label ?birth WHERE { ?election wdt:P31/wdt:P279* wd:Q858439; wdt:P17 wd:Q884; "
    "wdt:P585 ?date; wdt:P726 ?person. ?person rdfs:label ?label FILTER(lang(?label) = 'ko') "
    "OPTIONAL { ?person wdt:P569 ?birth } }"
)
PARTIES_QUERY = (
    "SELECT DISTINCT ?party ?label ?english ?start ?end WHERE { ?party wdt:P31/wdt:P279* wd:Q7278; wdt:P17 wd:Q884; "
    "rdfs:label ?label FILTER(lang(?label) = 'ko') OPTIONAL { ?party rdfs:label ?english FILTER(lang(?english) = 'en') } "
    "OPTIONAL { ?party wdt:P571 ?start } OPTIONAL { ?party wdt:P576 ?end } }"
)


def _sparql(query: str) -> str:
    return f"{SPARQL}?{urlencode({'query': query, 'format': 'json'})}"


def _winner_form(day: str) -> dict[str, str]:
    return {
        "electionId": "0000000000",
        "requestURI": "/electioninfo/0000000000/ep/epei01.jsp",
        "topMenuId": "EP",
        "secondMenuId": "EPEI01",
        "menuId": "EPEI01",
        "statementId": "EPEI01_#91" if _old(day) else "EPEI01_#1",
        "oldElectionType": "0" if _old(day) else "1",
        "electionType": "1",
        "electionName": day,
        "electionCode": "1",
        "cityCode": "-1",
        "townCode": "-1",
    }


def _winner_key(day: str) -> str:
    return f"{REPORT}?{urlencode(_winner_form(day))}"


def fetch_people() -> None:
    for day in DATES:
        if _ballot_key(day) not in raw.keys(SOURCE):
            _store(_ballot_key(day), _get(REPORT, _ballot_form(day)), ".html", "text/html")
        if _winner_key(day) not in raw.keys(SOURCE):
            _store(_winner_key(day), _get(REPORT, _winner_form(day)), ".html", "text/html")
    for query in (CANDIDATES_QUERY, PARTIES_QUERY):
        raw.fetch(SOURCE, _sparql(query), suffix=".json")


PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "선거통계시스템: 역대선거 대통령선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 대통령선거 당선인 명부"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP08"
LICENSE = "No license stated: public election statistics of the National Election Commission; facts published with attribution"
SIDO = {
    "11": ("서울", "서울특별시", "Seoul"),
    "26": ("부산", "부산광역시", "Busan"),
    "27": ("대구", "대구광역시", "Daegu"),
    "28": ("인천", "인천광역시", "Incheon"),
    "29": ("광주", "광주광역시", "Gwangju"),
    "30": ("대전", "대전광역시", "Daejeon"),
    "31": ("울산", "울산광역시", "Ulsan"),
    "50": ("세종", "세종특별자치시", "Sejong"),
    "41": ("경기", "경기도", "Gyeonggi"),
    "42": ("강원", "강원특별자치도", "Gangwon"),
    "43": ("충북", "충청북도", "North Chungcheong"),
    "44": ("충남", "충청남도", "South Chungcheong"),
    "45": ("전북", "전북특별자치도", "Jeonbuk"),
    "46": ("전남", "전라남도", "South Jeolla"),
    "47": ("경북", "경상북도", "North Gyeongsang"),
    "48": ("경남", "경상남도", "South Gyeongsang"),
    "49": ("제주", "제주특별자치도", "Jeju"),
}
SIDO_CODES = {
    **{short: code for code, (short, _, _) in SIDO.items()},
    **{full: code for code, (_, full, _) in SIDO.items()},
    "강원도": "42",
    "전라북도": "45",
    "제주도": "49",
}
NOTES = {
    "19600315": (
        "Syngman Rhee was the only candidate left after Cho Byeong-ok died in February 1960, and he was declared "
        "elected. The election was marred by fraud; Rhee resigned on April 26, 1960, after the April Revolution, and "
        "the National Election Commission lists his result as 사퇴 (resigned). The National Assembly elected Yun Po-sun "
        "president on August 12, 1960."
    ),
}
SPECIAL = {2: "aggregate", 3: "postal_district", 4: "abroad_mission"}
ORDINALS = {day: number for number, day in enumerate(DATES, 2)}
SYSTEM_NOTE = (
    "The candidate with the most valid votes is elected (Constitution of 1987, art. 67(1)–(2); Constitution of 1948 as "
    "amended in 1952, art. 53, for the elections of 1952–1960). When only one candidate stands, the 1987 Constitution "
    "requires at least one third of the registered voters (art. 67(3))."
)


def _sido(name: str) -> str:
    return SIDO_CODES[name]


def _town(name: str) -> str:
    return re.sub(r"\((?:[^()]*)\)$", "", name).strip()


def _winners(day: str) -> list[tuple[str, int | None]]:
    html = raw.cached(SOURCE, _winner_key(day)).path.read_text()
    rows = _rows(html[html.find("<tbody>") :])
    found = []
    for row in rows:
        if "대통령선거" not in row or (row[0][:1].isdigit() and not row[0].startswith(f"{day[:4]}. {int(day[4:6])}.")):
            continue
        at = row.index("대통령선거")
        votes = row[-1].split("(")[0].replace(",", "").strip()
        found.append((row[at + 2].split("(")[0].strip(), int(votes) if votes.isdigit() else None))
    return found


def _bindings(query: str) -> list[dict[str, str]]:
    data = json.loads(raw.cached(SOURCE, _sparql(query)).path.read_text())
    return [{key: value["value"] for key, value in row.items()} for row in data["results"]["bindings"]]


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    tickets = pl.read_parquet(PARSED / "tickets.parquet")
    people: dict[tuple[str, str], str] = {}
    for row in _bindings(CANDIDATES_QUERY):
        label = re.sub(r"\s*\(.*\)$", "", row["label"])
        people[row["date"][:10].replace("-", ""), label] = row["person"].rsplit("/", 1)[1]
    named: dict[str, set[str]] = {}
    for (_, label), item in people.items():
        named.setdefault(label, set()).add(item)
    unique = {label: next(iter(items)) for label, items in named.items() if len(items) == 1}
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
                "South Korea's 15 direct presidential elections, 1952–2025, from the National Election Commission's "
                "statistics system: by city, county, and district for 1952–1967 and by polling district from 1971"
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
            notes="개표현황(투표구별) for 1971–2025 and 개표현황(구시군별) for 1952–1967, one page per city, county, or "
            "district counting office, as the system serves them; sums of the rows checked against each page's total.",
        )
        winners_source = cs.source(PUBLISHER, WINNERS_TITLE)
        cs.add(
            "source",
            id=winners_source,
            kind="official_body",
            publisher=PUBLISHER,
            title=WINNERS_TITLE,
            url=f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01",
            license=LICENSE,
            license_url=BASE,
        )
        wikidata = cs.source("Wikidata", "Wikidata candidates of South Korean presidential elections (P726)")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Wikidata candidates of South Korean presidential elections (P726)",
            url="https://www.wikidata.org/wiki/Property:P726",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for query in (CANDIDATES_QUERY, PARTIES_QUERY):
            cs.artifact(raw.cached(SOURCE, _sparql(query)), wikidata)
        for day in DATES:
            cs.artifact(raw.cached(SOURCE, _winner_key(day)), winners_source)
            cs.artifact(raw.cached(SOURCE, _selector("city", day)), source)
            for city in _json(_selector("city", day)):
                if _menu(day)[0] == "VCCP09":
                    cs.artifact(raw.cached(SOURCE, _report_key(day, str(city["CODE"]), "-1")), source)
                    continue
                cs.artifact(raw.cached(SOURCE, _selector("town", day, str(city["CODE"]))), source)
                for town in _json(_selector("town", day, str(city["CODE"]))):
                    cs.artifact(raw.cached(SOURCE, _report_key(day, str(city["CODE"]), str(town["CODE"]))), source)

        for code, rank, noun, kind, system in (
            ("kr.nation", 0, "country", "administrative", None),
            ("kr.sido", 1, "province or metropolitan city", "administrative", "iso_3166_2"),
            ("kr.sigungu", 2, "city, county, or district", "administrative", None),
            ("kr.tupyogu", 4, "polling district", "precinct", None),
        ):
            cs.add(
                "scheme", code=code, polity_id=korea, kind=kind, rank=rank, noun=noun, kinds=None, code_system=system
            )
        nation = cs.area("kr.nation", "KR")
        cs.add("area", id=nation, scheme_code="kr.nation")
        cs.add("area_code", area_id=nation, code="KR", valid_from=None)
        name(nation, "대한민국")
        name(nation, "South Korea", "en", "english")
        cs.add("polity_territory", polity_id=korea, area_id=nation)
        country = conn.execute(
            "select encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'), g.source_id from area_geometry g"
            " join area a on a.id = g.area_id join area_code c on c.area_id = a.id"
            " where a.scheme_code = 'world.country' and c.code = 'KOR'"
        ).fetchone()
        if country is None:
            raise ValueError("world.country KOR has no geometry")
        shape, point, shape_source = country
        cs.add(
            "area_geometry",
            area_id=nation,
            valid_from=None,
            valid_to=None,
            geom=shape,
            label_point=point,
            quality="academic",
            source_id=shape_source,
        )
        sidos: dict[str, UUID] = {}
        for code, (_, full, english) in SIDO.items():
            area = sidos[code] = cs.area("kr.sido", code)
            cs.add("area", id=area, scheme_code="kr.sido")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
            polity = cs.polity(f"KR-{code}")
            cs.add("polity", id=polity, kind="region", iso_code=f"KR-{code}")
            cs.add("slug", path=f"kr/{english.lower().replace(' ', '-')}", entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=korea)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            for entity in (polity, area):
                name(entity, full)
                name(entity, english, "en", "english")

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

        office = cs.key(f"{SOURCE}.office", "president")
        cs.add("office", id=office, polity_id=korea, kind="head_of_state", body_id=None)
        name(office, "대통령")
        name(office, "President of the Republic of Korea", "en", "english")
        system = cs.key(f"{SOURCE}.electoral_system", "president")
        cs.add("electoral_system", id=system, polity_id=korea, rules={"formula": "fptp"}, rules_version=1)
        name(system, "Presidential election, plurality of the valid votes", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

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

        persons: dict[tuple[str, str], UUID] = {}
        for day in DATES:
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            rows = results.filter(pl.col("day") == day)
            slate = tickets.filter(pl.col("day") == day).sort("ticket")
            election = cs.key(f"{SOURCE}.election", day)
            cs.add(
                "election",
                id=election,
                polity_id=korea,
                kind="special" if day in {"20170509", "20250603"} else "general",
                date_from=held,
                date_to=held,
                date_precision="day",
                status="held",
            )
            ordinal = ORDINALS[day]
            name(election, f"제{ordinal}대 대통령선거")
            name(election, f"{held.year} South Korean presidential election", "en", "english")
            path = f"kr/{held}-presidential"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key(f"{SOURCE}.contest", day)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=nation,
                term="full",
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"제{ordinal}대 대통령선거")
            name(contest, f"{held.year} South Korean presidential election", "en", "english")
            cs.add("slug", path=f"{path}/president", entity_id=contest)
            if day in NOTES:
                cs.add("note", entity_id=contest, kind="caveat", body_md=NOTES[day])
            result_set = cs.key(f"{SOURCE}.result_set", day)
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
            winners = dict(_winners(day))
            if len(winners) != 1:
                raise ValueError(f"{day}: winners {winners}")
            totals = [sum(column) for column in zip(*rows["votes"].to_list(), strict=True)]
            options: list[UUID] = []
            for (number, party_name, person_name), total in zip(
                slate.select("ticket", "party", "name").rows(), totals, strict=True
            ):
                option = cs.key(f"{SOURCE}.option", f"{day}:{number}")
                options.append(option)
                independent = party_name == "무소속"
                member = None if independent else party(party_name, day)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=member,
                    ballot_position=None,
                    ballot_label=person_name,
                )
                item = people.get((day, person_name)) or unique.get(person_name)
                person = persons.get(("wikidata", item) if item else (day, person_name))
                if person is None:
                    person = cs.key("wikidata", item) if item else cs.key(f"{SOURCE}.person", f"{day}:{person_name}")
                    persons[("wikidata", item) if item else (day, person_name)] = person
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, person_name)
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{day}:{number}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=person_name,
                    member_of_party_id=member,
                    is_independent=independent,
                )
                elected = person_name in winners
                if elected and winners[person_name] not in {None, total}:
                    raise ValueError(f"{day}: {person_name} declared {winners[person_name]}, rows sum to {total}")
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if elected else "lost",
                    seats=None,
                )
            special = rows.filter(pl.col("channel") >= 0).height > 0
            for sido_name, town, dong, label, channel, registered, ballots, votes, valid, invalid in rows.select(
                "sido", "town", "dong", "unit", "channel", "registered", "ballots", "votes", "valid", "invalid"
            ).rows():
                code = _sido(sido_name)
                area = sidos[code]
                top = unit("kr.sido", code, None, area, "equals")
                place = unit("kr.sigungu", f"{code}/{_town(town)}", top, area, "within")
                if label:
                    parts = [day, code, _town(town), *([dong] if dong else []), label]
                    place = unit("kr.tupyogu", "/".join(parts), place, area, "within", SPECIAL.get(channel, "standard"))
                lane = channel if channel >= 0 else (1 if special else 0)
                for measure, option, value in (
                    (2, None, registered),
                    (3, None, ballots),
                    (4, None, valid),
                    (5, None, invalid),
                    *((1, options[k], v) for k, v in enumerate(votes)),
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
