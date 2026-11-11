import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.wa_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Washington Secretary of State"
TITLE = "Official Returns of the State General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "Washington's House and Senate results by county from the Secretary of State's official abstract of the general election"
STATE = "WA"
FIPS = "53"
ITEMS = {
    1986: ("micro_IA40706926_0225", date(1986, 11, 4), (5, 6, 7, 8)),
    1994: ("micro_IA40706953_0368", date(1994, 11, 8), (6, 7, 8)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
KEYS = {(1986, 0): "1986-11-04:WA:senate"}
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
