from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.nj_fire_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = DATA / "inbox" / "manual"
BURLINGTON = "https://www.co.burlington.nj.us/#sample-ballots-2026-general"
BOROUGH = "https://www.mercercounty.org/#sample-ballot-hopewell-borough-2026-general"
TOWNSHIP = "https://www.mercercounty.org/#sample-ballot-hopewell-township-2026-general"
SOURCES = {
    BURLINGTON: (
        "Burlington County Clerk",
        "Official General Election Sample Ballots, November 3, 2026",
        "MF-438/Burlington Samples ALL Gen26.pdf",
    ),
    BOROUGH: (
        "Mercer County Clerk",
        "Official General Election Sample Ballot, Hopewell Borough, November 3, 2026",
        "MF-440/F07 Hopewell Borough General 2026.pdf",
    ),
    TOWNSHIP: (
        "Mercer County Clerk",
        "Official General Election Sample Ballot, Hopewell Township, November 3, 2026",
        "MF-440/F08 Hopewell Township General 2026.pdf",
    ),
}
SEATS = (
    ("Beverly City Fire District", "Burlington", False, 2, BURLINGTON, ["James Lowden"]),
    ("Bordentown Township Fire District No. 1", "Burlington", False, 1, BURLINGTON, ["Timothy J. Kinsley"]),
    ("Bordentown Township Fire District No. 2", "Burlington", False, 2, BURLINGTON, ["Jim Cann", "Robert Bennett"]),
    ("Cinnaminson Township Fire District", "Burlington", False, 1, BURLINGTON, ["Robert Bond"]),
    ("Delanco Township Fire District", "Burlington", False, 2, BURLINGTON, ["Nicholas William Hubler", "Gary Stahl"]),
    ("Delran Fire District No. 1", "Burlington", False, 1, BURLINGTON, ["James C. Bauer"]),
    ("Delran Fire District No. 1", "Burlington", True, 1, BURLINGTON, ["Charles Francis"]),
    ("Eastampton Township Fire District", "Burlington", False, 1, BURLINGTON, ["Keith Black"]),
    ("Edgewater Park Township Fire District", "Burlington", False, 2, BURLINGTON, ["Brooke Allen", "Shawn Bolton"]),
    (
        "Evesham Township Fire District",
        "Burlington",
        False,
        1,
        BURLINGTON,
        ["Michael E. Powers", "Clifford C. Rickards Jr."],
    ),
    (
        "Florence Township Fire District",
        "Burlington",
        False,
        2,
        BURLINGTON,
        ["Scott W. Daily", "Philip R. Wilgus Jr.", "Andrew Joseph Popso"],
    ),
    (
        "Mount Holly Fire District",
        "Burlington",
        False,
        2,
        BURLINGTON,
        ["George I. Gaskill", "Rosemarie M. Logue", "J. Gary Brotz", "Tom Rossell"],
    ),
    (
        "Mount Laurel Township Fire District",
        "Burlington",
        False,
        2,
        BURLINGTON,
        ["Robert R. Shestack", "Phil Roura"],
    ),
    ("Mount Laurel Township Fire District", "Burlington", True, 1, BURLINGTON, ["Joseph H. Appleton"]),
    (
        "Riverside Township Fire District",
        "Burlington",
        False,
        2,
        BURLINGTON,
        ["Robert T. Miller", "Richard G. Horton"],
    ),
    (
        "Hopewell Borough Fire District No. 1",
        "Mercer",
        False,
        2,
        BOROUGH,
        ["Gregory Davies", "William H. Springer Jr."],
    ),
    (
        "Hopewell Township Fire District No. 1",
        "Mercer",
        False,
        2,
        TOWNSHIP,
        ["Thomas Pearse-Drance", "Linda Abey", "Kevin R. Baranowski"],
    ),
)


def fetch() -> None:
    for url, (_, _, file) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    rows = [
        {
            "district": district,
            "county": county,
            "seat": "",
            "remainder": remainder,
            "role": "Fire Commissioner",
            "function": "fire-protection",
            "vote": vote,
            "name": name,
            "url": url,
        }
        for district, county, remainder, vote, url, names in SEATS
        for name in names
    ]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="NJ",
        summary="Fire commissioner races on the November 3, 2026 ballot in Burlington and Mercer County fire districts, with candidates from the county clerks' official sample ballots",
        sources={url: (publisher, title) for url, (publisher, title, _) in SOURCES.items()},
        frame=pl.read_parquet(PARSED / "candidates.parquet"),
    )
