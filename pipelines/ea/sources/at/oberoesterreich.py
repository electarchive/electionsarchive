"""Upper Austria Landtag elections, 1997 to 2021, by municipality, from the state's statistics office.

Each election has a download archive with a workbook of the state result: one row for the state, one for each of the
five electoral districts (Wahlkreise), one for each of the 18 political districts (Bezirke, the three statutory cities
among them), and one for each municipality. A second sheet holds the seats of each list. The authority's downloads
begin with 1997.
"""

import functools
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import xlrd

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.oberoesterreich"
BASE = "https://www.land-oberoesterreich.gv.at"
OVERVIEW = f"{BASE}/42981.htm"
FILES = f"{BASE}/Mediendateien/Formulare/Dokumente%20PraesD%20Abt_TI"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))


@dataclass(frozen=True)
class Edition:
    """One Landtag election: the archive that holds its results and the workbook inside it."""

    day: date
    archive: str
    member: str

    @property
    def year(self) -> int:
        return self.day.year

    @property
    def key(self) -> str:
        return f"lt-{self.year}"

    @property
    def url(self) -> str:
        return f"{FILES}/{self.archive}"


EDITIONS = [
    Edition(date(1997, 10, 5), "lt97.zip", "stat_download_lw97.xlw"),
    Edition(date(2003, 9, 28), "lt03.zip", "stat_download_lw03.xlw"),
    Edition(date(2009, 9, 27), "lt09.zip", "stat_download_lw09.xls"),
    Edition(date(2015, 9, 27), "lt15.zip", "stat_download_lw15.xls"),
    Edition(date(2021, 9, 26), "lw21.zip", "stat_download_lw21.xls"),
]

# The electoral districts, by the political districts that make them up.
WAHLKREISE = {
    "4A": ("401", "410"),
    "4B": ("404", "412", "414"),
    "4C": ("403", "405", "408", "417", "418"),
    "4D": ("402", "407", "409", "415"),
    "4E": ("406", "411", "413", "416"),
}
WAHLKREIS_OF = {bezirk: wahlkreis for wahlkreis, bezirke in WAHLKREISE.items() for bezirk in bezirke}
CITIES = ("40100", "40200", "40300")
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
LANDTAG = key("body", "landtag")
LAND = key("area", "at-4.land", "4")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    raw.fetch(SOURCE, OVERVIEW, suffix=".html")
    for edition in EDITIONS:
        raw.fetch(SOURCE, edition.url, suffix=".zip")


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


@dataclass
class Row:
    code: str
    label: str
    registered: int
    cast: int
    valid: int
    invalid: int
    votes: list[int]


@dataclass
class Table:
    state: Row
    parties: list[str]
    seats: dict[str, int]
    magnitude: int
    wahlkreise: dict[str, Row]
    bezirke: dict[str, Row]
    municipalities: dict[str, Row]


def count(value: object) -> int:
    if value == "":
        return 0
    assert isinstance(value, float) and value == int(value), value
    return int(value)


def ident(value: object) -> str:
    return str(int(value)) if isinstance(value, float) else str(value).strip()


def name(label: str) -> str:
    return " ".join(label.split()).removeprefix("Wahlkreis ").removeprefix("Bezirk ").removeprefix("Stadt ")


@functools.cache
def table(edition: Edition) -> Table:
    """Read the workbook and sort its rows into the state, electoral districts, political districts, and municipalities."""
    with zipfile.ZipFile(raw.cached(SOURCE, edition.url).path) as archive, open(os.devnull, "w") as quiet:
        book = xlrd.open_workbook(file_contents=archive.read(edition.member), logfile=quiet)
    sheet = book.sheet_by_name("Stimmen")
    head = next(at for at in range(6) if sheet.cell_value(at, 0) == "Nr.")
    assert sheet.row_values(head)[2:9:2][:1] == ["Wahlbe-"] and sheet.cell_value(head, 7) == "gültige", head
    columns: dict[str, int] = {}
    for at in range(9, sheet.ncols, 2):
        text = str(sheet.cell_value(head, at)).strip()
        if text == "erste Eingabe":
            break
        if text:
            columns[text] = at
        else:
            assert all(sheet.cell_value(row, at) in ("", 0.0) for row in range(head + 2, sheet.nrows)), (
                edition.key,
                at,
            )
    parties = list(columns)
    rows: list[Row] = []
    for index in range(head + 2, sheet.nrows):
        cells = sheet.row_values(index)
        if cells[0] == "":
            continue
        rows.append(
            Row(
                ident(cells[0]),
                name(str(cells[1])) if ident(cells[0]) != "40000" else "Upper Austria",
                count(cells[2]),
                count(cells[3]),
                count(cells[7]),
                count(cells[5]),
                [count(cells[columns[party]]) for party in parties],
            )
        )
        assert rows[-1].cast == rows[-1].valid + rows[-1].invalid, (edition.key, rows[-1].code)
    state = rows[0]
    assert state.code == "40000", state
    wahlkreise: dict[str, Row] = {}
    bezirke: dict[str, Row] = {}
    municipalities: dict[str, Row] = {}
    for row in rows[1:]:
        if re.fullmatch(r"4[A-E]000", row.code):
            wahlkreise[row.code[:2]] = row
        elif row.code.endswith("00"):
            bezirke[row.code[:3]] = row
        else:
            assert row.code not in municipalities, row
            municipalities[row.code] = row
    for code in CITIES:
        city = f"{code[:3]}01"
        if city not in municipalities:
            municipalities[city] = bezirke[code[:3]]
    for code, row in bezirke.items():
        parts = [part for at, part in municipalities.items() if at.startswith(code) and part is not row]
        parts = parts or [row]
        assert sum(part.valid for part in parts) == row.valid, (edition.key, code)
        assert sum(part.registered for part in parts) == row.registered, (edition.key, code)
    for code, row in wahlkreise.items():
        parts = [bezirke[bezirk] for bezirk in WAHLKREISE[code]]
        assert sum(part.valid for part in parts) == row.valid, (edition.key, code)
        assert sum(part.registered for part in parts) == row.registered, (edition.key, code)
        assert sum(part.votes[0] for part in parts) == row.votes[0], (edition.key, code)
    mandates = book.sheet_by_name("Mandate")
    top = next(at for at in range(6) if mandates.cell_value(at, 0) == "Nr.")
    names = [str(text).strip() for text in mandates.row_values(top)]
    land = next(
        mandates.row_values(at) for at in range(top, mandates.nrows) if ident(mandates.cell_value(at, 0)) == "40000"
    )
    seats = {party: count(land[names.index(party)]) for party in parties}
    magnitude = count(land[5])
    assert sum(seats.values()) == magnitude, (edition.key, seats, magnitude)
    return Table(state, parties, seats, magnitude, wahlkreise, bezirke, municipalities)


def places() -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]], dict[str, str]]:
    """Each electoral district's, political district's, and municipality's names, newest first, and each municipality's district."""
    wahlkreise: dict[str, list[str]] = {}
    bezirke: dict[str, list[str]] = {}
    municipalities: dict[str, list[str]] = {}
    district_of: dict[str, str] = {}
    for edition in sorted(EDITIONS, key=lambda e: e.year, reverse=True):
        found = table(edition)
        for names, rows in ((wahlkreise, found.wahlkreise), (bezirke, found.bezirke)):
            for code, row in rows.items():
                text = names.setdefault(code, [])
                if row.label not in text:
                    text.append(row.label)
        for code, row in found.municipalities.items():
            text = municipalities.setdefault(code, [])
            label = "Linz" if code == "40101" else row.label
            if label not in text:
                text.append(label)
            district_of.setdefault(code, code[:3])
    return wahlkreise, bezirke, municipalities, district_of


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Upper Austria's electoral districts, political districts, and municipalities as the state's statistics office codes them",
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, kind, noun in (
        ("at-4.land", 1, "administrative", "state"),
        ("at-4.wahlkreis", 2, "electoral_district", "electoral district"),
        ("at-4.bezirk", 3, "administrative", "district"),
        ("at-4.gemeinde", 4, "administrative", "municipality"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=LAND, scheme_code="at-4.land")
    p.add("area_code", area_id=LAND, code="4")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Upper Austria", "en", "official")
    p.name(LAND, "Oberösterreich", "de", "official", False)
    wahlkreise, bezirke, municipalities, district_of = places()
    for code, names in sorted(wahlkreise.items()):
        id = key("area", "at-4.wahlkreis", code)
        p.add("area", id=id, scheme_code="at-4.wahlkreis")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=LAND)
        for index, text in enumerate(names):
            p.name(id, text, "de", "official", index == 0)
    for code, names in sorted(bezirke.items()):
        id = key("area", "at-4.bezirk", code)
        p.add("area", id=id, scheme_code="at-4.bezirk")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-4.wahlkreis", WAHLKREIS_OF[code]))
        for index, text in enumerate(names):
            p.name(id, text, "de", "official", index == 0)
    for code, names in sorted(municipalities.items()):
        id = key("area", "at-4.gemeinde", code)
        p.add("area", id=id, scheme_code="at-4.gemeinde")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-4.bezirk", district_of[code]))
        for index, text in enumerate(names):
            p.name(id, text, "de", "official", index == 0)
    return p


def party(label: str) -> str:
    return key("party", label)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Upper Austria Landtag and the lists on its ballots, as the state's statistics office prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Land Oberösterreich",
        title="Land Oberösterreich - Wahlen: Historische Wahlergebnisse",
        url=OVERVIEW,
        attribution="Land Oberösterreich",
    )
    p.artifact(raw.cached(SOURCE, OVERVIEW), SOURCE_ID)
    for edition in EDITIONS:
        p.artifact(raw.cached(SOURCE, edition.url), SOURCE_ID)
    p.add("body", id=LANDTAG, polity_id=AUSTRIA, kind="unicameral")
    p.name(LANDTAG, "Upper Austria Landtag", "en", "official")
    p.name(LANDTAG, "Oberösterreichischer Landtag", "de", "native", False)
    seen: set[str] = set()
    for edition in EDITIONS:
        for label in table(edition).parties:
            if label in seen:
                continue
            seen.add(label)
            p.add("party", id=party(label), kind="party", polity_id=AUSTRIA)
            p.add("external_id", entity_id=party(label), scheme="at.oberoesterreich.party", value=label)
            p.name(party(label), label, "de", "official")
    return p


METHOD = (
    "Results by municipality (Gemeinde) from the state statistics office's workbook of the Landtag result, with the "
    "seats from its second sheet. The statutory cities Linz, Steyr, and Wels are one municipality each. The state "
    "totals are the workbook's state row, and the municipalities add up to them."
)


def election(edition: Edition) -> Package:
    found = table(edition)
    state = found.state
    day, year = edition.day, edition.year
    p = Package(
        f"{SOURCE}/{edition.key}",
        SOURCE,
        f"The {day} Upper Austria Landtag election by municipality from the state's statistics office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, edition.url))
    id = key("election", edition.key)
    p.add("election", id=id, polity_id=AUSTRIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Upper Austria Landtag election", "en", "official")
    p.name(id, f"Landtagswahl {year}", "de", "native", False)
    path = f"at/{day}-oberoesterreich-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", edition.key)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=LANDTAG,
        district_area_id=LAND,
        magnitude=found.magnitude,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Upper Austria Landtag", "en", "official")
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
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
    figures = {"registered": state.registered, "cast": state.cast, "valid": state.valid, "invalid": state.invalid}
    for field, measure in (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=figures[field])
    for label, votes in zip(found.parties, state.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=votes)
        if found.seats[label]:
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=found.seats[label])
    rows = sorted(found.municipalities.items())
    for field in ("registered", "cast", "valid", "invalid"):
        total = sum(getattr(row, field) for _, row in rows)
        assert total == figures[field], (edition.key, field, total, figures[field])
    for at, label in enumerate(found.parties):
        total = sum(row.votes[at] for _, row in rows)
        assert total == state.votes[at], (edition.key, label, total, state.votes[at])
    for code, row in rows:
        unit = key("unit", edition.key, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code="at-4.gemeinde",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=key("area", "at-4.gemeinde", code),
            kind="standard",
            parent_id=None,
        )
        p.name(unit, "Linz" if code == "40101" else row.label, "de", "official")
        for measure, field in MEASURES:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=getattr(row, field),
            )
        for label, votes in zip(found.parties, row.votes, strict=True):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[label],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    over = [row for _, row in rows if row.cast > row.registered]
    if over:
        listed = "; ".join(f"{row.label} ({row.cast:,} ballots, {row.registered:,} registered)" for row in over)
        p.accept(
            "accounting.turnout",
            f"the workbook prints more ballots cast than registered voters for {len(over)} units, as printed: {listed}",
        )
    return p
