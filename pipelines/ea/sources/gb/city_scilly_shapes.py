import json
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.city_scilly_shapes"
VERSION = "1"
URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Wards_December_2022_Boundaries_UK_BGC"
    "/FeatureServer/0/query?where=LAD22CD+in+%28%27E09000001%27%2C%27E06000053%27%29&outFields=WD22CD,WD22NM,LAD22CD"
    "&outSR=27700&orderByFields=WD22CD&f=geojson"
)
TITLE = "Wards (December 2022) Boundaries UK BGC"
SINCE = {"E09000001": date(2013, 3, 8), "E06000053": date(2017, 5, 4)}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".geojson")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    features = json.loads(artifact.path.read_text())["features"]
    if len(features) != 30:
        raise ValueError(f"{len(features)} wards, expected 30")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ward boundaries for the City of London and the Isles of Scilly from ONS",
            polity="GB",
        )
        source = cs.source("Office for National Statistics", TITLE)
        if source in cs.minted:
            raise ValueError("publish gb.ons_local first: the ward layer must exist as a source")
        cs.artifact(artifact, source)
        conn.execute("create temp table feature (code text, council text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for feature in features:
                props = feature["properties"]
                copy.write_row((props["WD22CD"], props["LAD22CD"], json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select a.id, f.council,
                      encode(st_asewkb(st_multi(st_transform(g.geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(g.geom), 4326)), 'hex')
               from feature f
               cross join lateral (select st_multi(st_collectionextract(st_makevalid(
                   st_force2d(st_setsrid(st_geomfromgeojson(f.geojson), 27700))), 3)) as geom) g
               join area_code c on c.code = f.code
               join area a on a.id = c.area_id and a.scheme_code = 'gb.ward'
               where not exists (select from area_geometry x where x.area_id = a.id)"""
        ).fetchall()
        if len(rows) != len(features):
            raise ValueError(f"{len(rows)} of {len(features)} wards matched an unshaped gb.ward area")
        for area, council, geom, point in rows:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=SINCE[council],
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        return cs.write()
