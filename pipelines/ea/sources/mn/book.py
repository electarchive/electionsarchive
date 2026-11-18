"""The 2012 State Great Khural election by constituency from the Commission's book of results (the combined results
of the 1992-2020 elections). The book prints, for each constituency, a table of the votes of every candidate: its
columns are the soums of a province, or the polling sections of a district, and its rows are the registered voters,
the ballots, and the votes and share of each candidate. The tables are drawn sideways in the pages for provinces and
upright, as a pair of facing pages, for the capital; this module reads the text with its position on the page."""

import re
from dataclasses import dataclass, field

import pymupdf

from ea import raw
from ea.sources.mn import sheets

BOOK = "c693729c"
NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
NUMBERS = re.compile(r"^\d+(?:\.\d+)?(?:\s+\d+(?:\.\d+)?)+$")
RUN = re.compile(r"^\d+\.\d+(?:\d+\.\d+)+$")
MOJIBAKE = re.compile("[\u00c0-\u00ff]")
# Soum names the book wraps over two lines, and a soum it names beside a banner.
SOUMS = {
    "Улаан Баруунбаян-": "Баруунбаян-Улаан",
    "Зүүнбаян- Улаан": "Зүүнбаян-Улаан",
    "хайрхан Мөнх-": "Мөнххайрхан",
    "Эрдэнэ- бүрэн": "Эрдэнэбүрэн",
    "Чандмань- Өндөр": "Чандмань-Өндөр",
    "СУМД Ногооннуур": "Ногооннуур",
    "Хайрхан дулаан": "Хайрхандулаан",
}
SECTION = re.compile(r"^(\d+)-р хэсэг$")
# The first page of each constituency's table, then the pages that continue it.
PAGES: dict[int, list[int]] = {n: [240 + 2 * (n - 1)] for n in range(1, 19)}
PAGES.update(
    {
        19: [276],
        20: [278],
        21: [280, 281],
        22: [284, 285, 286, 287],
        23: [292, 293],
        24: [296, 297],
        25: [300, 301],
        26: [304, 305, 306, 307],
    }
)


@dataclass(frozen=True)
class Span:
    v: float
    u: float
    text: str
    u0: float
    u1: float
    exact: bool = True


@dataclass
class Column:
    u: float
    label: str
    figures: list[float] = field(default_factory=list)


@dataclass
class Page:
    number: int
    spans: list[Span]
    columns: list[Column]
    rows: list[list[float | None]]
    row_v: list[float]
    header_v: float | None


@dataclass
class Candidate:
    label: str
    party: str
    total: int = 0


@dataclass
class Unit:
    """One column of a constituency's table: a soum or a polling section."""

    label: str
    section: int | None
    registered: int
    voters: int
    valid: int
    invalid: int
    votes: list[int]
    derived: bool = False


@dataclass
class Detail:
    number: int
    candidates: list[Candidate]
    units: list[Unit]
    total: Unit | None
    misprints: list[str]


def _fix(text: str) -> str:
    """Letters the book's font encodes as Latin-1 characters, read as the Windows Cyrillic letters they stand for."""
    return MOJIBAKE.sub(lambda m: bytes([ord(m[0])]).decode("cp1251"), text)


def _split_run(text: str) -> list[str]:
    """The shares of a row printed without a space between them: two decimals each, or one for 100.0."""
    for places in (2, 1):
        tokens = re.findall(rf"\d+\.\d{{{places}}}", text)
        if "".join(tokens) == text and all(float(t) <= 100 and not re.match(r"0\d", t) for t in tokens):
            return tokens
    raise ValueError(f"unreadable run of figures {text!r}")


def _spans(page: pymupdf.Page) -> list[Span]:
    """The text of a page in table coordinates: v runs down the table and u along its rows, whichever way the page is
    turned."""
    height = page.rect.height
    raw_spans: list[tuple[tuple[float, float, float, float], str, tuple[float, float]]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    raw_spans.append((span["bbox"], _fix(span["text"].strip()), line["dir"]))
    sideways = any("ДЭЛГЭРЭНГҮЙ ДҮН" in t and round(d[1]) == -1 for _, t, d in raw_spans)
    out: list[Span] = []
    for (x0, y0, x1, y1), text, _ in raw_spans:
        if sideways:
            v, u, u0, u1 = (x0 + x1) / 2, height - (y0 + y1) / 2, height - y1, height - y0
        else:
            v, u, u0, u1 = (y0 + y1) / 2, (x0 + x1) / 2, x0, x1
        if (spaced := NUMBERS.match(text) and " " in text) or RUN.match(text):
            # a row printed as one run of text: spread its figures along the run
            tokens = text.split() if spaced else _split_run(text)
            total = sum(len(t) + 1 for t in tokens)
            offset = 0
            for token in tokens:
                centre = u0 + (offset + len(token) / 2) / total * (u1 - u0)
                out.append(Span(v, centre, token, centre, centre, False))
                offset += len(token) + 1
        else:
            out.append(Span(v, u, text, u0, u1))
    return sorted(out, key=lambda s: (s.v, s.u))


def _page(book: pymupdf.Document, number: int) -> Page:
    spans = _spans(book[number - 1])
    figures = [s for s in spans if NUMBER.match(s.text)]
    top = next((s.v for s in spans if s.text == "Үзүүлэлт"), None)
    floor = (top or 100) + 8
    clusters: list[list[Span]] = []
    for s in (s for s in figures if s.v > floor):
        if clusters and abs(clusters[-1][0].v - s.v) < 2.5:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    widest = max(clusters, key=lambda c: sum(s.exact for c_ in [c] for s in c_))
    positions = sorted(s.u for s in widest if s.exact)
    rows: list[list[float | None]] = []
    row_v: list[float] = []
    for cluster in clusters:
        cells: list[float | None] = [None] * len(positions)
        for s in cluster:
            nearest = min(range(len(positions)), key=lambda i: abs(positions[i] - s.u))
            if abs(positions[nearest] - s.u) < 4:
                cells[nearest] = float(s.text)
        if sum(c is not None for c in cells) >= len(positions) * 0.5:
            rows.append(cells)
            row_v.append(sum(s.v for s in cluster) / len(cluster))
    first_row = row_v[0]
    columns = []
    for u in positions:
        names = [
            s
            for s in spans
            if abs(s.u - u) < 6 and 118 <= s.v < first_row - 4 and s.text != "Үзүүлэлт" and not NUMBER.match(s.text)
        ]
        columns.append(Column(u, " ".join(s.text for s in sorted(names, key=lambda s: s.v))))
    return Page(number, spans, columns, rows, row_v, top)


def _close(a: float, b: float, places: int = 2) -> bool:
    return abs(a - b) <= 0.0051 * 10 ** (2 - places) + 1e-9


def _check(page: Page) -> list[str]:
    """Every share printed under a count is that count over the valid ballots, or over the voters for the turnout. The
    book misprints a few shares; they are returned, and a page with many of them is refused."""
    rows = page.rows
    reg, voters, share, valid, _, invalid, _ = rows[:7]
    problems: list[str] = []
    cells = 0
    for index in range(len(page.columns)):
        r, v, s, ok, bad = (row[index] for row in (reg, voters, share, valid, invalid))
        if r is None or v is None or s is None or ok is None or not r:
            raise ValueError(f"page {page.number}: column {index} lacks a figure")
        if abs(v / r * 100 - s) > 0.06:
            problems.append(f"turnout {s} is not {v}/{r}")
        if ok != v - (bad or 0):
            raise ValueError(f"page {page.number}: column {index} valid {ok} is not {v} less {bad}")
        for k in range(7, len(rows) - 1, 2):
            count, pct = rows[k][index], rows[k + 1][index]
            if count is None or pct is None:
                raise ValueError(f"page {page.number}: column {index} candidate row {k} lacks a figure")
            cells += 1
            if ok and abs(count / ok * 100 - pct) > 0.011:
                problems.append(f"page {page.number}: column {index} share {pct} is not {count}/{ok}")
    if len(problems) > cells // 3:
        raise ValueError(f"page {page.number}: {len(problems)} of {cells} shares do not match: {problems[:5]}")
    return problems


def _cell(value: float | None) -> int:
    return int(value or 0)


def read(number: int) -> Detail:
    path = raw.cached(sheets.SOURCE, sheets.url(BOOK)).path
    book = pymupdf.open(path)
    pages = [_page(book, n) for n in PAGES[number]]
    misprints: list[str] = []
    for page in pages:
        if len(page.rows) < 9 or len(page.rows) % 2 == 0:
            raise ValueError(f"constituency {number} page {page.number}: {len(page.rows)} rows")
        misprints += _check(page)
    rows_per_page = {len(p.rows) for p in pages}
    if len(rows_per_page) != 1:
        raise ValueError(f"constituency {number}: pages differ in rows {rows_per_page}")
    count = (len(pages[0].rows) - 7) // 2
    names = _names(pages[0], count)
    parties = _parties(pages[-1], count)
    candidates = [Candidate(name, party) for name, party in zip(names, parties, strict=True)]
    units: list[Unit] = []
    total: Unit | None = None
    for page in pages:
        for index, column in enumerate(page.columns):
            votes = [_cell(page.rows[7 + 2 * k][index]) for k in range(count)]
            reg, voters, _, valid, _, invalid, _ = (page.rows[i][index] for i in range(7))
            found = re.search(r"(\d+)-р хэсэг$", column.label)
            label = f"{found[1]}-р хэсэг" if found else SOUMS.get(column.label, column.label)
            unit = Unit(
                label, int(found[1]) if found else None, _cell(reg), _cell(voters), _cell(valid), _cell(invalid), votes
            )
            if label == "ДҮН":
                total = unit
            else:
                units.append(unit)
    if total is None:
        raise ValueError(f"constituency {number}: the table prints no total")
    if number in OMITTED:
        units.append(_remainder(number, units, total))
        units.sort(key=lambda u: u.section or 0)
    for name, mine, printed in (
        ("registered", sum(u.registered for u in units), total.registered),
        ("voters", sum(u.voters for u in units), total.voters),
        ("valid", sum(u.valid for u in units), total.valid),
        ("invalid", sum(u.invalid for u in units), total.invalid),
        *((f"candidate {k + 1}", sum(u.votes[k] for u in units), total.votes[k]) for k in range(len(candidates))),
    ):
        if mine != printed:
            raise ValueError(f"constituency {number}: {name} sums to {mine}, the book prints {printed}")
    for candidate, votes in zip(candidates, total.votes, strict=True):
        candidate.total = votes
    return Detail(number, candidates, units, total, misprints)


# The table of constituency 26 prints no column for section 39, though the printed total includes it.
OMITTED = {26: 39}


def _remainder(number: int, units: list[Unit], total: Unit) -> Unit:
    """The column the book leaves out: the printed total less the columns it prints."""
    left = [
        total.registered - sum(u.registered for u in units),
        total.voters - sum(u.voters for u in units),
        total.valid - sum(u.valid for u in units),
        total.invalid - sum(u.invalid for u in units),
        *(t - sum(u.votes[k] for u in units) for k, t in enumerate(total.votes)),
    ]
    if min(left) < 0 or any(u.section == OMITTED[number] for u in units):
        raise ValueError(f"constituency {number}: the omitted section does not fit the printed total: {left}")
    return Unit(f"{OMITTED[number]}-р хэсэг", OMITTED[number], *left[:4], left[4:], derived=True)


def _bands(page: Page, count: int) -> list[tuple[float, float]]:
    starts = [page.row_v[7 + 2 * k] for k in range(count)]
    ends = [page.row_v[8 + 2 * k] for k in range(count)]
    bands = []
    for k in range(count):
        low = (ends[k - 1] + starts[k]) / 2 if k else starts[k] - 6
        high = (ends[k] + starts[k + 1]) / 2 if k + 1 < count else ends[k] + 6
        bands.append((low, high))
    return bands


def _names(page: Page, count: int) -> list[str]:
    first = min(c.u for c in page.columns)
    labels = [s for s in page.spans if s.u < first - 6 and not NUMBER.match(s.text) and s.text not in {"тоо", "хувь"}]
    names = []
    for low, high in _bands(page, count):
        found = [s for s in labels if low <= s.v < high and s.u < first - 20]
        names.append(" ".join(s.text for s in sorted(found, key=lambda s: s.v)))
    return names


def _parties(page: Page, count: int) -> list[str]:
    last = max(c.u for c in page.columns)
    found = [
        s
        for s in page.spans
        if s.u > last + 8 and s.text != "Намын харъяалал" and not NUMBER.match(s.text) and "СОНГУУЛИЙН" not in s.text
    ]
    out = []
    for low, high in _bands(page, count):
        inside = [s for s in found if low <= s.v < high and s.v > page.row_v[0]]
        out.append(" ".join(s.text for s in sorted(inside, key=lambda s: (round(s.v / 4), s.u))))
    return out
