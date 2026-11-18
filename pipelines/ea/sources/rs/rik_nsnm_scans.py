"""Reads the national totals and the lists of the scanned reports of the national minority council elections.

The Republic Electoral Commission posted its reports of the total results of the 2022 and 2026 elections, and its
decisions on the electoral assemblies, as scans with no text layer. This module renders each page at 300 dpi, reads it
with Tesseract and keeps every figure with the page and the box it was read from. ``ea.sources.rs.rik_nsnm`` reads
the result from ``rik_nsnm_scans.json`` beside it, so building the packages needs no OCR. Run ``extract`` to read a
scan again: it prints the figures it found and what disagrees with the report's own arithmetic.
"""

import csv
import io
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DPI = 300
SCALE = 100 / DPI  # the boxes are kept in pixels of the page rendered at 100 dpi, as the other scanned sources do


@dataclass
class Word:
    page: int
    left: int
    top: int
    width: int
    height: int
    text: str
    block: tuple[int, int, int]

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


PASSES = (6, 11, 4)  # the page-segmentation modes of Tesseract whose readings are merged


def read(path: Path) -> list[Word]:
    """Every word Tesseract finds on every page of a scan, the figures as most of three readings agree on them."""
    passes: list[list[Word]] = [[] for _ in PASSES]
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", str(path), f"{work}/page"], check=True)
        for image in sorted(Path(work).glob("page-*.png")):
            page = int(image.stem.split("-")[-1])
            for i, mode in enumerate(PASSES):
                out = subprocess.run(
                    ["tesseract", str(image), "-", "-l", "srp+srp_latn", "--psm", str(mode), "tsv"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env={"OMP_THREAD_LIMIT": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
                )
                passes[i] += words_of(page, out.stdout)
    return merge(*passes)


def words_of(page: int, tsv: str) -> list[Word]:
    out = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"].strip():
            out.append(
                Word(
                    page,
                    int(row["left"]),
                    int(row["top"]),
                    int(row["width"]),
                    int(row["height"]),
                    row["text"].strip(),
                    (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
                )
            )
    return out


CYRILLIC = re.compile(r"[Ѐ-ӿ]")
DIGITS = re.compile(r"^[\d.,|]*\d[\d.,|]*$")
TOTALS = ("registered", "stations", "voters", "received", "unused", "used", "invalid", "valid")
VOTES_FROM, SEATS_FROM = 1700, 2040  # left edge, in pixels at 300 dpi, of the votes and of the seats column


def box(words: list[Word]) -> list[float]:
    """[page, x0, y0, x1, y1] around some words, in pixels at 100 dpi."""
    return [
        words[0].page,
        round(min(w.left for w in words) * SCALE, 1),
        round(min(w.top for w in words) * SCALE, 1),
        round(max(w.right for w in words) * SCALE, 1),
        round(max(w.bottom for w in words) * SCALE, 1),
    ]


def number(words: list[Word]) -> tuple[int, list[float]] | None:
    """The figure the given words spell, 'x.xxx' or 'x xxx' or 'xxxx', and the box around them."""
    words = [w for w in words if DIGITS.match(w.text)]
    if not words:
        return None
    digits = "".join(c for w in words for c in w.text if c.isdigit())
    return (int(digits), box(words)) if digits else None


def overlap(a: Word, b: Word) -> float:
    """How much of the smaller of two words' boxes the two share."""
    x = min(a.right, b.right) - max(a.left, b.left)
    y = min(a.bottom, b.bottom) - max(a.top, b.top)
    if x <= 0 or y <= 0:
        return 0
    return x * y / min(a.width * a.height, b.width * b.height)


def merge(*passes: list[Word]) -> list[Word]:
    """The words of the first pass, with each figure as most of the passes agree on it.

    Tesseract reads a table differently with each page-segmentation mode: one drops a figure another keeps, one reads
    "2.527" as a single word where another splits it in two, and they disagree on a digit now and then. The figures
    that overlap one another on the page are one group, and each pass's reading of the group is the digits of its words
    from left to right; the reading most passes give is kept, with the box around the words that gave it.
    """
    out = [w for w in passes[0] if not DIGITS.match(w.text)]
    tagged = [(i, w) for i, ws in enumerate(passes) for w in ws if DIGITS.match(w.text)]
    tagged.sort(key=lambda t: (t[1].page, t[1].top, t[1].left))
    groups: list[list[tuple[int, Word]]] = []
    for item in tagged:
        hits = [g for g in groups if any(item[1].page == m[1].page and overlap(item[1], m[1]) > 0.3 for m in g)]
        merged = [item]
        for g in hits:
            merged += g
            groups.remove(g)
        groups.append(merged)
    for g in groups:
        reading: dict[int, list[Word]] = {}
        for i, w in g:
            reading.setdefault(i, []).append(w)
        counts: dict[str, int] = {}
        for ws in reading.values():
            digits = "".join(c for w in sorted(ws, key=lambda w: w.left) for c in w.text if c.isdigit())
            counts[digits] = counts.get(digits, 0) + 1
        best = max(counts, key=lambda d: (counts[d], len(d)))
        ws = next(
            sorted(ws, key=lambda w: w.left)
            for ws in reading.values()
            if "".join(c for w in sorted(ws, key=lambda w: w.left) for c in w.text if c.isdigit()) == best
        )
        first = ws[0]
        out.append(
            Word(
                first.page,
                min(w.left for w in ws),
                min(w.top for w in ws),
                max(w.right for w in ws) - min(w.left for w in ws),
                max(w.bottom for w in ws) - min(w.top for w in ws),
                best,
                first.block,
            )
        )
    return out


def tidy(name: str) -> str:
    """A list's name as read, without the stray marks Tesseract takes the table's ruling for."""
    name = re.sub(r"(?<!\S)[|_'`~]+|[|_~]+(?!\S)", " ", name)
    return " ".join(name.replace("\u201c\u201c", "\u201c").split()).strip(" ,;")


def report(words: list[Word]) -> dict[str, Any]:
    """The eight totals and the votes and seats of each list of a report of the total results of a direct election."""
    figures = [w for w in words if DIGITS.match(w.text)]
    labels: dict[int, Word] = {}
    for w in words:
        m = re.match(r"^\|?(\d)[.,]?$", w.text)
        if m and w.page == 1 and w.left < 500 and int(m.group(1)) not in labels and w.top > 900:
            labels[int(m.group(1))] = w
    totals: dict[str, list[object]] = {}
    for n in range(1, 9):
        if n not in labels:
            continue
        near = [w for w in figures if w.page == 1 and w.left >= 1550 and abs(w.top - labels[n].top) < 70]
        found = number(sorted(near, key=lambda w: w.left))
        if found:
            totals[TOTALS[n - 1]] = [found[0], found[1]]
    if 9 not in labels:
        raise ValueError("no row 9 in the report")
    stop = (99, 10**9)
    for w in sorted(words, key=lambda w: (w.page, w.top)):
        if re.match(r"^(Овај|Ова)$", w.text) and w.left < 700 and w.page >= 1 and w.top > 150:
            stop = (w.page, w.top)
            break
    after = [
        w for w in figures if (w.page, w.top) > (1, labels[9].top + 50) and (w.page, w.top) < stop and w.height > 20
    ]
    votes = sorted((w for w in after if VOTES_FROM <= w.left < SEATS_FROM), key=lambda w: (w.page, w.top))
    seats = [w for w in after if w.left >= SEATS_FROM]
    names_from = [w for w in words if CYRILLIC.search(w.text) and 450 <= w.left < VOTES_FROM]
    lists = []
    for i, v in enumerate(votes):
        before = votes[i - 1] if i and votes[i - 1].page == v.page else None
        after_ = votes[i + 1] if i + 1 < len(votes) and votes[i + 1].page == v.page else None
        low = (before.top + v.top) / 2 if before else v.top - 230
        high = (v.top + after_.top) / 2 if after_ else v.top + 230
        mate = sorted(
            (s for s in seats if s.page == v.page and abs(s.top - v.top) < 110), key=lambda s: abs(s.top - v.top)
        )
        seat = number(mate[:1])
        names = [w for w in names_from if w.page == v.page and low <= w.top + w.height / 2 < high]
        lists.append(
            {
                "name": tidy(" ".join(w.text for w in sorted(names, key=lambda w: (round(w.top / 40), w.left)))),
                "votes": [int("".join(c for c in v.text if c.isdigit())), box([v])],
                "seats": [seat[0], seat[1]] if seat else None,
            }
        )
    return {"totals": totals, "lists": lists}


def problems(found: dict[str, Any]) -> list[str]:
    """Where a report's figures disagree with its own arithmetic."""
    totals = {k: v[0] for k, v in found["totals"].items()}
    lists = found["lists"]
    out = [f"missing total {t}" for t in TOTALS if t not in totals]
    if out:
        return out
    if totals["used"] != totals["valid"] + totals["invalid"]:
        out.append(f"used {totals['used']} is not valid {totals['valid']} plus invalid {totals['invalid']}")
    if totals["registered"] < totals["voters"] or totals["registered"] < totals["received"] // 2:
        out.append(f"registered {totals['registered']} is fewer than voters {totals['voters']}")
    votes = sum(row["votes"][0] for row in lists)
    if votes != totals["valid"]:
        out.append(f"list votes sum to {votes}, valid is {totals['valid']}")
    seats = [row["seats"] for row in lists]
    if any(s is None for s in seats) or sum(s[0] for s in seats) not in (15, 19, 23, 29, 35):
        out.append(f"seats {[s and s[0] for s in seats]}")
    return out
