"""South Africa's first democratic elections, 26 to 29 April 1994: the National Assembly and the nine provincial
legislatures, by province.

The Electoral Commission of South Africa (IEC) publishes the results as a four-page PDF: a national table of each
party's valid votes in each of the nine 1994 provinces, with the seats the National Assembly allotted, and one table
of votes and seats per provincial legislature. The province is the finest unit the document reports. Every figure
keeps its page and bounding box.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.za import iec

SOURCE = "za.iec1994"
URL = "https://www.elections.org.za/content/uploadedfiles/NPE%201994.pdf"
SOUTH_AFRICA = iec.SOUTH_AFRICA
DAY = date(1994, 4, 27)
FIRST = date(1994, 4, 26)
LAST = date(1994, 4, 29)
UNTIL = date(1997, 2, 4)
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
KEY = f"{SOURCE}/{DAY}"
VOTES, REGISTERED, VALID, SPOILT = 1, 2, 4, 5
DIGITIZED = 8
SCHEME = "za.province"

# The 1994 provinces in the order the national table prints them: code, name, successor province code.
PROVINCES = [
    ("EC", "Eastern Cape", "EC"),
    ("ET", "Eastern Transvaal", "MP"),
    ("KN", "KwaZulu-Natal", "KZN"),
    ("NC", "Northern Cape", "NC"),
    ("NT", "Northern Transvaal", "LP"),
    ("NW", "North West", "NW"),
    ("OF", "Orange Free State", "FS"),
    ("PW", "PWV", "GP"),
    ("WC", "Western Cape", "WC"),
]
SECTIONS = {
    "EASTERN CAPE": "EC",
    "EASTERN TRANSVAAL": "ET",
    "KWAZULU-NATAL": "KN",
    "NORTHERN CAPE": "NC",
    "NORTHERN TRANSVAAL": "NT",
    "NORTH WEST": "NW",
    "ORANGE FREE STATE": "OF",
    "PWV": "PW",
    "WESTERN CAPE": "WC",
}
NATIONAL_ROWS = 20  # nineteen parties and the total row
PROVINCIAL_SEATS = {"EC": 56, "ET": 30, "KN": 81, "NC": 30, "NT": 40, "NW": 30, "OF": 30, "PW": 86, "WC": 42}

# The national table prints abbreviations. Where the provincial tables spell a party out, the abbreviation takes that
# name; where they do not, the party keeps the abbreviation as its name.
ABBREVIATIONS = {
    "PAC": "Pan Africanist Congress of Azania",
    "VV-FF": "Vryheidsfront/Freedom Front",
    "WRPP": "Women's Rights Peace Party",
    "XPP": "Ximoko Progressive Party",
    "AMP": "Africa Muslim Party",
    "ACDP": "African Christian Democratic Party",
    "ADM": "African Democratic Movement",
    "ANC": "African National Congress",
    "DP": "Democratic Party",
    "DPSA": "Dikwankwetla Party of South Africa",
    "FP": "Federal Party",
    "LUSAP": "Luso South African Party",
    "MF": "Minority Front",
    "NP": "National Party",
    "IFP": "Inkatha Freedom Party",
}
# Spellings in the provincial tables that name a party another table spells differently.
ALIASES = {
    "Workers' lnrernational to Rebuild the Fourth International (SA)": "Workers' International to Rebuild the Fourth International (SA)",
    "Worker's International to Rebuild the Fourth International (SA)": "Workers' International to Rebuild the Fourth International (SA)",
}

METHOD = (
    "Declared by the Electoral Commission of South Africa (IEC) in a four-page summary of the 1994 National and "
    "Provincial Elections, which the IEC notes it did not run: a temporary electoral commission did, so this "
    "summary is the only 1994 data the IEC holds. The province is the finest unit it reports, and no registration "
    "or spoilt-ballot figures are published."
)
NATIONAL_NOTE = (
    "The National Assembly's 400 seats were filled from national and regional lists on one ballot. The IEC's table "
    "prints each party's valid votes in each province, the party's total, and the seats it allocated; it prints no "
    "national or regional split of the seats. Province names are those of 1994: Eastern Transvaal is now "
    "Mpumalanga, Northern Transvaal is Limpopo, Orange Free State is the Free State, and PWV is Gauteng."
)
PROVINCIAL_NOTE = (
    "The IEC prints the votes each party won on the provincial ballot and the seats it was allotted, with the "
    "legislature's total number of seats. It prints no valid-vote total for the province, so none is stored. "
    "The 1994 province was not the province of today: its borders were redrawn when the 1996 constitution took "
    "effect, so the legislature is recorded under its successor body."
)
GEO_NOTE = (
    "The nine provinces created by the 1993 interim constitution, as the IEC names them for the 1994 elections. "
    "They were replaced in 1997 by the provinces of the 1996 constitution, which differ in name for four of them "
    "and in borders for several."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return key("area", code)


def party(name: str) -> str:
    return key("party", name)


def unit(code: str) -> str:
    return key("unit", code)


def packages() -> list[str]:
    return [GEO, REGISTRY, KEY]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    for build in (geo, registry, election):
        build().write()


def emit() -> None:
    package.run("load", packages())


@dataclass(frozen=True)
class Cell:
    value: int
    page: int
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float


@dataclass
class National:
    votes: dict[str, dict[str, Cell]]  # abbreviation -> province code -> cell
    totals: dict[str, Cell]  # abbreviation -> total valid votes
    seats: dict[str, Cell]
    valid: dict[str, Cell]  # province code -> total valid votes
    valid_total: Cell
    seats_total: Cell


@dataclass
class Provincial:
    votes: dict[str, Cell]  # printed party name -> cell
    seats: dict[str, Cell]
    total: Cell


def _words(path: Path) -> list[tuple[int, pymupdf.Rect, str]]:
    found: list[tuple[int, pymupdf.Rect, str]] = []
    with pymupdf.open(path) as document:
        for page in document:
            matrix = page.rotation_matrix
            for x0, y0, x1, y1, text, *_ in page.get_text("words"):
                found.append((page.number + 1, pymupdf.Rect(x0, y0, x1, y1) * matrix, text))
    return found


def _pages(path: Path) -> dict[int, Page]:
    with pymupdf.open(path) as document:
        return {page.number + 1: Page(page.number + 1, page.rect.width, page.rect.height) for page in document}


def _rows(words: list[tuple[int, pymupdf.Rect, str]], number: int) -> list[list[tuple[pymupdf.Rect, str]]]:
    on_page = sorted(((r, t) for n, r, t in words if n == number), key=lambda item: (item[0].y0, item[0].x0))
    rows: list[list[tuple[pymupdf.Rect, str]]] = []
    for rect, text in on_page:
        middle = (rect.y0 + rect.y1) / 2
        if rows and abs(middle - (rows[-1][0][0].y0 + rows[-1][0][0].y1) / 2) < 3:
            rows[-1].append((rect, text))
        else:
            rows.append([(rect, text)])
    return [sorted(row, key=lambda item: item[0].x0) for row in rows]


def _number(text: str) -> int | None:
    return int(text.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", text) else None


def _cell(page: Page, rect: pymupdf.Rect, text: str) -> Cell:
    value = _number(text)
    if value is None:
        raise ValueError(f"{text!r} is not a count")
    box = (rect.x0 / page.width, rect.y0 / page.height, rect.x1 / page.width, rect.y1 / page.height)
    return Cell(value, page.number, box)


def read_national(path: Path) -> National:
    words = _words(path)
    pages = _pages(path)
    rows = _rows(words, 1)
    start = next(i for i, row in enumerate(rows) if row[0][1] == "Party")
    table = [row for row in rows[start:] if _number(row[-1][1]) is not None and not row[0][1].startswith("Page")]
    if len(table) != NATIONAL_ROWS:
        raise ValueError(f"the national table has {len(table)} rows, not {NATIONAL_ROWS}")
    national = National({}, {}, {}, {}, Cell(0, 1, (0, 0, 0, 0)), Cell(0, 1, (0, 0, 0, 0)))
    for row in table:
        label = row[0][1]
        cells = [_cell(pages[1], rect, text) for rect, text in row[1:] if _number(text) is not None]
        if len(cells) not in (10, 11):
            raise ValueError(f"{label}: {len(cells)} figures")
        if label == "TOTAL":
            for (code, _, _), cell in zip(PROVINCES, cells[:9], strict=True):
                national.valid[code] = cell
            national.valid_total, national.seats_total = cells[9], cells[10]
            continue
        national.votes[label] = {code: cell for (code, _, _), cell in zip(PROVINCES, cells[:9], strict=True)}
        national.totals[label] = cells[9]
        if len(cells) == 11:
            national.seats[label] = cells[10]
    return national


def read_provincial(path: Path) -> dict[str, Provincial]:
    words = _words(path)
    pages = _pages(path)
    found: dict[str, Provincial] = {}
    for number in (2, 3, 4):
        current: tuple[str, float, float] | None = None
        votes: dict[str, Cell] = {}
        seats: dict[str, Cell] = {}
        for row in _rows(words, number):
            text = " ".join(t for _, t in row)
            if text.endswith(" VOTES SEATS"):
                votes_x = next((r.x0 + r.x1) / 2 for r, t in row if t == "VOTES")
                seats_x = next((r.x0 + r.x1) / 2 for r, t in row if t == "SEATS")
                current = (SECTIONS[text.removesuffix(" VOTES SEATS")], votes_x, seats_x)
                votes, seats = {}, {}
                continue
            if current is None or text.startswith(("Page", "1994 National", "Provincial Results")):
                continue
            code, votes_x, seats_x = current
            label = " ".join(t for r, t in row if _number(t) is None or r.x0 < votes_x - 60)
            figures = [(r, t) for r, t in row if _number(t) is not None and r.x0 >= votes_x - 60]
            if label == "TOTAL":
                ((rect, figure),) = figures
                found[code] = Provincial(votes, seats, _cell(pages[number], rect, figure))
                continue
            for rect, figure in figures:
                middle = (rect.x0 + rect.x1) / 2
                target = votes if abs(middle - votes_x) < abs(middle - seats_x) else seats
                target[ALIASES.get(label, label)] = _cell(pages[number], rect, figure)
    if set(found) != set(SECTIONS.values()):
        raise ValueError(f"provincial tables found for {sorted(found)}")
    return found


def parties(national: National, provincial: dict[str, Provincial]) -> dict[str, str]:
    """Every party's name, by the abbreviation or printed name the tables use for it."""
    names = {abbreviation: ABBREVIATIONS.get(abbreviation, abbreviation) for abbreviation in national.votes}
    for table in provincial.values():
        for label in table.votes:
            names.setdefault(label, label)
    return names


def verify(national: National, provincial: dict[str, Provincial]) -> None:
    """Fail when the digitized figures disagree with the totals the document prints."""
    for code, _, _ in PROVINCES:
        column = sum(cells[code].value for cells in national.votes.values())
        if column != national.valid[code].value:
            raise ValueError(f"{code}: parties sum to {column}, the table prints {national.valid[code].value}")
    for label, cells in national.votes.items():
        row = sum(cell.value for cell in cells.values())
        if row != national.totals[label].value:
            raise ValueError(f"{label}: provinces sum to {row}, the table prints {national.totals[label].value}")
    if sum(cell.value for cell in national.totals.values()) != national.valid_total.value:
        raise ValueError("party totals do not sum to the printed total")
    if sum(cell.value for cell in national.valid.values()) != national.valid_total.value:
        raise ValueError("province totals do not sum to the printed total")
    if sum(cell.value for cell in national.seats.values()) != national.seats_total.value:
        raise ValueError("seats do not sum to the printed total")
    if national.seats_total.value != 400:
        raise ValueError("the National Assembly has 400 seats")
    for code, table in provincial.items():
        if sum(cell.value for cell in table.seats.values()) != table.total.value:
            raise ValueError(f"{code}: seats do not sum to the printed total")
        if table.total.value != PROVINCIAL_SEATS[code]:
            raise ValueError(f"{code}: {table.total.value} seats")
    if sum(table.total.value for table in provincial.values()) != 425:
        raise ValueError("the provincial legislatures have 425 seats")


def _artifact() -> raw.Artifact:
    return raw.cached(SOURCE, URL)


def read() -> tuple[National, dict[str, Provincial]]:
    path = _artifact().path
    national, provincial = read_national(path), read_provincial(path)
    verify(national, provincial)
    return national, provincial


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "South Africa's nine provinces of 1994, as the IEC names them in its 1994 results",
        depends=["za.iec/geo"],
    )
    p.external.add(SOUTH_AFRICA)
    for code, name, _ in PROVINCES:
        id = area(code)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"1994:{code}", valid_from=DAY, valid_to=UNTIL)
        p.name(id, name, "en", "official")
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=GEO_NOTE, lang="en")
    return p


def registry() -> Package:
    national, provincial = read()
    p = Package(
        REGISTRY,
        SOURCE,
        "The parties on the IEC's summary of the 1994 National and Provincial Elections",
    )
    p.external.add(SOUTH_AFRICA)
    p.add(
        "source",
        id=key("source"),
        kind="official_body",
        publisher="Electoral Commission of South Africa",
        title="1994 National and Provincial Elections",
        url=URL,
        license="IEC copyright notice: results may be reproduced with the IEC acknowledged as the source",
        license_url="https://www.elections.org.za/pw/Copyright-Notice",
        attribution="Electoral Commission of South Africa (IEC)",
    )
    p.artifact(_artifact(), key("source"))
    for name in sorted(set(parties(national, provincial).values())):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=SOUTH_AFRICA)
        p.add("external_id", entity_id=id, scheme="za.iec1994.party", value=name.upper())
        p.name(id, name, "en", "official")
    for abbreviation, name in ABBREVIATIONS.items():
        p.name(party(name), abbreviation, "en", "abbreviation", False)
    return p


def election() -> Package:
    national, provincial = read()
    names = parties(national, provincial)
    artifact = _artifact()
    source = key("source")
    p = Package(
        KEY,
        SOURCE,
        "South Africa's 1994 National Assembly and provincial legislature elections by province from the IEC",
        depends=[GEO, REGISTRY, "za.iec/geo", "za.iec/registry"],
    )
    p.external.add(SOUTH_AFRICA)
    pages = _pages(artifact.path)
    p.uses(artifact)
    artifact_id = str(
        uuid5(package.NAMESPACE, f"source_artifact\x1f{source}\x1f{artifact.storage_key}")
    )  # the registry holds the row
    page_ids = {number: p.id("page", number) for number in pages}
    for number, page in pages.items():
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{number}.png",
            width=round(page.width * 100 / 72),
            height=round(page.height * 100 / 72),
        )
    election_id = key("election", DAY)
    p.add(
        "election", id=election_id, polity_id=SOUTH_AFRICA, kind="general", date_from=FIRST, date_to=LAST, status="held"
    )
    p.name(election_id, "1994 South African general election", "en", "official")
    p.add("slug", path=f"za/{DAY}-general", entity_id=election_id, is_current=True)
    for code, name, _ in PROVINCES:
        p.add(
            "reporting_unit",
            id=unit(code),
            polity_id=SOUTH_AFRICA,
            scheme_code=SCHEME,
            code=f"{DAY}:{code}",
            area_relation="equals",
            area_id=area(code),
            kind="standard",
        )
        p.name(unit(code), name, "en", "official")
    values: list[tuple[str, str, str | None, int, Cell]] = []
    declared: list[tuple[str, str | None, int, int]] = []

    def option(contest: str, label: str, name: str) -> str:
        id = key("option", contest, label)
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="list",
            party_id=party(name),
            ballot_position=None,
            ballot_label=label,
        )
        return id

    contest = key("contest", DAY, "national")
    result_set = iec._contest(
        p,
        contest,
        election_id,
        "list_allocation",
        "National Assembly",
        f"za/{DAY}-general/national-assembly",
        body=iec.NA,
        district_area=None,
        magnitude=400,
        system=iec.SYSTEM_NA,
        method=METHOD,
    )
    p.add(
        "note",
        id=key("note", result_set, "caveat"),
        entity_id=result_set,
        kind="caveat",
        body_md=NATIONAL_NOTE,
        lang="en",
    )
    options = {label: option(contest, label, names[label]) for label in national.votes}
    for label, cells in national.votes.items():
        for code, cell in cells.items():
            values.append((result_set, unit(code), options[label], VOTES, cell))
        declared.append((result_set, options[label], VOTES, national.totals[label].value))
        if label in national.seats:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[label],
                kind="won",
                seats=national.seats[label].value,
            )
    for code, cell in national.valid.items():
        values.append((result_set, unit(code), None, VALID, cell))
    declared.append((result_set, None, VALID, national.valid_total.value))
    for code, name, successor in PROVINCES:
        table = provincial[code]
        contest = key("contest", DAY, "provincial", code)
        english = f"{name} Provincial Legislature"
        result_set = iec._contest(
            p,
            contest,
            election_id,
            "list_allocation",
            english,
            f"za/{DAY}-general/{iec.changeset.slug(name)}-provincial-legislature",
            body=iec.legislature(successor),
            district_area=area(code),
            magnitude=table.total.value,
            system=iec.SYSTEM_PROVINCE,
            method=METHOD,
        )
        p.add(
            "note",
            id=key("note", result_set, "caveat"),
            entity_id=result_set,
            kind="caveat",
            body_md=PROVINCIAL_NOTE,
            lang="en",
        )
        for label, cell in table.votes.items():
            id = option(contest, label, names[label])
            values.append((result_set, unit(code), id, VOTES, cell))
            if label in table.seats:
                p.add("outcome", result_set_id=result_set, option_id=id, kind="won", seats=table.seats[label].value)
        for label in table.seats:
            if label not in table.votes:
                raise ValueError(f"{code}: {label} has seats and no votes")
    frame = pl.DataFrame(
        [(rs, u, o, m, c.value, page_ids[c.page], *c.box) for rs, u, o, m, c in values],
        schema=["result_set_id", "unit_id", "option_id", "measure", "value", "page_id", "x0", "y0", "x1", "y1"],
        orient="row",
    ).with_columns(
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    )
    p.frame(
        "result_value",
        frame.select(
            "result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "value"
        ).with_columns(flags=pl.lit(DIGITIZED, pl.Int16)),
    )
    p.frame(
        "value_locator",
        frame.select(
            "result_set_id",
            "unit_id",
            "option_id",
            "channel",
            "measure",
            "count_round",
            "page_id",
            "x0",
            "y0",
            "x1",
            "y1",
        ).with_columns(source_id=pl.lit(None, pl.String)),
    )
    for rs, o, m, v in declared:
        p.add("declared", result_set_id=rs, option_id=o, measure=m, value=v)
    return p
