"""Party-list votes in the Election Commission's result books of the 2079 (2022) elections.

The House of Representatives book prints each party's list votes for every constituency (one table for each party),
the national totals and the seats of the parties above the threshold, and the registered, cast, valid, and invalid
list votes of every constituency. The Provincial Assembly book prints each province's party totals and seats and the
province's valid and invalid votes. Both books are drawn in Kalimati with a private glyph mapping, so the words are
read through `glyphs`.
"""

import re
from dataclasses import dataclass, field

import pymupdf

from ea.sources.np import glyphs
from ea.sources.np.glyphs import Word

DEV = str.maketrans("०१२३४५६७८९", "0123456789")
INTEGER = re.compile(r"\d+")
TITLE = re.compile(r"^([०-९]+)\.([०-९]+)$")

# PDF page numbers (1-based) of the House book's tables.
HOR_CONSTITUENCIES = range(354, 362)
HOR_PARTIES = range(382, 524)
HOR_NATIONAL = range(370, 372)
HOR_SEATS = 372


def digits(text: str) -> str:
    return text.translate(DEV).replace(",", "")


def integer(word: Word) -> int | None:
    text = digits(word.text)
    return int(text) if INTEGER.fullmatch(text) else None


def spaced(words: list[Word]) -> str:
    """A name from its words, with single spaces and none before a comma or inside brackets."""
    text = " ".join(w.text for w in words)
    text = re.sub(r"\s+([,)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return " ".join(text.split())


def page_words(doc: pymupdf.Document, pno: int) -> list[Word]:
    """The words of a page (1-based), without the printed page number at the foot."""
    return [w for w in glyphs.words(doc[pno - 1]) if w.y < 780]


@dataclass
class Entry:
    serial: int
    y: float
    words: list[Word] = field(default_factory=list)
    page: int = 0


def entries(ws: list[Word], page: int, serial_x: float, top: float) -> list[Entry]:
    """The numbered rows of a table: each word below `top` belongs to the serial row nearest to it vertically."""
    anchors = [
        Entry(int(digits(w.text)), w.y, [], page)
        for w in ws
        if w.x0 < serial_x and w.y > top and INTEGER.fullmatch(digits(w.text))
    ]
    for w in ws:
        if w.y <= top or (w.x0 < serial_x and INTEGER.fullmatch(digits(w.text))):
            continue
        if not anchors:
            continue
        nearest = min(anchors, key=lambda a: abs(a.y - w.y))
        if abs(nearest.y - w.y) <= 14:
            nearest.words.append(w)
    for a in anchors:
        a.words.sort(key=lambda w: (round(w.y / 3), w.x0))
    return anchors


def header_y(ws: list[Word], marker: str = "क्र.सं.") -> float:
    ys = [w.y for w in ws if w.text == marker]
    return max(ys) + 14 if ys else 0.0


EDGES = (445.0, 517.0, 642.0, 750.0)  # right edges of the registered, cast, valid, and invalid columns


def by_edge(ws: list[Word]) -> list[int]:
    """The four whole numbers of a constituency row, found by the right edge of their column."""
    out: list[int] = []
    for edge in EDGES:
        got = [v for w in ws if abs(w.x1 - edge) < 6 and (v := integer(w)) is not None]
        if len(got) != 1:
            raise ValueError(f"column at {edge}: {got} in {[w.text for w in ws]}")
        out.append(got[0])
    return out


@dataclass
class Constituency:
    district: str
    number: int
    registered: int
    cast: int
    valid: int
    invalid: int
    page: int


def constituencies(doc: pymupdf.Document) -> tuple[list[Constituency], tuple[int, int, int, int]]:
    """Registered, cast, valid, and invalid list votes of each constituency, with the printed national totals."""
    out: list[Constituency] = []
    total: tuple[int, int, int, int] | None = None
    for pno in HOR_CONSTITUENCIES:
        ws = page_words(doc, pno)
        for a in entries(ws, pno, 100, header_y(ws)):
            district = spaced([w for w in a.words if 170 <= w.x0 < 335 and integer(w) is None])
            number = next(integer(w) for w in a.words if 335 <= w.x0 < 365 and integer(w) is not None)
            figures = by_edge(a.words)
            if number is None:
                raise ValueError(f"page {pno}: {a.serial} {district}: figures {figures}")
            out.append(Constituency(district, number, *figures, pno))
        last = [w for w in ws if w.text == "जम्मा" and w.x0 < 280]
        if last:
            a, b, c, d = by_edge([w for w in ws if abs(w.y - last[0].y) < 4])
            total = (a, b, c, d)
    if total is None:
        raise ValueError("no total row for the constituencies")
    return out, total


@dataclass
class PartyGrid:
    number: int
    name: str
    cells: list[tuple[int, str, int, int]]  # serial, district name, constituency, votes
    total: int  # the sum of the cells; the tables print no column totals
    pages: list[int]


def party_grids(doc: pymupdf.Document) -> list[PartyGrid]:
    """Each party's list votes in every constituency, checked against the printed total of each district."""
    out: list[PartyGrid] = []
    current: dict | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        cells = current["cells"]
        out.append(PartyGrid(current["number"], current["name"], cells, sum(c[3] for c in cells), current["pages"]))
        current = None

    for pno in HOR_PARTIES:
        ws = page_words(doc, pno)
        title = next((w for w in ws if w.x0 < 80 and w.y < 120 and TITLE.match(w.text)), None)
        found = TITLE.match(title.text) if title is not None else None
        header = [w for w in ws if w.text == "क्र.सं."]
        if title is not None:
            finish()
            line = [w for w in ws if abs(w.y - title.y) < 3 and w is not title]
            extra = [w for w in ws if title.y + 3 < w.y < (header[0].y - 3 if header else title.y + 40)]
            current = {
                "number": int(digits(found.group(2))) if found else 0,
                "name": spaced(sorted(line, key=lambda w: w.x0) + sorted(extra, key=lambda w: (w.y, w.x0))),
                "cells": [],
                "pages": [],
            }
        if current is None or not header:
            continue
        current["pages"].append(pno)
        centers: dict[int, float] = {}
        line = [w for w in ws if abs(w.y - header[0].y) < 4]
        for w in line:
            text = digits(w.text)
            if INTEGER.fullmatch(text) and 1 <= int(text) <= 10 and w.x0 > 280:
                centers[int(text)] = (w.x0 + w.x1) / 2
        total_center = next(((w.x0 + w.x1) / 2 for w in line if w.text == "जम्मा"), 0.0)
        columns = {**centers, 0: total_center}

        def column_of(w: Word, columns: dict[int, float] = columns) -> int:
            mid = (w.x0 + w.x1) / 2
            return min(columns, key=lambda c: abs(columns[c] - mid))

        for a in entries(ws, pno, 100, header_y(ws)):
            district = spaced([w for w in a.words if w.x0 < 280 and integer(w) is None])
            row_total = 0
            row_sum = 0
            for w in a.words:
                votes = integer(w)
                if w.x0 < 280 or votes is None:
                    continue
                column = column_of(w)
                if column == 0:
                    row_total = votes
                else:
                    current["cells"].append((a.serial, district, column, votes))
                    row_sum += votes
            if row_sum != row_total:
                raise ValueError(f"party {current['number']} page {pno} {district}: {row_sum} != {row_total}")
    finish()
    return out


@dataclass
class ListRow:
    serial: int
    name: str
    votes: int
    seats: int | None
    page: int


def party_rows(
    doc: pymupdf.Document,
    pages: range,
    serial_x: float,
    name_x: tuple[float, float],
    votes_x: tuple[float, float],
    seats_x: tuple[float, float] | None = None,
    stop: str | None = None,
) -> tuple[list[ListRow], tuple[int, int | None] | None]:
    """Party rows of a table (a name that wraps joins its lines), and the printed total row if there is one."""
    out: list[ListRow] = []
    total: tuple[int, int | None] | None = None
    for pno in pages:
        ws = page_words(doc, pno)
        if stop:
            cut = next((w.y for w in ws if w.text == stop and w.x0 < 60 and w.y > 100), None)
            if cut is not None:
                ws = [w for w in ws if w.y < cut]
        for a in entries(ws, pno, serial_x, header_y(ws)):
            name = spaced([w for w in a.words if name_x[0] <= w.x0 < name_x[1]])
            votes = [v for w in a.words if votes_x[0] <= w.x0 < votes_x[1] and (v := integer(w)) is not None]
            seats = (
                [v for w in a.words if seats_x[0] <= w.x0 < seats_x[1] and (v := integer(w)) is not None]
                if seats_x
                else []
            )
            if len(votes) != 1 or len(seats) > 1:
                raise ValueError(f"page {pno} row {a.serial} {name}: votes {votes} seats {seats}")
            out.append(ListRow(a.serial, name, votes[0], seats[0] if seats else None, pno))
        last = next((w for w in ws if w.text == "जम्मा" and w.x0 < name_x[1]), None)
        if last is not None:
            votes = [v for w in ws if abs(w.y - last.y) < 4 and votes_x[0] <= w.x0 < votes_x[1] and (v := integer(w))]
            seats = (
                [v for w in ws if abs(w.y - last.y) < 4 and seats_x[0] <= w.x0 < seats_x[1] and (v := integer(w))]
                if seats_x
                else []
            )
            total = (votes[0], seats[0] if seats else None)
    return out, total


def national(doc: pymupdf.Document) -> tuple[list[ListRow], int]:
    """The House book's national table of party list votes and its printed total."""
    rows, total = party_rows(doc, HOR_NATIONAL, 70, (80, 420), (420, 500))
    if total is None:
        raise ValueError("no total for the national list")
    return rows, total[0]


def national_seats(doc: pymupdf.Document) -> dict[int, int]:
    """List seats of the parties above the threshold, by their votes, from the book's sixth table."""
    rows, _ = party_rows(doc, range(HOR_SEATS, HOR_SEATS + 1), 70, (80, 340), (340, 410), (460, 505), stop="७.")
    return {r.votes: r.seats for r in rows if r.seats is not None}


def provinces(doc: pymupdf.Document) -> dict[int, tuple[list[ListRow], tuple[int, int], tuple[int, int, int, int]]]:
    """For each province (1-7): its party rows with seats, the printed total of votes and seats, and the registered,
    cast, valid, and invalid list votes of the whole province."""
    heads: dict[tuple[int, int], int] = {}
    for pno in range(100, doc.page_count + 1):
        ws = page_words(doc, pno)
        for w in ws:
            m = TITLE.match(w.text)
            if m and w.y < 120 and w.x0 < 120:
                key = (int(digits(m.group(1))), int(digits(m.group(2))))
                if key[1] in (7, 8, 9) and 1 <= key[0] <= 7:
                    heads.setdefault(key, pno)
    out: dict[int, tuple[list[ListRow], tuple[int, int], tuple[int, int, int, int]]] = {}
    for province in range(1, 8):
        first, after = heads[(province, 7)], heads[(province, 8)]
        rows, total = party_rows(doc, range(first, after), 100, (95, 385), (385, 445), (450, 520))
        if total is None or total[1] is None:
            raise ValueError(f"province {province}: no total row")
        valid_start = heads[(province, 9)]
        found: tuple[int, int, int, int] | None = None
        for pno in range(valid_start, valid_start + 4):
            ws = page_words(doc, pno)
            last = next((w for w in ws if w.text == "जम्मा" and w.x0 < 200), None)
            if last is not None:
                line = sorted(
                    (w for w in ws if abs(w.y - last.y) < 4 and w.x0 > 300 and "." not in w.text), key=lambda w: w.x0
                )
                figures = [v for w in line if (v := integer(w)) is not None]
                if len(figures) != 4:
                    raise ValueError(f"province {province}: total row {figures}")
                found = (figures[0], figures[1], figures[2], figures[3])
                break
        if found is None:
            raise ValueError(f"province {province}: no valid and invalid total")
        out[province] = (rows, (total[0], total[1]), found)
    return out
