import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ie.tailte"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
COUNTIES = (
    "https://data-osi.opendata.arcgis.com/api/download/v1/items/dc24df2a5ce84ee9a38d9afe8431ee9b/geojson?layers=1"
)
CONNACHT_ULSTER = ("CAVAN", "DONEGAL", "GALWAY", "LEITRIM", "MAYO", "MONAGHAN", "ROSCOMMON", "SLIGO")
SOUTH_2004 = ("CORK", "KERRY", "LIMERICK", "TIPPERARY", "WATERFORD")
MIDLANDS_NORTH_WEST = (
    "CAVAN",
    "DONEGAL",
    "GALWAY",
    "KILDARE",
    "LAOIS",
    "LEITRIM",
    "LONGFORD",
    "LOUTH",
    "MAYO",
    "MEATH",
    "MONAGHAN",
    "OFFALY",
    "ROSCOMMON",
    "SLIGO",
    "WESTMEATH",
)
SOUTH_2014 = (
    "CARLOW",
    "CLARE",
    "CORK",
    "KERRY",
    "KILKENNY",
    "LIMERICK",
    "TIPPERARY",
    "WATERFORD",
    "WEXFORD",
    "WICKLOW",
)
PLANS = {
    date(1977, 12, 9): {
        "connacht-ulster": CONNACHT_ULSTER,
        "dublin": ("DUBLIN",),
        "leinster": (
            "CARLOW",
            "KILDARE",
            "KILKENNY",
            "LAOIS",
            "LONGFORD",
            "LOUTH",
            "MEATH",
            "OFFALY",
            "WESTMEATH",
            "WEXFORD",
            "WICKLOW",
        ),
        "munster": ("CLARE", *SOUTH_2004),
    },
    date(2004, 2, 27): {
        "dublin": ("DUBLIN",),
        "east": (
            "CARLOW",
            "KILDARE",
            "KILKENNY",
            "LAOIS",
            "LONGFORD",
            "LOUTH",
            "MEATH",
            "OFFALY",
            "WESTMEATH",
            "WEXFORD",
            "WICKLOW",
        ),
        "north-west": ("CLARE", *CONNACHT_ULSTER),
        "south": SOUTH_2004,
    },
    date(2009, 2, 24): {
        "dublin": ("DUBLIN",),
        "east": ("CARLOW", "KILDARE", "KILKENNY", "LAOIS", "LOUTH", "MEATH", "OFFALY", "WEXFORD", "WICKLOW"),
        "north-west": ("CLARE", "LONGFORD", "WESTMEATH", *CONNACHT_ULSTER),
        "south": SOUTH_2004,
    },
    date(2014, 2, 5): {
        "dublin": ("DUBLIN",),
        "midlands-north-west": MIDLANDS_NORTH_WEST,
        "south": SOUTH_2014,
    },
    date(2019, 3, 12): {
        "dublin": ("DUBLIN",),
        "midlands-north-west": tuple(c for c in MIDLANDS_NORTH_WEST if c not in ("LAOIS", "OFFALY")),
        "south": (*SOUTH_2014, "LAOIS", "OFFALY"),
    },
    date(2023, 12, 19): {
        "dublin": ("DUBLIN",),
        "midlands-north-west": MIDLANDS_NORTH_WEST,
        "south": SOUTH_2014,
    },
}
NAMES = {
    "connacht-ulster": "Connacht–Ulster",
    "leinster": "Leinster",
    "munster": "Munster",
    "east": "East",
    "north-west": "North-West",
}
ELECTIONS = (
    date(1979, 6, 7),
    date(1984, 6, 14),
    date(1989, 6, 15),
    date(1994, 6, 9),
    date(1999, 6, 11),
    date(2004, 6, 11),
    date(2009, 6, 5),
    date(2014, 5, 23),
    date(2019, 5, 24),
    date(2024, 6, 7),
)
TOLERANCE = 10


def fetch() -> None:
    raw.fetch(SOURCE, COUNTIES, suffix=".geojson")


def parse() -> None:
    features = json.loads(raw.cached(SOURCE, COUNTIES).path.read_bytes())["features"]
    starts = sorted(PLANS)
    versions: list[tuple[str, date, date | None, tuple[str, ...]]] = []
    for start, stop in zip(starts, [*starts[1:], None], strict=True):
        for code, counties in PLANS[start].items():
            members = tuple(sorted(counties))
            previous = next((v for v in reversed(versions) if v[0] == code), None)
            if previous is not None and previous[2] == start and previous[3] == members:
                versions[versions.index(previous)] = (code, previous[1], stop, members)
            else:
                versions.append((code, start, stop, members))
    with connect() as conn:
        conn.execute("create temp table feature (county text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (county, geojson) from stdin") as copy:
            for feature in features:
                copy.write_row((feature["properties"]["ENG_NAME_VALUE"].upper(), json.dumps(feature["geometry"])))
        conn.execute("create temp table member (version int, county text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy member (version, county) from stdin") as copy:
            for index, (_, _, _, members) in enumerate(versions):
                for county in members:
                    copy.write_row((index, county))
        found = {county for (county,) in conn.execute("select distinct county from feature").fetchall()}
        for start, plan in PLANS.items():
            if sorted(c for counties in plan.values() for c in counties) != sorted(found):
                raise ValueError(f"the {start} plan does not cover each of Tailte Éireann's counties exactly once")
        rows = conn.execute(
            """with county as (
                 select county, st_setsrid(st_coveragesimplify(geom, %s) over (), 2157) as geom
                 from (select county, st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(
                                st_force2d(st_setsrid(st_geomfromgeojson(geojson), 2157))))), 3)) as geom
                       from feature group by county) counties
               ),
               layers as (
                 select m.version, st_setsrid(st_coverageunion(c.geom), 2157) as geom
                 from county c join member m using (county) group by m.version
                 union all
                 select -1, st_setsrid(st_coverageunion(geom), 2157) from county
               )
               select version,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      case when version >= 0
                        then encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex') end
               from layers""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        [
            ("nation", "IE", None, None, geom, point)
            if index < 0
            else ("ep_constituency", versions[index][0], versions[index][1], versions[index][2], geom, point)
            for index, geom, point in rows
        ],
        schema={
            "layer": pl.String,
            "code": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    if frame.height != len(versions) + 1 or frame["geom"].null_count():
        raise ValueError(f"expected {len(versions)} constituency versions and the nation, got {frame.height} rows")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.sort("layer", "code", "valid_from").write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Ireland's European Parliament constituencies under each plan from the European Assembly "
                "Elections Act 1977 to the Electoral (Amendment) Act 2023, and the nation, as unions of "
                "Tailte Éireann's statutory counties"
            ),
            polity="IE",
        )
        title = "Counties - National Statutory Boundaries - Ungeneralised - 2024"
        source = cs.source("Tailte Éireann", title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Tailte Éireann",
            title=title,
            url="https://data-osi.opendata.arcgis.com/datasets/dc24df2a5ce84ee9a38d9afe8431ee9b_1",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="© Tailte Éireann",
        )
        cs.artifact(raw.cached(SOURCE, COUNTIES), source)
        nation = cs.area("ie.nation", "IE")
        for code, label in NAMES.items():
            area = cs.area("ie.ep_constituency", code)
            cs.add("area", id=area, scheme_code="ie.ep_constituency")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
            cs.add("name", entity_id=area, text=label, lang="en", kind="official", is_preferred=True)
        for layer, code, start, stop, geom, point in frame.rows():
            cs.add(
                "area_geometry",
                area_id=cs.area(f"ie.{layer}", code),
                valid_from=start,
                valid_to=stop,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        starts = sorted(PLANS)
        for start, stop in zip(starts, [*starts[1:], None], strict=True):
            cs.add(
                "boundary_set",
                scheme_code="ie.ep_constituency",
                vintage=str(start.year),
                valid_from=start,
                valid_to=stop,
                as_of=max(e for e in ELECTIONS if e >= start and (stop is None or e < stop)),
            )
        return cs.write()
