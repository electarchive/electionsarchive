from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_units"
VERSION = "3"


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
        yearly: dict[str, list[str]] = {}
        for code, area in conn.execute(
            """select s.code, x.area_id::text from reporting_unit s
               join reporting_unit_area x on x.unit_id = s.id
               join area a on a.id = x.area_id and a.scheme_code = 'us.precinct'
               where s.scheme_code = 'us.precinct' and s.code ~ '^[0-9]{4}:CA:[0-9]{5}:[^:]+:[^:]+$'"""
        ).fetchall():
            yearly.setdefault(code, []).append(area)
        rows = []
        unplaced = 0
        for unit, code, start, end in conn.execute(
            """select u.id::text, u.code, lower(u.valid), upper(u.valid) from reporting_unit u
               where u.scheme_code = 'us.precinct' and u.code ~ '^[0-9-]{10}:CA:[0-9]{5}:[^:]+:[^:]+$'
                 and not exists (
                   select 1 from reporting_unit_area x join area a on a.id = x.area_id and a.scheme_code = 'us.precinct'
                   where x.unit_id = u.id)"""
        ).fetchall():
            day, state, county, place, precinct = code.split(":")
            area = shapes.get(f"{day}:{state}:{county}:{precinct}") or shapes.get(
                f"{day}:{state}:{county}:{precinct.lstrip('0').zfill(5)}"
            )
            if area is not None:
                rows.append((unit, area, start, end, None))
            elif (areas := yearly.get(f"{day[:4]}:{state}:{county}:{place}:{precinct}")) is not None:
                rows.extend((unit, found, start, end, None) for found in areas)
            else:
                unplaced += 1
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
