"""Landmælingar Íslands' open boundary layers: the Althing constituencies of 1959 to 2002 and since 2003, and the
municipalities of 1904 onward, as shapes for the areas of `is.hagstofa`."""

import functools
import json
from datetime import date, timedelta
from typing import Any
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "is.lmi"
WFS = "https://gis.lmi.is/geoserver/wfs"
PUBLISHER = "Landmælingar Íslands (National Land Survey of Iceland)"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_ID = str(uuid5(package.NAMESPACE, f"is.hagstofa\x1fsource\x1f{SOURCE}"))

LAYERS = {
    "old": "althingi:kjordaemi_1959_2002",
    "mid": "althingi:kjordaemi_2003_2021",
    "late": "althingi:kjordaemi_2022_2024",
    "now": "althingi:kjordaemi",
    "muni": "LMI_vektor:Sveitarfelog_timalina",
}

# The printed name of each constituency layer feature, and the archive's constituency code.
OLD = {
    "Reykjavík": "reykjavik-1959",
    "Reykjaneskjördæmi": "reykjanes-1959",
    "Vesturlandskjördæmi": "west-1959",
    "Vestfjarðarkjördæmi": "westfjords-1959",
    "Norðulandskjördæmi vestra": "northwest-1959",
    "Norðurlandskjördæmi eystra": "northeast-1959",
    "Austurlandskjördæmi": "east-1959",
    "Suðurlandskjördæmi": "south-1959",
    # The layer draws the Westman Islands, which voted in the South constituency, as a feature of its own.
    "Suðurkjördæmi": "south-1959",
}
NEW = {
    "Norðvesturkjördæmi": "northwest-2003",
    "Norðausturkjördæmi": "northeast-2003",
    "Suðurkjördæmi": "south-2003",
    "Suðvesturkjördæmi": "southwest-2003",
    "Reykjavíkurkjördæmi suður": "reykjavik-south-2003",
    "Reykjavíkurkjördæmi norður": "reykjavik-north-2003",
}

# Local forms of a municipality's name that Statistics Iceland prints in another form.
RENAMED = {
    "Blönduósbær": "Blönduóssbær",
    "Blönduóshreppur": "Blönduóssbær",
    "Hveragerðisbær": "Hveragerði",
    "Húnaþing Vestra": "Húnaþing vestra",
    "Siglufjarðarkaupstaður": "Siglufjörður",
    "Ólafsfjarðarkaupstaður": "Ólafsfjarðarbær",
    "Ólafsfjörður": "Ólafsfjarðarbær",
    "Kópavogsbær": "Kópavogur",
    "Kópavogshreppur": "Kópavogur",
    "Mosfellshreppur": "Mosfellsbær",
    "Seltjarnarneshreppur": "Seltjarnarnes",
    "Grindavíkurhreppur": "Grindavík",
}
# Names Statistics Iceland gives a council elected for a merger that took effect after the vote; the area is the
# territory of the municipality the merger made.
MERGED = {
    "Sameinað sveitarfélag Arnarneshrepps og Hörgárbyggðar": "Hörgársveit",
    "Sandgerði og Garður": "Suðurnesjabær",
}


def url(layer: str) -> str:
    return (
        f"{WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames={LAYERS[layer]}"
        "&outputFormat=application/json&srsName=EPSG:4326"
    )


def _valid(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file).get("features"):
            raise ValueError(f"{path} holds no features")


def fetch() -> None:
    for layer in LAYERS:
        raw.fetch(SOURCE, url(layer), suffix=".json", check=_valid)


@functools.cache
def features(layer: str) -> list[dict[str, Any]]:
    with raw.cached(SOURCE, url(layer)).path.open("rb") as file:
        return json.load(file)["features"]


def _multipolygon(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    polygons = [polygon for part in parts for polygon in shapely.get_parts(part)]
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


def _day(text: str | None) -> date | None:
    return date.fromisoformat(text[:10]) if text else None


Span = tuple[date, date | None, shapely.Geometry]


def _span(properties: dict[str, Any], start: str, end: str, shape: dict[str, Any]) -> Span:
    """A layer's feature over valid time: the layer's last day is inclusive, so it ends the day after."""
    first, last = _day(properties[start]), _day(properties[end])
    assert first is not None
    until = None if last is None or last.year >= 2050 else last + timedelta(days=1)
    return first, until, _multipolygon(shapely.from_geojson(json.dumps(shape)))


def constituencies() -> dict[str, list[Span]]:
    """The outline of each constituency code over its years."""
    pieces: dict[tuple[str, date, date | None], list[shapely.Geometry]] = {}
    for layer, names in (("old", OLD), ("mid", NEW), ("late", NEW), ("now", NEW)):
        for feature in features(layer):
            props = feature["properties"]
            code = names[props["kjordaemi"]]
            first, until, shape = _span(props, "ar_fra", "ar_til", feature["geometry"])
            pieces.setdefault((code, first, until), []).append(shape)
    out: dict[str, list[Span]] = {}
    for (code, first, until), shapes in sorted(pieces.items(), key=lambda item: (item[0][0], item[0][1])):
        shape = _multipolygon(shapely.union_all(shapes)) if len(shapes) > 1 else shapes[0]
        runs = out.setdefault(code, [])
        if runs and runs[-1][1] == first and shapely.equals_exact(runs[-1][2], shape, 0):
            runs[-1] = (runs[-1][0], until, runs[-1][2])
        else:
            runs.append((first, until, shape))
    return out


def municipalities(known: set[str], aliases: dict[str, str]) -> tuple[dict[str, list[Span]], list[str]]:
    """The outline of each municipality that Statistics Iceland names, over valid time, and the layer's names that
    match none of them. A name that two municipalities of different years both bore goes to the one that lasted
    longest, which is the one that held the elections the tables report."""
    found: dict[str, dict[int, list[Span]]] = {}
    unmatched: set[str] = set()
    for feature in features("muni"):
        props = feature["properties"]
        printed = props["sveitarfelag"]
        name = aliases.get(printed, printed)
        name = RENAMED.get(name, name)
        if name not in known:
            unmatched.add(printed)
            continue
        found.setdefault(name, {}).setdefault(props["nrsveitarfelags"], []).append(
            _span(props, "upphaf_timabils", "endir_timabils", feature["geometry"])
        )
    out: dict[str, list[Span]] = {}
    for name, by_code in found.items():
        if len(by_code) > 1:
            lasted = max(by_code, key=lambda code: max((s[1] or date.max) for s in by_code[code]))
            by_code = {lasted: by_code[lasted]}
        (spans,) = by_code.values()
        merged: list[Span] = []
        for first, until, shape in sorted(spans, key=lambda s: (s[0], s[1] or date.max)):
            if merged and merged[-1][1] == first and shapely.equals_exact(merged[-1][2], shape, 0):
                merged[-1] = (merged[-1][0], until, merged[-1][2])
            else:
                merged.append((first, until, shape))
        out[name] = merged
    for name, result in MERGED.items():
        if name in known:
            out[name] = out[result][:1]
    return out, sorted(unmatched)


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher=PUBLISHER,
        title="Kjördæmi and Sveitarfélög tímalína (constituency and municipality boundaries, WFS of gis.lmi.is)",
        url="https://gis.lmi.is/geoserver/web/",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Landmælingar Íslands (National Land Survey of Iceland)",
    )
    for layer in LAYERS:
        p.artifact(raw.cached(SOURCE, url(layer)), SOURCE_ID)


def outline(p: Package, area_id: str, since: date, until: date | None, shape: shapely.Geometry) -> None:
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
        source_id=SOURCE_ID,
    )
