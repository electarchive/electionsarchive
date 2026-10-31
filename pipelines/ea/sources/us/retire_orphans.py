import os
from pathlib import Path

from psycopg import sql

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.retire_orphans"
VERSION = "1"
OWNED = (
    "candidacy",
    "name",
    "note",
    "external_id",
    "slug",
    "label",
    "flag",
    "search_document",
    "entity_retirement",
    "name_drop",
    "external_id_drop",
    "note_drop",
    "entity_subtype_drop",
    "unit_area_drop",
    "person_image",
)


def emit() -> Path:
    pipelines = os.environ.get("RETIRE_PIPELINES", "us.maricopa,us.clark_nv").split(",")
    paths = []
    with connect() as conn:
        conn.execute("set statement_timeout = '600s'")
        references = conn.execute(
            """select distinct c.conrelid::regclass::text, a.attname from pg_constraint c
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
               where c.contype = 'f' and c.connamespace = 'public'::regnamespace
                 and c.confrelid = 'public.person'::regclass and not c.conrelid::regclass::text = any(%s)""",
            [list(OWNED)],
        ).fetchall()
        for pipeline in pipelines:
            cs = Changeset(
                conn,
                pipeline=pipeline,
                version=VERSION,
                summary=f"Retire {pipeline}'s superseded options, candidacies, and people that hold no facts",
                polity="US",
            )
            options = conn.execute(
                """select o.id, o.contest_id, o.k from contest_option o join changeset c on c.id = o.changeset_id
                   where c.pipeline = %s
                     and not exists (select from result_value v where v.option_k = o.k)
                     and not exists (select from outcome t where t.option_id = o.id)
                     and not exists (select from contest_option x where x.parent_option_id = o.id)
                     and exists (select from result_set r join result_value v on v.result_set_k = r.k
                                 where r.contest_id = o.contest_id and r.changeset_id in
                                   (select id from changeset where pipeline = %s))""",
                [pipeline, pipeline],
            ).fetchall()
            option_ids = [id for id, _, _ in options]
            candidacies = [
                id
                for (id,) in conn.execute(
                    "select id from candidacy where option_id = any(%s::uuid[])", [option_ids]
                ).fetchall()
            ]
            people = [
                person
                for (person,) in conn.execute(
                    """select p.id from person p join changeset c on c.id = p.changeset_id
                       where c.pipeline = %s
                         and (not exists (select from candidacy y where y.person_id = p.id)
                              or (exists (select from candidacy y where y.person_id = p.id and y.id = any(%s::uuid[]))
                                  and not exists (select from candidacy y where y.person_id = p.id and not y.id = any(%s::uuid[]))))
                         and (%s = '' or exists (select from name q where q.entity_id = p.id and q.text ~ %s))
                         and not exists (select from entity_merge m where m.entity_id = p.id or m.into_id = p.id)
                         and not exists (select from name n join changeset w on w.id = n.changeset_id
                                         where n.entity_id = p.id and w.pipeline <> all(%s))
                         and not exists (select from external_id x join changeset w on w.id = x.changeset_id
                                         where x.entity_id = p.id and w.pipeline <> %s)""",
                    [
                        pipeline,
                        candidacies,
                        candidacies,
                        os.environ.get("RETIRE_NAMES", ""),
                        os.environ.get("RETIRE_NAMES", ""),
                        [pipeline, "shorts", "identity"],
                        pipeline,
                    ],
                ).fetchall()
            ]
            for relation, column in references:
                held = {
                    id
                    for (id,) in conn.execute(
                        sql.SQL("select distinct {} from public.{} where {} = any(%s::uuid[])").format(
                            sql.Identifier(column), sql.Identifier(relation), sql.Identifier(column)
                        ),
                        [people],
                    ).fetchall()
                }
                people = [p for p in people if p not in held]
            retired_people = set(people)
            future = conn.execute(
                """select k.id from contest k join changeset c on c.id = k.changeset_id
                   join election e on e.id = k.election_id
                   where c.pipeline = %s and e.date_from > current_date
                     and exists (select from result_set r where r.contest_id = k.id)
                     and not exists (select from result_set r join changeset w on w.id = r.changeset_id
                                     where r.contest_id = k.id and w.pipeline <> %s)""",
                [pipeline, pipeline],
            ).fetchall()
            for pattern in filter(None, os.environ.get("RETIRE_SLUGS", "").split(",")):
                future += conn.execute(
                    """select k.id from contest k join changeset c on c.id = k.changeset_id
                       join slug s on s.entity_id = k.id and s.is_current
                       where c.pipeline = %s and s.path ~ %s""",
                    [pipeline, pattern],
                ).fetchall()
            for (contest,) in future:
                cs.add("entity_retirement", entity_id=contest, reason="Results dated before the election was held")
            for row in conn.execute(
                """select r.id, r.election_id, r.contest_id, r.stage, r.kind, r.source_id, r.completeness
                   from result_set r join changeset w on w.id = r.changeset_id
                   join election e on e.id = r.election_id
                   where w.pipeline = %s and e.date_from > current_date and r.status = 'active'
                     and r.contest_id <> all(%s::uuid[])""",
                [pipeline, [contest for (contest,) in future]],
            ).fetchall():
                cs.add(
                    "result_set",
                    id=row[0],
                    election_id=row[1],
                    contest_id=row[2],
                    stage=row[3],
                    kind=row[4],
                    status="withdrawn",
                    source_id=row[5],
                    is_default=False,
                    completeness=row[6],
                )
            for candidacy in candidacies:
                cs.add("entity_retirement", entity_id=candidacy, reason="Superseded by a re-emit")
            held = {
                id
                for (id,) in conn.execute(
                    "select option_id from candidacy where option_id = any(%s::uuid[])", [option_ids]
                ).fetchall()
            }
            for option, contest, _ in options:
                if option not in held:
                    cs.add("option_drop", option_id=option, contest_id=contest, reason="Superseded by a re-emit")
            for person in retired_people:
                cs.add("entity_retirement", entity_id=person, reason="Superseded by a re-emit")
            with (DATA / "logs" / "retire_orphans.tsv").open("a", encoding="utf-8") as log:
                log.write(
                    f"{pipeline}\toptions {len(options)}\tcandidacies {len(candidacies)}\tpeople {len(retired_people)}\tfuture contests {len(future)}"
                    f"\n"
                )
            print(pipeline, len(options), len(candidacies), len(retired_people), len(future))
            paths.append(cs.write())
    return paths[-1]
