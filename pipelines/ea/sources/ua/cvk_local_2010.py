"""Ukraine: the regular local elections of 31 October 2010.

Part of the ua.cvk_local pipeline; its module calls fetch() and parse() here.

The Commission's results site for the local elections of 2010 (cvk.gov.ua/pls/vm2010, election 800) publishes only
who holds each seat on the date the page is read, not the votes:

- per council (wm02815): the seats, the seats filled, the deputies each nominator holds, and each deputy with the
  single-member district that elected them, or none for a deputy elected from a list, and the date they were
  recognized as elected;
- per head of a village, settlement, or city (wm02814): the head, their nominator, and the date they were elected,
  linked from the list of heads with the code of the election that chose them (800 for this one).

The Verkhovna Rada of Crimea, oblast, raion, city, and city-raion councils were elected half from closed party lists
and half in single-member districts; settlement and village councils in single-member districts; heads by a
plurality in one round.

Deputies whose powers ended early are not on the pages, and the list seats they left are shown held by the next
candidates of their list. A district seat filled at a later by-election is shown held by its winner, recognized after
2010, and is left out here. So is the Verkhovna Rada of Crimea, whose page lists no deputies, and the heads chosen
since 2010, whose 2010 predecessors the site no longer names.
"""

import functools
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import shapely

from ea import changeset, raw
from ea.latin import ukrainian
from ea.package import Package
from ea.sources.ua import cvk_local as base
from ea.sources.ua.cvk import clean, norm, number, text

SOURCE = base.SOURCE
BASE = "https://www.cvk.gov.ua/pls/vm2010/"
ELECTION = 800
DAY = date(2010, 10, 31)
YEAR_END = date(2010, 12, 31)
LAW = date(2010, 7, 10)  # the Law on the elections of deputies of the Verkhovna Rada of Crimea, councils, and heads
NEXT = date(2015, 10, 25)  # the next regular local elections, under a new law
COUNCILS = f"{BASE}wm00114pt00_t001f01={ELECTION}pxto=0.html"
HEADS = f"{BASE}wm00113pt00_t001f01={ELECTION}.html"
WORKERS = 6
GB = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/UKR/"
COUNCIL_SHAPES = f"{GB}ADM3/geoBoundaries-UKR-ADM3.geojson"
OBLAST_SHAPES = f"{GB}ADM1/geoBoundaries-UKR-ADM1.geojson"
TOLERANCE = base.TOLERANCE


def _links(url: str, pattern: str) -> list[str]:
    body = text(raw.cached(SOURCE, url))
    return [BASE + link for link in dict.fromkeys(re.findall(rf'href="({pattern}[^"]*)"', body))]


def council_lists() -> list[str]:
    return _links(COUNCILS, "wm00312[56]")


def head_lists() -> list[str]:
    return _links(HEADS, "wm003124")


def fetch() -> None:
    for url in (COUNCILS, HEADS):
        raw.fetch(SOURCE, url, suffix=".html")
    for url in (OBLAST_SHAPES, COUNCIL_SHAPES):
        raw.fetch(SOURCE, url, suffix=".geojson")
    lists = council_lists() + head_lists()
    raw.fetch_many(SOURCE, lists, suffix=".html", workers=WORKERS)
    pages: list[str] = []
    for url in lists:
        if url in raw.keys(SOURCE):
            pages += _links(url, "wm0281[45]")
    raw.fetch_many(SOURCE, list(dict.fromkeys(pages)), suffix=".html", workers=WORKERS)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the pages

key = base.key
param = base.param
rows = base.rows
UKRAINE = base.UKRAINE
SELF = base.SELF
KYIV, SEVASTOPOL, CRIMEA = "80", "85", "1"
CVK = key("source", "cvk", "vm2010")
S_REGION = "ua.region_2010"
S_RAION = "ua.raion_2010"
S_RADA = "ua.rada_2010"
S_DISTRICT = "ua.city_raion_2010"
# The council tiers by the lists' code (pid112) and the head tiers.
TIERS = {"11": "crimea", "12": "oblast", "14": "special_city", "21": "raion", "31": "city", "32": "city"}
TIERS |= {"33": "town", "41": "city_district", "51": "settlement", "61": "village"}
HEAD_TIERS = {"14": "city", "31": "city", "32": "city", "33": "town", "51": "settlement", "61": "village"}
MIXED = ("crimea", "oblast", "special_city", "raion", "city", "town", "city_district")
RADA_TIERS = ("city", "town", "settlement", "village")


def _header(url: str) -> str:
    """The place line a page prints under its title: «Вінницька область Барський район»."""
    found = re.search(
        r'<p class=p1><img src="img/check_tick1\.gif"[^>]*>&nbsp;([^<]*)</p>', text(raw.cached(SOURCE, url))
    )
    return clean(found.group(1)) if found else ""


@functools.cache
def region_names() -> dict[str, str]:
    """The region's name by its code (pid100), from the header of its lists."""
    found: dict[str, str] = {}
    for url in council_lists() + head_lists():
        region = param(url, "pid100")
        if region not in found and url in raw.keys(SOURCE):
            name = _header(url)
            if name:
                found[region] = name
    return found


def raion_of(region: str, place: str) -> str:
    """The raion a page's place line names after its region, or none."""
    rest = place.removeprefix(region_names().get(region, "")).strip()
    return rest if rest.endswith(" район") else ""


@dataclass
class Council:
    rada: str
    tier: str
    region: str
    name: str
    seats: int
    url: str
    raion: str = ""  # «Барський район», where the council lies in one


@functools.cache
def councils() -> dict[str, Council]:
    found: dict[str, Council] = {}
    for url in council_lists():
        tier = TIERS[param(url, "pid112")]
        region = param(url, "pid100")
        for cells, links in rows(url):
            link = next((link for link in links if link.startswith("wm02815")), None)
            if link is None or len(cells) < 4:
                continue
            name, _, raion = cells[0].partition(", ")
            rada = param(link, "pid102")
            page = BASE + link
            if page not in raw.keys(SOURCE):
                continue
            c = Council(rada, tier, region, name, number(cells[1]) or 0, page)
            c.raion = raion if raion.endswith(" район") else raion_of(region, _header(page))
            found[rada] = c
    return found


@dataclass
class Deputy:
    name: str
    district: str  # empty for a deputy elected from a list
    nominator: str
    recognized: date | None
    election: str  # the code of the election the district's link names
    seq: int = 1  # the namesake's place among deputies of one district or list with the same name

    @property
    def tag(self) -> tuple[object, ...]:
        return (self.district or "list", self.name) + ((self.seq,) if self.seq > 1 else ())


@dataclass
class Page:
    deputies: list[Deputy] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def _day(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        return None


@functools.cache
def council_page(url: str) -> Page:
    page = Page()
    section = ""
    nominator = ""
    for cells, links in rows(url):
        if not cells:
            continue
        head = cells[0]
        if head.startswith(("Суб'єкт висування", "Суб’єкт висування")):
            section = "summary"
            continue
        if head.startswith("№ зп"):
            section = "deputies"
            continue
        if head.startswith("©") or "WWW ІАС" in head:
            break
        if section == "summary" and len(cells) == 3 and number(cells[1]) is not None:
            page.summary[head] = number(cells[1]) or 0
        elif section == "deputies" and len(cells) == 1 and head:
            nominator = head
        elif section == "deputies" and len(cells) == 5 and cells[2]:
            link = next((link for link in links if link.startswith("wm0240")), "")
            election = param(link, "pt001f01") if link else ""
            district = "" if cells[1] == "б/м" else cells[1]
            page.deputies.append(Deputy(cells[2], district, nominator, _day(cells[3]), election))
    # A page whose deputies all share one nominator prints no nominator rows.
    only = next(iter(page.summary)) if len(page.summary) == 1 else ""
    seen: Counter[tuple[str, str]] = Counter()
    for d in page.deputies:
        d.nominator = d.nominator or only
        seen[(d.district, d.name)] += 1
        d.seq = seen[(d.district, d.name)]
    return page


def of_2010(d: Deputy) -> bool:
    """Whether a district deputy won their seat in this election rather than at a later one."""
    if d.election and d.election != str(ELECTION):
        return False
    return d.recognized is None or d.recognized <= YEAR_END


@dataclass
class Head:
    rada: str
    tier: str
    region: str
    council: str
    raion: str
    page: str
    name: str = ""
    nominator: str = ""
    elected: date | None = None


def head_page(h: Head) -> None:
    section = ""
    for cells, _ in rows(h.page):
        if not cells:
            continue
        if cells[0].startswith(("Суб'єкт висування", "Суб’єкт висування")):
            section = "nominator"
            continue
        if cells[0].startswith("Прізвище"):
            section = "head"
            continue
        if section == "nominator" and len(cells) == 1 and cells[0]:
            h.nominator = cells[0]
            section = ""
        elif section == "head" and len(cells) >= 2 and cells[0]:
            h.name, h.elected = cells[0], _day(cells[1])
            return


@functools.cache
def heads() -> dict[str, Head]:
    """The heads the site names as elected in this election, by council; heads chosen at a later election are left
    out, since the site no longer names their predecessors."""
    found: dict[str, Head] = {}
    for url in head_lists():
        tier = HEAD_TIERS[param(url, "pid111" if "pid111" in url else "pid112")]
        region = param(url, "pid100")
        for cells, links in rows(url):
            link = next((link for link in links if link.startswith("wm02814")), None)
            if link is None or param(link, "pt001f01") != str(ELECTION):
                continue
            page = BASE + link
            if page not in raw.keys(SOURCE):
                continue
            council, _, raion = cells[0].partition(", ")
            h = Head(param(link, "pid102"), tier, region, council, raion, page)
            if not raion.endswith(" район"):
                h.raion = raion_of(region, _header(page))
            head_page(h)
            if h.name and (h.elected is None or h.elected <= YEAR_END):
                found[h.rada] = h
    return found


@functools.cache
def head_counts() -> tuple[int, int]:
    """The heads listed and those linked to a page of any election, from the lists of heads."""
    listed = linked = 0
    for url in head_lists():
        for cells, links in rows(url):
            if len(cells) == 3 and cells[2] and not cells[0].startswith("Назва"):
                listed += 1
                linked += any(link.startswith("wm02814") for link in links)
    return listed, linked


# ---------------------------------------------------------------------------------------------------------------------
# Places


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit(scheme: str, code: str) -> str:
    return key("unit", scheme, code)


@dataclass(frozen=True)
class Place:
    scheme: str
    code: str
    name: str
    parent: str | None  # area id

    @property
    def id(self) -> str:
        return area(self.scheme, self.code)


def stem(name: str) -> str:
    """The stem shared by a raion's name and its council's: «Барський район» and «Барська районна рада»."""
    word = name.removesuffix(" район").split(" районна рада")[0]
    return word[:-2] if word.endswith(("ий", "ій")) else word[:-1]


def raion_code(region: str, name: str) -> str:
    return f"{region}-{changeset.slug(ukrainian(stem(name)))}"


def region_area(region: str) -> str:
    return area(S_REGION, region)


def raion_area(region: str, raion: str) -> str:
    return area(S_RAION, raion_code(region, raion)) if raion else region_area(region)


def _prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.casefold(), b.casefold(), strict=False):
        if x != y:
            break
        n += 1
    return n


@functools.cache
def city_of(rada: str) -> str:
    """The area of the city a city-raion council lies in: «Ленінська районна у місті Харкові рада» lies in the
    territory of «Харківська міська рада», matched by the longest shared beginning of the city's name."""
    c = councils()[rada]
    found = re.search(r"районна у (?:місті )?(.+?) рада$", c.name)
    if found is None or c.region == SEVASTOPOL:
        return region_area(c.region)
    city = found.group(1)
    scored = sorted(
        ((_prefix(city, o.name), o.rada) for o in councils().values() if o.region == c.region and o.tier == "city"),
        reverse=True,
    )
    if not scored or scored[0][0] < 4 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return region_area(c.region)
    return area(S_RADA, scored[0][1])


@functools.cache
def places() -> dict[str, Place]:
    """Every area of 2010 the elections use, by area id: regions, raions, councils' territories, raions of cities."""
    found: dict[str, Place] = {}

    def add(place: Place) -> None:
        found.setdefault(place.id, place)

    for region, name in sorted(region_names().items(), key=lambda item: int(item[0])):
        add(Place(S_REGION, region, name, None))
    ordered = sorted(councils().values(), key=lambda c: int(c.rada))
    for c in ordered:
        if c.tier == "raion":
            add(Place(S_RAION, raion_code(c.region, c.name), stem(c.name) + "ий район", region_area(c.region)))
    for c in [*ordered, *sorted(heads().values(), key=lambda h: int(h.rada))]:
        if c.raion:
            add(Place(S_RAION, raion_code(c.region, c.raion), c.raion, region_area(c.region)))
    for c in ordered:
        if c.tier in RADA_TIERS:
            add(Place(S_RADA, c.rada, c.name, raion_area(c.region, c.raion)))
    for h in sorted(heads().values(), key=lambda h: int(h.rada)):
        if district_of(h) == area(S_RADA, h.rada):
            add(Place(S_RADA, h.rada, h.council, raion_area(h.region, h.raion)))
    for c in ordered:
        if c.tier == "city_district":
            add(Place(S_DISTRICT, c.rada, re.sub(r"\s+рада$", "", c.name), city_of(c.rada)))
    return found


def district_of(c: Council | Head) -> str:
    if c.region in (KYIV, SEVASTOPOL) and c.tier in ("special_city", "city"):
        return region_area(c.region)
    if c.tier in ("crimea", "oblast"):
        return region_area(c.region)
    if c.tier == "raion" and isinstance(c, Council):
        return raion_area(c.region, c.name)
    if c.tier == "city_district":
        return area(S_DISTRICT, c.rada)
    return area(S_RADA, c.rada)


# ---------------------------------------------------------------------------------------------------------------------
# Shapes


@dataclass(frozen=True)
class Shape:
    name: str
    region: str
    geometry: shapely.Geometry


def _features(url: str) -> list[dict[str, object]]:
    return json.loads(raw.cached(SOURCE, url).path.read_bytes())["features"]


def _polygons(geometry: shapely.Geometry) -> shapely.Geometry:
    valid = shapely.make_valid(geometry)
    parts = [
        part
        for piece in shapely.get_parts(valid)
        if shapely.get_type_id(piece) in (3, 6)
        for part in shapely.get_parts(piece)
    ]
    return shapely.set_srid(shapely.MultiPolygon(parts), 4326)


def _simplify(s: Shape) -> Shape:
    geometry = shapely.simplify(s.geometry, TOLERANCE, preserve_topology=True)
    return Shape(s.name, s.region, _polygons(geometry))


def _squash(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.casefold())


def _adjective(name: str) -> str:
    """The romanized own name of a council: «Antonivska» of «Антонівська сільська рада»."""
    word = re.sub(r"\s+(сільська|селищна|міська)\s+рада$", "", name)
    return _squash(ukrainian(re.sub(r"['’ʼ`]", "", word)))


# ISO 3166-2 codes that differ from the Commission's region codes.
ISO_REGIONS = {"43": CRIMEA, "40": SEVASTOPOL, "30": KYIV, "09": "44", "77": "73"}


@functools.cache
def region_shapes() -> dict[str, Shape]:
    found: dict[str, Shape] = {}
    for feature in _features(OBLAST_SHAPES):
        props = feature["properties"]
        assert isinstance(props, dict)
        iso = str(props["shapeISO"]).removeprefix("UA-")
        code = ISO_REGIONS.get(iso, str(int(iso)))
        found[code] = Shape(str(props["shapeName"]), code, _polygons(shapely.geometry.shape(feature["geometry"])))
    return found


@functools.cache
def shapes() -> tuple[dict[str, Shape], dict[str, Shape]]:
    """The outline of each council's territory of 2010 matched to geoBoundaries' village councils by region and
    romanized name, and of each raion whose councils all matched, as the union of their outlines; by area id."""
    regions = list(region_shapes().values())
    tree = shapely.STRtree([s.geometry for s in regions])

    def region_of(point: shapely.Geometry) -> str:
        hits = tree.query(point, predicate="within")
        return regions[int(hits[0])].region if len(hits) == 1 else ""

    villages: list[Shape] = []
    for feature in _features(COUNCIL_SHAPES):
        props = feature["properties"]
        assert isinstance(props, dict)
        geometry = _polygons(shapely.geometry.shape(feature["geometry"]))
        villages.append(Shape(str(props["shapeName"]), region_of(shapely.point_on_surface(geometry)), geometry))
    points = [shapely.point_on_surface(s.geometry) for s in villages]
    by_name: dict[tuple[str, str], list[int]] = {}
    for n, s in enumerate(villages):
        by_name.setdefault((s.region, _squash(s.name)), []).append(n)
    ours: dict[tuple[str, str], list[str]] = {}
    for id, place in places().items():
        if place.scheme == S_RADA:
            region = place.parent and _region_of_place(place)
            ours.setdefault((region or "", _adjective(place.name)), []).append(id)
    matched: dict[str, int] = {}
    for found_key, ids in ours.items():
        hits = by_name.get(found_key, [])
        if len(ids) == 1 and len(hits) == 1:
            matched[ids[0]] = hits[0]
    # Where councils of a region share a name, each takes the outline nearest to the hull of its raion's other
    # matched councils, among the raions of the region.
    members: dict[str, list[int]] = {}
    for id, n in matched.items():
        parent = places()[id].parent
        if parent is not None:
            members.setdefault(parent, []).append(n)
    hulls = {
        id: shapely.convex_hull(shapely.MultiPoint([points[n] for n in found]))
        for id, found in members.items()
        if id in places() and places()[id].scheme == S_RAION
    }
    by_region: dict[str, list[str]] = {}
    for id in hulls:
        by_region.setdefault(places()[id].code.split("-")[0], []).append(id)

    def nearest(n: int) -> str:
        found = by_region.get(villages[n].region, [])
        return min(found, key=lambda id: shapely.distance(hulls[id], points[n])) if found else ""

    used = set(matched.values())
    for found_key, ids in ours.items():
        if len(ids) == 1 and ids[0] in matched:
            continue
        hits = [n for n in by_name.get(found_key, []) if n not in used]
        home = {n: nearest(n) for n in hits}
        for id in ids:
            found = [n for n in hits if home[n] == places()[id].parent]
            rivals = [other for other in ids if places()[other].parent == places()[id].parent]
            if len(found) == 1 and len(rivals) == 1:
                matched[id] = found[0]
                used.add(found[0])
    councils_of: dict[str, list[str]] = {}
    for id, place in places().items():
        if place.scheme == S_RADA and place.parent is not None:
            councils_of.setdefault(place.parent, []).append(id)
    outlines = {id: _simplify(villages[n]) for id, n in matched.items()}
    raions: dict[str, Shape] = {}
    for id, place in places().items():
        inside = councils_of.get(id, [])
        if place.scheme != S_RAION or not inside or any(c not in matched for c in inside):
            continue
        union = shapely.union_all([villages[matched[c]].geometry for c in inside])
        raions[id] = _simplify(Shape(place.name, place.code.split("-")[0], _polygons(union)))
    return raions, outlines


def _region_of_place(place: Place) -> str:
    while place.parent is not None:
        parent = places()[place.parent]
        if parent.scheme == S_REGION:
            return parent.code
        place = parent
    return place.code


# ---------------------------------------------------------------------------------------------------------------------
# Packages

GEO = f"{SOURCE}/geo-2010"
REGISTRY = f"{SOURCE}/registry-2010"
SCHEMES = (
    (S_REGION, 1, "oblast, the Autonomous Republic of Crimea, or a city with special status (2010)"),
    (S_RAION, 2, "raion (2010)"),
    (S_RADA, 3, "territory of a village, settlement, or city council (2010)"),
    (S_DISTRICT, 4, "raion within a city (2010)"),
)
GB_SOURCE = key("source", "geoboundaries", "UKR-ADM3", "2010")


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Ukraine's regions, raions, territories of village, settlement, and city councils, and raions within cities "
        "as the Central Election Commission names them for the local elections of 2010",
    )
    p.external.add(UKRAINE)
    for code, rank, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=UKRAINE, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries: Ukraine, ADM3 (village councils) and ADM1 (regions), gbOpen release 6.0",
        url="https://www.geoboundaries.org/countryDownloads.html",
        license="CC BY-SA 2.0, from OpenStreetMap",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes=(
            "The regions (ADM1) and the territories of village, settlement, and city councils (ADM3) from "
            "OpenStreetMap, named in Latin script. A council of 2010 is matched by its region and its romanized own "
            "name, and where councils of a region share a name, by the hull of its raion's other matched councils. A "
            "raion's outline is the union of its councils' outlines, drawn only where every council of the raion "
            "matched. The shapes are simplified to about 20 meters."
        ),
    )
    p.artifact(raw.cached(SOURCE, COUNCIL_SHAPES), GB_SOURCE)
    p.artifact(raw.cached(SOURCE, OBLAST_SHAPES), GB_SOURCE)
    raion_shapes, council_shapes = shapes()
    outlines = {region_area(code): _simplify(s) for code, s in region_shapes().items()}
    outlines |= raion_shapes | council_shapes
    for scheme, _, _ in SCHEMES[:3]:
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2010"),
            scheme_code=scheme,
            vintage="2010",
            valid_from=DAY,
            valid_to=NEXT,
            as_of=DAY,
        )
    for id, place in sorted(places().items(), key=lambda item: (item[1].scheme, item[1].code)):
        p.add("area", id=id, scheme_code=place.scheme)
        p.add("area_code", area_id=id, code=place.code)
        if place.parent is not None:
            p.add("area_parent", area_id=id, parent_id=place.parent)
        p.name(id, place.name, "uk", "official")
        outline = outlines.get(id)
        if outline is not None:
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=DAY,
                valid_to=NEXT,
                geom=shapely.to_wkb(outline.geometry, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(outline.geometry), 4326), hex=True, include_srid=True
                ),
                quality="osm",
                source_id=GB_SOURCE,
            )
        u = unit(place.scheme, place.code)
        p.add(
            "reporting_unit",
            id=u,
            polity_id=UKRAINE,
            scheme_code=place.scheme,
            code=place.code,
            area_relation="equals",
            area_id=id,
            kind="aggregate",
        )
        p.name(u, place.name, "uk", "official")
    return p


BODIES = {
    "crimea": ("Verkhovna Rada of the Autonomous Republic of Crimea", "Верховна Рада Автономної Республіки Крим"),
    "oblast": ("Oblast council (2010)", "Обласна рада"),
    "raion": ("Raion council (2010)", "Районна рада"),
    "council": ("Village, settlement, or city council (2010)", "Сільська, селищна, міська рада"),
    "city_district": ("Council of a raion within a city (2010)", "Районна у місті рада"),
    "sevastopol": ("Sevastopol City Council", "Севастопольська міська рада"),
}
OFFICES = {"head": ("Head of a village, settlement, or city (2010)", "Сільський, селищний, міський голова")}
SYSTEMS = {
    "list": (
        {
            "formula": "hare",
            "threshold": {"level": "list", "percent": 3},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        "Closed lists for half of a council's seats (2010)",
        (
            "Under the Law of 10 July 2010, half of the seats of the Verkhovna Rada of Crimea and of oblast, raion, "
            "city, and city-raion councils are filled from the closed lists of parties' local organizations in one "
            "multi-member district covering the council's territory. The lists that win at least 3% of the valid "
            "votes share these seats by the electoral quota and the largest remainders; a seat left vacant passes to "
            "the next candidate of the same list."
        ),
    ),
    "districts": (
        {"formula": "fptp"},
        "Single-member districts (2010)",
        (
            "The other half of the seats of the mixed councils, and every seat of settlement and village councils, "
            "are filled in single-member districts, each by a plurality of the votes."
        ),
    ),
    "head": (
        {"formula": "fptp"},
        "Plurality for the head of a village, settlement, or city (2010)",
        "The head of a village, settlement, or city is elected in one round by a plurality of the votes.",
    ),
}


def body(kind: str) -> str:
    return key("body", kind, "2010")


def office(kind: str) -> str:
    return key("office", kind, "2010")


def system(kind: str) -> str:
    return key("electoral_system", kind, "2010")


def party(name: str) -> str | None:
    return None if not name or norm(name) == SELF else key("party", "vm2010", norm(name))


@functools.cache
def nominators() -> dict[str, str]:
    """Every nominator the pages print, by key; the most common spelling wins."""
    spellings: dict[str, Counter[str]] = {}

    def see(name: str) -> None:
        if name and norm(name) != SELF:
            spellings.setdefault(norm(name), Counter())[name] += 1

    for c in councils().values():
        for deputy in council_page(c.url).deputies:
            see(deputy.nominator)
    for h in heads().values():
        see(h.nominator)
    return {code: counts.most_common(1)[0][0] for code, counts in spellings.items()}


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The councils, heads, and electoral systems of Ukraine's local elections of 2010 and the parties that "
        "nominated the deputies and heads elected, from the Central Election Commission",
    )
    p.external.add(UKRAINE)
    p.add(
        "source",
        id=CVK,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="WWW ІАС «Місцеві вибори 2010»: the deputies and heads elected in the local elections of 31 October 2010",
        url=f"{BASE}wp0011.html",
        license="Free reuse with the source cited, as the results site states",
        license_url=f"{BASE}wp0011.html",
        attribution="Центральна виборча комісія",
        notes=(
            "The site states: «При повному або частковому використанні інформації, розміщеної на web-сайті, "
            "посилання на джерело інформації обов’язкове». It shows who holds each seat on the day it is read."
        ),
    )
    for kind, (english, native) in BODIES.items():
        p.add("body", id=body(kind), polity_id=UKRAINE, kind="council" if kind != "crimea" else "unicameral")
        p.name(body(kind), english, "en", "official")
        p.name(body(kind), native, "uk", "native", False)
    for kind, (english, native) in OFFICES.items():
        p.add("office", id=office(kind), polity_id=UKRAINE, kind="mayor")
        p.name(office(kind), english, "en", "official")
        p.name(office(kind), native, "uk", "native", False)
    for kind, (rules, english, method) in SYSTEMS.items():
        p.add(
            "electoral_system",
            id=system(kind),
            polity_id=UKRAINE,
            valid_from=LAW,
            valid_to=date(2015, 7, 14),
            rules=rules,
            rules_version=1,
        )
        p.name(system(kind), english, "en", "official")
        p.add(
            "note", id=key("note", system(kind)), entity_id=system(kind), kind="methodology", body_md=method, lang="en"
        )
    for code, name in sorted(nominators().items()):
        id = key("party", "vm2010", code)
        p.add("party", id=id, kind="party", polity_id=UKRAINE)
        p.name(id, name, "uk", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# The election

LIST_METHOD = (
    "From the council's page on the Commission's results site, which lists the deputies holding the council's seats on "
    "the day it is read, each with the nominator of their list or district. The seats each list won are the deputies "
    "the page lists without a district; a list seat whose deputy left office is shown held by the next candidate of "
    "the same list. The votes are not published on the site and are not stored."
)
DISTRICT_METHOD = (
    "From the council's page on the Commission's results site, which names the deputy each single-member district "
    "elected. Deputies whose powers ended early are not listed, and a district filled at a later by-election, whose "
    "deputy was recognized after 2010, is left out, so fewer deputies can be named than the districts' seats. The "
    "candidates' votes are not published on the site and are not stored."
)
HEAD_METHOD = (
    "The Commission's results site names only the head elected and their nominator. The votes of the candidates are "
    "not published on the site and are not stored."
)


def packages() -> list[str]:
    return [GEO, REGISTRY, f"{SOURCE}/{DAY}"]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def council_body(c: Council) -> str:
    if c.tier == "special_city":
        return body("sevastopol")
    if c.tier in ("crimea", "oblast", "raion", "city_district"):
        return body(c.tier)
    return body("council")


def _names(c: Council | Head, name: str) -> tuple[str, ...]:
    return (name, c.raion, region_names().get(c.region, ""), c.rada)


CITED: set[str] = set()


def cite(p: Package, url: str) -> None:
    artifact = raw.cached(SOURCE, url)
    if artifact.storage_key not in CITED:
        CITED.add(artifact.storage_key)
        p.artifact(artifact, CVK)


@dataclass
class Tally:
    councils: int = 0
    list_seats: int = 0
    district_seats: int = 0
    later: int = 0
    empty: list[str] = field(default_factory=list)


TALLY = Tally()


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Ukraine's regular local elections of 31 October 2010: the oblast, raion, city, city-raion, settlement, and "
        "village councils and Sevastopol's council with the seats each list won and the deputy each district elected, "
        "and the heads of villages, settlements, and cities, from the Central Election Commission",
        depends=[GEO, REGISTRY],
    )
    p.external.add(UKRAINE)
    CITED.clear()
    for url in [COUNCILS, HEADS, *council_lists(), *head_lists()]:
        cite(p, url)
    id = key("election", DAY)
    p.add("election", id=id, polity_id=UKRAINE, kind="local", date_from=DAY, date_to=DAY, status="held")
    path = f"ua/{DAY}-local"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, "2010 Ukrainian local elections", "en", "official")
    p.name(id, f"Чергові місцеві вибори {DAY:%d.%m.%Y}", "uk", "native", False)
    b = base.Build(p, id, path, source=CVK)
    for c in sorted(councils().values(), key=lambda c: (int(c.region), c.tier, c.raion, c.name, int(c.rada))):
        page = council_page(c.url)
        if c.region == KYIV or not page.deputies:
            TALLY.empty.append(f"{region_names().get(c.region, c.region)}: {c.name}")
            continue
        cite(p, c.url)
        TALLY.councils += 1
        if c.tier in MIXED:
            _list(b, c, page)
        _districts(b, c, page)
    p.accept(
        "outcomes.seats_magnitude",
        "the Commission's council pages show who holds each list seat on the day they are read: a list seat left "
        "vacant leaves its list one deputy short of the council's half of the seats, and three pages list one deputy "
        "more without a district than the list seats; the seats are stored as the deputies each page lists",
    )
    held = sum(_head(b, h) for h in sorted(heads().values(), key=lambda h: (int(h.region), h.raion, h.council)))
    print(f"{SOURCE}/{DAY}: {TALLY.councils} councils and {held} heads staged; {len(TALLY.empty)} councils empty")
    return p


def _list(b: base.Build, c: Council, page: Page) -> None:
    p = b.p
    magnitude = c.seats // 2
    tag = ("council", c.rada, DAY, "list")
    cid, rs = b.contest(
        tag,
        f"{c.name}: багатомандатний округ",
        _names(c, f"{c.name} списки"),
        completeness=0.0,
        kind="list_allocation",
        body_id=council_body(c),
        district_area_id=district_of(c),
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=system("list"),
    )
    b.note(rs, "methodology", LIST_METHOD)
    members: dict[str, list[Deputy]] = {}
    for d in page.deputies:
        if not d.district:
            members.setdefault(d.nominator, []).append(d)
    seats = sum(len(found) for found in members.values())
    TALLY.list_seats += seats
    if seats != magnitude:
        b.note(
            rs,
            "caveat",
            f"The page lists {seats} deputies elected from lists for the {magnitude} list seats of the council's "
            f"{c.seats}; the other list seats are vacant on the site.",
            "seats",
        )
    for position, (nominator, found) in enumerate(sorted(members.items(), key=lambda item: -len(item[1])), 1):
        option = key("option", *tag, norm(nominator))
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="list",
            party_id=party(nominator),
            ballot_position=position,
            ballot_label=nominator,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=len(found))
        for d in found:
            person = b.person(("deputy", DAY, c.rada, *d.tag), d.name)
            candidacy = key("candidacy", *tag, *d.tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                ballot_name=d.name,
                member_of_party_id=None,
                is_independent=False,
            )
            p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")


def _districts(b: base.Build, c: Council, page: Page) -> None:
    p = b.p
    magnitude = c.seats - c.seats // 2 if c.tier in MIXED else c.seats
    elected = [d for d in page.deputies if d.district and of_2010(d)]
    TALLY.later += sum(1 for d in page.deputies if d.district and not of_2010(d))
    TALLY.district_seats += len(elected)
    tag = ("council", c.rada, DAY, "districts")
    cid, rs = b.contest(
        tag,
        f"{c.name}: одномандатні округи" if c.tier in MIXED else c.name,
        _names(c, f"{c.name} округи" if c.tier in MIXED else c.name),
        completeness=0.0,
        kind="body_seats",
        body_id=council_body(c),
        district_area_id=district_of(c),
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=system("districts"),
    )
    b.note(rs, "methodology", DISTRICT_METHOD)
    if len(elected) != magnitude:
        b.note(
            rs,
            "caveat",
            f"The Commission names {len(elected)} deputies elected in this election to the {magnitude} district seats; "
            "the others left office early, were filled at a later election, or are vacant.",
            "seats",
        )
    ordered = sorted(elected, key=lambda d: (d.district.zfill(4), d.name))
    for position, d in enumerate(ordered, 1):
        person_tag = ("deputy", DAY, c.rada, *d.tag)
        option = key("option", *tag, *d.tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="candidate",
            party_id=party(d.nominator),
            ballot_position=position,
            ballot_label=f"{d.name} (округ № {d.district})",
        )
        person = b.person(person_tag, d.name)
        p.add(
            "candidacy",
            id=key("candidacy", *tag, *d.tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=d.name,
            member_of_party_id=None,
            is_independent=norm(d.nominator) == SELF,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="elected")


def _head(b: base.Build, h: Head) -> int:
    p = b.p
    cite(p, h.page)
    tag = ("head", h.rada, DAY)
    cid, rs = b.contest(
        tag,
        f"{h.council}: голова",
        _names(h, f"{h.council} голова"),
        completeness=0.0,
        kind="office",
        office_id=office("head"),
        district_area_id=district_of(h),
        magnitude=1,
        round_number=1,
        electoral_system_id=system("head"),
    )
    b.note(rs, "methodology", HEAD_METHOD)
    option = key("option", *tag, h.name)
    p.add(
        "contest_option",
        id=option,
        contest_id=cid,
        kind="candidate",
        party_id=party(h.nominator),
        ballot_position=1,
        ballot_label=h.name,
    )
    person = b.person(("head", DAY, h.rada, h.name), h.name)
    p.add(
        "candidacy",
        id=key("candidacy", *tag, h.name),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=h.name,
        member_of_party_id=None,
        is_independent=party(h.nominator) is None,
    )
    p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    return 1
