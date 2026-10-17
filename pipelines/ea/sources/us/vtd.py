import io
import json
import re
import time
import zipfile
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.vtd"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Census Bureau"
YEARS = {
    2000: ("https://www2.census.gov/geo/tiger/TIGER2010/VTD/2000/", r"tl_2010_\d{5}_vtd00\.zip", "VTDIDFP00"),
    2010: ("https://www2.census.gov/geo/tiger/TIGER2012/VTD/", r"tl_2012_\d{2}_vtd10\.zip", "GEOID10"),
    2020: ("https://www2.census.gov/geo/tiger/TIGER2020PL/LAYER/VTD/2020/", r"tl_2020_\d{2}_vtd20\.zip", "GEOID20"),
}
TOLERANCE = 0.00005


def files(year: int) -> list[str]:
    listing, pattern, _ = YEARS[year]
    return [listing + file for file in sorted(set(re.findall(pattern, raw.cached(SOURCE, listing).path.read_text())))]


def fetch() -> None:
    for year, (listing, _, _) in YEARS.items():
        polite(listing, ".html")
        for url in files(year):
            polite(url, ".zip")


def polite(url: str, suffix: str) -> None:
    for attempt in range(8):
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
            return
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 or attempt == 7:
                raise
            time.sleep(30 * 2**attempt)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, (_, _, key) in YEARS.items():
        suffix = str(year)[2:]
        frames = []
        with connect() as conn:
            conn.execute("create temp table vtd (geoid text, name text, vtdst text, geojson text)")
            batches: dict[str, list[str]] = {}
            for url in files(year):
                batches.setdefault(url.rsplit("_", 2)[1][:2], []).append(url)
            for state, urls in batches.items():
                count = 0
                with conn.cursor().copy("copy vtd (geoid, name, vtdst, geojson) from stdin") as copy:
                    for url in urls:
                        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                            parts = {
                                name.rsplit(".", 1)[1].lower(): archive.read(name)
                                for name in archive.namelist()
                                if name.rsplit(".", 1)[-1].lower() in ("shp", "shx", "dbf")
                            }
                        reader = shapefile.Reader(
                            shp=io.BytesIO(parts["shp"]),
                            shx=io.BytesIO(parts["shx"]),
                            dbf=io.BytesIO(parts["dbf"]),
                            encoding="latin-1",
                        )
                        fields = [field[0].upper() for field in reader.fields[1:]]
                        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                            if record is None or shape is None:
                                continue
                            row = dict(zip(fields, list(record), strict=True))
                            vtdst = str(row[f"VTDST{suffix}"]).strip()
                            if shape.shapeType == shapefile.NULL or not shape.points or "ZZZ" in vtdst:
                                continue
                            copy.write_row(
                                (
                                    str(row[key]).strip(),
                                    str(row[f"NAME{suffix}"] or "").strip(),
                                    vtdst,
                                    json.dumps(shape.__geo_interface__),
                                )
                            )
                            count += 1
                frames.append(
                    pl.DataFrame(
                        conn.execute(
                            """with source as (
                                 select geoid, min(name) as name, min(vtdst) as vtdst,
                                        st_union(st_transform(st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)) as geom
                                 from vtd group by geoid),
                               simplified as (
                                 select geoid, name, vtdst, geom as original,
                                        st_coveragesimplify(geom, %s) over () as geom
                                 from source)
                               select geoid, name, vtdst, encode(st_asewkb(geom), 'hex'),
                                      encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                               from (select geoid, name, vtdst, st_multi(st_collectionextract(st_makevalid(
                                              case when geom is null or st_isempty(geom) then original else geom end), 3)) as geom
                                     from simplified) shapes
                               where not st_isempty(geom)""",
                            [TOLERANCE],
                        ).fetchall(),
                        schema=["geoid", "name", "vtdst", "geom", "point"],
                        orient="row",
                    )
                )
                conn.execute("truncate vtd")
                print(f"{year} {state}: {count} shapes, {frames[-1].height} voting districts", flush=True)
        pl.concat(frames).sort("geoid").write_parquet(PARSED / f"vtd_{year}.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Census voting districts of 2000, 2010, and 2020 from the TIGER/Line Shapefiles",
            polity="US",
        )
        existing = pl.DataFrame(
            conn.execute(
                """select n.id::text, n.entity_id::text, n.text from name n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'official' and n.lang = 'en'""",
                [SOURCE],
            ).fetchall(),
            schema={"id": pl.String, "entity_id": pl.String, "old": pl.String},
            orient="row",
        )
        cs.add("scheme", code="us.vtd", polity_id=cs.polity("US"), kind="census", rank=3, noun="voting district")
        counties = {
            code: (area, text.rsplit(",", 1)[0])
            for code, area, text in conn.execute(
                """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
        for year in YEARS:
            title = f"TIGER/Line Shapefiles, {year} Census Voting Districts"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=YEARS[year][0],
                license="Public domain",
            )
            cs.artifact(raw.cached(SOURCE, YEARS[year][0]), source)
            for url in files(year):
                cs.artifact(raw.cached(SOURCE, url), source)
            start, end = date(year, 4, 1), date(year + 10, 4, 1)
            cs.add("boundary_set", scheme_code="us.vtd", vintage=str(year), valid_from=start, valid_to=end, as_of=start)
            frame = pl.read_parquet(PARSED / f"vtd_{year}.parquet").with_columns(
                code=pl.lit(f"{year}:") + pl.col("geoid"), county=pl.col("geoid").str.slice(0, 5)
            )
            missing = sorted(set(frame["county"]) - set(counties))
            if missing:
                print(f"{year}: {len(missing)} counties not on file, placed in their state: {missing}")
            ids = [str(cs.area("us.vtd", code)) for code in frame["code"]]
            parents = [
                str(counties[county][0]) if county in counties else str(cs.area("us.state", county[:2]))
                for county in frame["county"]
            ]
            labels = [
                f"{(re.sub(r'\w+', lambda word: word[0] if re.search(r'\d', word[0]) else word[0].title(), name) if name.isupper() else name) or f'Voting District {vtdst}'}"
                + (f", {counties[county][1]}" if county in counties else "")
                for name, vtdst, county in frame.select("name", "vtdst", "county").iter_rows()
            ]
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.vtd"}))
            cs.frame(
                "area_code",
                pl.DataFrame({"area_id": ids, "code": frame["code"]}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_parent",
                pl.DataFrame({"area_id": ids, "parent_id": parents}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_geometry",
                pl.DataFrame({"area_id": ids, "geom": frame["geom"], "label_point": frame["point"]}).with_columns(
                    valid_from=pl.lit(start),
                    valid_to=pl.lit(end),
                    quality=pl.lit("official"),
                    source_id=pl.lit(str(source)),
                ),
            )
            cs.frame(
                "name",
                pl.DataFrame({"entity_id": ids, "text": labels})
                .join(existing, on="entity_id", how="left")
                .sort(pl.col("old") == pl.col("text"), descending=True, nulls_last=True)
                .with_columns(first=pl.int_range(pl.len()).over("entity_id") == 0)
                .select(
                    "id",
                    "entity_id",
                    text=pl.when("first").then("text").otherwise("old"),
                    is_preferred="first",
                    lang=pl.lit("en"),
                    kind=pl.lit("official"),
                ),
            )
            print(f"{year}: {frame.height} voting districts")
        return cs.write()
