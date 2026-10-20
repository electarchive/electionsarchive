import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ms_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Mississippi Summary for General Election: {day:%b. %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "Mississippi's House results by county from the Secretary of State's elections spreadsheet summary of the general election"
STATE = "MS"
FIPS = "28"
ITEMS = {
    1994: ("micro_IA40706953_0014", date(1994, 11, 8), (5, 6, 7, 8, 9)),
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
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
