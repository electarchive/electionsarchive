import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ny_vote"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "New York State Board of Elections"
TITLE = "General Election Vote, New York State, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "New York's House results by county, with every party line, from the State Board of Elections' general election vote books"
STATE = "NY"
FIPS = "36"
ITEMS = {
    1986: ("micro_IA40706926_0008", date(1986, 11, 4), range(7, 13)),
    1988: ("micro_IA40706932_0313", date(1988, 11, 8), range(9, 16)),
    1990: ("micro_IA40706939_0195", date(1990, 11, 6), range(10, 18)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA: set[tuple[int, int]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
