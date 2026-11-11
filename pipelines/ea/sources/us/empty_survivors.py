from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.empty_survivors"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge live elections that hold no contest into the same polity's election on the same day that does, when an earlier merge made the empty one a survivor",
            polity="US",
            kind="correction",
        )
        rows = conn.execute(
            """select e.id, array(select o.id from election o join entity oe on oe.id = o.id and oe.retired_by is null
                             where o.polity_id = e.polity_id and o.date_from = e.date_from and o.id <> e.id
                               and not exists (select from entity_merge m where m.entity_id = o.id)
                               and exists (select from contest c join entity ce on ce.id = c.id and ce.retired_by is null
                                           where c.election_id = o.id
                                             and not exists (select from entity_merge cm where cm.entity_id = c.id))
                             order by (o.kind = e.kind) desc, o.id limit 2)
               from election e join entity en on en.id = e.id and en.retired_by is null
               where exists (select from entity_merge m where m.into_id = e.id)
                 and not exists (select from entity_merge m where m.entity_id = e.id)
                 and not exists (select from election k join entity ke on ke.id = k.id and ke.retired_by is null
                                 where k.parent_id = e.id)
                 and not exists (select from contest c join entity ce on ce.id = c.id and ce.retired_by is null
                                 where c.election_id = e.id
                                   and not exists (select from entity_merge cm where cm.entity_id = c.id))"""
        ).fetchall()
        merged = 0
        for election, targets in rows:
            if len(targets) != 1:
                print(f"{election}: {len(targets)} candidate elections, skipped")
                continue
            cs.merge(election, targets[0])
            merged += 1
        print(f"{merged} of {len(rows)} elections merged")
        return cs.write()
