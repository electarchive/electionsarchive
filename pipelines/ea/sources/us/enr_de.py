import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import DAY

BASE = "https://elections.delaware.gov/results/enr/"
ID = f"GE{DAY.year}"
STATEWIDE = f"{BASE}Election_StatewideResults_ID_{ID}.json"
COUNTIES = f"{BASE}Election_ByCountyWithWilmington_ID_{ID}.json"
SEAT = re.compile(r"State (Senator|Representative) District (\d+)")


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    statewide = get(client, STATEWIDE)
    counties = statewide and get(client, COUNTIES)
    if not statewide or not counties or f'"{DAY}"'.encode() not in statewide:
        print(f"DE: no {ID} results files")
        return
    store(STATEWIDE, statewide, ".json")
    store(COUNTIES, counties, ".json")


def rows(url: str, path: Path, urls: dict[str, Path]):
    if url != COUNTIES or STATEWIDE not in urls:
        return
    contests: dict[str, tuple[int, int, str]] = {}
    for item in json.loads(urls[STATEWIDE].read_bytes()):
        contests[item["Contest Title"]] = (item["Precincts Reported"], item["Total Precincts"], item["ReportTime"])
    items = json.loads(path.read_bytes())
    cast: dict[tuple[str, str], int] = {}
    for item in items:
        for county in ("New Castle", "Kent", "Sussex"):
            key = (item["Contest Title"].strip(), county)
            cast[key] = cast.get(key, 0) + int(item.get(county) or 0)
    for item in items:
        title = item["Contest Title"].strip()
        seat = SEAT.fullmatch(title)
        office, district = (
            ("senate", "")
            if title == "U.S. Senator"
            else ("house", "0")
            if title == "Representative In Congress"
            else ("governor", "")
            if title.startswith("Governor")
            else (("senate" if seat[1] == "Senator" else "house"), f"{seat[2]}:g")
            if seat
            else ("other" if re.match(r"(New Castle|Kent|Sussex) County", title) else "state", "")
        )
        reporting, total, stamp = contests.get(title, (0, 0, None))
        for county in ("New Castle", "Kent", "Sussex"):
            if (
                seat
                and not cast[(title, county)]
                or re.match(r"(New Castle|Kent|Sussex) County", title)
                and not title.startswith(county)
            ):
                continue
            yield {
                "office": office,
                "contest": title if office in ("state", "other") else "",
                "district": district,
                "party": re.sub(r" Party$", "", item.get("Party Name") or ""),
                "county": county,
                "candidate": " ".join(item["Candidate Name"].split()),
                "write_in": "write" in item["Candidate Name"].lower(),
                "votes": int(item.get(county) or 0),
                "as_of": stamp,
                "reporting": reporting,
                "total": total,
            }
