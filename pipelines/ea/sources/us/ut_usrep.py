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

SOURCE = "us.ut_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Utah Lieutenant Governor's Office"
TITLE = "Official Results, State of Utah General Election"
LANDING = "https://vote.utah.gov/historical-election-results/"
URL = "https://vote.utah.gov/wp-content/uploads/2023/09/{}Gen.pdf"
FILES = {
    date(1992, 11, 3): URL.format(1992),
    date(1994, 11, 8): URL.format(1994),
    date(1996, 11, 5): URL.format(1996),
    date(1998, 11, 3): URL.format(1998),
}
METHOD = (
    "County totals are from the Lieutenant Governor's official general election results (the office's scans), read "
    "from the page images. Each candidate's county sum equals the total printed in the report."
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
                   where u.scheme_code = 'us.county' and u.code like '49%%'"""
            ).fetchall()
        }
    rows = []
    with (Path(__file__).parent / "ut_usrep.tsv").open() as handle:
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
        "Utah's U.S. House general elections of 1992 through 1998 by county from the Lieutenant Governor's official results",
        PUBLISHER,
        TITLE,
        LANDING,
        "UT",
        "49",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url) for url in FILES.values()],
        lambda key: METHOD,
    )
