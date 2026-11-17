"""Reads the polling-station tables of the scanned reports of Jamaica's parliamentary elections before 2002.

The reports have no text layer. Each page is rendered at 300 dpi, straightened, and stripped of its ruling lines; each
column of the table is then read on its own with Tesseract. Rows are anchored on the polling-division numbers in the
first column, and each figure belongs to the row whose number is printed at or above it, because the typewritten
reports print the figures on the last line of a station whose address takes two. Every row is checked against its own
total (the candidates' votes and the rejected ballots add up to the total votes), and every constituency against the
totals of the summary tables. `ea.sources.jm.boxes` keeps only the constituencies that pass.

This module is a tool that writes the transcriptions in `boxes/`; the packages read those files, not the scans.
"""

import csv
import io
import itertools
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

DPI = 300
WHITELIST = "0123456789-%"


@dataclass
class Token:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    conf: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Page:
    number: int
    image: Image.Image
    angle: float
    width: int
    height: int
    rules: list[int]
    title: str
    tokens: list[Token] = field(default_factory=list)


def render(pdf: Path, page: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(pdf), f"{folder}/p"],
            check=True,
        )
        return Image.open(f"{folder}/p.png").convert("L")


def skew(image: Image.Image) -> float:
    small = (np.asarray(image.resize((image.width // 5, image.height // 5))) < 160).astype(np.uint8)

    def score(step: float) -> float:
        rotated = ndimage.rotate(small, step, reshape=False, order=0)
        return float(np.var(rotated.sum(axis=1).astype(float)))

    coarse = max((float(a) for a in np.arange(-3.0, 3.01, 0.5)), key=score)
    fine = max((coarse + float(a) for a in np.arange(-0.4, 0.41, 0.1)), key=score)
    return round(fine, 1)


def clean(image: Image.Image) -> tuple[Image.Image, np.ndarray]:
    pixels = np.asarray(image)
    dark = pixels < 170
    vertical = _runs(dark, 70, 0)
    horizontal = _runs(dark, 150, 1)
    lines = ndimage.binary_dilation(vertical | horizontal, iterations=2)
    return Image.fromarray(np.where(lines, 255, pixels).astype(np.uint8)), vertical


def _runs(dark: np.ndarray, length: int, axis: int) -> np.ndarray:
    """The pixels that belong to an unbroken dark run of at least `length` along `axis`."""
    padded = np.concatenate(
        [np.zeros((1, dark.shape[1]) if axis == 0 else (dark.shape[0], 1), np.int32), dark.astype(np.int32)], axis=axis
    )
    total = np.cumsum(padded, axis=axis, dtype=np.int32)
    if axis == 0:
        full = (total[length:] - total[:-length]) == length
        out = np.zeros(dark.shape, np.int32)
        cover = np.cumsum(np.concatenate([full.astype(np.int32), np.zeros((length, dark.shape[1]), np.int32)]), axis=0)
        shifted = np.concatenate([np.zeros((length, dark.shape[1]), np.int32), cover[:-length]])
        out = cover - shifted
    else:
        full = (total[:, length:] - total[:, :-length]) == length
        cover = np.cumsum(
            np.concatenate([full.astype(np.int32), np.zeros((dark.shape[0], length), np.int32)], axis=1), axis=1
        )
        shifted = np.concatenate([np.zeros((dark.shape[0], length), np.int32), cover[:, :-length]], axis=1)
        out = cover - shifted
    return out[: dark.shape[0], : dark.shape[1]] > 0


def rules(vertical: np.ndarray, top: int, bottom: int) -> list[int]:
    share = vertical[top:bottom].mean(axis=0)
    groups: list[list[int]] = []
    for x in np.flatnonzero(share > 0.05):
        if groups and x - groups[-1][-1] <= 10:
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    return [round(sum(g) / len(g)) for g in groups]


def tesseract(image: Image.Image, psm: str, whitelist: str | None) -> list[Token]:
    with tempfile.TemporaryDirectory() as folder:
        image.save(f"{folder}/c.png")
        command = ["tesseract", f"{folder}/c.png", "stdout", "--psm", psm, "-l", "eng"]
        if whitelist:
            command += ["-c", f"tessedit_char_whitelist={whitelist}"]
        out = subprocess.run([*command, "tsv"], capture_output=True, text=True, check=True).stdout
    return [
        Token(
            row["text"].strip(),
            int(row["left"]),
            int(row["top"]),
            int(row["left"]) + int(row["width"]),
            int(row["top"]) + int(row["height"]),
            float(row["conf"]),
        )
        for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if row["level"] == "5" and row["text"].strip()
    ]


def strip(image: Image.Image, x0: int, x1: int, y0: int, y1: int, psm: str, whitelist: str | None) -> list[Token]:
    """Reads one vertical strip of the page and returns its words in page coordinates."""
    crop = image.crop((x0, y0, x1, y1))
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    return [
        Token(t.text, t.x0 - 20 + x0, t.y0 - 20 + y0, t.x1 - 20 + x0, t.y1 - 20 + y0, t.conf)
        for t in tesseract(padded, psm, whitelist)
    ]


def load(pdf: Path, number: int) -> Page:
    image = render(pdf, number)
    angle = skew(image)
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    cleaned, vertical = clean(image)
    found = rules(vertical, 0, image.height)
    title = " ".join(t.text for t in tesseract(cleaned.crop((0, 0, image.width, image.height // 7)), "6", None))
    return Page(number, cleaned, angle, image.width, image.height, found, title)


def cell(image: Image.Image, box: tuple[int, int, int, int], scale: int, psm: str) -> str:
    """Reads one cell again, enlarged, as a second opinion."""
    crop = image.crop(box)
    crop = crop.resize((max(1, crop.width * scale), max(1, crop.height * scale)), Image.Resampling.LANCZOS)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    return "".join(t.text for t in tesseract(padded, psm, WHITELIST))


NUMBER = re.compile(r"^-+$|^\d+%?$")


def value(text: str) -> int | None:
    text = text.strip(".,'")
    if re.fullmatch(r"-+|—", text):
        return 0
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


@dataclass
class Row:
    pd: str
    ps: str
    y0: int
    y1: int
    cells: list[Token | None]
    total_row: bool = False


@dataclass
class Table:
    page: int
    title: str
    columns: list[tuple[int, int]]
    rows: list[Row]
    percent: bool


STATION = re.compile(r"^(\d{1,3})([A-Z]{0,2})$")


def _fix(text: str) -> str:
    return text.strip(".,:;'`‘’\"|!_()[]").replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")


def numeric(t: Token) -> bool:
    return bool(re.fullmatch(r"\d+%?|-+|—|–", t.text.strip(".,'`:;")))


def clusters(edges: list[float], gap: float) -> list[list[float]]:
    out: list[list[float]] = []
    for x in sorted(edges):
        if out and x - out[-1][-1] <= gap:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def _division(text: str) -> int:
    found = STATION.match(_fix(text).upper())
    return int(found[1]) if found else 0


def lines(words: list[Token]) -> list[list[Token]]:
    out: list[list[Token]] = []
    for w in sorted(words, key=lambda w: w.yc):
        if out and abs(w.yc - float(np.mean([t.yc for t in out[-1]]))) < 18:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w.x0) for line in out]


def anchors(page: Page) -> tuple[list[tuple[Token, Token | None]], list[Token]]:
    """The polling-division and polling-station numbers down the left of the table, and any totals label."""
    image = page.image
    top = image.height // 12
    words = strip(image, 0, int(image.width * 0.25), top, image.height - 40, "6", None)
    totals = [w for w in words if "TOTAL" in w.text.upper()]
    found: list[tuple[Token, Token | None]] = []
    rows = [[w for w in line if STATION.match(_fix(w.text).upper()) and w.conf > 30] for line in lines(words)]
    # The division and station numbers are two columns side by side, aligned on their left or their right edges;
    # marks showing through from the facing page at the edge of the scan are not.
    best: tuple[int, list[float], list[float] | None, bool] | None = None
    for right in (False, True):
        edges = [float(w.x1 if right else w.x0) for line in rows for w in line]
        groups = [g for g in clusters(edges, 15) if len(g) >= 3 and g[-1] - g[0] <= 45]
        for a, b in itertools.combinations(groups, 2):
            if 60 <= float(np.median(b)) - float(np.median(a)) <= 320 and (
                best is None or min(len(a), len(b)) > best[0]
            ):
                best = (min(len(a), len(b)), a, b, right)
        if best is None and groups:
            biggest = max(groups, key=len)
            best = (0, biggest, None, right)
    if best is None:
        return [], totals
    _, first, second, right = best

    def edge(w: Token) -> int:
        return w.x1 if right else w.x0

    for line in rows:
        pd = next((w for w in line if first[0] - 20 <= edge(w) <= first[-1] + 20), None)
        if pd is None:
            continue
        ps = next((w for w in line if second and second[0] - 20 <= edge(w) <= second[-1] + 20), None)
        found.append((pd, ps))
    found = _narrow(image, found, top) or found
    # Drops the readings of the rotated column headings: the polling divisions never decrease down a table.
    numbers = [_division(pd.text) for pd, _ in found]
    best: list[int] = []
    for i in range(len(found)):
        run = [i]
        for j in range(i + 1, len(found)):
            if numbers[j] >= numbers[run[-1]] and numbers[j] - numbers[run[-1]] <= 3:
                run.append(j)
        if len(run) > len(best):
            best = run
    return [found[i] for i in best], totals


def _narrow(image: Image.Image, found: list[tuple[Token, Token | None]], top: int) -> list[tuple[Token, Token | None]]:
    """Reads the code columns again on their own, which recovers codes the wide reading missed."""
    if len(found) < 3:
        return []
    pd0 = int(np.percentile([pd.x0 for pd, _ in found], 10)) - 25
    pd1 = int(np.percentile([pd.x1 for pd, _ in found], 90)) + 15
    with_ps = [ps for _, ps in found if ps is not None]
    pds = [w for w in strip(image, max(0, pd0), pd1, top, image.height - 40, "6", "0123456789") if w.conf > 20]
    pds = [w for w in pds if re.fullmatch(r"\d{1,3}", w.text)]
    if len(with_ps) < len(found) * 0.5:
        return [(w, None) for w in pds]
    ps0 = max(pd1, int(np.percentile([w.x0 for w in with_ps], 10)) - 15)
    ps1 = int(np.percentile([w.x1 for w in with_ps], 75)) + 8
    if ps1 - ps0 < 20:
        return [(w, None) for w in pds]
    pss = [w for w in strip(image, ps0, ps1, top, image.height - 40, "6", "0123456789ABCDEFGH") if w.conf > 20]
    pss = [w for w in pss if STATION.match(w.text)]
    out: list[tuple[Token, Token | None]] = []
    used: set[int] = set()
    for ps in pss:
        near = [w for w in pds if abs(w.yc - ps.yc) < 20 and id(w) not in used]
        if near:
            pd = min(near, key=lambda w: abs(w.yc - ps.yc))
            used.add(id(pd))
            out.append((pd, ps))
        else:
            out.append((Token(re.sub(r"[A-Z]+$", "", ps.text), ps.x0, ps.y0, ps.x1, ps.y1, -1), ps))
    for pd in pds:
        if id(pd) not in used and not any(abs(pd.yc - ps.yc) < 20 for _, ps in out if ps):
            out.append((pd, None))
    return sorted(out, key=lambda pair: pair[0].yc)


def segments(image: Image.Image, x0: int, y0: int, y1: int) -> list[tuple[int, int]]:
    """The columns of figures right of `x0`, from the white gaps between them."""
    ink = (np.asarray(image)[y0:y1, x0:] < 128).sum(axis=0).astype(float)
    ink = np.convolve(ink, np.ones(3) / 3, mode="same")
    solid = ink > max(2.0, (y1 - y0) * 0.004)
    runs: list[list[int]] = []
    for x in np.flatnonzero(solid):
        if runs and x - runs[-1][1] <= 14:
            runs[-1][1] = int(x)
        else:
            runs.append([int(x), int(x)])
    dark = np.asarray(image)[y0:y1, x0:] < 128
    out = []
    for a, b in runs:
        tall = int(dark[:, a : b + 1].any(axis=1).sum())
        if tall >= (y1 - y0) * 0.08 and b - a >= 8:
            out.append((a + x0, b + x0))
    return out


def table(page: Page, expected: int, share: bool | None = None) -> Table | None:
    """Reads the station rows of one page, with `expected` numeric columns right of the locations."""
    image = page.image
    keys, totals = anchors(page)
    if len(keys) < 2:
        return None
    height = float(np.median([pd.y1 - pd.y0 for pd, _ in keys]))
    ys = [pd.yc for pd, _ in keys]
    pitch = float(np.median(np.diff(ys))) if len(ys) > 2 else height * 2
    # A first or last station whose number the reading missed still has its figures read; rows outside the numbers
    # read are kept only if they balance (see `edge`).
    first, last = keys[0][0].y0, keys[-1][0].y1
    del first
    top = page.height // 12
    label = min((t.y0 for t in totals if t.y0 > keys[0][0].y0), default=None)
    bottom = int(
        min(max(last + height * 3.2, last + pitch * 6), label + height * 2.5 if label else 1e9, page.height - 40)
    )
    body = int(max(top, keys[0][0].y0 - height))
    spans = []
    for x0, x1 in segments(image, int(image.width * 0.3), body, bottom):
        cuts = [r for r in page.rules if x0 + 15 < r < x1 - 15]
        edges = [x0, *cuts, x1]
        spans += [(a, b) for a, b in itertools.pairwise(edges) if b - a > 15]
    percent = bool(share)
    if share is None and len(spans) >= expected + 1:
        before = spans[-2]
        words = strip(image, before[0] - 10, before[1] + 10, body, bottom, "6", None)
        percent = sum(1 for w in words if "%" in w.text) >= 2
    want = expected + (1 if percent else 0)
    if len(spans) < want:
        return None
    spans = spans[-want:]
    bounds: list[tuple[int, int]] = []
    ruled = _ruled(page, spans, want)
    if ruled is not None:
        # The ruling lines of the table, where the scan keeps them, bound the columns better than the gaps do, and
        # they keep a column that is blank all down a page.
        return _rows(page, keys, totals, height, top, bottom, label, ruled, percent)
    clustered = _clustered(image, body, bottom, want, len(keys))
    if clustered is not None:
        spans = clustered
    for i, (x0, x1) in enumerate(spans):
        left = (spans[i - 1][1] + x0) // 2 if i else x0 - 30
        right = (x1 + spans[i + 1][0]) // 2 if i + 1 < len(spans) else min(image.width - 5, x1 + 30)
        bounds.append((max(0, left), right))
    return _rows(page, keys, totals, height, top, bottom, label, bounds, percent)


def _clustered(image: Image.Image, top: int, bottom: int, want: int, rows: int) -> list[tuple[int, int]] | None:
    """The columns of figures from the right edges of the numbers read across the table, which line up."""
    words = [w for w in strip(image, int(image.width * 0.3), image.width - 5, top, bottom, "6", None) if numeric(w)]
    groups = [g for g in clusters([float(w.x1) for w in words], 45) if len(g) >= max(3, rows * 0.3)]
    if len(groups) < want:
        return None
    spans = []
    for g in groups[-want:]:
        members = [w for w in words if g[0] <= w.x1 <= g[-1]]
        spans.append((min(w.x0 for w in members) - 10, max(w.x1 for w in members) + 10))
    if any(b[0] <= a[1] for a, b in itertools.pairwise(spans)):
        return None
    return spans


def _ruled(page: Page, spans: list[tuple[int, int]], want: int) -> list[tuple[int, int]] | None:
    ruled = [r for r in page.rules if r > page.width * 0.4]
    if len(ruled) < 2:
        return None
    widths = [b - a for a, b in itertools.pairwise(ruled)]
    if min(widths) < 90:
        return None
    pitch = float(np.median(widths))
    intervals = list(itertools.pairwise(ruled))
    right = any(x1 > ruled[-1] + 20 for _, x1 in spans)
    if right:
        intervals.append((ruled[-1], int(min(page.width - 5, ruled[-1] + pitch * 1.3))))
    if len(intervals) == want - 1:
        intervals.insert(0, (int(ruled[0] - pitch), ruled[0]))
    if len(intervals) != want:
        return None
    return [(a + 5, b - 5) for a, b in intervals]


def _rows(
    page: Page,
    keys: list[tuple[Token, Token | None]],
    totals: list[Token],
    height: float,
    top: int,
    bottom: int,
    label: int | None,
    bounds: list[tuple[int, int]],
    percent: bool,
) -> Table:
    image = page.image
    columns = [[w for w in strip(image, x0, x1, top, bottom, "6", WHITELIST) if numeric(w)] for x0, x1 in bounds]
    total_column = len(columns) - (3 if percent else 2)
    # A row of figures is a line where at least two columns print a figure; the typewritten reports print every
    # figure of a station on one line.
    marks = sorted((w.yc, k) for k, column in enumerate(columns) for w in column)
    groups: list[list[tuple[float, int]]] = []
    for y, k in marks:
        if groups and y - groups[-1][0][0] < height * 0.6:
            groups[-1].append((y, k))
        else:
            groups.append([(y, k)])
    lines_y = [float(np.median([y for y, _ in g])) for g in groups if len({k for _, k in g}) >= 2]
    del total_column
    rows: list[Row] = []
    previous = top
    for y in lines_y:
        cells: list[Token | None] = []
        for column in columns:
            inside = [w for w in column if abs(w.yc - y) < height * 0.55]
            cells.append(_join(inside) if inside else None)
        if sum(c is not None for c in cells) < max(2, len(cells) - 3):
            continue
        is_total = label is not None and y > label - height * 0.3
        codes = [pair for pair in keys if previous - 2 < pair[0].yc <= y + height * 0.6]
        if codes:
            pd, ps = codes[0]
            code = _fix(pd.text).upper()
            rows.append(Row(code, _fix(ps.text).upper() if ps else code, int(y - height), int(y + height), cells))
        else:
            rows.append(Row("?", "?", int(y - height), int(y + height), cells))
        if is_total:
            rows[-1].pd, rows[-1].ps, rows[-1].total_row = "", "TOTAL", True
        previous = int(y + height * 0.6)
    return Table(page.number, page.title, bounds, rows, percent)


def _join(tokens: list[Token]) -> Token:
    """Several readings in one cell: kept as one token marked with a bar so the row fails its check."""
    if len(tokens) == 1:
        return tokens[0]
    return Token(
        "|".join(t.text for t in tokens),
        min(t.x0 for t in tokens),
        min(t.y0 for t in tokens),
        max(t.x1 for t in tokens),
        max(t.y1 for t in tokens),
        min(t.conf for t in tokens),
    )


def match(title: str, names: list[str]) -> str | None:
    """The constituency a page heading names, if it names one."""
    import difflib

    def norm(text: str) -> str:
        text = re.sub(r"[^A-Z& ]", " ", text.upper().replace("ST.", "ST ").replace("ST,", "ST "))
        drop = {"CONSTITUENCY", "OF", "CONT", "D", "CONTD", "GENERAL", "ELECTION", "ELECTIONS", "THE"}
        return " ".join(w for w in text.split() if w not in drop)

    heading = norm(title)[:160]
    best, score = None, 0.0
    for name in names:
        target = norm(name).replace(" AND ", " & ")
        for candidate in {target, target.replace(" & ", " AND ")}:
            size = len(candidate)
            for start in range(max(1, len(heading) - size + 1)):
                matcher = difflib.SequenceMatcher(None, candidate, heading[start : start + size])
                if matcher.real_quick_ratio() <= score or matcher.quick_ratio() <= score:
                    continue
                ratio = matcher.ratio()
                if ratio > score:
                    best, score = name, ratio
    return best if score >= 0.82 else None
