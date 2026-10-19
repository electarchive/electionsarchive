import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ca_house"
RAW = "us.ca_sov"
VERSION = "1"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "California's House results by county from the Secretary of State's Statement of Vote"
STATE = "CA"
FIPS = "06"
ITEMS = {
    1984: ("statementofvote61984cali", date(1984, 11, 6), range(20, 27)),
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
