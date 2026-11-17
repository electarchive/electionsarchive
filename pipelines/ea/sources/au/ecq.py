import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import NamedTuple
from uuid import uuid5
from xml.etree import ElementTree

import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.ecq"
DATA = "https://resultsdata.elections.qld.gov.au"
ARCHIVE = "https://results.ecq.qld.gov.au/elections"
GIS = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Boundaries/AdministrativeBoundaries/MapServer"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
ELECTIONS = f"{DATA}/elections.json"
NO_PARTY = {"", "independent", "ind"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


ECQ = key("source", SOURCE)
GEO_SOURCE = key("source", "qld-spatial")
QUEENSLAND = area("au-qld.state", "QLD")

# Geometry layers of the Queensland Government's administrative boundaries service, queried as GeoJSON in WGS 84.
LAYERS = {"lga": 1, "district": 5, "division2024": 7, "division2020": 11, "ward2024": 8, "ward2020": 12}


def layer_url(name: str) -> str:
    return f"{GIS}/{LAYERS[name]}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson&geometryPrecision=5"


# Fetching -------------------------------------------------------------------------------------------------------


def events_json() -> list[dict[str, object]]:
    path = raw.cached(SOURCE, ELECTIONS).path
    return list(json.loads(path.read_text(encoding="utf-8"))["elections"])


def fetch() -> None:
    raw.fetch(SOURCE, ELECTIONS, suffix=".json")
    for event in events_json():
        zip_url = event.get("archiveXML")
        if isinstance(zip_url, str) and zip_url:
            raw.fetch(SOURCE, zip_url, suffix=".zip")
        elif event.get("electorates"):
            raw.fetch(SOURCE, f"{DATA}/{event['electorates']}", suffix=".json")
    for name in LAYERS:
        raw.fetch(SOURCE, layer_url(name), suffix=".json")
    from ea.sources.au import ecq_archive

    ecq_archive.fetch()


# The normalized model -------------------------------------------------------------------------------------------


@dataclass
class Cand:
    ballot: str
    order: int
    surname: str = ""
    given: str = ""
    party: str = ""
    code: str = ""


@dataclass
class Booth:
    id: str
    name: str
    type_code: str
    type_description: str
    ballots: int | None = None
    formal: int | None = None
    informal: int | None = None
    formal_ballots: int | None = None
    informal_ballots: int | None = None
    primary: dict[int, int] = field(default_factory=dict)
    final: dict[int, int] = field(default_factory=dict)


@dataclass
class Scope:
    """One district's counts: a state district, a council division or undivided council, or one division of a mayoral count."""

    name: str
    number: int
    enrolment: int | None
    system: str
    method: str
    formal: int | None = None
    informal: int | None = None
    total: int | None = None
    formal_ballots: int | None = None
    informal_ballots: int | None = None
    primary: dict[int, int] = field(default_factory=dict)
    final: dict[int, int] = field(default_factory=dict)
    booths: list[Booth] = field(default_factory=list)


@dataclass
class Contest:
    kind: str  # assembly, councillor, mayor
    name: str  # the district for a state or councillor contest; the council for a mayor
    council: str = ""
    council_code: str = ""
    council_type: str = ""
    elect: int = 1
    system: str = ""
    method: str = ""
    cands: list[Cand] = field(default_factory=list)
    declared: set[int] = field(default_factory=set)
    scopes: list[Scope] = field(default_factory=list)
    distributions: int = 0
    totals: Scope | None = None
    inferred: bool = False
    question: str = ""


@dataclass
class Event:
    id: str
    name: str
    day: date
    kind: str  # General, By-election, Local Quadrennial, ...
    stub: str
    contests: list[Contest]
    urls: list[str]
    unofficial: bool = False


def _int(node: ElementTree.Element | None) -> int | None:
    if node is None or node.text is None or not node.text.strip():
        return None
    return int(node.text.strip().replace(",", ""))


def _count(node: ElementTree.Element | None, tag: str) -> int | None:
    child = None if node is None else node.find(tag)
    if child is None:
        return None
    inner = child.find("count")
    return _int(inner if inner is not None else child)


def _votes(node: ElementTree.Element | None) -> dict[int, int]:
    found: dict[int, int] = {}
    if node is None:
        return found
    for c in node.findall("candidate"):
        value = _int(c.find("count"))
        if value is not None:
            found[int(c.get("ballotOrderNumber", "0"))] = value
    return found


def _booth(node: ElementTree.Element, tcp: ElementTree.Element | None) -> Booth:
    b = Booth(
        node.get("id", ""),
        (node.get("name") or "").strip(),
        node.get("typeCode") or "",
        node.get("typeDescription") or "",
        ballots=_int(node.find("ballots")),
        formal=_int(node.find("formalVotes")),
        informal=_int(node.find("informalVotes")),
        formal_ballots=_int(node.find("formalBallots")),
        informal_ballots=_int(node.find("informalBallots")),
        primary=_votes(node.find("primaryVoteResults")),
    )
    if tcp is not None:
        b.final = _votes(tcp.find("twoCandidateVotes"))
    return b


def _scope(district: ElementTree.Element) -> Scope:
    return Scope(
        district.get("districtName", "").strip(),
        int(district.get("number") or 0),
        int(district.get("enrolment") or 0) or None if district.get("enrolment") else None,
        district.get("votingSystem") or "",
        district.get("votingMethod") or "",
    )


def _rounds(node: ElementTree.Element) -> tuple[ElementTree.Element | None, ElementTree.Element | None]:
    """The official first preference round and the official distribution of preferences round of a node."""
    first = final = None
    for r in node.findall("countRound"):
        if r.get("id") == "3":
            first = r
        elif r.get("id") == "4":
            final = r
    return first, final


def _fill(scope: Scope, first: ElementTree.Element | None, final: ElementTree.Element | None) -> int:
    """Read one district's official counts into its scope, and return how many exclusions the distribution made."""
    if first is not None:
        scope.formal = _count(first, "totalFormalVotes")
        scope.informal = _count(first, "totalInformalVotes")
        scope.formal_ballots = _count(first, "totalFormalBallots")
        scope.informal_ballots = _count(first, "totalInformalBallots")
        scope.total = _int(first.find("totalVotes")) or _int(first.find("totalBallots"))
        scope.primary = _votes(first.find("primaryVoteResults"))
        finals = {}
        if final is not None:
            by_id = {b.get("id"): b for b in final.findall("booths/booth")}
            finals = by_id
        for b in first.findall("booths/booth"):
            scope.booths.append(_booth(b, finals.get(b.get("id"))))
    distributions = 0
    if final is not None:
        scope.final = _votes(final.find("twoCandidateVotes"))
        distributions = len(final.findall("preferenceDistributionSummary/preferenceDistribution"))
    return distributions


def _cands(node: ElementTree.Element) -> list[Cand]:
    found = []
    for c in node.findall("candidates/candidate"):
        found.append(
            Cand(
                c.get("ballotName", "").strip(),
                int(c.get("ballotOrderNumber", "0")),
                (c.findtext("surname") or "").strip(),
                (c.findtext("givenNames") or "").strip(),
                (c.get("party") or "").strip(),
                (c.get("partyCode") or "").strip(),
            )
        )
    return found


def _declared(node: ElementTree.Element) -> set[int]:
    return {int(d.get("ballotOrderNumber", "0")) for d in node.findall("declaredCandidate")}


def parse_state(district: ElementTree.Element) -> Contest:
    scope = _scope(district)
    first, final = _rounds(district)
    k = _fill(scope, first, final)
    return Contest(
        "assembly",
        scope.name,
        elect=_int(district.find("numElectedCandidates")) or 1,
        system=scope.system,
        method=scope.method,
        cands=_cands(district),
        declared=_declared(district),
        scopes=[scope],
        distributions=k,
        totals=scope,
    )


def parse_councillor(lga: ElementTree.Element, district: ElementTree.Element) -> Contest:
    scope = _scope(district)
    first, final = _rounds(district)
    k = _fill(scope, first, final)
    return Contest(
        "councillor",
        scope.name,
        council=lga.get("electorateName", "").strip(),
        council_code=lga.get("areaCode", ""),
        council_type=lga.get("electorateType", ""),
        elect=_int(district.find("numElectedCandidates")) or 1,
        system=scope.system,
        method=scope.method,
        cands=_cands(district),
        declared=_declared(district),
        scopes=[scope],
        distributions=k,
        totals=scope,
    )


def parse_mayor(lga: ElementTree.Element, contest: ElementTree.Element) -> Contest:
    name = lga.get("electorateName", "").strip()
    total = Scope(
        name,
        0,
        int(lga.get("enrolment") or 0) or None if lga.get("enrolment") else None,
        lga.get("votingSystemMayor") or "",
        lga.get("votingMethod") or "",
    )
    first, final = _rounds(contest)
    k = 0
    scopes: list[Scope] = []
    if first is not None:
        _fill(total, first, None)
        total.booths = []
        finals = {d.get("districtName"): d for d in final.findall("districts/district")} if final is not None else {}
        for d in first.findall("districts/district"):
            scope = _scope(d)
            _fill(scope, d, finals.get(d.get("districtName")))
            scopes.append(scope)
    if final is not None:
        total.final = _votes(final.find("twoCandidateVotes"))
        k = len(final.findall("preferenceDistributionSummary/preferenceDistribution"))
    return Contest(
        "mayor",
        name,
        council=name,
        council_code=lga.get("areaCode", ""),
        council_type=lga.get("electorateType", ""),
        elect=1,
        system=total.system,
        method=total.method,
        cands=_cands(contest),
        declared=_declared(contest),
        scopes=scopes,
        distributions=k,
        totals=total,
    )


@cache
def _tree(url: str) -> ElementTree.Element:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        return ElementTree.fromstring(archive.read("publicResults.xml"))


def sole_candidate(entry: dict[str, object]) -> Event:
    """An event the results host lists without a results file: a councillor by-election with no more candidates than places."""
    url = f"{DATA}/{entry['electorates']}"
    data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["electorates"]
    contests = []
    for d in data:
        name = str(d["electorateName"]).strip()
        council = re.sub(r"\s+Division \d+$", "", name)
        cands = [
            Cand(
                str(c["ballotName"]).strip(),
                int(c["ballotOrderNumber"]),
                str(c.get("candidateSurname") or ""),
                str(c.get("candidateGivenNames") or ""),
            )
            for c in d.get("candidatesCouncillor") or []
        ]
        elect = int(d.get("numberToElect") or 1)
        if len(cands) > elect:
            raise ValueError(f"{url}: {len(cands)} candidates for {elect} places, but no results file")
        scope = Scope(name, 1, d.get("enrolment"), str(d.get("votingSystem") or ""), str(d.get("votingMethod") or ""))
        contests.append(
            Contest(
                "councillor",
                name,
                council=council,
                council_code=str(d.get("areaCode") or ""),
                council_type="Council - Undivided",
                elect=elect,
                system=scope.system,
                method=scope.method,
                cands=cands,
                declared={c.order for c in cands},
                scopes=[scope],
                totals=scope,
                inferred=True,
            )
        )
    return Event(
        str(entry["id"]),
        str(entry["electionName"]).strip(),
        date.fromisoformat(str(entry["electionDay"])),
        str(entry["electionType"]),
        str(entry["stub"]),
        contests,
        [url],
    )


def modern_events() -> list[Event]:
    found: list[Event] = []
    for entry in events_json():
        url = entry.get("archiveXML")
        if not isinstance(url, str) or not url:
            if entry.get("electorates"):
                found.append(sole_candidate(entry))
            continue
        root = _tree(url)
        day = date.fromisoformat(str(entry["electionDay"]))
        name = str(entry["electionName"]).strip()
        elections = root.findall("election")
        matches = [e for e in elections if (e.get("electionName") or "").strip() == name]
        if not matches:
            matches = [e for e in elections if e.get("electionDay") == day.isoformat()]
        if len(matches) != 1:
            raise ValueError(f"{url}: {len(matches)} elections named {name!r}")
        el = matches[0]
        contests: list[Contest] = []
        for district in el.findall("districts/district"):
            contests.append(parse_state(district))
        for lga in el.findall("lga"):
            for contest in lga.findall("contest"):
                if contest.get("contestType") == "mayor":
                    contests.append(parse_mayor(lga, contest))
                else:
                    for district in contest.findall("districts/district"):
                        contests.append(parse_councillor(lga, district))
        found.append(
            Event(str(entry["id"]), name, day, str(entry["electionType"]), str(entry["stub"]), contests, [url])
        )
    return found


# Names, vintages, and ids ---------------------------------------------------------------------------------------

STATE_VINTAGES = [(date(2017, 10, 29), "2017"), (date(2008, 1, 1), "2008"), (date(1900, 1, 1), "pre-2008")]
DIVISION_VINTAGES = [
    (date(2024, 3, 16), "2024"),
    (date(2020, 3, 28), "2020"),
    (date(2016, 3, 19), "2016"),
    (date(2013, 4, 28), "2013"),
    (date(2012, 3, 31), "2012"),
    (date(1900, 1, 1), "2008"),
]
SHAPED = {"2017": date(2017, 10, 29), "2020": date(2020, 3, 28), "2024": date(2024, 3, 16)}
LGA_SINCE = date(2014, 1, 1)
STRIP = {"shire", "regional", "city", "aboriginal", "council", "town", "of"}


def vintage(table: list[tuple[date, str]], day: date) -> str:
    return next(label for since, label in table if day >= since)


def council_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("-", " ")).split()
    return " ".join(w for w in words if w not in STRIP).replace(" ", "-")


def suffix(council: str, name: str) -> str:
    """A division or ward's own name: the district name without its council's name in front."""
    rest = name[len(council) :].strip() if name.lower().startswith(council.lower()) else name
    return re.sub(r"\s+", " ", rest).strip().lower()


def title(name: str) -> str:
    return name.title().replace("'S ", "'s ") if name.isupper() else name


def person_name(ballot: str) -> str:
    surname, _, given = ballot.partition(", ")
    if surname.isupper():
        surname = (
            re.sub(r"^(MC|MAC)(?=[A-Z]{3})", lambda m: m.group(1).title(), surname.title())
            if False
            else surname.title()
        )
        surname = re.sub(r"^(Mc)([a-z])", lambda m: m.group(1) + m.group(2).upper(), surname)
    return f"{given} {surname}".strip()


PARTY_ALIASES = {"legalise cannabis qld (party)": "legalise cannabis queensland (party)"}


def party_code(name: str) -> str:
    code = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", "and")).strip()
    return PARTY_ALIASES.get(name.lower(), code)


def party_id(name: str) -> str:
    return key("party", party_code(PARTY_ALIASES.get(name.lower(), name)))


def independent(c: Cand) -> bool | None:
    if c.party.lower() == "independent" or c.code.lower() == "ind":
        return True
    return None if not c.party else False


ASSEMBLY = key("body", "legislative-assembly")
SYSTEM_ASSEMBLY_OP = key("electoral_system", "assembly", "optional")
SYSTEM_ASSEMBLY_CP = key("electoral_system", "assembly", "compulsory")
SYSTEM_DIVISION = key("electoral_system", "council", "division")
SYSTEM_SINGLE = key("electoral_system", "council", "single")
SYSTEM_BLOCK = key("electoral_system", "council", "block")
SYSTEM_MAYOR = key("electoral_system", "mayor")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")

SYSTEMS: list[tuple[str, date | None, date | None, dict[str, object], str]] = [
    (
        SYSTEM_ASSEMBLY_OP,
        date(1992, 1, 1),
        date(2017, 1, 1),
        {"formula": "irv"},
        (
            "Each district elects one member to the Legislative Assembly by optional preferential voting: voters number "
            "as many candidates as they choose, at least one, and the candidate with the fewest votes is excluded and "
            "the ballots passed on to their next preference until one candidate holds an absolute majority of the "
            "ballots still in the count. A ballot with no further preference is exhausted."
        ),
    ),
    (
        SYSTEM_ASSEMBLY_CP,
        date(2017, 1, 1),
        None,
        {"formula": "irv"},
        (
            "Each district elects one member to the Legislative Assembly by compulsory preferential voting: voters "
            "must number every box, and the candidate with the fewest votes is excluded and their ballots passed on to "
            "their next preference until one candidate holds an absolute majority."
        ),
    ),
    (
        SYSTEM_DIVISION,
        None,
        None,
        {"formula": "irv"},
        (
            "A division or ward elects one councillor by optional preferential voting: voters number as many "
            "candidates as they choose, and the candidate with the fewest votes is excluded and the ballots passed on "
            "to their next preference until one candidate holds an absolute majority of the ballots still in the count."
        ),
    ),
    (
        SYSTEM_SINGLE,
        None,
        None,
        {"formula": "fptp"},
        "A councillor is elected by first past the post: the candidate with the most votes wins.",
    ),
    (
        SYSTEM_BLOCK,
        None,
        None,
        {"formula": "block"},
        (
            "An undivided council elects several councillors at once by first past the post: each voter marks up to as "
            "many candidates as there are places, and the candidates with the most votes fill them."
        ),
    ),
    (
        SYSTEM_MAYOR,
        None,
        None,
        {"formula": "irv"},
        (
            "The mayor is elected by optional preferential voting: voters number as many candidates as they choose, "
            "and the candidate with the fewest votes is excluded and the ballots passed on to their next preference "
            "until one candidate holds an absolute majority of the ballots still in the count."
        ),
    ),
    (
        SYSTEM_REFERENDUM,
        None,
        None,
        {"formula": "referendum", "majority": {"share": 0.5, "strict": True}},
        "A question carries when more than half of the formal votes answer yes.",
    ),
]


def system_for(c: Contest, day: date) -> str:
    if c.kind == "assembly":
        return SYSTEM_ASSEMBLY_CP if c.system.lower().startswith("compulsory") else SYSTEM_ASSEMBLY_OP
    if c.kind == "mayor":
        return SYSTEM_MAYOR
    if c.system.lower().startswith("first"):
        return SYSTEM_SINGLE if c.elect == 1 else SYSTEM_BLOCK
    return SYSTEM_DIVISION


def council_body(council: str) -> str:
    return key("body", "council", council_key(council))


def mayor_office(council: str) -> str:
    return key("office", "mayor", council_key(council))


def unit_kind(b: Booth) -> str:
    if b.type_code == "TOTAL":
        return "aggregate"
    if b.type_code == "DV1":
        return "postal_district"
    if b.type_code in ("DV2", "AB", "AB1", "AB2"):
        return "aggregate"
    if b.name.lower().startswith("mobile"):
        return "mobile"
    return "standard"


# Geo ------------------------------------------------------------------------------------------------------------


def source_rows(p: Package) -> None:
    p.add(
        "source",
        id=ECQ,
        kind="official_body",
        publisher="Electoral Commission of Queensland",
        title="Queensland state and local government election results",
        url="https://results.ecq.qld.gov.au/elections",
        license="Official public record, no license stated",
        license_url=None,
        attribution="© State of Queensland (Electoral Commission of Queensland)",
    )
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="official_body",
        publisher="Queensland Government",
        title="Queensland administrative boundaries: state electorates, local government areas, divisions and wards",
        url=GIS,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© State of Queensland (Department of Resources; Queensland Redistribution Commission; Department "
        "of State Development, Infrastructure, Local Government and Planning)",
    )


class Shape(NamedTuple):
    properties: dict[str, object]
    geometry: shapely.Geometry


@cache
def shapes(layer: str) -> list[Shape]:
    data = json.loads(raw.cached(SOURCE, layer_url(layer)).path.read_text(encoding="utf-8"))
    found = []
    for f in data["features"]:
        geometry = shapely.from_geojson(json.dumps(f["geometry"]))
        parts = [
            polygon
            for g in shapely.get_parts(shapely.make_valid(geometry))
            if shapely.get_type_id(g) in (3, 6)
            for polygon in shapely.get_parts(g)
        ]
        found.append(Shape(f["properties"], shapely.MultiPolygon(parts)))
    return found


def outline(p: Package, id: str, shape: shapely.Geometry, since: date | None, until: date | None) -> None:
    shape = shapely.set_srid(shape, 4326)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        valid_to=until,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=GEO_SOURCE,
    )


def lga_layer() -> dict[str, tuple[str, str, shapely.Geometry]]:
    return {
        council_key(str(s.properties["lga"])): (str(s.properties["lga"]), str(s.properties["lga_code"]), s.geometry)
        for s in shapes("lga")
    }


def division_shapes() -> dict[tuple[str, str, str], shapely.Geometry]:
    """Divisions and wards with a published shape, by vintage, council, and the division's own name."""
    found: dict[tuple[str, str, str], shapely.Geometry] = {}
    for vint, layers in (("2024", ("division2024", "ward2024")), ("2020", ("division2020", "ward2020"))):
        for layer in layers:
            for s in shapes(layer):
                lga = council_key(str(s.properties["lga"]))
                if "ward_name" in s.properties:
                    name = str(s.properties["ward_name"]).lower()
                else:
                    name = str(s.properties["elect_div"]).lower()
                found[(vint, lga, name)] = s.geometry
    return found


def used(events: list[Event]) -> tuple[set[tuple[str, str]], dict[tuple[str, str, str], str], dict[str, str]]:
    """The state districts, council divisions, and council names that events use."""
    districts: set[tuple[str, str]] = set()
    divisions: dict[tuple[str, str, str], str] = {}
    councils: dict[str, str] = {}
    for e in events:
        for c in e.contests:
            if c.kind == "assembly":
                districts.add((vintage(STATE_VINTAGES, e.day), c.name))
                continue
            if not c.council:
                continue
            councils.setdefault(council_key(c.council), c.council)
            if c.council_type != "Council - Undivided":
                for sc in c.scopes:
                    divisions[
                        (vintage(DIVISION_VINTAGES, e.day), council_key(c.council), suffix(c.council, sc.name))
                    ] = sc.name
    return districts, divisions, councils


def district_area(day: date, name: str) -> str:
    return area("au-qld.district", f"{vintage(STATE_VINTAGES, day)}:{name}")


def scope_area(day: date, c: Contest, scope: Scope) -> str:
    if c.kind == "assembly":
        return district_area(day, scope.name)
    if c.kind == "question" and not c.council:
        return QUEENSLAND
    if c.council_type == "Council - Undivided":
        return area("au-qld.lga", council_key(c.council))
    return area(
        "au-qld.division", f"{vintage(DIVISION_VINTAGES, day)}:{council_key(c.council)}:{suffix(c.council, scope.name)}"
    )


def geo(events: list[Event]) -> Package:
    districts, divisions, councils = used(events)
    layer = lga_layer()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Queensland, its {len({n for _, n in districts})} state electoral districts by boundary vintage, its local "
        "government areas, and their divisions and wards, with the shapes the Queensland Government publishes",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    source_rows(p)
    for layer_name in LAYERS:
        p.uses(raw.cached(SOURCE, layer_url(layer_name)))
    for code, rank, kind, noun in (
        ("au-qld.state", 1, "administrative", "state"),
        ("au-qld.district", 2, "electoral_district", "district"),
        ("au-qld.lga", 2, "administrative", "local government area"),
        ("au-qld.division", 3, "electoral_district", "division"),
        ("au-qld.booth", 4, "precinct", "voting centre"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRALIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=QUEENSLAND, scheme_code="au-qld.state")
    p.add("area_code", area_id=QUEENSLAND, code="QLD")
    p.add("area_parent", area_id=QUEENSLAND, parent_id=COUNTRY)
    p.name(QUEENSLAND, "Queensland", "en", "official")

    firsts: dict[tuple[str, str], date] = {}
    for e in events:
        for c in e.contests:
            if c.kind == "assembly":
                firsts[("au-qld.district", vintage(STATE_VINTAGES, e.day))] = min(
                    e.day, firsts.get(("au-qld.district", vintage(STATE_VINTAGES, e.day)), e.day)
                )
            elif c.kind in ("councillor", "mayor") and c.council_type != "Council - Undivided":
                v = vintage(DIVISION_VINTAGES, e.day)
                firsts[("au-qld.division", v)] = min(e.day, firsts.get(("au-qld.division", v), e.day))
    for (scheme, vint), day in sorted(firsts.items()):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, vint),
            scheme_code=scheme,
            vintage=vint,
            valid_from=SHAPED.get(vint) if SHAPED.get(vint) and scheme == "au-qld.district" else None,
            as_of=day,
        )
    p.add(
        "boundary_set",
        id=key("boundary_set", "au-qld.lga", "2014"),
        scheme_code="au-qld.lga",
        vintage="2014",
        valid_from=LGA_SINCE,
        as_of=LGA_SINCE,
    )

    state_shapes = {str(s.properties["adminareaname"]).lower(): s.geometry for s in shapes("district")}
    for vint, name in sorted(districts):
        id = area("au-qld.district", f"{vint}:{name}")
        p.add("area", id=id, scheme_code="au-qld.district")
        p.add("area_code", area_id=id, code=f"{vint}:{name}")
        p.add("area_parent", area_id=id, parent_id=QUEENSLAND)
        p.name(id, name, "en", "official")
        if vint == "2017" and name.lower() in state_shapes:
            outline(p, id, state_shapes[name.lower()], SHAPED["2017"], None)

    names: dict[str, set[str]] = defaultdict(set)
    for e in events:
        for c in e.contests:
            if c.council:
                names[council_key(c.council)].add(c.council)
    for k, (name, code, shape) in sorted(layer.items()):
        id = area("au-qld.lga", k)
        p.add("area", id=id, scheme_code="au-qld.lga")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=QUEENSLAND)
        p.name(id, name, "en", "official")
        for other in sorted(names.get(k, set()) - {name}):
            p.add(
                "name",
                id=p.id("name", id, "en", "historical", other),
                entity_id=id,
                text=other,
                lang="en",
                kind="historical",
                is_preferred=False,
            )
        outline(p, id, shape, LGA_SINCE, None)
    for k, name in sorted(councils.items()):
        if k not in layer:
            id = area("au-qld.lga", k)
            p.add("area", id=id, scheme_code="au-qld.lga")
            p.add("area_code", area_id=id, code=k)
            p.add("area_parent", area_id=id, parent_id=QUEENSLAND)
            p.name(id, name, "en", "official")

    division_geometry = division_shapes()
    ends = {"2020": SHAPED["2024"], "2024": None}
    for (vint, council, own), name in sorted(divisions.items()):
        id = area("au-qld.division", f"{vint}:{council}:{own}")
        p.add("area", id=id, scheme_code="au-qld.division")
        p.add("area_code", area_id=id, code=f"{vint}:{council}:{own}")
        p.add("area_parent", area_id=id, parent_id=area("au-qld.lga", council))
        p.name(id, name, "en", "official")
        shape = division_geometry.get((vint, council, own))
        if shape is not None and vint in SHAPED:
            outline(p, id, shape, SHAPED[vint], ends[vint])
    return p


# Registry -------------------------------------------------------------------------------------------------------


def party_names(events: list[Event]) -> dict[str, str]:
    found: dict[str, str] = {}
    for e in events:
        for c in e.contests:
            for cand in c.cands:
                if cand.party and independent(cand) is not True:
                    found.setdefault(party_code(PARTY_ALIASES.get(cand.party.lower(), cand.party)), cand.party)
    return found


def registry(events: list[Event]) -> Package:
    names = party_names(events)
    _, _, councils = used(events)
    layer = lga_layer()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        f"The Queensland Legislative Assembly, {len(councils)} local councils and their mayors, their electoral "
        f"systems, and the {len(names)} registered parties on the Electoral Commission of Queensland's results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="unicameral")
    p.name(ASSEMBLY, "Queensland Legislative Assembly", "en", "official")
    for k, name in sorted(councils.items()):
        official = layer[k][0] if k in layer else name
        body = council_body(name)
        p.add("body", id=body, polity_id=AUSTRALIA, kind="council")
        p.name(body, f"{official} Council", "en", "official")
        office = mayor_office(name)
        p.add("office", id=office, polity_id=AUSTRALIA, body_id=body, kind="mayor")
        p.name(office, f"Mayor of {official}", "en", "official")
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, name in sorted(names.items()):
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    return p


# Elections ------------------------------------------------------------------------------------------------------


class Values:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "count_round", "value", "flags")
        }

    def add(
        self,
        result_set: str,
        unit: str,
        option: str | None,
        measure: int,
        value: int | None,
        round: int = 0,
        flags: int = 0,
    ) -> None:
        if value is None:
            return
        for column, item in zip(self.columns, (result_set, unit, option, measure, round, value, flags), strict=True):
            self.columns[column].append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16))


VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
DERIVED = 1  # result_value.flags: a figure the archive computed from the source's, not one the source prints

MAIN_METHOD = (
    "Counted by the Electoral Commission of Queensland for each voting centre and published in its results data "
    "as the official first preference count. Each unit is one voting centre, early voting centre, or group of "
    "declaration votes within one district. Where a distribution of preferences was held, the two candidates left "
    "at the end and each one's votes, as the Commission reports them for the district and for each voting centre, "
    "are stored as the last count round; the earlier steps of the distribution are not stored here. Enrolment at "
    "the close of the roll is stored on a separate unit per district."
)
MULTI_METHOD = (
    " Each voter could mark as many candidates as there were places to fill, so a candidate's votes are counted "
    "in votes, and the valid and invalid totals count ballots."
)
INFERRED_METHOD = (
    "The Electoral Commission of Queensland's results host lists this contest and its candidate but publishes no "
    "results file and no declaration. With no more candidates than places to fill, no poll is held, so the "
    "candidate is recorded as elected unopposed."
)
ARCHIVE_METHOD = (
    "Counted by the Electoral Commission of Queensland for each polling booth and published in its results archive "
    "as the final count. Each unit is one polling booth, or one kind of declaration, pre-poll, or absent vote, "
    "within one district. Where a distribution of preferences was held, the candidates left at the end and each "
    "one's votes for the whole district are stored as the last count round; the earlier steps of the distribution "
    "are not stored here. Enrolment at the close of the roll is stored on a separate unit per district."
)
QUESTION_METHOD = (
    "The Electoral Commission of Queensland's results archive publishes only the total for the whole of "
    "Queensland, with no figures for districts or voting centres. The total is stored on one unit, and the "
    "enrolment on another."
)
TACIT_METHOD = (
    "No poll was held for this contest because only as many candidates were nominated as there were places to "
    "fill. The Electoral Commission of Queensland declared them elected without a count."
)


def kind_of(event: Event) -> str:
    k = event.kind.lower()
    if "quadrennial" in k:
        return "local"
    if "by-election" in k:
        return "by_election"
    if "referendum" in k or "poll" in k:
        return "referendum"
    return "general"


def election_path(event: Event) -> str:
    kind = kind_of(event)
    if kind == "general":
        return f"au/qld/{event.day}-general"
    if kind == "local":
        return f"au/qld/{event.day}-local"
    stem = re.sub(r"^(?:19|20)\d{2}\s+", "", event.name)
    return f"au/qld/{event.day}-{changeset.slug(stem)}"


def package_key(event: Event) -> str:
    return f"{SOURCE}/{event.day}-{changeset.slug(event.stub)}"


def election(event: Event) -> Package:
    day = event.day
    p = Package(
        package_key(event),
        SOURCE,
        f"{event.name} in Queensland, first preferences by voting centre, from the Electoral Commission of Queensland",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    for url in event.urls:
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", event.stub, day)
    path = election_path(event)
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind=kind_of(event),
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, event.name, "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Values()
    units: dict[str, str] = {}
    declared_valid: dict[str, int] = {}
    labels: dict[str, str] = {}
    for c in event.contests:
        tag = c.kind
        question = c.kind == "question"
        ident = (event.stub, tag, c.council_code, c.name)
        contest = key("contest", *ident)
        multi = c.elect > 1
        if c.kind == "assembly":
            label, slug = c.name, f"assembly-{changeset.slug(c.name)}"
            district = district_area(day, c.name)
        elif c.kind == "mayor":
            label, slug = f"{c.council} mayor", f"mayor-{changeset.slug(c.council)}"
            district = area("au-qld.lga", council_key(c.council))
        elif question:
            label = f"{c.council} de-amalgamation question" if c.council else c.name
            slug = f"question-{changeset.slug(c.council or c.name)}"
            district = scope_area(day, c, c.scopes[0])
        else:
            label = f"{c.council} councillors" if c.council_type == "Council - Undivided" else f"{c.name} councillor"
            slug = f"council-{changeset.slug(c.name)}"
            district = scope_area(day, c, c.scopes[0])
        counted = bool(c.totals and c.totals.primary)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question" if question else "office" if c.kind == "mayor" else "body_seats",
            office_id=mayor_office(c.council) if c.kind == "mayor" else None,
            body_id=None
            if c.kind in ("mayor", "question")
            else ASSEMBLY
            if c.kind == "assembly"
            else council_body(c.council),
            district_area_id=district,
            magnitude=None if question else c.elect,
            round_number=1,
            electoral_system_id=SYSTEM_REFERENDUM if question else system_for(c, day),
            default_result_kind="counted" if counted else "tacit",
        )
        p.name(contest, label, "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        if question:
            p.add("ballot_question", contest_id=contest, type="referendum")
            if c.question:
                p.add("note", id=key("note", contest), entity_id=contest, kind="context", body_md=c.question, lang="en")
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted" if counted else "tacit",
            source_id=ECQ,
            status="active",
            is_default=True,
            completeness=1.0 if counted else None,
        )
        archive = any(u.startswith(ARCHIVE) for u in event.urls)
        method = (
            (QUESTION_METHOD if question and not c.scopes[0].booths else ARCHIVE_METHOD if archive else MAIN_METHOD)
            + (MULTI_METHOD if multi else "")
            if counted
            else INFERRED_METHOD
            if c.inferred
            else TACIT_METHOD
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: dict[int, str] = {}
        for cand in c.cands:
            id = options[cand.order] = key("option", *ident, cand.order)
            pid = party_id(cand.party) if cand.party and independent(cand) is not True else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="answer" if question else "candidate",
                party_id=pid,
                ballot_position=cand.order,
                ballot_label=cand.ballot,
            )
            if question:
                continue
            person = key("person", *ident, cand.order)
            p.add("person", id=person)
            p.name(person, person_name(cand.ballot), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", *ident, cand.order),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=cand.ballot,
                member_of_party_id=pid,
                is_independent=independent(cand),
            )
        finalists = set((c.totals.final if c.totals else {}) or {})
        for order, id in options.items():
            if c.declared:
                kind = (
                    "elected"
                    if order in c.declared
                    else "lost"
                    if (not finalists or order in finalists or multi)
                    else "eliminated"
                )
                p.add("outcome", result_set_id=result_set, option_id=id, kind=kind)
        if not counted:
            continue
        total = c.totals
        assert total is not None
        valid = total.formal_ballots if total.formal_ballots is not None else total.formal
        invalid = total.informal_ballots if total.informal_ballots is not None else total.informal
        for order, votes in total.primary.items():
            p.add("declared", result_set_id=result_set, option_id=options[order], measure=VOTES, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)
        if valid is not None:
            declared_valid[result_set] = valid
            labels[result_set] = label
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=invalid)
        if (
            valid is not None
            and invalid is not None
            and any(b.ballots is not None for sc in c.scopes for b in sc.booths)
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=BALLOTS, value=valid + invalid)
        for scope in c.scopes:
            area_id = scope_area(day, c, scope)
            roll = key("unit", event.stub, scope.name, "roll")
            if roll not in units:
                units[roll] = roll
                p.add(
                    "reporting_unit",
                    id=roll,
                    polity_id=AUSTRALIA,
                    scheme_code="au-qld.booth",
                    code=f"{day}:{event.stub}:{scope.name}:ROLL",
                    area_relation="within",
                    area_id=area_id,
                    kind="aggregate",
                )
                p.name(roll, f"{scope.name}: electoral roll", "en", "official")
            values.add(result_set, roll, None, REGISTERED, scope.enrolment)
            booths = scope.booths or [
                Booth("total", "total", "TOTAL", "", scope.total, scope.formal, scope.informal, primary=scope.primary)
            ]
            for b in booths:
                unit = key("unit", event.stub, scope.name, b.id)
                if unit not in units:
                    units[unit] = unit
                    kind = unit_kind(b)
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=AUSTRALIA,
                        scheme_code="au-qld.booth",
                        code=f"{day}:{event.stub}:{scope.name}:{b.id}",
                        area_relation="within",
                        area_id=area_id,
                        kind=kind,
                    )
                    p.name(unit, b.name if kind == "standard" else f"{scope.name}: {b.name}", "en", "official")
                for order, votes in b.primary.items():
                    values.add(result_set, unit, options[order], VOTES, votes)
                if multi and b.formal_ballots is None and b.ballots is not None and b.informal is not None:
                    # The source prints each voting centre's votes for a council, not its valid ballots.
                    values.add(result_set, unit, None, VALID, b.ballots - b.informal, flags=DERIVED)
                    values.add(result_set, unit, None, INVALID, b.informal)
                else:
                    valid_here = b.formal_ballots if b.formal_ballots is not None else b.formal
                    invalid_here = b.informal_ballots if b.informal_ballots is not None else b.informal
                    values.add(result_set, unit, None, VALID, valid_here)
                    values.add(result_set, unit, None, INVALID, invalid_here)
                values.add(result_set, unit, None, BALLOTS, b.ballots)
                if c.distributions:
                    for order, votes in b.final.items():
                        values.add(result_set, unit, options[order], VOTES, votes, c.distributions)
            if c.distributions and scope.final and not any(b.final for b in scope.booths):
                # The archive prints the last count only for the whole district.
                total_unit = key("unit", event.stub, scope.name, "total")
                p.add(
                    "reporting_unit",
                    id=total_unit,
                    polity_id=AUSTRALIA,
                    scheme_code="au-qld.booth",
                    code=f"{day}:{event.stub}:{scope.name}:TOTAL",
                    area_relation="within",
                    area_id=area_id,
                    kind="aggregate",
                )
                p.name(total_unit, f"{scope.name}: district total", "en", "official")
                for order, votes in scope.final.items():
                    values.add(result_set, total_unit, options[order], VOTES, votes, c.distributions)
    frame = values.frame()
    p.frame("result_value", frame)
    sums = {
        row["result_set_id"]: row["value"]
        for row in frame.filter((pl.col("measure") == VALID) & (pl.col("count_round") == 0))
        .group_by("result_set_id")
        .agg(pl.col("value").sum())
        .iter_rows(named=True)
    }
    off = [
        f"{label} (prints {declared_valid[rs]:,} valid ballots, its voting centres sum to {sums.get(rs, 0):,})"
        for rs, label in labels.items()
        if rs in declared_valid and declared_valid[rs] != sums.get(rs, 0)
    ]
    if off:
        p.accept(
            "accounting.declared",
            "The Commission's printed contest totals differ from the sum of its voting centres' counts in "
            + "; ".join(off)
            + ". Both are stored as printed; the candidates' votes agree with the printed totals.",
        )
    return p


def packages(events: list[Event]) -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(e) for e in events)]


def all_events() -> list[Event]:
    from ea.sources.au import ecq_archive

    return sorted([*ecq_archive.archive_events(), *modern_events()], key=lambda e: (e.day, e.stub))


def parse() -> None:
    events = all_events()
    geo(events).write()
    registry(events).write()
    for event in events:
        election(event).write()


def emit() -> None:
    package.run("load", packages(all_events()))
