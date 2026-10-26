import re
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ny_county_names"
VERSION = "2"
COLUMNS = ("id", "entity_id", "text", "lang", "script", "kind", "source_id")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York office, contest, and city names with a doubled County or a Census City suffix, corrected and the old name kept",
            polity=None,
        )
        for row in conn.execute(
            """select distinct n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id from name n
               join external_id x on x.entity_id = n.entity_id and x.scheme in ('us.office', 'us.contest')
               where n.is_preferred and n.text ~ '\\mCounty County\\M' and x.value ~ '(^|:)NY:'
               union
               select n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id from name n
               join polity p on p.id = n.entity_id and p.kind = 'municipality'
               join polity_territory t on t.polity_id = p.id
               join area_code c on c.area_id = t.area_id and c.code like '36%'
               where n.is_preferred and n.text ~ '^.+ City$' and n.text <> 'New York City'
               union
               select n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id from name n
               join polity_territory t on t.area_id = n.entity_id
               join polity p on p.id = t.polity_id and p.kind = 'municipality'
               join area_code c on c.area_id = t.area_id and c.code like '36%'
               where n.is_preferred and n.text ~ '^[^(]+ City, NY$' and n.text <> 'New York City, NY'
               order by 1"""
        ).fetchall():
            cs.add("name", **dict(zip(COLUMNS, row, strict=True)), is_preferred=False)
            cs.add(
                "name",
                id=None,
                entity_id=row[1],
                text=re.sub(r" City(?=,|$)", "", row[2].replace("County County", "County")),
                lang=row[3],
                script=row[4],
                kind=row[5],
                source_id=row[6],
                is_preferred=True,
            )
        return cs.write()
