import json
import re
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.district_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

LAYERS: list[dict[str, str]] = [
    {
        "publisher": "Cook County GIS",
        "title": "Cook County Commissioner District (effective 12/5/22)",
        "pattern": r"^17:17031:board-of-commissioners:district-",
        "layer": "https://gis12.cookcountyil.gov/traditional/rest/services/politicalBoundary/MapServer/9",
        "field": "DISTRICT_INT",
    },
    {
        "publisher": "Cook County GIS",
        "title": "Board of Review (effective 12/5/22)",
        "pattern": r"^17:17031:board-of-review:district-",
        "layer": "https://gis12.cookcountyil.gov/traditional/rest/services/politicalBoundary/MapServer/10",
        "field": "DISTRICT_INT",
    },
    {
        "publisher": "Westchester County GIS",
        "title": "Westchester County Legislative Districts",
        "pattern": r"^36:36119:(county-)?legislature:district-",
        "layer": "https://giswww.westchestergov.com/arcgis/rest/services/MappingWestchesterCounty/MapServer/80",
        "field": "DISTRICTID",
    },
    {
        "publisher": "Onondaga County",
        "title": "Onondaga County Legislative Districts",
        "pattern": r"^36:36067:(county-)?legislature:district-",
        "layer": "https://spatialags.vhb.com/arcgis/rest/services/29821_Syr_Onondaga/Onondaga_Service/MapServer/10",
        "field": "COUNTY_LEG",
    },
    {
        "publisher": "LOJIC",
        "title": "Louisville KY Metro Council Districts",
        "pattern": r"^21:21111:fiscal-court:district-",
        "layer": "https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataPolitical/MapServer/8",
        "field": "COUNDIST",
    },
    {
        "publisher": "Charleston County GIS",
        "title": "Charleston County Council Districts",
        "pattern": r"^45:45019:board-of-commissioners:district-",
        "layer": "https://gisccapps.charlestoncounty.org/arcgis/rest/services/CARTEGRAPH/CART_COUNCIL_DIST/MapServer/10",
        "field": "DISTRICT",
    },
    {
        "publisher": "Broome County GIS",
        "title": "Broome County Legislative Districts",
        "pattern": r"^36:36007:(county-)?legislature:district-",
        "layer": "https://gis.broomecountyny.gov/arcgis/rest/services/elections/br_elections/MapServer/3",
        "field": "LD",
    },
    {
        "publisher": "Prince George's County GIS",
        "title": "Prince George's County Councilmanic Districts",
        "pattern": r"^24:24033:(council|board-of-commissioners):district-",
        "layer": "https://gis.princegeorgescountymd.gov/arcgis/rest/services/SCF_NEW/MapServer/4",
        "field": "DISTRICTID",
    },
    {
        "publisher": "Wicomico County GIS",
        "title": "Wicomico County Councilmanic Districts",
        "pattern": r"^24:24045:(council|board-of-commissioners):district-",
        "layer": "https://gisapps.wicomicocounty.org/server/rest/services/Councilmanic2017a/MapServer/0",
        "field": "DistrictNo",
    },
    {
        "publisher": "Harford County GIS",
        "title": "Harford County Councilmanic Districts",
        "pattern": r"^24:24025:(council|board-of-commissioners|board-of-education):district-",
        "layer": "https://hcggis.harfordcountymd.gov/eg/rest/services/EnerGovWM/MapServer/42",
        "field": "DISTRICT",
    },
    {
        "publisher": "Greenville County GIS",
        "title": "Greenville County Council Districts",
        "pattern": r"^45:45045:board-of-commissioners:district-",
        "layer": "https://www.gcgis.org/arcgis3/rest/services/GCGIA/Greenville_Base/MapServer/48",
        "field": "DISTRICT",
    },
    {
        "publisher": "Winnebago County GIS",
        "title": "Winnebago County Board Districts",
        "pattern": r"^17:17201:(board-of-commissioners|county-board):district-",
        "layer": "https://maps.wingis.org/public/rest/services/WardsAndDistricts/MapServer/4",
        "field": "DIST_ID",
    },
    {
        "publisher": "Baltimore County GIS",
        "title": "Baltimore County Councilmanic Districts (Bill 22-22)",
        "pattern": r"^24:24005:(council|board-of-commissioners|board-of-education):district-",
        "layer": "https://cwgis.baltimorecountymd.gov/arcgis/rest/services/Highways/Highways/MapServer/108",
        "field": "COUNCILMANIC_DISTRICTS",
    },
    {
        "publisher": "City of Bakersfield GIS",
        "title": "Bakersfield City Council Wards",
        "pattern": r"^06:0603526:council:(district|area)-",
        "layer": "https://gis.bakersfieldcity.us/webmaps/rest/services/General/Boundary/MapServer/5",
        "field": "WARD_ID",
    },
    {
        "publisher": "City of Modesto GIS",
        "title": "Modesto Council Districts",
        "pattern": r"^06:0648354:council:(district|area)-",
        "layer": "https://services1.arcgis.com/KN76x1eyfvozZO4M/arcgis/rest/services/Council_District_2021/FeatureServer/9",
        "field": "DIST_TXT",
    },
    {
        "publisher": "City of Palm Springs GIS",
        "title": "Palm Springs Voting Districts",
        "pattern": r"^06:0655254:council:(district|area)-",
        "layer": "https://services.arcgis.com/f48yV21HSEYeCYMI/arcgis/rest/services/Palm_Springs_Voting_Districts_2022_(View)/FeatureServer/0",
        "field": "DISTRICT",
    },
    {
        "publisher": "City of Hemet GIS",
        "title": "Hemet Council Districts (December 2023)",
        "pattern": r"^06:0633182:council:(district|area)-",
        "layer": "https://services3.arcgis.com/czrgcV6qkPuBJCRH/arcgis/rest/services/Hemet_Council_Districts_December_2023/FeatureServer/0",
        "field": "DISTRICT",
    },
    {
        "publisher": "Pima County GIS",
        "title": "Pima County Justice Precincts",
        "pattern": r"^04:04019:(justice-of-the-peace|constable):",
        "layer": "https://gisdata.pima.gov/arcgis1/rest/services/GISOpenData/Boundaries/MapServer/12",
        "field": "JPDIST",
    },
    {
        "publisher": "Pinal County GIS",
        "title": "Pinal County Justice Court Precincts",
        "pattern": r"^04:04021:(justice-of-the-peace|constable):",
        "layer": "https://services1.arcgis.com/UpxtrwRYNaXVpkGe/arcgis/rest/services/PinalCountyJP_Boundaries/FeatureServer/0",
        "field": "Precinct",
    },
    {
        "publisher": "McHenry County GIS",
        "title": "McHenry County Board Districts",
        "pattern": r"^17:17111:(board-of-commissioners|county-board):district-",
        "layer": "https://services1.arcgis.com/6iYC5AXXYapRVNzl/arcgis/rest/services/McHenry_County_Board_Districts/FeatureServer/0",
        "field": "District",
    },
    {
        "publisher": "Carroll County GIS",
        "title": "Carroll County Commissioner Districts",
        "pattern": r"^24:24013:(board-of-commissioners|council):district-",
        "layer": "https://services.arcgis.com/Uf0DiYpD9NOFO5YH/arcgis/rest/services/CommissionerDistricts/FeatureServer/0",
        "field": "COMMDIST",
    },
    {
        "publisher": "Montgomery County Planning",
        "title": "Montgomery County Council Districts",
        "pattern": r"^24:24031:(council|board-of-commissioners):district-",
        "layer": "https://montgomeryplans.org/server/rest/services/Overlays/County_Council_Districts/FeatureServer/0",
        "field": "COUNCIL",
    },
    {
        "publisher": "DuPage County GIS",
        "title": "DuPage County Board Districts",
        "pattern": r"^17:17043:(board-of-commissioners|county-board):district-",
        "layer": "https://services.arcgis.com/neJvtQ4PXvnQ86MJ/arcgis/rest/services/County_Board_Dist_new/FeatureServer/0",
        "field": "District",
    },
    {
        "publisher": "Anne Arundel County GIS",
        "title": "Anne Arundel County Council Districts",
        "pattern": r"^24:24003:(council|board-of-commissioners):district-",
        "layer": "https://gis.aacounty.org/arcgis/rest/services/OpenData/Political_OpenData/MapServer/4",
        "field": "CNCL",
    },
    {
        "publisher": "Anderson County GIS",
        "title": "Anderson County Council Districts",
        "pattern": r"^45:45007:(council|board-of-commissioners):district-",
        "layer": "https://propertyviewer.andersoncountysc.org/arcgis/rest/services/Overlays/MapServer/3",
        "field": "OBJECTID",
    },
    {
        "publisher": "Charles County GIS",
        "title": "Charles County Commissioner Districts (2022)",
        "pattern": r"^24:24017:board-of-commissioners:district-",
        "layer": "https://services7.arcgis.com/3BMWkdyrt45RNCrq/arcgis/rest/services/CommDistricts_2022/FeatureServer/0",
        "field": "COMM_DIST",
    },
]


def url(layer: dict[str, str]) -> str:
    return f"{layer['layer']}/query?where=1%3D1&outFields={layer['field']}&outSR=4326&f=geojson"


def fetch() -> None:
    for layer in LAYERS:
        raw.fetch(SOURCE, url(layer), suffix=".json")


def parse() -> None:
    rows = []
    with connect() as conn:
        for layer in LAYERS:
            features = json.loads(raw.cached(SOURCE, url(layer)).path.read_bytes())["features"]
            shapes = {
                re.sub(r"^(district|ward)-", "", re.sub(r"[^a-z0-9]+", "-", str(f["properties"][layer["field"]]).lower()).strip("-")): f["geometry"]
                for f in features
                if f["geometry"] is not None
            }
            wanted = conn.execute(
                """select a.id::text, ac.code from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code = 'us.local_district' and ac.code ~ %s
                     and not exists (select 1 from area_geometry g where g.area_id = a.id)""",
                [layer["pattern"]],
            ).fetchall()
            matched = 0
            for area, code in wanted:
                geometry = shapes.get(re.split(r"(?:district|area|precinct)-", code, maxsplit=1)[1])
                if geometry is None:
                    print("no shape:", code)
                    continue
                matched += 1
                rows.append({"area": area, "geojson": json.dumps(geometry), "layer": layer["title"]})
            print(layer["title"], len(wanted), "wanted;", matched, "matched")
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "matches.json").write_text(json.dumps(rows))


def emit() -> Path:
    rows = json.loads((PARSED / "matches.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County district boundaries from open GIS layers for commissioner, legislature, and board districts",
            polity="US",
        )
        source_ids = {}
        for layer in LAYERS:
            source = cs.source(layer["publisher"], layer["title"])
            source_ids[layer["title"]] = source
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=layer["publisher"],
                    title=layer["title"],
                    url=layer["layer"],
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url(layer)), source)
        shapes = conn.execute(
            """select f.area, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex'), f.layer
               from unnest(%s::text[], %s::text[], %s::text[]) as f(area, geojson, layer)""",
            [[r["area"] for r in rows], [r["geojson"] for r in rows], [r["layer"] for r in rows]],
        ).fetchall()
        for area, geom, point, layer in shapes:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source_ids[layer],
            )
        return cs.write()
