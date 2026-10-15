import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ie.tailte"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
COUNTIES = (
    "https://data-osi.opendata.arcgis.com/api/download/v1/items/dc24df2a5ce84ee9a38d9afe8431ee9b/geojson?layers=1"
)
CONSTITUENCIES = {
    "dublin": ("DUBLIN",),
    "midlands-north-west": (
        "CAVAN",
        "DONEGAL",
        "GALWAY",
        "KILDARE",
        "LAOIS",
        "LEITRIM",
        "LONGFORD",
        "LOUTH",
        "MAYO",
        "MEATH",
        "MONAGHAN",
        "OFFALY",
        "ROSCOMMON",
        "SLIGO",
        "WESTMEATH",
    ),
    "south": (
        "CARLOW",
        "CLARE",
        "CORK",
        "KERRY",
        "KILKENNY",
        "LIMERICK",
        "TIPPERARY",
        "WATERFORD",
        "WEXFORD",
        "WICKLOW",
    ),
}
TOLERANCE = 10
ENACTED = date(2023, 12, 19)
ELECTION = date(2024, 6, 7)


def fetch() -> None:
    raw.fetch(SOURCE, COUNTIES, suffix=".geojson")


def parse() -> None:
    features = json.loads(raw.cached(SOURCE, COUNTIES).path.read_bytes())["features"]
    counties = {county for counties in CONSTITUENCIES.values() for county in counties}
    with connect() as conn:
        conn.execute("create temp table feature (county text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (county, geojson) from stdin") as copy:
            for feature in features:
                copy.write_row((feature["properties"]["ENG_NAME_VALUE"].upper(), json.dumps(feature["geometry"])))
        conn.execute("create temp table member (county text, constituency text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy member (county, constituency) from stdin") as copy:
            for constituency, members in CONSTITUENCIES.items():
                for county in members:
                    copy.write_row((county, constituency))
        found = {county for (county,) in conn.execute("select distinct county from feature").fetchall()}
        if found != counties:
            raise ValueError(f"county names differ from the 2023 Act's schedule: {found ^ counties}")
        rows = conn.execute(
            """with county as (
                 select county, st_setsrid(st_coveragesimplify(geom, %s) over (), 2157) as geom
                 from (select county, st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(
                                st_force2d(st_setsrid(st_geomfromgeojson(geojson), 2157))))), 3)) as geom
                       from feature group by county) counties
               ),
               layers as (
                 select 'ep_constituency' as layer, m.constituency as code, st_setsrid(st_coverageunion(c.geom), 2157) as geom
                 from county c join member m using (county) group by m.constituency
                 union all
                 select 'nation', 'IE', st_setsrid(st_coverageunion(geom), 2157) from county
               )
               select layer, code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      case when layer <> 'nation'
                        then encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex') end
               from layers""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    if frame.height != 4 or frame["geom"].null_count():
        raise ValueError(f"expected three constituencies and the nation, got {frame.height} rows")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Ireland's three European Parliament constituencies of the Electoral (Amendment) Act 2023 "
                "and the nation, as unions of Tailte Éireann's statutory counties"
            ),
            polity="IE",
        )
        title = "Counties - National Statutory Boundaries - Ungeneralised - 2024"
        source = cs.source("Tailte Éireann", title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Tailte Éireann",
            title=title,
            url="https://data-osi.opendata.arcgis.com/datasets/dc24df2a5ce84ee9a38d9afe8431ee9b_1",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="© Tailte Éireann",
        )
        cs.artifact(raw.cached(SOURCE, COUNTIES), source)
        for layer, code, geom, point in frame.select("layer", "code", "geom", "label_point").rows():
            cs.add(
                "area_geometry",
                area_id=cs.area(f"ie.{layer}", code),
                valid_from=ENACTED if layer == "ep_constituency" else None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        cs.add(
            "boundary_set",
            scheme_code="ie.ep_constituency",
            vintage="2023",
            valid_from=ENACTED,
            valid_to=None,
            as_of=ELECTION,
        )
        return cs.write()
