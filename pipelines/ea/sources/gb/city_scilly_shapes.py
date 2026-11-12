import json
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb import ons_local
from ea.sources.gb.city_scilly import OLD_WARDS, WARDS_FROM
from ea.store import connect

SOURCE = "gb.city_scilly_shapes"
VERSION = "2"
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
URL = (
    f"{SERVICES}/Wards_December_2022_Boundaries_UK_BGC"
    "/FeatureServer/0/query?where=LAD22CD+in+%28%27E09000001%27%2C%27E06000053%27%29&outFields=WD22CD,WD22NM,LAD22CD"
    "&outSR=27700&orderByFields=WD22CD&f=geojson"
)
TITLE = "Wards (December 2022) Boundaries UK BGC"
URL_2011 = (
    f"{SERVICES}/Wards_December_2011_GCB_EW_2022/FeatureServer/0/query?where=wd11cdo+like+%2700AA%25%27"
    "&outFields=wd11cd,wd11nm&outSR=27700&orderByFields=wd11cd&f=geojson"
)
TITLE_2011 = "Wards (December 2011) Boundaries EW GCB"
SINCE = {"E09000001": WARDS_FROM, "E06000053": date(2017, 5, 4)}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".geojson")
    raw.fetch(SOURCE, URL_2011, suffix=".geojson")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    features = json.loads(artifact.path.read_text())["features"]
    if len(features) != 30:
        raise ValueError(f"{len(features)} wards, expected 30")
    old = raw.cached(SOURCE, URL_2011)
    old_features = json.loads(old.path.read_text())["features"]
    names = {code: label.removesuffix(" and Bridge Without") for code, label, _ in OLD_WARDS}
    if {f["properties"]["wd11cd"]: f["properties"]["wd11nm"] for f in old_features} != names:
        raise ValueError("the December 2011 City of London wards do not match the old ward list")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ward boundaries for the City of London, before and after its 2013 ward changes, and the Isles of "
            "Scilly from ONS",
            polity="GB",
        )
        source = cs.source(ons_local.PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError("publish gb.ons_local first: the ward layer must exist as a source")
        cs.artifact(artifact, source)
        source_2011 = cs.source(ons_local.PUBLISHER, TITLE_2011)
        cs.add(
            "source",
            id=source_2011,
            kind="official_body",
            publisher=ons_local.PUBLISHER,
            title=TITLE_2011,
            url=f"{SERVICES}/Wards_December_2011_GCB_EW_2022/FeatureServer",
            license=ons_local.LICENSE,
            license_url=ons_local.LICENSE_URL,
            attribution=ons_local.ATTRIBUTION,
            notes="Generalized boundaries clipped to the coastline, as they stood in December 2011.",
        )
        cs.artifact(old, source_2011)
        conn.execute("create temp table feature (code text, council text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for feature in features:
                props = feature["properties"]
                copy.write_row((props["WD22CD"], props["LAD22CD"], json.dumps(feature["geometry"])))
            for feature in old_features:
                copy.write_row((feature["properties"]["wd11cd"], "2011", json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select a.id, f.council,
                      encode(st_asewkb(st_multi(st_transform(g.geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(g.geom), 4326)), 'hex')
               from feature f
               cross join lateral (select st_multi(st_collectionextract(st_makevalid(
                   st_force2d(st_setsrid(st_geomfromgeojson(f.geojson), 27700))), 3)) as geom) g
               join area_code c on c.code = f.code
               join area a on a.id = c.area_id and a.scheme_code = 'gb.ward'"""
        ).fetchall()
        if len(rows) != len(features) + len(old_features):
            raise ValueError(f"{len(rows)} of {len(features) + len(old_features)} wards matched a gb.ward area")
        for area, council, geom, point in rows:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None if council == "2011" else SINCE[council],
                valid_to=WARDS_FROM if council == "2011" else None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source_2011 if council == "2011" else source,
            )
        return cs.write()
