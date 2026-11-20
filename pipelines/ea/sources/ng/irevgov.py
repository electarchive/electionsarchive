"""Nigeria's off-cycle governorship elections by polling unit, read from the Form EC8A sheets on the Commission's
result viewing portal.

The portal (IReV) lists, for each governorship election, the state's local government areas and wards, and for each
ward every polling unit with the scan of its Form EC8A, the statement of result that the presiding officer signs and
uploads. Its API answers `elections` with every election on the portal, `elections/<id>/lga` with the areas and
their wards, and `elections/<id>/pus?ward=<id>` with a ward's polling units and their documents. For these elections
the portal holds no figures of its own (its party lists are empty), so the sheets are the only record by unit. Each
scan is a phone photograph of the form, with the figures written by hand, kept on the portal's image cache.

The sheets were read by eye from the page images, twice and independently, by two different multimodal models, and
the two readings were compared field by field. Every disagreement over a party's votes, the total valid votes, or
whether a sheet belongs to its polling unit was settled by a third look at the sheet; a disagreement over rows 1 to
8 was settled the same way or by the sheet's own arithmetic. The settled readings, with each figure's box on the
page as a fraction of its width and height, are kept in `irevgov/<date>-<state>-<lga>.tsv`, where `?` marks a figure
left unread (not stored). A sheet whose parties' votes do not add up to its total valid votes is stored as written,
with a caveat on the result set. A sheet with no readable figures, or a scan that is not the governorship EC8A of
its own polling unit, is left out.

The local government areas and wards are the Commission's, coded `state/lga` and `state/lga/ward` as in
`ea.sources.ng.irev2023`, with outlines from geoBoundaries (local government areas) and GRID3 (wards).
"""

import csv
import json
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ea import changeset, package, raw
from ea.package import Package
from ea.raw import Artifact
from ea.sources.ng import inec, inec2022, irev2023
from ea.store import DATA

SOURCE = "ng.irev"
API = "https://dolphin-app-sleqh.ondigitalocean.app/api/v1/elections"
PORTAL = "https://www.inecelectionresults.ng/"
NIGERIA = irev2023.NIGERIA
READINGS = Path(__file__).parent / "irevgov"
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
DIGITIZED = 8
RENDER = 2000


@dataclass(frozen=True)
class Election:
    portal: str
    day: date
    state: str
    code: str
    name: str
    parties: tuple[str, ...]
    # Parties the form leaves out that some presiding officers wrote in by hand below the printed rows; stored only
    # where written.
    extra: tuple[str, ...]
    # The local government areas read so far, by the Commission's code: geoBoundaries' name for the area, and GRID3's
    # name for each of its wards by the Commission's ward code.
    read: dict[str, tuple[str, str, dict[str, str]]]


ELECTIONS = [
    Election(
        "6a35bb87b4e45d80b33a6e38",
        date(2026, 6, 20),
        "13",
        "EK",
        "Ekiti",
        (
            "A", "AA", "AAC", "ADC", "ADP", "APC", "APM", "APP",
            "LP", "NNPP", "PDP", "PRP", "SDP", "YPP", "ZLP",
        ),
        ("APGA",),
        {
            "12": (
                "Ilejemeje",
                "Ilejemeje",
                {
                    "01": "Ewu",
                    "02": "Eda Oniyo",
                    "03": "Eda Obada",
                    "04": "Iye",
                    "05": "Ilefon",
                    "06": "Isapa",
                    "07": "Ijesa Modu",
                    "08": "Iludun I",
                    "09": "Iludun II",
                    "10": "Ipere",
                },
            ),
        },
    ),
]  # fmt: skip


def _json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not data.get("success"):
        raise ValueError(f"{path} is not a successful answer")
    return data


def area_url(e: Election) -> str:
    return f"{API}/{e.portal}/lga"


def ward_url(e: Election, ward: str) -> str:
    return f"{API}/{e.portal}/pus?ward={ward}"


def lgas(e: Election) -> list[dict]:
    return _json(raw.cached(SOURCE, area_url(e)).path)["data"]


def wards(e: Election, lga: str) -> list[tuple[str, dict]]:
    """The portal id and record of each ward in a local government area."""
    for entry in lgas(e):
        if entry["lga"]["state_id"] and irev2023._code(entry["lga"]["code"]) == lga:
            return [(w["_id"], w) for w in entry["wards"]]
    raise KeyError(f"no local government area {e.state}/{lga}")


def units(e: Election, lga: str) -> list[irev2023.Unit]:
    found: list[irev2023.Unit] = []
    for id, ward in wards(e, lga):
        for row in _json(raw.cached(SOURCE, ward_url(e, id)).path)["data"]:
            pu = row["polling_unit"]
            parts = pu["pu_code"].split("/")
            if parts[:3] != [e.state, lga, irev2023._code(ward["code"])]:
                raise ValueError(f"{pu['pu_code']} is listed under ward {ward['code']} of {e.state}/{lga}")
            found.append(
                irev2023.Unit(e.state, lga, parts[2], parts[3], pu["name"], ward["name"], irev2023.document(row))
            )
    codes = [u.code for u in found]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{e.state}/{lga} lists a polling unit twice")
    return sorted(found, key=lambda u: u.code)


def listed(e: Election) -> int:
    """How many polling units the portal lists for the whole election."""
    return sum(
        len(_json(raw.cached(SOURCE, ward_url(e, w["_id"])).path)["data"]) for entry in lgas(e) for w in entry["wards"]
    )


def fetch() -> None:
    raw.fetch(SOURCE, API, suffix=".json")
    raw.fetch(irev2023.SHAPES, irev2023.LGAS, suffix=".geojson")
    for e in ELECTIONS:
        raw.fetch(SOURCE, area_url(e), suffix=".json")
        raw.fetch(irev2023.GRID3, irev2023.WARDS.format(e.code), suffix=".geojson")
        for entry in lgas(e):
            for w in entry["wards"]:
                raw.fetch(SOURCE, ward_url(e, w["_id"]), suffix=".json")
        for lga in e.read:
            for unit in units(e, lga):
                if unit.document:
                    irev2023.scan(unit.document, fetching=True)


def image_key(artifact: Artifact) -> str:
    return f"pages/{SOURCE}/{artifact.sha256}-1.jpg"


def render(artifact: Artifact) -> tuple[int, int]:
    """The scan as an upright JPEG with its longer side at most `RENDER` pixels, and the image's width and height. The
    readings' boxes are fractions of this image."""
    out = DATA / image_key(artifact)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "convert",
                str(artifact.path),
                "-auto-orient",
                "-resize",
                f"{RENDER}x{RENDER}>",
                "-quality",
                "85",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
    size = subprocess.run(["identify", "-format", "%w %h", str(out)], check=True, capture_output=True, text=True)
    width, height = (int(x) for x in size.stdout.split())
    return width, height


def key(e: Election) -> str:
    return f"{SOURCE}/{e.day}"


def geo_key(e: Election) -> str:
    return f"{SOURCE}/geo-{e.code.lower()}"


def packages() -> list[str]:
    return [p for e in ELECTIONS for p in (geo_key(e), key(e))]


METHOD = (
    "Read by eye from the scans of Form EC8A that presiding officers uploaded to the Commission's result viewing "
    "portal (IReV), one sheet per polling unit, twice and independently by two different multimodal models, with "
    "every disagreement settled by a third look at the sheet. Registered voters are row 1 of the sheet, voters are "
    "the accredited voters of row 2, invalid are the rejected ballots of row 6, valid are the total valid votes, and "
    "each option's votes are the figures written against its party. Ballot papers issued, unused, spoiled, and used "
    "(rows 3, 4, 5, and 8) have no measure here. A cell written as nil, or left blank, is read as no votes. The "
    "portal names no candidates, so each option stands for its party's candidate. Only the local government areas "
    "named in the coverage note are read, and a polling unit whose scan is missing, unreadable, or not its own "
    "governorship EC8A is left out. These are the polling units' own figures, not the collated declaration."
)


@dataclass(frozen=True)
class Cell:
    value: int | None
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class Sheet:
    code: str
    status: str
    cells: dict[str, Cell]


ROWS = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
MEASURES = {"R1": REGISTERED, "R2": VOTERS, "R6": INVALID}


def readings(e: Election, lga: str) -> dict[str, Sheet]:
    """The settled readings of a local government area's sheets, by polling-unit code."""
    fields = [*ROWS, *e.parties, "TOTAL"]
    optional = set(e.extra)
    rows: dict[str, list[dict[str, str]]] = {}
    with (READINGS / f"{e.day}-{e.state}-{lga}.tsv").open(newline="") as file:
        for row in csv.DictReader(file, delimiter="\t", quoting=csv.QUOTE_NONE):
            rows.setdefault(row["code"], []).append(row)
    found: dict[str, Sheet] = {}
    for code, group in rows.items():
        status = [r for r in group if r["field"] == "STATUS"]
        if len(status) != 1:
            raise ValueError(f"{code} has {len(status)} status rows")
        cells: dict[str, Cell] = {}
        for r in group:
            if r["field"] == "STATUS":
                continue
            if r["field"] not in (*fields, *optional) or r["field"] in cells:
                raise ValueError(f"{code} has an unknown or repeated field {r['field']}")
            if r["value"] not in ("", "?") and not r["value"].isdigit():
                raise ValueError(f"{code} {r['field']} holds {r['value']!r}")
            if r["value"] == "?" and r["field"] in (*e.parties, *optional, "TOTAL"):
                raise ValueError(f"{code} cannot be stored with its {r['field']} figure unread")
            box = (float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))
            if not all(0 <= v <= 1 for v in box) or box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError(f"{code} {r['field']} has a box outside the page")
            cells[r["field"]] = Cell(int(r["value"]) if r["value"].isdigit() else None, box)
        if status[0]["value"] == "ok" and sorted(set(cells) - optional) != sorted(fields):
            raise ValueError(f"{code} does not hold every field")
        if status[0]["value"] != "ok" and cells:
            raise ValueError(f"{code} is {status[0]['value']} but holds figures")
        found[code] = Sheet(code, status[0]["value"], cells)
    return found


def valid(sheet: Sheet) -> tuple[str, int] | None:
    """The sheet's total valid votes and the field it is read from: the figure under the table, else row 7."""
    for field in ("TOTAL", "R7"):
        number = sheet.cells[field].value
        if number is not None:
            return field, number
    return None


def sources(p: Package, e: Election) -> str:
    id = p.id("source", "irev", e.day, e.code)
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title=f"INEC Result Viewing Portal (IReV): {e.day.year} {e.name} State governorship election, Forms EC8A "
        "by polling unit",
        url=PORTAL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent National Electoral Commission",
    )
    p.artifact(raw.cached(SOURCE, area_url(e)), id)
    return id


def geo(e: Election) -> Package:
    p = Package(
        geo_key(e),
        SOURCE,
        f"The local government areas and registration areas (wards) of {e.name} State whose {e.day.year} "
        "governorship EC8A sheets have been read, with outlines from geoBoundaries and GRID3",
        depends=[f"{SOURCE}/geo"],
    )
    state = irev2023.state_area(e.code)
    p.external |= {NIGERIA, state}
    # The shape sources get ids of their own: the reference the loader checks against carries no source rows, so
    # the depended-on geo package's sources cannot be cited from here.
    lga_source = p.id("source", irev2023.SHAPES, "adm2", e.code)
    ward_source = p.id("source", irev2023.GRID3, "wards-v4", e.code)
    lga_file = raw.cached(irev2023.SHAPES, irev2023.LGAS)
    ward_file = raw.cached(irev2023.GRID3, irev2023.WARDS.format(e.code))
    p.add(
        "source",
        id=lga_source,
        kind="academic",
        publisher="geoBoundaries",
        title=f"geoBoundaries gbOpen Nigeria ADM2 (local government areas, simplified; from GRID3), for {e.name} State",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); GRID3",
    )
    p.artifact(lga_file, lga_source)
    p.add(
        "source",
        id=ward_source,
        kind="academic",
        publisher="GRID3",
        title=f"GRID3 NGA - Operational Wards v4.0, {e.name} State",
        url="https://data.grid3.org/datasets/GRID3::grid3-nga-operational-wards-v4-0/about",
        license="Free to use, copy, distribute, and adapt with attribution (GRID3 terms of use)",
        attribution="GRID3 (Geo-Referenced Infrastructure and Demographic Data for Development); CIESIN",
    )
    p.artifact(ward_file, ward_source)
    shaped = irev2023.gb_lgas()
    ward_shapes: dict[tuple[str, str], object] = {}
    for feature in json.loads(ward_file.path.read_text(encoding="utf-8"))["features"]:
        props = feature["properties"]
        place = (props["lga"], props["ward"])
        if place in ward_shapes:
            raise ValueError(f"GRID3 lists {place} twice")
        ward_shapes[place] = irev2023.shapely.geometry.shape(feature["geometry"])
    for lga, (gb_name, grid3_lga, ward_names) in sorted(e.read.items()):
        id = irev2023.lga_area(e.state, lga)
        record = next(entry["lga"] for entry in lgas(e) if irev2023._code(entry["lga"]["code"]) == lga)
        name = irev2023.title(record["name"])
        p.add("area", id=id, scheme_code="ng.lga")
        p.add("area_code", area_id=id, code=f"{e.state}/{lga}")
        p.add("area_parent", area_id=id, parent_id=state)
        p.name(id, name, "en", "official")
        irev2023.outline(p, id, shaped[gb_name], lga_source)
        portal = {irev2023._code(w["code"]): w for _, w in wards(e, lga)}
        if sorted(portal) != sorted(ward_names):
            raise ValueError(f"{e.state}/{lga}: the portal's wards {sorted(portal)} differ from the curated list")
        for ward, w in sorted(portal.items()):
            ward_id = irev2023.ward_area(e.state, lga, ward)
            p.add("area", id=ward_id, scheme_code="ng.registration_area")
            p.add("area_code", area_id=ward_id, code=f"{e.state}/{lga}/{ward}")
            p.add("area_parent", area_id=ward_id, parent_id=id)
            p.name(ward_id, f"{irev2023.title(w['name'])} ({name})", "en", "official")
            irev2023.outline(p, ward_id, ward_shapes[(grid3_lga, ward_names[ward])], ward_source)
    return p


def results(e: Election) -> Package:
    p = Package(
        key(e),
        SOURCE,
        f"The {e.day:%-d %B %Y} {e.name} State governorship election, by polling unit, read from the EC8A sheets on "
        "the Commission's result viewing portal",
        depends=[geo_key(e), f"{SOURCE}/geo"],
    )
    tag = f"governor:{e.code}:{e.day}"
    office = inec2022.shared("office", "governor", e.code)
    state = irev2023.state_area(e.code)
    parties = {party: inec2022.party(party) for party in (*e.parties, *e.extra)}
    p.external |= {NIGERIA, office, state, *parties.values()}
    source = sources(p, e)
    for entry in lgas(e):
        for w in entry["wards"]:
            p.uses(raw.cached(SOURCE, ward_url(e, w["_id"])))
    election = p.id("election", tag)
    path = f"ng/{e.day}-{changeset.slug(e.name)}-governorship"
    p.add("election", id=election, polity_id=NIGERIA, kind="general", date_from=e.day, date_to=e.day, status="held")
    p.name(election, f"{e.day.year} {e.name} State governorship election", "en", "official")
    p.add("slug", path=path, entity_id=election, is_current=True)
    # The registry's systems are not in the reference the loader checks against, so the contest gets its own, with the
    # registry's rule for single-winner offices.
    system = p.id("electoral_system", tag)
    since, rules, rule = inec.SYSTEMS["fptp"]
    p.add(
        "electoral_system", id=system, polity_id=NIGERIA, valid_from=since, valid_to=None, rules=rules, rules_version=1
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=rule, lang="en")
    contest = p.id("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=office,
        body_id=None,
        district_area_id=state,
        magnitude=1,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, f"{e.day.year} {e.name} State governorship election", "en", "official")
    p.add("slug", path=f"{path}/governor/{changeset.slug(e.name)}", entity_id=contest, is_current=True)
    options = {}
    for position, party in enumerate((*e.parties, *e.extra), 1):
        options[party] = p.id("option", tag, party)
        p.add(
            "contest_option",
            id=options[party],
            contest_id=contest,
            kind="candidate",
            party_id=parties[party],
            ballot_position=position,
            ballot_label=party,
        )
    result_set = p.id("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    stored = 0
    accounting: list[str] = []
    left: list[str] = []
    names: list[str] = []
    for lga in sorted(e.read):
        read = readings(e, lga)
        portal = units(e, lga)
        unknown = set(read) - {u.code for u in portal}
        if unknown:
            raise ValueError(f"readings for polling units the portal does not list: {sorted(unknown)}")
        names.append(e.read[lga][0])
        for unit in portal:
            sheet = read.get(unit.code)
            if unit.document is None or sheet is None or sheet.status != "ok":
                left.append(unit.code)
                continue
            stored += 1
            artifact = irev2023.scan(unit.document)
            artifact_id = p.artifact(artifact, source)
            width, height = render(artifact)
            page = p.id("page", artifact.sha256)
            p.add(
                "source_page",
                id=page,
                artifact_id=artifact_id,
                page_number=1,
                image_key=image_key(artifact),
                width=width,
                height=height,
            )
            id = irev2023.key("unit", unit.code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=NIGERIA,
                scheme_code="ng.polling_unit",
                code=unit.code,
                area_relation="within",
                area_id=irev2023.ward_area(e.state, lga, unit.ward),
                kind="standard",
            )
            p.name(id, irev2023.title(unit.name), "en", "official")

            def put(option: str | None, measure: int, field: str, number: int) -> None:
                x0, y0, x1, y1 = sheet.cells[field].box
                fact: dict[str, object] = {
                    "result_set_id": result_set,
                    "unit_id": id,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                }
                p.add("result_value", **fact, count_round=0, flags=DIGITIZED, value=number)
                p.add(
                    "value_locator", **fact, count_round=0, page_id=page, source_id=source, x0=x0, y0=y0, x1=x1, y1=y1
                )

            votes = {party: sheet.cells[party].value or 0 for party in (*e.parties, *e.extra) if party in sheet.cells}
            for party, number in votes.items():
                put(options[party], VOTES, party, number)
            for field, measure in MEASURES.items():
                number = sheet.cells[field].value
                if number is not None:
                    put(None, measure, field, number)
            total = valid(sheet)
            if total is not None:
                put(None, VALID, *total)
            if total is None or sum(votes.values()) != total[1]:
                accounting.append(unit.code)
    p.add(
        "note",
        id=p.id("note", result_set, "coverage"),
        entity_id=result_set,
        kind="caveat",
        body_md=(
            f"The portal lists {listed(e):,} polling units for this election. The sheets read so far are those of "
            f"{', '.join(names)} local government area: {stored:,} polling units are stored, and "
            f"{len(left)} are left out because their scan is missing, unreadable, or not their own sheet"
            + (f" ({', '.join(left)})." if left else ".")
        ),
        lang="en",
    )
    if accounting:
        p.accept(
            "accounting.votes_valid",
            f"At {len(accounting)} polling units the parties' votes written on the sheet do not add up to the total "
            "valid votes written on the same sheet (by one to three votes); the figures are stored as written, and "
            "a caveat on the result set names the units.",
        )
        p.add(
            "note",
            id=p.id("note", result_set, "accounting"),
            entity_id=result_set,
            kind="caveat",
            body_md="These polling units' sheets do not add up as written, and are stored as written: "
            + ", ".join(accounting)
            + ".",
            lang="en",
        )
    return p


def parse() -> None:
    for e in ELECTIONS:
        geo(e).write()
        results(e).write()


def emit() -> None:
    package.run("load", packages())
