import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.il_judicial_subcircuit_shapes"
VERSION = "1"
URL = "https://www.ilsenateredistricting.com/images/Data/Enacted_Judicial_Sub_Circiuts.zip"
PAGE = "https://www.ilsenateredistricting.com/enacted-legislative-map/enacted-maps/36-proposed-judicial-subcircuits-maps-jan-3-2022"
LOCAL = DATA / "tmp" / "u1858" / "subcircuits.zip"
LAYERS = {
    "3rd Judicial Subcircuit": "IL:circuit-3-subcircuit-{}",
    "7th Judicial Subcircuits": "IL:circuit-7-subcircuit-{}",
    "12th Judicial Subcircuit": "IL:circuit-12-subcircuit-{}",
    "16th Judicial Subcircuit": "IL:circuit-16-subcircuit-{}",
    "17th Judicial Subcircuit": "IL:circuit-17-subcircuit-{}",
    "18th Judicial Subcircuit": "IL:circuit-18-subcircuit-{}",
    "19th Judicial Subcircuit": "IL:circuit-19-subcircuit-{}",
    "22nd Judicial Subcircuit": "IL:circuit-22-subcircuit-{}",
    "Cook County Sub Circuits": "IL:circuit-cook-subcircuit-{}",
}


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        LOCAL.read_bytes(),
        suffix=".zip",
        retrieved_at=datetime.now(UTC),
        media_type="application/zip",
    )


def emit() -> Path:
    with connect() as conn, zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        areas = dict(
            conn.execute(
                """select ac.code, a.id::text from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code = 'us.judicial_district' and ac.code like 'IL:%%'
                     and not exists (select 1 from area_geometry g where g.area_id = a.id)"""
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois judicial subcircuits as enacted in January 2022, from the shapefiles the General Assembly's redistricting committees published",
            polity="US",
        )
        publisher = "Illinois Senate Democratic Caucus redistricting committee"
        source = cs.source(publisher, "Enacted judicial subcircuit maps, January 24, 2022")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title="Enacted judicial subcircuit maps, January 24, 2022",
            url=PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for layer, pattern in LAYERS.items():
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(f"{layer}.shp")),
                shx=io.BytesIO(archive.read(f"{layer}.shx")),
                dbf=io.BytesIO(archive.read(f"{layer}.dbf")),
            )
            for item in reader.iterShapeRecords():
                record, shape = item.record, item.shape
                if record is None or shape is None:
                    raise ValueError(f"{layer}: a shape record lacks its shape or attributes")
                code = pattern.format(record["DISTRICTN"])
                if code not in areas:
                    continue
                row = conn.execute(
                    """with g as (select st_makevalid(st_setsrid(st_geomfromgeojson(%s), 4326)) as geom)
                       select encode(st_asewkb(st_multi(st_collectionextract(geom, 3))), 'hex'),
                              encode(st_asewkb(st_pointonsurface(geom)), 'hex') from g""",
                    [json.dumps(shape.__geo_interface__)],
                ).fetchone()
                if row is None:
                    raise ValueError(f"{code}: PostGIS returned no geometry")
                geom, point = row
                cs.add(
                    "area_geometry",
                    area_id=areas[code],
                    valid_from=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=source,
                )
        return cs.write()
