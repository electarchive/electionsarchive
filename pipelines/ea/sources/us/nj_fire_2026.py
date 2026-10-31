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
PENNINGTON = "https://www.mercercounty.org/#sample-ballot-pennington-borough-2026-general"
MANTUA = "https://www.gloucestercountynj.gov/DocumentCenter/View/19674/Mantua-Township-District-10-UOCAVA-GENERAL-26"
WASHINGTON = (
    "https://www.gloucestercountynj.gov/DocumentCenter/View/19568/Washington-Township-District-1-UOCAVA-GENERAL-26"
)
WESTVILLE = (
    "https://www.gloucestercountynj.gov/DocumentCenter/View/19513/Westville-Borough-District-1-UOCAVA-GENERAL-26"
)
SOMERSET = "https://www.somersetcountynj.gov/government/elected-officials/county-clerk/election-division"
HUNTERDON = "https://www.co.hunterdon.nj.us/clerk/#sample-ballot-%s-2026-general"
WARREN = "https://www.warrencountynj.gov/#sample-ballot-washington-township-2026-general"
FRANKLIN = HUNTERDON % "franklin-township"
EAST_AMWELL = HUNTERDON % "east-amwell-township"
LAMBERTVILLE = HUNTERDON % "lambertville-city"
WALL1 = "https://www.monmouthcountyclerk.gov/#sample-ballot-wall1-2026-general"
WALL3 = "https://www.monmouthcountyclerk.gov/#sample-ballot-wall3-2026-general"
TINTON1 = "https://www.monmouthcountyclerk.gov/#sample-ballot-tinton1-2026-general"
TINTON2 = "https://www.monmouthcountyclerk.gov/#sample-ballot-tinton2-2026-general"
MANALAPAN2 = "https://www.monmouthcountyclerk.gov/#sample-ballot-manalapan2-2026-general"
MARLBORO1 = "https://www.monmouthcountyclerk.gov/#sample-ballot-marlboro1-2026-general"
OCEAN1 = "https://www.monmouthcountyclerk.gov/#sample-ballot-ocean1-2026-general"
OCEAN2 = "https://www.monmouthcountyclerk.gov/#sample-ballot-ocean2-2026-general"
HAZLET = "https://www.monmouthcountyclerk.gov/#sample-ballot-hazlet-2026-general"
MILLSTONE = "https://www.monmouthcountyclerk.gov/#sample-ballot-millstone-2026-general"
PINEHILL = "https://www.camdencounty.com/#sample-ballot-pinehill-2026-general"
WINSLOW = "https://www.camdencounty.com/#sample-ballot-winslow-2026-general"
BERLIN = "https://www.camdencounty.com/#sample-ballot-berlin-2026-general"
LINDENWOLD = "https://www.camdencounty.com/#sample-ballot-lindenwold-2026-general"
CHERRYHILL = "https://www.camdencounty.com/#sample-ballot-cherryhill-2026-general"
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
    PENNINGTON: (
        "Mercer County Clerk",
        "Official General Election Sample Ballot, Pennington Borough, November 3, 2026",
        "manual/MF-476/F11 Pennington General 2026.pdf",
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
    FRANKLIN: (
        "Hunterdon County Clerk",
        "Official General Election Ballot, Franklin Township District 1, November 3, 2026",
        "manual/MF-464/Hunterdon/Franklin 1-3.pdf",
    ),
    EAST_AMWELL: (
        "Hunterdon County Clerk",
        "Official General Election Ballot, East Amwell Township District 1, November 3, 2026",
        "manual/MF-464/Hunterdon/East Amwell 1-5.pdf",
    ),
    LAMBERTVILLE: (
        "Hunterdon County Clerk",
        "Official General Election Ballot, Lambertville City District 1, November 3, 2026",
        "manual/MF-464/Hunterdon/Lambertville City 1-4.pdf",
    ),
    WARREN: (
        "Warren County Clerk",
        "2026 General Election Ballot, Washington Township District 1, November 3, 2026",
        "manual/MF-464/Warren/2026 General ElectionWashington Townshi.pdf",
    ),
    WALL1: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Wall Township Fire District No. 1, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Wall-Twp-FD1.pdf",
    ),
    WALL3: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Wall Township Fire District No. 3, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Wall-Twp-FD3.pdf",
    ),
    TINTON1: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Tinton Falls Fire District No. 1, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Tinton-Falls-Boro-FD1.pdf",
    ),
    TINTON2: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Tinton Falls Fire District No. 2, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Tinton-Falls-Boro-FD2.pdf",
    ),
    MANALAPAN2: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Manalapan Township Fire District No. 2, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Manalapan-Twp-FD2.pdf",
    ),
    MARLBORO1: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Marlboro Township Fire District No. 1, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Marlboro-Twp-FD1.pdf",
    ),
    OCEAN1: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Ocean Township Fire District No. 1, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Ocean-Twp-FD1.pdf",
    ),
    OCEAN2: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Ocean Township Fire District No. 2, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Ocean-Twp-FD2.pdf",
    ),
    HAZLET: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Hazlet Township, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Hazlet-Twp.pdf",
    ),
    MILLSTONE: (
        "Monmouth County Clerk",
        "Official General Election Sample Ballot, Millstone Township, November 3, 2026",
        "manual/MF-479/Monmouth/SB-Millstone-Twp.pdf",
    ),
    PINEHILL: (
        "Camden County Clerk",
        "Official General Election Sample Ballot, Pine Hill Borough, November 3, 2026",
        "manual/MF-479/Camden/PINE_HILL.pdf",
    ),
    WINSLOW: (
        "Camden County Clerk",
        "Official General Election Sample Ballot, Winslow Township Ward 1, November 3, 2026",
        "manual/MF-479/Camden/WINSLOW_W1.pdf",
    ),
    BERLIN: (
        "Camden County Clerk",
        "Official General Election Sample Ballot, Berlin Township, November 3, 2026",
        "manual/MF-479/Camden/BERLIN_TWP.pdf",
    ),
    LINDENWOLD: (
        "Camden County Clerk",
        "Official General Election Sample Ballot, Lindenwold Borough, November 3, 2026",
        "manual/MF-479/Camden/LINDENWOLD.pdf",
    ),
    CHERRYHILL: (
        "Camden County Clerk",
        "Official General Election Sample Ballot, Cherry Hill, November 3, 2026",
        "manual/MF-479/Camden/CHERRY_HILL.pdf",
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
    ("Pennington Borough Fire District", "Mercer", False, 2, PENNINGTON, ["Ross Dodd", "Scott T. Schaub"]),
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
    ("Franklin Township Fire District No. 1", "Hunterdon", False, 1, FRANKLIN, ["Charles A. Patkochis"]),
    ("Amwell Valley Fire Company", "Hunterdon", False, 2, EAST_AMWELL, ["Paul E. Carlucci"]),
    ("Lambertville Fire District No. 1", "Hunterdon", False, 1, LAMBERTVILLE, ["Nancy Bakewell"]),
    ("Washington Township Fire District No. 1", "Warren", False, 2, WARREN, ["Michael Riotto", "Karl Spolarich"]),
    ("Wall Fire District No. 1", "Monmouth", False, 2, WALL1, ["Roy Curtis", "Logan Rogers"]),
    ("Wall Fire District No. 3", "Monmouth", False, 2, WALL3, ["Michael Fischer", "Eric B. Olsen"]),
    ("Tinton Falls Fire District No. 1", "Monmouth", False, 1, TINTON1, ["Cary Costa", "Angie Swaroop"]),
    ("Tinton Falls Fire District No. 2", "Monmouth", False, 2, TINTON2, ["Spencer Maran"]),
    ("Manalapan Fire District No. 2", "Monmouth", False, 2, MANALAPAN2, ["Timothy D. Kirkland", "Joseph Spevak"]),
    ("Marlboro Fire District No. 1", "Monmouth", False, 2, MARLBORO1, ["Michael MacDonald", "Martin Rabinowitz"]),
    ("Ocean Fire District No. 1", "Monmouth", False, 1, OCEAN1, ["Dimitrios Kelesidis"]),
    ("Ocean Fire District No. 2", "Monmouth", False, 1, OCEAN2, ["Edward W. Horn", "Earl H. Gifford"]),
    ("Hazlet Fire District No. 1", "Monmouth", False, 2, HAZLET, ["Donald Malloy", "Michael C. Storcks"]),
    ("Millstone Fire District No. 1", "Monmouth", False, 2, MILLSTONE, ["Genevieve Semanchick", "Ronald P. Gesualdo"]),
    (
        "Pine Hill Borough Fire District No. 1",
        "Camden",
        False,
        2,
        PINEHILL,
        ["James T. Wakeley", "Richard Perez", "Timothy W. Chalfant", "Christopher Harris"],
    ),
    (
        "Winslow Township Fire District No. 1",
        "Camden",
        False,
        2,
        WINSLOW,
        ["Anthony L. Church", "John Matro", "John E. (Smitty) Smith", "Brian Donovan"],
    ),
    ("Berlin Township Fire District No. 1", "Camden", False, 1, BERLIN, ["Joseph C. Mohan", "Michael A. Schaffer"]),
    (
        "Lindenwold Borough Fire District No. 1",
        "Camden",
        False,
        2,
        LINDENWOLD,
        ["Richard J. Paul Jr.", "Michael Nolan", "Mark Wells", "Wayne Hans"],
    ),
    (
        "Cherry Hill Fire District No. 13",
        "Camden",
        False,
        2,
        CHERRYHILL,
        ["Michael Richter", "John J. Mulholland Jr.", "Rosy Arroyo"],
    ),
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
        summary="Fire commissioner races on the November 3, 2026 ballot in Burlington, Camden, Gloucester, Hunterdon, Mercer, Monmouth, Somerset, and Warren County fire districts, with candidates from the county clerks' official ballots and candidate lists",
        sources={url: (publisher, title) for url, (publisher, title, _) in SOURCES.items()},
        frame=pl.read_parquet(PARSED / "candidates.parquet"),
    )
