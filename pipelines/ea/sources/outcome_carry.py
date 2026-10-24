from collections import defaultdict
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "outcome_carry"
VERSION = "1"
NOTE = "Winners are taken from {publisher}'s figures for this race, which name them; the figures shown here do not."


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Winners for contests whose default result set declares none while another set of the same contest "
                "does, carried onto the default set's options matched by candidate, then by ballot label"
            ),
            polity=None,
            kind="correction",
        )
        survivors = dict(conn.execute("select entity_id, into_id from entity_merge").fetchall())
        pairs = conn.execute(
            """select d.id, d.contest_id, d.k, s.id, coalesce(src.publisher, x.pipeline), x.pipeline, dx.pipeline
               from result_set d join changeset dx on dx.id = d.changeset_id
               cross join lateral (
                 select o.* from result_set o
                 where o.contest_id = d.contest_id and o.id <> d.id
                   and exists (select from outcome t where t.result_set_id = o.id)
                 order by o.id = d.supersedes_id desc, o.status = 'active' desc, o.id desc limit 1) s
               join changeset x on x.id = s.changeset_id
               left join source src on src.id = s.source_id
               where d.is_default and d.kind = 'counted' and d.contest_id is not null
                 and not exists (select from outcome t where t.result_set_id = d.id)"""
        ).fetchall()
        contests = [contest for _, contest, *_ in pairs]
        options: dict[UUID, list[tuple[UUID, str | None, UUID | None, UUID | None]]] = defaultdict(list)
        for contest, option, label, candidacy, person in conn.execute(
            """select o.contest_id, o.id, o.ballot_label, c.id, c.person_id from contest_option o
               left join candidacy c on c.option_id = o.id and c.role = 'head'
               where o.contest_id = any(%s::uuid[]) and o.parent_option_id is null""",
            [contests],
        ).fetchall():
            options[contest].append((option, label, candidacy, survivors.get(person, person)))
        counted: dict[int, set[UUID]] = defaultdict(set)
        for k, option in conn.execute(
            """select v.result_set_k, coalesce(o.parent_option_id, o.id) from result_value v join contest_option o on o.k = v.option_k
               where v.result_set_k = any(%s)""",
            [[k for _, _, k, *_ in pairs]],
        ).fetchall():
            counted[k].add(option)
        declared: dict[UUID, list[tuple[UUID, UUID | None, str, int | None, str | None]]] = defaultdict(list)
        for result_set, option, candidacy, kind, seats, notes in conn.execute(
            """select result_set_id, option_id, candidacy_id, kind::text, seats, notes from outcome
               where result_set_id = any(%s::uuid[])""",
            [[source for _, _, _, source, *_ in pairs]],
        ).fetchall():
            declared[result_set].append((option, candidacy, kind, seats, notes))
        heads = {option: (label, person) for listed in options.values() for option, label, _, person in listed}
        carried: dict[str, int] = defaultdict(int)
        held: dict[str, int] = defaultdict(int)
        for default, contest, k, source, publisher, origin, owner in pairs:
            rows = []
            for option, candidacy, kind, seats, notes in declared[source]:
                label, person = heads.get(option, (None, None))
                listed = [entry for entry in options[contest] if entry[0] in counted[k]]
                if option in counted[k]:
                    target = [entry for entry in listed if entry[0] == option]
                else:
                    target = [entry for entry in listed if person is not None and entry[3] == person]
                    if not target:
                        target = [entry for entry in listed if label is not None and entry[1] == label]
                if len(target) != 1:
                    break
                matched, _, head, _ = target[0]
                rows.append(
                    (matched, candidacy if matched == option else head if candidacy else None, kind, seats, notes)
                )
            else:
                for option, candidacy, kind, seats, notes in rows:
                    cs.add(
                        "outcome",
                        result_set_id=default,
                        option_id=option,
                        candidacy_id=candidacy,
                        kind=kind,
                        seats=seats,
                        notes=notes,
                    )
                cs.add("note", entity_id=default, kind="methodology", body_md=NOTE.format(publisher=publisher))
                carried[owner] += 1
                print(f"{owner}: {default} <- {origin} {source}")
                continue
            held[owner] += 1
        for owner in sorted({*carried, *held}):
            print(f"{owner}: {carried[owner]} carried, {held[owner]} held")
        return cs.write()
