import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.regions"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
ERAS = {
    "2010": (
        "WD19_PCON19_LAD19_UK_LU_2b79d5f36feb42938063932a4d4a3533",
        "LAD19_RGN19_EN_LU_948748b0eaa54fe888a604b126f5e672",
        "Regions_December_2019_General_Clipped_Boundaries_EN_2022",
        "19",
        date(2010, 5, 6),
        date(2024, 7, 4),
        533,
    ),
    "2024": (
        "WD24_PCON24_LAD24_UTLA24_UK_LU",
        "LAD24_RGN24_EN_LU",
        "Regions_December_2023_Boundaries_EN_BGC",
        "23",
        date(2024, 7, 4),
        None,
        543,
    ),
}
TITLES = {
    "WD19_PCON19_LAD19_UK_LU_2b79d5f36feb42938063932a4d4a3533": "Ward to Westminster Parliamentary Constituency to "
    "Local Authority District (December 2019) Lookup in the UK",
    "LAD19_RGN19_EN_LU_948748b0eaa54fe888a604b126f5e672": "Local Authority District to Region (December 2019) "
    "Lookup in England",
    "Regions_December_2019_General_Clipped_Boundaries_EN_2022": "Regions (December 2019) Boundaries EN BGC",
    "WD24_PCON24_LAD24_UTLA24_UK_LU": "Ward to Westminster Parliamentary Constituency to Local Authority District to "
    "Upper Tier Local Authority (July 2024) Lookup in the UK",
    "LAD24_RGN24_EN_LU": "Local Authority District to Region (December 2024) Lookup in England",
    "Regions_December_2023_Boundaries_EN_BGC": "Regions (December 2023) Boundaries EN BGC",
}
PUBLISHER = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)


def pages(service: str) -> list[str]:
    count = json.loads(
        raw.fetch(
            SOURCE,
            f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&returnCountOnly=true&f=json",
            suffix=".json",
        ).path.read_text()
    )["count"]
    return [
        f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"
        + (
            f"&orderByFields={'ObjectId' if service.startswith('WD24') else 'FID'}&resultOffset={offset}&resultRecordCount=1000"
            if count > 1000
            else ""
        )
        for offset in range(0, count, 1000)
    ]


def fetch() -> None:
    for service in TITLES:
        for url in pages(service):
            if "features" not in json.loads(raw.fetch(SOURCE, url, suffix=".geojson").path.read_text()):
                raise ValueError(f"{url} returned no features")


def features(service: str) -> list[dict[str, object]]:
    return [
        {key.upper(): value for key, value in feature["properties"].items()} | {"geometry": feature["geometry"]}
        for url in pages(service)
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    ]


def parse() -> None:
    parents: list[tuple[str, str, str]] = []
    regions: list[tuple[str, str, str, str]] = []
    for era, (wards, lookup, shapes, year, _, _, seats) in ERAS.items():
        pcon = "PCON24CD" if era == "2024" else "PCON19CD"
        lad = "LAD24CD" if era == "2024" else "LAD19CD"
        rgn = "RGN24CD" if era == "2024" else "RGN19CD"
        region_of = {str(row[lad]): str(row[rgn]) for row in features(lookup)}
        found: dict[str, set[str]] = {}
        for row in features(wards):
            if str(row[pcon]).startswith("E14"):
                found.setdefault(str(row[pcon]), set()).add(region_of[str(row[lad])])
        split = {code: sorted(names) for code, names in found.items() if len(names) != 1}
        if split or len(found) != seats:
            raise ValueError(f"{era}: {len(found)} English seats, {seats} expected; split {split}")
        parents += [(era, code, names.pop()) for code, names in sorted(found.items())]
        for row in features(shapes):
            regions.append((era, str(row[f"RGN{year}CD"]), str(row[f"RGN{year}NM"]), json.dumps(row["geometry"])))
        if {row[1] for row in regions if row[0] == era} != set(region_of.values()):
            raise ValueError(f"{era}: region shapes and lookup codes differ")
    with connect() as conn:
        conn.execute("create temp table feature (era text, code text, name text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for row in regions:
                copy.write_row(row)
        rows = conn.execute(
            """select era, code, name,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select era, code, name, st_multi(st_collectionextract(st_makevalid(st_force2d(
                       st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom from feature) shape"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"era": pl.String, "code": pl.String, "name": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    ).write_parquet(PARSED / "regions.parquet")
    pl.DataFrame(
        parents, schema={"era": pl.String, "pcon": pl.String, "region": pl.String}, orient="row"
    ).write_parquet(PARSED / "parents.parquet")


def emit() -> Path:
    regions = pl.read_parquet(PARSED / "regions.parquet")
    parents = pl.read_parquet(PARSED / "parents.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="England's nine regions from the Office for National Statistics, with each 2010 and 2024 "
            "Westminster constituency placed in its region",
            polity="GB",
        )
        cs.manifest["scope"] = {"polity": "GB", "replaces": {"area_parent": ["gb.ons"]}}
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.region'").fetchone() is None:
            cs.add("scheme", code="gb.region", polity_id=gb, kind="administrative", rank=2, noun="region")
        sources = {}
        for service, title in TITLES.items():
            sources[service] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[service],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
            )
            for url in pages(service):
                cs.artifact(raw.cached(SOURCE, url), sources[service])
        made = set()
        for era, (_, _, shapes, _, start, end, _) in ERAS.items():
            england = cs.area("gb.country", "ENG", on=start)
            if england in cs.minted:
                raise ValueError("publish gb.umit before gb.regions")
            for code, label, geom, point in (
                regions.filter(pl.col("era") == era).select("code", "name", "geom", "label_point").sort("code").rows()
            ):
                area = cs.area("gb.region", code)
                if area in cs.minted and area not in made:
                    made.add(area)
                    cs.add("area", id=area, scheme_code="gb.region")
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=england, valid_from=None, valid_to=None)
                    cs.add("name", entity_id=area, text=label, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=start,
                    valid_to=end,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[shapes],
                )
            for pcon, region in parents.filter(pl.col("era") == era).select("pcon", "region").rows():
                seat = cs.area("gb.pcon", pcon, on=start)
                if seat in cs.minted:
                    raise ValueError(f"{pcon} is not a published gb.pcon area")
                cs.add(
                    "area_parent",
                    area_id=seat,
                    parent_id=cs.area("gb.region", region),
                    valid_from=start,
                    valid_to=end,
                )
            cs.add("boundary_set", scheme_code="gb.region", vintage=era, valid_from=start, valid_to=end, as_of=start)
        return cs.write()
