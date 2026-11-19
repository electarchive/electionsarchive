"""Estonian municipal outlines over time, from the Land and Spatial Development Board's dated files.

The board publishes the municipalities (omavalitsus) and the settlement units (asustusüksus, among them the city
districts) as they stood on a series of dates: ten files for 1994-2004, one for most years since, and the current
plan. Each file is read in the national grid; an area keeps one shape for as long as its outline does not change from
file to file, so a municipality that never moved has one dated row, not one per file. The board re-fits its outlines
to the cadastre from year to year, so two outlines count as one when they differ by less than half a percent of the
area, which is finer than the 25 meters the shapes are simplified to.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import cache

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import raw

ARCHIVE = "https://geoportaal.maaruum.ee/docs/haldus_asustus/ajaloolised"
CURRENT = "https://s3.pilw.io/rp-kemit-kataster/EHAK"
REFORM = date(2017, 10, 21)
TOLERANCE = 25.0
SAME = 1000.0  # square meters, and RELATIVE of the area, of difference below which two outlines are the same
RELATIVE = 0.005


@dataclass(frozen=True)
class Plan:
    day: date
    url: str


YEARS = (1994, 1997, 1999, *range(2000, 2018))
SETTLEMENT_YEARS = (1999, *range(2003, 2018))
OLD_PLAN_2017 = "https://geoportaal.maaruum.ee/docs/haldus_asustus/omavalitsus_20171015_shp.zip"


def dated(source: str, url: str) -> date:
    """The date a board file is for: the one its shapefile carries in its name, such as omavalitsus_2009-01-01."""
    names = zipfile.ZipFile(raw.cached(source, url).path).namelist()
    return date.fromisoformat(max(n for n in names if n.endswith(".shp"))[-14:-4])


def municipal_plans(source: str) -> list[Plan]:
    out = [Plan(dated(source, u), u) for u in (f"{ARCHIVE}/omavalitsus_{year}.zip" for year in YEARS)]
    out.append(Plan(date(2017, 10, 15), OLD_PLAN_2017))
    out.append(Plan(REFORM, f"{CURRENT}/omavalitsus_shp.zip"))
    return sorted(out, key=lambda plan: plan.day)


def settlement_plans(source: str) -> list[Plan]:
    out = [Plan(dated(source, u), u) for u in (f"{ARCHIVE}/asustusyksus_{year}.zip" for year in SETTLEMENT_YEARS)]
    out.append(Plan(REFORM, f"{CURRENT}/asustusyksus_shp.zip"))
    return out


def urls() -> list[str]:
    out = [f"{ARCHIVE}/omavalitsus_{year}.zip" for year in YEARS] + [OLD_PLAN_2017, f"{CURRENT}/omavalitsus_shp.zip"]
    out += [f"{ARCHIVE}/asustusyksus_{year}.zip" for year in SETTLEMENT_YEARS] + [f"{CURRENT}/asustusyksus_shp.zip"]
    return out


def fold(name: str) -> str:
    """A name reduced to its first word, with '_' read as '-'."""
    return name.casefold().replace("_", "-").split()[0] if name.split() else ""


def same_word(a: str, b: str) -> bool:
    """Two first words are one when equal, or when one is the other's genitive: Põhja-Tallinn and Põhja-Tallinna."""
    return a == b or a == b + "a" or b == a + "a"


def same_place(plan_name: str, names: set[str]) -> bool:
    return any(same_word(fold(plan_name), fold(name)) for name in names)


def full_name(name: str) -> str:
    return " ".join(name.casefold().replace("_", "-").split())


def decode(raw_bytes: bytes) -> str:
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("latin-1")


@dataclass(frozen=True)
class Feature:
    name: str
    shape: shapely.Geometry  # EPSG:3301


def _read(
    source: str, url: str, keep: Callable[[Mapping[str, object]], bool], code_field: str, name_field: str
) -> list[tuple[str, str, shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached(source, url).path)
    base = max({n.rsplit(".", 1)[0] for n in archive.namelist() if n.endswith(".shp")})
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{base}.shp")),
        dbf=io.BytesIO(archive.read(f"{base}.dbf")),
        shx=io.BytesIO(archive.read(f"{base}.shx")),
        encoding="latin-1",
    )
    rows: list[tuple[str, str, shapely.Geometry]] = []
    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record is None or shape is None:
            continue
        fields = record.as_dict()
        if not keep(fields):
            continue
        name = decode(str(fields[name_field]).encode("latin-1")).strip()
        outline = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
        rows.append((str(fields[code_field]).strip(), name, outline))
    return rows


def _features(rows: list[tuple[str, str, shapely.Geometry]]) -> dict[str, Feature]:
    out: dict[str, list[tuple[str, shapely.Geometry]]] = {}
    for code, name, shape in rows:
        out.setdefault(code, []).append((name, shape))
    return {
        code: Feature(found[0][0], shapely.union_all([shape for _, shape in found]) if len(found) > 1 else found[0][1])
        for code, found in out.items()
    }


@cache
def municipalities(source: str, url: str) -> dict[str, Feature]:
    return _features(_read(source, url, lambda _: True, "OKOOD", "ONIMI"))


@cache
def city_districts(source: str, url: str) -> dict[str, Feature]:
    """The city districts of one settlement file, by their code; a code is never reused within a city."""
    return _features(
        _read(source, url, lambda fields: str(fields["TYYP"]).strip() in ("6", "linnaosa"), "AKOOD", "ANIMI")
    )


def simplified(shapes: list[shapely.Geometry]) -> list[shapely.Geometry]:
    """Outlines simplified together, so neighbors keep sharing their edges, in WGS 84."""
    simple = shapely.coverage_simplify(numpy.array(shapes), TOLERANCE)
    project = pyproj.Transformer.from_crs(3301, 4326, always_xy=True)
    out: list[shapely.Geometry] = []
    for shape in simple:
        moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
        parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
        out.append(shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326))
    return out


def unchanged(old: shapely.Geometry, new: shapely.Geometry) -> bool:
    return old.equals(new) or shapely.area(shapely.symmetric_difference(old, new)) <= max(
        SAME, RELATIVE * shapely.area(old)
    )


@dataclass
class Run:
    start: date | None
    end: date | None
    plan: int  # the plan whose outline the run keeps
    shape: shapely.Geometry  # EPSG:3301
    code: str  # the code the plan gives the outline


def runs(plans: list[Plan], found: list[dict[str, Feature]], code: str, names: set[str], known: set[str]) -> list[Run]:
    """The dated outlines of one area across the plans, merging neighbors whose outline did not change.

    A plan counts for the area when it holds its code under the same name; the codes were reissued in the 1990s, so
    a code alone does not. When a plan does not hold the code at all, it counts when exactly one of its units has the
    area's full name and a code that belongs to no area of its own: the board recoded some municipalities when it
    merged their neighbors in 2005. A run starts on the date of the first plan that holds a new outline, or is open at
    its start when that plan is the first, and ends on the date of the first later plan that holds another outline or
    none."""
    wanted = {full_name(name) for name in names}
    out: list[Run] = []
    for i, plan in enumerate(plans):
        at = code
        feature = found[i].get(code)
        if feature is not None and not same_place(feature.name, names):
            feature = None
        if feature is None and code not in found[i]:
            same = [(c, f) for c, f in found[i].items() if c not in known and full_name(f.name) in wanted]
            if len(same) == 1:
                at, feature = same[0]
        last = out[-1] if out and out[-1].end is None else None
        if feature is None:
            if last is not None:
                last.end = plan.day
            continue
        if last is not None and unchanged(last.shape, feature.shape):
            continue
        if last is not None:
            last.end = plan.day
        out.append(Run(None if i == 0 else plan.day, None, i, feature.shape, at))
    return out


@cache
def outlines(source: str, url: str, districts: bool) -> dict[str, shapely.Geometry]:
    """The simplified outlines of one file by code, in WGS 84."""
    every = city_districts(source, url) if districts else municipalities(source, url)
    codes = sorted(every)
    return dict(zip(codes, simplified([every[code].shape for code in codes]), strict=True))
