from pathlib import Path
from uuid import uuid7

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "wikidata_photos_out"
VERSION = "1"
REASON = "Withdrawn: the file's license is not public domain, CC0, CC BY, CC BY-SA, OGL, or Licence Ouverte"
ALLOWED = r"^(Public domain|CC0|CC BY(-SA)? [0-9.]+|OGL|Licence Ouverte)"


def emit() -> Path | None:
    with connect() as conn:
        names = [d.name for d in conn.execute("select * from person_image limit 0").description or []]
        columns = [c for c in names if c not in ("changeset_id", "recorded_from")]
        rows = conn.execute("select * from person_image where license !~ %s order by id", [ALLOWED]).fetchall()
        if not rows:
            print("no person_image rows with a disallowed license remain")
            return None
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Withdraw {len(rows)} portraits whose Commons license is not one the archive accepts",
            polity=None,
        )
        for row in rows:
            record = dict(zip(names, row))
            cs.add(
                "row_drop",
                id=uuid7(),
                reason=REASON,
                relation="person_image",
                key={
                    c: record[c] if record[c] is None or isinstance(record[c], (int, str)) else str(record[c])
                    for c in columns
                },
            )
        return cs.write()
