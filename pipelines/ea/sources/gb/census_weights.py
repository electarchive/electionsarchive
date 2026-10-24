import csv
import io
import json
import zipfile
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "gb.census_weights"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ONS = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/OA_December_2021_EW_PWC_V4/FeatureServer/0/query"
    "?where=1%3D1&outFields=OA21CD&outSR=27700&orderByFields=OA21CD"
)
NOMIS = "https://www.nomisweb.co.uk/output/census/2021/census2021-ts001.zip"
NRS = (
    "https://maps.gov.scot/server/rest/services/NRS/Census2022/MapServer/2/query"
    "?where=1%3D1&outFields=code,popcount,easting,northing&returnGeometry=false&orderByFields=objectid"
)
NISRA = "https://www.nisra.gov.uk/system/files/statistics/geography-census-2021-population-weighted-centroids-csv.zip"
NISRA_PEOPLE = "https://build.nisra.gov.uk/en/custom/table.csv?d=PEOPLE&v=DZ21"
COUNTS = {"ons": 188_880, "nrs": 46_363, "nisra": 3_780}
PAIRS = [("gb.pcon", "2010", "2024"), ("gb.spc", "2011", "2026"), ("gb.spr", "2011", "2026")]
OGL = "Open Government Licence v3.0"
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
SOURCES = {
    "ons": (
        "Office for National Statistics",
        "Output Areas (December 2021) EW Population Weighted Centroids (V4)",
        ONS.split("/query")[0],
        (
            "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
            "Contains OS data © Crown copyright and database right"
        ),
    ),
    "nomis": (
        "Office for National Statistics",
        "Census 2021: TS001 Number of usual residents in households and communal establishments",
        NOMIS,
        "Source: Office for National Statistics licensed under the Open Government Licence v.3.0",
    ),
    "nrs": (
        "National Records of Scotland",
        "Census 2022 – Output Area Centroids",
        NRS.split("/query")[0],
        "Copyright National Records of Scotland, contains Ordnance Survey data © Crown copyright and database right",
    ),
    "nisra": (
        "Northern Ireland Statistics and Research Agency",
        "Census 2021 population-weighted centroids for Data Zones and Super Data Zones",
        NISRA,
        "Source: NISRA, licensed under the Open Government Licence v3.0",
    ),
    "nisra_people": (
        "Northern Ireland Statistics and Research Agency",
        "Census 2021 usual residents by Data Zone (Flexible Table Builder)",
        NISRA_PEOPLE,
        "Source: NISRA, licensed under the Open Government Licence v3.0",
    ),
}


def pages() -> dict[str, list[str]]:
    return {
        "ons": [
            f"{ONS}&resultOffset={offset}&resultRecordCount=2000&f=geojson" for offset in range(0, COUNTS["ons"], 2000)
        ],
        "nrs": [
            f"{NRS}&resultOffset={offset}&resultRecordCount=1000&f=json" for offset in range(0, COUNTS["nrs"], 1000)
        ],
    }


def fetch() -> None:
    for urls in pages().values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".json")
    raw.fetch(SOURCE, NOMIS, suffix=".zip")
    raw.fetch(SOURCE, NISRA, suffix=".zip")
    raw.fetch(SOURCE, NISRA_PEOPLE, suffix=".csv")


def parse() -> None:
    urls = pages()
    with zipfile.ZipFile(raw.cached(SOURCE, NOMIS).path) as archive:
        people = {
            row["geography code"]: int(row["Residence type: Total; measures: Value"])
            for row in csv.DictReader(io.TextIOWrapper(archive.open("census2021-ts001-oa.csv"), "utf-8-sig"))
        }
    rows: list[tuple[str, str, float, float, int]] = []
    for url in urls["ons"]:
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
            x, y = feature["geometry"]["coordinates"]
            code = feature["properties"]["OA21CD"]
            rows.append(("ons", code, x, y, people[code]))
    for url in urls["nrs"]:
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
            attributes = feature["attributes"]
            rows.append(
                (
                    "nrs",
                    attributes["code"],
                    float(attributes["easting"]),
                    float(attributes["northing"]),
                    attributes["popcount"],
                )
            )
    zones = {
        row["Census 2021 Data Zone Code"]: int(row["Count"])
        for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, NISRA_PEOPLE).path.read_text(encoding="utf-8-sig")))
    }
    with zipfile.ZipFile(raw.cached(SOURCE, NISRA).path) as archive:
        for row in csv.DictReader(
            io.TextIOWrapper(archive.open("census-2021-population-weighted-centroids-data-zone.csv"), "utf-8-sig")
        ):
            rows.append(("nisra", row["DZ2021_code"], float(row["X"]), float(row["Y"]), zones[row["DZ2021_code"]]))
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "x": pl.Float64, "y": pl.Float64, "people": pl.Int64},
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().rows())
    for layer, count in COUNTS.items():
        if counts.get(layer) != count:
            raise ValueError(f"{layer}: {counts.get(layer)} points, expected {count}")
    print(frame.group_by("layer").agg(pl.len(), pl.col("people").sum()).sort("layer").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "points.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "points.parquet")
    urls = pages()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Population-weighted crosswalks between the 2010 and 2024 UK Parliament constituencies and between "
            "the 2011 and 2026 Scottish Parliament constituencies and regions, from census small-area populations",
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for key, (publisher, title, url, attribution) in SOURCES.items():
            sources[key] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=OGL,
                license_url=OGL_URL,
                attribution=attribution,
            )
        for key in ("ons", "nrs"):
            for url in urls[key]:
                cs.artifact(raw.cached(SOURCE, url), sources[key])
        for key, url in (("nomis", NOMIS), ("nisra", NISRA), ("nisra_people", NISRA_PEOPLE)):
            cs.artifact(raw.cached(SOURCE, url), sources[key])
        conn.execute("create temp table point (layer text, code text, x float8, y float8, people int)")
        with conn.cursor() as cursor, cursor.copy("copy point from stdin") as copy:
            for row in frame.iter_rows():
                copy.write_row(row)
        conn.execute("alter table point add column geom geometry")
        conn.execute(
            "update point set geom = st_transform(st_setsrid(st_makepoint(x, y), case layer when 'nisra' then 29902 else 27700 end), 27700)"
        )
        conn.execute("create temp table side (scheme text, plan text, k int, geom geometry)")
        sets: dict[tuple[str, str], UUID] = {}
        for scheme, *vintages in PAIRS:
            for vintage in vintages:
                id, valid = conn.execute(
                    "select id, valid from boundary_set where scheme_code = %s and vintage = %s", [scheme, vintage]
                ).fetchone()
                sets[(scheme, vintage)] = id
                conn.execute(
                    """insert into side
                       select %(scheme)s, %(vintage)s, a.k, st_makevalid(st_transform(g.geom, 27700))
                       from area a join area_geometry g on g.area_id = a.id
                       where a.scheme_code = %(scheme)s and g.quality <> 'synthetic'
                         and g.valid @> lower(%(valid)s::daterange)""",
                    {"scheme": scheme, "vintage": vintage, "valid": valid},
                )
        conn.execute("create index on side using gist (geom)")
        conn.execute("analyze side")
        for scheme, earlier, later in PAIRS:
            placed = pl.DataFrame(
                conn.execute(
                    """select p.people, f.k, t.k
                       from point p
                       cross join lateral (select k from side s where s.scheme = %(scheme)s and s.plan = %(earlier)s
                                           order by s.geom <-> p.geom limit 1) f
                       cross join lateral (select k from side s where s.scheme = %(scheme)s and s.plan = %(later)s
                                           order by s.geom <-> p.geom limit 1) t
                       where p.layer = 'nrs' or %(scheme)s = 'gb.pcon'""",
                    {"scheme": scheme, "earlier": earlier, "later": later},
                ).fetchall(),
                schema={"people": pl.Int64, "earlier": pl.Int32, "later": pl.Int32},
                orient="row",
            )
            for source, target in (("earlier", "later"), ("later", "earlier")):
                weights = (
                    placed.group_by(source, target)
                    .agg(pl.col("people").sum())
                    .with_columns(weight=pl.col("people") / pl.col("people").sum().over(source))
                    .filter(pl.col("weight") >= 0.0005)
                )
                plans = {"earlier": earlier, "later": later}
                crosswalk = uuid5(
                    NAMESPACE,
                    f"{SOURCE}\x1f{sets[(scheme, plans[source])]}\x1f{sets[(scheme, plans[target])]}\x1f{VERSION}",
                )
                cs.add(
                    "crosswalk",
                    id=crosswalk,
                    from_kind="boundary_set",
                    from_boundary_set_id=sets[(scheme, plans[source])],
                    to_boundary_set_id=sets[(scheme, plans[target])],
                    method="population",
                )
                cs.frame(
                    "crosswalk_weight",
                    weights.select(
                        crosswalk_id=pl.lit(str(crosswalk)),
                        from_k=source,
                        to_k=target,
                        weight=pl.col("weight").round(6).cast(pl.Decimal(10, 6)),
                        exact=pl.lit(False),
                    ),
                )
                print(
                    f"{scheme} {plans[source]} -> {plans[target]}: {weights.height} weights, "
                    f"{weights[source].n_unique()} units, {placed['people'].sum()} people"
                )
        return cs.write()
