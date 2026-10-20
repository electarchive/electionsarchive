import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.wi_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Wisconsin Legislative Reference Bureau"
TITLE = "Wisconsin Blue Book 1993-1994: Vote for Members of the 103rd Congress"
LICENSE = "Public record"
SUMMARY = "Wisconsin's House results by county from the Blue Book's vote tables"
STATE = "WI"
FIPS = "55"
ITEM = "micro_IA40706946_0362"
VOLUMES = {
    1992: (f"https://archive.org/download/{ITEM}/{ITEM}.pdf", f"https://archive.org/details/{ITEM}", date(1992, 11, 3), range(931, 934)),
}
TITLES = {1992: TITLE}
NAMES: dict[tuple[str, str], str] = {}
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
