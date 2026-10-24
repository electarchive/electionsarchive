import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.ons_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LAYERS = {
    "wd21": ("Wards_December_2021_UK_BGC_2022", "WD21CD", 8694, 2000, "Wards (December 2021) Boundaries UK BGC"),
    "wd22": ("Wards_December_2022_Boundaries_UK_BGC", "WD22CD", 8483, 1000, "Wards (December 2022) Boundaries UK BGC"),
    "ced23": (
        "County_Electoral_Division_May_2023_Boundaries_EN_BGC",
        "CED23CD",
        1367,
        2000,
        "County Electoral Division (May 2023) Boundaries EN BGC",
    ),
    "lad21": (
        "Local_Authority_Districts_May_2021_UK_BGC_2022",
        "LAD21CD",
        374,
        2000,
        "Local Authority Districts (May 2021) Boundaries UK BGC",
    ),
    "lad22": (
        "Local_Authority_Districts_May_2022_UK_BGC_V3_2022",
        "LAD22CD",
        374,
        2000,
        "Local Authority Districts (May 2022) Boundaries UK BGC",
    ),
    "lad23": (
        "Local_Authority_Districts_May_2023_UK_BGC_V2",
        "LAD23CD",
        361,
        2000,
        "Local Authority Districts (May 2023) Boundaries UK BGC",
    ),
    "cty21": ("Counties_May_2021_EN_BGC_2022", "CTY21CD", 24, 2000, "Counties (May 2021) Boundaries EN BGC"),
}
POLLS = {2021: date(2021, 5, 6), 2022: date(2022, 5, 5)}
PUBLISHER = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)
NOTES = {
    "wd21": "Ward boundaries as of December 2021, which include the changes made at the May 2021 elections.",
    "wd22": "Ward boundaries as of December 2022, which include the changes made at the May 2022 elections.",
    "lad23": (
        "Local authority boundaries as of May 2023, used for the four unitary councils created that April, whose "
        "shadow councils were elected in May 2022."
    ),
    "ced23": (
        "ONS publishes no county electoral division boundaries before May 2023; divisions whose GSS code is "
        "unchanged since 2021 kept their boundaries."
    ),
}


def pages(key: str) -> list[str]:
    service, field, count, size, _ = LAYERS[key]
    return [
        f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&orderByFields={field}"
        f"&resultOffset={offset}&resultRecordCount={size}&f=geojson"
        for offset in range(0, count, size)
    ]


def fetch() -> None:
    for key in LAYERS:
        for url in pages(key):
            raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (layer text, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for key, (_, field, count, _, _) in LAYERS.items():
                rows = [
                    feature
                    for url in pages(key)
                    for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
                ]
                if len(rows) != count:
                    raise ValueError(f"{key}: {len(rows)} features, expected {count}")
                for feature in rows:
                    copy.write_row((key, feature["properties"][field], json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select layer, code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select layer, code, st_multi(st_collectionextract(st_makevalid(st_union(
                       st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
                     from feature group by layer, code) whole"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    print(frame.group_by("layer").len().sort("layer").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    shapes = {(layer, code): (geom, point) for layer, code, geom, point in frame.iter_rows()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ward, county division, council, and county boundaries for England and Wales's 2021 and 2022 "
            "local elections from ONS",
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for key, (service, _, _, _, title) in LAYERS.items():
            sources[key] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=NOTES.get(key, "Generalized boundaries (20 m) clipped to the coastline."),
            )
            for url in pages(key):
                cs.artifact(raw.cached(SOURCE, url), sources[key])
        wards = conn.execute(
            """select a.id, c.code, array_agg(distinct extract(year from e.date_from)::int)
               from area a join area_code c on c.area_id = a.id
               left join contest ct on ct.district_area_id = a.id
               left join election e on e.id = ct.election_id
               where a.scheme_code = 'gb.ward' group by a.id, c.code"""
        ).fetchall()
        councils = conn.execute(
            """select a.id, c.code from area a join area_code c on c.area_id = a.id where a.scheme_code = 'gb.lad'"""
        ).fetchall()
        missing: list[str] = []
        for area, code, years in wards:
            first = min(year for year in years if year is not None)
            layer = next(
                (
                    key
                    for key in (
                        ["ced23"] if code.startswith("E58") else ["wd21", "wd22"][:: 1 if first == 2021 else -1]
                    )
                    if (key, code) in shapes
                ),
                None,
            )
            if layer is None:
                missing.append(f"{code} ({first})")
                continue
            ends = first == 2021 and ("wd22", code) not in shapes
            geom, point = shapes[(layer, code)]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=POLLS[first],
                valid_to=POLLS[2022] if ends else None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[layer],
            )
        for area, code in councils:
            layer = next(
                (key for key in ("cty21", "lad21", "lad22", "lad23") if (key, code) in shapes),
                None,
            )
            if layer is None:
                missing.append(f"{code} (council)")
                continue
            geom, point = shapes[(layer, code)]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=POLLS[2021] if layer in ("cty21", "lad21") else POLLS[2022],
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[layer],
            )
        print(f"{len(missing)} areas without a shape: {', '.join(sorted(missing))}")
        plans = {
            (scheme, vintage): id
            for id, scheme, vintage in conn.execute(
                "select id, scheme_code, vintage from boundary_set where scheme_code in ('gb.ward', 'gb.lad')"
            ).fetchall()
        }
        for scheme in ("gb.ward", "gb.lad"):
            for year, end in ((2021, POLLS[2022]), (2022, None)):
                cs.add(
                    "boundary_set",
                    **({"id": plans[(scheme, str(year))]} if (scheme, str(year)) in plans else {}),
                    scheme_code=scheme,
                    vintage=str(year),
                    valid_from=POLLS[year],
                    valid_to=end,
                    as_of=POLLS[year],
                )
        return cs.write()
