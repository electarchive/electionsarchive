from pathlib import Path

from psycopg import sql

from ea import checks
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.precinct_codes"
VERSION = "1"
MIGRATED = ("us.swdb_sov",)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire the us.precinct units that the canonical precinct codes replaced and that nothing references",
            polity="US",
        )
        facts = sql.SQL("").join(
            sql.SQL(" and not exists (select from {} f where f.{} = u.k)").format(
                sql.Identifier(relation), sql.Identifier(column)
            )
            for relation, column, parent in checks.references(conn)
            if parent == "reporting_unit"
        )
        retired = conn.execute(
            sql.SQL(
                """select u.id, u.code from reporting_unit u join changeset c on c.id = u.changeset_id
                   join entity e on e.id = u.id
                   where u.scheme_code = 'us.precinct' and c.pipeline = any(%s) and e.retired_by is null
                     and u.code !~ '^[0-9]{{4}}:[A-Z]{{2}}:[0-9]{{5}}::'
                     and not exists (select from reporting_unit_area m where m.unit_id = u.id)
                     and not exists (select from reporting_unit x where x.parent_id = u.id or x.counted_with_id = u.id){}"""
            ).format(facts),
            [list(MIGRATED)],
        ).fetchall()
        for unit, code in retired:
            cs.add("entity_retirement", entity_id=unit, reason=f"{code} is replaced by its canonical precinct code")
        print(f"{len(retired)} units retired")
        return cs.write()
