import io
import json
import re
import zipfile
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.local_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URLS = {
    (year, kind): f"https://www2.census.gov/geo/tiger/GENZ{year}/shp/cb_{year}_us_{kind}_500k.zip"
    for year in (2024, 2020)
    for kind in ("place", "unsd", "elsd", "scsd")
}
WORDS = r"\b(?:city|town|village|borough|township|municipality|cdp|county|independent|schools?|district|consolidated|unified|metro|government|of|the|and|unified|elementary|union|high)\b|\d+"


def fold(text: str) -> str:
    return " ".join(re.sub(WORDS, " ", re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("&", " and "))).split())


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        wanted = pl.DataFrame(
            conn.execute(
                """select a.id::text, a.scheme_code, ac.code, n.text from area a
                   join area_code ac on ac.area_id = a.id
                   left join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code in ('us.place', 'us.school_district')
                     and not exists (select 1 from area_geometry g where g.area_id = a.id)"""
            ).fetchall(),
            schema=["area", "scheme", "code", "name"],
            orient="row",
        )
    exact = set(wanted.filter(pl.col("code").str.contains(r"^\d{7}$"))["code"])
    records: dict[str, tuple[str, str, str, str]] = {}
    byname: dict[tuple[str, str, str], set[str]] = {}
    for (year, kind), url in URLS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
        scheme = "us.place" if kind == "place" else "us.school_district"
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            geoid = str(record["GEOID"])
            key = (scheme, geoid)
            if shape is None or key in records:
                continue
            records[key] = (str(record["NAME"]), str(record["STATEFP"]), json.dumps(shape.__geo_interface__), str(year))
            byname.setdefault((scheme, geoid[:2], fold(str(record["NAME"]))), set()).add(geoid)
    rows = []
    for area, scheme, code, name in wanted.iter_rows():
        if re.fullmatch(r"\d{7}", code):
            geoid, how = (code, "geoid") if (scheme, code) in records else (None, "missing")
        else:
            found = byname.get((scheme, code.split(":")[0], fold((name or "").rsplit(",", 1)[0])), set())
            geoid, how = (next(iter(found)), "name") if len(found) == 1 else (None, "ambiguous" if found else "unmatched")
        row = records.get((scheme, geoid)) if geoid else None
        rows.append((area, scheme, code, name, geoid, how, row[2] if row else None, row[3] if row else None))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["area", "scheme", "code", "name", "geoid", "how", "geojson", "vintage"], orient="row")
    frame.write_parquet(PARSED / "matches.parquet")
    print(frame.group_by("scheme", "how").len().sort("scheme", "how"))
    frame.filter(pl.col("geoid").is_null()).drop("geojson").write_csv(PARSED / "unmatched.tsv", separator="\t")
    print(exact.__len__(), "geoid-coded areas wanted")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet").filter(pl.col("geoid").is_not_null())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Census cartographic boundaries for places and school districts the archive holds without a shape",
            polity="US",
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for url in URLS.values():
            cs.artifact(raw.cached(SOURCE, url), census)
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
