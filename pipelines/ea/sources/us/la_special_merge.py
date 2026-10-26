from collections import defaultdict
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.la_special_merge"
VERSION = "1"


def emit() -> Path:
    current = set(
        pl.read_parquet(raw.parsed("us.la_local") / "contests.parquet")
        .filter(pl.col("kind") == "special")["code"]
        .to_list()
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge the pre-normalization Louisiana special-district polities, with their areas, units, elections, and ballot questions, into the ones us.la_local now emits, and retire those with no survivor",
            polity="US",
            kind="correction",
        )
        codes = {
            entity: value.removeprefix("special:")
            for value, entity in conn.execute(
                """select x.value, x.entity_id from external_id x join entity e on e.id = x.entity_id
                   where x.scheme = 'us.polity' and x.value like 'special:22%%' and e.retired_by is null"""
            ).fetchall()
        }
        old = {code: entity for entity, code in codes.items() if code not in current}
        current_contests: dict[tuple[str, str], list[tuple[UUID, UUID, UUID]]] = defaultdict(list)
        for value, contest, election, polity in conn.execute(
            """select x.value, c.id, c.election_id, e.polity_id from external_id x
               join contest c on c.id = x.entity_id join election e on e.id = c.election_id
               where x.scheme = 'us.contest' and x.value ~ '^[0-9-]+:LA:'"""
        ).fetchall():
            current_contests[(value.split(":")[0], value.rsplit(":", 1)[1])].append((contest, election, polity))
        owners = set(old.values())
        stale = conn.execute(
            """select x.value, c.id, c.election_id, e.polity_id from external_id x
               join contest c on c.id = x.entity_id join election e on e.id = c.election_id
               where x.scheme = 'us.contest' and e.polity_id = any(%s)""",
            [list(owners)],
        ).fetchall()
        stale_ids = {contest for _, contest, _, _ in stale}
        elections: dict[UUID, set[UUID]] = defaultdict(set)
        survivors: dict[UUID, set[UUID]] = defaultdict(set)
        unmatched = 0
        for value, contest, election, polity in stale:
            found = [
                row
                for row in current_contests[(value.split(":")[0], value.rsplit(":", 1)[1])]
                if row[0] not in stale_ids
            ]
            if len(found) != 1:
                unmatched += 1
                continue
            cs.merge(contest, found[0][0])
            elections[election].add(found[0][1])
            survivors[polity].add(found[0][2])
        for election, targets in elections.items():
            if len(targets) == 1:
                cs.merge(election, next(iter(targets)))
        for polity, targets in survivors.items():
            survivor = min(targets, key=lambda target: "schooldistrict" in codes.get(target, ""))
            cs.merge(polity, survivor)
        print(f"{len(old)} old polities, {len(stale)} stale contests ({unmatched} unmatched), {len(survivors)} merged")
        return cs.write()
