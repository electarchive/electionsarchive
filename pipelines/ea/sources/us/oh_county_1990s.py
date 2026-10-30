import re
from datetime import UTC, datetime
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.oh_county_1990s"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-391" / "oh"
PAGE = "https://www.ohiosos.gov/elections/election-results-and-data/"
FILES = {
    "1996_presidential_by_county_november_5.xlsx": ("1996-11-05", "president"),
    "1998_governor_lt_governor_november_3.xlsx": ("1998-11-03", "governor"),
    "1998_us_senate_november_3.xlsx": ("1998-11-03", "senate"),
}
PUBLISHER = "Ohio Secretary of State"
TITLE = "Ohio past election results, 1996 and 1998 general elections by county"
METHOD = (
    "County totals are from the Secretary of State's past election results workbook for the contest, "
    "each candidate's county sum equal to the printed state total."
)


def url(name: str) -> str:
    return f"{PAGE}#{name}"


def number(text: str | None) -> int | None:
    digits = re.sub(r"[,.\s]", "", text or "")
    return int(digits) if digits.isdigit() else None


def fetch() -> None:
    for name in FILES:
        raw.store(
            SOURCE,
            url(name),
            (INBOX / name).read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
    rows = []
    for name, (day, office) in FILES.items():
        book = fastexcel.read_excel(str(raw.cached(SOURCE, url(name)).path))
        table = book.load_sheet_by_name(book.sheet_names[0], header_row=None).to_polars().rows()
        counties = {}
        totals: list[int | None] = []
        for row in table:
            label = re.sub(r"[^a-z]", "", str(row[0] or "").lower())
            if label in codes:
                counties[label] = [number(cell) for cell in row[1:]]
            elif label in {"total", "totals"}:
                totals = [number(cell) for cell in row[1:]]
        if len(counties) != 88:
            raise ValueError(f"{name}: {len(counties)} counties")
        for column, printed in enumerate(totals):
            if printed is None:
                continue
            votes = [(label, cells[column]) for label, cells in counties.items() if cells[column] is not None]
            if sum(count for _, count in votes) != printed:
                raise ValueError(f"{name} column {column}: counties sum {sum(c for _, c in votes)}, printed {printed}")
            for label, count in votes:
                rows.append((day, office, codes[label], str(column), count))
        print(name, "ties", len([t for t in totals if t is not None]), "columns")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Ohio's 1996 president and 1998 governor and Senate general elections by county from the Secretary of State's results workbooks",
        PUBLISHER,
        TITLE,
        PAGE,
        "OH",
        "39",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url(name)) for name in FILES],
        lambda key: METHOD,
        statewide=True,
    )
