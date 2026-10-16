import io
import json
import re
import zipfile
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import shapefile
from pyproj import CRS

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.precinct_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 11, 8)
PUBLISHERS = {
    "MN": ("Minnesota Secretary of State", "General Election Results By Precinct 2022", "Public record", None),
    "MI": ("Michigan Department of State, Bureau of Elections", "2022 Voting Precincts", "Public record", None),
    "MD": (
        "Maryland Department of Planning",
        "2022 Maryland Precincts",
        "Free to distribute with the metadata intact",
        "State of Maryland",
    ),
    "DC": ("District of Columbia Board of Elections", "Voting Precinct", "CC BY 4.0", "DC GIS"),
    "OK": (
        "Oklahoma State Election Board",
        "Voter Precincts 2020",
        "Public record",
        "Center for Spatial Analysis, University of Oklahoma",
    ),
    "RI": ("Rhode Island Department of State", "Voting Precincts (2022)", "Public record", "RIGIS"),
    "AZ": ("Maricopa County Recorder", "Maricopa County Voting Precincts (2022-)", "Public record", None),
    "NC": ("North Carolina State Board of Elections", "Precinct Shapefile, 2022-08-31", "Public record", None),
    "TX": ("Texas Legislative Council", "Precincts, 2022 General Election", "CC BY", None),
    "WA": ("Washington Secretary of State", "Statewide Precincts, 2022 General, Consolidated", "Public record", None),
    "LA": ("Louisiana Legislature", "2022 Louisiana Precincts (12-25-2022)", "Public record", None),
    "MA": ("MassGIS", "2022 Wards and Precincts", "Public record", None),
    "NM": ("New Mexico Secretary of State", "New Mexico Voting Precincts July 2024", "Public domain", "RGIS"),
    "WV": (
        "West Virginia GIS Technical Center",
        "WV Precinct 2022 (10-31-2022)",
        "Free to copy and distribute (MapWV terms of use)",
        None,
    ),
    "NH": (
        "New Hampshire Office of Planning and Development",
        "NH Political Districts 2022",
        "Public record",
        "NH GRANIT",
    ),
}
ARCGIS = "query?where=1%3D1&outFields=*&outSR=4326&f=geojson&resultOffset={offset}&resultRecordCount={count}"
LAYERS = {
    "MN": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2022_2030/FeatureServer/1/",
    "MI": "https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/boundaries/MapServer/7/",
    "MD": "https://mdgeodata.md.gov/archive/rest/services/Boundaries/MD_ArchivedElectionBoundaries/MapServer/4/",
    "DC": "https://maps2.dcgis.dc.gov/DCGIS/rest/services/DCGIS_DATA/Administrative_Other_Boundaries/MapServer/0/",
    "OK": "https://services.arcgis.com/3xOwF6p0r7IHIjfn/arcgis/rest/services/State_Wide_2020_Precincts/FeatureServer/0/",
    "RI": "https://services2.arcgis.com/S8zZg9pg23JUEexQ/ArcGIS/rest/services/BND_Voting_Precincts_2022/FeatureServer/0/",
    "AZ": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2022-)/FeatureServer/0/",
}
ZIPS = {
    "NC": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20220831.zip",
    "TX": "https://data.capitol.texas.gov/dataset/d04c72b9-16c4-4ab2-8c6d-c666d41e04b7/resource/8d1035a6-0aaf-495e-aef1-ede0b70deae4/download/precincts22g.zip",
    "WA": "https://www.sos.wa.gov/sites/default/files/2023-05/statewide_precincts_2022general_ok_consol.zip",
    "WI": "https://web.s3.wisc.edu/rml-gisdata/WI_MunicipalWards_Spring_2022.zip",
    "LA": "https://redist.legis.la.gov/2022_GeoUpdates/2022_Louisiana_Precincts_(12-25-2022).zip",
    "MA": "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/wardsprecincts2022_poly.zip",
    "NM": "https://gstore.unm.edu/apps/rgis/datasets/3b1de40a-b37e-4d35-abdd-fd40bbbbe39e/New_Mexico_Voting_Precinct_20240703.original.zip",
    "NH": "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Administrative_and_Political_Boundaries/d-nhpolitdists/2022/NHPolitDists2022.zip",
    "WV": "https://data.wvgis.wvu.edu/pub/Clearinghouse/Boundaries/WV_Voting_Districts_Precincts/Archive/WV_Precinct_2022_103122_UTM83.zip",
}


def pages(state: str) -> list[str]:
    layer = LAYERS[state]
    count = json.loads(raw.cached(SOURCE, layer + "?f=json").path.read_text())["maxRecordCount"]
    urls = []
    while True:
        urls.append(layer + ARCGIS.format(offset=len(urls) * count, count=count))
        try:
            page = json.loads(raw.cached(SOURCE, urls[-1]).path.read_text())
        except LookupError:
            return urls
        if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
            return urls


def fetch() -> None:
    for layer in LAYERS.values():
        count = json.loads(raw.fetch(SOURCE, layer + "?f=json", suffix=".json").path.read_text())["maxRecordCount"]
        offset = 0
        while True:
            page = json.loads(
                raw.fetch(SOURCE, layer + ARCGIS.format(offset=offset, count=count), suffix=".geojson").path.read_text()
            )
            if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
                break
            offset += count
    for url in ZIPS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def features(state: str) -> Iterator[tuple[dict[str, object], str, int]]:
    if state in LAYERS:
        for url in pages(state):
            for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
                if feature["geometry"] is not None:
                    yield feature["properties"], json.dumps(feature["geometry"]), 4326
        return
    with zipfile.ZipFile(raw.cached(SOURCE, ZIPS[state]).path) as archive:
        stems = sorted(
            name.rsplit(".", 1)[0]
            for name in archive.namelist()
            if name.lower().endswith(".shp") and not name.startswith("__MACOSX")
        )
        for stem in stems:
            parts = {
                name.rsplit(".", 1)[1].lower(): archive.read(name)
                for name in archive.namelist()
                if name.rsplit(".", 1)[0] == stem and name.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf", "prj")
            }
            srid = CRS(parts["prj"].decode("latin-1")).to_epsg(min_confidence=20)
            assert srid is not None, stem
            reader = shapefile.Reader(
                shp=io.BytesIO(parts["shp"]),
                shx=io.BytesIO(parts["shx"]),
                dbf=io.BytesIO(parts["dbf"]),
                encoding="latin-1",
            )
            fields = [field[0] for field in reader.fields[1:]]
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if record is not None and shape is not None and shape.shapeType != shapefile.NULL and shape.points:
                    yield dict(zip(fields, list(record), strict=True)), json.dumps(shape.__geo_interface__), srid


def canon(text: object) -> str:
    spaced = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", str(text).upper())
    return " ".join(
        (token.lstrip("0") or "0") if token.isdigit() else token for token in re.findall(r"[A-Z0-9]+", spaced)
    )


def bare(text: str) -> str:
    return canon(re.sub(r"^\s*(PRECINCT|PCT)\b", "", text, flags=re.IGNORECASE))


def town(text: str) -> str:
    match = re.fullmatch(r"(.+)_ward(\w+)_precinct(\w+)", text)
    return f"{canon(match[1])} W{canon(match[2])} P{canon(match[3])}" if match else canon(text)


SHAPES: dict[str, Callable[[dict[str, Any]], tuple[str, str, str]]] = {
    "MN": lambda p: (f"27{p['countyfips']}", canon(p["pctname"]), p["pctname"]),
    "MI": lambda p: (f"26{p['COUNTYFIPS']}", canon(p["Precinct_Long_Name"]), p["Precinct_Long_Name"]),
    "MD": lambda p: (p["COUNTY"], canon(p["LABEL"]), f"Precinct {p['LABEL']}"),
    "DC": lambda p: ("11001", bare(p["NAME"]), p["NAME"]),
    "OK": lambda p: (f"40{p['CO_FIPS']}", canon(p["PCT_CEB"]), f"Precinct {p['PCT_CEB']}"),
    "RI": lambda p: ("", canon(p["DISTRICT"]), f"{p['TOWN']} {p['DISTRICT']}"),
    "AZ": lambda p: ("04013", canon(p["BdVal"]), f"{p['BdVal']} {p['BdName'].title()}"),
    "NC": lambda p: (p["county_nam"], canon(p["prec_id"]), p["enr_desc"].title()),
    "TX": lambda p: (f"48{int(p['CNTY']):03d}", canon(p["PREC"]), f"Precinct {p['PREC']}"),
    "WA": lambda p: (p["County"], canon(p["PrecName"]), p["PrecName"]),
    "LA": lambda p: (f"22{p['COUNTY']}", bare(p["NAME20"]), f"Precinct {p['NAME20']}"),
    "MA": lambda p: ("", town(f"{p['TOWN']}_ward{p['WARD'] or 0}_precinct{p['PRECINCT']}"), p["WP_NAME"]),
    "NM": lambda p: (f"35{p['COUNTY_FIP']}", canon(p["VTD_NUM"]), f"Precinct {p['VTD_NUM']}"),
    "WV": lambda p: (p["StCoFIPS"], canon(p["SHORTNAME"]), f"Precinct {p['SHORTNAME']}"),
    "NH": lambda p: ("", canon(re.sub(r"-\s*entire$", "", p["DISTRICT"])), re.sub(r"-\s*entire$", "", p["DISTRICT"])),
}
UNITS: dict[str, Callable[[str, str], str]] = {
    "DC": lambda _, precinct: bare(precinct),
    "RI": lambda _, precinct: canon(precinct.split()[-1]),
    "AZ": lambda _, precinct: canon(precinct.split()[0]),
    "TX": lambda _, precinct: canon(precinct[3:7]),
    "LA": lambda _, precinct: re.sub(r"^0 ", "", canon(precinct)),
    "MA": lambda _, precinct: town(precinct),
    "NM": lambda _, precinct: bare(precinct),
    "WV": lambda _, precinct: bare(precinct),
}
STATEWIDE = ("RI", "MA", "NH")
WORDS = {"SAINT": "ST", "TOWNSHIP": "TWP", "HEIGHTS": "HTS", "MOUNT": "MT", "FORT": "FT"}
FILLER = {"CITY", "OF", "CHARTER", "PRECINCT", "PCT", "PREC", "P", "WARD", "W", "TOWN", "VILLAGE"}


def loose(key: str) -> str:
    return " ".join(WORDS.get(word, word) for word in key.split() if word not in FILLER)


def unordered(key: str) -> str:
    words = loose(key).split()
    return " ".join(
        [*sorted(word for word in words if not word.isdigit()), *(word for word in words if word.isdigit())]
    )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    units = pl.read_parquet(DATA / "parsed" / "us.medsl_precincts" / "units.parquet").with_columns(
        jurisdiction=pl.col("code").str.split(":").list.get(3),
        precinct=pl.col("code").str.split(":").list.slice(4).list.join(":"),
    )
    report = []
    for state, shape in SHAPES.items():
        named = {
            jurisdiction: county
            for county, jurisdiction in units.filter(pl.col("state") == state)
            .select("county", "jurisdiction")
            .iter_rows()
        }
        rows = []
        for properties, geojson, srid in features(state):
            county, key, label = shape(properties)
            rows.append(
                (named.get(county, county) if state == "NC" else county, key, str(label).strip(), geojson, srid)
            )
        with connect() as conn:
            conn.execute(
                "create temp table shape (county text, key text, label text, geojson text, srid int) on commit drop"
            )
            with conn.cursor().copy("copy shape (county, key, label, geojson, srid) from stdin") as copy:
                for row in rows:
                    copy.write_row(row)
            conn.execute(
                """create temp table grouped on commit drop as
                   select county, key, min(label) as label, count(*) as parts,
                          st_multi(st_collectionextract(st_makevalid(st_simplifypreservetopology(st_union(
                            st_makevalid(st_transform(st_setsrid(st_geomfromgeojson(geojson), srid), 4326))), 0.00001)), 3)) as geom
                   from shape group by county, key"""
            )
            conn.execute(
                """create temp table county on commit drop as
                   select u.code, g.geom from reporting_unit u join area_geometry g on g.area_id = u.area_id
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{units.filter(pl.col('state') == state)['county'][0][:2]}%"],
            )
            conn.execute("create index on county using gist (geom)")
            shapes = pl.DataFrame(
                conn.execute(
                    """select g.county, g.key, g.label, g.parts,
                              (select c.code from county c order by c.geom <-> st_pointonsurface(g.geom) limit 1),
                              encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(st_pointonsurface(g.geom)), 'hex')
                       from grouped g where not st_isempty(g.geom)"""
                ).fetchall(),
                schema=["county", "key", "label", "parts", "within", "geom", "point"],
                orient="row",
            )
        unit = UNITS.get(state, lambda _, precinct: canon(precinct))
        mine = units.filter(pl.col("state") == state).with_columns(
            county=pl.lit("") if state in STATEWIDE else pl.col("county"),
            key=pl.struct("jurisdiction", "precinct").map_elements(
                lambda row, unit=unit: unit(row["jurisdiction"], row["precinct"]), return_dtype=pl.String
            ),
        )
        passes = [
            pl.col("key"),
            pl.col("key").map_elements(loose, return_dtype=pl.String),
            pl.col("key").map_elements(unordered, return_dtype=pl.String),
        ]
        shapes = shapes.with_columns(code=pl.lit(None, pl.String))
        for number, rule in enumerate(passes):
            left = mine.filter(~pl.col("code").is_in(shapes["code"].drop_nulls().to_list())).with_columns(match=rule)
            if state == "LA" and number == 1:
                left = left.with_columns(
                    match=pl.col("precinct").map_elements(
                        lambda text: canon(text.split(" ", 1)[-1]), return_dtype=pl.String
                    )
                )
            found = (
                shapes.filter(pl.col("code").is_null())
                .with_columns(match=passes[0] if state == "LA" and number == 1 else rule)
                .filter(pl.len().over("county", "match") == 1)
                .join(
                    left.filter(pl.len().over("county", "match") == 1).select("county", "match", "code"),
                    on=["county", "match"],
                )
                .select("key", "county", pl.col("code_right").alias("found"))
            )
            shapes = (
                shapes.join(found, on=["county", "key"], how="left")
                .with_columns(code=pl.coalesce("code", "found"))
                .drop("found")
            )
        matched = shapes
        report.append(
            (
                state,
                mine.height,
                shapes.height,
                matched["code"].is_not_null().sum(),
                shapes.filter(pl.col("parts") > 1).height,
            )
        )
        print(
            f"{state}: {report[-1][3]} of {mine.height} units matched to {shapes.height} shapes "
            f"({report[-1][4]} keys with several features)",
            flush=True,
        )
        matched.with_columns(
            state=pl.lit(state), county=pl.when(pl.col("county") == "").then("within").otherwise("county")
        ).write_parquet(PARSED / f"{state}_2022.parquet")
    pl.DataFrame(report, schema=["state", "units", "shapes", "matched", "merged"], orient="row").write_parquet(
        PARSED / "report_2022.parquet"
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official precinct shapes for 2022 from state and county election offices, joined to MEDSL's precincts",
            polity="US",
        )
        counties = {
            code: (area, text.rsplit(",", 1)[0])
            for code, area, text in conn.execute(
                """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
        cs.add(
            "boundary_set",
            scheme_code="us.precinct",
            vintage="2022",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
            as_of=DAY,
        )
        for state, (publisher, title, license, attribution) in PUBLISHERS.items():
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=LAYERS.get(state) or ZIPS[state],
                license=license,
                attribution=attribution,
            )
            for url in [LAYERS[state] + "?f=json", *pages(state)] if state in LAYERS else [ZIPS[state]]:
                cs.artifact(raw.cached(SOURCE, url), source)
            frame = (
                pl.read_parquet(PARSED / f"{state}_2022.parquet")
                .with_columns(county=pl.when(pl.col("county").is_in(list(counties))).then("county").otherwise("within"))
                .with_columns(code=pl.coalesce("code", pl.format("2022:{}:{}:#{}", pl.lit(state), "county", "key")))
            )
            ids = [str(cs.area("us.precinct", code)) for code in frame["code"]]
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.precinct"}))
            cs.frame(
                "area_code",
                pl.DataFrame({"area_id": ids, "code": frame["code"]}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_parent",
                pl.DataFrame(
                    {"area_id": ids, "parent_id": [str(counties[county][0]) for county in frame["county"]]}
                ).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_geometry",
                pl.DataFrame({"area_id": ids, "geom": frame["geom"], "label_point": frame["point"]}).with_columns(
                    valid_from=pl.lit(DAY),
                    valid_to=pl.lit(DAY + timedelta(days=1)),
                    quality=pl.lit("official"),
                    source_id=pl.lit(str(source)),
                ),
            )
            cs.frame(
                "name",
                pl.DataFrame(
                    {
                        "entity_id": ids,
                        "text": [
                            f"{f'Precinct {label}' if label.isdigit() else label.title() if label.isupper() else label}, "
                            f"{counties[county][1]}"
                            for label, county in frame.select("label", "county").iter_rows()
                        ],
                    }
                ).with_columns(lang=pl.lit("en"), kind=pl.lit("official"), is_preferred=pl.lit(True)),
            )
        return cs.write()
