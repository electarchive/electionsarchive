import html
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES

LIST = "https://api.danecounty.gov/api/v1/Elections/List"
PAGE = "https://elections.countyofdane.com/Election-Result/"
LEGISLATURE = re.compile(r"(State Senator|Representative to the Assembly) District (\d+)")
STATEWIDE = re.compile(r"(Attorney General|Secretary of State|State Treasurer|State Superintendent|State Referendum).*")
RACE = re.compile(r"<h4>([^<]*)</h4>(.*?)Precincts Reporting \((\d+) of (\d+)\)", re.DOTALL)
ROW = re.compile(
    r'<td class="text-left"><strong>([^<]*)</strong>\s*(?:<small><em>\(([^)]*)\)</em></small>)?</td>.*?'
    r'<td class="text-right">([\d,]+)</td>',
    re.DOTALL,
)


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    listing = get(client, LIST)
    ids = [e["ElectionId"] for e in json.loads(listing or "[]") if e["ElectionDate"][:10] == DAY.isoformat()]
    content = ids and get(client, f"{PAGE}{ids[0]}")
    if not content:
        print("WI: no Dane County page")
        return
    store(LIST, listing, ".json")
    store(f"{PAGE}{ids[0]}", content, ".html")


def rows(url: str, path: Path, urls: dict[str, Path]):
    if not url.startswith(PAGE):
        return
    listing = urls.get(LIST)
    stamp = next(
        (
            e["LastPublished"]
            for e in json.loads(listing.read_bytes() if listing else "[]")
            if url.endswith(f"/{e['ElectionId']}")
        ),
        None,
    )
    as_of = datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("America/Chicago")).isoformat() if stamp else None
    for found in RACE.finditer(path.read_text(errors="replace")):
        name = re.sub(r" - (Official|Unofficial).*$", "", " ".join(html.unescape(found[1]).split()))
        office = EV_OFFICES.fullmatch(name)
        seat = LEGISLATURE.fullmatch(name)
        for label, party, votes in ROW.findall(found[2]):
            label = " ".join(html.unescape(label).split())
            party = party if party not in ("NON", "NP", "IND") else ""
            candidate = label.removeprefix(f"{party} ") if party else re.sub(r"^(IND|NON|NP) ", "", label)
            yield {
                "office": ("senate" if seat[1] == "State Senator" else "assembly")
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
                "county": "Dane",
                "candidate": re.sub(r"\s*/\s*", " / ", candidate),
                "write_in": label.lower().startswith("write-in"),
                "votes": int(votes.replace(",", "")),
                "as_of": as_of,
                "reporting": int(found[3]),
                "total": int(found[4]),
            }
