import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.ni_names"
VERSION = "1"


def fold(text: str) -> str:
    plain = (
        unicodedata.normalize("NFKD", text.replace("'", "").replace("\u2019", ""))
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))


def emit() -> Path:
    with connect() as conn:
        live = [
            person
            for (person,) in conn.execute(
                """select distinct cy.person_id from candidacy cy join contest_option o on o.id = cy.option_id
                   join contest k on k.id = o.contest_id
                   join area_parent ap on ap.area_id = k.district_area_id
                   join area_code pc on pc.area_id = ap.parent_id and pc.code = 'NIR'
                   where not exists (select from entity_merge m where m.entity_id = cy.person_id)"""
            ).fetchall()
        ]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland candidates keep the best spelling of each name; poorer spellings of the same name are dropped",
            polity="GB",
            kind="correction",
        )
        spellings: dict[UUID, dict[str, list[tuple[UUID, str, bool, str]]]] = defaultdict(lambda: defaultdict(list))
        for name_id, person, text, preferred, kind in conn.execute(
            "select id, entity_id, text, is_preferred, kind from name where entity_id = any(%s)", [live]
        ).fetchall():
            spellings[person][fold(text)].append((name_id, text, preferred, kind))
        dropped = 0
        for person, variants in spellings.items():
            for found in variants.values():
                best = max(
                    found,
                    key=lambda row: (
                        len(re.findall(r"[^\x00-\x7f']|'", row[1]))
                        - sum(len(word) > 1 and word.isupper() for word in row[1].split())
                        - len(re.findall(r"\b[A-Z]\.[a-z]\b", row[1])),
                        row[2],
                    ),
                )
                losers = [row for row in found if row[1] != best[1]]
                if not losers:
                    continue
                promote = any(row[2] for row in losers) and not best[2]
                for name_id, text, *_ in losers:
                    cs.add(
                        "name_drop",
                        name_id=name_id,
                        entity_id=person,
                        text=text,
                        reason="a poorer spelling of the same name",
                    )
                    dropped += 1
                if promote:
                    for name_id, text, *_ in [row for row in found if row[1] == best[1]]:
                        cs.add(
                            "name_drop",
                            name_id=name_id,
                            entity_id=person,
                            text=text,
                            reason="re-added as the preferred name",
                        )
                    cs.add("name", entity_id=person, text=best[1], lang="en", kind=best[3], is_preferred=True)
        print(f"{dropped} poorer spellings dropped")
        return cs.write()
