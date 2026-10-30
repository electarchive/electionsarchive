import difflib
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.ne_usrep import ALIASES, LANDING, PUBLISHER, TITLE, TOKEN, URL, number
from ea.store import DATA, connect

SOURCE = "us.ne_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BLOCKS = [
    (1996, date(1996, 11, 5), "president", r"PRESIDENT OF THE UNITED STATES"),
    (1996, date(1996, 11, 5), "senate", r"[uU]\.[sS]\. SENATE"),
]
METHOD = (
    "County totals are from the Board of State Canvassers' official report (the Nebraska Library Commission's scan, "
    "read from its text layer). Misread cells were settled from the page images, and each candidate's county sum "
    "equals the state total printed in the report."
)
ROWS: dict[tuple[int, str, str], dict[int, int]] = {
    (1996, "president", "Burt"): {2: 21, 3: 3, 4: 497, 5: 31},
    (1996, "senate", "Butler"): {0: 1663, 1: 1962, 2: 58, 3: 32, 4: 0},
    (1996, "senate", "Dawson"): {0: 2856, 1: 4987, 2: 145, 3: 50, 4: 4},
    (1996, "senate", "Hall"): {0: 8987, 1: 10098, 2: 320, 3: 101, 4: 9},
    (1996, "senate", "Wayne"): {0: 1287, 1: 2273, 2: 48, 3: 30, 4: 3},
}


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = []
    for year, day, office, heading in BLOCKS:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE.replace("statewide", "usrep"), URL.format(year, "prim_gen")).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        start = next(i for i, line in enumerate(text) if i > 1000 and re.search(heading, line))
        end = next(i for i in range(start, len(text)) if "TOTAL" in text[i])
        block = text[start : end + 1]
        total = [number(token) for token in re.findall(TOKEN, block[-1].split("TOTAL", 1)[1])]
        width = len(total)
        table: dict[str, list[int | None]] = {}
        for line in block:
            line = line.split("TOTAL", 1)[0]
            pattern = r"([A-Za-z][A-Za-z .'~]*?)\s{2,}((?:%s\s+){0,%d}%s)(?=\s{2,}|\s*$)" % (TOKEN, width - 1, TOKEN)
            for match in re.finditer(pattern, line):
                key = ALIASES.get(re.sub(r"[^a-z]", "", match[1].lower()), re.sub(r"[^a-z]", "", match[1].lower()))
                close = difflib.get_close_matches(key, codes, 1, 0.7) if key else []
                if close:
                    table[close[0]] = [number(token) for token in re.findall(TOKEN, match[2])]
        for (fix_year, fix_office, county), cells in ROWS.items():
            if (fix_year, fix_office) == (year, office):
                row = table.setdefault(re.sub(r"[^a-z]", "", county.lower()), [None] * width)
                row.extend([None] * (width - len(row)))
                for column, value in cells.items():
                    row[column] = value
        print(year, office, "counties", len(table), "width", width)
        for column in range(width):
            got = sum(row[column] or 0 for row in table.values() if len(row) > column)
            if got != total[column]:
                print("  column", column, "sum", got, "printed", total[column])
                for county, row in table.items():
                    if len(row) != width or None in row:
                        print("    short", county, row)
        missing = sorted(set(codes) - set(table))
        if missing:
            print("  missing", missing)
        for county, row in table.items():
            for column, value in enumerate(row):
                if value is not None:
                    rows.append((str(day), office, codes[county], str(column), value))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Nebraska's president, U.S. Senate, and governor general elections of 1992 through 1998 by county from the Board of State Canvassers' reports",
        PUBLISHER,
        TITLE,
        LANDING,
        "NE",
        "31",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached("us.ne_usrep", URL.format(year, "prim_gen")) for year in sorted({block[0] for block in BLOCKS})],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
