import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.md_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Maryland State Administrative Board of Election Laws"
TITLE = "Presidential General Election Returns - November 3, 1992"
LICENSE = "Public record"
SUMMARY = "Maryland's House results by county from the State Administrative Board of Election Laws' general election returns on the SRI microfiche"
STATE = "MD"
FIPS = "24"
ITEMS = {
    1992: ("micro_IA40706945_0405", date(1992, 11, 3), tuple(range(13, 21))),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA: set[tuple[int, int]] = set()
ALIASES: dict[str, tuple[str, ...]] = {"Gilchrest": ("gilchrest", "tilchrest", "trichrest", "gilchreat", "totchrest")}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = False
TURNED: set[tuple[int, int]] = set()
NAMES = {("MD", "Baltimore City"): "24510", ("MD", "Baltimore"): "24005"}
THRESHOLDS = (110, 130, 150, 170, 190, 210, 230, 250)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
