import json
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.gis_districts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
QUERY = "/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"

SOURCES: list[dict[str, str | list[str]]] = [
    {
        "publisher": "Lexington-Fayette Urban County Government",
        "title": "Council District",
        "url": "https://services1.arcgis.com/Mg7DLdfYcSWIaDnu/ArcGIS/rest/services/Council_District/FeatureServer/0",
        "field": "DISTRICT",
        "codes": ["21:21067:fiscal-court:district-{v}"],
    },
    {
        "publisher": "Lexington-Fayette Urban County Government",
        "title": "Magisterial District",
        "url": "https://services1.arcgis.com/Mg7DLdfYcSWIaDnu/ArcGIS/rest/services/Magisterial_District/FeatureServer/0",
        "field": "MAGISTERIAL",
        "codes": ["21:21067:magistrate:district-{v}", "21:21067:constable:district-{v}"],
    },
    {
        "publisher": "Lexington-Fayette Urban County Government",
        "title": "School Board District",
        "url": "https://services1.arcgis.com/Mg7DLdfYcSWIaDnu/ArcGIS/rest/services/School_Board_District/FeatureServer/0",
        "field": "SCHOOL",
        "codes": [
            "21:21067:board-of-education:district-{v}",
            "21:21067:board-of-education:educational-district-{v}",
            "21:21067:board-of-education:educational-division-{v}",
            "21:21067:county-board:of-education-educational-division-{v}",
        ],
    },
    {
        "publisher": "Louisville/Jefferson County Information Consortium",
        "title": "Metro Council Districts",
        "url": "https://gis.lojic.org/maps/rest/services/LojicApps/PoliticalDistricts/MapServer/2",
        "field": "COUNDIST",
        "codes": ["21:2148000:council:district-{v}"],
    },
    {
        "publisher": "Hennepin County GIS",
        "title": "Commissioner Districts",
        "url": "https://gis.hennepin.us/arcgis/rest/services/HennepinData/BOUNDARIES/MapServer/0",
        "field": "NAME_TXT",
        "codes": ["27:27053:board-of-commissioners:district-{v}", "27:27053:commission:district-{v}"],
    },
    {
        "publisher": "Ramsey County Enterprise GIS",
        "title": "Commissioner Districts 2022",
        "url": "https://gis.ramseycountymn.gov/server/rest/services/Boundary/BOUND_CommissionerDistrict2022_ViewOnly/MapServer/25",
        "field": "District",
        "codes": ["27:27123:board-of-commissioners:district-{v}", "27:27123:commission:district-{v}"],
    },
    {
        "publisher": "Ramsey County Enterprise GIS",
        "title": "Saint Paul Wards 2022",
        "url": "https://gis.ramseycountymn.gov/server/rest/services/Boundary/BOUND_SaintPaulWard2022_ViewOnly/MapServer/16",
        "field": "DISTRICT",
        "codes": ["27:2758000:council:district-{v}"],
    },
    {
        "publisher": "East Baton Rouge Parish Department of Information Services",
        "title": "Council District",
        "url": "https://maps.brla.gov/gis/rest/services/Governmental_Units/Council_District/MapServer/0",
        "field": "DISTRICT_NUM",
        "codes": ["22:22033:council:district-{v}", "22:22033:parish-council:district-{v}"],
    },
]


def fetch() -> None:
    for source in SOURCES:
        raw.fetch(SOURCE, f"{source['url']}{QUERY}", suffix=".json")


def parse() -> None:
    with connect() as conn:
        wanted = {
            code: area
            for area, code in conn.execute(
                """select a.id::text, ac.code from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code in ('us.local_district', 'us.special_district')
                     and ac.code ~ '^(21|22|27):'
                     and not exists (select 1 from area_geometry g where g.area_id = a.id)"""
            )
        }
    rows = []
    misses = []
    for source in SOURCES:
        url = f"{source['url']}{QUERY}"
        features = json.loads(raw.cached(SOURCE, url).path.read_bytes())["features"]
        values = {re.sub(r"\.0$", "", str(f["properties"][str(source["field"])])): f for f in features}
        for pattern in source["codes"]:
            prefix = pattern.split("{v}")[0]
            for code, area in wanted.items():
                if not code.startswith(prefix):
                    continue
                value = code.removeprefix(prefix)
                feature = values.get(value)
                if feature is None:
                    misses.append((code, str(source["title"])))
                    continue
                rows.append(
                    (area, code, str(source["publisher"]), str(source["title"]), url, json.dumps(feature["geometry"]))
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["area", "code", "publisher", "title", "url", "geojson"], orient="row").write_parquet(
        PARSED / "matches.parquet"
    )
    pl.DataFrame(misses, schema=["code", "layer"], orient="row").write_csv(PARSED / "unmatched.tsv", separator="\t")
    print(len(rows), "areas matched;", len(misses), "wanted areas missing from their layer:")
    for code, layer in misses:
        print("  miss", code, layer)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County, parish, and city GIS boundaries for election districts the archive holds without a shape",
            polity="US",
        )
        sources = {}
        for source in SOURCES:
            url = f"{source['url']}{QUERY}"
            sources[url] = cs.source(str(source["publisher"]), str(source["title"]))
            if sources[url] in cs.minted:
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=source["publisher"],
                    title=source["title"],
                    url=source["url"],
                )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        shapes = conn.execute(
            """select f.area, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex')
               from unnest(%s::text[], %s::text[]) as f(area, geojson)""",
            [frame["area"].to_list(), frame["geojson"].to_list()],
        ).fetchall()
        for (area, geom, point), url in zip(shapes, frame["url"].to_list(), strict=True):
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[url],
            )
        return cs.write()
