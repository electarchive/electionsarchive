import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import openelections, usrep_county
from ea.store import DATA, connect

SOURCE = "us.wv_returns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "West Virginia Secretary of State"
TITLE = "General Election Official Election Returns, 1992 and 1996"
BOOKS = {
    "1992-11-03": "https://ia600805.us.archive.org/21/items/1992GeneralElectionOfficialElectionReturns/1992%20General%20Election%20-%20Official%20Election%20Returns.pdf",
    "1996-11-05": "https://ia600800.us.archive.org/12/items/1996GeneralElectionOfficialElectionReturns/1996%20General%20Election%20-%20Official%20Election%20Returns.pdf",
}
PATCHES = {("1992-11-03", "harrison"): {1: 8195}, ("1996-11-05", "wayne"): {0: 7601}}
METHOD = (
    "County totals are from the Secretary of State's official election returns for the general election (the "
    "Internet Archive's copy, read from its text layer), and each candidate's county sum equals the state total the "
    "returns print."
)
HARRISON = (
    " The 1992 table prints Harrison County's Benedict vote as 6,195, which leaves that column 2,000 short of the "
    "printed state total, so the county is entered as 8,195, the figure that makes the column tie."
)
WAYNE = (
    " The 1996 table prints Wayne County's Pritt vote as 4,601, which leaves that column 3,000 short of the "
    "printed state total, so the county is entered as 7,601, the figure that makes the column tie."
)


def fetch() -> None:
    for url in BOOKS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["WV"]
    rows = []
    for day, url in BOOKS.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\n")
        start = next(i for i, line in enumerate(lines) if line.strip() == "GOVERNOR")
        table: dict[str, list[int]] = {}
        printed: list[int] = []
        for line in lines[start + 1 :]:
            match = re.match(r"\s*([A-Za-z][A-Za-z .]*?)\s+(\d+(?:\s+\d+){2})\s*$", line)
            if match is None:
                continue
            numbers = [int(value) for value in match[2].split()]
            if match[1].strip().upper() == "TOTAL":
                printed = numbers
                break
            table[match[1].strip()] = numbers
        for county, patch in PATCHES.items():
            if county[0] == day:
                for column, value in patch.items():
                    table[next(name for name in table if name.lower() == county[1])][column] = value
        sums = [sum(cells[i] for cells in table.values()) for i in range(3)]
        if len(table) != 55 or sums != printed:
            raise ValueError(f"{day}: {len(table)} counties, {sums} against printed {printed}")
        for county, cells in table.items():
            code = fips[re.sub(r"[^a-z]", "", county.lower())]
            rows.extend((day, "governor", code, str(i), votes) for i, votes in enumerate(cells) if votes)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "West Virginia's 1992 and 1996 governor general elections by county from the Secretary of State's official returns",
        PUBLISHER,
        TITLE,
        "https://archive.org/details/1996GeneralElectionOfficialElectionReturns",
        "WV",
        "54",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url) for url in BOOKS.values()],
        lambda key: METHOD + (HARRISON if str(key).startswith("1992") else WAYNE),
        official=True,
        statewide=True,
    )
