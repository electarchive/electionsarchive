"""The Tribunal Electoral's table of the 1994 presidential votes by party, per corregimiento (Cuadro 3)."""

import re
from dataclasses import dataclass, field

# The party columns in the order the table prints them.
PARTIES = [
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
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?$")
FIGURES = 6 + len(PARTIES) + 2  # roll, turnout, abstention, cast, valid, valid share, parties, null, null share


@dataclass
class Line:
    label: str
    indent: int
    reg: int
    cast: int
    valid: int
    null: int
    votes: dict[str, int]


@dataclass
class Circuit:
    code: str
    line: Line
    members: list[Line] = field(
        default_factory=list
    )  # the rows under the circuit: districts, their groups, corregimientos


@dataclass
class Province:
    line: Line
    circuits: list[Circuit] = field(default_factory=list)


@dataclass
class Table:
    total: Line
    provinces: list[Province]


def line_of(text: str) -> Line | None:
    parts = re.split(r"\s{2,}", text.strip())
    if len(parts) < 2:
        return None
    tokens = " ".join(parts[1:]).split()
    if len(tokens) != FIGURES or not all(NUMBER.match(t) for t in tokens):
        return None
    n = [int(t.replace(",", "")) if "." not in t else 0 for t in tokens]
    return Line(
        parts[0].strip(),
        len(text) - len(text.lstrip()),
        n[0],
        n[3],
        n[4],
        n[6 + len(PARTIES)],
        dict(zip(PARTIES, n[6 : 6 + len(PARTIES)], strict=True)),
    )


def parse(lines: list[str]) -> Table:
    rows: list[Line] = []
    for text in lines:
        if row := line_of(text):
            rows.append(row)
    total = next(row for row in rows if row.label.upper() == "TOTAL")
    provinces: list[Province] = []
    for row in rows:
        match = re.match(r"circuito\s+(\d+\.\d+)$", row.label.strip().lower())
        if row is total:
            continue
        if match:
            provinces[-1].circuits.append(Circuit(match[1], row))
        elif row.label.isupper():
            provinces.append(Province(row))
        else:
            provinces[-1].circuits[-1].members.append(row)
    return Table(total, provinces)
