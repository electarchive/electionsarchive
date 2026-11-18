"""Ukraine: the interim local elections of 2015 to 2022 and the elections of starostas.

Part of the ua.cvk_local pipeline; its module calls fetch() and parse() here.

The Commission's results sites for the local elections (cvk.gov.ua/pls/vm2015, election 100, and vm2020, election
695) list under each year every election held after the regular one: first elections of amalgamated communities,
early (позачергові), repeat (повторні), interim (проміжні), and additional (додаткові) elections, the repeat votes
(повторне голосування) of heads, and the elections of starostas. Each election has the pages of the regular ones:
the councils with the deputies elected, the heads with the votes of every candidate where the site prints them, and
the starostas elected.
"""

import functools
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from ea import changeset, raw
from ea.latin import ukrainian
from ea.package import Package
from ea.sources.ua import cvk_local as base
from ea.sources.ua import cvk_local_2015 as old
from ea.sources.ua.cvk import clean, norm, number, text

SOURCE = "ua.cvk_local"
HOST = "https://www.cvk.gov.ua/pls/"
SITES = {"vm2015": (100, range(2015, 2021)), "vm2020": (695, range(2020, 2023))}
WORKERS = 12
ATTEMPTS = 6
KINDS = {
    "Перші місцеві вибори": "first",
    "Позачергові місцеві вибори": "early",
    "Повторні місцеві вибори": "repeat",
    "Проміжні місцеві вибори": "interim",
    "Додаткові місцеві вибори": "additional",
    "Повторне голосування": "runoff",
    "Голосув. з черг. виборів": "regular_late",
}


def day_lists() -> list[str]:
    """The Commission's lists of elections: a year each, and on the 2015 site the first eight months of 2020 one by
    one."""
    found: list[str] = []
    for site, (election, years) in SITES.items():
        for year in years:
            if site == "vm2015" and year == 2020:
                found += [
                    f"{HOST}{site}/wm002pt001f01={election}kod_group=1pt001f10=01.{m:02d}.2020.html"
                    for m in range(1, 9)
                ]
            else:
                found.append(f"{HOST}{site}/wm002pt001f01={election}kod_group=2pt001f10=01.01.{year}.html")
    return found


def get(url: str) -> bool:
    """Fetch one page into the cache, trying again when the site answers with its «service unavailable» page."""
    if url in raw.keys(SOURCE):
        return True
    for attempt in range(ATTEMPTS):
        try:
            raw.fetch(SOURCE, url, suffix=".html")
            return True
        except httpx.HTTPStatusError as failure:
            if failure.response.status_code == 404:
                return False
            time.sleep(2 + 3 * attempt)
        except httpx.HTTPError:
            time.sleep(2 + 3 * attempt)
    print(f"{SOURCE}: {url} could not be fetched", file=sys.stderr, flush=True)
    return False


def get_all(urls: list[str]) -> list[str]:
    """Fetch many pages; the ones that failed are returned."""
    todo = [url for url in dict.fromkeys(urls) if url not in raw.keys(SOURCE)]
    with ThreadPoolExecutor(WORKERS) as pool:
        done = list(pool.map(get, todo))
    return [url for url, ok in zip(todo, done, strict=True) if not ok]


@dataclass(frozen=True)
class Election:
    site: str
    id: int
    day: date
    label: str
    kind: str

    @property
    def base(self) -> str:
        return f"{HOST}{self.site}/"


def listed() -> list[Election]:
    """Every election of the day lists, oldest first, without the regular elections' own second rounds."""
    found: dict[tuple[str, int], Election] = {}
    for url in day_lists():
        if url not in raw.keys(SOURCE):
            continue
        site = url.split("/")[-2]
        body = text(raw.cached(SOURCE, url))
        start = body.find("ЦЕНТРАЛЬНА ВИБОРЧА")
        for link, label in re.findall(r'<a[^>]*href="wm002pt001f01=(\d+)[^"]*"[^>]*>([^<]*)', body[start:]):
            label = " ".join(label.split())
            found_day = re.search(r"(\d\d\.\d\d\.\d{4})$", label)
            if found_day is None:
                continue
            kind = KINDS[label[: found_day.start()].strip()]
            found[site, int(link)] = Election(
                site, int(link), datetime.strptime(found_day.group(1), "%d.%m.%Y").date(), label, kind
            )
    return sorted(found.values(), key=lambda e: (e.day, e.id))


def landing(e: Election) -> str:
    return f"{e.base}wm001pt001f01={e.id}.html"


def links(url: str, *prefixes: str) -> list[str]:
    base = url.rsplit("/", 1)[0] + "/"
    body = text(raw.cached(SOURCE, url))
    found = re.findall(rf'href="((?:{"|".join(prefixes)})[^"]*)"', body)
    return [base + (link if link.endswith(".html") else link + ".html") for link in dict.fromkeys(found)]


def summaries(e: Election) -> list[str]:
    """The pages of an election's landing page that list its councils, heads, and starostas, and the officials whose
    powers ended."""
    if landing(e) not in raw.keys(SOURCE):
        return []
    return links(landing(e), "pvm002", "pvm003", "pvm009", "pvm070", "pvm071")


def stage(urls: list[str], name: str) -> list[str]:
    failed = get_all(urls)
    print(f"{SOURCE}: {name}: {len(urls)} pages, {len(failed)} failed", flush=True)
    return failed


STAROSTAS = f"{HOST}vm2015/pvm025pt001f01=223.html"


def fetch_starostas() -> None:
    """The cumulative list of starostas elected in 2016-2019, with each community's districts and their elected."""
    stage([STAROSTAS], "starostas")
    stage(links(STAROSTAS, "pvm026", "pvm027"), "starosta pages")


def fetch() -> None:
    for url, suffix in ((base.SHAPES, ".zip"), (old.OBLAST_SHAPES, ".geojson"), (old.COUNCIL_SHAPES, ".geojson")):
        raw.fetch(SOURCE, url, suffix=suffix)
    stage(day_lists(), "day lists")
    elections = listed()
    stage([landing(e) for e in elections], "landing pages")
    stage([url for e in elections for url in summaries(e)], "summaries")
    lists: list[str] = []
    for e in elections:
        for url in summaries(e):
            if url in raw.keys(SOURCE):
                lists += links(url, "pvm037", "pvm038", "pvm047")
    stage(lists, "lists")
    pages: list[str] = []
    for url in lists:
        if url in raw.keys(SOURCE):
            pages += links(url, "pvm057", "pvm058", "pvm048", "pvm047", "pvm028")
    stage(pages, "pages")
    fetch_starostas()


# ---------------------------------------------------------------------------------------------------------------------
# Reading the pages


# The regular elections' own second rounds, which the regular elections' packages hold.
OWN_RUNOFFS = {("vm2015", 102), ("vm2020", 697), ("vm2020", 698), ("vm2020", 699), ("vm2020", 708), ("vm2020", 709)}
TIERS = {**old.TIERS}
HEAD_TIERS = {**old.HEAD_TIERS}
SELF = base.SELF
BORN = old.BORN


@dataclass
class Council:
    rada: str
    tier: str
    region: str
    label: str
    name: str
    seats: int
    elected: int
    url: str


@dataclass
class Head:
    rada: str
    tier: str
    region: str
    label: str
    council: str
    name: str
    nominator: str
    born: str
    page: str | None
    status: str = ""


def elections() -> list[Election]:
    return [e for e in listed() if (e.site, e.id) not in OWN_RUNOFFS]


def have(url: str) -> bool:
    return url in raw.keys(SOURCE)


@functools.cache
def councils(site: str, id: int) -> list[Council]:
    """The councils an election lists, with the seats to fill and the number elected."""
    e = next(e for e in listed() if (e.site, e.id) == (site, id))
    found: dict[str, Council] = {}
    for summary in summaries(e):
        if "/pvm002" not in summary or not have(summary):
            continue
        for url in links(summary, "pvm037"):
            if not have(url):
                continue
            tier = TIERS[base.param(url, "pid112")]
            region = base.param(url, "pid100")
            for cells, hrefs in base.rows(url):
                link = next((h for h in hrefs if h.startswith(("pvm057", "pvm058"))), None)
                if link is None or len(cells) < 3:
                    continue
                label, _, name = cells[0].rpartition(", ")
                rada = base.param(link, "pid102")
                page = e.base + (link if link.endswith(".html") else link + ".html")
                found[rada] = Council(
                    rada, tier, region, label, name, number(cells[1]) or 0, number(cells[2]) or 0, page
                )
    return list(found.values())


def born(info: str) -> str:
    found = BORN.search(info)
    return found.group(1) if found else ""


@functools.cache
def heads(site: str, id: int) -> list[Head]:
    """The heads an election lists as elected, with the page of every candidate's votes where the site has one."""
    e = next(e for e in listed() if (e.site, e.id) == (site, id))
    found: dict[str, Head] = {}
    for summary in summaries(e):
        if "/pvm003" not in summary or not have(summary):
            continue
        for url in links(summary, "pvm038"):
            if not have(url):
                continue
            tier = HEAD_TIERS[base.param(url, "pid111")]
            region = base.param(url, "pid100")
            for cells, hrefs in base.rows(url):
                if len(cells) < 2 or not cells[0] or cells[0].startswith("Найменування") or "WWW ІАС" in cells[0]:
                    continue
                link = next((h for h in hrefs if h.startswith("pvm048")), None)
                page = e.base + (link if link.endswith(".html") else link + ".html") if link else None
                label, _, council = cells[0].rpartition(", ")
                rada = base.param(link, "pid102") if link else ""
                named = len(cells) >= 4 and bool(cells[1])
                name, nominator, info = cells[1:4] if named else ("", "", "")
                found[rada or f"{region}/{cells[0]}"] = Head(
                    rada, tier, region, label, council, name, nominator, born(info), page, "" if named else cells[1]
                )
    return list(found.values())


# ---------------------------------------------------------------------------------------------------------------------
# Places

key = base.key
UKRAINE = base.UKRAINE
CVK = key("source", "cvk", "interim")
S_OBLAST, S_COUNCIL = "ua.interim_oblast", "ua.interim_council"
KYIV = "80"
# The Commission's oblast codes (the first two digits of the territorial codes) and names.
REGIONS = {
    "1": "Автономна Республіка Крим",
    "5": "Вінницька область",
    "7": "Волинська область",
    "12": "Дніпропетровська область",
    "14": "Донецька область",
    "18": "Житомирська область",
    "21": "Закарпатська область",
    "23": "Запорізька область",
    "26": "Івано-Франківська область",
    "32": "Київська область",
    "35": "Кіровоградська область",
    "44": "Луганська область",
    "46": "Львівська область",
    "48": "Миколаївська область",
    "51": "Одеська область",
    "53": "Полтавська область",
    "56": "Рівненська область",
    "59": "Сумська область",
    "61": "Тернопільська область",
    "63": "Харківська область",
    "65": "Херсонська область",
    "68": "Хмельницька область",
    "71": "Черкаська область",
    "73": "Чернівецька область",
    "74": "Чернігівська область",
    "80": "Київ",
    "85": "Севастополь",
}


def stem_name(name: str) -> str:
    """A council's or community's own name: «Агрономічна» of «Агрономічна сільська рада»."""
    return re.sub(r"\s+(сільська|селищна|міська|районна)\s+(громада|рада)$", "", clean(name))


@dataclass
class Place:
    code: str
    name: str  # the community's name: «Комиш-Зорянська селищна громада»
    region: str
    label: str  # the raion the Commission prints before the name
    tier: str


def community(name: str) -> str:
    return re.sub(r"\s+рада$", " громада", clean(name))


def council_of(name: str) -> str:
    return re.sub(r"\s+громада$", " рада", clean(name))


@functools.cache
def code_index() -> dict[tuple[str, str], str]:
    """The council code (pid102) for each council the pages name by region and name, from every list that names it."""
    found: dict[tuple[str, str], set[str]] = {}
    for e in elections():
        for c in councils(e.site, e.id):
            found.setdefault((c.region, norm(c.label + " " + community(c.name))), set()).add(c.rada)
            found.setdefault((c.region, norm(community(c.name))), set()).add(c.rada)
        for h in heads(e.site, e.id):
            if h.rada:
                found.setdefault((h.region, norm(h.label + " " + community(h.council))), set()).add(h.rada)
                found.setdefault((h.region, norm(community(h.council))), set()).add(h.rada)
    return {k: next(iter(v)) for k, v in found.items() if len(v) == 1}


def code_of(region: str, label: str, name: str, rada: str = "") -> str:
    if rada:
        return rada
    name = community(name)
    found = code_index().get((region, norm(label + " " + name))) or code_index().get((region, norm(name)))
    return found or f"{region}-{changeset.slug(ukrainian(label + ' ' + name))}"


def area(scheme: str, code: str) -> str:
    return base.area(scheme, code)


def unit(scheme: str, code: str) -> str:
    return base.unit(scheme, code)


@functools.cache
def places() -> dict[str, Place]:
    """Every council or community the elections of the package name, by its code."""
    found: dict[str, Place] = {}
    for e in elections():
        for c in councils(e.site, e.id):
            found.setdefault(c.rada, Place(c.rada, community(c.name), c.region, c.label, c.tier))
        for h in heads(e.site, e.id):
            code = code_of(h.region, h.label, h.council, h.rada)
            found.setdefault(code, Place(code, community(h.council), h.region, h.label, h.tier))
    for community_name, region, label, code in starosta_places():
        found.setdefault(code, Place(code, community_name, region, label, "community"))
    return found


def region_name(region: str) -> str:
    return REGIONS[region]


# ---------------------------------------------------------------------------------------------------------------------
# Starostas


@dataclass
class District:
    number: int
    description: str
    day: date
    name: str
    nominator: str
    born: str


@dataclass
class StarostaCommunity:
    code: str
    region: str
    label: str
    name: str
    pages: tuple[str, str]
    districts: list[District]


@functools.cache
def starosta_communities() -> list[StarostaCommunity]:
    """The communities with elected starostas, each district with the date it voted and the starosta it elected."""
    if not have(STAROSTAS):
        return []
    by_name = {name: code for code, name in REGIONS.items()}
    found: list[StarostaCommunity] = []
    for elected in links(STAROSTAS, "pvm026"):
        dates = elected.replace("pvm026", "pvm027")
        if not have(elected) or not have(dates):
            continue
        winners: dict[str, list[tuple[str, str, str]]] = {}
        for cells, _ in base.rows(elected):
            if len(cells) == 4 and cells[0] != "Опис меж виборчого округу":
                winners.setdefault(cells[0], []).append((cells[1], cells[2], born(cells[3])))
        body = re.sub(r"<[^>]+>", " ", text(raw.cached(SOURCE, elected)))
        found_header = re.search(
            r"((?:[А-ЯІЇЄҐ][\w'’-]+ область|м\.[А-ЯІЇЄҐ][а-яіїєґ'’-]+)),\s*(.*?),?\s*([^,]+? громада)", body
        )
        if found_header is None:
            raise ValueError(f"{elected} names no community")
        region = by_name[clean(found_header.group(1))]
        label, name = clean(found_header.group(2)), clean(found_header.group(3))
        districts: list[District] = []
        for cells, _ in base.rows(dates):
            if len(cells) == 3 and cells[0].isdigit() and winners.get(cells[1]):
                who, nominator, birth = winners[cells[1]].pop(0)
                districts.append(
                    District(
                        int(cells[0]),
                        cells[1],
                        datetime.strptime(cells[2], "%d.%m.%Y").date(),
                        who,
                        nominator,
                        birth,
                    )
                )
        code = base.param(elected, "pid102")
        found.append(StarostaCommunity(code, region, label, name, (elected, dates), districts))
    return found


def starosta_places() -> list[tuple[str, str, str, str]]:
    return [(s.name, s.region, s.label, s.code) for s in starosta_communities()]


# ---------------------------------------------------------------------------------------------------------------------
# Shapes

import json
import zipfile

import numpy
import shapely

# ISO 3166-2 numbers of the boundary datasets that differ from the Commission's oblast codes.
ISO_TO_CVK = {"9": "44", "77": "73", "30": "80", "40": "85", "43": "1"}
OUTLINE_TOLERANCE = base.TOLERANCE
OTH_FROM = 12000  # council codes from here on belong to communities formed in 2015 or later


def _region(iso: str) -> str:
    number_ = str(int(iso.removeprefix("UA-").removeprefix("UA")))
    return ISO_TO_CVK.get(number_, number_)


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
    return _squash(ukrainian(re.sub(r"['’ʼ`]", "", stem_name(name))))


@dataclass(frozen=True)
class Outline:
    geometry: shapely.Geometry
    source: str  # "gb" (geoBoundaries) or "cod" (COD-AB)


@functools.cache
def _cod() -> list[tuple[str, str, shapely.Geometry]]:
    """Level 3 of COD-AB: the community's oblast (the Commission's code), name, and simplified outline."""
    with zipfile.ZipFile(raw.cached(SOURCE, base.SHAPES).path) as archive:
        features = json.loads(archive.read("ukr_admin3.geojson"))["features"]
    outlines = [shapely.make_valid(shapely.geometry.shape(f["geometry"])) for f in features]
    simple = shapely.coverage_simplify(numpy.array(outlines), OUTLINE_TOLERANCE)
    found = []
    for feature, outline in zip(features, simple, strict=True):
        props = feature["properties"]
        names = {clean(str(props["adm3_name1"] or "")), clean(str(props.get("adm3_name1_old") or ""))} - {""}
        for name in names:
            found.append((_region(str(props["adm1_pcode"])), name, _polygons(outline)))
    return found


@functools.cache
def shapes() -> dict[str, Outline]:
    """The outline of each council's or community's territory, by code. Councils of communities formed since 2015 take
    the community of the same name and oblast in COD-AB, where one has it; the rest take the village council of the
    same romanized name and oblast in geoBoundaries."""
    oblasts = []
    for feature in json.loads(raw.cached(SOURCE, old.OBLAST_SHAPES).path.read_bytes())["features"]:
        oblasts.append((_region(str(feature["properties"]["shapeISO"])), shapely.geometry.shape(feature["geometry"])))
    tree = shapely.STRtree([g for _, g in oblasts])

    def region_of(point: shapely.Geometry) -> str:
        hits = tree.query(point, predicate="within")
        return oblasts[int(hits[0])][0] if len(hits) == 1 else ""

    villages: dict[tuple[str, str], list[shapely.Geometry]] = {}
    for feature in json.loads(raw.cached(SOURCE, old.COUNCIL_SHAPES).path.read_bytes())["features"]:
        geometry = _polygons(shapely.geometry.shape(feature["geometry"]))
        key_ = (region_of(shapely.point_on_surface(geometry)), _squash(str(feature["properties"]["shapeName"])))
        villages.setdefault(key_, []).append(geometry)
    communities: dict[tuple[str, str], list[shapely.Geometry]] = {}
    for region, name, geometry in _cod():
        communities.setdefault((region, base._word(name)), []).append(geometry)
    aliases = {base._word(name): base._word(other) for name, other in base.SHAPE_NAMES.items()}
    ours: dict[tuple[str, str], list[str]] = {}
    for code, place in places().items():
        if place.tier != "city_district" and place.region != KYIV:
            ours.setdefault((place.region, base._word(place.name)), []).append(code)
    found: dict[str, Outline] = {}
    for (region, word), codes in ours.items():
        word = aliases.get(word, word)
        for code in codes:
            hits = communities.get((region, word), []) if code.isdigit() and int(code) >= OTH_FROM else []
            kind = "cod"
            if len(hits) != 1:
                hits = villages.get((region, _adjective(places()[code].name)), [])
                kind = "gb"
            if len(hits) == 1 and len(codes) == 1:
                geometry = _polygons(shapely.simplify(hits[0], OUTLINE_TOLERANCE, preserve_topology=True))
                found[code] = Outline(geometry, kind)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Registry

S_OBLAST_NOUN = "oblast or city with special status"
BODIES = {
    "oblast": ("Oblast council", "Обласна рада"),
    "raion": ("Raion council", "Районна рада"),
    "hromada": ("Village, settlement, or city council", "Сільська, селищна, міська рада"),
    "city_district": ("Council of a raion within a city", "Районна у місті рада"),
    "kyiv": ("Kyiv City Council", "Київська міська рада"),
}
OFFICES = {
    "hromada": ("Head of a village, settlement, or city community", "Сільський, селищний, міський голова", "mayor"),
    "kyiv": ("Mayor of Kyiv", "Київський міський голова", "mayor"),
    "starosta": ("Starosta of a village district in an amalgamated community", "Староста", "other"),
}
CUTOVER = date(2020, 1, 1)  # the Electoral Code took effect
# The legal regimes: the Law on Local Elections of 2015, then the Electoral Code.
REGIMES = {"law2015": (old.SYSTEMS, date(2015, 7, 14), CUTOVER), "code": (base.SYSTEMS, CUTOVER, None)}


def body(kind: str) -> str:
    return key("body", "interim", kind)


def office(kind: str) -> str:
    return key("office", "interim", kind)


def system(day: date, kind: str) -> str:
    return key("electoral_system", "interim", "law2015" if day < CUTOVER else "code", kind)


def party_id(name: str) -> str | None:
    return None if norm(name) == SELF else key("party", "interim", norm(name))


def packages_for(e: Election) -> str:
    return f"{SOURCE}/interim-{e.day}-{e.id}"


GEO = f"{SOURCE}/geo-interim"
REGISTRY = f"{SOURCE}/registry-interim"


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The bodies, offices, electoral systems, and nominating parties of Ukraine's interim local elections of 2015 to "
        "2022 and its elections of starostas, from the Central Election Commission",
    )
    p.external.add(UKRAINE)
    p.add(
        "source",
        id=CVK,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="WWW ІАС «Місцеві вибори»: results of the first, early, interim, repeat, and additional local elections "
        "and of the elections of starostas, 2015-2022",
        url="https://www.cvk.gov.ua/pls/vm2015/wm001pt001f01=100.html",
        license="Free reuse with the source cited, as the results site states",
        license_url="https://www.cvk.gov.ua/pls/vm2015/wm001pt001f01=100.html",
        attribution="Центральна виборча комісія",
        notes="The site states: «При повному або частковому використанні інформації посилання на джерело обов’язкове». "
        "The results sites of 2015 (vm2015) and 2020 (vm2020) list every election held between the regular ones.",
    )
    for kind, (english, native) in BODIES.items():
        p.add("body", id=body(kind), polity_id=UKRAINE, kind="council")
        p.name(body(kind), english, "en", "official")
        p.name(body(kind), native, "uk", "native", False)
    for kind, (english, native, office_kind) in OFFICES.items():
        p.add("office", id=office(kind), polity_id=UKRAINE, kind=office_kind)
        p.name(office(kind), english, "en", "official")
        p.name(office(kind), native, "uk", "native", False)
    for regime, (systems, start, end) in REGIMES.items():
        for kind, (rules, english, method) in systems.items():
            id = key("electoral_system", "interim", regime, kind)
            p.add(
                "electoral_system",
                id=id,
                polity_id=UKRAINE,
                valid_from=start,
                valid_to=end,
                rules=rules,
                rules_version=1,
            )
            p.name(id, english, "en", "official")
            p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, name in sorted(nominators().items()):
        id = key("party", "interim", code)
        p.add("party", id=id, kind="party", polity_id=UKRAINE)
        p.name(id, name, "uk", "official")
    return p


def _list_page(url: str) -> old.Page:
    """A page of the Electoral Code's layout for a council elected by lists: three cells for each list (the name,
    its votes, and its seats) and six for each deputy (the territorial district, the name, the details, the votes, the
    share of the quota, and the electoral district), under a row that names the list."""
    parties: list[old.Party] = []
    deputies: list[old.Deputy] = []
    section = ""
    nominator = ""
    for cells, _ in base.rows(url):
        if not cells:
            continue
        head = cells[0]
        if head.startswith("Назва політичної партії, місцева організація якої висунула кандидатів"):
            section = "votes" if len(cells) > 1 and cells[1].startswith("К-сть голосів") else "summary"
        elif head.startswith("№ ТВО"):
            section = "list"
        elif head.startswith("©") or "WWW ІАС" in head:
            break
        elif section == "votes" and len(cells) == 3 and number(cells[1]) is not None:
            parties.append(old.Party(head, number(cells[1]) or 0, number(cells[2]) or 0))
        elif section == "list" and len(cells) == 1 and head:
            nominator = head
        elif section == "list" and len(cells) == 6 and cells[1]:
            deputies.append(old.Deputy(cells[1], born(cells[2]), head, nominator, number(cells[3])))
    return old.Page(bool(parties), parties, deputies)


@functools.cache
def council_page(url: str) -> old.Page:
    page = old.council_page(url)
    if page.parties or page.deputies:
        return page
    return _list_page(url)


@functools.cache
def candidates(url: str) -> list[old.Candidate]:
    return old.head_results(url)


@functools.cache
def nominators() -> dict[str, str]:
    """Every nominating party the pages print, by key; the most common spelling wins."""
    spellings: dict[str, Counter[str]] = {}

    def see(name: str) -> None:
        if name and norm(name) != SELF:
            spellings.setdefault(norm(name), Counter())[name] += 1

    for e in elections():
        for c in councils(e.site, e.id):
            page = council_page(c.url)
            for row in page.parties:
                see(row.name)
            for deputy in page.deputies:
                see(deputy.nominator)
        for h in heads(e.site, e.id):
            see(h.nominator)
            if h.page and have(h.page):
                for candidate in candidates(h.page):
                    see(candidate.nominator)
    for s in starosta_communities():
        for d in s.districts:
            see(d.nominator)
    return {code: counts.most_common(1)[0][0] for code, counts in spellings.items()}


# ---------------------------------------------------------------------------------------------------------------------
# Geography

GB_SOURCE = key("source", "geoboundaries", "UKR-ADM3", "interim")
COD_SOURCE = key("source", "cod-ab", "interim")
FIRST_DAY = date(2015, 10, 25)


def oblast_area(region: str) -> str:
    return area(S_OBLAST, region)


def council_area(code: str) -> str:
    return area(S_COUNCIL, code)


def council_unit(code: str) -> str:
    return unit(S_COUNCIL, code)


def _wkb(geometry: shapely.Geometry) -> tuple[str, str]:
    return (
        shapely.to_wkb(geometry, hex=True, include_srid=True),
        shapely.to_wkb(shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True),
    )


def oblast_shapes() -> dict[str, shapely.Geometry]:
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, old.OBLAST_SHAPES).path.read_bytes())["features"]:
        region = _region(str(feature["properties"]["shapeISO"]))
        geometry = _polygons(shapely.geometry.shape(feature["geometry"]))
        found[region] = _polygons(shapely.simplify(geometry, OUTLINE_TOLERANCE, preserve_topology=True))
    return found


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The councils and communities of Ukraine's interim local elections of 2015 to 2022, with the oblasts that hold "
        "them, as the Central Election Commission names them",
    )
    p.external.add(UKRAINE)
    for code, rank, noun in (
        (S_OBLAST, 1, S_OBLAST_NOUN),
        (S_COUNCIL, 2, "territory of a council or of an amalgamated community"),
    ):
        p.add("scheme", code=code, polity_id=UKRAINE, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries: Ukraine, ADM3 (village councils) and ADM1 (oblasts), gbOpen release 6.0",
        url="https://www.geoboundaries.org/countryDownloads.html",
        license="CC BY-SA 2.0, from OpenStreetMap",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes=(
            "The territories of village, settlement, and city councils from OpenStreetMap, named in Latin script, and "
            "the oblasts (ADM1). A council takes the village council of the same romanized name in the same oblast, "
            "where exactly one has it. The shapes are simplified to about 20 meters."
        ),
    )
    p.add(
        "source",
        id=COD_SOURCE,
        kind="official_body",
        publisher='State Scientific Production Enterprise "Kartographia", through OCHA',
        title="Ukraine - Subnational Administrative Boundaries (COD-AB), version 05",
        url="https://data.humdata.org/dataset/cod-ab-ukr",
        license="CC BY-IGO 3.0",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution='State Scientific Production Enterprise "Kartographia"; OCHA',
        notes=(
            "Level 3 holds the territorial communities of the 2020 plan. A community formed in 2015 or later takes the "
            "community of the same name in the same oblast, where exactly one has it; the outline is the community "
            "as the 2020 plan drew it, which can be larger than the community was when it first voted. The shapes "
            "are simplified to about 20 meters as one coverage."
        ),
    )
    for url in (old.OBLAST_SHAPES, old.COUNCIL_SHAPES):
        p.artifact(raw.cached(SOURCE, url), GB_SOURCE)
    p.artifact(raw.cached(SOURCE, base.SHAPES), COD_SOURCE)
    for scheme, vintage, start, end in (
        (S_OBLAST, "2015", FIRST_DAY, None),
        (S_COUNCIL, "2015", FIRST_DAY, base.PLAN),
        (S_COUNCIL, "2020", base.PLAN, None),
    ):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, vintage, "interim"),
            scheme_code=scheme,
            vintage=vintage,
            valid_from=start,
            valid_to=end,
            as_of=start,
        )
    outlines = oblast_shapes()
    used = {pl.region for pl in places().values()}
    for region in sorted(used, key=int):
        id = oblast_area(region)
        p.add("area", id=id, scheme_code=S_OBLAST)
        p.add("area_code", area_id=id, code=region)
        p.name(id, REGIONS[region], "uk", "official")
        outline = outlines.get(region)
        if outline is not None:
            geom, point = _wkb(outline)
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=FIRST_DAY,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="osm",
                source_id=GB_SOURCE,
            )
        u = unit(S_OBLAST, region)
        p.add(
            "reporting_unit",
            id=u,
            polity_id=UKRAINE,
            scheme_code=S_OBLAST,
            code=region,
            area_relation="equals",
            area_id=id,
            kind="aggregate",
        )
        p.name(u, REGIONS[region], "uk", "official")
    found = shapes()
    for code, place in sorted(places().items(), key=lambda item: (int(item[1].region), item[1].name, item[0])):
        id = council_area(code)
        p.add("area", id=id, scheme_code=S_COUNCIL)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=oblast_area(place.region))
        p.name(id, place.name, "uk", "official")
        outline = found.get(code)
        if outline is not None:
            geom, point = _wkb(outline.geometry)
            from_cod = outline.source == "cod"
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=base.PLAN if from_cod else FIRST_DAY,
                valid_to=None if from_cod else base.PLAN,
                geom=geom,
                label_point=point,
                quality="official" if from_cod else "osm",
                source_id=COD_SOURCE if from_cod else GB_SOURCE,
            )
        u = council_unit(code)
        p.add(
            "reporting_unit",
            id=u,
            polity_id=UKRAINE,
            scheme_code=S_COUNCIL,
            code=code,
            area_relation="equals",
            area_id=id,
            kind="aggregate",
        )
        p.name(u, place.name, "uk", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Elections

ELECTION_KIND = {
    "first": "special",
    "early": "special",
    "regular_late": "special",
    "repeat": "by_election",
    "interim": "by_election",
    "additional": "by_election",
    "runoff": "runoff",
}
LABELS = {
    "first": ("first elections of amalgamated communities", "Перші місцеві вибори"),
    "early": ("early elections", "Позачергові місцеві вибори"),
    "regular_late": ("voting of the regular elections held later", "Голосування з чергових виборів"),
    "repeat": ("repeat elections", "Повторні місцеві вибори"),
    "interim": ("by-elections", "Проміжні місцеві вибори"),
    "additional": ("additional elections", "Додаткові місцеві вибори"),
    "runoff": ("second round of the election of heads", "Повторне голосування"),
}
COUNCIL_METHOD = (
    "From the council's page on the Commission's results site, which prints the votes cast for each party's local "
    "organization and the seats each list won, and the deputies elected. The counts of voters and ballots are not "
    "published on the site, so the lists' votes are not summed against a valid total."
)
DISTRICT_METHOD = (
    "From the council's page on the Commission's results site, which names the deputy each district elected. The "
    "candidates' votes are not published on the site and are not stored."
)
HEAD_METHOD = (
    "From the Commission's page of the results of the election of the head, which prints the votes of every "
    "candidate. The counts of voters and ballots are not published on the site and are not stored."
)
SMALL_HEAD_METHOD = (
    "The Commission's results site names only the head elected. The votes of the candidates are not published on "
    "the site and are not stored."
)
STAROSTA_METHOD = (
    "From the Commission's pages of the starostas elected and of the districts in which they were elected, which "
    "name the starosta each district elected and the date it voted. The votes are not published on the site and are "
    "not stored."
)


@functools.cache
def slug_of(site: str, id: int) -> str:
    e = next(e for e in listed() if (e.site, e.id) == (site, id))
    same = sorted(x.id for x in listed() if (x.day, x.kind) == (e.day, e.kind) and (x.site, x.id) not in OWN_RUNOFFS)
    suffix = "" if same[0] == e.id else f"-{e.id}"
    return f"ua/{e.day}-local-{e.kind.replace('_', '-')}{suffix}"


def election_entity(p: Package, e: Election) -> base.Build:
    id = key("election", "interim", e.site, e.id)
    p.add(
        "election", id=id, polity_id=UKRAINE, kind=ELECTION_KIND[e.kind], date_from=e.day, date_to=e.day, status="held"
    )
    path = slug_of(e.site, e.id)
    p.add("slug", path=path, entity_id=id, is_current=True)
    english, native = LABELS[e.kind]
    p.name(id, f"Ukrainian local {english} of {e.day:%-d %B %Y}", "en", "official")
    p.name(id, f"{native} {e.day:%d.%m.%Y}", "uk", "native", False)
    return base.Build(p, id, path, source=CVK)


def council_body(c: Council) -> str:
    if c.region == KYIV:
        return body("kyiv")
    return body({"oblast": "oblast", "raion": "raion", "city_district": "city_district"}.get(c.tier, "hromada"))


def party_or_none(name: str, known: set[str]) -> str | None:
    return party_id(name) if norm(name) in known else None


@functools.cache
def rounds() -> tuple[dict[tuple[str, int, str], Election], dict[tuple[str, int, str], Election]]:
    """The first round each second round follows, and the second round each first round led to, by election and the
    code of the council whose head was elected."""
    last: dict[str, Election] = {}
    previous: dict[tuple[str, int, str], Election] = {}
    following: dict[tuple[str, int, str], Election] = {}
    for e in elections():
        for h in heads(e.site, e.id):
            code = code_of(h.region, h.label, h.council, h.rada)
            if e.kind == "runoff":
                if code in last:
                    previous[e.site, e.id, code] = last[code]
                    following[last[code].site, last[code].id, code] = e
            else:
                last[code] = e
    return previous, following


def _keys(rows: list[old.Candidate]) -> list[str]:
    seen: Counter[str] = Counter()
    found: list[str] = []
    for row in rows:
        seen[row.name] += 1
        found.append(row.name if seen[row.name] == 1 else f"{row.name}#{seen[row.name]}")
    return found


def has_results(e: Election) -> bool:
    return any((council_page(c.url).parties or council_page(c.url).deputies) for c in councils(e.site, e.id)) or any(
        h.name or (h.page and have(h.page) and candidates(h.page)) for h in heads(e.site, e.id)
    )


def build(e: Election) -> Package | None:
    if not has_results(e):
        return None
    depends = [GEO, REGISTRY]
    for h in heads(e.site, e.id):
        earlier = rounds()[0].get((e.site, e.id, code_of(h.region, h.label, h.council, h.rada)))
        if earlier is not None and has_results(earlier) and packages_for(earlier) not in depends:
            depends.append(packages_for(earlier))
    p = Package(
        packages_for(e),
        SOURCE,
        f"Ukraine's {LABELS[e.kind][0]} of {e.day:%-d %B %Y}: the councils with the deputies elected and the heads with "
        "the votes of their candidates where the Commission's results site prints them",
        depends=depends,
    )
    p.external.add(UKRAINE)
    for summary in summaries(e):
        if have(summary):
            p.artifact(raw.cached(SOURCE, summary), CVK)
    b = election_entity(p, e)
    known = set(nominators())
    unmatched = 0
    for c in sorted(councils(e.site, e.id), key=lambda c: (int(c.region), c.tier, c.label, c.name, int(c.rada))):
        page = council_page(c.url)
        if not page.parties and not page.deputies:
            continue
        p.artifact(raw.cached(SOURCE, c.url), CVK)
        tag = ("council", e.id, c.rada)
        by_list = page.by_list
        cid, rs = b.contest(
            tag,
            council_of(c.name),
            (c.name, c.label, region_name(c.region), c.rada),
            completeness=1.0 if by_list else 0.0,
            kind="list_allocation" if by_list else "body_seats",
            body_id=council_body(c),
            district_area_id=council_area(c.rada),
            magnitude=c.seats,
            round_number=1,
            electoral_system_id=system(e.day, "list" if by_list else "districts"),
        )
        if by_list:
            b.note(rs, "methodology", COUNCIL_METHOD)
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
                    party_id=party_or_none(row.name, known),
                    ballot_position=position,
                    ballot_label=row.name,
                )
                b.value(rs, council_unit(c.rada), option, row.votes)
                p.add(
                    "outcome", result_set_id=rs, option_id=option, kind="won" if row.seats else "lost", seats=row.seats
                )
            seen: set[tuple[str, str]] = set()
            for deputy in page.deputies:
                option = options.get(norm(deputy.nominator))
                if option is None:
                    unmatched += 1
                    continue
                if (deputy.name, deputy.born) in seen:
                    continue
                seen.add((deputy.name, deputy.born))
                person_tag = ("deputy", e.id, c.rada, deputy.name, deputy.born)
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
            if len({(d.name, d.born) for d in page.deputies}) != c.seats:
                b.note(
                    rs,
                    "caveat",
                    f"The Commission names {len({(d.name, d.born) for d in page.deputies})} deputies elected to the council's {c.seats} seats; the "
                    "others were not elected in this election or left no record on the site.",
                    "seats",
                )
            listed = {(d.name, d.born): d for d in page.deputies}.values()
            for position, deputy in enumerate(sorted(listed, key=lambda d: (d.district.zfill(4), d.name)), 1):
                person_tag = ("deputy", e.id, c.rada, deputy.name, deputy.born)
                option = key("option", *tag, *person_tag)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=cid,
                    kind="candidate",
                    party_id=party_or_none(deputy.nominator, known),
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
    for h in sorted(heads(e.site, e.id), key=lambda h: (int(h.region), h.label, h.council)):
        _head(b, e, h, known)
    if any(council_page(c.url).by_list for c in councils(e.site, e.id)):
        p.accept(
            "outcomes.list_members",
            "the Commission's pages list the deputies elected who still sit, and its separate list of deputies who "
            "left office names no list position, so a list can name fewer deputies than the seats it won; the seats "
            "are stored as the council's page prints them",
        )
    if unmatched:
        print(f"{p.key}: {unmatched} list deputies name a nominator without a list on their page", flush=True)
    return p


def _head(b: base.Build, e: Election, h: Head, known: set[str]) -> None:
    p = b.p
    code = code_of(h.region, h.label, h.council, h.rada)
    page = h.page if h.page and have(h.page) else None
    rows = candidates(page) if page else []
    if not rows and not h.name:
        return
    previous, following = rounds()
    earlier = previous.get((e.site, e.id, code))
    later = following.get((e.site, e.id, code))
    kyiv = h.region == KYIV
    tag = ("head", e.id, code)
    name = community(h.council)
    cid, rs = b.contest(
        tag,
        f"{name}: голова" + (", повторне голосування" if e.kind == "runoff" else "")
        if not kyiv
        else "Київський міський голова",
        (name, h.label, region_name(h.region), code) if not kyiv else ("Київський міський голова", str(e.id)),
        completeness=1.0 if rows else 0.0,
        kind="office",
        office_id=office("kyiv" if kyiv else "hromada"),
        district_area_id=council_area(code),
        magnitude=1,
        round_number=2 if e.kind == "runoff" else 1,
        previous_round_id=key("contest", *("head", earlier.id, code))
        if earlier is not None and has_results(earlier)
        else None,
        electoral_system_id=system(e.day, "two_round" if kyiv or e.kind == "runoff" or later else "plurality"),
    )
    if rows:
        p.artifact(raw.cached(SOURCE, page or ""), CVK)
        b.note(rs, "methodology", HEAD_METHOD)
    else:
        rows = [old.Candidate(h.name, h.nominator, 0)]
        b.note(rs, "methodology", SMALL_HEAD_METHOD)
    counted = any(row.votes for row in rows)
    options: dict[str, str] = {}
    for position, (who, row) in enumerate(zip(_keys(rows), rows, strict=True), 1):
        option = options[who] = key("option", *tag, who)
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="candidate",
            party_id=party_or_none(row.nominator, known),
            ballot_position=position,
            ballot_label=row.name,
        )
        person_tag = ("head", e.id, code, who)
        person = b.person(person_tag, row.name, h.born if row.name == h.name else "")
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
            b.value(rs, council_unit(code), option, row.votes)
    if later is not None and not h.name:
        advancing = {
            c.name
            for hh in heads(later.site, later.id)
            if code_of(hh.region, hh.label, hh.council, hh.rada) == code
            for c in (candidates(hh.page) if hh.page and have(hh.page) else [])
        }
        for who, row in zip(_keys(rows), rows, strict=True):
            kind = "advanced" if row.name in advancing else "eliminated"
            p.add("outcome", result_set_id=rs, option_id=options[who], kind=kind)
    elif h.name:
        if h.name not in options:
            raise ValueError(f"{h.council}: the head elected, {h.name}, is not among the candidates")
        for who, option in options.items():
            p.add("outcome", result_set_id=rs, option_id=option, kind="elected" if who == h.name else "lost")
    else:
        said = f"The Commission's list of the heads elected prints «{h.status}» and" if h.status else "The Commission"
        b.note(rs, "caveat", f"{said} names no head elected in this election.", "later")


def starosta_days() -> list[date]:
    return sorted({d.day for s in starosta_communities() for d in s.districts})


def starosta_package(day: date) -> Package:
    p = Package(
        f"{SOURCE}/starosta-{day}",
        SOURCE,
        f"Ukraine's elections of starostas of {day:%-d %B %Y}: the starosta each district of an amalgamated community "
        "elected, from the Central Election Commission",
        depends=[GEO, REGISTRY],
    )
    p.external.add(UKRAINE)
    id = key("election", "interim", "starosta", day)
    p.add("election", id=id, polity_id=UKRAINE, kind="special", date_from=day, date_to=day, status="held")
    path = f"ua/{day}-starosta"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"Ukrainian starosta elections of {day:%-d %B %Y}", "en", "official")
    p.name(id, f"Вибори старост {day:%d.%m.%Y}", "uk", "native", False)
    b = base.Build(p, id, path, source=CVK)
    known = set(nominators())
    p.artifact(raw.cached(SOURCE, STAROSTAS), CVK)
    for s in sorted(starosta_communities(), key=lambda s: (int(s.region), s.name, s.code)):
        todo = [d for d in s.districts if d.day == day]
        if not todo:
            continue
        for url in s.pages:
            p.artifact(raw.cached(SOURCE, url), CVK)
        for d in sorted(todo, key=lambda d: d.number):
            tag = ("starosta", s.code, d.number, day)
            cid, rs = b.contest(
                tag,
                f"{s.name}: староста, округ № {d.number}",
                (f"{s.name} starosta {d.number}", region_name(s.region), s.code),
                completeness=0.0,
                kind="office",
                office_id=office("starosta"),
                district_area_id=council_area(s.code),
                magnitude=1,
                round_number=1,
                electoral_system_id=system(day, "plurality"),
            )
            b.note(rs, "methodology", STAROSTA_METHOD)
            b.note(rs, "context", f"The district's villages: {d.description}.", "district")
            option = key("option", *tag, d.name)
            p.add(
                "contest_option",
                id=option,
                contest_id=cid,
                kind="candidate",
                party_id=party_or_none(d.nominator, known),
                ballot_position=1,
                ballot_label=d.name,
            )
            person = b.person(("starosta", s.code, d.number, day, d.name), d.name, d.born)
            p.add(
                "candidacy",
                id=key("candidacy", *tag, d.name),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=d.name,
                member_of_party_id=None,
                is_independent=norm(d.nominator) == SELF,
            )
            p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    return p


def packages() -> list[str]:
    found = [GEO, REGISTRY]
    found += [packages_for(e) for e in elections() if has_results(e)]
    found += [f"{SOURCE}/starosta-{day}" for day in starosta_days()]
    return found


def parse() -> None:
    geo().write()
    registry().write()
    for e in elections():
        p = build(e)
        if p is not None:
            p.write()
    for day in starosta_days():
        starosta_package(day).write()


# ---------------------------------------------------------------------------------------------------------------------
# Totals the Commission prints


def _figures(url: str, labels: dict[str, str]) -> dict[str, int]:
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", text(raw.cached(SOURCE, url))))
    found: dict[str, int] = {}
    for name, label in labels.items():
        hit = re.search(re.escape(label) + r"\s*:?\s*(?:\[[^\]]*\]\s*)?(\d+)", body)
        if hit:
            found[name] = int(hit.group(1))
    return found


COUNCIL_FIGURES = {
    "councils": "Загальна кількість місцевих рад, склад яких обирається на відповідних місцевих виборах",
    "reported": "Загальна кількість місцевих рад, щодо яких отримано повідомлення про результати виборів",
    "seats": "Загальна кількість депутатських мандатів у місцевих радах, склад яких обирається на відповідних місцевих виборах",
    "elected": "Загальна кількість обраних депутатів місцевих рад, повноваження яких не припинено",
}
HEAD_FIGURES = {
    "posts": "Всього посад міських, селищних, сільських голів",
    "reported": "Загальна кількість рад, щодо яких отримано повідомлення про результати виборів",
    "elected": "Обрано на посади міських, селищних, сільських голів",
}


def totals(e: Election) -> dict[str, int]:
    """The Commission's own totals for an election beside the counts this module read from its pages."""
    found: dict[str, int] = {}
    for url in summaries(e):
        if not have(url):
            continue
        if "/pvm002" in url:
            found |= {f"council_{k}": v for k, v in _figures(url, COUNCIL_FIGURES).items()}
        elif "/pvm003" in url:
            found |= {f"head_{k}": v for k, v in _figures(url, HEAD_FIGURES).items()}
    found["read_councils"] = len(councils(e.site, e.id))
    found["read_seats"] = sum(c.seats for c in councils(e.site, e.id))
    found["read_elected"] = sum(c.elected for c in councils(e.site, e.id))
    found["read_heads"] = sum(1 for h in heads(e.site, e.id) if h.name)
    return found
