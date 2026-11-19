"""Bolivia's municipal outlines of the 2004 plan, and the 2015 plan for one municipality that lacks it.

GeoBolivia, the state's spatial data infrastructure, published the municipal plan of 2004 (327 municipalities) and the
plan of 2015 (339) as separate layers. The 2015 plan already draws the outline of every municipality that the
referendum package names, from geoBoundaries' copy of it. This module adds the 2004 plan: each municipality that
existed then gets that outline, valid from the plan's year until the 2015 plan takes over. The twelve municipalities
created between the two plans have no 2004 outline. The 2015 layer also supplies the outline of the one municipality
that the referendum package holds twice under two spellings.

The layers are the copies of GeoBolivia's GeoNode records that the geodatos project saved to the Internet Archive.
Each layer's six-digit codes are the national statistics institute's. The table beside this module gives, for each
municipality area of the referendum package, the code it has in each plan; the build checks that every pair of
outlines agrees on the ground.
"""

import csv
import json
from datetime import date
from pathlib import Path

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.bo import oep_referendos

SOURCE = "bo.geobolivia"
GEO = f"{SOURCE}/geo"
ARCHIVE = "https://archive.org/download/geodatosbolivia_planificacion_geobolivia_geonode_"
URL_2004 = ARCHIVE + "municipios2004_21d9f924/dataset.geojson"
URL_2015 = ARCHIVE + "municipios339_07191289/dataset.geojson"
PAGE = "https://archive.org/details/geodatosbolivia_planificacion_geobolivia_geonode_"
TABLE = Path(__file__).with_name("geobolivia_municipios.tsv")
SCHEME = "bo.municipio"
FROM_2004 = date(2004, 1, 1)
FROM_2015 = date(2015, 1, 1)
# The referendum package holds the municipality of Fernández Alonso twice, under this spelling too.
TWIN = ("7:fernandez-alonzo", "7:fernandez-alonso")
# The least share of the smaller outline that must lie within the other, for a pair of outlines taken as one place.
AGREEMENT = 0.6
# Outlines are kept to a grid of about ten centimeters.
GRID = 1e-6


def fetch() -> None:
    for url in (URL_2004, URL_2015):
        raw.fetch(SOURCE, url, suffix=".geojson")


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def table() -> list[dict[str, str]]:
    with TABLE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def layer(url: str, code: str) -> dict[str, shapely.Geometry]:
    """The outline of each municipality in a layer by its code; the layer's other features have no code."""
    features = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["features"]
    parts: dict[str, list[shapely.Geometry]] = {}
    for feature in features:
        found = feature["properties"].get(code)
        if feature["properties"].get("municipio" if code == "c_ut" else "nombre") and found:
            parts.setdefault(str(found), []).append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    return {found: shapely.union_all(shapes) for found, shapes in parts.items()}


def multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    gridded = shapely.set_precision(shapely.make_valid(shape), GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    if not polygons:
        raise ValueError("an outline holds no polygon")
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


def agree(one: shapely.Geometry, other: shapely.Geometry) -> float:
    return one.intersection(other).area / min(one.area, other.area)


def outline(p: Package, id: str, shape: shapely.MultiPolygon, source: str, since: date, until: date | None) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        valid_to=until,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=source,
    )


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of Bolivia's municipalities under GeoBolivia's plan of 2004, valid until the plan of 2015",
        depends=[f"{oep_referendos.SOURCE}/geo"],
    )
    source_2004, source_2015 = p.id("source", "municipios-2004"), p.id("source", "municipios-2015")
    for id, title, url, notes in (
        (
            source_2004,
            "GeoBolivia, municipal boundaries of 2004 (327 municipalities)",
            PAGE + "municipios2004_21d9f924",
            (
                "The plan of the municipalities as they stood at the municipal elections of December 2004. The "
                "layer names its year and no day, so the outlines are dated from 1 January 2004. No plan of 2009 "
                "was found: these outlines stand until the plan of 2015 takes over on 1 January 2015, so a vote "
                "between the two is drawn on them."
            ),
        ),
        (
            source_2015,
            "GeoBolivia, municipal boundaries of 2015 (339 municipalities)",
            PAGE + "municipios339_07191289",
            "The plan of the 339 municipalities as they stood in 2015, dated from 1 January 2015.",
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="official_body",
            publisher="GeoBolivia (Infraestructura de Datos Espaciales del Estado Plurinacional de Bolivia)",
            title=title,
            url=url,
            license="Public domain (GeoBolivia)",
            attribution="GeoBolivia; copy saved to the Internet Archive by the geodatos project",
            notes=notes,
        )
    p.artifact(raw.cached(SOURCE, URL_2004), source_2004)
    p.artifact(raw.cached(SOURCE, URL_2015), source_2015)
    p.add(
        "boundary_set",
        id=p.id("boundary_set", SCHEME, "2004"),
        scheme_code=SCHEME,
        vintage="2004",
        valid_from=FROM_2004,
        valid_to=FROM_2015,
        as_of=FROM_2004,
    )
    plan_2004 = layer(URL_2004, "codigo")
    plan_2015 = layer(URL_2015, "c_ut")
    rows = table()
    taken: set[str] = set()
    for row in rows:
        code, in_2015, in_2004 = row["area_code"], row["ine2015"], row["ine2004"]
        if in_2015 not in plan_2015:
            raise KeyError(f"{in_2015} is not in the plan of 2015")
        if not in_2004:
            continue
        if in_2004 in taken or in_2004 not in plan_2004:
            raise KeyError(f"{in_2004} is repeated or not in the plan of 2004")
        taken.add(in_2004)
        share = agree(plan_2004[in_2004], plan_2015[in_2015])
        if share < AGREEMENT:
            raise ValueError(f"{code}: the outlines of 2004 and 2015 agree on {share:.2f} of the smaller")
        shape = multipolygon(plan_2004[in_2004])
        for id in _areas(code):
            outline(p, id, shape, source_2004, FROM_2004, FROM_2015)
    if taken != set(plan_2004):
        raise ValueError(f"the plan of 2004 holds municipalities that no area takes: {sorted(set(plan_2004) - taken)}")
    twin = _id(TWIN[0])
    home = next(row for row in rows if row["area_code"] == TWIN[1])
    outline(p, twin, multipolygon(plan_2015[home["ine2015"]]), source_2015, FROM_2015, None)
    p.add(
        "note",
        id=p.id("note", twin),
        entity_id=twin,
        kind="caveat",
        body_md=(
            f"The referendum package holds this municipality twice: as Fernández Alonso (`{TWIN[1]}`) and under "
            "this spelling. Both areas carry the same outlines."
        ),
        lang="en",
    )
    return p


def _id(code: str) -> str:
    return oep_referendos.area(SCHEME, code)


def _areas(code: str) -> list[str]:
    """The areas that take the outline of a municipality: its own, and its twin's."""
    return [_id(code), *([_id(TWIN[0])] if code == TWIN[1] else [])]
