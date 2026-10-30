from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.az_house_1992 import FILE, PUBLISHER, TITLE, URL
from ea.store import DATA

SOURCE = "us.az_canvass_1992"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = "1992-11-03"
COUNTIES = [
    "04001",
    "04003",
    "04005",
    "04007",
    "04009",
    "04011",
    "04012",
    "04013",
    "04015",
    "04017",
    "04019",
    "04021",
    "04023",
    "04025",
    "04027",
]
COLUMNS = {
    ("president", "Clinton"): (
        [11218, 12701, 18888, 7571, 3391, 1695, 1808, 285457, 13255, 10882, 128569, 15468, 3512, 18268, 10367],
        543050,
    ),
    ("president", "Bush"): (
        [4588, 12202, 13769, 5781, 4169, 1451, 1599, 360049, 13684, 7994, 97036, 11669, 3024, 23419, 11652],
        572086,
    ),
    ("president", "Marrou"): ([94, 149, 273, 98, 29, 4, 31, 3742, 280, 139, 1268, 136, 46, 322, 148], 6759),
    ("president", "Fulani"): ([27, 22, 50, 14, 5, 7, 3, 555, 36, 30, 108, 20, 5, 25, 16], 923),
    ("president", "Perot"): (
        [1979, 7857, 9363, 4694, 1860, 794, 1488, 221475, 12706, 4787, 53925, 9231, 1447, 16409, 5726],
        353741,
    ),
    ("president", "Hagelin"): ([32, 103, 115, 71, 27, 5, 26, 1018, 257, 71, 284, 45, 29, 118, 66], 2267),
    ("president", "Gritz"): ([320, 116, 160, 249, 218, 6, 6, 4534, 398, 735, 291, 170, 16, 852, 70], 8141),
    ("president", "LaRouche"): ([0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 3, 0, 1, 2, 0], 8),
    ("senate", "Sargent"): (
        [8543, 10304, 14414, 6011, 2659, 1375, 1465, 234584, 12513, 7949, 95789, 12011, 2614, 17073, 9017],
        436321,
    ),
    ("senate", "McCain"): (
        [7564, 19166, 23766, 9007, 4766, 2086, 2641, 443607, 21190, 12601, 153015, 19095, 4591, 32053, 16247],
        771395,
    ),
    ("senate", "Delamare"): ([324, 399, 930, 260, 111, 50, 49, 13651, 600, 338, 3779, 417, 136, 1225, 344], 22613),
    ("senate", "Finkelstein"): ([166, 85, 210, 129, 31, 10, 14, 3998, 200, 104, 807, 186, 26, 241, 128], 6335),
    ("senate", "Mecham"): (
        [1469, 2903, 2477, 2939, 2160, 436, 740, 94808, 5266, 3505, 13802, 4795, 402, 7933, 1726],
        145361,
    ),
    ("senate", "Winn"): ([15, 0, 0, 0, 0, 0, 1, 1, 2, 1, 2, 1, 1, 0, 2], 26),
}
METHOD = (
    "County totals are keyed from the page images of the Secretary of State's official canvass (pages 1 to 3, the "
    "Arizona Memory Project's scan), and each candidate's county sum equals the state total the canvass prints."
)


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".pdf",
        retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
        media_type="application/pdf",
    )


def parse() -> None:
    rows = []
    for (office, column), (cells, printed) in COLUMNS.items():
        if sum(cells) != printed:
            raise ValueError(f"{office} {column}: {sum(cells)} against printed {printed}")
        rows.extend(
            (DAY, office, county, column, votes) for county, votes in zip(COUNTIES, cells, strict=True) if votes
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Arizona's 1992 president and U.S. Senate general elections by county from the Secretary of State's official canvass",
        PUBLISHER,
        TITLE,
        "https://azmemory.azlibrary.gov/nodes/view/102689",
        "AZ",
        "04",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
