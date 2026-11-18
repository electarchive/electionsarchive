"""Burgenland Landtag elections, 1996 to 2025, by municipality and village, from the state's election office.

Each election has a workbook of the state result: one row for the state, one for each of the seven electoral
districts (Wahlkreise), one for each municipality, and, under the 59 or 60 municipalities that print them, one for
each village (Ortsteil). Through 2010 the postal ballots (Wahlkarten) are counted apart from the municipalities, in
one row for each electoral district. The seats of each list come from the state summary page. The authority's
election pages begin with 1996.
"""

import functools
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from uuid import uuid5

import xlrd

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.burgenland"
BASE = "https://wahl.bgld.gv.at/wahlen"
OVERVIEW = f"{BASE}/lt.nsf/wahlergebnisse.xsp"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
LAND_FILE = "8F0416BFB09FC990C125756A002F4BA7"


@dataclass(frozen=True)
class Edition:
    """One Landtag election: the workbook that holds its results, and the state summary page with its seats."""

    day: date
    xls: str
    summary: str
    encoding: str

    @property
    def year(self) -> int:
        return self.day.year

    @property
    def key(self) -> str:
        return f"lt-{self.year}"


def old(day: date, name: str) -> Edition:
    """The 1996 to 2005 sites: a download folder and a home page that prints the state summary."""
    nsf = f"{BASE}/lt{day:%Y%m%d}.nsf"
    return Edition(day, f"{nsf}/vwDownloads/{LAND_FILE}/%24FILE/{name}", nsf, "cp1252")


def new(day: date, name: str) -> Edition:
    """The 2010 and later sites: attachments of the election's own database, and a summary page of it."""
    nsf = f"{BASE}/lt{day:%Y%m%d}x.nsf"
    attachment = f"{nsf}/xsp/.ibmmodres/domino/OpenAttachment/wahlen/lt{day:%Y%m%d}.nsf/{LAND_FILE}/%24File/{name}?Open"
    return Edition(day, attachment, f"{nsf}/index.xsp?key=SU_10000", "utf-8")


EDITIONS = [
    old(date(1996, 6, 2), "lt19960602_Land.xls"),
    old(date(2000, 12, 3), "lt20001203_Land.xls"),
    old(date(2005, 10, 9), "lt20051009_Land.xls"),
    new(date(2010, 5, 30), "lt20100530_Land.xls"),
    new(date(2015, 5, 31), "lt20150531_LandV3.xls"),
    new(date(2020, 1, 26), "LT_20200126_Land.xls"),
    new(date(2025, 1, 19), "lt20250119_Land_2001.xls"),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
LANDTAG = key("body", "landtag")
LAND = key("area", "at-1.land", "1")
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))
POSTAL = 3


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    raw.fetch(SOURCE, OVERVIEW, suffix=".html")
    for edition in EDITIONS:
        raw.fetch(SOURCE, edition.xls, suffix=".xls")
        raw.fetch(SOURCE, edition.summary, suffix=".html")


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


@dataclass
class Row:
    gkz: str
    number: str
    label: str
    municipality: str
    village: str
    registered: int | None
    cast: int
    valid: int
    invalid: int
    votes: list[int]


@dataclass
class Table:
    state: Row
    parties: list[str]
    districts: dict[int, Row]
    postal: dict[int, Row]
    municipalities: dict[str, Row]
    villages: dict[str, list[Row]]
    district_of: dict[str, int]


def count(value: object) -> int:
    if value == "":
        return 0
    assert isinstance(value, float) and value == int(value), value
    return int(value)


@functools.cache
def table(edition: Edition) -> Table:
    """Read the workbook and sort its rows into the state, districts, postal ballots, municipalities, and villages."""
    sheet = xlrd.open_workbook(raw.cached(SOURCE, edition.xls).path).sheet_by_index(0)
    header = sheet.row_values(0)
    assert header[:12] == [
        "GKZ",
        "GmdeNr",
        "Bezeichnung",
        "Gemeinde",
        "Ortsteil",
        "Wahlb.",
        "abgeg.",
        "%",
        "ungültig",
        "%",
        "gültig",
        "%",
    ], header[:12]
    parties: list[str] = []
    state: Row | None = None
    districts: dict[int, Row] = {}
    postal: dict[int, Row] = {}
    municipalities: dict[str, Row] = {}
    villages: dict[str, list[Row]] = defaultdict(list)
    district_of: dict[str, int] = {}
    current = 0
    for index in range(1, sheet.nrows):
        cells = sheet.row_values(index)
        label = str(cells[2]).strip()
        if not label or label.startswith(("nichtzuordenbare", "Statutarstadt", "Bezirk ", "Summe Wahlkarten")):
            continue
        if state is None:
            assert label == "Land Burgenland", label
            parties = [str(cells[at]).strip() for at in range(12, len(cells), 5) if str(cells[at]).strip()]
        row = Row(
            str(cells[0]).removesuffix(".0"),
            str(cells[1]).removesuffix(".0"),
            label,
            str(cells[3]).strip(),
            str(cells[4]).strip(),
            None if cells[5] == "" else count(cells[5]),
            count(cells[6]),
            count(cells[10]),
            count(cells[8]),
            [count(cells[at + 1]) for at in range(12, 12 + 5 * len(parties), 5)],
        )
        for at, name in zip(range(12, 12 + 5 * len(parties), 5), parties, strict=True):
            assert cells[at] in ("", name + " ", name) or str(cells[at]).strip() == name, (label, cells[at], name)
        if state is None:
            state = row
        elif label.startswith("Wahlkreis "):
            current = int(label.split()[1].rstrip(":"))
            districts[current] = row
        elif label.startswith("Wahlkarten-WK"):
            if row.cast:
                postal[int(label.removeprefix("Wahlkarten-WK").split(":")[0])] = row
        elif row.village.startswith("OT."):
            villages[row.gkz].append(row)
        else:
            assert row.gkz not in municipalities, row
            municipalities[row.gkz] = row
            district_of[row.gkz] = current
    assert state is not None
    return Table(state, parties, districts, postal, municipalities, dict(villages), district_of)


class Cells(HTMLParser):
    """Collect the text of the cells of each table row of a page, nested tables included."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.open: list[list[str]] = []
        self.cell: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.open.append([])
        elif tag == "td" and self.open:
            self.cell.append([])

    def handle_data(self, data: str) -> None:
        if self.cell:
            self.cell[-1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.cell and self.open:
            self.open[-1].append(" ".join("".join(self.cell.pop()).split()).replace("\xa0", ""))
        elif tag == "tr" and self.open:
            row = self.open.pop()
            if row:
                self.rows.append(row)


@dataclass
class Summary:
    registered: int
    cast: int
    invalid: int
    valid: int
    seats: int
    votes: dict[str, int]
    mandates: dict[str, int]


def number(text: str) -> int:
    return int(text.replace(",", "").replace(".", ""))


@functools.cache
def summary(edition: Edition) -> Summary:
    """The state summary page: the state totals, the 36 seats, and the votes and seats of each list."""
    html = raw.cached(SOURCE, edition.summary).path.read_bytes().decode(edition.encoding, errors="replace")
    parser = Cells()
    parser.feed(html)
    first = {
        cells[0]: cells
        for cells in reversed(parser.rows)
        if len(cells) >= 4 and re.fullmatch(r"[\d,]+", cells[1]) and re.fullmatch(r"[\d.,]*%?", cells[2])
    }
    totals = {}
    for field, label in (("registered", "Wahlber"), ("cast", "Abgegeben"), ("invalid", "Ungültig")):
        found = [cells for name, cells in first.items() if name.startswith(label)]
        assert len(found) == 1, (edition.key, label)
        totals[field] = number(found[0][1])
    valid = first["Gültig"]
    names = table(edition).parties
    votes: dict[str, int] = {}
    mandates: dict[str, int] = {}
    for name in names:
        cells = first[name]
        votes[name] = number(cells[1])
        mandates[name] = int(cells[3]) if re.fullmatch(r"\d+", cells[3]) else 0
    seats = int(valid[3])
    assert sum(mandates.values()) == seats, (edition.key, mandates, seats)
    return Summary(totals["registered"], totals["cast"], totals["invalid"], number(valid[1]), seats, votes, mandates)


def places() -> tuple[dict[int, list[str]], dict[str, list[str]], dict[str, list[str]], dict[str, int], dict[str, str]]:
    """Each district's, municipality's, and village's names (newest first), and where each municipality and village lies."""
    districts: dict[int, list[str]] = {}
    municipalities: dict[str, list[str]] = {}
    villages: dict[str, list[str]] = {}
    district_of: dict[str, int] = {}
    municipality_of: dict[str, str] = {}
    for edition in sorted(EDITIONS, key=lambda e: e.year, reverse=True):
        found = table(edition)
        for number_, row in found.districts.items():
            names = districts.setdefault(number_, [])
            if row.label not in names:
                names.append(row.label)
        for gkz, row in found.municipalities.items():
            names = municipalities.setdefault(gkz, [])
            if row.municipality not in names:
                names.append(row.municipality)
            assert district_of.setdefault(gkz, found.district_of[gkz]) == found.district_of[gkz], (edition.key, gkz)
        for gkz, rows in found.villages.items():
            for row in rows:
                names = villages.setdefault(row.number, [])
                text = row.village.removeprefix("OT.").strip()
                if text not in names:
                    names.append(text)
                assert municipality_of.setdefault(row.number, gkz) == gkz, (edition.key, row.number)
    return districts, municipalities, villages, district_of, municipality_of


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Burgenland's electoral districts, municipalities, and villages as the state's election office codes them",
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, kind, noun in (
        ("at-1.land", 1, "administrative", "state"),
        ("at-1.wahlkreis", 2, "electoral_district", "electoral district"),
        ("at-1.gemeinde", 3, "administrative", "municipality"),
        ("at-1.ortsteil", 4, "administrative", "village"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=LAND, scheme_code="at-1.land")
    p.add("area_code", area_id=LAND, code="1")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Burgenland", "de", "official")
    p.name(LAND, "Burgenland", "en", "official", False)
    districts, municipalities, villages, district_of, municipality_of = places()
    for code, names in sorted(districts.items()):
        id = key("area", "at-1.wahlkreis", code)
        p.add("area", id=id, scheme_code="at-1.wahlkreis")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=LAND)
        for index, text in enumerate(names):
            p.name(id, text, "de", "official", index == 0)
    for code, names in sorted(municipalities.items()):
        id = key("area", "at-1.gemeinde", code)
        p.add("area", id=id, scheme_code="at-1.gemeinde")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-1.wahlkreis", district_of[code]))
        for index, text in enumerate(names):
            p.name(id, text, "de", "official", index == 0)
    for code, names in sorted(villages.items()):
        id = key("area", "at-1.ortsteil", code)
        p.add("area", id=id, scheme_code="at-1.ortsteil")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-1.gemeinde", municipality_of[code]))
        for index, text in enumerate(names):
            p.name(id, text, "de", "official", index == 0)
    return p


def party(label: str) -> str:
    return key("party", label)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Burgenland Landtag and the lists on its ballots, as the state's election office prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Land Burgenland",
        title="Land Burgenland - Wahlen: Landtagswahlen",
        url=OVERVIEW,
        attribution="Land Burgenland",
    )
    p.artifact(raw.cached(SOURCE, OVERVIEW), SOURCE_ID)
    for edition in EDITIONS:
        p.artifact(raw.cached(SOURCE, edition.xls), SOURCE_ID)
        p.artifact(raw.cached(SOURCE, edition.summary), SOURCE_ID)
    p.add("body", id=LANDTAG, polity_id=AUSTRIA, kind="unicameral")
    p.name(LANDTAG, "Burgenland Landtag", "en", "official")
    p.name(LANDTAG, "Burgenländischer Landtag", "de", "native", False)
    seen: set[str] = set()
    for edition in EDITIONS:
        for label in table(edition).parties:
            if label in seen:
                continue
            seen.add(label)
            p.add("party", id=party(label), kind="party", polity_id=AUSTRIA)
            p.add("external_id", entity_id=party(label), scheme="at.burgenland.party", value=label)
            p.name(party(label), label, "de", "official")
    return p


def method(found: Table) -> str:
    text = (
        "Results by municipality (Gemeinde) from the Burgenland election office's workbook of the state result, "
        "with the seats from its state summary page. Where a municipality prints results for its villages "
        "(Ortsteile), the villages are the reporting units and the municipality is their sum."
    )
    if found.postal:
        text += (
            " Postal ballots (Wahlkarten) are counted apart from the municipalities, one unit for each electoral "
            "district, and they are part of the state totals."
        )
    return text


def election(edition: Edition) -> Package:
    found = table(edition)
    state = found.state
    declared = summary(edition)
    day, year = edition.day, edition.year
    p = Package(
        f"{SOURCE}/{edition.key}",
        SOURCE,
        f"The {day} Burgenland Landtag election by municipality from the state's election office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, edition.xls))
    p.uses(raw.cached(SOURCE, edition.summary))
    id = key("election", edition.key)
    p.add("election", id=id, polity_id=AUSTRIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Burgenland Landtag election", "en", "official")
    p.name(id, f"Landtagswahl {year}", "de", "native", False)
    path = f"at/{day}-burgenland-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", edition.key)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=LANDTAG,
        district_area_id=LAND,
        magnitude=declared.seats,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Burgenland Landtag", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug('Landtag')}", entity_id=contest, is_current=True)
    result_set = key("result_set", edition.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method(found), lang="en"
    )
    options = {}
    for position, label in enumerate(found.parties, start=1):
        option = options[label] = key("option", edition.key, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(label),
            ballot_position=position,
            ballot_label=label,
        )
    state_figures = {
        "registered": state.registered,
        "cast": state.cast,
        "valid": state.valid,
        "invalid": state.invalid,
    }
    page_figures = {
        "registered": declared.registered,
        "cast": declared.cast,
        "valid": declared.valid,
        "invalid": declared.invalid,
    }
    assert state_figures == page_figures, (edition.key, state_figures, page_figures)
    for field, measure in (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=state_figures[field])
    for label, votes in zip(found.parties, state.votes, strict=True):
        assert votes == declared.votes[label], (edition.key, label, votes, declared.votes[label])
        p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=votes)
        if declared.mandates[label]:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[label],
                kind="won",
                seats=declared.mandates[label],
            )
    rows: list[tuple[str, Row, str, str, str, int]] = []
    disagree: list[str] = []
    for gkz, row in sorted(found.municipalities.items()):
        if gkz not in found.villages:
            rows.append((gkz, row, "at-1.gemeinde", "standard", row.municipality, 0))
            continue
        parts = found.villages[gkz]
        for field in ("registered", "cast", "valid", "invalid"):
            assert sum(getattr(part, field) or 0 for part in parts) == getattr(row, field), (edition.key, gkz, field)
        if any(sum(part.votes[at] for part in parts) != row.votes[at] for at in range(len(found.parties))):
            rows.append((gkz, row, "at-1.gemeinde", "standard", row.municipality, 0))
            disagree.append(row.municipality)
            continue
        for part in parts:
            rows.append((part.number, part, "at-1.ortsteil", "standard", part.village.removeprefix("OT.").strip(), 0))
    for district, row in sorted(found.postal.items()):
        rows.append(
            (f"WK{district}", row, "at-1.wahlkreis", "aggregate", f"Wahlkarten {row.label.split(': ')[1]}", POSTAL)
        )
    area_of = {"at-1.gemeinde": "at-1.gemeinde", "at-1.ortsteil": "at-1.ortsteil", "at-1.wahlkreis": "at-1.wahlkreis"}
    unlisted: list[str] = []
    for code, row, scheme, kind, name, channel in rows:
        unit = key("unit", edition.key, scheme, code)
        area_code = str(int(code.removeprefix("WK"))) if channel else code
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=key("area", area_of[scheme], area_code),
            kind=kind,
            parent_id=None,
        )
        p.name(unit, name, "de", "official")
        for measure, field in MEASURES:
            value = getattr(row, field)
            if field == "registered" and (channel or (value == 0 and row.cast > 0)):
                if not channel:
                    unlisted.append(name)
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=channel,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for label, votes in zip(found.parties, row.votes, strict=True):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[label],
                channel=channel,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    for field in ("registered", "cast", "valid", "invalid"):
        total = sum(getattr(row, field) or 0 for _, row, *_ in rows)
        assert total == state_figures[field], (edition.key, field, total, state_figures[field])
    for at, label in enumerate(found.parties):
        total = sum(row.votes[at] for _, row, *_ in rows)
        assert total == state.votes[at], (edition.key, label, total, state.votes[at])
    if disagree:
        p.add(
            "note",
            id=key("note", result_set, "villages"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The village rows of {', '.join(disagree)} add up to different list votes than the municipality's "
            "own row, which agrees with the state totals. The municipality's row is used there and its villages are "
            "left out.",
            lang="en",
        )
    if unlisted:
        p.add(
            "note",
            id=key("note", result_set, "registered"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The workbook prints no registered voters (zero) for {len(unlisted)} units that counted "
            "ballots, so their registered voters are left out rather than stored as zero.",
            lang="en",
        )
    over = [(name, row) for _, row, _, _, name, _ in rows if row.registered and row.cast > row.registered]
    if over:
        listed = "; ".join(f"{name} ({row.cast:,} ballots, {row.registered:,} registered)" for name, row in over)
        p.accept(
            "accounting.turnout",
            f"the workbook prints more ballots cast than registered voters for {len(over)} units, as printed: {listed}",
        )
    return p
