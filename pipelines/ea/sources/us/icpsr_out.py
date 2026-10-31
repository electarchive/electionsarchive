import os
from pathlib import Path

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
            print("no result sets licensed from ICPSR remain")
            return None
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
