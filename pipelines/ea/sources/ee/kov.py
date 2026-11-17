"""Estonian municipal council elections, 1993 to 2025, from the State Electoral Office and the National Electoral
Committee.

Every council is elected in one or more electoral districts. The open data of 2013 to 2025 print, for each district,
every list's and every candidate's votes in every polling division of the municipality, plus the electronic votes and,
from 2021, the paper votes cast outside the voter's home district. The archive pages of 1993 to 2009 print the same
for every municipality. Those are the finest figures the authorities publish.
"""

import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from uuid import uuid5
from xml.etree import ElementTree

import httpx
import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ee.kov"
API = "https://opendata.valimised.ee/api"
SITE = "https://www.valimised.ee/sites/default/files/2022-02"
ESTONIA = str(uuid5(package.NAMESPACE, "polity\x1fEE"))
VOTES, REGISTERED, CAST, VALID, PREFERENCE = 1, 2, 3, 4, 7
DERIVED = 1
LICENSE = "CC BY 4.0"
INDEPENDENTS = "Üksikkandidaadid"
DIVISION = re.compile(r"E?J\d+")


@dataclass(frozen=True)
class Spec:
    key: str
    day: date
    zip: str | None = None
    api: str | None = None
    html: str | None = None

    @property
    def year(self) -> int:
        return self.day.year


ELECTIONS = {
    spec.key: spec
    for spec in (
        Spec("kov-1993", date(1993, 10, 17), html="k93"),
        Spec("kov-1996", date(1996, 10, 20), html="k96"),
        Spec("kov-1999", date(1999, 10, 17), html="k99"),
        Spec("kov-2002", date(2002, 10, 20), html="k02"),
        Spec("kov-2005", date(2005, 10, 16), html="k05"),
        Spec("kov-2009", date(2009, 10, 18), html="kov09"),
        Spec("kov-2013", date(2013, 10, 20), zip="KOV2013_election_result_data.zip"),
        Spec("kov-2017", date(2017, 10, 15), zip="KOV2017_election_result_data.zip"),
        Spec("kov-2021", date(2021, 10, 17), api="KOV_2021"),
        Spec("kov-2025", date(2025, 10, 19), api="KOV_2025"),
    )
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


# ---------------------------------------------------------------- files


def listing_url(spec: Spec) -> str:
    return f"{API}/{spec.api}/"


def api_names(spec: Spec) -> list[str]:
    found = json.loads(raw.cached(SOURCE, listing_url(spec)).path.read_text())
    return sorted(item["name"] for item in found if item["type"] == "file")


def api_url(spec: Spec, name: str) -> str:
    return f"{API}/{spec.api}/{name}"


def zip_url(spec: Spec) -> str:
    return f"{SITE}/{spec.zip}"


def source_urls(spec: Spec) -> list[str]:
    """Every raw file an election's package cites."""
    if spec.zip:
        return [zip_url(spec)]
    if spec.api:
        wanted = [
            name
            for name in api_names(spec)
            if name.startswith("DETAILED_RESULT_PARISH_")
            or name in {"RESULTS.xml", "metadata.json", "POSTELECTION_PARTICIPATION_INFO.xml"}
        ]
        return [listing_url(spec), *(api_url(spec, name) for name in wanted)]
    if spec.html:
        return html_files(spec)
    return []


def fetch() -> None:
    for spec in ELECTIONS.values():
        if spec.zip:
            raw.fetch(SOURCE, zip_url(spec), suffix=".zip")
        elif spec.api:
            raw.fetch(SOURCE, listing_url(spec), suffix=".json")
            urls = [url for url in source_urls(spec) if url != listing_url(spec)]
            suffixed = {url: ".json" if url.endswith(".json") else ".xml" for url in urls}
            for suffix in sorted(set(suffixed.values())):
                found = raw.fetch_many(SOURCE, [u for u, s in suffixed.items() if s == suffix], suffix=suffix)
                if len(found) != sum(1 for s in suffixed.values() if s == suffix):
                    raise RuntimeError(f"{spec.key}: some {suffix} files could not be fetched")
    for spec in ELECTIONS.values():
        if spec.html:
            found = raw.fetch_many(SOURCE, html_files(spec), suffix=".html")
            if len(found) != len(html_files(spec)):
                raise RuntimeError(f"{spec.key}: some pages could not be fetched")
    for url in (OLD_SHAPES, NEW_SHAPES):
        raw.fetch(SOURCE, url, suffix=".zip")


# ---------------------------------------------------------------- reading the open data


def plain(data: bytes) -> ElementTree.Element:
    return ElementTree.fromstring(re.sub(rb'\sxmlns="[^"]*"', b"", data, count=1))


def child(element: ElementTree.Element | None, tag: str) -> ElementTree.Element:
    found = element.find(tag) if element is not None else None
    if found is None:
        raise LookupError(f"no <{tag}> element")
    return found


def text(element: ElementTree.Element | None, tag: str) -> str:
    if element is None:
        return ""
    found = element.find(tag)
    return clean(found.text if found is not None else "")


def num(value: str | None) -> int:
    return int(float(value)) if value not in (None, "") else 0


def members(spec: Spec, pattern: str) -> list[tuple[str, bytes]]:
    """The XML files of an election whose names match a pattern, with their contents."""
    if spec.zip:
        archive = zipfile.ZipFile(raw.cached(SOURCE, zip_url(spec)).path)
        return [
            (name.rsplit("/", 1)[-1], archive.read(name))
            for name in sorted(archive.namelist())
            if re.search(pattern, name.rsplit("/", 1)[-1]) and name.endswith(".xml")
        ]
    out = []
    for name in api_names(spec):
        if re.search(pattern, name) and name.endswith(".xml"):
            out.append((name, raw.cached(SOURCE, api_url(spec, name)).path.read_bytes()))
    return out


@dataclass
class Candidate:
    regno: int
    name: str
    elected: bool
    votes: dict[str, int]


@dataclass
class Entry:
    """A list (or the independent candidates) in one district."""

    name: str
    total: dict[str, int]
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def independents(self) -> bool:
        return self.name.startswith("Üksik")


@dataclass
class District:
    number: str
    seats: int
    entries: dict[str, Entry]
    declared_votes: dict[str, int] = field(default_factory=dict)
    declared_seats: dict[str, int] = field(default_factory=dict)
    registered: int | None = None
    cast: int | None = None
    divisions: list[str] = field(default_factory=list)
    title: str = ""


@dataclass
class Muni:
    code: str
    name: str
    county: str
    districts: list[District]
    registered: dict[str, int] = field(default_factory=dict)
    cast: dict[str, int] = field(default_factory=dict)
    valid: int | None = None
    party_votes: dict[str, int] = field(default_factory=dict)
    party_seats: dict[str, int] = field(default_factory=dict)
    coded: bool = True
    parts: dict[tuple[str, str], tuple[int | None, int | None]] = field(default_factory=dict)


def unit_votes(cells: list[tuple[str, int]]) -> dict[str, int]:
    """Votes by unit from a row of cells, whichever of the three layouts the year uses.

    2013 prints each polling division (`J1`) and the electronic votes (`E`); 2017 prints each division's paper (`J1P`,
    kept as `J1`) and electronic (`J1E`, kept as `EJ1`: the votes of the division's voters) votes; 2021 and later print
    each division, the electronic votes, and the paper votes cast outside the home district (`VPR`). `R` is the total and
    `P` the paper total; neither is a unit."""
    out: dict[str, int] = {}
    split = any(re.fullmatch(r"J\d+[PE]", name) for name, _ in cells)
    for name, value in cells:
        match = re.fullmatch(r"(J\d+)([PE]?)", name)
        if match:
            label = f"E{match.group(1)}" if match.group(2) == "E" else match.group(1)
            out[label] = out.get(label, 0) + value
        elif name in {"E", "VPR"} and not split:
            out[name] = out.get(name, 0) + value
    return out


def cells(row: ElementTree.Element | None) -> list[tuple[str, int]]:
    if row is None:
        return []
    return [(text(c, "name"), num(text(c, "value"))) for c in row.iter("votesDistributionCell")]


def mandate_rows(root: ElementTree.Element) -> dict[str, dict[str, dict[str, int]]]:
    """For each list: `votes` and `seats`, each by `total` and by district number."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for line in root.iter("partyVotesAndMandatesRow"):
        name = text(line, "name")
        found: dict[str, dict[str, int]] = {"votes": {}, "seats": {}}
        part = ""
        for item in line.iter("votesAndMandatesRow"):
            label, value = text(item, "name"), num(text(item, "value"))
            if label == "H":
                part = "votes"
                found[part]["total"] = value
            elif label == "M":
                part = "seats"
                found[part]["total"] = value
            elif label.startswith("R") and part:
                found[part][label[1:]] = value
        out[name] = found
    return out


def read_muni(spec: Spec, code: str, name: str, county: str, data: bytes) -> Muni:
    root = plain(data)
    result = root.find(".//electionResult")
    if result is None:
        raise LookupError(f"{spec.key} {code} has no result")
    rows = mandate_rows(result)
    districts: list[District] = []
    for district in result.iter("district"):
        number = text(district, "districtNumber")
        entries: dict[str, Entry] = {}
        elected = 0
        for party in district.iter("party"):
            label = text(party, "name")
            entry = entries.setdefault(label, Entry(label, unit_votes(cells(party.find("votesDistributionRow")))))
            for person in party.iter("candidate"):
                chosen = text(person, "elected") == "true"
                elected += chosen
                entry.candidates.append(
                    Candidate(
                        num(text(person, "candidateRegNumber")),
                        text(person, "name"),
                        chosen,
                        unit_votes(cells(person.find("votesDistributionRow"))),
                    )
                )
        seats = num(text(district, "mandateCount"))
        found = District(number, seats, entries)
        for label, line in rows.items():
            if number in line["votes"]:
                found.declared_votes[label] = line["votes"][number]
            if number in line["seats"]:
                found.declared_seats[label] = line["seats"][number]
        districts.append(found)
    muni = Muni(code, name, county, districts)
    for line in result.iter("participationRow"):
        label = text(line, "name")
        if re.fullmatch(r"J\d+", label):
            muni.registered[label] = num(text(line, "totalVotersInList"))
            muni.cast[label] = num(text(line, "votedCount"))
        elif label == "E":
            muni.cast[label] = num(text(line, "votedCount"))
        elif re.fullmatch(r"R\d+", label):
            for found in districts:
                if found.number == label[1:]:
                    found.registered = num(text(line, "totalVotersInList"))
                    found.cast = num(text(line, "votedCount"))
    valid = result.find("validVotedCount")
    muni.valid = num(valid.text) if valid is not None else None
    total = rows.get("Kokku")
    if total:
        muni.party_votes = {label: line["votes"].get("total", 0) for label, line in rows.items() if label != "Kokku"}
        muni.party_seats = {label: line["seats"].get("total", 0) for label, line in rows.items() if label != "Kokku"}
        muni.party_votes["Kokku"] = total["votes"].get("total", 0)
        muni.party_seats["Kokku"] = total["seats"].get("total", 0)
    return muni


def admin_units(spec: Spec) -> dict[str, tuple[str, str]]:
    """Municipality code to its name and county name, from the office's own list."""
    out: dict[str, tuple[str, str]] = {}
    if spec.api:
        meta = json.loads(raw.cached(SOURCE, api_url(spec, "metadata.json")).path.read_text())
        counties = {c["ehak"]: clean(c["name"]) for c in meta["greaterMunicipalities"]}
        for item in meta["municipalities"]:
            county = counties.get(item["parentEhak"], "")
            out[item["ehak"]] = (clean(item["name"]), "" if county == clean(item["name"]) else county)
        return out
    ((_, data),) = members(spec, r"POSTELECTION_PARTICIPATION_INFO")

    def walk(unit: ElementTree.Element, county: str) -> None:
        children = unit.find("childAdminUnits")
        if children is not None and len(children):
            for child in children.findall("adminUnit"):
                walk(child, text(unit, "name") if not county else county)
        elif unit.find("participationInfoRow") is not None:
            out[text(unit, "code")] = (text(unit, "name"), county)

    for unit in child(plain(data), "data").findall("adminUnit"):
        walk(unit, "")
    return out


@cache
def read(spec: Spec) -> dict[str, Muni]:
    if spec.html:
        return read_html(spec)
    names = admin_units(spec)
    pattern = r"ELECTION_RESULT_\d{4}" if spec.key == "kov-2013" else r"DETAILED_RESULT_PARISH_\d{4}"
    out: dict[str, Muni] = {}
    for file, data in members(spec, pattern):
        found = re.search(r"_(\d{4})", file.replace("KOV_2013", "").replace("KOV2017", ""))
        if found is None:
            raise LookupError(f"{spec.key}: {file} has no municipality code")
        code = found.group(1)
        if code not in names:
            raise LookupError(f"{spec.key}: {file} has no name in the office's list")
        out[code] = read_muni(spec, code, *names[code], data)
    return out


OLD_SHAPES = "https://geoportaal.maaruum.ee/docs/haldus_asustus/omavalitsus_20171015_shp.zip"
NEW_SHAPES = "https://s3.pilw.io/rp-kemit-kataster/EHAK/omavalitsus_shp.zip"


@dataclass
class National:
    valid: int
    lists: dict[str, tuple[str, int]]  # name -> (code, votes); includes the alliances and independents as printed


@cache
def national(spec: Spec) -> National:
    """The country's valid votes and each party's votes, as the office prints them."""
    if spec.key == "kov-2013":
        ((_, data),) = members(spec, r"VOTING_RESULT_BY_PARTIES")
        top = next(
            u
            for u in plain(data).iter("partiesVotingResultInAdminUnit")
            if text(u, "adminUnitName") == "Eesti Vabariik"
        )
        lists = {
            text(v, "partyName"): (text(v, "partyCode"), num(text(v, "givenVotesCount")))
            for v in top.iter("partyVotes")
        }
        return National(num(text(top, "totalVotesInAdminUnit")), lists)
    ((_, data),) = members(spec, r"RESULTS" if spec.api else r"RESULTS_\d")
    for unit in plain(data).iter("resultsInAdminUnit"):
        code = text(unit.find("adminUnit"), "code")
        if code != "0000":
            continue
        if spec.key == "kov-2017":
            lists = {
                text(v, "name"): (text(v, "code"), num(text(v, "votesCount")))
                for v in child(unit, "parties").iter("party")
            }
            return National(num(text(unit, "totalVotes")), lists)
        lists = {}
        block = unit.find("votesAndMandates")
        for v in block if block is not None else []:
            lists[text(v, "name")] = (text(v, "code") or "ÜKSIK", num(text(v, "votes")))
        return National(num(text(unit.find("statistics"), "votes")), lists)
    raise LookupError(f"{spec.key}: no national result")


COALITION_CODE = "VAL_LIIDUD"


def party_names(spec: Spec) -> dict[str, tuple[str, str]]:
    """Casefolded list name to (party code, name) for the parties the office registers nationally.

    The archive pages carry no party codes, so a list there is a party only when its name is that of a party the
    open data registers or the 2009 page of party totals names."""
    if spec.html:
        return known_parties()
    out: dict[str, tuple[str, str]] = {}
    for name, (code, _) in national(spec).lists.items():
        if code in {COALITION_CODE, "ÜKSIK", "Üksik."} or name.startswith("Üksik") or name == "Valimisliidud":
            continue
        out[name.casefold()] = (code, name)
    return out


@cache
def known_parties() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for spec in ELECTIONS.values():
        if not spec.html:
            for folded, found in party_names(spec).items():
                out.setdefault(folded, found)
    for name in parties_2009()[1]:
        out.setdefault(name.casefold(), (f"2009:{changeset.slug(name)}", name))
    return out


# ---------------------------------------------------------------- places and shapes

EPOCH_END = date(2017, 10, 21)


@dataclass
class Place:
    id: str
    county: str
    names: dict[str, int] = field(default_factory=dict)
    codes: dict[str, list[int]] = field(default_factory=dict)
    coded: list[tuple[date, str]] = field(default_factory=list)
    first: Spec | None = None
    first_code: str = ""
    elections: list[str] = field(default_factory=list)


@cache
def places() -> tuple[dict[str, Place], dict[tuple[str, str], str]]:
    """Each municipality as one place across elections, and the place of every (election, code).

    A municipality in one election is the same place as one of an earlier election when it has the same name and
    county, or the same code and name. The office's codes are reissued from time to time, so a code alone never joins
    two."""
    found: dict[str, Place] = {}
    index: dict[tuple[str, str], str] = {}
    by_name: dict[tuple[str, str], str] = {}
    by_code: dict[str, str] = {}
    for spec in sorted(ELECTIONS.values(), key=lambda s: s.day):
        for code, muni in read(spec).items():
            name_key = (muni.county, muni.name.casefold())
            place_id = by_name.get(name_key)
            if (
                place_id is None
                and muni.coded
                and code in by_code
                and muni.name.casefold() in {n.casefold() for n in found[by_code[code]].names}
            ):
                place_id = by_code[code]
            if place_id is None:
                place_id = key("area", "ee.vald", f"{spec.year}:{code}")
                found[place_id] = Place(place_id, muni.county, first=spec, first_code=code)
            place = found[place_id]
            place.names[muni.name] = spec.year
            if muni.coded:
                place.codes.setdefault(code, []).append(spec.year)
                place.coded.append((spec.day, code))
                by_code[code] = place_id
            place.elections.append(spec.key)
            index[(spec.key, code)] = place_id
            by_name[name_key] = place_id
    return found, index


def code_rows(place: Place) -> list[tuple[str, date | None, date | None]]:
    """A place's codes, each valid from the election that first printed it until the election that printed another.

    An area holds one code at a time, so a place the office coded differently over the years gets one dated row per
    run of elections under the same code; the first row is open at its start and the last at its end."""
    runs: list[tuple[date, str]] = []
    for day, code in sorted(place.coded):
        if not runs or runs[-1][1] != code:
            runs.append((day, code))
    rows: list[tuple[str, date | None, date | None]] = []
    for i, (day, code) in enumerate(runs):
        end = runs[i + 1][0] if i + 1 < len(runs) else None
        rows.append((code, day if i else None, end))
    return rows


def place_name(place: Place) -> str:
    return max(place.names, key=lambda n: place.names[n])


TOLERANCE = 25.0
SHAPES_ID = key("source", "maaruum")
SOURCE_ID = key("source", "valimised")
HTML_SOURCE_ID = key("source", "vvk")


@cache
def outlines(url: str) -> dict[str, shapely.Geometry]:
    """The municipal outlines of one Land and Spatial Development Board file, by EHAK code, in WGS 84."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    base = max({n.rsplit(".", 1)[0] for n in archive.namelist() if n.endswith(".shp")})
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{base}.shp")),
        dbf=io.BytesIO(archive.read(f"{base}.dbf")),
        shx=io.BytesIO(archive.read(f"{base}.shx")),
        encoding="latin-1",
    )
    codes = [str(record.as_dict()["OKOOD"]) for record in reader.iterRecords() if record is not None]
    shapes = [
        shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
        for shape in reader.iterShapes()
        if shape is not None
    ]
    simple = shapely.coverage_simplify(numpy.array(shapes), TOLERANCE)
    project = pyproj.Transformer.from_crs(3301, 4326, always_xy=True)
    out: dict[str, shapely.Geometry] = {}
    for code, shape in zip(codes, simple, strict=True):
        moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
        parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
        out[code] = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    return out


def outline_rows(
    place: Place,
) -> list[tuple[shapely.Geometry, date | None, date | None, str]]:
    """The dated outlines of a place: those before the 2017 reform, and those since.

    The board publishes the municipalities as they stood on 15 October 2017 and as they stand now. The later file
    describes a place back to the reform only when the place kept its name and so its identity into the current
    plan; the reform took effect on 21 October 2017."""
    if not place.codes:
        return []
    old, new = outlines(OLD_SHAPES), outlines(NEW_SHAPES)
    rows: list[tuple[shapely.Geometry, date | None, date | None, str]] = []
    for code in place.codes:
        if min(place.codes[code]) <= 2013 and code in old:
            rows.append((old[code], None, EPOCH_END, "2017-10-15"))
            break
    latest = max(place.codes, key=lambda c: max(place.codes[c]))
    if "kov-2025" in place.elections and latest in new:
        started = any(e in place.elections for e in ("kov-2017", "kov-2021"))
        rows.append((new[latest], EPOCH_END if started else None, None, "current"))
    return rows


def put_shape(p: Package, area_id: str, shape: shapely.Geometry, start: date | None, end: date | None) -> None:
    p.add(
        "area_geometry",
        area_id=area_id,
        valid_from=start,
        valid_to=end,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SHAPES_ID,
    )


# ---------------------------------------------------------------- packages


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for name in ELECTIONS:
        election(name).write()


def emit() -> None:
    package.run("load", packages())


def source_rows(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Riigi Valimisteenistus",
        title="Estonian election results, open data",
        url="https://www.valimised.ee/en/archive/open-data-estonian-elections",
        license=LICENSE,
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Riigi Valimisteenistus, valimised.ee",
    )
    p.add(
        "source",
        id=SHAPES_ID,
        kind="official_body",
        publisher="Maa- ja Ruumiamet",
        title="Administrative and settlement division of Estonia: municipalities (omavalitsused)",
        url="https://geoportaal.maaruum.ee/est/ruumiandmed/haldus-ja-asustusjaotus-p119.html",
        license="Open data of the Land and Spatial Development Board, reuse with the source cited",
        attribution="Maa- ja Ruumiamet",
    )


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def whole(place: Place) -> str:
    """The district that is the whole of a municipality that has only one."""
    return area("ee.kov_ringkond", f"{place.id}:whole")


def district_area(spec: Spec, place: Place, code: str, number: str, single: bool) -> str:
    if single:
        return whole(place)
    return area("ee.kov_ringkond", f"{spec.day}:{code}:{number}")


def district_title(muni: Muni, district: District) -> str:
    suffix = f" ({district.title})" if district.title else ""
    return f"{muni.name}, valimisringkond nr {district.number}{suffix}"


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Estonian municipalities as the municipal council elections print them, with the Land and Spatial Development "
        "Board's outlines, and the councils' electoral districts",
    )
    p.external |= {ESTONIA, "ee.vald"}
    source_rows(p)
    for spec in ELECTIONS.values():
        for file in source_urls(spec):
            p.artifact(raw.cached(SOURCE, file), SOURCE_ID)
    for url in (OLD_SHAPES, NEW_SHAPES):
        p.artifact(raw.cached(SOURCE, url), SHAPES_ID)
    p.add(
        "scheme",
        code="ee.kov_ringkond",
        polity_id=ESTONIA,
        kind="electoral_district",
        rank=1,
        noun="council electoral district",
        code_system=None,
    )
    for vintage, url in (("2017-10-15", OLD_SHAPES), ("current", NEW_SHAPES)):
        p.add(
            "boundary_set",
            id=key("boundary_set", "ee.vald", vintage),
            scheme_code="ee.vald",
            vintage=vintage,
            as_of=raw.cached(SOURCE, url).retrieved_at.date(),
        )
    found, index = places()
    for place in sorted(found.values(), key=lambda x: x.id):
        p.add("area", id=place.id, scheme_code="ee.vald")
        for code, start, end in code_rows(place):
            p.add("area_code", area_id=place.id, code=code, valid_from=start, valid_to=end)
        latest = max(place.names.values())
        for name, year in sorted(place.names.items()):
            p.name(place.id, name, "et", "official", year == latest)
        for shape, start, end, _ in outline_rows(place):
            put_shape(p, place.id, shape, start, end)
    singles: set[str] = set()
    for spec in ELECTIONS.values():
        for code, muni in read(spec).items():
            place = found[index[(spec.key, code)]]
            many = len(muni.districts) > 1
            for district in muni.districts:
                if not many:
                    if place.id in singles:
                        continue
                    singles.add(place.id)
                    id = whole(place)
                    p.add("area", id=id, scheme_code="ee.kov_ringkond")
                    p.add(
                        "area_code", area_id=id, code=f"{place.first.year if place.first else ''}:{place.first_code}:1"
                    )
                    p.name(id, place_name(place), "et", "official")
                    p.add("area_parent", area_id=id, parent_id=place.id)
                    for shape, start, end, _ in outline_rows(place):
                        put_shape(p, id, shape, start, end)
                else:
                    id = district_area(spec, place, code, district.number, False)
                    p.add("area", id=id, scheme_code="ee.kov_ringkond")
                    p.add("area_code", area_id=id, code=f"{spec.day}:{code}:{district.number}")
                    p.name(id, district_title(muni, district), "et", "official")
                    p.add("area_parent", area_id=id, parent_id=place.id)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Estonian municipal councils, their electoral system, and the parties named on the councils' lists",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ESTONIA}
    for spec in ELECTIONS.values():
        for file in source_urls(spec):
            p.uses(raw.cached(SOURCE, file))
    found, _ = places()
    for place in sorted(found.values(), key=lambda x: x.id):
        body = key("body", place.id)
        p.add("body", id=body, polity_id=ESTONIA, kind="council")
        name = place_name(place)
        p.name(body, f"{name} volikogu", "et", "official")
        p.name(body, f"Council of {name}", "en", "english", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ESTONIA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    names: dict[str, dict[str, int]] = {}
    for spec in ELECTIONS.values():
        for code, name in party_names(spec).values():
            seen = names.setdefault(code, {})
            seen[name] = max(seen.get(name, 0), spec.year)
    for code, found_names in sorted(names.items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=ESTONIA)
        p.add("external_id", entity_id=id, scheme="ee.kov.party", value=code)
        latest = max(found_names.values())
        for name, year in sorted(found_names.items()):
            p.name(id, name, "et", "official", year == latest)
    return p


SYSTEM = key("electoral_system", "council")
SYSTEM_NOTE = (
    "A council is elected in one or more electoral districts by open lists. A candidate whose personal votes reach the "
    "district's simple quota is elected outright; the district's remaining seats go to the lists by a modified "
    "d'Hondt method, in the order of the candidates' personal votes; and seats the districts leave over are "
    "compensated across the whole municipality. The archive records the declared outcome and does not recompute it."
)
METHOD = (
    "Counted and published by the State Electoral Office (Vabariigi Valimiskomisjon) in the open data of valimised.ee. "
    "The votes are stored for each polling division of the municipality (kept as a unit of its own with its district), "
    "for the electronic votes of each district and, from 2021, for the paper votes cast outside the voter's home "
    "district. Each candidate's votes are stored as preference votes under the list, and each list's votes as the "
    "list total the source prints. The source splits votes by paper and electronic ballot; that split is not "
    "stored here. Where a municipality has more than one electoral district, each district is a contest; seats the "
    "council's compensation mandates add to a district are counted in it, so a district can hold more or fewer seats "
    "than its basic mandates."
)


def party(code: str) -> str:
    return key("party", code)


def unit_label(code: str, unit: str, number: str, name: str) -> str:
    if unit == "E":
        return f"{name}, e-hääled (valimisringkond {number})"
    if unit in {"R", "TOTAL"}:
        return f"{name}, valimisringkond {number}, kokku"
    if unit.startswith("EJ"):
        return f"{name}, e-hääled (jaoskonna {unit[2:]} valijad)"
    if unit == "VPR":
        return f"{name}, väljaspool elukohajärgset ringkonda hääletanud (valimisringkond {number})"
    return f"{name}, jaoskond {unit[1:]}"


def part_of(muni: Muni, number: str, unit: str, many: bool) -> tuple[int | None, int | None]:
    """A unit's voters on the roll and voters, where the source prints them.

    The electronic votes are one row for the whole municipality, so they are given to a district only when it is the
    municipality's only one."""
    if (number, unit) in muni.parts:
        return muni.parts[(number, unit)]
    if unit == "E" and many:
        return None, None
    if re.fullmatch(r"J\d+", unit) or unit == "E":
        return muni.registered.get(unit), muni.cast.get(unit)
    return None, None


def put(p: Package, result_set: str, unit_id: str, option: str | None, measure: int, value: int, derived: bool) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit_id,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=DERIVED if derived else 0,
        value=value,
    )


def election(name: str) -> Package:
    spec = ELECTIONS[name]
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {spec.year} municipal council elections in Estonia by electoral district and polling division",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ESTONIA, "ee.unit"}
    for file in source_urls(spec):
        p.uses(raw.cached(SOURCE, file))
    found, index = places()
    munis = read(spec)
    day = spec.day
    ident = key("election", name)
    p.add("election", id=ident, polity_id=ESTONIA, kind="local", date_from=day, date_to=day, status="held")
    p.name(ident, f"{day.year} Estonian municipal council elections", "en", "official")
    p.name(ident, f"Kohaliku omavalitsuse volikogu valimised {day.year}", "et", "native", False)
    path = f"ee/{day}-municipal-councils"
    p.add("slug", path=path, entity_id=ident, is_current=True)
    p.add("note", id=key("note", ident), entity_id=ident, kind="methodology", body_md=METHOD, lang="en")
    names = party_names(spec)
    units: dict[str, str] = {}
    slugs: set[str] = set()
    for code, muni in sorted(munis.items(), key=lambda item: item[1].name):
        place = found[index[(name, code)]]
        many = len(muni.districts) > 1
        homes: dict[str, str] = {}
        for district in muni.districts:
            for entry in district.entries.values():
                for unit in entry.total:
                    if DIVISION.fullmatch(unit):
                        homes.setdefault(unit, district.number)
        shared = {
            unit
            for district in muni.districts
            for entry in district.entries.values()
            for unit in entry.total
            if DIVISION.fullmatch(unit) and homes[unit] != district.number
        }

        def unit_of(district: str, unit: str, area_id: str) -> str:
            tag = f"{unit}:{district}" if not DIVISION.fullmatch(unit) or unit in shared else unit
            ucode = f"kov{day}:{code}:{tag}"
            id = units.get(ucode)
            if id is None:
                id = units[ucode] = key("unit", ucode)
                p.add(
                    "reporting_unit",
                    id=id,
                    polity_id=ESTONIA,
                    scheme_code="ee.unit",
                    code=ucode,
                    area_relation="within",
                    area_id=area_id,
                    kind="standard" if re.fullmatch(r"J\d+", unit) else "aggregate",
                )
                p.name(id, unit_label(code, unit, district, muni.name), "et", "official")
            return id

        body = key("body", place.id)
        for district in muni.districts:
            number = district.number
            contest = key("contest", name, code, number)
            elected = sum(1 for e in district.entries.values() for c in e.candidates if c.elected)
            coarse = not any(e.candidates for e in district.entries.values())
            if coarse or (spec.html and not elected):
                elected = district.seats
            district_id = district_area(spec, place, code, number, not many)
            p.add(
                "contest",
                id=contest,
                election_id=ident,
                kind="list_allocation",
                body_id=body,
                district_area_id=district_id,
                magnitude=elected,
                round_number=1,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            title = district_title(muni, district) if many else muni.name
            p.name(contest, title, "et", "official")
            label = (
                f"{muni.name} council, electoral district {number}{f' ({district.title})' if district.title else ''}"
                if many
                else f"{muni.name} council"
            )
            p.name(contest, label, "en", "english", False)
            slug = f"{path}/{changeset.slug(label)}"
            if slug in slugs:
                slug = f"{path}/{changeset.slug(f'{label} {muni.county}')}"
            if slug in slugs:
                slug = f"{path}/{changeset.slug(f'{label} {code}')}"
            if slug in slugs:
                raise ValueError(f"{name}: duplicate slug {slug}")
            slugs.add(slug)
            p.add("slug", path=slug, entity_id=contest, is_current=True)
            result_set = key("result_set", name, code, number)
            p.add(
                "result_set",
                id=result_set,
                election_id=ident,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD,
                lang="en",
            )
            if elected != district.seats:
                p.add(
                    "note",
                    id=key("note", result_set, "seats"),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=(
                        f"The page prints {district.seats} mandates for the district and marks {elected} candidates "
                        "as elected; the seats stored are those marked."
                        if spec.html
                        else f"The district's basic mandates were {district.seats}; {elected} candidates were "
                        "declared elected from its lists once the compensation mandates were allocated across the "
                        "city. The seats stored are those declared."
                    ),
                    lang="en",
                )
            if coarse:
                p.add(
                    "note",
                    id=key("note", result_set, "coarse"),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=(
                        "The source prints this district's votes for each list and its seats for each list, but not "
                        "its candidates or the votes by polling division. The votes are stored for the district as "
                        "a whole."
                    ),
                    lang="en",
                )
            every = sorted(
                {u for e in district.entries.values() for u in e.total}
                | {u for e in district.entries.values() for c in e.candidates for u in c.votes}
            )
            valid_units: dict[str, int] = {}
            for unit in every:
                valid_units[unit] = sum(e.total.get(unit, 0) for e in district.entries.values())
                uid = unit_of(number, unit, district_id)
                put(p, result_set, uid, None, VALID, valid_units[unit], True)
                registered, cast = part_of(muni, number, unit, many)
                if registered:
                    put(p, result_set, uid, None, REGISTERED, registered, False)
                if cast is not None:
                    put(p, result_set, uid, None, CAST, cast, False)
            for unit in district.divisions:
                if unit not in every:
                    uid = unit_of(number, unit, district_id)
                    registered, cast = part_of(muni, number, unit, many)
                    if registered:
                        put(p, result_set, uid, None, REGISTERED, registered, False)
                    if cast is not None:
                        put(p, result_set, uid, None, CAST, cast, False)
            if spec.year >= 2021 and district.registered is not None and district.cast is not None:
                total = unit_of(number, "TOTAL", district_id)
                put(p, result_set, total, None, REGISTERED, district.registered, False)
                put(p, result_set, total, None, CAST, district.cast, False)
                p.add(
                    "declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=district.registered
                )
                p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=district.cast)
            if "Kokku" in district.declared_votes:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=VALID,
                    value=district.declared_votes["Kokku"],
                )
            for entry in district.entries.values():
                code_ = names.get(entry.name.casefold())
                if entry.independents:
                    parent = key("option", name, code, number, "independents")
                    p.add(
                        "contest_option",
                        id=parent,
                        contest_id=contest,
                        kind="independents",
                        ballot_label="Independent candidates",
                    )
                    party_id = None
                else:
                    parent = key("option", name, code, number, "list", entry.name.casefold())
                    party_id = party(code_[0]) if code_ else None
                    p.add(
                        "contest_option",
                        id=parent,
                        contest_id=contest,
                        kind="list",
                        party_id=party_id,
                        ballot_label=entry.name,
                    )
                for unit in every:
                    put(
                        p,
                        result_set,
                        unit_of(number, unit, district_id),
                        parent,
                        VOTES,
                        entry.total.get(unit, 0),
                        False,
                    )
                if entry.name in district.declared_votes:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=parent,
                        measure=VOTES,
                        value=district.declared_votes[entry.name],
                    )
                wins = district.declared_seats.get(entry.name, 0) if not entry.candidates else 0
                for person in entry.candidates:
                    tag = f"{name}:{code}:{number}:{person.regno}"
                    full = person.name.title()
                    child = key("option", name, code, number, "candidate", person.regno)
                    p.add(
                        "contest_option",
                        id=child,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=parent,
                        party_id=party_id,
                        ballot_position=None if spec.html else person.regno or None,
                        ballot_label=full,
                    )
                    who = key("person", tag)
                    p.add("person", id=who, birth_precision=None)
                    p.name(who, full, "et", "official")
                    cand = key("candidacy", tag)
                    p.add(
                        "candidacy",
                        id=cand,
                        person_id=who,
                        option_id=parent,
                        role="list_member",
                        list_position=None if spec.html else person.regno or None,
                        ballot_name=full,
                        member_of_party_id=party_id,
                        is_independent=entry.independents,
                    )
                    for unit in every:
                        put(
                            p,
                            result_set,
                            unit_of(number, unit, district_id),
                            child,
                            PREFERENCE,
                            person.votes.get(unit, 0),
                            False,
                        )
                    if person.elected:
                        wins += 1
                        p.add("outcome", result_set_id=result_set, option_id=parent, candidacy_id=cand, kind="elected")
                if wins:
                    p.add("outcome", result_set_id=result_set, option_id=parent, kind="won", seats=wins)
    if spec.html:
        html_totals(spec, munis)
    else:
        totals(spec, munis)
    return p


def totals(spec: Spec, munis: dict[str, Muni]) -> None:
    """Fail unless the country's votes and each party's votes agree with the office's national figures."""
    official = national(spec)
    names = party_names(spec)
    mine: dict[str, int] = {}
    valid = 0
    for muni in munis.values():
        for district in muni.districts:
            for entry in district.entries.values():
                votes = sum(entry.total.values())
                valid += votes
                key_ = (
                    "ÜKSIK"
                    if entry.independents
                    else names[entry.name.casefold()][0]
                    if entry.name.casefold() in names
                    else COALITION_CODE
                )
                mine[key_] = mine.get(key_, 0) + votes
    if valid != official.valid:
        raise ValueError(f"{spec.key}: {valid} valid votes, the office prints {official.valid}")
    for label, (code, votes) in official.lists.items():
        if code in {"Üksik.", "ÜKSIK"} or label.startswith("Üksik"):
            code = "ÜKSIK"
        if code in mine and mine[code] != votes:
            raise ValueError(f"{spec.key} {label}: {mine[code]} votes, the office prints {votes}")
    matched = sum(1 for code, _ in official.lists.values() if code in mine)
    print(f"{spec.key}: {valid:,} valid votes and {matched} lists' votes match the office's national figures")


# ---------------------------------------------------------------- the archive pages of 1993 to 2009

VVK = "https://www.vvk.ee/varasemad"
COUNTIES = (
    "Harju", "Hiiu", "Ida-Viru", "Jõgeva", "Järva", "Lääne", "Lääne-Viru", "Põlva", "Pärnu", "Rapla", "Saare",
    "Tartu", "Valga", "Viljandi", "Võru",
)  # fmt: skip
COUNTY_CODES_2009 = {
    "0037": "Harju", "0039": "Hiiu", "0044": "Ida-Viru", "0049": "Jõgeva", "0051": "Järva", "0057": "Lääne",
    "0059": "Lääne-Viru", "0065": "Põlva", "0067": "Pärnu", "0070": "Rapla", "0074": "Saare", "0078": "Tartu",
    "0082": "Valga", "0084": "Viljandi", "0086": "Võru",
}  # fmt: skip
CITIES_2009 = {"0784": "Tallinn", "0795": "Tartu linn"}


def page(url: str) -> str:
    """A page of the archive as text; the older ones are Latin-1 and the newer ones UTF-8."""
    data = raw.cached(SOURCE, url).path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def ensure(url: str) -> str:
    """Fetch a page into the raw cache when it is not there yet, then read it."""
    raw.fetch(SOURCE, url, suffix=".html")
    return page(url)


def index_url(spec: Spec) -> str:
    folder = spec.html
    if spec.key == "kov-2009":
        return f"{VVK}/{folder}/detailed.html"
    return f"{VVK}/{folder}/tulemus/tulemus.html" if spec.key == "kov-2005" else f"{VVK}/{folder}/tulemus.stm"


PARTIES_2009 = f"{VVK}/kov09/erakonniti.html"
REPEATS: dict[str, list[str]] = {}


@cache
def html_files(spec: Spec) -> list[str]:
    """The pages an old election's package cites: the index, each county's page, and each municipality's table."""
    index = index_url(spec)
    top = ensure(index)
    urls = [index]
    folder = f"{VVK}/{spec.html}"
    if spec.key == "kov-2009":
        counties = sorted(set(re.findall(r"detailed_(\d{4})\.html", top)))
        found = set(counties)
        for code in counties:
            if code in COUNTY_CODES_2009:
                url = f"{folder}/detailed_{code}.html"
                urls.append(url)
                found |= set(re.findall(r"detailed_(\d{4})\.html", ensure(url)))
        urls += [f"{folder}/detailed_{code}.html" for code in sorted(found) if code not in COUNTY_CODES_2009]
        ensure(PARTIES_2009)
        return [*urls, PARTIES_2009]
    ids = set(re.findall(r"(?:tulemus/|\./)(\d{9,10})\.html", top, re.IGNORECASE))
    munis: set[str] = set()
    for ident in sorted(ids):
        if ident.endswith("00000000"):
            url = f"{folder}/tulemus/{ident}.html"
            urls.append(url)
            munis |= set(re.findall(r'href="(\d{9,10})\.html"', ensure(url), re.IGNORECASE)) - {ident}
        else:
            munis.add(ident)
    for ident in sorted(munis):
        url = f"{folder}/tulemus/vtl_{ident}.html"
        try:
            ensure(url)
        except httpx.HTTPStatusError as failure:
            if failure.response.status_code != 404:
                raise
            continue
        subpages = re.findall(rf"vtl_{ident}-(\d+)\.html", ensure(url))
        if subpages:
            first = f"{folder}/tulemus/vtl_{ident}-1.html"
            ensure(first)
            urls.append(first)
            REPEATS.setdefault(spec.key, []).append(ident)
            continue
        urls.append(url)
    return urls


DISTRICT = re.compile(
    r"Valimisringkond nr (\d+)(?:\s*-\s*([^<]*))?(?=(?:\s|<[^>]*>)*H(?:&auml;|ä)(?:&auml;|ä)lte jaotus kandidaatide vahel)",
    re.IGNORECASE,
)
CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
TAGS = re.compile(r"<[^>]+>")


@dataclass
class Cell:
    text: str
    raw: str
    attrs: str

    @property
    def marked(self) -> bool:
        """Whether the page marks the candidate as elected: bold type, or in 2009 a style class."""
        return bool(re.search(r"<b>", self.raw, re.IGNORECASE) or re.search(r"\belected\b", self.attrs))


def plain_text(fragment: str) -> str:
    return clean(html.unescape(TAGS.sub(" ", fragment)).replace("\xa0", " "))


def table_rows(fragment: str) -> list[list[Cell]]:
    return [
        [Cell(plain_text(c.group(3)), c.group(3), c.group(2)) for c in CELL.finditer(row.group(1))]
        for row in ROW.finditer(fragment)
    ]


def count(value: str) -> int | None:
    value = value.strip()
    return int(value) if re.fullmatch(r"\d+", value) else None


def decimal(value: str) -> float | None:
    value = value.replace(" ", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def figures(cells_: list[Cell]) -> tuple[int | None, int | None]:
    """Voters on the roll and voters from a participation row, whichever layout the year's page uses.

    The pages of 1993 to 1999 print citizens and others separately and the ballots taken before the voters; 2002 prints
    the ballots taken and then the voters; 2005 and 2009 print only the roll and the voters."""
    if len(cells_) >= 12:
        return count(cells_[3].text), count(cells_[10].text)
    if len(cells_) >= 6:
        return count(cells_[1].text), count(cells_[4].text)
    return count(cells_[1].text), count(cells_[2].text)


def read_page(spec: Spec, code: str, name: str, county: str, text_: str) -> Muni:
    """One municipality's page of the archive: its divisions, its lists, and each district's candidates."""
    found = list(DISTRICT.finditer(text_))
    head = text_[: found[0].start()] if found else text_
    chunks: list[str] = []
    titles: list[str] = []
    for index, hit in enumerate(found):
        chunks += [hit.group(1), text_[hit.end() : found[index + 1].start() if index + 1 < len(found) else None]]
        titles.append(clean(hit.group(2) or ""))
    muni = Muni(code, name, county, [], coded=spec.key == "kov-2009")
    rows = table_rows(head)
    in_party = False
    declared: dict[str, dict[str, list[int]]] = {}
    homes: dict[str, str] = {}
    waiting: list[str] = []
    pending: dict[str, tuple[int | None, int | None]] = {}
    for cells_ in rows:
        label = cells_[0].text if cells_ else ""
        if not cells_ or len(cells_) < 2:
            continue
        if label == "Nimekiri":
            in_party = True
            continue
        if not in_party:
            if re.fullmatch(r"R\d+", label):
                for unit in waiting:
                    homes[unit] = label[1:]
                for unit, pair in pending.items():
                    muni.parts[(label[1:], unit)] = pair
                waiting, pending = [], {}
            elif re.fullmatch(r"J\d+", label):
                waiting.append(label)
            if re.fullmatch(r"J\d+|E", label):
                registered, voted = figures(cells_)
                pending[label] = (registered or None, voted)
            continue
        if len(cells_) < 5:
            continue
        # A list's row has a rank and the name in two cells, or the name alone over both; the rest is the votes, their
        # share, and the votes in each district, then the mandates, their share, and the mandates in each district.
        offset = 2 if len(cells_) % 2 == 0 else 1
        name_ = cells_[offset - 1].text
        numbers = [count(c.text) for c in cells_[offset:]]
        k = (len(numbers) - 4) // 2
        if k < 1:
            continue
        if name_ == "KOKKU":
            muni.valid = numbers[0]
            name_ = "Kokku"
        declared[name_] = {"votes": [v or 0 for v in numbers[2 : 2 + k]], "seats": [v or 0 for v in numbers[4 + k :]]}
    for unit in waiting:
        homes[unit] = "1"
    for unit, pair in pending.items():
        muni.parts[("1", unit)] = pair
    if not chunks:
        # The page prints the lists' votes by district but no candidates.
        count_ = max((len(line["votes"]) for line in declared.values()), default=0)
        for index in range(count_):
            number = str(index + 1)
            entries = {
                label: Entry(label, {"R": line["votes"][index]})
                for label, line in declared.items()
                if label != "Kokku" and index < len(line["votes"]) and line["votes"][index]
            }
            seats = sum(
                line["seats"][index]
                for label, line in declared.items()
                if label != "Kokku" and index < len(line["seats"])
            )
            district = District(number, seats, entries, divisions=[u for u, d in homes.items() if d == number])
            for label, line in declared.items():
                if index < len(line["votes"]):
                    district.declared_votes[label] = line["votes"][index]
                if index < len(line["seats"]):
                    district.declared_seats[label] = line["seats"][index]
            muni.districts.append(district)
    for position in range(0, len(chunks), 2):
        number, body = chunks[position], chunks[position + 1]
        entries: dict[str, Entry] = {}
        seats = re.search(r"Mandaate\s*=\s*(\d+)", plain_text(body))
        district = District(number, int(seats.group(1)) if seats else 0, entries, title=titles[position // 2])
        columns: list[str] = []
        current: Entry | None = None
        for cells_ in table_rows(body):
            if not cells_:
                continue
            first = cells_[0].text
            if first == "Nimekiri/Kandidaat":
                columns = [c.text for c in cells_[3:]]
                continue
            if not columns:
                continue
            if len(cells_) == 1:
                current = entries.setdefault(first, Entry(first, {}))
                continue
            if current is None:
                continue
            width = len(columns)
            if first in {"KOKKU", "Ringkond KOKKU"} or first.startswith("Ringkond"):
                votes = {
                    c: count(v.text) or 0
                    for c, v in zip(columns, cells_[2:], strict=False)
                    if re.fullmatch(r"J\d+|E", c)
                }
                if first == "KOKKU" and len(cells_) == width + 3 - 1:
                    current.total = votes
                continue
            if len(cells_) == width + 3:
                votes = {
                    c: count(v.text) or 0
                    for c, v in zip(columns, cells_[3:], strict=True)
                    if re.fullmatch(r"J\d+|E", c)
                }
                current.candidates.append(
                    Candidate(sum(len(e.candidates) for e in entries.values()) + 1, first, cells_[0].marked, votes)
                )
        full = {re.sub(r"\s*\([^()]*\)\s*$", "", label): label for label in declared}
        for found_name in [n for n in entries if n not in declared and n in full]:
            entry = entries.pop(found_name)
            entry.name = full[found_name]
            entries[entry.name] = entry
        for label, line in declared.items():
            index = int(number) - 1
            if index < len(line["votes"]):
                district.declared_votes[label] = line["votes"][index]
            if index < len(line["seats"]):
                district.declared_seats[label] = line["seats"][index]
        muni.districts.append(district)
    return muni


ANCHOR = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
RENAMED = {"Tartu": "Tartu linn", "TALLINNA LINN": "Tallinn", "TARTU LINN": "Tartu linn", "Tallinna linn": "Tallinn"}


def html_county(ident: str) -> str:
    """The county of a municipality from the number of its page, which starts with the county's."""
    number = int(ident[:2] if len(ident) == 10 else ident[:1])
    return f"{COUNTIES[number - 1]} maakond"


def html_names(spec: Spec) -> dict[str, tuple[str, str]]:
    """Each municipality's page key (its number, or from 2009 its EHAK code) to its name and county."""
    folder = f"{VVK}/{spec.html}"
    out: dict[str, tuple[str, str]] = {}
    if spec.key == "kov-2009":
        for code, city in CITIES_2009.items():
            out[code] = (city, "")
        for county, county_name in COUNTY_CODES_2009.items():
            for link, label in ANCHOR.findall(page(f"{folder}/detailed_{county}.html")):
                found = re.fullmatch(r"detailed_(\d{4})\.html", link)
                if found and found.group(1) not in COUNTY_CODES_2009 and found.group(1) not in CITIES_2009:
                    out[found.group(1)] = (plain_text(label), f"{county_name} maakond")
        return out
    for url in html_files(spec):
        if url.endswith("00000000.html"):
            for link, label in ANCHOR.findall(page(url)):
                found = re.fullmatch(r"(\d{9,10})\.html", link)
                if found and not found.group(1).endswith("00000000") and found.group(1) not in out:
                    name = plain_text(label)
                    out[found.group(1)] = (RENAMED.get(name, name), html_county(found.group(1)))
    top = page(index_url(spec))
    for link, label in ANCHOR.findall(top):
        found = re.search(r"(\d{9,10})\.html", link)
        if found and not found.group(1).endswith("00000000") and found.group(1) not in out:
            name = plain_text(label)
            out[found.group(1)] = (RENAMED.get(name, name), "")
    return out


def read_html(spec: Spec) -> dict[str, Muni]:
    out: dict[str, Muni] = {}
    folder = f"{VVK}/{spec.html}"
    for code, (name, county) in html_names(spec).items():
        url = f"{folder}/detailed_{code}.html" if spec.key == "kov-2009" else f"{folder}/tulemus/vtl_{code}.html"
        if url not in html_files(spec):
            url = f"{folder}/tulemus/vtl_{code}-1.html"
        if url not in html_files(spec):
            continue
        out[code] = read_page(spec, code, name, county, page(url))
    return out


def is_county_page(spec: Spec, url: str) -> bool:
    if spec.key == "kov-2009":
        found = re.search(r"detailed_(\d{4})\.html$", url)
        return found is not None and found.group(1) in COUNTY_CODES_2009
    return url.endswith("00000000.html")


def html_participation(spec: Spec) -> dict[str, tuple[int, int]]:
    """Each municipality's voters on the roll and voters, as the county pages total them."""
    out: dict[str, tuple[int, int]] = {}
    for url in html_files(spec):
        if not is_county_page(spec, url):
            continue
        for cells_ in table_rows(page(url)):
            if len(cells_) < 4 or not re.fullmatch(r"[\d ]+", cells_[1].text):
                continue
            registered, voted = figures(cells_)
            if registered is not None and voted is not None:
                out[cells_[0].text] = (registered, voted)
    return out


# Where an archive page's divisions add up to a different figure than the county page's row for the municipality, the
# two pages disagree with each other; the municipality's own page is followed.
KNOWN_DIFFERENCES: dict[tuple[str, str], str] = {
    (
        "kov-1999",
        "Hummuli vald",
    ): "the municipality's divisions add up to 798 voters on the roll and 510 voters; the county page prints 800 and 521",
}


def html_totals(spec: Spec, munis: dict[str, Muni]) -> None:
    """Fail unless the municipalities' voters on the roll and voters add up to the county pages' rows, and, for 2009,
    the valid votes and the parties' votes to the committee's page of party totals."""
    expected = html_participation(spec)
    wrong: list[str] = []
    for muni in munis.values():
        want = expected.get(muni.name)
        if want is None or not muni.parts or (spec.key, muni.name) in KNOWN_DIFFERENCES:
            continue
        have = (
            sum(r or 0 for r, _ in muni.parts.values()),
            sum(c or 0 for _, c in muni.parts.values()),
        )
        if have != want:
            wrong.append(f"{muni.name}: {have} against {want}")
    if wrong:
        raise ValueError(f"{spec.key}: participation differs from the county pages: {wrong[:8]}")
    if spec.key != "kov-2009":
        return
    valid, parties = parties_2009()
    names = party_names(spec)
    mine: dict[str, int] = {}
    total = 0
    for muni in munis.values():
        for district in muni.districts:
            for entry in district.entries.values():
                votes = sum(entry.total.values())
                total += votes
                if entry.name.casefold() in names:
                    mine[entry.name] = mine.get(entry.name, 0) + votes
    if total != valid:
        raise ValueError(f"{spec.key}: {total} valid votes, the committee prints {valid}")
    for name, votes in parties.items():
        if mine.get(name, 0) != votes:
            raise ValueError(f"{spec.key} {name}: {mine.get(name, 0)} votes, the committee prints {votes}")


def parties_2009() -> tuple[int, dict[str, int]]:
    """The valid votes and each party's votes the committee prints for the whole country in 2009."""
    words = plain_text(page(PARTIES_2009))
    total = re.search(r"Hääli kokku:\s*(\d+)", words)
    if total is None:
        raise LookupError("the 2009 party page has no total")
    body = words[words.index("ERAKOND HÄÄLI") + len("ERAKOND HÄÄLI") :]
    body = body.replace("% häältest", "")
    pairs = re.findall(r"([^\d%]+?)\s+(\d+)\s+[\d,]+%", body)
    return int(total.group(1)), {clean(name): int(votes) for name, votes in pairs}
