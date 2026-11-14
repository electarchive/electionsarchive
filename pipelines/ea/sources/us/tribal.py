import html
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.tribal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SCHEME = "us.aiannh"
LANDS = "us.tribal_land"
NOTICE = "https://www.federalregister.gov/documents/full_text/xml/2026/01/30/2026-01899.xml"
NOTICE_PAGE = "https://www.federalregister.gov/documents/2026/01/30/2026-01899/indian-entities-recognized-by-and-eligible-to-receive-services-from-the-united-states-bureau-of"
NOTICE_TITLE = "Indian Entities Recognized by and Eligible To Receive Services From the United States Bureau of Indian Affairs (91 FR 4102, January 30, 2026)"
AIANNH = "https://www2.census.gov/geo/tiger/TIGER2025/AIANNH/tl_2025_us_aiannh.zip"
AIANNH_TITLE = "TIGER/Line Shapefiles, 2025 American Indian/Alaska Native/Native Hawaiian Areas"
CLASSES = ("D2", "D3", "D5", "D8")
KEYS = {"Navajo Nation, Arizona, New Mexico, & Utah": "navajo-nation"}
DESCRIPTIVE = (
    "previously listed",
    "aka ",
    "See ",
    "Six component",
    "Four constituent",
    "includes ",
    "Barona Group",
    "Cedar Band",
    "Carson Colony",
    "St. George Island and",
    "Arctic Village and",
)
FORMER = re.compile(r"(?:previously listed as|aka) (.+)")
TRIBES = {
    "0080": ["Seneca Nation of Indians"],
    "0115": ["Mi'kmaq Nation"],
    "0225": ["Seminole Tribe of Florida"],
    "0360": ["Seminole Tribe of Florida"],
    "0435": ["Cahuilla Band of Indians"],
    "0540": ["Seneca Nation of Indians"],
    "0690": ["Seminole Tribe of Florida"],
    "0795": ["Coushatta Tribe of Louisiana"],
    "0845": ["Crow Tribe of Montana"],
    "1070": ["Paiute-Shoshone Tribe of the Fallon Reservation and Colony, Nevada"],
    "1075": ["Paiute-Shoshone Tribe of the Fallon Reservation and Colony, Nevada"],
    "1260": ["Seminole Tribe of Florida"],
    "1340": ["Confederated Tribes of the Goshute Reservation, Nevada and Utah"],
    "1475": ["Seminole Tribe of Florida"],
    "1530": ["Houlton Band of Maliseet Indians"],
    "1550": ["Nottawaseppi Huron Band of the Potawatomi, Michigan"],
    "1555": ["Seminole Tribe of Florida"],
    "1575": ["Passamaquoddy Tribe"],
    "1590": ["Iowa Tribe of Kansas and Nebraska"],
    "1610": ["Saginaw Chippewa Indian Tribe of Michigan"],
    "1770": ["Kickapoo Tribe of Indians of the Kickapoo Reservation in Kansas"],
    "1775": ["Kickapoo Traditional Tribe of Texas"],
    "1800": ["Kootenai Tribe of Idaho"],
    "1880": ["Keweenaw Bay Indian Community, Michigan"],
    "1960": ["Little River Band of Ottawa Indians, Michigan"],
    "2100": ["Manchester Band of Pomo Indians of the Manchester Rancheria, California"],
    "2130": ["Ak-Chin Indian Community"],
    "2300": ["Mississippi Band of Choctaw Indians"],
    "2495": ["Northfork Rancheria of Mono Indians of California"],
    "2505": ["Northwestern Band of the Shoshone Nation"],
    "2535": ["Seneca Nation of Indians"],
    "2560": ["Oneida Nation"],
    "2580": ["Keweenaw Bay Indian Community, Michigan"],
    "2625": ["Paiute Indian Tribe of Utah"],
    "2680": ["Pascua Yaqui Tribe of Arizona"],
    "2715": ["Pauma Band of Luiseno Mission Indians of the Pauma & Yuima Reservation, California"],
    "2810": ["Oglala Sioux Tribe"],
    "2850": ["Passamaquoddy Tribe"],
    "2865": ["Poarch Band of Creek Indians"],
    "2900": ["Ponca Tribe of Nebraska"],
    "2980": ["Prairie Band Potawatomi Nation"],
    "3015": ["Eastern Band of Cherokee Indians"],
    "3265": ["Yocha Dehe Wintun Nation, California"],
    "3280": ["Sac & Fox Tribe of the Mississippi in Iowa"],
    "3285": ["Sac & Fox Nation of Missouri in Kansas and Nebraska"],
    "3320": ["Saint Regis Mohawk Tribe"],
    "3520": ["Santa Rosa Indian Community of the Santa Rosa Rancheria, California"],
    "3525": ["Santa Rosa Band of Cahuilla Indians, California"],
    "3565": ["Santee Sioux Nation, Nebraska"],
    "3665": ["Seminole Tribe of Florida"],
    "3695": ["Shawnee Tribe"],
    "3855": ["Tolowa Dee-ni' Nation"],
    "4095": ["Wiyot Tribe, California"],
    "4130": ["Seminole Tribe of Florida"],
    "4180": ["Timbisha Shoshone Tribe"],
    "4390": ["Ute Indian Tribe of the Uintah & Ouray Reservation, Utah"],
    "4530": ["Wampanoag Tribe of Gay Head (Aquinnah)"],
    "4610": [
        "Eastern Shoshone Tribe of the Wind River Reservation, Wyoming",
        "Northern Arapaho Tribe of the Wind River Reservation, Wyoming",
    ],
    "4545": ["Confederated Tribes of the Warm Springs Reservation of Oregon"],
    "4760": ["Yurok Tribe of the Yurok Reservation, California"],
}
UNHELD = {"0560"}


def fetch() -> None:
    raw.fetch(SOURCE, NOTICE, suffix=".xml")
    raw.fetch(SOURCE, AIANNH, suffix=".zip")


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def entities() -> list[dict[str, str | list[str]]]:
    text = raw.cached(SOURCE, NOTICE).path.read_text()
    out: list[dict[str, str | list[str]]] = []
    for region, part in zip(("contiguous", "alaska"), re.split(r'<HD SOURCE="HD1">', text)[1:], strict=True):
        for line in re.findall(r"<FP[^>]*>(.*?)</FP>", part, re.DOTALL):
            listed = html.unescape(" ".join(re.sub(r"<[^>]+>", "", line).split()))
            name, former = listed, []
            while (found := re.search(r" \(((?:[^()]|\([^()]*\))*)\)$", name)) and found[1].strip().startswith(
                DESCRIPTIVE
            ):
                name = name[: found.start()]
                former += [
                    re.sub(r"\s+", " ", match.group(1)).strip()
                    for match in [FORMER.match(found[1].strip())]
                    if match and "See " not in match.group(1)
                ]
            out.append({"listed": listed, "name": name, "region": region, "former": former})
    return out


def parse() -> None:
    tribes = entities()
    names = {str(tribe["listed"]): str(tribe["name"]) for tribe in tribes}
    with zipfile.ZipFile(raw.cached(SOURCE, AIANNH).path) as archive:
        files = {member.rsplit(".", 1)[1].lower(): archive.read(member) for member in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(files["shp"]), shx=io.BytesIO(files["shx"]), dbf=io.BytesIO(files["dbf"]))
    parts: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record is None or shape is None or record["AIANNHR"] != "F" or record["CLASSFP"] not in CLASSES:
            continue
        if str(record["AIANNHCE"]) in UNHELD:
            continue
        parts[str(record["AIANNHCE"])].append(
            (str(record["COMPTYP"]), str(record["NAMELSAD"]), str(record["NAME"]), json.dumps(shape.__geo_interface__))
        )
    areas, links = [], []
    for code, pieces in sorted(parts.items()):
        pieces.sort()
        label = pieces[0][1]
        if len(pieces) == 2:
            label = f"{label} and Off-Reservation Trust Land"
        if code in TRIBES:
            owners = [next(listed for listed, name in names.items() if name == want) for want in TRIBES[code]]
        else:
            core = fold(re.sub(r"\((?:state|[A-Z]{2})\)", "", pieces[0][2]))
            owners = [listed for listed in names if re.search(rf"\b{re.escape(core)}\b", fold(listed))]
            if len(owners) != 1:
                raise ValueError(f"{code} {label}: {len(owners)} BIA entities match")
        areas.append((code, label, [geojson for *_, geojson in pieces]))
        links += [(code, owner) for owner in owners]
    with connect() as conn:
        geometry = conn.execute(
            """select f.code, encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
               from (select code, array_agg(geojson) as shapes from unnest(%s::text[], %s::text[]) as u(code, geojson)
                     group by code) f
               cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(st_setsrid(
                 st_union(array(select st_makevalid(st_geomfromgeojson(g)) from unnest(f.shapes) g)), 4269), 4326)), 3))
                 as geom) s""",
            [
                [code for code, _, shapes in areas for _ in shapes],
                [shape for _, _, shapes in areas for shape in shapes],
            ],
        ).fetchall()
        shapes = {code: (geom, point) for code, geom, point in geometry}
        owned: dict[str, list[str]] = defaultdict(list)
        for code, owner in links:
            owned[owner].append(code)
        several = {owner: codes for owner, codes in owned.items() if len(codes) > 1}
        lands = conn.execute(
            """select f.owner, encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
               from (select owner, array_agg(geom::geometry) as shapes from unnest(%s::text[], %s::text[]) as u(owner, geom)
                     group by owner) f
               cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_union(f.shapes)), 3)) as geom) s""",
            [
                [owner for owner, codes in several.items() for _ in codes],
                [shapes[code][0] for codes in several.values() for code in codes],
            ],
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [(code, label, *shapes[code]) for code, label, _ in areas],
        schema={"code": pl.String, "name": pl.String, "geom": pl.String, "point": pl.String},
        orient="row",
    ).write_parquet(PARSED / "areas.parquet")
    pl.DataFrame(
        lands, schema={"listed": pl.String, "geom": pl.String, "point": pl.String}, orient="row"
    ).write_parquet(PARSED / "lands.parquet")
    pl.DataFrame(links, schema={"code": pl.String, "listed": pl.String}, orient="row").write_parquet(
        PARSED / "links.parquet"
    )
    pl.DataFrame(
        tribes, schema={"listed": pl.String, "name": pl.String, "region": pl.String, "former": pl.List(pl.String)}
    ).write_parquet(PARSED / "tribes.parquet")
    print(
        f"{len(tribes)} entities, {len(areas)} areas, {len(links)} links, {len({o for _, o in links})} nations with land"
    )


def emit() -> Path:
    tribes = pl.read_parquet(PARSED / "tribes.parquet")
    areas = pl.read_parquet(PARSED / "areas.parquet")
    links = pl.read_parquet(PARSED / "links.parquet")
    lands = {row["listed"]: row for row in pl.read_parquet(PARSED / "lands.parquet").iter_rows(named=True)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"{tribes.height} federally recognized American Indian and Alaska Native tribal nations from the Bureau of Indian Affairs list of January 30, 2026, as tribal_nation polities under the US, with {areas.height} federal reservations and off-reservation trust lands from the Census Bureau's 2025 TIGER/Line AIANNH layer as {SCHEME} areas",
            polity="US",
        )

        def name(entity: str, text: str, kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=preferred)

        us = cs.polity("US")
        notice = cs.source("Bureau of Indian Affairs", NOTICE_TITLE)
        if notice in cs.minted:
            cs.add(
                "source",
                id=notice,
                kind="official_body",
                publisher="Bureau of Indian Affairs",
                title=NOTICE_TITLE,
                url=NOTICE_PAGE,
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, NOTICE), notice)
        census = cs.source("U.S. Census Bureau", AIANNH_TITLE)
        if census in cs.minted:
            cs.add(
                "source",
                id=census,
                kind="official_body",
                publisher="U.S. Census Bureau",
                title=AIANNH_TITLE,
                url="https://www2.census.gov/geo/tiger/TIGER2025/AIANNH/",
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, AIANNH), census)
        if conn.execute("select 1 from scheme where code = %s", [SCHEME]).fetchone() is None:
            cs.add("scheme", code=SCHEME, polity_id=us, kind="administrative", rank=3, noun="reservation")
        if conn.execute("select 1 from scheme where code = %s", [LANDS]).fetchone() is None:
            cs.add("scheme", code=LANDS, polity_id=us, kind="synthetic", rank=3, noun="tribal land")
        for scheme in (SCHEME, LANDS):
            if conn.execute("select 1 from boundary_set where scheme_code = %s", [scheme]).fetchone() is None:
                cs.add(
                    "boundary_set",
                    scheme_code=scheme,
                    vintage="2025",
                    valid_from=None,
                    valid_to=None,
                    as_of=date(2025, 1, 1),
                )
        slugs = {path for (path,) in conn.execute("select path from slug where path like 'us/%%'").fetchall()}
        polities: dict[str, str] = {}
        territories: dict[str, str] = {}
        created = 0
        for row in tribes.iter_rows(named=True):
            base = KEYS.get(row["name"]) or slug(row["name"].removeprefix("The "))
            polity = cs.key("us.polity", f"tribal:{base}")
            polities[row["listed"]] = str(polity)
            if row["listed"] in lands:
                land = cs.area(LANDS, base)
                territories[row["listed"]] = str(land)
                if land in cs.minted:
                    cs.add("area", id=land, scheme_code=LANDS)
                    cs.add("area_code", area_id=land, code=base, valid_from=None, valid_to=None)
                    name(str(land), f"{row['name']} lands")
                    cs.add(
                        "area_geometry",
                        area_id=land,
                        valid_from=None,
                        geom=lands[row["listed"]]["geom"],
                        label_point=lands[row["listed"]]["point"],
                        quality="official",
                        source_id=census,
                    )
                    cs.add("polity_territory", polity_id=polity, area_id=land)
            if polity not in cs.minted:
                continue
            path = f"us/{base}"
            if path in slugs:
                raise ValueError(f"{path} is taken")
            slugs.add(path)
            cs.add("polity", id=polity, kind="tribal_nation", iso_code=None)
            name(str(polity), row["name"])
            for former in row["former"]:
                name(str(polity), former, "historical", False)
            cs.add("polity_parent", polity_id=polity, parent_id=us)
            cs.add("slug", path=path, entity_id=polity)
            cs.add("citation", entity_id=polity, source_id=notice, locator={"entry": row["listed"]})
            created += 1
        for row in areas.iter_rows(named=True):
            area = cs.area(SCHEME, row["code"])
            if area not in cs.minted:
                continue
            cs.add("area", id=area, scheme_code=SCHEME)
            cs.add("area_code", area_id=area, code=row["code"], valid_from=None, valid_to=None)
            name(str(area), row["name"])
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=row["geom"],
                label_point=row["point"],
                quality="official",
                source_id=census,
            )
            for listed in links.filter(pl.col("code") == row["code"]).get_column("listed"):
                if listed in territories:
                    cs.add("area_membership", area_id=area, parent_id=territories[listed], valid_from=None)
                else:
                    cs.add("polity_territory", polity_id=polities[listed], area_id=area)
        print(
            f"{created} polities minted, {tribes.height - created} already held, {areas.height} areas, {len(lands)} composite lands"
        )
        return cs.write()
