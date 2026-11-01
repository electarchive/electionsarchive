from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.enr_clarity_twins"
VERSION = "1"
OWNER = "01a16cae-7d73-773b-8c80-43cf8df218f8"
REASON = (
    "Township board contest written twice: us.enr_clarity filed it as board-of-trustees, and "
    "us.clarity_local already holds it as township-board with the same area, title, and candidates"
)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire township board contests that us.enr_clarity wrote under a second key",
            polity="US",
            kind="correction",
        )
        contests = [
            row[0]
            for row in conn.execute(
                """select x.entity_id from external_id x
                   where x.scheme = 'us.contest' and x.changeset_id = %s
                     and x.value like '%%:board-of-trustees'
                     and exists (
                       select 1 from external_id y
                       where y.scheme = 'us.contest' and y.changeset_id <> x.changeset_id
                         and y.value = replace(x.value, ':board-of-trustees', ':township-board'))""",
                [OWNER],
            ).fetchall()
        ]
        for contest in contests:
            cs.add("entity_retirement", entity_id=contest, reason=REASON)
        print(f"{len(contests)} contests")
        return cs.write()
