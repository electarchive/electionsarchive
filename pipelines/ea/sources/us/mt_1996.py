import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.mt_1996"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://web.archive.org/web/20130528184459/http://sos.mt.gov/Elections/Archives/1990s/1996/96GEN/1996gen.pdf"
DAY = "1996-11-05"
COLUMNS = {
    "president": [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)],
    "senate": [(0, 5), (1, 0), (1, 1), (1, 2)],
    "governor": [(2, 1), (2, 2), (2, 3)],
}
PUBLISHER = "Montana Secretary of State"
TITLE = "1996 Statewide General Canvass"
METHOD = (
    "County totals are from the Secretary of State's 1996 Statewide General Canvass (the copy the Internet Archive "
    "holds, read from its text layer), each candidate's county sum equal to the printed state total."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower().replace("&", "and")): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    names = [
        re.sub(r"[^a-z]", "", m[1].lower().replace("&", "and"))
        for line in lines[:75]
        if (m := re.match(r"^\s*([A-Za-z&. ]+?)\s+\d+\s+[\d,]+\s", line)) and m[1].strip() != "TOTAL"
    ]
    if len(names) != 56 or set(names) != set(codes):
        raise ValueError(f"{len(names)} counties; unmatched {set(names) ^ set(codes)}")
    numbered = [line.split() for line in lines if line.strip() and re.fullmatch(r"[\d,\s]+", line)]
    groups = [numbered[i : i + 57] for i in range(0, len(numbered) - 56, 57)]
    rows = []
    for office, spec in COLUMNS.items():
        for column, (group, index) in enumerate(spec):
            table = [[int(cell.replace(",", "")) for cell in row] for row in groups[group]]
            total, counties = table[0][index], [row[index] for row in table[1:]]
            if sum(counties) != total:
                raise ValueError(f"{office} column {column}: counties sum {sum(counties)}, printed {total}")
            for county, value in zip(names, counties, strict=True):
                rows.append((DAY, office, codes[county], str(column), value))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("district", "column").agg(pl.col("votes").sum()).sort("district", "column"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Montana's 1996 president, U.S. Senate, and governor general elections by county from the Secretary of State's canvass",
        PUBLISHER,
        TITLE,
        "https://sosmt.gov/elections/",
        "MT",
        "30",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        statewide=True,
    )
