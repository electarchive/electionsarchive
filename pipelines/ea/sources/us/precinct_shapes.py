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
VERSION = "2"
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
    "DC:1990": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/30/",
    "DC:2002": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/29/",
    "DC:2008": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/28/",
    "DC:2012": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/45/",
    "AZ:1998-1999": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(1998-1999)/FeatureServer/0/",
    "AZ:2000-2001": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2000-2001)/FeatureServer/0/",
    "AZ:2002-2003": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2002-2003)/FeatureServer/0/",
    "AZ:2004-2005": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2004-2005)/FeatureServer/0/",
    "AZ:2006-2007": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2006-2007)/FeatureServer/0/",
    "AZ:2008-2011": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2008-2011)/FeatureServer/0/",
    "AZ:2012-2017": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2012-2017)/FeatureServer/0/",
    "MN:2012": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2012_2020/FeatureServer/4/",
    "MN:2014": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2012_2020/FeatureServer/3/",
    "KY:snapshot": "https://services3.arcgis.com/ghsX9CKghMvyYjBU/arcgis/rest/services/Ky_SBE_VTD_Boundaries_WM/FeatureServer/0/",
    "AR:snapshot": "https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Boundaries/FeatureServer/11/",
    "AK:snapshot": "https://maps.commerce.alaska.gov/server/rest/services/Govt_Related/Govt_House_and_Senate_Districts/MapServer/4/",
    "UT:snapshot": "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/VistaBallotAreas/FeatureServer/0/",
    "VT:snapshot": "https://services1.arcgis.com/BkFxaEFNwHqX3tAw/ArcGIS/rest/services/FS_VCGI_VT_Municipal_Voting_Districts_SP_v1/FeatureServer/0/",
    "NJ:snapshot": "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/ArcGIS/rest/services/Election_Districts_for_New_Jersey/FeatureServer/0/",
    "ND:snapshot": "https://services1.arcgis.com/GOcSXpzwBHyk2nog/arcgis/rest/services/NDGISHUB_Voter_Precincts/FeatureServer/0/",
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
    "LA:1999": "https://redist.legis.la.gov/2011_Files/Shapefiles/1999%20LA%20Precincts.zip",
    "LA:2000": "https://redist.legis.la.gov/2011_Files/Shapefiles/2000%20Census%20LA%20Precincts.zip",
    "LA:2003-03": "https://redist.legis.la.gov/2011_Files/Shapefiles/2003-3%20LA%20Precincts.zip",
    "LA:2003-11": "https://redist.legis.la.gov/2011_Files/Shapefiles/2003-11%20LA%20Precincts.zip",
    "LA:2004": "https://redist.legis.la.gov/2011_Files/Shapefiles/2004-12%20LA%20Precincts.zip",
    "LA:2005": "https://redist.legis.la.gov/2011_Files/Shapefiles/2005-12%20LA%20Precincts.zip",
    "LA:2006": "https://redist.legis.la.gov/2011_Files/Shapefiles/2006-12%20LA%20Precincts.zip",
    "LA:2007": "https://redist.legis.la.gov/2011_Files/Shapefiles/Louisiana%20Precinct%20Shapefile%20(2007).zip",
    "LA:2008": "https://redist.legis.la.gov/2011_Files/Shapefiles/Precinct%20updates2008.zip",
    "LA:2009": "https://redist.legis.la.gov/2011_Files/Shapefiles/Precinct%20updates2009.zip",
    "LA:2010": "https://redist.legis.la.gov/2011_Files/Shapefiles/Precinct%20updates2010_11.zip",
    "LA:2011": "https://redist.legis.la.gov/2011_Files/Shapefiles/2011%20Louisiana%20Precinct%20Shapefile.zip",
    "LA:2012": "https://redist.legis.la.gov/2011_Files/Shapefiles/2012%20Louisiana%20Precinct%20Shapefile.zip",
    "LA:2013": "https://redist.legis.la.gov/2011_Files/Shapefiles/2013%20Louisiana%20Precinct%20Shapefile.zip",
    "LA:2014": "https://redist.legis.la.gov/2011_Files/Shapefiles/2014_LouisianaPrecinctShapefile.ZIP",
    "LA:2015": "https://redist.legis.la.gov/2011_Files/Shapefiles/2015_LouisianaPrecinctShapefile.zip",
    "WA:2004": "https://www.sos.wa.gov/sites/default/files/2022-05/2004.zip",
    "WA:2005": "https://www.sos.wa.gov/sites/default/files/2022-05/2005.zip",
    "WA:2006": "https://www.sos.wa.gov/sites/default/files/2022-05/2006.zip",
    "WA:2007": "https://www.sos.wa.gov/sites/default/files/2022-05/2007.zip",
    "WA:2008": "https://www.sos.wa.gov/sites/default/files/2022-05/2008.zip",
    "WA:2009": "https://www.sos.wa.gov/sites/default/files/2022-05/2009.zip",
    "WA:2010": "https://www.sos.wa.gov/sites/default/files/2022-05/2010.zip",
    "WA:2012": "https://www.sos.wa.gov/sites/default/files/2022-05/2012.zip",
    "WA:2013": "https://www.sos.wa.gov/sites/default/files/2022-05/Statewide_Prec_2013.zip",
    "WA:2014": "https://www.sos.wa.gov/sites/default/files/2022-05/Statewide_Prec_2014.zip",
    "MN:1996": "https://www.gis.lcc.mn.gov/data/shape/vtd96.zip",
    "MN:1998": "https://www.gis.lcc.mn.gov/data/shape/elec98.zip",
    "MN:2000": "https://www.gis.lcc.mn.gov/data/shape/elec00.zip",
    "MN:2002": "https://www.gis.lcc.mn.gov/data/shape/elec02.zip",
    "MN:2004": "https://www.gis.lcc.mn.gov/data/shape/elec04.zip",
    "MN:2006": "https://www.gis.lcc.mn.gov/data/shape/elec06.zip",
    "MN:2008": "https://www.gis.lcc.mn.gov/data/shape/elec08.zip",
    "MN:2010": "https://www.gis.lcc.mn.gov/data/shape/elec2010.zip",
    "TX:2010": "https://data.capitol.texas.gov/dataset/6381bd0b-3ad4-4556-bb3f-6c579a60d00a/resource/8aa724af-f8ed-4f7d-abd6-ea1642e0d2cc/download/2010general.zip",
    "TX:2012": "https://data.capitol.texas.gov/dataset/6381bd0b-3ad4-4556-bb3f-6c579a60d00a/resource/ab222bdb-37b9-4f78-9071-b15f9c5828cf/download/2012general.zip",
    "TX:2014": "https://data.capitol.texas.gov/dataset/6381bd0b-3ad4-4556-bb3f-6c579a60d00a/resource/b983204f-37d6-47e3-830e-e772cc465900/download/2014general.zip",
    "NC:2006": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/Precincts2006Gen.zip",
    "NC:2012": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20120901.zip",
    "NC:2014": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20141016.zip",
    "WV:2002": "https://data.wvgis.wvu.edu/pub/Clearinghouse/Boundaries/votingDistrictsWV_legislativeServices_2002_utm83_shp.zip",
    "SC:snapshot": "https://rfa.sc.gov/media/10668",
}
UNPROJECTED = {"WA:2006": 4269, "MN:1996": 26915, "MN:2002": 26915}
GEOJSONS = {
    "NM:2006": "https://gstore.unm.edu/apps/rgisarchive/datasets/edf218ed-22cc-4e1e-ad3e-255626bea630/nmprecincts_08.derived.geojson",
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
    for url in GEOJSONS.values():
        raw.fetch(SOURCE, url, suffix=".geojson")


def features(state: str) -> Iterator[tuple[dict[str, object], str, int]]:
    if state in LAYERS:
        for url in pages(state):
            for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
                if feature["geometry"] is not None:
                    yield feature["properties"], json.dumps(feature["geometry"]), 4326
        return
    path = raw.cached(SOURCE, GEOJSONS.get(state) or ZIPS[state]).path
    with zipfile.ZipFile(path) as archive:
        names = {name.lower(): name for name in archive.namelist() if not name.startswith("__MACOSX")}
        for name in sorted(name for name in names if name.endswith(".geojson")):
            for feature in json.loads(archive.read(names[name]))["features"]:
                if feature["geometry"] is not None:
                    yield feature["properties"], json.dumps(feature["geometry"]), 4326
        for stem in sorted(name[:-4] for name in names if name.endswith(".shp")):
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(names[f"{stem}.shp"])),
                shx=io.BytesIO(archive.read(names[f"{stem}.shx"])),
                dbf=io.BytesIO(archive.read(names[f"{stem}.dbf"])),
                encoding="latin-1",
            )
            crs = CRS(archive.read(names[f"{stem}.prj"]).decode("latin-1")) if f"{stem}.prj" in names else None
            srid = crs.to_epsg(min_confidence=20) if crs is not None else UNPROJECTED[state]
            assert srid is not None, f"{state}: {stem}"
            fields = [field[0] for field in reader.fields[1:]]
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if record is None or shape is None or shape.shapeType == shapefile.NULL or not shape.points:
                    continue
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
FIPS = {
    "AK": "02",
    "AR": "05",
    "AZ": "04",
    "DC": "11",
    "KY": "21",
    "LA": "22",
    "MA": "25",
    "MD": "24",
    "MI": "26",
    "MN": "27",
    "NC": "37",
    "ND": "38",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "OK": "40",
    "RI": "44",
    "SC": "45",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "WA": "53",
    "WV": "54",
}
GENERALS = [date(year, 11, 2 + (7 - date(year, 11, 1).weekday()) % 7) for year in range(1990, 2015, 2)]


def day(year: int) -> tuple[date, date]:
    general = next(general for general in GENERALS if general.year == year)
    return general, general + timedelta(days=1)


def years(first: int, last: int) -> tuple[date, date]:
    return date(first, 1, 1), date(min(last + 1, 2016), 1, 1)


def maricopa(p: dict[str, Any]) -> tuple[str, str, str]:
    number = int(p.get("BdVal") or p.get("BDVAL") or p.get("PctNum") or p.get("PCT_NUM") or 0)
    name = str(p.get("BdName") or p.get("PctName") or p.get("PCT_NAME") or "").title()
    return "04013", str(number), f"{number:04d} {name}".strip()


def lcc(p: dict[str, Any]) -> tuple[str, str, str]:
    if "DISTRICT" in p:
        label = next(p[field] for field in ("Precinct", "PRECINCT", "PRECINCTNA", "NAME_1") if field in p)
        return p["DISTRICT"][:5], canon(p["DISTRICT"][5:]), label
    if "CTYVTDCODE" in p:
        return f"27{p['CTYVTDCODE'][:3]}", canon(p["VTD"]), p["VTDNAME"]
    return f"27{p['COUNTY']}", canon(p["VTDCODE"][3:]), f"Precinct {p['VTD']}"


def census(p: dict[str, Any]) -> tuple[str, str, str]:
    geoid = next(field for field in p if field.startswith("GEOID"))
    suffix = geoid[5:]
    return p[geoid][:5], canon(p[f"VTDST{suffix}"]), p[f"NAME{suffix}"]


DC = ("District of Columbia Board of Elections", "CC BY 4.0", "DC GIS")
LCC = ("Minnesota Legislative Coordinating Commission, GIS Office", "Public record", None)
HISTORY: dict[str, tuple[date, date, str, str, str, str | None, Callable[[dict[str, Any]], tuple[str, str, str]]]] = {
    **{
        f"DC:{year}": (
            *years(int(year), end),
            DC[0],
            f"Voting Precinct - {year}",
            DC[1],
            DC[2],
            lambda p: ("11001", bare(p["NAME"]), p["NAME"]),
        )
        for year, end in (("1990", 2001), ("2002", 2007), ("2008", 2011), ("2012", 2018))
    },
    **{
        f"AZ:{era}": (
            *years(int(era[:4]), int(era[5:])),
            "Maricopa County Recorder",
            f"Maricopa County Voting Precincts ({era})",
            "Public record",
            None,
            maricopa,
        )
        for era in ("1998-1999", "2000-2001", "2002-2003", "2004-2005", "2006-2007", "2008-2011", "2012-2017")
    },
    **{
        f"MN:{year}": (
            *day(year),
            LCC[0],
            "1996 Precincts" if year == 1996 else f"{year} Election Results by Precinct",
            LCC[1],
            LCC[2],
            lcc,
        )
        for year in (1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010)
    },
    **{
        f"MN:{year}": (
            *day(year),
            "Minnesota Secretary of State",
            f"General Election Results By Precinct {year}",
            "Public record",
            None,
            lambda p: (f"27{p['countyfips']}", canon(p["pctname"]), p["pctname"]),
        )
        for year in (2012, 2014)
    },
    **{
        f"LA:{vintage}": (
            *(
                (date(2003, 1, 1), date(2003, 11, 1))
                if vintage == "2003-03"
                else (date(2003, 11, 1), date(2004, 1, 1))
                if vintage == "2003-11"
                else years(int(vintage), int(vintage))
            ),
            "Louisiana Legislature",
            f"Louisiana Precinct Shapefile ({vintage})",
            "Public record",
            None,
            lambda p: (f"22{p['COUNTY']}", bare(p["VTD"]), p["UNIT_NAME"]),
        )
        for vintage in (
            "1999",
            "2000",
            "2003-03",
            "2003-11",
            *(str(year) for year in range(2004, 2016)),
        )
    },
    **{
        f"WA:{year}": (
            *years(year, year),
            "Washington Secretary of State",
            f"Statewide Precincts {year}",
            "Public record",
            None,
            census
            if year <= 2010
            else (lambda p: ("", f"{p['COUNTYCODE']} {canon(p['PRECCODE'])}", p["PRECNAME"]))
            if year == 2014
            else (lambda p: ("", f"{p['CountyCode']} {canon(p['Code'])}", p["Name"])),
        )
        for year in (2004, 2005, 2006, 2007, 2008, 2009, 2010, 2012, 2013, 2014)
    },
    **{
        f"TX:{year}": (
            *day(year),
            "Texas Legislative Council",
            f"Precincts, {year} General Election",
            "CC BY",
            None,
            lambda p: (f"48{int(p['CNTY']):03d}", canon(p["PREC"]), f"Precinct {p['PREC']}"),
        )
        for year in (2010, 2012, 2014)
    },
    "NC:2006": (
        *day(2006),
        "North Carolina State Board of Elections",
        "Precincts, 2006 General Election",
        "Public record",
        None,
        lambda p: (p["County"], canon(p["PRECINCT_I"]), p["Precinct"]),
    ),
    **{
        f"NC:{year}": (
            *day(year),
            "North Carolina State Board of Elections",
            f"Precinct Shapefile, {stamp}",
            "Public record",
            None,
            lambda p: (p["COUNTY_NAM"], canon(p["PREC_ID"]), p["ENR_DESC"].split("_", 1)[-1]),
        )
        for year, stamp in ((2012, "2012-09-01"), (2014, "2014-10-16"))
    },
    "WV:2002": (
        *years(2002, 2002),
        "West Virginia GIS Technical Center",
        "Voting Districts, West Virginia Legislative Services, 2002",
        "Free to copy and distribute (MapWV terms of use)",
        None,
        lambda p: (p["COUNTY"], canon(p["NAME"]), f"Precinct {p['NAME']}"),
    ),
    "NM:2006": (
        date(2006, 7, 1),
        date(2012, 1, 1),
        "New Mexico Secretary of State",
        "Voting Precinct Boundaries as of July 2006",
        "Public domain",
        "RGIS",
        lambda p: (f"35{p['COUNTY']}", canon(p["VTD"]), p["NAME"].rstrip("*").strip()),
    ),
}
SNAPSHOTS: dict[
    str, tuple[date | None, date | None, str, str, str, str | None, Callable[[dict[str, Any]], tuple[str, str, str]]]
] = {
    "KY:snapshot": (
        None,
        None,
        "Kentucky State Board of Elections",
        "Voting Precinct Boundaries in Kentucky",
        "CC0 1.0",
        "Kentucky Division of Geographic Information",
        lambda p: (p["StCoFIPS_str"], canon(p["PrecinctCode"]), p["PrecinctName"]),
    ),
    "AR:snapshot": (
        None,
        None,
        "Arkansas Secretary of State",
        "Election Precincts",
        "Public record",
        "Arkansas GIS Office",
        lambda p: (f"05{p['county_fips']}", canon(p["precinct"]), p["precinct"]),
    ),
    "AK:snapshot": (
        None,
        None,
        "Alaska Division of Elections",
        "Alaska Election Precincts",
        "Public record",
        "Alaska Division of Community and Regional Affairs",
        lambda p: ("", p["Precinct_Name"].split()[0], p["Precinct_Name"]),
    ),
    "UT:snapshot": (
        None,
        None,
        "Utah Geospatial Resource Center",
        "Utah Vista Ballot Areas",
        "CC BY 4.0",
        "Utah AGRC, Utah Counties, Lt. Governor's Elections Office",
        lambda p: (f"49{2 * int(p['CountyID']) - 1:03d}", canon(p["PrecinctID"]), p["PrecinctID"]),
    ),
    "VT:snapshot": (
        None,
        None,
        "Vermont Center for Geographic Information",
        "Vermont Municipal Voting Districts",
        "Public record",
        None,
        lambda p: (
            "",
            f"{p['TOWNGEOID']} {canon(p['WARD'])}" if p["WARD"] else p["TOWNGEOID"],
            p["WARDUNI"] or p["TOWNREG"],
        ),
    ),
    "NJ:snapshot": (
        date(2021, 11, 24),
        date(2021, 11, 25),
        "New Jersey Department of State, Division of Elections",
        "Election Districts for New Jersey",
        "Public record",
        "NJGIN",
        lambda p: (
            p["COUNTY"],
            p["ELECD_KEY"],
            " ".join(
                [
                    p["MUN_NAME"],
                    *([f"Ward {p['WARD_CODE'].lstrip('0')}"] if p["WARD_CODE"] not in (None, "00") else []),
                    *([f"District {p['ELECD_CODE'].lstrip('0') or '0'}"] if p["ELECD_CODE"] else []),
                ]
            ),
        ),
    ),
    "ND:snapshot": (
        None,
        None,
        "North Dakota Secretary of State",
        "NDGISHUB Voter Precincts",
        "Public record",
        "NDGIS Hub",
        lambda p: (str(p["CountyID"]), p["Precinct"], f"Precinct {p['Precinct']}"),
    ),
    "SC:snapshot": (
        None,
        None,
        "South Carolina Revenue and Fiscal Affairs Office",
        "Statewide Precinct Shapefile",
        "Public record",
        None,
        lambda p: (f"45{int(p['FIPS']):03d}", canon(p["PCode"]), p["PName"]),
    ),
}
WORDS = {"SAINT": "ST", "TOWNSHIP": "TWP", "HEIGHTS": "HTS", "MOUNT": "MT", "FORT": "FT"}
FILLER = {"CITY", "OF", "CHARTER", "PRECINCT", "PCT", "PREC", "P", "WARD", "W", "TOWN", "VILLAGE"}


def loose(key: str) -> str:
    return " ".join(WORDS.get(word, word) for word in key.split() if word not in FILLER)


def unordered(key: str) -> str:
    words = loose(key).split()
    return " ".join(
        [*sorted(word for word in words if not word.isdigit()), *(word for word in words if word.isdigit())]
    )


def group(rows: list[tuple[str, str, str, str, int]], prefix: str) -> pl.DataFrame:
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
            [f"{prefix}%"],
        )
        conn.execute("create index on county using gist (geom)")
        return pl.DataFrame(
            conn.execute(
                """select g.county, g.key, g.label, g.parts,
                          (select c.code from county c order by c.geom <-> st_pointonsurface(g.geom) limit 1),
                          encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(st_pointonsurface(g.geom)), 'hex')
                   from grouped g where not st_isempty(g.geom)"""
            ).fetchall(),
            schema=["county", "key", "label", "parts", "within", "geom", "point"],
            orient="row",
        )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        named = {
            (code[:2], text.upper()): code
            for code, text in conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'short' where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
    for vintage, (_, _, _, _, _, _, rule) in {**HISTORY, **SNAPSHOTS}.items():
        state = vintage.split(":")[0]
        rows = []
        for properties, geojson, srid in features(vintage):
            county, key, label = rule(properties)
            rows.append((named.get((FIPS[state], county.upper()), county), key, str(label).strip(), geojson, srid))
        shapes = group(rows, FIPS[state]).with_columns(
            county=pl.when(pl.col("county") == "").then("within").otherwise("county")
        )
        print(f"{vintage}: {len(rows)} features, {shapes.height} precincts", flush=True)
        shapes.write_parquet(PARSED / f"{vintage.replace(':', '_')}.parquet")

    units = pl.read_parquet(DATA / "parsed" / "us.medsl_precincts" / "units.parquet").with_columns(
        jurisdiction=pl.col("code").str.split(":").list.get(3),
        precinct=pl.col("code").str.split(":").list.slice(4).list.join(":"),
    )
    report = []
    for state, shape in SHAPES.items():
        rows = []
        for properties, geojson, srid in features(state):
            county, key, label = shape(properties)
            rows.append((named.get((FIPS[state], county.upper()), county), key, str(label).strip(), geojson, srid))
        shapes = group(rows, FIPS[state])
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
            summary="Official precinct shapes from state and county election offices, 1990–2015 and 2022, "
            "with 2022's joined to MEDSL's precincts",
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
        retrieved = {
            vintage: raw.cached(
                SOURCE, LAYERS[vintage] + "?f=json" if vintage in LAYERS else ZIPS[vintage]
            ).retrieved_at.date()
            for vintage, entry in SNAPSHOTS.items()
            if entry[0] is None
        }
        for general in [*GENERALS, DAY, *sorted(set(retrieved.values()))]:
            cs.add(
                "boundary_set",
                scheme_code="us.precinct",
                vintage=str(general) if general in retrieved.values() else str(general.year),
                valid_from=general,
                valid_to=general + timedelta(days=1),
                as_of=general,
            )
        vintages = {
            **{state: (DAY, DAY + timedelta(days=1), *PUBLISHERS[state]) for state in SHAPES},
            **{vintage: entry[:6] for vintage, entry in HISTORY.items()},
            **{
                vintage: (
                    start or retrieved[vintage],
                    end or retrieved[vintage] + timedelta(days=1),
                    publisher,
                    title,
                    license,
                    attribution,
                )
                for vintage, (start, end, publisher, title, license, attribution, _) in SNAPSHOTS.items()
            },
        }
        for vintage, (start, end, publisher, title, license, attribution) in vintages.items():
            state = vintage.split(":")[0]
            source = cs.source(publisher, title)
            url = LAYERS.get(vintage) or ZIPS.get(vintage) or GEOJSONS[vintage]
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=license,
                attribution=attribution,
            )
            for cached in [url + "?f=json", *pages(vintage)] if vintage in LAYERS else [url]:
                cs.artifact(raw.cached(SOURCE, cached), source)
            frame = (
                pl.read_parquet(PARSED / f"{vintage.replace(':', '_')}{'' if ':' in vintage else '_2022'}.parquet")
                .filter(pl.col("key") != "", ~pl.col("county").str.contains(r"^\d{2}(000)?$"))
                .with_columns(
                    known=pl.col("county").is_in(list(counties)),
                    label=pl.when(pl.col("label") == "").then("key").otherwise("label"),
                )
                .sort("known", descending=True, maintain_order=True)
                .with_columns(county=pl.when("known").then("county").otherwise("within"))
                .with_columns(
                    code=pl.coalesce("code", pl.format("2022:{}:{}:#{}", pl.lit(state), "county", "key"))
                    if ":" not in vintage
                    else pl.format("{}:{}:{}:{}", pl.lit(str(start)), pl.lit(state), "county", "key")
                )
                .unique("code", keep="first", maintain_order=True)
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
                    valid_from=pl.lit(start),
                    valid_to=pl.lit(end),
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
            print(f"{vintage}: {frame.height} precincts, {start} to {end}")
        return cs.write()
