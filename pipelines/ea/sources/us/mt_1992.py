import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.mt_1992"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://sosmt.gov/Portals/142/Elections/archives/1990s/1992/1992gen.pdf"
DAY = "1992-11-03"
COLUMNS = {
    "president": [(0, 4), (0, 5), (0, 6), (0, 7), (1, 0)],
    "governor": [(1, 4), (1, 5)],
}
PUBLISHER = "Montana Secretary of State"
TITLE = "Official 1992 General Election Canvass"
METHOD = (
    "County totals are from the Secretary of State's Official 1992 General Election Canvass, read from its text layer, "
    "each candidate's county sum equal to the printed state total."
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
    blocks: list[list[tuple[str, list[int]]]] = [[]]
    for line in lines[:140]:
        match = re.match(r"^\s*([A-Za-z&. ]+?)\s{2,}(\d[\d,\s]*)$", line)
        if match is None:
            continue
        name = match[1].strip()
        cells = [int(cell.replace(",", "")) for cell in match[2].split()]
        if name == "Total":
            blocks.append([])
            blocks[-2].append((name, cells))
        else:
            blocks[-1].append((name, cells))
    blocks = blocks[:2]
    for block in blocks:
        names = [re.sub(r"[^a-z]", "", name.lower().replace("&", "and")) for name, _ in block[:-1]]
        if len(names) != 56 or set(names) != set(codes):
            raise ValueError(f"{len(names)} counties; unmatched {set(names) ^ set(codes)}")
    rows = []
    for office, spec in COLUMNS.items():
        for column, (group, index) in enumerate(spec):
            block = blocks[group]
            counties = [(name, cells[index]) for name, cells in block[:-1]]
            if sum(votes for _, votes in counties) != block[-1][1][index]:
                raise ValueError(f"{office} column {column}: counties sum {sum(v for _, v in counties)}")
            for name, votes in counties:
                county = codes[re.sub(r"[^a-z]", "", name.lower().replace("&", "and"))]
                rows.append((DAY, office, county, str(column), votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("district", "column").agg(pl.col("votes").sum()).sort("district", "column"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Montana's 1992 president and governor general elections by county from the Secretary of State's canvass",
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
