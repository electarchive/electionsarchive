"""Hungary's parliamentary constituency (OEVK) boundaries from the National Election Office's results service.

The service publishes one polygon per constituency in `OevkPoligonok.json`, a single ring of `latitude longitude` pairs
rounded to four decimals (about 11 m), with a center point. The 2022 file shows the 106 constituencies of the 2014
period as the 2022 election used them; the 2026 file shows the 2026 map. Each file becomes one boundary set, and one
package, `hu.valasztas/boundaries`, carries both on the constituency areas that `hu.valasztas/geo` already holds.

The office's `Korzethatar-<maz>-<taz>.json` files are not shapes: each lists the streets and house-number ranges that
belong to each polling station of a settlement, so the archive holds no polygon for a polling station or settlement.
"""

import json
from datetime import date
from typing import cast

import shapely

from ea import package, raw
from ea.labelpoint import label_point
from ea.package import Package
from ea.sources.hu import valasztas as hu

SOURCE = hu.SOURCE
KEY = f"{SOURCE}/boundaries"
ELECTIONS = {2022: ("ogy2022", date(2022, 4, 3), date(2026, 4, 12)), 2026: ("ogy2026", date(2026, 4, 12), None)}
SCHEME = "hu.oevk"


def fetch() -> None:
    for election, _, _ in ELECTIONS.values():
        raw.fetch(SOURCE, hu.vtr_url(election, "ver", "OevkPoligonok.json"), suffix=".json")
        raw.fetch(SOURCE, hu.vtr_url(election, "ver", "OevkAdatok.json"), suffix=".json")


def packages() -> list[str]:
    return [KEY]


def ring(text: str) -> shapely.Polygon:
    """The polygon of a `lat lon,lat lon,...` ring; the file leaves most rings open."""
    pairs = [tuple(float(value) for value in pair.split()) for pair in text.split(",")]
    return shapely.Polygon([(lon, lat) for lat, lon in pairs])


def repaired(polygon: shapely.Polygon) -> tuple[shapely.MultiPolygon, bool]:
    """The polygon as a valid multipolygon, and whether rounding had made its ring cross itself."""
    if polygon.is_valid:
        return shapely.MultiPolygon([polygon]), False
    repaired_shape = shapely.make_valid(polygon)
    members = shapely.get_parts(repaired_shape)
    while any(isinstance(member, (shapely.GeometryCollection, shapely.MultiPolygon)) for member in members):
        members = [part for member in members for part in shapely.get_parts(member)]
    fixed = shapely.MultiPolygon([member for member in members if isinstance(member, shapely.Polygon)])
    if fixed.is_empty or abs(fixed.area - polygon.area) > 0.01 * polygon.area:
        raise ValueError("repairing a constituency ring changed its shape")
    return fixed, True


def parse() -> None:
    p = Package(
        KEY,
        SOURCE,
        "Hungary's parliamentary constituency boundaries for the 2022 and 2026 elections, from the National Election "
        "Office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    for year, (election, since, until) in ELECTIONS.items():
        polygons = raw.cached(SOURCE, hu.vtr_url(election, "ver", "OevkPoligonok.json"))
        p.artifact(polygons, hu.NVI)
        p.artifact(raw.cached(SOURCE, hu.vtr_url(election, "ver", "OevkAdatok.json")), hu.NVI)
        rows = cast(list[dict[str, str]], json.loads(polygons.path.read_bytes())["list"])
        expected = {
            (str(row["maz"]).zfill(2), str(row["evk"]).zfill(2))
            for row in cast(list[dict[str, object]], hu.vtr_json(election, "ver", "OevkAdatok.json")["list"])
        }
        found = {(row["maz"].zfill(2), row["evk"].zfill(2)) for row in rows}
        if found != expected or len(rows) != len(found):
            raise ValueError(f"{election}: polygons {sorted(found ^ expected)} differ from the constituency list")
        period = hu.oevk_period(since)
        fixed_rings = 0
        for row in rows:
            geometry, fixed = repaired(ring(row["poligon"]))
            fixed_rings += fixed
            p.add(
                "area_geometry",
                area_id=hu.district(period, row["maz"].zfill(2), row["evk"].zfill(2)),
                valid_from=since,
                valid_to=until,
                geom=shapely.to_wkb(shapely.set_srid(geometry, 4326), hex=True, include_srid=True),
                label_point=label_point(shapely.to_geojson(geometry)),
                quality="official",
                source_id=hu.NVI,
            )
        p.add(
            "boundary_set",
            id=p.id("boundary_set", SCHEME, year),
            scheme_code=SCHEME,
            vintage=str(year),
            valid_from=since,
            valid_to=until,
            as_of=since,
        )
        print(f"{year}: {len(rows)} constituencies, {fixed_rings} rings repaired")
    p.write()


def emit() -> None:
    package.run("load", packages())
