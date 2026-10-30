import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import openelections, usrep_county
from ea.sources.us.sd_sos import PUBLISHER, table, url
from ea.store import DATA, connect

SOURCE = "us.sd_canvass_1998"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "1998/1998_general_election_official_canvass_statewide_candidates.aspx"
DAY = "1998-11-03"
TITLE = "1998 General Election Official Canvass Statewide Candidates"
METHOD = (
    "County totals are from the Secretary of State's 1998 general election official canvass page, and each "
    "candidate's county sum equals the state total the page prints."
)


def fetch() -> None:
    raw.fetch(SOURCE, url(PAGE), suffix=".html")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["SD"]
    fips["shannon"] = fips["oglalalakota"]
    rows = table(PAGE, SOURCE)
    header = next(i for i, cells in enumerate(rows) if any("Daschle" in cell for cell in cells))
    names = [re.sub(r"\s*-\s*[A-Z]$", "", cell).strip() for cell in rows[header] if cell and "Senator" not in cell]
    sums = [0] * len(names)
    frame = []
    printed: list[int] = []
    for cells in rows[header + 1 :]:
        if not cells or not cells[0]:
            continue
        if cells[0].upper().startswith("TOTAL"):
            printed = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", " ".join(cells[1:]))][
                -len(names) :
            ]
            break
        values = [int(value.replace(",", "")) for value in cells[1:] if value.strip()]
        if len(values) != len(names):
            raise ValueError(cells)
        county = fips[re.sub(r"[^a-z]", "", cells[0].lower())]
        for index, (name, votes) in enumerate(zip(names, values, strict=True)):
            sums[index] += votes
            frame.append((DAY, "senate", county, name, votes))
    if sums != printed:
        raise ValueError(f"{sums} against printed {printed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(frame, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "South Dakota's 1998 U.S. Senate general election by county from the Secretary of State's official canvass",
        PUBLISHER,
        TITLE,
        url(PAGE),
        "SD",
        "46",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url(PAGE))],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
