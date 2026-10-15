from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea.changeset import Changeset
from ea.sources.tr import ysk
from ea.store import connect

SOURCE = "tr.match"
VERSION = "3"
PAIRS = (
    (ysk.ROUND_DAYS[1], ysk.DAY_2018, ()),
    (ysk.DAY_2015, ysk.DAY_2018, ("06",)),
    (ysk.DAY_JUNE_2015, ysk.DAY_2011, ()),
    (ysk.DAY_2015, ysk.DAY_2011, ()),
    (ysk.DAY_2011, ysk.DAY_2007, ()),
)


def emit() -> Path:
    with connect() as conn:
        seated: dict[str, UUID] = dict(
            conn.execute(
                """select x.value, c.person_id from external_id x join candidacy c on c.id = x.entity_id
                   where x.scheme = 'tr.candidacy'"""
            ).fetchall()
        )
        items: dict[UUID, set[str]] = {}
        for person, qid in conn.execute(
            "select entity_id, value from external_id where scheme = 'wikidata'"
        ).fetchall():
            items.setdefault(person, set()).add(qid)
        candidacies: dict[UUID, int] = dict(
            conn.execute("select person_id, count(*) from candidacy group by person_id").fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Members of the Grand National Assembly matched across consecutive elections by printed name and "
                "electoral district"
            ),
            polity="TR",
            kind="match",
        )

        members: dict[date, dict[tuple[str, str], UUID]] = {}
        for day, suffix in (
            (ysk.ROUND_DAYS[1], ""),
            (ysk.DAY_2018, f"_{ysk.DAY_2018}"),
            (ysk.DAY_2015, f"_{ysk.DAY_2015}"),
            (ysk.DAY_JUNE_2015, f"_{ysk.DAY_JUNE_2015}"),
            (ysk.DAY_2011, f"_{ysk.DAY_2011}"),
            (ysk.DAY_2007, f"_{ysk.DAY_2007}"),
        ):
            rows = [
                (code, ysk.upper(name), sequence)
                for code, sequence, name in pl.read_parquet(ysk.PARSED / f"elected{suffix}.parquet")
                .select("code", "sequence", "name")
                .iter_rows()
            ]
            seen = Counter((code, printed) for code, printed, _ in rows)
            members[day] = {
                (code, printed): seated[f"{day}:{code}:{sequence}"]
                for code, printed, sequence in rows
                if seen[(code, printed)] == 1
            }

        parent: dict[UUID, UUID] = {}

        def root(node: UUID) -> UUID:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        matched = Counter[tuple[date, date]]()
        for later, earlier, skipped in PAIRS:
            for place, person in members[later].items():
                other = members[earlier].get(place)
                if other is None or place[0].startswith(skipped) or root(person) == root(other):
                    continue
                parent[root(person)] = root(other)
                matched[(later, earlier)] += 1

        groups: dict[UUID, list[UUID]] = {}
        for node in list(parent):
            groups.setdefault(root(node), []).append(node)
        merges, conflicts = 0, []
        for people in groups.values():
            if len(people) < 2:
                continue
            if len(set().union(*(items.get(person, set()) for person in people))) > 1:
                conflicts.append(people)
                continue
            survivor = max(people, key=lambda person: (person in items, candidacies.get(person, 0), -person.int))
            for person in people:
                if person != survivor:
                    cs.add("entity_merge", entity_id=person, into_id=survivor)
                    merges += 1
        print(f"pairs matched: {dict(matched)}; {merges} merges; {len(conflicts)} groups with several Wikidata items")
        for people in conflicts:
            print(people, [sorted(items.get(person, set())) for person in people])
        return cs.write()
