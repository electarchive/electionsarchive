import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.umit import fold
from ea.store import DATA, connect

SOURCE = "gb.senedd_boundaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LAYERS = {
    ("gb.senc", "2026"): (
        "SENC_MAY_2026_WA_BGC",
        "SENC26NM",
        16,
        "Senedd Cymru Constituencies (May 2026) Boundaries WA BGC",
    ),
    ("gb.senc", "2007"): (
        "Senedd_Cymru_Constituencies_December_2022_Boundaries_WA_BGC",
        "SENC22NM",
        40,
        "Senedd Cymru Constituencies (December 2022) Boundaries WA BGC",
    ),
    ("gb.senr", "2007"): (
        "Senedd_Cymru_Electoral_Regions_December_2022_WA_BGC",
        "SENER22NM",
        5,
        "Senedd Cymru Electoral Regions (December 2022) Boundaries WA BGC",
    ),
}
ERAS = {
    "1999": (date(1999, 5, 6), date(2007, 5, 3)),
    "2007": (date(2007, 5, 3), date(2026, 5, 7)),
    "2026": (date(2026, 5, 7), None),
}
ONS = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)


def layer_url(service: str) -> str:
    return f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"


def fetch() -> None:
    for service, *_ in LAYERS.values():
        raw.fetch(SOURCE, layer_url(service), suffix=".geojson")


def parse() -> None:
    rows = []
    for (scheme, era), (service, field, count, _) in LAYERS.items():
        features = json.loads(raw.cached(SOURCE, layer_url(service)).path.read_text())["features"]
        if len(features) != count:
            raise ValueError(f"{scheme} {era}: {len(features)} features, expected {count}")
        rows += [(scheme, era, feature["properties"][field], json.dumps(feature["geometry"])) for feature in features]
    with connect() as conn:
        conn.execute("create temp table feature (scheme text, era text, name text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for row in rows:
                copy.write_row(row)
        shapes = conn.execute(
            """select scheme, era, name, encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select scheme, era, name, st_multi(st_collectionextract(st_makevalid(
                       st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom from feature) whole"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        shapes,
        schema={"scheme": pl.String, "era": pl.String, "name": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    ).write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Senedd constituency and electoral region boundaries for 1999 and 2007",
            polity="GB",
        )
        sources = {}
        for layer, (service, _, _, title) in LAYERS.items():
            sources[layer] = cs.source(ONS, title)
            cs.add(
                "source",
                id=sources[layer],
                kind="official_body",
                publisher=ONS,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes="Generalized boundaries (20 m) clipped to the coastline, in force "
                + ("from 2007 to 2026." if layer[1] == "2007" else "from 2026."),
            )
            cs.artifact(raw.cached(SOURCE, layer_url(service)), sources[layer])
        areas = {
            (scheme, code.split(":", 1)[0], fold(text)): area
            for scheme, code, text, area in conn.execute(
                """select a.scheme_code, c.code, n.text, a.id from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code in ('gb.senc', 'gb.senr')"""
            ).fetchall()
        }
        shapes = {
            (scheme, era, fold(name)): (geom, point, sources[scheme, era])
            for scheme, era, name, geom, point in frame.rows()
        }
        westminster = conn.execute(
            """select n.text, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'), g.source_id
               from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred
               join area_geometry g on g.area_id = a.id and g.valid @> %s::date
               where a.scheme_code = 'gb.pcon' and c.code ~ '^PCON01-(5[3-6][0-9])$'""",
            [ERAS["1999"][0]],
        ).fetchall()
        for text, geom, point, source in westminster:
            shapes[("gb.senc", "1999", fold(text))] = (geom, point, source)
        seats = {id: shapes[key][0] for key, id in areas.items() if key[:2] == ("gb.senc", "1999") and key in shapes}
        for region, geom, point in conn.execute(
            """select p.parent_id, encode(st_asewkb(st_multi(st_union(g2.geom))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_union(g2.geom))), 'hex')
               from area_parent p
               join unnest(%(ids)s::uuid[], %(geoms)s::text[]) g(area_id, hex) on g.area_id = p.area_id,
               lateral (select st_makevalid(st_geomfromewkb(decode(g.hex, 'hex'))) as geom) g2(geom)
               where p.valid @> %(start)s::date
               group by p.parent_id""",
            {"start": ERAS["1999"][0], "ids": list(seats), "geoms": list(seats.values())},
        ).fetchall():
            key = next(key for key, id in areas.items() if id == region)
            shapes[key] = (geom, point, None)
        westminster_source = {source for *_, source in westminster}
        if missing := sorted(f"{scheme} {era} {name}" for scheme, era, name in set(areas) - set(shapes)):
            raise ValueError(f"areas without a shape: {missing}")
        if extra := sorted(f"{scheme} {era} {name}" for scheme, era, name in set(shapes) - set(areas)):
            raise ValueError(f"shapes without an area: {extra}")
        for key, (geom, point, source) in shapes.items():
            scheme, era, _ = key
            cs.add(
                "area_geometry",
                area_id=areas[key],
                valid_from=ERAS[era][0],
                valid_to=ERAS[era][1],
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source if source is not None else next(iter(westminster_source)),
            )
        plans = {
            (scheme, vintage): id
            for id, scheme, vintage in conn.execute(
                "select id, scheme_code, vintage from boundary_set where scheme_code in ('gb.senc', 'gb.senr')"
            ).fetchall()
        }
        for scheme in ("gb.senc", "gb.senr"):
            for era, (start, end) in ERAS.items():
                if (scheme, era) == ("gb.senr", "2026"):
                    continue
                cs.add(
                    "boundary_set",
                    **({"id": plans[(scheme, era)]} if (scheme, era) in plans else {}),
                    scheme_code=scheme,
                    vintage=era,
                    valid_from=start,
                    valid_to=end,
                    as_of=start,
                )
        return cs.write()
