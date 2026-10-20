import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.al_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Alabama Secretary of State"
TITLE = "General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "Alabama's House results by county from the Secretary of State's general election returns on the SRI microfiche"
STATE = "AL"
FIPS = "01"
ITEMS = {
    1994: ("micro_IA40706952_0123", date(1994, 11, 8), tuple(range(51, 61))),
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
TRANSPOSED = {1994}
THRESHOLDS = (150, 170, 190, 210, 230)
NAMES = {("AL", "Bulter"): "Butler"}


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
