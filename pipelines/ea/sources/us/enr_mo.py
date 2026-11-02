import csv
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES

FOLDERS = {"St. Louis": "https://extcontent.stlouisco.com/BOE/eResults/"}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
LEGISLATURE = re.compile(r"STATE (SENATOR|REPRESENTATIVE) - DISTRICT (\d+)")
STATEWIDE = re.compile(
    r"(LIEUTENANT GOVERNOR|SECRETARY OF STATE|STATE TREASURER|ATTORNEY GENERAL|STATE AUDITOR|STATE - .+)"
)
LISTED = re.compile(r"(\d+/\d+/\d{4} +\d+:\d+ [AP]M) +\d+ <A HREF=\"([^\"]+\.csv)\"", re.IGNORECASE)


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    for county, base in FOLDERS.items():
        folder = f"{base}el{DAY:%y%m%d}/"
        listing = get(client, folder, HEADERS)
        found = LISTED.search((listing or b"").decode(errors="replace"))
        content = found and get(client, str(httpx.URL(folder).join(found[2])), HEADERS)
        if not content:
            print(f"MO: no {DAY} file for {county}")
            continue
        store(folder, listing, ".html")
        store(f"{folder}CSV.csv", content, ".csv")


def rows(url: str, path: Path, urls: dict[str, Path]):
    county = next((name for name, base in FOLDERS.items() if url.startswith(base)), None)
    if county is None or not url.endswith(".csv"):
        return
    listing = urls.get(url.removesuffix("CSV.csv"))
    stamp = LISTED.search(listing.read_text(errors="replace")) if listing else None
    as_of = (
        datetime.strptime(" ".join(stamp[1].split()), "%m/%d/%Y %I:%M %p")
        .replace(tzinfo=ZoneInfo("America/Chicago"))
        .isoformat()
        if stamp
        else None
    )
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    votes: dict[tuple[str, str, str], int] = {}
    precincts: dict[str, set[str]] = {}
    reporting: dict[str, set[str]] = {}
    for row in csv.DictReader(text.splitlines()):
        title = " ".join(row["Contest Title"].split())
        precincts.setdefault(title, set()).add(row["#Precinct"])
        if row["Precinct Reporting"].strip() == "Reporting":
            reporting.setdefault(title, set()).add(row["#Precinct"])
        key = (title, row["Choice Name"].strip(), row["Choice Party"].strip())
        votes[key] = votes.get(key, 0) + int(row["Total Votes"] or 0)
    for (title, candidate, party), count in votes.items():
        office = EV_OFFICES.fullmatch(title)
        seat = LEGISLATURE.fullmatch(title)
        yield {
            "office": ("senate" if seat[1] == "SENATOR" else "house")
            if seat
            else ("state" if STATEWIDE.fullmatch(title) else "other")
            if office is None
            else "senate"
            if office["senate"]
            else "governor"
            if office["governor"]
            else "house",
            "contest": title if office is None and seat is None else "",
            "district": f"{seat[2]}:g" if seat else (office["district"] if office else None) or "",
            "party": party,
            "county": county,
            "candidate": candidate,
            "write_in": "WRITE-IN" in candidate.upper(),
            "votes": count,
            "as_of": as_of,
            "reporting": len(reporting.get(title, ())),
            "total": len(precincts[title]),
        }
