import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.vest"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ACCESS = "https://dataverse.harvard.edu/api/access/datafile/"
FILES = {"GA": "11070054"}
DOCUMENTATION = ACCESS + "14304792"
PUBLISHER = "Voting and Election Science Team"
TITLE = "2020 Precinct-Level Election Results"
DAY = date(2020, 11, 3)
CONTEST = "2020-11-03:{state}:president"


def plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fetch() -> None:
    for file in FILES.values():
        raw.fetch(SOURCE, ACCESS + file, suffix=".zip")
    raw.fetch(SOURCE, DOCUMENTATION, suffix=".txt")


def parse() -> None:
    records = []
    for state, file in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, ACCESS + file).path) as archive:
            parts = {
                name.rsplit(".", 1)[1].lower(): archive.read(name)
                for name in archive.namelist()
                if name.count(".") == 1
            }
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        records += [
            (
                state,
                f"13{record['FIPS2']}",
                str(record["PRECINCT_I"]),
                str(record["PRECINCT_N"]),
                int(record["G20PRERTRU"]),
                int(record["G20PREDBID"]),
                json.dumps(shape.__geo_interface__),
            )
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and shape is not None
        ]
    shapes = pl.DataFrame(
        records,
        schema=["state", "county", "id", "name", "rep", "dem", "geojson"],
        orient="row",
    ).with_row_index("polygon")
    with connect() as conn:
        units = pl.DataFrame(
            conn.execute(
                """select u.code, left(u.code, 5),
                          sum(v.value) filter (where p.text = 'Republican Party'),
                          sum(v.value) filter (where p.text = 'Democratic Party')
                   from external_id x join result_set r on r.contest_id = x.entity_id
                   join result_value v on v.result_set_k = r.k and v.measure = 1
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   join contest_option o on o.k = v.option_k
                   join name p on p.entity_id = o.party_id and p.is_preferred and p.lang = 'en'
                   where x.scheme = 'us.contest' and x.value = any(%s)
                   group by u.code""",
                [[CONTEST.format(state=state) for state in FILES]],
            ).fetchall(),
            schema={"code": pl.String, "county": pl.String, "rep": pl.Int64, "dem": pl.Int64},
            orient="row",
        ).with_columns(precinct=pl.col("code").str.slice(6))
    counted = shapes.join(units, on=["county", "rep", "dem"]).filter(
        (pl.col("rep") + pl.col("dem") > 0) & (pl.len().over("county", "rep", "dem") == 1)
    )
    named = (
        shapes.with_columns(key=pl.col("name").map_elements(plain, return_dtype=pl.String))
        .join(
            units.with_columns(key=pl.col("precinct").map_elements(plain, return_dtype=pl.String)),
            on=["county", "key"],
            suffix="_unit",
        )
        .vstack(
            shapes.with_columns(key=pl.col("id").map_elements(plain, return_dtype=pl.String)).join(
                units.with_columns(key=pl.col("precinct").map_elements(plain, return_dtype=pl.String)),
                on=["county", "key"],
                suffix="_unit",
            )
        )
        .unique(["polygon", "code"])
    )
    matched = (
        pl.concat(
            [
                counted.select("polygon", "code", pl.lit("votes").alias("by")),
                named.select("polygon", "code", pl.lit("name").alias("by")),
            ]
        )
        .unique(["polygon", "code"], keep="first", maintain_order=True)
        .filter(pl.len().over("polygon") == 1, pl.len().over("code") == 1)
    )
    print(
        f"{matched.height} of {units.height} precincts matched to {shapes.height} shapes "
        f"({matched.filter(pl.col('by') == 'votes').height} by their presidential votes)"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    found = shapes.join(matched, on="polygon")
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)) shapes""",
                [found["code"].to_list(), found["name"].to_list(), found["geojson"].to_list()],
            ).fetchall(),
            schema=["code", "name", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 2020 precinct boundaries from the Voting and Election Science Team",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/K7760H",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
        for file in FILES.values():
            cs.artifact(raw.cached(SOURCE, ACCESS + file), source)
        cs.artifact(raw.cached(SOURCE, DOCUMENTATION), source)
        counties = {
            code: (area, text)
            for code, area, text in conn.execute(
                """select u.code, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county'"""
            ).fetchall()
        }
        cs.add(
            "boundary_set",
            scheme_code="us.precinct",
            vintage="2020",
            valid_from=DAY,
            valid_to=None,
            as_of=DAY,
        )
        for code, text, geom, point in frame.select("code", "name", "geom", "point").iter_rows():
            county, county_name = counties[code[:5]]
            area = cs.area("us.precinct", code)
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=county, valid_from=None)
            cs.add(
                "name",
                entity_id=area,
                text=f"{text.title()}, {county_name} County",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=DAY,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        return cs.write()
