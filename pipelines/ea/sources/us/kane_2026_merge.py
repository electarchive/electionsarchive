from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.kane_2026_merge"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge us.kane_il's leftover 2026-11-03 candidacies, options, and people into us.local_2026's",
            polity="US-IL",
        )
        rows = conn.execute(
            """select y.id, y.option_id, y.person_id, o.id, o.contest_id
               from candidacy y join changeset ch on ch.id = y.changeset_id
               join contest_option o on o.id = y.option_id
               join external_id e on e.entity_id = o.contest_id and e.scheme = 'us.contest'
               where ch.pipeline = 'us.kane_il' and e.value like '2026-11-03:%'"""
        ).fetchall()
        for candidacy, option, person, _, contest in rows:
            local = conn.execute(
                """select y.id, y.person_id, y.option_id from candidacy y
                   join changeset ch on ch.id = y.changeset_id
                   join contest_option o on o.id = y.option_id
                   where ch.pipeline = 'us.local_2026' and o.contest_id = %s
                     and (y.option_id = %s or lower(regexp_replace(o.ballot_label, '[^a-zA-Z]', '', 'g')) = (
                         select lower(regexp_replace(replace(x.ballot_label, '&quot;', ''), '[^a-zA-Z]', '', 'g'))
                         from contest_option x where x.id = %s))""",
                [contest, option, option],
            ).fetchall()
            if len(local) != 1:
                print(f"{candidacy}: {len(local)} local_2026 matches")
                continue
            local_candidacy, local_person, local_option = local[0]
            history = conn.execute(
                "select count(*) from candidacy where person_id = %s and id <> %s", [person, candidacy]
            ).fetchone()[0]
            if option != local_option:
                cs.merge(option, local_option)
            cs.merge(candidacy, local_candidacy)
            if history:
                cs.merge(local_person, person)
            else:
                cs.merge(person, local_person)
        return cs.write()
