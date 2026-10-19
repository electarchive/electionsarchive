import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ej_almanac"
RAW = "us.ny_almanac"
VERSION = "1"
KIND = "compendium"
PUBLISHER = "Albany Evening Journal"
TITLE = "The Evening Journal Almanac"
LICENSE = "Public domain"
SUMMARY = "House results by county in every state from the Albany Evening Journal Almanac"
STATE = None
FIPS = None
ITEMS = {
    1872: (
        "eveningjournalal1873slsn",
        1873,
        date(1872, 11, 5),
        (
            51,
            52,
            53,
            54,
            55,
            56,
            57,
            58,
            59,
            60,
            61,
            62,
            63,
            64,
            65,
            66,
            67,
            68,
            69,
            70,
            72,
            73,
            74,
            75,
            76,
            77,
            78,
            79,
            80,
            81,
            82,
            83,
        ),
    ),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, _, day, pages) in ITEMS.items()
}
TITLES = {year: f"The Evening Journal Almanac, {volume}" for year, (_, volume, _, _) in ITEMS.items()}
PARTIES = ("rep", "dem", "lib", "ind", "scat", "cons", "rad", "opp", "gbk", "pro", "lab", "ref", "peo", "tem")
ERRATA: set[tuple[int, tuple[str, int]]] = {
    (1872, ("AL", 4)),
    (1872, ("CA", 3)),
    (1872, ("GA", 4)),
    (1872, ("GA", 9)),
    (1872, ("IL", 1)),
    (1872, ("IL", 19)),
    (1872, ("IN", 3)),
    (1872, ("KS", 0)),
    (1872, ("KY", 1)),
    (1872, ("KY", 8)),
    (1872, ("KY", 9)),
    (1872, ("KY", 10)),
    (1872, ("LA", 3)),
    (1872, ("LA", 5)),
    (1872, ("MI", 3)),
    (1872, ("MN", 1)),
    (1872, ("MN", 3)),
    (1872, ("MO", 10)),
    (1872, ("MO", 12)),
    (1872, ("MS", 6)),
    (1872, ("NC", 3)),
    (1872, ("NC", 5)),
    (1872, ("NC", 6)),
    (1872, ("NJ", 3)),
    (1872, ("OH", 1)),
    (1872, ("OH", 2)),
    (1872, ("OH", 5)),
    (1872, ("OH", 7)),
    (1872, ("OH", 9)),
    (1872, ("OH", 11)),
    (1872, ("OH", 12)),
    (1872, ("OH", 13)),
    (1872, ("OH", 18)),
    (1872, ("OH", 19)),
    (1872, ("OR", 0)),
    (1872, ("PA", 1)),
    (1872, ("PA", 2)),
    (1872, ("PA", 3)),
    (1872, ("PA", 13)),
    (1872, ("PA", 18)),
    (1872, ("PA", 23)),
    (1872, ("SC", 2)),
    (1872, ("SC", 3)),
    (1872, ("TN", 1)),
    (1872, ("TN", 4)),
    (1872, ("TN", 6)),
    (1872, ("VT", 2)),
}
NAMES = {
    ("AL", "Tuskaloosa"): "Tuscaloosa",
    ("CA", "Klamath"): "CA:klamath",
    ("FL", "Dade"): "Miami-Dade",
    ("GA", "Bullock"): "Bulloch",
    ("GA", "Campbell"): "GA:campbell",
    ("GA", "Dooley"): "Dooly",
    ("GA", "Glasscock"): "Glascock",
    ("GA", "Lampkin"): "Lumpkin",
    ("GA", "Milton"): "GA:milton",
    ("IL", "Chicago"): "Cook",
    ("IL", "Cook, towns"): "Cook",
    ("IN", "Vanderburg"): "Vanderburgh",
    ("KS", "Davis"): "Geary",
    ("KS", "Howard"): "KS:howard",
    ("KS", "Waubonsee"): "Wabaunsee",
    ("KY", "Callaway"): "Calloway",
    ("KY", "Josh Bell"): "Bell",
    ("KY", "Metcalf"): "Metcalfe",
    ("KY", "Muhlenburg"): "Muhlenberg",
    ("LA", "Carroll"): "LA:carroll",
    ("MD", "Baltimore"): "24005",
    ("MD", "Baltimore City"): "24510",
    ("MD", "Hartford"): "Harford",
    ("MD", "Prince George"): "Prince George's",
    ("MI", "Leelanaw"): "Leelanau",
    ("MI", "Manitou"): "MI:manitou",
    ("MN", "Issanti"): "Isanti",
    ("MN", "Monongalia"): "MN:monongalia",
    ("MN", "Pembina"): "MN:pembina",
    ("MO", "Clarke"): "Clark",
    ("MO", "St. Louis"): "29189",
    ("MS", "Clark"): "Clarke",
    ("MS", "Colfax"): "Clay",
    ("MS", "Pearl"): "MS:pearl",
    ("NC", "Edgecomb"): "Edgecombe",
    ("NC", "Forsythe"): "Forsyth",
    ("NC", "Tyrrel"): "Tyrrell",
    ("OH", "Clarke"): "Clark",
    ("SC", "Spartanburgh"): "Spartanburg",
    ("SC", "Williamsburgh"): "Williamsburg",
    ("TN", "James"): "TN:james",
    ("VA", "Accomac"): "Accomack",
    ("VA", "Alexandria"): "51013",
    ("VA", "Alexandria City"): "51510",
    ("VA", "Bedford"): "51019",
    ("VA", "Elizabeth City"): "VA:elizabethcity",
    ("VA", "Fairfax"): "51059",
    ("VA", "Franklin"): "51067",
    ("VA", "Nansemond"): "VA:nansemond",
    ("VA", "Norfolk"): "VA:norfolkcounty",
    ("VA", "Norfolk City"): "51710",
    ("VA", "Pr. Edward"): "Prince Edward",
    ("VA", "Princess Anne"): "VA:princessanne",
    ("VA", "Richmond"): "51159",
    ("VA", "Richmond city"): "51760",
    ("VA", "Roanoke"): "51161",
    ("VA", "Spottsylvania"): "Spotsylvania",
    ("VA", "Warwick"): "VA:warwick",
}
FORMER = {
    ("CA", "klamath"): ("Klamath", "06023", date(1874, 3, 28)),
    ("KS", "howard"): ("Howard", "20049", date(1875, 3, 3)),
    ("LA", "carroll"): ("Carroll", "22035", date(1877, 3, 14)),
    ("MN", "pembina"): ("Pembina", "27069", date(1878, 2, 25)),
    ("MS", "pearl"): ("Pearl", "28109", date(1878, 2, 8)),
}
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
