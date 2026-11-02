import json
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

API = "https://orresultswebservices.azureedge.us/ResultsAjax.svc/"
RACES = {
    "FED": range(300038695, 300038702),
    "SWPAR": range(300038702, 300038703),
    "SENATE": range(300038703, 300038718),
    "HOUSE": range(300038718, 300038778),
}
SEAT = re.compile(r"(US Representative|State Senator|State Representative), (\d+)(?:st|nd|rd|th) District")


def pages() -> list[str]:
    if "ENR_2026_OR_RACES" in os.environ:
        races = [pair.split(":") for pair in os.environ["ENR_2026_OR_RACES"].split(",")]
    else:
        races = [(kind, str(race)) for kind, ids in RACES.items() for race in ids]
    archive = f"Archive?electionID={os.environ['ENR_2026_OR_EID']}&" if "ENR_2026_OR_EID" in os.environ else "?"
    return [
        f"{API}GetMapData{archive}type={kind}&category=CTY&raceID={race}&osn=0&county=0&party=0" for kind, race in races
    ]


def collect(client: httpx.Client, get: Callable[..., bytes | None], store: Callable[[str, bytes], None]) -> None:
    with ThreadPoolExecutor(8) as pool:
        for url, content in zip(urls := pages(), pool.map(lambda url: get(client, url), urls), strict=True):
            if content and json.loads(content).get("d"):
                store(url, content)


def rows(url: str, path: Path, urls: dict[str, Path]):
    entries = json.loads(path.read_bytes()).get("d") or []
    if not entries:
        return
    name = " ".join(entries[0]["RaceName"].split())
    seat = SEAT.fullmatch(name)
    if name == "US Senator":
        office, district = "senate", ""
    elif name == "Governor":
        office, district = "governor", ""
    elif seat:
        office = "senate" if seat[1] == "State Senator" else "house"
        district = seat[2] + ("" if seat[1] == "US Representative" else ":g")
    else:
        return
    counties = {entry["CountyID"]: entry for entry in entries}
    reporting = sum(int(entry["PrecinctsReporting"] or 0) for entry in counties.values())
    total = sum(int(entry["TotalPrecincts"] or 0) for entry in counties.values())
    for entry in entries:
        candidate = " ".join((entry["calcCandidate"] or "").split())
        yield {
            "office": office,
            "district": district,
            "party": (entry["PartyName"] or "").split(",")[0].strip(),
            "county": entry["CountyName"],
            "candidate": candidate,
            "write_in": candidate.lower() == "write-in",
            "votes": int(entry["calcCandidateVotes"] or 0),
            "as_of": None,
            "reporting": reporting,
            "total": total,
        }
