"""The IEBC's "Data Report of 2017 Elections" and its fresh presidential tables, read from the PDFs' text layers.

The report prints every ballot as a table whose columns sit at fixed positions on each page. Each page repeats its
header, so the columns are taken from the header's own word positions, and each word is given to the column it starts
in. A row is one printed line with a vote count at its right edge or, where a table numbers its rows, a line that
starts with the row number; the lines under it that only wrap a name belong to that row.
"""

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import cast

import pymupdf

# The first page of each ballot's table in the report, and the last.
PAGES = {
    "president": (18, 20),
    "assembly": (28, 127),
    "woman": (129, 140),
    "senate": (142, 153),
    "governor": (155, 165),
    "ward": (167, 685),
}
# The header words, left to right, that start each column of a ballot's table.
HEADERS: dict[str, list[tuple[str, str]]] = {
    "assembly": [
        ("county_code", "COUNTY"),
        ("county", "COUNTY"),
        ("constituency_code", "CONSTITUENCY"),
        ("constituency", "CONSTITUENCY"),
        ("surname", "SURNAME"),
        ("names", "OTHER"),
        ("party_code", "PARTY"),
        ("party", "POLITICAL"),
        ("abbreviation", "ABBRV"),
        ("votes", "VOTES"),
    ],
    "woman": [
        ("no", "NO"),
        ("surname", "SURNAME"),
        ("names", "OTHER"),
        ("county_code", "COUNTY"),
        ("county", "COUNTY"),
        ("party_code", "PARTY"),
        ("party", "POLITICAL"),
        ("abbreviation", "ABBRV"),
        ("votes", "VOTES"),
    ],
    "governor": [
        ("no", "NO"),
        ("surname", "SURNAME"),
        ("names", "OTHER"),
        ("mate_surname", "RUNNING"),
        ("mate_names", "RUNNING"),
        ("county_code", "COUNTY"),
        ("county", "COUNTY"),
        ("party_code", "PARTY"),
        ("party", "POLITICAL"),
        ("abbreviation", "ABBRV"),
        ("votes", "VOTES"),
    ],
    "ward": [
        ("no", "NO"),
        ("surname", "SURNAME"),
        ("names", "OTHER"),
        ("county_code", "COUNTY"),
        ("county", "COUNTY"),
        ("constituency_code", "CONST"),
        ("constituency", "CONSTITUENCY"),
        ("ward_code", "WARD"),
        ("ward", "WARD"),
        ("party_code", "PARTY"),
        ("party", "POLITICAL"),
        ("abbreviation", "ABBRV"),
        ("votes", "VOTES"),
    ],
}
HEADERS["senate"] = HEADERS["woman"]
NUMBER = re.compile(r"^\d{1,3}(,\d{3})*$|^\d+$")
LINE = 3.0


@dataclass
class Row:
    page: int
    no: str = ""
    cells: dict[str, list[str]] = field(default_factory=dict)
    raw_votes: str = ""
    totals: list[str] = field(default_factory=list)
    y: float = 0.0

    def get(self, name: str) -> str:
        return " ".join(self.cells.get(name, []))


def _lines(page: pymupdf.Page) -> list[list[tuple[float, float, str]]]:
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    lines: list[list[tuple[float, float, str]]] = []
    top = None
    for w in words:
        if top is None or abs(w[1] - top) > LINE:
            lines.append([])
            top = w[1]
        lines[-1].append((w[0], w[1], str(w[4])))
    return [sorted(line) for line in lines]


def _columns(header: list[tuple[float, float, str]], spec: list[tuple[str, str]]) -> list[tuple[str, float]] | None:
    found: list[tuple[str, float]] = []
    last = -1.0
    words = sorted(header)
    for name, token in spec:
        hit = next((w for w in words if w[2] == token and w[0] > last), None)
        if hit is None:
            return None
        found.append((name, hit[0]))
        last = hit[0]
    return found


def _split(line: list[tuple[float, float, str]], columns: list[tuple[str, float]]) -> dict[str, list[str]]:
    cells: dict[str, list[str]] = {}
    for x, _, text in line:
        name = columns[0][0]
        for column, start in columns:
            if x + 4 >= start:
                name = column
        cells.setdefault(name, []).append(text)
    return cells


def _first(lines: list[list[tuple[float, float, str]]], numbered: bool) -> int:
    for index, line in enumerate(lines):
        if len(line) < 6:
            continue
        if numbered and line[0][0] < 120 and re.match(r"^\d+$", line[0][2]):
            return index
        if not numbered and NUMBER.match(line[-1][2]) and line[-1][0] > 600:
            return index
    raise ValueError("no row on the page")


def _is_row(line: list[tuple[float, float, str]], columns: list[tuple[str, float]], numbered: bool) -> bool:
    if numbered:
        return line[0][0] < columns[1][1] - 4 and bool(re.match(r"^\d+$", line[0][2]))
    return bool(re.match(r"^[\d,.]+$|^-$", line[-1][2])) and line[-1][0] > dict(columns)["votes"] - 35 and len(line) > 5


def _numbered(page: pymupdf.Page, number: int, columns: list[tuple[str, float]], start_y: float) -> list[Row]:
    """The rows of a table that numbers them: every word belongs to the nearest row number above it."""
    words = [w for w in page.get_text("words") if w[1] > start_y - 2 and w[1] < 545]
    surname = dict(columns)["surname"]
    anchors = sorted((w[1], w) for w in words if w[0] < surname - 4 and re.match(r"^\d+$", w[4]))
    votes_x = dict(columns)["votes"] - 35
    out: list[Row] = []
    for y, word in anchors:
        row = Row(number, no=str(word[4]), y=y)
        out.append(row)
    if not out:
        return out
    found: list[list[tuple[float, float, str]]] = [[] for _ in out]
    for w in words:
        if w[0] < surname - 4 and re.match(r"^\d+$", w[4]):
            continue
        index = max((i for i, row in enumerate(out) if row.y <= w[1] + 4), default=None)
        if index is not None:
            found[index].append((w[1], w[0], str(w[4])))
    for row, items in zip(out, found, strict=True):
        figures = [t for t in sorted(items) if t[1] > votes_x and re.match(r"^[\d,.]+$|^-$", t[2])]
        text = sorted((t for t in items if t not in figures), key=lambda t: (round(t[0] / 3), t[1]))
        row.cells = _split([(t[1], t[0], t[2]) for t in text], columns)
        if figures:
            row.raw_votes = figures[0][2]
            row.totals = [t[2] for t in figures[1:]]
    return out


def rows(document: pymupdf.Document, ballot: str) -> list[Row]:
    """The printed rows of one ballot's table, each with its cells and the votes at its right edge."""
    first, last = PAGES[ballot]
    spec = HEADERS[ballot]
    numbered = spec[0][0] == "no"
    out: list[Row] = []
    columns: list[tuple[str, float]] | None = None
    for number in range(first, last + 1):
        lines = _lines(document[number - 1])
        start = _first(lines, numbered)
        found = _columns([w for line in lines[:start] for w in line], spec)
        if found is not None:
            columns = found
        if columns is None:
            raise ValueError(f"no header on page {number}")
        if numbered:
            out.extend(_numbered(document[number - 1], number, columns, lines[start][0][1] - 6))
            continue
        current: Row | None = None
        for line in lines[start:]:
            if line[0][2] == "Page":
                continue
            figures = [w for w in line if w[0] > dict(columns)["votes"] - 35 and re.match(r"^[\d,.]+$|^-$", w[2])]
            text = [w for w in line if w not in figures]
            if _is_row(line, columns, numbered):
                row = Row(number, y=line[0][1])
                row.cells = _split(text, columns)
                if figures:
                    row.raw_votes = figures[0][2]
                    row.totals = [w[2] for w in figures[1:]]
                out.append(row)
                current = row
            elif current is not None:
                for name, parts in _split(text, columns).items():
                    current.cells.setdefault(name, []).extend(parts)
    return out


def number(text: str) -> int | None:
    cleaned = text.replace(",", "").replace(".", "")
    return int(cleaned) if cleaned.isdigit() else None


@dataclass(frozen=True)
class Entry:
    """One candidate's printed line: the area it belongs to, who stood, for which party, and the votes."""

    ballot: str
    page: int
    no: int
    area: str
    surname: str
    names: str
    party: str
    mate_surname: str
    mate_names: str
    votes: int | None
    total: int | None


def clean(text: str) -> str:
    """Names as the text layer reads them, with the digits its recognition put in place of letters undone."""
    words = []
    for word in text.split():
        if re.search(r"[A-Za-z]", word):
            word = word.replace("1", "I").replace("0", "O")
        words.append(word)
    return " ".join(words)


def tokens(text: str) -> str:
    return " ".join(sorted(re.findall(r"[A-Z]+", text.upper())))


def like(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, tokens(a), tokens(b)).ratio()


def code(text: str, width: int) -> str:
    return text.zfill(width) if text.isdigit() else text


def abbreviated(row: Row) -> str:
    return row.get("abbreviation").replace("- ", "-")


def parties(rows_by_ballot: dict[str, list[Row]]) -> dict[str, tuple[str, str]]:
    """Each party's three-digit code with its abbreviation and name, as most of its printed lines read.

    A line that prints the code in full also prints the name unwrapped, so only those give names. A party that has no
    such line keeps its abbreviation as its name.
    """
    names: dict[str, Counter[tuple[str, str]]] = {}
    abbreviations: dict[str, Counter[str]] = {}
    for rows_ in rows_by_ballot.values():
        for row in rows_:
            c = code(row.get("party_code"), 3)
            if c.isdigit() and len(c) == 3 and abbreviated(row) and not row.get("party").startswith("INDEPENDENT"):
                abbreviations.setdefault(c, Counter())[abbreviated(row)] += 1
                if len(row.get("party_code")) == 3:
                    names.setdefault(c, Counter())[(abbreviated(row), row.get("party"))] += 1
    table: dict[str, tuple[str, str]] = {}
    for c, counter in abbreviations.items():
        abbreviation = counter.most_common(1)[0][0]
        named = Counter({n: k for (a, n), k in names.get(c, Counter()).items() if a == abbreviation and n})
        table[c] = (abbreviation, named.most_common(1)[0][0] if named else abbreviation)
    return table


def entries(
    ballot: str,
    rows_: list[Row],
    counties: dict[str, str],
    constituencies: dict[str, tuple[str, str]],
    wards: dict[str, tuple[str, str]],
    parties_: dict[str, tuple[str, str]],
) -> list[Entry]:
    """The ballot's lines with each area settled against the commission's lists and each party by its code."""
    by_abbreviation = {abbreviation: c for c, (abbreviation, _) in parties_.items()}
    out: list[Entry] = []
    previous = ""
    rows_ = repair(ballot, rows_)
    for index, row in enumerate(rows_):
        if ballot in ("woman", "senate", "governor"):
            printed = code(row.get("county_code"), 3)
            if printed in counties and like(row.get("county"), counties[printed]) >= 0.6:
                area = printed
            else:
                found = [c for c, n in counties.items() if row.get("county") and like(row.get("county"), n) >= 0.9]
                area = found[0] if found else previous
        elif ballot == "assembly":
            printed = code(row.get("constituency_code"), 3)
            area = printed
            name = row.get("constituency")
            if printed not in constituencies or (name and like(name, constituencies[printed][1]) < 0.5):
                found = [c for c, (_, n) in constituencies.items() if name and tokens(name) == tokens(n)]
                if len(found) == 1:
                    area = found[0]
        else:
            printed = code(row.get("ward_code"), 4)
            name = row.get("ward")
            area = printed
            if printed not in wards or like(name, wards[printed][1]) < 0.7:
                follower = next((code(r.get("ward_code"), 4) for r in rows_[index + 1 :] if r.get("ward_code")), "")
                if previous and like(name, wards[previous][1]) >= 0.7:
                    area = previous
                elif follower in wards and like(name, wards[follower][1]) >= 0.7:
                    area = follower
                else:
                    raise ValueError(f"ward {printed} {name} on page {row.page} is not on the commission's list")
        previous = area
        party = code(row.get("party_code"), 3)
        if party not in parties_:
            if (
                party == "IND"
                or row.get("party").startswith("INDEPENDENT")
                or party.replace("1", "I") == "IND"
                or (not party and not row.get("party") and abbreviated(row) not in by_abbreviation)
            ):
                party = "IND"
            else:
                party = by_abbreviation.get(abbreviated(row), party)
        if party not in parties_ and party != "IND":
            raise ValueError(f"{ballot} line {row.no} on page {row.page} names the unknown party {party!r}")
        votes = row.raw_votes
        out.append(
            Entry(
                ballot,
                row.page,
                int(row.no) if row.no.isdigit() else 0,
                area,
                clean(row.get("surname")),
                clean(row.get("names")),
                party,
                clean(row.get("mate_surname")),
                clean(row.get("mate_names")),
                0 if votes == "-" else number(votes),
                number(row.totals[0]) if row.totals else None,
            )
        )
    return out


def repair(ballot: str, rows_: list[Row]) -> list[Row]:
    """Split the one pair of lines the text layer merged: the Wajir senate candidates Hussein and Keynan (page 144)."""
    if ballot != "senate":
        return rows_
    out: list[Row] = []
    skip = False
    for row in rows_:
        if skip:
            skip = False
            continue
        if row.get("party_code") == "012 048":
            common = {"county_code": ["008"], "county": ["WAJIR"]}
            first = Row(row.page, "67", {**common, "surname": ["HUSSEIN"], "names": ["ADAN", "NOOR"]}, "421", [], row.y)
            first.cells |= {"party_code": ["012"], "abbreviation": ["WDM-K"], "party": ["WIPER"]}
            second = Row(
                row.page, "68", {**common, "surname": ["KEYNAN"], "names": ["WARSAME", "HUSSEIN"]}, "951", [], row.y
            )
            second.cells |= {"party_code": ["048"], "abbreviation": ["EFP"], "party": ["ECONOMIC"]}
            out += [first, second]
            skip = True
        else:
            out.append(row)
    return out


CANDIDATES = (
    "John Ekuru Longoggy Aukot",
    "Mohamed Abduba Dida",
    "Shakhalaga Khwa Jirongo",
    "Japheth Kavinga Kaluyu",
    "Uhuru Kenyatta",
    "Michael Wainaina Mwaura",
    "Joseph William Nthiga Nyagah",
    "Raila Odinga",
)
CELL = r"(?:[\d,.]+|-|no)"


@dataclass(frozen=True)
class County:
    """A county's printed presidential line: its registered voters, each candidate's votes, the valid and rejected.

    A figure the table prints as the word "no" has no value.
    """

    name: str
    registered: int
    votes: tuple[int | None, ...] | None
    valid: int | None
    rejected: int | None


def _cell(text: str) -> int | None:
    if text == "-":
        return 0
    return number(text) if text != "no" else None


def _line(name: str, cells: list[str]) -> County:
    values = [_cell(c) for c in cells]
    if len(values) != 11:
        raise ValueError(f"{name}: {len(values)} figures on its line, not 11")
    if values[0] is None or values[9] is None or values[10] is None:
        raise ValueError(f"{name}: an unreadable registered, valid, or rejected figure")
    return County(name, values[0], tuple(values[1:9]), values[9], values[10])


def counties(text: str, total: str) -> tuple[list[County], County]:
    """The county lines of a presidential table and its national line."""
    out: list[County] = []
    national: County | None = None
    pattern = re.compile(rf"^\s*(?:(\d{{1,2}})\s+)?([A-Z][A-Z'/\- ]*?)\s{{2,}}((?:{CELL}\s+)*{CELL})\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        no, name, figures = match.groups()
        cells = figures.split()
        name = re.sub(r"\s*COUNTY TOTAL$", "", name.strip())
        if len(cells) == 1 and "COUNTY TOTAL" in line:
            # A county where no one voted on the day: the table prints only its registered voters.
            out.append(County(name, cast(int, number(cells[0])), None, None, None))
        elif len(cells) != 11:
            continue
        elif name == total:
            national = _line(name, cells)
        elif no or "COUNTY TOTAL" in line:
            out.append(_line(name, cells))
    if national is None:
        raise ValueError("the national line is missing")
    return out, national
