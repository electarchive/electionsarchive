import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books, sri_primaries
from ea.store import DATA

SOURCE = "us.mn_pri94"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Minnesota Secretary of State"
LICENSE = "Public record"
SUMMARY = "Minnesota's U.S. House and Senate primaries of 1994 by county from the Secretary of State's Minnesota Election Results"
BOOK_TITLE = "Minnesota Election Results 1994: State Primary Election and State General Election"
STATE = "MN"
FIPS = "27"
COUNTIES = 87
YEAR = 1994
DAY = "1994-09-13"
BOOK = "micro_IA40706952_0469"
VOLUMES = {
    1994: (
        f"https://archive.org/download/{BOOK}/{BOOK}.pdf",
        f"https://archive.org/details/{BOOK}",
        date(1994, 9, 13),
        (3, 4, 5, 6, 15, 16),
    )
}
PARTIES = {
    "D": {"minnesota-democratic-farmer-labor", "minnesota-democraticfarmerlabor"},
    "R": {"independent-republican", "republican-party-of-minnesota"},
}
ERRATA = {(1994, ("R-house", 2))}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
RENAMED: str | None = None
REVISION = 1
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
CHECKED = {
    ("1994-09-13:MN:house:2:independent-republican", "revier"),
    ("1994-09-13:MN:house:2:republican-party-of-minnesota", "revier"),
}
NONDEFAULT = {
    "1994-09-13:MN:house:2:independent-republican",
    "1994-09-13:MN:house:2:republican-party-of-minnesota",
}
DERIVED: set[tuple[str, str, int]] = set()
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    sri_primaries.parse(sys.modules[__name__])


def emit() -> Path:
    return sri_primaries.emit(sys.modules[__name__])
