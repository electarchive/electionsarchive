import base64
import io
import json
from typing import Any

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.jp import ndl

SOURCE = ndl.SOURCE
MODEL = ndl.MODEL
PROMPT = "t1"
READS = 2
LONGEST = 2576
CHUNK = 50
DISTRICT = {
    "1350244": range(1, 11),
    "1350245": range(12, 13),
    "1337792": range(7, 16),
}
MUNICIPAL = {
    "1453092": 16,
    "1453117": 17,
    "1453130": 18,
    "1452169": 19,
    "1452183": 20,
    "1351321": 21,
    "1350471": 22,
}
PAGES = {"right": (0.48, 0.96), "left": (0.04, 0.5)}
BANDS = ((0.1, 0.45), (0.415, 0.675), (0.645, 0.97))
HEADER = (0.11, 0.207)
DISTRICT_SYSTEM = (
    "You read a crop of one page of 衆議院議員総選挙一覧, a Japanese House of Representatives election book printed "
    "between 1912 and 1926 that covers the general elections of 1890 to 1924 district by district. The text is "
    "vertical. Across the top runs the prefecture (府縣) name, then a row of district headings, one per column, read "
    "right to left: 第一區 or a city or county name (市, 郡部, 郡), with the seat count in parentheses, for example "
    "(一人) or (五人). Below, the page is divided into horizontal bands, one per general election, labeled at the "
    "right edge (第一回, 第十二回). In each band, each district column holds, top to bottom: 選擧人 (electors), 失格 "
    "(disqualified, later books only), 棄權 (abstentions), 無效 (invalid ballots), then 得票數 (votes), 黨派 (party "
    "abbreviation such as 自, 改, 政, 憲, 國, 同, 中, 獨, 無, 官), and 候補者氏名 (candidate name) for each candidate. Elected "
    "candidates (當選者) come first in larger type; losing candidates (落選者) follow in smaller type, and a final "
    "其外 entry stands for the remaining minor candidates. Figures are kanji numerals written top to bottom, with 、 as "
    "the thousands separator and 〇 for zero, often with two digits squeezed into one character width (五六八 is 568, "
    "一、二七八 is 1,278). A bar (｜) or blank means none. A figure in parentheses is a note, not a count. When the top "
    "of the crop is a strip of headings above a thick black line, that strip is the table header copied from higher on "
    "the page; its district headings stand over the columns below the line. For every district column and every "
    "election band visible in the crop, give one record: the district heading as printed, its seat count, the "
    "election label, the electors, disqualified, abstention, and invalid figures, and every candidate with name, "
    "party abbreviation, votes, and whether listed as elected (當選者) or losing (落選者); give the 其外 entry as a "
    "candidate named 其外 with its figure if any. For a figure, give its kanji as printed and its value in Arabic "
    "digits; for a missing figure, text '' and null. Mark a record partial when the crop cuts it. Keep old kanji as "
    "printed (縣, 區, 擧). Also copy the prefecture name and any heading or footnote text you see."
)
CELL = ndl.CELL
DISTRICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "prefecture": {"type": ["string", "null"]},
        "headings": {"type": "array", "items": {"type": "string"}},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "district": {"type": ["string", "null"]},
                    "seats": {"type": ["integer", "null"]},
                    "election": {"type": ["string", "null"]},
                    "electors": CELL,
                    "disqualified": CELL,
                    "abstained": CELL,
                    "invalid": CELL,
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "party": {"type": ["string", "null"]},
                                "votes": CELL,
                                "elected": {"type": "boolean"},
                            },
                            "required": ["name", "party", "votes", "elected"],
                            "additionalProperties": False,
                        },
                    },
                    "partial": {"type": "boolean"},
                },
                "required": [
                    "district",
                    "seats",
                    "election",
                    "electors",
                    "disqualified",
                    "abstained",
                    "invalid",
                    "candidates",
                    "partial",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["prefecture", "headings", "records"],
    "additionalProperties": False,
}
MUNICIPAL_SYSTEM = ndl.READ_SYSTEM.replace(
    "a 1947–1967 Japanese House of Representatives election book",
    "a 1928–1946 Japanese House of Representatives election book (some scans are gray or inverted microfilm)",
)


def canvases(pid: str) -> list[int]:
    return list(range(1, len(ndl.manifest(pid)["sequences"][0]["canvases"]) + 1))


def district_tiles(pid: str, canvas: int) -> dict[str, tuple[tuple[int, int, int, int], Image.Image]]:
    image = Image.open(ndl.spread(pid, canvas).path).convert("L")
    width, height = image.size
    out = {}
    for page, (left, right) in PAGES.items():
        x0, x1 = int(width * left), int(width * right)
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
            out[f"{page}{band}"] = (box, crop)
    return out


def read_key(pid: str, canvas: int, tile: str, read: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{pid}/{canvas}/{tile}/{read}"


def read(pid: str, canvas: int, tile: str, read: int) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(pid, canvas, tile, read)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(crop: Image.Image, read: int, system: str, schema: dict[str, object]) -> batch.Want:
    buffer = io.BytesIO()
    crop.save(buffer, "JPEG", quality=90)
    price_in, price_out = batch.PRICES[MODEL]
    params = MessageCreateParamsNonStreaming(
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
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(buffer.getvalue()).decode(),
                        },
                    ),
                    TextBlockParam(
                        type="text",
                        text="Transcribe this table crop."
                        if read == 1
                        else "Transcribe this table crop, one district column at a time from the right.",
                    ),
                ],
            }
        ],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
    )
    return params, (crop.width * crop.height / 750 * price_in + 5000 * price_out) / 1e6


def wants(pid: str, canvas: int) -> dict[str, batch.Want]:
    if pid in DISTRICT:
        cut, system, schema = district_tiles(pid, canvas), DISTRICT_SYSTEM, DISTRICT_SCHEMA
    else:
        cut, system, schema = ndl.tiles(pid, canvas), MUNICIPAL_SYSTEM, ndl.READ_SCHEMA
    return {
        read_key(pid, canvas, tile, number): request(crop, number, system, schema)
        for tile, (_, crop) in cut.items()
        for number in range(1, READS + 1)
    }


def fetch(pids: list[str]) -> None:
    for pid in pids:
        pages = canvases(pid)
        for start in range(0, len(pages), CHUNK):
            wanted: dict[str, batch.Want] = {}
            for canvas in pages[start : start + CHUNK]:
                wanted |= wants(pid, canvas)
            print(pid, start, batch.ensure(SOURCE, f"teikoku/{pid}-{start:04d}", wanted)[1], flush=True)
