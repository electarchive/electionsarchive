import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.oh_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Ohio Secretary of State"
TITLE = "Ohio Election Statistics, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Ohio's House results by county from the Secretary of State's Ohio Election Statistics"
STATE = "OH"
FIPS = "39"
ITEMS = {
    1982: ("micro_IA40706919_0320", date(1982, 11, 2), range(156, 161)),
    1984: ("micro_IA40706919_0320", date(1984, 11, 6), range(532, 537)),
    1986: ("micro_IA40706926_0051", date(1986, 11, 4), range(126, 130)),
    1988: ("micro_IA40706932_0362", date(1988, 11, 8), range(116, 120)),
    1990: ("micro_IA40706942_0388", date(1990, 11, 6), range(101, 105)),
    1992: ("micro_IA40706946_0141", date(1992, 11, 3), range(127, 131)),
    1994: ("micro_IA40706953_0173", date(1994, 11, 8), range(141, 145)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1986, 6)}
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
