from datetime import date
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_floterials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PLANS = ((date(2012, 1, 1), date(2022, 1, 1)), (date(2022, 1, 1), None))


def fetch() -> None:
    pass


def parse() -> None:
    with connect() as conn:
        contests = conn.execute(
            """select c.district_area_id, e.date_from, array_agg(distinct substr(u.code, 6)),
                      exists (select from area_geometry g where g.area_id = c.district_area_id and g.valid @> e.date_from)
               from external_id x join contest c on c.id = x.entity_id join election e on e.id = c.election_id
               join result_set r on r.contest_id = c.id and r.status = 'active'
               join result_value v on v.result_set_k = r.k
               join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
               where x.scheme = 'us.contest' and x.value ~ '^20[0-9][0-9]-..-..:NH:house:[0-9a-z-]+:g$'
               group by 1, 2 order by 2"""
        ).fetchall()
        rows = []
        seen = set()
        for area, day, units, shaped in contests:
            plan = next((plan for plan in PLANS if plan[0] <= day and (plan[1] is None or day < plan[1])), None)
            if shaped or plan is None or (area, plan) in seen:
                continue
            members = [
                base for base, other, parts, has in contests if has and other == day and set(parts) <= set(units)
            ]
            spanned = {part for base, other, parts, _ in contests if base in members and other == day for part in parts}
            if not members or spanned != set(units):
                print(f"{day} {area}: no exact union of base districts")
                continue
            found = conn.execute(
                """select encode(st_asewkb(st_multi(st_union(g.geom))), 'hex'), min(g.source_id::text)
                   from area_geometry g where g.area_id = any(%s) and g.valid @> %s""",
                [members, day],
            ).fetchone()
            geom, source = found
            seen.add((area, plan))
            overlap = conn.execute(
                "select 1 from area_geometry where area_id = %s and valid && daterange(%s, %s)", [area, *plan]
            ).fetchone()
            if overlap:
                print(f"{day} {area}: a shape already overlaps {plan}")
                continue
            point = conn.execute(
                "select encode(st_asewkb(st_pointonsurface(st_geomfromewkb(decode(%s, 'hex')))), 'hex')", [geom]
            ).fetchone()[0]
            rows.append((str(area), *plan, geom, point, source, len(members)))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema=["area_id", "valid_from", "valid_to", "geom", "label_point", "source_id", "members"],
        orient="row",
    )
    frame.write_parquet(PARSED / "floterials.parquet")
    print(f"{frame.height} floterial shapes on {frame['area_id'].n_unique()} districts")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "floterials.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire House floterial district shapes, each the union of the base districts whose towns and wards it spans",
            polity="US-NH",
        )
        cs.frame(
            "area_geometry",
            frame.drop("members").with_columns(
                valid_from_precision=pl.lit("year"), valid_to_precision=pl.lit("year"), quality=pl.lit("official")
            ),
        )
        return cs.write()
