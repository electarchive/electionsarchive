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

SOURCE = "us.nyc_dcp"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New York City Department of City Planning"
TITLE = "Election Districts (Clipped to Shoreline), BYTES of the BIG APPLE"
PAGE = "https://www.nyc.gov/content/planning/pages/resources/datasets/election-districts"
LICENSE = "Public record; free download with DCP's disclaimer of warranty"
OLD = "https://www.nyc.gov/assets/planning/download/zip/data-maps/open-data"
NEW = "https://s-media.nyc.gov/agencies/dcp/assets/files/zip/data-tools/bytes/election-districts"
RELEASED = {
    "13a": date(2013, 2, 28),
    "13b": date(2013, 5, 29),
    "13c": date(2013, 9, 24),
    "14c": date(2014, 8, 13),
    "14d": date(2014, 12, 1),
    "15a": date(2015, 2, 11),
    "15b": date(2015, 5, 6),
    "15c": date(2015, 8, 18),
    "15d": date(2015, 11, 4),
    "16a": date(2016, 2, 23),
    "16b": date(2016, 5, 18),
    "16c": date(2016, 8, 11),
    "16d": date(2016, 11, 9),
    "17a": date(2017, 2, 9),
    "17b": date(2017, 6, 2),
    "17c": date(2017, 8, 10),
    "17d": date(2017, 11, 14),
    "18a": date(2018, 2, 7),
    "18b": date(2018, 5, 22),
    "18c": date(2018, 8, 8),
    "18d": date(2018, 11, 16),
    "19a": date(2019, 2, 4),
    "19b": date(2019, 5, 15),
    "19c": date(2019, 8, 26),
    "19d": date(2019, 11, 14),
    "20a": date(2020, 2, 19),
    "20b": date(2020, 5, 13),
    "20c": date(2020, 7, 24),
    "20d": date(2020, 12, 1),
    "21a": date(2021, 2, 19),
    "21b": date(2021, 5, 20),
    "21c": date(2021, 8, 4),
    "21d": date(2021, 11, 9),
    "22a": date(2022, 2, 7),
    "22b": date(2022, 7, 18),
    "22c": date(2022, 11, 4),
    "23a": date(2023, 2, 6),
    "23b": date(2023, 4, 28),
    "23c": date(2023, 7, 20),
    "23d": date(2023, 11, 3),
    "24a": date(2024, 2, 21),
    "24b": date(2024, 4, 26),
    "24c": date(2024, 7, 24),
    "24d": date(2024, 11, 6),
    "25a": date(2025, 2, 19),
    "25b": date(2025, 5, 16),
    "25c": date(2025, 8, 12),
    "25d": date(2025, 11, 12),
}
EFFECTIVE = {**RELEASED, "22b": date(2022, 6, 28), "23d": date(2023, 11, 8)}
ORDER = list(RELEASED)
EDITIONS = {
    edition: (
        f"{OLD if edition <= '22b' else NEW}/nyed_{edition}.zip",
        EFFECTIVE[edition],
        EFFECTIVE[ORDER[index + 1]] if index + 1 < len(ORDER) else None,
    )
    for index, edition in enumerate(ORDER)
}
ERAS = {"21b": ("13a", "22a"), "22b": ("22b", "22c"), "23a": ("23a", "23c"), "25b": ("23d", "25d")}
COUNTIES = ("36005", "36047", "36061", "36081", "36085")
BOROUGHS = {
    "21b": {"50105": "36047", "65094": "36061", "65095": "36061", "77073": "36005"},
    "22b": {},
    "23a": {},
    "25b": {"52093": "36047", "61071": "36061", "77058": "36005"},
}


def fetch() -> None:
    for link, _, _ in EDITIONS.values():
        raw.fetch(SOURCE, link, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (edition text, position int, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (edition, position, code, geojson) from stdin") as copy:
            for position, (edition, (link, _, _)) in enumerate(EDITIONS.items()):
                with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
                    stem = next(name for name in archive.namelist() if name.endswith(".shp"))[:-4]
                    reader = shapefile.Reader(
                        shp=io.BytesIO(archive.read(f"{stem}.shp")),
                        shx=io.BytesIO(archive.read(f"{stem}.shx")),
                        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                    )
                    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                        if record is None or shape is None:
                            raise ValueError(f"{edition}: a null record or shape")
                        rings = [
                            [list(point) for point in shape.points[first:last]]
                            for first, last in zip(shape.parts, [*shape.parts[1:], len(shape.points)], strict=True)
                            if last - first >= 4
                        ]
                        copy.write_row(
                            (
                                edition,
                                position,
                                str(record["ElectDist"]),
                                json.dumps({"type": "MultiLineString", "coordinates": rings}),
                            )
                        )
        rows = conn.execute(
            """with shape as (
                 select edition, position, code,
                        st_transform(st_multi(st_collectionextract(st_makevalid(coalesce(
                          st_buildarea(st_setsrid(st_geomfromgeojson(geojson), 2263)),
                          st_buildarea(st_node(st_setsrid(st_geomfromgeojson(geojson), 2263))))), 3)), 4326) as geom
                 from feature
               ),
               hashed as (
                 select *, md5(st_asbinary(geom)) as hash,
                        position - row_number() over (partition by code, md5(st_asbinary(geom)) order by position) as run
                 from shape
               ),
               runs as (
                 select code, min(edition) as first, max(edition) as last, (array_agg(geom))[1] as geom
                 from hashed where not st_isempty(geom) group by code, hash, run
               )
               select r.first, r.last, r.code, county.code,
                      encode(st_asewkb(r.geom), 'hex'), encode(st_asewkb(st_pointonsurface(r.geom)), 'hex')
               from runs r
               cross join lateral (
                 select c.code from area_geometry g
                 join area_code c on c.area_id = g.area_id
                 join area a on a.id = g.area_id and a.scheme_code = 'us.county'
                 where c.code = any(%s)
                 order by g.geom <-> st_pointonsurface(r.geom) limit 1
               ) county""",
            [list(COUNTIES)],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "first": pl.String,
            "last": pl.String,
            "code": pl.String,
            "county": pl.String,
            "geom": pl.String,
            "point": pl.String,
        },
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
            summary="New York City's election districts from the Department of City Planning, every edition from 2013 on",
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
        vintages = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'us.nyc_ed'").fetchall())
        for edition, (_, effective, replaced) in EDITIONS.items():
            cs.add(
                "boundary_set",
                id=vintages.get(edition, uuid4()),
                scheme_code="us.nyc_ed",
                vintage=edition,
                valid_from=effective,
                valid_to=replaced,
                as_of=effective,
            )
        for era, (first, last) in ERAS.items():
            runs = (
                frame.with_columns(pl.max_horizontal(pl.col("first"), pl.lit(first)).alias("first"))
                .with_columns(pl.min_horizontal(pl.col("last"), pl.lit(last)).alias("last"))
                .filter(pl.col("first") <= pl.col("last"))
                .with_columns(
                    start=pl.col("first").replace_strict({e: v[1] for e, v in EDITIONS.items()}, return_dtype=pl.Date),
                    end=pl.col("last").replace_strict({e: v[2] for e, v in EDITIONS.items()}, return_dtype=pl.Date),
                    county=pl.when(pl.col("code").is_in(list(BOROUGHS[era])))
                    .then(pl.col("code").replace(BOROUGHS[era]))
                    .otherwise(pl.col("county")),
                )
                .sort("code", "start")
            )
            for code, spans in runs.group_by("code", maintain_order=True):
                code = str(code[0])
                area = cs.area("us.nyc_ed", code, EDITIONS[era][1])
                cs.add("area", id=area, scheme_code="us.nyc_ed")
                cs.add(
                    "area_code",
                    area_id=area,
                    code=code,
                    valid_from=spans["start"].min(),
                    valid_to=None if spans["end"].null_count() else spans["end"].max(),
                )
                cs.add(
                    "name",
                    entity_id=area,
                    text=f"AD {code[:2]}, ED {code[2:]}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                parent = None
                for county, run_start, run_end, geom, point in spans.select(
                    "county", "start", "end", "geom", "point"
                ).iter_rows():
                    cs.add(
                        "area_geometry",
                        area_id=area,
                        valid_from=run_start,
                        valid_to=run_end,
                        geom=geom,
                        label_point=point,
                        quality="official",
                        source_id=source,
                    )
                    if parent is not None and parent[0] == county and parent[2] == run_start:
                        parent = (county, parent[1], run_end)
                        continue
                    if parent is not None:
                        cs.add(
                            "area_parent",
                            area_id=area,
                            parent_id=counties[parent[0]],
                            valid_from=parent[1],
                            valid_to=parent[2],
                        )
                    parent = (county, run_start, run_end)
                if parent is not None:
                    cs.add(
                        "area_parent",
                        area_id=area,
                        parent_id=counties[parent[0]],
                        valid_from=parent[1],
                        valid_to=parent[2],
                    )
        held = [
            str(area)
            for (area,) in conn.execute(
                """select a.id from area a join changeset w on w.id = a.changeset_id
                   where a.scheme_code = 'us.nyc_ed' and w.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        ]
        cs.manifest["scope"] = {
            "polity": "US",
            "drops": {
                table: {"areas": held, "from": "2013-01-01"} for table in ("area_code", "area_parent", "area_geometry")
            },
        }
        return cs.write()
