"""Reads the polling station tables in Appendix I of the commission's scanned report on the 2020 election.

Each table page is a landscape table scanned on its side. A page is rendered at 200 dpi, turned upright, and its
ruled grid is found from long runs of dark pixels. Three Tesseract passes read the numbers: one reads each column of
the page body as a strip, one reads every cell on its own at two scales, and one reads a tight crop around the ink of
each short figure. Each row's candidate votes must add up to its valid votes, and each page's rows must carry the
running totals printed at the foot of one page and the head of the next, so a misread digit fails two checks and the
reading that passes both is kept. Cells the passes cannot settle are read from the page by hand and recorded in
`report2020.json`, keyed by page, station, and column, as are candidate headings the scan prints irregularly.
"""

import csv
import io
import itertools
import json
import os
import re
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

from ea import raw

DPI = 200
VERSION = 3
ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}
OVERRIDES = Path(__file__).with_name("report2020.json")
TOTAL_ROW = 90
# Station rows lie at least this far apart on the upright page; a number closer below one is a note.
MERGED = 75
DIGITS = ["-l", "eng", "-c", "tessedit_char_whitelist=0123456789,"]
CODES = ["-l", "eng", "-c", "tessedit_char_whitelist=0123456789-*"]
type Box = tuple[float, float, float, float]


@dataclass
class Row:
    code: str
    special: bool
    venue: str
    values: list[int]
    boxes: list[Box]


def _runs(indices: np.ndarray, gap: int = 4) -> list[tuple[int, int]]:
    found: list[list[int]] = []
    for index in indices.tolist():
        if found and index - found[-1][1] <= gap:
            found[-1][1] = index
        else:
            found.append([index, index])
    return [(start, end) for start, end in found]


def _lines(dark: np.ndarray, window: int) -> list[tuple[int, int, int]]:
    """Each straight dark line along the second axis: its position and its first and last pixel."""
    cumulative = np.cumsum(dark, axis=1, dtype=np.int32)
    hit = cumulative[:, window:] - cumulative[:, :-window] >= window * 0.9
    found = []
    for start, end in _runs(np.flatnonzero(hit.any(axis=1))):
        spans = np.flatnonzero(hit[start : end + 1].any(axis=0))
        found.append(((start + end) // 2, int(spans[0]), int(spans[-1]) + window))
    return found


def _tesseract(image: Image.Image, args: list[str]) -> str:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return subprocess.run(
        ["tesseract", "stdin", "stdout", *args], input=buffer.getvalue(), capture_output=True, check=True, env=ENV
    ).stdout.decode()


def _scaled(image: Image.Image, box: tuple[int, int, int, int], scale: int) -> Image.Image:
    crop = image.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    return padded


def _text(image: Image.Image, box: tuple[int, int, int, int], psm: str = "6") -> str:
    return _tesseract(_scaled(image, box, 2), ["--psm", psm, "-l", "eng"]).strip()


def _strip(image: Image.Image, box: tuple[int, int, int, int], args: list[str]) -> list[tuple[str, Box]]:
    """Words in a column strip, read at twice the size, with boxes in the upright page's pixels."""
    x0, y0 = box[0], box[1]
    out = _tesseract(_scaled(image, box, 2), ["--psm", "6", *args, "tsv"])
    words = []
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] != "5" or not row["text"].strip():
            continue
        left, top = (int(row["left"]) - 20) / 2 + x0, (int(row["top"]) - 20) / 2 + y0
        words.append((row["text"], (left, top, left + int(row["width"]) / 2, top + int(row["height"]) / 2)))
    return words


def _cell(image: Image.Image, box: tuple[int, int, int, int]) -> list[str]:
    """The second pass: one cell on its own, read as a single line at two scales."""
    return [_tesseract(_scaled(image, box, scale), ["--psm", "7", *DIGITS]).strip() for scale in (2, 3)]


def _glyph(image: Image.Image, box: tuple[int, int, int, int]) -> list[str]:
    """The third pass, for short figures: the cell's ink cropped tight and read as one word and as one character."""
    crop = np.asarray(image.crop(box)) < 140
    # Ignore ink touching the cell's edges, which belongs to the ruling.
    rows, columns = np.flatnonzero(crop[:, 3:-3].sum(axis=1) > 0), np.flatnonzero(crop[3:-3].sum(axis=0) > 0)
    if not len(rows) or not len(columns):
        return []
    x0, y0 = box[0], box[1]
    tight = (x0 + int(columns[0]) - 2, y0 + int(rows[0]) - 2, x0 + int(columns[-1]) + 4, y0 + int(rows[-1]) + 4)
    texts = []
    for psm in ("8", "10"):
        texts.append(_tesseract(_scaled(image, tight, 3), ["--psm", psm, *DIGITS]).strip())
    return texts


def glyphs(reading: dict[str, object], path: Path) -> dict[str, list[str]]:
    """Third-pass readings of every short or unread cell on a page, keyed by band, row, and column."""
    image = Image.open(path).convert("L").rotate(-90, expand=True)
    xs = cast(list[int], reading["xs"])
    columns = list(itertools.pairwise(xs[2:]))
    jobs: dict[str, tuple[int, int, int, int]] = {}
    for b, band in enumerate(cast(list[dict[str, object]], reading["bands"])):
        y0, y1 = cast(list[int], band["box"])
        if band["kind"] == "total":
            for c, texts in enumerate(cast(list[list[str]], band["cells"])):
                jobs[f"{b}:total:{c}"] = (columns[c][0] + 4, y0 + 4, columns[c][1] - 4, y1 - 4)
            continue
        for r, row in enumerate(cast(list[dict[str, object]], band["rows"])):
            centre = int(cast(float, row["centre"]))
            for c, texts in enumerate(cast(list[list[str]], row["cells"])):
                if max(len(re.sub(r"\D", "", text)) for text in texts) <= 2:
                    jobs[f"{b}:{r}:{c}"] = (columns[c][0] + 4, centre - 22, columns[c][1] - 4, centre + 22)
    with ThreadPoolExecutor(4) as pool:
        futures = {key: pool.submit(_glyph, image, box) for key, box in jobs.items()}
        return {key: future.result() for key, future in futures.items()}


def render(pdf: Path, number: int, folder: Path) -> Path:
    target = folder / f"{number}.png"
    if not target.exists():
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(pdf), str(target.with_suffix(""))],
            check=True,
        )
    return target


CODE = re.compile(r"\*?\d{3,4}(-\d+)?")


def _codes(words: list[tuple[str, Box]]) -> list[tuple[str, Box]]:
    """Polling station numbers in the first column, without stray dashes the scan reads before them."""
    return [(text.lstrip("-"), box) for text, box in words if CODE.fullmatch(text.lstrip("-"))]


def _centre(box: Box | list[float]) -> float:
    return (box[1] + box[3]) / 2


def _columns(dark: np.ndarray, top: int, bottom: int, left: int, right: int) -> list[int]:
    """The table's column rules: vertical lines that reach its bottom rule from near its top.

    A rule above a candidate's column starts below the header's first line, so only the end is held tight; a
    scan edge runs past the table at one end or the other.
    """
    tall = (bottom - top) * 0.6

    def meets(x: int) -> bool:
        # The bottom rule's ink runs into a column rule from the left; a scan edge stands clear of the table.
        return bool(dark[bottom - 8 : bottom + 9, max(0, x - 40) : x].any(axis=0).mean() > 0.8)

    return sorted(
        line[0]
        for line in _lines(dark.T, 120)
        if line[2] - line[1] > tall
        and abs(line[2] - bottom) < 40
        and line[1] > top - 40
        and left - 150 <= line[0] <= right + 150
        and (line[0] < left + 40 or meets(line[0]))
    )


def read_page(pdf: Path, number: int, folder: Path) -> dict[str, object] | None:
    """One page's raw reading: its grid, title, header, and both passes over every cell."""
    path = render(pdf, number, folder)
    scanned = Image.open(path)
    image = scanned.convert("L").rotate(-90, expand=True)
    dark = np.asarray(image) < 170
    # Dark scan edges at the border of the image are not rules.
    horizontal = [line for line in _lines(dark, 300) if 20 < line[0] < image.height - 20]
    if len(horizontal) < 3:
        return None
    top = horizontal[0][0]
    # The table's extent is that of its longest rule; the top rule is sometimes broken in the scan.
    left, right = max(((line[1], line[2]) for line in horizontal), key=lambda span: span[1] - span[0])
    title = _text(image, (0, max(0, top - 300), image.width, top - 5))
    if "ELECTORAL DISTRICT" not in title.upper():
        return None
    full = [line for line in horizontal if line[2] - line[1] > (right - left) * 0.7]
    partial = [line for line in horizontal if line not in full and line[0] > top and line[2] - line[1] > 300]
    xs = _columns(dark, top, full[-1][0], left, right)
    if len(xs) < 7:
        raise ValueError(f"page {number}: found {len(xs)} column rules")
    rules = [line[0] for line in full]
    header = rules[1]
    sub = partial[0][0] if partial else top
    columns = list(itertools.pairwise(xs[2:]))
    names = [_text(image, (x0 + 3, sub + 3, x1 - 3, header - 3)) for x0, x1 in columns[:-3]]
    bands: list[dict[str, object]] = []
    with ThreadPoolExecutor(4) as pool:
        for y0, y1 in itertools.pairwise(rules[1:]):
            if y1 - y0 < TOTAL_ROW:
                label = pool.submit(_text, image, (xs[0] + 3, y0 + 3, xs[2] - 3, y1 - 3), "7")
                cells = [pool.submit(_cell, image, (x0 + 4, y0 + 3, x1 - 4, y1 - 3)) for x0, x1 in columns]
                bands.append(
                    {"kind": "total", "label": label.result(), "box": [y0, y1], "cells": [c.result() for c in cells]}
                )
                continue
            codes = pool.submit(_strip, image, (xs[0] + 3, y0 + 3, xs[1] - 3, y1 - 3), CODES)
            venues = pool.submit(_strip, image, (xs[1] + 3, y0 + 3, xs[2] - 3, y1 - 3), ["-l", "eng"])
            strips = [pool.submit(_strip, image, (x0 + 4, y0 + 3, x1 - 4, y1 - 3), DIGITS) for x0, x1 in columns]
            anchors = sorted(
                ((text, b) for text, b in _codes(codes.result())),
                key=lambda item: item[1][1],
            )
            rows = []
            for position, (code, code_box) in enumerate(anchors):
                centre = _centre(code_box)
                low = (anchors[position - 1][1][3] + code_box[1]) / 2 if position else y0
                high = (code_box[3] + anchors[position + 1][1][1]) / 2 if position + 1 < len(anchors) else y1
                reads = [
                    pool.submit(_cell, image, (x0 + 4, int(centre) - 22, x1 - 4, int(centre) + 22))
                    for x0, x1 in columns
                ]
                rows.append(
                    {
                        "code": code,
                        "centre": centre,
                        "range": [low, high],
                        "venue": " ".join(t for t, b in venues.result() if abs(_centre(b) - centre) < 15),
                        "cells": [r.result() for r in reads],
                    }
                )
            bands.append(
                {
                    "kind": "body",
                    "box": [y0, y1],
                    "rows": rows,
                    "strips": [s.result() for s in strips],
                    "codes": codes.result(),
                }
            )
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


def readings(pdf: Path, pages: range, name: str = "report2020") -> dict[int, dict[str, object]]:
    folder = raw.parsed("tt.ebc") / name
    images = folder / "pages"
    images.mkdir(parents=True, exist_ok=True)
    found: dict[int, dict[str, object]] = {}
    missing = []
    for number in pages:
        cache = folder / f"{number}.json"
        if cache.exists():
            reading = json.loads(cache.read_text())
            if reading is None or reading.get("version") == VERSION:
                continue
        missing.append(number)
    for number in missing:
        reading = read_page(pdf, number, images)
        (folder / f"{number}.json").write_text(json.dumps(reading))
    for number in pages:
        reading = json.loads((folder / f"{number}.json").read_text())
        if reading is None:
            continue
        extra = folder / f"{number}.glyphs.json"
        if not extra.exists():
            extra.write_text(json.dumps(glyphs(reading, render(pdf, number, images))))
        reading["glyphs"] = json.loads(extra.read_text())
        found[number] = reading
    return found


def district_name(title: str) -> str:
    found = re.search(r"ELECTORAL DISTRICT OF\s+(.+)", title.upper())
    if found is None:
        raise ValueError(f"no district in {title!r}")
    return re.sub(r"[^A-Z/' .\-]", "", found.group(1).strip().splitlines()[0]).strip()


def _number(text: str) -> int | None:
    text = text.strip(" ,.'")
    if not re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", text):
        return None
    return int(text.replace(",", ""))


def _reads(texts: list[str]) -> list[int]:
    """The values the passes read for a cell, most often read first."""
    read = []
    for text in texts:
        for part in [text, *text.split()]:
            if (value := _number(part)) is not None:
                read.append(value)
                break
    counted = Counter(read)
    return sorted(counted, key=lambda value: (-counted[value], read.index(value)))


def _options(texts: list[str]) -> list[int]:
    """Readings of a cell: what the passes read, then each reading with one digit dropped or a thin 1 restored."""
    found = _reads(texts)
    if not any(re.sub(r"\D", "", text) for text in texts):
        # Tesseract often skips a lone 0 or 1 in a cell of its own.
        return [0, 1]
    for text in texts:
        digits = re.sub(r"\D", "", text)
        variants = [digits[:cut] + digits[cut + 1 :] for cut in range(len(digits))]
        variants += [digits[:cut] + "1" + digits[cut:] for cut in range(len(digits) + 1)]
        for variant in [digits, *variants]:
            if variant and int(variant) not in found:
                found.append(int(variant))
    return found


def _unturn(box: Box, height: int) -> Box:
    """Maps a box on the upright page back to the scanned page, which lies a quarter turn counterclockwise."""
    x0, y0, x1, y1 = box
    return (y0, height - x1, y1, height - x0)


@dataclass
class Draft:
    """A page's rows with every reading of each cell, before the district's sums settle them."""

    number: int
    width: int
    height: int
    district: str
    names: list[str]
    labels: list[tuple[str, bool, str, list[Box]]]
    grid: list[list[list[int]]]
    totals: dict[str, list[list[int]]]


def draft(reading: dict[str, object], fixes: dict[str, int]) -> Draft:
    number = cast(int, reading["page"])
    xs = cast(list[int], reading["xs"])
    height = cast(int, reading["upright_height"])
    page = Draft(
        number,
        cast(int, reading["width"]),
        cast(int, reading["height"]),
        district_name(cast(str, reading["title"])),
        cast(list[str], reading["names"]),
        [],
        [],
        {},
    )
    extra = cast(dict[str, list[str]], reading.get("glyphs", {}))
    for b, band in enumerate(cast(list[dict[str, object]], reading["bands"])):
        if band["kind"] == "total":
            label = cast(str, band["label"]).upper()
            kind = "brought" if not page.grid else "carried" if "CARRIED" in label else "total"
            page.totals[kind] = [
                [fixes[key]]
                if (key := f"{number}:{kind}:{column}") in fixes
                else _reads([*texts, *extra.get(f"{b}:total:{column}", [])])
                for column, texts in enumerate(cast(list[list[str]], band["cells"]))
            ]
            continue
        strips = cast(list[list[tuple[str, list[float]]]], band["strips"])
        previous: float | None = None
        for r, row in enumerate(cast(list[dict[str, object]], band["rows"])):
            code = cast(str, row["code"])
            low, high = cast(list[float], row["range"])
            centre = cast(float, row["centre"])
            if previous is not None and centre - previous < MERGED:
                # A number printed just below a station's is a note: "merged with" that station.
                station, special, venue, boxes = page.labels[-1]
                page.labels[-1] = (station, special, f"{venue} (merged with {code.lstrip('*')})", boxes)
                continue
            previous = centre
            options, boxes = [], []
            for column, texts in enumerate(cast(list[list[str]], row["cells"])):
                near = [(t, b) for t, b in strips[column] if low <= _centre(b) < high]
                key = f"{number}:{code.lstrip('*')}:{column}"
                tight = extra.get(f"{b}:{r}:{column}")
                reads = ["".join(t for t, _ in near), *texts, *(tight or [])]
                found = _options(reads)
                if found == [0, 1] and tight:
                    # The cell holds ink that no pass could read: a thin 1, since a 0 reads reliably.
                    found = [1, 0]
                options.append([fixes[key]] if key in fixes else found)
                if near:
                    box = (min(b[0] for _, b in near), min(b[1] for _, b in near))
                    box = (*box, max(b[2] for _, b in near), max(b[3] for _, b in near))
                else:
                    box = (xs[column + 2] + 4, centre - 15, xs[column + 3] - 4, centre + 15)
                boxes.append(_unturn(box, height))
            page.grid.append(options)
            page.labels.append((code.lstrip("*"), code.startswith("*"), cast(str, row["venue"]), boxes))
    return page


@dataclass
class Settled:
    rows: list[tuple[int, Row]]
    total: list[int]
    problems: list[str]


def settle(pages: list[Draft]) -> Settled:
    """Picks each cell's reading so every row's votes add up to its valid votes and every column carries the
    running totals printed at the foot and head of each page and in the district's total row."""
    width = len(pages[0].grid[0])
    candidates = width - 3
    chosen = [[[options[0] for options in row] for row in page.grid] for page in pages]
    problems: list[str] = []

    def consistent(row: list[int]) -> bool:
        return sum(row[:candidates]) == row[candidates]

    for k, page in enumerate(pages):
        for r, row in enumerate(page.grid):
            if consistent(chosen[k][r]):
                continue
            fixes = [
                (c, option)
                for c in range(candidates + 1)
                for option in row[c][1:]
                if consistent([*chosen[k][r][:c], option, *chosen[k][r][c + 1 :]])
            ]
            if len(fixes) == 1:
                chosen[k][r][fixes[0][0]] = fixes[0][1]

    def printed_at(k: int, c: int) -> list[int]:
        """Every reading of the running total printed at the foot of page k (and the head of page k + 1)."""
        if k + 1 < len(pages):
            reads = [
                *pages[k].totals.get("carried", [[]] * width)[c],
                *pages[k + 1].totals.get("brought", [[]] * width)[c],
            ]
        else:
            reads = pages[k].totals.get("total", [[]] * width)[c]
        counted = Counter(reads)
        return sorted(counted, key=lambda value: (-counted[value], reads.index(value)))

    # A row with several misread cells: when it is the only row on its page that does not add up, the gaps
    # between the page's columns and its printed totals say what each of its cells must be.
    for k, page in enumerate(pages):
        failing = [r for r in range(len(page.grid)) if not consistent(chosen[k][r])]
        if len(failing) != 1:
            continue
        r = failing[0]
        trial = chosen[k][r][:]
        for c in range(width):
            end, start = printed_at(k, c), printed_at(k - 1, c) if k else [0]
            if not end or not start:
                break
            gap = end[0] - start[0] - sum(row[c] for row in chosen[k])
            # A cell no pass could read at all may take any value the two sums agree on.
            if gap and (
                trial[c] + gap in page.grid[r][c] or (sorted(page.grid[r][c]) == [0, 1] and trial[c] + gap >= 0)
            ):
                trial[c] += gap
        if consistent(trial):
            chosen[k][r] = trial
    for c in range(width):
        running = 0
        # The rows' own running total, kept apart from the printed figure carried on after a mismatch.
        counted = 0
        pending: str | None = None
        for k, page in enumerate(pages):
            printed = printed_at(k, c)
            total = running + sum(row[c] for row in chosen[k])
            counted += sum(row[c] for row in chosen[k])
            if pending is not None:
                if counted in printed:
                    # The rows were right all along; the earlier printed total was misread.
                    pending, total = None, counted
                else:
                    problems.append(pending)
                    pending = None
            if total not in printed:
                fixes = {
                    (r, option)
                    for r, row in enumerate(page.grid)
                    for option in row[c][1:]
                    if total + option - chosen[k][r][c] in printed
                    and (c > candidates or consistent([*chosen[k][r][:c], option, *chosen[k][r][c + 1 :]]))
                }
                if len(fixes) == 1:
                    r, option = fixes.pop()
                    counted += option - chosen[k][r][c]
                    chosen[k][r][c] = option
                    total = running + sum(row[c] for row in chosen[k])
                else:
                    pending = f"page {page.number} column {c}: rows reach {total}, printed {printed}"
                    if k + 1 == len(pages):
                        problems.append(pending)
                    # Carry on from the printed figure, so the next page is checked on its own.
                    total = printed[0] if printed else total
            running = total
    for k, page in enumerate(pages):
        for r, row in enumerate(chosen[k]):
            if not consistent(row):
                problems.append(f"page {page.number} station {page.labels[r][0]}: votes do not add up in {row}")
    rows = [
        (page.number, Row(code, special, venue, chosen[k][r], boxes))
        for k, page in enumerate(pages)
        for r, (code, special, venue, boxes) in enumerate(page.labels)
    ]
    total = [sum(row.values[c] for _, row in rows) for c in range(width)]
    return Settled(rows, total, problems)


def _overrides(path: Path) -> dict[str, dict[str, object]]:
    return json.loads(path.read_text()) if path.exists() else {}


def overrides(path: Path = OVERRIDES) -> dict[str, int]:
    """Cells read from the page by hand, keyed by page, station or total row, and column."""
    cells = cast(dict[str, dict[str, object]], _overrides(path).get("cells", {}))
    return {key: int(cast(int, item["value"])) for key, item in cells.items()}


def headings(path: Path = OVERRIDES) -> dict[str, list[list[str]]]:
    """Candidate headings read by hand, by district code: each candidate's printed name and party abbreviation."""
    return cast(dict[str, list[list[str]]], _overrides(path).get("candidates", {}))
