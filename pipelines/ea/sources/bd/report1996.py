"""The Bangladesh Election Commission's statistical report of the 12 June 1996 general election: an English scan
whose chapter 6 prints each constituency's candidates in a ruled table. Each page is cleaned of its rules, read
whole with Tesseract for the words, and then read cell by cell for the numbers, which are reconciled against the
percentages and totals the report prints beside them."""

import csv
import functools
import io
import itertools
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from ea.store import DATA

DPI = 300
# The PDF pages of chapter 6, "Detailed Results as Described in the Result Sheet furnished by the Returning Officer".
FIRST, LAST = 164, 299
CACHE = DATA / "scan" / "bd.ecs_parliament" / "1996"
# Cells read by eye from the page image, where the scan is too damaged for OCR to settle a number. Each entry is
# keyed by constituency number and names the cell, the value, and the reason.
OVERRIDES = Path(__file__).with_name("report1996_overrides.json")

# Tesseract on one thread: several run at once, and its own threads then only contend.
SINGLE = {**os.environ, "OMP_THREAD_LIMIT": "1"}

type Box = tuple[int, int, int, int]


@dataclass
class Word:
    text: str
    box: Box
    line: tuple[int, int, int]


@dataclass
class Row:
    name: str
    party: str
    symbol: str
    choices: list[int]  # what the vote cell could read, most likely first
    percents: list[float]
    page: int
    box: Box  # the vote cell, in pixels at 300 dpi
    share: Box  # the percentage cell
    spans: list[Box] = field(default_factory=list)  # the name, party, and symbol cells


@dataclass
class Table:
    number: int
    name: str
    page: int
    rows: list[Row] = field(default_factory=list)
    registered: int = 0
    valid: int = 0
    invalid: int = 0
    cast: int = 0
    turnout: float | None = None
    boxes: dict[str, tuple[int, Box]] = field(default_factory=dict)  # measure -> (page, box)
    reads: dict[str, list[int]] = field(default_factory=dict)
    printed: int | None = None
    shares: list[float] = field(
        default_factory=list
    )  # what the printed turnout percentage could read  # the number as OCR reads it, to check against the order of the tables
    footnote: str | None = None  # "decisive" or "repoll", from the table's footnote


def render(pdf: Path, page: int) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"p{page}.png"
    if not out.exists():
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf)]
            + [str(out.with_suffix(""))],
            check=True,
        )
    return out


def runs(dark: np.ndarray, length: int) -> np.ndarray:
    """The pixels that lie on a horizontal run of ink at least `length` long."""
    padded = np.pad(dark, ((0, 0), (1, 0))).astype(np.int32)
    total = np.cumsum(padded, axis=1)
    full = (total[:, length:] - total[:, :-length]) == length  # a run starts at each such column
    marks = np.zeros((dark.shape[0], dark.shape[1] + 1), np.int32)
    marks[:, : full.shape[1]] += full
    marks[:, length : length + full.shape[1]] -= full
    return np.cumsum(marks, axis=1)[:, : dark.shape[1]] > 0


def clean(page: Path) -> Path:
    """The page with its rules, dashes, and stray marks taken out, so that Tesseract sees only the printed text."""
    out = page.with_name(f"c{page.name}")
    if out.exists():
        return out
    image = np.asarray(Image.open(page).convert("L"))
    dark = image < 160
    vertical = runs(dark.T, 42).T
    horizontal = runs(dark, 45)
    dark &= ~ndimage.binary_dilation(vertical | horizontal, structure=np.ones((3, 3), bool))
    labels, count = ndimage.label(dark, structure=np.ones((3, 3)))
    drop = np.zeros(count + 1, bool)
    for index, found in enumerate(ndimage.find_objects(labels), start=1):
        if found is None:
            continue
        height, width = found[0].stop - found[0].start, found[1].stop - found[1].start
        if (height <= 8 and width >= 12) or (height <= 3 and width >= 5):
            drop[index] = True
    Image.fromarray(np.where(dark & ~drop[labels], 0, 255).astype(np.uint8)).save(out)
    return out


def words(page: Path) -> list[Word]:
    cache = page.with_suffix(".tsv")
    if not cache.exists():
        cache.write_text(
            subprocess.run(
                ["tesseract", str(page), "stdout", "--psm", "6", "-l", "eng", "tsv"],
                capture_output=True,
                text=True,
                check=True,
                env=SINGLE,
            ).stdout
        )
    return [
        Word(
            row["text"].strip(),
            (
                int(row["left"]),
                int(row["top"]),
                int(row["left"]) + int(row["width"]),
                int(row["top"]) + int(row["height"]),
            ),
            (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
        )
        for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
        if row["level"] == "5" and row["text"].strip()
    ]


def lines(found: list[Word]) -> list[list[Word]]:
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for word in found:
        grouped.setdefault(word.line, []).append(word)
    out = [sorted(line, key=lambda w: w.box[0]) for line in grouped.values()]
    return sorted(out, key=lambda line: sum(w.box[1] + w.box[3] for w in line) / len(line))


def integers(reads: list[str]) -> list[int]:
    """Every whole number the reads of one cell could be, most likely first."""
    found: list[int] = []
    for text in reads:
        digits = re.sub(r"\D", "", text)
        if digits and int(digits) not in found:
            found.append(int(digits))
    return found


def percents(reads: list[str]) -> list[float]:
    """Every percentage the reads of one cell could be, most likely first. The report prints two decimals; where the
    point is lost, or a piece of the percent sign is read as a digit, each way of reading the digits is kept."""
    found: list[float] = []

    def add(value: float) -> None:
        if value <= 100 and value not in found:
            found.append(value)

    for text in reads:
        if "." in text:
            whole, _, rest = text.partition(".")
            whole, rest = re.sub(r"\D", "", whole), re.sub(r"\D", "", rest)
            if len(rest) >= 2:
                add(float(f"{whole or 0}.{rest[:2]}"))
                continue
        digits = re.sub(r"\D", "", text)
        for drop in range(3):
            kept = digits[: len(digits) - drop]
            if len(kept) >= 3:
                add(float(f"{kept[:-2]}.{kept[-2:]}"))
    return found


def numbers(pdf: Path, tables: list[Table]) -> None:
    """Read every number of the tables from their pages."""
    pages: dict[int, np.ndarray] = {}
    path = CACHE / "reads.json"
    cache: dict[str, list[str]] = json.loads(path.read_text()) if path.exists() else {}

    def read_cached(page: int, box: Box) -> list[str]:
        key = f"{page}:{','.join(map(str, box))}"
        if key not in cache:
            cache[key] = read(pages[page], box)
        return cache[key]

    for table in tables:
        for page in {table.page, *(row.page for row in table.rows)}:
            if page not in pages:
                pages[page] = np.asarray(Image.open(render(pdf, page)).convert("L"))
        for row in table.rows:
            row.choices = integers(read_cached(row.page, row.box))
            row.percents = percents(read_cached(row.page, row.share))
        # A band with no number in it is a stray mark, and a first band with no share is the column heads.
        table.rows = [row for row in table.rows if row.choices or row.percents]
        if table.rows and not table.rows[0].percents and len(re.sub(r"[^A-Za-z]", "", table.rows[0].name)) < 4:
            table.rows = table.rows[1:]
        for measure, (page, box) in table.boxes.items():
            reads = read_cached(page, box)
            if measure == "turnout":
                table.turnout = next(iter(percents(reads)), None)
                table.shares = percents(reads)
            else:
                table.reads[measure] = integers(reads)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))


NUMBER = re.compile(r"Const\S*\s*N\S*\s*[:;.,]?\s*(\d{1,3})\b\s*(.*?)\s*T\w+\s*V\w+", re.IGNORECASE)


def joined(line: list[Word]) -> str:
    return " ".join(w.text for w in line)


def band_rows(image: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    """The rows of text between y0 and y1, from where the columns x0 to x1 hold ink."""
    ink = (image[y0:y1, x0:x1] < 128).sum(axis=1)
    rows: list[tuple[int, int]] = []
    start = None
    for y, count in enumerate(ink):
        if count >= 3 and start is None:
            start = y
        elif count < 3 and start is not None:
            if y - start >= 14:
                rows.append((y0 + start, y0 + y))
            start = None
    if start is not None and y1 - y0 - start >= 14:
        rows.append((y0 + start, y1))
    return rows


def tables(pdf: Path) -> list[Table]:
    """Every constituency's table in chapter 6, in the order printed, which is the order of their numbers."""
    found: list[Table] = []
    for number in range(FIRST, LAST + 1):
        found.extend(page_tables(pdf, number))
    for index, table in enumerate(found, start=1):
        table.number = index
    numbers(pdf, found)
    return found


def page_tables(pdf: Path, number: int) -> list[Table]:
    page = render(pdf, number)
    cleaned = clean(page)
    image = np.asarray(Image.open(cleaned).convert("L"))
    raw = np.asarray(Image.open(page).convert("L"))
    all_lines = lines(words(cleaned))
    heads = [line for line in all_lines if is_head(line)]
    out: list[Table] = []
    for k, head in enumerate(heads):
        top = min(w.box[1] for w in head)
        end = min(w.box[1] for w in heads[k + 1]) - 10 if k + 1 < len(heads) else FOOTER
        block = [line for line in all_lines if top <= min(w.box[1] for w in line) < end]
        out.append(read_table(head, block, image, number, end, raw))
    return out


# Where the running footer of each page begins, below the last table.
FOOTER = 3300


def is_head(line: list[Word]) -> bool:
    """The first line of a constituency's table, "Const. No: 22  RANGPUR-4 ... Total Voters 225648"."""
    right = [w.text.strip(".,:;'‘“") for w in line[1:] if w.box[0] > 1200]
    return (
        line[0].box[0] < 500
        and any(similar(t, "Voters") >= 0.7 for t in right)
        and any(similar(t, "Total") >= 0.6 for t in right)
    )


def vertical_bands(image: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    return [b for b in band_rows(image, x0, x1, y0, y1) if b[1] - b[0] >= 16]


PITCH = 53  # the distance between two rows of a table, in pixels at 300 dpi


def regular(
    bands: list[tuple[int, int]], image: np.ndarray, columns: tuple[int, int], top: int, end: int
) -> list[tuple[int, int]]:
    """The rows of a table from the bands of ink in its number columns. The rows sit on a fixed pitch, so a band two
    rows tall is split, a row the scan left too faint to find is put back where the pitch says it is, and inked rows
    just above or below the bands found are added."""
    height = 24  # the digits' height, with a little room
    diffs = sorted(b[0] - a[0] for a, b in itertools.pairwise(bands) if 40 <= b[0] - a[0] <= 70)
    pitch = diffs[len(diffs) // 2] if diffs else PITCH
    centers: list[float] = []
    for b0, b1 in bands:
        count = 1 if b1 - b0 < pitch + 12 else round((b1 - b0 - height) / pitch) + 1
        start = (b0 + b1) / 2 - (count - 1) * pitch / 2
        centers.extend(start + k * pitch for k in range(count))
    filled: list[float] = []
    for center in centers:
        if filled:
            steps = round((center - filled[-1]) / pitch)
            if steps < 1:
                continue
            filled.extend(filled[-1] + k * (center - filled[-1]) / steps for k in range(1, steps))
        filled.append(center)

    def inked(center: float) -> bool:
        y0, y1 = round(center - height / 2), round(center + height / 2)
        if not (top <= y0 and y1 <= end):
            return False
        votes = int((image[y0:y1, columns[0] : columns[1] - 200] < 128).sum())
        shares = int((image[y0:y1, columns[1] - 115 : columns[1]] < 128).sum())
        return votes >= 30 and shares >= 30

    while inked(filled[0] - pitch):
        filled.insert(0, filled[0] - pitch)
    while inked(filled[-1] + pitch):
        filled.append(filled[-1] + pitch)
    half = height / 2 + 4
    return [(round(c - half), round(c + half)) for c in filled]


def read_table(
    head: list[Word], block: list[list[Word]], image: np.ndarray, page: int, end: int, raw: np.ndarray | None = None
) -> Table:
    # The left edge of "Const.", past any stray mark OCR reads before it.
    anchor = next((w.box[0] for w in head if len(re.sub(r"[^A-Za-z]", "", w.text)) >= 3), head[0].box[0])
    text = joined(head)
    number = re.search(r"N\S{0,2}\W*\s*(\d{1,3})", text)
    names = [w.text for w in head if re.search(r"[A-Za-z]{3,}.*[-.]\s*\d|[A-Z]{4,}", w.text)]
    table = Table(int(number[1]) if number else 0, names[0] if names else "", page)
    table.printed = int(number[1]) if number else None
    head_top = min(w.box[1] for w in head)
    head_bottom = max(w.box[3] for w in head)
    table.boxes["registered"] = (page, (anchor + 1495, head_top - 12, anchor + 1800, head_bottom + 12))
    name_zone = (max(anchor - 60, 0), anchor + 260)
    labels = vertical_bands(image, *name_zone, head_bottom + 20, end)
    if not labels:
        raise ValueError(f"1996 page {page}: nothing under constituency {text!r}")
    header = labels[0]
    body = [w for line in block for w in line if (w.box[1] + w.box[3]) / 2 > header[1] + 4]
    edges = sorted(w.box[2] for w in body if "%" in w.text and w.box[0] > anchor + 1700)
    right = edges[len(edges) // 2] if len(edges) >= 2 else anchor + 1940
    bands: list[tuple[int, int]] = []
    for band in vertical_bands(image, right - 430, right + 25, header[1] + 8, end):
        if int((image[band[0] : band[1], right - 90 : right + 25] < 128).sum()) < 20:
            continue  # no percent sign at the right edge: a footnote, not a row
        if bands and band[0] - bands[-1][1] > 120:
            break
        bands.append(band)
    if not bands:
        raise ValueError(f"1996 page {page}: no candidates under constituency {text!r}")
    bands = regular(bands, image, (right - 430, right + 25), header[1] + 8, end)
    # The column heads can sit lower than the name column's head, so the first band may be them: it has no share.
    if len(bands) > 1 and raw is not None:
        first, second = ((right - 175, b0 - 12, right + 30, b1 + 12) for b0, b1 in bands[:2])
        near = (bands[0][0] + bands[0][1]) / 2 - (header[0] + header[1]) / 2 < 45
        if near and not percents(read(raw, first)) and percents(read(raw, second)):
            bands = bands[1:]
    # A starred table carries a footnote: polling was suspended at some centres, and either the result was declared
    # decisive without them (Annex V) or they polled again on 19 June 1996 (Annex VI).
    notes = " ".join(joined(line) for line in block).lower()
    if "repoll" in notes or "re poll" in notes or "tookplace" in notes or "took place" in notes:
        table.footnote = "repoll"
    elif "decisive" in notes or "decisrve" in notes:
        table.footnote = "decisive"
    vote_boxes: list[Box] = [(right - 470, b0 - 12, right - 175, b1 + 12) for b0, b1 in bands]
    pct_boxes: list[Box] = [(right - 175, b0 - 12, right + 30, b1 + 12) for b0, b1 in bands]
    zones = ((anchor - 80, anchor + 590), (anchor + 590, anchor + 1060), (anchor + 1060, right - 470))
    texts: list[list[list[Word]]] = [[[] for _ in zones] for _ in bands]
    spans: list[list[Box]] = [[] for _ in bands]
    for z, (x0, x1) in enumerate(zones):
        own = vertical_bands(image, max(x0, 0), x1, header[1] + 8, bands[-1][1] + 10)
        rows = own if len(own) == len(bands) else bands
        for i, (b0, b1) in enumerate(rows):
            spans[i].append((max(x0, 0), b0 - 8, x1, b1 + 8))
        centers = [(b0 + b1) / 2 for b0, b1 in rows]
        for w in body:
            if not x0 <= (w.box[0] + w.box[2]) / 2 < x1:
                continue
            middle = (w.box[1] + w.box[3]) / 2
            nearest = min(range(len(centers)), key=lambda i: abs(centers[i] - middle))
            if abs(centers[nearest] - middle) <= 30:
                texts[nearest][z].append(w)
    for band_words, vote, share, span in zip(texts, vote_boxes, pct_boxes, spans, strict=True):
        name, party, symbol = (" ".join(w.text for w in zone) for zone in band_words)
        first = [w.text.strip("'\"‘“:;,.|!_") for w in band_words[0]] + [
            w.text.strip("'\"‘“:;,.|!_") for w in band_words[2]
        ]
        over = [w.text for w in body if w.box[0] >= vote[0] and w.box[2] <= share[2] and vote[1] <= w.box[1] <= vote[3]]
        if not table.rows and (
            any(similar(t, "Candidate") >= 0.75 or similar(t, "Symbol") >= 0.75 for t in first)
            or any(similar(t.strip("'\"‘“:;,.|!_"), "Vote") >= 0.75 for t in over)
        ):
            continue  # the column heads, set lower on the page than the first row's band
        table.rows.append(Row(name, party, symbol, [], [], page, vote, share, span))
    totals = [b for b in labels if b[0] > bands[-1][1] - 5][:4]
    for measure, (b0, b1) in zip(("valid", "invalid", "cast", "turnout"), totals, strict=False):
        table.boxes[measure] = (page, (anchor + 440, b0 - 12, anchor + 720, b1 + 12))
    if len(totals) < 4:
        raise ValueError(f"1996 page {page}: only {len(totals)} total lines under constituency {text!r}")
    return table


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def variants(n: int) -> set[int]:
    """The numbers one misread digit away from n: one digit changed, dropped, or added."""
    text = str(n)
    found: set[str] = set()
    for i in range(len(text) + 1):
        for d in "0123456789":
            found.add(text[:i] + d + text[i:])
        if i < len(text):
            found.add(text[:i] + text[i + 1 :])
            for d in "0123456789":
                found.add(text[:i] + d + text[i + 1 :])
    return {int(t) for t in found if t and not (len(t) > 1 and t[0] == "0")}


def hundredths(part: int, whole: int) -> int:
    """part / whole as a percentage in hundredths, rounded half up, as the report prints it."""
    return (part * 20000 + whole) // (2 * whole) if whole else -1


def score(value: int, reads: list[int], percents: list[float], valid: int) -> int:
    if reads and value == reads[0]:
        points = 6
    elif value in reads:
        points = 3
    else:
        points = 1 if any(value in variants(r) for r in reads[:3]) else 0
    printed = [round(p * 100) for p in percents]
    shown = hundredths(value, valid)
    if shown in printed:
        points += 4
    elif any(abs(shown - p) <= 1 for p in printed):
        points += 1
    return points


@dataclass
class Settled:
    votes: list[int]
    registered: int
    valid: int
    invalid: int
    cast: int
    problems: list[str]


def settle(table: Table, overrides: dict[str, int]) -> Settled:
    """Choose each number of a table from its reads so that the table adds up as printed."""
    problems: list[str] = []
    reads = table.reads
    rows_read = [k for k in overrides if k.startswith("row")]
    if rows_read and len(rows_read) != len(table.rows):
        problems.append(f"{len(rows_read)} candidates read by eye, but {len(table.rows)} rows found on the page")

    def options(measure: str) -> list[int]:
        if measure in overrides:
            return [overrides[measure]]
        first = reads.get(measure, [])
        # A read can carry a stray glyph or two from the label before the number.
        cut = {int(str(r)[k:]) for r in first[:2] for k in (1, 2) if len(str(r)) > k + 2}
        return first + sorted(({v for r in first[:2] for v in variants(r)} | cut) - set(first))

    def weight(measure: str, value: int) -> float:
        first = reads.get(measure, [])
        if measure in overrides:
            return 3.0
        if first and value == first[0]:
            return 2.0
        return 1.0 if value in first else 0.3

    # The valid votes are also the sum of the candidates' votes, and give each candidate its printed share.
    tops = [row.choices[0] if row.choices else 0 for row in table.rows]
    printed = [{round(p * 100) for p in row.percents} for row in table.rows]

    def fit(total: int) -> float:
        hits = sum(hundredths(v, total) in shares for v, shares in zip(tops, printed, strict=True))
        return 3.0 * hits / max(len(tops), 1) + (2.0 if sum(tops) == total else 0.0)

    shown = [round(p * 100) for p in table.shares]
    registers = options("registered")
    casts = set(options("cast"))
    best: tuple[float, int, int, int] | None = None
    for v in {*options("valid"), sum(tops)}:
        if v <= 0:
            continue
        valid_rank = weight("valid", v) + fit(v)
        for i in options("invalid"):
            c = v + i
            if c not in casts:
                continue
            rank = valid_rank + weight("invalid", i) + weight("cast", c)
            if any(hundredths(c, r) in shown for r in registers[:3]):
                rank += 1.0
            if best is None or rank > best[0]:
                best = (rank, v, i, c)
    if best is None or best[0] < 5:
        problems.append(f"valid, cancelled, and turnout do not add up: {reads}")
        valid = next(iter(options("valid")), 0)
        invalid = next(iter(options("invalid")), 0)
        cast = next(iter(options("cast")), 0)
    else:
        _, valid, invalid, cast = best
    registered = 0
    candidates = options("registered")
    fits = [r for r in candidates if hundredths(cast, r) in shown]
    exact = [r for r in fits if r in reads.get("registered", [])] or fits
    if "registered" in overrides:
        registered = overrides["registered"]
    elif exact:
        registered = exact[0]
    else:
        problems.append(f"registered voters do not give the printed turnout {table.turnout}: {reads.get('registered')}")
        registered = next(iter(candidates), 0)

    rows = table.rows
    pools: list[list[tuple[int, int]]] = []
    for index, row in enumerate(rows):
        key = f"row{index + 1}"
        if key in overrides:
            pools.append([(overrides[key], 99)])
            continue
        values = set(row.choices) | {v for r in row.choices[:3] for v in variants(r)}
        values |= {int(str(r)[k:]) for r in row.choices[:2] for k in (1, 2) if len(str(r)) > k + 1}
        ranked = sorted(((score(v, row.choices, row.percents, valid), v) for v in values), reverse=True)
        pools.append([(v, s) for s, v in ranked if s > 0][:12])
    chosen = [pool[0][0] if pool else 0 for pool in pools]
    if sum(chosen) != valid:
        chosen = repair(pools, chosen, valid, rows)
    if chosen is None or sum(chosen) != valid:
        problems.append("the candidates' votes do not add to the valid votes")
        chosen = [pool[0][0] if pool else 0 for pool in pools]
    else:
        for index, (value, row) in enumerate(zip(chosen, rows, strict=True)):
            if f"row{index + 1}" in overrides:
                continue
            shown = hundredths(value, valid)
            if row.percents and shown not in [round(p * 100) for p in row.percents] and value not in row.choices:
                problems.append(f"row {index + 1}: {value} matches neither a read nor the printed share")
    return Settled(chosen, registered, valid, invalid, cast, problems)


def repair(pools: list[list[tuple[int, int]]], chosen: list[int], valid: int, rows: list[Row]) -> list[int] | None:
    """Change one row, then two, so the votes add to the valid votes, preferring the best-scored change."""
    gap = valid - sum(chosen)
    found: list[tuple[int, list[int]]] = []
    for i, pool in enumerate(pools):
        base = pool[0][1] if pool else 0
        want = chosen[i] + gap
        for value, points in pool[1:]:
            if value == want:
                found.append((points - base, [*chosen[:i], value, *chosen[i + 1 :]]))
        if want >= 0 and rows[i].percents and hundredths(want, valid) in [round(p * 100) for p in rows[i].percents]:
            found.append((-base - 1, [*chosen[:i], want, *chosen[i + 1 :]]))
    if found:
        return max(found, key=lambda f: f[0])[1]
    for i in range(len(pools)):
        for j in range(i + 1, len(pools)):
            for vi, pi in pools[i][1:6]:
                for vj, pj in pools[j][1:6]:
                    if vi + vj == chosen[i] + chosen[j] + gap:
                        trial = list(chosen)
                        trial[i], trial[j] = vi, vj
                        found.append((pi + pj - pools[i][0][1] - pools[j][0][1], trial))
    if found:
        return max(found, key=lambda f: f[0])[1]
    return None


# ----------------------------------------------------------------------------------------------------------------
# Numbers: every cell's glyphs are cut out of the 300 dpi page and matched against templates of the report's own
# digits, which were labeled by eye from clusters of the glyphs themselves (report1996_glyphs.json).

GLYPHS = Path(__file__).with_name("report1996_glyphs.json")
SIZE = (16, 24)  # width, height of a normalized glyph


@dataclass
class Glyph:
    x0: int
    x1: int
    mask: np.ndarray  # the glyph's own pixels
    width: int
    height: int
    point: bool = False  # a decimal point


def ink(raw: np.ndarray, box: Box) -> np.ndarray:
    """The ink of one cell without the table's rules."""
    x0, y0, x1, y1 = box
    cell = raw[max(y0, 0) : y1, max(x0, 0) : x1]
    dark = cell < 150
    # A rule crosses the whole cell, and no run of digits fills more than a fraction of a row or a column of it, so
    # rows and columns that are mostly ink are rules. A rule leaning a pixel or two is caught on its neighbors too.
    height, width = dark.shape
    across, down = dark.sum(axis=1), dark.sum(axis=0)
    rows = across >= 0.6 * width
    columns = down >= 0.6 * height
    rows |= (np.roll(rows, 1) | np.roll(rows, -1)) & (across >= 0.3 * width)
    columns |= (np.roll(columns, 1) | np.roll(columns, -1)) & (down >= 0.4 * height)
    dark[rows, :] = False
    dark[:, columns] = False
    return dark


def cut(raw: np.ndarray, box: Box) -> list[Glyph]:
    """The glyphs of one cell, left to right."""
    dark = ink(raw, box)
    labels, _ = ndimage.label(dark, structure=np.ones((3, 3)))
    found = [(i, s) for i, s in enumerate(ndimage.find_objects(labels), start=1) if s is not None]
    sizes = [(s[0].stop - s[0].start, s[1].stop - s[1].start) for _, s in found]
    heights = [h for h, w in sizes if 15 <= h <= 40 and w >= 6]
    if not heights:
        return []
    height = float(np.median(heights))
    bottoms = [s[0].stop for (_, s), (h, _) in zip(found, sizes, strict=True) if abs(h - height) <= 4]
    base = float(np.median(bottoms))
    glyphs: list[Glyph] = []
    for (i, s), (h, w) in zip(found, sizes, strict=True):
        mask = labels[s] == i
        if abs(h - height) <= 5 and 3 <= w <= 140 and abs(s[0].stop - base) <= 6:
            glyphs.append(Glyph(s[1].start, s[1].stop, mask, w, h))
        elif h <= 10 and w <= 10 and mask.sum() >= 10 and abs(s[0].stop - base) <= 5:
            glyphs.append(Glyph(s[1].start, s[1].stop, mask, w, h, point=True))
    return sorted(glyphs, key=lambda g: g.x0)


def normalize(mask: np.ndarray) -> np.ndarray:
    image = Image.fromarray((mask * 255).astype(np.uint8)).resize(SIZE, Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float32) / 255


def features(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    return np.concatenate([normalize(mask).ravel(), [width / 14 * 3, height / 21 * 3]])


@functools.cache
def templates() -> tuple[np.ndarray, list[str]]:
    data = json.loads(GLYPHS.read_text())
    return np.array([t["vector"] for t in data["templates"]]), [t["char"] for t in data["templates"]]


def match(mask: np.ndarray) -> list[tuple[float, str]]:
    """The characters a glyph could be, nearest template first, with the distance to each."""
    vectors, chars = templates()
    distance = np.sqrt(((vectors - features(mask)) ** 2).sum(axis=1))
    best: dict[str, float] = {}
    for d, c in sorted(zip(distance.tolist(), chars, strict=True)):
        best.setdefault(c, d)
    return sorted((d, c) for c, d in best.items())


def split(mask: np.ndarray, last: bool) -> list[list[tuple[float, str]]]:
    """Read a run of touching glyphs: the cut into single glyphs whose parts match the templates best. Only the
    last glyph of a cell may be a percent sign."""
    width = mask.shape[1]
    whole = match(mask)
    if not last:
        whole = [m for m in whole if m[1] != "%"]
    if width <= 22:
        return [whole]
    inf = float("inf")
    cost = [0.0] + [inf] * width
    back: list[tuple[int, list[tuple[float, str]]] | None] = [None] * (width + 1)
    for end in range(1, width + 1):
        for start in range(max(0, end - 24), end - 4):
            if cost[start] == inf:
                continue
            part = trim(mask[:, start:end])
            if part is None:
                continue
            found = [m for m in match(part) if m[1] != "%" or (last and end == width)]
            total = cost[start] + found[0][0] + SPLIT
            if total < cost[end]:
                cost[end], back[end] = total, (start, found)
    if back[width] is None:
        return [whole]
    parts: list[list[tuple[float, str]]] = []
    end = width
    while end > 0:
        step = back[end]
        assert step is not None
        parts.append(step[1])
        end = step[0]
    return parts[::-1]


# What each cut costs, so that a glyph is not read as two narrower ones.
SPLIT = 1.5


def trim(mask: np.ndarray) -> np.ndarray | None:
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if len(rows) == 0 or len(cols) == 0 or mask.sum() < 15:
        return None
    return mask[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]


def read(raw: np.ndarray, box: Box) -> list[str]:
    """What one cell's digits could read, most likely first; a percentage keeps its point and stops at its sign."""
    slots: list[list[tuple[float, str]]] = []
    glyphs = cut(raw, box)
    # The numbers are right-aligned: a glyph well apart to the left of them is the tail of the next cell's text.
    if len(glyphs) > 1 and glyphs[1].x0 - glyphs[0].x1 > 30:
        glyphs = glyphs[1:]
    for index, glyph in enumerate(glyphs):
        if glyph.point:
            slots.append([(0.0, ".")])
            continue
        slots.extend(split(glyph.mask, index == len(glyphs) - 1))
    cells: list[list[tuple[float, str]]] = []
    for slot in slots:
        if slot[0][1] == "%":
            break
        cells.append(slot)
    best = "".join(slot[0][1] for slot in cells)
    found = [best]
    # The next likeliest readings: one glyph read as its runner-up, closest calls first.
    close = sorted(
        (slot[1][0] - slot[0][0], i, slot[1][1]) for i, slot in enumerate(cells) if len(slot) > 1 and slot[1][1] != "%"
    )
    for _, i, char in close[:4]:
        found.append(best[:i] + char + best[i + 1 :])
    return found


def text(pdf: Path, page: int, box: Box) -> str:
    """One cell's words read on their own, for a cell the page's OCR left out or ran into its neighbor."""
    image = Image.open(clean(render(pdf, page))).convert("L").crop(box)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return (
        subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng"],
            input=buffer.getvalue(),
            capture_output=True,
            check=True,
            env=SINGLE,
        )
        .stdout.decode()
        .strip()
    )
