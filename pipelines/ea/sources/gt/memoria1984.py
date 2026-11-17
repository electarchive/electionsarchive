"""Guatemala's election of the National Constituent Assembly of 1 July 1984, read by OCR from the scanned memoria.

The Tribunal Supremo Electoral's memoria of its first election is a scan with no text layer. For the national list
and for each of the 23 electoral districts it prints a summary table of the municipal counts (resumen de escrutinios
municipales): one row for each municipality and one column for each organization, then the valid, blank, and null
votes and the votes cast, with a row of totals. The national list's table for Suchitepéquez is missing from the
memoria; its department totals are read from the national summary by department.

Each page is rendered, straightened, and cut into cells along its printed grid. Every cell is read with Tesseract
several times; a municipality's row is kept only when its organizations add up to its printed valid votes and those
with the blank and null votes add up to its printed votes cast, and each column is checked against the printed
totals. Each stored number keeps its page and its box on the page.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher, get_close_matches
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

import numpy as np
import polars as pl
from PIL import Image
from scipy import ndimage
from scipy.optimize import linear_sum_assignment

from ea import changeset, raw
from ea.package import Package
from ea.sources.gt import memoria as base
from ea.sources.gt import tse
from ea.sources.gt.memoria_scan import (
    Cell,
    Page,
    Reader,
    _readings,
    glyphs,
    pack,
    render,
    runs,
    settle,
    tesseract,
)
from ea.store import DATA

SOURCE = base.SOURCE
YEAR = 1984
DAY = date(1984, 7, 1)
FILE = "1984_Memoria_Elecciones-AsambleaNacionalConstituyente"
URL = f"{base.SITE}/images/memoriaselec/me/{FILE}.pdf"
VERSION = 2
"""Bumped when the reading of the pages changes, so their cached readings are redone."""
CACHE = DATA / "ocr" / SOURCE / str(YEAR)
DPI = 200

METRO = "00"
"""The Distrito Metropolitano, Guatemala City's municipality, which elects its own district deputies."""
NATIONAL_PAGES = {
    60: METRO, 62: "01", 64: "02", 66: "03", 68: "04", 70: "05", 72: "06", 74: "07", 76: "08", 78: "09", 80: "11",
    82: "12", 84: "13", 86: "14", 88: "15", 90: "16", 92: "17", 94: "18", 96: "19", 98: "20", 100: "21", 102: "22",
}  # fmt: skip
"""The pages of the national list's tables, by the department (or the Distrito Metropolitano) each one covers."""
DISTRICT_PAGES = {
    110: METRO, 118: "01", 126: "02", 132: "03", 138: "04", 144: "05", 152: "06", 158: "07", 164: "08", 170: "09",
    180: "10", 186: "11", 192: "12", 200: "13", 208: "14", 214: "15", 220: "16", 230: "17", 236: "18", 242: "19",
    246: "20", 252: "21", 258: "22",
}  # fmt: skip
"""The pages of the district tables, by district."""
SUMMARY_PAGE = 59
"""The national list's summary by department, the only place the memoria prints Suchitepéquez's national list."""
MISSING_NATIONAL = "10"


def path() -> Path:
    return raw.cached(SOURCE, URL).path


def artifact() -> raw.Artifact:
    return raw.cached(SOURCE, URL)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


# Images -----------------------------------------------------------------------------------------------------------


def strokes(gray: np.ndarray) -> np.ndarray:
    """What is printed on a page: pixels clearly darker than the paper around them, so faint rules count too."""
    a = gray.astype(np.int16)
    paper = ndimage.maximum_filter(ndimage.uniform_filter(a, 15), 31)
    return (paper - a) > 60


def skew(dark: np.ndarray) -> float:
    """The angle in degrees that straightens a page, from the sharpness of its rules."""
    small = Image.fromarray(dark[::2, ::2].astype(np.uint8) * 255)

    def score(angle: float) -> float:
        turned = np.asarray(small.rotate(angle, resample=Image.Resampling.NEAREST, fillcolor=0)) > 0
        return float((turned.mean(axis=0) ** 2).sum() + (turned.mean(axis=1) ** 2).sum())

    best = max((a / 4 for a in range(-12, 13)), key=score)
    return max((best + a / 40 for a in range(-10, 11)), key=score)


def straightened(number: int) -> Page:
    image = render(path(), number, DPI)
    angle = skew(strokes(np.asarray(image)))
    if abs(angle) >= 0.02:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    return Page(image, angle, image.width * 72 / DPI, image.height * 72 / DPI, DPI)


# Reading a table --------------------------------------------------------------------------------------------------


@dataclass
class Row:
    label: str
    cells: list[Cell | None]


@dataclass
class Sheet:
    page: int
    headers: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    totals: list[Cell | None] = field(default_factory=list)
    width: float = 0
    height: float = 0
    problem: str = ""


def grid(dark: np.ndarray) -> tuple[list[int], list[int]] | None:
    """The rules of a page's table: its horizontal rules top to bottom, and its vertical rules from the one that
    closes the place names to the right border."""
    profile = dark.mean(axis=1)
    rows = [(a + b) // 2 for a, b in runs(profile > 0.45)]
    if len(rows) < 4:
        return None
    # A table whose rule under its totals is printed faint or short: the totals still close it.
    below = [(a + b) // 2 for a, b in runs(profile > 0.3) if b - a <= 14 and rows[-1] + 30 < a < rows[-1] + 150]
    if below:
        rows.append(below[0])
    # A rule the scan leaves leaning spreads over a few pixels' width: widen the strokes before tracing it.
    band = ndimage.maximum_filter1d(dark[rows[0] : rows[-1]], 9, axis=1)
    columns = [(a + b) // 2 for a, b in runs(band.mean(axis=0) > 0.7)]
    # The place names' column is several times wider than a number's; the rule that closes it starts the numbers.
    wide = [i for i, (a, b) in enumerate(pairwise(columns)) if b - a > 250]
    if wide:
        columns = columns[wide[-1] + 1 :]
    if len(columns) < 6:
        return None
    merged: list[int] = []
    for y in rows:
        if merged and y - merged[-1] < 12:
            continue
        merged.append(y)
    return merged, columns


def inner(dark: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """A cell's box narrowed to its line of print, so a row the memoria sets taller, like its totals, reads too."""
    x0, y0, x1, y1 = box
    sub = dark[y0:y1, x0:x1].copy()
    if sub.size == 0:
        return box
    sub[:, sub.mean(axis=0) > 0.8] = False
    profile = sub.mean(axis=1)
    spans = runs((profile > 0.02) & (profile < 0.6))
    if not spans:
        return box
    a, b = max(spans, key=lambda s: s[1] - s[0])
    return x0, max(y0, y0 + a - 3), x1, min(y1, y0 + b + 4)


def read_sheet(number: int) -> Sheet:
    page = straightened(number)
    image = page.image
    gray = np.asarray(image)
    dark = strokes(gray)
    sheet = Sheet(number, width=page.width, height=page.height)
    found = grid(dark)
    if found is None:
        sheet.problem = "no table"
        return sheet
    rows, columns = found
    left = 60
    sheet.headers = [
        " ".join(tesseract(image.crop((a + 4, rows[0] + 4, b - 4, rows[1] - 4)), 6, 1, None, "spa").split())
        for a, b in pairwise(columns)
    ]
    jobs: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    counts: dict[tuple[int, int], int] = {}
    bands = [(y0, y1) for y0, y1 in pairwise(rows[1:]) if y1 - y0 >= 20]
    for r, (y0, y1) in enumerate(bands):
        for c, (x0, x1) in enumerate(pairwise(columns)):
            tight, count = glyphs(dark, inner(dark, (x0 + 5, y0 + 3, x1 - 4, y1 - 2)))
            if tight is not None:
                jobs[(r, c)] = tight
                counts[(r, c)] = count
    readings = _readings(image, jobs, counts)
    width = len(columns) - 1
    for r, (y0, y1) in enumerate(bands):
        cells: list[Cell | None] = []
        for c in range(width):
            if (r, c) not in jobs:
                cells.append(None)
                continue
            tight = jobs[(r, c)]
            cells.append(Cell(page.box(*tight), counts[(r, c)], readings[(r, c)], pack(dark, tight)))
        if not any(cells):
            continue
        label = " ".join(tesseract(image.crop((left, y0 + 3, columns[0] - 4, y1 - 2)), 7, 1, None, "spa").split())
        sheet.rows.append(Row(label, cells))
    if sheet.rows:
        # Every table closes with its row of totals.
        sheet.totals = sheet.rows.pop().cells
    return sheet


def _dump(s: Sheet) -> dict[str, object]:
    def cells(found: list[Cell | None]) -> list[object]:
        return [None if c is None else [list(c.box), c.glyphs, c.readings, c.crop] for c in found]

    return {
        "version": VERSION,
        "page": s.page,
        "headers": s.headers,
        "rows": [{"label": r.label, "cells": cells(r.cells)} for r in s.rows],
        "totals": cells(s.totals),
        "width": s.width,
        "height": s.height,
        "problem": s.problem,
    }


def _load(d: dict) -> Sheet:
    def cells(found: list) -> list[Cell | None]:
        return [None if c is None else Cell(tuple(c[0]), c[1], c[2], c[3]) for c in found]

    return Sheet(
        d["page"],
        d["headers"],
        [Row(r["label"], cells(r["cells"])) for r in d["rows"]],
        cells(d["totals"]),
        d["width"],
        d["height"],
        d["problem"],
    )


def sheet(number: int) -> Sheet:
    """A table page as read, from the cache when it holds this version's reading."""
    target = CACHE / f"{number}.json"
    if target.exists():
        stored = json.loads(target.read_text())
        if stored.get("version") == VERSION:
            return _load(stored)
    found = read_sheet(number)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_dump(found)))
    return found


# Organizations ------------------------------------------------------------------------------------------------------

ORGANIZATIONS = {
    "AD": ("AD", "Alianza Democrática", "party"),
    "ANP": ("ANP", "Alianza Nacional Progresista", "party"),
    "FDPCND": (
        "FDP-CND",
        "Coalición Fuerza Demócrata Popular - Coordinadora Nacional Democrática",
        "bloc",
    ),
    "MLNCAN": (
        "MLN-CAN",
        "Coalición Movimiento de Liberación Nacional - Central Auténtica Nacionalista",
        "bloc",
    ),
    "PNRDCG": (
        "PNR-DCG",
        "Coalición Partido Nacional Renovador - Democracia Cristiana Guatemalteca",
        "bloc",
    ),
    "DCG": ("DCG", "Democracia Cristiana Guatemalteca", "party"),
    "FCD": ("FCD", "Frente Cívico Democrático", "party"),
    "FUN": ("FUN", "Frente de Unidad Nacional", "party"),
    "FUR": ("FUR", "Frente Unido de la Revolución", "party"),
    "MEC": ("MEC", "Movimiento Emergente de Concordia", "party"),
    "OCAS": ("OCAS", "Organización Campesina de Acción Social", "party"),
    "PDCN": ("PDCN", "Partido Democrático de Cooperación Nacional", "party"),
    "PUA": ("PUA", "Partido de Unificación Anticomunista", "party"),
    "PID": ("PID", "Partido Institucional Democrático", "party"),
    "PNR": ("PNR", "Partido Nacional Renovador", "party"),
    "PP": ("PP", "Partido Populista", "party"),
    "PR": ("PR", "Partido Revolucionario", "party"),
    "UCN": ("UCN", "Unión del Centro Nacional", "party"),
    "COZAUN": ("COZAUN", "Zacapa Unido", "party"),
}
"""The 19 organizations on the 1984 ballots, by label, with the acronym and name the memoria's actas print."""
LABELS = {
    "CNDFDP": "FDPCND",
    "FDPCND": "FDPCND",
    "DCGPNR": "PNRDCG",
    "PIO": "PID",
    "PLD": "PID",
    "RUA": "PUA",
    "FOP": "FDPCND",
    "FDP": "FDPCND",
    "CND": "FDPCND",
    "MLN": "MLNCAN",
    "CAN": "MLNCAN",
}
"""Header readings that name an organization in part, or as the scan's type is misread."""
ACCOUNT_LABELS = ("valid", "blank", "null", "cast")


def organization(header: str) -> str | None:
    """The label of the organization a column header names, or None for a column with no organization."""
    words = [w for w in re.sub(r"[^A-Z ]", " ", base.fold(header)).split() if len(w) >= 2 or w in ("AD", "PP", "PR")]
    if not words:
        return None
    text = "".join(words)
    if text in ORGANIZATIONS:
        return text
    if text in LABELS:
        return LABELS[text]
    close = get_close_matches(text, [*ORGANIZATIONS, *LABELS], n=1, cutoff=0.8)
    if close:
        return LABELS.get(close[0], close[0])
    for word in words:
        if word in ORGANIZATIONS and len(word) >= 3:
            return word
    close = get_close_matches(text, [*ORGANIZATIONS, *LABELS], n=1, cutoff=0.6)
    if close:
        return LABELS.get(close[0], close[0])
    return None


def party_id(label: str) -> str:
    return base.key("party", label)


# Contests -----------------------------------------------------------------------------------------------------------


class Value(NamedTuple):
    value: int
    page: int
    box: tuple[float, float, float, float]


@dataclass
class Table:
    """One contest's table as settled: its organizations, its rows by unit, and its printed totals."""

    key: str
    page: int
    labels: list[str]
    """The organizations of its columns, then the valid, blank, and null votes and the votes cast."""
    rows: dict[str, list[Value | None]] = field(default_factory=dict)
    totals: list[int | None] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    """The place name each unit's row prints."""
    unread: list[str] = field(default_factory=list)
    """Rows that could not be read to their own sums."""
    off: list[str] = field(default_factory=list)
    """Columns whose rows do not add up to the printed totals."""
    learned: list[tuple[Cell, int]] = field(default_factory=list)
    """Cells the sums settled, with their values, to teach the second reader the type."""


def _settled(cells: list[Cell | None]) -> list[int | None] | None:
    """A row's values, read to its own sums: the organizations add up to the valid votes, and those with the blank and
    null votes to the votes cast."""
    if len(cells) < 5:
        return None
    parties, (valid, blank, null, cast) = cells[:-4], cells[-4:]
    found = settle(parties, valid, null, blank, cast)
    if found is None:
        return None
    return [*found.values, found.valid, found.blank, found.null, found.cast]


def _value(cell: Cell | None, value: int | None, page: int) -> Value | None:
    if cell is None or value is None:
        return None
    return Value(value, page, cell.box)


def _names(code: str) -> list[tuple[str, str]]:
    """The municipalities of a department, folded, with their codes."""
    text = base.departments_by_code()[int(code)]
    return base.municipalities()[base.department(text) or base.fold(text)]


def _score(label: str, town: str) -> float:
    """How well a printed place name, which the memoria often abbreviates, matches a municipality's name."""
    words = label.split()
    towns = town.split()
    hits = 0.0
    for w in words:
        best = 0.0
        for t in towns:
            if w == t:
                best = 1.0
            elif len(w) >= 1 and t.startswith(w) and len(w) < len(t):
                best = max(best, 0.8)
            else:
                best = max(best, SequenceMatcher(None, w, t).ratio() if len(w) >= 3 else 0.0)
        hits += best
    whole = SequenceMatcher(None, label.replace(" ", ""), town.replace(" ", "")).ratio()
    return 0.5 * whole + 0.5 * hits / max(len(words), len(towns))


def _clean(label: str) -> str:
    words = [w for w in base.fold(label).split() if len(w) >= 2 or w.isalpha() and w in ("A", "E", "O", "Y")]
    while words and len(words[0]) <= 2 and words[0] not in ("EL", "LA", "LAS", "LOS", "SAN"):
        words.pop(0)
    while words and len(words[-1]) <= 1:
        words.pop()
    return " ".join(words)


RENAMED = {
    "TECUN UMAN": "AYUTLA",
    "SAN ILDELFONSO IXTAHUACAN": "IXTAHUACAN",
    "SAN ILDELFONSO IXTAH": "IXTAHUACAN",
    "SANILDELFONSO IXTAH": "IXTAHUACAN",
    "TECUNUMAN": "AYUTLA",
}
"""Municipalities the memoria names otherwise than the Tribunal's codes do today."""


def towns(labels: list[str], dep: str) -> list[tuple[str, float]]:
    """The municipality each printed row names, matched one to one within its department."""
    pool = _names(dep)
    cleaned = [RENAMED.get(_clean(label), _clean(label)) for label in labels]
    scores = np.array([[_score(label, town) for town, _ in pool] for label in cleaned])
    rows, cols = linear_sum_assignment(-scores)
    found: list[tuple[str, float]] = [("", 0.0)] * len(labels)
    for r, c in zip(rows, cols, strict=True):
        found[r] = (pool[c][1], float(scores[r, c]))
    return found


NATIONAL_ORDER = (
    "FUN",
    "UCN",
    "MLNCAN",
    "PID",
    "PUA",
    "MEC",
    "FDPCND",
    "DCG",
    "FUR",
    "PDCN",
    "FCD",
    "AD",
    "PP",
    "PR",
    "PNR",
)
"""The national list's ballot, which numbers its organizations the same in every department's table."""
DISTRICT_BALLOTS = {
    110: "AD ANP FDPCND MLNCAN DCG FCD FUN FUR MEC PDCN PUA PID PNR PP PR UCN",
    118: "UCN DCG MLNCAN PR PNR PID FUR PUA FUN MEC FCD PDCN AD PP FDPCND ANP",
    126: "UCN MLNCAN PR DCG PNR FUN PID FUR PUA AD PDCN MEC FCD PP",
    132: "DCG UCN MLNCAN PR PNR PUA PID MEC AD PDCN FUR FCD FUN PP FDPCND",
    138: "PID MLNCAN PR PNRDCG UCN AD FUR MEC PUA FUN FCD FDPCND PP",
    144: "UCN DCG PR MLNCAN PNR PID FUR MEC FDPCND FUN FCD AD PUA PP",
    152: "MLNCAN PR PID UCN DCG FUN MEC AD PNR PUA PDCN FUR PP FDPCND",
    158: "PNRDCG PR UCN MLNCAN PID FUN FUR PUA AD FCD PP",
    164: "DCG PNR MLNCAN UCN PDCN PR PUA PID FUN MEC AD FCD PP FDPCND",
    170: "UCN DCG OCAS PR PID PNR MLNCAN PUA FUR PDCN MEC FUN AD PP FCD",
    180: "DCG UCN MLNCAN PR PID AD FUN MEC FCD PNR PUA FUR PP FDPCND",
    186: "DCG UCN PR MLNCAN AD PUA PNR PID PDCN MEC FUR FUN FCD FDPCND PP",
    192: "MLNCAN PR DCG UCN PNR PID PUA FUN MEC AD PDCN FUR PP FDPCND FCD",
    200: "MLNCAN DCG UCN PR PID PNR MEC FUN PUA FDPCND FCD AD PP",
    208: "MLNCAN DCG FUN PR UCN PNR PID MEC PUA AD FCD FDPCND PP",
    214: "MLNCAN DCG PID UCN PR PNR FUN MEC FDPCND FCD AD PUA PP",
    220: "DCG PR MLNCAN UCN PID PNR AD MEC PUA FCD PP",
    230: "UCN PID PR MLNCAN DCG PNR AD PUA PP FCD",
    236: "PR MLNCAN DCG UCN PUA PID MEC FUR PNR FDPCND FCD FUN PP",
    242: "MLNCAN PID UCN PNRDCG COZAUN PR PUA MEC FUN PP",
    246: "MLNCAN DCG PR UCN PID PNR PUA FCD FUR AD PP",
    252: "MLNCAN PR DCG PNR UCN PID AD PUA FUR MEC PP FUN",
    258: "MLNCAN PR UCN DCG AD PNR PID FUN MEC PUA FCD FUR FDPCND PP",
}
"""Each district's ballot, its organizations in the order its table's columns number them, transcribed from the
column headers, which the OCR reads unreliably; the headers as read are checked against it."""


def _noise(row: Row) -> bool:
    """A blank row of the grid where the scan left a mark."""
    return len(_clean(row.label).replace(" ", "")) < 3 and sum(c is not None for c in row.cells) < 3


def _firm(cell: Cell) -> bool:
    """A cell every pass read alike, with as many digits as it has glyphs."""
    return len(set(cell.readings)) == 1 and len(cell.readings[0]) == cell.glyphs


def _fits(value: int, cell: Cell | None) -> bool:
    """Whether a value could be what a cell prints: as many digits as its glyphs, or a reading with a digit at its
    edge that the glyph count missed, as a thin 1 beside a rule."""
    if cell is None:
        return value == 0
    text = str(value)
    if value < 0:
        return False
    return len(text) == cell.glyphs or any(r and (text.endswith(r) or text.startswith(r)) for r in cell.readings)


def _fill(
    settled: dict[int, list[int | None]], cells: list[list[Cell | None]], totals: list[int | None], missing: list[int]
) -> None:
    """Rows their own sums leave unsettled, filled from what the printed totals leave over.

    With one such row, the totals leave its whole row over: it is kept when every value could be what its cell
    prints and the passes read nearly all of them so. With more, a column whose cells in those rows the passes read
    alike in all but one leaves that one over; a row so filled is kept when it meets its own sums."""
    width = len(totals)
    others = [sum(v[i] or 0 for v in settled.values()) for i in range(width)]
    rest = [(total or 0) - other for total, other in zip(totals, others, strict=True)]
    if len(missing) == 1:
        (r,) = missing
        row = cells[r]
        if all(_fits(v, c) for v, c in zip(rest, row, strict=True)):
            unread = sum(c is not None and str(v) not in c.readings for v, c in zip(rest, row, strict=True))
            if unread <= 3:
                settled[r] = [None if c is None else v for v, c in zip(rest, row, strict=True)]
        return
    filled: dict[int, list[int | None]] = {r: [None] * width for r in missing}
    known: dict[int, list[bool]] = {r: [False] * width for r in missing}
    for r in missing:
        for i, cell in enumerate(cells[r]):
            if cell is None:
                known[r][i] = True
            elif _firm(cell):
                filled[r][i] = int(cell.readings[0])
                known[r][i] = True
    for i in range(width):
        open_ = [r for r in missing if not known[r][i]]
        if len(open_) != 1:
            continue
        (r,) = open_
        value = rest[i] - sum(filled[x][i] or 0 for x in missing if x != r)
        if _fits(value, cells[r][i]):
            filled[r][i] = value
            known[r][i] = True
    for r in missing:
        if all(known[r]):
            n = [v or 0 for v in filled[r]]
            if sum(n[:-4]) == n[-4] and n[-4] + n[-3] + n[-2] == n[-1]:
                settled[r] = filled[r]


def _repair(settled: dict[int, list[int | None]], cells: list[list[Cell | None]], totals: list[int | None]) -> None:
    """Where the rows miss the printed totals, the one row whose doubtful cells take up the difference, keeping its own
    sums and its cells' glyph counts: the passes can misread two cells of a row so that the row still adds up."""
    sums = [sum(v[i] or 0 for v in settled.values()) for i in range(len(totals))]
    residual = [(t or 0) - s for t, s in zip(totals, sums, strict=True)]
    if not any(residual):
        return
    fits: list[tuple[int, list[int | None]]] = []
    for r, values in settled.items():
        changed: list[int | None] = []
        for value, cell, d in zip(values, cells[r], residual, strict=True):
            if not d:
                changed.append(value)
                continue
            new = (value or 0) + d
            if cell is None or _firm(cell) or new < 0 or len(str(new)) != cell.glyphs:
                break
            changed.append(new)
        else:
            n = [v or 0 for v in changed]
            if sum(n[:-4]) == n[-4] and n[-4] + n[-3] + n[-2] == n[-1]:
                fits.append((r, changed))
    if len(fits) == 1:
        r, changed = fits[0]
        settled[r] = changed


def table(number: int, key: str, dep: str, reader: Reader | None = None) -> Table:
    s = sheet(number)
    if s.problem:
        raise ValueError(f"page {number}: {s.problem}")
    rows = [row for row in s.rows if not _noise(row)]
    if s.headers and "MUNICIP" in base.fold(s.headers[0]):
        # A place column the grid took for a number's: its rule was printed too faint to trace.
        s.headers = s.headers[1:]
        rows = [Row(row.label, row.cells[1:]) for row in rows]
        s.totals = s.totals[1:]
    width = len(s.headers)
    accounts = [base.fold(h) for h in s.headers[-4:]]
    if not (re.search("VAL|SUMA", accounts[0]) and re.search("TOTAL|SUFRA|GIOS", accounts[-1])):
        raise ValueError(f"page {number}: the last columns are not the valid, blank, null, and cast votes: {accounts}")
    # A column the ballot leaves without an organization holds only marks the scan left.
    marks = [sum(row.cells[i] is not None for row in rows) for i in range(width - 4)]
    used = [n > 0 for n in marks]
    if number in DISTRICT_BALLOTS:
        ballot = DISTRICT_BALLOTS[number].split()
    elif key.startswith("national:"):
        ballot = list(NATIONAL_ORDER)
    else:
        raise ValueError(f"page {number}: no ballot for {key}")
    if len(ballot) > width - 4:
        raise ValueError(f"page {number}: {width - 4} columns for a ballot of {len(ballot)}: {s.headers}")
    labels = ballot + [""] * (width - 4 - len(ballot))
    read = [organization(h) for h in s.headers[: len(ballot)]]
    agree = sum(a == b for a, b in zip(read, ballot, strict=True))
    if agree < len(ballot) / 2:
        raise ValueError(f"page {number}: the headers as read do not follow the ballot: {s.headers}")
    used = [use and (bool(label) or n > 2) for use, label, n in zip(used, labels, marks, strict=True)]
    for i, (label, use) in enumerate(zip(labels, used, strict=True)):
        if use and not label:
            raise ValueError(f"page {number}: column {i + 1} ({s.headers[i]!r}) names no organization")
    keep = [i for i in range(width - 4) if used[i]] + list(range(width - 4, width))
    t = Table(key, number, [labels[i] for i in keep[:-4]] + list(ACCOUNT_LABELS))
    if len(set(t.labels)) != len(t.labels):
        raise ValueError(f"page {number}: an organization heads two columns: {t.labels} from {s.headers}")
    totals = _settled([s.totals[i] if i < len(s.totals) else None for i in keep]) if s.totals else None
    t.totals = totals or []
    if dep == METRO:
        places = [("0101", 1.0)] * len(rows)
    else:
        places = towns([row.label for row in rows], dep)
        codes = [code for code, _ in places]
        if codes != sorted(codes):
            raise ValueError(f"page {number}: the rows, matched, leave the municipalities' official order: {codes}")
    settled: dict[int, list[int | None]] = {}
    for r, row in enumerate(rows):
        cells = [row.cells[i] for i in keep]
        values = _settled(cells)
        if values is None and reader is not None:
            values = _settled([_reread(c, reader) for c in cells])
        if values is not None:
            settled[r] = values
    missing = [r for r in range(len(rows)) if r not in settled]
    if missing and totals is not None:
        _fill(settled, [[row.cells[i] for i in keep] for row in rows], totals, missing)
    if totals is not None and len(settled) == len(rows):
        _repair(settled, [[row.cells[i] for i in keep] for row in rows], totals)
    for r, row in enumerate(rows):
        code, score = places[r]
        name = _clean(row.label)
        if score < 0.3:
            raise ValueError(f"page {number}: row {row.label!r} matches no municipality well ({code}, {score:.2f})")
        if code in t.rows:
            raise ValueError(f"page {number}: two rows name {code}")
        if r not in settled:
            t.unread.append(name or row.label)
            continue
        cells = [row.cells[i] for i in keep]
        t.rows[code] = [_value(c, v, number) for c, v in zip(cells, settled[r], strict=True)]
        t.learned.extend((c, v) for c, v in zip(cells, settled[r], strict=True) if c is not None and v is not None)
        t.names[code] = name
    if totals is not None and not t.unread:
        for i, label in enumerate(t.labels):
            total = sum(x.value for v in t.rows.values() if (x := v[i]) is not None)
            if total != (totals[i] or 0):
                t.off.append(f"{label} {total} against {totals[i]}")
    return t


@cache
def summary() -> Table:
    """The national list's summary by department, read as a table of departments."""
    s = sheet(SUMMARY_PAGE)
    labels = [organization(h) or "" for h in s.headers[:-4]]
    t = Table("summary", SUMMARY_PAGE, [*labels, *ACCOUNT_LABELS])
    t.totals = _settled(s.totals) or []
    for row in s.rows:
        values = _settled(row.cells)
        name = _clean(row.label)
        if values is None:
            t.unread.append(name)
            continue
        t.rows[name] = [_value(c, v, SUMMARY_PAGE) for c, v in zip(row.cells, values, strict=True)]
    return t


def _reread(cell: Cell | None, reader: Reader) -> Cell | None:
    """A doubtful cell with the second reader's readings added to the passes'."""
    if cell is None or _firm(cell):
        return cell
    more = reader.read(cell)
    # A thin 1 beside a rule is the digit the passes most often lose.
    ones = sorted({"1" + r for r in {*cell.readings, *more} if r and len(r) < cell.glyphs + 1})
    return Cell(cell.box, cell.glyphs, [*cell.readings, *more, *ones], cell.crop)


@cache
def tables() -> dict[str, Table]:
    pages = [(n, f"national:{dep}", dep) for n, dep in NATIONAL_PAGES.items()]
    pages += [(n, f"district:{dep}", dep) for n, dep in DISTRICT_PAGES.items()]
    found = {key: table(n, key, dep) for n, key, dep in pages}
    reader = Reader()
    for t in found.values():
        for cell, value in t.learned:
            if _firm(cell) and len(str(value)) == cell.glyphs:
                reader.learn(cell, value)
    if reader.ready():
        # A second pass over the tables with rows left unread, now that the reader knows the type.
        for n, key, dep in pages:
            if found[key].unread:
                again = table(n, key, dep, reader)
                if not _over(again):
                    found[key] = again
    return found


def _over(t: Table) -> bool:
    """Whether a table's rows add up to more than its printed totals in some column, which rows left unread cannot
    explain: a row its own sums settled with two misreadings that cancel out."""
    if not t.totals:
        return False
    return any(
        sum(x.value for row in t.rows.values() if (x := row[i]) is not None) > (total or 0)
        for i, total in enumerate(t.totals)
    )


def report() -> None:
    for key, t in tables().items():
        print(
            key,
            t.page,
            len(t.rows),
            "unread:",
            t.unread,
            "off:",
            t.off,
            "totals" if t.totals else "NO TOTALS",
            flush=True,
        )
    s = summary()
    print("summary", len(s.rows), s.unread, list(s.rows)[:30])


# Packages -----------------------------------------------------------------------------------------------------------

SEATS = {
    "national": {"DCG": 6, "UCN": 5, "MLNCAN": 5, "PR": 2, "PNR": 2, "PID": 2, "PUA": 1},
    METRO: {"UCN": 3, "DCG": 2, "MLNCAN": 1, "PNR": 1},
    "01": {"UCN": 2, "DCG": 1, "MLNCAN": 1, "PR": 1},
    "02": {"UCN": 2},
    "03": {"DCG": 1, "UCN": 1},
    "04": {"PID": 1, "MLNCAN": 1},
    "05": {"UCN": 1, "DCG": 1, "PR": 1},
    "06": {"MLNCAN": 1, "PR": 1},
    "07": {"PNRDCG": 1, "PR": 1},
    "08": {"DCG": 1, "PNR": 1},
    "09": {"UCN": 2, "DCG": 1, "OCAS": 1},
    "10": {"DCG": 1, "UCN": 1},
    "11": {"DCG": 1, "UCN": 1},
    "12": {"MLNCAN": 1, "PR": 1, "DCG": 1, "UCN": 1, "PNR": 1},
    "13": {"MLNCAN": 2, "DCG": 1, "UCN": 1},
    "14": {"MLNCAN": 1, "DCG": 1, "FUN": 1},
    "15": {"MLNCAN": 1, "DCG": 1},
    "16": {"DCG": 1, "PR": 1, "MLNCAN": 1},
    "17": {"UCN": 1, "PID": 1},
    "18": {"PR": 1, "MLNCAN": 1},
    "19": {"MLNCAN": 1, "PID": 1},
    "20": {"MLNCAN": 2},
    "21": {"MLNCAN": 2},
    "22": {"MLNCAN": 2, "PR": 1},
}
"""The seats the Tribunal adjudicated in each contest, as the memoria's summary of adjudications prints them (pages
270 to 275 of the file) and, for the national list, its analytic table (page 106)."""

VOTES, CAST, VALID, INVALID, BLANK = base.VOTES, base.CAST, base.VALID, base.INVALID, base.BLANK
MEASURES = {"valid": VALID, "blank": BLANK, "null": INVALID, "cast": CAST}
ASSEMBLY = base.key("body", "asamblea-nacional-constituyente", YEAR)
SYSTEM = base.key("electoral_system", "asamblea-nacional-constituyente", YEAR)
SOURCE_ID = base.key("source", YEAR)
REGISTRY = f"{SOURCE}/{YEAR}-registry"
ELECTION = f"{SOURCE}/{DAY}"
SUCHITEPEQUEZ = "SUCHITEPEQUEZ"


def packages() -> list[str]:
    return [REGISTRY, ELECTION]


def _pages() -> list[int]:
    found = {SUMMARY_PAGE}
    for t in tables().values():
        found |= {v.page for row in t.rows.values() for v in row if v}
    return sorted(found)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The organizations on the ballots of Guatemala's 1984 National Constituent Assembly election, the Assembly, and "
        "its electoral system, with the Tribunal Supremo Electoral's memoria as a source",
        depends=["gt.tse/geo"],
    )
    p.external |= {base.GUATEMALA}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Supremo Electoral",
        title="Memoria de la Elección de Asamblea Nacional Constituyente 1984",
        url=URL,
        license=base.LICENSE,
        license_url=f"{base.SITE}/informacion-publica",
        attribution="Tribunal Supremo Electoral (TSE), Guatemala",
    )
    a = artifact()
    artifact_id = p.artifact(a, SOURCE_ID)
    for number in _pages():
        s = sheet(number)
        p.add(
            "source_page",
            id=base.key("page", YEAR, number),
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{a.path.stem}/{number}.png",
            width=round(s.width),
            height=round(s.height),
        )
    p.add("body", id=ASSEMBLY, polity_id=base.GUATEMALA, kind="constituent_assembly")
    p.name(ASSEMBLY, "National Constituent Assembly of Guatemala (1984)", "en", "official")
    p.name(ASSEMBLY, "Asamblea Nacional Constituyente", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=base.GUATEMALA,
        valid_from=DAY,
        valid_to=DAY,
        rules={"formula": "dhondt"},
        rules_version=1,
    )
    p.add(
        "note",
        id=base.key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The 88 deputies were elected from closed lists by D'Hondt: 23 by a national list and 65 in 23 electoral "
        "districts, the 22 departments with Guatemala City's municipality (the Distrito Metropolitano) apart from the "
        "rest of its department (Decreto Ley 3-84).",
        lang="en",
    )
    used = {label for t in tables().values() for label in t.labels if label not in ACCOUNT_LABELS}
    for label in sorted(used):
        acronym, name, kind = ORGANIZATIONS[label]
        p.add("party", id=party_id(label), kind=kind, polity_id=base.GUATEMALA)
        p.name(party_id(label), name, "es", "official")
        p.name(party_id(label), acronym, "es", "abbreviation", False)
    return p


def _district(dep: str) -> tuple[str, str, str]:
    """A district contest's area, Spanish title, and slug."""
    if dep == METRO:
        return tse.area("gt.municipio", "0101"), "el Distrito Metropolitano", "distrito-metropolitano"
    if dep == "01":
        return tse.area("gt.distrito", "01"), "el Distrito de Guatemala", "distrito-guatemala"
    text = base.departments_by_code()[int(dep)]
    return tse.area("gt.departamento", dep), text, changeset.slug(text)


NOTE = (
    "Read by OCR from the scanned pages of the Tribunal Supremo Electoral's memoria of the 1984 National Constituent "
    "Assembly election, with each number's page and box. The memoria prints each contest's summary of the municipal "
    "counts (resumen de escrutinios municipales), one row for each municipality, the finest unit it publishes. Each "
    "cell was read several times and checked against the page's own sums: a municipality's row is stored only where "
    "its organizations add up to its printed valid votes, and those with its blank and null votes add up to its "
    "printed votes cast. Null votes are stored as invalid ballots, blank votes as blank ballots, and the votes cast "
    "(total de sufragios) as ballots cast. Seats are as the memoria's summary of adjudications prints them."
)


def election() -> Package:
    p = Package(
        ELECTION,
        SOURCE,
        "Guatemala's National Constituent Assembly election of 1 July 1984 by municipality, from the Tribunal Supremo "
        "Electoral's memoria",
        ["gt.tse/geo", REGISTRY],
    )
    p.external |= {base.GUATEMALA, base.COUNTRY}
    p.uses(artifact())
    election_id = base.key("election", DAY, "constituent")
    p.add(
        "election",
        id=election_id,
        polity_id=base.GUATEMALA,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(election_id, "1984 Guatemalan Constituent Assembly election", "en", "official")
    p.name(election_id, "Elección de Asamblea Nacional Constituyente de 1984", "es", "native", False)
    root = f"gt/{DAY}-asamblea-constituyente"
    p.add("slug", path=root, entity_id=election_id, is_current=True)
    units: dict[str, str] = {}

    def unit(code: str) -> str:
        if code not in units:
            id = units[code] = base.key("unit", DAY, code)
            if code.startswith("D"):
                scheme, area = "gt.departamento", tse.area("gt.departamento", code[1:])
            else:
                scheme, area = "gt.municipio", tse.area("gt.municipio", code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=base.GUATEMALA,
                scheme_code=scheme,
                code=f"{DAY}:{code}",
                area_relation="equals",
                area_id=area,
                kind="aggregate",
            )
        return units[code]

    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    off: list[str] = []
    unread: list[str] = []
    for contest_key, parts in contests().items():
        national = contest_key == "national"
        dep = contest_key.partition(":")[2]
        contest_id = base.key("contest", DAY, contest_key)
        result_set = base.key("result_set", DAY, contest_key)
        seats = SEATS["national" if national else dep]
        if national:
            area, slug = base.COUNTRY, "lista-nacional"
            text = "Diputados a la Asamblea Nacional Constituyente por Lista Nacional"
        else:
            area, place, slug = _district(dep)
            text = f"Diputados a la Asamblea Nacional Constituyente por {place}"
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation",
            body_id=ASSEMBLY,
            district_area_id=area,
            magnitude=sum(seats.values()),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest_id, text, "es", "official")
        p.add("slug", path=f"{root}/{slug}", entity_id=contest_id, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
        )
        order: list[str] = []
        for t in parts:
            order += [label for label in t.labels if label not in ACCOUNT_LABELS and label not in order]
        options = {label: base.key("option", DAY, contest_key, label) for label in order}
        for position, label in enumerate(order, 1):
            p.add(
                "contest_option",
                id=options[label],
                contest_id=contest_id,
                kind="list",
                party_id=party_id(label),
                ballot_position=position,
                ballot_label=ORGANIZATIONS[label][0],
            )
        for label, count in seats.items():
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=count)
        note = NOTE
        for t in parts:
            for code, row in t.rows.items():
                unit_id = unit(code)
                for label, v in zip(t.labels, row, strict=True):
                    if v is None:
                        continue
                    option_id = options.get(label)
                    measure = VOTES if option_id else MEASURES[label]
                    values.append((result_set, unit_id, option_id, measure, v.value))
                    locators.append((result_set, unit_id, option_id, measure, base.key("page", YEAR, v.page), *v.box))
            if t.unread:
                unread.extend(t.unread)
                note += (
                    f" Left out because their printed row on page {t.page} of the file could not be read to its own "
                    f"sums: {', '.join(sorted(t.unread))}."
                )
        for t in parts:
            if t.off and not t.unread:
                code = t.key.partition(":")[2]
                where = {METRO: "the Distrito Metropolitano", "01": "the department of Guatemala outside the city"}.get(
                    code, base.departments_by_code()[int(code)]
                )
                note += (
                    f" In {where} the memoria's municipal rows add up otherwise than its printed totals "
                    f"({'; '.join(t.off)}); the rows are stored as printed."
                )
        if national:
            note += (
                " The memoria prints no table of the national list's municipal counts for Suchitepéquez; the "
                "department's totals are taken from its national summary by department (page 59 of the file). In "
                "the Distrito Metropolitano the memoria prints the national list as one row for its 793 polling "
                "tables, with no blank votes, as stored."
            )
        p.add(
            "note", id=base.key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en"
        )
        # A contest with rows left unread is short of its printed totals by those rows, so its totals are not
        # declared against it.
        declared = parts[0].totals if len(parts) == 1 and not parts[0].unread else []
        if declared and parts[0].off:
            off.append(f"{text}: {'; '.join(parts[0].off)}")
        for label, value in zip(parts[0].labels, declared, strict=False):
            if value is None:
                continue
            option_id = options.get(label)
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option_id,
                measure=VOTES if option_id else MEASURES[label],
                value=value,
            )
    if (national_declared := summary().totals) and not any(t.unread for t in contests()["national"]):
        result_set = base.key("result_set", DAY, "national")
        for label, value in zip(summary().labels, national_declared, strict=True):
            if value is None:
                continue
            option_id = base.key("option", DAY, "national", label) if label not in ACCOUNT_LABELS else None
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option_id,
                measure=VOTES if option_id else MEASURES[label],
                value=value,
            )
    facts = pl.DataFrame(
        values,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
        orient="row",
    )
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="value",
        ),
    )
    located = pl.DataFrame(
        locators,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "page_id": pl.String,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    p.frame(
        "value_locator",
        located.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            page_id="page_id",
            source_id=pl.lit(SOURCE_ID),
            x0="x0",
            y0="y0",
            x1="x1",
            y1="y1",
        ),
    )
    if off:
        p.accept(
            "accounting.declared",
            "The memoria's printed totals differ from the sum of its own municipal rows in "
            + " | ".join(off)
            + "; both are stored as printed, the totals as declared",
        )
    print(f"{DAY}: {facts.height:,} values, {len(units)} units, unread {unread}, off {off}", flush=True)
    return p


def contests() -> dict[str, list[Table]]:
    """Each contest's tables: the national list's from every department, and each district's own."""
    found: dict[str, list[Table]] = {"national": []}
    for key, t in tables().items():
        if key.startswith("national:"):
            found["national"].append(t)
        else:
            found[key] = [t]
    found["national"].append(suchitepequez())
    return found


def suchitepequez() -> Table:
    """The national list in Suchitepéquez, from the summary by department: the memoria prints no table for it."""
    s = summary()
    name = SUCHITEPEQUEZ if SUCHITEPEQUEZ in s.rows else None
    if name is None:
        raise ValueError(f"page {SUMMARY_PAGE}: no row for Suchitepéquez among {list(s.rows)}")
    t = Table(f"national:{MISSING_NATIONAL}", SUMMARY_PAGE, s.labels)
    t.rows[f"D{MISSING_NATIONAL}"] = s.rows[name]
    return t


def parse() -> None:
    registry().write()
    election().write()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["report"]:
        report()
        sys.exit()
    for n in map(int, sys.argv[1:]):
        s = sheet(n)
        print(n, s.problem, s.headers)
        for row in s.rows:
            print(" ", row.label, [c.readings[0] if c else "" for c in row.cells])
        print("  TOTAL", [c.readings[0] if c else "" for c in s.totals], flush=True)
