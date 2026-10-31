from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.nh_redate"
VERSION = "1"
WRONG = date(2022, 9, 11)
RIGHT = date(2022, 9, 13)
RETIRED = "A New Hampshire primary dated to the wrong day, now held with the primary on its election day"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2022 county primaries moved from September 11 to their election day, September 13",
            polity="US",
        )
        parent = cs.key("us.election", f"{RIGHT}:NH:primary")
        misdated = cs.key("us.election", f"{WRONG}:NH:primary")
        rows = conn.execute(
            """select e.id, e.polity_id, e.kind::text, e.status::text, s.path
               from election e join slug s on s.entity_id = e.id and s.is_current
               where e.date_from = %s and e.parent_id = %s""",
            [WRONG, misdated],
        ).fetchall()
        for election, polity, kind, status, path in rows:
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind=kind,
                date_from=RIGHT,
                date_to=RIGHT,
                date_precision="day",
                status=status,
            )
            cs.add("slug", path=path.replace(str(WRONG), str(RIGHT)), entity_id=election)
            for contest, contest_path in conn.execute(
                "select c.id, s.path from contest c join slug s on s.entity_id = c.id and s.is_current where c.election_id = %s",
                [election],
            ).fetchall():
                cs.add("slug", path=contest_path.replace(str(WRONG), str(RIGHT)), entity_id=contest)
        cs.add("entity_retirement", entity_id=misdated, reason=RETIRED)
        return cs.write()
