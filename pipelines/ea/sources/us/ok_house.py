import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ok_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Oklahoma State Election Board"
TITLE = "State of Oklahoma Election Results and Statistics, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Oklahoma's House results by county from the State Election Board's election results and statistics"
STATE = "OK"
FIPS = "40"
ITEMS = {
    1978: ("micro_IA40706903_0066", date(1978, 11, 7), range(77, 79)),
    1980: ("micro_IA40706906_0120", date(1980, 11, 4), range(73, 77)),
    1982: ("micro_IA40706913_0232", date(1982, 11, 2), range(83, 88)),
    1984: ("micro_IA40706919_0326", date(1984, 11, 6), range(54, 58)),
    1986: ("micro_IA40706926_0058", date(1986, 11, 4), range(109, 112)),
    1988: ("micro_IA40706932_0371", date(1988, 11, 8), range(69, 71)),
    1990: ("micro_IA40706939_0247", date(1990, 11, 6), range(113, 116)),
    1992: ("micro_IA40706946_0152", date(1992, 11, 3), range(64, 67)),
    1994: ("micro_IA40706953_0183", date(1994, 11, 8), range(136, 140)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1982, 3)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED = "us.ok_seb"
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
