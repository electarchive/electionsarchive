"""Ukraine's 1998 and 2002 parliamentary elections from the Central Election Commission's results sites.

Both sites are static copies of the commission's old information system. They publish the nationwide list and the 225
single-member districts per single-member district; they print the number of polling stations per district but no
station results, so the district is the finest reporting unit.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date
from functools import cache
from html import unescape
from urllib.parse import urljoin
from uuid import uuid5

import httpx
import shapely
from shapely.geometry import shape

from ea import package, raw
from ea.package import Package
from ea.sources.ua.cvk import tables

SOURCE = "ua.cvk_old"
BASE = "https://www.cvk.gov.ua/pls"
DELAY = 0.4


@dataclass(frozen=True)
class Election:
    site: str
    kodvib: int
    stamp: str  # the suffix the site puts on every page that takes no further query
    day: date

    def national(self, n: int) -> str:
        return f"{BASE}/{self.site}/webproc{n}v{self.stamp}.html?kodvib={self.kodvib}"


ELECTIONS = [
    Election("vnd1998", 1, "fc93", date(1998, 3, 29)),
    Election("vnd2002", 400, "b639", date(2002, 3, 31)),
]
# The national pages: the list's totals and seats, the district summary, the count of candidates (which links each
# district's candidates), and the districts where the vote was declared invalid.
NATIONAL = (15, 73, 75, 78)


# geoBoundaries' open set of the 27 first-level units, which have kept their outlines since before 1998
SHAPES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/UKR/ADM1/"
    "geoBoundaries-UKR-ADM1.geojson"
)
ISO = {
    "автономна республіка крим": "UA-43",
    "вінницька область": "UA-05",
    "волинська область": "UA-07",
    "дніпропетровська область": "UA-12",
    "донецька область": "UA-14",
    "житомирська область": "UA-18",
    "закарпатська область": "UA-21",
    "запорізька область": "UA-23",
    "івано-франківська область": "UA-26",
    "київська область": "UA-32",
    "кіровоградська область": "UA-35",
    "луганська область": "UA-09",
    "львівська область": "UA-46",
    "миколаївська область": "UA-48",
    "одеська область": "UA-51",
    "полтавська область": "UA-53",
    "рівненська область": "UA-56",
    "сумська область": "UA-59",
    "тернопільська область": "UA-61",
    "харківська область": "UA-63",
    "херсонська область": "UA-65",
    "хмельницька область": "UA-68",
    "черкаська область": "UA-71",
    "чернівецька область": "UA-77",
    "чернігівська область": "UA-74",
    "м.київ": "UA-30",
    "м.севастополь": "UA-40",
}


def get(url: str) -> raw.Artifact | None:
    """Fetch one page into the raw cache; a page the site does not serve is None."""
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    time.sleep(DELAY)
    try:
        return raw.fetch(SOURCE, url, suffix=".html")
    except httpx.HTTPStatusError as failure:
        if failure.response.status_code == 404:
            return None
        raise


def text(artifact: raw.Artifact) -> str:
    return artifact.path.read_bytes().decode("windows-1251", errors="replace")


def cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


def district_links(e: Election) -> dict[int, tuple[int, str]]:
    """Each district's region code and the suffix of its pages, from the page that summarizes every district."""
    url = e.national(73)
    artifact = cached(url)
    found: dict[int, tuple[int, str]] = {}
    if artifact is None:
        return found
    for href in re.findall(r'href\s*=\s*"([^"]+)"', text(artifact), re.IGNORECASE):
        link = unescape(urljoin(url, href))
        match = re.search(r"webproc69v([0-9a-f]+)(?:-\d+)?\.html\?kodvib=\d+&kodreg=(\d+)&kodokr=(\d+)", link)
        if match:
            found[int(match.group(3))] = (int(match.group(2)), match.group(1))
    return found


def candidate_links(e: Election) -> dict[int, str]:
    """The page of each district's candidates on the ballot, from the page that counts the candidates."""
    url = e.national(75)
    artifact = cached(url)
    found: dict[int, str] = {}
    if artifact is None:
        return found
    for href in re.findall(r'href\s*=\s*"([^"]+)"', text(artifact), re.IGNORECASE):
        link = unescape(urljoin(url, href))
        match = re.search(r"webproc1v[0-9a-f]+(?:-\d+)?\.html\?kodvib=\d+&kodreg=\d+&kodokr=(\d+)&rejim=2$", link)
        if match:
            found[int(match.group(1))] = link
    return found


def district_page(e: Election, name: int, region: int, district: int, stamp: str, extra: str = "") -> str:
    return f"{BASE}/{e.site}/webproc{name}v{stamp}.html?kodvib={e.kodvib}&kodreg={region}&kodokr={district}{extra}"


def pages(e: Election) -> list[str]:
    found = [e.national(n) for n in NATIONAL]
    for district, (region, stamp) in sorted(district_links(e).items()):
        found += [district_page(e, n, region, district, stamp) for n in (69, 97)]
    found += [url for _, url in sorted(candidate_links(e).items())]
    return found


def fetch() -> None:
    if cached(SHAPES) is None:
        raw.fetch(SOURCE, SHAPES, suffix=".geojson")
    for e in ELECTIONS:
        for url in e.national(73), *(e.national(n) for n in NATIONAL):
            get(url)
        for url in pages(e):
            get(url)
        print(f"{SOURCE}: {e.day} fetched", flush=True)


# ---------------------------------------------------------------------------------------------------------------------
# Parsing


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UKRAINE = str(uuid5(package.NAMESPACE, "polity\x1fUA"))
SOURCE_ID = key("source", SOURCE)
RADA = key("body", "rada")
LIST_SYSTEM = key("electoral_system", "rada-list-1998")
DISTRICT_SYSTEM = key("electoral_system", "rada-district-1998")
REGION_SCHEME = "ua.region_rada"
SEATS = 225
SELF_NOMINATED = "самовисування"
AGAINST = "Проти всіх"  # a ballot's line for a voter who supports no list or no candidate

METHOD = (
    "Counted by the Central Election Commission and published on its results site per single-member district, "
    "which is the finest level the site gives: it prints the number of polling stations in a district but no "
    "station results. Registered voters are the voters on the roll, voters who received a ballot and voters who took "
    "part are as printed, and invalid ballots are those declared invalid. Voters who supported no list or no "
    "candidate are stored as a 'none of the above' option. The site prints no total of valid ballots, so none is "
    "stored."
)
LIST_METHOD = (
    "225 seats are filled from one nationwide list. The votes are the district totals for each party or bloc, "
    "which the site prints for each of the 225 districts; the roll of registered voters is the one for the "
    "district. " + METHOD
)
DISTRICT_METHOD = (
    "One seat, filled by the candidate with the most votes. The site prints the voters on the roll only with the "
    "district's list results, so the district's registered voters are taken from them. " + METHOD
)


def clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def norm(value: str) -> str:
    """The key of a party across the two elections: its name without case, spacing, or quote style."""
    for old, new in (("’", "'"), ("ʼ", "'"), ("`", "'"), ("“", '"'), ("”", '"'), ("«", '"'), ("»", '"'), ("„", '"')):
        value = value.replace(old, new)
    return " ".join(value.casefold().split())


def squash(value: str) -> str:
    return re.sub(r"\W", "", value).casefold()


def number(value: str) -> int | None:
    digits = re.sub(r"[\s\xa0]", "", value)
    return int(digits) if digits.isdigit() else None


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


def scheme(e: Election) -> str:
    return f"ua.ovo_{e.day.year}"


def grids(url: str) -> list[list[list[str]]]:
    artifact = cached(url)
    if artifact is None:
        raise LookupError(f"{url} is not in the cache")
    return tables(artifact)


def find(found: list[list[list[str]]], first: str, url: str) -> list[list[str]]:
    """The table of a page whose header row starts with the text."""
    for table in found:
        if table and table[0] and table[0][0].startswith(first):
            return table
    raise ValueError(f"{url} holds no table that starts with {first!r}")


@dataclass(frozen=True)
class Summary:
    """One row of the district summary: the district's figures and its leading candidate."""

    region: str
    received: int
    voted: int
    invalid: int
    against: int
    leader: str
    votes: int
    nominator: str


@cache
def summary(e: Election) -> dict[int, Summary]:
    url = e.national(73)
    table = find(grids(url), "№ ВО", url)
    region = ""
    found: dict[int, Summary] = {}
    for row in table[2:]:
        if len(row) == 1:
            region = clean(row[0])
        elif len(row) >= 13 and row[0].isdigit():
            found[int(row[0])] = Summary(
                region,
                *(number(row[i]) or 0 for i in (1, 3, 5, 7)),
                clean(row[9]),
                number(row[10]) or 0,
                clean(row[12]),
            )
    if sorted(found) != [*range(1, SEATS + 1)]:
        raise ValueError(f"{url} does not summarize the districts 1 to {SEATS}")
    return found


@cache
def regions(e: Election) -> dict[int, tuple[int, str]]:
    """Each district's region code and name."""
    links = district_links(e)
    return {n: (links[n][0], summary(e)[n].region) for n in sorted(links)}


@dataclass(frozen=True)
class Candidate:
    name: str
    votes: int
    elected: bool


@dataclass(frozen=True)
class Sheet:
    district: int
    stations: int
    received: int
    voted: int
    invalid: int
    against: int
    candidates: list[Candidate]


def district_url(e: Election, name: int, n: int, extra: str = "") -> str:
    region, stamp = district_links(e)[n]
    return district_page(e, name, region, n, stamp, extra)


@cache
def smd(e: Election, n: int) -> Sheet:
    url = district_url(e, 69, n)
    found = grids(url)
    totals = {clean(row[0]): number(row[1]) for row in find(found, "Загальна", url) if len(row) > 1}
    table = find(found, "№", url)
    candidates: list[Candidate] = []
    for row in table[1:]:
        votes = number(row[2]) if len(row) > 2 else None
        if votes is None:
            continue
        cell = clean(row[1])
        name = re.split(r"\s(?:НЕ ОБРАНО|ОБРАНО|Не обрано)", cell)[0]
        candidates.append(Candidate(name, votes, re.search(r"(?<!НЕ )ОБРАНО", cell) is not None))
    values = list(totals.values())
    if len(values) != 5 or None in values:
        raise ValueError(f"{url} prints unexpected totals: {totals}")
    stations, received, voted, invalid, against = (int(v or 0) for v in values)
    return Sheet(n, stations, received, voted, invalid, against, candidates)


@dataclass(frozen=True)
class ListSheet:
    district: int
    registered: int
    received: int
    voted: int
    invalid: int
    against: int
    votes: dict[str, int]  # the votes of each party or bloc, by name


@cache
def lists(e: Election, n: int) -> ListSheet:
    url = district_url(e, 97, n)
    found = grids(url)
    rows = find(found, "Кількість виборців, внесених", url)
    values = [number(row[1]) for row in rows]
    if len(values) != 5 or None in values:
        raise ValueError(f"{url} prints unexpected totals")
    table = find(found, "№пп", url)
    votes = {clean(row[1]): number(row[2]) or 0 for row in table[1:] if len(row) > 2 and number(row[2]) is not None}
    registered, received, voted, invalid, against = (int(v or 0) for v in values)
    return ListSheet(n, registered, received, voted, invalid, against, votes)


@dataclass(frozen=True)
class Registered:
    name: str
    born: int | None
    nominator: str
    withdrawn: bool


@cache
def registered(e: Election, n: int) -> dict[str, Registered]:
    """The registered candidates of a district, by squashed name."""
    url = candidate_links(e)[n]
    table = find(grids(url), "Прізвище", url)
    found: dict[str, Registered] = {}
    for row in table[1:]:
        if len(row) < 3:
            continue
        born = re.search(r"(\d{4}) року народження", row[1])
        found[squash(row[0])] = Registered(
            clean(row[0]),
            int(born.group(1)) if born else None,
            clean(row[2]),
            len(row) > 3 and bool(re.search(r"\d", row[3])),
        )
    return found


@dataclass(frozen=True)
class National:
    registered: int
    received: int
    voted: int
    invalid: int
    against: int
    votes: dict[str, int]
    seats: dict[str, int]


@cache
def national(e: Election) -> National:
    url = e.national(15)
    found = grids(url)
    rows = find(found, "Кількість виборців, внесених", url)
    values = [number(row[1]) for row in rows]
    if len(values) != 5 or None in values:
        raise ValueError(f"{url} prints unexpected totals")
    table = find(found, "№пп", url)
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for row in table[1:]:
        won = number(row[2]) if len(row) > 2 else None
        if won is None:
            continue
        votes[clean(row[1])] = won
        if len(row) > 5 and number(row[5]):
            seats[clean(row[1])] = number(row[5]) or 0
    registered, received, voted, invalid, against = (int(v or 0) for v in values)
    return National(registered, received, voted, invalid, against, votes, seats)


def annulled(e: Election) -> dict[int, str]:
    """The districts whose election was declared invalid, with the result the site prints for each."""
    url = e.national(78)
    table = find(grids(url), "Округ", url)
    return {int(m.group(1)): clean(row[1]) for row in table[1:] if (m := re.match(r"ВО\s*№\s*(\d+)", row[0]))}


def party_names(e: Election) -> dict[str, str]:
    """Every party the district pages and the list print, by key."""
    found = {norm(name): name for name in national(e).votes}
    for n in district_links(e):
        for candidate in registered(e, n).values():
            if norm(candidate.nominator) != SELF_NOMINATED:
                found.setdefault(norm(candidate.nominator), candidate.nominator)
    return found


def party(name: str) -> str:
    return key("party", norm(name))


def districts(e: Election) -> list[int]:
    return sorted(district_links(e))


def region_shapes() -> dict[str, shapely.Geometry]:
    """The outline of each first-level unit by its ISO 3166-2 code, as a multipolygon in SRID 4326."""
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, SHAPES).path.read_bytes())["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            geometry = shapely.make_valid(geometry)
        if geometry.geom_type == "Polygon":
            geometry = shapely.MultiPolygon([geometry])
        found[feature["properties"]["shapeISO"]] = shapely.set_srid(geometry, 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The regions and the 225 single-member districts of Ukraine's 1998 and 2002 parliamentary elections as the "
        "Central Election Commission numbers them",
    )
    p.external.add(UKRAINE)
    for code, rank, kind, noun in (
        (REGION_SCHEME, 1, "administrative", "region"),
        *((scheme(e), 2, "electoral_district", "single-member district") for e in ELECTIONS),
    ):
        p.add("scheme", code=code, polity_id=UKRAINE, kind=kind, rank=rank, noun=noun, code_system=None)
    names: dict[int, str] = {}
    for e in ELECTIONS:
        for code, name in regions(e).values():
            names.setdefault(code, name)
    outlines = region_shapes()
    shapes_id = key("source", "geoboundaries")
    p.add(
        "source",
        id=shapes_id,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries Open: Ukraine ADM1",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries, William & Mary geoLab",
        notes="The first-level units of Ukraine, built from OpenStreetMap boundaries (release of 2023-12-12).",
    )
    p.artifact(raw.cached(SOURCE, SHAPES), shapes_id)
    p.add(
        "boundary_set",
        id=key("boundary_set", REGION_SCHEME, "2017"),
        scheme_code=REGION_SCHEME,
        vintage="2017",
        valid_from=date(1998, 1, 1),
        valid_to=None,
        as_of=date(2002, 3, 31),
    )
    for code, name in sorted(names.items()):
        id = area(REGION_SCHEME, code)
        p.add("area", id=id, scheme_code=REGION_SCHEME)
        p.add("area_code", area_id=id, code=str(code))
        p.name(id, name, "uk", "official")
        outline = outlines[ISO[norm(name)]]
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=date(1998, 1, 1),
            valid_to=None,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=shapes_id,
        )
    for e in ELECTIONS:
        for n, (code, _) in regions(e).items():
            id = area(scheme(e), n)
            p.add("area", id=id, scheme_code=scheme(e))
            p.add("area_code", area_id=id, code=str(n))
            p.add("area_parent", area_id=id, parent_id=area(REGION_SCHEME, code))
            p.name(id, f"Одномандатний виборчий округ № {n} ({e.day.year})", "uk", "official")
            p.name(id, f"Single-member district {n} ({e.day.year})", "en", "official", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Verkhovna Rada, the electoral systems of 1998 and 2002, and the parties and blocs that ran, from the "
        "Central Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(UKRAINE)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="Results of the 1998 and 2002 parliamentary elections, Central Election Commission of Ukraine",
        url="https://www.cvk.gov.ua/",
        license="Free reuse with the source cited, as the commission's results sites state",
        license_url="https://www.cvk.gov.ua/",
        attribution="Центральна виборча комісія",
    )
    p.add("body", id=RADA, polity_id=UKRAINE, kind="unicameral")
    p.name(RADA, "Verkhovna Rada", "en", "official")
    p.name(RADA, "Верховна Рада України", "uk", "native", False)
    for id, rules, method in (
        (
            LIST_SYSTEM,
            {
                "formula": "hare",
                "threshold": {"level": "list", "scope": "national", "percent": 4},
                "lists": {"order": "closed", "vacancy": "next_on_list"},
            },
            (
                "225 seats are allocated by largest remainder with the Hare quota among the national closed lists "
                "that reach 4% of the voters who took part."
            ),
        ),
        (
            DISTRICT_SYSTEM,
            {"formula": "fptp"},
            (
                "225 deputies are elected in single-member districts, each by a plurality of the votes. The "
                "commission declares the election in a district invalid where the law requires it, and no deputy "
                "is elected there."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=UKRAINE,
            valid_from=date(1998, 1, 1),
            valid_to=date(2006, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names: dict[str, str] = {}
    for e in ELECTIONS:
        names |= party_names(e)
    for code, text in sorted(names.items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=UKRAINE)
        p.add("external_id", entity_id=id, scheme="ua.cvk.party.1998", value=code)
        p.name(id, clean(text), "uk", "official")
    return p


def result_set(p: Package, e: Election, contest: str, name: str, method: str) -> str:
    id = key("result_set", e.day, name)
    p.add(
        "result_set",
        id=id,
        election_id=key("election", "rada", e.day),
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return id


def unit(p: Package, e: Election, units: dict[int, str], n: int) -> str:
    if n not in units:
        id = units[n] = key("unit", e.day, n)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=UKRAINE,
            scheme_code=scheme(e),
            code=f"{e.day}:{n}",
            area_relation="equals",
            area_id=area(scheme(e), n),
            kind="standard",
        )
    return units[n]


def value(p: Package, result_set: str, unit: str, option: str | None, measure: int, amount: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=amount,
    )


def election(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/rada-{e.day}",
        SOURCE,
        f"The {e.day} Ukrainian parliamentary election by single-member district from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(UKRAINE)
    id = key("election", "rada", e.day)
    p.add("election", id=id, polity_id=UKRAINE, kind="general", date_from=e.day, date_to=e.day, status="held")
    path = f"ua/{e.day}-general"
    p.name(id, f"{e.day.year} Ukrainian parliamentary election", "en", "official")
    p.name(id, f"Вибори народних депутатів України {e.day.year}", "uk", "native", False)
    p.add("slug", path=path, entity_id=id, is_current=True)
    for url in dict.fromkeys(pages(e)):
        artifact = cached(url)
        if artifact is not None:
            p.artifact(artifact, SOURCE_ID)
    numbers = districts(e)
    units: dict[int, str] = {}
    # The nationwide list
    total = national(e)
    contest = key("contest", "rada", e.day, "list")
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=RADA,
        magnitude=SEATS,
        round_number=1,
        electoral_system_id=LIST_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{e.day.year} Verkhovna Rada national list", "en", "official")
    p.name(contest, "Загальнодержавний багатомандатний виборчий округ", "uk", "native", False)
    p.add("slug", path=f"{path}/national-list", entity_id=contest, is_current=True)
    rs = result_set(p, e, contest, "list", LIST_METHOD)
    options: dict[str, str] = {}
    for name in sorted(total.votes, key=norm):
        option = options[name] = key("option", "rada", e.day, "list", norm(name))
        p.add("contest_option", id=option, contest_id=contest, kind="list", party_id=party(name), ballot_label=name)
        if name in total.seats:
            p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=total.seats[name])
        p.add("declared", result_set_id=rs, option_id=option, measure=1, value=total.votes[name])
    against = key("option", "rada", e.day, "list", "against")
    p.add("contest_option", id=against, contest_id=contest, kind="none_of_the_above", ballot_label=AGAINST)
    p.add("declared", result_set_id=rs, option_id=against, measure=1, value=total.against)
    for measure, amount in ((2, total.registered), (14, total.received), (3, total.voted), (5, total.invalid)):
        p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=amount)
    for n in numbers:
        sheet = lists(e, n)
        u = unit(p, e, units, n)
        for measure, amount in ((2, sheet.registered), (14, sheet.received), (3, sheet.voted), (5, sheet.invalid)):
            value(p, rs, u, None, measure, amount)
        value(p, rs, u, against, 1, sheet.against)
        for name, votes in sheet.votes.items():
            value(p, rs, u, options[name], 1, votes)
    # The single-member districts
    invalid = annulled(e)
    for n in numbers:
        sheet = smd(e, n)
        contest = key("contest", "rada", e.day, "district", n)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=RADA,
            district_area_id=area(scheme(e), n),
            magnitude=1,
            round_number=1,
            electoral_system_id=DISTRICT_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Single-member district {n}", "en", "official")
        p.name(contest, f"Одномандатний виборчий округ № {n}", "uk", "native", False)
        p.add("slug", path=f"{path}/district-{n}", entity_id=contest, is_current=True)
        rs = result_set(p, e, contest, f"district-{n}", DISTRICT_METHOD)
        if n in invalid:
            p.add(
                "note",
                id=key("note", rs, "invalid"),
                entity_id=rs,
                kind="caveat",
                body_md=f"The commission declared the election in this district invalid, and no deputy was elected. "
                f"The site prints: {invalid[n].rstrip('.')}.",
                lang="en",
            )
        known = registered(e, n)
        u = unit(p, e, units, n)
        for measure, amount in (
            (2, lists(e, n).registered),
            (14, sheet.received),
            (3, sheet.voted),
            (5, sheet.invalid),
        ):
            value(p, rs, u, None, measure, amount)
        none = key("option", "rada", e.day, "district", n, "against")
        p.add("contest_option", id=none, contest_id=contest, kind="none_of_the_above", ballot_label=AGAINST)
        value(p, rs, u, none, 1, sheet.against)
        for candidate in sheet.candidates:
            detail = known.get(squash(candidate.name))
            if detail is None:
                raise ValueError(f"{e.site} district {n}: {candidate.name} is not among the registered candidates")
            option = key("option", "rada", e.day, "district", n, squash(candidate.name))
            p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate.name)
            person = key("person", "rada", e.day, n, squash(candidate.name))
            p.add(
                "person",
                id=person,
                birth_date=date(detail.born, 1, 1) if detail.born else None,
                birth_precision="year" if detail.born else None,
            )
            p.name(person, candidate.name, "uk", "official")
            independent = norm(detail.nominator) == SELF_NOMINATED
            p.add(
                "candidacy",
                id=key("candidacy", "rada", e.day, n, squash(candidate.name)),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=None if independent else party(detail.nominator),
                is_independent=independent,
            )
            value(p, rs, u, option, 1, candidate.votes)
            if candidate.elected:
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/rada-{e.day}" for e in ELECTIONS)]


def emit() -> None:
    package.run("load", packages())
