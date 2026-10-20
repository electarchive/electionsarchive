import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.fl_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Florida Division of Elections"
TITLE = "Tabulation of Official Votes, Florida General Election, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Florida's House results by county from the Division of Elections' tabulation of official votes"
STATE = "FL"
FIPS = "12"
ITEMS = {
    1980: ("micro_IA40706905_0253", date(1980, 11, 4), range(9, 11)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
NAMES = {("FL", "Dade"): "Miami-Dade"}
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
