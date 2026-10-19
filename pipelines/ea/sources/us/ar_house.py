import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ar_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Arkansas General Election Returns, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "Arkansas's House results by county from the Secretary of State's official general election returns"
STATE = "AR"
FIPS = "05"
ITEMS = {
    1984: ("micro_IA40706925_0082", date(1984, 11, 6), (5, 6, 7)),
    1986: ("micro_IA40706925_0082", date(1986, 11, 4), (74, 75, 76, 77)),
    1988: ("micro_IA40706931_0281", date(1988, 11, 8), (7, 8, 9)),
    1990: ("micro_IA40706938_0258", date(1990, 11, 6), (5, 6, 7, 9, 11, 12)),
    1992: ("micro_IA40706945_0167", date(1992, 11, 3), (99, 100, 101, 102, 103, 105, 106)),
    1994: ("micro_IA40706952_0176", date(1994, 11, 8), range(1, 7)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1984, 1)}
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
