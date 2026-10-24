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
TITLE = "Election Report"
LANDING = "https://scvotes.gov/election-results/"
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_{}.pdf"
BOOKS = {1970: "1969-1973", 1978: "1978-1979"}
DAYS = {1970: date(1970, 11, 3), 1978: date(1978, 11, 7)}
METHOD = (
    "County totals are keyed from the district tables of the State Election Commission's election report (page 306 of "
    "the 1969-1973 book; pages 59 to 61 of the 1978-1979 book), read from the page images. Each candidate's county sum "
    "equals the district total printed in the report."
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
        for year, district, candidate, cells in csv.reader(handle, delimiter="\t"):
            for cell in cells.split(", "):
                name, votes = cell.rsplit(" ", 1)
                key = re.sub(r"[^a-z]", "", name.lower())
                key = key if key in counties else difflib.get_close_matches(key, counties, 1, 0.7)[0]
                rows.append((str(DAYS[int(year)]), int(district), counties[key], candidate, int(votes)))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district").agg(pl.col("county").n_unique(), pl.col("votes").sum()).sort("day", "district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "South Carolina's U.S. House general elections of 1970 and 1978 by county from the State Election Commission's report",
        PUBLISHER,
        TITLE,
        LANDING,
        "SC",
        "45",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(BOOK_SOURCE, URL.format(book)) for book in BOOKS.values()],
        lambda key: METHOD,
        official=True,
    )
