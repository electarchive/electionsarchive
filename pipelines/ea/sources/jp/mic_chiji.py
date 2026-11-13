import base64
import html
import io
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "jp.mic_chiji"
INDEX = "https://www.soumu.go.jp/senkyo/senkyo_s/data/chiji/index.html"
ROOT = "https://www.soumu.go.jp"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
READ_SYSTEM = (
    "You read one page of a Japanese Ministry of Internal Affairs and Communications summary of a prefectural governor "
    "election (知事選挙 選挙結果調). Copy what is printed, character for character, keeping old or variant kanji as "
    "printed. For every candidate row in the 開票結果 table give: the name as it appears on the ballot (the large "
    "characters in the 候補者氏名 column, without the small furigana), the legal name printed in parentheses under it "
    "(null when none), sex (男 or 女, null when the column is absent), age, occupation (職業), party (党派, joined "
    "when it wraps across lines), status (新, 現, 元, or 前), votes exactly as printed (with any decimals, or 無投票), "
    "and the result mark (当, 落, or null). Also copy the page's totals as printed: 当日有権者数 (total column), "
    "投票者数 (total), 投票総数, 有効投票数, 無効投票数, 持帰りその他, and 法定得票数, null when not on the page."
)
FIELD = {"type": ["string", "null"]}
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    key: FIELD
                    for key in ("name", "legal_name", "sex", "age", "occupation", "party", "status", "votes", "result")
                },
                "required": ["name", "legal_name", "sex", "age", "occupation", "party", "status", "votes", "result"],
                "additionalProperties": False,
            },
        },
        **{key: FIELD for key in ("registered", "voters", "cast", "valid", "invalid", "unreturned", "threshold")},
    },
    "required": ["candidates", "registered", "voters", "cast", "valid", "invalid", "unreturned", "threshold"],
    "additionalProperties": False,
}
ERAS = {"令和": 2018, "平成": 1988}
LINK = re.compile(r'href="(/main_content/\d+\.pdf)"[^>]*>([^<]*)')
LABEL = re.compile(r"(令和|平成)(元|\d+)年(\d+)月(\d+)日(?:執行)?([（(]無投票[）)])?(\S+?[都道府県])知事選挙")


def listing() -> list[tuple[str, str, str, bool]]:
    html = raw.cached(SOURCE, INDEX).path.read_bytes().decode("cp932")
    rows = []
    for href, text in LINK.findall(html):
        label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text.replace("&nbsp;", " ")))
        if not (match := LABEL.search(label)):
            raise ValueError(f"unread label {label!r}")
        era, year, month, day, unopposed, prefecture = match.groups()
        held = f"{ERAS[era] + (1 if year == '元' else int(year)):04d}-{int(month):02d}-{int(day):02d}"
        rows.append((ROOT + href, held, prefecture, unopposed is not None))
    return rows


def read_key(sha256: str, page: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{sha256}/{page}"


def read(sha256: str, page: int) -> dict:
    message = json.loads(raw.cached(SOURCE, read_key(sha256, page)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def page_count(path: Path) -> int:
    info = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True).stdout
    match = re.search(r"Pages:\s+(\d+)", info)
    if match is None:
        raise ValueError(f"{path} has no page count")
    return int(match[1])


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for url, *_ in listing():
        artifact = raw.fetch(SOURCE, url, suffix=".pdf")
        for page in range(1, page_count(artifact.path) + 1):
            png = subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "200",
                    "-gray",
                    "-png",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    str(artifact.path),
                ],
                check=True,
                capture_output=True,
            ).stdout
            image = Image.open(io.BytesIO(png))
            image.thumbnail((LONGEST, LONGEST))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=8000,
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
                            TextBlockParam(type="text", text="Read the candidates and totals on this page."),
                        ],
                    }
                ],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
            )
            wanted[read_key(artifact.sha256, page)] = (
                params,
                (image.width * image.height / 750 * price_in + 3000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "chiji", wanted)[1])


VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
PAGE = re.compile(r"<page [^>]*>(.*?)</page>", re.DOTALL)
VOTES = re.compile(r"^(?:[\d,]+(?:\.\d+)?|無投票)$")
FIGURES = {
    "registered": "当日有権者数",
    "voters": "投票者数",
    "cast": "投票総数",
    "valid": "有効投票数",
    "invalid": "無効投票数",
    "unreturned": "持帰りその他",
    "threshold": "法定得票数",
}


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def pages(path: Path) -> list[list[Word]]:
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [
        [
            Word(float(a), float(b), float(c), float(d), unicodedata.normalize("NFKC", html.unescape(t)))
            for a, b, c, d, t in WORD.findall(page)
        ]
        for page in PAGE.findall(xml)
    ]


def layout(path: Path) -> str:
    return unicodedata.normalize(
        "NFKC",
        subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout,
    )


def lines(words: list[Word]) -> list[list[Word]]:
    out: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.y, w.x)):
        if out and abs(out[-1][0].y - word.y) < word.h / 2:
            out[-1].append(word)
        else:
            out.append([word])
    return [sorted(line, key=lambda w: w.x) for line in out]


def figures(text: str) -> dict[str, str | None]:
    flat = re.sub(r"\s+", "", text)
    out: dict[str, str | None] = {}
    for key, label in FIGURES.items():
        if key in {"registered", "voters"}:
            match = re.search(label + r"([\d,]+)人([\d,]+)人([\d,]+)人", flat)
            out[key] = match[3].replace(",", "") if match else None
        else:
            match = re.search(label + r"(-?[\d,]+(?:\.\d+)?)[人票]", flat)
            out[key] = match[1].replace(",", "") if match else None
    return out


def name_of(words: list[Word], body: float) -> tuple[str | None, str | None]:
    plain, bracketed = [], []
    for line in lines([w for w in words if w.h > body * 0.8]):
        text = "".join(
            w.text if i == 0 or w.x0 - line[i - 1].x1 < body / 2 else " " + w.text for i, w in enumerate(line)
        )
        if re.search(r"[（(]", text):
            bracketed.append(re.sub(r"\s*[（()）]\s*", "", text))
        else:
            plain.append(text)
    if not plain and bracketed:
        plain.append(bracketed.pop(0))
    elif len(plain) > 1 and not bracketed:
        bracketed.append(plain.pop())
    return " ".join(plain) or None, " ".join(bracketed) or None


def candidates(page_words: list[list[Word]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    header: dict[str, Word] = {}
    for words in page_words:
        marks = {"who": "候", "age": "年齢", "job": "職", "party": "派", "status": "新現", "sex": "性別"}
        found = {
            key: w
            for key, mark in marks.items()
            for w in words
            if w.text.startswith(mark) and len(w.text) <= len(mark) + 3
        }
        top = max((found[key].y1 for key in ("status", "who") if key in found), default=0) + 1
        header |= found
        if "status" not in header:
            continue
        bottom = min(
            (
                w.y
                for w in words
                if re.sub(r"\s", "", w.text).startswith(("投票総数", "投"))
                and any("総" in v.text for v in words if abs(v.y - w.y) < 2 and v.x - w.x < 80)
            ),
            default=1e9,
        )
        status_x = header["status"].x
        anchors = sorted(
            (
                w
                for w in words
                if w.text in {"新", "現", "元", "前"}
                and abs(w.x - status_x) < 15
                and top < w.y < bottom
                and any(VOTES.match(v.text) and abs(v.y - w.y) < 2 and v.x0 > w.x1 for v in words)
            ),
            key=lambda w: w.y,
        )
        body = sorted(a.h for a in anchors)[len(anchors) // 2] if anchors else 0
        split = (header["job"].x + header["party"].x) / 2
        edge = min(header[key].x0 for key in ("age", "sex") if key in header)
        for k, anchor in enumerate(anchors):
            lo = (anchors[k - 1].y + anchor.y) / 2 if k else max(top, anchor.y - 3 * body)
            hi = (anchor.y + anchors[k + 1].y) / 2 if k + 1 < len(anchors) else min(bottom, anchor.y + 3 * body)
            band = [w for w in words if lo <= w.y < hi]
            row = [w for w in band if abs(w.y - anchor.y) < body / 2]
            ballot, legal = name_of([w for w in band if w.x1 < edge - 2], body)
            age = next(
                (w.text for w in band if abs(w.x - header["age"].x) < 12 and re.match(r"^\d{2,3}歳?$", w.text)), None
            )
            votes = next((w.text for w in row if w.x0 > anchor.x1 and VOTES.match(w.text)), None)
            result = next((w.text for w in row if w.text in {"当", "落"}), None)
            sex = next(
                (
                    w.text
                    for w in band
                    if w.text in {"男", "女"} and "sex" in header and abs(w.x - header["sex"].x) < 15
                ),
                None,
            )
            party = "".join(w.text for w in sorted(band, key=lambda w: (w.y, w.x)) if split < w.x and w.x1 < anchor.x0)
            rows.append(
                {
                    "name": ballot,
                    "legal_name": legal,
                    "sex": sex,
                    "age": None if age is None else int(age.rstrip("歳")),
                    "party": party or None,
                    "status": anchor.text,
                    "votes": None if votes in {None, "無投票"} else votes.replace(",", ""),
                    "result": result,
                }
            )
    for order, row in enumerate(rows, 1):
        row["order"] = order
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    elections, people = [], []
    for url, held, prefecture, unopposed in listing():
        artifact = raw.cached(SOURCE, url)
        words = pages(artifact.path)
        text = layout(artifact.path)
        if words[0] and prefecture not in re.sub(r"\s+", "", text):
            print(f"{held} {prefecture}: {url} prints another election; skipped")
            continue
        key = f"{held}:{prefecture}"
        unopposed = unopposed or bool(re.search(r"選\s*挙\s*期\s*日\s+無\s*投\s*票", text))
        elections.append(
            {
                "key": key,
                "url": url,
                "sha256": artifact.sha256,
                "held": held,
                "prefecture": prefecture,
                "unopposed": unopposed,
                **figures(text),
            }
        )
        people.extend({"key": key, **row} for row in candidates(words))
    pl.DataFrame(elections, infer_schema_length=None).write_parquet(PARSED / f"elections-v{VERSION}.parquet")
    pl.DataFrame(people, infer_schema_length=None).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")
