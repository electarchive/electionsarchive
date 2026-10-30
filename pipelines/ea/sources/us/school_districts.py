import io
import json
import zipfile
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.school_districts"
VERSION = "1"
URLS = [
    f"https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_{kind}_500k.zip" for kind in ("unsd", "elsd", "scsd")
]
STATES = {"47": "TN", "34": "NJ"}
CENSUS = "Cartographic Boundary Files, 1:500,000 (2024)"


def fetch() -> None:
    for url in URLS:
        raw.fetch(SOURCE, url, suffix=".zip")


def emit() -> Path:
    records: list[tuple[str, str, str]] = []
    for url in URLS:
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
        records += [
            (str(record["GEOID"]), str(record["NAME"]), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and shape is not None and record["STATEFP"] in STATES and (record["STATEFP"] == "34" or "_scsd_" not in url) and not str(record["GEOID"]).endswith(("99997", "99999"))
        ]
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)) shapes""",
                [[code for code, _, _ in records], [name for _, name, _ in records], [geojson for _, _, geojson in records]],
            ).fetchall(),
            schema=["code", "name", "geom", "point"],
            orient="row",
        )
        taken = {
            path
            for (path,) in conn.execute("select path from slug where path like 'us/tn/%%' or path like 'us/nj/%%'").fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="School districts of Tennessee and New Jersey missing a polity, with the Census Bureau's 2024 cartographic boundaries",
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
        for url in URLS:
            cs.artifact(raw.cached(SOURCE, url), census)
        counts = 0
        for code, text, geom, point in shapes.iter_rows():
            abbreviation = STATES[code[:2]]
            polity = cs.key("us.polity", f"school:{code}")
            area = cs.area("us.school_district", code)
            if polity not in cs.minted:
                continue
            path = f"us/{abbreviation.lower()}/{slug(text)}"
            if path in taken:
                path = f"{path}-{code}"
            taken.add(path)
            cs.add("polity", id=polity, kind="school_district", iso_code=None)
            cs.add("name", entity_id=polity, text=text, lang="en", kind="official", is_preferred=True)
            cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{abbreviation}"))
            cs.add("slug", path=path, entity_id=polity)
            cs.add("area", id=area, scheme_code="us.school_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", code[:2]))
            cs.add("name", entity_id=area, text=f"{text}, {abbreviation}", lang="en", kind="official", is_preferred=True)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.school_district", code),
                polity_id=polity,
                scheme_code="us.school_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            counts += 1
        print({"school_districts": counts})
        return cs.write()
