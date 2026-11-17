"""South Korea's provinces and metropolitan cities (시·도) and its cities, counties, and districts (시·군·구) as areas,
with geoBoundaries outlines, for the National Election Commission's reporting units to point at.

The areas are named as the commission names its counting offices on its most recent election day. geoBoundaries
draws the autonomous cities, counties, and districts under romanized names; each is matched to the commission's
Korean name by romanizing the latter (Revised Romanization) inside the province its outline falls in. A city that
the commission counts by its non-autonomous districts (수원시장안구 and the like) is one area, and those districts'
units lie within it. The two schemes, kr.sido and kr.sigungu, are the census's tiers of local government.
"""

import functools
import json
import re
from datetime import date
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "kr.nec_stats"
GEO = f"{SOURCE}/geo"
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fKOR"))
SIDO_SCHEME = "kr.sido"
SIGUNGU_SCHEME = "kr.sigungu"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/KOR"
GB_SIDO = f"{GEOBOUNDARIES}/ADM1/geoBoundaries-KOR-ADM1.geojson"
GB_SIGUNGU = f"{GEOBOUNDARIES}/ADM2/geoBoundaries-KOR-ADM2.geojson"
SIDO_FROM = date(2021, 1, 1)
SIGUNGU_FROM = date(2020, 1, 1)
LINK_FROM = "19950627"
"""The first day whose city, county, and district units are linked by name: the 1995 local elections, which set up
the autonomous cities, counties, and districts of today. Before it a name can stand for a different territory."""
ROMAN = {
    # Names whose romanization the outlines spell otherwise than the rules give.
    "여주": "yeoju",
    "세종특별자치": "sejong",
}

MOVED = {("47", "군위군"): "27"}
"""Counties that moved to another province while keeping their territory: Gunwi joined Daegu on July 1, 2023."""
RENAMED = {("28", "남구"): "미추홀구", ("41", "여주군"): "여주시"}
"""Names given up with the territory kept: Incheon's Nam-gu became Michuhol-gu in 2018, and Yeoju County became
Yeoju City in 2013."""

INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
VOWELS = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo",
    "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
]  # fmt: skip
FINALS = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l",
    "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
]  # fmt: skip
CARRY = {1: "g", 4: "n", 7: "d", 8: "r", 16: "m", 17: "b", 19: "s", 22: "j", 23: "ch", 25: "t", 26: "p"}
"""Finals that move to the next syllable when it begins with a silent ㅇ."""


def romanize(text: str) -> str:
    """Revised Romanization of a Hangul place name, with the sound changes place names show, lowercased."""
    parts = []
    for char in text:
        index = ord(char) - 0xAC00
        if not 0 <= index < 11172:
            continue
        parts.append([index // 588, (index % 588) // 28, index % 28])
    out = []
    for at, (first, middle, last) in enumerate(parts):
        initial = INITIALS[first]
        if at:
            before = parts[at - 1][2]
            if first == 5:  # ㄹ
                initial = "l" if FINALS[before] in {"l", "n"} else "n" if before else "r"
            elif first == 11 and before in CARRY:
                initial = CARRY[before]
            elif first == 2 and FINALS[before] == "l":
                initial = "l"
        final = FINALS[last]
        if at + 1 < len(parts):
            after = parts[at + 1][0]
            if after == 11 and last in CARRY:
                final = ""
            elif after in {2, 6} or (after == 5 and final in {"k", "p", "t"}):
                final = {"k": "ng", "p": "m", "t": "n"}.get(final, final)
            elif after == 5 and final == "n":
                final = "l"
        out.append(initial + VOWELS[middle] + final)
    return "".join(out)


SUFFIX = re.compile(r"(시|군|구)$")
SPLIT = re.compile(r"^(.+?시)(.+구|갑|을|병|정)$")


def stem(text: str) -> str:
    return SUFFIX.sub("", text) if len(text) > 1 else text


def city(text: str) -> str:
    """The autonomous city of a non-autonomous district's name (수원시장안구 → 수원시), or the name itself."""
    found = SPLIT.match(text)
    return found.group(1) if found else text


def _roman_stem(text: str) -> str:
    name = re.sub(r"\[.*\]", "", text).strip().lower()
    return re.sub(r"-(si|gun|gu)$", "", name).replace("-", "").replace(" ", "")


def _shape(feature: dict[str, object]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
    polygons = [
        polygon
        for part in shapely.get_parts(found)
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def fetch() -> None:
    for url in (GB_SIDO, GB_SIGUNGU):
        raw.fetch(SOURCE, url, suffix=".geojson")


def key(kind: str, *parts: str) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *parts])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


@functools.cache
def outlines() -> tuple[dict[str, shapely.Geometry], list[tuple[str, dict[str, float], shapely.Geometry]]]:
    """Province outlines by commission code, and each city, county, and district outline with its romanized stem
    and how far its inner point lies from each province's outline."""
    sido: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_SIDO).path.read_bytes())["features"]:
        sido[feature["properties"]["shapeISO"].removeprefix("KR-")] = _shape(feature)
    sigungu = []
    for feature in json.loads(raw.cached(SOURCE, GB_SIGUNGU).path.read_bytes())["features"]:
        shape = _shape(feature)
        point = shapely.point_on_surface(shape)
        distance = {code: float(shapely.distance(outline, point)) for code, outline in sido.items()}
        sigungu.append((_roman_stem(feature["properties"]["shapeName"]), distance, shape))
    return sido, sigungu


def match(names: set[tuple[str, str]]) -> dict[tuple[str, str], shapely.Geometry]:
    """The outline of each (province code, name) whose romanized name geoBoundaries draws: the only outline of
    that name, or, where several share it, the one lying nearest the province (Natural Earth's province outlines are
    coarse, so a district on a coast or a border can fall just outside its own)."""
    _, sigungu = outlines()
    found: dict[tuple[str, str], shapely.Geometry] = {}
    taken: set[int] = set()
    for code, town in sorted(names):
        roman = ROMAN.get(stem(town), romanize(stem(town)))
        candidates = [at for at, (text, _, _) in enumerate(sigungu) if text == roman and at not in taken]
        if not candidates:
            continue
        best = min(candidates, key=lambda at: sigungu[at][1].get(code, float("inf")))
        taken.add(best)
        found[code, town] = sigungu[best][2]
    return found


class Areas:
    """The areas, built from the commission's (province code, counting office) names of one day."""

    def __init__(self, sido: dict[str, tuple[str, str]], towns: set[tuple[str, str]]) -> None:
        self.sido = sido
        self.towns: dict[tuple[str, str], str] = {}
        for code, town in towns:
            self.towns[code, town] = city(town)
        self._names = {(code, city(town)) for code, town in towns}

    def sido_area(self, code: str) -> str | None:
        return area(SIDO_SCHEME, code) if code in self.sido else None

    def link(self, day: str, path: tuple[str, ...]) -> tuple[str, str | None]:
        """The relation and area of the unit at a province or city, county, or district path."""
        if len(path) == 1:
            found = self.sido_area(path[0])
            return ("equals", found) if found else ("unlocated", None)
        if day < LINK_FROM or len(path) != 2:
            return "unlocated", None
        code, town = path
        town = RENAMED.get((code, town), town)
        code = MOVED.get((code, town), code)
        whole = city(town)
        if (code, whole) not in self.names():
            return "unlocated", None
        return ("equals" if whole == town else "within"), area(SIGUNGU_SCHEME, f"{code}:{whole}")

    def names(self) -> set[tuple[str, str]]:
        return self._names


def build(areas: Areas) -> tuple[Package, list[str]]:
    """The geo package, and the cities, counties, and districts geoBoundaries does not draw."""
    p = Package(
        GEO,
        SOURCE,
        "South Korea's provinces and metropolitan cities, and its cities, counties, and districts, as the National "
        "Election Commission names them, with geoBoundaries outlines",
    )
    p.external |= {COUNTRY, SIDO_SCHEME, SIGUNGU_SCHEME}
    sido_source, sigungu_source = key("source", "geoboundaries-adm1"), key("source", "geoboundaries-adm2")
    for id, title, license_, license_url, notes in (
        (
            sido_source,
            "geoBoundaries gbOpen: South Korea ADM1 (provinces and metropolitan cities)",
            "Public domain (Natural Earth)",
            "https://www.naturalearthdata.com/about/terms-of-use/",
            "Boundary year 2021, from Natural Earth.",
        ),
        (
            sigungu_source,
            "geoBoundaries gbOpen: South Korea ADM2 (cities, counties, and districts)",
            "Creative Commons Attribution 3.0",
            "https://creativecommons.org/licenses/by/3.0/",
            "Boundary year 2020, from geoBoundaries and citypopulation.de.",
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab)",
            title=title,
            url="https://www.geoboundaries.org/",
            license=license_,
            license_url=license_url,
            attribution="geoBoundaries (William & Mary geoLab)",
            notes=notes,
        )
    p.artifact(raw.cached(SOURCE, GB_SIDO), sido_source)
    p.artifact(raw.cached(SOURCE, GB_SIGUNGU), sigungu_source)

    def place(scheme: str, code: str, parent: str, names: list[tuple[str, str]]) -> str:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        for at, (text, lang) in enumerate(names):
            p.name(id, text, lang, "official", at == 0)
        return id

    sido_shapes, _ = outlines()
    drawn: dict[tuple[str, str], shapely.Geometry] = {}
    for code, (korean, english) in sorted(areas.sido.items()):
        place(SIDO_SCHEME, code, COUNTRY, [(korean, "ko"), (english, "en")])
        if code in sido_shapes:
            drawn[SIDO_SCHEME, code] = sido_shapes[code]
    shapes = match(areas.names())
    missing = []
    for code, town in sorted(areas.names()):
        place(SIGUNGU_SCHEME, f"{code}:{town}", area(SIDO_SCHEME, code), [(town, "ko")])
        if (code, town) in shapes:
            drawn[SIGUNGU_SCHEME, f"{code}:{town}"] = shapes[code, town]
        else:
            missing.append(f"{town} ({areas.sido[code][0]})")
    for scheme, vintage, valid_from in ((SIDO_SCHEME, "2021", SIDO_FROM), (SIGUNGU_SCHEME, "2020", SIGUNGU_FROM)):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, vintage),
            scheme_code=scheme,
            vintage=vintage,
            valid_from=valid_from,
            as_of=valid_from,
        )
    for (scheme, code), shape in sorted(drawn.items(), key=lambda item: item[0]):
        p.add(
            "area_geometry",
            area_id=area(scheme, code),
            valid_from=SIDO_FROM if scheme == SIDO_SCHEME else SIGUNGU_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=sido_source if scheme == SIDO_SCHEME else sigungu_source,
        )
    return p, missing
