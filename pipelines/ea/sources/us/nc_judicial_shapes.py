import re
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nc_judicial_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.removesuffix(", NC").removesuffix(" County").lower()): code
            for code, text in conn.execute(
                """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.text like '%% County, NC'
                   where a.scheme_code = 'us.county' and exists (select 1 from area_geometry g where g.area_id = a.id)"""
            ).fetchall()
        }
        wanted = conn.execute(
            """select u.area_id::text, u.code, nt.body_md from reporting_unit u
               join note nt on nt.entity_id = u.polity_id and nt.kind = 'context'
               where u.scheme_code = 'us.judicial_district' and u.code ~ '^NC:[a-z-]+:[0-9]+$'
                 and not exists (select 1 from area_geometry g where g.area_id = u.area_id)"""
        ).fetchall()
    rows = []
    for area, code, body in wanted:
        names = [
            re.sub(r"[^a-z]", "", name.lower())
            for name in body.removeprefix("Counties on the 2026 ballot: ").rstrip(".").split(", ")
        ]
        if all(name in counties for name in names):
            rows.append({"area": area, "code": code, "counties": [counties[name] for name in names]})
        else:
            print(f"{code}: unmatched counties in {body}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "matches.parquet")
    print(f"{len(rows)} of {len(wanted)} whole-county judicial districts matched")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina prosecutorial, district court, and superior court districts shaped as the union of their counties",
            polity="US-NC",
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for area, counties in zip(frame["area"], frame["counties"], strict=True):
            geom, point = conn.execute(
                """with latest as (
                       select distinct on (ac.code) ac.code, g.geom from area_geometry g
                       join area_code ac on ac.area_id = g.area_id
                       where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                   select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex') from latest""",
                [list(counties)],
            ).fetchone()
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
        return cs.write()
