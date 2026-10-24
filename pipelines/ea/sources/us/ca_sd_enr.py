import csv
import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_sd_enr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "San Diego County Registrar of Voters"
TITLE = "Election night reporting, final results by precinct"
LISTING = "https://www.livevoterturnout.com/ENR/sandiegocaenr/24/en/Index_24.html"
URL = "https://www.livevoterturnout.com/ENR/sandiegocaenr/{id}/precincts_{id}.csv"
ELECTIONS = {
    3: date(2018, 6, 5),
    5: date(2018, 11, 6),
    8: date(2020, 3, 3),
    10: date(2020, 11, 3),
    13: date(2021, 9, 14),
    15: date(2022, 6, 7),
    16: date(2022, 11, 8),
    18: date(2023, 11, 7),
    19: date(2024, 3, 5),
    20: date(2024, 11, 5),
    23: date(2025, 11, 4),
    24: date(2026, 6, 2),
}
METHOD = (
    "Precinct totals from the San Diego County Registrar of Voters' election night reporting export, matched to the "
    "contest by candidate and checked against its official totals."
)


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, URL.format(id=election), suffix=".csv")


def parse() -> None:
    rows = []
    for election, day in ELECTIONS.items():
        text = raw.cached(SOURCE, URL.format(id=election)).path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("Precinct,"))
        for precinct, contest, candidate, votes, *_ in csv.reader(io.StringIO("\n".join(lines[start + 1 :]))):
            if votes.isdigit():
                rows.append(
                    {
                        "day": day,
                        "url": URL.format(id=election),
                        "contest": contest,
                        "precinct": precinct,
                        "registered": None,
                        "ballots": None,
                        "candidate": candidate,
                        "votes": int(votes),
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema_overrides={"registered": pl.Int64, "ballots": pl.Int64}).write_parquet(
        PARSED / "rows.parquet"
    )


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="San Diego County's contests by precinct from the election night reporting exports",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="06073",
        place="SAN DIEGO",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
