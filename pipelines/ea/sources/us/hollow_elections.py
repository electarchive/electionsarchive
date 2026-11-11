from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.hollow_elections"
VERSION = "1"
PIPELINES = [
    "us.enr_static",
    "us.enr_clarity",
    "us.enr_clarity_json",
    "us.stlco",
    "us.stlco_old",
    "us.medsl_local",
    "us.oh_franklin",
    "us.tx_austin",
    "us.enr_ev",
    "us.klarner",
    "us.il_cook",
    "us.local_2026",
]
REASON = (
    "Every contest of this election now belongs to the election of a finer polity or was withdrawn as a twin, "
    "so it holds no contest, no child election, and no active result set"
)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire elections left with no contest, no child election, and no active result set",
            polity="US",
            kind="correction",
        )
        elections = [
            election
            for (election,) in conn.execute(
                """select e.id from election e join entity n on n.id = e.id and n.retired_by is null
                   join changeset h on h.id = e.changeset_id and h.pipeline = any(%s)
                   where not exists (select from contest c where c.election_id = e.id)
                     and not exists (select from election k where k.parent_id = e.id)
                     and not exists (select from result_set r where r.election_id = e.id and r.status = 'active')
                     and not exists (select from entity_merge m where m.entity_id = e.id or m.into_id = e.id)
                     and not exists (select from result_set r where r.election_id = e.id and r.contest_id is null)
                     and not exists (select from entity_retirement t where t.entity_id = e.id)
                   order by 1""",
                [PIPELINES],
            ).fetchall()
        ]
        for election in elections:
            cs.add("entity_retirement", entity_id=election, reason=REASON)
        moved = conn.execute(
            """select r.id, c.election_id, r.contest_id, r.stage, r.kind, r.source_id, r.as_of, r.status,
                      r.supersedes_id, r.completeness
               from result_set r join contest c on c.id = r.contest_id
               where r.election_id = any(%s::uuid[]) and r.status <> 'active' order by 1""",
            [elections],
        ).fetchall()
        for row in moved:
            cs.add(
                "result_set",
                id=row[0],
                election_id=row[1],
                contest_id=row[2],
                stage=row[3],
                kind=row[4],
                source_id=row[5],
                as_of=row[6],
                status=row[7],
                is_default=False,
                supersedes_id=row[8],
                completeness=row[9],
            )
        print(f"{len(elections)} elections, {len(moved)} result sets moved to their contest's election")
        return cs.write()
