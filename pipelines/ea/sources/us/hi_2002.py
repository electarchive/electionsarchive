import subprocess
from datetime import date
from pathlib import Path

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.hi_2002"
VERSION = "1"
PAGES = DATA / "pages" / SOURCE
BASE = "https://files.hawaii.gov/elections/files/results/2002/primary/"
DAY = date(2002, 9, 21)
PUBLISHER = "Hawaii Office of Elections"
TITLE = "Primary election 2002 summary reports by county"
COUNTIES = {"coh": "15001", "cch": "15003", "cok": "15007", "com": "15009"}
PARTIES = {"NL": "natural-law", "R": "republican", "D": "democratic", "L": "libertarian"}
READS: dict[str, list[tuple[str, str, int, int]]] = {
    "coh": [
        ("NL", "BEDWORTH, NICHOLAS", 47, 290),
        ("R", "MCDERMOTT, BOB", 4150, 435),
        ("R", "DONOVAN, JAMES M. (THE UMP)", 2182, 456),
        ("D", "MINK, PATSY TAKEMOTO", 18549, 598),
        ("D", "TATAII, STEVE", 3993, 623),
        ("L", "MALLAN, JEFF", 30, 765),
        ("L", "DUQUESNE, LAWRENCE (LEHR)", 27, 788),
    ],
    "cch": [
        ("NL", "BEDWORTH, NICHOLAS", 88, 788),
        ("R", "MCDERMOTT, BOB", 10838, 932),
        ("R", "DONOVAN, JAMES M. (THE UMP)", 3340, 953),
        ("D", "MINK, PATSY TAKEMOTO", 23803, 1098),
        ("D", "TATAII, STEVE", 6329, 1118),
        ("L", "MALLAN, JEFF", 28, 1262),
        ("L", "DUQUESNE, LAWRENCE (LEHR)", 19, 1285),
    ],
    "cok": [
        ("NL", "BEDWORTH, NICHOLAS", 18, 290),
        ("R", "MCDERMOTT, BOB", 1204, 435),
        ("R", "DONOVAN, JAMES M. (THE UMP)", 638, 456),
        ("D", "MINK, PATSY TAKEMOTO", 10613, 602),
        ("D", "TATAII, STEVE", 1517, 622),
        ("L", "MALLAN, JEFF", 11, 767),
        ("L", "DUQUESNE, LAWRENCE (LEHR)", 6, 788),
    ],
    "com": [
        ("NL", "BEDWORTH, NICHOLAS", 79, 295),
        ("R", "MCDERMOTT, BOB", 3988, 438),
        ("R", "DONOVAN, JAMES M. (THE UMP)", 1788, 459),
        ("D", "MINK, PATSY TAKEMOTO", 14281, 605),
        ("D", "TATAII, STEVE", 2339, 625),
        ("L", "MALLAN, JEFF", 26, 770),
        ("L", "DUQUESNE, LAWRENCE (LEHR)", 19, 790),
    ],
}


def fetch() -> None:
    for county in COUNTIES:
        raw.fetch(SOURCE, f"{BASE}{county}.pdf", suffix=".pdf")


def emit() -> Path:
    PAGES.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's 2002 primary U.S. House 2 results by county, keyed from the Office of Elections' county summary scans and checked against the held totals",
            polity="US-HI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.hawaii.gov/resources/election-results/",
            license="Public record",
        )
        rows = []
        for county, reads in READS.items():
            stored = raw.cached(SOURCE, f"{BASE}{county}.pdf")
            file = cs.artifact(stored, source)
            image = PAGES / f"{stored.sha256}-1.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-f", "1", "-l", "1", "-png", "-singlefile", str(stored.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = Image.open(image).size
            page = cs.page(file, 1)
            cs.add(
                "source_page",
                id=page,
                artifact_id=file,
                page_number=1,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            rows.extend(
                {
                    "year": 2002,
                    "chamber": f"house:{PARTIES[party]}",
                    "day": DAY,
                    "special": False,
                    "state": "HI",
                    "district": 2,
                    "code": COUNTIES[county],
                    "candidate": candidate,
                    "measure": 1,
                    "writein": False,
                    "votes": votes,
                    "kind": "county",
                    "source": "HI",
                    "file": 0,
                    "page": 1,
                    "page_id": str(page),
                    "x0": 0.18,
                    "y0": (y - 11) / 2199,
                    "x1": 0.27,
                    "y1": (y + 11) / 2199,
                }
                for party, candidate, votes, y in reads
            )
        frame = pl.DataFrame(rows).with_columns(
            year=pl.col("year").cast(pl.Int32), district=pl.col("district").cast(pl.Int16), measure=pl.col("measure").cast(pl.Int16)
        )
        medsl.attach(cs, conn, frame, {"HI": source}, strict=True)
        return cs.write()
