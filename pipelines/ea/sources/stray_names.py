from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "stray_names"
VERSION = "1"
FUSION = "a fusion ballot line, kept on the option's ballot label; merging the label into the party carried it over"
DROPS = [
    ("us", "Republican Party", "Republican/Democratic", FUSION),
    ("us", "Republican Party", "Democratic and Republican", FUSION),
    ("us", "Republican Party", "Republican (Write-In)", FUSION),
]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Drop names that sit on the wrong entity, keeping the entity",
            polity=None,
            kind="correction",
        )
        for root, preferred, text, reason in DROPS:
            found = conn.execute(
                """select d.id, d.entity_id from name d
                   join name p on p.entity_id = d.entity_id and p.is_preferred
                   join party y on y.id = d.entity_id
                   join slug s on s.entity_id = y.polity_id and s.is_current
                   where d.text = %s and p.text = %s and s.path = %s and not d.is_preferred""",
                [text, preferred, root],
            ).fetchall()
            for name, entity in found:
                cs.add("name_drop", name_id=name, entity_id=entity, text=text, reason=reason)
                print(f"{root} {preferred}: {text}")
        return cs.write()
