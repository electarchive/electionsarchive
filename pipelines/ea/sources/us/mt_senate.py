import sys
from pathlib import Path

from ea.sources.us import house_books, mt_house

SOURCE = "us.mt_senate"
KEYS = {(1994, 0): "1994-11-08:MT:senate"}
RAW = mt_house.RAW
VERSION = mt_house.VERSION
PUBLISHER = mt_house.PUBLISHER
TITLE = mt_house.TITLE
LICENSE = mt_house.LICENSE
SUMMARY = (
    "Montana's U.S. Senate results by county from the Secretary of State's statewide canvass on the SRI microfiche"
)
STATE = mt_house.STATE
FIPS = mt_house.FIPS
ITEMS = mt_house.ITEMS
VOLUMES = mt_house.VOLUMES
REVISION = 1
RENAMED: str | None = None
TURNED = mt_house.TURNED
NAMES = mt_house.NAMES


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    mt_house.locate(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
