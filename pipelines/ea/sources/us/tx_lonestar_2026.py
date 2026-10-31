from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import govcand
from ea.store import DATA, connect

SOURCE = "us.tx_lonestar_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://www.lonestar.edu/trustee-elections.htm"
FILE = DATA / "inbox" / "manual" / "MF-380" / "ExecutedOrderCancellationEnglish.pdf"
PUBLISHER = "Lone Star College System"
TITLE = "Order Declaring Unopposed Candidates Elected to Office and Canceling Elections for Districts 3, 4, and 9"
DISTRICT = "Lone Star College System"
RACES = {
    "District 3": ["Iesheia Ayers-Wilson"],
    "District 4": ["Art Murillo"],
    "District 9": ["Jim Cain"],
}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE, URL, FILE.read_bytes(), suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )


def parse() -> None:
    rows = [
        {
            "county": "Harris",
            "district": DISTRICT,
            "seat": seat,
            "role": "Trustee",
            "seats": 1,
            "name": name,
            "unexpired": False,
        }
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
            summary="Lone Star College System trustees declared elected without a contest for the canceled November 3, 2026 elections",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        kept = govcand.races(conn, cs, SOURCE, "TX", DAY, frame, source, "48", "tacit")
        print(f"{kept} candidates")
        return cs.write()
