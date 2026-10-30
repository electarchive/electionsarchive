import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.nv_1998"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://web.archive.org/web/20260516033022if_/https://www.nvsos.gov/sos/home/showdocument?id=192"
DAY = "1998-11-03"
COUNTIES = [
    "carsoncity", "churchill", "clark", "douglas", "elko", "esmeralda", "eureka", "humboldt", "lander", "lincoln",
    "lyon", "mineral", "nye", "pershing", "storey", "washoe", "whitepine",
]
BLOCKS = {"senate": ("United States Senator", "Congressional District One"), "governor": ("Governor", "Lieutenant Governor")}
PUBLISHER = "Nevada Secretary of State"
TITLE = "1998 Official General Election Returns"
METHOD = (
    "County totals are from the Secretary of State's 1998 Official General Election Returns (the copy the Internet "
    "Archive holds, read from its text layer), each candidate's county sum equal to the printed state total."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '32%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    rows = []
    for office, (start, stop) in BLOCKS.items():
        begin = next(i for i, line in enumerate(lines) if line.strip() == start)
        end = next(i for i in range(begin + 1, len(lines)) if lines[i].strip().startswith(stop))
        column = 0
        for line in lines[begin:end]:
            numbers = re.findall(r"(?<![\w(])\d[\d,]*(?![\w)])", line)
            if len(numbers) != len(COUNTIES) + 1:
                continue
            values = [int(n.replace(",", "")) for n in numbers]
            if sum(values[:-1]) != values[-1]:
                raise ValueError(f"{office} column {column}: counties sum {sum(values[:-1])}, printed {values[-1]}")
            for county, value in zip(COUNTIES, values, strict=False):
                rows.append((DAY, office, codes[county], str(column), value))
            column += 1
        print(office, column, "candidates")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Nevada's 1998 governor and U.S. Senate general elections by county from the Secretary of State's official returns",
        PUBLISHER,
        TITLE,
        "https://www.nvsos.gov/sos/elections/election-information/election-results",
        "NV",
        "32",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        statewide=True,
    )
