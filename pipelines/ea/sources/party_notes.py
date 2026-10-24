from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "party_notes"
VERSION = "1"
NOTES = [
    (
        "us",
        "Republican Party",
        date(1862, 1, 1),
        date(1867, 1, 1),
        (
            "From 1862 to 1866, many Republican-aligned candidates ran on state Union and Union Republican tickets. "
            "The archive records those labels as separate parties, so the chart does not count their votes or seats "
            "as Republican."
        ),
    ),
]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Caveats on party pages for years a party's chart undercounts it",
            polity=None,
        )
        for root, text, start, end, body in NOTES:
            found = conn.execute(
                """select p.id from party p join name n on n.entity_id = p.id and n.is_preferred
                   join slug s on s.entity_id = p.polity_id and s.is_current
                   where n.text = %s and s.path = %s
                     and not exists (select from entity_merge m where m.entity_id = p.id)""",
                [text, root],
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"{root} {text}: {len(found)} parties")
            party = found[0][0]
            held = conn.execute(
                """select n.id from note n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and c.pipeline = %s and n.valid_from = %s""",
                [party, SOURCE, start],
            ).fetchone()
            cs.add(
                "note",
                id=None if held is None else held[0],
                entity_id=party,
                kind="caveat",
                body_md=body,
                valid_from=start,
                valid_from_precision="year",
                valid_to=end,
                valid_to_precision="year",
            )
        return cs.write()
