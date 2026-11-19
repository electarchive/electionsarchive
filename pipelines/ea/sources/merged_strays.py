from collections import Counter
from pathlib import Path
from uuid import uuid7

from psycopg import sql

from ea.apply import _survivors
from ea.changeset import Changeset
from ea.checks import MERGE_DELETES, merged, references
from ea.store import Connection, connect

SOURCE = "merged_strays"
VERSION = "1"
HELD = {
    "area_code": "area_id",
    "area_parent": "area_id",
    "area_geometry": "area_id",
    "polity_parent": "polity_id",
    "polity_territory": "polity_id",
    "party_color": "party_id",
    "party_position": "party_id",
    "party_affiliation": "party_id",
    "tenure_affiliation": "tenure_id",
}
LOOSE = {"valid", "valid_from", "valid_to", "valid_from_precision", "valid_to_precision", "source_id"}


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


def _entities(conn: Connection, table: str) -> set[str]:
    return {
        column
        for (column,) in conn.execute(
            """select a.attname from pg_constraint c
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
               where c.contype = 'f' and c.conrelid = %s::regclass
                 and c.confrelid::regclass::text = any(array['entity'] || enum_range(null::entity_type)::text[])""",
            [f"public.{table}"],
        ).fetchall()
    }


def _duplicates(conn: Connection, table: str, column: str) -> list[dict[str, object]]:
    columns = _columns(conn, table)
    entities = _entities(conn, table)
    same = [
        sql.SQL(
            "coalesce((select into_id from survivor where entity_id = t.{0}), t.{0}) is not distinct from x.{0}"
            if other in entities
            else "x.{0} is not distinct from t.{0}"
        ).format(sql.Identifier(other))
        for other in columns
        if other != column and other not in LOOSE
    ]
    return [
        key
        for (key,) in conn.execute(
            sql.SQL(
                """select {key} from public.{table} t join survivor s on s.entity_id = t.{column}
                   where exists (select from public.{table} x where x.{column} = s.into_id
                                   and x.valid @> t.valid and {same})"""
            ).format(
                key=_key(columns),
                table=sql.Identifier(table),
                column=sql.Identifier(column),
                same=sql.SQL(" and ").join([sql.SQL("true"), *same]),
            )
        ).fetchall()
    ]


def _orphans(conn: Connection, table: str) -> list[dict[str, object]]:
    unreferenced = [
        sql.SQL("not exists (select from public.{} r where r.{} = t.id)").format(
            sql.Identifier(relation), sql.Identifier(column)
        )
        for relation, column in conn.execute(
            """select c.conrelid::regclass::text, a.attname from pg_constraint c
               join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
               where c.contype = 'f' and c.confrelid = %s::regclass and c.conrelid <> c.confrelid""",
            [f"public.{table}"],
        ).fetchall()
    ] + [
        sql.SQL("not exists (select from public.{} r where r.{} = t.k)").format(
            sql.Identifier(relation), sql.Identifier(column)
        )
        for relation, column, parent in references(conn)
        if parent == table
    ]
    return [
        key
        for (key,) in conn.execute(
            sql.SQL("select {} from public.{} t join survivor s on s.entity_id = t.id where {}").format(
                _key(_columns(conn, table)),
                sql.Identifier(table),
                sql.SQL(" and ").join([sql.SQL("true"), *unreferenced]),
            )
        ).fetchall()
    ]


def emit() -> Path | None:
    with connect() as conn:
        _survivors(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Clear rows that still reference merged-away entities and that the survivor already holds",
            polity=None,
            kind="correction",
        )
        dropped: Counter[str] = Counter()
        for table, column in HELD.items():
            for key in _duplicates(conn, table, column):
                cs.add(
                    "row_drop",
                    id=uuid7(),
                    reason="Duplicate of a row the survivor of this entity's merge already holds for the same period",
                    relation=table,
                    key=key,
                )
                dropped[table] += 1
        moved = conn.execute(
            """select t.unit_id, s.into_id, t.reason, to_jsonb(t) - 'changeset_id' - 'recorded_from'
               from unit_area_drop t join survivor s on s.entity_id = t.area_id"""
        ).fetchall()
        for unit, into, reason, key in moved:
            cs.add(
                "row_drop",
                id=uuid7(),
                reason="The area was merged; the drop now names its survivor",
                relation="unit_area_drop",
                key=key,
            )
            if (
                conn.execute("select from unit_area_drop where unit_id = %s and area_id = %s", [unit, into]).fetchone()
                is None
            ):
                cs.add("unit_area_drop", unit_id=unit, area_id=into, reason=reason)
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
        left: Counter[str] = Counter()
        for relation, column, _, _, _, rows in merged(conn):
            left[f"{relation}.{column}"] += rows
        for reference, rows in sorted(left.items()):
            print(f"still referencing merged entities before this changeset: {reference} {rows:,}")
        return cs.write() if dropped else None
