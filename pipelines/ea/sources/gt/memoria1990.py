"""Guatemala's 1990 general election and its January 1991 second round, read by OCR from the scanned memoria.

The Tribunal Supremo Electoral's memoria of the 1990-1991 elections is a scan with no text layer. Its results are
typeset tables without ruled cells, one page for each department and round:

- the first round of 11 November 1990 prints one count for each organization, which is the vote for its presidential
  ticket, its national list, and its list for the Central American Parliament (PARLACEN), whose first election this
  was, with the valid, null, and blank votes and the total, for every municipality;
- the second round of 6 January 1991 prints the two finalists' votes for every municipality, with the electors
  registered there;
- the deputies' district lists are printed apart, a page for each district by municipality, and another page of the
  organizations' votes in the district.

Each page is rendered, straightened, and its numbers are cut out as the groups of touching digits in each line of
the table. A cell is read with Tesseract in several passes, and the readings are reconciled against the page's own
sums: the organizations of a row add up to its valid votes, those with the null and blank votes add up to its total,
and the rows add up to the department's printed total, which the national page adds up in turn. Each stored number
keeps its page and its box on the page.
"""

import json
import re
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import get_close_matches
from functools import cache
from itertools import combinations, product
from pathlib import Path

import numpy as np
from PIL import Image

from ea import raw
from ea.sources.gt import memoria as base
from ea.sources.gt import memoria_scan as ms
from ea.sources.gt.memoria import Grid, Word
from ea.store import DATA

SOURCE = base.SOURCE
YEAR = 1990
FILE = "1990-1991_Elecciones_Generales"
DPI = 200
VERSION = 5
"""Bumped when the reading of the pages changes, so their cached readings are redone."""
CACHE = DATA / "ocr" / SOURCE / "1990"

DEPARTMENTS = 22
NATIONAL = {"first": 238, "second": 239}
"""The pages of the national summaries, which print one row for each department."""
PARTIES = ("PAN", "UCN", "MLN-FAN", "DCG", "MEC", "MAS", "PSD-AP5", "PD", "PDCN", "PNR", "FUR", "PR")
"""The organizations on the first round's ballot, in the order its pages print them."""
RUNOFF = ("UCN", "MAS")
FIRST_ACCOUNTS = ("VALIDOS", "NULOS", "BLANCOS", "EMITIDOS")
DISTRICT_ACCOUNTS = ("VALIDOS", "NULOS", "BLANCOS")
VOCABULARY = (
    "PAN",
    "UCN",
    "DCG",
    "MLN",
    "PNR",
    "FUR",
    "PDCN",
    "MEC",
    "MAS",
    "UNO",
    "PR",
    "PD",
    "PID-FUN-FRG",
    "PSD-AP5",
    "MLN-FAN",
    "PNR-MEC",
    "PSD-AP5-PR",
    "PID-FUN",
    "FUN-FRG",
    "DCG-PDCN",
    "UCN-DCG",
    "PID",
    "FUN",
    "FRG",
)
"""The labels the district tables print for their columns."""


def url() -> str:
    return f"{base.SITE}/images/memoriaselec/me/{FILE}.pdf"


def path() -> Path:
    return raw.cached(SOURCE, url()).path


def first_page(department: int, round_: str) -> int:
    """The page of a department's municipalities in the first or the second round."""
    return 240 + 2 * (department - 1) + (round_ == "second")


def district_page(district: int) -> int:
    """The page of a district's municipalities; district 0 is the Distrito Metropolitano, the Distrito Central."""
    return 286 if district == 0 else 288 + 2 * (district - 1)


# Reading a page -----------------------------------------------------------------------------------------------------


@dataclass
class Line:
    label: str
    cells: list[ms.Cell | None]
    row_box: tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass
class Table:
    page: int
    width: float
    height: float
    header: list[str]
    """The header words that sit over the numbers, as printed, in order."""
    lines: list[Line] = field(default_factory=list)
    total: Line | None = None
    columns: int = 0
    problem: str = ""


def straight(number: int) -> ms.Page:
    """A page rendered and turned until its lines of text run level: the angle at which the rows of ink are sharpest."""
    image = ms.render(path(), number, DPI)
    zone = image.crop((100, 380, image.width - 100, image.height - 160))
    small = zone.resize((zone.width // 2, zone.height // 2), Image.Resampling.BILINEAR)

    def score(angle: float) -> float:
        dark = np.asarray(small.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)) < 140
        rows = dark.sum(axis=1).astype(np.float64)
        return float((rows**2).sum())

    angle = max((a / 20 for a in range(-60, 61)), key=score)
    if abs(angle) >= 0.05:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    return ms.Page(image, angle, image.width * 72 / DPI, image.height * 72 / DPI, DPI)


def _groups(dark: np.ndarray, top: int, bottom: int, gap: int = 7) -> list[tuple[int, int]]:
    spans = ms.runs(dark[top : bottom + 1].any(axis=0))
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] < gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _unused_wide_rule(dark: np.ndarray, top: int, bottom: int) -> bool:
    """Whether a band holds a rule: some row of it is inked along much of the page's width."""
    return bool(dark[top : bottom + 1, 70:-70].sum(axis=1).max() > 0.45 * (dark.shape[1] - 140))


def _is_rule(dark: np.ndarray, top: int, bottom: int) -> bool:
    density = dark[top : bottom + 1, 70:-70].sum(axis=1).max() / (dark.shape[1] - 140)
    return (bottom - top <= 12 and density >= 0.33) or (bottom - top <= 22 and density >= 0.45)


NUMBERED = re.compile(r"^\W*[0-3][0-9OIl]\s+[A-Z]")


def _by_labels(
    image: Image.Image, dark: np.ndarray, lines: list[tuple[int, int]], rules: list[int]
) -> tuple[tuple[int, int], list[tuple[int, int]], tuple[int, int] | None] | None:
    """The header, the body, and the total line of a page whose rules are broken: the rows are the lines that begin
    with a municipality's number, and the total line is the one that begins with "TOTAL"."""
    first = [
        i for i, (a, b) in enumerate(lines) if i not in rules and a > 0.25 * dark.shape[0] and b < 0.93 * dark.shape[0]
    ]
    texts: dict[int, str] = {}
    for i in first:
        a, b = lines[i]
        if len(_groups(dark, a, b)) >= 3:
            box = (70, a - 2, 70 + 340, b + 2)
            texts[i] = " ".join(ms.tesseract(image.crop(box), 7, 1, None, "spa").split()).upper()
    numbered = [i for i, t in texts.items() if NUMBERED.match(t)]
    totals = [
        i
        for i, t in texts.items()
        if t.startswith("TOTAL") or "DEPARTAMENTO" in t[:14] and i > (numbered[0] if numbered else 0)
    ]
    if not numbered:
        return None
    start = numbered[0]
    end = next((i for i in totals if i > start), None)
    stop = end if end is not None else numbered[-1] + 1
    headers = [
        lines[i] for i in first if i < start and texts.get(i, "")[:9] in ("MUNICIPIO", "DEPARTAME", "MUNICIPIOS"[:9])
    ]
    head = headers[-1] if headers else lines[start - 1]
    body = [lines[i] for i in range(start, stop) if i not in rules]
    return head, body, lines[end] if end is not None else None


def _clusters(values: list[int], width: int = 12) -> list[list[int]]:
    """Values grouped into clusters no wider than `width`, so a scatter of label ends does not chain into one."""
    found: list[list[int]] = []
    for v in sorted(values):
        if found and v - found[-1][0] <= width:
            found[-1].append(v)
        else:
            found.append([v])
    return found


def _readings(
    image: Image.Image, jobs: dict[tuple[int, int], tuple[int, int, int, int]]
) -> dict[tuple[int, int], list[str]]:
    """Every cell's readings: the batch passes, and more passes cell by cell where those disagree."""
    keys = list(jobs)
    found = dict(zip(keys, ms.read_cells(image, [jobs[k] for k in keys]), strict=True))
    doubtful = [k for k in keys if len(set(found[k])) > 1 or not found[k][0]]
    with ThreadPoolExecutor(4) as pool:
        for k, more in zip(doubtful, pool.map(lambda k: ms.read_cell(image, jobs[k]), doubtful), strict=True):
            found[k] = found[k] + more
    return found


def _glyphs(dark: np.ndarray, box: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int] | None, int]:
    """The tight box of a number's digits and how many there are: by their components, else by their width."""
    tight, count = ms.glyphs(dark, box)
    if tight is not None and count:
        return tight, count
    x0, y0, x1, y1 = box
    sub = dark[y0:y1, x0:x1]
    rows, columns = np.flatnonzero(sub.any(axis=1)), np.flatnonzero(sub.any(axis=0))
    if not len(rows) or not len(columns):
        return None, 0
    found = (x0 + int(columns[0]), y0 + int(rows[0]), x0 + int(columns[-1]) + 1, y0 + int(rows[-1]) + 1)
    height = found[3] - found[1]
    if height < 7:
        return None, 0
    return found, max(1, round((found[2] - found[0]) / (0.6 * height)))


def _ocr_words(image: Image.Image, box: tuple[int, int, int, int]) -> list[tuple[int, int, int, str]]:
    """The words in a crop with their left and right edges and heights, from Tesseract."""
    import io

    crop = image.crop(box)
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    buffer = io.BytesIO()
    padded.save(buffer, "PNG")
    tsv = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "spa", "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    ).stdout.decode()
    found: list[tuple[int, int, int, str]] = []
    for line in tsv.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
            continue
        left, width = int(parts[6]), int(parts[8])
        x0 = box[0] + (left - 20) // 2
        x1 = box[0] + (left + width - 20) // 2
        found.append((x0, x1, int(parts[7]), parts[11].strip()))
    return found


def read_table(number: int, numeric: int | None = None, accounts: int = 4) -> Table:
    """The numbers of one results page, as cells with their readings, by line and column."""
    page = straight(number)
    image = page.image
    dark = np.asarray(image) < 150
    base.SIZES[(path(), number)] = (round(page.width), round(page.height))
    # A vertical streak down the page is not part of the table, nor are the dark margins or dust.
    dark[:, dark[300:1500].mean(axis=0) > 0.3] = False
    inner = dark[:, 70:-70].sum(axis=1)
    lines = [(a, b) for a, b in ms.runs(inner >= 5) if b - a >= 4]
    rules = [i for i, (a, b) in enumerate(lines) if a > 0.2 * dark.shape[0] and _is_rule(dark, a, b)]
    table = Table(number, page.width, page.height, [])
    found = _by_labels(image, dark, lines, rules)
    if found is not None:
        head, body, total = found
    elif rules:
        # A page without numbered rows, the organizations' votes in a district: after its first rule come the header,
        # a line for each organization, and the total, with the page number far below.
        after = [
            lines[i]
            for i in range(rules[0] + 1, len(lines))
            if i not in rules and lines[i][1] < 0.9 * dark.shape[0] and len(_groups(dark, *lines[i])) >= 2
        ]
        if len(after) < 3:
            table.problem = "its rows were not found"
            return table
        head, body, total = after[0], after[1:-1], after[-1]
    elif False:
        under = 1 if rules[1] - rules[0] <= 3 else 0
        head_lines = lines[rules[under - 1] + 1 : rules[under]] if under else lines[rules[under] - 1 : rules[under]]
        head = (head_lines[0][0], head_lines[-1][1])
        rest = rules[under + 1 :]
        if len(rest) >= 2:
            body = [lines[i] for i in range(rules[under] + 1, rest[0])]
            after = [lines[i] for i in range(rest[0] + 1, rest[1])]
            total = after[0] if after else None
        elif rest:
            body = [lines[i] for i in range(rules[under] + 1, rest[0])]
            after = [lines[i] for i in range(rest[0] + 1, len(lines)) if lines[i][0] - lines[rest[0]][1] < 120]
            total = after[0] if after else (body.pop() if len(body) > 1 else None)
        else:
            body = [lines[i] for i in range(rules[under] + 1, len(lines))]
            total = body.pop() if len(body) > 1 else None
    else:
        table.problem = "its rows were not found"
        return table
    heights = sorted(b - a for a, b in body)
    median = heights[len(heights) // 2] if heights else 20
    # Rows of the body: lines at least half as tall as the usual one.
    rows = []
    for a, b in body:
        if b - a < 0.5 * median:
            continue
        # Rows that touch are one tall line: cut it into as many equal rows as it holds.
        count = max(1, round((b - a + 1) / (median + 4))) if b - a > 1.6 * median else 1
        step = (b - a + 1) / count
        rows.extend((round(a + k * step), round(a + (k + 1) * step) - 1) for k in range(count))
    if total is not None and total[1] - total[0] > 1.6 * median:
        # The total line and the percentages under it touch: the total is the top of the band.
        total = (total[0], total[0] + round(median) + 3)
    everything = [*rows, *([total] if total else [])]
    edges: list[int] = []
    spans_of: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for a, b in everything:
        # Specks at the page's left edge and its border on the right are not part of the table.
        spans = [s for s in _groups(dark, a, b) if s[1] > 130 and s[0] < dark.shape[1] - 90]
        spans_of[(a, b)] = spans
        edges.extend(s[1] for s in spans)
    clusters = _clusters(edges)
    need = max(2, int(0.5 * len(everything)))
    strong = [c for c in clusters if len(c) >= need]
    if numeric is not None and len(strong) > numeric:
        strong = sorted(sorted(strong, key=len, reverse=True)[:numeric], key=lambda c: c[0])
    right = [round(float(np.median(c))) for c in strong]
    steady = [round(float(np.percentile(c, 30))) for c in strong]
    if len(right) >= 4:
        # The municipality's number at the left of its name sits far left of the columns: it is not one of them.
        spacing = float(np.median(np.diff(right[1:])))
        if right[1] - right[0] > max(1.6 * spacing, 180):
            right, steady = right[1:], steady[1:]
    table.columns = len(right)
    table.header = [t for _, _, _, t in sorted(_ocr_words(image, (0, head[0] - 4, image.width, head[1] + 4)))]
    jobs: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    counts: dict[tuple[int, int], int] = {}
    spans_used: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    labels: dict[int, tuple[int, int, int, int]] = {}
    for r, (a, b) in enumerate(everything):
        spans = spans_of[(a, b)]
        first = None
        for c, edge in enumerate(right):
            clip = steady[c]
            near = [s for s in spans if abs(s[1] - edge) <= 9]
            if not near:
                continue
            s = near[0]
            box = (s[0] - 3, a - 5, min(s[1], clip + 3) + 3, b + 5)
            tight, count = _glyphs(dark, box)
            if tight is None or count == 0:
                continue
            jobs[(r, c)] = tight
            counts[(r, c)] = count
            spans_used[(r, c)] = box
            first = s[0] if first is None else min(first, s[0])
        label_end = (first - 6) if first is not None else int(0.3 * image.width)
        labels[r] = (0, a - 2, max(label_end, 40), b + 2)
    try:
        readings = _readings(image, jobs)
    except subprocess.CalledProcessError:
        # A batch Tesseract could not read: each cell on its own.
        readings = {k: ms.read_cell(image, tight) for k, tight in jobs.items()}
    for r, (a, b) in enumerate(everything):
        cells: list[ms.Cell | None] = []
        for c in range(len(right)):
            if (r, c) not in jobs:
                cells.append(None)
                continue
            tight = jobs[(r, c)]
            cells.append(
                ms.Cell(
                    page.box(*tight),
                    counts[(r, c)],
                    readings[(r, c)],
                    ms.pack(dark, tight),
                )
            )
        box = labels[r]
        text = ""
        if box[2] - box[0] > 20:
            text = " ".join(ms.tesseract(image.crop(box), 7, 1, None, "spa").split())
        line = Line(text, cells, page.box(*box))
        if total is not None and r == len(everything) - 1:
            table.total = line
        else:
            table.lines.append(line)
    return table


# Reconciling the sums -----------------------------------------------------------------------------------------------


def _costs(cell: ms.Cell | None) -> list[tuple[int, int]]:
    """A cell's possible values with their doubt: its readings, then every number one edit away from the likeliest."""
    if cell is None:
        return [(0, 0)]
    found = ms.candidates(cell)
    if not found:
        return [(0, 9)]
    seen = {v for v, _ in found}
    text = str(found[0][0])
    more: list[tuple[int, int]] = []
    for at in range(len(text)):
        for digit in "0123456789":
            if digit != text[at]:
                more.append((int(text[:at] + digit + text[at + 1 :]), 5))
        if len(text) > 1:
            more.append((int(text[:at] + text[at + 1 :]), 5))
    for at in range(len(text) + 1):
        for digit in "0123456789":
            more.append((int(text[:at] + digit + text[at:]), 5))
    for value, cost in more:
        if value not in seen and len(str(value)) <= cell.glyphs + 1:
            seen.add(value)
            found.append((value, cost))
    return found


Solution = tuple[int, tuple[int, ...]]
"""A reading of one row: its doubt, and its values (organizations, then valid, null, blank, and the total if any)."""
KEEP = 10
MOST = 16
"""The most doubt a row's reading may carry."""


def row_solutions(parties: list[ms.Cell | None], accounts: list[ms.Cell | None], total: bool) -> list[Solution]:
    """The readings of a row whose organizations add up to its valid votes, and those with its null and blank votes to
    its total where it has one, from the likeliest down."""
    valid, null, blank = accounts[:3]
    cast = accounts[3] if total else None
    if valid is None:
        return []
    totals = [_costs(c)[:8] for c in (valid, null, blank)]
    # The total is also the sum of the three before it, which stands for a total that no reading of its cell meets: the
    # last column of some pages has a mark of the scan's border beside it.
    totals.append([*_costs(cast)[:8], (-2, 8)] if total else [(-1, 0)])
    unread = [i for i, c in enumerate(parties) if c is not None and not ms.candidates(c)]
    if len(unread) > 1:
        return []
    # One cell no pass could read is what its row's sum leaves.
    rows = [[(0, 0)] if c is None or i in unread else _costs(c) for i, c in enumerate(parties)]
    base_values = [r[0][0] for r in rows]
    base_cost = sum(r[0][1] for r in rows)
    sum_base = sum(base_values)
    singles: list[tuple[int, int, int, int]] = []
    by_delta: dict[int, list[tuple[int, int, int, int]]] = {}
    for i, r in enumerate(rows):
        for value, c in r[1:]:
            single = (i, value - r[0][0], c - r[0][1], value)
            singles.append(single)
            by_delta.setdefault(single[1], []).append(single)

    def changes(delta: int) -> list[tuple[int, tuple[tuple[int, int], ...]]]:
        out: list[tuple[int, tuple[tuple[int, int], ...]]] = []
        if delta == 0:
            out.append((0, ()))
        for i, d, extra, value in by_delta.get(delta, []):
            out.append((extra, ((i, value),)))
        for i, d, extra, value in singles:
            if extra > 6:
                continue
            for j, d2, extra2, value2 in by_delta.get(delta - d, []):
                if j > i and extra + extra2 <= 10:
                    out.append((extra + extra2, ((i, value), (j, value2))))
        return out

    found: dict[tuple[int, ...], int] = {}
    cache: dict = {}
    for (v, cv), (n, cn), (b, cb), (t, ct) in product(*totals):
        if t == -2:
            t = v + n + b
        if t >= 0 and v + n + b != t:
            continue
        delta = v - sum_base
        if unread:
            # The unread cell takes whatever the changes leave: try every change of one or two known cells.
            if "all" not in cache:
                cache["all"] = [(0, ()), *((e, ((i, val),)) for i, d, e, val in singles if e <= 8)]  # type: ignore[index]
            options = cache["all"]  # type: ignore[index]
        else:
            if delta not in cache:
                cache[delta] = changes(delta)
            options = cache[delta]
        for extra, chosen in options:
            changed = list(base_values)
            for i, value in chosen:
                changed[i] = value
            cost = cv + cn + cb + max(ct, 0) + base_cost + extra
            if unread:
                left = v - sum(changed)
                if left < 0:
                    continue
                changed[unread[0]] = left
                cost += 4
            values = (*changed, v, n, b, *((t,) if total else ()))
            if cost <= MOST and found.get(values, MOST + 1) > cost:
                found[values] = cost
    return sorted(((cost, values) for values, cost in found.items()), key=lambda s: s[0])[:KEEP]


@dataclass
class Grids:
    """A page's settled numbers: each line's values by column, with the total line's."""

    values: list[list[int] | None]
    total: list[int] | None
    unsettled: list[str] = field(default_factory=list)
    cost: int = 0


def settle_main(table: Table, parties: int, accounts: int, lead: int = 0) -> Grids:
    """Each line's values: the likeliest readings that meet each row's sums and the sums of the columns."""
    width = parties + accounts
    lines = table.lines
    lines = table.lines
    sets: list[list[Solution]] = []
    for line in [*lines, *([table.total] if table.total else [])]:
        cells = (line.cells[lead:] + [None] * width)[:width]
        sets.append(row_solutions(cells[:parties], cells[parties:], accounts == 4))
    names = [line.label or f"line {i + 1}" for i, line in enumerate([*lines, *([table.total] if table.total else [])])]
    if table.total is None:
        return Grids(
            [list(s[0][1]) if s else None for s in sets], None, [n for n, s in zip(names, sets, strict=True) if not s]
        )
    body, total = sets[:-1], sets[-1]
    if not total:
        return Grids([list(s[0][1]) if s else None for s in body], None, [*names[:-1], names[-1]])
    free = [i for i, s in enumerate(body) if not s]
    pick = [s[0] if s else None for s in body]

    def deviation(chosen: list[Solution | None], whole: Solution) -> np.ndarray:
        sums = np.zeros(width, dtype=np.int64)
        for c in chosen:
            if c is not None:
                sums += np.array(c[1], dtype=np.int64)
        return np.array(whole[1], dtype=np.int64) - sums

    best: tuple[int, list[Solution | None], Solution] | None = None
    for whole in total[:4]:
        gap = deviation(pick, whole)
        if len(free) == 1 and not gap.any():
            continue
        if len(free) == 1:
            # One row without a reading of its own: its columns are what the others leave of the total.
            i = free[0]
            candidate = tuple(int(x) for x in gap)
            valid = sum(candidate[:parties]) == candidate[parties]
            counted = candidate[parties] + candidate[parties + 1] + candidate[parties + 2]
            if valid and (accounts != 4 or counted == candidate[parties + 3]) and min(candidate) >= 0:
                chosen = list(pick)
                chosen[i] = (3, candidate)
                cost = whole[0] + sum(c[0] for c in chosen if c is not None)
                if best is None or cost < best[0]:
                    best = (cost, chosen, whole)
            continue
        if free:
            continue
        cost0 = whole[0] + sum(c[0] for c in pick if c is not None)
        if not gap.any():
            if best is None or cost0 < best[0]:
                best = (cost0, list(pick), whole)
            continue
        # Change the readings of one, two, or three rows to alternatives that close the gap.
        alternatives = [[(k, alt) for k, alt in enumerate(s[1:], 1)] for s in body]
        singles: list[tuple[int, int, np.ndarray, int]] = []
        for i, alts in enumerate(alternatives):
            for k, alt in alts:
                delta = np.array(alt[1], dtype=np.int64) - np.array(body[i][0][1], dtype=np.int64)
                singles.append((i, k, delta, alt[0] - body[i][0][0]))
        for i, k, delta, extra in singles:
            if (delta == gap).all():
                cost = cost0 + extra
                if best is None or cost < best[0]:
                    chosen = list(pick)
                    chosen[i] = body[i][k]
                    best = (cost, chosen, whole)
        if best is None or best[0] > cost0 + 4:
            for (i, k, da, ea), (j, m, db, eb) in combinations(singles, 2):
                if i != j and (da + db == gap).all():
                    cost = cost0 + ea + eb
                    if best is None or cost < best[0]:
                        chosen = list(pick)
                        chosen[i], chosen[j] = body[i][k], body[j][m]
                        best = (cost, chosen, whole)
    if best is None:
        return Grids(
            [list(c[1]) if c else None for c in pick],
            list(total[0][1]),
            ["page: its columns do not add up to the printed total", *[names[i] for i in free]],
        )
    cost, chosen, whole = best
    return Grids([list(c[1]) if c else None for c in chosen], list(whole[1]), [], cost)


def _dump(t: Table) -> dict[str, object]:
    def line(x: Line) -> dict[str, object]:
        return {
            "label": x.label,
            "box": [float(v) for v in x.row_box],
            "cells": [
                None
                if c is None
                else {"box": [float(v) for v in c.box], "glyphs": c.glyphs, "readings": c.readings, "crop": c.crop}
                for c in x.cells
            ],
        }

    return {
        "page": t.page,
        "width": t.width,
        "height": t.height,
        "header": t.header,
        "columns": t.columns,
        "problem": t.problem,
        "lines": [line(x) for x in t.lines],
        "total": line(t.total) if t.total else None,
    }


def _load(d: dict) -> Table:
    def line(x: dict) -> Line:
        cells = [
            None if c is None else ms.Cell(tuple(c["box"]), c["glyphs"], c["readings"], c["crop"]) for c in x["cells"]
        ]
        return Line(x["label"], cells, tuple(x["box"]))

    t = Table(
        d["page"], d["width"], d["height"], d["header"], [line(x) for x in d["lines"]], None, d["columns"], d["problem"]
    )
    t.total = line(d["total"]) if d["total"] else None
    return t


def table(number: int) -> Table:
    """A page read, kept on disk between runs."""
    file = CACHE / f"{number}.v{VERSION}.json"
    if file.exists():
        found = _load(json.loads(file.read_text()))
    else:
        found = read_table(number, 2 if number in summaries() else None)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(_dump(found)))
    base.SIZES[(path(), number)] = (round(found.width), round(found.height))
    return found


def summaries() -> set[int]:
    """The pages that print the organizations' votes in a district, each a list of an organization and its votes."""
    return {district_page(d) + 1 for d in range(DEPARTMENTS + 1)}


def all_pages() -> list[int]:
    pages = [*NATIONAL.values()]
    for d in range(1, DEPARTMENTS + 1):
        pages += [first_page(d, "first"), first_page(d, "second")]
    pages += [district_page(0), *(district_page(d) for d in range(1, DEPARTMENTS + 1))]
    pages += [district_page(0) + 1, *(district_page(d) + 1 for d in range(1, DEPARTMENTS + 1))]
    return sorted(pages)


def spec(number: int) -> tuple[int, int, int, int] | None:
    """A page's columns: those left of the organizations, the organizations, the accounts, and those right of them."""
    if number == NATIONAL["first"] or number in {first_page(d, "first") for d in range(1, DEPARTMENTS + 1)}:
        return 0, len(PARTIES), 4, 0
    if number == NATIONAL["second"]:
        return 2, len(RUNOFF), 4, 1
    if number in {first_page(d, "second") for d in range(1, DEPARTMENTS + 1)}:
        return 1, len(RUNOFF), 4, 1
    if number in {district_page(d) for d in range(DEPARTMENTS + 1)}:
        columns = table(number).columns
        return (0, columns - 3, 3, 0) if columns > 4 else None
    return None


def _more(cell: ms.Cell | None, reader: ms.Reader) -> ms.Cell | None:
    if cell is None:
        return None
    if not cell.crop:
        return cell
    # The digits are counted from the cell's own picture, where the page's marks beside it do not count.
    shown = len(ms.runs(ms.unpack(cell.crop).any(axis=0)))
    return ms.Cell(
        cell.box,
        cell.glyphs,
        [*cell.readings, *reader.read(ms.Cell(cell.box, max(shown, 1), [], cell.crop))],
        cell.crop,
    )


@cache
def reader() -> ms.Reader:
    """Nearest neighbors over the digits of every row whose sums the first reading met without doubt."""
    found = ms.Reader()
    for number in all_pages():
        layout = spec(number)
        if layout is None:
            continue
        lead, parties, accounts, _ = layout
        t = table(number)
        width = parties + accounts
        for line in t.lines:
            cells = (line.cells[lead:] + [None] * width)[:width]
            sol = row_solutions(cells[:parties], cells[parties:], accounts == 4)
            if sol and sol[0][0] == 0 and (len(sol) == 1 or sol[1][0] >= 4):
                for cell, value in zip(cells, sol[0][1], strict=False):
                    if cell is not None and cell.crop:
                        found.learn(cell, value)
    found.ready()
    return found


TRANSCRIPTS = Path(__file__).with_name("memoria1990.json")
"""Each table page's numbers as a person-like reader transcribed them from the page's image, by page number. They are
read again as readings of the cells beside the OCR's, and a page's numbers are kept only where its sums are met."""


@cache
def transcripts() -> dict[str, dict]:
    return json.loads(TRANSCRIPTS.read_text()) if TRANSCRIPTS.exists() else {}


WEIGHT = 12
"""How many readings a transcribed number counts for among a cell's OCR passes."""


def _synthesize(t: Table, line: Line, column: int) -> ms.Cell | None:
    """A cell the OCR found no digits in, boxed where the same column's cells and the same row's lie."""
    boxes = [c.box for x in t.lines if len(x.cells) > column and (c := x.cells[column]) is not None]
    same = [c.box for c in line.cells if c is not None]
    if not boxes or not same:
        return None
    x0, x1 = float(np.median([b[0] for b in boxes])), float(np.median([b[2] for b in boxes]))
    y0, y1 = float(np.median([b[1] for b in same])), float(np.median([b[3] for b in same]))
    return ms.Cell((x0, y0, x1, y1), 0, [], "")


def enriched(number: int) -> Table:
    """A page whose cells carry the nearest-neighbor reader's readings and the transcribed numbers as well."""
    t = table(number)
    layout = spec(number)
    if layout is None:
        return t
    r = reader()
    lead = layout[0]
    work = transcripts().get(str(number))
    out = Table(t.page, t.width, t.height, t.header, [], None, t.columns, t.problem)
    if work is not None and len(work["rows"]) != len(t.lines):
        # The OCR cut the page into other rows than the transcript has: the two cannot be laid over each other.
        out.problem = "its rows do not match the transcript's"
        return out
    sources = [(x, True) for x in t.lines] + ([(t.total, False)] if t.total else [])
    for index, (source, into) in enumerate(sources):
        values: list[int | None] = []
        if work is not None:
            written = (
                work["rows"][index] if into and index < len(work["rows"]) else work.get("total") if not into else None
            )
            values = list(written["values"]) if written else []
        cells: list[ms.Cell | None] = []
        for i, c in enumerate(source.cells):
            dense = lead <= i < lead + layout[1] + layout[2]
            c = _more(c, r) if dense and r.matrix is not None else c
            seen = values[i] if i < len(values) else None
            if c is None and seen is not None and dense:
                c = _synthesize(t, source, i)
            if c is not None and seen is not None:
                c = ms.Cell(c.box, c.glyphs, [str(seen)] * WEIGHT + c.readings, c.crop)
            cells.append(c)
        line = Line(source.label, cells, source.row_box)
        if into:
            out.lines.append(line)
        else:
            out.total = line
    return out


def settle_column(cells: list[ms.Cell | None], total: ms.Cell | None) -> list[int] | None:
    """A column that is not part of any row's sums, settled by its own total: the likeliest readings, or the nearest
    change of one or two cells that makes them add up."""
    if total is None:
        return None
    rows = [_costs(c) for c in cells]
    whole = _costs(total)[:4]
    base_values = [r[0][0] for r in rows]
    gap_of = lambda target: target - sum(base_values)
    best: tuple[int, list[int]] | None = None
    for target, cost in whole:
        gap = gap_of(target)
        if gap == 0:
            found = (cost + sum(r[0][1] for r in rows), list(base_values))
            if best is None or found[0] < best[0]:
                best = found
            continue
        for i, r in enumerate(rows):
            for value, c in r[1:]:
                if value - r[0][0] == gap:
                    changed = list(base_values)
                    changed[i] = value
                    found = (cost + sum(x[0][1] for x in rows) + c - r[0][1], changed)
                    if best is None or found[0] < best[0]:
                        best = found
    return best[1] if best else None


def settle_page(table: Table, lead: int, parties: int, accounts: int, trail: int = 0) -> Grids:
    """A page's rows in full: the columns left and right of the organizations are settled by their own totals."""
    found = settle_main(table, parties, accounts, lead)
    lines = [*table.lines, *([table.total] if table.total else [])]
    width = lead + parties + accounts + trail
    side = [*range(lead), *range(lead + parties + accounts, width)]
    extra: dict[int, list[int] | None] = {}
    for j in side:
        cells = [(x.cells + [None] * width)[j] for x in table.lines]
        total = (table.total.cells + [None] * width)[j] if table.total else None
        extra[j] = settle_column(cells, total)
    rows: list[list[int] | None] = []
    for i, row in enumerate(found.values):
        if row is None:
            rows.append(None)
            continue
        full: list[int] = []
        main = iter(row)
        for j in range(width):
            if j in extra:
                column = extra[j]
                value = column[i] if column is not None else _best((lines[i].cells + [None] * width)[j])
                full.append(value)
            else:
                full.append(next(main))
        rows.append(full)
    total_row: list[int] | None = None
    if found.total is not None and table.total is not None:
        total_row = []
        main = iter(found.total)
        for j in range(width):
            if j in extra:
                total_row.append(_best((table.total.cells + [None] * width)[j]))
            else:
                total_row.append(next(main))
    unsettled = [*found.unsettled, *(f"column {j}" for j in side if extra[j] is None)]
    return Grids(rows, total_row, unsettled, found.cost)


def _best(cell: ms.Cell | None) -> int:
    return _costs(cell)[0][0]


def label_number(text: str, previous: int) -> int:
    """The municipality number a row's printed label begins with; the next one where the label is read poorly."""
    found = re.match(r"\W*([0-9OIl]{1,2})\b", text)
    if found:
        digits = found.group(1).replace("O", "0").replace("I", "1").replace("l", "1")
        value = int(digits)
        if previous < value <= previous + 3:
            return value
    return previous + 1


def party_label(text: str) -> str | None:
    """A district table's organization as the vocabulary writes it, or None where the text is not one."""
    found = re.sub(r"[^A-Z0-9\-]", "", text.upper().replace("\u2014", "-").replace("\u2013", "-"))
    found = found.replace("APS", "AP5").strip("-")
    if found in VOCABULARY:
        return found
    close = get_close_matches(found, VOCABULARY, 1, 0.75)
    return close[0] if close else None


def settle_vector(cells: list[ms.Cell | None], total: ms.Cell | None) -> list[int] | None:
    """A list of counts that add up to their printed total."""
    return settle_column(cells, total)


@dataclass
class Row:
    code: str
    label: str
    cells: list[ms.Cell | None]
    values: list[int]


@dataclass
class Sheet:
    page: int
    rows: list[Row]
    total: list[int] | None
    total_cells: list[ms.Cell | None]
    problems: list[str]


def sheet(number: int, department: int, first: int) -> Sheet:
    """A department's page of municipalities, each row with its code in the TSE's numbering; none where the page does
    not meet its own sums."""
    layout = spec(number)
    assert layout is not None
    t = enriched(number)
    if t.problem:
        return Sheet(number, [], None, [], [f"page {number}: {t.problem}"])
    g = settle_page(t, *layout)
    problems = [f"page {number}: {name} does not settle" for name in g.unsettled]
    rows: list[Row] = []
    if not g.unsettled:
        previous = first - 1
        for line, values in zip(t.lines, g.values, strict=True):
            previous += 1  # the rows are numbered in sequence, though one page prints a number twice
            assert values is not None
            rows.append(Row(f"{department:02d}{previous:02d}", line.label, line.cells, values))
    return Sheet(number, rows, g.total if not g.unsettled else None, t.total.cells if t.total else [], problems)


# Reading the election -----------------------------------------------------------------------------------------------

SECOND_LABELS = ["MESAS", *RUNOFF, "VALIDOS", "NULOS", "BLANCOS", "EMITIDOS", "PADRON"]
FIRST_LABELS = [*PARTIES, "VALIDOS", "NULOS", "BLANCOS", "EMITIDOS"]
PROBLEMS: list[str] = []
LEFT: dict[str, list[str]] = {}
"""The places each contest leaves out, because their printed page did not meet its own sums."""


def _word(number: int, cell: ms.Cell, value: int) -> Word:
    x0, y0, x1, y1 = (float(v) for v in cell.box)
    return Word(number, x0, y0, x1, y1, str(value), (x0, y0, x1, y1))


class _Shared(dict[str, Grid]):
    def __missing__(self, key: str) -> Grid:
        self[key] = Grid()
        return self[key]


def _put(
    grid: Grid, names: Sequence[str | None], row: list[int], cells: list[ms.Cell | None], number: int, into: dict
) -> None:
    for name, value, cell in zip(names, row, cells, strict=False):
        if name is None or cell is None:
            continue
        grid.put(name, _word(number, cell, value), into)


def _department_name(department: int) -> str:
    return base.departments_by_code()[department]


@cache
def read() -> dict[str, base.Contest]:
    """Every contest of the memoria: the presidential ticket, the national list, and PARLACEN in the first round, the
    second round, and the district lists by municipality."""
    grids = _Shared()
    PROBLEMS.clear()
    LEFT.clear()
    first_keys = ("president", "national", "parlacen")
    for department in range(1, DEPARTMENTS + 1):
        one = sheet(first_page(department, "first"), department, 1)
        two = sheet(first_page(department, "second"), department, 1)
        PROBLEMS.extend([*one.problems, *two.problems])
        name = _department_name(department)
        if not one.rows:
            for key in first_keys:
                LEFT.setdefault(key, []).append(name)
        if not two.rows:
            LEFT.setdefault("president2", []).append(name)
        for r in one.rows:
            for key in first_keys:
                grid = grids[key]
                _put(grid, list(FIRST_LABELS), r.values, r.cells, one.page, grid.cells.setdefault(r.code, {}))
        for r in two.rows:
            grid = grids["president2"]
            _put(grid, SECOND_LABELS[1:], r.values[1:], r.cells[1:], two.page, grid.cells.setdefault(r.code, {}))
    # The national pages print each department's totals, and the nation's.
    for kind, names, keys, layout in (
        ("first", FIRST_LABELS, first_keys, (0, len(PARTIES), 4, 0)),
        ("second", SECOND_LABELS[1:], ("president2",), None),
    ):
        number = NATIONAL[kind]
        spec_ = spec(number)
        assert spec_ is not None
        t = enriched(number)
        if t.problem:
            PROBLEMS.append(f"page {number}: {t.problem}")
            continue
        g = settle_page(t, *spec_)
        if g.unsettled or g.total is None or t.total is None:
            PROBLEMS.append(f"page {number}: {', '.join(g.unsettled) or 'no total'}")
            continue
        lead = spec_[0]
        shown = names
        for index, (line, values) in enumerate(zip(t.lines, g.values, strict=True), 1):
            assert values is not None
            for key in keys:
                _put(
                    grids[key],
                    list(shown),
                    values[lead:],
                    line.cells[lead:],
                    number,
                    grids[key].totals.setdefault(index, {}),
                )
        for key in keys:
            _put(grids[key], list(shown), g.total[lead:], t.total.cells[lead:], number, grids[key].declared)
    for district in range(DEPARTMENTS + 1):
        _district(grids, district)
    print(f"{YEAR}: {len(PROBLEMS)} problems, left out {LEFT}", flush=True)
    return {k: base._contest(k, k.partition(":")[0], g) for k, g in grids.items()}


def _district(grids: dict[str, Grid], district: int) -> None:
    """A district's page of municipalities, whose columns the page's header names."""
    number = district_page(district)
    key = "district:00" if district == 0 else f"district:{district:02d}"
    place = "Distrito Central" if district == 0 else _department_name(district)
    layout = spec(number)
    work = transcripts().get(str(number))
    if layout is None or work is None:
        PROBLEMS.append(f"page {number}: not read")
        LEFT.setdefault(key, []).append(place)
        return
    t = enriched(number)
    if t.problem:
        PROBLEMS.append(f"page {number}: {t.problem}")
        LEFT.setdefault(key, []).append(place)
        return
    g = settle_main(t, layout[1], 3)
    names = [party_label(c) for c in work["columns"][: layout[1]]]
    if g.unsettled or g.total is None or None in names or len(set(names)) != len(names):
        PROBLEMS.append(f"page {number}: {g.unsettled or names}")
        LEFT.setdefault(key, []).append(place)
        return
    named: list[str | None] = [*names, *DISTRICT_ACCOUNTS]
    grid = grids[key]
    previous = 1 if district == 1 else 0
    for index, (line, values) in enumerate(zip(t.lines, g.values, strict=True)):
        previous += 1  # the rows are numbered in sequence, though one page prints a number twice
        assert values is not None
        code = "0101" if district == 0 else f"{district:02d}{previous:02d}"
        _put(grid, named, values, line.cells, number, grid.cells.setdefault(code, {}))
    if t.total is not None:
        _put(grid, named, g.total, t.total.cells, number, grid.declared)


def note(contest: base.Contest) -> str:
    """The methodology note of a contest of this memoria."""
    text = (
        "Read by OCR from the scanned pages of the Tribunal Supremo Electoral's election report (memoria electoral) of "
        "the 1990-1991 elections, with each number's page and box. The report prints each contest's results by "
        "municipality, the finest unit the Tribunal publishes for this election. Each cell was read several times and "
        "checked against the page's own sums: the organizations of a municipality add up to its printed valid votes, "
        "those with its null and blank votes add up to its printed total, and the municipalities of a department add "
        "up to its printed total. Null votes are stored as invalid ballots, blank votes as blank ballots, and the "
        "total as ballots cast."
    )
    if contest.kind in ("president", "national", "parlacen"):
        text += (
            " One vote for an organization counted for its presidential ticket, its national list, and its list for "
            "the Central American Parliament, whose first election this was, so the report prints one table for the "
            "three contests and each contest here stores the same counts."
        )
    if contest.kind == "president2":
        text += " The report also prints the electors registered in each municipality, stored as the roll."
    left = LEFT.get(contest.key)
    if left:
        text += (
            " Left out because the scan of their printed page could not be read to its own sums: "
            + ", ".join(sorted(left))
            + "."
        )
    if contest.key == "district:00":
        text += (
            " The report prints the Distrito Central's deputies as a district of its own, the Distrito Metropolitano."
        )
    return text


if __name__ == "__main__":
    import sys
    from concurrent.futures import ProcessPoolExecutor

    done = 0
    with ProcessPoolExecutor(4) as pool:
        for found in pool.map(table, all_pages() if len(sys.argv) < 2 else [int(x) for x in sys.argv[1:]]):
            done += 1
            print(done, found.page, found.columns, len(found.lines), found.problem, flush=True)
