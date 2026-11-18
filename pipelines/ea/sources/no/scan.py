"""Reading the printed tables of Statistics Norway's scanned volumes from the text layer of the PDFs.

The text layer is the scan's character recognition. `pdftotext -bbox` gives every word with its box; a row is the words
that share a height, and a figure is the digit groups of a row that the thin spaces of thousands join. Every figure keeps
its page and box, and the sums a table prints check the reading.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ea.sources.fi import digitized
from ea.sources.fi.digitized import Box


@dataclass
class Line:
    page: int
    words: list[Box]

    @property
    def y(self) -> float:
        return sum((w.y0 + w.y1) / 2 for w in self.words) / len(self.words)


def lines(path: Path, first: int, last: int, tolerance: float = 2.5) -> list[Line]:
    out: list[Line] = []
    for number, page in digitized.pages(path, first, last).items():
        out += [Line(number, line) for line in digitized.rows(page.words, tolerance)]
    return out


def sizes(path: Path, first: int, last: int) -> dict[int, tuple[float, float]]:
    return {n: (p.width, p.height) for n, p in digitized.pages(path, first, last).items()}


def value(text: str) -> int | None:
    return digitized.number(text)


NOISE = re.compile(r"[.,:;'\"`~_\-—–•■*0]{3,}|0{3,}\d*|[.,:;'`~_•■*]+")
GROUP = re.compile(r"\d{1,3}|\d{4,}")


def groups(words: list[Box]) -> list[Box]:
    """The digit words of a row, with the leader dots and the noise the recognition made of them dropped."""
    out = []
    for word in words:
        text = word.text
        if NOISE.fullmatch(text):
            continue
        if re.fullmatch(r"\d+", text):
            out.append(word)
    return out


def segmentations(items: list[Box], count: int) -> list[list[Box]]:
    """Every way to join consecutive digit words into `count` figures, a figure being one word, or a word of one to three
    digits followed by words of exactly three."""
    found: list[list[Box]] = []

    def join(parts: list[Box]) -> Box:
        return Box(
            " ".join(p.text for p in parts),
            parts[0].x0,
            min(p.y0 for p in parts),
            parts[-1].x1,
            max(p.y1 for p in parts),
        )

    def walk(start: int, made: list[Box]) -> None:
        if len(made) == count:
            if start == len(items):
                found.append(list(made))
            return
        if start >= len(items) or len(items) - start < count - len(made):
            return
        for end in range(start + 1, len(items) + 1):
            parts = items[start:end]
            if len(parts) > 1 and not (len(parts[0].text) <= 3 and all(len(p.text) == 3 for p in parts[1:])):
                break
            walk(end, [*made, join(parts)])

    walk(0, [])
    return found
