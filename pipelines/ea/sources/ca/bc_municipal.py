"""British Columbia general local elections, 2008 to 2022: municipalities, regional districts, school districts, the
Islands Trust, and park and community commissions.

CivicInfo BC publishes, for each general local election since 2008, a report of candidates and results with one row
per candidate (the local government, the office, the votes, whether the candidate was elected or acclaimed) and a
turnout report with the ballots cast and the estimated eligible voters of each local government and, from 2014, of
each regional district electoral area and Islands Trust area. Each office of each local government, and each ward,
electoral area, or trustee area where there is one, is a contest. The mayor, and a councillor for an area that elects
one, is decided by plurality; a council or board that elects several members at large is decided by block voting,
every elector marking up to as many candidates as there are seats. A contest whose candidates were all acclaimed
carries no votes and a `tacit` result set.

The reports for 2008 and 2011 do not name the electoral area, the ward, or the trustee area of a candidate, so they
load only the mayors and the councils that elect all their members at large. Boundaries of the municipalities,
regional districts and their electoral areas, school districts, and Islands Trust local trust areas come from the
Province of British Columbia's administrative boundaries (Open Government Licence - British Columbia).
"""

import functools
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ca import bc_vancouver

SOURCE = "ca.bc_municipal"
BOUNDS = "ca.bc_municipal.bounds"
CIVICINFO = "https://www.civicinfo.bc.ca/"
REPORTS = CIVICINFO + "electionreports/"
OPENMAPS = "https://openmaps.gov.bc.ca/geo/pub/ows?service=WFS&version=2.0.0&request=GetFeature"
OPENMAPS += "&outputFormat=application/json&srsName=EPSG:4326&typeNames=pub:"
CATALOGUE = "https://catalogue.data.gov.bc.ca/dataset/"
LAYERS = {
    "municipality": "WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_MUNICIPALITIES_SP",
    "regional_district": "WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_REGIONAL_DISTRICTS_SP",
    "electoral_area": "WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_ELECTORAL_AREAS_SP",
    "islands_trust": "WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_ISLANDS_TRUST_SP",
    "trust_area": "WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_LOCAL_TRUST_AREAS_SP",
    "school_district": "WHSE_TANTALIS.TA_SCHOOL_DISTRICTS_SVW",
}
LAYER_PAGES = {
    "municipality": "municipalities-legally-defined-administrative-areas-of-bc",
    "regional_district": "regional-districts-legally-defined-administrative-areas-of-bc",
    "electoral_area": "regional-district-electoral-areas-legally-defined-administrative-areas-of-bc",
    "islands_trust": "islands-trust-legally-defined-administrative-areas-of-bc",
    "trust_area": "local-trust-areas-legally-defined-administrative-areas-of-bc",
    "school_district": "school-district-boundaries-edited",
}
# General local elections only the city of Vancouver's own results cover: those before CivicInfo BC's reports begin,
# and the latest.
EARLIER = {
    1996: date(1996, 11, 16),
    1999: date(1999, 11, 20),
    2002: date(2002, 11, 16),
    2005: date(2005, 11, 19),
    2026: date(2026, 10, 17),
}
DAYS = {
    2008: date(2008, 11, 15),
    2011: date(2011, 11, 19),
    2014: date(2014, 11, 15),
    2018: date(2018, 10, 20),
    2022: date(2022, 10, 15),
}
# Production holds Canada, British Columbia, and its province area under the ids their first pipeline gave them.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
BC = "01a13fb6-eab7-7501-a0f9-4908fbc129f2"
BC_AREA = "01a13fb6-eab7-7501-a0f9-48fb66b1f67b"
SIMPLIFY = 0.0002
AT_LARGE = {"", "at large", "at-large", "na", "n/a"}
MUNICIPAL = {
    "City": "City of {}",
    "District": "District of {}",
    "Village": "Village of {}",
    "Town": "Town of {}",
    "Township": "Township of {}",
    "Island Municipality": "{} Island Municipality",
    "Resort Municipality": "Resort Municipality of {}",
    "Regional Municipality": "{} Regional Municipality",
    "Mountain Resort Municipality": "{} Mountain Resort Municipality",
    "Indian Government District": "{} Indian Government District",
}
# CivicInfo's name for a municipality where the province's boundary file spells it another way.
ALIASES = {
    ("Fort St. James", "District"): "Fort St James",
    ("Fort St. John", "City"): "Fort St John",
    ("Langley", "City"): "Langley - City",
    ("Langley", "Township"): "Langley - District",
    ("North Vancouver", "City"): "North Vancouver - City",
    ("North Vancouver", "District"): "North Vancouver - District",
}
# CivicInfo's name for a school district or regional district where the province's boundary file spells it another way.
SCHOOL_ALIASES = {
    "Cowichan Valley": "Cowichan",
    "Greater Victoria": "Victoria",
    "Kamloops/Thompson": "Kamloops-Thompson",
    "Maple Ridge-Pitt Meadows": "Maple-Ridge Pitt-Meadows",
    "Nisga'a": "Nisgaa",
    "North Okanagan-Shuswap": "Okanagan-Shuswap",
    "Okanagan Similkameen": "Okanagan-Similkameen",
    "Okanagan Skaha": "Okanagan-Skaha",
    "Southeast Kootenay": "South East Kootenay",
}
DISTRICT_ALIASES = {"Skeena-Queen Charlotte": "North Coast"}
# A local government a report names differently in another year, under the name it has now.
RENAMED = {
    ("Alberni", "School District"): "Pacific Rim",
    ("Queen Charlotte", "Village"): "Daajing Giids",
    ("Westside", "District"): "West Kelowna",
    ("Powell River", "Regional District"): "qathet",
}
OFFICES = {
    "MAYOR": "mayor",
    "COUNCILLOR": "councillor",
    "DIRECTOR": "director",
    "ELECTORAL AREA DIRECTOR": "director",
    "TRUSTEE": "trustee",
    "ISLANDS TRUST COUNCILLOR": "trustee",
    "COMMISSIONER": "commissioner",
    "ADVISORY COUNCILLOR": "advisory",
}
SYSTEM_NOTE = (
    "A British Columbia local government elects its mayor, and each member it elects by area, by plurality: every "
    "elector marks one candidate and the candidate with the most votes wins. Members elected at large are chosen by "
    "block voting: every elector marks up to as many candidates as there are seats, and the candidates with the most "
    "votes fill them. A contest with no more candidates than seats is filled by acclamation without a vote."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CIVICINFO_SOURCE = key("source", "civicinfo")
CITY_SOURCE = key("source", "vancouver")
BOUNDS_SOURCE = key("source", "bounds")
PLURALITY = key("electoral_system", "plurality")
BLOCK = key("electoral_system", "block")


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def tidy(text: str) -> str:
    return " ".join(text.split())


def candidates_url(year: int) -> str:
    return f"{REPORTS}candidates-and-results.php?year={year}"


def turnout_url(year: int) -> str:
    return f"{REPORTS}voter-turnout.php?year={year}"


def layer_url(layer: str) -> str:
    return OPENMAPS + LAYERS[layer]


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, candidates_url(year), suffix=".html")
        raw.fetch(SOURCE, turnout_url(year), suffix=".html")
    for layer in LAYERS:
        raw.fetch(BOUNDS, layer_url(layer), suffix=".json")
    bc_vancouver.fetch()


def days() -> dict[int, date]:
    return {**EARLIER, **DAYS}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for year in days():
        election(year).write()


def emit() -> None:
    package.run("load", packages())


class Row(NamedTuple):
    government: str
    jurisdiction: str
    first: str
    middle: str
    last: str
    experience: str
    office: str
    elected: bool
    votes: int | None
    acclaimed: bool
    area: str
    organization: str


def _cell(text: str) -> str:
    return tidy(html.unescape(re.sub(r"<[^>]+>", "", text)))


def _tables(path: str) -> list[tuple[list[str], list[list[str]]]]:
    text = Path(path).read_text(encoding="utf8")
    found: list[tuple[list[str], list[list[str]]]] = []
    for body in re.findall(r"<table.*?</table>", text, re.DOTALL | re.IGNORECASE):
        heads = [_cell(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", body, re.DOTALL | re.IGNORECASE)]
        rows = [
            [_cell(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL | re.IGNORECASE)
        ]
        found.append((heads, [row for row in rows if row]))
    return found


def _count(text: str) -> int | None:
    text = text.replace(",", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


@functools.cache
def candidates(year: int) -> list[Row]:
    heads, rows = _tables(raw.cached(SOURCE, candidates_url(year)).path.as_posix())[0]
    col = {name: i for i, name in enumerate(heads)}
    found: list[Row] = []
    for row in rows:
        if len(row) != len(heads):
            raise ValueError(f"{year}: a candidate row has {len(row)} cells for {len(heads)} headings")
        office = row[col["Type"]]
        if office not in OFFICES:
            raise ValueError(f"{year}: unknown office {office!r}")
        area = row[col["Electoral/SD Area"]] if "Electoral/SD Area" in col else "\x00"
        name, jurisdiction = row[col["Local Government"]], row[col["Jurisdiction Type"]]
        name = RENAMED.get((name, jurisdiction), name)
        found.append(
            Row(
                name,
                jurisdiction,
                row[col["First Name"]],
                row[col["Middle Name"]],
                row[col["Last Name"]],
                row[col["Experience"]],
                office,
                row[col["Elected"]] == "YES",
                _count(row[col["Number of Votes"]]),
                row[col["Acclamation"]] == "YES",
                area,
                row[col["Elector Organization"]] if "Elector Organization" in col else "",
            )
        )
    return found


class Turnout(NamedTuple):
    seats: int | None
    ballots: int | None
    eligible: int | None


class Question(NamedTuple):
    government: str
    jurisdiction: str
    area: str
    text: str
    yes: int | None
    no: int | None
    passed: str


@functools.cache
def turnout(year: int) -> tuple[dict[tuple[str, str], Turnout], dict[tuple[str, str], Turnout], list[Question]]:
    """Turnout by local government, turnout by electoral area, and the referendum questions."""
    governments: dict[tuple[str, str], Turnout] = {}
    areas: dict[tuple[str, str], Turnout] = {}
    questions: list[Question] = []
    for heads, rows in _tables(raw.cached(SOURCE, turnout_url(year)).path.as_posix()):
        if "Referendum Question" in heads:
            col = {name: i for i, name in enumerate(heads)}
            for row in rows:
                questions.append(
                    Question(
                        RENAMED.get((row[0], row[1]), row[0]),
                        row[1],
                        row[col["Electoral Area/Island/SD Area"]] if "Electoral Area/Island/SD Area" in col else "",
                        row[col["Referendum Question"]],
                        _count(row[col["Votes For"]]),
                        _count(row[col["Votes Against"]]),
                        row[col["Passed?"]],
                    )
                )
        elif heads[0] == "Local Government":
            for row in rows:
                governments[(RENAMED.get((row[0], row[1]), row[0]), row[1])] = Turnout(
                    _count(row[2]), _count(row[3]), _count(row[4])
                )
        else:
            for row in rows:
                areas[(RENAMED.get((row[0], "Regional District"), row[0]), row[1])] = Turnout(
                    _count(row[2]), _count(row[3]), _count(row[4])
                )
    return governments, areas, questions


class Government(NamedTuple):
    name: str
    jurisdiction: str
    kind: str
    title: str
    path: str


def kind_of(jurisdiction: str) -> str:
    if jurisdiction in MUNICIPAL:
        return "municipality"
    return {
        "Regional District": "regional_district",
        "School District": "school_district",
        "Islands Trust": "islands_trust",
        "Commission": "commission",
        "Park Board": "commission",
    }[jurisdiction]


@functools.cache
def governments() -> dict[tuple[str, str], Government]:
    """Every local government that appears in a report of any year, with its official title and its address."""
    seen: dict[tuple[str, str], None] = {}
    for year in DAYS:
        for row in candidates(year):
            seen[(row.government, row.jurisdiction)] = None
        for question in turnout(year)[2]:
            seen[(question.government, question.jurisdiction)] = None
        for name, jurisdiction in turnout(year)[0]:
            seen[(name, jurisdiction)] = None
    names = Counter(name for name, jurisdiction in seen if jurisdiction in MUNICIPAL)
    found: dict[tuple[str, str], Government] = {}
    for name, jurisdiction in sorted(seen):
        kind = kind_of(jurisdiction)
        if kind == "municipality":
            title = MUNICIPAL[jurisdiction].format(name)
            path = slug(name) + (f"-{slug(jurisdiction)}" if names[name] > 1 else "")
        elif kind == "regional_district":
            title = f"{name} Regional District"
            path = f"{slug(name)}-regional-district"
        elif kind == "school_district":
            title = f"{name} School District"
            path = f"school-district-{slug(name)}"
        elif kind == "islands_trust":
            title, path = "Islands Trust", "islands-trust"
        else:
            title = name if jurisdiction == "Commission" else f"{name} Park Board"
            path = slug(title)
        found[(name, jurisdiction)] = Government(name, jurisdiction, kind, title, f"ca/bc/{path}")
    paths = Counter(government.path for government in found.values())
    clash = [path for path, n in paths.items() if n > 1]
    if clash:
        raise ValueError(f"local governments share an address: {clash}")
    return found


def polity(government: Government) -> str:
    return key("polity", government.name, government.jurisdiction)


def area(government: Government) -> str:
    return key("area", government.name, government.jurisdiction)


def sub_area(government: Government, label: str) -> str:
    return key("area", government.name, government.jurisdiction, label)


@functools.cache
def _layer(layer: str) -> list[tuple[dict[str, object], shapely.Geometry]]:
    data = json.loads(raw.cached(BOUNDS, layer_url(layer)).path.read_text(encoding="utf8"))
    found: list[tuple[dict[str, object], shapely.Geometry]] = []
    for feature in data["features"]:
        if feature.get("geometry") is None:
            continue
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        shape = shapely.simplify(shape, SIMPLIFY, preserve_topology=True)
        parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
        if parts:
            found.append((feature["properties"], shapely.set_srid(shapely.MultiPolygon(parts), 4326)))
    return found


def _bounds(layer: str, field: str) -> dict[str, shapely.Geometry]:
    found: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for properties, shape in _layer(layer):
        found[str(properties[field])].append(shape)
    result: dict[str, shapely.Geometry] = {}
    for name, shapes in found.items():
        union = shapely.union_all(shapes)
        parts = [part for part in shapely.get_parts(union) if shapely.get_type_id(part) in (3, 6)]
        result[name] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    return result


def _wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def _district_key(text: str) -> str:
    text = fold(text)
    text = re.sub(r"\b(regional district|regional municipality|the|of)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _outline(government: Government) -> shapely.Geometry | None:
    """The outline the province publishes for a local government, when it publishes one."""
    if government.kind == "municipality":
        name = ALIASES.get((government.name, government.jurisdiction), government.name)
        return _bounds("municipality", "ADMIN_AREA_ABBREVIATION").get(name)
    if government.kind == "regional_district":
        shapes = {_district_key(name): shape for name, shape in _bounds("regional_district", "ADMIN_AREA_NAME").items()}
        return shapes.get(_district_key(DISTRICT_ALIASES.get(government.name, government.name)))
    if government.kind == "school_district":
        shapes = {fold(name): shape for name, shape in _bounds("school_district", "SCHOOL_DISTRICT_NAME").items()}
        return shapes.get(fold(SCHOOL_ALIASES.get(government.name, government.name)))
    if government.kind == "islands_trust":
        return _bounds("islands_trust", "ADMIN_AREA_NAME").get("Islands Trust")
    if government.jurisdiction == "Park Board" and government.name == "Vancouver":
        return _bounds("municipality", "ADMIN_AREA_ABBREVIATION").get("Vancouver")
    return None


def _letter(text: str) -> str | None:
    found = re.search(r"electoral area\s+\"?([A-Za-z0-9]+)\b", text, re.IGNORECASE)
    return found.group(1).upper() if found else None


def _descriptor(text: str) -> str:
    """What names an electoral area besides its letter: the place in parentheses, or the whole name without its
    district's initials and the words electoral area."""
    inside = re.search(r"\(([^)]*)\)", text)
    if inside:
        return re.sub(r"[^a-z0-9]", "", fold(inside.group(1)))
    text = re.sub(r"^(?:[A-Za-z]*[A-Z]{2,}[A-Za-z]*)\s+", "", text.strip())
    text = re.sub(r"electoral area", " ", text, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", fold(text))


@functools.cache
def electoral_areas() -> dict[tuple[str, str], tuple[str, shapely.Geometry]]:
    """The regional district electoral areas the province publishes, by district key and by letter and descriptor."""
    found: dict[tuple[str, str], tuple[str, shapely.Geometry]] = {}
    for properties, shape in _layer("electoral_area"):
        district = _district_key(str(properties["ADMIN_AREA_GROUP_NAME"]))
        name = str(properties["ADMIN_AREA_NAME"])
        letter = _letter(name)
        if letter is not None:
            found[(district, letter)] = (name, shape)
        descriptor = _descriptor(name)
        if descriptor:
            found.setdefault((district, descriptor), (name, shape))
    return found


def electoral_area(government: Government, label: str) -> tuple[str, tuple[str, shapely.Geometry]] | None:
    """The electoral area a director's label names, with the key its area id derives from."""
    district = _district_key(DISTRICT_ALIASES.get(government.name, government.name))
    areas = electoral_areas()
    descriptor = _descriptor(label)
    letter = _letter(label)
    for tag in (letter, descriptor):
        if tag and (district, tag) in areas:
            return f"{district}:{areas[(district, tag)][0]}", areas[(district, tag)]
    return None


@functools.cache
def trust_areas() -> dict[str, tuple[str, shapely.Geometry]]:
    found: dict[str, tuple[str, shapely.Geometry]] = {}
    for properties, shape in _layer("trust_area"):
        name = str(properties["ADMIN_AREA_NAME"])
        found[fold(re.sub(r"\s*(Island )?Local Trust Area$", "", name))] = (name, shape)
    return found


class Place(NamedTuple):
    """The area a contest is for: the id and name of the area and whether the units are within it."""

    area: str
    equals: bool


def contest_area(government: Government, office: str, label: str) -> Place:
    """The area record a contest points to, and whether its reporting unit is that area."""
    if label.lower() in AT_LARGE or label == "\x00":
        return Place(area(government), True)
    if government.kind == "regional_district":
        found = electoral_area(government, label)
        if found:
            return Place(key("area", "electoral_area", found[0]), True)
    if government.kind == "islands_trust":
        found = trust_areas().get(fold(label))
        if found:
            return Place(key("area", "trust_area", fold(label)), True)
    return Place(area(government), False)


def used_areas() -> tuple[dict[str, tuple[Government, str, tuple[str, shapely.Geometry]]], dict[str, str]]:
    """The electoral areas and local trust areas that some contest of some year is for."""
    electoral: dict[str, tuple[Government, str, tuple[str, shapely.Geometry]]] = {}
    trust: dict[str, str] = {}
    found = governments()
    for year in DAYS:
        for row in candidates(year):
            if row.area == "\x00":
                continue
            government = found[(row.government, row.jurisdiction)]
            if government.kind == "regional_district":
                match = electoral_area(government, row.area)
                if match:
                    electoral[match[0]] = (government, match[0], match[1])
            elif government.kind == "islands_trust" and fold(row.area) in trust_areas():
                trust[fold(row.area)] = trust_areas()[fold(row.area)][0]
    return electoral, trust


def _area_name(government: Government, text: str) -> str:
    text = re.sub(r"^(?:[A-Za-z]*[A-Z]{2,}[A-Za-z]*)\s+", "", text.strip())
    return f"{government.name} {text}"


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "British Columbia's municipalities, regional districts, school districts, and the Islands Trust that held a "
        "general local election from 2008 to 2022, with the regional district electoral areas and local trust areas "
        "their directors and trustees are elected for, and the outlines the province publishes for them",
    )
    p.external |= {CANADA, BC, BC_AREA}
    p.add(
        "source",
        id=BOUNDS_SOURCE,
        kind="official_body",
        publisher="Province of British Columbia, Ministry of Municipal Affairs",
        title="Legally defined administrative areas of BC",
        url=CATALOGUE + LAYER_PAGES["municipality"],
        license="Open Government Licence - British Columbia",
        license_url="https://www2.gov.bc.ca/gov/content/data/open-data/open-government-licence-bc",
        attribution="Contains information licensed under the Open Government Licence - British Columbia",
    )
    for layer in LAYERS:
        p.artifact(raw.cached(BOUNDS, layer_url(layer)), BOUNDS_SOURCE)
    for year in DAYS:
        p.uses(raw.cached(SOURCE, candidates_url(year)))
        p.uses(raw.cached(SOURCE, turnout_url(year)))
    schemes = (
        ("ca-bc.municipality", "administrative", 3, "municipality"),
        ("ca-bc.regional_district", "administrative", 3, "regional district"),
        ("ca-bc.school_district", "administrative", 3, "school district"),
        ("ca-bc.local_authority", "administrative", 3, "local authority"),
        ("ca-bc.electoral_area", "electoral_district", 4, "electoral area"),
        ("ca-bc.trust_area", "electoral_district", 4, "local trust area"),
        ("ca-bc.result_unit", "precinct", 5, "reporting unit"),
    )
    for code, kind, rank, noun in schemes:
        p.add("scheme", code=code, polity_id=BC, kind=kind, rank=rank, noun=noun, code_system=None)
    scheme = {
        "municipality": "ca-bc.municipality",
        "regional_district": "ca-bc.regional_district",
        "school_district": "ca-bc.school_district",
        "islands_trust": "ca-bc.local_authority",
        "commission": "ca-bc.local_authority",
    }
    for government in governments().values():
        place = area(government)
        town = polity(government)
        p.add("area", id=place, scheme_code=scheme[government.kind])
        p.add("area_parent", area_id=place, parent_id=BC_AREA)
        p.name(place, government.title, "en", "official")
        outline = _outline(government)
        if outline is not None:
            p.add(
                "area_geometry",
                area_id=place,
                valid_from=None,
                valid_to=None,
                geom=_wkb(outline),
                label_point=_wkb(shapely.point_on_surface(outline)),
                quality="official",
                source_id=BOUNDS_SOURCE,
            )
        p.add("polity", id=town, kind="municipality" if government.kind == "municipality" else "region", iso_code=None)
        p.name(town, government.title, "en", "official")
        p.add("polity_parent", polity_id=town, parent_id=BC)
        p.add("polity_territory", polity_id=town, area_id=place)
        p.add("slug", path=government.path, entity_id=town, is_current=True)
    electoral, trust = used_areas()
    for tag, (government, _, (name, shape)) in sorted(electoral.items()):
        place = key("area", "electoral_area", tag)
        p.add("area", id=place, scheme_code="ca-bc.electoral_area")
        p.add("area_parent", area_id=place, parent_id=area(government))
        p.name(place, _area_name(government, name), "en", "official")
        p.add(
            "area_geometry",
            area_id=place,
            valid_from=None,
            valid_to=None,
            geom=_wkb(shape),
            label_point=_wkb(shapely.point_on_surface(shape)),
            quality="official",
            source_id=BOUNDS_SOURCE,
        )
    trust_government = governments()[("Islands Trust", "Islands Trust")]
    for tag, name in sorted(trust.items()):
        place = key("area", "trust_area", tag)
        shape = trust_areas()[tag][1]
        p.add("area", id=place, scheme_code="ca-bc.trust_area")
        p.add("area_parent", area_id=place, parent_id=area(trust_government))
        p.name(place, name, "en", "official")
        p.add(
            "area_geometry",
            area_id=place,
            valid_from=None,
            valid_to=None,
            geom=_wkb(shape),
            label_point=_wkb(shapely.point_on_surface(shape)),
            quality="official",
            source_id=BOUNDS_SOURCE,
        )
    return p


def organization(government: Government, text: str) -> str:
    return key("party", government.name, government.jurisdiction, fold(text))


def body(government: Government) -> str:
    return key("body", government.name, government.jurisdiction)


def mayoralty(government: Government) -> str:
    return key("office", government.name, government.jurisdiction)


BODY_TITLES = {
    "municipality": "{} council",
    "regional_district": "{} board of directors",
    "school_district": "{} board of education",
    "islands_trust": "{} council",
    "commission": "{} commission",
}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils, boards, mayoralties, elector organizations, and electoral systems of British Columbia's "
        "general local elections, with the sources of their results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, BC}
    p.add(
        "source",
        id=CIVICINFO_SOURCE,
        kind="aggregator",
        publisher="CivicInfo BC",
        title="Election reports: candidates and results, voter turnout",
        url=CIVICINFO + "elections",
        attribution="CivicInfo BC, from the results local governments report to it",
    )
    for year in DAYS:
        p.artifact(raw.cached(SOURCE, candidates_url(year)), CIVICINFO_SOURCE)
        p.artifact(raw.cached(SOURCE, turnout_url(year)), CIVICINFO_SOURCE)
    p.add(
        "source",
        id=CITY_SOURCE,
        kind="official_body",
        publisher="City of Vancouver",
        title="Municipal election results",
        url=bc_vancouver.PAGE,
        license="Open Government Licence - Vancouver",
        license_url="https://opendata.vancouver.ca/pages/licence/",
        attribution="Contains information licensed under the Open Government Licence - Vancouver",
    )
    for year in bc_vancouver.FILES:
        p.artifact(raw.cached(bc_vancouver.SOURCE, bc_vancouver.FILES[year]), CITY_SOURCE)
    for system, formula, title in (
        (PLURALITY, "fptp", "British Columbia local plurality voting"),
        (BLOCK, "block", "British Columbia local block voting"),
    ):
        p.add(
            "electoral_system", id=system, polity_id=BC, valid_from=None, valid_to=None,
            rules={"formula": formula}, rules_version=1,
        )  # fmt: skip
        p.name(system, title, "en", "official")
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for government in governments().values():
        council = body(government)
        p.add("body", id=council, polity_id=polity(government), kind="council")
        p.name(council, BODY_TITLES[government.kind].format(government.title), "en", "official")
        if government.kind == "municipality":
            office = mayoralty(government)
            p.add("office", id=office, polity_id=polity(government), kind="mayor")
            p.name(office, f"Mayor of {government.title}", "en", "official")
    seen: dict[str, tuple[Government, str]] = {}
    for year in DAYS:
        governments_ = governments()
        for row in candidates(year):
            if row.organization:
                government = governments_[(row.government, row.jurisdiction)]
                seen[organization(government, row.organization)] = (government, row.organization)
    for year in bc_vancouver.FILES:
        for race in bc_vancouver.count(year).races:
            for candidate in race.candidates:
                government = governments()[VANCOUVER[race.office]]
                if race.office != "question" and (id := vancouver_party(government, candidate.party)):
                    seen[id] = (government, candidate.party)
    for id, (government, text) in sorted(seen.items()):
        p.add("party", id=id, kind="party", polity_id=polity(government))
        p.name(id, text, "en", "official")
    return p


OFFICE_TITLES = {
    "mayor": "Mayor",
    "councillor": "Councillor",
    "director": "Electoral area director",
    "trustee": "Trustee",
    "commissioner": "Commissioner",
    "advisory": "Advisory councillor",
}


class Group(NamedTuple):
    government: Government
    office: str
    label: str


def _label(text: str) -> str:
    return "" if text.lower() in AT_LARGE else text


def groups(year: int) -> tuple[dict[Group, list[Row]], list[str]]:
    """The candidates of each contest, and what the year's report leaves out because it names no area."""
    found: dict[Group, list[Row]] = defaultdict(list)
    omitted: Counter[str] = Counter()
    lookup = governments()
    if year not in DAYS:
        return found, []
    for row in candidates(year):
        office = OFFICES[row.office]
        government = lookup[(row.government, row.jurisdiction)]
        if row.area == "\x00":
            if office not in ("mayor", "councillor") or government.kind != "municipality":
                omitted[f"{row.office.lower()} ({government.kind.replace('_', ' ')})"] += 1
                continue
            found[Group(government, office, "")].append(row)
        else:
            found[Group(government, office, _label(row.area))].append(row)
    return found, [f"{n} {what} candidates" for what, n in sorted(omitted.items())]


def person_name(row: Row) -> str:
    return " ".join(part for part in (row.first, row.middle, row.last) if part)


def contest_title(group: Group) -> str:
    title = f"{group.government.title}: {OFFICE_TITLES[group.office]}"
    return f"{title} ({group.label})" if group.label else title


VANCOUVER = {
    "mayor": ("Vancouver", "City"),
    "councillor": ("Vancouver", "City"),
    "question": ("Vancouver", "City"),
    "plebiscite": ("Vancouver", "City"),
    "commissioner": ("Vancouver", "Park Board"),
    "trustee": ("Vancouver", "School District"),
}
NO_PARTY = {"", "np", "ind", "independent", "non partisan", "non-partisan", "n/a"}


def vancouver_party(government: Government, text: str) -> str | None:
    return organization(government, text) if fold(text) not in NO_PARTY else None


def vancouver(year: int) -> bc_vancouver.Count | None:
    return bc_vancouver.count(year) if year in bc_vancouver.FILES else None


def election(year: int) -> Package:
    day = days()[year]
    city = vancouver(year)
    covered = {(VANCOUVER[race.office], race.office) for race in city.races} if city else set()
    close = day + timedelta(days=1)
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} British Columbia general local elections: the mayors and councils of its municipalities, and "
        "the boards of its regional districts and school districts and the Islands Trust where the report names "
        "the area, with the votes of each candidate and the turnout",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, BC}
    if year in DAYS:
        p.uses(raw.cached(SOURCE, candidates_url(year)))
        p.uses(raw.cached(SOURCE, turnout_url(year)))
    if city:
        p.uses(raw.cached(bc_vancouver.SOURCE, bc_vancouver.FILES[year]))
    by_government, area_turnout = (turnout(year)[0], turnout(year)[1]) if year in DAYS else ({}, {})
    contests, _ = groups(year)
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=BC, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} British Columbia general local elections", "en", "official")
    p.add("slug", path=f"ca/bc/{day}-local", entity_id=umbrella, is_current=True)
    elections: dict[tuple[str, str], str] = {}
    units: dict[tuple[str, str, str], str] = {}
    taken: set[str] = set()
    over: set[tuple[str, str]] = set()
    for group, rows in sorted(contests.items(), key=lambda item: (item[0].government, item[0].office, item[0].label)):
        government = group.government
        gid = (government.name, government.jurisdiction)
        if (gid, group.office) in covered:
            continue
        if gid not in elections:
            vote = elections[gid] = key("election", year, *gid)
            p.add(
                "election",
                id=vote,
                polity_id=polity(government),
                parent_id=umbrella,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.name(vote, f"{year} {government.title} election", "en", "official")
            p.add("slug", path=f"{government.path}/{day}-local", entity_id=vote, is_current=True)
        place = contest_area(government, group.office, group.label)
        ukey = (*gid, group.label)
        if ukey not in units:
            unit = units[ukey] = key("unit", day, *ukey)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CANADA,
                scheme_code="ca-bc.result_unit",
                code=f"{day}:{government.path}:{group.label}".rstrip(":"),
                area_relation="equals" if place.equals else "within",
                area_id=place.area,
                kind="standard",
                valid_from=day,
                valid_to=close,
            )
        unit = units[ukey]
        elected = [row for row in rows if row.elected]
        seats = len(elected) or 1
        mayoral = group.office == "mayor"
        tag = f"{government.name}:{government.jurisdiction}:{group.office}:{group.label}"
        contest = key("contest", year, tag)
        p.add(
            "contest",
            id=contest,
            election_id=elections[gid],
            kind="office" if mayoral else "body_seats",
            office_id=mayoralty(government) if mayoral else None,
            body_id=None if mayoral else body(government),
            district_area_id=place.area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=PLURALITY if seats == 1 else BLOCK,
            default_result_kind="counted",
        )
        p.name(contest, contest_title(group), "en", "official")
        path = f"{government.path}/{day}-local/{group.office}" + (f"-{slug(group.label)}" if group.label else "")
        while path in taken:
            path += "-2"
        taken.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        counted = any(row.votes for row in rows)
        tacit = all(row.acclaimed for row in rows) or (not counted and any(row.elected for row in rows))
        result_set = key("result_set", year, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[gid],
            contest_id=contest,
            stage="official",
            kind="tacit" if tacit else "counted",
            source_id=CIVICINFO_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0 if counted and not tacit else None,
        )
        for position, row in enumerate(rows, 1):
            name = person_name(row)
            seq = f"{tag}:{position}"
            person = key("person", year, seq)
            p.add("person", id=person, birth_precision=None)
            p.name(person, name, "en", "official")
            option = key("option", year, seq)
            party = organization(government, row.organization) if row.organization else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=None,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", year, seq),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party,
                is_incumbent=row.experience.startswith("Incumbent"),
            )
            if row.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if not tacit and row.votes is not None:
                _value(p, result_set, unit, option, 1, row.votes)
        if tacit:
            continue
        if group.office == "director" or government.kind == "islands_trust":
            known = area_turnout.get((government.name, group.label))
        elif group.label == "" and government.kind == "municipality":
            known = by_government.get(gid)
        else:
            known = None
        if known is not None:
            if known.ballots is not None:
                _value(p, result_set, unit, None, 3, known.ballots)
            if known.eligible:
                _value(p, result_set, unit, None, 2, known.eligible)
            if known.ballots is not None and known.eligible and known.ballots > known.eligible:
                over.add((government.name, group.label))
    if city:
        _vancouver(p, year, city, elections, umbrella, taken, contests)
    _questions(p, year, elections, umbrella, taken, skip=("Vancouver", "City") if city else None)
    if over:
        p.accept(
            "accounting.turnout",
            f"CivicInfo BC's own figures give more ballots cast than estimated eligible voters for {len(over)} "
            "local governments or areas; the eligible voters are an estimate the report itself calls an estimate",
        )
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def question_type(text: str) -> str:
    lowered = text.lower()
    if "non-binding" in lowered or "nonbinding" in lowered:
        return "plebiscite"
    if "borrow" in lowered or "loan authorization" in lowered:
        return "mandatory_referendum"
    return "optional_referendum"


def _questions(
    p: Package,
    year: int,
    elections: dict[tuple[str, str], str],
    umbrella: str,
    taken: set[str],
    skip: tuple[str, str] | None = None,
) -> None:
    """The referendum and assent questions the turnout report lists, each a yes-or-no contest of its government."""
    day = DAYS[year] if year in DAYS else EARLIER[year]
    lookup = governments()
    counts: Counter[tuple[str, str]] = Counter()
    for question in turnout(year)[2] if year in DAYS else []:
        if question.yes is None or question.no is None or (question.government, question.jurisdiction) == skip:
            continue
        government = lookup[(question.government, question.jurisdiction)]
        gid = (government.name, government.jurisdiction)
        counts[gid] += 1
        number = counts[gid]
        if gid not in elections:
            vote = elections[gid] = key("election", year, *gid)
            p.add(
                "election",
                id=vote,
                polity_id=polity(government),
                parent_id=umbrella,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.name(vote, f"{year} {government.title} election", "en", "official")
            p.add("slug", path=f"{government.path}/{day}-local", entity_id=vote, is_current=True)
        tag = f"{government.name}:{government.jurisdiction}:question:{number}"
        unit = key("unit", day, *gid, "question", number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CANADA,
            scheme_code="ca-bc.result_unit",
            code=f"{day}:{government.path}:question-{number}",
            area_relation="equals" if not question.area else "within",
            area_id=area(government),
            kind="standard",
            valid_from=day,
            valid_to=day + timedelta(days=1),
        )
        contest = key("contest", year, tag)
        p.add(
            "contest",
            id=contest,
            election_id=elections[gid],
            kind="ballot_question",
            district_area_id=area(government),
            round_number=1,
            electoral_system_id=PLURALITY,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type=question_type(question.text))
        p.name(contest, f"{government.title}: Referendum question {number}", "en", "official")
        path = f"{government.path}/{day}-local/question-{number}"
        taken.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        p.add(
            "ballot_question_text",
            id=key("question_text", year, tag),
            contest_id=contest,
            kind="text",
            lang="en",
            body=question.text,
            source_id=CIVICINFO_SOURCE,
        )
        result_set = key("result_set", year, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[gid],
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CIVICINFO_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        options: dict[str, str] = {}
        for position, (answer, label, votes) in enumerate((("yes", "Yes", question.yes), ("no", "No", question.no)), 1):
            options[answer] = key("option", year, tag, answer)
            p.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            _value(p, result_set, unit, options[answer], 1, votes)
        if question.passed in ("YES", "NO"):
            p.add("outcome", result_set_id=result_set, option_id=options[question.passed.lower()], kind="won")


def _unit(p: Package, day: date, units: dict[str, str], code: str, name: str) -> str:
    if code not in units:
        unit = units[code] = key("unit", day, "vancouver", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CANADA,
            scheme_code="ca-bc.result_unit",
            code=f"{day}:vancouver:{code}",
            area_relation="within",
            area_id=area(governments()[("Vancouver", "City")]),
            kind="standard",
            valid_from=day,
            valid_to=day + timedelta(days=1),
        )
        if name and name != code:
            p.name(unit, name, "en", "official")
    return units[code]


def _elected(race: bc_vancouver.Race, rows: list[Row]) -> set[int]:
    """The candidates of a Vancouver race that CivicInfo BC reports elected, found by their votes, or by their names
    where the report's votes are not the city's final count."""
    won = Counter(row.votes for row in rows if row.elected and row.votes)
    totals = Counter(candidate.total for candidate in race.candidates)
    picked = {
        i for i, candidate in enumerate(race.candidates) if candidate.total in won and totals[candidate.total] == 1
    }
    if len(picked) == race.seats and sum(won.values()) == race.seats:
        return picked
    picked = set()
    for row in (row for row in rows if row.elected):
        last, first = fold(row.last), (fold(row.first).split() or [""])[0]
        found = [
            i
            for i, candidate in enumerate(race.candidates)
            if fold(candidate.name).endswith(last) and fold(candidate.name).startswith(first)
        ]
        if len(found) == 1:
            picked.add(found[0])
    return picked if len(picked) == race.seats and sum(row.elected for row in rows) == race.seats else set()


def _vancouver(
    p: Package,
    year: int,
    city: bc_vancouver.Count,
    elections: dict[tuple[str, str], str],
    umbrella: str,
    taken: set[str],
    contests: dict[Group, list[Row]],
) -> None:
    """The city's own results by voting division or place, in place of the report's totals for the same races."""
    day = days()[year]
    lookup = governments()
    units: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for race in city.races:
        gid = VANCOUVER[race.office]
        government = lookup[gid]
        if gid not in elections:
            vote = elections[gid] = key("election", year, *gid)
            p.add(
                "election",
                id=vote,
                polity_id=polity(government),
                parent_id=umbrella,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.name(vote, f"{year} {government.title} election", "en", "official")
            p.add("slug", path=f"{government.path}/{day}-local", entity_id=vote, is_current=True)
        question = race.office in ("question", "plebiscite")
        if question:
            counts[race.office] += 1
        number = counts[race.office]
        tag = f"vancouver:{race.office}" + (f":{number}" if question else "")
        contest = key("contest", year, tag)
        mayoral = race.office == "mayor"
        p.add(
            "contest",
            id=contest,
            election_id=elections[gid],
            kind="ballot_question" if question else ("office" if mayoral else "body_seats"),
            office_id=mayoralty(government) if mayoral else None,
            body_id=None if mayoral or question else body(government),
            district_area_id=area(government),
            magnitude=None if question else race.seats,
            round_number=1,
            electoral_system_id=PLURALITY if race.seats == 1 else BLOCK,
            default_result_kind="counted",
        )
        if question:
            plebiscite = race.office == "plebiscite"
            p.add("ballot_question", contest_id=contest, type="plebiscite" if plebiscite else "mandatory_referendum")
            label = f"{'Plebiscite question' if plebiscite else 'Question'} {number}"
            p.name(contest, f"{government.title}: {label}", "en", "official")
            path = f"{government.path}/{day}-local/{race.office}-{number}"
            p.add(
                "ballot_question_text",
                id=key("question_text", year, tag),
                contest_id=contest,
                kind="text",
                lang="en",
                body=race.title,
                source_id=CITY_SOURCE,
            )
        else:
            names = {
                "mayor": "Mayor",
                "councillor": "Councillor",
                "commissioner": "Park board commissioner",
                "trustee": "School trustee",
            }
            p.name(contest, f"{government.title}: {names[race.office]}", "en", "official")
            path = f"{government.path}/{day}-local/{race.office}"
        while path in taken:
            path += "-2"
        taken.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", year, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[gid],
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CITY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        rows = next(
            (
                r
                for g, r in contests.items()
                if (g.government.name, g.government.jurisdiction) == gid and g.office == race.office
            ),
            [],
        )
        won = _elected(race, rows) if not question else set()
        for position, candidate in enumerate(race.candidates, 1):
            seq = f"{tag}:{position}"
            option = key("option", year, seq)
            if question:
                answer = candidate.name.strip().lower()
                if answer not in ("yes", "no"):
                    raise ValueError(f"{year}: question {number} has the answer {candidate.name!r}")
                name = answer.title()
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=name,
                )
            else:
                name = candidate.name
                person = key("person", year, seq)
                party = vancouver_party(government, candidate.party)
                p.add("person", id=person, birth_precision=None)
                p.name(person, name, "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=None,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", year, seq),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party,
                )
            if position - 1 in won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            for code, votes in candidate.votes.items():
                _value(p, result_set, _unit(p, day, units, code, city.units.get(code, code)), option, 1, votes)
            if candidate.total is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=candidate.total)
        for measure, figures in ((3, race.ballots), (2, race.registered)):
            for code, value in figures.items():
                _value(p, result_set, _unit(p, day, units, code, city.units.get(code, code)), None, measure, value)
