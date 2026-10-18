import difflib
import math
import statistics
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import TypedDict

import numpy as np
import polars as pl
from PIL import Image

from ea.sources.us import house_books
from ea.store import DATA

SOURCE = "us.mi_sos"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Michigan Secretary of State"
TITLE = "State of Michigan General Election, {day:%B %-d, %Y}: Official Canvass"
LICENSE = "Public record"
SUMMARY = "Michigan's House results by county from the Secretary of State's official canvass"
STATE = "MI"
FIPS = "26"
ITEMS = {
    1980: ("micro_IA40706905_0413", date(1980, 11, 4), range(7, 14)),
    1982: ("micro_IA40706913_0052", date(1982, 11, 2), range(11, 19)),
    1984: ("micro_IA40706919_0158", date(1984, 11, 6), range(29, 36)),
    1986: ("micro_IA40706925_0347", date(1986, 11, 4), range(29, 34)),
    1988: ("micro_IA40706932_0179", date(1988, 11, 8), range(9, 14)),
    1990: ("micro_IA40706939_0062", date(1990, 11, 6), range(6, 10)),
    1992: ("micro_IA40706945_0442", date(1992, 11, 3), range(5, 9)),
    1994: ("micro_IA40706952_0453", date(1994, 11, 8), range(14, 21)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA: set[tuple[int, int]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


class _Cell(TypedDict):
    county: str
    column: int
    votes: int
    located: str
    box: list[int]
    right: float
    middle: float


def _turn(x: float, y: float, theta: float, width: int, height: int) -> tuple[float, float]:
    r = math.radians(theta)
    return (
        width / 2 + (x - width / 2) * math.cos(r) + (y - height / 2) * math.sin(r),
        height / 2 - (x - width / 2) * math.sin(r) + (y - height / 2) * math.cos(r),
    )


def _match(
    ink: np.ndarray, band: int, pitch: float, line: list[_Cell], y: int, expect: dict[int, float], strict: bool
) -> list[tuple[float, float] | None] | None:
    strip = ink[y - band // 2 : y + band // 2 + 1]
    third = strip.shape[0] // 3
    loose = np.zeros(strip.shape[1], bool)
    tall = np.zeros(strip.shape[1], bool)
    for x in np.flatnonzero(strip.any(axis=0)):
        ys = np.flatnonzero(strip[:, x])
        tall[x] = ys[-1] - ys[0] >= 0.5 * band
        loose[x] = ys[-1] - ys[0] >= 0.35 * band or ys.size >= 0.3 * band
    ends = strip[:third].any(axis=0) & strip[-third:].any(axis=0)
    for trim, mask in ((False, loose), (False, ends), (False, tall), (True, ends)):
        runs: list[tuple[int, int]] = []
        for x in np.flatnonzero(mask):
            if runs and x - runs[-1][1] <= 2.2 * pitch:
                runs[-1] = (runs[-1][0], int(x) + 1)
            else:
                runs.append((int(x), int(x) + 1))
        runs = [r for r in runs if r[0] >= 60]
        cost = [[math.inf] * (len(runs) + 1) for _ in range(len(line) + 1)]
        back: dict[tuple[int, int], int] = {}
        cost[0] = [0.0] * (len(runs) + 1)
        for i, cell in enumerate(line, 1):
            chars = len(f"{cell['votes']:,}")
            spare = 80 if strict else 40 if cell["votes"] == 0 else math.inf
            cost[i][0] = cost[i - 1][0] + spare
            back[(i, 0)] = 2
            for j in range(1, len(runs) + 1):
                s0, s1 = runs[j - 1]
                wide = (s1 - s0) / pitch + 0.3
                off = abs(s1 - expect[cell["column"]])
                fit = (
                    math.inf
                    if strict and off > 2.5 * pitch
                    else off + 20 * abs(wide - chars)
                    if abs(wide - chars) < 1.2
                    else 60 + off
                    if trim and chars < wide < chars + 3
                    else math.inf
                )
                moves = [(cost[i - 1][j - 1] + fit, 1), (cost[i][j - 1], 0), (cost[i - 1][j] + spare, 2)]
                cost[i][j], back[(i, j)] = min(moves)
        if cost[-1][-1] < math.inf:
            found: list[tuple[float, float] | None] = [None] * len(line)
            i, j = len(line), len(runs)
            while i > 0:
                move = back[(i, j)]
                if move == 1:
                    s0, s1 = runs[j - 1]
                    if trim:
                        s0 = max(s0, s1 - (len(f"{line[i - 1]['votes']:,}") - 0.3) * pitch)
                    found[i - 1] = (s0, s1)
                    i -= 1
                    j -= 1
                elif move == 2:
                    i -= 1
                else:
                    j -= 1
            return found
    return None


def parse() -> None:
    house_books.parse(sys.modules[__name__])
    parsed = DATA / "parsed" / SOURCE
    frame = pl.read_parquet(parsed / "values.parquet")
    relocated: dict[tuple[int, int, str, int], list[int]] = {}
    with tempfile.TemporaryDirectory() as folder:
        pitches: dict[tuple[int, int], list[float]] = {}
        heights: dict[int, list[int]] = {}
        for (year, page), sheet in frame.group_by("year", "page"):
            book = house_books._artifact(sys.modules[__name__], int(year))
            image = Path(folder) / f"{year}-{page}.png"
            house_books._render(book, int(page), 300, image, False)
            gray = np.asarray(Image.open(image).convert("L")).astype(float)
            ink = gray < np.median(gray) - 70
            for row in sheet.filter((pl.col("located") == "unique") & (pl.col("votes") >= 1000)).iter_rows(named=True):
                x0, y0, x1, y1 = row["box"]
                heights.setdefault(int(year), []).append(y1 - y0)
                xs = np.flatnonzero(ink[y0 + 3 : y1 - 3, max(0, x0 - 60) : x1 + 60].sum(axis=0) >= 3)
                if xs.size == 0:
                    continue
                start = last = xs[0]
                for x in [*xs[1:], 10**6]:
                    if x - last > 30:
                        if abs(last + max(0, x0 - 60) - x1) < 12:
                            pitches.setdefault((int(year), int(page)), []).append(
                                (last - start + 1) / (len(f"{row['votes']:,}") - 0.3)
                            )
                        start = x
                    last = x
        steps: dict[tuple[int, int], list[float]] = {}
        for (year, page, _), table in frame.group_by("year", "page", "district"):
            order = list(dict.fromkeys(table["county"]))
            if len(order) >= 3:
                spots = [
                    (order.index(r["county"]), (r["box"][1] + r["box"][3]) / 2) for r in table.iter_rows(named=True)
                ]
                steps.setdefault((int(year), int(page)), []).append(
                    statistics.median((b - a) / (j - i) for i, a in spots for j, b in spots if j > i)
                )
        for (year, page, district), table in frame.group_by("year", "page", "district"):
            year, page = int(year), int(page)
            widths = pitches.get((year, page), [])
            pitch = statistics.median(
                widths if len(widths) >= 5 else [w for (y, _), ws in pitches.items() if y == year for w in ws]
            )
            book = house_books._artifact(sys.modules[__name__], year)
            gray = np.asarray(Image.open(Path(folder) / f"{year}-{page}.png").convert("L")).astype(float)
            ink = gray < np.median(gray) - 70
            height, width = ink.shape
            spread = np.array(table["box"].to_list())
            top, bottom = max(0, int(spread[:, 1].min() - 60)), int(spread[:, 3].max() + 60)
            small = Image.fromarray(ink[top:bottom, : int(spread[:, 2].max() + 60)][::2, ::2].astype(np.uint8) * 255)
            theta = float(
                max(
                    np.arange(-3, 3.01, 0.1),
                    key=lambda th: float(
                        np.var((np.asarray(small.rotate(float(th), resample=Image.Resampling.NEAREST)) > 0).sum(axis=1))
                    ),
                )
            )

            ink = (
                np.asarray(
                    Image.fromarray(ink.astype(np.uint8) * 255).rotate(
                        theta, center=(width / 2, height / 2), resample=Image.Resampling.NEAREST
                    )
                )
                > 0
            )
            for y in range(height):
                edges = np.flatnonzero(np.diff(np.concatenate(([0], ink[y].astype(int), [0]))))
                for s, e in zip(edges[::2], edges[1::2], strict=True):
                    if e - s > 4 * pitch:
                        ink[y, s:e] = False
            cells: list[_Cell] = []
            for row in table.iter_rows(named=True):
                x0, y0, x1, y1 = row["box"]
                cx, cy = _turn((x0 + x1) / 2, (y0 + y1) / 2, theta, width, height)
                cells.append(
                    _Cell(
                        county=row["county"],
                        column=row["column"],
                        votes=row["votes"],
                        located=row["located"],
                        box=row["box"],
                        right=cx + (x1 - x0) / 2,
                        middle=cy,
                    )
                )
            band = int(statistics.median(heights[year]))
            counties = list(dict.fromkeys(cell["county"] for cell in cells))
            step = statistics.median(
                steps.get((year, page)) or [s for (y, _), found in steps.items() if y == year for s in found]
            )
            base = statistics.median(cell["middle"] - step * counties.index(cell["county"]) for cell in cells)
            first = min(
                statistics.median(r["box"][0] for r in cells if r["column"] == c) for c in {r["column"] for r in cells}
            )
            words = []
            for tsv in (parsed / "ocr").glob(f"{book.sha256}-{page}-300-*.tsv"):
                for line in tsv.read_text().splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) == 12 and parts[11].strip():
                        wx, wy = _turn(int(parts[6]), int(parts[7]) + int(parts[9]) / 2, theta, width, height)
                        words.append((house_books._plain(parts[11]), wx, wy))
            options = []
            for i, county in enumerate(counties):
                key = max((house_books._plain(w) for w in county.split()), key=len)
                prior = base + step * i
                seen = sorted(
                    [
                        (y, -8.0)
                        for w, x, y in words
                        if x < first - 20 and len(w) >= 3 and difflib.SequenceMatcher(None, w, key).ratio() >= 0.75
                    ]
                    + [
                        (r["middle"], -20.0 if r["located"] == "unique" else -10.0)
                        for r in cells
                        if r["county"] == county and r["located"] != "placed"
                    ]
                    + [(prior, 0.0)]
                )
                groups: list[list[float]] = []
                weights: list[float] = []
                for y, w in seen:
                    if groups and y - groups[-1][-1] < 8:
                        groups[-1].append(y)
                        weights[-1] += w
                    else:
                        groups.append([y])
                        weights.append(w)
                options.append(
                    [
                        (statistics.median(g), w + 0.05 * abs(statistics.median(g) - prior))
                        for g, w in zip(groups, weights, strict=True)
                    ]
                )
            best: list[dict[int, tuple[float, int]]] = []
            for i, choices in enumerate(options):
                best.append({})
                for k, (y, own) in enumerate(choices):
                    if i == 0:
                        best[i][k] = (own, -1)
                        continue
                    paths = [
                        (cost + own + 0.3 * abs(y - options[i - 1][pk][0] - step), pk)
                        for pk, (cost, _) in best[i - 1].items()
                        if y - options[i - 1][pk][0] > 0.5 * step
                    ]
                    if paths:
                        best[i][k] = min(paths)
            pick = min(best[-1], key=lambda k: best[-1][k][0])
            rows: dict[str, float] = {}
            for i in range(len(counties) - 1, -1, -1):
                rows[counties[i]] = options[i][pick][0]
                pick = best[i][pick][1]
            low, high = int(spread[:, 0].min() - 2 * pitch), int(spread[:, 2].max() + 2 * pitch)
            reach = max(4, int(abs(step) / 3))
            for county, y in rows.items():
                rows[county] = max(
                    range(int(y) - reach, int(y) + reach + 1),
                    key=lambda yy: (
                        ink[yy - band // 3 : yy + band // 3 + 1, low:high].sum()
                        - 3 * ink[yy - band // 2 - 2 : yy - band // 2 + 1, low:high].sum()
                        - 3 * ink[yy + band // 2 : yy + band // 2 + 3, low:high].sum()
                    ),
                )

            lines = {
                county: sorted((r for r in cells if r["county"] == county), key=lambda r: r["column"])
                for county in counties
            }
            first_pass = {
                county: _match(
                    ink, band, pitch, line, int(rows[county]), {r["column"]: r["right"] for r in line}, False
                )
                for county, line in lines.items()
            }
            columns: dict[int, list[float]] = {}
            for county, found in first_pass.items():
                for cell, spot in zip(lines[county], found or [], strict=False):
                    if spot is not None:
                        columns.setdefault(cell["column"], []).append(spot[1])
            settled = [(counties.index(c), rows[c]) for c, found in first_pass.items() if found is not None]
            slopes = [(b - a) / (j - i) for i, a in settled for j, b in settled if j > i]
            if slopes:
                pace = statistics.median(slopes)
                offset = statistics.median(y - pace * i for i, y in settled)
                for i, county in enumerate(counties):
                    if abs(rows[county] - offset - pace * i) > 0.35 * pace:
                        rows[county] = offset + pace * i
            consensus = {
                column: min(
                    spots,
                    key=lambda s: (
                        -sum(abs(s - o) < 1.5 * pitch for o in spots),
                        abs(s - statistics.median(r["right"] for r in cells if r["column"] == column)),
                    ),
                )
                for column, spots in columns.items()
            }
            for county, line in lines.items():
                expect = {r["column"]: consensus.get(r["column"], r["right"]) for r in line}
                found = _match(ink, band, pitch, line, int(rows[county]), expect, True)
                if found is None:
                    print(f"{year} district {district} {county}: kept the parsed boxes")
                    continue
                for cell, spot in zip(line, found, strict=True):
                    if spot is None:
                        right = expect[cell["column"]]
                        spot = (right - (len(f"{cell['votes']:,}") - 0.3) * pitch, right)
                    corners = [
                        _turn(x, y, -theta, width, height)
                        for x in (spot[0] - 3, spot[1] + 3)
                        for y in (rows[county] - band // 2, rows[county] + band // 2)
                    ]
                    relocated[(year, int(district), county, cell["column"])] = [
                        round(min(c[0] for c in corners)),
                        round(min(c[1] for c in corners)),
                        round(max(c[0] for c in corners)),
                        round(max(c[1] for c in corners)),
                    ]
    frame = frame.with_columns(
        pl.Series(
            "box",
            [
                relocated.get((row["year"], row["district"], row["county"], row["column"]), row["box"])
                for row in frame.iter_rows(named=True)
            ],
            dtype=pl.List(pl.Int64),
        )
    )
    frame.write_parquet(parsed / "values.parquet")


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
