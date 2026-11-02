import sys

from psycopg import sql

from ea.checks import FACTS
from ea.store import Connection

ROLES = {
    ("entity_merge", "entity_id"): "merged",
    ("entity_retirement", "entity_id"): "retired",
    ("citation", "entity_id"): "cited",
}

UPSERT = sql.SQL(
    """insert into changeset_entity (entity_id, changeset_id, role)
       select entity_id, changeset_id, min(role) from ({}) x(entity_id, changeset_id, role)
       where entity_id is not null
       group by entity_id, changeset_id
       on conflict (entity_id, changeset_id) do update set role = least(changeset_entity.role, excluded.role)"""
)

PUBLISHED = sql.SQL("join changeset c on c.id = t.{} and c.status = 'published'")


def links(conn: Connection) -> list[tuple[str, str, str, bool]]:
    types = [kind for (kind,) in conn.execute("select unnest(enum_range(null::entity_type))::text").fetchall()]
    found: dict[str, list[tuple[str, bool]]] = {}
    for table, column, history in conn.execute(
        """with core as (
                 select c.oid, c.relname from pg_class c
                 where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p') and not c.relispartition
                   and c.relname <> all(%s)
                   and exists (select from pg_attribute s where s.attrelid = c.oid and s.attname = 'changeset_id'
                               and not s.attisdropped)
               ), linked as (
                 select r.relname, a.attname from core r
                 join pg_constraint f on f.conrelid = r.oid and f.contype = 'f' and cardinality(f.conkey) = 1
                 join pg_attribute a on a.attrelid = r.oid and a.attnum = f.conkey[1]
                 where f.confrelid::regclass::text = any(%s)
                 union
                 select r.relname, 'id' from core r
                 join pg_trigger t on t.tgrelid = r.oid and t.tgname = 'ea_entity'
               )
               select l.relname, l.attname, to_regclass(format('public.%%I', l.relname || '_history')) is not null
               from linked l order by 1, 2""",
        [list(FACTS), ["entity", *types]],
    ).fetchall():
        found.setdefault(table, []).append((column, history))
    rows: list[tuple[str, str, str, bool]] = []
    for table, columns in found.items():
        names = [column for column, _ in columns]
        keep = ["id"] if "id" in names else [column for column in names if column != "source_id"] or names
        rows.extend(
            (table, column, ROLES.get((table, column), "changed"), history)
            for column, history in columns
            if column in keep
        )
    return rows


def _select(table: str, column: str, role: str, written: str, condition: sql.Composable) -> sql.Composable:
    return sql.SQL("select t.{}, t.{}, {}::changeset_entity_role from public.{} t {}").format(
        sql.Identifier(column), sql.Identifier(written), sql.Literal(role), sql.Identifier(table), condition
    )


def record(conn: Connection, changeset_id: str) -> None:
    at = sql.Literal(changeset_id)
    parts: list[sql.Composable] = [
        sql.SQL("select id, changeset_id, 'created'::changeset_entity_role from entity where changeset_id = {}").format(
            at
        )
    ]
    for table, column, role, history in links(conn):
        parts.append(_select(table, column, role, "changeset_id", sql.SQL("where t.changeset_id = {}").format(at)))
        if history:
            parts.append(
                _select(
                    f"{table}_history", column, role, "superseded_by", sql.SQL("where t.superseded_by = {}").format(at)
                )
            )
    conn.execute(UPSERT.format(sql.SQL(" union all ").join(parts)))


def recorded(conn: Connection, changeset_id: str) -> None:
    try:
        with conn.transaction():
            record(conn, changeset_id)
    except Exception as failure:
        print(f"touched: skipped changeset {changeset_id}: {failure}", file=sys.stderr, flush=True)


def backfill(conn: Connection, table: str | None = None, batch: int = 200) -> None:
    published = [
        changeset
        for (changeset,) in conn.execute(
            "select id::text from changeset where status = 'published' order by published_at, id"
        ).fetchall()
    ]
    found = links(conn)
    for name in [table] if table is not None else ["entity", *dict.fromkeys(row[0] for row in found)]:
        for start in range(0, len(published), batch):
            chunk = sql.SQL("where t.changeset_id = any({}::uuid[])").format(
                sql.Literal(published[start : start + batch])
            )
            conn.execute(
                UPSERT.format(
                    sql.SQL(" union all ").join(
                        [_select("entity", "id", "created", "changeset_id", chunk)]
                        if name == "entity"
                        else [
                            _select(name, column, role, "changeset_id", chunk)
                            for t, column, role, _ in found
                            if t == name
                        ]
                    )
                )
            )
            conn.commit()
        for t, column, role, history in found:
            if t == name and history:
                for written in ("changeset_id", "superseded_by"):
                    conn.execute(
                        UPSERT.format(
                            _select(
                                f"{name}_history",
                                column,
                                role,
                                written,
                                PUBLISHED.format(sql.Identifier(written)),
                            )
                        )
                    )
                    conn.commit()
        print(name, flush=True)
