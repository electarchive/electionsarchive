"""Bihar's 2019 Lok Sabha general election by polling station, from the Form 20 final result sheets the Chief Electoral
Officer, Bihar posts (part of the `in.form20` source; `ea parse in.form20` builds it).

The Chief Electoral Officer's site (ceoelection.bihar.gov.in) lists the 2019 sheets on two pages under its Form 20
menu: "LSGE_2019_AC_Wise_Report.html" links one Part I sheet per assembly segment (`Form20_New/Loksabha2019_ACWISE/
AC<segment>.pdf`, all 243), and "LSGE_2019_PC_Wise_Report.html" links one Part II sheet per constituency
(`Form20/GE-2019/PC<pc>.pdf`, all 40).

Part I has a row per polling station: the station's serial number, the votes each candidate polled on the voting
machines in ballot order, the valid votes, the rejected votes, the votes for none of the above (NOTA), the total, and
the tendered votes, with a total row at the foot and the segment's electors in the heading. Part II has a row per
segment with the same columns, the machine total, the postal ballots, and the grand total. Both were printed from the
counting application's report pages and carry a text layer, read here with each word's position.

A segment is kept only when every station row satisfies its own arithmetic (the candidates add up to the valid votes,
and the valid, rejected, and NOTA votes to the total), every column adds up to the total row the sheet prints, and
that row equals the segment's row on Part II. A constituency is kept only when Part II's segment rows add up to its
machine total, its machine and postal rows to its grand total, and each candidate's machine, postal, and grand totals
equal the Election Commission's constituency-wise detailed result that `in.eci_stat` holds.

The 2019 contests, their candidates, and NOTA are those `in.eci_stat` files under the 2019 general election from that
detailed result; each Form 20 set attaches to its contest and options, column by column in ballot order, and stays
beside the default constituency count.
"""

import functools
import html
import importlib
import itertools
import re
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid5

import polars as pl
import pymupdf

from ea import eci_workbook, package, raw
from ea.package import Package

eci_stat = importlib.import_module("ea.sources.in.eci_stat")

SOURCE = "in.form20"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
SITE = "https://ceoelection.bihar.gov.in/"
AC_INDEX = SITE + "LSGE_2019_AC_Wise_Report.html"
PC_INDEX = SITE + "LSGE_2019_PC_Wise_Report.html"
ECI_STATE = "S04"  # the Commission's state code
LGD = "10"  # the Local Government Directory's state code, which in.geo's constituency codes carry
STATION_SCHEME, SEGMENT_SCHEME, PC_SCHEME = "in.ps", "in.ac", "in.pc"
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
ALL, ELECTION_DAY, POSTAL = 0, 1, 3
YEAR = 2019
PACKAGE = f"{SOURCE}/ls-{YEAR}-br"


def _ids(pipeline: str) -> Callable[..., str]:
    return Package(pipeline, pipeline, "ids").id


_GEO = _ids("in.geo")
_STAT = _ids("in.eci_stat")
key = _ids(SOURCE)
ELECTION = str(uuid5(package.NAMESPACE, "in.form20_wb\x1felection\x1f2019"))
SOURCE_ID = key("source", f"br-{YEAR}")


def pc_code(pc: int) -> str:
    return f"{LGD}{pc:02d}"


def pc_area(pc: int) -> str:
    return _GEO("area", PC_SCHEME, pc_code(pc), "2008")


def ac_area(ac: int) -> str:
    return _GEO("area", SEGMENT_SCHEME, f"{LGD}{ac:03d}", "2008")


def contest_id(pc: int) -> str:
    return _STAT("contest", f"{YEAR}:{pc_code(pc)}")


def option_id(pc: int, index: int) -> str:
    """The option in.eci_stat gives the index-th line (from 1) of a constituency in the detailed result."""
    return _STAT("option", f"{YEAR}:{pc_code(pc)}", index)


# ---- the raw files -------------------------------------------------------------------------------------------------


def _links(url: str, pattern: str) -> list[tuple[str, str]]:
    """The sheets an index page links, with the text of each link."""
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    found = re.findall(r'href="(' + pattern + r')"[^>]*>(.*?)</a>', text, re.DOTALL)
    return list(dict.fromkeys((u, " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())) for u, t in found))


def ac_links() -> list[tuple[str, str]]:
    return _links(AC_INDEX, r"http://ceoelection\.bihar\.gov\.in/Form20_New/Loksabha2019_ACWISE/AC\d+\.pdf")


def pc_links() -> list[tuple[str, str]]:
    return _links(PC_INDEX, r"http://ceoelection\.bihar\.gov\.in/Form20/GE-2019/PC\d+\.pdf")


def fetch() -> None:
    """The two index pages and every sheet they link. The detailed result is in.eci_stat's (`ea fetch in.eci_stat`)."""
    for url in (AC_INDEX, PC_INDEX):
        raw.fetch(SOURCE, url, suffix=".html")
    urls = [u for u, _ in ac_links()] + [u for u, _ in pc_links()]
    raw.fetch_many(SOURCE, urls, suffix=".pdf", workers=6, batch=50)


def _cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


def ready() -> bool:
    return _cached(AC_INDEX) is not None and _cached(PC_INDEX) is not None


def _number(pattern: str, url: str) -> int:
    m = re.search(pattern, url)
    if m is None:
        raise ValueError(f"{url} does not match {pattern}")
    return int(m.group(1))


@functools.cache
def segments() -> dict[int, tuple[str, str]]:
    """Each segment the index lists: its sheet's URL and its name."""
    out: dict[int, tuple[str, str]] = {}
    for url, text in ac_links():
        m = re.fullmatch(r"(\d+)\s*-\s*(.+?)\)?", text.strip())
        ac = _number(r"AC(\d+)\.pdf$", url)
        name = m.group(2).strip() if m else text
        if name.count("(") > name.count(")"):
            name += ")"
        out[ac] = (url, name)
    return dict(sorted(out.items()))


# ---- reading the text layer ----------------------------------------------------------------------------------------

type Word = tuple[float, float, float, float, str]
NUMBER = re.compile(r"\d+")
SERIAL = re.compile(r"\d+[A-Za-z]?")


def _lines(page: pymupdf.Page) -> list[list[Word]]:
    """The page's words grouped into lines by their vertical centres, each line left to right, on the page as shown
    (Part II is printed on a turned page)."""
    turn = page.rotation_matrix
    words: list[Word] = []
    for w in page.get_text("words"):
        box = pymupdf.Rect(w[:4]) * turn
        words.append((box.x0, box.y0, box.x1, box.y1, w[4]))
    grouped: list[list[Word]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (w[1] + w[3]) / 2
        if grouped and abs((grouped[-1][-1][1] + grouped[-1][-1][3]) / 2 - centre) <= 2.5:
            grouped[-1].append(w)
        else:
            grouped.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in grouped]


def _numbering(texts: list[str]) -> bool:
    """The row that numbers the columns: 1, 2, 3, and so on."""
    return all(t == str(i) for i, t in enumerate(texts, start=1)) or (
        all(NUMBER.fullmatch(t) for t in texts) and all(int(b) == int(a) + 1 for a, b in itertools.pairwise(texts))
    )


@dataclass
class Row:
    station: str
    votes: list[int]
    valid: int
    rejected: int
    nota: int
    total: int
    clipped: bool = False  # the total's last digits fell off the page; it is the valid, rejected, and NOTA votes

    def figures(self) -> list[int]:
        return [*self.votes, self.valid, self.rejected, self.nota, self.total]


@dataclass
class Sheet:
    url: str
    ac: int
    k: int
    pc: int | None = None  # the constituency the heading names
    electors: int | None = None
    rows: list[Row] = field(default_factory=list)
    printed: list[int] | None = None  # the total row: candidates, valid, rejected, NOTA, total
    problems: list[str] = field(default_factory=list)

    def summed(self) -> list[int]:
        return [sum(x) for x in zip(*(r.figures() for r in self.rows), strict=True)] if self.rows else []


HEADING = re.compile(r"FROM\s+THE\s+(\d+)\s*-", re.IGNORECASE)
ELECTORS = re.compile(r"Electors\s+in\s+Assembly\s+Constituency\s*/\s*Segment\s*:?\s*(\d+)", re.IGNORECASE)
SEGMENT_NAME = re.compile(r"Name\s+of\s+the\s+Assembly\s+Segment\s*:?\s*[^()]*\((\d+)\)", re.IGNORECASE)


LABEL = re.compile(r"Grand|Total", re.IGNORECASE)


@dataclass
class Line:
    y: float
    label: bool  # the line carries the total row's label
    words: list[Word]  # its figures (and a station's serial number)


def _figure_lines(page: pymupdf.Page) -> list[Line]:
    """The page's lines of figures: a station's serial number and figures, or the total row's label and figures, or a
    fragment of figures that wrapped in their cells."""
    out: list[Line] = []
    for line in _lines(page):
        texts = [w[4] for w in line]
        if _numbering(texts) or texts[:10] == [str(i) for i in range(1, 11)]:
            continue  # the row that numbers the columns, perhaps with its last number wrapped
        lead = 0
        while lead < len(texts) and LABEL.fullmatch(texts[lead]):
            lead += 1
        rest = line[lead:]
        if not rest:
            if lead:
                out.append(Line((line[0][1] + line[0][3]) / 2, True, []))
            continue
        if not (SERIAL.fullmatch(rest[0][4]) and all(NUMBER.fullmatch(w[4]) for w in rest[1:])):
            continue
        out.append(Line(statistics.median((w[1] + w[3]) / 2 for w in rest), lead > 0, rest))
    return out


def _overlap(a: Word, b: Word) -> bool:
    return a[0] < b[2] - 0.5 and b[0] < a[2] - 0.5


def _rows(lines: list[Line], width: int) -> list[tuple[bool, list[str], float]]:
    """The rows of figures: each line with most of a row's cells, joined by the fragments nearest it of figures that
    wrapped onto a second line in their cells (above and below it where the row centres its cells), each cell's parts
    in reading order; and whether the row carries the total row's label."""
    main = [x for x in lines if len(x.words) >= width - 4]
    if not main:
        return []
    parts: list[list[Line]] = [[x] for x in main]
    labelled = [x.label for x in main]
    for x in lines:
        if x in main:
            continue
        # A cell whose top line sits on its row's line continues below it; where a row centres its cells, a wrapped
        # cell has parts above and below the row's line and none on it.
        above = [
            i
            for i, r in enumerate(main)
            if 0 < x.y - r.y <= 12 and all(any(_overlap(w, v) for v in r.words) for w in x.words)
        ]
        near = above[-1] if above else min(range(len(main)), key=lambda i: abs(main[i].y - x.y))
        if abs(main[near].y - x.y) > 12:
            continue
        if x.words:
            parts[near].append(x)
        if x.label:
            labelled[near] = True
    out: list[tuple[bool, list[str], float]] = []
    for label, group in zip(labelled, parts, strict=True):
        words = sorted((w for x in group for w in x.words), key=lambda w: w[0])
        cells: list[list[Word]] = []
        for w in words:
            if cells and w[0] < max(c[2] for c in cells[-1]) - 0.5:
                cells[-1].append(w)
            else:
                cells.append([w])
        texts = ["".join(w[4] for w in sorted(c, key=lambda w: w[1])) for c in cells]
        out.append((label, texts, max(w[2] for w in cells[-1]) if cells else 0.0))
    return out


def _shown(document: pymupdf.Document) -> int | None:
    """How many columns the row that numbers them shows: a sheet printed from a page wider than the paper loses its
    right-hand columns."""
    found: list[int] = []
    for page in document:
        for line in _lines(page):
            texts = [w[4] for w in line]
            n = 0
            while n < len(texts) and texts[n] == str(n + 1):
                n += 1
            if n >= 10:
                found.append(n)
    return max(found, default=None)


def _kind(cells: list[str], k: int) -> str | None:
    """A row's kind, from its arithmetic: a station's row has a serial number, then candidates that add up to its
    valid votes; the total row has the candidates first."""
    if not cells or not all(NUMBER.fullmatch(c) for c in cells[1:]):
        return None
    if len(cells) >= k + 4 and sum(int(c) for c in cells[1 : k + 1]) == int(cells[k + 1]):
        return "station"
    if NUMBER.fullmatch(cells[0]) and len(cells) >= k + 3 and sum(int(c) for c in cells[:k]) == int(cells[k]):
        return "total"
    if k + 4 <= len(cells) <= k + 6:
        return "station"  # one that does not add up, reported below
    return None


def _clipped(printed: int, whole: int, at_edge: bool) -> bool:
    """Whether a total is the sum of its parts with its last digits cut off at the page's right edge: some sheets were
    printed from a page wider than the paper, which lost the tendered votes and the end of the total."""
    return at_edge and printed != whole and str(whole).startswith(str(printed))


def read_part_one(url: str, ac: int, k: int) -> Sheet:
    """A segment's sheet: a row per station with its serial number, the candidates' votes, and the valid, rejected,
    NOTA, total, and (on most sheets) tendered votes, and a total row without a serial number."""
    document = pymupdf.open(raw.cached(SOURCE, url).path)
    sheet = Sheet(url, ac, k)
    text = " ".join(" ".join(document[0].get_text().split()).split())
    if m := HEADING.search(text):
        sheet.pc = int(m.group(1))
    if m := ELECTORS.search(text):
        sheet.electors = int(m.group(1))
    if (m := SEGMENT_NAME.search(text)) and int(m.group(1)) != ac:
        sheet.problems.append(f"the heading names segment {m.group(1)}")
    shown = _shown(document) or k + 6
    if shown < k + 4:
        sheet.problems.append(
            f"only {shown} of the sheet's {k + 6} columns are on the page; the rest fell off its edge"
        )
        return sheet
    totals: list[list[int]] = []
    for page in document:
        edge = page.rect.width - 30
        for _, cells, right in _rows(_figure_lines(page), shown):
            kind = _kind(cells, k)
            if kind is None:
                sheet.problems.append(
                    f"a row of {len(cells)} cells that is neither a station's nor the total: {cells[:3]}"
                )
                continue
            lead = 1 if kind == "station" else 0
            n = [int(c) for c in cells[lead : lead + k + 4]]
            whole = n[k] + n[k + 1] + n[k + 2]
            clipped = False
            if len(n) == k + 3 and (right > edge or shown <= k + 5):
                n.append(whole)  # the total column, the last the page shows, fell off it
                clipped = True
            elif len(n) == k + 4 and len(cells) == lead + k + 4 and _clipped(n[k + 3], whole, right > edge):
                n[k + 3] = whole
                clipped = True
            if len(n) != k + 4:
                sheet.problems.append(f"a row without its total: {cells[:3]}")
            elif kind == "station":
                sheet.rows.append(Row(cells[0].upper(), n[:k], n[k], n[k + 1], n[k + 2], n[k + 3], clipped))
            else:
                totals.append(n)
    summed = sheet.summed()
    for row in sheet.rows:
        if sum(row.votes) != row.valid or row.valid + row.rejected + row.nota != row.total:
            sheet.problems.append(f"station {row.station} does not add up")
    if repeated := [s for s, n in Counter(r.station for r in sheet.rows).items() if n > 1]:
        sheet.problems.append(f"repeated stations {repeated[:5]}")
    if not sheet.rows:
        sheet.problems.append("no station rows in the text layer")
        return sheet
    sheet.printed = next((t for t in totals if t == summed), totals[-1] if totals else None)
    if sheet.printed is None:
        sheet.problems.append("no printed total row")
    elif sheet.printed != summed:
        sheet.problems.append(f"the columns sum to {summed}, the sheet prints {sheet.printed}")
    return sheet


@dataclass
class PartTwo:
    url: str
    segments: dict[int, list[int]]  # each segment's row: candidates, valid, rejected, NOTA, total
    evm: list[int]
    postal: list[int]
    polled: list[int]


ROW_LABEL = re.compile(r"(\d+)\s*-?\s*[^\d\s-].*")  # "51-Sikti", printed once as "51Sikti-"


def _cell(text: str | None) -> str:
    """A cell's text on one line: a figure too wide for its cell wraps onto a second line."""
    return "".join((text or "").split())


def read_part_two(url: str, k: int) -> PartTwo | str:
    """A constituency's sheet, read from its ruled table: a row per segment, then the machine total, the postal
    ballots, and the grand total, each with k + 5 figures (the candidates, valid, rejected, NOTA, total, and tendered
    votes; the postal row may leave the tendered votes out)."""
    document = pymupdf.open(raw.cached(SOURCE, url).path)
    segments: dict[int, list[int]] = {}
    labelled: dict[str, list[int]] = {}
    for page in document:
        for table in page.find_tables().tables:
            for row in table.extract():
                cells = [_cell(c) for c in row]
                if not cells or not cells[0]:
                    continue
                figures = cells[1:]
                if len(figures) < k + 4 or not all(NUMBER.fullmatch(c) for c in figures[: k + 4]):
                    continue
                values = [int(c) for c in figures[: k + 4]]
                if m := ROW_LABEL.fullmatch(cells[0]):
                    segments[int(m.group(1))] = values
                else:
                    labelled[cells[0].lower()] = values
    evm, postal, polled = labelled.get("total"), labelled.get("postalballot"), labelled.get("grandtotal")
    if evm is None or postal is None or polled is None:
        return f"the total, postal, and grand total rows were not all read ({sorted(labelled)})"
    if [sum(x) for x in zip(*segments.values(), strict=True)] != evm:
        return "the segments' rows don't add up to the machine total"
    if [a + b for a, b in zip(evm, postal, strict=True)] != polled:
        return "the machine and postal rows don't add up to the grand total"
    for figures in (*segments.values(), evm, postal, polled):
        if sum(figures[:k]) != figures[k] or figures[k] + figures[k + 1] + figures[k + 2] != figures[k + 3]:
            return "a row does not add up"
    return PartTwo(url, segments, evm, postal, polled)


# ---- putting a constituency together -------------------------------------------------------------------------------


@functools.cache
def detailed() -> dict[int, list[eci_workbook.Line]]:
    """Each Bihar constituency's lines in the detailed result in.eci_stat reads, by constituency number."""
    return {
        int(s.code[2:]): list(s.seat.lines)
        for s in eci_stat.workbook(YEAR)
        if s.code.startswith(LGD) and len(s.code) == 4
    }


@dataclass
class Segment:
    ac: int
    row: list[int]  # its row on Part II: the candidates, valid, rejected, NOTA, and total votes
    sheet: Sheet | None  # its Part I sheet, where its station rows are kept
    rest: list[int] | None  # the votes Part II gives that the sheet's station rows don't hold
    why: str | None  # why its stations are not kept, or not all of them

    @property
    def complete(self) -> bool:
        return self.sheet is not None and self.rest is None


@dataclass
class Seat:
    pc: int
    name: str
    lines: list[eci_workbook.Line]  # in.eci_stat's lines: the candidates in ballot order, then NOTA
    part_two: PartTwo | None
    segments: list[Segment]
    why: str | None = None  # why the constituency is not kept
    differences: list[str] = field(default_factory=list)  # where Part II's totals differ from the detailed result

    @property
    def k(self) -> int:
        return len(self.lines) - 1


def _against_detailed(two: PartTwo, lines: list[eci_workbook.Line]) -> list[str]:
    """Where Part II's machine, postal, and grand totals differ from the detailed result's, column by column."""
    k = len(lines) - 1
    found: list[str] = []
    for j, line in enumerate(lines):
        at = j if j < k else k + 2
        mine = (two.evm[at], two.postal[at], two.polled[at])
        theirs = (line.general, line.postal, line.total)
        if mine != theirs:
            who = "NOTA" if j == k else f"candidate {j + 1} ({line.name})"
            found.append(
                f"{who}: Part II gives {mine[0]:,} machine, {mine[1]:,} postal, {mine[2]:,} in all; the detailed "
                f"result {theirs[0]:,}, {theirs[1]:,}, {theirs[2]:,}"
            )
    return found


SHORT = ("the columns sum to", "its totals differ from its row on Part II")


def segment(ac: int, url: str | None, row: list[int], pc: int, k: int) -> Segment:
    """A segment of a constituency, from its Part I sheet and its row on Part II. Some sheets leave out a few stations:
    their stations each add up, but the sheet's columns fall short of its own total row, or of its row on Part II. Its
    stations are kept then, and the votes they don't hold are kept for the segment as a whole."""
    if url is None or _cached(url) is None:
        return Segment(ac, row, None, row, "no copy of its sheet was fetched")
    sheet = read_part_one(url, ac, k)
    if sheet.pc is not None and sheet.pc != pc:
        sheet.problems.append(f"the heading names constituency {sheet.pc}")
    if not sheet.problems and sheet.printed != row:
        sheet.problems.append("its totals differ from its row on Part II")
    if not sheet.problems:
        return Segment(ac, row, sheet, None, None)
    rest = [a - b for a, b in zip(row, sheet.summed(), strict=True)] if sheet.rows else None
    if (
        rest is not None
        and all(p.startswith(SHORT) for p in sheet.problems)
        and all(x >= 0 for x in rest)
        and sum(rest[:k]) == rest[k]
        and rest[k] + rest[k + 1] + rest[k + 2] == rest[k + 3]
        and (sheet.printed is None or sheet.printed in (row, sheet.summed()))
    ):
        found = sheet.summed()
        where = "its own total row" if sheet.printed == row else "its row on Part II"
        why = (
            f"the sheet's {len(sheet.rows)} station rows hold {found[k + 3]:,} of the {row[k + 3]:,} votes {where} "
            "gives"
        )
        return Segment(ac, row, sheet, rest, why)
    return Segment(ac, row, None, row, "; ".join(sheet.problems[:2]))


@functools.cache
def seats() -> list[Seat]:
    found = detailed()
    listed = segments()
    out: list[Seat] = []
    for url, text in pc_links():
        pc = _number(r"PC(\d+)\.pdf$", url)
        name = re.sub(r"^\d+\s*-\s*", "", text).strip()
        lines = found[pc]
        k = len(lines) - 1
        two = read_part_two(url, k) if _cached(url) else "no copy of the sheet was fetched"
        if isinstance(two, str):
            out.append(Seat(pc, name, lines, None, [], f"Part II: {two}"))
            continue
        if lines[-1].party != "NOTA":
            out.append(Seat(pc, name, lines, None, [], "the detailed result's last line is not NOTA"))
            continue
        differences = _against_detailed(two, lines)
        if len(differences) > 1:
            out.append(Seat(pc, name, lines, None, [], "Part II differs from the detailed result"))
            continue
        kept = [
            segment(ac, listed[ac][0] if ac in listed else None, row, pc, k) for ac, row in sorted(two.segments.items())
        ]
        out.append(Seat(pc, name, lines, two, kept, None, differences))
    return sorted(out, key=lambda s: s.pc)


# ---- the package ---------------------------------------------------------------------------------------------------

METHOD = (
    "Counted at each polling station on the voting machines, as the Form 20 Part I sheet of each assembly segment "
    "prints it (Chief Electoral Officer, Bihar), read from the sheets' text layer with each word's position; a figure "
    "too wide for its cell wraps onto a second line and is joined. Every station's candidates add up to its valid "
    "votes, and its valid, rejected, and NOTA votes to its total. A segment's stations are complete when its columns "
    "add up to the total row its sheet prints and to the segment's row on the Returning Officer's Part II sheet. Part "
    "II's segments, machine total, postal ballots, and grand total add up, and each candidate's machine, postal, and "
    "total votes there are compared with the Election Commission's constituency-wise detailed result, the default "
    "count of this contest. Polling stations are numbered as the sheets number them and nested in their assembly "
    "segment, which carries the electors its sheet prints. Ballots cast are the station's total votes (valid, "
    "rejected, and NOTA); tendered votes are not stored. The postal ballots, which are counted for the whole "
    "constituency, are stored on a constituency-wide unit from Part II."
)
CLIPPED = (
    " Some sheets were printed from a page wider than the paper and lost their right-hand columns: the tendered "
    "votes, and the end or all of each station's total. Such a total is the station's valid, rejected, and NOTA votes "
    "({count} stations here); what is left of the printed total agrees with it."
)
REST = (
    " Where a segment's sheet leaves out some of its votes (its station rows fall short of the total row it prints, "
    "or of its row on Part II), or where its sheet cannot be read, the votes its station rows don't hold are kept on "
    "an aggregate unit of the segment: Part II's row for the segment less the station rows, flagged as derived and "
    "as reported only above the finest unit."
)
DERIVED, UNALLOCATED = 1, 32  # result_value.flags


def packages() -> list[str]:
    return [PACKAGE] if ready() and any(s.part_two for s in seats()) else []


def election() -> Package:
    p = Package(
        PACKAGE,
        SOURCE,
        "The 2019 Lok Sabha general election in Bihar by polling station, from the Form 20 sheets the Chief Electoral "
        "Officer posts, attached to the constituency contests of the Election Commission's detailed result",
        depends=["in.eci_stat/ls-2019"],
    )
    p.external |= {INDIA, ELECTION, STATION_SCHEME, SEGMENT_SCHEME, PC_SCHEME}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Chief Electoral Officer, Bihar",
        title="Form 20 final result sheets, 2019 Lok Sabha general election, Bihar",
        url=SITE,
        license="Official publication of the Election Commission of India",
        attribution="Chief Electoral Officer, Bihar, Form 20 final result sheets",
        notes=f"Part I sheets by assembly segment are listed on {AC_INDEX}, Part II sheets by constituency on "
        f"{PC_INDEX}.",
    )
    p.uses(raw.cached(SOURCE, AC_INDEX))
    p.uses(raw.cached(SOURCE, PC_INDEX))
    units: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    for seat in seats():
        if seat.part_two:
            _contest(p, seat, units, values)
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None, schema_overrides={"option_id": pl.String}))
    return p


def _value(
    rows: list[dict[str, object]],
    rs: str,
    unit: str,
    option: str | None,
    channel: int,
    measure: int,
    v: int,
    flags: int = 0,
) -> None:
    rows.append(
        {
            "result_set_id": rs,
            "unit_id": unit,
            "option_id": option,
            "channel": channel,
            "measure": measure,
            "count_round": 0,
            "flags": flags,
            "value": v,
        }
    )


def _figures(
    values: list[dict[str, object]],
    rs: str,
    unit: str,
    options: list[str],
    nota: str,
    channel: int,
    row: list[int],
    flags: int = 0,
) -> None:
    """A row's figures (the candidates, valid, rejected, NOTA, and total votes) on a unit."""
    k = len(options)
    for option, votes in zip(options, row[:k], strict=True):
        _value(values, rs, unit, option, channel, VOTES, votes, flags)
    _value(values, rs, unit, nota, channel, VOTES, row[k + 2], flags)
    _value(values, rs, unit, None, channel, INVALID, row[k + 1], flags)
    _value(values, rs, unit, None, channel, BALLOTS, row[k + 3], flags)


def _contest(p: Package, seat: Seat, units: list[dict[str, object]], values: list[dict[str, object]]) -> None:
    assert seat.part_two is not None
    contest = contest_id(seat.pc)
    options = [option_id(seat.pc, j) for j in range(1, seat.k + 1)]
    nota = option_id(seat.pc, seat.k + 1)
    p.external |= {contest, nota, *options}
    rs = key("result_set", YEAR, ECI_STATE, seat.pc)
    complete = [s.ac for s in seat.segments if s.complete]
    p.add(
        "result_set",
        id=rs,
        election_id=ELECTION,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=False,
        completeness=round(len(complete) / len(seat.segments), 4),
    )
    clipped = sum(r.clipped for s in seat.segments if s.sheet for r in s.sheet.rows)
    body = METHOD + (CLIPPED.format(count=f"{clipped:,}") if clipped else "")
    partial = [s for s in seat.segments if not s.complete]
    if partial:
        body += REST
    body += f" Segments with every station: {', '.join(map(str, complete)) or 'none'} of {len(seat.segments)}."
    if partial:
        body += " Others: " + "; ".join(f"segment {s.ac}, {s.why}" for s in partial) + "."
    if seat.differences:
        body += (
            " Part II and the detailed result differ: " + "; ".join(seat.differences) + ". The figures here are "
            "Part II's."
        )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
    names = segments()
    for s in seat.segments:
        ac = s.ac
        area = ac_area(ac)
        p.external.add(area)
        unit_id = key("unit", YEAR, ECI_STATE, seat.pc, ac)
        units.append(
            {
                "id": unit_id,
                "polity_id": INDIA,
                "scheme_code": SEGMENT_SCHEME,
                "code": f"{YEAR}:{ECI_STATE}:{seat.pc}:{ac}",
                "area_relation": "equals",
                "area_id": area,
                "kind": "standard",
            }
        )
        p.name(unit_id, f"{ac} {names[ac][1]}" if ac in names else str(ac), "en", "official")
        if s.sheet is not None:
            sheet = s.sheet
            p.artifact(raw.cached(SOURCE, sheet.url), SOURCE_ID)
            if sheet.electors and sheet.electors >= s.row[seat.k + 3]:
                _value(values, rs, unit_id, None, ALL, REGISTERED, sheet.electors)
            for r in sheet.rows:
                unit = key("unit", YEAR, LGD, ac, r.station)
                units.append(
                    {
                        "id": unit,
                        "polity_id": INDIA,
                        "scheme_code": STATION_SCHEME,
                        "code": f"{YEAR}:{LGD}{ac:03d}:{r.station}",
                        "parent_id": unit_id,
                        "area_relation": "within",
                        "area_id": area,
                        "kind": "standard",
                    }
                )
                _figures(values, rs, unit, options, nota, ELECTION_DAY, r.figures())
        if s.rest is not None:
            rest = key("unit", YEAR, ECI_STATE, seat.pc, ac, "rest")
            units.append(
                {
                    "id": rest,
                    "polity_id": INDIA,
                    "scheme_code": SEGMENT_SCHEME,
                    "code": f"{YEAR}:{ECI_STATE}:{seat.pc}:{ac}:rest",
                    "parent_id": unit_id,
                    "area_relation": "within",
                    "area_id": area,
                    "kind": "aggregate",
                }
            )
            label = "votes not itemized by station" if s.sheet is not None else "votes of the segment as a whole"
            p.name(rest, f"{ac} {names.get(ac, ('', ''))[1]}: {label}".replace("  ", " "), "en", "official")
            flags = UNALLOCATED | (DERIVED if s.sheet is not None else 0)
            _figures(values, rs, rest, options, nota, ELECTION_DAY, s.rest, flags)
    two = seat.part_two
    p.artifact(raw.cached(SOURCE, two.url), SOURCE_ID)
    p.external.add(pc_area(seat.pc))
    postal = key("unit", YEAR, ECI_STATE, seat.pc, "postal")
    units.append(
        {
            "id": postal,
            "polity_id": INDIA,
            "scheme_code": PC_SCHEME,
            "code": f"{YEAR}:{ECI_STATE}:{seat.pc}:postal",
            "area_relation": "equals",
            "area_id": pc_area(seat.pc),
            "kind": "postal_district",
        }
    )
    p.name(postal, f"{seat.name}: postal ballots", "en", "official")
    _figures(values, rs, postal, options, nota, POSTAL, two.postal)


def coverage() -> dict[str, object]:
    """Each constituency: its segments, whether each one's stations are all kept, and why not."""
    return {
        str(s.pc): {
            "name": s.name,
            "why": s.why,
            "segments": {
                str(x.ac): {"stations": len(x.sheet.rows) if x.sheet else 0, "complete": x.complete, "why": x.why}
                for x in s.segments
            },
        }
        for s in seats()
    }
