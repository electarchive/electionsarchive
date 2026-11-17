"""Panama's Tribunal Electoral: general election results by circuit, district, and corregimiento."""

import functools
import json
import re
import subprocess
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import fastexcel
import xlrd

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "pa.te"
SITE = "https://www.tribunal-electoral.gob.pa"
BROWSER = "browser://20261116-222948-d9df9f/"
INBOX = DATA / "inbox" / "pa.te"

# Measures
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6


DOWNLOAD = SITE + "/wp-content/plugins/download-attachments/includes/download.php?id="


def url(name: str) -> str:
    """The URL a file was cached under: a full URL, a site download id written `dl:<id>`, or a name saved in the
    browser."""
    if name.startswith("https://"):
        return name
    return DOWNLOAD + name[3:] if name.startswith("dl:") else BROWSER + name


def fetch() -> None:
    """Load the files that were saved from the Tribunal's site (data/inbox/pa.te/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    for key, entry in index.items():
        content = (INBOX / entry["file"]).read_bytes()
        raw.store(
            SOURCE,
            entry.get("source", key),
            content,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def norm(text: str) -> str:
    """Fold a place or party label to a key: no accents, case, footnote marks, or punctuation."""
    text = re.sub(r"\s+\d/\s*$", "", str(text).strip())
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\(\s*cab[^)]*\)", "(cab)", text)
    text = re.sub(r"[^a-z0-9()./ ]+", " ", text)
    return " ".join(text.split())


# Grids -----------------------------------------------------------------------------------------------------------


def grids(name: str) -> dict[str, list[list[object]]]:
    """Every sheet of a workbook as rows of cells, empty cells as None."""
    path = raw.cached(SOURCE, url(name)).path
    data = path.read_bytes()
    out: dict[str, list[list[object]]] = {}
    if data[:2] == b"PK":
        book = fastexcel.read_excel(data)
        for sheet in book.sheet_names:
            frame = book.load_sheet(sheet, header_row=None).to_polars()
            out[sheet] = [list(row) for row in frame.iter_rows()]
    else:
        sheets = xlrd.open_workbook(file_contents=data).sheets()
        for sheet in sheets:
            out[sheet.name] = [
                [cell if cell != "" else None for cell in sheet.row_values(i)] for i in range(sheet.nrows)
            ]
    return out


def number(cell: object) -> int | None:
    """An integer count from a cell, or None for blank and 'N/P' (not presented)."""
    if cell is None:
        return None
    if isinstance(cell, bool):
        return None
    if isinstance(cell, int | float):
        return round(cell) if float(cell).is_integer() else None
    text = str(cell).strip().replace(",", "")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.0+", text):
        return int(float(text))
    return None


def text(cell: object) -> str:
    return " ".join(str(cell).split()) if cell is not None else ""


@dataclass
class Row:
    label: str
    indent: int
    cells: list[object]
    reg: int | None = None
    cast: int | None = None
    valid: int | None = None
    blank: int | None = None
    null: int | None = None
    votes: dict[str, int] = field(default_factory=dict)


@dataclass
class Layout:
    parties: dict[int, str]
    reg: int | None
    cast: int | None
    valid: int
    blank: int | None
    null: int | None


def _party_label(header: list[list[object]], col: int, first: int) -> str:
    label = ""
    for row in header[first : first + 3]:
        part = text(row[col]) if col < len(row) else ""
        if part:
            label = label[:-1] + part if label.endswith("-") else f"{label} {part}".strip()
    return label


def layout(grid: list[list[object]], first_row: int = 0) -> tuple[Layout, int]:
    """Find the columns of a party-by-unit table from its header and check them against the Total row."""
    total = next(
        i
        for i, row in enumerate(grid)
        if i >= first_row
        and row
        and re.match(r"total\b", text(row[0]).lower())
        and sum(number(c) is not None for c in row[1:]) >= 3
    )
    header = grid[:total]
    width = max(len(row) for row in grid)
    cells = grid[total] + [None] * (width - len(grid[total]))
    start = None
    for i, row in enumerate(header):
        for j, cell in enumerate(row):
            if text(cell).upper() == "PRD":
                start = (i, j)
                break
        if start:
            break
    if start is None:
        raise LookupError("no PRD column")
    r0, j0 = start

    def cols(word: str) -> list[int]:
        return [
            j
            for j in range(width)
            if any(j < len(row) and word in text(row[j]).upper() for row in header[max(0, r0 - 4) :])
        ]

    blank_cols = [j for j in cols("BLANCO") if j > j0]
    null_cols = [j for j in cols("NULOS") if j > j0]
    blank = min(blank_cols) if blank_cols else None
    null = min(c for c in null_cols if blank is None or c > blank) if null_cols else None
    end = blank if blank is not None else null
    if end is None:
        raise LookupError("no blank or null column")
    parties = {}
    for j in range(j0, end):
        label = _party_label(header, j, r0)
        if label and "(%)" not in label:
            parties[j] = label
    values = [number(c) for c in cells]
    summed = sum(values[j] or 0 for j in parties)
    candidates = [j for j in range(1, j0) if values[j] == summed]
    if not candidates:
        raise ValueError(f"valid votes {summed:,} not found in the Total row {values[:j0]}")
    valid = max(candidates)
    extra = (values[blank] or 0 if blank is not None else 0) + (values[null] or 0 if null is not None else 0)
    cast_candidates = [j for j in range(1, valid) if values[j] == summed + extra]
    cast = max(cast_candidates) if cast_candidates else None
    reg = next((j for j in cols("PADR") if j < valid), None)
    return Layout(parties, reg, cast, valid, blank, null), total


def party_rows(grid: list[list[object]]) -> tuple[Layout, list[Row]]:
    lay, total = layout(grid)
    rows: list[Row] = []
    for raw_row in grid[total:]:
        if not raw_row or raw_row[0] is None or not isinstance(raw_row[0], str) or not text(raw_row[0]):
            continue
        cells = raw_row + [None] * (lay.valid + 1)
        valid = number(cells[lay.valid])
        if valid is None:
            continue
        label = raw_row[0]
        row = Row(
            text(label),
            len(label) - len(label.lstrip()),
            cells,
            reg=number(cells[lay.reg]) if lay.reg is not None else None,
            cast=number(cells[lay.cast]) if lay.cast is not None else None,
            valid=valid,
            blank=number(cells[lay.blank]) if lay.blank is not None else None,
            null=number(cells[lay.null]) if lay.null is not None else None,
        )
        for j, party in lay.parties.items():
            value = number(cells[j]) if j < len(cells) else None
            if value is not None:
                row.votes[party] = value
        rows.append(row)
    return lay, rows


# PDF tables ------------------------------------------------------------------------------------------------------


@functools.cache
def pdf_lines(name: str) -> list[str]:
    path = raw.cached(SOURCE, url(name)).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace").splitlines()


def pdf_grid(name: str, start: str | None = None, stop: str | None = None) -> list[list[object]]:
    """A PDF table as grid rows: the label, then every other token of the line, in the order they print."""
    lines = pdf_lines(name)
    first = next((i for i, line in enumerate(lines) if start and start in line), 0) if start else 0
    grid: list[list[object]] = []
    for line in lines[first:]:
        if stop and stop in line and grid:
            break
        parts = re.split(r"\s{2,}", line.strip())
        indent = len(line) - len(line.lstrip())
        if len(parts) < 2 or not line.strip():
            continue
        label = parts[0].strip()
        tokens = " ".join(parts[1:]).split()
        if len(tokens) < 4 or not any(number(t) is not None for t in tokens):
            continue
        grid.append([" " * indent + label, *tokens])
    return grid


def pdf_party_rows(grid: list[list[object]], labels: list[str], reg: int | None = None) -> list[Row]:
    """Rows of a PDF table of parties. The columns follow from the Total row: the party votes must add up to the valid
    votes, and the valid, blank, and null votes to the votes cast."""
    total = next(i for i, row in enumerate(grid) if text(row[0]).upper().startswith("TOTAL"))
    cells = [number(c) for c in grid[total][1:]]
    np = len(labels)
    for jv in range(1, 9):
        valid = cells[jv]
        if valid is None:
            continue
        for gap in (2, 1):
            end = jv + gap + np
            if end > len(cells) or sum(c or 0 for c in cells[jv + gap : end]) != valid:
                continue
            for blank, null in ((None, 0), (0, 2), (0, 1)):
                nulls = cells[end + null] if end + null < len(cells) else None
                blanks = 0 if blank is None else cells[end + blank] if end + blank < len(cells) else None
                if nulls is not None and blanks is not None and cells[jv - 1] == valid + blanks + nulls:
                    return rows_from(
                        grid, total, labels, jv, gap, end + blank if blank is not None else None, end + null, reg
                    )
    raise ValueError(f"no column layout adds up in the Total row {cells}")


def rows_from(
    grid: list[list[object]],
    total: int,
    labels: list[str],
    jv: int,
    gap: int,
    blank: int | None,
    null: int,
    reg: int | None,
) -> list[Row]:
    rows: list[Row] = []
    for raw_row in grid[total:]:
        label = raw_row[0]
        cells = list(raw_row[1:]) + [None] * (null + 2)
        valid = number(cells[jv])
        if valid is None:
            continue
        row = Row(
            text(label), len(str(label)) - len(str(label).lstrip()), cells, valid=valid, cast=number(cells[jv - 1])
        )
        row.reg = number(cells[reg]) if reg is not None else None
        row.blank = number(cells[blank]) if blank is not None else None
        row.null = number(cells[null])
        for j, party in enumerate(labels):
            value = number(cells[jv + gap + j])
            if value is not None:
                row.votes[party] = value
        rows.append(row)
    return rows


# Hierarchy -------------------------------------------------------------------------------------------------------

TOP = {
    "bocas del toro", "cocle", "colon", "chiriqui", "darien", "herrera", "los santos", "panama", "panama oeste",
    "veraguas", "kuna yala", "guna yala", "ngabe bugle", "ngobe bugle", "ngabe buble", "embera", "embera wounaan",
    "naso tjer di",
}  # fmt: skip


def is_top(row: Row) -> bool:
    key = norm(row.label)
    return row.indent == 0 and (key in TOP or key.startswith("comarca"))


def is_top_pdf(row: Row) -> bool:
    """Provinces and comarcas print in capitals in the PDF tables, whose indentation varies."""
    return row.label.isupper() and not norm(row.label).startswith("circuito")


@dataclass
class Unit:
    scheme: str  # circuito, distrito, corregimiento, exterior, adelantado
    path: tuple[str, ...]  # province, then circuit or district, then corregimiento
    row: Row


def circuits(rows: list[Row]) -> list[Unit]:
    """Units of a table by electoral circuit, with provinces, comarcas, and the votes abroad between them."""
    units: list[Unit] = []
    province = ""
    for i, row in enumerate(rows):
        key = norm(row.label)
        if key == "total":
            continue
        match = re.match(r"circuito\s+(\d+\.\d+)$", key)
        following = norm(rows[i + 1].label) if i + 1 < len(rows) else ""
        if not match and row.indent == 0 and key.startswith("comarca") and not following.startswith("circuito"):
            # A comarca with no electoral circuit of its own (2004): its row is the unit, as a district.
            units.append(Unit("distrito", (row.label, row.label), row))
            continue
        if match:
            units.append(Unit("circuito", (province, match[1]), row))
        elif "exterior" in key or key.startswith("panamenos residentes"):
            units.append(Unit("exterior", (row.label,), row))
        elif "adelantado" in key:
            units.append(Unit("adelantado", (row.label,), row))
        else:
            province = row.label
    return units


def districts(rows: list[Row], depth: int, issues: list[str], is_block: Callable[[Row], bool] = is_top) -> list[Unit]:
    """Units of a table by district (depth 2) or by corregimiento (depth 3), found by indentation and checked by sums."""
    blocks: list[tuple[Row, list[Row]]] = []
    for row in rows:
        if norm(row.label) == "total":
            continue
        if is_block(row) or not blocks:
            blocks.append((row, []))
        else:
            blocks[-1][1].append(row)
    units: list[Unit] = []
    for top, members in blocks:
        if not members:
            units.append(
                Unit(
                    "distrito" if depth == 2 else "corregimiento",
                    (top.label, top.label, top.label)[: depth + 1 if depth == 3 else 2],
                    top,
                )
            )
            continue
        if depth == 2:
            for row in members:
                units.append(Unit("distrito", (top.label, row.label), row))
            check(top, members, issues)
            continue
        units.extend(
            Unit("corregimiento", (top.label, d.label, leaf.label), leaf) for d, leaf in nest(top, members, issues)
        )
    return units


FIELDS = ("valid", "cast", "reg", "blank", "null")


def whole(parent: Row, children: list[Row]) -> bool:
    """Whether the children's figures add up to the parent's, in every field both print and in every party."""
    if not children or not parent.valid:
        return False
    for name in FIELDS:
        total = getattr(parent, name)
        if (
            total is not None
            and all(getattr(c, name) is not None for c in children)
            and total != sum(getattr(c, name) for c in children)
        ):
            return False
    return all(votes == sum(c.votes.get(party, 0) for c in children) for party, votes in parent.votes.items())


def nest(top: Row, members: list[Row], issues: list[str]) -> list[tuple[Row, Row]]:
    """The leaf rows under a province or comarca, each with its district, by finding the rows that add up to a parent."""
    nodes: dict[int, tuple[list[int], int]] = {}

    def build(i: int) -> tuple[list[int], int]:
        if i in nodes:
            return nodes[i]
        row, kids, j, total = members[i], [], i + 1, 0
        while j < len(members) and total < (row.valid or 0):
            _, after = build(j)
            kids.append(j)
            total += members[j].valid or 0
            j = after
        if not whole(row, [members[k] for k in kids]):
            kids, j = [], i + 1
        nodes[i] = (kids, j)
        return kids, j

    out: list[tuple[Row, Row]] = []

    def leaves(i: int) -> list[Row]:
        kids, _ = build(i)
        return [members[i]] if not kids else [leaf for k in kids for leaf in leaves(k)]

    index = 0
    found: list[Row] = []
    while index < len(members):
        _, after = build(index)
        found.append(members[index])
        out.extend((members[index], leaf) for leaf in leaves(index))
        index = after
    if not whole(top, found):
        issues.append(f"{top.label}: districts do not add up to the province")
    return out


def check(parent: Row, children: list[Row], issues: list[str]) -> None:
    for name in ("valid", "cast", "reg", "blank", "null"):
        total = getattr(parent, name)
        if total is None or any(getattr(c, name) is None for c in children):
            continue
        if total != sum(getattr(c, name) for c in children):
            issues.append(f"{parent.label}: {name} {total:,} vs {sum(getattr(c, name) for c in children):,}")
    for party, votes in parent.votes.items():
        if votes != sum(c.votes.get(party, 0) for c in children):
            issues.append(f"{parent.label}: {party} {votes:,} vs {sum(c.votes.get(party, 0) for c in children):,}")


# Presidential tables ---------------------------------------------------------------------------------------------


@dataclass
class Ticket:
    label: str  # the alliance, party, or "Libre postulación" line printed over the column
    name: str
    mates: tuple[str, ...]
    column: int
    parties: tuple[str, ...]


def sheet_rows(grid: list[list[object]], columns: dict[str, int], votes: dict[str, int] | None = None) -> list[Row]:
    """Rows of a table whose columns are known: each row with a label and a valid-votes figure."""
    rows: list[Row] = []
    for cells in grid:
        label = cells[0] if cells else None
        if not isinstance(label, str) or not text(label):
            continue
        cells = cells + [None] * 40
        valid = number(cells[columns["valid"]])
        if valid is None:
            continue
        row = Row(text(label), len(label) - len(label.lstrip()), cells)
        for name, column in columns.items():
            setattr(row, name, number(cells[column]))
        for ticket, column in (votes or {}).items():
            value = number(cells[column])
            if value is not None:
                row.votes[ticket] = value
        rows.append(row)
    return rows


# Elections -------------------------------------------------------------------------------------------------------


@dataclass
class Election:
    year: int
    day: date
    files: list[str]
    tickets: list[Ticket] = field(default_factory=list)
    president: list[Unit] = field(default_factory=list)
    deputy: list[Unit] = field(default_factory=list)
    mayor: list[Unit] = field(default_factory=list)
    rep: list[Unit] = field(default_factory=list)
    council: list[Unit] = field(default_factory=list)
    printed: dict[str, Row] = field(default_factory=dict)  # the national Total row each table prints
    issues: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)


C1 = {"reg": 5, "cast": 6, "valid": 11, "blank": 13, "null": 15}

TICKETS = {
    2024: [
        Ticket("Vamos con Todo Panamá", "José Gabriel Carrizo Jaén", ("Camilo Alleyne",), 2, ("PRD", "MOLIRENA")),
        Ticket("Alianza para Salvar Panamá", "José Raúl Mulino Quintero", (), 6, ("RM", "P. Alianza")),
        Ticket("Por un Panamá Mejor, lo bueno viene", "Rómulo Alberto Roux Moses", ("José Blandón",), 10, ("CD", "P. Panameñista")),
        Ticket("Movimiento Otro Camino", "Ricardo Lombana", ("Michael Chen",), 14, ("MOCA",)),
        Ticket("P. Popular", "Martín Torrijos", ("Rosario Turner",), 16, ("P. Popular",)),
        Ticket("PAIS - Libre Postulación (3)", "Melitón Alejandro Arrocha", ("Aida Michelle Ureña de Maduro",), 18, ("PAIS", "Libre Postulación (3)")),
        Ticket("Libre Postulación (1)", "Zulay Leyset Rodríguez Lu", ("Athenas Athanasiadis Rojas",), 22, ("Libre Postulación (1)",)),
        Ticket("Libre Postulación (2)", "Maribel Gordon Calderón", ("Richard Anthony Morales Villareal",), 24, ("Libre Postulación (2)",)),
    ],
    1994: [
        Ticket("Alianza Democrática", "Mireya Moscoso de Gruber", ("Arnulfo Escalona Ríos", "José Manuel Terán"), 2, ("ARN", "PLA", "LIB", "UDI")),
        Ticket("Cambio 94", "Rubén Carles", ("Guillermo E. Quijano", "Tomás Herrera"), 8, ("MOLIRENA", "REN CIV", "MORENA")),
        Ticket("Concertación Nacional", "Samuel L. Galindo", ("David Guerra", "Marisín V. de Arias"), 13, ("SOL", "MUN")),
        Ticket("Pueblo Unido", "Ernesto Pérez Balladares", ("Gabriel Altamirano Duque", "Felipe A. Virzi"), 17, ("PRD", "PALA", "LIBRE")),
        Ticket("PDC", "Eduardo Vallarino", ("Raúl J. Ossa", "Julia Orillac"), 22, ("PDC",)),
        Ticket("PPD", "José Muñoz", ("Rodrigo Bernal", "Ricardo J. Suira"), 24, ("PPD",)),
        Ticket("MPE", "Rubén Blades", ("Fernando Manfredo", "Ricardo J. Bermúdez"), 26, ("MPE",)),
    ],
    1999: [
        Ticket("Nueva Nación", "Martín Torrijos", ("Raúl Arango", "Laurentino Cortizo"), 2, ("PRD", "MPE", "SOL", "LIB NAL")),
        Ticket("Acción Opositora", "Alberto Vallarino", ("Mayín Correa", "Joaquín Fernando Franco"), 8, ("PDC", "REN CIV", "PNP", "LIB")),
        Ticket("Unión por Panamá", "Mireya Moscoso", ("Arturo Vallarino", "Dominador Kayser Bazán"), 14, ("MOLIRENA", "ARN", "MORENA", "CAM DEM")),
    ],
    2014: [
        Ticket("Unidos por un Cambio", "José Domingo Arias", ("Marta Linares de Martinelli",), 2, ("CAM DEM", "MOLIRENA")),
        Ticket("El Pueblo Primero", "Juan Carlos Varela Rodríguez", ("Isabel De Saint Malo",), 6, ("PAN", "POPULAR")),
        Ticket("Partido Revolucionario Democrático", "Juan Carlos Navarro Quelquejeu", ("Gerardo Solís",), 10, ("PRD",)),
        Ticket("Frente Amplio por la Democracia", "Genaro López", ("Maribel Gordon",), 12, ("FAD",)),
        Ticket("Celeste", "Esteban Rodríguez", ("Sarai Blaisdell",), 14, ("CELESTE",)),
        Ticket("Verde", "Gerardo Barroso", ("José Luís Galloway",), 16, ("VERDE",)),
        Ticket("Chocolate", "Juan Jované", ("Alberto Reyes",), 18, ("CHOCOLATE",)),
    ],
    2004: [
        Ticket("Patria Nueva", "Martín Torrijos", ("Samuel Lewis Navarro", "Rubén Arosemena Valdés"), 2, ("PRD", "POPULAR")),
        Ticket("Visión de País", "José Miguel Alemán", ("Jesús Rosas", "Aníbal Galindo Navarro"), 6, ("ARN", "MOLIRENA", "LIB NAL")),
        Ticket("Solidaridad", "Guillermo Endara Galimany", ("Guillermo Ford", "Alejandro Posse"), 11, ("SOL",)),
        Ticket("Cambio Democrático", "Ricardo Martinelli", ("Roberto Henríquez", "Roxana Méndez"), 13, ("CAM DEM",)),
    ],
    2019: [
        Ticket("Uniendo Fuerzas", "Laurentino Cortizo Cohen", ("José Gabriel Carrizo Jaén",), 2, ("PRD", "MOLIRENA")),
        Ticket("Panamá Podemos", "José Isabel Blandón Figueroa", ("Nilda Iris Quijano Peña",), 6, ("PAN", "Popular")),
        Ticket("Un Cambio para Despertar", "Rómulo Alberto Roux Moses", ("Luis Gabriel Casis Aguilar",), 10, ("CD", "Alianza")),
        Ticket("Frente Amplio por la Democracia", "Saúl Méndez Rodríguez", ("Maribel Gordon Calderón",), 14, ("FAD",)),
        Ticket("Celeste", "Ana Matilde Gómez Ruiloba", ("Jorge Arango Arias",), 16, ("CELESTE",)),
        Ticket("Verde", "Marco Antonio Ameglio Samudio", ("Mario Antonio Boyd Galindo",), 18, ("VERDE",)),
        Ticket("Chocolate", "Ricardo Alberto Lombana González", ("Guillermo Márquez Amado",), 20, ("CHOCOLATE",)),
    ],
    2009: [
        Ticket("Un País para Todos", "Balbina del Carmen Herrera Araúz", ("Juan Carlos Navarro Quelquejeu",), 2, ("PRD", "POPULAR", "LIBERAL")),
        Ticket("Alianza por el Cambio", "Ricardo Alberto Martinelli Berrocal", ("Juan Carlos Varela Rodríguez",), 7, ("MOLIRENA", "PAN", "CD", "UNIÓN PATRIOTICA")),
        Ticket("Vanguardia Moral de la Patria", "Guillermo Endara Galimany", ("Manuel Cortizo Cohen",), 13, ("VMP",)),
    ],
}  # fmt: skip


def president_table(
    year: int, one: list[list[object]], columns: dict[str, int], two: list[list[object]], e: Election
) -> None:
    """The presidency: accounting by circuit from table 1 and the votes of each ticket by circuit from table 2."""
    rows = sheet_rows(one, columns)
    tickets = TICKETS[year]
    cells = sheet_rows(two, {"valid": 1}, {t.label: t.column for t in tickets})
    e.tickets = tickets
    by_circuit = {place_key(u.path[-1]): u for u in circuits(cells)}
    for unit in circuits(rows):
        votes = by_circuit[place_key(unit.path[-1])].row
        if votes.valid != unit.row.valid:
            e.issues.append(
                f"president {unit.path[-1]}: valid {unit.row.valid:,} in table 1, {votes.valid:,} in table 2"
            )
        unit.row.votes = votes.votes
        e.president.append(unit)
    e.printed["president"] = rows[0]
    e.printed["president_votes"] = cells[0]


def party_table(grid: list[list[object]], kind: str, e: Election, depth: int | str, office: str) -> None:
    _, rows = party_rows(grid)
    units = circuits(rows) if depth == "c" else districts(rows, int(depth), e.issues)
    getattr(e, office).extend(units)
    e.printed[office] = rows[0]
    labels = [row.label for row in rows if norm(row.label) == "total"]
    if len(labels) > 1:
        e.notes[office] = "the table prints a second total row"


def load_2024() -> Election:
    e = Election(2024, date(2024, 5, 5), [])
    base = "2024/Cuadro-"
    names = {
        "c1": "01-Presidente-3", "c2": "02-Presidente-3", "dep": "04-Diputado-PP", "mayor": "08-Alcalde-PP",
        "rep": "11-Representantes-PP", "council": "14-Concejal-PP",
    }  # fmt: skip
    e.files = [f"{base}{n}.xlsx" for n in names.values()]
    president_table(2024, next(iter(grids(e.files[0]).values())), C1, grids(e.files[1])["PRESID2"], e)
    party_table(next(iter(grids(e.files[2]).values())), "dep", e, "c", "deputy")
    party_table(next(iter(grids(e.files[3]).values())), "mayor", e, 2, "mayor")
    party_table(next(iter(grids(e.files[4]).values())), "rep", e, 3, "rep")
    party_table(next(iter(grids(e.files[5]).values())), "council", e, 2, "council")
    return e


def load_2019() -> Election:
    e = Election(2019, date(2019, 5, 5), [])
    base = SITE + "/wp-content/uploads/2019/06/Cuadro-"
    names = ["01-Presidente", "02-Presidente", "04-Diputado-PP", "08-Alcalde-PP", "11-Representantes-PP",
             "14-Concejal-PP"]  # fmt: skip
    e.files = [f"{base}{n}.xlsx" for n in names]
    president_table(2019, grids(e.files[0])["PRESID1"], C1, grids(e.files[1])["PRESID2"], e)
    party_table(next(iter(grids(e.files[2]).values())), "dep", e, "c", "deputy")
    party_table(next(iter(grids(e.files[3]).values())), "mayor", e, 2, "mayor")
    party_table(next(iter(grids(e.files[4]).values())), "rep", e, 3, "rep")
    party_table(next(iter(grids(e.files[5]).values())), "council", e, 2, "council")
    return e


def load_2009() -> Election:
    e = Election(2009, date(2009, 5, 3), [])
    names = ["cuadro01-presidente", "cuadro02-presidente", "cuadro04-diputadopp", "cuadro08-alcaldepp",
             "cuadro11-representantespp", "cuadro14-concejalpp"]  # fmt: skip
    e.files = [f"2009/{n}.xls" for n in names]
    president_table(2009, next(iter(grids(e.files[0]).values())), C1, grids(e.files[1])["PRESID2"], e)
    party_table(next(iter(grids(e.files[2]).values())), "dep", e, "c", "deputy")
    party_table(next(iter(grids(e.files[3]).values())), "mayor", e, 2, "mayor")
    party_table(next(iter(grids(e.files[4]).values())), "rep", e, 3, "rep")
    party_table(next(iter(grids(e.files[5]).values())), "council", e, 2, "council")
    return e


def load_2004() -> Election:
    e = Election(2004, date(2004, 5, 2), [])
    names = ["dl:6948", "dl:6949", "dl:6951", "dl:6955", "dl:6942", "dl:6945"]
    e.files = names
    president_table(2004, next(iter(grids(names[0]).values())), C1, next(iter(grids(names[1]).values())), e)
    party_table(next(iter(grids(names[2]).values())), "dep", e, "c", "deputy")
    party_table(next(iter(grids(names[3]).values())), "mayor", e, 2, "mayor")
    party_table(next(iter(grids(names[4]).values())), "rep", e, 3, "rep")
    party_table(next(iter(grids(names[5]).values())), "council", e, 2, "council")
    return e


# Packages --------------------------------------------------------------------------------------------------------

PANAMA = str(uuid5(package.NAMESPACE, "polity\x1fPA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPAN"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
BODY_ASSEMBLY = key("body", "assembly")
BODY_COUNCIL = key("body", "council")
BODY_JUNTA = key("body", "junta")
PRESIDENCY = key("office", "president")
MAYORALTY = key("office", "mayor")
REPRESENTATIVE = key("office", "representative")
SYSTEM_PLURALITY = key("electoral_system", "plurality")

PARTIES: dict[str, tuple[str, str]] = {
    "prd": ("PRD", "Partido Revolucionario Democrático"),
    "popular": ("PP", "Partido Popular"),
    "p. popular": ("PP", "Partido Popular"),
    "molirena": ("MOLIRENA", "Movimiento Liberal Republicano Nacionalista"),
    "pan": ("PAN", "Partido Panameñista"),
    "p. panamenista": ("PAN", "Partido Panameñista"),
    "panamenista": ("PAN", "Partido Panameñista"),
    "cam dem": ("CD", "Cambio Democrático"),
    "cd": ("CD", "Cambio Democrático"),
    "lib": ("LIB", "Partido Liberal"),
    "liberal": ("LIB", "Partido Liberal"),
    "lib nal": ("LIBNAL", "Partido Liberal Nacional"),
    "union patriotica": ("UP", "Unión Patriótica"),
    "vmp": ("VMP", "Vanguardia Moral de la Patria"),
    "p. alianza": ("ALIANZA", "Partido Alianza"),
    "alianza": ("ALIANZA", "Partido Alianza"),
    "rm": ("RM", "Realizando Metas"),
    "pais": ("PAIS", "PAIS"),
    "moca": ("MOCA", "Movimiento Otro Camino"),
    "arn": ("ARN", "Partido Arnulfista"),
    "pdc": ("PDC", "Partido Demócrata Cristiano"),
    "mpe": ("MPE", "Movimiento Papa Egoró"),
    "morena": ("MORENA", "Movimiento de Renovación Nacional"),
    "pla": ("PLA", "Partido Liberal Auténtico"),
    "sol": ("SOL", "Partido Solidaridad"),
    "ren civ": ("RENCIV", "Partido Renovación Civilista"),
    "pnp": ("PNP", "Partido Nacionalista Popular"),
    "fad": ("FAD", "Frente Amplio por la Democracia"),
}  # fmt: skip
INDEPENDENTS = ("libre postulacion", "celeste", "verde", "chocolate", "azul oscuro", "gris oscuro")


def independent(label: str) -> bool:
    return norm(label).startswith(INDEPENDENTS)


def party_code(label: str) -> str:
    return PARTIES.get(norm(label), (norm(label).upper(), text(label)))[0]


def party_id(label: str) -> str:
    return key("party", party_code(label))


def alliance_id(label: str) -> str:
    return key("party", "alliance", norm(label))


@functools.cache
def elections() -> dict[int, Election]:
    loaders = {
        1994: load_1994, 1999: load_1999, 2004: load_2004, 2009: load_2009, 2014: load_2014, 2019: load_2019,
        2024: load_2024,
    }  # fmt: skip
    return {year: load() for year, load in sorted(loaders.items())}


PROVINCE_ALIASES = {
    "comarca ngobe bugle": "ngabe bugle", "ngobe bugle": "ngabe bugle", "ngabe buble": "ngabe bugle",
    "comarca ngabe bugle": "ngabe bugle", "comarca kuna yala": "kuna yala", "guna yala": "kuna yala",
    "comarca guna yala": "kuna yala", "comarca embera": "embera", "comarca embera wounaan": "embera",
    "embera wounaan": "embera", "comarca naso tjer di": "naso tjer di", "san blas": "kuna yala",
    "kanquintu": "kankintu", "nole duina": "nole duima", "renacimientos": "renacimiento",
    "sta catalina o calovebora": "santa catalina o calovebora",
    "santa catalina o calovebora (bledeshia)": "santa catalina o calovebora",
    "comarca k. de madungandi": "comarca kuna de madungandi", "comarca k. de wargandi": "comarca kuna de wargandi",
    "comarca de san blas": "kuna yala", "com. de kuna yala": "kuna yala", "comarca de kuna yala": "kuna yala",
}  # fmt: skip


def place_key(label: str) -> str:
    key_ = norm(label)
    return PROVINCE_ALIASES.get(key_, key_)


SMALL = {"de", "del", "la", "las", "los", "el", "y", "e"}


def pretty(label: str) -> str:
    label = re.sub(r"\s+\d/\s*$", "", text(label))
    if not label.isupper():
        return label
    words = label.lower().split()
    return " ".join(w if w in SMALL and i else w.capitalize() for i, w in enumerate(words))


COMARCAS = {
    "kuna yala",
    "ngabe bugle",
    "embera",
    "naso tjer di",
    "comarca kuna de madungandi",
    "comarca kuna de wargandi",
}
DISPLAY = {
    "kuna yala": "Comarca Kuna Yala",
    "ngabe bugle": "Comarca Ngäbe Buglé",
    "embera": "Comarca Emberá",
    "naso tjer di": "Comarca Naso Tjër Di",
}


def best(labels: list[tuple[int, str]]) -> str:
    key_ = place_key(labels[0][1])
    if key_ in DISPLAY:
        return DISPLAY[key_]
    mixed = [(year, label) for year, label in labels if not label.isupper()]
    return pretty(max(mixed or labels)[1])


@functools.cache
def colliding() -> set[str]:
    """District names that two provinces share in one election, so that their keys carry the province."""
    found: set[str] = set()
    for e in elections().values():
        for office in ("mayor", "rep", "council"):
            seen: dict[str, str] = {}
            for unit in getattr(e, office):
                if unit.scheme in ("distrito", "corregimiento"):
                    district, province = place_key(unit.path[1]), place_key(unit.path[0])
                    if seen.setdefault(district, province) != province:
                        found.add(district)
    return found


def district_label(unit: Unit) -> str:
    name = best(places().districts[district_key(unit)])
    return (
        f"{name} ({best(places().provinces[place_key(unit.path[0])])})"
        if place_key(unit.path[1]) in colliding()
        else name
    )


def district_key(unit: Unit) -> str:
    district = place_key(unit.path[1])
    return f"{district}@{place_key(unit.path[0])}" if district in colliding() else district


# Corregimientos a later table spells without the native name, or with another preposition, keyed by district, so that
# a name another district uses as printed keeps its own key.
CORREGIMIENTO_ALIASES = {
    ("omar torrijos herrera", "san juan del turbe"): "san juan de turbe",
    ("santa catalina o calovebora", "alto bilingue"): "alto bilingue (gdogueshia)",
    ("santa catalina o calovebora", "san pedrito"): "san pedrito (jikui)",
    ("santa catalina o calovebora", "valle bonito"): "valle bonito (dogata)",
}


def corregimiento_key(unit: Unit) -> str:
    key_ = place_key(unit.path[2])
    return CORREGIMIENTO_ALIASES.get((district_key(unit), key_), key_)


@dataclass
class Places:
    provinces: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    circuits: dict[str, str] = field(default_factory=dict)  # code -> latest province key
    districts: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    corregimientos: dict[tuple[str, str], list[tuple[int, str]]] = field(default_factory=dict)
    parents: dict[str, list[tuple[date, str]]] = field(default_factory=dict)  # district -> (day, province)
    circuit_parents: dict[str, list[tuple[date, str]]] = field(default_factory=dict)
    extras: dict[str, str] = field(default_factory=dict)


@functools.cache
def places() -> Places:
    out = Places()
    for e in elections().values():
        for office in ("president", "deputy", "mayor", "rep", "council"):
            for unit in getattr(e, office):
                if unit.scheme in ("exterior", "adelantado"):
                    out.extras[unit.scheme] = pretty(unit.path[0])
                    continue
                province = place_key(unit.path[0])
                out.provinces.setdefault(province, []).append((e.year, unit.path[0]))
                if unit.scheme == "circuito":
                    out.circuit_parents.setdefault(unit.path[1], []).append((e.day, province))
                    continue
                district = district_key(unit)
                out.districts.setdefault(district, []).append((e.year, unit.path[1]))
                out.parents.setdefault(district, []).append((e.day, province))
                if unit.scheme == "corregimiento":
                    out.corregimientos.setdefault((district, corregimiento_key(unit)), []).append(
                        (e.year, unit.path[2])
                    )
    return out


def spans(history: list[tuple[date, str]]) -> list[tuple[str, date | None, date | None]]:
    """Parents over time: each distinct parent from the first election that shows it until the next one's."""
    ordered: list[tuple[date, str]] = []
    by_day: dict[date, list[str]] = {}
    for day, parent in history:
        by_day.setdefault(day, []).append(parent)
    for day, parents in sorted(by_day.items()):
        parent = min(set(parents), key=lambda k: (k not in COMARCAS, k))
        if not ordered or ordered[-1][1] != parent:
            ordered.append((day, parent))
    return [
        (parent, day if i else None, ordered[i + 1][0] if i + 1 < len(ordered) else None)
        for i, (day, parent) in enumerate(ordered)
    ]


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


SCHEMES = (
    ("pa.provincia", 1, "administrative", "province"),
    ("pa.circuito", 2, "electoral_district", "electoral circuit"),
    ("pa.distrito", 2, "administrative", "district"),
    ("pa.corregimiento", 3, "administrative", "corregimiento"),
)
METHOD = (
    "Counted by the Tribunal Electoral per electoral circuit, district, or corregimiento, as published in the "
    "Tribunal's results tables of the general election. Registered voters are the electoral roll, ballots cast are "
    "the votes issued, valid votes are the votes for parties and free-nomination lines, and blank and null votes "
    "are printed separately. The tables give no results per polling station."
)


def source_rows(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Electoral de Panamá",
        title="Resultados de las elecciones generales, cuadros estadísticos",
        url=f"{SITE}/eventos-electorales/elecciones-generales-1994-2024/",
        license="Publicly posted official results; no license stated",
        license_url=SITE,
        attribution="Tribunal Electoral de Panamá",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Panama's provinces and comarcas, electoral circuits, districts, and corregimientos, as named in the "
        "Tribunal Electoral's results tables",
    )
    p.external |= {PANAMA, COUNTRY}
    for code, rank, kind, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=PANAMA, kind=kind, rank=rank, noun=noun, code_system=None)
    found = places()

    def put(scheme: str, code: str, label: str, parents: list[tuple[str, date | None, date | None]]) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        for parent, since, until in parents:
            p.add("area_parent", area_id=id, parent_id=parent, valid_from=since, valid_to=until)
        p.name(id, label, "es", "official")

    for code, labels in sorted(found.provinces.items()):
        put("pa.provincia", code, best(labels), [(COUNTRY, None, None)])
    for code, history in sorted(found.circuit_parents.items(), key=lambda item: [int(x) for x in item[0].split(".")]):
        parents = [(area("pa.provincia", prov), since, until) for prov, since, until in spans(history)]
        put("pa.circuito", code, f"Circuito {code}", parents)
    for code, labels in sorted(found.districts.items()):
        parents = [(area("pa.provincia", prov), since, until) for prov, since, until in spans(found.parents[code])]
        put("pa.distrito", code, best(labels), parents)
    for (district, code), labels in sorted(found.corregimientos.items()):
        put("pa.corregimiento", f"{district}:{code}", best(labels), [(area("pa.distrito", district), None, None)])
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Panama's parties and alliances, offices, bodies, and electoral systems on the Tribunal Electoral's results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PANAMA, COUNTRY}
    source_rows(p)
    for e in elections().values():
        for name in e.files:
            p.artifact(raw.cached(SOURCE, url(name)), SOURCE_ID)
    p.add("body", id=BODY_ASSEMBLY, polity_id=PANAMA, kind="unicameral")
    p.name(BODY_ASSEMBLY, "National Assembly", "en", "official")
    p.name(BODY_ASSEMBLY, "Asamblea Nacional", "es", "native", False)
    p.name(BODY_ASSEMBLY, "Asamblea Legislativa", "es", "historical", False)
    p.add("body", id=BODY_COUNCIL, polity_id=PANAMA, kind="council")
    p.name(BODY_COUNCIL, "Municipal councils", "en", "official")
    p.name(BODY_COUNCIL, "Concejos Municipales", "es", "native", False)
    p.add("body", id=BODY_JUNTA, polity_id=PANAMA, kind="council")
    p.name(BODY_JUNTA, "Communal boards", "en", "official")
    p.name(BODY_JUNTA, "Juntas Comunales", "es", "native", False)
    for id, kind, en, es, body in (
        (PRESIDENCY, "head_of_state", "President of Panama", "Presidente de la República", None),
        (MAYORALTY, "mayor", "Mayor", "Alcalde", None),
        (REPRESENTATIVE, "other", "Representative of a corregimiento", "Representante de corregimiento", BODY_JUNTA),
    ):
        p.add("office", id=id, polity_id=PANAMA, body_id=body, kind=kind)
        p.name(id, en, "en", "official")
        p.name(id, es, "es", "native", False)
    p.add("electoral_system", id=SYSTEM_PLURALITY, polity_id=PANAMA, valid_from=None, valid_to=None,
          rules={"formula": "fptp"}, rules_version=1)  # fmt: skip
    p.add(
        "note",
        id=key("note", SYSTEM_PLURALITY),
        entity_id=SYSTEM_PLURALITY,
        kind="methodology",
        body_md="The candidate with the most votes is elected president, mayor, or representative of a corregimiento.",
        lang="en",
    )
    seen: set[str] = set()
    for e in elections().values():
        labels = {
            label
            for office in ("deputy", "mayor", "rep", "council")
            for unit in getattr(e, office)
            for label in unit.row.votes
        }
        labels |= {label for t in e.tickets for label in t.parties}
        for label in sorted(labels):
            if independent(label) or party_code(label) in seen:
                continue
            seen.add(party_code(label))
            id = party_id(label)
            code, name = PARTIES.get(norm(label), (party_code(label), text(label)))
            p.add("party", id=id, kind="party", polity_id=PANAMA)
            p.name(id, name, "es", "official")
            if name != code:
                p.name(id, code, "es", "abbreviation", False)
        for t in e.tickets:
            if len(t.parties) > 1 and not independent(t.label) and "libre" not in norm(t.label):
                id = alliance_id(t.label)
                if id not in seen:
                    seen.add(id)
                    p.add("party", id=id, kind="bloc", polity_id=PANAMA)
                    p.name(id, t.label, "es", "official")
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in elections().values())]


# Where the rows of a table do not add up to its printed Total, and why. The figures are stored as the rows print them.
EXPLAINED = {
    (
        1994,
        "rep",
        "reg",
    ): "the rows add up to 3,854 fewer registered voters than the printed Total, and the Yaviza row prints 2 registered voters against 2,517 votes cast",
    (
        2024,
        "rep",
        "reg",
    ): "the rows add up to 1,173 fewer registered voters than the printed Total, and the Paraíso row prints 668 registered voters against 952 votes cast",
    (2024, "council", "valid"): "the printed Total leaves out the Comarca Naso Tjër Di, whose row is in the table",
}


def compare(e: Election) -> list[str]:
    """Check each office's units against the national Total row the Tribunal prints, and say what was compared."""
    lines: list[str] = []
    for office in ("president", "deputy", "mayor", "rep", "council"):
        units = getattr(e, office)
        printed = e.printed.get(office)
        if not units or printed is None:
            continue
        found: dict[str, tuple[int, int]] = {}
        for name in ("valid", "cast", "reg", "blank", "null"):
            want = getattr(printed, name)
            got = sum(getattr(u.row, name) or 0 for u in units)
            if want is not None and got != want:
                found[name] = (got, want)
        for party, want in printed.votes.items():
            got = sum(u.row.votes.get(party, 0) for u in units)
            if got != want:
                found[party] = (got, want)
        if not found:
            lines.append(f"{e.year} {office}: {len(units)} units, every printed total matches")
            continue
        reasons = [EXPLAINED.get((e.year, office, name)) for name in ("valid", "reg") if name in found]
        if not all(reasons) or (e.year, office) == (2024, "council") and "valid" not in found:
            raise ValueError(f"{e.year} {office}: the rows differ from the printed totals {found}")
        lines.append(
            f"{e.year} {office}: {len(units)} units, differs from the printed total in {sorted(found)}: "
            + "; ".join(str(r) for r in reasons)
        )
    return lines


def parse() -> None:
    for e in elections().values():
        if e.issues:
            raise ValueError(f"{e.year}: {e.issues}")
        for line in compare(e):
            print(line, flush=True)
    geo().write()
    registry().write()
    for e in elections().values():
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def area_of(unit: Unit) -> tuple[str, str] | None:
    """The scheme and code of the area a unit lies in, or None for votes that belong to no area."""
    if unit.scheme == "circuito":
        return "pa.circuito", unit.path[1]
    if unit.scheme == "distrito":
        return "pa.distrito", district_key(unit)
    if unit.scheme == "corregimiento":
        return "pa.corregimiento", f"{district_key(unit)}:{corregimiento_key(unit)}"
    return None


OFFICES = {
    "president": ("President", "Presidente"),
    "deputy": ("Deputies, {}", "Diputados, {}"),
    "mayor": ("Mayor of {}", "Alcalde de {}"),
    "rep": ("Representative of {}", "Representante de {}"),
    "council": ("Councillors of {}", "Concejales de {}"),
}
WORDS = {
    "president": "president",
    "deputy": "deputies",
    "mayor": "mayors",
    "rep": "representatives",
    "council": "councillors",
}


def election(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"The {e.day:%-d %B %Y} Panamanian general election by circuit, district, and corregimiento from the "
        "Tribunal Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PANAMA, COUNTRY}
    for name in e.files:
        p.uses(raw.cached(SOURCE, url(name)))
    id = key("election", e.day)
    p.add("election", id=id, polity_id=PANAMA, kind="general", date_from=e.day, date_to=e.day, status="held")
    p.name(id, f"{e.year} Panamanian general election", "en", "official")
    p.name(id, f"Elecciones generales de {e.year}", "es", "native", False)
    path = f"pa/{e.day}-general"
    p.add("slug", path=path, entity_id=id, is_current=True)
    units: dict[str, str] = {}

    def unit(u: Unit) -> str:
        found = area_of(u)
        code = f"{found[0]}:{found[1]}" if found else u.scheme
        if code in units:
            return units[code]
        uid = units[code] = key("unit", e.day, code)
        scheme = found[0] if found else "pa.circuito"
        p.add(
            "reporting_unit",
            id=uid,
            polity_id=PANAMA,
            scheme_code=scheme,
            code=f"{e.day}:{found[1] if found else u.scheme}",
            area_relation="equals" if found else "unlocated",
            area_id=area(*found) if found else None,
            kind="standard" if found or u.scheme == "adelantado" else "abroad_mission",
        )
        return uid

    def contest(office: str, parts: Sequence[object], label: str, area_id: str, **fields: object) -> tuple[str, str]:
        cid = key("contest", e.day, office, *parts)
        en, es = OFFICES[office]
        p.add(
            "contest",
            id=cid,
            election_id=id,
            district_area_id=area_id,
            round_number=1,
            default_result_kind="counted",
            **fields,
        )
        p.name(cid, en.format(label), "en", "official")
        p.name(cid, es.format(label), "es", "native", False)
        slug = changeset.slug(label)
        p.add(
            "slug",
            path=f"{path}/{WORDS[office]}" + (f"/{slug}" if office != "president" else ""),
            entity_id=cid,
            is_current=True,
        )
        rid = key("result_set", e.day, office, *parts)
        p.add(
            "result_set", id=rid, election_id=id, contest_id=cid, stage="official", kind="counted",
            source_id=SOURCE_ID, status="active", is_default=True, completeness=1.0,
        )  # fmt: skip
        p.add("note", id=key("note", rid), entity_id=rid, kind="methodology", body_md=METHOD, lang="en")
        return cid, rid

    over: dict[str, list[str]] = {}

    def values(rid: str, uid: str, row: Row, options: dict[str, str]) -> None:
        if row.reg is not None and row.cast is not None and row.cast > row.reg:
            over.setdefault(rid, []).append(f"{row.cast:,} ballots cast and {row.reg:,} registered")
        for measure, value in (
            (REGISTERED, row.reg),
            (CAST, row.cast),
            (VALID, row.valid),
            (INVALID, row.null),
            (BLANK, row.blank),
        ):
            if value is not None:
                p.add("result_value", result_set_id=rid, unit_id=uid, option_id=None, channel=0, measure=measure,
                      count_round=0, flags=0, value=value)  # fmt: skip
        for label, value in row.votes.items():
            p.add("result_value", result_set_id=rid, unit_id=uid, option_id=options[label], channel=0, measure=VOTES,
                  count_round=0, flags=0, value=value)  # fmt: skip

    def option(cid: str, label: str, position: int, tag: str) -> str:
        oid = key("option", cid, norm(label))
        if independent(label):
            p.add("contest_option", id=oid, contest_id=cid, kind="independents", ballot_position=position,
                  ballot_label=pretty(label))  # fmt: skip
        else:
            p.add("contest_option", id=oid, contest_id=cid, kind="list", party_id=party_id(label), ballot_position=position,
                  ballot_label=text(label))  # fmt: skip
        return oid

    # The presidency: one contest, with a unit for every electoral circuit.
    cid, rid = contest("president", [], "", COUNTRY, kind="office", office_id=PRESIDENCY, magnitude=1,
                       electoral_system_id=SYSTEM_PLURALITY)  # fmt: skip
    options: dict[str, str] = {}
    for position, t in enumerate(e.tickets, 1):
        oid = options[t.label] = key("option", cid, norm(t.label))
        member = None
        if len(t.parties) > 1 and not independent(t.label) and "libre" not in norm(t.label):
            member = alliance_id(t.label)
        elif len(t.parties) == 1 and not independent(t.parties[0]):
            member = party_id(t.parties[0])
        p.add(
            "contest_option",
            id=oid,
            contest_id=cid,
            kind="ticket",
            party_id=member,
            ballot_position=position,
            ballot_label=t.label,
        )
        for role, name in (("head", t.name), *(("running_mate", mate) for mate in t.mates)):
            if name:
                person = key("person", e.day, norm(t.label), role, name)
                p.add("person", id=person)
                p.name(person, name, "es", "official")
                p.add("candidacy", id=key("candidacy", e.day, norm(t.label), role, name), person_id=person, option_id=oid, role=role,
                      ballot_name=name, is_independent=any(independent(label) for label in (t.label, *t.parties)))  # fmt: skip
    for u in e.president:
        values(rid, unit(u), u.row, options)
    total, votes = e.printed["president"], e.printed["president_votes"]
    for measure, value in (
        (REGISTERED, total.reg),
        (CAST, total.cast),
        (VALID, total.valid),
        (INVALID, total.null),
        (BLANK, total.blank),
    ):
        if value is not None:
            p.add("declared", result_set_id=rid, option_id=None, measure=measure, value=value)
    for label, value in votes.votes.items():
        p.add("declared", result_set_id=rid, option_id=options[label], measure=VOTES, value=value)

    # Every other contest has one unit: its own circuit, district, or corregimiento.
    for office in ("deputy", "mayor", "rep", "council"):
        for u in getattr(e, office):
            found = area_of(u)
            assert found is not None
            if office == "deputy":
                label, parts = f"circuit {u.path[1]}", [u.path[1]]
                fields = {"kind": "body_seats", "body_id": BODY_ASSEMBLY}
            elif office == "mayor":
                label, parts = district_label(u), [found[1]]
                fields = {
                    "kind": "office",
                    "office_id": MAYORALTY,
                    "magnitude": 1,
                    "electoral_system_id": SYSTEM_PLURALITY,
                }
            elif office == "rep":
                label = f"{best(places().corregimientos[(district_key(u), corregimiento_key(u))])}, {district_label(u)}"
                parts = [found[1]]
                fields = {
                    "kind": "office",
                    "office_id": REPRESENTATIVE,
                    "magnitude": 1,
                    "electoral_system_id": SYSTEM_PLURALITY,
                }
            else:
                label, parts = district_label(u), [found[1]]
                fields = {"kind": "body_seats", "body_id": BODY_COUNCIL}
            cid, rid = contest(office, parts, label, area(*found), **fields)
            options = {name: option(cid, name, position, "") for position, name in enumerate(u.row.votes, 1)}
            values(rid, unit(u), u.row, options)
    if over:
        p.accept(
            "accounting.turnout",
            f"the Tribunal prints more ballots cast than registered voters in {sum(map(len, over.values()))} units, "
            "stored as printed",
        )
        for rid, figures in over.items():
            p.add(
                "note",
                id=key("note", rid, "turnout"),
                entity_id=rid,
                kind="caveat",
                body_md="The Tribunal's table prints more ballots cast than registered voters here "
                f"({'; '.join(figures)}). The figures are stored as printed.",
                lang="en",
            )
    return p


P94 = [
    "LIB",
    "PRD",
    "PDC",
    "MOLIRENA",
    "PALA",
    "PLA",
    "PPD",
    "ARN",
    "MUN",
    "MPE",
    "UDI",
    "LIBRE",
    "REN CIV",
    "SOL",
    "MORENA",
]
P99 = ["PRD", "PDC", "MOLIRENA", "ARN", "MPE", "REN CIV", "SOL", "MORENA", "PNP", "LIB", "LIB NAL", "CAM DEM"]
P14 = ["PRD", "POPULAR", "MOLIRENA", "PAN", "CAM DEM", "FAD", "CELESTE", "VERDE", "CHOCOLATE"]
LP = ["Libre Postulación (1)", "Libre Postulación (2)"]
C94 = {"reg": 5, "cast": 6, "valid": 7, "null": 9}
C99 = {"reg": 5, "cast": 6, "valid": 9, "blank": 11, "null": 13}


def pdf_party_table(
    name: str,
    labels: list[str],
    e: Election,
    depth: int | str,
    office: str,
    reg: int | None = None,
    start: str | None = None,
) -> None:
    rows = pdf_party_rows(pdf_grid(name, start), labels, reg)
    units = circuits(rows) if depth == "c" else districts(rows, int(depth), e.issues, is_top_pdf)
    getattr(e, office).extend(units)
    e.printed[office] = rows[0]


def load_1994() -> Election:
    e = Election(1994, date(1994, 5, 8), [])
    names = ["01_-_Presidente-1", "02_-_Presidente_x_circuito", "04_-_Legislador_x_circuito", "09_-_Alcalde_x_distrito",
             "12_-_Representante_x_corregimiento", "14_-_Concejal_x_distrito"]  # fmt: skip
    e.files = [f"1994/Cuadro_{n}.pdf" for n in names]
    president_table(1994, pdf_grid(e.files[0]), C94, pdf_grid(e.files[1]), e)
    pdf_party_table(e.files[2], P94, e, "c", "deputy", start="CUADRO No.4")
    pdf_party_table(e.files[3], [*P94, "PNP", *LP], e, 2, "mayor")
    pdf_party_table(e.files[4], [*P94, "PNP", *LP], e, 3, "rep", reg=4)
    pdf_party_table(e.files[5], [*P94, "Libre Postulación"], e, 2, "council")
    e.issues = [i for i in e.issues if i != "DARIEN: districts do not add up to the province"]
    e.notes["rep"] = "the table counts the Emberá comarca inside Darién province's printed total"
    return e


def load_1999() -> Election:
    e = Election(1999, date(1999, 5, 2), [])
    names = ["01_-_Presidente_x_circuito", "02_-_Presidente_x_circuito_y_partido", "04_Legislador", "11_-_Alcalde_por_distrito",
             "14_-_Representante_x_corregimiento", "16_-_Concejal_x_distrito"]  # fmt: skip
    e.files = [f"1999/Cuadro_{n}.pdf" for n in names]
    president_table(1999, pdf_grid(e.files[0]), C99, pdf_grid(e.files[1]), e)
    pdf_party_table(e.files[2], P99, e, "c", "deputy")
    full = [*P99, "CHOCOLATE", "AZUL OSCURO", "GRIS OSCURO"]
    pdf_party_table(e.files[3], full, e, 2, "mayor")
    pdf_party_table(e.files[4], full, e, 3, "rep", reg=4)
    pdf_party_table(e.files[5], P99, e, 2, "council")
    return e


def load_2014() -> Election:
    e = Election(2014, date(2014, 5, 4), [])
    names = ["01_-_Presidente", "02_-_Presidente", "04_-_Diputado_PP", "08_-_Alcalde_PP"]
    e.files = [f"2014/Cuadro_{n}.pdf" for n in names]
    president_table(2014, pdf_grid(e.files[0], stop="DE MAYO DE 2009"), C1, pdf_grid(e.files[1]), e)
    pdf_party_table(e.files[2], P14, e, "c", "deputy")
    pdf_party_table(e.files[3], P14, e, 2, "mayor")
    return e
