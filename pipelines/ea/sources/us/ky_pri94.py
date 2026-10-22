import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books, sri_primaries
from ea.store import DATA

SOURCE = "us.ky_pri94"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Kentucky Secretary of State"
LICENSE = "Public record"
SUMMARY = "Kentucky's U.S. House primaries of 1994 by county from the Secretary of State's official returns"
BOOK_TITLE = "Official Primary and General Election Returns for 1994, Kentucky Secretary of State"
STATE = "KY"
FIPS = "21"
COUNTIES = 120
YEAR = 1994
DAY = "1994-05-24"
BOOK = "micro_IA40706952_0386"
VOLUMES = {
    1994: (
        f"https://archive.org/download/{BOOK}/{BOOK}.pdf",
        f"https://archive.org/details/{BOOK}",
        date(1994, 5, 24),
        tuple(range(3, 7)),
    )
}
PARTIES = {"D": {"democratic"}, "R": {"republican"}}
ERRATA: set[tuple[int, tuple[str, int]]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
RENAMED: str | None = None
REVISION = 1
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
CHECKED: set[tuple[str, str]] = set()
NONDEFAULT: set[str] = set()
DERIVED: set[tuple[str, str, int]] = set()
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    sri_primaries.parse(sys.modules[__name__])


def emit() -> Path:
    return sri_primaries.emit(sys.modules[__name__])
