import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.tn_sos"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Tennessee Secretary of State"
TITLE = "Certification of Election Returns, State of Tennessee, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Tennessee's House results by county from the Secretary of State's certification of election returns"
STATE = "TN"
FIPS = "47"
ITEMS = {
    1980: ("micro_IA40706906_0210", date(1980, 11, 4), range(18, 22)),
    1982: ("micro_IA40706913_0326", date(1982, 11, 2), range(11, 14)),
    1984: ("micro_IA40706919_0430", date(1984, 11, 6), range(20, 23)),
    1986: ("micro_IA40706926_0149", date(1986, 11, 4), range(9, 22)),
    1988: ("micro_IA40706933_0046", date(1988, 11, 8), range(12, 15)),
    1990: ("micro_IA40706939_0340", date(1990, 11, 6), range(6, 10)),
    1994: ("micro_IA40706953_0275", date(1994, 11, 8), range(10, 14)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1982, 1), (1988, 7), (1988, 8)}
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
