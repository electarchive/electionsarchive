"""Guatemala's general elections from the Tribunal Supremo Electoral's election reports (memorias electorales).

The Tribunal's per-table files reach back only to 2023 (`gt.tse`). For earlier elections, the finest unit it still
publishes is the municipality, in the tables of each election's memoria. The memorias are typeset PDFs with a text
layer, so each number is read with its word box from `pdftotext -bbox` and stored with its page and box.
"""

import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import get_close_matches
from functools import cache
from html import unescape
from pathlib import Path
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
def words(path: Path, first: int, last: int) -> dict[int, list[Word]]:
    """Every word on pages `first` to `last` of a PDF, by page, with its box in points from the page's top left."""
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
    for number, found_words in pages.items():
        long = [w for w in found_words if len(w.text) >= 3]
        if long and sum(w.y1 - w.y0 > w.x1 - w.x0 for w in long) > len(long) / 2:
            # Set sideways, reading upward: the printed line runs up the page and lines follow left to right.
            height = heights[number]
            pages[number] = [Word(w.page, height - w.y1, w.x0, height - w.y0, w.x1, w.text, w.box) for w in found_words]
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


def _segment(page: list[Word], title: str, section: str) -> tuple[list[Segment], str, str]:
    """Split a page into table segments: (title, header words, lines, section at start)."""
    found: list[Segment] = []
    current: Segment | None = None
    pending: list[list[Word]] = []
    rows = lines(page)
    used: set[int] = set()
    for index, row in enumerate(rows):
        if index in used:
            continue
        heads = [i for i, w in enumerate(row) if w.text == "Resultados"]
        if heads:
            run = [row[heads[0]]]
            for w in row[heads[0] + 1 :]:
                if w.x0 - run[-1].x1 > 15:
                    break
                run.append(w)
            title = text(run)
            current = None
            pending = []
            row = [w for w in row if w not in run]
            if sum(is_number(w.text) for w in row) < 2:
                continue
        if _is_header(row):
            band = [
                w
                for other in rows
                for w in other
                if abs(other[0].yc - row[0].yc) <= 9 and not is_number(w.text) and w is not row[0]
            ]
            for j, other in enumerate(rows):
                if j != index and abs(other[0].yc - row[0].yc) <= 9 and not any(is_number(w.text) for w in other):
                    used.add(j)
            current = (title, [row[0], *band], [], section)
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


def tables(path: Path, first: int, last: int) -> list[Table]:
    """The tables on pages `first` to `last`, a table running on across pages while its title and columns hold."""
    found: list[Table] = []
    title, section = "", ""
    pages = words(path, first, last)
    for number in range(first, last + 1):
        segments: list[Segment] = []
        for strip in _strips(pages[number]):
            found_segments, title, section = _segment(strip, title, section)
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
                if slots[nearest] is not None:
                    table.problems.append(f"page {w.page}: two numbers in column {nearest} of row {name!r}")
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
    """"tables" prints each contest by municipality; "cards" prints one contest per page, by its own area."""


MEMORIA_LIST = (
    Memoria(2015, "2015_Memoria_Elecciones", date(2015, 9, 6), date(2015, 10, 25), (248, 326), (328, 456), 31),
    Memoria(2019, "2019_Memoria_Elecciones", date(2019, 6, 16), date(2019, 8, 11), (375, 804), (0, 0), 32, "cards"),
)
NATION = "GT"
"""The unit code of a contest the memoria prints only as a national total."""
BY_YEAR = {m.year: m for m in MEMORIA_LIST}


def url(m: Memoria) -> str:
    return f"{SITE}/images/memoriaselec/me/{m.file}.pdf"


def fetch() -> None:
    for m in MEMORIA_LIST:
        raw.fetch(SOURCE, url(m), suffix=".pdf")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


TYPOS = {"CORVERGENCIA": "CONVERGENCIA", "FUEZA": "FUERZA", "UNGMAIZ": "URNGMAIZ", "WIMAQ": "WINAQ"}
"""Misspelled acronyms in the memorias' tables, read as the organization they name."""
BLOCS = {"URNGWINAQ"}


def label(text: str) -> str:
    """An organization's label as one key: the memorias print one acronym with and without hyphens and spaces."""
    found = re.sub(r"[^A-Z0-9]", "", fold(text))
    return TYPOS.get(found, found)


ACCOUNTS = {label(name): measure for name, measure in ACCOUNTING.items()}


def number(w: Word) -> int:
    return int(w.text.replace(",", ""))


@dataclass
class Contest:
    """One contest as the memoria prints it: its columns, its rows by municipality, and its printed totals."""

    key: str
    kind: str
    columns: list[str]
    rows: dict[str, list[Word]] = field(default_factory=dict)
    declared: dict[str, int] = field(default_factory=dict)
    candidates: dict[str, str] = field(default_factory=dict)
    departments: dict[int, list[Word]] = field(default_factory=dict)
    """The printed department totals of a national contest."""
    zero: list[str] = field(default_factory=list)
    """Municipalities the memoria prints with every cell zero, as for an annulled election."""


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
    contest.rows[code] = values


@cache
def read(year: int) -> tuple[dict[str, Contest], list[Block]]:
    """Every contest of a year's memoria, and its mayoral blocks."""
    m = BY_YEAR[year]
    path = raw.cached(SOURCE, url(m)).path
    if m.layout == "cards":
        return _read_cards(path, m), []
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
                    contest.departments[department_code(row.name)] = row.values
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
        diff = {c: (sums[c], contest.declared[c]) for c in contest.columns if sums[c] != contest.declared[c]}
        if diff:
            report[ck] = diff
    return report


def compare_departments(year: int) -> dict[str, dict[int, dict[str, tuple[int, int]]]]:
    """Each national contest's municipal sums by department against the department totals the memoria prints."""
    contests, _ = read(year)
    report: dict[str, dict[int, dict[str, tuple[int, int]]]] = {}
    for ck, contest in contests.items():
        for dep, values in contest.departments.items():
            rows = [row for code, row in contest.rows.items() if (int(code[:2]) or 1) == dep]
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
        if sum(v for i, v in enumerate(values) if i != cast) != values[cast]:
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
    return {
        label(c)
        for contest in contests.values()
        if contest.kind not in ("municipal", "rerun")
        for c in contest.columns
        if label(c) not in ACCOUNTS
    }


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
    artifact = raw.cached(SOURCE, url(m))
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
    artifact_id = p.artifact(artifact, source)
    contests, _ = read(year)
    pages = {w.page for contest in contests.values() for row in contest.rows.values() for w in row}
    for page in sorted(pages):
        width, height = SIZES[(artifact.path, page)]
        p.add(
            "source_page",
            id=key("page", year, page),
            artifact_id=artifact_id,
            page_number=page,
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
    "ballots, blank votes as blank ballots, and the votes cast (emitidos) as ballots cast. A municipality the memoria "
    "prints with every cell zero, where the election was annulled, is left out."
)
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
    p.uses(raw.cached(SOURCE, url(m)))
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
        note = SOURCE_NOTE
        if contest.zero:
            zeros.extend(contest.zero)
            note += f" Left out as printed with every cell zero: {', '.join(sorted(contest.zero))}."
        if family == "rerun":
            note += (
                " The Tribunal annulled this municipality's elections of 6 September and held them again on 25 "
                "October; the memoria prints the mayoral results of the repeated election."
            )
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
        for town, row in contest.rows.items():
            unit_id = unit(town)
            for index, (column, w) in enumerate(zip(contest.columns, row, strict=True)):
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
