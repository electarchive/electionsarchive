import csv
import difflib
import io
import re
import statistics
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.sources.us import house_books
from ea.store import DATA

SOURCE = "us.tx_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Texas Secretary of State"
TITLE = "State of Texas General Election Returns, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "Texas's House results by county from the Secretary of State's official general election returns"
STATE = "TX"
FIPS = "48"
ITEMS = {
    1980: ("micro_IA40706906_0226", date(1980, 11, 4), (6, 7, 8, 13, 14, 15, 16, 17)),
    1982: ("micro_IA40706913_0346", date(1982, 11, 2), range(10, 27)),
    1986: ("micro_IA40706926_0170", date(1986, 11, 4), range(1, 15)),
    1988: ("micro_IA40706933_0069", date(1988, 11, 8), range(13, 40)),
    1990: ("micro_IA40706943_0094", date(1990, 11, 6), range(37, 64)),
    1992: ("micro_IA40706946_0270", date(1992, 11, 3), range(7, 37)),
    1994: ("micro_IA40706953_0297", date(1994, 11, 8), range(20, 50)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1982, 1), (1986, 13), (1988, 13)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
COMMAS_YEARS = {1980, 1982, 1988, 1990, 1992, 1994}
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
ANCHORS = {
    (1982, 16, "Culberson", 0): (1010, 1615),
    (1982, 16, "Culberson", 1): (1352, 1615),
    (1982, 16, "Culberson", 2): (1720, 1620),
    (1982, 7, "Harris", 1): (1325, 1872),
    (1982, 7, "Harris", 2): (1696, 1879),
    (1982, 27, "Kleberg", 1): (1360, 2081),
    (1986, 24, "Dallas", 0): (1082, 621),
    (1986, 24, "Dallas", 1): (1422, 621),
    (1986, 25, "Harris", 0): (1082, 1205),
    (1988, 17, "Montague", 0): (522, 1196),
    (1988, 17, "Nolan", 0): (522, 1228),
    (1988, 17, "Palo Pinto", 0): (522, 1258),
    (1988, 27, "Cameron", 0): (2185, 497),
    (1988, 27, "Kenedy", 0): (2225, 527),
    (1988, 27, "Kleberg", 0): (2190, 560),
    (1990, 10, "Bastrop", 5): (2070, 441),
    (1992, 13, "Armstrong", 0): (852, 667),
    (1992, 13, "Swisher", 1): (1138, 1685),
    (1992, 16, "El Paso", 0): (860, 675),
    (1992, 16, "El Paso", 1): (1088, 675),
}


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])
    path = DATA / "parsed" / SOURCE / "values.parquet"
    cells = pl.read_parquet(path).to_dicts()
    by_key = {(cell["year"], cell["page"], cell["district"], cell["county"], cell["column"]): cell for cell in cells}
    for year, (url, _, _, pages) in VOLUMES.items():
        book = raw.cached(RAW, url)
        page, tables = 0, []
        for line in (DATA / "parsed" / SOURCE / "model" / f"{year}.txt").read_text().splitlines():
            if line.startswith("@"):
                page = int(line[1:])
            elif (head := line.split("|")[0].strip()).isdigit():
                tables.append({"page": page, "district": int(head), "rows": []})
            else:
                tables[-1]["rows"].append(head)
        for page in pages:
            on_page = [table for table in tables if table["page"] == page]
            items = [(table, i, name) for table in on_page for i, name in enumerate(table["rows"])]
            if not items:
                continue
            tokens: list[tuple[str, tuple[float, float, float, float]]] = []
            words: list[tuple[str, tuple[float, float, float, float]]] = []
            for cache in (DATA / "parsed" / SOURCE / "ocr").glob(f"{book.sha256}-{page}-*.tsv"):
                scale = int(cache.name.split("-")[2]) / 300
                for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
                    if row["level"] != "5" or not row["text"].strip():
                        continue
                    box = (
                        int(row["left"]) / scale,
                        int(row["top"]) / scale,
                        (int(row["left"]) + int(row["width"])) / scale,
                        (int(row["top"]) + int(row["height"])) / scale,
                    )
                    if re.search(r"\d", row["text"]):
                        for digits in {
                            re.sub(r"\D", "", row["text"]),
                            re.sub(r"\D", "", row["text"].translate(scan.CONFUSABLE)),
                        }:
                            tokens.append((digits, box))
                    if len(letters := re.sub(r"[^a-z]", "", row["text"].lower())) >= 3:
                        words.append((letters, box))
            with tempfile.TemporaryDirectory() as folder:
                image = Path(folder) / "page.png"
                house_books._render(book, page, 300, image, False)
                gray = np.asarray(Image.open(image).convert("L"))
            inked = np.concatenate(
                [
                    np.sort(gray[round(box[1]) : round(box[3]), round(box[0]) : round(box[2])], axis=None)[:20]
                    for digits, box in tokens
                    if len(digits) >= 3
                ]
            )
            ink = gray < (float(np.median(inked)) + float(np.median(gray))) / 2
            width = ink.shape[1]
            digit = statistics.median(
                (box[2] - box[0]) / len(digits) for digits, box in tokens if len(digits) >= 3 and digits.isdigit()
            )
            height = statistics.median(box[3] - box[1] for digits, box in tokens if len(digits) >= 3)
            matches = []
            for _, _, name in items:
                first = re.sub(r"[^a-z]", "", name.lower().split()[0]) if name != "Totals" else "total"
                indent = 0.0
                if len(first) < 3:
                    indent = (len(name.split()[0]) + 1) * digit
                    first = re.sub(r"[^a-z]", "", name.lower().split()[1])
                matches.append(
                    [
                        (box[0] - indent, box[1], box[2], box[3])
                        for text, box in words
                        if box[0] < 0.4 * width
                        and (
                            text == first
                            or (len(first) >= 4 and difflib.SequenceMatcher(None, text, first).ratio() >= 0.8)
                        )
                    ]
                )
            lefts = [box[0] for boxes in matches for box in boxes]
            margin = max(lefts, key=lambda left: sum(abs(other - left) < 2 * digit for other in lefts))
            options = [
                sorted({(box[1] + box[3]) / 2 for box in boxes if abs(box[0] - margin) < 2 * digit})
                for boxes in matches
            ]
            best: list[tuple[int, float | None, list[float | None]]] = [(0, None, [])]
            for choices in options:
                grown = []
                for count, last, picks in best:
                    grown.append((count, last, [*picks, None]))
                    for y in choices:
                        if last is None or y > last + 0.8 * height:
                            grown.append((count + 1, y, [*picks, y]))
                frontier: dict[float | None, tuple[int, float | None, list[float | None]]] = {}
                for entry in grown:
                    if entry[1] not in frontier or entry[0] > frontier[entry[1]][0]:
                        frontier[entry[1]] = entry
                best = sorted(frontier.values(), key=lambda entry: -entry[0])[:200]
            picks = max(best, key=lambda entry: entry[0])[2]
            lines = {(id(table), i): y for (table, i, _), y in zip(items, picks, strict=True) if y is not None}
            steps = [
                (b - a)
                for (_, a), (_, b) in zip(
                    sorted(lines.items(), key=lambda kv: kv[1]), sorted(lines.items(), key=lambda kv: kv[1])[1:]
                )
            ]
            pitch = statistics.median(step for step in steps if step < 3 * height) if steps else 2 * height
            top, bottom = round(min(lines.values()) - 3 * pitch), round(max(lines.values()) + 3 * pitch)
            values = {
                str(by_key[key]["votes"])
                for table, i, name in items
                for c in range(12)
                if (key := (year, page, table["district"], name, c)) in by_key and by_key[key]["votes"] >= 10
            }
            right = max((box[2] for digits, box in tokens if digits in values and top < box[1] < bottom), default=width)
            small = np.pad(ink[max(0, top) : bottom : 4, round(margin) : round(right) : 4], ((120, 120), (0, 0)))
            across = np.arange(small.shape[1])
            slope = max(
                np.linspace(-0.12, 0.12, 121),
                key=lambda s: float(
                    np.var(
                        small[
                            np.arange(120, small.shape[0] - 120)[:, None] + np.round(s * across).astype(int),
                            across,
                        ].sum(axis=1)
                    )
                ),
            )
            reach = abs(round(slope * width)) + 1
            sheared = np.pad(ink, ((reach, reach), (0, 0)))[
                reach + np.arange(ink.shape[0])[:, None] + np.round(slope * (np.arange(width) - margin)).astype(int),
                np.arange(width),
            ]
            for table in on_page:
                rows = table["rows"]
                known = {i: lines[(id(table), i)] for i in range(len(rows)) if (id(table), i) in lines}
                if not known:
                    continue
                for i in range(len(rows)):
                    if i not in known:
                        below, above = [k for k in known if k < i], [k for k in known if k > i]
                        known[i] = (
                            known[max(below)]
                            + (known[min(above)] - known[max(below)]) * (i - max(below)) / (min(above) - max(below))
                            if below and above
                            else known[max(below)] + pitch * (i - max(below))
                            if below
                            else known[min(above)] - pitch * (min(above) - i)
                        )
                confirmed = [
                    (cell["box"][1] + cell["box"][3]) / 2
                    - slope * ((cell["box"][0] + cell["box"][2]) / 2 - margin)
                    - known[i]
                    for i, county in enumerate(rows)
                    for c in range(12)
                    if (cell := by_key.get((year, page, table["district"], county, c))) is not None
                    and cell["located"] == "unique"
                    and cell["box"]
                ]
                offset = statistics.median(confirmed) if confirmed else 0.0
                for i in known:
                    known[i] += offset
                runs: dict[int, list[tuple[int, int, int, int]]] = {}
                for i in range(len(rows)):
                    top, bottom = max(0, round(known[i] - 0.5 * pitch)), round(known[i] + 0.5 * pitch)
                    start = round(margin + (12 if rows[i] == "Totals" else len(rows[i]) + 1) * digit)
                    strip = sheared[top:bottom, start:]
                    lit = np.flatnonzero(strip.any(axis=0))
                    runs[i] = []
                    for run in np.split(lit, np.flatnonzero(np.diff(lit) > 2 * digit) + 1):
                        if not len(run):
                            continue
                        rows_lit = np.flatnonzero(strip[:, run[0] : run[-1] + 1].any(axis=1))
                        bands = [
                            band
                            for band in np.split(rows_lit, np.flatnonzero(np.diff(rows_lit) > 1) + 1)
                            if band[-1] - band[0] + 1 >= 0.6 * height
                        ]
                        if not bands:
                            continue
                        band = min(bands, key=lambda band: abs((band[0] + band[-1]) / 2 - (known[i] - top)))
                        low, high = int(band[0]), int(band[-1]) + 1
                        if high - low > 1.4 * height:
                            low, high = round(known[i] - top - height / 2), round(known[i] - top + height / 2)
                        inner = np.flatnonzero(strip[low:high, run[0] : run[-1] + 1].any(axis=0)) + run[0]
                        for piece in np.split(inner, np.flatnonzero(np.diff(inner) > 2 * digit) + 1):
                            parts = [
                                part
                                for part in np.split(piece, np.flatnonzero(np.diff(piece) > 0.3 * digit) + 1)
                                if len(part)
                            ]
                            while (
                                parts
                                and np.ptp(np.flatnonzero(strip[low:high, parts[0][0] : parts[0][-1] + 1].any(axis=1)))
                                < 0.5 * height
                            ):
                                parts.pop(0)
                            if parts and strip[low:high, parts[0][0] : parts[-1][-1] + 1].mean() < 0.7:
                                runs[i].append(
                                    (
                                        int(parts[0][0]) + start,
                                        low + top,
                                        int(parts[-1][-1]) + 1 + start,
                                        high + top,
                                    )
                                )
                edges = sorted(run[2] for found in runs.values() for run in found)
                clusters = [
                    statistics.median(group)
                    for group in np.split(np.array(edges), np.flatnonzero(np.diff(edges) > 1.5 * digit) + 1)
                    if len(group) >= 0.3 * len(rows)
                ]
                grid = {
                    i: [by_key[key] for c in range(12) if (key := (year, page, table["district"], county, c)) in by_key]
                    for i, county in enumerate(rows)
                }
                columns = sorted({cell["column"] for row in grid.values() for cell in row})

                nearest = {
                    (i, k): min(near, key=lambda run: abs(run[2] - edge)) if near else None
                    for i in runs
                    for k, edge in enumerate(clusters)
                    for near in [[run for run in runs[i] if abs(run[2] - edge) < 1.5 * digit]]
                }
                score = {}
                for c in columns:
                    for k in range(len(clusters)):
                        total = 0
                        for i, row in grid.items():
                            cell = next((cell for cell in row if cell["column"] == c), None)
                            if cell is None or (run := nearest[(i, k)]) is None:
                                continue
                            if 0.75 <= (run[2] - run[0]) / (digit * len(str(cell["votes"]))) <= 1.55:
                                total += 1
                            if any(
                                digits == str(cell["votes"])
                                and min(box[2], run[2]) - max(box[0], run[0]) > 0.5 * (box[2] - box[0])
                                and abs(
                                    (box[1] + box[3]) / 2
                                    - slope * ((box[0] + box[2]) / 2 - margin)
                                    - (run[1] + run[3]) / 2
                                )
                                < 0.5 * pitch
                                for digits, box in tokens
                            ):
                                total += 2
                        score[(c, k)] = total
                plan: dict[tuple[int, int], tuple[int, list[int]]] = {}
                for n, c in enumerate(columns):
                    for k in range(len(clusters)):
                        prior = max(
                            (plan[(n - 1, j)] for j in range(k) if (n - 1, j) in plan),
                            key=lambda entry: entry[0],
                            default=(0, []) if n == 0 else None,
                        )
                        if prior is not None:
                            plan[(n, k)] = (prior[0] + score[(c, k)], [*prior[1], k])
                ends = [plan[(len(columns) - 1, k)] for k in range(len(clusters)) if (len(columns) - 1, k) in plan]
                if not ends:
                    continue
                chosen = dict(zip(columns, max(ends, key=lambda entry: entry[0])[1], strict=True))
                for i, row in grid.items():
                    for cell in row:
                        run = nearest[(i, chosen[cell["column"]])]
                        if run is None:
                            right = clusters[chosen[cell["column"]]]
                            printed = len(f"{cell['votes']:,}" if year in COMMAS_YEARS else str(cell["votes"]))
                            run = (
                                round(right - digit * printed),
                                round(known[i] - height / 2),
                                round(right),
                                round(known[i] + height / 2),
                            )
                        shift = slope * ((run[0] + run[2]) / 2 - margin)
                        box = [run[0], round(run[1] + shift), run[2], round(run[3] + shift)]
                        old = cell["box"]
                        if (
                            old
                            and min(old[2], box[2]) - max(old[0], box[0]) > 0.5 * (box[2] - box[0])
                            and min(old[3], box[3]) - max(old[1], box[1]) > 0.5 * (box[3] - box[1])
                        ):
                            continue
                        hit = [
                            token
                            for digits, token in tokens
                            if digits == str(cell["votes"])
                            and min(token[2], box[2]) - max(token[0], box[0]) > 0.5 * (token[2] - token[0])
                            and min(token[3], box[3]) - max(token[1], box[1]) > 0.5 * (token[3] - token[1])
                        ]
                        cell["box"] = [round(v) for v in hit[0]] if hit else box
                        cell["located"] = "aligned" if hit else "placed"
                sure = [
                    (i, cell) for i, row in grid.items() for cell in row if cell["located"] != "placed" and cell["box"]
                ]
                if not sure:
                    continue
                centers = {
                    i: statistics.median(
                        (cell["box"][1] + cell["box"][3]) / 2 - slope * ((cell["box"][0] + cell["box"][2]) / 2 - margin)
                        for k, cell in sure
                        if k == i
                    )
                    for i in {k for k, _ in sure}
                }
                for i in grid:
                    if i not in centers:
                        below, above = [k for k in centers if k < i], [k for k in centers if k > i]
                        centers[i] = (
                            centers[max(below)]
                            + (centers[min(above)] - centers[max(below)]) * (i - max(below)) / (min(above) - max(below))
                            if below and above
                            else centers[max(below)] + pitch * (i - max(below))
                            if below
                            else centers[min(above)] - pitch * (min(above) - i)
                        )
                edges = {
                    c: statistics.median(cell["box"][2] for _, cell in sure if cell["column"] == c)
                    if any(cell["column"] == c for _, cell in sure)
                    else clusters[chosen[c]]
                    for c in columns
                }
                for i, row in grid.items():
                    for cell in row:
                        if cell["located"] != "placed":
                            continue
                        x0, y0, x1, y1 = cell["box"]
                        middle = (y0 + y1) / 2 - slope * ((x0 + x1) / 2 - margin)
                        if abs(middle - centers[i]) < 0.35 * pitch and abs(x1 - edges[cell["column"]]) < 1.5 * digit:
                            continue
                        edge = edges[cell["column"]]
                        printed = len(f"{cell['votes']:,}" if year in COMMAS_YEARS else str(cell["votes"]))
                        top = max(0, round(centers[i] - 0.5 * pitch))
                        left = max(0, round(edge - (printed + 2) * digit))
                        window = sheared[top : round(centers[i] + 0.5 * pitch), left : round(edge + 1.5 * digit)]
                        rows_lit = np.flatnonzero(window.any(axis=1))
                        bands = [
                            band
                            for band in np.split(rows_lit, np.flatnonzero(np.diff(rows_lit) > 1) + 1)
                            if len(band) and band[-1] - band[0] + 1 >= 0.6 * height
                        ]
                        box = [
                            round(edge - printed * digit),
                            round(centers[i] - height / 2),
                            round(edge),
                            round(centers[i] + height / 2),
                        ]
                        if bands:
                            band = min(bands, key=lambda band: abs((band[0] + band[-1]) / 2 + top - centers[i]))
                            low, high = int(band[0]), int(band[-1]) + 1
                            if high - low > 1.4 * height:
                                low, high = round(centers[i] - top - height / 2), round(centers[i] - top + height / 2)
                            lit = np.flatnonzero(window[low:high].any(axis=0))
                            pieces = [
                                piece
                                for piece in np.split(lit, np.flatnonzero(np.diff(lit) > 2 * digit) + 1)
                                if len(piece)
                            ]
                            if pieces:
                                piece = min(pieces, key=lambda piece: abs(piece[-1] + left - edge))
                                box = [int(piece[0]) + left, low + top, int(piece[-1]) + 1 + left, high + top]
                        shift = slope * ((box[0] + box[2]) / 2 - margin)
                        cell["box"] = [box[0], round(box[1] + shift), box[2], round(box[3] + shift)]
            for table in on_page:
                rows = table["rows"]
                if len(rows) == 2 and rows[1] == "Totals" and (id(table), 0) in lines and (id(table), 1) in lines:
                    own, total = lines[(id(table), 0)], lines[(id(table), 1)]
                    for cell in [
                        by_key[key] for c in range(12) if (key := (year, page, table["district"], rows[0], c)) in by_key
                    ]:
                        x0, y0, x1, y1 = cell["box"]
                        middle = (y0 + y1) / 2 - slope * ((x0 + x1) / 2 - margin)
                        if abs(middle - own) <= abs(middle - total):
                            continue
                        top, left = max(0, round(own - 0.6 * pitch)), max(0, round(x0 - 2 * digit))
                        window = sheared[top : round(own + 0.6 * pitch), left : round(x1 + 2 * digit)]
                        rows_lit = np.flatnonzero(window.any(axis=1))
                        bands = [
                            band
                            for band in np.split(rows_lit, np.flatnonzero(np.diff(rows_lit) > 1) + 1)
                            if len(band) and band[-1] - band[0] + 1 >= 0.6 * height
                        ]
                        if not bands:
                            continue
                        band = min(bands, key=lambda band: abs((band[0] + band[-1]) / 2 + top - own))
                        lit = np.flatnonzero(window[band[0] : band[-1] + 1].any(axis=0))
                        piece = max(
                            (
                                piece
                                for piece in np.split(lit, np.flatnonzero(np.diff(lit) > 2 * digit) + 1)
                                if len(piece)
                            ),
                            key=lambda piece: min(piece[-1] + left, x1) - max(piece[0] + left, x0),
                        )
                        shift = slope * ((piece[0] + piece[-1]) / 2 + left - margin)
                        cell["box"] = [
                            int(piece[0]) + left,
                            round(band[0] + top + shift),
                            int(piece[-1]) + 1 + left,
                            round(band[-1] + 1 + top + shift),
                        ]
                        cell["located"] = "placed"
            for (when, district, county, column), (x, y) in ANCHORS.items():
                cell = by_key.get((year, page, district, county, column))
                if when != year or cell is None:
                    continue
                window = ink[
                    round(y - 0.6 * pitch) : round(y + 0.6 * pitch), round(x - 5 * digit) : round(x + 5 * digit)
                ]
                rows_lit = np.flatnonzero(window.any(axis=1))
                band = min(
                    (
                        band
                        for band in np.split(rows_lit, np.flatnonzero(np.diff(rows_lit) > 1) + 1)
                        if len(band) and band[-1] - band[0] + 1 >= 0.6 * height
                    ),
                    key=lambda band: abs((band[0] + band[-1]) / 2 - 0.6 * pitch),
                )
                lit = np.flatnonzero(window[band[0] : band[-1] + 1].any(axis=0))
                piece = min(
                    (piece for piece in np.split(lit, np.flatnonzero(np.diff(lit) > 2 * digit) + 1) if len(piece)),
                    key=lambda piece: abs((piece[0] + piece[-1]) / 2 - 5 * digit),
                )
                left, top = round(x - 5 * digit), round(y - 0.6 * pitch)
                cell["box"] = [
                    int(piece[0]) + left,
                    int(band[0]) + top,
                    int(piece[-1]) + 1 + left,
                    int(band[-1]) + 1 + top,
                ]
                cell["located"] = "placed"
    frame = pl.DataFrame(cells, infer_schema_length=None)
    print(frame.group_by("located").len().sort("located"))
    frame.write_parquet(path)


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
