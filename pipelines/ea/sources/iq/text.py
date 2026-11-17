"""Reading the Independent High Electoral Commission's Excel-printed Arabic PDF tables.

The PDFs come from Excel, and their text layer puts the letters that hang below the line (the final yeh, alef
maksura, noon, and reh) in separate runs at their own positions. A reader that joins words by position puts each
stray letter back at the end of the word whose span holds it.
"""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')
ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'"}
STRAY = set("يىنر")
MARKS = re.compile("[ً-ٰٟ]")


@dataclass(frozen=True)
class Word:
    x0: float
    x1: float
    y: float
    text: str
    page: int = 0

    @property
    def middle(self) -> float:
        return (self.x0 + self.x1) / 2


def pages(path: Path) -> Iterator[tuple[int, list[Word]]]:
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
            text = found[4]
            for entity, char in ENTITIES.items():
                text = text.replace(entity, char)
            words.append(Word(float(found[1]), float(found[3]), float(found[2]), text, number))
    if number:
        yield number, words


def logical(word: str) -> str:
    """The text layer lists an Arabic word's letters in visual order; digits and Latin runs are already in order."""
    if re.fullmatch(r"[\d.,/()%-]+", word) or re.fullmatch(r"[A-Za-z0-9 .,/()-]+", word):
        return word
    return word[::-1]


def phrase(words: list[Word]) -> str:
    """Join one cell's words, right to left, putting each stray letter at the end of the word that holds it."""
    body = [w for w in words if not (len(w.text) == 1 and w.text in STRAY)]
    strays = [w for w in words if len(w.text) == 1 and w.text in STRAY]
    pieces = {id(w): logical(w.text) for w in body}
    for stray in strays:
        host = min(
            (w for w in body if w.x0 - 4 <= stray.middle <= w.x1 + 4),
            key=lambda w: abs(w.middle - stray.middle),
            default=None,
        )
        if host is None:
            if body:
                host = min(body, key=lambda w: abs(w.middle - stray.middle))
            else:
                continue
        letter = stray.text
        if letter in "رن" and pieces[id(host)].endswith("ي") and len(pieces[id(host)]) > 2:
            letter = "ن" if letter == "ر" else letter
        pieces[id(host)] += letter
    ordered = sorted(body, key=lambda w: -w.x1)
    return " ".join(pieces[id(w)] for w in ordered)


COLUMNS = (  # the left edge of each column of the winners' vote table, right to left
    ("governorate", 645),
    ("center_number", 605),
    ("center", 490),
    ("list_number", 452),
    ("list", 278),
    ("candidate_number", 259),
    ("candidate", 98),
    ("votes", 0),
)


def column(word: Word) -> str:
    for name, edge in COLUMNS:
        if word.x0 >= edge - 0.5 and word.middle >= edge:
            return name
    return "votes"


def table(path: Path) -> Iterator[dict[str, str | float]]:
    """The vote table's rows, one per candidate and registration center."""
    for number, words in pages(path):
        anchors = sorted({w.y for w in words if column(w) == "center_number" and w.text.isdigit()})
        if not anchors:
            continue
        footer = min((w.y for w in words if w.text == "Page"), default=1e9)
        rows: dict[float, list[Word]] = {y: [] for y in anchors}
        for w in words:
            if w.y < anchors[0] - 8 or w.y >= footer - 2:
                continue
            rows[min(anchors, key=lambda y: abs(y - w.y))].append(w)
        for y in anchors:
            cells: dict[str, list[Word]] = {}
            for w in rows[y]:
                cells.setdefault(column(w), []).append(w)
            yield {"page": number, "y": y, **{name: phrase(cells.get(name, [])) for name, _ in COLUMNS}}
