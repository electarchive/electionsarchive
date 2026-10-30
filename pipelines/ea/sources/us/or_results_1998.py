import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import openelections, usrep_county
from ea.store import DATA, connect

SOURCE = "us.or_results_1998"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://records.sos.state.or.us/ORSOSWebDrawer/Record/6920722/File/document"
PUBLISHER = "Oregon Secretary of State"
TITLE = "Official Results, November 3, 1998 General Election"
DAY = "1998-11-03"
STARTS = {"senate": "United States Senator", "governor": "Governor"}
WIDTHS = {"senate": 7, "governor": 8}
METHOD = (
    "County totals are from the Elections Division's official results for the November 3, 1998 general election "
    "(read from the PDF's text layer), and each column's county sum equals the state total the report prints."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["OR"]
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    rows = []
    for office, start in STARTS.items():
        begin = next(i for i, line in enumerate(lines) if line.strip() == start)
        width = WIDTHS[office]
        table: dict[str, list[int]] = {}
        printed: list[int] = []
        for line in lines[begin + 1 :]:
            match = re.match(r"\s*([A-Za-z][A-Za-z .]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$", line)
            if match is None:
                if line.strip().startswith("State Senator") or line.strip().startswith("State Representative"):
                    break
                continue
            numbers = [int(value.replace(",", "")) for value in match[2].split()]
            if len(numbers) != width:
                continue
            if match[1].strip().upper() == "TOTAL":
                printed = numbers
                break
            else:
                table[match[1].strip()] = numbers
        sums = [sum(cells[i] for cells in table.values()) for i in range(width)]
        if len(table) != 36 or sums != printed:
            raise ValueError(f"{office}: {len(table)} counties, {sums} against printed {printed}")
        for county, cells in table.items():
            code = fips[re.sub(r"[^a-z]", "", county.lower())]
            rows.extend((DAY, office, code, str(i), votes) for i, votes in enumerate(cells) if votes)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Oregon's 1998 U.S. Senate and governor general elections by county from the Elections Division's official results",
        PUBLISHER,
        TITLE,
        URL,
        "OR",
        "41",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
