import io
import json
import re
import tarfile
import zipfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.census"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
TIGER = "https://www2.census.gov/geo/tiger/"
STATES = f"{TIGER}GENZ2020/shp/cb_2020_us_state_500k.zip"
COUNTIES = f"{TIGER}GENZ2020/shp/cb_2020_us_county_500k.zip"
DISTRICTS = {
    "108": (f"{TIGER}PREVGENZ/cd/cd108shp/cd99_108_shp.zip", date(2003, 1, 3), date(2005, 1, 3)),
    "109": (f"{TIGER}PREVGENZ/cd/cd109shp/cd99_109_shp.zip", date(2005, 1, 3), date(2007, 1, 3)),
    "110": (f"{TIGER}PREVGENZ/cd/cd110shp/cd99_110_shp.zip", date(2007, 1, 3), date(2009, 1, 3)),
    "111": (f"{TIGER}GENZ2010/gz_2010_us_500_11_5m.zip", date(2009, 1, 3), date(2013, 1, 3)),
    "113": (f"{TIGER}GENZ2013/cb_2013_us_cd113_500k.zip", date(2013, 1, 3), date(2015, 1, 3)),
    "114": (f"{TIGER}GENZ2014/shp/cb_2014_us_cd114_500k.zip", date(2015, 1, 3), date(2017, 1, 3)),
    "115": (f"{TIGER}GENZ2016/shp/cb_2016_us_cd115_500k.zip", date(2017, 1, 3), date(2019, 1, 3)),
    "116": (f"{TIGER}GENZ2020/shp/cb_2020_us_cd116_500k.zip", date(2019, 1, 3), date(2023, 1, 3)),
    "118": (f"{TIGER}GENZ2022/shp/cb_2022_us_cd118_500k.zip", date(2023, 1, 3), date(2025, 1, 3)),
    "119": (f"{TIGER}GENZ2024/shp/cb_2024_us_cd119_500k.zip", date(2025, 1, 3), None),
}
REDRAWN_2021 = {"37"}
LEWIS = (
    "https://github.com/JeffreyBLewis/congressional-district-boundaries/archive/"
    "aba2b6b17120718782605534b4bcc6c5ab3d09e2.tar.gz"
)
LEWIS_TITLE = "Digital Boundary Definitions of United States Congressional Districts, 1789–2025"
CONGRESSES = {number: date(1787 + 2 * number, *((3, 4) if number <= 73 else (1, 3))) for number in range(1, 121)}
DELEGATES = {
    "11": ((42, 43), (92, 119)),
    "60": ((97, 119),),
    "66": ((93, 119),),
    "69": ((111, 119),),
    "72": ((57, 119),),
    "78": ((93, 119),),
}
UNCOVERED = {"3633": (98, 102)}
UNCOVERED_NOTE = (
    "The Lewis et al. district maps leave this district out from 1983 to 1993. Its shape for those years is the part "
    "of New York that the state's other districts don't cover."
)
TOLERANCE = 0.0005


def fetch() -> None:
    for url in (STATES, COUNTIES, *(url for url, _, _ in DISTRICTS.values())):
        raw.fetch(SOURCE, url, suffix=".zip")
    raw.fetch(SOURCE, LEWIS, suffix=".tar.gz")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    layers: dict[str, list[tuple[Mapping[str, object], str]]] = {}
    for layer, url in (("state", STATES), ("county", COUNTIES), *((f"cd{v}", u) for v, (u, _, _) in DISTRICTS.items())):
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as z:
            parts = {name.rsplit(".", 1)[1].lower(): z.read(name) for name in z.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]),
            shx=io.BytesIO(parts["shx"]),
            dbf=io.BytesIO(parts["dbf"]),
            encoding="utf-8" if b"UTF" in parts.get("cpg", b"UTF-8").upper() else "latin-1",
        )
        layers[layer] = [
            (record.as_dict(), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None and shape.shapeType != shapefile.NULL
        ]
        if layer.startswith("cd") and int(layer[2:]) < 113:
            layers[layer] = [
                (
                    {
                        "GEOID": f"{record['STATE']}{record['CD']}",
                        "STATEFP": record["STATE"],
                        f"CD{layer[2:]}FP": record["CD"],
                    },
                    geojson,
                )
                for record, geojson in layers[layer]
                if record["CD"]
            ]

    historical: list[dict[str, object]] = []
    with connect() as conn:
        with tarfile.open(raw.cached(SOURCE, LEWIS).path, "r|gz") as archive:
            for member in archive:
                match = re.search(r"/GeoJson/(.+)_(\d{3})_to_(\d{3})\.geojson$", member.name)
                file = archive.extractfile(member)
                if match is None or file is None or match[1] == "District Of Columbia" or int(match[2]) > 117:
                    continue
                features: list[tuple[str, int, int, int, int, bool, str]] = []
                for feature in json.load(file)["features"]:
                    properties = feature["properties"]
                    first, last, number = (int(properties[key]) for key in ("startcong", "endcong", "district"))
                    for low, high in (
                        (first, min(last, 107)),
                        *([(117, 117)] if match[1] == "North Carolina" and first <= 117 <= last else []),
                    ):
                        if number >= 0 and low <= high:
                            features.append(
                                (
                                    f"{properties['id'][1:3]}{number:02d}",
                                    first,
                                    last,
                                    low,
                                    high,
                                    number == 0,
                                    json.dumps(feature["geometry"]),
                                )
                            )
                if not features:
                    continue
                historical.extend(
                    {
                        "code": code,
                        "valid_from": CONGRESSES[low],
                        "valid_to": CONGRESSES[high + 1],
                        "quality": "academic",
                        "geom": geom,
                        "label_point": point,
                    }
                    for code, low, high, geom, point in conn.execute(
                        """select code, low, high, encode(st_asewkb(geom), 'hex'),
                                  encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                           from (
                             select code, low, high,
                                    case when count(*) = 1 then (array_agg(geom))[1]
                                         else st_multi(st_collectionextract(st_union(geom), 3)) end as geom
                             from (
                               select code, low, high,
                                      st_coveragesimplify(geom, %s) over (partition by first, last, low, high, at_large) as geom
                               from (
                                 select code, first, last, low, high, at_large,
                                        st_multi(st_collectionextract(st_makevalid(st_transform(
                                          st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                                 from unnest(%s::text[], %s::int[], %s::int[], %s::int[], %s::int[], %s::bool[],
                                             %s::text[]) as f(code, first, last, low, high, at_large, geojson)
                               ) shapes
                             ) simplified
                             group by code, low, high
                           ) districts""",
                        [TOLERANCE, *(list(column) for column in zip(*features, strict=True))],
                    ).fetchall()
                )

        conn.execute(
            """create temp table shape on commit drop as
               select layer, code, st_multi(st_collectionextract(st_makevalid(st_transform(
                        st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
               from unnest(%s::text[], %s::text[], %s::text[]) as f(layer, code, geojson)""",
            [
                [layer for layer, rows in layers.items() for _ in rows],
                [str(record["GEOID"]) for rows in layers.values() for record, _ in rows],
                [geojson for rows in layers.values() for _, geojson in rows],
            ],
        )
        geometry = {
            (layer, code): (geom, point)
            for layer, code, geom, point in conn.execute(
                """select layer, code, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (
                     select layer, code,
                            case when count(*) = 1 then (array_agg(geom))[1]
                                 else st_multi(st_collectionextract(st_union(geom), 3)) end as geom
                     from shape group by layer, code
                   ) parts
                   union all
                   select 'nation', 'US', encode(st_asewkb(st_multi(st_union(geom))), 'hex'), null
                   from shape where layer = 'state'"""
            ).fetchall()
        }
        for code, (first, last) in UNCOVERED.items():
            plans = {
                (row["valid_from"], row["valid_to"])
                for row in historical
                if str(row["code"]).startswith(code[:2])
                and row["valid_from"] in {CONGRESSES[number] for number in range(first, last + 1)}
            }
            for valid_from, valid_to in sorted(plans, key=str):
                derived = conn.execute(
                    """select encode(st_asewkb(st_multi(piece)), 'hex'), encode(st_asewkb(st_pointonsurface(piece)), 'hex')
                       from (select (st_dump(st_difference(st_setsrid(%s::geometry, 4326), st_union(st_setsrid(other::geometry, 4326))))).geom as piece
                             from unnest(%s::text[]) as other) pieces
                       order by st_area(piece::geography) desc limit 1""",
                    [
                        geometry[("state", code[:2])][0],
                        [
                            row["geom"]
                            for row in historical
                            if str(row["code"]).startswith(code[:2]) and row["valid_from"] == valid_from
                        ],
                    ],
                ).fetchone()
                if derived is None:
                    raise ValueError(f"{code}: nothing left uncovered for {valid_from}")
                historical.append(
                    {
                        "code": code,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "quality": "synthetic",
                        "geom": derived[0],
                        "label_point": derived[1],
                    }
                )

    pl.DataFrame(
        [
            {
                "code": record["GEOID"],
                "usps": record["STUSPS"],
                "name": record["NAME"],
                "geom": geometry[("state", str(record["GEOID"]))][0],
                "label_point": geometry[("state", str(record["GEOID"]))][1],
            }
            for record, _ in layers["state"]
        ]
        + [{"code": "US", "usps": "US", "name": "United States", "geom": geometry[("nation", "US")][0]}]
    ).write_parquet(PARSED / "state.parquet")
    pl.DataFrame(
        [
            {
                "code": record["GEOID"],
                "state": record["STATEFP"],
                "name": record["NAMELSAD"],
                "short": record["NAME"],
                "geom": geometry[("county", str(record["GEOID"]))][0],
                "label_point": geometry[("county", str(record["GEOID"]))][1],
            }
            for record, _ in layers["county"]
        ]
    ).write_parquet(PARSED / "county.parquet")
    districts = pl.DataFrame(
        [
            {
                "code": record["GEOID"],
                "valid_from": valid_from,
                "valid_to": date(2021, 1, 3) if vintage == "116" and record["STATEFP"] in REDRAWN_2021 else valid_to,
                "quality": "official",
                "geom": geometry[(f"cd{vintage}", str(record["GEOID"]))][0],
                "label_point": geometry[(f"cd{vintage}", str(record["GEOID"]))][1],
            }
            for vintage, (_, valid_from, valid_to) in DISTRICTS.items()
            for record in {str(record["GEOID"]): record for record, _ in layers[f"cd{vintage}"]}.values()
            if record[f"CD{vintage}FP"] != "ZZ"
        ]
        + [
            {
                "code": f"{state}98",
                "valid_from": valid_from,
                "valid_to": valid_to,
                "quality": "official",
                "geom": geometry[("state", state)][0],
                "label_point": geometry[("state", state)][1],
            }
            for state, spans in DELEGATES.items()
            for valid_from, valid_to in [
                *(
                    (valid_from, valid_to)
                    for vintage, (_, valid_from, valid_to) in DISTRICTS.items()
                    if any(CONGRESSES[first] <= valid_from < CONGRESSES[last + 1] for first, last in spans)
                    and not any(record["GEOID"] == f"{state}98" for record, _ in layers[f"cd{vintage}"])
                ),
                *((CONGRESSES[first], CONGRESSES[min(last, 107) + 1]) for first, last in spans if first <= 107),
            ]
        ]
        + historical
    ).sort("code", "valid_from")
    overlaps = districts.filter(pl.col("valid_from") < pl.col("valid_to").shift(1).over("code"))
    if overlaps.height:
        raise ValueError(f"district geometry overlaps in time: {overlaps.select('code', 'valid_from').rows()}")
    districts.write_parquet(PARSED / "district.parquet")


def emit() -> Path:
    states = pl.read_parquet(PARSED / "state.parquet")
    counties = pl.read_parquet(PARSED / "county.parquet")
    districts = pl.read_parquet(PARSED / "district.parquet")

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="US states, counties, and congressional districts from Census cartographic boundary files and, for 1789–2003 and North Carolina in 2021–2023, from UCLA's historical district boundaries",
            polity="US",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.add(
            "source",
            id=census,
            kind="official_body",
            publisher="U.S. Census Bureau",
            title="Cartographic Boundary Files, 1:500,000",
            url="https://www.census.gov/geographies/mapping-files/time-series/geo/carto-boundary-file.html",
            license="Public domain",
        )
        for url in (STATES, COUNTIES, *(url for url, _, _ in DISTRICTS.values())):
            cs.artifact(raw.cached(SOURCE, url), census)
        lewis = cs.source("University of California, Los Angeles", LEWIS_TITLE)
        cs.add(
            "source",
            id=lewis,
            kind="academic",
            publisher="University of California, Los Angeles",
            title=LEWIS_TITLE,
            url="https://cdmaps.polisci.ucla.edu",
            license="MIT",
            attribution="Jeffrey B. Lewis, Brandon DeVine, Lincoln Pitcher, and Kenneth C. Martis",
        )
        cs.artifact(raw.cached(SOURCE, LEWIS), lewis)

        us = cs.polity("US")
        cs.add("polity", id=us, kind="sovereign_state", iso_code="US")
        name(us, "United States")
        cs.add("slug", path="us", entity_id=us)
        cs.add("external_id", entity_id=us, scheme="wikidata", value="Q30")
        for code, rank, noun, kind in (
            ("us.nation", 0, "country", "administrative"),
            ("us.state", 1, "state", "administrative"),
            ("us.county", 2, "county", "administrative"),
            ("us.cd", 2, "congressional district", "electoral_district"),
        ):
            cs.add("scheme", code=code, polity_id=us, kind=kind, rank=rank, noun=noun)

        state_areas: dict[str, UUID] = {}
        state_units: dict[str, UUID] = {}
        usps: dict[str, str] = {}
        state_names: dict[str, str] = {}
        for state in states.sort("code").iter_rows(named=True):
            if state["code"] == "US":
                nation = cs.area("us.nation", "US")
                cs.add("area", id=nation, scheme_code="us.nation")
                cs.add("area_code", area_id=nation, code="US", valid_from=None)
                name(nation, "United States")
                cs.add("polity_territory", polity_id=us, area_id=nation)
                cs.add(
                    "area_geometry",
                    area_id=nation,
                    valid_from=None,
                    geom=state["geom"],
                    label_point=None,
                    quality="official",
                    source_id=census,
                )
                continue
            polity = cs.polity(f"US-{state['usps']}")
            usps[state["code"]] = state["usps"]
            state_names[state["code"]] = state["name"]
            cs.add(
                "polity",
                id=polity,
                kind="federal_unit" if int(state["code"]) <= 56 else "region",
                iso_code=f"US-{state['usps']}",
            )
            name(polity, state["name"])
            cs.add("polity_parent", polity_id=polity, parent_id=us)
            cs.add("slug", path=f"us/{state['usps'].lower()}", entity_id=polity)
            area = state_areas[state["code"]] = cs.area("us.state", state["code"])
            cs.add("area", id=area, scheme_code="us.state")
            cs.add("area_code", area_id=area, code=state["code"], valid_from=None)
            name(area, state["name"])
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=state["geom"],
                label_point=state["label_point"],
                quality="official",
                source_id=census,
            )
            unit = state_units[state["code"]] = cs.unit("us.state", state["code"])
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code="us.state",
                code=state["code"],
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        for area in state_areas.values():
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.nation", "US"))

        for county in counties.sort("code").iter_rows(named=True):
            area = cs.area("us.county", county["code"])
            cs.add("area", id=area, scheme_code="us.county")
            cs.add("area_code", area_id=area, code=county["code"], valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_areas[county["state"]])
            name(area, f"{county['name']}, {usps[county['state']]}")
            name(area, county["short"], "short")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=county["geom"],
                label_point=county["label_point"],
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.county", county["code"]),
                polity_id=cs.polity(f"US-{usps[county['state']]}"),
                scheme_code="us.county",
                code=county["code"],
                parent_id=state_units[county["state"]],
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        cs.add("boundary_set", scheme_code="us.state", vintage="2020", valid_from=None, as_of=date(2020, 1, 1))
        cs.add("boundary_set", scheme_code="us.county", vintage="2020", valid_from=None, as_of=date(2020, 1, 1))

        spans: dict[str, tuple[date, date | None]] = {}
        for district in districts.iter_rows(named=True):
            valid_from, valid_to = district["valid_from"], district["valid_to"]
            start, end = spans.get(district["code"], (valid_from, valid_to))
            spans[district["code"]] = (
                min(start, valid_from),
                None if end is None or valid_to is None else max(end, valid_to),
            )
            cs.add(
                "area_geometry",
                area_id=cs.area("us.cd", district["code"]),
                valid_from=valid_from,
                valid_to=valid_to,
                geom=district["geom"],
                label_point=district["label_point"],
                quality=district["quality"],
                source_id=census if district["quality"] == "official" else lewis,
            )
        for code in UNCOVERED:
            cs.add("note", entity_id=cs.area("us.cd", code), kind="caveat", body_md=UNCOVERED_NOTE)
        for code, (valid_from, valid_to) in sorted(spans.items()):
            area = cs.area("us.cd", code)
            state, number = code[:2], int(code[2:])
            cs.add("area", id=area, scheme_code="us.cd")
            cs.add("area_code", area_id=area, code=code, valid_from=valid_from, valid_to=valid_to)
            cs.add("area_parent", area_id=area, parent_id=state_areas[state])
            cs.add(
                "reporting_unit",
                id=cs.unit("us.cd", code),
                polity_id=cs.polity(f"US-{usps[state]}"),
                scheme_code="us.cd",
                code=code,
                parent_id=state_units[state],
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=valid_from,
                valid_to=valid_to,
            )
            if number in (0, 98):
                name(area, f"{state_names[state]}'s at-large congressional district")
                name(area, f"{usps[state]}-AL", "short")
            else:
                suffix = "th" if 11 <= number % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
                name(area, f"{state_names[state]}'s {number}{suffix} congressional district")
                name(area, f"{usps[state]}-{number:02d}", "short")
        for vintage, valid_from, valid_to in (
            *((vintage, valid_from, valid_to) for vintage, (_, valid_from, valid_to) in DISTRICTS.items()),
            *((str(number), CONGRESSES[number], CONGRESSES[number + 1]) for number in (*range(1, 108), 117)),
        ):
            cs.add(
                "boundary_set",
                scheme_code="us.cd",
                vintage=vintage,
                valid_from=valid_from,
                valid_to=valid_to,
                as_of=valid_from,
            )
        return cs.write()
