import base64
import difflib
import io
import json
import re
import subprocess
import tempfile
import time
import unicodedata
import urllib.request
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import numpy as np
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image
from scipy import ndimage

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.sources.ie.dail import ALIASES as DAIL_ALIASES
from ea.sources.ie.dail import CEANN, EXHAUSTED, PARTY_CODES, QUOTA, REGISTERED, VALID, VOTES
from ea.sources.ie.dail_pdf import ORDINALS
from ea.sources.ie.referendums import SIGNED
from ea.store import DATA, connect

SOURCE = "ie.dail_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
MODEL = "claude-haiku-5-5"
PROMPT = "1"
CELLS = "cells1"
HIGH = 300
INK = 185
TILE = 1450
LIBRARY = "https://opac.oireachtas.ie/Data/Library3/Official%20Publications/pdf/"
VOLUMES = {
    1954: ("15thDail_May1954_ByeElections14thDail_1951_1954.pdf", "20220714180512"),
    1957: ("16thDail_March1957_ByeElections15thDail_1954_1957.pdf", "20220714185147"),
    1961: ("17thDail_October1961_ByeElections16th.pdf", "20221220054656"),
    1965: ("18thDail_April1965_ByeElections17th_1961_1965.pdf", "20220717100722"),
    1969: ("19thDail_June1969_ByeElections18th_1965_1969.pdf", "20220723062919"),
    1973: ("20thDail_February1973_ByeElections19th_1969_1973.pdf", "20230422061711"),
    1977: ("21stDail_June1977_ByeElections20th_1973_1977.pdf", "20220802192103"),
    1981: ("22ndDail_June1981_ByeElections21st_1977_1981.pdf", "20241129132606"),
    19822: ("23rdDail_Feb1982_NoByeElections.pdf", "20230611152426"),
    198211: ("24thDail_Nov1982_ByeElections23rd.pdf", "20220810062847"),
    1987: ("25thDail_February1987_ByeElections24th_1982_1987.pdf", "20221210165409"),
    1989: ("26thDail_June1989.pdf", "20220831205240"),
    1992: ("27thDail_November1992.pdf", "20220815063006"),
    1997: ("28thDail_June1997.pdf", "20221210165715"),
}
WORDS = {"count", "transfer", "result", "votes", "surplus", "candidates", "quota", "electorate", "poll", "seats"}
LONGEST = 1568
SYSTEM = (
    "You transcribe one scanned page of an Irish Stationery Office volume of Dáil election results (general "
    "elections and by-elections, 1951 to 1997). Each constituency's count is a table: a heading (election and "
    "constituency), total electorate, valid poll, number of seats, and quota; columns for the first count (votes) "
    "and then, for each later count, a transfer column (signed, such as + 30 or - 2,178) and a result column "
    "(the running total); one row per candidate, then a non-transferable papers row and a total row; and the names "
    "of candidates elected below. A page may hold one table, parts of two tables (a table cut by the page edge or "
    "continued from the facing page), a summary table, or front matter. Transcribe exactly as printed, without "
    "correcting, summing, or guessing anything. For each count table give: the printed heading lines; the "
    "constituency name; `complete`, true only when the whole table (header, every candidate row, and the total row) "
    "is visible on this page; electorate, valid poll, seats, and quota as printed; `counts`, each count's column "
    "heading in order (such as `Second Count: Transfer of Burke's (Dick) Surplus`); `rows`, every table row from "
    "top to bottom, including the non-transferable and total rows, each with the row label exactly as printed (name, "
    "party in parentheses, and any marks such as * or †), `cells`, every cell from left to right as printed (the "
    "first-count votes, then for each later count its transfer cell and its result cell; a dash or blank is `-`), "
    "and `box`, the row's box as integers from 0 to 1000 across the image's width and height [x0, y0, x1, y1]; the "
    "names of candidates elected as printed; and every footnote. Cells keep their printed signs and commas. "
    "Under `other`, say in one line what else the page holds."
)
TEXT = {"type": "string"}
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "array", "items": TEXT},
                    "constituency": TEXT,
                    "complete": {"type": "boolean"},
                    "electorate": TEXT,
                    "valid_poll": TEXT,
                    "seats": TEXT,
                    "quota": TEXT,
                    "counts": {"type": "array", "items": TEXT},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": TEXT,
                                "cells": {"type": "array", "items": TEXT},
                                "box": {"type": "array", "items": {"type": "integer"}},
                            },
                            "required": ["label", "cells", "box"],
                            "additionalProperties": False,
                        },
                    },
                    "elected": {"type": "array", "items": TEXT},
                    "footnotes": {"type": "array", "items": TEXT},
                },
                "required": [
                    "heading",
                    "constituency",
                    "complete",
                    "electorate",
                    "valid_poll",
                    "seats",
                    "quota",
                    "counts",
                    "rows",
                    "elected",
                    "footnotes",
                ],
                "additionalProperties": False,
            },
        },
        "other": TEXT,
    },
    "required": ["tables", "other"],
    "additionalProperties": False,
}


CELL_SYSTEM = (
    "You transcribe a crop of one count table from an Irish Stationery Office volume of Dáil election results. The "
    "crop joins the table's first column (candidate names, then the non-transferable papers row and the total row) "
    "to a run of its numeric columns, cut along the printed rules; a thick black bar marks the join. The column "
    "headings are at the top. Transcribe exactly as printed, without correcting, summing, or guessing anything. Give "
    "`headings`, each numeric column's heading from left to right (such as `First Count Votes`, `Transfer of "
    "Murray's Votes`, or `Result`). Give `rows`, every row under the headings from top to bottom, including the "
    "non-transferable and total rows: `label`, the row label exactly as printed (name, party in parentheses, and "
    "marks such as * or †); `cells`, one string per numeric column from left to right, keeping printed signs and "
    "commas (a dash is `-`, an empty cell is ``; when a cell holds a transfer printed above a result, give both "
    "separated by one space, such as `+62 4,956`); and `top` and `bottom`, the row's vertical extent as integers "
    "from 0 to 1000 of the image height. A label printed on two lines is one row."
)
CELL_TEXT = "Transcribe this table crop. It has {count} numeric columns after the name column."
CELL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "headings": {"type": "array", "items": TEXT},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": TEXT,
                    "cells": {"type": "array", "items": TEXT},
                    "top": {"type": "integer"},
                    "bottom": {"type": "integer"},
                },
                "required": ["label", "cells", "top", "bottom"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headings", "rows"],
    "additionalProperties": False,
}


def url(year: int) -> str:
    return LIBRARY + VOLUMES[year][0]


def volume(year: int) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url(year))
    except LookupError:
        pass
    name, stamp = VOLUMES[year]
    for attempt in range(1, 6):
        try:
            content = urllib.request.urlopen(f"https://web.archive.org/web/{stamp}id_/{url(year)}", timeout=600).read()
            if content[:4] == b"%PDF" and b"%%EOF" in content[-2048:]:
                return raw.store(
                    SOURCE,
                    url(year),
                    content,
                    suffix=".pdf",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/pdf",
                )
            print(name, attempt, "incomplete")
        except OSError as error:
            print(name, attempt, repr(error)[:100])
        time.sleep(20 * attempt)
    raise SystemExit(f"could not fetch {name}")


def turn(image: Image.Image) -> int:
    small = image.copy()
    small.thumbnail((1600, 1600))
    found: dict[int, int] = {}
    with tempfile.TemporaryDirectory() as folder:
        for angle in (90, 270):
            small.rotate(angle, expand=True).save(f"{folder}/p.png")
            text = subprocess.run(
                ["tesseract", f"{folder}/p.png", "-", "--psm", "6"], check=False, capture_output=True, text=True
            ).stdout
            found[angle] = sum(re.sub(r"[^a-z]", "", word.lower()) in WORDS for word in text.split())
    return max(found, key=lambda angle: found[angle])


def crop(image: Image.Image) -> tuple[int, int, int, int]:
    pixels = np.asarray(image.convert("L"))
    runs, start = [], None
    for index, flag in enumerate([*(pixels.mean(axis=0) > 100), False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((index - start, start, index))
            start = None
    _, x0, x1 = max(runs) if runs else (0, 0, pixels.shape[1])
    region = pixels[:, x0:x1]
    height = pixels.shape[0]
    for threshold in (100, int(np.median(region)) - 50):
        ink = region < threshold
        rows, columns = np.where(ink.sum(axis=1) > 3)[0], np.where(ink.sum(axis=0) > 3)[0]
        if not len(rows) or not len(columns):
            continue
        y0, y1 = max(int(rows[0]) - 20, 0), min(int(rows[-1]) + 20, height)
        left, right = x0 + max(int(columns[0]) - 20, 0), x0 + min(int(columns[-1]) + 20, x1 - x0)
        if right - left >= (x1 - x0) / 2 and y1 - y0 >= height / 2:
            return left, y0, right, y1
    return x0, 0, x1, height


def frames(year: int) -> dict[str, dict]:
    folder = PAGES / str(year)
    if not (folder / "frames.json").exists():
        folder.mkdir(parents=True, exist_ok=True)
        found: dict[str, dict] = {}
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(
                ["pdftoppm", "-r", "150", "-gray", "-png", str(volume(year).path), f"{scratch}/p"], check=True
            )
            for path in sorted(Path(scratch).glob("p-*.png")):
                image = Image.open(path).convert("L")
                size = [*image.size]
                angle = turn(image) if image.height > image.width else 0
                image = image.rotate(angle, expand=True)
                box = crop(image)
                image = image.crop(box)
                image.thumbnail((LONGEST, LONGEST))
                target = folder / f"p{int(path.stem.split('-')[1]):03d}.png"
                same = target.exists() and np.array_equal(np.asarray(Image.open(target)), np.asarray(image))
                if not same:
                    image.save(target)
                found[target.stem] = {"angle": angle, "box": [*box], "size": size, "render": 1 if same else 2}
        (folder / "frames.json").write_text(json.dumps(found))
    return json.loads((folder / "frames.json").read_text())


def pages(year: int) -> list[Path]:
    return [PAGES / str(year) / f"{stem}.png" for stem in sorted(frames(year))]


def key(year: int, page: Path) -> str:
    render = frames(year)[page.stem]["render"]
    return f"anthropic-batch:{MODEL}/{PROMPT if render == 1 else f'{PROMPT}.{render}'}/{year}/{page.stem}"


def read(year: int, page: Path) -> dict | None:
    try:
        message = json.loads(raw.cached(SOURCE, key(year, page)).path.read_text())
    except LookupError:
        return None
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def high(year: int, stem: str) -> Image.Image:
    frame = frames(year)[stem]
    with tempfile.TemporaryDirectory() as scratch:
        number = int(stem[1:])
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(HIGH),
                "-gray",
                "-png",
                "-f",
                str(number),
                "-l",
                str(number),
                str(volume(year).path),
                f"{scratch}/p",
            ],
            check=True,
        )
        image = Image.open(next(Path(scratch).glob("p-*.png"))).convert("L")
    image = image.rotate(int(frame["angle"]), expand=True)
    x0, y0, x1, y1 = (round(value * HIGH / 150) for value in frame["box"])
    return image.crop((x0, y0, x1, y1))


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"\(contin[^)]*\)|continued.*", "", text.lower()))


def wanted_tables(year: int) -> list[tuple[Path, int, dict]]:
    found: list[tuple[Path, int, dict]] = []
    for page in pages(year):
        if (got := read(year, page)) is not None:
            found.extend((page, index, table) for index, table in enumerate(got["tables"]) if len(table["rows"]) >= 2)
    complete = {fold(table["constituency"]) for _, _, table in found if table["complete"]}
    return [item for item in found if item[2]["complete"] or fold(item[2]["constituency"]) not in complete]


def grids(pixels: np.ndarray) -> list[dict]:
    height, width = pixels.shape
    ink = pixels < INK
    thin = ink.copy()
    thin[:, 12:] &= ~ink[:, :-12]
    thin[:, :-12] &= ~ink[:, 12:]
    dark = ndimage.binary_closing(ndimage.binary_dilation(thin, structure=np.ones((1, 5))), structure=np.ones((15, 1)))
    run = np.zeros(width, dtype=np.int32)
    best = np.zeros(width, dtype=np.int32)
    end = np.zeros(width, dtype=np.int32)
    for y in range(height):
        run = (run + 1) * dark[y]
        longer = run > best
        best[longer] = run[longer]
        end[longer] = y
    lines: list[tuple[int, int, int]] = []
    tall = best > height * 0.12
    x = 0
    while x < width:
        if not tall[x]:
            x += 1
            continue
        start = x
        while x < width and tall[x]:
            x += 1
        if x - start <= 24:
            middle = start + int(np.argmax(best[start:x]))
            lines.append(((start + x) // 2, int(end[middle] - best[middle]), int(end[middle])))
    parent = list(range(len(lines)))

    def root(index: int) -> int:
        while parent[index] != index:
            index = parent[index]
        return index

    for first, (_, top_a, bottom_a) in enumerate(lines):
        for second in range(first + 1, len(lines)):
            _, top_b, bottom_b = lines[second]
            shared = min(bottom_a, bottom_b) - max(top_a, top_b)
            if shared > 0.6 * max(bottom_a - top_a, bottom_b - top_b):
                parent[root(second)] = root(first)
    clusters: dict[int, list[tuple[int, int, int]]] = {}
    for index, line in enumerate(lines):
        clusters.setdefault(root(index), []).append(line)
    found: list[dict] = [
        {
            "rules": [line[0] for line in members],
            "top": min(line[1] for line in members),
            "bottom": int(np.median([line[2] for line in members])),
        }
        for members in clusters.values()
        if len(members) >= 2
    ]
    tables: list[dict] = []
    for grid in found:
        rules = sorted(grid["rules"])
        merged = [rules[0]]
        for x in rules[1:]:
            if x - merged[-1] > 40:
                merged.append(x)
        if len(merged) < 2:
            continue
        step = int(np.median(np.diff(merged)))
        share = dark[grid["top"] : grid["bottom"]].mean(axis=0)
        for a, b in list(pairwise(merged)):
            if b - a > 1.6 * step:
                x = a + 40 + int(np.argmax(share[a + 40 : b - 40]))
                if share[x] > 0.25:
                    merged.append(x)
        merged.sort()
        if len(merged) > 2 and merged[1] - merged[0] > 2.5 * int(np.median(np.diff(merged[1:]))):
            left, merged = merged[0], merged[1:]
        else:
            left = max(merged[0] - 650, 0)
        last = merged[-1]
        width_next = merged[-2] - merged[-3] if len(merged) > 2 else step
        beyond = pixels[grid["top"] : grid["bottom"], last + 10 : min(last + width_next - 10, width)] < INK
        edges = [*merged, min(last + width_next, width)] if beyond.size and beyond.mean() > 0.01 else merged
        tables.append({"left": left, "edges": edges, "top": grid["top"], "bottom": grid["bottom"]})
    return sorted(tables, key=lambda table: table["top"])


def geometry(year: int, page: Path, tables: list[dict]) -> list[dict]:
    target = PARSED / "tiles" / str(year) / f"{page.stem}.json"
    if target.exists():
        return json.loads(target.read_text())
    image = high(year, page.stem)
    pixels = np.asarray(image)
    height, width = pixels.shape
    found_grids = grids(pixels)
    wanted = [index for index, table in enumerate(tables) if len(table["rows"]) >= 2]
    if len(found_grids) != len(wanted):
        stored = Image.open(page).height
        middles = {
            index: np.median(
                [(row["box"][1] + row["box"][3]) / 2 for row in tables[index]["rows"] if len(row["box"]) == 4]
            )
            / stored
            * height
            for index in wanted
        }
        pairs = (
            [
                (
                    index,
                    min(
                        found_grids,
                        key=lambda grid, middle=middle: (
                            0
                            if grid["top"] <= middle <= grid["bottom"]
                            else min(abs(grid["top"] - middle), abs(grid["bottom"] - middle))
                        ),
                    ),
                )
                for index, middle in middles.items()
            ]
            if found_grids
            else []
        )
        print(year, page.stem, f"{len(found_grids)} grids for {len(wanted)} tables")
    else:
        pairs = list(zip(wanted, found_grids, strict=True))
    found: list[dict] = []
    for index, grid in pairs:
        label = [grid["left"], grid["edges"][0]]
        groups: list[list[list[int]]] = [[]]
        for column in pairwise(grid["edges"]):
            if groups[-1] and label[1] - label[0] + column[1] - groups[-1][0][0] > TILE:
                groups.append([])
            groups[-1].append([*column])
        first = 0
        for group in groups:
            found.append(
                {
                    "table": index,
                    "first": first,
                    "label": label,
                    "columns": group,
                    "top": max(grid["top"] - 10, 0),
                    "bottom": min(grid["bottom"] + 10, height),
                    "size": [width, height],
                }
            )
            first += len(group)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(found))
    return found


def cell_key(year: int, page: Path, tile: dict) -> str:
    last = tile["first"] + len(tile["columns"]) - 1
    return f"anthropic-batch:{MODEL}/{CELLS}/{year}/{page.stem}/t{tile['table']}/c{tile['first']}-{last}"


def tile_image(tile: dict, image: Image.Image) -> Image.Image:
    top, bottom = tile["top"], tile["bottom"]
    label = image.crop((tile["label"][0], top, tile["label"][1] + 3, bottom))
    body = image.crop((tile["columns"][0][0] - 3, top, tile["columns"][-1][1] + 3, bottom))
    joined = Image.new("L", (label.width + 8 + body.width, bottom - top), 255)
    joined.paste(label, (0, 0))
    joined.paste(Image.new("L", (4, bottom - top), 0), (label.width + 2, 0))
    joined.paste(body, (label.width + 8, 0))
    joined.thumbnail((LONGEST, LONGEST))
    return joined


def message(image: Image.Image, system: str, text: str, schema: dict[str, object]) -> MessageCreateParamsNonStreaming:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(buffer.getvalue()).decode(),
                        },
                    ),
                    TextBlockParam(type="text", text=text),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )


def fetch() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for year in VOLUMES:
        for page in pages(year):
            image = Image.open(page)
            wanted[key(year, page)] = (
                message(image, SYSTEM, "Transcribe this page.", SCHEMA),
                (image.width * image.height / 750 * price_in + 6000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "pages", wanted)[1])
    cells: dict[str, batch.Want] = {}
    for year in VOLUMES:
        chosen = {(page.stem, index) for page, index, _ in wanted_tables(year)}
        for page in pages(year):
            got = read(year, page)
            if got is None or not any(stem == page.stem for stem, _ in chosen):
                continue
            image: Image.Image | None = None
            for tile in geometry(year, page, got["tables"]):
                if (page.stem, tile["table"]) not in chosen:
                    continue
                image = image or high(year, page.stem)
                crop = tile_image(tile, image)
                cells[cell_key(year, page, tile)] = (
                    message(crop, CELL_SYSTEM, CELL_TEXT.format(count=len(tile["columns"])), CELL_SCHEMA),
                    (crop.width * crop.height / 750 * price_in + 2000 * price_out) / 1e6,
                )
    print(batch.ensure(SOURCE, "cells", cells)[1])


NUMBER = re.compile(r"([+-]*)\s*(\d{1,3}(?:[,.]\d{3})+|\d+)")
TOTAL = re.compile(r"^\W*total")
SPOILT = re.compile(r"non.?transfer|not effective|papers")


def amounts(text: str) -> list[int]:
    found = []
    for sign, digits in NUMBER.findall(text.replace("—", "").replace("–", "-").replace("\u2212", "-")):
        value = int(re.sub(r"\D", "", digits))
        found.append(-value if "-" in sign else value)
    return found


def answer(path: Path) -> dict:
    message = json.loads(path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def cell_read(year: int, page: Path, tile: dict) -> dict | None:
    try:
        return answer(raw.cached(SOURCE, cell_key(year, page, tile)).path)
    except LookupError:
        return None


def person(label: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"\(.*", "", label.lower()).split(",")[0])


def role(label: str) -> str:
    folded = label.lower()
    if TOTAL.search(folded):
        return "total"
    if SPOILT.search(folded):
        return "spoilt"
    return "candidate"


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, fold(a), fold(b)).ratio()


def align(base: list[dict], other: list[dict], numbers: bool = False) -> list[int | None]:
    def score(row: dict, candidate: dict, index: int, position: int) -> float:
        points = similar(row["label"], candidate["label"]) + (role(row["label"]) == role(candidate["label"]))
        if numbers:
            mine = {value for cell in row["cells"] for value in amounts(cell) if value > 0}
            theirs = {value for cell in candidate["cells"] for value in amounts(cell) if value > 0}
            points += 2 * len(mine & theirs) / max(len(mine | theirs), 1)
            points += 0.3 * (len(base) == len(other) and index == position)
        return points

    matched: list[int | None] = []
    used: set[int] = set()
    for position, row in enumerate(base):
        kind = role(row["label"])
        options = [
            (score(row, candidate, index, position), index)
            for index, candidate in enumerate(other)
            if index not in used and (kind == "candidate") == (role(candidate["label"]) == "candidate")
        ]
        best = max(options, default=(0.0, -1))
        if best[0] >= (1.5 if kind != "candidate" else 0.6):
            used.add(best[1])
            matched.append(best[1])
        else:
            matched.append(None)
    return matched


def grid(year: int, page: Path, tiles: list[dict]) -> dict | None:
    reads = [
        (tile, got) for tile in sorted(tiles, key=lambda tile: tile["first"]) if (got := cell_read(year, page, tile))
    ]
    if not reads or not any(got["rows"] for _, got in reads):
        return None
    rows = [
        {"label": row["label"], "cells": []}
        for row in max((got["rows"] for _, got in reads), key=len)
        if row["label"].strip() and not re.fullmatch(r"\W*(votes|count|result|candidates?)\W*", row["label"].lower())
    ]
    headings: list[str] = []
    for tile, got in reads:
        width = len(tile["columns"])
        heads = [*got["headings"][:width], *[""] * (width - len(got["headings"]))]
        matched = align(rows, got["rows"])
        for row, index in zip(rows, matched, strict=True):
            cells = got["rows"][index]["cells"] if index is not None else []
            row["cells"].extend([*cells[:width], *[""] * (width - len(cells))])
        headings.extend(heads)
    keep = [column for column in range(len(headings)) if any(amounts(row["cells"][column]) for row in rows)]
    return {
        "headings": [headings[column] for column in keep],
        "rows": [{"label": row["label"], "cells": [row["cells"][column] for column in keep]} for row in rows],
    }


def layout(table: dict) -> list[str]:
    kinds: list[str] = []
    rows = table["rows"]
    for column, heading in enumerate(table["headings"]):
        cells = [row["cells"][column] for row in rows]
        head = heading.lower()
        if column == 0:
            signed = sum(bool(re.match(r"\s*[+\-\u2212]\s*\d", cell)) for cell in cells)
            kinds.append("transfer" if signed >= 2 else "first")
        elif sum(len(amounts(cell)) == 2 for cell in cells) >= 2 or "and result" in head:
            kinds.append("combined")
        elif "transfer" in head or "distribution" in head:
            kinds.append("transfer")
        elif "result" in head:
            kinds.append("result")
        else:
            kinds.append("result" if kinds[-1] == "transfer" else "transfer")
    return kinds


def series(table: dict) -> dict:
    kinds = layout(table)
    results: list[list[int | None]] = [[] for _ in table["rows"]]
    transfers: list[list[int | None]] = [[] for _ in table["rows"]]
    for column, kind in enumerate(kinds):
        for index, row in enumerate(table["rows"]):
            found = amounts(row["cells"][column])
            text = row["cells"][column].strip()
            if kind == "first":
                results[index].append(found[0] if found else None)
            elif kind == "transfer":
                if not results[index]:
                    results[index].append(None)
                transfers[index].append(found[0] if found else None)
            elif kind == "result":
                if len(transfers[index]) < len(results[index]):
                    transfers[index].append(None)
                results[index].append(found[-1] if found else None)
            elif len(found) == 2:
                transfers[index].append(found[0])
                results[index].append(found[1])
            elif found and re.match(r"[+\-\u2212]", text) and not (role(row["label"]) == "spoilt" and found[0] < 0):
                transfers[index].append(found[0])
                results[index].append(None)
            else:
                transfers[index].append(None)
                results[index].append(abs(found[0]) if found else None)
    return {"kinds": kinds, "results": results, "transfers": transfers}


def page_grid(table: dict) -> dict:
    width = max(len(row["cells"]) for row in table["rows"])
    return {
        "headings": ["first", *["transfer", "result"] * ((width - 1) // 2), *["transfer"] * ((width - 1) % 2)],
        "rows": [
            {"label": row["label"], "cells": [*row["cells"], *[""] * (width - len(row["cells"]))]}
            for row in table["rows"]
        ],
    }


def merge(page: dict | None, cells: dict | None) -> dict:
    base = page or cells
    assert base is not None
    sources = [got for got in (cells, page) if got is not None]
    matched = [
        align(base["rows"], got["rows"], True) if got is not base else list(range(len(base["rows"]))) for got in sources
    ]
    found = [series(got) for got in sources]
    if len(found) == 2:
        reference = [found[1]["results"][where] if where is not None else [] for where in matched[1]]
        mine = [found[0]["results"][where] if where is not None else [] for where in matched[0]]

        def agreement(shift: int) -> int:
            return sum(
                1
                for left, right in zip(mine, reference, strict=True)
                for count, value in enumerate(left)
                if value and 0 <= count + shift < len(right) and right[count + shift] == value
            )

        shift = max((0, 1, -1, 2), key=agreement)
        if shift > 0:
            found[0]["results"] = [[None] * shift + row for row in found[0]["results"]]
            found[0]["transfers"] = [[None] * shift + row for row in found[0]["transfers"]]
        elif shift < 0:
            found[0]["results"] = [row[-shift:] for row in found[0]["results"]]
            found[0]["transfers"] = [row[-shift:] for row in found[0]["transfers"]]
    counts = max(
        (count + 1 for got in found for row in got["results"] for count, value in enumerate(row) if value is not None),
        default=0,
    )
    rows: list[dict] = []
    for index, row in enumerate(base["rows"]):
        reads = [
            (got["results"][where], got["transfers"][where])
            for got, mapping in zip(found, matched, strict=True)
            if (where := mapping[index]) is not None
        ]
        rows.append({"label": row["label"], "role": role(row["label"]), "reads": reads})
    for row in rows:

        def consistent(read: tuple[list[int | None], list[int | None]]) -> float:
            results, moved = read
            good = sum(
                abs((results[count] or 0) - (results[count - 1] or 0)) == abs(moved[count - 1] or 0)
                for count in range(1, min(len(results), len(moved) + 1))
            )
            return good + 0.5 * (read is row["reads"][0])

        ranked = sorted(row["reads"], key=consistent, reverse=True)
        row["results"] = []
        for count in range(counts):
            prior = row["results"][-1] if row["results"] else 0
            value = None
            for results, moved in ranked:
                if count < len(results) and results[count] is not None:
                    value = results[count]
                    break
                step = moved[count - 1] if 0 < count <= len(moved) else None
                if step is not None and count < len(results):
                    value = 0 if step < 0 and row["role"] == "candidate" else prior
                    break
            row["results"].append(prior if value is None else value)
    return {"rows": rows, "counts": counts}


def steps(row: dict, count: int) -> set[int]:
    found = set()
    for results, moved in row["reads"]:
        if count - 1 < len(moved):
            value = moved[count - 1] or 0
            if value > 0 and count - 1 < len(results) and results[count - 1] == value:
                found.add(0)
            found.update(abs(option) for option in variants(value))
    return found


def named(names: list[str], elected: str) -> int | None:
    text = fold(elected)
    exact = [index for index, name in enumerate(names) if name and name in text]
    if exact:
        return max(exact, key=lambda index: len(names[index]))
    words = [fold(word) for word in re.split(r"[\s,]+", re.sub(r"\(.*", "", elected))]
    close = [
        (difflib.SequenceMatcher(None, name, word).ratio(), index)
        for index, name in enumerate(names)
        if len(name) >= 4
        for word in words
        if len(word) >= 4
    ]
    best = max(close, default=(0.0, None))
    return best[1] if best[0] >= 0.8 else None


def overlap(a: dict, b: dict) -> float:
    matched = align(a["rows"], b["rows"], True)
    return sum(index is not None for index in matched) / max(len(matched), 1)


def repair(merged: dict, valid: int | None) -> None:
    if valid is None:
        return
    for count in range(merged["counts"]):
        rows = [row for row in merged["rows"] if row["role"] != "total"]
        gap = valid - sum(row["results"][count] for row in rows)
        if not gap:
            continue
        fixes = [
            (row, option)
            for row in rows
            for results, _ in row["reads"]
            if count < len(results) and (option := results[count] or 0) - row["results"][count] == gap
        ]
        if len({id(row) for row, _ in fixes}) == 1:
            row, option = fixes[0]
            row["results"][count] = option


def poll(merged: dict, table: dict) -> int | None:
    printed = [
        value
        for row in merged["rows"]
        if row["role"] == "total"
        for results, _ in row["reads"]
        for value in results
        if value
    ]
    printed.extend(amounts(table["valid_poll"])[:1] * 2)
    quota, seats = amounts(table["quota"])[:1], amounts(table["seats"])[:1]
    if quota and seats:
        fits = [value for value in printed if value // (seats[0] + 1) + 1 == quota[0]]
        if fits:
            printed = fits
    return max(printed, key=printed.count) if printed else None


def droop(table: dict, valid: int | None) -> int | None:
    quota, seats = amounts(table["quota"])[:1], amounts(table["seats"])[:1]
    if not quota or not seats or valid is None:
        return quota[0] if quota else None
    target = valid // (seats[0] + 1) + 1
    return target if target in variants(quota[0]) else quota[0]


def assess(merged: dict, table: dict) -> list[str]:
    issues = []
    valid = merged["valid"]
    seats = (amounts(table["seats"]) or [None])[0]
    quota = droop(table, valid)
    rows = [row for row in merged["rows"] if row["role"] != "total"]
    if sum(row["role"] == "total" for row in merged["rows"]) != 1:
        issues.append("no total row")
    if valid is None or seats is None:
        issues.append("no valid poll or seats")
    elif quota != valid // (seats + 1) + 1:
        issues.append(f"quota {quota} is not the Droop quota of {valid} for {seats}")
    for count in range(merged["counts"]):
        summed = sum(row["results"][count] for row in rows)
        if summed != valid:
            issues.append(f"count {count + 1}: rows sum to {summed}, valid poll {valid}")
    for row in rows:
        for count in range(1, merged["counts"]):
            before, after = row["results"][count - 1], row["results"][count]
            moves = steps(row, count)
            if abs(after - before) not in moves and not (
                after == 0
                and row["role"] == "candidate"
                and not any(results[count] for results, _ in row["reads"] if count < len(results))
            ):
                issues.append(f"{row['label']}: count {count + 1}: {before} to {after}, transfers {sorted(moves)}")
    names = [person(row["label"]) for row in rows if row["role"] == "candidate"]
    for elected in table["elected"]:
        if named(names, elected) is None:
            issues.append(f"elected {elected} not among the candidates")
    return issues


def settle(table: dict, shaped: dict, cells: dict | None) -> dict:
    merged = merge(shaped, cells)
    words = {word for heading in table["counts"] for word in ORDINALS if heading.lower().startswith(word.lower())}
    printed = len(words | {"First"}) if words else 0
    if table["complete"] and 0 < printed < merged["counts"]:
        merged["counts"] = printed
        for row in merged["rows"]:
            row["results"] = row["results"][:printed]
    merged["valid"] = valid = poll(merged, table)
    repair(merged, valid)
    merged["changes"] = solve(merged, valid) if valid else []
    return merged


CORRECTED = {(1977, "p024"): {"CRUISE": {9: 6629}, "WOODS": {9: 7352}}}


def correct(year: int, page: str, merged: dict) -> dict:
    for key, fixes in CORRECTED.get((year, page), {}).items():
        for row in merged["rows"]:
            if key in row["label"].upper():
                for count, value in fixes.items():
                    row["results"][count] = value
                    for results, moved in row["reads"]:
                        if count < len(results):
                            results[count] = value
                        if count - 1 < len(moved):
                            moved[count - 1] = value - row["results"][count - 1]
    return merged


def tables(year: int) -> list[dict]:
    found = []
    for page, index, table in wanted_tables(year):
        target = PARSED / "tiles" / str(year) / f"{page.stem}.json"
        groups: dict[int, list[dict]] = {}
        for tile in json.loads(target.read_text()) if target.exists() else []:
            groups.setdefault(tile["table"], []).append(tile)
        built = [cells for tiles in groups.values() if (cells := grid(year, page, tiles)) is not None]
        shaped = page_grid(table)
        best = max(built, key=lambda cells: overlap(shaped, cells), default=None)
        cells = best if best is not None and overlap(shaped, best) >= 0.5 else None
        merged = correct(year, page.stem, settle(table, shaped, cells))
        if cells is not None and assess(merged, table):
            alone = correct(year, page.stem, settle(table, shaped, None))
            if not assess(alone, table):
                merged, cells = alone, None
        found.append(
            {
                "year": year,
                "page": page.stem,
                "index": index,
                "table": table,
                "merged": merged,
                "cells": cells is not None,
            }
        )
    return found


CONFUSED = {"3": "58", "5": "36", "6": "58", "8": "3609", "9": "80", "0": "89", "1": "7", "7": "1"}


def variants(value: int, wide: bool = False) -> list[int]:
    text = str(abs(value))
    found = [value]
    for position, digit in enumerate(text):
        for other in "0123456789".replace(digit, "") if wide else CONFUSED.get(digit, ""):
            if position == 0 and other == "0":
                continue
            found.append(int(text[:position] + other + text[position + 1 :]))
    return list(dict.fromkeys(found))


def solve(merged: dict, valid: int) -> list[tuple[int, int, int, int]]:
    rows = [row for row in merged["rows"] if row["role"] != "total"]
    counts = merged["counts"]
    moves = [[steps(row, count) if count else set() for count in range(counts)] for row in rows]
    read = [
        [{results[count] or 0 for results, _ in row["reads"] if count < len(results)} for count in range(counts)]
        for row in rows
    ]

    def broken(index: int, count: int) -> int:
        if count == 0 or count >= counts:
            return 0
        results = rows[index]["results"]
        step = abs(results[count] - results[count - 1])
        if step in moves[index][count] or (
            results[count] == 0 and rows[index]["role"] == "candidate" and 0 in read[index][count]
        ):
            return 0
        return 1

    def gap(count: int) -> int:
        return valid - sum(row["results"][count] for row in rows)

    def cost(index: int, first: int, last: int) -> tuple[int, int]:
        sums = sum(bool(gap(k)) for k in range(first, last + 1))
        return sums + 2 * sum(broken(index, k) for k in range(first, last + 2)), sum(
            abs(gap(k)) for k in range(first, last + 1)
        )

    changes: list[tuple[int, int, int, int]] = []
    wide = False
    for _ in range(200):
        best: tuple[float, int, int, int, int] | None = None
        for index, row in enumerate(rows):
            results = row["results"]
            for count in range(counts):
                if not (gap(count) or broken(index, count) or broken(index, count + 1)):
                    continue
                old = results[count]
                options = {old, *read[index][count]}
                for option in sorted({value for base in options for value in variants(base, wide)}):
                    if option == old or option < 0:
                        continue
                    delta = option - old
                    first, last = count, count
                    while True:
                        before = cost(index, first, last)
                        for k in range(first, last + 1):
                            results[k] += delta
                        after = cost(index, first, last)
                        for k in range(first, last + 1):
                            results[k] -= delta
                        if after[0] < before[0]:
                            known = option in read[index][first]
                            score = (after[0] - before[0]) - 0.5 * known + 0.01 * (last - first)
                            score += 0.1 * (after[1] >= before[1])
                            if best is None or score < best[0]:
                                best = (score, index, first, last, delta)
                        last += 1
                        if last >= counts or results[last] + delta not in {
                            value for base in {results[last], *read[index][last]} for value in variants(base, wide)
                        }:
                            break
        if best is None and not wide:
            wide = True
            continue
        if best is None:
            break
        _, index, first, last, delta = best
        for count in range(first, last + 1):
            changes.append((index, count, rows[index]["results"][count], rows[index]["results"][count] + delta))
            rows[index]["results"][count] += delta
    return changes


GENERALS = {
    1954: date(1954, 5, 18),
    1957: date(1957, 3, 5),
    1961: date(1961, 10, 4),
    1965: date(1965, 4, 7),
    1969: date(1969, 6, 18),
    1973: date(1973, 2, 28),
    1977: date(1977, 6, 16),
    1981: date(1981, 6, 11),
    19822: date(1982, 2, 18),
    198211: date(1982, 11, 24),
    1987: date(1987, 2, 17),
    1989: date(1989, 6, 15),
    1992: date(1992, 11, 25),
    1997: date(1997, 6, 6),
}
MONTHS = {
    month: number
    for number, month in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
POLLING = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\.?\s+(" + "|".join(MONTHS) + r"),?\s*(\d{4})")


def polling_day(table: dict) -> date | None:
    text = " ".join([*table["heading"], *table["footnotes"]]).lower()
    found = POLLING.search(text)
    return date(int(found[3]), MONTHS[found[2]], int(found[1])) if found else None


def firsts(entry: dict) -> set[int]:
    return {
        row["results"][0]
        for row in entry["merged"]["rows"]
        if row["role"] == "candidate" and row["results"] and row["results"][0]
    }


def select(year: int) -> list[dict]:
    found = [entry for entry in tables(year) if entry["merged"]["counts"] and firsts(entry)]
    for entry in found:
        entry["issues"] = assess(entry["merged"], entry["table"])
    ranked = sorted(
        found, key=lambda entry: (bool(entry["issues"]), not entry["table"]["complete"], -len(firsts(entry)))
    )
    kept: list[dict] = []
    for entry in ranked:
        mine = firsts(entry)
        if all(len(mine & firsts(other)) < 0.6 * len(mine) for other in kept):
            kept.append(entry)
    dated: dict[str, date] = {}
    for page in pages(year):
        for table in (read(year, page) or {"tables": []})["tables"]:
            if (day := polling_day(table)) is not None:
                dated.setdefault(fold(table["constituency"]), day)
    for entry in kept:
        table = entry["table"]
        entry["by"] = (amounts(table["seats"]) or [0])[0] == 1
        entry["day"] = (polling_day(table) or dated.get(fold(table["constituency"]))) if entry["by"] else GENERALS[year]
    return sorted(kept, key=lambda entry: (entry["page"], entry["index"]))


BY_ELECTIONS = {
    (1961, "p048"): date(1957, 11, 14),
    (1965, "p049"): date(1963, 5, 30),
    (1969, "p058"): date(1967, 11, 9),
    (1969, "p059"): date(1966, 12, 7),
    (1969, "p056"): date(1966, 12, 7),
    (1977, "p060"): date(1975, 3, 4),
    (198211, "p057"): date(1982, 5, 25),
    (1997, "p074"): date(1994, 11, 10),
    (1965, "p048"): date(1964, 2, 19),
    (1965, "p052"): date(1964, 2, 19),
    (1965, "p053"): date(1964, 7, 8),
    (1973, "p062"): date(1970, 4, 14),
    (1977, "p058"): date(1976, 6, 10),
    (1977, "p059"): date(1975, 3, 4),
    (1977, "p062"): date(1973, 11, 27),
    (198211, "p058"): date(1982, 7, 20),
    (1997, "p073"): date(1994, 11, 10),
    (1997, "p075"): date(1996, 4, 2),
    (1997, "p076"): date(1994, 6, 9),
    (1997, "p077"): date(1996, 4, 2),
    (1997, "p078"): date(1994, 6, 9),
    (1997, "p079"): date(1995, 6, 29),
}
ACTS = [
    (date(1948, 2, 4), 1947),
    (date(1961, 10, 4), 1961),
    (date(1969, 6, 18), 1969),
    (date(1977, 6, 16), 1974),
    (date(1981, 6, 11), 1980),
    (date(1987, 2, 17), 1983),
    (date(1992, 11, 25), 1990),
    (date(1997, 6, 6), 1995),
]
PARTIES = {
    "F.F.": "Fianna Fáil",
    "F.G.": "Fine Gael",
    "Lab.": "The Labour Party",
    "S.F.": "Sinn Féin",
    "G.P.": "Green Party",
    "G.A.": "Green Party",
    "P.D.": "Progressive Democrats",
    "W.P.": "The Workers' Party",
    "S.F.W.P.": "Sinn Féin The Workers' Party",
    "D.L.": "Democratic Left",
    "C.-P.": "Clann na Poblachta",
    "C.-T.": "Clann na Talmhan",
    "A.É.": "Aontacht Éireann",
    "C.P.I.": "Communist Party of Ireland",
    "D.S.P.": "Democratic Socialist Party",
    "N.L.P.": "Natural Law Party",
    "I.R.S.P.": "Irish Republican Socialist Party",
    "C.S.P.": "Christian Solidarity Party",
    "S.L.P.": "Socialist Labour Party",
    "S.P.": "Socialist Party",
    "C.C.P.": "Christian Centrist Party",
    "N.P.D.": "National Progressive Democrats",
    "S.W.P.": "Socialist Workers Party",
    "Y.I.": "Young Ireland",
    "R.P.": "Ratepayers",
    "N.P.": "National Party (1995)",
    "N.A.": "National Action",
    "C.G.": "Cine Gael",
    "I.W.L.": "Irish Workers' League",
    "C.D.": "Christian Democrat",
    "W.L.": "Workers' League",
    "S.K.I.A.": "South Kerry Independent Alliance",
}
DISTINCT = {"National Party (1995)": "National Party", "Socialist Party (1971)": "Socialist Party"}
MISREAD = {
    "F.F": "F.F.",
    "F.F.F.": "F.F.",
    "F.G": "F.G.",
    "F.C.": "F.G.",
    "LAB.": "Lab.",
    "Lab": "Lab.",
    "A.E.": "A.É.",
    "A.Ë.": "A.É.",
    "A.F.": "A.É.",
    "C.P.": "C.P.I.",
    "D S P": "D.S.P.",
    "N.L.P": "N.L.P.",
    "C.S.P": "C.S.P.",
}
INDEPENDENT = re.compile(r"^(ind\b|non|neamh|far\.)", re.IGNORECASE)
AFFILIATION = re.compile(r"\(([^()]*)\)\W*$")
DESCRIPTOR = re.compile(r"\s*[—–-]\s*(community|independent)\b", re.IGNORECASE)
SPELLING = {"leix": "laois", "laoghis": "laois", "laoighis": "laois", "laoise": "laois"}
FILLER = {"borough", "county", "city", "constituency", "of", "the", "and"}


def tokens(name: str) -> frozenset[str]:
    plain = "".join(char for char in unicodedata.normalize("NFKD", name.lower()) if not unicodedata.combining(char))
    words = re.sub(r"[^a-z]+", " ", plain).split()
    return frozenset(SPELLING.get(word, word) for word in words if word not in FILLER)


def act_of(day: date) -> int:
    return [act for start, act in ACTS if start <= day][-1]


def affiliation(year: int, label: str) -> tuple[str | None, bool | None]:
    found = AFFILIATION.search(label)
    text = found[1].strip() if found else ""
    if not text:
        return None, None
    if INDEPENDENT.match(text):
        return None, True
    text = MISREAD.get(text, text)
    if year == 1981 and text == "S.P.":
        return "Socialist Party (1971)", None
    if text not in PARTIES:
        raise LookupError(text)
    return PARTIES[text], None


def capital(word: str) -> str:
    mixed = re.match(r"^(Mc|Mac|O')([A-ZÁÉÍÓÚ].*)$", word)
    if mixed:
        return mixed[1] + mixed[2].capitalize()
    if not word.isupper():
        return word[0].upper() + word[1:]
    word = word.capitalize()
    for prefix in ("Mc", "O'"):
        if word.startswith(prefix) and len(word) > len(prefix) + 1:
            word = prefix + word[len(prefix)].upper() + word[len(prefix) + 1 :]
    return word


def full_name(label: str) -> str:
    text = DESCRIPTOR.sub("", AFFILIATION.sub("", label))
    text = re.sub(r"(\s*\.){2,}.*$", "", text)
    text = re.sub(r"^[\s*†‡+]+|[\s…,]+$", "", text)
    text = re.sub(r"(?<![A-Z])\.+$", "", text).strip(" ,")
    parts = re.split(r",\s*|\.\s+", text, maxsplit=1)
    if len(parts) == 1:
        parts = text.split(" ", 1)
    surname, given = parts[0], parts[1] if len(parts) > 1 else ""
    words = [capital(word) for word in f"{given} {surname}".split()]
    return " ".join("-".join(capital(piece) for piece in word.split("-")) for word in words)


def title(name: str) -> str:
    text = re.sub(r"(^|[\s(-])(\w)", lambda found: found[1] + found[2].upper(), re.sub(r"\s*-\s*", "-", name.lower()))
    return re.sub(r"\s+", " ", text.replace(" And ", " and ")).strip()


MARKED = re.compile(r"^[\s*]*[†‡]")


def given(label: str, text: str) -> float:
    words = [fold(word) for word in re.sub(r"\(.*", "", text).split()]
    return sum(
        max((difflib.SequenceMatcher(None, fold(word), other).ratio() for other in words if other), default=0.0)
        for word in re.sub(r"\(.*", "", label).replace(",", " ").split()
        if fold(word)
    )


def winners(rows: list[dict], elected: list[str]) -> set[int] | None:
    chosen: set[int] = set()
    for text in elected:
        names = [index for index, row in enumerate(rows) if row["surname"] and row["surname"] in fold(text)]
        if not names:
            if (index := named([row["surname"] for row in rows], text)) is None:
                return None
            names = [index]
        chosen.add(
            max(
                names,
                key=lambda index: similar(rows[index]["label"], text) + given(rows[index]["label"], text),
            )
        )
    return chosen if len(chosen) == len(elected) else None


def contest(entry: dict, areas: dict[int, dict[frozenset[str], str]]) -> dict:
    year, table, merged = entry["year"], entry["table"], entry["merged"]
    day = BY_ELECTIONS.get((year, entry["page"]), entry["day"])
    if day is None:
        raise LookupError("no polling day")
    act = act_of(day if entry["by"] else GENERALS[year])
    label = title(table["constituency"])
    if act in areas:
        code = areas[act].get(tokens(label))
        if code is None:
            raise LookupError(f"no {act} constituency named {label}")
    else:
        code = f"{act}:{slug(label)}"
    rows = []
    for row, printed in zip(merged["rows"], table["rows"], strict=True):
        if row["role"] == "total":
            continue
        party, independent = affiliation(year, row["label"]) if row["role"] == "candidate" else (None, None)
        rows.append(
            {
                "label": row["label"],
                "role": row["role"],
                "name": full_name(row["label"]) if row["role"] == "candidate" else None,
                "surname": person(row["label"]),
                "party": party,
                "independent": independent,
                "results": row["results"][: merged["counts"]],
                "box": printed["box"],
            }
        )
    spoilt = [row for row in rows if row["role"] == "spoilt"]
    for extra in spoilt[1:]:
        spoilt[0]["results"] = [
            left + right for left, right in zip(spoilt[0]["results"], extra["results"], strict=True)
        ]
        spoilt[0]["box"] = [*spoilt[0]["box"][:3], extra["box"][3]]
        rows.remove(extra)
    deemed = [
        text
        for text in [*table["elected"], *table["footnotes"]]
        if MARKED.match(text) or "deemed to be elected" in text.lower()
    ]
    for row in rows:
        row["speaker"] = (
            row["role"] == "candidate"
            and bool(MARKED.match(row["label"]))
            and not any(row["results"])
            and any(row["surname"] in fold(text) for text in deemed)
        )
    candidates = [row for row in rows if row["role"] == "candidate" and not row["speaker"]]
    speakers = [row for row in rows if row["speaker"]]
    elected = [text for text in table["elected"] if not (speakers and text in deemed)]
    if len(speakers) > 1:
        raise LookupError("more than one candidate is deemed elected")
    chosen = winners(candidates, elected)
    if chosen is None:
        raise LookupError("elected names do not match the candidates one to one")
    seats = amounts(table["seats"])[0]
    if len(chosen) < seats:
        quota = droop(table, merged["valid"])
        reached = {
            index
            for index, row in enumerate(candidates)
            if quota is not None and row["results"] and (row["results"][-1] or 0) >= quota
        }
        if len(reached) == seats and chosen <= reached:
            chosen = reached
    if len(chosen) != amounts(table["seats"])[0]:
        raise LookupError(f"{len(chosen)} elected for {table['seats']} seats")
    for index, row in enumerate(candidates):
        row["elected"] = index in chosen
    for row in speakers:
        row["elected"] = True
    return {
        "year": year,
        "page": entry["page"],
        "by": entry["by"],
        "day": day.isoformat(),
        "general": GENERALS[year].isoformat(),
        "constituency": label,
        "act": act,
        "code": code,
        "seats": amounts(table["seats"])[0],
        "total": amounts(table["seats"])[0] + len(speakers),
        "quota": droop(table, merged["valid"]),
        "valid": merged["valid"],
        "electorate": (amounts(table["electorate"]) or [None])[0],
        "counts": merged["counts"],
        "resolved": len(merged["changes"]),
        "rows": rows,
    }


def parse() -> None:
    with connect() as conn:
        found = conn.execute(
            "select c.code, n.text from area a join area_code c on c.area_id = a.id"
            " join name n on n.entity_id = a.id and n.is_preferred"
            " where a.scheme_code = 'ie.dail' and c.code ~ '^19[4-9][0-9]:' and c.code !~ '^1974:'"
        ).fetchall()
    areas: dict[int, dict[frozenset[str], str]] = {}
    for code, text in found:
        areas.setdefault(int(code[:4]), {})[tokens(text)] = code
    kept, skipped = [], []
    for year in VOLUMES:
        for entry in select(year):
            where = {"year": year, "page": entry["page"], "constituency": entry["table"]["constituency"]}
            if entry["issues"]:
                skipped.append({**where, "issues": entry["issues"]})
                continue
            try:
                kept.append(contest(entry, areas))
            except LookupError as error:
                skipped.append({**where, "issues": [str(error)]})
    keys = [(item["day"], item["code"]) for item in kept]
    if len(keys) != len(set(keys)):
        raise ValueError("two tables for one contest")
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "contests.json").write_text(json.dumps(kept, ensure_ascii=False, indent=1))
    (PARSED / "skipped.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=1))
    print(len(kept), "contests kept,", len(skipped), "tables skipped")


RESOLVED = (
    "The scanned print makes some digits hard to tell apart, most often 3, 5, 6, 8, 9, and 0. Where a read of this "
    "table did not add up, each doubtful figure was replaced by the other reading of it or by a one-digit change, "
    "whichever makes every count sum to the valid poll and every candidate's total move by the printed transfer."
)
LICENSE = "Irish public sector information licence, CC BY 4.0 terms"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
DIGITIZED = 8


def emit() -> Path:
    found = json.loads((PARSED / "contests.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Dáil general elections from 1954 to 1997 and the by-elections between them, digitized from the "
                f"Stationery Office's printed results and transfers: {len(found)} constituency counts"
            ),
            polity="IE",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ireland = cs.polity("IE")
        nation = cs.area("ie.nation", "IE")
        system = cs.key("ie.electoral_system", "dail-eireann")
        body = cs.key("ie.body", "dail-eireann")
        if {system, body, nation} & cs.minted:
            raise ValueError("run ie.dail first")
        known: dict[str, list[UUID]] = {}
        for text, party in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'IE'"
        ).fetchall():
            known.setdefault(fold(text), []).append(party)
        strays = {
            str(value): entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'ie.dail.party'"
            ).fetchall()
        }
        parties: dict[str, UUID] = {}

        def party_id(label: str) -> UUID:
            if label not in parties:
                canonical = DAIL_ALIASES.get(label, label)
                if label in PARTY_CODES:
                    parties[label] = cs.key("ie.party", PARTY_CODES[label])
                elif label not in DISTINCT and slug(canonical) in strays:
                    parties[label] = strays[slug(canonical)]
                elif label not in DISTINCT and len(set(known.get(fold(canonical), ()))) == 1:
                    parties[label] = known[fold(canonical)][0]
                else:
                    parties[label] = cs.key("ie.dail.party", slug(canonical))
                    if parties[label] in cs.minted:
                        cs.add("party", id=parties[label], kind="party", polity_id=ireland)
                        name(parties[label], DISTINCT.get(label, label))
            return parties[label]

        sources: dict[int, UUID] = {}
        artifacts: dict[int, UUID] = {}
        for year in sorted({item["year"] for item in found}):
            when = GENERALS[year]
            title_text = (
                f"Election results and transfer of votes in the general election of {when:%B %Y} "
                "and the preceding by-elections"
            )
            sources[year] = cs.source("Stationery Office", title_text)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher="Stationery Office",
                title=title_text,
                url=url(year),
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=(
                    "Election results and transfer of votes, published by the Stationery Office and held by the "
                    "Oireachtas Library (opac.oireachtas.ie)."
                ),
            )
            artifacts[year] = cs.artifact(volume(year), sources[year])
        pages_done: dict[tuple[int, str], UUID] = {}

        def page_id(year: int, stem: str) -> UUID:
            if (year, stem) not in pages_done:
                path = PAGES / str(year) / f"{stem}.png"
                width, height = Image.open(path).size
                pages_done[year, stem] = cs.page(artifacts[year], int(stem[1:]))
                cs.add(
                    "source_page",
                    id=pages_done[year, stem],
                    artifact_id=artifacts[year],
                    page_number=int(stem[1:]),
                    image_key=f"pages/{SOURCE}/{year}/{stem}.png",
                    width=width,
                    height=height,
                )
            return pages_done[year, stem]

        elections: dict[tuple[str, bool], tuple[UUID, str]] = {}
        dated: set[UUID] = set()
        for item in sorted(found, key=lambda item: (item["day"], item["constituency"])):
            day = date.fromisoformat(item["day"])
            if (item["day"], item["by"]) not in elections:
                if item["by"]:
                    election = cs.key(f"{SOURCE}.election", f"{day}:dail")
                    path = f"ie/{day}-by-election"
                    label = (
                        f"{day.year} Dáil by-election"
                        if sum(other["by"] and other["day"] == item["day"] for other in found) == 1
                        else f"{day.year} Dáil by-elections"
                    )
                    kind = "by_election"
                else:
                    election = cs.key("ie.election", f"{day}:dail-eireann")
                    path = f"ie/{day}-general"
                    label = label_of(day)
                    kind = "general"
                cs.add("election", id=election, polity_id=ireland, kind=kind, date_from=day, date_to=day, status="held")
                name(election, label)
                cs.add("slug", path=path, entity_id=election)
                elections[item["day"], item["by"]] = (election, path)
            election, path = elections[item["day"], item["by"]]
            constituency, code = item["constituency"], item["code"]
            area = cs.area("ie.dail", code)
            if item["act"] == 1974 and area not in dated:
                dated.add(area)
                cs.add("area_code", area_id=area, code=code, valid_from=SIGNED[1974])
            if area in cs.minted:
                if item["act"] != 1974:
                    raise ValueError(f"{code} is not on record")
                cs.add("area", id=area, scheme_code="ie.dail")
                cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
                name(area, constituency)
            unit = cs.unit("ie.dail", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=ireland,
                    scheme_code="ie.dail",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, constituency)
            contest_key = f"{day}:dail-eireann:{slug(constituency)}"
            contest = cs.key("ie.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=item["total"],
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            if item["by"]:
                name(contest, f"{day.year} {constituency} by-election")
            else:
                name(contest, f"{label_of(day)} in {constituency}")
            cs.add("slug", path=f"{path}/{slug(constituency)}", entity_id=contest)
            if item["resolved"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=RESOLVED)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[item["year"]],
                is_default=True,
                completeness=1,
            )
            if item["total"] != item["seats"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=CEANN.format(contested=item["seats"], total=item["total"]),
                )
            page = page_id(item["year"], item["page"])
            values: list[tuple[UUID | None, int, int, int, list[int] | None]] = [
                (None, VALID, 0, item["valid"], None),
                (None, QUOTA, 0, item["quota"], None),
            ]
            if item["electorate"]:
                values.append((None, REGISTERED, 0, item["electorate"], None))
            position = 0
            for row in item["rows"]:
                if row["role"] == "spoilt":
                    values.extend(
                        (None, EXHAUSTED, count + 1, amount, row["box"])
                        for count, amount in enumerate(row["results"])
                        if count
                    )
                    continue
                position += not row["speaker"]
                key = f"{contest_key}:{fold(row['name'])}"
                if sum(fold(other["name"] or "") == fold(row["name"]) for other in item["rows"]) > 1:
                    key = f"{key}:{position}"
                person = cs.key(f"{SOURCE}.person", key)
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, row["name"])
                party = party_id(row["party"]) if row["party"] else None
                option = cs.key("ie.option", key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=None if row["speaker"] else position,
                    ballot_label=row["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("ie.candidacy", key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                if row["speaker"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                    continue
                results = row["results"]
                last = max((count for count, amount in enumerate(results) if amount), default=0)
                values.extend(
                    (option, VOTES, count + 1, amount, row["box"]) for count, amount in enumerate(results[: last + 1])
                )
                kind = "elected" if row["elected"] else "lost" if last == len(results) - 1 else "eliminated"
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            for option, measure, count_round, amount, box in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=count_round,
                    flags=DIGITIZED,
                    value=amount,
                )
                if box is not None:
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=count_round,
                        page_id=page,
                        x0=box[0] / 1000,
                        y0=box[1] / 1000,
                        x1=1.0,
                        y1=box[3] / 1000,
                    )
        fresh = {(row["entity_id"], row["body_md"]) for row in cs.rows["note"]}
        for note, entity, text in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
               where n.entity_id = any(%s) and n.kind = 'caveat' and c.pipeline = %s""",
            [[row["id"] for kind in ("contest", "result_set") for row in cs.rows[kind]], SOURCE],
        ).fetchall():
            if (entity, text) not in fresh:
                cs.add(
                    "note_drop",
                    note_id=note,
                    entity_id=entity,
                    body_md=text,
                    reason="A re-emit writes the count's caveats afresh.",
                )
        return cs.write()


def label_of(day: date) -> str:
    month = f"{day:%B} " if day.year == 1982 else ""
    return f"{month}{day.year} Irish general election"
