from collections import defaultdict
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.answer_merges"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge ballot answers left twice under one question after a pipeline folded a re-keyed copy of the question into its corrected one: the copy's Yes and No stayed behind with only its withdrawn results, so the question listed each answer twice",
            polity="US",
            kind="correction",
        )
        groups: dict[tuple[UUID, str], list[tuple[UUID, int, set[int], bool]]] = defaultdict(list)
        for contest, label, option, k, sets, used in conn.execute(
            """with live as (
                 select o.id, o.k, o.contest_id, lower(o.ballot_label) as label
                 from contest_option o join entity e on e.id = o.id
                 join contest c on c.id = o.contest_id join changeset g on g.id = c.changeset_id
                 where o.kind = 'answer' and o.ballot_label is not null and e.retired_by is null
                   and g.pipeline like 'us.%'
                   and not exists (select from entity_merge m where m.entity_id = o.id)
               )
               select l.contest_id, l.label, l.id, l.k,
                      array(select distinct r.k from result_set r join result_value v on v.result_set_k = r.k
                            where r.contest_id = l.contest_id and v.option_k = l.k),
                      exists (select from result_set r join result_value v on v.result_set_k = r.k
                              where r.contest_id = l.contest_id and r.status = 'active' and v.option_k = l.k)
               from live l
               where (l.contest_id, l.label) in (select contest_id, label from live group by 1, 2 having count(*) > 1)"""
        ).fetchall():
            groups[(contest, label)].append((option, k, set(sets), used))
        skipped = 0
        for (contest, label), options in groups.items():
            active = [option for option in options if option[3]]
            if len(active) > 1:
                skipped += 1
                print(f"  {contest} {label}: {len(active)} answers carry active results")
                continue
            survivor = active[0] if active else min(options, key=lambda option: option[1])
            for option in options:
                if option is survivor:
                    continue
                if option[2] & survivor[2]:
                    skipped += 1
                    print(f"  {contest} {label}: one result set counts both answers")
                    continue
                cs.merge(option[0], survivor[0])
        print(f"{len(groups)} repeated answers, {skipped} left alone")
        return cs.write()
