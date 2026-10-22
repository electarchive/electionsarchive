import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books, sri_primaries
from ea.store import DATA

SOURCE = "us.ks_pri94"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Kansas Secretary of State"
LICENSE = "Public record"
SUMMARY = (
    "Kansas's U.S. House and Senate primaries of 1994 by county from the Secretary of State's primary election report"
)
BOOK_TITLE = "Primary Election Report of the Secretary of State of the State of Kansas, 1994"
STATE = "KS"
FIPS = "20"
COUNTIES = 105
YEAR = 1994
DAY = "1994-08-02"
BOOK = "micro_IA40706952_0371"
VOLUMES = {
    1994: (
        f"https://archive.org/download/{BOOK}/{BOOK}.pdf",
        f"https://archive.org/details/{BOOK}",
        date(1994, 8, 2),
        tuple(range(31, 35)),
    )
}
PARTIES = {"D": {"democratic"}, "R": {"republican"}}
ERRATA: set[tuple[int, tuple[str, int]]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
RENAMED: str | None = None
REVISION = 1
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
CHECKED: set[tuple[str, str]] = set()
NONDEFAULT: set[str] = set()
ANCHORS = {
    (32, (kind, 2)): {
        (r, c): (
            round((right - 12 * digits) * 1.56),
            round((1678 + 35 * r) * 1.56 - 18),
            round(right * 1.56),
            round((1678 + 35 * r) * 1.56 + 18),
        )
        for c, right in enumerate(rights)
        for r, digits in enumerate(widths[c])
    }
    for kind, rights, widths in (
        ("D-house", (506,), ((3, 3, 5),)),
        ("R-house", (713, 920, 1130), ((3, 3, 3), (3, 3, 3), (3, 2, 3))),
    )
}
DERIVED: set[tuple[str, str, int]] = set()
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    sri_primaries.parse(sys.modules[__name__])


def emit() -> Path:
    return sri_primaries.emit(sys.modules[__name__])
