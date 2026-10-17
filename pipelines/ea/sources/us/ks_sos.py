import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ks_sos"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Kansas Secretary of State"
TITLE = "Election Statistics, State of Kansas, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Kansas's House results by county from the Secretary of State's election statistics"
STATE = "KS"
FIPS = "20"
ITEMS = {
    1980: ("micro_IA40706905_0339", date(1980, 11, 4), range(76, 79)),
    1984: ("micro_IA40706919_0082", date(1984, 11, 6), range(72, 75)),
    1986: ("micro_IA40706928_0350", date(1986, 11, 4), range(71, 74)),
    1988: ("micro_IA40706932_0105", date(1988, 11, 8), range(94, 98)),
    1990: ("micro_IA40706938_0450", date(1990, 11, 6), range(106, 110)),
    1992: ("micro_IA40706945_0353", date(1992, 11, 3), range(128, 132)),
    1994: ("micro_IA40706952_0371", date(1994, 11, 8), range(101, 105)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1984, 1), (1992, 1)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
