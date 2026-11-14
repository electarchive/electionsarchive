import html
import json
import re
import sys
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.licenses import COUNCIL, PUBLIC_RECORD
from ea.store import DATA, connect

SOURCE = "gb.parish"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LAYERS = {
    2023: ("Parishes_May_2023_Boundaries_EW_BGC", "PAR23", "NMW", 11344, date(2023, 5, 4)),
    2024: ("Parishes_May_2024_Boundaries_EW_BGC", "PAR24", "NMW", 11337, date(2024, 5, 2)),
    2025: ("PARNCP_MAY_2025_EW_BGC", "PARNCP25", "NW", 11539, date(2025, 5, 1)),
    2026: ("PARNCP_MAY_2026_EW_BGC", "PARNCP26", "NW", 11542, date(2026, 5, 7)),
}
PAGE = 1000
TOLERANCE = 0.005
PUBLISHER = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)
NOTE = (
    "Every civil parish in England and community in Wales, from the ONS parish boundary layers for the May 2023, "
    "2024, 2025, and 2026 elections (generalized to 20 m and clipped to the coastline). A shape runs from the first "
    "layer that shows it until a layer redraws it by more than half a percent of its area. Each parish is a polity "
    "because every English parish has a parish meeting (Local Government Act 1972, s. 9) and every Welsh community "
    "can hold a community meeting (s. 27). ONS does not say which parishes have a council rather than only a "
    "meeting, and it publishes no parish wards, so neither councils nor parish wards are recorded here."
)
PRECEPTS = (
    "https://statswales.gov.wales/Catalogue/Local-Government/Finance/Council-Tax/Levels/"
    "community-council-precepts-by-community-council"
)
PRECEPTS_COPY = f"https://web.archive.org/web/20250910183531id_/{PRECEPTS}"
PRECEPTS_NOTE = (
    "StatsWales table LGFS0036, community council precepts and tax bases for 2019-20 to 2022-23, read from the "
    "copy archived on 10 September 2025 after the table stopped being updated. Each council with a 2022-23 precept "
    "is a council body of the community it serves, or of the group of communities it serves where the principal "
    "council has grouped several communities under one council."
)
AUTHORITIES = {"Rhondda Cynon Taff": "Rhondda Cynon Taf"}
RENAMED = {
    ("Flintshire", "Gwernaffield"): "Gwernaffield with Pantymwyn",
    ("Flintshire", "Leeswood"): "Leeswood and Pontblyddyn",
    ("Gwynedd", "Abergwyngregyn"): "Aber",
    ("Gwynedd", "Clynnog Fawr"): "Clynnog",
    ("Gwynedd", "Llangelynin"): "Llangelynnin",
    ("Gwynedd", "Llangywair"): "Llangywer",
    ("Monmouthshire", "Llanover"): "Gobion Fawr",
    ("Monmouthshire", "Tintern"): "Tyndyrn",
    ("Neath Port Talbot", "Gwaencaegurwen"): "Gwaun-Cae-Gurwen",
    ("Newport", "Michaelstone"): "Michaelstone-y-Fedw",
    ("Pembrokeshire", "Scleddau (merged with Trecwn)"): "Scleddau",
    ("Vale of Glamorgan", "Michaelston"): "Michaelston-le-Pit and Leckwith",
    ("Vale of Glamorgan", "St George's and St Bride's Super Ely"): "St. Georges-super-Ely",
    ("Vale of Glamorgan", "Sully"): "Sully and Lavernock",
}
GROUPED = {
    ("Denbighshire", "Tremeirchion/Cwm/Waen"): ("Tremeirchion, Cwm and Waen", ("Tremeirchion", "Cwm", "Waen")),
    ("Torfaen", "Croesyceiliog & Llanyrafon"): ("Croesyceiliog and Llanyrafon", ("Croesyceiliog", "Llanyrafon")),
    ("Torfaen", "Cwmbran"): ("Cwmbran", ("Cwmbran Central", "Fairwater", "Llantarnam", "Pontnewydd", "Upper Cwmbran")),
    ("Torfaen", "Pontypool"): (
        "Pontypool",
        ("Abersychan", "New Inn", "Panteg", "Pen Tranch", "Pontymoile", "Trevethin"),
    ),
}
GROUP_SCHEME = "gb.community_group"
ENGLISH = Path(__file__).with_name("parish_councils.tsv")
ENGLISH_INBOX = DATA / "inbox" / SOURCE / "councils"
ELECTIONS = "gb.parish_elections"
TORFAEN_RESULTS = "https://www.torfaen.gov.uk/en/about-the-council/elections/elections-results/community-council"
DENBIGHSHIRE_COUNCIL = "https://moderngov.denbighshire.gov.uk/mgParishCouncilDetails.aspx?ID=267&LLL=0"
GROUP_SOURCES = {
    "Torfaen": [
        ("Torfaen County Borough Council", "Community Council Election Results", TORFAEN_RESULTS, TORFAEN_RESULTS),
        (
            "legislation.gov.uk",
            "The County Borough of Torfaen (Electoral Changes) Order 2002",
            "https://www.legislation.gov.uk/wsi/2002/3279/made",
            "https://www.legislation.gov.uk/wsi/2002/3279/made/data.xml",
        ),
        (
            "legislation.gov.uk",
            "The County Borough of Torfaen (Electoral Arrangements) Order 2021",
            "https://www.legislation.gov.uk/wsi/2021/1139/made",
            "https://www.legislation.gov.uk/wsi/2021/1139/made/data.xml",
        ),
    ],
    "Denbighshire": [
        (
            "Denbighshire County Council",
            "City, Town and Community Councils: Tremeirchion Cwm & Waen",
            DENBIGHSHIRE_COUNCIL,
            f"https://web.archive.org/web/20250814134725id_/{DENBIGHSHIRE_COUNCIL}",
        ),
    ],
}
GROUP_EVIDENCE = {
    "Torfaen": (
        "Torfaen's community council results list each council's wards, and the County Borough of Torfaen "
        "(Electoral Changes) Order 2002 and (Electoral Arrangements) Order 2021 place those wards in their communities"
    ),
    "Denbighshire": (
        "Denbighshire's register of city, town, and community councils lists one Tremeirchion Cwm & Waen council, "
        "and the Welsh Government's precept table shows no separate council for any of the three communities"
    ),
}


def pages(year: int) -> list[str]:
    service, prefix, _, count, _ = LAYERS[year]
    return [
        f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&orderByFields={prefix}CD"
        f"&resultOffset={offset}&resultRecordCount={PAGE}&f=geojson"
        for offset in range(0, count, PAGE)
    ]


def fetch() -> None:
    for year in LAYERS:
        for url in pages(year):
            raw.fetch(SOURCE, url, suffix=".geojson")
    raw.fetch(SOURCE, PRECEPTS_COPY, suffix=".html")
    for entries in GROUP_SOURCES.values():
        for _, _, _, copy in entries:
            raw.fetch(SOURCE, copy, suffix=".xml" if copy.endswith(".xml") else ".html")
    for file in sorted(ENGLISH_INBOX.glob("*.html")):
        content = file.read_bytes()
        url = re.match(rb"<!-- URL: (\S+) -->", content)
        if url is None:
            raise ValueError(f"{file} has no URL header")
        raw.store(
            SOURCE,
            url.group(1).decode(),
            content,
            suffix=".html",
            retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
            media_type="text/html",
        )


def parse_precepts() -> pl.DataFrame:
    text = re.sub(
        r"<script.*?</script>|<style.*?</style>",
        "",
        raw.cached(SOURCE, PRECEPTS_COPY).path.read_text(),
        flags=re.DOTALL,
    )
    cells = [cell.strip() for cell in re.split(r"(?:\s*\|\s*)+", html.unescape(re.sub(r"<[^>]+>", "|", text)))]
    figure = re.compile(r"[\d,.]+|\.")
    rows = []
    for index, cell in enumerate(cells):
        values = cells[index + 1 : index + 13]
        if " - " in cell and len(values) == 12 and all(figure.fullmatch(value) for value in values):
            authority, name = cell.split(" - ", 1)
            if values[3] != ".":
                rows.append(
                    {
                        "authority": AUTHORITIES.get(authority, authority),
                        "council": re.sub(r"\s+", " ", name),
                        "precept": int(values[3].replace(",", "")),
                        "tax_base": int(values[7].replace(",", "")),
                    }
                )
    if len(rows) != 728:
        raise ValueError(f"{len(rows)} community councils with a 2022-23 precept, expected 728")
    return pl.DataFrame(rows)


def parse() -> None:
    names: list[dict[str, object]] = []
    with connect() as conn:
        conn.execute("create temp table feature (year int, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for year, (_, prefix, welsh, count, _) in LAYERS.items():
                rows = [
                    feature
                    for url in pages(year)
                    for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
                ]
                if len(rows) != count:
                    raise ValueError(f"{year}: {len(rows)} features, expected {count}")
                for feature in rows:
                    properties = feature["properties"]
                    code = properties[f"{prefix}CD"]
                    if code[:3] not in ("E04", "W04"):
                        continue
                    names.append(
                        {
                            "year": year,
                            "code": code,
                            "name": properties[f"{prefix}NM"],
                            "name_cy": properties[f"{prefix}{welsh}"] or None,
                            "lad": properties.get("LAD26CD"),
                        }
                    )
                    copy.write_row((year, code, json.dumps(feature["geometry"])))
        conn.execute(
            """create temp table shape on commit drop as
               select year, code, st_multi(st_collectionextract(st_makevalid(st_union(
                        st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
               from feature group by year, code"""
        )
        rows = conn.execute(
            """select year, code, st_area(geom), encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex'),
                      (select st_area(st_symdifference(geom, before.geom)) / nullif(st_area(geom), 0)
                       from shape before where before.code = shape.code and before.year < shape.year
                       order by before.year desc limit 1)
               from shape order by code, year"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "code": pl.String,
            "area": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
            "change": pl.Float64,
        },
        orient="row",
    ).join(pl.DataFrame(names, infer_schema_length=None), on=["year", "code"])
    print(frame.group_by("year").len().sort("year").rows(), file=sys.stderr)
    print(
        frame.filter(pl.col("change").is_not_null())
        .select(pl.col("change").quantile(q).alias(str(q)) for q in (0.5, 0.9, 0.99, 0.999))
        .rows(),
        file=sys.stderr,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "parishes.parquet")
    parse_precepts().write_parquet(PARSED / "councils.parquet")


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded.replace("&", " and ")).strip("-")


def fold(text: str) -> str:
    return re.sub(r"\b(?:town|city|and)\b|-", "", slug(text))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "parishes.parquet").sort("code", "year")
    years = sorted(LAYERS)
    if frame["change"].fill_null(0).max() != 0:
        raise ValueError("a layer redraws a parish under its old code")
    spans: dict[str, tuple[int, int]] = {}
    for code, first, last in (
        frame.group_by("code").agg(pl.col("year").min().alias("first"), pl.col("year").max().alias("last")).iter_rows()
    ):
        if (last - first + 1) != frame.filter(pl.col("code") == code).height:
            raise ValueError(f"{code} skips a layer")
        spans[code] = (first, last)
    latest = {
        row["code"]: row
        for row in frame.filter(pl.col("year") == pl.col("year").max().over("code")).iter_rows(named=True)
    }
    with connect() as conn:
        orphans = [(code, row["year"], row["label_point"]) for code, row in latest.items() if row["lad"] is None]
        lads = dict(
            conn.execute(
                """select distinct on (f.code) f.code, c.code
                   from unnest(%s::text[], %s::int[], %s::text[]) f(code, year, point)
                   join area a on a.scheme_code = 'gb.lad' join area_code c on c.area_id = a.id
                   join area_geometry g on g.area_id = a.id and g.valid @> make_date(f.year, 5, 1)
                   where st_intersects(g.geom, st_geomfromewkb(decode(f.point, 'hex')))
                   order by f.code, st_distance(st_centroid(g.geom), st_geomfromewkb(decode(f.point, 'hex')))""",
                [[orphan[index] for orphan in orphans] for index in range(3)],
            ).fetchall()
        )
        lads |= dict(
            conn.execute(
                """select c.code, l.code from area a join area_code c on c.area_id = a.id
                   join area_parent p on p.area_id = a.id join area_code l on l.area_id = p.parent_id
                   where a.scheme_code = 'gb.parish'"""
            ).fetchall()
        )
        lads |= {code: row["lad"] for code, row in latest.items() if row["lad"] is not None}
        if missing := sorted(latest.keys() - lads.keys()):
            raise ValueError(f"no council area for {', '.join(missing)}")
        councils = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity'"""
            ).fetchall()
        )
        taken = dict(conn.execute("select path, entity_id from slug where path like 'gb/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Every civil parish in England and community in Wales, May 2023 to May 2026, from ONS",
            polity="GB",
        )

        def name(entity: object, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.parish'").fetchone() is None:
            cs.add("scheme", code="gb.parish", polity_id=gb, kind="administrative", rank=4, noun="parish or community")
        sources = {}
        for year, (service, _, _, _, _) in LAYERS.items():
            title = f"Parishes{' and Non Civil Parished Areas' if year >= 2025 else ''} (May {year}) Boundaries EW BGC"
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=NOTE,
            )
            for url in pages(year):
                cs.artifact(raw.cached(SOURCE, url), sources[year])

        def ends(last: int) -> date | None:
            return LAYERS[last + 1][4] if last < years[-1] else None

        successor: dict[str, str] = {}
        starts: dict[tuple[str, str, int], list[str]] = {}
        for code, (first, _) in spans.items():
            starts.setdefault((lads[code], slug(latest[code]["name"]), first), []).append(code)
        for code, (_, last) in spans.items():
            if last < years[-1]:
                heirs = starts.get((lads[code], slug(latest[code]["name"]), last + 1), [])
                if len(heirs) == 1:
                    successor[code] = heirs[0]
        root: dict[str, str] = {}
        for code in sorted(spans, key=lambda code: spans[code][0]):
            for prior, heir in successor.items():
                if heir == code:
                    root[code] = root.get(prior, prior)
            root.setdefault(code, code)

        polities: dict[str, object] = {}
        for code in sorted(spans, key=lambda code: (-spans[code][1], code)):
            first, last = spans[code]
            row = latest[code]
            area = cs.area("gb.parish", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.parish")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add(
                    "area_parent", area_id=area, parent_id=cs.area("gb.lad", lads[code]), valid_from=None, valid_to=None
                )
            name(area, row["name"])
            if code.startswith("W04") and row["name_cy"]:
                name(area, row["name_cy"], lang="cy", kind="native")
            for former in (
                frame.filter((pl.col("code") == code) & (pl.col("name") != row["name"]))["name"].unique().sort()
            ):
                name(area, former, kind="historical")
            for year, geom, point in (
                frame.filter(pl.col("code") == code).select("year", "geom", "label_point").rows()[:1]
            ):
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=LAYERS[year][4],
                    valid_to=ends(last),
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[year],
                )
            key = root[code]
            polity = cs.key("gb.polity", key)
            if key not in polities:
                polities[key] = polity
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="submunicipal", iso_code=None)
                    cs.add("polity_parent", polity_id=polity, parent_id=cs.key("gb.polity", lads[code]))
                name(polity, row["name"])
                if code.startswith("W04") and row["name_cy"]:
                    name(polity, row["name_cy"], lang="cy", kind="native")
                path = f"{councils[lads[code]]}/{slug(row['name'])}"
                if taken.get(path, polity) != polity:
                    path = f"{path}-{key.lower()}"
                taken[path] = polity
                cs.add("slug", path=path, entity_id=polity)
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=LAYERS[first][4], valid_to=ends(last))
        print(
            f"{len(spans)} areas, {len(polities)} polities, {len(successor)} codes succeeded by a namesake",
            file=sys.stderr,
        )
        authorities = dict(
            conn.execute(
                """select n.text, c.code from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.lang = 'en' and n.is_preferred
                   where a.scheme_code = 'gb.lad' and c.code like 'W06%%'"""
            ).fetchall()
        )
        communities: dict[tuple[str, str], set[str]] = {}
        for code, english, welsh in (
            frame.filter(pl.col("code").str.starts_with("W04")).select("code", "name", "name_cy").rows()
        ):
            for text in (english, welsh):
                if text:
                    communities.setdefault((lads[code], fold(text)), set()).add(root[code])
        precepts = cs.source("Welsh Government", "Community council precepts by community council")
        cs.add(
            "source",
            id=precepts,
            kind="official_body",
            publisher="Welsh Government",
            title="Community council precepts by community council",
            url=PRECEPTS,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Contains public sector information licensed under the Open Government Licence v3.0",
            notes=PRECEPTS_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, PRECEPTS_COPY), precepts)
        served: dict[str, str] = {}
        for authority, council in pl.read_parquet(PARSED / "councils.parquet").select("authority", "council").rows():
            if (authority, council) in GROUPED:
                continue
            keys = communities.get((authorities[authority], fold(RENAMED.get((authority, council), council))), set())
            if len(keys) != 1:
                raise ValueError(f"{authority} - {council}: {len(keys)} communities")
            key = keys.pop()
            if key in served:
                raise ValueError(f"{authority} - {council} and {served[key]} serve one community")
            served[key] = council
            body = cs.key("gb.body", key)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polities[key], kind="council")
            style = re.search(r"\b(Town|City)$", council)
            base = re.sub(r"\s+(Town|City)$", "", council)
            name(body, f"{base} {style.group(1) if style else 'Community'} Council")
        print(f"{len(served)} community councils", file=sys.stderr)
        if conn.execute("select 1 from scheme where code = %s", [GROUP_SCHEME]).fetchone() is None:
            cs.add(
                "scheme", code=GROUP_SCHEME, polity_id=gb, kind="administrative", rank=4, noun="group of communities"
            )
        evidence: dict[str, list[object]] = {}
        for authority, entries in GROUP_SOURCES.items():
            for publisher, title, url, copy in entries:
                source = cs.source(publisher, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license=LICENSE if publisher == "legislation.gov.uk" else PUBLIC_RECORD,
                    license_url=LICENSE_URL if publisher == "legislation.gov.uk" else None,
                )
                cs.artifact(raw.cached(SOURCE, copy), source)
                evidence.setdefault(authority, []).append(source)
        group_spans = set()
        for (authority, council), (label, members) in sorted(GROUPED.items()):
            lad = authorities[authority]
            keys = []
            for member in members:
                found = communities.get((lad, fold(member)), set())
                if len(found) != 1:
                    raise ValueError(f"{council}: {member} matches {len(found)} communities")
                keys.append(next(iter(found)))
            if clash := [key for key in keys if key in served]:
                raise ValueError(f"{council}: {', '.join(clash)} already have their own council")
            served |= dict.fromkeys(keys, council)
            codes = [code for code in spans if root[code] in keys]
            if len(codes) != len(keys):
                raise ValueError(f"{council}: a member community was recoded")
            group_spans |= {spans[code] for code in codes}
            first, last = spans[codes[0]]
            if any(spans[code] != (first, last) for code in codes):
                raise ValueError(f"{council}: member communities span different layers")
            row = conn.execute(
                """with u as (select st_union(st_transform(st_geomfromewkb(decode(g, 'hex')), 27700)) as geom
                              from unnest(%s::text[]) g)
                   select encode(st_asewkb(st_transform(
                            st_multi(st_collectionextract(st_makevalid(geom), 3)), 4326)), 'hex'),
                          encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
                   from u""",
                [[latest[code]["geom"] for code in codes]],
            ).fetchone()
            assert row is not None
            geom, point = row
            code = f"{lad}:{slug(label)}"
            area = cs.area(GROUP_SCHEME, code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=GROUP_SCHEME)
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=cs.area("gb.lad", lad), valid_from=None, valid_to=None)
            name(area, label)
            names = sorted(latest[code]["name"] for code in codes)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=LAYERS[first][4],
                valid_to=ends(last),
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[first],
            )
            cs.add(
                "note",
                entity_id=area,
                kind="methodology",
                body_md=f"The communities of {', '.join(names[:-1])}{',' if len(names) > 2 else ''} and {names[-1]}, "
                f"which {label} Community Council serves; {GROUP_EVIDENCE[authority]}. The shape is the union of the "
                "ONS shapes of those communities.",
            )
            polity = cs.key("gb.polity", code)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="submunicipal", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.key("gb.polity", lad))
            name(polity, label)
            path = f"{councils[lad]}/{slug(label)}"
            if taken.get(path, polity) != polity:
                raise ValueError(f"{path} is taken")
            taken[path] = polity
            cs.add("slug", path=path, entity_id=polity)
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=LAYERS[first][4], valid_to=ends(last))
            for source in [precepts, *evidence[authority]]:
                cs.add("citation", entity_id=polity, source_id=source)
            body = cs.key("gb.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, f"{label} Community Council")
        print(f"{len(GROUPED)} grouped councils, {len(served)} communities with a council", file=sys.stderr)
        english = pl.read_csv(ENGLISH, separator="\t")
        listings: dict[str, UUID] = {}
        for publisher, title, url in english.select("publisher", "title", "url").unique(maintain_order=True).rows():
            listings[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=listings[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=COUNCIL,
            )
            try:
                artifact = raw.cached(SOURCE, url)
            except LookupError:
                artifact = raw.cached(ELECTIONS, url)
            cs.artifact(artifact, listings[url])
        for lad, code, council, url in english.select("lad", "code", "council", "url").rows():
            if lads.get(code) != lad:
                raise ValueError(f"{council}: {code} is not in {lad}")
            key = root[code]
            if key in served:
                raise ValueError(f"{council} and {served[key]} serve one parish")
            served[key] = council
            body = cs.key("gb.body", key)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polities[key], kind="council")
            name(body, council)
            cs.add("citation", entity_id=body, source_id=listings[url])
        print(f"{english.height} English parish and town councils", file=sys.stderr)
        group_plans = {
            vintage: id
            for id, vintage in conn.execute(
                "select id, vintage from boundary_set where scheme_code = %s", [GROUP_SCHEME]
            ).fetchall()
        }
        for first, last in sorted(group_spans):
            cs.add(
                "boundary_set",
                **({"id": group_plans[str(first)]} if str(first) in group_plans else {}),
                scheme_code=GROUP_SCHEME,
                vintage=str(first),
                valid_from=LAYERS[first][4],
                valid_to=ends(last),
                as_of=LAYERS[first][4],
            )
        plans = {
            vintage: id
            for id, vintage in conn.execute(
                "select id, vintage from boundary_set where scheme_code = 'gb.parish'"
            ).fetchall()
        }
        for year in years:
            cs.add(
                "boundary_set",
                **({"id": plans[str(year)]} if str(year) in plans else {}),
                scheme_code="gb.parish",
                vintage=str(year),
                valid_from=LAYERS[year][4],
                valid_to=ends(year),
                as_of=LAYERS[year][4],
            )
        return cs.write()
