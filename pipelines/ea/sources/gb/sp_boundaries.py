import html
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.umit import fold
from ea.store import DATA, connect

SOURCE = "gb.sp_boundaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
UPLOADS = "https://www.bcomm-scotland.independent.gov.uk/wp-content/uploads/sites/2/2025/09"
FOURTH = f"{UPLOADS}/UK_Parliament_4th_constituencies_1758732213-1.zip"
TERMS = f"{UPLOADS}/SBC_Terms_of_Use_Historical_electoral_data_1758732219-1.pdf"
ORDER = "https://www.legislation.gov.uk/uksi/1996/1926/made/data.xml"
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LAYERS = {
    ("gb.spc", "2011"): (
        "Scottish_Parliamentary_Constituencies_May_2021_Boundaries_SC_BGC",
        "SPC21NM",
        "Scottish Parliamentary Constituencies (May 2021) Boundaries SC BGC",
    ),
    ("gb.spr", "2011"): (
        "Scottish_Parliamentary_Regions_December_2022_SC_BGC",
        "SPR22NM",
        "Scottish Parliamentary Regions (December 2022) Boundaries SC BGC",
    ),
    ("gb.spc", "2026"): ("SPC_MAY_2026_SC_BGC", "SPC26NM", "Scottish Parliamentary Constituencies (May 2026) SC BGC"),
    ("gb.spr", "2026"): ("SPR_MAY_2026_SC_BGC", "SPR26NM", "Scottish Parliamentary Regions (May 2026) SC BGC"),
}
ERAS = {
    "1999": (date(1999, 5, 6), date(2011, 5, 5)),
    "2011": (date(2011, 5, 5), date(2026, 5, 7)),
    "2026": (date(2026, 5, 7), None),
}
NAMES = {
    ("2011", "Orkney Islands"): "Orkney",
    ("2011", "South Scotland"): "South of Scotland",
    ("2011", "West Scotland"): "West of Scotland",
    ("2026", "South Scotland"): "South of Scotland",
    ("2026", "West Scotland"): "West of Scotland",
    ("1999", "Glasgow Baillieston"): "Glasgow Ballieston",
}
SHETLAND = 1_062_000
TOLERANCE = 50
ONS = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ONS_ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)
BCS = "Boundary Commission for Scotland"
BCS_ATTRIBUTION = (
    "© Boundary Commission for Scotland, Local Government Boundary Commission for Scotland. Contains Ordnance Survey "
    "data © Crown copyright and database rights 2013"
)


def layer_url(service: str) -> str:
    return f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"


def fetch() -> None:
    raw.fetch(SOURCE, FOURTH, suffix=".zip")
    raw.fetch(SOURCE, TERMS, suffix=".pdf")
    raw.fetch(SOURCE, ORDER, suffix=".xml")
    for service, _, _ in LAYERS.values():
        raw.fetch(SOURCE, layer_url(service), suffix=".geojson")


def regions_1999() -> dict[str, str]:
    text = raw.cached(SOURCE, ORDER).path.read_text()
    body = text[text.index("<tbody>") : text.index("</tbody>")]
    found: dict[str, str] = {}
    region = ""
    for row in re.findall(r"<tr>(.*?)</tr>", body):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*?(?:/>|>.*?</td>)", row)
        ]
        if len(cells) != 2:
            continue
        if cells[0]:
            region = cells[0]
        elif cells[1]:
            found[cells[1]] = region
    return found


def parse() -> None:
    with connect() as conn:
        conn.execute(
            "create temp table feature (scheme text, era text, name text, geojson text, clip boolean) on commit drop"
        )
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            with zipfile.ZipFile(raw.cached(SOURCE, FOURTH).path) as archive:
                stem = "UK_Parliament_4th_constituencies"
                reader = shapefile.Reader(
                    shp=io.BytesIO(archive.read(f"{stem}.shp")),
                    shx=io.BytesIO(archive.read(f"{stem}.shx")),
                    dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                )
                for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                    copy.write_row(("gb.spc", "1999", str(record[2]), json.dumps(shape.__geo_interface__), True))
            for (scheme, era), (service, field, _) in LAYERS.items():
                for feature in json.loads(raw.cached(SOURCE, layer_url(service)).path.read_text())["features"]:
                    copy.write_row((scheme, era, feature["properties"][field], json.dumps(feature["geometry"]), False))
        conn.execute(
            """create temp table shape on commit drop as
               select scheme, era, name, geom from (
                 select scheme, era, name, st_multi(st_collectionextract(st_makevalid(case when clip
                          then st_intersection(st_union(st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson),
                            27700)))), coast.geom)
                          else st_union(st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700)))) end),
                          3)) as geom
                 from feature,
                      (select st_makevalid(st_transform(st_union(g.geom), 27700)) as geom
                       from area a join area_geometry g on g.area_id = a.id
                       where a.scheme_code = 'gb.nation' and g.quality = 'official') coast
                 group by scheme, era, name, clip, coast.geom) whole"""
        )
        conn.execute(
            """insert into shape
               select 'gb.spc', '1999', part, st_multi(st_collectionextract(st_intersection(geom, box), 3))
               from shape, (values
                 ('Orkney', st_makeenvelope(0, 0, 800000, %(line)s, 27700)),
                 ('Shetland', st_makeenvelope(0, %(line)s, 800000, 1300000, 27700))) halves(part, box)
               where era = '1999' and name = 'Orkney and Shetland'""",
            {"line": SHETLAND},
        )
        conn.execute("delete from shape where era = '1999' and name = 'Orkney and Shetland'")
        conn.execute("create temp table member (name text, region text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy member from stdin") as copy:
            for name, region in regions_1999().items():
                copy.write_row((name, region))
        missing = conn.execute(
            """select m.name from member m where m.name <> 'Orkney and Shetland'
               and not exists (select from shape s where s.era = '1999' and s.name = m.name)"""
        ).fetchall()
        if missing or conn.execute("select count(*) from member").fetchone() != (72,):
            raise ValueError(f"1996 Order constituencies without a 1999 shape: {missing}")
        conn.execute(
            """insert into shape
               select 'gb.spr', '1999', m.region, st_multi(st_union(s.geom))
               from member m join shape s on s.era = '1999' and s.scheme = 'gb.spc'
                 and (s.name = m.name or (m.name = 'Orkney and Shetland' and s.name in ('Orkney', 'Shetland')))
               group by m.region"""
        )
        rows = conn.execute(
            """select scheme, era, name,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select scheme, era, name,
                            st_setsrid(st_coveragesimplify(geom, %s) over (partition by scheme, era), 27700) as geom
                     from shape) simple""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"scheme": pl.String, "era": pl.String, "name": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    print(frame.group_by("scheme", "era").len().sort("scheme", "era").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish Parliament constituency and region boundaries for 1999, 2011, and 2026",
            polity="GB",
        )
        bcs = cs.source(BCS, "UK Parliament Constituencies 4th Review 1995–2005")
        cs.add(
            "source",
            id=bcs,
            kind="official_body",
            publisher=BCS,
            title="UK Parliament Constituencies 4th Review 1995–2005",
            url="https://www.bcomm-scotland.independent.gov.uk/boundary-maps/boundary-maps-data-files/",
            license="Scottish Boundary Commissions Historical Electoral Data terms of use",
            license_url=TERMS,
            attribution=BCS_ATTRIBUTION,
            notes=(
                "The Scottish Parliament's 1999–2011 constituencies are the UK Parliament constituencies of the 4th "
                "Review, with Orkney and Shetland split between its two island groups. Regions are the European "
                "Parliamentary constituencies of the European Parliamentary Constituencies (Scotland) Order 1996 "
                "(SI 1996/1926), built here as unions of their constituencies. Clipped to the coastline of ONS's 2019 "
                "generalized constituencies and simplified to 50 m."
            ),
        )
        cs.artifact(raw.cached(SOURCE, FOURTH), bcs)
        cs.artifact(raw.cached(SOURCE, TERMS), bcs)
        cs.artifact(raw.cached(SOURCE, ORDER), bcs)
        sources: dict[tuple[str, str], UUID] = {("gb.spc", "1999"): bcs, ("gb.spr", "1999"): bcs}
        for key, (service, _, title) in LAYERS.items():
            sources[key] = cs.source(ONS, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=ONS,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ONS_ATTRIBUTION,
                notes="Generalized boundaries (20 m) clipped to the coastline, simplified here to 50 m.",
            )
            cs.artifact(raw.cached(SOURCE, layer_url(service)), sources[key])
        areas = {
            (scheme, code.split(":", 1)[0], fold(text)): area
            for scheme, code, text, area in conn.execute(
                """select a.scheme_code, c.code, n.text, a.id from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code in ('gb.spc', 'gb.spr')"""
            ).fetchall()
        }
        matched = set()
        for scheme, era, label, geom, point in frame.iter_rows():
            key = (scheme, era, fold(NAMES.get((era, label), label)))
            if key not in areas:
                raise ValueError(f"no {scheme} {era} area named {label}")
            matched.add(key)
            cs.add(
                "area_geometry",
                area_id=areas[key],
                valid_from=ERAS[era][0],
                valid_to=ERAS[era][1],
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[(scheme, era)],
            )
        if missing := sorted(set(areas) - matched):
            raise ValueError(f"areas without a shape: {missing}")
        plans = {
            (scheme, vintage): id
            for id, scheme, vintage in conn.execute(
                "select id, scheme_code, vintage from boundary_set where scheme_code in ('gb.spc', 'gb.spr')"
            ).fetchall()
        }
        for scheme in ("gb.spc", "gb.spr"):
            for era, (start, end) in ERAS.items():
                cs.add(
                    "boundary_set",
                    **({"id": plans[(scheme, era)]} if (scheme, era) in plans else {}),
                    scheme_code=scheme,
                    vintage=era,
                    valid_from=start,
                    valid_to=end,
                    as_of=start,
                )
        return cs.write()
