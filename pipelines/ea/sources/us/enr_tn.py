import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES, TN_AMENDMENT

DATA = "https://elect2.hamiltontn.gov/results/results/data.js"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
LEGISLATURE = re.compile(r"(?:Tennessee|TN) (Senate|House of Representatives),? District (\d+)")


def load(content: bytes) -> dict:
    text = content.decode(errors="replace")
    return json.loads(text[text.index("{") : text.rindex("}") + 1])


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    content = get(client, DATA, HEADERS)
    if content is None:
        print("TN: no Hamilton data file")
        return
    held = datetime.strptime(load(content)["latest"]["electionDate"], "%B %d, %Y").date()
    if held != DAY:
        print(f"TN: Hamilton data file is for {held}, not {DAY}")
        return
    store(DATA, content, ".js")


def rows(url: str, path: Path):
    if url != DATA:
        return
    data = load(path.read_bytes())
    stamp = data["metadata"].get("lastUpdated")
    as_of = (
        datetime.strptime(stamp, "%B %d, %Y %I:%M %p").replace(tzinfo=ZoneInfo("America/New_York")).isoformat()
        if stamp
        else None
    )
    for contest in data["latest"]["contests"]:
        title = re.sub(r"^(?:REP|DEM) ", "", contest["title"]).strip()
        office = EV_OFFICES.fullmatch(title)
        seat = LEGISLATURE.fullmatch(title)
        for candidate in contest["candidates"]:
            yield {
                "office": ("senate" if seat[1] == "Senate" else "house")
                if seat
                else ("state" if TN_AMENDMENT.fullmatch(title) else "other")
                if office is None
                else "senate"
                if office["senate"]
                else "governor"
                if office["governor"]
                else "house",
                "contest": contest["title"] if office is None and seat is None else "",
                "district": f"{seat[2]}:g" if seat else (office["district"] if office else None) or "",
                "party": candidate.get("party") or "",
                "county": "Hamilton",
                "candidate": candidate["name"],
                "write_in": "write-in" in candidate["name"].lower(),
                "votes": int(candidate["votes"]),
                "as_of": as_of,
                "reporting": int(contest["precinctsReporting"]),
                "total": int(contest["precinctsTotal"]),
            }
