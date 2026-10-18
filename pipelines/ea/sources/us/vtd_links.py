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
PENNSYLVANIA = re.compile(r"\b(TWP|TOWNSHIP|BORO|BOROUGH|ED)\b")


def emit() -> Path:
    def norm(text: str) -> str:
        words = NOISE.sub(" ", text.upper())
        return "".join(part.lstrip("0") or "0" if part.isdigit() else part for part in re.findall(r"[A-Z]+|\d+", words))

    def unit(state: str, vintage: int, name: str) -> tuple[str, str]:
        if state == "PA":
            name = PENNSYLVANIA.sub(" ", re.sub(r"^\d+\s+(?=[A-Za-z])|\(\d+\)", "", name.upper()))
        elif state == "FL":
            name = re.sub(r"^[A-Z]{3}(?=\w*\d)", "", name)
        elif state == "VA" and (split := re.fullmatch(r"(\w+)\s+-\s+(.+)", name)):
            return norm(split[1]), norm(split[2])
        elif state == "OK" and re.fullmatch(r"\d{6}", name):
            return norm(name[2:]), ""
        elif state == "CO" and re.fullmatch(r"\d{10}", name):
            return norm(name[-5:] if vintage == 2010 else name[-3:]), norm(name)
        elif state == "WI" and (ward := re.search(r"([A-Za-z][A-Za-z .'-]*? - [CTV] \d+(?: [A-Z])?)$", name)):
            name = ward[1]
        elif state == "WY" and (pair := re.search(r"(\d+)-(\d+)$", name)):
            return f"D{int(pair[1])}P{int(pair[2])}", ""
        return norm(name), norm(name)

    def district(state: str, code: str, name: str) -> tuple[str, str]:
        if state == "56" and (pair := re.fullmatch(r"(\d+)-(\d+)|(\d{2})(\d{2})", code)):
            return f"D{int(pair[1] or pair[3])}P{int(pair[2] or pair[4])}", norm(name)
        return norm(code), norm(name)

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
                """select u.id::text, u.code, u.valid_from, u.valid_to, c.code, ch.pipeline from reporting_unit u
                   join area a on a.id = u.area_id and a.scheme_code = 'us.county'
                   join area_code c on c.area_id = a.id join changeset ch on ch.id = u.changeset_id
                   where u.scheme_code = 'us.precinct' and u.area_relation = 'within'
                     and u.valid_from >= '2000-01-01' and u.valid_from < '2015-01-01'"""
            ).fetchall(),
            schema=["unit", "code", "valid_from", "valid_to", "county", "pipeline"],
            orient="row",
        ).with_columns(
            vintage=pl.when(pl.col("valid_from") < date(2010, 4, 1)).then(2000).otherwise(2010),
            year=pl.col("valid_from").dt.year(),
            state=pl.col("code").str.split(":").list.get(1),
        )
        units = units.with_columns(
            pl.Series(
                "keys",
                [
                    list(unit(state, vintage, code.split(":")[-1].strip()))
                    for state, vintage, code in units.select("state", "vintage", "code").iter_rows()
                ],
                dtype=pl.List(pl.String),
            )
        ).with_columns(code_key=pl.col("keys").list.get(0), name_key=pl.col("keys").list.get(1))
        vtds = pl.concat(
            [
                pl.read_parquet(DATA / "parsed" / "us.vtd" / f"vtd_{vintage}.parquet")
                .select(
                    vintage=pl.lit(vintage),
                    area=pl.lit(f"{vintage}:") + pl.col("geoid"),
                    county=pl.col("geoid").str.slice(0, 5),
                    keys=pl.struct("geoid", "vtdst", "name").map_elements(
                        lambda row: list(district(row["geoid"][:2], row["vtdst"], row["name"])),
                        return_dtype=pl.List(pl.String),
                    ),
                )
                .select("vintage", "area", "county", code=pl.col("keys").list.get(0), name=pl.col("keys").list.get(1))
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
            left = units.join(links, on="unit", how="anti").rename({f"{side}_key": "key"}).filter(pl.col("key") != "")
            right = vtds.join(links.select("area").unique(), on="area", how="anti").filter(pl.col(side) != "")
            once = ["vintage", "county", "key"]
            left = left.filter(
                pl.col("code").str.split(":").list.last().n_unique().over([*once, "valid_from", "pipeline"]) == 1
            )
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
