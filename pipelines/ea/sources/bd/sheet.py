"""Read the candidates' votes from the Election Commission's scanned result sheets ("barta sheets").

Every returning officer filled the same printed form: a table of candidates (serial, name, party, symbol, votes,
remark) followed by a box of totals (valid votes, cancelled votes, votes cast, turnout). The numbers are typed in
Bengali digits. This module finds the table on the deskewed scan, cuts each number into its digits, and classifies
the digits by their nearest neighbours among digits whose value is known: the totals each sheet prints, which the
portal also serves as text. A sheet's reading is kept only when its candidates' votes add up to the valid total
that the portal states for it.
"""

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from scipy import ndimage

VERSION = 2  # of the layout reader, which keys the cache of layouts
WIDTH = 2480  # pixels across the page once rendered
SIZE = 20  # the side of a normalized digit

type Box = tuple[int, int, int, int]  # x0, y0, x1, y1 in pixels of the straightened page


@dataclass
class Glyph:
    box: Box
    features: np.ndarray


@dataclass
class Number:
    box: Box  # the cell that holds the number
    glyphs: list[Glyph]


@dataclass
class Layout:
    width: int
    height: int
    angle: float
    columns: list[int]
    votes: list[Number] = field(default_factory=list)  # one per candidate row, top to bottom
    totals: list[Number] = field(default_factory=list)  # valid, cancelled, cast: the digits that end each row


def render(pdf: Path) -> np.ndarray:
    page = pymupdf.open(pdf)[0]
    zoom = WIDTH / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width].copy()


def otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    weights = np.cumsum(hist)
    means = np.cumsum(hist * np.arange(256))
    between = (means[-1] * weights / total - means) ** 2 / np.maximum(weights * (total - weights), 1)
    return int(np.argmax(between))


def skew(dark: np.ndarray) -> float:
    ys, xs = np.nonzero(dark)
    if len(ys) > 300_000:
        pick = np.random.default_rng(0).choice(len(ys), 300_000, replace=False)
        ys, xs = ys[pick], xs[pick]
    best, score = 0.0, -1.0
    for coarse in (np.arange(-4, 4.001, 0.2), None):
        steps = coarse if coarse is not None else np.arange(best - 0.2, best + 0.2001, 0.02)
        for degrees in steps:
            shifted = (ys - xs * np.tan(np.radians(degrees)) + 400).astype(int).clip(0)
            value = float((np.bincount(shifted).astype(np.float64) ** 2).sum())
            if value > score:
                best, score = float(degrees), value
    return best


def runs(mask: np.ndarray, gap: int = 4) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in np.flatnonzero(mask):
        if out and i - out[-1][1] <= gap:
            out[-1] = (out[-1][0], int(i))
        else:
            out.append((int(i), int(i)))
    return out


@dataclass
class Line:
    y0: int
    y1: int
    x0: int
    x1: int

    @property
    def y(self) -> int:
        return (self.y0 + self.y1) // 2


def hlines(dark: np.ndarray, minimum: int) -> list[Line]:
    """Horizontal rules: rows with a long run of ink, bridging the small gaps a scan leaves in a rule."""
    bridged = ndimage.binary_closing(dark, structure=np.ones((1, 9), bool))
    found: list[Line] = []
    for y0, y1 in runs(bridged.sum(axis=1) >= minimum, gap=3):
        band = bridged[y0 : y1 + 1].any(axis=0)
        longest = max(runs(band, gap=6), key=lambda r: r[1] - r[0])
        if longest[1] - longest[0] >= minimum:
            found.append(Line(y0, y1, longest[0], longest[1]))
    return found


def glyphs(dark: np.ndarray, box: Box) -> list[Glyph]:
    """The marks in a cell, left to right, with marks that share a column joined into one glyph."""
    x0, y0, x1, y1 = box
    cell = dark[y0:y1, x0:x1]
    labels, _count = ndimage.label(cell, structure=np.ones((3, 3), bool))
    parts: list[list[int]] = []
    for i, found in enumerate(ndimage.find_objects(labels), start=1):
        if found is None:
            continue
        ys, xs = found
        h, w = ys.stop - ys.start, xs.stop - xs.start
        pixels = int((labels[found] == i).sum())
        touches = ys.start == 0 or xs.start == 0 or ys.stop == cell.shape[0] or xs.stop == cell.shape[1]
        if pixels < 12 or (touches and (h > 0.6 * cell.shape[0] or w > 0.5 * cell.shape[1])):
            continue
        if (h <= 7 and w > 4 * h) or (w <= 7 and h > 5 * w and touches):
            continue  # a piece of a rule
        parts.append([xs.start, ys.start, xs.stop, ys.stop, i])
    if not parts:
        return []
    height = float(np.median([p[3] - p[1] for p in parts]))
    parts = [p for p in parts if p[3] - p[1] >= 0.25 * height or p[2] - p[0] >= 0.25 * height]
    parts.sort()
    groups: list[list[list[int]]] = []
    for p in parts:
        if groups:
            g = groups[-1]
            right = max(q[2] for q in g)
            if min(right, p[2]) - p[0] > 0.5 * min(p[2] - p[0], right - min(q[0] for q in g)):
                g.append(p)
                continue
        groups.append([p])
    out: list[Glyph] = []
    for g in groups:
        gx0, gy0 = min(p[0] for p in g), min(p[1] for p in g)
        gx1, gy1 = max(p[2] for p in g), max(p[3] for p in g)
        mask = np.isin(labels[gy0:gy1, gx0:gx1], [p[4] for p in g])
        for left, piece in split(mask):
            ys, xs = np.nonzero(piece)
            if len(ys) < 12:
                continue
            ax, ay = x0 + gx0 + left, y0 + gy0
            mark: Box = (ax + int(xs.min()), ay + int(ys.min()), ax + int(xs.max()) + 1, ay + int(ys.max()) + 1)
            out.append(Glyph(mark, normalize(piece[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1])))
    if out:
        size = float(np.median([g.box[3] - g.box[1] for g in out]))
        out = [g for g in out if max(g.box[3] - g.box[1], g.box[2] - g.box[0]) >= 0.45 * size]
    return out


def cluster(found: list[Glyph]) -> list[Glyph]:
    """The run of marks that makes up a number, without stray marks set apart from it."""
    if not found:
        return found
    size = float(np.median([g.box[3] - g.box[1] for g in found]))
    runs_: list[list[Glyph]] = [[found[0]]]
    for g in found[1:]:
        if g.box[0] - runs_[-1][-1].box[2] > 1.2 * size:
            runs_.append([g])
        else:
            runs_[-1].append(g)
    return max(runs_, key=lambda run: (len(run), sum(g.box[3] - g.box[1] for g in run)))


def split(mask: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Cut a mark much wider than tall, which is digits that touch, at the columns with the least ink."""
    h, w = mask.shape
    if w <= 1.25 * h:
        return [(0, mask)]
    n = max(2, round(w / (0.8 * h)))
    ink = mask.sum(axis=0)
    cuts = [0]
    for i in range(1, n):
        target = i * w / n
        lo, hi = int(target - 0.25 * w / n), int(target + 0.25 * w / n) + 1
        window = ink[lo:hi]
        best = lo + min(range(len(window)), key=lambda j: (window[j], abs(lo + j - target)))
        cuts.append(best)
    cuts.append(w)
    return [(a, mask[:, a:b]) for a, b in itertools.pairwise(cuts) if b > a]


def normalize(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    side = max(h, w)
    square = np.zeros((side, side), np.uint8)
    square[(side - h) // 2 : (side - h) // 2 + h, (side - w) // 2 : (side - w) // 2 + w] = mask * 255
    small = np.asarray(Image.fromarray(square).resize((SIZE, SIZE), Image.Resampling.BILINEAR), np.float32) / 255
    return np.concatenate([small.ravel(), [2.0 * w / h]])


def ink(gray: np.ndarray) -> np.ndarray:
    """The dark pixels: Otsu's lower class, which on a black-and-white scan is the black pixels alone."""
    return gray <= min(otsu(gray), 200)


def layout(pdf: Path) -> Layout:
    gray = render(pdf)
    angle = skew(ink(gray))
    gray = np.asarray(Image.fromarray(gray).rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255))
    dark = ink(gray)
    height, width = dark.shape
    lines = hlines(dark, int(0.3 * width))
    if not lines:
        raise ValueError("no table rules")
    longest = max(line.x1 - line.x0 for line in lines)
    full = [line for line in lines if line.x1 - line.x0 >= 0.85 * longest]
    # The candidates' table has six columns, so seven rules cross each of its rows.
    for top, bottom in itertools.pairwise(full):
        band = dark[top.y1 + 2 : bottom.y0 - 1]
        if band.shape[0] < 15:
            continue
        left, right = min(line.x0 for line in full) - 60, max(line.x1 for line in full) + 60
        found = [(a + b) // 2 for a, b in runs(band.mean(axis=0) >= 0.8, gap=4)]
        columns = [x for x in found if left <= x <= right]
        if len(columns) == 7:
            break
    else:
        raise ValueError("no row with six columns")
    result = Layout(width, height, angle, columns)
    # Rows: the rules that cross the serial column, which holds nothing wider than a serial number. A candidate's row
    # holds one or two digits there, and its rules run on across the remark column.
    rows = [(a, b) for a, b in bands(dark, columns[0], columns[1], max(top.y0 - 400, 0)) if b - a >= 30]
    end = None
    for a, b in rows:
        cell = (columns[0] + 6, a + 4, columns[1] - 5, b - 3)
        serial = [g for g in glyphs(dark, cell) if tall(g, b - a)]
        spread = (max(g.box[2] for g in serial) - min(g.box[0] for g in serial)) if serial else 0
        if not 1 <= len(serial) <= 2 or spread > 0.45 * (cell[2] - cell[0]):
            if result.votes:
                break
            continue
        if not (crosses(dark, a - 3, columns[5], columns[6]) and crosses(dark, b + 2, columns[5], columns[6])):
            if result.votes:
                break
            continue
        box = (columns[4] + 6, a + 4, columns[5] - 5, b - 3)
        result.votes.append(Number(box, cluster(glyphs(dark, box))))
        end = b
    if end is None:
        raise ValueError("no candidate rows")
    # The totals box follows the candidates and spans the columns left of the votes. Its rows are found under the
    # symbol column, where the box holds no text.
    for a, b in bands(dark, columns[3], columns[4], end - 5):
        if len(result.totals) == 3 or b - a < 30:
            if len(result.totals) == 3:
                break
            continue
        band = dark[a + 4 : b - 3, columns[2] : columns[6] + 8]
        rules = [columns[2] + (x0 + x1) // 2 for x0, x1 in runs(band.mean(axis=0) >= 0.8, gap=4)]
        right = rules[0] if rules else columns[4]
        found = glyphs(dark, (columns[0] + 6, a + 4, right - 5, b - 3))
        if len(found) >= 8:
            result.totals.append(Number((columns[0] + 6, a + 4, right - 5, b - 3), found))
    return result


def crosses(dark: np.ndarray, y: int, x0: int, x1: int) -> bool:
    """Whether a rule near y spans the strip from x0 to x1."""
    strip = dark[max(y - 6, 0) : y + 6, x0 + 6 : x1 - 5]
    return bool(strip.size) and float(strip.any(axis=0).mean()) >= 0.8


def normalized(sheet: Layout, box: Box) -> tuple[float, float, float, float]:
    """A box on the straightened page as a fraction of the page as published, undoing the straightening."""
    t = np.radians(sheet.angle)
    cx, cy = sheet.width / 2, sheet.height / 2
    xs, ys = [], []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        xs.append(cx + (x - cx) * np.cos(t) - (y - cy) * np.sin(t))
        ys.append(cy + (x - cx) * np.sin(t) + (y - cy) * np.cos(t))
    clip = lambda v: float(min(max(v, 0.0), 1.0))
    return (
        clip(min(xs) / sheet.width),
        clip(min(ys) / sheet.height),
        clip(max(xs) / sheet.width),
        clip(max(ys) / sheet.height),
    )


def attempt(pdf: Path) -> Layout | str:
    """The sheet's layout, or why it could not be found."""
    try:
        return layout(pdf)
    except (ValueError, RuntimeError, IndexError) as failure:
        return str(failure) or type(failure).__name__


def labelled(sheet: Layout, valid: int, cancelled: int) -> list[tuple[np.ndarray, int]]:
    """The digits of the totals a sheet prints, each with the value the portal states for it."""
    out: list[tuple[np.ndarray, int]] = []
    if len(sheet.totals) < 3:
        return out
    for number, value in zip(sheet.totals, (valid, cancelled, valid + cancelled), strict=False):
        digits = str(value)
        if len(number.glyphs) <= len(digits):
            continue
        tail = number.glyphs[-len(digits) :]
        before = number.glyphs[-len(digits) - 1]
        gaps = [b.box[0] - a.box[2] for a, b in itertools.pairwise(tail)]
        if gaps and tail[0].box[0] - before.box[2] < max(gaps):
            continue  # the number does not stand apart from the label before it
        out += [(g.features, int(d)) for g, d in zip(tail, digits, strict=True)]
    return out


class Reader:
    """A nearest-neighbour classifier of Bengali digits."""

    def __init__(self, samples: list[tuple[np.ndarray, int]]) -> None:
        self.x = np.stack([s for s, _ in samples]).astype(np.float32)
        self.y = np.array([d for _, d in samples])

    def ranked(self, glyph: Glyph) -> list[tuple[int, float]]:
        """Each digit with the distance of its nearest known sample, nearest first."""
        distance = ((self.x - glyph.features) ** 2).sum(axis=1)
        best: dict[int, float] = {}
        near = np.argpartition(distance, 200)[:200] if len(distance) > 200 else np.arange(len(distance))
        for i in near[np.argsort(distance[near])]:
            best.setdefault(int(self.y[i]), float(distance[i]))
        return sorted(best.items(), key=lambda item: item[1])

    def votes(self, numbers: list[Number], valid: int) -> tuple[list[int], bool] | None:
        """The numbers read, if they add up to the valid votes, and whether one digit had to change for that: a
        digit read as its runner-up, when that is the only such change that closes the gap and that digit is the
        least certain one on the sheet."""
        read: list[list[list[tuple[int, float]]]] = [[self.ranked(g) for g in n.glyphs] for n in numbers]
        if any(not row for row in read):
            return None
        values = [int("".join(str(r[0][0]) for r in row)) for row in read]
        gap = valid - sum(values)
        if gap == 0:
            return values, False
        margins = [
            (r[1][1] - r[0][1] if len(r) > 1 else float("inf"), n, k)
            for n, row in enumerate(read)
            for k, r in enumerate(row)
        ]
        fixes = []
        for n, row in enumerate(read):
            for k, r in enumerate(row):
                if len(r) > 1 and (r[1][0] - r[0][0]) * 10 ** (len(row) - 1 - k) == gap:
                    fixes.append((n, k))
        least = min(margins)
        if len(fixes) != 1 or fixes[0] != (least[1], least[2]):
            return None
        n, k = fixes[0]
        values[n] += gap
        return values, True


def tall(glyph: Glyph, row: int) -> bool:
    return glyph.box[3] - glyph.box[1] >= 0.2 * row


def bands(dark: np.ndarray, x0: int, x1: int, start: int) -> list[tuple[int, int]]:
    """The bands between the horizontal rules that cross the strip from x0 to x1 below `start`."""
    strip = dark[start:, x0 + 6 : x1 - 5]
    smeared = strip.copy()
    for shift in (1, 2):
        smeared[shift:] |= strip[:-shift]
        smeared[:-shift] |= strip[shift:]
    cover = smeared.mean(axis=1)
    plain = strip.mean(axis=1)
    rules: list[int] = []
    for y0, y1 in runs(cover >= 0.6, gap=2):
        if y1 - y0 > 16:
            continue
        above = plain[max(y0 - 10, 0) : max(y0 - 2, 0)]
        below = plain[y1 + 3 : y1 + 11]
        if max(above.mean() if above.size else 0, below.mean() if below.size else 0) > 0.2:
            continue
        rules.append(start + (y0 + y1) // 2)
    return [(a + 3, b - 2) for a, b in itertools.pairwise(rules)]
