import io
import json
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nyc_dcp"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New York City Department of City Planning"
TITLE = "Election Districts (Clipped to Shoreline), BYTES of the BIG APPLE"
PAGE = "https://www.nyc.gov/content/planning/pages/resources/datasets/election-districts"
LICENSE = "Public record; free download with DCP's disclaimer of warranty"
OLD = "https://www.nyc.gov/assets/planning/download/zip/data-maps/open-data"
NEW = "https://s-media.nyc.gov/agencies/dcp/assets/files/zip/data-tools/bytes/election-districts"
EDITIONS = {
    "21b": (f"{OLD}/nyed_21b.zip", date(2021, 5, 20), date(2021, 8, 4)),
    "21c": (f"{OLD}/nyed_21c.zip", date(2021, 8, 4), date(2021, 11, 9)),
    "25b": (f"{NEW}/nyed_25b.zip", date(2025, 5, 16), date(2025, 8, 12)),
    "25c": (f"{NEW}/nyed_25c.zip", date(2025, 8, 12), date(2025, 11, 12)),
}
ERAS = {"21b": "21b", "21c": "21b", "25b": "25b", "25c": "25b"}
COUNTIES = ("36005", "36047", "36061", "36081", "36085")
BOROUGHS = {
    "21b": {"50105": "36047", "65094": "36061", "65095": "36061", "77073": "36005"},
    "25b": {"52093": "36047", "61071": "36061", "77058": "36005"},
}


def fetch() -> None:
    for link, _, _ in EDITIONS.values():
        raw.fetch(SOURCE, link, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (edition text, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (edition, code, geojson) from stdin") as copy:
            for edition, (link, _, _) in EDITIONS.items():
                with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
                    stem = f"nyed_{edition}/nyed"
                    reader = shapefile.Reader(
                        shp=io.BytesIO(archive.read(f"{stem}.shp")),
                        shx=io.BytesIO(archive.read(f"{stem}.shx")),
                        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                    )
                    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                        copy.write_row((edition, str(record["ElectDist"]), json.dumps(shape.__geo_interface__)))
        rows = conn.execute(
            """with shape as (
                 select edition, code,
                        st_transform(st_multi(st_collectionextract(st_makevalid(st_force2d(
                          st_setsrid(st_geomfromgeojson(geojson), 2263))), 3)), 4326) as geom
                 from feature
               )
               select s.edition, s.code, county.code,
                      encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
               from shape s
               cross join lateral (
                 select c.code from area_geometry g
                 join area_code c on c.area_id = g.area_id
                 join area a on a.id = g.area_id and a.scheme_code = 'us.county'
                 where c.code = any(%s)
                 order by g.geom <-> st_pointonsurface(s.geom) limit 1
               ) county""",
            [list(COUNTIES)],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"edition": pl.String, "code": pl.String, "county": pl.String, "geom": pl.String, "point": pl.String},
        orient="row",
    )
    if (
        frame.filter(pl.col("code").str.len_chars() != 5).height
        or frame.select("edition", "code").is_duplicated().any()
    ):
        raise ValueError("election district codes are not unique five-digit AD/ED codes per edition")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "districts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's election districts from the Department of City Planning, 2021 and 2025 editions",
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
        for link, _, _ in EDITIONS.values():
            cs.artifact(raw.cached(SOURCE, link), source)
        cs.add(
            "scheme",
            code="us.nyc_ed",
            polity_id=cs.key("us.polity", "new-york-city"),
            kind="precinct",
            rank=1,
            noun="election district",
        )
        counties = {code: cs.area("us.county", code) for code in COUNTIES}
        for edition, (_, start, end) in EDITIONS.items():
            cs.add(
                "boundary_set",
                scheme_code="us.nyc_ed",
                vintage=edition,
                valid_from=start,
                valid_to=end,
                as_of=start,
            )
        for era in sorted(set(ERAS.values())):
            editions = [edition for edition, owner in ERAS.items() if owner == era]
            start, end = EDITIONS[editions[0]][1], EDITIONS[editions[-1]][2]
            districts = frame.filter(pl.col("edition").is_in(editions))
            for code, county in districts.group_by("code").agg(pl.col("county").last()).sort("code").iter_rows():
                county = BOROUGHS[era].get(code, county)
                area = cs.area("us.nyc_ed", code, start)
                cs.add("area", id=area, scheme_code="us.nyc_ed")
                cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                cs.add("area_parent", area_id=area, parent_id=counties[county], valid_from=start, valid_to=end)
                cs.add(
                    "name",
                    entity_id=area,
                    text=f"AD {code[:2]}, ED {code[2:]}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
            for edition, code, geom, point in districts.select("edition", "code", "geom", "point").iter_rows():
                cs.add(
                    "area_geometry",
                    area_id=cs.area("us.nyc_ed", code, start),
                    valid_from=EDITIONS[edition][1],
                    valid_to=EDITIONS[edition][2],
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=source,
                )
        return cs.write()
