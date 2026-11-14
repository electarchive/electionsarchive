import re
import unicodedata
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from uuid import UUID

from psycopg import Connection

from ea.changeset import Changeset
from ea.sources.fr.names import head_parts, person_name
from ea.sources.fr.regionales import NEW_REGIONS, OLD_REGIONS, OVERSEAS
from ea.store import connect

SOURCE = "fr.match"
VERSION = "2"
CHAINED = (
    "fr.cdsp",
    "fr.interieur",
    "fr.legislatives_1986",
    "fr.legislatives_1981_overseas",
    "fr.senatoriales",
    "fr.departementales",
)
GAP = 12
ANCHORS = (
    "fr.cdsp.person",
    "fr.interieur.person",
    "fr.legislatives_1986.person",
    "fr.legislatives_1981_overseas.person",
    "fr.senatoriales.person",
    "fr.departementales.person",
    "fr.municipales.person",
)
ABBREVIATED = re.compile(r"\b[A-Z][.-][A-Z]|\b[A-Z]\.|\s(De|Du|Des)$")
FOLD = "trim(regexp_replace(lower(unaccent({})), '[^a-z]+', ' ', 'g'))"


def region_departements(scheme: str, code: str) -> set[str]:
    if scheme == "fr.departement":
        return departement(scheme, code)
    parts = NEW_REGIONS[code][1] if code in NEW_REGIONS else (code,)
    return {dept for part in parts for dept in OLD_REGIONS[part][1].split()}


def departement(scheme: str, code: str) -> set[str]:
    if scheme == "fr.departement":
        dept = code
    elif scheme == "fr.circonscription":
        dept = code[:-2]
    else:
        dept = code[:3] if code.startswith("97") else code[:2]
    dept = OVERSEAS.get(dept, dept)
    return {"2A", "2B"} if dept == "20" else {dept}


def words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return [word for word in re.split(r"[^a-z]+", plain) if word]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Heads of list in the French regional elections matched with each other when they share a surname, "
                "a compatible forename, and a region, and with législatives, sénatoriales, départementales, and "
                "municipal heads of list who share their full name and ran in a département of the same region; "
                "législatives, sénatoriales, and départementales candidates matched across elections when they share "
                "a printed name and a département, or the only two adjacent départements where that name stood in any election, no "
                "namesake ran in the same election or stood in another commune of the département in the same "
                "municipal election, and their candidacies lie at most 12 years apart, with municipal heads of list "
                "from a single commune joining them"
            ),
            polity="FR",
            kind="match",
        )
        heads: dict[UUID, set[str]] = defaultdict(set)
        folded: dict[UUID, set[str]] = defaultdict(set)
        split: dict[UUID, set[tuple[str, tuple[str, ...]]]] = defaultdict(set)
        shown: dict[UUID, str] = {}
        rows = conn.execute(
            f"""select cy.person_id, cy.ballot_name, a.scheme_code, ac.code, n.text, {FOLD.format("n.text")}
               from candidacy cy
               join changeset c on c.id = cy.changeset_id and c.pipeline = 'fr.regionales'
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join area a on a.id = k.district_area_id
               join area_code ac on ac.area_id = a.id
               join name n on n.entity_id = cy.person_id and n.is_preferred"""
        ).fetchall()
        for person, ballot, scheme, region, raw, text in rows:
            heads[person] |= region_departements(scheme, region)
            shown[person] = raw
            forename, surname = head_parts(ballot)
            full = " ".join(words(person_name(forename, surname)))
            folded[person] |= {text, full}
            split[person].add((" ".join(words(surname)), tuple(words(forename or ""))))

        contests: dict[UUID, set[UUID]] = defaultdict(set)
        candidacies: dict[UUID, int] = defaultdict(int)
        wikidata: dict[UUID, str] = {}

        def load(people: list[UUID]) -> None:
            for person, contest in conn.execute(
                """select cy.person_id, o.contest_id from candidacy cy join contest_option o on o.id = cy.option_id
                   where cy.person_id = any(%s)""",
                [people],
            ).fetchall():
                contests[person].add(contest)
                candidacies[person] += 1
            wikidata.update(
                conn.execute(
                    "select entity_id, value from external_id where scheme = 'wikidata' and entity_id = any(%s)",
                    [people],
                ).fetchall()
            )

        load(list(heads))

        parent: dict[UUID, UUID] = {}

        def root(node: UUID) -> UUID:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
            short, long = sorted((a, b), key=len)
            return bool(short) and any(long[i : i + len(short)] == short for i in range(len(long) - len(short) + 1))

        def separate(left: set[UUID], right: set[UUID]) -> bool:
            return not any(contests[a] & contests[b] for a in left for b in right if a != b)

        def clash(people: set[UUID]) -> bool:
            return len({wikidata[person] for person in people if person in wikidata}) > 1

        groups: dict[UUID, set[UUID]] = {}
        internal = 0
        people = sorted(heads, key=lambda person: person.int)
        for index, person in enumerate(people):
            for other in people[index + 1 :]:
                if not heads[person] & heads[other] or root(person) == root(other):
                    continue
                if not any(
                    sur == other_sur and compatible(fore, other_fore)
                    for sur, fore in split[person]
                    for other_sur, other_fore in split[other]
                ):
                    continue
                left = {node for node in parent if root(node) == root(person)} | {person}
                right = {node for node in parent if root(node) == root(other)} | {other}
                if separate(left, right) and not clash(left | right):
                    parent[root(other)] = root(person)
                    internal += 1
        for person in people:
            groups.setdefault(root(person), set()).add(person)
        print(f"{len(heads)} regional heads, {internal} matched with each other, {len(groups)} people")

        names = sorted({text for person in heads for text in folded[person]})
        anchors: dict[str, set[UUID]] = defaultdict(set)
        for person, raw, text in conn.execute(
            f"""select distinct x.entity_id, n.text, {FOLD.format("n.text")} from external_id x
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = any(%s) and {FOLD.format("n.text")} = any(%s)
                 and not exists (select from entity_merge m where m.entity_id = x.entity_id)""",
            [list(ANCHORS), names],
        ).fetchall():
            if person not in heads:
                anchors[text].add(person)
                shown[person] = raw
        found = sorted({person for people in anchors.values() for person in people}, key=lambda person: person.int)
        places: dict[UUID, set[str]] = defaultdict(set)
        communes: dict[UUID, set[str]] = defaultdict(set)
        for person, scheme, code, role in conn.execute(
            """select cy.person_id, a.scheme_code, ac.code, cy.role from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join area a on a.id = k.district_area_id
               join area_code ac on ac.area_id = a.id
               where cy.person_id = any(%s)""",
            [found],
        ).fetchall():
            if scheme in ("fr.commune", "fr.secteur"):
                if role != "head":
                    continue
                communes[person].add(code[:5])
            places[person] |= departement(scheme, code)
        load(found)

        nearby: dict[UUID, set[UUID]] = {}
        apart: list[str] = []
        for key, members in groups.items():
            area = set().union(*(heads[person] for person in members))
            texts = set().union(*(folded[person] for person in members))
            nearby[key] = {person for text in texts for person in anchors.get(text, ()) if places[person] & area}
            if len(set().union(*(communes[person] for person in nearby[key]))) > 1:
                nearby[key] = {person for person in nearby[key] if not communes[person]}
                apart.append(" / ".join(sorted(texts)))
        shared = Counter(person for near in nearby.values() for person in near)
        joined = 0
        skipped: list[str] = []
        for key, members in groups.items():
            near = nearby[key]
            people = members | near
            if near and (any(shared[person] > 1 for person in near) or not separate(people, people) or clash(people)):
                skipped.append(" / ".join(sorted(set().union(*(folded[person] for person in members)))))
                near = set()
                people = members
            joined += bool(near)
            survivor = max(
                people,
                key=lambda person: (
                    person in wikidata,
                    not ABBREVIATED.search(shown[person]),
                    candidacies[person],
                    -person.int,
                ),
            )
            for person in sorted(people, key=lambda person: person.int):
                if person != survivor:
                    cs.merge(person, survivor)
        print(f"{len(apart)} people not matched with municipal heads of list in more than one commune:")
        for text in sorted(apart):
            print(f"  {text}")
        print(f"{joined} people matched with other French candidates; {len(skipped)} held back:")
        for text in sorted(skipped):
            print(f"  {text}")
        chain(conn, cs)
        return cs.write()


def absorb[T](table: dict[UUID, set[T]], left: UUID, right: UUID) -> None:
    table[left] |= table[right]


def chain(conn: Connection, cs: Changeset) -> None:
    rows = conn.execute(
        f"""select cy.person_id, {FOLD.format("n.text")}, n.text, c.pipeline, e.date_from, k.id, a.scheme_code, ac.code,
                  p.birth_date
           from candidacy cy
           join contest_option o on o.id = cy.option_id
           join contest k on k.id = o.contest_id
           join changeset c on c.id = k.changeset_id
           join election e on e.id = k.election_id
           join area a on a.id = k.district_area_id and a.scheme_code <> 'fr.nation'
           join area_code ac on ac.area_id = a.id
           join name n on n.entity_id = cy.person_id and n.is_preferred
           join person p on p.id = cy.person_id
           where c.pipeline = any(%s) or (c.pipeline = 'fr.municipales' and o.kind = 'list' and cy.role = 'head')""",
        [list(CHAINED)],
    ).fetchall()
    survivors = cs.survivors
    names: dict[UUID, set[str]] = defaultdict(set)
    places: dict[UUID, set[str]] = defaultdict(set)
    shown: dict[UUID, str] = {}
    counts: Counter[UUID] = Counter()
    wikidata: dict[UUID, set[str]] = defaultdict(set)
    births: dict[UUID, set[object]] = defaultdict(set)
    contests: dict[UUID, set[UUID]] = defaultdict(set)
    polls: dict[UUID, set[tuple[bool, object]]] = defaultdict(set)
    years: dict[UUID, set[int]] = defaultdict(set)
    communes: dict[UUID, set[str]] = defaultdict(set)
    core: set[UUID] = set()
    raw: set[UUID] = set()
    for person, text, name, pipeline, day, contest, scheme, code, birth in rows:
        raw.add(person)
        person = survivors.get(person, person)
        local = pipeline == "fr.municipales"
        names[person].add(text)
        shown.setdefault(person, name)
        places[person] |= departement(scheme, code)
        counts[person] += 1
        contests[person].add(contest)
        polls[person].add((local, day))
        years[person].add(day.year)
        if birth:
            births[person].add(birth)
        if local:
            communes[person].add(code[:5])
        else:
            core.add(person)
    for person, value in conn.execute(
        "select entity_id, value from external_id where scheme = 'wikidata' and entity_id = any(%s)", [list(raw)]
    ).fetchall():
        wikidata[survivors.get(person, person)].add(value)
    adjacent = {
        frozenset(pair)
        for pair in conn.execute(
            """with d as (select ac.code, st_simplify(g.geom, 0.005) as geom from area a
                          join area_code ac on ac.area_id = a.id
                          join area_geometry g on g.area_id = a.id and g.valid @> current_date
                          where a.scheme_code = 'fr.departement')
               select x.code, y.code from d x join d y on x.code < y.code and st_dwithin(x.geom, y.geom, 0.01)"""
        ).fetchall()
    }

    crowded: set[tuple[str, str]] = set()
    spread: dict[str, set[str]] = defaultdict(set)
    for text, place, most in conn.execute(
        f"""select text, place, max(communes) from (
               select {FOLD.format("n.text")} as text,
                      case when ac.code like '97%%' then left(ac.code, 3) else left(ac.code, 2) end as place,
                      count(distinct left(ac.code, 5)) as communes
               from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join changeset c on c.id = k.changeset_id and c.pipeline = 'fr.municipales'
               join election e on e.id = k.election_id
               join area_code ac on ac.area_id = k.district_area_id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               group by 1, 2, e.date_from) s
           group by 1, 2"""
    ):
        spread[text].add(place)
        if most > 1:
            crowded.add((text, place))

    parent: dict[UUID, UUID] = {}

    def root(node: UUID) -> UUID:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def fits(left: UUID, right: UUID) -> bool:
        if contests[left] & contests[right] or polls[left] & polls[right]:
            return False
        if len(wikidata[left] | wikidata[right]) > 1 or len(births[left] | births[right]) > 1:
            return False
        if len(communes[left] | communes[right]) > 1:
            return False
        span = sorted(years[left] | years[right])
        return all(b - a <= GAP for a, b in pairwise(span))

    def join(left: UUID, right: UUID) -> bool:
        left, right = root(left), root(right)
        if left == right:
            return True
        if not fits(left, right):
            return False
        parent[right] = left
        for table in (names, places, wikidata, contests, communes):
            absorb(table, left, right)
        absorb(births, left, right)
        absorb(polls, left, right)
        absorb(years, left, right)
        return True

    groups: dict[tuple[str, str], set[UUID]] = defaultdict(set)
    for person, texts in names.items():
        for text in texts:
            for place in places[person]:
                groups[text, place].add(person)
    rivals = {
        key
        for key, members in groups.items()
        if key in crowded or any(polls[a] & polls[b] for a in members & core for b in members & core if a != b)
    }
    for key, members in sorted(groups.items()):
        if key in rivals or not members & core:
            continue
        people = sorted(members & core, key=lambda person: (min(years[person]), person.int))
        for person in people[1:]:
            join(people[0], person)
        local = sorted(members - core, key=lambda person: person.int)
        if len({commune for person in local for commune in communes[person]}) == 1:
            for person in local:
                join(people[0], person)
    print(
        f"{len(core)} législatives, sénatoriales, and départementales people; {len(rivals)} names held back by a namesake"
    )

    near: dict[tuple[str, str], UUID] = {}
    for (text, place), members in groups.items():
        if members & core:
            spread[text].add(place)
        if (text, place) in rivals:
            continue
        found = {root(person) for person in members & core}
        if len(found) == 1:
            near[text, place] = found.pop()
    pairs = [
        (near[text, place], near[text, other])
        for (text, place) in near
        for other in {code for pair in adjacent if place in pair for code in pair if code != place}
        if spread[text] == {place, other}
        and (text, other) in near
        and place < other
        and near[text, place] != near[text, other]
    ]
    sides = Counter(person for pair in pairs for person in pair)
    linked = sum(join(left, right) for left, right in pairs if sides[left] == sides[right] == 1)
    print(f"{len(pairs)} names in adjacent départements, {linked} matched")

    clusters: dict[UUID, set[UUID]] = defaultdict(set)
    for node in parent:
        clusters[root(node)].add(node)
    merged = 0
    for people in clusters.values():
        if len(people) < 2:
            continue
        survivor = max(
            people,
            key=lambda person: (
                bool(wikidata.get(person)),
                not ABBREVIATED.search(shown[person]),
                counts[person],
                -person.int,
            ),
        )
        for person in sorted(people, key=lambda person: person.int):
            if person != survivor:
                cs.merge(person, survivor)
                merged += 1
    print(f"{merged} people merged into {sum(len(people) > 1 for people in clusters.values())}")
