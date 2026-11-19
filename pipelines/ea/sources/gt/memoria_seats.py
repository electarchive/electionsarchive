"""Seats the 1995 and 1999 memorias adjudicate, read by OCR from their scanned pages.

The memorias print, beside the results, who holds each seat:

- 1995 section 5 lists the elected of the national list, of each district, and of the Central American Parliament
  under a heading for each organization, and every municipality's results page ends with a box that lists the
  titular and substitute posts of its corporation, each led by its organization's acronym.
- 1999 prints a box on each department's results page for its district deputies, one for the Distrito Central, and
  one on each municipality's page for its corporation, with the organization's acronym at the end of each row; its
  national list is a page of acronyms beside names.

An organization's seats are the rows its acronym leads, for the titular posts only. Every count is checked against
the size of the body: the 16 deputies of the 1995 national list, the 20 of PARLACEN, the posts of each corporation.
A box that does not read to its checks is left out and listed by `LEFT`.
"""

import io
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import NamedTuple

import numpy as np
from PIL import Image
from scipy import ndimage

from ea.sources.gt import memoria as base
from ea.sources.gt.memoria import fold, label
from ea.sources.gt.memoria_scan import CACHE, path, runs, straightened

LEFT: dict[int, list[str]] = {}
"""What each year's reading left out, with the reason."""

ACRONYM = re.compile(r"[(\[{]\s*([A-Z0-9][A-Z0-9\-= ]{0,14}?)\s*[)\]}]?\s*[:.]?\s*$")


class Token(NamedTuple):
    """A word of a page read by OCR with its box."""

    text: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


def read_tokens(image: Image.Image, psm: int = 6, language: str = "eng") -> list[Token]:
    """The words of a page with their boxes, from Tesseract's table output."""
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    out = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", str(psm), "-l", language, "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=False,
    ).stdout.decode()
    found: list[Token] = []
    for row in out.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12 or not cells[11].strip():
            continue
        x, y, w, h = (int(c) for c in cells[6:10])
        found.append(Token(cells[11].strip(), x, y, x + w, y + h))
    return found


def rows_of(found: list[Token]) -> list[list[Token]]:
    """Tokens grouped into printed lines, each left to right."""
    out: list[list[Token]] = []
    for t in sorted(found, key=lambda t: t.yc):
        height = t.y1 - t.y0
        if out and abs(out[-1][-1].yc - t.yc) <= max(height * 0.6, 6):
            out[-1].append(t)
        else:
            out.append([t])
    return [sorted(row, key=lambda t: t.x0) for row in out]


def line_text(row: list[Token]) -> str:
    return " ".join(t.text for t in row)


# Resolving an acronym -----------------------------------------------------------------------------------------------


def resolve(text: str, columns: list[str]) -> str | None:
    """The label of the organization on a ballot that an acronym as printed names, or None."""
    wanted = label(text.replace("CCG", "DCG"))
    parties = [label(c) for c in columns if label(c) not in base.ACCOUNTS]
    if wanted in parties:
        return wanted
    ends = [
        p for p in parties if wanted and (p.endswith(wanted) or wanted.endswith(p)) and min(len(p), len(wanted)) >= 3
    ]
    if len(ends) == 1:
        return ends[0]
    scored = sorted(
        (
            (max(SequenceMatcher(None, wanted, p).ratio(), SequenceMatcher(None, wanted, p[-len(wanted) :]).ratio()), p)
            for p in parties
        ),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        return scored[0][1]
    return None


# 1995: section 5 ------------------------------------------------------------------------------------------------------

SECTION = {"national": [316], "district": [317, 318, 319], "parlacen": [320]}
"""The PDF pages of the 1995 memoria's lists of the elected."""
DPI = 200
FURNITURE = re.compile(r"TRIBUNAL|SUPREMO|ELECTORAL|ELECCIONES|MEMORIA|ADJUDICACION|DIPUTADOS|TITULARES|SUPLENTES")
WORDS = re.compile(r"[A-Za-z\u00c0-\u00ff]{3,}")


class Listing:
    """The names a column of a list of the elected prints under each heading, counted by heading."""

    def __init__(self, start: str | None = None) -> None:
        self.counts: dict[tuple[str, str], int] = {}
        self.district = ""
        self.party = ""
        self.live = start is None
        self.start = start
        self.skipping = False
        self.last = ""

    def feed(self, text: str) -> None:
        text = text.strip()
        folded = fold(text)
        if not folded:
            return
        if self.start:
            # The list's heading runs over both columns: it starts the left one and ends in the right one's top.
            if self.start in folded or re.fullmatch(r"NACIONAL\W*", folded):
                self.live, self.party = True, ""
                return
            if "TRIBUNAL" in folded:
                self.live, self.party = False, ""
                return
        if not self.live:
            return
        if folded.startswith("DISTRITO"):
            self.district = "00" if "CENTRAL" in folded else _district(folded)
            self.party = ""
            return
        if folded.startswith("COALI"):
            self.skipping = True
            return
        found = ACRONYM.search(text)
        if found and re.search(r"[)}\]]", text):
            party = re.sub(r"[^A-Z0-9\-]", "", found.group(1).replace("=", "-"))
            if text.lstrip().startswith("(") and not self.skipping and self.last:
                # The line above was the first half of this heading, not a name.
                self._drop(self.last)
            self.party, self.skipping, self.last = party, False, ""
            return
        if self.skipping or not self.party:
            return
        if FURNITURE.search(folded) or len(WORDS.findall(text)) < 2 or len(re.sub(r"[^A-Za-z]", "", text)) < 11:
            return
        key = (self.district, self.party)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.last = text if text.upper() == text else ""

    def _drop(self, text: str) -> None:
        key = (self.district, self.party)
        if self.counts.get(key):
            self.counts[key] -= 1


def _district(folded: str) -> str:
    """The code of the department a district heading names."""
    words = re.sub(r"^DISTRITO\s+(?:DE\s+)?", "", folded).split()
    for size in (1, 2):
        found = " ".join(words[:size])
        if base.department(found) is not None:
            return f"{base.department_code(found):02d}"
    raise ValueError(f"no department in the district heading {folded!r}")


def _image(number: int) -> Image.Image:
    return straightened(path(1995), number, DPI).image.convert("L")


def page_lines(image: Image.Image, edge: float = 0.45, gap: int = 70) -> list[str]:
    """The lines of a page of two columns, the left column then the right, each from top to bottom.

    Tesseract reads the page as one block, so a printed line holds the words of both columns: each is cut where the
    words part by more than `gap`, and a piece belongs to the right column where it starts past `edge` of the width.
    """
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    out = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "eng", "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    ).stdout.decode()
    found: dict[tuple[str, str, str], list[Token]] = {}
    for row in out.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12 or not cells[11].strip():
            continue
        x, y, w, h = (int(c) for c in cells[6:10])
        found.setdefault((cells[2], cells[3], cells[4]), []).append(Token(cells[11].strip(), x, y, x + w, y + h))
    starts = [
        t.x0
        for ts in found.values()
        for before, t in zip(sorted(ts, key=lambda t: t.x0), sorted(ts, key=lambda t: t.x0)[1:], strict=False)
        if t.x0 - before.x1 > gap and 0.35 * image.width < t.x0 < 0.7 * image.width
    ]
    right = float(np.median(starts)) - 25 if starts else edge * image.width
    pieces: list[tuple[bool, float, str]] = []
    for ts in found.values():
        ts.sort(key=lambda t: t.x0)
        for side in (False, True):
            part = [t for t in ts if (t.x0 >= right) == side]
            if part:
                pieces.append((side, min(q.y0 for q in part), line_text(part)))
    return [text for _, _, text in sorted(pieces, key=lambda e: (e[0], e[1]))]


def section(kind: str) -> dict[tuple[str, str], int]:
    """The seats 1995 section 5 prints for a body, by (district, acronym)."""
    reader = Listing("LISTA" if kind == "national" else None)
    substitutes = Listing()
    for number in SECTION[kind]:
        image = _image(number)
        if kind == "parlacen":
            _parlacen(image, reader, substitutes)
            continue
        for line in page_lines(image):
            reader.feed(line)
    if kind != "parlacen":
        return reader.counts
    # Each titular deputy has a substitute, and a name that wraps onto a second line counts twice on one side.
    return {k: min(n, substitutes.counts.get(k, n)) for k, n in reader.counts.items()}


def _parlacen(image: Image.Image, reader: Listing, substitutes: Listing) -> None:
    """The PARLACEN page: its titular deputies in the left column and their substitutes in the right, under each
    centered heading."""
    rows = rows_of(read_tokens(image))
    starts = Counter(round(t.x0 / 10) * 10 for r in rows for t in r[:2] if len(t.text) >= 3 and t.text.isupper())
    left = starts.most_common(1)[0][0]
    for row in rows:
        words = [t for t in row if t.x0 >= left - 15]
        if not words:
            continue
        if words[0].x0 > left + 60:
            reader.feed(line_text(words))
            substitutes.feed(line_text(words))
            continue
        name = [words[0]]
        for t in words[1:]:
            if t.x0 - name[-1].x1 > 55:
                break
            name.append(t)
        reader.feed(line_text(name))
        rest = words[len(name) :]
        if rest:
            substitutes.feed(line_text(rest))


# Municipal corporations ---------------------------------------------------------------------------------------------


def _letters(dark: np.ndarray, tall: int = 45, wide: int = 120) -> np.ndarray:
    """Only the strokes the size of letters: no rules, frames, or screens."""
    labels, count = ndimage.label(dark)
    keep = np.zeros(count + 1, dtype=bool)
    for i, found in enumerate(ndimage.find_objects(labels)):
        if found is None:
            continue
        h = found[0].stop - found[0].start
        w = found[1].stop - found[1].start
        if 5 <= h <= tall and w <= wide:
            keep[i + 1] = True
    return keep[labels]


def _merge(spans: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _bands(profile: np.ndarray, floor: float = 2) -> list[tuple[int, int]]:
    """The printed lines of a column of text: a band of rows of ink, cut into lines where it holds more than one."""
    bands = runs(profile > floor)
    sizes = sorted(b - a for a, b in bands if b - a >= 15)
    if not sizes:
        return []
    height = sizes[len(sizes) // 2]
    out: list[tuple[int, int]] = []
    for a, b in bands:
        if b - a < 10:
            continue
        count = max(1, round((b - a) / (height * 1.18)))
        step = (b - a) / count
        out.extend((round(a + i * step), round(a + (i + 1) * step)) for i in range(count))
    return out


def recognize(image: Image.Image, whitelist: str, scale: int = 3) -> str:
    """One cell's text by Tesseract, as a single line."""
    image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
    padded = Image.new("L", (image.width + 40, image.height + 40), 255)
    padded.paste(image, (20, 20))
    buffer = io.BytesIO()
    padded.save(buffer, "PNG")
    config = ["-c", f"tessedit_char_whitelist={whitelist}"] if whitelist else []
    out = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng", *config],
        input=buffer.getvalue(),
        capture_output=True,
        check=False,
    )
    # Tesseract fails on a crop too small to hold a line; it holds no text.
    return " ".join(out.stdout.decode().split()) if out.returncode == 0 else ""


ACRONYM_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789- "


class Row(NamedTuple):
    """One row of a corporation box as read: its acronym by cell and by line, and how wide its post is."""

    cell: str
    line: str
    post: int
    """The post's width in pixels, which tells a single letter (a titular post) from two (a substitute)."""
    text: str = ""
    """The post's letters as read, for notes."""


def _box_top(image: Image.Image) -> int:
    """The y just under the heading of a 1995 corporation box, where its rows begin."""
    height, width = image.height, image.width
    y0 = int(height * 0.74)
    found = read_tokens(image.crop((int(width * 0.1), y0, int(width * 0.9), int(height * 0.94))), 6)
    heading = [t for t in found if re.match(r"(?i)^\W*(ADJ|DJU|CORPO)", t.text)]
    if heading:
        return y0 + max(t.y1 for t in heading[:2]) + 6
    return int(height * 0.84)


def posts_1995(number: int) -> list[Row]:
    """Every row of a 1995 municipal page's corporation box, in reading order.

    The box prints its rows in groups across the page: an acronym, then a post and the holder's name, with the
    acronym set apart from the post. Each row is read whole by Tesseract and its acronym again by itself, and the
    width of its post tells a titular post (one letter) from a substitute's (two). A page whose blocks do not pair
    into groups of an acronym and a post is read as no rows.
    """
    image = straightened(path(1995), number, DPI).image.convert("L")
    gray = np.asarray(image)
    height, width = gray.shape
    top, bottom = _box_top(image), int(height * 0.965)
    mask = _letters(gray[top:bottom] < 140)
    mask[:, : int(width * 0.04)] = False
    blocks = [b for b in _merge(runs(mask.sum(axis=0) >= 3), 18) if b[1] - b[0] > 20]
    if len(blocks) % 2:
        # A stray mark past the last group, or the page's number.
        blocks = blocks[:-1] if blocks[-1][1] - blocks[-1][0] < 60 else blocks[1:]
    found: list[Row] = []
    for acr, rest in zip(blocks[0::2], blocks[1::2], strict=True):
        for y0, y1 in _bands(mask[:, acr[0] : acr[1]].sum(axis=1)):
            if y1 - y0 < 12:
                continue
            row = image.crop((acr[0] - 6, top + y0 - 4, rest[1] + 6, top + y1 + 4))
            line = recognize(row, "", 2)
            if re.search(r"(?i)ADJUD|CORPORAC|MUNICIP", line):
                continue
            cell = image.crop((max(acr[0] - 4, 0), top + y0 - 3, acr[1] + 4, top + y1 + 3))
            reads = "|".join([recognize(cell, ACRONYM_CHARS, 3), recognize(cell, ACRONYM_CHARS, 4)])
            span = _merge(runs(mask[y0:y1, rest[0] : rest[0] + 70].sum(axis=0) > 0), 8)
            post = span[0][1] - span[0][0] if span else 0
            found.append(Row(reads, line, post))
    return found


VERSION = 8
"""Bumped when the reading of a box changes, so its cached readings are redone."""


def _cached(year: int, number: int, read: Callable[[int], list[Row]]) -> list[Row]:
    target = CACHE / "seats" / str(year) / f"{number}.json"
    if target.exists():
        stored = json.loads(target.read_text())
        if stored.get("version") == VERSION:
            return [Row(*r) for r in stored["rows"]]
    rows = read(number)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": VERSION, "rows": rows}))
    return rows


def titular(row: Row) -> bool:
    """Whether a row is a titular post: its post is one letter wide, where a substitute's are two."""
    return 5 <= row.post < 15


def heads(row: Row) -> list[str]:
    """The readings of a row's acronym: those of its cell, then, where it has none, the words its line starts with."""
    reads = [r for r in row.cell.split("|") if r.strip()]
    if reads:
        return reads
    words = re.sub(r"[^A-Z0-9\- ]", " ", row.line.upper()).split()
    return [" ".join(words[:n]) for n in (1, 2)]


def held(rows: list[Row], columns: list[str]) -> Counter[str] | None:
    """A corporation's titular seats by organization, or None where a row's organization cannot be read."""
    seats: Counter[str] = Counter()
    if any(not 5 <= r.post < 25 for r in rows):
        return None
    if not rows or titular(rows[-1]) or not any(r.post >= 15 for r in rows[:-1]):
        # A box ends with its substitute councilors, after the syndic's; a box that does not has lost a group.
        return None
    for row in rows:
        if not titular(row):
            continue
        found = next((r for text in heads(row) if text and (r := resolve(text, columns))), None)
        if found is None:
            return None
        seats[found] += 1
    return seats


def read_municipal(year: int) -> dict[str, list[Row]]:
    """Every 1995 municipal page's corporation box, by municipality code."""
    from ea.sources.gt import memoria_scan as scan

    pages = [(s.page, code) for s, kind, _, code in scan.places(year) if kind in ("municipality", "central")]
    with ThreadPoolExecutor(4) as pool:
        boxes = list(pool.map(lambda pc: _cached(year, pc[0], posts_1995), pages))
    return {code: rows for (_, code), rows in zip(pages, boxes, strict=True)}


EXPECTED = {1995: {"national": 16, "parlacen": 20}}
"""The seats of each body that a year's memoria adjudicates by list, which its lists must add up to."""


def apply(year: int, contests: dict[str, base.Contest]) -> None:
    """Set the seats of every contest a year's adjudications cover."""
    LEFT[year] = []
    if year == 1999:
        apply_1999(contests)
        return
    _apply_section(contests)
    municipal = read_municipal(year)
    done = 0
    for code, rows in municipal.items():
        contest = contests.get(f"municipal:{code}")
        if contest is None:
            continue
        seats = held(rows, contest.columns)
        if not seats or sum(seats.values()) < 3:
            LEFT[year].append(f"municipal:{code}: corporation box not read to its organizations")
            continue
        contest.seats = dict(seats)
        contest.notes.append(
            "Seats are the titular posts (mayor, trustees, and councilors) the memoria's corporation box leads with "
            "this organization's acronym, read by OCR from the box."
        )
        done += 1
    print(f"{year}: seats for {done} of {len(municipal)} corporations, {len(LEFT[year])} left out", flush=True)


def _apply_section(contests: dict[str, base.Contest]) -> None:
    for kind in ("national", "parlacen", "district"):
        found = section(kind)
        if kind in EXPECTED[1995] and sum(found.values()) != EXPECTED[1995][kind]:
            raise ValueError(f"1995 {kind}: {sum(found.values())} seats listed, not {EXPECTED[1995][kind]}")
        if kind == "district" and sum(found.values()) != 64:
            raise ValueError(f"1995 districts: {sum(found.values())} seats listed, not 64")
        for (district, acronym), count in found.items():
            key_ = kind if kind != "district" else f"district:{district}"
            contest = contests[key_]
            wanted = resolve(acronym, contest.columns)
            if wanted is None:
                raise ValueError(f"1995 {key_}: seats for {acronym}, who is not on its ballot")
            contest.seats[wanted] = contest.seats.get(wanted, 0) + count
        for contest in contests.values():
            if contest.kind == kind and contest.seats:
                contest.notes.append(
                    "Seats are counted from the memoria's list of the elected, which names each deputy under the "
                    "organization that won the seat."
                )


# 1999: the national list ----------------------------------------------------------------------------------------------

NATIONAL_1999 = 47
"""The PDF page that lists the 22 elected of the national list, one row each, led by the organization's acronym."""


def national_1999() -> list[str]:
    """The acronym on each row of the 1999 national list's page, from top to bottom, as read."""
    image = straightened(path(1999), NATIONAL_1999, 300).image.convert("L")
    gray = np.asarray(image)
    height, width = gray.shape
    top = int(height * 0.28)
    inner = gray[top : int(height * 0.8), int(width * 0.3) : int(width * 0.55)]
    bar = runs((inner < 140).mean(axis=0) > 0.5)
    x0, x1 = max(bar, key=lambda b: b[1] - b[0])
    x0 += int(width * 0.3)
    x1 += int(width * 0.3)
    names = (gray[top : int(height * 0.8), x1 + 40 : x1 + 700] < 100).sum(axis=1)
    column = 255 - gray[top : int(height * 0.8), x0:x1]
    found: list[str] = []
    for y0, y1 in _bands(names, floor=3):
        cell = Image.fromarray(column[max(y0 - 4, 0) : y1 + 4])
        found.append(re.sub(r"[^A-Z\-]", "", recognize(cell, "ABCDEFGHIJKLMNOPQRSTUVWXYZ-", 3)))
    return found


def order(reads: list[str], columns: list[str]) -> list[str]:
    """A list's organizations row by row, where each organization holds one run of rows.

    Each row's reading is scored against every organization on the ballot, and the sequence that costs least while
    giving each organization at most one run is taken.
    """
    parties = sorted({label(c) for c in columns if label(c) not in base.ACCOUNTS})
    cost = [
        [
            1
            - max(
                SequenceMatcher(None, label(r), p).ratio(), SequenceMatcher(None, label(r), p[: len(label(r))]).ratio()
            )
            for p in parties
        ]
        for r in reads
    ]
    best: dict[tuple[int, frozenset[int]], tuple[float, list[int]]] = {(-1, frozenset()): (0.0, [])}
    for i in range(len(reads)):
        step: dict[tuple[int, frozenset[int]], tuple[float, list[int]]] = {}
        for (last, used), (total, path_) in best.items():
            for j in range(len(parties)):
                if j != last and j in used:
                    continue
                key_ = (j, used | {j})
                value = (total + cost[i][j], [*path_, j])
                if key_ not in step or value[0] < step[key_][0]:
                    step[key_] = value
        best = step
    _, chosen = min(best.values(), key=lambda v: v[0])
    return [parties[j] for j in chosen]


def apply_1999(contests: dict[str, base.Contest]) -> None:
    """The seats of the 1999 national list, from the memoria's page of its elected.

    The boxes on each department's and municipality's results page set their acronyms over a screen too coarse for
    Tesseract to read, so their seats are left out.
    """
    national = contests["national"]
    found = order(national_1999(), national.columns)
    if len(found) != 22:
        raise ValueError(f"1999 national list: {len(found)} rows, not 22")
    national.seats = dict(Counter(found))
    national.notes.append(
        "Seats are the rows of the memoria's list of the elected deputies of the national list, each led by the "
        "organization's acronym, read by OCR."
    )
    LEFT[1999] = [
        "district:*, municipal:*: the adjudication boxes of the results pages (district deputies, corporations)"
    ]
