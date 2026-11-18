"""Reads the Central Elections Commission's 2026 local election PDFs into tables.

Every report is a stack of pages, one local authority to a page or more, with a header (the district and the local
authority, the seats, the electors, and the ballots cast) and one table. The Arabic text is read by glyph (see
`arabic_pdf`); the PDFs of acclaimed authorities print their digits in a substitution alphabet, undone here.
"""

import itertools
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pymupdf

from ea.sources.ps import arabic_pdf
from ea.sources.ps.arabic_pdf import Line, clean

DIGITS = {chr(ord("M") + n): str(n) for n in range(10)} | {"I": ",", "L": "/", "J": "-"}


class Box(NamedTuple):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class Cell(NamedTuple):
    text: str
    box: Box


class Council(NamedTuple):
    district: str
    authority: str
    seats: int
    registered: Cell
    cast: Cell
    first_page: int
    rows: list[dict[str, Cell]]
    lists: list[dict[str, Cell]]
    winners: list[dict[str, Cell]]


def number(text: str) -> int:
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        raise ValueError(f"no number in {text!r}")
    return int(digits)


def cipher(text: str) -> str:
    """Digits of an acclamation PDF, which prints them as letters of a substitution alphabet."""
    return "".join(DIGITS.get(ch, ch) for ch in text)


def box(line: Line) -> Box:
    return Box(line.page, line.x0, line.y0, line.x1, line.y1)


def _pages(path: Path) -> dict[int, list[Line]]:
    pages: dict[int, list[Line]] = defaultdict(list)
    for line in arabic_pdf.lines(path):
        pages[line.page].append(line)
    return pages


def _rows(
    lines: list[Line], centers: dict[str, float], keys: tuple[str, ...] = ("votes", "result")
) -> list[dict[str, Cell]]:
    """Groups the lines of a table by row and assigns each to the nearest column by its center."""
    ordered = sorted(lines, key=lambda line: (line.y0 + line.y1) / 2)
    rows: list[list[Line]] = []
    for line in ordered:
        middle = (line.y0 + line.y1) / 2
        if rows and middle - (rows[-1][0].y0 + rows[-1][0].y1) / 2 < 9:
            rows[-1].append(line)
        else:
            rows.append([line])
    out: list[dict[str, Cell]] = []
    for row in rows:
        grouped: dict[str, list[Line]] = defaultdict(list)
        for line in row:
            column = min(centers, key=lambda name: abs(centers[name] - (line.x0 + line.x1) / 2))
            grouped[column].append(line)
        cells: dict[str, Cell] = {}
        for column, parts in grouped.items():
            parts.sort(key=lambda part: -part.x1)
            text = parts[0].text
            for before, after in itertools.pairwise(parts):
                text += ("" if before.x0 - after.x1 < 1.5 else " ") + after.text
            cells[column] = Cell(
                clean(text),
                Box(
                    parts[0].page,
                    min(p.x0 for p in parts),
                    min(p.y0 for p in parts),
                    max(p.x1 for p in parts),
                    max(p.y1 for p in parts),
                ),
            )
        if out and keys and not any(key in cells for key in keys):
            for column, cell in cells.items():  # a name wrapped onto a second line of its cell
                before = out[-1].get(column)
                out[-1][column] = Cell(f"{before.text} {cell.text}", before.box) if before else cell
            continue
        out.append(cells)
    return out


def _header(lines: list[Line]) -> dict[str, Cell]:
    found: dict[str, Cell] = {}
    for line in lines:
        text = clean(line.text)
        for label, name in (
            ("الدائرة الانتخابية:", "district"),
            ("الهيئة المحلية:", "authority"),
            ("عدد المقاعد:", "seats"),
            ("عدد الناخبين:", "registered"),
            ("عدد المقترعين:", "cast"),
            ("نسبة الاقتراع:", "turnout"),
        ):
            if text.startswith(label):
                found[name] = Cell(text.removeprefix(label).strip(), box(line))
    return found


def _centers(lines: list[Line], labels: dict[str, str]) -> tuple[dict[str, float], float]:
    found: dict[str, float] = {}
    top = 0.0
    for line in lines:
        text = clean(line.text)
        for name, label in labels.items():
            if text == label and name not in found:
                found[name] = (line.x0 + line.x1) / 2
                top = max(top, line.y1)
    missing = set(labels) - set(found)
    if missing:
        raise ValueError(f"page {lines[0].page}: no table header {missing}")
    return found, top


def councils(path: Path, kind: str) -> list[Council]:
    """The councils of a results PDF: `votes` (candidate rows), `lists` (list rows and winners), or `villages`."""
    out: list[Council] = []
    for number_, lines in sorted(_pages(path).items()):
        head = _header(lines)
        if kind == "villages" or kind == "votes":
            labels = {
                "result": "النتيجة",
                "votes": "الأصوات" if kind == "votes" else "عدد الأصوات",
                "name": "اسم المرشح/ة",
            }
            if kind == "votes":
                labels["list"] = "اسم القائمة"
            centers, top = _centers(lines, labels)
            body = [line for line in lines if line.y0 > top - 1 and not line.text.startswith(("OM", "NO"))]
            body = [line for line in body if not _is_header(line)]
            rows = _rows(body, centers)
            current = Council(
                head["district"].text,
                head["authority"].text,
                number(head["seats"].text),
                _figure(head["registered"]),
                _figure(head["cast"]),
                number_,
                rows,
                [],
                [],
            )
        else:
            current = _list_page(number_, lines, head)
        previous = out[-1] if out else None
        if previous and (previous.district, previous.authority) == (current.district, current.authority):
            previous.rows.extend(current.rows)
            previous.lists.extend(current.lists)
            previous.winners.extend(current.winners)
        else:
            out.append(current)
    return out


def _figure(cell: Cell) -> Cell:
    return Cell(str(number(cell.text)), cell.box)


def _is_header(line: Line) -> bool:
    text = clean(line.text)
    return text.startswith(
        ("الدائرة الانتخابية:", "الهيئة المحلية:", "عدد المقاعد:", "عدد الناخبين:", "عدد المقترعين:", "نسبة الاقتراع:")
    ) or text in {
        "النتيجة",
        "الأصوات",
        "عدد الأصوات",
        "اسم المرشح/ة",
        "اسم القائمة",
        "النتائج حسب أسماء المرشحين والمرشحات",
    }


def _list_page(page: int, lines: list[Line], head: dict[str, Cell]) -> Council:
    columns, top = _centers(lines, {"votes": "عدد الأصوات", "type": "صفة الترشح", "name": "اسم القائمة"})
    split = next((line for line in lines if clean(line.text) == "اسماء الفائزين والفائزات"), None)
    if split is None:
        raise ValueError(f"page {page}: no winners table")
    lists_part = [line for line in lines if top - 1 < line.y0 < split.y0 and not _is_header(line)]
    lists_part = [line for line in lists_part if not clean(line.text) in {"عدد", "المرشحين", "المقاعد"}]
    seat_centers = {"seats": _seat_center(lines), "candidates": _candidate_center(lines), **columns}
    rows = _rows(lists_part, seat_centers, ("seats", "votes"))
    wins = [line for line in lines if line.y0 > split.y1 and not _is_header(line)]
    win_columns, win_top = _centers(lines, {"list": "اسم القائمة", "name": "اسم الفائز/ة"}) if False else ({}, 0)
    names = [line for line in lines if clean(line.text) == "اسم الفائز/ة"]
    lists_label = [line for line in lines if clean(line.text) == "اسم القائمة" and line.y0 > split.y0]
    win_top = max(line.y1 for line in names)
    win_columns = {
        "list": (lists_label[0].x0 + lists_label[0].x1) / 2,
        "name": (names[0].x0 + names[0].x1) / 2,
    }
    wins = [line for line in lines if line.y0 > win_top - 1 and not _is_header(line)]
    winners = _rows(wins, win_columns, ())
    return Council(
        head["district"].text,
        head["authority"].text,
        number(head["seats"].text),
        _figure(head["registered"]),
        _figure(head["cast"]),
        page,
        [],
        rows,
        winners,
    )


def _seat_center(lines: list[Line]) -> float:
    line = next(line for line in lines if clean(line.text) == "المقاعد")
    return (line.x0 + line.x1) / 2


def _candidate_center(lines: list[Line]) -> float:
    line = next(line for line in lines if clean(line.text) == "المرشحين")
    return (line.x0 + line.x1) / 2


def plain_pages(path: Path) -> list[list[tuple[float, float, float, float, str]]]:
    """Words of a PDF whose own text map is sound, with boxes."""
    document = pymupdf.open(path)
    return [[(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")] for page in document]


class Acclaimed(NamedTuple):
    district: str
    authority: str
    seats: int
    registered: int
    page: int
    list_name: str | None
    list_type: str | None
    candidates: int | None
    winners: list[tuple[int, Cell]]


def acclaimed(path: Path, kind: str) -> list[Acclaimed]:
    """The authorities whose councils were elected without a vote: a list (`lists`) or individuals (`villages`)."""
    out: list[Acclaimed] = []
    for page, lines in sorted(_pages(path).items()):
        band = sorted((line for line in lines if abs((line.y0 + line.y1) / 2 - 104) < 12), key=lambda line: -line.x1)
        labels = [clean(line.text) for line in band]
        if labels[:1] != ["الدائرة الانتخابية"] or labels[2:3] != ["الهيئة المحلية"]:
            raise ValueError(f"page {page}: unexpected header {labels}")
        district, authority = clean(band[1].text), " ".join(clean(line.text) for line in band[3:])
        second = sorted((line for line in lines if abs((line.y0 + line.y1) / 2 - 141) < 8), key=lambda line: -line.x1)
        seats_label = next(i for i, line in enumerate(second) if clean(line.text) == "عدد المقاعد")
        seats = number(cipher(second[seats_label + 1].text))
        registered_label = next(i for i, line in enumerate(second) if clean(line.text) == "عدد الناخبين")
        registered = number(cipher(second[registered_label + 1].text))
        header = next(line for line in lines if clean(line.text) == "اسم الفائز")
        name_list = type_ = candidates = None
        if kind == "lists":
            row_top = next(line for line in lines if clean(line.text) == "اسم القائمة")
            row = [line for line in lines if row_top.y1 < line.y0 < header.y0 - 20]
            row.sort(key=lambda line: -line.x1)
            name_list = clean(row[0].text)
            type_ = clean(row[1].text)
            candidates = number(cipher(row[2].text))
        names = [line for line in lines if line.y0 > header.y1 and line.x1 < 530]
        index = [line for line in lines if line.y0 > header.y1 and line.x0 >= 530]
        wins = _rows(
            names + index,
            {"name": (header.x0 + header.x1) / 2, "index": 555.0},
            (),
        )
        winners = [(number(cipher(row["index"].text)), row["name"]) for row in wins]
        out.append(Acclaimed(district, authority, seats, registered, page, name_list, type_, candidates, winners))
    return out


class Turnout(NamedTuple):
    number: int
    district_en: str
    authority_en: str
    district: str
    authority: str
    percent: Decimal
    cast: int
    registered: int
    page: int
    box: Box


class Total(NamedTuple):
    percent: Decimal
    cast: int
    registered: int


def turnout(path: Path) -> tuple[list[Turnout], Total]:
    """Per-authority turnout of the polled authorities, and the printed total, from the Commission's own table."""
    out: list[Turnout] = []
    total: Total | None = None
    for page, words in enumerate(plain_pages(path), 1):
        anchors = [w for w in words if w[0] > 530 and re.fullmatch(r"\d+", w[4])]
        middle = lambda w: (w[1] + w[3]) / 2
        groups: dict[int, list[tuple[float, float, float, float, str]]] = defaultdict(list)
        for w in words:
            if w[0] > 530 or w in anchors or (page == 1 and anchors and w[3] < min(a[1] for a in anchors) - 2):
                continue
            if not anchors:
                continue
            nearest = min(range(len(anchors)), key=lambda i: abs(middle(anchors[i]) - middle(w)))
            if abs(middle(anchors[nearest]) - middle(w)) < 11:
                groups[nearest].append(w)
        for index, anchor in enumerate(anchors):
            row = sorted(groups[index], key=lambda w: (round(middle(w) / 6), w[0]))
            numbers = [w for w in row if re.fullmatch(r"[\d,]+", w[4]) and 300 < w[0] < 420]
            percent = next((w for w in row if w[4].endswith("%")), None)
            if percent is None:
                raise ValueError(f"page {page} row {anchor[4]}: {[w[4] for w in row]}")
            english = [w[4] for w in row if w[0] < 255 and re.search(r"[A-Za-z']", w[4]) and w[2] < 255]
            district_en = " ".join(w[4] for w in row if w[0] < 130 and re.search(r"[A-Za-z]", w[4]))
            authority_en = " ".join(w[4] for w in row if 130 <= w[0] < 255 and re.search(r"[A-Za-z']", w[4]))
            arabic = sorted(
                (w for w in row if w[0] > 380 and not re.fullmatch(r"[\d,]+", w[4])),
                key=lambda w: (round(middle(w) / 6), -w[0]),
            )
            district = min(arabic, key=lambda w: -w[0])[4] if arabic else ""
            authority = " ".join(w[4] for w in arabic if w[4] != district)
            del english
            out.append(
                Turnout(
                    int(anchor[4]),
                    district_en,
                    authority_en,
                    district,
                    authority,
                    Decimal(percent[4].rstrip("%")),
                    number(numbers[0][4]),
                    number(numbers[1][4]),
                    page,
                    Box(page, anchor[0], anchor[1], anchor[2], anchor[3]),
                )
            )
        totals = [w for w in words if w[4] == "Total"]
        if totals:
            y = middle(totals[0])
            row = sorted((w for w in words if abs(middle(w) - y) < 6), key=lambda w: w[0])
            figures = [w[4] for w in row if re.fullmatch(r"[\d,%]+", w[4])]
            total = Total(Decimal(figures[0].rstrip("%")), number(figures[1]), number(figures[2]))
    if total is None:
        raise ValueError("no total row in the turnout table")
    return out, total
