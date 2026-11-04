import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from ea.sources.us.enr_2026 import FIPS, table

BASE = "https://apps.npr.org/2026-election-results/data/"
OFFICES = {"S": "senate", "G": "governor", "H": "house"}


def collect(client: httpx.Client, state: str, store: Callable[[str, bytes, str], None]) -> None:
    response = client.get(f"{BASE}states/{state}.json")
    response.raise_for_status()
    races = [race for race in response.json()["results"] if not race.get("test") and race.get("office") in OFFICES]
    if not any(c.get("votes") for race in races for c in race.get("candidates") or []):
        return
    store(f"{BASE}states/{state}.json", response.content, ".json")
    for race in races:
        if race["office"] == "H":
            continue
        detail = client.get(f"{BASE}counties/{state}-{race['id']}.json")
        if detail.status_code < 400:
            store(f"{BASE}counties/{state}-{race['id']}.json", detail.content, ".json")


def frame(state: str, paths: dict[str, Path]) -> pl.DataFrame:
    races = {race["id"]: race for race in json.loads(paths[f"{BASE}states/{state}.json"].read_bytes())["results"]}
    found = []
    for id, race in races.items():
        if race.get("test") or race.get("office") not in OFFICES:
            continue
        url = f"{BASE}counties/{state}-{id}.json"
        district = (race.get("seat") or "").removeprefix("District ") if race["office"] == "H" else ""
        towns = (
            [town for town in json.loads(paths[url].read_bytes())["results"] if not town["fips"].endswith("000")]
            if url in paths
            else [{**race, "fips": FIPS[state]}]
        )
        for town in towns:
            for candidate in town.get("candidates") or []:
                name = f"{candidate.get('first') or ''} {candidate.get('last') or ''}".strip()
                if name == "Other":
                    continue
                found.append(
                    {
                        "state": state,
                        "page": url if url in paths else f"{BASE}states/{state}.json",
                        "scope": "",
                        "contest": "",
                        "office": OFFICES[race["office"]],
                        "district": district,
                        "party": candidate.get("party"),
                        "county": town["fips"],
                        "candidate": name,
                        "write_in": False,
                        "votes": int(candidate.get("votes") or 0),
                        "as_of": datetime.fromtimestamp(race["updated"] / 1000, UTC).isoformat(),
                        "reporting": int(race.get("reporting") or 0),
                        "total": int(race.get("precincts") or 0),
                    }
                )
    return pl.DataFrame(found, schema=table({}).schema)
