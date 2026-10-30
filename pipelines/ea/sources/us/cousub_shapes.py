import io
import json
import re
import zipfile
from pathlib import Path

import polars as pl
import shapefile
from shapely.geometry import shape

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.cousub_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATES = {"51": "board-of-supervisors"}
URL = "https://www2.census.gov/geo/tiger/GENZ{year}/shp/cb_{year}_{state}_cousub_500k.zip"
YEARS = (2024, 2020)


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\bdistrict\b", "", text.lower()))


def fetch() -> None:
    for state in STATES:
        for year in YEARS:
            raw.fetch(SOURCE, URL.format(year=year, state=state), suffix=".zip")


def parse() -> None:
    with connect() as conn:
        wanted = conn.execute(
            """select a.id::text, ac.code, n.text from area a
               join area_code ac on ac.area_id = a.id
               left join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'en'
               where a.scheme_code = 'us.local_district' and ac.code ~ '^51:51[0-9]{3}:board-of-supervisors:'
                 and not exists (select 1 from area_geometry g where g.area_id = a.id)"""
        ).fetchall()
        counties = dict(
            conn.execute(
                """select u.code, st_area(g.geom::geography) from reporting_unit u
                   join area_geometry g on g.area_id = u.area_id
                   where u.scheme_code = 'us.county' and u.code like '51%%'"""
            ).fetchall()
        )
    bodies: dict[str, list[tuple[str, str]]] = {}
    for area, code, _ in wanted:
        _, fips, _, slug = code.split(":", 3)
        bodies.setdefault(fips, []).append((area, slug))
    rows = []
    for year in YEARS:
        with zipfile.ZipFile(raw.cached(SOURCE, URL.format(year=year, state="51")).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        subs: dict[str, dict[str, tuple[str, dict[str, object]]]] = {}
        for record, geometry in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            subs.setdefault(str(record["STATEFP"]) + str(record["COUNTYFP"]), {})[fold(str(record["NAMELSAD"]))] = (
                str(record["GEOID"]),
                geometry.__geo_interface__,
            )
        for fips, members in bodies.items():
            if any(r[0] == fips for r in rows):
                continue
            found = [(area, subs.get(fips, {}).get(fold(slug.replace("-", " ")))) for area, slug in members]
            if any(match is None for _, match in found):
                continue
            share = sum(shape(match[1]).area for _, match in found if match is not None)
            full = sum(shape(match[1]).area for match in subs[fips].values())
            if abs(share - full) > 0.02 * full:
                continue
            for area, match in found:
                rows.append((fips, area, match[0], year, json.dumps(match[1])))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema=["county", "area", "geoid", "vintage", "geojson"],
        orient="row",
    ).write_parquet(PARSED / "matches.parquet")
    print(len(wanted), "wanted;", len(rows), "matched in", len({r[0] for r in rows}), "counties")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Census county subdivision boundaries for board of supervisors districts the archive holds without a shape",
            polity="US",
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for state in STATES:
            for year in YEARS:
                cs.artifact(raw.cached(SOURCE, URL.format(year=year, state=state)), census)
        shapes = conn.execute(
            """select f.area, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(st_transform(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)))), 'hex')
               from unnest(%s::text[], %s::text[]) as f(area, geojson)""",
            [frame["area"].to_list(), frame["geojson"].to_list()],
        ).fetchall()
        for area, geom, point in shapes:
            cs.add("area_geometry", area_id=area, valid_from=None, geom=geom, label_point=point, quality="official", source_id=census)
        return cs.write()
