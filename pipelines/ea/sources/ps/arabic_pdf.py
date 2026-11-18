"""Reads the Arabic text of the Central Elections Commission's PDF tables.

The Commission's reports embed an Arial subset whose ToUnicode map is wrong for most Arabic letters, so a text
extractor returns letters missing or garbled. The glyphs keep Arial's own glyph numbers, so this module drops the
font's map, reads each character by glyph number, and names the letter by the shape of that glyph in the font (a
table checked against the rendered glyph shapes and the printed pages). The characters of a line come in visual
order, left to right, so a line's words and letters are reversed to read in logical order.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pymupdf

LETTERS: dict[int, str] = {}


def _letters(text: str, *glyphs: int) -> None:
    for glyph in glyphs:
        LETTERS[glyph] = text


_letters("ء", 0x2EF)
_letters("،", 0x2EC)
_letters("", 0x2F1, 0x2F2, 0x2F3, 0x2F4, 0x2F5, 0x2F6, 0x2F7, 0x2F8, 0x349)  # vowel marks, not read
_letters("الله", 0x37F, 0xBCC)
_letters("ريال", 0xBCD)
_letters("آ", 0x381)
_letters("أ", 0x383, 0x384)
_letters("ؤ", 0x385)
_letters("إ", 0x387)
_letters("ئ", 0x38B, 0x529)
_letters("ا", 0x38D, 0x38E)
_letters("ب", 0x38F, 0x391)
_letters("ة", 0x393, 0x394)
_letters("ت", 0x395, 0x397)
_letters("ث", 0x399, 0x39B)
_letters("ج", 0x39D, 0x39E, 0x39F)
_letters("ح", 0x3A1, 0x3A2, 0x3A3)
_letters("خ", 0x3A5, 0x3A6, 0x3A7)
_letters("د", 0x3A9)
_letters("ذ", 0x3AB)
_letters("ر", 0x3AD)
_letters("ز", 0x3AF)
_letters("س", 0x3B1, 0x3B3)
_letters("ش", 0x3B5, 0x3B7)
_letters("ص", 0x3B9, 0x3BB)
_letters("ض", 0x3BD, 0x3BF)
_letters("ط", 0x3C1)
_letters("ظ", 0x3C5)
_letters("ع", 0x3C9, 0x3CA, 0x3CB, 0x3CC)
_letters("غ", 0x3CF, 0x3D0)
_letters("ف", 0x3D1, 0x3D3, 0x3D4)
_letters("ق", 0x3D5, 0x3D6, 0x3D7, 0x3D8)
_letters("ك", 0x3D9, 0x3DB)
_letters("ل", 0x3DD, 0x3DF, 0x3E0)
_letters("م", 0x3E1, 0x3E3)
_letters("ن", 0x3E5, 0x3E7)
_letters("ه", 0x3E9, 0x3EA, 0x3EB, 0x3EC)
_letters("و", 0x3ED)
_letters("ى", 0x3EF, 0x3F0)
_letters("ي", 0x3F1, 0x3F2, 0x3F3)
_letters("لآ", 0x3F5)
_letters("لأ", 0x3F7, 0x3F8)
_letters("لإ", 0x3F9)
_letters("لا", 0x3FB, 0x3FC)


class Line(NamedTuple):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def open_clean(path: Path) -> pymupdf.Document:
    document = pymupdf.open(path)
    for page in document:
        for font in page.get_fonts():
            if font[2] == "Type0":
                document.xref_set_key(font[0], "ToUnicode", "null")
    return document


def _decode(chars: list[tuple[str, float, float, float, bool]]) -> str:
    """Logical text of one run of characters given in visual left-to-right order.

    Each character is its glyph, its left and right edge, and its font size. The stream holds no space characters,
    so a gap wider than a fraction of the font size is a space. A run of Latin letters, digits, and punctuation reads
    left to right inside the right-to-left line.
    """
    segments: list[tuple[str, list[str]]] = []
    previous: float | None = None
    for glyph, left, right, size, embedded in chars:
        code = ord(glyph)
        if embedded and code < 0x80:
            glyph = chr(code + 29)  # Arial's first glyphs run from the space in ASCII order
            code = ord(glyph)
        kind = "arabic" if code >= 128 else "latin"
        text = LETTERS.get(code, f"\ufffd{code:04x}") if kind == "arabic" else glyph
        spaced = glyph == " " or (previous is not None and left - previous > 0.17 * size)
        previous = right
        if spaced:
            segments.append(("space", [" "]))
        if glyph == " ":
            continue
        if segments and segments[-1][0] == kind:
            segments[-1][1].append(text)
        else:
            segments.append((kind, [text]))
    words = ["".join(reversed(letters)) if kind == "arabic" else "".join(letters) for kind, letters in segments]
    return re.sub(r"\)([^()]*)\(", r"(\1)", "".join(reversed(words)))


def lines(path: Path) -> Iterator[Line]:
    document = open_clean(path)
    for number in range(1, document.page_count + 1):
        page = document[number - 1]
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                chars = [
                    (c["c"], c["bbox"][0], c["bbox"][2], span["size"], not span["font"].startswith("Helvetica"))
                    for span in line["spans"]
                    for c in span["chars"]
                ]
                if not chars:
                    continue
                text = _decode(chars)
                if not text.strip():
                    continue
                x0, y0, x1, y1 = line["bbox"]
                yield Line(number, x0, y0, x1, y1, text)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
