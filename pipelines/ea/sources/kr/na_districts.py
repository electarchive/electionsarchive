"""National Assembly constituency outlines, put together from eup, myeon, and dong outlines.

No official map of the constituencies exists. The National Election Commission counts every constituency by eup,
myeon, and dong (읍면동) from 2008 on, and by polling district (투표구) named after its eup, myeon, or dong from 1973
to 2004, so the count itself says which eup, myeon, and dong make up each constituency. Each constituency's outline
is the union of those eup, myeon, and dong outlines, from Statistics Korea's statistical geographic information
service (SGIS) as vuski/admdongkor keeps them for every year since 1975, taken at the vintage nearest each election.

A name the count prints is matched to an outline of the same name in its province (or the province it was then part
of), narrowed to the city, county, or district the count names, and, where several outlines still share the name, to
the one nearest the constituency's other eup, myeon, and dong. A name the vintage lacks is looked up in the next
vintage, then as the eup, myeon, or dong it was later merged into or split from. An outline that no constituency's
count names (an area without voters, such as a strip by the demilitarized zone, or a name both vintages spell
otherwise) goes to the constituency of its own city, county, or district that it shares the longest border with, or,
when no count names any part of that city, county, or district, to the constituency named after it. One that neither
rule places is left out: it lay in a constituency whose member was returned unopposed, which held no vote.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
import pyproj
import shapely

from ea import raw

SOURCE = "kr.na"
REPOSITORY = "https://github.com/vuski/admdongkor"
COMMIT = "078ef4ac13b8bfeb215d2eabace49f4fdea71d5e"
BASE = f"https://raw.githubusercontent.com/vuski/admdongkor/{COMMIT}/parquet"
VINTAGES = {
    "19730227": ("19751231", "19751231"),
    "19781212": ("19801231", "19751231"),
    "19810325": ("19801231", "19851231"),
    "19850212": ("19851231", "19801231"),
    "19880426": ("19851231", "19901231"),
    "19920324": ("19901231", "19951231"),
    "19960411": ("19951231", "20001231"),
    "20000413": ("20001231", "19951231"),
    "20040415": ("20031231", "20041231"),
    "20080409": ("20071231", "20081231"),
    "20120411": ("20111231", "20121231"),
    "20160413": ("20160201", "20170418"),
    "20200415": ("20200101", "20200701"),
    "20240410": ("20240401", "20240701"),
}
"""For each election the counts name eup, myeon, and dong for, the outline vintage nearest it, and the vintage to try
next for a name the first lacks."""
FIRST = "19730227"
EMD = "20080409"

PREFIXES = (
    ("서울", "11"), ("부산", "26"), ("대구", "27"), ("인천", "28"), ("광주", "29"), ("대전", "30"), ("울산", "31"),
    ("세종", "50"), ("경기", "41"), ("강원", "42"), ("충청북", "43"), ("충북", "43"), ("충청남", "44"),
    ("충남", "44"), ("전라북", "45"), ("전북", "45"), ("전라남", "46"), ("전남", "46"), ("경상북", "47"),
    ("경북", "47"), ("경상남", "48"), ("경남", "48"), ("제주", "49"),
)  # fmt: skip
"""Province names to the commission's province codes."""
FAMILY = {
    "11": {"11", "41"},
    "26": {"26", "48"},
    "27": {"27", "47"},
    "28": {"28", "41"},
    "29": {"29", "46"},
    "30": {"30", "44"},
    "31": {"31", "48"},
    "41": {"41", "28", "11"},
    "42": {"42"},
    "43": {"43", "50"},
    "44": {"44", "30", "50"},
    "45": {"45"},
    "46": {"46", "29"},
    "47": {"47", "27"},
    "48": {"48", "26", "31"},
    "49": {"49"},
    "50": {"50", "43", "44"},
}
"""The provinces whose outlines a province's constituencies may take eup, myeon, and dong from: the province, and
those it gave territory to or took territory from (Daegu, Incheon, Gwangju, Daejeon, and Ulsan left their provinces
between 1981 and 1997, Seoul grew into Gyeonggi, and Sejong was cut from South Chungcheong and North Chungcheong in
2012)."""
FORMER = {
    ("41", "성남시"): "광주군",
    ("41", "안양시"): "시흥군",
    ("42", "춘성군"): "춘천군",
}
"""Cities and counties an outline vintage names otherwise than the election before it: Seongnam and Anyang were cut
from Gwangju and Siheung counties in July 1973, after that February's election, and Chunseong County was renamed
Chuncheon County in 1991, before the 1992 election."""
POLLING = re.compile(r"제?\d+투(?:표구)?$")
PARENS = re.compile(r"\([^()]*\)")
SPLIT_SUFFIX = re.compile(r"(갑|을|병|정|무)$")
SUFFIX = re.compile(r"(시|군|구)$")
TO_WGS84 = pyproj.Transformer.from_crs(5179, 4326, always_xy=True)


PUBLISHER = "vuski/admdongkor, from Statistics Korea's SGIS"
TITLE = "대한민국 행정동 경계 (admdongkor): eup, myeon, and dong outlines by vintage, 1975–2026"
LICENSE = "CC BY 4.0, from SGIS boundaries released under the Korea Open Government License Type 1 (attribution)"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "This data is derived from administrative-dong boundaries released by Statistics Korea SGIS "
    "(https://sgis.kostat.go.kr) under KOGL Type 1, modified by vuski/admdongkor "
    "(https://github.com/vuski/admdongkor), distributed under CC BY 4.0."
)
NOTES = (
    "Constituency outlines for the National Assembly elections of 1973 to 2024 are unions of these eup, myeon, and "
    "dong outlines, at the vintage nearest each election, for the eup, myeon, and dong the National Election "
    "Commission counts each constituency by (by name from 2008, and by the names of its polling districts before). "
    "An outline the count does not name goes to the constituency of its own city, county, or district that it "
    "shares the longest border with. The outlines are approximate where the vintage differs from the plan, most of "
    "all for 1973 to 1985, whose nearest vintages are 1975, 1980, and 1985, and an eup, myeon, or dong that the "
    "plan split between two constituencies lies whole in both."
)


def url(vintage: str) -> str:
    return f"{BASE}/emd_{vintage}.parquet"


def vintages() -> list[str]:
    return sorted({code for pair in VINTAGES.values() for code in pair})


def fetch() -> None:
    for code in vintages():
        raw.fetch(SOURCE, url(code), suffix=".parquet")


def _spelling(text: str) -> str:
    text = re.sub(r"[\s·.,ㆍ_\-]", "", text)
    return text.replace("룡", "용").replace("릉", "능")


def counted(text: str) -> list[str]:
    """The forms a name the commission prints may take in the outlines: 숭인제1동 is 숭인1동, but 거제1동 is itself."""
    plain = _spelling(text)
    return list(dict.fromkeys([_spelling(re.sub(r"(?<=[가-힣])제(\d)", r"\1", text)), plain]))


def _place(label: str) -> bool:
    """Whether a polling district's label names a place, not a row of misplaced or absentee ballots."""
    return bool(label) and not label.startswith("잘못") and "투표" not in label


def drawn(text: str) -> str:
    return _spelling(text)


def _compact(text: str) -> str:
    return re.sub(r"\s", "", PARENS.sub("", text))


def _province(name: str) -> str | None:
    return next((code for prefix, code in PREFIXES if name.startswith(prefix)), None)


@dataclass
class Emd:
    code: str
    name: str
    town: str
    province: str
    shape: shapely.Geometry
    point: tuple[float, float]


@dataclass
class District:
    names: list[tuple[str, str]] = field(default_factory=list)
    """(counting office, name) of each eup, myeon, and dong the count names."""
    taken: dict[str, Emd] = field(default_factory=dict)
    later: dict[str, Emd] = field(default_factory=dict)
    """Outlines taken from the next vintage."""
    beneath: dict[str, Emd] = field(default_factory=dict)
    """Outlines of the first vintage that lie mostly under one taken from the next: the part of each that no other
    constituency covers joins this one, so the two vintages' differing lines leave no holes."""
    missing: list[str] = field(default_factory=list)
    filled: int = 0
    nearest: int = 0


def vintage(code: str) -> list[Emd]:
    frame = pl.read_parquet(raw.cached(SOURCE, url(code)).path)
    found = []
    for row in frame.iter_rows(named=True):
        province = _province(str(row["sidonm"]))
        if province is None:
            raise ValueError(f"{code}: no province code for {row['sidonm']}")
        shape = shapely.make_valid(shapely.from_wkb(row["geom"]))
        point = shapely.point_on_surface(shape)
        found.append(
            Emd(
                code=f"{code}/{row['emdcd']}",
                name=drawn(str(row["emdnm"])),
                town=_compact(str(row["sggnm"])),
                province=province,
                shape=shape,
                point=(shapely.get_x(point), shapely.get_y(point)),
            )
        )
    return found


def members(results: pl.DataFrame, day: str) -> dict[tuple[str, str], District]:
    """Each constituency of the election with the eup, myeon, and dong its count names, in the count's order."""
    rows = results.filter(pl.col("day") == day)
    if day >= EMD:
        rows = rows.filter((pl.col("kind") == "emd") & (pl.col("channel") == 0))
    else:
        rows = rows.filter(pl.col("kind") == "tupyogu")
    found: dict[tuple[str, str], District] = {}
    for sido, sgg, town, label in rows.select("sido", "sgg", "town", "label").iter_rows():
        name = _spelling(POLLING.sub("", str(label)) if day < EMD else str(label))
        district = found.setdefault((sido, sgg), District())
        if _place(name) and (town, name) not in district.names:
            district.names.append((town, name))
    return found


def _towns(emd: Emd) -> set[str]:
    """Forms of an outline's city, county, or district name to look for in the counting office's name."""
    forms = {emd.town}
    city = re.match(r"^(.+?시)(.+구)$", emd.town)
    if city:
        forms |= {city.group(1), city.group(2)}
    return {form for form in forms if len(form) > 1}


def _office(town: str) -> str:
    return SPLIT_SUFFIX.sub("", _compact(town))


def _hinted(candidates: list[Emd], town: str, sgg: str) -> list[Emd]:
    """The candidates in the city, county, or district the counting office or the constituency is named after."""
    office = _office(town)
    exact = [emd for emd in candidates if emd.town == office or office in _towns(emd)]
    if exact:
        return exact
    text = f"{office}|{sgg}"
    return [emd for emd in candidates if any(form in text for form in _towns(emd))]


NEAR = 15_000.0
"""How far, in meters, from the rest of its constituency an outline found only by its name may lie."""


class Index:
    def __init__(self, emds: list[Emd]) -> None:
        self.emds = emds
        self.by_name: dict[tuple[str, str], list[Emd]] = defaultdict(list)
        self.by_stem: dict[tuple[str, str], list[Emd]] = defaultdict(list)
        for emd in emds:
            self.by_name[emd.province, emd.name].append(emd)
            self.by_stem[emd.province, re.sub(r"\d+", "", emd.name)].append(emd)

    def named(self, sido: str, name: str) -> list[Emd]:
        found = [
            emd
            for form in counted(name)
            for province in sorted(FAMILY[sido])
            for emd in self.by_name.get((province, form), [])
        ]
        return list({id(emd): emd for emd in found}.values())

    def merged(self, sido: str, name: str) -> list[Emd]:
        """The outlines a name stands for when the count's eup, myeon, or dong was later merged from several
        (공덕동 for 공덕1동 and 공덕2동), or split into several (흑석1동 from 흑석동)."""
        found = []
        for form in counted(name):
            table, key = (self.by_name, re.sub(r"\d+", "", form)) if re.search(r"\d", form) else (self.by_stem, form)
            found += [emd for province in sorted(FAMILY[sido]) for emd in table.get((province, key), [])]
        return list({emd.code: emd for emd in found}.values())


def _centre(district: District) -> tuple[float, float] | None:
    points = [emd.point for emd in [*district.taken.values(), *district.later.values()]]
    if not points:
        return None
    return (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))


def _distance(emd: Emd, centre: tuple[float, float]) -> float:
    return (emd.point[0] - centre[0]) ** 2 + (emd.point[1] - centre[1]) ** 2


def assemble(results: pl.DataFrame, day: str) -> dict[tuple[str, str], District]:
    first, second = (Index(vintage(code)) for code in VINTAGES[day])
    districts = members(results, day)
    waiting: list[tuple[District, str, list[Emd], dict[str, Emd], bool]] = []
    for (sido, sgg), district in districts.items():
        for town, name in district.names:
            label = f"{town} {name}"
            found = None
            for into, look in ((district.taken, first.named), (district.later, second.named)):
                hinted = _hinted(look(sido, name), town, sgg)
                if hinted:
                    found = (into, hinted, True)
                    break
            if found is None:
                merged = _hinted(first.merged(sido, name), town, sgg)
                if merged and not re.search(r"\d", name):
                    for emd in merged:
                        district.taken[emd.code] = emd
                    continue
                if merged:
                    found = (district.taken, merged, True)
            if found is None:
                for into, look in ((district.taken, first.named), (district.later, second.named)):
                    named = look(sido, name)
                    if named:
                        found = (into, named, False)
                        break
            if found is None:
                district.missing.append(label)
                continue
            into, candidates, hinted = found
            if hinted and len(candidates) == 1:
                into[candidates[0].code] = candidates[0]
            else:
                waiting.append((district, label, candidates, into, hinted))
    claimed = {
        id(emd) for district in districts.values() for emd in [*district.taken.values(), *district.later.values()]
    }
    for district, label, candidates, into, hinted in sorted(waiting, key=lambda item: (not item[4], len(item[2]))):
        own = {id(emd) for emd in [*district.taken.values(), *district.later.values()]}
        candidates = [emd for emd in candidates if id(emd) not in claimed or id(emd) in own] or (
            candidates if hinted else []
        )
        if not candidates:
            district.missing.append(label)
            continue
        centre = _centre(district)
        if centre is None:
            if len(candidates) == 1:
                into[candidates[0].code] = candidates[0]
                claimed.add(id(candidates[0]))
            else:
                district.missing.append(label)
            continue
        best = min(candidates, key=lambda emd: _distance(emd, centre))
        if not hinted and _distance(best, centre) > NEAR**2:
            district.missing.append(label)
            continue
        into[best.code] = best
        claimed.add(id(best))
        district.nearest += 1
    _fill(districts, first)
    return districts


def _fill(districts: dict[tuple[str, str], District], first: Index) -> None:
    """Give every outline of the first vintage that no count names to the constituency it shares the longest border
    with, preferring one in its own city, county, or district."""
    owner: dict[str, tuple[str, str]] = {}
    for key, district in districts.items():
        for code in district.taken:
            owner.setdefault(code, key)
    later = [(key, emd.shape) for key, district in districts.items() for emd in district.later.values()]
    if later:
        tree = shapely.STRtree([shape for _, shape in later])
        for emd in first.emds:
            if emd.code in owner:
                continue
            for at in tree.query(emd.shape, predicate="intersects"):
                key, shape = later[int(at)]
                if shapely.area(shapely.intersection(emd.shape, shape)) > 0.5 * shapely.area(emd.shape):
                    owner[emd.code] = key
                    districts[key].beneath[emd.code] = emd
                    break
    present: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for emd in first.emds:
        if emd.code in owner:
            present[emd.province, emd.town].add(owner[emd.code])
    texts = {
        key: "|".join([*sorted({_office(town) for town, _ in district.names}), re.sub(r"\s", "", key[1])])
        for key, district in districts.items()
    }
    titled: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for emd in first.emds:
        place = (emd.province, emd.town)
        if place in present or place in titled:
            continue
        forms = _towns(emd) | {stem for stem in [SUFFIX.sub("", emd.town)] if len(stem) > 1}
        if place in FORMER:
            forms.add(FORMER[place])
        titled[place] = {
            key for key, text in texts.items() if emd.province in FAMILY[key[0]] and any(f in text for f in forms)
        }

    def allowed(emd: Emd) -> set[tuple[str, str]] | None:
        place = (emd.province, emd.town)
        return present.get(place) or titled.get(place) or None

    # A city, county, or district that no count names and no constituency is named after stays out: it was the
    # territory of a constituency without a vote, whose member was returned unopposed. A branch office (출장소)
    # counted with its parent city or county is not named on its own, so it goes by its borders.
    left = [
        emd for emd in first.emds if emd.code not in owner and (allowed(emd) is not None or emd.town.endswith("출장소"))
    ]
    while left:
        claimed = [emd for emd in first.emds if emd.code in owner]
        tree = shapely.STRtree([emd.shape for emd in claimed])
        progress = []
        for emd in left:
            keys = allowed(emd)
            border: dict[tuple[str, str], float] = defaultdict(float)
            for at in tree.query(shapely.buffer(emd.shape, 5), predicate="intersects"):
                other = claimed[int(at)]
                key = owner[other.code]
                if keys is None or key in keys:
                    edge = shapely.intersection(shapely.boundary(emd.shape), shapely.buffer(other.shape, 5))
                    border[key] += float(shapely.length(edge))
            if border:
                progress.append((emd, max(border, key=lambda key: border[key])))
        if not progress:
            for emd in left:
                keys = allowed(emd)
                pool = [other for other in claimed if keys is None or owner[other.code] in keys] or claimed
                progress.append((emd, owner[min(pool, key=lambda other: _distance(other, emd.point)).code]))
        for emd, key in progress:
            owner[emd.code] = key
            districts[key].taken[emd.code] = emd
            districts[key].filled += 1
        left = [emd for emd in left if emd.code not in owner]


def _wgs84(shape: shapely.Geometry) -> shapely.Geometry:
    def move(points: Any) -> Any:
        x, y = TO_WGS84.transform(points[:, 0], points[:, 1])
        return np.column_stack([x, y])

    moved = shapely.set_precision(shapely.transform(shape, move), 1e-6)
    polygons = [
        polygon
        for part in shapely.get_parts(shapely.make_valid(moved))
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def outlines(districts: dict[tuple[str, str], District]) -> dict[tuple[str, str], shapely.Geometry]:
    """Each constituency's outline, in WGS 84."""
    keys = list(districts)
    unions = [
        shapely.union_all([emd.shape for emd in [*districts[key].taken.values(), *districts[key].later.values()]])
        for key in keys
    ]
    tree = shapely.STRtree(unions)
    found = {}
    for at, key in enumerate(keys):
        pieces = [unions[at]]
        for emd in districts[key].beneath.values():
            others = [unions[int(near)] for near in tree.query(emd.shape, predicate="intersects") if int(near) != at]
            pieces.append(shapely.difference(emd.shape, shapely.union_all(others)) if others else emd.shape)
        found[key] = _wgs84(shapely.union_all(pieces))
    return found


def build(results: pl.DataFrame) -> pl.DataFrame:
    """Every constituency's outline and how it was put together, for each election the counts name eup, myeon, and
    dong for."""
    rows = []
    for day in VINTAGES:
        districts = assemble(results, day)
        shared: dict[str, int] = defaultdict(int)
        for district in districts.values():
            for code in {*district.taken, *district.later}:
                shared[code] += 1
        shapes = outlines(districts)
        for (sido, sgg), district in districts.items():
            shape = shapes[sido, sgg]
            rows.append(
                {
                    "day": day,
                    "sido": sido,
                    "sgg": sgg,
                    "geom": shapely.to_wkb(shape, hex=True, include_srid=True),
                    "label_point": shapely.to_wkb(
                        shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                    ),
                    "named": len(district.names),
                    "outlines": len(district.taken) + len(district.later),
                    "later": len(district.later),
                    "nearest": district.nearest,
                    "filled": district.filled,
                    "shared": sum(1 for code in {*district.taken, *district.later} if shared[code] > 1),
                    "missing": district.missing,
                }
            )
        print(day, len(districts), "constituencies", flush=True)
    return pl.DataFrame(rows)
