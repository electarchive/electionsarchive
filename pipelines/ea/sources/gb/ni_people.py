import re
import unicodedata
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.ni_people"
VERSION = "1"
GAP = timedelta(days=6 * 366)
LEFT = DATA / "tmp" / "uk-dev" / "ni_people_left.tsv"


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.replace("'", "").replace("’", "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))


def emit() -> Path:
    with connect() as conn:
        rows = conn.execute(
            """select cy.person_id, coalesce(pn.text, cy.ballot_name), e.date_from, an.text,
                      (select text from name where entity_id = coalesce(cy.member_of_party_id, o.party_id) and is_preferred limit 1)
               from candidacy cy join contest_option o on o.id = cy.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join area_parent ap on ap.area_id = k.district_area_id
               join area_code pc on pc.area_id = ap.parent_id and pc.code = 'NIR'
               join name an on an.entity_id = k.district_area_id and an.is_preferred
               left join lateral (select text from name where entity_id = cy.person_id and is_preferred limit 1) pn on true
               where not exists (select from entity_merge m where m.entity_id = cy.person_id) and e.date_from >= date '1990-01-01'"""
        ).fetchall()
        created = dict(conn.execute("select id, created_at from entity where type = 'person'").fetchall())
        appearances: Counter[UUID] = Counter(person for person, *_ in rows)
        spelling: dict[UUID, int] = {}
        for person, name, *_ in rows:
            spelling[person] = max(spelling.get(person, 0), len(re.findall(r"[^A-Za-z .\-]", name or "")))
        groups: dict[tuple[str, str, str], list[tuple[object, UUID]]] = defaultdict(list)
        for person, name, day, area, party in rows:
            words = fold(re.sub(r"\(.*?\)", "", name or "")).split()
            if len(words) < 2 or party is None:
                continue
            owner = " ".join(re.sub(r"\b(?:of northern ireland|northern ireland|party|the|and)\b", "", fold(party)).split())
            groups[(f"{words[0]} {words[-1]}", fold(area), owner)].append((day, person))
        parent: dict[UUID, UUID] = {}

        def find(person: UUID) -> UUID:
            while parent.setdefault(person, person) != person:
                parent[person] = parent[parent[person]]
                person = parent[person]
            return person

        left = []
        for key, found in sorted(groups.items()):
            if len({person for _, person in found}) < 2:
                continue
            by_day: dict[object, set[UUID]] = defaultdict(set)
            for day, person in found:
                by_day[day].add(person)
            if any(len(people) > 1 for people in by_day.values()):
                left.append((key, "two people in one election"))
                continue
            days = sorted(by_day)
            for before, after in zip(days, days[1:]):
                if after - before > GAP:
                    left.append((key, f"gap between {before} and {after}"))
                else:
                    parent[find(next(iter(by_day[after])))] = find(next(iter(by_day[before])))
        components: dict[UUID, set[UUID]] = defaultdict(set)
        for person in list(parent):
            components[find(person)].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland candidates joined across elections and bodies on the same constituency, party, and name",
            polity="GB",
            kind="match",
        )
        merged = 0
        for people in components.values():
            if len(people) < 2:
                continue
            survivor = max(people, key=lambda person: (spelling[person], appearances[person], -created[person].timestamp()))
            for person in people - {survivor}:
                cs.merge(person, survivor)
                merged += 1
        LEFT.parent.mkdir(parents=True, exist_ok=True)
        LEFT.write_text("\n".join(f"{' | '.join(key)}\t{why}" for key, why in left) + "\n", encoding="utf-8")
        print(f"{merged} people merged into {sum(len(p) > 1 for p in components.values())} records, {len(left)} groups left ({LEFT})")
        return cs.write()
