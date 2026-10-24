from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.commons_size"
VERSION = "1"
PAGE = "https://commonslibrary.parliament.uk/research-briefings/sn02384/"
URL = "https://web.archive.org/web/2024/https://researchbriefings.files.parliament.uk/documents/SN02384/SN02384.pdf"
SIZES: list[tuple[date, str, int]] = [
    (date(1801, 1, 1), "day", 658),
    (date(1844, 1, 1), "year", 656),
    (date(1852, 1, 1), "year", 654),
    (date(1861, 1, 1), "year", 656),
    (date(1865, 1, 1), "year", 658),
    (date(1870, 1, 1), "year", 652),
    (date(1885, 1, 1), "year", 648),
    (date(1885, 11, 24), "day", 670),
    (date(1918, 12, 14), "day", 707),
    (date(1922, 11, 15), "day", 615),
    (date(1945, 7, 5), "day", 640),
    (date(1950, 2, 23), "day", 625),
    (date(1955, 5, 26), "day", 630),
    (date(1974, 2, 28), "day", 635),
    (date(1983, 6, 9), "day", 650),
    (date(1992, 4, 9), "day", 651),
    (date(1997, 5, 1), "day", 659),
    (date(2005, 5, 5), "day", 646),
    (date(2010, 5, 6), "day", 650),
]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Seats in the House of Commons from the Union with Ireland in 1801 to the present, from the House of Commons Library",
            polity="GB",
        )
        commons = cs.key("gb.body", "house-of-commons")
        if commons in cs.minted:
            raise ValueError("the House of Commons must already exist")
        library = cs.source("House of Commons Library", "Number of Seats in the House of Commons since 1707")
        cs.add(
            "source",
            id=library,
            kind="official_body",
            publisher="House of Commons Library",
            title="Number of Seats in the House of Commons since 1707",
            url=PAGE,
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
        )
        cs.artifact(artifact, library)
        for index, (start, precision, seats) in enumerate(SIZES):
            end = SIZES[index + 1][0] if index + 1 < len(SIZES) else None
            cs.add(
                "body_size",
                body_id=commons,
                seats=seats,
                valid_from=start,
                valid_from_precision=precision,
                valid_to=end,
                valid_to_precision=SIZES[index + 1][1] if end is not None else "day",
            )
        return cs.write()
