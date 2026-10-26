import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "world.natural_earth"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson"
PLACES = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_populated_places.geojson"
PAGE = "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/"
CITIES = {
    "SANJOSE": "CRI",
    "ANDORRE": "AND",
    "ASSOMPTION": "PRY",
    "EKATERINBOURG": "RUS",
    "TANANARIVE": "MDG",
    "BAHAMASNASSAU": "BHS",
}
UNION_NOTE = (
    "This shape is the union of Natural Earth's outlines of the countries whose polling stations vote in this "
    "constituency. The decree that draws the constituency lists countries; it draws no boundary."
)
LANGS = {
    "NAME_EN": "en",
    "NAME_FR": "fr",
    "NAME_IT": "it",
    "NAME_PT": "pt",
    "NAME_DE": "de",
    "NAME_ES": "es",
    "NAME_TR": "tr",
}


def fold(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper())


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".geojson")
    raw.fetch(SOURCE, PLACES, suffix=".geojson")


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
    places: dict[str, tuple[str, int]] = {}
    for feature in json.loads(raw.cached(SOURCE, PLACES).path.read_bytes())["features"]:
        properties = feature["properties"]
        for field in ("NAME_FR", "NAME", "NAMEASCII", "NAME_EN"):
            if properties.get(field):
                key = fold(properties[field])
                if key not in places or (properties["POP_MAX"] or 0) > places[key][1]:
                    places[key] = (properties["ADM0_A3"], properties["POP_MAX"] or 0)
    pl.DataFrame(
        [(key, country) for key, (country, _) in places.items()], schema=["city", "country"], orient="row"
    ).write_parquet(PARSED / "places.parquet")
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
        cs.artifact(raw.cached(SOURCE, PLACES), source)
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
        areas = {row["code"]: cs.area("world.country", row["code"]) for row in frame.iter_rows(named=True)}
        aliases = {"SDS": "SSD", "PSX": "PSE", "SAH": "ESH"}
        cities = {
            city: aliases.get(country, country)
            for city, country in pl.read_parquet(PARSED / "places.parquet").iter_rows()
        } | CITIES
        constituencies: dict[UUID, set[UUID]] = {}
        for unit, text, constituency in conn.execute(
            """select r.id, n.text, m.area_id from reporting_unit r join name n on n.entity_id = r.id
               join reporting_unit_area m on m.unit_id = r.id join area a on a.id = m.area_id
               where r.scheme_code = 'fr.bureau' and r.kind = 'abroad_mission' and a.scheme_code = 'fr.circonscription'
               order by r.code"""
        ).fetchall():
            city = fold(text.split(", bureau")[0])
            if city not in cities or cities[city] not in areas:
                raise ValueError(f"no country for the French polling station in {text}")
            country = areas[cities[city]]
            cs.add("reporting_unit_area", unit_id=unit, area_id=country, valid_from=None, method="source_code")
            constituencies.setdefault(constituency, set()).add(country)
        for constituency, countries in constituencies.items():
            geom, point = conn.execute(
                """with u as (
                     select st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom
                     from area_geometry where area_id = any(%s)
                   )
                   select encode(st_asewkb(geom), 'hex'),
                          encode(st_asewkb((select st_pointonsurface(d.geom) from st_dump(geom) d
                                            order by st_area(d.geom::geography) desc limit 1)), 'hex')
                   from u""",
                [list(countries)],
            ).fetchall()[0]
            cs.add(
                "area_geometry",
                area_id=constituency,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="academic",
                source_id=source,
            )
            cs.add("note", entity_id=constituency, kind="methodology", body_md=UNION_NOTE)
        return cs.write()
