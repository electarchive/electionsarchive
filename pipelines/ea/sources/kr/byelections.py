"""South Korea's by-elections and re-runs, from the National Election Commission's statistics system.

The system's 역대선거 menu lists the by-elections (재·보궐선거) held since 2010 under its own election type. Each
by-election day holds contests of several kinds, and each kind has its own selector: a National Assembly seat by
counting office (구시군위원회), a governor, education superintendent, or mayor by counting office, and a council
seat by constituency within the counting office. Every page prints the count by polling district (개표현황(투표구별),
VCCP08, or by neighborhood, VCCP04, on the days that share a count with local elections), with the early, absentee, and overseas rows; the winners' list (당선인명부, EPEI01) names who was elected.
`kr.nec_stats_byelections` builds one package per day from these pages.
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from ea import raw
from ea.sources.kr.nec import BASE, REPORT

SOURCE = "kr.byelections"
SELECT = f"{BASE}/bizcommon/selectbox"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP08"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
# The by-election dates the 역대선거 menu offers (election type 재·보궐선거), as its selector lists them.
DATES = (
    "20100728",
    "20101027",
    "20110427",
    "20111026",
    "20120411",
    "20121219",
    "20130424",
    "20131030",
    "20140730",
    "20141029",
    "20150429",
    "20151028",
    "20160413",
    "20170412",
    "20170509",
    "20180613",
    "20190403",
    "20200415",
    "20210407",
    "20220309",
    "20220601",
    "20230405",
    "20231011",
    "20240410",
    "20241016",
    "20250402",
)
# The sub-election codes of the statistics system: 1 president, 2 National Assembly, 3 governor, 4 mayor, 5
# provincial council, 6 municipal council, 7 provincial list, 8 education seat, 11 education superintendent.
CODES = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11")
HEADERS = {"User-Agent": raw.USER_AGENT, "Referer": BASE}
LOCK = threading.RLock()
KNOWN: set[str] = set()

# Special-statement pages: the system answers some constituencies with its "_S" statement, which adds the
# constituency column. The menu's own script picks the statement; this mirrors it.
S_FOR_ALL = {"20151028", "20150429", "20141029", "20140730"}
S_RULES: dict[str, Any] = {
    "20250402": lambda c, sgg_town, sgg_city, town: c == "6" and sgg_town in {"6280901", "6460804"},
    "20240410": lambda c, sgg_town, sgg_city, town: (
        c != "4" and not (c == "5" and sgg_town in {"5421701", "5441401", "5472101", "5472401"})
    ),
    "20230405": lambda c, sgg_town, sgg_city, town: c == "5" and sgg_town == "5481301",
    "20220309": lambda c, sgg_town, sgg_city, town: town == "1122",
    "20210407": lambda c, sgg_town, sgg_city, town: (
        False
        if c in {"3", "4"}
        else sgg_town in {"5110901", "5481101"}
        if c == "5"
        else sgg_town in {"6111906", "6112404", "6481103"}
    ),
    "20200415": lambda c, sgg_town, sgg_city, town: c != "4" and not (c == "5" and sgg_town == "5430801"),
    "20190403": lambda c, sgg_town, sgg_city, town: c != "2" and sgg_town != "6450104",
    "20170509": lambda c, sgg_town, sgg_city, town: True,
    "20170412": lambda c, sgg_town, sgg_city, town: (
        False
        if c in {"2", "4"}
        else sgg_town in {"5414102", "5481401"}
        if c == "5"
        else sgg_town in {"6441701", "6441702", "6441802", "6480801", "6480806", "6481405"}
    ),
    "20160413": lambda c, sgg_town, sgg_city, town: sgg_city == "4290100" if c == "4" else True,
}


def special(day: str, code: str, sgg_city: str, town: str, sgg_town: str) -> bool:
    if day in S_FOR_ALL:
        return True
    rule = S_RULES.get(day)
    return bool(rule(code, sgg_town, sgg_city, town)) if rule else False


# The polling-district page prints these days' counts without the neighborhoods' names (the by-election shared its
# day with the local elections), so their count pages are the neighborhood ones (개표현황(읍면동별), VCCP04).
NEIGHBORHOOD = {("20180613", "2"), ("20220601", "2")}


def form(day: str, code: str, city: str, sgg_city: str, from_sgg: str, town: str, sgg_town: str) -> dict[str, str]:
    menu, statement = "VCCP08", "VCCP08_#1_0_S" if special(day, code, sgg_city, town, sgg_town) else "VCCP08_#1_0"
    if (day, code) in NEIGHBORHOOD:
        menu, statement = "VCCP04", "VCCP04_#2_0"
    return {
        "electionId": "0000000000",
        "requestURI": f"/electioninfo/0000000000/vc/{menu.lower()}.jsp",
        "topMenuId": "VC",
        "secondMenuId": menu,
        "menuId": menu,
        "statementId": statement,
        "oldElectionType": "1",
        "electionType": "0",
        "electionName": day,
        "electionCode": code,
        "cityCode": city,
        "sggCityCode": sgg_city,
        "townCodeFromSgg": from_sgg,
        "townCode": town,
        "sggTownCode": sgg_town,
    }


def winners_form(day: str, code: str, city: str) -> dict[str, str]:
    return {
        "electionId": "0000000000",
        "requestURI": "/electioninfo/0000000000/ep/epei01.jsp",
        "topMenuId": "EP",
        "secondMenuId": "EPEI01",
        "menuId": "EPEI01",
        "statementId": "EPEI01_#1",
        "oldElectionType": "1",
        "electionType": "0",
        "electionName": day,
        "electionCode": code,
        "cityCode": city,
        "sggCityCode": "-1",
        "townCodeFromSgg": "-1",
        "townCode": "-1",
        "sggTownCode": "-1",
    }


def address(data: dict[str, str]) -> str:
    return f"{REPORT}?{urlencode(data)}"


def _get(url: str, data: dict[str, str] | None = None) -> bytes:
    for attempt in range(8):
        try:
            response = (
                httpx.get(url, timeout=120, headers=HEADERS)
                if data is None
                else httpx.post(url, data=data, timeout=180, headers=HEADERS)
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == 7:
                raise
            time.sleep(4 * (attempt + 1))
    raise AssertionError


def _store(url: str, content: bytes, suffix: str, media: str) -> None:
    with LOCK:
        raw.store(SOURCE, url, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)
        KNOWN.add(url)


def _json(path: str, query: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{SELECT}/{path}?{urlencode(query)}"
    if not KNOWN:
        KNOWN.update(raw.keys(SOURCE))
    if url not in KNOWN:
        for _ in range(10):
            content = _get(url)
            try:
                json.loads(content)
            except ValueError:
                time.sleep(3)
                continue
            _store(url, content, ".json", "application/json")
            break
        else:
            raise ValueError(f"{url}: no JSON")
    return list(json.loads(raw.cached(SOURCE, url).path.read_text())["jsonResult"]["body"])


def _pairs(path: str, query: dict[str, str]) -> dict[str, str]:
    return {str(row["CODE"]): str(row["NAME"]) for row in _json(path, query)}


def cities(day: str, code: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionCode": day, "subElectionCode": code}
    return _pairs("selectbox_cityCodeBySgJson_JAEBO_Old.json", query)


def towns(day: str, code: str, city: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionCode": day, "cityCode": city, "subElectionCode": code}
    return _pairs("selectbox_AllTownCodeBySgJson_JAEBO_Old.json", query)


def sgg_towns(day: str, code: str, town: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionName": day, "electionCode": code, "townCode": town}
    return _pairs("selectbox_getSggTownCodeJson_Old.json", query)


def sgg_cities(day: str, code: str, city: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionName": day, "electionCode": code, "cityCode": city}
    return _pairs("selectbox_getSggCityCodeJson_Old.json", query)


Job = tuple[str, str, str, str, str, str, str]


def jobs(day: str) -> list[Job]:
    found: list[Job] = []
    for code in CODES:
        for city in cities(day, code):
            office = towns(day, code, city)
            if code in {"4", "9", "10"}:
                for sgg_city in sgg_cities(day, code, city):
                    found += [(day, code, city, sgg_city, town, "-1", "-1") for town in office]
            elif code in {"5", "6"}:
                for town in office:
                    found += [(day, code, city, "-1", "-1", town, sgg) for sgg in sgg_towns(day, code, town)]
            else:
                found += [(day, code, city, "-1", town, town, "-1") for town in office]
    return found


def winner_jobs(day: str) -> list[tuple[str, str, str]]:
    return [(day, code, city) for code in CODES for city in cities(day, code)]


def complete(html: str) -> bool:
    start = html.find('id="table01"')
    return start >= 0 and "<tbody" in html[start:]


def _page(data: dict[str, str]) -> None:
    url = address(data)
    if url in KNOWN:
        return
    for _ in range(30):
        content = _get(REPORT, data)
        if complete(content.decode(errors="replace")):
            _store(url, content, ".html", "text/html")
            return
        time.sleep(3)
    raise ValueError(f"{url}: the page keeps coming back without its table")


def _attempt(data: dict[str, str]) -> str | None:
    try:
        _page(data)
    except (ValueError, httpx.HTTPError) as error:
        return str(error)
    return None


TAG = re.compile(r"<[^>]+>")


def has_rows(html: str) -> bool:
    start = html.find('id="table01"')
    return (
        start >= 0 and "조회된 자료가 없습니다" not in html[start:] and "정보가 존재하지 않습니다" not in html[start:]
    )


def fetch(days: tuple[str, ...] = DATES, threads: int = 4) -> None:
    KNOWN.update(raw.keys(SOURCE))
    for day in days:
        counts = [form(*job) for job in jobs(day)]
        winners = [winners_form(*job) for job in winner_jobs(day)]
        with ThreadPoolExecutor(threads) as pool:
            failed = [error for error in pool.map(_attempt, [*counts, *winners]) if error]
        for error in failed:
            print(error, flush=True)
        print(f"{day}: {len(counts)} count pages, {len(winners)} winners pages", flush=True)
