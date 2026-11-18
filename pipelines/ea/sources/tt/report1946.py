"""Reads the commission's scanned reports on the 1946 and 1956 Legislative Council elections and the 1961 and 1966
parliamentary elections.

Each report prints each constituency's figures in a summary table: Table 3 of the 1946 report, and Table 4 of the
1956 and 1961 reports, give the votes of every candidate with the rejected ballots and the votes cast. The file the
commission publishes for the 1966 report ends partway through, so only its first 63 pages survive; its summary
table is lost, but the polling station tables of Table 1 survive for 25 districts, and each district's last page
prints the district's total row (each candidate's votes, the rejected postal votes, the total votes, and the
electorate) under the candidates' names.

The tables are short, so their figures were read from the page by hand and recorded in `report1946.json`. The
scans are only images, so each figure's box on the page is found among the words Tesseract reads there: the figures
of a district are matched in the order the table prints them, and a figure Tesseract does not read takes a box
placed between the figures around it.

Page images are taken straight from the JPEG streams the PDFs embed, one per page, rather than rendered: the 1966
file is cut short and no renderer opens it, but its first 63 page images are whole.
"""

import csv
import io
import itertools
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from ea import raw

SOURCE = "tt.ebc"
UPLOADS = "https://ebctt.com/wp-content/uploads"
REPORTS = {
    1946: f"{UPLOADS}/Report-on-the-Legislative-Council-General-Elections-1946-1st-July-1946.pdf",
    1956: f"{UPLOADS}/Report-on-the-Legislative-Council-General-Elections-1956-24th-September-1956.pdf",
    1961: f"{UPLOADS}/Report-on-the-Parliamentary-General-Elections-1961-4th-December-1961.pdf",
    1966: f"{UPLOADS}/Report-on-the-Parliamentary-General-Elections-1966-7th-November-1966.pdf",
}
TRANSCRIPT = Path(__file__).with_name("report1946.json")
ENV = {"OMP_THREAD_LIMIT": "1", "PATH": "/usr/local/bin:/usr/bin:/bin"}

Box = tuple[float, float, float, float]

# What each figure of a district is, in the order the table prints it.
REGISTERED, BALLOTS, VALID, REJECTED = "registered", "ballots", "valid", "rejected"


@dataclass
class Entry:
    """One constituency's figures as the report prints them, with the page and box of each."""

    district: str
    candidates: list[tuple[str, int, str | None]]
    registered: int | None
    ballots: int
    valid: int | None
    rejected: int | None
    # For each figure, keyed by a candidate's index or by one of the measures above: its page and box.
    cells: dict[int | str, tuple[int, Box]] = field(default_factory=dict)


def images(year: int) -> dict[int, Path]:
    """Each page's JPEG, by page number, cut from the PDF's image streams in the order its pages hold them."""
    folder = raw.parsed(SOURCE) / f"report{year}" / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    data = raw.cached(SOURCE, REPORTS[year]).path.read_bytes()
    found: dict[int, Path] = {}
    for number, start in enumerate(re.finditer(rb"/Subtype\s*/Image", data), start=1):
        stream = data.find(b"stream", start.end())
        if stream < 0:
            break
        begin = data.find(b"\xff\xd8", stream, stream + 16)
        end = data.find(b"endstream", begin)
        if begin < 0 or end < 0:
            break
        body = data[begin:end].rstrip(b"\r\n")
        if not body.endswith(b"\xff\xd9"):
            break
        target = folder / f"{number}.jpg"
        if not target.exists():
            target.write_bytes(body)
        found[number] = target
    return found


def words(year: int, page: int, path: Path) -> list[tuple[str, Box]]:
    """The words Tesseract reads on a page, with their boxes in the page image's pixels."""
    cache = raw.parsed(SOURCE) / f"report{year}" / f"{page}.json"
    if cache.exists():
        return [(text, (b[0], b[1], b[2], b[3])) for text, b in json.loads(cache.read_text())]
    found: list[tuple[str, Box]] = []
    with Image.open(path) as image:
        gray = image.convert("L")
    # The page as scanned, and enlarged twice over, which reads the worn typescript of 1961 and 1966 far better.
    for scale in (1, 2):
        picture = gray if scale == 1 else gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        picture.save(buffer, "PNG")
        for psm in ("6", "11"):
            out = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", psm, "tsv"],
                input=buffer.getvalue(),
                capture_output=True,
                check=True,
                env=ENV,
            ).stdout.decode()
            for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
                text = (row.get("text") or "").strip()
                if not text:
                    continue
                left, top, width, height = (float(row[k]) / scale for k in ("left", "top", "width", "height"))
                found.append((text, (left, top, left + width, top + height)))
    cache.write_text(json.dumps(found))
    return found


def _digits(text: str) -> str | None:
    """A figure's digits, or None for a word that is not a whole number (percentages carry a point)."""
    text = text.strip(".,;:'\"|[](){}")
    if not text or "." in text or not re.fullmatch(r"[\d,]+", text):
        return None
    return text.replace(",", "")


def _numbers(page: int, found: list[tuple[str, Box]]) -> list[tuple[int, str, Box]]:
    seen: set[tuple[str, int, int]] = set()
    out = []
    for text, box in found:
        digits = _digits(text)
        if digits is None:
            continue
        key = (digits, round(box[0] / 8), round(box[1] / 8))
        if key in seen:
            continue
        seen.add(key)
        out.append((page, digits, box))
    return out


def _like(digits: str, value: int) -> bool:
    """Whether a word Tesseract read is the figure: the same digits, or one digit lost, added, or misread."""
    text = str(value)
    if digits == text:
        return True
    if len(text) < 3:
        return False
    if len(digits) == len(text):
        return sum(a != b for a, b in zip(digits, text, strict=True)) == 1
    short, long = sorted((digits, text), key=len)
    if len(long) - len(short) != 1:
        return False
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def _between(before: tuple[int, Box] | None, after: tuple[int, Box] | None, width: Box) -> tuple[int, Box]:
    """A box for a figure Tesseract missed, placed between the figures printed before and after it."""
    if before is None and after is None:
        raise ValueError("no figure of the district was found on the page")
    if before is None or (after is not None and before[0] != after[0]):
        assert after is not None
        page, box = after
        return page, (width[0], box[1] - (box[3] - box[1]) - 4, width[2], box[1] - 4)
    page, box = before
    if after is None:
        return page, (width[0], box[3] + 4, width[2], box[3] + 4 + (box[3] - box[1]))
    low, high = box[3], after[1][1]
    return page, (width[0], low + 1, width[2], max(low + 2, min(high - 1, low + 30)))


def _column(year: int, entries: list[Entry], pages: dict[int, Path]) -> None:
    """Boxes for the 1946, 1956, and 1961 summary tables, where a district's figures run down one column."""
    # Each district's figures come after the previous district's, so the search starts below them. In 1946 the
    # constituency table follows a table of electors and ballots by constituency on its first page, and starts at
    # the heading of its Totals column.
    first = _pages(year)[0]
    heads = [box[1] for text, box in words(year, first, pages[first]) if text.strip(".,") == "Totals"]
    floor = (first, min(heads)) if year == 1946 and heads else (0, 0.0)
    # The column of figures, found where the districts' votes cast are read exactly; percentages and majorities
    # stand in columns to its right.
    totals = {str(entry.ballots) for entry in entries}
    # The figures are set flush right, so their right edges line up; each page is scanned at its own offset.
    edges: dict[int, float] = {}
    for number in set(_pages(year)):
        found = sorted(
            box[2]
            for _, digits, box in _numbers(number, words(year, number, pages[number]))
            if digits in totals and (number, box[1]) > floor
        )
        if found:
            edges[number] = found[len(found) // 2]
    for entry, page in zip(entries, _pages(year), strict=True):
        order: list[tuple[int | str, int]] = []
        if year == 1946:
            assert entry.registered is not None and entry.valid is not None and entry.rejected is not None
            order = [(REGISTERED, entry.registered), (BALLOTS, entry.ballots), (VALID, entry.valid)]
            order += [(REJECTED, entry.rejected), *((i, c[1]) for i, c in enumerate(entry.candidates))]
        elif year == 1956:
            order = [(BALLOTS, entry.ballots), *((i, c[1]) for i, c in enumerate(entry.candidates))]
            if entry.rejected is not None:
                order.append((REJECTED, entry.rejected))
        else:
            order = [(i, c[1]) for i, c in enumerate(entry.candidates)]
            if entry.rejected is not None:
                order.append((REJECTED, entry.rejected))
            order.append((BALLOTS, entry.ballots))
        tokens: list[tuple[int, str, Box]] = []
        # A district's figures run over to the next page only where the next district starts there.
        for number in (page, page + 1):
            if number in pages and (number == page or number in _pages(year)):
                tokens += sorted(_numbers(number, words(year, number, pages[number])), key=lambda t: (t[2][1], t[2][0]))
        tokens = [t for t in tokens if (t[0], t[2][1]) > floor and abs(t[2][2] - edges.get(t[0], t[2][2])) < 45]
        # The figures of a district stand below its heading, in one column; match them in order.
        cursor = 0
        found: dict[int | str, tuple[int, Box]] = {}
        last: tuple[int, float] | None = floor if floor[0] else None
        for key, value in order:
            near: list[int] = []
            for index in range(cursor, len(tokens)):
                number, _, box = tokens[index]
                # A district's figures stand close together, carrying over at most to the head of the next page.
                if last is not None and not (
                    (number == last[0] and box[1] - last[1] < 260)
                    or (number == last[0] + 1 and box[1] < 900 and last[1] > 1700)
                ):
                    break
                near.append(index)
            # The word that reads as the figure, or failing that the first that reads nearly as it.
            pick = next((i for i in near if tokens[i][1] == str(value)), None)
            if pick is None:
                pick = next((i for i in near if _like(tokens[i][1], value)), None)
            if pick is not None:
                number, _, box = tokens[pick]
                found[key] = (number, box)
                cursor = pick + 1
                last = (number, box[1])
        if found:
            floor = max((page_, box[1]) for page_, box in found.values())
        _fill(entry, order, found)


def _fill(entry: Entry, order: list[tuple[int | str, int]], found: dict[int | str, tuple[int, Box]]) -> None:
    """Every figure's box: those Tesseract read, and the others placed between them."""
    if not found:
        raise ValueError(f"no figure of {entry.district} was found")
    lefts = sorted(box[0] for _, box in found.values())
    rights = sorted(box[2] for _, box in found.values())
    width: Box = (lefts[len(lefts) // 2], 0, rights[len(rights) // 2], 0)
    keys = [key for key, _ in order]
    for position, key in enumerate(keys):
        if key in found:
            entry.cells[key] = found[key]
            continue
        before = next((found[k] for k in reversed(keys[:position]) if k in found), None)
        after = next((found[k] for k in keys[position + 1 :] if k in found), None)
        entry.cells[key] = _between(before, after, width)


def _row(year: int, entries: list[Entry], pages: dict[int, Path]) -> None:
    """Boxes for the 1966 total rows: each district's figures run across one line near the foot of its last page.

    The scan is too worn for Tesseract to read the row's figures reliably, so each figure's box is its cell in the
    table's grid. The grid is read from the table's rules: the vertical rules that run down the table, of which the
    scan loses some, from the table's right border leftwards; and the row is placed by its TOTAL label where
    Tesseract reads it, or else between the horizontal rules above and below it, the last row above the returning
    officer's certificate. The candidates, the rejected postal votes, and the
    total votes fill the columns before the electorate.
    """
    for entry, page in zip(entries, _pages(year), strict=True):
        keys: list[int | str] = [*range(len(entry.candidates)), REJECTED, BALLOTS, REGISTERED]
        found = words(year, page, pages[page])
        certificate = [box[1] for text, box in found if text.lower().startswith(("certif", "hereby", "foregoing"))]
        if not certificate:
            raise ValueError(f"no certificate under {entry.district}'s table on page {page}")
        foot = int(min(certificate))
        with Image.open(pages[page]) as image:
            dark = np.asarray(image.convert("L")) < 128
        # Rules down the rows just above the total; over a longer span a skewed scan's rules lean out of line.
        rules = _rules(dark[max(700, foot - 360) : foot - 40])
        gaps = [b - a for a, b in itertools.pairwise(rules) if 45 <= b - a <= 90]
        pitch = sorted(gaps)[len(gaps) // 2] if gaps else 77
        bounds = _bounds(rules, len(keys), pitch)
        columns = list(itertools.pairwise(bounds))
        values = [*(c[1] for c in entry.candidates), entry.rejected or 0, entry.ballots, entry.registered or 0]
        tokens = [t for t in _numbers(page, found) if t[2][1] < foot and t[2][0] > 600]
        labels = [
            (box[1] + box[3]) / 2
            for text, box in found
            if text.upper().startswith("TOTA") and foot - 160 < box[1] < foot and box[0] < 900
        ]
        middle = max(labels) if labels else _total(dark, foot, int(bounds[0]), int(bounds[-1]))
        # Where Tesseract reads three or more of the row's figures on one line, that line is the row.
        best = 0
        for _, _, box in tokens:
            centre = (box[1] + box[3]) / 2
            line = [t for t in tokens if abs((t[2][1] + t[2][3]) / 2 - centre) < 12]
            hits = sum(any(_like(t[1], value) for t in line) for value in values if value >= 10)
            if hits >= 3 and hits > best:
                best, middle = hits, centre
        read: dict[int | str, Box | None] = {}
        for key, value, (left, end) in zip(keys, values, columns, strict=True):
            # A figure Tesseract reads in its cell keeps the box of the word it read.
            word = next(
                (
                    t[2]
                    for t in tokens
                    if value >= 10
                    and _like(t[1], value)
                    and abs((t[2][1] + t[2][3]) / 2 - middle) < 16
                    and left - 10 <= t[2][0]
                    and t[2][2] <= end + 10
                ),
                None,
            )
            read[key] = word
        for key, (left, end) in zip(keys, columns, strict=True):
            entry.cells[key] = (page, read[key] or (float(left + 2), middle - 15, float(end - 2), middle + 15))


def _bounds(rules: list[int], count: int, pitch: float) -> list[float]:
    """The left and right edges of the table's last `count` columns, from its right border leftwards.

    The electorate's column, the last, is the widest. A gap between two rules found that spans several columns
    is split evenly, since the worn scan loses some rules, and columns past the last rule found are set a pitch
    apart.
    """
    border = float(max((x for x in rules if 1380 < x < 1660), default=max(rules)))
    previous = max((x for x in rules if x < border), default=0)
    if border - previous < pitch * 1.35:
        # The rule found last is the one before the electorate: the scan lost the table's right border.
        border += pitch * 1.6
    bounds = [border]
    for x in sorted((x for x in rules if x < border - 20), reverse=True):
        if len(bounds) > count:
            break
        gap = bounds[-1] - x
        if gap < pitch * 0.7:
            continue
        start = bounds[-1]
        if len(bounds) == 1:
            if gap <= pitch * 2.4:
                bounds.append(float(x))
                continue
            # The rule before the electorate was lost: the electorate's column is some 1.8 pitches wide.
            start = start - pitch * 1.8
            bounds.append(start)
            gap = start - x
        cells = max(1, round(gap / pitch))
        for k in range(1, cells + 1):
            bounds.append(start - gap * k / cells)
    while len(bounds) <= count:
        bounds.append(bounds[-1] - pitch)
    return sorted(bounds[: count + 1])


def _rules(span: np.ndarray) -> list[int]:
    """The x of each vertical rule that runs down most of a span of the table, at the strictest darkness that
    finds the table's rules; the worn scan breaks some of them."""
    found: list[int] = []
    thick = np.maximum(span, np.roll(span, 1, axis=1)).mean(axis=0)
    for threshold in (0.6, 0.45, 0.3):
        found = []
        for x in np.flatnonzero(thick > threshold).tolist():
            if found and x - found[-1] < 10:
                found[-1] = x
            else:
                found.append(x)
        if len([x for x in found if x > 850]) >= 3:
            break
    return found


def _total(dark: np.ndarray, foot: int, left: int, right: int) -> float:
    """The middle of the total row: between the rule above it and the double rule below, the last two rules
    across the figure columns above the certificate."""
    rows = dark[foot - 200 : foot, left:right].mean(axis=1)
    lines: list[list[int]] = []
    for y in np.flatnonzero(rows > 0.5).tolist():
        if lines and y - lines[-1][-1] <= 3:
            lines[-1].append(y)
        else:
            lines.append([y])
    if len(lines) >= 2 and 30 < lines[-1][0] - lines[-2][-1] < 90:
        return foot - 200 + (lines[-2][-1] + lines[-1][0]) / 2
    if lines:
        return foot - 200 + lines[-1][0] - 26.0
    return foot - 55.0


def _pages(year: int) -> list[int]:
    return [int(row["page"]) for row in json.loads(TRANSCRIPT.read_text())[str(year)]]


def entries(year: int) -> list[Entry]:
    """Each constituency's figures, as read from the report, with the page and box of each figure."""
    rows = json.loads(TRANSCRIPT.read_text())[str(year)]
    found = [
        Entry(
            district=row["district"],
            candidates=[(c[0], int(c[1]), c[2] if len(c) > 2 else None) for c in row["candidates"]],
            registered=row.get("registered"),
            ballots=int(row["ballots"]),
            valid=row.get("valid"),
            rejected=row.get("rejected"),
        )
        for row in rows
    ]
    pages = images(year)
    if year == 1966:
        _row(year, found, pages)
    else:
        _column(year, found, pages)
    return found


def sizes(year: int) -> dict[int, tuple[int, int]]:
    """Each page's width and height in pixels, for the pages the figures are read from."""
    pages = images(year)
    out = {}
    for entry in entries(year):
        for page, _ in entry.cells.values():
            if page not in out:
                with Image.open(pages[page]) as image:
                    out[page] = image.size
    return out


def matched(year: int) -> tuple[int, int]:
    """How many figures Tesseract read where the transcript has them, out of all the figures, for the report."""
    total = hits = 0
    for entry in entries(year):
        for page, box in entry.cells.values():
            total += 1
            words_ = words(year, page, images(year)[page])
            hits += any(_digits(text) is not None and b == box for text, b in words_)
    return hits, total
