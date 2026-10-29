from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.sld_nv_numbered"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    with connect() as conn:
        rows = conn.execute(
            """with twins as (
                   select a.id, ac.code, (regexp_match(ac.code, '^NV:[a-z]+-(\\d+)$'))[1] as n
                   from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code = 'us.sldl' and ac.code ~ '^NV:[a-z]+-\\d+$'
                     and not exists (select 1 from area_geometry g where g.area_id = a.id)),
               numbered as (
                   select distinct on (ac.code, g.valid) ac.code, g.valid_from, g.valid_to, g.source_id, g.geom, g.label_point
                   from area a join area_code ac on ac.area_id = a.id join area_geometry g on g.area_id = a.id
                   where a.scheme_code = 'us.sldl' and ac.code ~ '^NV:\\d+$')
               select t.id::text, t.code, n.valid_from, n.valid_to, n.source_id::text,
                      encode(st_asewkb(n.geom), 'hex'), encode(st_asewkb(n.label_point), 'hex')
               from twins t join numbered n on n.code = 'NV:' || t.n
               where exists (select 1 from contest c join election e on e.id = c.election_id
                             where c.district_area_id = t.id and e.date_from <@ daterange(n.valid_from, n.valid_to, '[)'))"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema=["area_id", "code", "valid_from", "valid_to", "source_id", "geom", "label_point"],
        orient="row",
    )
    frame.write_parquet(PARSED / "twins.parquet")
    print(f"{frame.height} shapes on {frame['area_id'].n_unique()} districts")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "twins.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nevada Assembly districts that Klarner names by county and number, shaped from the numbered districts they repeat",
            polity="US",
        )
        out = frame.drop("code").with_columns(
            valid_from_precision=pl.lit("year"),
            valid_to_precision=pl.lit("year"),
            quality=pl.lit("official"),
        )
        cs.frame("area_geometry", out)
        print(f"{out.height} shapes")
        return cs.write()
