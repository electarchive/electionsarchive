import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.il_sbe"
RAW = SOURCE
VERSION = "2"
PUBLISHER = "Illinois State Board of Elections"
TITLE = "Official Vote Cast at the General Election, November {day:%-d, %Y}"
LICENSE = "Public domain"
SUMMARY = "Illinois's House results by county from the State Board of Elections' official vote"
STATE = "IL"
FIPS = "17"
ITEMS = {
    1966: ("officialvote66illi", date(1966, 11, 8), range(34, 39)),
    1968: ("officialvote1968illi", date(1968, 11, 5), range(67, 72)),
    1970: ("officialvoteofst1970illi", date(1970, 11, 3), range(37, 42)),
    1972: ("officialvote72illi", date(1972, 11, 7), range(57, 61)),
    1974: ("officialvote1974illi", date(1974, 11, 5), range(29, 35)),
    1976: ("officialvoteofst1976illi", date(1976, 11, 2), range(47, 53)),
    1978: ("officialvote78illi", date(1978, 11, 7), range(58, 64)),
    1980: ("officialvote1980illi", date(1980, 11, 4), range(28, 35)),
    1982: ("officialvoteofst1982illi", date(1982, 11, 2), range(36, 43)),
    1984: ("officialvote84illi", date(1984, 11, 6), range(34, 40)),
    1986: ("officialvote1986illi", date(1986, 11, 4), range(34, 43)),
    1988: ("officialvoteofst1988illi", date(1988, 11, 8), range(33, 44)),
    1990: ("officialvote90illi", date(1990, 11, 6), range(37, 51)),
    1992: ("stateofillinois1992illi", date(1992, 11, 3), range(59, 72)),
    1994: ("stateofillinoiso1994illi", date(1994, 11, 8), range(39, 52)),
    1996: ("stateofillinois1996illi", date(1996, 11, 5), range(32, 45)),
    1998: ("officialvoteof1998illi", date(1998, 11, 3), range(33, 45)),
    2000: ("stateofillinois2000illi", date(2000, 11, 7), range(15, 28)),
    2002: ("officialvoteof2002illi", date(2002, 11, 5), range(31, 44)),
    2004: ("stateofillinoiso2004illi", date(2004, 11, 2), range(35, 48)),
    2006: ("officialvote2006illi", date(2006, 11, 7), range(37, 51)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1972, 12), (1982, 6), (1986, 3)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 2
TURNED: set[tuple[int, int]] = set()
THRESHOLDS: tuple[int, ...] = ()


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
