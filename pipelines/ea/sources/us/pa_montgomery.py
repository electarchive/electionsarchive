import json
from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.pa_montgomery"
VERSION = "1"
PLATFORM = "pa_montgomery"
SITE = "https://services1.arcgis.com/kOChldNuKsox8qZD/arcgis/rest/services/"
FIPS = "42"
YEARS = (2020, 2021, 2022, 2023, 2024, 2025)
PAGE = 1000
FIELDS = "Precinct_name,Split_name,Contest_title,Selectable_Options,candidate_name,Party_Code,total_votes"


def query(year: int, offset: int) -> str:
    return (
        f"{SITE}Montgomery_County_GE_{year}_Election_Results/FeatureServer/0/query?where=1%3D1&outFields={FIELDS}"
        f"&returnGeometry=false&orderByFields=OBJECTID&resultOffset={offset}&resultRecordCount={PAGE}&f=json"
    )


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for year in YEARS:
        offset = 0
        while True:
            page = json.loads(raw.fetch(SOURCE, query(year, offset), suffix=".json").path.read_text())
            offset += PAGE
            if not page.get("exceededTransferLimit"):
                break


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for year in YEARS:
        contests: dict[str, dict[str, Any]] = {}
        offset = 0
        while True:
            page = json.loads(raw.cached(SOURCE, query(year, offset)).path.read_text())
            for feature in page["features"]:
                row = feature["attributes"]
                contest = contests.setdefault(
                    row["Contest_title"], {"vote_for": row["Selectable_Options"], "options": {}}
                )
                place = row["Precinct_name"] + (f" {row['Split_name']}" if row["Split_name"] else "")
                places = contest["options"].setdefault((row["candidate_name"], row["Party_Code"]), {})
                places[place] = places.get(place, 0) + (row["total_votes"] or 0)
            offset += PAGE
            if not page.get("exceededTransferLimit"):
                break
        first = date(year, 11, 1)
        day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        nodes = []
        for n, (title, contest) in enumerate(contests.items()):
            choices = [
                {
                    "text": name,
                    "party": party,
                    "total": str(sum(places.values())),
                    "places": [(place, str(votes)) for place, votes in places.items()],
                }
                for (name, party), places in contest["options"].items()
            ]
            nodes.append({"key": str(n), "text": title, "vote_for": contest["vote_for"], "choices": choices})
        yield {
            "tenant": tenant,
            "election": str(year),
            "day": day,
            "title": f"{year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE))
