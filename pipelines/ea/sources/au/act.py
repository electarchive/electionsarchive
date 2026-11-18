"""Australian Capital Territory Legislative Assembly elections from Elections ACT, 1989 to 2024.

The Commission publishes a statistics workbook for most elections, with first preference votes for each candidate at
each polling place. Where it does not, the results pages and the printed statistics book give the votes by electorate.
"""

import functools
import re
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid5

import fastexcel
import xlrd

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.act"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
PAGE = "https://www.elections.act.gov.au/elections_and_voting/past_act_legislative_assembly_elections"
LICENSE = "Creative Commons Attribution 4.0 International"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Elections ACT, Australian Capital Territory"
ASSETS = "https://www.elections.act.gov.au/__data/assets"
BROWSER = "browser://20261117-055949-88dfba"
DATES_URL = "https://www.data.act.gov.au/resource/gypk-fkpc.json"

FILES = {
    "html-1989": f"{BROWSER}/act-1989-results-pages.html",
    "html-1992": f"{BROWSER}/act-1992-results-pages.html",
    "html-1995": f"{BROWSER}/act-1995-results-pages.html",
    "html-1998": f"{BROWSER}/act-1998-results-pages.html",
    "html-2001": f"{BROWSER}/act-2001-results-pages.html",
    "html-2004": f"{BROWSER}/act-2004-results-pages.html",
    "html-2008": f"{BROWSER}/act-2008-results-pages.html",
    "html-2012": f"{BROWSER}/act-2012-results-pages.html",
    "html-2016-2020": f"{BROWSER}/act-2020-2016-list-of-elected-candidates.html",
    "html-2024": "https://www.elections.act.gov.au/elections/previous-assembly-elections/2024-election/"
    "2020-list-of-elected-candidates",
    "book-2001": f"{ASSETS}/excel_doc/0004/2508862/2001-first-preferences.xls",
    "book-2004": f"{ASSETS}/excel_doc/0009/2508777/2004-first-preferences-by-polling-place.xls",
    "pdf-2008": f"{ASSETS}/pdf_file/0009/2466972/Election-Statistics-2008-publication.pdf",
    "book-2012": f"{ASSETS}/excel_doc/0008/2466116/2012_Statistics_book.xlsx",
    "book-2016": f"{ASSETS}/excel_doc/0006/2462046/Election-Statistics-2016.xlsx",
    "book-2020": f"{ASSETS}/excel_doc/0008/2472470/Election-Statistics-2020-election.xlsx",
    "book-2024": f"{ASSETS}/excel_doc/0006/2799717/Stats-Book-2024.XLSX",
}
DAYS = {
    1989: date(1989, 3, 4),
    1992: date(1992, 2, 15),
    1995: date(1995, 2, 18),
    1998: date(1998, 2, 21),
    2001: date(2001, 10, 20),
    2004: date(2004, 10, 16),
    2008: date(2008, 10, 18),
    2012: date(2012, 10, 20),
    2016: date(2016, 10, 15),
    2020: date(2020, 10, 17),
    2024: date(2024, 10, 19),
}
BOOKS = {2001: "xls", 2004: "xls", 2012: "xlsx", 2016: "xlsx", 2020: "xlsx", 2024: "xlsx"}
PLACES = {*BOOKS, 2008}
SHEETS = {
    "Brindabella": "Brin",
    "Ginninderra": "Ginn",
    "Molonglo": "Molo",
    "Kurrajong": "Kurr",
    "Murrumbidgee": "Murr",
    "Yerrabi": "Yerr",
}
LIST_FILES = {1995: "html-1995", 1998: "html-1998", 2001: "html-2001", 2004: "html-2004", 2008: "html-2008"}
SPECIAL = ("central scrutiny", "declaration", "interstate", "overseas")

# Party names the Commission prints for one party at different elections.
CANON = {
    "australian labor party": "ACT Labor",
    "australian labor party, australian capital territory": "ACT Labor",
    "australian labor party (act branch)": "ACT Labor",
    "act labor": "ACT Labor",
    "alp": "ACT Labor",
    "liberal party": "Liberal Party",
    "liberal party of australia (a.c.t. division)": "Liberal Party",
    "canberra liberals": "Liberal Party",
    "cl": "Liberal Party",
    "lib": "Liberal Party",
    "the act greens": "The ACT Greens",
    "act greens": "The ACT Greens",
    "the greens": "The ACT Greens",
    "greens": "The ACT Greens",
    "green": "The ACT Greens",
    "australian democrats": "Australian Democrats",
    "act democrats": "Australian Democrats",
}
INDEPENDENT = {"", "ung", "other", "independent", "ungrouped", "non-party", "none"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ACT = key("source", "elections-act")
DATES = key("source", "assembly-dates")
ASSEMBLY = key("body", "legislative-assembly")
HARE_CLARK = key("electoral_system", "hare-clark")
D_HONDT = key("electoral_system", "modified-d-hondt")


def party_id(name: str) -> str:
    return key("party", name)


@dataclass
class Candidate:
    name: str
    party: str
    group: str = ""
    votes: dict[str, int] = field(default_factory=dict)
    total: int | None = None
    elected: bool = False


@dataclass
class Place:
    name: str
    formal: int | None = None
    informal: int | None = None
    cast: int | None = None


@dataclass
class Electorate:
    name: str
    candidates: list[Candidate] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    enrolled: int | None = None
    formal: int | None = None
    informal: int | None = None
    cast: int | None = None
    quota: int | None = None
    seats: int = 0
    note: str = ""


def clean(text: object) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def number(text: object) -> int | None:
    if text is None:
        return None
    if isinstance(text, float):
        return round(text)
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def fetch() -> None:
    for url in [*FILES.values(), DATES_URL]:
        if url.startswith("http"):
            raw.fetch(SOURCE, url, suffix=Path(url).suffix or ".html")


def cached(name: str) -> raw.Artifact:
    return raw.cached(SOURCE, FILES[name])


# Names


def surname_token(token: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", token)
    return len(letters) >= 2 and letters[-2:].isupper() and letters.upper() != letters.lower()


def split_name(text: str) -> tuple[str, str]:
    """The given names and the family name of a printed candidate name, whichever order it uses."""
    text = clean(re.sub(r"\([^)]*\)", " ", text))
    if "," in text:
        last, _, first = text.partition(",")
        return clean(first), clean(last)
    tokens = text.split()
    family = [t for t in tokens if surname_token(t)]
    given = [t for t in tokens if not surname_token(t)]
    if not family:
        return " ".join(tokens[:-1]), tokens[-1]
    return " ".join(given), " ".join(family)


def proper(word: str) -> str:
    if not word.isupper():
        return word
    word = re.sub(r"(?<![A-Za-z])[a-z]", lambda m: m.group().upper(), word.lower())
    word = re.sub(r"(?<=['\-])[a-z]", lambda m: m.group().upper(), word)
    word = re.sub(r"^(Mc)([a-z])", lambda m: m.group(1) + m.group(2).upper(), word)
    return word


def person_name(text: str) -> str:
    first, last = split_name(text)
    return clean(f"{first} {' '.join(proper(w) for w in last.split())}")


def folded(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " " + clean(re.sub(r"[^a-z]+", " ", text)) + " "


def party_name(label: str) -> str:
    label = clean(label)
    return CANON.get(label.lower(), label)


# Elected members. The Commission prints the members of each electorate in one cell, so a candidate is elected when
# the cell holds the candidate's name.

ALIASES = {
    "william wood": "bill wood",
    "wayne berry": "wayne berry",
}


def mark_elected(electorate: Electorate, cell: str) -> None:
    text = folded(cell)
    for c in electorate.candidates:
        first, last = split_name(c.name)
        for given in {first, ALIASES.get(f"{first} {last}".lower(), first)}:
            if f"{folded(given).strip()} {folded(last).strip()} " in text:
                c.elected = True


# HTML tables


class Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[tuple[int, list[list[str]]]] = []
        self.docs: list[str] = []
        self._stack: list[list[list[str]]] = []
        self._cell: str | None = None
        self._row: list[str] | None = None
        self._title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._title = ""
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._stack:
            self._cell = ""
        elif tag == "br" and self._cell is not None:
            self._cell += " "

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title is not None:
            self.docs.append(clean(self._title))
            self._title = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(clean(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append((len(self.docs), self._stack.pop()))

    def handle_data(self, data: str) -> None:
        if self._title is not None:
            self._title += data
        if self._cell is not None:
            self._cell += data


@functools.cache
def html(name: str) -> Tables:
    parser = Tables()
    parser.feed(cached(name).path.read_text(errors="replace"))
    return parser


def elected_lists(name: str, title: str) -> dict[str, str]:
    """The cell of members for each electorate in the list of elected candidates whose page title starts with title."""
    parser = html(name)
    out: dict[str, str] = {}
    for doc, rows in parser.tables:
        if rows and rows[0][:2] == ["Electorate", "Name and party"] and parser.docs[doc - 1].startswith(title):
            for row in rows[1:]:
                out[row[0]] = row[1]
    if not out:
        raise LookupError(f"{name} has no list of elected candidates titled {title}")
    return out


def group_tables(year: int) -> dict[str, Electorate]:
    """Electorates from the results pages that print each party's candidates in a table, 1995 to 2004."""
    parser = html(LIST_FILES[year])
    out: dict[str, Electorate] = {}
    for doc, rows in parser.tables:
        found = re.fullmatch(rf"{year} (\w+) first preferences - Elections ACT", parser.docs[doc - 1])
        if not found or not rows or rows[0][1:2] != ["Votes"]:
            continue
        electorate = out.setdefault(found.group(1), Electorate(found.group(1)))
        head = rows[0][0]
        if head in ("Total", "Totals"):
            facts = {clean(r[0]).lower(): number(r[1]) for r in rows[1:]}
            electorate.formal = facts.get("total formal votes")
            electorate.informal = facts.get("informal votes")
            electorate.cast = facts.get("total votes") or facts.get("electorate total")
            electorate.enrolled = facts.get("enrolment")
            electorate.quota = facts.get("quota")
            continue
        label = re.sub(r"^[A-Z]\)?\s*", "", head).strip() if re.match(r"^[A-Z]\)?(\s|$)", head) else head
        group = head[0] if re.match(r"^[A-Z]\)?(\s|$)", head) else ""
        for row in rows[1:]:
            if row[0].lower().startswith(("group total", "ungrouped total")) or number(row[1]) is None:
                continue
            electorate.candidates.append(Candidate(row[0], party_of(label, {}), group, total=number(row[1])))
    return out


# Statistics workbooks


def workbook(year: int, sheet: str) -> list[list[object]]:
    path = cached(f"book-{year}").path
    if BOOKS[year] == "xls":
        book = xlrd.open_workbook(path)
        found = book.sheet_by_name(sheet)
        return [[c if c != "" else None for c in found.row_values(r)] for r in range(found.nrows)]
    frame = fastexcel.read_excel(str(path)).load_sheet_by_name(sheet, header_row=None).to_polars()
    return [list(row) for row in frame.iter_rows()]


def sheets(year: int) -> set[str]:
    path = cached(f"book-{year}").path
    if BOOKS[year] == "xls":
        return set(xlrd.open_workbook(path).sheet_names())
    return set(fastexcel.read_excel(str(path)).sheet_names)


def party_codes(year: int) -> dict[str, str]:
    return {
        clean(row[0]): clean(row[1])
        for row in workbook(year, "Abbreviations")[1:]
        if len(row) > 1 and row[0] is not None and row[1] is not None
    }


def party_of(code: object, codes: dict[str, str]) -> str:
    code = clean(code)
    if code.lower() in INDEPENDENT:
        return ""
    return party_name(codes.get(code, code))


def place_columns(header: list[object]) -> list[tuple[int, str]]:
    columns = []
    for index in range(2, len(header), 2):
        name = clean(header[index])
        if name and name.lower() != "total":
            columns.append((index, name))
    return columns


def count(cell: object) -> int | None:
    if cell is None or (isinstance(cell, str) and not clean(cell)):
        return None
    return round(float(str(cell)))


def book_electorate(year: int, name: str, codes: dict[str, str]) -> Electorate:
    rows = workbook(year, f"{SHEETS[name]}PP")
    columns = place_columns(rows[0])
    electorate = Electorate(name)
    seen: Counter[str] = Counter()
    places: list[Place] = []
    for _, place in columns:
        seen[place] += 1
        places.append(Place(place if seen[place] == 1 else f"{place} ({seen[place]})"))
    for row in rows[1:]:
        label = clean(row[0])
        low = label.lower()
        if not label or low.startswith(("group total", "ungrouped tot")):
            continue
        if all(clean(cell) in ("", "None") for cell in row[2:]):
            continue
        if low in ("formal", "informal", "polling place", "polling place total"):
            for (index, _), place in zip(columns, places, strict=True):
                value = count(row[index])
                if low == "formal":
                    place.formal = value
                elif low == "informal":
                    place.informal = value
                else:
                    place.cast = value
            continue
        candidate = Candidate(label, party_of(row[1], codes))
        for (index, _), place in zip(columns, places, strict=True):
            value = count(row[index])
            if value is not None:
                candidate.votes[place.name] = value
        electorate.candidates.append(candidate)
    electorate.places = [p for p in places if p.formal is not None or p.cast is not None]
    return electorate


def book_totals(year: int, electorate: Electorate) -> None:
    rows = workbook(year, f"{SHEETS[electorate.name]}CanSum")
    head = next(i for i, row in enumerate(rows) if clean(row[0]) == "Candidate")
    last = max(i for i, cell in enumerate(rows[head]) if cell is not None and i % 2 == 0)
    by_name = {clean(c.name): c for c in electorate.candidates}
    for row in rows[head + 1 :]:
        label = clean(row[0])
        low = label.lower()
        value = count(row[last])
        if label in by_name:
            by_name[label].total = value
        elif low == "formal":
            electorate.formal = value
        elif low == "informal":
            electorate.informal = value
        elif low in ("total", "polling place total", "polling place"):
            electorate.cast = value
    for c in electorate.candidates:
        if c.total is None:
            raise ValueError(f"{year} {electorate.name}: no total for {c.name}")


def book_enrolment(year: int) -> dict[str, int]:
    return {
        clean(row[0]): round(float(str(row[1])))
        for row in workbook(year, "Enrolment")[1:]
        if row[1] is not None and clean(row[0]) in SHEETS
    }


def book_electorates(year: int) -> list[Electorate]:
    codes = party_codes(year)
    enrolled = book_enrolment(year)
    names = [n for n in SHEETS if f"{SHEETS[n]}PP" in sheets(year)]
    out = []
    for name in names:
        electorate = book_electorate(year, name, codes)
        book_totals(year, electorate)
        electorate.enrolled = enrolled[name]
        out.append(electorate)
    return out


# The 2008 printed statistics book


@functools.cache
def pdf_text(name: str) -> list[str]:
    done = subprocess.run(
        ["pdftotext", "-layout", str(cached(name).path), "-"], check=True, capture_output=True, text=True
    )
    return done.stdout.splitlines()


def pdf_codes(lines: list[str]) -> dict[str, str]:
    start = next(i for i, line in enumerate(lines) if line.strip() == "Abbreviations" and not line.startswith(" "))
    codes = {}
    for line in lines[start : start + 30]:
        found = re.match(r"\s+(\w+)\s*\.{3,}\s*(.+?)\s*$", line)
        if found:
            codes[found.group(1)] = found.group(2)
    return codes


def pdf_electorates() -> list[Electorate]:
    lines = pdf_text("pdf-2008")
    codes = pdf_codes(lines)
    out: list[Electorate] = []
    current: Electorate | None = None
    for line in lines:
        head = re.search(r"Table 1\.[678] First preference votes by candidate/vote type: (\w+)", line)
        if head:
            current = Electorate(head.group(1))
            out.append(current)
            continue
        if current is None or not line.strip():
            continue
        if line.strip().startswith(
            ("ACT Electoral", "ACT Legislative", "Party/", "Candidate", "Group ", "Ungrouped total", "Table")
        ):
            if line.strip().startswith("Table 2"):
                current = None
            continue
        parts = re.split(r"\s{2,}", line.strip())
        counts = [int(x) for x in re.findall(r"(?<![\d.])(\d+)(?=\s+\d+\.\d+%)", line)]
        if len(counts) != 5:
            continue
        label = parts[0]
        if label in ("Formal", "Informal", "Total", "Polling place total"):
            if label == "Formal":
                current.formal = counts[4]
            elif label == "Informal":
                current.informal = counts[4]
            else:
                current.cast = counts[4]
            continue
        current.candidates.append(Candidate(label, party_of(parts[1], codes), total=counts[4]))
    enrol = next(line for line in lines if re.match(r"\s+Enrolment\s+\d", line))
    for electorate, value in zip(out, re.findall(r"\d+", enrol), strict=False):
        electorate.enrolled = int(value)
    pdf_places(out)
    return out


@functools.cache
def pdf_pages(name: str) -> list[list[tuple[float, float, float, str]]]:
    """The words of each page as (left, right, top, text), from the positions the PDF gives them."""
    done = subprocess.run(
        ["pdftotext", "-bbox", str(cached(name).path), "-"], check=True, capture_output=True, text=True
    )
    pattern = r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>'
    return [
        [(float(a), float(c), float(b), unescape(w)) for a, b, c, w in re.findall(pattern, page)]
        for page in done.stdout.split("<page ")[1:]
    ]


def text_lines(words: list[tuple[float, float, float, str]]) -> list[list[tuple[float, float, float, str]]]:
    lines: list[list[tuple[float, float, float, str]]] = []
    for word in sorted(words, key=lambda w: (w[2], w[0])):
        if lines and abs(word[2] - lines[-1][0][2]) < 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line) for line in lines]


def pdf_place_page(
    lines: list[list[tuple[float, float, float, str]]],
) -> tuple[str, list[str], list[tuple[str, dict[int, int]]]]:
    """The electorate, the polling place columns, and each row's label with its counts by column, from one page."""
    title = next(
        i
        for i, line in enumerate(lines)
        if " ".join(w[3] for w in line).startswith("Table 2.") and ":" in " ".join(w[3] for w in line)
    )
    electorate = " ".join(w[3] for w in lines[title]).rsplit(":", 1)[1].strip()
    head = next(i for i, line in enumerate(lines) if line[0][3] == "Candidate")
    heads: list[tuple[float, list[tuple[float, str]]]] = []
    for line in lines[title + 1 : head + 1]:
        phrase: list[tuple[float, float, str]] = []
        for left, right, _, text in [w for w in line if w[0] > 185] + [(1e9, 1e9, 0.0, "")]:
            if phrase and left - phrase[-1][1] > 6:
                centre = (phrase[0][0] + phrase[-1][1]) / 2
                near = next((h for h in heads if abs(h[0] - centre) < 22), None)
                if near is None:
                    near = (centre, [])
                    heads.append(near)
                near[1].append((line[0][2], " ".join(t for _, _, t in phrase)))
                phrase = []
            phrase.append((left, right, text))
    heads.sort()
    names = [clean(" ".join(t for _, t in sorted(h[1]))).replace("Pre- Poll", "Pre-Poll") for h in heads]
    rows = []
    for line in lines[head + 1 :]:
        label = clean(" ".join(w[3] for w in line if w[0] < 150))
        counts: dict[int, int] = {}
        for _, right, _, text in line:
            if right > 185 and re.fullmatch(r"\d+", text):
                counts[min(range(len(heads)), key=lambda i: abs(heads[i][0] - right))] = int(text)
        if label and counts:
            rows.append((label, counts))
    return electorate, names, rows


def pdf_places(electorates: list[Electorate]) -> None:
    """Add the polling places of Tables 2.1 to 2.3, whose pages each hold a few of the places as columns."""
    by_name = {e.name: e for e in electorates}
    for words in pdf_pages("pdf-2008"):
        lines = text_lines(words)
        if not any(re.match(r"Table 2\.\d .*:", " ".join(w[3] for w in line)) for line in lines):
            continue
        name, columns, rows = pdf_place_page(lines)
        electorate = by_name[name]
        candidates = {clean(c.name): c for c in electorate.candidates}
        places = {}
        for index, column in enumerate(columns):
            if column.lower() == "total" or not any(index in counts for _, counts in rows):
                continue
            seen = sum(1 for p in electorate.places if p.name == column or p.name.startswith(f"{column} ("))
            places[index] = Place(column if not seen else f"{column} ({seen + 1})")
            electorate.places.append(places[index])
        for label, counts in rows:
            for index, place in places.items():
                value = counts.get(index)
                if label in candidates:
                    if value is not None:
                        candidates[label].votes[place.name] = value
                elif label == "Formal":
                    place.formal = value
                elif label == "Informal":
                    place.informal = value
                elif label == "Polling place total":
                    place.cast = value


# The territory-wide elections of 1989 and 1992


@dataclass
class Territory:
    lists: list[tuple[str, int, int]]  # name, votes, seats
    members: dict[str, str]
    formal: int
    informal: int
    cast: int
    enrolled: int
    quota: int


def territory(year: int) -> Territory:
    parser = html(f"html-{year}")
    elected = next(rows for _, rows in parser.tables if rows[0][:3] == ["Party", "Number", "Name"])
    results = next(rows for _, rows in parser.tables if rows[0][0] == "Party/Independent Candidate")
    totals = next(rows for _, rows in parser.tables if rows[0][0] == "Totals")
    seats = {row[0]: int(row[1]) for row in elected[1:]}
    members = {row[0]: row[2] for row in elected[1:]}
    names = {row[0] for row in results[1:]}
    if set(seats) - names:
        raise ValueError(f"{year}: elected lists {sorted(set(seats) - names)} are not on the results page")
    facts = {clean(r[0]).lower(): number(r[1]) for r in totals[1:]}
    return Territory(
        [(row[0], number(row[1]) or 0, seats.get(row[0], 0)) for row in results[1:]],
        members,
        facts["total formal votes"] or 0,
        facts["informal votes"] or 0,
        facts.get("electorate total") or 0,
        facts["enrolment"] or 0,
        facts["quota"] or 0,
    )


# Every election


ELECTED = {
    1995: ("html-1995", "1995 List"),
    1998: ("html-1998", "1998 List"),
    2001: ("html-2001", "2001 List"),
    2004: ("html-2004", "2004 List"),
    2008: ("html-2008", "2008 list"),
    2012: ("html-2012", "2012 list"),
    2016: ("html-2016-2020", "2016 List"),
    2020: ("html-2016-2020", "2020 List"),
    2024: ("html-2024", "2024 List"),
}


@functools.cache
def electorates(year: int) -> list[Electorate]:
    if year in BOOKS:
        found = book_electorates(year)
    elif year == 2008:
        found = pdf_electorates()
    else:
        found = list(group_tables(year).values())
    cells = elected_lists(*ELECTED[year])
    for electorate in found:
        mark_elected(electorate, cells[electorate.name])
        electorate.seats = sum(c.elected for c in electorate.candidates)
    return found


def years() -> list[int]:
    return list(DAYS)


def files(year: int) -> list[str]:
    if year in (1989, 1992):
        return [f"html-{year}"]
    return sorted({ELECTED[year][0], *(name for name in (f"book-{year}", f"pdf-{year}") if name in FILES)})


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in years())]


def parse() -> None:
    registry().write()
    for year in years():
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def party_names() -> list[str]:
    names = {c.party for year in years() if year > 1992 for e in electorates(year) for c in e.candidates}
    for year in (1989, 1992):
        names |= {party_name(name) for name, _, _ in territory(year).lists if "," not in name}
    return sorted(names - {""})


SYSTEMS = (
    (
        D_HONDT,
        date(1989, 1, 1),
        date(1995, 1, 1),
        {"formula": "dhondt"},
        (
            "The whole territory formed one electorate that elected 17 members by a modified d'Hondt method. Seats "
            "went to parties and groups in proportion to their first preference votes, and each party's seats then "
            "went to its candidates by the distribution of preferences within the party."
        ),
    ),
    (
        HARE_CLARK,
        date(1995, 1, 1),
        None,
        {"formula": "stv", "quota": "droop", "surplus": "last_parcel"},
        (
            "Each multi-member electorate elects its members by the Hare-Clark single transferable vote with a Droop "
            "quota. Voters number the candidates, and the order of candidates within each party column on the ballot "
            "paper is rotated between ballot papers (Robson rotation). A candidate's surplus passes to the next "
            "preferences by transferring the last parcel of votes the candidate received."
        ),
    ),
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Parties, the Legislative Assembly, and its electoral systems for Australian Capital Territory elections "
        "from Elections ACT",
    )
    p.external.add(AUSTRALIA)
    p.add(
        "source",
        id=ACT,
        kind="official_body",
        publisher="Elections ACT",
        title="ACT Legislative Assembly election results and statistics published by Elections ACT",
        url=PAGE,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
    )
    p.add(
        "source",
        id=DATES,
        kind="official_body",
        publisher="Elections ACT",
        title="ACT Legislative Assembly election dates, from the ACT open data portal",
        url="https://www.data.act.gov.au/resource/gypk-fkpc",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
    )
    for name in sorted({name for year in years() for name in files(year)}):
        p.artifact(cached(name), ACT)
    p.artifact(raw.cached(SOURCE, DATES_URL), DATES)
    p.add("scheme", code="au-act.electorate", polity_id=AUSTRALIA, kind="electoral_district", rank=1, noun="electorate")
    p.add("scheme", code="au-act.place", polity_id=AUSTRALIA, kind="precinct", rank=2, noun="polling place")
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="unicameral")
    p.name(ASSEMBLY, "Australian Capital Territory Legislative Assembly", "en", "official")
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in party_names():
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def place_kind(name: str) -> str:
    low = name.lower()
    if "postal" in low:
        return "postal_district"
    if "mobile" in low:
        return "mobile"
    if any(word in low for word in SPECIAL):
        return "aggregate"
    return "standard"


def method(year: int, places: bool) -> str:
    text = (
        "Counted by the ACT Electoral Commission. First preference votes are stored for each candidate, and the "
        "members elected are stored as declared. The archive stores a count only at the first preference stage, so "
        "the distribution of preferences that decides the later seats is not stored. "
    )
    if places:
        text += (
            "Votes are published for each polling place, with the votes taken at pre-poll voting centres, by post, by "
            "mobile teams, and under declaration shown as separate columns. Votes are counted for a polling place "
            "even when the voter's enrolled electorate is another one. A polling place that issued fewer than 20 "
            "votes for an electorate has them counted at central scrutiny."
        )
    else:
        text += "Votes are published for the whole electorate, not for each polling place."
    return text


def build(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Australian Capital Territory Legislative Assembly election"
        + (" by polling place" if year in PLACES else ""),
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(AUSTRALIA)
    for name in files(year):
        p.uses(cached(name))
    election = key("election", year)
    path = f"au-act/{day}-general"
    p.add("election", id=election, polity_id=AUSTRALIA, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Australian Capital Territory general election", "en", "official")
    if year in (1989, 1992):
        build_territory(p, year, election, path)
    else:
        build_electorates(p, year, election, path)
    return p


def build_territory(p: Package, year: int, election: str, path: str) -> None:
    data = territory(year)
    contest = key("contest", year, "act")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=ASSEMBLY,
        magnitude=17,
        round_number=1,
        electoral_system_id=D_HONDT,
        default_result_kind="counted",
    )
    p.name(contest, "Australian Capital Territory, Legislative Assembly", "en", "official")
    p.add("slug", path=f"{path}/australian-capital-territory", entity_id=contest, is_current=True)
    result_set = key("result_set", year, "act")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ACT,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md="Counted by the ACT Electoral Commission. The whole territory was one electorate. First preference "
        "votes are stored for each party, group, and independent candidate, and the seats each won are stored as "
        "declared. The Commission's results page prints votes for the whole territory only, so no finer count is "
        f"stored. The quota was {data.quota:,} votes. The members elected, by party: "
        + "; ".join(f"{name}: {names}" for name, names in data.members.items())
        + ".",
        lang="en",
    )
    unit = key("unit", year, "act")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=AUSTRALIA,
        scheme_code="au-act.electorate",
        code=f"{year}:act",
        area_relation="unlocated",
        kind="standard",
    )
    p.name(unit, "Australian Capital Territory", "en", "official")
    for position, (name, votes, seats) in enumerate(data.lists, 1):
        option = key("option", year, "act", position)
        independent = "," in name
        label = party_name(name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=None if independent else party_id(label),
            ballot_position=position,
            ballot_label=name,
        )
        put(p, result_set, unit, option, VOTES, votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
    for measure, value in (
        (REGISTERED, data.enrolled),
        (CAST, data.cast),
        (VALID, data.formal),
        (INVALID, data.informal),
    ):
        put(p, result_set, unit, None, measure, value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


def build_electorates(p: Package, year: int, election: str, path: str) -> None:
    places_held = year in PLACES
    mismatched: list[str] = []
    for electorate in electorates(year):
        name = electorate.name
        slug = changeset.slug(name)
        contest = key("contest", year, slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            magnitude=electorate.seats,
            round_number=1,
            electoral_system_id=HARE_CLARK,
            default_result_kind="counted",
        )
        p.name(contest, f"{name}, Legislative Assembly", "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ACT,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        body = method(year, places_held)
        if electorate.quota:
            body += f" The quota was {electorate.quota:,} votes."
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
        options: list[str] = []
        for position, c in enumerate(electorate.candidates, 1):
            person = key("person", year, slug, position)
            p.add("person", id=person)
            p.name(person, person_name(c.name), "en", "official")
            option = key("option", year, slug, position)
            options.append(option)
            party = party_id(c.party) if c.party else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c.name,
            )
            candidacy = key("candidacy", year, slug, position)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=party,
                is_independent=not c.party,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if c.elected else "lost",
            )
        district = key("unit", year, slug)
        p.add(
            "reporting_unit",
            id=district,
            polity_id=AUSTRALIA,
            scheme_code="au-act.electorate",
            code=f"{year}:{slug}",
            area_relation="unlocated",
            kind="aggregate" if places_held else "standard",
        )
        p.name(district, f"Electorate of {name}", "en", "official")
        if places_held:
            seen: Counter[str] = Counter()
            for place in electorate.places:
                code = f"{year}:{slug}:{changeset.slug(place.name)}"
                seen[code] += 1
                if seen[code] > 1:
                    code += f"-{seen[code]}"
                unit = key("unit", code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=AUSTRALIA,
                    scheme_code="au-act.place",
                    code=code,
                    parent_id=district,
                    area_relation="unlocated",
                    kind=place_kind(place.name),
                )
                p.name(unit, f"{place.name} ({name})", "en", "official")
                for c, option in zip(electorate.candidates, options, strict=True):
                    put(p, result_set, unit, option, VOTES, c.votes.get(place.name))
                put(p, result_set, unit, None, VALID, place.formal)
                put(p, result_set, unit, None, INVALID, place.informal)
                put(p, result_set, unit, None, CAST, place.cast)
            roll = key("unit", year, slug, "roll")
            p.add(
                "reporting_unit",
                id=roll,
                polity_id=AUSTRALIA,
                scheme_code="au-act.electorate",
                code=f"{year}:{slug}:roll",
                parent_id=district,
                area_relation="unlocated",
                kind="aggregate",
            )
            p.name(roll, f"Electorate of {name}: electoral roll", "en", "official")
        else:
            roll = district
            for c, option in zip(electorate.candidates, options, strict=True):
                put(p, result_set, district, option, VOTES, c.total)
            put(p, result_set, district, None, VALID, electorate.formal)
            put(p, result_set, district, None, INVALID, electorate.informal)
            put(p, result_set, district, None, CAST, electorate.cast)
        put(p, result_set, roll, None, REGISTERED, electorate.enrolled)
        for measure, value in (
            (REGISTERED, electorate.enrolled),
            (CAST, electorate.cast),
            (VALID, electorate.formal),
            (INVALID, electorate.informal),
        ):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for c, option in zip(electorate.candidates, options, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.total)
        if places_held:
            added = sum(pl.informal or 0 for pl in electorate.places)
            if electorate.informal is not None and added != electorate.informal:
                mismatched.append(f"{name} ({added:,} against {electorate.informal:,})")
                p.add(
                    "note",
                    id=key("note", result_set, "informal"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The informal ballots in the polling place columns add up to {added:,}, while the "
                    f"Commission's electorate total is {electorate.informal:,}. Both are stored as printed.",
                    lang="en",
                )
    if mismatched:
        p.accept(
            "accounting.declared",
            "the polling place columns hold informal ballots that do not add up to the electorate total the "
            "Commission prints for " + ", ".join(mismatched),
        )
