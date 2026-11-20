"""The three plebiscites on a directly elected mayor held on 24 May 2019, from the Department of Housing's open data.

Part 6 of the Local Government Act 2019 put the question to the electors of Cork City, Limerick City and County, and
Waterford City and County. The Department's CSV gives each council's electorate, poll, valid and invalid votes, and
the votes for and against. Each council area is one reporting unit, so the package builds one election per council,
under a polity for that council, with one ballot question each.

The archive already holds the three council areas, their reporting units, and their council bodies, and the package
refers to them by id. Tailte Éireann's local authority boundaries give each area its shape.
"""

import csv
import json
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "ie.mayor_plebiscites"
PORTAL = "https://opendata.housing.gov.ie"
CSV = (
    f"{PORTAL}/en_GB/dataset/69c6b3c7-842c-498b-969e-bdb2b0f38e61/resource/c95dad5b-7b1c-4d0e-977a-1dc7aef05a76"
    "/download/2019-plebiscite-results.csv"
)
DATASET = f"{PORTAL}/en_GB/dataset/69c6b3c7-842c-498b-969e-bdb2b0f38e61"
SERVICE = (
    "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services/"
    "LocalAuthorities_NationalStatutoryBoundaries_Ungeneralised_2024/FeatureServer/0/query"
)
OUTLINES = (
    f"{SERVICE}?where=ENG_NAME_VALUE+IN+(%27CORK+CITY+COUNCIL%27,%27LIMERICK+CITY+AND+COUNTY+COUNCIL%27,"
    "%27WATERFORD+CITY+AND+COUNTY+COUNCIL%27)&outFields=BDY_ID,ENG_NAME_VALUE&outSR=4326"
    "&maxAllowableOffset=0.0002&geometryPrecision=6&f=geojson"
)
DAY = date(2019, 5, 24)
# Ireland's record predates derived country ids, so the archive keeps the id it was first given
IRELAND = "01a13bd3-643c-74a1-9302-0a8df9ef9df8"
SCHEME = "ie.local_authority"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
METHOD = (
    "Published by the Department of Housing, Local Government and Heritage from the returning officers' figures. The "
    "electorate is the register of electors for the poll, the poll is the ballot papers issued, and the valid and "
    "invalid votes and the votes for and against the proposal are as printed."
)


class Council:
    def __init__(self, printed: str, code: str, name: str, area: str, unit: str, body: str, outline: str) -> None:
        self.printed = printed  # the CSV's name of the administrative area
        self.code = code  # the code of its reporting unit and area in the archive
        self.name = name
        self.area = area
        self.unit = unit
        self.body = body
        self.outline = outline  # the boundary file's name for it


# The archive's rows for the three council areas, their reporting units, and their council bodies.
COUNCILS = [
    Council(
        "Cork City Council",
        "cork-city",
        "Cork City",
        "c128e72c-a181-53a7-a8a8-70f75f8ff762",
        "3247fdd2-0691-5bdd-a6fa-7ac3806bbd8f",
        "bd5d95d8-ed4a-5a74-8e6f-eb94fcc79ee5",
        "CORK CITY COUNCIL",
    ),
    Council(
        "Limerick City and County Council",
        "limerick-city-and-county",
        "Limerick City and County",
        "93550433-458c-58fb-aa2f-bbbeb9a2f48d",
        "da53d97f-f5d6-59bd-ab56-2b4c2f8ecbfc",
        "af19a883-0fb4-584a-b7c4-d4f78cacadbe",
        "LIMERICK CITY AND COUNTY COUNCIL",
    ),
    Council(
        "Waterford City and County Council",
        "waterford-city-and-county",
        "Waterford City and County",
        "a212c387-de58-557a-9108-7670e5ff01c1",
        "73c0c5bb-43f6-5b58-9f73-a498cf7f3159",
        "50b409b5-645c-5f1d-a60a-0c46f758392b",
        "WATERFORD CITY AND COUNTY COUNCIL",
    ),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "housing")
TAILTE_ID = key("source", "tailte")
SYSTEM = key("electoral_system", "plebiscite")
BOUNDARY_SET = key("boundary_set", SCHEME, 2024)


def polity(council: Council) -> str:
    return key("polity", council.code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/plebiscites-{DAY}"]


def fetch() -> None:
    raw.fetch(SOURCE, CSV, suffix=".csv")
    raw.fetch(SOURCE, OUTLINES, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    plebiscites().write()


def emit() -> None:
    package.run("load", packages())


def figures() -> dict[str, dict[str, int]]:
    text = raw.cached(SOURCE, CSV).path.read_text(encoding="utf-8-sig")
    out: dict[str, dict[str, int]] = {}
    for row in csv.DictReader(text.splitlines()):
        if row["Polling day"] != DAY.strftime("%d/%m/%Y"):
            raise ValueError(f"{row['Administrative area']} polled on {row['Polling day']}")
        out[row["Administrative area"]] = {
            "registered": int(row["Electorate"]),
            "ballots": int(row["Total poll"]),
            "valid": int(row["Valid votes"]),
            "invalid": int(row["Invalid votes"]),
            "yes": int(row["Votes in favour of proposal"]),
            "no": int(row["Votes against proposal"]),
        }
    if set(out) != {c.printed for c in COUNCILS}:
        raise ValueError(f"the CSV holds {sorted(out)}")
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines of Cork City, Limerick City and County, and Waterford City and County from Tailte Éireann",
    )
    p.external |= {SCHEME, *(c.area for c in COUNCILS)}
    p.add(
        "source",
        id=TAILTE_ID,
        kind="official_body",
        publisher="Tailte Éireann",
        title="Local authorities, national statutory boundaries, ungeneralised, 2024",
        url="https://data-osi.opendata.arcgis.com/",
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Tailte Éireann",
    )
    p.artifact(raw.cached(SOURCE, OUTLINES), TAILTE_ID)
    p.add(
        "boundary_set",
        id=BOUNDARY_SET,
        scheme_code=SCHEME,
        vintage="2024",
        valid_from=DAY,
        valid_to=None,
        as_of=date(2024, 1, 1),
    )
    parts: dict[str, list[shapely.Geometry]] = {}
    body = json.loads(raw.cached(SOURCE, OUTLINES).path.read_text("utf-8"))
    for feature in body["features"]:
        parts.setdefault(feature["properties"]["ENG_NAME_VALUE"], []).append(
            shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        )
    for council in COUNCILS:
        if council.outline not in parts:
            raise ValueError(f"the boundary file lacks {council.outline}")
        shape = shapely.union_all(parts[council.outline])
        if shape.geom_type == "Polygon":
            shape = shapely.geometry.MultiPolygon([shape])
        p.add(
            "area_geometry",
            area_id=council.area,
            valid_from=DAY,
            valid_to=None,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=TAILTE_ID,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The polities of the three councils that held plebiscites on a directly elected mayor in 2019, the source, "
        "and the electoral system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {IRELAND, *(c.area for c in COUNCILS)}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Department of Housing, Local Government and Heritage",
        title="2019 plebiscite results",
        url=DATASET,
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Department of Housing, Local Government and Heritage",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=IRELAND,
        valid_from=DAY,
        valid_to=DAY,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.name(SYSTEM, "Plebiscite on a directly elected mayor", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under Part 6 of the Local Government Act 2019 the electors of the council area voted yes or no on "
        "a directly elected mayor, and the proposal carried when the votes in favor outnumbered the votes against.",
        lang="en",
    )
    for council in COUNCILS:
        id = polity(council)
        p.add("polity", id=id, kind="county", iso_code=None)
        p.add("polity_parent", polity_id=id, parent_id=IRELAND)
        p.add("polity_territory", polity_id=id, area_id=council.area)
        p.name(id, f"{council.name} Council", "en", "official")
        p.add("external_id", entity_id=id, scheme=SCHEME, value=council.code)
    return p


def plebiscites() -> Package:
    p = Package(
        f"{SOURCE}/plebiscites-{DAY}",
        SOURCE,
        "The plebiscites on a directly elected mayor in Cork City, Limerick City and County, and Waterford City and "
        "County on 24 May 2019, by council area",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {IRELAND, SCHEME}
    p.artifact(raw.cached(SOURCE, CSV), SOURCE_ID)
    found = figures()
    mismatches: list[str] = []
    for council in COUNCILS:
        p.external |= {council.area, council.unit, council.body}
        row = found[council.printed]
        if row["valid"] + row["invalid"] != row["ballots"] or row["yes"] + row["no"] != row["valid"]:
            mismatches.append(f"{council.name}: {row}")
        election = key("election", council.code)
        p.add(
            "election",
            id=election,
            polity_id=polity(council),
            kind="referendum",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        p.name(election, f"2019 {council.name} directly elected mayor plebiscite", "en", "official")
        base = f"ie/{DAY}-mayor-plebiscite-{council.code}"
        p.add("slug", path=base, entity_id=election, is_current=True)
        contest = key("contest", council.code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            office_id=None,
            body_id=None,
            district_area_id=council.area,
            magnitude=None,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Directly elected mayor for {council.name}", "en", "official")
        p.add("slug", path=f"{base}/directly-elected-mayor", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="plebiscite")
        result_set = key("result_set", council.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options = {}
        for position, label in enumerate(("Yes", "No"), 1):
            option = options[label] = key("option", council.code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, label, "en", "official")
        facts = [
            (None, REGISTERED, row["registered"]),
            (None, BALLOTS, row["ballots"]),
            (None, VALID, row["valid"]),
            (None, INVALID, row["invalid"]),
            (options["Yes"], VOTES, row["yes"]),
            (options["No"], VOTES, row["no"]),
        ]
        for option, measure, value in facts:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=council.unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        winner = "Yes" if row["yes"] > row["no"] else "No"
        for label, option in options.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if label == winner else "lost")
    print(f"{p.key}: {len(COUNCILS)} plebiscites, {len(mismatches)} rows whose figures do not add up", flush=True)
    if mismatches:
        raise ValueError("; ".join(mismatches))
    return p
