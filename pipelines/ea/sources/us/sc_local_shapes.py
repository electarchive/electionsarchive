import io
import json
import zipfile
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.sc_local_2026 import PLACES, SCHOOLS
from ea.store import connect

SOURCE = "us.sc_local_shapes"
VERSION = "1"
CENSUS = "Cartographic Boundary Files, 1:500,000 (2024)"
LAYERS = {
    "us.place": ("https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_45_place_500k.zip", PLACES),
    "us.school_district": ("https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_45_unsd_500k.zip", SCHOOLS),
}


def fetch() -> None:
    for url, _ in LAYERS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def emit() -> Path:
    records: list[tuple[str, str, str]] = []
    for scheme, (url, wanted) in LAYERS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        found = {
            str(record["GEOID"]): json.dumps(shape.__geo_interface__)
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and shape is not None and str(record["GEOID"]) in wanted
        }
        if set(found) != set(wanted):
            raise ValueError(f"{scheme}: missing {sorted(set(wanted) - set(found))}")
        records += [(scheme, code, geojson) for code, geojson in found.items()]
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """select scheme, code, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select scheme, code, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[]) as f(scheme, code, geojson)) shapes""",
                [
                    [scheme for scheme, _, _ in records],
                    [code for _, code, _ in records],
                    [geojson for _, _, geojson in records],
                ],
            ).fetchall(),
            schema=["scheme", "code", "geom", "point"],
            orient="row",
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Census Bureau 2024 cartographic boundaries for the South Carolina places and school districts minted for the 2026 local contests",
            polity="US",
        )
        census = cs.source("U.S. Census Bureau", CENSUS)
        if census in cs.minted:
            cs.add(
                "source",
                id=census,
                kind="official_body",
                publisher="U.S. Census Bureau",
                title=CENSUS,
                url="https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
                license=None,
            )
        for url, _ in LAYERS.values():
            cs.artifact(raw.cached(SOURCE, url), census)
        for scheme, code, geom, point in shapes.iter_rows():
            cs.add(
                "area_geometry",
                area_id=cs.area(scheme, code),
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
        print({"shapes": shapes.height})
        return cs.write()
