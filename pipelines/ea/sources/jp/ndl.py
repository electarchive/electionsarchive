import base64
import io
import json
from collections.abc import Iterable
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
