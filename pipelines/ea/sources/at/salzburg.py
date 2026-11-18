"""Salzburg Landtag elections, 1945 to 2023, by municipality from the Land Salzburg statistics office.

The office publishes one CSV per election with a row for the state, each political district, each municipality,
and, in some years, the ballots it does not assign to a municipality. Its list of parties names each ballot position.
"""

import csv
import functools
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.salzburg"
BASE = "https://www.salzburg.gv.at/stat/wahlen/ltw"
DOWNLOADS = f"{BASE}/download.html"
PARTIES = f"{BASE}/download/LTW-Parteiliste.xlsx"
HISTORY = f"{BASE}/LTW-hist.js"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
STATE = "50000"
MEASURES = ((2, "registered"), (3, "cast"), (5, "invalid"), (4, "valid"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
LANDTAG = key("body", "landtag")
LAND = key("area", "at-5.land", "5")


def csv_url(year: int) -> str:
    return f"{BASE}/download/LTW-{year}.csv"


@functools.cache
def dates() -> dict[int, date]:
    """Each election's date, as the download page lists it beside its results file."""
    page = raw.cached(SOURCE, DOWNLOADS).path.read_text("utf-8-sig")
    found = re.findall(r"(\d\d)\.(\d\d)\.(\d{4})(?=.*?download/LTW-(\d{4})\.csv)", page, flags=re.DOTALL)
    out: dict[int, date] = {}
    for day, month, year, file in found:
        if year == file and int(year) not in out:
            out[int(year)] = date(int(year), int(month), int(day))
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/lt-{year}" for year in dates())]


def fetch() -> None:
    raw.fetch(SOURCE, DOWNLOADS, suffix=".html")
    raw.fetch(SOURCE, PARTIES, suffix=".xlsx")
    raw.fetch(SOURCE, HISTORY, suffix=".js")
    for year in dates():
        raw.fetch(SOURCE, csv_url(year), suffix=".csv")


def parse() -> None:
    geo().write()
    registry().write()
    for year in dates():
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def count(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


@dataclass
class Row:
    code: str
    name: str
    registered: int | None
    cast: int | None
    invalid: int | None
    valid: int | None
    votes: dict[int, int | None]
    seats: dict[int, int | None]
    total_seats: int | None
    assigned: int | None


@functools.cache
def table(year: int) -> list[Row]:
    """The rows of one election's results file, keyed by the office's region code."""
    data = raw.cached(SOURCE, csv_url(year)).path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    head = next(reader)
    at = {name: index for index, name in enumerate(head)}
    positions = sorted(int(n[7:]) for n in head if re.fullmatch(r"Stimmen\d+", n))
    rows = []
    for line in reader:
        if not line or not line[0].strip():
            continue

        def cell(name: str, line: list[str] = line) -> int | None:
            return count(line[at[name]]) if name in at and at[name] < len(line) else None

        rows.append(
            Row(
                line[0].strip(),
                " ".join(line[1].split()),
                cell("Wahlberechtigte gesamt"),
                cell("abgegebene Stimmen"),
                cell("ungültige Stimmen"),
                cell("gültige Stimmen"),
                {p: cell(f"Stimmen{p}") for p in positions},
                {p: cell(f"Mandate{p}") for p in positions},
                cell("Mandate gesamt"),
                cell("Mandate zugewiesen"),
            )
        )
    return rows


def state(year: int) -> Row:
    return next(r for r in table(year) if r.code == STATE)


def seats(year: int) -> tuple[dict[int, int], bool]:
    """The seats of each list from the state row, and whether the file prints them one column to the left.

    In the files of the early elections the first list's seats sit under "Mandate zugewiesen" and the second list's
    under "Mandate1", so the reading is the one whose seats add up to the total the same row prints."""
    top = state(year)
    plain_read = {p: n for p, n in top.seats.items() if n}
    shifted = {p + 1: n for p, n in top.seats.items() if n}
    if top.assigned:
        shifted[1] = top.assigned
    if sum(plain_read.values()) == top.total_seats:
        return plain_read, False
    assert sum(shifted.values()) == top.total_seats, f"{year}: seats do not add up to the total in either reading"
    return dict(sorted(shifted.items())), True


def is_special(row: Row) -> bool:
    """Ballots the office does not assign to a municipality: one row per district, and one for the whole state."""
    return row.code.endswith("99")


def is_municipality(row: Row) -> bool:
    return not row.code.endswith("00") and not is_special(row)


def plain(name: str) -> str:
    """A name without the note the early files add about the municipality's earlier territory."""
    return re.sub(r"\s+-\s+bis\s.*$", "", name).strip()


@dataclass
class Entry:
    year: int
    position: int
    short: str
    long: str
    group: str


@functools.cache
def entries() -> dict[int, list[Entry]]:
    """Each election's ballot positions from the office's list of parties, whose year cell is filled once a block."""
    book = fastexcel.read_excel(raw.cached(SOURCE, PARTIES).path)
    sheet = book.load_sheet(0, header_row=None, dtypes="string").to_polars()
    found: dict[int, list[Entry]] = {}
    year = 0
    for row in sheet.iter_rows():
        cells = ["" if c is None else " ".join(c.split()) for c in row]
        if re.fullmatch(r"\d{4}", cells[0]):
            year = int(cells[0])
        if year and re.fullmatch(r"\d+", cells[1]):
            found.setdefault(year, []).append(Entry(year, int(cells[1]), cells[2], cells[3], cells[4]))
    return found


GROUPS = {"ÖVP", "SPÖ", "FPÖ", "GRÜNE", "KPÖ", "NEOS"}


def party_key(entry: Entry) -> str:
    """The office's own grouping for the lists it follows across years, else the list's short name."""
    return entry.group if entry.group in GROUPS else entry.short


def party(entry: Entry) -> str:
    return key("party", party_key(entry))


@functools.cache
def places() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Each district's name, and each municipality's names, newest first."""
    districts: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    for year in sorted(dates(), reverse=True):
        for row in table(year):
            if row.code.endswith("00") and row.code != STATE:
                districts.setdefault(row.code[:3], row.name)
            elif is_municipality(row):
                found = names.setdefault(row.code, [])
                if plain(row.name) not in found:
                    found.append(plain(row.name))
    return districts, names


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Salzburg's political districts and municipalities as the state statistics office codes them in its Landtag "
        "results",
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, noun in (
        ("at-5.land", 1, "state"),
        ("at-5.bezirk", 2, "political district"),
        ("at-5.gemeinde", 3, "municipality"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add("area", id=LAND, scheme_code="at-5.land")
    p.add("area_code", area_id=LAND, code="5")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Salzburg", "de", "official")
    districts, names = places()
    for code, text in sorted(districts.items()):
        id = key("area", "at-5.bezirk", code)
        p.add("area", id=id, scheme_code="at-5.bezirk")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=LAND)
        p.name(id, text, "de", "official")
    for code, found in sorted(names.items()):
        id = key("area", "at-5.gemeinde", code)
        p.add("area", id=id, scheme_code="at-5.gemeinde")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-5.bezirk", code[:3]))
        for index, text in enumerate(found):
            p.name(id, text, "de", "official", index == 0)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Salzburg Landtag and the lists on its ballots, as the state statistics office prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Land Salzburg",
        title="Land Salzburg - Landesstatistik: Landtagswahlen",
        url=f"{BASE}/index.html",
        attribution="Land Salzburg",
    )
    for artifact in (DOWNLOADS, PARTIES, HISTORY, *(csv_url(y) for y in dates())):
        p.artifact(raw.cached(SOURCE, artifact), SOURCE_ID)
    p.add("body", id=LANDTAG, polity_id=AUSTRIA, kind="unicameral")
    p.name(LANDTAG, "Salzburg Landtag", "en", "official")
    p.name(LANDTAG, "Salzburger Landtag", "de", "native", False)
    named: dict[str, set[str]] = {}
    for year in sorted(entries(), reverse=True):
        for entry in entries()[year]:
            id = party(entry)
            if id not in named:
                p.add("party", id=id, kind="party", polity_id=AUSTRIA)
                p.add("external_id", entity_id=id, scheme="at.salzburg.party", value=party_key(entry))
            if entry.long not in named.setdefault(id, set()):
                named[id].add(entry.long)
                p.name(id, entry.long, "de", "official", len(named[id]) == 1)
    return p


@functools.cache
def history() -> dict[int, dict[str, int | None]]:
    """The state's registered, cast, invalid, and valid counts by election from the office's historical series."""
    text = raw.cached(SOURCE, HISTORY).path.read_text("utf-8-sig")
    data = json.loads(text)
    row = next(r for r in data["stimmen"] if r["region_id"] == 5)
    out = {}
    for stamp, figures in zip(data["datum"], row["daten"], strict=True):
        year = int(stamp[-4:])
        out[year] = dict(zip(("registered", "cast", "invalid", "valid"), figures[:4], strict=True))
    return out


METHOD = (
    "Results by municipality (Gemeinde) from the Land Salzburg statistics office's results file for the election, "
    "with the state totals and seats from the file's state row. Where the file prints ballots it does not assign to "
    "a municipality (postal ballots counted by district or for the whole state), they are stored as aggregate units "
    "for that district or the state and are not spread over municipalities."
)


def election(year: int) -> Package:
    day = dates()[year]
    rows = table(year)
    top = state(year)
    listed = entries()[year]
    p = Package(
        f"{SOURCE}/lt-{year}",
        SOURCE,
        f"The {day} Salzburg Landtag election by municipality from the state statistics office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, csv_url(year)))
    p.uses(raw.cached(SOURCE, PARTIES))
    p.uses(raw.cached(SOURCE, HISTORY))
    id = key("election", year)
    p.add("election", id=id, polity_id=AUSTRIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Salzburg Landtag election", "en", "official")
    p.name(id, f"Landtagswahl {year}", "de", "native", False)
    path = f"at/{day}-salzburg-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=LANDTAG,
        district_area_id=LAND,
        magnitude=top.total_seats,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Salzburg Landtag", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug('Landtag')}", entity_id=contest, is_current=True)
    result_set = key("result_set", year)
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
    options: dict[int, str] = {}
    for entry in listed:
        option = options[entry.position] = key("option", year, entry.position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(entry),
            ballot_position=entry.position,
            ballot_label=entry.long,
        )
    assert set(options) == {n for n, v in top.votes.items() if v is not None}, (
        f"{year}: the party list and the results file disagree on positions"
    )
    won, moved = seats(year)
    if moved:
        p.add(
            "note",
            id=key("note", result_set, "seats"),
            entity_id=result_set,
            kind="caveat",
            body_md="The file prints each list's seats one column to the left of its header, starting with the first "
            "list under the column for the seats assigned in total. The seats are read that way because only that "
            "reading adds up to the total number of seats the same row gives.",
            lang="en",
        )
    for measure, field_name in MEASURES:
        value = getattr(top, field_name)
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, option in options.items():
        votes = top.votes[position]
        if votes is not None:
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        if won.get(position):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won[position])
    units = [r for r in rows if is_municipality(r) or is_special(r)]
    for field_name in ("registered", "cast", "invalid", "valid"):
        total = sum(getattr(r, field_name) or 0 for r in units)
        declared = getattr(top, field_name)
        if declared is not None and total != declared:
            p.accept(
                "accounting.declared",
                f"the rows add up to {total:,} {field_name} against {declared:,} in the state row of the file",
            )
    series = history().get(year)
    if series:
        for field_name, value in series.items():
            if value is not None and value != getattr(top, field_name):
                p.accept(
                    "accounting.declared",
                    f"the office's historical series gives {value:,} {field_name} for the state against "
                    f"{getattr(top, field_name):,} in the results file",
                )
    older = [r for r in units if is_municipality(r) and plain(r.name) != r.name]
    if older:
        p.add(
            "note",
            id=key("note", result_set, "territory"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The file marks {len(older)} municipalities with the date from which each was a municipality "
            "of its own. Their rows are stored under today's municipality.",
            lang="en",
        )
    over = [r for r in units if r.registered and r.cast and r.cast > r.registered]
    if over:
        named = ", ".join(f"{plain(r.name)} ({r.cast:,} cast, {r.registered:,} registered)" for r in over)
        p.accept("accounting.turnout", f"the file prints more ballots cast than registered voters for {named}")
    for row in units:
        special = is_special(row)
        if special:
            scheme, relation, area, kind = (
                "at-5.land" if row.code == "50099" else "at-5.bezirk",
                "equals",
                LAND if row.code == "50099" else key("area", "at-5.bezirk", row.code[:3]),
                "aggregate",
            )
        else:
            scheme, relation, area, kind = "at-5.gemeinde", "equals", key("area", "at-5.gemeinde", row.code), "standard"
        unit = key("unit", year, row.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code=scheme,
            code=f"{day}:{row.code}",
            area_relation=relation,
            area_id=area,
            kind=kind,
        )
        label = f"{row.name} (ballots not assigned to a municipality)" if special else plain(row.name)
        p.name(unit, label, "de", "official")
        for measure, field_name in MEASURES:
            value = getattr(row, field_name)
            if value is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for position, option in options.items():
            votes = row.votes.get(position)
            if votes is None and (row.valid or 0) > 0:
                votes = 0
            if votes is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    return p
