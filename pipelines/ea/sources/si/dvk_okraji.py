"""Slovenian National Assembly elections of 1996-2008 by electoral district (volilni okraj), from the State Election
Commission's archived result sites (https://www.dvk-rs.si/arhivi/dz<year>/).

Each election is one package, `si.dvk/dz-<date>`, built on `si.dvk/geo` and `si.dvk/registry` from
`ea.sources.si.dvk`, plus `si.dvk/systems-1992`, which holds the electoral systems in force before the 2011
National Assembly election. As in the polling-station packages, each of the 8 electoral units is a contest whose
options are the lists standing there. The commission published these elections no finer than the 88 electoral
districts, so each district is a reporting unit that equals its district area. The printed totals of each electoral
unit are stored as the declared figures of its contest. The 1992 site prints national list totals only, so 1992 is
not here.
"""

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.si.dvk import (
    CHAMBER_BODY,
    DVK_SOURCE,
    ELECTORAL_UNITS,
    SLOVENIA,
    SOURCE,
    area,
    bare,
    clean,
    key,
    plain,
    title,
)

ARCHIVE = "https://www.dvk-rs.si/arhivi"
SYSTEMS_KEY = "si.dvk/systems-1992"
SYSTEM_1992 = key("electoral_system", "dz-1992")
SYSTEM_2000 = key("electoral_system", "dz-2000")
SYSTEM_RULES: dict[str, tuple[str, date, date, dict[str, object], str, str, str]] = {
    "dz-1992": (
        SYSTEM_1992,
        date(1992, 9, 10),
        date(2000, 7, 24),
        {"formula": "droop", "remainders": "national"},
        "National Assembly electoral system (1992-2000)",
        "Volilni sistem Državnega zbora (1992-2000)",
        (
            "The 88 deputies chosen by list are elected in 8 electoral units of 11 deputies each, and every unit is "
            "divided into 11 electoral districts (volilni okraji), each holding one candidate of every list. A vote "
            "for a candidate is a vote for the candidate's list. Seats are first given in each unit by the Droop quota; "
            "the remaining seats are pooled nationally and given to the lists' remainders by the D'Hondt method. Only "
            "lists whose votes nationally would win at least three quota seats share the national remainder. The "
            "Italian and Hungarian national communities each elect one more deputy separately."
        ),
    ),
    "dz-2000": (
        SYSTEM_2000,
        date(2000, 7, 25),
        date(2010, 12, 31),
        {
            "formula": "droop",
            "remainders": "national",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
        },
        "National Assembly electoral system (2000-2011)",
        "Volilni sistem Državnega zbora (2000-2011)",
        (
            "The constitutional amendment of July 2000 kept the 8 electoral units of 11 deputies and their 11 electoral "
            "districts each, and set a national threshold of 4 percent of the valid votes. Seats are first given in each "
            "unit by the Droop quota; the remaining seats are pooled nationally and given to the lists' remainders by "
            "the D'Hondt method. Before 2006 a list could field one candidate in two districts of a unit. The Italian and "
            "Hungarian national communities each elect one more deputy separately."
        ),
    ),
}


@dataclass(frozen=True)
class Year:
    """One archived National Assembly election site."""

    day: date
    folder: str
    pages: dict[str, str]
    system: str
    stage: str = "official"

    @property
    def year(self) -> int:
        return self.day.year

    def url(self, page: str) -> str:
        return f"{ARCHIVE}/{self.folder}/{self.pages[page]}"

    def urls(self) -> list[str]:
        return [self.url(page) for page in self.pages]


def _units(prefix: str) -> dict[str, str]:
    return {f"okraji{unit}": prefix.format(unit) for unit in ELECTORAL_UNITS}


YEARS = [
    Year(
        date(1996, 11, 10),
        "dz1996",
        {"okraji": "rezultati_vo.html", "units": "rezultati_ve.html", "national": "rezultati_sl.html"},
        SYSTEM_1992,
    ),
    Year(
        date(2000, 10, 15),
        "dz2000",
        {
            **_units("rez_vo{}.html"),
            "turnout": "udel_vo.html",
            "units": "rez_ve.html",
            "national": "rez_sl.html",
        },
        SYSTEM_2000,
        stage="preliminary",
    ),
    Year(
        date(2004, 10, 3),
        "dz2004",
        {
            **_units("html/rez_ka{}.html"),
            "turnout": "html/udel_vo.html",
            "units": "html/rez_ve.html",
            "national": "html/rez_si.html",
            "elected": "html/rez_imena.html",
        },
        SYSTEM_2000,
    ),
    Year(
        date(2008, 9, 21),
        "dz2008",
        {
            **_units("rezultati/rez_ka{}.html"),
            "turnout": "udelezba/udelezba_okraji.html",
            "units": "rezultati/izidi_enote.html",
            "national": "rezultati/rezultati_slo.html",
            "elected": "rezultati/izvoljeni_kandid_po_volilnih_enotah.html",
        },
        SYSTEM_2000,
    ),
]

UNIT_NAMES = {
    1: "Kranj",
    2: "Postojna",
    3: "Ljubljana Center",
    4: "Ljubljana Bežigrad",
    5: "Celje",
    6: "Novo mesto",
    7: "Maribor",
    8: "Ptuj",
}


class _Tables(HTMLParser):
    """Collect every table of a page as rows of cell texts; a line break inside a cell becomes a space."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.stack: list[list[list[str]]] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.stack.append([])
        elif tag == "tr" and self.stack:
            self.stack[-1].append([])
        elif tag in ("td", "th") and self.stack:
            if not self.stack[-1]:
                self.stack[-1].append([])
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.stack:
            self.stack[-1][-1].append(clean("".join(self.cell)))
            self.cell = None
        elif tag == "table" and self.stack:
            self.tables.append([row for row in self.stack.pop() if any(row)])

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def text(path: Path) -> str:
    body = path.read_bytes()
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1250")


def tables_at(url: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(text(raw.cached(SOURCE, url).path))
    return parser.tables


def tables(year: Year, page: str) -> list[list[list[str]]]:
    return tables_at(year.url(page))


def number(cell: str) -> int:
    """The count at the start of a cell, which may be followed by a percentage."""
    found = re.match(r"\s*(\d[\d.]*)", cell)
    if found is None:
        raise ValueError(f"no count in {cell!r}")
    return int(found.group(1).replace(".", ""))


def dash(cell: str) -> int | None:
    return None if cell.strip() in ("", "-") else number(cell)


@dataclass
class Okraj:
    unit: int
    seat: int
    name: str
    registered: int | None = None
    voters: int | None = None
    cast: int | None = None
    invalid: int | None = None
    valid: int | None = None
    votes: dict[str, int] = field(default_factory=dict)
    candidates: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Totals:
    """The printed totals of an electoral unit or of Slovenia."""

    cast: int | None = None
    invalid: int | None = None
    votes: dict[str, int] = field(default_factory=dict)

    @property
    def valid(self) -> int | None:
        return None if self.cast is None or self.invalid is None else self.cast - self.invalid


@dataclass(frozen=True)
class Elected:
    """One deputy the commission names as elected, with the electoral unit, list, and district it prints."""

    unit: int
    list_name: str
    candidate: str
    district: str


@dataclass
class Parsed:
    year: Year
    lists: dict[str, str]
    """Each list's key (the label the district tables print) mapped to its full name."""
    okraji: list[Okraj]
    units: dict[int, Totals]
    national: Totals
    labels: dict[tuple[int, str], str] = field(default_factory=dict)
    """A per-unit full name for lists whose name differs by unit, such as the independent candidates of 1996."""
    seats: dict[int, dict[str, int]] = field(default_factory=dict)
    """Each electoral unit's declared seats by list key, where the site prints them (2004 and 2008)."""
    elected: list[Elected] = field(default_factory=list)
    """The deputies the site names, by unit and list (2004 and 2008)."""


def fetch() -> None:
    for year in YEARS:
        for url in year.urls():
            raw.fetch(SOURCE, url, suffix=".html")


def _national_names(year: Year) -> dict[str, int]:
    """The national list totals as printed with each list's full name."""
    found: dict[str, int] = {}
    for table in tables(year, "national"):
        if not table or not table[0] or table[0][0] != "Kandidatna lista":
            continue
        for row in table[1:]:
            if len(row) >= 2 and row[0] and re.match(r"\d", row[1] or ""):
                found[row[0]] = number(row[1])
    if not found:
        raise LookupError(f"{year.url('national')} has no list totals")
    return found


def _unit_table(year: Year) -> tuple[list[str], dict[int, Totals], Totals]:
    """The table of each electoral unit's ballots and list votes, with Slovenia's column where printed."""
    return unit_votes(tables(year, "units"), year.year, year.url("units"))


def unit_votes(found: list[list[list[str]]], year: int, where: str) -> tuple[list[str], dict[int, Totals], Totals]:
    for table in found:
        head = table[0] if table else []
        if len(head) < 9 or not any("Kranj" in cell for cell in head):
            continue
        if not any(re.search(r"glas", row[0], re.IGNORECASE) for row in table[1:3]):
            continue
        offset = 1 if year == 1996 else 2
        first = offset if year == 1996 else 1
        units = {unit: Totals() for unit in ELECTORAL_UNITS}
        national = Totals()
        order: list[str] = []
        for row in table[1:]:
            label = row[0]
            cells = row[offset : offset + 8]
            whole = None if year == 1996 else row[first]
            if re.search(r"preštetih|oddanih", label, re.IGNORECASE):
                for unit, cell in zip(ELECTORAL_UNITS, cells, strict=True):
                    units[unit].cast = number(cell)
                national.cast = None if whole is None else number(whole)
            elif re.search(r"neveljav", label, re.IGNORECASE):
                for unit, cell in zip(ELECTORAL_UNITS, cells, strict=True):
                    units[unit].invalid = number(cell)
                national.invalid = None if whole is None else number(whole)
            else:
                order.append(label)
                for unit, cell in zip(ELECTORAL_UNITS, cells, strict=True):
                    value = dash(cell)
                    if value is not None:
                        units[unit].votes[label] = value
                if whole is not None:
                    national.votes[label] = number(whole)
        return order, units, national
    raise LookupError(f"{where} has no table of electoral units")


def seat_table(found: list[list[list[str]]], where: str) -> dict[str, dict[int, int]]:
    """Each list's seats in each electoral unit, by the list's short label, from the page's table of mandates by
    electoral unit. The table's own total column and the 11 seats of every unit are checked."""
    for table in found:
        head = table[0] if table else []
        if len(head) != 10 or head[:2] != ["Lista", "Skupaj"]:
            continue
        seats: dict[str, dict[int, int]] = {}
        for row in table[1:]:
            if len(row) != 10:
                raise ValueError(f"{where}: unreadable seat row {row}")
            by_unit = {
                unit: 0 if cell == "-" else number(cell) for unit, cell in zip(ELECTORAL_UNITS, row[2:], strict=True)
            }
            if sum(by_unit.values()) != number(row[1]):
                raise ValueError(f"{where}: {row[0]} has {sum(by_unit.values())} seats in its units, not {row[1]}")
            seats[row[0]] = {unit: n for unit, n in by_unit.items() if n}
        for unit in ELECTORAL_UNITS:
            if sum(lists.get(unit, 0) for lists in seats.values()) != 11:
                raise ValueError(f"{where}: unit {unit} does not hold 11 seats")
        return seats
    raise LookupError(f"{where} has no table of seats by electoral unit")


def elected_2004(url: str) -> list[Elected]:
    """The elected deputies of 2004: one table per list, headed by the list's name, with a row for each unit."""
    page = text(raw.cached(SOURCE, url).path)
    heads = re.findall(r'<p class="tekst">([^<]+)</p>\s*<table', page)
    boards = [t for t in tables_at(url) if t and t[0][:2] == ["Kandidat", "Volilni okraj"]]
    if len(heads) != len(boards):
        raise LookupError(f"{url}: {len(heads)} list headings but {len(boards)} tables")
    found: list[Elected] = []
    for head, board in zip(heads, boards, strict=True):
        unit = 0
        for row in board[1:]:
            if len(row) == 1:
                heading = re.match(r"VE\s+(\d)", row[0])
                if heading is None:
                    raise ValueError(f"{url}: unreadable unit heading {row[0]!r}")
                unit = int(heading.group(1))
            else:
                found.append(Elected(unit, clean(head), row[0], row[1]))
    return found


def elected_2008(url: str) -> list[Elected]:
    """The elected deputies of 2008: one table per electoral unit, with a row naming each list."""
    boards = [t for t in tables_at(url) if t and t[0][:2] == ["Kandidat", "Volilni okraj"]]
    if len(boards) != 8:
        raise LookupError(f"{url}: found {len(boards)} unit tables")
    found: list[Elected] = []
    for unit, board in zip(ELECTORAL_UNITS, boards, strict=True):
        name = ""
        for row in board[1:]:
            if len(row) == 1:
                name = row[0]
            else:
                found.append(Elected(unit, name, row[0], row[1]))
    return found


def _turnout(year: Year) -> dict[tuple[int, int], tuple[int, int, str]]:
    """Each district's registered voters, voters, and printed name, from the turnout page; the first row of each
    unit's table is the unit itself."""
    found: dict[tuple[int, int], tuple[int, int, str]] = {}
    unit = 0
    for table in tables(year, "turnout"):
        head = " ".join(table[0]) if table else ""
        if "Udeležba" not in head or len(table) != 13:
            continue
        unit += 1
        cells = table[0]
        names = [cell for cell in cells if cell]
        voters_first = names.index("Udeležba") < next(
            i for i, cell in enumerate(names) if re.search(r"volivcev|upravičencev", cell)
        )
        for seat, row in enumerate(table[2:], start=1):
            values = [cell for cell in row[1:] if cell]
            first, second = number(values[0]), number(values[1])
            registered, voters = (second, first) if voters_first else (first, second)
            found[(unit, seat)] = (registered, voters, row[0])
    if unit != 8:
        raise LookupError(f"{year.url('turnout')}: found {unit} electoral units")
    return found


SEAT_HEAD = re.compile(r"^(\d+)\.\s*volilni\s+okraj\s*[-:]\s*(.+)$")


def _candidates(year: Year, unit: int) -> list[Okraj]:
    """The districts of one unit from a page that prints, per district, every candidate with the list and votes."""
    page = text(raw.cached(SOURCE, year.url(f"okraji{unit}")).path)
    heads = [
        (int(found.group(1)), clean(found.group(2)))
        for found in (
            SEAT_HEAD.match(clean(re.sub(r"<[^>]+>", " ", line)))
            for line in re.findall(r">([^<>]*\d+\.\s*volilni\s+okraj\s*[-:][^<>]+)<", page)
        )
        if found
    ]
    parser = _Tables()
    parser.feed(page)
    boards = [
        table for table in parser.tables if table and table[0][:2] in (["Kandidat", "Lista"], ["Kandidat", "Stranka"])
    ]
    if len(boards) != 11 or [seat for seat, _ in heads] != list(range(1, 12)):
        raise LookupError(f"{year.url(f'okraji{unit}')}: found {len(boards)} districts and headings {heads}")
    okraji: list[Okraj] = []
    for (seat, name), board in zip(heads, boards, strict=True):
        okraj = Okraj(unit, seat, name, valid=number(board[1][1]))
        for row in board[2:]:
            found = re.match(r"(\d+)\.\s*(.+)$", row[0])
            if found is None or len(row) < 3:
                raise ValueError(f"{year.year} district {unit}.{seat}: unreadable row {row}")
            label = row[1]
            okraj.votes[label] = okraj.votes.get(label, 0) + number(row[2])
            okraj.candidates.setdefault(label, []).append(clean(found.group(2)))
        okraji.append(okraj)
    return okraji


def _grid(year: Year, unit: int, rows: list[list[str]], names: list[str]) -> list[Okraj]:
    """The districts of one unit from a table whose columns are the districts and whose rows are the lists."""
    okraji = [Okraj(unit, seat, name) for seat, name in enumerate(names, start=1)]
    for row in rows:
        label, cells = row[0], row[-11:]
        if len(row) < 12:
            raise ValueError(f"{year.year} unit {unit}: short row {row}")
        for okraj, cell in zip(okraji, cells, strict=True):
            if re.search(r"preštetih", label, re.IGNORECASE):
                okraj.cast = number(cell)
            elif re.search(r"neveljav", label, re.IGNORECASE):
                okraj.invalid = number(cell)
            elif (value := dash(cell)) is not None:
                okraj.votes[label] = value
    for okraj in okraji:
        if okraj.cast is not None and okraj.invalid is not None:
            okraj.valid = okraj.cast - okraj.invalid
        else:
            okraj.valid = sum(okraj.votes.values())
    return okraji


def _okraji_2000(year: Year) -> list[Okraj]:
    okraji: list[Okraj] = []
    for unit in ELECTORAL_UNITS:
        table = next(t for t in tables(year, f"okraji{unit}") if len(t) > 2 and "Skupaj" in t[1])
        names = [title(cell) for cell in table[1][-11:]]
        okraji += _grid(year, unit, table[2:], names)
    return okraji


def _okraji_1996(year: Year) -> tuple[list[Okraj], dict[str, str], dict[tuple[int, str], str]]:
    found = tables(year, "okraji")
    grids = [t for t in found if t and t[0] and t[0][0].startswith("Lista / Volilni okraj")]
    if len(grids) != 8:
        raise LookupError(f"{year.url('okraji')}: found {len(grids)} unit tables")
    okraji: list[Okraj] = []
    for unit, table in zip(ELECTORAL_UNITS, grids, strict=True):
        names = [re.sub(r"^\d+", "", cell).strip() for cell in table[0][1:12]]
        okraji += _grid(year, unit, table[1:], names)
    board = next(t for t in tables(year, "units") if t and t[0] and t[0][0] == "LEGENDA")
    full = {row[1]: row[2] for row in board[1:] if len(row) >= 3 and row[1]}
    legend: dict[tuple[int, str], str] = {}
    for code, description in list(full.items()):
        if not code.startswith("NK"):
            continue
        for part in re.split(r"\s*v volilni enoti\s+", description)[1:]:
            unit_text, _, name = part.partition(":")
            legend[(int(unit_text), code)] = clean(name)
        full[code] = f"Neodvisni kandidat {code[2:]}"
    labels: dict[tuple[int, str], str] = {}
    for unit in ELECTORAL_UNITS:
        standing = {
            code
            for okraj in okraji
            if okraj.unit == unit
            for code, v in okraj.votes.items()
            if v and code in full and code.startswith("NK")
        }
        named = {code for (where, code) in legend if where == unit}
        for code in standing:
            # The legend names the independents of units 2 and 3 in a way that contradicts the vote tables (it gives
            # unit 2 two independents and unit 3 three, where the tables show the reverse), so a unit whose legend
            # does not match its table keeps the generic label.
            labels[(unit, code)] = (
                legend[(unit, code)] if named == standing else f"{full[code]} ({unit}. volilna enota)"
            )
    return okraji, full, labels


def load(year: Year) -> Parsed:
    order, units, national = _unit_table(year)
    printed = _national_names(year)
    labels: dict[tuple[int, str], str] = {}
    short: dict[str, str] = {}
    if year.year == 1996:
        okraji, full, labels = _okraji_1996(year)
        lists = {label: full[label] for label in order}
        national = Totals(
            cast=sum(totals.cast or 0 for totals in units.values()),
            invalid=sum(totals.invalid or 0 for totals in units.values()),
            votes={},
        )
        by_name = {re.sub(r"^\S+ - ", "", name, count=1): votes for name, votes in printed.items()}
        for label, name in lists.items():
            if label.startswith("NK"):
                national.votes[label] = sum(totals.votes.get(label, 0) for totals in units.values())
            else:
                national.votes[label] = by_name[name]
    elif year.year == 2000:
        okraji = _okraji_2000(year)
        lists = _match_names(year, national.votes, printed)
        national.votes = {label: printed[lists[label]] for label in national.votes}
    else:
        okraji = [okraj for unit in ELECTORAL_UNITS for okraj in _candidates(year, unit)]
        seen = {label for okraj in okraji for label in okraj.votes}
        lists = {label: label for label in printed if label in seen}
        missing = seen - set(lists)
        if missing:
            raise LookupError(f"{year.year}: district lists missing from the national table: {sorted(missing)}")
        short = _match_names(year, national.votes, {name: printed[name] for name in lists})
        units = {
            unit: Totals(totals.cast, totals.invalid, {short[label]: v for label, v in totals.votes.items()})
            for unit, totals in units.items()
        }
        national = Totals(national.cast, national.invalid, {name: printed[name] for name in lists})
    if "turnout" in year.pages:
        turnout = _turnout(year)
        for okraj in okraji:
            okraj.registered, okraj.voters, _ = turnout[(okraj.unit, okraj.seat)]
    parsed = Parsed(year, lists, okraji, units, national, labels)
    if "elected" in year.pages:
        _seats(parsed, short)
    return parsed


def _seats(parsed: Parsed, short: dict[str, str]) -> None:
    """Read the seats of each list in each unit and the deputies the commission names, and check them against each
    other."""
    year = parsed.year
    where = year.url("units")
    parsed.seats = {unit: {} for unit in ELECTORAL_UNITS}
    for label, by_unit in seat_table(tables(year, "units"), where).items():
        if label not in short:
            raise LookupError(f"{where}: seats for {label}, which has no votes")
        for unit, n in by_unit.items():
            parsed.seats[unit][short[label]] = n
    named = (elected_2004 if year.year == 2004 else elected_2008)(year.url("elected"))
    by_plain: dict[str, str] = {plain(name): name for name in parsed.lists}
    for deputy in named:
        key_ = plain(deputy.list_name)
        found = [name for name in by_plain.values() if plain(name) == key_] or [
            name for name in by_plain.values() if plain(name) in key_ or key_ in plain(name)
        ]
        if len(found) != 1:
            raise LookupError(f"{year.year}: elected deputy list {deputy.list_name!r} matches {found}")
        parsed.elected.append(replace(deputy, list_name=found[0]))
    count: Counter[tuple[int, str]] = Counter((d.unit, d.list_name) for d in parsed.elected)
    seats = {(unit, name): n for unit, lists in parsed.seats.items() for name, n in lists.items()}
    if dict(count) != seats:
        raise ValueError(f"{year.year}: named deputies {dict(count)} disagree with the seats {seats}")


def _match_names(year: Year, short: dict[str, int], printed: dict[str, int]) -> dict[str, str]:
    """Pair the abbreviated list labels of the unit table with the full names of the national table by their equal
    national totals."""
    by_votes: dict[int, list[str]] = {}
    for name, votes in printed.items():
        by_votes.setdefault(votes, []).append(name)
    paired: dict[str, str] = {}
    for label, votes in short.items():
        names = by_votes.get(votes, [])
        if len(names) != 1:
            raise LookupError(f"{year.year}: {label} ({votes:,}) matches {names} in the national table")
        paired[label] = names[0]
    if len(set(paired.values())) != len(paired):
        raise LookupError(f"{year.year}: two labels share a national list")
    return paired


def reconcile(parsed: Parsed) -> dict[str, object]:
    """Compare the sums of the district rows with each unit's printed totals, and the units with Slovenia's."""
    problems: list[str] = []
    for unit in ELECTORAL_UNITS:
        here = [okraj for okraj in parsed.okraji if okraj.unit == unit]
        printed = parsed.units[unit]
        for label in {*printed.votes, *(label for okraj in here for label in okraj.votes)}:
            got = sum(okraj.votes.get(label, 0) for okraj in here)
            if got != printed.votes.get(label, 0):
                problems.append(f"unit {unit} {label}: districts {got:,}, printed {printed.votes.get(label, 0):,}")
        valid = sum(okraj.valid or 0 for okraj in here)
        if printed.valid is not None and valid != printed.valid:
            problems.append(f"unit {unit} valid: districts {valid:,}, printed {printed.valid:,}")
        for name in ("cast", "invalid"):
            if all(getattr(okraj, name) is not None for okraj in here):
                got = sum(getattr(okraj, name) for okraj in here)
                if got != getattr(printed, name):
                    problems.append(f"unit {unit} {name}: districts {got:,}, printed {getattr(printed, name):,}")
    for label, votes in parsed.national.votes.items():
        got = sum(totals.votes.get(label, 0) for totals in parsed.units.values())
        if got != votes:
            problems.append(f"Slovenia {label}: units {got:,}, printed {votes:,}")
    for name in ("cast", "invalid"):
        wanted = getattr(parsed.national, name)
        got = sum(getattr(totals, name) or 0 for totals in parsed.units.values())
        if wanted is not None and got != wanted:
            problems.append(f"Slovenia {name}: units {got:,}, printed {wanted:,}")
    return {
        "districts": len(parsed.okraji),
        "valid": sum(okraj.valid or 0 for okraj in parsed.okraji),
        "national_valid": sum(parsed.national.votes.values()),
        "problems": problems,
    }


def slug(year: Year) -> str:
    return f"si.dvk/dz-{year.day}"


def packages() -> list[str]:
    return [SYSTEMS_KEY, *(slug(year) for year in YEARS)]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    systems().write()
    for year in YEARS:
        parsed = load(year)
        found = reconcile(parsed)
        print(f"{slug(year)}: {found}", flush=True)
        election(parsed).write()


def systems() -> Package:
    p = Package(
        SYSTEMS_KEY,
        SOURCE,
        "The National Assembly electoral systems in force before 2011",
        depends=["si.dvk/registry"],
    )
    p.external.add(SLOVENIA)
    for id, since, until, rules, english, native, body in SYSTEM_RULES.values():
        p.add(
            "electoral_system",
            id=id,
            polity_id=SLOVENIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.name(id, english, "en", "official")
        p.name(id, native, "sl", "native", False)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    return p


def method(parsed: Parsed) -> str:
    year = parsed.year
    text = (
        "Published by the State Election Commission (Državna volilna komisija) on its archived results site for the "
        f"{year.year} election, by electoral district (volilni okraj), the finest level it published. Each of the 8 "
        "electoral units is a contest; its 11 districts are its reporting units, numbered within the unit as the "
        "commission numbers them, and each is tied to the district area of that number. The unit totals the "
        "commission prints are stored as the contest's declared figures, and the district rows sum to them. "
    )
    if year.year == 1996:
        text += (
            "The 1996 site prints each list's valid votes per district but not the ballots cast, invalid ballots, or "
            "turnout per district, so a district's valid votes are the sum of its lists. A list that fielded no "
            "candidate in a district is printed with 0 votes there. Each independent candidate stands as a list of "
            "its own in its unit; the commission groups them under the labels NK1 to NK3, whose candidates differ "
            "by unit. "
        )
    elif year.year == 2000:
        text += (
            "The 2000 site still shows the commission's unofficial count, made from the polling-station figures the "
            "district commissions sent in, with 99.93 percent of the ballots counted; it was never replaced by the "
            "official count, so these are stored as a preliminary tabulation. Registered voters and voters are the "
            "electoral roll and those who voted, from the turnout page; ballots cast and invalid ballots are the "
            "counted ballots. "
        )
    else:
        text += (
            "Each district shows every list's candidate there with the votes cast for that candidate, which are the "
            "list's votes in the district; the candidates are stored as list members of their unit's list. "
            "Registered voters and voters come from the turnout page. Ballots cast and invalid ballots are printed "
            "for the units only, and are stored with each unit's declared figures. "
        )
    if parsed.seats:
        text += (
            "Each list's seats in the electoral unit are the commission's declared outcome, from its table of mandates "
            "by electoral unit: the seats the list won in the unit from the unit allocation and the national remainder "
            "allocation. The deputies the commission names as elected are stored as outcomes of their candidacies. "
        )
    text += (
        "The two seats of the Italian and Hungarian national communities are elected separately and are not here. "
        "The district areas are the commission's 88 districts as numbered today; their boundaries have changed "
        "little since 1992."
    )
    return text


def _artifacts(p: Package, year: Year) -> None:
    for url in year.urls():
        p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)


def person_name(name: str) -> str:
    plain = bare(name)
    return title(plain) if plain.isupper() else plain


def _outcomes(
    p: Package,
    parsed: Parsed,
    unit: int,
    result_set: str,
    options: dict[str, str],
    candidacies: dict[tuple[str, str], str],
) -> None:
    """Declare the seats each list won in the unit and the deputies the commission names as elected."""
    for label, seats in sorted(parsed.seats.get(unit, {}).items()):
        p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=seats)
    for deputy in parsed.elected:
        if deputy.unit != unit:
            continue
        candidacy = candidacies.get((deputy.list_name, plain(bare(deputy.candidate))))
        if candidacy is None:
            raise LookupError(f"{parsed.year.year} unit {unit}: elected {deputy.candidate} is no candidate of the list")
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[deputy.list_name],
            candidacy_id=candidacy,
            kind="elected",
        )


def election(parsed: Parsed) -> Package:
    year = parsed.year
    p = Package(
        slug(year),
        SOURCE,
        f"The {year.year} Slovenian National Assembly election by electoral district",
        depends=["si.dvk/geo", "si.dvk/registry", SYSTEMS_KEY],
    )
    p.external.add(SLOVENIA)
    _artifacts(p, year)
    election_id = key("election", "dz", year.day)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="general",
        date_from=year.day,
        date_to=year.day,
        status="held",
    )
    p.name(election_id, f"{year.year} Slovenian parliamentary election", "en", "official")
    p.name(election_id, f"Volitve v Državni zbor {year.year}", "sl", "native", False)
    p.add("slug", path=f"si/{year.day}-general", entity_id=election_id, is_current=True)
    parties: dict[str, str] = {}
    for label, name in parsed.lists.items():
        if label.startswith("NK") and year.year == 1996:
            continue  # an independent candidate's label, which names a different candidate in each unit
        party = parties[label] = key("party", year.day, name)
        p.add("party", id=party, kind="party", polity_id=SLOVENIA)
        p.name(party, name, "sl", "official")
        if name != label:
            p.name(party, label, "sl", "short", False)
    for (unit, label), name in sorted(parsed.labels.items()):
        party = parties[f"{unit}:{label}"] = key("party", year.day, unit, label, name)
        p.add("party", id=party, kind="independent_group", polity_id=SLOVENIA)
        p.name(party, name, "sl", "official")
    text = method(parsed)
    people: set[str] = set()
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    for unit in ELECTORAL_UNITS:
        name = UNIT_NAMES[unit]
        here = [okraj for okraj in parsed.okraji if okraj.unit == unit]
        contest = key("contest", year.day, unit)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=CHAMBER_BODY,
            district_area_id=area("si.ve", str(unit)),
            magnitude=11,
            round_number=1,
            electoral_system_id=year.system,
            default_result_kind="counted",
        )
        p.name(contest, f"Electoral unit {unit} ({name})", "en", "official")
        p.name(contest, f"{unit}. volilna enota ({name})", "sl", "native", False)
        p.add(
            "slug",
            path=f"si/{year.day}-general/unit-{unit}-{changeset.slug(name)}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", f"{year.day}:{unit}")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage=year.stage,
            kind="counted",
            source_id=DVK_SOURCE,
            status="active",
            is_default=True,
            completeness=None if year.stage == "preliminary" else 1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=text, lang="en")
        printed = parsed.units[unit]
        options: dict[str, str] = {}
        members_of: dict[tuple[str, str], str] = {}
        for label in parsed.lists:
            if label not in printed.votes and not any(label in okraj.votes for okraj in here):
                continue
            if not printed.votes.get(label) and not any(okraj.votes.get(label) for okraj in here):
                continue
            ballot = parsed.labels.get((unit, label), parsed.lists[label])
            option = options[label] = key("option", year.day, unit, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=parties.get(f"{unit}:{label}") or parties[label],
                ballot_position=None,
                ballot_label=ballot,
            )
            members: list[str] = []
            for okraj in here:
                for candidate in okraj.candidates.get(label, []):
                    if candidate not in members:
                        members.append(candidate)
            for candidate in members:
                person = key("person", f"dz:{year.year}:{parsed.lists[label]}:{person_name(candidate)}")
                if person not in people:
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, person_name(candidate), "sl", "official")
                members_of[(label, plain(bare(candidate)))] = key("candidacy", year.day, unit, label, candidate)
                p.add(
                    "candidacy",
                    id=key("candidacy", year.day, unit, label, candidate),
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=candidate,
                )
        _outcomes(p, parsed, unit, result_set, options, members_of)
        short = [
            f"{okraj.name} prints candidates with {sum(okraj.votes.values()):,} votes but {okraj.valid:,} valid votes"
            for okraj in here
            if okraj.valid is not None and sum(okraj.votes.values()) != okraj.valid
        ]
        gaps = [
            f"{parsed.lists[label]} has {printed.votes.get(label, 0):,} votes in the unit total but "
            f"{sum(okraj.votes.get(label, 0) for okraj in here):,} in its districts"
            for label in options
            if printed.votes.get(label, 0) != sum(okraj.votes.get(label, 0) for okraj in here)
        ]
        if short or gaps:
            p.add(
                "note",
                id=key("note", result_set, "gap"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    "The commission's district table leaves out a list's candidate that its own totals count: "
                    f"{'; '.join(short + gaps)}. The figures are stored as printed."
                ),
                lang="en",
            )
            p.accept(
                "accounting.votes_valid",
                f"district {', '.join(okraj.name for okraj in here if okraj.valid != sum(okraj.votes.values()))} of "
                f"unit {unit}: the commission's district table omits one list's candidate row, while the district's "
                "printed valid votes and the unit's printed list totals include those votes; stored as printed",
            )
        generic = [code for (where, code), name in parsed.labels.items() if where == unit and name.endswith("enota)")]
        if generic:
            p.add(
                "note",
                id=key("note", result_set, "independents"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The commission's legend does not match the independent candidates its tables show in this unit "
                    f"({', '.join(sorted(generic))}): for units 2 and 3 it names Marko Brecelj and Matija Potokar in "
                    "unit 2 and Franc Klančar - Kopavel, Kristjan Verbič, and Rudolf Šimac in unit 3, while the tables "
                    "give unit 2 three independents and unit 3 two. Their lines keep the commission's generic labels."
                ),
                lang="en",
            )
        for measure, value in ((3, printed.cast), (5, printed.invalid), (4, printed.valid)):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        if all(okraj.registered is not None for okraj in here):
            for measure, name in ((2, "registered"), (14, "voters")):
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=measure,
                    value=sum(getattr(okraj, name) for okraj in here),
                )
        for label, option in options.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=printed.votes.get(label, 0))
        for okraj in here:
            unit_id = key("unit", f"{year.day}:{unit}.{okraj.seat}")
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=SLOVENIA,
                scheme_code="si.vo",
                code=f"{year.day}:{unit}.{okraj.seat}",
                area_relation="equals",
                area_id=area("si.vo", f"{unit}.{okraj.seat}"),
                kind="standard",
            )
            p.name(unit_id, okraj.name, "sl", "official")
            for measure, value in (
                (2, okraj.registered),
                (14, okraj.voters),
                (3, okraj.cast),
                (5, okraj.invalid),
                (4, okraj.valid),
            ):
                if value is not None:
                    sets.append(result_set)
                    unit_ids.append(unit_id)
                    option_ids.append(None)
                    measures.append(measure)
                    values.append(value)
            for label, value in okraj.votes.items():
                if label not in options:
                    if value:
                        raise ValueError(f"{year.year} {unit}.{okraj.seat}: {label} has votes but no option")
                    continue
                sets.append(result_set)
                unit_ids.append(unit_id)
                option_ids.append(options[label])
                measures.append(1)
                values.append(value)
    reasons: list[str] = []
    if any(okraj.cast is None for okraj in parsed.okraji):
        reasons.append(
            "the commission prints ballots cast and invalid ballots for each electoral unit but not for its "
            "districts, so the unit's declared ballots have no district values to sum"
        )
    if "accounting.votes_valid" in p.accepted:
        reasons.append(
            "and one district table omits a list's candidate row that the unit's printed list total counts "
            "(see the caveat note on that contest)"
        )
    if reasons:
        p.accept("accounting.declared", "; ".join(reasons))
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )
    return p
