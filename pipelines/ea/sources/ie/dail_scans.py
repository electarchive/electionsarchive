import base64
import io
import json
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image
from scipy import ndimage

from ea import batch, raw
from ea.store import DATA

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
    found = [
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
