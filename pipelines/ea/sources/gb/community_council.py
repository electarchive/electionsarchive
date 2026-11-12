import html
import io
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.parish import slug
from ea.store import DATA, connect

SOURCE = "gb.community_council"
SCHEME = "gb.community_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HUB = "https://data.spatialhub.scot/dataset"
QUERY = "query?where=1%3D1&outFields=*&outSR=27700&f=geojson"
TOLERANCE = 10
RETRIEVED = date(2026, 11, 12)
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
COUNCILS = {
    "S12000005": (
        "cl",
        "Clackmannanshire Council",
        (
            "https://clacks.cloud.cadcorp.com/WFS91/wfs.exe?SERVICE=WFS&VERSION=1.0.0&REQUEST=GetFeature"
            "&typeName=ns:Community_Councils"
        ),
        "Name",
        27700,
    ),
    "S12000006": (
        "dg",
        "Dumfries and Galloway Council",
        (
            f"{HUB}/6e7f0bfa-8c5d-4c0a-86e0-33122d74ea01/resource/2c56dc1d-f0f4-41bb-a1f5-310dc6df435a/download/"
            "dg_community_councils12843921_polygon.zip"
        ),
        "COMMUNITY_",
        27700,
    ),
    "S12000008": (
        "ea",
        "East Ayrshire Council",
        f"{HUB}/d6301087-4ebb-46d2-ba97-c4c89634a7e0/resource/89ab6574-6f06-4ff6-b967-5409348a315c/download/commc.zip",
        "NAME",
        27700,
    ),
    "S12000010": (
        "el",
        "East Lothian Council",
        (
            f"{HUB}/3261b95d-8170-42d9-b956-b5e2a55aca73/resource/6cec2847-5eb9-433b-9330-7325fd0b518d/download/"
            "community-council-boundaries.zip"
        ),
        "Name",
        27700,
    ),
    "S12000011": (
        "er",
        "East Renfrewshire Council",
        (
            f"{HUB}/0c6f13e2-e5ba-4792-a11d-de940ae6f359/resource/dda8ea95-62f3-40cb-9626-70822d397440/download/"
            "er-community-councils.zip"
        ),
        "NAME",
        27700,
    ),
    "S12000013": (
        "es",
        "Comhairle nan Eilean Siar",
        (
            f"{HUB}/95fa330f-8e54-4cc6-a814-4a48a8a97861/resource/3675c98c-c1e8-4018-b7f9-55143061d698/download/"
            "community-councils-2019.zip"
        ),
        "Community_",
        27700,
    ),
    "S12000014": (
        "fa",
        "Falkirk Council",
        (
            "https://services-eu1.arcgis.com/ejHeInmA8D8R9Vok/arcgis/rest/services/Community%20Council%20Boundaries/"
            f"FeatureServer/0/{QUERY}"
        ),
        "community_",
        27700,
    ),
    "S12000017": (
        "hi",
        "The Highland Council",
        (
            "https://services-eu1.arcgis.com/tc4hh050NPZgWq0B/arcgis/rest/services/Community_Councils_Open_Data/"
            f"FeatureServer/0/{QUERY}"
        ),
        "NAME",
        27700,
    ),
    "S12000018": (
        "in",
        "Inverclyde Council",
        (
            f"{HUB}/54c176fc-9c7b-44ac-80a7-a940ba279123/resource/7757b2c1-92ff-4e84-b36f-473c146412c5/download/"
            "inverclyde_community_councils.zip"
        ),
        "area_name",
        27700,
    ),
    "S12000019": (
        "ml",
        "Midlothian Council",
        (
            f"{HUB}/2024392b-14f5-4a09-aab4-0fa976ede3f2/resource/7fbfe97c-55a5-449b-bd65-00a009ef7850/download/"
            "commcouncil.zip"
        ),
        "Name",
        27700,
    ),
    "S12000020": (
        "mo",
        "The Moray Council",
        f"https://mapping.moray.gov.uk/arcgis/rest/services/OpenData/OpenData/MapServer/2/{QUERY}",
        "CC_AREA",
        27700,
    ),
    "S12000021": (
        "na",
        "North Ayrshire Council",
        f"https://www.maps.north-ayrshire.gov.uk/arcgis/rest/services/AGOL/Spatial_Hub/FeatureServer/40/{QUERY}",
        "CC_NAME",
        27700,
    ),
    "S12000023": (
        "oi",
        "Orkney Islands Council",
        (
            f"{HUB}/c967b41a-b836-4a47-b2ca-fe5aa3488283/resource/e032e46c-50b8-4f18-8697-9f9a05eacf8f/download/"
            "community-councils.zip"
        ),
        "community",
        27700,
    ),
    "S12000026": (
        "sb",
        "Scottish Borders Council",
        (
            f"{HUB}/a86aea80-d5d1-41c8-8f5d-87c61e8ff321/resource/07b35399-bd41-4871-b252-28dca2930fb5/download/"
            "bound_community_councils.zip"
        ),
        "NAME",
        27700,
    ),
    "S12000027": (
        "si",
        "Shetland Islands Council",
        (
            f"{HUB}/ea788464-0b57-4e06-bc66-5a0f21af158d/resource/94365e3d-54d8-427b-872d-f5d2f2a86116/download/"
            "communitycouncils2019_region.communityc.zip"
        ),
        "CommunityC",
        27700,
    ),
    "S12000028": (
        "sa",
        "South Ayrshire Council",
        f"https://gisext.south-ayrshire.gov.uk/server/rest/services/Inspire/OpenData/MapServer/23/{QUERY}",
        "community",
        27700,
    ),
    "S12000029": (
        "sl",
        "South Lanarkshire Council",
        (
            f"{HUB}/a1910f23-09e8-417f-bc7e-7203c025be05/resource/58bdf401-40a8-46a4-9c87-90ead78b6859/download/"
            "sl_cc_region.shp.zip"
        ),
        "CC_NAME",
        27700,
    ),
    "S12000030": (
        "st",
        "Stirling Council",
        (
            "https://services-eu1.arcgis.com/cECIr59LclpO818r/ArcGIS/rest/services/stirling_council_community_councils/"
            f"FeatureServer/4/{QUERY}"
        ),
        "Name",
        27700,
    ),
    "S12000033": (
        "ac",
        "Aberdeen City Council",
        (
            "https://services5.arcgis.com/0sktPVp3t1LvXc9z/ArcGIS/rest/services/Community_Councils_2024/FeatureServer/74/"
            f"{QUERY}"
        ),
        "NAME",
        27700,
    ),
    "S12000034": (
        "as",
        "Aberdeenshire Council",
        (
            f"{HUB}/fd4c1b90-1623-4512-a7cf-790d8a882e79/resource/6640fb15-a2d7-42b3-8067-3ce084448802/download/"
            "communitycouncils.zip"
        ),
        "cc_name",
        27700,
    ),
    "S12000035": (
        "ab",
        "Argyll and Bute Council",
        f"https://arcgis.argyll-bute.gov.uk/arcgis/rest/services/Open_Data/MapServer/20/{QUERY}",
        "NAME",
        27700,
    ),
    "S12000036": (
        "ce",
        "The City of Edinburgh Council",
        f"https://edinburghcouncilmaps.info/arcgis/rest/services/Atlas/Atlas/MapServer/189/{QUERY}",
        "LABEL",
        27700,
    ),
    "S12000038": (
        "re",
        "Renfrewshire Council",
        (
            "https://services2.arcgis.com/fq3IIWzrdf1xBoPI/arcgis/rest/services/BOUNDARIES_NON_STATUTORY/FeatureServer/1/"
            f"{QUERY}"
        ),
        "NAME",
        27700,
    ),
    "S12000039": (
        "wd",
        "West Dunbartonshire Council",
        (
            f"https://services9.arcgis.com/wZYknYToxgELcbw2/ArcGIS/rest/services/WDC_Community_Councils_/FeatureServer/0/"
            f"{QUERY}"
        ),
        "NAME",
        27700,
    ),
    "S12000040": (
        "wl",
        "West Lothian Council",
        (
            f"{HUB}/33609934-c9a0-4801-a982-062327540f68/resource/46abf8a8-f01e-4f46-b87e-f2aca6ebee1c/download/"
            "communitycouncil.zip"
        ),
        "NAME",
        27700,
    ),
    "S12000041": (
        "an",
        "Angus Council",
        (
            f"{HUB}/a22e6489-e492-4a93-b1f1-ea1433faea60/resource/d044ce40-dfe4-4fc6-a61e-54df4136a81c/download/"
            "angus-community-council-boundaries.geojson"
        ),
        "title",
        3857,
    ),
    "S12000042": (
        "dc",
        "Dundee City Council",
        (
            f"{HUB}/602a5e01-b770-4d98-bd52-bf1bb52b8a8e/resource/d267cc7b-a72b-435e-9d39-be091fa51836/download/"
            "dc_community_councils.zip"
        ),
        "cc_name",
        27700,
    ),
    "S12000047": (
        "fi",
        "Fife Council",
        (
            f"{HUB}/f2a5d12a-07eb-4cb8-859a-63dbcb29e6cb/resource/4f8d1c3b-d552-4dfa-afbc-917debc6dde1/download/"
            "fife_community_councils.zip"
        ),
        "CC_NAME",
        3857,
    ),
    "S12000048": (
        "pk",
        "Perth and Kinross Council",
        (
            "https://utility.arcgis.com/usrsvcs/servers/a33d8cbc0dd4402ba405fd3ce39e5a10/rest/services/"
            f"Community_Councils_2019/FeatureServer/28/{QUERY}"
        ),
        "NAME",
        27700,
    ),
    "S12000049": (
        "gc",
        "Glasgow City Council",
        f"https://www.mapping.glasgow.gov.uk/arcgis_web/rest/services/OPEN_DATA/Community_Councils/MapServer/0/{QUERY}",
        "AREANAME",
        27700,
    ),
    "S12000050": (
        "nl",
        "North Lanarkshire Council",
        (
            "https://services-eu1.arcgis.com/9edRUxcMgH07BEka/arcgis/rest/services/Community_Councils_view/FeatureServer/0/"
            f"{QUERY}"
        ),
        "name",
        27700,
    ),
}
NAMES = {
    ("S12000005", "Tullibody Community Development Trust"): "Tullibody",
    ("S12000005", "Sauchie Community Group"): "Sauchie",
    ("S12000008", "DARVEL and DISTRICT"): "Darvel and District",
    ("S12000017", "Cawdor And West Nairnshire"): "Cawdor and West Nairnshire",
    ("S12000017", "Grantown On Spey"): "Grantown on Spey",
    ("S12000026", "Bowden Village Committee"): "Bowden",
    ("S12000047", "Milesmark and Baldridge ((Dunfermline))"): "Milesmark and Baldridge (Dunfermline)",
}
BODIES = {
    ("S12000005", "Tullibody"): "Tullibody Community Development Trust",
    ("S12000005", "Sauchie"): "Sauchie Community Group",
    ("S12000026", "Bowden"): "Bowden Village Committee",
}
SKIP = {
    ("S12000049", "Shared Baillieston / Garrowhill"),
    ("S12000049", "Shared Cadder/Milton/Lambhill/Maryhill/Summerston"),
}
SMALL = {"and", "of", "the", "on", "by", "with", "in"}
NOTE = (
    "The community council areas of one Scottish council, from the boundary layer it publishes and the Spatial Hub "
    "lists. Community councils are not local authorities: each council draws their areas and sets their elections "
    "under its own scheme for community councils (Local Government (Scotland) Act 1973, Part IV). The areas need not "
    "cover the whole council area and the layers do not tessellate, so each shape is simplified to 10 m on its own "
    "and kept as published. Glasgow's two shared zones are left out. Whether a community council is active or dormant is not recorded."
)
SUMMARY_NOTE = (
    "East Dunbartonshire's layer is not public on the Spatial Hub, and the Improvement Service's national layer "
    "needs an account key, so 31 of the 32 councils are covered."
)


def suffix(url: str) -> str:
    return ".zip" if url.endswith(".zip") else ".gml" if "wfs.exe" in url else ".geojson"


def fetch() -> None:
    for _, _, url, _, _ in COUNCILS.values():
        raw.fetch(SOURCE, url, suffix=suffix(url))


def tidy(lad: str, text: str) -> str:
    text = NAMES.get((lad, text.strip()), text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    text = re.sub(r"\s+Community Council$", "", text, flags=re.IGNORECASE)
    if text.isupper():
        words = re.split(r"(\s+|-|/)", text.lower())
        text = "".join(
            word if index and word in SMALL else word[:1].upper() + word[1:] for index, word in enumerate(words)
        )
        text = re.sub(r"\bMc([a-z])", lambda match: f"Mc{match.group(1).upper()}", text)
        text = re.sub(r"'([a-z])\b", lambda match: f"'{match.group(1)}", text)
    return text


def features(lad: str) -> list[tuple[str, str, str]]:
    _, _, url, field, _ = COUNCILS[lad]
    path = raw.cached(SOURCE, url).path
    found: list[tuple[str, str, str]] = []
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            shp = next(name for name in names if name.lower().endswith(".shp"))

            def part(extension: str) -> bytes:
                return archive.read(next(name for name in names if name.lower() == shp[:-4].lower() + extension))

            cpg = next((name for name in names if name.lower() == shp[:-4].lower() + ".cpg"), None)
            encoding = "utf-8" if cpg and "utf" in archive.read(cpg).decode().lower() else "latin-1"
            reader = shapefile.Reader(
                shp=io.BytesIO(part(".shp")),
                shx=io.BytesIO(part(".shx")),
                dbf=io.BytesIO(part(".dbf")),
                encoding=encoding,
            )
            for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                if record is not None and shape is not None and shape.shapeType != shapefile.NULL:
                    found.append((str(record[field]), "geojson", json.dumps(shape.__geo_interface__)))
    elif path.suffix == ".gml":
        text = path.read_text()
        for member in re.findall(r"<gml:featureMember>(.*?)</gml:featureMember>", text, re.DOTALL):
            name = re.search(rf"<ns:{field}>(.*?)</ns:{field}>", member, re.DOTALL)
            geom = re.search(r"<ns:geom>(.*?)</ns:geom>", member, re.DOTALL)
            if name and geom:
                found.append((name.group(1), "gml", geom.group(1)))
    else:
        for feature in json.loads(path.read_text())["features"]:
            if feature["geometry"]:
                found.append((str(feature["properties"][field]), "geojson", json.dumps(feature["geometry"])))
    return [
        (tidy(lad, name), kind, geom)
        for name, kind, geom in found
        if name.strip() not in ("", "None") and (lad, name.strip()) not in SKIP
    ]


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (lad text, name text, kind text, geom text, srid int) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for lad, (_, _, _, _, srid) in COUNCILS.items():
                for name, kind, geom in features(lad):
                    copy.write_row((lad, name, kind, geom, srid))
        rows = conn.execute(
            """with parsed as (
                 select lad, name, st_makevalid(st_force2d(st_transform(st_setsrid(case kind
                          when 'gml' then st_geomfromgml(geom) else st_geomfromgeojson(geom) end, srid), 27700))) geom
                 from feature),
               merged as (select lad, name, st_union(geom) geom from parsed group by lad, name),
               council as (
                 select c.code lad, st_makevalid(st_transform(st_union(g.geom), 27700)) geom
                 from area a join area_code c on c.area_id = a.id join area_geometry g on g.area_id = a.id
                 where a.scheme_code = 'gb.lad' and c.code like 'S12%%' and (g.valid_to is null)
                 group by c.code),
               clipped as (
                 select m.lad, m.name, c.geom council,
                        st_multi(st_collectionextract(st_makevalid(st_simplifypreservetopology(m.geom, %s)), 3)) geom
                 from merged m join council c using (lad))
               select lad, name, round((st_area(st_intersection(geom, council)) / st_area(geom))::numeric, 3)::float8,
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from clipped where not st_isempty(geom)
               order by lad, name""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"lad": pl.String, "name": pl.String, "kept": pl.Float64, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    print(frame.filter(pl.col("kept") < 0.9).select("lad", "name", "kept").rows(), file=sys.stderr)
    if low := frame.filter(pl.col("kept") < 0.3).select("lad", "name", "kept").rows():
        raise ValueError(f"areas mostly outside their council: {low}")
    if (
        clash := frame.group_by("lad", pl.col("name").map_elements(slug, return_dtype=pl.String))
        .len()
        .filter(pl.col("len") > 1)
        .rows()
    ):
        raise ValueError(f"names that share a slug: {clash}")
    print(frame.group_by("lad").len().sort("lad").rows(), file=sys.stderr)
    print(f"{frame.height} community council areas in {frame['lad'].n_unique()} councils", file=sys.stderr)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        councils = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value like 'S12%%'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Scottish community council areas and councils from 31 councils' boundary layers. {SUMMARY_NOTE}",
            polity="GB",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        if conn.execute("select 1 from scheme where code = %s", [SCHEME]).fetchone() is None:
            cs.add(
                "scheme",
                code=SCHEME,
                polity_id=cs.polity("GB"),
                kind="administrative",
                rank=4,
                noun="community council area",
            )
        sources = {}
        for lad, (code, publisher, url, _, _) in COUNCILS.items():
            title = "Community Council Boundaries"
            sources[lad] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[lad],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=f"{HUB}/community_council_boundaries-{code}",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=f"Contains public sector information from {publisher} licensed under the Open Government "
                "Licence v3.0. Contains OS data © Crown copyright and database right",
                notes=NOTE,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[lad])
        for lad, text, _, geom, point in frame.iter_rows():
            key = f"{lad}-{slug(text)}"
            area = cs.area(SCHEME, key)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=SCHEME)
                cs.add("area_code", area_id=area, code=key, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=cs.area("gb.lad", lad), valid_from=None, valid_to=None)
            name(area, text)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[lad],
            )
            polity = cs.key("gb.polity", key)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="submunicipal", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.key("gb.polity", lad))
                path = f"{councils[lad]}/{slug(text)}"
                if path in taken:
                    path = f"{path}-community-council"
                taken.add(path)
                cs.add("slug", path=path, entity_id=polity)
            name(polity, text)
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=None, valid_to=None)
            body = cs.key("gb.body", key)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, BODIES.get((lad, text), f"{text} Community Council"))
        plan = conn.execute("select id from boundary_set where scheme_code = %s", [SCHEME]).fetchone()
        cs.add(
            "boundary_set",
            **({"id": plan[0]} if plan else {}),
            scheme_code=SCHEME,
            vintage=str(RETRIEVED.year),
            valid_from=None,
            valid_to=None,
            as_of=RETRIEVED,
        )
        print(f"{frame.height} areas, polities, and community councils", file=sys.stderr)
        return cs.write()
