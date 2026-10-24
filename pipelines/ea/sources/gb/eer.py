import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.eu import gisco
from ea.store import DATA, connect

SOURCE = "gb.eer"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICE = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/EER_Dec_2018_GCB_UK_2022/FeatureServer"
LAYER = f"{SERVICE}/0/query?where=1%3D1&outFields=eer18cd,eer18nm&outSR=27700&f=geojson"
TITLE = "European Electoral Regions (December 2018) Boundaries UK GCB"
ONS = "Office for National Statistics"
COUNTRIES = gisco.DATASETS["countries"][0]
DIRECT = date(1979, 6, 7)
REGIONS = date(1999, 1, 14)
GIBRALTAR = date(2003, 5, 8)
WITHDRAWAL = date(2020, 2, 1)
CODES = {
    "E15000001": "north-east",
    "E15000002": "north-west",
    "E15000003": "yorkshire-and-the-humber",
    "E15000004": "east-midlands",
    "E15000005": "west-midlands",
    "E15000006": "eastern",
    "E15000007": "london",
    "E15000008": "south-east",
    "E15000009": "south-west",
    "N07000001": "northern-ireland",
    "S15000001": "scotland",
    "W08000001": "wales",
}
NOTES = (
    "Generalized (20 m) boundaries clipped to the coastline. The regions are those of the European Parliamentary "
    "Elections Act 1999 and did not move before the UK left in 2020, so the December 2018 map serves 1999–2020; "
    "Northern Ireland, a single constituency since 1979, uses it from 1979. Gibraltar joins South West from the "
    "European Parliament (Representation) Act 2003 (royal assent 2003-05-08); ONS does not map it, so its shape is "
    "Eurostat GISCO's (Countries 2024, 1:1 million, © EuroGeographics, free for non-commercial use)."
)


def fetch() -> None:
    raw.fetch(SOURCE, LAYER, suffix=".geojson")


def parse() -> None:
    features = json.loads(raw.cached(SOURCE, LAYER).path.read_text())["features"]
    if sorted(feature["properties"]["eer18cd"] for feature in features) != sorted(CODES):
        raise ValueError(f"unexpected regions: {[feature['properties']['eer18cd'] for feature in features]}")
    gibraltar = next(
        json.dumps(feature["geometry"])
        for feature in json.loads(raw.cached(gisco.SOURCE, COUNTRIES).path.read_text())["features"]
        if feature["properties"]["CNTR_ID"] == "GI"
    )
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for feature in features:
                copy.write_row((CODES[feature["properties"]["eer18cd"]], json.dumps(feature["geometry"])))
        conn.execute(
            """create temp table shape on commit drop as
               select code, %s::date as valid_from, %s::date as valid_to,
                      st_multi(st_collectionextract(st_makevalid(st_transform(
                        st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700)), 4326)), 3)) as geom
               from feature""",
            [REGIONS, WITHDRAWAL],
        )
        conn.execute("update shape set valid_from = %s where code = 'northern-ireland'", [DIRECT])
        conn.execute(
            """insert into shape
               select code, %s, valid_to, st_multi(st_collectionextract(st_union(geom,
                        st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(%s), 4326)))), 3))
               from shape where code = 'south-west'""",
            [GIBRALTAR, gibraltar],
        )
        conn.execute(
            "update shape set valid_to = %s where code = 'south-west' and valid_from = %s", [GIBRALTAR, REGIONS]
        )
        rows = conn.execute(
            """select code, valid_from, valid_to, encode(st_asewkb(geom), 'hex'),
                      encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from shape order by code, valid_from"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "code": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    print(frame.height, "shapes")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="ONS shapes for the twelve UK European Parliament electoral regions, 1999–2020, with Gibraltar in "
            "South West from 2003 and Northern Ireland from 1979",
            polity="GB",
        )
        cs.manifest["scope"] = {"polity": "GB", "replaces": {"area_geometry": ["eu.gisco"]}}
        source = cs.source(ONS, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ONS,
            title=TITLE,
            url=SERVICE,
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution=(
                "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
                "Contains OS data © Crown copyright and database right"
            ),
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, LAYER), source)
        cs.artifact(raw.cached(gisco.SOURCE, COUNTRIES), source)
        areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.ep_constituency'"""
            ).fetchall()
        )
        for code, start, end, geom, point in frame.rows():
            cs.add(
                "area_geometry",
                area_id=areas[code],
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        existing = conn.execute(
            "select id from boundary_set where scheme_code = 'gb.ep_constituency' and vintage = '1979'"
        ).fetchone()
        cs.add(
            "boundary_set",
            **({"id": existing[0]} if existing else {}),
            scheme_code="gb.ep_constituency",
            vintage="1979",
            valid_from=DIRECT,
            valid_to=REGIONS,
            as_of=DIRECT,
        )
        return cs.write()
