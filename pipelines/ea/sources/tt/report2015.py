"""Reads the polling station tables in Appendix I of the commission's scanned report on the 2015 election.

The pages are laid out like the 2020 report's, scanned on their side, but the body of each table has no rules between
its rows, its figures are smaller and fainter, and the electorate of a polling division with several stations is
printed once, beside one of them. A page is turned upright and its column rules are found from long vertical runs of
dark pixels. Every row, station or total, prints its valid votes once, so the figures in that column anchor the rows;
a row with no station number, or whose label reads as a total, is a total row. Each figure's ink is found in its cell,
enlarged, and read by Tesseract as a line and as a word; the station number is read the same way.

The scan's thin 1s and bold one-digit figures defeat Tesseract often, so `augment` adds readings: a nearest-neighbour
reading of each one-digit figure, taught by the digits whose every reading agrees; a 1 for a figure too narrow to be
anything else; and, in the columns a row's sum checks, any digit for a lone digit no pass could read and thin 1s
restored up to the count of digits in the ink. `report2020.settle` then picks each cell's reading so each station's
candidate votes add up to its valid votes and each page carries the running totals printed at its foot and at the
head of the next, and `resolve` closes what is left against the district's totals. Cells, station numbers, and
candidate headings that no pass settles are read from the page by hand and recorded in `report2015.json`.
"""

import difflib
import itertools
import json
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from math import prod
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image, ImageOps

from ea import raw
from ea.sources.tt.report2020 import (
    CODES,
    DIGITS,
    _centre,
    _codes,
    _lines,
    _runs,
    _strip,
    _tesseract,
    _text,
    render,
)

VERSION = 5
OVERRIDES = Path(__file__).with_name("report2015.json")
# Half the height of a row's band of figures, in pixels of the page at 200 dpi.
HALF = 24


def _rules(dark: np.ndarray) -> tuple[list[int], int, int]:
    """The table's column rules, and the top and bottom of the table from where they start and end."""
    # A district's last page can hold a single row, under a header some 230 pixels tall.
    found = [line for line in _lines(dark.T, 60) if line[2] - line[1] > 300]
    found = [line for line in found if 10 < line[0] < dark.shape[1] - 10]
    if not found:
        return [], 0, 0
    bottom = int(np.median([line[2] for line in found]))
    top = min(line[1] for line in found)
    # A rule the scan breaks near the foot of the table still runs most of its height.
    columns = sorted(line[0] for line in found if line[2] - line[1] > (bottom - top) * 0.6)
    # A rule broken in the scan is found twice a few pixels apart.
    merged: list[int] = []
    for x in columns:
        if merged and x - merged[-1] < 25:
            continue
        merged.append(x)
    return merged, top, bottom


def _header_bottom(dark: np.ndarray, top: int, left: int, right: int) -> int:
    """The rule under the column headings: the lowest long horizontal rule near the top of the table."""
    for window, length in ((150, 300), (60, 120)):
        lines = [
            line
            for line in _lines(dark, window)
            if top + 120 < line[0] < top + 330 and line[1] < right and line[2] > left and line[2] - line[1] > length
        ]
        if lines:
            return max(line[0] for line in lines)
    # A rule too faint to find: the headings stand some 225 pixels tall on every page.
    return top + 225


def _unruled(dark: np.ndarray) -> np.ndarray:
    """A column's ink without its rules: lines of pixels mostly dark, and the two pixels either side of them.

    A scan turned slightly askew carries a column rule across several pixel columns, so rules are looked for in
    short slices of the column.
    """
    sub = dark.copy()
    if not sub.size:
        return sub
    for start in range(0, sub.shape[0], 40):
        piece = sub[start : start + 40]
        ruled = np.flatnonzero(piece.mean(axis=0) > 0.6)
        for d in range(-2, 3):
            piece[:, np.clip(ruled + d, 0, piece.shape[1] - 1)] = False
    ruled = np.flatnonzero(sub.mean(axis=1) > 0.45)
    for d in range(-2, 3):
        sub[np.clip(ruled + d, 0, sub.shape[0] - 1), :] = False
    return sub


def _blob(dark: np.ndarray, x0: int, x1: int, low: int, high: int, target: float) -> tuple[int, int, int, int] | None:
    """The box around the figure in one column between two heights nearest the target, without the ruling."""
    low, high = max(0, low), min(dark.shape[0], high)
    sub = _unruled(dark[low:high, x0 + 3 : x1 - 3])
    if not sub.size:
        return None
    runs = [(a + low, b + low) for a, b in _runs(np.flatnonzero(sub.sum(axis=1) > 0), gap=3) if 6 <= b - a <= 40]
    if not runs:
        return None
    a, b = min(runs, key=lambda run: abs((run[0] + run[1]) / 2 - target))
    columns = np.flatnonzero(sub[a - low : b - low + 1].any(axis=0))
    return (x0 + 3 + int(columns[0]) - 6, a - 6, x0 + 3 + int(columns[-1]) + 7, b + 7)


def _figures(dark: np.ndarray, x0: int, x1: int, top: int, bottom: int) -> list[float]:
    """The heights of the figures in one column: one for each row of the table."""
    sub = _unruled(dark[top:bottom, x0 + 3 : x1 - 3])
    return [top + (a + b) / 2 for a, b in _runs(np.flatnonzero(sub.sum(axis=1) > 1), gap=3) if 6 <= b - a <= 40]


def _read_cell(image: Image.Image, box: tuple[int, int, int, int], args: list[str] = DIGITS) -> list[str]:
    """A figure's ink enlarged three and four times, each read as a line and as a word."""
    texts = []
    for scale in (3, 4):
        crop = image.crop(box)
        crop = ImageOps.autocontrast(
            crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS), 1
        )
        padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        padded.paste(crop, (20, 20))
        modes = ("7", "8", "10") if box[2] - box[0] < 24 else ("7", "8")
        texts += [_tesseract(padded, ["--psm", psm, *args]).strip() for psm in modes]
    return texts


def _total_label(text: str, first: bool) -> str:
    """A total row's label, which the scan sometimes garbles: totals brought forward head a page, totals carried
    forward end every page of a district but its last, and its last ends with the district's totals."""
    if first:
        return "TOTALS BROUGHT FORWARD"
    words = re.findall(r"[A-Z]+", text.upper())
    if any(difflib.get_close_matches(word, ["CARRIED", "FORWARD"], n=1, cutoff=0.6) for word in words):
        return "TOTALS CARRIED FORWARD"
    return "TOTALS"


def read_page(pdf: Path, number: int, folder: Path) -> dict[str, object] | None:
    """One page's raw reading in the shape `report2020.draft` takes: its grid, title, header, and every cell."""
    path = render(pdf, number, folder)
    scanned = Image.open(path)
    image = scanned.convert("L").rotate(-90, expand=True)
    dark = np.asarray(image) < 170
    xs, top, bottom = _rules(dark)
    if len(xs) < 7:
        return None
    title = _text(image, (0, max(0, top - 300), image.width, top - 2))
    # The scan garbles a letter of the heading now and then (WLECTORAL, DISTRICT GF).
    title = re.sub(r"\b\w{0,2}ECT\wRAL\W+DIST\w*\W+\w{2}\b", "ELECTORAL DISTRICT OF", title.upper())
    if "ELECTORAL DISTRICT OF" not in title:
        return None
    header = _header_bottom(dark, top, xs[0], xs[-1]) + 10
    columns = list(itertools.pairwise(xs[2:]))
    names = [_text(image, (x0 + 3, top + 50, x1 - 3, header - 12)) for x0, x1 in columns[:-3]]
    # Every row, station or total, prints its valid votes once, beside the first line of its venue.
    heights = _figures(dark, *columns[-3], header, bottom - 3)
    with ThreadPoolExecutor(4) as pool:
        codes = pool.submit(_strip, image, (xs[0] + 3, header, xs[1] - 3, bottom - 3), CODES)
        venues = pool.submit(_strip, image, (xs[1] + 3, header, xs[2] - 3, bottom - 3), ["-l", "eng"])
        strips = [pool.submit(_strip, image, (x0 + 4, header, x1 - 4, bottom - 3), DIGITS) for x0, x1 in columns]
        words = venues.result()
        found = _codes(codes.result())
        jobs = []
        for position, centre in enumerate(heights):
            low = (heights[position - 1] + centre) / 2 if position else header
            high = (centre + heights[position + 1]) / 2 if position + 1 < len(heights) else bottom
            label = " ".join(t for t, b in words if abs(_centre(b) - centre) < 30)
            # The station number stands level with the venue's first line, a little below the figures; a total
            # row has none.
            spot = _blob(dark, xs[0], xs[1], int(max(low, centre - 30)), int(min(high, centre + 50)), centre + 15)
            if position == 0 and spot is not None:
                # The totals brought forward stand close above the first station, whose number can fall in their
                # window, so the first row's label is read on its own.
                label += " " + _text(image, (xs[1] + 3, int(centre) - 40, xs[2] - 3, int(centre) + 20), "7")
            total = spot is None or any(
                difflib.get_close_matches(word, ["TOTALS", "BROUGHT", "CARRIED", "FORWARD"], n=1, cutoff=0.7)
                # Short words such as CARE, in a venue's name, would pass for CARRIED.
                for word in re.findall(r"[A-Z]{5,}", label.upper())
            )
            near = []
            if not total and spot is not None:
                near += [code for code, _ in _codes([(t, (0, 0, 0, 0)) for t in _read_cell(image, spot, CODES)])]
            near += [code for code, b in found if low <= _centre(b) < high]
            # Polling divisions are numbered with four digits, and their stations with two more.
            near = [code for code in near if re.fullmatch(r"\*?\d{4}(-\d{2})?", code)] or near
            spots = []
            for x0, x1 in columns:
                box = _blob(dark, x0, x1, int(max(low, centre - 30)), int(min(high, centre + 30)), centre)
                spots.append(None if box is None else pool.submit(_read_cell, image, box))
            counted = Counter(near)
            code = max(counted, key=lambda c: (counted[c], -near.index(c))) if near else "?"
            jobs.append((total, label, code, centre, low, high, spots))
        bands: list[dict[str, object]] = []
        body: dict[str, object] | None = None
        figures = [s.result() for s in strips]
        for total, label, code, centre, low, high, spots in jobs:
            cells = [[] if spot is None else spot.result() for spot in spots]
            if total:
                label = _total_label(label, not bands)
                box = [int(centre) - HALF, int(centre) + HALF]
                bands.append({"kind": "total", "label": label, "box": box, "cells": cells})
                body = None
                continue
            if body is None:
                body = {"kind": "body", "box": [int(low), int(bottom)], "rows": [], "strips": figures, "codes": []}
                bands.append(body)
            venue = " ".join(t for t, b in words if centre - 20 <= _centre(b) < high)
            row = {"code": code, "centre": centre, "range": [low, high], "venue": venue, "cells": cells}
            cast(list[dict[str, object]], body["rows"]).append(row)
    return {
        "version": VERSION,
        "page": number,
        "width": scanned.width,
        "height": scanned.height,
        "upright_height": image.height,
        "title": title,
        "names": names,
        "xs": xs,
        "bands": bands,
    }


def _glyphs(dark: np.ndarray, box: tuple[int, int, int, int]) -> int:
    """How many digits a figure's ink holds: runs of inked pixel columns, without the commas below the line."""
    crop = dark[box[1] : box[3], box[0] : box[2]]
    rows = np.flatnonzero(crop.any(axis=1))
    if not len(rows):
        return 0
    middle = (rows[0] + rows[-1]) / 2
    count = 0
    for start, end in _runs(np.flatnonzero(crop.any(axis=0)), gap=1):
        inked = np.flatnonzero(crop[:, start : end + 1].any(axis=1))
        if inked[0] < middle:
            count += 1
    return count


def _variants(texts: list[str], glyphs: int, summed: bool) -> list[str]:
    """Readings the passes may have missed, in the columns a row's sum checks: any digit for a lone digit no pass
    could read, and thin 1s restored up to the figure's count of digits."""
    if not summed:
        return []
    digits = {re.sub(r"\D", "", text) for text in texts} - {""}
    if not digits:
        return [str(d) for d in range(10)] if glyphs == 1 else []
    found: list[str] = []
    for text in sorted(digits):
        short = glyphs - len(text)
        if not 0 < short <= 2:
            continue
        for places in itertools.combinations(range(glyphs), short):
            out, rest = [], iter(text)
            for i in range(glyphs):
                out.append("1" if i in places else next(rest))
            found.append("".join(out))
    return found


def codes() -> dict[str, str]:
    """Station numbers read from the page by hand, keyed by page and the number as the scan reads it."""
    if not OVERRIDES.exists():
        return {}
    return cast(dict[str, str], json.loads(OVERRIDES.read_text()).get("codes", {}))


def discrepancies() -> dict[str, str]:
    """Districts whose station rows were checked against the page by hand and add up to other totals than the
    report prints, by district code, with what differs."""
    if not OVERRIDES.exists():
        return {}
    return cast(dict[str, str], json.loads(OVERRIDES.read_text()).get("discrepancies", {}))


def _shapes(image: np.ndarray, dark: np.ndarray, box: tuple[int, int, int, int]) -> list[np.ndarray]:
    """Each digit of a figure as a small grid of its ink, from left to right, without commas and specks."""
    crop, ink = image[box[1] : box[3], box[0] : box[2]], dark[box[1] : box[3], box[0] : box[2]]
    rows = np.flatnonzero(ink.any(axis=1))
    if not len(rows):
        return []
    middle = (rows[0] + rows[-1]) / 2
    found = []
    for start, end in _runs(np.flatnonzero(ink.any(axis=0)), gap=1):
        inked = np.flatnonzero(ink[:, start : end + 1].any(axis=1))
        if inked[0] >= middle or inked[-1] - inked[0] < 9:
            continue
        glyph = crop[inked[0] : inked[-1] + 1, start : end + 1]
        # A glyph's width says much about it, so it is kept in a fixed frame wide enough for any digit.
        frame = np.full((glyph.shape[0], max(14, glyph.shape[1])), 255, dtype=np.uint8)
        left = (frame.shape[1] - glyph.shape[1]) // 2
        frame[:, left : left + glyph.shape[1]] = glyph
        small = Image.fromarray(frame).resize((10, 12), Image.Resampling.BILINEAR)
        found.append(1 - np.asarray(small, dtype=np.float32).ravel() / 255)
    return found


def _narrow(dark: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """Whether a figure is a single glyph no more than six pixels wide."""
    ink = dark[box[1] : box[3], box[0] : box[2]]
    columns = np.flatnonzero(ink.any(axis=0))
    rows = np.flatnonzero(ink.any(axis=1))
    return bool(len(columns)) and columns[-1] - columns[0] < 6 and rows[-1] - rows[0] >= 10


def _cells(reading: dict[str, object]) -> list[tuple[float, int, list[str]]]:
    """Each body cell of a page: the height of its row, its column, and its readings."""
    found = []
    for band in cast(list[dict[str, object]], reading["bands"]):
        if band["kind"] == "total":
            continue
        for row in cast(list[dict[str, object]], band["rows"]):
            for column, texts in enumerate(cast(list[list[str]], row["cells"])):
                found.append((cast(float, row["centre"]), column, texts))
    return found


def _upright(pdf: Path, number: int, images: Path) -> Image.Image:
    return Image.open(render(pdf, number, images)).convert("L").rotate(-90, expand=True)


def augment(pages: dict[int, dict[str, object]], pdf: Path, images: Path) -> None:
    """Adds readings to the cells of every page.

    Digits whose cell every pass reads alike teach a nearest-neighbour reading of digit shapes, which then reads
    each one-digit figure again: Tesseract takes the report's bold 1 for a 7 and its 0 for a 4. Then each cell in
    the columns a row's sum checks takes the readings `_variants` gives from its count of digits.
    """
    samples: list[np.ndarray] = []
    labels: list[int] = []
    for number, reading in pages.items():
        image = _upright(pdf, number, images)
        gray, dark = np.asarray(image), np.asarray(image) < 170
        columns = list(itertools.pairwise(cast(list[int], reading["xs"])[2:]))
        for centre, column, texts in _cells(reading):
            agreed = {text.strip() for text in texts if text.strip()}
            if len(agreed) != 1 or sum(1 for text in texts if text.strip()) < 3:
                continue
            digits = re.sub(r"\D", "", agreed.pop())
            box = _blob(dark, *columns[column], int(centre - 30), int(centre + 30), centre)
            if box is None or not digits:
                continue
            shapes = _shapes(gray, dark, box)
            if len(shapes) == len(digits):
                samples += shapes
                labels += [int(digit) for digit in digits]
    known, digits_of = np.array(samples), np.array(labels)
    for number, reading in pages.items():
        image = _upright(pdf, number, images)
        gray, dark = np.asarray(image), np.asarray(image) < 170
        columns = list(itertools.pairwise(cast(list[int], reading["xs"])[2:]))
        for centre, column, texts in _cells(reading):
            box = _blob(dark, *columns[column], int(centre - 30), int(centre + 30), centre)
            if box is None:
                continue
            shapes = _shapes(gray, dark, box)
            # An electorate is never a lone 1, and a speck in its empty cell can look like one.
            if len(shapes) == 1 and column < len(columns) - 1 and _narrow(dark, box):
                # Only a 1 is this narrow; the scan's 1 is often read as a 7 or a 0, or not at all.
                texts += ["1"] * 6
                continue
            # Only a figure some pass read as one: a speck in an empty cell is not a digit.
            if len(shapes) == 1 and len(known) >= 50 and any(re.fullmatch(r"\d", t.strip()) for t in texts):
                distances = ((known - shapes[0]) ** 2).sum(axis=1)
                nearest = Counter(digits_of[np.argsort(distances)[:7]].tolist()).most_common(1)[0]
                texts += [str(nearest[0])] * (5 if nearest[1] >= 6 else 2)
            texts += _variants(texts, _glyphs(dark, box), column < len(columns) - 2)


def _read(pdf: Path, number: int, folder: Path, images: Path) -> None:
    cache = folder / f"{number}.json"
    if cache.exists():
        reading = json.loads(cache.read_text())
        if reading is None or reading.get("version") == VERSION:
            return
    cache.write_text(json.dumps(read_page(pdf, number, images)))


def readings(pdf: Path, pages: range, workers: int = 1) -> dict[int, dict[str, object]]:
    folder = raw.parsed("tt.ebc") / "report2015"
    images = folder / "pages"
    images.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda number: _read(pdf, number, folder, images), pages))
    found: dict[int, dict[str, object]] = {}
    for number in pages:
        reading = json.loads((folder / f"{number}.json").read_text())
        if reading is not None:
            found[number] = reading
    augment(found, pdf, images)
    return found


type Move = tuple[int, int, int]


def _fixes(row: list[int], options: list[list[int]], candidates: int) -> list[list[tuple[int, int]]]:
    """Ways to make a row's candidate votes add up to its valid votes by changing one or two of its cells."""
    width = candidates + 1

    def adds(values: list[int]) -> bool:
        return sum(values[:candidates]) == values[candidates]

    found: list[list[tuple[int, int]]] = []
    for c in range(width):
        for option in options[c]:
            if option != row[c] and adds([*row[:c], option, *row[c + 1 :]]):
                found.append([(c, option)])
    if found:
        return found
    for a, b in itertools.combinations(range(width), 2):
        for x in options[a]:
            if x == row[a]:
                continue
            for y in options[b]:
                trial = row[:]
                trial[a], trial[b] = x, y
                if y != row[b] and adds(trial):
                    found.append([(a, x), (b, y)])
    return found


def resolve(
    grid: list[list[list[int]]],
    values: list[list[int]],
    target: list[list[int]],
    candidates: int,
    score: Callable[[list[list[int]]], int],
) -> list[list[int]] | None:
    """Values for every row that add up row by row and to the district's totals, picked from each cell's readings.

    Each row that does not add up takes one of the changes that would make it add up; then the gaps left in each
    column are closed by moving one more cell to another of its readings, in a column outside the sum or in a pair
    of cells of one row that keeps it adding up. Of the ways that close every gap, the one matching most of the
    printed page totals is kept, and only when no other matches as many.
    """
    width = len(values[0])
    failing = [r for r, row in enumerate(values) if sum(row[:candidates]) != row[candidates]]
    choices = [_fixes(values[r], grid[r], candidates) for r in failing]
    if any(not choice for choice in choices) or prod(len(choice) for choice in choices) > 20000:
        return None
    totals = [sum(row[c] for row in values) for c in range(width)]
    # Single cells outside the sum, and pairs of cells in one row that keep it adding up.
    extras: list[list[Move]] = []
    for r, row in enumerate(values):
        if r in failing:
            continue
        for c in range(candidates + 1, width):
            extras += [[(r, c, option)] for option in grid[r][c] if option != row[c]]
        for a, b in itertools.combinations(range(candidates + 1), 2):
            for x in grid[r][a]:
                delta = x - row[a]
                if not delta:
                    continue
                y = row[b] + (delta if b == candidates else -delta)
                if y in grid[r][b]:
                    extras.append([(r, a, x), (r, b, y)])
    solutions: list[list[Move]] = []
    for combination in itertools.product(*choices):
        moves = [(r, c, v) for r, change in zip(failing, combination, strict=True) for c, v in change]
        reached = totals[:]
        for r, c, v in moves:
            reached[c] += v - values[r][c]
        if all(reached[c] in target[c] for c in range(width)):
            solutions.append(moves)
            continue
        for extra in extras:
            after = reached[:]
            for r, c, v in extra:
                after[c] += v - values[r][c]
            if all(after[c] in target[c] for c in range(width)):
                solutions.append(moves + extra)
        if len(solutions) > 200:
            return None
    if not solutions:
        return None
    ranked = []
    for moves in solutions:
        trial = [row[:] for row in values]
        for r, c, v in moves:
            trial[r][c] = v
        ranked.append((score(trial), -len(moves), trial))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        return None
    return ranked[0][2]
