from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.va_cleanup"
VERSION = "1"
LABELS = {
    "Jennifer Bartonboysko": "Jennifer Barton Boysko",
    "Ibraheem Sabrisamirah": "Ibraheem Sabri Samirah",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia General Assembly ballot labels and names that Klarner's rows ran together",
            polity="US-VA",
            kind="correction",
        )
        for option, contest, label, candidacy, person in conn.execute(
            """select o.id, o.contest_id, o.ballot_label, c.id, c.person_id
               from contest_option o join candidacy c on c.option_id = o.id and c.role = 'head'
               where o.ballot_label = any(%s) and o.kind = 'candidate'""",
            [list(LABELS)],
        ).fetchall():
            cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=LABELS[label])
            cs.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=LABELS[label])
        return cs.write()
