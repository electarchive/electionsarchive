"""The shapes of Panama's provinces and comarcas, districts, and corregimientos, for the areas of `pa.te`.

The shapes come from the boundary layers that the Smithsonian Tropical Research Institute publishes on its GIS data
portal under CC BY-SA 4.0: the corregimientos of the 2010 census, drawn by the Instituto Nacional de Estadística y
Censo, and the corregimientos the Instituto Geográfico Nacional Tommy Guardia vectorized from its 1:25,000 maps, in a
2016 edition (710 shapes) and a 2024 edition (699 corregimientos). Each layer's corregimientos are matched by name,
within their district, to the corregimientos of the Tribunal Electoral's results tables. A district's or province's
shape is the union of its corregimientos in the same layer, so neighbors share their edges.

An election takes a district's corregimientos from a layer only when the layer holds exactly the corregimientos that
the election's tables print in that district, and it takes the nearest such layer in time. It takes a district's
outline the same way, or, in 2014, whose tables give no corregimientos, from the nearest layer whose districts in the
province are the election's. A province takes the nearest layer whose districts in it are the election's. Neither
takes a layer across the day a comarca took land from it. So a shape is never drawn for an election in which the area
held other corregimientos, districts, or land than the layer shows. A corregimiento whose shape spans several elections
is dated from the first of them. The
electoral circuits get no shapes here: the results tables do not say which districts and corregimientos make up each
circuit.
"""

import difflib
import functools
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import numpy
import shapely
from shapely.geometry import shape

from ea import raw
from ea.package import Package
from ea.sources.pa import te

SERVICES = "https://services2.arcgis.com/HRY6x8qt5qjGnAA9/arcgis/rest/services"
PORTAL = "https://www.arcgis.com/home/item.html?id="
TOLERANCE = 0.0001  # degrees, about 11 meters
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


@dataclass(frozen=True)
class Layer:
    vintage: str
    service: str
    order: str  # the field the query orders features by, so a refetch returns them in the same order
    item: str  # the portal item
    title: str
    drawn_by: str
    year: int  # the year the layer shows, for picking the nearest layer to an election
    as_of: date  # the day the layer shows, for the land the comarcas took
    count: int
    notes: str

    @property
    def url(self) -> str:
        return (
            f"{SERVICES}/{self.service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326"
            f"&orderByFields={self.order}&f=geojson"
        )


LAYERS = (
    Layer(
        "2010",
        "Corregimientos_Census_2010",
        "OBJECTID_1",
        "e76657f454754ace8d66036ca5709171",
        "Panama Corregimientos Census - 2010",
        "Instituto Nacional de Estadística y Censo (INEC)",
        2010,
        date(2010, 5, 16),
        631,
        "The 631 corregimientos of the 2010 census, drawn by the census office, with their census codes. The layer "
        "names no district; each corregimiento's district is the one of the results tables whose corregimientos "
        "share the census code's district digits.",
    ),
    Layer(
        "2016",
        "Corregimientos",
        "OBJECTID_1",
        "339ba1515a264217805efc29ec9d144a",
        "Panama Corregimientos Boundaries - 2016",
        "Instituto Geográfico Nacional Tommy Guardia (IGNTG)",
        2016,
        date(2016, 1, 1),
        710,
        "The corregimientos digitized from the institute's 1:25,000 topographic maps and satellite images, credited "
        "to the institute in 2019. Some corregimientos are split into more than one shape.",
    ),
    Layer(
        "2024",
        "Panama_Corregimientos_Boundaries_2024",
        "OBJECTID",
        "33262847f3a442ae90cf303d1d8a08cf",
        "Panama Corregimientos Boundaries - 2024",
        "Instituto Geográfico Nacional Tommy Guardia (IGNTG)",
        2024,
        date(2024, 1, 1),
        699,
        "The 699 corregimientos vectorized from the institute's topographic maps. The layer predates the districts "
        "of the Naso Tjër Di comarca and keeps El Teribe in Changuinola.",
    ),
)

# A district the layers spell otherwise than the results tables, folded as `te.place_key` folds it.
DISTRICTS = {"nurum": "nurun", "distrito de nurun": "nurun", "los santos cabecera": "los santos"}
# Kuna Yala's layer rows name no district, or the comarca; its corregimientos are districts in the results tables.
NO_DISTRICT = {"sin distrito", "no asignado", "kuna yala"}
# A corregimiento a layer names otherwise than the results tables, as (layer, district key, layer spelling): the
# results tables' spelling. None leaves out a layer row that matches no corregimiento of the tables.
CORREGIMIENTOS: dict[tuple[str, str, str], str | None] = {
    ("2024", "changuinola", "el teribe"): None,  # the land of the Naso Tjër Di comarca's districts since 2020
    ("2024", "penonome", "tucue"): "boca de tucue",
}


# The days comarcas took land from the provinces and districts around them, which the tables' lists of districts and
# corregimientos do not always show: Kuna de Madungandí (Ley 24 of 12 January 1996), Ngäbe-Buglé (Ley 10 of 7 March
# 1997), Kuna de Wargandí (Ley 34 of 25 July 2000), and Naso Tjër Di (Ley 188 of 4 December 2020). An area takes no
# shape from a layer across one of these days.
CARVED: dict[tuple[str, str], tuple[date, ...]] = {
    ("pa.provincia", "panama"): (date(1996, 1, 12),),
    ("pa.distrito", "chepo"): (date(1996, 1, 12),),
    ("pa.provincia", "bocas del toro"): (date(1997, 3, 7), date(2020, 12, 4)),
    ("pa.provincia", "chiriqui"): (date(1997, 3, 7),),
    ("pa.provincia", "veraguas"): (date(1997, 3, 7),),
    ("pa.provincia", "darien"): (date(2000, 7, 25),),
    ("pa.distrito", "pinogana"): (date(2000, 7, 25),),
}


def squash(label: str) -> str:
    label = re.sub(r"\((cab|cabecera)\)|\bcabecera\b", "", label)
    return re.sub(r"[^a-z0-9]", "", label)


def forms(label: str) -> set[str]:
    """The ways a label may be written: whole, with or without a parenthesized name, and each name joined by 'o'."""
    label = re.sub(r"\((cab|cabecera)\)|\bcabecera\b", "", label).strip()
    out = {label}
    found = re.match(r"(.*?)\s*\((.*)\)$", label)
    if found:
        out |= {found[1], found[2]}
    for each in list(out):
        out |= set(each.split(" o "))
    return {squash(each) for each in out if each.strip()}


@dataclass
class Piece:
    layer: str
    province: str  # the province key of the results tables
    district: str  # the district key of the results tables
    label: str  # the corregimiento as the layer folds it, after the curated spellings
    geometry: shapely.Geometry


def _features(layer: Layer) -> list[tuple[dict[str, object], shapely.Geometry]]:
    data = json.loads(raw.cached(te.SOURCE, layer.url).path.read_bytes())
    features = data["features"]
    if len(features) != layer.count:
        raise ValueError(f"the {layer.vintage} layer holds {len(features)} features, not {layer.count}")
    shapes = [shapely.make_valid(shape(feature["geometry"])) for feature in features]
    simple = shapely.coverage_simplify(numpy.array(shapes), TOLERANCE)
    return [(feature["properties"], outline) for feature, outline in zip(features, simple, strict=True)]


@functools.cache
def held() -> dict[int, dict[str, set[str]]]:
    """The corregimientos each election's tables print, by year and district key."""
    out: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for (district, corregimiento), labels in te.places().corregimientos.items():
        for year, _ in labels:
            out[year][district].add(corregimiento)
    return out


@functools.cache
def district_sets() -> dict[int, dict[str, set[str]]]:
    """The districts each election's tables print, by year and province key."""
    out: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for year, e in te.elections().items():
        for office in ("mayor", "rep", "council"):
            for unit in getattr(e, office):
                if unit.scheme in ("distrito", "corregimiento"):
                    out[year][te.place_key(unit.path[0])].add(te.district_key(unit))
    return out


def _similar(a: str, b: str) -> float:
    """How alike two labels are: the best ratio between any of their forms."""
    return max(difflib.SequenceMatcher(None, x, y).ratio() for x in forms(a) for y in forms(b))


def _district(province: str, district: str, corregimiento: str) -> str:
    """The district key of the results tables for a layer row."""
    district = DISTRICTS.get(district, district)
    plain = squash(corregimiento)
    if district in NO_DISTRICT or plain.startswith("comarca"):
        for key in te.places().districts:
            if squash(key) == plain or (plain.startswith("comarca") and _similar(key, plain) > 0.9):
                return key
    return f"{district}@{province}" if district in te.colliding() else district


def _province(label: str) -> str:
    key = te.place_key(label)
    return key if key in te.places().provinces else te.place_key(re.sub(r"^comarca ", "", key))


def _census_districts(rows: list[tuple[dict[str, object], shapely.Geometry]]) -> dict[str, str]:
    """The district key of each census district code: the 2009 district whose corregimientos share most names."""
    names: dict[str, set[str]] = defaultdict(set)
    for properties, _ in rows:
        names[str(properties["CODIGO"])[:4]] |= forms(te.place_key(str(properties["CORR_NOM"])))
    out: dict[str, str] = {}
    for code, found in names.items():
        score, best = max(
            (len(found & set().union(*(forms(c) for c in members))), district)
            for district, members in held()[2009].items()
        )
        if score == 0:
            raise ValueError(f"census district {code} matches no district of 2009")
        out[code] = best
    return out


@functools.cache
def pieces() -> dict[str, list[Piece]]:
    """Each layer's corregimientos, with the province and district keys of the results tables."""
    out: dict[str, list[Piece]] = {}
    census = te.elections()[2009].day
    parents = {  # the province of each district at the 2009 election, for the census layer
        district: max(((day, province) for day, province in history if day <= census), default=history[0])[1]
        for district, history in te.places().parents.items()
    }
    for layer in LAYERS:
        found: list[Piece] = []
        rows = _features(layer)
        codes = _census_districts(rows) if layer.vintage == "2010" else {}
        for properties, outline in rows:
            if layer.vintage == "2010":
                label = te.place_key(str(properties["CORR_NOM"]))
                district = codes[str(properties["CODIGO"])[:4]]
                # Kuna Yala's corregimientos and the Kuna comarcas are districts in the tables, each holding a
                # corregimiento of its name
                own = sorted(
                    (_similar(key, label), key)
                    for key, members in held()[2009].items()
                    if members == {key} and (parents[key] == parents[district] or key in te.places().provinces)
                )
                if own and own[-1][0] > 0.85 and (len(own) == 1 or own[-2][0] < own[-1][0]):
                    district = own[-1][1]
                province = parents[district]
            else:
                fields = {key.lower(): value for key, value in properties.items()}
                province = _province(str(fields["provincia"]))
                label = te.place_key(str(fields["corregimiento"]))
                district_label = te.place_key(re.sub(r"\s*\(.*\)$", "", str(fields["distrito"])))
                district = _district(province, district_label, label)
                if district in te.places().provinces:  # a comarca the layer files under the province around it
                    province = district
            curated = CORREGIMIENTOS.get((layer.vintage, district, label), label)
            if curated is None:
                continue
            found.append(Piece(layer.vintage, province, district, curated, outline))
        out[layer.vintage] = found
    return out


@functools.cache
def matches(vintage: str, year: int) -> dict[str, dict[str, str]]:
    """The corregimiento key of an election's tables that each layer label names, by district: exact first, then by
    any of the forms a name takes, then by the closest spelling, one label to one corregimiento. A district is left
    out when a label or a corregimiento stays unmatched."""
    by_district: dict[str, set[str]] = defaultdict(set)
    for piece in pieces()[vintage]:
        by_district[piece.district].add(piece.label)
    out: dict[str, dict[str, str]] = {}
    for district, labels in by_district.items():
        pool = held()[year].get(district, set())
        if len(labels) != len(pool):
            continue
        taken: dict[str, str] = {}
        for label in sorted(labels):
            hits = [k for k in pool if squash(k) == squash(label)]
            if len(hits) == 1:
                taken[label] = hits[0]
        free = {k for k in pool if k not in taken.values()}
        open_labels = {label for label in labels if label not in taken}
        for label in sorted(open_labels):
            hits = [k for k in free if forms(k) & forms(label)]
            if len(hits) == 1 and [x for x in open_labels if forms(x) & forms(hits[0])] == [label]:
                taken[label] = hits[0]
        pairs = sorted(
            (-_similar(label, k), -difflib.SequenceMatcher(None, squash(label), squash(k)).ratio(), label, k)
            for label in labels
            if label not in taken
            for k in pool
            if k not in taken.values()
        )
        for score, _, label, k in pairs:
            if -score >= 0.75 and label not in taken and k not in taken.values():
                taken[label] = k
        if len(taken) == len(labels):
            out[district] = taken
    return out


def nearest(year: int, layers: Iterable[str], area: tuple[str, str] | None = None) -> str | None:
    """The layer nearest an election in time, among those no comarca's creation separates from it."""
    day = te.elections()[year].day
    by = {layer.vintage: layer for layer in LAYERS}
    carved = CARVED.get(area, ()) if area else ()
    options = sorted(
        (v for v in layers if not any(min(day, by[v].as_of) < c <= max(day, by[v].as_of) for c in carved)),
        key=lambda v: (abs(by[v].year - year), -by[v].year),
    )
    return options[0] if options else None


@functools.cache
def plans() -> dict[tuple[str, str], dict[int, tuple[str, str]]]:
    """The shape each area takes in each election, by (scheme, area code) and year, as (layer, the layer's key)."""
    out: dict[tuple[str, str], dict[int, tuple[str, str]]] = defaultdict(dict)
    provinces: dict[str, dict[str, set[str]]] = {}
    for vintage, found in pieces().items():
        provinces[vintage] = defaultdict(set)
        for piece in found:
            provinces[vintage][piece.province].add(piece.district)
    for year in te.elections():
        for province, districts in district_sets()[year].items():
            same = [v for v in provinces if provinces[v].get(province) == districts]
            for district in districts:
                if year in held():
                    fits = [v for v in provinces if district in matches(v, year)]
                    if (v := nearest(year, fits, ("pa.distrito", district))) is not None:
                        out[("pa.distrito", district)][year] = (v, district)
                        for label, key in matches(v, year)[district].items():
                            out[("pa.corregimiento", f"{district}:{key}")][year] = (v, f"{district}:{label}")
                elif (v := nearest(year, same, ("pa.distrito", district))) is not None:
                    out[("pa.distrito", district)][year] = (v, district)
            if (v := nearest(year, same, ("pa.provincia", province))) is not None:
                out[("pa.provincia", province)][year] = (v, province)
    return out


@functools.cache
def outlines() -> dict[tuple[str, str, str], shapely.Geometry]:
    """Each shape of each layer, by (scheme, the layer's key, layer)."""
    out: dict[tuple[str, str, str], shapely.Geometry] = {}
    for vintage, found in pieces().items():
        groups: dict[tuple[str, str], list[shapely.Geometry]] = defaultdict(list)
        for piece in found:
            groups[("pa.provincia", piece.province)].append(piece.geometry)
            groups[("pa.distrito", piece.district)].append(piece.geometry)
            groups[("pa.corregimiento", f"{piece.district}:{piece.label}")].append(piece.geometry)
        for (scheme, code), parts in groups.items():
            out[(scheme, code, vintage)] = _multi(shapely.union_all(parts))
    return out


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = [p for p in shapely.get_parts(shapely.make_valid(geometry)) if shapely.get_type_id(p) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def spans(
    years: dict[int, tuple[str, str]], days: list[tuple[int, date]]
) -> list[tuple[tuple[str, str], date, date | None]]:
    """The layer an area takes from each election on, merged over consecutive elections that take the same one, until
    the next election that takes another layer or none."""
    out: list[tuple[tuple[str, str], date, date | None]] = []
    current: tuple[tuple[str, str], date] | None = None
    for year, day in days:
        vintage = years.get(year)
        if current is not None and vintage != current[0]:
            out.append((current[0], current[1], day))
            current = None
        if vintage is not None and current is None:
            current = (vintage, day)
    if current is not None:
        out.append((current[0], current[1], None))
    return out


def source_id(layer: Layer) -> str:
    return te.key("source", "stri", layer.vintage)


def add(p: Package) -> None:
    """Add the layers as sources and each area's shapes to the geo package."""
    for layer in LAYERS:
        sid = source_id(layer)
        p.add(
            "source",
            id=sid,
            kind="official_body",
            publisher=f"{layer.drawn_by}, through the Smithsonian Tropical Research Institute GIS data portal",
            title=layer.title,
            url=PORTAL + layer.item,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution=f"{layer.drawn_by}; Smithsonian Tropical Research Institute (STRI) GIS data portal",
            notes=(
                f"{layer.notes} The shapes here are simplified to about 11 meters as one coverage, and each district "
                "and province is the union of its corregimientos."
            ),
        )
        p.artifact(raw.cached(te.SOURCE, layer.url), sid)
    by_layer = {layer.vintage: layer for layer in LAYERS}
    every = sorted((year, e.day) for year, e in te.elections().items())
    with_corregimientos = [(year, day) for year, day in every if year in held()]
    shapes = outlines()
    for (scheme, code), years in sorted(plans().items()):
        days = with_corregimientos if scheme == "pa.corregimiento" else every
        for (vintage, own), start, end in spans(years, days):
            geometry = shapes[(scheme, own, vintage)]
            p.add(
                "area_geometry",
                area_id=te.area(scheme, code),
                valid_from=start,
                valid_to=end,
                geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=source_id(by_layer[vintage]),
            )


def fetch() -> None:
    for layer in LAYERS:
        raw.fetch(te.SOURCE, layer.url, suffix=".geojson")
