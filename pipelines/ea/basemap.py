import gzip
import json
import math
import shutil
import subprocess
from multiprocessing import Pool
from pathlib import Path

import httpx
import mapbox_vector_tile
import numpy as np
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
from pmtiles.reader import MmapSource, Reader
from pyproj import Transformer
from shapely import affinity, box, clip_by_rect, make_valid, unary_union
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

from ea import raw
from ea.plane import REGISTRY
from ea.store import DATA, ROOT

BUILDS = "https://build-metadata.protomaps.dev/builds.json"
PLANET = "https://build.protomaps.com/{key}"
ADMIN = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson"
FONT = "https://github.com/google/fonts/raw/main/ofl/sofiasanssemicondensed/SofiaSansSemiCondensed%5Bwght%5D.ttf"
WORK = DATA / "work" / "basemap"
OUT = DATA / "plane" / "world.basemap"
GLYPHS = ROOT / "apps" / "web" / "static" / "glyphs"
COUNTRIES = {
    "us": ["USA", "PRI", "VIR", "GUM", "MNP", "ASM"],
    "ca": ["CAN"],
    "br": ["BRA"],
    "gb": ["GBR"],
    "ie": ["IRL"],
    "fr": ["FRA", "PYF", "NCL", "SPM", "WLF", "BLM", "MAF"],
    "de": ["DEU"],
    "it": ["ITA"],
    "ch": ["CHE"],
    "tr": ["TUR"],
}
BANDS = {6: (0, 7), 8: (8, 9), 10: (10, 12)}
METRO = 500_000
ROADS = {
    "motorway": ("highway", 4),
    "trunk": ("highway", 5),
    "motorway_link": ("highway", 9),
    "trunk_link": ("highway", 9),
    "primary": ("primary", 7),
    "secondary": ("secondary", 8),
}
STREETS = {
    "tertiary": ("tertiary", 10),
    "tertiary_link": ("tertiary", 11),
    "residential": ("minor", 11),
    "unclassified": ("minor", 11),
    "living_street": ("minor", 12),
    "road": ("minor", 12),
}
WEIGHTS = {"Regular": 400, "SemiBold": 600}
SCALE = 8
BUFFER = 3
RADIUS = 8
CUTOFF = 0.25

zones: list[tuple[list[list[float]], list[float] | None, Transformer, list[float] | None]] = []
land: BaseGeometry
inside: PreparedGeometry


def setup(scheme: str) -> None:
    global land, inside
    entry = REGISTRY["projections"][REGISTRY["schemes"][scheme]]
    zones.clear()
    for part in [*entry.get("insets", []), entry]:
        project = Transformer.from_crs("EPSG:4326", f"EPSG:{part['srid']}", always_xy=True)
        zones.append((part["within"], part.get("clip"), project, part.get("affine")))
    admin = raw.fetch("world.natural_earth", ADMIN, suffix=".geojson")
    land = unary_union(
        [
            shape(f["geometry"])
            for f in json.loads(admin.path.read_text())["features"]
            if f["properties"]["ADM0_A3"] in COUNTRIES[scheme]
        ]
    ).buffer(0.02)
    inside = prep(land)


def tiles(z: int, w: float, s: float, e: float, n: float) -> list[tuple[int, int, int]]:
    span = 2**z
    x0, x1 = int((w + 180) / 360 * span), int((e + 180) / 360 * span)
    y0 = int((1 - math.asinh(math.tan(math.radians(n))) / math.pi) / 2 * span)
    y1 = int((1 - math.asinh(math.tan(math.radians(s))) / math.pi) / 2 * span)
    return [(z, x, y) for x in range(x0, min(x1, span - 1) + 1) for y in range(y0, min(y1, span - 1) + 1)]


def walk(coords, fn):
    if isinstance(coords[0], (int, float)):
        return fn(coords)
    return [walk(part, fn) for part in coords]


def emit(geometry: dict, z: int, x: int, y: int, layer: str, props: dict, zooms: tuple[int, int]) -> str | None:
    span, extent = 2**z, 4096
    raw_shape = shape(geometry)
    if raw_shape.geom_type == "Point":
        if not (0 <= raw_shape.x < extent and 0 <= raw_shape.y < extent):
            return None
    else:
        raw_shape = clip_by_rect(raw_shape if raw_shape.is_valid else make_valid(raw_shape), 0, 0, extent, extent)
        if raw_shape.is_empty or raw_shape.geom_type == "GeometryCollection":
            return None
    geom = affinity.affine_transform(
        raw_shape, [360 / extent / span, 0, 0, 1 / extent / span, 360 * x / span - 180, y / span]
    )
    geom = shape(
        {
            "type": geom.geom_type,
            "coordinates": walk(
                mapping(geom)["coordinates"],
                lambda p: (p[0], math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * p[1]))))),
            ),
        }
    )
    w, s, e, n = geom.bounds
    cx, cy = (w + e) / 2, (s + n) / 2
    zone = next(
        (zone for zone in zones if any(bw <= cx <= be and bs <= cy <= bn for bw, bs, be, bn in zone[0])),
        None,
    )
    if zone is None:
        return None
    _, clip, project, scaling = zone
    if clip is not None:
        geom = clip_by_rect(geom, *clip)
    if not inside.contains(geom):
        if not inside.intersects(geom):
            return None
        geom = make_valid(geom).intersection(land)
    if geom.is_empty or geom.geom_type == "GeometryCollection":
        return None

    def place(p):
        px, py = project.transform(p[0], p[1])
        if scaling is not None:
            px, py = scaling[0] * px + scaling[1], scaling[0] * py + scaling[2]
        return [round(px, 1), round(py, 1)]

    planar = {"type": geom.geom_type, "coordinates": walk(mapping(geom)["coordinates"], place)}
    return json.dumps(
        {
            "type": "Feature",
            "properties": props,
            "geometry": planar,
            "tippecanoe": {"minzoom": zooms[0], "maxzoom": zooms[1], "layer": layer},
        },
        separators=(",", ":"),
    )


def work(job: tuple[str, int, int, int, bool]) -> list[tuple[str, str]]:
    path, z, x, y, metro = job
    with open(path, "rb") as f:
        data = Reader(MmapSource(f)).get(z, x, y)
    if not data:
        return []
    tile = mapbox_vector_tile.decode(gzip.decompress(data), default_options={"y_coord_down": True})
    low, high = (10, 14) if metro else BANDS[z]
    out = []

    def push(feature: dict, layer: str, props: dict, minzoom: int) -> None:
        if max(low, minzoom) <= high:
            row = emit(feature["geometry"], z, x, y, layer, props, (max(low, minzoom), high))
            if row is not None:
                out.append((layer, row))

    for feature in tile.get("places", {}).get("features", []):
        p = feature["properties"]
        name = p.get("name")
        if name is None:
            continue
        if p.get("kind") == "locality" and not metro:
            mz = float(p.get("min_zoom") or 12)
            tier = 0 if mz <= 4 else 1 if mz <= 6 else 2 if mz <= 8 else 3
            props = {"name": name, "pop": int(p.get("population") or 0), "tier": tier, "mz": mz}
            row = emit(feature["geometry"], z, x, y, "places", props, (min(12, max(0, math.floor(mz) - 1)), 12))
            if row is not None:
                out.append(("places", row))
        elif p.get("kind") in ("neighbourhood", "macrohood") and metro:
            pop = int(p.get("population") or 0)
            props = {"name": name, "kind": "district" if pop >= 250_000 else p["kind"], "pop": pop}
            row = emit(feature["geometry"], z, x, y, "hoods", props, (8 if pop >= 250_000 else 10, 14))
            if row is not None:
                out.append(("hoods", row))
    for feature in [] if metro else tile.get("water", {}).get("features", []):
        p = feature["properties"]
        if "Polygon" in feature["geometry"]["type"] and p.get("kind") != "ocean":
            push(feature, "water", {"kind": p.get("kind", "")}, 0)
        elif p.get("kind") in ("river", "canal"):
            push(feature, "rivers", {"kind": p["kind"]}, 7)
    for feature in tile.get("roads", {}).get("features", []):
        p = feature["properties"]
        if p.get("is_tunnel"):
            continue
        detail = p.get("kind_detail", "")
        if not metro and detail in ROADS:
            kind, minzoom = ROADS[detail]
            push(
                feature,
                "roads",
                {"kind": kind, "name": p.get("ref") or p.get("name") or ""},
                minzoom,
            )
        elif metro and detail in STREETS:
            kind, minzoom = STREETS[detail]
            push(
                feature,
                "streets",
                {"kind": kind, "name": p.get("name", "")},
                minzoom,
            )
        elif metro and p.get("kind") == "rail" and detail not in ("subway", "station", "ventilation_shaft"):
            push(feature, "streets", {"kind": "rail", "name": ""}, 10)
    return out


def extract(key: str, target: Path, region: object, minzoom: int, maxzoom: int) -> None:
    if target.exists():
        return
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    area = folder / f"{target.stem}.region.geojson"
    area.write_text(json.dumps(mapping(region)))
    partial = folder / f"{target.name}.part"
    subprocess.run(
        [
            "pmtiles",
            "extract",
            PLANET.format(key=key),
            str(partial),
            f"--region={area}",
            f"--minzoom={minzoom}",
            f"--maxzoom={maxzoom}",
        ],
        check=True,
    )
    partial.replace(target)


def build(scheme: str) -> str:
    setup(scheme)
    key = httpx.get(BUILDS, timeout=60).raise_for_status().json()[-1]["key"]
    folder = WORK / key.removesuffix(".pmtiles")
    for old in WORK.glob("2*"):
        if old != folder:
            shutil.rmtree(old)
    country = folder / f"{scheme}.pmtiles"
    extract(key, country, unary_union([box(*b) for zone in zones for b in zone[0]]), 0, 10)
    jobs = [
        (str(country), *t, False) for t in {t for z in BANDS for zone in zones for b in zone[0] for t in tiles(z, *b)}
    ]
    found = []
    with open(country, "rb") as f:
        reader = Reader(MmapSource(f))
        for z, x, y in {t for zone in zones for b in zone[0] for t in tiles(8, *b)}:
            data = reader.get(z, x, y)
            if not data:
                continue
            decoded = mapbox_vector_tile.decode(gzip.decompress(data), default_options={"y_coord_down": True})
            for feature in decoded.get("places", {}).get("features", []):
                pop = int(feature["properties"].get("population") or 0)
                if feature["properties"].get("kind") != "locality":
                    continue
                px, py = feature["geometry"]["coordinates"]
                lon = (x + px / 4096) / 2**z * 360 - 180
                lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / 4096) / 2**z))))
                if inside.contains(Point(lon, lat)):
                    found.append((pop, lon, lat))
    top = max(found, default=(0,))[0]
    cities = [
        affinity.scale(
            Point(lon, lat).buffer(min(0.36, 0.18 * math.sqrt(pop / METRO))),
            1 / math.cos(math.radians(lat)),
            1,
            origin=(lon, lat),
        )
        for pop, lon, lat in found
        if pop >= METRO or pop == top
    ]
    if cities:
        metros = unary_union(cities)
        city = folder / f"{scheme}.metros.pmtiles"
        extract(key, city, metros, 14, 14)
        covered = prep(metros)
        jobs += [
            (str(city), z, x, y, True)
            for z, x, y in {t for b in getattr(metros, "geoms", [metros]) for t in tiles(14, *b.bounds)}
            if covered.intersects(
                box(
                    x / 2**14 * 360 - 180,
                    math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / 2**14)))),
                    (x + 1) / 2**14 * 360 - 180,
                    math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2**14)))),
                )
            )
        ]
    staging = WORK / "staging" / scheme
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    files = {}
    seen: dict[str, list[tuple[float, float]]] = {}
    with Pool(8, initializer=setup, initargs=(scheme,)) as pool:
        for rows in pool.imap_unordered(work, jobs, chunksize=16):
            for layer, line in rows:
                if layer in ("places", "hoods"):
                    feature = json.loads(line)
                    name = feature["properties"]["name"]
                    px, py = feature["geometry"]["coordinates"][:2]
                    if any(abs(px - a) < 4000 and abs(py - b) < 4000 for a, b in seen.get(name, [])):
                        continue
                    seen.setdefault(name, []).append((px, py))
                if layer not in files:
                    files[layer] = (staging / f"{layer}.geojsons").open("w")
                files[layer].write(line + "\n")
    for handle in files.values():
        handle.close()
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = []
    for name, layers, low, high in (
        (scheme, ("places", "water", "rivers", "roads"), 0, 12),
        (f"{scheme}.metro", ("streets", "hoods"), 8, 14),
    ):
        target = OUT / f"{name}.pmtiles"
        subprocess.run(
            [
                "tippecanoe",
                "-o",
                str(target),
                "--force",
                "-t",
                str(staging),
                "--projection=EPSG:3857",
                f"-Z{low}",
                f"-z{high}",
                "--no-feature-limit",
                "--no-tile-size-limit",
                "-r1",
                "--simplification=4",
                "--detect-shared-borders",
                "-n",
                f"Basemap {name}",
                "-A",
                "Map data © OpenStreetMap contributors (ODbL); Protomaps",
                "-P",
                "-q",
                *[str(staging / f"{layer}.geojsons") for layer in layers if (staging / f"{layer}.geojsons").exists()],
            ],
            check=True,
        )
        sizes.append(f"{target.name} {target.stat().st_size / 1e6:.0f} MB")
    shutil.rmtree(staging)
    return f"{scheme}: {len(jobs)} source tiles, {len(cities)} metro areas, {', '.join(sizes)}"


def varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number: int, payload: int | bytes) -> bytes:
    if isinstance(payload, int):
        return varint(number << 3) + varint(payload)
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def glyph(font: ImageFont.FreeTypeFont, code: int, ascender: int) -> bytes:
    char = chr(code)
    size = 24 * SCALE
    pad = (BUFFER + 1) * SCALE
    advance = math.floor(font.getlength(char) / SCALE)
    canvas = Image.new("L", (3 * size, 3 * size), 0)
    ImageDraw.Draw(canvas).text((size, 2 * size), char, font=font, anchor="ls", fill=255)
    ink = canvas.getbbox()
    if ink is None:
        return field(1, code) + field(3, 0) + field(4, 0) + field(5, 0) + field(6, 0) + field(7, advance)
    left = math.floor((ink[0] - size) / SCALE)
    up = math.ceil((2 * size - ink[1]) / SCALE)
    width = math.ceil((ink[2] - size) / SCALE) - left
    height = up + math.ceil((ink[3] - 2 * size) / SCALE)
    ox, oy = size + left * SCALE - pad, 2 * size - up * SCALE - pad
    hi = np.asarray(canvas.crop((ox, oy, ox + width * SCALE + 2 * pad, oy + height * SCALE + 2 * pad))) >= 128
    edge = np.zeros_like(hi)
    edge[:-1] |= hi[:-1] != hi[1:]
    edge[1:] |= hi[:-1] != hi[1:]
    edge[:, :-1] |= hi[:, :-1] != hi[:, 1:]
    edge[:, 1:] |= hi[:, :-1] != hi[:, 1:]
    ey, ex = np.nonzero(edge)
    gy, gx = np.mgrid[0 : height + 2 * BUFFER, 0 : width + 2 * BUFFER]
    sy, sx = ((gy + 1.5) * SCALE).ravel(), ((gx + 1.5) * SCALE).ravel()
    distance = np.sqrt(((sx[:, None] - ex - 0.5) ** 2 + (sy[:, None] - ey - 0.5) ** 2).min(axis=1))
    signed = np.where(hi[sy.astype(int), sx.astype(int)], -distance, distance) / SCALE
    bitmap = np.clip(255 - 255 * (signed / RADIUS + CUTOFF), 0, 255).round().astype(np.uint8).tobytes()
    return (
        field(1, code)
        + field(2, bitmap)
        + field(3, width)
        + field(4, height)
        + field(5, (left << 1) ^ (left >> 31))
        + field(6, ((up - ascender) << 1) ^ ((up - ascender) >> 31))
        + field(7, advance)
    )


def glyphs() -> str:
    source = raw.fetch("world.basemap", FONT, suffix=".ttf")
    written = 0
    for weight, value in WEIGHTS.items():
        name = f"Sofia Sans Semi Condensed {weight}"
        font = ImageFont.truetype(str(source.path), 24 * SCALE, layout_engine=ImageFont.Layout.BASIC)
        font.set_variation_by_axes([value])
        cmap: dict[int, str] = TTFont(source.path).getBestCmap() or {}
        codes = {code for code in cmap if code < 0x10000}
        ascender = math.ceil(font.getmetrics()[0] / SCALE)
        folder = GLYPHS / name
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        for start in range(0, 0x10000, 256):
            stack = field(1, name.encode()) + field(2, f"{start}-{start + 255}".encode())
            for code in sorted(c for c in codes if start <= c < start + 256):
                stack += field(3, glyph(font, code, ascender))
            (folder / f"{start}-{start + 255}.pbf").write_bytes(field(1, stack))
            written += 1
    return f"{written} glyph ranges in {GLYPHS}"
