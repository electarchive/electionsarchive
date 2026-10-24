from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "stray_names"
VERSION = "2"
FUSION = "a fusion ballot line, kept on the option's ballot label; merging the label into the party carried it over"
DROPS = [
    ("us", "Republican Party", "Republican/Democratic", FUSION),
    ("us", "Republican Party", "Democratic and Republican", FUSION),
    ("us", "Republican Party", "Republican (Write-In)", FUSION),
]
PEOPLE = [
    (
        "us.thea.person",
        r"%[a-z]+<| (Dr|Col|Gen|Capt|Maj|Major|Judge|Rev|Hon) [^ ]+$|^(Dr|Col|Gen|Capt|Maj|Rev|Hon) ",
        "THEA's honorific (printed as %dr< or in parentheses) read as part of the name",
    ),
    ("us.klarner.person", r"^([A-Z][a-z]+) \1 ", "Klarner's middle name copied into the first-name field"),
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
        for scheme, pattern, reason in PEOPLE:
            for name, entity, text in conn.execute(
                """select distinct d.id, d.entity_id, d.text from name d
                   join changeset c on c.id = d.changeset_id and c.pipeline = %s
                   join name p on p.entity_id = d.entity_id and p.is_preferred
                   where d.text ~ %s and p.text !~ %s and not d.is_preferred
                     and exists (select from external_id x where x.entity_id = d.entity_id and x.scheme = %s)""",
                [scheme.rsplit(".", 1)[0], pattern, pattern, scheme],
            ).fetchall():
                cs.add("name_drop", name_id=name, entity_id=entity, text=text, reason=reason)
                print(f"{scheme}: {text}")
        return cs.write()
