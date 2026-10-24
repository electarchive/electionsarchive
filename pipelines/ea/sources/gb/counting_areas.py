import json
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.eu import gisco
from ea.sources.gb.umit import fold
from ea.store import DATA, connect

SOURCE = "gb.counting_areas"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LAD = f"{SERVICES}/Local_Authority_Districts_December_2016_GCB_in_the_UK_2022/FeatureServer"
LADS = f"{LAD}/0/query?where=1%3D1&outFields=lad16cd,lad16nm&outSR=4326&f=geojson"
LOOKUP = (
    f"{SERVICES}/LAD16_CTY16_EN_LU_512582791c8646eeac1e67b842520142/FeatureServer/0/query"
    "?where=1%3D1&outFields=LAD16CD,CTY16NM&f=json"
)
COUNTRIES = gisco.DATASETS["countries"][0]
ONS = "Office for National Statistics"
TITLE = "Local Authority Districts (December 2016) Boundaries UK GCB"
COUNTIES_1974 = {
    "Avon": ("Bath and North East Somerset", "Bristol, City of", "North Somerset", "South Gloucestershire"),
    "Bedfordshire": ("Bedford", "Central Bedfordshire", "Luton"),
    "Berkshire": ("Bracknell Forest", "Reading", "Slough", "West Berkshire", "Windsor and Maidenhead", "Wokingham"),
    "Buckinghamshire": ("Milton Keynes",),
    "Cambridgeshire": ("Peterborough",),
    "Cheshire": ("Cheshire East", "Cheshire West and Chester", "Halton", "Warrington"),
    "Cleveland": ("Hartlepool", "Middlesbrough", "Redcar and Cleveland", "Stockton-on-Tees"),
    "Cornwall": ("Cornwall",),
    "Derbyshire": ("Derby",),
    "Devon": ("Plymouth", "Torbay"),
    "Dorset": ("Bournemouth", "Poole"),
    "Durham": ("County Durham", "Darlington"),
    "East Sussex": ("Brighton and Hove",),
    "Essex": ("Southend-on-Sea", "Thurrock"),
    "Greater London": ("Inner London", "Outer London"),
    "Hampshire": ("Portsmouth", "Southampton"),
    "Hereford and Worcester": ("Herefordshire, County of", "Worcestershire"),
    "Humberside": (
        "East Riding of Yorkshire",
        "Kingston upon Hull, City of",
        "North East Lincolnshire",
        "North Lincolnshire",
    ),
    "Isle of Wight": ("Isle of Wight",),
    "Isles of Scilly": ("Isles of Scilly",),
    "Kent": ("Medway",),
    "Lancashire": ("Blackburn with Darwen", "Blackpool"),
    "Leicestershire": ("Leicester", "Rutland"),
    "North Yorkshire": ("York",),
    "Northumberland": ("Northumberland",),
    "Nottinghamshire": ("Nottingham",),
    "Shropshire": ("Shropshire", "Telford and Wrekin"),
    "Staffordshire": ("Stoke-on-Trent",),
    "Wiltshire": ("Swindon", "Wiltshire"),
    "Clwyd": ("Denbighshire", "Flintshire", "Wrexham"),
    "Dyfed": ("Carmarthenshire", "Ceredigion", "Pembrokeshire"),
    "Gwent": ("Blaenau Gwent", "Monmouthshire", "Newport", "Torfaen"),
    "Gwynedd": ("Conwy", "Gwynedd", "Isle of Anglesey"),
    "Mid Glamorgan": ("Bridgend", "Caerphilly", "Merthyr Tydfil", "Rhondda Cynon Taf"),
    "Powys": ("Powys",),
    "South Glamorgan": ("Cardiff", "Vale of Glamorgan"),
    "West Glamorgan": ("Neath Port Talbot", "Swansea"),
    "Borders": ("Scottish Borders",),
    "Central": ("Clackmannanshire", "Falkirk", "Stirling"),
    "Dumfries and Galloway": ("Dumfries and Galloway",),
    "Fife": ("Fife",),
    "Grampian": ("Aberdeen City", "Aberdeenshire", "Moray"),
    "Highland": ("Highland",),
    "Lothian": ("City of Edinburgh", "East Lothian", "Midlothian", "West Lothian"),
    "Orkney": ("Orkney Islands",),
    "Shetland": ("Shetland Islands",),
    "Strathclyde": (
        "Argyll and Bute",
        "East Ayrshire",
        "East Dunbartonshire",
        "East Renfrewshire",
        "Glasgow City",
        "Inverclyde",
        "North Ayrshire",
        "North Lanarkshire",
        "Renfrewshire",
        "South Ayrshire",
        "South Lanarkshire",
        "West Dunbartonshire",
    ),
    "Tayside": ("Angus", "Dundee City", "Perth and Kinross"),
    "Western Isles": ("Na h-Eileanan Siar",),
}
ALIASES = {
    "Bath & NE Somerset": "Bath and North East Somerset",
    "Cheshire West": "Cheshire West and Chester",
    "Durham": "County Durham",
    "East Hants": "East Hampshire",
    "East Staffs": "East Staffordshire",
    "Newcastle": "Newcastle upon Tyne",
    "North Herts": "North Hertfordshire",
    "South Cambs": "South Cambridgeshire",
    "Stockton": "Stockton-on-Tees",
    "West Lancs": "West Lancashire",
}
NOTES = (
    "Referendum counting areas. Districts, unitary authorities, London boroughs, and Scottish and Welsh councils use "
    "ONS's December 2016 boundaries (20 m, clipped to the coastline) for every referendum from 1997, matched by GSS "
    "code, or by name for the 2011 AV referendum's English districts; Northern Ireland is the union of its districts. "
    "The 1975 and 1979 counties and regions of the Local Government Acts 1972 and 1973 have no open map, so they are "
    "synthetic unions of today's councils and county districts (quality synthetic): Conwy counts wholly to Gwynedd and "
    "Caerphilly to Mid Glamorgan, and the 1996 regrading's small boundary moves are ignored. Gibraltar is Eurostat "
    "GISCO's shape (Countries 2024, © EuroGeographics, free for non-commercial use)."
)


def fetch() -> None:
    raw.fetch(SOURCE, LADS, suffix=".geojson")
    raw.fetch(SOURCE, LOOKUP, suffix=".json")


def parse() -> None:
    lads = [
        (feature["properties"]["lad16cd"], feature["properties"]["lad16nm"], json.dumps(feature["geometry"]))
        for feature in json.loads(raw.cached(SOURCE, LADS).path.read_text())["features"]
    ]
    county = {
        row["attributes"]["LAD16CD"]: row["attributes"]["CTY16NM"]
        for row in json.loads(raw.cached(SOURCE, LOOKUP).path.read_text())["features"]
    }
    members: dict[str, str] = {}
    for code, label, _ in lads:
        target = county.get(code, label)
        members[code] = next(
            (name for name, parts in COUNTIES_1974.items() if target in parts or label in parts),
            target if code[:3] in ("E07", "E08") else "",
        )
    gibraltar = next(
        json.dumps(feature["geometry"])
        for feature in json.loads(raw.cached(gisco.SOURCE, COUNTRIES).path.read_text())["features"]
        if feature["properties"]["CNTR_ID"] == "GI"
    )
    with connect() as conn:
        units = conn.execute(
            """select u.code, n.text, cc.code, array_agg(distinct e.date_from order by e.date_from)
               from reporting_unit u
               join name n on n.entity_id = u.id and n.is_preferred
               left join area_code cc on cc.area_id = u.area_id and cc.code in ('ENG', 'WLS', 'SCT', 'NIR')
               join result_value v on v.unit_k = u.k
               join result_set rs on rs.k = v.result_set_k
               join changeset cs on cs.id = rs.changeset_id and cs.pipeline = 'gb.referendums'
               join contest k on k.id = rs.contest_id
               join election e on e.id = k.election_id
               where u.scheme_code = 'gb.counting_area' and u.kind = 'standard'
               group by 1, 2, 3"""
        ).fetchall()
        devolved = {
            (scheme, fold(text)): (geom, point, str(source), quality)
            for scheme, text, geom, point, source, quality in conn.execute(
                """select a.scheme_code, n.text, encode(st_asewkb(g.geom), 'hex'),
                          encode(st_asewkb(g.label_point), 'hex'), g.source_id, g.quality
                   from area a join name n on n.entity_id = a.id and n.is_preferred
                   join area_geometry g on g.area_id = a.id and g.valid @> date '2011-05-05'
                   where a.scheme_code in ('gb.spc', 'gb.senc')"""
            ).fetchall()
        }
        conn.execute("create temp table lad (code text, name text, member text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy lad from stdin") as copy:
            for code, label, geojson in lads:
                copy.write_row((code, label, members[code], geojson))
        conn.execute(
            """alter table lad add column geom geometry;
               update lad set geom = st_multi(st_collectionextract(st_makevalid(
                 st_force2d(st_setsrid(st_geomfromgeojson(geojson), 4326))), 3))"""
        )
        names = {fold(re.sub(r", (City|County) of$", "", label)): code for code, label, _ in lads if code[0] in "EW"}
        rows: list[tuple[str, str, date, str, str, str, str]] = []
        unmatched = []
        for code, label, country, dates in units:
            spec: tuple[str, str] | None = None
            if code in members:
                spec = ("lad", f"code = '{code}'")
            elif code == "N92000002" or (code.startswith("AV2011-") and country == "NIR"):
                spec = ("lad", "code like 'N09%'")
            elif "-1974-" in code:
                spec = ("synthetic", f"member = '{label.replace("'", "''")}'")
            elif code.startswith("AV2011-") and country in ("SCT", "WLS"):
                found = devolved.get(("gb.spc" if country == "SCT" else "gb.senc", fold(label)))
                if found is not None:
                    rows += [(code, label, day, *found) for day in dates]
                    continue
            elif code.startswith("AV2011-") and (lad := names.get(fold(ALIASES.get(label, label)))) is not None:
                spec = ("lad", f"code = '{lad}'")
            if code == "GI":
                geom, point = conn.execute(
                    """select encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex')
                       from st_multi(st_collectionextract(st_makevalid(st_force2d(
                         st_setsrid(st_geomfromgeojson(%s), 4326))), 3)) g""",
                    [gibraltar],
                ).fetchone()
                rows += [(code, label, day, geom, point, "gisco", "official") for day in dates]
                continue
            if spec is None:
                unmatched.append(f"{code} {label} ({country})")
                continue
            kind, where = spec
            geom, point, count = conn.execute(
                f"""select encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex'), n
                    from (select st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as g, count(*) as n
                          from lad where {where}) u"""
            ).fetchone()
            if not count:
                unmatched.append(f"{code} {label} ({country}): no district for {where}")
                continue
            rows += [(code, label, day, geom, point, "ons", kind) for day in dates]
    if unmatched:
        raise ValueError(f"{len(unmatched)} unmatched counting areas: {unmatched}")
    frame = pl.DataFrame(
        rows,
        schema={
            "code": pl.String,
            "name": pl.String,
            "day": pl.Date,
            "geom": pl.String,
            "label_point": pl.String,
            "source": pl.String,
            "quality": pl.String,
        },
        orient="row",
    ).with_columns(pl.col("quality").replace({"lad": "official"}))
    print(frame.height, "shapes", frame.group_by("day").len().sort("day").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Shapes for the referendum counting areas of 1975–2016: ONS council boundaries, the Scottish "
            "Parliament and Senedd constituencies for the 2011 AV referendum, and synthetic 1974 counties and regions",
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for key, url, artifacts in (("ons", LAD, (LADS, LOOKUP)), ("gisco", COUNTRIES, ())):
            publisher, title = (ONS, TITLE) if key == "ons" else ("Eurostat", gisco.DATASETS["countries"][2])
            sources[key] = cs.source(publisher, title)
            if key == "ons":
                cs.add(
                    "source",
                    id=sources[key],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Open Government Licence v3.0",
                    license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                    attribution=(
                        "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
                        "Contains OS data © Crown copyright and database right"
                    ),
                    notes=NOTES,
                )
            for artifact in artifacts:
                cs.artifact(raw.cached(SOURCE, artifact), sources[key])
        cs.artifact(raw.cached(gisco.SOURCE, COUNTRIES), sources["ons"])
        countries = dict(
            conn.execute(
                """select u.code, u.area_id from reporting_unit u
                   where u.scheme_code = 'gb.counting_area' and u.area_id is not null"""
            ).fetchall()
        )
        unit_ids = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'gb.counting_area'").fetchall()
        )
        memberships = []
        for (code, label), shapes in frame.group_by("code", "name", maintain_order=True):
            area = cs.area("gb.counting_area", str(code))
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.counting_area")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add(
                    "name",
                    entity_id=area,
                    text=label,
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
            if code in countries:
                cs.add("area_parent", area_id=area, parent_id=countries[code], valid_from=None)
            for _, _, day, geom, point, source, quality in shapes.rows():
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=day,
                    valid_from_precision="day",
                    valid_to=day + timedelta(days=1),
                    valid_to_precision="day",
                    geom=geom,
                    label_point=point,
                    quality=quality,
                    source_id=sources.get(source, source),
                )
                memberships.append((str(unit_ids[code]), str(area), day, day + timedelta(days=1)))
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                memberships,
                schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                orient="row",
            ),
        )
        plans = dict(
            conn.execute("select vintage, id from boundary_set where scheme_code = 'gb.counting_area'").fetchall()
        )
        for day in sorted(set(frame["day"])):
            vintage = day.isoformat()
            cs.add(
                "boundary_set",
                **({"id": plans[vintage]} if vintage in plans else {}),
                scheme_code="gb.counting_area",
                vintage=vintage,
                valid_from=day,
                valid_to=day + timedelta(days=1),
                as_of=day,
            )
        return cs.write()
