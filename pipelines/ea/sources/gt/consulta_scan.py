"""The 1994 popular consultation of Guatemala, read by OCR from the scanned memoria.

The memoria is a scan with no text layer. It prints a card for each municipality: its electoral board, its registered
voters, and a block of results (yes, no, the valid votes, the null votes, the blank votes, and the total votes).

A page is straightened and read whole with Tesseract, which gives each word with its box. A card's figures are kept
only when the arithmetic the memoria prints holds: yes and no make the valid votes, which with the null and blank
votes make the votes cast, and the registered literate and illiterate voters make the registered voters. A figure
that does not hold is read again from its own crop, in several ways, and a municipality whose figures still do not
hold is left out.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

from ea.sources.gt import consulta as main
from ea.sources.gt import memoria as base
from ea.sources.gt import memoria_scan as ms
from ea.store import DATA

os.environ.setdefault("OMP_THREAD_LIMIT", "1")
CACHE = DATA / "ocr" / main.SOURCE
DPI = 300
VERSION = 1


class Token(NamedTuple):
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def yc(self) -> float:
        return self.top + self.height / 2


class Sheet(NamedTuple):
    page: int
    angle: float
    width: float
    height: float
    """The printed page's size in points."""
    pixels: tuple[int, int]
    tokens: list[Token]


def _tsv(image: Image.Image, psm: int = 11) -> list[Token]:
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "page.png"
        image.save(file)
        out = subprocess.run(
            ["tesseract", str(file), "stdout", "--psm", str(psm), "-l", "eng", "tsv"],
            capture_output=True,
            check=True,
        ).stdout.decode()
    found: list[Token] = []
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or not parts[11].strip():
            continue
        found.append(
            Token(parts[11].strip(), int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]), float(parts[10]))
        )
    return found


def sheet(year: int, page: int) -> Sheet:
    """A page's words, from the cache or read now."""
    file = CACHE / str(year) / f"{page}.json"
    if file.exists():
        d = json.loads(file.read_text())
        if d["version"] == VERSION:
            return Sheet(
                page, d["angle"], d["width"], d["height"], tuple(d["pixels"]), [Token(*t) for t in d["tokens"]]
            )
    pdf = main.path(year)
    straight = ms.straightened(pdf, page, DPI)
    tokens = _tsv(straight.image)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(
            {
                "version": VERSION,
                "angle": straight.angle,
                "width": straight.width,
                "height": straight.height,
                "pixels": [straight.image.width, straight.image.height],
                "tokens": [list(t) for t in tokens],
            }
        )
    )
    return Sheet(
        page, straight.angle, straight.width, straight.height, (straight.image.width, straight.image.height), tokens
    )


def _warm(args: tuple[int, int]) -> int:
    sheet(*args)
    return args[1]


def warm(year: int, pages: Iterable[int], workers: int = 4) -> None:
    with ProcessPoolExecutor(workers) as pool:
        for done in pool.map(_warm, [(year, p) for p in pages]):
            print(done, end=" ", flush=True)


def box(s: Sheet, t: Token) -> tuple[float, float, float, float]:
    """A token's box in points on the printed page."""
    import math

    cx, cy = s.pixels[0] / 2, s.pixels[1] / 2
    a = math.radians(s.angle)
    xs, ys = [], []
    for x, y in ((t.left, t.top), (t.right, t.top), (t.left, t.bottom), (t.right, t.bottom)):
        dx, dy = x - cx, y - cy
        xs.append(cx + dx * math.cos(a) - dy * math.sin(a))
        ys.append(cy + dx * math.sin(a) + dy * math.cos(a))
    scale = 72 / DPI
    return (
        round(max(min(xs), 0) * scale, 1),
        round(max(min(ys), 0) * scale, 1),
        round(min(max(xs) * scale, s.width), 1),
        round(min(max(ys) * scale, s.height), 1),
    )


def word(s: Sheet, t: Token, value: int) -> base.Word:
    b = box(s, t)
    return base.Word(s.page, *b, str(value), b)


def digits(text: str) -> int | None:
    found = re.sub(r"\D", "", text)
    return int(found) if found else None


def is_figure(t: Token) -> bool:
    return re.fullmatch(r"[\d.,:;'\"|!]*\d[\d.,:;'\"|!]*", t.text) is not None


if __name__ == "__main__" and len(sys.argv) > 3:  # pragma: no cover
    warm(int(sys.argv[1]), range(int(sys.argv[2]), int(sys.argv[3]) + 1), int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    sys.exit(0)


class Card(NamedTuple):
    kind: str
    """M for a municipality, D for a department, X for the nation and the Distrito Central."""
    sheet: Sheet
    anchor: Token
    results: list[Token]
    """The figures of the results block, top to bottom."""
    roll: list[Token]
    """The figures of the registered voters' row, left to right."""


def _is_results(t: Token) -> bool:
    text = t.text.upper()
    return t.left > 1300 and (text.startswith(("RESU", "RESD", "RES")) or "SULT" in text) and len(text) >= 3


def cards(year: int, page: int) -> list[Card]:
    s = sheet(year, page)
    anchors = sorted((t for t in s.tokens if _is_results(t)), key=lambda t: t.top)
    # Merge anchors read twice for one block.
    merged: list[Token] = []
    for t in anchors:
        if merged and t.top - merged[-1].top < 150:
            continue
        merged.append(t)
    headings = sorted(
        (t for t in s.tokens if t.left < 1000 and t.text.upper().startswith(("MUNIC", "DEPARTAM")) and t.conf > 30),
        key=lambda t: t.top,
    )
    for h in headings:
        if h.text.upper().startswith("DEPARTAMENTAL"):
            continue
        if not any(0 < a.top - h.top < 600 for a in merged):
            merged.append(Token("RESULTADOS", 1700, h.top + 380, 280, 40, 0.0))
    merged.sort(key=lambda t: t.top)
    found: list[Card] = []
    for a in merged:
        window = [t for t in s.tokens if t.left < 1000 and a.top - 470 < t.top < a.top - 120 and t.conf > 25]
        kinds = [t.text.upper() for t in window]
        kind = (
            "D"
            if any(k.startswith("DEPARTAM") and not k.startswith("DEPARTAMENTAL") for k in kinds)
            else "M"
            if any(k.startswith("MUNICIP") and not k.startswith("MUNICIPAL") for k in kinds)
            else "X"
        )
        results = sorted(
            (t for t in s.tokens if t.left > 1950 and a.top + 40 < t.top < a.top + 520 and is_figure(t)),
            key=lambda t: t.top,
        )
        roll = sorted(
            (t for t in s.tokens if t.left < 1950 and a.top + 380 < t.top < a.top + 560 and is_figure(t)),
            key=lambda t: t.left,
        )
        found.append(Card(kind, s, a, results, roll))
    return found


# Reading a figure from its own crop ---------------------------------------------------------------------------------


class Reading(NamedTuple):
    value: int
    box: tuple[int, int, int, int]
    """The figure's box on the straightened page, in pixels."""
    votes: int
    """How many readings gave this value."""


def _image(year: int, page: int) -> Image.Image:
    return ms.straightened(main.path(year), page, DPI).image


def ocr(image: Image.Image, psm: int, scale: int) -> str:
    try:
        return re.sub(r"\D", "", ms.tesseract(image, psm, scale, ms.DIGITS))
    except subprocess.CalledProcessError:
        return ""


def ocr_cell(image: Image.Image, tight: tuple[int, int, int, int]) -> list[str]:
    crop = image.crop((tight[0] - 3, tight[1] - 3, tight[2] + 3, tight[3] + 3))
    return [ocr(crop, psm, scale) for psm, scale in ms.PASSES]


def crop_readings(image: Image.Image, area: tuple[int, int, int, int]) -> list[Reading]:
    """What a crop of the page reads as one figure, in several ways, by value with its tight box."""

    gray = np.asarray(image)
    counts: dict[int, int] = {}
    boxes: dict[int, tuple[int, int, int, int]] = {}
    first: tuple[int, int, int, int] | None = None
    for threshold in (150, 120, 180):
        tight, _ = ms.glyphs(gray < threshold, area)
        if tight is None:
            continue
        first = first or tight
        padded = image.crop((tight[0] - 6, tight[1] - 6, tight[2] + 6, tight[3] + 6))
        texts = ocr_cell(image, tight) if threshold == 150 else []
        texts += [ocr(padded, psm, scale) for psm, scale in ((7, 3), (8, 3), (7, 4))]
        for text in texts:
            if text:
                counts[int(text)] = counts.get(int(text), 0) + 1
                boxes.setdefault(int(text), tight)
    if first is None:
        return []
    for psm in (7, 13):
        text = ocr(image.crop(area), psm, 2)
        if text and len(text) <= 7:
            counts[int(text)] = counts.get(int(text), 0) + 1
            boxes.setdefault(int(text), first)
    return [Reading(v, boxes[v], n) for v, n in sorted(counts.items(), key=lambda kv: -kv[1])]


ROWS = (84.3, 150.0, 216.8, 282.7, 349.0, 415.9)
"""Where a results block's six figures sit below the heading of the block, in pixels at 300 dpi."""
PITCH = 66.3


class Solved(NamedTuple):
    values: list[int]
    boxes: list[tuple[int, int, int, int]]


def _fits(v: list[int]) -> bool:
    return v[0] + v[1] == v[2] and v[2] + v[3] + v[4] == v[5]


def solve(candidates: list[list[Reading]]) -> Solved | None:
    """The one combination of readings that satisfies yes + no = valid and valid + null + blank = cast."""
    from itertools import product

    best: list[tuple[int, list[Reading]]] = []
    for combo in product(*candidates):
        if _fits([r.value for r in combo]):
            best.append((sum(r.votes for r in combo), list(combo)))
    if not best:
        return None
    best.sort(key=lambda b: -b[0])
    if len(best) > 1 and best[0][0] == best[1][0] and [r.value for r in best[0][1]] != [r.value for r in best[1][1]]:
        return None
    return Solved([r.value for r in best[0][1]], [r.box for r in best[0][1]])


def read_results(s: Sheet, card: Card, image: Image.Image | None) -> tuple[Solved | None, Image.Image | None]:
    """A card's six figures, from the cache or read now."""
    file = CACHE / str(1994) / f"results-{s.page}.json"
    cached = json.loads(file.read_text()) if file.exists() else {}
    name = str(card.anchor.top)
    if name in cached:
        got = cached[name]
        return (Solved(got[0], [tuple(b) for b in got[1]]) if got else None), image  # type: ignore[arg-type]
    solved, image = _read_results(s, card, image)
    cached[name] = [solved.values, [list(b) for b in solved.boxes]] if solved else None
    file.write_text(json.dumps(cached))
    return solved, image


def _read_results(s: Sheet, card: Card, image: Image.Image | None) -> tuple[Solved | None, Image.Image | None]:
    """A card's six figures: the words read from the whole page, then each figure's own crop where those do not fit."""
    base_y = card.anchor.yc
    by_row: list[list[Token]] = [[] for _ in ROWS]
    for t in card.results:
        i = round((t.yc - base_y - ROWS[0]) / PITCH)
        if 0 <= i < 6 and abs(t.yc - base_y - ROWS[i]) < 32:
            by_row[i].append(t)
    picked = [max(r, key=lambda t: t.conf) if r else None for r in by_row]
    whole = [digits(t.text) if t else None for t in picked]
    if None not in whole and _fits([int(v) for v in whole if v is not None]):
        return Solved(
            [int(v) for v in whole if v is not None], [(t.left, t.top, t.right, t.bottom) for t in picked if t]
        ), image
    if image is None:
        image = _image(1994, s.page)
    candidates: list[list[Reading]] = []
    for i, t in enumerate(picked):
        yc = int(base_y + ROWS[i])
        area = (1960, yc - 30, 2340, yc + 30)
        found = crop_readings(image, area)
        if t is not None and whole[i] is not None:
            known = int(whole[i] or 0)
            box = (t.left, t.top, t.right, t.bottom)
            found = [r for r in found if r.value != known] + [Reading(known, box, 3)]
        candidates.append(found)
    if any(not c for c in candidates):
        return None, image
    solved = solve(candidates)
    if solved is None:
        solved = solve([_confused(c) for c in candidates])
    return solved, image


CONFUSED = {"0": "86", "1": "47", "3": "8", "4": "1", "5": "6", "6": "5", "7": "1", "8": "30", "9": "4"}


def _confused(found: list[Reading]) -> list[Reading]:
    """The readings, with those they are most often mistaken for added: a 1 for a 4, a 3 for an 8, and the like."""
    out = list(found)
    seen = {r.value for r in found}
    for r in found[:8]:
        text = str(r.value)
        for i, digit in enumerate(text):
            for other in CONFUSED.get(digit, ""):
                value = int(text[:i] + other + text[i + 1 :])
                if value not in seen:
                    seen.add(value)
                    out.append(Reading(value, r.box, 0))
    return out


DEPARTMENTS_1994 = (1, 2, 3, 4, 5, 6, 8, 7, 9, 10, 12, 11, 13, 14, 15, 16, 18, 17, 19, 20, 21, 22)
STARTS_1994 = (65, 72, 80, 88, 92, 98, 104, 108, 116, 126, 134, 146, 150, 162, 170, 174, 180, 184, 190, 196, 202, 206)
"""The page each department's section starts on, in the order the memoria prints them."""
"""The department codes in the order the 1994 memoria prints its departments."""
ABBREVIATIONS = {"SN": "SAN", "STA": "SANTA", "STO": "SANTO", "GUAT": "GUATEMALA"}


def fold_name(text: str) -> str:
    words = re.sub(r"[^A-Z ]", " ", base.fold(text)).split()
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)


BLACKLIST = {
    "MUNICIPIO",
    "DE",
    "MUNICIPIC",
    "MUNICIP",
    "MUNICIPA",
    "MUNICIPIOC",
    "VOTOS",
    "EN",
    "BLANCO",
    "TOTAL",
    "ELECTORAL",
    "JUNTA",
    "MUNICIPAL",
    "MUNICIPIG",
    "MUNICIFPIC",
    "MUNICIPICG",
    "MUNICIPIOG",
}


def card_name(card: Card) -> str:
    """The municipality a card names, as its heading prints it."""
    s = card.sheet
    top = card.anchor.top
    junta = [t for t in s.tokens if t.text.upper().startswith("JUNTA") and top - 420 < t.top < top - 100]
    stop = min((t.top for t in junta), default=top - 160)
    heading = [
        t
        for t in s.tokens
        if t.left < 1700 and top - 440 < t.top < stop - 8 and t.conf > 20 and re.fullmatch(r"[^\W\d_]+\.?", t.text)
    ]
    heading.sort(key=lambda t: (t.top // 45, t.left))
    return " ".join(t.text for t in heading if t.text.upper() not in BLACKLIST)


def similarity(printed: str, town: str) -> float:
    from difflib import SequenceMatcher

    a, b = fold_name(printed), fold_name(town)
    if not a:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shared = len(set(a.split()) & set(b.split())) / max(len(b.split()), 1)
    return max(ratio, 0.5 * ratio + 0.5 * shared)


class Found(NamedTuple):
    card: Card
    values: list[int] | None
    boxes: list[tuple[int, int, int, int]]


def assign(found: list[Found], expected: list[tuple[str, str]]) -> dict[str, Found]:
    """A department's cards matched in order to its municipalities, by the names their headings print where they read.

    The memoria prints fewer municipalities than the Tribunal's codes list today, since some were created later, and
    not always in code order. The cards are aligned to the municipalities in order, each card scored by how well its
    heading reads as the municipality; a card whose heading reads as a different municipality is dropped."""
    names = [card_name(f.card) for f in found]
    n, m = len(found), len(expected)
    if n > m:
        print(f"{n} cards for {m} municipalities", flush=True)
        return {}
    score = [
        [(similarity(names[i], expected[j][0]) if len(names[i]) >= 4 else 0.45) for j in range(m)] for i in range(n)
    ]
    best = [[-1e9] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        best[0][j] = 0.0
    for i in range(1, n + 1):
        for j in range(i, m + 1):
            take = best[i - 1][j - 1] + score[i - 1][j - 1]
            skip = best[i][j - 1] if j > i else -1e9
            best[i][j], back[i][j] = (take, 1) if take >= skip else (skip, 0)
    out: dict[str, Found] = {}
    i, j = n, m
    while i > 0:
        if back[i][j]:
            if len(names[i - 1]) < 4 or score[i - 1][j - 1] >= 0.45:
                out[expected[j - 1][1]] = found[i - 1]
            i, j = i - 1, j - 1
        else:
            j -= 1
    return out


NATION_1994 = {"yes": 370044, "no": 70761, "null": 97107, "blank": 7982, "cast": 545894}
"""The national totals as the memoria prints them on page 63, read from the page image."""
CENTRAL_1994 = {"yes": 106033, "no": 21450, "null": 27598, "blank": 752, "cast": 155833}
"""The Distrito Central's figures on page 64, read from the page image: valid votes 127,483 are yes plus no."""
CENTRAL_X = (515.5, 541.4)
CENTRAL_ROWS = {"yes": 164.2, "no": 180.0, "null": 212.2, "blank": 228.0, "cast": 244.3}
"""The figures' pixel rows on page 64, in points from the top."""


def _roll(card: Card) -> int | None:
    v = [digits(t.text) for t in card.roll]
    if len(v) == 7 and None not in v:
        a, b, c, d, e, f, g = (int(x or 0) for x in v)
        if a + b == c and d + e == f and c + f == g:
            return g
    return None


def read1994() -> dict[str, main.Tally]:
    tally = main.Tally()
    first, last = main.MEMORIAS[1994].pages
    ordered: list[Card] = []
    for page in range(first + 2, last):
        ordered.extend(cards(1994, page))
    sections: list[tuple[int, list[Found]]] = [(dep, []) for dep in DEPARTMENTS_1994]
    image: Image.Image | None = None
    current = 0
    seen: set[int] = set()
    for card in ordered:
        page = card.sheet.page
        if page != current:
            current, image = page, None
            base.SIZES[(main.path(1994), current)] = (round(card.sheet.width), round(card.sheet.height))
        if page < STARTS_1994[0] or page > STARTS_1994[-1] + 6:
            continue
        index = max(i for i, start in enumerate(STARTS_1994) if start <= page)
        solved, image = read_results(card.sheet, card, image)
        f = Found(card, solved.values if solved else None, solved.boxes if solved else [])
        dep = sections[index][0]
        if page == STARTS_1994[index] and index not in seen:
            seen.add(index)
            if f.values:
                yes, no, _, null, blank, cast = f.values
                tally.departments[dep] = {"yes": yes, "no": no, "null": null, "blank": blank, "cast": cast}
            continue
        sections[index][1].append(f)
    expected: dict[int, list[tuple[str, str]]] = {}
    for towns in base.municipalities().values():
        for town, code in towns:
            if code != "0101":
                expected.setdefault(int(code[:2]), []).append((town, code))
    names = {code: town for towns in expected.values() for town, code in towns}
    for dep, found in sections:
        matched = assign(found, expected[dep])
        taken = {id(f) for f in matched.values()}
        for f in found:
            if id(f) not in taken:
                tally.left_out.append(
                    f"a card on page {f.card.sheet.page} whose heading cannot be matched to a municipality"
                )
        for code, f in matched.items():
            s = f.card.sheet
            if f.values is None:
                tally.left_out.append(f"{names[code]} (page {s.page}, readings do not add up)")
                continue
            tally.cells[code] = {
                field: word(s, Token("", *box_[:2], box_[2] - box_[0], box_[3] - box_[1], 0.0), v)
                for field, v, box_ in zip(
                    ("yes", "no", "valid", "null", "blank", "cast"), f.values, f.boxes, strict=True
                )
                if field != "valid"
            }
            total = _roll(f.card)
            if total is not None and total >= f.values[5]:
                tally.registered[code] = word(s, f.card.roll[-1], total)
        for _, code in expected[dep]:
            if code not in matched:
                tally.left_out.append(f"{names[code]} (the memoria prints no card for it)")
    base.SIZES[(main.path(1994), 64)] = base.SIZES[(main.path(1994), 66)]
    tally.declared = dict(NATION_1994)
    tally.cells["0101"] = {
        field: base.Word(
            64,
            CENTRAL_X[0],
            y - 6,
            CENTRAL_X[1],
            y + 6,
            str(CENTRAL_1994[field]),
            (CENTRAL_X[0], y - 6, CENTRAL_X[1], y + 6),
        )
        for field, y in CENTRAL_ROWS.items()
    }
    return {"question": tally}


def read(year: int) -> dict[str, main.Tally]:
    if year == 1994:
        return read1994()
    raise NotImplementedError(f"the {year} memoria is not read")
