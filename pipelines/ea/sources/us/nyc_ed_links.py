from datetime import date
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.nyc_ed_links"
VERSION = "2"
COUNTIES = "36005|36047|36061|36081|36085"
LEVELS = (
    ("us.sldu", "c.code like 'NY:%%'"),
    ("us.cd", "c.code ~ '^36[0-9]{2}$'"),
    ("us.local_district", "c.code like '36:3651000:council:%%'"),
)
WITHIN = 0.99
SLIVER = 0.02


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City election district units linked to the City Planning election district shape in "
            "force on their election day, and through it to their Assembly, State Senate, congressional, and City "
            "Council districts",
            polity="US-NY",
        )
        precincts = pl.DataFrame(
            conn.execute(
                f"""select u.id::text, u.code, u.valid_from, u.valid_to from reporting_unit u
                    join entity e on e.id = u.id
                    where u.scheme_code = 'us.precinct' and e.retired_by is null and u.valid_from is not null
                      and u.code ~ '^[0-9]{{4}}(-[0-9]{{2}}-[0-9]{{2}})?:NY:({COUNTIES}):([A-Z ]*:)?([0-9]{{5}}|[0-9]{{3}}/[0-9]{{2}})$'"""
            ).fetchall(),
            schema=["unit_id", "code", "valid_from", "valid_to"],
            orient="row",
        )
        last = pl.col("code").str.extract(r"([0-9]{3}/[0-9]{2}|[0-9]{5})$")
        precincts = precincts.with_columns(
            ed=pl.when(last.str.contains("/")).then(last.str.slice(4) + last.str.slice(0, 3)).otherwise(last),
            year=pl.col("code").str.slice(0, 4).cast(pl.Int32),
            dated=pl.col("code").str.contains(r"^[0-9]{4}-"),
        ).with_columns(
            day=pl.when(pl.col("dated"))
            .then(pl.col("code").str.slice(0, 10).str.to_date(strict=False))
            .otherwise(
                pl.col("year").replace_strict(
                    {
                        year: next(day for day in (date(year, 11, n) for n in range(2, 9)) if day.weekday() == 1)
                        for year in range(2000, 2031)
                    },
                    return_dtype=pl.Date,
                )
            )
        )
        areas = pl.DataFrame(
            conn.execute(
                """select c.code, c.area_id::text, c.valid_from, c.valid_to from area_code c
                   join area a on a.id = c.area_id where a.scheme_code = 'us.nyc_ed'"""
            ).fetchall(),
            schema=["ed", "area_id", "area_from", "area_to"],
            orient="row",
        )
        placed = (
            precincts.join(areas, on="ed")
            .filter(
                pl.col("day") >= pl.col("area_from"),
                pl.col("area_to").is_null() | (pl.col("day") < pl.col("area_to")),
            )
            .unique(["unit_id", "area_id"])
        )
        for year, total, matched in (
            precincts.join(placed.select("unit_id", hit=pl.lit(True)).unique("unit_id"), on="unit_id", how="left")
            .group_by("year")
            .agg(pl.len(), pl.col("hit").is_not_null().sum())
            .sort("year")
            .iter_rows()
        ):
            print(f"{year}: {matched} of {total}")
        print(f"{placed.height} of {precincts.height} precinct units placed in an election district")
        own = pl.DataFrame(
            conn.execute(
                """select u.id::text, u.code, u.area_id::text, u.valid_from, u.valid_to from reporting_unit u
                   join entity e on e.id = u.id
                   where u.scheme_code = 'us.nyc_ed' and e.retired_by is null and u.valid_from is not null
                     and u.area_id is not null and u.area_relation = 'equals'"""
            ).fetchall(),
            schema=["unit_id", "ed", "area_id", "valid_from", "valid_to"],
            orient="row",
        )
        nearest = pl.DataFrame(
            conn.execute(
                """select distinct on (u.id) u.id::text, u.code, c.area_id::text, u.valid_from, u.valid_to
                   from reporting_unit u join entity e on e.id = u.id
                   join area_code c on c.code = u.code
                   join area a on a.id = c.area_id and a.scheme_code = 'us.nyc_ed'
                   where u.scheme_code = 'us.nyc_ed' and e.retired_by is null and u.area_relation <> 'equals'
                     and u.valid_from is not null
                     and exists (select from result_value v where v.unit_k = u.k and v.measure = 1 and v.value > 0)
                   order by u.id, greatest(c.valid_from - u.valid_from, u.valid_from - coalesce(c.valid_to, u.valid_from), 0)"""
            ).fetchall(),
            schema=["unit_id", "ed", "area_id", "valid_from", "valid_to"],
            orient="row",
        )
        print(f"{nearest.height} unlocated election districts with votes linked approximately to the nearest edition")
        everyone = pl.concat(
            [
                placed.select("unit_id", "ed", "area_id", "valid_from", "valid_to", "day", approximate=pl.lit(False)),
                own.select(
                    "unit_id", "ed", "area_id", "valid_from", "valid_to", day="valid_from", approximate=pl.lit(False)
                ),
                nearest.select(
                    "unit_id", "ed", "area_id", "valid_from", "valid_to", day="valid_from", approximate=pl.lit(True)
                ),
            ]
        )
        unplaced = precincts.join(placed.select("unit_id").unique(), on="unit_id", how="anti").select(
            "unit_id", "ed", "valid_from", "valid_to", approximate=pl.lit(False)
        )
        everyone = pl.concat([everyone, unplaced], how="diagonal_relaxed").with_columns(
            ad=pl.col("ed").str.slice(0, 2).str.to_integer()
        )

        sldl = {
            f"{code}": area
            for code, area in conn.execute(
                """select c.code, c.area_id::text from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.sldl' and c.code like 'NY:%'"""
            ).fetchall()
        }
        overlaps = []
        for scheme, test in LEVELS:
            rows = conn.execute(
                f"""with ed as (
                      select g.area_id, g.valid_from vf, g.geom, st_area(g.geom) total from area_geometry g
                      join area a on a.id = g.area_id and a.scheme_code = 'us.nyc_ed'),
                    d as (
                      select g.area_id, g.geom, g.valid from area_geometry g
                      join area a on a.id = g.area_id and a.scheme_code = %s
                      join area_code c on c.area_id = a.id and {test})
                    select ed.area_id::text, ed.vf, d.area_id::text,
                           st_area(st_intersection(st_makevalid(ed.geom), st_makevalid(d.geom))) / ed.total
                    from ed join d on d.valid @> ed.vf and st_intersects(ed.geom, d.geom)""",
                [scheme],
            ).fetchall()
            overlaps.append(
                pl.DataFrame(
                    rows,
                    schema={"ed_area": pl.String, "vf": pl.Date, "district": pl.String, "share": pl.Float64},
                    orient="row",
                ).with_columns(level=pl.lit(scheme))
            )
        overlaps = pl.concat(overlaps).filter(pl.col("share") >= SLIVER)
        vintages = overlaps.select(area_id="ed_area", vf="vf").unique().sort("vf")
        shaped = (
            everyone.filter(pl.col("area_id").is_not_null())
            .sort("day")
            .join_asof(vintages, left_on="day", right_on="vf", by="area_id")
            .join(overlaps.rename({"ed_area": "area_id"}), on=["area_id", "vf"], how="inner")
            .select(
                "unit_id",
                area_id="district",
                valid_from="valid_from",
                valid_to="valid_to",
                approximate="approximate",
                partial=pl.col("share") < WITHIN,
                method=pl.lit("shape_contained"),
                level="level",
            )
        )
        coded = everyone.filter(pl.col("ad").cast(pl.String).is_in([key.split(":")[1] for key in sldl])).select(
            "unit_id",
            area_id=pl.col("ad")
            .cast(pl.String)
            .replace_strict({key.split(":")[1]: area for key, area in sldl.items()}),
            valid_from="valid_from",
            valid_to="valid_to",
            approximate="approximate",
            partial=pl.lit(False),
            method=pl.lit("source_code"),
            level=pl.lit("us.sldl"),
        )
        placement = pl.concat(
            [
                placed.select("unit_id", "area_id", "valid_from", "valid_to", approximate=pl.lit(False)),
                nearest.select("unit_id", "area_id", "valid_from", "valid_to", approximate=pl.lit(True)),
            ]
        ).select(
            "unit_id",
            "area_id",
            "valid_from",
            "valid_to",
            "approximate",
            partial=pl.lit(False),
            method=pl.lit("source_code"),
            level=pl.lit("us.nyc_ed"),
        )
        everything = pl.concat([placement, coded, shaped.select(placement.columns)]).unique(
            ["unit_id", "area_id"], keep="first", maintain_order=True
        )
        taken = pl.DataFrame(
            conn.execute(
                """select m.unit_id::text, m.area_id::text from reporting_unit_area m
                   join changeset c on c.id = m.changeset_id
                   where c.pipeline <> %s and m.unit_id = any(%s::uuid[])""",
                [SOURCE, everything["unit_id"].unique().to_list()],
            ).fetchall(),
            schema={"unit_id": pl.String, "area_id": pl.String},
            orient="row",
        )
        everything = everything.join(taken, on=["unit_id", "area_id"], how="anti")
        for level, count, units_ in (
            everything.group_by("level").agg(pl.len(), pl.col("unit_id").n_unique()).sort("level").iter_rows()
        ):
            print(f"{level}: {count} links over {units_} units")
        cs.frame("reporting_unit_area", everything.drop("level"))
        return cs.write()
