"""The municipal table of the 1931 prohibition referendum, from pages 15 to 26 of Suomen virallinen tilasto XXIX C 1.

The volume prints one row for each province, its towns and rural municipalities as a whole, and each town and rural
municipality. A row has, for men, women, and both: the persons entitled to vote, the persons who voted, the percentage
who voted, the rejected ballots, the valid ballots, the votes for alternatives I, II, and III, and the percentages of
the valid ballots. The text layer of the scan is read as `digitized` reads every volume: a figure is the words whose
right edge lies on its column, and the arithmetic the table satisfies settles a misread digit.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ea.sources.fi import digitized
from ea.sources.fi.digitized import Box

FIRST_PAGE, LAST_PAGE = 15, 26
SEXES = ("men", "women", "both")
# the 21 printed counts, in the order of the table: three figures (men, women, both) for each
REGISTERED, CAST, REJECTED, VALID, FIRST, SECOND, THIRD = range(7)
NAMES = ("registered", "cast", "rejected", "valid", "first", "second", "third")
# the right edges of the 33 columns of page 17, which the other pages follow with a shift of a few points
CANON = (
    242, 274, 305, 333, 360, 392, 415, 438, 461, 487, 510, 533, 598, 624, 655, 679, 703, 730, 752, 771, 793, 818, 843,
    870, 890, 908, 930, 952, 972, 993, 1016, 1037, 1058, 1070,
)  # fmt: skip
# the columns of the counts among them: the percentages (6 to 8 and 24 to 32) and the row's running number (33) are not
COUNTS = tuple(range(6)) + tuple(range(9, 24))
CELL = {column: (index // 3, index % 3) for index, column in enumerate(COUNTS)}
RUNNING = 33
WINDOW = 7.0

DIGITS = re.compile(r"^[\dOolIiZSB]+$")
NIL = {"-", "—", "–", "_", "~", "“", "”"}


@dataclass
class Cell:
    text: str
    boxes: list[Box]

    @property
    def box(self) -> Box:
        return Box(
            self.text,
            min(b.x0 for b in self.boxes),
            min(b.y0 for b in self.boxes),
            max(b.x1 for b in self.boxes),
            max(b.y1 for b in self.boxes),
        )


@dataclass
class Record:
    """A row of the table as the recognition read it."""

    page: int
    y: float
    name: str = ""
    number: int | None = None  # the running number printed on the left of the row
    cells: dict[int, Cell] = field(default_factory=dict)  # by column
    notes: list[str] = field(default_factory=list)
    mark: float | None = None  # the height of the running number on the left


def calibrate(page: digitized.Page) -> list[float]:
    """The right edges of the page's columns: the shared layout shifted and scaled to the page's own figures."""
    xs = sorted(
        round(w.x1) for w in page.words if w.y0 > 120 and w.text.replace(".", "").isdigit() and 200 < w.x1 < 1100
    )
    counts = {x: xs.count(x) for x in set(xs)}
    peaks: list[float] = []
    for x in range(200, 1100):
        around = sum(counts.get(x + d, 0) for d in range(-2, 3))
        strongest = around >= max(sum(counts.get(x + d + e, 0) for d in range(-2, 3)) for e in range(-8, 9))
        if around >= 15 and strongest and (not peaks or x - peaks[-1] >= 6):
            peaks.append(x)
    # each block of the table (the left one to the rejected ballots, the right one from the valid ones) shifts apart
    edges: list[float] = []
    for block in (range(12), range(12, len(CANON))):
        mine = [peak for peak in peaks if (peak < 570) == (block[0] == 0)]

        def matches(shift: int, block: range = block, mine: list[float] = mine) -> int:
            return sum(any(abs(peak - CANON[c] - shift) <= 2.5 for peak in mine) for c in block)

        edges.extend(CANON[c] + max(range(-40, 41), key=matches) for c in block)
    return edges


Half = tuple[float, dict[int, Cell]]


def merge(halves: list[Half], size: int) -> list[Half]:
    """Join the pieces a row's half broke into: neighbors that disagree in no column and together fit one half."""
    out: list[Half] = []
    for half in halves:
        if out:
            last = out[-1]
            conflicts = [
                c
                for c in set(last[1]) & set(half[1])
                if last[1][c].text != half[1][c].text and last[1][c].text not in NIL and half[1][c].text not in NIL
            ]
            union = {**half[1], **{c: cell for c, cell in last[1].items() if cell.text not in NIL or c not in half[1]}}
            if not conflicts and abs(half[0] - last[0]) <= 10 and sum(1 for c in union if c in CELL) <= size:
                out[-1] = ((last[0] + half[0]) / 2, union)
                continue
        out.append(half)
    return out


def figure(half: Half | None, column: int) -> int | None:
    cell = half[1].get(column) if half else None
    return digitized.number(cell.text) if cell else None


def fits(left: Half, right: Half) -> float:
    """How well a left half and a right half make one row: the ballots cast exceed the valid ones by about the
    rejected ones."""
    cast, valid, rejected = figure(left, 5), figure(right, 14), figure(left, 11)
    if cast is None or valid is None:
        return 0.0
    gap = cast - valid
    if rejected is not None and gap == rejected:
        return 4.0
    return 2.0 if 0 <= gap <= max(40, cast // 15) else -1.5


def align(lefts: list[Half], rights: list[Half]) -> list[tuple[Half | None, Half | None]]:
    """Pair the left and right halves of a page in order, leaving out the pieces that have no partner."""
    n, m = len(lefts), len(rights)
    offsets = sorted(r[0] - l[0] for l, r in zip(lefts[:15], rights[:15], strict=False))
    offset = offsets[len(offsets) // 2] if offsets else 0.0
    gap = -2.5
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        best[i][0], back[i][0] = best[i - 1][0] + gap, "u"
    for j in range(1, m + 1):
        best[0][j], back[0][j] = best[0][j - 1] + gap, "l"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dy = abs(rights[j - 1][0] - lefts[i - 1][0] - offset)
            score = fits(lefts[i - 1], rights[j - 1]) - min(dy, 12) * 0.15
            options = (
                (best[i - 1][j - 1] + score, "d"),
                (best[i - 1][j] + gap, "u"),
                (best[i][j - 1] + gap, "l"),
            )
            best[i][j], back[i][j] = max(options)
    i, j = n, m
    out: list[tuple[Half | None, Half | None]] = []
    while i > 0 or j > 0:
        move = back[i][j]
        if move == "d":
            out.append((lefts[i - 1], rights[j - 1]))
            i, j = i - 1, j - 1
        elif move == "u":
            out.append((lefts[i - 1], None))
            i -= 1
        else:
            out.append((None, rights[j - 1]))
            j -= 1
    return out[::-1]


def read_page(page: digitized.Page) -> list[Record]:
    edges = calibrate(page)
    header = max((w.y1 for w in page.words if w.text in ("Total", "Summa") and w.x1 < 330), default=0.0)
    figures: list[Box] = []
    left: list[Box] = []
    for word in page.words:
        if word.y0 <= header + 1:
            continue
        if word.x1 > 200:
            if DIGITS.match(word.text) or word.text in NIL or re.match(r"^[\d.,]+$", word.text):
                figures.append(word)
        elif word.x1 < 235:
            left.append(word)

    def height(word: Box) -> float:
        return (word.y0 + word.y1) / 2

    # each figure to the column whose right edge it ends on; the words before it that end on no edge are its thousands
    cells: dict[int, list[Cell]] = {}
    ordered = sorted(figures, key=lambda w: -w.x1)
    owners: list[tuple[Box, int]] = []
    loose: list[Box] = []
    for word in ordered:
        column = min(range(len(edges)), key=lambda i: abs(edges[i] - word.x1))
        if abs(edges[column] - word.x1) <= WINDOW:
            owners.append((word, column))
        else:
            loose.append(word)
    cell_of = {id(word): Cell(word.text, [word]) for word, _ in owners}
    for word in sorted(loose, key=lambda w: w.x1, reverse=True):
        near = [
            (owner, column)
            for owner, column in owners
            if owner.x1 > word.x1 and owner.x1 - word.x1 < 45 and abs(height(owner) - height(word)) < 3
        ]
        if near:
            owner, column = min(near, key=lambda item: item[0].x1)
            cell = cell_of[id(owner)]
            cell.boxes.insert(0, word)
            cell.text = "".join(box.text for box in cell.boxes)
    for word, column in owners:
        cells.setdefault(column, []).append(cell_of[id(word)])

    # a row is the chain of figures that follow one another from column to column at about one height, which holds
    # on a page that was scanned askew, where the heights of one row drift apart across the page. The two halves of a
    # page can sit at different heights, so each half chains by itself and the halves are paired afterwards.
    def chain(columns: range) -> list[tuple[float, dict[int, Cell]]]:
        chains: list[tuple[float, dict[int, Cell]]] = []
        for column in columns:
            taken: set[int] = set()
            for cell in sorted(cells.get(column, []), key=lambda c: height(c.boxes[-1])):
                y = height(cell.boxes[-1])
                best = None
                for index, (last, members) in enumerate(chains):
                    if index in taken or column in members:
                        continue
                    if abs(last - y) <= 4.5 and (best is None or abs(last - y) < abs(chains[best][0] - y)):
                        best = index
                if best is None:
                    chains.append((y, {column: cell}))
                    taken.add(len(chains) - 1)
                else:
                    chains[best][1][column] = cell
                    chains[best] = (y, chains[best][1])
                    taken.add(best)
        return sorted(chains, key=lambda item: item[0])

    def rows_at(columns: range, slope: float) -> list[list[tuple[int, Cell, float]]]:
        members = [
            (column, cell, height(cell.boxes[-1]) - slope * cell.boxes[-1].x1)
            for column in columns
            for cell in cells.get(column, [])
        ]
        members.sort(key=lambda item: item[2])
        rows: list[list[tuple[int, Cell, float]]] = []
        for item in members:
            if rows and item[2] - rows[-1][-1][2] <= 3.4:
                rows[-1].append(item)
            else:
                rows.append([item])
        return rows

    def slope_of(columns: range) -> float:
        """The tilt of a half of the page: the one that sorts the figures into the most rows that hold no column
        twice, which is what a wrong tilt breaks by running two rows together."""

        def clean(slope: float) -> int:
            score = 0
            for row in rows_at(columns, slope):
                counted = [c for c, _, _ in row if c in CELL]
                if len(counted) >= 6:
                    score += 1 if len(counted) == len(set(counted)) else -1
            return score

        candidates = [step / 5000 for step in range(-38, 39)]
        return max(candidates, key=lambda slope: (clean(slope), -abs(slope)))

    def straight(columns: range) -> list[Half]:
        """The rows of a half of the page: its figures once the tilt is taken out, by the height they share."""
        out: list[Half] = []
        for row in rows_at(columns, slope_of(columns)):
            by_column: dict[int, Cell] = {}
            for column, cell, _ in row:
                by_column.setdefault(column, cell)
            if sum(1 for c in by_column if c in CELL) >= 3:
                out.append((sum(y for _, _, y in row) / len(row), by_column))
        return out

    lefts = straight(range(12))
    rights = straight(range(12, 34))
    lefts = merge(lefts, 9)
    rights = merge(rights, 12)
    pairs = align(lefts, rights)
    records: list[Record] = []
    for index, (lhalf, rhalf) in enumerate(pairs):
        record = Record(page.number, 0.0, number=index + 1)
        if lhalf is not None:
            record.y = lhalf[0]
            record.cells.update(lhalf[1])
        else:
            record.notes.append("no left half")
        if rhalf is not None:
            record.cells.update(rhalf[1])
            if lhalf is None:
                record.y = rhalf[0]
        else:
            record.notes.append("no right half")
        records.append(record)
    # the names: a row starts at its running number on the left and runs down to the next number, and it holds the
    # row of figures that lies in that stretch. A row printed without figures keeps its name and no figures.
    marks = sorted(height(w) for w in left if w.x1 <= 62.5)
    page_end = max((height(w) for w in page.words), default=0.0) + 20
    blocks: list[Record] = []
    taken: set[int] = set()
    for index, start in enumerate(marks):
        stop = marks[index + 1] if index + 1 < len(marks) else page_end
        block = Record(page.number, start)
        block.mark = start
        words = [w for w in left if w.x1 > 62.5 and start - 2.5 <= height(w) < stop - 2.5]
        block.name = " ".join(w.text for w in sorted(words, key=lambda w: (round(height(w) / 4), w.x0)))
        inside = [
            (abs(r.y - start - 3.0), id(r), r)
            for r in records
            if start - 3.5 <= r.y < stop - 3.5 and id(r) not in taken
        ]
        if inside:
            owner = min(inside)[2]
            taken.add(id(owner))
            owner.name, owner.mark = block.name, start
        else:
            block.notes.append("no figures")
            blocks.append(block)
    return sorted([*records, *blocks], key=lambda r: r.y)


def read_pages(path: Path) -> dict[int, list[Record]]:
    pages = digitized.pages(path, FIRST_PAGE, LAST_PAGE)
    return {number: read_page(page) for number, page in pages.items()}


# ---------------------------------------------------------------------------------------------------------------------
# The arithmetic of a row

Grid = list[list[int | None]]


def grid_of(record: Record) -> Grid:
    grid: Grid = [[None] * 3 for _ in range(7)]
    for column, (kind, sex) in CELL.items():
        cell = record.cells.get(column)
        if cell is not None:
            grid[kind][sex] = digitized.number(cell.text)
    return grid


def equations() -> list[list[tuple[int, int, int]]]:
    """The identities of a row as lists of (kind, sex, sign) that add up to zero."""
    out: list[list[tuple[int, int, int]]] = []
    for kind in range(7):
        out.append([(kind, 0, 1), (kind, 1, 1), (kind, 2, -1)])
    for sex in range(3):
        out.append([(CAST, sex, 1), (VALID, sex, -1), (REJECTED, sex, -1)])
        out.append([(VALID, sex, 1), (FIRST, sex, -1), (SECOND, sex, -1), (THIRD, sex, -1)])
    return out


EQUATIONS = equations()


def broken(grid: Grid) -> list[int]:
    """The identities the row's figures do not satisfy, or cannot be tested by for want of a figure."""
    bad = []
    for index, equation in enumerate(EQUATIONS):
        values = [grid[k][s] for k, s, _ in equation]
        known = [v for v in values if v is not None]
        if len(known) < len(values) or sum(v * sign for v, (_, _, sign) in zip(known, equation, strict=True)) != 0:
            bad.append(index)
    return bad


def matrix() -> list[list[int]]:
    rows = []
    for equation in EQUATIONS:
        row = [0] * 21
        for kind, sex, sign in equation:
            row[kind * 3 + sex] = sign
        rows.append(row)
    return rows


def edits(a: str, b: str) -> int:
    """The number of characters to change, add, or drop to turn one string into the other."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@dataclass
class Reading:
    values: list[int]  # the 21 counts, in the order of the table
    changed: dict[int, tuple[str, int]]  # the cells the sums corrected: what the scan read and what they give
    certain: bool = True
    image: bool = False


# Rows whose figures the text layer lost or ran into the next row's, read from the page image, by page and name. Each
# is men, women, and both for the registered, cast, rejected, valid, first, second, and third.
FROM_IMAGE = {
    (18, "Ikaalinen"): (2737, 2905, 5642, 1307, 784, 2091, 5, 4, 9, 1302, 780, 2082, 436, 399, 835, 26, 11, 37, 840, 370, 1210),
    (18, "Jomala"): (821, 930, 1751, 345, 380, 725, 2, 0, 2, 343, 380, 723, 6, 7, 13, 0, 0, 0, 337, 373, 710),
    (18, "Lumparland"): (162, 181, 343, 95, 99, 194, 0, 0, 0, 95, 99, 194, 0, 0, 0, 1, 0, 1, 94, 99, 193),
    (18, "Lemland"): (484, 559, 1043, 244, 272, 516, 0, 1, 1, 244, 271, 515, 0, 0, 0, 1, 0, 1, 243, 271, 514),
    (25, "Siikajoki"): (404, 413, 817, 215, 144, 359, 1, 0, 1, 214, 144, 358, 77, 63, 140, 3, 5, 8, 134, 76, 210),
}  # fmt: skip


# A figure the sums of its row cannot settle alone, which the sums of its province settle: the cell's index among the 21
CORRECTED = {(25, "Kärsämäki"): {2: 1518}}  # the registered men and women are 798 and 720


def settle(record: Record) -> Reading | None:
    """The row's 21 counts: as read when the row's sums hold, else with the fewest corrections that make them hold,
    each a figure the recognition could have misread. None when no such correction exists."""
    import itertools

    import numpy as np

    for (page, name), values in FROM_IMAGE.items():
        if record.page == page and record.name.startswith(name):
            assert not broken([list(values[i : i + 3]) for i in range(0, 21, 3)]), name  # type: ignore[arg-type]
            return Reading(list(values), {}, image=True)
    grid = grid_of(record)
    for (page, name), cells in CORRECTED.items():
        if record.page == page and record.name.startswith(name):
            for i, v in cells.items():
                grid[i // 3][i % 3] = v
    read: list[int | None] = [grid[i // 3][i % 3] for i in range(21)]
    text = [
        (record.cells[c].text if (c := next(c for c, (k, s) in CELL.items() if k * 3 + s == i)) in record.cells else "")
        for i in range(21)
    ]
    blank = [i for i in range(21) if not text[i]]
    # a blank cell of the rejected ballots is a zero; a blank of any other count is lost
    for i in blank:
        if i // 3 == REJECTED:
            read[i] = 0
    if all(v is not None for v in read) and not broken([read[i : i + 3] for i in range(0, 21, 3)]):  # type: ignore[misc]
        return Reading([v or 0 for v in read], {})
    unknown = [i for i in range(21) if read[i] is None or (i in blank and i // 3 == REJECTED)]
    mat = np.array(matrix(), dtype=float)
    best: tuple[int, int, list[int], dict[int, int]] | None = None
    ties = 0
    for extra in range(4):
        pool = [i for i in range(21) if i not in unknown]
        for chosen in itertools.combinations(pool, extra):
            free = [*unknown, *chosen]
            fixed = [i for i in range(21) if i not in free]
            if any(read[i] is None for i in fixed):
                continue
            a = mat[:, free]
            if np.linalg.matrix_rank(a) < len(free):
                continue
            rhs = -mat[:, fixed] @ np.array([read[i] for i in fixed], dtype=float)
            solution, *_ = np.linalg.lstsq(a, rhs, rcond=None)
            if np.abs(a @ solution - rhs).max() > 1e-6 or np.abs(solution - np.round(solution)).max() > 1e-6:
                continue
            values = [round(x) for x in solution]
            if min(values) < 0:
                continue
            cost = 0
            ok = True
            for i, v in zip(free, values, strict=True):
                if i in unknown:
                    continue
                digits = text[i].translate(digitized.READ)
                distance = edits(digits, str(v))
                if distance > (2 if text[i].isdigit() and len(digits) >= len(str(v)) else 9):
                    ok = False
                    break
                cost += distance
            if not ok:
                continue
            full = [int(v) if v is not None else 0 for v in read]
            for i, v in zip(free, values, strict=True):
                full[i] = v
            candidate = (extra, cost, full, dict(zip(free, values, strict=True)))
            if best is None or (candidate[0], candidate[1]) < (best[0], best[1]):
                best, ties = candidate, 0
            elif (candidate[0], candidate[1]) == (best[0], best[1]) and candidate[2] != best[2]:
                ties += 1
        if best is not None:
            break
    if best is None:
        return None
    changed = {i: (text[i], v) for i, v in best[3].items() if i not in unknown}
    return Reading(best[2], changed, certain=ties == 0)


# ---------------------------------------------------------------------------------------------------------------------
# The rows of the municipalities

# Names the recognition garbled or lost, read from the page image, by page and running number
NAME_FIXES = {
    (16, 50): "Sauvo (Sagu)",
    (17, 14): "Uskela",
    (18, 19): "Huittinen (Hvittis)",
    (19, 52): "Hattula",
    (20, 45): "Kirvu",
    (20, 46): "Jääski",
    (20, 47): "Antrea (S:t Andreæ)",
    (21, 49): "Sysmä",
    (23, 8): "Säyneinen",
    (24, 55): "Lappajärvi",
    (26, 5): "Säräisniemi",
    (26, 29): "Yli-Ii",
    (15, 16): "Ingå (med Fagervik) — Inkoo (ynnä Fagervik)",
    (15, 34): "Brändö villastad — Brandon huvilakaupunki (Kulosaari)",
    (15, 3): "Helsinki ja Suomenlinna — Helsingfors med Sveaborg",
    (16, 17): "Pori ynnä Reposaari — Björneborg med Räfsö",
    (17, 7): "Perniö (ynnä Yliskylä) — Bjernå (med Överby)",
    (18, 4): "Ikaalinen, Ikaalisten k:la — Ikalis kp.",
    (25, 47): "Siikajoki",
}


def clean_name(page: int, number: int | None, text: str) -> str:
    if number is not None and (page, number) in NAME_FIXES:
        return NAME_FIXES[page, number]
    text = text.replace("&quot;", "").replace("•", "")
    text = re.sub(r"\b1k\b", "lk", text)
    text = re.sub(r"S: t", "S:t", text)
    text = re.sub(r"[\s.]*(?:\.\s*)+$", "", text) if re.search(r"\.\s+\.", text) else text
    return re.sub(r"\s+", " ", text).strip(" —")


@dataclass
class Leaf:
    province: int
    kind: str  # urban or rural
    seq: int  # the place in its group, in the order of the volume
    page: int
    name: str
    values: list[int]  # the 21 counts
    boxes: dict[int, Box]  # the boxes of the figures of both sexes together that were read as printed, by cell
    image: bool


def leaves(path: Path) -> list[Leaf]:
    """Every town and rural municipality of the table, each under its province."""
    out: list[Leaf] = []
    province, kind = -1, None
    seqs: dict[tuple[int, str], int] = {}
    for page, records in read_pages(path).items():
        for record in records:
            name = record.name
            if re.search(r"lääni|lään|Iän|Ahvenanmaa — Åland|Nylands|Hämeen l", name):
                province, kind = province + 1, None
                continue
            if re.search(r"Kaup\w+ — |Kaupunki|Städer|Kaummait", name) or (
                kind is None and not name and "no figures" not in record.notes
            ):
                kind = "urban"
                continue
            if re.search(r"Maaseutu|Landsbygd", name):
                kind = "rural"
                continue
            if "no figures" in record.notes:
                continue
            reading = settle(record)
            if reading is None or kind is None:
                raise ValueError(f"page {page} row {record.number} {name!r}: no reading")
            seqs[(province, kind)] = seq = seqs.get((province, kind), 0) + 1
            boxes: dict[int, Box] = {}
            for column, (k, sex) in CELL.items():
                index = k * 3 + sex
                cell = record.cells.get(column)
                read_as_printed = (
                    sex == 2
                    and cell is not None
                    and not reading.image
                    and index not in reading.changed
                    and digitized.number(cell.text) == reading.values[index]
                )
                if read_as_printed and cell is not None:
                    boxes[index] = cell.box
            out.append(
                Leaf(
                    province,
                    kind,
                    seq,
                    page,
                    clean_name(page, record.number, name),
                    reading.values,
                    boxes,
                    reading.image,
                )
            )
    return out
