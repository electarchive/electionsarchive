from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.census import SOURCE as CENSUS
from ea.sources.us.census import STATES
from ea.store import DATA, connect

SOURCE = "us.sld_land"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WRITERS = ["us.sld_shapes", "us.sld_shapes_2000", "us.nh_floterials"]
THIN = 0.0025

MASK = """
create temp table water as
select st_subdivide(st_difference(st_expand(st_envelope(g.geom), 0.5), l.geom), 255) geom
from area a join area_geometry g on g.area_id = a.id
cross join (select st_union(g.geom) geom from area a join area_geometry g on g.area_id = a.id
            where a.scheme_code = 'us.state' and g.valid @> '2020-06-01'::date) l
where a.scheme_code = 'us.state' and g.valid @> '2020-06-01'::date
"""
CLIP = """
with wet as (
  select g.area_id, g.valid, g.geom, st_union(w.geom) wet
  from area a join area_geometry g on g.area_id = a.id join changeset c on c.id = g.changeset_id
  join water w on w.geom && g.geom and st_intersects(w.geom, g.geom)
  where a.scheme_code in ('us.sldl', 'us.sldu') and c.pipeline = any(%(writers)s)
  group by g.area_id, g.valid, g.geom
),
parts as (
  select area_id, valid, (st_dump(st_collectionextract(st_intersection(geom, wet), 3))).geom part from wet
),
kept as (
  select area_id, valid, st_union(part) cut from parts
  where (st_maximuminscribedcircle(part)).radius >= %(thin)s group by area_id, valid
),
clipped as (
  select g.area_id, g.valid, g.valid_from_precision, g.valid_to_precision, g.quality, g.source_id,
         st_multi(st_collectionextract(st_makevalid(st_difference(g.geom, k.cut)), 3)) geom,
         st_area(g.geom::geography) before
  from kept k join area_geometry g on g.area_id = k.area_id and g.valid = k.valid
)
select area_id::text, lower(valid), upper(valid), valid_from_precision::text, valid_to_precision::text, quality::text,
       source_id::text, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex'),
       before, st_area(geom::geography)
from clipped where not st_isempty(geom)
"""


def fetch() -> None:
    pass


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute(MASK)
        conn.execute("create index on water using gist (geom)")
        frame = pl.DataFrame(
            conn.execute(CLIP, {"writers": [SOURCE, *WRITERS], "thin": THIN}).fetchall(),
            schema=[
                "area_id",
                "valid_from",
                "valid_to",
                "valid_from_precision",
                "valid_to_precision",
                "quality",
                "source_id",
                "geom",
                "label_point",
                "before",
                "after",
            ],
            orient="row",
        )
    frame.write_parquet(PARSED / "shapes.parquet")
    print(
        f"{frame.height} shapes clipped, {(frame['before'].sum() - frame['after'].sum()) / 1e6:,.0f} km² of water removed"
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative district shapes clipped to the shoreline of the Census 1:500,000 cartographic state boundaries, so districts no longer run into the Great Lakes and the sea",
            polity="US",
        )
        cs.manifest["scope"] = {"polity": "US", "replaces": {"area_geometry": WRITERS}}
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.artifact(raw.cached(CENSUS, STATES), census)
        cs.frame("area_geometry", pl.read_parquet(PARSED / "shapes.parquet").drop("before", "after"))
        return cs.write()
