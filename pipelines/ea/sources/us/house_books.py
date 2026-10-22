import csv
import difflib
import io
import re
import statistics
import subprocess
import tempfile
import unicodedata
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

DIGITIZED = 8
DERIVED = 1


def _artifact(book: ModuleType, year: int) -> raw.Artifact:
    url = book.VOLUMES[year][0]
    try:
        return raw.cached(book.RAW, url)
    except LookupError:
        return raw.fetch(book.RAW, url, suffix=".pdf")


def fetch(book: ModuleType) -> None:
    for year in book.VOLUMES:
        _artifact(book, year)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def _render(book: raw.Artifact, page: int, dpi: int, image: Path, turned: bool) -> None:
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
        + [str(book.path), str(image.with_suffix(""))],
        check=True,
    )
    if turned:
        with Image.open(image) as rendered:
            rendered.rotate(-90, expand=True).save(image)


def _tables(path: Path) -> list[dict[str, object]]:
    tables: list[dict[str, object]] = []
    page = 0
    state = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            page = int(line[1:])
            continue
        if line.startswith("#"):
            state = line[1:].strip()
            continue
        head, rest = (part.strip() for part in line.split("|", 1))
        if head.isdigit():
            tables.append(
                {
                    "page": page,
                    "state": state,
                    "district": int(head),
                    "columns": [c.strip() for c in rest.split(",")],
                    "rows": [],
                }
            )
            continue
        votes = [int(cell) for cell in rest.split()]
        if len(votes) != len(tables[-1]["columns"]):
            raise ValueError(f"{path.name}: {line}")
        tables[-1]["rows"].append((head, votes))
    return tables


def parse(source: ModuleType) -> None:
    parsed = raw.parsed(source.SOURCE)
    rows: list[dict[str, object]] = []
    failed: list[str] = []
    columns_by: dict[tuple[int, object, int], list[int]] = {}
    printed: dict[tuple[int, object, int], int] = {}
    labels: dict[tuple[int, object], list[str]] = {}
    steps: list[float] = []
    carried: list[float] = []
    for year, (_, _, _, pages) in source.VOLUMES.items():
        book = _artifact(source, year)
        learned: list[tuple[float, float, float, float]] = []
        carried, steps = steps or carried, []
        spans: list[float] = []
        firsts = _tables(parsed / "model" / f"{year}.txt")
        seconds = _tables(parsed / "model" / f"{year}.v2.txt")
        if [(t["page"], t["state"], t["district"], t["columns"], [r[0] for r in t["rows"]]) for t in firsts] != [
            (t["page"], t["state"], t["district"], t["columns"], [r[0] for r in t["rows"]]) for t in seconds
        ]:
            raise ValueError(f"{year}: the two reads lay out different tables")
        for page in pages:
            tokens: list[tuple[str, tuple[int, int, int, int]]] = []
            words: list[tuple[str, tuple[int, int, int, int]]] = []
            for dpi, psm, threshold in (
                (300, "6", None),
                (300, "11", None),
                (600, "6", None),
                (600, "11", None),
                *((300, psm, threshold) for threshold in source.THRESHOLDS for psm in ("6", "11")),
            ):
                cache = (
                    parsed
                    / "ocr"
                    / f"{book.sha256}-{page}-{dpi}-{psm}{'' if threshold is None else f'-{threshold}'}.tsv"
                )
                if not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as folder:
                        image = Path(folder) / "page.png"
                        _render(book, page, dpi, image, (year, page) in source.TURNED)
                        if threshold is not None:
                            with Image.open(image) as rendered:
                                rendered.point(lambda v, cut=threshold: 255 if v > cut else 0).save(image)
                        cache.write_text(
                            subprocess.run(
                                ["tesseract", str(image), "stdout", "--psm", psm, "tsv"],
                                capture_output=True,
                                text=True,
                                check=True,
                            ).stdout
                        )
                scale = dpi // 300
                previous: tuple[tuple[str, ...], str, tuple[int, int, int, int]] | None = None
                for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
                    if row["level"] != "5":
                        continue
                    box = (
                        int(row["left"]) // scale,
                        int(row["top"]) // scale,
                        (int(row["left"]) + int(row["width"])) // scale,
                        (int(row["top"]) + int(row["height"])) // scale,
                    )
                    line = (row["block_num"], row["par_num"], row["line_num"])
                    if dpi == 300 and (plain := _plain(row["text"])):
                        words.append((plain, box))
                        if previous is not None and previous[0] == line:
                            words.append(
                                (
                                    previous[1] + plain,
                                    (previous[2][0], min(previous[2][1], box[1]), box[2], max(previous[2][3], box[3])),
                                )
                            )
                        previous = (line, plain, box)
                    if not re.search(r"\d", row["text"]):
                        continue
                    for digits in {
                        re.sub(r"\D", "", row["text"]),
                        re.sub(r"\D", "", row["text"].translate(scan.CONFUSABLE)),
                    }:
                        tokens.append((digits, box))
            reach: list[tuple[float, float, float]] = []
            pitch: list[float] = []
            grid: dict[int, list[float]] = {}
            for table, second in sorted(
                ((table, second) for table, second in zip(firsts, seconds, strict=True) if table["page"] == page),
                key=lambda pair: sum(county != "Totals" for county, _ in pair[0]["rows"]) == 1,
            ):
                columns = table["columns"]
                where = table["district"] if table["state"] is None else (table["state"], table["district"])
                known = labels.setdefault((year, where), [])
                known.extend(label for label in columns if label not in known)
                cells = [
                    (r, c, county, value, again[c])
                    for r, ((county, votes), (_, again)) in enumerate(zip(table["rows"], second["rows"], strict=True))
                    for c, value in enumerate(votes)
                ]
                lines = [r for r, (county, _) in enumerate(table["rows"]) if county != "Totals"]
                total = next((r for r, (county, _) in enumerate(table["rows"]) if county == "Totals"), None)
                settled: dict[tuple[int, int], int] = {(r, c): v for r, c, _, v, w in cells if v == w}
                for r, c, _, value, again in cells:
                    if (r, c) not in settled:
                        failed.append(f"{year} district {where} row {r} column {c}: {value} or {again}")
                for r in range(len(table["rows"])):
                    top = sorted((settled[(r, k)] for k in range(1, len(columns)) if (r, k) in settled), reverse=True)
                    if (
                        (year, where) not in source.ERRATA
                        and columns[0] == "Plurality"
                        and (r, 0) in settled
                        and len(top) == len(columns) - 1
                        and settled[(r, 0)] not in (top[0], top[0] - (top[1] if len(top) > 1 else 0))
                    ):
                        failed.append(f"{year} district {where} row {r}: the plurality does not fit")
                for c in range(int(columns[0] == "Plurality"), len(columns)):
                    column = columns_by.setdefault((year, where, known.index(columns[c])), [])
                    column.extend(settled.get((k, c), -(10**9)) for k in lines)
                    if total is not None:
                        printed[(year, where, known.index(columns[c]))] = settled.get((total, c), -1)
                if year in getattr(source, "TRANSPOSED", ()):
                    for r, c, county, value, again in cells:
                        if r == total or (r, c) not in settled:
                            continue
                        votes = settled[(r, c)]
                        text = str(votes)
                        names = [
                            k
                            for name, k in words
                            if name == _plain(columns[c]) or _plain(columns[c]).startswith(name) and len(name) >= 5
                        ]
                        heads_ = [
                            k
                            for name, k in words
                            if name == _plain(county) or (len(name) >= 3 and _plain(county).startswith(name))
                        ]
                        hits = []
                        for digits, b in tokens:
                            if digits != text:
                                continue
                            if not any(abs((b[1] + b[3]) / 2 - (k[1] + k[3]) / 2) < 14 for k in names):
                                continue
                            head = [
                                k
                                for k in heads_
                                if 0 < b[1] - k[3] < 700 and abs((b[0] + b[2]) / 2 - (k[0] + k[2]) / 2) < 140
                            ]
                            if head:
                                hits.append((min(b[1] - k[3] for k in head), b))
                        boxes_ = list(dict.fromkeys(b for _, b in sorted(hits)))
                        box = boxes_[0] if boxes_ else None
                        how = "unique" if len(boxes_) == 1 else "aligned"
                        if box is None and names and heads_:
                            head = min(heads_, key=lambda k: abs((k[1] + k[3]) / 2 - (names[0][1] + names[0][3]) / 2))
                            line = min(names, key=lambda k: abs(k[1] - head[3]) if k[1] > head[3] else 10**6)
                            box = (head[0], line[1], head[2], line[3])
                            how = "placed"
                        if box is None and names:
                            box = tuple(names[0])
                            how = "placed"
                        if box is None and heads_:
                            box = tuple(heads_[0])
                            how = "placed"
                        if box is None and tokens:
                            box = tuple(min(tokens, key=lambda token: token[1][1])[1])
                            how = "placed"
                        if box is None and heads_:
                            box = tuple(heads_[0])
                            how = "placed"
                        if box is None and tokens:
                            box = tuple(min(tokens, key=lambda token: token[1][1])[1])
                            how = "placed"
                        rows.append(
                            {
                                "year": year,
                                "page": page,
                                "state": table["state"] or source.STATE,
                                "district": table["district"],
                                "county": county,
                                "column": known.index(columns[c]),
                                "label": columns[c],
                                "votes": votes,
                                "box": None if box is None else list(box),
                                "located": how,
                            }
                        )
                    continue
                found = {
                    (r, c): list(
                        dict.fromkeys(
                            box
                            for digits, box in sorted(tokens, key=lambda token: token[1][2] - token[1][0])
                            if digits == str(settled[(r, c)])
                        )
                    )
                    for r, c, *_ in cells
                    if r != total and (r, c) in settled
                }
                boxes: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
                for key, candidates in found.items():
                    merged: list[tuple[int, int, int, int]] = []
                    for box in candidates:
                        if not any(abs(box[0] - b[0]) < 15 and abs(box[1] - b[1]) < 15 for b in merged):
                            merged.append(box)
                    boxes[key] = merged
                anchors = {key: merged[0] for key, merged in boxes.items() if len(merged) == 1}
                for r in lines:
                    same = [c for c in range(len(columns)) if (r, c) in settled]
                    for value in {settled[(r, c)] for c in same}:
                        repeated = [c for c in same if settled[(r, c)] == value]
                        if len(repeated) > 1 and len(boxes[(r, repeated[0])]) == len(repeated):
                            for c, box in zip(repeated, sorted(boxes[(r, repeated[0])]), strict=True):
                                anchors[(r, c)] = box
                if len(lines) == 1:
                    same = [c for c in range(len(columns)) if (lines[0], c) in settled]
                    for value in {settled[(lines[0], c)] for c in same}:
                        repeated = [c for c in same if settled[(lines[0], c)] == value]
                        found_ = boxes.get((lines[0], repeated[0]), [])
                        if len(found_) < 2:
                            continue
                        lowest = max(b[1] for b in found_)
                        bottom = sorted(b for b in found_ if lowest - b[1] < 12)
                        if len(bottom) == len(repeated):
                            for c, box in zip(repeated, bottom, strict=True):
                                anchors[(lines[0], c)] = box
                heads = {
                    c: min(pairs)[1]
                    for c in range(len(columns))
                    if (len(columns) > 1 or getattr(source, "SINGLE_HEADS", False))
                    and (
                        pairs := [
                            (k[1] - h[3], h)
                            for name, h in words
                            if name == _plain(columns[c])
                            or (len(name) >= 5 and _plain(columns[c]).startswith(name))
                            or name in source.ALIASES.get(columns[c], ())
                            for place, k in words
                            if place == _plain(table["rows"][lines[0]][0])
                            and 0 < k[1] - h[3] < 400
                            and not any(
                                k[0] + 50 < b[0] < h[0] and abs(b[1] - h[1]) < 200
                                for name_, b in words
                                if name_ in source.BESIDE
                            )
                        ]
                    )
                }
                anchors = {
                    (r, c): b
                    for (r, c), b in anchors.items()
                    if c not in heads or (b[2] > heads[c][0] - 60 and b[0] < heads[c][2] + 150)
                }
                for _ in range(2):
                    if not anchors:
                        break
                    ys = {
                        r: statistics.median((b[1] + b[3]) / 2 for (rr, _), b in anchors.items() if rr == r)
                        for r in {r for r, _ in anchors}
                    }
                    xs = {
                        c: statistics.median(b[2] for (_, cc), b in anchors.items() if cc == c)
                        for c in {c for _, c in anchors}
                    }
                    anchors = {
                        (r, c): b
                        for (r, c), b in anchors.items()
                        if abs((b[1] + b[3]) / 2 - ys[r]) < 12 and abs(b[2] - xs[c]) < 40
                    }
                if not anchors:
                    for r, c, county, *_ in cells:
                        near = {
                            b
                            for b in boxes.get((r, c), [])
                            for name, k in words
                            if name == _plain(county)
                            and abs((k[1] + k[3]) / 2 - (b[1] + b[3]) / 2) < 12
                            and 0 < b[0] - k[2] < 700
                            and any(name_ == _plain(columns[c]) and h[3] < k[1] < h[3] + 1200 for name_, h in words)
                        }
                        if len(near) == 1:
                            anchors[(r, c)] = near.pop()
                guessed = set()
                if not anchors and reach:
                    offset = statistics.median(r_[0] for r_ in reach)
                    for r, c, county, *_ in cells:
                        if r == total or (r, c) not in settled:
                            continue
                        heads_ = [h for name, h in words if name == _plain(columns[c])]
                        near = sorted(
                            (k[1] - h[3], k)
                            for name, k in words
                            if name == _plain(county)
                            for h in heads_
                            if 0 < k[1] - h[3] < 1200 and abs(k[0] - h[0]) < 150
                        )
                        if near:
                            k = near[0][1]
                            width = statistics.median(r_[2] for r_ in reach) * len(
                                f"{settled[(r, c)]:,}" if source.COMMAS else str(settled[(r, c)])
                            )
                            middle, half = (k[1] + k[3]) / 2, statistics.median(r_[1] for r_ in reach) / 2
                            anchors[(r, c)] = (
                                round(k[0] + offset - width),
                                round(middle - half),
                                round(k[0] + offset),
                                round(middle + half),
                            )
                            guessed.add((r, c))
                if not anchors and reach:
                    for r, c, county, *_ in cells:
                        if r == total or (r, c) not in settled or c not in grid or c not in heads:
                            continue
                        below = [k for name, k in words if name == _plain(county) and 0 < k[1] - heads[c][3] < 1200]
                        if below:
                            k = min(below, key=lambda k: k[1])
                            right = statistics.median(grid[c])
                            width = statistics.median(r_[2] for r_ in reach) * len(
                                f"{settled[(r, c)]:,}" if source.COMMAS else str(settled[(r, c)])
                            )
                            middle, half = (k[1] + k[3]) / 2, statistics.median(r_[1] for r_ in reach) / 2
                            anchors[(r, c)] = (
                                round(right - width),
                                round(middle - half),
                                round(right),
                                round(middle + half),
                            )
                            guessed.add((r, c))
                if not anchors and learned and steps:
                    for c, column in enumerate(columns):
                        spots: list[tuple[int, int, int, int]] = []
                        for name, h in words:
                            if name == _plain(column) and not any(
                                abs(h[0] - spot[0]) < 40 and abs(h[1] - spot[1]) < 40 for spot in spots
                            ):
                                spots.append(h)
                        if len(spots) != 1 and c in heads:
                            spots = [heads[c]]
                        if len(spots) != 1:
                            continue
                        for i, r in enumerate(lines):
                            if (r, c) not in settled:
                                continue
                            right = spots[0][0] + statistics.median(item[0] for item in learned)
                            middle = (
                                spots[0][3]
                                + statistics.median(item[1] for item in learned)
                                + i * statistics.median(steps)
                            )
                            half = statistics.median(item[2] for item in learned) / 2
                            anchors[(r, c)] = (
                                round(
                                    right
                                    - statistics.median(item[3] for item in learned)
                                    * len(f"{settled[(r, c)]:,}" if source.COMMAS else str(settled[(r, c)]))
                                ),
                                round(middle - half),
                                round(right),
                                round(middle + half),
                            )
                            guessed.add((r, c))
                if not anchors:
                    given = getattr(source, "ANCHORS", {}).get((page, where), {})
                    anchors.update(given)
                    guessed.update(given)
                if not anchors:
                    raise ValueError(f"{year} page {page} district {where}: no cell found on the page")
                for (r, c), b in anchors.items():
                    lefts = [
                        k
                        for name, k in words
                        if name == _plain(table["rows"][r][0])
                        and k[2] < b[0]
                        and abs((k[1] + k[3]) / 2 - (b[1] + b[3]) / 2) < 12
                    ]
                    if lefts and (r, c) not in guessed:
                        k = max(lefts, key=lambda k: k[2])
                        reach.append(
                            (
                                b[2] - k[0],
                                b[3] - b[1],
                                (b[2] - b[0]) / len(f"{settled[(r, c)]:,}" if source.COMMAS else str(settled[(r, c)])),
                            )
                        )
                ys = {
                    r: statistics.median((b[1] + b[3]) / 2 for (rr, _), b in anchors.items() if rr == r)
                    for r in {r for r, _ in anchors}
                }
                xs = {
                    c: statistics.median(b[2] for (_, cc), b in anchors.items() if cc == c)
                    for c in {c for _, c in anchors}
                }
                for c in sorted(set(range(len(columns))) - set(xs)):
                    lo = max((xs[k] for k in xs if k < c), default=None)
                    hi = min((b[0] for (_, k), b in anchors.items() if k > c), default=None)
                    for r in lines:
                        near = [
                            b
                            for b in boxes.get((r, c), [])
                            if r in ys
                            and abs((b[1] + b[3]) / 2 - ys[r]) < 12
                            and (lo is None or b[0] > lo)
                            and (hi is None or b[2] < hi)
                        ]
                        if len(near) == 1:
                            anchors[(r, c)] = near[0]
                    if any(k == c for _, k in anchors):
                        xs[c] = statistics.median(b[2] for (_, k), b in anchors.items() if k == c)
                for c, x in xs.items():
                    grid.setdefault(c, []).append(x)
                height = statistics.median(b[3] - b[1] for b in anchors.values())
                digit = statistics.median(
                    (b[2] - b[0]) / len(f"{settled[key]:,}" if source.COMMAS else str(settled[key]))
                    for key, b in anchors.items()
                )
                pitch.extend((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                for r in set(lines) - set(ys):
                    below, above = [k for k in ys if k < r], [k for k in ys if k > r]
                    if below and above:
                        lo, hi = max(below), min(above)
                        ys[r] = ys[lo] + (ys[hi] - ys[lo]) * (r - lo) / (hi - lo)
                    elif len(ys) >= 2 or (ys and pitch):
                        step = (
                            statistics.median((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                            if len(ys) >= 2
                            else statistics.median(pitch)
                        )
                        ys[r] = (
                            ys[max(below)] + step * (r - max(below))
                            if below
                            else ys[min(above)] - step * (min(above) - r)
                        )
                for c in set(range(len(columns))) - set(xs):
                    left, right = [k for k in xs if k < c], [k for k in xs if k > c]
                    if left and right:
                        lo, hi = max(left), min(right)
                        xs[c] = xs[lo] + (xs[hi] - xs[lo]) * (c - lo) / (hi - lo)
                    elif len(xs) >= 2:
                        step = statistics.median((xs[b] - xs[a]) / (b - a) for a, b in zip(sorted(xs), sorted(xs)[1:]))
                        xs[c] = (
                            xs[min(right)] - step * (min(right) - c)
                            if right
                            else xs[max(left)] + step * (c - max(left))
                        )
                if not guessed:
                    steps.extend((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                    spans.extend((xs[b] - xs[a]) / (b - a) for a, b in zip(sorted(xs), sorted(xs)[1:]))
                    learned.extend(
                        (xs[k] - heads[k][0], ys[lines[0]] - heads[k][3], height, digit)
                        for k in heads
                        if k in xs and lines and lines[0] in ys
                    )
                for r, c, county, value, again in cells:
                    if r == total or columns[c] == "Plurality" or (r, c) not in settled:
                        continue
                    votes = settled[(r, c)]
                    box, how = None, None
                    if (r, c) in anchors:
                        box, how = anchors[(r, c)], "placed" if (r, c) in guessed else "unique"
                    elif pairs := sorted(
                        (k[1] - h[3], h, k)
                        for name, h in words
                        if name == _plain(columns[c])
                        or (len(name) >= 5 and _plain(columns[c]).startswith(name))
                        or name in source.ALIASES.get(columns[c], ())
                        for place, k in words
                        if place == _plain(county)
                        and k[1] > h[3]
                        and not any(
                            k[0] + 50 < b[0] < h[0] and abs(b[1] - h[1]) < 200
                            for name_, b in words
                            if name_ in source.BESIDE
                        )
                    ):
                        _, h, k = pairs[0]
                        right = xs[c] if any(cc == c for _, cc in anchors) else (h[0] + h[2]) / 2 + 25
                        down = (k[1] + k[3]) / 2
                        if r in ys and (len(columns) == 1 or abs(ys[r] - down) < 15):
                            down = ys[r]
                        near = sorted(
                            (
                                b
                                for b in boxes.get((r, c), [])
                                if abs((b[1] + b[3]) / 2 - down) < 12 and abs(b[2] - right) < 80
                            ),
                            key=lambda b: abs(b[2] - right),
                        )
                        if near:
                            box, how = near[0], "aligned"
                        else:
                            width = digit * len(f"{votes:,}" if source.COMMAS else str(votes))
                            box = (
                                round(right - width),
                                round(down - height / 2),
                                round(right),
                                round(down + height / 2),
                            )
                            how = "placed"
                    elif r in ys and c in xs:
                        near = [
                            b
                            for b in boxes.get((r, c), [])
                            if abs((b[1] + b[3]) / 2 - ys[r]) < 12 and abs(b[2] - xs[c]) < 40
                        ]
                        if near:
                            box, how = near[0], "aligned"
                        else:
                            width = digit * len(f"{votes:,}" if source.COMMAS else str(votes))
                            box = (
                                round(xs[c] - width),
                                round(ys[r] - height / 2),
                                round(xs[c]),
                                round(ys[r] + height / 2),
                            )
                            how = "placed"
                    if (
                        box is None
                        and (c in xs or c in grid or (xs and spans))
                        and (r in ys or (ys and (steps or carried)))
                    ):
                        known_ = min(ys, key=lambda k: abs(k - r))
                        down = ys[r] if r in ys else ys[known_] + statistics.median(steps or carried) * (r - known_)
                        right = (
                            xs[c]
                            if c in xs
                            else statistics.median(grid[c])
                            if c in grid
                            else xs[nearest := min(xs, key=lambda k: abs(k - c))]
                            + statistics.median(spans) * (c - nearest)
                        )
                        box = (
                            round(right - digit * len(f"{votes:,}" if source.COMMAS else str(votes))),
                            round(down - height / 2),
                            round(right),
                            round(down + height / 2),
                        )
                        how = "placed"
                    if box is not None and box[3] - box[1] > 1.6 * height and r in ys:
                        box = (box[0], round(ys[r] - height / 2), box[2], round(ys[r] + height / 2))
                    if (
                        box is not None
                        and box[2] - box[0] > 1.6 * digit * len(f"{votes:,}" if source.COMMAS else str(votes)) + 10
                        and c in xs
                    ):
                        box = (
                            round(xs[c] - digit * len(f"{votes:,}" if source.COMMAS else str(votes))),
                            box[1],
                            round(xs[c]),
                            box[3],
                        )
                    rows.append(
                        {
                            "year": year,
                            "page": page,
                            "state": table["state"] or source.STATE,
                            "district": table["district"],
                            "county": county,
                            "column": known.index(columns[c]),
                            "label": columns[c],
                            "votes": votes,
                            "box": None if box is None else list(box),
                            "located": how,
                        }
                    )
    for (year, district, c), column in columns_by.items():
        if (
            (year, district) not in source.ERRATA
            and (year, district, c) in printed
            and sum(column) != printed[(year, district, c)]
        ):
            failed.append(f"{year} district {district} column {c}: the column does not add up")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    print(frame.group_by("located").len().sort("located"))
    if failed:
        for line in failed:
            print(line)
        raise ValueError(f"{len(failed)} cells or sums do not reconcile")
    for row in rows:
        if row["box"] is not None:
            continue
        same_column = [
            r["box"]
            for r in rows
            if r["box"] is not None
            and (r["year"], r["district"], r["column"]) == (row["year"], row["district"], row["column"])
        ]
        same_row = [
            r["box"]
            for r in rows
            if r["box"] is not None
            and (r["year"], r["district"], r["county"]) == (row["year"], row["district"], row["county"])
        ]
        if not same_column or not same_row:
            continue
        digit = statistics.median(
            (r["box"][2] - r["box"][0]) / len(str(r["votes"]))
            for r in rows
            if r["box"] is not None and r["year"] == row["year"]
        )
        right = round(statistics.median(b[2] for b in same_column))
        middle = statistics.median((b[1] + b[3]) / 2 for b in same_row)
        half = statistics.median(b[3] - b[1] for b in same_column) / 2
        row["box"] = [round(right - digit * len(str(row["votes"]))), round(middle - half), right, round(middle + half)]
        row["located"] = "placed"
    frame = pl.DataFrame(rows, infer_schema_length=None)
    missing = frame.filter(pl.col("box").is_null())
    if missing.height:
        raise ValueError(f"no box for {missing.select('year', 'district', 'county', 'column').rows()}")
    frame.write_parquet(parsed / "values.parquet")


def emit(source: ModuleType) -> Path:
    frame = pl.read_parquet(raw.parsed(source.SOURCE) / "values.parquet")
    if "state" not in frame.columns:
        frame = frame.with_columns(state=pl.lit(source.STATE))
    names = getattr(source, "NAMES", {})
    frame = frame.with_columns(
        county=pl.struct("state", "county").map_elements(
            lambda row: names.get((row["state"], row["county"]), row["county"]), return_dtype=pl.String
        )
    )
    folder = DATA / "pages" / source.SOURCE
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=source.SOURCE,
            version=source.VERSION,
            summary=source.SUMMARY,
            polity="US",
        )
        fips: dict[str, str] = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.code from reporting_unit u join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        counties: dict[tuple[str, str], tuple[str, UUID, UUID]] = {
            (code[:2], _plain(text)): (code, unit, area)
            for code, unit, area, text in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{source.FIPS or ''}%"],
            ).fetchall()
        }
        for code, unit, area in conn.execute(
            """select code, id, area_id from reporting_unit
               where scheme_code in ('us.county', 'us.former_county') and code like %s""",
            [f"{source.FIPS or source.STATE or ''}%"],
        ).fetchall():
            counties[(code, code)] = (code, unit, area)
        for (postal, plain), (label, successor, ended) in getattr(source, "FORMER", {}).items():
            code = f"{postal}:{plain}"
            if (code, code) in counties:
                continue
            unit = cs.unit("us.former_county", code)
            area = counties[(successor, successor)][2]
            counties[(code, code)] = (code, unit, area)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=cs.polity(f"US-{postal}"),
                scheme_code="us.former_county",
                code=code,
                parent_id=cs.unit("us.state", successor[:2]),
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=None,
                valid_to=ended,
            )
            cs.add("name", entity_id=unit, text=label, lang="en", kind="official", is_preferred=True)
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id where scheme = 'us.contest'
                   and (value like %s or value like %s or value like %s)""",
                [
                    f"%:{source.STATE or ''}%:{getattr(source, 'CHAMBER', 'house')}:%",
                    f"%:{source.STATE or ''}:senate",
                    f"%:{source.STATE or ''}%:president",
                ],
            ).fetchall()
        )
        folder.mkdir(parents=True, exist_ok=True)
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year, (_, landing, day, numbers) in source.VOLUMES.items():
            title = getattr(source, "TITLES", {}).get(year) or source.TITLE.format(day=day)
            if (made := cs.source(source.PUBLISHER, title)) not in sources.values():
                cs.add(
                    "source",
                    id=made,
                    kind=getattr(source, "KIND", "official_body"),
                    publisher=source.PUBLISHER,
                    title=title,
                    url=landing,
                    license=source.LICENSE,
                )
            sources[year] = made
            book = _artifact(source, year)
            artifact = cs.artifact(book, made)
            for number in numbers:
                image = folder / f"{book.sha256}-{number}.png"
                if not image.exists():
                    _render(book, number, 100, image, (year, number) in source.TURNED)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(year, number)] = (page, width, height)
                if conn.execute(
                    """select 1 from source_page p join changeset c on c.id = p.changeset_id
                       where p.id = %s and c.pipeline <> %s""",
                    [page, source.SOURCE],
                ).fetchone():
                    continue
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{source.SOURCE}/{book.sha256}-{number}.png",
                    width=width,
                    height=height,
                )
        split = {
            (int(year), str(where), str(county))
            for (year, where, county), districts in frame.filter(pl.col("district") > 0).group_by(
                "year", "state", "county"
            )
            if districts["district"].n_unique() > 1 or _plain(str(county)) == "cook" or hasattr(source, "CHAMBER")
        }
        parts: set[str] = set()
        for (year, where, district), lines in frame.group_by("year", "state", "district", maintain_order=True):
            day = source.VOLUMES[int(year)][2]
            state = cs.polity(f"US-{where}")
            key = getattr(source, "KEYS", {}).get(
                (int(year), int(district)) if source.STATE else (int(year), str(where), int(district)),
                f"{day}:{where}:" + (f"house:{district}" if district else "president"),
            )
            if key not in contests:
                dated = [k for k in contests if re.fullmatch(rf"{year}-[0-9-]+:{where}:house:{district}", k)]
                if len(dated) != 1:
                    print(f"{year} {where} district {district}: no contest")
                    continue
                key = dated[0]
            contest = contests[key]
            election, district_area, first, official, held = conn.execute(
                """select c.election_id, c.district_area_id, coalesce(min(v.count_round), 0),
                          coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                   sum(v.value) filter (where v.measure = 1))::bigint, e.date_from
                   from contest c join election e on e.id = c.election_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   left join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
                   where c.id = %s group by 1, 2, 5""",
                [contest],
            ).fetchone()
            day = held or day
            known = conn.execute(
                """select o.id, o.kind, coalesce(o.ballot_label, ''),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1)
                   from contest_option o join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            picked: dict[int, UUID] = {}
            unmatched: list[tuple[int, str, int]] = []
            for (column, label), cells in lines.group_by("column", "label", maintain_order=True):
                summed = int(cells["votes"].sum())
                surname = _plain(str(label).split("/")[0])
                named = [
                    (option, total)
                    for option, kind, name, total in known
                    if kind in ("candidate", "write_in", "ticket")
                    and surname not in getattr(source, "PARTIES", ())
                    and (words := [w for w in re.sub(r"\(.*\)", "", name).split() if _plain(w) not in ("jr", "sr")])
                    and (
                        surname in _plain(" ".join(words))
                        or difflib.SequenceMatcher(None, surname, _plain(words[-1])).ratio() >= 0.8
                    )
                ]
                exact = [option for option, total in named if total == summed]
                if len(exact) == 1:
                    picked[int(column)] = exact[0]
                elif len({option for option, _ in named}) == 1:
                    picked[int(column)] = named[0][0]
                elif (
                    not named
                    and len(same := [o for o, k, _, t in known if k in ("candidate", "write_in", "ticket") and t == summed]) == 1
                ):
                    picked[int(column)] = same[0]
                elif summed == 0:
                    lines = lines.filter(pl.col("column") != column)
                else:
                    unmatched.append((int(column), str(label), summed))
            if getattr(source, "PARTIES", ()):
                free = [(o, t) for o, k, _, t in known if k == "candidate" and t and o not in picked.values()]
                if len(unmatched) == 1 and len(free) == 1:
                    picked[unmatched[0][0]] = free[0][0]
                    unmatched = []
                for column, label, summed in list(unmatched):
                    near = sorted(free, key=lambda pair: abs(pair[1] - summed))
                    if (
                        near
                        and abs(near[0][1] - summed) <= 0.05 * summed
                        and (len(near) == 1 or abs(near[1][1] - summed) > 0.1 * summed)
                        and near[0][0] not in picked.values()
                    ):
                        picked[column] = near[0][0]
                        unmatched.remove((column, label, summed))
            for column, label, summed in unmatched:
                print(f"{key}: no option for {label} ({summed})")
            result_set = cs.key(
                f"{source.SOURCE}.result_set", f"{key}:county" + (f":{source.REVISION}" if source.REVISION > 1 else "")
            )
            previous = (
                cs.key(f"{source.RENAMED}.result_set", f"{key}:county")
                if source.RENAMED is not None
                else cs.key(
                    f"{source.SOURCE}.result_set",
                    f"{key}:county" + (f":{source.REVISION - 1}" if source.REVISION > 2 else ""),
                )
                if source.REVISION > 1
                else None
            )
            if result_set not in cs.minted:
                continue
            if len(picked) < lines["column"].n_unique():
                rest = int(lines.filter(~pl.col("column").is_in(list(picked)))["votes"].sum())
                lumped = [
                    o
                    for o, k, _, t in known
                    if k in ("write_in", "others")
                    and t
                    == rest
                    + int(lines.filter(pl.col("column").is_in([c for c, p in picked.items() if p == o]))["votes"].sum())
                ]
                empty = [o for o, k, _, t in known if k == "others" and t is None]
                if len(lumped) == 1:
                    pool = lumped[0]
                elif not lumped and len(empty) == 1:
                    pool = empty[0]
                else:
                    pool = cs.key("us.option", f"{key}:others:{source.SOURCE.removeprefix('us.')}")
                    cs.add("contest_option", id=pool, contest_id=contest, kind="others", ballot_label="Others")
                picked = {int(column): picked.get(int(column), pool) for column in lines["column"].unique()}
            lines = lines.with_columns(
                option=pl.col("column").replace_strict({column: str(option) for column, option in picked.items()})
            )
            counted = int(lines["votes"].sum())
            for option, kind, name, total in known:
                sum_ = int(lines.filter(pl.col("option") == str(option))["votes"].sum())
                if total and sum_ != total:
                    print(f"{key}: {name or kind} {sum_} by county, {total} in the default set")
            for id, status in ((previous, "superseded"), (result_set, "active")):
                if id is not None:
                    cs.add(
                        "result_set",
                        id=id,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status=status,
                        source_id=sources[int(year)],
                        is_default=False,
                        supersedes_id=previous if id == result_set else None,
                        completeness=round(min(counted / official, 1), 4) if official else None,
                    )
            held = {
                code
                for (code,) in conn.execute(
                    "select code from reporting_unit where scheme_code = 'us.county_part' and code like %s and valid_from <> %s",
                    [f"{year}:%", day],
                ).fetchall()
            }
            units: dict[str, UUID] = {}
            for county in lines["county"].unique(maintain_order=True):
                code, parent, area = counties.get((county, county)) or counties[(fips[where], _plain(county))]
                if (int(year), where, county) in split:
                    part = (
                        f"{year}:{code}:{source.CHAMBER}:{key.split(':', 3)[3]}"
                        if hasattr(source, "CHAMBER")
                        else f"{year}:{code}:{key.split(':')[3]}"
                    )
                    if part in held:
                        part = f"{day}:{part.split(':', 1)[1]}"
                    units[county] = cs.unit("us.county_part", part)
                    if part in parts:
                        continue
                    parts.add(part)
                    cs.add(
                        "reporting_unit",
                        id=units[county],
                        polity_id=state,
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=parent,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=units[county],
                        area_id=district_area,
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                else:
                    units[county] = parent
            for (county, option), cells in lines.group_by("county", "option", maintain_order=True):
                unit = units[str(county)]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    flags=(DIGITIZED if "box" in frame.columns else 0) | (DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                if "box" not in frame.columns:
                    continue
                boxes = [list(box) for box in cells["box"]]
                page, width, height = pages[(int(year), int(cells["page"][0]))]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    page_id=page,
                    source_id=None,
                    x0=min(box[0] for box in boxes) / width / 3,
                    y0=min(box[1] for box in boxes) / height / 3,
                    x1=max(box[2] for box in boxes) / width / 3,
                    y1=max(box[3] for box in boxes) / height / 3,
                )
        return cs.write()
