from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.phila_units"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Philadelphia's election divisions placed on their ward and division shapes, and named by ward and division",
            polity="US",
        )
        shapes: dict[str, list[tuple[str, str]]] = {}
        for code, area in conn.execute(
            """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
               where a.scheme_code = 'us.precinct' and c.code ~ '^[0-9-]{10}:PA:42101:[0-9]{4}$'"""
        ).fetchall():
            vintage, _, _, division = code.split(":")
            shapes.setdefault(division, []).append((vintage, area))
        rows = []
        unplaced = 0
        for unit, code, start, end, named in conn.execute(
            """select u.id::text, u.code, lower(u.valid), upper(u.valid),
                      exists (select 1 from name n where n.entity_id = u.id and n.kind = 'official')
               from reporting_unit u where u.scheme_code = 'us.precinct' and u.code ~ ':PA:42101:PHILADELPHIA:[0-9]{4}$'"""
        ).fetchall():
            division = code[-4:]
            earlier = [(vintage, area) for vintage, area in shapes.get(division, []) if vintage <= str(start)]
            if not earlier:
                unplaced += 1
                continue
            rows.append((unit, max(earlier)[1], start, end, None))
            if not named:
                cs.add(
                    "name",
                    entity_id=unit,
                    text=f"Ward {int(division[:2])} Division {int(division[2:])}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
        print(f"{len(rows)} divisions placed, {unplaced} without a shape")
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
