from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.nj_fire_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox"
BURLINGTON = "https://www.co.burlington.nj.us/#sample-ballots-2026-general"
BOROUGH = "https://www.mercercounty.org/#sample-ballot-hopewell-borough-2026-general"
TOWNSHIP = "https://www.mercercounty.org/#sample-ballot-hopewell-township-2026-general"
MANTUA = "https://www.gloucestercountynj.gov/DocumentCenter/View/19674/Mantua-Township-District-10-UOCAVA-GENERAL-26"
WASHINGTON = (
    "https://www.gloucestercountynj.gov/DocumentCenter/View/19568/Washington-Township-District-1-UOCAVA-GENERAL-26"
)
WESTVILLE = (
    "https://www.gloucestercountynj.gov/DocumentCenter/View/19513/Westville-Borough-District-1-UOCAVA-GENERAL-26"
)
SOMERSET = "https://www.somersetcountynj.gov/government/elected-officials/county-clerk/election-division"
SOURCES = {
    BURLINGTON: (
        "Burlington County Clerk",
        "Official General Election Sample Ballots, November 3, 2026",
        "manual/MF-438/Burlington Samples ALL Gen26.pdf",
    ),
    BOROUGH: (
        "Mercer County Clerk",
        "Official General Election Sample Ballot, Hopewell Borough, November 3, 2026",
        "manual/MF-440/F07 Hopewell Borough General 2026.pdf",
    ),
    TOWNSHIP: (
        "Mercer County Clerk",
        "Official General Election Sample Ballot, Hopewell Township, November 3, 2026",
        "manual/MF-440/F08 Hopewell Township General 2026.pdf",
    ),
    MANTUA: (
        "Gloucester County Clerk",
        "Official General Election Ballot, Township of Mantua District 10, November 3, 2026",
        "y26/nj/gloucester/Mantua-Township-District-10-UOCAVA-GENERAL-26.pdf",
    ),
    WASHINGTON: (
        "Gloucester County Clerk",
        "Official General Election Ballot, Township of Washington District 1, November 3, 2026",
        "y26/nj/gloucester/Washington-Township-District-1-UOCAVA-GENERAL-26.pdf",
    ),
    WESTVILLE: (
        "Gloucester County Clerk",
        "Official General Election Ballot, Borough of Westville District 1, November 3, 2026",
        "y26/nj/gloucester/Westville-Borough-District-1-UOCAVA-GENERAL-26.pdf",
    ),
    SOMERSET: (
        "Somerset County Clerk",
        "2026 General Election School Board and Fire Commissioner Candidates",
        "y26/nj/somerset_school.xlsx",
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
    ("Mantua Township Fire District", "Gloucester", False, 2, MANTUA, ["Nicholas A. Vacarino", "Michael S. Riggs"]),
    (
        "Washington Township Fire District",
        "Gloucester",
        False,
        2,
        WASHINGTON,
        ["John J. Reisenwitz III", "Frank Stella"],
    ),
    ("Westville Borough Fire District", "Gloucester", False, 2, WESTVILLE, ["William Marsden", "Vincent DiCicco"]),
    ("Westville Borough Fire District", "Gloucester", True, 1, WESTVILLE, ["Paul Mailley"]),
    ("Hillsborough Fire District", "Somerset", False, 1, SOMERSET, ["John Lazorchak"]),
)


def fetch() -> None:
    for url, (_, _, file) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = INBOX / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf"
                if path.suffix == ".pdf"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
        summary="Fire commissioner races on the November 3, 2026 ballot in Burlington, Gloucester, Mercer, and Somerset County fire districts, with candidates from the county clerks' official ballots and candidate lists",
        sources={url: (publisher, title) for url, (publisher, title, _) in SOURCES.items()},
        frame=pl.read_parquet(PARSED / "candidates.parquet"),
    )
