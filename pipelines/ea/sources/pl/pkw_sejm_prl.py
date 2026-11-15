import base64
import json
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "pl.pkw_sejm_prl"
PAGES = DATA / "pages" / SOURCE
MODEL = "claude-haiku-5-5"
PROMPT = "p1"
READS = ("a", "b")
ELI = "https://api.sejm.gov.pl/eli/acts/{act}/text.pdf"
NOTICES: dict[date, list[str]] = {
    date(1947, 1, 19): ["MP/1947/19", "MP/1947/31"],
    date(1952, 10, 26): ["MP/1952/1414"],
    date(1957, 1, 20): ["MP/1957/30"],
    date(1961, 4, 16): ["MP/1961/145"],
    date(1965, 5, 30): ["MP/1965/157"],
    date(1969, 6, 1): ["MP/1969/177"],
    date(1972, 3, 19): ["MP/1972/110"],
    date(1976, 3, 21): ["MP/1976/52"],
    date(1980, 3, 23): ["MP/1980/37"],
    date(1985, 10, 13): ["MP/1985/198"],
}

SYSTEM = (
    "You transcribe one scanned page of Monitor Polski, the Polish official gazette, holding part of the State "
    "Electoral Commission's notice of the results of an election to the Sejm (1947 to 1985). The notice has prose "
    "sections with national totals; a table of results by constituency (okręg wyborczy), one row per constituency "
    "with its number, seat, and figures such as persons entitled to vote, persons who voted, turnout percent, valid "
    "votes, votes for the lists of the Front, and a percent; a section listing, constituency by constituency, the "
    "votes for each candidate (a numbered name, the number of valid votes, and the percent of valid votes), and "
    "possibly the national list; and a section listing the deputies elected in each constituency (numbered names "
    "without votes). The 1947 notice instead has a table whose rows give the constituency, its valid votes, and the "
    "names of the deputies elected there. Transcribe exactly as printed, without correcting, summing, or guessing. "
    "Numbers keep their printed separators (dots, spaces, or commas). Keep Polish diacritics. A cell you cannot "
    "read is `?`. Give: `prose`, every prose paragraph that contains a number or names a section, verbatim, with "
    "section numerals (I, II, ...) as their own items; `table`, the constituency table on this page, with `columns`, "
    "its column headings in order, and `rows`, each with `cells` (every cell left to right, the constituency number "
    "first, a blank cell as an empty string), `names` (deputies printed in the row, if any, top to bottom), and "
    "`box`; `blocks`, every constituency block of candidate votes or elected names in reading order (columns "
    "left to right, top to bottom within each), each with `heading` (as printed, such as `Okręg Wyborczy nr 3 w "
    "Warszawie-Praga` or `Krajowa lista wyborcza`, or empty when the block continues from the previous page), "
    "`number` (the constituency number as printed, or empty), and `entries`, each with `position` (the printed "
    "number), `name` (as printed, surname first), `votes` and `percent` (as printed, empty when the block lists "
    "names only), and `box`. Every `box` is [x0, y0, x1, y1] as integers from 0 to 1000 across the image's width "
    "and height, covering the whole printed row or line. Under `other`, say in one line what else the page holds."
)
BOX = {"type": "array", "items": {"type": "integer"}}
TEXT = {"type": "string"}


def strict(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA = strict(
    {
        "prose": {"type": "array", "items": TEXT},
        "table": strict(
            {
                "columns": {"type": "array", "items": TEXT},
                "rows": {
                    "type": "array",
                    "items": strict(
                        {
                            "cells": {"type": "array", "items": TEXT},
                            "names": {"type": "array", "items": TEXT},
                            "box": BOX,
                        }
                    ),
                },
            }
        ),
        "blocks": {
            "type": "array",
            "items": strict(
                {
                    "heading": TEXT,
                    "number": TEXT,
                    "entries": {
                        "type": "array",
                        "items": strict({"position": TEXT, "name": TEXT, "votes": TEXT, "percent": TEXT, "box": BOX}),
                    },
                }
            ),
        },
        "other": TEXT,
    }
)


def url(act: str) -> str:
    return ELI.format(act=act)


def fetch() -> None:
    for acts in NOTICES.values():
        for act in acts:
            raw.fetch(SOURCE, url(act), suffix=".pdf")
    read()


def pages(act: str) -> list[Path]:
    artifact = raw.cached(SOURCE, url(act))
    PAGES.mkdir(parents=True, exist_ok=True)
    found = sorted(PAGES.glob(f"{artifact.sha256}-*.png"))
    if not found:
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(["pdftoppm", "-r", "150", "-gray", "-png", artifact.path, f"{scratch}/p"], check=True)
            for image in sorted(Path(scratch).glob("p-*.png")):
                shutil.move(image, PAGES / f"{artifact.sha256}-{int(image.stem.split('-')[1])}.png")
        found = sorted(PAGES.glob(f"{artifact.sha256}-*.png"))
    return sorted(found, key=lambda path: int(path.stem.split("-")[1]))


def key(act: str, page: Path, read: str) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}{read}/{act}/{page.stem.split('-')[1]}"


def message(image: Path, read: str) -> MessageCreateParamsNonStreaming:
    return MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=32000,
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
                            "data": base64.b64encode(image.read_bytes()).decode(),
                        },
                    ),
                    TextBlockParam(type="text", text=f"Transcribe this page (read {read})."),
                ],
            }
        ],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
    )


def read() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for acts in NOTICES.values():
        for act in acts:
            for page in pages(act):
                with Image.open(page) as image:
                    tokens = image.width * image.height / 750
                for one in READS:
                    wanted[key(act, page, one)] = (message(page, one), (tokens * price_in + 12000 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "pages", wanted)[1])


def answer(act: str, page: Path, read: str) -> dict | None:
    try:
        message = json.loads(raw.cached(SOURCE, key(act, page, read)).path.read_text())
    except LookupError:
        return None
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))
