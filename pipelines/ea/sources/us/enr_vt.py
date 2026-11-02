import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY

BASE = "https://static.electionresults.vermont.gov/"
PARTS = ("federal", "stateWide", "senate", "house", "county")
AGGREGATE = {"STATE WIDE", "COUNTY WIDE", "DISTRICT WIDE"}


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    listing = get(client, f"{BASE}elections/elections.json")
    guid = next(
        (
            e["electionGuid"]
            for e in json.loads(listing or "[]")
            if e["electionDate"].startswith(DAY.isoformat()) and e["isStateWideElection"] and e["isGeneralElection"]
        ),
        None,
    )
    election = guid and get(client, f"{BASE}elections/{guid}.json")
    if not election:
        print(f"VT: no {DAY} general election file")
        return
    store(f"{BASE}elections/{guid}.json", election, ".json")
    details = json.loads(election)
    for part in PARTS:
        if (details.get(part) or {}).get("isEnable"):
            url = BASE + details[part]["path"].replace("\\", "/")
            content = get(client, url)
            if content:
                store(url, content, ".json")


def district(text: str, senate: bool) -> str:
    text = text.strip().rstrip("_").lower()
    if senate:
        return re.sub(r"[\s-]+", "", text)
    return re.sub(r"\s+", "", re.sub(r" (\d+)$", r"-\1", text))


def rows(url: str, path: Path, urls: dict[str, Path]):
    if not re.search(r"-(f|s|sn|h|cty)-\d+\.json$", url):
        return
    county_file = next((p for u, p in urls.items() if re.search(r"-cty-\d+\.json$", u)), None)
    if county_file is None:
        return
    towns: dict[str, str] = {}
    for office in json.loads(county_file.read_bytes())["d"][0].get("co") or []:
        for town in office["cs"]:
            towns.setdefault(town["tn"].upper(), town["ctyn"].title())
    election = next((p for u, p in urls.items() if re.search(r"/elections/[0-9a-f-]{36}\.json$", u)), None)
    stamp = json.loads(election.read_bytes()).get("lastUpdatedDate") if election else None
    as_of = (
        datetime.strptime(stamp, "%m/%d/%Y %I:%M %p").replace(tzinfo=ZoneInfo("America/New_York")).isoformat()
        if stamp
        else None
    )
    payload = json.loads(path.read_bytes())["d"][0]
    for office in payload.get("o") or payload.get("co") or []:
        name = office["on"].strip()
        places = [town for town in office["cs"] if town["tn"].upper() not in AGGREGATE]
        done = [
            tuple(int(n) for n in (town.get("distReported") or town.get("townsReported") or "0/0").split("/"))
            for town in places
        ]
        reporting = sum(1 for n, total in done if total and n >= total)
        for town in places:
            county = towns.get(town["tn"].upper())
            if county is None:
                continue
            kind, seat, contest = (
                ("house", "0", "")
                if name == "REPRESENTATIVE TO CONGRESS"
                else ("governor", "", "")
                if name == "GOVERNOR"
                else ("senate", f"{district(town['send'], True)}:g", "")
                if name == "STATE SENATOR"
                else ("house", f"{district(town['repd'], False)}:g", "")
                if name == "STATE REPRESENTATIVE"
                else ("other", "", f"{town['ctyn'].title()} County {name.title()}")
                if office.get("otc") == "C"
                else ("state", "", name.title())
            )
            for choice in [*town.get("rc", []), *town.get("wc", [])]:
                yield {
                    "office": kind,
                    "contest": contest,
                    "district": seat,
                    "party": (choice.get("pn") or "").title(),
                    "county": county,
                    "candidate": " ".join((choice.get("cn") or "").split()),
                    "write_in": bool(choice.get("isWriteIn")) or "WRITE-IN" in (choice.get("cn") or "").upper(),
                    "votes": int(choice.get("vc") or 0),
                    "as_of": as_of,
                    "reporting": reporting,
                    "total": len(places),
                }
