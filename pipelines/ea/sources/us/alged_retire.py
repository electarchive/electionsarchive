from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import alged
from ea.store import connect

SOURCE = "us.alged_retire"
VERSION = "2"


def emit() -> Path:
    phantoms = pl.read_parquet(raw.parsed(alged.SOURCE) / "phantoms.parquet")["contest"].to_list()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire ALGED contests that record a succession, appointment, or party switch rather than an election",
            polity="US",
            kind="correction",
        )
        contests = [
            entity
            for (entity,) in conn.execute(
                """select x.entity_id from external_id x join entity e on e.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = any(%s) and e.retired_by is null order by 1""",
                [[f"alged:{contest}" for contest in phantoms]],
            ).fetchall()
        ]
        elections = [
            election
            for (election,) in conn.execute(
                """select c.election_id from contest c group by 1
                   having count(*) = count(*) filter (where c.id = any(%s)) and bool_or(c.id = any(%s)) order by 1""",
                [contests, contests],
            ).fetchall()
        ]
        for entity in contests:
            cs.add(
                "entity_retirement",
                entity_id=entity,
                reason="A single placeholder row in a month off the office's election cycle: a succession, appointment, or party switch, not an election",
            )
        for entity in elections:
            cs.add(
                "entity_retirement",
                entity_id=entity,
                reason="Held only a retired ALGED placeholder contest",
            )
        hollow = [
            election
            for (election,) in conn.execute(
                """select e.id from election e join entity n on n.id = e.id and n.retired_by is null
                   join changeset h on h.id = e.changeset_id and h.pipeline = %s
                   where not exists (select from contest c where c.election_id = e.id)
                     and not exists (select from election k where k.parent_id = e.id)
                     and not exists (select from result_set r where r.election_id = e.id)
                     and not exists (select from entity_merge m where m.entity_id = e.id)
                     and not exists (select from entity_retirement t where t.entity_id = e.id)
                   order by 1""",
                [alged.SOURCE],
            ).fetchall()
            if election not in elections
        ]
        for entity in hollow:
            cs.add(
                "entity_retirement",
                entity_id=entity,
                reason="Every contest of this election was withdrawn as a twin of a day-dated contest from another pipeline",
            )
        print(f"{len(contests)} contests, {len(elections)} elections, {len(hollow)} hollow elections")
        return cs.write()
