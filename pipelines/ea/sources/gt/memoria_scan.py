"""Guatemala's 1995 and 1999 general elections by municipality, read by OCR from the scanned memorias.

The Tribunal Supremo Electoral's memorias of the 1995 and 1999 elections are scans with no text layer. Each prints a
results page for every municipality, every department, and the Distrito Central: one row for each organization and
one column for each contest (both presidential rounds, the national list, the district deputies, the Central
American Parliament, and the municipal corporation), with the valid, null, and blank votes and the votes cast.

Each page is rendered, straightened, and cut into cells along its printed grid. Every cell is read with Tesseract on
its own, in several passes, and checked by its glyph count. A column is kept only when its organizations add up to
its printed valid votes and those with the null and blank votes add up to its printed votes cast; the readings
are reconciled against those sums. Each stored number keeps its page and its box on the page.
"""

import io
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches
from itertools import combinations, pairwise, product
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage
from scipy.optimize import linear_sum_assignment

from ea import raw
from ea.sources.gt import memoria as base
from ea.sources.gt.memoria import Grid, Word, fold
from ea.store import DATA

SOURCE = base.SOURCE
VERSIONS = {1995: 3, 1999: 5}
"""Bumped when the reading of a year's pages changes, so their cached readings are redone."""
CACHE = DATA / "ocr" / SOURCE


class Scan(NamedTuple):
    year: int
    file: str
    first: int
    last: int
    dpi: int
    columns: tuple[str, ...]
    """The contests of a municipality's page, left to right."""


SCANS = {
    1995: Scan(
        1995,
        "1995-1996_Memoria_Elecciones",
        446,
        797,
        200,
        ("president", "president2", "national", "parlacen", "district", "municipal"),
    ),
    1999: Scan(
        1999,
        "1999_Memoria_Elecciones",
        2,
        865,
        300,
        ("president", "president2", "national", "district", "parlacen", "municipal"),
    ),
}


def url(year: int) -> str:
    return f"{base.SITE}/images/memoriaselec/me/{SCANS[year].file}.pdf"


def path(year: int) -> Path:
    return raw.cached(SOURCE, url(year)).path


# Images -----------------------------------------------------------------------------------------------------------


def render(pdf: Path, page: int, dpi: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as folder:
        stem = Path(folder) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-f", str(page), "-l", str(page), "-singlefile", "-png", pdf, stem],
            check=True,
        )
        return Image.open(stem.with_suffix(".png")).convert("L")


def runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """The first and last index of each run of true values."""
    padded = np.concatenate([[False], flags, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def skew(image: Image.Image) -> float:
    """The angle in degrees that straightens a page, from the sharpness of its long rules."""
    small = image.resize((image.width // 4, image.height // 4))

    def score(angle: float) -> float:
        dark = np.asarray(small.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)) < 140
        return float((dark.mean(axis=0) ** 2).sum() + (dark.mean(axis=1) ** 2).sum())

    best = max((a / 4 for a in range(-24, 25)), key=score)
    return max((best + a / 100 for a in range(-15, 16, 3)), key=score)


class Page(NamedTuple):
    image: Image.Image
    angle: float
    width: float
    height: float
    """The printed page's size in points."""
    dpi: int

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        """A box on the straightened image, as a box in points on the printed page."""
        cx, cy = self.image.width / 2, self.image.height / 2
        a = np.radians(self.angle)
        xs, ys = [], []
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            dx, dy = x - cx, y - cy
            # PIL rotates counterclockwise for a positive angle; undo it.
            xs.append(cx + dx * np.cos(a) - dy * np.sin(a))
            ys.append(cy + dx * np.sin(a) + dy * np.cos(a))
        scale = 72 / self.dpi
        return (
            round(max(min(xs), 0) * scale, 1),
            round(max(min(ys), 0) * scale, 1),
            round(min(max(xs) * scale, self.width), 1),
            round(min(max(ys) * scale, self.height), 1),
        )


def straightened(pdf: Path, page: int, dpi: int) -> Page:
    image = render(pdf, page, dpi)
    angle = skew(image)
    if abs(angle) >= 0.05:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    width, height = image.width * 72 / dpi, image.height * 72 / dpi
    return Page(image, angle, width, height, dpi)


# OCR ----------------------------------------------------------------------------------------------------------------

os.environ.setdefault("OMP_THREAD_LIMIT", "1")
DIGITS = "0123456789"


def tesseract(image: Image.Image, psm: int, scale: int = 1, whitelist: str | None = None, language: str = "eng") -> str:
    if scale != 1:
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
    padded = Image.new("L", (image.width + 40, image.height + 40), 255)
    padded.paste(image, (20, 20))
    buffer = io.BytesIO()
    padded.save(buffer, "PNG")
    config = ["-l", language, *(["-c", f"tessedit_char_whitelist={whitelist}"] if whitelist else [])]
    return (
        subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", str(psm), *config],
            input=buffer.getvalue(),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def glyphs(dark: np.ndarray, box: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int] | None, int]:
    """The tight box of the digits in a cell, and how many glyphs it holds."""
    x0, y0, x1, y1 = box
    sub = dark[y0:y1, x0:x1].copy()
    if sub.size == 0:
        return None, 0
    sub[:, sub.mean(axis=0) > 0.8] = False
    sub[sub.mean(axis=1) > 0.6, :] = False
    labels, _ = ndimage.label(sub)
    tall = 0.3 * (y1 - y0)
    kept = []
    for i, found in enumerate(ndimage.find_objects(labels)):
        if found is None:
            continue
        h = found[0].stop - found[0].start
        w = found[1].stop - found[1].start
        if tall <= h <= (y1 - y0) and w <= 6 * h and (labels[found] == i + 1).sum() >= 12:
            kept.append(found)
    if not kept:
        return None, 0
    spans: list[list[int]] = []
    for a, b in sorted((s[1].start, s[1].stop) for s in kept):
        if spans and a < spans[-1][1] - 1:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    height = max(s[0].stop - s[0].start for s in kept)
    # Digits printed close together touch; count a wide span as the digits it is wide enough to hold.
    count = sum(max(1, round((b - a) / (0.62 * height))) if b - a > 0.95 * height else 1 for a, b in spans)
    tight = (
        x0 + min(s[1].start for s in kept),
        y0 + min(s[0].start for s in kept),
        x0 + max(s[1].stop for s in kept),
        y0 + max(s[0].stop for s in kept),
    )
    return tight, count


PASSES = ((7, 2), (8, 2), (13, 3))
BATCH = ((6, 2), (6, 3), (4, 2))


def read_batch(
    image: Image.Image,
    tights: list[tuple[int, int, int, int]],
    psm: int,
    scale: int,
    whitelist: str | None = DIGITS,
    language: str = "eng",
) -> list[str]:
    """The text of many cells in one call: each cell's crop on a line of its own, read as one block."""
    out: list[str] = []
    for start in range(0, len(tights), 40):
        chunk = tights[start : start + 40]
        crops = [image.crop((t[0] - 3, t[1] - 3, t[2] + 3, t[3] + 3)) for t in chunk]
        crops = [c.resize((c.width * scale, c.height * scale), Image.Resampling.LANCZOS) for c in crops]
        slot = max(c.height for c in crops) + 30 * scale
        sheet = Image.new("L", (max(c.width for c in crops) + 80, slot * len(crops) + 40), 255)
        for i, c in enumerate(crops):
            sheet.paste(c, (40, 20 + i * slot + (slot - c.height) // 2))
        buffer = io.BytesIO()
        sheet.save(buffer, "PNG")
        tsv = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "--psm",
                str(psm),
                "-l",
                language,
                *(["-c", f"tessedit_char_whitelist={whitelist}"] if whitelist else []),
                "tsv",
            ],
            input=buffer.getvalue(),
            capture_output=True,
            check=True,
        ).stdout.decode()
        found: dict[int, list[tuple[int, str]]] = {}
        for line in tsv.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
                continue
            left, top, height = int(parts[6]), int(parts[7]), int(parts[9])
            index = (top + height // 2 - 20) // slot
            if 0 <= index < len(chunk):
                text = re.sub(r"\D", "", parts[11]) if whitelist == DIGITS else parts[11]
                found.setdefault(index, []).append((left, text))
        joiner = "" if whitelist == DIGITS else " "
        out.extend(joiner.join(t for _, t in sorted(found.get(i, []))) for i in range(len(chunk)))
    return out


def read_cells(image: Image.Image, tights: list[tuple[int, int, int, int]]) -> list[list[str]]:
    passes = [read_batch(image, tights, psm, scale) for psm, scale in BATCH]
    return [list(found) for found in zip(*passes, strict=True)] if tights else []


def read_cell(image: Image.Image, tight: tuple[int, int, int, int], passes: int = len(PASSES)) -> list[str]:
    crop = image.crop((tight[0] - 3, tight[1] - 3, tight[2] + 3, tight[3] + 3))
    return [re.sub(r"\D", "", tesseract(crop, psm, scale, DIGITS)) for psm, scale in PASSES[:passes]]


def _readings(
    image: Image.Image, jobs: dict[tuple[int, int], tuple[int, int, int, int]], counts: dict[tuple[int, int], int]
) -> dict[tuple[int, int], list[str]]:
    """Every cell's readings: the batch passes, and the passes cell by cell where those disagree."""
    keys = list(jobs)
    found = dict(zip(keys, read_cells(image, [jobs[k] for k in keys]), strict=True))
    doubtful = [k for k in keys if len(set(found[k])) > 1 or len(found[k][0]) != counts[k]]
    with ThreadPoolExecutor(4) as pool:
        for k, more in zip(doubtful, pool.map(lambda k: read_cell(image, jobs[k]), doubtful), strict=True):
            found[k] = found[k] + more
    return found


def _labels(image: Image.Image, boxes: list[tuple[int, int, int, int]], rows: list[int]) -> dict[int, str]:
    with ThreadPoolExecutor(4) as pool:
        texts = pool.map(lambda b: tesseract(image.crop(b), 7, 1, None, "spa"), boxes)
        return dict(zip(rows, texts, strict=True))


# Reading a page -----------------------------------------------------------------------------------------------------


@dataclass
class Cell:
    box: tuple[float, float, float, float]
    """In points on the printed page."""
    glyphs: int
    readings: list[str]
    crop: str = ""
    """The cell's digits, binarized and scaled to a fixed height, as packed bits in hex."""


HEIGHT = 24


def pack(dark: np.ndarray, tight: tuple[int, int, int, int]) -> str:
    """A cell's tight crop of dark pixels, scaled to a fixed height, as hex: its width, then its bits."""
    x0, y0, x1, y1 = tight
    sub = Image.fromarray((dark[y0:y1, x0:x1] * 255).astype(np.uint8))
    width = max(1, round(sub.width * HEIGHT / max(sub.height, 1)))
    scaled = np.asarray(sub.resize((width, HEIGHT), Image.Resampling.BILINEAR)) > 100
    return f"{width:04x}" + np.packbits(scaled.ravel()).tobytes().hex()


def unpack(text: str) -> np.ndarray:
    width = int(text[:4], 16)
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(text[4:]), dtype=np.uint8))[: width * HEIGHT]
    return bits.reshape(HEIGHT, width).astype(bool)


@dataclass
class Row:
    label: str
    cells: list[Cell | None]


@dataclass
class Sheet:
    """One results page as read."""

    page: int
    title: str
    rows: list[Row] = field(default_factory=list)
    width: float = 0
    height: float = 0
    problem: str = ""


def _rules_1995(dark: np.ndarray) -> tuple[list[int], int, int, list[int]] | None:
    """The column rules of a 1995 results table, its top and bottom, and the rules between its rows."""
    height, width = dark.shape
    profile = dark[int(height * 0.3) : int(height * 0.8)].mean(axis=0)
    rules = [(a + b) // 2 for a, b in runs(profile > 0.45) if (a + b) // 2 > width * 0.25]
    if len(rules) < 6:
        return None
    # The numeric columns are of one width: keep the longest run of rules about one column apart.
    best: list[int] = []
    for start in range(len(rules)):
        run = [rules[start]]
        for x in rules[start + 1 :]:
            if 115 <= x - run[-1] <= 165:
                run.append(x)
            elif x - run[-1] > 165:
                break
        if len(run) >= len(best):
            best = run
    columns = best[:7]
    if len(columns) < 6:
        return None
    x0, x1 = columns[1] + 10, columns[2] - 10
    lines = runs(dark[:, x0:x1].mean(axis=1) > 0.6)
    # The rows' thin rules come about one row apart; the longest such chain is the table's body.
    thin = [(a + b) // 2 for a, b in lines if b - a <= 3 and a > height * 0.15]
    thick = [a for a, b in lines if b - a > 3]
    chain: list[int] = []
    for start in range(len(thin)):
        run = [thin[start]]
        for y in thin[start + 1 :]:
            gap = y - run[-1]
            if any(run[-1] < t < y for t in thick):
                break
            if 33 <= gap <= 47 or 76 <= gap <= 88:
                run.append(y)
            elif gap > 88:
                break
        if len(run) > len(chain):
            chain = run
    if len(chain) < 8:
        return None
    pitch = float(np.median([y - x for x, y in pairwise(chain) if y - x <= 47]))
    above = [b for a, b in lines if b < chain[0] - 10 and b - a > 3]
    top = above[-1] if above and chain[0] - above[-1] <= 1.6 * pitch else round(chain[0] - pitch)
    after = [a for a, b in lines if a > chain[-1] + 10 and b - a >= 3]
    bottom = after[0] if after and after[0] - chain[-1] <= 2.2 * pitch else round(chain[-1] + pitch)
    below = [(a, b) for a, b in lines if top < a and b - a <= 3]
    found = [(a + b) // 2 for a, b in below if b < bottom]
    pitch = float(np.median(np.diff([top, *found, bottom])))
    edges: list[int] = []
    # A rule the scan lost leaves a row twice as tall: split it evenly.
    for a, b in zip([top, *found], [*found, bottom], strict=True):
        parts = max(round((b - a) / pitch), 1)
        edges.extend(round(a + (b - a) * i / parts) for i in range(1, parts))
        if b != bottom:
            edges.append(b)
    return columns, top, bottom, edges


def read_1995(pdf: Path, number: int) -> Sheet:
    page = straightened(pdf, number, 200)
    image = page.image
    dark = np.asarray(image) < 128
    sheet = Sheet(number, "", width=page.width, height=page.height)
    found = _rules_1995(dark)
    if found is None:
        sheet.problem = "no results table"
        return sheet
    columns, top, bottom, edges = found
    head = image.crop((80, 50, image.width - 80, max(top - 400, 160)))
    sheet.title = title(head)
    bounds = [top, *edges, bottom]
    jobs: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    shapes: dict[tuple[int, int], tuple[tuple[int, int, int, int], int]] = {}
    rows: list[tuple[int, int]] = []
    for y0, y1 in pairwise(bounds):
        if y1 - y0 < 25:
            continue
        r = len(rows)
        rows.append((y0, y1))
        for c, (c0, c1) in enumerate(pairwise(columns)):
            tight, count = glyphs(dark, (c0 + 7, y0 + 4, c1 - 6, y1 - 3))
            if tight is not None:
                jobs[(r, c)] = tight
                shapes[(r, c)] = (tight, count)
    readings = _readings(image, jobs, {k: v[1] for k, v in shapes.items()})
    used = [r for r in range(len(rows)) if any((r, c) in jobs for c in range(len(columns) - 1))]
    left = max(columns[0] - 620, 0)
    boxes = [(left + 3, rows[r][0] + 5, columns[0] - 7, rows[r][1] - 5) for r in used]
    labels = _labels(image, boxes, used)
    for r, (y0, y1) in enumerate(rows):
        cells: list[Cell | None] = []
        for c in range(len(columns) - 1):
            if (r, c) not in jobs:
                cells.append(None)
                continue
            tight, count = shapes[(r, c)]
            cells.append(Cell(page.box(*tight), count, readings[(r, c)], pack(dark, tight)))
        if any(cells):
            sheet.rows.append(Row(labels[r], cells))
    return sheet


def title(image: Image.Image) -> str:
    """The heading of a results page, which the memoria prints over a screen."""
    clean = image.filter(ImageFilter.GaussianBlur(2.2)).point(lambda v: 0 if v < 100 else 255)
    return " ".join(tesseract(clean, 6, 1, None, "spa").split())


def sheet(year: int, number: int) -> Sheet:
    """A results page as read, from the cache when it holds this version's reading."""
    target = CACHE / str(year) / f"{number}.json"
    if target.exists():
        stored = json.loads(target.read_text())
        if stored.get("version") == VERSIONS[year]:
            return _load(stored)
    found = (read_1995 if year == 1995 else read_1999)(path(year), number)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": VERSIONS[year], **_dump(found)}))
    return found


def _dump(s: Sheet) -> dict[str, object]:
    return {
        "page": s.page,
        "title": s.title,
        "width": s.width,
        "height": s.height,
        "problem": s.problem,
        "rows": [
            {
                "label": r.label,
                "cells": [None if c is None else [list(c.box), c.glyphs, c.readings, c.crop] for c in r.cells],
            }
            for r in s.rows
        ],
    }


def _load(d: dict) -> Sheet:
    return Sheet(
        d["page"],
        d["title"],
        [
            Row(r["label"], [None if c is None else Cell(tuple(c[0]), c[1], c[2], c[3]) for c in r["cells"]])
            for r in d["rows"]
        ],
        d["width"],
        d["height"],
        d["problem"],
    )


THRESHOLD = 130


def ink(image: Image.Image, opened: bool = False) -> np.ndarray:
    """The printed strokes of a page set over screens: what is clearly darker than its surroundings."""
    sharp = np.asarray(image.filter(ImageFilter.GaussianBlur(0.8))).astype(np.int16)
    smooth = np.asarray(image.filter(ImageFilter.GaussianBlur(2))).astype(np.int16)
    # Over a screen the background is gray: there only strokes that stay dark once the screen is smoothed are ink.
    white = ndimage.maximum_filter(smooth, size=25) > 230
    contrast = (ndimage.maximum_filter(sharp, size=21) - sharp) > 95
    if not opened:
        return contrast
    # The screen's dots are smaller than a stroke of the bold type set over it, so an opening clears them.
    screened = ndimage.binary_opening(np.asarray(image) < THRESHOLD, np.ones((3, 3)))
    return np.where(white, contrast, screened)


def clean(strokes: np.ndarray, box: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = box
    sub = strokes[y0:y1, x0:x1]
    labels, _ = ndimage.label(sub)
    sizes = np.bincount(labels.ravel())
    keep = sizes >= 15
    keep[0] = False
    return Image.fromarray(np.where(keep[labels], 0, 255).astype(np.uint8))


def _letters(strokes: np.ndarray) -> np.ndarray:
    """Only the strokes the size of letters and digits: no rules, dots, or bars."""
    labels, _ = ndimage.label(strokes)
    keep = np.zeros(labels.max() + 1, dtype=bool)
    for i, found in enumerate(ndimage.find_objects(labels)):
        if found is None:
            continue
        h = found[0].stop - found[0].start
        w = found[1].stop - found[1].start
        if 14 <= h <= 60 and w <= 80:
            keep[i + 1] = True
    return keep[labels]


def _header_1999(gray: np.ndarray) -> tuple[int, int, list[int]] | None:
    """The top and bottom of a 1999 results table's heading, and the gaps between its columns."""
    height, width = gray.shape
    band = (gray[:, int(width * 0.35) : int(width * 0.75)] < 120).mean(axis=1)
    smooth = np.convolve(band, np.ones(15) / 15, mode="same")
    found = [(a, b) for a, b in runs(smooth > 0.4) if 50 <= b - a <= 220 and height * 0.2 < a < height * 0.65]
    if not found:
        return None
    top, bottom = found[0]
    inner = gray[top + 10 : bottom - 10] < 120
    light = runs(inner.mean(axis=0) < 0.15)
    gaps = [(a + b) // 2 for a, b in light if width * 0.25 < (a + b) // 2 and b - a < 40]
    right = next((a for a, b in light if b - a >= 40 and a > width * 0.5), None)
    if right is None:
        return None
    # The numeric columns are each about as wide; walk left from the table's right edge one column at a time.
    columns = [right]
    while True:
        fits = [g for g in gaps if 140 <= columns[0] - g <= 290]
        if not fits:
            break
        columns.insert(0, min(fits, key=lambda g: abs(columns[0] - g - 195)))
    if len(columns) < 6:
        return None
    return top, bottom, columns


def _likely_1999(pdf: Path, number: int) -> bool:
    """Whether a 1999 page looks like a results page: set sideways, with the dark bar of its table's title at left."""
    small = np.asarray(render(pdf, number, 20)).astype(np.int16)
    height, width = small.shape
    if width < height:
        return False
    bar = small[int(height * 0.4) : int(height * 0.95), int(width * 0.07) : int(width * 0.14)] < 110
    return bool(bar.mean(axis=0).max() > 0.2)


def read_1999(pdf: Path, number: int) -> Sheet:
    if not _likely_1999(pdf, number):
        return Sheet(number, "", problem="not a results page")
    page = straightened(pdf, number, 300)
    image = page.image
    gray = np.asarray(image).astype(np.int16)
    sheet = Sheet(number, "", width=page.width, height=page.height)
    found = _header_1999(gray)
    if found is None:
        sheet.problem = "no results table"
        return sheet
    top, bottom, columns = found
    strokes = ink(image)
    sheet.title = title_1999(image, top)
    # The heading of the organizations' column runs from the title bar at the table's left to the first column.
    heading = (gray[top + 10 : bottom - 10, : columns[0]] < 120).mean(axis=0)
    spans = runs(heading > 0.5)
    left = (spans[-1][0] if spans else int(image.width * 0.1)) + 100
    letters = _letters(strokes[bottom + 5 :, left : columns[-1]])
    profile = letters.mean(axis=1)
    lines = [(bottom + 5 + a, bottom + 5 + b) for a, b in runs(profile > 0.004)]
    lines = [(a, b) for a, b in lines if 15 <= b - a <= 70]
    jobs: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    shapes: dict[tuple[int, int], int] = {}
    for r, (y0, y1) in enumerate(lines):
        for c, (c0, c1) in enumerate(pairwise(columns)):
            tight, count = glyphs(strokes, (c0 + 6, y0 - 8, c1 - 6, y1 + 8))
            if tight is not None:
                jobs[(r, c)] = tight
                shapes[(r, c)] = count
    cleaned = Image.fromarray(np.where(strokes, 0, 255).astype(np.uint8))
    readings = _readings(cleaned, jobs, shapes)
    # The same cells with the screens cleared another way, read again.
    other = Image.fromarray(np.where(ink(image, opened=True), 0, 255).astype(np.uint8))
    keys = list(jobs)
    for k, more in zip(keys, read_cells(other, [jobs[k] for k in keys]), strict=True):
        readings[k] = readings[k] + more
    used = sorted({r for r, _ in jobs})
    boxes = [(left + 3, lines[r][0] - 3, columns[0] - 7, lines[r][1] + 3) for r in used]
    labels = _labels(cleaned, boxes, used)
    poor = [r for r in used if len(re.sub(r"[^A-Za-z]", "", labels[r])) < 4]
    if poor:
        again = _labels(image, [(left + 3, lines[r][0] - 3, columns[0] - 7, lines[r][1] + 3) for r in poor], poor)
        labels.update({r: t for r, t in again.items() if len(re.sub(r"[^A-Za-z]", "", t)) >= 4})
    for r in range(len(lines)):
        if r not in labels:
            continue
        cells: list[Cell | None] = []
        for c in range(len(columns) - 1):
            if (r, c) in jobs:
                cells.append(
                    Cell(page.box(*jobs[(r, c)]), shapes[(r, c)], readings[(r, c)], pack(strokes, jobs[(r, c)]))
                )
            else:
                cells.append(None)
        sheet.rows.append(Row(labels[r], cells))
        if account(labels[r]) == "EMITIDOS":
            break
        if [account(row.label) for row in sheet.rows[-4:]][:1] == ["VALIDOS"] and len(sheet.rows) >= 4:
            # The votes cast close the table, three rows under the valid votes, where their label reads poorly.
            break
    return sheet


def title_1999(image: Image.Image, top: int) -> str:
    """The department and municipality a 1999 results page names in its heading."""
    gray = np.asarray(image).astype(np.int16)
    found = []
    head = image.crop((int(image.width * 0.1), 0, int(image.width * 0.85), min(top, int(image.height * 0.25))))
    inverted = Image.fromarray(255 - np.asarray(head))
    for candidate in (head, inverted):
        found.append(" ".join(tesseract(candidate, 6, 1, None, "spa").split()))
    del gray
    return " | ".join(found)


def read_all(year: int) -> Iterator[Sheet]:
    scan = SCANS[year]
    for number in range(scan.first, scan.last + 1):
        yield sheet(year, number)


# Reconciling a column -----------------------------------------------------------------------------------------------

CONFUSED = {
    "0": "86",
    "1": "74",
    "2": "7",
    "3": "859",
    "4": "1",
    "5": "36",
    "6": "580",
    "7": "12",
    "8": "3605",
    "9": "35",
}
"""Digits the scans' type is read as in place of each other."""


def candidates(cell: Cell) -> list[tuple[int, int]]:
    """A cell's possible values with their cost: its readings by agreement, then readings with one digit changed."""
    counts = Counter(r for r in cell.readings if r)
    found: dict[int, int] = {}
    ranked = sorted(counts, key=lambda r: (-counts[r] - (len(r) == cell.glyphs), r))
    for i, text in enumerate(ranked):
        value = int(text)
        found.setdefault(value, 0 if i == 0 and counts[text] > len(cell.readings) / 2 else 1 if i == 0 else 2)
    if cell.glyphs == 1 and (not ranked or counts[ranked[0]] <= len(cell.readings) / 2):
        # A faint mark the scan left in an empty cell.
        found.setdefault(0, 2)
    seeds = ranked[:2] or []
    # A digit misread the same way by every pass is less likely than one the passes disagree on.
    firm = 2 if len(counts) == 1 and len(ranked[0]) == cell.glyphs else 0
    for text in seeds:
        for at, digit in enumerate(text):
            for other in CONFUSED[digit]:
                changed = text[:at] + other + text[at + 1 :]
                if changed[0] != "0" or len(changed) == 1:
                    found.setdefault(int(changed), 3 + firm)
        if len(text) == cell.glyphs - 1:
            for at in range(len(text) + 1):
                for digit in "0123456789":
                    changed = text[:at] + digit + text[at:]
                    if changed[0] != "0":
                        found.setdefault(int(changed), 4 + firm)
    return sorted(found.items(), key=lambda kv: kv[1])


MAX_COST = 6
"""The most doubt a column's reading may carry: the sum of its cells' costs."""
MARGIN = 2
"""How much likelier a column's reading must be than any other reading that also meets its sums."""


class Settled(NamedTuple):
    values: list[int | None]
    """Each party row's value, in order, or None where the cell is blank."""
    valid: int
    null: int
    blank: int
    cast: int
    cost: int


def settle(
    parties: list[Cell | None], valid: Cell | None, null: Cell | None, blank: Cell | None, cast: Cell | None
) -> Settled | None:
    """The one reading of a column whose organizations add up to its valid votes, and those with its null and blank
    votes to its votes cast; None when no reading, or more than one equally likely, does."""
    if valid is None or cast is None:
        return None
    totals = [candidates(c)[:6] if c else [(0, 0)] for c in (valid, null, blank, cast)]
    rows = [candidates(c) if c else [] for c in parties]
    unknown = [i for i, (c, r) in enumerate(zip(parties, rows, strict=True)) if c is not None and not r]
    if len(unknown) > 1:
        return None
    if unknown:
        # A cell no pass could read: the one value that completes the sum, if it has as many digits as glyphs.
        (i,) = unknown
        cell = parties[i]
        assert cell is not None
        known = sum(r[0][0] for r in rows if r)
        for (v, _), (n, _), (b, _), (t, _) in product(*totals):
            missing = v - known
            if v + n + b == t and missing >= 0 and len(str(missing)) == cell.glyphs:
                rows[i] = [(missing, 4)]
                break
        else:
            return None
    base_values = [r[0][0] if r else 0 for r in rows]
    base_cost = sum(r[0][1] for r in rows if r)
    total = sum(base_values)
    # Every way to change one cell to any of its other values, or two or three cells to their other readings, by
    # how much each changes the column's sum.
    changes: dict[int, list[tuple[int, tuple[tuple[int, int], ...]]]] = {0: [(0, ())]}
    cheap: list[tuple[int, int, int]] = []
    for i, r in enumerate(rows):
        for value, c in r[1:]:
            d = value - r[0][0]
            changes.setdefault(d, []).append((c - r[0][1], ((i, d),)))
            if c <= 2:
                cheap.append((i, d, c - r[0][1]))
    for size in (2, 3):
        for chosen in combinations(cheap, size):
            if len({i for i, _, _ in chosen}) == size:
                d = sum(x for _, x, _ in chosen)
                changes.setdefault(d, []).append(
                    (sum(c for _, _, c in chosen) + size - 1, tuple((i, x) for i, x, _ in chosen))
                )
    solutions: list[tuple[int, list[int]]] = []
    for (v, cv), (n, cn), (b, cb), (t, ct) in product(*totals):
        if v + n + b != t:
            continue
        for extra, chosen in changes.get(v - total, []):
            changed = list(base_values)
            for i, d in chosen:
                changed[i] += d
            if all(x >= 0 for x in changed):
                solutions.append((cv + cn + cb + ct + base_cost + extra, changed))
    if not solutions:
        return None
    solutions.sort(key=lambda s: s[0])
    best = solutions[0]
    if best[0] > MAX_COST:
        return None
    rival = next((cost for cost, values in solutions[1:] if values != best[1]), None)
    if rival is not None and rival - best[0] < MARGIN:
        return None
    v, n, b, t = _totals_of(best, totals)
    values = [
        None if c is None or (value == 0 and "0" not in c.readings) else value
        for value, c in zip(best[1], parties, strict=True)
    ]
    return Settled(values, v, n, b, t, best[0])


def _totals_of(best: tuple[int, list[int]], totals: list[list[tuple[int, int]]]) -> tuple[int, int, int, int]:
    """The cheapest totals that agree with a settled column's sum."""
    valid = sum(best[1])
    found = [
        (cv + cn + cb + ct, v, n, b, t)
        for (v, cv), (n, cn), (b, cb), (t, ct) in product(*totals)
        if v == valid and v + n + b == t
    ]
    return min(found)[1:]


# Organizations ------------------------------------------------------------------------------------------------------

ORGANIZATIONS = {
    1995: {
        "PAN": "PARTIDO DE AVANZADA NACIONAL",
        "FRG": "FRENTE REPUBLICANO GUATEMALTECO",
        "PLP": "PARTIDO LIBERADOR PROGRESISTA",
        "PREG": "PARTIDO REFORMADOR GUATEMALTECO",
        "FDNG": "FRENTE DEMOCRATICO NUEVA GUATEMALA",
        "DIA": "DESARROLLO INTEGRAL AUTENTICO",
        "UCN": "UNION DEL CENTRO NACIONAL",
        "DCG": "DEMOCRACIA CRISTIANA GUATEMALTECA",
        "PSD": "PARTIDO SOCIALISTA DEMOCRATICO",
        "UD": "UNION DEMOCRATICA",
        "PDP": "PARTIDO DEL PUEBLO",
        "MLN": "MOVIMIENTO DE LIBERACION NACIONAL",
        "PDG": "PARTIDO DEMOCRATA GUATEMALTECO",
        "FDP": "FUERZA DEMOCRATICA POPULAR",
        "AP5": "ALIANZA POPULAR 5",
        "MD": "MOVIMIENTO DE LOS DESCAMISADOS",
        "CAMHINA": "CAMBIO HISTORICO NACIONAL",
        "PCN": "PARTIDO DE CONCILIACION NACIONAL",
        "MPL": "MOVIMIENTO PATRIOTICO LIBERTAD",
        "CAN": "CENTRAL AUTENTICA NACIONALISTA",
        "PP": "PARTIDO PROGRESISTA",
        "URS": "UNION REFORMISTA SOCIAL",
        "PLG": "PARTIDO LABORISTA GUATEMALTECO",
        "PSC": "PARTIDO SOCIAL CRISTIANO",
    },
    1999: {
        "PAN": "PARTIDO DE AVANZADA NACIONAL",
        "FRG": "FRENTE REPUBLICANO GUATEMALTECO",
        "DIA-URNG": "DIA UNIDAD REVOLUCIONARIA NACIONAL GUATEMALTECA",
        "UCN": "UNION DEL CENTRO NACIONAL",
        "FDNG": "FRENTE DEMOCRATICO NUEVA GUATEMALA",
        "ARDE": "ACCION RECONCILIADORA DEMOCRATICA",
        "LOV-UD": "LA ORGANIZACION VERDE UNION DEMOCRATICA",
        "ARENA": "ALIANZA RECONCILIADORA NACIONAL",
        "MLN": "MOVIMIENTO DE LIBERACION NACIONAL",
        "AD": "ACCION DEMOCRATICA",
        "PLP": "PARTIDO LIBERTADOR PROGRESISTA",
        "DCG": "DEMOCRACIA CRISTIANA GUATEMALTECA",
    },
}
"""Each year's national organizations, in the order the memoria prints their rows."""
COALITIONS = {
    1995: ["UCN-DCG-PSD", "DCG-PSD", "UCN-DCG", "FUN-PID", "PCN-MPL"],
    1999: ["DIA-URNG", "LOV-UD"],
}
"""Coalitions that ran in many places, which a misread acronym is matched against before its parts are."""
PARTS = {"PSD", "PSC", "PID", "FUN", "PCN", "MPL", "URNG", "ANN", "LOV", "DIA", "UD"}
"""Parties that ran only within a coalition."""
ACCOUNT_WORDS = (
    ("BLANCOS", ("BLANC", "BLANO", "UANCO", "LANCO")),
    ("VALIDOS", ("VALID", "VALD", "ALIDO", "VALUD")),
    ("NULOS", ("NULO", "HULO", "MULO", "NULOS", "MUTOS", "NUTOS", "HULOS", "MUROS", "NUROS")),
    ("EMITIDOS", ("TOTAL", "TOTALDEVOTOS")),
)
COMMITTEE = re.compile(r"COMIT|CIVIC|C\.\s*C\.", re.IGNORECASE)


def account(text: str) -> str | None:
    """The accounting row a label names, if it names one."""
    folded = re.sub(r"[^A-Z]", "", fold(text))
    for name, needles in ACCOUNT_WORDS:
        if any(n in folded for n in needles):
            return name
    return None


def organization(year: int, text: str) -> tuple[str, bool]:
    """An organization's label from its printed row, and whether it is a national organization or coalition."""
    known = ORGANIZATIONS[year]
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    folded = re.sub(r"[\[{]", "(", re.sub(r"[\]}]", ")", folded))
    found = re.search(r"\(([^()]*)\)?[^A-Z0-9]*$", folded)
    acronym = re.sub(r"[^A-Z0-9=+\-]", "", found.group(1)) if found else ""
    name = fold(folded[: found.start()] if found else folded)
    name = re.sub(r"^(?:[A-Z0-9]{1,2} )+(?=[A-Z]{3})", "", name).strip()
    if "COALI" in folded and not acronym:
        acronym = "-".join(fold(folded).split("COALICION", 1)[-1].split())
    if "COALI" in folded or len(re.findall(r"[-=+]", acronym)) >= 1:
        parts = [p for p in re.split(r"[-=+]", acronym) if p]
        pool = [*known, *PARTS]
        if parts and all(p in pool for p in parts):
            # One coalition is printed with its parties in either order.
            same = [c for c in COALITIONS[year] if sorted(c.split("-")) == sorted(parts)]
            return (same[0] if same else "-".join(parts)), True
        close = get_close_matches("-".join(parts), COALITIONS[year], 1, 0.5)
        if close:
            return close[0], True
        fixed = []
        for part in parts:
            close = get_close_matches(
                part.replace("1", "I").replace("5", "S") if part not in pool else part, pool, 1, 0.5
            )
            fixed.append(close[0] if close else part)
        if fixed and all(p in pool for p in fixed):
            return "-".join(fixed), True
    best = max(known, key=lambda a: SequenceMatcher(None, name, known[a]).ratio())
    ratio = SequenceMatcher(None, name, known[best]).ratio()
    if ratio >= 0.75 or (acronym in known and SequenceMatcher(None, name, known[acronym]).ratio() >= 0.5):
        return (best if ratio >= 0.75 else acronym), True
    label = acronym if len(acronym) >= 2 else name
    return label, False


# Pages to places ----------------------------------------------------------------------------------------------------


def sheets(year: int) -> list[Sheet]:
    """The year's results pages, in the memoria's order."""
    found = [s for s in read_all(year) if s.rows and not s.problem]
    if year == 1999:
        # Pages with a table that are not a place's results, such as the district summaries, have no such heading.
        found = [s for s in found if re.search(r"MUNICIPIO|DEPARTAMENTO|DISTRITO", fold(s.title).split("|")[-1])]
    return found


def _is_department(year: int, s: Sheet) -> bool:
    """A department's page prints no municipal column."""
    return len(s.rows[0].cells) < len(SCANS[year].columns)


def _name_of(year: int, s: Sheet) -> str:
    """The place name a page's heading prints, as read."""
    text = fold(s.title)
    if "DISTRITO CENTRAL" in text:
        return "GUATEMALA"
    if year == 1999:
        found = re.search(r"(?:MUNICIPIO|DEPARTAMENTO)\s+DE\s+([A-Z .]+)", text.split("|")[-1])
        return found.group(1).strip() if found else text
    found = re.search(r"(?:MUNICIPIO|DEPARTAMENTO)\s+DE\s+(.+)", text)
    if found:
        return found.group(1)
    return re.sub(r"^[^ ]+\s+", "", text)


def _similarity(name: str, title: str) -> float:
    title = re.sub(r"[^A-Z ]", "", title)
    words = title.split()
    best = SequenceMatcher(None, name, title).ratio()
    size = len(name.split())
    for start in range(len(words)):
        best = max(best, SequenceMatcher(None, name, " ".join(words[start : start + size])).ratio())
    return best


def places(year: int) -> list[tuple[Sheet, str, int, str]]:
    """Each results page with its kind ("department", "central", or "municipality"), its department, and its
    municipality's code (empty for a department)."""
    found: list[tuple[Sheet, str, int, str]] = []
    groups: list[tuple[Sheet, list[Sheet]]] = []
    early: list[Sheet] = []
    for s in sheets(year):
        if _is_department(year, s):
            groups.append((s, early))
            early = []
        elif groups:
            groups[-1][1].append(s)
        else:
            # The 1999 memoria prints the Distrito Central before its department's page.
            early.append(s)
    names = base.departments_by_code()
    order = sorted(names)
    if len(groups) != len(order):
        raise ValueError(f"{year}: {len(groups)} departments, not {len(order)}")
    # The memorias do not print the departments in the order of their codes: name each group of pages by how well
    # its headings match each department's name and its municipalities' names.
    towns_of = {
        dep: [fold(town) for town, code in base.municipalities()[fold(names[dep])] if int(code[:2]) == dep]
        for dep in order
    }
    score = np.zeros((len(groups), len(order)))
    for g, (head, towns) in enumerate(groups):
        for d, dep in enumerate(order):
            score[g, d] = 3 * _similarity(fold(names[dep]), fold(head.title)) + sum(
                max(_similarity(t, fold(_name_of(year, s))) for t in towns_of[dep]) for s in towns
            ) / max(len(towns), 1) * 5
    chosen = dict(zip(*linear_sum_assignment(-score), strict=True))
    for g, (head, towns) in enumerate(groups):
        dep = order[chosen[g]]
        if _similarity(fold(names[dep]), fold(head.title)) < 0.5:
            print(f"{year} page {head.page}: department {names[dep]} read as {head.title!r}", flush=True)
        found.append((head, "department", dep, ""))
        pool = [(fold(town), code) for town, code in base.municipalities()[fold(names[dep])] if int(code[:2]) == dep]
        pool.sort(key=lambda tc: tc[1])
        scores = [[_similarity(t, fold(_name_of(year, s))) for t, _ in pool] for s in towns]
        if len(towns) > len(pool):
            raise ValueError(f"{year}: {len(towns)} pages for the {len(pool)} municipalities of {names[dep]}")
        rows, picked = linear_sum_assignment(-np.array(scores)) if towns else ([], [])
        ordered = _align(scores)
        free = sum(scores[i][j] for i, j in zip(rows, picked, strict=True))
        kept = sum(scores[i][j] for i, j in enumerate(ordered))
        if kept >= free - 0.6:
            # The pages follow the municipalities' codes where their headings allow it.
            rows, picked = list(range(len(towns))), ordered
        for i, j in zip(rows, picked, strict=True):
            s = towns[i]
            town, code = pool[j]
            if scores[i][j] < 0.45:
                print(f"{year} page {s.page}: {town} ({code}) read as {s.title!r}", flush=True)
            found.append((s, "central" if code == "0101" else "municipality", dep, code))
    return found


def _align(scores: list[list[float]]) -> list[int]:
    """The order-keeping assignment of pages to municipalities that best matches their headings."""
    n, m = len(scores), len(scores[0]) if scores else 0
    best = np.full((n + 1, m + 1), -1e9)
    best[0, :] = 0
    for i in range(1, n + 1):
        for j in range(i, m + 1):
            best[i, j] = max(best[i, j - 1], best[i - 1, j - 1] + scores[i - 1][j - 1])
    out: list[int] = []
    i, j = n, m
    while i > 0:
        if j > i and best[i, j] == best[i, j - 1]:
            j -= 1
        else:
            out.append(j - 1)
            i -= 1
            j -= 1
    return out[::-1]


# Contests -----------------------------------------------------------------------------------------------------------

LEFT: dict[int, Counter[str]] = {}
MISSING: dict[int, dict[str, list[str]]] = {}
"""Each contest's municipalities whose printed column could not be read to its own sums, by year."""


def _key(column: Column) -> str:
    if column.what == "district":
        return "district:00" if column.code == "0101" else f"district:{column.department:02d}"
    if column.what == "municipal":
        return f"municipal:{column.code}"
    return column.what


def note(year: int, contest: base.Contest) -> str:
    """The methodology note of a contest read from a scanned memoria."""
    text = (
        f"Read by OCR from the scanned pages of the Tribunal Supremo Electoral's {year} election report (memoria "
        "electoral), with each number's page and box. The memoria prints a results page for each municipality, the "
        "finest unit the Tribunal publishes for this election. Each cell was read several times and checked against "
        "the page's own sums: a municipality's column is stored only where its organizations add up to its printed "
        "valid votes, and those with its null and blank votes add up to its printed votes cast. Null votes are stored "
        "as invalid ballots, blank votes as blank ballots, and the votes cast as ballots cast."
    )
    missing = MISSING.get(year, {}).get(contest.key, [])
    if missing:
        names = ", ".join(sorted(base._town(code) for code in missing))
        text += f" Left out because their printed column could not be read to its own sums: {names}."
    return text


class Column(NamedTuple):
    sheet: Sheet
    kind: str
    department: int
    code: str
    what: str
    index: int
    labels: list[tuple[str, bool]]
    parties: list[Cell | None]
    totals: list[Cell | None]
    """The valid, null, and blank votes and the votes cast."""


def columns(year: int) -> list[Column]:
    """Every printed column of every results page."""
    MISSING[year] = {}
    found: list[Column] = []
    for s, kind, dep, code in places(year):
        base.SIZES[(path(year), s.page)] = (round(s.width), round(s.height))
        width = len(s.rows[0].cells)
        totals = {account(r.label): r for r in s.rows[-6:] if account(r.label)}
        if set(totals) != {"VALIDOS", "NULOS", "BLANCOS", "EMITIDOS"} and len(s.rows) >= 4:
            # The four totals rows close every table; read them by place where their labels read poorly.
            totals = dict(zip(("VALIDOS", "NULOS", "BLANCOS", "EMITIDOS"), s.rows[-4:], strict=True))
        parties = [r for r in s.rows if all(r is not t for t in totals.values())]
        order = list(ORGANIZATIONS[year])
        labels = _fill([organization(year, r.label) for r in parties], order, parties, width)
        places_ = [order.index(name) for name, _ in labels if name in order]
        named = [name for name, national in labels if national]
        if places_ != sorted(places_) or len(named) != len(set(named)):
            # Rows read out of the order the memoria prints them in, or one organization twice: a label misread.
            print(f"{year} page {s.page}: organizations read out of order: {named}", flush=True)
            if kind != "department":
                for what in SCANS[year].columns[:width]:
                    column = Column(s, kind, dep, code, what, 0, [], [], [])
                    MISSING.setdefault(year, {}).setdefault(_key(column), []).append(code)
            continue
        for c, what in enumerate(SCANS[year].columns[:width]):
            cells = [r.cells[c] for r in parties]
            sums = [totals[k].cells[c] for k in ("VALIDOS", "NULOS", "BLANCOS", "EMITIDOS")]
            if all(x is None for x in [*cells, *sums]):
                continue
            found.append(Column(s, kind, dep, code, what, c, labels, cells, sums))
    return found


def _fill(labels: list[tuple[str, bool]], order: list[str], rows: list[Row], width: int) -> list[tuple[str, bool]]:
    """Rows whose label could not be read, named by their place: national organizations print in a fixed order, so a
    row between the organizations one apart from each other in that order is the one between them."""
    out = list(labels)
    for i, ((_, national), row) in enumerate(zip(labels, rows, strict=True)):
        if national or all(c is None for c in row.cells[: width - 1 if width == 6 else width]):
            continue
        before = next((out[j][0] for j in range(i - 1, -1, -1) if out[j][0] in order), None)
        after = next((out[j][0] for j in range(i + 1, len(out)) if out[j][0] in order), None)
        if before is None:
            continue
        between = after is not None and order.index(after) - order.index(before) == 2
        last = after is None and order.index(before) + 1 < len(order) and i == len(out) - 1
        if between or last:
            out[i] = (order[order.index(before) + 1], True)
    return out


def _settle(column: Column, reader: Reader | None = None) -> Settled | None:
    valid, null, blank, cast = column.totals
    if valid is None or cast is None:
        return None
    if reader is None:
        return settle(column.parties, valid, null, blank, cast)

    def more(cell: Cell | None) -> Cell | None:
        return None if cell is None else Cell(cell.box, cell.glyphs, [*cell.readings, *reader.read(cell)], cell.crop)

    return settle([more(c) for c in column.parties], *(more(c) for c in column.totals))


def read(year: int) -> dict[str, base.Contest]:
    grids = _Shared()
    found = columns(year)
    settled: dict[int, Settled] = {}
    reader = Reader()
    for i, column in enumerate(found):
        result = _settle(column)
        if result is None:
            continue
        settled[i] = result
        for cell, value in zip(column.parties, result.values, strict=True):
            if cell is not None and value is not None:
                reader.learn(cell, value)
        for cell, value in zip(column.totals, (result.valid, result.null, result.blank, result.cast), strict=True):
            if cell is not None:
                reader.learn(cell, value)
    first = len(settled)
    if reader.ready():
        for i, column in enumerate(found):
            if i not in settled and (result := _settle(column, reader)) is not None:
                settled[i] = result
    for i, column in enumerate(found):
        if i in settled and column.what != "municipal":
            values = settled[i].values
            if any(not national and v for (_, national), v in zip(column.labels, values, strict=True)):
                # Votes on a row whose label could not be read as any organization on that ballot.
                print(f"{year} page {column.sheet.page} {column.what}: votes on an unread label", flush=True)
                del settled[i]
    left: Counter[str] = Counter()
    unsettled: dict[str, list[str]] = {}
    for i, column in enumerate(found):
        s = column.sheet
        if i not in settled:
            left[column.what] += 1
            unsettled.setdefault(column.what, []).append(str(s.page))
            if column.kind != "department":
                MISSING.setdefault(year, {}).setdefault(_key(column), []).append(column.code)
            continue
        result = settled[i]
        district = "district:00" if column.code == "0101" else f"district:{column.department:02d}"
        # The department's page counts the Distrito Central in its national totals: only the district apart.
        kind = "municipality" if column.kind == "central" and column.what != "district" else column.kind
        put = base._router(grids, kind, column.code, district, column.department)
        for (text, national), cell, value in zip(column.labels, column.parties, result.values, strict=True):
            if value is None or cell is None:
                continue
            if not national and column.what != "municipal":
                raise ValueError(f"{year} page {s.page}: {text!r} is not a known organization")
            put(column.what, text, _word(s, cell, value))
        for name, value, cell in zip(
            ("VALIDOS", "NULOS", "BLANCOS", "EMITIDOS"),
            (result.valid, result.null, result.blank, result.cast),
            column.totals,
            strict=True,
        ):
            if cell is not None:
                put(column.what, name, _word(s, cell, value))
    LEFT[year] = left
    for key_, grid in grids.items():
        if key_.startswith("district:") and MISSING.get(year, {}).get(key_) and grid.declared:
            # The department's printed total covers municipalities left out here; compare it in the report only.
            grid.declared = {}
    print(f"{year}: {len(found)} columns, {first} settled by the OCR passes, {len(settled)} with the second reader")
    for what, pages in unsettled.items():
        print(f"{year} {what}: {len(pages)} columns left out, pages {', '.join(pages[:60])}", flush=True)
    return {k: base._contest(k, k.partition(":")[0], g) for k, g in grids.items()}


class _Shared(dict[str, Grid]):
    """A dict of grids that makes each grid it is asked for."""

    def __missing__(self, key: str) -> Grid:
        self[key] = Grid()
        return self[key]


def _word(s: Sheet, cell: Cell, value: int) -> Word:
    x0, y0, x1, y1 = cell.box
    return Word(s.page, x0, y0, x1, y1, str(value), (x0, y0, x1, y1))


# A second reader: the scans' own digits ------------------------------------------------------------------------------


def _segments(crop: np.ndarray, count: int) -> list[np.ndarray]:
    """A cell's crop cut into `count` digits: along blank columns where they part it so, else into equal widths."""
    ink_columns = crop.any(axis=0)
    parts = [(a, b + 1) for a, b in runs(ink_columns)]
    if len(parts) != count:
        width = crop.shape[1]
        parts = [(round(width * i / count), round(width * (i + 1) / count)) for i in range(count)]
    return [crop[:, a:b] for a, b in parts]


def _feature(segment: np.ndarray) -> np.ndarray:
    """A digit as a fixed-size vector, keeping its width as a feature, since a 1 is narrow."""
    rows = segment.any(axis=1)
    if rows.any():
        segment = segment[np.flatnonzero(rows)[0] : np.flatnonzero(rows)[-1] + 1]
    image = Image.fromarray((segment * 255).astype(np.uint8)).resize((10, 16), Image.Resampling.BILINEAR)
    vector = np.asarray(image, dtype=np.float32).ravel() / 255
    return np.concatenate([vector, [segment.shape[1] / HEIGHT * 3]])


class Reader:
    """Nearest neighbors over digits whose values the sums have settled."""

    def __init__(self) -> None:
        self.features: list[np.ndarray] = []
        self.digits: list[int] = []
        self.matrix: np.ndarray | None = None

    def learn(self, cell: Cell, value: int) -> None:
        if not cell.crop:
            return
        text = str(value)
        for segment, digit in zip(_segments(unpack(cell.crop), len(text)), text, strict=True):
            self.features.append(_feature(segment))
            self.digits.append(int(digit))

    def ready(self) -> bool:
        if len(self.features) < 200:
            return False
        # Evenly spaced samples of each digit keep the search quick.
        kept: list[int] = []
        for digit in range(10):
            where = [i for i, d in enumerate(self.digits) if d == digit]
            step = max(1, len(where) // 400)
            kept.extend(where[::step][:400])
        self.matrix = np.stack([self.features[i] for i in kept])
        self.digits = [self.digits[i] for i in kept]
        return True

    def read(self, cell: Cell) -> list[str]:
        """The cell read at its glyph count, and at one digit fewer and more."""
        if self.matrix is None or not cell.crop:
            return []
        crop = unpack(cell.crop)
        found: list[tuple[float, str]] = []
        for count in {max(cell.glyphs - 1, 1), cell.glyphs, cell.glyphs + 1}:
            text = ""
            worst = 0.0
            for segment in _segments(crop, count):
                distances = ((self.matrix - _feature(segment)) ** 2).sum(axis=1)
                nearest = np.argsort(distances)[:3]
                votes = Counter(self.digits[i] for i in nearest)
                text += str(votes.most_common(1)[0][0])
                worst = max(worst, float(distances[nearest[0]]))
            found.append((worst, text))
        found.sort()
        return [text for _, text in found[:2]]


if __name__ == "__main__":
    import sys

    for found in read_all(int(sys.argv[1])):
        print(found.page, found.title[:50], found.problem, len(found.rows), flush=True)
