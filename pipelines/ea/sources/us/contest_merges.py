from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.contest_merges"
VERSION = "1"
ELECTIONS = {"2016-11:ID:primary": "2016-05-17:ID:primary"}
OPTIONS = {"2016-11:ID:senate:5:rp:363818": "2016-05-17:ID:senate:5:rp:1984104a-862f-5ae9-bcfa-17f733bb61f6"}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge contests recorded twice under a misdated election into the contests of the real one: Klarner's November 2016 Idaho primary into the May 17 primary",
            polity="US",
            kind="correction",
        )

        def keyed(scheme: str, value: str) -> UUID:
            row = conn.execute(
                "select entity_id from external_id where scheme = %s and value = %s", [scheme, value]
            ).fetchone()
            assert row is not None, value
            return row[0]

        for old, new in ELECTIONS.items():
            duplicate, survivor = keyed("us.election", old), keyed("us.election", new)
            before, after = old.split(":")[0], new.split(":")[0]
            for contest, value in conn.execute(
                """select c.id, x.value from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where c.election_id = %s and x.value like %s""",
                [duplicate, f"{before}:%"],
            ).fetchall():
                row = conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value = %s",
                    [value.replace(before, after, 1)],
                ).fetchone()
                if row is not None and row[0] != contest:
                    cs.merge(contest, row[0])
            cs.merge(duplicate, survivor)
        for old, new in OPTIONS.items():
            cs.merge(keyed("us.option", old), keyed("us.option", new))
        return cs.write()
