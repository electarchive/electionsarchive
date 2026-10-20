import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.fl_sldl"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Florida Division of Elections"
TITLE = "Tabulation of Official Votes, Florida General Election, {day:%Y}: State House"
LICENSE = "Public record"
SUMMARY = "Florida's 1980 state house general elections by county from the Division of Elections' tabulation of official votes"
STATE = "FL"
FIPS = "12"
CHAMBER = "house"
ITEM = "micro_IA40706905_0253"
VOLUMES = {
    1980: (f"https://archive.org/download/{ITEM}/{ITEM}.pdf", f"https://archive.org/details/{ITEM}", date(1980, 11, 4), range(12, 16)),
}
NAMES = {("FL", "Dade"): "Miami-Dade"}
KEYS = {
    (1980, 22): '1980-11-04:FL:house:21:2:g',
    (1980, 24): '1980-11-04:FL:house:21:4:g',
    (1980, 25): '1980-11-04:FL:house:25:g',
    (1980, 29): '1980-11-04:FL:house:29:1:g',
    (1980, 31): '1980-11-04:FL:house:29:3:g',
    (1980, 32): '1980-11-04:FL:house:32:g',
    (1980, 34): '1980-11-04:FL:house:34:g',
    (1980, 35): '1980-11-04:FL:house:35:g',
    (1980, 37): '1980-11-04:FL:house:37:g',
    (1980, 38): '1980-11-04:FL:house:38:1:g',
    (1980, 39): '1980-11-04:FL:house:38:2:g',
    (1980, 40): '1980-11-04:FL:house:38:3:g',
    (1980, 42): '1980-11-04:FL:house:38:5:g',
    (1980, 43): '1980-11-04:FL:house:38:6:g',
    (1980, 44): '1980-11-04:FL:house:44:1:g',
    (1980, 46): '1980-11-04:FL:house:44:3:g',
    (1980, 48): '1980-11-04:FL:house:48:g',
    (1980, 55): '1980-11-04:FL:house:53:3:g',
    (1980, 60): '1980-11-04:FL:house:57:4:g',
    (1980, 63): '1980-11-04:FL:house:62:2:g',
    (1980, 64): '1980-11-04:FL:house:62:3:g',
    (1980, 69): '1980-11-04:FL:house:66:4:g',
    (1980, 72): '1980-11-04:FL:house:71:2:g',
    (1980, 78): '1980-11-04:FL:house:78:1:g',
    (1980, 79): '1980-11-04:FL:house:78:2:g',
    (1980, 80): '1980-11-04:FL:house:78:3:g',
    (1980, 81): '1980-11-04:FL:house:78:4:g',
    (1980, 82): '1980-11-04:FL:house:78:5:g',
    (1980, 83): '1980-11-04:FL:house:78:6:g',
    (1980, 84): '1980-11-04:FL:house:84:1:g',
    (1980, 85): '1980-11-04:FL:house:84:2:g',
    (1980, 86): '1980-11-04:FL:house:84:3:g',
    (1980, 87): '1980-11-04:FL:house:84:4:g',
    (1980, 104): '1980-11-04:FL:house:104:1:g',
    (1980, 105): '1980-11-04:FL:house:104:2:g',
    (1980, 107): '1980-11-04:FL:house:104:4:g',
    (1980, 109): '1980-11-04:FL:house:109:1:g',
    (1980, 110): '1980-11-04:FL:house:109:2:g',
    (1980, 111): '1980-11-04:FL:house:109:3:g',
    (1980, 112): '1980-11-04:FL:house:109:4:g',
    (1980, 113): '1980-11-04:FL:house:109:5:g',
    (1980, 114): '1980-11-04:FL:house:109:6:g',
    (1980, 115): '1980-11-04:FL:house:115:1:g',
    (1980, 116): '1980-11-04:FL:house:115:2:g',
    (1980, 117): '1980-11-04:FL:house:115:3:g',
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
