from pathlib import Path

from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.enr_write_ins_2026"
VERSION = "1"
DAY = "2026-11-03"
WRITE_INS = {
    "CT:governor": ["Christian Noe", "L. Lee Whitnum", "Zenobia Adgers"],
    "CT:house:1": ["Mark Stewart Greenstein"],
    "CT:house:2": ["Kyle Gauck"],
    "CT:house:3": ["Andrew Rice"],
    "CT:house:5": ["Jonathan DeBarros"],
    "MD:house:1": ["Edward Shlikas"],
    "MD:house:3": ["Robert L. Coffman"],
    "MD:house:5": ["Mildred Marie Hall"],
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered write-in candidates for Connecticut and Maryland's November 3, 2026 governor and House contests",
            polity="US",
        )
        for tail, names in WRITE_INS.items():
            contest_key = f"{DAY}:{tail}"
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.contest' and value = %s", [contest_key]
            ).fetchone()
            assert found is not None, contest_key
            contest = found[0]
            for candidate in names:
                option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                if option not in cs.minted:
                    continue
                person = cs.key("us.person", f"{SOURCE}:{tail}:{slug(candidate)}")
                cs.add("person", id=person)
                cs.add(
                    "name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True
                )
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=True,
                )
        return cs.write()
