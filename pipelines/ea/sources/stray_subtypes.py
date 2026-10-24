from pathlib import Path

from psycopg import sql

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "stray_subtypes"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Drop subtype rows that sit on an entity of another type, keeping the entity",
            polity=None,
            kind="correction",
        )
        for (table,) in conn.execute(
            """select c.relname from pg_trigger t join pg_class c on c.oid = t.tgrelid
               where t.tgname = 'ea_entity' order by 1"""
        ).fetchall():
            for entity, kind in conn.execute(
                sql.SQL(
                    "select s.id, e.type::text from public.{} s join entity e on e.id = s.id where e.type::text <> %s"
                ).format(sql.Identifier(table)),
                [table],
            ).fetchall():
                cs.add(
                    "entity_subtype_drop",
                    entity_id=entity,
                    subtype=table,
                    reason=f"a {kind}, not a {table}; the {table} row was minted onto an existing {kind}",
                )
                print(f"{entity}: {table} row on a {kind}")
        return cs.write()
