import base64
import io
import json
from typing import Any

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw

SOURCE = "jp.ndl"
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
PROMPT = "1"
READS = 2
LONGEST = 1568
PAGES = {"right": (0.48, 0.90), "left": (0.06, 0.52)}
BANDS = ((0.02, 0.56), (0.44, 0.98))
READ_SYSTEM = (
    "You read a crop of one page of 衆議院議員総選挙一覧, a 1947–1967 Japanese House of Representatives election book. "
    "The text is vertical. Each table has its columns running right to left: a column of row labels at the right edge "
    "(選挙区及定員, 市(区)郡町村, 有権者数 男/女/計, 失格, 棄権, 無効, 得票数, or one candidate name per row), then one column "
    "per municipality, county (郡), subprefecture (支庁), city ward, or total (計, 合計), with its name printed "
    "vertically at the top. Figures are kanji numerals written top to bottom, with 、 as the thousands separator and "
    "〇 for zero, often with two digits squeezed into one character width (五六八 is 568, 一、二七八 is 1,278). A dash "
    "or bar (｜, —) in a cell means zero or nothing. The crop may cut the table: columns or rows at its edges can be "
    "partial, and the crop may lack the column names (bottom part of a page) or the row labels (left page of a spread). "
    "Transcribe every column right to left, and in each column every cell top to bottom, exactly as printed. Keep "
    "old kanji as printed (縣, 區, 廣). For a figure give its kanji as printed and its value in Arabic digits; for a "
    "cell with a name or a word, give the text as printed and null value. Give an empty cell as text '' with null "
    "value only when other columns have a figure on that row. Mark a column partial when the crop cuts it. Also copy "
    "the district heading (第一區 (四人)) and any other heading text you see."
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
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "partial": {"type": "boolean"},
                    "cells": {"type": "array", "items": CELL},
                },
                "required": ["name", "partial", "cells"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headings", "columns"],
    "additionalProperties": False,
}


def manifest(pid: str) -> dict[str, Any]:
    return json.loads(raw.cached(SOURCE, MANIFEST.format(pid)).path.read_text())


def spread(pid: str, canvas: int) -> raw.Artifact:
    return raw.cached(SOURCE, IMAGE.format(pid, canvas))


def tiles(pid: str, canvas: int) -> dict[str, tuple[tuple[int, int, int, int], Image.Image]]:
    image = Image.open(spread(pid, canvas).path).convert("L")
    out = {}
    for page, (left, right) in PAGES.items():
        for band, (top, bottom) in enumerate(BANDS):
            box = (
                int(image.width * left),
                int(image.height * top),
                int(image.width * right),
                int(image.height * bottom),
            )
            crop = image.crop(box)
            crop.thumbnail((LONGEST, LONGEST))
            out[f"{page}{band}"] = (box, crop)
    return out


def read_key(pid: str, canvas: int, tile: str, read: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{pid}/{canvas}/{tile}/{read}"


def read(pid: str, canvas: int, tile: str, read: int) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(pid, canvas, tile, read)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(crop: Image.Image, read: int) -> batch.Want:
    buffer = io.BytesIO()
    crop.save(buffer, "PNG", optimize=True)
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
                            "media_type": "image/png",
                            "data": base64.b64encode(buffer.getvalue()).decode(),
                        },
                    ),
                    TextBlockParam(
                        type="text",
                        text="Transcribe this table crop."
                        if read == 1
                        else "Transcribe this table crop, column by column from the right.",
                    ),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
    )
    return params, (crop.width * crop.height / 750 * price_in + 5000 * price_out) / 1e6


def fetch(election: int, first: int, last: int, job: str) -> None:
    pid = VOLUMES[election]
    wanted: dict[str, batch.Want] = {}
    for canvas in range(first, last + 1):
        for tile, (_, crop) in tiles(pid, canvas).items():
            for number in range(1, READS + 1):
                wanted[read_key(pid, canvas, tile, number)] = request(crop, number)
    print(batch.ensure(SOURCE, job, wanted)[1])
