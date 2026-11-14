import base64
import html
import io
import json
import re
import subprocess
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
from ea.store import DATA, connect

SOURCE = "jp.mic_chiji"
INDEX = "https://www.soumu.go.jp/senkyo/senkyo_s/data/chiji/index.html"
ROOT = "https://www.soumu.go.jp"
ARCHIVE = (
    "https://web.archive.org/web/20100620022653id_/http://www.soumu.go.jp:80/senkyo/senkyo_s/data/chiji/index.html"
)
EARLY = {
    "060409kyoto": "20110323112407",
    "060702shiga": "20110323112402",
    "060806nagano": "20110323112401",
    "060827kagawa": "20110323112400",
    "061112fukushima": "20110323112359",
    "061119okinawa": "20110323112358",
    "061217wakayama": "20110323112350",
    "070121ehime": "20110323112342",
    "070121miyazaki": "20110323112340",
    "070121yamanashi": "20110323112344",
    "070603aomori": None,
    "070722gunma": None,
    "070826saitama": None,
    "071125kochi": None,
    "080127osaka": None,
    "080323kumamoto": None,
    "080713kagoshima": None,
    "080803yamaguchi": None,
    "081019nigata": None,
    "081019toyama": None,
    "081026okayama": None,
    "081116tochiji": None,
    "090125gifu": None,
}
MODEL = "claude-haiku-5-5"
PROMPT = "1"
WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"
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
OLD_LINK = re.compile(r'href="pdf/(\w+)\.pdf"[^>]*>(.*?)</a>')
LABEL = re.compile(r"(令和|平成)(元|\d+)年(\d+)月(\d+)日(?:執行)?([（(]無投票[）)])?(\S+?[都道府県])知事選挙")


def listing() -> list[tuple[str, str, str, bool]]:
    html = raw.cached(SOURCE, INDEX).path.read_bytes().decode("cp932")
    archived = raw.cached(SOURCE, ARCHIVE).path.read_bytes().decode("cp932")
    links = [(ROOT + href, text) for href, text in LINK.findall(html)]
    for name, text in OLD_LINK.findall(archived):
        if name in EARLY:
            url = f"{ROOT}/senkyo/senkyo_s/data/chiji/pdf/{name}.pdf"
            if (stamp := EARLY[name]) is not None:
                url = WAYBACK.format(stamp=stamp, url=url.replace("https", "http"))
            links.append((url, re.sub(r"<[^>]+>", "", text)))
    rows = []
    for url, text in links:
        label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text.replace("&nbsp;", " ")))
        if not (match := LABEL.search(label)):
            raise ValueError(f"unread label {label!r}")
        era, year, month, day, unopposed, prefecture = match.groups()
        held = f"{ERAS[era] + (1 if year == '元' else int(year)):04d}-{int(month):02d}-{int(day):02d}"
        rows.append((url, held, prefecture, unopposed is not None))
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
    raw.fetch(SOURCE, ARCHIVE, suffix=".html")
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


VERSION = "3"
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


def candidates(page_words: list[list[Word]], width: float, height: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    header: dict[str, Word] = {}
    for page, words in enumerate(page_words, 1):
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
            cell = next((w for w in row if w.x0 > anchor.x1 and VOTES.match(w.text)), None)
            votes = None if cell is None else cell.text
            box = None if cell is None else [cell.x0 / width, cell.y0 / height, cell.x1 / width, cell.y1 / height]
            tail = next((w for w in row if cell and re.match(r"^\.\d+$", w.text) and 0 <= w.x0 - cell.x1 < 8), None)
            if votes and cell is not None and tail is not None:
                votes += tail.text
                box = [cell.x0 / width, min(cell.y0, tail.y0) / height, tail.x1 / width, max(cell.y1, tail.y1) / height]
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
                    "page": page,
                    "box": box,
                }
            )
    for order, row in enumerate(rows, 1):
        row["order"] = order
    return rows


FIXES = {
    "之口 里花": "樋之口 里花",
    "松 佐一": "榑松 佐一",
    "\ue020渡 啓祐": "樋渡 啓祐",
    "野口 俊\ue000": "野口 俊邦",
    "小\ue002 隆史": "小鑓 隆史",
    "横内 正 明": "横内 正明",
    "泉 敏 孝": "泉 敏孝",
}
SCANNED = {
    "2008-03-23:熊本県": [
        ("岩下 栄一", None),
        ("北里 敏明", None),
        ("かまくら 孝幸", "鎌倉 孝幸"),
        ("かばしま 郁夫", "蒲島 郁夫"),
        ("矢上 雅義", None),
    ],
    "2008-01-27:大阪府": [
        ("梅田 章二", None),
        ("橋下 とおる", "橋下 徹"),
        ("熊谷 さだとし", "熊谷 貞俊"),
        ("杉浦 清一", None),
        ("高橋 正明", None),
    ],
    "2007-11-25:高知県": [
        ("近森 正久", None),
        ("せきや 徳", "関谷 徳"),
        ("国松 勝", None),
        ("おざき 正直", "尾崎 正直"),
    ],
    "2011-07-31:埼玉県": [("原冨 さとる", "原冨 悟"), ("上田 きよし", "上田 清司"), ("たけだ のぶひろ", "武田 信弘")],
    "2011-07-03:群馬県": [
        ("えびね 篤", "海老根 篤"),
        ("大沢 正明", None),
        ("後藤 新", None),
        ("こすげ 啓司", "小菅 啓司"),
    ],
    "2011-06-05:青森県": [("山内 たかし", "山内 崇"), ("三村 申吾", None), ("よしまた 洋", "吉俣 洋")],
    "2011-02-06:愛知県": [
        ("大村 ひであき", "大村 秀章"),
        ("しげとく 和彦", "重徳 和彦"),
        ("みその 愼一郎", "御園 愼一郎"),
        ("どい 敏彦", "土井 敏彦"),
        ("薬師寺 みちよ", "齋藤 道代"),
    ],
    "2011-01-30:山梨県": [("横内 正明", None), ("大久保 令子", None)],
    "2010-12-26:宮崎県": [
        ("宮本 大善", None),
        ("河野 しゅんじ", "河野 俊嗣"),
        ("津島 忠勝", None),
        ("中馬 章一", None),
    ],
    "2010-11-28:和歌山県": [
        ("にさか 吉伸", "仁坂 吉伸"),
        ("ふじ本 まり子", "藤本 眞利子"),
        ("ほら よしかず", "洞 佳和"),
    ],
    "2010-11-28:愛媛県": [("田中 かつひこ", "田中 克彦"), ("中村 時広", None), ("小松 正幸", None)],
    "2010-11-28:沖縄県": [("なかいま 弘多", "仲井真 弘多"), ("イハ 洋一", "伊波 洋一"), ("金城 タツロー", "金城 竜郎")],
}
MISREAD = {"2007-11-25:高知県": {"voters": "297205"}}
PAGES = DATA / "pages" / SOURCE


def cleaned(name: str | None) -> str | None:
    if name is None:
        return None
    parts = FIXES.get(name, name).split()
    if len(parts) > 2 and len(parts[0]) == len(parts[1]) == 1:
        parts = [parts[0] + parts[1], "".join(parts[2:])]
    name = " ".join(parts)
    if re.search(r"[\ue000-\uf8ff]", name):
        raise ValueError(f"private-use character left in {name!r}")
    return name


def digits(text: str) -> str:
    return re.sub(r"[^\d.]", "", text)


def size(path: Path) -> tuple[float, float]:
    info = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True).stdout
    match = re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info)
    if match is None:
        raise ValueError(f"{path} has no page size")
    return float(match[1]), float(match[2])


def render(path: Path, page: int, dpi: int) -> Image.Image:
    png = subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(path)],
        check=True,
        capture_output=True,
    ).stdout
    return Image.open(io.BytesIO(png))


def ocr_boxes(path: Path, page: int) -> list[tuple[str, list[float]]]:
    image = render(path, page, 300)
    out = []
    for left, top, right, bottom in ((0, 0, 1, 1), (0.6, 0.2, 0.9, 0.8), (0.1, 0.55, 0.9, 0.95)):
        box = (int(image.width * left), int(image.height * top), int(image.width * right), int(image.height * bottom))
        crop = image.crop(box)
        for psm in ("4", "11"):
            tsv = subprocess.run(
                ["tesseract", "-", "-", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789,.", "tsv"],
                input=image_bytes(crop),
                check=True,
                capture_output=True,
            ).stdout.decode()
            lines_: dict[tuple[str, ...], list[tuple[str, float, float, float, float]]] = {}
            for line in tsv.splitlines()[1:]:
                cells = line.split("\t")
                if len(cells) == 12 and cells[11].strip():
                    x, y, w, h = (float(v) for v in cells[6:10])
                    x, y = x + box[0], y + box[1]
                    lines_.setdefault(tuple(cells[1:5]), []).append((cells[11], x, y, x + w, y + h))
            for words in lines_.values():
                for i in range(len(words)):
                    for j in range(i + 1, min(i + 5, len(words) + 1)):
                        span = words[i:j]
                        out.append(
                            (
                                digits("".join(text for text, *_ in span)),
                                [
                                    min(w[1] for w in span) / image.width,
                                    min(w[2] for w in span) / image.height,
                                    max(w[3] for w in span) / image.width,
                                    max(w[4] for w in span) / image.height,
                                ],
                            )
                        )
    return out


def image_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def locate(found: list[tuple[int, str, list[float]]], value: str | None) -> tuple[int | None, list[float] | None]:
    if value is None:
        return None, None
    want = value.rstrip("0").rstrip(".") if "." in value else value
    for page, text, box in reversed(found):
        got = text.rstrip("0").rstrip(".") if "." in text else text
        if got == want:
            return page, box
    return None, None


def checked(key: str, figures_: dict[str, str | None], rows: list[dict[str, Any]], unopposed: bool) -> None:
    if unopposed:
        if len(rows) != 1 or rows[0]["votes"] is not None:
            raise ValueError(f"{key}: an unopposed election should list one candidate without votes")
        return
    votes = [float(str(row["votes"])) for row in rows]
    if abs(sum(votes) - float(figures_["valid"] or "nan")) >= 1:
        raise ValueError(f"{key}: candidate votes sum to {sum(votes)}, valid votes are {figures_['valid']}")
    winners = [row for row in rows if row["result"] == "当"]
    if len(winners) != 1 or float(str(winners[0]["votes"])) != max(votes):
        raise ValueError(f"{key}: the declared winner is not the one leading candidate")
    cast, valid, invalid = (int(figures_[k] or 0) for k in ("cast", "valid", "invalid"))
    if cast != valid + invalid:
        raise ValueError(f"{key}: ballots cast {cast} are not valid {valid} plus invalid {invalid}")
    if figures_["voters"] and figures_["unreturned"] and int(figures_["voters"]) != cast + int(figures_["unreturned"]):
        print(f"{key}: voters {figures_['voters']} are not ballots cast plus {figures_['unreturned']} unreturned")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)
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
        if words[0]:
            width, height = size(artifact.path)
            found = [
                (page, digits(w.text), [w.x0 / width, w.y0 / height, w.x1 / width, w.y1 / height])
                for page, page_words in enumerate(words, 1)
                for w in page_words
                if re.match(r"^-?[\d,]+(?:\.\d+)?[人票]?$", w.text)
            ]
            figures_ = figures(text)
            rows = candidates(words, width, height)
            for row in rows:
                row["name"], row["legal_name"] = cleaned(row["name"]), cleaned(row["legal_name"])
        else:
            found = [(1, t, box) for t, box in ocr_boxes(artifact.path, 1)]
            read_ = read(artifact.sha256, 1)
            figures_ = {k: digits(read_[k]) if read_[k] else None for k in FIGURES}
            figures_["cast"] = str(int(figures_["valid"] or 0) + int(figures_["invalid"] or 0))
            figures_ |= MISREAD.get(key, {})
            rows = []
            for order, ((name, legal), seen) in enumerate(zip(SCANNED[key], read_["candidates"], strict=True), 1):
                votes = digits(seen["votes"])
                page, box = locate(found, votes)
                rows.append(
                    {
                        "name": name,
                        "legal_name": legal,
                        "sex": seen["sex"],
                        "age": int(seen["age"]),
                        "party": seen["party"],
                        "status": seen["status"],
                        "votes": votes,
                        "result": seen["result"] or "落",
                        "page": page or 1,
                        "box": box or [0.0, 0.0, 1.0, 1.0],
                        "order": order,
                    }
                )
        checked(key, figures_, rows, unopposed)
        boxes = {}
        for k in FIGURES:
            page, box = locate(found, figures_[k])
            boxes[f"{k}_page"], boxes[f"{k}_box"] = page, box
        for page in range(1, len(words) + 1):
            image = PAGES / f"{artifact.sha256}-{page}.png"
            if not image.exists():
                render(artifact.path, page, 100).save(image)
        elections.append(
            {
                "key": key,
                "url": url,
                "sha256": artifact.sha256,
                "held": held,
                "prefecture": prefecture,
                "unopposed": unopposed,
                "pages": len(words),
                **figures_,
                **boxes,
            }
        )
        people.extend({"key": key, **row} for row in rows)
    pl.DataFrame(elections, infer_schema_length=None).write_parquet(PARSED / f"elections-v{VERSION}.parquet")
    pl.DataFrame(people, infer_schema_length=None).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")


PUBLISHER = "総務省"
TITLE = "知事選挙 選挙結果調"
LICENSE = "Public Data License 1.0"
LICENSE_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
ATTRIBUTION = "出典：総務省ホームページ"
DIGITIZED = 8
RULES = {"formula": "fptp", "minimum": {"of": "valid", "share": 0.25}}
NHK_SPLIT = "2023-01-01"
WIKIDATA = {
    "日本共産党": "Q641600",
    "れいわ新選組": "Q64405573",
    "日本維新の会": "Q21499745",
    "幸福実現党": "Q1571486",
    "みんなの党": "Q1355093",
    "大阪維新の会": "Q385785",
    "自由民主党": "Q232595",
    "琉球独立党": "Q859008",
}
NEW_PARTIES = {
    "Q859008": (
        ("琉球独立党", "official", None),
        ("かりゆしクラブ", "historical", None),
        ("Kariyushi Club", "english", None),
    ),
    "Q385785": (("大阪維新の会", "official", None), ("Osaka Restoration Association", "english", None)),
    "Q39142381": (
        ("みんなでつくる党", "official", None),
        ("NHKから国民を守る党", "historical", None),
        ("The Collaborative Party", "english", None),
    ),
    "Q136749137": (
        ("NHKから国民を守る党", "official", None),
        ("The Party to Protect the People from NHK", "english", None),
    ),
}
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}


def party_key(party: str, held: str) -> tuple[str, str] | None:
    if party == "無所属":
        return None
    if party == "NHKから国民を守る党":
        return "wikidata", "Q39142381" if held < NHK_SPLIT else "Q136749137"
    if party in WIKIDATA:
        return "wikidata", WIKIDATA[party]
    return f"{SOURCE}.party", party


def emit() -> Path:
    elections = pl.read_parquet(PARSED / f"elections-v{VERSION}.parquet").sort("held", "prefecture")
    people = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    with connect() as conn:
        prefectures = {
            native: (iso, english)
            for iso, native, english in conn.execute(
                """select p.iso_code, ja.text, en.text from polity p
                   join name ja on ja.entity_id = p.id and ja.lang = 'ja' and ja.kind = 'official'
                   join name en on en.entity_id = p.id and en.lang = 'en'
                   where p.iso_code like 'JP-__'"""
            )
        }
        if len(prefectures) != 47:
            raise ValueError("run jp.mlit and jp.diet first: Japan's 47 prefectures must exist")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's prefectural governor elections 2006-2026 from the Ministry of Internal Affairs and "
            "Communications' election summaries, by prefecture",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str, kind: str = "official", preferred: bool | None = None) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" if preferred is None else preferred,
            )

        japan = cs.polity("JP")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=INDEX,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution=ATTRIBUTION,
            notes="Counts are as the ministry's summaries print them, read from each PDF's text layer; the twelve "
            "summaries of 2007 to 2011 that are scans were read from the page images and checked by hand. Summaries "
            "of 2006 and early 2007 no longer on the ministry's site are copies from the Internet Archive.",
        )
        cs.artifact(raw.cached(SOURCE, INDEX), source)
        cs.artifact(raw.cached(SOURCE, ARCHIVE), source)
        system = cs.key(f"{SOURCE}.electoral_system", "governor")
        cs.add("electoral_system", id=system, polity_id=japan, rules=RULES, rules_version=1)
        name(system, "First past the post with a legal minimum of a quarter of the valid votes", "en")
        name(system, "相対多数制（法定得票数は有効投票総数の4分の1）", "ja", "native", False)

        parties: dict[tuple[str, str], UUID] = {}
        for party, held in people.join(elections.select("key", "held"), on="key").select("party", "held").iter_rows():
            if party is None or (found := party_key(party, held)) is None or found in parties:
                continue
            entity = parties[found] = cs.key(*found)
            if entity not in cs.minted:
                continue
            if found[0] == "wikidata" and found[1] not in NEW_PARTIES:
                raise ValueError(f"{found[1]} should already exist from jp.diet")
            cs.add("party", id=entity, kind="party", polity_id=japan)
            if found[0] == "wikidata":
                for text, kind, _ in NEW_PARTIES[found[1]]:
                    name(entity, text, "en" if kind == "english" else "ja", kind, kind in {"official", "english"})
            else:
                name(entity, re.sub(r"^\(略称\)", "", party), "ja")

        locators: list[dict[str, object]] = []
        added: set[UUID] = set()
        pages_: dict[tuple[str, int], UUID] = {}
        for row in elections.iter_rows(named=True):
            held = date.fromisoformat(row["held"])
            iso, english = prefectures[row["prefecture"]]
            code = iso[3:]
            polity = cs.polity(iso)
            area = cs.area("jp.prefecture", code)
            office = cs.key("jp.office", f"governor-{code}")
            if {polity, area, office} & cs.minted:
                raise ValueError(f"run jp.mlit and jp.diet first: {iso} is missing")
            artifact = cs.artifact(raw.cached(SOURCE, row["url"]), source)
            scanned = row["key"] in SCANNED
            for page in range(1, row["pages"] + 1):
                image = PAGES / f"{row['sha256']}-{page}.png"
                width, height = Image.open(image).size
                pages_[row["key"], page] = cs.page(artifact, page)
                cs.add(
                    "source_page",
                    id=pages_[row["key"], page],
                    artifact_id=artifact,
                    page_number=page,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            unit = cs.unit("jp.prefecture", code)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="jp.prefecture",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            election = cs.key(f"{SOURCE}.election", row["key"])
            cs.add(
                "election", id=election, polity_id=polity, kind="general", date_from=held, date_to=held, status="held"
            )
            name(election, f"{held.year} {english} gubernatorial election", "en")
            name(election, f"{row['prefecture']}知事選挙", "ja", "native", False)
            path = f"jp/{slug(english)}/{held}-governor"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key(f"{SOURCE}.contest", row["key"])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit" if row["unopposed"] else "counted",
            )
            name(contest, f"{held.year} election for Governor of {english}", "en")
            cs.add("slug", path=f"{path}/governor", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", row["key"])
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if row["unopposed"] else "counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1,
            )

            def value(
                option: UUID | None, measure: int, amount: str, page: int | None, box: list[float] | None
            ) -> None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=DIGITIZED if scanned else 0,
                    value=Decimal(amount),
                )
                if page is not None and box is not None:
                    locators.append(
                        {
                            "result_set_id": result_set,
                            "unit_id": unit,
                            "option_id": option,
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "page_id": pages_[row["key"], page],
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                        }
                    )

            if not row["unopposed"]:
                for key, measure in MEASURES.items():
                    if row[key] is not None:
                        value(None, measure, row[key], row[f"{key}_page"], row[f"{key}_box"])
            for candidate in people.filter(pl.col("key") == row["key"]).sort("order").iter_rows(named=True):
                legal = candidate["legal_name"] or candidate["name"]
                tag = f"{code}:{legal.replace(' ', '')}"
                person = cs.key(f"{SOURCE}.person", tag)
                if person in cs.minted and person not in added:
                    added.add(person)
                    cs.add("person", id=person, gender={"男": "male", "女": "female"}.get(candidate["sex"] or ""))
                    name(person, legal, "ja")
                found = None if candidate["party"] is None else party_key(candidate["party"], row["held"])
                party = None if found is None else parties[found]
                option = cs.key(f"{SOURCE}.option", f"{row['key']}:{candidate['order']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=candidate["order"],
                    ballot_label=candidate["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{row['key']}:{candidate['order']}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=party,
                    is_independent=party is None,
                    is_incumbent=candidate["status"] == "現",
                )
                if candidate["votes"] is not None:
                    value(option, 1, candidate["votes"], candidate["page"], candidate["box"])
                if row["unopposed"] or candidate["result"] == "当":
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for locator in locators:
            cs.add("value_locator", **locator)
        return cs.write()
