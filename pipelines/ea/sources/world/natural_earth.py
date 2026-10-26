import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "world.natural_earth"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson"
PAGE = "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/"
LANGS = {
    "NAME_EN": "en",
    "NAME_FR": "fr",
    "NAME_IT": "it",
    "NAME_PT": "pt",
    "NAME_DE": "de",
    "NAME_ES": "es",
    "NAME_TR": "tr",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".geojson")


def parse() -> None:
    features = json.loads(raw.cached(SOURCE, URL).path.read_bytes())["features"]
    rows: list[dict[str, str | None]] = []
    for feature in features:
        properties = feature["properties"]
        code = properties["ISO_A3"] if properties["ISO_A3"] != "-99" else properties["ADM0_A3"]
        rows.append(
            {
                "code": code,
                "continent": properties["CONTINENT"],
                "geojson": json.dumps(feature["geometry"]),
                **{lang: properties[field] or None for field, lang in LANGS.items()},
            }
        )
    frame = pl.DataFrame(rows)
    if frame["code"].n_unique() != frame.height:
        raise ValueError("country codes are not unique")
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (code, geojson) from stdin") as copy:
            for code, geojson in frame.select("code", "geojson").iter_rows():
                copy.write_row((code, geojson))
        shapes = conn.execute(
            """with shapes as (
                 select code, st_multi(st_collectionextract(st_makevalid(
                          st_force2d(st_setsrid(st_geomfromgeojson(geojson), 4326))), 3)) as geom
                 from feature
               )
               select code, encode(st_asewkb(geom), 'hex'),
                      encode(st_asewkb((select st_pointonsurface(d.geom) from st_dump(geom) d
                                        order by st_area(d.geom::geography) desc limit 1)), 'hex')
               from shapes"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("geojson").join(
        pl.DataFrame(shapes, schema=["code", "geom", "label_point"], orient="row"), on="code", how="left"
    ).write_parquet(PARSED / "countries.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "countries.parquet")
    if frame["geom"].null_count():
        raise ValueError("a country has no shape")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The world's countries and dependencies from Natural Earth's 1:10m admin-0 countries",
            polity=None,
        )
        source = cs.source("Natural Earth", "Admin 0 – Countries, 1:10m")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Natural Earth",
            title="Admin 0 – Countries, 1:10m",
            url=PAGE,
            license="Public domain",
            license_url="https://www.naturalearthdata.com/about/terms-of-use/",
            attribution="Made with Natural Earth",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        cs.add("scheme", code="world.country", polity_id=None, kind="administrative", rank=0, noun="country")
        for row in frame.iter_rows(named=True):
            area = cs.area("world.country", row["code"])
            cs.add("area", id=area, scheme_code="world.country")
            cs.add("area_code", area_id=area, code=row["code"], valid_from=None)
            for lang in LANGS.values():
                if row[lang] is not None:
                    cs.add(
                        "name", entity_id=area, text=row[lang], lang=lang, kind="official", is_preferred=lang == "en"
                    )
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=row["geom"],
                label_point=row["label_point"],
                quality="academic",
                source_id=source,
            )
        cs.add("boundary_set", scheme_code="world.country", vintage="5.1", valid_from=None, as_of=date(2026, 10, 26))
        return cs.write()
