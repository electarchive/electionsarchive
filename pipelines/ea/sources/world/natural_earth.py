import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "world.natural_earth"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson"
PLACES = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_populated_places.geojson"
DISPUTED = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_disputed_areas.geojson"
PAGE = "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/"
MOVED = {"B89": "UKR", "B16": "SYR", "B19": "ESH", "B98": "PSE"}
FOLDED = {
    "SOL": ("SOM", "Somaliland is drawn within Somalia, per UN recognition"),
    "KAS": ("IND", "Siachen is drawn within India, its administrator"),
}
DISPLAY = {"CYN": "Northern Cyprus"}
STATUS = {
    "UKR": "Includes Crimea, which Russia has administered since 2014, and the parts of Donetsk, Luhansk, Zaporizhzhia, "
    "and Kherson oblasts that Russia has occupied since 2022. The UN regards all of them as part of Ukraine (General "
    "Assembly Resolutions 68/262 and ES-11/4).",
    "SYR": "Includes the Golan Heights, which Israel has administered since 1967. The UN regards them as part of Syria "
    "(Security Council Resolution 497).",
    "ESH": "Morocco administers most of Western Sahara and claims all of it; the Sahrawi Arab Democratic Republic "
    "administers the area east of the berm. The UN lists Western Sahara as a non-self-governing territory.",
    "MAR": "Western Sahara is drawn separately. Morocco administers most of it and claims all of it.",
    "PSE": "Includes East Jerusalem, which Israel administers and claims. The UN regards it as occupied Palestinian "
    "territory.",
    "SOM": "Includes Somaliland, which has governed itself since 1991. Somalia claims it, and the UN regards it as part "
    "of Somalia.",
    "GEO": "Includes Abkhazia and South Ossetia, which govern themselves with Russian backing. Georgia claims both, and "
    "the UN regards them as part of Georgia.",
    "MDA": "Includes Transnistria, which has governed itself since 1990. Moldova claims it, and the UN regards it as "
    "part of Moldova.",
    "IND": "India administers Jammu and Kashmir and Ladakh, including the Siachen Glacier, which Pakistan claims, and Arunachal Pradesh, which China claims. The final status of Jammu and Kashmir has not been agreed.",
    "PAK": "Pakistan administers Azad Kashmir and Gilgit-Baltistan, which India claims. The final status of Jammu and Kashmir has not been agreed.",
    "CHN": "China administers Aksai Chin and the Shaksgam Valley, which India claims. The final status of Jammu and Kashmir has not been agreed.",
    "TWN": "Disputed status. Taiwan governs itself and holds its own elections; the People's Republic of China claims "
    "it. The UN seats the People's Republic of China (General Assembly Resolution 2758).",
    "KOS": "Disputed status. Kosovo declared independence from Serbia in 2008 and holds its own elections; Serbia "
    "claims it, and UN members are divided on recognizing it.",
    "CYN": "Turkish Republic of Northern Cyprus (Kuzey Kıbrıs Türk Cumhuriyeti). Recognized only by Turkey; the UN "
    "regards it as part of Cyprus (Security Council Resolution 541, 1983).",
    "CNM": "Administered by the UN Peacekeeping Force in Cyprus.",
}
CITIES = {
    "SANJOSE": "CRI",
    "ANDORRE": "AND",
    "ASSOMPTION": "PRY",
    "EKATERINBOURG": "RUS",
    "TANANARIVE": "MDG",
    "BAHAMASNASSAU": "BHS",
}
UNION_NOTE = (
    "This shape is the union of Natural Earth's outlines of the countries whose polling stations vote in this "
    "constituency. The decree that draws the constituency lists countries; it draws no boundary."
)
LANGS = {
    "NAME_EN": "en",
    "NAME_FR": "fr",
    "NAME_IT": "it",
    "NAME_PT": "pt",
    "NAME_DE": "de",
    "NAME_ES": "es",
    "NAME_TR": "tr",
}


def fold(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper())


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".geojson")
    raw.fetch(SOURCE, PLACES, suffix=".geojson")
    raw.fetch(SOURCE, DISPUTED, suffix=".geojson")


def parse() -> None:
    features = json.loads(raw.cached(SOURCE, URL).path.read_bytes())["features"]
    rows: list[dict[str, str | None]] = []
    for feature in features:
        properties = feature["properties"]
        code = properties["ISO_A3"] if properties["ISO_A3"] != "-99" else properties["ADM0_A3"]
        rows.append(
            {
                "code": code,
                "continent": properties["CONTINENT"],
                "geojson": json.dumps(feature["geometry"]),
                **{lang: properties[field] or None for field, lang in LANGS.items()},
            }
        )
    frame = pl.DataFrame(rows)
    if frame["code"].n_unique() != frame.height:
        raise ValueError("country codes are not unique")
    moved = [
        (MOVED[feature["properties"]["BRK_A3"]], json.dumps(feature["geometry"]))
        for feature in json.loads(raw.cached(SOURCE, DISPUTED).path.read_bytes())["features"]
        if feature["properties"]["BRK_A3"] in MOVED
    ]
    if len(moved) != len(MOVED):
        raise ValueError("a disputed area to move is missing from Natural Earth's layer")
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        conn.execute("create temp table moved (code text, geojson text) on commit drop")
        conn.execute("create temp table folded (code text, target text) on commit drop")
        with conn.cursor() as cursor:
            with cursor.copy("copy feature (code, geojson) from stdin") as copy:
                for code, geojson in frame.select("code", "geojson").iter_rows():
                    copy.write_row((code, geojson))
            with cursor.copy("copy moved (code, geojson) from stdin") as copy:
                for row in moved:
                    copy.write_row(row)
            with cursor.copy("copy folded (code, target) from stdin") as copy:
                for code, (target, _) in FOLDED.items():
                    copy.write_row((code, target))
        shapes = conn.execute(
            """with base as (
                 select coalesce(f.target, b.code) as code,
                        st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(b.geojson), 4326))) as geom
                 from feature b left join folded f on f.code = b.code
               ),
               moved as (
                 select code, st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 4326))) as geom
                 from moved
               ),
               cut as (select st_union(geom) as geom from moved),
               pieces as (
                 select b.code, case when st_intersects(b.geom, cut.geom) then st_difference(b.geom, cut.geom)
                                     else b.geom end as geom
                 from base b cross join cut
                 union all
                 select code, geom from moved
               ),
               shapes as (
                 select code, st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom
                 from pieces group by code
               )
               select code, encode(st_asewkb(geom), 'hex'),
                      encode(st_asewkb((select st_pointonsurface(d.geom) from st_dump(geom) d
                                        order by st_area(d.geom::geography) desc limit 1)), 'hex')
               from shapes"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    places: dict[str, tuple[str, int]] = {}
    for feature in json.loads(raw.cached(SOURCE, PLACES).path.read_bytes())["features"]:
        properties = feature["properties"]
        for field in ("NAME_FR", "NAME", "NAMEASCII", "NAME_EN"):
            if properties.get(field):
                key = fold(properties[field])
                if key not in places or (properties["POP_MAX"] or 0) > places[key][1]:
                    places[key] = (properties["ADM0_A3"], properties["POP_MAX"] or 0)
    pl.DataFrame(
        [(key, country) for key, (country, _) in places.items()], schema=["city", "country"], orient="row"
    ).write_parquet(PARSED / "places.parquet")
    frame.drop("geojson").filter(~pl.col("code").is_in(list(FOLDED))).join(
        pl.DataFrame(shapes, schema=["code", "geom", "label_point"], orient="row"), on="code", how="left"
    ).write_parquet(PARSED / "countries.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "countries.parquet")
    if frame["geom"].null_count():
        raise ValueError("a country has no shape")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The world's countries and dependencies from Natural Earth's 1:10m admin-0 countries",
            polity=None,
        )
        source = cs.source("Natural Earth", "Admin 0 – Countries, 1:10m")
        disputed = cs.source("Natural Earth", "Admin 0 – Breakaway, disputed areas, 1:10m")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Natural Earth",
            title="Admin 0 – Countries, 1:10m",
            url=PAGE,
            license="Public domain",
            license_url="https://www.naturalearthdata.com/about/terms-of-use/",
            attribution="Made with Natural Earth",
        )
        cs.add(
            "source",
            id=disputed,
            kind="community",
            publisher="Natural Earth",
            title="Admin 0 – Breakaway, disputed areas, 1:10m",
            url="https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-breakaway-disputed-areas/",
            license="Public domain",
            license_url="https://www.naturalearthdata.com/about/terms-of-use/",
            attribution="Made with Natural Earth",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        cs.artifact(raw.cached(SOURCE, PLACES), source)
        cs.artifact(raw.cached(SOURCE, DISPUTED), disputed)
        cs.add("scheme", code="world.country", polity_id=None, kind="administrative", rank=0, noun="country")
        for row in frame.iter_rows(named=True):
            area = cs.area("world.country", row["code"])
            cs.add("area", id=area, scheme_code="world.country")
            cs.add("area_code", area_id=area, code=row["code"], valid_from=None)
            for lang in LANGS.values():
                if row[lang] is not None:
                    cs.add(
                        "name",
                        entity_id=area,
                        text=row[lang],
                        lang=lang,
                        kind="official",
                        is_preferred=lang == "en" and row["code"] not in DISPLAY,
                    )
            if row["code"] in DISPLAY:
                cs.add("name", entity_id=area, text=DISPLAY[row["code"]], lang="en", kind="english", is_preferred=True)
            if row["code"] in STATUS:
                cs.add("note", entity_id=area, kind="context", body_md=STATUS[row["code"]])
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=row["geom"],
                label_point=row["label_point"],
                quality="academic",
                source_id=source,
            )
        cs.add("boundary_set", scheme_code="world.country", vintage="5.1", valid_from=None, as_of=date(2026, 10, 26))
        for note, entity, body, code in conn.execute(
            """select n.id, n.entity_id, n.body_md, c.code from note n
               join area a on a.id = n.entity_id and a.scheme_code = 'world.country'
               join area_code c on c.area_id = a.id
               join changeset w on w.id = n.changeset_id and w.pipeline = %s
               where n.kind = 'context'""",
            [SOURCE],
        ).fetchall():
            if STATUS.get(code) != body:
                cs.add(
                    "note_drop",
                    note_id=note,
                    entity_id=entity,
                    body_md=body,
                    reason="the area's status note was rewritten",
                )
        for code, (_, reason) in FOLDED.items():
            try:
                folded = cs.area("world.country", code)
            except ValueError:
                continue
            cs.add("entity_retirement", entity_id=folded, reason=reason)
        areas = {row["code"]: cs.area("world.country", row["code"]) for row in frame.iter_rows(named=True)}
        shapes = {areas[code]: geom for code, geom in frame.select("code", "geom").iter_rows()}
        aliases = {"SDS": "SSD", "PSX": "PSE", "SAH": "ESH"}
        cities = {
            city: aliases.get(country, country)
            for city, country in pl.read_parquet(PARSED / "places.parquet").iter_rows()
        } | CITIES
        constituencies: dict[UUID, set[UUID]] = {}
        for unit, text, constituency in conn.execute(
            """select r.id, n.text, m.area_id from reporting_unit r join name n on n.entity_id = r.id
               join reporting_unit_area m on m.unit_id = r.id join area a on a.id = m.area_id
               where r.scheme_code = 'fr.bureau' and r.kind = 'abroad_mission' and a.scheme_code = 'fr.circonscription'
               order by r.code"""
        ).fetchall():
            city = fold(text.split(", bureau")[0])
            if city not in cities or cities[city] not in areas:
                raise ValueError(f"no country for the French polling station in {text}")
            country = areas[cities[city]]
            cs.add("reporting_unit_area", unit_id=unit, area_id=country, valid_from=None, method="source_code")
            constituencies.setdefault(constituency, set()).add(country)
        for constituency, countries in constituencies.items():
            geom, point = conn.execute(
                """with u as (
                     select st_multi(st_collectionextract(st_makevalid(st_union(st_geomfromewkb(decode(g, 'hex')))), 3))
                              as geom
                     from unnest(%s::text[]) as g
                   )
                   select encode(st_asewkb(geom), 'hex'),
                          encode(st_asewkb((select st_pointonsurface(d.geom) from st_dump(geom) d
                                            order by st_area(d.geom::geography) desc limit 1)), 'hex')
                   from u""",
                [[shapes[country] for country in countries]],
            ).fetchall()[0]
            cs.add(
                "area_geometry",
                area_id=constituency,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="academic",
                source_id=source,
            )
            cs.add("note", entity_id=constituency, kind="methodology", body_md=UNION_NOTE)
        return cs.write()
