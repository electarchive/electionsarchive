"""Slovenian presidential elections of 1992, 1997, and 2002 (https://www.dvk-rs.si/).

The commission published these elections at three grains, and each package keeps the grain its source has:

- 1992: the Republic Election Commission's report of the result, a PDF with national figures only.
- 1997: the archived result site (https://www.dvk-rs.si/arhivi/vp1997/), by electoral district (volilni okraj).
- 2002: the archived result site (https://www.dvk-rs.si/arhivi/vp2002/), by electoral district, for both rounds. The
  site is a mirror of the commission's election-night pages, so its district tables are an unofficial count that
  stopped just short of every ballot. The official national figures are on the commission's page for the 2002
  election, so each round holds an official set with those figures and a preliminary set with the district rows.

Each election is one package, `si.dvk/pres-<date>`, built on `si.dvk/geo` and `si.dvk/registry` from
`ea.sources.si.dvk`. A district is a reporting unit that equals its district area.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.si.dvk import (
    DVK_SOURCE,
    ELECTORAL_UNITS,
    PRESIDENCY,
    SLOVENIA,
    SOURCE,
    SYSTEMS,
    area,
    bare,
    clean,
    key,
    title,
)

SITE = "https://www.dvk-rs.si"
ARCHIVE = f"{SITE}/arhivi"
PAGE_2002 = (
    f"{SITE}/volitve-in-referendumi/predsednik-republike/volitve-predsednika-rs/volitve-predsednika-rs-leto-2002/"
)
REPORT_1992 = f"{SITE}/fileadmin/user_upload/dokumenti/volitve/predsednika_rs_1992/porocilo_pr_1992.pdf"
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
DISTRICTS = 11


@dataclass(frozen=True)
class Round:
    """One round of a presidential election."""

    day: date
    round: int
    layout: str
    """`1997` for the 1997 site, `2002` for the 2002 site, `report` for the 1992 report."""
    folder: str = ""

    @property
    def year(self) -> int:
        return self.day.year

    def url(self, page: str) -> str:
        return f"{ARCHIVE}/{self.folder}/{page}"

    def pages(self) -> list[str]:
        if self.layout == "1997":
            names = ["rezultati_sl", "rezultati_ve", "rezultati_vo", "udelezba_sl", "udelezba_ve", "udelezba_vo"]
            return [self.url(f"{name}.html") for name in names]
        if self.layout == "2002":
            names = ["rez_sl", "rez_ve", "udel_ve", "udel_vo", *(f"rez_vo{unit}" for unit in ELECTORAL_UNITS)]
            return [PAGE_2002, *(self.url(f"{name}.html") for name in names)]
        return [REPORT_1992]


ROUNDS = [
    Round(date(1992, 12, 6), 1, "report"),
    Round(date(1997, 11, 23), 1, "1997", "vp1997"),
    Round(date(2002, 11, 10), 1, "2002", "vp2002/prvi-krog"),
    Round(date(2002, 12, 1), 2, "2002", "vp2002"),
]


@dataclass
class Okraj:
    unit: int
    seat: int
    name: str
    registered: int | None = None
    voters: int | None = None
    cast: int | None = None
    invalid: int | None = None
    votes: dict[str, int] = field(default_factory=dict)

    @property
    def valid(self) -> int:
        return sum(self.votes.values())


@dataclass
class Totals:
    """A set of national figures: the candidates' votes, in the commission's order, and the counts around them."""

    votes: dict[str, int]
    registered: int | None = None
    voters: int | None = None
    cast: int | None = None
    invalid: int | None = None

    @property
    def valid(self) -> int:
        return sum(self.votes.values())


@dataclass
class Parsed:
    round: Round
    official: Totals
    """The national figures the commission declares as the result."""
    candidates: list[str]
    snapshot: Totals | None = None
    """The national figures the site prints beside its district tables, where those differ from the result."""
    okraji: list[Okraj] = field(default_factory=list)
    units: dict[int, tuple[int | None, int | None, dict[str, int]]] = field(default_factory=dict)
    """Each electoral unit's printed ballots cast, invalid ballots, and votes by candidate."""
    unit_turnout: dict[int, tuple[int, int]] = field(default_factory=dict)
    """Each electoral unit's printed registered voters and voters."""


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


def _decode(path: Path) -> str:
    """The page's text. The 2002 mirror names ISO 8859-2 in its meta tag but stores UTF-8, so UTF-8 comes first."""
    body = path.read_bytes()
    found = re.search(rb"charset=([A-Za-z0-9-]+)", body[:2000])
    for charset in ["utf-8", *([found.group(1).decode("ascii")] if found else []), "cp1250"]:
        try:
            return body.decode(charset)
        except UnicodeDecodeError, LookupError:
            continue
    raise ValueError(f"{path}: unreadable text")


def _page(url: str) -> str:
    return _decode(raw.cached(SOURCE, url).path)


def _tables(url: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(_page(url))
    return parser.tables


def _plain(url: str) -> str:
    """The text of a page without markup."""
    return clean(
        re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", _page(url))).replace("&nbsp;", " ")
    )


def _int(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def _find(pattern: str, text: str) -> int:
    """The number the pattern's one group captures in the text."""
    found = re.search(pattern, text)
    if found is None:
        raise LookupError(f"no match for {pattern}")
    return _int(found.group(1))


def _count(cell: str) -> int:
    """A count in a result cell: the 1997 site prints `3.25 %526` (percent first), the 2002 site `66.121 51,52 %`."""
    percent_first = re.fullmatch(r"\s*[\d.,]+\s*%\s*(\d[\d.]*)\s*", cell)
    if percent_first:
        return _int(percent_first.group(1))
    found = re.match(r"\s*(\d[\d.]*)", cell)
    if found is None:
        raise ValueError(f"no count in {cell!r}")
    return _int(found.group(1))


def fetch() -> None:
    for found in ROUNDS:
        for url in found.pages():
            raw.fetch(SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".html")


# 1992 ----------------------------------------------------------------------------------------------------------------


def _report_1992() -> Parsed:
    text = ""
    with pymupdf.open(raw.cached(SOURCE, REPORT_1992).path) as pdf:
        for page in pdf:
            text += page.get_text() + "\n"
    flat = clean(text)

    def figure(pattern: str) -> int:
        found = re.search(pattern, flat)
        if found is None:
            raise LookupError(f"1992 report: no match for {pattern}")
        return int(re.sub(r"\D", "", found.group(1)))

    votes: dict[str, int] = {}
    for line in text.splitlines():
        row = re.match(r"\|\s*(\d)\.\s*\|([^|]+)\|\s*([\d. ]+?)\s*\|\s*glasov", line)
        if row:
            votes[title(row.group(2))] = int(re.sub(r"\D", "", row.group(3)))
    printed = re.search(r"\|\s*\|\s*\|\s*([\d.]+)\s*\|\s*glasov", text)
    if printed is None or int(re.sub(r"\D", "", printed.group(1))) != sum(votes.values()):
        raise ValueError("1992 report: the candidates' votes do not sum to the printed total")
    registered = figure(r"imelo pravico voliti skupaj ([\d.,]+) volivcev")
    totals = Totals(
        votes,
        registered=registered,
        voters=figure(r"Glasovalo je skupaj ([\d.,]+) volivcev"),
        cast=figure(r"oddanih ([\d.,]+) glasovnic"),
        invalid=figure(r"je bilo ([\d.,]+) glasovnic neveljavnih"),
    )
    return Parsed(ROUNDS[0], totals, list(votes))


def _reconcile_1992(parsed: Parsed) -> dict[str, object]:
    totals = parsed.official
    problems = []
    if totals.cast is None or totals.invalid is None or totals.cast - totals.invalid != totals.valid:
        problems.append("cast - invalid is not the sum of the candidates' votes")
    if len(totals.votes) != 8:
        problems.append(f"{len(totals.votes)} candidates")
    return {
        "candidates": len(totals.votes),
        "registered": totals.registered,
        "voters": totals.voters,
        "cast": totals.cast,
        "invalid": totals.invalid,
        "valid": totals.valid,
        "problems": problems,
    }


# 1997 ----------------------------------------------------------------------------------------------------------------


def _candidate_header(cells: list[str]) -> list[str]:
    return [bare(cell) for cell in cells]


def _unit_blocks(found: Round, page: str) -> dict[int, list[list[str]]]:
    """For each electoral unit, the table of its districts: a header row of candidates and one row per district."""
    blocks: dict[int, list[list[str]]] = {}
    for table in _tables(found.url(page)):
        if table and table[0][:1] == ["Volilni okraj"] and len(table) == 1 + DISTRICTS:
            blocks[len(blocks) + 1] = table
    if sorted(blocks) != list(ELECTORAL_UNITS):
        raise LookupError(f"{found.day}: found district tables for units {sorted(blocks)}")
    return blocks


def _district_name(label: str, seat: int) -> str:
    found = re.fullmatch(rf"{seat}\.\s*(.+)", label)
    if found is None:
        raise LookupError(f"district {seat}: unexpected label {label!r}")
    return clean(found.group(1))


def _load_1997(found: Round) -> Parsed:
    national = _tables(found.url("rezultati_sl.html"))[0]
    votes = {bare(row[1]): _int(row[2]) for row in national[1:] if len(row) >= 3 and row[1]}
    plain = _plain(found.url("rezultati_sl.html"))
    cast = _find(r"Število oddanih glasovnic:\s*([\d.]+)", plain)
    invalid = _find(r"Število neveljavnih glasovnic:\s*([\d.]+)", plain)
    turnout_plain = _plain(found.url("udelezba_sl.html"))
    registered = _find(r"pravico voliti skupaj ([\d.]+) volilcev", turnout_plain)
    voters = _find(r"glasovalo skupaj ([\d.]+) volilcev", turnout_plain)
    parsed = Parsed(found, Totals(votes, registered, voters, cast, invalid), list(votes))
    # unit table
    for table in _tables(found.url("rezultati_ve.html")):
        if table and table[0][:1] == ["Volilna enota"]:
            names = _candidate_header(table[0][1:])
            for row in table[1:9]:
                unit = _find(r"^\s*(\d)", row[0])
                parsed.units[unit] = (None, None, {n: _count(c) for n, c in zip(names, row[1:], strict=True)})
    for unit, block in _unit_blocks(found, "rezultati_vo.html").items():
        names = _candidate_header(block[0][1:])
        for seat, row in enumerate(block[1:], start=1):
            okraj = Okraj(unit, seat, _district_name(row[0], seat))
            okraj.votes = {n: _count(c) for n, c in zip(names, row[1:], strict=True)}
            parsed.okraji.append(okraj)
    for table in _tables(found.url("udelezba_ve.html")):
        if table and table[0][:1] == ["Volilna enota"]:
            for row in table[1:9]:
                unit = _find(r"^\s*(\d)", row[0])
                parsed.unit_turnout[unit] = (_int(row[2]), _int(row[1]))
    turnout = {}
    for unit, block in _turnout_blocks(found).items():
        for seat, row in enumerate(block[1:], start=1):
            turnout[(unit, seat)] = (_int(row[2]), _int(row[1]))
    for okraj in parsed.okraji:
        okraj.registered, okraj.voters = turnout[(okraj.unit, okraj.seat)]
    return parsed


def _turnout_blocks(found: Round) -> dict[int, list[list[str]]]:
    blocks: dict[int, list[list[str]]] = {}
    page = "udelezba_vo.html" if found.layout == "1997" else "udel_vo.html"
    for table in _tables(found.url(page)):
        if table and table[0][:1] == ["Volilni okraj"] and len(table) >= 1 + DISTRICTS:
            blocks[len(blocks) + 1] = table
    if sorted(blocks) != list(ELECTORAL_UNITS):
        raise LookupError(f"{found.day}: found turnout tables for units {sorted(blocks)}")
    return blocks


# 2002 ----------------------------------------------------------------------------------------------------------------

ROMAN = re.compile(r"^[IVX]+$")
LOSSY = "�"


def _district_title(text: str) -> str:
    """A district name from the capitals the 2002 pages print, keeping Roman numerals and hyphenated parts."""
    words = []
    for word in clean(text).split(" "):
        words.append(word if ROMAN.match(word) else "-".join(part.capitalize() for part in word.split("-")))
    return " ".join(words)


def _same(printed: str, wanted: str) -> bool:
    """Whether a printed name matches a clean one; the mirror replaced some accented letters with U+FFFD."""
    a, b = printed.lower(), wanted.lower()
    return len(a) == len(b) and all(x == y or x == LOSSY for x, y in zip(a, b, strict=True))


def _person(name: str) -> str:
    """A candidate's name as the official page prints it: titles dropped, capitalized surnames in title case."""
    return " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w for w in bare(name).split(" "))


def _canon(printed: str, official: list[str]) -> str:
    """The official name of the candidate a mirror page prints, matched on the surname."""
    last = bare(printed).split(" ")[-1]
    found = [name for name in official if _same(last, name.split(" ")[-1])]
    if len(found) != 1:
        raise LookupError(f"2002: {printed!r} matches {found}")
    return found[0]


def _official_2002(found: Round) -> tuple[Totals, list[str]]:
    """The result the commission declares for the round, from its page for the 2002 election."""
    tables = [t for t in _tables(PAGE_2002) if t and t[0][:2] == ["", "Število glasov"]]
    plain = _plain(PAGE_2002)
    if len(tables) != 2:
        raise LookupError(f"2002 page: found {len(tables)} result tables")
    table = tables[found.round - 1]
    names = [_person(row[0]) for row in table[1:]]
    votes = {name: _int(row[1]) for name, row in zip(names, table[1:], strict=True)}
    registered = re.findall(r"imelo pravico glasovati ([\d.]+) volivcev", plain)
    voters = re.findall(r"Glasovalo je skupaj ([\d.]+) volivcev", plain)
    if len(registered) != 2 or len(voters) != 2:
        raise LookupError("2002 page: expected the turnout of two rounds")
    totals = Totals(votes, _int(registered[found.round - 1]), _int(voters[found.round - 1]))
    if found.round == 2:
        ballots = re.search(r"oddanih ([\d.]+) glasovnic\. Neveljavnih je bilo ([\d.]+) glasovnic", plain)
        if ballots is None:
            raise LookupError("2002 page: no second-round ballots")
        totals.cast, totals.invalid = _int(ballots.group(1)), _int(ballots.group(2))
    return totals, names


def _national_2002(found: Round, official: list[str]) -> Totals:
    plain = _plain(found.url("rez_sl.html"))
    got = {}
    for label, pattern in (
        ("cast", r"Število preštetih glasovnic: ([\d.]+)"),
        ("invalid", r"Število neveljavnih glasovnic: ([\d.]+)"),
    ):
        match = re.search(pattern.replace("š", ".").replace("Š", "."), plain)
        if match is None:
            raise LookupError(f"{found.day}: no {label} ballots on the national page")
        got[label] = _int(match.group(1))
    table = next(t for t in _tables(found.url("rez_sl.html")) if t and t[0][:2] == ["Mesto", "Kandidat"])
    votes = {_canon(row[1], official): _int(row[2]) for row in table[1:] if len(row) >= 3 and row[1]}
    return Totals(votes, cast=got["cast"], invalid=got["invalid"])


def _load_2002(found: Round) -> Parsed:
    official, names = _official_2002(found)
    parsed = Parsed(found, official, names, _national_2002(found, names))
    # electoral unit table: Slovenia and the 8 units as columns
    table = next(t for t in _tables(found.url("rez_ve.html")) if any(row[:2] == ["", "Slovenija"] for row in t))
    head = next(row for row in table if row[:2] == ["", "Slovenija"])
    columns = len(head)
    if columns != 2 + len(ELECTORAL_UNITS):
        raise LookupError(f"{found.day}: unit table has {columns} columns")
    unit_rows = [row for row in table if row is not head]
    cast, invalid, *candidate_rows = unit_rows
    _labels(found, cast[0], invalid[0])
    for slot, unit in enumerate(ELECTORAL_UNITS, start=2):
        votes = {_canon(row[0], names): _count(row[slot]) for row in candidate_rows}
        parsed.units[unit] = (_count(cast[slot]), _count(invalid[slot]), votes)
    for table in _tables(found.url("udel_ve.html")):
        if table and table[0][:1] == ["Volilna enota"]:
            for unit, row in zip(ELECTORAL_UNITS, table[1:9], strict=True):
                parsed.unit_turnout[unit] = (_int(row[2]), _int(row[1]))
    plain = _plain(found.url("udel_ve.html"))
    registered = re.search(r"pravico glasovati skupaj ([\d.]+) volilcev", plain)
    voters = re.search(r"glasovalo skupaj ([\d.]+) volilcev", plain)
    if registered is None or voters is None or parsed.snapshot is None:
        raise LookupError(f"{found.day}: no national turnout on the turnout page")
    parsed.snapshot.registered, parsed.snapshot.voters = _int(registered.group(1)), _int(voters.group(1))
    turnout = _turnout_blocks(found)
    canon = _district_names()
    for unit in ELECTORAL_UNITS:
        sums, districts, cast_row, invalid_row, *candidate_rows = _district_table(found, unit)
        for seat, printed in enumerate(districts, start=1):
            name = canon[(unit, seat)]
            label = turnout[unit][seat]
            if not _same(printed, name) or not _same(_district_title(label[0]), name):
                raise LookupError(f"{found.day} unit {unit}.{seat}: {printed!r}, {label[0]!r} are not {name!r}")
            okraj = Okraj(unit, seat, name)
            okraj.cast = _count(cast_row[seat])
            okraj.invalid = _count(invalid_row[seat])
            okraj.votes = {_canon(row[0], names): _count(row[seat]) for row in candidate_rows}
            okraj.voters, okraj.registered = _int(label[1]), _int(label[2])
            parsed.okraji.append(okraj)
        if (_count(sums[0]), _count(sums[1])) != parsed.units[unit][:2]:
            raise ValueError(f"{found.day} unit {unit}: the Skupaj column disagrees with the unit table")
    return parsed


def _district_names() -> dict[tuple[int, int], str]:
    """The 88 district names as the second round's turnout page prints them, the one 2002 page free of lost letters."""
    found = ROUNDS[3]
    names: dict[tuple[int, int], str] = {}
    for unit, block in _turnout_blocks(found).items():
        for seat, row in enumerate(block[1:], start=1):
            name = _district_title(row[0])
            if LOSSY in name:
                raise ValueError(f"2002 unit {unit}.{seat}: {name!r} has a lost letter")
            names[(unit, seat)] = name
    return names


def _labels(found: Round, cast: str, invalid: str) -> None:
    """Check the first two rows of a 2002 table are the ballots counted and the invalid ballots."""
    if not (
        re.fullmatch(r".t\. pre.tetih glasovnic", cast)
        and re.fullmatch(r".t\. neveljavnih glasovnic", invalid, re.IGNORECASE)
    ):
        raise LookupError(f"{found.day}: unexpected rows {cast!r}, {invalid!r}")


def _district_table(found: Round, unit: int) -> list[list[str]]:
    """A unit's district table as [the Skupaj cast and invalid], [district names], the cast row, the invalid row, then
    one row per candidate; each row after the names holds its label and then one cell per district."""
    table = next(t for t in _tables(found.url(f"rez_vo{unit}.html")) if any(row[:2] == ["", "Skupaj"] for row in t))
    start = next(i for i, row in enumerate(table) if row[:2] == ["", "Skupaj"])
    head, body = table[start], table[start + 1 :]
    names = [_district_title(clean(cell)) for cell in head[2:]]
    if len(names) != DISTRICTS:
        raise LookupError(f"{found.day} unit {unit}: {len(names)} districts")
    rows = [row for row in body if len(row) == len(head)]
    if len(rows) != len(body) or len(rows) < 3:
        raise LookupError(f"{found.day} unit {unit}: ragged district table")
    cast, invalid = rows[0], rows[1]
    _labels(found, cast[0], invalid[0])
    return [
        [cast[1], invalid[1]],
        names,
        [cast[0], *cast[2:]],
        [invalid[0], *invalid[2:]],
        *[[row[0], *row[2:]] for row in rows[2:]],
    ]


def load(found: Round) -> Parsed:
    if found.layout == "report":
        return _report_1992()
    return _load_1997(found) if found.layout == "1997" else _load_2002(found)


def reconcile(parsed: Parsed) -> dict[str, object]:
    """Compare the district rows with the unit and national figures the site prints."""
    if parsed.round.layout == "report":
        return _reconcile_1992(parsed)
    problems: list[str] = []
    shown = parsed.snapshot or parsed.official
    if len(parsed.okraji) != 88:
        problems.append(f"{len(parsed.okraji)} districts")
    for unit in ELECTORAL_UNITS:
        here = [okraj for okraj in parsed.okraji if okraj.unit == unit]
        cast, invalid, votes = parsed.units[unit]
        if cast is not None and cast != sum(okraj.cast or 0 for okraj in here):
            problems.append(f"unit {unit} cast: districts {sum(okraj.cast or 0 for okraj in here):,}, printed {cast:,}")
        if invalid is not None and invalid != sum(okraj.invalid or 0 for okraj in here):
            problems.append(
                f"unit {unit} invalid: districts {sum(o.invalid or 0 for o in here):,}, printed {invalid:,}"
            )
        for candidate in parsed.candidates:
            got = sum(okraj.votes[candidate] for okraj in here)
            if got != votes[candidate]:
                problems.append(f"unit {unit} {candidate}: districts {got:,}, printed {votes[candidate]:,}")
        registered, voters = parsed.unit_turnout[unit]
        if registered != sum(okraj.registered or 0 for okraj in here):
            problems.append(
                f"unit {unit} registered: districts {sum(o.registered or 0 for o in here):,}, printed {registered:,}"
            )
        if voters != sum(okraj.voters or 0 for okraj in here):
            problems.append(f"unit {unit} voters: districts {sum(o.voters or 0 for o in here):,}, printed {voters:,}")
    for candidate, wanted in shown.votes.items():
        got = sum(votes[candidate] for _, _, votes in parsed.units.values())
        if got != wanted:
            problems.append(f"Slovenia {candidate}: units {got:,}, printed {wanted:,}")
    if shown.cast is not None and parsed.round.layout == "2002":
        total = sum(okraj.cast or 0 for okraj in parsed.okraji)
        if total != shown.cast:
            problems.append(f"Slovenia cast: districts {total:,}, printed {shown.cast:,}")
    for okraj in parsed.okraji:
        if okraj.cast is not None and okraj.invalid is not None and okraj.cast - okraj.invalid != okraj.valid:
            problems.append(
                f"{okraj.unit}.{okraj.seat}: cast - invalid {okraj.cast - okraj.invalid} != votes {okraj.valid}"
            )
    notes = []
    if shown.cast is not None and shown.invalid is not None and shown.cast - shown.invalid != shown.valid:
        notes.append(f"printed cast - invalid is {shown.cast - shown.invalid:,}, printed valid {shown.valid:,}")
    if parsed.round.layout == "1997":
        for name, wanted, got in (
            ("registered", parsed.official.registered, sum(okraj.registered or 0 for okraj in parsed.okraji)),
            ("voters", parsed.official.voters, sum(okraj.voters or 0 for okraj in parsed.okraji)),
        ):
            if wanted != got:
                problems.append(f"Slovenia {name}: districts {got:,}, printed {wanted:,}")
    gap = {
        "valid": parsed.official.valid - sum(okraj.valid for okraj in parsed.okraji),
        "registered": (parsed.official.registered or 0) - sum(okraj.registered or 0 for okraj in parsed.okraji),
        "voters": (parsed.official.voters or 0) - sum(okraj.voters or 0 for okraj in parsed.okraji),
    }
    return {
        "districts": len(parsed.okraji),
        "valid": sum(okraj.valid for okraj in parsed.okraji),
        "official valid": parsed.official.valid,
        "official minus districts": gap,
        "printed gaps": notes,
        "problems": problems,
    }


def slug(found: Round) -> str:
    return f"si.dvk/pres-{found.day}"


def packages() -> list[str]:
    return [slug(found) for found in ROUNDS]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    for found in ROUNDS:
        parsed = load(found)
        print(f"{slug(found)}: {reconcile(parsed)}", flush=True)
        election(parsed).write()


def _title(found: Round) -> str:
    if found.year != 2002:
        return f"{found.year} Slovenian presidential election"
    return f"{found.year} Slovenian presidential election, {'first' if found.round == 1 else 'second'} round"


def _method(parsed: Parsed) -> str:
    found = parsed.round
    if found.layout == "report":
        return (
            "The Republic Election Commission's report of the result of the 1992 presidential election, read from "
            "the PDF the State Election Commission (Državna volilna komisija) publishes. The report prints the "
            "national figures only: the voters on the roll, the voters who voted, the ballots cast, the invalid "
            "ballots, and each candidate's votes. They are stored as the declared figures of the contest. The "
            "commission published no result by electoral unit or district, so there are no reporting units."
        )
    if found.layout == "1997":
        return (
            "Published by the State Election Commission (Državna volilna komisija) on its archived results site for "
            "the 1997 election, by electoral district (volilni okraj), the finest level it published. Each of the "
            "88 districts is a reporting unit, numbered within its electoral unit in the order the site lists them "
            "and tied to the district area of that number. Registered voters and voters come from the site's "
            "turnout pages, and valid votes are the sum of the candidates' votes. The site prints ballots cast, "
            "invalid ballots, and valid ballots for Slovenia only. Its printed ballots cast less its printed invalid "
            "ballots exceed its printed valid ballots by 1,264, and all three are stored as printed. The district "
            "rows sum to the printed national votes, registered voters, and voters."
        )
    return (
        "The result the State Election Commission (Državna volilna komisija) declares for the "
        f"{found.day:%-d %B %Y} round on its page for the 2002 election: the voters on the roll, the voters who "
        "voted, each candidate's votes"
        + (", the ballots cast, and the invalid ballots" if found.round == 2 else "")
        + ". The page prints no result by unit or district; the preliminary tabulation of the same round holds those."
    )


def _snapshot_method(parsed: Parsed) -> str:
    found = parsed.round
    return (
        "The commission's archived results site for the 2002 election keeps the pages published while the votes "
        f"were counted. The pages for the {found.day:%-d %B %Y} round are headed as unofficial results, and print "
        "ballots counted from "
        f"{parsed.snapshot.cast if parsed.snapshot else 0:,} ballots, a count that stopped short of the final one, "
        "so they are stored as a preliminary tabulation. The turnout pages print the registered voters and the voters "
        "at 19:00 on the election day. Each of the 88 districts is a reporting unit, numbered within its electoral "
        "unit in the order the site lists them and tied to the district area of that number. The national figures "
        "the pages print are stored as the declared figures of this tabulation, and the district rows sum to them. "
        "The official result is the other result set of this contest."
    )


def election(parsed: Parsed) -> Package:
    found = parsed.round
    first = next(r for r in ROUNDS if r.year == found.year and r.round == 1)
    p = Package(
        slug(found),
        SOURCE,
        f"The {_title(found)}" + (" by electoral district" if found.layout != "report" else ", national figures"),
        depends=[
            "si.dvk/geo",
            "si.dvk/registry",
            *([] if found.round == 1 else [slug(first)]),
        ],
    )
    p.external.add(SLOVENIA)
    for url in found.pages():
        if not (
            found.round == 2 and url == PAGE_2002
        ):  # the first round's package cites it, and this one depends on it
            p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)
    election_id = key("election", "pres", found.day)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="general" if found.round == 1 else "runoff",
        date_from=found.day,
        date_to=found.day,
        status="held",
    )
    p.name(election_id, _title(found), "en", "official")
    suffix = f", {found.round}. krog" if found.year == 2002 else ""
    p.name(election_id, f"Volitve predsednika republike {found.year}{suffix}", "sl", "native", False)
    p.add("slug", path=f"si/{found.day}-presidential", entity_id=election_id, is_current=True)
    contest = key("contest", found.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=found.round,
        previous_round_id=None if found.round == 1 else key("contest", first.day),
        electoral_system_id=SYSTEMS["pres"],
        default_result_kind="counted",
    )
    p.name(contest, _title(found), "en", "official")
    p.add("slug", path=f"si/{found.day}-presidential/president", entity_id=contest, is_current=True)
    official_set = key("result_set", str(found.day))
    p.add(
        "result_set",
        id=official_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DVK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", official_set),
        entity_id=official_set,
        kind="methodology",
        body_md=_method(parsed),
        lang="en",
    )
    options: dict[str, str] = {}
    ballot = [*parsed.official.votes] if found.layout != "1997" else parsed.candidates
    for index, name in enumerate(ballot, start=1):
        person = key("person", f"pres:{found.year}:{name}")
        option = options[name] = key("option", found.day, index, name)
        if found.round == 1:
            p.add("person", id=person)
            p.name(person, name, "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=index,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", found.day, index, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
    _declare(p, official_set, parsed.official, options)
    _national(p, parsed, official_set, options)
    if found.layout == "report":
        return p
    sets = [official_set]
    if found.layout == "2002" and parsed.snapshot is not None:
        shown = key("result_set", f"{found.day}:preliminary")
        p.add(
            "result_set",
            id=shown,
            election_id=election_id,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=DVK_SOURCE,
            status="active",
            is_default=True,
            completeness=parsed.snapshot.valid / parsed.official.valid,
        )
        p.add(
            "note",
            id=key("note", shown),
            entity_id=shown,
            kind="methodology",
            body_md=_snapshot_method(parsed),
            lang="en",
        )
        _declare(p, shown, parsed.snapshot, options)
        sets = [shown]
    _units(p, parsed, sets[0], options)
    _accept(p, parsed)
    return p


def _declare(p: Package, result_set: str, totals: Totals, options: dict[str, str]) -> None:
    for measure, value in (
        (2, totals.registered),
        (14, totals.voters),
        (3, totals.cast),
        (5, totals.invalid),
        (4, totals.valid),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for name, option in options.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=totals.votes[name])


def _national(p: Package, parsed: Parsed, result_set: str, options: dict[str, str]) -> None:
    """The declared national figures as the values of one unit for Slovenia, which has no district to sum."""
    found = parsed.round
    unit_id = key("unit", f"{found.day}:national")
    p.add(
        "reporting_unit",
        id=unit_id,
        polity_id=SLOVENIA,
        scheme_code="si.ve",
        code=f"{found.day}:national",
        area_relation="unlocated",
        area_id=None,
        kind="aggregate",
    )
    p.name(unit_id, "Slovenija", "sl", "official")
    totals = parsed.official
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    pairs = [(None, 2, totals.registered), (None, 14, totals.voters), (None, 3, totals.cast), (None, 5, totals.invalid)]
    pairs += [(None, 4, totals.valid), *((option, 1, totals.votes[name]) for name, option in options.items())]
    for option, measure, value in pairs:
        if value is not None:
            sets.append(result_set)
            unit_ids.append(unit_id)
            option_ids.append(option)
            measures.append(measure)
            values.append(value)
    _frame(p, sets, unit_ids, option_ids, measures, values)


def _accept(p: Package, parsed: Parsed) -> None:
    if parsed.round.layout != "1997":
        return
    reason = (
        "the commission's printed national ballots cast less its printed invalid ballots exceed its printed valid "
        "ballots by 1,264; the three figures are stored as printed, and the district rows hold no ballots cast or "
        "invalid ballots, which the site does not print by district"
    )
    p.accept("accounting.declared", reason)
    p.accept("accounting.ballots_sum", reason)


def _units(p: Package, parsed: Parsed, result_set: str, options: dict[str, str]) -> None:
    found = parsed.round
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    for okraj in parsed.okraji:
        unit_id = key("unit", f"{found.day}:{okraj.unit}.{okraj.seat}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.vo",
            code=f"{found.day}:{okraj.unit}.{okraj.seat}",
            area_relation="equals",
            area_id=area("si.vo", f"{okraj.unit}.{okraj.seat}"),
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
        for name, option in options.items():
            sets.append(result_set)
            unit_ids.append(unit_id)
            option_ids.append(option)
            measures.append(1)
            values.append(okraj.votes[name])
    _frame(p, sets, unit_ids, option_ids, measures, values)


def _frame(
    p: Package,
    sets: list[str],
    unit_ids: list[str],
    option_ids: list[str | None],
    measures: list[int],
    values: list[int],
) -> None:
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
