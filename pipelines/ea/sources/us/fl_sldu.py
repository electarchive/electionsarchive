import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.fl_sldu"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Florida Division of Elections"
TITLE = "Tabulation of Official Votes, Florida General Election, {day:%Y}: State Senators"
LICENSE = "Public record"
SUMMARY = "Florida's 1980 state senate general elections by county from the Division of Elections' tabulation of official votes"
STATE = "FL"
FIPS = "12"
CHAMBER = "senate"
ITEM = "micro_IA40706905_0253"
VOLUMES = {
    1980: (f"https://archive.org/download/{ITEM}/{ITEM}.pdf", f"https://archive.org/details/{ITEM}", date(1980, 11, 4), range(11, 13)),
}
NAMES = {("FL", "Dade"): "Miami-Dade"}
KEYS = {
    (1980, 3): '1980-11-04:FL:senate:3:1:g',
    (1980, 6): '1980-11-04:FL:senate:5:2:gs',
    (1980, 7): '1980-11-04:FL:senate:7:1:g',
    (1980, 11): '1980-11-04:FL:senate:11:g',
    (1980, 13): '1980-11-04:FL:senate:12:2:g',
    (1980, 15): '1980-11-04:FL:senate:14:2:g',
    (1980, 17): '1980-11-04:FL:senate:16:2:g',
    (1980, 19): '1980-11-04:FL:senate:18:2:g',
    (1980, 23): '1980-11-04:FL:senate:21:3:g',
    (1980, 27): '1980-11-04:FL:senate:26:2:g',
    (1980, 29): '1980-11-04:FL:senate:29:1:g',
    (1980, 33): '1980-11-04:FL:senate:33:1:g',
    (1980, 37): '1980-11-04:FL:senate:35:3:g',
}
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
