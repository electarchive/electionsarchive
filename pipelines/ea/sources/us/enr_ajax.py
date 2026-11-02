import csv
import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import DAY

SITES = {
    "NM": (
        "https://electionresults.sos.nm.gov/",
        "https://nmresultswebservice.azurewebsites.us/NMResultsAjax.svc/",
        ("FED", "SW", "LGX"),
    ),
    "SD": (
        "https://electionresults.sd.gov/",
        "https://sdresws.azurewebsites.us/ResultsAjax.svc/",
        ("SWR", "BQ", "LEG"),
    ),
    "ND": ("https://results.sos.nd.gov/", "https://resultsws.sos.nd.gov/ResultsAjax.svc/", ("SW", "LG")),
}
HEADERS = {"User-Agent": "Mozilla/5.0"}
DISTRICT = re.compile(r"(?i)district\s+0*(\d+)\s*([A-Z])?")


def collect(state: str, client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    site, service, kinds = SITES[state]
    home = get(client, site, HEADERS)
    if home is None or f"{DAY:%B} {DAY.day}, {DAY.year}".encode() not in home:
        print(f"{state}: results site does not show {DAY}")
        return
    for kind in kinds:
        listing = get(
            client,
            f"{site}resultsCSV.aspx?text=All&type={kind}&map={'DIST' if kind.startswith('L') else 'CTY'}",
            HEADERS,
        )
        races = {
            (row["RaceID"], row.get("OfficeSeqNo") or "0")
            for row in csv.DictReader((listing or b"").decode("utf-8-sig", errors="replace").splitlines())
            if row.get("RaceID")
        }
        for race, osn in sorted(races):
            url = f"{service}GetMapData?type={kind}&category=CTY&raceID={race}&osn={osn}&county=0&party=0"
            content = get(client, url, HEADERS)
            if content:
                store(url, content, ".json")


def place(race: str, area: str) -> tuple[str, str, str]:
    seat = DISTRICT.search(area)
    if re.fullmatch(r"(?i)United States Senator", race):
        return "senate", "", ""
    if re.fullmatch(r"(?i)United States Representative", race):
        return "house", seat[1] if seat else "0", ""
    if re.match(r"(?i)Governor", race):
        return "governor", "", ""
    if seat and re.fullmatch(r"(?i)State (Senator|Representative)", race):
        number = seat[1] + (f"-{seat[2].lower()}" if seat[2] else "")
        return ("senate" if "Senator" in race.title() else "house"), f"{number}:g", ""
    return "state", "", f"{race} {area}".strip()


def rows(url: str, path: Path):
    if "/GetMapData?" not in url:
        return
    found = json.loads(path.read_bytes())
    for item in (found.get("d") if isinstance(found, dict) else found) or []:
        if not item.get("CandidateID"):
            continue
        office, district, contest = place(item["RaceName"].strip(), (item.get("AreaNum") or "").strip())
        votes = str(item.get("calcCandidateVotes") or "0").replace(",", "")
        yield {
            "office": office,
            "contest": contest,
            "district": district,
            "party": item.get("PartyCode") or "",
            "county": re.sub(r"\s*\(.*\)$", "", item["CountyName"].strip()),
            "candidate": " ".join((item.get("calcCandidate") or "").split()),
            "write_in": "write" in (item.get("calcCandidate") or "").lower(),
            "votes": int(votes) if votes.isdigit() else 0,
            "as_of": None,
            "reporting": int(item.get("PrecinctsReporting") or 0),
            "total": int(item.get("TotalPrecincts") or 0),
        }
