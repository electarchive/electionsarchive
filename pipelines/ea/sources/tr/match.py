from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea.changeset import Changeset
from ea.sources.tr import ysk
from ea.store import connect

SOURCE = "tr.match"
VERSION = "4"
PAIRS = (
    (ysk.ROUND_DAYS[1], ysk.DAY_2018, ()),
    (ysk.DAY_2015, ysk.DAY_2018, ("06",)),
    (ysk.DAY_JUNE_2015, ysk.DAY_2011, ()),
    (ysk.DAY_2015, ysk.DAY_2011, ()),
    (ysk.DAY_2011, ysk.DAY_2007, ()),
)
LISTED = (
    *PAIRS,
    (ysk.DAY_2015, ysk.DAY_JUNE_2015, ()),
    (ysk.DAY_2007, ysk.DAY_2002, ()),
    (ysk.DAY_2002, ysk.DAY_1999, ()),
    (ysk.DAY_1999, ysk.DAY_1995, ()),
    (ysk.DAY_1995, ysk.DAY_1991, ()),
    (ysk.DAY_1991, ysk.DAY_1987, ()),
    (ysk.DAY_1987, ysk.DAY_1983, ()),
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
                "electoral district, and list candidates by printed name, electoral district, and party"
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

        listed: dict[date, Counter[tuple[object, ...]]] = {}
        keyed: dict[date, dict[tuple[object, ...], UUID]] = {}
        for day, code, party, person, label, won in conn.execute(
            """select split_part(x.value, ':', 1)::date, split_part(x.value, ':', 3), o.party_id, y.person_id, n.text,
                      exists (select from outcome w where w.candidacy_id = y.id and w.kind = 'elected')
               from candidacy y
               join contest_option o on o.id = y.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'tr.contest'
               join name n on n.entity_id = y.person_id and n.is_preferred
               where x.value ~ ':parliament:.'"""
        ).fetchall():
            printed = " ".join(ysk.upper(label).split())
            for key in ([(code, printed)] if won else []) + ([(code, printed, party)] if party is not None else []):
                listed.setdefault(day, Counter())[key] += 1
                keyed.setdefault(day, {})[key] = person
        for later, earlier, skipped in LISTED:
            for key, person in keyed[later].items():
                other = keyed[earlier].get(key)
                if (
                    other is None
                    or str(key[0]).startswith(skipped)
                    or listed[later][key] > 1
                    or listed[earlier][key] > 1
                    or root(person) == root(other)
                ):
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
