import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ie.dail import ELECTIONS
from ea.store import DATA, connect

SOURCE = "ie.dail_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOWNLOAD = "https://data-osi.opendata.arcgis.com/api/download/v1/items/{item}/geojson?layers=0"
SETS = {
    2013: (
        "ca26d0becaa249a69e7cc05918700802",
        "Constituency Boundaries Generalised 20m - National Electoral Boundaries - 2013",
        "https://data-osi.opendata.arcgis.com/datasets/osi::constituency-boundaries-generalised-20m-national-electoral-boundaries-2013",
    ),
    2017: (
        "cd64ef76ef3b4c04bdce1f556bc43892",
        "Constituency Boundaries - Generalised 20m - National Electoral Boundaries - 2017",
        "https://data-osi.opendata.arcgis.com/datasets/osi::constituency-boundaries-generalised-20m-national-electoral-boundaries-2017",
    ),
    2023: (
        "a37ad6a3a6ff47e4a5a0ff313b418448",
        "Constituency Boundaries Ungeneralised - National Electoral Boundaries - 2023",
        "https://data-osi.opendata.arcgis.com/datasets/osi::constituency-boundaries-ungeneralised-national-electoral-boundaries-2023",
    ),
}
FIELDS = ("MAX_CON_NA", "CON_SEAT_", "ENG_NAME_VALUE")
RENAMES = {(2013, "Limerick"): "Limerick County"}
TOLERANCE = 20


def fetch() -> None:
    for item, *_ in SETS.values():
        raw.fetch(SOURCE, DOWNLOAD.format(item=item), suffix=".geojson")


def parse() -> None:
    rows: list[tuple[int, str, str, str]] = []
    for year, (item, _, _) in SETS.items():
        features = json.loads(raw.cached(SOURCE, DOWNLOAD.format(item=item)).path.read_bytes())["features"]
        with connect() as conn:
            conn.execute("create temp table feature (name text, geojson text) on commit drop")
            with conn.cursor() as cursor, cursor.copy("copy feature (name, geojson) from stdin") as copy:
                for feature in features:
                    raw_name = next(feature["properties"][f] for f in FIELDS if f in feature["properties"])
                    name = re.sub(r"\s*\(\d\)\s*$", "", raw_name.strip())
                    copy.write_row((RENAMES.get((year, name), name), json.dumps(feature["geometry"])))
            found = conn.execute(
                """with seat as (
                     select name, st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(
                            st_force2d(st_setsrid(st_geomfromgeojson(geojson), 2157))))), 3)) as geom
                     from feature group by name
                   ),
                   simple as (
                     select name, st_setsrid(st_coveragesimplify(geom, %s) over (), 2157) as geom from seat
                   )
                   select name, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                          encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
                   from simple""",
                [TOLERANCE],
            ).fetchall()
        rows.extend((year, *row) for row in found)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["year", "name", "geom", "label_point"], orient="row").write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    firsts: dict[int, tuple[date, date]] = {}
    for day, act, signed, _ in ELECTIONS.values():
        firsts[act] = (signed, min(day, firsts[act][1]) if act in firsts else day)
    acts = sorted(((act, signed, day) for act, (signed, day) in firsts.items()), key=lambda a: a[1])
    ends = {act: following[1] for (act, _, _), following in zip(acts, acts[1:], strict=False)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dáil constituency boundaries under the Electoral (Amendment) Acts of 2013, 2017, and 2023, from Tailte Éireann",
            polity="IE",
        )
        for act, signed, day in acts:
            if act not in SETS:
                continue
            _, title, url = SETS[act]
            source = cs.source("Tailte Éireann", title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Tailte Éireann",
                title=title,
                url=url,
                license="CC BY 4.0",
                license_url="https://creativecommons.org/licenses/by/4.0/",
                attribution="© Tailte Éireann, licensed under CC BY 4.0",
            )
            cs.artifact(raw.cached(SOURCE, DOWNLOAD.format(item=SETS[act][0])), source)
            for name, geom, point in frame.filter(pl.col("year") == act).sort("name").select("name", "geom", "label_point").iter_rows():
                area = cs.area("ie.dail", f"{act}:{slug(name)}")
                if area in cs.minted:
                    raise ValueError(f"no {act} constituency {name} in ie.dail; publish ie.dail first")
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=signed,
                    valid_to=ends.get(act),
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=source,
                )
            cs.add("boundary_set", scheme_code="ie.dail", vintage=str(act), valid_from=signed, valid_to=ends.get(act), as_of=day)
        return cs.write()
