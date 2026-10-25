import io
import json
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import LiteralString, cast

import polars as pl
from psycopg import sql
from psycopg.types.json import Jsonb

from ea import checks, coverage_tables
from ea.checks import FACTS, KEYS, Stage
from ea.store import DATA, Connection, connect

MERGE_LOCK = 1700949316


def _stage(conn: Connection, changeset_id: str) -> Stage:
    row = conn.execute(
        "select pipeline, scope ->> 'polity', artifact_keys, scope -> 'replaces', scope -> 'clears' from changeset where id = %s",
        [changeset_id],
    ).fetchone()
    assert row is not None, changeset_id
    pipeline, polity, keys, replaces, clears = row
    schema = f"cs_{changeset_id.replace('-', '')}"
    tables: dict[str, list[str]] = {}
    for key in keys:
        table = Path(key).stem
        tables[table] = [
            column
            for (column,) in conn.execute(
                """select attname from pg_attribute
                   where attrelid = %s::regclass and attnum > 0 and not attisdropped order by attnum""",
                [f'"{schema}"."{table}"'],
            ).fetchall()
        ]
    entities = {
        table
        for (table,) in conn.execute(
            """select c.relname from pg_trigger t join pg_class c on c.oid = t.tgrelid
               where t.tgname = 'ea_entity'"""
        ).fetchall()
    }
    temporal: dict[str, list[str]] = {
        table: keys
        for table, keys in conn.execute(
            """select c.conrelid::regclass::text, array_agg(a.attname order by k.ordinality)
               from pg_constraint c
               cross join lateral unnest(c.conkey) with ordinality k(attnum, ordinality)
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
               where c.conperiod and c.connamespace = 'public'::regnamespace and a.attname <> 'valid'
               group by c.conrelid"""
        ).fetchall()
        if table in tables and "id" not in tables[table]
    }
    parents: dict[str, str] = {
        table: parent
        for table, parent in conn.execute(
            """select distinct on (c.conrelid) c.conrelid::regclass::text, a.attname from pg_constraint c
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
               where c.contype in ('p', 'u') and c.connamespace = 'public'::regnamespace
                 and not exists (select from pg_attribute i
                                 where i.attrelid = c.conrelid and i.attname = 'id' and not i.attisdropped)
               order by c.conrelid, c.contype"""
        ).fetchall()
        if table in tables
        and table not in temporal
        and table
        not in (
            *FACTS,
            "external_id",
            "slug",
            "entity_merge",
            "entity_retirement",
            "entity_subtype_drop",
            "name_drop",
            "membership_drop",
            "unit_area_drop",
            "note_drop",
            "option_drop",
        )
    }
    owners: dict[str, str] = {
        table: owner
        for table, owner in conn.execute(
            """select f.conrelid::regclass::text, f.confrelid::regclass::text from pg_constraint f
               where f.contype = 'f' and f.connamespace = 'public'::regnamespace
                 and not exists (select from pg_attribute i
                                 where i.attrelid = f.conrelid and i.attname = 'changeset_id' and not i.attisdropped)"""
        ).fetchall()
        if table in parents
    }
    nullable = {table: set(keys) - _not_null(conn, table) for table, keys in temporal.items()}
    return Stage(
        schema, tables, entities, pipeline, polity, temporal, parents, owners, replaces or {}, nullable, clears or []
    )


def _not_null(conn: Connection, table: str) -> set[str]:
    return {
        column
        for (column,) in conn.execute(
            "select attname from pg_attribute where attrelid = %s::regclass and attnum > 0 and attnotnull",
            [f"public.{table}"],
        ).fetchall()
    }


def _diff(conn: Connection, stage: Stage) -> dict[str, dict[str, int]]:
    diff: dict[str, dict[str, int]] = {}
    for table, columns in stage.tables.items():
        staged = stage.staged(table)
        target = sql.Identifier(table)
        compared = [column for column in columns if column != "id"]
        if table in FACTS:
            query = sql.SQL(
                "select count(*), 0, 0, (select count(*) from public.{} where result_set_k in ({})) from {}"
            ).format(target, stage.replaced(table), staged)
        elif "id" in columns:
            query = sql.SQL(
                """select count(*) filter (where s.id is not null and p.id is null or s.id is null and not {present}),
                          count(*) filter (where p.id is not null and ({current}) is distinct from ({incoming})),
                          count(*) filter (where p.id is not null and ({current}) is not distinct from ({incoming})
                                              or s.id is null and {present})
                   from {staged} s left join public.{target} p on p.id = s.id"""
            ).format(
                current=sql.SQL(", ").join(sql.SQL("p.{}").format(sql.Identifier(c)) for c in compared or ["id"]),
                incoming=sql.SQL(", ").join(sql.SQL("s.{}").format(sql.Identifier(c)) for c in compared or ["id"]),
                present=_present(conn, table, compared) if compared else sql.SQL("false"),
                staged=staged,
                target=target,
            )
        elif table == "external_id":
            query = sql.SQL(
                """select count(*) filter (where p.value is null),
                          count(*) filter (where p.entity_id <> s.entity_id),
                          count(*) filter (where p.entity_id = s.entity_id)
                   from {} s left join public.external_id p using (scheme, value)"""
            ).format(staged)
        elif table == "slug":
            query = sql.SQL(
                """select count(*) filter (where p.path is null),
                          count(*) filter (where p.path is not null and not p.is_current),
                          count(*) filter (where p.is_current)
                   from {} s left join public.slug p on p.path = s.path and p.entity_id = s.entity_id"""
            ).format(staged)
        else:
            query = sql.SQL(
                "select count(*) filter (where not {present}), 0, count(*) filter (where {present}) from {staged} s"
            ).format(present=_present(conn, table, columns), staged=staged)
        row = conn.execute(query).fetchone()
        assert row is not None
        diff[table] = {
            "insert": row[0],
            "update": row[1],
            "unchanged": row[2],
            "delete": row[3] if table in FACTS else 0,
        }
        if table in stage.parents:
            deleted = conn.execute(
                sql.SQL("select count(*) from public.{} p, changeset c where {}").format(
                    sql.Identifier(table), stage.stale(table)
                )
            ).fetchone()
            assert deleted is not None
            diff[table]["delete"] = deleted[0]
    return diff


def _present(conn: Connection, table: str, columns: list[str]) -> sql.Composable:
    required = _not_null(conn, table)
    return sql.SQL("exists (select 1 from public.{} p where {})").format(
        sql.Identifier(table),
        sql.SQL(" and ").join(
            sql.SQL("p.{0} = s.{0}" if column in required else "p.{0} is not distinct from s.{0}").format(
                sql.Identifier(column)
            )
            for column in columns
        ),
    )


def _publish_table(conn: Connection, stage: Stage, table: str) -> None:
    columns = stage.tables[table]
    staged = stage.staged(table)
    target = sql.Identifier(table)
    if table in FACTS:
        joins: list[sql.Composable] = []
        selected: list[sql.Composable] = []
        inserted: list[sql.Composable] = []
        resolving: list[sql.Composable] = []
        unresolved: list[sql.Composable] = []
        for index, column in enumerate(columns):
            if column in KEYS:
                alias = sql.Identifier(f"j{index}")
                optional = KEYS[column] == "contest_option"
                resolving.append(
                    sql.SQL("left join public.{} {} on {}.id = s.{}").format(
                        sql.Identifier(KEYS[column]), alias, alias, sql.Identifier(column)
                    )
                )
                unresolved.append(
                    sql.SQL("count(*) filter (where {}{}.id is null)").format(
                        sql.SQL("s.{} is not null and ").format(sql.Identifier(column)) if optional else sql.SQL(""),
                        alias,
                    )
                )
                joins.append(
                    sql.SQL("{} join public.{} {} on {}.id = s.{}").format(
                        sql.SQL("left" if optional else ""),
                        sql.Identifier(KEYS[column]),
                        alias,
                        alias,
                        sql.Identifier(column),
                    )
                )
                selected.append(sql.SQL("coalesce({}.k, 0)" if optional else "{}.k").format(alias))
                inserted.append(sql.Identifier(f"{column.removesuffix('_id')}_k"))
            else:
                selected.append(sql.SQL("s.{}").format(sql.Identifier(column)))
                inserted.append(sql.Identifier(column))
        resolved = [column for column in columns if column in KEYS]
        if resolved:
            counts = conn.execute(
                sql.SQL("select {} from {} s {}").format(
                    sql.SQL(", ").join(unresolved), staged, sql.SQL(" ").join(resolving)
                )
            ).fetchone()
            assert counts is not None
            missing = {column: count for column, count in zip(resolved, counts, strict=True) if count}
            if missing:
                raise ValueError(
                    f"{table}: rows whose keys resolve to nothing would be dropped: "
                    + ", ".join(f"{count} by {column}" for column, count in missing.items())
                )
        replaced = [key for (key,) in conn.execute(stage.replaced(table)).fetchall()]
        if replaced and stage.clears:
            cleared = conn.execute(
                sql.SQL(
                    """select current_setting('ea.changeset_id'), r.id::text,
                              (select count(*) from public.{} v where v.result_set_k = r.k),
                              (select count(*) from {} s where s.result_set_id = r.id)
                       from public.result_set r where r.k = any(%s) and r.id = any(%s::uuid[]) order by r.id"""
                ).format(target, staged),
                [replaced, stage.clears],
            ).fetchall()
            with (DATA / "logs" / "clears.tsv").open("a", encoding="utf-8") as log:
                log.writelines(
                    f"{changeset}\t{table}\t{result_set}\t{removed}\t{restaged}\n"
                    for changeset, result_set, removed, restaged in cleared
                )
            print(
                f"{table}: cleared {sum(row[2] for row in cleared)} rows in {len(cleared)} listed sets, restaged {sum(row[3] for row in cleared)}"
            )
        if replaced:
            conn.execute(
                "update result_set set is_default = is_default where k = any(%s) and changeset_id <> ea_current_changeset()",
                [replaced],
            )
            for fact in (
                table,
                *(("result_rollup_sum", "result_rollup_unit", "rollup_coverage") if table == "result_value" else ()),
            ):
                conn.execute(
                    sql.SQL("delete from public.{} where result_set_k = any(%s)").format(sql.Identifier(fact)),
                    [replaced],
                )
        conn.execute(
            sql.SQL("insert into public.{} ({}) select {} from {} s {}").format(
                target,
                sql.SQL(", ").join(inserted),
                sql.SQL(", ").join(selected),
                staged,
                sql.SQL(" ").join(joins),
            )
        )
        return
    listed = sql.SQL(", ").join(map(sql.Identifier, columns))
    if table == "name" and "is_preferred" in columns:
        conn.execute(
            sql.SQL(
                """update public.name p set is_preferred = false from {} s, changeset c
                   where s.is_preferred and p.is_preferred and p.entity_id = s.entity_id and p.lang = s.lang
                     and p.text <> s.text and p.valid_from is null and p.valid_to is null{}{}
                     and c.id = p.changeset_id
                     and (c.pipeline = %s or (%s = 'identity'
                          and regexp_replace(lower(unaccent(p.text)), '[^a-z0-9]', '', 'g')
                            = regexp_replace(lower(unaccent(s.text)), '[^a-z0-9]', '', 'g')))"""
            ).format(
                staged,
                sql.SQL(
                    " and p.script is not distinct from s.script" if "script" in columns else " and p.script is null"
                ),
                sql.SQL(
                    "".join(f" and s.{column} is null" for column in ("valid_from", "valid_to") if column in columns)
                ),
            ),
            [stage.pipeline, stage.pipeline],
        )
    if "id" in columns:
        compared = [column for column in columns if column != "id"]
        conflict = (
            sql.SQL("do update set {} where ({}) is distinct from ({}){}").format(
                sql.SQL(", ").join(sql.SQL("{0} = excluded.{0}").format(sql.Identifier(c)) for c in compared),
                sql.SQL(", ").join(sql.SQL("{}.{}").format(target, sql.Identifier(c)) for c in compared),
                sql.SQL(", ").join(sql.SQL("excluded.{}").format(sql.Identifier(c)) for c in compared),
                sql.SQL(
                    " or (select o.pipeline from changeset o where o.id = name.changeset_id) <> 'identity'"
                    if table == "name" and stage.pipeline == "identity"
                    else ""
                ),
            )
            if compared
            else sql.SQL("do nothing")
        )
        if table == "result_set":
            conn.execute(
                sql.SQL(
                    "update public.result_set p set is_default = false from {} s where s.id = p.id and p.is_default and not s.is_default"
                ).format(staged)
            )
        conn.execute(
            sql.SQL("insert into public.{} ({}) select {} from {} where id is not null on conflict (id) {}").format(
                target, listed, listed, staged, conflict
            )
        )
        if table == "name" and "is_preferred" in columns:
            conn.execute(
                sql.SQL(
                    """update public.name p set is_preferred = true from {} s, changeset c
                       where s.id is null and s.is_preferred and not p.is_preferred and {}
                         and c.id = p.changeset_id and c.pipeline = %s
                         and not exists (select from public.name q where q.entity_id = p.entity_id
                                         and q.text = p.text and q.lang = p.lang and q.is_preferred)"""
                ).format(
                    staged,
                    sql.SQL(" and ").join(
                        sql.SQL("p.{0} is not distinct from s.{0}").format(sql.Identifier(column))
                        for column in compared
                        if column != "is_preferred"
                    ),
                ),
                [stage.pipeline],
            )
        if compared:
            others = sql.SQL(", ").join(map(sql.Identifier, compared))
            conn.execute(
                sql.SQL("insert into public.{} ({}) select distinct {} from {} s where s.id is null and not {}").format(
                    target, others, others, staged, _present(conn, table, compared)
                )
            )
        return
    if table == "external_id":
        conn.execute(
            sql.SQL(
                """insert into public.external_id ({0}) select distinct {0} from {1}
                   on conflict (scheme, value) do update set entity_id = excluded.entity_id
                   where external_id.entity_id <> excluded.entity_id"""
            ).format(listed, staged)
        )
        return
    if table == "entity_merge":
        if conn.execute(
            sql.SQL(
                """select exists (
                     select from {0} s join entity a on a.id = s.entity_id join entity b on b.id = s.into_id
                     where a.type <> b.type or s.into_id in (select entity_id from {0} union select entity_id from entity_merge)
                   )"""
            ).format(staged)
        ).fetchone() != (False,):
            raise ValueError("a merge joins two types or targets an entity that is itself merged")
        conn.execute(
            sql.SQL(
                """with merging as (select entity_id from {0} union select into_id from {0}),
                   moving as (
                     select r.id, coalesce(e.into_id, r.election_id) as election, coalesce(c.into_id, r.contest_id) as contest,
                            r.stage, o.kind = 'official_body' as official, e.entity_id is null and c.entity_id is null as staying
                     from public.result_set r join public.source o on o.id = r.source_id
                     left join {0} e on e.entity_id = r.election_id
                     left join {0} c on c.entity_id = r.contest_id
                     where r.is_default and (r.election_id in (select entity_id from merging)
                                             or r.contest_id in (select entity_id from merging))
                   )
                   update public.result_set r set is_default = false
                   from (select id, row_number() over (partition by election, contest, stage
                                                       order by official desc, staying desc, id) as n from moving) x
                   where x.id = r.id and x.n > 1"""
            ).format(staged)
        )
        types = [
            kind
            for (kind,) in conn.execute(
                sql.SQL("select distinct e.type::text from {} s join entity e on e.id = s.entity_id").format(staged)
            ).fetchall()
        ]
        periods: dict[str, list[str]] = dict(
            conn.execute(
                """select c.conrelid::regclass::text, array_agg(a.attname order by k.ordinality)
                   from pg_constraint c
                   cross join lateral unnest(c.conkey) with ordinality k(attnum, ordinality)
                   join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
                   where c.conperiod and c.contype in ('p', 'u') and c.connamespace = 'public'::regnamespace
                     and a.attname <> 'valid'
                   group by c.conrelid"""
            ).fetchall()
        )
        keyed: dict[str, list[list[str]]] = {}
        for owner, key in conn.execute(
            """select c.conrelid::regclass::text, array_agg(a.attname order by k.ordinality)
               from pg_constraint c
               cross join lateral unnest(c.conkey) with ordinality k(attnum, ordinality)
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
               where c.contype in ('p', 'u') and not c.conperiod and c.connamespace = 'public'::regnamespace
               group by c.oid, c.conrelid"""
        ).fetchall():
            keyed.setdefault(owner, []).append(key)
        for referencing, column in conn.execute(
            """select c.conrelid::regclass::text, a.attname from pg_constraint c
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
               where c.contype = 'f' and c.connamespace = 'public'::regnamespace
                 and c.confrelid::regclass::text = any(%s) and a.attname <> 'id'
                 and c.conrelid::regclass::text <> 'search_document'
                 and (c.conrelid::regclass::text, a.attname) <> ('entity_merge', 'entity_id')""",
            [["entity", *types]],
        ).fetchall():
            moved = sql.Identifier(column)
            for key in keyed.get(referencing, []):
                if column in key:
                    conn.execute(
                        sql.SQL(
                            """delete from public.{target} t using {staged} s where t.{moved} = s.entity_id
                               and exists (select from public.{target} x where x.{moved} = s.into_id and {same})"""
                        ).format(
                            target=sql.Identifier(referencing),
                            moved=moved,
                            staged=staged,
                            same=sql.SQL(" and ").join(
                                [
                                    sql.SQL("true"),
                                    *(
                                        sql.SQL("x.{0} is not distinct from t.{0}").format(sql.Identifier(other))
                                        for other in key
                                        if other != column
                                    ),
                                ]
                            ),
                        )
                    )
            conn.execute(
                sql.SQL(
                    "update public.{target} t set {moved} = s.into_id{extra} from {staged} s where t.{moved} = s.entity_id{guard}"
                ).format(
                    target=sql.Identifier(referencing),
                    moved=moved,
                    extra=sql.SQL(
                        ", is_preferred = false"
                        if referencing == "name"
                        else ", is_current = false"
                        if referencing == "slug"
                        else ""
                    ),
                    staged=staged,
                    guard=sql.SQL(
                        " and not exists (select from public.{} x where x.{} = s.into_id and x.valid && t.valid{})"
                    ).format(
                        sql.Identifier(referencing),
                        moved,
                        sql.SQL("").join(
                            sql.SQL(" and x.{0} is not distinct from t.{0}").format(sql.Identifier(key))
                            for key in periods[referencing]
                            if key != column
                        ),
                    )
                    if column in periods.get(referencing, [])
                    else sql.SQL(""),
                )
            )
        for relation, column, parent in checks.references(conn):
            if parent not in types:
                continue
            others = [
                key
                for (key,) in conn.execute(
                    """select a.attname from pg_constraint c cross join lateral unnest(c.conkey) k(attnum)
                       join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
                       where c.conrelid = %s::regclass and c.contype = 'p'""",
                    [f"public.{relation}"],
                ).fetchall()
                if key != column
            ]
            moving = sql.SQL(
                """from {staged} s join public.{parent} duplicate on duplicate.id = s.entity_id
                   join public.{parent} survivor on survivor.id = s.into_id"""
            ).format(staged=staged, parent=sql.Identifier(parent))
            rekeyed = sql.Identifier(relation)
            moved = sql.Identifier(column)
            if conn.execute(
                sql.SQL("select exists (select {} join public.{} t on t.{} = duplicate.k)").format(
                    moving, rekeyed, moved
                )
            ).fetchone() != (True,):
                continue
            if conn.execute(
                sql.SQL(
                    """select exists (select {moving} join public.{target} t on t.{moved} = duplicate.k
                       join public.{target} x on x.{moved} = survivor.k and {same})"""
                ).format(
                    moving=moving,
                    target=rekeyed,
                    moved=moved,
                    same=sql.SQL(" and ").join(sql.SQL("x.{0} = t.{0}").format(sql.Identifier(key)) for key in others),
                )
            ).fetchone() != (False,):
                raise ValueError(
                    f"merging these {parent} rows would give {relation} two rows for one key; settle the duplicate "
                    "values first"
                )
            conn.execute(
                sql.SQL(
                    """with moved as (
                         update public.{target} t set {moved} = survivor.k {moving} where t.{moved} = duplicate.k
                         returning {others}, duplicate.k as from_k, survivor.k as to_k
                       )
                       insert into fact_rekey (changeset_id, relation, key_column, from_k, to_k, row_key)
                       select current_setting('ea.changeset_id')::uuid, {relation}, {column}, from_k, to_k,
                              jsonb_build_object({row_key})
                       from moved"""
                ).format(
                    target=rekeyed,
                    moved=moved,
                    moving=moving,
                    others=sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(key)) for key in others),
                    relation=sql.Literal(relation),
                    column=sql.Literal(column),
                    row_key=sql.SQL(", ").join(
                        sql.SQL("{}, {}").format(sql.Literal(key), sql.Identifier(key)) for key in others
                    ),
                )
            )
        for kind in types:
            filled = [
                sql.Identifier(attribute)
                for (attribute,) in conn.execute(
                    """select attname from pg_attribute
                       where attrelid = %s::regclass and attnum > 0 and not attisdropped and attgenerated = ''
                         and attidentity = '' and attname not in ('id', 'changeset_id', 'recorded_from')""",
                    [f"public.{kind}"],
                ).fetchall()
            ]
            if filled:
                merged = sql.SQL(", ").join(sql.SQL("coalesce(p.{0}, l.{0})").format(column) for column in filled)
                conn.execute(
                    sql.SQL(
                        """update public.{kind} p set ({columns}) = row({merged})
                           from {staged} s join public.{kind} l on l.id = s.entity_id
                           where p.id = s.into_id and ({current}) is distinct from ({merged})"""
                    ).format(
                        kind=sql.Identifier(kind),
                        columns=sql.SQL(", ").join(filled),
                        merged=merged,
                        staged=staged,
                        current=sql.SQL(", ").join(sql.SQL("p.{}").format(column) for column in filled),
                    )
                )
            if kind in ("contest_option", "candidacy", "contest"):
                conn.execute(
                    sql.SQL("delete from public.{} where id in (select entity_id from {})").format(
                        sql.Identifier(kind), staged
                    )
                )
        conn.execute(
            sql.SQL("delete from search_document where entity_id in (select entity_id from {})").format(staged)
        )
    if table == "entity_retirement":
        retiring: dict[str, list[str]] = {}
        for entity, kind, retired in conn.execute(
            sql.SQL(
                "select s.entity_id::text, e.type::text, e.retired_by from {} s join entity e on e.id = s.entity_id"
            ).format(staged)
        ).fetchall():
            if retired is not None:
                raise ValueError(f"{entity} is already retired by {retired}")
            retiring.setdefault(kind, []).append(entity)
        held: list[str] = []
        for kind, ids in sorted(retiring.items(), key=lambda item: item[0] != "contest"):
            if kind == "contest":
                for parent, query in (
                    ("result_set", "select k from public.result_set where contest_id = any(%s::uuid[])"),
                    ("contest_option", "select k from public.contest_option where contest_id = any(%s::uuid[])"),
                ):
                    for relation, column, owner in checks.references(conn):
                        if owner == parent:
                            conn.execute(
                                sql.SQL("delete from public.{} where {} in ({})").format(
                                    sql.Identifier(relation), sql.Identifier(column), sql.SQL(query)
                                ),
                                [ids],
                            )
                conn.execute("delete from public.ballot_question where contest_id = any(%s::uuid[])", [ids])
                conn.execute("delete from public.result_set where contest_id = any(%s::uuid[])", [ids])
                conn.execute(
                    "delete from public.candidacy where option_id in (select id from public.contest_option where contest_id = any(%s::uuid[]))",
                    [ids],
                )
                conn.execute("delete from public.contest_option where contest_id = any(%s::uuid[])", [ids])
            if kind == "person":
                conn.execute("delete from public.person_image where person_id = any(%s::uuid[])", [ids])
            if kind == "tenure":
                for owned, column in (
                    ("tenure_affiliation", "tenure_id"),
                    ("tenure_event", "tenure_id"),
                    ("citation", "entity_id"),
                ):
                    conn.execute(
                        sql.SQL("delete from public.{} where {} = any(%s::uuid[])").format(
                            sql.Identifier(owned), sql.Identifier(column)
                        ),
                        [ids],
                    )
            for relation, column in conn.execute(
                """select distinct c.conrelid::regclass::text, a.attname from pg_constraint c
                   join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
                   where c.contype = 'f' and c.connamespace = 'public'::regnamespace
                     and c.confrelid::regclass::text = any(%s)
                     and c.conrelid::regclass::text not in
                       ('name', 'note', 'external_id', 'slug', 'label', 'flag', 'search_document', 'entity_retirement',
                        'name_drop', 'note_drop', 'entity_subtype_drop')
                     and (c.conrelid::regclass::text, a.attname) <> (%s, 'id')
                     and (c.conrelid::regclass::text, c.confrelid::regclass::text)
                       <> ('candidacy_nomination', 'candidacy')""",
                [["entity", kind], kind],
            ).fetchall():
                held += [
                    f"{relation}.{column} {entity} ({rows} rows)"
                    for entity, rows in conn.execute(
                        sql.SQL(
                            "select {0}::text, count(*) from public.{1} where {0} = any(%s::uuid[]) {2} group by 1"
                        ).format(
                            sql.Identifier(column),
                            sql.Identifier(relation),
                            sql.SQL("and id <> all(%s::uuid[])" if relation == kind else ""),
                        ),
                        [ids, ids] if relation == kind else [ids],
                    ).fetchall()
                ]
            for relation, column, parent in checks.references(conn):
                if parent == kind:
                    held += [
                        f"{relation}.{column} {entity} ({rows} rows)"
                        for entity, rows in conn.execute(
                            sql.SQL(
                                """select p.id::text, count(*) from public.{} p join public.{} t on t.{} = p.k
                                   where p.id = any(%s::uuid[]) group by 1"""
                            ).format(sql.Identifier(parent), sql.Identifier(relation), sql.Identifier(column)),
                            [ids],
                        ).fetchall()
                    ]
            if held:
                raise ValueError("retiring entities still referenced: " + "; ".join(held[:10]))
            conn.execute(
                sql.SQL("delete from public.{} where id = any(%s::uuid[])").format(sql.Identifier(kind)), [ids]
            )
        for owned in ("name", "slug", "external_id", "label", "search_document"):
            conn.execute(
                sql.SQL("delete from public.{} where entity_id in (select entity_id from {})").format(
                    sql.Identifier(owned), staged
                )
            )
        conn.execute(
            sql.SQL(
                """update entity set retired_by = current_setting('ea.changeset_id')::uuid
                   where id in (select entity_id from {})"""
            ).format(staged)
        )
        conn.execute(
            sql.SQL("insert into note (entity_id, kind, body_md) select entity_id, 'retired', reason from {}").format(
                staged
            )
        )
        conn.execute(
            sql.SQL(
                """update changeset set rationale = (select string_agg(distinct reason, '; ') from {})
                   where id = current_setting('ea.changeset_id')::uuid"""
            ).format(staged)
        )
    if table == "entity_subtype_drop":
        for entity, subtype, kind in conn.execute(
            sql.SQL(
                "select s.entity_id::text, s.subtype, e.type::text from {} s join entity e on e.id = s.entity_id"
            ).format(staged)
        ).fetchall():
            if subtype == kind or subtype not in stage.entities:
                raise ValueError(f"{entity} is a {kind}; {subtype} is not a stray subtype row to drop")
            if subtype == "person":
                conn.execute("delete from public.person_image where person_id = %s", [entity])
            held = [
                f"{relation}.{column} ({rows} rows)"
                for relation, column in conn.execute(
                    """select distinct c.conrelid::regclass::text, a.attname from pg_constraint c
                       join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
                       where c.contype = 'f' and c.confrelid = %s::regclass""",
                    [subtype],
                ).fetchall()
                for (rows,) in conn.execute(
                    sql.SQL("select count(*) from public.{} where {} = %s").format(
                        sql.Identifier(relation), sql.Identifier(column)
                    ),
                    [entity],
                ).fetchall()
                if rows
            ]
            if held:
                raise ValueError(f"dropping {subtype} row of {entity} still referenced: " + "; ".join(held))
            conn.execute(sql.SQL("delete from public.{} where id = %s").format(sql.Identifier(subtype)), [entity])
    if table == "membership_drop":
        for person, party, start, end, writer in conn.execute(
            sql.SQL(
                """select s.person_id::text, s.party_id::text, s.valid_from, s.valid_to, c.pipeline from {} s
                   left join public.person_party_membership m on m.person_id = s.person_id and m.party_id = s.party_id
                     and m.valid_from is not distinct from s.valid_from and m.valid_to is not distinct from s.valid_to
                   left join changeset c on c.id = m.changeset_id"""
            ).format(staged)
        ).fetchall():
            if writer != stage.pipeline:
                raise ValueError(
                    f"membership of {person} in {party} from {start} to {end} is "
                    + ("missing" if writer is None else f"written by {writer}, not {stage.pipeline}")
                )
        conn.execute(
            sql.SQL(
                """delete from public.person_party_membership m using {} s
                   where m.person_id = s.person_id and m.party_id = s.party_id
                     and m.valid_from is not distinct from s.valid_from and m.valid_to is not distinct from s.valid_to"""
            ).format(staged)
        )
    if table == "name_drop":
        for name, entity, text, held, preferred in conn.execute(
            sql.SQL(
                """select s.name_id::text, s.entity_id::text, s.text, n.entity_id::text, n.is_preferred
                   from {} s left join public.name n on n.id = s.name_id and n.text = s.text"""
            ).format(staged)
        ).fetchall():
            if held != entity:
                raise ValueError(f"name {name} is not {text!r} on {entity}")
            if (
                preferred
                and not conn.execute(
                    "select 1 from public.name where entity_id = %s and id <> %s", [entity, name]
                ).fetchone()
                and not (
                    "name" in stage.tables
                    and conn.execute(
                        sql.SQL("select 1 from {} where entity_id = %s and is_preferred").format(stage.staged("name")),
                        [entity],
                    ).fetchone()
                )
            ):
                raise ValueError(f"name {name} ({text!r}) is the only name of {entity}")
        conn.execute(sql.SQL("delete from public.name where id in (select name_id from {})").format(staged))
        conn.execute(
            sql.SQL(
                """update public.name n set is_preferred = true
                   from (select distinct on (n.entity_id) n.id from public.name n
                         where n.entity_id in (select entity_id from {}) and n.valid_from is null and n.valid_to is null
                           and not exists (select from public.name o where o.entity_id = n.entity_id and o.is_preferred)
                         order by n.entity_id, n.recorded_from desc) pick
                   where n.id = pick.id"""
            ).format(staged)
        )
    if table == "note_drop":
        for note, entity, held, writer in conn.execute(
            sql.SQL(
                """select s.note_id::text, s.entity_id::text, n.entity_id::text, c.pipeline from {} s
                   left join public.note n on n.id = s.note_id and n.body_md = s.body_md
                   left join changeset c on c.id = n.changeset_id"""
            ).format(staged)
        ).fetchall():
            if held != entity:
                raise ValueError(f"note {note} is not that text on {entity}")
            if writer != stage.pipeline:
                raise ValueError(f"note {note} was written by {writer}, not {stage.pipeline}")
        conn.execute(sql.SQL("delete from public.note where id in (select note_id from {})").format(staged))
    if table == "option_drop":
        for option, contest, k, held, writer in conn.execute(
            sql.SQL(
                """select s.option_id::text, s.contest_id::text, o.k, o.contest_id::text, c.pipeline from {} s
                   left join public.contest_option o on o.id = s.option_id
                   left join changeset c on c.id = o.changeset_id"""
            ).format(staged)
        ).fetchall():
            if held != contest:
                raise ValueError(f"option {option} is not in contest {contest}")
            if writer != stage.pipeline:
                raise ValueError(f"option {option} was written by {writer}, not {stage.pipeline}")
            sets = [
                row[0]
                for row in conn.execute("select k from public.result_set where contest_id = %s", [contest]).fetchall()
            ]
            used = [
                f"{relation}.{column} ({rows} rows)"
                for relation, column in (
                    ("result_value", "option_k"),
                    ("result_flow", "option_k"),
                    ("result_flow", "from_option_k"),
                    ("result_rollup", "option_k"),
                    ("value_locator", "option_k"),
                )
                for (rows,) in conn.execute(
                    sql.SQL("select count(*) from public.{} where result_set_k = any(%s) and {} = %s").format(
                        sql.Identifier(relation), sql.Identifier(column)
                    ),
                    [sets, k],
                ).fetchall()
                if rows
            ]
            for relation, column in (
                ("candidacy", "option_id"),
                ("outcome", "option_id"),
                ("contest_option", "parent_option_id"),
            ):
                (rows,) = conn.execute(
                    sql.SQL("select count(*) from public.{} where {} = %s").format(
                        sql.Identifier(relation), sql.Identifier(column)
                    ),
                    [option],
                ).fetchone()
                if rows:
                    used.append(f"{relation}.{column} ({rows} rows)")
            if used:
                raise ValueError(f"option {option} is still used: " + "; ".join(used))
        for relation in ("name", "note", "slug", "external_id"):
            conn.execute(
                sql.SQL("delete from public.{} where entity_id in (select option_id from {})").format(
                    sql.Identifier(relation), staged
                )
            )
        conn.execute(sql.SQL("delete from public.contest_option where id in (select option_id from {})").format(staged))
        conn.execute(sql.SQL("delete from public.entity where id in (select option_id from {})").format(staged))
    if table == "unit_area_drop":
        conn.execute(
            sql.SQL(
                """delete from public.reporting_unit_area p using {} s
                   where p.unit_id = s.unit_id and p.area_id = s.area_id"""
            ).format(staged)
        )
    if table == "slug":
        conn.execute(
            sql.SQL(
                """delete from public.slug p using {} s
                   where p.path = s.path and p.entity_id <> s.entity_id and not p.is_current"""
            ).format(staged)
        )
        conn.execute(
            sql.SQL(
                """update public.slug p set is_current = false from {} s
                   where p.entity_id = s.entity_id and p.is_current and p.path <> s.path"""
            ).format(staged)
        )
        conn.execute(
            sql.SQL(
                """update public.slug p set is_current = true from {} s
                   where p.entity_id = s.entity_id and not p.is_current and p.path = s.path"""
            ).format(staged)
        )
    if table in stage.temporal:
        conn.execute(
            sql.SQL(
                """delete from public.{target} p using {staged} s, changeset c
                   where c.id = p.changeset_id and c.pipeline = any({writers}) and {overlaps}"""
            ).format(target=target, staged=staged, writers=stage.writers(table), overlaps=stage.overlaps(table))
        )
    if table in stage.parents:
        if stage.parents[table] == "result_set_id" and stage.clears:
            cleared = conn.execute(
                sql.SQL(
                    """select current_setting('ea.changeset_id'), p.result_set_id::text, count(*)
                       from public.{} p, changeset c where {} and p.result_set_id = any(%s::uuid[]) group by 2 order by 2"""
                ).format(target, stage.stale(table)),
                [stage.clears],
            ).fetchall()
            with (DATA / "logs" / "clears.tsv").open("a", encoding="utf-8") as log:
                log.writelines(
                    f"{changeset}\t{table}\t{result_set}\t{removed}\t0\n" for changeset, result_set, removed in cleared
                )
            print(f"{table}: cleared {sum(row[2] for row in cleared)} rows in {len(cleared)} listed sets")
        conn.execute(sql.SQL("delete from public.{} p using changeset c where {}").format(target, stage.stale(table)))
    conn.execute(
        sql.SQL("insert into public.{} ({}) select distinct {} from {} s where not {}").format(
            target, listed, listed, staged, _present(conn, table, columns)
        )
    )


def _rollup(
    conn: Connection, stage: Stage | None, moved: list[str], repointed: list[str], sets: list[int] | None = None
) -> None:
    universe = stage is not None and "area_universe" in stage.tables
    if (
        (stage is None or "result_value" not in stage.tables)
        and not universe
        and not moved
        and not repointed
        and not sets
    ):
        return
    stale = [
        *(sets or []),
        *[
            key
            for (key,) in conn.execute(
                """select result_set_k from result_rollup
               where area_k in (select k from area where id = any(%s::uuid[]))
               union
               select v.result_set_k from result_value v join reporting_unit u on u.k = v.unit_k
               where u.id = any(%s::uuid[])""",
                [moved, repointed],
            ).fetchall()
        ],
        *(
            [
                key
                for (key,) in conn.execute(
                    sql.SQL(
                        """select r.result_set_k from result_rollup_sum r join area a on a.k = r.area_k
                           where a.id in (select area_id from {staged})
                           union
                           select r.result_set_k from result_rollup_unit r join area a on a.k = r.area_k
                           where a.id in (select area_id from {staged})"""
                    ).format(staged=stage.staged("area_universe"))
                ).fetchall()
            ]
            if stage is not None and universe
            else []
        ),
    ]
    conn.execute("select k from result_set where k = any(%s) order by k for update", [stale])
    conn.execute("delete from result_rollup_sum where result_set_k = any(%s)", [stale])
    conn.execute("delete from result_rollup_unit where result_set_k = any(%s)", [stale])
    conn.execute("delete from rollup_coverage where result_set_k = any(%s)", [stale])
    facts = (
        sql.SQL("select distinct result_set_id from {}").format(stage.staged("result_value"))
        if stage is not None and "result_value" in stage.tables
        else sql.SQL("select null::uuid")
    )
    conn.execute(
        sql.SQL(
            """with recursive
            rs as (
              select r.k,
                     case when c.notional then greatest(e.date_from, (select min(lower(p.valid)) from area_parent p
                                                                      where p.area_id = c.district_area_id))
                          else e.date_from end as on_date,
                     bs.valid_from as plan_from, c.id as contest_id, c.district_area_id, c.allocation_parent_id
              from result_set r join election e on e.id = r.election_id join contest c on c.id = r.contest_id
              left join boundary_set bs on bs.id = c.boundary_set_id
              where r.id in ({facts}) or r.k = any({stale})
            ),
            above as (
              select rs.k as result_set_k, p.parent_id as area_id, rs.on_date
              from rs join area_parent p on p.area_id = rs.district_area_id and p.valid @> rs.on_date
              where rs.allocation_parent_id is not null
              union
              select b.result_set_k, p.parent_id, b.on_date
              from above b join area_parent p on p.area_id = b.area_id and p.valid @> b.on_date
            ),
            ceiling as (
              select b.result_set_k, b.area_id from above b join rs on rs.k = b.result_set_k
              where exists (
                select from contest s join above t on t.result_set_k = b.result_set_k and t.area_id = s.district_area_id
                where s.allocation_parent_id = rs.allocation_parent_id and s.id <> rs.contest_id
              )
            ),
            vals as materialized (
              select v.result_set_k, v.unit_k, v.option_k, v.channel, v.measure, v.count_round, v.value
              from (select distinct k from rs) s cross join lateral (
                select * from result_value v where v.result_set_k = s.k offset 0) v
            ),
            units as (
              select rs.k as result_set_k, ru.k as unit_k, ru.id as unit_id,
                     coalesce(ru.area_id, pt.area_id) as area_id, rs.on_date
              from rs
              join (select distinct result_set_k, unit_k from vals) v
                on v.result_set_k = rs.k
              join reporting_unit ru on ru.k = v.unit_k
              left join polity_territory pt on pt.polity_id = ru.polity_id and pt.valid @> rs.on_date
            ),
            grain as (
              select distinct unit_k, unit_id, area_id, on_date from units
            ),
            links as materialized (
              select g.unit_k, g.area_id as own_id, g.on_date, ua.area_id, ua.partial,
                     not ua.partial and dated.scheme_code = own.scheme_code and dated.id <> own.id as rival
              from grain g join reporting_unit_area ua on ua.unit_id = g.unit_id and ua.valid @> g.on_date and not ua.approximate
              join area dated on dated.id = ua.area_id left join area own on own.id = g.area_id
            ),
            ancestors as (
              select unit_k, area_id, on_date, true as complete, area_id as origin_id from grain
              where not exists (
                select from links l where l.unit_k = grain.unit_k and l.on_date = grain.on_date
                  and l.own_id = grain.area_id and l.rival
              )
              union
              select unit_k, area_id, on_date, not partial, area_id from links
              union
              select a.unit_k, p.parent_id, a.on_date, a.complete, a.origin_id
              from ancestors a join area_parent p on p.area_id = a.area_id and p.valid @> a.on_date
            ),
            climbed as (
              select u.result_set_k, u.unit_k, a.area_id, bool_or(a.complete) as complete
              from (select distinct result_set_k, unit_k, coalesce(on_date, 'infinity') as day from units) u
              join rs on rs.k = u.result_set_k
              left join area district on district.id = rs.district_area_id
              join ancestors a on a.unit_k = u.unit_k and coalesce(a.on_date, 'infinity') = u.day
              left join area origin on origin.id = a.origin_id
              left join area reached on reached.id = a.area_id
              where district.id is null
                 or (origin.id = district.id or origin.scheme_code is distinct from district.scheme_code)
                and (reached.id = district.id or reached.scheme_code is distinct from district.scheme_code)
              group by u.result_set_k, u.unit_k, a.area_id
            ),
            spans as (
              select rs.k as result_set_k, dc.county_area_id
              from rs join district_county dc on dc.district_area_id = rs.district_area_id
                and dc.district_valid @> coalesce(rs.plan_from, rs.on_date) and dc.county_valid @> rs.on_date and dc.share >= 0.005
            ),
            reached as (
              select * from climbed
              union all
              select u.result_set_k, u.unit_k, rs.district_area_id,
                     exists (select from spans s where s.result_set_k = rs.k) and not exists (
                       select from spans s where s.result_set_k = rs.k and not exists (
                         select from climbed r where r.result_set_k = s.result_set_k and r.area_id = s.county_area_id))
              from units u join rs on rs.k = u.result_set_k
              where rs.district_area_id is not null and rs.allocation_parent_id is null
                and not exists (select from climbed r where r.result_set_k = rs.k and r.area_id = rs.district_area_id)
            ),
            coverage as (
              select m.result_set_k, m.area_id, au.expected, greatest(m.shaped, m.shapeless) as reporting
              from (
                select r.result_set_k, r.area_id, rs.on_date,
                       count(distinct own.id) filter (where own.scheme_code = ru.scheme_code)::int as shaped,
                       count(distinct r.unit_k) filter (where own.scheme_code is distinct from ru.scheme_code)::int as shapeless
                from reached r join rs on rs.k = r.result_set_k
                join reporting_unit ru on ru.k = r.unit_k and ru.scheme_code = 'us.precinct'
                left join area own on own.id = ru.area_id
                where exists (select from area_universe u where u.area_id = r.area_id and u.valid @> rs.on_date)
                group by r.result_set_k, r.area_id, rs.on_date
              ) m
              join area_universe au on au.area_id = m.area_id and au.valid @> m.on_date and au.shaped = (m.shaped >= m.shapeless)
            ),
            covered as (
              insert into rollup_coverage (result_set_k, area_k, reporting, expected)
              select c.result_set_k, area.k, c.reporting, c.expected from coverage c join area on area.id = c.area_id
            ),
            counted as (
              select r.result_set_k, r.area_id, min(r.unit_k) as unit_k,
                     count(*) > 1 or not bool_and(r.complete) or exists (
                       select from coverage c where c.result_set_k = r.result_set_k and c.area_id = r.area_id
                         and c.reporting <> c.expected) as summed
              from reached r
              where not exists (select from ceiling x where x.result_set_k = r.result_set_k and x.area_id = r.area_id)
              group by r.result_set_k, r.area_id
            ),
            single as (
              insert into result_rollup_unit (result_set_k, area_k, unit_k)
              select c.result_set_k, area.k, c.unit_k from counted c join area on area.id = c.area_id where not c.summed
            )
            insert into result_rollup_sum (result_set_k, area_k, option_k, channel, measure, count_round, complete, value)
            select v.result_set_k, area.k, v.option_k, v.channel, v.measure, v.count_round,
                   bool_and(a.complete) and not exists (
                     select from coverage s where s.result_set_k = v.result_set_k and s.area_id = area.id
                       and s.reporting <> s.expected), sum(v.value)
            from vals v
            join reached a on a.result_set_k = v.result_set_k and a.unit_k = v.unit_k
            join counted c on c.result_set_k = a.result_set_k and c.area_id = a.area_id and c.summed
            join area on area.id = a.area_id
            group by v.result_set_k, area.k, area.id, v.option_k, v.channel, v.measure, v.count_round"""
        ).format(facts=facts, stale=sql.Literal(stale))
    )


def rollup(conn: Connection, sets: list[int], affected: bool) -> int:
    if affected:
        sets += [
            key
            for (key,) in conn.execute(
                """select r.k from result_set r join contest c on c.id = r.contest_id join area a on a.id = c.district_area_id
                   where r.status = 'active' and c.allocation_parent_id is null
                     and exists (select from result_rollup x where x.result_set_k = r.k)
                     and not exists (select from result_rollup x where x.result_set_k = r.k and x.area_k = a.k)
                   order by r.k"""
            ).fetchall()
        ]
        conn.commit()
    for start in range(0, len(sets), 500):
        with conn.transaction():
            _rollup(conn, None, [], [], sets[start : start + 500])
        print(f"{min(start + 500, len(sets))} of {len(sets)} result sets rolled up", flush=True)
    return len(sets)


def defaults(
    conn: Connection, contests: list[str] | None
) -> tuple[list[tuple[str, str, bool]], list[dict[str, object]]]:
    tally = """select coalesce(jsonb_object_agg(t.option_k, t.votes), '{{}}') from (
                 select v.option_k, sum(v.value) as votes from result_value v
                 where v.result_set_k = p.{0} and v.measure = 1 and v.option_k <> 0
                   and v.count_round = (select max(z.count_round) from result_value z
                                        where z.result_set_k = p.{0} and z.measure = 1)
                 group by v.option_k) t"""
    picks = conn.execute(
        cast(
            LiteralString,
            f"""with picked as (
                  select distinct on (d.id) d.id as old, d.k as old_k, r.id as new, r.k as new_k,
                         r.stage = d.stage as same, d.contest_id,
                         exists (select from external_id x where x.scheme = 'ea.authoritative' and x.value = r.id::text)
                           as authoritative
                  from result_set d
                  join source ds on ds.id = d.source_id
                  join result_set r on r.election_id = d.election_id and r.contest_id = d.contest_id
                    and not r.is_default and r.status = 'active' and r.kind = 'counted'
                    and coalesce(r.completeness, 1) >= 0.98
                    and (r.stage = d.stage or (r.stage > d.stage and r.stage <> 'alternative' and not exists (
                      select from result_set x where x.election_id = r.election_id and x.contest_id = r.contest_id
                        and x.stage = r.stage and x.is_default)))
                  join source rs on rs.id = r.source_id and rs.kind = 'official_body'
                  where d.is_default and d.status = 'active' and d.kind = 'counted' and d.contest_id is not null
                    and (ds.kind <> 'official_body' or exists (
                      select from external_id x where x.scheme = 'ea.authoritative' and x.value = r.id::text))
                    and (%(contests)s::uuid[] is null or d.contest_id = any(%(contests)s::uuid[]))
                    and not exists (select from flag f where f.result_set_id = r.id and f.check_code = 'reconcile.official')
                  order by d.id, r.stage desc, r.completeness desc nulls last, r.k
                )
                select p.old::text, p.new::text, p.same, p.contest_id::text, ({tally.format("old_k")}),
                       ({tally.format("new_k")}),
                       array(select o.k::text from outcome x join contest_option o on o.id = x.option_id
                             where x.result_set_id = p.old and x.candidacy_id is null and x.kind in ('elected', 'won')),
                       p.authoritative,
                       array(select o.k::text from contest_option o where o.contest_id = p.contest_id
                             and o.kind in ('others', 'none_of_the_above', 'uncommitted'))
                from picked p""",
        ),
        {"contests": contests},
    ).fetchall()
    flipped: list[tuple[str, str, bool]] = []
    skipped: list[dict[str, object]] = []
    for old, new, same, contest, before, after, winners, authoritative, unranked in picks:
        total, counted = sum(before.values()), sum(after.get(option, 0) for option in before)
        losing = [votes for option, votes in after.items() if option not in winners and option not in unranked]
        reason = (
            "no votes"
            if not sum(after.values())
            else f"counts {counted} against {total}"
            if total and not authoritative and not 0.98 <= counted / total <= 1.02
            else "winner differs"
            if winners
            and (
                any(option not in after for option in winners)
                or (losing and min(after[option] for option in winners) <= max(losing))
            )
            else "already promoted"
            if new in {promoted for _, promoted, _ in flipped}
            else None
        )
        if reason is None:
            flipped.append((old, new, same))
        else:
            skipped.append({"contest": contest, "default": old, "official": new, "reason": reason})
    return flipped, skipped


def publish(conn: Connection, changeset_id: str) -> None:
    stage = _stage(conn, changeset_id)
    with conn.transaction():
        conn.execute("select set_config('ea.changeset_id', %s, true)", [changeset_id])
        if "entity_merge" in stage.tables or "entity_retirement" in stage.tables:
            for _ in range(240):
                taken = conn.execute(
                    "select pg_try_advisory_xact_lock(hashtext('ea.search')) and pg_try_advisory_xact_lock(%s)",
                    [MERGE_LOCK],
                ).fetchone()
                if taken is not None and taken[0]:
                    break
                time.sleep(5)
            else:
                conn.execute("select pg_advisory_xact_lock(hashtext('ea.search'))")
                conn.execute("select pg_advisory_xact_lock(%s)", [MERGE_LOCK])
        else:
            conn.execute("select pg_advisory_xact_lock_shared(%s)", [MERGE_LOCK])
        conn.execute(
            """create temporary table survivor on commit drop as
               with recursive chain (entity_id, into_id) as (
                 select entity_id, into_id from entity_merge
                 union select c.entity_id, m.into_id from chain c join entity_merge m on m.entity_id = c.into_id
               )
               select c.entity_id, c.into_id from chain c
               where not exists (select from entity_merge m where m.entity_id = c.into_id)"""
        )
        types = [
            kind
            for (kind,) in conn.execute(
                "select distinct e.type::text from survivor s join entity e on e.id = s.entity_id"
            ).fetchall()
        ]
        referencing = {
            (relation, column)
            for relation, column in conn.execute(
                """select c.conrelid::regclass::text, a.attname from pg_constraint c
                   join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
                   where c.contype = 'f' and c.connamespace = 'public'::regnamespace
                     and c.confrelid::regclass::text = any(%s)
                     and not exists (
                       select from pg_constraint k where k.conrelid = c.conrelid
                         and (k.contype = 'p' or k.conname = 'person_party_membership_key')
                         and a.attnum = any(k.conkey))""",
                [["entity", *types]],
            ).fetchall()
        } | {(fact, column) for fact in FACTS for column, parent in KEYS.items() if parent in types}
        for table, columns in stage.tables.items():
            for column in columns:
                if (table, column) in referencing and (table, column) != ("entity_merge", "entity_id"):
                    conn.execute(
                        sql.SQL("update {} t set {} = s.into_id from survivor s where t.{} = s.entity_id").format(
                            stage.staged(table), sql.Identifier(column), sql.Identifier(column)
                        )
                    )
        findings = checks.run(conn, stage)
        moved = (
            [
                str(area)
                for (area,) in conn.execute(
                    sql.SQL(
                        """select s.area_id from {} s where not exists (
                             select 1 from public.area_parent p where p.area_id = s.area_id and p.parent_id = s.parent_id{})"""
                    ).format(
                        stage.staged("area_parent"),
                        sql.SQL("").join(
                            sql.SQL(" and p.{column} is not distinct from s.{column}").format(
                                column=sql.Identifier(column)
                            )
                            for column in ("valid_from", "valid_to")
                            if column in stage.tables["area_parent"]
                        ),
                    )
                ).fetchall()
            ]
            if "area_parent" in stage.tables
            else []
        )
        repointed = (
            [
                str(unit)
                for (unit,) in conn.execute(
                    sql.SQL(
                        """select s.id from {} s join public.reporting_unit u on u.id = s.id
                           where u.area_id is distinct from s.area_id"""
                    ).format(stage.staged("reporting_unit"))
                ).fetchall()
            ]
            if "reporting_unit" in stage.tables and "area_id" in stage.tables["reporting_unit"]
            else []
        )
        for table in ("reporting_unit_area", "unit_area_drop"):
            if table in stage.tables:
                repointed += [
                    str(unit)
                    for (unit,) in conn.execute(
                        sql.SQL("select distinct unit_id from {}").format(stage.staged(table))
                    ).fetchall()
                ]
        if "is_default" in stage.tables.get("result_set", []):
            group = "d.election_id = s.election_id and d.contest_id = s.contest_id and d.stage = s.stage"
            official = sql.SQL(
                "exists (select from {} k where k.id = s.source_id and k.kind = 'official_body')"
            ).format(stage.either("source", "id", "kind"))
            conn.execute(
                sql.SQL(
                    """update {staged} s set is_default = false
                       where s.is_default and not {official} and exists (
                         select from public.result_set d join public.source o on o.id = d.source_id
                         where d.is_default and d.id <> s.id and o.kind = 'official_body' and {group})"""
                ).format(staged=stage.staged("result_set"), official=official, group=sql.SQL(group))
            )
            conn.execute(
                sql.SQL(
                    """update {staged} s set is_default = true
                       from public.result_set d
                       where d.id = s.id and d.is_default and not s.is_default and {official} and s.status = 'active'
                         and not exists (select from {staged} x where x.is_default and x.election_id = s.election_id
                                           and x.contest_id = s.contest_id and x.stage = s.stage)"""
                ).format(staged=stage.staged("result_set"), official=official)
            )
            conn.execute(
                sql.SQL(
                    """update {staged} s set is_default = true
                       where s.status = 'active' and not s.is_default and {official}
                         and exists (select from {staged} w join public.result_set d on d.id = w.id and d.is_default
                                     where w.status = 'withdrawn' and w.election_id = s.election_id
                                       and w.contest_id = s.contest_id and w.stage = s.stage)
                         and not exists (select from {staged} x where x.is_default and x.election_id = s.election_id
                                           and x.contest_id = s.contest_id and x.stage = s.stage)"""
                ).format(staged=stage.staged("result_set"), official=official)
            )
            conn.execute(
                sql.SQL(
                    """update public.result_set d set is_default = false
                       from {staged} s
                       where s.is_default and d.is_default and d.id <> s.id and {official} and {group}
                         and not exists (select from public.source o where o.id = d.source_id and o.kind = 'official_body')"""
                ).format(staged=stage.staged("result_set"), official=official, group=sql.SQL(group))
            )
        for table in stage.tables:
            _publish_table(conn, stage, table)
        if "name" in stage.tables:
            conn.execute(
                sql.SQL(
                    """update public.name p set is_preferred = false from public.name q, changeset c, changeset o
                       where %s <> 'identity' and p.is_preferred and p.valid_from is null and p.valid_to is null
                         and o.id = p.changeset_id and o.pipeline = %s
                         and exists (select from {} s where s.entity_id = p.entity_id and s.text = p.text
                                     and s.lang = p.lang and s.is_preferred)
                         and q.entity_id = p.entity_id and q.lang = p.lang and q.script is not distinct from p.script
                         and q.is_preferred and q.text <> p.text and q.valid_from is null and q.valid_to is null
                         and c.id = q.changeset_id and c.pipeline = 'identity'
                         and (regexp_replace(lower(unaccent(p.text)), '[^a-z0-9]', '', 'g')
                                = regexp_replace(lower(unaccent(q.text)), '[^a-z0-9]', '', 'g')
                              or exists (select from public.name r join changeset i on i.id = r.changeset_id
                                         where i.pipeline = 'identity' and r.entity_id = p.entity_id
                                           and r.lang = p.lang and r.script is not distinct from p.script
                                           and r.text = p.text and not r.is_preferred
                                           and r.valid_from is null and r.valid_to is null))"""
                ).format(stage.staged("name")),
                [stage.pipeline, stage.pipeline],
            )
            conflicts = conn.execute(
                sql.SQL(
                    """select distinct p.entity_id::text, p.text, c.pipeline, q.text from public.name p
                       join changeset c on c.id = p.changeset_id and c.pipeline <> 'identity'
                       join public.name q on q.entity_id = p.entity_id and q.lang = p.lang
                         and q.script is not distinct from p.script and q.is_preferred
                         and q.valid_from is null and q.valid_to is null
                       join changeset i on i.id = q.changeset_id and i.pipeline = 'identity'
                       where p.is_preferred and p.valid_from is null and p.valid_to is null
                         and p.entity_id in (select entity_id from {})
                         and regexp_replace(lower(unaccent(p.text)), '[^a-z0-9]', '', 'g')
                           <> regexp_replace(lower(unaccent(q.text)), '[^a-z0-9]', '', 'g')"""
                ).format(stage.staged("name"))
            ).fetchall()
            if conflicts:
                with (DATA / "logs" / "identity-name-conflicts.tsv").open("a", encoding="utf-8") as log:
                    log.writelines(
                        f"{changeset_id}\t{entity}\t{source}\t{text}\tidentity\t{curated}\n"
                        for entity, text, source, curated in conflicts
                    )
        stray = checks.merged(conn, changeset_id, set(stage.tables))
        if stray:
            raise ValueError(
                f"{changeset_id} still references merged entities: "
                + "; ".join(
                    f"{relation}.{column} {entity} ({rows} rows), merged into {into} by {merge}"
                    for relation, column, entity, into, merge, rows in stray[:10]
                )
            )
        _rollup(conn, stage, moved, repointed)
        if "result_set" in stage.tables or stage.pipeline == "defaults":
            flipped, _ = defaults(
                conn,
                None
                if stage.pipeline == "defaults"
                else [
                    str(contest)
                    for (contest,) in conn.execute(
                        sql.SQL("select distinct contest_id from {} where contest_id is not null").format(
                            stage.staged("result_set")
                        )
                    ).fetchall()
                ],
            )
            conn.execute(
                "update result_set set is_default = false where id = any(%s::uuid[])",
                [[old for old, _, same in flipped if same]],
            )
            conn.execute(
                "update result_set set is_default = true where id = any(%s::uuid[])", [[new for _, new, _ in flipped]]
            )
            conn.execute(
                """insert into outcome (result_set_id, option_id, candidacy_id, kind, seats, notes)
                   select f.new, x.option_id, x.candidacy_id, x.kind, x.seats, x.notes
                   from unnest(%s::uuid[], %s::uuid[]) f(old, new) join outcome x on x.result_set_id = f.old
                   on conflict do nothing""",
                [[old for old, _, _ in flipped], [new for _, new, _ in flipped]],
            )
        with (
            conn.cursor() as cursor,
            cursor.copy("copy flag (entity_id, result_set_id, check_code, severity, details) from stdin") as copy,
        ):
            for finding in findings:
                if finding.entity_id is not None or finding.result_set_id is not None:
                    copy.write_row(
                        (
                            finding.entity_id,
                            finding.result_set_id,
                            finding.code,
                            finding.severity,
                            json.dumps(finding.details, default=str),
                        )
                    )
        conn.execute("update changeset set status = 'published', published_at = now() where id = %s", [changeset_id])
        conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(stage.schema)))
    us = conn.execute(
        "select pipeline like 'us.%%' or scope->>'polity' = 'US' from changeset where id = %s", [changeset_id]
    ).fetchone()
    if us is not None and us[0]:
        coverage_tables.request()


def revert(conn: Connection, changeset_id: str, reason: str) -> None:
    status = conn.execute("select status from changeset where id = %s", [changeset_id]).fetchone()
    if status is not None and status[0] in ("draft", "pending_review"):
        with conn.transaction():
            conn.execute(
                sql.SQL("drop schema if exists {} cascade").format(
                    sql.Identifier(f"cs_{changeset_id.replace('-', '')}")
                )
            )
            conn.execute(
                "update changeset set status = 'rejected', rationale = %s where id = %s",
                [f"Rejected: {reason}", changeset_id],
            )
        return
    for _ in range(240):
        taken = conn.execute(
            "select pg_try_advisory_xact_lock(hashtext('ea.search')) and pg_try_advisory_xact_lock(%s)", [MERGE_LOCK]
        ).fetchone()
        if taken is not None and taken[0]:
            break
        time.sleep(5)
    else:
        conn.execute("select pg_advisory_xact_lock(hashtext('ea.search'))")
        conn.execute("select pg_advisory_xact_lock(%s)", [MERGE_LOCK])
    logged = conn.execute(
        "select distinct relation, key_column from fact_rekey where changeset_id = %s order by 1, 2", [changeset_id]
    ).fetchall()
    for relation, column in logged:
        kinds = dict(
            conn.execute(
                """select attname, format_type(atttypid, atttypmod) from pg_attribute
                   where attrelid = %s::regclass and attnum > 0 and not attisdropped""",
                [f"public.{relation}"],
            ).fetchall()
        )
        others = [
            key
            for (key,) in conn.execute(
                """select a.attname from pg_constraint c cross join lateral unnest(c.conkey) k(attnum)
                   join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
                   where c.conrelid = %s::regclass and c.contype = 'p'""",
                [f"public.{relation}"],
            ).fetchall()
            if key != column
        ]
        conn.execute(
            sql.SQL(
                """update public.{target} t set {moved} = r.from_k from fact_rekey r
                   where r.changeset_id = %s and r.relation = %s and r.key_column = %s and t.{moved} = r.to_k and {same}"""
            ).format(
                target=sql.Identifier(relation),
                moved=sql.Identifier(column),
                same=sql.SQL(" and ").join(
                    sql.SQL("t.{0} = (r.row_key ->> {1})::{2}").format(
                        sql.Identifier(key), sql.Literal(key), sql.SQL(cast(LiteralString, kinds[key]))
                    )
                    for key in others
                ),
            ),
            [changeset_id, relation, column],
        )
    rekeyed = next(
        (
            folder / "rekey"
            for folder in (DATA / "changesets" / changeset_id, DATA / "retired" / "changesets" / changeset_id)
            if (folder / "rekey").exists()
        ),
        None,
    )
    for path in sorted(rekeyed.glob("*.parquet")) if rekeyed is not None and not logged else []:
        relation, column = path.stem.split(".")
        frame = pl.read_parquet(path)
        kinds = dict(
            conn.execute(
                """select attname, format_type(atttypid, atttypmod) from pg_attribute
                   where attrelid = %s::regclass and attnum > 0 and not attisdropped""",
                [f"public.{relation}"],
            ).fetchall()
        )
        others = [key for key in frame.columns if key not in ("from_k", "to_k")]
        conn.execute(
            sql.SQL(
                "update public.{target} t set {moved} = r.from_k from unnest({arrays}) r({names})"
                " where t.{moved} = r.to_k and {same}"
            ).format(
                target=sql.Identifier(relation),
                moved=sql.Identifier(column),
                arrays=sql.SQL(", ").join(
                    sql.SQL("%s::{}[]").format(sql.SQL(cast(LiteralString, kinds.get(key, kinds[column]))))
                    for key in [*others, "from_k", "to_k"]
                ),
                names=sql.SQL(", ").join(map(sql.Identifier, [*others, "from_k", "to_k"])),
                same=sql.SQL(" and ").join(sql.SQL("t.{0} = r.{0}").format(sql.Identifier(key)) for key in others),
            ),
            [frame[key].to_list() for key in [*others, "from_k", "to_k"]],
        )
    stamped = [
        table
        for (table,) in conn.execute(
            """select c.relname from pg_attribute a join pg_class c on c.oid = a.attrelid
               where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p') and not c.relispartition
                 and a.attname = 'changeset_id' and not a.attisdropped and c.relname not like '%\\_history'"""
        ).fetchall()
    ]
    versioned = {
        name.removesuffix("_history")
        for (name,) in conn.execute(
            "select relname from pg_class where relnamespace = 'public'::regnamespace and relname like '%\\_history'"
        ).fetchall()
    }
    histories = [table for table in stamped if table in versioned]
    later: set[str] = set()
    for table in histories:
        later.update(
            superseder
            for (superseder,) in conn.execute(
                sql.SQL(
                    "select distinct superseded_by::text from {} where changeset_id = %s and superseded_by <> %s"
                ).format(sql.Identifier(f"{table}_history")),
                [changeset_id, changeset_id],
            ).fetchall()
        )
    for child, column, parent, key in conn.execute(
        """select c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text, p.attname from pg_constraint c
           join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
           join pg_attribute p on p.attrelid = c.confrelid and p.attnum = c.confkey[1]
           where c.contype = 'f' and c.connamespace = 'public'::regnamespace
             and c.conrelid::regclass::text = any(%s) and c.confrelid::regclass::text = any(%s)""",
        [stamped, stamped],
    ).fetchall():
        later.update(
            writer
            for (writer,) in conn.execute(
                sql.SQL(
                    """select distinct t.changeset_id::text from {child} t join {parent} p on p.{key} = t.{column}
                       where p.changeset_id = %(id)s and t.changeset_id <> %(id)s {inserted}"""
                ).format(
                    child=sql.Identifier(child),
                    parent=sql.Identifier(parent),
                    key=sql.Identifier(key),
                    column=sql.Identifier(column),
                    inserted=sql.SQL(
                        "and not exists (select from {} h where h.{} = p.{} and h.superseded_by = %(id)s)"
                    ).format(sql.Identifier(f"{parent}_history"), sql.Identifier(key), sql.Identifier(key))
                    if parent in versioned
                    else sql.SQL(""),
                ),
                {"id": changeset_id},
            ).fetchall()
        )
    for table, column, parent in checks.references(conn):
        if parent == "result_set":
            continue
        inserted = sql.SQL(
            """select p.k from {} p where p.changeset_id = %(id)s
               and not exists (select from {} h where h.id = p.id and h.superseded_by = %(id)s)"""
        ).format(sql.Identifier(parent), sql.Identifier(f"{parent}_history"))
        if conn.execute(sql.SQL("select exists ({})").format(inserted), {"id": changeset_id}).fetchone() == (True,):
            later.update(
                writer
                for (writer,) in conn.execute(
                    sql.SQL(
                        """select distinct rs.changeset_id::text from {} v join result_set rs on rs.k = v.result_set_k
                           where rs.changeset_id <> %(id)s and v.{} in ({})"""
                    ).format(sql.Identifier(table), sql.Identifier(column), inserted),
                    {"id": changeset_id},
                ).fetchall()
            )
    if later:
        dependents = conn.execute(
            "select pipeline || ' ' || id::text from changeset where id = any(%s::uuid[]) order by published_at",
            [list(later)],
        ).fetchall()
        raise ValueError(f"{changeset_id} has later changesets built on it: {', '.join(row for (row,) in dependents)}")
    with conn.transaction():
        conn.execute("select set_config('ea.changeset_id', %s, true)", [changeset_id])
        conn.execute(
            """delete from search_document where entity_id in (select id from entity where changeset_id = %(id)s)
               or polity_id in (select id from polity where changeset_id = %(id)s)""",
            {"id": changeset_id},
        )
        for table in (*FACTS, "result_rollup_sum", "result_rollup_unit", "rollup_coverage"):
            conn.execute(
                sql.SQL(
                    "delete from {} where result_set_k in (select k from result_set rs where rs.changeset_id = %s"
                    " and not exists (select from result_set_history h where h.id = rs.id and h.superseded_by = %s))"
                ).format(sql.Identifier(table)),
                [changeset_id, changeset_id],
            )
        conn.execute("update entity set retired_by = null where retired_by = %s", [changeset_id])
        conn.execute(
            """update area_geometry_history h set geom = coalesce(
                 (select g.geom from area_geometry g
                  where g.area_id = h.area_id and md5(st_asbinary(g.geom))::uuid = h.geom_md5 limit 1),
                 (select o.geom from area_geometry_history o
                  where o.area_id = h.area_id and o.geom_md5 = h.geom_md5 and o.geom is not null limit 1))
               where h.superseded_by = %s and h.geom is null""",
            [changeset_id],
        )
        conn.execute("set local session_replication_role = replica")
        restored: list[str] = []
        for table in histories:
            history = sql.Identifier(f"{table}_history")
            if conn.execute(
                sql.SQL("select exists (select from {} where superseded_by = %s)").format(history), [changeset_id]
            ).fetchone() != (True,):
                continue
            restored.append(table)
            columns = conn.execute(
                """select attname, attidentity <> '' from pg_attribute
                   where attrelid = %s::regclass and attnum > 0 and not attisdropped and attgenerated = ''
                   order by attnum""",
                [f"public.{table}"],
            ).fetchall()
            target = sql.Identifier(table)
            if "id" in {column for column, _ in columns}:
                settable = [column for column, identity in columns if not identity and column != "id"]
                conn.execute(
                    sql.SQL(
                        "delete from {} t where t.changeset_id = %s"
                        " and not exists (select from {} h where h.id = t.id and h.superseded_by = %s)"
                    ).format(target, history),
                    [changeset_id, changeset_id],
                )
                conn.execute(
                    sql.SQL("update {} t set ({}) = ({}) from {} h where h.id = t.id and h.superseded_by = %s").format(
                        target,
                        sql.SQL(", ").join(map(sql.Identifier, settable)),
                        sql.SQL(", ").join(sql.SQL("h.{}").format(sql.Identifier(column)) for column in settable),
                        history,
                    ),
                    [changeset_id],
                )
                conn.execute(
                    sql.SQL(
                        """insert into {target} ({listed}) overriding system value
                           select distinct on (h.id) {kept} from {history} h
                           where h.superseded_by = %s and not exists (select from {target} t where t.id = h.id)
                           order by h.id, lower(h.recorded)"""
                    ).format(
                        target=target,
                        listed=sql.SQL(", ").join(sql.Identifier(column) for column, _ in columns),
                        kept=sql.SQL(", ").join(
                            sql.SQL("h.{}").format(sql.Identifier(column)) for column, _ in columns
                        ),
                        history=history,
                    ),
                    [changeset_id],
                )
            else:
                listed = sql.SQL(", ").join(sql.Identifier(column) for column, _ in columns)
                conn.execute(sql.SQL("delete from {} where changeset_id = %s").format(target), [changeset_id])
                conn.execute(
                    sql.SQL(
                        "insert into {} ({}) overriding system value select {} from {} where superseded_by = %s"
                    ).format(target, listed, listed, history),
                    [changeset_id],
                )
        conn.execute("set local session_replication_role = origin")
        edges = conn.execute(
            """select conrelid::regclass::text, confrelid::regclass::text from pg_constraint
               where contype = 'f' and connamespace = 'public'::regnamespace and conrelid <> confrelid"""
        ).fetchall()
        remaining = [table for table in stamped if table != "entity"]
        while remaining:
            leaves = [
                table
                for table in remaining
                if not any(target == table and child in remaining for child, target in edges)
            ]
            assert leaves, remaining
            for table in leaves:
                conn.execute(
                    sql.SQL("delete from {} where changeset_id = %s").format(sql.Identifier(table)), [changeset_id]
                )
            remaining = [table for table in remaining if table not in leaves]
        conn.execute("delete from entity where changeset_id = %s", [changeset_id])
        stranded = [
            f"{child}.{column} {value} ({parent} missing)"
            for child, column, parent, key in conn.execute(
                """select c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text, p.attname
                   from pg_constraint c
                   join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
                   join pg_attribute p on p.attrelid = c.confrelid and p.attnum = c.confkey[1]
                   where c.contype = 'f' and c.connamespace = 'public'::regnamespace
                     and c.conrelid::regclass::text = any(%s)""",
                [restored],
            ).fetchall()
            for (value,) in conn.execute(
                sql.SQL(
                    """select distinct h.{column}::text from {history} h
                       where h.superseded_by = %s and h.{column} is not null
                         and not exists (select from {parent} p where p.{key} = h.{column})
                         and exists (select from {child} t where t.{column} = h.{column})"""
                ).format(
                    column=sql.Identifier(column),
                    history=sql.Identifier(f"{child}_history"),
                    parent=sql.Identifier(parent),
                    key=sql.Identifier(key),
                    child=sql.Identifier(child),
                ),
                [changeset_id],
            ).fetchall()
        ]
        if stranded:
            raise ValueError(f"reverting {changeset_id} would strand restored rows: " + "; ".join(stranded[:10]))
        for table in histories:
            conn.execute(
                sql.SQL("delete from {} where superseded_by = %s").format(sql.Identifier(f"{table}_history")),
                [changeset_id],
            )
        conn.execute(
            "update changeset set status = 'rejected', rationale = %s where id = %s",
            [f"Reverted: {reason}", changeset_id],
        )


def apply(folder: Path) -> str:
    manifest = json.loads((folder / "manifest.json").read_text())
    changeset_id: str = manifest["id"]
    tables: list[str] = manifest["tables"]
    schema = sql.Identifier(f"cs_{changeset_id.replace('-', '')}")
    with connect() as conn:
        existing = conn.execute("select status from changeset where id = %s", [changeset_id]).fetchone()
        if existing is not None:
            return str(existing[0])
        conn.execute(
            """insert into changeset
                 (id, author_kind, pipeline, pipeline_version, kind, status, summary, scope, payload_kind,
                  artifact_keys)
               values (%s, %s, %s, %s, %s, 'draft', %s, %s, 'artifacts', %s)""",
            [
                changeset_id,
                manifest["author_kind"],
                manifest["pipeline"],
                manifest["pipeline_version"],
                manifest["kind"],
                manifest["summary"],
                Jsonb(manifest["scope"]),
                [f"changesets/{changeset_id}/{table}.parquet" for table in tables],
            ],
        )
        conn.execute(sql.SQL("create schema {}").format(schema))
        for table in tables:
            frame = pl.read_parquet(folder / f"{table}.parquet")
            types = dict(
                conn.execute(
                    """select attname, format_type(atttypid, atttypmod) from pg_attribute
                       where attrelid = %s::regclass and attnum > 0 and not attisdropped""",
                    [f"public.{table}"],
                ).fetchall()
            )
            definitions: list[sql.Composable] = []
            for column in frame.columns:
                kind = types.get(column) or ("uuid" if table in FACTS and column in KEYS else None)
                if kind is None:
                    raise ValueError(f"{table}.{column} is not a column of {table}")
                definitions.append(sql.SQL("{} {}").format(sql.Identifier(column), sql.SQL(cast(LiteralString, kind))))
            conn.execute(
                sql.SQL("create table {}.{} ({})").format(
                    schema, sql.Identifier(table), sql.SQL(", ").join(definitions)
                )
            )
            with (
                conn.cursor() as cursor,
                cursor.copy(
                    sql.SQL("copy {}.{} ({}) from stdin (format csv)").format(
                        schema, sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, frame.columns))
                    )
                ) as copy,
            ):
                for chunk in frame.iter_slices(500_000):
                    buffer = io.BytesIO()
                    chunk.write_csv(buffer, include_header=False, quote_style="non_numeric")
                    copy.write(buffer.getvalue())
            conn.execute(sql.SQL("analyze {}.{}").format(schema, sql.Identifier(table)))
        stage = _stage(conn, changeset_id)
        if "entity_merge" in stage.tables:
            staged = stage.staged("entity_merge")
            contests = conn.execute(
                sql.SQL("select s.entity_id, s.into_id from {} s join public.contest c on c.id = s.entity_id").format(
                    staged
                )
            ).fetchall()
            if contests:
                merged = sql.SQL(
                    "(select entity_id, into_id from public.entity_merge union all select entity_id, into_id from {})"
                ).format(staged)
                taken = {
                    entity
                    for (entity,) in conn.execute(
                        sql.SQL("select entity_id from {0} union select into_id from {0}").format(staged)
                    ).fetchall()
                }
                options: dict[object, list[tuple[object, str, object, object, str, frozenset[object]]]] = {}
                for option, contest, kind, parent, party, label, persons in conn.execute(
                    sql.SQL(
                        """select o.id, o.contest_id, o.kind::text, o.parent_option_id, o.party_id, o.ballot_label,
                                  array_remove(array_agg(distinct coalesce(m.into_id, k.person_id)), null)
                           from public.contest_option o
                           left join public.candidacy k on k.option_id = o.id and k.role = 'head'
                           left join {} m on m.entity_id = k.person_id
                           where o.contest_id = any(%s) group by o.id"""
                    ).format(merged),
                    [[contest for pair in contests for contest in pair]],
                ).fetchall():
                    if option not in taken:
                        folded = "".join(
                            character
                            for character in unicodedata.normalize("NFKD", label or "").lower()
                            if character.isalnum() and not unicodedata.combining(character)
                        )
                        options.setdefault(contest, []).append(
                            (option, kind, parent, party, folded, frozenset(persons))
                        )
                explicit = conn.execute(
                    sql.SQL(
                        "select o.contest_id, s.entity_id, s.into_id from {} s join public.contest_option o on o.id = s.entity_id"
                    ).format(staged)
                ).fetchall()
                paired: dict[object, object] = {}
                for duplicate, survivor in contests:
                    groups = [
                        (None, None),
                        *((left, right) for contest, left, right in explicit if contest == duplicate),
                    ]
                    while groups:
                        left, right = groups.pop()
                        lefts = [option for option in options.get(duplicate, []) if option[2] == left]
                        rights = [option for option in options.get(survivor, []) if option[2] == right]
                        for rule in ("person", "party", "label"):
                            held: dict[object, tuple[list[object], list[object]]] = {}
                            for side, group in ((0, lefts), (1, rights)):
                                for option, kind, _, party, folded, persons in group:
                                    key = (
                                        persons or None
                                        if rule == "person"
                                        else party
                                        if rule == "party" and not persons
                                        else (kind, folded)
                                        if rule == "label" and folded
                                        else None
                                    )
                                    if key is not None:
                                        held.setdefault(key, ([], []))[side].append(option)
                            for ours, theirs in held.values():
                                if len(ours) == len(theirs) == 1:
                                    paired[ours[0]] = theirs[0]
                                    groups.append((ours[0], theirs[0]))
                            lefts = [option for option in lefts if option[0] not in paired]
                            rights = [option for option in rights if option[0] not in set(paired.values())]
                pairs = [*paired.items(), *((left, right) for _, left, right in explicit)]
                candidacies: dict[object, list[tuple[object, str, object]]] = {}
                for candidacy, option, role, person in conn.execute(
                    sql.SQL(
                        """select k.id, k.option_id, k.role::text, coalesce(m.into_id, k.person_id)
                           from public.candidacy k left join {} m on m.entity_id = k.person_id
                           where k.option_id = any(%s)"""
                    ).format(merged),
                    [[option for pair in pairs for option in pair]],
                ).fetchall():
                    if candidacy not in taken:
                        candidacies.setdefault(option, []).append((candidacy, role, person))
                for left, right in pairs:
                    lefts = candidacies.get(left, [])
                    rights = candidacies.get(right, [])
                    for rule in ("person", "role"):
                        held = {}
                        for side, group in ((0, lefts), (1, rights)):
                            for candidacy, role, person in group:
                                held.setdefault((role, person) if rule == "person" else role, ([], []))[side].append(
                                    candidacy
                                )
                        for ours, theirs in held.values():
                            if len(ours) == len(theirs) == 1:
                                paired[ours[0]] = theirs[0]
                        lefts = [candidacy for candidacy in lefts if candidacy[0] not in paired]
                        rights = [candidacy for candidacy in rights if candidacy[0] not in set(paired.values())]
                with conn.cursor() as cursor:
                    cursor.executemany(
                        sql.SQL("insert into {} (entity_id, into_id) values (%s, %s)").format(staged),
                        list(paired.items()),
                    )
        if "is_default" in stage.tables.get("result_set", []):
            kind = sql.SQL("(select k.kind from {} k where k.id = {}.source_id)")
            sources = stage.either("source", "id", "kind")
            conn.execute(
                sql.SQL(
                    """update {staged} s set is_default = false
                       from public.result_set p
                       where p.id = s.id and s.is_default and not p.is_default and exists (
                         select from public.result_set d
                         where d.is_default and d.id <> s.id and d.election_id = s.election_id
                           and d.contest_id is not distinct from s.contest_id and d.stage = s.stage
                           and not exists (select from {staged} x where x.id = d.id and not x.is_default)
                           and ({new} <> 'official_body' or {old} = 'official_body'))"""
                ).format(
                    staged=stage.staged("result_set"),
                    new=kind.format(sources, sql.Identifier("s")),
                    old=kind.format(sources, sql.Identifier("d")),
                )
            )
        findings = checks.run(conn, stage)
        diff = _diff(conn, stage)
        if not findings and all(
            change["insert"] == change["update"] == change["delete"] == 0 for change in diff.values()
        ):
            conn.rollback()
            return "unchanged"
        counts = Counter(finding.code for finding in findings)
        examples: dict[str, list[dict[str, object]]] = {}
        for finding in findings:
            if len(examples.setdefault(finding.code, [])) < 20:
                examples[finding.code].append(
                    {
                        "entity_id": finding.entity_id,
                        "result_set_id": finding.result_set_id,
                        "details": finding.details,
                    }
                )
        official = conn.execute(
            sql.SQL(
                """select count(*) > 0 and bool_and(s.kind = 'official_body')
                   from {}.source_artifact a join {} s on s.id = a.source_id"""
            ).format(schema, stage.either("source", "id", "kind"))
            if "source_artifact" in tables
            else sql.SQL("select false")
        ).fetchone()
        conn.execute(
            "update changeset set checks = %s where id = %s",
            [
                Jsonb(
                    {"diff": diff, "findings": dict(counts), "examples": examples},
                    dumps=lambda value: json.dumps(value, default=str),
                ),
                changeset_id,
            ],
        )
        if any(finding.severity == "error" for finding in findings) or (
            manifest["kind"] != "match" and (official is None or not official[0])
        ):
            conn.execute("update changeset set status = 'pending_review' where id = %s", [changeset_id])
            return "pending_review"
        conn.commit()
        publish(conn, changeset_id)
        return "published"
