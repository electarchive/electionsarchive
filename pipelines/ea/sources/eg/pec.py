"""Egypt's presidential election of 2014, from the Presidential Election Commission.

The Commission's results page loads a governorate table and a workbook of every sub-committee (al-lajna al-far'iyya),
the unit that counted ballots, and the Commission's statement of results prints the national totals. The workbook
lists 14,040 sub-committees: 13,899 in Egypt grouped under 353 general committees, and 141 embassies and consulates
that ran the vote abroad. The Commission annulled 12 of them and publishes only their rolls.

The 2012 election's results page embeds an application that is no longer online, and the Commission's press releases
for that election print no results, so the 2012 election is not built.
"""

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry
import xlrd

from ea import package, raw
from ea.package import Package

SOURCE = "eg.pec"
EGYPT = str(uuid5(package.NAMESPACE, "polity\x1fEG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fEGY"))
HOME = "https://pres2014.elections.eg/"
STATEMENT = HOME + "images/pdfs/statement_of_results.pdf"
DETAILED = HOME + "results/data/excels/pres2014-results-detailed.xls"
NATIONAL = HOME + "results/data/csv/EG.csv"
LISTING = HOME + "results/compact-listing.html"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/EGY"
GB_GOVERNORATES = f"{GEOBOUNDARIES}/ADM1/geoBoundaries-EGY-ADM1.geojson"
FIRST, LAST = date(2014, 5, 15), date(2014, 5, 28)
VOTES, REGISTERED, BALLOTS, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
ABROAD = "الخارج"
TOTAL = "الإجمالي"

# The governorates by the name the Commission prints, with the English name and geoBoundaries' name for its shape.
GOVERNORATES = {
    "القاهرة": ("Cairo", "Cairo Governorate"),
    "الأسكندرية": ("Alexandria", "Alexandria Governorate"),
    "بورسعيد": ("Port Said", "Port Said Governorate"),
    "السويس": ("Suez", "Suez Governorate"),
    "دمياط": ("Damietta", "Damietta Governorate"),
    "الدقهلية": ("Dakahlia", "Dakahlia Governorate"),
    "الشرقية": ("Sharqia", "Al Sharqia Governorate"),
    "القليوبية": ("Qalyubia", "Qalyubia Governorate"),
    "كفر الشيخ": ("Kafr el-Sheikh", "Kafr el-Sheikh Governorate"),
    "الغربية": ("Gharbia", "Gharbiyya Governorate"),
    "المنوفية": ("Monufia", "Monufia Governorate"),
    "البحيرة": ("Beheira", "Beheira Governorate"),
    "الإسماعيلية": ("Ismailia", "Ismailia Governorate"),
    "الجيزة": ("Giza", "Giza Governorate"),
    "بني سويف": ("Beni Suef", "Beni Suef Governorate"),
    "الفيوم": ("Faiyum", "Faiyum Governorate"),
    "المنيا": ("Minya", "Minya Governate"),
    "أسيوط": ("Asyut", "Asyut Governorate"),
    "سوهاج": ("Sohag", "Sohag Governorate"),
    "قنا": ("Qena", "Qena Governorate"),
    "أسوان": ("Aswan", "Aswan Governorate"),
    "الأقصر": ("Luxor", "Luxor Governate"),
    "البحر الأحمر": ("Red Sea", "Red Sea Governorate"),
    "الوادي الجديد": ("New Valley", "New Valley Governorate"),
    "مرسى مطروح": ("Matrouh", "Matrouh Governorate"),
    "شمال سيناء": ("North Sinai", "North Sinai Governorate"),
    "جنوب سيناء": ("South Sinai", "South Sinai Governorate"),
}
CANDIDATES = (
    ("sisi", "Abdel Fattah el-Sisi", "عبد الفتاح السيسي", "عبد الفتاح السيسي"),
    ("sabahi", "Hamdeen Sabahi", "حمدين صباحي", "حمدين صباحى"),
)
# The statement of results, page 19 to 21.
DECLARED = {
    "registered": 53_909_306,
    "abroad": 318_033,
    "domestic": 25_260_190,
    "ballots": 25_578_223,
    "valid": 24_537_615,
    "invalid": 1_040_608,
    "sisi": 23_780_104,
    "sabahi": 757_511,
}
METHOD = (
    "Counted by the Presidential Election Commission per sub-committee and published in its workbook of detailed "
    "results. Registered voters are the electoral roll. Voters are the registered voters who voted; ballots cast "
    "add the staff who voted at their own committee, whom the Commission lists apart, and equal the valid and "
    "invalid ballots. Embassies and consulates abroad published no roll, so the national roll the Commission "
    "declares counts the 318,033 voters abroad as registered and no per-mission rolls are stored. The Commission "
    "annulled 12 sub-committees, 9 for admitting voters who had not registered to vote away from home, 1 for "
    "conflicting records, 1 for an unexplained gap of ninety between the voters and the ballots, and 1 on a "
    "candidate's objection; the workbook gives only their rolls, which are stored without results."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return key("area", "eg.governorate", code)


PEC = key("source", "pec")
GEO = key("source", "geoboundaries-adm1")
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president", 2014)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2014"]


def fetch() -> None:
    raw.fetch(SOURCE, STATEMENT, suffix=".pdf")
    raw.fetch(SOURCE, DETAILED, suffix=".xls")
    raw.fetch(SOURCE, NATIONAL, suffix=".csv")
    raw.fetch(SOURCE, LISTING, suffix=".html")
    raw.fetch(SOURCE, GB_GOVERNORATES, suffix=".geojson")


def parse() -> None:
    for build in (geo(), registry(), election()):
        build.write()


def emit() -> None:
    package.run("load", packages())


def codes() -> dict[str, str]:
    """The Commission's governorate codes by name, from the options of its governorate selector."""
    text = raw.cached(SOURCE, LISTING).path.read_text(encoding="utf-8")
    block = text.split('id="cities"', 1)[1].split("</select>", 1)[0]
    found = {name.strip(): code for code, name in re.findall(r"<option value='(\d+)'>([^<]+)</option>", block)}
    found.pop("", None)
    missing = [name for name in [*GOVERNORATES, ABROAD] if name not in found]
    if missing or len(found) != len(GOVERNORATES) + 1:
        raise ValueError(f"the governorate selector does not match the table: {missing}")
    return found


def _shapes() -> dict[str, shapely.Geometry]:
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_GOVERNORATES).path.read_bytes())["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        parts = [piece for part in parts for piece in shapely.get_parts(part)]
        found[feature["properties"]["shapeName"]] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Egypt's 27 governorates, from OpenStreetMap through geoBoundaries",
    )
    p.external |= {EGYPT, COUNTRY}
    p.add(
        "source",
        id=GEO,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
        title="geoBoundaries gbOpen: Egypt ADM1 (governorates)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes="Boundary year 2017, built December 2023. The twenty-seven governorates of 2014 are the same as these.",
    )
    artifact = raw.cached(SOURCE, GB_GOVERNORATES)
    p.artifact(artifact, GEO)
    for scheme, rank, kind, noun in (
        ("eg.governorate", 1, "administrative", "governorate"),
        ("eg.general_committee", 2, "precinct", "general committee"),
        ("eg.subcommittee", 3, "precinct", "sub-committee"),
    ):
        p.add("scheme", code=scheme, polity_id=EGYPT, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add(
        "boundary_set",
        id=key("boundary_set", "eg.governorate", 2017),
        scheme_code="eg.governorate",
        vintage="2017",
        valid_from=date(2017, 1, 1),
        as_of=date(2023, 12, 12),
    )
    shapes = _shapes()
    ids = codes()
    for arabic, (english, shape_name) in GOVERNORATES.items():
        id = area(ids[arabic])
        shape = shapes[shape_name]
        p.add("area", id=id, scheme_code="eg.governorate")
        p.add("area_code", area_id=id, code=ids[arabic])
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, english, "en", "official")
        p.name(id, arabic, "ar", "native", False)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=date(2017, 1, 1),
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GEO,
        )
    return p


def _source(p: Package) -> None:
    p.add(
        "source",
        id=PEC,
        kind="official_body",
        publisher="Presidential Election Commission of Egypt",
        title="Results of the 2014 presidential election",
        url=HOME + "presidential-elections-2014-results",
        license="No license stated; published by the electoral authority for public use",
        attribution="Presidential Election Commission of Egypt",
    )


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "Egypt's presidency and the electoral system of its direct election")
    p.external.add(EGYPT)
    p.add("office", id=PRESIDENCY, polity_id=EGYPT, kind="head_of_state")
    p.name(PRESIDENCY, "President of Egypt", "en", "official")
    p.name(PRESIDENCY, "رئيس جمهورية مصر العربية", "ar", "official", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=EGYPT,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The President is elected directly. A candidate with more than half the valid votes wins in the "
        "first round; otherwise the two leading candidates face a runoff.",
        lang="en",
    )
    return p


@dataclass(frozen=True)
class Sub:
    governorate: str
    general: str
    sub: str
    registered: int | None
    staff: int | None
    voters: int | None
    ballots: int | None
    invalid: int | None
    valid: int | None
    sisi: int | None
    sabahi: int | None


def table() -> tuple[list[Sub], Sub]:
    """The workbook's sub-committees and its total row, which is checked against them and the statement."""
    sheet = xlrd.open_workbook(raw.cached(SOURCE, DETAILED).path).sheet_by_index(0)
    header = sheet.row_values(0)
    expected = ["المحافظة", "اللجنة العامة", "اللجنة الفرعية", "عدد المسجلين", "العاملون باللجان", "حضور المسجلين"]
    if header[:6] != expected:
        raise ValueError(f"the workbook's columns changed: {header}")
    rows: list[Sub] = []
    total: Sub | None = None
    for index in range(1, sheet.nrows):
        v = sheet.row_values(index)
        numbers = [None if value == "" else int(value) for value in v[3:11]]
        row = Sub(str(v[0]), str(v[1]), str(v[2]).strip(), *numbers)
        if row.governorate == TOTAL:
            total = row
        else:
            rows.append(row)
    if total is None:
        raise ValueError("the workbook has no total row")
    done = [row for row in rows if row.ballots is not None]
    for name in ("staff", "voters", "ballots", "invalid", "valid", "sisi", "sabahi"):
        got = sum(getattr(row, name) for row in done)
        if got != getattr(total, name):
            raise ValueError(f"{name}: sub-committees add to {got}, the workbook's total is {getattr(total, name)}")
    for row in done:
        if row.ballots != (row.valid or 0) + (row.invalid or 0) or row.valid != (row.sisi or 0) + (row.sabahi or 0):
            raise ValueError(f"{row}: the row does not add up")
    for name in ("registered", "ballots", "valid", "invalid", "sisi", "sabahi"):
        if getattr(total, name) != DECLARED[name]:
            raise ValueError(f"{name}: the workbook total {getattr(total, name)} differs from the statement")
    return rows, total


def _check_governorates(rows: list[Sub]) -> None:
    """Each governorate's sub-committees must add to the Commission's governorate table."""
    csv_file = raw.cached(SOURCE, NATIONAL).path.read_text(encoding="utf-8")
    published = {line["المحافظة"]: line for line in csv.DictReader(io.StringIO(csv_file))}
    columns = {
        "voters": "حضور المسجلين",
        "ballots": "إجمالي الحضور",
        "invalid": "الأصوات الباطلة",
        "valid": "الأصوات الصحيحة",
        "sisi": "عبد الفتاح السيسي",
        "sabahi": "حمدين صباحى",
    }
    for name, line in published.items():
        members = [row for row in rows if row.governorate == name]
        if not members:
            raise ValueError(f"{name} is in the governorate table but not the workbook")
        for field, column in columns.items():
            got = sum(getattr(row, field) or 0 for row in members)
            if got != int(line[column]):
                raise ValueError(f"{name} {field}: sub-committees add to {got}, table has {line[column]}")


def election() -> Package:
    p = Package(
        f"{SOURCE}/2014",
        SOURCE,
        "The 2014 Egyptian presidential election by sub-committee, from the Presidential Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {EGYPT, COUNTRY}
    _source(p)
    p.accept(
        "accounting.declared",
        "The declared roll of 53,909,306 counts the 318,033 voters abroad, for whom the Commission publishes no "
        "per-mission roll, so the sub-committees' rolls add to 53,591,273, the difference being exactly those voters.",
    )
    for url in (STATEMENT, DETAILED, NATIONAL, LISTING):
        p.artifact(raw.cached(SOURCE, url), PEC)
    rows, total = table()
    _check_governorates(rows)
    ids = codes()
    id = key("election", 2014)
    p.add("election", id=id, polity_id=EGYPT, kind="general", date_from=FIRST, date_to=LAST, status="held")
    path = "eg/2014-05-26-presidential"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, "2014 Egyptian presidential election", "en", "official")
    p.name(id, "انتخابات رئاسة الجمهورية 2014", "ar", "native", False)
    contest = key("contest", 2014)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "2014 Egyptian presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", 2014)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=PEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for position, (slug, english, arabic, column) in enumerate(CANDIDATES, start=1):
        person = key("person", slug)
        p.add("person", id=person, gender="male")
        p.name(person, english, "en", "official")
        p.name(person, arabic, "ar", "native", False)
        option = options[slug] = key("option", slug)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=english,
        )
        p.add(
            "candidacy",
            id=key("candidacy", slug),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=english,
            is_independent=True,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if slug == "sisi" else "lost")
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=DECLARED[slug])
    for measure, value in (
        (REGISTERED, DECLARED["registered"]),
        (BALLOTS, DECLARED["ballots"]),
        (VALID, DECLARED["valid"]),
        (INVALID, DECLARED["invalid"]),
        (VOTERS, total.voters),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    generals: dict[tuple[str, str], str] = {}
    values: list[dict[str, object]] = []

    def put(unit: str, option: str | None, measure: int, value: object) -> None:
        if value is not None:
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": 0,
                    "value": value,
                }
            )

    for row in rows:
        governorate, general, sub = row.governorate, row.general, row.sub
        abroad = governorate == ABROAD
        parent = generals.get((governorate, general))
        if parent is None:
            parent = generals[(governorate, general)] = key("unit", "general", governorate, general)
            p.add(
                "reporting_unit",
                id=parent,
                polity_id=EGYPT,
                scheme_code="eg.general_committee",
                code=f"{ids[governorate]}:{general}",
                area_relation="unlocated" if abroad else "within",
                area_id=None if abroad else area(ids[governorate]),
                kind="aggregate",
            )
            p.name(parent, general if abroad else f"{governorate} - {general}", "ar", "official")
        unit = key("unit", "sub", governorate, general, sub)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=EGYPT,
            scheme_code="eg.subcommittee",
            code=f"{ids[governorate]}:{general}:{sub}",
            parent_id=parent,
            area_relation="unlocated" if abroad else "within",
            area_id=None if abroad else area(ids[governorate]),
            kind="abroad_mission" if abroad else "standard",
        )
        p.name(unit, sub if abroad else f"{governorate} - {general} - {sub}", "ar", "official")
        if not abroad:
            put(unit, None, REGISTERED, row.registered)
        put(unit, None, VOTERS, row.voters)
        put(unit, None, BALLOTS, row.ballots)
        put(unit, None, VALID, row.valid)
        put(unit, None, INVALID, row.invalid)
        put(unit, options["sisi"], VOTES, row.sisi)
        put(unit, options["sabahi"], VOTES, row.sabahi)
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))
    return p
