import re
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_units"
VERSION = "6"
VEST_DAYS = ("2020-11-03",)
VEST_DROP = "The precinct sits on VEST's shape for the same election, the layer us.precinct shows for this day"


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
        yearly: dict[str, list[str]] = {
            code: [area]
            for code, area in conn.execute(
                """select s.code, s.area_id::text from reporting_unit s
                   join area a on a.id = s.area_id and a.scheme_code = 'us.precinct'
                   where s.scheme_code = 'us.precinct' and s.area_relation = 'equals'
                     and s.code ~ '^[0-9]{4}:CA:[0-9]{5}:[^:]+:[^:]+$'"""
            ).fetchall()
        }
        vest = {
            f"{code[:4]}:{county}:{precinct.lstrip('0')}": area
            for code, area, county, precinct in conn.execute(
                """select c.code, a.id::text, pc.code, substring(n.text from '^Precinct (\\S+), ')
                   from area a join area_code c on c.area_id = a.id
                   join area_parent p on p.area_id = a.id join area_code pc on pc.area_id = p.parent_id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.precinct' and c.code ~ '^[0-9]{4}:CA:[0-9]+$'
                     and left(c.code, 4) = any(%s) and pc.code ~ '^06[0-9]{3}$' and n.text ~ '^Precinct \\S+, '""",
                [[day[:4] for day in VEST_DAYS]],
            ).fetchall()
        }
        rows = []
        drops = []
        unplaced = 0
        for unit, code, start, end, linked, placed in conn.execute(
            """select u.id::text, u.code, lower(u.valid), upper(u.valid),
                      array(select x.area_id::text from reporting_unit_area x
                            join changeset s on s.id = x.changeset_id and s.pipeline = %s
                            join area a on a.id = x.area_id and a.scheme_code = 'us.precinct'
                            join area_code c on c.area_id = a.id and c.code like left(u.code, 10) || ':%%'
                            where x.unit_id = u.id),
                      exists (select 1 from reporting_unit_area x
                              join area a on a.id = x.area_id and a.scheme_code = 'us.precinct'
                              where x.unit_id = u.id)
               from reporting_unit u
               where u.scheme_code = 'us.precinct' and u.code ~ '^[0-9-]{10}:CA:[0-9]{5}:[^:]+:[^:]+$'
                 and (left(u.code, 10) = any(%s) or not exists (
                   select 1 from reporting_unit_area x join area a on a.id = x.area_id and a.scheme_code = 'us.precinct'
                   where x.unit_id = u.id))""",
            [SOURCE, list(VEST_DAYS)],
        ).fetchall():
            day, state, county, place, precinct = code.split(":")
            if (
                day in VEST_DAYS
                and (linked or not placed)
                and (area := vest.get(f"{day[:4]}:{county}:{precinct.lstrip('0')}")) is not None
            ):
                rows.append((unit, area, start, end, None))
                drops.extend((unit, old) for old in linked)
                continue
            if placed:
                continue
            keys = [precinct, precinct.lstrip("0").zfill(5)]
            if (suffixed := re.match(r"^\d+-(\d+)-", precinct)) is not None:
                keys.append(suffixed[1])
            area = next(
                (found for key in keys if (found := shapes.get(f"{day}:{state}:{county}:{key}")) is not None), None
            )
            if area is not None:
                rows.append((unit, area, start, end, None))
            elif (areas := yearly.get(f"{day[:4]}:{state}:{county}:{place}:{precinct}")) is not None:
                rows.extend((unit, found, start, end, None) for found in areas)
            else:
                unplaced += 1
        print(f"{len(rows)} precincts placed, {unplaced} without a shape, {len(drops)} moved onto VEST's shapes")
        for unit, area in drops:
            cs.add("unit_area_drop", unit_id=unit, area_id=area, reason=VEST_DROP)
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
