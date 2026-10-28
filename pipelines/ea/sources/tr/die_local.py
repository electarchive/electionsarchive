import difflib
import html
import io
import itertools
import json
import math
import re
import struct
import subprocess
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from uuid import UUID

import httpx
import numpy as np
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.tr.tuik_secim import ILCE_ALIASES, norm
from ea.sources.tr.ysk import DIGITIZED, PROVINCES, REGISTERED, VALID, VOTERS, VOTES, title
from ea.store import DATA, connect

SOURCE = "tr.die_local"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
LIBRARY = "https://kutuphane.tuik.gov.tr/pdf"
WAYBACK = "https://web.archive.org/web/2025id_"
BOOKS = {1963: "0015160", 1973: "0015468"}
TABLES_1973 = {"il_genel": (10, 47), "belediye_baskani": (52, 133), "belediye_meclisi": (138, 221)}
PARTY_WORDS = {"AP", "CHP", "CGP", "DP", "MP", "MHP", "MSP", "TBP", "Bağımsızlar", "CKMP", "TİP", "YTP"}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
DIGITS = str.maketrans({"b": "6", "i": "1", "İ": "1", "I": "1", "l": "1", "o": "0", "O": "0", "S": "5", ",": "."})
DASHES = {"—", "-", "_", "–", ".—", "—.", "•—", "—,", ".", "•", ",", "'", "^"}


def url(year: int) -> str:
    return f"{LIBRARY}/{BOOKS[year]}.pdf"


def fetch() -> None:
    for year in BOOKS:
        try:
            raw.cached(SOURCE, url(year))
            continue
        except LookupError:
            pass
        response = httpx.get(f"{WAYBACK}/{url(year)}", follow_redirects=True, timeout=900)
        response.raise_for_status()
        raw.store(
            SOURCE,
            url(year),
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def pages(
    path: str, first: int, last: int
) -> dict[int, tuple[float, float, list[tuple[float, float, float, float, str]]]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), path, "-"], capture_output=True, check=True
    ).stdout.decode()
    found = {}
    for number, chunk in enumerate(out.split("<page ")[1:], first):
        width, height = (float(value) for value in re.findall(r'(?:width|height)="([\d.]+)"', chunk)[:2])
        found[number] = (
            width,
            height,
            [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(chunk)],
        )
    return found


def lines(words: list[tuple[float, float, float, float, str]], top: float) -> list[list[tuple]]:
    rows: list[list[tuple]] = []
    for word in sorted((w for w in words if w[1] > top), key=lambda w: (w[1] + w[3]) / 2):
        center = (word[1] + word[3]) / 2
        if rows and abs(center - median((w[1] + w[3]) / 2 for w in rows[-1])) <= 3.2:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row) for row in rows]


def numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\d.,bilIİoOS]+", text)) and any(c.isdigit() for c in text)


def integer(tokens: list[tuple]) -> int | None:
    text = " ".join(t[4].translate(DIGITS) for t in tokens).replace(".", "")
    if re.fullmatch(r"\d{1,3}( \d{3})*|\d+", text):
        return int(text.replace(" ", ""))
    return None


def percent(tokens: list[tuple]) -> float | None:
    text = "".join(t[4].translate(DIGITS) for t in tokens).replace(".", "")
    if re.fullmatch(r"\d{2,4}", text) and int(text) <= 1000:
        return int(text) / 10
    return None


def header(words: list[tuple], top: float) -> tuple[dict[str, float], float]:
    labels = {}
    for x0, y0, x1, y1, text, *_ in words:
        if y1 < top and text.strip(".") in PARTY_WORDS and text not in labels:
            labels[text.strip(".")] = (x0 + x1) / 2
    return dict(sorted(labels.items(), key=lambda item: item[1])), top


def bands(labels: dict[str, float], right: float | None) -> dict[str, tuple[float, float]]:
    names = list(labels)
    centers = [labels[n] for n in names]
    out = {}
    for i, label in enumerate(names):
        left = (centers[i - 1] + centers[i]) / 2 if i else centers[i] - (centers[1] - centers[0]) / 2
        end = (
            (centers[i] + centers[i + 1]) / 2
            if i + 1 < len(names)
            else (right if right is not None else centers[i] + (centers[i] - centers[i - 1]) / 2)
        )
        out[label] = (left, end)
    return out


def groups(tokens: list[tuple]) -> list[list[tuple]]:
    out: list[list[tuple]] = []
    for token in tokens:
        text = token[4].translate(DIGITS)
        if out and token[0] - out[-1][-1][2] <= 6.5 and re.fullmatch(r"\d{3}", text) and "." not in out[-1][-1][4]:
            out[-1].append(token)
        else:
            out.append([token])
    return out


def box(tokens: list[tuple], width: float, height: float) -> list[float]:
    return [
        round(min(t[0] for t in tokens) / width, 6),
        round(min(t[1] for t in tokens) / height, 6),
        round(max(t[2] for t in tokens) / width, 6),
        round(max(t[3] for t in tokens) / height, 6),
    ]


HEADS = {"Aldığı", "oy", "%", "/o", "V", "sayısı", "Partisi", "Soyadı", "oranı", "Adı"}


def spread(left: tuple, right: tuple, table: str, pages: tuple[int, int], last: dict | None = None) -> list[dict]:
    rows = []
    sides = {}
    last = {} if last is None else last
    for which, (width, height, words) in (("left", left), ("right", right)):
        top = max((w[3] for w in words if w[4] in HEADS and w[3] < 230), default=150) + 2
        labels, _ = header(words, top)
        if len(labels) < 2 and which in last:
            top, labels, adi = last[which]
            words = [*words, *adi]
        else:
            last[which] = (top, labels, [w for w in words if w[4] == "Adı" and w[3] < top])
        named = next((w for w in words if w[4] == "Adı" and w[3] < top), None)
        cut = None if named is None else named[0] - 6
        parts = bands(labels, cut)
        if cut is not None:
            cut = parts["Bağımsızlar"][0] + 2 * (labels["Bağımsızlar"] - parts["Bağımsızlar"][0]) + 4
            parts["Bağımsızlar"] = (parts["Bağımsızlar"][0], cut)
        dotted: dict[str, list[float]] = {label: [] for label in parts}
        body = lines(words, top)
        for row in body:
            for token in row:
                if numeric(token[4]):
                    for label, (start, end) in parts.items():
                        if start <= (token[0] + token[2]) / 2 < end:
                            dotted[label].append(token[2])
        edges = {label: median(x for x in xs if x >= max(xs) - 6) if xs else None for label, xs in dotted.items()}
        previous = None
        for label, (begin, end) in list(parts.items()):
            if edges[label] is not None:
                parts[label] = (begin if previous is None else previous + 2, edges[label] + 3)
                previous = edges[label]
        sides[which] = (width, height, body, parts, edges, cut, top, labels)
    width, height, body, parts, edges, _, ltop, _ = sides["left"]
    if not parts or not sides["right"][3]:
        return rows
    start = min(begin for begin, _ in parts.values())
    entries = []
    for row in body:
        text = [t for t in row if not numeric(t[4]) and t[4] not in DASHES and t[2] < start]
        if not text:
            continue
        before = [t for t in row if numeric(t[4]) and t[2] < start]
        entries.append(((row[0][1] + row[0][3]) / 2, row, text, before))
    rbody, rtop, rlabels = sides["right"][2], sides["right"][6], sides["right"][7]
    rtokens = [t for r in rbody for t in r]
    rcenters = [(r[0][1] + r[0][3]) / 2 for r in rbody]
    best, offset = -1, 0.0
    for step in range(-32, 33):
        shift = rtop - ltop + step / 4
        hits = sum(any(abs(y + shift - c) <= 2.5 for c in rcenters) for y, *_ in entries)
        if hits > best:
            best, offset = hits, shift
    for y, row, text, before in entries:
        match = sorted(t for t in rtokens if abs((t[1] + t[3]) / 2 - y - offset) <= 4.5)
        record = {
            "table": table,
            "name": " ".join(t[4] for t in text),
            "indent": min(t[0] for t in text),
            "cells": [],
            "mayor": None,
            "mayor_party": None,
            "mayor_box": None,
        }
        metrics = ["boxes", "registered", "voters", "valid", "turnout"]
        for chosen in ([t for t in before if len(t) == 5], before):
            values = groups(chosen)
            ints = [g for g in values if "." not in "".join(t[4] for t in g)]
            pcts = [g for g in values if "." in "".join(t[4] for t in g)]
            if len(ints) == 4 or (len(ints) == 5 and not pcts):
                break
        if len(ints) == 5 and not pcts:
            pcts = [ints.pop()]
        for metric, group in zip(metrics, [*ints[:4], *pcts[:1]], strict=False):
            value = percent(group) if metric == "turnout" else integer(group)
            crop = [min(t[0] for t in group), y - 6, max(t[2] for t in group), y + 6]
            record["cells"].append((metric, value, None, pages[0], box(group, width, height), {"vote": crop}))
        if len(ints) != 4:
            record["cells"].append(("?pre", None, None, pages[0], box(before or text, width, height), {}))
        for side, tokens_row in (("left", row), ("right", match or [])):
            w, h, _, bands_, edges_, cut, _, _ = sides[side]
            page = pages[0] if side == "left" else pages[1]
            middle = y if side == "left" else y + offset
            for label, (begin, end) in bands_.items():
                inside = [t for t in tokens_row if begin <= (t[0] + t[2]) / 2 < end and numeric(t[4])]
                if not inside:
                    continue
                edge = edges_[label] if edges_[label] is not None else end - 4
                share = [t for t in inside if t[2] >= edge - 21]
                count = [t for t in inside if t[2] < edge - 21]
                record["cells"].append(
                    (
                        label,
                        integer(count) if count else None,
                        percent(share) if share else None,
                        page,
                        box(inside, w, h),
                        {
                            "vote": [begin, middle - 6, edge - 19, middle + 6],
                            "pct": [edge - 22, middle - 6, edge + 3, middle + 6],
                            "ocr": any(len(t) > 5 for t in count),
                        },
                    )
                )
            if side == "right" and cut is not None:
                tail = [
                    t
                    for t in tokens_row
                    if t[0] > rlabels["Bağımsızlar"] + 10 and not numeric(t[4]) and t[4] not in DASHES
                ]
                if tail:
                    words_ = [t[4] for t in tail]
                    if words_[-1] in PARTY_WORDS or words_[-1] == "Bağımsız":
                        record["mayor_party"] = words_.pop()
                    record["mayor"] = " ".join(words_)
                    record["mayor_box"] = (page, box(tail, w, h))
        rows.append(record)
    return rows


def ocr(year: int, number: int, width: float, height: float) -> tuple[float, float, list[tuple]]:
    pdf = raw.cached(SOURCE, url(year)).path
    found = scan.words(pdf, number, PARSED / "ocr" / f"{year}-{number}.tsv", "tur")
    return (
        width,
        height,
        [(b[0] * 72 / 300, b[1] * 72 / 300, b[2] * 72 / 300, b[3] * 72 / 300, text) for text, b, _, _ in found],
    )


def merged(year: int, number: int, layer: tuple[float, float, list[tuple]]) -> tuple[float, float, list[tuple]]:
    width, height, words = layer
    extra = []
    for x0, y0, x1, y1, text in ocr(year, number, width, height)[2]:
        text = text.strip(")(|[]{}!,;:'\"‘’`©“”*»«")
        if any(c.isdigit() for c in text):
            text = re.sub(r"[)|İi]+$", "", text.replace(")", "").replace("|", ""))
            if not re.fullmatch(r"\d+|\d{1,3}\.\d", text):
                continue
        elif not (
            re.fullmatch(r"[A-ZÇĞİÖŞÜ][a-zçğıöşü]+|[A-ZÇĞİÖŞÜ]\.", text) or text in PARTY_WORDS or text == "Bağımsız"
        ):
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(a - 1.5 <= cx <= c + 1.5 and b - 1.5 <= cy <= d + 1.5 for a, b, c, d, *_ in words):
            continue
        if any(t == text and abs((b + d) / 2 - cy) < 6 and abs((a + c) / 2 - cx) < 16 for a, b, c, d, t, *_ in words):
            continue
        extra.append((x0, y0, x1, y1, text, 1))
    return width, height, [*words, *extra]


PARTIES_1973 = ("AP", "CHP", "CGP", "DP", "MP", "MHP", "MSP", "TBP", "Bağımsızlar")


def problems(cells: dict[str, list]) -> list[tuple[str, str]]:
    valid = cells["valid"][1] if "valid" in cells else None
    found = []
    for label, cell in cells.items():
        if label not in PARTIES_1973:
            continue
        vote, share = cell[1], cell[2]
        if vote is None and share is not None:
            found.append((label, "vote"))
        elif vote is not None and share is None:
            found.append((label, "pct"))
        elif vote is not None and valid and abs(vote * 100 / valid - share) > 0.15:
            found.append((label, "both"))
    return found


def settled(cells: dict[str, list]) -> bool:
    found = problems(cells)
    votes = [cell[1] for label, cell in cells.items() if label in PARTIES_1973]
    valid = cells["valid"][1] if "valid" in cells else None
    return not found or (None not in votes and sum(votes) == valid)


def reread(year: int, page: int, crops: list[list[float]]) -> list[list[str]]:
    image = PARSED / "img" / f"{year}-{page}.png"
    if not image.exists():
        image.parent.mkdir(parents=True, exist_ok=True)
        scan.rendered(raw.cached(SOURCE, url(year)).path, page, image.parent).rename(image)
    return scan.read(
        image,
        [
            ((round(a * 300 / 72) - 6, round(b * 300 / 72) - 6, round(c * 300 / 72) + 6, round(d * 300 / 72) + 6), True)
            for a, b, c, d in crops
        ],
    )


def readings(texts: list[str], kind: str) -> list[float]:
    out = []
    for text in texts:
        digits = re.sub(r"[^0-9.]", "", text)
        if kind == "pct":
            digits = digits.replace(".", "")
            if re.fullmatch(r"\d{2,4}", digits) and int(digits) <= 1000:
                out.append(int(digits) / 10)
        elif re.fullmatch(r"\d+", digits.replace(".", "")):
            out.append(int(digits.replace(".", "")))
    return out


def resolve(year: int, record: dict) -> dict:
    cells = {cell[0]: list(cell) for cell in record["cells"]}
    if settled(cells) or "valid" not in cells:
        record["unresolved"] = [] if settled(cells) else [label for label, _ in problems(cells)]
        return record
    wanted = []
    for label, kind in problems(cells):
        cell = cells[label]
        for part in ("vote", "pct"):
            if kind in (part, "both") or (kind == "pct" and part == "vote"):
                wanted.append((label, part, cell[3], cell[5][part]))
    if len(problems(cells)) == len([c for c in cells if c in PARTIES_1973]) or all(
        k == "both" for _, k in problems(cells)
    ):
        wanted.append(("valid", "vote", cells["valid"][3], cells["valid"][5]["vote"]))
    read: dict[tuple[str, str], list[float]] = {}
    for page in {w[2] for w in wanted}:
        texts = reread(year, page, [w[3] for w in wanted if w[2] == page])
        for (label, part, _, _), got in zip([w for w in wanted if w[2] == page], texts, strict=True):
            read[(label, part)] = readings(got, part)
    valids = [cells["valid"][1], *read.get(("valid", "vote"), [])]
    for valid in dict.fromkeys(v for v in valids if v):
        trial = {label: list(cell) for label, cell in cells.items()}
        trial["valid"][1] = valid
        for label, kind in problems(trial):
            cell = trial[label]
            votes = [cell[1], *read.get((label, "vote"), [])] if kind != "pct" else [cell[1]]
            shares = [cell[2], *read.get((label, "pct"), [])]
            fitting = [
                (v, p)
                for v in votes
                if v is not None
                for p in shares
                if p is not None and abs(v * 100 / valid - p) <= 0.15
            ]
            if fitting:
                cell[1], cell[2] = fitting[0]
            elif kind == "pct" and (cell[5]["ocr"] or cell[1] < 10):
                cell[1] = None
        if not problems(trial) or settled(trial):
            cells = trial
            break
    record["cells"] = [tuple(cell) for cell in cells.values()]
    record["unresolved"] = [] if settled(cells) else [label for label, _ in problems(cells)]
    return record


NOISE = re.compile(r"[^\wçğıöşüÇĞİÖŞÜâîûÂÎÛ().\- ]")


def tidy(text: str) -> tuple[str, str | None, bool]:
    bucak = "B.M" in text or "B. M" in text
    text = re.sub(r"\(\s*B\s*\.\s*M\s*\.?\s*\)", "", text)
    text = NOISE.sub(" ", text)
    alias = None
    found = re.search(r"\(([^)]+)\)", text)
    if found:
        alias = found.group(1).strip()
        text = text[: found.start()] + text[found.end() :]
    words = [w for w in text.split() if len(w) > 2 or w[:1].isupper()]
    words = [w[0].upper().replace("i", "İ") + w[1:] if w[0].islower() else w for w in words]
    return " ".join(words), alias, bucak


TABLES_1963 = {"mayor": "belediye_baskani", "meclis": "belediye_meclisi", "genel": "il_genel"}
LEFT_1963 = ("name", "population", "boxes", "registered", "voters")
PARTIES_1963 = ("AP", "CHP", "CKMP", "MP", "TİP", "YTP", "Bağımsızlar")
RIGHT_1963 = ("valid", *PARTIES_1963)
COUNTS_1963 = ("registered", "voters", "valid")
ERRATA_1963 = {
    (1174, "registered", 17475): 174715,
    (1174, "registered", 31487): 61487,
    (1174, "registered", 20795): 10795,
    (1174, "registered", 18264): 18624,
}
NOT_ILCE = {("01", "Mustafabeyli"), ("42", "Akçaşehir"), ("44", "Erkenek")}
NAMES_1963 = {(796, 3): "Çukurca Şehir", (602, 1): "Elazığ Şehir", (604, 1): "Elazığ Şehir"}


def runs(mask: np.ndarray, gap: int = 3) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for i in np.flatnonzero(mask):
        if out and i - out[-1][1] <= gap:
            out[-1][1] = int(i)
        else:
            out.append([int(i), int(i)])
    return [(a, b) for a, b in out]


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def image_1963(number: int) -> Image.Image:
    image = PARSED / "img63" / f"{number}.png"
    if not image.exists():
        image.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "100",
                "-gray",
                "-png",
                "-f",
                str(number),
                "-l",
                str(number),
                "-singlefile",
                str(raw.cached(SOURCE, url(1963)).path),
                str(image.with_suffix("")),
            ],
            check=True,
        )
    return Image.open(image).convert("L")


def digits(crop: Image.Image, passes: tuple[tuple[bool, str, int], ...]) -> list[int | None]:
    out: list[int | None] = []
    for binary, psm, scale in passes:
        image = crop.point(lambda v: 0 if v < 165 else 255) if binary else crop
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        padded = Image.new("L", (image.width + 40, image.height + 40), 255)
        padded.paste(image, (20, 20))
        text = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", psm, "-l", "eng", "-c", "tessedit_char_whitelist=0123456789"],
            input=png(padded),
            capture_output=True,
            check=True,
        ).stdout.decode()
        found = re.sub(r"\D", "", text)
        out.append(int(found) if found else None)
    return out


def sheet_1963(number: int, columns: tuple[str, ...], fixed: dict | None = None) -> dict:
    gray = image_1963(number)
    if fixed is None:
        small = gray.resize((gray.width // 4, gray.height // 4))
        best, angle = -1.0, 0.0
        for step in range(-40, 41):
            ink = np.asarray(small.rotate(step / 20, fillcolor=255, resample=Image.Resampling.BILINEAR)) < 140
            score = float((ink.sum(axis=1).astype(np.float64) ** 2).sum())
            if score > best:
                best, angle = score, step / 20
    else:
        angle = fixed["angle"]
    gray = gray.rotate(angle, fillcolor=255, resample=Image.Resampling.BICUBIC)
    ink = np.asarray(gray) < 140
    if fixed is None:
        height, width = ink.shape
        lines = ink[:, int(width * 0.1) : int(width * 0.9)].mean(axis=1)
        rules = [r for r in runs(lines > 0.4) if r[0] < height * 0.35]
        if len(rules) < 2:
            raise ValueError(f"page {number}: rules {rules}")
        top, head = rules[0], rules[-1]
        floor = [r for r in runs(lines > 0.4) if r[0] > height * 0.8]
        body0, body1 = head[1] + 4, (floor[0][0] - 5 if floor else int(height * 0.97))
        edge = ink.mean(axis=0) > 0.5
        x0, x1 = max(runs(ink[head[0] - 2 : head[1] + 3].any(axis=0) & ~edge, 60), key=lambda r: r[1] - r[0])
        scores = []
        for block, scale in (
            (ink[top[1] + 3 : head[0] - 2, x0 + 25 : x1 - 25], max(head[0] - top[1], 1) / 2.5),
            (ink[body0:body1, x0 + 25 : x1 - 25], 400),
        ):
            block = block | np.roll(block, 1, axis=1) | np.roll(block, -1, axis=1) | np.roll(block, 2, axis=1)
            longest = np.zeros(block.shape[1], dtype=np.int32)
            current = np.zeros(block.shape[1], dtype=np.int32)
            for row in block:
                current = np.where(row, current + 1, 0)
                longest = np.maximum(longest, current)
            scores.append(longest / scale)
        score = np.maximum(*scores)
        peaks = [(float(score[a : b + 1].max()), (a + b) // 2 + x0 + 25) for a, b in runs(score > 0.15, 6)]
        shares = (
            (0.177, 0.290, 0.403, 0.516, 0.629, 0.742, 0.855) if columns == RIGHT_1963 else (0.338, 0.522, 0.636, 0.820)
        )
        edges = [x0]
        for x in (x0 + (x1 - x0) * share for share in shares):
            near = [(abs(p - x) - v * 10, p) for v, p in peaks if abs(p - x) <= 40]
            edges.append(int(min(near)[1]) if near else round(x))
        edges.append(x1)
        start = 1 if columns == LEFT_1963 else 0
        marks = []
        for index, (a, b) in enumerate(itertools.pairwise(edges[start:])):
            column = ink[body0:body1, a + 12 : b - 12].sum(axis=1)
            for s, e in runs(column >= 3, 2):
                if 24 <= e - s <= 62 and column[s : e + 1].sum() >= 60:
                    marks.append(((s + e) / 2 + body0, s + body0, e + body0, index))
        clusters: list[list[tuple]] = []
        for mark in sorted(marks):
            if clusters and mark[0] - np.mean([m[0] for m in clusters[-1]]) <= 14:
                clusters[-1].append(mark)
            else:
                clusters.append([mark])
        spans = [
            (int(np.median([m[1] for m in cluster])), int(np.median([m[2] for m in cluster])))
            for cluster in clusters
            if len(cluster) >= 2 and any(m[3] == 0 for m in cluster)
        ]
    else:
        edges, spans, body0, body1 = fixed["edges"], fixed["spans"], 0, 0
    rows = []
    for a, b in spans:
        cells = {}
        for i, label in enumerate(columns):
            if label == "name":
                continue
            x0, y0, x1, y1 = edges[i] + 9, a - 6, edges[i + 1] - 9, b + 6
            part = ink[y0:y1, x0:x1]
            if part.sum() < 25:
                cells[label] = ("blank", None)
                continue
            ys, xs = np.nonzero(part)
            if ys.max() - ys.min() <= 9 and xs.max() - xs.min() >= 14:
                cells[label] = ("dash", None)
                continue
            cells[label] = (
                "ink",
                gray.crop(
                    (x0 + int(xs.min()) - 6, y0 + int(ys.min()) - 6, x0 + int(xs.max()) + 7, y0 + int(ys.max()) + 7)
                ),
            )
        rows.append({"y": [a, b], "cells": cells})
    return {"angle": angle, "edges": edges, "body": [body0, body1], "rows": rows, "gray": gray}


def spread_1963(left: int, table: str) -> dict:
    sides = [sheet_1963(left, LEFT_1963), sheet_1963(left + 1, RIGHT_1963)]
    page, edges = sides[0], sides[0]["edges"]
    tsv = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "tur", "tsv"],
        input=png(page["gray"].crop((max(edges[0] - 70, 0), page["body"][0], edges[1] - 6, page["body"][1]))),
        capture_output=True,
        check=True,
    ).stdout.decode()
    words: dict[tuple, list] = {}
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and f[0] == "5" and f[11].strip():
            words.setdefault((f[2], f[3], f[4]), []).append(
                (int(f[7]) + page["body"][0] + int(f[9]) / 2, int(f[6]), f[11])
            )
    centers = [(r["y"][0] + r["y"][1]) / 2 for r in page["rows"]]
    for record in page["rows"]:
        record["name"] = ""
    for found in sorted(words.values(), key=lambda ws: sum(w[0] for w in ws) / len(ws)):
        text = " ".join(w[2] for w in sorted(found, key=lambda w: w[1]))
        if centers and re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{2}", text):
            y = sum(w[0] for w in found) / len(found)
            record = page["rows"][min(range(len(centers)), key=lambda i: abs(centers[i] - y))]
            record["name"] = (record["name"] + " " + text).strip()
    jobs = [
        (record, label, crop)
        for side in sides
        for record in side["rows"]
        for label, (kind, crop) in record["cells"].items()
    ]
    passes = ((False, "7", 1), (False, "7", 2), (False, "8", 1))
    with ThreadPoolExecutor(12) as pool:
        reads = list(pool.map(lambda job: digits(job[2], passes) if job[2] is not None else [], jobs))
    for (record, label, crop), found in zip(jobs, reads, strict=True):
        kind = record["cells"][label][0]
        if kind == "ink" and all(r is None for r in found) and crop.width > 2.2 * crop.height:
            found = [0, "wide"]
        record["cells"][label] = [None] if kind == "blank" else [0] if kind == "dash" else found
    lrows, rrows = sides[0]["rows"], sides[1]["rows"]
    lc = [(r["y"][0] + r["y"][1]) / 2 for r in lrows]
    rc = [(r["y"][0] + r["y"][1]) / 2 for r in rrows]
    best, scale, shift = -1.0, 1.0, 0.0
    for k in range(-12, 13):
        for d in range(-450, 451, 3):
            hits = (
                (sum(min(abs(a * (1 + k / 400) + d - b) for b in rc) < 10 for a in lc) if rc else 0)
                - abs(d) / 2000
                - abs(k) / 200
            )
            if hits > best:
                best, scale, shift = hits, 1 + k / 400, d
    lc = [a * scale + shift for a in lc]
    n, m = len(lc), len(rc)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            score[i][j] = max(
                score[i + 1][j], score[i][j + 1], score[i + 1][j + 1] + 1 if abs(lc[i] - rc[j]) < 25 else 0
            )
    rows = []
    i = j = 0
    while i < n and j < m:
        if abs(lc[i] - rc[j]) < 25 and score[i][j] == score[i + 1][j + 1] + 1:
            rows.append(
                {
                    "name": lrows[i]["name"],
                    "ly": lrows[i]["y"],
                    "ry": rrows[j]["y"],
                    **lrows[i]["cells"],
                    **rrows[j]["cells"],
                }
            )
            i, j = i + 1, j + 1
        elif score[i][j] == score[i + 1][j]:
            print(f"1963 page {left}: left row at {lrows[i]['y']} unmatched")
            i += 1
        else:
            print(f"1963 page {left + 1}: right row at {rrows[j]['y']} unmatched")
            j += 1
    return {
        "left": left,
        "table": table,
        "angles": [sides[0]["angle"], sides[1]["angle"]],
        "edges": [sides[0]["edges"], sides[1]["edges"]],
        "rows": rows,
        "counts": [n, m],
    }


def choose_1963(row: dict) -> dict:
    tallies = {
        label: Counter(r for r in row.get(label, [None]) if isinstance(r, int)) for label in LEFT_1963[1:] + RIGHT_1963
    }
    agreed = {
        label: bool(found) and (found.most_common(1)[0][1] >= 2 or row.get(label) in ([0], [0, "wide"]))
        for label, found in tallies.items()
    }
    pick = {label: [v for v, _ in found.most_common()] for label, found in tallies.items()}
    choice = {label: (found[0] if found else None) for label, found in pick.items()}
    status = "unsure"
    if None not in (choice[label] for label in RIGHT_1963) and choice["valid"] == sum(choice[p] for p in PARTIES_1963):
        status = "sum"
    else:
        spread = [pick[label] or [None] for label in RIGHT_1963]
        hits = []
        if spread[0][0] is not None and sum(len(s) > 1 for s in spread) <= 6:
            hits = [c for c in itertools.product(*spread) if None not in c and c[0] == sum(c[1:])]
        if len(hits) == 1:
            choice.update(zip(RIGHT_1963, hits[0], strict=True))
            status = "sum"
        elif all(agreed[label] for label in RIGHT_1963):
            status = "agreed"
    unsure = [label for label, ok in agreed.items() if not ok]
    if status == "sum":
        unsure = [label for label in unsure if label in LEFT_1963]
    return {"values": choice, "status": status, "unsure": unsure}


def parse() -> None:
    pdf = str(raw.cached(SOURCE, url(1973)).path)
    rows, values, mayors = [], [], []
    for table, (first, last) in TABLES_1973.items():
        got = {n: merged(1973, n, layer) for n, layer in pages(pdf, first, last).items()}
        plate, district, seq = 0, None, 0
        headers: dict = {}
        for left in range(first, last, 2):
            records = [
                resolve(1973, record) for record in spread(got[left], got[left + 1], table, (left, left + 1), headers)
            ]
            plain = [r["indent"] for r in records if "toplam" not in r["name"].lower()]
            base = min(plain) if plain else 0
            for record in records:
                lowered = record["name"].lower().replace(" ", "")
                cells = {cell[0]: cell for cell in record["cells"]}
                if "valid" not in cells and not any(label in cells for label in PARTIES_1973):
                    continue
                if record["name"].lstrip()[:1] in "(*" or lowered.startswith(("kısaltma", "not")):
                    continue
                if "toplam" in lowered or "topl" in lowered:
                    if "genel" in lowered or "türkiye" in lowered:
                        level, name, alias, bucak = "national", "Türkiye", None, False
                    else:
                        plate += 1
                        district = None
                        level, name, alias, bucak = "province", "", None, False
                else:
                    name, alias, bucak = tidy(record["name"])
                    if table == "il_genel" or record["indent"] <= base + 7:
                        level, district = "district", name
                    else:
                        level = "belde"
                seq += 1
                confirmed = not record["unresolved"]
                valid = cells["valid"][1] if "valid" in cells else None
                pre_ok = "?pre" not in cells and all(
                    cells.get(m, (0, None))[1] is not None for m in ("registered", "voters", "valid")
                )
                if pre_ok:
                    pre_ok = cells["registered"][1] >= cells["voters"][1] >= cells["valid"][1]
                rows.append(
                    {
                        "year": 1973,
                        "table": table,
                        "seq": seq,
                        "plate": plate,
                        "level": level,
                        "district": district if level == "belde" else None,
                        "name": name,
                        "alias": alias,
                        "bucak": bucak,
                        "unresolved": record["unresolved"],
                        "accounting": pre_ok,
                        "printed": record["name"],
                        "page": left,
                    }
                )
                for label, cell in cells.items():
                    if label in ("turnout", "?pre", "boxes") or cell[1] is None:
                        continue
                    if label in ("registered", "voters", "valid"):
                        if not pre_ok:
                            continue
                    elif not confirmed and not (
                        cell[2] is not None and valid and abs(cell[1] * 100 / valid - cell[2]) <= 0.15
                    ):
                        continue
                    values.append(
                        {
                            "year": 1973,
                            "table": table,
                            "seq": seq,
                            "column": label,
                            "value": cell[1],
                            "page": cell[3],
                            "x0": cell[4][0],
                            "y0": cell[4][1],
                            "x1": cell[4][2],
                            "y1": cell[4][3],
                        }
                    )
                if table == "belediye_baskani" and record["mayor"]:
                    mayors.append(
                        {
                            "year": 1973,
                            "seq": seq,
                            "mayor": record["mayor"],
                            "party": record["mayor_party"],
                            "page": record["mayor_box"][0],
                            "x0": record["mayor_box"][1][0],
                            "y0": record["mayor_box"][1][1],
                            "x1": record["mayor_box"][1][2],
                            "y1": record["mayor_box"][1][3],
                        }
                    )
        if plate != 67:
            print(f"{table}: {plate} province totals found, not 67")
    for plate in range(1, 68):
        mayoral = [
            r
            for r in rows
            if r["table"] == "belediye_baskani" and r["plate"] == plate and r["level"] in ("district", "belde")
        ]
        council = [
            r
            for r in rows
            if r["table"] == "belediye_meclisi" and r["plate"] == plate and r["level"] in ("district", "belde")
        ]
        blocks = difflib.SequenceMatcher(
            None, [r["name"] for r in mayoral], [r["name"] for r in council], autojunk=False
        )
        for a, b, size in blocks.get_matching_blocks():
            for offset in range(size):
                council[b + offset]["level"] = mayoral[a + offset]["level"]
        district = None
        for row in council:
            if row["level"] == "district":
                district = row["name"]
                row["district"] = None
            else:
                row["district"] = district
    seq = 0
    book = raw.cached(SOURCE, url(1963)).path
    layer = subprocess.run(["pdftotext", "-layout", str(book), "-"], capture_output=True, check=True).stdout.decode()
    candidates = [
        number
        for number, text in enumerate(layer.split("\f")[:-1], 1)
        if number >= 9
        and not re.search(
            r"MUHTAR|M U H T A R|HEYET|IHTIYAR|İHTİYAR|ÎHTÎYAR",
            " ".join(" ".join(text.split("\n")[:8]).split())[:140].upper(),
        )
    ]
    cached = PARSED / "titles63.json"
    titles = json.loads(cached.read_text()) if cached.exists() else {}

    def strip(number: int) -> tuple[int, str]:
        image = image_1963(number)
        found = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "tur"],
            input=png(image.crop((0, 0, image.width, int(image.height * 0.13)))),
            capture_output=True,
            check=True,
        ).stdout.decode()
        return number, " ".join(found.split())

    with ThreadPoolExecutor(8) as pool:
        for number, text in pool.map(strip, [n for n in candidates if str(n) not in titles]):
            titles[str(number)] = text
    PARSED.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(titles, ensure_ascii=False))
    heads = {int(number): text.upper().replace("Î", "İ") for number, text in titles.items()}

    def left_role(text: str) -> str | None:
        if re.search(r"MUHTAR|HEYET|İHTİYAR|IHTIYAR", text):
            return "muhtar"
        if "GENEL" in text:
            return "genel"
        if re.search(r"BELED.YE\s*M.CL", text):
            return "meclis"
        return "mayor" if re.search(r"BELED.YE", text) else None

    def right_role(text: str) -> str | None:
        if re.search(r"BAŞKAN|BASKAN", text):
            return "mayor"
        if re.search(r"ÜYELER|UYELER", text):
            return "meclis"
        return "R" if re.search(r"MECL.S|SEÇİMİ|SONUÇ|ALINAN", text) else None

    starts: list[tuple[int, str | None, str | None]] = []
    number = min(heads)
    while number <= max(heads):
        lt, rt = left_role(heads.get(number, "")), right_role(heads.get(number + 1, ""))
        if lt in TABLES_1963 or (rt is not None and starts and starts[-1][0] == number - 2):
            starts.append((number, lt, rt))
            number += 2
        else:
            number += 1
    spreads_1963: list[tuple[int, int, str]] = []
    plate, previous = 0, None
    for index, (number, lt, rt) in enumerate(starts):
        if index == 0 or starts[index - 1][0] != number - 2:
            plate += 1
            previous = None
        table = lt if lt in TABLES_1963 else rt if rt in ("mayor", "meclis") else previous
        if lt in TABLES_1963 and rt in ("mayor", "meclis") and rt != lt:
            print(f"1963 spread {number}: left page {lt}, right page {rt}, left out")
        else:
            spreads_1963.append((plate, number, table))
        previous = table
    folder = PARSED / "cells63"
    folder.mkdir(parents=True, exist_ok=True)
    rereads = ((True, "7", 3), (False, "6", 2), (True, "8", 2))
    grouped_1963: dict[tuple[int, str], list[dict]] = {}
    for plate, number, table in spreads_1963:
        target = folder / f"{number}.json"
        if not target.exists():
            try:
                found = spread_1963(number, table)
            except ValueError as error:
                print(f"1963 spread {number} ({table}) not read: {error}")
                continue
            target.write_text(json.dumps(found, ensure_ascii=False))
        data = json.loads(target.read_text())
        if 0 in data["counts"]:
            print(f"1963 spread {number} ({table}): rows found {data['counts']}, left out")
            continue
        if not data.get("reread"):
            wanted = [row for row in data["rows"] if choose_1963(row)["status"] != "sum"]
            jobs = []
            for side, columns, key in ((0, LEFT_1963, "ly"), (1, RIGHT_1963, "ry")):
                sheet = sheet_1963(
                    number + side,
                    columns,
                    {"angle": data["angles"][side], "edges": data["edges"][side], "spans": [r[key] for r in wanted]},
                )
                for row, record in zip(wanted, sheet["rows"], strict=True):
                    jobs += [
                        (row, label, crop)
                        for label, (kind, crop) in record["cells"].items()
                        if kind == "ink" and "wide" not in row[label]
                    ]
            with ThreadPoolExecutor(12) as pool:
                found = list(pool.map(lambda job: digits(job[2], rereads), jobs))
            for (row, label, _), reads in zip(jobs, found, strict=True):
                row[label] = row[label] + reads
            data["reread"] = True
            target.write_text(json.dumps(data, ensure_ascii=False))
        grouped_1963.setdefault((plate, table), []).append(data)
    sizes: dict[int, tuple[int, int]] = {}
    for (plate, table), parts in grouped_1963.items():
        district, first = None, True
        for data in parts:
            for position, row in enumerate(data["rows"]):
                printed = unicodedata.normalize("NFC", NAMES_1963.get((data["left"], position), row["name"]))
                text = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşüâîûÂÎÛ,.()\- ]", " ", printed)
                if "itiraz" in text.lower():
                    continue
                picked = choose_1963(row)
                values_1963 = picked["values"]
                for (page, label, old), new in ERRATA_1963.items():
                    if page == data["left"] and values_1963[label] == old:
                        values_1963[label] = new
                name, alias, bucak = tidy(re.sub(r"\(\s*B\s*\.\s*L\s*\.?\s*\)", "(B.M.)", text))
                words = name.split()
                level = "district"
                town = next(
                    (
                        index
                        for index, word in enumerate(words)
                        if (index and re.fullmatch(r"[ŞS][EeHh][EeHhIiİırR]{0,4}[.,]?", word)) or "Şehir" in word
                    ),
                    None,
                )
                if ("TOPLAM" in text.upper() and town is None and "BUCAK" not in text.upper()) or (first and not words):
                    level, name = "province", ""
                elif words[:1] == ["Bucak"] and table == "genel" and "İlcesi" not in words:
                    name = ""
                elif town is not None:
                    name = " ".join([*words[:town], re.split(r"[ŞS][EeHh]", words[town])[0]]).strip()
                else:
                    for index, word in enumerate(words):
                        stem = word.strip(",.").lower().replace("i̇", "i")
                        if stem in ("ilcesi", "ilçesi", "ilcegi", "ilçegi"):
                            name = " ".join(words[:index])
                            break
                        if stem in ("bucağı", "bucagı", "köyü", "kasabası", "koyu", "nahiyesi"):
                            name, bucak = " ".join(words[:index]), True
                            break
                if level != "province":
                    if bucak:
                        level = "belde"
                    if norm(name) == norm(PROVINCES[plate - 1]):
                        name = "Merkez"
                    if level == "district":
                        district = name
                first = False
                unsure = picked["unsure"]
                counts = [values_1963[label] for label in COUNTS_1963]
                accounting = (
                    None not in counts
                    and not any(label in unsure for label in COUNTS_1963)
                    and counts[0] >= counts[1] >= counts[2]
                )
                seq += 1
                rows.append(
                    {
                        "year": 1963,
                        "table": TABLES_1963[table],
                        "seq": seq,
                        "plate": plate,
                        "level": level,
                        "district": district if level == "belde" else None,
                        "name": name,
                        "alias": alias,
                        "bucak": bucak,
                        "unresolved": [label for label in PARTIES_1963 if label in unsure],
                        "accounting": accounting,
                        "printed": printed,
                        "page": data["left"],
                    }
                )
                for label in [*COUNTS_1963, *PARTIES_1963]:
                    if values_1963[label] is None or label in unsure or (label in COUNTS_1963 and not accounting):
                        continue
                    side = 0 if label in LEFT_1963 else 1
                    columns = LEFT_1963 if side == 0 else RIGHT_1963
                    edges = data["edges"][side]
                    column = columns.index(label)
                    top, bottom = row["ly" if side == 0 else "ry"]
                    page = data["left"] + side
                    if page not in sizes:
                        sizes[page] = image_1963(page).size
                    width, height = sizes[page]
                    angle = math.radians(data["angles"][side])
                    corners = [
                        (
                            width / 2 + (x - width / 2) * math.cos(angle) - (y - height / 2) * math.sin(angle),
                            height / 2 + (x - width / 2) * math.sin(angle) + (y - height / 2) * math.cos(angle),
                        )
                        for x in (edges[column] + 9, edges[column + 1] - 9)
                        for y in (top - 6, bottom + 6)
                    ]
                    values.append(
                        {
                            "year": 1963,
                            "table": TABLES_1963[table],
                            "seq": seq,
                            "column": label,
                            "value": values_1963[label],
                            "page": page,
                            "x0": round(max(min(c[0] for c in corners) / width, 0), 6),
                            "y0": round(max(min(c[1] for c in corners) / height, 0), 6),
                            "x1": round(min(max(c[0] for c in corners) / width, 1), 6),
                            "y1": round(min(max(c[1] for c in corners) / height, 1), 6),
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "rows.parquet")
    pl.DataFrame(values, infer_schema_length=None).write_parquet(PARSED / "values.parquet")
    pl.DataFrame(mayors, infer_schema_length=None).write_parquet(PARSED / "mayors.parquet")


DAY_1973 = date(1973, 12, 9)
PUBLISHER = "Devlet İstatistik Enstitüsü"
TITLE_1973 = "9 Aralık 1973 Mahalli Seçimler Sonuçları (DİE yayın no. 716)"
DAY_1963 = date(1963, 11, 17)
TITLE_1963 = "17 Kasım 1963 Mahalli Seçimler Sonuçları (DİE yayın no. 474)"
LICENSE = (
    "Official statistics published by the State Institute of Statistics under Laws 286, 306, and 307; served by "
    "TÜİK's library, which states no license, retrieved through the Wayback Machine."
)
SPELLINGS_1973 = {
    "Cay": "Çay",
    "Cav": "Çay",
    "Yemce": "Yenice",
    "Sanz": "Sarız",
    "Derinkaya": "Derinkuyu",
    "Gençilcesi": "Genç",
    "Ilıç": "İliç",
    "Gercuş": "Gercüş",
    "Beytüşşebab": "Beytüşşebap",
    "A. Karahisar": "Merkez",
    "A. KARAHİSAR": "Merkez",
    "Maraş": "Merkez",
    "Içel": "Merkez",
    "İçel": "Merkez",
}
KEYS = {
    "AP": "adalet-partisi-1961",
    "CHP": "cumhuriyet-halk-partisi",
    "CGP": "cumhuriyetci-guven-partisi",
    "DP": "demokratik-parti",
    "MP": "millet-partisi-1962",
    "MHP": "cumhuriyetci-koylu-millet-partisi",
    "CKMP": "cumhuriyetci-koylu-millet-partisi",
    "TİP": "turkiye-isci-partisi-1961",
    "YTP": "yeni-turkiye-partisi-1961",
    "MSP": "milli-selamet-partisi",
    "TBP": "turkiye-birlik-partisi",
    "Bağımsızlar": None,
    "Bağımsız": None,
}
CONTESTS_1973 = {
    "il_genel": "provincial-councils",
    "belediye_baskani": "mayors",
    "belediye_meclisi": "municipal-councils",
}
METHOD_1973 = (
    "From the State Institute of Statistics' book of the 9 December 1973 local elections, which prints the "
    "consolidation reports of the provincial and ilçe election boards unchanged, so party votes do not always add "
    "up to the valid votes. The book's text layer and a second optical reading were checked cell by cell against "
    "each party's printed share of the valid votes; a count that could not be confirmed that way is left out."
)
METHOD_1963 = (
    "From the State Institute of Statistics' book of the 17 November 1963 local elections, which prints the "
    "provincial and ilçe election boards' counts without shares, so party votes do not always add up to the valid "
    "votes. Every count was read from the page image by optical recognition, three to six times, and a row was "
    "taken as read where the readings agree or where exactly one combination of them makes the party votes add up "
    "to the valid votes; a count whose readings disagree is left out. The book's errata are applied."
)
UNREAD_1963 = (
    "Some party counts in this row of the book could not be read with confidence, so they are left out and the "
    "stored party votes do not add up to the valid votes. Counts left out: {labels}."
)
GAP_1963 = (
    "The party votes in this row of the book do not add up to the valid votes it prints, so both are stored as "
    "printed. Party votes {votes:,}, valid votes {valid:,}."
)
UNREAD_1973 = (
    "Some counts in this row of the book could not be read with confidence ({labels}), so they are left out and "
    "the stored party votes do not add up to the valid votes."
)
NO_ACCOUNTING_1973 = (
    "The registered voters, voters, and valid votes in this row of the book could not be read with confidence."
)
WINNER_PARTY_1973 = (
    "The book names {mayor} as the elected mayor, but its party column for the mayor could not be read, so no "
    "outcome is recorded for this contest."
)
INDEPENDENTS_1973 = (
    "The book names {mayor}, an independent, as the elected mayor. The independents' column sums every independent "
    "candidate in the municipality, so the mayor's own votes are unknown and no outcome is recorded."
)
GAP_1973 = (
    "The party votes stored for this row add up to {votes:,} against the {valid:,} valid votes the book prints; "
    "the difference is a count that could not be read or confirmed, or a misprint in the book."
)
UNREAD_WINNER_1973 = (
    "The book names {mayor} ({party}) as the elected mayor, but a party's count in this row could not be read, so "
    "the mayor's lead cannot be confirmed and no outcome is recorded."
)
SUSPECT_1973 = (
    "The book names {mayor} ({party}) as the elected mayor, but another list has more votes in this row, so the "
    "printed name, party, or a count is wrong, and no outcome is recorded."
)


STRAYS = {
    (1963, "34", "Küçükköy"): "Eyüp",
    (1973, "34", "Avcdar"): "Bakırköy",
    (1973, "34", "Kocasinan"): "Bakırköy",
    (1973, "34", "Safra"): "Bakırköy",
    (1973, "34", "Yenibosna"): "Bakırköy",
    (1973, "34", "Alibeyköy"): "Eyüp",
    (1973, "34", "Kemerburgaz"): "Eyüp",
    (1973, "34", "Sağmalcılar"): "Eyüp",
    (1973, "34", "Küçükköy"): "Gaziosmanpaşa",
    (1973, "34", "Ümıaniye"): "Üsküdar",
}


def emit() -> Path:
    parsed_rows = pl.read_parquet(PARSED / "rows.parquet")
    parsed_values = pl.read_parquet(PARSED / "values.parquet")
    parsed_mayors = pl.read_parquet(PARSED / "mayors.parquet")
    carved: dict[tuple[str, str], set[tuple[str, str]]] = {}
    primary: dict[tuple[str, str], tuple[str, str]] = {}
    firsts: dict[tuple[str, str], date] = {}
    for row in pl.read_csv(Path(__file__).with_name("tkgm_parents.tsv"), separator="\t", infer_schema=False).iter_rows(
        named=True
    ):
        firsts[row["plate"], norm(row["ilce"])] = date.fromisoformat(row["first"])
        primary[row["plate"], norm(row["ilce"])] = (row["parent_plate"], row["parent"])
        for parent_plate, parent in [
            (row["parent_plate"], row["parent"]),
            *(other.split(":") for other in (row["also"] or "").split(";") if other),
        ]:
            carved.setdefault((row["plate"], norm(row["ilce"])), set()).add((parent_plate, norm(parent)))
    measures = {"registered": REGISTERED, "voters": VOTERS, "valid": VALID}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 17 November 1963 and 9 December 1973 local elections by province, ilçe, and "
            "municipality, with the 1973 mayors' names, from the State Institute of Statistics",
            polity="TR",
        )
        turkey = cs.polity("TR")
        pairs = list(
            pl.read_csv(
                Path(__file__).with_name("die_local_renamed.tsv"), separator="\t", infer_schema=False
            ).iter_rows()
        )
        for old, new in pairs:
            cs.renamed[new] = old
            cs.renamed[f"{new[:4]}:{new[11:]}"] = f"{old[:4]}:{old[11:]}"

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        equals = dict(
            conn.execute(
                "select area_id, id from reporting_unit where scheme_code = 'tr.ilce' and area_relation = 'equals'"
            ).fetchall()
        )
        matched: list[str] = []
        minted_ilce: list[str] = []
        areas = {unit: area for area, unit in equals.items()}
        polities = {value for (value,) in conn.execute("select value from external_id where scheme = 'tr.polity'")}
        settled = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join changeset c on c.id = x.changeset_id
                   where x.scheme = 'tr.polity' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        offices: set[UUID] = set()
        known_units = {
            (scheme, code)
            for scheme, code in conn.execute(
                "select scheme_code, code from reporting_unit where scheme_code in ('tr.ilce', 'tr.belde')"
            ).fetchall()
        }
        mayoral = cs.key("tr.electoral_system", "mayor")
        parties: dict[str, UUID] = {}
        party_names: dict[str, str] = {}
        for label, key in KEYS.items():
            if key is not None:
                parties[label] = cs.key("tr.party", key)
                if parties[label] in cs.minted:
                    raise ValueError(f"tr.party {key} is not in the archive")
                party_names[label] = conn.execute(
                    """select n.text from external_id x join name n on n.entity_id = x.entity_id and n.kind = 'official'
                       where x.scheme = 'tr.party' and x.value = %s order by n.valid_from desc nulls last limit 1""",
                    [key],
                ).fetchone()[0]
        cells: list[dict] = []
        located: list[dict] = []
        seen: set[tuple[int, str, str]] = set()

        def load(year: int, day: date, title_text: str, method: str) -> None:
            rows = parsed_rows.filter(pl.col("year") == year)
            values = parsed_values.filter(pl.col("year") == year)
            mayors = {row["seq"]: row for row in parsed_mayors.filter(pl.col("year") == year).iter_rows(named=True)}
            book = raw.cached(SOURCE, url(year))
            source = cs.source(PUBLISHER, title_text)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title_text,
                url=url(year),
                license=LICENSE,
            )
            artifact = cs.artifact(book, source)
            page_ids = {}
            for number in sorted({*values["page"].unique().to_list(), *(row["page"] for row in mayors.values())}):
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            str(book.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page_ids[number] = cs.page(artifact, number)
                cs.add(
                    "source_page",
                    id=page_ids[number],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )

            named_areas: dict[str, dict[str, set[UUID]]] = {}
            current_names: dict[str, dict[str, set[UUID]]] = {}
            unfounded: dict[tuple[str, str], str] = {}
            kin: dict[str, set[str]] = {}
            ilce_names = conn.execute(
                """select a.id, n.text, n.valid_from, n.valid_to, pc.code from area a
                   join name n on n.entity_id = a.id and n.lang = 'tr'
                   join area_parent ap on ap.area_id = a.id
                   join area p on p.id = ap.parent_id and p.scheme_code = 'tr.il'
                   join area_code pc on pc.area_id = p.id
                   where a.scheme_code = 'tr.ilce'"""
            ).fetchall()
            founding = {
                area: firsts[plate_code, norm(text)]
                for area, text, _, _, plate_code in ilce_names
                if (plate_code, norm(text)) in firsts
            }
            plates_of: dict[UUID, set[str]] = {}
            for area, _, _, _, plate_code in ilce_names:
                plates_of.setdefault(area, set()).add(plate_code)
            for plates in plates_of.values():
                for plate_code in plates:
                    kin.setdefault(plate_code, set()).update(plates)
            for area, text, start, end, plate_code in ilce_names:
                named_areas.setdefault(plate_code, {}).setdefault(norm(text), set()).add(area)
                founded = founding.get(area, day) <= day
                if not founded:
                    unfounded[plate_code, norm(text)] = slug(text)
                if founded and (start is None or start <= day) and (end is None or end > day):
                    current_names.setdefault(plate_code, {}).setdefault(norm(text), set()).add(area)
            area_names: dict[UUID, set[str]] = {}
            for by_name in current_names.values():
                for form, found in by_name.items():
                    for area in found:
                        area_names.setdefault(area, set()).add(form)

            def lineage(start: tuple[str, str]) -> set[str]:
                seen_keys, frontier = set(), {start}
                while frontier:
                    seen_keys |= frontier
                    frontier = {up for at in frontier for up in carved.get(at, set())} - seen_keys
                return {form for _, form in seen_keys}

            resolved: dict[tuple[str, str], tuple[UUID, str]] = {}

            def trims(label: str) -> list[str]:
                words = label.split()
                out = []
                for start in range(len(words)):
                    for end in range(len(words), start, -1):
                        out.append(" ".join(words[start:end]))
                return out

            def variants(text: str) -> list[str]:
                out = [SPELLINGS_1973.get(text, text)]
                for old, replacement in (("ı", "r"), (" ", ""), ("Ş. ", "Şerefli"), ("M. ", "Mustafa")):
                    out += [v.replace(old, replacement) for v in out if old in v]
                out += [v[0].replace("I", "İ") + v[1:] for v in out if v.startswith("I")]
                out += [v[0].replace("İ", "I") + v[1:] for v in out if v.startswith("İ")]
                out += [v.replace("Ş. Karahisar", "Şebinkarahisar") for v in out if "Ş. Karahisar" in v]
                return list(dict.fromkeys(out))

            def lookup(plate: str, label: str, alias: str | None, strict: bool = False) -> tuple[UUID, str] | None:
                later = [f"{n:02}" for n in range(68, 82)]
                tables = (current_names,) if strict else (current_names, named_areas)
                texts = [*trims(label), *([alias] if alias else [])]
                for allowed in ([plate], later):
                    for text in texts:
                        for variant in variants(text):
                            form = norm(variant)
                            if form == "MERKEZ":
                                form = norm(f"{PROVINCES[int(plate) - 1]} Merkez")
                            form = ILCE_ALIASES.get((plate, form), form)
                            for table_ in tables:
                                for candidate in (form, f"{form} MERKEZ"):
                                    found = {a for code in allowed for a in table_.get(code, {}).get(candidate, set())}
                                    if len(found) == 1:
                                        return next(iter(found)), text
                for table_ in tables:
                    for text in trims(label):
                        if year == 1963 and any(
                            norm(variant).startswith(norm(name))
                            for variant in variants(text)
                            for code, name in NOT_ILCE
                            if code == plate
                        ):
                            continue
                        best: dict[tuple[str, str], float] = {}
                        for variant in variants(text):
                            for code in [plate, *later]:
                                for other in table_.get(code, {}):
                                    ratio = difflib.SequenceMatcher(None, norm(variant), other).ratio()
                                    best[(other, code)] = max(ratio, best.get((other, code), 0))
                        scored = sorted((ratio, other, code) for (other, code), ratio in best.items())
                        if (
                            scored
                            and scored[-1][0] >= (0.85 if strict else 0.8)
                            and (len(scored) < 2 or scored[-2][0] < scored[-1][0])
                        ):
                            found = table_[scored[-1][2]][scored[-1][1]]
                            if len(found) == 1:
                                matched.append(f"{plate} {label} -> {scored[-1][1]}")
                                return next(iter(found)), text
                return None

            def ilce(plate: str, label: str, alias: str | None) -> UUID:
                if (plate, label) not in resolved:
                    hit = lookup(plate, label, alias)
                    if hit is None:
                        code = f"{plate}:{title(label)}"
                        unit = cs.unit("tr.ilce", code)
                        if unit in cs.minted and code not in minted_ilce:
                            minted_ilce.append(code)
                            cs.add(
                                "reporting_unit",
                                id=unit,
                                polity_id=turkey,
                                scheme_code="tr.ilce",
                                code=code,
                                parent_id=None,
                                area_relation="within",
                                area_id=cs.area("tr.il", plate),
                                kind="aggregate",
                            )
                            name(unit, title(label))
                        resolved[(plate, label)] = (unit, label)
                    else:
                        area, text = hit
                        if area not in equals:
                            code = f"{plate}:{title(text)}"
                            equals[area] = cs.unit("tr.ilce", code)
                            cs.add(
                                "reporting_unit",
                                id=equals[area],
                                polity_id=turkey,
                                scheme_code="tr.ilce",
                                code=code,
                                parent_id=None,
                                area_relation="equals",
                                area_id=area,
                                kind="aggregate",
                            )
                            name(equals[area], title(text))
                        resolved[(plate, label)] = (equals[area], text)
                return resolved[(plate, label)][0]

            local = cs.key("tr.election", f"{day}:local")
            if {mayoral, local} & cs.minted:
                raise ValueError("publish tr.ysk_local before tr.die_local")

            def put(result_set: UUID, unit: UUID, option: UUID | None, measure: int, row: dict) -> None:
                fact = {
                    "result_set_id": str(result_set),
                    "unit_id": str(unit),
                    "option_id": None if option is None else str(option),
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                }
                cells.append({**fact, "flags": DIGITIZED, "value": row["value"]})
                located.append(
                    {
                        **fact,
                        "page_id": str(page_ids[row["page"]]),
                        "x0": row["x0"],
                        "y0": row["y0"],
                        "x1": row["x1"],
                        "y1": row["y1"],
                    }
                )

            def option_of(contest_key: str, label: str) -> UUID:
                key = KEYS[label]
                option = cs.key("tr.option", f"{contest_key}:{key or 'independents'}")
                if option in cs.minted:
                    raise ValueError(f"{contest_key}: no {label} option")
                return option

            def shortfall(stored: dict) -> tuple[int, int] | None:
                valid = next((cell["value"] for column, cell in stored.items() if measures.get(column) == VALID), None)
                votes = sum(cell["value"] for column, cell in stored.items() if column not in measures)
                return None if valid is None or votes == valid else (int(votes), int(valid))

            grouped = {(table, seq): frame for (table, seq), frame in values.group_by("table", "seq")}

            def mayor_contest(
                row: dict, stored: dict, parts: list[str], english: str, plate: str, unit: UUID, district: str
            ) -> None:
                key = "-".join(parts)
                path = "tr/" + "/".join(parts)
                polity = cs.key("tr.polity", key)
                office = cs.key("tr.office", f"{key}-mayor")
                if key not in polities:
                    polities.add(key)
                    cs.add("polity", id=polity, kind="municipality", iso_code=None)
                    name(polity, f"{english} Municipality", "en")
                    name(polity, f"{english} Belediyesi", kind="native")
                    cs.add("polity_parent", polity_id=polity, parent_id=turkey)
                    cs.add("slug", path=path, entity_id=polity)
                if office in cs.minted and office not in offices:
                    offices.add(office)
                    cs.add("office", id=office, polity_id=polity, kind="mayor")
                    name(office, f"Mayor of {english}", "en")
                    name(office, f"{english} Belediye Başkanı", kind="native")
                election = cs.key("tr.election", f"{day}:{key}-mayor")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=local,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {english} mayoral election", "en")
                name(election, f"{year} {english} Belediye Başkanlığı Seçimi", kind="native")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                contest_key = f"{day}:{key}-mayor"
                contest = cs.key("tr.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=areas.get(ilce(plate, district, None)) or cs.area("tr.il", plate),
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=mayoral,
                    default_result_kind="counted",
                )
                name(contest, f"Mayor of {english}", "en")
                cs.add("slug", path=f"{path}/{day}-local/mayor", entity_id=contest)
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=declared, kind="methodology", body_md=method)
                if row["unresolved"]:
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=(UNREAD_1963 if year == 1963 else UNREAD_1973).format(
                            labels=", ".join(row["unresolved"])
                        ),
                    )
                if not row["accounting"]:
                    cs.add("note", entity_id=declared, kind="caveat", body_md=NO_ACCOUNTING_1973)
                elif not row["unresolved"] and (gap := shortfall(stored)) is not None:
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=(GAP_1963 if year == 1963 else GAP_1973).format(votes=gap[0], valid=gap[1]),
                    )
                options: dict[str, UUID] = {}

                def option(label: str) -> UUID:
                    owner = KEYS[label]
                    tag = "independents" if owner is None else owner
                    if tag not in options:
                        options[tag] = cs.key("tr.option", f"{contest_key}:{tag}")
                        cs.add(
                            "contest_option",
                            id=options[tag],
                            contest_id=contest,
                            kind="independents" if owner is None else "candidate",
                            party_id=None if owner is None else parties[label],
                            ballot_label="Bağımsızlar" if owner is None else party_names[label],
                        )
                    return options[tag]

                for column, cell in stored.items():
                    if column in measures:
                        put(declared, unit, None, measures[column], cell)
                    else:
                        put(declared, unit, option(column), VOTES, cell)
                winner = mayors.get(row["seq"])
                if winner is None:
                    return
                label = winner["party"]
                person_name, _, _ = tidy(winner["mayor"])
                if label is None:
                    cs.add(
                        "note", entity_id=declared, kind="caveat", body_md=WINNER_PARTY_1973.format(mayor=person_name)
                    )
                    return
                if label not in KEYS:
                    raise ValueError(f"{contest_key}: unknown party {label}")
                votes = {column: cell["value"] for column, cell in stored.items() if column not in measures}
                listed = sorted((value for column, value in votes.items() if KEYS[column] is not None), reverse=True)
                independent = KEYS[label] is None
                unread = not independent and (label not in votes or any(column in KEYS for column in row["unresolved"]))
                suspect = (
                    not independent
                    and not unread
                    and (votes[label] != listed[0] or (len(listed) > 1 and listed[0] == listed[1]))
                )
                if independent:
                    cs.add(
                        "note", entity_id=declared, kind="caveat", body_md=INDEPENDENTS_1973.format(mayor=person_name)
                    )
                elif unread:
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=UNREAD_WINNER_1973.format(mayor=person_name, party=party_names[label]),
                    )
                elif suspect:
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=SUSPECT_1973.format(mayor=person_name, party=party_names[label]),
                    )
                person = cs.key(f"{SOURCE}.person", f"{year}:{key}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, person_name)
                candidacy = cs.key("tr.candidacy", f"{contest_key}:1")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option(label),
                    role="head",
                    list_position=None,
                    ballot_name=None,
                    member_of_party_id=None,
                    is_independent=independent,
                )
                if not independent:
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[label], kind="nominated")
                if not independent and not unread and not suspect:
                    cs.add(
                        "outcome",
                        result_set_id=declared,
                        option_id=option(label),
                        candidacy_id=candidacy,
                        kind="elected",
                        seats=None,
                    )
                cs.add(
                    "citation",
                    entity_id=candidacy,
                    source_id=source,
                    locator={
                        "page_id": str(page_ids[winner["page"]]),
                        "bbox": [winner["x0"], winner["y0"], winner["x1"], winner["y1"]],
                    },
                )

            for table, contest_code in CONTESTS_1973.items():
                contest_key = f"{day}:{contest_code}"
                contest = cs.key("tr.contest", contest_key)
                if contest in cs.minted:
                    raise ValueError(f"{contest_key} is not in the archive")
                sets = {}
                for level in ("province", "district" if table == "il_genel" else "municipality"):
                    sets[level] = cs.key(f"{SOURCE}.result_set", f"{contest_key}:by-{level}")
                    cs.add(
                        "result_set",
                        id=sets[level],
                        election_id=local,
                        contest_id=contest,
                        stage="alternative",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=False,
                        completeness=1,
                    )
                    cs.add("note", entity_id=sets[level], kind="methodology", body_md=method)
                unread = {"province": [], "district": [], "municipality": []}
                districts: dict[str, set[str]] = {}
                for plate_number, district_name in (
                    rows.filter(pl.col("table") == table, pl.col("level") == "district")
                    .select("plate", "name")
                    .iter_rows()
                ):
                    districts.setdefault(f"{plate_number:02}", set()).add(district_name)
                current: dict[str, str] = {}
                listed = rows.filter(pl.col("table") == table).sort("seq")
                if table != "il_genel":
                    order: dict[tuple[int, str], list[str]] = {}
                    for plate_number, text, level in (
                        rows.filter(pl.col("table") == "belediye_baskani")
                        .sort("seq")
                        .select("plate", "name", "level")
                        .iter_rows()
                    ):
                        order.setdefault((plate_number, text), []).append(level)
                    seen_names: dict[tuple[int, str], int] = {}
                    printed = {norm(v) for text in listed["name"].unique() for v in variants(text)}
                    levels = []
                    for plate_number, text, level, bucak in listed.select(
                        "plate", "name", "level", "bucak"
                    ).iter_rows():
                        index = seen_names.get((plate_number, text), 0)
                        seen_names[(plate_number, text)] = index + 1
                        if level in ("province", "national"):
                            levels.append(level)
                        elif len(order.get((plate_number, text), [])) > 1:
                            levels.append(order[(plate_number, text)][min(index, len(order[(plate_number, text)]) - 1)])
                        else:
                            before = len(matched)
                            hit = None if bucak else lookup(f"{plate_number:02}", text, None, strict=True)
                            names = set() if hit is None else area_names.get(hit[0], set())
                            fuzzy = len(matched) > before
                            levels.append("belde" if hit is None or fuzzy and printed & names else "district")
                    listed = listed.with_columns(level=pl.Series(levels))
                for row in listed.iter_rows(named=True):
                    if row["level"] == "national":
                        continue
                    if all(len(word) == 1 for word in row["name"].split()):
                        row = {**row, "name": "".join(row["name"].split())}
                    if (
                        sum(len(word) == 1 for word in row["name"].split()) >= 2
                        or re.search(r"Dağılım|Listesindeki|lçelere", row["name"])
                        or (
                            row["level"] != "province"
                            and row["name"] != "Of"
                            and not any(len(word) >= 3 for word in row["name"].split())
                        )
                    ):
                        print(f"skipped {table} {row['plate']} {row['printed']}")
                        continue
                    plate = f"{row['plate']:02}"
                    province = PROVINCES[row["plate"] - 1]
                    province_slug = slug(province)
                    frame = grouped.get((table, row["seq"]))
                    if frame is None:
                        if row["level"] == "district" and table != "il_genel":
                            ilce(plate, row["name"], row["alias"])
                            current[plate] = resolved[(plate, row["name"])][1]
                        continue
                    if row["level"] == "province":
                        level, unit = "province", cs.unit("tr.il", plate)
                        place = province
                    elif table == "il_genel":
                        level, unit, place = "district", ilce(plate, row["name"], row["alias"]), row["name"]
                    else:
                        level = "municipality"
                        if row["level"] == "district":
                            ilce(plate, row["name"], row["alias"])
                            label = resolved[(plate, row["name"])][1]
                            current[plate] = label
                            central = label == "Merkez"
                            parts = [province_slug] if central else [province_slug, slug(label)]
                            english = province if central else label
                            scheme, code = "tr.ilce", "-".join(parts)
                            district = label
                        else:
                            district = current.get(plate, "Merkez")
                            words = row["name"].split()
                            while len(words) > 1 and (
                                len(words[-1]) <= 2 or ")" in words[-1] or any(c.isdigit() for c in words[-1])
                            ):
                                words.pop()
                            while len(words) > 1 and (len(words[0]) <= 2 or any(c.isdigit() for c in words[0])):
                                words.pop(0)
                            row = {**row, "name": " ".join(words)}
                            within = area_names.get(areas.get(ilce(plate, district, None)), set())
                            reach = {plate} | {other for other in kin.get(plate, set()) if other >= "68"}
                            itself = [
                                (other, norm(row["name"]))
                                for other in sorted(reach)
                                if (other, norm(row["name"])) in primary and (other, norm(row["name"])) in unfounded
                            ]
                            if (year, plate, row["name"]) in STRAYS:
                                district = STRAYS[year, plate, row["name"]]
                            elif itself and not lineage(itself[0]) & within:
                                up = primary[itself[0]]
                                while (up[0], norm(up[1])) in primary and (up[0], norm(up[1])) in unfounded:
                                    up = primary[up[0], norm(up[1])]
                                label = up[1].removesuffix(" Merkez")
                                target = areas.get(ilce(plate, label, None))
                                printed = sorted(
                                    text
                                    for (at, other), (unit_id, text) in resolved.items()
                                    if at == plate
                                    and other in districts.get(plate, set())
                                    and areas.get(unit_id) == target
                                )
                                district = "Merkez" if label == province else printed[0] if printed else label
                                within = area_names.get(areas.get(ilce(plate, district, None)), set())
                            options = [
                                [province_slug, "merkez" if district == "Merkez" else slug(district), slug(row["name"])]
                            ]
                            if row["name"] not in districts.get(plate, set()):
                                options.append([province_slug, slug(row["name"])])
                            if row["alias"]:
                                options.append(
                                    [
                                        province_slug,
                                        "merkez" if district == "Merkez" else slug(district),
                                        slug(row["alias"]),
                                    ]
                                )
                            moved = [
                                [province_slug, later, slug(row["name"])]
                                for at, later in sorted(unfounded.items())
                                if at[0] in reach
                                and f"{province_slug}-{later}-{slug(row['name'])}" in settled
                                and lineage(at) & within
                            ]
                            parts = (
                                moved[0]
                                if len(moved) == 1
                                else next((o for o in options if "-".join(o) in polities), options[0])
                            )
                            english = row["name"]
                            scheme, code = (
                                "tr.belde",
                                "-".join(parts if len(parts) == 3 and parts in options else options[0]),
                            )
                        key = "-".join(parts)
                        if (year, table, key) in seen:
                            raise ValueError(f"two {year} municipalities share {key}")
                        seen.add((year, table, key))
                        unit = cs.unit(scheme, code)
                        if (scheme, code) not in known_units:
                            known_units.add((scheme, code))
                            area = areas.get(ilce(plate, district, None)) or cs.area("tr.il", plate)
                            cs.add(
                                "reporting_unit",
                                id=unit,
                                polity_id=turkey,
                                scheme_code=scheme,
                                code=code,
                                area_relation="within",
                                area_id=area,
                                kind="aggregate",
                            )
                            name(unit, english)
                        place = english
                    stored = {r["column"]: r for r in frame.iter_rows(named=True)}
                    if row["unresolved"]:
                        unread[level].append(f"{place} ({', '.join(row['unresolved'])})")
                    if not row["accounting"]:
                        unread[level].append(f"{place} (registered, voters, valid)")
                    elif not row["unresolved"] and (gap := shortfall(stored)) is not None:
                        unread[level].append(f"{place} (party votes {gap[0]:,} against {gap[1]:,} valid)")
                    for column, cell in stored.items():
                        if column in measures:
                            put(sets[level], unit, None, measures[column], cell)
                        else:
                            put(sets[level], unit, option_of(contest_key, column), VOTES, cell)
                    if table == "belediye_baskani" and level == "municipality":
                        mayor_contest(row, stored, parts, english, plate, unit, district)
                for level, missing in unread.items():
                    if missing and level in sets:
                        cs.add(
                            "note",
                            entity_id=sets[level],
                            kind="caveat",
                            body_md="Some counts in the book could not be read with confidence and are left out, or the "
                            + "party votes do not add up to the valid votes: "
                            + "; ".join(missing)
                            + ".",
                        )

        load(1963, DAY_1963, TITLE_1963, METHOD_1963)
        load(1973, DAY_1973, TITLE_1973, METHOD_1973)
        print("fuzzy:", *matched, sep="\n  ")
        print("new ilçe units:", *minted_ilce, sep="\n  ")
        keys = ["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round"]
        facts = pl.DataFrame(cells, infer_schema_length=None)
        if facts.select(keys).is_duplicated().any():
            raise ValueError(f"duplicate cells: {facts.filter(facts.select(keys).is_duplicated()).head(5)}")
        cs.frame(
            "result_value", facts.with_columns(pl.col("channel", "measure", "count_round", "flags").cast(pl.Int16))
        )
        cs.frame(
            "value_locator",
            pl.DataFrame(located, infer_schema_length=None).with_columns(
                pl.col("channel", "measure", "count_round").cast(pl.Int16), source_id=pl.lit(None, pl.String)
            ),
        )
        staged = {table: {str(row["id"]) for row in cs.rows.get(table, [])} for table in ("contest", "election")}
        holders = {str(row["polity_id"]) for row in cs.rows.get("election", [])}
        mine = dict(
            conn.execute(
                """select x.value, x.entity_id::text from external_id x join changeset c on c.id = x.changeset_id
                   where x.scheme = 'tr.polity' and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        for old, new in pairs:
            before, after = old[11:], new[11:]
            if before != after and before in mine and mine[before] not in holders:
                cs.merge(cs.key("tr.polity", before), cs.key("tr.polity", after))
                cs.merge(cs.key("tr.office", f"{before}-mayor"), cs.key("tr.office", f"{after}-mayor"))
        reason = "A duplicate of a 1963 or 1973 municipal race, left behind when the archive re-read the race's town."
        for contest, election in conn.execute(
            """select c.id::text, c.election_id::text from contest c join changeset ch on ch.id = c.changeset_id
               where ch.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if contest not in staged["contest"]:
                cs.add("entity_retirement", entity_id=contest, reason=reason)
                if election not in staged["election"]:
                    cs.add("entity_retirement", entity_id=election, reason=reason)
                for (person,) in conn.execute(
                    """select distinct cy.person_id::text from candidacy cy join contest_option o on o.id = cy.option_id
                       join external_id x on x.entity_id = cy.person_id and x.scheme = %s
                       where o.contest_id = %s and not exists (
                         select from candidacy other join contest_option oo on oo.id = other.option_id
                         where other.person_id = cy.person_id and oo.contest_id <> %s)""",
                    [f"{SOURCE}.person", contest, contest],
                ).fetchall():
                    cs.add("entity_retirement", entity_id=person, reason=reason)
        used = set(facts["unit_id"].unique())
        for (unit,) in conn.execute(
            """select ru.id::text from reporting_unit ru join changeset ch on ch.id = ru.changeset_id
               where ch.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if unit not in used:
                cs.add(
                    "entity_retirement",
                    entity_id=unit,
                    reason="A 1963 or 1973 reporting unit the archive no longer uses, after re-reading which ilçe each town was in.",
                )
        return cs.write()
