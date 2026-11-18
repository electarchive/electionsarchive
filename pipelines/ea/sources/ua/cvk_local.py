"""Ukraine: the regular local elections of 25 October 2020 from the Central Election Commission.

The Commission's results site for the local elections of 2020 (cvk.gov.ua/pls/vm2020, election 695) publishes:

- per council (pvm057): for a council elected by open lists (oblast, raion, and city-raion councils, and the
  councils of communities with 10,000 voters or more), the votes and seats of each party's list; for a council
  elected in multi-member districts (communities under 10,000 voters), the seats won by each nominator;
- per head of a city community (pvm048): the votes of every candidate, in the first round and, where one was held,
  the second round;
- for every head of a village or settlement community: only the head elected (pvm040);
- as open data (opendata_obr.xml): every sitting deputy and head with the district they were elected in and the
  votes cast for them.

The territorial commissions' protocols, with the counts of voters and ballots, are scanned PDF files and are not read
here, so no turnout or valid-vote totals are stored.
"""

import functools
import html
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import uuid5
from xml.etree import ElementTree

import numpy
import shapely

from ea import changeset, package, raw
from ea.latin import ukrainian
from ea.package import Package
from ea.sources.ua.cvk import clean, norm, number, text

SOURCE = "ua.cvk_local"
BASE = "https://www.cvk.gov.ua/pls/vm2020/"
ELECTION = 695
DAY = date(2020, 10, 25)
OPEN_DATA = f"{BASE}opendata_obr.xml"
COUNCILS = f"{BASE}pvm002pt001f01={ELECTION}pt00_t001f01={ELECTION}.html"
HEADS = f"{BASE}pvm004pt001f01={ELECTION}.html"
DEPARTED_HEADS = f"{BASE}pvm071pt001f01={ELECTION}pt00_t001f01={ELECTION}rej=0.html"
WORKERS = 4
SHAPES = (
    "https://data.humdata.org/dataset/d23f529f-31e4-4021-a65b-13987e5cfb42/resource/"
    "681beb86-391b-4a08-8140-ca52e80fcdce/download/ukr_admin_boundaries.geojson.zip"
)
TOLERANCE = 0.0002  # degrees, about 20 meters
PLAN = date(2020, 7, 17)  # Verkhovna Rada resolution No 807-IX forming the raions of the 2020 plan


def _links(url: str, pattern: str) -> list[str]:
    body = text(raw.cached(SOURCE, url))
    return [BASE + link for link in dict.fromkeys(re.findall(rf'href="({pattern}[^"]*)"', body))]


def _first_round(url: str) -> str:
    return re.sub(r"pvm048pt001f01=\d+", f"pvm048pt001f01={ELECTION}", url)


def fetch() -> None:
    raw.fetch(SOURCE, SHAPES, suffix=".zip")
    raw.fetch(SOURCE, OPEN_DATA, suffix=".xml")
    raw.fetch(SOURCE, COUNCILS, suffix=".html")
    raw.fetch(SOURCE, HEADS, suffix=".html")
    raw.fetch(SOURCE, DEPARTED_HEADS, suffix=".html")
    lists = _links(COUNCILS, r"pvm037") + _links(HEADS, r"pvm040")
    raw.fetch_many(SOURCE, lists, suffix=".html", workers=WORKERS)
    pages: list[str] = []
    for url in lists:
        if url not in raw.keys(SOURCE):
            continue
        for link in _links(url, r"pvm057") + _links(url, r"pvm048"):
            pages.append(link)
            if "pvm048" in link:
                pages.append(_first_round(link))
    raw.fetch_many(SOURCE, list(dict.fromkeys(pages)), suffix=".html", workers=WORKERS)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the pages


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UKRAINE = str(uuid5(package.NAMESPACE, "polity\x1fUA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fUKR"))
CVK = key("source", "cvk")
S_OBLAST, S_RAION, S_HROMADA, S_DISTRICT = "ua.oblast", "ua.raion", "ua.hromada", "ua.city_district"
KYIV = "80"
SELF = "самовисування"
VOTES = 1

# The council tiers by the site's code (pid112) and the head tiers (pid111).
TIERS = {"12": "oblast", "21": "raion", "30": "city", "41": "city_district", "51": "settlement", "61": "village"}
HEAD_TIERS = {"14": "city", "32": "city", "51": "settlement", "61": "village"}


def _cells(chunk: str) -> list[str]:
    parts = re.split(r"<t[dh]\b[^>]*>", chunk, flags=re.IGNORECASE)[1:]
    return [clean(html.unescape(re.sub(r"<[^>]+>", " ", part))) for part in parts]


def rows(url: str) -> list[tuple[list[str], list[str]]]:
    """Each table row of a page, as its cell texts and the links it holds."""
    body = text(raw.cached(SOURCE, url))
    found: list[tuple[list[str], list[str]]] = []
    for chunk in re.split(r"<tr\b[^>]*>", body, flags=re.IGNORECASE)[1:]:
        chunk = re.split(r"</table", chunk, flags=re.IGNORECASE)[0]
        found.append((_cells(chunk), re.findall(r'href="([^"]+)"', chunk)))
    return found


def param(link: str, name: str) -> str:
    found = re.search(rf"{name}=(\d+)", link)
    if found is None:
        raise ValueError(f"{link} has no {name}")
    return found.group(1)


def title(url: str) -> str:
    """The election a page reports, from its subtitle: «Місцеві вибори 25.10.2020», «Повторне голосування …»."""
    body = re.sub(r"<[^>]+>|&nbsp;", " ", text(raw.cached(SOURCE, url)))
    found = re.search(r"Результати виборів[^\d]*?\s([А-ЯІЇЄҐ][а-яіїєґ’' -]+?)\s+(\d\d\.\d\d\.\d{4})", body)
    if found is None:
        raise ValueError(f"{url} names no election")
    return f"{clean(found.group(1))} {found.group(2)}"


def day_of(label: str) -> date:
    return datetime.strptime(label.rsplit(" ", 1)[1], "%d.%m.%Y").date()


def lists(pattern: str) -> list[str]:
    root = COUNCILS if pattern == "pvm037" else HEADS
    return _links(root, pattern)


@dataclass
class Council:
    rada: str
    tier: str
    region: str
    label: str  # the prefix the list prints: the raion, the oblast, or «Україна»
    name: str
    seats: int
    url: str


@dataclass
class Head:
    rada: str
    tier: str
    region: str
    label: str
    community: str
    name: str
    nominator: str
    pages: list[str] = field(default_factory=list)


@functools.cache
def councils() -> dict[str, Council]:
    found: dict[str, Council] = {}
    for url in lists("pvm037"):
        tier = TIERS[param(url, "pid112")]
        region = param(url, "pid100")
        for cells, links in rows(url):
            link = next((link for link in links if link.startswith("pvm057")), None)
            if link is None or len(cells) < 3:
                continue
            label, _, name = cells[0].rpartition(", ")
            rada = param(link, "pid102")
            found[rada] = Council(rada, tier, region, label, name, int(cells[1]), BASE + link)
    return found


@functools.cache
def heads() -> dict[str, Head]:
    by_name = {(c.region, c.label, c.name): c.rada for c in councils().values()}
    xml = open_data()
    found: dict[str, Head] = {}
    for url in lists("pvm040"):
        tier = HEAD_TIERS[param(url, "pid111")]
        region = param(url, "pid100")
        for cells, links in rows(url):
            if len(cells) < 3 or cells[0].startswith("Найменування"):
                continue
            label, _, community = cells[0].rpartition(", ")
            pages = [BASE + link for link in links if link.startswith("pvm048")]
            council = re.sub(r"громада$", "рада", community)
            rada = param(pages[0], "pid102") if pages else by_name.get((region, label, council))
            if rada is None:
                rada = xml.by_name.get((region, council))
            if rada is None:
                raise ValueError(f"no council for the head of {cells[0]} in region {region}")
            found[rada] = Head(rada, tier, region, label, community, cells[1], cells[2], pages)
    return found


@dataclass
class Deputy:
    name: str
    born: str
    district: str
    nominator: str


@dataclass
class OpenData:
    regions: dict[str, str]
    radas: dict[str, tuple[str, str]]  # rada id → region, name
    by_name: dict[tuple[str, str], str]
    deputies: dict[str, list[Deputy]]
    heads: dict[str, tuple[str, str]]  # rada id → head's name, the process that elected them


REGULAR = f"Місцеві вибори {DAY:%d.%m.%Y}"


@functools.cache
def open_data() -> OpenData:
    root = ElementTree.parse(raw.cached(SOURCE, OPEN_DATA).path).getroot()
    regions: dict[str, str] = {}
    radas: dict[str, tuple[str, str]] = {}
    by_name: dict[tuple[str, str], str] = {}
    names: Counter[tuple[str, str]] = Counter()
    deputies: dict[str, list[Deputy]] = {}
    elected: dict[str, tuple[str, str]] = {}
    for region in root.iter("region"):
        code = region.attrib["id"]
        regions[code] = clean(region.attrib["name"])
        for rada in region.iter("rada"):
            id, name = rada.attrib["id"], clean(rada.attrib["name"])
            radas[id] = (code, name)
            by_name[(code, name)] = id
            names[(code, name)] += 1
            for deputy in rada.iter("deputy"):
                process = clean(deputy.findtext("PROCESS") or "")
                appointed = clean(deputy.findtext("DATE_OF_APPOINTMENT") or "")
                if process != REGULAR or not appointed.endswith(str(DAY.year)):
                    continue
                deputies.setdefault(id, []).append(
                    Deputy(
                        clean(deputy.findtext("PIB") or ""),
                        clean(deputy.findtext("DN") or ""),
                        clean(deputy.findtext("DISTRICT") or ""),
                        clean(deputy.findtext("NOMINATED") or ""),
                    )
                )
            head = rada.find("head")
            if head is not None and head.findtext("PIB"):
                elected[id] = (clean(head.findtext("PIB") or ""), clean(head.findtext("PROCESS") or ""))
    for twice in [name for name, n in names.items() if n > 1]:
        del by_name[twice]
    return OpenData(regions, radas, by_name, deputies, elected)


@functools.cache
def departed_heads() -> dict[str, str]:
    """The heads elected in 2020 whose powers have since ended or who never took office, by council."""
    data = open_data()
    region_of = {name: code for code, name in data.regions.items()}
    found: dict[str, str] = {}
    for cells, _ in rows(DEPARTED_HEADS):
        if len(cells) < 6 or not re.fullmatch(r"\d\d\.\d\d\.\d{4}", cells[5]) or not cells[5].endswith(str(DAY.year)):
            continue
        region, _, council = cells[0].partition(", ")
        rada = data.by_name.get((region_of.get(region, ""), council))
        if rada is None:
            raise ValueError(f"no council for the departed head of {cells[0]}")
        found[rada] = cells[1]
    return found


def elected_head(rada: str) -> str | None:
    """The head elected on 25 October 2020 or in its second rounds, whether or not still in office."""
    name, process = open_data().heads.get(rada, ("", ""))
    if process == REGULAR or (process.startswith("Повторне голосування") and process.endswith(str(DAY.year))):
        return name
    return departed_heads().get(rada)


@dataclass
class Party:
    name: str
    votes: int | None
    seats: int


def council_results(c: Council) -> tuple[bool, list[Party]]:
    """Whether the council was elected by lists, and its parties' votes and seats (seats alone in a district council)."""
    found: list[Party] = []
    by_list = False
    header: list[str] = []
    for cells, _ in rows(c.url):
        if cells and cells[0].startswith(("Назва політичної партії", "Висування")):
            header = cells
            if found:
                break
            by_list = len(cells) > 1 and cells[1].startswith("К-сть голосів")
            continue
        if not header or len(cells) != 3 or number(cells[1]) is None:
            if found:
                break
            continue
        if by_list:
            found.append(Party(cells[0], number(cells[1]), number(cells[2]) or 0))
        else:
            found.append(Party(cells[0], None, number(cells[1]) or 0))
    return by_list, found


@dataclass
class Candidate:
    name: str
    nominator: str
    votes: int


def head_results(url: str) -> list[Candidate]:
    found: list[Candidate] = []
    for cells, _ in rows(url):
        if len(cells) == 4 and number(cells[2]) is not None:
            found.append(Candidate(cells[0], cells[1], number(cells[2]) or 0))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Places

# The cities whose raions have councils, by the label the council list prints: the city's own council.
CITY_COUNCILS = {"м.Кривий Ріг": "4381", "м.Полтава": "11480", "м.Херсон": "3996", "м.Кропивницький": "4833"}


# Raions renamed since 2020: the stem the community labels print, and the stem of the raion council's current name.
RENAMED = {
    "Володимир-Волинськ": "Володимирськ",
    "Новомосковськ": "Самарівськ",
    "Новоград-Волинськ": "Звягельськ",
    "Сєвєродонецьк": "Сіверськодонецьк",
    "Червоноградськ": "Шептицьк",
    "Красноградськ": "Берестинськ",
}
# Communities the boundary dataset names otherwise: the name the Commission prints, and the dataset's.
SHAPE_NAMES = {"Новоград-Волинська": "Звягельська", "Війтівецька": "Жданівська"}


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit(scheme: str, code: str) -> str:
    return key("unit", scheme, code)


def stem(name: str) -> str:
    """The stem shared by a raion's name and its council's: «Вінницький р-н» and «Вінницька районна рада»."""
    word = name.removesuffix(" р-н").removesuffix(" район").split(" районна рада")[0]
    word = word[:-2] if word.endswith(("ий", "ій")) else word[:-1]
    return RENAMED.get(word, word)


def raion_code(region: str, name: str) -> str:
    return f"{region}-{changeset.slug(ukrainian(stem(name)))}"


@dataclass(frozen=True)
class Place:
    scheme: str
    code: str
    name: str
    parent: str  # area id
    old: str = ""  # the name the Commission printed in 2020, where the area was renamed since

    @property
    def id(self) -> str:
        return area(self.scheme, self.code)


def oblast_name(region: str) -> str:
    name = open_data().regions[region]
    return "Київ" if region == KYIV else name


@functools.cache
def places() -> dict[str, Place]:
    """Every area the elections use, by area id: oblasts and Kyiv, raions, communities, and raions of cities."""
    found: dict[str, Place] = {}

    def add(place: Place) -> str:
        found.setdefault(place.id, place)
        return place.id

    def oblast(region: str) -> str:
        return add(Place(S_OBLAST, region, oblast_name(region), COUNTRY))

    def raion(region: str, label: str) -> str:
        if not label.endswith(" р-н"):
            return oblast(region)
        id = area(S_RAION, raion_code(region, label))
        if id in found:
            return id
        word = label.removesuffix(" р-н")
        name = stem(label) + ("ій" if word.endswith("ій") else "ий") + " район"
        old = f"{word} район" if f"{word} район" != name else ""
        return add(Place(S_RAION, raion_code(region, label), name, oblast(region), old))

    for c in sorted(councils().values(), key=lambda c: int(c.rada)):
        if c.tier == "oblast" or c.region == KYIV:
            oblast(c.region)
        elif c.tier == "raion":
            name = stem(c.name) + "ий район"
            add(Place(S_RAION, raion_code(c.region, c.name), name, oblast(c.region)))
    for c in sorted(councils().values(), key=lambda c: int(c.rada)):
        if c.tier in ("city", "settlement", "village") and c.region != KYIV:
            name = re.sub(r"рада$", "громада", c.name)
            add(Place(S_HROMADA, c.rada, name, raion(c.region, c.label)))
    for h in sorted(heads().values(), key=lambda h: int(h.rada)):
        if h.region != KYIV:
            add(Place(S_HROMADA, h.rada, h.community, raion(h.region, h.label)))
    for c in sorted(councils().values(), key=lambda c: int(c.rada)):
        if c.tier == "city_district":
            name = re.sub(r"\s+рада$", "", c.name)
            add(Place(S_DISTRICT, c.rada, name, area(S_HROMADA, CITY_COUNCILS[c.label])))
    return found


def district_of(c: Council | Head) -> str:
    if c.region == KYIV or (isinstance(c, Council) and c.tier == "oblast"):
        return area(S_OBLAST, c.region)
    if isinstance(c, Council) and c.tier == "raion":
        return area(S_RAION, raion_code(c.region, c.name))
    if isinstance(c, Council) and c.tier == "city_district":
        return area(S_DISTRICT, c.rada)
    return area(S_HROMADA, c.rada)


def unit_of(c: Council | Head) -> str:
    place = places()[district_of(c)]
    return unit(place.scheme, place.code)


@dataclass(frozen=True)
class Shape:
    pcode: str
    name: str
    old: str
    parent: str
    geometry: shapely.Geometry


def _level(level: int) -> list[Shape]:
    with zipfile.ZipFile(raw.cached(SOURCE, SHAPES).path) as archive:
        features = json.loads(archive.read(f"ukr_admin{level}.geojson"))["features"]
    outlines = [shapely.make_valid(shapely.geometry.shape(f["geometry"])) for f in features]
    simple = shapely.coverage_simplify(numpy.array(outlines), TOLERANCE)
    found: list[Shape] = []
    for feature, outline in zip(features, simple, strict=True):
        props = feature["properties"]
        polygons = [
            part
            for geometry in shapely.get_parts(outline)
            if shapely.get_type_id(geometry) in (3, 6)
            for part in shapely.get_parts(geometry)
        ]
        found.append(
            Shape(
                str(props[f"adm{level}_pcode"]),
                clean(str(props[f"adm{level}_name1"] or "")),
                clean(str(props.get(f"adm{level}_name1_old") or "")),
                str(props[f"adm{level - 1}_pcode"]),
                shapely.set_srid(shapely.MultiPolygon(polygons), 4326),
            )
        )
    return found


def _word(name: str) -> str:
    """A community's own name without its kind: «Агрономічна» of «Агрономічна сільська громада»."""
    return norm(re.sub(r"\s+(сільська|селищна|міська)\s+(громада|рада)$", "", name)).replace("'", "")


@functools.cache
def shapes() -> dict[str, Shape]:
    """The outline of each oblast, raion, and community, by area id, matched to the dataset by code and name."""
    regions = {shape.pcode: shape for shape in _level(1)}
    raions = _level(2)
    communities = _level(3)
    found: dict[str, Shape] = {}
    raion_of: dict[str, str] = {}
    aliases = {_word(name): _word(other) for name, other in SHAPE_NAMES.items()}
    for id, place in places().items():
        if place.scheme == S_OBLAST:
            found[id] = regions[f"UA{int(place.code):02d}"]
    for id, place in places().items():
        if place.scheme != S_RAION:
            continue
        region = place.code.split("-")[0]
        hits = [
            shape
            for shape in raions
            if shape.parent == f"UA{int(region):02d}"
            and stem(shape.name + " р-н") == stem(place.name.removesuffix(" район") + " р-н")
        ]
        if len(hits) == 1:
            found[id] = hits[0]
            raion_of[id] = hits[0].pcode
    for id, place in places().items():
        if place.scheme != S_HROMADA:
            continue
        parent = places().get(place.parent)
        within = (
            raion_of.get(place.parent, "\0")
            if parent is not None and parent.scheme == S_RAION
            else f"UA{int(parent.code if parent else 0):02d}"
        )
        hits = [
            shape
            for shape in communities
            if shape.pcode.startswith(within)
            and aliases.get(_word(place.name), _word(place.name)) in {_word(shape.name), _word(shape.old)}
        ]
        if len(hits) == 1:
            found[id] = hits[0]
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Ukraine's oblasts, the raions of the 2020 plan, the territorial communities, and the raions within cities, as "
        "the Central Election Commission names them for the local elections of 2020",
    )
    p.external |= {UKRAINE, COUNTRY}
    for code, rank, noun in (
        (S_OBLAST, 1, "oblast or city with special status"),
        (S_RAION, 2, "raion"),
        (S_HROMADA, 3, "territorial community"),
        (S_DISTRICT, 4, "raion within a city"),
    ):
        p.add("scheme", code=code, polity_id=UKRAINE, kind="administrative", rank=rank, noun=noun, code_system=None)
    cod = key("source", "cod-ab")
    p.add(
        "source",
        id=cod,
        kind="official_body",
        publisher='State Scientific Production Enterprise "Kartographia", through OCHA',
        title="Ukraine - Subnational Administrative Boundaries (COD-AB), version 05",
        url="https://data.humdata.org/dataset/cod-ab-ukr",
        license="CC BY-IGO 3.0",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution='State Scientific Production Enterprise "Kartographia"; OCHA',
        notes=(
            "Level 1 holds the oblasts and Kyiv, level 2 the raions of the 2020 plan, and level 3 the territorial "
            "communities, valid for use from 2025-09-01. The raions and communities match the Commission's 2020 "
            "names by oblast, raion, and the community's own name; six raions renamed since 2020 and two "
            "communities the dataset names otherwise are matched through curated lists. The shapes are simplified "
            "to about 20 meters as one coverage per level, so neighbors still share their edges."
        ),
    )
    p.artifact(raw.cached(SOURCE, SHAPES), cod)
    outlines = shapes()
    for scheme in (S_OBLAST, S_RAION, S_HROMADA):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2020"),
            scheme_code=scheme,
            vintage="2020",
            valid_from=PLAN,
            valid_to=None,
            as_of=date(2025, 9, 1),
        )
    for id, place in sorted(places().items(), key=lambda item: (item[1].scheme, item[1].code)):
        p.add("area", id=id, scheme_code=place.scheme)
        p.add("area_code", area_id=id, code=place.code)
        p.add("area_parent", area_id=id, parent_id=place.parent)
        p.name(id, place.name, "uk", "official")
        if place.old:
            p.name(id, place.old, "uk", "historical", False)
        outline = outlines.get(id)
        if outline is not None:
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=PLAN,
                valid_to=None,
                geom=shapely.to_wkb(outline.geometry, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(outline.geometry), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=cod,
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


# ---------------------------------------------------------------------------------------------------------------------
# Bodies, offices, systems, and parties

BODIES = {
    "oblast": ("Oblast council", "Обласна рада"),
    "raion": ("Raion council", "Районна рада"),
    "hromada": ("Village, settlement, or city council", "Сільська, селищна, міська рада"),
    "city_district": ("Council of a raion within a city", "Районна у місті рада"),
    "kyiv": ("Kyiv City Council", "Київська міська рада"),
}
OFFICES = {
    "hromada": ("Head of a village, settlement, or city community", "Сільський, селищний, міський голова"),
    "kyiv": ("Mayor of Kyiv", "Київський міський голова"),
}
SYSTEMS = {
    "list": (
        {
            "formula": "hare",
            "threshold": {"level": "list", "percent": 5},
            "lists": {"order": "open", "vacancy": "next_on_list"},
        },
        "Open lists in each council",
        (
            "Oblast, raion, and city-raion councils, Kyiv's council, and the councils of communities with 10,000 voters or "
            "more are elected by open lists in a single multi-member district (Electoral Code arts. 189-190 and 260-261). "
            "The lists that win at least 5% of the votes cast for all lists share the seats by the electoral quota and the "
            "largest remainders. The first candidate of each list that wins seats is elected first; the list's other "
            "seats go to the candidates who reach a quarter of the quota by the order of their votes, then by their place "
            "on the list."
        ),
    ),
    "districts": (
        {"formula": "sntv"},
        "Multi-member districts in small communities",
        (
            "The councils of communities with fewer than 10,000 voters are elected in multi-member districts of two to "
            "four seats. Each voter votes for one candidate, and the candidates with the most votes win the district's "
            "seats."
        ),
    ),
    "plurality": (
        {"formula": "fptp"},
        "Plurality for the head of a community",
        (
            "The head of a village, settlement, or city community with fewer than 75,000 voters is elected by a plurality "
            "of the votes."
        ),
    ),
    "two_round": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        "Two rounds for the head of a large city",
        (
            "The head of a city community with 75,000 voters or more, and Kyiv's mayor, is elected by more than half of the "
            "votes of the voters who took part; otherwise the two leading candidates go to a second round three weeks "
            "later."
        ),
    ),
}


def body(kind: str) -> str:
    return key("body", kind)


def office(kind: str) -> str:
    return key("office", kind)


def system(kind: str) -> str:
    return key("electoral_system", kind)


def party(name: str) -> str | None:
    return None if norm(name) == SELF else key("party", norm(name))


@functools.cache
def nominators() -> dict[str, str]:
    """Every nominating party the pages and the open data print, by key; the most common spelling wins."""
    spellings: dict[str, Counter[str]] = {}

    def see(name: str) -> None:
        if name and norm(name) != SELF:
            spellings.setdefault(norm(name), Counter())[name] += 1

    for c in councils().values():
        for row in council_results(c)[1]:
            see(row.name)
        for deputy in open_data().deputies.get(c.rada, []):
            see(deputy.nominator)
    for h in heads().values():
        see(h.nominator)
        for url in h.pages:
            for candidate in head_results(url):
                see(candidate.nominator)
    return {code: counts.most_common(1)[0][0] for code, counts in spellings.items()}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Ukraine's local councils and community heads, their electoral systems, and the parties that nominated "
        "candidates in the local elections of 2020, from the Central Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(UKRAINE)
    p.add(
        "source",
        id=CVK,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="WWW ІАС «Місцеві вибори 2020»: results of the regular local elections of 25 October 2020",
        url=f"{BASE}wm001.html",
        license="Free reuse with the source cited, as the results site states",
        license_url=f"{BASE}wm001.html",
        attribution="Центральна виборча комісія",
        notes="The site states: «При повному або частковому використанні інформації посилання на джерело обов’язкове».",
    )
    for kind, (english, native) in BODIES.items():
        p.add("body", id=body(kind), polity_id=UKRAINE, kind="council")
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
            valid_from=date(2020, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.name(system(kind), english, "en", "official")
        p.add(
            "note", id=key("note", system(kind)), entity_id=system(kind), kind="methodology", body_md=method, lang="en"
        )
    for code, name in sorted(nominators().items()):
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=UKRAINE)
        p.add("external_id", entity_id=id, scheme="ua.cvk.party", value=code)
        p.name(id, name, "uk", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Elections

COUNCIL_METHOD = (
    "From the council's page on the Commission's results site, which prints the votes cast for each party's list and "
    "the seats each list won, as the territorial election commission established them. The counts of voters and "
    "ballots are in the commission's protocol, a scanned file, and are not stored, so the lists' votes are not summed "
    "against a valid total. The deputies stored as elected are those the Commission's open data lists as elected on "
    "25 October 2020 and registered that year; deputies whose powers have ended since are not listed there."
)
DISTRICT_COUNCIL_METHOD = (
    "From the council's page on the Commission's results site and its open data, which name the deputies elected in "
    "each multi-member district. The votes of the candidates are in the territorial election commission's protocol, "
    "a scanned file, and are not stored. The deputies stored as elected are those the open data lists as elected on "
    "25 October 2020 and registered that year; deputies whose powers have ended since are not listed there, so the "
    "council's seats can exceed the deputies named."
)
HEAD_METHOD = (
    "From the Commission's page of the results of the election of the head, which prints the votes of every "
    "candidate. The counts of voters and ballots are in the territorial election commission's protocol, a scanned "
    "file, and are not stored."
)
SMALL_HEAD_METHOD = (
    "The Commission's results site names only the head elected in a village or settlement community. The votes of "
    "all the candidates are in the territorial election commission's protocol, a scanned file, and are not stored."
)


def runoff_days() -> list[date]:
    found: set[date] = set()
    for h in heads().values():
        for url in h.pages:
            label = title(url)
            if label.startswith("Повторне голосування") and day_of(label).year == DAY.year:
                found.add(day_of(label))
    return sorted(found)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}", *(f"{SOURCE}/{day}" for day in runoff_days())]


def parse() -> None:
    geo().write()
    registry().write()
    first().write()
    for day in runoff_days():
        runoff(day).write()


def emit() -> None:
    package.run("load", packages())


@dataclass
class Build:
    p: Package
    election: str
    base: str
    slugs: set[str] = field(default_factory=set)

    def slug(self, *names: str) -> str:
        for n in range(1, len(names) + 1):
            path = f"{self.base}/{changeset.slug(ukrainian(' '.join(names[:n])))}"
            if path not in self.slugs:
                self.slugs.add(path)
                return path
        raise ValueError(f"no unique slug for {names}")

    def contest(
        self, tag: tuple[object, ...], name: str, names: tuple[str, ...], completeness: float, **columns: object
    ) -> tuple[str, str]:
        p = self.p
        cid = key("contest", *tag)
        p.add("contest", id=cid, election_id=self.election, default_result_kind="counted", **columns)
        p.name(cid, name, "uk", "official")
        p.add("slug", path=self.slug(*names), entity_id=cid, is_current=True)
        rs = key("result_set", *tag)
        p.add(
            "result_set",
            id=rs,
            election_id=self.election,
            contest_id=cid,
            stage="official",
            kind="counted",
            source_id=CVK,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        return cid, rs

    def note(self, entity: str, kind: str, body_md: str, *tag: object) -> None:
        self.p.add("note", id=key("note", entity, kind, *tag), entity_id=entity, kind=kind, body_md=body_md, lang="en")

    def value(self, rs: str, u: str, option: str, value: int) -> None:
        self.p.add(
            "result_value",
            result_set_id=rs,
            unit_id=u,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=0,
            value=value,
        )

    def person(self, tag: tuple[object, ...], name: str, born: str = "") -> str:
        id = key("person", *tag)
        birth = datetime.strptime(born, "%d.%m.%Y").date() if re.fullmatch(r"\d\d\.\d\d\.\d{4}", born) else None
        self.p.add("person", id=id, birth_date=birth, birth_precision="day" if birth else None)
        self.p.name(id, name, "uk", "official")
        return id


def _election(p: Package, day: date, kind: str) -> Build:
    id = key("election", day)
    p.add("election", id=id, polity_id=UKRAINE, kind=kind, date_from=day, date_to=day, status="held")
    base = f"ua/{day}-local" + ("-runoff" if kind == "runoff" else "")
    p.add("slug", path=base, entity_id=id, is_current=True)
    if kind == "runoff":
        p.name(id, f"Ukrainian local elections of 2020, second round of {day:%-d %B %Y}", "en", "official")
        p.name(id, f"Повторне голосування на місцевих виборах {day:%d.%m.%Y}", "uk", "native", False)
    else:
        p.name(id, "2020 Ukrainian local elections", "en", "official")
        p.name(id, f"Чергові місцеві вибори {day:%d.%m.%Y}", "uk", "native", False)
    return Build(p, id, base)


def council_body(c: Council) -> str:
    if c.region == KYIV:
        return body("kyiv")
    return body({"oblast": "oblast", "raion": "raion", "city_district": "city_district"}.get(c.tier, "hromada"))


def head_contest(h: Head) -> tuple[object, ...]:
    return ("head", h.rada, DAY)


def first() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Ukraine's regular local elections of 25 October 2020: the oblast, raion, city-raion, and community councils "
        "with each list's votes and seats, and the heads of communities and Kyiv's mayor, from the Central Election "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(UKRAINE)
    for url in [OPEN_DATA, COUNCILS, HEADS, DEPARTED_HEADS, *lists("pvm037"), *lists("pvm040")]:
        p.artifact(raw.cached(SOURCE, url), CVK)
    b = _election(p, DAY, "local")
    known = set(nominators())
    data = open_data()
    unmatched = 0
    for c in sorted(councils().values(), key=lambda c: (int(c.region), c.tier, c.label, c.name, int(c.rada))):
        p.artifact(raw.cached(SOURCE, c.url), CVK)
        by_list, parties = council_results(c)
        place = places()[district_of(c)]
        region = oblast_name(c.region)
        tag = ("council", c.rada, DAY)
        cid, rs = b.contest(
            tag,
            c.name,
            (c.name, place.name if c.tier == "raion" else c.label, region, c.rada),
            completeness=1.0 if by_list else 0.0,
            kind="body_seats" if not by_list else "list_allocation",
            body_id=council_body(c),
            district_area_id=place.id,
            magnitude=c.seats,
            round_number=1,
            electoral_system_id=system("list" if by_list else "districts"),
        )
        u = unit_of(c)
        deputies = data.deputies.get(c.rada, [])
        if by_list:
            b.note(rs, "methodology", COUNCIL_METHOD)
            options: dict[str, str] = {}
            total = sum(row.seats for row in parties)
            if total != c.seats:
                b.note(
                    rs,
                    "caveat",
                    f"The lists won {total} of the council's {c.seats} seats; the Commission's page leaves the "
                    f"other {c.seats - total} unfilled.",
                    "seats",
                )
            for position, row in enumerate(parties, 1):
                option = options[norm(row.name)] = key("option", *tag, norm(row.name))
                pid = party(row.name)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=cid,
                    kind="list",
                    party_id=pid if pid is not None and norm(row.name) in known else None,
                    ballot_position=position,
                    ballot_label=row.name,
                )
                if row.votes is not None:
                    b.value(rs, u, option, row.votes)
                p.add(
                    "outcome", result_set_id=rs, option_id=option, kind="won" if row.seats else "lost", seats=row.seats
                )
            for deputy in deputies:
                option = options.get(norm(deputy.nominator))
                if option is None:
                    unmatched += 1
                    continue
                person_tag = ("deputy", c.rada, deputy.name, deputy.born)
                person = b.person(person_tag, deputy.name, deputy.born)
                candidacy = key("candidacy", *tag, *person_tag)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=deputy.name,
                    member_of_party_id=None,
                    is_independent=False,
                )
                p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
        else:
            b.note(rs, "methodology", DISTRICT_COUNCIL_METHOD)
            for position, deputy in enumerate(sorted(deputies, key=lambda d: (d.district.zfill(4), d.name)), 1):
                person_tag = ("deputy", c.rada, deputy.name, deputy.born)
                option = key("option", *tag, *person_tag)
                pid = party(deputy.nominator)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=cid,
                    kind="candidate",
                    party_id=pid if pid is not None and norm(deputy.nominator) in known else None,
                    ballot_position=position,
                    ballot_label=deputy.name,
                )
                person = b.person(person_tag, deputy.name, deputy.born)
                p.add(
                    "candidacy",
                    id=key("candidacy", *tag, *person_tag),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=deputy.name,
                    member_of_party_id=None,
                    is_independent=pid is None,
                )
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    p.accept(
        "outcomes.list_members",
        "the Commission's open data names only the deputies still in office, so a list whose elected deputies "
        "later resigned, died, or never took their seats names fewer deputies than the seats it won; the seats are "
        "stored as the council's page prints them",
    )
    if unmatched:
        raise ValueError(f"{unmatched} list deputies name a nominator without a list on their council's page")
    held = sum(
        _head(b, h, known)
        for h in sorted(heads().values(), key=lambda h: (int(h.region), h.label, h.community, int(h.rada)))
    )
    print(f"{SOURCE}/{DAY}: {held} of {len(heads())} heads' elections staged", flush=True)
    return p


def _pages(h: Head) -> tuple[str | None, dict[date, str]]:
    """The head's first-round page and the pages of its second rounds held in 2020, by day."""
    rounds: dict[date, str] = {}
    first_page: str | None = None
    for url in h.pages:
        for candidate in (url, _first_round(url)):
            try:
                label = title(candidate)
            except LookupError:
                continue
            if label == REGULAR:
                first_page = candidate
            elif label.startswith("Повторне голосування") and day_of(label).year == DAY.year:
                rounds[day_of(label)] = candidate
    return first_page, rounds


def _keys(candidates: list[Candidate]) -> list[str]:
    """A key for each candidate of a contest: the name, with a count for a namesake."""
    seen: Counter[str] = Counter()
    found: list[str] = []
    for row in candidates:
        seen[row.name] += 1
        found.append(row.name if seen[row.name] == 1 else f"{row.name}#{seen[row.name]}")
    return found


def _options(
    b: Build,
    cid: str,
    rs: str,
    tag: tuple[object, ...],
    h: Head,
    candidates: list[Candidate],
    known: set[str],
    counted: bool,
) -> dict[str, str]:
    p = b.p
    u = unit_of(h)
    options: dict[str, str] = {}
    for position, (who, row) in enumerate(zip(_keys(candidates), candidates, strict=True), 1):
        option = options[who] = key("option", *tag, who)
        pid = party(row.nominator)
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="candidate",
            party_id=pid if pid is not None and norm(row.nominator) in known else None,
            ballot_position=position,
            ballot_label=row.name,
        )
        person = key("person", "head", h.rada, who)
        if tag[-1] == DAY:
            b.person(("head", h.rada, who), row.name)
        p.add(
            "candidacy",
            id=key("candidacy", *tag, who),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=row.name,
            member_of_party_id=None,
            is_independent=pid is None,
        )
        if counted:
            b.value(rs, u, option, row.votes)
    return options


def _head(b: Build, h: Head, known: set[str]) -> int:
    """Stage the first round of a head's election; 0 when the site holds no result of it."""
    p = b.p
    elected = elected_head(h.rada)
    first_page, rounds = _pages(h)
    if first_page is None and elected is None:
        return 0
    place = places()[district_of(h)]
    tag = head_contest(h)
    kyiv = h.region == KYIV
    cid, rs = b.contest(
        tag,
        f"{h.community}: голова" if not kyiv else "Київський міський голова",
        (h.community, h.label, oblast_name(h.region), h.rada) if not kyiv else ("Київський міський голова",),
        completeness=1.0 if first_page else 0.0,
        kind="office",
        office_id=office("kyiv" if kyiv else "hromada"),
        district_area_id=place.id,
        magnitude=1,
        round_number=1,
        electoral_system_id=system("two_round" if kyiv or rounds else "plurality"),
    )
    candidates = head_results(first_page) if first_page else [Candidate(elected or "", h.nominator, 0)]
    if first_page:
        p.artifact(raw.cached(SOURCE, first_page), CVK)
        b.note(rs, "methodology", HEAD_METHOD)
    else:
        b.note(rs, "methodology", SMALL_HEAD_METHOD)
    options = _options(b, cid, rs, tag, h, candidates, known, first_page is not None)
    if rounds:
        advancing = {row.name for row in head_results(rounds[min(rounds)])}
        for who, row in zip(_keys(candidates), candidates, strict=True):
            kind = "advanced" if row.name in advancing else "eliminated"
            p.add("outcome", result_set_id=rs, option_id=options[who], kind=kind)
    elif elected is not None:
        if elected not in options:
            raise ValueError(f"{h.community}: the head elected, {elected}, is not among the candidates")
        for who in options:
            p.add("outcome", result_set_id=rs, option_id=options[who], kind="elected" if who == elected else "lost")
    else:
        later = open_data().heads.get(h.rada, ("", ""))[1]
        b.note(
            rs,
            "caveat",
            f"The Commission names no head elected in this election; its open data names a head elected at «{later}»."
            if later
            else "The Commission names no head elected in this election.",
            "later",
        )
    return 1


def runoff(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The second round of {day:%-d %B %Y} of the elections of heads of Ukrainian city communities, from the "
        "Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"],
    )
    p.external.add(UKRAINE)
    b = _election(p, day, "runoff")
    known = set(nominators())
    for h in sorted(heads().values(), key=lambda h: (int(h.region), h.label, h.community, int(h.rada))):
        rounds = _pages(h)[1]
        page = rounds.get(day)
        if page is None:
            continue
        p.artifact(raw.cached(SOURCE, page), CVK)
        tag = ("head", h.rada, day)
        place = places()[district_of(h)]
        cid, rs = b.contest(
            tag,
            f"{h.community}: голова, повторне голосування",
            (h.community, h.label, oblast_name(h.region), h.rada),
            completeness=1.0,
            kind="office",
            office_id=office("hromada"),
            district_area_id=place.id,
            magnitude=1,
            round_number=2,
            previous_round_id=key("contest", *head_contest(h)),
            electoral_system_id=system("two_round"),
        )
        b.note(rs, "methodology", HEAD_METHOD)
        candidates = head_results(page)
        options = _options(b, cid, rs, tag, h, candidates, known, True)
        elected = elected_head(h.rada)
        if elected is None or day != max(rounds):
            b.note(rs, "caveat", "The Commission names no head elected in this round.", "later")
            continue
        if elected not in options:
            raise ValueError(f"{h.community}: the head elected, {elected}, is not in the second round")
        for who in options:
            p.add("outcome", result_set_id=rs, option_id=options[who], kind="elected" if who == elected else "lost")
    return p
