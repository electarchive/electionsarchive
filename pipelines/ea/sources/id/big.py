import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid5

import httpx
import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "id.big"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LAYER = "https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH/BATAS_WILAYAH/MapServer/13"
ATTRIBUTES = f"{LAYER}/query?" + urlencode({"where": "1=1", "outFields": "*", "returnGeometry": "false", "f": "json"})
PAGE = 10
OFFSET = "0.0003"
BRIDGE = "https://sig.bps.go.id/rest-bridging/getwilayah?"
PERIOD = "2019_1.2019"
SPARQL = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": "SELECT ?item ?code WHERE { ?item wdt:P2588 ?code . FILTER(STRLEN(?code) <= 5) "
        "FILTER NOT EXISTS { ?item wdt:P576 ?end } }",
        "format": "json",
    }
)
PUBLISHER = "Badan Informasi Geospasial"
TITLE = "Geodatabase data batas wilayah administrasi nasional edisi Juni 2026"
BRIDGE_PUBLISHER = "Badan Pusat Statistik"
BRIDGE_TITLE = "Bridging kode wilayah BPS dan Kemendagri, periode 2019"
EDITION = date(2026, 6, 1)
SPLIT = {"93": date(2022, 7, 25), "94": date(2022, 7, 25), "95": date(2022, 7, 25), "96": date(2022, 12, 8)}
FROM = {"93": "91", "94": "91", "95": "91", "96": "92"}
LAWS = {
    "93": "Undang-Undang Nomor 14 Tahun 2022 tentang Pembentukan Provinsi Papua Selatan",
    "94": "Undang-Undang Nomor 15 Tahun 2022 tentang Pembentukan Provinsi Papua Tengah",
    "95": "Undang-Undang Nomor 16 Tahun 2022 tentang Pembentukan Provinsi Papua Pegunungan",
    "96": "Undang-Undang Nomor 29 Tahun 2022 tentang Pembentukan Provinsi Papua Barat Daya",
}
PROVINCES = {
    "11": ("AC", "aceh", "Aceh", "Aceh"),
    "12": ("SU", "sumatera-utara", "Sumatera Utara", "North Sumatra"),
    "13": ("SB", "sumatera-barat", "Sumatera Barat", "West Sumatra"),
    "14": ("RI", "riau", "Riau", "Riau"),
    "15": ("JA", "jambi", "Jambi", "Jambi"),
    "16": ("SS", "sumatera-selatan", "Sumatera Selatan", "South Sumatra"),
    "17": ("BE", "bengkulu", "Bengkulu", "Bengkulu"),
    "18": ("LA", "lampung", "Lampung", "Lampung"),
    "19": ("BB", "kepulauan-bangka-belitung", "Kepulauan Bangka Belitung", "Bangka Belitung Islands"),
    "21": ("KR", "kepulauan-riau", "Kepulauan Riau", "Riau Islands"),
    "31": ("JK", "jakarta", "DKI Jakarta", "Jakarta"),
    "32": ("JB", "jawa-barat", "Jawa Barat", "West Java"),
    "33": ("JT", "jawa-tengah", "Jawa Tengah", "Central Java"),
    "34": ("YO", "yogyakarta", "Daerah Istimewa Yogyakarta", "Special Region of Yogyakarta"),
    "35": ("JI", "jawa-timur", "Jawa Timur", "East Java"),
    "36": ("BT", "banten", "Banten", "Banten"),
    "51": ("BA", "bali", "Bali", "Bali"),
    "52": ("NB", "nusa-tenggara-barat", "Nusa Tenggara Barat", "West Nusa Tenggara"),
    "53": ("NT", "nusa-tenggara-timur", "Nusa Tenggara Timur", "East Nusa Tenggara"),
    "61": ("KB", "kalimantan-barat", "Kalimantan Barat", "West Kalimantan"),
    "62": ("KT", "kalimantan-tengah", "Kalimantan Tengah", "Central Kalimantan"),
    "63": ("KS", "kalimantan-selatan", "Kalimantan Selatan", "South Kalimantan"),
    "64": ("KI", "kalimantan-timur", "Kalimantan Timur", "East Kalimantan"),
    "65": ("KU", "kalimantan-utara", "Kalimantan Utara", "North Kalimantan"),
    "71": ("SA", "sulawesi-utara", "Sulawesi Utara", "North Sulawesi"),
    "72": ("ST", "sulawesi-tengah", "Sulawesi Tengah", "Central Sulawesi"),
    "73": ("SN", "sulawesi-selatan", "Sulawesi Selatan", "South Sulawesi"),
    "74": ("SG", "sulawesi-tenggara", "Sulawesi Tenggara", "Southeast Sulawesi"),
    "75": ("GO", "gorontalo", "Gorontalo", "Gorontalo"),
    "76": ("SR", "sulawesi-barat", "Sulawesi Barat", "West Sulawesi"),
    "81": ("MA", "maluku", "Maluku", "Maluku"),
    "82": ("MU", "maluku-utara", "Maluku Utara", "North Maluku"),
    "91": ("PA", "papua", "Papua", "Papua"),
    "92": ("PB", "papua-barat", "Papua Barat", "West Papua"),
    "93": ("PS", "papua-selatan", "Papua Selatan", "South Papua"),
    "94": ("PT", "papua-tengah", "Papua Tengah", "Central Papua"),
    "95": ("PE", "papua-pegunungan", "Papua Pegunungan", "Highland Papua"),
    "96": ("PD", "papua-barat-daya", "Papua Barat Daya", "Southwest Papua"),
}
ADMINISTRATIVE = {"31.01", "31.71", "31.72", "31.73", "31.74", "31.75"}
COUNTS = {"kabkota": 514, "provinsi": 38, "nation": 1}


def _page(start: int, end: int, offset: str = OFFSET) -> str:
    return f"{LAYER}/query?" + urlencode(
        {
            "where": f"objectid between {start} and {end}",
            "outFields": "objectid",
            "returnGeometry": "true",
            "maxAllowableOffset": offset,
            "f": "geojson",
        }
    )


def _pages() -> list[str]:
    return sorted(key for key in raw.keys(SOURCE) if key.startswith(f"{LAYER}/query?") and "returnGeometry=true" in key)


def _bridge(level: str, parent: str | None = None) -> str:
    return BRIDGE + urlencode(
        {"level": level, **({} if parent is None else {"parent": parent}), "periode_merge": PERIOD}
    )


def _fetch_checked(url: str) -> bool:
    if url in raw.keys(SOURCE):
        return True
    response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, timeout=600)
    if response.status_code != 200 or "features" not in response.text[:200]:
        return False
    raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".json",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", "application/json"),
    )
    return True


def fetch() -> None:
    if not _fetch_checked(ATTRIBUTES):
        raise ValueError("the attribute query failed")
    ids = sorted(
        f["attributes"]["objectid"] for f in json.loads(raw.cached(SOURCE, ATTRIBUTES).path.read_text())["features"]
    )
    for start in range(ids[0], ids[-1] + 1, PAGE):
        if _fetch_checked(_page(start, start + PAGE - 1)):
            continue
        for one in range(start, start + PAGE):
            if not any(_fetch_checked(_page(one, one, offset)) for offset in (OFFSET, "0.0005", "0.001")):
                raise ValueError(f"feature {one} failed at every offset")
    provinces = json.loads(raw.fetch(SOURCE, _bridge("provinsi"), suffix=".json").path.read_text())
    for province in provinces:
        raw.fetch(SOURCE, _bridge("kabupaten", province["kode_bps"]), suffix=".json")
    raw.fetch(SOURCE, SPARQL, suffix=".json")


def _name(code: str, text: str, kind: int) -> str:
    text = re.sub(r"^(Kota )?(Adm\. )?", "", text.strip())
    text = re.sub(r"^Kep\. ", "Kepulauan ", text)
    if code in ADMINISTRATIVE:
        return f"Kota Administrasi {text}" if kind == 5 else f"Kabupaten Administrasi {text}"
    return f"Kota {text}" if kind == 5 else f"Kabupaten {text}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse() -> None:
    attributes = {
        feature["attributes"]["objectid"]: feature["attributes"]
        for feature in json.loads(raw.cached(SOURCE, ATTRIBUTES).path.read_text())["features"]
    }
    features = []
    seen: set[int] = set()
    for url in _pages():
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
            if feature["id"] in seen:
                continue
            seen.add(feature["id"])
            row = attributes[feature["id"]]
            regency = (row["kdpkab"] or "").strip()
            features.append(
                (
                    row["kdppum"].strip(),
                    regency if row["tipadm"] in (4, 5) and regency else None,
                    json.dumps(feature["geometry"]),
                )
            )
    if len(features) != len(attributes):
        raise ValueError(f"{len(attributes)} features listed, {len(features)} fetched")
    names: dict[str, tuple[str, int, float]] = {}
    for row in attributes.values():
        regency = (row["kdpkab"] or "").strip()
        if row["tipadm"] in (4, 5) and regency and (regency not in names or row["luaswh"] > names[regency][2]):
            names[regency] = (row["namobj"], row["tipadm"], row["luaswh"])
    with connect() as conn:
        conn.execute(
            """create temp table shape on commit drop as
               select provinsi, kabkota, st_multi(st_collectionextract(st_makevalid(st_force2d(
                        st_setsrid(st_geomfromgeojson(geojson), 4326))), 3)) as geom
               from unnest(%s::text[], %s::text[], %s::text[]) as f(provinsi, kabkota, geojson)""",
            [list(column) for column in zip(*features, strict=True)],
        )
        rows = conn.execute(
            """with layers as (
                 select 'kabkota' as layer, kabkota as code, min(provinsi) as parent, st_union(geom) as geom
                 from shape where kabkota is not null group by kabkota
                 union all select 'provinsi', provinsi, 'ID', st_union(geom) from shape group by provinsi
                 union all select 'provinsi_2019', '91', 'ID', st_union(geom) from shape
                   where provinsi in ('91', '93', '94', '95')
                 union all select 'provinsi_2019', '92', 'ID', st_union(geom) from shape where provinsi in ('92', '96')
                 union all select 'nation', 'ID', null, st_union(geom) from shape
               )
               select layer, code, parent, st_area(geom::geography) / 1e6,
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(geom), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from layers"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "code": pl.String,
            "parent": pl.String,
            "km2": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    ).with_columns(
        name=pl.struct("code", "layer").map_elements(
            lambda row: _name(row["code"], *names[row["code"]][:2]) if row["layer"] == "kabkota" else None,
            return_dtype=pl.String,
        ),
        kind=pl.struct("code", "layer").map_elements(
            lambda row: ("kota" if names[row["code"]][1] == 5 else "kabupaten") if row["layer"] == "kabkota" else None,
            return_dtype=pl.String,
        ),
    )
    counts = {layer: count for layer, count in frame.group_by("layer").len().iter_rows() if layer in COUNTS}
    if counts != COUNTS or frame["geom"].null_count():
        raise ValueError(f"expected {COUNTS}, got {counts}")
    if set(frame.filter(pl.col("layer") == "provinsi")["code"]) != set(PROVINCES):
        raise ValueError("province codes differ from the list")
    kinds = dict(frame.filter(pl.col("layer") == "kabkota").group_by("kind").len().iter_rows())
    if kinds != {"kabupaten": 416, "kota": 98}:
        raise ValueError(f"expected 416 regencies and 98 cities, got {kinds}")
    old: list[tuple[str, str, str]] = []
    for province in json.loads(raw.cached(SOURCE, _bridge("provinsi")).path.read_text()):
        for row in json.loads(raw.cached(SOURCE, _bridge("kabupaten", province["kode_bps"])).path.read_text()):
            old.append((row["kode_dagri"], row["nama_dagri"], province["kode_bps"]))
    if len(old) != 514:
        raise ValueError(f"expected 514 regencies and cities in 2019, got {len(old)}")
    current = frame.filter(pl.col("layer") == "kabkota")
    key = {
        (
            code[:2],
            label.startswith("KOTA"),
            _slug(re.sub(r"^(KAB\.? ADM\.|KOTA ADM\.|KAB\.?|KOTA)\s*", "", label.replace("FAK FAK", "FAKFAK"))),
        ): code
        for code, label, _ in old
    }
    recoded = []
    for code, text in current.select("code", "name").rows():
        if code[:2] not in FROM:
            if code not in {code for code, _, _ in old}:
                raise ValueError(f"{code} {text} has no 2019 code")
            continue
        bare = _slug(re.sub(r"^(Kabupaten|Kota) ", "", text))
        previous = key.get((FROM[code[:2]], text.startswith("Kota"), bare))
        if previous is None:
            raise ValueError(f"no 2019 code for {code} {text}")
        recoded.append((code, previous))
    moved = {previous for _, previous in recoded}
    unmatched = [code for code, _, _ in old if code[:2] in ("91", "92") and code not in moved]
    stay = set(current.filter(pl.col("parent").is_in(["91", "92"]))["code"])
    if set(unmatched) != stay:
        raise ValueError(f"2019 Papua codes left over: {sorted(set(unmatched) ^ stay)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")
    pl.DataFrame(recoded, schema={"code": pl.String, "previous": pl.String}, orient="row").write_parquet(
        PARSED / "recoded.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    recoded = dict(pl.read_parquet(PARSED / "recoded.parquet").iter_rows())
    items = {
        row["code"]["value"]: row["item"]["value"].rsplit("/", 1)[1]
        for row in json.loads(raw.cached(SOURCE, SPARQL).path.read_text())["results"]["bindings"]
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indonesia, its 38 provinces, and its 514 regencies and cities, with BIG's June 2026 boundaries",
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=f"{LAYER}",
            license="No license stated; public map service of the national geospatial agency, credited to BIG",
            attribution="Badan Informasi Geospasial",
            notes=(
                "Province and regency/city polygons from the public ArcGIS layer, generalized by the server to "
                f"{OFFSET} degrees. Island areas outside any regency count toward their province only."
            ),
        )
        cs.artifact(raw.cached(SOURCE, ATTRIBUTES), source)
        for url in _pages():
            cs.artifact(raw.cached(SOURCE, url), source)
        bridge = cs.source(BRIDGE_PUBLISHER, BRIDGE_TITLE)
        cs.add(
            "source",
            id=bridge,
            kind="official_body",
            publisher=BRIDGE_PUBLISHER,
            title=BRIDGE_TITLE,
            url="https://sig.bps.go.id/bridging-kode",
            license="No license stated; public code service of Statistics Indonesia",
            notes="Kemendagri codes of 2019, used for the regencies of Papua recoded in 2022.",
        )
        cs.artifact(raw.cached(SOURCE, _bridge("provinsi")), bridge)
        for province in json.loads(raw.cached(SOURCE, _bridge("provinsi")).path.read_text()):
            cs.artifact(raw.cached(SOURCE, _bridge("kabupaten", province["kode_bps"])), bridge)
        wikidata = cs.source("Wikidata", "Wikidata items with an Indonesian administrative code (P2588)")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Wikidata items with an Indonesian administrative code (P2588)",
            url="https://www.wikidata.org/wiki/Property:P2588",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, SPARQL), wikidata)

        def geometry(area: UUID, geom: str, point: str | None, **valid: date) -> None:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=valid.get("valid_from"),
                valid_to=valid.get("valid_to"),
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )

        indonesia = cs.polity("ID")
        cs.add("polity", id=indonesia, kind="sovereign_state", iso_code="ID")
        name(indonesia, "Indonesia", "en")
        name(indonesia, "Republik Indonesia", "id", "native")
        cs.add("slug", path="id", entity_id=indonesia)
        cs.add("external_id", entity_id=indonesia, scheme="wikidata", value="Q252")
        for code, rank, noun, kinds, system in (
            ("id.nation", 0, "country", None, None),
            ("id.provinsi", 1, "province", None, "kemendagri"),
            ("id.kabkota", 2, "regency or city", {"kabupaten": "regency", "kota": "city"}, "kemendagri"),
        ):
            cs.add(
                "scheme",
                code=code,
                polity_id=indonesia,
                kind="administrative",
                rank=rank,
                noun=noun,
                kinds=kinds,
                code_system=system,
            )
        layers = {layer: lines for (layer,), lines in frame.group_by("layer")}
        nation = cs.area("id.nation", "ID")
        cs.add("area", id=nation, scheme_code="id.nation")
        cs.add("area_code", area_id=nation, code="ID", valid_from=None)
        name(nation, "Indonesia", "en")
        cs.add("polity_territory", polity_id=indonesia, area_id=nation)
        geometry(nation, layers["nation"]["geom"][0], None)

        before = {
            code: (geom, point)
            for code, geom, point in layers["provinsi_2019"].select("code", "geom", "label_point").rows()
        }
        provinces: dict[str, UUID] = {}
        paths: dict[str, str] = {}
        for code, geom, point in layers["provinsi"].sort("code").select("code", "geom", "label_point").rows():
            iso, path, native, english = PROVINCES[code]
            area = provinces[code] = cs.area("id.provinsi", code)
            created = SPLIT.get(code)
            cs.add("area", id=area, scheme_code="id.provinsi")
            cs.add("area_code", area_id=area, code=code, valid_from=created)
            cs.add("area_parent", area_id=area, parent_id=nation, valid_from=created)
            changed = min(day for split, day in SPLIT.items() if FROM[split] == code) if code in before else None
            if changed is None:
                geometry(area, geom, point, **({} if created is None else {"valid_from": created}))
            else:
                geometry(area, *before[code], valid_to=changed)
                geometry(area, geom, point, valid_from=changed)
            polity = cs.polity(f"ID-{iso}")
            paths[code] = f"id/{path}"
            cs.add("polity", id=polity, kind="region", iso_code=f"ID-{iso}")
            cs.add("slug", path=paths[code], entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=indonesia)
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=created)
            if code in items:
                cs.add("external_id", entity_id=polity, scheme="wikidata", value=items[code])
            for entity in (polity, area):
                name(entity, native)
                if english != native:
                    name(entity, english, "en", "english")
        for code, day in SPLIT.items():
            event = uuid5(NAMESPACE, f"{SOURCE}.event\x1fsplit:{code}")
            cs.add("area_event", id=event, kind="split", date=day, notes=LAWS[code])
            cs.add("area_event_member", event_id=event, area_id=provinces[FROM[code]], role="from")
            cs.add("area_event_member", event_id=event, area_id=provinces[code], role="to")

        rows = layers["kabkota"].sort("code").select("code", "parent", "name", "kind", "geom", "label_point").rows()
        for code, parent, text, kind, geom, point in rows:
            area = cs.area("id.kabkota", code)
            cs.add("area", id=area, scheme_code="id.kabkota")
            cs.add("area_kind", area_id=area, kind=kind, valid_from=None)
            if code in recoded:
                day = SPLIT[parent]
                cs.add("area_code", area_id=area, code=recoded[code], valid_to=day)
                cs.add("area_code", area_id=area, code=code, valid_from=day)
                cs.add("area_parent", area_id=area, parent_id=provinces[FROM[parent]], valid_to=day)
                cs.add("area_parent", area_id=area, parent_id=provinces[parent], valid_from=day)
            else:
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=provinces[parent], valid_from=None)
            geometry(area, geom, point)
            name(area, text)
            if code in ADMINISTRATIVE:
                continue
            polity = cs.key(f"{SOURCE}.polity", code)
            cs.add("polity", id=polity, kind="municipality" if kind == "kota" else "county", iso_code=None)
            cs.add("slug", path=f"{paths[parent]}/{_slug(text)}", entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"ID-{PROVINCES[parent][0]}"))
            cs.add("polity_territory", polity_id=polity, area_id=area)
            name(polity, text)
            if code in items:
                cs.add("external_id", entity_id=polity, scheme="wikidata", value=items[code])
        for scheme in ("id.provinsi", "id.kabkota"):
            for vintage, as_of, valid_from, valid_to in (
                ("2019", date(2019, 4, 17), None, date(2022, 7, 25)),
                ("2026", EDITION, date(2022, 12, 8), None),
            ):
                cs.add(
                    "boundary_set",
                    scheme_code=scheme,
                    vintage=vintage,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    as_of=as_of,
                )
        return cs.write()
