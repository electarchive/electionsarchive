"""Guatemala's general elections from the Tribunal Supremo Electoral's election reports (memorias electorales).

The Tribunal's per-table files reach back only to 2023 (`gt.tse`). For earlier elections, the finest unit it still
publishes is the municipality, in the tables of each election's memoria. The memorias are typeset PDFs with a text
layer, so each number is read with its word box from `pdftotext -bbox` and stored with its page and box.

The 2003 memoria is a two-page file for each municipality, read in `gt.memoria2003`. The other four print their
results in four layouts: 2007 a page of results for each municipality, department, and the Distrito Central; 2011 a
table for each department, the district tables set sideways, and a block for each municipality; 2015 tables by
department and mayoral blocks; 2019 one contest per page.
"""

import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from difflib import get_close_matches
from functools import cache
from html import unescape
from pathlib import Path
from types import ModuleType
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.gt import tse

SOURCE = "gt.memoria"
GUATEMALA = tse.GUATEMALA
COUNTRY = tse.COUNTRY
SITE = "https://www.tse.org.gt"
MEMORIAS = f"{SITE}/comunicacion/publicaciones/memorias/memorias-electorales-2"
LICENSE = tse.LICENSE

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ACCOUNTING = {"NULOS": INVALID, "BLANCOS": BLANK, "EMITIDOS": CAST, "VALIDOS": VALID, "PADRON": REGISTERED}


class Word(NamedTuple):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    box: tuple[float, float, float, float]
    """The word's box on the printed page, which differs from x0 to y1 on a page set sideways."""

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


def fold(text: str) -> str:
    return tse.fold(text)


WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>|<page width')


@cache
def raw_words(path: Path, first: int, last: int) -> tuple[dict[int, list[Word]], dict[int, float]]:
    """Every word on pages `first` to `last` of a PDF as printed, by page, with each page's height in points."""
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pages: dict[int, list[Word]] = {}
    heights: dict[int, float] = {}
    page = first - 1
    for found in WORD.finditer(out):
        if found.group(1) is None:
            page += 1
            pages[page] = []
            size = re.search(r'width="([\d.]+)" height="([\d.]+)"', out[found.start() : found.start() + 80])
            if size is None:
                raise ValueError(f"{path}: page {page} has no size")
            heights[page] = float(size.group(2))
            SIZES[(path, page)] = (round(float(size.group(1))), round(float(size.group(2))))
            continue
        x0, y0, x1, y1 = (float(found.group(i)) for i in range(1, 5))
        pages[page].append(Word(page, x0, y0, x1, y1, unescape(found.group(5)), (x0, y0, x1, y1)))
    return pages, heights


def turned(found: list[Word], height: float) -> list[Word]:
    """Words set sideways, reading upward, as a page's lines: the printed line runs up the page."""
    return [Word(w.page, height - w.y1, w.x0, height - w.y0, w.x1, w.text, w.box) for w in found]


@cache
def words(path: Path, first: int, last: int) -> dict[int, list[Word]]:
    """Every word on pages `first` to `last` of a PDF, by page, with its box in points from the page's top left."""
    raw_pages, heights = raw_words(path, first, last)
    pages = dict(raw_pages)
    for number, found_words in raw_pages.items():
        long = [w for w in found_words if len(w.text) >= 3]
        if long and sum(w.y1 - w.y0 > w.x1 - w.x0 for w in long) > len(long) / 2:
            pages[number] = turned(found_words, heights[number])
    return pages


SIZES: dict[tuple[Path, int], tuple[int, int]] = {}
"""Each read page's width and height in points."""

NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")


def is_number(text: str) -> bool:
    return NUMBER.fullmatch(text) is not None


def lines(found: list[Word], tolerance: float = 2.5) -> list[list[Word]]:
    """Words grouped into lines by their vertical centers, each line sorted left to right."""
    rows: list[list[Word]] = []
    for word in sorted(found, key=lambda w: (w.yc, w.x0)):
        if rows and abs(rows[-1][0].yc - word.yc) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w.x0) for row in rows]


def text(row: list[Word]) -> str:
    return " ".join(w.text for w in row)


@dataclass
class Row:
    """One printed table row: the place's name, with each column's number and its word."""

    name: str
    values: list[Word]
    section: str


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[Row] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    left: list[Word] = field(default_factory=list)
    """Every word left of the numbers, in page order: the place names, which wrap above and below their rows."""


SECTION = re.compile(r"^(?:Contin[uú]a\s+(?:resultados\s+del?\s+)?)?departamento\s+de\s+(.+)$", re.IGNORECASE)


def _is_header(row: list[Word]) -> bool:
    return (
        len(row) >= 3
        and fold(row[0].text) in ("MUNICIPIO", "DEPARTAMENTO")
        and fold(row[1].text) not in ("DE", "DEL")
        and not any(is_number(w.text) for w in row)
    )


Segment = tuple[str, list[Word] | None, list[list[Word]], str]


def _section(row: list[Word]) -> tuple[str | None, list[Word]]:
    """A department heading set in the row ("Departamento de X", "Continúa departamento de X"), and the rest."""
    for i, w in enumerate(row):
        if fold(w.text) not in ("DEPARTAMENTO", "CONTINUA"):
            continue
        run = [w]
        for other in row[i + 1 :]:
            if other.x0 - run[-1].x1 > 12 or is_number(other.text):
                break
            run.append(other)
        found = SECTION.match(text(run))
        if found and fold(found.group(1)) not in ("", "DE"):
            return found.group(1), [x for x in row if x not in run]
    return None, row


def _segment(page: list[Word], title: str, section: str, band: float = 9) -> tuple[list[Segment], str, str]:
    """Split a page into table segments: (title, header words, lines, section at start)."""
    found: list[Segment] = []
    current: Segment | None = None
    pending: list[list[Word]] = []
    rows = lines(page)
    used: set[int] = set()
    titled: set[int] = set()
    for index, row in enumerate(rows):
        if index in used:
            continue
        heads = [
            i for i, w in enumerate(row) if w.text == "Resultados" or (w.text in ("ELECCION", "SEGUNDA") and i == 0)
        ]
        if heads:
            run = [row[heads[0]]]
            for w in row[heads[0] + 1 :]:
                if w.x0 - run[-1].x1 > 15:
                    break
                run.append(w)
            title = text(run)
            titled.add(index)
            current = None
            pending = []
            named = re.search(r"DEPARTAMENTO DE (.+)$", title)
            if named:
                section = named.group(1)
            row = [w for w in row if w not in run]
            if sum(is_number(w.text) for w in row) < 2:
                continue
        if _is_header(row):
            heads = [
                w
                for j, other in enumerate(rows)
                if j not in titled
                for w in other
                if abs(other[0].yc - row[0].yc) <= band
                and not is_number(w.text)
                and w is not row[0]
                and w.x0 > row[0].x1
            ]
            for j, other in enumerate(rows):
                if (
                    j != index
                    and abs(other[0].yc - row[0].yc) <= band
                    and not any(is_number(w.text) for w in other)
                    and any(w.x0 > row[0].x1 for w in other)
                ):
                    used.add(j)
            current = (title, [row[0], *heads], [], section)
            found.append(current)
            continue
        if current is not None:
            current[2].append(row)
            continue
        heading, rest = _section(row)
        if heading is not None:
            section = heading
        numbers = [w for w in rest if is_number(w.text)]
        if len(numbers) >= 2:
            labels = [w for line in pending for w in line if w.x0 >= numbers[0].x0 - 20]
            if len(labels) >= 2:
                # A header without the "Municipio" label: its labels stand alone above the first row.
                left = min(w.x0 for w in labels)
                header = [Word(row[0].page, 0, 0, left - 25, 0, "", (0, 0, 0, 0)), *labels]
                current = (title, header, [row], section)
            else:
                current = (title, None, [*pending, row], section)
            found.append(current)
        elif heading is None and rest:
            pending.append(rest)
    return found, title, section


def _columns(header: list[Word], rows: list[list[Word]], boundary: float) -> list[tuple[str, float]]:
    numeric = [[w for w in row if is_number(w.text) and w.xc > boundary] for row in rows]
    numeric = [row for row in numeric if len(row) >= 2]
    if not numeric:
        return []
    width = Counter(len(row) for row in numeric).most_common(1)[0][0]
    full = [row for row in numeric if len(row) == width]
    centers = [sum(row[i].xc for row in full) / len(full) for i in range(width)]
    labels: list[list[Word]] = [[] for _ in centers]
    for w in header[1:]:
        nearest = min(range(width), key=lambda i: abs(centers[i] - w.xc))
        labels[nearest].append(w)
    return [
        (" ".join(w.text for w in sorted(group, key=lambda w: (round(w.y0), w.x0))), center)
        for group, center in zip(labels, centers, strict=True)
    ]


def _strips(page: list[Word]) -> list[list[Word]]:
    """A page split into side-by-side strips where it sets two tables beside each other.

    The left strip's header ends well before the page's numbers do; the right strip may have its own header or
    continue the left one's columns.
    """
    for row in lines(page):
        ends = [i for i, w in enumerate(row) if fold(w.text) == "EMITIDOS"]
        if not ends or not _is_header(row[: ends[0] + 1]):
            continue
        last = row[ends[0]].x1
        beyond = [w for w in page if is_number(w.text) and w.x0 > last + 30 and 0 <= w.yc - row[0].yc <= 40]
        if len(beyond) >= 5:
            cut = last + 15
            return [[w for w in page if w.xc < cut], [w for w in page if w.xc >= cut]]
    return [page]


def spread_tables(path: Path, first: int, last: int) -> dict[int, list[Table]]:
    """The table set sideways on the right page of each spread, by PDF page."""
    raw_pages, heights = raw_words(path, first, last)
    found: dict[int, list[Table]] = {}
    for number, page in raw_pages.items():
        right = turned([w for w in page if w.x0 >= 580 and w.x0 < 1100], heights[number])
        found[number] = tables(path, number, number, {number: right}, 20) if right else []
    return found


def tables(
    path: Path, first: int, last: int, pages: dict[int, list[Word]] | None = None, band: float = 9
) -> list[Table]:
    """The tables on pages `first` to `last`, a table running on across pages while its title and columns hold."""
    found: list[Table] = []
    title, section = "", ""
    pages = words(path, first, last) if pages is None else pages
    for number in range(first, last + 1):
        segments: list[Segment] = []
        for strip in _strips(pages[number]):
            found_segments, title, section = _segment(strip, title, section, band)
            segments.extend(found_segments)
        for seg_title, header, rows, start in segments:
            if header is None:
                if not found:
                    continue
                table = found[-1]
                columns = _inherit(table.columns, rows)
                if not columns:
                    continue
                boundary = columns[0][1] - (columns[1][1] - columns[0][1]) / 2
            else:
                boundary = (header[0].x1 + min((w.x0 for w in header[1:]), default=header[0].x1)) / 2
                columns = _columns(header, rows, boundary)
                if not columns:
                    continue
                names = [c for c, _ in columns]
                table = found[-1] if found and found[-1].title == seg_title and found[-1].columns == names else None
                if table is None:
                    table = Table(seg_title, names)
                    found.append(table)
            table.pages.append(number)
            _rows(table, rows, columns, boundary, start)
    return found


def _inherit(labels: list[str], rows: list[list[Word]]) -> list[tuple[str, float]]:
    """The previous table's columns placed on a strip that does not repeat its header."""
    numeric = [[w for w in row if is_number(w.text)] for row in rows]
    full = [row[-len(labels) :] for row in numeric if len(row) >= len(labels) >= 2]
    if not full:
        return []
    return [(label, sum(row[i].xc for row in full) / len(full)) for i, label in enumerate(labels)]


def _rows(table: Table, rows: list[list[Word]], columns: list[tuple[str, float]], boundary: float, start: str) -> None:
    section = start
    data: list[tuple[float, list[Word], str]] = []
    names: list[tuple[float, list[Word]]] = []
    for full in rows:
        heading, row = _section(full)
        table.left.extend(w for w in row if w.xc <= boundary and not is_number(w.text))
        if heading is not None:
            section = heading
        numeric = [w for w in row if is_number(w.text) and w.xc > boundary]
        left = [w for w in row if w.xc <= boundary and not is_number(w.text)]
        if len(numeric) >= 2:
            data.append((row[0].yc, row, section))
        elif left and not numeric:
            names.append((sum(w.yc for w in left) / len(left), left))
    owner: dict[int, list[Word]] = defaultdict(list)
    for yc, left in names:
        if not data:
            break
        nearest = min(range(len(data)), key=lambda i: abs(data[i][0] - yc))
        if abs(data[nearest][0] - yc) <= 12:
            owner[nearest].extend(left)
    centers = [center for _, center in columns]
    for index, (_, row, row_section) in enumerate(data):
        numeric = [w for w in row if is_number(w.text) and w.xc > boundary]
        name_words = [w for w in row if w.xc <= boundary and not is_number(w.text)] + owner[index]
        name = " ".join(w.text for w in sorted(name_words, key=lambda w: (round(w.yc / 3), w.x0)))
        if len(numeric) == len(centers):
            values = numeric
        else:
            slots: list[Word | None] = [None] * len(centers)
            for w in numeric:
                nearest = min(range(len(centers)), key=lambda i: abs(centers[i] - w.xc))
                held = slots[nearest]
                if held is not None:
                    table.problems.append(f"page {w.page}: two numbers in column {nearest} of row {name!r}")
                    if len(held.text) >= len(w.text):
                        # A stray digit of the percentage beside the last column, set apart from its decimals.
                        continue
                slots[nearest] = w
            if any(s is None for s in slots):
                table.problems.append(f"page {row[0].page}: row {name!r} has {len(numeric)} of {len(centers)} numbers")
                continue
            values = [s for s in slots if s is not None]
        table.rows.append(Row(" ".join(name.split()), values, row_section))


@cache
def places() -> dict[tuple[int, int], tuple[str, str]]:
    """The TSE's department and municipality codes and names, from its 2023 per-table files."""
    return tse.places()


@cache
def municipalities() -> dict[str, list[tuple[str, str]]]:
    """Each department's folded name, with its municipalities' folded names and codes, as the TSE codes them."""
    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    names = places()
    departments = {dep: fold(d) for (dep, _), (d, _) in names.items() if dep not in tse.ABROAD and dep != tse.CENTRAL}
    for (dep, mun), (_, town) in sorted(names.items()):
        if dep in tse.ABROAD:
            continue
        found[departments[1 if dep == tse.CENTRAL else dep]].append((fold(town), tse.municipality(dep, mun)))
    return found


ALIASES = {
    "SAN MIGUEL PETAPA": "PETAPA",
    "SAN JUAN COMALAPA": "COMALAPA",
    "DISTRITO CENTRAL": "GUATEMALA",
    "SAN JOSE EL RODEO": "EL RODEO",
}


def department(text: str) -> str | None:
    folded = fold(text)
    if folded in municipalities():
        return folded
    close = get_close_matches(folded, list(municipalities()), n=1, cutoff=0.8)
    return close[0] if close else None


def match(name: str, section: str) -> str | None:
    """The TSE code of the municipality a row names, within the department its section names."""
    folded = fold(name)
    folded = ALIASES.get(folded, folded)
    dep = department(section)
    pools = [municipalities()[dep]] if dep else []
    pools.append([m for towns in municipalities().values() for m in towns])
    for pool in pools:
        names = dict(pool)
        if folded in names:
            return names[folded]
        starts = [code for town, code in pool if town.startswith(folded + " ") or folded.startswith(town + " ")]
        if len(starts) == 1:
            return starts[0]
        close = get_close_matches(folded, list(names), n=2, cutoff=0.85)
        if len(close) == 1 or (close and pool is pools[0]):
            return names[close[0]]
    return None


@dataclass
class Block:
    """One municipality's mayoral results: each organization's votes, with the candidate the block prints."""

    town: str
    department: str
    rows: list[tuple[str, str, Word]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    rerun: bool = False


RERUN = re.compile(r"Decreto\s+3-2015")
ALCALDE = re.compile(r"Resultados\s+de\s+elecci[oó]n\s+de\s+alcalde", re.IGNORECASE)


def blocks(path: Path, first: int, last: int) -> list[Block]:
    """The mayoral results blocks on pages `first` to `last`, read from the left of each page's adjudications."""
    found: list[Block] = []
    current: Block | None = None
    columns: float | None = None
    body: list[list[Word]] = []
    expect_place = False
    pages = words(path, first, last)

    def close() -> None:
        if current is not None and columns is not None:
            _block_rows(current, body, columns)

    for number in range(first, last + 1):
        page = pages[number]
        right = min((w.x0 for w in page if w.text.startswith("Adjudicaci")), default=10_000.0)
        for row in lines([w for w in page if w.x1 <= right - 4], tolerance=4):
            joined = text(row)
            if ALCALDE.search(joined):
                close()
                expect_place, current, columns, body = True, None, None, []
                continue
            if expect_place:
                place = _place(joined)
                if place is None:
                    continue
                town, dep = place
                current = Block(" ".join(town.split()), " ".join(dep.split()))
                found.append(current)
                expect_place = False
                continue
            if current is None:
                if found and RERUN.search(joined):
                    found[-1].rerun = True
                continue
            heads = {fold(w.text): w for w in row}
            if "POSTULADOS" in heads:
                columns = heads["POSTULADOS"].x0
                continue
            if columns is None:
                continue
            body.append(row)
            if row and fold(row[0].text) == "EMITIDOS":
                close()
                current, columns, body = None, None, []
    close()
    return found


def _place(line: str) -> tuple[str, str] | None:
    """A block's "Town, Department" line, which a few blocks print without the comma."""
    if "Tribunal Supremo" in line:
        return None
    if "," in line:
        town, _, dep = line.rpartition(",")
        return town, dep
    words_ = line.split()
    for cut in (1, 2):
        if len(words_) > cut and fold(" ".join(words_[-cut:])) in municipalities():
            return " ".join(words_[:-cut]), " ".join(words_[-cut:])
    return None


def _block_rows(block: Block, body: list[list[Word]], column: float) -> None:
    """Pair each printed vote count with its organization, which may sit a little above or below it."""
    counted: list[tuple[float, list[Word], Word]] = []
    loose: list[tuple[float, list[Word]]] = []
    for row in body:
        numbers = [w for w in row if is_number(w.text) and w.x0 > column]
        label = [w for w in row if w.x0 < column - 2 and not is_number(w.text)]
        if numbers:
            counted.append((row[0].yc, label, numbers[-1]))
        elif label:
            loose.append((sum(w.yc for w in label) / len(label), label))
    for yc, label in loose:
        near = [i for i, (y, _, _) in enumerate(counted) if abs(y - yc) <= 10]
        bare = [i for i in near if not counted[i][1]] or near
        if bare:
            nearest = min(bare, key=lambda i: abs(counted[i][0] - yc))
            y, lab, votes = counted[nearest]
            counted[nearest] = (y, sorted([*lab, *label], key=lambda w: (round(w.yc), w.x0)), votes)
    for _, label, votes in counted:
        if not label:
            block.problems.append(f"page {votes.page}: a count with no organization: {votes.text}")
            continue
        block.rows.append((" ".join(w.text for w in label), "", votes))


# The elections ---------------------------------------------------------------------------------------------------


class Memoria(NamedTuple):
    year: int
    file: str
    general: date
    runoff: date
    tables: tuple[int, int]
    blocks: tuple[int, int]
    seats: int
    """Deputies elected by national list."""
    layout: str = "tables"
    """"tables" prints each contest by municipality; "cards" prints one contest per page, by its own area; "files2003"
    is a two-page file for each municipality; "scan" is a scan read by OCR in `gt.memoria_scan`."""
    districts: tuple[int, int] = (0, 0)
    """The pages of the district tables of a memoria that sets each one sideways."""
    parlacen: tuple[int, int] = (0, 0)
    """The pages of the Central American Parliament's tables."""


MEMORIA_LIST = (
    Memoria(
        2011,
        "2011_Memoria_Elecciones",
        date(2011, 9, 11),
        date(2011, 11, 6),
        (318, 355),
        (380, 547),
        31,
        "report2011",
        districts=(357, 379),
        parlacen=(548, 561),
    ),
    Memoria(2007, "2007_Memoria_Elecciones", date(2007, 9, 9), date(2007, 11, 4), (0, 0), (0, 0), 31, "report2007"),
    Memoria(2015, "2015_Memoria_Elecciones", date(2015, 9, 6), date(2015, 10, 25), (248, 326), (328, 456), 31),
    Memoria(2019, "2019_Memoria_Elecciones", date(2019, 6, 16), date(2019, 8, 11), (375, 804), (0, 0), 32, "cards"),
    Memoria(2003, "", date(2003, 11, 9), date(2003, 12, 28), (0, 0), (0, 0), 31, "files2003"),
    Memoria(1999, "1999_Memoria_Elecciones", date(1999, 11, 7), date(1999, 12, 26), (0, 0), (0, 0), 22, "scan"),
    Memoria(1995, "1995-1996_Memoria_Elecciones", date(1995, 11, 12), date(1996, 1, 7), (0, 0), (0, 0), 16, "scan"),
)
NATION = "GT"
"""The unit code of a contest the memoria prints only as a national total."""
BY_YEAR = {m.year: m for m in MEMORIA_LIST}


def _scans() -> ModuleType:
    """The reader of the scanned memorias, which imports this module."""
    from ea.sources.gt import memoria_scan

    return memoria_scan


def _files2003() -> ModuleType:
    """The reader of the 2003 memoria, which imports this module."""
    from ea.sources.gt import memoria2003

    return memoria2003


def url(m: Memoria) -> str:
    """The memoria's PDF, or for the memoria that is a file for each municipality, its index page."""
    if m.layout == "files2003":
        return _files2003().INDEX
    return f"{SITE}/images/memoriaselec/me/{m.file}.pdf"


def artifacts(m: Memoria) -> list[raw.Artifact]:
    """The raw files a memoria's numbers are read from."""
    if m.layout == "files2003":
        return _files2003().used()
    return [raw.cached(SOURCE, url(m))]


def located(m: Memoria, page: int) -> tuple[raw.Artifact, int]:
    """The raw file a page number of a memoria's words is in, with its page number there."""
    if m.layout == "files2003":
        return _files2003().pages()[page]
    return raw.cached(SOURCE, url(m)), page


def fetch() -> None:
    for m in MEMORIA_LIST:
        if m.layout == "files2003":
            _files2003().fetch()
        else:
            raw.fetch(SOURCE, url(m), suffix=".pdf")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


TYPOS = {
    "CORVERGENCIA": "CONVERGENCIA",
    "FUEZA": "FUERZA",
    "UNGMAIZ": "URNGMAIZ",
    "WIMAQ": "WINAQ",
    "EMMITIDOS": "EMITIDOS",
    "BLANCO": "BLANCOS",
    "ENBLANCO": "BLANCOS",
    "VOTOSNULOS": "NULOS",
    "VOTOSENBLANCO": "BLANCOS",
    "VOTOSEMITIDOS": "EMITIDOS",
    "EMPADRONADOS": "PADRON",
}
"""Misspelled acronyms in the memorias' tables, read as the organization they name."""
BLOCS = {"URNGWINAQ"}


def label(text: str) -> str:
    """An organization's label as one key: the memorias print one acronym with and without hyphens and spaces."""
    found = re.sub(r"[^A-Z0-9]", "", fold(text))
    return TYPOS.get(found, found)


ACCOUNTS = {label(name): measure for name, measure in ACCOUNTING.items()}


def number(w: Word | None) -> int:
    """A printed count; a blank cell counts as zero in sums."""
    return 0 if w is None else int(w.text.replace(",", ""))


@dataclass
class Contest:
    """One contest as the memoria prints it: its columns, its rows by municipality, and its printed totals."""

    key: str
    kind: str
    columns: list[str]
    rows: dict[str, list[Word | None]] = field(default_factory=dict)
    """Each municipality's printed cells in the order of `columns`; a cell the memoria leaves blank is None."""
    declared: dict[str, int] = field(default_factory=dict)
    candidates: dict[str, str] = field(default_factory=dict)
    departments: dict[int, list[Word | None]] = field(default_factory=dict)
    """The printed department totals of a national contest."""
    zero: list[str] = field(default_factory=list)
    """Municipalities the memoria prints with every cell zero, as for an annulled election."""
    seats: dict[str, int] = field(default_factory=dict)
    """Seats the memoria adjudicates to each organization, by its label, where it prints the adjudications."""
    notes: list[str] = field(default_factory=list)
    """Sentences about how the memoria prints this contest that its result set's note carries."""


DISTRICT = re.compile(
    r"distritales\s+(?:del?\s+)?(?:los\s+municipios\s+del\s+)?(?:departamento\s+de\s+(.+)|(Distrito Central))",
    re.IGNORECASE,
)


def _national_kind(title: str) -> str | None:
    folded = fold(title)
    if "SEGUNDA" in folded:
        return "president2"
    if "LISTA NACIONAL" in folded:
        return "national"
    if "PARLAMENTO" in folded:
        return "parlacen"
    if "PRESIDENTE" in folded:
        return "president"
    return None


def _add_row(contest: Contest, code: str, values: list[Word]) -> None:
    if all(number(w) == 0 for w in values):
        contest.zero.append(code)
        return
    if code in contest.rows:
        raise ValueError(f"{contest.key}: municipality {code} is printed twice")
    row: list[Word | None] = list(values)
    contest.rows[code] = row


@cache
def read(year: int) -> tuple[dict[str, Contest], list[Block]]:
    """Every contest of a year's memoria, and its mayoral blocks."""
    m = BY_YEAR[year]
    if m.layout == "files2003":
        return _files2003().read(), []
    if m.layout == "scan":
        return _scans().read(year), []
    path = raw.cached(SOURCE, url(m)).path
    if m.layout == "cards":
        return _read_cards(path, m), []
    if m.layout == "report2007":
        return _read_2007(path), []
    if m.layout == "report2011":
        return _read_2011(path, m), []
    contests: dict[str, Contest] = {}
    district = ""
    for table in tables(path, *m.tables):
        found = DISTRICT.search(table.title)
        if found or ("distritales" in table.title and district):
            if found:
                district = "00" if found.group(2) else f"{department_code(found.group(1)):02d}"
            ck = f"district:{district}"
            contest = contests.setdefault(ck, Contest(ck, "district", table.columns))
            if [label(c) for c in contest.columns] != [label(c) for c in table.columns]:
                raise ValueError(f"{ck}: columns differ across its tables")
            rows = list(table.rows)
            if district == "00":
                (row,) = rows
                contest.declared = {c: number(w) for c, w in zip(table.columns, row.values, strict=True)}
                _add_row(contest, "0101", row.values)
                continue
            if found and found.group(1) and "municipios" not in table.title and rows:
                contest.declared = {c: number(w) for c, w in zip(table.columns, rows[0].values, strict=True)}
                rows = rows[1:]
            for row in rows:
                code = match(row.name, departments_by_code()[int(district)])
                if code is None or int(code[:2]) != int(district):
                    raise ValueError(f"{ck}: no municipality for {row.name!r} on page {row.values[0].page}")
                _add_row(contest, code, row.values)
            continue
        kind = _national_kind(table.title)
        if kind is None:
            continue
        contest = contests.setdefault(kind, Contest(kind, kind, table.columns))
        if [label(c) for c in contest.columns] != [label(c) for c in table.columns]:
            raise ValueError(f"{kind}: columns differ across its tables: {table.title}")
        named = [(row, match(row.name, row.section)) for row in table.rows]
        if len(table.rows) <= 23:
            # A summary: the national total, or one row per department.
            totals = [row for row in table.rows if fold(row.name).startswith("TOTAL") or len(table.rows) == 1]
            for row in table.rows:
                if row not in totals:
                    cells: list[Word | None] = list(row.values)
                    contest.departments[department_code(row.name)] = cells
            if totals:
                contest.declared = {c: number(w) for c, w in zip(table.columns, totals[0].values, strict=True)}
            elif not contest.declared:
                contest.declared = {
                    c: sum(number(row.values[i]) for row in table.rows) for i, c in enumerate(table.columns)
                }
            continue
        for row, code in named:
            if code is None:
                raise ValueError(f"{kind}: no municipality for {row.name!r} on page {row.values[0].page}")
            _add_row(contest, code, row.values)
    found_blocks = blocks(path, *m.blocks)
    for block in found_blocks:
        code = match(block.town, block.department)
        if code is None:
            raise ValueError(f"no municipality for the block of {block.town}, {block.department}")
        ck = f"municipal:{code}"
        if ck in contests:
            raise ValueError(f"{ck}: two blocks")
        contest = contests[ck] = Contest(ck, "municipal", [s for s, _, _ in block.rows])
        contest.rows[code] = [w for _, _, w in block.rows]
        if block.rerun:
            contest.kind = "rerun"
    return contests, found_blocks


def _read_cards(path: Path, m: Memoria) -> dict[str, Contest]:
    contests: dict[str, Contest] = {}
    for card in cards(path, *m.tables):
        if card.kind == "municipal":
            code = match(card.place, card.department)
            if code is None:
                raise ValueError(f"no municipality for {card.place}, {card.department}")
            ck, unit = f"municipal:{code}", code
        elif card.kind == "district":
            dep = department_code(card.department)
            ck = "district:00" if card.place else f"district:{dep:02d}"
            unit = "0101" if card.place else f"D{dep:02d}"
        else:
            ck, unit = card.kind, NATION
        if ck in contests:
            raise ValueError(f"{m.year} {ck}: printed twice")
        contest = contests[ck] = Contest(ck, card.kind, [name for name, _ in card.rows])
        contest.rows[unit] = [w for _, w in card.rows]
    return contests


@cache
def departments_by_code() -> dict[int, str]:
    return {dep: d for (dep, _), (d, _) in places().items() if dep not in tse.ABROAD and dep != tse.CENTRAL}


def department_code(text: str) -> int:
    folded = department(text)
    for code, name in departments_by_code().items():
        if fold(name) == folded:
            return code
    raise ValueError(f"no department {text!r}")


def compare(year: int) -> dict[str, dict[str, tuple[int, int]]]:
    """Each contest's municipal sums against the totals the memoria prints for it, where they differ."""
    contests, _ = read(year)
    report: dict[str, dict[str, tuple[int, int]]] = {}
    for ck, contest in contests.items():
        if not contest.declared:
            continue
        sums = {c: sum(number(row[i]) for row in contest.rows.values()) for i, c in enumerate(contest.columns)}
        diff = {
            c: (sums[c], contest.declared[c])
            for c in contest.columns
            if c in contest.declared and sums[c] != contest.declared[c]
        }
        if diff:
            report[ck] = diff
    return report


def _department_of(code: str, contest: Contest) -> int:
    """A municipality's department, where a memoria prints the Distrito Central apart from the department's rest."""
    if code == "0101" and 0 in contest.departments:
        return 0
    return int(code[:2]) or 1


def compare_departments(year: int) -> dict[str, dict[int, dict[str, tuple[int, int]]]]:
    """Each national contest's municipal sums by department against the department totals the memoria prints."""
    contests, _ = read(year)
    report: dict[str, dict[int, dict[str, tuple[int, int]]]] = {}
    for ck, contest in contests.items():
        for dep, values in contest.departments.items():
            rows = [row for code, row in contest.rows.items() if _department_of(code, contest) == dep]
            sums = [sum(number(row[i]) for row in rows) for i in range(len(contest.columns))]
            diff = {
                c: (sums[i], number(values[i])) for i, c in enumerate(contest.columns) if sums[i] != number(values[i])
            }
            if diff:
                report.setdefault(ck, {})[dep] = diff
    return report


def unit_area(code: str) -> tuple[str, str]:
    """The scheme and area of a unit: the nation, a deputies' district, or a municipality."""
    if code == NATION:
        return "gt.pais", COUNTRY
    if code == "D01":
        return "gt.distrito", tse.area("gt.distrito", "01")
    if code.startswith("D"):
        return "gt.departamento", tse.area("gt.departamento", code[1:])
    return "gt.municipio", tse.area("gt.municipio", code)


def unbalanced(contest: Contest) -> list[str]:
    """The municipalities whose printed votes, null votes, and blank votes do not add up to the votes cast."""
    labels = [label(c) for c in contest.columns]
    if "EMITIDOS" not in labels:
        return []
    cast = labels.index("EMITIDOS")
    found: list[str] = []
    for code, row in sorted(contest.rows.items()):
        values = [number(w) for w in row]
        counted = sum(v for i, v in enumerate(values) if labels[i] not in ("EMITIDOS", "VALIDOS", "PADRON"))
        if counted != values[cast]:
            found.append(_town(code))
    return found


def _units(year: int) -> set[str]:
    contests, _ = read(year)
    return {code for contest in contests.values() for code in contest.rows}


def _town(code: str) -> str:
    names = places()
    dep, mun = int(code[:2]), int(code[2:])
    return (names.get((dep, mun)) or names[(tse.CENTRAL, mun)])[1]


def national_labels(year: int) -> set[str]:
    contests, _ = read(year)
    found = {
        label(c)
        for contest in contests.values()
        if contest.kind not in ("municipal", "rerun")
        for c in contest.columns
        if label(c) not in ACCOUNTS
    }
    return found | _files2003().alliances() if year == 2003 else found


def party(year: int, text: str, code: str) -> str:
    """A national organization by its label; a civic committee by its label within its municipality."""
    found = label(text)
    if found in national_labels(year):
        return key("party", found)
    return key("party", "committee", code, found)


COALITION = re.compile(r"-(?!MAIZ|MAÍZ|NACI)", re.IGNORECASE)


def _organizations(year: int) -> dict[str, tuple[str, str, str]]:
    """Each organization's id, with its printed label and its kind."""
    contests, _ = read(year)
    found: dict[str, tuple[str, str, str]] = {}
    national = national_labels(year)
    for contest in contests.values():
        for column in contest.columns:
            if label(column) in ACCOUNTS:
                continue
            code = next(iter(contest.rows)) if contest.rows else ""
            id = party(year, column, code)
            if id in found:
                continue
            printed = " ".join(column.replace("- ", "-").split())
            if label(column) not in national:
                kind = "independent_group"
            elif COALITION.search(printed) or printed.count("-") >= 2 or label(column) in BLOCS:
                kind = "bloc"
            else:
                kind = "party"
            found[id] = (label(column), printed, kind)
    return found


def registry(year: int) -> Package:
    m = BY_YEAR[year]
    p = Package(
        f"{SOURCE}/{year}-registry",
        SOURCE,
        f"The political parties, coalitions, and civic committees on Guatemala's {year} ballots as the Tribunal "
        "Supremo Electoral's memoria prints them, with the memoria as a source",
        depends=["gt.tse/geo"],
    )
    p.external |= {GUATEMALA}
    contests, _ = read(year)
    source = key("source", year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral",
        title=f"Memoria de Elecciones Generales {year}",
        url=url(m),
        license=LICENSE,
        license_url=f"{SITE}/informacion-publica",
        attribution="Tribunal Supremo Electoral (TSE), Guatemala",
    )
    artifact_ids = {a.path: p.artifact(a, source) for a in artifacts(m)}
    pages = {w.page for contest in contests.values() for row in contest.rows.values() for w in row if w}
    for page in sorted(pages):
        artifact, local = located(m, page)
        width, height = SIZES[(artifact.path, local)]
        p.add(
            "source_page",
            id=key("page", year, page),
            artifact_id=artifact_ids[artifact.path],
            page_number=local,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{local}.png",
            width=width,
            height=height,
        )
    if NATION in _units(year):
        p.external.add(COUNTRY)
        p.add(
            "scheme",
            code="gt.pais",
            polity_id=GUATEMALA,
            kind="administrative",
            rank=0,
            noun="country",
            code_system="tse",
        )
    for id, (_, printed, kind) in sorted(_organizations(m.year).items()):
        p.add("party", id=id, kind=kind, polity_id=GUATEMALA)
        p.name(id, printed, "es", "abbreviation")
    return p


SOURCE_NOTE = (
    "Read from the text layer of the Tribunal Supremo Electoral's election report (memoria electoral), with each "
    "number's page and box. The memoria prints each contest's results by municipality, the finest unit the Tribunal "
    "publishes for this election; its per-table files are no longer online. Null votes are stored as invalid "
    "ballots, blank votes as blank ballots, and the votes cast (emitidos) as ballots cast."
)
ZERO_NOTE = " A municipality the memoria prints with every cell zero, where the election was annulled, is left out."
LEFT_OUT = " A municipality the memoria prints with every cell zero is left out."
TITLES = {
    "president": ("Presidente y Vicepresidente de la República", "presidente"),
    "president2": ("Presidente y Vicepresidente de la República, segunda elección", "presidente-segunda-eleccion"),
    "national": ("Diputados al Congreso por Lista Nacional", "diputados-lista-nacional"),
    "parlacen": ("Diputados al Parlamento Centroamericano", "parlamento-centroamericano"),
}


def _contest_place(ck: str) -> tuple[str, str, str]:
    """A contest's area, Spanish title, and slug."""
    kind, _, code = ck.partition(":")
    names = places()
    if kind in TITLES:
        return COUNTRY, *TITLES[kind]
    if kind == "district":
        if code == "00":
            return (
                tse.area("gt.municipio", "0101"),
                "Diputados al Congreso por el Distrito Central",
                "diputados-distrito-central",
            )
        if code == "01":
            return (
                tse.area("gt.distrito", "01"),
                "Diputados al Congreso por el Distrito de Guatemala",
                "diputados-guatemala",
            )
        text = departments_by_code()[int(code)]
        return (
            tse.area("gt.departamento", code),
            f"Diputados al Congreso por {text}",
            f"diputados-{changeset.slug(text)}",
        )
    dep, mun = int(code[:2]), int(code[2:])
    town = (names.get((dep, mun)) or names[(tse.CENTRAL, mun)])[1]
    text = departments_by_code()[dep]
    return (
        tse.area("gt.municipio", code),
        f"Corporación Municipal de {town}, {text}",
        f"corporacion-{changeset.slug(text)}-{changeset.slug(town)}",
    )


ELECTION_NAMES = {
    "general": ("general", "{year} Guatemalan general election", "Elecciones Generales de {year}", "general"),
    "runoff": (
        "runoff",
        "{year} Guatemalan presidential election, second round",
        "Segunda Elección Presidencial de {year}",
        "runoff",
    ),
    "rerun": (
        "local",
        "{year} Guatemalan repeated municipal elections",
        "Repetición de elecciones municipales de {year}",
        "municipal-repeat",
    ),
}
BODIES = {"national": "congress", "district": "congress", "parlacen": "parlacen"}
SYSTEMS = {"president": "president", "president2": "president", "parlacen": "parlacen", "national": "congress"}


def days(year: int) -> list[date]:
    m = BY_YEAR[year]
    return [m.general, m.runoff]


def packages() -> list[str]:
    return [
        key_
        for m in MEMORIA_LIST
        for key_ in (f"{SOURCE}/{m.year}-registry", *(f"{SOURCE}/{day}" for day in days(m.year)))
    ]


def _family(contest: Contest) -> str:
    if contest.kind == "president2":
        return "runoff"
    if contest.kind == "rerun":
        return "rerun"
    return "general"


def election(year: int, day: date) -> Package:
    m = BY_YEAR[year]
    contests, _ = read(year)
    first = day == m.general
    chosen = [c for c in contests.values() if (_family(c) == "general") == first]
    depends = ["gt.tse/geo", "gt.tse/registry", f"{SOURCE}/{year}-registry"]
    if not first:
        depends.append(f"{SOURCE}/{m.general}")
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Guatemala's elections of {day:%-d %B %Y} by municipality from the Tribunal Supremo Electoral's {year} "
        "memoria electoral",
        depends,
    )
    p.external |= {GUATEMALA, COUNTRY}
    for artifact in artifacts(m):
        p.uses(artifact)
    source = key("source", year)
    elections: dict[str, str] = {}

    def held(family: str) -> str:
        if family not in elections:
            kind, english, spanish, word = ELECTION_NAMES[family]
            id = elections[family] = key("election", day, family)
            p.add(
                "election",
                id=id,
                polity_id=GUATEMALA,
                kind=kind,
                date_from=day,
                date_to=day,
                status="held",
                rerun_of_id=key("election", m.general, "general") if family == "rerun" else None,
            )
            p.name(id, english.format(year=year), "en", "official")
            p.name(id, spanish.format(year=year), "es", "native", False)
            p.add("slug", path=f"gt/{day}-{word}", entity_id=id, is_current=True)
        return elections[family]

    units: dict[str, str] = {}

    def unit(code: str) -> str:
        if code not in units:
            id = units[code] = key("unit", day, code)
            scheme, area_id = unit_area(code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=GUATEMALA,
                scheme_code=scheme,
                code=f"{day}:{code}",
                area_relation="equals",
                area_id=area_id,
                kind="aggregate",
            )
        return units[code]

    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    zeros: list[str] = []
    tally: Counter[str] = Counter()
    off: list[str] = []
    for contest in sorted(chosen, key=lambda c: c.key):
        family = _family(contest)
        election_id = held(family)
        kind, _, code = contest.key.partition(":")
        place, text, slug = _contest_place(contest.key.replace("rerun", "municipal"))
        ck = contest.key if kind != "rerun" else f"municipal:{code}"
        contest_id = key("contest", day, ck)
        president = kind in ("president", "president2")
        local = kind in ("municipal", "rerun")
        body_id = tse.council(code) if local else tse.body(BODIES[kind]) if kind in BODIES else None
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="office" if president else "list_allocation",
            office_id=tse.PRESIDENCY if president else None,
            body_id=body_id,
            district_area_id=place,
            magnitude={"president": 1, "president2": 1, "national": m.seats, "parlacen": 20}.get(kind),
            round_number=2 if kind == "president2" else 1,
            previous_round_id=key("contest", m.general, "president") if kind == "president2" else None,
            electoral_system_id=tse.system("municipal" if local else SYSTEMS.get(kind, "congress")),
            default_result_kind="counted",
        )
        p.name(contest_id, text, "es", "official")
        word = ELECTION_NAMES[family][3]
        p.add("slug", path=f"gt/{day}-{word}/{slug}", entity_id=contest_id, is_current=True)
        result_set = key("result_set", day, ck)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
        )
        note = (_scans().note(year, contest) if m.layout == "scan" else SOURCE_NOTE) + (
            ZERO_NOTE if year in (2015, 2019) else LEFT_OUT
        )
        if contest.zero:
            zeros.extend(contest.zero)
            note += f" Left out as printed with every cell zero: {', '.join(sorted(contest.zero))}."
        if contest.key.startswith("rerun:") and f"municipal:{code}" in contests:
            note += (
                f" The mayoral election tied on {m.general:%-d %B}; the Tribunal held a second election between the "
                f"tied parties on {m.runoff:%-d %B}, and the memoria prints the result of that second election."
            )
        elif family == "rerun":
            note += (
                f" The Tribunal annulled this municipality's elections of {m.general:%-d %B} and held them again on "
                f"{m.runoff:%-d %B}; the memoria prints the mayoral results of the repeated election."
            )
        if contest.seats:
            note += " Seats are counted from the memoria's list of the elected, for the titular posts only."
        if contest.notes:
            note += " " + " ".join(contest.notes)
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        uneven = unbalanced(contest)
        if uneven:
            p.add(
                "note",
                id=key("note", result_set, "uneven"),
                entity_id=result_set,
                kind="caveat",
                body_md="In the memoria, the organizations' votes with the null and blank votes do not add up to the "
                f"votes cast for {', '.join(uneven)}. The figures are stored as printed.",
                lang="en",
            )
            tally["uneven"] += len(uneven)
        sums = {c: sum(number(row[i]) for row in contest.rows.values()) for i, c in enumerate(contest.columns)}
        if any(sums[c] != v for c, v in contest.declared.items()):
            tally["declared"] += 1
            off.append(text)
        options: dict[int, str] = {}
        position = 0
        home = code if local else ""
        for index, column in enumerate(contest.columns):
            if label(column) in ACCOUNTS:
                continue
            position += 1
            option = options[index] = key("option", day, ck, label(column))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="ticket" if president else "list",
                party_id=party(year, column, home),
                ballot_position=position,
                ballot_label=" ".join(column.replace("- ", "-").split()),
            )
        for column, count in contest.seats.items():
            wanted = [i for i, c in enumerate(contest.columns) if label(c) == column]
            if not wanted:
                raise ValueError(f"{contest.key}: seats for {column}, which is not on its ballot")
            p.add("outcome", result_set_id=result_set, option_id=options[wanted[0]], kind="won", seats=count)
        for town, row in contest.rows.items():
            unit_id = unit(town)
            for index, (column, w) in enumerate(zip(contest.columns, row, strict=True)):
                if w is None:
                    continue
                option_id = options.get(index)
                measure = VOTES if option_id else ACCOUNTS[label(column)]
                values.append((result_set, unit_id, option_id, measure, number(w)))
                locators.append((result_set, unit_id, option_id, measure, key("page", year, w.page), *w.box))
        for index, column in enumerate(contest.columns):
            if column in contest.declared:
                option_id = options.get(index)
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option_id,
                    measure=VOTES if option_id else ACCOUNTS[label(column)],
                    value=contest.declared[column],
                )
    facts = pl.DataFrame(
        values,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
        orient="row",
    )
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="value",
        ),
    )
    located = pl.DataFrame(
        locators,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "page_id": pl.String,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    p.frame(
        "value_locator",
        located.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            page_id="page_id",
            source_id=pl.lit(source),
            x0="x0",
            y0="y0",
            x1="x1",
            y1="y1",
        ),
    )
    if off:
        p.accept(
            "accounting.declared",
            f"In {len(off)} contest{'s' if len(off) > 1 else ''} the memoria's printed totals differ from the sum of its own municipal rows "
            f"({'; '.join(sorted(off))}); both are stored as printed, the totals as declared",
        )
    print(f"{day}: {facts.height:,} values, {len(chosen)} contests, {len(units)} units, {dict(tally)}", flush=True)
    return p


def parse() -> None:
    for m in MEMORIA_LIST:
        for name, diff in compare(m.year).items():
            print(f"{m.year} {name}: {diff}", flush=True)
        registry(m.year).write()
        for day in days(m.year):
            election(m.year, day).write()


def emit() -> None:
    package.run("load", packages())


# The 2019 memoria prints one contest per page: its organizations and their votes, beside the seats adjudicated.


@dataclass
class Card:
    kind: str
    place: str
    """The municipality's or department's printed name; empty for a national contest."""
    department: str
    rows: list[tuple[str, Word]] = field(default_factory=list)


CARD_KINDS = (
    ("Corporaciones Municipales", "municipal"),
    ("Diputados Distritales", "district"),
    ("Lista Nacional", "national"),
    ("Primera Elección Presidencial", "president"),
    ("Segunda Elección Presidencial", "president2"),
)


def cards(path: Path, first: int, last: int) -> list[Card]:
    """Each contest page's organizations and votes, a page without a title continuing the one before."""
    found: list[Card] = []
    pages = words(path, first, last)
    for number_ in range(first, last + 1):
        rows = lines(pages[number_])
        header = next(
            (
                (row[i], row[i + 1])
                for row in rows
                for i in range(len(row) - 1)
                if fold(row[i].text) == "ORGANIZACION" and fold(row[i + 1].text) == "VOTOS"
            ),
            None,
        )
        if header is None:
            continue
        org, votes = header
        above = [text(row) for row in rows if row[0].yc < org.yc - 2]
        kind = next((k for title, k in CARD_KINDS if any(title in line for line in above)), None)
        if kind is not None:
            place, department_ = "", ""
            for i, line in enumerate(above):
                found_dep = re.match(r"\s*Departamento de (.+)$", line)
                if found_dep:
                    department_ = found_dep.group(1).strip()
                    if i and kind != "national":
                        place = above[i - 1].strip()
                    break
            if kind == "district" and fold(place) != "DISTRITO CENTRAL":
                place = ""
            found.append(Card(kind, place, department_))
        elif not found:
            continue
        card = found[-1]
        for row in rows:
            if row[0].yc <= org.yc + 2:
                continue
            count = [w for w in row if is_number(w.text) and votes.x0 - 45 <= w.x1 <= votes.x1 + 15]
            if not count:
                continue
            name = [w for w in row if w.x0 >= org.x0 - 10 and w.x1 < count[0].x0 and not is_number(w.text)]
            if name:
                card.rows.append((" ".join(w.text for w in name), count[0]))
    return found


# The 2007 memoria prints a page of results for each municipality, each department, and the Distrito Central. ------


def _name(tokens: list[Word]) -> str:
    """A label the memoria sets with letter spacing ("P A N") as one word."""
    found = [w.text for w in tokens]
    if len(found) > 1 and all(len(t) == 1 for t in found):
        return "".join(found)
    return " ".join(found)


@dataclass
class Grid:
    """The cells of one contest read from many pages, by area and by column label."""

    printed: dict[str, str] = field(default_factory=dict)
    """Each column's label as first printed, in order of first appearance."""
    cells: dict[str, dict[str, Word]] = field(default_factory=dict)
    """Each municipality's cells by column label."""
    totals: dict[int, dict[str, Word]] = field(default_factory=dict)
    """The printed totals of each department by its code, and of the Distrito Central under 0."""
    declared: dict[str, Word] = field(default_factory=dict)
    """The printed total of the whole contest."""
    seats: Counter[str] = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    def put(self, name: str, w: Word, into: dict[str, Word]) -> None:
        column = label(name)
        self.printed.setdefault(column, name)
        into[column] = w


ACCOUNT_ORDER = ("VALIDOS", "NULOS", "BLANCOS", "EMITIDOS", "PADRON")


def _contest(key_: str, kind: str, grid: Grid, declared: dict[str, Word] | None = None) -> Contest:
    """A contest from a grid: its organizations in order of appearance, then its accounting columns."""
    used = {c for cells in grid.cells.values() for c in cells} | set(grid.declared)
    for cells in grid.totals.values():
        used |= set(cells)
    order = [
        *(c for c in grid.printed if c not in ACCOUNTS and c in used),
        *(c for c in ACCOUNT_ORDER if c in grid.printed and c in used),
    ]
    contest = Contest(key_, kind, [grid.printed[c] for c in order])
    for unit, cells in grid.cells.items():
        row = [cells.get(c) for c in order]
        if all(w is None or number(w) == 0 for w in row):
            contest.zero.append(unit)
            continue
        contest.rows[unit] = row
    for dep, cells in grid.totals.items():
        contest.departments[dep] = [cells.get(c) for c in order]
    given = grid.declared if declared is None else declared
    contest.declared = {grid.printed[c]: number(given[c]) for c in order if c in given}
    contest.seats = dict(grid.seats)
    contest.notes = list(grid.notes)
    return contest


def _finalists(grid: Grid) -> list[str]:
    """Second-round votes the memoria prints on a party that did not run, read as the finalist's.

    Every municipality prints the two finalists' votes; where a count stands on another party's row and a finalist's
    row is empty, it is that finalist's, and its box still locates the printed cell. Zeros on other rows are dropped.
    """
    finalists = [
        c for c, n in Counter(c for cells in grid.cells.values() for c in cells if c not in ACCOUNTS).most_common(2)
    ]
    notes: list[str] = []
    for unit, cells in grid.cells.items():
        for column in [c for c in cells if c not in ACCOUNTS and c not in finalists]:
            w = cells.pop(column)
            if number(w) == 0:
                continue
            missing = [f for f in finalists if f not in cells]
            if len(missing) != 1:
                raise ValueError(f"{unit}: second-round votes for {column} and no single finalist missing")
            cells[missing[0]] = w
            notes.append(
                f"{_town(unit)}: {w.text} printed on {grid.printed[column]}, read as {grid.printed[missing[0]]}"
            )
    return notes


def _find(rows: list[list[Word]], *needles: str) -> int | None:
    for i, row in enumerate(rows):
        folded = [fold(w.text) for w in row]
        if all(n in folded for n in needles):
            return i
    return None


def _side(rows: list[list[Word]], low: float, high: float) -> list[list[Word]]:
    """The rows of one of two tables set side by side, cut by the x of their words."""
    return [r for r in (sorted((w for w in row if low <= w.x0 < high), key=lambda w: w.x0) for row in rows) if r]


def _rows_of(found: list[Word], limit: float, lowest: float = 0.0) -> list[tuple[list[Word], list[Word]]]:
    """Table rows from the words below a header: each label (its words left of `limit`) with the counts nearest it.

    The memorias set a label and its counts a few points apart, so a count goes to the label line closest to it.
    """
    labels = lines([w for w in found if w.x0 < limit and not is_number(w.text)], tolerance=3)
    counts = [w for w in found if w.x0 >= max(limit, lowest) and is_number(w.text)]
    owned: dict[int, list[Word]] = defaultdict(list)
    for w in counts:
        near = min(
            range(len(labels)), key=lambda i: abs(sum(x.yc for x in labels[i]) / len(labels[i]) - w.yc), default=None
        )
        if near is not None and abs(sum(x.yc for x in labels[near]) / len(labels[near]) - w.yc) <= 8:
            owned[near].append(w)
    return [(labels[i], sorted(ws, key=lambda w: w.x0)) for i, ws in sorted(owned.items())]


def _president(page: list[Word], put: Callable[[str, str, Word], None]) -> None:
    """The presidential table of a page: each party's votes in the first round and the second."""
    rows = lines(page, tolerance=3)
    head = _find(rows, "PRIMERA", "VUELTA")
    columns = None if head is None else _find(rows[head:], "RESULTADOS", "RESULTADOS")
    if head is None or columns is None:
        raise ValueError(f"page {page[0].page}: no presidential table")
    marks = [w.x0 for w in rows[head + columns] if fold(w.text) == "RESULTADOS"]
    cut = (marks[0] + marks[1]) / 2 + 10
    body = [w for row in rows[head + columns + 1 :] for w in row]
    end = next((w for w in body if fold(w.text) == "ABSTENCIONISMO"), None)
    if end is not None:
        body = [w for w in body if w.yc <= end.yc + 3]
    sigla = min(w.x0 for w in body)
    for label_, values in _rows_of(body, marks[0] - 60):
        if label_[0].x0 > sigla + 60 or "ABSTENCIONISMO" in (fold(w.text) for w in label_):
            continue
        for w in values:
            put("president2" if w.x0 >= cut else "president", _name([x for x in label_ if x.x0 < sigla + 60]), w)


def _deputies(page: list[Word], put: Callable[[str, str, Word], None]) -> None:
    """The deputies' votes of a page, and the mayor's beside them where the page prints a municipality."""
    rows = lines(page, tolerance=3)
    start = _find(rows, "DIPUTADOS")
    if start is None:
        raise ValueError(f"page {page[0].page}: no deputies' table")
    below = [w for row in rows[start:] for w in row]
    mayor = next((w for w in below if fold(w.text) == "ALCALDE" and w.x0 > 300), None)
    split = mayor.x0 - 15 if mayor else 10_000.0
    left = [w for w in below if w.x0 < split]
    nacional = next(w for w in left if fold(w.text) == "NACIONAL")
    distritales = next(w for w in left if fold(w.text) == "DISTRITALES")
    for label_, values in _rows_of([w for w in left if w.yc > nacional.yc + 3], nacional.x0 - 10):
        for w in values:
            put("district" if w.x0 >= distritales.x0 - 5 else "national", _name(label_), w)
    if mayor:
        right = [w for w in below if w.x0 >= split]
        header = next(w for w in right if fold(w.text) == "RESULTADOS")
        for label_, values in _rows_of([w for w in right if w.yc > header.yc + 3], header.x0 + 5):
            put("municipal", _name(label_), values[0])


POSTS = ("ALCALDE", "SINDICO", "CONCEJAL")


def _header(page: list[Word], *needles: str) -> Word:
    """The first word of the heading row that holds all of `needles`; the pages are set mirrored left to right."""
    for row in lines(page, tolerance=3):
        folded = [fold(w.text) for w in row]
        if all(any(f.startswith(n) for f in folded) for n in needles):
            last = next(i for i, f in enumerate(folded) if f.startswith(needles[-1]))
            return next(row[i] for i in range(last, -1, -1) if folded[i].startswith(needles[0]))
    raise ValueError(f"page {page[0].page}: no heading with {needles}")


def _has_heading(rows: list[list[Word]], *prefixes: str) -> bool:
    return any(all(any(fold(w.text).startswith(p) for w in row) for p in prefixes) for row in rows)


def _corporation(page: list[Word]) -> list[list[str]]:
    """The titular posts a municipal page adjudicates, each as the folded words of its holder and organization.

    The name and the organization sit in one row and are told apart later, against the organizations that ran.
    """
    head = _header(page, "ADJUDICACION", "CORPORACION")
    box = [w for w in page if w.yc > head.yc + 3 and head.x0 - 20 <= w.x0 < head.x0 + 230]
    posts: list[list[str]] = []
    cargos: list[Word] = []
    for row in lines([w for w in box if w.x0 >= head.x0 + 135], tolerance=3):
        first = next((w for w in row if fold(w.text) in POSTS), None)
        if first is not None and not any(fold(w.text) == "SUPLENTE" for w in row):
            cargos.append(first)
    if not cargos:
        raise ValueError(f"page {page[0].page}: no adjudicated posts")
    edge = min(c.x0 for c in cargos) - 8
    between = [w for w in box if head.x0 - 20 <= w.x0 < edge and not is_number(w.text)]
    for cargo in cargos:
        row = sorted((w for w in between if abs(w.yc - cargo.yc) <= 6), key=lambda w: w.x0)
        posts.append([label(w.text) for w in row])
    return posts


UNRESOLVED: list[tuple[str, list[str], list[str]]] = []


def _adjudicated(posts: list[list[str]], known: set[str], where: str) -> Counter[str]:
    """Seats by organization: each post ends in the label of one organization that ran.

    A civic committee's label in the adjudications can differ from its label in the results, so a post that ends in
    no known label is matched on its last words to the nearest label that ran.
    """
    parties = sorted(k for k in known if k not in ACCOUNTS)
    found: Counter[str] = Counter()
    for post in posts:
        text_ = "".join(post)
        ends = [k for k in parties if text_.endswith(k)]
        if ends:
            found[max(ends, key=len)] += 1
            continue
        tails = ["".join(post[-size:]) for size in (3, 2, 1)]
        near = next(
            (m_[0] for tail in tails[::-1] if (m_ := get_close_matches(tail, parties, n=1, cutoff=0.6))),
            next((k for tail in tails if len(tail) >= 6 for k in parties if k.endswith(tail)), None),
        )
        if near is None:
            UNRESOLVED.append((where, post, parties))
            continue
        found[near] += 1
    return found


def _district_department(page: list[Word]) -> Counter[str]:
    """The deputies a department's page adjudicates, each row led by its organization."""
    head = _header(page, "ADJUDICACION", "DIPUTADO", "DISTRITAL")
    box = [w for w in page if w.yc > head.yc + 3 and head.x0 - 30 <= w.x0 < head.x0 + 230]
    found: Counter[str] = Counter()
    for row in lines(box, tolerance=3):
        party = [w for w in row if w.x0 < head.x0 + 20]
        if party and len(row) > len(party) and fold(row[0].text) != "TOMO":
            found[label(_name(party))] += 1
    return found


def _district_central(page: list[Word]) -> Counter[str]:
    """The deputies the Distrito Central's page adjudicates, each row ending in its organization."""
    head = _header(page, "ADJUDICACION", "DIPUTADO")
    stop = _header(page, "POSTULACION")
    box = [w for w in page if head.yc + 3 < w.yc < stop.yc - 3 and head.x0 <= w.x0 + 25 and w.x0 < head.x0 + 220]
    found: Counter[str] = Counter()
    for row in lines(box, tolerance=3):
        party = [w for w in row if w.x0 >= head.x0 + 140]
        if party:
            found[label(_name(party))] += 1
    return found


def _padron(page: list[Word]) -> Word | None:
    """The registered voters of the area a page describes, from its "Total en el Municipio" row."""
    for row in lines([w for w in page if w.x0 >= 250], tolerance=3):
        folded = [fold(w.text) for w in row]
        if folded[:3] == ["TOTAL", "EN", "EL"] or folded[:2] == ["EN", "EL"]:
            counts = [w for w in row if is_number(w.text)]
            return counts[-1] if counts else None
    return None


def _title(rows: list[list[Word]]) -> tuple[str, str]:
    """The area a results page names: "municipality", "department", or "central", with its printed name.

    A long name wraps onto the line below the title.
    """
    for i, row in enumerate(rows):
        words_ = [fold(w.text) for w in row]
        if words_[:1] != ["RESULTADOS"] or len(words_) < 2:
            continue
        if words_[1] in ("MUNICIPIO", "DEPARTAMENTO"):
            name = " ".join(w.text for w in row[3:]) or text(rows[i + 1])
            return ("municipality" if words_[1] == "MUNICIPIO" else "department"), name
        if words_[1:3] == ["DISTRITO", "CENTRAL"]:
            return "central", "Distrito Central"
    return "", ""


def _router(
    grids: dict[str, Grid], kind: str, unit: str, district: str, department: int
) -> Callable[[str, str, Word], None]:
    """Where a results page's cells go: a municipality's cells are rows, a department's are its printed totals."""

    def put(what: str, name: str, w: Word) -> None:
        if what == "municipal":
            mayor = grids[f"municipal:{unit}"]
            mayor.put(name, w, mayor.cells.setdefault(unit, {}))
            return
        grid = grids[district] if what == "district" else grids[what]
        if kind == "department":
            into = grid.declared if what == "district" else grid.totals.setdefault(department, {})
        else:
            into = grid.cells.setdefault(unit, {})
            if kind == "central":
                if what == "district":
                    grid.declared[label(name)] = w
                else:
                    grid.totals.setdefault(0, into)
        grid.put(name, w, into)

    return put


def _read_2007(path: Path) -> dict[str, Contest]:
    pages = words(path, 67, 828)
    grids: dict[str, Grid] = defaultdict(Grid)

    def national(kind: str, name: str, w: Word) -> None:
        grids[kind].put(name, w, grids[kind].declared)

    _president(pages[67], national)
    page = pages[68]
    # The national list: its adjudications, then its national totals.
    rows = lines(page, tolerance=3)
    start = _find(rows, "SIGLAS")
    stop = _find(rows, "RESULTADOS", "DIPUTADOS")
    if start is None or stop is None:
        raise ValueError("page 68: no national list")
    for row in rows[start + 1 : stop]:
        grids["national"].seats[label(_name([w for w in row if w.x0 < 195]))] += 1
    for row in rows[stop + 2 :]:
        counted = [w for w in row if is_number(w.text)]
        name = [w for w in row if not is_number(w.text) and w.x0 < 300]
        if counted and name and fold(name[0].text) != "TOMO":
            national("national", _name(name), counted[-1])
    department = 0
    seats: Counter[str] = Counter()
    posts: list[list[str]] = []
    annulled = False
    registered: Word | None = None
    reruns: set[str] = set()
    for number in range(69, 829):
        page = pages[number]
        rows = lines(page, tolerance=3)
        full = fold(" ".join(text(r) for r in rows))
        if _find(rows, "JUNTA", "ELECTORAL", "MUNICIPAL") is not None:
            annulled = "NULIDAD" in full
            posts = []
        elif _find(rows, "JUNTA", "ELECTORAL", "DEPARTAMENTAL") is not None:
            seats = _district_department(page)
        elif "JUNTA ELECTORAL DEL DISTRITO CENTRAL" in full:
            seats = _district_central(page)
        if _has_heading(rows, "ADJUDICACION", "CORPORACION"):
            posts = _corporation(page)
        if "JUNTA ELECTORAL" in full:
            registered = _padron(page)
        kind, name = _title(rows)
        if not kind:
            continue
        if kind == "department":
            department = department_code(name)
        unit = ""
        if kind == "central":
            unit = match("DISTRITO CENTRAL", "GUATEMALA") or ""
            department = 1
        elif kind == "municipality":
            unit = match(name, departments_by_code()[department]) or ""
            if not unit or int(unit[:2]) != department:
                raise ValueError(f"page {number}: no municipality for {name!r} in department {department}")
        district = f"district:{'00' if kind == 'central' else f'{department:02d}'}"
        if kind != "municipality":
            grids[district].seats = Counter(seats)
        put = _router(grids, kind, unit, district, department)

        try:
            _president(page, put)
            _deputies(page, put)
        except (StopIteration, ValueError, IndexError) as error:
            raise ValueError(f"page {number}: {name}: {error!r}") from error
        if registered is not None:
            for what in ("president", "president2"):
                put(what, "Padron", registered)
            registered = None
        if kind != "department":
            mayor = grids[f"municipal:{unit}"]
            mayor.seats = _adjudicated(posts, set(mayor.printed), f"page {number}: {name}")
            if not posts:
                mayor.notes.append("The memoria prints no adjudication of this municipal corporation.")
            posts = []
            if annulled and kind == "municipality":
                reruns.add(unit)
            annulled = False
    corrections = _finalists(grids["president2"])
    contests = {
        k: _contest(
            k, "rerun" if k.partition(":")[2] in reruns and k.startswith("municipal") else k.partition(":")[0], g
        )
        for k, g in grids.items()
    }
    contests["president2"].notes = corrections
    return contests


# The 2011 memoria prints each contest's tables by department, its district tables set sideways, and one block per
# municipality. ---------------------------------------------------------------------------------------------------

TITLE_WORDS = {"DIPUTADOS", "DISTRITALES", "RESULTADOS", "FINALES", "%", "ABST.", "ABST", "PARTICIPACION", "."}
VOCABULARY = {
    "PP", "PAN", "UCN", "UNIONISTA", "CREO", "LIDER", "ADN", "CASA", "VIVA", "EG", "UNE", "GANA", "FRG", "FCN",
    "VICTORIA", "PAIS", "WINAQ", "URNG", "MAIZ", "ANN", "NULOS", "BLANCO", "EN", "EMITIDOS", "PADRON",
}  # fmt: skip


def clean_header(name: str) -> str:
    """A column's label from the words stacked above it, without the table's title or its percentage column."""
    found = [t for t in name.split() if t not in TITLE_WORDS and not re.fullmatch(r"[\d.]+%", t)]
    found = [t for t in found if all(part in VOCABULARY for part in t.split("-") if part)]
    if "NULOS" in found and "EN" in found:
        found.remove("EN")
    if found == ["EN", "BLANCO"]:
        found = ["BLANCO"]
    return " ".join(found)


PARTY_ALIASES = {"VIVA": "VIVA-EG"}


def clean_party(name: str) -> str:
    return PARTY_ALIASES.get(name, name)


def _is_total(name: str) -> bool:
    return fold(name).startswith("TOTAL")


def _read_tables(path: Path, first: int, last: int, grids: dict[str, Grid], kinds: dict[str, str]) -> None:
    """The president, runoff, and list tables: one summary of the departments, then a table for each department."""
    for table in tables(path, first, last, band=20):
        title = fold(table.title)
        kind = next((k for needle, k in kinds.items() if title.startswith(needle)), None)
        if kind is None or not table.rows or "DEPARTAMENTO" not in title:
            continue
        grid = grids[kind]
        names = [clean_party(clean_header(c)) for c in table.columns]
        summary = "TOTALES FINALES" in title
        codes = iter([] if summary else _towns(table, table.rows[0].section))
        for row in table.rows:
            if _is_total(row.name) and summary:
                where = grid.declared
            elif _is_total(row.name):
                continue
            elif summary:
                where = grid.totals.setdefault(
                    0 if fold(row.name) == "DISTRITO CENTRAL" else department_code(row.name), {}
                )
            else:
                where = grid.cells.setdefault(next(codes), {})
            for name, w in zip(names, row.values, strict=True):
                grid.put(name, w, where)


def _read_districts(path: Path, first: int, last: int) -> dict[str, Contest]:
    raw_pages, _ = raw_words(path, first, last)
    found = spread_tables(path, first, last)
    contests: dict[str, Contest] = {}
    for number in range(first, last + 1):
        left = [w for w in raw_pages[number] if w.x1 <= 580]
        titled = [text(r) for r in lines(left, 3) if r and fold(r[0].text) == "DISTRITO"]
        if not titled or len(found[number]) != 1:
            continue
        table = found[number][0]
        central = "CENTRAL" in fold(titled[0])
        dep = 1 if central else department_code(titled[0].removeprefix("DISTRITO DE "))
        ck = "district:00" if central else f"district:{dep:02d}"
        grid = Grid()
        names = [clean_party(clean_header(c)) for c in table.columns]
        codes = iter([] if central else _towns(table, departments_by_code()[dep]))
        for row in table.rows:
            if central:
                code = match("DISTRITO CENTRAL", "GUATEMALA") or ""
                where = grid.cells.setdefault(code, {})
                for name, w in zip(names, row.values, strict=True):
                    grid.put(name, w, where)
                    grid.declared[label(name)] = w
            elif _is_total(row.name):
                for name, w in zip(names, row.values, strict=True):
                    grid.put(name, w, grid.declared)
            else:
                where = grid.cells.setdefault(next(codes), {})
                for name, w in zip(names, row.values, strict=True):
                    grid.put(name, w, where)
        known = {label(n) for n in names}
        grid.seats = _seats_list(left, known, "PARTIDO", f"page {number}")
        contests[ck] = _contest(ck, "district", grid)
    return contests


def _seats_list(page: list[Word], known: set[str], header: str, where: str) -> Counter[str]:
    """The seats an adjudication table lists: one row for each deputy, led by the deputy's organization."""
    rows = lines(page, 3)
    start = next((i for i, r in enumerate(rows) if any(fold(w.text) == header for w in r) and r[0].x0 > 100), None)
    if start is None:
        raise ValueError(f"{where}: no adjudications")
    found: Counter[str] = Counter()
    for row in rows[start + 1 :]:
        if any(fold(w.text) in ("TOTAL", "GENERO", "TIPO") for w in row):
            break
        party = [w for w in row if w.x0 < 215]
        name = [w for w in row if w.x0 >= 215]
        if party and name:
            found[label(_name(party))] += 1
    unknown = set(found) - known
    if unknown:
        raise ValueError(f"{where}: seats for {sorted(unknown)} but only {sorted(known)} ran")
    return found


MISSING: list[tuple[str, str, str]] = []


def _blocks_2011(path: Path, first: int, last: int) -> dict[str, Grid]:
    """The mayoral results and adjudications of each municipality's block, by the key of the contest.

    A municipality whose election the Tribunal annulled prints the repeated election's results, and one whose
    mayoral election tied prints a second column for the election it held again; both are contests of the repeat.
    """
    raw_pages, _ = raw_words(path, first, last)
    grids: dict[str, Grid] = defaultdict(Grid)
    seen: set[str] = set()
    for number in range(first, last + 1):
        page = raw_pages[number]
        starts = sorted(
            w.x0
            for row in lines(page, 3)
            for i, w in enumerate(row)
            if fold(w.text) == "ELECCION"
            and w.yc < 100
            and [fold(x.text) for x in row[i + 1 : i + 3]] == ["DE", "CORPORACION"]
        )
        for index, start in enumerate(starts):
            stop = starts[index + 1] - 20 if index + 1 < len(starts) else 10_000.0
            region = [w for w in page if start - 20 <= w.x0 < stop and w.x1 < 1100]
            where = f"page {number}"
            rows = lines(region, 2.5)
            head = next(r for r in rows if fold(r[0].text) == "ELECCION")
            at = next(i for i, w in enumerate(head) if fold(w.text) == "MUNICIPAL")
            after = rows[rows.index(head) + 1 :]
            at_dep = next(i for i, r in enumerate(after) if fold(r[0].text) == "DEPARTAMENTO")
            town = " ".join([*(w.text for w in head[at + 2 :]), *(text(r) for r in after[:at_dep])])
            dep = text(after[at_dep]).removeprefix("DEPARTAMENTO DE ")
            code = match(town, dep)
            if code is None:
                MISSING.append((where, town, dep))
                continue
            if code in seen:
                raise ValueError(f"{where}: {town!r} is printed twice")
            seen.add(code)
            header = next(w for w in region if fold(w.text) == "POSTULADOS")
            partidos = next(w for w in region if fold(w.text) == "PARTIDOS" and abs(w.yc - header.yc) < 3)
            cargo = next(w for w in region if fold(w.text) == "CARGO")
            ends = next(w for w in region if fold(w.text) == "ADJUDICADA")
            final = next((w for w in region if fold(w.text) == "FINAL" and abs(w.yc - header.yc) < 12), None)
            annulled = any(fold(w.text) == "NULIDAD" for w in region)
            first_key = f"rerun:{code}" if annulled else f"municipal:{code}"
            grid = grids[first_key]
            limit = (partidos.x0 + header.x0) / 2 - 5
            body = [w for w in region if header.yc + 3 < w.yc < ends.yc - 8]
            for label_, values in _rows_of(body, limit):
                for w in values:
                    again = final is not None and w.x0 >= final.x0 - 30
                    target = grids[f"rerun:{code}"] if again else grid
                    target.put(_name(label_), w, target.cells.setdefault(code, {}))
            pcol = next(w.x0 for w in region if fold(w.text) == "PARTIDO" and abs(w.yc - cargo.yc) < 3) - 25
            posts: list[list[str]] = []
            for row in lines([w for w in region if w.yc > cargo.yc + 3], 2.5):
                if fold(row[0].text) not in POSTS or any(fold(w.text) == "SUPLENTE" for w in row):
                    continue
                near = [w for w in region if w.x0 >= pcol and abs(w.yc - row[0].yc) <= 5]
                posts.append([label(w.text) for w in sorted(near, key=lambda w: w.x0)])
            adjudicated = grids[f"rerun:{code}"] if final is not None else grid
            adjudicated.seats = _adjudicated(posts, set(adjudicated.printed), f"{where}: {town}")
    return grids


def _read_2011(path: Path, m: Memoria) -> dict[str, Contest]:
    grids: dict[str, Grid] = defaultdict(Grid)
    _read_tables(
        path,
        *m.tables,
        grids,
        {
            "ELECCION DE PRESIDENTE": "president",
            "SEGUNDA ELECCION": "president2",
            "ELECCION DE DIPUTADOS POR LISTADO": "national",
        },
    )
    _read_tables(path, *m.parlacen, grids, {"ELECCION DE DIPUTADOS AL PARLAMENTO": "parlacen"})
    raw_pages, _ = raw_words(path, m.tables[0], m.parlacen[1])
    for kind, number in (("national", 344), ("parlacen", 549)):
        left = [w for w in raw_pages[number] if w.x1 <= 580]
        grids[kind].seats = _seats_list(left, set(grids[kind].printed), "PARTIDO", f"page {number}")
    contests = {k: _contest(k, k, g) for k, g in grids.items()}
    contests |= _read_districts(path, *m.districts)
    for ck, grid in _blocks_2011(path, *m.blocks).items():
        contests[ck] = _contest(ck, ck.partition(":")[0], grid)
    return contests


def _towns(table: Table, department_name: str) -> list[str]:
    """The municipality codes of a table's rows, read from the names wrapped above and below the numbers.

    The names appear in the department's order, so each row takes the first municipality whose name the words in
    turn spell; the table's total row is last.
    """
    tokens = [fold(w.text) for w in table.left]
    if "TOTAL" in tokens:
        tokens = tokens[: len(tokens) - 1 - tokens[::-1].index("TOTAL")]
    expected = municipalities()[department(department_name) or ""]
    aliases: dict[str, list[str]] = defaultdict(list)
    for printed, official in ALIASES.items():
        aliases[official].append(printed)
    total = len([r for r in table.rows if not _is_total(r.name)])
    found: list[str] = []
    at = 0
    used: set[int] = set()
    while len(found) < total:
        for k in range(len(expected)):
            town, code = expected[k]
            if k in used:
                continue
            forms = [town, *aliases.get(town, [])]
            hit = next((f for f in forms if tokens[at : at + len(f.split())] == f.split()), None)
            if hit is None:
                close = get_close_matches(" ".join(tokens[at : at + len(town.split())]), forms, n=1, cutoff=0.85)
                hit = close[0] if close else None
            if hit is not None:
                found.append(code)
                at += len(hit.split())
                used.add(k)
                break
        else:
            raise ValueError(f"{table.title}: no municipality at {' '.join(tokens[at : at + 4])!r} after {found[-1:]}")
    if at != len(tokens):
        raise ValueError(f"{table.title}: names left over: {' '.join(tokens[at:])!r}")
    return found
