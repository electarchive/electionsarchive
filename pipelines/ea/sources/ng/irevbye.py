"""Nigeria's bye-elections to the House of Representatives by polling unit, read from the Form EC8A sheets on the
Commission's result viewing portal.

The portal (IReV) lists each bye-election with its local government areas and wards, and for each ward every polling
unit with the scan of its Form EC8A (II), the statement of result that the presiding officer signs and uploads. Its
API answers `elections` with every election on the portal, `elections/<id>/lga` with the areas and their wards, and
`elections/<id>/pus?ward=<id>` with a ward's polling units and their documents. For these elections the portal holds
no figures of its own (its party lists are empty), so the sheets are the only record by unit. Each scan is a phone
photograph of the form, with the figures written by hand.

The sheets were read by eye from the page images, twice and independently, by two different multimodal models, and
the two readings were compared field by field. Every disagreement over a party's votes, the total valid votes,
the registered or accredited voters, the rejected ballots, or whether a sheet belongs to its polling unit was settled
by a third look at the sheet by a third model. The settled readings, with each figure's box on the page as a
fraction of its width and height, are kept in `irevbye/<date>-<state>-<lga>.tsv`, where `?` marks a figure left
unread (not stored). A sheet whose parties' votes do not add up to its total valid votes is stored as written, with
a caveat on the result set. A sheet with no readable figures, or a scan that is not the EC8A of its own polling unit,
is left out.

The constituency, the House, the state, and the parties are the rows the `ng.inec` pipeline holds. The local
government areas and wards are the Commission's, coded `state/lga` and `state/lga/ward` as in
`ea.sources.ng.irev2023`, with outlines from geoBoundaries (local government areas) and GRID3 (wards).
"""

import csv
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ng import inec, inec2022, irev2023, irevgov
from ea.sources.ng.irevgov import Cell, Sheet

SOURCE = "ng.irev"
API = irevgov.API
PORTAL = irevgov.PORTAL
NIGERIA = irev2023.NIGERIA
READINGS = Path(__file__).parent / "irevbye"
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
DIGITIZED = 8


@dataclass(frozen=True)
class Election:
    portal: str
    day: date
    # The Commission's two-digit state code and the archive's state code.
    state: str
    code: str
    # GRID3's code for the state.
    grid3: str
    # The constituency's code in the `ng.inec` registry and its name.
    district: str
    name: str
    parties: tuple[str, ...]
    # The local government areas read, by the Commission's code: geoBoundaries' name for the area, GRID3's name for
    # it, and GRID3's name for each of its wards by the Commission's ward code.
    read: dict[str, tuple[str, str, dict[str, str]]]


ELECTIONS = [
    Election(
        "6aadab293ab7cdac706bb5c4",
        date(2026, 9, 19),
        "15",
        "GM",
        "GO",
        "FC/127/GM",
        "Gombe/Kwami/Funakaye",
        ("APC", "APM", "APP", "LP", "NDP"),
        {
            "05": (
                "Funakaye",
                "Funakaye",
                {
                    "01": "Ashaka/Magaba", "02": "Bage", "03": "Bajoga West", "04": "Bajoga East",
                    "05": "Tilde/Bodor", "06": "Jillahi", "07": "Kupto", "08": "Ribadu", "09": "Tongo",
                    "10": "Wawa/Wakkaltu",
                },
            ),
            "06": (
                "Gombe",
                "Gombe",
                {
                    "01": "Ajiya", "02": "Bajoga", "03": "Bolari East", "04": "Bolari West", "05": "Dawaki",
                    "06": "Herwa Gana", "07": "Jekadafari", "08": "Kumbiya Kumbiya", "09": "Nasarawo",
                    "10": "Pantami", "11": "Shamaki",
                },
            ),
            "08": (
                "Kwami",
                "Kwami",
                {
                    "01": "Bojude", "02": "Daban Fulani", "03": "Doho", "04": "Dukul", "05": "Gadam",
                    "06": "Jurara", "07": "Komfulata", "08": "Kwami", "09": "Malam Sidi", "10": "Malleri",
                },
            ),
        },
    ),
]  # fmt: skip
# Parties on the sheets that the Commission's registry package does not hold; the sheet gives only the abbreviation.
NEW_PARTIES = {"NDP"}


def area_url(e: Election) -> str:
    return f"{API}/{e.portal}/lga"


def ward_url(e: Election, ward: str) -> str:
    return f"{API}/{e.portal}/pus?ward={ward}"


def lgas(e: Election) -> list[dict]:
    return irevgov._json(raw.cached(SOURCE, area_url(e)).path)["data"]


def wards(e: Election, lga: str) -> list[tuple[str, dict]]:
    """The portal id and record of each ward in a local government area."""
    for entry in lgas(e):
        if irev2023._code(entry["lga"]["code"]) == lga:
            return [(w["_id"], w) for w in entry["wards"]]
    raise KeyError(f"no local government area {e.state}/{lga}")


def units(e: Election, lga: str) -> list[irev2023.Unit]:
    found: list[irev2023.Unit] = []
    for id, ward in wards(e, lga):
        for row in irevgov._json(raw.cached(SOURCE, ward_url(e, id)).path)["data"]:
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
    return sum(len(units(e, irev2023._code(entry["lga"]["code"]))) for entry in lgas(e))


def fetch() -> None:
    raw.fetch(SOURCE, API, suffix=".json")
    raw.fetch(irev2023.SHAPES, irev2023.LGAS, suffix=".geojson")
    for e in ELECTIONS:
        raw.fetch(SOURCE, area_url(e), suffix=".json")
        raw.fetch(irev2023.GRID3, irev2023.WARDS.format(e.grid3), suffix=".geojson")
        for entry in lgas(e):
            for w in entry["wards"]:
                raw.fetch(SOURCE, ward_url(e, w["_id"]), suffix=".json")
        documents = [u.document for lga in e.read for u in units(e, lga) if u.document]
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda url: irevgov.render(irev2023.scan(url, fetching=True)), documents))


def slug(e: Election) -> str:
    return changeset.slug(e.name.replace("/", " "))


def key(e: Election) -> str:
    return f"{SOURCE}/{e.day}-{slug(e)}"


def geo_key(e: Election) -> str:
    return f"{SOURCE}/geo-{e.code.lower()}"


def packages() -> list[str]:
    return [p for e in ELECTIONS for p in (geo_key(e), key(e))]


METHOD = (
    "Read by eye from the scans of Form EC8A (II) that presiding officers uploaded to the Commission's result viewing "
    "portal (IReV), one sheet per polling unit, twice and independently by two different multimodal models, with "
    "every disagreement over a stored figure settled by a third look at the sheet by a third model. Registered "
    "voters are row 1 of the sheet, voters are the accredited voters of row 2, invalid are the rejected ballots of "
    "row 6, valid are the total valid votes under the party table (row 7 where that is blank), and each option's "
    "votes are the figures written against its party. Ballot papers issued, unused, spoiled, and used (rows 3, 4, "
    "5, and 8) have no measure here. A cell written as nil, zero, or a dash, or left blank, is read as no votes. "
    "The portal names no candidates, so each option stands for its party's candidate. A polling unit whose scan is "
    "missing, unreadable, or not its own EC8A is left out. These are the polling units' own figures, not the "
    "collated declaration."
)
ROWS = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
MEASURES = {"R1": REGISTERED, "R2": VOTERS, "R6": INVALID}


def readings(e: Election, lga: str) -> dict[str, Sheet]:
    """The settled readings of a local government area's sheets, by polling-unit code."""
    fields = [*ROWS, *e.parties, "TOTAL"]
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
            if r["field"] not in fields or r["field"] in cells:
                raise ValueError(f"{code} has an unknown or repeated field {r['field']}")
            if r["value"] not in ("", "?") and not r["value"].isdigit():
                raise ValueError(f"{code} {r['field']} holds {r['value']!r}")
            if r["value"] == "?" and r["field"] in e.parties:
                raise ValueError(f"{code} cannot be stored with its {r['field']} figure unread")
            box = (float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))
            if not all(0 <= v <= 1 for v in box) or box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError(f"{code} {r['field']} has a box outside the page")
            cells[r["field"]] = Cell(int(r["value"]) if r["value"].isdigit() else None, box)
        if status[0]["value"] == "ok" and sorted(cells) != sorted(fields):
            raise ValueError(f"{code} does not hold every field")
        if status[0]["value"] != "ok" and cells:
            raise ValueError(f"{code} is {status[0]['value']} but holds figures")
        found[code] = Sheet(code, status[0]["value"], cells)
    return found


def source(p: Package, e: Election) -> str:
    id = p.id("source", "irev", e.day, e.district)
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title=f"INEC Result Viewing Portal (IReV): {e.day.year} {e.name} federal constituency bye-election, Forms "
        "EC8A (II) by polling unit",
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
        f"The local government areas and registration areas (wards) of {e.name} federal constituency, with outlines "
        "from geoBoundaries and GRID3",
        depends=[f"{SOURCE}/geo"],
    )
    state = irev2023.state_area(e.code)
    p.external |= {NIGERIA, state}
    # The shape sources get ids of their own: the reference the loader checks against carries no source rows, so
    # the depended-on geo package's sources cannot be cited from here.
    lga_source = p.id("source", irev2023.SHAPES, "adm2", e.code)
    ward_source = p.id("source", irev2023.GRID3, "wards-v4", e.code)
    lga_file = raw.cached(irev2023.SHAPES, irev2023.LGAS)
    ward_file = raw.cached(irev2023.GRID3, irev2023.WARDS.format(e.grid3))
    state_name = irev2023.title(lgas(e)[0]["lga"].get("state_name") or e.name.split("/")[0])
    p.add(
        "source",
        id=lga_source,
        kind="academic",
        publisher="geoBoundaries",
        title=f"geoBoundaries gbOpen Nigeria ADM2 (local government areas, simplified; from GRID3), for {state_name} "
        "State",
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
        title=f"GRID3 NGA - Operational Wards v4.0, {state_name} State",
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
        f"The {e.day:%-d %B %Y} {e.name} federal constituency bye-election to the House of Representatives, by "
        "polling unit, read from the EC8A (II) sheets on the Commission's result viewing portal",
        depends=[geo_key(e), f"{SOURCE}/geo"],
    )
    tag = f"reps:{e.district}:{e.day}"
    body = inec.HOUSE
    district = inec2022.area("ng.federal", e.district)
    parties: dict[str, str] = {}
    for party in e.parties:
        if party in NEW_PARTIES:
            parties[party] = p.id("party", party)
            p.add("party", id=parties[party], kind="party", polity_id=NIGERIA)
            p.add("external_id", entity_id=parties[party], scheme="ng.irev.party", value=party)
            p.name(parties[party], party, "en", "abbreviation")
        else:
            parties[party] = inec2022.party(party)
            p.external.add(parties[party])
    p.external |= {NIGERIA, body, district}
    source_id = source(p, e)
    for entry in lgas(e):
        for w in entry["wards"]:
            p.uses(raw.cached(SOURCE, ward_url(e, w["_id"])))
    election = p.id("election", tag)
    path = f"ng/{e.day}-{slug(e)}-bye-election"
    p.add("election", id=election, polity_id=NIGERIA, kind="by_election", date_from=e.day, date_to=e.day, status="held")
    p.name(election, f"{e.day.year} {e.name} federal constituency bye-election", "en", "official")
    p.add("slug", path=path, entity_id=election, is_current=True)
    # The registry's systems are not in the reference the loader checks against, so the contest gets its own, with the
    # registry's rule for single-winner seats.
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
        kind="body_seats",
        office_id=None,
        body_id=body,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, f"{e.day.year} House of Representatives bye-election, {e.name}", "en", "official")
    p.add(
        "slug",
        path=f"{path}/house-of-representatives/{slug(e)}",
        entity_id=contest,
        is_current=True,
    )
    options = {}
    for position, party in enumerate(e.parties, 1):
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
        source_id=source_id,
        status="active",
        is_default=True,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    stored = 0
    accounting: list[str] = []
    turnout: list[str] = []
    left: list[str] = []
    for lga in sorted(e.read):
        read = readings(e, lga)
        portal = units(e, lga)
        unknown = set(read) - {u.code for u in portal}
        if unknown:
            raise ValueError(f"readings for polling units the portal does not list: {sorted(unknown)}")
        for unit in portal:
            sheet = read.get(unit.code)
            if unit.document is None or sheet is None or sheet.status != "ok":
                left.append(unit.code)
                continue
            stored += 1
            artifact = irev2023.scan(unit.document)
            artifact_id = p.artifact(artifact, source_id)
            width, height = irevgov.render(artifact)
            page = p.id("page", artifact.sha256)
            p.add(
                "source_page",
                id=page,
                artifact_id=artifact_id,
                page_number=1,
                image_key=irevgov.image_key(artifact),
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

            def put(option: str | None, measure: int, field: str, number: int, sheet: Sheet = sheet) -> None:
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
                    "value_locator",
                    **fact,
                    count_round=0,
                    page_id=page,
                    source_id=source_id,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )

            votes = {party: sheet.cells[party].value or 0 for party in e.parties}
            for party, number in votes.items():
                put(options[party], VOTES, party, number)
            for field, measure in MEASURES.items():
                number = sheet.cells[field].value
                if number is not None:
                    put(None, measure, field, number)
            registered, voters = sheet.cells["R1"].value, sheet.cells["R2"].value
            if registered is not None and voters is not None and voters > registered:
                turnout.append(unit.code)
            total = irevgov.valid(sheet)
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
            f"The portal lists {listed(e):,} polling units for this election; {stored:,} are stored, and "
            f"{len(left)} are left out because their scan is missing, unreadable, or not their own sheet"
            + (f" ({', '.join(left)})." if left else ".")
        ),
        lang="en",
    )
    if accounting:
        p.accept(
            "accounting.votes_valid",
            f"At {len(accounting)} polling units the parties' votes written on the sheet do not add up to the total "
            "valid votes written on the same sheet; the figures are stored as written, and a caveat on the result "
            "set names the units.",
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
    if turnout:
        p.accept(
            "accounting.turnout",
            f"At {len(turnout)} polling units ({', '.join(turnout)}) the sheet gives more accredited voters than "
            "voters on the register, as written by the presiding officer; the figures are stored as written.",
        )
    return p


def parse() -> None:
    for e in ELECTIONS:
        geo(e).write()
        results(e).write()


def emit() -> None:
    package.run("load", packages())
