import base64
import html
import io
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.jp import mic, mic_hoc
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA

SOURCE = "jp.mic_hoc"
JOB = "sangiin21"
PARSED = DATA / "parsed" / SOURCE
VERSION = "1"
RAW = mic.SOURCE
BASE = f"{mic.DATA_ROOT}/sangiin21"
MODEL = "claude-haiku-5-5"
CHECKER = "claude-sonnet-5-5"
RECHECK = "sangiin21-sonnet"
PROMPT = "1"
READS = 2
READERS = [(MODEL, 1), (MODEL, 2), (CHECKER, 1), (MODEL, 3)]
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


@dataclass(frozen=True)
class Document:
    prefecture: int
    url: str
    kind: str
    party: str


def documents() -> list[Document]:
    found = []
    for number in range(1, 48):
        page = f"{BASE}/sangiin21_3_14_{number}.html"
        raw.fetch(RAW, page, suffix=".html")
        for href, label in mic.anchors(page):
            if href.startswith("pdf/"):
                kind = "district" if label == "選挙区" else "party" if label == "政党別" else "list"
                found.append(Document(number, f"{BASE}/{href}", kind, "" if kind != "list" else label))
    return found


def fetch() -> None:
    for document in documents():
        raw.fetch(RAW, document.url, suffix=".pdf")


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


def read_key(url: str, page: int, tile: str, read: int, model: str = MODEL) -> str:
    return f"anthropic-batch:{model}/{PROMPT}/sangiin21/{stem(url)}/{page}/{tile}/{read}"


def read(url: str, page: int, tile: str, number: int, model: str = MODEL) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(url, page, tile, number, model)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(crop: Image.Image, read: int, model: str = MODEL) -> batch.Want:
    buffer = io.BytesIO()
    crop.quantize(4).save(buffer, "PNG", optimize=True)
    price_in, price_out = batch.PRICES[model]
    params = MessageCreateParamsNonStreaming(
        model=model,
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


def pages(document: Document) -> int:
    return count(raw.cached(RAW, document.url).path)


def requests() -> dict[str, batch.Want]:
    """Every request for the volume: two reads of every band of every page."""
    todo = [(document, page) for document in documents() for page in range(1, pages(document) + 1)]

    def build(item: tuple[Document, int]) -> dict[str, batch.Want]:
        document, page = item
        return {
            read_key(document.url, page, tile, number): request(crop, number)
            for tile, (_, crop) in tiles(document.url, page).items()
            for number in range(1, READS + 1)
        }

    wanted: dict[str, batch.Want] = {}
    with ThreadPoolExecutor(4) as pool:
        for part in pool.map(build, todo):
            wanted.update(part)
    return wanted


def estimate(wanted: dict[str, batch.Want]) -> tuple[int, float]:
    return len(wanted), sum(cost for _, cost in wanted.values())


def approval(wanted: dict[str, batch.Want]) -> str:
    """The line that lets the batch run, with a 50% margin over the request estimate."""
    total, dollars = estimate(wanted)
    return f"ea batch approve {SOURCE}/{JOB} {max(1.0, round(dollars * 1.5)):.0f}   # {total} requests, estimate ${dollars:.2f}"


def ocr() -> None:
    """Send the requests through the batch. It stops before sending until the approval line has run."""
    wanted = requests()
    print(approval(wanted))
    print(batch.ensure(SOURCE, JOB, wanted)[1])


def rechecks(crops: str) -> dict[str, batch.Want]:
    """One read with a stronger model of each band a list in the parsed folder names."""
    urls = {stem(document.url): document.url for document in documents()}
    wanted_tiles: dict[tuple[str, int], set[str]] = {}
    for name, page, tile in json.loads((PARSED / crops).read_text()):
        wanted_tiles.setdefault((urls[name], page), set()).add(tile)

    def build(item: tuple[tuple[str, int], set[str]]) -> dict[str, batch.Want]:
        (url, page), chosen = item
        return {
            read_key(url, page, tile, 1, CHECKER): request(crop, 1, CHECKER)
            for tile, (_, crop) in tiles(url, page).items()
            if tile in chosen
        }

    wanted: dict[str, batch.Want] = {}
    with ThreadPoolExecutor(4) as pool:
        for part in pool.map(build, wanted_tiles.items()):
            wanted.update(part)
    return wanted


def recheck(crops: str = f"recheck-v{VERSION}.json") -> None:
    """Send the stronger reads through their own batch, by default of every band whose figures the last parse
    could not reconcile. It stops before sending until the approval line has run."""
    wanted = rechecks(crops)
    total, dollars = estimate(wanted)
    print(
        f"ea batch approve {SOURCE}/{RECHECK} {max(1.0, round(dollars * 1.5)):.0f}   # {total} requests, ${dollars:.2f}"
    )
    print(batch.ensure(SOURCE, RECHECK, wanted)[1])


PARTIES = [
    "維新政党・新風",
    "社会民主党",
    "公明党",
    "9条ネット",
    "共生新党",
    "日本共産党",
    "国民新党",
    "自由民主党",
    "民主党",
    "新党日本",
    "女性党",
]
SHORT = {
    "共産党": "日本共産党",
    "共産": "日本共産党",
    "自民党": "自由民主党",
    "自民": "自由民主党",
    "社民党": "社会民主党",
    "社民": "社会民主党",
    "国民": "国民新党",
    "新風": "維新政党・新風",
    "共生": "共生新党",
    "公明": "公明党",
    "民主": "民主党",
    "9条": "9条ネット",
    "女党": "女性党",
    "女性": "女性党",
    "日本": "新党日本",
}
XLS = f"{BASE}/xls/sangiin21_{{}}.xls"
BOOK = f"{BASE}/pdf/sangiin21_3_13.pdf"
NAMES = [native for native, _ in PREFECTURES]
MARKS = re.compile(r"[\s☆★※◎○●◇◆□■*＊]+")
AGGREGATE = re.compile(r"(計|郡)(\(.*\))?$")
ROSTER = re.compile(r"^\d")
NOISE = re.compile(r"表記|率|順位|番号|名簿|候補者|得票総数|その他の|unreadable|^[.…・]+$")
TIME = re.compile(r"\d\s*[時:]\s*\d|確定|未")
CLOCK = "!clock"
STAMP = re.compile(r"\d{1,2}:\d{2}(今回)?確定$")
MISREAD = re.compile(r"(?<![市区郡])[都部]$")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')
SKIP = ("率", "時刻", "確定", "残", "不受理", "持", "その他", "番号", "法定", "没収")
SMD_KEYS = (
    ("投票者", "s:voters"),
    ("無効", "s:invalid"),
    ("投票総数", "s:ballots"),
    ("有効", "s:valid"),
    ("按分", "s:frac"),
    ("あん分", "s:frac"),
    ("端数", "s:frac"),
    ("属", "s:none"),
    ("得票", "s:sum"),
    ("合計", "s:sum"),
    ("小計", "s:sum"),
)
PR_BALLOTS = (
    ("投票者", "r:voters"),
    ("無効", "r:invalid"),
    ("投票総数", "r:ballots"),
    ("有効", "r:valid"),
    ("按分", "r:frac"),
    ("端数", "r:frac"),
    ("属さない", "r:none"),
)
REPAIRS = 4
TRIES = 3
MAX_OVERLAP = 40


def norm(text: str) -> str:
    return MARKS.sub("", unicodedata.normalize("NFKC", text)).replace("ケ", "ヶ")


def place_label(text: str) -> str:
    label = STAMP.sub("", norm(text)).rstrip("・")
    return "" if NOISE.search(label) else MISREAD.sub("郡", label) if len(label) >= 3 else label


def flat(text: str) -> str:
    return re.sub(r"[\s・]", "", unicodedata.normalize("NFKC", text))


def figure(text: str) -> Decimal | None:
    """A printed cell as a number, or None for a blank or unreadable cell; a dash is 0."""
    text = re.sub(r"\(\s*\d+\.\d+\s*\)$", "", unicodedata.normalize("NFKC", text)).replace(",", "").replace(" ", "")
    if text in {"-", "－", "―", "ー", "|"}:
        return Decimal(0)
    if not re.fullmatch(r"\d+(?:\.\d{1,3})?", text):
        return None
    return Decimal(text)


def ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right, autojunk=False).ratio() if left and right else 0.0


@dataclass(frozen=True)
class Candidate:
    order: int
    name: str
    party: str
    votes: Decimal
    elected: bool


def prefecture_of(label: str) -> int | None:
    label = re.sub(r"\s", "", label)
    return NAMES.index(label) + 1 if label in NAMES else None


def book() -> dict[int, list[Candidate]]:
    """The prefectural district candidates in ballot order, from the 結果調 book's two-column pages."""
    path = raw.cached(RAW, BOOK).path
    text = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, text=True, check=True).stdout
    found: dict[int, list[Candidate]] = {}
    prefecture: int | None = None
    for page in text.split("<page")[1:]:
        words = [
            (float(left), float(top), float(right), unicodedata.normalize("NFKC", html.unescape(word)))
            for left, top, right, word in WORD.findall(page)
        ]
        for half in (0, 1):
            lines: dict[int, list[tuple[float, float, float, str]]] = {}
            for left, top, right, word in words:
                if (left >= 412) == bool(half):
                    lines.setdefault(round(top), []).append((left - 373 * half, top, right - 373 * half, word))
            for _, line in sorted(lines.items()):
                line.sort()
                heads = [word for left, _, _, word in line if left < 60 and prefecture_of(word)]
                if heads:
                    prefecture = prefecture_of(heads[0])
                    found[prefecture or 0] = []
                    continue
                if prefecture is None or line[0][3] not in ("当", "落") or line[0][0] > 55:
                    continue
                name = [word for word in line if 55 <= word[0] < 160]
                if not name:
                    continue
                gaps = [(name[i + 1][0] - name[i][2], i) for i in range(len(name) - 1)]
                cut = max(gaps)[1] + 1 if gaps and max(gaps)[0] > 9 else len(name)
                display = " ".join(
                    part for part in ("".join(w[3] for w in name[:cut]), "".join(w[3] for w in name[cut:])) if part
                )
                party = "".join(word for left, _, _, word in line if 190 <= left < 280)
                votes = "".join(word for left, _, _, word in line if 340 <= left < 412)
                found[prefecture].append(
                    Candidate(len(found[prefecture]) + 1, display, party, mic.amount(votes), line[0][3] == "当")
                )
    return found


def sheet(name: str, last: bool = False) -> list[list[str]]:
    path = raw.cached(RAW, XLS.format(name)).path
    names = mic.sheets(path)
    return [row for title in (names[-1:] if last else names) for row in mic.grid(path, title)]


def party_name(text: str) -> str | None:
    text = flat(text)
    for name in sorted(PARTIES, key=len, reverse=True):
        if flat(name) in text:
            return name
    for token in re.split(r"[/:()]", text):
        if token in SHORT:
            return SHORT[token]
        best = max(PARTIES, key=lambda name: ratio(token, flat(name)))
        if len(token) >= 3 and ratio(token, flat(best)) >= 0.75:
            return best
    return None


@dataclass
class Official:
    """What the ministry's tables print for each prefecture: the district candidates and every column's total."""

    smd: dict[int, list[Candidate]]
    lists: dict[str, list[str]]
    totals: dict[int, dict[str, Decimal]]


def official() -> Official:
    smd = book()
    totals: dict[int, dict[str, Decimal]] = {number: {} for number in range(1, 48)}
    for number, candidates in smd.items():
        for candidate in candidates:
            totals[number][f"s{candidate.order}"] = candidate.votes
    for name, prefix in (("3_8", "s"), ("3_7", "r")):
        for row in sheet(name):
            if (number := prefecture_of(row[0] or row[1])) is not None:
                cells = [cell for cell in row if cell]
                for part, cell in zip(("ballots", "valid", "invalid"), cells[1:4], strict=True):
                    totals[number][f"{prefix}:{part}"] = mic.amount(cell)
    starts: list[tuple[int, str | None]] = []
    for row in sheet("3_5"):
        if not row[0] and any(cell and party_name(cell) for cell in row):
            starts = [(i, party_name(cell)) for i, cell in enumerate(row) if cell]
        if (number := prefecture_of(row[0])) is None:
            continue
        for i, party in starts:
            for part, offset in (("t", 0), ("p", 2), ("l", 3)):
                totals[number][f"{part}:{party}"] = mic.amount(row[i + offset])
    for found in totals.values():
        for part in ("t", "p", "l"):
            found[f"{part}:all"] = sum((found[f"{part}:{party}"] for party in PARTIES), Decimal(0))
    lists: dict[str, list[str]] = {}
    for index in range(1, 12):
        rows = sheet(f"3_11_{index}", last=True)
        party = party_name(next(cell for row in rows for cell in row if cell.startswith("政党等の名称")))
        if party is None:
            raise ValueError(f"3_11_{index}: no party")
        people = lists.setdefault(party, [])
        columns: dict[int, str] = {}
        for row in rows:
            if row[0].startswith("名簿登載者名"):
                columns = {i: mic.person_name(cell) for i, cell in enumerate(row) if i and cell}
                people += [name for name in columns.values() if name not in people]
            elif (number := prefecture_of(row[0])) is not None:
                for i, name in columns.items():
                    totals[number][f"c:{party}:{name}"] = mic.amount(row[i])
    return Official(smd, lists, totals)


def smd_key(text: str, candidates: list[Candidate]) -> str | None:
    plain = flat(text)
    if not plain or any(word in plain for word in SKIP):
        return None
    match = re.match(r"^\(?0*(\d{1,2})\)?[/:.]?", plain)
    rest = plain[match.end() :] if match else plain
    rest = re.sub(r"\(.*?\)|（.*?）", "", rest)
    for party in sorted({c.party for c in candidates} | set(PARTIES), key=len, reverse=True):
        rest = rest.replace(flat(party), "")
    rest = rest.replace("/", "")
    scores = sorted(((ratio(rest, flat(c.name)), c.order) for c in candidates), reverse=True)
    if match and 1 <= int(match[1]) <= len(candidates):
        number = int(match[1])
        named = ratio(rest, flat(candidates[number - 1].name))
        if scores and scores[0][0] >= 0.75 and scores[0][1] != number and named < 0.5:
            return f"s{scores[0][1]}"
        if not rest or named >= 0.3 or not scores or scores[0][0] < 0.6:
            return f"s{number}"
    if scores and scores[0][0] >= 0.6 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.15):
        return f"s{scores[0][1]}"
    for word, key in SMD_KEYS:
        if word in plain:
            return key
    return None


def part_of(text: str) -> str | None:
    if any(word in text for word in ("率", "時刻", "確定", "残")):
        return None
    if "政党等" in text or "政党得票" in text or "政党名" in text or re.search(r"(^|/)政党(/|$)", text):
        return "p"
    if "名簿登載者" in text or "候補者" in text:
        return "l"
    if "得票" in text or "合計" in text or text.endswith("計"):
        return "t"
    return None


def party_keys(header: list[str]) -> list[str | None]:
    """Keys for a party table's columns: each party's total, party-name votes, and list votes, or the ballots."""
    cells = [flat(text) for text in header]
    found: list[tuple[str | None, str | None]] = []
    for text in cells:
        ballot = next((key for word, key in PR_BALLOTS if word in text), None)
        if ballot is not None and party_name(text) is None and "名簿" not in text and "政党" not in text:
            found.append(("ballot", ballot))
            continue
        number = re.match(r"^0*(\d{1,2})[/:]", text)
        party = "all" if "全党派" in text or text.startswith("合計/") else party_name(text)
        if party is None and number and 1 <= int(number[1]) <= len(PARTIES):
            party = PARTIES[int(number[1]) - 1]
        part = part_of(text)
        if party is not None and part is None and "率" not in text:
            part = "t"
        found.append((party, part))
    order = {"t": 0, "p": 1, "l": 2}
    named = [party for party, _ in found if party not in (None, "ballot")]
    for i, (party, part) in enumerate(found):
        if party is not None or part is None:
            continue
        before = next(((p, q) for p, q in reversed(found[:i]) if q is not None), None)
        after = next(((p, q) for p, q in found[i + 1 :] if q is not None), None)
        if before and before[0] not in (None, "ballot") and order.get(before[1] or "", 9) < order[part]:
            found[i] = (before[0], part)
        elif after and after[0] not in (None, "ballot") and order.get(after[1] or "", -1) > order[part]:
            found[i] = (after[0], part)
        elif not named:
            found[i] = ("all", part)
    return [
        part if party == "ballot" else f"{part}:{party}" if party is not None and part is not None else None
        for party, part in found
    ]


def candidate_text(text: str) -> tuple[int | None, str]:
    plain = flat(text)
    number = re.match(r"^\(?0*(\d{1,2})\)?[/:.]?", plain)
    rest = plain[number.end() :] if number else plain
    for name in sorted({*PARTIES, *SHORT}, key=len, reverse=True):
        rest = rest.replace(flat(name), "")
    return (int(number[1]) if number else None), rest.replace("/", "")


def named(text: str, people: list[str], least: float) -> str | None:
    scores = sorted(((ratio(text, flat(name)), name) for name in people), reverse=True)
    if scores and scores[0][0] >= least and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.1):
        return scores[0][1]
    return None


def list_key(text: str, party: str, people: list[str]) -> str | None:
    """A list table column: a candidate by list number (or by name when no number is printed), or a party total."""
    plain = flat(text)
    if not plain or any(word in plain for word in ("率", "時刻", "確定")):
        return None
    number, rest = candidate_text(text)
    if number is None or rest.startswith(("計", "合計")):
        part = part_of(plain)
        if part == "t" and ("計" in plain and "合計得票" not in plain and "得票総数" not in plain):
            part = "l"
        if part is not None:
            return f"{part}:{party_name(plain) or party}"
    name = named(rest, people, 0.75 if number is not None else 0.6)
    if name is not None:
        return f"c:{party}:{name}"
    return f"#:{party}:{number}" if number is not None else None


@dataclass
class Fragment:
    document: Document
    page: int
    tile: str
    read: int
    keys: list[str | None]
    labels: list[str]
    cells: list[list[str]]


def header_keys(document: Document, header: list[str], facts: Official) -> list[str | None]:
    if document.kind == "district":
        keys = [smd_key(text, facts.smd[document.prefecture]) for text in header]
    elif document.kind == "party":
        keys = party_keys(header)
    else:
        party = party_name(document.party) or document.party
        keys = [list_key(text, party, facts.lists.get(party, [])) for text in header]
    return [CLOCK if re.search(r"時刻|確定", text) else key for text, key in zip(header, keys, strict=True)]


def real(header: list[str], keys: list[str | None]) -> bool:
    printed = [key for text, key in zip(header, keys, strict=True) if flat(text) and not flat(text).isdigit()]
    return bool(printed) and sum(key is not None for key in printed) * 2 >= len(printed)


def renumbered(found: list[Fragment], seen: dict[str, list[tuple[int, str]]]) -> None:
    """Name the list columns printed with a number only. Most prefectures number each party's candidates from 1,
    some number all lists in one run, so each document's offset comes from the columns whose names do match."""
    numbers: dict[str, Counter[int]] = {}
    for pairs in seen.values():
        for at, key in pairs:
            numbers.setdefault(key, Counter())[at] += 1
    position = {key: counts.most_common(1)[0][0] for key, counts in numbers.items()}
    by_number = {(key.split(":")[1], at): key for key, at in position.items()}
    shift = {
        name: Counter(at - position[key] for at, key in pairs).most_common(1)[0][0] if pairs else 0
        for name, pairs in seen.items()
    }
    for fragment in found:
        offset = shift.get(stem(fragment.document.url), 0)
        fragment.keys = [
            by_number.get((key.split(":")[1], int(key.split(":")[2]) - offset))
            if key is not None and key.startswith("#:")
            else key
            for key in fragment.keys
        ]


def fragments(held: set[str], facts: Official) -> list[Fragment]:
    """Every table of every band read, with the column keys of the header its page prints."""
    out: list[Fragment] = []
    seen: dict[str, list[tuple[int, str]]] = {}
    for document in documents():
        previous: dict[int, list[str | None]] = {}
        for page in range(1, pages(document) + 1):
            found: dict[int, list[tuple[str, list[str | None] | None, dict[str, Any]]]] = {}
            for number, (model, attempt) in enumerate(READERS, 1):
                for tile in ("top", "middle", "bottom"):
                    if read_key(document.url, page, tile, attempt, model) not in held:
                        continue
                    try:
                        tables = read(document.url, page, tile, attempt, model)["tables"]
                    except json.JSONDecodeError:
                        continue
                    for table in tables:
                        header = [str(text) for text in table["columns"]]
                        keys = header_keys(document, header, facts)
                        for text, key in zip(header, keys, strict=True):
                            at, _ = candidate_text(text)
                            if document.kind == "list" and at is not None and key is not None and key.startswith("c:"):
                                seen.setdefault(stem(document.url), []).append((at, key))
                        found.setdefault(number, []).append((tile, keys if real(header, keys) else None, table))
            first = {n: next((keys for _, keys, _ in tables if keys), None) for n, tables in found.items()}
            for number, tables in found.items():
                current = first[number] or next((keys for keys in first.values() if keys), None)
                current = current or previous.get(number)
                for tile, keys, table in tables:
                    current = keys or current
                    rows = [
                        (place_label(str(row["label"])), [str(cell) for cell in row["cells"]]) for row in table["rows"]
                    ]
                    rows = [(label, cells) for label, cells in rows if label]
                    if (
                        current is None
                        or not rows
                        or 2 * sum(bool(ROSTER.match(label)) for label, _ in rows) > len(rows)
                    ):
                        continue
                    out.append(
                        Fragment(
                            document,
                            page,
                            tile,
                            number,
                            current,
                            [label for label, _ in rows],
                            [cells for _, cells in rows],
                        )
                    )
                if current is not None:
                    previous[number] = current
    renumbered(out, seen)
    return out


def place(labels: list[str], canon: list[str]) -> list[int | None]:
    """Where each row of a band falls among the prefecture's rows: by printed order, or by a label printed once
    when a document orders its rows differently."""
    at: list[int | None] = [None] * len(labels)
    for tag, i0, i1, j0, j1 in SequenceMatcher(None, labels, canon, autojunk=False).get_opcodes():
        if tag == "equal":
            for offset in range(i1 - i0):
                at[i0 + offset] = j0 + offset
        elif tag == "replace" and (i1 - i0 == j1 - j0 or i0 == 0 or i1 == len(labels)):
            size = min(i1 - i0, j1 - j0)
            pairs = (
                [(i1 - k, j1 - k) for k in range(1, size + 1)]
                if i0 == 0 and i1 - i0 != j1 - j0
                else [(i0 + k, j0 + k) for k in range(size)]
            )
            for i, j in pairs:
                if ratio(labels[i], canon[j]) >= 0.34:
                    at[i] = j
        elif tag == "replace":
            j = j0
            for i in range(i0, i1):
                best = max(range(j, j1), key=lambda k: ratio(labels[i], canon[k]), default=None)
                if best is not None and ratio(labels[i], canon[best]) >= 0.5:
                    at[i] = best
                    j = best + 1
    once = {label: j for j, label in enumerate(canon) if canon.count(label) == 1}
    taken = {j for j in at if j is not None}
    for i, label in enumerate(labels):
        if at[i] is None and once.get(label) is not None and once[label] not in taken:
            at[i] = once[label]
            taken.add(once[label])
    return at


def canonical(sequences: list[list[str]]) -> list[str]:
    """The prefecture's rows in printed order, assembled from overlapping bands by majority."""
    order = sorted(
        (labels for labels in sequences if len(labels) >= 2),
        key=lambda labels: (len(set(labels)) - len(labels), len(labels)),
        reverse=True,
    )
    if not order:
        return []
    canon = list(order[0])
    for _ in range(60):
        heads: Counter[tuple[str, ...]] = Counter()
        tails: Counter[tuple[str, ...]] = Counter()
        inserts: Counter[tuple[int, tuple[str, ...]]] = Counter()
        cover = [0] * (len(canon) + 1)
        for labels in order:
            ops = SequenceMatcher(None, labels, canon, autojunk=False).get_opcodes()
            equal = [op for op in ops if op[0] == "equal"]
            if not equal or sum(op[2] - op[1] for op in equal) < min(3, len(labels)):
                continue
            lo, hi = equal[0][3] - equal[0][1], equal[-1][4] + (len(labels) - equal[-1][2])
            for j in range(max(lo, 0) + 1, min(hi, len(canon))):
                cover[j] += 1
            for tag, i0, i1, j0, j1 in ops:
                if tag != "delete":
                    continue
                if j0 == 0 and i0 == 0:
                    heads[tuple(labels[i0:i1])] += 1
                elif j0 == len(canon) and i1 == len(labels):
                    tails[tuple(labels[i0:i1])] += 1
                elif 0 < j0 < len(canon):
                    inserts[j0, tuple(labels[i0:i1])] += 1
        grown = False
        known = set(canon)
        added = sorted(
            (
                (j, rows)
                for (j, rows), count in inserts.items()
                if count >= max(2, cover[j] * 0.3) and not known.issuperset(rows)
            ),
            reverse=True,
        )
        taken: set[int] = set()
        for j, rows in added:
            if j not in taken:
                canon[j:j] = list(rows)
                taken.add(j)
                grown = True
        for counter, front in ((heads, True), (tails, False)):
            counter = Counter({rows: count for rows, count in counter.items() if not known.issuperset(rows)})
            if not counter:
                continue
            longest = max(len(rows) for rows in counter)
            for size in range(longest, 0, -1):
                votes = Counter(rows[-size:] if front else rows[:size] for rows in counter if len(rows) >= size)
                rows, count = votes.most_common(1)[0]
                if count >= 2 or (count >= 1 and len(counter) == 1):
                    canon = [*rows, *canon] if front else [*canon, *rows]
                    grown = True
                    break
        if not grown:
            break
    votes = [Counter[str]() for _ in canon]
    for labels in order:
        for label, j in zip(labels, place(labels, canon), strict=True):
            if j is not None:
                votes[j][label] += 1
    return [vote.most_common(1)[0][0] if vote else label for vote, label in zip(votes, canon, strict=True)]


Source = tuple[str, int, str, int]
Cell = tuple[str, int]


@dataclass
class Prefecture:
    number: int
    rows: list[str]
    observed: dict[Cell, list[tuple[Decimal | None, Source]]]
    dropped: int = 0
    alternates: dict[Cell, set[Decimal]] = field(default_factory=dict)
    leaves: list[int] = field(default_factory=list)
    members: dict[int, list[int]] = field(default_factory=dict)
    values: dict[Cell, Decimal | None] = field(default_factory=dict)
    how: dict[Cell, str] = field(default_factory=dict)
    review: list[dict[str, Any]] = field(default_factory=list)


def alike(left: list[str | None], right: list[str | None]) -> bool:
    return len(left) == len(right) and 2 * sum(
        a == b and a is not None for a, b in zip(left, right, strict=True)
    ) >= len(left)


def joined(head: list[str], tail: list[str]) -> list[str]:
    """One band's rows after the band above, without the rows both show."""
    for size in range(min(len(head), len(tail), MAX_OVERLAP), 0, -1):
        same = sum(ratio(a, b) >= 0.6 for a, b in zip(head[-size:], tail[:size], strict=True))
        if same >= 0.7 * size and (size >= 2 or head[-1] == tail[0]):
            return head + tail[size:]
    return head + tail


def repeats(labels: list[str], added: list[str]) -> bool:
    seen = set(labels)
    return 2 * sum(label in seen for label in added) > len(added)


def sequences(found: list[Fragment]) -> list[list[str]]:
    """The rows each read of each document shows for one header, page after page."""
    groups: dict[tuple[str, int], list[tuple[list[str | None], list[str]]]] = {}
    for fragment in found:
        sections = groups.setdefault((stem(fragment.document.url), fragment.read), [])
        match = next(
            (
                labels
                for keys, labels in sections
                if alike(keys, fragment.keys) and not repeats(labels, joined(labels, fragment.labels)[len(labels) :])
            ),
            None,
        )
        if match is None:
            sections.append((fragment.keys, list(fragment.labels)))
        else:
            match[:] = joined(match, fragment.labels)
    return [labels for sections in groups.values() for _, labels in sections]


def fitted(keys: list[str | None], cells: list[str]) -> list[tuple[str, str]]:
    """A row's cells by key when a band read without its header gives the row more or fewer cells: blank cells the
    header has no column for are dropped, and unkeyed columns at either end may be missing."""
    if any(TIME.search(cell) for cell in cells):
        cells = [cell for cell in cells if not TIME.search(cell)]
        keys = [key for key in keys if key != CLOCK]
    width = len(keys)
    if len(cells) > width:
        cells = [cell for cell in cells if cell.strip()]
    gap = width - len(cells)
    if gap > 0 and not any(keys[len(cells) :]):
        keys = keys[: len(cells)]
    elif gap > 0 and not any(keys[:gap]):
        keys = keys[gap:]
    if len(keys) != len(cells):
        keyed = [key for key in keys if key is not None and key != CLOCK]
        filled = [cell for cell in cells if cell.strip()]
        if len(keyed) == len(filled):
            return list(zip(keyed, filled, strict=True))
        if 0 < len(keys) - len(cells) <= 2:
            keys = keys[: len(cells)]
        else:
            return []
    return [(key, cell) for key, cell in zip(keys, cells, strict=True) if key is not None and key != CLOCK]


def observe(number: int, found: list[Fragment]) -> Prefecture:
    rows = canonical(sequences(found))
    out = Prefecture(number, rows, {})
    for fragment in found:
        source = (stem(fragment.document.url), fragment.page, fragment.tile, fragment.read)
        for j, cells in zip(place(fragment.labels, rows), fragment.cells, strict=True):
            pairs = fitted(fragment.keys, cells) if j is not None else []
            if j is None or not pairs:
                out.dropped += 1
                continue
            for key, cell in pairs:
                out.observed.setdefault((key, j), []).append((figure(cell), source))
                comma = re.fullmatch(r"(\d{1,3}(?:,\d{3})*),(\d{3})", re.sub(r"\s|\(.*\)$", "", cell))
                if comma:
                    out.alternates.setdefault((key, j), set()).add(Decimal(f"{comma[1].replace(',', '')}.{comma[2]}"))
    return out


def tally(seen: list[tuple[Decimal | None, Source]]) -> list[tuple[Decimal | None, int]]:
    ranked: list[tuple[Decimal | None, int]] = Counter(value for value, _ in seen).most_common()
    numeric: list[tuple[Decimal | None, int]] = [(value, count) for value, count in ranked if value is not None]
    return numeric or ranked


def structure(prefecture: Prefecture) -> None:
    """Which rows are counting districts and which add others up, judged by the labels and by the sums."""
    keys = sorted({key for key, _ in prefecture.observed})
    best = {cell: tally(seen)[0][0] for cell, seen in prefecture.observed.items()}
    size = len(prefecture.rows)
    aggregate = {r for r, label in enumerate(prefecture.rows) if AGGREGATE.search(label)}
    busy = sorted(keys, key=lambda key: -sum((key, r) in best for r in range(size)))[:60]

    def windows(r: int, leaves: list[int], longest: int) -> list[list[int]]:
        before = [leaf for leaf in leaves if leaf < r]
        after = [leaf for leaf in leaves if leaf > r]
        found = [before[-k:] for k in range(2, min(len(before), longest) + 1)]
        found += [after[:k] for k in range(2, min(len(after), longest) + 1)]
        return found

    def score(r: int, window: list[int]) -> tuple[int, int]:
        hit = tried = 0
        for key in busy:
            total = best.get((key, r))
            if not total:
                continue
            parts = [part for leaf in window if (part := best.get((key, leaf))) is not None]
            if len(parts) < len(window):
                continue
            tried += 1
            hit += sum(parts, Decimal(0)) == total
        return hit, tried

    for _ in range(5):
        leaves = [r for r in range(size) if r not in aggregate]
        moved = False
        for r in leaves:
            others = [leaf for leaf in leaves if leaf != r and leaf not in aggregate]
            for window in windows(r, others, 30):
                hit, tried = score(r, window)
                if tried >= 3 and hit >= 0.8 * tried:
                    aggregate.add(r)
                    moved = True
                    break
        if not moved:
            break
    prefecture.leaves = [r for r in range(size) if r not in aggregate]
    for r in sorted(aggregate):
        options = [*windows(r, prefecture.leaves, size), prefecture.leaves]
        scored = sorted(((score(r, window), -len(window), window) for window in options), reverse=True)
        if scored and scored[0][0][1] and scored[0][0][0] >= 0.6 * scored[0][0][1]:
            prefecture.members[r] = scored[0][2]
        else:
            prefecture.review.append({"row": prefecture.rows[r], "check": "subtotal without members"})


Equation = tuple[list[tuple[Cell, int]], Decimal, str]


def equations(prefecture: Prefecture, facts: Official) -> list[Equation]:
    have = {key for key, _ in prefecture.observed}
    anchors = facts.totals[prefecture.number]
    out: list[Equation] = []
    for key in sorted(have):
        if key in anchors:
            out.append(([((key, leaf), 1) for leaf in prefecture.leaves], anchors[key], f"total {key}"))
        for r, members in prefecture.members.items():
            if (key, r) in prefecture.observed:
                terms = [((key, r), 1)] + [((key, leaf), -1) for leaf in members]
                out.append((terms, Decimal(0), f"subtotal {key} {prefecture.rows[r]}"))
    rules: list[tuple[str, list[str], str]] = []
    for party in PARTIES:
        rules.append((f"t:{party}", [f"p:{party}", f"l:{party}"], f"party {party}"))
        rules.append((f"l:{party}", [f"c:{party}:{name}" for name in facts.lists.get(party, [])], f"list {party}"))
    candidates = [f"s{c.order}" for c in facts.smd.get(prefecture.number, [])]
    rules.append(("s:sum", candidates, "candidates"))
    rules.append(("s:valid", ["s:sum", *(key for key in ("s:frac", "s:none") if key in have)], "valid"))
    rules.append(("s:ballots", ["s:valid", "s:invalid"], "ballots"))
    rules.append(("r:ballots", ["r:valid", "r:invalid"], "pr ballots"))
    for total, parts, name in rules:
        if total not in have or not all(part in have for part in parts):
            continue
        for r in range(len(prefecture.rows)):
            out.append(([((total, r), 1)] + [((part, r), -1) for part in parts], Decimal(0), f"row {name}"))
    return out


def solve(prefecture: Prefecture, facts: Official) -> None:
    """Pick each cell's value: the reading most reads agree on, overruled where printed totals and row identities
    settle a disagreement; a cell no read can give is solved from an identity it alone leaves open."""
    options = {cell: tally(seen) for cell, seen in prefecture.observed.items()}
    for cell, alternates in prefecture.alternates.items():
        options[cell] += [(value, 0) for value in sorted(alternates) if value not in dict(options[cell])]
    values: dict[Cell, Decimal | None] = {cell: ranked[0][0] for cell, ranked in options.items()}
    how = {cell: "agreed" if len(ranked) == 1 else "voted" for cell, ranked in options.items()}
    system = equations(prefecture, facts)
    involved: dict[Cell, list[int]] = {}
    for index, (terms, _, _) in enumerate(system):
        for cell, _ in terms:
            involved.setdefault(cell, []).append(index)

    def residual(index: int) -> Decimal | None:
        terms, constant, _ = system[index]
        total = Decimal(0)
        for cell, sign in terms:
            value = values.get(cell)
            if value is None:
                return None
            total += sign * value
        return constant - total

    def broken(cell: Cell) -> int:
        return sum(1 for index in involved.get(cell, []) if residual(index) not in (None, Decimal(0)))

    for _ in range(TRIES):
        moved = False
        for cell, ranked in options.items():
            if len(ranked) < 2:
                continue
            scored = []
            for value, count in ranked:
                values[cell] = value
                scored.append((broken(cell), -count, value))
            scored.sort(key=lambda item: item[:2])
            if values[cell] != scored[0][2] or scored[0][2] != ranked[0][0]:
                moved = moved or scored[0][2] != ranked[0][0]
            values[cell] = scored[0][2]
            how[cell] = "settled" if scored[0][2] != ranked[0][0] else how[cell]
        if not moved:
            break
    groups: dict[tuple[int, str], list[Cell]] = {}
    for cell in prefecture.alternates:
        key, r = cell
        groups.setdefault((r, ""), []).append(cell)
        groups.setdefault((r, key.split(":")[1] if key[:2] in ("t:", "p:", "l:", "c:") else key), []).append(cell)
    for cells in groups.values():
        before = {cell: values.get(cell) for cell in cells}
        after = {cell: max(prefecture.alternates[cell]) for cell in cells}
        if any(before[cell] is None or before[cell] in prefecture.alternates[cell] for cell in cells):
            continue
        indexes = {index for cell in cells for index in involved.get(cell, [])}
        old = sum(residual(index) not in (None, Decimal(0)) for index in indexes)
        values.update(after)
        if sum(residual(index) not in (None, Decimal(0)) for index in indexes) < old:
            how.update(dict.fromkeys(cells, "decimal"))
        else:
            values.update(before)
    for _ in range(REPAIRS):
        moved = False
        for index, (terms, _, _) in enumerate(system):
            unknown = [(cell, sign) for cell, sign in terms if values.get(cell) is None]
            if len(unknown) != 1:
                continue
            cell, sign = unknown[0]
            values[cell] = Decimal(0)
            left = residual(index)
            if left is None or sign * left < 0:
                values[cell] = None
                continue
            values[cell] = sign * left
            how[cell] = "solved"
            moved = True
        for cell, indexes in involved.items():
            if values.get(cell) is None or len(indexes) < 2:
                continue
            gaps = [gap for index in indexes if (gap := residual(index))]
            if len(gaps) < len(indexes):
                continue
            shifts = {gap * dict(system[index][0])[cell] for gap, index in zip(gaps, indexes, strict=True)}
            if len(shifts) != 1:
                continue
            shift = shifts.pop()
            value = values[cell]
            if value is None or value + shift < 0:
                continue
            values[cell] = value + shift
            how[cell] = "repaired"
            moved = True
        if not moved:
            break
    prefecture.values = values
    prefecture.how = how
    for index, (terms, constant, name) in enumerate(system):
        gap = residual(index)
        if gap not in (None, Decimal(0)):
            prefecture.review.append({"check": name, "residual": str(gap), "expected": str(constant)})
    for cell, kind in how.items():
        if kind in ("repaired", "solved"):
            key, r = cell
            seen = sorted({str(value) for value, _ in prefecture.observed.get(cell, [])})
            prefecture.review.append(
                {"check": kind, "column": key, "row": prefecture.rows[r], "value": str(values[cell]), "reads": seen}
            )


def doubtful(prefecture: Prefecture, facts: Official) -> set[tuple[str, int, str]]:
    """The bands behind every cell of an unmet identity or subtotal, and behind every disputed cell of a column
    whose counting districts miss the printed total."""
    cells: set[Cell] = set()
    for terms, constant, name in equations(prefecture, facts):
        if name.startswith("total"):
            continue
        values = [(value, sign) for cell, sign in terms if (value := prefecture.values.get(cell)) is not None]
        if len(values) == len(terms) and sum((sign * value for value, sign in values), Decimal(0)) != constant:
            cells.update(cell for cell, _ in terms)
    for key, total in facts.totals[prefecture.number].items():
        leaves = [(key, leaf) for leaf in prefecture.leaves]
        if not any(cell in prefecture.observed for cell in leaves):
            continue
        if sum((prefecture.values.get(cell) or Decimal(0) for cell in leaves), Decimal(0)) != total:
            cells.update(cell for cell in leaves if len({value for value, _ in prefecture.observed.get(cell, [])}) != 1)
    return {source[:3] for cell in cells for _, source in prefecture.observed.get(cell, [])}


def parse() -> Path:
    """Parse every cached read into counting-district values and write the review list and the ties."""
    facts = official()
    found = fragments(raw.keys(SOURCE), facts)
    by: dict[int, list[Fragment]] = {}
    for fragment in found:
        by.setdefault(fragment.document.prefecture, []).append(fragment)
    rows: list[dict[str, str]] = []
    review: list[dict[str, Any]] = []
    ties: dict[str, Any] = {
        "prefectures": {},
        "parties": {},
        "people": {"tied": 0, "miss": []},
        "smd": {"tied": 0, "miss": []},
    }
    sums: dict[str, Decimal] = {}
    doubted: set[tuple[str, int, str]] = set()
    for number in sorted(by):
        prefecture = observe(number, by[number])
        structure(prefecture)
        solve(prefecture, facts)
        doubted |= doubtful(prefecture, facts)
        review += [{"prefecture": number, **item} for item in prefecture.review]
        anchors = facts.totals[number]
        missing = sorted(key for key in anchors if not any((key, r) in prefecture.observed for r in prefecture.leaves))
        if missing:
            review.append({"prefecture": number, "check": "columns not found", "columns": missing})
        leaves = set(prefecture.leaves)
        for (key, r), value in sorted(prefecture.values.items(), key=lambda item: (item[0][1], item[0][0])):
            if value is None:
                continue
            first = prefecture.observed.get((key, r), [(None, ("", 0, "", 0))])
            source = next((s for v, s in first if v == value), first[0][1])
            rows.append(
                {
                    "prefecture": str(number),
                    "row": str(r),
                    "unit": prefecture.rows[r],
                    "level": "leaf" if r in leaves else "aggregate",
                    "column": key,
                    "votes": str(value),
                    "how": prefecture.how.get((key, r), ""),
                    "pdf": source[0],
                    "page": str(source[1]),
                    "tile": source[2],
                }
            )
            if r in leaves and not key.startswith("s"):
                sums[key] = sums.get(key, Decimal(0)) + value
        off = {
            key: [
                str(sum((prefecture.values.get((key, leaf)) or Decimal(0) for leaf in prefecture.leaves), Decimal(0))),
                str(total),
            ]
            for key, total in anchors.items()
            if sum((prefecture.values.get((key, leaf)) or Decimal(0) for leaf in prefecture.leaves), Decimal(0))
            != total
        }
        ties["prefectures"][NAMES[number - 1]] = {
            "rows": len(prefecture.rows),
            "leaves": len(prefecture.leaves),
            "dropped": prefecture.dropped,
            "off": off,
        }
    found_lists = mic_hoc.listing(21)
    parties, people = mic_hoc.national_lists(raw.cached(RAW, found_lists.lists).path, mic_hoc.ELECTIONS[21][0])
    for record in parties:
        party = party_name(record["party"])
        if party is None:
            continue
        diff = {
            part: [str(sums.get(f"{letter}:{party}")), record[part]]
            for part, letter in (("total", "t"), ("party_votes", "p"), ("candidate_votes", "l"))
            if sums.get(f"{letter}:{party}") != Decimal(record[part])
        }
        ties["parties"][party] = diff or "tied"
    for person in people:
        party = party_name(person["party"]) or person["party"]
        got = sums.get(f"c:{party}:{person['name']}")
        if got == Decimal(person["votes"]):
            ties["people"]["tied"] += 1
        else:
            ties["people"]["miss"].append([party, person["name"], person["votes"], str(got)])
    for number, candidates in facts.smd.items():
        off = ties["prefectures"].get(NAMES[number - 1], {}).get("off", {})
        for candidate in candidates:
            if f"s{candidate.order}" in off:
                ties["smd"]["miss"].append([NAMES[number - 1], candidate.name, *off[f"s{candidate.order}"]])
            else:
                ties["smd"]["tied"] += 1
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / f"counting-v{VERSION}.parquet")
    (PARSED / f"review-v{VERSION}.json").write_text(json.dumps(review, ensure_ascii=False, indent=1))
    (PARSED / f"ties-v{VERSION}.json").write_text(json.dumps(ties, ensure_ascii=False, indent=1))
    (PARSED / f"recheck-v{VERSION}.json").write_text(json.dumps(sorted(doubted), ensure_ascii=False))
    return PARSED
