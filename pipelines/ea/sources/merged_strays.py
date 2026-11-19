from collections import Counter
from pathlib import Path
from uuid import uuid7

from psycopg import sql

from ea.apply import _survivors
from ea.changeset import Changeset
from ea.checks import MERGE_DELETES, references
from ea.store import Connection, connect

SOURCE = "merged_strays"
VERSION = "2"


def _columns(conn: Connection, table: str) -> dict[str, str]:
    return dict(
        conn.execute(
            """select attname, format_type(atttypid, atttypmod) from pg_attribute
               where attrelid = %s::regclass and attnum > 0 and not attisdropped and attgenerated = ''
                 and attname not in ('changeset_id', 'recorded_from')
               order by attnum""",
            [f"public.{table}"],
        ).fetchall()
    )


def _key(columns: dict[str, str]) -> sql.Composable:
    return sql.SQL("jsonb_build_object({})").format(
        sql.SQL(", ").join(
            sql.SQL("{}, t.{}{}").format(
                sql.Literal(column), sql.Identifier(column), sql.SQL("" if kind in ("json", "jsonb") else "::text")
            )
            for column, kind in columns.items()
        )
    )


def _periods(conn: Connection) -> list[tuple[str, str, list[str]]]:
    keys = dict(
        conn.execute(
            """select c.conrelid::regclass::text, array_agg(a.attname order by k.ordinality)
               from pg_constraint c
               cross join lateral unnest(c.conkey) with ordinality k(attnum, ordinality)
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
               where c.conperiod and c.contype in ('p', 'u') and c.connamespace = 'public'::regnamespace
                 and a.attname <> 'valid'
               group by c.oid, c.conrelid"""
        ).fetchall()
    )
    return [
        (relation, column, [other for other in keys[relation] if other != column])
        for relation, column in conn.execute(
            """select c.conrelid::regclass::text, a.attname from pg_constraint c
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
               where c.contype = 'f' and c.connamespace = 'public'::regnamespace and a.attname <> 'id'
                 and c.confrelid::regclass::text = any(array['entity'] || enum_range(null::entity_type)::text[])
               order by 1, 2"""
        ).fetchall()
        if column in keys.get(relation, [])
    ]


def _overridden(conn: Connection, table: str, column: str, others: list[str]) -> list[dict[str, object]]:
    return [
        key
        for (key,) in conn.execute(
            sql.SQL(
                """select {key} from public.{table} t join survivor s on s.entity_id = t.{column}
                   where exists (select from public.{table} x where x.{column} = s.into_id and x.valid && t.valid{same})"""
            ).format(
                key=_key(_columns(conn, table)),
                table=sql.Identifier(table),
                column=sql.Identifier(column),
                same=sql.SQL("").join(
                    sql.SQL(" and x.{0} is not distinct from t.{0}").format(sql.Identifier(other)) for other in others
                ),
            )
        ).fetchall()
    ]


def _orphans(conn: Connection, table: str) -> list[dict[str, object]]:
    columns = _columns(conn, table)
    keyed = "k" in columns
    found = conn.execute(
        sql.SQL("select t.id, {} from public.{} t join survivor s on s.entity_id = t.id").format(
            sql.SQL("t.k" if keyed else "null::int"), sql.Identifier(table)
        )
    ).fetchall()
    if not found:
        return []
    ids = [id for id, _ in found]
    ks = [k for _, k in found if k is not None]
    held: set[object] = set()
    for relation, column in conn.execute(
        """select c.conrelid::regclass::text, a.attname from pg_constraint c
           join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
           where c.contype = 'f' and c.confrelid = %s::regclass and c.conrelid <> c.confrelid""",
        [f"public.{table}"],
    ).fetchall():
        held |= {
            id
            for (id,) in conn.execute(
                sql.SQL("select distinct {0} from public.{1} where {0} = any(%s)").format(
                    sql.Identifier(column), sql.Identifier(relation)
                ),
                [ids],
            ).fetchall()
        }
    for relation, column, parent in references(conn) if keyed else []:
        if parent == table:
            used = {
                k
                for (k,) in conn.execute(
                    sql.SQL("select distinct {0} from public.{1} where {0} = any(%s)").format(
                        sql.Identifier(column), sql.Identifier(relation)
                    ),
                    [ks],
                ).fetchall()
            }
            held |= {id for id, k in found if k in used}
    free = [id for id in ids if id not in held]
    return [
        key
        for (key,) in conn.execute(
            sql.SQL("select {} from public.{} t where t.id = any(%s)").format(_key(columns), sql.Identifier(table)),
            [free],
        ).fetchall()
    ]


def emit() -> Path | None:
    with connect() as conn:
        _survivors(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Clear rows that still reference merged-away entities where the survivor holds its own row",
            polity=None,
            kind="correction",
        )
        dropped: Counter[str] = Counter()
        for table, column, others in _periods(conn):
            for key in _overridden(conn, table, column, others):
                cs.add(
                    "row_drop",
                    id=uuid7(),
                    reason="The survivor of this entity's merge holds its own row for this period",
                    relation=table,
                    key=key,
                )
                dropped[table] += 1
        moved = conn.execute(
            """select t.unit_id, s.into_id, t.reason, to_jsonb(t) - 'changeset_id' - 'recorded_from'
               from unit_area_drop t join survivor s on s.entity_id = t.area_id"""
        ).fetchall()
        named: set[tuple[object, object]] = set()
        for unit, into, reason, key in moved:
            cs.add(
                "row_drop",
                id=uuid7(),
                reason="The area was merged; the drop now names its survivor",
                relation="unit_area_drop",
                key=key,
            )
            if (unit, into) not in named and (
                conn.execute("select from unit_area_drop where unit_id = %s and area_id = %s", [unit, into]).fetchone()
                is None
            ):
                cs.add("unit_area_drop", unit_id=unit, area_id=into, reason=reason)
                named.add((unit, into))
            dropped["unit_area_drop"] += 1
        for (key,) in conn.execute(
            sql.SQL(
                """select {} from public.flag t join survivor s on s.entity_id = t.entity_id
                   where exists (select from public.flag x where x.entity_id = s.into_id and x.check_code = t.check_code
                                   and x.result_set_id is not distinct from t.result_set_id)"""
            ).format(_key(_columns(conn, "flag")))
        ).fetchall():
            cs.add(
                "row_drop",
                id=uuid7(),
                reason="The survivor of this entity's merge carries the same flag",
                relation="flag",
                key=key,
            )
            dropped["flag"] += 1
        for table in MERGE_DELETES:
            for key in _orphans(conn, table):
                cs.add(
                    "row_drop",
                    id=uuid7(),
                    reason="The merge into the survivor deletes this row, and nothing references it",
                    relation=table,
                    key=key,
                )
                dropped[table] += 1
        for table, count in sorted(dropped.items()):
            print(f"{table}: {count:,} rows")
        return cs.write() if dropped else None
