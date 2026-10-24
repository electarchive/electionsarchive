import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.mn_manual_house"
RAW = "us.mn_lrl"
VERSION = "1"
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Minnesota Legislative Manual, election returns {day:%Y}"
LICENSE = "Public record; the manuals carry no copyright notice, and the Legislative Reference Library posts no terms for its scans"
SUMMARY = "Minnesota's House results by county from the Legislative Manuals"
STATE = "MN"
FIPS = "27"
LANDING = "https://www.lrl.mn.gov/mngov/electionresults"
ITEMS = {
    1934: (date(1934, 11, 6), range(174, 176)),
    1936: (date(1936, 11, 3), range(176, 179)),
    1938: (date(1938, 11, 8), range(176, 179)),
    1940: (date(1940, 11, 5), range(174, 177)),
    1950: (date(1950, 11, 7), range(194, 196)),
}
VOLUMES = {
    year: (
        f"https://www.lrl.mn.gov/archive/sessions/electionresults/{day}-g-man.pdf",
        LANDING,
        day,
        pages,
    )
    for year, (day, pages) in ITEMS.items()
}
ERRATA: set[tuple[int, int]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
COMMAS_YEARS = set(ITEMS)
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
