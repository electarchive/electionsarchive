"""Reads the Arabic text of the Central Elections Commission's 2021 and 2022 result PDFs.

These reports embed subset TrueType fonts (Arial) whose ToUnicode map is sound for single letters but maps each
ligature glyph to one letter: the lam-alef glyphs (لا, لأ, لإ) read as a bare lam, and the Allah glyphs read as an
alef or a lam. A plain text extractor therefore drops letters (صلاح reads صلح, عبد الله reads عبد ا). This module
rewrites each font's map before extraction (the extractor reverses a right-to-left run, so a multi-letter value is stored reversed), naming each ligature by the shape of its glyph in the embedded font:
its advance width tells the ligature from a plain lam, and the extent of its outline tells the hamza above
(لأ) or below (لإ) from none (لا). A glyph this table does not know raises an error instead of reading wrong.
"""

import io
import re
from pathlib import Path
from typing import NamedTuple

import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

LAM = 0x644
ALEF = 0x627
# Advance widths (2,048 units to the em) of Arial's regular and bold lam forms and lam-alef ligatures.
LAM_WIDTHS = {424, 480, 924, 1037}
LIGATURE_WIDTHS = {996, 1108, 1114, 1230}
ALLAH = 1600  # the glyph of "الله" and its width in the regular and bold subsets
LAM_LAM_HA = 1262  # the glyph of "لله" after an alef printed on its own


class Line(NamedTuple):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def _name(letter: int, width: int, low: float, high: float) -> str:
    if width == 0:
        return ""  # a vowel mark
    if letter == ALEF and width >= ALLAH:
        return "الله"
    if letter != LAM:
        return chr(letter)
    if width in LAM_WIDTHS:
        return "ل"
    if width == LAM_LAM_HA:
        return "لله"
    if width in LIGATURE_WIDTHS:
        if high > 1700:
            return "لأ"
        if low < -100:
            return "لإ"
        return "لا"
    raise ValueError(f"an unknown lam glyph of width {width} (outline {low}..{high})")


def _repair(document: pymupdf.Document, font: int) -> None:
    obj = document.xref_object(font)
    cmap = re.search(r"/ToUnicode (\d+) 0 R", obj)
    descriptor = re.search(r"/FontDescriptor (\d+) 0 R", obj)
    if cmap is None or descriptor is None or "/TrueType" not in obj:
        return
    program = re.search(r"/FontFile2 (\d+) 0 R", document.xref_object(int(descriptor.group(1))))
    if program is None:
        return
    text = document.xref_stream(int(cmap.group(1))).decode("latin1")
    codes = {int(a, 16): int(u, 16) for a, _, u in re.findall(r"<(..)> <(..)> <(....)>", text)}
    # The fonts' ranges each hold one code; a longer range would shift the table silently.
    for a, b in re.findall(r"<(..)> <(..)> <....>", text):
        if a != b:
            raise ValueError(f"font {font}: a ToUnicode range of several codes")
    ttf = TTFont(io.BytesIO(document.xref_stream(int(program.group(1)))))
    table = ttf["cmap"].tables[0].cmap
    glyphs = ttf.getGlyphSet()
    metrics = ttf["hmtx"].metrics
    lines = [
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        "1 begincodespacerange <00> <ff> endcodespacerange",
    ]
    entries: list[str] = []
    for code, letter in sorted(codes.items()):
        glyph = table.get(0xF000 + code)
        if glyph is None:
            raise ValueError(f"font {font}: code {code:02x} has no glyph")
        pen = BoundsPen(glyphs)
        glyphs[glyph].draw(pen)
        low, high = (pen.bounds[1], pen.bounds[3]) if pen.bounds else (0, 0)
        value = _name(letter, metrics[glyph][0], low, high)
        entries.append(f"<{code:02x}> <{value[::-1].encode('utf-16-be').hex() or '0020'}>")
    # An empty mapping would read as a space, so a vowel mark maps to a zero-width non-joiner and is dropped later.
    entries = [
        e.replace("<0020>", "<200c>") if e.endswith("<0020>") and codes[int(e[1:3], 16)] != 0x20 else e for e in entries
    ]
    lines.append(f"{len(entries)} beginbfchar")
    lines.extend(entries)
    lines.append("endbfchar endcmap CMapName currentdict /CMap defineresource pop end end")
    document.update_stream(int(cmap.group(1)), "\n".join(lines).encode("latin1"))


def open_fixed(path: Path) -> pymupdf.Document:
    document = pymupdf.open(path)
    done: set[int] = set()
    for number in range(document.page_count):
        for font in document[number].get_fonts():
            if font[0] not in done:
                done.add(font[0])
                _repair(document, font[0])
    return document


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("‌", "")).strip()


def lines(path: Path) -> list[Line]:
    out: list[Line] = []
    document = open_fixed(path)
    for number in range(1, document.page_count + 1):
        for block in document[number - 1].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = clean(span["text"])
                    if text:
                        x0, y0, x1, y1 = span["bbox"]
                        out.append(Line(number, x0, y0, x1, y1, text))
    return out


def words(path: Path) -> list[list[tuple[float, float, float, float, str]]]:
    """Each page's words with boxes, read through the repaired fonts."""
    document = open_fixed(path)
    return [[(w[0], w[1], w[2], w[3], clean(w[4])) for w in page.get_text("words") if clean(w[4])] for page in document]
