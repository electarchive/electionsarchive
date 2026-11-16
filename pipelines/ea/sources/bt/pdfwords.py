"""Word-position reader for the Election Commission of Bhutan's PDF result tables."""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')
ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'"}
LINE_GAP = 3.5


@dataclass(frozen=True)
class Word:
    x: float
    y: float
    text: str
    page: int = 0
    x1: float = 0.0

    @property
    def number(self) -> int | None:
        text = self.text.replace(",", "")
        return int(text) if text.isdigit() else None


def clean(text: str) -> str:
    for entity, char in ENTITIES.items():
        text = text.replace(entity, char)
    return text


def pages(path: Path) -> Iterator[tuple[int, list[Word]]]:
    """Yield each page's number and words, read from `pdftotext -bbox`."""
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    number = 0
    words: list[Word] = []
    for line in out.splitlines():
        if "<page " in line:
            if number:
                yield number, words
            number += 1
            words = []
            continue
        found = WORD.search(line)
        if found:
            words.append(Word(float(found[1]), float(found[2]), clean(found[4]), number, float(found[3])))
    if number:
        yield number, words


def lines(words: list[Word]) -> list[list[Word]]:
    """Group a page's words into lines by vertical position, each line ordered left to right."""
    grouped: list[list[Word]] = []
    top = -100.0
    for word in sorted(words, key=lambda word: (word.y, word.x)):
        if grouped and word.y - top <= LINE_GAP:
            grouped[-1].append(word)
        else:
            grouped.append([word])
            top = word.y
    return [sorted(group, key=lambda word: word.x) for group in grouped]


def stream(path: Path) -> Iterator[list[Word]]:
    for _, words in pages(path):
        yield from lines(words)
