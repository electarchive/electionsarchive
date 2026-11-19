"""Shapes for Latvia's councils of 1990 to 2009 (cities, parishes, and counties) and its 26 districts.

The Central Statistical Bureau publishes the State Land Service's generalized boundaries of Latvia's territorial
units as they stood on 1 January of each year from 2010. The 2010 file is the earliest and the nearest to the
division that ended on 1 July 2009: it holds the 500 parishes (pagasti), the towns, and the municipalities (novadi)
formed in 2009, each with the municipality it lies in. A council elected from 1997 to 2005 maps to one or more of
those units:

- a parish or town that kept its name is the unit of that name;
- a parish that the 2009 reform made a municipality of its own is the unit of that municipality;
- a "town with its rural territory" or an old county (novads) is the town, or the seat of the county, and the
  parishes of the 2010 file that no other council of the municipality claims, which are the rural territory that
  the reform cut into parishes of their own.

A district (rajons) is the union of the councils the commission lists under it in 2001.
"""

import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
from shapely.geometry import shape as geometry

from ea import package, raw
from ea.package import Package
from ea.sources.lu.model import slug

SOURCE = "lv.cvk"
DATASET = "https://data.gov.lv/dati/lv/dataset/robezas"
ZIP = (
    "https://data.gov.lv/dati/dataset/e3d606f2-6d38-444d-b767-119fdcc435fe/resource/"
    "783e1c19-1ad1-40ee-809b-f2ad3495c5d1/download/territorial_units_lv_1.2m_2010.01.01..zip"
)
START = date(1990, 1, 1)
END = date(2009, 7, 1)
VINTAGE = "1990-2009"
LATVIA = str(uuid5(package.NAMESPACE, "polity\x1fLV"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fLVA"))
LOCAL = "ar lauku teritoriju"

# Councils the commission spells differently from the unit's name in the 2010 file.
RENAMED = {
    "Rušonu pagasts": "Rušonas pagasts",
    "Sokolu pagasts": "Sokolku pagasts",
    "Salacas pagasts": "Salacgrīvas pagasts",
    "Rēzeknes pilsēta": "Rēzekne",
}
# Parishes of one name in two districts: the municipality of 2010 that holds each.
TWINS = {
    ("Pilskalnes pagasts", "Aizkraukles rajons"): "Neretas novads",
    ("Pilskalnes pagasts", "Daugavpils rajons"): "Ilūkstes novads",
    ("Salas pagasts", "Jēkabpils rajons"): "Salas novads",
    ("Salas pagasts", "Rīgas rajons"): "Babītes novads",
}
# Counties of 2001 whose seat is a town of another name than the county's.
SEATS = {"Sabiles novads": "Sabile"}
# Rural parishes of 2010 whose name does not start like their town's, with the 2001 council that holds them.
RURAL = {"Abavas pagasts": "Sabiles novads", "Ārlavas pagasts": "Valdemārpils ar lauku teritoriju"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


SOURCE_ID = key("source", "territorial-units")


class Council(NamedTuple):
    day: str
    code: str
    name: str
    group: str


class Unit(NamedTuple):
    code: str
    name: str
    kind: int  # 1 republic city, 5 municipality, 6 municipality town, 7 parish
    parent: str
    parent_name: str
    shape: shapely.Geometry


@dataclass
class Place:
    scheme: str
    code: str
    name: str
    units: tuple[str, ...]
    parent: str | None = None


@dataclass
class Plan:
    """The places the councils map to: each council's place, and each district's."""

    places: dict[str, Place] = field(default_factory=dict)
    councils: dict[tuple[str, str], str] = field(default_factory=dict)
    districts: dict[str, str] = field(default_factory=dict)
    cities: dict[str, str] = field(default_factory=dict)
    unplaced: list[Council] = field(default_factory=list)

    def district(self, name: str) -> str | None:
        """The area of a district or a republic city as the commission prints it, in any case."""
        found = plain(name)
        return self.districts.get(found) or self.cities.get(found)


def plain(text: str) -> str:
    return " ".join(text.casefold().split())


def fetch() -> None:
    raw.fetch(SOURCE, ZIP, suffix=".zip")


def artifact() -> raw.Artifact:
    return raw.cached(SOURCE, ZIP)


class Atlas:
    def __init__(self) -> None:
        self.units: dict[str, Unit] = {}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.by_parent: dict[str, list[str]] = defaultdict(list)
        with tempfile.TemporaryDirectory() as folder, zipfile.ZipFile(artifact().path) as archive:
            archive.extractall(folder)
            reader = shapefile.Reader(str(next(Path(folder).rglob("*.shp"))), encoding="utf-8")
            for item in reader.shapeRecords():
                if item.record is None or item.shape is None:
                    raise ValueError("the boundary file holds a unit without a record or a shape")
                row = {name: str(value) for name, value in item.record.as_dict().items()}
                unit = Unit(
                    row["L1_code"],
                    row["L1_name"],
                    int(row["L1_type"]),
                    row["L0_code"],
                    row["L0_name"],
                    shapely.make_valid(geometry(item.shape.__geo_interface__)),
                )
                self.units[unit.code] = unit
                self.by_name[unit.name].append(unit.code)
                self.by_parent[unit.parent].append(unit.code)
            reader.close()


def _upper(name: str) -> str:
    name = " ".join(name.split())
    return name[:1].upper() + name[1:]


def _place(atlas: Atlas, councils: list[Council]) -> tuple[dict[str, list[str]], list[Council]]:
    """Each council's 2010 units, and the councils that no unit of the file holds."""
    claimed: dict[str, Council] = {}
    found: dict[str, list[str]] = {}
    seats: dict[str, str] = {}  # council code -> the unit that anchors a town with its rural territory or a county
    twice = {
        (_upper(c.name), c.group)
        for c in councils
        if sum(_upper(o.name) == _upper(c.name) and o.group == c.group for o in councils) > 1
    }

    def take(council: Council, code: str) -> None:
        claimed[code] = council
        found.setdefault(council.code, []).append(code)

    def spelled(council: Council) -> str:
        return RENAMED.get(_upper(council.name), _upper(council.name))

    rest: list[Council] = []
    for council in councils:
        name = spelled(council)
        codes = atlas.by_name.get(name, [])
        if (name, council.group) in TWINS:
            codes = [c for c in codes if atlas.units[c].parent_name == TWINS[(name, council.group)]]
        elif len(codes) > 1 and council.group == "":
            codes = []
        if (_upper(council.name), council.group) in twice and (name, council.group) not in TWINS:
            codes = []
        solo = atlas.by_name.get(name.removesuffix("pagasts") + "novads", []) if name.endswith(" pagasts") else []
        if len(codes) == 1 and codes[0] not in claimed:
            take(council, codes[0])
        elif not codes and len(solo) == 1 and atlas.units[solo[0]].kind == 5 and solo[0] not in claimed:
            take(council, solo[0])
        else:
            rest.append(council)
    left: list[Council] = []
    for council in rest:
        name = spelled(council)
        near: list[str] = []
        if name.endswith(" " + LOCAL):
            near = [c for c in atlas.by_name.get(name.removesuffix(" " + LOCAL), []) if c not in claimed]
        elif name.endswith(" novads"):
            if name in SEATS:
                near = [c for c in atlas.by_name.get(SEATS[name], []) if c not in claimed]
            else:
                parents = {u.parent for u in atlas.units.values() if u.parent_name == name}
                inside = [c for p in sorted(parents) for c in atlas.by_parent[p] if c not in claimed]
                near = sorted(inside, key=lambda c: (atlas.units[c].kind not in (1, 6), c))[:1]
        if len(near) == 1:
            take(council, near[0])
            seats[council.code] = near[0]
        else:
            left.append(council)
    by_code = {c.code: c for c in councils}
    owners: dict[str, list[str]] = defaultdict(list)
    for code, seat in seats.items():
        owners[atlas.units[seat].parent].append(code)
    for parent, holders in owners.items():
        for unit in atlas.by_parent[parent]:
            if unit in claimed:
                continue
            name = atlas.units[unit].name
            match = [
                h
                for h in holders
                if (spelled(by_code[h]) == RURAL[name] if name in RURAL else len(holders) == 1)
                or plain(by_code[h].name)[:4] == plain(name)[:4]
            ]
            if len(match) == 1:
                take(by_code[match[0]], unit)
    return found, left


def build(atlas: Atlas, days: dict[str, list[Council]]) -> Plan:
    """Place the councils of each day. The 2001 list comes first: it names every town with its rural territory, and
    a council of another day takes the rural territory of the town of its name."""
    plan = Plan()
    rural: dict[str, tuple[str, ...]] = {}  # a town's unit -> the units of the town with its rural territory
    names: dict[str, str] = {}
    for day in sorted(days, key=lambda d: (d != "2001-03-11", d)):
        found, left = _place(atlas, days[day])
        for council in days[day]:
            codes = found.get(council.code)
            if codes is None:
                continue
            if day == "2001-03-11" and len(codes) > 1:
                rural[codes[0]] = tuple(codes)
            elif len(codes) == 1 and codes[0] in rural and day != "2001-03-11":
                # A town that is listed with its rural territory in 2001 holds the same rural territory earlier.
                taken = {c for other in found.values() for c in other if c != codes[0]}
                codes = [c for c in rural[codes[0]] if c == codes[0] or c not in taken]
            units = tuple(sorted(codes))
            code = "+".join(units)
            place = plan.places.setdefault(code, Place("lv.pasvaldiba", code, "", units))
            plan.councils[(day, council.code)] = key("area", "lv.pasvaldiba", code)
            names.setdefault(code, _upper(council.name))
            place.name = place.name or names[code]
        plan.unplaced += left
    return finish(plan, days)


def finish(plan: Plan, days: dict[str, list[Council]]) -> Plan:
    by_id = {key("area", "lv.pasvaldiba", p.code): p for p in plan.places.values()}
    groups: dict[str, set[str]] = defaultdict(set)
    for council in days["2001-03-11"]:
        area_id = plan.councils.get(("2001-03-11", council.code))
        if area_id is None:
            continue
        if council.group:
            groups[council.group].add(area_id)
        else:
            plan.cities[plain(council.name)] = area_id
    for city in ("2005-03-12",):
        for council in days.get(city, []):
            area_id = plan.councils.get((city, council.code))
            if area_id:
                plan.cities.setdefault(plain(council.name), area_id)
    for group, members in groups.items():
        units = tuple(sorted(u for m in members for u in by_id[m].units))
        id = key("area", "lv.rajons", slug(group))
        plan.districts[plain(group)] = id
        plan.places[f"rajons:{slug(group)}"] = Place("lv.rajons", slug(group), group, units)
        for member in members:
            by_id[member].parent = id
    return plan


def wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(3059, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def geo(atlas: Atlas, plan: Plan) -> Package:
    p = Package(
        "lv.cvk/geo-pv",
        SOURCE,
        "Shapes of Latvia's cities, parishes, counties, and districts of the 1990-2009 division, from the State "
        "Land Service's boundaries of 2010",
        depends=["lv.cvk/geo"],
    )
    p.external |= {LATVIA, COUNTRY}
    p.add("scheme", code="lv.rajons", polity_id=LATVIA, kind="administrative", rank=1, noun="district")
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Centrālā statistikas pārvalde",
        title="Ģeneralizētas teritoriālo vienību robežas (generalized boundaries of territorial units), 1 January 2010, "
        "1:1 200 000, from the State Land Service's data",
        url=DATASET,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Centrālā statistikas pārvalde, Valsts zemes dienests",
    )
    p.artifact(artifact(), SOURCE_ID)
    note = (
        "The shape is the unit's boundary on 1 January 2010, the earliest the State Land Service's generalized "
        "boundaries cover, drawn at 1:1 200 000. The division of 1990 to 2009 ended on 1 July 2009, so the shape "
        "stands for the unit as it was whenever its council was elected, and a parish's or a town's border may "
        "differ from it by the changes made before 2010."
    )
    p.add("note", id=key("note", SOURCE_ID), entity_id=SOURCE_ID, kind="methodology", body_md=note, lang="en")
    for scheme in ("lv.pasvaldiba", "lv.rajons"):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme, VINTAGE),
            scheme_code=scheme,
            vintage=VINTAGE,
            valid_from=START,
            valid_to=END,
            as_of=START,
        )
    for place in sorted(plan.places.values(), key=lambda item: (item.scheme, item.code)):
        id = key("area", place.scheme, place.code)
        p.add("area", id=id, scheme_code=place.scheme)
        p.add("area_code", area_id=id, code=place.code)
        p.add("area_parent", area_id=id, parent_id=place.parent or COUNTRY, valid_from=START, valid_to=END)
        p.name(id, place.name, "lv", "official")
        shape = wgs(shapely.union_all([atlas.units[u].shape for u in place.units]))
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=START,
            valid_to=END,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=SOURCE_ID,
        )
    return p
