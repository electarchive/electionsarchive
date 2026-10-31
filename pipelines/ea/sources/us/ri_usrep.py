import csv
import difflib
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.ri_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Count Book, General Election"
LANDING = "https://elections.ri.gov/publications/election-publications"
BASE = "https://elections.ri.gov/sites/g/files/xkgbur756/files/publications/Election_Publications/Countbooks/"
DAYS = {
    1992: date(1992, 11, 3),
    1994: date(1994, 11, 8),
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
}
METHOD = (
    "County totals are from the Board of Elections' Count Book for the election, read from the scan's text layer. "
    "Providence County is split between the districts, with Providence city's two wards reported as parts. "
    "Where the book prints town rows only (1994 and the second district in 1998), counties are the sums of their towns. "
    "Each candidate's county sum equals the state total printed in the book, except the fifth candidate in 1996's "
    "second district, whose state total is cut off in the scan."
)


def fetch() -> None:
    for year in DAYS:
        url = f"{BASE}{year}-Count-Book.pdf"
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sL", "-m", "600", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().removesuffix(" county")): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '44%%'"""
            ).fetchall()
        }
    rows = []
    with (Path(__file__).parent / "ri_usrep.tsv").open() as handle:
        for year, district, candidate, cells in csv.reader(handle, delimiter="\t"):
            for cell in cells.split(", "):
                name, votes = cell.rsplit(" ", 1)
                key = (
                    name.lower()
                    if name.lower() in counties
                    else difflib.get_close_matches(name.lower(), counties, 1, 0.7)[0]
                )
                rows.append((str(DAYS[int(year)]), int(district), counties[key], candidate, int(votes)))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("day", "district")
        .agg(pl.col("county").n_unique(), pl.col("votes").sum())
        .sort("day", "district")
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Rhode Island's U.S. House general elections of 1992 through 1998 by county from the Board of Elections' Count Books",
        PUBLISHER,
        TITLE,
        LANDING,
        "RI",
        "44",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, f"{BASE}{year}-Count-Book.pdf") for year in DAYS],
        lambda key: METHOD,
    )
