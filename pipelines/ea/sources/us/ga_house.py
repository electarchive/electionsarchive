import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ga_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Georgia Secretary of State"
TITLE = "State of Georgia General Election: Nov. 8, 1994, Votes by County for Candidates"
LICENSE = "Public record"
SUMMARY = "Georgia's House results by county from the Secretary of State's election returns"
STATE = "GA"
FIPS = "13"
ITEM = "micro_IA40706952_0290"
VOLUMES = {
    1994: (f"https://archive.org/download/{ITEM}/{ITEM}.pdf", f"https://archive.org/details/{ITEM}", date(1994, 11, 8), range(62, 73)),
}
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
