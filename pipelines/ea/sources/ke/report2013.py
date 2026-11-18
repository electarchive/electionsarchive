"""The IEBC's "4th March 2013 General Election Data", read from the PDF's text layer.

Every ballot is a ruled table. The horizontal rules cut each page into row bands and the vertical rules into columns,
so a candidate's wrapped name or party, which a text extraction splits across lines, stays in its own cell. A band is
one printed row; the words whose centers fall in a cell are that cell's text.
"""

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

# The first and last PDF page (1-based) of each ballot's tables.
PAGES = {
    "president": (68, 88),
    "assembly": (90, 194),
    "woman": (196, 213),
    "senate": (215, 230),
    "governor": (232, 243),
    "ward": (245, 766),
}
# The columns of each ballot's table, left to right.
COLUMNS: dict[str, list[str]] = {
    "assembly": ["county_code", "county", "constituency_code", "constituency", "no", "name", "party", "votes"],
    "woman": ["county_code", "county", "no", "name", "party", "votes"],
    "senate": ["county_code", "county", "no", "name", "party", "votes"],
    "governor": ["county_code", "county", "no", "name", "party", "votes"],
    "ward": [
        "county_code",
        "county",
        "constituency_code",
        "constituency",
        "ward_code",
        "ward",
        "no",
        "name",
        "party",
        "votes",
    ],
}
MERGE = 25.0
LINE = 3.0


@dataclass
class Band:
    page: int
    top: float
    cells: dict[str, str]


def _rules(page: pymupdf.Page) -> tuple[list[float], list[float]]:
    """The y positions of the horizontal rules and the x positions of the vertical ones, each clustered."""
    horizontal: list[float] = []
    vertical: list[float] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.height < 1.6 and rect.width > 15:
            horizontal.append((rect.y0 + rect.y1) / 2)
        elif rect.width < 1.6 and rect.height > 5:
            vertical.append((rect.x0 + rect.x1) / 2)
    return _cluster(horizontal, 1.5), _cluster(vertical, MERGE)


def _cluster(values: list[float], gap: float) -> list[float]:
    out: list[float] = []
    for value in sorted(values):
        if out and value - out[-1] <= gap:
            continue
        out.append(value)
    return out


def bands(page: pymupdf.Page, number: int, names: list[str]) -> list[Band]:
    """The rows of a ruled table on one page, each as its cells' text by column name."""
    ys, xs = _rules(page)
    if len(xs) != len(names) + 1:
        raise ValueError(f"page {number} has {len(xs)} column rules, not {len(names) + 1}")
    rows: list[list[tuple[float, float, str]]] = [[] for _ in range(len(ys) - 1)]
    for w in page.get_text("words"):
        x, y = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        i = bisect.bisect_right(ys, y) - 1
        if 0 <= i < len(rows) and xs[0] <= x <= xs[-1]:
            rows[i].append((x, y, str(w[4])))
    out = []
    for i, words in enumerate(rows):
        if not words:
            continue
        cells: dict[str, list[tuple[float, float, str]]] = {}
        for x, y, text in words:
            cells.setdefault(names[min(max(bisect.bisect_right(xs, x) - 1, 0), len(names) - 1)], []).append(
                (x, y, text)
            )
        out.append(Band(number, ys[i], {name: _join(cell) for name, cell in cells.items()}))
    return out


def _join(words: list[tuple[float, float, str]]) -> str:
    lines: list[list[tuple[float, float, str]]] = []
    top = None
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        if top is None or abs(word[1] - top) > LINE:
            lines.append([])
            top = word[1]
        lines[-1].append(word)
    return " ".join(" ".join(w[2] for w in sorted(line)) for line in lines)


def read(doc: pymupdf.Document, ballot: str) -> list[Band]:
    """Every row of a ballot's tables. A row that breaks across a page continues in the first band of the next page,
    which carries neither an area code nor a candidate number and is appended to the row before it if that row has no
    votes yet."""
    first, last = PAGES[ballot]
    out: list[Band] = []
    for number in range(first, last + 1):
        page = bands(doc[number - 1], number, COLUMNS[ballot])
        for index, band in enumerate(page):
            if (
                index == 0
                and out
                and number > first
                and "votes" not in out[-1].cells
                and "name" in out[-1].cells
                and AREA[ballot][0] not in band.cells
                and "no" not in band.cells
            ):
                for name, text in band.cells.items():
                    previous = out[-1].cells.get(name)
                    out[-1].cells[name] = f"{previous} {text}" if previous else text
            else:
                out.append(band)
    return out


def number(text: str) -> int | None:
    digits = re.sub(r"[,\s]", "", text)
    return int(digits) if digits.isdigit() else None


@dataclass
class Entry:
    """One candidate's line in a ballot's table."""

    page: int
    position: int
    name: str
    party: str
    votes: int | None
    no: str = ""
    unopposed: bool = False


@dataclass
class Block:
    """The candidates of one contest as the table groups them, with the total printed under them."""

    page: int
    code: str
    name: str
    entries: list[Entry] = field(default_factory=list)
    total: int | None = None
    printed: int | None = None
    closed: bool = False
    unnamed: bool = False


# The cells that name the area a ballot's row belongs to.
AREA = {
    "assembly": ("constituency_code", "constituency"),
    "woman": ("county_code", "county"),
    "senate": ("county_code", "county"),
    "governor": ("county_code", "county"),
    "ward": ("ward_code", "ward"),
}


def _mode(values: list[str]) -> str:
    return Counter(values).most_common(1)[0][0] if values else ""


def blocks(ballot: str, rows: list[Band]) -> tuple[list[Block], int | None]:
    """The contests of a ballot, in table order, and the grand total that the last page prints below them.

    A contest ends at its total line. The code and name beside its candidates are the ones most of them carry, because
    a few lines print a neighbor's code."""
    code, name = AREA[ballot]
    found: list[Block] = []
    codes: list[list[str]] = []
    names: list[list[str]] = []
    grand: int | None = None
    last_code = last_name = ""
    for band in rows:
        cells = band.cells
        text = " ".join(cells.values()).upper()
        total = "name" not in cells and "votes" in cells and set(cells) <= {"votes", "no"}
        if re.search(r"\bTOTAL\b", text):
            total = True
        if total:
            votes = number(cells.get("votes", ""))
            if found and not found[-1].closed:
                found[-1].total = votes
                found[-1].printed = number(cells.get("no", ""))
                found[-1].closed = True
            elif votes is not None:
                grand = votes
            continue
        if "name" not in cells and "UNOPPOSED" in text and cells.get(code):
            found.append(Block(band.page, cells[code].strip(), re.sub(r"\s+", " ", cells.get(name, "")).strip()))
            found[-1].closed = found[-1].unnamed = True
            codes.append([])
            names.append([])
            continue
        if (
            "name" not in cells
            or re.search(r"[A-Z]|^$", cells.get("no", "").upper())
            and re.search(r"[A-Z]", cells.get("no", "").upper())
        ):
            continue
        if re.search(r"RESULTS|CANDIDATES? ?NAMES?|CANDIDATE'S", text):
            continue
        here = re.sub(r"\s+", " ", cells.get(name, "")).strip()
        number_here = cells.get(code, "").strip()
        # Without a total line, a contest ends where both its code and its name change; one of the two alone is a
        # misprint in a single line.
        moved = bool(
            here and number_here and last_name and last_code and here != last_name and number_here != last_code
        )
        if not found or found[-1].closed or moved:
            found.append(Block(band.page, "", ""))
            codes.append([])
            names.append([])
        block = found[-1]
        if cells.get(code):
            codes[-1].append(cells[code].strip())
            last_code = cells[code].strip()
        if here:
            names[-1].append(here)
            last_name = here
        alone = re.search(r"\bUN-?OPPOSED\b", cells["name"].upper()) is not None
        block.entries.append(
            Entry(
                band.page,
                len(block.entries) + 1,
                re.sub(r"\s*\bUN-?OPPOSED\b", "", re.sub(r"\s+", " ", cells["name"]), flags=re.IGNORECASE).strip(),
                cells.get("party", ""),
                number(cells.get("votes", "")),
                cells.get("no", ""),
                alone or "UNOPPOSED" in cells.get("votes", "").upper(),
            )
        )
    for block, c, n in zip(found, codes, names, strict=True):
        if c or n:
            block.code, block.name = _mode(c), _mode(n)
    return found, grand


# Each party as the report's party-strength tables name it, with the other ways the ballot tables print it. A printed
# party that is not here, such as an initialism that more than one party uses, is stored without a party. The
# initialisms below are here because each one's winners, added to those who print the party's full name, give the seat
# count that the book's party strength table (12.6) lists for that party.
PARTIES: dict[str, list[str]] = {
    "Orange Democratic Movement": ["Orange Democratic", "Orange Democtratic Movement"],
    "The National Alliance": ["Tha National Alliance"],
    "United Republican Party": ["United Republican"],
    "United Democratic Forum Party": [
        "United Democratic Forum PartyK",
        "United Democratic Forum PartyF",
        "United Democratic Movement Forum Party",
    ],
    "Wiper Democratic Movement- Kenya": [
        "Wiper Democratic Movement - Kenya",
        "Wiper Democratic Movement-Kenya",
        "Wiper",
    ],
    "Kenya National Congress": ["Kenya National Congress Party of Kenya"],
    "Democratic Party of Kenya": ["Democratic Party", "Democratic Party of KenyaK"],
    "Federal Party of Kenya": [],
    "Safina": ["Safina - Safina"],
    "Forum for Restoration of Democracy-Kenya": [
        "Forum for Restoration of Democracy - Kenya",
        "Forum for Restoration of Democracy- Kenya",
        "Forum for Restoration of",
        "Ford-Kenya",
        "Ford Kenya",
        "Ford-K",
        "Ford K",
        "Ford_K",
        "Ford- Kenya",
        "Ford- K",
        "FDK",
    ],
    "Alliance Party of Kenya": ["NAlliance Party of Kenya", "Alliance Aprty of Kenya"],
    "Kenya African National Union": ["KANU", "Kenya African National", "Kenya African National Union(KANU)"],
    "Grand National Union": ["Grand National Union Party", "Grand National Union Party of Kenya"],
    "New Ford Kenya": ["New Ford K", "NewFord Kenya", "New Ford-K"],
    "Narc-Kenya": [
        "Narc-K",
        "Narc K",
        "Narc Kenya",
        "Narc- K",
        "Narc - Kenya",
        "Narc - K",
        "Narc- Kenya",
        "NarcK-K",
        "NarcK-K - K",
        "Nark-Kenya",
        "Nark-K",
        "Nark K",
        "Nark- K",
        "Narc_K",
        "Narc -K",
        "N Arc",
    ],
    "National Rainbow Coalition": ["Narc"],
    "Party of Independent Candidates of Kenya": [
        "Party of Independent Candidates of",
        "Party of Independent",
        "Party of Independent Candidate",
        "Party of Indepennent Candidates of Kenya",
        "Party of Indepenndent Candidates of Kenya",
    ],
    "The Independent Party": ["The Independent", "The Indipendent P", "The Independent Party of Kenya"],
    "The National Vision Party": [
        "National Vision Party Kenya",
        "National Vision Party",
        "National Vision Party of Kenya",
        "Nationa Vision Party",
    ],
    "Chama Cha Uzalendo": [],
    "Agano Party": ["Agano"],
    "Peoples Democratic Party": [],
    "Mazingira Greens Party of Kenya": [
        "Mazingira Green Party",
        "Mazingira Greens Party",
        "Mazingira Greens",
        "Mazingira",
        "MGPK",
        "Mazingira GPK",
    ],
    "Restore and Build Kenya": ["Restore and Build Kenya Party"],
    "Ford-People": ["Ford People", "Ford-P", "Ford P"],
    "Ford-Asili": ["Ford-A"],
    "Muungano Party": ["Muungano", "Muugano Party"],
    "The Labour Party of Kenya": ["Labour Party of Kenya", "LPK"],
    "United Democratic Movement": ["UDM"],
    "Shirikisho Party of Kenya": ["Shirikisho"],
    "Farmers Party": ["Farmers", "Farmers Party of Kenya", "Farmers P", "Farmer's Party", "FP"],
    "Kenya Social Congress": [],
    "National Agenda Party of Kenya": ["National Agenda Party of Kenya", "National Agenda PartyOf Kenya"],
    "Peoples Party of Kenya": ["PPeoples Party of Kenya", "Peoples Party"],
    "Chama Cha Mwananchi": ["Chama Cha Mwanainchi", "Chama Cha Mwana Inchi", "C C Mwananchi"],
    "Party of Democratic Unity": [],
    "Unity Party of Kenya": [],
    "Saba Saba Asili": ["Sabasaba Asili", "Saba Saba"],
    "Republican Congress Party of Kenya": ["Republican Congress", "Republican Congress Party"],
    "Progressive Party of Kenya": ["Progresive Party of Kenya", "PPK"],
    "Mwangaza Party": ["Mwangaza"],
    "Party of Action": [],
    "Kenya African Democratic Union-Asili": [
        "Kadu Asili",
        "Kadu-Asili",
        "Kadu- Asili",
        "Kaddu Asili",
        "Kadu-A",
        "Kandu Asili",
        "Kadu- Saili",
        "Kenya African Democratic Union- Asili",
    ],
    "New Democrats": ["New Democrat", "New Democrats Party of Kenya", "ND"],
    "The National Labour Party": ["National Labour Party", "National Labour Party of Kenya"],
    "Mzalendo Saba Saba Party": ["Mzalendo Saba Saba", "Mzalendo", "MSS"],
    "Kenya National Democratic Alliance": [],
    "Sisi Kwa Sisi Party": ["Sisi Kwa Sisi Party of Kenya"],
    "Mkenya Solidarity Movement": ["Mkenya Solidarity Movements", "Mkenya Solidary"],
    "Social Democratic Party of Kenya": [
        "Social Democratic Party",
        "SDP",
        "SDemocratic Party of Kenya",
        "SDemocratic Party of KenyaK",
    ],
    "Republican Liberty Party": ["RLP"],
    "Maendeleo Democratic Party": ["Maendeleo Development Party"],
    "National Party of Kenya": ["NPK"],
    "Peoples Patriotic Party of Kenya": [],
    "Roots Party of Kenya": ["Roots Party"],
    "Chama Cha Mapinduzi": [],
    "Alliance for Real Change": ["ARK"],
    "National Democratic Movement": ["NDM"],
}
INDEPENDENT = (
    "Independent Candidate",
    "Independent",
    "Indipendent Candidate",
    "Independeny Candidate",
    "Independednt Candidate",
    "Indepent Candidate",
    "Independent Candidates",
    "Inde",
    "IND",
)


def _fold(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


_PARTY = {_fold(name): name for name in PARTIES} | {
    _fold(alias): name for name, aliases in PARTIES.items() for alias in aliases
}
_INDEPENDENT = {_fold(name) for name in INDEPENDENT}


def party(printed: str) -> str | None:
    """The party a printed name stands for, `independent` for an independent candidate, or None when it is unclear."""
    folded = _fold(printed)
    if folded in _INDEPENDENT or folded == "INDEPE":
        return "independent"
    return _PARTY.get(folded)


CANDIDATES = (
    "Mohamed Abduba Dida",
    "Martha Wangari Karua",
    "Peter Kenneth",
    "Uhuru Kenyatta",
    "James Legilisho Kiyiapi",
    "Musalia Mudavadi",
    "Paul Kibugi Muite",
    "Raila Odinga",
)
NUMBERS = r"((?:[\d,]+\s+){11}[\d,]+)"
ROW = re.compile(rf"^\s*(\d{{3}})\s+([A-Z][A-Z .'/()-]*?)\s{{2,}}{NUMBERS}\s+[\d.]+%?\s*$")
COUNTY_TOTAL = re.compile(rf"^\s*COUNTY TOTAL\s{{2,}}{NUMBERS}\s+[\d.]+%?\s*$")
DIASPORA = re.compile(rf"^\s*DIASPORA\s+{NUMBERS}\s+[\d.]+%?\s*$")
GRAND = re.compile(rf"^\s*GRAND TOTAL\s+{NUMBERS}\s+[\d.]+%?\s*$")


@dataclass
class Line:
    """A line of the presidential table: eight candidates' votes, then valid votes, rejected ballots, ballots cast, and
    registered voters."""

    code: str
    name: str
    votes: list[int]
    valid: int
    rejected: int
    cast: int
    registered: int


def _line(code: str, name: str, numbers: str) -> Line:
    values = [int(x.replace(",", "")) for x in numbers.split()]
    return Line(code, name, values[:8], *values[8:])


def president(text: str) -> tuple[list[Line], list[Line], Line, Line]:
    """The constituency lines, the county total lines, the diaspora line, and the grand total line of the presidential
    table's text."""
    constituencies, counties = [], []
    diaspora = grand = None
    for line in text.splitlines():
        if match := ROW.match(line):
            constituencies.append(_line(match[1], match[2].strip(), match[3]))
        elif match := COUNTY_TOTAL.match(line):
            counties.append(_line("", "COUNTY TOTAL", match[1]))
        elif match := DIASPORA.match(line):
            diaspora = _line("", "DIASPORA", match[1])
        elif match := GRAND.match(line):
            grand = _line("", "GRAND TOTAL", match[1])
    if diaspora is None or grand is None:
        raise ValueError("the presidential table has no diaspora or grand total line")
    return constituencies, counties, diaspora, grand
