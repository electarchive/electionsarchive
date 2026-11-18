"""Decode the Devanagari text of the Election Commission's result books.

The books were laid out in Word with the Kalimati font and exported to PDF with a private text mapping, so the text
layer is unusable. Every character is a shaped glyph (conjuncts, half forms, and a detached pre-base vowel sign). The
embedded font keeps the font's own glyph ids, so each glyph id maps to the Unicode text it draws, and the shaped order is
put back into logical order here.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pymupdf

PRE_I = "\x01"  # the short-i vowel sign, drawn before its syllable
REPH = "\x02"  # the superscript form of ra, drawn after its syllable
REPH_II = "\x03"  # the superscript ra fused with the long-i vowel sign
REPH_O = "\x04"
REPH_AU = "\x05"

DIGITS = "०१२३४५६७८९"
SIGNS = {"ा", "ि", "ी", "ु", "ू", "ृ", "े", "ै", "ो", "ौ", "ं", "ँ", "ः", "्", "ें", "ैं", "ों", "ौं"}


def _build() -> dict[int, str]:
    g: dict[int, str] = {}
    g.update({3: " ", 10: "(", 11: ")", 14: ",", 15: "-", 16: ".", 17: "/", 28: ":", 37: "]", 45: "—", 64: "।"})
    for i, d in enumerate(DIGITS):
        g[18 + i] = d
        g[66 + i] = d
    g[81] = "्"
    g[91] = REPH
    g.update({92: "अ", 93: "आ", 94: "इ", 95: "ई", 96: "उ", 98: "ऋ", 102: "ए", 103: "ऐ", 106: "ओ", 107: "औ"})
    letters = [
        "क",
        "ख",
        "ग",
        "घ",
        "ङ",
        "च",
        "छ",
        "ज",
        "झ",
        "ञ",
        "ट",
        "ठ",
        "ड",
        "ढ",
        "ण",
        "त",
        "थ",
        "द",
        "ध",
        "न",
        "प",
        "फ",
        "ब",
        "भ",
        "म",
        "य",
        "र",
        "ल",
        "ळ",
        "व",
        "श",
        "ष",
        "स",
        "ह",
        "क्ष",
        "ज्ञ",
    ]
    for i, c in enumerate(letters):
        g[128 + i] = c
    halves = {
        200: "क", 201: "ख", 202: "ग", 203: "घ", 204: "ङ", 205: "च", 206: "छ", 207: "ज", 208: "झ", 209: "ञ",
        210: "ट", 212: "ड", 214: "ण", 215: "त", 216: "थ", 217: "द", 218: "ध", 219: "न", 220: "प", 221: "फ",
        222: "ब", 223: "भ", 224: "म", 227: "ल", 229: "व", 230: "श", 231: "ष", 232: "स", 234: "क्ष", 616: "ङ",
    }  # fmt: skip
    for k, v in halves.items():
        g[k] = v + "्"
    conj = {
        272: "क्र", 273: "ख्र", 274: "ग्र", 278: "छ्र", 279: "ज्र", 282: "ट्र", 287: "त्र", 289: "द्र", 290: "ध्र",
        292: "प्र", 293: "फ्र", 294: "ब्र", 295: "भ्र", 296: "म्र", 301: "व्र", 302: "श्र", 304: "स्र", 305: "ह्र",
        416: "क्त", 417: "ख्न", 419: "त्त", 420: "त्न", 423: "द्द", 424: "द्ध", 428: "द्म", 429: "द्य", 430: "द्व",
        431: "प्त", 432: "श्न", 433: "श्च", 434: "श्ल", 435: "श्व", 436: "ष्ट", 437: "ष्ठ", 438: "ष्ट्र",
        440: "स्त्र", 441: "ह्न", 442: "ह्म", 446: "ह्व", 459: "ङ्क", 460: "ङ्ख", 461: "ङ्ग", 462: "ङ्घ",
        463: "न्न", 509: "रु", 510: "रू", 511: "हृ", 669: "ड्ग", 670: "क्क", 675: "ट्ट", 683: "ट्य", 685: "ड्य",
        693: "ञ्च", 694: "ञ्च", 695: "ञ्ज", 344: "र्क्", 354: "र्ट्", 374: "श्र",
    }  # fmt: skip
    g.update(conj)
    g.update({97: "ऊ", 233: "ह्", 443: "ह्य", 506: "दृ", 589: "ं", 622: "द्", 638: "द्र", 470: ""})
    for k in (464, 465, 466, 467, 468):
        g[k] = PRE_I
    g[545] = "ा"
    for k in (546, 547, 548, 551):
        g[k] = "ी"
    g[469] = "ु"
    g[471] = "ू"
    g[473] = "ृ"
    g[554] = "ो"
    for k in (568, 569, 573):
        g[k] = REPH_II
    g[574] = REPH_O
    g[575] = REPH_AU
    g[555] = "ौ"
    g.update({556: "ः", 559: "े", 560: "ै", 561: "ँ", 562: "ं", 566: "े", 567: "ै", 581: "ें", 583: "ैं"})
    g.update({585: "ों", 586: "ौं", 587: "ौं", 578: "ईं", 579: "ईं", 668: "्"})
    return g


REPH_SIGN = {REPH_II: "ी", REPH_O: "ो", REPH_AU: "ौ"}
GLYPHS = _build()
# a few names are set in PCS Nepali, which keeps Preeti's key-to-letter layout
PCS = {"=": ".", "M": ":", ";": "स", "f": "ा", "i": "ष", "r": "च", "k": "प"}
HALF = {k for k, v in GLYPHS.items() if v.endswith("्") and len(v) >= 2}


def logical(glyphs: list[int | str]) -> str:
    """Put a run of shaped glyphs (one word) back into Unicode order."""
    out: list[str] = []
    start = 0  # index in `out` where the current syllable began
    pending_i = False
    has_base = False
    for gid in glyphs:
        t = GLYPHS.get(gid) if isinstance(gid, int) else gid
        if t is None:
            out.append(f"⟨{gid}⟩")
            continue
        if t == PRE_I:
            if has_base:
                start = len(out)
                has_base = False
            pending_i = True
            continue
        if t == REPH:
            out.insert(start, "र्")
            continue
        if t in REPH_SIGN:
            out.insert(start, "र्")
            out.append(REPH_SIGN[t])
            continue
        if t == "्":
            if not out or not out[-1].endswith("्"):
                out.append(t)  # a bare virama after a half form adds nothing
            continue
        if t in SIGNS:
            out.append(t)
            continue
        # a letter-like glyph
        is_vowel_letter = t in "अआइईउऊऋएऐओऔ" and len(t) == 1
        if has_base:
            start = len(out)
            has_base = False
        if pending_i and t.endswith("्") is False:
            out.append(t)
            out.append("ि")
            pending_i = False
            has_base = True
            continue
        out.append(t)
        if is_vowel_letter or not t.endswith("्"):
            has_base = True
    if pending_i:
        out.append("ि")
    return "".join(out)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    y: float
    size: float


def words(page: pymupdf.Page) -> list[Word]:
    """The words of a page: Kalimati glyph runs decoded, other fonts as their own text."""
    seen: set[tuple[Any, ...]] = set()
    out: list[Word] = []
    cur: list[Any] = []  # glyph ids (Kalimati) or characters
    kal = False
    x0 = x1 = y = size = 0.0
    blank: float | None = None  # x of a space glyph not yet known to advance

    def flush() -> None:
        nonlocal cur, blank
        if cur:
            text = logical(cur) if kal else "".join(cur)
            if text.strip():
                out.append(Word(text, x0, x1, y, size))
        cur = []
        blank = None

    for t in page.get_texttrace():
        font = str(t["font"])
        pcs = "PCSNEPALI" in font
        isk = "Kalimati" in font or pcs
        chars = t["chars"]
        if not chars:
            continue
        by_glyph = isk and not pcs
        key = (isk, round(chars[0][2][0]), round(chars[0][2][1]), tuple(c[1] if by_glyph else c[0] for c in chars))
        if key in seen:
            continue
        seen.add(key)
        fx, fy = chars[0][2]
        gap = fx - (blank if blank is not None else x1)
        if not (cur and isk == kal and abs(fy - y) < 1.0 and -2.0 <= gap < (1.5 if blank is not None else 3.0)):
            flush()
            kal, y, size = isk, fy, float(t["size"])
        for uni, gid, origin, bbox in chars:
            if by_glyph and gid < 0:
                continue  # the extra characters a ligature glyph stands for
            if (gid == 3) if by_glyph else chr(uni) == " ":
                if blank is not None and origin[0] - blank >= 1.5:
                    flush()
                if cur:
                    blank = origin[0]
                continue
            if blank is not None:
                if origin[0] - blank >= 1.5:
                    flush()
                blank = None
            if not cur:
                x0, y = origin
            cur.append(gid if by_glyph else PCS.get(chr(uni), chr(uni)) if pcs else chr(uni))
            x1 = bbox[2]
    flush()
    return out


def lines(page: pymupdf.Page, tol: float = 2.5) -> Iterator[tuple[float, list[Word]]]:
    ws = sorted(words(page), key=lambda w: (round(w.y), w.x0))
    cur: list[Word] = []
    for w in ws:
        if cur and abs(w.y - cur[0].y) > tol:
            yield cur[0].y, sorted(cur, key=lambda v: v.x0)
            cur = []
        cur.append(w)
    if cur:
        yield cur[0].y, sorted(cur, key=lambda v: v.x0)


NUM = re.compile(r"^[\d,.]+$")


def grid(page: pymupdf.Page) -> tuple[list[float], list[tuple[float, float, float]]]:
    """Row rules (y) and vertical rules (x, y0, y1) from the thin filled rectangles that draw a table."""
    horizontal: dict[int, list[tuple[float, float]]] = {}
    vertical: list[tuple[float, float, float]] = []
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] != "re":
                continue
            r = item[1]
            if r.height < 1.6 and r.width > 0.5:
                horizontal.setdefault(round((r.y0 + r.y1) / 2 * 2), []).append((r.x0, r.x1))
            elif r.width < 1.6 and r.height > 3:
                vertical.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
    rules: list[float] = []
    for key, spans in sorted(horizontal.items()):
        if max(b for _, b in spans) - min(a for a, _ in spans) >= 250:
            y = key / 2
            if not rules or y - rules[-1] > 1.5:
                rules.append(y)
    return rules, vertical
