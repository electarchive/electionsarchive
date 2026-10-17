import re
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.vtd_links"
VERSION = "1"
NOISE = re.compile(r"\b(VOTING DISTRICT|VTD|PRECINCT|PREC|PCT|DISTRICT|DIST|WARD|WD|NO)\b")


def emit() -> Path:
    def norm(text: str) -> str:
        words = NOISE.sub(" ", text.upper())
        return "".join(part.lstrip("0") or "0" if part.isdigit() else part for part in re.findall(r"[A-Z]+|\d+", words))

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Approximate shapes for 2000–2014 precincts without an official one: the Census Bureau's 2000 "
            "and 2010 voting districts matched one to one within a county by code or name",
            polity="US",
        )
        units = pl.DataFrame(
            conn.execute(
                """select u.id::text, u.code, u.valid_from, u.valid_to, c.code from reporting_unit u
                   join area a on a.id = u.area_id and a.scheme_code = 'us.county'
                   join area_code c on c.area_id = a.id
                   where u.scheme_code = 'us.precinct' and u.area_relation = 'within'
                     and u.valid_from >= '2000-01-01' and u.valid_from < '2015-01-01'"""
            ).fetchall(),
            schema=["unit", "code", "valid_from", "valid_to", "county"],
            orient="row",
        ).with_columns(
            vintage=pl.when(pl.col("valid_from") < date(2010, 4, 1)).then(2000).otherwise(2010),
            year=pl.col("valid_from").dt.year(),
            state=pl.col("code").str.split(":").list.get(1),
            key=pl.col("code").str.split(":").list.last().map_elements(norm, return_dtype=pl.String),
        )
        vtds = pl.concat(
            [
                pl.read_parquet(DATA / "parsed" / "us.vtd" / f"vtd_{vintage}.parquet").select(
                    vintage=pl.lit(vintage),
                    area=pl.lit(f"{vintage}:") + pl.col("geoid"),
                    county=pl.col("geoid").str.slice(0, 5),
                    code=pl.col("vtdst").map_elements(norm, return_dtype=pl.String),
                    name=pl.col("name").map_elements(norm, return_dtype=pl.String),
                )
                for vintage in (2000, 2010)
            ]
        )
        areas = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.vtd'"""
            ).fetchall()
        }
        links = pl.DataFrame(schema={"unit": pl.String, "area": pl.String, "pass": pl.String})
        for side in ("code", "name"):
            left = units.join(links, on="unit", how="anti").filter(pl.col("key") != "")
            right = vtds.join(links.select("area").unique(), on="area", how="anti").filter(pl.col(side) != "")
            once = ["vintage", "county", "key"]
            left = left.filter(pl.len().over([*once, "valid_from"]) == 1)
            right = right.rename({side: "key"}).filter(pl.len().over(once) == 1)
            links = pl.concat([links, left.join(right, on=once).select("unit", "area", **{"pass": pl.lit(side)})])
        counts = Counter(links["area"].to_list())
        matched = units.join(links, on="unit").filter(pl.col("area").is_in(list(areas)))
        cs.frame(
            "reporting_unit_area",
            matched.select(
                unit_id="unit",
                area_id=pl.col("area").replace_strict(areas),
                valid_from="valid_from",
                valid_to="valid_to",
                approximate=pl.lit(True),
            ),
        )
        for year, state, total, linked in (
            units.join(matched.select("unit", linked=pl.lit(True)), on="unit", how="left")
            .group_by("year", "state")
            .agg(pl.len(), pl.col("linked").is_not_null().sum())
            .sort("year", "state")
            .iter_rows()
        ):
            print(f"{year} {state}: {linked} of {total} ({linked / total:.1%})")
        print(
            f"{matched.height} of {units.height} units linked to {len(counts)} voting districts, "
            f"{matched.filter(pl.col('pass') == 'code').height} by code"
        )
        matched.group_by("year", "state").agg(pl.len().alias("linked")).join(
            units.group_by("year", "state").len().rename({"len": "units"}), on=["year", "state"], how="right"
        ).sort("year", "state").write_csv(DATA / "parsed" / f"{SOURCE}.csv")
        return cs.write()
