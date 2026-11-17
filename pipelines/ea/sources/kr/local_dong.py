"""South Korea's local elections below the city, county, or district, from the National Election Commission.

The statistics system prints the local elections of 1995, 1998, and 2002 by polling district (개표현황(투표구별),
VCCP08) and those from 2006 on by neighborhood (개표현황(읍면동별), VCCP04), where the commission stopped publishing
polling districts. Each page covers one counting office (구시군위원회) for one contest: a governor's or a
provincial list contest by counting office, a mayor's or a municipal list contest by counting office within the
mayoralty, and a council contest by constituency within the counting office. `kr.nec_stats` builds the counts at
these units into the local election days.
"""

import hashlib
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
from ea.sources.kr import local
from ea.sources.kr.nec import BASE, CHANNELS, REPORT
from ea.store import DATA

SOURCE = "kr.local_dong"
PARSED = DATA / "parsed" / SOURCE
DATES = tuple(day for day in local.DATES if day <= "20220601")
CODES = ("3", "4", "5", "6", "8", "9")
SELECT = f"{BASE}/bizcommon/selectbox"
FLUSH = 200


def old(day: str) -> bool:
    return day <= "20020613"


def codes(day: str) -> tuple[str, ...]:
    """The contests the commission prints for a day: no provincial lists before 2002, no municipal lists before 2006."""
    return tuple(
        code for code in CODES if not (code == "8" and day < "20020613") and not (code == "9" and day < "20060531")
    )


def _suffix(day: str) -> str:
    return "GuOld" if old(day) else "Old"


def _menu(day: str, code: str) -> tuple[str, str]:
    if old(day):
        return "VCCP08", "VCCP08_#E1" if day == "20020613" and code == "6" else "VCCP08_#1"
    return "VCCP04", "VCCP04_#1" if day < "20140604" else "VCCP04_#2"


def form(day: str, code: str, city: str, sgg_city: str, town_from_sgg: str, town: str, sgg_town: str) -> dict[str, str]:
    menu, statement = _menu(day, code)
    return {
        "electionId": "0000000000",
        "requestURI": f"/electioninfo/0000000000/vc/{menu.lower()}.jsp",
        "topMenuId": "VC",
        "secondMenuId": menu,
        "menuId": menu,
        "statementId": statement,
        "oldElectionType": "0" if old(day) else "1",
        "electionType": "4",
        "electionName": day,
        "electionCode": code,
        "cityCode": city,
        "sggCityCode": sgg_city,
        "townCodeFromSgg": town_from_sgg,
        "townCode": town,
        "sggTownCode": sgg_town,
    }


Job = tuple[str, str, str, str, str, str, str]


def key(job: Job) -> str:
    return f"{REPORT}?{urlencode(form(*job))}"


def _get(url: str, data: dict[str, str] | None = None) -> bytes:
    for attempt in range(8):
        try:
            response = (
                httpx.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
                if data is None
                else httpx.post(url, data=data, timeout=180, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE})
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == 7:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError


PENDING: dict[str, dict[str, str | int | None]] = {}
INDEX: dict[str, dict[str, str | int | None]] = {}
LOCK = threading.RLock()


def _write(address: str, content: bytes, suffix: str, media: str) -> None:
    """Write the file now and its index entry in batches, since the index of thousands of pages is slow to rewrite."""
    folder = DATA / "raw" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    (folder / f"{digest}{suffix}").write_bytes(content)
    entry: dict[str, str | int | None] = {
        "sha256": digest,
        "file": f"{digest}{suffix}",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "media_type": media,
        "bytes": len(content),
    }
    with LOCK:
        PENDING[address] = entry
        if len(PENDING) >= FLUSH:
            flush()


def flush() -> None:
    with LOCK:
        if not PENDING:
            return
        with raw.locked(SOURCE) as index:
            index.update(PENDING)
        INDEX.update(PENDING)
        PENDING.clear()


KNOWN: set[str] = set()


def _have(address: str) -> bool:
    return address in KNOWN or address in PENDING


def _json(path: str, query: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{SELECT}/{path}?{urlencode(query)}"
    if not _have(url):
        for _ in range(10):
            content = _get(url)
            try:
                json.loads(content)
            except ValueError:
                time.sleep(3)
                continue
            _write(url, content, ".json", "application/json")
            break
        else:
            raise ValueError(f"{url}: no JSON")
    entry = PENDING.get(url) or INDEX.get(url) or raw._read(SOURCE)[url]
    return json.loads((DATA / "raw" / SOURCE / str(entry["file"])).read_text())["jsonResult"]["body"]


def _pairs(path: str, query: dict[str, str]) -> dict[str, str]:
    return {str(row["CODE"]): str(row["NAME"]) for row in _json(path, query)}


def cities(day: str, code: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionCode": day, "subElectionCode": code}
    return _pairs(f"selectbox_cityCodeBySgJson_{_suffix(day)}.json", query)


def towns(day: str, code: str, city: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionCode": day, "cityCode": city, "subElectionCode": code}
    return _pairs(f"selectbox_townCodeBySgJson_{_suffix(day)}.json", query)


# Constituencies the selector leaves out of a counting office that counts part of them, as the statistics
# system's own page adds them: Masan's 7th provincial council constituency in 1995 is also counted in 48081.
EXTRA = {("19950627", "5", "48081"): {"54807007": "마산시제7"}}


def sgg_towns(day: str, code: str, town: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionName": day, "electionCode": code, "townCode": town}
    return _pairs(f"selectbox_getSggTownCodeJson_{_suffix(day)}.json", query) | EXTRA.get((day, code, town), {})


def sgg_cities(day: str, code: str, city: str) -> dict[str, str]:
    query = {"electionId": "0000000000", "electionName": day, "electionCode": code, "cityCode": city}
    return _pairs(f"selectbox_getSggCityCodeJson_{_suffix(day)}.json", query)


def towns_from_sgg(day: str, code: str, sgg_city: str) -> dict[str, str]:
    """The counting offices of a mayoralty; before 2006 none are listed and the page takes the whole mayoralty."""
    query = {"electionId": "0000000000", "electionName": day, "electionCode": code, "sggCityCode": sgg_city}
    return _pairs("selectbox_townCodeFromSggJson_Old.json", query) or {"-1": ""}


def jobs(day: str, code: str) -> list[Job]:
    found: list[Job] = []
    for city in cities(day, code):
        if code in {"3", "8"}:
            found += [(day, code, city, "-1", "-1", town, "-1") for town in towns(day, code, city)]
        elif code in {"5", "6"}:
            for town in towns(day, code, city):
                found += [(day, code, city, "-1", "-1", town, sgg) for sgg in sgg_towns(day, code, town)]
        else:
            for sgg_city in sgg_cities(day, code, city):
                found += [(day, code, city, sgg_city, town, "-1", "-1") for town in towns_from_sgg(day, code, sgg_city)]
    return found


def complete(html: str) -> bool:
    """A page is whole when it has its table and its rows carry their unit names; the server sometimes returns the
    figures with the names left out."""
    start = html.find('id="table01"')
    if start < 0 or "<tbody" not in html[start:]:
        return False
    try:
        rows = parse_page(html)["rows"]
    except ValueError:
        return False
    return not rows or sum(1 for row in rows if not any(row["labels"])) == 0


def _cached(address: str) -> str | None:
    entry = PENDING.get(address) or INDEX.get(address)
    if entry is None:
        return None
    return (DATA / "raw" / SOURCE / str(entry["file"])).read_text(errors="replace")


def _page(job: Job) -> None:
    address = key(job)
    html = _cached(address)
    if html is not None and complete(html):
        return
    for _ in range(30):
        content = _get(REPORT, form(*job))
        if complete(content.decode(errors="replace")):
            _write(address, content, ".html", "text/html")
            return
        time.sleep(3)
    raise ValueError(f"{job}: the page keeps coming back without its table or its unit names")


def _attempt(job: Job) -> str | None:
    try:
        _page(job)
    except (ValueError, httpx.HTTPError) as error:
        return str(error)
    return None


def fetch(days: tuple[str, ...] = DATES, threads: int = 4) -> None:
    INDEX.update(raw._read(SOURCE))
    KNOWN.update(INDEX)
    try:
        for day in days:
            for code in codes(day):
                found = jobs(day, code)
                with ThreadPoolExecutor(threads) as pool:
                    failed = [error for error in pool.map(_attempt, found) if error]
                for error in failed:
                    print(error, flush=True)
                print(f"{day} {code}: {len(found)} pages", flush=True)
                flush()
    finally:
        flush()


FIELDS = ("electionName", "electionCode", "cityCode", "sggCityCode", "townCodeFromSgg", "townCode", "sggTownCode")
TAG = re.compile(r"<[^>]+>")


def _text(cell: str) -> str:
    return " ".join(TAG.sub(" ", cell.replace("&nbsp;", " ")).split())


def _number(text: str) -> int:
    return int(text.replace(",", "")) if text.strip() else 0


TOTALS = {"합계", "계"}
SUBTOTALS = {"소계"}
DAY_CHANNELS = {"선거일투표": 1, "관내사전투표": 2}


def parse_page(html: str) -> dict[str, Any]:
    """The header's options, the leading label columns, and every row of figures on one page."""
    start = html.index('id="table01"')
    head = html[start : html.index("</thead>", start)]
    rows_html = re.findall(r"<tr[^>]*>(.*?)</tr>", head, re.DOTALL)
    first = [_text(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", rows_html[0], re.DOTALL)]
    lead = first.index("선거인수")
    options: list[tuple[str, str]] = []
    for cell in re.findall(r"<th[^>]*>(.*?)</th>", rows_html[1], re.DOTALL):
        parts = [_text(part) for part in re.split(r"<br\s*/?>", cell)]
        parts = [part for part in parts if part]
        if parts == ["계"]:
            continue
        options.append((parts[0], parts[1]) if len(parts) == 2 else (parts[0] if parts else "", ""))
    body = html[html.index("<tbody", start) : html.index("</tbody>", start)]
    width = len(options)
    rows: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
        cells = [_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)]
        if not cells or "조회된 자료가 없습니다" in cells[0]:
            continue
        if len(cells) != lead + width + 5:
            raise ValueError(f"row {cells} against {lead} labels and {width} options")
        figures = [_number(cell) for cell in cells[lead:]]
        rows.append(
            {
                "labels": cells[:lead],
                "registered": figures[0],
                "ballots": figures[1],
                "votes": figures[2 : 2 + width],
                "valid": figures[2 + width],
                "invalid": figures[3 + width],
            }
        )
    return {"options": options, "lead": lead, "rows": rows}


def units(page: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The page's total row and its leaves: each neighborhood's channels (or the neighborhood when it has none),
    each polling district, and the counting office's own absentee, early, and overseas rows."""
    total = None
    leaves: list[dict[str, Any]] = []
    dong = ""
    lead = page["lead"]
    for row in page["rows"]:
        labels = row["labels"]
        first = labels[0]
        second = labels[1] if lead > 1 else ""
        if first in TOTALS and not second:
            total = row
            continue
        if first in SUBTOTALS:
            continue
        if lead > 1 and second == "계":
            dong = first
            leaves.append({**row, "unit": first, "channel": 0, "subtotal": True})
            continue
        if lead > 1 and second in DAY_CHANNELS:
            leaves.append({**row, "unit": first or dong, "channel": DAY_CHANNELS[second], "subtotal": False})
            continue
        if lead > 1 and second:
            raise ValueError(f"unknown row kind {labels}")
        channel = CHANNELS.get(first, 0)
        leaves.append({**row, "unit": first, "channel": channel, "subtotal": False, "special": channel > 0})
    with_channels = {leaf["unit"] for leaf in leaves if not leaf["subtotal"] and leaf["channel"] in (1, 2)}
    leaves = [leaf for leaf in leaves if not (leaf["subtotal"] and leaf["unit"] in with_channels)]
    return total, leaves


def _names(
    day: str, code: str, city: str, sgg_city: str, town_from_sgg: str, town: str, sgg_town: str
) -> dict[str, str]:
    """The counting office's name and the contest's: a mayoralty for mayors and municipal lists, a constituency for
    councils."""
    if code in {"4", "9"}:
        sgg = sgg_cities(day, code, city)[sgg_city]
        office = towns_from_sgg(day, code, sgg_city).get(town_from_sgg) or sgg
        return {"town_name": office, "sgg_name": sgg}
    office = towns(day, code, city)[town]
    return {"town_name": office, "sgg_name": sgg_towns(day, code, town)[sgg_town] if code in {"5", "6"} else ""}


def parse() -> None:
    index = raw._read(SOURCE)
    KNOWN.update(index)
    found: list[dict[str, Any]] = []
    for address in sorted(index):
        if not address.startswith(REPORT):
            continue
        query = dict(part.split("=", 1) for part in address.split("?", 1)[1].split("&"))
        html = (DATA / "raw" / SOURCE / str(index[address]["file"])).read_text()
        page = parse_page(html)
        total, leaves = units(page)
        sums = [sum(column) for column in zip(*(leaf["votes"] for leaf in leaves), strict=True)] if leaves else []
        if total is not None and leaves and sums != total["votes"]:
            print(f"{address}: leaves {sums} != total {total['votes']}", flush=True)
        found.append(
            {
                "day": query["electionName"],
                "code": query["electionCode"],
                "city": query["cityCode"],
                "sgg_city": query["sggCityCode"],
                "town_from_sgg": query["townCodeFromSgg"],
                "town": query["townCode"],
                "sgg_town": query["sggTownCode"],
                "address": address,
                **_names(*(query[k] for k in FIELDS)),
                "options": page["options"],
                "total": total,
                "leaves": [
                    {k: leaf[k] for k in ("unit", "channel", "registered", "ballots", "votes", "valid", "invalid")}
                    | {"special": bool(leaf.get("special"))}
                    for leaf in leaves
                ],
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "pages.json").write_text(json.dumps(found, ensure_ascii=False))
    print(f"{len(found)} pages", flush=True)
