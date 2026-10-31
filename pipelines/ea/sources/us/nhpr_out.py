import os
from pathlib import Path

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
            print("no result sets from New Hampshire Public Radio remain")
            return None
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
