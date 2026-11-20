"""Reading scanned ruled tables, such as India's Form 20 sheets: the grid of each page, and each cell read twice
with Tesseract.

A sheet's page is a scan of a ruled table. The grid comes from the long dark runs of the table's rules
(`ea.scan.tables`). Each row of the grid is read as one strip in which the cells are pasted apart with white space
between them, so the rules never touch the digits and every word Tesseract finds falls inside one cell: once at the
scan's own size as it is, and once binarized. A third read, of each cell on its own at a larger size, is made
only for the cells where the first two disagree or are empty.

A row's figures are taken only when they satisfy the sheet's own arithmetic, the candidates' votes summing to the valid
votes and the valid, rejected, and NOTA votes summing to the total, using for each cell a value one of the reads gave,
and only when exactly one choice among the reads does. The sheet as a whole is then held against the totals it prints.
"""

import csv
import io
import itertools
import os
import re
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ea import scan

type Box = tuple[int, int, int, int]

GAP = 60
DIGITS = "0123456789"
WORKERS = max(1, (os.cpu_count() or 2))
ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


def _tesseract(image: Image.Image, psm: str, whitelist: str | None) -> list[tuple[int, int, str]]:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    config = ["-c", f"tessedit_char_whitelist={whitelist}"] if whitelist else []
    done = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", psm, "-l", "eng", *config, "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=False,
        env=ENV,
    )
    if done.returncode:  # Tesseract gives up on a few slivers of a page; such a cell reads as empty
        return []
    out = done.stdout.decode()
    return [
        (int(r["left"]), int(r["left"]) + int(r["width"]), r["text"].strip())
        for r in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if r["level"] == "5" and r["text"].strip()
    ]


def _crop(image: Image.Image, box: Box, scale: float, binary: bool = False) -> Image.Image:
    crop = image.crop((box[0] + 7, box[1] + 6, max(box[2] - 7, box[0] + 8), max(box[3] - 5, box[1] + 7)))
    if binary:
        crop = crop.point(lambda v: 0 if v < 150 else 255)
    if scale != 1:
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
    return crop


def strip(
    image: Image.Image,
    boxes: Sequence[Box],
    scale: float,
    binary: bool = False,
    whitelist: str | None = DIGITS,
    psm: str = "7",
) -> list[str]:
    """Each cell's text, read from one strip of the cells set apart by white space."""
    crops = [_crop(image, box, scale, binary) for box in boxes]
    height = max(c.height for c in crops) + 40
    width = sum(c.width for c in crops) + GAP * (len(crops) + 1)
    canvas = Image.new("L", (width, height), 255)
    spans: list[tuple[int, int]] = []
    x = GAP
    for c in crops:
        canvas.paste(c, (x, 20))
        spans.append((x, x + c.width))
        x += c.width + GAP
    cells = [""] * len(boxes)
    for left, right, text in _tesseract(canvas, psm, whitelist):
        centre = (left + right) / 2
        for i, (x0, x1) in enumerate(spans):
            if x0 - GAP / 2 <= centre <= x1 + GAP / 2:
                cells[i] += (" " if cells[i] and whitelist is None else "") + text
                break
    return cells


CELL_READS = ((1.0, "7", True), (1.0, "8", True), (1.5, "8", True), (2.0, "8", True), (1.0, "8", False))


def cell(image: Image.Image, box: Box, whitelist: str | None = DIGITS) -> list[str]:
    """One cell read on its own, binarized and not, at its own size and larger."""
    out = []
    for scale, psm, binary in CELL_READS:
        crop = _crop(image, box, scale, binary)
        canvas = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        canvas.paste(crop, (20, 20))
        out.append("".join(t for _, _, t in _tesseract(canvas, psm, whitelist)))
    return out


def deskew(image: Image.Image) -> Image.Image:
    """The page turned so its rules run level: the angle, within two degrees, at which the rows of dark pixels are
    most uneven, which is where the horizontal rules line up."""
    small = image.convert("L").resize((image.width // 4, image.height // 4))
    dark = Image.eval(small, lambda v: 255 if v < 160 else 0)

    def score(angle: float) -> float:
        rows = np.asarray(dark.rotate(angle, resample=Image.Resampling.NEAREST), dtype=np.float32).sum(axis=1)
        return float(np.square(np.diff(rows)).sum())

    best = max((a / 20 for a in range(-40, 41)), key=score)
    if best == 0:
        return image
    return image.rotate(best, resample=Image.Resampling.BICUBIC, fillcolor=255)


def text(image: Image.Image) -> str:
    """The words of a block of print, such as a sheet's heading."""
    return " ".join(t for _, _, t in _tesseract(image, "6", None))


def number(text: str) -> int | None:
    return int(text) if re.fullmatch(r"\d{1,7}", text) else None


@dataclass
class Grid:
    image: Image.Image
    rows: list[int]  # the y of each horizontal rule
    cols: list[list[int]]  # the x of each vertical rule, in each band between two horizontal rules
    size: tuple[int, int]

    def box(self, r: int, c: int) -> Box:
        return (self.cols[r][c], self.rows[r], self.cols[r][c + 1], self.rows[r + 1])


def _runs(mask: np.ndarray, gap: int = 6) -> list[int]:
    """The centres of the runs of true values, joining runs closer than `gap`."""
    found: list[list[int]] = []
    for i in np.flatnonzero(mask):
        if found and i - found[-1][-1] <= gap:
            found[-1].append(int(i))
        else:
            found.append([int(i)])
    return [(f[0] + f[-1]) // 2 for f in found]


def grid(png: Path) -> Grid | None:
    """The page's main table: its horizontal rules, and its vertical rules found band by band, so that a rule that
    leans or fades in places is still placed in every row."""
    image = Image.open(png).convert("L")
    dark = np.asarray(image) < 160
    found = [t for t in scan.tables(png) if len(t[1]) >= 2]
    if found:
        _, guess = max(found, key=lambda t: len(t[0]) * len(t[1]))
        x0, x1 = max(guess[0] - 20, 0), min(guess[-1] + 20, dark.shape[1])
    else:
        # the table's width from its longest rule, where the rules are too broken for `scan.tables`
        y = int(np.argmax(dark.mean(axis=1)))
        xs = np.flatnonzero(dark[max(y - 2, 0) : y + 3].any(axis=0))
        if len(xs) < dark.shape[1] // 2:
            return None
        x0, x1 = max(int(xs[0]) - 20, 0), min(int(xs[-1]) + 20, dark.shape[1])
    rows = _runs(dark[:, x0:x1].mean(axis=1) > 0.6)
    bands = [(a, b) for a, b in itertools.pairwise(rows) if b - a > 25]
    if len(bands) < 2:
        return None
    rows = sorted({y for band in bands for y in band})
    at: list[list[int]] = []
    for a, b in itertools.pairwise(rows):
        profile = dark[a + 5 : b - 4, x0:x1].mean(axis=0) if b - a > 12 else np.zeros(x1 - x0)
        at.append([x0 + x for x in _runs(profile > 0.85)])
    every = sorted(x for xs in at for x in xs)
    columns: list[list[int]] = []
    for x in every:
        if columns and x - columns[-1][-1] <= 15:
            columns[-1].append(x)
        else:
            columns.append([x])
    common = [int(np.median(c)) for c in columns if len(c) >= 0.5 * len(at)]
    if len(common) < 6:
        return None
    cols = []
    for xs in at:
        cols.append(
            [min(xs, key=lambda x: abs(x - c)) if xs and min(abs(x - c) for x in xs) <= 15 else c for c in common]
        )
    return Grid(image, rows, cols, image.size)


@dataclass
class Reads:
    """Each grid row's cells as two strip reads give them."""

    a: list[list[str]]
    b: list[list[str]]


def reads(g: Grid) -> Reads:
    n = len(g.cols[0]) - 1
    spans = list(range(len(g.rows) - 1))

    def one(r: int, scale: float, binary: bool = False) -> list[str]:
        return strip(g.image, [g.box(r, c) for c in range(n)], scale, binary)

    with ThreadPoolExecutor(WORKERS) as pool:
        a = list(pool.map(lambda r: one(r, 1.0), spans))
        b = list(pool.map(lambda r: one(r, 1.0, True), spans))
    return Reads(a, b)


def options(texts: Sequence[str]) -> list[int]:
    out: list[int] = []
    for t in texts:
        v = number(t)
        if v is not None and v not in out:
            out.append(v)
    return out


def solve(cands: list[list[int]], k: int, rejected: bool = True, limit: int = 4096) -> tuple[list[int] | None, int]:
    """The one choice of a value per cell (candidates, then valid, rejected, NOTA, total) that satisfies the row's
    arithmetic, and how many choices did. Agreement first: a cell all reads agree on has one option."""
    if any(not c for c in cands):
        return None, 0
    space = 1
    for c in cands:
        space *= len(c)
    if space > limit:
        return None, 0
    found: list[list[int]] = []
    for choice in itertools.product(*cands):
        votes, valid, rej, nota, total = list(choice[:k]), choice[k], choice[k + 1], choice[k + 2], choice[k + 3]
        if sum(votes) == valid and valid + (rej if rejected else 0) + nota == total:
            found.append(list(choice))
            if len(found) > 1:
                return None, 2
    return (found[0], 1) if found else (None, 0)
