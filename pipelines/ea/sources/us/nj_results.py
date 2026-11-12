import base64
import difflib
import io
import itertools
import json
import re
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nj_results"
PUBLISHER = "New Jersey Division of Elections"
DIGITIZED = 8
PEOPLE = ("candidate", "ticket", "legend")
DERIVED = 1
CORRECTED = {(1946, "Smith", "Hudson"): 92991, (1946, "Brunner", "Hudson"): 155366}
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
URL = "https://www.nj.gov/state/elections/assets/pdf/election-results/1920-1970/{year}-general-election.pdf"
YEARS = [1925, 1927, 1928, *range(1930, 1941), *range(1942, 1980)]
MODEL = "claude-haiku-5-5"
JOB = "tables"
RETRY = "tables2"
RETRIES = {
    (1949, 5, 1): "Governor",
    (1954, 2, 1): "United States Senator",
    (1958, 3, 1): "United States Senator",
    (1973, 3, 0): "Governor",
    (1976, 3, 0): "President and Vice-President",
    (1977, 3, 0): "Governor",
}
DPI = 300
PIXELS = 1_150_000
READS = ("gray", "ink")
TALL = 1600
PROMPT = (
    "This is part of a page from the State of New Jersey's official statement of the result of the {year} general "
    "election. Transcribe every table on it that reports votes for President and Vice-President (or presidential "
    "electors), Governor, United States Senator, or Member of the House of Representatives (Congress). Skip every "
    "other table, such as State Senate, General Assembly, public questions, and county offices. Reply with only a "
    'JSON list, one object per table, like {{"title": "...", "district": 3, "columns": [...], "rows": [{{"label": '
    '"...", "values": [...]}}]}}. title is the table heading as printed, or null if no heading is visible; district '
    "is the congressional district number for a House table, else null. columns holds every column heading from "
    "left to right after the row label column, as printed (candidate names with their party, or county names, "
    "Party, and Totals). Each row has its label as printed (a county, a candidate, or Total) and one entry in values "
    "per column: numbers as plain integers without commas, text such as a party as a string, and null for a blank or "
    "dotted cell. Include total rows and columns exactly as printed. Leave out plurality and majority columns and "
    "rows. Copy the digits exactly as printed; do not correct, add, or infer numbers. If a table is cut off at an "
    "edge, include only its fully visible rows. If there is no such table, reply []."
)


def pdf(year: int) -> raw.Artifact:
    url = URL.format(year=year)
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return raw.fetch(SOURCE, url, suffix=".pdf")


def fetch() -> None:
    for year in YEARS:
        pdf(year)


def count(year: int) -> int:
    info = subprocess.run(["pdfinfo", str(pdf(year).path)], capture_output=True, text=True, check=True).stdout
    return int(re.findall(r"Pages:\s+(\d+)", info)[0])


def page(year: int, number: int) -> Image.Image:
    cache = DATA / "tmp" / SOURCE / f"{year}-{number}.png"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(pdf(year).path), str(cache.with_suffix(""))],
            check=True,
        )
    return Image.open(cache).convert("L")


def _bands(ink: np.ndarray, top: int, bottom: int, gap: int) -> list[tuple[int, int]]:
    rows = ink[top:bottom].sum(axis=1) > 2
    bands: list[list[int]] = []
    blank = gap
    for y, inked in enumerate(rows, top):
        if inked:
            if blank >= gap or not bands:
                bands.append([y, y + 1])
            bands[-1][1] = y + 1
            blank = 0
        else:
            blank += 1
    return [(a, b) for a, b in bands]


def blocks(image: Image.Image) -> list[tuple[int, int, int, int]]:
    ink = np.asarray(image) < 128
    margin = round(0.04 * image.width)
    ink[:, :margin] = False
    ink[:, -margin:] = False
    ink[: round(0.02 * image.height)] = False
    ink[-round(0.02 * image.height) :] = False
    pending = _bands(ink, 0, image.height, 60)
    out: list[tuple[int, int]] = []
    while pending:
        top, bottom = pending.pop(0)
        if bottom - top <= TALL:
            out.append((top, bottom))
            continue
        for gap in (40, 25, 15):
            parts = _bands(ink, top, bottom, gap)
            if len(parts) > 1:
                break
        if len(parts) == 1:
            out.append((top, bottom))
            continue
        middle = (top + bottom) / 2
        cut = min(range(1, len(parts)), key=lambda i: abs(parts[i][0] - middle))
        pending[:0] = [(top, parts[cut - 1][1]), (parts[cut][0], bottom)]
    merged: list[list[int]] = []
    for top, bottom in out:
        if merged and bottom - merged[-1][0] <= TALL:
            merged[-1][1] = bottom
        else:
            merged.append([top, bottom])
    boxes: list[tuple[int, int, int, int]] = []
    for top, bottom in merged:
        columns = np.flatnonzero(ink[top:bottom].sum(axis=0) > 1)
        if bottom - top < 0.15 * DPI or not len(columns):
            continue
        boxes.append(
            (
                max(int(columns[0]) - 25, 0),
                max(top - 25, 0),
                min(int(columns[-1]) + 25, image.width),
                min(bottom + 25, image.height),
            )
        )
    return boxes


def crop(image: Image.Image, box: tuple[int, int, int, int], read: str) -> tuple[bytes, tuple[int, int]]:
    part = image.crop(box)
    scale = min(1.0, (PIXELS / (part.width * part.height)) ** 0.5, 1568 / max(part.size))
    part = part.resize((round(part.width * scale), round(part.height * scale)), Image.Resampling.LANCZOS)
    if read == "ink":
        part = part.point(lambda v: 0 if v < 150 else 255)
    buffer = io.BytesIO()
    part.save(buffer, "PNG")
    return buffer.getvalue(), part.size


def layout() -> dict[tuple[int, int], list[tuple[int, int, int, int]]]:
    path = PARSED / "blocks.json"
    if path.exists():
        return {
            (int(k.split(":")[0]), int(k.split(":")[1])): [tuple(b) for b in v]
            for k, v in json.loads(path.read_text()).items()
        }
    found = {(year, number): blocks(page(year, number)) for year in YEARS for number in range(1, count(year) + 1)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({f"{y}:{n}": v for (y, n), v in found.items()}))
    return found


def key(year: int, number: int, block: int, read: str) -> str:
    return f"{SOURCE}/{JOB}/{year}/{number}/{block}/{read}"


def ensure() -> bool:
    wanted: dict[str, batch.Want] = {}
    for (year, number), boxes in layout().items():
        image = page(year, number)
        for block, box in enumerate(boxes):
            for read in READS:
                data, (width, height) = crop(image, box, read)
                content: list[ImageBlockParam | TextBlockParam] = [
                    ImageBlockParam(
                        type="image",
                        source={"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
                    ),
                    TextBlockParam(type="text", text=PROMPT.format(year=year)),
                ]
                params = MessageCreateParamsNonStreaming(
                    model=MODEL, max_tokens=12000, messages=[MessageParam(role="user", content=content)]
                )
                wanted[key(year, number, block, read)] = (params, (width * height / 750 * 0.05 + 4000 * 0.25) / 1e6)
    ready, note = batch.ensure(SOURCE, JOB, wanted)
    print(note)
    return ready


def tiles(year: int, number: int, block: int) -> list[bytes]:
    box = layout()[(year, number)][block]
    part = page(year, number).crop(box)
    turn = _words(year, number)[1]
    if turn:
        part = part.rotate(turn, expand=True)
    small = part.resize((part.width // 3, part.height // 3))
    angle = max(
        (a / 4 for a in range(-16, 17)),
        key=lambda a: float(
            (np.asarray(small.rotate(a, expand=False, fillcolor=255)) < 128).sum(axis=1).astype(float).var()
        ),
    )
    part = part.rotate(angle, expand=False, fillcolor=255, resample=Image.Resampling.BICUBIC)
    ink = np.asarray(part) < 128
    band = ink[int(part.height * 0.6) : int(part.height * 0.8)]
    sums = band.sum(axis=0)
    rows = sums > max(2, 0.15 * np.percentile(sums, 90))
    edges = np.flatnonzero(np.diff(np.concatenate([[False], rows, [False]]).astype(int)))
    runs = [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]
    clusters: list[list[int]] = []
    for a, b in runs:
        if clusters and a - clusters[-1][1] < 22:
            clusters[-1][1] = b
        else:
            clusters.append([a, b])
    clusters = [c for c in clusters if c[1] - c[0] >= 25]
    label, columns = clusters[0], clusters[1:]
    out = []
    for start in range(0, len(columns), 3):
        group = columns[start : start + 3]
        left = max(label[1] if start == 0 else (columns[start - 1][1] + group[0][0]) // 2, 0)
        right = min(
            group[-1][1] + 30 if start + 3 >= len(columns) else (group[-1][1] + columns[start + 3][0]) // 2, part.width
        )
        first = part.crop((0, 0, label[1] + 15, part.height))
        rest = part.crop((max(left, label[1] + 15), 0, right, part.height))
        tile = Image.new("L", (first.width + 40 + rest.width, part.height), 255)
        tile.paste(first, (0, 0))
        tile.paste(rest, (first.width + 40, 0))
        out.append(tile)
    images = []
    for tile in out:
        scale = min(1.0, (PIXELS * 1.4 / (tile.width * tile.height)) ** 0.5, 1568 / max(tile.size))
        tile = tile.resize((round(tile.width * scale), round(tile.height * scale)), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        tile.save(buffer, "PNG")
        images.append(buffer.getvalue())
    return images


def retry_key(year: int, number: int, block: int, tile: int, read: str) -> str:
    return f"{SOURCE}/{RETRY}/{year}/{number}/{block}/{tile}/{read}"


def retry() -> bool:
    wanted: dict[str, batch.Want] = {}
    for (year, number, block), title in RETRIES.items():
        for index, data in enumerate(tiles(year, number, block)):
            for read in READS:
                if read == "ink":
                    with Image.open(io.BytesIO(data)) as im:
                        buffer = io.BytesIO()
                        im.point(lambda v: 0 if v < 150 else 255).save(buffer, "PNG")
                        data_read = buffer.getvalue()
                else:
                    data_read = data
                text = (
                    PROMPT.format(year=year)
                    + f" The image is a strip of one table of votes for {title}: the county label column and some of its "
                    f'columns. Transcribe it as one table with the title "{title}".'
                )
                content: list[ImageBlockParam | TextBlockParam] = [
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(data_read).decode(),
                        },
                    ),
                    TextBlockParam(type="text", text=text),
                ]
                wanted[retry_key(year, number, block, index, read)] = (
                    MessageCreateParamsNonStreaming(
                        model=MODEL, max_tokens=12000, messages=[MessageParam(role="user", content=content)]
                    ),
                    0.004,
                )
    ready, note = batch.ensure(SOURCE, RETRY, wanted)
    print(note)
    return ready


def joined(year: int, number: int, block: int, read: str) -> list[dict[str, object]]:
    found: list[dict[str, list[dict[str, list[object]]]]] = []
    index = 0
    while True:
        try:
            message = json.loads(raw.cached(SOURCE, retry_key(year, number, block, index, read)).path.read_text())
        except LookupError:
            break
        text = next((b["text"] for b in message["content"] if b.get("type") == "text"), "")
        match = re.search(r"\[.*\]", text, re.DOTALL)
        try:
            tables = json.loads(match[0]) if match else []
        except json.JSONDecodeError:
            tables = []
        found.append(next((t for t in tables if isinstance(t, dict) and t.get("rows")), {}))
        index += 1
    if not found or any(not t for t in found) or len({len(t["rows"]) for t in found}) != 1:
        return []
    merged: dict[str, object] = {
        "title": RETRIES[(year, number, block)],
        "district": None,
        "columns": [c for t in found for c in t.get("columns", [])],
        "rows": [
            {"label": t0["label"], "values": [v for t in found for v in t["rows"][i]["values"]]}
            for i, t0 in enumerate(found[0]["rows"])
        ],
    }
    return [merged]


def seen(year: int, number: int, block: int, read: str) -> list[dict[str, object]]:
    if (year, number, block) in RETRIES:
        return joined(year, number, block, read)
    message = json.loads(raw.cached(SOURCE, key(year, number, block, read)).path.read_text())
    text = next((b["text"] for b in message["content"] if b.get("type") == "text"), "")
    found = re.search(r"\[.*\]", text, re.DOTALL)
    try:
        return json.loads(found[0]) if found else []
    except json.JSONDecodeError:
        return []


def parse() -> None:
    if not ensure() or not retry():
        raise SystemExit(f"waiting for batch:{SOURCE}/{JOB} or {RETRY}")
    rows: list[dict[str, object]] = []
    failed: list[str] = []
    for (year, number), boxes in sorted(layout().items()):
        words: list[tuple[str, tuple[int, int, int, int]]] | None = None
        turn, size = 0, (0, 0)
        headings: dict[str, str | None] = {}
        for block, box in enumerate(boxes):
            reads: list[list[Shape]] = []
            for read in READS:
                shapes = []
                for table in seen(year, number, block, read):
                    if not isinstance(table, dict):
                        continue
                    if (shape := _shape(table, headings.get(read))) is not None:
                        shapes.append(shape)
                    headings[read] = _office(table.get("title")) or headings.get(read)
                reads.append(shapes)
            gray, ink = reads
            if not gray:
                continue
            if words is None:
                words, turn, size = _words(year, number)
            frame = _turned(box, turn, size)
            inside = [
                (d, b)
                for d, b in words
                if frame[0] <= (b[0] + b[2]) / 2 <= frame[2] and frame[1] <= (b[1] + b[3]) / 2 <= frame[3]
            ]
            printed = {d for d, _ in inside}
            used: set[int] = set()
            for index, first in enumerate(gray):
                match = next(
                    (
                        i
                        for i, t in enumerate(ink)
                        if i not in used and t.office == first.office and t.district == first.district
                    ),
                    None,
                )
                if match is not None:
                    used.add(match)
                settled, problems = _settle(first, None if match is None else ink[match], printed)
                if problems:
                    where = f"{year} p{number} b{block} t{index} {first.office} {first.district or ''}"
                    failed.extend(f"{where}: {p}" for p in problems)
                    continue
                located = _locate(settled, inside, frame, first.across)
                for k, (votes, status, unit, label) in list(settled.items()):
                    for (y, name, county), fixed in CORRECTED.items():
                        if y == year and name in label and county == unit and first.office == "senate":
                            settled[k] = (fixed, "corrected", unit, label)
                for (r, c), (votes, status, unit, label) in settled.items():
                    found, how = located[(r, c)]
                    rows.append(
                        {
                            "year": year,
                            "office": first.office,
                            "title": first.title,
                            "district": first.district,
                            "page": number,
                            "table": f"{year}:{number}:{block}:{index}",
                            "unit": unit,
                            "label": label,
                            "votes": votes,
                            "box": list(_turned(found, -turn, size)),
                            "located": how,
                            "status": status,
                        }
                    )
    for line in failed:
        print(line)
    values = pl.DataFrame(rows, infer_schema_length=None)
    print(values.group_by("office", "status").len().sort("office", "status"))
    print(values.group_by("located").len().sort("located"))
    values.write_parquet(PARSED / "values.parquet")


def _words(year: int, number: int) -> tuple[list[tuple[str, tuple[int, int, int, int]]], int, tuple[int, int]]:
    image = page(year, number)
    best: tuple[int, list[tuple[str, tuple[int, int, int, int]]], int] | None = None
    for turn in (0, 90, 270):
        cache = PARSED / "ocr" / (f"{year}-{number}.tsv" if turn == 0 else f"{year}-{number}-{turn}.tsv")
        if turn and not cache.exists():
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "page.png"
                image.rotate(turn, expand=True).save(path)
                cache.write_text(
                    subprocess.run(
                        ["tesseract", str(path), "stdout", "--psm", "6", "tsv"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                )
        found = scan.words(pdf(year).path, number, cache)
        tokens = [
            (digits, b)
            for text, b, _, _ in found
            for digits in {re.sub(r"\D", "", text), re.sub(r"\D", "", text.translate(scan.CONFUSABLE))}
            if digits
        ]
        score = sum(bool(re.fullmatch(r"[\d,.]{3,}", text)) for text, *_ in found)
        if best is None or score > best[0]:
            best = (score, tokens, turn)
        if turn == 0 and score >= 60:
            break
    assert best is not None
    return best[1], best[2], image.size


def _turned(box: tuple[int, int, int, int], turn: int, size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    x0, y0, x1, y1 = box
    match turn:
        case 90:
            return (y0, width - x1, y1, width - x0)
        case 270:
            return (height - y1, x0, height - y0, x1)
        case -90:
            return (width - y1, x0, width - y0, x1)
        case -270:
            return (y0, height - x1, y1, height - x0)
    return box


COUNTIES = [
    "Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland", "Essex", "Gloucester", "Hudson",
    "Hunterdon", "Mercer", "Middlesex", "Monmouth", "Morris", "Ocean", "Passaic", "Salem", "Somerset", "Sussex",
    "Union", "Warren",
]  # fmt: skip
SKIP = ("total", "plurality", "majority", "number", "whole", "names", "ballots", "rejected", "registered", "party")


def _plain(text: object) -> str:
    return re.sub(r"[^a-z]", "", str(text or "").lower())


def _county(label: object) -> tuple[str | None, bool]:
    text = _plain(label)
    hit = next((c for c in COUNTIES if text.startswith(_plain(c)) or text.endswith(_plain(c))), None)
    if hit is None or len(text) > len(_plain(hit)) + len("part") + 2:
        return None, False
    return hit, "part" in text


@dataclass
class Shape:
    title: str
    office: str
    district: int | None
    across: bool
    units: list[str | None]
    names: list[str | None]
    total: int | None
    grid: list[list[int | None]]


def _office(title: object) -> str | None:
    text = _plain(title)
    if "average" in text or "military" in text:
        return None
    if "congress" in text or "representatives" in text:
        return "house"
    if "senat" in text and ("united" in text or "us" in text):
        return "senate"
    if "governor" in text:
        return "governor"
    if "president" in text:
        return "president"
    return None


def _shape(table: dict[str, object], heading: str | None) -> Shape | None:
    title = _plain(table.get("title"))
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    listed = table.get("rows")
    rows = (
        [r for r in listed if isinstance(r, dict) and isinstance(r.get("values"), list)]
        if isinstance(listed, list)
        else []
    )
    assert isinstance(columns, list)
    if "average" in title or "military" in title or not rows:
        return None
    district = table.get("district")
    office = _office(title)
    if office is None:
        office = heading if heading in ("president", "senate", "governor") else "statewide"
    if office == "house" and not isinstance(district, int):
        return None
    across = office != "house"
    labels = [r.get("label") for r in rows]
    heads = [str(c or "") for c in columns]
    if across:
        units = [_unit(label) for label in labels]
        names: list[str | None] = [
            None if any(_plain(h).startswith(s) for s in SKIP) or not _plain(h) else h for h in heads
        ]
        totals = [i for i, label in enumerate(labels) if "total" in _plain(label)]
        if sum(u is not None for u in units) < 15:
            return None
    else:
        units = [_unit(h) for h in heads]
        names = [None if "total" in _plain(label) else str(label) for label in labels]
        totals = [i for i, h in enumerate(heads) if "total" in _plain(h)]
        if not totals and heads and not _plain(heads[-1]) and len(rows[0]["values"]) == len(heads):
            totals = [len(heads) - 1]
        if not any(u is not None for u in units):
            return None
    return Shape(
        str(table.get("title") or ""),
        office,
        district if isinstance(district, int) and office == "house" else None,
        across,
        units,
        names,
        totals[0] if totals else None,
        [[v if isinstance(v, int) and not isinstance(v, bool) else None for v in r["values"]] for r in rows],
    )


def _unit(label: object) -> str | None:
    county, part = _county(label)
    return None if county is None else f"part of {county}" if part else county


def _cells(shape: Shape) -> tuple[list[str], dict[tuple[int, str], tuple[int | None, int, int]], dict[int, int | None]]:
    grid, total = shape.grid, shape.total
    if shape.across:
        picks = [c for c, name in enumerate(shape.names) if name]
        lines = [(r, u) for r, u in enumerate(shape.units) if u]
    else:
        picks = [r for r, name in enumerate(shape.names) if name and r < len(grid)]
        lines = [(c, u) for c, u in enumerate(shape.units) if u]
    names = [str(shape.names[k]) for k in picks]
    cells: dict[tuple[int, str], tuple[int | None, int, int]] = {}
    totals: dict[int, int | None] = {}
    for n, k in enumerate(picks):
        for i, unit in lines:
            r, c = (i, k) if shape.across else (k, i)
            if r < len(grid) and c < len(grid[r]):
                cells[(n, unit.removeprefix("part of "))] = (grid[r][c], r, c)
        if total is not None:
            r, c = (total, k) if shape.across else (k, total)
            totals[n] = grid[r][c] if r < len(grid) and c < len(grid[r]) else None
    return names, cells, totals


def _settle(
    first: Shape, second: Shape | None, printed: set[str]
) -> tuple[dict[tuple[int, int], tuple[int, str, str, str]], list[str]]:
    names, one, sums = _cells(first)
    others, two, again = _cells(second) if second is not None else (names, one, sums)
    if len(names) != len(others):
        return {}, [f"the reads find {len(names)} and {len(others)} candidates"]
    settled: dict[tuple[int, int], tuple[int, str, str, str]] = {}
    problems: list[str] = []
    for n, name in enumerate(names):
        units = sorted({u for k, u in one if k == n} | {u for k, u in two if k == n})
        values = [((one.get((n, u)) or (None,))[0], (two.get((n, u)) or (None,))[0]) for u in units]
        balanced = n in sums or n in again
        if balanced:
            values.append((sums.get(n), again.get(n)))
        ruled = set()
        for i, (x, y) in enumerate(values):
            if x != y and (str(x) in printed) != (str(y) in printed):
                values[i] = (x, x) if str(x) in printed else (y, y)
                ruled.add(i)
        disputed = [i for i, (x, y) in enumerate(values) if x != y]
        if len(disputed) > 8:
            problems.append(f"{name}: {len(disputed)} cells disagree")
            continue
        if disputed and not balanced:
            problems.append(f"{name}: no printed total and {len(disputed)} cells disagree")
            continue
        if second is None and not balanced:
            problems.append(f"{name}: one read and no printed total")
            continue
        fits = []
        for picks in itertools.product((0, 1), repeat=len(disputed)):
            trial = [v[0] for v in values]
            for i, p in zip(disputed, picks):
                trial[i] = values[i][p]
            if not balanced or (trial[-1] is not None and sum(v or 0 for v in trial[: len(units)]) == trial[-1]):
                fits.append(tuple(trial))
        fits = list(dict.fromkeys(fits))
        status = "agreed" if second is not None else "single"
        if not fits and not disputed and not ruled and second is not None:
            fits, status = [tuple(v[0] for v in values)], "unbalanced"
            if all(str(v) in printed for v in fits[0][: len(units)] if v is not None):
                status = "printed"
        if len(fits) != 1:
            problems.append(f"{name}: {len(fits)} ways to balance {values}")
            continue
        for i, unit in enumerate(units):
            if (votes := fits[0][i]) is None:
                continue
            _, r, c = one.get((n, unit)) or two[(n, unit)]
            how = "balanced" if i in disputed else "ocr" if i in ruled else status
            settled[(r, c)] = (votes, how, unit, name)
    return settled, problems


def _locate(
    settled: dict[tuple[int, int], tuple[int, str, str, str]],
    words: list[tuple[str, tuple[int, int, int, int]]],
    block: tuple[int, int, int, int],
    across: bool,
) -> dict[tuple[int, int], tuple[tuple[int, int, int, int], str]]:
    hits: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
    for key, (votes, *_) in settled.items():
        merged: list[tuple[int, int, int, int]] = []
        for _, box in sorted((w for w in words if w[0] == str(votes)), key=lambda w: w[1][2] - w[1][0]):
            if not any(abs(box[0] - m[0]) < 15 and abs(box[1] - m[1]) < 15 for m in merged):
                merged.append(box)
        hits[key] = merged
    anchors = {key: found[0] for key, found in hits.items() if len(found) == 1}
    if not across:
        for key, found in hits.items():
            if len(found) > 1 and max(b[1] for b in found) - min(b[1] for b in found) < 15:
                anchors.setdefault(key, min(found))
    for _ in range(2):
        ys, xs = _axes(anchors)
        anchors = {
            (r, c): b for (r, c), b in anchors.items() if abs((b[1] + b[3]) / 2 - ys[r]) < 25 and abs(b[2] - xs[c]) < 80
        }
    ys, xs = _axes(anchors)
    height = statistics.median(b[3] - b[1] for b in anchors.values()) if anchors else 30
    digit = statistics.median((b[2] - b[0]) / len(str(settled[k][0])) for k, b in anchors.items()) if anchors else 20
    wanted = {0: {r for r, _ in settled}, 1: {c for _, c in settled}}
    for side, axis in enumerate((ys, xs)):
        known = sorted(axis)
        if not known or (len(known) == 1 and side == 1):
            continue
        if len(known) > 1:
            step = statistics.median((axis[j] - axis[i]) / (j - i) for i, j in itertools.pairwise(known))
        else:
            step = height * 1.4
        for k in wanted[side] - set(known):
            below, above = [i for i in known if i < k], [i for i in known if i > k]
            if below and above:
                lo, hi = max(below), min(above)
                axis[k] = axis[lo] + (axis[hi] - axis[lo]) * (k - lo) / (hi - lo)
            elif below:
                axis[k] = axis[max(below)] + step * (k - max(below))
            else:
                axis[k] = axis[min(above)] - step * (min(above) - k)
    out: dict[tuple[int, int], tuple[tuple[int, int, int, int], str]] = {}
    for (r, c), (votes, *_) in settled.items():
        if (r, c) in anchors:
            out[(r, c)] = (anchors[(r, c)], "unique")
        elif r in ys and c in xs:
            near = [b for b in hits[(r, c)] if abs((b[1] + b[3]) / 2 - ys[r]) < 25 and abs(b[2] - xs[c]) < 80]
            width = digit * len(str(votes))
            out[(r, c)] = (
                (near[0], "aligned")
                if near
                else (
                    (round(xs[c] - width), round(ys[r] - height / 2), round(xs[c]), round(ys[r] + height / 2)),
                    "placed",
                )
            )
        else:
            out[(r, c)] = (block, "block")
    return out


def _axes(
    anchors: dict[tuple[int, int], tuple[int, int, int, int]],
) -> tuple[dict[int, float], dict[int, float]]:
    ys = {
        r: statistics.median((b[1] + b[3]) / 2 for (rr, _), b in anchors.items() if rr == r)
        for r in {r for r, _ in anchors}
    }
    xs = {c: statistics.median(b[2] for (_, cc), b in anchors.items() if cc == c) for c in {c for _, c in anchors}}
    return ys, xs


def _surnames(label: str) -> list[str]:
    words = [w for w in re.split(r"[\s,]+", re.sub(r"\(.*?\)", "", label)) if _plain(w) not in ("jr", "sr", "and")]
    return [_plain(w) for w in words if len(_plain(w)) > 2]


def _named(label: str, ballot: str, fuzzy: bool = True) -> bool:
    words = [
        w
        for w in re.sub(r"\(.*\)", "", ballot).replace(",", " ").replace("-", " ").split()
        if _plain(w) not in ("jr", "sr")
    ]
    if not words:
        return False
    surname = _plain(words[-1])
    return any(
        w == surname or fuzzy and difflib.SequenceMatcher(None, w, surname).ratio() >= 0.85 for w in _surnames(label)
    )


def _pick(cells: pl.DataFrame, known: list[tuple[UUID, str, str, int]]) -> dict[str, tuple[UUID, int]]:
    picked: dict[str, tuple[UUID, int, bool]] = {}
    signatures: set[tuple[tuple[str, int], ...]] = set()
    for (label,), part in cells.group_by("label", maintain_order=True):
        signature = tuple(sorted(zip(part["unit"], part["votes"])))
        if signature in signatures:
            continue
        signatures.add(signature)
        summed = int(part["votes"].sum())
        people = [(o, t, name) for o, kind, name, t in known if kind in PEOPLE]
        exact = [(o, t) for o, t, name in people if _named(str(label), name, False)]
        named = exact or [(o, t) for o, t, name in people if _named(str(label), name)]
        same = [(o, t) for o, t in named if t == summed]
        if len(same) == 1:
            picked[str(label)] = (*same[0], bool(exact))
        elif len({o for o, _ in named}) == 1:
            picked[str(label)] = (*named[0], bool(exact))
        elif not named and len(same := [(o, t) for o, t, _ in people if t == summed]) == 1:
            picked[str(label)] = (*same[0], False)
    claims: dict[UUID, list[str]] = {}
    for label, (option, _, _) in picked.items():
        claims.setdefault(option, []).append(label)
    for labels in claims.values():
        if len(labels) > 1:
            sure = [label for label in labels if picked[label][2]]
            for label in labels:
                if len(sure) != 1 or label != sure[0]:
                    picked.pop(label)
    return {label: (option, total) for label, (option, total, _) in picked.items()}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet").filter(pl.col("year").is_in(YEARS))
    shared = (
        frame.filter(pl.col("office") == "house")
        .group_by("year", "unit")
        .agg(pl.col("district").n_unique().alias("districts"))
        .filter(pl.col("districts") > 1)
    )
    split = {(int(y), str(u)) for y, u, _ in shared.iter_rows()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's federal and governor results by county from the state's general election results",
            polity="US",
        )
        counties: dict[str, tuple[str, UUID, UUID]] = {
            _plain(text): (code, unit, area)
            for code, unit, area, text in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '34%%'"""
            ).fetchall()
        }
        newjersey = cs.polity("US-NJ")
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id where scheme = 'us.contest'
                   and value ~ '^19[2-7][0-9][^:]*:NJ:(president|governor|senate|senate:special|house:[0-9]+)$'"""
            ).fetchall()
        )
        held: dict[str, tuple[UUID, UUID | None, date, int, int, list[tuple[UUID, str, str, int]]]] = {}

        def facts(key: str) -> tuple[UUID, UUID | None, date, int, int, list[tuple[UUID, str, str, int]]]:
            if key not in held:
                row = conn.execute(
                    """select c.election_id, c.district_area_id, e.date_from, coalesce(min(v.count_round), 0),
                              coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                       sum(v.value) filter (where v.measure = 1), 0)::bigint
                       from contest c join election e on e.id = c.election_id
                       left join result_set rs on rs.contest_id = c.id and rs.is_default
                       left join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
                       where c.id = %s group by 1, 2, 3""",
                    [contests[key]],
                ).fetchone()
                assert row is not None
                election, district_area, day, first, official = row
                known = conn.execute(
                    """select o.id, o.kind, coalesce(o.ballot_label, ''),
                              coalesce((select sum(v.value)::bigint from result_value v
                               where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1), 0)
                       from contest_option o left join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                       where o.contest_id = %s""",
                    [contests[key]],
                ).fetchall()
                held[key] = (election, district_area, day, first, official, known)
            return held[key]

        def keys(year: int, office: str, district: int | None) -> list[str]:
            if office == "house":
                ends = (f":NJ:house:{district}",)
            else:
                offices = ("president", "governor", "senate") if office == "statewide" else (office,)
                ends = tuple(f":NJ:{o}{tail}" for o in offices for tail in ("", ":special"))
            return [k for k in contests if k[:4] == str(year) and k.endswith(ends)]

        chosen: dict[str, list[pl.DataFrame]] = {}
        for (table,), cells in frame.group_by("table", maintain_order=True):
            year, office, district = int(cells["year"][0]), str(cells["office"][0]), cells["district"][0]
            labels = cells["label"].unique(maintain_order=True).to_list()
            scored = []
            for key in keys(year, office, district):
                known = facts(key)[5]
                hits = sum(
                    any(kind in PEOPLE and _named(label, name) for _, kind, name, _ in known) for label in labels
                )
                scored.append((hits, key))
            scored.sort(reverse=True)
            if not scored or scored[0][0] < min(2, len(labels)) or (len(scored) > 1 and scored[1][0] == scored[0][0]):
                print(f"{table} {office} {district or ''}: no contest ({scored[:2]})")
                continue
            chosen.setdefault(scored[0][1], []).append(cells)
        PAGES.mkdir(parents=True, exist_ok=True)
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        loaded = 0
        for key, tables in sorted(chosen.items()):
            election, district_area, day, first, official, known = facts(key)
            if len(tables) > 1 and any("recount" in _plain(t["title"][0]) for t in tables):
                tables = [t for t in tables if "recount" in _plain(t["title"][0])]
            picks = [_pick(t, known) for t in tables]
            fits = [
                sum(sum(t.filter(pl.col("label") == label)["votes"]) == total for label, (_, total) in pick.items())
                for t, pick in zip(tables, picks)
            ]
            merged = pl.concat(
                t.with_columns(
                    option=pl.col("label").replace_strict({k: str(v[0]) for k, v in pick.items()}, default=None)
                )
                for t, pick in zip(tables, picks)
            )
            if merged.unique(["option", "unit", "votes"]).select("option", "unit").is_duplicated().any():
                best = max(fits)
                if fits.count(best) > 1:
                    print(f"{key}: tables disagree")
                    continue
                tables, picks = [tables[fits.index(best)]], [picks[fits.index(best)]]
            picked = {k: v[0] for pick in picks for k, v in pick.items()}
            lines = pl.concat(tables)
            lines = lines.with_columns(
                option=pl.col("label").replace_strict({k: str(v) for k, v in picked.items()}, default=None)
            )
            if (
                lines.filter(pl.col("option").is_not_null())
                .unique(["option", "unit", "votes"])
                .select("option", "unit")
                .is_duplicated()
                .any()
            ):
                print(f"{key}: two columns name one candidate")
                continue
            loose = lines.filter(pl.col("option").is_null())
            signatures = {
                tuple(sorted(zip(part["unit"], part["votes"])))
                for _, part in lines.filter(pl.col("option").is_not_null()).group_by("table", "label")
            }
            kept: list[tuple[str, str]] = []
            for (table, label), part in loose.group_by("table", "label", maintain_order=True):
                signature = tuple(sorted(zip(part["unit"], part["votes"])))
                if signature not in signatures:
                    signatures.add(signature)
                    kept.append((str(table), str(label)))
            loose = loose.filter(
                pl.concat_str("table", "label", separator="\x1f").is_in([f"{t}\x1f{l}" for t, l in kept])
            )
            lines = loose.vstack(
                lines.filter(pl.col("option").is_not_null()).unique(
                    ["option", "unit", "votes"], keep="first", maintain_order=True
                )
            )
            totals = {str(o): t for o, _, _, t in known}
            shaky = [
                str(label)
                for (label, option), cells in lines.group_by("label", "option")
                if (cells["status"] == "unbalanced").any() and totals.get(str(option)) != int(cells["votes"].sum())
            ]
            if shaky:
                print(f"{key}: {shaky} miss the printed total and the held total, not emitted")
                continue
            if lines["option"].is_null().any():
                pool = cs.key("us.option", f"{key}:others:nj_results")
                cs.add("contest_option", id=pool, contest_id=contests[key], kind="others", ballot_label="Others")
                lines = lines.with_columns(pl.col("option").fill_null(str(pool)))
            counted = int(lines["votes"].sum())
            matched = lines.filter(pl.col("option").is_in([str(o) for o, *_ in known]))
            owned = sum(t for o, _, _, t in known if str(o) in set(matched["option"]))
            if not official or counted < 0.98 * official or abs(int(matched["votes"].sum()) - owned) > 0.02 * owned:
                print(f"{key}: {counted} by county against {official} held, not emitted")
                continue
            for option, kind, name, total in known:
                sum_ = int(lines.filter(pl.col("option") == str(option))["votes"].sum())
                if total and sum_ != total:
                    print(f"{key}: {name or kind} {sum_} by county, {total} in the default set")
            year = int(lines["year"][0])
            if year not in sources:
                title = f"Results of the General Election Held {day:%B} {day.day}, {year}"
                sources[year] = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=sources[year],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=URL.format(year=year),
                    license="Public domain",
                )
            artifact = cs.artifact(pdf(year), sources[year])
            for number in lines["page"].unique().to_list():
                if (year, number) in pages:
                    continue
                image = PAGES / f"{pdf(year).sha256}-{number}.png"
                if not image.exists():
                    page(year, number).resize(
                        (round(page(year, number).width / 3), round(page(year, number).height / 3))
                    ).save(image)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                pages[(year, number)] = (cs.page(artifact, number), width, height)
                cs.add(
                    "source_page",
                    id=pages[(year, number)][0],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{pdf(year).sha256}-{number}.png",
                    width=width,
                    height=height,
                )
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contests[key],
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=False,
                completeness=round(min(counted / official, 1), 4),
            )
            district = lines["district"][0]
            units: dict[str, UUID] = {}
            for county in lines["unit"].unique(maintain_order=True).to_list():
                code, parent, area = counties[_plain(county)]
                if district is None or (year, county) not in split:
                    units[county] = parent
                    continue
                part = f"{year}:{code}:{district}"
                units[county] = cs.unit("us.county_part", part)
                cs.add(
                    "reporting_unit",
                    id=units[county],
                    polity_id=newjersey,
                    scheme_code="us.county_part",
                    code=part,
                    parent_id=parent,
                    area_relation="within",
                    area_id=area,
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                if district_area is not None:
                    cs.add(
                        "reporting_unit_area",
                        unit_id=units[county],
                        area_id=district_area,
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            for (county, option), cells in lines.group_by("unit", "option", maintain_order=True):
                unit = units[str(county)]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    flags=DIGITIZED | (DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                boxes = [list(box) for box in cells["box"]]
                number, width, height = pages[(year, int(cells["page"][0]))]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    page_id=number,
                    source_id=None,
                    x0=min(box[0] for box in boxes) / width / 3,
                    y0=min(box[1] for box in boxes) / height / 3,
                    x1=max(box[2] for box in boxes) / width / 3,
                    y1=max(box[3] for box in boxes) / height / 3,
                )
            loaded += 1
        print(f"{loaded} contests")
        return cs.write()
