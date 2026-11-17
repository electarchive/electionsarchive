"""Reading printed tables from the text layer of Statistics Finland's scanned volumes.

The volumes come from `otos.stat.fi` as PDFs whose text layer is the scan's character recognition, so a figure is
sometimes misread (a 6 for an 8) or lost. `pdftotext -bbox` gives each word with its box. A row is the words that share
a height, and a column is the words whose right edges line up, because a blank cell breaks the order of the words.
Every figure keeps its page and box, and the printed sums of a row check the reading.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE = re.compile(r'width="([\d.]+)" height="([\d.]+)">')
READ = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "i": "1", "Z": "2"})
NIL = {"-", "_", "—", "–", ".", "~", "“", "”", "■", "•", "*", "'", "&apos;", "&quot;"}


@dataclass(frozen=True)
class Box:
    """A word or run of words, in the points of the PDF page."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float
    words: list[Box]


def pages(path: Path, first: int, last: int) -> dict[int, Page]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: dict[int, Page] = {}
    for number, part in enumerate(re.split(r"<page ", out)[1:], first):
        size = PAGE.match(part)
        assert size
        words = [Box(m[5], float(m[1]), float(m[2]), float(m[3]), float(m[4])) for m in WORD.finditer(part)]
        found[number] = Page(number, float(size[1]), float(size[2]), words)
    return found


def rows(words: list[Box], tolerance: float = 2.5) -> list[list[Box]]:
    """The words of a page in reading order, grouped by their height."""
    ordered = sorted(words, key=lambda word: (word.y0 + word.y1) / 2)
    lines: list[list[Box]] = []
    last = None
    for word in ordered:
        height = (word.y0 + word.y1) / 2
        if last is None or height - last > tolerance:
            lines.append([])
        lines[-1].append(word)
        last = height
    return [sorted(line, key=lambda word: word.x0) for line in lines]


def runs(line: list[Box], gap: float = 3.5) -> list[Box]:
    """Words that touch one another, such as the characters the recognition split a number or a name into."""
    out: list[Box] = []
    for word in line:
        if out and word.x0 - out[-1].x1 < gap:
            last = out[-1]
            joined = last.text + (" " if word.x0 - last.x1 > 0.9 else "") + word.text
            out[-1] = Box(joined, last.x0, min(last.y0, word.y0), word.x1, max(last.y1, word.y1))
        else:
            out.append(word)
    return out


def number(text: str | None) -> int | None:
    """A printed count: a nil mark or an empty cell is zero, and a figure the recognition garbled is None."""
    if text is None:
        return 0
    text = text.replace(" ", "")
    if text in NIL or not text:
        return 0
    text = text.translate(READ)
    return int(text) if text.isdigit() else None


def letters(text: str) -> str:
    """A name with its spacing and punctuation removed, to compare names the recognition spaced out letter by letter."""
    return re.sub(r"[^a-zåäö]", "", text.lower().replace("é", "e"))


def pixels(box: Box) -> tuple[float, float, float, float]:
    """A box in the pixels of a page rendered at 100 dpi."""
    scale = 100 / 72
    return (round(box.x0 * scale, 1), round(box.y0 * scale, 1), round(box.x1 * scale, 1), round(box.y1 * scale, 1))
