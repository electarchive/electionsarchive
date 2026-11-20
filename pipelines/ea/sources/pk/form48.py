"""Pakistan's 2024 National Assembly election by polling station in the Punjab and Islamabad, from the returning
officers' consolidated statements of the count (Form 48).

After the count of 8 February 2024, each returning officer typed a Form 48: one row per polling station with each
candidate's valid votes (tendered and challenged votes included), the valid votes, the invalid votes, and the total,
and a row of totals at the foot. The Election Commission's shared folder of the Punjab's forms holds the Form 48 of
each National Assembly constituency of the Punjab and of Islamabad, NA-46 to NA-189, as a PDF of photographs of the
typed pages (`fetch` lists the folder through Drive's embedded folder view and downloads each file).

The photographs carry no usable text, so each page is read here. It is turned so that its rules run level and its
polling-station column lies to the left of the candidates' columns; its horizontal rules give the rows, and its
vertical rules, found band by band since a photographed page leans and bows, are fitted to the columns of the
document's table: two for the serial number and the station, one per candidate, and three for the valid, invalid,
and total votes. Each cell's glyphs are compared with digits of the same typeface: those the form prints under its
heading to number its columns, and then those of the rows already settled. A station's figures are kept only when
one reading of its glyphs makes the candidates' votes sum to its valid votes and the valid and invalid votes sum to
its total.

The election, each constituency's contest, its candidates, and its declared Form 49 result are held by pk.ecp
(`ecp2024`). Each constituency's stations make a second, finer result set of the same contest beside that default,
with its completeness. The form's columns follow the Form 49 serials in most constituencies; where the printed
totals show they do not, each column is matched to a candidate by the name the heading prints above it and the total
it adds up to. A constituency is left out when its stations give a candidate more votes than its Form 49 or than the
form's printed totals, or when too few of its rows can be read."""

import html
import io
import itertools
import json
import os
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid5

import numpy as np
import polars as pl
import pymupdf
from PIL import Image
from scipy import ndimage
from scipy.optimize import linear_sum_assignment

from ea import package, raw
from ea.package import Package
from ea.sources.pk import ecp
from ea.store import DATA

SOURCE = "pk.form48"
ROOT = "14JBo7rHc8-VzvtmWon_shlgeCCZ21DfK"
LISTING = "https://drive.google.com/embeddedfolderview?id={}"
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download"
ENTRY = re.compile(
    r'<div class="flip-entry" id="entry-([^"]+)".*?<a href="([^"]+)".*?<div class="flip-entry-title">(.*?)</div>',
    re.DOTALL,
)
SEAT = re.compile(r"^NA[\s_.-]*0*(\d+)", re.IGNORECASE)


def entries(folder: str) -> list[tuple[str, bool, str]]:
    text = raw.cached(SOURCE, LISTING.format(folder)).path.read_text()
    return [(id, "/folders/" in href, html.unescape(title)) for id, href, title in ENTRY.findall(text)]


def _not_html(path: Path) -> None:
    head = path.read_bytes()[:1024].lstrip().lower()
    if not head.startswith(b"%pdf"):
        raise ValueError("the host returned something other than a PDF")


def files(fetching: bool = False) -> dict[str, str]:
    """The Drive id of each constituency's Form 48, by seat code."""
    if fetching:
        raw.fetch(SOURCE, LISTING.format(ROOT), suffix=".html")
    folder = next(id for id, is_folder, title in entries(ROOT) if is_folder and title == "National_Assembly")
    if fetching:
        raw.fetch(SOURCE, LISTING.format(folder), suffix=".html")
    found: dict[str, str] = {}
    for id, is_folder, title in entries(folder):
        m = SEAT.match(title.strip())
        if is_folder or m is None:
            continue
        found[f"NA-{int(m.group(1))}"] = id
    return found


CONFIRMED = DRIVE + "&confirm=t"  # past the page Drive shows instead of a file too large for its virus scan


def url(id: str) -> str:
    """The cached download of a Drive file."""
    return DRIVE.format(id) if DRIVE.format(id) in raw.keys(SOURCE) else CONFIRMED.format(id)


def fetch() -> None:
    found = files(True)

    def one(id: str) -> None:
        if DRIVE.format(id) in raw.keys(SOURCE):
            return
        try:
            raw.fetch(SOURCE, DRIVE.format(id), suffix=".pdf", check=_not_html)
        except ValueError:
            raw.fetch(SOURCE, CONFIRMED.format(id), suffix=".pdf", check=_not_html)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(one, found.values()))


# ---- reading a page ------------------------------------------------------------------------------------------------

WORK = DATA / "work" / SOURCE
LONG_SIDE = 3000  # the most pixels a page is rendered at along its longer side
ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}
WORKERS = 2
GAP = 50


def render(path: Path, number: int) -> Image.Image:
    """A page in grey at the resolution of the photograph it holds, at most LONG_SIDE pixels along its longer side."""
    with pymupdf.open(path) as document:
        page = document[number - 1]
        infos = page.get_image_info()
        side = max(page.rect.width, page.rect.height)
        dpi = 200.0
        if infos:
            big = max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
            box_side = max(big["bbox"][2] - big["bbox"][0], big["bbox"][3] - big["bbox"][1])
            if box_side > 0:
                dpi = max(big["width"], big["height"]) / box_side * 72
        dpi = min(dpi, LONG_SIDE / side * 72)
        pix = page.get_pixmap(dpi=round(max(dpi, 100)), colorspace=pymupdf.csGRAY)
        return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def binary(gray: np.ndarray) -> np.ndarray:
    """Dark pixels against the local background, so a photograph's uneven light does not swallow faint print."""
    g = gray.astype(np.float32)
    local = ndimage.uniform_filter(g, 61)
    return (g < local - 25) | (g < 90)


def deskew(image: Image.Image) -> Image.Image:
    small = image.resize((image.width // 4, image.height // 4))
    dark = Image.fromarray((binary(np.asarray(small)) * 255).astype(np.uint8))

    def score(angle: float) -> float:
        rows = np.asarray(dark.rotate(angle, resample=Image.Resampling.NEAREST), dtype=np.float32).sum(axis=1)
        return float(np.square(np.diff(rows)).sum())

    best = max((a / 10 for a in range(-30, 31)), key=score)
    return image if best == 0 else image.rotate(best, resample=Image.Resampling.BICUBIC, fillcolor=255)


def _runs(mask: np.ndarray, gap: int) -> list[tuple[int, int]]:
    found: list[list[int]] = []
    for i in np.flatnonzero(mask):
        if found and i - found[-1][1] <= gap:
            found[-1][1] = int(i)
        else:
            found.append([int(i), int(i)])
    return [(a, b) for a, b in found]


def _long(dark: np.ndarray, window: int, axis: int) -> np.ndarray:
    """Pixels inside a straight dark run of at least `window` along the axis."""
    d = dark if axis == 1 else dark.T
    c = np.cumsum(d, axis=1, dtype=np.int32)
    c = np.concatenate([np.zeros((d.shape[0], 1), np.int32), c], axis=1)
    full = (c[:, window:] - c[:, :-window]) >= window - 1
    out = np.zeros_like(d)
    # mark every pixel covered by a full window
    starts = np.zeros((d.shape[0], d.shape[1] + 1), np.int32)
    starts[:, : full.shape[1]] += full
    starts[:, window : window + full.shape[1]] -= full
    out = np.cumsum(starts, axis=1)[:, : d.shape[1]] > 0
    return out if axis == 1 else out.T


@dataclass
class Lines:
    """A page's rules as found: the horizontal rules, the vertical rules in each band, and the vertical rules most
    bands share."""

    rows: list[int]
    at: list[list[int]]
    common: list[int]
    size: tuple[int, int]
    weak: list[list[int]] = field(default_factory=list)  # the fainter rules of each band too


@dataclass
class Grid:
    rows: list[int]  # the y of each horizontal rule
    cols: list[list[int]]  # the x of each vertical rule in each band between two rules
    size: tuple[int, int]

    def box(self, r: int, c: int) -> tuple[int, int, int, int]:
        return (self.cols[r][c], self.rows[r], self.cols[r][c + 1], self.rows[r + 1])


def lines(gray: np.ndarray) -> Lines | None:
    dark = binary(gray)
    h, w = dark.shape
    # a rule that bows across a photographed page still runs within a few rows
    horizontal = _long(ndimage.maximum_filter1d(dark, size=5, axis=0), max(w // 20, 40), 1)
    score = horizontal.sum(axis=1)
    if score.max() < w // 4:
        return None
    rules = [((a + b) // 2) for a, b in _runs(score > 0.45 * score.max(), 4)]
    spans = [np.flatnonzero(horizontal[max(y - 3, 0) : y + 4].any(axis=0)) for y in rules]
    lengths = [int(s[-1] - s[0]) if len(s) else 0 for s in spans]
    full = [s for s, n in zip(spans, lengths, strict=True) if n >= 0.85 * max(lengths)]
    x0 = max(min(int(s[0]) for s in full) - 10, 0)
    x1 = min(max(int(s[-1]) for s in full) + 10, w)
    rules = [y for y in rules if horizontal[max(y - 3, 0) : y + 4, x0:x1].any(axis=0).mean() > 0.5]
    bands = [(a, b) for a, b in itertools.pairwise(rules) if b - a > max(h // 120, 12)]
    if len(bands) < 2:
        return None
    rows = sorted({y for band in bands for y in band})
    smeared = ndimage.maximum_filter1d(dark, size=5, axis=1)  # a rule that leans still fills its column
    at: list[list[int]] = []
    weak: list[list[int]] = []
    for a, b in itertools.pairwise(rows):
        if b - a <= 12:
            at.append([])
            weak.append([])
            continue
        # over the whole band, rules included: a rule meets the rules above and below it, a digit stands clear
        profile = smeared[a : b + 1, x0:x1].mean(axis=0)
        at.append([x0 + (p + q) // 2 for p, q in _runs(profile > 0.85, 3)])
        weak.append([x0 + (p + q) // 2 for p, q in _runs(profile > 0.5, 3)])
    every = sorted(x for xs in at for x in xs)
    clusters: list[list[int]] = []
    for x in every:
        if clusters and x - clusters[-1][-1] <= 8:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    filled = [xs for xs in at if xs]
    common = [int(np.median(c)) for c in clusters if len(c) >= 0.4 * len(filled)]
    return Lines(rows, at, common, (w, h), weak)


def _window(xs: list[int], weak: list[int], columns: int) -> list[int] | None:
    """A band's rules of the table proper: the polling station's column is the widest, with the serial's column
    before it and the candidates', valid, invalid, and total votes' after it. Rules beyond the table (a photograph's
    edge) are dropped, and a missing outer rule before the serial is taken from the band's fainter rules."""
    if len(xs) < columns - 1:
        return None
    w = int(np.argmax(np.diff(xs)))
    if w > columns / 2:
        return None
    end = w + columns - 1
    if end >= len(xs):
        return None
    if w >= 1:
        return xs[w - 1 : end + 1]
    gap = float(np.median(np.diff(xs[w + 2 : end + 1])))
    left = [x for x in weak if xs[w] - 4 * gap <= x <= xs[w] - 0.5 * gap]
    if not left:
        return None
    return [min(left), *xs[w : end + 1]]


def _exact(found: Lines, columns: int) -> list[list[int]]:
    weak = found.weak or [[] for _ in found.at]
    return [x for xs, wk in zip(found.at, weak, strict=True) if (x := _window(xs, wk, columns)) is not None]


def flipped(found: Lines, columns: int) -> bool:
    """Whether the page is upside down: the widest column, the polling station's, sits on the right."""
    votes = [int(np.argmax(np.diff(xs))) > len(xs) / 2 for xs in found.at if len(xs) >= columns - 1]
    return sum(votes) > len(votes) / 2


def template(pages: list[Lines], columns: int) -> list[float] | None:
    """The vertical rules of the document's table, as fractions of its width, from the bands where all were found."""
    exact = [xs for p in pages for xs in _exact(p, columns)]
    if not exact:
        return None
    shares = np.array([[(x - c[0]) / (c[-1] - c[0]) for x in c] for c in exact])
    return [float(v) for v in np.median(shares, axis=0)]


def _interpolate(shares: list[float], matched: dict[int, float]) -> list[float]:
    """Each rule's place, from the nearest matched rules on either side of it in the template."""
    known = sorted(matched)
    out = []
    for t, share in enumerate(shares):
        if t in matched:
            out.append(matched[t])
            continue
        left = [k for k in known if k < t][-2:]
        right = [k for k in known if k > t][:2]
        pair = (left[-1:] + right[:1]) if left and right else (left if len(left) == 2 else right)
        if len(pair) < 2:
            pair = known[:2] if len(known) >= 2 else pair
        a, b = pair[0], pair[-1]
        scale = (matched[b] - matched[a]) / (shares[b] - shares[a]) if b != a else 0.0
        out.append(matched[a] + (share - shares[a]) * scale)
    return out


def _nearest(found: np.ndarray, guess: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each guess, the nearest found rule and how far it is."""
    at = np.clip(np.searchsorted(found, guess), 1, len(found) - 1)
    left, right = found[at - 1], found[at]
    x = np.where(np.abs(guess - left) <= np.abs(right - guess), left, right)
    return x, np.abs(x - guess)


def _match(c: list[int], shares: list[float]) -> list[int] | None:
    """A band's rules with the document's columns: the template stretched over the rules found, each template
    rule matched to a found rule near where its neighbours put it, and placed between them where none was found."""
    if len(c) < 0.6 * len(shares):
        return None
    gaps = np.diff(shares)
    found = np.array(c, dtype=float)
    stretched = np.array(shares)
    best: dict[int, float] = {}
    for i in range(min(4, len(c))):
        for j in range(max(len(c) - 4, i + 1), len(c)):
            width = c[j] - c[i]
            for a in range(3):
                for b in range(len(shares) - 3, len(shares)):
                    if b - a < 0.6 * len(shares):
                        continue
                    scale = width / (shares[b] - shares[a])
                    tol = 0.35 * float(gaps.min()) * scale
                    x, far = _nearest(found, c[i] + (stretched - shares[a]) * scale)
                    hits = far <= tol
                    if int(hits.sum()) > len(best):
                        best = {int(t): float(x[t]) for t in np.flatnonzero(hits)}
    if len(best) < 2:
        return None
    matched = best
    span = (max(matched.values()) - min(matched.values())) / (shares[max(matched)] - shares[min(matched)])
    tol = 0.35 * float(gaps.min()) * span
    for _ in range(3):
        nearest, far = _nearest(found, np.array(_interpolate(shares, matched)))
        matched = {}
        used: set[float] = set()
        for t in range(len(shares)):
            x = float(nearest[t])
            if far[t] <= tol and x not in used:
                matched[t] = x
                used.add(x)
        if len(matched) < 2:
            return None
    if len(matched) < 0.75 * len(shares) or 0 not in matched and 1 not in matched:
        return None
    out = [round(x) for x in _interpolate(shares, matched)]
    if any(b - a < 6 for a, b in itertools.pairwise(out)):
        return None
    return out


def fit(found: Lines, shares: list[float]) -> Grid | None:
    """The page's grid with the document's columns, fitted band by band, since a photographed page leans and bows;
    a band whose rules don't fit takes the nearest band's."""
    fitted = [_match(xs, shares) for xs in found.at]
    good = [r for r, f in enumerate(fitted) if f is not None]
    if not good:
        return None
    cols = []
    for r, f in enumerate(fitted):
        cols.append(f if f is not None else fitted[min(good, key=lambda g: abs(g - r))])  # type: ignore[index]
    return Grid(found.rows, [list(c) for c in cols if c is not None], found.size)


DIGITS = "0123456789"


def _tesseract(image: Image.Image, psm: str) -> list[tuple[int, int, str]]:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    done = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", psm, "-l", "eng", "-c", f"tessedit_char_whitelist={DIGITS}", "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=False,
        env=ENV,
    )
    if done.returncode:
        return []
    out: list[tuple[int, int, str]] = []
    for line in done.stdout.decode().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 12 and parts[0] == "5" and parts[11].strip():
            out.append((int(parts[6]), int(parts[6]) + int(parts[8]), parts[11].strip()))
    return out


def _clean(crop: np.ndarray) -> np.ndarray:
    """A cell's pixels with what is left of its rules at the edges turned white."""
    out = crop.copy()
    dark = out < 140
    h, w = dark.shape
    for x in list(range(min(6, w))) + list(range(max(w - 6, 0), w)):
        if dark[:, x].mean() > 0.4:
            out[:, x] = 255
    for y in list(range(min(5, h))) + list(range(max(h - 5, 0), h)):
        if dark[y, :].mean() > 0.4:
            out[y, :] = 255
    return out


def crop(gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return _clean(gray[y0 + 3 : max(y1 - 2, y0 + 5), x0 + 3 : max(x1 - 2, x0 + 5)])


def strip(crops: list[np.ndarray], scale: float = 1.0, psm: str = "7") -> list[str]:
    """Each cell's digits, read from one strip of the cells set apart by white space."""
    if scale != 1:
        crops = [
            np.asarray(
                Image.fromarray(c).resize(
                    (max(round(c.shape[1] * scale), 1), max(round(c.shape[0] * scale), 1)), Image.Resampling.LANCZOS
                )
            )
            for c in crops
        ]
    height = max(c.shape[0] for c in crops) + 40
    width = sum(c.shape[1] for c in crops) + GAP * (len(crops) + 1)
    canvas = np.full((height, width), 255, np.uint8)
    spans: list[tuple[int, int]] = []
    x = GAP
    for c in crops:
        top = 20 + (height - 40 - c.shape[0]) // 2
        canvas[top : top + c.shape[0], x : x + c.shape[1]] = c
        spans.append((x, x + c.shape[1]))
        x += c.shape[1] + GAP
    cells = [""] * len(crops)
    for left, right, text in _tesseract(Image.fromarray(canvas), psm):
        centre = (left + right) / 2
        for i, (a, b) in enumerate(spans):
            if a - GAP / 2 <= centre <= b + GAP / 2:
                cells[i] += text
                break
    return cells


def single(c: np.ndarray) -> list[str]:
    """One cell read on its own at a few sizes."""
    out = []
    for scale, psm in ((1.5, "8"), (2.0, "10"), (2.5, "8")):
        img = Image.fromarray(c)
        img = img.resize(
            (max(round(img.width * scale), 1), max(round(img.height * scale), 1)), Image.Resampling.LANCZOS
        )
        canvas = Image.new("L", (img.width + 40, img.height + 40), 255)
        canvas.paste(img, (20, 20))
        out.append("".join(t for _, _, t in _tesseract(canvas, psm)))
    return out


def number(text: str) -> int | None:
    return int(text) if re.fullmatch(r"\d{1,5}", text) else None


def options(texts: list[str]) -> list[int]:
    out: list[int] = []
    for t in texts:
        v = number(t)
        if v is not None and v not in out:
            out.append(v)
    return out


def solve(cands: list[list[int]], k: int, limit: int = 20000) -> tuple[list[int] | None, int]:
    """The one choice of a value per cell (the candidates, then valid, invalid, total) that satisfies the row's
    arithmetic, and how many choices did."""
    if any(not c for c in cands):
        return None, 0
    space = 1
    for c in cands:
        space *= len(c)
    if space > limit:
        return None, 0
    found: list[list[int]] = []
    for valid, invalid, total in itertools.product(*cands[k:]):
        if valid + invalid != total:
            continue
        # the candidates: count the choices whose sum is the valid votes
        sums: dict[int, list[list[int]]] = {0: [[]]}
        for c in cands[:k]:
            nxt: dict[int, list[list[int]]] = {}
            for s, ways in sums.items():
                for v in c:
                    if s + v <= valid:
                        bucket = nxt.setdefault(s + v, [])
                        if len(bucket) < 2:
                            bucket.extend([w + [v] for w in ways[: 2 - len(bucket)]])
            sums = nxt
        for way in sums.get(valid, []):
            found.append([*way, valid, invalid, total])
            if len(found) > 1:
                return None, 2
    return (found[0], 1) if found else (None, 0)


# ---- reading the figures -------------------------------------------------------------------------------------------

SIZE = (10, 14)  # the width and height a glyph is scaled to before it is compared


@dataclass
class Cell:
    glyphs: list[tuple[int, int, int, int]]  # each glyph's box on the page, left to right
    dash: bool = False  # a short stroke and nothing else


def cell_glyphs(
    dark: np.ndarray, box: tuple[int, int, int, int], height: float | None, width: float | None = None
) -> Cell:
    """The glyphs in a cell: its dark components, with the remnants of its rules and specks left out, pieces of one
    glyph joined, and glyphs that touch split."""
    x0, y0, x1, y1 = box
    ax, ay = x0 + 3, y0 + 3
    if x1 - x0 < 10 or y1 - y0 < 10:
        return Cell([])
    sub = dark[ay : max(y1 - 2, ay + 4), ax : max(x1 - 2, ax + 4)].copy()
    h, w = sub.shape
    if h < 4 or w < 4:
        return Cell([])
    for x in list(range(min(5, w))) + list(range(max(w - 5, 0), w)):
        if sub[:, x].mean() > 0.35:
            sub[:, x] = False
    for y in list(range(min(5, h))) + list(range(max(h - 5, 0), h)):
        if sub[y, :].mean() > 0.35:
            sub[y, :] = False
    # what is left of a rule that leans into the cell: runs longer than any digit is tall or wide
    reach = round(1.4 * height) if height else 40
    if h > reach:
        sub &= ~_long(sub, reach, 0)
    across = max(round(0.7 * w), 3 * reach)
    if w > across:
        sub &= ~_long(sub, across, 1)
    labels, _ = ndimage.label(sub, structure=np.ones((3, 3)))
    parts: list[list[int]] = []
    for s in ndimage.find_objects(labels):
        if s is None:
            continue
        py0, py1, px0, px1 = s[0].start, s[0].stop, s[1].start, s[1].stop
        ph, pw = py1 - py0, px1 - px0
        if ph * pw < 6:
            continue
        if (px0 <= 3 or px1 >= w - 3) and pw <= max(3, 0.2 * (height or 20)):
            continue
        parts.append([px0, py0, px1, py1])
    if not parts:
        return Cell([])
    tall = max(p[3] - p[1] for p in parts)
    ref = height or tall
    dash = False
    keep: list[list[int]] = []
    for p in sorted(parts):
        ph, pw = p[3] - p[1], p[2] - p[0]
        if ph < 0.45 * ref:
            if ph <= 0.3 * ref and pw >= 0.4 * ref:
                dash = True
            continue
        if ph > 1.8 * ref:
            continue
        if keep and min(p[2], keep[-1][2]) - max(p[0], keep[-1][0]) >= 0.5 * min(pw, keep[-1][2] - keep[-1][0]):
            k = keep[-1]
            keep[-1] = [min(k[0], p[0]), min(k[1], p[1]), max(k[2], p[2]), max(k[3], p[3])]
        else:
            keep.append(p)
    # glyphs that touch are cut where the fewest dark pixels lie near where an even cut would fall
    out: list[tuple[int, int, int, int]] = []
    for p in keep:
        pw = p[2] - p[0]
        n = max(1, round(pw / width)) if width and pw > 1.5 * width else 1
        cuts = [p[0]]
        if n > 1 and width:
            profile = sub[p[1] : p[3], p[0] : p[2]].sum(axis=0)
            for i in range(1, n):
                at = round(i * pw / n)
                lo, hi = max(at - round(0.3 * width), 1), min(at + round(0.3 * width), pw - 1)
                cut = lo + int(np.argmin(profile[lo:hi])) if hi > lo else at
                cuts.append(p[0] + cut)
        cuts.append(p[2])
        for a, b in itertools.pairwise(cuts):
            if b - a >= 2:
                out.append((ax + a, ay + p[1], ax + b, ay + p[3]))
    return Cell(out, dash and not out)


def feature(gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    g = 255 - gray[y0:y1, x0:x1].astype(np.float32)
    img = Image.fromarray(g).resize(SIZE, Image.Resampling.BILINEAR)
    v = np.asarray(img, dtype=np.float32).ravel()
    v = v / (np.linalg.norm(v) + 1e-6)
    aspect = (x1 - x0) / max(y1 - y0, 1)
    return np.concatenate([v, [aspect * 0.8]]).astype(np.float32)


@dataclass
class Bank:
    """Labelled glyphs to read others against."""

    features: list[np.ndarray] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)

    def add(self, f: np.ndarray, label: int) -> None:
        self.features.append(f)
        self.labels.append(label)

    def classes(self) -> set[int]:
        return set(self.labels)

    def matrix(self) -> tuple[np.ndarray, np.ndarray]:
        return np.stack(self.features), np.array(self.labels)


def classify(bank: tuple[np.ndarray, np.ndarray], features: np.ndarray, spread: float) -> list[list[int]]:
    """The digits each glyph may be, best first: each digit's nearest labelled glyph, kept within `spread` of the
    best."""
    known, labels = bank
    if len(features) == 0:
        return []
    d2 = (features**2).sum(axis=1)[:, None] + (known**2).sum(axis=1)[None, :] - 2 * features @ known.T
    d = np.sqrt(np.maximum(d2, 0))
    per = np.full((len(features), 10), np.inf)
    for digit in range(10):
        mask = labels == digit
        if mask.any():
            per[:, digit] = d[:, mask].min(axis=1)
    out = []
    for row in per:
        order = np.argsort(row)[:3]
        top = row[order[0]]
        out.append([int(x) for x in order if np.isfinite(row[x]) and row[x] <= top * spread + 0.02])
    return out


@dataclass
class Page:
    number: int
    gray: np.ndarray
    dark: np.ndarray
    lines: Lines | None
    grid: Grid | None = None
    cells: list[list[Cell]] = field(default_factory=list)


def prepare(path: Path, number: int, columns: int, keep: Path | None = None) -> Page:
    """A page turned upright and level, with its rules; the turned page is saved to `keep` when given, since the
    boxes of its figures are given on it."""
    image = deskew(render(path, number))

    def fit_score(found: Lines | None) -> float:
        # how near the bands' counts of vertical rules come to the form's columns
        if found is None:
            return float("inf")
        counts = [len(xs) for xs in found.at if xs]
        return float(np.median([abs(c - columns - 1) for c in counts])) if counts else float("inf")

    found = lines(np.asarray(image))
    turned = image.rotate(90, expand=True)
    other = lines(np.asarray(turned))
    if fit_score(other) < fit_score(found):
        image, found = turned, other
    if found is not None and flipped(found, columns):
        image = image.rotate(180)
        found = lines(np.asarray(image))
    if keep is not None:
        keep.parent.mkdir(parents=True, exist_ok=True)
        image.save(keep, quality=60, optimize=True)
    gray = np.asarray(image)
    return Page(number, gray, binary(gray), found)


def values(digits: list[list[int]], cap: int = 16) -> list[int]:
    """The numbers a cell may hold, from the digits each of its glyphs may be, likeliest first."""
    if not digits:
        return []
    combos: list[tuple[int, list[int]]] = [(0, [])]
    for options in digits:
        combos = sorted(((r + i, [*c, d]) for r, c in combos for i, d in enumerate(options)), key=lambda x: x[0])[:cap]
    out: list[int] = []
    for _, c in combos:
        v = int("".join(map(str, c)))
        if v not in out:
            out.append(v)
    return out


def bootstrap(pending: list[tuple[Page, int]], k: int, features: dict[tuple[int, int], np.ndarray]) -> Bank:
    """Glyphs labelled by the rows Tesseract can settle among the first unsettled rows."""
    out = Bank()
    tried = 0
    for p, r in pending:
        row = p.cells[r]
        if sum(bool(cell.glyphs) for cell in row[2:]) < max(2, 0.6 * (k + 3)) or p.grid is None:
            continue
        tried += 1
        if tried > 60 or len(set(out.labels)) == 10 and len(out.labels) > 400:
            break
        boxes = [p.grid.box(r, c) for c in range(2, k + 5)]
        crops = [crop(p.gray, b) for b in boxes]
        a = strip(crops, 2.0)
        b = strip([np.where(binary(c), 0, 255).astype(np.uint8) for c in crops], 2.0)
        opts = [options([x, y]) for x, y in zip(a, b, strict=True)]
        solved, _ = solve(opts, k)
        if solved is None:
            for i, o in enumerate(opts):
                if len(o) != 1:
                    opts[i] = options([a[i], b[i], *single(crops[i])])
            solved, _ = solve(opts, k)
        if solved is None:
            continue
        at = sum(len(cell.glyphs) for cell in row[:2])
        for cell, v in zip(row[2:], solved, strict=True):
            if len(cell.glyphs) == len(str(v)):
                for n, d in enumerate(str(v)):
                    out.add(features[(p.number, r)][at + n], int(d))
            at += len(cell.glyphs)
    return out


@dataclass
class Row:
    page: int
    band: int
    serial: int | None
    serials: list[int]
    figures: list[int]  # the candidates' votes in the form's column order, then valid, invalid, total
    boxes: list[tuple[int, int, int, int]]
    size: tuple[int, int]


@dataclass
class Document:
    pages: int
    rows: list[Row]
    failed: list[tuple[int, int]]  # the page and band of each row the reads did not settle
    unread: list[int]  # pages without a grid


def read_document(path: Path, k: int, images: Path | None = None, shared: Bank | None = None) -> Document:
    """The station rows of a Form 48 with `k` candidates, each settled by its glyphs and its arithmetic. The glyphs
    are read against the document's own column numbers (printed 1, 2, 3, ... under the heading of every page) and
    then against the glyphs of the rows already settled. A form with too few columns to print every digit borrows
    the digits it lacks from `shared`, the glyphs of the forms read before it, and adds its own settled glyphs to it."""
    columns = k + 5
    with pymupdf.open(path) as document:
        count = len(document)

    def one(n: int) -> Page:
        return prepare(path, n, columns, None if images is None else images / f"{path.stem}-{n}.jpg")

    with ThreadPoolExecutor(WORKERS) as pool:
        pages = list(pool.map(one, range(1, count + 1)))
    shares = template([p.lines for p in pages if p.lines is not None], columns)
    unread: list[int] = []
    for p in pages:
        p.grid = None if p.lines is None or shares is None else fit(p.lines, shares)
        if p.grid is None:
            unread.append(p.number)
    tallest: list[int] = []
    widths: list[int] = []
    for p in pages:
        if p.grid is None:
            continue
        for r in range(len(p.grid.rows) - 1):
            for c in range(2, columns):
                cell = cell_glyphs(p.dark, p.grid.box(r, c), None)
                if cell.glyphs:
                    tallest.append(max(g[3] - g[1] for g in cell.glyphs))
                    widths += [g[2] - g[0] for g in cell.glyphs]
    if not tallest:
        return Document(count, [], [], unread)
    height = float(np.median(tallest))
    # the width of one digit: the commonest width among glyphs no narrower than a 0 nor wider than two digits
    plain = [w for w in widths if 0.35 * height <= w <= 0.8 * height]
    width = float(np.median(plain)) if plain else 0.55 * height
    bank = Bank()
    for p in pages:
        if p.grid is None:
            continue
        p.cells = [
            [cell_glyphs(p.dark, p.grid.box(r, c), height, width) for c in range(columns)]
            for r in range(len(p.grid.rows) - 1)
        ]
        for row in p.cells:
            fits = sum(len(cell.glyphs) == len(str(c + 1)) for c, cell in enumerate(row))
            if fits >= 0.8 * columns:
                for c, cell in enumerate(row):
                    if len(cell.glyphs) == len(str(c + 1)):
                        for g, d in zip(cell.glyphs, str(c + 1), strict=True):
                            bank.add(feature(p.gray, g), int(d))
    if shared is not None:
        for f, label in zip(shared.features, shared.labels, strict=True):
            if bank.labels.count(label) < 3:
                bank.add(f, label)
    pending = [(p, r) for p in pages if p.grid is not None for r in range(len(p.cells))]
    width_ = SIZE[0] * SIZE[1] + 1
    features = {
        (p.number, r): np.array([feature(p.gray, g) for cell in p.cells[r] for g in cell.glyphs]).reshape(-1, width_)
        for p, r in pending
    }
    booted = False
    if len(bank.classes()) < 10:
        # a form that does not number its columns as expected: learn its digits from rows Tesseract settles
        booted = True
        boot = bootstrap(pending, k, features)
        for f, label in zip(boot.features, boot.labels, strict=True):
            bank.add(f, label)
    if len(bank.classes()) < 10:
        return Document(count, [], [(p.number, -1) for p in pages], unread)
    rows: dict[tuple[int, int], Row] = {}
    spreads = [1.15, 1.3, 1.5]
    while spreads:
        spread = spreads.pop(0)
        matrix = bank.matrix()
        learned = Bank()
        left = []
        order = [(p.number, r) for p, r in pending]
        stacked = [features[key] for key in order]
        flat: list[list[int]] = []
        everything = np.concatenate(stacked) if stacked else np.zeros((0, width_))
        for start in range(0, len(everything), 2000):
            flat += classify(matrix, everything[start : start + 2000], spread)
        batch: dict[tuple[int, int], list[list[int]]] = {}
        at = 0
        for key_, f in zip(order, stacked, strict=True):
            batch[key_] = flat[at : at + len(f)]
            at += len(f)
        for p, r in pending:
            row = p.cells[r]
            if sum(bool(cell.glyphs) for cell in row[2:]) < max(2, 0.6 * (k + 3)):
                continue
            guessed = batch[(p.number, r)]
            reads: list[list[list[int]]] = []
            at = 0
            for cell in row:
                reads.append(guessed[at : at + len(cell.glyphs)])
                at += len(cell.glyphs)
            opts = [values(x) if x else [0] for x in reads[2:]]
            solved, _ = solve(opts, k)
            if solved is None:
                left.append((p, r))
                continue
            serials = values(reads[0]) if row[0].glyphs else []
            grid = p.grid
            assert grid is not None
            rows[(p.number, r)] = Row(
                p.number, r, None, serials, solved, [grid.box(r, c) for c in range(2, columns)], grid.size
            )
            at = sum(len(cell.glyphs) for cell in row[:2])
            for cell, v in zip(row[2:], solved, strict=True):
                if len(cell.glyphs) == len(str(v)):
                    for n, d in enumerate(str(v)):
                        learned.add(features[(p.number, r)][at + n], int(d))
                at += len(cell.glyphs)
        pending = left
        for f, label in zip(learned.features[:3000], learned.labels[:3000], strict=True):
            bank.add(f, label)
        if not booted and len(left) > len(rows):
            # the figures are set in another weight than the column numbers: settle a few rows with Tesseract and
            # learn the figures' glyphs from them
            booted = True
            boot = bootstrap(pending, k, features)
            for f, label in zip(boot.features, boot.labels, strict=True):
                bank.add(f, label)
            if boot.labels:
                spreads = [1.15, *spreads]
        if shared is not None:
            for f, label in zip(learned.features[:200], learned.labels[:200], strict=True):
                if shared.labels.count(label) < 400:
                    shared.add(f, label)
        if not pending:
            break
    ordered = [rows[key] for key in sorted(rows)]
    return Document(count, ordered, [(p.number, r) for p, r in pending], unread)


def documents() -> dict[str, raw.Artifact]:
    """Each constituency's cached Form 48, by seat code."""
    return {code: raw.cached(SOURCE, url(id)) for code, id in sorted(files().items(), key=lambda x: int(x[0][3:]))}


PAGES = DATA / "pages" / SOURCE
FORMS: dict[str, dict] = json.loads((Path(__file__).parent / "ecp2024.json").read_text())


SHARED = Bank()


def cached_read(code: str, artifact: raw.Artifact) -> dict:
    """A constituency's read, cached by the scan's hash."""
    WORK.mkdir(parents=True, exist_ok=True)
    cache = WORK / f"{artifact.sha256}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    k = len(FORMS[code]["candidates"])
    d = read_document(artifact.path, k, PAGES, SHARED)
    out = {
        "pages": d.pages,
        "rows": [r.__dict__ for r in d.rows],
        "failed": d.failed,
        "unread": d.unread,
    }
    cache.write_text(json.dumps(out))
    return out


def _read(job: tuple[str, raw.Artifact]) -> str:
    code, artifact = job
    try:
        d = cached_read(code, artifact)
    except Exception as failure:  # one unreadable scan should not stop the others
        return f"{code}: failed: {failure!r}"
    return f"{code}: {len(d['rows'])} rows, {len(d['failed'])} unsettled, {len(d['unread'])} pages unread"


def parse() -> None:
    jobs = [(code, artifact) for code, artifact in documents().items() if code in FORMS]
    with ProcessPoolExecutor(3) as pool:
        for line in pool.map(_read, jobs):
            print(line, flush=True)
    print(write())


# ---- the constituencies --------------------------------------------------------------------------------------------

STAMP = "2024-02-08"
KEY = f"{SOURCE}/{STAMP}"
STATION = "pk.station"
VOTES, CAST, VALID, INVALID = 1, 3, 4, 5
DIGITIZED = 8
MIN_COMPLETENESS = 0.25


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


@dataclass
class Station:
    serial: int
    page: int
    figures: list[int]  # the candidates' votes in Form 49 serial order, then valid, invalid, total
    boxes: list[tuple[float, float, float, float]]  # each figure's box as fractions of the page image


@dataclass
class Seat:
    code: str
    artifact: raw.Artifact
    stations: list[Station]
    completeness: float
    printed: bool  # whether the form's printed totals were read and the stations held against them
    complete: bool  # whether every station of the form was read
    order: str  # how the form's columns were matched to the candidates


def _serials(rows: list[dict]) -> list[int | None]:
    """Each row's station serial: the read that continues the sequence, and where the reads give none, the number
    between its neighbours."""
    chosen: list[int | None] = []
    previous = 0
    for row in rows:
        options = row["serials"]
        pick = previous + 1 if previous + 1 in options else (options[0] if options else None)
        chosen.append(pick)
        if pick is not None:
            previous = pick
    n = len(chosen)
    for i in range(n):
        before = chosen[i - 1] if i > 0 else 0
        after = chosen[i + 1] if i + 1 < n else None
        here = chosen[i]
        if before is not None and after is not None and after - before == 2 and here != before + 1:
            chosen[i] = before + 1
    out: list[int | None] = []
    for i in range(n):
        here = chosen[i]
        before = next((chosen[j] for j in range(i - 1, -1, -1) if chosen[j] is not None), 0)
        after = next((chosen[j] for j in range(i + 1, n) if chosen[j] is not None), None)
        fits = here is not None and before is not None and here > before and (after is None or here < after)
        near = here is not None and (here - (before or 0) <= 6 or (after is not None and after - here <= 6))
        out.append(here if fits and near else None)
    return out


def _box(row: dict, c: int) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = row["boxes"][c]
    w, h = row["size"]
    return (round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4))


def _fold(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _fold(a), _fold(b)).ratio()


def header_names(code: str, artifact: raw.Artifact) -> list[str] | None:
    """The candidates' names as the form's heading prints them, turned on their side above each column, read from
    the first pages; cached by the scan's hash."""
    cache = WORK / f"{artifact.sha256}-names.json"
    if cache.exists():
        return json.loads(cache.read_text())
    k = len(FORMS[code]["candidates"])
    columns = k + 5
    with pymupdf.open(artifact.path) as document:
        count = len(document)
    pages = [prepare(artifact.path, n, columns) for n in range(1, min(count, 3) + 1)]
    shares = template([p.lines for p in pages if p.lines is not None], columns)
    found: list[list[str]] = []
    for p in pages:
        g = None if p.lines is None or shares is None else fit(p.lines, shares)
        if g is None:
            continue
        for r in range(1, len(g.rows) - 1):
            cells = [cell_glyphs(p.dark, g.box(r, c), None) for c in range(columns)]
            fits = sum(len(cell.glyphs) == len(str(c + 1)) for c, cell in enumerate(cells))
            if fits < 0.7 * columns:
                continue
            texts = []
            for c in range(2, k + 2):
                x0, y0, x1, y1 = g.box(r - 1, c)
                crop = Image.fromarray(p.gray[y0 + 3 : y1 - 2, x0 + 2 : x1 - 2]).rotate(-90, expand=True)
                crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
                canvas = Image.new("L", (crop.width + 40, crop.height + 40), 255)
                canvas.paste(crop, (20, 20))
                buffer = io.BytesIO()
                canvas.save(buffer, "PNG")
                done = subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "eng"],
                    input=buffer.getvalue(),
                    capture_output=True,
                    check=False,
                    env=ENV,
                )
                texts.append(" ".join(done.stdout.decode().split()))
            found.append(texts)
            break
    if not found:
        return None
    # each column's name from the page whose reading matches the candidates best
    listed = [c[1] for c in FORMS[code]["candidates"]]
    names = [max((f[i] for f in found), key=lambda t: max(_similar(t, n) for n in listed)) for i in range(k)]
    cache.write_text(json.dumps(names))
    return names


def assemble(code: str, artifact: raw.Artifact, read: dict) -> Seat | str:
    """A constituency's stations with the form's columns matched to the Form 49 candidates, or why it is left out."""
    form = FORMS[code]
    k = len(form["candidates"])
    f49 = [c[3] for c in sorted(form["candidates"], key=lambda c: c[0])]
    valid49 = form["valid"]
    rows = read["rows"]
    if not rows:
        return "no row of the form could be read"
    totals = [r for r in rows if r["figures"][k] > 0.3 * valid49]
    body = [r for r in rows if r["figures"][k] <= 0.3 * valid49]
    serials = _serials(body)
    seen: dict[int, int] = {}
    for s in serials:
        if s is not None:
            seen[s] = seen.get(s, 0) + 1
    kept = [(s, r) for s, r in zip(serials, body, strict=True) if s is not None and seen[s] == 1]
    if not kept:
        return "no station row could be numbered"
    sums = np.array([r["figures"] for _, r in kept]).sum(axis=0)
    valid = int(sums[k])
    completeness = valid / valid49 if valid49 else 0.0
    printed = min(totals, key=lambda r: abs(r["figures"][k] - valid49))["figures"] if totals else None
    if printed is not None and abs(printed[k] - valid49) > 0.05 * valid49:
        printed = None

    # which candidate each column can be: one whose Form 49 total the column's total comes near, the printed total
    # falling short of it by at most the postal votes, or by a few votes where the two forms disagree
    def fits(i: int, j: int) -> bool:
        if printed is not None:
            return abs(f49[j] - printed[i]) <= max(valid49 - printed[k] + 2, 0.03 * f49[j] + 5)
        return bool(abs(sums[i] - completeness * f49[j]) <= 0.35 * completeness * f49[j] + 4 * np.sqrt(f49[j]) + 10)

    feasible = [[fits(i, j) for j in range(k)] for i in range(k)]
    if all(feasible[i][i] for i in range(k)):
        mapping = list(range(k))
        order = "in the order of the Form 49 serials"
    else:
        names = header_names(code, artifact)
        if names is None:
            return "its columns are not in the order of the Form 49 serials, and its heading could not be read"
        listed = [c[1] for c in sorted(form["candidates"], key=lambda c: c[0])]
        similarity = np.array([[_similar(names[i], listed[j]) for j in range(k)] for i in range(k)])
        # a column keeps its serial's candidate where the total fits and its name points to no one else
        sure: dict[int, int] = {}
        for i in range(k):
            others = [similarity[i, j] for j in range(k) if j != i and feasible[i][j]]
            if feasible[i][i] and similarity[i, i] >= max(others, default=0.0) - 0.1:
                sure[i] = i
        rest = [i for i in range(k) if i not in sure]
        free = [j for j in range(k) if j not in sure.values()]
        if rest and free:
            cost = np.array([[-similarity[i, j] if feasible[i][j] else 10.0 for j in free] for i in rest])
            picked_i, picked_j = linear_sum_assignment(cost)
            for a, b in zip(picked_i, picked_j, strict=True):
                i, j = rest[int(a)], free[int(b)]
                if feasible[i][j] and similarity[i, j] >= 0.5:
                    sure[i] = j
        # a column whose name could not be read takes the one candidate left whose total fits it
        changed = True
        while changed:
            changed = False
            left_over = set(range(k)) - set(sure.values())
            for i in range(k):
                if i in sure:
                    continue
                options_ = [j for j in left_over if feasible[i][j]]
                if len(options_) == 1:
                    sure[i] = options_[0]
                    changed = True
                    break
        weak = [i + 1 for i in range(k) if i not in sure]
        if weak:
            return (
                "its columns are not in the order of the Form 49 serials, and the names its heading gives columns "
                f"{weak} could not be matched to a candidate whose total fits the column"
            )
        mapping = [sure[i] for i in range(k)]
        moved = sum(mapping[i] != i for i in range(k))
        order = (
            f"matched to the candidates by the names the form's heading gives them and the totals each adds up to, "
            f"since the form does not list {moved} of them in the order of the Form 49 serials"
        )
    votes = np.zeros(k, dtype=np.int64)
    for i, j in enumerate(mapping):
        votes[j] = sums[i]
    over = [j + 1 for j in range(k) if votes[j] > f49[j] + 2 + 0.002 * f49[j]]
    if over:
        return f"its stations give candidates {over} more votes than Form 49"
    if valid > valid49 + 2:
        return "its stations give more valid votes than Form 49"
    complete = printed is not None and valid == printed[k]
    if printed is not None:
        if complete and any(int(sums[i]) != printed[i] for i in range(k + 2)):
            return "its stations do not add up to the totals the form prints"
        if any(int(sums[i]) > printed[i] for i in range(k + 2)):
            return "its stations give more votes than the totals the form prints"
    if completeness < MIN_COMPLETENESS:
        return f"too few of its rows could be read ({completeness:.0%} of the valid votes)"
    stations = []
    for s, r in kept:
        figures = [0] * k
        boxes: list[tuple[float, float, float, float]] = [(0, 0, 0, 0)] * k
        for i, j in enumerate(mapping):
            figures[j] = r["figures"][i]
            boxes[j] = _box(r, i)
        stations.append(
            Station(s, r["page"], [*figures, *r["figures"][k:]], [*boxes, *(_box(r, i) for i in range(k, k + 3))])
        )
    return Seat(code, artifact, stations, completeness, printed is not None, complete, order)


SOURCE_ID = key("source", "form48", STAMP)
FOLDER = "https://drive.google.com/drive/folders/" + ROOT


def seats() -> tuple[list[Seat], dict[str, str]]:
    found: list[Seat] = []
    left: dict[str, str] = {}
    for code, artifact in documents().items():
        if code not in FORMS:
            left[code] = "no Form 49 is held for it"
            continue
        cache = WORK / f"{artifact.sha256}.json"
        if not cache.exists():
            left[code] = "its form has not been read"
            continue
        seat = assemble(code, artifact, json.loads(cache.read_text()))
        if isinstance(seat, str):
            left[code] = seat
        else:
            found.append(seat)
    return found, left


def build(found: list[Seat], left: dict[str, str]) -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The 2024 Pakistani National Assembly election in the Punjab and Islamabad by polling station, read from the "
        "returning officers' scanned consolidated statements (Form 48)",
    )
    p.external.add(ecp.PAKISTAN)
    p.external.add(STATION)
    election = ecp.key("election", STAMP)
    p.external.add(election)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2024: consolidated statements of the count (Form 48), National Assembly, the Punjab",
        url=FOLDER,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for folder in (ROOT, *(id for id, is_folder, _ in entries(ROOT) if is_folder)):
        if LISTING.format(folder) in raw.keys(SOURCE):
            p.artifact(raw.cached(SOURCE, LISTING.format(folder)), SOURCE_ID)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    for seat in found:
        _seat(p, seat, values, locators)
    if left:
        p.add(
            "note",
            id=key("note", election, "left-out"),
            entity_id=election,
            kind="context",
            body_md="Polling-station results from the returning officers' Forms 48 are stored for "
            f"{len(found)} National Assembly constituencies of the Punjab and Islamabad. Left out: "
            + "; ".join(f"{code} ({why})" for code, why in left.items())
            + ".",
            lang="en",
        )
    p.frame(
        "result_value",
        pl.DataFrame(values, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(locators, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )
    return p


METHOD = (
    "The votes of each polling station as the returning officer's consolidated statement of the count (Form 48) "
    "gives them: one row per station with each candidate's valid votes, the valid votes, the invalid votes, and the "
    "votes polled. The Election Commission published the forms as photographs of the typed sheets, so each page was "
    "turned upright, its ruled grid found, and each figure read glyph by glyph against the digits the form prints "
    "under its own heading. A station's figures are kept only when one reading of its glyphs makes the candidates' "
    "votes sum to its valid votes and the valid and invalid votes sum to the votes polled; each figure keeps the "
    "page and box it was read from. The form's columns were {order}. {coverage} Completeness is the stations' "
    "valid votes divided by the valid votes of the constituency's Form 49, which also counts the postal ballots."
)


def _seat(p: Package, seat: Seat, values: list[dict[str, object]], locators: list[dict[str, object]]) -> None:
    form = FORMS[seat.code]
    k = len(form["candidates"])
    contest = ecp.key("contest", STAMP, seat.code)
    parent = ecp.key("unit", STAMP, seat.code)
    options = [ecp.key("option", STAMP, seat.code, c[0]) for c in sorted(form["candidates"], key=lambda c: c[0])]
    p.external |= {contest, parent, *options}
    rs = key("result_set", STAMP, seat.code)
    p.add(
        "result_set",
        id=rs,
        election_id=ecp.key("election", STAMP),
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=False,
        completeness=round(seat.completeness, 6),
    )
    if seat.complete:
        coverage = (
            "Every station of the form was read, and the stations add up to the totals the form prints in each column."
        )
    elif seat.printed:
        coverage = (
            f"{len(seat.stations)} stations were read; the rest could not be read with certainty and are left out. "
            "No column of the stations read exceeds the totals the form prints."
        )
    else:
        coverage = (
            f"{len(seat.stations)} stations were read; the rest could not be read with certainty and are left out. "
            "No column of the stations read exceeds the candidate's Form 49 total."
        )
    p.add(
        "note",
        id=key("note", rs),
        entity_id=rs,
        kind="methodology",
        body_md=METHOD.format(order=seat.order, coverage=coverage),
        lang="en",
    )
    stored = p.artifact(seat.artifact, SOURCE_ID)
    pages: dict[int, str] = {}
    for n in sorted({s.page for s in seat.stations}):
        image = PAGES / f"{seat.artifact.sha256}-{n}.jpg"
        width, height = Image.open(image).size
        pages[n] = key("page", seat.artifact.sha256, n)
        p.add(
            "source_page",
            id=pages[n],
            artifact_id=stored,
            page_number=n,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )
    for s in seat.stations:
        unit = key("unit", STAMP, seat.code, s.serial)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ecp.PAKISTAN,
            scheme_code=STATION,
            code=f"{STAMP}:{seat.code}:{s.serial}",
            parent_id=parent,
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, f"{seat.code} polling station {s.serial}", "en", "official")
        cells = [*((options[j], VOTES) for j in range(k)), (None, VALID), (None, INVALID), (None, CAST)]
        for (option, measure), value, box in zip(cells, s.figures, s.boxes, strict=True):
            fact = {"result_set_id": rs, "unit_id": unit, "option_id": option, "measure": measure}
            values.append({**fact, "value": value})
            locators.append({**fact, "page_id": pages[s.page], "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]})


def packages() -> list[str]:
    return [KEY]


def emit() -> None:
    package.run("load", packages())


def write() -> Path:
    found, left = seats()
    return build(found, left).write()
