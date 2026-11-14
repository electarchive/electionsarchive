import base64
import io
import json
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.sources.jp import mic, mic_chiji
from ea.sources.jp.mic import BLOCK_OF
from ea.sources.jp.mlit import BLOCKS, PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_book"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"


@dataclass(frozen=True)
class Book:
    held: str
    index: tuple[str, str]
    book: tuple[str, str]


BOOKS = {
    44: Book(
        "2005-09-11",
        ("20071230111711", "http://www.soumu.go.jp:80/senkyo/senkyo_s/data/shugiin44/index.html"),
        ("20110520101329", "http://www.soumu.go.jp/senkyo/senkyo_s/data/shugiin44/pdf/h17sousenkyo_050911_all.pdf"),
    ),
}
ESTAT = "https://www.e-stat.go.jp/stat-search/file-download?statInfId={}&fileKind=2"


@dataclass(frozen=True)
class Scanned:
    held: str
    file: str
    sections: dict[str, tuple[int, int]]
    national: tuple[int, int]


SCANNED = {
    43: Scanned("2003-11-09", "000008255748", {"forfeit": (339, 342), "smd": (373, 408), "pr": (420, 452)}, (179, 180)),
}
MODEL = "claude-haiku-5-5"
PROMPT = "1"
READS = 2
LONGEST = 1568
DPI = 200
TILES = {
    "smd": {"top": (0.06, 0.05, 0.94, 0.52), "bottom": (0.06, 0.46, 0.94, 0.95)},
    "pr": {"left": (0.06, 0.05, 0.51, 0.95), "right": (0.49, 0.05, 0.94, 0.95)},
    "forfeit": {"left": (0.06, 0.05, 0.51, 0.95), "right": (0.49, 0.05, 0.94, 0.95)},
}
PREAMBLE = (
    "You read a crop of one page of 衆議院議員総選挙・最高裁判所裁判官国民審査結果調, the official 2003 Japanese House "
    "of Representatives election book. The table is printed horizontally in Arabic numerals with commas as thousands "
    "separators. Transcribe exactly as printed, row by row from the top, and keep every digit, comma, and decimal "
    "point of a figure. Keep names as printed, with one space between surname and given name where the print shows "
    "a gap. Skip rows the crop cuts in half. "
)
SYSTEMS = {
    "smd": PREAMBLE
    + "This is table 72, 候補者別得票数, votes by candidate in the single-member districts. Its columns are 都道府県, "
    "小選挙区 (第N区), 当落の別 (○ marks the winner), 候補者氏名 (the ballot name, with the legal name below it in "
    "parentheses when it differs), 得票数 (may carry three decimals, as in 12,345.678), 男女別, 年令, 届出政党 (所属団体), "
    "新前元別, 職業, 重複立候補 (重 when the candidate also stood on a list), and 惜敗率 (%). The prefecture and the "
    "district are printed only on the first candidate of each group: give them on that row and null on the rows "
    "below. Give the votes as the printed figure, and the 惜敗率 as printed or null when blank.",
    "pr": PREAMBLE
    + "This is table 74, 比例代表党派別当選者に関する調, one panel per party list in a proportional block. A panel "
    "starts with the block (北海道選挙区, printed above the first panel of a block only), then 政党等名, 得票数 (a figure "
    "followed by 票), 当選人数 (followed by 人), and the men and women elected, then one row per list member with "
    "名簿 (list position), 氏名, 順位 (election order, blank for members not elected), 小選挙区 (当 or 落 for members who "
    "also stood in a district, blank otherwise), and 惜敗率 (a ratio, ×, or blank). Give every panel in the crop, "
    "left to right, and every member row, including those not elected.",
    "forfeit": PREAMBLE
    + "This is table 70, 法定得票数, 供託物没収点及び選挙運動法定費用額に関する調. It has two side-by-side column groups, "
    "each with 都道府県/小選挙区別 (a prefecture name on its own row, then district numbers), 法定得票数, 供託物没収点, "
    "and 選挙運動法定費用額 (円). Give one row per district with its prefecture; repeat the prefecture on every row.",
}
TEXT = {"type": "string"}
MAYBE_TEXT = {"type": ["string", "null"]}
MAYBE_INT = {"type": ["integer", "null"]}


def strict(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMAS: dict[str, dict[str, object]] = {
    "smd": strict(
        {
            "rows": {
                "type": "array",
                "items": strict(
                    {
                        "prefecture": MAYBE_TEXT,
                        "district": MAYBE_INT,
                        "winner": {"type": "boolean"},
                        "name": TEXT,
                        "legal": MAYBE_TEXT,
                        "votes": TEXT,
                        "gender": TEXT,
                        "age": MAYBE_INT,
                        "party": TEXT,
                        "status": TEXT,
                        "dual": {"type": "boolean"},
                        "ratio": MAYBE_TEXT,
                    }
                ),
            }
        }
    ),
    "pr": strict(
        {
            "panels": {
                "type": "array",
                "items": strict(
                    {
                        "block": MAYBE_TEXT,
                        "party": TEXT,
                        "votes": TEXT,
                        "seats": MAYBE_INT,
                        "men": MAYBE_INT,
                        "women": MAYBE_INT,
                        "members": {
                            "type": "array",
                            "items": strict(
                                {
                                    "position": {"type": "integer"},
                                    "name": TEXT,
                                    "order": MAYBE_INT,
                                    "district": MAYBE_TEXT,
                                    "ratio": MAYBE_TEXT,
                                }
                            ),
                        },
                    }
                ),
            }
        }
    ),
    "forfeit": strict(
        {
            "rows": {
                "type": "array",
                "items": strict(
                    {
                        "prefecture": TEXT,
                        "district": {"type": "integer"},
                        "legal": TEXT,
                        "forfeit": TEXT,
                        "expense": TEXT,
                    }
                ),
            }
        }
    ),
}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
FIGURE = re.compile(r"^[\d,]+(?:\.\d+)?$")
DISTRICT = re.compile(r"^第(\d+)区$")
MARKS = {"当", "落"}
GLYPHS = str.maketrans({"\x08": "吉", "\x04": "隆"})
NAMES = {
    ("2005-09-11", "茨城県", 2, "ぬかが 8志郎"): "ぬかが 福志郎",
    ("2005-09-11", "高知県", 2, "谷ß 治之"): "谷﨑 治之",
    ("2005-09-11", "福岡県", 6, "はとやま 3夫"): "はとやま 邦夫",
    ("2005-09-11", "大分県", 1, "佐藤 !"): "佐藤 錬",
}
LIST_NAMES = {"江 てつま"}
LISTED = {
    ("2005-09-11", "江0 洋一郎"): "江﨑 洋一郎",
    ("2005-09-11", "とかしきなおみ"): "とかしき なおみ",
    ("2005-09-11", "仲野ひろ子"): "仲野 ひろ子",
}
LEGAL = {("2005-09-11", "福岡県", 6, "鳩山 3夫"): "鳩山 邦夫"}


def archived(stamp: str, url: str) -> str:
    return WAYBACK.format(stamp=stamp, url=url)


def fetch() -> None:
    for book in BOOKS.values():
        raw.fetch(SOURCE, archived(*book.index), suffix=".html")
        raw.fetch(SOURCE, archived(*book.book), suffix=".pdf")
    for scanned in SCANNED.values():
        raw.fetch(SOURCE, ESTAT.format(scanned.file), suffix=".pdf")


def section(scanned: Scanned, page: int) -> str:
    return next(name for name, (first, last) in scanned.sections.items() if first <= page <= last)


def tiles(scanned: Scanned, page: int) -> dict[str, tuple[tuple[int, int, int, int], Image.Image]]:
    with tempfile.TemporaryDirectory() as folder:
        stem = Path(folder) / "page"
        path = raw.cached(SOURCE, ESTAT.format(scanned.file)).path
        subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(DPI), "-gray", "-png", "-singlefile", path, stem],
            check=True,
        )
        image = Image.open(stem.with_suffix(".png"))
        image.load()
    out = {}
    for tile, (left, top, right, bottom) in TILES[section(scanned, page)].items():
        box = (int(image.width * left), int(image.height * top), int(image.width * right), int(image.height * bottom))
        crop = image.crop(box)
        crop.thumbnail((LONGEST, LONGEST))
        out[tile] = (box, crop)
    return out


def read_key(scanned: Scanned, page: int, tile: str, read: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{scanned.file}/{page}/{tile}/{read}"


def read(scanned: Scanned, page: int, tile: str, read: int) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(scanned, page, tile, read)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(kind: str, crop: Image.Image, read: int) -> batch.Want:
    buffer = io.BytesIO()
    crop.save(buffer, "PNG", optimize=True)
    price_in, price_out = batch.PRICES[MODEL]
    params = MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEMS[kind],
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
                        text="Transcribe this table crop." if read == 1 else "Transcribe this table crop, row by row.",
                    ),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMAS[kind]}},
    )
    return params, (crop.width * crop.height / 750 * price_in + 5000 * price_out) / 1e6


def ocr(election: int) -> None:
    scanned = SCANNED[election]
    wanted: dict[str, batch.Want] = {}
    for kind, (first, last) in scanned.sections.items():
        for page in range(first, last + 1):
            for tile, (_, crop) in tiles(scanned, page).items():
                for number in range(1, READS + 1):
                    wanted[read_key(scanned, page, tile, number)] = request(kind, crop, number)
    print(batch.ensure(SOURCE, f"hor{election}", wanted)[1])


@dataclass(frozen=True)
class Word:
    x: float
    right: float
    top: float
    bottom: float
    text: str


@dataclass(frozen=True)
class Crop:
    width: float
    height: float


def pages(path: Path) -> list[list[Word]]:
    html = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    out = []
    for chunk in html.split("<page ")[1:]:
        out.append(
            [
                Word(float(x0), float(x1), float(y0), float(y1), unicodedata.normalize("NFKC", text))
                for x0, y0, x1, y1, text in WORD.findall(chunk)
            ]
        )
    return out


def lines(words: list[Word], tolerance: float = 2.0) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.bottom):
        if rows and word.bottom - rows[-1][0].bottom <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w.x) for row in rows]


def heading(words: list[Word]) -> str:
    return "".join(w.text for w in sorted(words, key=lambda w: (w.top, w.x))[:6])


def prefecture_of(text: str) -> str:
    return next(native for native, _ in PREFECTURES if native == text or native[:-1] == text)


def figure(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def script(char: str) -> str:
    name = unicodedata.name(char, "")
    return "kana" if name.startswith(("HIRAGANA", "KATAKANA")) else "kanji"


def split_name(chars: list[Word]) -> str:
    chars = [w for w in chars if w.text]
    if len(chars) > 1:
        gaps = [(chars[i + 1].x - chars[i].right, i) for i in range(len(chars) - 1)]
        widest, at = max(gaps)
        others = [gap for gap, i in gaps if i != at]
        if not others or widest > max(others) + 3:
            return "".join(w.text for w in chars[: at + 1]) + " " + "".join(w.text for w in chars[at + 1 :])
    text = "".join(w.text for w in chars)
    changes = [i for i in range(1, len(text)) if script(text[i]) != script(text[i - 1])]
    if len(changes) == 1:
        return f"{text[: changes[0]]} {text[changes[0] :]}"
    return text


def smd_columns(words: list[Word]) -> list[tuple[float, list[Word]]]:
    origins = sorted(w.x for w in words if w.text == "当落")
    if len(origins) != 2:
        raise ValueError(f"expected two 当落 headers, found {origins}")
    cut = origins[1] - 3
    return [(origins[0], [w for w in words if w.x < cut]), (origins[1], [w for w in words if w.x >= cut])]


def smd(book: list[list[Word]], held: str) -> list[dict]:
    candidates: list[dict] = []
    current: tuple[str, int] | None = None
    last: dict | None = None
    for number, words in enumerate(book, 1):
        if "候補者別得票数" not in heading(words):
            continue
        for origin, column in smd_columns(words):
            for row in lines(column):
                rel = [(w.x - origin, w) for w in row]
                first = rel[0][1].text
                header = DISTRICT.match(row[1].text) if len(row) > 1 else None
                if header and rel[0][0] < 5:
                    current = (prefecture_of(first), int(header[1]))
                    forfeit = "".join(w.text for x, w in rel if x > 330 and re.match(r"^[\d,.]+$", w.text))
                    candidates.append(
                        {"held": held, "kind": "forfeit", "page": number, "value": forfeit} | key(current)
                    )
                    last = None
                    continue
                if first in MARKS and rel[0][0] < 10:
                    if current is None:
                        raise ValueError(f"page {number}: candidate before any district")
                    votes = ["".join(w.text for x, w in rel if 285 <= x <= 340 and re.match(r"^[\d,.]+$", w.text))]
                    if not FIGURE.match(votes[0]):
                        raise ValueError(f"page {number}: no vote figure on {[w.text for w in row]}")
                    chars = [w for x, w in rel if 12 <= x < 112]
                    party = "".join(w.text for x, w in rel if 140 <= x < 228)
                    last = {
                        "held": held,
                        "kind": "candidate",
                        "page": number,
                        "result": first,
                        "name": split_name(chars),
                        "gender": next((w.text for x, w in rel if 112 <= x < 125), ""),
                        "party": mic.party_name(party),
                        "status": next((w.text for x, w in rel if 228 <= x < 242), ""),
                        "value": votes[0],
                        "legal": None,
                    } | key(current)
                    last["name"] = last["name"].translate(GLYPHS)
                    last["name"] = NAMES.get((held, *current, last["name"]), last["name"])
                    candidates.append(last)
                    continue
                if last is not None and first.startswith("(") and rel[0][0] < 25:
                    chars = [w for x, w in rel if 12 <= x < 112]
                    text = "".join(w.text for w in chars).strip("()")
                    if text:
                        legal = split_name([Word(w.x, w.right, w.top, w.bottom, w.text.strip("()")) for w in chars])
                        legal = legal.translate(GLYPHS)
                        last["legal"] = LEGAL.get((held, last["prefecture"], last["district"], legal), legal)
    return candidates


def key(current: tuple[str, int]) -> dict:
    return {"prefecture": current[0], "district": current[1]}


def digits(row: list[tuple[float, Word]], low: float, high: float) -> str:
    return "".join(w.text for x, w in row if low <= x < high and re.match(r"^[\d,.]+$", w.text))


def pr(book: list[list[Word]], held: str) -> list[dict]:
    rows: list[dict] = []
    for number, words in enumerate(book, 1):
        if "比例代表党派別当選人数" not in "".join(w.text for w in words[:4]):
            continue
        block = next(w.text for w in words if w.text.endswith("選挙区")).removesuffix("選挙区")
        header = lines(words)
        origins: list[tuple[float, str]] = []
        for line in header:
            texts = [w.text for w in line]
            for i, w in enumerate(line):
                if texts[i : i + 4] == ["政", "党", "等", "名"]:
                    origins.append((w.x, line[i + 4].text))
        if not origins:
            raise ValueError(f"page {number}: no party headers")
        parties: dict[str, dict[str, Any]] = {name: {"votes": "", "seats": None, "list": []} for _, name in origins}
        for line in header:
            for origin, name in origins:
                rel = [(w.x - origin, w) for w in line if -4 <= w.x - origin < 184]
                texts = [w.text for _, w in rel]
                if texts[:3] == ["得", "票", "数"]:
                    parties[name]["votes"] = digits(rel, 40, 170)
                elif texts[:4] == ["当", "選", "人", "数"]:
                    parties[name]["seats"] = int(digits(rel, 100, 160))
                elif (
                    rel and rel[0][0] < 8 and re.match(r"^\d+$", rel[0][1].text) and any(14 <= x < 110 for x, _ in rel)
                ):
                    parties[name]["list"].append(
                        {
                            "rank": int(rel[0][1].text),
                            "name": split_name([w for x, w in rel if 14 <= x < 110]).translate(GLYPHS),
                            "order": int(digits(rel, 110, 135) or 0) or None,
                            "district": next((w.text for x, w in rel if 135 <= x < 150 and w.text in MARKS), None),
                            "ratio": "".join(w.text for x, w in rel if 150 <= x < 184) or None,
                        }
                    )
        for name, party in parties.items():
            if not FIGURE.match(party["votes"]) or party["seats"] is None:
                raise ValueError(f"page {number}: {block} {name} lacks votes or seats")
            elected = [row for row in party["list"] if row["order"]]
            if len(elected) != party["seats"]:
                raise ValueError(
                    f"page {number}: {block} {name} lists {len(elected)} elected for {party['seats']} seats"
                )
            rows.append({"held": held, "block": block, "party": name, "page": number} | party)
    return rows


SPACE = "\u0793"
MARK = "\u0664"
DUAL = "\u3280"
SEX = {"\u21b5": "男", "\u115a": "女"}
STATUS = {"\u0ce8": "前", "\u18c2": "新", "\u0c30": "元"}
OUTCOME = {"\u14b0": "当", "\u2aad": "落"}
PARTY_HEAD = "\u187d"
VOTES_HEAD = "\u14e7"
SETTLED = {
    "2003-11-09 北海道 9 141,442 name": "はとやま 由紀夫",
    "2003-11-09 岩手県 4 10,642.653 legal": "髙橋 綱記",
    "2003-11-09 宮城県 5 6,853 name": "高野 博",
    "2003-11-09 愛知県 6 97,776 name": "前田 ゆうきち",
    "2003-11-09 愛知県 7 16,255 name": "坂林 たくみ",
    "2003-11-09 愛知県 15 14,622 name": "斎藤 ひろむ",
    "2003-11-09 大阪府 11 8,599 name": "よしたけ 信昭",
    "2003-11-09 大阪府 13 33,446 name": "吉井 英勝",
    "2003-11-09 熊本県 2 9,829 name": "まえだ 正治",
    "2003-11-09 東北 日本共産党 2": "五島 平",
    "2003-11-09 東北 民主党 17": "斎藤 じゅん",
    "2003-11-09 北関東 自由民主党 4": "蓮実 進",
    "2003-11-09 北関東 自由民主党 13": "さとう 勉",
    "2003-11-09 北関東 自由民主党 35": "百武 公親",
    "2003-11-09 北関東 民主党 15": "中野 ジョー",
    "2003-11-09 南関東 自由民主党 26": "江口 かずお",
    "2003-11-09 東京都 民主党 27": "片山 光代",
    "2003-11-09 北陸信越 自由民主党 1": "萩山 教嚴",
    "2003-11-09 北陸信越 自由民主党 18": "吉田 六ざえもん",
    "2003-11-09 近畿 民主党 38": "北神 けいろう",
    "2003-11-09 近畿 自由民主党 11": "谷畑 孝",
    "2003-11-09 近畿 日本共産党 3": "吉井 英勝",
    "2003-11-09 中国 自由民主党 23": "鈴木 匡信",
    "2003-11-09 四国 自由民主党 3": "山口 しゅんいち",
    "2003-11-09 九州 民主党 11": "なら崎 きん弥",
}
CONFLICTS: list[str] = []


def glyphs(path: Path, first: int, last: int) -> dict[int, tuple[Crop, list[Word]]]:
    html = subprocess.run(
        ["pdftotext", "-f", str(first), "-l", str(last), "-bbox", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    out = {}
    for number, chunk in enumerate(html.split("<page ")[1:], first):
        width, height = (float(v) for v in re.findall(r'(?:width|height)="([\d.]+)"', chunk)[:2])
        out[number] = (
            Crop(width, height),
            [
                Word(
                    float(x0),
                    float(x1),
                    float(y0),
                    float(y1),
                    "".join(chr(ord(c) + 30) if ord(c) < 0x60 else c for c in text),
                )
                for x0, y0, x1, y1, text in WORD.findall(chunk)
            ],
        )
    return out


def within(row: list[Word], low: float, high: float) -> list[Word]:
    return [w for w in row if low <= w.x < high]


def boxed(words: list[Word], crop: Crop) -> list[float]:
    return [
        min(w.x for w in words) / crop.width,
        min(w.top for w in words) / crop.height,
        max(w.right for w in words) / crop.width,
        max(w.bottom for w in words) / crop.height,
    ]


def coded(words: list[Word]) -> str:
    seen = {(round(w.x), round(w.bottom), w.text): w for w in words}
    return re.sub(f"[ {SPACE}\u0201]", "", "".join(w.text for w in seen.values()))


def forfeit_layer(book: dict[int, tuple[Crop, list[Word]]], first: int, last: int) -> list[dict]:
    found = []
    for number in range(first, last + 1):
        crop, words = book[number]
        for row in lines([w for w in words if 200 < w.bottom < 900]):
            for column, (low, high, value_low, value_high) in enumerate(((88, 110, 205, 262), (336, 360, 455, 510))):
                district = [w for w in within(row, low, high) if w.text.isdigit()]
                value = [w for w in within(row, value_low, value_high) if FIGURE.match(w.text)]
                if district and value:
                    found.append(
                        (
                            number,
                            column,
                            row[0].bottom,
                            int(district[0].text),
                            value[0].text,
                            boxed(value, crop),
                        )
                    )
    out = []
    prefecture = -1
    for number, _, _, district, value, box in sorted(found, key=lambda f: f[:3]):
        prefecture += district == 1
        out.append(
            {"prefecture": PREFECTURES[prefecture][0], "district": district, "value": value, "page": number, "box": box}
        )
    return out


def smd_layer(book: dict[int, tuple[Crop, list[Word]]], first: int, last: int) -> list[dict]:
    out: list[dict] = []
    prefecture, district = -1, 0
    for number in range(first, last + 1):
        crop, words = book[number]
        rows = lines([w for w in words if 195 < w.bottom < 900])
        for row in rows:
            votes = [w for w in within(row, 230, 262) if FIGURE.match(w.text)]
            if not votes:
                continue
            votes += [w for w in within(row, 262, 285) if re.fullmatch(r"\.\d+", w.text)]
            numbered = [w for w in within(row, 116, 140) if w.text.isdigit()]
            if numbered:
                if int(numbered[0].text) == 1:
                    prefecture += 1
                elif int(numbered[0].text) != district + 1:
                    raise ValueError(f"page {number}: district {numbered[0].text} follows {district}")
                district = int(numbered[0].text)
            bottom = row[0].bottom
            ballot = [w for r in rows if bottom - 6 <= r[0].bottom <= bottom + 0.5 for w in within(r, 165, 175)]
            legal = [w for r in rows if bottom + 0.5 < r[0].bottom <= bottom + 6 for w in within(r, 175, 186)]
            out.append(
                {
                    "prefecture": PREFECTURES[prefecture][0],
                    "district": district,
                    "page": number,
                    "votes": "".join(w.text for w in votes),
                    "box": boxed(votes, crop),
                    "result": "当" if any(w.text == MARK for w in within(row, 140, 160)) else "落",
                    "sex": next(SEX[w.text] for w in within(row, 288, 305)),
                    "status": next(STATUS[w.text] for w in within(row, 438, 452)),
                    "dual": any(w.text == DUAL for w in within(row, 530, 545)),
                    "codes": {"name": coded(ballot), "legal": coded(legal), "party": coded(within(row, 330, 438))},
                }
            )
    return out


def pr_layer(book: dict[int, tuple[Crop, list[Word]]], first: int, last: int) -> list[dict]:
    out: list[dict] = []
    block, header = -1, None
    for number in range(first, last + 1):
        crop, words = book[number]
        rows = lines([w for w in words if 120 < w.bottom < 800])
        title = coded([w for w in words if 128 < w.bottom < 140 and w.x < 120])
        if title != header:
            block, header = block + 1, title
        for origin in sorted(w.x for w in words if w.text == PARTY_HEAD):
            panel = [within(row, origin - 4, origin + 250) for row in rows]
            panel = [row for row in panel if row]
            start = next(i for i, row in enumerate(panel) if row[0].text == PARTY_HEAD)
            votes_row, seats_row = panel[start + 1], panel[start + 2]
            if votes_row[0].text != VOTES_HEAD:
                raise ValueError(f"page {number}: no votes row at {origin}")
            votes = [w for w in within(votes_row, origin + 150, origin + 240) if re.match(r"^[\d,]+", w.text)]
            seats = [w for w in within(seats_row, origin + 200, origin + 225) if re.match(r"^\d+", w.text)]
            members = []
            for row in panel[start + 4 :]:
                rank = [w for w in within(row, origin + 5, origin + 16) if w.text.isdigit()]
                if not rank:
                    continue
                order = [w for w in within(row, origin + 160, origin + 182) if w.text.isdigit()]
                mark = [OUTCOME[w.text] for w in within(row, origin + 182, origin + 198) if w.text in OUTCOME]
                members.append(
                    {
                        "rank": int(rank[0].text),
                        "order": int(order[0].text) if order else None,
                        "district": mark[0] if mark else None,
                        "codes": coded(within(row, origin + 30, origin + 155)),
                        "bottom": row[0].bottom,
                    }
                )
            out.append(
                {
                    "block": BLOCKS[block][0],
                    "page": number,
                    "votes": votes[0].text.split()[0],
                    "box": boxed(votes, crop),
                    "seats": int(seats[0].text.split()[0]),
                    "codes": coded(within(panel[start], origin + 30, origin + 250)),
                    "members": members,
                }
            )
    return out


def national_layer(book: dict[int, tuple[Crop, list[Word]]], first: int, last: int) -> list[Decimal]:
    out = []
    for number in range(first, last + 1):
        rows = [row for row in lines(book[number][1]) if row[0].bottom < 900 and any(FIGURE.match(w.text) for w in row)]
        out += [figure(w.text) for w in rows[-1] if FIGURE.match(w.text)]
    return sorted(out)


def ocr_rows(scanned: Scanned, kind: str, number: int) -> dict[int, list[dict]]:
    first, last = scanned.sections[kind]
    field = "panels" if kind == "pr" else "rows"
    return {
        page: [row for tile in TILES[kind] for row in read(scanned, page, tile, number)[field]]
        for page in range(first, last + 1)
    }


def tally(votes: str) -> str:
    return re.sub(r"[^\d,.]", "", unicodedata.normalize("NFKC", votes))


def learned(pairs: list[tuple[str, list[str | None]]]) -> dict[str, str]:
    counts: dict[str, dict[str, int]] = {}
    for codes, (first, second) in pairs:
        if first is None or mic.plain(first) != mic.plain(second or ""):
            continue
        text = mic.plain(unicodedata.normalize("NFKC", first))
        if len(text) == len(codes):
            for code, char in zip(codes, text, strict=True):
                counts.setdefault(code, {}).setdefault(char, 0)
                counts[code][char] += 1
    return {code: max(seen, key=lambda c: seen[c]) for code, seen in counts.items()}


def settled(codes: str, reads: list[str | None], cipher: dict[str, str], where: str) -> str:
    texts = [unicodedata.normalize("NFKC", text) for text in reads if text is not None]
    decoded = mic.party_name("".join(cipher.get(code, "?") for code in codes))
    for text in texts:
        if mic.party_name(text) == decoded:
            return text
    if len(texts) == READS and len({mic.party_name(text) for text in texts}) == 1 and "?" in decoded:
        return texts[0]
    if where in SETTLED:
        return SETTLED[where]
    CONFLICTS.append(f"{where}: reads {texts} against the text layer's {decoded}")
    return texts[0] if texts else decoded


def scanned_parse(scanned: Scanned) -> tuple[list[dict], list[dict], list[dict]]:
    held = scanned.held
    path = raw.cached(SOURCE, ESTAT.format(scanned.file)).path
    first = min(first for first, _ in scanned.sections.values())
    book = glyphs(path, min(first, scanned.national[0]), max(last for _, last in scanned.sections.values()))
    forfeits = {(r["prefecture"], r["district"]): r for r in forfeit_layer(book, *scanned.sections["forfeit"])}
    people = smd_layer(book, *scanned.sections["smd"])
    panels = pr_layer(book, *scanned.sections["pr"])
    smd_reads = [ocr_rows(scanned, "smd", number) for number in range(1, READS + 1)]
    pr_reads = [ocr_rows(scanned, "pr", number) for number in range(1, READS + 1)]

    def matched(rows: list[dict], votes: str) -> dict | None:
        found = [row for row in rows if tally(row["votes"]) == votes]
        return found[0] if found else None

    for person in people:
        person["reads"] = [matched(rows[person["page"]], person["votes"]) for rows in smd_reads]
    for panel in panels:
        panel["reads"] = [matched(rows[panel["page"]], panel["votes"]) for rows in pr_reads]
    pairs: list[tuple[str, list[str | None]]] = []
    for person in people:
        for field in ("name", "legal", "party"):
            texts = [row[field] if row else None for row in person["reads"]]
            pairs.append((person["codes"][field], texts))
    for panel in panels:
        pairs.append((panel["codes"], [row["party"] if row else None for row in panel["reads"]]))
        for index, member in enumerate(panel["members"]):
            texts = [
                row["members"][index]["name"] if row and index < len(row["members"]) else None for row in panel["reads"]
            ]
            pairs.append((member["codes"], texts))
    cipher = learned(pairs)

    districts = []
    for (prefecture, district), forfeit in forfeits.items():
        group = [p for p in people if (p["prefecture"], p["district"]) == (prefecture, district)]
        total = sum(figure(p["votes"]) for p in group)
        if abs(total - figure(forfeit["value"]) * 10) > 1 or [p["result"] for p in group].count("当") != 1:
            raise ValueError(f"{held} {prefecture} {district}: {total} against forfeit {forfeit['value']}")
        for order, person in enumerate(group, 1):
            where = f"{held} {prefecture} {district} {person['votes']}"
            name, legal, party = (
                settled(
                    person["codes"][field],
                    [r[field] if r else None for r in person["reads"]],
                    cipher,
                    f"{where} {field}",
                )
                if person["codes"][field]
                else None
                for field in ("name", "legal", "party")
            )
            person.update(name=name, legal=legal, party=mic.party_name(party or ""))
            districts.append(
                {
                    "held": held,
                    "prefecture": prefecture,
                    "district": district,
                    "name": name,
                    "legal": legal,
                    "party": person["party"],
                    "status": person["status"],
                    "order": order,
                    "page": person["page"],
                    "votes": str(figure(person["votes"])),
                    "result": person["result"],
                    "box": person["box"],
                }
            )

    lists, members = [], []
    totals: dict[str, Decimal] = {}
    for panel in panels:
        where = f"{held} {panel['block']}"
        party = mic.party_name(
            settled(panel["codes"], [r["party"] if r else None for r in panel["reads"]], cipher, where)
        )
        totals[party] = totals.get(party, Decimal(0)) + figure(panel["votes"])
        lists.append(
            {
                "held": held,
                "block": panel["block"],
                "party": party,
                "page": panel["page"],
                "seats": panel["seats"],
                "votes": str(figure(panel["votes"])),
                "box": panel["box"],
            }
        )
        for index, member in enumerate(panel["members"]):
            texts = [r["members"][index]["name"] if r and index < len(r["members"]) else None for r in panel["reads"]]
            row = {
                "rank": member["rank"],
                "order": member["order"],
                "district": member["district"],
                "name": settled(member["codes"], texts, cipher, f"{where} {party} {index + 1}"),
            }
            match = dual(people, panel["block"], party, row) if row["district"] else None
            members.append(
                {
                    "held": held,
                    "block": panel["block"],
                    "party": party,
                    "rank": row["rank"],
                    "name": row["name"],
                    "prefecture": match["prefecture"] if match else None,
                    "district_number": match["district"] if match else None,
                    "elected": row["order"],
                    "district": row["district"],
                }
            )
    if CONFLICTS:
        raise ValueError("\n".join(CONFLICTS))
    if sorted([*totals.values(), sum(totals.values())]) != national_layer(book, *scanned.national):
        raise ValueError(f"{held}: block list votes {totals} do not add up to the national totals")
    return districts, lists, members


def odd(text: str) -> bool:
    return any(
        not unicodedata.name(ch, "").startswith(("CJK", "HIRAGANA", "KATAKANA")) and ch not in " 々ヶ" for ch in text
    )


def dual(people: list[dict], block: str, party: str, row: dict) -> dict:
    pool = [
        person
        for person in people
        if BLOCK_OF[person["prefecture"]] == block and person["result"] == row["district"] and person["party"] == party
    ]
    wanted = mic.plain(row["name"])
    exact = [p for p in pool if wanted in {mic.plain(p["name"]), mic.plain(p["legal"] or "")}]
    if len(exact) == 1:
        return exact[0]
    scored = sorted(
        (
            (max(len(set(wanted) & set(mic.plain(p[k] or ""))) for k in ("name", "legal")), i)
            for i, p in enumerate(pool)
        ),
        reverse=True,
    )
    surname = [p for p in pool if row["name"].split()[0] in {(p[k] or "").split(" ")[0] for k in ("name", "legal")}]
    if len(surname) == 1 and scored and scored[0][0] >= 2 and pool[scored[0][1]] not in surname:
        return surname[0]
    if len(surname) == 1 and len(scored) > 1 and scored[1][0] == scored[0][0]:
        return surname[0]
    if not scored or scored[0][0] < 2 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        raise ValueError(f"{block} {party} {row['name']}: no single district candidate matches")
    return pool[scored[0][1]]


def book_path(book: Book) -> Path:
    return raw.cached(SOURCE, archived(*book.book)).path


def parse() -> None:
    districts: list[dict] = []
    lists: list[dict] = []
    members: list[dict] = []
    for book in BOOKS.values():
        words = pages(book_path(book))
        found = smd(words, book.held)
        forfeits = {(r["prefecture"], r["district"]): figure(r["value"]) for r in found if r["kind"] == "forfeit"}
        people = [r for r in found if r["kind"] == "candidate"]
        for (prefecture, district), forfeit in forfeits.items():
            group = [r for r in people if (r["prefecture"], r["district"]) == (prefecture, district)]
            total = sum(figure(r["value"]) for r in group)
            if abs(total - forfeit * 10) > 1 or [r["result"] for r in group].count("当") != 1:
                raise ValueError(f"{book.held} {prefecture} {district}: {total} against forfeit {forfeit}")
            for order, row in enumerate(group, 1):
                districts.append(
                    {key: row[key] for key in ("held", "prefecture", "district", "name", "legal", "party", "status")}
                    | {
                        "order": order,
                        "page": row["page"],
                        "votes": str(figure(row["value"])),
                        "result": row["result"],
                        "box": None,
                    }
                )
        for party in pr(words, book.held):
            if any(
                (row["held"], row["block"], row["party"]) == (book.held, party["block"], party["party"])
                for row in lists
            ):
                raise ValueError(f"{book.held} {party['block']} {party['party']} appears twice")
            lists.append(
                {key: party[key] for key in ("held", "block", "party", "page", "seats")}
                | {"votes": str(figure(party["votes"])), "box": None}
            )
            for row in party["list"]:
                row["name"] = LISTED.get((book.held, row["name"]), row["name"])
                match = dual(people, party["block"], party["party"], row) if row["district"] else None
                members.append(
                    {
                        "held": book.held,
                        "block": party["block"],
                        "party": party["party"],
                        "rank": row["rank"],
                        "name": match["name"]
                        if match and (odd(row["name"]) or row["name"] in LIST_NAMES)
                        else row["name"],
                        "prefecture": match["prefecture"] if match else None,
                        "district_number": match["district"] if match else None,
                        "elected": row["order"],
                        "district": row["district"],
                    }
                )
    for scanned in SCANNED.values():
        found = scanned_parse(scanned)
        districts += found[0]
        lists += found[1]
        members += found[2]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(districts, infer_schema_length=None).write_parquet(PARSED / f"districts-v{VERSION}.parquet")
    pl.DataFrame(lists, infer_schema_length=None).write_parquet(PARSED / f"lists-v{VERSION}.parquet")
    pl.DataFrame(members, infer_schema_length=None).write_parquet(PARSED / f"members-v{VERSION}.parquet")


SMD_METHOD = (
    "Candidate votes by district from the ministry's 結果調 book (候補者別得票数, 小選挙区). The book gives no "
    "breakdown below the district. Each district's candidate votes add up to ten times its printed deposit threshold "
    "(供託物没収点, a tenth of the valid votes). Votes for candidates whose names other candidates share are "
    "apportioned (按分) and carry three decimals. Result marks (当, 落) are the book's."
)
PR_METHOD = (
    "Party list votes, seats, and the members elected from each list, from the ministry's 結果調 book (比例代表党派別"
    "当選人数). List positions are the parties' ranks (名簿順位); dual candidates share a rank and are ordered by "
    "their district vote as a share of the district winner's (惜敗率). The book's tables of list votes by prefecture "
    "have no text layer and are not loaded."
)


LAYER = {
    43: " The 2003 book is a PDF whose text layer uses its fonts' internal glyph codes. Figures, district numbers, and "
    "result marks are read from that layer, and each figure links to its place on the page. Names and parties come "
    "from two machine reads of the page images, checked against the text layer, and the few that disagreed were "
    "settled by reading the page."
}
PR_METHODS = {
    43: PR_METHOD.replace(
        " The book's tables of list votes by prefecture have no text layer and are not loaded.",
        " The book's table of list votes by prefecture is not loaded yet. Block list votes add up to its national "
        "totals.",
    )
    + LAYER[43]
}


def emit() -> Path:
    districts = pl.read_parquet(PARSED / f"districts-v{VERSION}.parquet")
    lists = pl.read_parquet(PARSED / f"lists-v{VERSION}.parquet")
    members = pl.read_parquet(PARSED / f"members-v{VERSION}.parquet")
    codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    english = {native: name for native, name in PREFECTURES}
    blocks = {native: (f"{number:02d}", name) for number, (native, name, _) in enumerate(BLOCKS, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's House of Representatives general elections of 2003 and 2005 from the Ministry of Internal "
            "Affairs and Communications' 結果調 books: district candidates by district and party lists by block, with "
            "declared winners, block seats, and elected list members",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-representatives")
        smd_system = cs.key(f"{mic.SOURCE}.electoral_system", "hor-smd")
        pr_system = cs.key(f"{mic.SOURCE}.electoral_system", "hor-pr")
        source = cs.source(mic.PUBLISHER, mic.TITLE)
        if {japan, house, smd_system, pr_system, source} & cs.minted:
            raise ValueError("run jp.mlit, jp.diet, and jp.mic first")

        parties: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = mic.party_key(text, held)
            if found is None:
                return None
            if found not in parties:
                entity = parties[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata":
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, found[1], "ja")
            return parties[found]

        added: set[UUID] = set()

        def person(code: str, ballot: str, legal: str | None) -> UUID:
            entity = cs.key(f"{mic.SOURCE}.person", f"{code}:{mic.plain(ballot)}")
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add("person", id=entity)
                name(entity, ballot, "ja")
                if legal and mic.plain(legal) != mic.plain(ballot):
                    name(entity, legal, "ja", "official", False)
            return entity

        def prefecture_unit(code: str) -> UUID:
            entity = cs.unit("jp.prefecture", code)
            if entity in cs.minted:
                raise ValueError("run jp.mic first")
            return entity

        values: list[tuple[UUID, UUID, UUID, Decimal, UUID | None, list[float] | None]] = []
        held_by = {number: book.held for number, book in BOOKS.items()} | {
            number: scanned.held for number, scanned in SCANNED.items()
        }
        for number, held in sorted(held_by.items()):
            day = date.fromisoformat(held)
            pages_: dict[int, UUID] = {}
            if number in BOOKS:
                for stamp, url in (BOOKS[number].index, BOOKS[number].book):
                    cs.artifact(raw.cached(SOURCE, archived(stamp, url)), source)
            else:
                cached = raw.cached(SOURCE, ESTAT.format(SCANNED[number].file))
                artifact = cs.artifact(cached, source)
                PAGES.mkdir(parents=True, exist_ok=True)
                used = {
                    int(page)
                    for frame in (districts, lists)
                    for page in frame.filter(pl.col("held") == held)["page"].to_list()
                }
                for page in sorted(used):
                    image = PAGES / f"{cached.sha256}-{page}.png"
                    if not image.exists():
                        mic_chiji.render(cached.path, page, 100).save(image)
                    width, height = Image.open(image).size
                    pages_[page] = cs.page(artifact, page)
                    cs.add(
                        "source_page",
                        id=pages_[page],
                        artifact_id=artifact,
                        page_number=page,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese general election")
            name(election, f"第{number}回衆議院議員総選挙", "ja", "native", False)
            path = f"jp/{held}-general"
            cs.add("slug", path=path, entity_id=election)

            for (prefecture, district), rows in districts.filter(pl.col("held") == held).group_by(
                "prefecture", "district", maintain_order=True
            ):
                code = codes[str(prefecture)]
                key = f"{held}:{code}:{district}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    magnitude=1,
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                name(contest, f"{english[str(prefecture)]} {district}")
                name(contest, f"{prefecture}第{district}区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(english[str(prefecture)])}-{district}", entity_id=contest)
                unit = cs.unit("jp.hor_smd", f"{held}:{code}{district:02d}")
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=cs.polity(f"JP-{code}"),
                        scheme_code="jp.hor_smd",
                        code=f"{held}:{code}{district:02d}",
                        parent_id=prefecture_unit(code),
                        area_relation="within",
                        area_id=cs.area("jp.prefecture", code),
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, f"{prefecture}第{district}区", "ja")
                    name(unit, f"{english[str(prefecture)]} {district}", "en", "english", False)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=SMD_METHOD + LAYER.get(number, ""))
                for row in rows.iter_rows(named=True):
                    option = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=row["order"],
                        ballot_label=row["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{key}:{row['order']}"),
                        person_id=person(code, row["name"], row["legal"]),
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                        is_incumbent=row["status"] == "前",
                    )
                    if row["result"] == "当":
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                    values.append(
                        (result_set, unit, option, Decimal(row["votes"]), pages_.get(row["page"]), row["box"])
                    )

            for block, (block_code, block_english) in blocks.items():
                key = f"{held}:pr:{block_code}"
                contest = cs.key(f"{SOURCE}.contest", key)
                allocated = lists.filter((pl.col("held") == held) & (pl.col("block") == block))
                size = int(allocated["seats"].sum())
                area = cs.area("jp.hor_block", block_code)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=house,
                    district_area_id=area,
                    magnitude=size,
                    electoral_system_id=pr_system,
                    default_result_kind="counted",
                )
                name(contest, f"{block_english} proportional block")
                name(contest, f"比例代表{block}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(block_english)}-block", entity_id=contest)
                block_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=block_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=block_set, kind="methodology", body_md=PR_METHODS.get(number, PR_METHOD))
                block_votes = {row["party"]: Decimal(row["votes"]) for row in allocated.iter_rows(named=True)}
                declared_seats = {row["party"]: row["seats"] for row in allocated.iter_rows(named=True)}
                engine = mic.dhondt(block_votes, size)
                if engine != declared_seats:
                    moved = ", ".join(
                        f"{label} {declared_seats[label]} and {engine[label]}"
                        for label in block_votes
                        if engine[label] != declared_seats[label]
                    )
                    cs.add(
                        "note",
                        entity_id=block_set,
                        kind="caveat",
                        body_md="The declared seats differ from a plain D'Hondt count because a list ran out of "
                        "eligible names: its candidates won their districts or fell below a tenth of their district's "
                        f"valid votes, so its seats passed to the next highest quotients. Declared, then D'Hondt "
                        f"alone: {moved}.",
                    )
                block_unit = cs.unit("jp.hor_block", block_code)
                if block_unit in cs.minted:
                    raise ValueError("run jp.mic first")
                for position, row in enumerate(allocated.sort("votes", descending=True).iter_rows(named=True), 1):
                    option = cs.key(f"{SOURCE}.option", f"{key}:{row['party']}")
                    member = party(row["party"], held)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=member,
                        ballot_position=position,
                        ballot_label=mic.party_label(row["party"]),
                    )
                    values.append(
                        (block_set, block_unit, option, Decimal(row["votes"]), pages_.get(row["page"]), row["box"])
                    )
                    if row["seats"]:
                        cs.add("outcome", result_set_id=block_set, option_id=option, kind="won", seats=row["seats"])
                    for listed in members.filter(
                        (pl.col("held") == held) & (pl.col("block") == block) & (pl.col("party") == row["party"])
                    ).iter_rows(named=True):
                        code = codes[listed["prefecture"]] if listed["prefecture"] else f"pr:{block_code}"
                        smd = (
                            districts.filter(
                                (pl.col("held") == held)
                                & (pl.col("prefecture") == listed["prefecture"])
                                & (pl.col("district") == listed["district_number"])
                                & (pl.col("result") == listed["district"])
                            ).row(0, named=True)
                            if listed["prefecture"]
                            else None
                        )
                        who = person(code, smd["name"], smd["legal"]) if smd else person(code, listed["name"], None)
                        candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{row['party']}:{mic.plain(listed['name'])}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=who,
                            option_id=option,
                            role="list_member",
                            list_position=listed["rank"],
                            ballot_name=listed["name"],
                            member_of_party_id=member,
                            is_independent=False,
                        )
                        if listed["elected"]:
                            cs.add(
                                "outcome",
                                result_set_id=block_set,
                                option_id=option,
                                candidacy_id=candidacy,
                                kind="elected",
                            )

        for result_set, place, option, amount_, page_id, box in values:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=mic.VOTES,
                count_round=0,
                flags=0,
                value=amount_,
            )
            if page_id is not None and box is not None:
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=place,
                    option_id=option,
                    channel=0,
                    measure=mic.VOTES,
                    count_round=0,
                    page_id=page_id,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                )
        return cs.write()
