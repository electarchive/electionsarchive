import base64
import io
import json
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "ie.dail_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
MODEL = "claude-haiku-5-5"
PROMPT = "1"
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


def paper(image: Image.Image) -> Image.Image:
    pixels = np.asarray(image.convert("L"))
    runs, start = [], None
    for index, flag in enumerate([*(pixels.mean(axis=0) > 100), False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((index - start, start, index))
            start = None
    _, x0, x1 = max(runs) if runs else (0, 0, pixels.shape[1])
    ink = pixels[:, x0:x1] < 100
    rows, columns = np.where(ink.sum(axis=1) > 3)[0], np.where(ink.sum(axis=0) > 3)[0]
    y0, y1 = 0, pixels.shape[0]
    if len(rows) and len(columns):
        y0, y1 = max(int(rows[0]) - 20, 0), min(int(rows[-1]) + 20, y1)
        x0, x1 = x0 + max(int(columns[0]) - 20, 0), x0 + min(int(columns[-1]) + 20, x1 - x0)
    return image.crop((x0, y0, x1, y1))


def pages(year: int) -> list[Path]:
    folder = PAGES / str(year)
    if not (folder / "done").exists():
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(
                ["pdftoppm", "-r", "150", "-gray", "-png", str(volume(year).path), f"{scratch}/p"], check=True
            )
            for path in sorted(Path(scratch).glob("p-*.png")):
                image = Image.open(path).convert("L")
                if image.height > image.width:
                    image = image.rotate(turn(image), expand=True)
                image = paper(image)
                image.thumbnail((LONGEST, LONGEST))
                image.save(folder / f"p{int(path.stem.split('-')[1]):03d}.png")
        (folder / "done").touch()
    return sorted(folder.glob("p*.png"))


def key(year: int, page: Path) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{year}/{page.stem}"


def fetch() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    sizes: dict[str, list[int]] = {}
    for year in VOLUMES:
        for page in pages(year):
            image = Image.open(page)
            sizes[key(year, page)] = [*image.size]
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            wanted[key(year, page)] = (
                MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=16000,
                    system=SYSTEM,
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
                                TextBlockParam(type="text", text="Transcribe this page."),
                            ],
                        }
                    ],
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                ),
                (image.width * image.height / 750 * price_in + 6000 * price_out) / 1e6,
            )
    (PARSED / "pages.json").write_text(json.dumps(sizes))
    print(batch.ensure(SOURCE, "pages", wanted)[1])
