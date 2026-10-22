import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books, sri_primaries
from ea.store import DATA

SOURCE = "us.in_pri94"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Indiana Secretary of State"
LICENSE = "Public record"
SUMMARY = "Indiana's U.S. House and Senate primaries of 1994 by county from the Secretary of State's primary election report"
BOOK_TITLE = "Primary Election Report of the Secretary of State of the State of Indiana, 1994"
STATE = "IN"
FIPS = "18"
COUNTIES = 92
YEAR = 1994
DAY = "1994-05-03"
BOOK = "micro_IA40706952_0343"
VOLUMES = {
    1994: (
        f"https://archive.org/download/{BOOK}/{BOOK}.pdf",
        f"https://archive.org/details/{BOOK}",
        date(1994, 5, 3),
        tuple(range(4, 11)),
    )
}
PARTIES = {"D": {"democratic"}, "R": {"republican"}}
ERRATA = {(1994, ("D-senate", 0)), (1994, ("R-house", 3)), (1994, ("R-house", 8))}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
RENAMED: str | None = None
REVISION = 1
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
CHECKED = {
    ("1994-05-03:IN:house:3:republican", "burkett"),
    ("1994-05-03:IN:house:8:republican", "hostettler"),
}
NONDEFAULT = {"1994-05-03:IN:house:3:republican", "1994-05-03:IN:house:8:republican"}
DERIVED: set[tuple[str, str, int]] = set()
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    sri_primaries.parse(sys.modules[__name__])


def emit() -> Path:
    return sri_primaries.emit(sys.modules[__name__])
