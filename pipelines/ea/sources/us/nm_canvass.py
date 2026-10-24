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

SOURCE = "us.nm_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Mexico Secretary of State"
TITLE = "Canvass of Returns of General Election"
LANDING = "https://www.sos.nm.gov/sos-archive/election-results-archive/"
BASE = "https://klvg4oyd4j.execute-api.us-west-2.amazonaws.com/prod/PublicFiles/ee3072ab0d43456cb15a51f7d82c77a2/"
FILES = {
    date(1992, 11, 3): BASE + "bc0f917b-b2b1-4642-95ec-102685fc82b6/CanvassGeneral1992.pdf",
    date(1994, 11, 8): BASE + "61ee7e43-1b3d-48d1-8070-546c1eba66e5/CanvassGeneral1994.pdf",
    date(1996, 11, 5): BASE + "921fd2fa-c28a-495e-b09a-63e93177ef99/1996%20General%20Summary.pdf",
}
METHOD = (
    "County totals are from the State Canvassing Board's handwritten Canvass of Returns of General Election (the "
    "Secretary of State's scan), read from the page images. Each candidate's county sum equals the total printed "
    "in the canvass, except Robert J. Aragon in 1992's first district, whose county cells sum to 76,100 against a "
    "printed total of 76,600."
)


def fetch() -> None:
    for url in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sL", "-m", "300", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '35%%'"""
            ).fetchall()
        }
    rows = []
    with (Path(__file__).parent / "nm_canvass.tsv").open() as handle:
        for year, district, candidate, cells in csv.reader(handle, delimiter="\t"):
            day = next(day for day in FILES if day.year == int(year))
            for cell in cells.split(", "):
                name, votes = cell.rsplit(" ", 1)
                key = re.sub(r"[^a-z]", "", name.lower())
                key = key if key in counties else difflib.get_close_matches(key, counties, 1, 0.7)[0]
                rows.append((str(day), int(district), counties[key], candidate, int(votes)))
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
        "New Mexico's U.S. House general elections of 1992 through 1996 by county from the State Canvassing Board's canvasses",
        PUBLISHER,
        TITLE,
        LANDING,
        "NM",
        "35",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url) for url in FILES.values()],
        lambda key: METHOD,
    )
