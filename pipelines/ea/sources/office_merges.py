from pathlib import Path

from ea.changeset import Changeset
from ea.offices import TWINS
from ea.store import connect

SOURCE = "office_merges"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "One office recorded twice, by two pipelines that key it differently or by one whose key changed, "
                "merged into the record that carries the most contests, tenures, and governments"
            ),
            polity=None,
            kind="correction",
        )
        groups = 0
        for ranked in conn.execute(TWINS).fetchall():
            survivor, *duplicates = ranked[0]
            groups += 1
            for duplicate in duplicates:
                cs.merge(duplicate, survivor)
        print(f"{groups} duplicate office groups")
        return cs.write()
