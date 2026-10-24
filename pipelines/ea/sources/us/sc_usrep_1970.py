import csv
import difflib
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.sc_usrep_1970"
BOOK_SOURCE = "us.sc_report"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "South Carolina State Election Commission"
TITLE = "1969-1973 Election Report"
LANDING = "https://scvotes.gov/election-results/"
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_1969-1973.pdf"
DAY = date(1970, 11, 3)
METHOD = (
    "County totals are keyed from page 306 (printed page 299) of the State Election Commission's 1969-1973 election "
    "report, read from the page image. Each candidate's county sum equals the district total printed in the report."
)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    rows = []
    with (Path(__file__).parent / "sc_usrep_1970.tsv").open() as handle:
        for _, district, candidate, cells in csv.reader(handle, delimiter="\t"):
            for cell in cells.split(", "):
                name, votes = cell.rsplit(" ", 1)
                key = re.sub(r"[^a-z]", "", name.lower())
                key = key if key in counties else difflib.get_close_matches(key, counties, 1, 0.7)[0]
                rows.append((str(DAY), int(district), counties[key], candidate, int(votes)))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("district").agg(pl.col("county").n_unique(), pl.col("votes").sum()).sort("district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "South Carolina's U.S. House general election of 1970 by county from the State Election Commission's report",
        PUBLISHER,
        TITLE,
        LANDING,
        "SC",
        "45",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(BOOK_SOURCE, URL)],
        lambda key: METHOD,
    )
