"""Positional readers for the tables printed in the Atlas Electoral de Bolivia (text layer of the PDF volumes).

The tables leave a cell empty where a list did not run, so values are assigned to columns by the right edge of each
number, taken from the rows of the table that have every column filled.
"""

import functools
import itertools
import re
import statistics
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from ea import raw

NUMBER = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+|\d+)$")
TOKEN = re.compile(r"\S+")
SEGMENT = re.compile(r"\S+(?: \S+)*")


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", plain.lower())


@functools.cache
def pages(source: str, url: str) -> list[list[str]]:
    path = raw.cached(source, url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return [page.replace("ﬁ", "fi").replace("ﬂ", "fl").split("\n") for page in text.split("\f")]


@dataclass
class Row:
    label: str
    values: list[tuple[int, int, int]]  # (start, right edge, value) of each number, in reading order
    start: int = 0
    prefix: list[str] = field(default_factory=list)
    suffix: list[str] = field(default_factory=list)


def split(line: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str]]]:
    """The text segments and the trailing numbers of one line, each with its start and end column."""
    tokens = [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(line)]
    tail = len(tokens)
    while tail > 0 and NUMBER.match(tokens[tail - 1][2]):
        tail -= 1
    numbers = tokens[tail:]
    head = line[: tokens[tail][0]] if tail < len(tokens) else line
    segments = [(m.start(), m.end(), m.group()) for m in SEGMENT.finditer(head)] if tail else []
    if tail == len(tokens):
        segments = [(m.start(), m.end(), m.group()) for m in SEGMENT.finditer(line)]
        numbers = []
    return segments, numbers


def count(text: str) -> int:
    return int(text.replace(".", ""))


def anchors(rows: Sequence[Sequence[tuple[int, int, int]]], width: int) -> list[float]:
    """Right edge of each column: from the rows that have every column filled, else from runs of aligned numbers."""
    full = [row for row in rows if len(row) == width]
    if full:
        return [statistics.median(row[i][1] for row in full) for i in range(width)]
    ends = sorted(end for row in rows for _, end, _ in row)
    groups: list[list[int]] = []
    for end in ends:
        if groups and end - groups[-1][-1] <= EDGE_GAP:
            groups[-1].append(end)
        else:
            groups.append([end])
    if len(groups) != width:
        raise ValueError(f"{len(groups)} number columns for {width} headings")
    return [statistics.mean(group) for group in groups]


EDGE_GAP = 3


def place(row: Sequence[tuple[int, int, int]], edges: Sequence[float], centered: bool = False) -> list[int | None]:
    """Put the numbers in increasing columns, choosing the columns whose edges (or, for centered headings, centers)
    lie closest to the numbers overall."""
    if len(row) > len(edges):
        raise ValueError(f"{len(row)} values for {len(edges)} columns: {row}")
    if len(row) == len(edges) and not centered:
        return [value for _, _, value in row]
    at = [(start + end) / 2 if centered else end for start, end, _ in row]
    inf = float("inf")
    best = [[inf] * (len(edges) + 1) for _ in range(len(row) + 1)]
    take = [[False] * (len(edges) + 1) for _ in range(len(row) + 1)]
    for j in range(len(edges) + 1):
        best[0][j] = 0.0
    for i in range(1, len(row) + 1):
        for j in range(i, len(edges) + 1):
            skip = best[i][j - 1]
            use = best[i - 1][j - 1] + abs(edges[j - 1] - at[i - 1])
            best[i][j] = min(skip, use)
            take[i][j] = use <= skip
    cells: list[int | None] = [None] * len(edges)
    i, j = len(row), len(edges)
    while i > 0:
        if take[i][j]:
            cells[j - 1] = row[i - 1][2]
            i -= 1
        j -= 1
    return cells


DEPARTMENTS = ("Chuquisaca", "La Paz", "Cochabamba", "Oruro", "Potosí", "Tarija", "Santa Cruz", "Beni", "Pando")
TITLE = re.compile(
    r"^\s*(Chuquisaca|La Paz|Cochabamba|Oruro|Potosí|Tarija|Santa Cruz|Beni|Pando)\. "
    r"(Resultados oficiales de votación por organización política y municipio|"
    r"Distribución de Concejalías por muni[cC]ipios?), (\d{4})\s*$",
    re.IGNORECASE,
)


@dataclass
class Muni:
    department: str
    name: str
    cells: dict[str, int | None]


def _rows(lines: list[str], department: str) -> list[Row]:
    """The data rows of a table body, with wrapped municipality names joined from the lines around the numbers."""
    items: list[tuple[str, int, list[tuple[int, int, str]], list[tuple[int, int, str]]]] = []
    for line in lines:
        segments, numbers = split(line)
        if "ATLAS ELECTORAL" in line or not line.strip():
            items.append(("blank", 0, [], []))
            continue
        if not numbers and not segments:
            continue
        if not numbers:
            items.append(("text", 0, segments, []))
            continue
        if len(numbers) == 1 and not segments:
            continue
        items.append(("row", 0, segments, numbers))
    rows: list[Row] = []
    pending: list[str] = []
    starts = Counter(segments[-1][0] for kind, _, segments, _ in items if kind == "row" and segments)
    both = Counter(segments[-1][0] for kind, _, segments, _ in items if kind == "row" and len(segments) >= 2)
    zone = (both or starts).most_common(1)[0][0] - 2 if starts else 0
    previous: Row | None = None
    for kind, _, segments, numbers in items:
        names = [text for start, _, text in segments if start >= zone]
        if kind == "row" and fold(" ".join(text for _, _, text in segments)) == fold(department):
            names = [department]
        if kind == "blank":
            continue
        if kind == "text":
            if names:
                pending.append(" ".join(names))
            continue
        label = " ".join(names)
        row = Row(label, [(start, end, count(text)) for start, end, text in numbers])
        if (
            pending
            and previous is not None
            and "(" in previous.label
            and previous.label.count("(") > previous.label.count(")")
            and ")" in pending[0]
        ):
            previous.suffix.append(pending[0])
            pending = pending[1:]
        if pending:
            lone = previous is not None and not previous.label
            if lone and not label and len(pending) >= 2:
                previous.suffix.append(pending[0])
                pending = pending[1:]
            elif lone and label:
                previous.suffix += pending
                pending = []
            elif lone and len(pending) == 1:
                raise ValueError(f"cannot tell where {pending} belongs")
            row.prefix = pending
            pending = []
        rows.append(row)
        previous = row
    if pending and rows and not rows[-1].label:
        rows[-1].suffix += pending
    for row in rows:
        row.label = " ".join(" ".join([*row.prefix, row.label, *row.suffix]).split())
    return rows


def _found(
    source: str, url: str, year: int, kind: str
) -> dict[str, list[tuple[list[tuple[int, int, str]], list[str]]]]:
    """The heading line and the body lines of every page of each department's table."""
    found: dict[str, list[tuple[list[tuple[int, int, str]], list[str]]]] = {}
    for page in pages(source, url):
        for number, line in enumerate(page):
            m = TITLE.match(line)
            if m is None or int(m.group(3)) != year or m.group(2).startswith("Resultados") != (kind == "votos"):
                continue
            header_at = next(
                (i for i in range(number + 1, min(number + 14, len(page))) if "Municipio" in page[i]), None
            )
            if header_at is None:
                raise ValueError(f"{m.group(1)} {year} {kind}: no header after the title")
            end = len(page)
            for i in range(header_at + 1, len(page)):
                if (
                    TITLE.match(page[i])
                    or re.match(r"^\s*(Ciudad|Primeras|Elecciones|Bolivia\.|Nota:)", page[i])
                    or re.search(r"\S, (?:19|20)\d\d\s*$", page[i])
                ):
                    end = i
                    break
            cells = heading(page[header_at])
            first = header_at + 1
            above = [(a, b, t) for a, b, t in heading(page[header_at - 1]) if a > cells[1][0] and not NUMBER.match(t)]
            if header_at - 1 > number and above:
                cells = _joined(cells, above, heading(page[header_at + 1]))
                first += 1
            found.setdefault(m.group(1), []).append((cells, page[first:end]))
    return found


def _joined(
    cells: list[tuple[int, int, str]], above: list[tuple[int, int, str]], below: list[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    """A heading whose name runs over three lines, such as FRENTE / AMPLIO, as one column."""
    out = list(cells)
    for start, end, text in above:
        words = [text, *(t for a, b, t in below if abs((a + b) - (start + end)) <= 12)]
        out.append((start, end, " ".join(words)))
    return sorted(out, key=lambda cell: cell[0] + cell[1])


Table = dict[str, list[tuple[str, dict[str, int | None]]]]


def vote_tables(source: str, url: str, year: int) -> tuple[Table, dict[str, list[str]]]:
    """Each department's municipalities with the votes printed for them, by printed column, and the printed columns.

    The last row of a department is its printed total, named for the department; it is empty when the total row has
    a column the heading does not name.
    """
    out: Table = {}
    headings: dict[str, list[str]] = {}
    for department, parts in _found(source, url, year, "votos").items():
        columns = [text for _, _, text in parts[0][0]][2:]
        reference: list[float] | None = None
        for head, body in parts:
            try:
                reference = anchors([row.values for row in _rows(body, department)], len(columns))
                break
            except ValueError:
                continue
        table: list[tuple[str, dict[str, int | None]]] = []
        for head, body in parts:
            if [text for _, _, text in head][2:] != columns:
                raise ValueError(f"{department} {year}: the heading changes between pages")
            rows = _rows(body, department)
            edges = shifted(rows, len(columns), reference)
            for row in rows:
                if len(row.values) > len(columns):
                    table.append((row.label, {}))  # a printed total with a column the heading does not name
                else:
                    table.append((row.label, dict(zip(columns, place(row.values, edges), strict=True))))
        out[department] = table
        headings[department] = columns
    return out, headings


def divisors(method: str, seat: int) -> int:
    return seat + 1 if method == "dhondt" else 2 * seat + 1


def allocate(votes: dict[str, int], seats: int, method: str) -> dict[str, int]:
    """Highest-averages allocation, ties going to the larger vote."""
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        winner = max(votes, key=lambda party: (votes[party] / divisors(method, won[party]), votes[party]))
        won[winner] += 1
    return {party: n for party, n in won.items() if n}


def seat_tables(
    source: str, url: str, year: int, votes: Table, voted: dict[str, list[str]], renames: dict[str, str]
) -> Table:
    """Each department's seats by municipality and printed party; a column of seats is matched to its heading by the
    votes of the municipalities it appears in, and the last column is the council's size."""
    out: Table = {}
    for department, parts in _found(source, url, year, "concejalias").items():
        headings = [renames.get(text, text) for _, _, text in parts[0][0]][2:]
        names = {fold(label): {fold(k): v for k, v in cells.items()} for label, cells in votes[department]}
        table: list[tuple[str, dict[str, int | None]]] = []
        for number, (_, body) in enumerate(parts):
            everything = _rows(body, department)
            closing = number == len(parts) - 1 and bool(everything) and fold(everything[-1].label) == fold(department)
            rows = everything[:-1] if closing else everything
            edges = clusters(rows, None, department, year)
            mapping = seat_columns(rows, edges, headings, names, department, year)
            for row in rows:
                cells = place(row.values, edges, True)
                table.append((row.label, {mapping[i]: value for i, value in enumerate(cells) if value is not None}))
            if closing:
                table.append((everything[-1].label, {}))
        out[department] = table
    del voted
    return out


def shifted(rows: Sequence[Row], width: int, reference: list[float] | None) -> list[float]:
    """The column edges of one page. A page that lacks a column in its numbers borrows the spacing of a page that has
    them all: the columns it does show are fitted to the borrowed ones, the four count columns on the right always
    being among them, and the edges of the missing columns follow from the fit."""
    try:
        return anchors([row.values for row in rows], width)
    except ValueError:
        if reference is None:
            raise
    ends = sorted(end for row in rows for _, end, _ in row.values)
    groups: list[list[int]] = []
    for end in ends:
        if groups and end - groups[-1][-1] <= EDGE_GAP:
            groups[-1].append(end)
        else:
            groups.append([end])
    seen = [statistics.mean(group) for group in groups]
    fixed = min(len(seen), COUNT_COLUMNS)
    best: tuple[float, list[float]] | None = None
    free = width - fixed
    for chosen in itertools.combinations(range(free), len(seen) - fixed):
        columns = [*chosen, *range(free, width)]
        for scale in (0.8 + 0.01 * step for step in range(41)):
            at = [seen[-1] + scale * (reference[c] - reference[-1]) for c in columns]
            error = sum((a - b) ** 2 for a, b in zip(at, seen, strict=True))
            if best is None or error < best[0]:
                best = (error, [seen[-1] + scale * (r - reference[-1]) for r in reference])
    if best is None:
        raise ValueError("no way to fit the columns")
    return best[1]


COUNT_COLUMNS = 4


def clusters(rows: Sequence[Row], width: int | None, department: str, year: int) -> list[float]:
    """Centers of the number columns of a page, found as runs of numbers whose positions lie close together."""
    centers = sorted((start + end) / 2 for row in rows for start, end, _ in row.values)
    groups: list[list[float]] = []
    for center in centers:
        if groups and center - groups[-1][-1] <= CLUSTER_GAP:
            groups[-1].append(center)
        else:
            groups.append([center])
    if width is not None and len(groups) != width:
        raise ValueError(f"{department} {year}: {len(groups)} number columns for {width} headings")
    return [statistics.mean(group) for group in groups]


CLUSTER_GAP = 4.5


def heading(line: str) -> list[tuple[int, int, str]]:
    """The column headings of a table, with the two-word names joined."""
    cells = [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(line) if m.group() != "*"]
    joined: list[tuple[int, int, str]] = []
    for start, end, text in cells:
        text = "Válidos" if text == "Validos" else text
        if joined and (joined[-1][2], text) in TWO_WORDS:
            joined[-1] = (joined[-1][0], end, f"{joined[-1][2]} {text}")
        else:
            joined.append((start, end, text))
    return joined


TWO_WORDS = {("AS", "XXI"), ("Santa", "Cruz"), ("La", "Paz")}


def seat_columns(
    rows: Sequence[Row],
    edges: Sequence[float],
    headings: list[str],
    votes: dict[str, dict[str, int | None]],
    department: str,
    year: int,
) -> dict[int, str]:
    """Which printed party each column of numbers on one page of a seat table belongs to.

    The seat tables print a heading for every list that ran in the department, but only the columns that hold a seat
    on the page carry numbers. The columns map to headings in order, and a list can hold a seat only where it has
    votes, so the mappings that put no seat on a list without votes in its municipality are the candidates. Among
    them the one whose seats match a highest-averages count of the municipality's votes for the most councils is read.
    """
    width = len(edges)
    parties = headings[:-1]
    cells = [(fold(row.label), place(row.values, edges, True)) for row in rows]
    candidates: list[tuple[int, tuple[int, ...]]] = []
    for chosen in itertools.combinations(range(len(parties)), width - 1):
        misses = 0
        for label, values in cells:
            cast = votes.get(label)
            if cast is None:
                continue
            misses += sum(1 for i, v in enumerate(values[:-1]) if v and not (cast.get(fold(parties[chosen[i]])) or 0))
        candidates.append((misses, chosen))
    least = min(misses for misses, _ in candidates)
    near = [chosen for misses, chosen in candidates if misses == least]
    if len(near) > 1:
        scored: list[tuple[int, tuple[int, ...]]] = []
        for chosen in near:
            fits = 0
            for label, values in cells:
                cast = votes.get(label)
                if cast is None:
                    continue
                printed = {parties[chosen[i]]: v for i, v in enumerate(values[:-1]) if v}
                size = values[-1] or 0
                ran = {p: count for p in parties if (count := cast.get(fold(p)))}
                fits += any(allocate(ran, size, m) == printed for m in ("dhondt", "sainte"))
            scored.append((-fits, chosen))
        scored.sort()
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            raise ValueError(f"{department} {year}: {len(near)} seat column mappings fit")
        near = [scored[0][1]]
    return {**{i: parties[c] for i, c in enumerate(near[0])}, width - 1: headings[-1]}


DEPARTMENT_COLUMNS = (*DEPARTMENTS, "Bolivia")
CAPITALS = (
    "Sucre",
    "La Paz",
    "Cochabamba",
    "Oruro",
    "Potosí",
    "Tarija",
    "Santa Cruz de la Sierra",
    "Trinidad",
    "Cobija",
)
CAPITALS_WITH_EL_ALTO = (*CAPITALS[:2], "El Alto", *CAPITALS[2:])
ACCOUNTS = ("Válidos", "Blancos", "Nulos", "Emitidos", "Inscritos")
GRIDS = {
    # year, subject, the title text, the columns
    "dept": r"Resultados (?:oficiales (?:de votación )?por organización política(?: y departamento)?"
    r"|departamentales)|Resultados oficiales de votación por$",
    "capitals": r"Resultados oficiales de votación por organización política$|Resultados oficiales de votación$"
    r"|Resultados en las capitales|Resultados oficiales de votación por organización política y capitales",
    "seats": r"Asignación de Concejalías",
}


def grid(source: str, url: str, year: int, subject: str, columns: Sequence[str]) -> dict[str, dict[str, int | None]]:
    """A printed table of lists down the side and places across the top: {list: {place: value}}.

    The tables in the early volumes leave a cell empty where a list did not run, so the numbers are placed by the
    columns every filled row shares.
    """
    lines = [line for page in pages(source, url) for line in page]
    wanted = re.compile(rf"^Bolivia[.:] Elecciones [Mm]unicipales, {year}\. (?:{GRIDS[subject]})")
    at = None
    for i, line in enumerate(lines[:-1]):
        if wanted.match(line + " " + lines[i + 1].strip()) or wanted.match(line):
            second = lines[i + 1].strip()
            if subject == "dept" and "capitales" in (line + second):
                continue
            if subject == "capitals" and "departamento" in second and "capitales" not in (line + second):
                continue
            if subject == "capitals" and "capitales" not in (line + second) and "capitales" not in line:
                continue
            if subject == "seats" or subject == "dept" and "departament" in line + second or subject == "capitals":
                at = i
                break
    if at is None:
        raise LookupError(f"{year} {subject} table not found")
    rows: list[tuple[str, list[tuple[int, int, int]]]] = []
    stop = "Total" if subject == "seats" else "Emitidos"
    for line in lines[at + 1 : at + 80]:
        if "ATLAS" in line or "CUADROS" in line:
            continue
        segments, numbers = split(line)
        if not numbers or not segments:
            continue
        label = segments[0][2] if len(segments) == 1 else " ".join(text for _, _, text in segments)
        if label.startswith(("Organización", "Política")):
            continue
        rows.append((label, [(start, end, count(text)) for start, end, text in numbers]))
        if label == stop and subject != "dept" or label == "Inscritos":
            break
        if label == "Emitidos" and subject == "dept":
            nxt = next((x for x in lines[lines.index(line) + 1 : lines.index(line) + 4] if "Inscritos" in x), None)
            if nxt is None:
                break
    edges = anchors([values for _, values in rows], len(columns))
    return {label: dict(zip(columns, place(values, edges), strict=True)) for label, values in rows}
