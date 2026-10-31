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
    1942: (date(1942, 11, 3), range(178, 181)),
    1944: (date(1944, 11, 7), range(180, 183)),
    1946: (date(1946, 11, 5), range(176, 179)),
    1948: (date(1948, 11, 2), range(176, 179)),
    1950: (date(1950, 11, 7), range(194, 196)),
    1952: (date(1952, 11, 4), range(177, 179)),
    1954: (date(1954, 11, 2), range(179, 181)),
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
ERRATA = {(1950, 7)}
NOTES = {
    (1950, 7): (
        (
            "The 1950 Legislative Manual prints Carl J. Eastvold's figures for Pope, Redwood, Renville, and Stevens counties "
            "without their leading digit (',803', ',923', '.943', and '.179'). The printed column total of 40,785 shows that "
            "the four missing digits add up to 5, but not how they split among the four counties, so those four counties "
            "are left out of this set entirely, with their other columns, and its totals are short of the district's."
        ),
    ),
}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 2
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
