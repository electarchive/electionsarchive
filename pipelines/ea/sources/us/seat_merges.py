import re
import unicodedata
from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.sources.us.wikidata_links import NICKNAMES
from ea.store import connect

SOURCE = "us.seat_merges"
VERSION = "3"
NEAR = 12 * 366
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
STRONG = ("bioguide", "wikidata", "fec.candidate")
LOCAL = r"^[0-9-]+:(alged:|[A-Z]{2}:[0-9]{4,}:)"
PLACEHOLDERS = re.compile(
    r"write|scatter|blank|\bover|\bunder|vacan|none of|no candidate|total|^yes$|^no$|uncommitted|^other"
)
CANON = {"rudy": "rudolph", "rudi": "rudolph"} | {
    form: first
    for first, forms in NICKNAMES.items()
    for form in forms.split()
    if form not in {"kathleen", "jon", "bert"}
}

type Name = tuple[tuple[str, ...], frozenset[str]]


def parts(text: str) -> Name | None:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    if PLACEHOLDERS.search(folded.strip()):
        return None
    bare = re.sub(r"\([^)]*\)|\"[^\"]*\"|“[^”]*”|(?<![a-z])'+[^']*'+(?![a-z])", " ", folded)
    folded = bare if len(re.findall(r"[a-z]+", bare)) > 1 else folded
    if "," in folded:
        head, _, rest = folded.partition(",")
        given = [word for word in re.split(r"[^a-z]+", rest) if word]
        if given and not all(word in SUFFIXES for word in given):
            folded = f"{rest} {head}"
    folded = re.sub(r"(?<=[a-z])[-'’](?=[a-z])", "", folded)
    found = [word for word in re.split(r"[^a-z]+", folded) if word]
    words = tuple(word for word in found if word not in SUFFIXES)
    if len(words) < 2 or len(words[-1]) < 2:
        return None
    return words, frozenset(word for word in found if word in SUFFIXES)


def fits(one: str, two: str) -> bool:
    short, long = sorted((one, two), key=len)
    added = long[len(short) :]
    return (
        one == two
        or CANON.get(one, one) == CANON.get(two, two)
        or long.startswith(short)
        and (len(short) == 1 or len(short) >= 3 and (len(added) >= 3 or added == short[-1]))
    )


def same(one: Name, two: Name) -> bool:
    (left, left_suffixes), (right, right_suffixes) = one, two
    if left_suffixes == right_suffixes and "".join(left) == "".join(right):
        return True
    if left[-1] != right[-1] or left_suffixes != right_suffixes or not fits(left[0], right[0]):
        return False
    middle, other = left[1:-1], right[1:-1]
    return not (middle and other) or len(middle) == len(other) and all(map(fits, middle, other))


def emit() -> Path:
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        rows: list[tuple[str, date, UUID, UUID, UUID, str | None, str | None, str]] = conn.execute(
            """select regexp_replace(x.value, '^[0-9-]+:', ''), el.date_from, co.id, o.id, ca.person_id,
                      coalesce(ca.ballot_name, o.ballot_label), pn.text, pc.pipeline
               from external_id x join contest co on co.id = x.entity_id
               join entity ce on ce.id = co.id and ce.retired_by is null
               join election el on el.id = co.election_id
               join contest_option o on o.contest_id = co.id
               join candidacy ca on ca.option_id = o.id join entity e on e.id = ca.id and e.retired_by is null
               join entity pe on pe.id = ca.person_id and pe.retired_by is null
               join changeset pc on pc.id = pe.changeset_id
               left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
               where x.scheme = 'us.contest' and x.value ~ %s
                 and not exists (select from entity_merge m where m.entity_id = ca.person_id)""",
            [LOCAL],
        ).fetchall()
        held: dict[UUID, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for person, scheme, value in conn.execute(
            "select entity_id, scheme, value from external_id where scheme = any(%s)", [list(STRONG)]
        ).fetchall():
            held[person][scheme].add(value)
        ran = dict(
            conn.execute(
                """select ca.person_id, count(*) from candidacy ca join entity e on e.id = ca.id and e.retired_by is null
                   group by ca.person_id"""
            ).fetchall()
        )
        created = dict(conn.execute("select id, created_at from entity where type = 'person'").fetchall())

        seats: dict[str, dict[UUID, list[tuple[Name, date, UUID, UUID]]]] = defaultdict(lambda: defaultdict(list))
        minted: dict[UUID, str] = {}
        for tail, day, contest, option, person, ballot, preferred, pipeline in rows:
            minted[person] = pipeline
            for text in {text for text in (ballot, preferred) if text}:
                named = parts(text)
                if named is not None:
                    seats[tail][person].append((named, day, contest, option))
        pairs: list[tuple[UUID, UUID, list[tuple[UUID, UUID, UUID]]]] = []
        for people in seats.values():
            by_surname: dict[str, set[UUID]] = defaultdict(set)
            for person, records in people.items():
                for record in records:
                    by_surname[record[0][0][-1]].add(person)
                    by_surname["".join(record[0][0])].add(person)
            for group in by_surname.values():
                for one, two in combinations(sorted(group), 2):
                    if minted[one] == minted[two]:
                        continue
                    left, right = people[one], people[two]
                    if not all(same(a[0], b[0]) for a in left for b in right):
                        continue
                    if min(abs((a[1] - b[1]).days) for a in left for b in right) > NEAR:
                        continue
                    shared = [(a[2], a[3], b[3]) for a in left for b in right if a[2] == b[2] and a[3] != b[3]]
                    pairs.append((one, two, shared))

        ballots = {(contest, a, b) for _, _, shared in pairs for contest, a, b in shared}
        rivals: set[tuple[UUID, UUID]] = set()
        if ballots:
            for a, b in conn.execute(
                """with pair(contest, a, b) as (select * from unnest(%s::uuid[], %s::uuid[], %s::uuid[]))
                   select p.a, p.b from pair p
                   join result_set rs on rs.contest_id = p.contest
                   join contest_option oa on oa.id = p.a join contest_option ob on ob.id = p.b
                   where exists (select from result_value v where v.result_set_k = rs.k and v.option_k = oa.k)
                     and exists (select from result_value v where v.result_set_k = rs.k and v.option_k = ob.k)""",
                [[c for c, _, _ in ballots], [a for _, a, _ in ballots], [b for _, _, b in ballots]],
            ).fetchall():
                rivals.add((a, b))

        parent: dict[UUID, UUID] = {}

        def root(node: UUID) -> UUID:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        blocked = 0
        for one, two, shared in pairs:
            if any((a, b) in rivals for _, a, b in shared):
                blocked += 1
                continue
            parent[root(one)] = root(two)
        groups: dict[UUID, list[UUID]] = defaultdict(list)
        for person in parent:
            groups[root(person)].append(person)

        names: dict[UUID, set[Name]] = defaultdict(set)
        for people in seats.values():
            for person, records in people.items():
                if person in parent:
                    names[person].update(record[0] for record in records)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "People that two or more pipelines recorded apart on the same local seat (the same contest key "
                "without its date) joined into one person where their names share a surname and suffix, their "
                "first and middle names agree (ignoring case, accents, punctuation, and spaces, a common nickname such as "
                "Bill for William, a name printed as its initial, or a short form three or more letters shorter), they ran within 12 "
                "years of each other, no result counts both on one ballot, and no Wikidata, Bioguide, or FEC "
                "identifier tells them apart"
            ),
            polity=None,
            kind="match",
        )
        merged = skipped = 0
        for people in groups.values():
            schemes: dict[str, set[str]] = defaultdict(set)
            for person in people:
                for scheme, values in held[person].items():
                    schemes[scheme] |= values
            if any(len(values) > 1 for values in schemes.values()) or not all(
                same(a, b) for one, two in combinations(people, 2) for a in names[one] for b in names[two]
            ):
                skipped += 1
                continue
            survivor = max(
                people,
                key=lambda person: (
                    any(scheme in held[person] for scheme in STRONG),
                    ran.get(person, 0),
                    -created[person].timestamp(),
                    -person.int,
                ),
            )
            for person in people:
                if person != survivor:
                    cs.merge(person, survivor)
                    merged += 1
        print(f"{len(pairs)} pairs, {blocked} on one ballot, {len(groups)} groups, {merged} merged, {skipped} skipped")
        return cs.write()
