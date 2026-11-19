"""The boundaries of Jamaica's parishes, parliamentary constituencies, and local electoral divisions.

The Electoral Office of Jamaica's GIS Department publishes them on a public ArcGIS service, in the layers it made for
the 2016 local government elections. The constituency layer outlines the 63 constituencies, and the division layer
the 228 electoral divisions with the councillor elected in each. Only the outlines are used here.
"""

import json
import re
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.jm import ecj

SOURCE = "jm.eoj"
KEY = f"{SOURCE}/geo"
SERVICE = "https://services6.arcgis.com/3R3y1KXaPJ9BFnsU/ArcGIS/rest/services/LGE_2016/FeatureServer"
QUERY = "query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
PARISHES, CONSTITUENCIES, DIVISIONS = (f"{SERVICE}/{layer}/{QUERY}" for layer in (0, 1, 2))
JAMAICA = ecj.JAMAICA
PARISH = "jm.parish"
CONSTITUENCY = "jm.constituency"
DIVISION = "jm.electoral_division"
# Sixty-three constituencies have elected the House since the general election of 29 December 2011, and the
# divisions are those of the local government election of 28 November 2016, which the layers were made for.
CONSTITUENCIES_FROM = date(2011, 12, 29)
DIVISIONS_FROM = date(2016, 11, 28)
CONTAINED = 0.95


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "gis")


def packages() -> list[str]:
    return [KEY]


def fetch() -> None:
    for url in (PARISHES, CONSTITUENCIES, DIVISIONS):
        raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def title(name: str) -> str:
    return re.sub(r"'S\b", "'s", " ".join(name.split()).title())


def parish(name: str) -> str:
    return key("area", PARISH, name.strip().upper())


def constituency(number: int) -> str:
    return key("area", CONSTITUENCY, number)


def division(constituency_number: int, number: int) -> str:
    return key("area", DIVISION, constituency_number, number)


def shapes(url: str, count: int) -> list[tuple[dict[str, object], shapely.MultiPolygon]]:
    collection = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    found: list[tuple[dict[str, object], shapely.MultiPolygon]] = []
    for feature in collection["features"]:
        shape = shapely.force_2d(shapely.geometry.shape(feature["geometry"]))
        if not shape.is_valid:
            shape = shapely.make_valid(shape)
        parts = [part for part in getattr(shape, "geoms", [shape]) if part.geom_type == "Polygon" and part.area > 0]
        if not parts:
            raise ValueError(f"{feature['properties']}: no polygon")
        found.append((feature["properties"], shapely.MultiPolygon(parts)))
    if len(found) != count:
        raise ValueError(f"{url}: {len(found)} features, expected {count}")
    return found


def wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def geo() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The shapes of Jamaica's 14 parishes, 63 parliamentary constituencies, and 228 local electoral divisions, from "
        "the Electoral Office of Jamaica's GIS Department",
        depends=[ecj.GENERAL_REGISTRY],
    )
    p.external.add(JAMAICA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Electoral Office of Jamaica, GIS Department",
        title="Electoral boundaries of Jamaica: parishes, constituencies, and electoral divisions (2016 local "
        "government election layers)",
        url=SERVICE,
        license="No license stated; published on the Electoral Office's public map service",
        attribution="Electoral Office of Jamaica, GIS Department",
        notes=(
            "The service's metadata gives the electoral divisions' accuracy as ±5 meters, the projection as JAD 2001, "
            "the data source as the Electoral Office of Jamaica, and the last modification as March 2017. The "
            "constituency and division layers describe the hierarchy of regions, constituencies, electoral divisions, "
            "and polling divisions. The outlines are the boundaries in force for the 2016 local government election; "
            "the Portmore parishes' boundaries under consultation since are not in them."
        ),
    )
    for url in (PARISHES, CONSTITUENCIES, DIVISIONS):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("scheme", code=PARISH, polity_id=JAMAICA, kind="administrative", rank=3, noun="parish")
    p.add("scheme", code=DIVISION, polity_id=JAMAICA, kind="electoral_district", rank=2, noun="electoral division")

    parishes = shapes(PARISHES, 14)
    constituencies = shapes(CONSTITUENCIES, 63)
    divisions = shapes(DIVISIONS, 228)
    vintage = raw.cached(SOURCE, DIVISIONS).retrieved_at.date()
    for scheme, start in ((CONSTITUENCY, CONSTITUENCIES_FROM), (DIVISION, DIVISIONS_FROM)):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, vintage),
            scheme_code=scheme,
            vintage=f"Electoral Office of Jamaica, 2016 local government election layers, retrieved {vintage}",
            valid_from=start,
            as_of=vintage,
        )
    p.add(
        "boundary_set",
        id=key("boundary_set", PARISH, vintage),
        scheme_code=PARISH,
        vintage=f"Electoral Office of Jamaica, 2016 local government election layers, retrieved {vintage}",
        as_of=vintage,
    )

    def put(id: str, scheme: str, code: str, name: str, shape: shapely.MultiPolygon, start: date | None) -> None:
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "en", "official")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=start,
            geom=wkb(shape),
            label_point=wkb(shapely.point_on_surface(shape)),
            quality="official",
            source_id=SOURCE_ID,
        )

    parish_shapes: dict[str, shapely.MultiPolygon] = {}
    for properties, shape in parishes:
        name = str(properties["PARISH_NAM"])
        parish_shapes[name.strip().upper()] = shape
        put(parish(name), PARISH, name.strip().upper().replace(".", "").replace(" ", "-"), title(name), shape, None)

    shapes_by_number: dict[int, shapely.MultiPolygon] = {}
    for properties, shape in constituencies:
        number = int(str(properties["CONST_NO"]))
        name = str(properties["CONST_NAME"])
        home = str(properties["PARISH_NAM"]).strip().upper()
        if home not in parish_shapes:
            raise ValueError(f"constituency {number} {name}: unknown parish {home}")
        shapes_by_number[number] = shape
        put(constituency(number), CONSTITUENCY, str(number), title(name), shape, CONSTITUENCIES_FROM)
        p.add(
            "area_parent",
            area_id=constituency(number),
            parent_id=parish(home),
            valid_from=CONSTITUENCIES_FROM,
        )
    if sorted(shapes_by_number) != list(range(1, 64)):
        raise ValueError("the constituencies are not numbered 1 to 63")

    seen: set[tuple[int, int]] = set()
    for properties, shape in divisions:
        number = int(str(properties["CONST_NO_1"]))
        ed = int(str(properties["ED_NO_1"]))
        name = str(properties["ED_NAME_1"])
        if (number, ed) in seen or number not in shapes_by_number:
            raise ValueError(f"division {number}.{ed} {name}: repeated or in an unknown constituency")
        seen.add((number, ed))
        inside = shape.intersection(shapes_by_number[number]).area / shape.area
        if inside < CONTAINED:
            raise ValueError(f"division {number}.{ed} {name}: {inside:.0%} inside constituency {number}")
        put(division(number, ed), DIVISION, f"{number}.{ed}", title(name), shape, DIVISIONS_FROM)
        p.add("area_parent", area_id=division(number, ed), parent_id=constituency(number), valid_from=DIVISIONS_FROM)
    return p
