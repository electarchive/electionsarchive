"""Lagos State's local government elections of 12 July 2025, by ward, from the State Independent Electoral Commission.

The Commission publishes one workbook for each of the 20 local governments and 37 local council development areas
(LCDAs), also bundled in `results.zip`. Each workbook has a `WARD SUMMARY` sheet that lists, ward by ward, the party,
candidate, votes scored, and gender of the councillor declared elected; the sheet holds no losing candidates and no
accounting figures. One workbook, Ifelodun, also has a `SUMMARY CHAIRMANSHIP` sheet with the chairmanship and vice
chairmanship results of that LCDA, runner-up included. The wards and LCDAs have no published boundaries, so only the 20
local governments are drawn, from geoBoundaries.
"""

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import cast
from uuid import uuid5

import shapely
import shapely.geometry
import xlrd

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ng.lasiec"
SHAPES = "ng.geoboundaries"
NIGERIA = str(uuid5(package.NAMESPACE, "polity\x1fNG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNGA"))
LAGOS = str(uuid5(package.NAMESPACE, "ng.inec\x1farea\x1fng.state\x1fLA"))
DAY = date(2025, 7, 12)
PAGE = "https://lasiec.gov.ng/2025_election_results/"
UPLOADS = "https://lasiec.gov.ng/wp-content/uploads/2025/07/"
ARCHIVE = f"{UPLOADS}results.zip"
# The media library holds 226 files on four pages of 100; the results workbooks are all on the first.
PAGES = 4
MEDIA = "https://lasiec.gov.ng/wp-json/wp/v2/media?per_page=100&page={}"
OUTLINES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/NGA/ADM2/"
    "geoBoundaries-NGA-ADM2_simplified.geojson"
)
# geoBoundaries' name for each local government whose plain name differs from the Commission's.
OUTLINE_NAMES = {"ifakoijaiye": "ifakoijaye", "somolu": "shomolu"}
LAGOS_BOX = (2.6, 6.2, 4.5, 6.95)
# Oto Awori's sheet is headed "LGA", but it is a local council development area of Ojo, and not one of Lagos State's
# 20 local governments.
KINDS = {"Oto Awori": "LCDA"}
PARTIES = {"APC": "All Progressives Congress", "PDP": "Peoples Democratic Party"}
GENDERS = {"M": "male", "MALE": "male", "F": "female", "FEMALE": "female"}
METHOD = (
    "Declared by the Lagos State Independent Electoral Commission in its statement of result of poll, one row for each "
    "ward. The sheet lists the candidate declared elected with the votes scored; it lists no other candidate and prints "
    "no registered voters, ballots cast, or valid votes, so each ward's result set holds the winner alone and the "
    "votes are not checked against a ward total. Where the printed row lacks the votes, the name, or the gender, the "
    "figure is left out."
)
CHAIRMAN_METHOD = (
    "Declared by the Lagos State Independent Electoral Commission in its statement of result of poll for the "
    "chairmanship and vice chairmanship, with the votes scored by each ticket. No registered voters, ballots cast, or "
    "valid votes are printed."
)
SYSTEM = {"formula": "fptp"}
SYSTEM_NOTE = (
    "One member or officeholder for each ward or council, elected by the most votes. Lagos State's local councils "
    "elect a chairman and vice chairman on one ticket and a councillor for each ward."
)
WARD = re.compile(r"^\s*WARD\s*[:\-]?\s*[‘'’\"`]*\s*([A-Z]{1,2})\s*[’'\"`]*\s*[:\-]?\s*(.*?)\s*$", re.IGNORECASE)
AREA = re.compile(r"AREA:\s*(.+?)\s+(LGA|LCDA)\s*$", re.IGNORECASE)
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class Row:
    party: str
    name: str | None
    votes: int | None
    gender: str | None
    winner: bool = False


@dataclass
class Ward:
    letter: str
    name: str | None
    rows: list[Row] = field(default_factory=list)


@dataclass
class Ticket:
    party: str
    chairman: str | None
    deputy: str | None
    votes: int | None
    chairman_gender: str | None
    deputy_gender: str | None
    winner: bool


@dataclass
class Council:
    name: str
    kind: str
    wards: list[Ward]
    tickets: list[Ticket]
    files: list[str]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def title(text: str) -> str:
    words = re.sub(r"\s+", " ", text.strip()).title()
    return re.sub(r"\b(Ii|Iii)\b", lambda m: m.group(1).upper(), words)


def listing() -> list[str]:
    urls: list[str] = []
    for page in range(1, PAGES + 1):
        found = json.loads(raw.cached(SOURCE, MEDIA.format(page)).path.read_text(encoding="utf-8"))
        urls += [
            item["source_url"]
            for item in found
            if item["source_url"].startswith(UPLOADS) and item["source_url"].lower().endswith(".xls")
        ]
    return sorted(urls)


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for page in range(1, PAGES + 1):
        raw.fetch(SOURCE, MEDIA.format(page), suffix=".json")
    raw.fetch(SOURCE, ARCHIVE, suffix=".zip")
    for url in listing():
        raw.fetch(SOURCE, url, suffix=".xls")
    raw.fetch(SHAPES, OUTLINES, suffix=".geojson")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def _number(value: object) -> int | None:
    if isinstance(value, float):
        return int(value)
    match = re.match(r"^\s*([0-9][0-9,]*)(?:\.0+)?\b", str(value))
    return int(match.group(1).replace(",", "")) if match else None


def _gender(value: object) -> str | None:
    text = _text(value).upper()
    # A stray letter in the column (Surulere's "A") names no gender.
    return GENDERS.get(text)


def _sheet(book: xlrd.Book, word: str) -> xlrd.sheet.Sheet | None:
    found = [sheet for sheet in book.sheets() if word in sheet.name.upper() and "WARD D" != sheet.name.upper()]
    return found[0] if found else None


def _wards(sheet: xlrd.sheet.Sheet) -> list[Ward]:
    wards: list[Ward] = []
    fresh = False
    columns: tuple[int, int, int, int] | None = None
    for r in range(sheet.nrows):
        cells = [_text(value) for value in sheet.row_values(r)]
        head = next((cell for cell in cells if cell.upper().startswith("WARD")), None)
        if head is not None and "S/N" not in " ".join(cells):
            match = WARD.match(head)
            if match is None:
                raise ValueError(f"{sheet.name} row {r}: ward header {head!r}")
            wards.append(Ward(match.group(1).upper(), match.group(2) or None))
            fresh = True
            continue
        party = next((i for i, cell in enumerate(cells) if re.match(r"^NAME OF\s+POL", cell.upper())), None)
        if party is None and any(cell.upper() in ("GENDER", "S/N") for cell in cells):
            party = next((i for i, cell in enumerate(cells) if "PARTY" in cell.upper()), None)
        if party is not None:
            columns = (party, party + 1, party + 2, party + 3)
            if not fresh:
                last = wards[-1].letter
                wards.append(Ward(LETTERS[LETTERS.index(last) + 1], None))
            fresh = False
            continue
        if columns is None or not wards:
            continue
        p, n, v, g = columns
        if not cells[p] and not cells[n]:
            continue
        marked = "WINNER" in cells[n].upper()
        name = re.sub(r"\(?\s*WINNER\s*\)?", "", cells[n], flags=re.IGNORECASE).strip() or None
        votes = _number(sheet.row_values(r)[v]) if cells[v] else None
        wards[-1].rows.append(Row(cells[p].upper(), name, votes, _gender(cells[g]) if g < len(cells) else None, marked))
    return wards


def _tickets(sheet: xlrd.sheet.Sheet) -> list[Ticket]:
    chairmen: list[Row] = []
    deputies: list[Row] = []
    into = chairmen
    for r in range(sheet.nrows):
        cells = [_text(value) for value in sheet.row_values(r)]
        text = " ".join(cells).upper()
        if "VICE CHAIRMANSHIP" in text:
            into = deputies
        found = [i for i, cell in enumerate(cells) if re.search(r"\(([A-Z]+)\)\s*$", cell)]
        if not found or not isinstance(sheet.row_values(r)[found[0] - 1], float):
            continue
        i = found[0]
        votes = next((_number(sheet.row_values(r)[j]) for j in range(i + 2, len(cells)) if cells[j]), None)
        # The winner's votes print as "20907 WINNER".
        winner = any("WINNER" in cell.upper() for cell in cells[i + 1 :])
        party = cast("re.Match[str]", re.search(r"\(([A-Z]+)\)\s*$", cells[i])).group(1)
        gender = next((_gender(cell) for cell in reversed(cells) if cell.upper() in GENDERS), None)
        into.append(Row(party, cells[i + 1] or None, votes, gender, winner))
    if len(chairmen) != len(deputies):
        raise ValueError("chairmanship and vice chairmanship tables differ in length")
    tickets: list[Ticket] = []
    for first, second in zip(chairmen, deputies, strict=True):
        if first.party != second.party or first.votes != second.votes:
            raise ValueError(f"chairman and vice chairman rows differ: {first} {second}")
        tickets.append(
            Ticket(first.party, first.name, second.name, first.votes, first.gender, second.gender, first.winner)
        )
    return tickets


def _book(content: bytes) -> Council:
    book = xlrd.open_workbook(file_contents=content)
    sheet = _sheet(book, "WARD")
    if sheet is None:
        raise ValueError("no ward sheet")
    head = next(
        match
        for r in range(sheet.nrows)
        for cell in sheet.row_values(r)
        if (match := AREA.search(_text(cell).replace("\n", " "))) is not None
    )
    chair = _sheet(book, "CHAIRMANSHIP")
    return Council(
        title(head.group(1)),
        KINDS.get(title(head.group(1)), head.group(2).upper()),
        _wards(sheet),
        _tickets(chair) if chair is not None else [],
        [],
    )


def councils() -> list[Council]:
    """Every council once, read from its own workbook, with the bundle's copy checked against it."""
    found: dict[str, Council] = {}
    for url in listing():
        council = _book(raw.cached(SOURCE, url).path.read_bytes())
        council.files.append(url)
        known = found.get(council.name)
        if known is None:
            found[council.name] = council
        elif (known.wards, known.tickets) == (council.wards, council.tickets):
            known.files.append(url)
        else:
            raise ValueError(f"{url} differs from {known.files[0]}")
    bundle = zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE).path)
    for name in bundle.namelist():
        if not name.lower().endswith(".xls"):
            continue
        council = _book(bundle.read(name))
        known = found.get(council.name)
        if known is None or (known.wards, known.tickets) != (council.wards, council.tickets):
            raise ValueError(f"results.zip: {name} differs from the workbook of {council.name}")
    return sorted(found.values(), key=lambda council: (council.kind, council.name))


def winner(ward: Ward, council: Council) -> Row:
    rows = ward.rows
    marked = [row for row in rows if row.winner]
    if len(marked) == 1:
        return marked[0]
    named = [row for row in rows if row.name or row.votes is not None]
    if len(named) == 1:
        return named[0]
    raise ValueError(f"{council.name} ward {ward.letter}: no clear winner in {rows}")


def ward_code(council: Council, ward: Ward) -> str:
    return f"{plain(council.name)}/{ward.letter}"


def ward_name(council: Council, ward: Ward) -> str:
    place = title(ward.name) if ward.name else f"Ward {ward.letter}"
    return f"{place} ({council.name})"


def party(acronym: str) -> str:
    return key("party", acronym)


def lga_area(council: Council) -> str:
    return key("area", "ng.lagos_lga", plain(council.name))


def council_area(council: Council) -> str:
    return lga_area(council) if council.kind == "LGA" else key("area", "ng.lagos_lcda", plain(council.name))


def ward_area(council: Council, ward: Ward) -> str:
    return key("area", "ng.lagos_ward", ward_code(council, ward))


def council_noun(council: Council) -> str:
    return "Local Government" if council.kind == "LGA" else "LCDA"


def outlines(found: list[Council]) -> dict[str, shapely.MultiPolygon]:
    """Each local government's outline by its geoBoundaries name; Surulere also names a district of Oyo State."""
    wanted = {outline_name(c): c for c in found if c.kind == "LGA"}
    features = json.loads(raw.cached(SHAPES, OUTLINES).path.read_text(encoding="utf-8"))["features"]
    west, south, east, north = LAGOS_BOX
    shapes: dict[str, shapely.MultiPolygon] = {}
    for feature in features:
        name = plain(feature["properties"]["shapeName"])
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        at = shape.representative_point()
        if name not in wanted or not (west <= at.x <= east and south <= at.y <= north):
            continue
        if name in shapes:
            raise ValueError(f"two outlines for {name} inside Lagos State")
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        shapes[name] = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    if set(shapes) != set(wanted):
        raise ValueError(f"no outline for {sorted(set(wanted) - set(shapes))}")
    return shapes


def outline_name(council: Council) -> str:
    return OUTLINE_NAMES.get(plain(council.name), plain(council.name))


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", SHAPES)


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Lagos State Independent Electoral Commission",
        title="Results of the 2025 local government elections",
        url=PAGE,
        license="No license stated; published by the electoral authority for public use",
        attribution="Lagos State Independent Electoral Commission",
    )


def _artifacts(found: list[Council]) -> list[raw.Artifact]:
    names = (PAGE, ARCHIVE, *(url for council in found for url in council.files))
    return list({artifact.storage_key: artifact for artifact in (raw.cached(SOURCE, name) for name in names)}.values())


def _cite(p: Package, found: list[Council], own: bool = False) -> None:
    """The geo package owns the source and its artifacts; the others only list the files they use."""
    for artifact in _artifacts(found):
        if own:
            p.artifact(artifact, SOURCE_ID)
        else:
            p.uses(artifact)


def geo(found: list[Council]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Lagos State's 20 local governments with outlines from geoBoundaries, and the 37 local council development "
        "areas and 376 wards of the Commission's 2025 results, without boundaries",
    )
    p.external |= {NIGERIA, COUNTRY, LAGOS}
    _source(p)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Nigeria ADM2 (local government areas, simplified)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab)",
    )
    _cite(p, found, own=True)
    shapes = raw.cached(SHAPES, OUTLINES)
    p.artifact(shapes, SHAPES_ID)
    p.add(
        "boundary_set",
        id=key("boundary_set", "ng.lagos_lga", "geoboundaries"),
        scheme_code="ng.lagos_lga",
        vintage="geoBoundaries gbOpen",
        as_of=shapes.retrieved_at.date(),
    )
    for code, rank, kind, noun in (
        ("ng.lagos_lga", 2, "administrative", "local government"),
        ("ng.lagos_lcda", 2, "administrative", "local council development area"),
        ("ng.lagos_ward", 3, "electoral_district", "ward"),
    ):
        p.add("scheme", code=code, polity_id=NIGERIA, kind=kind, rank=rank, noun=noun, code_system=None)

    def place(scheme: str, id: str, code: str, text: str, parent: str) -> None:
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "en", "official")

    shaped = outlines(found)
    for council in found:
        scheme = "ng.lagos_lga" if council.kind == "LGA" else "ng.lagos_lcda"
        id = council_area(council)
        place(scheme, id, plain(council.name), f"{council.name} {council_noun(council)}", LAGOS)
        if council.kind == "LGA":
            shape = shaped[outline_name(council)]
            p.add(
                "area_geometry",
                area_id=id,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="academic",
                source_id=SHAPES_ID,
            )
        for ward in council.wards:
            place("ng.lagos_ward", ward_area(council, ward), ward_code(council, ward), ward_name(council, ward), id)
    return p


def registry(found: list[Council]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Lagos State's local councils, their chairmanships, the parties of the 2025 local elections, and the "
        "plurality system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NIGERIA, LAGOS}
    _cite(p, found)
    for council in found:
        body = key("body", plain(council.name))
        p.add("body", id=body, polity_id=NIGERIA, kind="council")
        p.name(body, f"{council.name} {council_noun(council)} Council", "en", "official")
        office = key("office", "chairman", plain(council.name))
        p.add("office", id=office, polity_id=NIGERIA, kind="other", body_id=body)
        p.name(office, f"Chairman of {council.name} {council_noun(council)}", "en", "official")
    p.add(
        "electoral_system",
        id=key("electoral_system", "fptp"),
        polity_id=NIGERIA,
        valid_from=date(1999, 1, 1),
        valid_to=None,
        rules=SYSTEM,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", "fptp"),
        entity_id=key("electoral_system", "fptp"),
        kind="methodology",
        body_md=SYSTEM_NOTE,
        lang="en",
    )
    used = {row.party for council in found for ward in council.wards for row in ward.rows}
    used |= {ticket.party for council in found for ticket in council.tickets}
    for acronym in sorted(used):
        id = party(acronym)
        p.add("party", id=id, kind="party", polity_id=NIGERIA)
        p.add("external_id", entity_id=id, scheme="ng.lasiec.party", value=acronym)
        if acronym in PARTIES:
            p.name(id, PARTIES[acronym], "en", "official")
        p.name(id, acronym, "en", "abbreviation", False)
    return p


def election(found: list[Council]) -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 12 July 2025 Lagos State local government elections: the councillor declared elected in each ward of the "
        "20 local governments and 37 LCDAs, and the chairmanship of Ifelodun LCDA",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NIGERIA}
    _cite(p, found)
    system = key("electoral_system", "fptp")
    id = key("election", DAY)
    path = f"ng/{DAY}-local"
    p.add("election", id=id, polity_id=NIGERIA, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(id, "2025 Lagos State local government elections", "en", "official")
    p.add("slug", path=path, entity_id=id, is_current=True)

    def contest(
        tag: str, name: str, slug: str, kind: str, district: str, body: str | None, office: str | None, method: str
    ) -> tuple[str, str]:
        contest_id = key("contest", tag)
        p.add(
            "contest",
            id=contest_id,
            election_id=id,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest_id, name, "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest_id, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        return contest_id, result_set

    def person(tag: str, name: str | None, gender: str | None) -> str:
        person_id = key("person", tag)
        p.add("person", id=person_id, birth_precision=None, gender=gender)
        if name:
            p.name(person_id, title(name), "en", "official")
        return person_id

    for council in found:
        slug = changeset.slug(council.name)
        body = key("body", plain(council.name))
        for ward in council.wards:
            if not ward.rows:
                continue
            row = winner(ward, council)
            code = ward_code(council, ward)
            tag = f"councillor:{code}"
            name = ward_name(council, ward)
            contest_id, result_set = contest(
                tag,
                f"2025 {council.name} {council_noun(council)} councillorship election, {name.rsplit(' (', 1)[0]} ward",
                f"councillor/{slug}/{ward.letter.lower()}-{changeset.slug(ward.name or 'ward-' + ward.letter)}",
                "body_seats",
                ward_area(council, ward),
                body,
                None,
                METHOD,
            )
            unit = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NIGERIA,
                scheme_code="ng.lagos_ward",
                code=code,
                area_relation="equals",
                area_id=ward_area(council, ward),
                kind="standard",
            )
            option = key("option", tag, row.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=party(row.party),
                ballot_label=row.name,
            )
            candidacy = key("candidacy", tag, row.party)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person(tag, row.name, row.gender),
                option_id=option,
                role="head",
                ballot_name=row.name,
                member_of_party_id=party(row.party),
                is_independent=False,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=1)
            if row.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=row.votes,
                )
        if not council.tickets:
            continue
        tag = f"chairman:{plain(council.name)}"
        contest_id, result_set = contest(
            tag,
            f"2025 {council.name} {council_noun(council)} chairmanship election",
            f"chairman/{slug}",
            "office",
            council_area(council),
            None,
            key("office", "chairman", plain(council.name)),
            CHAIRMAN_METHOD,
        )
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NIGERIA,
            scheme_code="ng.lagos_lcda",
            code=plain(council.name),
            area_relation="equals",
            area_id=council_area(council),
            kind="standard",
        )
        for number, ticket in enumerate(council.tickets, 1):
            option = key("option", tag, ticket.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="ticket",
                party_id=party(ticket.party),
                ballot_position=number,
                ballot_label=f"{ticket.party}: {ticket.chairman} and {ticket.deputy}",
            )
            head = key("candidacy", tag, ticket.party, "chairman")
            p.add(
                "candidacy",
                id=head,
                person_id=person(f"{tag}:{ticket.party}:chairman", ticket.chairman, ticket.chairman_gender),
                option_id=option,
                role="head",
                ballot_name=ticket.chairman,
                member_of_party_id=party(ticket.party),
                is_independent=False,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag, ticket.party, "deputy"),
                person_id=person(f"{tag}:{ticket.party}:deputy", ticket.deputy, ticket.deputy_gender),
                option_id=option,
                role="running_mate",
                ballot_name=ticket.deputy,
                member_of_party_id=party(ticket.party),
                is_independent=False,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if ticket.winner else "lost",
                seats=1 if ticket.winner else None,
            )
            if ticket.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=ticket.votes,
                )
    return p


def parse() -> None:
    found = councils()
    geo(found).write()
    registry(found).write()
    election(found).write()


def emit() -> None:
    package.run("load", packages())


def check(found: list[Council]) -> dict[str, int]:
    """Counts of what the sheets hold, for the report."""
    return {
        "lgas": sum(c.kind == "LGA" for c in found),
        "lcdas": sum(c.kind == "LCDA" for c in found),
        "wards": sum(len(c.wards) for c in found),
        "votes": sum(winner(w, c).votes or 0 for c in found for w in c.wards if w.rows),
    }
