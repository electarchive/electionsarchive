"""Ukraine: the regular local elections of 25 October 2015 and the runoffs of 15 November 2015.

Part of the ua.cvk_local pipeline; its module calls fetch() and the builders here.

The Commission's results site for the local elections of 2015 (cvk.gov.ua/pls/vm2015, election 100, and election 102
for the second round) publishes:

- per council (pvm057): for a council elected by party lists in territorial districts (oblast, raion, city, and
  city-raion councils), the votes and seats of each party's local organization and the deputies elected; for a
  village or settlement council elected in single-member districts, the deputy each district elected;
- per head of a city (pvm048): the votes of every candidate, in the first round and, where one was held, the second;
- for every head of a village or settlement: only the head elected (pvm038);
- the deputies (pvm070) and heads (pvm071) elected in 2015 whose powers ended early or who never took office,
  which is how the councils merged into amalgamated communities since 2015 keep their results, since their own pages
  now list no one.

The territorial commissions' protocols, with the counts of voters and ballots, are not published on the site, so no
turnout or valid-vote totals are stored.
"""

import functools
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date

import shapely

from ea import changeset, raw
from ea.latin import ukrainian
from ea.package import Package
from ea.sources.ua import cvk_local as base
from ea.sources.ua.cvk import clean, norm, number, text

SOURCE = base.SOURCE
BASE = "https://www.cvk.gov.ua/pls/vm2015/"
ELECTION = 100
RUNOFF = 102
DAY = date(2015, 10, 25)
RUNOFF_DAY = date(2015, 11, 15)
COUNCILS = f"{BASE}pvm002pt001f01={ELECTION}pt00_t001f01={ELECTION}.html"
HEADS = f"{BASE}pvm003pt001f01={ELECTION}pt00_t001f01={ELECTION}.html"
RUNOFF_HEADS = f"{BASE}pvm003pt001f01={RUNOFF}pt00_t001f01={ELECTION}.html"
DEPARTED_DEPUTIES = f"{BASE}pvm070pt001f01={ELECTION}pt00_t001f01={ELECTION}rej=0pxto=2.html"
DEPARTED_HEADS = f"{BASE}pvm071pt001f01={ELECTION}pt00_t001f01={ELECTION}rej=0.html"
DEPARTED_RUNOFF_HEADS = f"{BASE}pvm071pt001f01={RUNOFF}pt00_t001f01={ELECTION}rej=0.html"
WORKERS = 6
GB = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/UKR/"
COUNCIL_SHAPES = f"{GB}ADM3/geoBoundaries-UKR-ADM3.geojson"
OBLAST_SHAPES = f"{GB}ADM1/geoBoundaries-UKR-ADM1.geojson"


def _links(url: str, pattern: str) -> list[str]:
    body = text(raw.cached(SOURCE, url))
    return [BASE + link for link in dict.fromkeys(re.findall(rf'href="({pattern}[^"]*)"', body))]


def runoff_page(url: str) -> str:
    return re.sub(r"pvm048pt001f01=\d+", f"pvm048pt001f01={RUNOFF}", url)


def fetch() -> None:
    for url in (COUNCILS, HEADS, RUNOFF_HEADS, DEPARTED_DEPUTIES, DEPARTED_HEADS, DEPARTED_RUNOFF_HEADS):
        raw.fetch(SOURCE, url, suffix=".html")
    for url in (OBLAST_SHAPES, COUNCIL_SHAPES):
        raw.fetch(SOURCE, url, suffix=".geojson")
    lists = _links(COUNCILS, "pvm037") + _links(HEADS, "pvm038") + _links(RUNOFF_HEADS, "pvm038")
    raw.fetch_many(SOURCE, lists, suffix=".html", workers=WORKERS)
    pages: list[str] = []
    for url in lists:
        if url not in raw.keys(SOURCE):
            continue
        pages += _links(url, "pvm05[78]") + _links(url, "pvm048")
    raw.fetch_many(SOURCE, list(dict.fromkeys(pages)), suffix=".html", workers=WORKERS)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the pages

key = base.key
param = base.param
rows = base.rows
UKRAINE = base.UKRAINE
KYIV = base.KYIV
SELF = base.SELF
CVK = key("source", "cvk", "vm2015")
S_RAION = "ua.raion_2015"
S_RADA = "ua.rada_2015"
S_DISTRICT = "ua.city_raion_2015"
# The council tiers by the site's code (pid112) and the head tiers (pid111).
TIERS = {"12": "oblast", "21": "raion", "30": "city", "33": "town", "41": "city_district", "51": "settlement"}
TIERS |= {"61": "village"}
HEAD_TIERS = {"14": "city", "32": "city", "33": "town", "51": "settlement", "61": "village"}
RADA_TIERS = ("city", "town", "settlement", "village")
BORN = re.compile(r"народи(?:вся|лася)\s+(\d\d\.\d\d\.\d{4})")
STATUS_RUNOFF = "Призначено повторне голосування"


def born(info: str) -> str:
    found = BORN.search(info)
    return found.group(1) if found else ""


def match_key(text: str) -> str:
    """A council's label and name reduced for matching across the site's lists: «Барський р-н, Антонівська сільська
    рада» and «Барський район Антонівська сільська рада» give the same key."""
    text = re.sub(r"\bр-н\b", "район", text)
    text = re.sub(r"^[^,]*\bобл\.,?\s*", "", text)
    return re.sub(r"\W", "", text.replace("’", "'")).casefold()


@dataclass
class Council:
    rada: str
    tier: str
    region: str
    label: str  # the raion («Барський р-н»), the city («м.Ірпінь»), or the oblast the list prints before the name
    name: str
    seats: int
    url: str


@functools.cache
def councils() -> dict[str, Council]:
    found: dict[str, Council] = {}
    for url in _links(COUNCILS, "pvm037"):
        tier = TIERS[param(url, "pid112")]
        region = param(url, "pid100")
        for cells, links in rows(url):
            link = next((link for link in links if link.startswith(("pvm057", "pvm058"))), None)
            if link is None or len(cells) < 2:
                continue
            label, _, name = cells[0].rpartition(", ")
            rada = param(link, "pid102")
            found[rada] = Council(rada, tier, region, label, name, int(cells[1]), BASE + link)
    labels = {rada: label for rada, label in head_labels().items() if label.endswith(" р-н")}
    for c in found.values():
        if c.tier == "town" and not c.label and c.rada in labels:
            c.label = labels[c.rada]
    return found


@functools.cache
def head_labels() -> dict[str, str]:
    """The label the heads' lists print for each city with a results page, by council."""
    found: dict[str, str] = {}
    for url in _links(HEADS, "pvm038"):
        for cells, links in rows(url):
            pages = [link for link in links if link.startswith("pvm048")]
            if pages and cells:
                found[param(pages[0], "pid102")] = cells[0].rpartition(", ")[0]
    return found


@dataclass
class Party:
    name: str
    votes: int
    seats: int


@dataclass
class Deputy:
    name: str
    born: str
    district: str
    nominator: str
    votes: int | None = None
    departed: bool = False


@dataclass
class Page:
    by_list: bool
    parties: list[Party]
    deputies: list[Deputy]


def council_page(url: str) -> Page:
    parties: list[Party] = []
    deputies: list[Deputy] = []
    section = ""
    nominator = ""
    by_list = False
    for cells, _ in rows(url):
        if not cells:
            continue
        head = cells[0]
        if head.startswith(
            ("Назва політичної партії, місцева організація якої висунула кандидатів", "Найменування партії")
        ):
            if len(cells) > 1 and cells[1].startswith("К-сть голосів"):
                section, by_list = "votes", True
            else:
                section = "summary"
            continue
        if head.startswith("Висування"):
            section = "summary"
            continue
        if head.startswith("№ ТВО"):
            section = "list"
            continue
        if head.startswith("№ ОВО"):
            section = "districts"
            continue
        if head.startswith("©") or "WWW ІАС" in head:
            break
        if section == "votes" and len(cells) == 4 and number(cells[1]) is not None:
            parties.append(Party(head, number(cells[1]) or 0, number(cells[2]) or 0))
        elif section == "list" and len(cells) == 1 and head:
            nominator = head
        elif section == "list" and len(cells) == 5 and cells[1]:
            deputies.append(Deputy(cells[1], born(cells[2]), head, nominator, number(cells[3])))
        elif section == "districts" and len(cells) == 4 and cells[1]:
            deputies.append(Deputy(cells[1], born(cells[3]), head, cells[2]))
    if "pvm058" in url:
        # This page of a council elected later lists every candidate of each list, not the deputies elected.
        deputies = []
    return Page(by_list, parties, deputies)


def _regions() -> dict[str, str]:
    """The oblast's code by the name the site prints: «Вінницька область» → 5, «м.Київ» → 80."""
    found: dict[str, str] = {}
    for url in _links(COUNCILS, "pvm037"):
        if param(url, "pid100") in found.values():
            continue
        body = re.sub(r"<[^>]+>", " ", text(raw.cached(SOURCE, url)))
        name = re.search(r"&nbsp;\s*((?:[А-ЯІЇЄҐ][А-ЯІЇЄҐа-яіїєґ'’-]+ область)|м\.Київ)", body)
        if name is not None:
            found[clean(name.group(1))] = param(url, "pid100")
    return found


@functools.cache
def regions() -> dict[str, str]:
    return _regions()


@functools.cache
def departed_deputies() -> dict[str, list[Deputy]]:
    """The deputies elected in 2015 whose powers ended early or who never took them up, by council."""
    by_key: dict[tuple[str, str], list[str]] = {}
    by_name: dict[tuple[str, str], list[str]] = {}
    for c in councils().values():
        by_key.setdefault((c.region, match_key(f"{c.label} {c.name}")), []).append(c.rada)
        by_name.setdefault((c.region, match_key(c.name)), []).append(c.rada)
    found: dict[str, list[Deputy]] = {}
    region = ""
    council: str | None = None
    lost = ""
    unmatched: Counter[str] = Counter()
    for cells, _ in rows(DEPARTED_DEPUTIES):
        if len(cells) == 1 and cells[0] in regions():
            region = regions()[cells[0]]
            continue
        if len(cells) != 6 or cells[1].startswith("Прізвище") or not cells[1]:
            continue
        if cells[0] not in ('-"-', ""):
            label = re.sub(r"^м\s+", "м.", cells[0])
            own = re.sub(r"^.*?(\bрайон|\bм\.\S+)\s+", "", label)
            hits = by_key.get((region, match_key(label)), []) or by_name.get((region, match_key(own)), [])
            if len(hits) > 1:
                # A raion renamed since 2015 leaves its councils listed under the oblast alone.
                hits = [rada for rada in hits if councils()[rada].label.endswith("обл.")]
            council = hits[0] if len(hits) == 1 else None
            lost = f"{region} {cells[0]}"
        if council is None:
            unmatched[lost] += 1
            continue
        found.setdefault(council, []).append(Deputy(cells[1], "", "", cells[5], departed=True))
    if unmatched:
        print(
            f"{SOURCE}: {sum(unmatched.values())} departed deputies of {len(unmatched)} councils unmatched: "
            f"{list(unmatched)[:20]}",
            flush=True,
        )
    return found


@dataclass
class Head:
    rada: str
    tier: str
    region: str
    label: str
    council: str
    name: str = ""
    born: str = ""
    nominator: str = ""
    status: str = ""
    page: str | None = None  # the first round's results page
    runoff: str | None = None  # the second round's results page
    runoff_name: str = ""


def _head_rows(url: str) -> list[tuple[list[str], str | None]]:
    found: list[tuple[list[str], str | None]] = []
    for cells, links in rows(url):
        if len(cells) < 2 or not cells[0] or cells[0].startswith("Найменування") or "WWW ІАС" in cells[0]:
            continue
        page = next((BASE + link for link in links if link.startswith("pvm048")), None)
        found.append((cells, page))
    return found


@functools.cache
def listed_heads() -> dict[str, Head]:
    """The heads the lists of the elected print, with the second rounds' pages, by council."""
    by_label = {(c.region, match_key(f"{c.label} {c.name}")): c.rada for c in councils().values()}
    by_name: dict[tuple[str, str], list[str]] = {}
    for c in councils().values():
        if c.tier in RADA_TIERS and not c.label:
            by_name.setdefault((c.region, match_key(c.name)), []).append(c.rada)
    found: dict[str, Head] = {}
    lost: list[str] = []
    for url in _links(HEADS, "pvm038"):
        tier = HEAD_TIERS[param(url, "pid111")]
        region = param(url, "pid100")
        for cells, page in _head_rows(url):
            label, _, council = cells[0].rpartition(", ")
            if page is not None:
                rada = param(page, "pid102")
            else:
                rada = by_label.get((region, match_key(cells[0])))
            if rada is None:
                hits = by_name.get((region, match_key(council)), [])
                rada = hits[0] if len(hits) == 1 else None
            if rada is None or rada not in councils():
                lost.append(f"{region} {cells[0]}")
                continue
            h = Head(rada, tier, region, label, council, page=page)
            if len(cells) >= 4 and cells[1]:
                h.name, h.nominator, h.born = cells[1], cells[2], born(cells[3])
            else:
                h.status = cells[1]
            found[rada] = h
    if lost:
        print(f"{SOURCE}: {len(lost)} heads have no council in the councils' lists: {lost[:30]}", flush=True)
    for url in _links(RUNOFF_HEADS, "pvm038"):
        for cells, page in _head_rows(url):
            if page is None:
                continue
            h = found[param(page, "pid102")]
            h.runoff = page
            if len(cells) >= 4 and cells[1]:
                h.runoff_name = cells[1]
    return found


def heads_named(rada: str) -> bool:
    h = listed_heads().get(rada)
    return h is not None and bool(h.name)


@functools.cache
def heads() -> dict[str, Head]:
    """Every head's election, with the heads who have since left office filled in from the Commission's list."""
    found = {rada: replace(h) for rada, h in listed_heads().items()}
    for rada, (name, nominator, birth) in departed_heads(ELECTION).items():
        h = found.get(rada)
        if h is not None and not h.name and not h.status.startswith((STATUS_RUNOFF, "Призначені повторні вибори")):
            h.name, h.nominator, h.born = name, nominator, birth
    for rada, (name, _, _) in departed_heads(RUNOFF).items():
        h = found.get(rada)
        if h is not None and h.runoff and not h.runoff_name:
            h.runoff_name = name
    return found


@functools.cache
def departed_heads(election: int) -> dict[str, tuple[str, str, str]]:
    """The heads elected in 2015 whose powers ended early or who never took them up, by council, matched by the
    oblast and the council's name, and by the raion of the head's residence where two councils share a name."""
    url = DEPARTED_HEADS if election == ELECTION else DEPARTED_RUNOFF_HEADS
    by_name: dict[tuple[str, str], list[Council]] = {}
    for c in councils().values():
        if c.tier in RADA_TIERS or c.region == KYIV and c.tier == "city":
            by_name.setdefault((c.region, match_key(c.name)), []).append(c)
    found: dict[str, tuple[str, str, str]] = {}
    unmatched = 0
    for cells, _ in rows(url):
        if len(cells) != 8 or cells[0].startswith("Найменування"):
            continue
        region_name, _, council = cells[0].partition(", ")
        region = regions().get(region_name)
        hits = by_name.get((region or "", match_key(council)), [])
        if len(hits) > 1:
            residence = match_key(cells[3])
            near = [c for c in hits if c.label.endswith(" р-н") and match_key(c.label) in residence]
            hits = near or [c for c in hits if c.label.endswith("обл.") and not heads_named(c.rada)]
        if len(hits) != 1:
            unmatched += 1
            continue
        found[hits[0].rada] = (cells[1], cells[4], cells[2])
    if unmatched:
        print(f"{SOURCE}: {unmatched} departed heads of election {election} unmatched", flush=True)
    return found


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

area = base.area
unit = base.unit
Place = base.Place

# The councils of raions within cities, by the city their name gives in the locative: the city council's name.
CITY_OF_DISTRICT = {
    "Дніпропетровську": "Дніпровська міська рада",
    "Дніпрі": "Дніпровська міська рада",
    "Кривому Розі": "Криворізька міська рада",
    "Полтаві": "Полтавська міська рада",
    "Херсоні": "Херсонська міська рада",
    "Кіровограді": "Кіровоградська міська рада",
    "Кропивницькому": "Кіровоградська міська рада",
    "Маріуполі": "Маріупольська міська рада",
    "Горлівці": "Горлівська міська рада",
    "Макіївці": "Макіївська міська рада",
    "Житомирі": "Житомирська міська рада",
    "Чернігові": "Чернігівська міська рада",
}


def stem(name: str) -> str:
    """The stem shared by a raion's name and its council's: «Барський р-н» and «Барська районна рада»."""
    word = name.removesuffix(" р-н").removesuffix(" район").split(" районна рада")[0]
    return word[:-2] if word.endswith(("ий", "ій")) else word[:-1]


def raion_code(region: str, name: str) -> str:
    return f"{region}-{changeset.slug(ukrainian(stem(name)))}"


def raion_name(name: str) -> str:
    word = name.removesuffix(" р-н")
    if name.endswith(" р-н"):
        return f"{word} район"
    return stem(name) + "ий район"


def oblast(region: str) -> str:
    return area(base.S_OBLAST, region)


def parent_of(c: Council) -> str:
    if c.label.endswith(" р-н"):
        return area(S_RAION, raion_code(c.region, c.label))
    return oblast(c.region)


def city_of(c: Council) -> Council:
    found = re.search(r"у місті (.+?) рада$", c.name)
    city = CITY_OF_DISTRICT.get(found.group(1) if found else "", "")
    hits = [o for o in councils().values() if o.region == c.region and o.tier == "city" and o.name == city]
    if len(hits) != 1:
        raise ValueError(f"no city council for {c.name}")
    return hits[0]


@functools.cache
def places() -> dict[str, Place]:
    """Every area of 2015 the elections use, by area id: raions, councils' territories, and raions of cities."""
    found: dict[str, Place] = {}

    def add(place: Place) -> None:
        found.setdefault(place.id, place)

    ordered = sorted(councils().values(), key=lambda c: int(c.rada))
    for c in ordered:
        if c.tier == "raion":
            add(Place(S_RAION, raion_code(c.region, c.name), raion_name(c.name), oblast(c.region)))
    for c in ordered:
        if c.label.endswith(" р-н"):
            add(Place(S_RAION, raion_code(c.region, c.label), raion_name(c.label), oblast(c.region)))
    for c in ordered:
        if c.tier in RADA_TIERS and c.region != KYIV:
            add(Place(S_RADA, c.rada, c.name, parent_of(c)))
    for c in ordered:
        if c.tier == "city_district":
            name = re.sub(r"\s+рада$", "", c.name)
            add(Place(S_DISTRICT, c.rada, name, area(S_RADA, city_of(c).rada)))
    return found


def district_of(c: Council | Head) -> str:
    tier = c.tier
    if c.region == KYIV or tier == "oblast":
        return oblast(c.region)
    if tier == "raion" and isinstance(c, Council):
        return area(S_RAION, raion_code(c.region, c.name))
    if tier == "city_district":
        return area(S_DISTRICT, c.rada)
    return area(S_RADA, c.rada)


def unit_of(c: Council | Head) -> str:
    id = district_of(c)
    if id == oblast(c.region):
        return unit(base.S_OBLAST, c.region)
    place = places()[id]
    return unit(place.scheme, place.code)


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


def _squash(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.casefold())


def _adjective(name: str) -> str:
    """The romanized own name of a council: «Antonivska» of «Антонівська сільська рада»."""
    word = re.sub(r"\s+(сільська|селищна|міська)\s+рада$", "", name)
    return _squash(ukrainian(re.sub(r"['’ʼ`]", "", word)))


@functools.cache
def shapes() -> tuple[dict[str, Shape], dict[str, Shape]]:
    """The outline of each council's territory of 2015 matched to geoBoundaries' village councils by oblast and
    romanized name, and of each raion whose councils all matched, as the union of their outlines; by area id."""
    oblasts = []
    for feature in _features(OBLAST_SHAPES):
        props = feature["properties"]
        assert isinstance(props, dict)
        code = str(int(str(props["shapeISO"]).removeprefix("UA-")))
        oblasts.append((code, shapely.geometry.shape(feature["geometry"])))
    tree = shapely.STRtree([g for _, g in oblasts])

    def region_of(point: shapely.Geometry) -> str:
        hits = tree.query(point, predicate="within")
        return oblasts[int(hits[0])][0] if len(hits) == 1 else ""

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
            c = councils()[place.code]
            ours.setdefault((c.region, _adjective(c.name)), []).append(id)
    matched: dict[str, int] = {}
    for found_key, ids in ours.items():
        hits = by_name.get(found_key, [])
        if len(ids) == 1 and len(hits) == 1:
            matched[ids[0]] = hits[0]
    # Where councils of an oblast share a name, each takes the outline nearest to the hull of its raion's other
    # matched councils, among the raions of the oblast.
    members: dict[str, list[int]] = {}
    for id, n in matched.items():
        members.setdefault(places()[id].parent, []).append(n)
    hulls = {
        id: shapely.convex_hull(shapely.MultiPoint([points[n] for n in found]))
        for id, found in members.items()
        if places().get(id) is not None and places()[id].scheme == S_RAION
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
        if place.scheme == S_RADA:
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


def _simplify(s: Shape) -> Shape:
    geometry = shapely.simplify(s.geometry, TOLERANCE, preserve_topology=True)
    return Shape(s.name, s.region, _polygons(geometry))


TOLERANCE = base.TOLERANCE


# ---------------------------------------------------------------------------------------------------------------------
# Packages

GEO = f"{SOURCE}/geo-2015"
REGISTRY = f"{SOURCE}/registry-2015"
SCHEMES = (
    (S_RAION, 2, "raion (before the 2020 reform)"),
    (S_RADA, 3, "territory of a village, settlement, or city council (before the 2020 reform)"),
    (S_DISTRICT, 4, "raion within a city (before the 2020 reform)"),
)
GB_COUNCILS = key("source", "geoboundaries", "UKR-ADM3")


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Ukraine's raions, the territories of village, settlement, and city councils, and the raions within cities "
        "as the Central Election Commission names them for the local elections of 2015, before the 2020 reform",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(UKRAINE)
    for code, rank, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=UKRAINE, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add(
        "source",
        id=GB_COUNCILS,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries: Ukraine, ADM3 (village councils) and ADM1 (oblasts), gbOpen release 6.0",
        url="https://www.geoboundaries.org/countryDownloads.html",
        license="CC BY-SA 2.0, from OpenStreetMap",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes=(
            "The territories of village, settlement, and city councils from OpenStreetMap, named in Latin script, and "
            "the oblasts (ADM1) used to place them. A council of 2015 is matched by its oblast and its romanized own "
            "name, and where councils of an oblast share a name, by the hull of its raion's other matched councils. "
            "A raion's outline is the union of its councils' outlines, drawn only where every council of the raion "
            "matched. The shapes are simplified to about 20 meters."
        ),
    )
    p.artifact(raw.cached(SOURCE, COUNCIL_SHAPES), GB_COUNCILS)
    p.artifact(raw.cached(SOURCE, OBLAST_SHAPES), GB_COUNCILS)
    raion_shapes, council_shapes = shapes()
    for scheme, _, _ in SCHEMES[:2]:
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2015"),
            scheme_code=scheme,
            vintage="2015",
            valid_from=DAY,
            valid_to=base.PLAN,
            as_of=DAY,
        )
    for id, place in sorted(places().items(), key=lambda item: (item[1].scheme, item[1].code)):
        p.add("area", id=id, scheme_code=place.scheme)
        p.add("area_code", area_id=id, code=place.code)
        p.add("area_parent", area_id=id, parent_id=place.parent)
        p.name(id, place.name, "uk", "official")
        outline = (raion_shapes if place.scheme == S_RAION else council_shapes).get(id)
        if outline is not None:
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=DAY,
                valid_to=base.PLAN,
                geom=shapely.to_wkb(outline.geometry, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(outline.geometry), 4326), hex=True, include_srid=True
                ),
                quality="osm",
                source_id=GB_COUNCILS,
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


SYSTEMS = {
    "list": (
        {
            "formula": "hare",
            "threshold": {"level": "list", "percent": 5},
            "lists": {"order": "share", "vacancy": "next_on_list"},
        },
        "Party lists in territorial districts (2015)",
        (
            "Under the Law on Local Elections of 2015, oblast, raion, city, and city-raion councils are elected from "
            "the lists of parties' local organizations; each candidate but the first stands in one territorial "
            "district. The lists that win at least 5% of the votes cast for all lists share the seats by the electoral "
            "quota and the largest remainders. The first candidate of each list that wins seats is elected first; the "
            "list's other seats go to its candidates by their share of the votes in their territorial districts."
        ),
    ),
    "districts": (
        {"formula": "fptp"},
        "Single-member districts in village and settlement councils (2015)",
        (
            "Village and settlement councils are elected in single-member districts; the candidate with the most votes "
            "in each district wins its seat."
        ),
    ),
    "plurality": (
        {"formula": "fptp"},
        "Plurality for the head of a village, settlement, or small city (2015)",
        (
            "The head of a village, settlement, or city with fewer than 90,000 voters is elected by a plurality of the "
            "votes."
        ),
    ),
    "two_round": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        "Two rounds for the head of a large city (2015)",
        (
            "The head of a city with 90,000 voters or more, and Kyiv's mayor, is elected by more than half of the "
            "votes of the voters who took part; otherwise the two leading candidates go to a second round on the third "
            "Sunday after the election."
        ),
    ),
}


def system(kind: str) -> str:
    return key("electoral_system", kind, "2015")


def _spellings() -> dict[str, str]:
    """Every nominator the pages print, by key; the most common spelling wins."""
    spellings: dict[str, Counter[str]] = {}

    def see(name: str) -> None:
        if name and norm(name) != SELF:
            spellings.setdefault(norm(name), Counter())[name] += 1

    for c in councils().values():
        page = council_page(c.url)
        for row in page.parties:
            see(row.name)
        for deputy in page.deputies + departed_deputies().get(c.rada, []):
            see(deputy.nominator)
    for h in heads().values():
        see(h.nominator)
        for url in (h.page, h.runoff):
            for candidate in head_results(url) if url and url in raw.keys(SOURCE) else []:
                see(candidate.nominator)
    return {code: counts.most_common(1)[0][0] for code, counts in spellings.items()}


@functools.cache
def nominators() -> dict[str, str]:
    return _spellings()


loose = base.loose


@functools.cache
def local() -> dict[str, str]:
    """The parties the 2020 local registry adds itself, by loose code, with the ids that registry gave them."""
    return {loose(code): base.key("party", code) for code in base.nominators() if base.earlier(code) is None}


def existing(code: str) -> str | None:
    """The id of the party an already-landed registry declares under ua.cvk.party, spelled as this code or alike."""
    return base.earlier(code) or local().get(loose(code))


def party_ids(codes: set[str]) -> set[str]:
    """The ids of the parliamentary registry's parties among the codes, for the package to list as external."""
    return base.earlier_ids(codes)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The electoral systems of Ukraine's local elections of 2015 and the parties that nominated candidates in them "
        "that the 2020 registry lacks, from the Central Election Commission",
        depends=[f"{SOURCE}/registry", "ua.cvk/registry"],
    )
    p.external.add(UKRAINE)
    p.add(
        "source",
        id=CVK,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="WWW ІАС «Місцеві вибори 2015»: results of the regular local elections of 25 October 2015",
        url=f"{BASE}wm001pt001f01={ELECTION}.html",
        license="Free reuse with the source cited, as the results site states",
        license_url=f"{BASE}wm001pt001f01={ELECTION}.html",
        attribution="Центральна виборча комісія",
        notes="The site states: «При повному або частковому використанні інформації посилання на джерело обов’язкове».",
    )
    for kind, (rules, english, method) in SYSTEMS.items():
        p.add(
            "electoral_system",
            id=system(kind),
            polity_id=UKRAINE,
            valid_from=date(2015, 7, 14),
            valid_to=date(2020, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.name(system(kind), english, "en", "official")
        p.add(
            "note", id=key("note", system(kind)), entity_id=system(kind), kind="methodology", body_md=method, lang="en"
        )
    for code, name in sorted(nominators().items()):
        if existing(code) is not None:
            continue
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=UKRAINE)
        p.add("external_id", entity_id=id, scheme="ua.cvk.party", value=code)
        p.name(id, name, "uk", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Elections

LIST_METHOD = (
    "From the council's page on the Commission's results site, which prints the votes cast for each party's local "
    "organization and the seats each list won, as the territorial election commission established them. The counts "
    "of voters and ballots are not published on the site, so the lists' votes are not summed against a valid total. "
    "The deputies stored as elected are those the council's page lists as elected on 25 October 2015, with those the "
    "Commission lists as having left office early or never taken it up; the page can list deputies who took a seat "
    "later as the next candidates of their list, so a list can name more deputies than the seats it won."
)
DISTRICT_METHOD = (
    "From the council's page on the Commission's results site, which names the deputy each single-member district "
    "elected, and the Commission's list of deputies elected in 2015 who left office early or never took it up. The "
    "candidates' votes are not published on the site and are not stored."
)
HEAD_METHOD = (
    "From the Commission's page of the results of the election of the city head, which prints the votes of every "
    "candidate. The counts of voters and ballots are not published on the site and are not stored."
)
SMALL_HEAD_METHOD = (
    "The Commission's results site names only the head elected in a village or settlement, or in a town whose "
    "results page lists no candidates. The votes of all the candidates are not published on the site and are not "
    "stored."
)
MERGED = "Рада увійшла до складу об’єднаної громади"


def packages() -> list[str]:
    return [GEO, REGISTRY, f"{SOURCE}/{DAY}", f"{SOURCE}/{RUNOFF_DAY}"]


def parse() -> None:
    geo().write()
    registry().write()
    first().write()
    runoff().write()


def _election(p: Package, day: date, kind: str) -> base.Build:
    id = key("election", day)
    p.add("election", id=id, polity_id=UKRAINE, kind=kind, date_from=day, date_to=day, status="held")
    path = f"ua/{day}-local" + ("-runoff" if kind == "runoff" else "")
    p.add("slug", path=path, entity_id=id, is_current=True)
    if kind == "runoff":
        p.name(id, f"Ukrainian local elections of 2015, second round of {day:%-d %B %Y}", "en", "official")
        p.name(id, f"Повторне голосування на місцевих виборах {day:%d.%m.%Y}", "uk", "native", False)
    else:
        p.name(id, "2015 Ukrainian local elections", "en", "official")
        p.name(id, f"Чергові місцеві вибори {day:%d.%m.%Y}", "uk", "native", False)
    return base.Build(p, id, path, source=CVK)


def council_body(c: Council) -> str:
    if c.region == KYIV:
        return base.body("kyiv")
    return base.body({"oblast": "oblast", "raion": "raion", "city_district": "city_district"}.get(c.tier, "hromada"))


def region_name(region: str) -> str:
    return base.oblast_name(region) if region != KYIV else "Київ"


def _region_names() -> dict[str, str]:
    return {code: name for name, code in regions().items()}


def label_of(c: Council | Head) -> str:
    return c.label or _region_names().get(c.region, "")


def party_of(name: str, known: set[str]) -> str | None:
    id = base.party(name)
    if id is None or norm(name) not in known:
        return None
    return existing(name) or id


def _merge(page: list[Deputy], departed: list[Deputy]) -> list[Deputy]:
    """The deputies of the council's page, each once, then those of the list of departed deputies it lacks."""
    listed = {(d.name, d.born): d for d in page}
    seen = {d.name for d in page}
    later = {d.name: d for d in departed if d.name not in seen}
    return [*listed.values(), *later.values()]


CITED: dict[str, set[str]] = {}


def cite(p: Package, url: str) -> None:
    """Cite a page once, though two of the site's addresses can serve the same file."""
    artifact = raw.cached(SOURCE, url)
    cited = CITED.setdefault(p.key, set())
    if artifact.storage_key not in cited:
        cited.add(artifact.storage_key)
        p.artifact(artifact, CVK)


def first() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Ukraine's regular local elections of 25 October 2015: the oblast, raion, city, city-raion, settlement, and "
        "village councils with each list's votes and seats or each district's deputy, and the heads of villages, "
        "settlements, and cities, with every candidate's votes where the city had a results page, from the Central "
        "Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", GEO, REGISTRY, "ua.cvk/registry"],
    )
    p.external.add(UKRAINE)
    p.external |= party_ids(set(nominators()))
    for url in [COUNCILS, HEADS, DEPARTED_DEPUTIES, DEPARTED_HEADS, *_links(COUNCILS, "pvm037")]:
        cite(p, url)
    for url in _links(HEADS, "pvm038"):
        cite(p, url)
    b = _election(p, DAY, "local")
    known = set(base.nominators()) | set(nominators())
    unmatched = empty = 0
    for c in sorted(councils().values(), key=lambda c: (int(c.region), c.tier, c.label, c.name, int(c.rada))):
        page = council_page(c.url)
        departed = departed_deputies().get(c.rada, [])
        deputies = _merge(page.deputies, departed)
        if not page.parties and not deputies:
            empty += 1
            continue
        cite(p, c.url)
        by_list = page.by_list
        tag = ("council", c.rada, DAY)
        district = district_of(c)
        cid, rs = b.contest(
            tag,
            c.name,
            (c.name, label_of(c), region_name(c.region), c.rada),
            completeness=1.0 if by_list else 0.0,
            kind="list_allocation" if by_list else "body_seats",
            body_id=council_body(c),
            district_area_id=district,
            magnitude=c.seats,
            round_number=1,
            electoral_system_id=system("list" if by_list else "districts"),
        )
        u = unit_of(c)
        if by_list:
            b.note(rs, "methodology", LIST_METHOD)
            options: dict[str, str] = {}
            total = sum(row.seats for row in page.parties)
            if total != c.seats:
                b.note(
                    rs,
                    "caveat",
                    f"The lists won {total} of the council's {c.seats} seats, as the Commission's page prints them.",
                    "seats",
                )
            for position, row in enumerate(page.parties, 1):
                option = options[norm(row.name)] = key("option", *tag, norm(row.name))
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=cid,
                    kind="list",
                    party_id=party_of(row.name, known),
                    ballot_position=position,
                    ballot_label=row.name,
                )
                b.value(rs, u, option, row.votes)
                p.add(
                    "outcome", result_set_id=rs, option_id=option, kind="won" if row.seats else "lost", seats=row.seats
                )
            for deputy in deputies:
                option = options.get(norm(deputy.nominator))
                if option is None:
                    unmatched += 1
                    continue
                person_tag = ("deputy", DAY, c.rada, deputy.name, deputy.born)
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
            b.note(rs, "methodology", DISTRICT_METHOD)
            if len(deputies) != c.seats:
                b.note(
                    rs,
                    "caveat",
                    f"The Commission names {len(deputies)} deputies elected to the council's {c.seats} seats; the "
                    "others were not elected in this election or left no record on the site.",
                    "seats",
                )
            ordered = sorted(deputies, key=lambda d: (d.departed, d.district.zfill(4), d.name))
            for position, deputy in enumerate(ordered, 1):
                person_tag = ("deputy", DAY, c.rada, deputy.name, deputy.born)
                option = key("option", *tag, *person_tag)
                pid = party_of(deputy.nominator, known)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=cid,
                    kind="candidate",
                    party_id=pid,
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
                    is_independent=norm(deputy.nominator) == SELF,
                )
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    p.accept(
        "outcomes.list_members",
        "the Commission's pages list the deputies elected in 2015 who still sit or who took a seat later as the next "
        "candidates of their list, and its list of deputies who left office names no list position, so a list can "
        "name more or fewer deputies than the seats it won; the seats are stored as the council's page prints them",
    )
    if unmatched:
        print(f"{SOURCE}/{DAY}: {unmatched} list deputies name a nominator without a list on their page", flush=True)
    print(f"{SOURCE}/{DAY}: {empty} councils have no results on the site", flush=True)
    held = sum(
        _head(b, h, known)
        for h in sorted(heads().values(), key=lambda h: (int(h.region), h.label, h.council, int(h.rada)))
    )
    print(f"{SOURCE}/{DAY}: {held} of {len(heads())} heads' elections staged", flush=True)
    return p


def _keys(candidates: list[Candidate]) -> list[str]:
    seen: Counter[str] = Counter()
    found: list[str] = []
    for row in candidates:
        seen[row.name] += 1
        found.append(row.name if seen[row.name] == 1 else f"{row.name}#{seen[row.name]}")
    return found


def _options(
    b: base.Build, cid: str, rs: str, tag: tuple[object, ...], h: Head, candidates: list[Candidate], known: set[str]
) -> dict[str, str]:
    p = b.p
    u = unit_of(h)
    options: dict[str, str] = {}
    counted = any(row.votes for row in candidates)
    for position, (who, row) in enumerate(zip(_keys(candidates), candidates, strict=True), 1):
        option = options[who] = key("option", *tag, who)
        pid = party_of(row.nominator, known)
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="candidate",
            party_id=pid,
            ballot_position=position,
            ballot_label=row.name,
        )
        person = key("person", "head", DAY, h.rada, who)
        if tag[-1] == DAY:
            b.person(("head", DAY, h.rada, who), row.name, h.born if row.name == h.name else "")
        p.add(
            "candidacy",
            id=key("candidacy", *tag, who),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=row.name,
            member_of_party_id=None,
            is_independent=norm(row.nominator) == SELF,
        )
        if counted:
            b.value(rs, u, option, row.votes)
    return options


def head_contest(h: Head) -> tuple[object, ...]:
    return ("head", h.rada, DAY)


def _head(b: base.Build, h: Head, known: set[str]) -> int:
    p = b.p
    page = h.page if h.page and h.page in raw.keys(SOURCE) else None
    candidates = head_results(page) if page else []
    if not candidates and not h.name:
        return 0
    kyiv = h.region == KYIV
    tag = head_contest(h)
    cid, rs = b.contest(
        tag,
        f"{h.council}: голова" if not kyiv else "Київський міський голова",
        (h.council, label_of(h), region_name(h.region), h.rada) if not kyiv else ("Київський міський голова",),
        completeness=1.0 if candidates else 0.0,
        kind="office",
        office_id=base.office("kyiv" if kyiv else "hromada"),
        district_area_id=district_of(h),
        magnitude=1,
        round_number=1,
        electoral_system_id=system("two_round" if kyiv or h.runoff else "plurality"),
    )
    if candidates:
        cite(p, page or "")
        b.note(rs, "methodology", HEAD_METHOD)
    else:
        candidates = [Candidate(h.name, h.nominator, 0)]
        b.note(rs, "methodology", SMALL_HEAD_METHOD)
    options = _options(b, cid, rs, tag, h, candidates, known)
    if h.runoff and h.runoff in raw.keys(SOURCE):
        advancing = {row.name for row in head_results(h.runoff)}
        for who, row in zip(_keys(candidates), candidates, strict=True):
            kind = "advanced" if row.name in advancing else "eliminated"
            p.add("outcome", result_set_id=rs, option_id=options[who], kind=kind)
    elif h.name:
        if h.name not in options:
            raise ValueError(f"{h.council}: the head elected, {h.name}, is not among the candidates")
        for who in options:
            p.add("outcome", result_set_id=rs, option_id=options[who], kind="elected" if who == h.name else "lost")
    else:
        said = f"The Commission's list of the heads elected prints «{h.status}» and" if h.status else "The Commission"
        b.note(rs, "caveat", f"{said} names no head elected in this election.", "later")
    return 1


def runoff() -> Package:
    p = Package(
        f"{SOURCE}/{RUNOFF_DAY}",
        SOURCE,
        f"The second round of {RUNOFF_DAY:%-d %B %Y} of the elections of Ukrainian city heads and Kyiv's mayor, from "
        "the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", GEO, REGISTRY, "ua.cvk/registry", f"{SOURCE}/{DAY}"],
    )
    p.external.add(UKRAINE)
    p.external |= party_ids(set(nominators()))
    for url in [RUNOFF_HEADS, DEPARTED_RUNOFF_HEADS, *_links(RUNOFF_HEADS, "pvm038")]:
        cite(p, url)
    b = _election(p, RUNOFF_DAY, "runoff")
    known = set(base.nominators()) | set(nominators())
    for h in sorted(heads().values(), key=lambda h: (int(h.region), h.label, h.council, int(h.rada))):
        if not h.runoff or h.runoff not in raw.keys(SOURCE):
            continue
        cite(p, h.runoff)
        tag = ("head", h.rada, RUNOFF_DAY)
        kyiv = h.region == KYIV
        cid, rs = b.contest(
            tag,
            f"{h.council}: голова, повторне голосування" if not kyiv else "Київський міський голова",
            (h.council, label_of(h), region_name(h.region), h.rada) if not kyiv else ("Київський міський голова",),
            completeness=1.0,
            kind="office",
            office_id=base.office("kyiv" if kyiv else "hromada"),
            district_area_id=district_of(h),
            magnitude=1,
            round_number=2,
            previous_round_id=key("contest", *head_contest(h)),
            electoral_system_id=system("two_round"),
        )
        b.note(rs, "methodology", HEAD_METHOD)
        options = _options(b, cid, rs, tag, h, head_results(h.runoff), known)
        if not h.runoff_name:
            b.note(rs, "caveat", "The Commission names no head elected in this round.", "later")
            continue
        if h.runoff_name not in options:
            raise ValueError(f"{h.council}: the head elected, {h.runoff_name}, is not in the second round")
        for who in options:
            kind = "elected" if who == h.runoff_name else "lost"
            p.add("outcome", result_set_id=rs, option_id=options[who], kind=kind)
    return p
