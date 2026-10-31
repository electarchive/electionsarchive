from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.enr_clarity_retire"
VERSION = "1"
REASON = (
    "Illinois Winnebago County contest written by us.enr_clarity changeset 01a16cae under the placeholder date "
    "01/01/0001 from Clarity files named not_set; the same contest exists under its real date"
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
        elections = [
            row[0] for row in conn.execute("select id from election where date_from < '1700-01-01'").fetchall()
        ]
        contests = [
            row[0]
            for row in conn.execute(
                """select c.id from contest c where c.election_id = any(%s::uuid[])
                   union
                   select s.entity_id from slug s join contest c on c.id = s.entity_id
                   join election el on el.id = c.election_id
                   join slug t on t.path = replace(s.path, '0001-01-01', el.date_from::text)
                   join contest d on d.id = t.entity_id and d.election_id = c.election_id and d.id <> c.id
                   where s.is_current and s.path like '%%0001-01-01%%'""",
                [elections],
            ).fetchall()
        ]
        for contest in contests:
            cs.add("entity_retirement", entity_id=contest, reason=REASON)
        for election in elections:
            cs.add("entity_retirement", entity_id=election, reason=REASON)
        stale = conn.execute(
            """select s.path, s.entity_id, el.date_from from slug s
               join election el on el.id = s.entity_id where s.is_current and s.path like %s
               union all
               select s.path, s.entity_id, el.date_from from slug s
               join contest co on co.id = s.entity_id join election el on el.id = co.election_id
               where s.is_current and s.path like %s""",
            ["%0001-01-01%", "%0001-01-01%"],
        ).fetchall()
        owners = dict(conn.execute("select path, entity_id from slug").fetchall())
        retiring = set(contests) | set(elections)
        for path, entity, day in stale:
            fixed = path.replace("0001-01-01", day.isoformat())
            if entity not in retiring and owners.get(fixed, entity) == entity:
                cs.add("slug", path=fixed, entity_id=entity)
        print(f"{len(elections)} elections, {len(contests)} contests, {len(stale)} slugs")
        return cs.write()
