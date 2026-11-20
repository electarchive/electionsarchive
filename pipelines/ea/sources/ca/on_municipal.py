"""Ontario municipal general elections: Toronto by voting subdivision, 2003 to 2026.

The City of Toronto publishes, for each general election, one workbook per office with a sheet for every ward: a row
per candidate, a column per voting subdivision, the candidate's total, and (from 2006) a row with the ward's valid
votes. The mayor is elected citywide and each councillor by plurality in a ward, so each office and ward is a contest
with one result set whose values sit at the voting subdivisions. The city's files do not name the winners. For 2014,
2018, and 2022 they come from the Ministry of Municipal Affairs and Housing's list of successful candidates, and a
winner is recorded only where that list names the candidate and gives the votes the city's files give. The earlier
elections and 2026 carry no outcomes.

Ward boundaries come from the City of Toronto: the 44-ward model (May 2010 release) for 2003 to 2014 and the 25-ward
model (2018) for 2018 to 2026. School board contests are not loaded.
"""

import functools
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import polars as pl
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ca.on_municipal"
WARDS = "ca.on_municipal.wards"
CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/96d35404-44d9-49d8-95bb-fb1e5489240d/resource/"
CKAN_WARDS = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/5e7a8234-f805-43ac-820f-03d7c360b588/resource/"
ONTARIO = "https://data.ontario.ca/dataset/a5871234-ed52-4c79-af33-b205aee59fd3/resource/"
RESULTS_PAGE = "https://open.toronto.ca/dataset/election-results-official/"
WARDS_PAGE = "https://open.toronto.ca/dataset/city-wards/"
ONTARIO_PAGE = "https://data.ontario.ca/dataset/municipal-election-results"
RESULTS = {
    2003: CKAN + "1ed53c9d-a316-465e-96ce-e72be74f8aa9/download/2003-results.zip",
    2006: CKAN + "3fb1227c-a279-4523-a1aa-f00190ba717f/download/2006-results.zip",
    2010: CKAN + "6fbfaab0-bb84-442a-8e4b-1c14d4c10d6d/download/2010-results.zip",
    2014: CKAN + "d59b5a52-33c3-4380-a136-82707e4d9aae/download/2014-results.zip",
    2018: CKAN + "2fcd5f20-90f5-4dd0-88eb-22e978b9bf89/download/2018-results.zip",
    2022: CKAN + "3ad371de-7c51-45d3-9ea4-0b4efac5fc2b/download/2022-results.zip",
    2026: CKAN + "8006f3bd-0a48-4e20-8cc4-4155a30cd203/download/2026-results.zip",
}
WARDS_44 = CKAN_WARDS + (
    "d96d198c-fb5b-4229-a586-7673c45e80e7/download/44-ward-model-may-2010-wgs84-latitude-longitude.zip"
)
WARDS_25 = CKAN_WARDS + (
    "35f67d86-cfc8-4483-8d77-50d035b010d9/download/25-ward-model-december-2018-wgs84-latitude-longitude.zip"
)
WINNERS = {
    2014: ONTARIO + "562a2b81-b4b0-41ee-97dc-33f10c400f61/download/2014_municipal_election_-_candidate_data.csv",
    2018: ONTARIO
    + "80304bc4-1ab9-443e-876a-9f79dcbd2c98/download/2018_municipal_election_-_candidate_data_-_csv_for_release_-_2020-05-12.csv",
    2022: ONTARIO
    + "7f408f5e-71c7-43fa-82bf-5eb8be77b9a7/download/2022_municipal_election_-_successful_candidate_data_as_of_voting_day.csv",
}
DAYS = {
    2003: date(2003, 11, 10),
    2006: date(2006, 11, 13),
    2010: date(2010, 10, 25),
    2014: date(2014, 10, 27),
    2018: date(2018, 10, 22),
    2022: date(2022, 10, 24),
    2026: date(2026, 10, 26),
}
# The 25-ward model first governed the 2018 election; the 44-ward model governed the five before it.
NEW_WARDS = 2018
# Production holds Canada, Ontario, and its province area under the ids their first pipeline gave them.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
ONTARIO_POLITY = "01a13fb6-eab7-7501-a0f9-490459fabb2a"
ONTARIO_AREA = "01a13fb6-eab7-7501-a0f9-48f733d47ba4"
SYSTEM_NOTE = (
    "Toronto elects its mayor by a citywide vote and one councillor in each ward. Every elector marks one candidate "
    "for each office, and the candidate with the most votes wins."
)
SIMPLIFY = 0.00005


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CITY_SOURCE = key("source", "toronto")
WARD_SOURCE = key("source", "toronto_wards")
MMAH = key("source", "mmah")
SYSTEM = key("electoral_system", "plurality")
TORONTO = key("polity", "toronto")
CITY = key("area", "toronto")
BODY = key("body", "toronto")
MAYOR = key("office", "toronto")


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def models(year: int) -> str:
    return "25" if year >= NEW_WARDS else "44"


def ward_area(model: str, number: int) -> str:
    return key("area", "ward" + model, number)


def boundary_set(model: str) -> str:
    return key("boundary_set", "ward" + model)


def fetch() -> None:
    for url in RESULTS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    for url in WINNERS.values():
        raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(WARDS, WARDS_44, suffix=".zip")
    raw.fetch(WARDS, WARDS_25, suffix=".zip")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


class Ward(NamedTuple):
    number: int
    subs: list[str]
    candidates: list[tuple[str, dict[str, int], int | None]]
    valid: dict[str, int]


def _int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, float):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(",", "")
    return int(float(text)) if re.fullmatch(r"-?\d+(\.0+)?", text) else None


def _sub(value: object) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    text = str(value).strip()
    return str(int(text)) if re.fullmatch(r"\d+(\.0+)?", text) else text


def _label(text: str) -> str:
    text = " ".join(text.split())
    return text.title() if text.isupper() and len(text) > 1 else text


def _sheet(rows: list[tuple[object, ...]], number: int) -> Ward | None:
    start = next((i for i, row in enumerate(rows) if str(row[0]).strip() in ("Subdivision", "Name")), None)
    if start is None:
        return None
    head = rows[start]
    total = next((i for i, cell in enumerate(head) if str(cell).strip() == "Total"), None)
    columns = [(i, _sub(cell)) for i, cell in enumerate(head) if i > 0 and cell is not None and i != total]
    candidates: list[tuple[str, dict[str, int], int | None]] = []
    valid: dict[str, int] = {}
    for row in rows[start + 1 :]:
        if row[0] is None or not str(row[0]).strip():
            continue
        text = str(row[0]).strip()
        if text.lower() in ("mayor", "councillor"):
            continue
        votes = {sub: v for i, sub in columns if (v := _int(row[i])) is not None}
        if re.search(r"\btotals?$", text, re.IGNORECASE):
            valid = votes
        elif votes:
            candidates.append((_label(text), votes, None if total is None else _int(row[total])))
    return Ward(number, [sub for _, sub in columns], candidates, valid)


@functools.cache
def results(year: int) -> dict[str, list[Ward]]:
    """The mayor's and the councillors' sheets of one election, by office, each ward once."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, RESULTS[year]).path)
    found: dict[str, list[Ward]] = {}
    for member in archive.namelist():
        base = member.rsplit("/", 1)[-1]
        match = re.search(r"(?i)(?:^|[_ ])(mayor|councillor)\.xlsx?$", base)
        if not match:
            continue
        office = match.group(1).lower()
        book = fastexcel.read_excel(archive.read(member))
        wards: list[Ward] = []
        for name in book.sheet_names:
            number = re.search(r"(\d+)\s*$", name)
            if number is None:
                continue
            frame = book.load_sheet_by_name(name, header_row=None).to_polars()
            ward = _sheet(frame.rows(), int(number.group(1)))
            if ward is not None:
                wards.append(ward)
        found[office] = sorted(wards, key=lambda ward: ward.number)
    if set(found) != {"mayor", "councillor"}:
        raise ValueError(f"{year}: missing office files {sorted(found)}")
    return found


@functools.cache
def _shapes(model: str) -> dict[int, tuple[str, shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached(WARDS, WARDS_25 if model == "25" else WARDS_44).path)
    shp = next(name for name in archive.namelist() if name.lower().endswith(".shp"))
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(shp)), dbf=io.BytesIO(archive.read(shp[:-4] + ".dbf")), encoding="utf8"
    )
    found: dict[int, tuple[str, shapely.Geometry]] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            continue
        if model == "25":
            number, name = int(str(record["AREA_S_CD"])), str(record["AREA_NAME"])
        else:
            number, name = int(str(record["SCODE_NAME"])), re.sub(r"\s*\(\d+\)$", "", str(record["NAME"]))
        geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
        geometry = shapely.simplify(geometry, SIMPLIFY, preserve_topology=True)
        parts = [part for part in shapely.get_parts(geometry) if shapely.get_type_id(part) in (3, 6)]
        found[number] = (name.strip(), shapely.set_srid(shapely.MultiPolygon(parts), 4326))
    return found


def ward_names(model: str) -> dict[int, str]:
    return {number: name for number, (name, _) in _shapes(model).items()}


def _wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The City of Toronto as a polity and an area, with its 44-ward and 25-ward models and the outlines the city "
        "publishes for them",
    )
    p.external |= {CANADA, ONTARIO_POLITY, ONTARIO_AREA}
    p.add(
        "source",
        id=WARD_SOURCE,
        kind="official_body",
        publisher="City of Toronto",
        title="City Wards",
        url=WARDS_PAGE,
        license="Open Government Licence – Toronto",
        license_url="https://open.toronto.ca/open-data-license/",
        attribution="Contains information licensed under the Open Government Licence – Toronto",
    )
    for url in (WARDS_44, WARDS_25):
        p.artifact(raw.cached(WARDS, url), WARD_SOURCE)
    p.add(
        "scheme",
        code="ca-on.municipality",
        polity_id=ONTARIO_POLITY,
        kind="administrative",
        rank=3,
        noun="municipality",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-on.toronto_ward",
        polity_id=ONTARIO_POLITY,
        kind="electoral_district",
        rank=4,
        noun="ward",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-on.toronto_subdivision",
        polity_id=ONTARIO_POLITY,
        kind="precinct",
        rank=5,
        noun="voting subdivision",
        code_system=None,
    )
    p.add("area", id=CITY, scheme_code="ca-on.municipality")
    p.add("area_parent", area_id=CITY, parent_id=ONTARIO_AREA)
    p.name(CITY, "Toronto", "en", "official")
    p.add("polity", id=TORONTO, kind="municipality", iso_code=None)
    p.name(TORONTO, "Toronto", "en", "official")
    p.add("polity_parent", polity_id=TORONTO, parent_id=ONTARIO_POLITY)
    p.add("polity_territory", polity_id=TORONTO, area_id=CITY)
    p.add("slug", path="ca/on/toronto", entity_id=TORONTO, is_current=True)
    spans = {"44": (None, DAYS[NEW_WARDS] + timedelta(days=39)), "25": (DAYS[NEW_WARDS] + timedelta(days=39), None)}
    for model, (since, until) in spans.items():
        retrieved = raw.cached(WARDS, WARDS_25 if model == "25" else WARDS_44).retrieved_at
        p.add(
            "boundary_set",
            id=boundary_set(model),
            scheme_code="ca-on.toronto_ward",
            vintage=f"{model}-ward model",
            valid_from=since,
            valid_to=until,
            as_of=since or date(2010, 5, 1),
        )
        for number, (name, shape) in sorted(_shapes(model).items()):
            place = ward_area(model, number)
            p.add("area", id=place, scheme_code="ca-on.toronto_ward")
            p.add("area_code", area_id=place, code=f"{number:02d}", valid_from=since, valid_to=until)
            p.add("area_parent", area_id=place, parent_id=CITY, valid_from=since, valid_to=until)
            p.name(place, f"Ward {number} {name}", "en", "official")
            p.add(
                "area_geometry",
                area_id=place,
                valid_from=since,
                valid_to=until,
                geom=_wkb(shape),
                label_point=_wkb(shapely.point_on_surface(shape)),
                quality="official",
                source_id=WARD_SOURCE,
            )
        del retrieved
    outline = shapely.union_all([shape for _, shape in _shapes("25").values()])
    parts = [part for part in shapely.get_parts(outline) if shapely.get_type_id(part) in (3, 6)]
    outline = shapely.MultiPolygon(parts)
    p.add(
        "area_geometry",
        area_id=CITY,
        valid_from=None,
        valid_to=None,
        geom=_wkb(outline),
        label_point=_wkb(shapely.point_on_surface(outline)),
        quality="official",
        source_id=WARD_SOURCE,
    )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The city council, the mayoralty, and the electoral system of Toronto's municipal elections, with the sources "
        "of its results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, ONTARIO_POLITY}
    p.add(
        "source",
        id=CITY_SOURCE,
        kind="official_body",
        publisher="City of Toronto, City Clerk's Office",
        title="Election Results - Official",
        url=RESULTS_PAGE,
        license="Open Government Licence – Toronto",
        license_url="https://open.toronto.ca/open-data-license/",
        attribution="Contains information licensed under the Open Government Licence – Toronto",
    )
    p.add(
        "source",
        id=MMAH,
        kind="official_body",
        publisher="Ministry of Municipal Affairs and Housing",
        title="Municipal election results",
        url=ONTARIO_PAGE,
        license="Open Government Licence – Ontario",
        license_url="https://www.ontario.ca/page/open-government-licence-ontario",
        attribution="Contains information licensed under the Open Government Licence – Ontario",
    )
    for url in RESULTS.values():
        p.artifact(raw.cached(SOURCE, url), CITY_SOURCE)
    for url in WINNERS.values():
        p.artifact(raw.cached(SOURCE, url), MMAH)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ONTARIO_POLITY,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(SYSTEM, "Ontario municipal plurality voting", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    p.add("body", id=BODY, polity_id=TORONTO, kind="council")
    p.name(BODY, "Toronto City Council", "en", "official")
    p.add("office", id=MAYOR, polity_id=TORONTO, kind="mayor")
    p.name(MAYOR, "Mayor of Toronto", "en", "official")
    return p


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(sorted(re.findall(r"[a-z0-9]+", fold(text))))


@functools.cache
def winners(year: int) -> dict[tuple[str, ...], list[tuple[str, int | None]]]:
    """Toronto's successful candidates in the province's list: name tokens to office and votes."""
    if year not in WINNERS:
        return {}
    frame = pl.read_csv(raw.cached(SOURCE, WINNERS[year]).path, infer_schema_length=0, encoding="utf8-lossy")
    names = {fold(column): column for column in frame.columns}

    def column(*parts: str) -> str:
        return next(name for text, name in names.items() if all(part in text for part in parts))

    municipality, last, first = column("municipality"), column("last"), column("first")
    office, votes = (
        column("office"),
        next(name for text, name in names.items() if "votes" in text and "office" not in text),
    )
    found: dict[tuple[str, ...], list[tuple[str, int | None]]] = defaultdict(list)
    for row in frame.filter(pl.col(municipality).str.starts_with("Toronto")).iter_rows(named=True):
        kind = "mayor" if "mayor" in fold(str(row[office])) else "councillor"
        found[_tokens(f"{row[last]} {row[first]}")].append((kind, _int(row[votes])))
    return dict(found)


def _declared(year: int) -> list[tuple[tuple[str, ...], tuple[str, int | None]]]:
    return [(token, entry) for token, entries in winners(year).items() for entry in entries]


def _named(text: str, token: tuple[str, ...], entry: tuple[str, int | None], votes: int, office: str) -> bool:
    """Whether the province's list names this candidate: the same office and votes, and the same name up to a typo."""
    if entry[0] != office or entry[1] not in (None, votes):
        return False
    return token == _tokens(text) or SequenceMatcher(None, " ".join(token), " ".join(_tokens(text))).ratio() >= 0.8


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Toronto municipal election: the mayor and the councillor of every ward, by voting subdivision",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, ONTARIO_POLITY}
    p.uses(raw.cached(SOURCE, RESULTS[year]))
    if year in WINNERS:
        p.uses(raw.cached(SOURCE, WINNERS[year]))
    close = day + timedelta(days=1)
    model = models(year)
    names = ward_names(model)
    found = results(year)
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=ONTARIO_POLITY, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} Ontario municipal elections", "en", "official")
    p.add("slug", path=f"ca/on/{day}-local", entity_id=umbrella, is_current=True)
    city = key("election", year, "toronto")
    p.add(
        "election",
        id=city,
        polity_id=TORONTO,
        parent_id=umbrella,
        kind="local",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(city, f"{year} Toronto municipal election", "en", "official")
    p.add("slug", path=f"ca/on/toronto/{day}-local", entity_id=city, is_current=True)
    units: dict[tuple[int, str], str] = {}
    for office in found.values():
        for ward in office:
            for sub in ward.subs:
                if (ward.number, sub) in units:
                    continue
                unit = units[(ward.number, sub)] = key("unit", day, ward.number, sub)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=CANADA,
                    scheme_code="ca-on.toronto_subdivision",
                    code=f"{day}:{ward.number:02d}:{sub}",
                    area_relation="within",
                    area_id=ward_area(model, ward.number),
                    kind="standard",
                    valid_from=day,
                    valid_to=close,
                )
    contests = [("mayor", found["mayor"])] + [("councillor", [ward]) for ward in found["councillor"]]
    for office, sheets in contests:
        mayoral = office == "mayor"
        ward = sheets[0]
        tag = office if mayoral else f"{office}:{ward.number}"
        contest = key("contest", year, tag)
        place = CITY if mayoral else ward_area(model, ward.number)
        p.add(
            "contest",
            id=contest,
            election_id=city,
            kind="office" if mayoral else "body_seats",
            office_id=MAYOR if mayoral else None,
            body_id=None if mayoral else BODY,
            district_area_id=place,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
            boundary_set_id=None if mayoral else boundary_set(model),
        )
        if mayoral:
            label, path = "Mayor", "mayor"
        else:
            label = f"Ward {ward.number} {names[ward.number]}: Councillor"
            path = f"councillor-ward-{ward.number:02d}"
        p.name(contest, f"Toronto: {label}", "en", "official")
        p.add("slug", path=f"ca/on/toronto/{day}-local/{path}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=city,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CITY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        options: dict[str, str] = {}
        totals: dict[str, int] = defaultdict(int)
        for sheet in sheets:
            for label_text, votes, declared in sheet.candidates:
                if label_text not in options:
                    position = len(options) + 1
                    seq = f"{tag}:{position}"
                    person = key("person", year, seq)
                    p.add("person", id=person, birth_precision=None)
                    p.name(person, label_text, "en", "official")
                    option = options[label_text] = key("option", year, seq)
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None,
                        ballot_position=position,
                        ballot_label=label_text,
                    )
                    p.add(
                        "candidacy",
                        id=key("candidacy", year, seq),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label_text,
                    )
                option = options[label_text]
                for sub, value in votes.items():
                    totals[label_text] += value
                    _value(p, result_set, units[(sheet.number, sub)], option, 1, value)
                if not mayoral and declared is not None:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=declared)
            for sub, value in sheet.valid.items():
                _value(p, result_set, units[(sheet.number, sub)], None, 4, value)
        leader = max(totals.items(), key=lambda item: item[1], default=None)
        if leader is None:
            continue
        for label_text, option in options.items():
            if totals[label_text] != leader[1]:
                continue
            if any(_named(label_text, token, entry, totals[label_text], office) for token, entry in _declared(year)):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
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
