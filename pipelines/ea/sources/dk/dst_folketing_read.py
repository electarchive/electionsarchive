"""Reading the per-nomination-district tables of Statistics Denmark's historical Folketing volumes.

The volumes are scans whose text layer is the scan's character recognition. `pdftotext -bbox` gives each word with its
box. A row is the words that share a height. A figure printed with spaces between its thousands (`2 794 007`) can come
out as one word or as several, so neighboring digit groups are joined before each figure is placed in the column whose
right edge it shares. A table that spans a spread is read page by page, and its rows are matched by their order.

Every figure keeps its page and box. `reconcile` checks each row's printed sums and each column's printed totals, and
mends a figure the recognition garbled only where both a row and a column prove the same value.
"""

import html
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE = re.compile(r'width="([\d.]+)" height="([\d.]+)">')
# Characters the recognition reads in place of a digit.
DIGIT = str.maketrans(
    {"O": "0", "o": "0", "Q": "0", "D": "0", "l": "1", "I": "1", "i": "1", "|": "1", "!": "1", "¡": "1", "]": "1"}
)
NIL = {"-", "--", "---", "—", "–", "_", "~", "..", "...", "-,", ",-", "-.", ".-", "·"}
GROUP = re.compile(r"^\d{3}$")
LEAD = re.compile(r"^\d{1,3}$")
FIGURE = re.compile(r"^[\dOolIi|!¡\]][\dOolIi|!¡\]. ]*[\'`,;:.r¡!]*$")


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    number: int
    width: float
    height: float
    words: list[Word]


def pages(path: Path, first: int, last: int) -> dict[int, Page]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: dict[int, Page] = {}
    for number, part in enumerate(re.split(r"<page ", out)[1:], first):
        size = PAGE.match(part)
        assert size
        words = [Word(html.unescape(m[5]), *map(float, m.group(1, 2, 3, 4))) for m in WORD.finditer(part)]
        found[number] = Page(number, float(size[1]), float(size[2]), words)
    return found


def lines(words: Iterable[Word], tolerance: float) -> list[list[Word]]:
    """The words of a page in reading order, grouped by their height."""
    ordered = sorted(words, key=lambda word: (word.y0 + word.y1) / 2)
    found: list[list[Word]] = []
    last = None
    for word in ordered:
        height = (word.y0 + word.y1) / 2
        if last is None or height - last > tolerance:
            found.append([])
        found[-1].append(word)
        last = height
    return [sorted(line, key=lambda word: word.x0) for line in found]


def figure(text: str) -> int | None:
    """A printed count: a nil mark is zero, and a figure the recognition garbled is None."""
    text = text.strip()
    parts = text.split(" ")
    if len(parts) > 1 and any(p.strip() in NIL for p in parts):
        kept = [p for p in parts if p.strip() not in NIL]
        text = " ".join(kept) if kept else parts[0]
    if text in NIL:
        return 0
    text = re.sub(r"[\'`,;:r¡!]+$", "", text) if len(text) > 1 else text
    digits = text.replace(" ", "").replace(".", "").translate(DIGIT)
    return int(digits) if digits.isdigit() else None


def numeric(text: str) -> bool:
    text = text.strip()
    if re.match(r"^\d{1,3},\d{1,2}$", text):
        return True  # a percentage, read into its own column and not stored
    if re.fullmatch(r"[Il|]{2,4}", text):
        return True  # ones read as letters
    return text in NIL or bool(FIGURE.match(text)) and any(c.isdigit() for c in text)


def joined(words: list[Word], gap: float) -> list[Word]:
    """Join the digit groups of one figure: a group of three digits close after a group of one to three digits."""
    out: list[Word] = []
    for word in words:
        if out:
            last = out[-1]
            tail = last.text.split(" ")[-1].split(".")[-1]
            head = word.text.split(" ")[0]
            close = word.x0 - last.x1 < gap
            if (
                close
                and (LEAD.match(tail) or GROUP.match(tail))
                and GROUP.match(head)
                and LEAD.match(last.text.split(" ")[0])
            ):
                out[-1] = Word(
                    f"{last.text} {word.text}", last.x0, min(last.y0, word.y0), word.x1, max(last.y1, word.y1)
                )
                continue
        out.append(word)
    return out


@dataclass
class Cell:
    value: int | None
    text: str
    page: int
    box: tuple[float, float, float, float]
    alternatives: list[int] = field(default_factory=list)
    mended: str | None = None


@dataclass
class Row:
    label: str
    page: int
    y: float
    cells: list[Cell | None]
    header: bool = False


def readings(text: str) -> list[int]:
    """Other values a garbled or misjoined figure could stand for: a stray rule or mark read as a digit at either
    end, or a digit lost."""
    digits = re.sub(r"[^0-9OolIi|!¡\]]", "", text).translate(DIGIT)
    found: list[int] = []
    if len(digits) > 1:
        found += [int(digits[:-1]), int(digits[1:])]
    swaps = {"1": "7", "7": "1", "3": "8", "8": "3", "5": "6", "6": "5", "0": "6", "9": "0", "4": "1"}
    for i, ch in enumerate(digits):
        if ch in swaps:
            found.append(int(digits[:i] + swaps[ch] + digits[i + 1 :]))
    return [value for value in found if value >= 0]


def clusters(rows: list[list[Word]], spread: float = 9.0) -> list[list[float]]:
    """Right edges of figures grouped where they line up, most used first."""
    edges = sorted(word.x1 for row in rows for word in row)
    found: list[list[float]] = []
    for edge in edges:
        if found and edge - found[-1][-1] <= spread:
            found[-1].append(edge)
        else:
            found.append([edge])
    return sorted(found, key=len, reverse=True)


def columns(rows: list[list[Word]], count: int, template: list[float] | None = None) -> list[float]:
    """The right edges of a page's figure columns: the `count` most used clusters of right edges. With a template
    from another page of the same table, a column this page leaves empty takes the template's place, shifted by how
    far the page's other columns sit from the template's."""
    found = clusters(rows)
    best = [c for c in found[:count] if len(c) >= 2]
    centers = sorted(sorted(c)[len(c) // 2] for c in best)
    if len(centers) == count:
        return centers
    if template is None or len(template) != count:
        raise ValueError(f"found {len(centers)} columns, expected {count}")
    centers = sorted(sorted(c)[len(c) // 2] for c in found if len(c) >= 2)
    shifts = sorted(min((c - t for t in template), key=abs) for c in centers)
    shift = shifts[len(shifts) // 2] if shifts else 0.0
    return [t + shift for t in template]


def label_edge(page: Page, top: float = 0, bottom: float = 1e9, tolerance: float = 3.0) -> float:
    """Where the row labels end: just left of the first figure that follows a word, on most lines."""
    starts: list[float] = []
    body = [w for w in page.words if top <= (w.y0 + w.y1) / 2 <= bottom]
    for line in lines(body, tolerance):
        seen = False
        for w in line:
            if re.search(r"[A-Za-zÆØÅæøå]{3,}", w.text):
                seen = True
            elif seen and re.fullmatch(r"\d[\d .]*", w.text):
                starts.append(w.x0)
                break
    if not starts:
        raise ValueError(f"page {page.number}: no row labels found")
    starts.sort()
    return starts[len(starts) // 2] - 4


def lines_of(
    page: Page,
    *,
    left: float,
    top: float = 0,
    bottom: float = 1e9,
    labels: bool,
    tolerance: float = 3.0,
    gap: float = 6.5,
    headers: re.Pattern[str] | None = None,
) -> list[tuple[str, float, list[Word]]]:
    """The figure lines of one page: each line's label, height, and figures right of `left`."""
    body = [w for w in page.words if top <= (w.y0 + w.y1) / 2 <= bottom]
    grids: list[tuple[str, float, list[Word]]] = []
    for line in lines(body, tolerance):
        touching: list[Word] = []
        for w in line:
            stray = re.fullmatch(r"[Il|!]", w.text) and touching and w.x0 - touching[-1].x1 < 5
            if touching and (w.x0 - touching[-1].x1 < 1.5 or stray) and numeric(touching[-1].text + w.text):
                last = touching[-1]
                touching[-1] = Word(last.text + w.text, last.x0, min(last.y0, w.y0), w.x1, max(last.y1, w.y1))
            else:
                touching.append(w)
        line = touching
        inside = [w for w in line if w.x1 <= left or (w.x0 < left and not numeric(w.text))]
        while (
            len(inside) > 1
            and re.fullmatch(r"\d{1,3}", inside[-1].text)
            and (after := [w for w in line if w not in inside])
            and GROUP.match(after[0].text.split(" ")[0])
            and after[0].x0 - inside[-1].x1 < 8
        ):
            inside.pop()  # the leading digits of a figure that starts left of the column's usual edge
        label = " ".join(w.text for w in inside)
        rest = [w for w in line if w not in inside]
        figures = [w for w in rest if numeric(w.text)]
        words = [w for w in rest if not numeric(w.text)]
        if not figures:
            if headers is not None and label and headers.search(label):
                grids.append((label, (line[0].y0 + line[0].y1) / 2, []))
            continue
        prose = sum(len(re.sub(r"[^A-Za-zÆØÅæøå]", "", w.text)) >= 3 for w in words)
        if prose >= 2 or (prose and len(figures) < 3):
            continue
        if not labels and len(figures) < 2:
            continue
        grids.append((label, (line[0].y0 + line[0].y1) / 2, figures))
    return grids


def grouped(words: list[Word]) -> bool:
    """Whether words read as the digit groups of one figure: one to three digits, then groups of three."""
    texts = [w.text.replace(" ", "") for w in words]
    return bool(texts) and bool(LEAD.match(texts[0])) and all(GROUP.match(t) for t in texts[1:])


def place(
    number: int,
    grids: list[tuple[str, float, list[Word]]],
    edges: list[float],
    reach: float = 14.0,
    snap: float = 7.0,
) -> list[Row]:
    """Put each figure in the column whose right edge it shares. Digit groups are joined left to right until a
    group ends on a column's right edge."""
    count = len(edges)
    found: list[Row] = []

    def nearest(x: float) -> tuple[int, float]:
        best = min(range(count), key=lambda i: abs(x - edges[i]))
        return best, abs(x - edges[best])

    for label, y, figures in grids:
        if not figures:
            found.append(Row(label.strip(), number, y, [None] * count, header=True))
            continue
        cells: list[Cell | None] = [None] * count
        pieces: list[list[Word]] = []
        pending: list[Word] = []
        for word in sorted(figures, key=lambda w: w.x0):
            for token in [Word(t, word.x0, word.y0, word.x1, word.y1) for t in [word.text]]:
                pending.append(token)
                if nearest(token.x1)[1] <= snap:
                    start = next(i for i in range(len(pending)) if i == len(pending) - 1 or grouped(pending[i:]))
                    pieces += [[w] for w in pending[:start]]
                    pieces.append(pending[start:])
                    pending = []
        if pending:
            pieces += [pending] if grouped(pending) else [[w] for w in pending]
        for piece in pieces:
            text = " ".join(w.text for w in piece)
            column, distance = nearest(piece[-1].x1)
            if distance > reach:
                continue
            box = (piece[0].x0, min(w.y0 for w in piece), piece[-1].x1, max(w.y1 for w in piece))
            previous = cells[column]
            if previous is not None:
                text = f"{previous.text} {text}"
                box = (previous.box[0], min(previous.box[1], box[1]), box[2], max(previous.box[3], box[3]))
            cell = Cell(figure(text), text, number, box)
            cell.alternatives = readings(text)
            cells[column] = cell
        found.append(Row(label.strip(), number, y, cells))
    return found
