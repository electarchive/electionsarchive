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
from shapely import LineString, affinity, box, clip_by_rect, get_dimensions, make_valid, unary_union
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

from ea import display, raw
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
ASPECTS = (0.45, 2.5)
BROAD = 6
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
ATTRIBUTION = "Map data © OpenStreetMap contributors (ODbL); Protomaps"
OHM = "https://planet.openhistoricalmap.org/planet/planet-{date}_0000.osm.pbf"
OHM_ATTRIBUTION = "Historical data © OpenHistoricalMap contributors (CC0, CC BY; credits in the credits file)"
RAIL_ZOOM = {
    "rail": 5,
    "narrow_gauge": 8,
    "light_rail": 9,
    "preserved": 9,
    "monorail": 10,
    "funicular": 10,
    "tram": 10,
    "subway": 10,
}
BORDER_IDS = {"tr": {2801012, 2864001, 2863989}}
BORDER_LEVELS = {"de": ({"2", "3", "4"}, ((1950, 1, 1), (1980, 1, 1)))}
MUTED = {
    "before": "1970-01-01",
    "hide": ["roads", "streets", "places", "hoods"],
    "keep": ["earth", "bounds", "water"],
}

zones: list[tuple[list[list[float]], list[float] | None, Transformer, list[float] | None]] = []
land: BaseGeometry
inside: PreparedGeometry
near: PreparedGeometry


def setup(scheme: str) -> None:
    global land, inside, near
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
    near = prep(unary_union([box(*b) for zone in zones for b in zone[0]] + surround(framed=True)))


def tiles(z: int, w: float, s: float, e: float, n: float) -> list[tuple[int, int, int]]:
    span = 2**z
    x0, x1 = int((w + 180) / 360 * span), int((e + 180) / 360 * span)
    y0 = int((1 - math.asinh(math.tan(math.radians(n))) / math.pi) / 2 * span)
    y1 = int((1 - math.asinh(math.tan(math.radians(s))) / math.pi) / 2 * span)
    return [(z, x, y) for x in range(x0, min(x1, span - 1) + 1) for y in range(y0, min(y1, span - 1) + 1)]


def cell(z: int, x: int, y: int) -> BaseGeometry:
    def lat(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / 2**z))))

    return box(x / 2**z * 360 - 180, lat(y + 1), (x + 1) / 2**z * 360 - 180, lat(y))


def surround(framed: bool) -> list[BaseGeometry]:
    within, _, project, _ = zones[-1]
    back = Transformer.from_crs(project.target_crs, "EPSG:4326", always_xy=True)
    bounds = [project.transform_bounds(*b, densify_pts=64) for b in within]
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    ratio = min(2, max(0.8, (x1 - x0) / (y1 - y0) * 1.04))
    low, high = (ratio * 0.84, ratio) if framed else ASPECTS
    wide, tall = max(x1 - x0, (y1 - y0) * high) * 0.6, max(y1 - y0, (x1 - x0) / low) * 0.6
    xs, ys = np.meshgrid((x0 + x1) / 2 + np.linspace(-wide, wide, 257), (y0 + y1) / 2 + np.linspace(-tall, tall, 257))
    lons, lats = back.transform(xs.ravel(), ys.ravel())
    keep = np.isfinite(lons) & np.isfinite(lats) & (np.abs(lons) <= 180) & (np.abs(lats) <= 90)
    span = 2**BROAD
    tx = np.clip(((lons[keep] + 180) / 360 * span).astype(int), 0, span - 1)
    mercator = np.arcsinh(np.tan(np.radians(np.clip(lats[keep], -85.05, 85.05))))
    ty = np.clip(((1 - mercator / np.pi) / 2 * span).astype(int), 0, span - 1)
    hit = set(zip(tx.tolist(), ty.tolist(), strict=True))
    reach = {((x + dx) % span, y + dy) for x, y in hit for dx in (-1, 0, 1) for dy in (-1, 0, 1) if 0 <= y + dy < span}
    return [cell(BROAD, x, y) for x, y in reach]


def walk(coords, fn):
    if isinstance(coords[0], (int, float)):
        return fn(coords)
    return [walk(part, fn) for part in coords]


def emit(
    geometry: dict | BaseGeometry,
    z: int,
    x: int,
    y: int,
    layer: str,
    props: dict,
    zooms: tuple[int, int],
    outside: bool = False,
) -> str | None:
    span, extent = 2**z, 4096
    raw_shape = geometry if isinstance(geometry, BaseGeometry) else shape(geometry)
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
    return settle(geom, layer, props, zooms, outside)


def settle(geom: BaseGeometry, layer: str, props: dict, zooms: tuple[int, int], outside: bool = False) -> str | None:
    w, s, e, n = geom.bounds
    cx, cy = (w + e) / 2, (s + n) / 2
    zone = next(
        (zone for zone in zones if any(bw <= cx <= be and bs <= cy <= bn for bw, bs, be, bn in zone[0])),
        None,
    )
    if zone is None:
        if not outside:
            return None
        zone = zones[-1]
    _, clip, project, scaling = zone
    if clip is not None:
        geom = clip_by_rect(geom, *clip)
    if outside:
        english = props.get("en")
        props = {key: value for key, value in props.items() if key != "en"}
        if "name" in props and not inside.contains(geom):
            props |= {"out": 1, "name": english or props["name"]}
    elif not inside.contains(geom):
        if not inside.intersects(geom):
            return None
        geom = make_valid(geom).intersection(land)
    if geom.is_empty or geom.geom_type == "GeometryCollection":
        return None
    if zone is not zones[-1]:
        props = props | {"inset": 1}

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


def work(job: tuple[str, int, int, int, bool, list[tuple[int, int, int, int]] | None]) -> list[tuple[str, str]]:
    path, z, x, y, metro, rest = job
    with open(path, "rb") as f:
        data = Reader(MmapSource(f)).get(z, x, y)
    if not data:
        return []
    tile = mapbox_vector_tile.decode(gzip.decompress(data), default_options={"y_coord_down": True})
    low, high = (10, 14) if metro else BANDS[z]
    spare = None if rest is None else box(0, 0, 4096, 4096).difference(unary_union([box(*r) for r in rest]))
    out = []

    def push(geometry: dict | BaseGeometry, layer: str, props: dict, minzoom: int, outside: bool = False) -> None:
        geometry = geometry if isinstance(geometry, BaseGeometry) else shape(geometry)
        bands = [(low, high, geometry)]
        if outside and spare is not None and not spare.is_empty:
            piece = make_valid(geometry).intersection(spare)
            if piece.geom_type == "GeometryCollection":
                piece = unary_union([g for g in piece.geoms if get_dimensions(g) == get_dimensions(geometry)])
            bands.append((high + 1, 12, piece))
        for start, end, part in bands:
            if max(start, minzoom) <= end and not part.is_empty:
                row = emit(part, z, x, y, layer, props, (max(start, minzoom), end), outside)
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
            props = {
                "name": name,
                "en": p.get("name:en"),
                "pop": int(p.get("population") or 0),
                "tier": tier,
                "mz": mz,
            }
            row = emit(feature["geometry"], z, x, y, "places", props, (min(12, max(0, math.floor(mz) - 1)), 12), True)
            if row is not None:
                out.append(("places", row))
        elif p.get("kind") in ("neighbourhood", "macrohood") and metro:
            pop = int(p.get("population") or 0)
            props = {"name": name, "kind": "district" if pop >= 250_000 else p["kind"], "pop": pop}
            row = emit(feature["geometry"], z, x, y, "hoods", props, (8 if pop >= 250_000 else 10, 14))
            if row is not None:
                out.append(("hoods", row))
    earth = (
        [] if metro else [f for f in tile.get("earth", {}).get("features", []) if "Polygon" in f["geometry"]["type"]]
    )
    for feature in earth:
        push(feature["geometry"], "earth", {}, 0, True)
    seen = metro or near.intersects(cell(z, x, y))
    ground = unary_union([make_valid(shape(f["geometry"])) for f in earth]) if earth else None
    for feature in [] if ground is None else tile.get("boundaries", {}).get("features", []):
        kind = feature["properties"].get("kind")
        if kind not in ("country", "region", "county"):
            continue
        cut = shape(feature["geometry"]).intersection(ground)
        lines = [part for part in getattr(cut, "geoms", [cut]) if "LineString" in part.geom_type]
        if lines:
            push(unary_union(lines), "bounds", {"kind": kind}, 8 if kind == "county" else 0 if seen else 5, True)
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
            push(feature["geometry"], "streets", {"kind": "rail", "name": ""}, 10)
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
    core = [b for zone in zones for b in zone[0]]
    extract(key, country, unary_union([box(*b) for b in core]), 0, 10)
    wide = unary_union([box(*b) for b in core] + surround(framed=False))
    broad = folder / f"{scheme}.wide.pmtiles"
    extract(key, broad, wide, 0, BROAD)
    fine = {z: {t for b in core for t in tiles(z, *b)} for z in (8, 10)}
    jobs = []
    for z, path, area, finer in (
        (6, broad, getattr(wide, "geoms", [wide]), fine[8]),
        (8, country, [box(*b) for b in core], fine[10]),
    ):
        for t in {t for part in area for t in tiles(z, *part.bounds)}:
            covered = [
                ((cx % 4) * 1024, (cy % 4) * 1024, (cx % 4 + 1) * 1024, (cy % 4 + 1) * 1024)
                for _, cx, cy in finer
                if cx // 4 == t[1] and cy // 4 == t[2]
            ]
            jobs.append((str(path), *t, False, None if len(covered) == 16 else covered))
    jobs += [(str(country), *t, False, None) for t in fine[10]]
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
            (str(city), z, x, y, True, None)
            for z, x, y in {t for b in getattr(metros, "geoms", [metros]) for t in tiles(14, *b.bounds)}
            if covered.intersects(cell(z, x, y))
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
    with (staging / "water.geojsons").open("w") as handle:
        for lake in (lake for part in getattr(wide, "geoms", [wide]) for lake in display.lakes(part.bounds)):
            row = settle(shape(lake), "water", {}, (0, 12), True)
            if row is not None:
                handle.write(row + "\n")
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = []
    for name, layers, low, high in (
        (scheme, ("earth", "bounds", "places", "water", "roads"), 0, 12),
        (f"{scheme}.metro", ("streets", "hoods"), 8, 14),
    ):
        target = OUT / f"{name}.pmtiles"
        pack(target, f"Basemap {name}", ATTRIBUTION, low, high, staging, layers)
        sizes.append(f"{target.name} {target.stat().st_size / 1e6:.0f} MB")
    shutil.rmtree(staging)
    return f"{scheme}: {len(jobs)} source tiles, {len(cities)} metro areas, {', '.join(sizes)}"


def historical(schemes: list[str], date: str) -> str:
    """Builds the OpenHistoricalMap layers (railways, German boundaries, Turkish and Syrian outlines) for each scheme
    from the planet of `date` (such as 261111), plus the credits and the modern-layer config. See `ea.ohm` for the
    license and date rules."""
    import sqlite3

    from ea import ohm

    planet = raw.fetch("world.ohm", OHM.format(date=date), suffix=".osm.pbf").path
    admin = raw.fetch("world.natural_earth", ADMIN, suffix=".geojson")
    world = unary_union([shape(f["geometry"]) for f in json.loads(admin.path.read_text())["features"]]).buffer(0.02)
    regions: dict[str, tuple[PreparedGeometry, PreparedGeometry]] = {}
    for scheme in schemes:
        setup(scheme)
        regions[scheme] = (near, inside)
    folder = WORK / "ohm" / date
    shutil.rmtree(folder, ignore_errors=True)
    for scheme in schemes:
        (folder / scheme).mkdir(parents=True)
    tally = ohm.Tally()

    def selects(scheme: str, tags: dict[str, str]) -> bool:
        if scheme not in BORDER_LEVELS or tags.get("boundary") != "administrative":
            return False
        levels, dates = BORDER_LEVELS[scheme]
        props = ohm.stamp(tags)
        return (
            tags.get("admin_level") in levels
            and props is not None
            and any(ohm.valid_on(props, ohm.days(*day)) for day in dates)
        )

    found = ohm.relations(
        planet,
        lambda rid, tags: any(rid in BORDER_IDS.get(s, ()) or selects(s, tags) for s in schemes),
    )
    keep = {ref for _, members in found.values() for ref, _ in members}
    handles = {scheme: (folder / scheme / "rail.wgs").open("w") for scheme in schemes}

    def rail(points: list[tuple[float, float]], tags: dict[str, str]) -> None:
        line = LineString(points)
        props = ohm.stamp(tags)
        vetted = None if props is None else ohm.vet(tags, tally)
        for scheme, (area, ground) in regions.items():
            if not area.intersects(line):
                continue
            beyond = not ground.intersects(line)
            if props is None:
                tally.counts[(scheme, "rail", "dropped_undated" if "start_date" not in tags else "dropped_date")] += 1
            elif vetted is None:
                tally.counts[(scheme, "rail", "dropped_license")] += 1
            else:
                tally.counts[(scheme, "rail", vetted[0])] += 1
                row = {"kind": tags["railway"], **props}
                if beyond:
                    tally.counts[(scheme, "rail_outside", vetted[0])] += 1
                    row["out"] = 1
                if vetted[1] is not None:
                    tally.credits[(scheme, "rail", vetted[1])] += 1
                    row["c"] = vetted[1].id
                zoom = RAIL_ZOOM[tags["railway"]] if "service" not in tags else max(9, RAIL_ZOOM[tags["railway"]])
                handles[scheme].write(
                    json.dumps({"p": row, "z": zoom, "g": mapping(line)}, separators=(",", ":")) + "\n"
                )

    store = sqlite3.connect(folder / "ways.db")
    ohm.ways(planet, folder, keep, rail, store)
    for handle in handles.values():
        handle.close()
    borders: dict[str, list[tuple[BaseGeometry, dict[str, str | int]]]] = {scheme: [] for scheme in schemes}
    for rid, (tags, members) in found.items():
        geometry, members_credits = ohm.assemble(store, members, tally)
        if geometry is None and members_credits is not None:
            continue
        clipped = geometry.intersection(world) if geometry is not None else None
        point = None if clipped is None or clipped.is_empty else clipped.representative_point()
        targets = [
            scheme
            for scheme, (_, ground) in regions.items()
            if rid in BORDER_IDS.get(scheme, ())
            or (point is not None and selects(scheme, tags) and ground.contains(point))
        ]
        props = ohm.stamp(tags)
        vetted = None if props is None or members_credits is None else ohm.vet(tags, tally)
        for scheme in targets:
            if props is None:
                tally.counts[
                    (scheme, "borders", "dropped_undated" if "start_date" not in tags else "dropped_date")
                ] += 1
            elif vetted is None:
                tally.counts[(scheme, "borders", "dropped_license")] += 1
            elif clipped is not None and not clipped.is_empty:
                tally.counts[(scheme, "borders", vetted[0])] += 1
                credits = {*(members_credits or []), *([vetted[1]] if vetted[1] is not None else [])}
                for credit in credits:
                    tally.credits[(scheme, "borders", credit)] += 1
                info: dict[str, str | int] = {"id": rid, "level": int(tags.get("admin_level", 0)), **props}
                name = tags.get("name") or tags.get("name:en") or tags.get("name:de")
                if name is not None:
                    info["name"] = name
                if credits:
                    info["c"] = ",".join(sorted(credit.id for credit in credits))
                borders[scheme].append((clipped, info))
    store.close()
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    for scheme in schemes:
        setup(scheme)
        staging = folder / scheme
        written = {"rail": 0, "borders": 0}
        with (staging / "rail.geojsons").open("w") as out, (staging / "rail.wgs").open() as source:
            for line in source:
                item = json.loads(line)
                row = settle(shape(item["g"]), "rail", item["p"], (item["z"], 12), True)
                if row is not None:
                    out.write(row + "\n")
                    written["rail"] += 1
        with (staging / "borders.geojsons").open("w") as out:
            for geometry, props in borders[scheme]:
                row = settle(geometry, "borders", props, (0 if props["level"] == 2 else 4, 12), True)
                if row is not None:
                    out.write(row + "\n")
                    written["borders"] += 1
        target = OUT / f"{scheme}.historical.pmtiles"
        pack(target, f"Historical basemap {scheme}", OHM_ATTRIBUTION, 0, 12, staging, ("rail", "borders"))
        counts: dict[str, dict[str, int]] = {}
        for (s, theme, outcome), n in sorted(tally.counts.items()):
            if s == scheme:
                counts.setdefault(theme, {})[outcome] = n
        sources: dict[str, dict[str, str]] = {}
        features: dict[str, dict[str, int]] = {}
        for (s, theme, credit), n in sorted(
            tally.credits.items(), key=lambda item: (item[0][0], item[0][1], item[0][2].id)
        ):
            if s == scheme:
                sources[credit.id] = {
                    "id": credit.id,
                    "license": credit.license,
                    "source": credit.source,
                    "attribution": credit.attribution,
                }
                features.setdefault(credit.id, {})[theme] = n
        (OUT / f"{scheme}.historical.credits.json").write_text(
            json.dumps(
                {
                    "planet": date,
                    "provider": "OpenHistoricalMap",
                    "url": "https://www.openhistoricalmap.org/",
                    "note": "Features without a license tag are kept as the planet's default CC0. Every feature below with a credit id in its c property is CC BY or an attribution-only equivalent and must be credited as listed.",
                    "tiles": written,
                    "counts": counts,
                    "sources": [{**entry, "features": features[key]} for key, entry in sources.items()],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        report.append(
            f"{scheme}: {written['rail']} rail, {written['borders']} borders, {len(sources)} credits, {target.stat().st_size / 1e6:.0f} MB; {json.dumps(counts)}"
        )
    (OUT / "historical.json").write_text(
        json.dumps(
            {
                "planet": date,
                "days": "days since 1970-01-01",
                "valid": "s <= day < e; a missing e means open",
                "muted": MUTED,
                "rejected_licenses": dict(tally.rejected.most_common()),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return "\n".join(report)


def pack(
    target: Path, title: str, attribution: str, low: int, high: int, staging: Path, layers: tuple[str, ...]
) -> None:
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
            title,
            "-A",
            attribution,
            "-P",
            "-q",
            *[str(staging / f"{layer}.geojsons") for layer in layers if (staging / f"{layer}.geojsons").exists()],
        ],
        check=True,
    )


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
