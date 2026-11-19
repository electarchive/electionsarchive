"""Election Commission of India: general elections to the State and Union territory legislative assemblies, by
assembly constituency, from table 10 ("Detailed Results") of the Commission's statistical report on each election.

The Commission's site refuses scripted requests, so each workbook reaches the raw cache as a copy a person saved from
it: `fetch()` reads `data/inbox/in.eci/index.json` (the Commission's URL of each file, its SHA-256 hash, and when it was
retrieved) and stores each file under that URL, refusing any file whose hash differs from the one recorded here.

The states and Union territories are `federal_unit` polities keyed by their ISO 3166-2 codes, drawn from
geoBoundaries' open ADM1 layer (CC BY 4.0). Assembly constituencies are areas of the `in.ac` scheme, keyed by state
and constituency number under the delimitation in force; they carry no shapes yet.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "in.eci"
SHAPES = "in.geoboundaries"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIND"))
INBOX = DATA / "inbox" / SOURCE
HOME = "https://www.eci.gov.in/statistical-reports/"
FILES = "https://www.eci.gov.in/eci-backend/public/all_files/"
REPORTS = "https://www.eci.gov.in/eci-backend/public//all_files/election_report/"
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/IND/ADM1/"
    "geoBoundaries-IND-ADM1_simplified.geojson"
)
STATE_SCHEME = "in.state"
AC_SCHEME = "in.ac"
VOTES, REGISTERED = 1, 2
ELECTION_DAY, POSTAL = 1, 3
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECI = key("source", "statistical-reports")
SHAPE_SOURCE = key("source", "geoboundaries")
FPTP = key("electoral_system", "fptp")


@dataclass(frozen=True)
class State:
    """A State or Union territory: its ISO 3166-2 code, English name, whether it is a Union territory, the code
    geoBoundaries gives it where that differs, and whether its legislature has a second chamber."""

    iso: str
    name: str
    territory: bool = False
    shape_iso: str | None = None
    council: bool = False


STATES = {
    s.iso: s
    for s in (
        State("AP", "Andhra Pradesh", council=True),
        State("AR", "Arunachal Pradesh"),
        State("AS", "Assam"),
        State("BR", "Bihar", council=True),
        State("CG", "Chhattisgarh", shape_iso="CT"),
        State("DL", "Delhi", territory=True),
        State("HR", "Haryana"),
        State("JH", "Jharkhand"),
        State("JK", "Jammu and Kashmir", territory=True),
        State("KL", "Kerala"),
        State("MH", "Maharashtra", council=True),
        State("MP", "Madhya Pradesh"),
        State("MZ", "Mizoram"),
        State("OD", "Odisha", shape_iso="OR"),
        State("PY", "Puducherry", territory=True),
        State("RJ", "Rajasthan"),
        State("SK", "Sikkim"),
        State("TN", "Tamil Nadu"),
        State("WB", "West Bengal"),
    )
}
# The delimitation each state's constituencies follow, and the day it took effect: the Delimitation Commission's
# 2008 orders, Jammu and Kashmir's order of 5 May 2022, and Assam's of 11 August 2023.
DELIMITATION = {"AS": date(2023, 8, 11), "JK": date(2022, 5, 5)}
DELIMITATION_2008 = date(2008, 2, 19)


@dataclass(frozen=True)
class Report:
    """One general election's table 10 workbook: the state, the poll days, the assembly's number of seats, the URL,
    the SHA-256 hash of the file the Commission served, and notes for constituencies polled on another day."""

    state: str
    days: tuple[date, ...]
    seats: int
    url: str
    sha256: str
    late: dict[int, date] = field(default_factory=dict)
    unopposed: int = 0


ELECTIONS = [
    Report(
        "RJ",
        (date(2023, 11, 25),),
        200,
        FILES + "full-statistical-reports/rajasthan/2023_Including/Detailed_Results.xlsx",
        "bf179205dd430f471d41d8d37564e7b2106cdae124aed6ed06645f74b06483a4",
        late={3: date(2024, 1, 5)},
    ),
    Report(
        "MP",
        (date(2023, 11, 17),),
        230,
        FILES + "full-statistical-reports/mp/2023/Detailed_Results.xlsx",
        "cd396988e21825b75b3fef068005f5a60919b89df824758550d33c83b7a36f1e",
    ),
    Report(
        "CG",
        (date(2023, 11, 7), date(2023, 11, 17)),
        90,
        FILES + "full-statistical-reports/chhattisgarh/2023/Detailed_Results.xlsx",
        "0a5e8276d4dad3fa13c7f2a5d9a0354de065122009eb532e69a21f9ee6481746",
    ),
    Report(
        "MZ",
        (date(2023, 11, 7),),
        40,
        FILES + "full-statistical-reports/mizoram/2023/Detailed_Results.xlsx",
        "40ec038e6d69a5f2401c01260b1930b73860e41f940ae175866245f1946a04a6",
    ),
    Report(
        "AR",
        (date(2024, 4, 19),),
        60,
        FILES + "AE-2024-statistical-report-AR/10-Detailed-Results.xlsx",
        "244d45921c45f6fc740b5ed7b0d51db7e961db601c00e66d16d7febbcb8018cd",
        unopposed=10,
    ),
    Report(
        "SK",
        (date(2024, 4, 19),),
        32,
        FILES + "AE-2024-statistical-report-SK/10-Detailed-Results.xlsx",
        "f29850ae8f6045289cc764c53823797e6a435cdc7717610cc0f80671e61ac235",
    ),
    Report(
        "AP",
        (date(2024, 5, 13),),
        175,
        FILES + "AE-2024-statistical-report-AP/10-Detailed-Results.xlsx",
        "e25fb680fbb831d00c74d49f2468260979585aeedcdd817cd5dfa3cbf38262ca",
    ),
    Report(
        "OD",
        (date(2024, 5, 13), date(2024, 5, 20), date(2024, 5, 25), date(2024, 6, 1)),
        147,
        FILES + "AE-2024-statistical-report-OR/10-Detailed-Results.xlsx",
        "91b3aad22309915bb25ced1e9cb32791af6adcee476c88c67cced025329b749e",
    ),
    Report(
        "JK",
        (date(2024, 9, 18), date(2024, 9, 25), date(2024, 10, 1)),
        90,
        FILES + "AE-2024-statistical-report-JK/10-Detailed-Results.xlsx",
        "61c29ff942c6ee27ba7f675c026cbf9aaf5f80864888a43673c5fdc7bb8d3263",
    ),
    Report(
        "HR",
        (date(2024, 10, 5),),
        90,
        FILES + "AE-2024-statistical-report-HR/10-Detailed-Results.xlsx",
        "05e6d1b1140595ae7baaa9490ff57baf68cc8498bc16b7baf81d38b2200e5349",
    ),
    Report(
        "MH",
        (date(2024, 11, 20),),
        288,
        REPORTS + "Maharashtra_Legislative_Assembly_Election__2024_2024/10-Detailed_Results_1744893339.xlsx",
        "2de0cdf3e2203350e50d19e89d6407437168833b544225c86f80580a16fa975f",
    ),
    Report(
        "JH",
        (date(2024, 11, 13), date(2024, 11, 20)),
        81,
        REPORTS + "Jharkhand_Legislative_Assembly_Election__2024_2024/10-Detailed_Results_1744892172.xlsx",
        "cd9beddb815d816a83dc9ee8ac630654ef2563a18104b6ef3980d3b1e891f7bc",
    ),
    Report(
        "DL",
        (date(2025, 2, 5),),
        70,
        REPORTS + "NCT_of_Delhi_Legislative_Assembly_Election_2025_2025/10-Detailed_Results_1744913508.xlsx",
        "4a296a2d1079ea4d221b99c07f7a78f7556a914b39065e65847913d30386a547",
    ),
    Report(
        "BR",
        (date(2025, 11, 6), date(2025, 11, 11)),
        243,
        REPORTS + "General_Election_to_the_Legislative_Assembly_of_Bihar_October_to_November_2025_Statistical_Report"
        "_2025/10-Detailed_Results_1763549630.xlsx",
        "788f996b9e066bdf71fb9d7ce3c93387ce1f8070c9aca10055a05eaa891c993a",
    ),
    Report(
        "AS",
        (date(2026, 4, 9),),
        126,
        REPORTS + "General_Election_to_the_Legislative_Assembly_of_Assam_2026_2026/10-Detailed_Results_1778163955.xlsx",
        "6596e5174aea2fad61e62020af8816e824642c588012aead0382dbb6e5994c76",
    ),
    Report(
        "KL",
        (date(2026, 4, 9),),
        140,
        REPORTS
        + "General_Election_to_the_Legislative_Assembly_of_Kerala_2026_2026/10-Detailed_Results_1778164525.xlsx",
        "1720f3f442f482b183e22aa0b65a8c8dbf5f73b52d3da0503c7b957ce601dd53",
    ),
    Report(
        "PY",
        (date(2026, 4, 9),),
        30,
        REPORTS
        + "General_Election_to_the_Legislative_Assembly_of_Puducherry_2026_2026/10-Detailed_Results_1778164807.xlsx",
        "1ef87ea0e24277ac10e2f5b223ea3da368ecd6174d8709559e93ed77aae8b528",
    ),
    Report(
        "TN",
        (date(2026, 4, 23),),
        234,
        REPORTS
        + "General_Election_to_the_Legislative_Assembly_of_Tamil_Nadu_2026_2026/10-Detailed_Results_1778165153.xlsx",
        "e579a9944e64e2ab983a961c2145add2d643fc9d41283a98feaca6d22f6231b1",
    ),
    Report(
        "WB",
        (date(2026, 4, 23), date(2026, 4, 29)),
        294,
        REPORTS + "General_Election_to_the_Legislative_Assembly_of_West_Bengal_2026_Including_AC_144_2026/"
        "10-Detailed_Results_1779879116.xlsx",
        "4b0e592cb8973b16d9c67a1d696cc1ca877946b0790ff7506ed1ad09779ff107",
        late={144: date(2026, 5, 21)},
    ),
]
BY_STATE = {r.state: r for r in ELECTIONS}

METHOD = (
    'Counted by the Election Commission of India for each assembly constituency, as table 10 ("Detailed Results") '
    "of its statistical report on the election prints them, from the index cards the Returning Officers filed. "
    "Votes are split into those cast on the voting machines at the polling stations (stored as election-day votes) "
    "and postal ballots. The votes for none of the above (NOTA) are their own option. Registered voters are the "
    "constituency's total electors. The report does not mark the winner, so the candidate with the most votes is "
    "stored as elected. The Commission notes that its reports are compiled for research from the index cards, and "
    "that the statutory forms kept by the Returning Officers prevail."
)
LATE_NOTE = (
    "This constituency polled on {day:%-d %B %Y}, after the rest of the State, in a fresh poll the Commission "
    "ordered after countermanding or adjourning its first."
)
UNOPPOSED_NOTE = (
    "The report leaves out the {n} constituencies whose candidates were declared elected unopposed, so they are not "
    "stored here."
)


# ---- the raw files -------------------------------------------------------------------------------------------------


def fetch() -> None:
    """Store the saved copies of the Commission's workbooks under their URLs, and fetch the State outlines."""
    raw.fetch(SHAPES, ADM1, suffix=".geojson")
    have = raw.keys(SOURCE)
    wanted = [r for r in ELECTIONS if r.url not in have]
    if not wanted:
        return
    index = json.loads((INBOX / "index.json").read_text())
    for report in wanted:
        entry = index[report.url]
        stored = raw.store(
            SOURCE,
            report.url,
            (INBOX / entry["file"]).read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=XLSX,
        )
        if report.sha256 and stored.sha256 != report.sha256:
            raise ValueError(f"{report.url}: the saved file's hash {stored.sha256} differs from the Commission's")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(election_key(r) for r in ELECTIONS)]


def election_key(report: Report) -> str:
    return f"{SOURCE}/{report.state.lower()}-{report.days[0]:%Y}"


def parse() -> None:
    geo().write()
    registry().write()
    for report in ELECTIONS:
        election(report).write()


def emit() -> None:
    package.run("load", packages())


# ---- reading table 10 ----------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    name: str
    party: str
    gender: str | None
    general: int
    postal: int
    total: int

    @property
    def nota(self) -> bool:
        return self.party == "NOTA"


@dataclass
class Seat:
    number: int
    name: str
    electors: int
    candidates: list[Candidate] = field(default_factory=list)
    printed: tuple[int, int, int] | None = None


def number(text: object) -> int:
    value = str(text).strip().replace(",", "")
    if not re.fullmatch(r"\d+(\.0+)?", value):
        raise ValueError(f"not a count: {text!r}")
    return int(float(value))


@functools.cache
def seats(state: str) -> list[Seat]:
    """The constituencies of a state's table 10, each checked against the printed TURN OUT row beneath it."""
    report = BY_STATE[state]
    frame = pl.read_excel(raw.cached(SOURCE, report.url).path, has_header=False, infer_schema_length=0)
    rows = frame.rows()
    header = next(i for i, row in enumerate(rows) if row[0] == "STATE/UT NAME")
    columns = {str(name).strip(): i for i, name in enumerate(rows[header]) if name is not None}
    gender = columns.get("GENDER", columns.get("SEX"))
    found: list[Seat] = []
    grand: tuple[int, int, int] | None = None
    for row in rows[header + 1 :]:
        first = (row[0] or "").strip()
        if first == "TURN OUT":
            found[-1].printed = (number(row[columns["GENERAL"]]), number(row[columns["POSTAL"]]), number(row[11]))
            continue
        if first.startswith("GRAND"):
            grand = (number(row[columns["GENERAL"]]), number(row[columns["POSTAL"]]), number(row[11]))
            break
        if not first:
            continue
        seat_no = number(row[columns["AC NO."]])
        if not found or found[-1].number != seat_no:
            found.append(Seat(seat_no, str(row[columns["AC NAME"]]).strip(), number(row[columns["TOTAL ELECTORS"]])))
        label = re.sub(r"^\d+\s+", "", str(row[columns["CANDIDATE NAME"]]).strip())
        party = str(row[columns["PARTY"]]).strip()
        sex = row[gender] if gender is not None else None
        found[-1].candidates.append(
            Candidate(
                "NOTA" if party == "NOTA" else " ".join(label.split()),
                party,
                str(sex).strip().lower() if sex else None,
                number(row[columns["GENERAL"]]),
                number(row[columns["POSTAL"]]),
                number(row[columns["TOTAL"]]),
            )
        )
    if grand is None:
        raise ValueError(f"{state}: no GRAND TOTAL row")
    for seat in found:
        for c in seat.candidates:
            if c.general + c.postal != c.total:
                raise ValueError(f"{state} {seat.number}: {c.name}'s votes do not add to the total")
        sums = (
            sum(c.general for c in seat.candidates),
            sum(c.postal for c in seat.candidates),
            sum(c.total for c in seat.candidates),
        )
        if sums != seat.printed:
            raise ValueError(
                f"{state} {seat.number}: the candidates sum to {sums}, the TURN OUT row prints {seat.printed}"
            )
    sums = (
        sum(s.printed[0] for s in found if s.printed),
        sum(s.printed[1] for s in found if s.printed),
        sum(s.printed[2] for s in found if s.printed),
    )
    if sums != grand:
        raise ValueError(f"{state}: the constituencies sum to {sums}, the GRAND TOTAL row prints {grand}")
    numbers = [s.number for s in found]
    if len(set(numbers)) != len(numbers):
        raise ValueError(f"{state}: a constituency appears twice")
    if len(found) + report.unopposed != report.seats:
        raise ValueError(f"{state}: {len(found)} constituencies and {report.unopposed} unopposed, not {report.seats}")
    return found


def grand_total(state: str) -> int:
    return sum(c.total for s in seats(state) for c in s.candidates)


# ---- the geo package -----------------------------------------------------------------------------------------------


def polity(iso: str) -> str:
    return str(uuid5(package.NAMESPACE, f"polity\x1fIN-{iso}"))


def state_area(iso: str) -> str:
    return key("area", STATE_SCHEME, iso)


def delimitation(iso: str) -> date:
    return DELIMITATION.get(iso, DELIMITATION_2008)


def ac_area(iso: str, seat: int) -> str:
    return key("area", AC_SCHEME, iso, delimitation(iso).isoformat(), seat)


@functools.cache
def outlines() -> dict[str, shapely.Geometry]:
    data = json.loads(raw.cached(SHAPES, ADM1).path.read_text(encoding="utf-8"))
    return {
        f["properties"]["shapeISO"].removeprefix("IN-"): shapely.geometry.shape(f["geometry"]) for f in data["features"]
    }


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    polygons = [
        polygon
        for part in shapely.get_parts(shapely.make_valid(shape))
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    shape = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=SHAPE_SOURCE,
    )


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=ECI,
        kind="official_body",
        publisher="Election Commission of India",
        title="Statistical reports of the general elections to the State legislative assemblies",
        url=HOME,
        attribution="Election Commission of India",
        notes="The Commission's site refuses scripted requests; each workbook was saved from the URL it is cited "
        "under, and its SHA-256 hash matches the file the Commission served.",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "India's States and Union territories as polities under the national polity, with geoBoundaries outlines, "
        "and the assembly constituencies of the Election Commission's statistical reports",
    )
    p.external |= {INDIA, COUNTRY}
    _sources(p)
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="India administrative boundaries, ADM1 (geoBoundaries gbOpen, simplified)",
        url="https://www.geoboundaries.org/countryDownloads.html",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries gbOpen, CC BY 4.0",
    )
    shapes = raw.cached(SHAPES, ADM1)
    p.artifact(shapes, SHAPE_SOURCE)
    p.add("scheme", code=STATE_SCHEME, polity_id=INDIA, kind="administrative", rank=1, noun="state")
    p.add("scheme", code=AC_SCHEME, polity_id=INDIA, kind="electoral_district", rank=2, noun="assembly constituency")
    p.add(
        "boundary_set",
        id=key("boundary_set", STATE_SCHEME, "gbopen"),
        scheme_code=STATE_SCHEME,
        vintage="gbOpen",
        as_of=shapes.retrieved_at.date(),
    )
    drawn = outlines()
    for iso, state in sorted(STATES.items()):
        id, area = polity(iso), state_area(iso)
        p.add("area", id=area, scheme_code=STATE_SCHEME)
        p.add("area_code", area_id=area, code=iso)
        p.add("area_parent", area_id=area, parent_id=COUNTRY)
        p.add("area_kind", area_id=area, kind="union_territory" if state.territory else "state")
        p.name(area, state.name, "en", "official")
        _outline(p, area, drawn[state.shape_iso or iso])
        p.add("polity", id=id, kind="federal_unit", iso_code=f"IN-{iso}")
        p.add("polity_parent", polity_id=id, parent_id=INDIA)
        p.add("polity_territory", polity_id=id, area_id=area)
        p.name(id, state.name, "en", "official")
        p.add("slug", path=f"in/{iso.lower()}", entity_id=id, is_current=True)
    for report in ELECTIONS:
        iso = report.state
        for seat in seats(iso):
            area = ac_area(iso, seat.number)
            p.add("area", id=area, scheme_code=AC_SCHEME)
            p.add("area_code", area_id=area, code=f"{iso}-{seat.number}", valid_from=delimitation(iso))
            p.add("area_parent", area_id=area, parent_id=state_area(iso))
            p.name(area, seat.name, "en", "official")
    return p


# ---- the registry package ------------------------------------------------------------------------------------------


def assembly(iso: str) -> str:
    return key("body", "assembly", iso)


def party(abbreviation: str) -> str:
    return key("party", abbreviation)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "India's State and Union territory legislative assemblies, their electoral system, and the parties of the "
        "Election Commission's statistical reports, by the abbreviations the Commission gives them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(INDIA)
    p.add("electoral_system", id=FPTP, polity_id=INDIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each assembly constituency elects one member by plurality. Voters may choose none of the above "
        "(NOTA); those votes are counted and published but do not affect who is elected.",
        lang="en",
    )
    for iso, state in sorted(STATES.items()):
        body = assembly(iso)
        p.add("body", id=body, polity_id=polity(iso), kind="lower_house" if state.council else "unicameral")
        p.name(body, f"{state.name} Legislative Assembly", "en", "official")
        if iso in BY_STATE:
            p.add("body_size", body_id=body, seats=BY_STATE[iso].seats, valid_from=BY_STATE[iso].days[0])
    used = sorted(
        {c.party for r in ELECTIONS for s in seats(r.state) for c in s.candidates if c.party not in ("NOTA", "IND")}
    )
    for abbreviation in used:
        id = party(abbreviation)
        p.add("party", id=id, kind="party", polity_id=INDIA)
        p.name(id, abbreviation, "en", "abbreviation")
    return p


# ---- the election packages -----------------------------------------------------------------------------------------


def put(p: Package, result_set: str, unit: str, option: str | None, channel: int, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=channel,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def election(report: Report) -> Package:
    iso = report.state
    state = STATES[iso]
    first, last = report.days[0], report.days[-1]
    p = Package(
        election_key(report),
        SOURCE,
        f"The {first:%Y} general election to the {state.name} Legislative Assembly by assembly constituency, from "
        "the Election Commission of India's statistical report",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(INDIA)
    p.artifact(raw.cached(SOURCE, report.url), ECI)
    id = key("election", iso, first)
    path = f"in/{iso.lower()}/{first.isoformat()}-assembly"
    p.add("election", id=id, polity_id=polity(iso), kind="general", date_from=first, date_to=last, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{first:%Y} {state.name} Legislative Assembly election", "en", "official")
    if report.unopposed:
        p.add(
            "note",
            id=key("note", id, "unopposed"),
            entity_id=id,
            kind="caveat",
            body_md=UNOPPOSED_NOTE.format(n=report.unopposed),
            lang="en",
        )
    for seat in seats(iso):
        tag = f"{iso}:{first.isoformat()}:{seat.number}"
        district = ac_area(iso, seat.number)
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=polity(iso),
            scheme_code=AC_SCHEME,
            code=tag,
            area_relation="equals",
            area_id=district,
            kind="standard",
            valid_from=report.late.get(seat.number, first),
        )
        p.name(unit, f"{seat.number} {seat.name}", "en", "official")
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=assembly(iso),
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        p.name(contest, f"{first:%Y} {state.name} Legislative Assembly election, {seat.name}", "en", "official")
        p.add("slug", path=f"{path}/{seat.number}-{changeset.slug(seat.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECI,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD
        if seat.number in report.late:
            method = f"{method} {LATE_NOTE.format(day=report.late[seat.number])}"
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        ranked = sorted((c.total for c in seat.candidates if not c.nota), reverse=True)
        for index, c in enumerate(seat.candidates, start=1):
            option = key("option", tag, index)
            if c.nota:
                p.add("contest_option", id=option, contest_id=contest, kind="none_of_the_above", ballot_label="NOTA")
            else:
                solo = c.party == "IND"
                person = key("person", tag, index)
                p.add("person", id=person, gender=c.gender if c.gender in ("male", "female") else None)
                p.name(person, c.name, "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if solo else party(c.party),
                    ballot_label=c.name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", tag, index),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=c.name,
                    member_of_party_id=None if solo else party(c.party),
                    is_independent=solo,
                )
                if c.total == ranked[0] and (len(ranked) < 2 or ranked[0] > ranked[1]):
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            put(p, result_set, unit, option, ELECTION_DAY, VOTES, c.general)
            put(p, result_set, unit, option, POSTAL, VOTES, c.postal)
        put(p, result_set, unit, None, 0, REGISTERED, seat.electors)
    return p
