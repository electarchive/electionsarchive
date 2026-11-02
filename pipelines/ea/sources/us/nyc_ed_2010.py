import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect
from ea.typed import present

SOURCE = "us.nyc_ed_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New York City Department of City Planning"
TITLE = "Election Districts, 2010 edition (copy in NYU's Spatial Data Repository)"
PAGE = "https://geodata.lib.utexas.edu/catalog/nyu-2451-34163"
LINK = "https://archive.nyu.edu/retrieve/73162/nyu_2451_34163.zip"
LICENSE = "Public record; free download with DCP's disclaimer of warranty"
FROM = date(2002, 9, 10)
TO = date(2012, 11, 1)
COUNTIES = ("36005", "36047", "36061", "36081", "36085")


def fetch() -> None:
    raw.fetch(SOURCE, LINK, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with (
            conn.cursor() as cursor,
            cursor.copy("copy feature (code, geojson) from stdin") as copy,
            zipfile.ZipFile(raw.cached(SOURCE, LINK).path) as archive,
        ):
            stem = next(name for name in archive.namelist() if name.endswith(".shp"))[:-4]
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(f"{stem}.shp")),
                shx=io.BytesIO(archive.read(f"{stem}.shx")),
                dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            )
            for stored, outline in zip(reader.records(), reader.shapes(), strict=True):
                record = present(stored, "a shapefile record")
                shape = present(outline, f"the shape of election district {record['ELECTDIST']}")
                rings = [
                    [list(point) for point in shape.points[first:last]]
                    for first, last in zip(shape.parts, [*shape.parts[1:], len(shape.points)], strict=True)
                    if last - first >= 4
                ]
                copy.write_row(
                    (str(record["ELECTDIST"]), json.dumps({"type": "MultiLineString", "coordinates": rings}))
                )
        rows = conn.execute(
            """with shape as (
                 select code,
                        st_transform(st_multi(st_collectionextract(st_makevalid(coalesce(
                          st_buildarea(st_setsrid(st_geomfromgeojson(geojson), 2263)),
                          st_buildarea(st_node(st_setsrid(st_geomfromgeojson(geojson), 2263))))), 3)), 4326) as geom
                 from feature
               )
               select r.code, county.code, encode(st_asewkb(r.geom), 'hex'),
                      encode(st_asewkb(st_pointonsurface(r.geom)), 'hex')
               from shape r
               cross join lateral (
                 select c.code from area_geometry g
                 join area_code c on c.area_id = g.area_id
                 join area a on a.id = g.area_id and a.scheme_code = 'us.county'
                 where c.code = any(%s)
                 order by g.geom <-> st_pointonsurface(r.geom) limit 1
               ) county
               where not st_isempty(r.geom)""",
            [list(COUNTIES)],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"code": pl.String, "county": pl.String, "geom": pl.String, "point": pl.String},
        orient="row",
    )
    if frame.filter(pl.col("code").str.len_chars() != 5).height:
        raise ValueError("election district codes are not five-digit AD/ED codes")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "districts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's election districts as the Department of City Planning's 2010 edition drew them, "
            "applied to the elections from the September 2002 primary through 2012",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license=LICENSE,
        )
        cs.artifact(raw.cached(SOURCE, LINK), source)
        counties = {code: cs.area("us.county", code) for code in COUNTIES}
        vintages = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'us.nyc_ed'").fetchall())
        cs.add(
            "boundary_set",
            id=vintages.get("10", uuid4()),
            scheme_code="us.nyc_ed",
            vintage="10",
            valid_from=FROM,
            valid_to=TO,
            as_of=date(2010, 10, 1),
        )
        for code, county, geom, point in frame.sort("code").iter_rows():
            area = cs.area("us.nyc_ed", code, FROM)
            cs.add("area", id=area, scheme_code="us.nyc_ed")
            cs.add("area_code", area_id=area, code=code, valid_from=FROM, valid_to=TO)
            cs.add(
                "name",
                entity_id=area,
                text=f"AD {code[:2]}, ED {code[2:]}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=FROM,
                valid_to=TO,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
            cs.add("area_parent", area_id=area, parent_id=counties[county], valid_from=FROM, valid_to=TO)
        return cs.write()
