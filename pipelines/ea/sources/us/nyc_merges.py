from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.nyc_merges"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge MEDSL's 2024 Queens County Surrogate contest and its county election into the Board of Elections' copies under New York City's 2024 general election",
            polity="US",
            kind="correction",
        )
        for scheme, old, new in (
            ("us.contest", "2024-11-05:NY:36081:surrogate", "2024-11-05:new-york-city:surrogate-queens"),
            ("us.election", "2024-11-05:NY:36081", "2024-11-05:NY:3651000"),
        ):
            ids = [
                conn.execute("select entity_id from external_id where scheme = %s and value = %s", [scheme, value]).fetchone()[0]
                for value in (old, new)
            ]
            cs.merge(*ids)
        return cs.write()
