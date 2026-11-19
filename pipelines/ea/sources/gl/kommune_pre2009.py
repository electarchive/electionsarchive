"""Shapes of Greenland's 18 municipalities of 1979 to 2008, from a schematic map of them.

No agency publishes boundary data for the municipalities that existed until the end of 2008: they were merged into four
on 1 January 2009 and the current boundaries (geoBoundaries) hold only the five of 2018. The one redistributable source
that draws the old ones is NordNordWest's map "Greenland municipalities merged.svg" on Wikimedia Commons (CC BY-SA
3.0), made in 2008 from a drawn outline of Greenland. It colors the 18 municipalities by the four they merged into and
leaves the Northeast Greenland National Park, which was never a municipality, in grey.

The drawing is not georeferenced, so the shapes are fitted to the coast:

1. The filled outlines are read as polygons in the drawing's own units and matched to the 18 areas by the map's labels
   and by the colors of the four 2009 municipalities (`_POLYGONS`).
2. The drawing's outline is registered to the main island of the geoBoundaries national outline (ADM0) in a polar
   stereographic projection (true scale at 70 degrees north, central meridian 45 degrees west) by a similarity
   transform (scale, rotation, translation). The constants in `_FIT` come from maximizing the overlap of the two
   outlines; the overlap is 0.83 by intersection over union. The fit is checked on every build (`_check`).
3. The thin white borders between the polygons are closed by buffering each polygon by `_CLOSE` drawing units
   (about 700 m) and giving each polygon up what the polygons before it already hold.

One drawing unit is about 1.2 km on the ground. The coast and the borders between municipalities are drawn
by hand and simplified. The shapes therefore carry the quality `synthetic`, so the site flags them, and they locate a
municipality rather than measure it. The current municipalities of geoBoundaries serve as a check: each of the five is
compared with the union of the old municipalities it took over.
"""

import json
from datetime import date

import numpy as np
import shapely
import shapely.affinity
import shapely.geometry
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

from ea import raw
from ea.package import Package
from ea.sources.gl import svgmap

SOURCE = "gl.commons"
SVG_URL = "https://upload.wikimedia.org/wikipedia/commons/5/58/Greenland_municipalities_merged.svg"
PAGE_URL = "https://commons.wikimedia.org/wiki/File:Greenland_municipalities_merged.svg"
GEOBOUNDARIES = "gl.geoboundaries"
_RELEASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/GRL"
OUTLINE_URL = f"{_RELEASE}/ADM0/geoBoundaries-GRL-ADM0_simplified.geojson"
CURRENT_URL = f"{_RELEASE}/ADM1/geoBoundaries-GRL-ADM1_simplified.geojson"
VALID_TO = date(2009, 1, 1)

# The filled paths of the drawing in document order, with the municipality each one draws and the fill of the
# municipality of 2009 it merged into. The two grey paths are the National Park.
_POLYGONS: list[tuple[str | None, str]] = [
    (None, "#D3D3D3"),
    (None, "#D3D3D3"),
    ("Qaanaaq", "#8AD1C9"),
    ("Upernavik", "#8AD1C9"),
    ("Ittoqqortoormiit", "#D0E3A3"),
    ("Uummannaq", "#8AD1C9"),
    ("Ilulissat", "#8AD1C9"),
    ("Qeqertarsuaq", "#8AD1C9"),
    ("Qasigiannguit", "#8AD1C9"),
    ("Kangaatsiaq", "#8AD1C9"),
    ("Tasiilaq", "#D0E3A3"),
    ("Sisimiut", "#AFDAB7"),
    ("Maniitsoq", "#AFDAB7"),
    ("Nuuk", "#D0E3A3"),
    ("Paamiut", "#D0E3A3"),
    ("Narsaq", "#91CD9F"),
    ("Ivittuut", "#D0E3A3"),
    ("Qaqortoq", "#91CD9F"),
    ("Nanortalik", "#91CD9F"),
    ("Aasiaat", "#8AD1C9"),
]
# The current municipalities that took over each old one (geoBoundaries names); Ivittuut went to Sermersooq.
_CURRENT = {
    "Kujalleq": ("Nanortalik", "Qaqortoq", "Narsaq"),
    "Sermersooq": ("Ivittuut", "Paamiut", "Nuuk", "Tasiilaq", "Ittoqqortoormiit"),
    "Qeqqata": ("Maniitsoq", "Sisimiut"),
    "Qeqertalik": ("Kangaatsiaq", "Aasiaat", "Qasigiannguit", "Qeqertarsuaq"),
    "Avannaata": ("Ilulissat", "Uummannaq", "Upernavik", "Qaanaaq"),
}

_PROJECTION = "+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +datum=WGS84"
_FIT = {"scale": 8.35430881e-4, "rotation": 3.55195398, "dx": 518.850762, "dy": -650.375306}
"""Drawing units per meter, the rotation in degrees counterclockwise, and the offset, from the projected coast."""
_CLOSE = 0.6
_MIN_FIT = 0.8
_MIN_CURRENT = 0.3


def fetch() -> None:
    raw.fetch(SOURCE, SVG_URL, suffix=".svg")
    raw.fetch(SOURCE, PAGE_URL, suffix=".html")
    raw.fetch(GEOBOUNDARIES, OUTLINE_URL, suffix=".geojson")
    raw.fetch(GEOBOUNDARIES, CURRENT_URL, suffix=".geojson")


def _geojson(url: str) -> list[dict]:
    return json.loads(raw.cached(GEOBOUNDARIES, url).path.read_text(encoding="utf-8"))["features"]


def _to_ground(shape: BaseGeometry) -> BaseGeometry:
    """Drawing units to longitude and latitude, inverting the fit."""
    f = _FIT
    shape = shapely.affinity.translate(shape, -f["dx"], -f["dy"])
    shape = shapely.affinity.rotate(shape, -f["rotation"], origin=(0, 0))
    shape = shapely.affinity.scale(shape, 1 / f["scale"], -1 / f["scale"], origin=(0, 0))
    inverse = Transformer.from_crs(CRS.from_proj4(_PROJECTION), 4326, always_xy=True)
    return shapely.transform(shape, lambda a: np.column_stack(inverse.transform(a[:, 0], a[:, 1])))


def _iou(a: BaseGeometry, b: BaseGeometry) -> float:
    a, b = shapely.make_valid(a), shapely.make_valid(b)
    both = a.intersection(b).area
    return both / (a.area + b.area - both)


def _check(drawn: dict[str, BaseGeometry]) -> dict[str, float]:
    """Check the fit against the national outline and the current municipalities; return the latter's overlaps."""
    outline = shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in _geojson(OUTLINE_URL)])
    main = max(shapely.get_parts(outline), key=lambda g: g.area)
    # The drawing includes the National Park, so it is compared with the whole main island.
    fit = _iou(shapely.union_all(list(drawn.values())).simplify(0.02), main.simplify(0.02))
    if fit < _MIN_FIT:
        raise ValueError(f"the drawing lies on Greenland only to {fit:.2f}")
    current = {f["properties"]["shapeName"]: shapely.geometry.shape(f["geometry"]) for f in _geojson(CURRENT_URL)}
    overlaps = {}
    for new, olds in _CURRENT.items():
        overlaps[new] = _iou(shapely.union_all([drawn[o] for o in olds]).simplify(0.02), current[new].simplify(0.02))
        if overlaps[new] < _MIN_CURRENT:
            raise ValueError(f"the old municipalities of {new} cover it only to {overlaps[new]:.2f}")
    return overlaps


def shapes() -> tuple[dict[str, shapely.MultiPolygon], dict[str, float]]:
    """The 18 municipalities' shapes in longitude and latitude, by name, and the checks' overlaps."""
    svg = raw.cached(SOURCE, SVG_URL).path.read_text(encoding="utf-8")
    drawn = svgmap.filled_polygons(svg)
    if [(c) for c, _ in drawn] != [c for _, c in _POLYGONS]:
        raise ValueError("the drawing's fills do not match the polygons expected")
    closed: dict[str, BaseGeometry] = {}
    taken: BaseGeometry | None = None
    for i, ((name, _), (_, shape)) in enumerate(zip(_POLYGONS, drawn, strict=True)):
        shape = shape.buffer(_CLOSE, join_style="mitre")
        if taken is not None:
            shape = shapely.difference(shape, taken)
        taken = shape if taken is None else shapely.union(taken, shape)
        closed[name or f"park{i}"] = shape
    out: dict[str, shapely.MultiPolygon] = {}
    ground = {name: shapely.make_valid(_to_ground(shape).simplify(0.0005)) for name, shape in closed.items()}
    overlaps = _check(ground)
    for name, shape in ground.items():
        if name.startswith("park"):
            continue
        polygons = [g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3 and g.area > 0]
        out[name] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return out, overlaps


def add(p: Package, area: dict[str, str], valid_from: date | None = None) -> dict[str, float]:
    """Add the sources and one `area_geometry` row for each municipality (names as keys of `area`)."""
    source = p.id("source", "commons-greenland-municipalities")
    outline = p.id("source", "geoboundaries-grl")
    p.add(
        "source",
        id=source,
        kind="community",
        publisher="Wikimedia Commons (NordNordWest)",
        title="Greenland municipalities merged.svg: schematic map of the municipalities of Greenland until 2008",
        url=PAGE_URL,
        license="CC BY-SA 3.0",
        license_url="https://creativecommons.org/licenses/by-sa/3.0/",
        attribution="NordNordWest, CC BY-SA 3.0, via Wikimedia Commons; outline after Greenland.svg by carol",
    )
    p.add(
        "source",
        id=outline,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Greenland ADM0 and ADM1 (simplified), the outline the shapes are registered to",
        url="https://www.geoboundaries.org/",
        license="CC BY-SA 2.0",
        license_url="https://creativecommons.org/licenses/by-sa/2.0/",
        attribution="geoBoundaries (William & Mary geoLab)",
    )
    p.artifact(raw.cached(SOURCE, SVG_URL), source)
    p.artifact(raw.cached(SOURCE, PAGE_URL), source)
    p.artifact(raw.cached(GEOBOUNDARIES, OUTLINE_URL), outline)
    p.artifact(raw.cached(GEOBOUNDARIES, CURRENT_URL), outline)
    result, overlaps = shapes()
    if set(result) != set(area):
        raise ValueError("a municipality has no shape")
    for name in sorted(result):
        shape = result[name]
        p.add(
            "area_geometry",
            area_id=area[name],
            valid_from=valid_from,
            valid_to=VALID_TO,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="synthetic",
            source_id=source,
        )
    return overlaps
