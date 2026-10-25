from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_units"
VERSION = "2"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California counties' election precincts placed on the precinct shapes for the same election",
            polity="US",
        )
        shapes = dict(
            conn.execute(
                """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'us.precinct' and c.code ~ '^[0-9-]{10}:CA:[0-9]{5}:[^:]+$'"""
            ).fetchall()
        )
        rows = []
        unplaced = 0
        for unit, code, start, end in conn.execute(
            """select u.id::text, u.code, lower(u.valid), upper(u.valid) from reporting_unit u
               where u.scheme_code = 'us.precinct' and u.code ~ '^[0-9-]{10}:CA:[0-9]{5}:[^:]+:[^:]+$'
                 and not exists (
                   select 1 from reporting_unit_area x join area a on a.id = x.area_id and a.scheme_code = 'us.precinct'
                   where x.unit_id = u.id)"""
        ).fetchall():
            day, state, county, _, precinct = code.split(":")
            area = shapes.get(f"{day}:{state}:{county}:{precinct}") or shapes.get(
                f"{day}:{state}:{county}:{precinct.lstrip('0').zfill(5)}"
            )
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
