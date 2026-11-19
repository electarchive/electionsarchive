"""Outlines for the departments, municipalities, and the Guatemala district of `gt.tse`.

The shapes are geoBoundaries' gbOpen release for Guatemala: its first level (22 departments, drawn from OpenStreetMap)
and its second (342 municipal outlines, from the national disaster-reduction coordinator's data through OCHA). The
second level has no department, so each outline takes the department whose first-level outline holds most of it. An
outline matches the Tribunal's municipality by its department and its name, folded to plain capitals. The second level
also holds the lakes of Amatitlán and Atitlán, which no Tribunal municipality names; they get no area.

The Distrito Central is the municipality of Guatemala, so it takes that municipality's outline. The Guatemala district,
which elects its deputies apart from the Distrito Central, is the rest of the department: the union of the other
municipal outlines of department 1.
"""

import functools
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import date
from uuid import uuid5

import shapely
import shapely.ops
from shapely.geometry import shape

from ea import package, raw
from ea.package import Package

SOURCE = "gt.tse"
BASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/GTM/"
ADM1 = BASE + "ADM1/geoBoundaries-GTM-ADM1.geojson"
ADM2 = BASE + "ADM2/geoBoundaries-GTM-ADM2.geojson"
URLS = (ADM1, ADM2)
VINTAGE_1 = date(2017, 1, 1)
VINTAGE_2 = date(2021, 1, 1)
LAKES = {"LAGO DE AMATITLAN", "LAGO DE ATITLAN"}
CAPITAL = "0101"


def _id(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


DEPARTMENTS_SOURCE = _id("source", "geoboundaries-adm1")
MUNICIPALITIES_SOURCE = _id("source", "geoboundaries-adm2")


def _features(url: str) -> list[dict[str, object]]:
    data = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    return list(data["features"])


def _shape(feature: dict[str, object]) -> shapely.Geometry:
    found = shape(feature["geometry"])  # type: ignore[arg-type]
    if not found.is_valid:
        found = shapely.make_valid(found)
    return found


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    polygons = [g for g in shapely.get_parts(geometry) if g.geom_type == "Polygon" and not g.is_empty]
    if not polygons:
        raise ValueError("no polygon in an outline")
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _name(feature: dict[str, object]) -> str:
    props = feature["properties"]
    assert isinstance(props, dict)
    return str(props["shapeName"])


@functools.cache
def departments() -> dict[str, shapely.Geometry]:
    """The first-level outlines by folded name."""
    return {fold(_name(f)): _shape(f) for f in _features(ADM1)}


@functools.cache
def municipalities(dep_names: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], shapely.Geometry]:
    """The second-level outlines by (department code, folded name), the lakes left out."""
    deps = departments()
    code_of = {fold(name): code for code, name in dep_names}
    found: dict[tuple[str, str], shapely.Geometry] = {}
    for feature in _features(ADM2):
        name = fold(_name(feature))
        if name in LAKES:
            continue
        geometry = _shape(feature)
        best = max(deps, key=lambda d: shapely.area(shapely.intersection(deps[d], geometry)))
        key = (code_of[best], name)
        if key in found:
            raise ValueError(f"two outlines for {key}")
        found[key] = geometry
    return found


def add(
    p: Package,
    area: Callable[[str, str], str],
    deps: dict[str, str],
    towns: dict[str, tuple[str, str]],
) -> dict[str, int]:
    """Add the outlines to the geo package.

    `deps` maps each department code to its name; `towns` maps each municipality code to its department code and name.
    """
    for url in URLS:
        p.artifact(raw.cached(SOURCE, url), DEPARTMENTS_SOURCE if url == ADM1 else MUNICIPALITIES_SOURCE)
    p.add(
        "source",
        id=DEPARTMENTS_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap boundaries)",
        title="geoBoundaries gbOpen Guatemala ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="Open Database License 1.0 (OpenStreetMap contributors), through geoBoundaries (CC BY 4.0)",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="OpenStreetMap contributors (Wambacher), through geoBoundaries (William & Mary geoLab)",
    )
    p.add(
        "source",
        id=MUNICIPALITIES_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Guatemalan municipal boundaries)",
        title="geoBoundaries gbOpen Guatemala ADM2, 2021",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO (Coordinadora Nacional para la Reducción de Desastres, OCHA) through geoBoundaries "
        "(CC BY 4.0)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Coordinadora Nacional para la Reducción de Desastres (CONRED) and OCHA, through geoBoundaries "
        "(William & Mary geoLab)",
    )
    for scheme, vintage in (("gt.departamento", VINTAGE_1), ("gt.municipio", VINTAGE_2), ("gt.distrito", VINTAGE_2)):
        p.add(
            "boundary_set",
            id=_id("boundary_set", scheme, vintage.year),
            scheme_code=scheme,
            vintage=str(vintage.year),
            valid_from=None,
            valid_to=None,
            as_of=vintage,
        )

    def outline(id: str, geometry: shapely.Geometry, source: str, quality: str) -> None:
        whole = _multi(geometry)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=None,
            valid_to=None,
            geom=shapely.to_wkb(whole, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
            ),
            quality=quality,
            source_id=source,
        )

    first = departments()
    for code, name in sorted(deps.items()):
        outline(area("gt.departamento", code), first[fold(name)], DEPARTMENTS_SOURCE, "osm")
    second = municipalities(tuple(sorted(deps.items())))
    used: set[tuple[str, str]] = set()
    rest: list[shapely.Geometry] = []
    for code, (dep, name) in sorted(towns.items()):
        key = (dep, fold(name))
        if key not in second:
            raise ValueError(f"municipality {code} {name} has no outline")
        used.add(key)
        outline(area("gt.municipio", code), second[key], MUNICIPALITIES_SOURCE, "official")
        if dep == "01" and code != CAPITAL:
            rest.append(second[key])
    left = set(second) - used
    if left:
        raise ValueError(f"outlines without a municipality: {sorted(left)}")
    outline(area("gt.distrito", "01"), shapely.union_all(rest), MUNICIPALITIES_SOURCE, "official")
    return {"departments": len(deps), "municipalities": len(towns), "districts": 1}
