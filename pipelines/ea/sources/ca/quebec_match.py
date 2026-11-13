import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

from ea.changeset import Changeset
from ea.store import Connection, connect

SOURCE = "ca.quebec_match"
VERSION = "1"
SHORT = {"st": "saint", "ste": "sainte"}


class Run(NamedTuple):
    person: UUID
    day: date
    area: UUID
    party: UUID | None
    printed: str
    won: bool


def words(text: str) -> list[str]:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return [SHORT.get(word, word) for word in re.split(r"[^a-z]+", folded) if len(word) > 1]


def variant(a: str, b: str) -> bool:
    *left, _ = a.split()
    *right, _ = b.split()
    return set(left) <= set(right) or set(right) <= set(left) or (left[0][:4] == right[0][:4] and len(left[0]) > 3)


def groups(conn: Connection) -> tuple[dict[UUID, list[UUID]], dict[UUID, Run]]:
    rows = conn.execute(
        """select cy.person_id, e.date_from, e.kind = 'general', k.district_area_id, o.party_id, cy.ballot_name,
                  exists (select from outcome w join result_set r on r.id = w.result_set_id
                          where w.option_id = o.id and r.is_default and w.kind = 'elected'),
                  array(select n.text from name n where n.entity_id = k.district_area_id)
           from candidacy cy
           join contest_option o on o.id = cy.option_id
           join contest k on k.id = o.contest_id
           join election e on e.id = k.election_id
           join changeset c on c.id = k.changeset_id
           where c.pipeline = 'ca.quebec' and c.status = 'published'"""
    ).fetchall()
    runs = [
        Run(person, day, area, party, " ".join(words(printed)), won)
        for person, day, _, area, party, printed, won, _ in rows
    ]
    generals = sorted({day for _, day, general, *_ in rows if general})
    tokens = {
        area: {word for label in labels for word in words(label) if len(word) > 2} for *_, area, _, _, _, labels in rows
    }
    generic = {
        word
        for general in generals
        for word, count in Counter(
            word for area in {run.area for run in runs if run.day == general} for word in tokens[area]
        ).items()
        if count > 2
    }

    def near(a: Run, b: Run) -> bool:
        return a.area == b.area or bool((tokens[a.area] & tokens[b.area]) - generic)

    def horizon(day: date, step: int) -> date:
        later = [general for general in generals if general > day]
        return later[min(step, len(later)) - 1] if later else day

    by_surname: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        if run.printed:
            by_surname[run.printed.split()[-1]].append(run)
    rules: dict[str, list[tuple[Run, Run]]] = {"riding": [], "member": [], "party": [], "variant": []}
    for same in by_surname.values():
        for a in same:
            for b in same:
                if not a.day < b.day <= horizon(a.day, 2):
                    continue
                party = a.party is not None and a.party == b.party
                next_general = b.day <= horizon(a.day, 1)
                if a.printed != b.printed:
                    alone = a.party is None and b.party is None and a.area == b.area
                    if next_general and (party or alone) and near(a, b) and variant(a.printed, b.printed):
                        rules["variant"].append((a, b))
                elif next_general and near(a, b):
                    rules["riding"].append((a, b))
                elif next_general and a.won and party:
                    rules["member"].append((a, b))
                elif party and near(a, b):
                    rules["party"].append((a, b))

    parent: dict[UUID, UUID] = {}
    days: dict[UUID, set[date]] = {run.person: {run.day} for run in runs}

    def root(node: UUID) -> UUID:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for rule, pairs in rules.items():
        sides = Counter(side for a, b in pairs for side in ((a.person, b.day), (b.person, a.day)))
        matched = clashes = 0
        for a, b in sorted(pairs, key=lambda pair: (pair[0].day, pair[1].day)):
            if sides[a.person, b.day] != 1 or sides[b.person, a.day] != 1:
                continue
            left, right = root(a.person), root(b.person)
            if left == right:
                continue
            if days[left] & days[right]:
                clashes += 1
                continue
            parent[left] = right
            days[right] |= days.pop(left)
            matched += 1
        print(f"{rule}: {len(pairs)} pairs, {matched} joined, {clashes} refused for running twice on one day")

    groups: dict[UUID, list[UUID]] = defaultdict(list)
    for node in list(parent):
        groups[root(node)].append(node)
    return {key: people for key, people in groups.items() if len(people) > 1}, {run.person: run for run in runs}


def emit() -> Path:
    with connect() as conn:
        found, runs = groups(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Quebec provincial candidates matched across elections by printed name, ignoring initials: in the same or a namesake riding at the next general election, as a sitting member under the same party at the next general election, in the same or a namesake riding under the same party two general elections later, or with a shortened or extended given name in the same or a namesake riding under the same party, or as an independent in the same riding, at the next general election",
            polity="CA-QC",
            kind="match",
        )
        sizes = Counter(len(people) for people in found.values())
        print(
            f"{sum(sizes.values())} people from {sum(n * c for n, c in sizes.items())} candidacies; sizes {sorted(sizes.items())}"
        )
        for people in found.values():
            survivor = min(people, key=lambda person: (runs[person].day, person.int))
            for person in people:
                if person != survivor:
                    cs.add("entity_merge", entity_id=person, into_id=survivor)
        return cs.write()
