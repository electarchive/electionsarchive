import json

import numpy as np
import polars as pl
from pyproj import Transformer
from shapely import (
    LineString,
    MultiPolygon,
    Polygon,
    box,
    clip_by_rect,
    coverage_simplify,
    from_wkb,
    get_parts,
    line_merge,
    make_valid,
    transform,
    unary_union,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from ea.sources.world.natural_earth import PARSED
from ea.store import ROOT

OUT = ROOT / "apps" / "web" / "src" / "lib" / "server" / "world.json"
WIDTH = 1000
EARTH = "+proj=eqearth +lon_0=0"
EUROPE = "+proj=laea +lat_0=52 +lon_0=10"
EUROPE_BOX = (-45.0, 25.0, 80.0, 82.0)
EUROPE_FRAME = ((-11.0, 52.5), (31.0, 70.6), (36.0, 34.3), (-9.8, 36.5), (14.5, 35.6), (44.8, 39.7), (43.4, 41.4))
EU = {
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
}


def number(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def ring(coords: np.ndarray, closed: bool = True) -> str:
    points = np.round((coords[:-1] if closed else coords) * 10).astype(np.int64)
    steps = np.diff(points, axis=0)
    steps = steps[(steps != 0).any(axis=1)]
    if len(steps) < (2 if closed else 1):
        return ""
    out = [f"M{number(points[0][0] / 10)} {number(points[0][1] / 10)}l"]
    for dx, dy in steps:
        y = number(dy / 10)
        out.append(f"{number(dx / 10)}{y if y.startswith('-') else ' ' + y} ")
    return "".join(out).rstrip() + ("z" if closed else "")


def path(geometry: BaseGeometry) -> str:
    rings: list[str] = []
    for part in get_parts(geometry):
        if not isinstance(part, Polygon):
            continue
        rings.append(ring(np.asarray(part.exterior.coords)))
        rings.extend(ring(np.asarray(hole.coords)) for hole in part.interiors)
    return "".join(rings)


def polygonal(geometry: BaseGeometry) -> BaseGeometry:
    parts = [part for part in get_parts(make_valid(geometry)) if isinstance(part, Polygon) and not part.is_empty]
    return MultiPolygon(parts) if parts else Polygon()


def trimmed(geometry: BaseGeometry, smallest: float) -> BaseGeometry:
    parts = sorted(get_parts(geometry), key=lambda part: part.area, reverse=True)
    kept = [part for index, part in enumerate(parts) if index == 0 or part.area >= smallest]
    return MultiPolygon(
        [Polygon(part.exterior, [hole for hole in part.interiors if Polygon(hole).area >= smallest]) for part in kept]
    )


def anchor(geometry: BaseGeometry) -> list[float]:
    largest = max(get_parts(geometry), key=lambda part: part.area)
    point = polylabel(largest, tolerance=0.5)
    return [round(point.x, 1), round(point.y, 1)]


def view(shapes: dict[str, BaseGeometry], tolerance: float, smallest: float) -> dict[str, dict[str, object]]:
    keys = [key for key, shape in shapes.items() if not shape.is_empty]
    simplified = coverage_simplify(np.array([shapes[key] for key in keys], dtype=object), tolerance)
    countries: dict[str, dict[str, object]] = {}
    for key, shape in zip(keys, simplified, strict=True):
        cleaned = polygonal(shape)
        if cleaned.is_empty:
            continue
        cleaned = trimmed(cleaned, smallest)
        countries[key] = {"d": path(cleaned), "at": anchor(cleaned), "area": round(cleaned.area, 1), "shape": cleaned}
    return countries


def border(countries: dict[str, dict[str, object]]) -> str:
    shapes = {key: entry.pop("shape") for key, entry in countries.items()}
    inside = unary_union([shape for key, shape in shapes.items() if key.upper() in EU])
    outside = unary_union([shape for key, shape in shapes.items() if key.upper() not in EU])
    shared = line_merge(
        unary_union(
            [part for part in get_parts(inside.boundary.intersection(outside.boundary)) if isinstance(part, LineString)]
        )
    )
    return "".join(
        ring(np.asarray(part.coords), closed=False)
        for part in get_parts(shared)
        if isinstance(part, LineString) and part.length >= 15
    )


def build() -> str:
    frame = pl.read_parquet(PARSED / "countries.parquet").filter(
        pl.col("geom").is_not_null() & (pl.col("code") != "ATA")
    )
    lonlat = {
        (iso2 or code).lower(): polygonal(from_wkb(bytes.fromhex(geom)))
        for code, iso2, geom in frame.select("code", "iso2", "geom").iter_rows()
    }
    earth = Transformer.from_crs("EPSG:4326", EARTH, always_xy=True)
    europe = Transformer.from_crs("EPSG:4326", EUROPE, always_xy=True)
    back = Transformer.from_crs(EUROPE, "EPSG:4326", always_xy=True)

    def project(transformer: Transformer, shape: BaseGeometry) -> BaseGeometry:
        return transform(shape, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))

    world = {key: project(earth, shape) for key, shape in lonlat.items()}
    x0, y0, x1, y1 = unary_union(list(world.values())).bounds
    pad = (x1 - x0) * 0.004
    scale = WIDTH / (x1 - x0 + 2 * pad)
    height = round((y1 - y0 + 2 * pad) * scale, 1)

    def fit_world(xy: np.ndarray) -> np.ndarray:
        return np.column_stack(((xy[:, 0] - x0 + pad) * scale, (y1 + pad - xy[:, 1]) * scale))

    world = {key: polygonal(transform(shape, fit_world)) for key, shape in world.items()}
    world_countries = view(world, 1.0, 2.5)
    for entry in world_countries.values():
        entry.pop("shape")

    edges = np.array(europe.transform([lon for lon, _ in EUROPE_FRAME], [lat for _, lat in EUROPE_FRAME])).T
    ex0, ey0 = edges.min(axis=0)
    ex1, ey1 = edges.max(axis=0)
    margin = (ex1 - ex0) * 0.03
    ex0, ex1, ey0, ey1 = ex0 - margin, ex1 + margin, ey0 - margin, ey1 + margin
    escale = WIDTH / (ex1 - ex0)
    eheight = round((ey1 - ey0) * escale, 1)
    region = box(*EUROPE_BOX)

    def fit_europe(xy: np.ndarray) -> np.ndarray:
        return np.column_stack(((xy[:, 0] - ex0) * escale, (ey1 - xy[:, 1]) * escale))

    near = {key: polygonal(shape.intersection(region)) for key, shape in lonlat.items() if shape.intersects(region)}
    inset = {
        key: polygonal(clip_by_rect(project(europe, shape), ex0, ey0, ex1, ey1))
        for key, shape in near.items()
        if not shape.is_empty
    }
    inset = {key: polygonal(transform(shape, fit_europe)) for key, shape in inset.items() if not shape.is_empty}
    europe_countries = view(inset, 1.8, 6.0)
    europe_border = border(europe_countries)

    steps = np.linspace(0, 1, 24)
    outline = np.vstack(
        [
            np.column_stack((ex0 + (ex1 - ex0) * steps, np.full_like(steps, ey1))),
            np.column_stack((np.full_like(steps, ex1), ey1 - (ey1 - ey0) * steps)),
            np.column_stack((ex1 - (ex1 - ex0) * steps, np.full_like(steps, ey0))),
            np.column_stack((np.full_like(steps, ex0), ey0 + (ey1 - ey0) * steps)),
        ]
    )
    lon, lat = back.transform(outline[:, 0], outline[:, 1])
    traced = fit_world(np.column_stack(earth.transform(lon, lat)))
    corners = [traced[0], traced[24], traced[48], traced[72]]
    data = {
        "world": {
            "width": WIDTH,
            "height": height,
            "countries": world_countries,
            "frame": ring(np.vstack([traced, traced[:1]])),
            "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in corners],
        },
        "europe": {
            "width": WIDTH,
            "height": eheight,
            "countries": europe_countries,
            "border": europe_border,
            "members": sorted(key for key in europe_countries if key.upper() in EU),
        },
    }
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    OUT.write_text(text + "\n")
    return (
        f"wrote {OUT.relative_to(ROOT)}: {len(world_countries)} countries in the world view ({height} high), "
        f"{len(europe_countries)} in the Europe inset ({eheight} high), {len(text):,} bytes"
    )
