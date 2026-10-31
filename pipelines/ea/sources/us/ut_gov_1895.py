from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ut_gov_1895"
VERSION = "1"
CONTEST = "0926fb7b-5c5b-570d-a1e9-b295c4cca5f7"
COLUMNS = ("office_id", "district_area_id", "electoral_system_id")


def stale(conn, cs: Changeset) -> Path:
    cursor = conn.execute(
        "select * from name where entity_id = %s and text = '1896 Utah gubernatorial election'", [CONTEST]
    )
    names = [d.name for d in cursor.description]
    for row in cursor.fetchall():
        record = dict(zip(names, row, strict=True))
        cs.add(
            "name",
            id=record["id"],
            entity_id=CONTEST,
            text=record["text"],
            lang=record["lang"],
            kind="historical",
            is_preferred=False,
        )
    return cs.write()


def emit() -> Path:
    with connect() as conn:
        office, area, system = conn.execute(
            f"select {', '.join(COLUMNS)} from contest where id = %s", [CONTEST]
        ).fetchone()
        moved = conn.execute(
            "select extract(year from e.date_from) = 1895 from contest c join election e on e.id = c.election_id where c.id = %s",
            [CONTEST],
        ).fetchone()[0]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version="2" if moved else VERSION,
            summary="Utah's first governor election was held on November 5, 1895, not in 1896",
            polity="US",
        )
        if moved:
            return stale(conn, cs)
        election = cs.key("us.election", "1895-11-05:UT")
        cs.add(
            "election",
            id=election,
            polity_id=cs.polity("US-UT"),
            kind="general",
            date_from=date(1895, 11, 5),
            date_to=date(1895, 11, 5),
            date_precision="day",
            status="held",
        )
        cs.add(
            "name", entity_id=election, text="1895 Utah general election", lang="en", kind="official", is_preferred=True
        )
        cs.add("slug", path="us/ut/1895-11-05-general", entity_id=election)
        cs.add(
            "contest",
            id=CONTEST,
            election_id=election,
            kind="office",
            office_id=office,
            district_area_id=area,
            term="full",
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add(
            "name",
            entity_id=CONTEST,
            text="1895 Utah gubernatorial election",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add("slug", path="us/ut/1895-11-05-general/governor", entity_id=CONTEST)
        cs.add("external_id", entity_id=CONTEST, scheme="us.contest", value="1895-11-05:UT:governor")
        return cs.write()
