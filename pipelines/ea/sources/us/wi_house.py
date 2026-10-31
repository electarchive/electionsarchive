import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.wi_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Wisconsin Legislative Reference Bureau"
LICENSE = "Public record"
SUMMARY = "Wisconsin's House results by county from the Blue Book's vote tables"
STATE = "WI"
FIPS = "55"
ITEMS = {
    1980: ("micro_IA40706910_0093", "1981-1982", "97th", date(1980, 11, 4), range(919, 922)),
    1982: ("micro_IA40706917_0074", "1983-1984", "98th", date(1982, 11, 2), range(913, 916)),
    1984: ("micro_IA40706923_0147", "1985-1986", "99th", date(1984, 11, 6), range(928, 931)),
    1986: ("micro_IA40706929_0409", "1987-1988", "100th", date(1986, 11, 4), range(913, 916)),
    1988: ("micro_IA40706936_0250", "1989-1990", "101st", date(1988, 11, 8), range(933, 936)),
    1990: ("micro_IA40706939_0451", "1991-1992", "102nd", date(1990, 11, 6), range(909, 912)),
    1992: ("micro_IA40706946_0362", "1993-1994", "103rd", date(1992, 11, 3), range(931, 934)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, _, _, day, pages) in ITEMS.items()
}
TITLES = {
    year: f"Wisconsin Blue Book {book}: Vote for Members of the {congress} Congress"
    for year, (_, book, congress, _, _) in ITEMS.items()
}
NAMES: dict[tuple[str, str], str] = {}
ERRATA: set[tuple[int, int]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
SINGLE_HEADS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
