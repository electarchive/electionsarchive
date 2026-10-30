from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA

SOURCE = "us.az_canvass_1994"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = "1994-11-08"
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
PUBLISHER = "Arizona Secretary of State"
TITLE = "State of Arizona Official Canvass, General Election, November 8, 1994"
URL = "https://web.archive.org/web/2013id_/http://azsos.gov/election/1994/General/Canvass1994GE.pdf"
LANDING = "https://azsos.gov/election/1994/General/Canvass1994GE.pdf"
COLUMNS = {
    ("governor", "Basha"): (
        [12409, 10647, 17110, 7770, 3101, 1358, 1773, 272298, 12656, 11949, 104669, 16338, 3978, 17584, 7062],
        500702,
    ),
    ("governor", "Symington"): (
        [4229, 15203, 13971, 6978, 4570, 1522, 1850, 349473, 19315, 9780, 107287, 13755, 2856, 29213, 13490],
        593492,
    ),
    ("governor", "Others"): ([425, 763, 1221, 521, 143, 60, 117, 21764, 930, 526, 5644, 801, 112, 1892, 494], 35413),
    ("senate", "Coppersmith"): (
        [10819, 10352, 14962, 6231, 2771, 1258, 1339, 228021, 11314, 10107, 105127, 13815, 3712, 15216, 7466],
        442510,
    ),
    ("senate", "Kyl"): (
        [5321, 14216, 14571, 7748, 4607, 1473, 2061, 362348, 19005, 10891, 100970, 14538, 2601, 28374, 12275],
        600999,
    ),
    ("senate", "Grainger"): (
        [710, 1746, 2355, 1104, 341, 124, 257, 45998, 2151, 1023, 11628, 2145, 317, 4552, 1042],
        75493,
    ),
    ("senate", "Pope"): ([2, 0, 4, 1, 0, 0, 0, 35, 4, 2, 3, 5, 0, 2, 0], 58),
}
METHOD = (
    "County totals are keyed from the page images of the Secretary of State's official canvass (pages 1 and 2, the "
    "copy the Wayback Machine holds), and each candidate's county sum equals the state total the canvass prints. "
    "For governor, John Buttrick and the seven write-in candidates are pooled as other votes."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


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
        "Arizona's 1994 governor and U.S. Senate general elections by county from the Secretary of State's official canvass",
        PUBLISHER,
        TITLE,
        LANDING,
        "AZ",
        "04",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
