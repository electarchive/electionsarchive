import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import polars as pl
from psycopg import Connection

from ea import raw
from ea.package import Draft
from ea.sources.id.big import EDITION, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "id.big_desa"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
LAYER = "https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH/BATAS_DESAKEL_AR/MapServer/0"
DISTRICTS = "https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH/BATAS_KECAMATAN_AR/MapServer/0"
PAGE = 1000
SPAN = 500
DISTRICT_SPAN = 100
OFFSET = "0.0001"
LICENSE = (
    "Pending: no license stated; open under UU 4/2011 Pasal 42 (batas wilayah is a base-map theme under Pasal 12); "
    "raw files private"
)
KINDS = {1: "desa", 2: "kelurahan"}
DISTRICT = 3


def _attributes(offset: int, layer: str = LAYER) -> str:
    return f"{layer}/query?" + urlencode(
        {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "false",
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
            "f": "json",
        }
    )


def _shapes(start: int, end: int, offset: str = OFFSET, layer: str = LAYER) -> str:
    return f"{layer}/query?" + urlencode(
        {
            "where": f"OBJECTID between {start} and {end}",
            "outFields": "OBJECTID",
            "returnGeometry": "true",
            "maxAllowableOffset": offset,
            "f": "geojson",
        }
    )


def _clean(geometry: dict) -> dict:
    parts = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    kept = [
        [ring for ring in part if len({tuple(point[:2]) for point in ring}) >= 3]
        for part in parts
        if len({tuple(point[:2]) for point in part[0]}) >= 3
    ]
    return {"type": "MultiPolygon", "coordinates": kept}


def _count(layer: str = LAYER) -> str:
    return f"{layer}/query?" + urlencode({"where": "1=1", "returnIdsOnly": "true", "f": "json"})


def _fetch_checked(url: str) -> bool:
    if url in raw.keys(SOURCE):
        return True
    for _ in range(4):
        try:
            response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, timeout=600)
        except httpx.HTTPError:
            continue
        if response.status_code == 200 and ('"features"' in response.text or '"objectIds"' in response.text):
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", "application/json"),
            )
            return True
    return False


def _ids(layer: str = LAYER) -> list[int]:
    return sorted(json.loads(raw.cached(SOURCE, _count(layer)).path.read_text())["objectIds"])


def _attribute_pages(layer: str = LAYER) -> list[str]:
    return [_attributes(offset, layer) for offset in range(0, len(_ids(layer)), PAGE)]


def _range(url: str) -> range:
    low, high = map(int, re.findall(r"\d+", parse_qs(urlsplit(url).query)["where"][0]))
    return range(low, high + 1)


def _shape_pages(layer: str = LAYER) -> list[str]:
    """The cached shape pages of the layer, leaving out a page whose range holds no listed feature or whose features
    narrower cached pages all hold. The fetch splits a page the server fails to answer, and a later fetch may get
    the wide page that once failed; the narrower pages it was split into stay the ones read, so a refetch changes no
    shape."""
    pages = {
        key: _range(key)
        for key in raw.keys(SOURCE)
        if key.startswith(f"{layer}/query?") and "returnGeometry=true" in key
    }
    ids = set(_ids(layer))
    kept = []
    for url, span in pages.items():
        inner = {
            id
            for other in pages.values()
            if len(other) < len(span) and span.start <= other.start and other.stop <= span.stop
            for id in other
        }
        listed = ids & set(span)
        if listed and not (inner and listed <= inner):
            kept.append(url)
    return sorted(kept)


def _fetch_layer(layer: str, span: int) -> None:
    if not _fetch_checked(_count(layer)):
        raise ValueError(f"the id query failed: {layer}")
    ids = _ids(layer)
    for url in _attribute_pages(layer):
        if not _fetch_checked(url):
            raise ValueError(f"attribute page failed: {url}")
    held = {id for url in _shape_pages(layer) for id in _range(url)}
    for start in range(ids[0], ids[-1] + 1, span):
        wanted = set(ids) & set(range(start, start + span))
        if wanted <= held:
            continue
        if _fetch_checked(_shapes(start, start + span - 1, layer=layer)):
            continue
        for low in range(start, start + span, 10):
            if _fetch_checked(_shapes(low, low + 9, layer=layer)):
                continue
            for one in range(low, low + 10):
                if not any(_fetch_checked(_shapes(one, one, offset, layer)) for offset in (OFFSET, "0.0003", "0.001")):
                    raise ValueError(f"feature {one} of {layer} failed at every offset")


def fetch() -> None:
    _fetch_layer(LAYER, SPAN)
    _fetch_layer(DISTRICTS, DISTRICT_SPAN)


def _layer(layer: str) -> tuple[dict[int, dict[str, Any]], dict[int, dict]]:
    """The layer's attribute rows and its shapes by OBJECTID, the shapes in the order the pages list them."""
    attributes = {
        feature["attributes"]["OBJECTID"]: feature["attributes"]
        for url in _attribute_pages(layer)
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    }
    if set(attributes) != set(_ids(layer)):
        raise ValueError(f"{layer}: {len(_ids(layer))} features listed, {len(attributes)} attribute rows")
    shapes: dict[int, dict] = {}
    for url in _shape_pages(layer):
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
            if feature["id"] not in shapes and feature["geometry"] is not None:
                shapes[feature["id"]] = feature["geometry"]
    if set(shapes) != set(attributes):
        raise ValueError(f"{layer}: {len(attributes)} features listed, {len(shapes)} shapes fetched")
    return attributes, shapes


def _union(conn: Connection, features: list[tuple[str, str, str, str]]) -> pl.DataFrame:
    """One row per code from (code, parent code, name, GeoJSON) features: the union of its shapes, its area, a label
    point, and how many parents its features name."""
    rows = conn.execute(
        """with shape as (
             select code, parent, name, st_multi(st_collectionextract(st_makevalid(st_force2d(
                      st_setsrid(st_geomfromgeojson(geojson), 4326))), 3)) as geom
             from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, parent, name, geojson)
           ), area as (
             select code, min(parent) as parent, count(distinct parent) as parents,
                    mode() within group (order by name) as name, st_union(geom) as geom
             from shape group by code
           )
           select code, parent, parents, name, st_area(geom::geography) / 1e6,
                  encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(geom), 3))), 'hex'),
                  encode(st_asewkb(st_pointonsurface(geom)), 'hex')
           from area""",
        [list(column) for column in zip(*features, strict=True)],
    ).fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "code": pl.String,
            "parent": pl.String,
            "parents": pl.Int64,
            "name": pl.String,
            "km2": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )


def parse() -> None:
    attributes, shapes = _layer(LAYER)
    features = [
        (
            row["KDEPUM"].strip(),
            row["KDPKAB"].strip(),
            row["NAMOBJ"].strip(),
            json.dumps(_clean(geometry)),
        )
        for id, geometry in shapes.items()
        if (row := attributes[id])["TIPADM"] in KINDS
    ]
    kinds = {row["KDEPUM"].strip(): row["TIPADM"] for row in attributes.values() if row["TIPADM"] in KINDS}
    names = {
        row["KDEPUM"].strip(): (row["WADMKC"] or "").strip() or None
        for row in attributes.values()
        if row["TIPADM"] in KINDS
    }
    within = {
        row["KDEPUM"].strip(): (row["KDCPUM"] or "").strip() or None
        for row in attributes.values()
        if row["TIPADM"] in KINDS
    }
    district_attributes, district_shapes = _layer(DISTRICTS)
    district_features = [
        (
            row["KDCPUM"].strip(),
            row["KDPKAB"].strip(),
            row["NAMOBJ"].strip(),
            json.dumps(_clean(geometry)),
        )
        for id, geometry in district_shapes.items()
        if (row := district_attributes[id])["TIPADM"] == DISTRICT
    ]
    with connect() as conn:
        regencies = {
            code
            for (code,) in conn.execute(
                "select c.code from area a join area_code c on c.area_id = a.id "
                "where a.scheme_code = 'id.kabkota' and upper_inf(c.valid)"
            ).fetchall()
        }
        frame = _union(conn, features)
        districts = _union(conn, district_features)
    problems = districts.filter(
        ~pl.col("code").str.contains(r"^\d{2}\.\d{2}\.\d{2}$")
        | (pl.col("parents") != 1)
        | ~pl.col("parent").is_in(list(regencies))
        | (pl.col("code").str.slice(0, 5) != pl.col("parent"))
        | pl.col("geom").is_null()
    )
    if problems.height:
        print(problems.select("code", "parent", "parents", "name"))
        raise ValueError(f"{problems.height} districts fail the code or parent checks")
    frame = frame.with_columns(
        kind=pl.col("code").replace_strict({code: KINDS.get(kind) for code, kind in kinds.items()}),
        district=pl.col("code").replace_strict(names, default=None),
        kecamatan=pl.col("code").replace_strict(within, default=None),
    )
    problems = frame.filter(
        ~pl.col("code").str.contains(r"^\d{2}\.\d{2}\.\d{2}\.\d{4}$")
        | (pl.col("parents") != 1)
        | ~pl.col("parent").is_in(list(regencies))
        | (pl.col("code").str.slice(0, 5) != pl.col("parent"))
        | (pl.col("code").str.slice(0, 8) != pl.col("kecamatan")).fill_null(True)
        | ~pl.col("kecamatan").is_in(districts["code"].to_list()).fill_null(False)
        | pl.col("kind").is_null()
        | pl.col("geom").is_null()
    )
    if problems.height:
        print(problems.select("code", "parent", "parents", "kecamatan", "name", "kind"))
        raise ValueError(f"{problems.height} villages fail the code, parent, district, or kind checks")
    empty = districts.filter(~pl.col("code").is_in(frame["kecamatan"].to_list()))
    if empty.height:
        print(empty.select("code", "parent", "name"))
        raise ValueError(f"{empty.height} districts hold no village")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("parents").write_parquet(PARSED / "villages.parquet")
    districts.drop("parents").write_parquet(PARSED / "districts.parquet")
    skipped = sum(row["TIPADM"] not in KINDS for row in attributes.values())
    left = sum(row["TIPADM"] != DISTRICT for row in district_attributes.values())
    print(
        f"{frame.height} villages from {len(features)} features ({skipped} undefined areas skipped): "
        f"{dict(frame.group_by('kind').len().iter_rows())}; {districts.height} districts from "
        f"{len(district_features)} features ({left} undefined areas skipped)"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "villages.parquet").sort("code")
    districts = pl.read_parquet(PARSED / "districts.parquet").sort("code")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Indonesia's {districts.height:,} districts (kecamatan) and {frame.height:,} villages "
                "(desa and kelurahan) with BIG's June 2026 boundaries"
            ),
            polity="ID",
        )
        source = cs.source(PUBLISHER, f"{TITLE}: desa dan kelurahan")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=f"{TITLE}: desa dan kelurahan",
            url=LAYER,
            license=LICENSE,
            attribution="Badan Informasi Geospasial",
            notes=(
                "Village polygons from the public ArcGIS layer BATAS_DESAKEL_AR, generalized by the server to "
                f"{OFFSET} degrees; multipart villages are unions of their features. Codes are the Kemendagri codes "
                "in KDEPUM, which follow the 2022 recoding of Papua. Most boundaries are indicative (batas indikatif) "
                "rather than definitive."
            ),
        )
        cs.artifact(raw.cached(SOURCE, _count()), source)
        for url in [*_attribute_pages(), *_shape_pages()]:
            cs.artifact(raw.cached(SOURCE, url), source)
        district_source = cs.source(PUBLISHER, f"{TITLE}: kecamatan")
        cs.add(
            "source",
            id=district_source,
            kind="official_body",
            publisher=PUBLISHER,
            title=f"{TITLE}: kecamatan",
            url=DISTRICTS,
            license=LICENSE,
            attribution="Badan Informasi Geospasial",
            notes=(
                "District polygons from the public ArcGIS layer BATAS_KECAMATAN_AR, generalized by the server to "
                f"{OFFSET} degrees; multipart districts are unions of their features. Codes are the Kemendagri codes "
                "in KDCPUM, and each village's district is the KDCPUM of its BATAS_DESAKEL_AR row. Most boundaries "
                "are indicative (batas indikatif) rather than definitive."
            ),
        )
        cs.artifact(raw.cached(SOURCE, _count(DISTRICTS)), district_source)
        for url in [*_attribute_pages(DISTRICTS), *_shape_pages(DISTRICTS)]:
            cs.artifact(raw.cached(SOURCE, url), district_source)
        indonesia = cs.polity("ID")
        cs.add(
            "scheme",
            code="id.desa",
            polity_id=indonesia,
            kind="administrative",
            rank=4,
            noun="village",
            kinds={"desa": "village", "kelurahan": "urban village"},
            code_system="kemendagri",
        )
        # Rank 3 is shared with id.tps, as rank 2 is shared by id.kabkota and id.dapil_dpr.
        cs.add(
            "scheme",
            code="id.kecamatan",
            polity_id=indonesia,
            kind="administrative",
            rank=3,
            noun="district",
            kinds=None,
            code_system="kemendagri",
        )
        regencies = {code: str(cs.area("id.kabkota", code)) for code in districts["parent"].unique()}
        districts = districts.with_columns(
            id=pl.Series([str(cs.area("id.kecamatan", code)) for code in districts["code"]]),
            parent_id=pl.col("parent").replace_strict(regencies),
        )
        cs.frame("area", districts.select("id", scheme_code=pl.lit("id.kecamatan")))
        cs.frame("area_code", districts.select(area_id="id", code="code", valid_from=pl.lit(None, pl.Date)))
        cs.frame("area_parent", districts.select(area_id="id", parent_id="parent_id", valid_from=pl.lit(None, pl.Date)))
        cs.frame(
            "area_geometry",
            districts.select(
                area_id="id",
                valid_from=pl.lit(None, pl.Date),
                valid_to=pl.lit(None, pl.Date),
                geom="geom",
                label_point="label_point",
                quality=pl.lit("official"),
                source_id=pl.lit(str(district_source)),
            ),
        )
        cs.frame(
            "name",
            districts.select(
                entity_id="id", text="name", lang=pl.lit("id"), kind=pl.lit("official"), is_preferred=pl.lit(True)
            ),
        )
        cs.add(
            "boundary_set", scheme_code="id.kecamatan", vintage="2026", valid_from=None, valid_to=None, as_of=EDITION
        )
        within = dict(districts.select("code", "id").iter_rows())
        ids = [str(cs.area("id.desa", code)) for code in frame["code"]]
        frame = frame.with_columns(id=pl.Series(ids), parent_id=pl.col("kecamatan").replace_strict(within))
        cs.frame("area", frame.select("id", scheme_code=pl.lit("id.desa")))
        cs.frame("area_code", frame.select(area_id="id", code="code", valid_from=pl.lit(None, pl.Date)))
        cs.frame("area_kind", frame.select(area_id="id", kind="kind", valid_from=pl.lit(None, pl.Date)))
        cs.frame("area_parent", frame.select(area_id="id", parent_id="parent_id", valid_from=pl.lit(None, pl.Date)))
        cs.frame(
            "area_geometry",
            frame.select(
                area_id="id",
                valid_from=pl.lit(None, pl.Date),
                valid_to=pl.lit(None, pl.Date),
                geom="geom",
                label_point="label_point",
                quality=pl.lit("official"),
                source_id=pl.lit(str(source)),
            ),
        )
        cs.frame(
            "name",
            frame.select(
                entity_id="id", text="name", lang=pl.lit("id"), kind=pl.lit("official"), is_preferred=pl.lit(True)
            ),
        )
        cs.add("boundary_set", scheme_code="id.desa", vintage="2026", valid_from=None, valid_to=None, as_of=EDITION)
        return cs.write()
