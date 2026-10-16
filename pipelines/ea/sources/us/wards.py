import io
import json
import re
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import polars as pl
import shapefile
from pyproj import CRS, Transformer

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.wards"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCGIS = "query?where=1%3D1&outFields=*&outSR=4326&f=geojson&resultOffset={offset}&resultRecordCount={count}"
LAST = 2026
FREE = "No license stated; free download from the city's open data portal"
PUBLISHERS = {
    "chicago": (
        "City of Chicago",
        "City of Chicago Data Portal Terms of Use",
        "https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html",
    ),
    "nyc": (
        "New York City Department of City Planning",
        "Public record; free download with DCP's disclaimer of warranty",
        None,
    ),
}
NYC = "https://www.nyc.gov/assets/planning/download/zip/data-maps/open-data"
NYC_NEW = "https://s-media.nyc.gov/agencies/dcp/assets/files/zip/data-tools/bytes/city-council"
PLANS: dict[str, list[tuple[int, int | None, str, str, str, str]]] = {
    "1714000": [
        (2003, 2015, "socrata:data.cityofchicago.org/72b6-8fe8", "ward", "chicago", "Boundaries - Wards (2003-2015)"),
        (2015, 2023, "socrata:data.cityofchicago.org/k9yb-bpqx", "ward", "chicago", "Boundaries - Wards (2015-2023)"),
        (2023, None, "socrata:data.cityofchicago.org/p293-wvbd", "ward", "chicago", "Boundaries - Wards (2023-)"),
    ],
    "3651000": [
        (
            2003,
            2013,
            f"{NYC}/nycc_12c.zip",
            "CounDist",
            "nyc",
            "City Council Districts (Clipped to Shoreline), edition 12C",
        ),
        (
            2013,
            2023,
            f"{NYC}/nycc_21c.zip",
            "CounDist",
            "nyc",
            "City Council Districts (Clipped to Shoreline), edition 21C",
        ),
        (
            2023,
            None,
            f"{NYC_NEW}/nycc_25c.zip",
            "CounDist",
            "nyc",
            "City Council Districts (Clipped to Shoreline), edition 25C",
        ),
    ],
}
AREAS: set[str] = set()
PREFIXES = ("council district", "district", "ward", "cd", "area")


def token(label: str) -> str:
    text = re.sub(r"\.0+$", "", str(label).strip().lower())
    for prefix in PREFIXES:
        text = text.removeprefix(prefix).strip(" -_#")
    text = re.sub(r"^(\d+)(st|nd|rd|th)\b", r"\1", text).strip(" -_")
    return text.lstrip("0") or text


def layer(url: str) -> str:
    return url.removeprefix("arcgis:")


def pages(url: str) -> Iterator[str]:
    base = layer(url)
    count = json.loads(raw.cached(SOURCE, base + "?f=json").path.read_text())["maxRecordCount"]
    offset = 0
    while True:
        link = base + ARCGIS.format(offset=offset, count=count)
        yield link
        page = json.loads(raw.cached(SOURCE, link).path.read_text())
        if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
            return
        offset += count


def socrata(url: str) -> str:
    domain, id = url.removeprefix("socrata:").split("/")
    return f"https://{domain}/resource/{id}.geojson?$limit=50000"


def links(url: str) -> list[str]:
    if url.startswith("arcgis:"):
        return [layer(url) + "?f=json", *pages(url)]
    return [socrata(url) if url.startswith("socrata:") else url]


def fetch() -> None:
    for plans in PLANS.values():
        for _, _, url, _, _, _ in plans:
            if url.startswith("arcgis:"):
                base = layer(url)
                count = json.loads(raw.fetch(SOURCE, base + "?f=json", suffix=".json").path.read_text())[
                    "maxRecordCount"
                ]
                offset = 0
                while True:
                    page = json.loads(
                        raw.fetch(
                            SOURCE, base + ARCGIS.format(offset=offset, count=count), suffix=".geojson"
                        ).path.read_text()
                    )
                    if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
                        break
                    offset += count
            else:
                raw.fetch(
                    SOURCE,
                    socrata(url) if url.startswith("socrata:") else url,
                    suffix=".zip" if url.endswith(".zip") else ".geojson",
                )


def features(url: str) -> Iterator[tuple[dict[str, object], str]]:
    if not url.endswith(".zip"):
        for link in links(url):
            if link.endswith("?f=json"):
                continue
            for feature in json.loads(raw.cached(SOURCE, link).path.read_text())["features"]:
                if feature["geometry"] is not None:
                    yield feature["properties"], json.dumps(feature["geometry"])
        return
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        names = {name.lower(): name for name in archive.namelist() if not name.startswith("__MACOSX")}
        for stem in sorted(name[:-4] for name in names if name.endswith(".shp")):
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(names[f"{stem}.shp"])),
                shx=io.BytesIO(archive.read(names[f"{stem}.shx"])),
                dbf=io.BytesIO(archive.read(names[f"{stem}.dbf"])),
                encoding="latin-1",
            )
            project = Transformer.from_crs(
                CRS(archive.read(names[f"{stem}.prj"]).decode("latin-1")), 4326, always_xy=True
            ).transform
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if shape.shapeType == shapefile.NULL or not shape.points:
                    continue
                geometry = shape.__geo_interface__
                rings = (
                    geometry["coordinates"]
                    if geometry["type"] == "Polygon"
                    else [ring for polygon in geometry["coordinates"] for ring in polygon]
                )
                for ring in rings:
                    ring[:] = [project(*point[:2]) for point in ring]
                yield record.as_dict(), json.dumps(geometry)


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (geoid text, plan int, label text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (geoid, plan, label, geojson) from stdin") as copy:
            for geoid, plans in PLANS.items():
                for start, _, url, field, _, _ in plans:
                    for properties, geometry in features(url):
                        label = properties.get(field)
                        if label is not None and token(str(label)):
                            copy.write_row((geoid, start, token(str(label)), geometry))
        rows = conn.execute(
            """with shape as (
                 select geoid, plan, label,
                        st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(st_force2d(
                          st_setsrid(st_geomfromgeojson(geojson), 4326))))), 3)) as geom
                 from feature group by geoid, plan, label
               )
               select geoid, plan, label, encode(st_asewkb(geom), 'hex'),
                      encode(st_asewkb(st_pointonsurface(geom)), 'hex'), st_area(geom::geography) / 1e6
               from shape where not st_isempty(geom)"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "geoid": pl.String,
            "plan": pl.Int32,
            "label": pl.String,
            "geom": pl.String,
            "point": pl.String,
            "km2": pl.Float64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "districts.parquet")
    print(frame.group_by("geoid", "plan").agg(pl.len(), pl.col("label").sort().str.join(" ")).sort("geoid", "plan"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        areas: dict[tuple[str, str], list[str]] = {}
        for code, id in conn.execute(
            """select c.code, a.id from area a join area_code c on c.area_id = a.id
               where a.scheme_code = 'us.local_district' and split_part(c.code, ':', 2) = any(%s)
                 and split_part(c.code, ':', 3) not in ('mayor', 'board-of-education')""",
            [list(PLANS)],
        ).fetchall():
            geoid, label = code.split(":")[1], code.split(":")[3]
            if label.startswith("area-") and geoid not in AREAS:
                continue
            if not re.fullmatch(r"(district|ward|area)-[0-9a-z]+|[0-9]+(st|nd|rd|th)-ward", label):
                continue
            areas.setdefault((geoid, token(label.replace("-", " "))), []).append(id)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="City council districts and wards, current and past plans, from city open data portals",
            polity="US",
        )
        sources = {}
        for geoid, plans in PLANS.items():
            for start, _, url, _, publisher, title in plans:
                name, license, license_url = PUBLISHERS[publisher]
                source = cs.source(name, title)
                if source not in sources.values():
                    cs.add(
                        "source",
                        id=source,
                        kind="official_body",
                        publisher=name,
                        title=title,
                        url=socrata(url) if url.startswith("socrata:") else layer(url),
                        license=license,
                        license_url=license_url,
                    )
                    for link in links(url):
                        cs.artifact(raw.cached(SOURCE, link), source)
                sources[(geoid, start)] = source
        years = [start for plans in PLANS.values() for start, *_ in plans]
        for year in range(min(years), LAST + 1):
            cs.add(
                "boundary_set",
                scheme_code="us.local_district",
                vintage=str(year),
                valid_from=date(year, 1, 1),
                valid_to=date(year + 1, 1, 1),
                as_of=date(year, 1, 1),
            )
        shaped = 0
        for geoid, plan, label, geom, point in frame.select("geoid", "plan", "label", "geom", "point").iter_rows():
            end = next(end for start, end, *_ in PLANS[geoid] if start == plan)
            for area in areas.get((geoid, label), []):
                shaped += 1
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=date(plan, 1, 1),
                    valid_from_precision="year",
                    valid_to=None if end is None else date(end, 1, 1),
                    valid_to_precision="year",
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[(geoid, plan)],
                )
        print(f"{shaped} geometry versions on {len({id for ids in areas.values() for id in ids})} candidate areas")
        return cs.write()
