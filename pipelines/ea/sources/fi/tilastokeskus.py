"""Statistics Finland's statistical area boundaries from its open WFS, as shapes for the areas of `fi.vaalit`."""

import functools
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import shapely

from ea import raw
from ea.package import Package

SOURCE = "fi.tilastokeskus"
WFS = "https://geo.stat.fi/geoserver/wfs"
PUBLISHER = "Tilastokeskus"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
FIRST, LAST = 2013, 2026


@dataclass(frozen=True)
class Layer:
    """One boundary layer: the archive's scheme, the WFS layer stem, the code field, and the years published."""

    scheme: str
    stem: str
    field: str
    years: range


LAYERS = (
    Layer("fi.kunta", "kunta1000k", "kunta", range(FIRST, LAST + 1)),
    Layer("fi.vaalipiiri", "vaalipiiri1000k", "vaalipiiri", range(2019, LAST + 1)),
    Layer("fi.hyvinvointialue", "hyvinvointialue1000k", "hyvinvointialue", range(2022, LAST + 1)),
)
SCHEMES = {layer.scheme for layer in LAYERS}


def url(layer: Layer, year: int) -> str:
    return (
        f"{WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=tilastointialueet:{layer.stem}_{year}"
        "&outputFormat=application/json&srsName=EPSG:4326"
    )


def urls() -> list[str]:
    return [url(layer, year) for layer in LAYERS for year in layer.years]


def _valid(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file).get("features"):
            raise ValueError(f"{path} holds no features")


def fetch() -> None:
    for address in urls():
        raw.fetch(SOURCE, address, suffix=".json", check=_valid)


def _multipolygon(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    polygons = [polygon for part in parts for polygon in shapely.get_parts(part)]
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


@functools.cache
def features(scheme: str, year: int) -> list[dict[str, Any]]:
    (found,) = [item for item in LAYERS if item.scheme == scheme]
    with raw.cached(SOURCE, url(found, year)).path.open("rb") as file:
        return json.load(file)["features"]


def names(scheme: str) -> dict[str, str]:
    """The Finnish name of each of a scheme's codes, the newest vintage's name winning."""
    (found,) = [item for item in LAYERS if item.scheme == scheme]
    return {
        str(feature["properties"][found.field]): str(feature["properties"]["nimi"])
        for year in found.years
        for feature in features(scheme, year)
    }


@functools.cache
def layer(scheme: str, year: int) -> dict[str, shapely.Geometry]:
    """The shapes of one scheme in the vintage of 1 January of a year, by Statistics Finland's code."""
    (found,) = [item for item in LAYERS if item.scheme == scheme]
    shapes: dict[str, shapely.Geometry] = {}
    for feature in features(scheme, year):
        code = str(feature["properties"][found.field])
        if code in shapes:
            raise ValueError(f"{found.stem}_{year} repeats code {code}")
        shapes[code] = _multipolygon(shapely.from_geojson(json.dumps(feature["geometry"])))
    return shapes


def spans(scheme: str) -> dict[str, list[tuple[date, date | None, shapely.Geometry]]]:
    """Each code's shapes over valid time, one span per run of consecutive vintages with the same outline. A vintage
    holds from 1 January of its year to 1 January of the next; the newest one stays open."""
    (found,) = [item for item in LAYERS if item.scheme == scheme]
    out: dict[str, list[tuple[date, date | None, shapely.Geometry]]] = {}
    for year in found.years:
        until = None if year == found.years[-1] else date(year + 1, 1, 1)
        for code, shape in layer(scheme, year).items():
            runs = out.setdefault(code, [])
            if runs and runs[-1][1] == date(year, 1, 1) and shapely.equals_exact(runs[-1][2], shape, 0):
                runs[-1] = (runs[-1][0], until, runs[-1][2])
            else:
                runs.append((date(year, 1, 1), until, shape))
    return out


def add_source(p: Package, source_id: str) -> None:
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher=PUBLISHER,
        title="Tilastointialueet (Statistics Finland statistical area boundaries, 1:1,000,000, WFS)",
        url="https://www.stat.fi/org/avoindata/paikkatietoaineistot.html",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Tilastokeskus (Statistics Finland)",
    )
    for address in urls():
        p.artifact(raw.cached(SOURCE, address), source_id)


def outline(p: Package, area_id: str, source_id: str, since: date, until: date | None, shape: shapely.Geometry) -> None:
    p.add(
        "area_geometry",
        area_id=area_id,
        valid_from=since,
        valid_to=until,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=source_id,
    )
