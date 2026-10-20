import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.tribune_almanac"
RAW = "us.tribune_almanac"
VERSION = "1"
KIND = "compendium"
PUBLISHER = "The Tribune Association"
TITLE = "The Tribune Almanac"
LICENSE = "Public domain"
SUMMARY = "House results by county from the Tribune Almanac"
STATE = None
FIPS = None
ITEMS = {
    1854: ("tribunealmanacpo1855newy", 1855, date(1854, 11, 7), (51, 52, 53, 55, 56, 57, 58, 59, 60)),
    1856: ("vol1857tribuneal00unse", 1857, date(1856, 11, 4), (47, 48, 49, 50, 54, 56, 57, 58, 59, 60, 61, 63)),
    1858: ("vol1859tribuneal00unse", 1859, date(1858, 11, 2), (46, 55, 57, 58, 59, 60, 61)),
    1860: ("vol1861tribuneal00unse", 1861, date(1860, 11, 6), (42, 47, 48, 49, 56, 57, 58, 59, 60, 62, 63)),
    1862: ("tribunealmanacpo1863newy", 1863, date(1862, 11, 4), (57, 58)),
    1864: ("tribunealmanacpo00otta", 1865, date(1864, 11, 8), (53,)),
    1866: ("bub_gb_53IwAAAAIAAJ", 1867, date(1866, 11, 6), (51,)),
    1868: ("tribunealmanacpo1869newy", 1869, date(1868, 11, 3), (62,)),
    1878: ("vol1881tribuneal00unse", 1881, date(1878, 11, 5), (81, 82)),
    1880: ("vol1881tribuneal00unse", 1881, date(1880, 11, 2), (81, 82)),
    1874: ("tribunealmanacpo18751879newy", 1875, date(1874, 11, 3), (76, 77, 78)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, _, day, pages) in ITEMS.items()
}
TITLES = {year: f"The Tribune Almanac and Political Register for {volume}" for year, (_, volume, _, _) in ITEMS.items()}
TITLES[1854] = "The Whig Almanac and United States Register for 1855"
PARTIES = ("rep", "dem", "lib", "ind", "scat", "cons", "rad", "opp", "gbk", "pro", "lab", "ref", "peo", "tem")
ERRATA: set[tuple[int, tuple[str, int]]] = {(1854, ("IA", 2)), (1860, ("PA", 17)), (1856, ("NY", 26)), (1856, ("NY", 27)), (1858, ("NY", 31)), (1858, ("OH", 16)), (1858, ("OH", 18)), (1862, ("NY", 27)), (1864, ("NY", 11)), (1868, ("NY", 12)), (1868, ("NY", 14))}
NAMES: dict[tuple[str, str], str] = {}
KEYS: dict[tuple[int, str, int], str] = {}
FORMER: dict[tuple[str, str], tuple[str, str, date]] = {("MI", "manitou"): ("Manitou", "26029", date(1895, 1, 1))}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 190, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
