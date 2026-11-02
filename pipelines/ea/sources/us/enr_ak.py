import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from collections.abc import Callable
from pathlib import Path

import httpx

URL = "https://www.elections.alaska.gov/results/26GENR/results/statewide.js"
PARTIES = {
    "REP": "Republican",
    "DEM": "Democratic",
    "LIB": "Libertarian",
    "NON": "Nonpartisan",
    "UND": "Undeclared",
}
NICKNAMES = {"Kreiss-Tomkins": "Kreiss-Tomkins “JKT”"}
MEASURE = re.compile(r"BM#(\d+)\b")
SEAT = re.compile(r"(House|Senate) District (\w+)")


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    with httpx.Client(verify=False, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as insecure:
        content = get(insecure, URL)
    if content and content.lstrip().startswith(b"{"):
        store(URL, content, ".json")
    else:
        print("AK: no 2026 general results file")


def candidate(label: str) -> tuple[str, str, bool]:
    name = label.strip()
    party = re.search(r"\s*\(([A-Z/]+)\)$", name)
    if party:
        name = name[: party.start()]
    code = party[1].split("/")[0] if party else ""
    if "/" in name:
        ticket = name.split("/")[0]
        return NICKNAMES.get(ticket, ticket), PARTIES.get(code, ""), False
    last, _, first = name.partition(", ")
    full = f"{first} {last}".strip() if first else name
    return full, PARTIES.get(code, ""), not party and name.upper() not in ("YES", "NO")


def rows(url: str, path: Path, urls: dict[str, Path]):
    if url != URL:
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    contests = data["contestNames"]
    names = data["candidateNames"]
    stamp = (
        datetime.strptime(data["updated"], "%m/%d/%Y %I:%M %p")
        .replace(tzinfo=ZoneInfo("America/Anchorage"))
        .astimezone(UTC)
        .isoformat()
    )

    def tally(entry: dict, place: str, office: str, contest: str, district: str):
        for result in entry["c"]:
            name, party, write_in = candidate(names[result["n"]])
            yield {
                "office": office,
                "contest": contest,
                "district": district,
                "party": party,
                "scope": "",
                "county": place,
                "candidate": name,
                "write_in": write_in,
                "votes": result["t"],
                "as_of": stamp,
                "reporting": entry.get("r", 0),
                "total": entry.get("p", 0),
            }

    def classify(title: str) -> tuple[str, str, str] | None:
        if title == "U.S. Senator":
            return "senate", "", ""
        if title == "U.S. Representative":
            return "house", "", "0"
        if title.startswith("Governor"):
            return "governor", "", ""
        if measure := MEASURE.match(title):
            return "state", f"Ballot Measure {measure[1]}", ""
        if title.startswith("Supreme Court - "):
            return "state", f"Supreme Court Justice {title.partition(' - ')[2]}", ""
        return None

    for district in data["districts"]:
        for entry in district["contests"]:
            title = contests[entry["n"]]
            if kind := classify(title):
                yield from tally(entry, f"AK:{district['number']}", *kind)
            elif (seat := SEAT.fullmatch(title)) and seat[1] == "House":
                yield from tally(entry, f"AK:{seat[2]}", "house", "", f"{seat[2]}:g")
    for entry in data["totals"]:
        if (seat := SEAT.fullmatch(contests[entry["n"]])) and seat[1] == "Senate":
            yield from tally(entry, f"AK:{seat[2].lower()}", "senate", "", f"{seat[2].lower()}:g")
