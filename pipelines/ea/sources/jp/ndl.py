import base64
import io
import itertools
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "jp.ndl"
PARSED = DATA / "parsed" / SOURCE
MANIFEST = "https://dl.ndl.go.jp/api/iiif/{}/manifest.json"
IMAGE = "https://dl.ndl.go.jp/api/iiif/{}/R{:07d}/full/full/0/default.jpg"
VOLUMES = {
    23: "1340421",
    24: "1350470",
    25: "1350473",
    26: "1350472",
    27: "1350474",
    28: "1350393",
    29: "1351318",
    30: "1351319",
    31: "1351320",
}
MODEL = "claude-haiku-5-5"
PROMPT = "2"
READS = 2
LONGEST = 2576
PAGES = {"right": (0.48, 0.96), "left": (0.04, 0.53)}
HALVES = ((0.48, 1.0), (0.0, 0.54))
BANDS = ((0.05, 0.55), (0.45, 0.96))
HEADER = (0.06, 0.222)
READ_SYSTEM = (
    "You read a crop of one page of 衆議院議員総選挙一覧, a 1947–1967 Japanese House of Representatives election book. "
    "The text is vertical. A table's columns run right to left: a column of row labels (選挙区及定員, 市(区)郡町村, 有権者数 "
    "男/女/計, 失格, 棄権, 無効, then one candidate name per row under 候補者得票数, then 計), then one column per "
    "municipality, county (郡), subprefecture (支庁), city ward, or total (計, 合計, 小計), with its name printed vertically "
    "at the top. Horizontal ruled lines separate the rows. Figures are kanji numerals written top to bottom, with 、 as "
    "the thousands separator and 〇 for zero, often with two digits squeezed into one character width (五六八 is 568, "
    "一、二七八 is 1,278). A figure sits at the bottom of its row's cell, so a short figure leaves blank space above it. A "
    "bar (｜) or dash in a cell means zero. The crop is a quarter of the page: the row labels may be outside it, and "
    "columns or rows at its edges may be cut. When the top of the crop is a strip of column names above a thick black "
    "line, that strip is the table's header copied from higher on the page; the names in it stand over the columns "
    "below the line. Read the table row by row. List the columns right to left with their names (null if none is "
    "visible), and for each full or cut row give its label if the crop shows one and exactly one cell per listed "
    "column, in the same right-to-left order: for a figure, its kanji as printed and its value in Arabic digits; for "
    "a bar, text '｜' and value 0; for a blank cell, text '' and null. Mark a row or column partial when the crop cuts "
    "it so its figures are not whole. Keep old kanji as printed (縣, 區, 廣). Also copy the district heading "
    "(第一區 (四人)) and any other heading text you see."
)
CELL = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "value": {"type": ["integer", "null"]}},
    "required": ["text", "value"],
    "additionalProperties": False,
}
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "headings": {"type": "array", "items": {"type": "string"}},
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": ["string", "null"]}, "partial": {"type": "boolean"}},
                "required": ["name", "partial"],
                "additionalProperties": False,
            },
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": ["string", "null"]},
                    "partial": {"type": "boolean"},
                    "cells": {"type": "array", "items": CELL},
                },
                "required": ["label", "partial", "cells"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headings", "columns", "rows"],
    "additionalProperties": False,
}


def manifest(pid: str) -> dict[str, Any]:
    return json.loads(raw.cached(SOURCE, MANIFEST.format(pid)).path.read_text())


def spread(pid: str, canvas: int) -> raw.Artifact:
    return raw.cached(SOURCE, IMAGE.format(pid, canvas))


def tiles(pid: str, canvas: int) -> dict[str, tuple[tuple[int, int, int, int], Image.Image]]:
    image = Image.open(spread(pid, canvas).path).convert("L")
    width, height = image.size
    out = {}
    for page, (left, right) in PAGES.items():
        for half, (start, end) in enumerate(HALVES):
            x0 = int(width * (left + (right - left) * start))
            x1 = int(width * (left + (right - left) * end))
            for band, (top, bottom) in enumerate(BANDS):
                box = (x0, int(height * top), x1, int(height * bottom))
                crop = image.crop(box)
                if band:
                    header = image.crop((x0, int(height * HEADER[0]), x1, int(height * HEADER[1])))
                    tile = Image.new("L", (crop.width, header.height + 12 + crop.height), 255)
                    tile.paste(header, (0, 0))
                    tile.paste(0, (0, header.height + 2, crop.width, header.height + 10))
                    tile.paste(crop, (0, header.height + 12))
                    crop = tile
                crop.thumbnail((LONGEST, LONGEST))
                out[f"{page}{half}{band}"] = (box, crop)
    return out


def read_key(pid: str, canvas: int, tile: str, read: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{pid}/{canvas}/{tile}/{read}"


def read(pid: str, canvas: int, tile: str, read: int) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(pid, canvas, tile, read)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(crop: Image.Image, read: int) -> batch.Want:
    buffer = io.BytesIO()
    crop.save(buffer, "JPEG", quality=90)
    price_in, price_out = batch.PRICES[MODEL]
    params = MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=16000,
        system=READ_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(buffer.getvalue()).decode(),
                        },
                    ),
                    TextBlockParam(
                        type="text",
                        text="Transcribe this table crop row by row."
                        if read == 1
                        else "Transcribe this table crop, one ruled row at a time from the top.",
                    ),
                ],
            }
        ],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
    )
    return params, (crop.width * crop.height / 750 * price_in + 5000 * price_out) / 1e6


def fetch(election: int, canvases: Iterable[int], job: str) -> None:
    pid = VOLUMES[election]
    wanted: dict[str, batch.Want] = {}
    for canvas in canvases:
        for tile, (_, crop) in tiles(pid, canvas).items():
            for number in range(1, READS + 1):
                wanted[read_key(pid, canvas, tile, number)] = request(crop, number)
    print(batch.ensure(SOURCE, job, wanted)[1])


# Reconciling the reads. The page's own ruling gives each cell a box in spread pixels: vertical rules split a page
# into its tables and its row-label columns, horizontal rules bound the rows, and the figures under each column name
# form one stripe of ink per column (a county heading has no figures under it, so it is no column). Each read of a
# tile is placed on that grid by counting its rows and columns from the tile's uncut side, so the overlapping tiles
# and the two reads of each tile land on the same cells. A cell holds when every read that sees it whole gives the
# same value and a printed sum it belongs to holds: 男 + 女 = 計 for the electorate, the candidate rows sum to the
# vote 計, and a county's 計 column sums its municipalities. A cell the reads disagree on, or that only one read saw
# whole, is settled when a sum whose other cells hold implies one of the values read for it.

type Band = tuple[int, int]
type Box = tuple[int, int, int, int]

LINE = 30  # a ruled line is this much darker than the paper
INK = 50  # printed ink is this much darker than the paper
CHUNK = 100  # horizontal rules are found as runs of dark chunks this wide
THICK = 40  # a dark band thicker than this is the book's edge, not a rule
ROW = 100  # a body row is at least this tall; a rule closer than this to the next one is text
NAMES = 300  # the row of column names is at least this tall
STRIPES = 3  # a stretch between vertical rules with fewer name stripes than this holds row labels or margin
CUT = 0.9  # a row or column is whole in a tile when this share of it lies inside the tile
SEEN = 0.2  # a tile shows a row or column at all when this share of it lies inside the tile
GROUPS = ("郡", "支庁", "支廳")


@dataclass
class Table:
    page: str
    x: Band
    columns: list[Band]  # right to left, columns of figures only: a county heading over no figures is left out
    rows: list[Band]  # top to bottom, body rows only
    regular: bool  # every body row is about as tall as the others


@dataclass
class Cell:
    canvas: int
    page: str
    table: int
    row: int
    column: int
    box: Box
    whole: list[tuple[str, int, int | None]] = field(default_factory=list)  # (tile, read, value)
    cut: list[tuple[str, int, int | None]] = field(default_factory=list)
    value: int | None = None
    status: str = "none"


def _background(gray: np.ndarray) -> float:
    return float(np.median(gray))


def _rules(gray: np.ndarray, run: int) -> list[tuple[int, int, int]]:
    """Horizontal rules in a crop: (top, bottom, longest run of dark chunks)."""
    chunks = gray.shape[1] // CHUNK
    if chunks < run:
        return []
    medians = np.stack([np.median(gray[:, i * CHUNK : (i + 1) * CHUNK], axis=1) for i in range(chunks)], axis=1)
    dark = medians < _background(gray) - LINE
    near = dark.copy()
    for shift in range(1, 4):  # a skewed rule drifts a few pixels across one chunk
        near[shift:] |= dark[:-shift]
        near[:-shift] |= dark[shift:]
    longest = np.zeros(len(near), dtype=np.int32)
    current = np.zeros(len(near), dtype=np.int32)
    for chunk in range(chunks):
        current = np.where(near[:, chunk], current + 1, 0)
        longest = np.maximum(longest, current)
    groups: list[list[int]] = []
    for y in np.flatnonzero(longest >= run):
        if groups and y - groups[-1][-1] <= 20:
            groups[-1].append(int(y))
        else:
            groups.append([int(y)])
    return [
        (group[0], group[-1], int(longest[group[0] : group[-1] + 1].max()))
        for group in groups
        if group[-1] - group[0] <= THICK
    ]


def _spaced(rules: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Drop the weaker of two rules closer than a row: one of them is a line of text."""
    rules = list(rules)
    while True:
        close = next((i for i in range(len(rules) - 1) if rules[i + 1][0] - rules[i][1] < ROW), None)
        if close is None:
            return rules
        del rules[close if rules[close][2] < rules[close + 1][2] else close + 1]


def _runs(on: np.ndarray, join: int) -> list[Band]:
    runs: list[list[int]] = []
    for x in np.flatnonzero(on):
        if runs and x - runs[-1][-1] <= join:
            runs[-1].append(int(x))
        else:
            runs.append([int(x)])
    return [(run[0], run[-1]) for run in runs]


def grid(pid: str, canvas: int) -> list[Table]:
    """The tables of a spread, right page first, each with its rows and columns in spread pixels."""
    gray = np.asarray(Image.open(spread(pid, canvas).path).convert("L")).astype(np.int16)
    width = gray.shape[1]
    tables: list[Table] = []
    for page, (left, right) in PAGES.items():
        x0, x1 = int(width * left), int(width * right)
        crop = gray[:, x0:x1]
        background = _background(crop)
        frame = _spaced(_rules(crop, 10))
        names = next(
            ((frame[i][1], frame[i + 1][0]) for i in range(len(frame) - 1) if frame[i + 1][0] - frame[i][1] >= NAMES),
            None,
        )
        if names is None:
            continue
        top, bottom = frame[0][0], frame[-1][1]
        walls = [x0 + (a + b) // 2 for a, b, _ in _rules(crop[top:bottom].T, max(8, (bottom - top) // 4 // CHUNK))]
        heading = names[1] - (names[1] - names[0]) * 3 // 4
        ink = (crop[heading : names[1] - 8] < background - INK).mean(axis=0) > 0.02
        stripes = [(x0 + a, x0 + b) for a, b in _runs(ink, 6) if b - a > 20]
        bounds = [x0, *walls, x1]
        for start, end in reversed(list(itertools.pairwise(bounds))):
            heads = [s for s in stripes if s[0] >= start + 8 and s[1] <= end - 8]
            if len(heads) < STRIPES:
                continue
            span = (min(a for a, _ in heads), max(b for _, b in heads))
            rules = _spaced(_rules(gray[:, span[0] : span[1]], max(3, (span[1] - span[0]) // CHUNK // 2)))
            below = [rule for rule in rules if rule[0] >= names[1] - 2 * THICK]
            rows = [(a[1], b[0]) for a, b in itertools.pairwise(below)]
            if not rows:
                continue
            body = np.concatenate([gray[upper + 15 : lower - 15, start + 8 : end - 8] for upper, lower in rows])
            figures = (body < background - INK).mean(axis=0) > 0.01
            inside = sorted(((start + 8 + a, start + 8 + b) for a, b in _runs(figures, 6) if b - a > 15), reverse=True)
            if len(inside) < STRIPES:
                continue
            pitch = int(np.median([a[0] - b[0] for a, b in itertools.pairwise(inside)]))
            columns = []
            for i, (a, b) in enumerate(inside):
                center = (a + b) // 2
                high = min(center + pitch // 2, (inside[i - 1][0] + b) // 2 if i else end)
                low = max(center - pitch // 2, (a + inside[i + 1][1]) // 2 if i + 1 < len(inside) else start)
                columns.append((low, high))
            heights = [b - a for a, b in rows]
            middle = float(np.median(heights))
            regular = all(0.6 * middle <= height <= 1.6 * middle for height in heights)
            tables.append(Table(page, (columns[-1][0], columns[0][1]), columns, rows, regular))
    return tables


def _share(band: Band, span: Band) -> float:
    return max(0, min(band[1], span[1]) - max(band[0], span[0])) / (band[1] - band[0])


def _place(seen: list[int], whole: list[int], n: int, uncut_first: bool) -> list[int] | None:
    """Which of the grid lines a tile shows the read's n lines are, counting from the tile's uncut side."""
    if n == len(seen):
        return seen
    if n == len(whole):
        return whole
    if n == len(seen) - 1:
        far = seen[-1] if uncut_first else seen[0]
        if far not in whole:
            return seen[:-1] if uncut_first else seen[1:]
    return None


def _value(cell: dict[str, Any]) -> int | None:
    text = cell["text"].strip()
    if text in {"｜", "|", "—", "-", "－", "ー", "〇"}:
        return 0
    value = cell["value"]
    return value if isinstance(value, int) else None


def _blank(column: int, rows: list[dict[str, Any]]) -> bool:
    return all(column >= len(row["cells"]) or _value(row["cells"][column]) is None for row in rows)


@dataclass
class Placement:
    tile: str
    read: int
    found: dict[str, Any]
    table: int
    columns: list[tuple[int, int, int, bool]]  # per listed column with figures: (slot, table, column, whole)
    rows: list[tuple[int, bool]]  # per listed row: (row, whole)


def _placements(pid: str, canvas: int, tables: list[Table], problems: list[str]) -> list[list[Placement]]:
    """For every read of every tile, each way its rows and columns fit the grid."""
    out = []
    for tile, (box, _) in tiles(pid, canvas).items():
        page, half, band = tile[:-2], int(tile[-2]), int(tile[-1])
        span_x, span_y = (box[0], box[2]), (box[1], box[3])
        options = sorted(
            (
                (sum(_share(column, span_x) >= CUT for column in table.columns), table.page == page, number)
                for number, table in enumerate(tables)
            ),
            reverse=True,
        )
        options = [option for option in options if option[0]]
        if not options:
            problems.append(f"{canvas} {tile}: no table")
            continue
        if len(options) > 1 and options[1][0] >= 2:
            problems.append(f"{canvas} {tile}: spans two tables")
            continue
        number = options[0][2]
        table = tables[number]
        # A tile can reach a column of the facing page across the gutter, and a read may list it or not, so a read is
        # placed both ways; its column names choose between them. The facing page's cells come from its own tiles.
        everything = sorted(
            (-(sum(column)), other, index)
            for other, table_ in enumerate(tables)
            for index, column in enumerate(table_.columns)
            if _share(column, span_x) >= SEEN
        )
        ways = [[slot for slot in everything if slot[1] == number], everything]
        rows = [i for i, row in enumerate(table.rows) if _share(row, span_y) >= SEEN]
        whole_rows = [i for i in rows if _share(table.rows[i], span_y) >= CUT]
        for number_read in range(1, READS + 1):
            try:
                found = read(pid, canvas, tile, number_read)
            except LookupError, StopIteration, json.JSONDecodeError:
                problems.append(f"{canvas} {tile}/{number_read}: no read")
                continue
            kept = [i for i in range(len(found["columns"])) if not _blank(i, found["rows"])]
            placed_rows = _place(rows, whole_rows, len(found["rows"]), uncut_first=band == 0)
            fits: list[Placement] = []
            for seen in ways:
                whole = [
                    i for i, (_, other, index) in enumerate(seen) if _share(tables[other].columns[index], span_x) >= CUT
                ]
                placed = _place(list(range(len(seen))), whole, len(kept), uncut_first=half == 0)
                if placed is None or placed_rows is None:
                    continue
                columns = [(slot, seen[i][1], seen[i][2], i in whole) for slot, i in zip(kept, placed, strict=True)]
                if all(fit.columns != columns for fit in fits):
                    rows_ = [(row, row in whole_rows) for row in placed_rows]
                    fits.append(Placement(tile, number_read, found, number, columns, rows_))
            if not fits:
                problems.append(
                    f"{canvas} {tile}/{number_read}: read {len(kept)} columns and {len(found['rows'])} rows, grid "
                    f"shows {len(ways[0])} columns and {len(rows)} rows ({len(whole_rows)} whole)"
                )
                continue
            out.append(fits)
    return out


def _named(placements: Iterable[Placement]) -> dict[tuple[int, int], Counter[str]]:
    names: dict[tuple[int, int], Counter[str]] = {}
    for placement in placements:
        for slot, other, column, _ in placement.columns:
            if name := placement.found["columns"][slot]["name"]:
                names.setdefault((other, column), Counter())[name] += 1
    return names


def reconcile(pid: str, canvas: int) -> tuple[list[Table], dict[tuple[int, int, int], Cell], list[str]]:
    """Every cell of a spread with the reads of each tile placed on it, and what failed to place or to sum."""
    tables = grid(pid, canvas)
    problems: list[str] = []
    placements = _placements(pid, canvas, tables, problems)
    chosen = [fits[0] for fits in placements if len(fits) == 1]
    sure = _named(chosen)
    for fits in placements:
        if len(fits) == 1:
            continue
        scores = [
            sum(
                1
                for slot, other, column, _ in fit.columns
                if (name := fit.found["columns"][slot]["name"])
                and (other, column) in sure
                and sure[(other, column)].most_common(1)[0][0] == name
            )
            for fit in fits
        ]
        if scores.count(max(scores)) > 1:
            problems.append(f"{canvas} {fits[0].tile}/{fits[0].read}: placed {len(fits)} ways, names do not choose")
            continue
        chosen.append(fits[scores.index(max(scores))])
    cells: dict[tuple[int, int, int], Cell] = {}
    labels: dict[tuple[int, int], Counter[str]] = {}
    starts: set[tuple[int, int]] = set()  # the first column after a county heading
    for placement in chosen:
        found, number = placement.found, placement.table
        table = tables[number]
        own = [(slot, column, whole) for slot, other, column, whole in placement.columns if other == number]
        listed = [slot for slot, *_ in placement.columns]
        for slot, column, _ in own:
            heading = found["columns"][slot - 1]["name"] if slot else None
            if slot - 1 not in listed and heading and heading.endswith(GROUPS):
                starts.add((number, column))
        for found_row, (row, whole_row) in zip(found["rows"], placement.rows, strict=True):
            if found_row["label"] and whole_row:
                labels.setdefault((number, row), Counter())[found_row["label"]] += 1
            for slot, column, whole_column in own:
                if slot >= len(found_row["cells"]):
                    continue
                key = (number, row, column)
                if key not in cells:
                    (x0, x1), (y0, y1) = table.columns[column], table.rows[row]
                    cells[key] = Cell(canvas, table.page, number, row, column, (x0, y0, x1, y1))
                reading = (placement.tile, placement.read, _value(found_row["cells"][slot]))
                (cells[key].whole if whole_row and whole_column else cells[key].cut).append(reading)
    names = _named(chosen)
    for cell in cells.values():
        values = {value for _, _, value in cell.whole}
        if len(cell.whole) >= 2 and len(values) == 1:
            cell.status, cell.value = "agree", cell.whole[0][2]
        elif len(values) > 1:
            cell.status = "conflict"
        elif cell.whole:
            cell.status, cell.value = "single", cell.whole[0][2]
    _sums(tables, cells, names, starts, problems, canvas)
    for (number, column), counter in names.items():
        if len(counter) > 1:
            problems.append(f"{canvas} table {number} column {column}: names {dict(counter)}")
    for table_number, table in enumerate(tables):
        table_labels = [labels.get((table_number, row), Counter()).most_common(1) for row in range(len(table.rows))]
        printed = [label[0][0] if label else None for label in table_labels]
        if any(printed) and (printed[:3] != ["男", "女", "計"] or printed[-1] != "計"):
            problems.append(f"{canvas} table {table_number}: row labels {printed}")
        if not table.regular:
            problems.append(f"{canvas} table {table_number}: irregular rows {[b - a for a, b in table.rows]}")
    return tables, cells, problems


def _sums(
    tables: list[Table],
    cells: dict[tuple[int, int, int], Cell],
    names: dict[tuple[int, int], Counter[str]],
    starts: set[tuple[int, int]],
    problems: list[str],
    canvas: int,
) -> None:
    """Settle cells against the printed sums, and mark the cells of every sum that fails."""

    good = {"agree", "checked", "settled"}

    def equation(parts: list[tuple[int, int, int]], total: tuple[int, int, int]) -> None:
        if any(key not in cells for key in [*parts, total]):
            return
        members = [cells[key] for key in parts]
        whole = cells[total]
        unsettled = [member for member in [*members, whole] if member.status not in good]
        if len(unsettled) == 1:
            missing = unsettled[0]
            others = sum(member.value or 0 for member in members if member is not missing)
            implied = others if missing is whole else (whole.value or 0) - others
            if implied in {value for _, _, value in missing.whole + missing.cut}:
                missing.value, missing.status = implied, "settled"
        if any(member.status not in good for member in [*members, whole]):
            return
        if sum(member.value or 0 for member in members) == (whole.value or 0):
            for member in [*members, whole]:
                if member.status == "agree":
                    member.status = "checked"
            return
        for member in [*members, whole]:
            member.status = "failed"
        problems.append(
            f"{canvas} {whole.page} table {whole.table} row {whole.row} column {whole.column}: "
            f"{'+'.join(str(member.value) for member in members)} != {whole.value}"
        )

    for number, table in enumerate(tables):
        last = len(table.rows) - 1
        if last < 4:
            continue
        for column in range(len(table.columns)):
            equation([(number, 0, column), (number, 1, column)], (number, 2, column))
            equation([(number, row, column) for row in range(3, last)], (number, last, column))
    # A county's columns run from the one after its heading to its 計, across the page break when the left page
    # carries on the right page's table.
    sequence = [
        (number, column)
        for number, table in enumerate(tables)
        for column in range(len(table.columns))
        if tables and len(table.rows) == len(tables[0].rows)
    ]
    group: list[tuple[int, int]] | None = None
    for number, column in sequence:
        name = (names.get((number, column)) or Counter()).most_common(1)
        printed = name[0][0] if name else ""
        if printed in {"計", "小計"} or printed.endswith(tuple(f"{group}計" for group in GROUPS)):
            if group:
                for row in range(len(tables[number].rows)):
                    equation([(n, row, c) for n, c in group], (number, row, column))
            group = None
        elif (number, column) in starts:
            group = [(number, column)]
        elif group is not None:
            group.append((number, column))


HELD = {"checked", "settled"}


def check(election: int, canvases: Iterable[int]) -> dict[str, Any]:
    """Reconcile the reads of some canvases, write every cell to the parsed folder, and measure the reads."""
    pid = VOLUMES[election]
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    grid_cells = 0
    for canvas in canvases:
        tables, cells, found = reconcile(pid, canvas)
        problems += found
        grid_cells += sum(len(table.rows) * len(table.columns) for table in tables)
        for cell in cells.values():
            rows.append(
                {
                    "canvas": canvas,
                    "page": cell.page,
                    "table": cell.table,
                    "row": cell.row,
                    "column": cell.column,
                    "x0": cell.box[0],
                    "y0": cell.box[1],
                    "x1": cell.box[2],
                    "y1": cell.box[3],
                    "value": cell.value,
                    "status": cell.status,
                    "reads": json.dumps(cell.whole, ensure_ascii=False),
                    "cut": json.dumps(cell.cut, ensure_ascii=False),
                }
            )
    folder = PARSED / pid
    folder.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(folder / "cells.parquet")
    statuses = Counter(row["status"] for row in rows)
    compared = [row for row in rows if len(json.loads(row["reads"])) >= 2]
    agreed = [row for row in compared if len({value for *_, value in json.loads(row["reads"])}) == 1]
    summary = {
        "grid cells": grid_cells,
        "cells read": len(rows),
        "statuses": dict(statuses),
        "cells read whole twice or more": len(compared),
        "of those, reads agree": len(agreed),
        "agreeing cells in a sum that failed": sum(1 for row in agreed if row["status"] == "failed"),
        "agreeing cells in a sum that held": sum(1 for row in agreed if row["status"] == "checked"),
        "held": sum(statuses[status] for status in HELD),
        "reads not placed": sum(
            1 if "rows, grid shows" in problem else READS if "two tables" in problem else 0 for problem in problems
        ),
        "sums failed": sum("!=" in problem for problem in problems),
    }
    (folder / "problems.txt").write_text("\n".join(problems) + "\n")
    return summary
