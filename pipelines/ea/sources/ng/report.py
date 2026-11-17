"""Word-position readers for the annexures of INEC's Report of the 2019 General Election."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')
ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'"}
SAME_LINE = 2.0
FIRST_PAGE = 362
LAST_PAGE = 413


@dataclass(frozen=True)
class Word:
    x: float
    y: float
    x1: float
    text: str


def clean(text: str) -> str:
    for entity, char in ENTITIES.items():
        text = text.replace(entity, char)
    return text


def pages(path: Path) -> list[tuple[int, list[Word]]]:
    """Each annexure page's number and words, read from `pdftotext -bbox`."""
    out = subprocess.run(
        ["pdftotext", "-f", str(FIRST_PAGE), "-l", str(LAST_PAGE), "-bbox", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    result: list[tuple[int, list[Word]]] = []
    number = FIRST_PAGE - 1
    words: list[Word] = []
    for line in out.splitlines():
        if "<page " in line:
            if number >= FIRST_PAGE:
                result.append((number, words))
            number += 1
            words = []
            continue
        found = WORD.search(line)
        if found:
            words.append(Word(float(found[1]), float(found[2]), float(found[3]), clean(found[4])))
    result.append((number, words))
    return result


def line_of(words: list[Word], anchor: Word) -> list[Word]:
    return sorted((w for w in words if abs(w.y - anchor.y) <= SAME_LINE), key=lambda w: w.x)


def wrapped(
    words: list[Word], anchors: list[Word], left: float, right_of: dict[int, float], reach: float = 14.0
) -> dict[int, str]:
    """Join each anchor's name fragments: every line of text in the name column goes to the nearest anchor row."""
    taken: dict[int, list[Word]] = {i: [] for i in range(len(anchors))}
    for word in words:
        for i, anchor in enumerate(anchors):
            if word.x >= left and word.x1 <= right_of[i] and abs(word.y - anchor.y) <= SAME_LINE:
                taken[i].append(word)
                break
        else:
            if word.x < left or not anchors:
                continue
            near = min(range(len(anchors)), key=lambda i, y=word.y: abs(anchor.y - y))
            if word.x1 <= right_of[near] and abs(anchors[near].y - word.y) <= reach:
                taken[near].append(word)
    return {i: " ".join(w.text for w in sorted(ws, key=lambda w: (round(w.y), w.x))) for i, ws in taken.items()}


PARTY_FIRST = 93
PARTY_LAST = 101
PARTIES = 91


def parties(path: Path) -> list[tuple[int, str, str]]:
    """Table 4's numbered parties: number, name, and acronym."""
    out = subprocess.run(
        ["pdftotext", "-f", str(PARTY_FIRST), "-l", str(PARTY_LAST), "-bbox", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found: list[tuple[int, str, str]] = []
    words: list[Word] = []

    def flush() -> None:
        anchors = sorted(
            (w for w in words if w.x < 70 and w.text.isdigit() and w.y > 120 and int(w.text) <= PARTIES),
            key=lambda w: w.y,
        )
        bottom = anchors[-1].y + 45 if anchors and anchors[-1].text == str(PARTIES) else 1e9
        names = wrapped(
            [w for w in words if 120 < w.y < bottom], anchors, 88.0, {i: 175.0 for i in range(len(anchors))}, 60.0
        )
        for i, anchor in enumerate(anchors):
            acronym = next(w for w in line_of(words, anchor) if 170 <= w.x < 300)
            found.append((int(anchor.text), names[i], acronym.text))

    for line in out.splitlines():
        if "<page " in line:
            if words:
                flush()
            words = []
            continue
        match = WORD.search(line)
        if match:
            words.append(Word(float(match[1]), float(match[2]), float(match[3]), clean(match[4])))
    flush()
    return found
