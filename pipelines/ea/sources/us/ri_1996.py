import re
from datetime import UTC, datetime
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA

SOURCE = "us.ri_1996"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-391" / "ri" / "1996results.zip"
URL = "https://elections.ri.gov/elections/results/1996/Book96_Pt2.xls"
DAY = "1996-11-05"
SHEETS = {"Pres_byCTY": "president", "USSen_byCTY": "senate"}
COUNTIES = {"bristol": "44001", "kent": "44003", "newport": "44005", "providence": "44007", "washington": "44009"}
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Rhode Island General Election, November 5, 1996: Results by County"
METHOD = (
    "County totals are from the Board of Elections' 1996 results workbook, "
    "each candidate's county sum equal to the printed state total."
)


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".zip",
        retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
        media_type="application/zip",
    )


def parse() -> None:
    import io
    import zipfile

    archive = zipfile.ZipFile(raw.cached(SOURCE, URL).path)
    PARSED.mkdir(parents=True, exist_ok=True)
    book_path = PARSED / "Book96_Pt2.xls"
    book_path.write_bytes(archive.read("Book96_Pt2.xls"))
    book = fastexcel.read_excel(str(book_path))
    rows = []
    for sheet, office in SHEETS.items():
        table = book.load_sheet_by_name(sheet, header_row=None).to_polars().rows()
        header = next(row for row in table if str(row[0] or "").strip() == "CITY / TOWN")
        columns = [index for index, cell in enumerate(header) if index > 0 and cell]
        county = ""
        counties: dict[str, list[int]] = {}
        printed: list[int] = []
        for row in table:
            label = str(row[0] or "").strip().lower()
            match = re.fullmatch(r"(\w+) county", label)
            if match is not None and match[1] in COUNTIES:
                county = match[1]
            elif label == "county total":
                counties[county] = [int(str(row[index]).replace(",", "")) for index in columns]
            elif label == "state totals":
                printed = [int(str(row[index]).replace(",", "")) for index in columns]
        if set(counties) != set(COUNTIES):
            raise ValueError(f"{sheet}: counties {sorted(counties)}")
        for position, total in enumerate(printed):
            if sum(cells[position] for cells in counties.values()) != total:
                raise ValueError(f"{sheet} column {position}: counties do not sum to {total}")
        for name, cells in counties.items():
            for position, votes in enumerate(cells):
                rows.append((DAY, office, COUNTIES[name], f"{office}{position}", votes))
        print(sheet, "ties", len(printed), "columns")
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Rhode Island's 1996 presidential and Senate general elections by county from the Board of Elections' results workbook",
        PUBLISHER,
        TITLE,
        URL,
        "RI",
        "44",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        statewide=True,
    )
