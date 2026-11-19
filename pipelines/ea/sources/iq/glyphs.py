"""Reading Arabic text from the glyphs of the Commission's Excel-printed PDFs, where the text layer drops letters.

Excel prints these files with Calibri and shapes some letters as a dotless skeleton followed by separate zero-width dot
glyphs, and it ligates skeletons (a tooth and a final yeh, a lam and a final yeh, and so on). The PDF maps none of
those glyphs to a character, so a text extractor loses them. The fonts are embedded with their substitution tables,
so this module runs those tables backwards: it follows every substitution from a character to the glyph it ends as,
recording the dot glyphs split off on the way, and then reads each printed glyph as the letters whose skeleton it is
and whose dots are the ones printed with it.
"""

import io
import itertools
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
from fontTools.ttLib import TTFont

# Calibri's zero-width dot glyphs, as (dots above, dots below, hamza above). Glyph ids are Calibri's own, which the
# embedded subsets keep.
MARKS: dict[int, tuple[int, int, int]] = {
    6247: (1, 0, 0),
    6248: (2, 0, 0),
    6251: (3, 0, 0),
    6261: (3, 0, 0),
    6269: (0, 0, 1),
    6292: (0, 1, 0),
    6293: (0, 2, 0),
    6301: (0, 2, 0),
}
# Glyphs that no character in the embedded subsets reaches: a medial yeh with its dots drawn (in a Kurdish name) and a
# medial yeh with hamza, both read from the rendered page.
UNMAPPED = {5678: "ي", 5721: "ئ"}
# The dots each letter carries, as (above, below, hamza above); letters missing here have none.
DOTS: dict[str, tuple[int, int, int]] = {
    "ب": (0, 1, 0), "ت": (2, 0, 0), "ث": (3, 0, 0), "ن": (1, 0, 0), "ي": (0, 2, 0), "ج": (0, 1, 0),
    "خ": (1, 0, 0), "ذ": (1, 0, 0), "ز": (1, 0, 0), "ش": (3, 0, 0), "ض": (1, 0, 0), "ظ": (1, 0, 0),
    "غ": (1, 0, 0), "ف": (1, 0, 0), "ق": (2, 0, 0), "ة": (2, 0, 0), "ئ": (0, 0, 1), "أ": (0, 0, 1),
    "ؤ": (0, 0, 1),
}  # fmt: skip
# Letters that share a skeleton; a final yeh's skeleton is shared only with the other final yehs.
SKELETONS = ["جحخ", "دذ", "رز", "سش", "صض", "طظ", "عغ", "فق", "بتثنيئ", "هة", "اأ", "وؤ"]
FINAL_YEH = "يىئ"
# Letters a reading never contains: dotless archetypes, the Kurdish heh, and the tatweel, which the fonts join to
# their skeletons.
IGNORED = set("ٮںڡٯەـإآ")
LETTERS = {chr(c) for c in range(0x621, 0x64B)}

Derivation = dict[int, set[tuple[str, tuple[int, ...]]]]


def _dots(text: str) -> tuple[int, int, int]:
    above, below, hamza = 0, 0, 0
    for letter in text:
        a, b, h = DOTS.get(letter, (0, 0, 0))
        above, below, hamza = above + a, below + b, hamza + h
    return above, below, hamza


def _sum(marks: Iterable[int]) -> tuple[int, int, int]:
    above, below, hamza = 0, 0, 0
    for mark in marks:
        a, b, h = MARKS[mark]
        above, below, hamza = above + a, below + b, hamza + h
    return above, below, hamza


def _subtables(lookup: Any) -> Iterable[tuple[int, Any]]:
    for table in lookup.SubTable:
        if lookup.LookupType == 7:
            yield table.ExtSubTable.LookupType, table.ExtSubTable
        else:
            yield lookup.LookupType, table


def derive(font: TTFont, cmap: dict[int, int]) -> Derivation:
    """Every glyph the font's substitutions can make from a character, with the text and the dot glyphs split off."""
    order = font.getGlyphOrder()
    zero = {name for name in order if font["hmtx"][name][0] == 0}
    src: defaultdict[str, set[tuple[str, tuple[str, ...]]]] = defaultdict(set)
    for code, glyph in cmap.items():
        if chr(code) in LETTERS:
            src[order[glyph]].add((chr(code), ()))
    marks = {order[g] for g in MARKS}
    lookups = font["GSUB"].table.LookupList.Lookup
    while True:
        size = sum(map(len, src.values()))
        for lookup in lookups:
            for kind, table in _subtables(lookup):
                if kind in (1, 8):
                    pairs = (
                        table.mapping.items()
                        if kind == 1
                        else zip(table.Coverage.glyphs, table.Substitute, strict=True)
                    )
                    for a, b in pairs:
                        if a in src:
                            src[b] |= src[a]
                elif kind == 2:
                    for a, out in table.mapping.items():
                        split = tuple(x for x in out[1:] if x in marks)
                        for text, m in list(src.get(a, ())):
                            src[out[0]].add((text, tuple(sorted(m + split))))
                elif kind == 3:
                    for a, out in table.alternates.items():
                        for b in out:
                            if a in src:
                                src[b] |= src[a]
                elif kind == 4:
                    for first, ligatures in table.ligatures.items():
                        for ligature in ligatures:
                            parts = [first, *ligature.Component]
                            if any(p in zero or p not in src for p in parts):
                                continue
                            for combo in itertools.product(*(sorted(src[p]) for p in parts)):
                                text = "".join(t for t, _ in combo)
                                if len(text) <= 4:
                                    src[ligature.LigGlyph].add((text, tuple(sorted(x for _, m in combo for x in m))))
        if sum(map(len, src.values())) == size:
            break
    ids = {name: i for i, name in enumerate(order)}
    return {
        ids[name]: {(t, tuple(ids[x] for x in m)) for t, m in found if not set(t) & IGNORED}
        for name, found in src.items()
    }


@dataclass
class Glyph:
    glyph: int
    char: int
    x: float
    y: float
    width: float
    derivation: Derivation
    marks: list[tuple[int, float]] = field(default_factory=list)


def _intrinsic(found: set[tuple[str, tuple[int, ...]]]) -> tuple[int, int, int]:
    """The dots a glyph draws itself: a skeleton draws none, a precomposed letter draws its own."""
    counts: Counter[tuple[int, int, int]] = Counter()
    for text, marks in found:
        a, b, h = _dots(text)
        x, y, z = _sum(marks)
        if min(a - x, b - y, h - z) >= 0:
            counts[(a - x, b - y, h - z)] += 1
    return min(counts, key=lambda v: (sum(v), -counts[v]))


def _fits(text: str, glyph: Glyph) -> bool:
    """Whether the marks, placed along a ligature from the right, fall on the letters that carry them."""
    k = len(text)
    options = []
    for mark, x in glyph.marks:
        place = (glyph.x + glyph.width - x) / glyph.width * k
        low, high = (min(k - 1, max(0, int(place + d))) for d in (-0.075 * k, 0.075 * k))
        options.append([(i, mark) for i in range(low, high + 1)])
    for choice in itertools.product(*options):
        per: list[list[int]] = [[] for _ in range(k)]
        for i, mark in choice:
            per[i].append(mark)
        if all(_dots(text[i]) == _sum(per[i]) for i in range(k)):
            return True
    return False


def read(glyph: Glyph) -> str:
    """The letters one printed glyph stands for, given the dot glyphs printed on it."""
    if glyph.glyph in UNMAPPED and not glyph.marks:
        return UNMAPPED[glyph.glyph]
    unknown = [m for m, _ in glyph.marks if m not in MARKS]
    if unknown:
        raise ValueError(f"glyph {glyph.glyph} carries unknown zero-width glyphs {unknown}")
    found = glyph.derivation.get(glyph.glyph, set())
    seen = tuple(sorted(m for m, _ in glyph.marks))
    if not found:
        if glyph.char != 0xFFFD and not glyph.marks:
            return chr(glyph.char)
        raise ValueError(f"glyph {glyph.glyph} with marks {seen} is not made from any character")
    texts = {t for t, _ in found}
    own = _intrinsic(found)
    printed = _sum(seen)
    options: set[str] = set()
    for text in texts:
        last = len(text) - 1
        choices = [
            FINAL_YEH
            if i == last and any(len(t) == len(text) and t[i] == "ى" for t in texts)
            else next((s for s in SKELETONS if c in s), c)
            for i, c in enumerate(text)
        ]
        options |= {"".join(c) for c in itertools.product(*choices)}
    hits = sorted(
        o for o in options if tuple(a - b for a, b in zip(_dots(o), own, strict=True)) == printed and set(o) <= LETTERS
    )
    if len(hits) > 1:
        hits = [h for h in hits if not any(c in "ىة" for c in h[:-1])] or hits
    if len(hits) > 1:
        exact = {t for t, m in found if tuple(sorted(m)) == seen}
        hits = [h for h in hits if h in exact] or hits
    if len(hits) > 1 and not any(own):
        hits = [h for h in hits if _fits(h, glyph)] or hits
    if len(hits) != 1:
        raise ValueError(f"glyph {glyph.glyph} with marks {seen} reads as {hits}")
    return hits[0]


class Decoder:
    """Reads the glyphs of a set of PDFs printed with the same fonts, whose embedded subsets each map a few characters."""

    def __init__(self, paths: Iterable[Path]) -> None:
        cmap: dict[int, int] = {}
        fonts: dict[bytes, TTFont] = {}
        for path in paths:
            with pymupdf.open(path) as doc:
                for xref in {font[0] for page in doc for font in page.get_fonts(full=True)}:
                    font = TTFont(io.BytesIO(doc.extract_font(xref)[3]))
                    for code, name in (font.getBestCmap() or {}).items():
                        cmap.setdefault(code, font.getGlyphID(name))
                    fonts.setdefault(font["GSUB"].compile(font), font)
        self.derivation: Derivation = defaultdict(set)
        for font in fonts.values():
            for glyph, found in derive(font, cmap).items():
                self.derivation[glyph] |= found

    def glyphs(self, path: Path) -> list[list[Glyph]]:
        """Each page's printed glyphs other than dots, in the order printed, with the dot glyphs that sit on them."""
        out = []
        with pymupdf.open(path) as doc:
            for page in doc:
                items = [
                    Glyph(char[1], char[0], char[2][0], char[2][1], char[3][2] - char[3][0], self.derivation)
                    for span in page.get_texttrace()
                    for char in span["chars"]
                ]
                bases = [g for g in items if g.width > 0 or g.glyph == 3]
                pending: list[Glyph] = []
                loose: list[Glyph] = []
                for item in items:
                    if not (item.width > 0 or item.glyph == 3):
                        pending.append(item)
                        continue
                    for mark in pending:
                        if item.glyph != 3 and item.x - 0.3 <= mark.x <= item.x + item.width + 0.3:
                            item.marks.append((mark.glyph, mark.x))
                        else:
                            loose.append(mark)
                    pending = []
                for mark in loose + pending:
                    near = [b for b in bases if b.glyph != 3 and abs(b.y - mark.y) < 6]
                    if near:
                        host = min(
                            near,
                            key=lambda b: (
                                0
                                if b.x <= mark.x <= b.x + b.width
                                else min(abs(mark.x - b.x - d) for d in (0, b.width))
                            ),
                        )
                        host.marks.append((mark.glyph, mark.x))
                out.append(bases)
        return out


def text(glyphs: list[Glyph]) -> str:
    """One cell's glyphs, printed left to right, read as text in logical order, without the tatweels that stretch it."""
    letters = [" " if g.glyph == 3 else read(g) for g in reversed(glyphs)]
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", "".join(letters)).replace("\u0640", "")).strip()
