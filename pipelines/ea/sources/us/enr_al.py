import io
import os
import re
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import httpx

PAGE = "https://www2.alabamavotes.gov/electionnight/statewideresultsbycontest.aspx?ecode={}"
ECODE = os.environ.get("ENR_2026_AL_ECODE", "1001305")
HEADERS = {"User-Agent": "Mozilla/5.0"}
SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CENTRAL = timezone(timedelta(hours=-6))


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    url = PAGE.format(ECODE)
    page = get(client, url, HEADERS)
    if page is None:
        print("AL: no results page")
        return
    if b"Results Coming Soon" in page:
        print("AL: results coming soon")
        return
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page.decode()))
    try:
        response = client.post(
            url, data={**fields, "__EVENTTARGET": "hlnkExportData", "__EVENTARGUMENT": ""}, headers=HEADERS
        )
    except httpx.TransportError:
        print("AL: export failed")
        return
    if response.status_code == 200 and response.content.startswith(b"PK"):
        store(f"{url}#export", response.content, ".xlsx")
    else:
        print(f"AL: export returned {response.status_code}")


def sheets(path: Path) -> dict[str, list[dict[str, str]]]:
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as book:
        shared = [
            "".join(text.text or "" for text in item.iter(f"{SHEET}t"))
            for item in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).iter(f"{SHEET}si")
        ]
        names = [
            sheet.get("name") or ""
            for sheet in ElementTree.fromstring(book.read("xl/workbook.xml")).iter(f"{SHEET}sheet")
        ]
        found: dict[str, list[dict[str, str]]] = {}
        for number, name in enumerate(names, 1):
            grid: list[dict[str, str]] = []
            for row in ElementTree.fromstring(book.read(f"xl/worksheets/sheet{number}.xml")).iter(f"{SHEET}row"):
                cells: dict[str, str] = {}
                for cell in row.iter(f"{SHEET}c"):
                    value = cell.findtext(f"{SHEET}v") or "".join(t.text or "" for t in cell.iter(f"{SHEET}t"))
                    cells[re.sub(r"\d", "", cell.get("r") or "")] = (
                        shared[int(value)] if cell.get("t") == "s" and value else value
                    )
                grid.append(cells)
            header = grid[0] if grid else {}
            found[name] = [
                {header[column]: value for column, value in cells.items() if column in header} for cells in grid[1:]
            ]
        return found


def stamp(value: str) -> datetime | None:
    try:
        return datetime(1899, 12, 30, tzinfo=CENTRAL) + timedelta(days=float(value))
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=CENTRAL)
        except ValueError:
            continue
    return None


def rows(url: str, path: Path):
    from ea.sources.us.al_precincts import contest

    found = sheets(path)
    counties: dict[str, tuple[int, int, datetime | None]] = {}
    for row in found.get("Statistics", []):
        if row.get("County Code"):
            counties[row["County Code"]] = (
                int(float(row.get("Precincts Reported") or 0)),
                int(float(row.get("Total Precincts") or 0)),
                stamp(row.get("Last Updated") or ""),
            )
    latest = max((when for *_, when in counties.values() if when is not None), default=None)
    for row in found.get("AllResults", []):
        title = (row.get("Contest Title") or "").strip()
        candidate = " ".join((row.get("Candidate Name") or "").split())
        if not title or not row.get("County Name") or candidate in ("Over Votes", "Under Votes"):
            continue
        placed = contest(title)
        office, district, label = "state", "", title
        if placed is not None:
            key, seat = placed
            if key in ("governor", "senate"):
                office, label = key, ""
            elif key == "house" and seat:
                office, district, label = "house", str(seat), ""
            elif re.fullmatch(r"(senate|house):\d+:g", key):
                office, district, label = key.split(":")[0], key.split(":", 1)[1], ""
        reporting, total, _ = counties.get(row.get("County Code") or "", (0, 0, None))
        votes = (row.get("Votes") or "0").replace(",", "")
        yield {
            "office": office,
            "contest": label,
            "district": district,
            "party": row.get("Party Code") or "",
            "county": row["County Name"].strip().title(),
            "candidate": candidate,
            "write_in": "write" in candidate.lower(),
            "votes": int(float(votes)) if re.fullmatch(r"\d+(\.0+)?", votes) else 0,
            "as_of": latest.isoformat() if latest else None,
            "reporting": reporting,
            "total": total,
        }
