import re
import unicodedata
from collections import Counter
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "ca.match"
VERSION = "6"
PAIRS = (
    ("2006-01-23", "2004-06-28"),
    ("2008-10-14", "2006-01-23"),
    ("2011-05-02", "2008-10-14"),
    ("2015-10-19", "2011-05-02"),
    ("2019-10-21", "2015-10-19"),
    ("2021-09-20", "2019-10-21"),
    ("2025-04-28", "2021-09-20"),
)
SEATED = "2000-11-27"
FIRST_POLL = "2004-06-28"


def emit() -> Path:
    with connect() as conn:
        candidacies: dict[UUID, int] = dict(
            conn.execute("select person_id, count(*) from candidacy group by person_id").fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Candidates matched across consecutive Canadian elections by printed name, ignoring middle initials, and overlapping ridings, and 2004 incumbents matched with members of the 37th Parliament",
            polity="CA",
            kind="match",
        )
        parent: dict[UUID, UUID] = {}

        def root(node: UUID) -> UUID:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for later, earlier in PAIRS:
            pairs: list[tuple[UUID, UUID, bool]] = conn.execute(
                """with c as (
                         select x.person_id, x.is_incumbent, e.date_from as day, g.geom,
                                trim(regexp_replace(regexp_replace(regexp_replace(lower(x.ballot_name), '[.,]', ' ', 'g'),
                                     '(?<=\\s)\\w(?=\\s|$)', '', 'g'), '\\s+', ' ', 'g')) as printed,
                                exists (select from outcome w join result_set r on r.id = w.result_set_id
                                        where w.option_id = x.option_id and r.is_default and w.kind = 'elected') as won
                         from candidacy x
                         join contest_option o on o.id = x.option_id
                         join contest k on k.id = o.contest_id
                         join election e on e.id = k.election_id
                         join area_geometry g on g.area_id = k.district_area_id and g.valid @> e.date_from
                         where e.polity_id = (select id from polity where iso_code = 'CA')
                           and e.date_from in (%s, %s)
                       )
                       select l.person_id, r.person_id, l.is_incumbent and r.won
                       from c l join c r on r.printed = l.printed and l.day = %s and r.day = %s
                       where st_intersects(l.geom, r.geom)
                         and st_area(st_intersection(l.geom, r.geom)::geography)
                             > 0.01 * least(st_area(l.geom::geography), st_area(r.geom::geography))""",
                [later, earlier, later, earlier],
            ).fetchall()
            sides = Counter(side for person, other, _ in pairs for side in ((0, person), (1, other)))
            held = Counter(side for person, other, seat in pairs if seat for side in ((0, person), (1, other)))
            matched = 0
            for person, other, seat in pairs:
                if person != other and (
                    sides[0, person] == sides[1, other] == 1 or (seat and held[0, person] == held[1, other] == 1)
                ):
                    parent[root(person)] = root(other)
                    matched += 1
            print(f"{later} with {earlier}: {len(pairs)} pairs, {matched} matched")

        def words(text: str) -> list[str]:
            folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
            return [word for word in re.split(r"[^a-z]+", folded) if len(word) > 1]

        rows: list[tuple[UUID, str, str, bool]] = conn.execute(
            """select cy.person_id, cy.ballot_name, n.text, e.date_from = %s
               from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join changeset c on c.id = k.changeset_id
               join name n on n.entity_id = k.district_area_id and n.is_preferred and n.lang = 'en'
               where e.polity_id = (select id from polity where iso_code = 'CA')
                 and ((e.date_from = %s and cy.is_incumbent and c.pipeline = 'ca.elections')
                   or (e.date_from >= %s and e.date_from < %s and c.pipeline = 'ca.lop'
                       and exists (select from outcome w join result_set r on r.id = w.result_set_id
                                   where w.option_id = o.id and r.is_default and w.kind = 'elected')))""",
            [FIRST_POLL, FIRST_POLL, SEATED, FIRST_POLL],
        ).fetchall()
        members = [(person, words(name), set(words(riding))) for person, name, riding, later in rows if not later]
        found: list[tuple[UUID, UUID]] = []
        for person, name, riding, later in rows:
            if not later:
                continue
            *given, surname = words(name)
            shared = set(words(riding))
            near = [
                (member, bool(set(given) & set(printed[:-1])), bool(shared & area))
                for member, printed, area in members
                if printed[-1] == surname and (set(given) & set(printed[:-1]) or shared & area)
            ]
            for index in (1, 2):
                near = [pair for pair in near if pair[index]] if len(near) > 1 else near
            if len(near) == 1:
                found.append((person, near[0][0]))
        sides = Counter(side for person, member in found for side in ((0, person), (1, member)))
        matched = 0
        for person, member in found:
            if sides[0, person] == sides[1, member] == 1 and root(person) != root(member):
                parent[root(person)] = root(member)
                matched += 1
        print(f"{FIRST_POLL} incumbents with members seated from {SEATED}: {len(found)} pairs, {matched} matched")

        groups: dict[UUID, list[UUID]] = {}
        for node in list(parent):
            groups.setdefault(root(node), []).append(node)
        for people in groups.values():
            survivor = max(people, key=lambda person: (candidacies.get(person, 0), -person.int))
            for person in people:
                if person != survivor:
                    cs.add("entity_merge", entity_id=person, into_id=survivor)
        return cs.write()
