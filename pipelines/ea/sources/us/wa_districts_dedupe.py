import re
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.wa_districts_dedupe"
VERSION = "1"
DAY = "2026-11-03"
ADOPTED = {
    "special:53037:district:kittitas-county-public-utility-district": "53037:utility:1",
    "special:53049:district:pacific-county-public-utility-district": "53049:utility:2",
}


def fold(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\bno\.? ?(?=\d)", "", name.lower()))


def parse() -> None:
    return None


def emit() -> Path:
    with connect() as conn:
        rows = conn.execute(
            """select x.value, p.id, n.text, pp.parent_id, ac.code, t.area_id
               from polity p
               join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
               join name n on n.entity_id = p.id and n.is_preferred
               join polity_parent pp on pp.polity_id = p.id
               join polity_territory t on t.polity_id = p.id
               join area_code ac on ac.area_id = t.area_id
               where p.kind = 'special_district' and x.value like 'special:53%%'"""
        ).fetchall()
        elections = dict(
            conn.execute(
                "select polity_id, id from election where date_from = %s and polity_id = any(%s)",
                [DAY, [row[1] for row in rows]],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire Washington special districts minted twice by the November 3, 2026 candidate load into the districts already held",
            polity="US-WA",
        )
        mine = [
            row
            for row in rows
            if re.fullmatch(r"special:\d+:(district:.+|port:pasco|port:bellingham|fire-protection:rimrock.+)", row[0])
        ]
        held = {row[4]: row for row in rows}
        for value, polity, name, parent, code, area in mine:
            target = held.get(ADOPTED.get(value, ""))
            if target is None:
                target = next(
                    (
                        row
                        for row in rows
                        if row[3] == parent and row[1] != polity and row not in mine and fold(row[2]) == fold(name)
                    ),
                    None,
                )
            if target is None:
                continue
            print(name, "->", target[2], target[4])
            cs.merge(polity, target[1])
            cs.merge(area, target[5])
            cs.merge(cs.unit("us.special_district", code), cs.unit("us.special_district", target[4]))
            if target[1] in elections and polity in elections:
                cs.merge(elections[polity], elections[target[1]])
        return cs.write()
