import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
import polars as pl

from ea import raw
from ea.package import Draft
from ea.sources.id.big import EDITION, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "id.big_desa"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LAYER = "https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH/BATAS_DESAKEL_AR/MapServer/0"
PAGE = 1000
SPAN = 500
OFFSET = "0.0001"
LICENSE = (
    "Pending: no license stated; open under UU 4/2011 Pasal 42 (batas wilayah is a base-map theme under Pasal 12); "
    "raw files private"
)
KINDS = {1: "desa", 2: "kelurahan"}


def _attributes(offset: int) -> str:
    return f"{LAYER}/query?" + urlencode(
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


def _shapes(start: int, end: int, offset: str = OFFSET) -> str:
    return f"{LAYER}/query?" + urlencode(
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


def _count() -> str:
    return f"{LAYER}/query?" + urlencode({"where": "1=1", "returnIdsOnly": "true", "f": "json"})


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


def _ids() -> list[int]:
    return sorted(json.loads(raw.cached(SOURCE, _count()).path.read_text())["objectIds"])


def _attribute_pages() -> list[str]:
    return [_attributes(offset) for offset in range(0, len(_ids()), PAGE)]


def _shape_pages() -> list[str]:
    return sorted(key for key in raw.keys(SOURCE) if key.startswith(f"{LAYER}/query?") and "returnGeometry=true" in key)


def fetch() -> None:
    if not _fetch_checked(_count()):
        raise ValueError("the id query failed")
    ids = _ids()
    for url in _attribute_pages():
        if not _fetch_checked(url):
            raise ValueError(f"attribute page failed: {url}")
    for start in range(ids[0], ids[-1] + 1, SPAN):
        if _fetch_checked(_shapes(start, start + SPAN - 1)):
            continue
        for low in range(start, start + SPAN, 10):
            if _fetch_checked(_shapes(low, low + 9)):
                continue
            for one in range(low, low + 10):
                if not any(_fetch_checked(_shapes(one, one, offset)) for offset in (OFFSET, "0.0003", "0.001")):
                    raise ValueError(f"feature {one} failed at every offset")


def parse() -> None:
    attributes = {
        feature["attributes"]["OBJECTID"]: feature["attributes"]
        for url in _attribute_pages()
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    }
    if set(attributes) != set(_ids()):
        raise ValueError(f"{len(_ids())} features listed, {len(attributes)} attribute rows")
    features = []
    seen: set[int] = set()
    for url in _shape_pages():
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
            if feature["id"] in seen or feature["geometry"] is None:
                continue
            seen.add(feature["id"])
            row = attributes[feature["id"]]
            if row["TIPADM"] not in KINDS:
                continue
            features.append(
                (
                    row["KDEPUM"].strip(),
                    row["KDPKAB"].strip(),
                    row["NAMOBJ"].strip(),
                    json.dumps(_clean(feature["geometry"])),
                )
            )
    if seen != set(attributes):
        raise ValueError(f"{len(attributes)} features listed, {len(seen)} shapes fetched")
    kinds = {row["KDEPUM"].strip(): row["TIPADM"] for row in attributes.values() if row["TIPADM"] in KINDS}
    districts = {
        row["KDEPUM"].strip(): (row["WADMKC"] or "").strip() or None
        for row in attributes.values()
        if row["TIPADM"] in KINDS
    }
    with connect() as conn:
        regencies = {
            code
            for (code,) in conn.execute(
                "select c.code from area a join area_code c on c.area_id = a.id "
                "where a.scheme_code = 'id.kabkota' and upper_inf(c.valid)"
            ).fetchall()
        }
        rows = conn.execute(
            """with shape as (
                 select code, kabkota, name, st_multi(st_collectionextract(st_makevalid(st_force2d(
                          st_setsrid(st_geomfromgeojson(geojson), 4326))), 3)) as geom
                 from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, kabkota, name, geojson)
               ), village as (
                 select code, min(kabkota) as kabkota, count(distinct kabkota) as parents,
                        mode() within group (order by name) as name, st_union(geom) as geom
                 from shape group by code
               )
               select code, kabkota, parents, name, st_area(geom::geography) / 1e6,
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(geom), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from village""",
            [list(column) for column in zip(*features, strict=True)],
        ).fetchall()
    frame = pl.DataFrame(
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
    ).with_columns(
        kind=pl.col("code").replace_strict({code: KINDS.get(kind) for code, kind in kinds.items()}),
        district=pl.col("code").replace_strict(districts, default=None),
    )
    problems = frame.filter(
        ~pl.col("code").str.contains(r"^\d{2}\.\d{2}\.\d{2}\.\d{4}$")
        | (pl.col("parents") != 1)
        | ~pl.col("parent").is_in(list(regencies))
        | (pl.col("code").str.slice(0, 5) != pl.col("parent"))
        | pl.col("kind").is_null()
        | pl.col("geom").is_null()
    )
    if problems.height:
        print(problems.select("code", "parent", "parents", "name", "kind"))
        raise ValueError(f"{problems.height} villages fail the code, parent, or kind checks")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("parents").write_parquet(PARSED / "villages.parquet")
    skipped = sum(row["TIPADM"] not in KINDS for row in attributes.values())
    print(
        f"{frame.height} villages from {len(features)} features ({skipped} undefined areas skipped): {dict(frame.group_by('kind').len().iter_rows())}"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "villages.parquet").sort("code")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Indonesia's {frame.height:,} villages (desa and kelurahan) with BIG's June 2026 boundaries",
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
        cs.add(
            "scheme",
            code="id.desa",
            polity_id=cs.polity("ID"),
            kind="administrative",
            rank=4,
            noun="village",
            kinds={"desa": "village", "kelurahan": "urban village"},
            code_system="kemendagri",
        )
        ids = [str(cs.area("id.desa", code)) for code in frame["code"]]
        parents = {code: str(cs.area("id.kabkota", code)) for code in frame["parent"].unique()}
        frame = frame.with_columns(id=pl.Series(ids), parent_id=pl.col("parent").replace_strict(parents))
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
