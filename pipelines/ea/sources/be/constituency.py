"""Outlines for the electoral districts of the Belgian federal, regional, and Community parliaments.

Each district is the union of the municipalities of Statbel's 2018 plan that make up the arrondissements, or the
province, that its name lists (see `electoral`). Districts that are one province or region of the Statbel plans link to
those areas instead. A district that changed its municipalities (Mouscron, Comines-Warneton, and Fourons moved in
1963, and the Walloon constituencies follow Statbel's arrondissements from 2019) is one area with one outline for each
span. Pre-1977 municipalities are not reconstructed, so a municipal merger of 1977 can move an edge by part of a
municipality.
"""

import functools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid5

import numpy
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.be import electoral, nis
from ea.sources.be.vlaanderen import PLANS, SECTORS, TOLERANCE

SOURCE = "be.ibz"
KEY = f"{SOURCE}/constituencies"
STATBEL = "be.statbel"
PLAN = "2018"
SCHEME = "be.ibz_district"
BELGIUM = nis.BELGIUM
TERRITORY = nis.TERRITORY
# the 1977 municipal plan is the earliest whose provinces and regions Statbel's outlines stand for
PLANNED = date(1977, 1, 1)
# the dates the districts' plans changed: the law of 1899, the language-border law, the Chamber's constituencies of
# 1995, the provincial constituencies of 2003, those of 2007, the split of Brabant in 2014, and Statbel's arrondissements
BREAKS = (
    date(1894, 1, 1),
    date(1900, 1, 1),
    electoral.BORDER,
    date(1995, 1, 1),
    date(2003, 1, 1),
    date(2007, 1, 1),
    date(2014, 1, 1),
    electoral.LOUVIERE,
)
BODIES = {
    "Chamber": "chamber",
    "Senate": "senate",
    "ParlementFlanders": "flemish",
    "ParlementWallonia": "walloon",
}
GERMAN = re.compile(r"german|duitstalig|germanophone|deutschsprach", re.IGNORECASE)
DG = "dg"


@dataclass
class Use:
    day: date
    munis: frozenset[str]
    labels: dict[str, str]


@dataclass
class Area:
    key: str
    uses: list[Use] = field(default_factory=list)


# every district area the elections use, by its composition key
AREAS: dict[str, Area] = {}
USED: set[str] = set()


def area_id(key: str) -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1farea\x1f{SCHEME}\x1f{key}"))


def _clean(text: str) -> str:
    return re.sub(r"\s*-\s*", "-", text).strip()


def _region_or_province(munis: frozenset[str]) -> str | None:
    towns = nis.towns(PLAN)
    for scheme, attribute in ((nis.PROVINCE, "province"), (nis.REGION, "region")):
        groups = {getattr(towns[code], attribute) for code in munis}
        if len(groups) == 1:
            group = next(iter(groups))
            if group and munis == {code for code, town in towns.items() if getattr(town, attribute) == group}:
                return nis.area(scheme, group)
    return None


def district(day: date, kind: str, labels: dict[str, str], fallback: str) -> str | None:
    """The area of a seat-allocation district of a national or regional body, built from the arrondissements its name
    lists, or None when the name lists none (such as a language college). The use is recorded for `geo`."""
    if kind == "ParlementEurope":
        return community(day, labels, fallback)
    body = BODIES.get(kind)
    if body is None:
        return None
    codes = None
    for text in (*(labels.get(lang) or "" for lang in ("nl", "en", "fr", "de")), fallback):
        if text and (codes := electoral.codes(f"x – {text}", day, body)):
            break
    if not codes:
        return None
    munis = frozenset(code for code, atom in electoral.atoms(day, body).items() if atom in codes)
    if not munis:
        return None
    return linked(day, munis) or record("+".join(sorted(codes)), Use(day, munis, labels or {"nl": fallback}))


def linked(day: date, munis: frozenset[str]) -> str | None:
    return _region_or_province(munis) if day >= PLANNED else None


def record(key: str, use: Use) -> str:
    AREAS.setdefault(key, Area(key)).uses.append(use)
    USED.add(key)
    return area_id(key)


def community(day: date, labels: dict[str, str], fallback: str) -> str | None:
    """The German-speaking Community's area when the district's name says it is that college or parliament."""
    if not any(GERMAN.search(text) for text in (*labels.values(), fallback) if text):
        return None
    return record(DG, Use(day, frozenset(electoral.GERMAN_SPEAKING), labels or {"de": fallback}))


def single(day: date, kind: str) -> str | None:
    """The area of a body elected in one district: the Brussels-Capital Region for its Parliament, and the German-speaking
    Community for its own."""
    if kind == "ParlementBrussels" and day >= PLANNED:
        return nis.area(nis.REGION, nis.BRUSSELS)
    if kind == "ParlementGermanRegion":
        return record(DG, Use(day, frozenset(electoral.GERMAN_SPEAKING), {"de": "Deutschsprachige Gemeinschaft"}))
    return None


def is_own(id: str) -> bool:
    return id in {area_id(key) for key in USED}


# ---- the outlines -----------------------------------------------------------------------------------------------


@functools.cache
def municipalities() -> dict[str, shapely.Geometry]:
    """Each municipality of the 2018 plan, simplified together so that neighbors keep one shared edge."""
    reader, _ = nis._reader(PLAN, True)
    pieces: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for record_, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record_ is not None and shape is not None:
            pieces[nis._column(dict(record_.as_dict()), "CNIS5_")].append(
                shapely.geometry.shape(shape.__geo_interface__)
            )
    codes = sorted(pieces)
    merged = [shapely.make_valid(shapely.coverage_union_all(pieces[code])) for code in codes]
    simple = list(shapely.make_valid(shapely.coverage_simplify(numpy.array(merged), TOLERANCE)))
    return dict(zip(codes, simple, strict=True))


def spans(area: Area) -> list[tuple[date, date | None, frozenset[str], list[Use]]]:
    """The spans of one area's outline: each run of uses with the same municipalities, from the plan date at or before
    its first use to the start of the next run, and for the last run to the plan date after its last use, or open when
    the area is still in use."""
    uses = sorted(area.uses, key=lambda use: use.day)
    runs: list[list[Use]] = []
    for use in uses:
        if runs and runs[-1][0].munis == use.munis:
            runs[-1].append(use)
        else:
            runs.append([use])
    marks = sorted({*BREAKS})

    def start(day: date) -> date:
        return max(mark for mark in marks if mark <= day)

    out = []
    for i, run in enumerate(runs):
        first = start(run[0].day)
        if i + 1 < len(runs):
            until: date | None = start(runs[i + 1][0].day)
        elif run[-1].day.year >= LAST:
            until = None
        else:
            until = next((mark for mark in marks if mark > run[-1].day), run[-1].day + timedelta(days=1))
        out.append((first, until, run[0].munis, run))
    return out


# the year of the newest election; an area used in it is still current
LAST = 2024


def names(area: Area) -> dict[str, str]:
    """The area's names from the first election that used it, English first, then Dutch, French, and German."""
    first = min(area.uses, key=lambda use: use.day)
    found: dict[str, str] = {}
    for lang in ("en", "nl", "fr", "de"):
        text = re.sub(
            r"^(Constituency of|Electoral District of|Kieskring van|Circonscription de)\s+",
            "",
            first.labels.get(lang) or "",
            flags=re.IGNORECASE,
        )
        if text:
            found[lang] = _clean(text)
    return found


def geo() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "Outlines of the electoral districts of the Belgian federal and regional parliaments, 1894 to 2024, dissolved "
        "from Statbel's municipalities",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BELGIUM, TERRITORY, nis.STATBEL_SOURCE}
    statbel = nis.STATBEL_SOURCE  # the source row and its artifacts belong to the geo package
    p.uses(raw.cached(STATBEL, SECTORS + PLANS[PLAN][0]))
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=BELGIUM,
        kind="electoral_district",
        rank=2,
        noun="electoral district",
        code_system=None,
    )
    shapes = municipalities()
    _, crs = nis._reader(PLAN, False)
    starts: set[date] = set()
    for key in sorted(USED):
        area = AREAS[key]
        id = area_id(key)
        found = spans(area)
        p.add("area", id=id, scheme_code=SCHEME)
        since, until = found[0][0], found[-1][1]
        p.add("area_code", area_id=id, code=key, valid_from=since, valid_to=until)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=since, valid_to=until)
        label = names(area)
        for lang, text in label.items():
            p.name(id, text, lang, "native" if lang != "en" else "official", lang == next(iter(label)))
        for start, end, munis, _ in found:
            starts.add(start)
            merged = shapely.make_valid(shapely.union_all([shapes[code] for code in sorted(munis)]))
            wgs = nis._wgs(merged, crs)
            point = shapely.set_srid(shapely.point_on_surface(wgs), 4326)
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=start,
                valid_to=end,
                geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
                label_point=shapely.to_wkb(point, hex=True, include_srid=True),
                quality="official",
                source_id=statbel,
            )
    plans = sorted(BREAKS)
    for since in sorted(starts):
        later = [mark for mark in plans if mark > since]
        p.add(
            "boundary_set",
            id=p.id("boundary_set", SCHEME, since),
            scheme_code=SCHEME,
            vintage=str(since.year),
            valid_from=since,
            valid_to=later[0] if later else None,
            as_of=since,
        )
    return p
