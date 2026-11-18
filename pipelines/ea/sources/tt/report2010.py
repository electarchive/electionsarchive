"""Reads the polling station tables in Appendix I of the commission's scanned reports on the 2002, 2007, and 2010
elections.

The three reports share one layout, close to the 2015 report's: each table page is scanned upright, the body of a
table has no rules between its rows, and the electorate of a polling division is printed once, beside one of its
stations. The columns differ: after the candidates come the rejected ballots, the votes cast (the candidates' votes
plus the rejected ballots), and the electorate. Every row, station or total, prints its votes cast, so the figures in
that column anchor the rows. Each figure is read as in `report2015`, and `report2020.settle` then picks each cell's
reading so each station's candidate votes and rejected ballots add up to its votes cast and each page carries the
running totals printed at its foot and at the head of the next. Cells, station numbers, and candidate headings that
no pass settles are read from the page by hand and recorded in `report2010.json`, keyed by year.
"""

import itertools
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

from ea import raw
from ea.sources.tt.report2015 import (
    HALF,
    _blob,
    _figures,
    _glyphs,
    _total_label,
    _variants,
)
from ea.sources.tt.report2020 import (
    CODES,
    DIGITS,
    _centre,
    _codes,
    _lines,
    _number,
    _reads,
    _runs,
    _strip,
    _tesseract,
    _text,
    render,
)

VERSION = 6
OVERRIDES = Path(__file__).with_name("report2010.json")


def _rules(dark: np.ndarray) -> tuple[list[int], int, int]:
    """The table's column rules, and the top and bottom of the table.

    A dark bar some scans carry down the left margin runs past the table's foot, so only lines that end with most
    rules are kept.
    """
    # A district's last page can hold its totals alone, under a header some 230 pixels tall.
    found = [line for line in _lines(dark.T, 60) if line[2] - line[1] > 150 and 10 < line[0] < dark.shape[1] - 10]
    if not found:
        return [], 0, 0
    bottom = int(np.median([line[2] for line in found]))
    found = [line for line in found if abs(line[2] - bottom) < 30]
    # Only rules within the span of the table's longest horizontal rule; a scan edge stands outside it.
    across = [line for line in _lines(dark, 150) if line[2] - line[1] > 300]
    if across:
        left, right = max(((line[1], line[2]) for line in across), key=lambda span: span[1] - span[0])
        found = [line for line in found if left - 60 <= line[0] <= right + 60]
    if not found:
        return [], 0, 0
    top = min(line[1] for line in found)
    columns = sorted(line[0] for line in found if line[2] - line[1] > (bottom - top) * 0.6)
    merged: list[int] = []
    for x in columns:
        if merged and x - merged[-1] < 25:
            continue
        merged.append(x)
    return merged, top, bottom


def _read_cell(image: Image.Image, box: tuple[int, int, int, int], args: list[str] = DIGITS) -> list[str]:
    """A figure's ink enlarged two, three, and four times, each read as a line, and as a character when narrow.

    On these reports' bold print, Tesseract reads a figure as a line far more often than as a word.
    """
    texts = []
    for scale in (2, 3, 4):
        crop = image.crop(box)
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        padded.paste(crop, (20, 20))
        modes = ("7", "10") if box[2] - box[0] < 24 and scale == 3 else ("7",)
        texts += [_tesseract(padded, ["--psm", psm, *args]).strip() for psm in modes]
    return texts


def _header(dark: np.ndarray, top: int, left: int, right: int) -> int:
    """The rule under the column headings: the first long horizontal rule at least 150 pixels below the table's top.

    The headings stand some 190 pixels tall, and on a district's last page the rule above its totals can lie close
    below them.
    """
    for line in _lines(dark, 150):
        if top + 150 <= line[0] <= top + 260 and line[1] < right and line[2] > left and line[2] - line[1] > 300:
            return line[0]
    return top + 190


def _spot(dark: np.ndarray, x0: int, x1: int, low: int, high: int, target: float) -> tuple[int, int, int, int] | None:
    """The box around a station number nearest a height, or None for a row without one.

    The number column is narrow and its bold figures fill most of it, so a line of pixels is not taken for a rule
    as `report2015._blob` takes it; only the column's vertical rules are cut away.
    """
    low, high = max(0, low), min(dark.shape[0], high)
    sub = dark[low:high, x0 + 4 : x1 - 4].copy()
    if not sub.size:
        return None
    sub[:, sub.mean(axis=0) > 0.6] = False
    runs = [(a + low, b + low) for a, b in _runs(np.flatnonzero(sub.sum(axis=1) > 1), gap=3) if 5 <= b - a <= 40]
    if not runs:
        return None
    a, b = min(runs, key=lambda run: abs((run[0] + run[1]) / 2 - target))
    columns = np.flatnonzero(sub[a - low : b - low + 1].any(axis=0))
    if columns[-1] - columns[0] < 15:
        return None
    return (x0 + 4 + int(columns[0]) - 6, a - 6, x0 + 4 + int(columns[-1]) + 7, b + 7)


def _heading(image: Image.Image, dark: np.ndarray, top: int, left: int, right: int) -> str:
    """The lines above the table read one at a time, which reads a letter-spaced heading the block read garbles."""
    band = dark[max(0, top - 160) : top - 2, left:right]
    lines = []
    for a, b in _runs(np.flatnonzero(band.sum(axis=1) > 0), gap=2):
        if b - a < 5:
            continue
        y0, y1 = max(0, top - 160) + a - 4, max(0, top - 160) + b + 4
        crop = image.crop((left, y0, right, y1))
        crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
        padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        padded.paste(crop, (20, 20))
        lines.append(_tesseract(padded, ["--psm", "7", "-l", "eng"]).strip())
    return _title("\n".join(lines))


def _title(text: str) -> str:
    # The scan garbles a letter of the heading now and then.
    text = re.sub(r"\b\w{0,2}ECT\wRAL\W+DIST\w*\W+\w{2}\b", "ELECTORAL DISTRICT OF", text.upper())
    return re.sub(r"ELECTORAL DISTRICT OF(?=\S)", "ELECTORAL DISTRICT OF ", text)


def read_page(pdf: Path, number: int, folder: Path, rules: list[int] | None = None) -> dict[str, object] | None:
    """One page's raw reading in the shape `report2020.draft` takes: its grid, title, header, and every cell.

    `rules` gives the column rules for a page whose scan lost one, in place of those found on it.
    """
    path = render(pdf, number, folder)
    image = Image.open(path).convert("L")
    dark = np.asarray(image) < 170
    xs, top, bottom = _rules(dark)
    if rules is not None:
        xs = rules
    if len(xs) < 7:
        return None
    title = _title(_text(image, (0, max(0, top - 160), image.width, top - 2)))
    if "ELECTORAL DISTRICT OF" not in title:
        title = _heading(image, dark, top, xs[0], xs[-1])
    if "ELECTORAL DISTRICT OF" not in title:
        return None
    header = _header(dark, top, xs[0], xs[-1]) + 10
    if header > bottom - 40:
        return None
    columns = list(itertools.pairwise(xs[2:]))
    names = [_text(image, (x0 + 3, top + 50, x1 - 3, header - 12)) for x0, x1 in columns[:-3]]
    # Every row, station or total, prints its votes cast once, beside the first line of its venue.
    heights = _figures(dark, *columns[-2], header, bottom - 3)
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
            label = " ".join(t for t, b in words if abs(_centre(b) - centre) < 20)
            # The station number stands level with the first line of the venue; a total row has none.
            spot = _spot(dark, xs[0], xs[1], int(max(low, centre - 25)), int(min(high, centre + 25)), centre)
            # Venues such as CHARLIEVILLE read close to the words of a total row's label, so only the station number
            # tells a station from a total.
            total = spot is None
            near = []
            if spot is not None:
                near += [code for code, _ in _codes([(t, (0, 0, 0, 0)) for t in _read_cell(image, spot, CODES)])]
            near += [code for code, b in found if low <= _centre(b) < high]
            # Polling divisions are numbered with four digits, and their stations with two more.
            near = [code for code in near if re.fullmatch(r"\*?\d{4}(-\d{2})?", code)] or near
            spots = []
            for x0, x1 in columns:
                box = _blob(dark, x0, x1, int(max(low, centre - 25)), int(min(high, centre + 25)), centre)
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
            venue = " ".join(t for t, b in words if centre - 15 <= _centre(b) < high)
            row = {"code": code, "centre": centre, "range": [low, high], "venue": venue, "cells": cells}
            cast(list[dict[str, object]], body["rows"]).append(row)
    return {
        "version": VERSION,
        "page": number,
        "width": image.width,
        "height": image.height,
        "upright_height": image.height,
        "upright": True,
        "title": title,
        "names": names,
        "xs": xs,
        "bands": bands,
    }


def _trim(dark: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """A figure's box without the column rule it can catch: pixel columns inked over most of the box's height."""
    x0, y0, x1, y1 = box
    ruled = np.flatnonzero(dark[y0:y1, x0:x1].mean(axis=0) > 0.7)
    middle = (x1 - x0) / 2
    left = [x for x in ruled.tolist() if x < middle]
    right = [x for x in ruled.tolist() if x >= middle]
    return (x0 + (max(left) + 2 if left else 0), y0, x0 + (min(right) - 1 if right else x1 - x0), y1)


def _inked(ink: np.ndarray, middle: float) -> np.ndarray:
    """The pixel columns of a figure that hold a digit's ink: a comma's columns hold ink below the middle alone."""
    tops = np.where(ink.any(axis=0), ink.argmax(axis=0), ink.shape[0])
    return np.flatnonzero(tops < middle)


def _pieces(gray: np.ndarray, dark: np.ndarray, box: tuple[int, int, int, int], pitch: float) -> list[np.ndarray]:
    """Each digit of a figure as a small grid of its ink, from left to right, without commas and specks.

    The reports set their figures in a face whose digits share one width, and digits that touch in the scan are
    parted at that width.
    """
    crop, ink = gray[box[1] : box[3], box[0] : box[2]], dark[box[1] : box[3], box[0] : box[2]]
    rows = np.flatnonzero(ink.any(axis=1))
    if not len(rows):
        return []
    middle = (rows[0] + rows[-1]) / 2
    found = []
    for start, end in _runs(_inked(ink, middle), gap=1):
        inked = np.flatnonzero(ink[:, start : end + 1].any(axis=1))
        if inked[0] >= middle or inked[-1] - inked[0] < 6:
            continue
        count = max(1, round((end - start + 1.5) / (pitch + 0.5))) if pitch else 1
        edges = np.linspace(start, end + 1, count + 1).round().astype(int)
        for left, right in itertools.pairwise(edges.tolist()):
            glyph = crop[inked[0] : inked[-1] + 1, left:right]
            frame = np.full((glyph.shape[0], max(16, glyph.shape[1])), 255, dtype=np.uint8)
            offset = (frame.shape[1] - glyph.shape[1]) // 2
            frame[:, offset : offset + glyph.shape[1]] = glyph
            small = Image.fromarray(frame).resize((12, 14), Image.Resampling.BILINEAR)
            found.append(1 - np.asarray(small, dtype=np.float32).ravel() / 255)
    return found


def _narrow(dark: np.ndarray, box: tuple[int, int, int, int], pitch: float) -> bool:
    """Whether a figure is a single glyph narrower than the reports' other digits: a 1."""
    ink = dark[box[1] : box[3], box[0] : box[2]]
    columns = np.flatnonzero(ink.any(axis=0))
    rows = np.flatnonzero(ink.any(axis=1))
    return bool(len(columns)) and columns[-1] - columns[0] + 1 < pitch - 1 and rows[-1] - rows[0] >= 6


def _pitch(dark: np.ndarray, box: tuple[int, int, int, int], digits: str) -> list[int]:
    """The widths of a figure's separate glyphs other than 1s, when each digit stands apart."""
    ink = dark[box[1] : box[3], box[0] : box[2]]
    rows = np.flatnonzero(ink.any(axis=1))
    if not len(rows):
        return []
    middle = (rows[0] + rows[-1]) / 2
    runs = []
    for start, end in _runs(_inked(ink, middle), gap=1):
        inked = np.flatnonzero(ink[:, start : end + 1].any(axis=1))
        if inked[0] < middle and inked[-1] - inked[0] >= 6:
            runs.append(end - start + 1)
    if len(runs) != len(digits):
        return []
    return [width for width, digit in zip(runs, digits, strict=True) if digit != "1"]


def _cells(reading: dict[str, object]) -> list[tuple[float, int, list[str]]]:
    found = []
    for band in cast(list[dict[str, object]], reading["bands"]):
        if band["kind"] == "total":
            continue
        for row in cast(list[dict[str, object]], band["rows"]):
            for column, texts in enumerate(cast(list[list[str]], row["cells"])):
                found.append((cast(float, row["centre"]), column, texts))
    return found


# The digit shapes the last call of `augment` learned, kept for inspection.
_MODEL: dict[str, object] = {}


def _agreed(texts: list[str]) -> str | None:
    """A cell's digits when every pass that read it read the same, and at least two did."""
    agreed = {text.strip() for text in texts if text.strip()}
    if len(agreed) != 1 or sum(1 for text in texts if text.strip()) < 2:
        return None
    return re.sub(r"\D", "", agreed.pop()) or None


def augment(pages: dict[int, dict[str, object]], pdf: Path, images: Path) -> None:
    """Adds readings to the cells of every page.

    Digits of the cells every pass reads alike teach a nearest-neighbour reading of digit shapes, which reads every
    figure again: Tesseract takes the reports' bold 5 for an 8 and their 7 for a 1, often in every pass. A figure
    with fewer digits read than its ink holds is read again in other modes, and the columns a row's sum checks take
    readings with a dropped digit restored, as `report2015.augment` does.
    """

    def load(number: int) -> tuple[Image.Image, np.ndarray, np.ndarray]:
        image = Image.open(render(pdf, number, images)).convert("L")
        gray = np.asarray(image)
        return image, gray, gray < 170

    def boxes(reading: dict[str, object], dark: np.ndarray) -> list[tuple[int, list[str], tuple[int, int, int, int]]]:
        columns = list(itertools.pairwise(cast(list[int], reading["xs"])[2:]))
        found = []
        for centre, column, texts in _cells(reading):
            box = _blob(dark, *columns[column], int(centre - 25), int(centre + 25), centre)
            if box is not None:
                found.append((column, texts, _trim(dark, box)))
        return found

    widths: list[int] = []
    for number, reading in pages.items():
        _, _, dark = load(number)
        for _, texts, box in boxes(reading, dark):
            if (digits := _agreed(texts)) is not None:
                widths += _pitch(dark, box, digits)
    pitch = float(np.mean(widths)) if widths else 0.0
    samples: list[np.ndarray] = []
    labels: list[int] = []
    for number, reading in pages.items():
        _, gray, dark = load(number)
        for _, texts, box in boxes(reading, dark):
            if (digits := _agreed(texts)) is None:
                continue
            pieces = _pieces(gray, dark, box, pitch)
            if len(pieces) == len(digits):
                samples += pieces
                labels += [int(digit) for digit in digits]
    known, digits_of = np.array(samples), np.array(labels)
    _MODEL.update(pitch=pitch, known=known, digits=digits_of)
    for number, reading in pages.items():
        image, gray, dark = load(number)
        last = len(cast(list[int], reading["xs"])) - 4
        cache = images.parent / f"{number}.more.json"
        more: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
        for column, texts, box in boxes(reading, dark):
            summed = column < last
            glyphs = _glyphs(dark, box)
            # No figure but a lone 0 begins with a 0: such a reading has dropped a digit the scan blurred.
            texts[:] = [text for text in texts if not re.match(r"0\d", text.strip())]
            if max((len(re.sub(r"\D", "", text)) for text in texts), default=0) < glyphs:
                # Fewer digits read than the ink holds: read the figure again in the modes the first pass skips.
                tag = f"{box[0]}:{box[1]}"
                if tag not in more:
                    more[tag] = _again(image, box)
                texts += more[tag]
            pieces = _pieces(gray, dark, box, pitch)
            if pieces and len(known) >= 100:
                read = ""
                for piece in pieces:
                    distances = ((known - piece) ** 2).sum(axis=1)
                    read += str(Counter(digits_of[np.argsort(distances)[:5]].tolist()).most_common(1)[0][0])
                texts[:0] = [read] * 3
            texts += _doubled(texts, glyphs, summed)
            if len(pieces) == 1 and summed and _narrow(dark, box, pitch):
                # Only a 1 is this narrow; the scan's 1 is often read as a 7 or a 0, or not at all.
                texts += ["1"] * 6
                continue
            texts += _variants(texts, glyphs, summed)
        cache.write_text(json.dumps(more))
        _complete(reading)


def _complete(reading: dict[str, object]) -> None:
    """Gives the least certain cell of each row that does not add up the value that would make it add up.

    A row's candidates' votes and rejected ballots add up to its ballots cast. When the most common readings of its
    cells do not, the cell whose passes agree least takes, as one more reading, the value the others call for; the
    district's sums and printed totals then decide whether it stands.
    """
    for band in cast(list[dict[str, object]], reading["bands"]):
        if band["kind"] == "total":
            continue
        for row in cast(list[dict[str, object]], band["rows"]):
            cells = cast(list[list[str]], row["cells"])[:-1]
            tops: list[int] = []
            shares: list[float] = []
            for texts in cells:
                values = _reads(texts)
                read = [text for text in texts if text.strip()]
                tops.append(values[0] if values else 0)
                shares.append(sum(1 for text in read if _number(text) == tops[-1]) / len(read) if read else 0.0)
            if sum(tops[:-1]) == tops[-1]:
                continue
            weakest = min(range(len(cells)), key=lambda column: shares[column])
            if weakest == len(cells) - 1:
                needed = sum(tops[:-1])
            else:
                needed = tops[-1] - sum(tops[:-1]) + tops[weakest]
            if needed >= 0:
                cells[weakest].append(str(needed))


def _again(image: Image.Image, box: tuple[int, int, int, int]) -> list[str]:
    """A figure read as a word and as raw text at three scales, and as a line at five times its size."""
    texts = []
    for scale in (3, 4, 5):
        crop = image.crop(box)
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        padded.paste(crop, (20, 20))
        modes = ("8", "13", "7") if scale == 5 else ("8", "13")
        texts += [_tesseract(padded, ["--psm", psm, *DIGITS]).strip() for psm in modes]
    return texts


def _doubled(texts: list[str], glyphs: int, summed: bool) -> list[str]:
    """Readings with a digit the passes dropped from a pair restored, such as 441 read as 41, in the columns a row's
    sum checks."""
    if not summed:
        return []
    found = []
    # Two like digits often touch in the scan, so the count of glyphs does not rule a pair out.
    for digits in {re.sub(r"\D", "", text) for text in texts} - {""}:
        if len(digits) < max(glyphs + 1, 4):
            found += [digits[: i + 1] + digits[i:] for i in range(len(digits))]
    return found


def _read(pdf: Path, number: int, folder: Path, images: Path) -> None:
    cache = folder / f"{number}.json"
    if cache.exists():
        reading = json.loads(cache.read_text())
        if reading is None or reading.get("version") == VERSION:
            return
    cache.write_text(json.dumps(read_page(pdf, number, images)))


def readings(pdf: Path, year: int, pages: range, workers: int = 2) -> dict[int, dict[str, object]]:
    folder = raw.parsed("tt.ebc") / f"report{year}"
    images = folder / "pages"
    images.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda number: _read(pdf, number, folder, images), pages))
    found: dict[int, dict[str, object]] = {}
    for number in pages:
        reading = json.loads((folder / f"{number}.json").read_text())
        if reading is not None:
            reading["title"] = _title(cast(str, reading["title"]))
            found[number] = reading
    for number in _retitle(pdf, found, folder, images):
        (folder / f"{number}.json").write_text(json.dumps(found[number]))
    for number, rules in _repairs(found).items():
        cache = folder / f"{number}.fixed.json"
        reading = json.loads(cache.read_text()) if cache.exists() else None
        if reading is None or reading.get("xs") != rules or reading.get("version") != VERSION:
            reading = read_page(pdf, number, images, rules)
            cache.write_text(json.dumps(reading))
        if reading is not None:
            found[number] = reading
    augment(found, pdf, images)
    return found


def _retitle(pdf: Path, found: dict[int, dict[str, object]], folder: Path, images: Path) -> list[int]:
    """Reads again, line by line, the headings of pages whose block reading named no district."""
    changed = []
    for number, reading in found.items():
        title = cast(str, reading["title"])
        if re.search(r"DISTRICT OF\s+[A-Z]{3}", title):
            continue
        image = Image.open(render(pdf, number, images)).convert("L")
        dark = np.asarray(image) < 170
        xs, top, _ = _rules(dark)
        if not xs:
            continue
        better = _heading(image, dark, top, xs[0], xs[-1])
        if re.search(r"DISTRICT OF\s+[A-Z]{3}", better):
            reading["title"] = better
            changed.append(number)
    return changed


def _repairs(found: dict[int, dict[str, object]]) -> dict[int, list[int]]:
    """Column rules for each page that lacks one its district's other pages have.

    The scan loses a faint rule now and then. A district's pages share one layout, shifted a little from page to
    page, so the missing rule is placed where the district's layout puts it, aligned on the rules the page has.
    """
    districts: dict[str, list[int]] = {}
    for number, reading in found.items():
        districts.setdefault(re.sub(r"[^A-Z]", "", cast(str, reading["title"]).split("DISTRICT OF")[-1]), []).append(
            number
        )
    repairs: dict[int, list[int]] = {}
    for numbers in districts.values():
        counts = Counter(len(cast(list[int], found[n]["xs"])) for n in numbers)
        size = counts.most_common(1)[0][0]
        model = [n for n in numbers if len(cast(list[int], found[n]["xs"])) == size]
        offsets = np.median(
            [np.array(found[n]["xs"]) - cast(list[int], found[n]["xs"])[0] for n in model], axis=0
        ).tolist()
        for number in numbers:
            xs = cast(list[int], found[number]["xs"])
            if len(xs) >= size:
                continue
            # The shift that lays the most of the page's own rules within six pixels of the layout's.
            shifts = [x - offset for x in xs for offset in offsets]
            best = max(shifts, key=lambda shift: sum(min(abs(x - shift - o) for o in offsets) <= 6 for x in xs))
            placed = []
            for offset in offsets:
                near = [x for x in xs if abs(x - best - offset) <= 6]
                placed.append(near[0] if near else round(float(best + offset)))
            repairs[number] = placed
    return repairs


def _section(year: int, name: str) -> dict[str, object]:
    data = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    return cast(dict[str, object], cast(dict[str, object], data.get(str(year), {})).get(name, {}))


def overrides(year: int) -> dict[str, int]:
    """Cells read from the page by hand, keyed by page, station or total row, and column."""
    cells = cast(dict[str, dict[str, object]], _section(year, "cells"))
    return {key: int(cast(int, item["value"])) for key, item in cells.items()}


def headings(year: int) -> dict[str, list[list[str]]]:
    """Candidate headings read by hand, by district code: each candidate's printed name and party abbreviation."""
    return cast(dict[str, list[list[str]]], _section(year, "candidates"))


def codes(year: int) -> dict[str, str]:
    """Station numbers read from the page by hand, keyed by page and the number as the scan reads it."""
    return cast(dict[str, str], _section(year, "codes"))
