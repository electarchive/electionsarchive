import base64
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES, get

API = "https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/"
LEGISLATURE = re.compile(r"STATE (REPRESENTATIVE|SENATOR),? DISTRICT (\d+)( - UNEXPIRED TERM)?")


def unpack(content: bytes) -> dict:
    return json.loads(base64.b64decode(json.loads(content)["upload"]))


def collect(client: httpx.Client, store: Callable[[str, str, bytes, str], None]) -> None:
    if "ENR_2026_TX_EID" in os.environ:
        ids = os.environ["ENR_2026_TX_EID"].split(",")
    else:
        listing = get(client, f"{API}electionConstants")
        if listing is None:
            print("TX: no election listing")
            return
        ids = [
            str(election["ID"])
            for kind in unpack(listing)["electionInfo"].get(str(DAY.year), {}).values()
            for election in kind.values()
            if election["O"] == "N" or f"{DAY:%B}".upper() in election["N"]
        ]
    found = False
    for id in ids:
        content = get(client, f"{API}election/{id}")
        if content is None:
            continue
        home = json.loads(base64.b64decode(json.loads(content)["Home"]))
        if home.get("ElecDate") != f"{DAY:%m%d%Y}":
            continue
        counties = get(client, f"{API}election/countyInfo/{id}")
        if counties is None:
            print(f"TX: no county file for {id}")
            continue
        store("TX", f"{API}election/{id}", content, ".json")
        store("TX", f"{API}election/countyInfo/{id}", counties, ".json")
        found = True
    if not found:
        print(f"TX: no election on {DAY}")


def rows(url: str, path: Path, urls: dict[str, Path]):
    if "/countyInfo/" not in url:
        return
    election = urls.get(url.replace("/countyInfo/", "/"))
    home = json.loads(base64.b64decode(json.loads(election.read_bytes())["Home"])) if election else {}
    stamp = home.get("LastUpdatedTime")
    as_of = datetime.strptime(stamp, "%b %d, %Y %H:%M:%S").replace(tzinfo=UTC).isoformat() if stamp else None
    counties = unpack(path.read_bytes())
    precincts: dict[str, list[int]] = {}
    for county in counties.values():
        for id, race in county["Races"].items():
            tally = precincts.setdefault(id, [0, 0])
            tally[0] += int(race.get("PR") or 0)
            tally[1] += int(race.get("TP") or 0)
    for county in counties.values():
        for id, race in county["Races"].items():
            name = " ".join(race["N"].split())
            found = EV_OFFICES.fullmatch(name)
            seat = LEGISLATURE.fullmatch(name)
            for candidate in race["C"].values():
                yield {
                    "office": ("house" if seat[1] == "REPRESENTATIVE" else "senate")
                    if seat
                    else ("state" if race.get("OT") == "SW" else "other")
                    if found is None
                    else "senate"
                    if found["senate"]
                    else "governor"
                    if found["governor"]
                    else "house",
                    "contest": name if found is None and seat is None else "",
                    "district": f"{seat[2]}:{'gs' if seat[3] else 'g'}"
                    if seat
                    else (found["district"] if found else None) or "",
                    "party": "" if candidate["P"] in ("W", "IND") else candidate["P"],
                    "county": county["N"].title(),
                    "candidate": re.sub(r"\s*\(I\)$", "", candidate["N"]).strip(),
                    "write_in": candidate["P"] == "W",
                    "votes": int(candidate["V"] or 0),
                    "as_of": as_of,
                    "reporting": precincts[id][0],
                    "total": precincts[id][1],
                }
