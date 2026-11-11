import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.or_sri"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Oregon Secretary of State"
TITLE = "Official Abstract of Votes, General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = (
    "Oregon's president, U.S. Senate, U.S. House, and governor results by county from the Secretary of State's "
    "official abstracts of votes, 1980-86"
)
STATE = "OR"
FIPS = "41"
ITEMS = {
    1980: ("micro_IA40706906_0142", date(1980, 11, 4), (13, 14, 15, 16)),
    1982: ("micro_IA40706913_0253", date(1982, 11, 2), (13, 14, 16)),
    1984: ("micro_IA40706919_0347", date(1984, 11, 6), (13, 14, 15, 16)),
    1986: ("micro_IA40706926_0078", date(1986, 11, 4), (13, 14, 15, 17)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
KEYS = {
    (1980, -2): "1980-11-04:OR:senate",
    (1982, -1): "1982-11-02:OR:governor",
    (1984, -2): "1984-11-06:OR:senate",
    (1986, 0): "1986-11-04:OR:senate",
    (1986, -1): "1986-11-04:OR:governor",
}
ERRATA: set[tuple[int, int]] = {(1980, 2)}
NOTES = {
    (1980, 2): (
        (
            "The abstract's county figures for Denny Smith add up to 141,844, ten fewer than the 141,854 it prints as "
            "the district total."
        ),
    )
}
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
