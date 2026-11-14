import base64
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.jp import mic

SOURCE = "jp.mic_hoc"
RAW = mic.SOURCE
BASE = f"{mic.DATA_ROOT}/sangiin21"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
READS = 2
LONGEST = 1568
DPI = 200
TILES = {
    "landscape": {"top": (0.03, 0.02, 0.97, 0.56), "bottom": (0.03, 0.44, 0.97, 0.98)},
    "portrait": {
        "top": (0.04, 0.02, 0.96, 0.38),
        "middle": (0.04, 0.32, 0.96, 0.68),
        "bottom": (0.04, 0.62, 0.96, 0.98),
    },
}
TEXT = {"type": "string"}
MAYBE_TEXT = {"type": ["string", "null"]}


def strict(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA = strict(
    {
        "tables": {
            "type": "array",
            "items": strict(
                {
                    "party": MAYBE_TEXT,
                    "columns": {"type": "array", "items": TEXT},
                    "rows": {
                        "type": "array",
                        "items": strict({"label": TEXT, "cells": {"type": "array", "items": TEXT}}),
                    },
                }
            ),
        }
    }
)
SYSTEM = (
    "You read a crop of one scanned page of the official tables of the 21st House of Councillors regular election "
    "of 29 July 2007 (参議院議員通常選挙, 選挙期日 平成19年7月29日), published by Japan's Ministry of Internal Affairs and "
    "Communications. Each page holds ruled tables of votes by counting district (開票区) within one prefecture, in "
    "layouts that vary by prefecture, such as "
    "選挙区 候補者別得票数 (votes per prefectural-district candidate, headed by a candidate number, the name, and the "
    "party in parentheses), 開票区別投票総数 (valid, invalid, and total ballots, and voters), 比例代表 名簿届出政党等別"
    "得票数 (per party: 得票総数, 政党等の得票総数, and 名簿登載者の得票総数), or 名簿登載者の得票総数の開票区別一覧 "
    "(votes per list candidate of one party, the party named in a 政党等の名称 box above the table). Figures use "
    "commas as thousands separators and a period before three decimals, as in 1,102.000 or 4.666; keep every digit, "
    "comma, and period exactly as printed, and never confuse the two. Return one entry in tables per ruled table in "
    "the crop, top to bottom. For each table: party is the 政党等の名称 when one is printed above it, else null. "
    "columns has one string per numeric column, left to right, joining that column's header texts from top to bottom "
    "with ' / ' (for example '1 / 羽柴 秀吉 / 無所属' or '2 / 社会民主党 / 政党等の得票総数'); leave columns empty when "
    "the crop shows no header. rows has one entry per printed row, top to bottom, including subtotal rows such as "
    "市 計, 石狩支庁 計, 郡 計, and 合計: label is the row's leftmost text as printed, and cells holds every numeric "
    "column's figure left to right, with an empty string for a blank cell, so every row has as many cells as the "
    "table has numeric columns. Skip the 確定 column of asterisks, but keep the 開票率 column's text when present. "
    "Skip blank spacer rows and rows the crop cuts in half."
)


def documents() -> list[tuple[int, str]]:
    found = []
    for number in range(1, 48):
        page = f"{BASE}/sangiin21_3_14_{number}.html"
        raw.fetch(RAW, page, suffix=".html")
        for href, _ in mic.anchors(page):
            if href.startswith("pdf/"):
                found.append((number, f"{BASE}/{href}"))
    return found


def fetch() -> None:
    for _, url in documents():
        raw.fetch(RAW, url, suffix=".pdf")


def count(path: Path) -> int:
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True, check=True).stdout
    match = re.search(r"Pages:\s+(\d+)", info)
    if match is None:
        raise ValueError(path)
    return int(match[1])


def stem(url: str) -> str:
    return url.rsplit("/", 1)[1].removesuffix(".pdf")


def tiles(url: str, page: int) -> dict[str, tuple[tuple[int, int, int, int], Image.Image]]:
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "page"
        path = raw.cached(RAW, url).path
        subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(DPI), "-gray", "-png", "-singlefile", path, out],
            check=True,
        )
        image = Image.open(out.with_suffix(".png")).convert("L")
        image.load()
    found = {}
    for tile, (left, top, right, bottom) in TILES["landscape" if image.width > image.height else "portrait"].items():
        box = (int(image.width * left), int(image.height * top), int(image.width * right), int(image.height * bottom))
        crop = image.crop(box)
        crop.thumbnail((LONGEST, LONGEST))
        found[tile] = (box, crop)
    return found


def read_key(url: str, page: int, tile: str, read: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/sangiin21/{stem(url)}/{page}/{tile}/{read}"


def read(url: str, page: int, tile: str, number: int) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(url, page, tile, number)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(crop: Image.Image, read: int) -> batch.Want:
    buffer = io.BytesIO()
    crop.quantize(4).save(buffer, "PNG", optimize=True)
    price_in, price_out = batch.PRICES[MODEL]
    params = MessageCreateParamsNonStreaming(
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
                    TextBlockParam(
                        type="text",
                        text="Transcribe the tables in this crop."
                        if read == 1
                        else "Transcribe this crop, row by row.",
                    ),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    return params, (crop.width * crop.height / 750 * price_in + 5000 * price_out) / 1e6


def ocr() -> None:
    wanted: dict[str, batch.Want] = {}
    for _, url in documents():
        for page in range(1, count(raw.cached(RAW, url).path) + 1):
            for tile, (_, crop) in tiles(url, page).items():
                for number in range(1, READS + 1):
                    wanted[read_key(url, page, tile, number)] = request(crop, number)
    print(batch.ensure(SOURCE, "sangiin21", wanted)[1])
