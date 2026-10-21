import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ga_sldu"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Georgia Secretary of State"
TITLE = "General Election November 3, 1992: Votes by County for Candidates"
LICENSE = "Public record"
SUMMARY = "Georgia's 1992 state senate general elections by county from the Secretary of State's election returns on the SRI microfiche"
STATE = "GA"
FIPS = "13"
CHAMBER = "senate"
ITEM = "micro_IA40706945_0278"
PAGES = [*range(43, 74), *range(75, 100)]
VOLUMES = {
    1992: (
        f"https://archive.org/download/{ITEM}/{ITEM}.pdf",
        f"https://archive.org/details/{ITEM}",
        date(1992, 11, 3),
        PAGES,
    ),
}
NAMES: dict[tuple[str, str], str] = {}
KEYS = {(1992, district): f"1992-11-03:GA:senate:{district}:g" for district in range(1, 57)}
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
