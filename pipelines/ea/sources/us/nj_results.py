import base64
import io
import json
import re
import subprocess

import numpy as np
from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "us.nj_results"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
URL = "https://www.nj.gov/state/elections/assets/pdf/election-results/1920-1970/{year}-general-election.pdf"
YEARS = [1925, 1927, 1928, *range(1930, 1941), *range(1942, 1980)]
MODEL = "claude-haiku-5-5"
JOB = "tables"
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


def seen(year: int, number: int, block: int, read: str) -> list[dict[str, object]]:
    message = json.loads(raw.cached(SOURCE, key(year, number, block, read)).path.read_text())
    text = next((b["text"] for b in message["content"] if b.get("type") == "text"), "")
    found = re.search(r"\[.*\]", text, re.DOTALL)
    try:
        return json.loads(found[0]) if found else []
    except json.JSONDecodeError:
        return []


def parse() -> None:
    if not ensure():
        raise SystemExit(f"waiting for batch:{SOURCE}/{JOB}")
