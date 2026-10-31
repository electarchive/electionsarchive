import os
from pathlib import Path
from uuid import uuid7

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.icpsr_out"
VERSION = "1"
REASON = "Withdrawn: compiled from ICPSR files, whose terms do not permit redistribution"


def emit() -> Path | None:
    size = int(os.environ.get("ICPSR_OUT_CHUNK", "4000"))
    with connect() as conn:
        sets = conn.execute(
            """select r.id, r.source_id from result_set r join source s on s.id = r.source_id
               where s.license ilike '%%ICPSR%%' order by r.contest_id, r.k limit %s""",
            [size],
        ).fetchall()
        if not sets:
            return retire_sources(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Withdraw {len(sets):,} result sets compiled from ICPSR files or from Algara and Amlani's sets built on them",
            polity="US",
        )
        for result_set, source in sets:
            cs.add("result_set_drop", result_set_id=result_set, source_id=source, reason=REASON)
        return cs.write()


def retire_sources(conn) -> Path | None:
    sources = conn.execute("select id from source where license ilike '%%ICPSR%%' order by id").fetchall()
    if not sources:
        print("no sources licensed from ICPSR remain")
        return None
    cs = Changeset(
        conn,
        pipeline=SOURCE,
        version="2",
        summary=f"Retire {len(sources)} sources licensed from ICPSR, with their artifacts, now that no result set cites them",
        polity="US",
    )
    columns = [
        row[0]
        for row in conn.execute(
            """select attname from pg_attribute where attrelid = 'public.source_artifact'::regclass
               and attnum > 0 and not attisdropped and attgenerated = '' and attname not in ('changeset_id', 'recorded_from')
               order by attnum"""
        ).fetchall()
    ]
    artifacts = conn.execute(
        "select * from source_artifact where source_id in (select id from source where license ilike '%%ICPSR%%') order by id"
    ).fetchall()
    names = [d.name for d in conn.execute("select * from source_artifact limit 0").description]
    for row in artifacts:
        record = dict(zip(names, row))
        cs.add(
            "row_drop",
            id=uuid7(),
            reason=REASON,
            relation="source_artifact",
            key={
                c: record[c] if record[c] is None or isinstance(record[c], (int, str)) else str(record[c])
                for c in columns
            },
        )
    for (source,) in sources:
        cs.add("entity_retirement", entity_id=source, reason=REASON)
    return cs.write()
