"""The Philippines' Commission on Elections (COMELEC): the first regular election of the Bangsamoro Parliament, held
on 14 September 2026, by clustered precinct and by city or municipality, from the results site the Commission ran
for it.

The site serves one file per clustered precinct (the electronic return), one per city, municipality, province, and
the region (the certificates of canvass), and the place tree. A person saved those files as two JSON bundles keyed
by the site's own paths, because the site refuses scripted downloads; this module reads them from
`data/inbox/manual/ph.comelec_barmm/` and keeps them in the raw cache under the URLs the bundles came from.

The ballot held three kinds of contest: the regional parliamentary political party vote for the 40 party
representative seats, one district representative in each of the 32 parliamentary districts, and five sectoral
representative contests (two seats for settler communities, one each for women, youth, the Ulama, and traditional
leaders). The city and municipal canvasses are complete and add up exactly to the provincial and regional ones;
the returns cover 5,175 of the 5,212 clustered precincts. The site prints no proclamation, so the seats each party
and candidate won are not recorded here.

The outlines of the cities and municipalities are the 2020 boundaries of the national mapping agency as
geoBoundaries redistributes them; provinces and parliamentary districts made of whole municipalities take the union
of those outlines. The eight municipalities of the Special Geographic Area, created in 2024, have no outline there.
"""

import functools
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ph.comelec_barmm"
GEOSOURCE = "ph.comelec_barmm.geoboundaries"
INBOX = DATA / "inbox" / "manual" / SOURCE
PHILIPPINES = str(uuid5(package.NAMESPACE, "polity\x1fPH"))
BANGSAMORO = str(uuid5(package.NAMESPACE, "polity\x1fPH-14"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPHL"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

SITE = "https://barmmpelectionresults.comelec.gov.ph/"
BUNDLE = "https://barmmpelectionresults.comelec.gov.ph/data/"
RETURNS = "https://barmmpelectionresults.comelec.gov.ph/data/er/<first"
DASHBOARD = "https://barmmpelectionresults.comelec.gov.ph/dashboard"
SAVED = (BUNDLE, RETURNS, DASHBOARD)
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/"
ADM2 = GEOBOUNDARIES + "41af8f1/releaseData/gbOpen/PHL/ADM2/geoBoundaries-PHL-ADM2_simplified.geojson"
ADM3 = GEOBOUNDARIES + "9469f09/releaseData/gbOpen/PHL/ADM3/geoBoundaries-PHL-ADM3_simplified.geojson"
DAY = date(2026, 9, 14)
VINTAGE = date(2020, 1, 1)
REGION = "R0BARMM"
RBOC = "9706000"  # the regional board of canvassers: its certificate covers the whole region
EXPECTED_RETURNS = 5212
PARTY_CONTEST = "01295000"
# The geoBoundaries provinces whose municipalities make up the region's provinces (Maguindanao was divided in 2022).
OUTLINES = {
    "0700000": ("Basilan",),
    "3600000": ("Lanao del Sur",),
    "7000000": ("Tawi-Tawi",),
    "8700000": ("Maguindanao", "Cotabato City"),
    "8800000": ("Maguindanao",),
}
# Municipalities the 2020 outlines name differently: Bumbaran was renamed Amai Manabilang in 2015.
ALIASES = {"AMAI MANABILANG": "Bumbaran", "TAGOLOAN": "Tagoloan II"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


RESULTS_SOURCE = key("source", "results")
GEO_SOURCE = key("source", "geoboundaries")
REGION_AREA = area("ph.barmm_region", REGION)


# ---------------------------------------------------------------------------------------------------------------
# Fetching


def fetch() -> None:
    """Add the saved bundles to the raw cache under the URLs they came from, and fetch the outlines."""
    index = json.loads((INBOX / "index.json").read_text())
    for url in SAVED:
        entry = index[url]
        file = INBOX / entry["file"]
        content = file.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{file} does not match its recorded hash")
        raw.store(
            SOURCE,
            entry.get("source", url),
            content,
            suffix=file.suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
    for url in (ADM2, ADM3):
        raw.fetch(GEOSOURCE, url, suffix=".geojson")


def _source_url(url: str) -> str:
    index = json.loads((INBOX / "index.json").read_text()) if (INBOX / "index.json").exists() else {}
    return str(index.get(url, {}).get("source", url))


def _artifact(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, _source_url(url))


@functools.cache
def files(url: str) -> dict[str, Any]:
    return json.loads(_artifact(url).path.read_bytes())["files"]


def site() -> dict[str, Any]:
    return files(BUNDLE)


def returns() -> dict[str, Any]:
    return files(RETURNS)


# ---------------------------------------------------------------------------------------------------------------
# Names

SMALL = {"of", "del", "de", "sa", "the", "and", "for", "to"}
ORDINALS = ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def title(text: str) -> str:
    words = []
    for index, word in enumerate(clean(text).lower().split()):
        if word in SMALL and index:
            words.append(word)
        else:
            words.append(re.sub(r"(^|[-'/(.])([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), word))
    return " ".join(words)


def number(text: str) -> tuple[int | None, str]:
    """Split a ballot number off a ballot line: "13. UBJP" or "2. GANDAMRA, DOC PALA (SIAP)"."""
    match = re.match(r"^(\d+)\.?\s+(.*)$", text.strip())
    return (int(match.group(1)), clean(match.group(2))) if match else (None, clean(text))


BALLOT = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def candidate(text: str) -> tuple[str, str | None]:
    """Split "GANDAMRA, DOC PALA (SIAP)" into the name and the party abbreviation."""
    match = BALLOT.match(text)
    if match is None:
        return clean(text), None
    return clean(match.group(1)), clean(match.group(2)).upper() or None


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def fold(name: str) -> str:
    """Compare a place name with a geoBoundaries one: no punctuation or spacing, and no "City of"."""
    text = re.sub(r"\(.*?\)", " ", name.upper())
    text = re.sub(r"\bCITY OF\b|\bCITY\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", "", text)


# ---------------------------------------------------------------------------------------------------------------
# Places


class Place(NamedTuple):
    scheme: str
    code: str
    name: str
    parent: tuple[str, str] | None


@functools.cache
def tree() -> dict[tuple[str, str], Place]:
    """The region, its provinces (with the Special Geographic Area), cities and municipalities, and barangays."""
    data = site()
    found: dict[tuple[str, str], Place] = {}
    regions = [r for r in data["/data/regions/local/0.json"]["regions"] if r["code"] == REGION]
    if len(regions) != 1:
        raise ValueError("the site lists no single BARMM region")
    found["ph.barmm_region", REGION] = Place("ph.barmm_region", REGION, regions[0]["name"], None)
    for province in data[f"/data/regions/local/{REGION}.json"]["regions"]:
        code = province["code"]
        found["ph.barmm_province", code] = Place(
            "ph.barmm_province", code, province["name"], ("ph.barmm_region", REGION)
        )
        for city in data[f"/data/regions/local/{code}.json"]["regions"]:
            found["ph.barmm_city", city["code"]] = Place(
                "ph.barmm_city", city["code"], city["name"], ("ph.barmm_province", code)
            )
            for barangay in data[f"/data/regions/local/{city['code']}.json"]["regions"]:
                found["ph.barmm_barangay", barangay["code"]] = Place(
                    "ph.barmm_barangay", barangay["code"], barangay["name"], ("ph.barmm_city", city["code"])
                )
    return found


@functools.cache
def precincts() -> dict[str, str]:
    """The barangay of each clustered precinct the site lists."""
    found: dict[str, str] = {}
    for path, body in site().items():
        if not path.startswith("/data/regions/precinct/"):
            continue
        barangay = path.rsplit("/", 1)[1].removesuffix(".json")
        if ("ph.barmm_barangay", barangay) not in tree():
            raise ValueError(f"precinct list {path} names no barangay in the tree")
        for item in body["regions"]:
            if item["code"] in found:
                raise ValueError(f"precinct {item['code']} is listed twice")
            found[item["code"]] = barangay
    if len(found) != EXPECTED_RETURNS:
        raise ValueError(f"{len(found)} clustered precincts listed, not {EXPECTED_RETURNS}")
    return found


def city_of(barangay: str) -> str:
    parent = tree()["ph.barmm_barangay", barangay].parent
    assert parent is not None
    return parent[1]


DISTRICT = re.compile(
    r"^LEGISLATIVE DISTRICT REPRESENTATIVE TO THE BANGSAMORO PARLIAMENT of BARMM - (.+?) -\s+(\w+) PARLIAMENTARY "
    r"DISTRICT$"
)
SECTORAL = re.compile(r"^PARLIAMENTARY SECTORAL REPRESENTATIVE FOR (.+) of BARMM$")


class Contest(NamedTuple):
    code: str
    kind: str  # party, district, or sectoral
    printed: str
    label: str
    magnitude: int
    votes_per_ballot: int
    district: tuple[str, str] | None  # (place name as printed, ordinal) for a district contest


def _province_code(name: str) -> tuple[str, str]:
    """The parent of a district: its province, or the City of Cotabato for its own three districts."""
    for (scheme, code), place in tree().items():
        if scheme in ("ph.barmm_province", "ph.barmm_city") and place.name == name:
            return scheme, code
    raise ValueError(f"no place named {name}")


@functools.cache
def contests() -> dict[str, Contest]:
    region = site()[f"/data/coc/{RBOC}.json"]["local"]
    registered = _region_registered()
    found: dict[str, Contest] = {}
    for item in region:
        found[item["contestCode"]] = _contest(item, registered)
    for path, body in site().items():
        if path.startswith("/data/coc/"):
            for item in body["local"]:
                if item["contestCode"] not in found:
                    found[item["contestCode"]] = _contest(item, None)
    return dict(sorted(found.items()))


def _region_registered() -> int:
    """The region's registered voters: the "valid votes" the canvass prints for the one-vote party contest."""
    for item in site()[f"/data/coc/{RBOC}.json"]["local"]:
        if item["contestCode"] == PARTY_CONTEST:
            return int(item["statistic"]["validVotes"])
    raise ValueError("the regional canvass holds no party contest")


def _contest(item: dict[str, Any], registered: int | None) -> Contest:
    code, printed = item["contestCode"], clean(item["contestName"])
    if code == PARTY_CONTEST:
        return Contest(code, "party", printed, "Party representatives", 40, 1, None)
    match = DISTRICT.match(printed)
    if match:
        place, ordinal = clean(match.group(1)), match.group(2).upper()
        if ordinal not in ORDINALS:
            raise ValueError(f"unknown district ordinal in {printed}")
        return Contest(
            code, "district", printed, f"{title(place)} {title(ordinal)} Parliamentary District", 1, 1, (place, ordinal)
        )
    match = SECTORAL.match(printed)
    if match and registered:
        per, remainder = divmod(int(item["statistic"]["validVotes"]), registered)
        if remainder or per not in (1, 2):
            raise ValueError(f"{printed}: {item['statistic']['validVotes']} is not a multiple of the voters")
        sector = clean(match.group(1)).lower().replace("ulama", "Ulama")
        return Contest(code, "sectoral", printed, f"Sectoral representative for {sector}", per, per, None)
    raise ValueError(f"unknown contest: {printed}")


def district_area(contest: Contest) -> str:
    return area("ph.barmm_district", contest.code)


@functools.cache
def district_units() -> dict[str, Counter[str]]:
    """For each district contest, the cities its returns come from, with the number of returns."""
    found: dict[str, Counter[str]] = defaultdict(Counter)
    for path, body in returns().items():
        code = path.rsplit("/", 1)[1].removesuffix(".json")
        for item in body["local"]:
            if contests()[item["contestCode"]].kind == "district":
                found[item["contestCode"]][city_of(precincts()[code])] += 1
    return found


# ---------------------------------------------------------------------------------------------------------------
# Shapes


def _features(url: str) -> list[tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(GEOSOURCE, url).path.read_text(encoding="utf-8"))
    return [(f["properties"]["shapeName"], shapely.geometry.shape(f["geometry"])) for f in data["features"]]


def multipolygon(geometry: shapely.Geometry) -> shapely.Geometry:
    geometry = shapely.make_valid(geometry)
    if geometry.geom_type == "GeometryCollection":
        geometry = shapely.union_all([g for g in geometry.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    if geometry.geom_type == "Polygon":
        return shapely.MultiPolygon([geometry])
    return geometry


@functools.cache
def shapes() -> dict[tuple[str, str], shapely.Geometry]:
    """The outline of each city and municipality, and of each province and district made of whole ones."""
    provinces = _features(ADM2)
    towns = _features(ADM3)
    found: dict[tuple[str, str], shapely.Geometry] = {}
    for (scheme, code), place in tree().items():
        if scheme != "ph.barmm_city":
            continue
        assert place.parent is not None
        names = OUTLINES.get(place.parent[1])
        if names is None:
            continue
        outer = shapely.union_all([shape for name, shape in provinces if name in names])
        inside = [
            shape
            for name, shape in towns
            if fold(name) == fold(ALIASES.get(place.name, place.name))
            and outer.contains(shapely.point_on_surface(shape))
        ]
        if len(inside) != 1:
            raise ValueError(f"{place.name}: {len(inside)} outlines")
        found[scheme, code] = inside[0]
    for (scheme, code), place in tree().items():
        if scheme != "ph.barmm_province":
            continue
        cities = [c for (s, c), p in tree().items() if s == "ph.barmm_city" and p.parent == (scheme, code)]
        if cities and all(("ph.barmm_city", c) in found for c in cities):
            found[scheme, code] = shapely.union_all([found["ph.barmm_city", c] for c in cities])
    # A district takes the union of its cities only when every return of each of those cities votes in it.
    totals: Counter[str] = Counter()
    for counted in district_units().values():
        totals.update(counted)
    for contest_code, counted in district_units().items():
        whole = all(totals[city] == n for city, n in counted.items())
        if whole and all(("ph.barmm_city", city) in found for city in counted):
            found["ph.barmm_district", contest_code] = shapely.union_all(
                [found["ph.barmm_city", city] for city in counted]
            )
    return found


# ---------------------------------------------------------------------------------------------------------------
# Packages


def _results_source(p: Package) -> None:
    p.add(
        "source",
        id=RESULTS_SOURCE,
        kind="official_body",
        publisher="Commission on Elections",
        title="2026 BARMM Parliamentary Elections results",
        url=SITE,
        license=None,
        license_url=None,
        attribution="Commission on Elections",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Bangsamoro Autonomous Region in Muslim Mindanao as a polity, with its provinces, cities and "
        "municipalities, barangays, and 32 parliamentary districts as the Commission on Elections lists them for "
        "the 2026 parliamentary election, and the outlines of those made of the 2020 municipalities",
    )
    p.external |= {PHILIPPINES, COUNTRY}
    p.uses(_artifact(BUNDLE))
    p.uses(_artifact(RETURNS))
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (NAMRIA, PSA, and OCHA Philippines boundaries)",
        title="geoBoundaries gbOpen Philippines ADM2 and ADM3, 2020",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO (the national mapping agency's boundaries, as geoBoundaries redistributes them)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab), from NAMRIA, PSA, and OCHA Philippines",
    )
    for url in (ADM2, ADM3):
        p.artifact(raw.cached(GEOSOURCE, url), GEO_SOURCE)
    p.add("polity", id=BANGSAMORO, kind="region", iso_code="PH-14")
    p.add("polity_parent", polity_id=BANGSAMORO, parent_id=PHILIPPINES)
    p.add("polity_territory", polity_id=BANGSAMORO, area_id=REGION_AREA)
    p.name(BANGSAMORO, "Bangsamoro Autonomous Region in Muslim Mindanao", "en", "official")
    p.name(BANGSAMORO, "BARMM", "en", "abbreviation", False)
    p.name(BANGSAMORO, "Bangsamoro", "en", "short", False)
    for code, rank, noun, kind, polity in (
        ("ph.barmm_region", 1, "autonomous region", "administrative", PHILIPPINES),
        ("ph.barmm_province", 2, "province", "administrative", PHILIPPINES),
        ("ph.barmm_city", 3, "city or municipality", "administrative", PHILIPPINES),
        ("ph.barmm_barangay", 4, "barangay", "administrative", PHILIPPINES),
        ("ph.barmm_district", 5, "parliamentary district", "electoral_district", BANGSAMORO),
    ):
        p.add("scheme", code=code, polity_id=polity, kind=kind, rank=rank, noun=noun)
    p.add(
        "boundary_set",
        id=key("boundary_set", "ph.barmm_city", "2020"),
        scheme_code="ph.barmm_city",
        vintage="2020",
        valid_from=VINTAGE,
        as_of=VINTAGE,
    )
    for scheme in ("ph.barmm_province", "ph.barmm_district"):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2026"),
            scheme_code=scheme,
            vintage="2026",
            valid_from=DAY,
            as_of=DAY,
        )
    geometries = shapes()
    for (scheme, code), place in sorted(tree().items()):
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area(*place.parent) if place.parent else COUNTRY)
        p.name(id, clean(place.name), "fil", "official")
    for contest in contests().values():
        if contest.kind != "district":
            continue
        assert contest.district is not None
        id = district_area(contest)
        p.add("area", id=id, scheme_code="ph.barmm_district")
        p.add("area_code", area_id=id, code=contest.code)
        p.add("area_parent", area_id=id, parent_id=area(*_province_code(contest.district[0])))
        p.name(id, contest.label, "en", "official")
    for (scheme, code), shape in sorted(geometries.items(), key=lambda item: item[0]):
        id = district_area(contests()[code]) if scheme == "ph.barmm_district" else area(scheme, code)
        outline = multipolygon(shape)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=VINTAGE if scheme == "ph.barmm_city" else DAY,
            geom=shapely.to_wkb(shapely.set_srid(outline, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=GEO_SOURCE,
        )
    return p


PARLIAMENT = key("body", "parliament")
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
SYSTEMS = (
    (
        FPTP,
        {"formula": "fptp"},
        "Each voter casts one vote for one candidate, and the candidate with the most votes is elected.",
    ),
    (
        BLOCK,
        {"formula": "block"},
        (
            "Each voter casts as many votes as there are seats, at most one per candidate, and the candidates with "
            "the most votes fill the seats. A ballot with more votes than seats in a contest is an overvote and "
            "counts for no candidate in that contest."
        ),
    ),
)
INDEPENDENT = {"IND", "INDEPENDENT"}


def party_key(text: str) -> str:
    return clean(text).upper()


def party(name: str) -> str:
    return key("party", party_key(name))


@functools.cache
def parties() -> set[str]:
    """Every party the ballots name: the party contest's lines and the abbreviations after candidates' names."""
    found: set[str] = set()
    for item in site()[f"/data/coc/{RBOC}.json"]["local"]:
        for line in item["candidates"]["candidates"]:
            _, text = number(line["name"])
            if item["contestCode"] == PARTY_CONTEST:
                found.add(party_key(text))
            else:
                abbreviation = candidate(text)[1]
                if abbreviation and abbreviation not in INDEPENDENT:
                    found.add(abbreviation)
    for path, body in site().items():
        if path.startswith("/data/coc/"):
            for item in body["local"]:
                if item["contestCode"] == PARTY_CONTEST:
                    continue
                for line in item["candidates"]["candidates"]:
                    abbreviation = candidate(number(line["name"])[1])[1]
                    if abbreviation and abbreviation not in INDEPENDENT:
                        found.add(abbreviation)
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Bangsamoro Parliament, its electoral systems, and the parties on the 2026 parliamentary ballot",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PHILIPPINES}
    p.uses(_artifact(BUNDLE))
    p.add("body", id=PARLIAMENT, polity_id=BANGSAMORO, kind="unicameral")
    p.name(PARLIAMENT, "Bangsamoro Parliament", "en", "official")
    p.name(PARLIAMENT, "Parlamento ng Bangsamoro", "fil", "native", False)
    for id, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=BANGSAMORO,
            valid_from=None,
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in sorted(parties()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=BANGSAMORO)
        p.name(id, name, "fil", "official" if " " in name else "abbreviation")
    return p


# ---------------------------------------------------------------------------------------------------------------
# The election


METHOD = (
    "Counted {grain}, as the Commission on Elections published {what} on its results site for the 2026 BARMM "
    "parliamentary elections. A clustered precinct is the group of precincts that share one counting machine. "
    "Registered voters and ballots cast come from the {source}; valid votes are the votes given to candidates or "
    "parties, invalid ballots are the ballots overvoted in the contest, and blank ballots are the ballots with no "
    "vote in it. In the contest for the two settler-community seats each ballot carries two votes, so valid votes "
    "and blank ballots are left out there and only each candidate's votes are stored. The proclamations of the "
    "boards of canvassers are not on the site and are not recorded here."
)
PARTY_NOTE = (
    "Voters cast one vote for a regional parliamentary political party. The 40 party representative seats are "
    "shared among the parties that pass the threshold of the Bangsamoro Electoral Code, from the closed lists of "
    "nominees each party filed. Moro Ako stayed on the ballot after the Commission cancelled its registration "
    "(Resolution No. 11271, 8 September 2026); its votes are stored as the canvass prints them."
)


def as_of() -> datetime:
    stamp = site()["/data/common/latestTime.json"]
    local = datetime.strptime(f"{stamp['date']} {stamp['time']}", "%d %b %Y %I:%M:%S %p")
    return local.replace(tzinfo=UTC) - timedelta(hours=8)


class Line(NamedTuple):
    id: str
    text: str
    ordinal: int | None
    name: str
    party: str | None


@functools.cache
def lines() -> dict[str, list[Line]]:
    """The ballot lines of each contest, in ballot order, from the regional canvass and the city canvasses."""
    found: dict[str, dict[str, Line]] = defaultdict(dict)
    for path, body in site().items():
        if not path.startswith("/data/coc/"):
            continue
        for item in body["local"]:
            code = item["contestCode"]
            for entry in item["candidates"]["candidates"]:
                printed = entry["name"]
                if printed in found[code]:
                    continue
                ordinal, text = number(printed)
                if code == PARTY_CONTEST:
                    name, abbreviation = party_key(text), party_key(text)
                else:
                    name, abbreviation = candidate(text)
                found[code][printed] = Line(key("option", code, printed), text, ordinal, name, abbreviation)
    return {code: sorted(found[code].values(), key=lambda line: line.ordinal or 0) for code in sorted(found)}


def _option(code: str, printed: str) -> str:
    return key("option", code, printed)


class Tally:
    """The values of one result set, keyed by unit, option, and measure."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str | None, int], int] = {}

    def add(self, unit: str, option: str | None, measure: int, value: int) -> None:
        slot = (unit, option, measure)
        if slot in self.values:
            raise ValueError(f"{slot} is counted twice")
        self.values[slot] = value


def _statistics(
    tally: Tally, unit: str, contest: Contest, stat: dict[str, int], registered: int, cast: int, odd: Counter[str]
) -> None:
    per = contest.votes_per_ballot
    obtained, over, under = stat["obtainedVotes"], stat["overVotes"], stat["underVotes"]
    if obtained + over + under != per * cast:
        odd[f"{contest.code}: votes do not add to the ballots"] += 1
    tally.add(unit, None, REGISTERED, registered)
    tally.add(unit, None, CAST, cast)
    if over % per == 0:
        tally.add(unit, None, INVALID, over // per)
    if per == 1:
        tally.add(unit, None, VALID, obtained)
        tally.add(unit, None, BLANK, under)


def _votes(tally: Tally, unit: str, contest: Contest, item: dict[str, Any]) -> int:
    total = 0
    for entry in item["candidates"]["candidates"]:
        tally.add(unit, _option(contest.code, entry["name"]), VOTES, int(entry["votes"]))
        total += int(entry["votes"])
    if total != item["statistic"]["obtainedVotes"]:
        raise ValueError(f"{contest.code}: candidate votes {total} differ from {item['statistic']['obtainedVotes']}")
    return total


def city_unit(code: str) -> str:
    return key("unit", DAY, "city", code)


def precinct_unit(code: str) -> str:
    return key("unit", DAY, "precinct", code)


@functools.cache
def canvass() -> dict[str, Tally]:
    """The city and municipal certificates of canvass, checked against the provincial and regional ones."""
    tallies: dict[str, Tally] = defaultdict(Tally)
    sums: dict[str, Counter[str]] = defaultdict(Counter)
    odd: Counter[str] = Counter()
    cities = [code for (scheme, code) in tree() if scheme == "ph.barmm_city"]
    for code in cities:
        for item in site()[f"/data/coc/{code}.json"]["local"]:
            contest = contests()[item["contestCode"]]
            stat = item["statistic"]
            per = contest.votes_per_ballot
            registered, remainder = divmod(stat["validVotes"], per)
            cast, extra = divmod(stat["obtainedVotes"] + stat["overVotes"] + stat["underVotes"], per)
            if remainder or extra:
                odd[f"{contest.code}: the canvass is not a whole number of ballots"] += 1
            _statistics(tallies[contest.code], city_unit(code), contest, stat, registered, cast, odd)
            _votes(tallies[contest.code], city_unit(code), contest, item)
            for name, value in stat.items():
                sums[contest.code][name] += value
            for entry in item["candidates"]["candidates"]:
                sums[contest.code][entry["name"]] += entry["votes"]
    if odd:
        raise ValueError(f"the canvasses do not balance: {dict(odd)}")
    upper = [code for (scheme, code) in tree() if scheme == "ph.barmm_province"]
    for scope in ([RBOC], upper):
        printed: dict[str, Counter[str]] = defaultdict(Counter)
        for code in scope:
            for item in site()[f"/data/coc/{code}.json"]["local"]:
                for name, value in item["statistic"].items():
                    printed[item["contestCode"]][name] += value
                for entry in item["candidates"]["candidates"]:
                    printed[item["contestCode"]][entry["name"]] += entry["votes"]
        for contest_code, counted in printed.items():
            if counted != sums[contest_code]:
                raise ValueError(f"{contest_code}: the city canvasses differ from the canvass of {scope}")
    return tallies


class Returns(NamedTuple):
    tallies: dict[str, Tally]
    units: dict[str, dict[str, Any]]  # precinct code -> its information block
    districts: dict[str, set[str]]  # precinct code -> the district contests it votes in
    odd: Counter[str]


@functools.cache
def precinct_returns() -> Returns:
    tallies: dict[str, Tally] = defaultdict(Tally)
    units: dict[str, dict[str, Any]] = {}
    districts: dict[str, set[str]] = defaultdict(set)
    odd: Counter[str] = Counter()
    for path, body in sorted(returns().items()):
        code = path.rsplit("/", 1)[1].removesuffix(".json")
        if code not in precincts():
            raise ValueError(f"return {code} is for no listed precinct")
        info = body["information"]
        if info["precinctId"] != code:
            raise ValueError(f"return {path} is for precinct {info['precinctId']}")
        units[code] = info
        registered, cast = int(info["numberOfRegisteredVoters"]), int(info["numberOfActuallyVoters"])
        for item in body["local"]:
            contest = contests()[item["contestCode"]]
            if contest.kind == "district":
                districts[code].add(contest.code)
            stat = item["statistic"]
            if stat["validVotes"] != contest.votes_per_ballot * registered:
                odd[f"{contest.code}: the return's vote ceiling is not the voters times the votes per ballot"] += 1
            _statistics(tallies[contest.code], precinct_unit(code), contest, stat, registered, cast, odd)
            _votes(tallies[contest.code], precinct_unit(code), contest, item)
        if body["national"]:
            raise ValueError(f"return {code} holds national contests")
    if odd:
        raise ValueError(f"the returns do not balance: {dict(odd)}")
    return Returns(tallies, units, districts, odd)


def _units(p: Package) -> None:
    for (scheme, code), place in sorted(tree().items()):
        if scheme != "ph.barmm_city":
            continue
        id = city_unit(code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=BANGSAMORO,
            scheme_code="ph.barmm_city",
            code=f"{DAY}:{code}",
            area_relation="equals",
            area_id=area(scheme, code),
            kind="standard",
        )
        p.name(id, clean(place.name), "fil", "official")
    found = precinct_returns()
    for code, info in sorted(found.units.items()):
        id = precinct_unit(code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=BANGSAMORO,
            scheme_code="ph.precinct",
            code=f"{DAY}:{code}",
            area_relation="within",
            area_id=area("ph.barmm_barangay", precincts()[code]),
            kind="standard",
        )
        p.name(id, f"{clean(info['votingCenter'])} (clustered precinct {code})", "fil", "official")
        for district in sorted(found.districts[code]):
            p.add(
                "reporting_unit_area",
                unit_id=id,
                area_id=district_area(contests()[district]),
                approximate=False,
                partial=False,
                method="official_list",
            )


def _result_set(
    p: Package, contest: Contest, grain: str, tally: Tally, default: bool, completeness: float | None, election: str
) -> None:
    id = key("result_set", contest.code, grain)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=key("contest", contest.code),
        stage="official",
        kind="counted",
        source_id=RESULTS_SOURCE,
        status="active",
        is_default=default,
        completeness=completeness,
        as_of=as_of(),
    )
    body = METHOD.format(
        grain="by city and municipality" if grain == "canvass" else "by clustered precinct",
        what="the certificates of canvass of the cities and municipalities"
        if grain == "canvass"
        else f"the electronic returns of {len(precinct_returns().units):,} of the {EXPECTED_RETURNS:,} clustered "
        "precincts (the site never received the other 37)",
        source="canvass" if grain == "canvass" else "return",
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    for (unit, option, measure), value in sorted(
        tally.values.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
    ):
        p.add(
            "result_value",
            result_set_id=id,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2026 Bangsamoro Parliament election (party representatives, 32 parliamentary districts, and five "
        "sectoral contests) by city and municipality and by clustered precinct, from the Commission on Elections",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PHILIPPINES, "ph.precinct"}
    _results_source(p)
    p.artifact(_artifact(BUNDLE), RESULTS_SOURCE)
    p.artifact(_artifact(RETURNS), RESULTS_SOURCE)
    election_id = key("election", DAY)
    path = f"ph/bangsamoro/{DAY}"
    p.add("election", id=election_id, polity_id=BANGSAMORO, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2026 Bangsamoro Parliament election", "en", "official")
    _units(p)
    found = precinct_returns()
    tallies = canvass()
    for contest in contests().values():
        id = key("contest", contest.code)
        if contest.kind == "party":
            kind, system, district = "list_allocation", None, REGION_AREA
        else:
            kind = "body_seats"
            system = FPTP if contest.magnitude == 1 else BLOCK
            district = district_area(contest) if contest.kind == "district" else REGION_AREA
        p.add(
            "contest",
            id=id,
            election_id=election_id,
            kind=kind,
            body_id=PARLIAMENT,
            district_area_id=district,
            magnitude=contest.magnitude,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        label = f"Bangsamoro Parliament, {contest.label[0].lower()}{contest.label[1:]}"
        if contest.kind == "district":
            label = f"Bangsamoro Parliament, {contest.label}"
        p.name(id, label, "en", "official")
        p.add("slug", path=f"{path}/{slug(contest.label)}", entity_id=id, is_current=True)
        if contest.kind == "party":
            p.add("note", id=key("note", id), entity_id=id, kind="context", body_md=PARTY_NOTE, lang="en")
        for line in lines()[contest.code]:
            if contest.kind == "party":
                p.add(
                    "contest_option",
                    id=line.id,
                    contest_id=id,
                    kind="list",
                    party_id=party(line.name),
                    ballot_position=line.ordinal,
                    ballot_label=line.text,
                )
                continue
            independent = line.party in INDEPENDENT
            member = party(line.party) if line.party and not independent else None
            p.add(
                "contest_option",
                id=line.id,
                contest_id=id,
                kind="candidate",
                party_id=member,
                ballot_position=line.ordinal,
                ballot_label=line.text,
            )
            person = key("person", line.id)
            p.add("person", id=person)
            p.name(person, line.name, "fil", "ballot")
            p.add(
                "candidacy",
                id=key("candidacy", line.id),
                person_id=person,
                option_id=line.id,
                role="head",
                ballot_name=line.name,
                member_of_party_id=member,
                is_independent=independent,
            )
        canvassed = tallies[contest.code]
        counted = found.tallies[contest.code]
        whole = sum(v for (_, o, m), v in canvassed.values.items() if m == VOTES and o is not None)
        part = sum(v for (_, o, m), v in counted.values.items() if m == VOTES and o is not None)
        _result_set(p, contest, "canvass", canvassed, True, 1.0, election_id)
        _result_set(p, contest, "precinct", counted, False, round(part / whole, 6) if whole else None, election_id)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def summary() -> dict[str, Any]:
    """The regional totals of each contest, from the regional canvass, for comparing with the authority's."""
    found: dict[str, Any] = {}
    for item in site()[f"/data/coc/{RBOC}.json"]["local"]:
        found[item["contestCode"]] = {
            "name": item["contestName"],
            "statistic": item["statistic"],
            "options": {entry["name"]: entry["votes"] for entry in item["candidates"]["candidates"]},
        }
    return found


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())
