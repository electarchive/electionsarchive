"""Reads the 2004-2005 local election PDFs of the Higher Committee for Local Elections into ruled tables.

Each report is a stack of landscape pages (stored rotated by 90 degrees) holding one ruled table. A block of rows
belongs to one local authority, whose electoral district (governorate), number, and name are printed once in cells
that span the block; a block that does not fit a page continues on the next without repeating them. Every box here
is in the page as displayed, which is the page the archive renders for its reviewers.
"""

import itertools
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import pymupdf

from ea.sources.ps.cec_local_tables import Box, Cell


class Word(NamedTuple):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Spec:
    """Where the columns of a table are: the x of each vertical rule, the column names, and how rows are found."""

    edges: tuple[float, ...]
    names: tuple[str, ...]
    block_columns: int  # the leading columns whose cells span a block of rows
    anchor: str  # a column that holds a number in every data row
    header: str = "Gender"  # a word of the header, whose line ends the header
    top: float = 0.0  # where the table starts on a page that does not repeat the header
    rows: str = "rules"  # "rules": rows lie between ruled lines; "anchors": a row is a line with an anchor number
    row_rule_x: float | None = None  # the x where the rules between rows start
    block_rule_x: float | None = None  # the x where the rules between blocks start


@dataclass
class Block:
    page: int
    labels: dict[str, Cell] = field(default_factory=dict)
    rows: list[dict[str, Cell]] = field(default_factory=list)
    top: bool = False  # the fragment starts at the top of its page


def displayed(page: pymupdf.Page) -> list[Word]:
    matrix = page.rotation_matrix
    out = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        rect = pymupdf.Rect(x0, y0, x1, y1) * matrix
        out.append(Word(text, rect.x0, rect.y0, rect.x1, rect.y1))
    return out


def _rules(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """The horizontal ruled lines of a page as (x0, x1, y), each drawn as a thin rectangle."""
    matrix = page.rotation_matrix
    out: set[tuple[float, float, float]] = set()
    for drawing in page.get_drawings():
        rect = drawing["rect"] * matrix
        if rect.height < 2 and rect.width > 8:
            out.add((round(rect.x0, 1), round(rect.x1, 1), round((rect.y0 + rect.y1) / 2, 1)))
    return sorted(out, key=lambda r: r[2])


def _levels(values: list[float], tolerance: float = 1.5) -> list[float]:
    out: list[float] = []
    for value in sorted(values):
        if not out or value - out[-1] > tolerance:
            out.append(value)
    return out


def _lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.y):
        if lines and word.y - lines[-1][0].y < 3.5:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _cell(words: list[Word], page: int) -> Cell:
    text = " ".join(w.text for line in _lines(words) for w in line)
    if not words:
        return Cell("", Box(page, 0, 0, 0, 0))
    return Cell(
        re.sub(r"\s+", " ", text).strip(),
        Box(
            page, min(w.x0 for w in words), min(w.y0 for w in words), max(w.x1 for w in words), max(w.y1 for w in words)
        ),
    )


def _column(spec: Spec, x: float) -> str | None:
    for index, name in enumerate(spec.names):
        if spec.edges[index] <= x < spec.edges[index + 1]:
            return name
    return None


def _split(words: list[Word], spec: Spec, page: int) -> dict[str, Cell]:
    columns: dict[str, list[Word]] = defaultdict(list)
    for word in words:
        name = _column(spec, word.x)
        if name is not None:
            columns[name].append(word)
    return {name: _cell(columns.get(name, []), page) for name in spec.names}


def _merge(into: dict[str, Cell], more: dict[str, Cell]) -> None:
    """Joins the carried-on text of a row cut by a page break to the part of the row printed before it."""
    for name, cell in more.items():
        if not cell.text:
            continue
        old = into[name]
        into[name] = Cell(f"{old.text} {cell.text}".strip(), old.box if old.text else cell.box)


FOOTER = re.compile(r"^(Page|of|\d+)$")


def read(path: Path, spec: Spec) -> list[Block]:
    """Every fragment of the table's cells that span rows, page by page, with the labels printed in it.

    A row can be cut by a page break, so that its name or its result carries on at the top of the next page (or
    below the last ruled line of its own page); the carried-on text is joined to the row it belongs to.
    """
    document = pymupdf.open(path)
    blocks: list[Block] = []
    for number in range(1, document.page_count + 1):
        page = document[number - 1]
        words = displayed(page)
        every = _rules(page)
        edge = spec.edges[spec.block_columns]
        right = [w for w in words if spec.edges[0] <= w.x < spec.edges[-1]]
        header = [w for w in right if w.text == spec.header]
        floor = min((r[2] for r in every if r[2] > max(w.y1 for w in header)), default=0.0) if header else spec.top
        # the table's cells hold every word between the columns' outer rules, below the header, above the footer
        footer = min((w.y for w in words if w.text == "of" and w.y > 450), default=10_000.0)
        body = [w for w in right if w.y0 > floor and w.y < footer - 3]
        anchors = sorted(
            (w for w in body if _column(spec, w.x) == spec.anchor and re.fullmatch(r"[\d,]+", w.text)),
            key=lambda w: w.y,
        )
        if not body:
            continue
        block_ys = _levels([r[2] for r in every if spec.block_rule_x is not None and r[0] <= spec.block_rule_x + 1.5])
        if spec.rows == "rules":
            assert spec.row_rule_x is not None
            ys = [y for y in _levels([r[2] for r in every if r[0] <= spec.row_rule_x + 1.5]) if y > floor]
            low, high = min(w.y0 for w in body), max(w.y1 for w in body)
            if not ys or low < ys[0] - 1:
                ys = [low - 1, *ys]
                block_ys = _levels([low - 1, *block_ys])
            if high > ys[-1] + 1:
                ys = [*ys, high + 1]
                block_ys = _levels([*block_ys, high + 1])
            bands = list(itertools.pairwise(ys))
        else:
            mids = [(a.y + b.y) / 2 for a, b in itertools.pairwise(anchors)]
            edges = [anchors[0].y - 8, *mids, anchors[-1].y + 8] if anchors else []
            bands = list(itertools.pairwise(edges))
        if not bands:
            continue
        block_ys = _levels(
            [y for y in block_ys if bands[0][0] - 1 <= y <= bands[-1][1] + 1] + [bands[0][0], bands[-1][1]]
        )
        first_band = bands[0]
        for low, high in itertools.pairwise(block_ys):
            inside = [(a, b) for a, b in bands if low <= (a + b) / 2 <= high]
            if not inside:
                continue
            block = Block(number, top=inside[0] == first_band)
            block.labels = _split([w for w in body if low < w.y < high and w.x < edge], spec, number)
            for a, b in inside:
                row = _split([w for w in body if a <= w.y < b and w.x >= edge], spec, number)
                if any(a < w.y < b for w in anchors):
                    block.rows.append(row)
                elif (a, b) == first_band and blocks and blocks[-1].rows and number == blocks[-1].page + 1:
                    _merge(blocks[-1].rows[-1], row)  # the rest of a row cut by the page break
                elif block.rows:
                    _merge(block.rows[-1], row)  # text below the last ruled line of the page
                elif blocks and blocks[-1].rows and blocks[-1].page == number:
                    _merge(blocks[-1].rows[-1], row)
            if block.rows or any(c.text for c in block.labels.values()):
                blocks.append(block)
    return blocks


@dataclass
class Group:
    """The rows of one local authority and the labels printed for it."""

    labels: dict[str, Cell]
    rows: list[dict[str, Cell]]
    pages: list[int]


def groups(
    blocks: list[Block],
    score: Callable[[list[dict[str, Cell]], int], int],
    fit: Callable[[list[dict[str, Cell]], dict[str, Cell], int, int], int],
    key: str = "authority",
    anchor: str = "votes",
) -> list[Group]:
    """Cuts a table's rows into one group per local authority.

    A cell that spans a block's rows prints its label once, wherever the page layout puts it, and the cell is
    often drawn in pieces, so neither a label nor a ruled line marks where a block starts. Each authority's rows
    are consecutive and include the piece that carries its label; the cut between two authorities lies between
    the rows of the one label's piece and the other's. `score` says how likely a cut is before row i, and `fit`
    how well the rows from one cut to the next suit the authority's label (a size, or the seats a council has). A
    cut at a piece that starts mid-page is likelier than one at the top of a page, which continues a cell.
    """
    rows: list[dict[str, Cell]] = []
    spans: list[tuple[int, int, Block]] = []  # a labeled fragment's first and last row, and the fragment
    starts: dict[int, int] = {}
    extras: list[tuple[int, dict[str, Cell]]] = []  # pieces of a label's cells printed apart from the authority's name
    for block in blocks:
        first = len(rows)
        rows.extend(block.rows)
        if block.rows and not block.labels[key].text and any(c.text for c in block.labels.values()):
            extras.append((first + len(block.rows) // 2, block.labels))
        if block.rows:
            starts[first] = 2 if not block.top else 1
        if block.labels[key].text and block.rows:
            label = block.labels[key].box
            middle = (label.y0 + label.y1) / 2
            nearest = min(
                range(len(block.rows)),
                key=lambda i: abs((block.rows[i][anchor].box.y0 + block.rows[i][anchor].box.y1) / 2 - middle),
            )
            spans.append((first + nearest, first + nearest, block))
    ranges = [range(end + 1, start + 1) for (_, end, _), (start, _, _) in itertools.pairwise(spans)]

    def cost(i: int) -> int:
        return score(rows, i) * 10 + starts.get(i, 0)

    best: list[dict[int, tuple[int, int]]] = []  # per cut: position -> (total, previous position)
    for index, candidates in enumerate(ranges):
        layer: dict[int, tuple[int, int]] = {}
        for q in candidates:
            if index == 0:
                layer[q] = (cost(q) + fit(rows, spans[0][2].labels, 0, q), 0)
            else:
                options = [
                    (total + cost(q) + fit(rows, spans[index][2].labels, p, q), p)
                    for p, (total, _) in best[index - 1].items()
                    if p < q
                ]
                if options:
                    layer[q] = max(options)
        best.append(layer)
    cuts = [0, len(rows)]
    if ranges:
        last_labels = spans[-1][2].labels
        last = max(best[-1], key=lambda q: (best[-1][q][0] + fit(rows, last_labels, q, len(rows)), -q))
        chosen = [last]
        for index in range(len(ranges) - 1, 0, -1):
            chosen.append(best[index][chosen[-1]][1])
        cuts = [0, *reversed(chosen), len(rows)]
    out: list[Group] = []
    for (_, _, block), low, high in zip(spans, cuts, cuts[1:], strict=False):
        labels = dict(block.labels)
        for at, more in extras:
            if low <= at < high:
                for name, cell in more.items():
                    if cell.text and not labels[name].text:
                        labels[name] = cell
        pages = sorted({b.page for b in blocks if b.rows and low <= _first(blocks, b) < high} | {block.page})
        out.append(Group(labels, rows[low:high], pages))
    return out


def _first(blocks: list[Block], block: Block) -> int:
    count = 0
    for other in blocks:
        if other is block:
            return count
        count += len(other.rows)
    return count
