from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ct_sos_2010"
VERSION = "1"
OLD, NEW = "2010-11-02:CT:secretary-of-state", "2010-11-02:CT:secretary-of-the-state"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline="us.oe_statewide",
            version=VERSION,
            summary="Move Connecticut's 2010 secretary of the state election off the stray secretary-of-state office "
            "us.oe_statewide minted for it",
            polity="US-CT",
            kind="correction",
        )
        stray = cs.key("us.office", "secretary-of-state:CT")
        office = cs.key("us.office", "secretary-of-the-state:CT")
        contest = cs.key("us.contest", OLD)
        if cs.minted & {stray, office, contest}:
            raise ValueError("the contest and both offices must exist")
        held = conn.execute(
            "select count(*) from contest where office_id = %s and id <> %s", [stray, contest]
        ).fetchone()
        if held != (0,):
            raise ValueError(f"secretary-of-state:CT holds {held[0]} other contests")
        cs.merge(stray, office)
        cs.renamed = {"secretary-of-the-state": "secretary-of-state"}
        if cs.key("us.contest", NEW) != contest:
            raise ValueError(f"{NEW} did not resolve to {OLD}")
        ((name,),) = conn.execute(
            "select id from name where entity_id = %s and is_preferred and lang = 'en'", [contest]
        ).fetchall()
        cs.add(
            "name",
            id=name,
            entity_id=contest,
            text="2010 Connecticut Secretary of the State election",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add("slug", path="us/ct/2010-11-02-general/secretary-of-the-state", entity_id=contest)
        return cs.write()
