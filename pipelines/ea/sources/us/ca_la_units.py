from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_la_units"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Los Angeles County's election precincts placed on the county's precinct shapes for the same election",
            polity="US",
        )
        shapes = dict(
            conn.execute(
                """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'us.precinct' and c.code ~ '^[0-9-]{10}:CA:06037:[^:]+$'"""
            ).fetchall()
        )
        rows = []
        unplaced = 0
        for unit, code, start, end in conn.execute(
            """select u.id::text, u.code, lower(u.valid), upper(u.valid) from reporting_unit u
               where u.scheme_code = 'us.precinct' and u.code ~ '^[0-9-]{10}:CA:06037:[^:]+:[^:]+$'"""
        ).fetchall():
            day, state, county, _, precinct = code.split(":")
            area = shapes.get(f"{day}:{state}:{county}:{precinct}")
            if area is None:
                unplaced += 1
            else:
                rows.append((unit, area, start, end, None))
        print(f"{len(rows)} precincts placed, {unplaced} without a shape")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                rows,
                schema={
                    "unit_id": pl.String,
                    "area_id": pl.String,
                    "valid_from": pl.Date,
                    "valid_to": pl.Date,
                    "method": pl.String,
                },
                orient="row",
            ),
        )
        return cs.write()
