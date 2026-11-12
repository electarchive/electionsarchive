from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_runoff_people_merge"
VERSION = "1"

PAIRS = (
    ("3da61869-fcb4-5888-a5b9-eac1b0078270", "03330df0-a484-55f6-b63d-5ca13a452860"),
    ("9eaf00ed-144c-592c-b571-652dd33b112b", "152100e3-3a8f-5bf4-a4d7-79d5167f0532"),
)


def parse() -> None:
    return None


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Merge the records of John McCann (Chula Vista council District 1, then mayor) and Fred Shorett "
                "(San Bernardino council District 4) that the 2022 and 2026 mayor and council loads minted apart "
                "from the same candidates' earlier council races"
            ),
            polity="US-CA",
        )
        for duplicate, survivor in PAIRS:
            cs.merge(duplicate, survivor)
        return cs.write()
