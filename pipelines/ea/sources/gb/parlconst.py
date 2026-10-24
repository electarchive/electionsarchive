import html
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.umit import fold
from ea.store import DATA, connect

SOURCE = "gb.parlconst"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
UMAP = "https://umap.openstreetmap.fr/en"
MAPS = {
    "1885": [
        "1885-constituencies-england-south_956651",
        "1885-constituencies-england-midlands_958231",
        "1885-constituencies-england-north_967524",
        "1885-wales-constituencies_888092",
        "1885-constituencies-scotland_918203",
        "1885-constituencies-northern-ireland_918763",
    ],
    "1918": [
        "1918-constituencies-england-south_956656",
        "1918-constituencies-england-midlands_958232",
        "1918-constituencies-england-north_967530",
        "1918-wales-constituencies_885976",
        "1918-constituencies-northern-ireland_918768",
    ],
    "1922": ["1922-constituencies-northern-ireland_919430"],
    "1945": [
        "1945-consituencies-england-south_956661",
        "1945-constituencies-england-midlands_958234",
        "1945-constituencies-england-north_967534",
    ],
    "1950": [
        "1950-constituencies-england-south_956664",
        "1950-constituencies-england-midlands_958238",
        "1950-constituencies-england-north_967539",
        "1950-wales-constituencies_885978",
        "1950-constituencies-northern-ireland_919557",
    ],
    "1955": [
        "1955-constituencies-england-south_956667",
        "1955-constituencies-england-midlands_958239",
        "1955-constituencies-england-north_967548",
        "1955-wales-constituencies_885980",
    ],
    "1974": [
        "1974-constituencies-england-south_956671",
        "1974-constituencies-england-midlands_958240",
        "1974-constituencies-england-north_967550",
        "1974-wales-constituencies_885984",
        "1974-constituencies-northern-ireland_919567",
    ],
    "1983": [
        "1983-constituencies-england-south_956674",
        "1983-constituencies-england-midlands_958242",
        "1983-constituencies-england-north_967565",
        "1983-wales-constituencies_885986",
        "1983-constituencies-northern-ireland_919573",
    ],
}


def layers(slug: str) -> list[str]:
    page = raw.fetch(SOURCE, f"{UMAP}/map/{slug}", suffix=".html").path.read_text()
    match = re.search(r'id="map-settings"[^>]*data-settings="([^"]*)"', page)
    if match is None:
        raise ValueError(f"no uMap settings in {slug}")
    settings = json.loads(html.unescape(match.group(1)))
    return [layer["id"] for layer in settings["properties"]["datalayers"]]


def fetch() -> None:
    for slugs in MAPS.values():
        for slug in slugs:
            for layer in layers(slug):
                raw.fetch(SOURCE, f"{UMAP}/datalayer/{slug.rsplit('_', 1)[1]}/{layer}/", suffix=".geojson")


COUNTRIES = {"england": ("eng",), "wales": ("wls",), "scotland": ("sct",), "northern-ireland": ("irl", "nir")}
ARCHIVE = {"1922": "1918", "1945": "1918"}
STARTS = {"1918": {date(1918, 12, 14)}, "1945": {date(1945, 7, 5)}}
EXTRA = {("1950", "nir"): "1955"}
DIRECTIONS = {
    "e": "east",
    "w": "west",
    "n": "north",
    "s": "south",
    "ne": "north east",
    "nw": "north west",
    "se": "south east",
    "sw": "south west",
    "c": "central",
}
LOOSE = {"district", "districts", "boroughs", "borough", "burghs", "burgh", "division", "city", "of", "the", "and"}
NAMES = {
    "Ashton-under-Line": "Ashton-under-Lyne",
    "Birkendhead": "Birkenhead",
    "Birmingha Selly Oak": "Birmingham Selly Oak",
    "Cities of London & Westmister": "Cities of London and Westminster",
    "City of London & Westmister S": "City of London and Westminster South",
    "City of London (2)": "City of London",
    "Clackmannan & Kinross": "Clackmannanshire and Kinross-shire",
    "Deptford": "Lewisham Deptford",
    "Edinburghshire": "Midlothian",
    "Gllingham": "Gillingham",
    "Govan": "Glasgow Govan",
    "Grimsby": "Great Grimsby",
    "Hull E": "East Kingston upon Hull",
    "Hull N": "North Kingston upon Hull",
    "Hull W": "West Kingston upon Hull",
    "Inverness": "Inverness-shire",
    "Inverness District": "Inverness Burghs",
    "Kingston": "Kingston upon Thames",
    "Kingston-upon-Hull Cenrtral": "Central Kingston upon Hull",
    "Kinswinford": "Kingswinford",
    "Leomisnter": "Leominster",
    "Llanelly": "Llanelli",
    "Maccclesfield": "Macclesfield",
    "Mansfiield": "Mansfield",
    "Merthyr Tydfil, Merthyr": "Merthyr",
    "Merthyr Tyfil, Aberdare": "Aberdare",
    "Newport": "Newport (Shropshire)",
    "Nottinghsm W": "Nottingham West",
    "Oxon Mid": "Mid Oxfordshire",
    "Rochester, Chatham": "Chatham",
    "Rochester, Gillingham": "Gillingham",
    "Sheffield Hillborough": "Sheffield Hillsborough",
    "Southgate": "Enfield Southgate",
    "Southwark": "Southwark West",
    "St George Hanover Square": "St George's, Hanover Square",
    "St George's": "St George",
    "St Marylebone E": "East Marylebone",
    "St Marylebone W": "West Marylebone",
    "Stepney Limehouse": "Limehouse",
    "Stepney Mile End": "Mile End",
    "Stockton": "Stockton-on-Tees",
    "Stoke-on-Tent Stoke": "Stoke",
    "Stoke-on-Trent Burslem": "Burslem",
    "Stoke-on-Trent Hanley": "Hanley",
    "Stratford": "Stratford-on-Avon",
    "Wolverhampton Bilston": "Bilston",
    "Wolverhamton NE": "North East Wolverhampton",
    "Wolverhamton SW": "South West Wolverhampton",
    "Wolverhmapton NE": "North East Wolverhampton",
    "Yarmouth": "Great Yarmouth",
}
REGIONAL = {
    ("north", "Richmond"): "Richmond (Yorks)",
    ("south", "Richmond"): "Richmond (Surrey)",
    ("south", "Wellington"): "Wellington (Somerset)",
}
ALIASES = {"1974:eng-richmond-upon-thames": "Richmond (Surrey)"}
GAPS = {"1983:eng-north-east-milton-keynes": "PCON01-315", "1983:eng-south-west-milton-keynes": "PCON01-292"}
INTERIM = date(1992, 4, 9)
SAME = {
    "1955:eng-woodford": "1955:eng-wanstead-and-woodford",
    "1955:eng-richmond-upon-thames": "1955:eng-richmond-surrey",
}
PAGE = "https://www.parlconst.org/constituency-maps"
PUBLISHER = "parlconst.org"
TITLE = "UK Parliamentary Constituencies: constituency maps since 1885"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "Constituency boundaries © parlconst.org, CC BY 4.0. Contains OS data © Crown copyright and database right"
)
NOTES = (
    "Constituency maps drawn by the author of parlconst.org backwards in time from Ordnance Survey's 2010 and 2024 "
    "boundaries and published as uMap layers on umap.openstreetmap.fr. Clipped here to the coastline of ONS's 2019 "
    "generalized constituencies and simplified to 20 m. Buckingham from 1992 to 1997 is ONS's 1997 Buckingham cut "
    "to this 1983 Buckingham: the Parliamentary Constituencies (England) (Miscellaneous Changes) Order 1990 "
    "(SI 1990/1307) moved its Milton Keynes wards to the two new Milton Keynes seats, and the Parliamentary "
    "Constituencies (England) Order 1995 (SI 1995/1626) added Aston Clinton ward."
)
TOLERANCE = 20


def keys(label: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    words = []
    for word in fold(NAMES.get(label, label).replace("'", "")).split():
        words += DIRECTIONS.get(word, word).split()
    exact = frozenset(words)
    bare = frozenset(word.removesuffix("shire") or word for word in words)
    return exact, bare, bare - LOOSE


def parse() -> None:
    index = json.loads((DATA / "raw" / SOURCE / "index.json").read_text())
    with connect() as conn:
        conn.execute(
            "create temp table feature (vintage text, country text, map text, name text, geojson text) on commit drop"
        )
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for vintage, slugs in MAPS.items():
                for slug in slugs:
                    country = next(c for c in COUNTRIES if c in slug)
                    for url in [url for url in index if url.startswith(f"{UMAP}/datalayer/{slug.rsplit('_', 1)[1]}/")]:
                        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
                            label = str(feature["properties"].get("Name") or "").strip().replace("\u2019", "'")
                            label = REGIONAL.get(
                                (next(r for r in ("south", "midlands", "north", "") if r in slug), label), label
                            )
                            if label and feature.get("geometry") is not None:
                                copy.write_row((vintage, country, slug, label, json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select vintage, country, name,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select vintage, country, name,
                            st_setsrid(st_coveragesimplify(geom, %s) over (partition by vintage, map), 27700) as geom
                     from (select vintage, country, map, name, st_multi(st_collectionextract(st_intersection(
                             st_makevalid(st_union(st_makevalid(st_transform(
                               st_setsrid(st_force2d(st_geomfromgeojson(geojson)), 4326), 27700)))), coast.geom), 3)) as geom
                           from feature,
                                (select st_makevalid(st_transform(st_union(g.geom), 27700)) as geom
                                 from area a join area_geometry g on g.area_id = a.id
                                 where a.scheme_code = 'gb.nation' and g.quality = 'official') coast
                           group by vintage, country, map, name, coast.geom) whole) simple
               where not st_isempty(geom)""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "vintage": pl.String,
            "country": pl.String,
            "name": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    print(frame.group_by("vintage", "country").len().sort("vintage", "country").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def match(
    frame: pl.DataFrame, areas: list[tuple[str, str, UUID, date, date | None]]
) -> tuple[dict[UUID, int], list[str]]:
    matched: dict[UUID, int] = {}
    misses: list[str] = []
    expected: list[tuple[str, UUID, str]] = []
    features = list(enumerate(frame.select("vintage", "country", "name").iter_rows()))
    for vintage, country in sorted({(v, c) for _, (v, c, _) in features}):
        group = [(i, label) for i, (v, c, label) in features if (v, c) == (vintage, country)]
        used: set[int] = set()
        for prefix in COUNTRIES[country]:
            for archive in [
                ARCHIVE.get(vintage, vintage),
                *([EXTRA[(vintage, prefix)]] if (vintage, prefix) in EXTRA else []),
            ]:
                pool = [
                    (area, ALIASES.get(code, label))
                    for code, label, area, start, _ in areas
                    if code.startswith(f"{archive}:{prefix}-")
                    and (vintage not in STARTS or start in STARTS[vintage])
                    and code not in GAPS
                ]
                taken: set[int] = set()
                for tier in (0, 1, 2):
                    left = [(i, keys(label)[tier]) for i, label in group if i not in taken]
                    open_ = [(area, keys(label)[tier]) for area, label in pool if area not in matched]
                    for area, key in open_:
                        hits = [i for i, k in left if k == key]
                        if len(hits) == 1 and sum(k == key for _, k in open_) == 1:
                            matched[area] = hits[0]
                            taken.add(hits[0])
                used |= taken
                expected += [(f"{vintage} {country}", area, label) for area, label in pool if prefix != "irl"]
        if vintage != "1945":
            misses += [f"{vintage} {country} feature: {label}" for i, label in group if i not in used]
    areas_by_code = {code: area for code, _, area, _, _ in areas}
    for code, partner in SAME.items():
        if areas_by_code[partner] in matched:
            matched.setdefault(areas_by_code[code], matched[areas_by_code[partner]])
    misses += [f"{group} area: {label}" for group, area, label in expected if area not in matched]
    return matched, misses


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        areas = conn.execute(
            """select c.code, n.text, a.id, lower(c.valid), upper(c.valid) from area a
               join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred
               where a.scheme_code = 'gb.pcon' and c.code ~ '^(1885|1918|1950|1955|1974|1983):'"""
        ).fetchall()
        matched, misses = match(frame, areas)
        if misses:
            raise ValueError("unmatched:\n" + "\n".join(misses))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="UK Parliament constituency boundaries 1885–1997 in England, Wales, and Northern Ireland, and "
            "Scotland's 1885–1918, from parlconst.org",
            polity="GB",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution=ATTRIBUTION,
            notes=NOTES,
        )
        for url in json.loads((DATA / "raw" / SOURCE / "index.json").read_text()):
            cs.artifact(raw.cached(SOURCE, url), source)
        valid = {area: (start, end) for _, _, area, start, end in areas}
        ids = {code: area for code, _, area, _, _ in areas}
        rows = frame.select("geom", "label_point").rows()
        buckingham = ids["1983:eng-buckingham"]
        for area, i in sorted(matched.items(), key=lambda item: str(item[0])):
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=valid[area][0],
                valid_to=INTERIM if area == buckingham else valid[area][1],
                geom=rows[i][0],
                label_point=rows[i][1],
                quality="digitized",
                source_id=source,
            )
        ons = {
            code: (geom, point, ons_source)
            for code, geom, point, ons_source in conn.execute(
                """select c.code, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'), g.source_id
                   from area_code c join area_geometry g on g.area_id = c.area_id and g.valid @> date '1997-05-01'
                   where c.code in ('PCON01-081', 'PCON01-292', 'PCON01-315')"""
            ).fetchall()
        }
        for code, successor in GAPS.items():
            geom, point, ons_source = ons[successor]
            cs.add(
                "area_geometry",
                area_id=ids[code],
                valid_from=INTERIM,
                valid_to=valid[ids[code]][1],
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ons_source,
            )
        geom, point = conn.execute(
            """select encode(st_asewkb(shape), 'hex'), encode(st_asewkb(st_pointonsurface(shape)), 'hex')
               from (select st_multi(st_collectionextract(st_makevalid(st_intersection(
                       st_geomfromewkb(decode(%s, 'hex')), st_geomfromewkb(decode(%s, 'hex')))), 3)) as shape) cut""",
            [ons["PCON01-081"][0], rows[matched[buckingham]][0]],
        ).fetchone()
        cs.add(
            "area_geometry",
            area_id=buckingham,
            valid_from=INTERIM,
            valid_to=valid[buckingham][1],
            geom=geom,
            label_point=point,
            quality="digitized",
            source_id=source,
        )
        return cs.write()
