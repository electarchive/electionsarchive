from itertools import pairwise
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea.changeset import NAMESPACE, Changeset
from ea.store import connect

SOURCE = "gb.crosswalks"
VERSION = "1"
VINTAGES = ["1885", "1918", "1950", "1955", "1974", "1983", "1997", "2005", "2010", "2024"]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Estimated crosswalks between consecutive UK Parliament constituency boundary sets, 1885–2024, "
            "weighting each constituency by the share of its land, as mapped in both sets, inside each constituency of the neighboring set",
            polity="GB",
        )
        sets = {
            vintage: (id, valid)
            for id, vintage, valid in conn.execute(
                "select id, vintage, valid from boundary_set where scheme_code = 'gb.pcon'"
            ).fetchall()
        }
        conn.execute("set work_mem = '512MB'")
        conn.execute("create temp table side (plan text, whole boolean, k int, geom geometry)")
        for vintage in VINTAGES:
            _, valid = sets[vintage]
            conn.execute(
                """insert into side
                   select %(vintage)s, g.valid @> lower(%(valid)s::daterange), a.k,
                          st_makevalid(st_transform(g.geom, 27700))
                   from area a join area_geometry g on g.area_id = a.id
                   where a.scheme_code = 'gb.pcon' and g.quality <> 'synthetic' and g.valid && %(valid)s::daterange""",
                {"vintage": vintage, "valid": valid},
            )
        conn.execute("create index on side using gist (geom)")
        conn.execute("analyze side")
        for earlier, later in pairwise(VINTAGES):
            for source, target in ((earlier, later), (later, earlier)):
                weights = pl.DataFrame(
                    conn.execute(
                        """select f.k, t.k, sum(st_area(st_intersection(f.geom, t.geom)))
                                  / sum(sum(st_area(st_intersection(f.geom, t.geom)))) over (partition by f.k)
                           from side f join side t on st_intersects(f.geom, t.geom)
                           where f.plan = %s and t.plan = %s and t.whole
                           group by f.k, t.k""",
                        [source, target],
                    ).fetchall(),
                    schema={"from_k": pl.Int32, "to_k": pl.Int32, "weight": pl.Float64},
                    orient="row",
                ).filter(pl.col("weight") >= 0.0005)
                if weights.is_empty():
                    continue
                crosswalk = uuid5(NAMESPACE, f"{SOURCE}\x1f{sets[source][0]}\x1f{sets[target][0]}\x1f{VERSION}")
                cs.add(
                    "crosswalk",
                    id=crosswalk,
                    from_kind="boundary_set",
                    from_boundary_set_id=sets[source][0],
                    to_boundary_set_id=sets[target][0],
                    method="area",
                )
                cs.frame(
                    "crosswalk_weight",
                    weights.select(
                        crosswalk_id=pl.lit(str(crosswalk)),
                        from_k="from_k",
                        to_k="to_k",
                        weight=pl.col("weight").round(6).cast(pl.Decimal(10, 6)),
                        exact=pl.lit(False),
                    ),
                )
                print(f"gb.pcon {source} -> {target}: {weights.height} weights, {weights['from_k'].n_unique()} units")
        return cs.write()
