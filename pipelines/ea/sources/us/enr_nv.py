import html
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
PAGES = {"Clark": "https://elections.clarkcountynv.gov/ElectionResultsTV/ENR.aspx"}
LEGISLATURE = re.compile(r"STATE (SENATE|ASSEMBLY) DISTRICT (\d+)")
STATEWIDE = re.compile(
    r"(LIEUTENANT GOVERNOR|SECRETARY OF STATE|ATTORNEY GENERAL|STATE TREASURER|STATE CONTROLLER|"
    r"JUSTICE OF (THE )?SUPREME COURT|STATE QUESTION|REGENT|MEMBER STATE BOARD OF EDUCATION).*"
)
CONTEST = re.compile(r'_lblOffice"[^>]*>([^<]*)</span>(.*?)</table>', re.DOTALL)
ROW = re.compile(r'<tr valign="top"[^>]*>\s*<td[^>]*>([^<]*)</td>.*?<td align="right"[^>]*>([\d,]+)</td>', re.DOTALL)
STAMP = re.compile(r"Updated (\d+/\d+/\d+ \d+:\d+ [AP]M)")


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes], None]) -> None:
    for county, url in PAGES.items():
        content = get(client, url, HEADERS)
        title = re.search(rb'ELECTION_TITLELabel"[^>]*>([^<]*)', content or b"")
        if content is None or title is None or str(DAY.year).encode() not in title[1]:
            print(f"NV: no {DAY.year} page for {county}")
            continue
        store(url, content)


def rows(url: str, path: Path, urls: dict[str, Path]):
    county = next((name for name, page in PAGES.items() if url.startswith(page)), None)
    if county is None:
        return
    text = path.read_text(errors="replace")
    stamp = STAMP.search(text)
    as_of = (
        datetime.strptime(stamp[1], "%m/%d/%y %I:%M %p").replace(tzinfo=ZoneInfo("America/Los_Angeles")).isoformat()
        if stamp
        else None
    )
    for found in CONTEST.finditer(text):
        name = " ".join(html.unescape(found[1]).split())
        if name.startswith("REGISTRATION"):
            continue
        office = EV_OFFICES.fullmatch(name)
        seat = LEGISLATURE.fullmatch(name)
        for label, votes in ROW.findall(found[2]):
            label = " ".join(html.unescape(label).split())
            party, _, candidate = label.partition(" - ") if re.match(r"[A-Z]{2,4} - ", label) else ("", "", label)
            last, comma, first = candidate.partition(", ")
            yield {
                "office": ("senate" if seat[1] == "SENATE" else "assembly")
                if seat
                else ("state" if STATEWIDE.fullmatch(name) else "other")
                if office is None
                else "senate"
                if office["senate"]
                else "governor"
                if office["governor"]
                else "house",
                "contest": name if office is None and seat is None else "",
                "district": f"{seat[2]}:g" if seat else (office["district"] if office else None) or "",
                "party": party,
                "county": county,
                "candidate": f"{first} {last}" if comma else candidate,
                "write_in": False,
                "votes": int(votes.replace(",", "")),
                "as_of": as_of,
                "reporting": None,
                "total": None,
            }
