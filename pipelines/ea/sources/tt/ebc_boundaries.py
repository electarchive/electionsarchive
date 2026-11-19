"""Trinidad and Tobago's constituency boundaries of the Elections and Boundaries Commission Order, 2015.

The commission's interactive electoral districts map loads one GeoJSON file with a polygon for each of the 41
constituencies. Its names are those of the 2014 boundary review, and each feature's count of polling divisions
matches the counts that review's Order (Legal Notice No. 61 of 2015) gives for the ten constituencies it altered,
so the shapes are dated to that plan: from the Order of 30 April 2015 to the Order of 13 May 2020 that gave effect
to the 2019 review. The 7 September 2015 election is the one election held on them.
"""

import json
import time
from datetime import date

import httpx
import shapely
from pyproj import Geod

from ea import package, raw
from ea.package import Package
from ea.sources.tt.ebc import PUBLISHER, SITE, TRINIDAD, area, norm

SOURCE = "tt.ebc_boundaries"
MAP_PAGE = f"{SITE}/electoral-districts/interactive-district-map/"
MAP = f"{SITE}/wp-content/plugins/maps/map-parliamentary-districts.json"
ORDER_2015 = f"{SITE}/wp-content/uploads/Legal-Notice-61.-Boundary-Report-2014.pdf"
PRINTED_MAP = f"{SITE}/wp-content/uploads/Parliamentary-Approved-2014.pdf"
FILES = {MAP: ".json", ORDER_2015: ".pdf", PRINTED_MAP: ".pdf"}
# The Elections and Boundaries Commission Order, 2015 (Legal Notice No. 61, Legal Supplement Part B of 30 April 2015)
# and the Elections and Boundaries Commission Order, 2020 (Legal Notice No. 94 of 13 May 2020) that replaced it.
VALID_FROM = date(2015, 4, 30)
VALID_TO = date(2020, 5, 13)
ELECTION = date(2015, 9, 7)
# The polling divisions of the constituencies the 2015 Order altered, as its summaries of adjustments give them. The
# map's counts must match them; the other constituencies' counts grew as divisions were split after the Order.
ORDER_DIVISIONS = {
    "COUVANORTH": 39,
    "DIEGOMARTINWEST": 48,
    "DIEGOMARTINCENTRAL": 42,
    "DIEGOMARTINNORTHEAST": 42,
    "PORTOFSPAINNORTHSTANNSWEST": 59,
    "PORTOFSPAINSOUTH": 64,
    "SANFERNANDOEAST": 45,
}
# Trinidad and Tobago's land area is about 5,128 km²; the shapes include offshore islands and coastal water.
AREA_KM2 = (5_000, 5_300)


def fetch() -> None:
    for url, suffix in FILES.items():
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=suffix)
                break
            except httpx.HTTPError:
                if attempt == 5:
                    raise
                time.sleep(3 * (attempt + 1))


def _shape(rings: list[list[list[float]]]) -> shapely.MultiPolygon:
    """The file encodes some islands as further rings of one polygon; read the rings by the even-odd rule."""
    out: shapely.Geometry = shapely.Polygon()
    for ring in rings:
        out = shapely.symmetric_difference(out, shapely.Polygon(ring))
    valid = shapely.make_valid(out)
    parts = [g for g in getattr(valid, "geoms", [valid]) if g.geom_type in {"Polygon", "MultiPolygon"}]
    polygons = shapely.union_all(parts)
    if polygons.geom_type == "Polygon":
        polygons = shapely.MultiPolygon([polygons])
    if polygons.geom_type != "MultiPolygon" or polygons.is_empty:
        raise ValueError("a constituency's rings make no polygon")
    return polygons


def shapes() -> dict[str, tuple[str, shapely.MultiPolygon]]:
    data = json.loads(raw.cached(SOURCE, MAP).path.read_bytes())
    found: dict[str, tuple[str, shapely.MultiPolygon]] = {}
    for feature in data["features"]:
        properties = feature["properties"]
        name = str(properties["Constituency"])
        code = norm(name)
        geometry = feature["geometry"]
        rings = (
            geometry["coordinates"]
            if geometry["type"] == "Polygon"
            else [ring for polygon in geometry["coordinates"] for ring in polygon]
        )
        if code in found:
            raise ValueError(f"the map repeats {name}")
        if code in ORDER_DIVISIONS and int(properties["PD"]) != ORDER_DIVISIONS[code]:
            raise ValueError(f"{name} has {properties['PD']} polling divisions, not the 2015 Order's")
        found[code] = (name, _shape(rings))
    if len(found) != 41:
        raise ValueError(f"the map has {len(found)} constituencies, not 41")
    geod = Geod(ellps="WGS84")
    total = sum(abs(geod.geometry_area_perimeter(shape)[0]) for _, shape in found.values()) / 1e6
    if not AREA_KM2[0] < total < AREA_KM2[1]:
        raise ValueError(f"the constituencies cover {total:,.0f} km²")
    return found


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def _wkb(geometry: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(geometry, 4326), hex=True, include_srid=True)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The shapes of Trinidad and Tobago's 41 constituencies under the Elections and Boundaries Commission Order, "
        "2015, from the commission's interactive electoral districts map",
        depends=["tt.ebc/geo"],
    )
    p.external.add(TRINIDAD)
    source_id = p.id("source", "map")
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher=PUBLISHER,
        title="Interactive electoral districts map: constituency boundaries",
        url=MAP_PAGE,
        license="Official public record, no license stated",
        attribution=PUBLISHER,
        notes=(
            "The map's GeoJSON gives the 41 constituencies of the 2014 boundary review, which the Elections and "
            "Boundaries Commission Order, 2015 (Legal Notice No. 61) made law; its polling division counts for the "
            "constituencies that Order altered match the Order's. Islands the file stores as extra rings of one "
            "polygon are read as separate polygons."
        ),
    )
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), source_id)
    for code, (_, shape) in sorted(shapes().items()):
        p.add(
            "area_geometry",
            area_id=area(code),
            valid_from=VALID_FROM,
            valid_to=VALID_TO,
            geom=_wkb(shape),
            label_point=_wkb(shapely.point_on_surface(shape)),
            quality="official",
            source_id=source_id,
        )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", "tt.constituency", "2015"),
        scheme_code="tt.constituency",
        vintage="2015",
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        as_of=ELECTION,
    )
    return p


def emit() -> None:
    package.run("load", packages())
