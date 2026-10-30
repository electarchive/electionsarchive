from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.enr_clarity_retire"
VERSION = "1"
REASON = (
    "Winnebago County, Illinois election written by us.enr_clarity changeset 01a16cae with the placeholder date "
    "01/01/0001 from four Clarity files named not_set; the same elections exist under their real dates"
)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire the placeholder-dated Winnebago County, Illinois election and its contests",
            polity="US",
            kind="correction",
        )
        elections = [row[0] for row in conn.execute("select id from election where date_from < '1700-01-01'").fetchall()]
        contests = [
            row[0]
            for row in conn.execute(
                "select id from contest where election_id = any(%s::uuid[])", [elections]
            ).fetchall()
        ]
        for contest in contests:
            cs.add("entity_retirement", entity_id=contest, reason=REASON)
        for election in elections:
            cs.add("entity_retirement", entity_id=election, reason=REASON)
        print(f"{len(elections)} elections, {len(contests)} contests")
        return cs.write()
