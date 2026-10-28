from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "stray_names"
VERSION = "4"
FUSION = "a fusion ballot line, kept on the option's ballot label; merging the label into the party carried it over"
DROPS = [
    ("us", "Republican Party", "Republican/Democratic", FUSION),
    ("us", "Republican Party", "Democratic and Republican", FUSION),
    ("us", "Republican Party", "Republican (Write-In)", FUSION),
]
PERSON_NAMES = [
    (
        "01a13c0c-c7c6-700f-98ec-39090d2d9638",
        "2023 Turkish presidential runoff",
        "the runoff election is named second round, as France and Brazil are",
    ),
    (
        "01a13c0c-c7ca-71cd-8444-76c36286aee3",
        "2023 Turkish presidential election, runoff",
        "the runoff contest is named second round, as France and Brazil are",
    ),
    (
        "5454d003-f584-5e5c-8fcb-a356a9b69faa",
        "Cellucci",
        "a short name derived from the wrong surname before the person's names were repaired",
    ),
    (
        "3e625f6e-1b10-517f-a021-9386a01635fa",
        "Marshall J.P./.",
        "a stray slash and period from the source's name field, kept beside the repaired name",
    ),
    (
        "ae09ec56-1de6-5c5b-8bb6-4c87a5ebd43b",
        "Margaret Sandra (<i>no surname</i>)",
        "the source's markup note kept as part of the name, beside the repaired name",
    ),
    ("c121ae31-3594-5b7b-8e3d-00c50773f615", "W/I", "the write-in ballot label derived as a short name"),
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
        for entity, text, reason in PERSON_NAMES:
            for (name,) in conn.execute(
                "select id from name where entity_id = %s and text = %s and not is_preferred", [entity, text]
            ).fetchall():
                cs.add("name_drop", name_id=name, entity_id=entity, text=text, reason=reason)
                print(f"person {entity}: {text}")
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
