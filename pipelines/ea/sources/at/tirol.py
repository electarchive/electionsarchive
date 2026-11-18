import csv
import functools
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.at import landtag

SOURCE = "at.tirol"
BASE = "https://wahlen.tirol.gv.at"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
LAND_POLITY = landtag.polity("AT-7")
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))


@dataclass(frozen=True)
class Edition:
    """One Tyrolean Landtag election, the file that holds its results by municipality, and its state-level page."""

    year: int
    file: str

    @property
    def key(self) -> str:
        return f"lt-{self.year}"

    @property
    def csv(self) -> str:
        return f"{BASE}/landtagswahl_{self.year}/dokumente/{self.file}.csv"

    @property
    def page(self) -> str:
        return f"{BASE}/landtagswahl_{self.year}/index.html"


EDITIONS = [
    Edition(1989, "wahl19"),
    Edition(1994, "wahl1"),
    Edition(1999, "wahl2"),
    Edition(2003, "wahl11"),
    Edition(2008, "wahl20"),
    Edition(2013, "wahl33"),
    Edition(2018, "wahl53"),
    Edition(2022, "wahl62"),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
LANDTAG = key("body", "landtag")
LAND = key("area", "at-7.land", "7")
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    for edition in EDITIONS:
        raw.fetch(SOURCE, edition.csv, suffix=".csv")
        raw.fetch(SOURCE, edition.page, suffix=".html")


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


def number(text: str) -> int:
    return int(text.replace(".", "").strip())


@dataclass
class Party:
    position: int
    label: str
    votes: int


@dataclass
class Row:
    district: str
    district_name: str
    code: str
    name: str
    registered: int
    cast: int
    valid: int
    invalid: int
    parties: list[Party]


@functools.cache
def table(edition: Edition) -> tuple[date, list[Row]]:
    """Read the results file: one printed row per municipality, with its totals and each list's votes."""
    text = raw.cached(SOURCE, edition.csv).path.read_bytes().decode("cp1252")
    lines = text.splitlines()
    match = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", lines[0])
    assert match is not None
    day = date(int(match[3]), int(match[2]), int(match[1]))
    reader = csv.reader(io.StringIO("\n".join(lines[1:])), delimiter=";")
    header = next(reader)
    at = {name: header.index(name) for name in ("Wahlberechtigte gesamt", "Abgegebene Stimmen", "Gültige Stimmen")}
    invalid = header.index("Ungültige Stimmen")
    rows: list[Row] = []
    for line in reader:
        if not line or not line[0].strip():
            continue
        parties = []
        for index in range(invalid + 1, len(line) - 3, 4):
            if not line[index].strip():
                continue
            parties.append(Party(int(line[index]), " ".join(line[index + 1].split()), number(line[index + 2])))
        rows.append(
            Row(
                line[0],
                line[1],
                line[2],
                line[3],
                number(line[at["Wahlberechtigte gesamt"]]),
                number(line[at["Abgegebene Stimmen"]]),
                number(line[at["Gültige Stimmen"]]),
                number(line[invalid]),
                parties,
            )
        )
    return day, rows


class Cells(HTMLParser):
    """Collect the text of each cell of each table row that is marked as a current-election party row."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.live = False
        self.cell: list[str] | None = None
        self.current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            classes = dict(attrs).get("class") or ""
            self.live = "werber-aktuell" in classes.split()
            self.current = []
        elif tag == "td" and self.live:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.cell is not None:
            self.current.append(" ".join("".join(self.cell).split()).replace("\xa0", ""))
            self.cell = None
        elif tag == "tr" and self.live:
            self.rows.append(self.current)
            self.live = False


@dataclass
class Declared:
    registered: int
    cast: int
    valid: int
    invalid: int
    votes: dict[str, int]
    seats: dict[str, int]


@functools.cache
def declared(edition: Edition) -> Declared:
    """The state totals and each list's votes and seats as the authority's state-level page prints them."""
    html = raw.cached(SOURCE, edition.page).path.read_text("utf-8")
    plain = " ".join(re.sub(r"<[^>]*>", " ", html).replace("&nbsp;", " ").split())
    totals = {}
    for field, label in (
        ("registered", r"Wahlberechtigte(?: S\))?"),
        ("cast", r"Abgegebene Stimmen(?: A\))?"),
        ("valid", r"davon gültige(?: G\))?"),
        ("invalid", r"davon ungültige(?: U\))?"),
    ):
        found = re.search(label + r"\s+([\d.]+)", plain)
        assert found is not None, f"{edition.key}: no {field} on the state page"
        totals[field] = number(found[1])
    parser = Cells()
    parser.feed(html)
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for cells in parser.rows:
        cells = [c for c in cells]
        name = cells[0]
        offset = 1 if len(cells) > 1 and re.fullmatch(r"\d{4}", cells[1]) else 0
        votes[name] = number(cells[1 + offset])
        seat = cells[5 + offset] if offset else (cells[3] if len(cells) > 3 else "")
        if re.fullmatch(r"\d+", seat):
            seats[name] = int(seat)
    return Declared(totals["registered"], totals["cast"], totals["valid"], totals["invalid"], votes, seats)


PERSISTENT = [
    (re.compile(r"^(övp|vp\b|anton mattle|tiroler volkspartei)", re.IGNORECASE), "ÖVP"),
    (re.compile(r"^(spö|sozialdemokratische)", re.IGNORECASE), "SPÖ"),
    (re.compile(r"^(fpö|freiheitliche)", re.IGNORECASE), "FPÖ"),
    (re.compile(r"^(grüne|die grünen)", re.IGNORECASE), "GRÜNE"),
    (re.compile(r"^(kpö|kommunistische)", re.IGNORECASE), "KPÖ"),
    (re.compile(r"^(neos)", re.IGNORECASE), "NEOS"),
    (re.compile(r"^(lif\b|liberales forum)", re.IGNORECASE), "LIF"),
    (re.compile(r"^(fritz|liste fritz)", re.IGNORECASE), "FRITZ"),
]


def party_key(edition: Edition, label: str) -> str:
    for pattern, name in PERSISTENT:
        if pattern.search(label):
            return name
    return f"{label}@{edition.key}"


def party(edition: Edition, label: str) -> str:
    return key("party", party_key(edition, label))


def places() -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    """Each district's name, and each municipality's names (newest first) and district."""
    districts: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    parent: dict[str, str] = {}
    for edition in sorted(EDITIONS, key=lambda e: e.year, reverse=True):
        for row in table(edition)[1]:
            districts.setdefault(row.district, row.district_name)
            found = names.setdefault(row.code, [])
            if row.name not in found:
                found.append(row.name)
            parent.setdefault(row.code, row.district)
    return districts, names, parent


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Tyrol's districts and municipalities as the state's election office codes them in its Landtag results",
        depends=[f"{landtag.SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, noun in (
        ("at-7.land", 1, "state"),
        ("at-7.bezirk", 2, "political district"),
        ("at-7.gemeinde", 3, "municipality"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add("polity_territory", polity_id=LAND_POLITY, area_id=LAND)
    p.add("area", id=LAND, scheme_code="at-7.land")
    p.add("area_code", area_id=LAND, code="7")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Tirol", "de", "official")
    p.name(LAND, "Tyrol", "en", "official", False)
    districts, names, parent = places()
    for code, text in sorted(districts.items()):
        id = key("area", "at-7.bezirk", code)
        p.add("area", id=id, scheme_code="at-7.bezirk")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=LAND)
        p.name(id, text, "de", "official")
    for code, found in sorted(names.items()):
        id = key("area", "at-7.gemeinde", code)
        p.add("area", id=id, scheme_code="at-7.gemeinde")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-7.bezirk", parent[code]))
        for index, text in enumerate(found):
            p.name(id, text, "de", "official", index == 0)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Tyrolean Landtag and the lists on its ballots, as the state's election office prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Land Tirol",
        title="Land Tirol - Wahlen: Landtagswahlen",
        url=f"{BASE}/",
        attribution="Land Tirol",
    )
    for edition in EDITIONS:
        p.artifact(raw.cached(SOURCE, edition.csv), SOURCE_ID)
        p.artifact(raw.cached(SOURCE, edition.page), SOURCE_ID)
    p.add("body", id=LANDTAG, polity_id=LAND_POLITY, kind="unicameral")
    p.name(LANDTAG, "Tyrolean Landtag", "en", "official")
    p.name(LANDTAG, "Tiroler Landtag", "de", "native", False)
    seen: set[str] = set()
    for edition in EDITIONS:
        for row in table(edition)[1]:
            for entry in row.parties:
                id = party(edition, entry.label)
                if id in seen:
                    continue
                seen.add(id)
                p.add("party", id=id, kind="party", polity_id=AUSTRIA)
                p.add("external_id", entity_id=id, scheme="at.tirol.party", value=party_key(edition, entry.label))
                p.name(id, entry.label, "de", "official")
    return p


METHOD = (
    "Results by municipality (Gemeinde) from the Tyrolean state election office's results file, with the state "
    "totals and seats from its state-level page. Ballots cast by Tyroleans living abroad are counted in the "
    "municipality that lists them. Registered voters include them."
)


def election(edition: Edition) -> Package:
    day, rows = table(edition)
    state = declared(edition)
    year = edition.year
    p = Package(
        f"{SOURCE}/{edition.key}",
        SOURCE,
        f"The {day} Tyrolean Landtag election by municipality from the state's election office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, edition.csv))
    p.uses(raw.cached(SOURCE, edition.page))
    id = key("election", edition.key)
    p.add("election", id=id, polity_id=LAND_POLITY, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Tyrolean Landtag election", "en", "official")
    p.name(id, f"Landtagswahl {year}", "de", "native", False)
    path = f"at/{day}-tirol-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", edition.key)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=LANDTAG,
        district_area_id=LAND,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Tyrolean Landtag", "en", "official")
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
    labels = {entry.position: entry.label for row in rows for entry in row.parties}
    options: dict[int, str] = {}
    for position, label in sorted(labels.items()):
        option = options[position] = key("option", edition.key, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(edition, label),
            ballot_position=position,
            ballot_label=label,
        )
    total = {pos: sum(e.votes for r in rows for e in r.parties if e.position == pos) for pos in labels}
    by_page = match(labels, total, state)
    for field, measure in (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=getattr(state, field))
    for position, name in by_page.items():
        p.add("declared", result_set_id=result_set, option_id=options[position], measure=1, value=state.votes[name])
        if state.seats.get(name):
            p.add("outcome", result_set_id=result_set, option_id=options[position], kind="won", seats=state.seats[name])
    sums = {f: sum(getattr(r, f) for r in rows) for f in ("registered", "cast", "valid", "invalid")}
    for field, value in sums.items():
        if value != getattr(state, field):
            p.accept(
                "accounting.declared",
                f"the municipality rows add up to {value:,} {field} against {getattr(state, field):,} on the state page",
            )
    unlisted: list[str] = []
    for row in rows:
        unit = key("unit", edition.key, row.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LAND_POLITY,
            scheme_code="at-7.gemeinde",
            code=f"{day}:{row.code}",
            area_relation="equals",
            area_id=key("area", "at-7.gemeinde", row.code),
            kind="standard",
        )
        p.name(unit, row.name, "de", "official")
        for measure, field in MEASURES:
            if field == "registered" and row.registered == 0 and row.cast > 0:
                unlisted.append(row.name)
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
                value=getattr(row, field),
            )
        for entry in row.parties:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[entry.position],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=entry.votes,
            )
    if unlisted:
        p.add(
            "note",
            id=key("note", result_set, "registered"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The file prints no registered voters (zero) for {len(unlisted)} "
            f"{'municipality' if len(unlisted) == 1 else 'municipalities'} that counted ballots, so their "
            "registered voters are left out rather than stored as zero.",
            lang="en",
        )
    over = [r for r in rows if r.registered and r.cast > r.registered]
    if over:
        p.accept(
            "accounting.turnout",
            f"{len(over)} {'municipality' if len(over) == 1 else 'municipalities'} counted more ballots than the "
            "file lists registered voters, as printed",
        )
    return p


def match(labels: dict[int, str], total: dict[int, int], state: Declared) -> dict[int, str]:
    """Pair each list of the results file with its row on the state page: by name, else by its vote total."""
    names = defaultdict(list)
    for name, votes in state.votes.items():
        names[votes].append(name)
    found: dict[int, str] = {}
    for position, votes in total.items():
        candidates = [n for n in names.get(votes, []) if n not in found.values()]
        exact = [n for n in state.votes if n == labels[position]]
        if exact:
            found[position] = exact[0]
        elif len(candidates) == 1:
            found[position] = candidates[0]
        else:
            raise ValueError(f"{position} {labels[position]} ({votes}) does not match a row of the state page")
    return found
