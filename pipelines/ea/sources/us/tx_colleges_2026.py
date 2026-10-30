from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import govcand
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.tx_colleges_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://hayscountytx.gov/DocumentCenter/View/5221/Austin-Community-College---Notice-of-General-Trustee-Election"
FILE = DATA / "inbox" / "manual" / "MF-380" / "Austin Community College - Notice of General Trustee Election.pdf"
PUBLISHER = "Austin Community College District"
TITLE = "Notice of General Trustee Election, November 3, 2026"
DISTRICT = "Austin Community College District"
RACES = {
    "Place 1": ["Dana Walker", "Gloria Gonzales-Dholakia"],
    "Place 2": ["Gigi Edwards Bryant"],
    "Place 3": ["Trevor Hale", "Nan McRaven"],
}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(SOURCE, URL, FILE.read_bytes(), suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    rows = [
        {"county": "Travis", "district": DISTRICT, "seat": seat, "role": "Trustee", "seats": 1, "name": name, "unexpired": False}
        for seat, names in RACES.items()
        for name in names
    ]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")
    print(len(rows))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Austin Community College District trustee candidates on the November 3, 2026 ballot",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record")
        cs.artifact(raw.cached(SOURCE, URL), source)
        kept = govcand.races(conn, cs, SOURCE, "TX", DAY, frame, source, "48")
        print(f"{kept} candidates")
        return cs.write()
