import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.in_sos"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Indiana Secretary of State"
TITLE = "Election Report, State of Indiana, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Indiana's House results by county from the Secretary of State's election reports"
STATE = "IN"
FIPS = "18"
ITEMS = {
    1980: ("micro_IA40706905_0314", date(1980, 11, 4), range(43, 46)),
    1986: ("micro_IA40706925_0233", date(1986, 11, 4), range(44, 49)),
    1990: ("micro_IA40706938_0427", date(1990, 11, 6), range(36, 40)),
    1992: ("micro_IA40706945_0328", date(1992, 11, 3), range(39, 43)),
    1994: ("micro_IA40706952_0343", date(1994, 11, 8), range(37, 40)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1986, 5)}
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
