from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ny_county_names"
VERSION = "1"
COLUMNS = ("id", "entity_id", "text", "lang", "script", "kind", "source_id")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York office and contest names that read County twice, corrected and the old name kept",
            polity=None,
        )
        for row in conn.execute(
            """select distinct n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id from name n
               join external_id x on x.entity_id = n.entity_id and x.scheme in ('us.office', 'us.contest')
               where n.is_preferred and n.text ~ '\\mCounty County\\M' and x.value ~ '(^|:)NY:'
               order by n.id"""
        ).fetchall():
            cs.add("name", **dict(zip(COLUMNS, row, strict=True)), is_preferred=False)
            cs.add(
                "name",
                id=None,
                entity_id=row[1],
                text=row[2].replace("County County", "County"),
                lang=row[3],
                script=row[4],
                kind=row[5],
                source_id=row[6],
                is_preferred=True,
            )
        return cs.write()
