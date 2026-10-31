from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ri_place_out"
VERSION = "1"
REASON = "Duplicate of the town's own contest: Rhode Island towns are recorded under the town polity, not a place polity"
SLUGS = ("us/ri/charlestown-place", "us/ri/hopkinton-place")


def emit() -> Path | None:
    with connect() as conn:
        rows = conn.execute(
            """select s.entity_id, e.type::text from slug s join entity e on e.id = s.entity_id
               where s.is_current and e.retired_by is null
                 and (s.path like any(%s) or s.path = any(%s))""",
            [[f"{path}/%" for path in SLUGS], list(SLUGS)],
        ).fetchall()
        if not rows:
            print("the place-polity duplicates are already retired")
            return None
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire duplicate Charlestown and Hopkinton ballot measure contests recorded under place polities",
            polity="US",
        )
        for entity, kind in sorted(rows, key=lambda row: ("contest", "election", "polity").index(row[1])):
            cs.add("entity_retirement", entity_id=entity, reason=REASON)
        return cs.write()
