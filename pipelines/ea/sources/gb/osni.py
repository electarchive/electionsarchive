import json
import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.osni"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WARDS = (
    "https://admin.opendatani.gov.uk/dataset/aa140654-cbd8-45b9-82ed-4aeb05725929/resource/"
    "1c67d5ef-4882-4567-8a86-295f39e25fde/download/osni_open_data_largescale_boundaries_wards_1993.geojson"
)
ORDER = "https://www.legislation.gov.uk/uksi/1995/2992/made/data.xml"


def fetch() -> None:
    raw.fetch(SOURCE, WARDS, suffix=".geojson")
    raw.fetch(SOURCE, ORDER, suffix=".xml")


DISTRICTS = (
    "Antrim",
    "Ards",
    "Armagh",
    "Ballymena",
    "Ballymoney",
    "Banbridge",
    "Belfast",
    "Carrickfergus",
    "Castlereagh",
    "Coleraine",
    "Cookstown",
    "Craigavon",
    "Down",
    "Dungannon",
    "Fermanagh",
    "Larne",
    "Limavady",
    "Lisburn",
    "Derry",
    "Magherafelt",
    "Moyle",
    "Newry and Mourne",
    "Newtownabbey",
    "North Down",
    "Omagh",
    "Strabane",
)
VINTAGES = {
    "1997": ("PCON01", date(1997, 5, 1), date(2005, 5, 5)),
    "2005": ("PCON05", date(2005, 5, 5), date(2010, 5, 6)),
}
TOLERANCE = 20


def parse() -> None:
    def fold(text: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", text.upper().replace("ST.", "ST"))

    features = json.loads(raw.cached(SOURCE, WARDS).path.read_text())["features"]
    wards: dict[int, dict[str, int]] = {}
    for feature in features:
        ward = int(feature["properties"]["WARD93_ID"])
        wards.setdefault(ward // 100, {})[fold(feature["properties"]["NAME"])] = ward
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, ORDER).path.read_text()))
    text = text[
        text.index("Name and Designation Composition") + 32 : text.index("(This note is not part of the Order)")
    ]
    found = list(re.finditer(r"([A-Z][A-Za-z ]+?) \((borough|county) constituency\)", text))
    assigned: dict[int, str] = {}
    constituencies = []
    for index, match in enumerate(found):
        body = text[match.end() : found[index + 1].start() if index + 1 < len(found) else len(text)]
        constituencies.append((match[1], match[2]))
        for part in re.split(r"\b(?:i|ii|iii|iv)\b (?=[Tt]he )", body):
            listed = re.match(
                r"\s*[Tt]he following wards of the local government district of (.+?), namely,? (.+?)(?:;|\.)?(?: and)?\s*$",
                part,
            )
            whole = re.match(r"\s*[Tt]he local government district of (.+?)(?:;|\.)?(?: and)?\s*$", part)
            matched = listed or whole
            if matched is None:
                if part.strip():
                    raise ValueError(f"{match[1]}: cannot read {part!r}")
                continue
            district = DISTRICTS.index(matched[1].strip()) + 1
            if listed is None:
                members = list(wards[district].values())
            else:
                pieces = [fold(piece) for piece in re.split(r", | and ", listed[2])]
                members = []
                while pieces:
                    joined = next((count for count in (2, 1) if "AND".join(pieces[:count]) in wards[district]), None)
                    if joined is None:
                        close = [
                            name for name in wards[district] if SequenceMatcher(None, pieces[0], name).ratio() >= 0.85
                        ]
                        if len(close) != 1:
                            raise ValueError(f"{match[1]}: no ward {pieces[0]} in {sorted(wards[district])}")
                        print(f"{match[1]}: {pieces[0]} read as {close[0]}")
                        members.append(wards[district][close[0]])
                        pieces = pieces[1:]
                        continue
                    members.append(wards[district]["AND".join(pieces[:joined])])
                    pieces = pieces[joined:]
            for ward in members:
                if ward in assigned:
                    raise ValueError(f"ward {ward} is in {assigned[ward]} and {match[1]}")
                assigned[ward] = match[1]
    every = {ward for district in wards.values() for ward in district.values()}
    if every - set(assigned) or len(constituencies) != 18:
        raise ValueError(f"unassigned wards {sorted(every - set(assigned))}, {len(constituencies)} constituencies")
    with connect() as conn:
        rows = conn.execute(
            """with ward as (
                 select f.name, st_makevalid(st_transform(st_setsrid(st_geomfromgeojson(f.geojson), 4326), 29902)) as geom
                 from unnest(%s::text[], %s::text[]) as f(name, geojson)
               ),
               seat as (
                 select name, st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom from ward group by name
               ),
               simple as (
                 select name, st_setsrid(st_coveragesimplify(geom, %s) over (), 29902) as geom from seat
               )
               select name, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from simple""",
            [
                [assigned[int(feature["properties"]["WARD93_ID"])] for feature in features],
                [json.dumps(feature["geometry"]) for feature in features],
                TOLERANCE,
            ],
        ).fetchall()
    kinds = dict(constituencies)
    frame = pl.DataFrame(rows, schema=["name", "geom", "label_point"], orient="row").with_columns(
        kind=pl.col("name").replace_strict(kinds),
        number=pl.col("name").rank("ordinal").cast(pl.Int64),
    )
    print(frame.select("number", "name", "kind"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "constituencies.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "constituencies.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland's 18 Westminster constituencies of 1997–2010, built from OSNI's 1993 wards",
            polity="GB",
        )
        wards = cs.source(
            "Ordnance Survey of Northern Ireland", "OSNI Open Data - Largescale Boundaries - Wards (1993)"
        )
        cs.add(
            "source",
            id=wards,
            kind="official_body",
            publisher="Ordnance Survey of Northern Ireland",
            title="OSNI Open Data - Largescale Boundaries - Wards (1993)",
            url="https://www.data.gov.uk/dataset/osni-open-data-largescale-boundaries-wards-19939",
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution="© Ordnance Survey of Northern Ireland, licensed under the Open Government Licence v3.0",
            notes=(
                "Each constituency is the union of the 1993 wards that the Parliamentary Constituencies (Northern "
                "Ireland) Order 1995 assigns to it, simplified at 20 m."
            ),
        )
        cs.artifact(raw.cached(SOURCE, WARDS), wards)
        order = cs.source("legislation.gov.uk", "The Parliamentary Constituencies (Northern Ireland) Order 1995")
        cs.add(
            "source",
            id=order,
            kind="official_body",
            publisher="legislation.gov.uk",
            title="The Parliamentary Constituencies (Northern Ireland) Order 1995",
            url="https://www.legislation.gov.uk/uksi/1995/2992/made",
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        )
        cs.artifact(raw.cached(SOURCE, ORDER), order)
        for prefix, start, end in VINTAGES.values():
            country = cs.area("gb.country", "NIR", on=start)
            if country in cs.minted:
                raise ValueError("publish gb.umit before gb.osni")
            for number, label, geom, point in (
                frame.select("number", "name", "geom", "label_point").sort("number").iter_rows()
            ):
                code = f"{prefix}-N{number:02d}"
                area = cs.area("gb.pcon", code, on=start)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="gb.pcon")
                    cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                    cs.add("area_parent", area_id=area, parent_id=country, valid_from=start, valid_to=end)
                    cs.add("name", entity_id=area, text=label, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=start,
                    valid_to=end,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=wards,
                )
        return cs.write()
