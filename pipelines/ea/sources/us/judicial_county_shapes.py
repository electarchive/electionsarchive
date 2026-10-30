import re
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.judicial_county_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    lists = pl.read_csv(Path(__file__).with_name("judicial_county_lists.tsv"), separator="\t")
    with connect() as conn:
        counties = {
            (
                text.rsplit(", ", 1)[1],
                re.sub(r"[^a-z]", "", text.rsplit(", ", 1)[0].removesuffix(" County").lower()),
            ): code
            for code, text in conn.execute(
                """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.text like '%% County, __'
                   where a.scheme_code = 'us.county' and exists (select 1 from area_geometry g where g.area_id = a.id)"""
            ).fetchall()
        }
        areas = dict(
            conn.execute(
                """select u.code, u.area_id::text from reporting_unit u
                   where u.scheme_code = 'us.judicial_district'
                     and not exists (select 1 from area_geometry g where g.area_id = u.area_id)"""
            ).fetchall()
        )
    rows = []
    for code, state, names, _ in lists.iter_rows():
        found = [counties.get((state, re.sub(r"[^a-z]", "", name.lower()))) for name in names.split(", ")]
        if code in areas and all(found):
            rows.append({"area": areas[code], "code": code, "counties": found})
        elif code in areas:
            print(f"{code}: unmatched counties in {names}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "matches.parquet")
    print(f"{len(rows)} of {lists.height} listed districts matched")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Judicial and prosecutorial districts made of whole counties, shaped as the union of their counties",
            polity="US",
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
