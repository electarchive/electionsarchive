import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.sc_report import COUNTIES, LANDING, PUBLISHER, TITLE, URL
from ea.store import DATA, connect

SOURCE = "us.sc_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BOOK = "1992-1993"
DAY = date(1992, 11, 3)
BLOCKS = [("president", 5128, 6), ("senate", 5218, 5)]
ROWS: dict[tuple[str, str], list[int | None]] = {
    ("president", "GEORGETOWN"): [18, 25, 6870, 25, 1840, 7494],
    ("president", "GREENVILLE"): [143, 99, 65066, 249, 13699, 34651],
}
METHOD = (
    "County totals are from the State Election Commission's 1992-1993 Election Report (read from its text layer). "
    "A cell the scan garbled is the printed state total less the other counties, and each other candidate's county "
    "sum equals the state total printed in the report."
)


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE.replace("statewide", "report"), URL.format(BOOK)).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    with connect() as conn:
        codes = {
            re.sub(r"[^A-Z]", "", name.upper()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    codes["CLAREDON"] = codes["CLARENDON"]
    rows = []
    for office, start, width in BLOCKS:
        table: dict[str, list[int | None]] = {}
        total: list[int] = []
        for line in text[start : start + 80]:
            match = re.match(r"\s*\.?~?,?\s*([A-Z]+)\s{2,}(.*)$", line)
            if match is None or match[1] not in {*(re.sub(r"[^A-Z]", "", c.upper()) for c in COUNTIES), "CLAREDON", "TOTALS"}:
                continue
            tokens = [t for t in match[2].split() if re.search(r"[A-Za-z0-9]", t)]
            cells = [int(t.lstrip(".").replace(",", "")) if re.fullmatch(r"\.?\d[\d,]*", t) else None for t in tokens]
            if match[1] == "TOTALS":
                total = [c or 0 for c in cells]
            elif len(cells) == width:
                table[match[1]] = cells
        for (fix_office, county), cells in ROWS.items():
            if fix_office == office:
                table[county] = cells
        for column in range(width):
            missing = [county for county, cells in table.items() if cells[column] is None]
            if len(missing) == 1:
                table[missing[0]][column] = total[column] - sum(c[column] or 0 for c in table.values())
        short = sorted(set(codes) - set(table) - {"CLARENDON"})
        print(office, len(table), "missing", short)
        for column in range(width):
            got = sum(cells[column] or 0 for cells in table.values())
            if got != total[column]:
                print("  column", column, "sum", got, "printed", total[column])
        for county, cells in table.items():
            for column, value in enumerate(cells):
                rows.append((str(DAY), office, codes[county], str(column), value))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "South Carolina's 1992 president and U.S. Senate general elections by county from the State Election Commission's report",
        PUBLISHER,
        TITLE.format(year="1992-1993"),
        LANDING,
        "SC",
        "45",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached("us.sc_report", URL.format(BOOK))],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
