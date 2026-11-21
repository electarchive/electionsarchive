"""Reads the tables of the National Electoral Council's 2017 results book from its text layer.

The book prints each province's results by canton, and the overseas vote by country. A name that does not fit on one
line wraps above and below the line that holds its numbers, so a row's name is the words to the left of its numbers
that sit closest to that line.
"""

import functools
import re
import subprocess
import unicodedata
from typing import NamedTuple

from ea.sources.ec import cne

BOOK_2017 = f"{cne.BASE}/2022/05/RESULTADOS-ELECTORALES-2017.pdf"
NUM = re.compile(r"^\d{1,3}(?:\.\d{3})*$")
WIDE = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+|\d+)$")  # also a figure that the book leaves without its separator
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
BROKEN = re.compile(r"^\d{1,3}(?:\.\d{3})*\.\d\d$")  # a figure missing its last digit, such as 32.27 for 32.270
SIDEBAR = {"PROVINCIALES", "ASAMBEÍSTAS", "NACIONALES", "ANDINOS", "PARLAMENTARIOS", "ASAMBLEÍSTAS"}
# the book leaves Turkey's ballots-cast cell empty, and its total row counts no ballots there
SHORT = {"TURQUÍA"}
REACH = 12.0  # how far above or below a row's numbers a wrapped name can sit, in points


class Word(NamedTuple):
    x0: float
    x1: float
    y: float
    text: str


def norm(text: str) -> str:
    """A name without accents, punctuation, or case, for matching the book's spelling with the files'."""
    text = unicodedata.normalize("NFKD", text.upper())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    return " ".join(text.split())


def unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


@functools.cache
def pages(book: str = BOOK_2017) -> list[list[Word]]:
    path = cne.artifact(book).path
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    result: list[list[Word]] = []
    for page in xml.split("<page ")[1:]:
        found = [
            Word(float(x0), float(x1), (float(y0) + float(y1)) / 2, unescape(text))
            for x0, y0, x1, y1, text in WORD.findall(page)
        ]
        result.append(found)
    return result


def _joined(words: list[Word]) -> list[Word]:
    """The page's words in reading order, with a figure that the book broke across two words put back together."""
    ordered = sorted(words, key=lambda w: (round(w.y), w.x0))
    result: list[Word] = []
    for word in ordered:
        last = result[-1] if result else None
        if (
            last is not None
            and abs(last.y - word.y) < 1.0
            and word.x0 - last.x1 < 2.5
            and BROKEN.match(last.text)
            and re.fullmatch(r"\d", word.text)
        ):
            result[-1] = Word(last.x0, word.x1, last.y, last.text + word.text)
            continue
        if (
            last is not None
            and abs(last.y - word.y) < 1.0
            and word.x0 - last.x1 < 2.5
            and re.fullmatch(r"\d{1,2}", last.text)
            and re.fullmatch(r"\d\.\d{3}(?:\.\d{3})*", word.text)
        ):
            # a figure whose first digit the book set apart from the rest, such as 2 3.497
            result[-1] = Word(last.x0, word.x1, last.y, last.text + word.text)
            continue
        result.append(word)
    return result


def lines(page: int, book: str = BOOK_2017) -> list[tuple[float, list[Word]]]:
    """A page's words grouped into lines by height, top to bottom."""
    grouped: list[tuple[float, list[Word]]] = []
    for word in _joined(pages(book)[page - 1]):
        if word.text in SIDEBAR:
            continue
        if grouped and abs(grouped[-1][0] - word.y) <= 2.0:
            grouped[-1][1].append(word)
        else:
            grouped.append((word.y, [word]))
    return [(y, sorted(ws, key=lambda w: w.x0)) for y, ws in grouped]


def text(page: int) -> str:
    return "\n".join(" ".join(w.text for w in ws) for _, ws in lines(page))


def number(token: str) -> int:
    return int(token.replace(".", ""))


class Row(NamedTuple):
    page: int
    y: float
    name: str
    numbers: tuple[int, ...]
    x: float  # where the row's numbers start


def rows(
    page: int, width: int | None = None, below: float = 0.0, above: float = 1e9, book: str = BOOK_2017
) -> list[Row]:
    """Rows of whole numbers between two heights, each with the name words that sit nearest to it.

    The width is the count of numbers in a row; without one, the most common count on the page is used. A name that
    starts with a number, such as 24 DE MAYO, keeps that number in its name.
    """
    num = NUM if book == BOOK_2017 else WIDE
    candidates: list[tuple[float, list[Word]]] = []
    for y, words in lines(page, book):
        if not below <= y <= above:
            continue
        digits = [w for w in words if num.match(w.text)]
        if len(digits) >= 2:
            candidates.append((y, words))
    if not candidates:
        return []
    if width is None:
        counts = [sum(1 for w in words if num.match(w.text)) for _, words in candidates]
        width = max(set(counts), key=counts.count)
    found: list[tuple[float, float, list[int], list[Word]]] = []
    for y, words in candidates:
        digits = [w for w in words if num.match(w.text)]
        if not width <= len(digits) <= width + 1:
            continue
        numbers = digits[-width:]
        start = numbers[0].x0
        found.append((y, start, [number(w.text) for w in numbers], [w for w in words if w.x1 <= start - 0.5]))
    owned: dict[int, list[Word]] = {i: list(own) for i, (_, _, _, own) in enumerate(found)}
    start_of = min((start for _, start, _, _ in found), default=0)
    for y, words in lines(page, book):
        if not below <= y <= above or any(abs(y - f[0]) <= 2.0 for f in found):
            continue
        for word in words:
            if word.x1 > start_of - 0.5 or word.x0 < 30:
                continue
            nearest = min(range(len(found)), key=lambda i: abs(found[i][0] - word.y))
            if abs(found[nearest][0] - word.y) <= REACH and word.x1 <= found[nearest][1] - 0.5:
                owned[nearest].append(word)
    result: list[Row] = []
    for i, (y, start, numbers, _) in enumerate(found):
        fragments = sorted(owned[i], key=lambda w: (round(w.y), w.x0))
        name = " ".join(w.text for w in fragments)
        result.append(Row(page, y, name, tuple(numbers), start))
    return result


def top(page: int) -> str:
    """The heading line at the top of a page, such as PROVINCIA - AZUAY or the name of an overseas circumscription."""
    heads = [" ".join(w.text for w in ws) for y, ws in lines(page) if y < 22]
    return " ".join(heads)


PERCENT = re.compile(r"^\d{1,3}[,.]\d\d%$")
LISTNO = re.compile(r"^\d{1,3}(?:-\d{1,3})*$")


class Entry(NamedTuple):
    number: str  # the list number or numbers, such as 35 or 21-23-62
    sigla: str
    name: str
    votes: int


def box(page: int, book: str = BOOK_2017) -> dict[str, int]:
    """The INFORMACIÓN box of a summary page: voters on the roll, voters, blank ballots, and null ballots."""
    found: dict[str, int] = {}
    labels = (
        ("ELECTORES", "registered"),
        ("SUFRAGANTES QUE VOTARON EN BLANCO", "blank"),
        ("SUFRAGANTES QUE ANULARON SU VOTO", "null"),
        ("NO SUFRAGARON", "absent"),
        ("TOTAL SUFRAGANTES", "cast"),
        ("SUFRAGANTES", "cast"),
        ("JUNTAS", "stations"),
    )
    for _, words in lines(page, book):
        line = " ".join(w.text for w in words)
        for label, key in labels:
            if line.startswith(label) or (" " + label + " ") in " " + line + " " and line.split(label)[0].strip() == "":
                digits = [w.text for w in words if NUM.match(w.text)]
                if digits and key not in found and line.startswith(label):
                    found[key] = number(digits[0])
                break
    return found


def _header(ls: list[tuple[float, list[Word]]], names: tuple[str, ...]) -> dict[str, Word]:
    """The first, topmost occurrence of each column heading, so that the same word in a name is not taken for it."""
    found: dict[str, Word] = {}
    for _, ws in ls:
        for w in ws:
            if w.text in names and w.text not in found:
                found[w.text] = w
    return found


def summary(page: int, book: str = BOOK_2017) -> list[Entry]:
    """The organizations of a summary page with their list numbers, abbreviations, and valid votes."""
    ls = lines(page, book)
    header = _header(ls, ("NOMBRE", "SIGLAS", "LISTA"))
    if not {"NOMBRE", "SIGLAS", "LISTA"} <= set(header):
        raise ValueError(f"page {page} has no organization table")
    cut = min(header["SIGLAS"].x0, header["LISTA"].x0) - 6
    list_c = (header["LISTA"].x0 + header["LISTA"].x1) / 2
    sigla_c = (header["SIGLAS"].x0 + header["SIGLAS"].x1) / 2
    top_y = max(header["NOMBRE"].y, header["SIGLAS"].y, header["LISTA"].y) + 3
    total_y = next(
        y
        for y, ws in ls
        if y > top_y and any(w.text == "TOTAL" for w in ws) and any(w.text in ("100,00%", "100%") for w in ws)
    )
    mains: list[tuple[float, int]] = []
    starts: list[float] = []
    for y, ws in ls:
        if top_y < y < total_y:
            pct = [w for w in ws if PERCENT.match(w.text)]
            if not pct:
                continue
            votes = [w for w in ws if NUM.match(w.text) and w.x1 < pct[0].x0]
            if not votes:
                continue  # a percentage label of a chart
            mains.append((y, number(votes[-1].text)))
            left = [w for w in ws if w.x1 < cut]
            if left:
                begin = left[-1].x0
                for w in reversed(left[:-1]):
                    if begin - w.x1 > 6:
                        break
                    begin = w.x0
                starts.append(begin)
    if not mains:
        raise ValueError(f"page {page} lists no organizations")
    name_x = sorted(starts)[len(starts) // 2] - 2
    zones: list[dict[str, list[Word]]] = [{"name": [], "list": [], "sigla": []} for _ in mains]
    for y, ws in ls:
        if not top_y < y < total_y:
            continue
        for w in ws:
            if w.x0 < name_x:
                continue
            nearest = min(range(len(mains)), key=lambda i: abs(mains[i][0] - y))
            if abs(mains[nearest][0] - y) > REACH + 2:
                continue
            if w.x1 < cut:
                if not LISTNO.match(w.text):
                    zones[nearest]["name"].append(w)
                continue
            centre = (w.x0 + w.x1) / 2
            if PERCENT.match(w.text) or centre > max(list_c, sigla_c) + 30:
                continue  # the percent and votes columns
            # some pages print the two headings over each other's columns, so the shape of a token decides
            zones[nearest]["list" if LISTNO.match(w.text) else "sigla"].append(w)
    entries = []
    for (_, votes), zone in zip(mains, zones, strict=True):
        entries.append(
            Entry(
                "".join(w.text for w in sorted(zone["list"], key=_order)),
                (
                    "".join(w.text for w in sorted(zone["sigla"], key=_order))
                    if book == BOOK_2017
                    else _abbreviation(sorted(zone["sigla"], key=_order))
                ),
                " ".join(w.text for w in sorted(zone["name"], key=_order)),
                votes,
            )
        )
    return entries


def _abbreviation(words: list[Word]) -> str:
    """An abbreviation read from the words of one cell: letters set apart join up, and other words keep their spaces."""
    text = ""
    for i, word in enumerate(words):
        letters = len(word.text.strip('“”"')) == 1 and len(words[i - 1].text.strip('“”"')) == 1
        if i and not (text.endswith("/") or word.text.startswith("/") or letters):
            text += " "
        text += word.text
    return text


def _order(word: Word) -> tuple[int, float]:
    return round(word.y / 3), word.x0


class Winner(NamedTuple):
    number: str
    sigla: str
    name: str
    gender: str
    votes: int


def winners(page: int, book: str = BOOK_2017) -> list[Winner]:
    """The elected members a seats page lists, each with the list they won for and their individual votes."""
    ls = lines(page, book)
    header = _header(ls, ("NOMBRE", "SIGLAS", "LISTA", "GÉNERO"))
    if not {"NOMBRE", "SIGLAS", "LISTA", "GÉNERO"} <= set(header):
        return []
    anchors = [
        (y, w)
        for y, ws in ls
        for w in ws
        if w.text in ("MASCULINO", "FEMENINO", "FEMENIO") and abs(w.x0 - header["GÉNERO"].x0) < 40
    ]
    left = max(header["LISTA"].x1, header["SIGLAS"].x1) + 2
    right = header["GÉNERO"].x0 - 2
    sigla_c = (header["SIGLAS"].x0 + header["SIGLAS"].x1) / 2
    names: list[list[Word]] = [[] for _ in anchors]
    numbers: list[str] = ["" for _ in anchors]
    siglas: list[list[Word]] = [[] for _ in anchors]
    votes: list[int] = [0 for _ in anchors]
    list_c = (header["LISTA"].x0 + header["LISTA"].x1) / 2
    for y, ws in ls:
        for w in ws:
            if y < header["NOMBRE"].y + 4 or not anchors:
                continue
            nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i][0] - w.y))
            if abs(anchors[nearest][0] - w.y) > 14:
                continue
            if left <= w.x0 and w.x1 <= right:
                names[nearest].append(w)
            elif abs(anchors[nearest][0] - w.y) <= 3:
                centre = (w.x0 + w.x1) / 2
                if w.x0 > right and NUM.match(w.text):
                    votes[nearest] = number(w.text)
                elif LISTNO.match(w.text) and w.x0 < left and abs(centre - list_c) < 40:
                    numbers[nearest] = w.text
                elif abs(centre - sigla_c) < 45 and any(c.isalpha() for c in w.text):
                    siglas[nearest].append(w)
    return [
        Winner(
            numbers[i],
            "".join(w.text for w in sorted(siglas[i], key=_order)),
            " ".join(w.text for w in sorted(names[i], key=_order)),
            anchor.text,
            votes[i],
        )
        for i, (_, anchor) in enumerate(anchors)
    ]


class Group(NamedTuple):
    page: int  # the summary page
    head: str
    entries: list[Entry]
    info: dict[str, int]
    winners: list[Winner]
    units: list[Row]  # the canton, province, or country rows
    total: Row | None  # the row the book totals them in
    parishes: list[Row]  # the parish rows of circumscriptions that split a canton
    pages: list[int]


def group_starts() -> list[int]:
    """Summary pages of every legislative contest: the page after each one lists the seats and who won them."""
    found = []
    for page in range(158, len(pages())):
        here, after = text(page), text(page + 1) if page < len(pages()) else ""
        if "INFORMACIÓN" in here and "NÚMERO DE ESCAÑOS" in after and "VOTOS" in here:
            found.append(page)
    return found


def heading(page: int) -> str:
    words = " ".join(w.text for y, ws in lines(page) if y < 22 for w in ws)
    return " ".join(words.replace("RESULTADOS ELECTORALES", "").split())


def groups() -> list[Group]:
    starts = group_starts()
    ends = [*starts[1:], len(pages()) + 1]
    result = []
    for start, end in zip(starts, ends, strict=True):
        entries = summary(start)
        seats = [p for p in range(start + 1, end) if "NÚMERO DE ESCAÑOS" in text(p)]
        elected = [w for p in seats for w in winners(p)]
        table_pages = [p for p in range(start + 2, end) if "RESULTADOS POR" in text(p) or "CANTÓN/PARROQUIA" in text(p)]
        found: list[Row] = []
        parishes: list[Row] = []
        split = False  # the pages after the first parish heading hold only parish rows
        for p in range(start + 2, end):
            if p in seats or not table_pages:
                continue
            cut = next((y for y, ws in lines(p) if any(w.text == "CANTÓN/PARROQUIA" for w in ws)), 1e9)
            if split:
                cut = 0.0
            best: list[Row] = []
            for width in (len(entries) + 6, len(entries) + 5, len(entries) + 1):
                here = [r for r in rows(p, width, 0, cut) if len(r.numbers) == width]
                if len(here) > len(best):
                    best = here
            if best:
                short = [r for r in rows(p, len(best[0].numbers) - 1, 0, cut) if r.name in SHORT]
                best = sorted(
                    [*best, *(Row(r.page, r.y, r.name, (*r.numbers, 0), r.x) for r in short)], key=lambda r: r.y
                )
            found.extend(best)
            parishes.extend(rows(p, len(entries) + 1, cut, 1e9))
            split = split or cut < 1e9
        total = next((r for r in found if r.name.startswith("TOTAL")), None)
        units = [r for r in found if r is not total]
        result.append(Group(start, heading(start), entries, box(start), elected, units, total, parishes, table_pages))
    return result


class Table(NamedTuple):
    head: str  # the province or overseas circumscription
    units: list[Row]
    total: Row


def president_tables(runoff: bool) -> list[Table]:
    """Every canton table (and overseas country table) of one presidential round, each with the book's own total."""
    width = 8 if runoff else 14
    result = []
    for page in range(1, len(pages()) + 1):
        here = text(page)
        if "RESULTADOS POR CANTÓN" not in here and "RESULTADOS POR PAÍS" not in here:
            continue
        if ("ZUQUILANDA" in here) == runoff or not ("ZUQUILANDA" in here or "GUILLERMO LASSO" in here):
            continue
        if "TERRITORIO NACIONAL" in heading(page) or "RESULTADOS POR PROVINCIA" in here:
            continue
        found = rows(page, width)
        more = page + 1
        while not any(r.name.startswith("TOTAL") for r in found) and more <= len(pages()):
            found += rows(more, width)
            more += 1
        total = next(r for r in found if r.name.startswith("TOTAL"))
        result.append(Table(heading(page), [r for r in found if r is not total], total))
    return result


def declared_president(runoff: bool) -> list[int]:
    """The nation's printed results for a presidential round: votes for each ticket, then null, blank, cast, and registered."""
    page = 84 if runoff else 18
    chart = [(w.x0, number(w.text)) for _, ws in lines(page) for w in ws if NUM.match(w.text) and w.y > 300]
    votes = [value for _, value in sorted(chart) if value >= 1000]
    info = box(page)
    return [*votes, info["null"], info["blank"], info["cast"], info["registered"]]


class Canton(NamedTuple):
    row: Row  # the canton's own row in a "CANTÓN/PARROQUIA" table
    parishes: list[Row]  # its parishes, whose rows add up to the canton's row


def parish_tables(group: Group) -> list[Canton]:
    """The cantons of a circumscription's parish table, each with its parish rows.

    The table prints a canton's row and then the rows of its parishes in the circumscription, so a canton row is the
    row whose figures the rows after it add up to. A canton with one parish prints the same figures twice. A name that
    wraps is read as in ``rows``.
    """
    width = len(group.entries) + 1
    rest = group.parishes
    found: list[Canton] = []
    i = 0
    while i < len(rest):
        head = rest[i]
        total = [0] * width
        j = i + 1
        while j < len(rest) and total != list(head.numbers):
            total = [a + b for a, b in zip(total, rest[j].numbers, strict=True)]
            j += 1
        if total != list(head.numbers) or j == i + 1:
            raise ValueError(f"page {head.page}: the rows after {head.name!r} do not add up to its figures")
        found.append(Canton(head, rest[i + 1 : j]))
        i = j
    return found
