import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "de.bkg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VG250 = "https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/2025/vg250_01-01.utm32s.shape.ebenen.zip"
GEMEINDEN = "vg250_01-01.utm32s.shape.ebenen/vg250_ebenen_0101/VG250_GEM"
BERLIN = (
    "https://gdi.berlin.de/services/wfs/alkis_bezirke?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
    "&TYPENAMES=alkis_bezirke:bezirksgrenzen&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"
)
HAMBURG = (
    "https://geodienste.hamburg.de/HH_WFS_Verwaltungsgrenzen?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
    "&TYPENAMES=de.hh.up:bezirke&OUTPUTFORMAT=application/geo%2Bjson&SRSNAME=EPSG:4326"
)
COUNTS = {"gemeinde": 10949, "kreis": 400, "bezirk": 19}
TOLERANCE = 10
ELECTION = date(2025, 2, 23)
SOURCES = {
    VG250: (
        "Bundesamt für Kartographie und Geodäsie",
        "Verwaltungsgebiete 1:250 000, Ebenen, Stand 01.01.2025 (VG250 01.01.)",
        "https://gdz.bkg.bund.de/index.php/default/verwaltungsgebiete-1-250-000-ebenen-stand-01-01-vg250-ebenen-01-01.html",
        "Datenlizenz Deutschland – Namensnennung – Version 2.0",
        "https://www.govdata.de/dl-de/by-2-0",
        "© GeoBasis-DE / BKG (2025)",
    ),
    BERLIN: (
        "Senatsverwaltung für Stadtentwicklung, Bauen und Wohnen Berlin",
        "ALKIS Berlin Bezirke",
        "https://gdi.berlin.de/services/wfs/alkis_bezirke",
        "Datenlizenz Deutschland – Zero – Version 2.0",
        "https://www.govdata.de/dl-de/zero-2-0",
        None,
    ),
    HAMBURG: (
        "Landesbetrieb Geoinformation und Vermessung Hamburg",
        "WFS ALKIS Verwaltungsgrenzen Hamburg: Bezirke",
        "https://geodienste.hamburg.de/HH_WFS_Verwaltungsgrenzen",
        "Datenlizenz Deutschland – Namensnennung – Version 2.0",
        "https://www.govdata.de/dl-de/by-2-0",
        "Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung",
    ),
}


def fetch() -> None:
    raw.fetch(SOURCE, VG250, suffix=".zip")
    for url in (BERLIN, HAMBURG):
        raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, VG250).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{GEMEINDEN}.shp")),
            shx=io.BytesIO(archive.read(f"{GEMEINDEN}.shx")),
            dbf=io.BytesIO(archive.read(f"{GEMEINDEN}.dbf")),
            encoding="utf-8",
        )
        gemeinden = [
            (record["AGS"], json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None and record["GF"] == 4
        ]
    bezirke = [
        (f"11{feature['properties']['gem']}", json.dumps(feature["geometry"]))
        for feature in json.loads(raw.cached(SOURCE, BERLIN).path.read_bytes())["features"]
    ] + [
        (f"02{int(feature['properties']['bezirk']):03d}", json.dumps(feature["geometry"]))
        for feature in json.loads(raw.cached(SOURCE, HAMBURG).path.read_bytes())["features"]
    ]
    with connect() as conn:
        conn.execute("create temp table feature (layer text, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (layer, code, geojson) from stdin") as copy:
            for code, geojson in gemeinden:
                copy.write_row(("gemeinde", code, geojson))
            for code, geojson in bezirke:
                copy.write_row(("bezirk", code, geojson))
        conn.execute(
            """create temp table gemeinde on commit drop as
               select code, st_multi(st_collectionextract(st_makevalid(st_union(
                        st_force2d(st_setsrid(st_geomfromgeojson(geojson), 25832)))), 3)) as geom
               from feature where layer = 'gemeinde' group by code"""
        )
        conn.execute(
            """create temp table shape on commit drop as
               select layer, code, st_setsrid(st_coveragesimplify(geom, %s) over (partition by layer), 25832) as geom
               from (select 'gemeinde' as layer, code, geom from gemeinde
                     union all
                     select 'bezirk', f.code, st_multi(st_collectionextract(st_intersection(
                              st_makevalid(st_force2d(st_transform(st_setsrid(st_geomfromgeojson(f.geojson), 4326), 25832))),
                              g.geom), 3))
                     from feature f join gemeinde g on g.code = left(f.code, 2) || '000000'
                     where f.layer = 'bezirk') features""",
            [TOLERANCE],
        )
        rows = conn.execute(
            """with layers as (
                 select layer, code, geom from shape
                 union all
                 select 'kreis', left(code, 5), st_setsrid(st_coverageunion(geom), 25832)
                 from shape where layer = 'gemeinde' group by left(code, 5)
               )
               select layer, code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from layers"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if counts != COUNTS or frame["geom"].null_count():
        raise ValueError(f"expected {COUNTS}, got {counts}")
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
                "Germany's Kreise and Gemeinden from BKG's VG250 as of 2025-01-01, "
                "and Berlin's and Hamburg's boroughs from the two cities' ALKIS services"
            ),
            polity="DE",
        )
        sources: dict[str, UUID] = {}
        for url, (publisher, title, page, license, license_url, attribution) in SOURCES.items():
            sources[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=page,
                license=license,
                license_url=license_url,
                attribution=attribution,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        for layer in COUNTS:
            shapes = {
                code: (geom, point)
                for code, geom, point in frame.filter(pl.col("layer") == layer)
                .select("code", "geom", "label_point")
                .rows()
            }
            known = conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = %s",
                [f"de.{layer}"],
            ).fetchall()
            if missing := sorted(code for code, _ in known if code not in shapes):
                raise ValueError(f"no shape for de.{layer} {missing}")
            for code, area in known:
                geom, point = shapes[code]
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    valid_to=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[BERLIN if code.startswith("11") else HAMBURG]
                    if layer == "bezirk"
                    else sources[VG250],
                )
            cs.add(
                "boundary_set",
                scheme_code=f"de.{layer}",
                vintage="2025",
                valid_from=None,
                valid_to=None,
                as_of=ELECTION,
            )
        return cs.write()
