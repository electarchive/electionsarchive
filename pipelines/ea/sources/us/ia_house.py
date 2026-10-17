import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ia_house"
RAW = "us.ia_sos"
VERSION = "1"
PUBLISHER = "Iowa Secretary of State"
TITLE = "Official canvass summary"
LICENSE = "Public record"
SUMMARY = "Iowa's House results by county from the Secretary of State's canvass books"
STATE = "IA"
FIPS = "19"
DAYS = {
    1980: (date(1980, 11, 4), range(8, 9)),
    1982: (date(1982, 11, 2), range(2, 6)),
    1984: (date(1984, 11, 6), range(8, 10)),
    1986: (date(1986, 11, 4), range(4, 8)),
    1988: (date(1988, 11, 8), range(7, 9)),
    1990: (date(1990, 11, 6), range(5, 9)),
    1992: (date(1992, 11, 3), range(29, 34)),
    1994: (date(1994, 11, 8), range(7, 10)),
    1996: (date(1996, 11, 5), range(6, 9)),
}
VOLUMES = {
    year: (
        f"https://sos.iowa.gov/elections/pdf/results/{'80s' if year < 1990 else '90s'}/{year}gencanv.pdf",
        "https://sos.iowa.gov/elections/results/",
        day,
        pages,
    )
    for year, (day, pages) in DAYS.items()
}
ERRATA = {(1982, 1), (1994, 5), (1996, 1)}
ALIASES = {"Write-Ins": ("sc", "scat", "scattering", "scattered")}
BESIDE = ("counties",)
REVISION = 1
TURNED = {(1984, 8), (1984, 9)}


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
