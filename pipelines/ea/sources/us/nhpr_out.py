import os
from pathlib import Path
from uuid import uuid7

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.nhpr_out"
VERSION = "1"
NHPR = "f213a101-ba36-59bc-b33c-b304b746e953"
REASON = (
    "Withdrawn: copied from New Hampshire Public Radio's election database, whose terms do not permit redistribution"
)


def emit() -> Path | None:
    size = int(os.environ.get("NHPR_OUT_CHUNK", "2800"))
    with connect() as conn:
        sets = conn.execute(
            "select id, source_id from result_set where source_id = %s order by k limit %s", [NHPR, size]
        ).fetchall()
        if not sets:
            return retire_source(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Withdraw {len(sets):,} New Hampshire result sets copied from New Hampshire Public Radio's election database",
            polity="US",
        )
        for result_set, source in sets:
            cs.add("result_set_drop", result_set_id=result_set, source_id=source, reason=REASON)
        return cs.write()


def retire_source(conn) -> Path | None:
    if conn.execute("select 1 from source where id = %s", [NHPR]).fetchone() is None:
        print("the New Hampshire Public Radio source is already retired")
        return None
    columns = [
        row[0]
        for row in conn.execute(
            """select attname from pg_attribute where attrelid = 'public.source_artifact'::regclass
               and attnum > 0 and not attisdropped and attgenerated = '' and attname not in ('changeset_id', 'recorded_from')
               order by attnum"""
        ).fetchall()
    ]
    names = [d.name for d in conn.execute("select * from source_artifact limit 0").description]
    cs = Changeset(
        conn,
        pipeline=SOURCE,
        version="2",
        summary="Retire the New Hampshire Public Radio source and its artifacts now that no result set cites it",
        polity="US",
    )
    for row in conn.execute("select * from source_artifact where source_id = %s order by id", [NHPR]).fetchall():
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
    cs.add("entity_retirement", entity_id=NHPR, reason=REASON)
    return cs.write()
