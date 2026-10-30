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
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
BOOKS = {1992: "1992-1993", 1998: "1997-1998"}
DAYS = {1992: date(1992, 11, 3), 1998: date(1998, 11, 3)}
BLOCKS = [
    (1992, "president", 5128, 6),
    (1992, "senate", 5218, 5),
    (1998, "governor", 1713, 5),
    (1998, "senate", 1998, 4),
]
STACKED: dict[tuple[int, str], dict[int, list[int]]] = {
    (1992, "president"): {21: [18, 25, 6870, 25, 1840, 7494], 22: [143, 99, 65066, 249, 13699, 34651]},
}
METHOD = (
    "County totals are from the State Election Commission's Election Reports (read from the text layer, columns "
    "assigned by position). A cell the scan garbled is the printed state total less the other counties, and each "
    "candidate's county sum equals the state total printed in the report."
)


def page(year: int) -> list[str]:
    path = raw.cached(SOURCE.replace("statewide", "report"), URL.format(BOOKS[year])).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout.split("\n")


def tokens(line: str) -> list[tuple[int, int | None]]:
    line = re.sub(r"(\d) ,(\d)", r"\1,\2", line.replace("'", ""))
    return [
        (m.end(), int(m[0].lstrip(".").replace(",", "")) if re.fullmatch(r"\.?\d[\d,]*", m[0]) else None)
        for m in re.finditer(r"\S+", line)
        if re.search(r"\d", m[0])
    ]


def parse() -> None:
    order = sorted(re.sub(r"[^A-Z]", "", c.upper()) for c in COUNTIES)
    with connect() as conn:
        codes = {
            re.sub(r"[^A-Z]", "", name.upper()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    rows = []
    for year, office, start, width in BLOCKS:
        lines = page(year)
        data: list[list[tuple[int, int | None]]] = []
        total: list[tuple[int, int | None]] = []
        begin = next(i for i in range(start - 12, start + 15) if "ABBEVILLE" in lines[i])
        for line in lines[begin : begin + 90]:
            if re.search(r"STATE TOTAL|TOTALS", line):
                total = tokens(line)[:width]
                break
            found = tokens(line)
            if len(found) > 1 or (found and (year, office) not in STACKED):
                data.append(found)
        edges = [end for end, _ in total]
        table: dict[str, list[int | None]] = {}
        fixed = STACKED.get((year, office), {})
        names = [county for index, county in enumerate(order) if index not in fixed]
        for county, found in zip(names, data, strict=True):
            cells: list[int | None] = [0] * width
            for end, value in found:
                column = min(range(width), key=lambda i: abs(edges[i] - end))
                cells[column] = value
            table[county] = cells
        for index, cells in STACKED.get((year, office), {}).items():
            table[order[index]] = list(cells)
        printed = [value or 0 for _, value in total]
        for column in range(width):
            missing = [county for county, cells in table.items() if cells[column] is None]
            if len(missing) == 1:
                table[missing[0]][column] = printed[column] - sum(c[column] or 0 for c in table.values())
        print(year, office, "rows", len(data), "counties", len(table))
        for column in range(width):
            got = sum(cells[column] or 0 for cells in table.values())
            if got != printed[column]:
                print("  column", column, "sum", got, "printed", printed[column])
        for county, cells in table.items():
            for column, value in enumerate(cells):
                if value:
                    rows.append((str(DAYS[year]), office, codes[county], str(column), value, year))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes", "year"], orient="row").drop("year").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "South Carolina's president, governor, and U.S. Senate general elections of 1992 and 1998 by county from the State Election Commission's reports",
        PUBLISHER,
        TITLE.format(year="Election Report"),
        LANDING,
        "SC",
        "45",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached("us.sc_report", URL.format(book)) for book in BOOKS.values()],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
