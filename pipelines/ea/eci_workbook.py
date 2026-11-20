"""The Election Commission of India's "Constituency Wise Detailed Result" workbooks for the 2014 and 2019 Lok Sabha
general elections.

Each workbook is one sheet with a row per candidate (and one for "None of the Above") in the order the constituency's
list prints, under a header row that names State Name, PC NAME, the candidate, the party, the votes on the machines
(GENERAL), the postal votes, the total, and the constituency's total electors. The workbook has no constituency numbers,
no winner column, and no turnout column. A constituency is identified by its state and name.

The 2014 file is an .xlsx workbook, the 2019 file an .xls workbook whose compound-document stream has a sector chain
that `xlrd` calls corrupt although every cell reads back (`ignore_workbook_corruption`); the same Commission table was
also published as .xlsx, which the module reads the same way.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import fastexcel
import xlrd


@dataclass
class Line:
    name: str
    sex: str | None
    party: str
    symbol: str | None
    general: int
    postal: int
    total: int


@dataclass
class Seat:
    state: str
    name: str
    electors: int
    lines: list[Line] = field(default_factory=list)
    printed: tuple[int, int, int] | None = None  # the PDF's printed totals: postal, total, machine votes

    @property
    def candidates(self) -> list[Line]:
        return [x for x in self.lines if x.party != "NOTA"]

    @property
    def nota(self) -> Line | None:
        found = [x for x in self.lines if x.party == "NOTA"]
        return found[0] if found else None


def _cells(path: Path) -> Iterator[list[object]]:
    if path.suffix == ".xls":
        sheet = xlrd.open_workbook(path, ignore_workbook_corruption=True).sheet_by_index(0)
        for i in range(sheet.nrows):
            yield sheet.row_values(i)
        return
    book = fastexcel.read_excel(str(path))
    frame = book.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars()
    yield from (list(row) for row in frame.iter_rows())


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _int(value: object) -> int:
    text = _text(value)
    number = float(text)
    if number != int(number):
        raise ValueError(f"{text!r} is not a whole number")
    return int(number)


def seats(path: Path) -> list[Seat]:
    """The workbook's constituencies in file order, each with its lines."""
    found: list[Seat] = []
    started = False
    for row in _cells(path):
        first = _text(row[0]).lower()
        if not started:
            started = first == "state name"
            continue
        if not first or first == "disclaimer":
            if found:
                break
            continue
        state, pc, name = _text(row[0]), _text(row[1]), _text(row[2])
        sex = _text(row[3]).upper()
        line = Line(
            name=name,
            sex={"M": "male", "MALE": "male", "F": "female", "FEMALE": "female"}.get(sex),
            party=_text(row[6]),
            symbol=_text(row[7]) or None,
            general=_int(row[8]),
            postal=_int(row[9]),
            total=_int(row[10]),
        )
        electors = _int(row[13])
        if not found or (found[-1].state, found[-1].name) != (state, pc):
            found.append(Seat(state, pc, electors))
        elif found[-1].electors != electors:
            raise ValueError(f"{state} {pc}: two figures for the electors")
        found[-1].lines.append(line)
    return found


# The PDF print of the 2014 table, which holds all 543 constituencies (the 2014 workbook lacks Odisha's and
# Chhattisgarh's). Its text layer runs: the constituency's state (on the first of the state's constituencies), number,
# name, "CONSTITUENCY :", the electors, then for each candidate the serial number and name (which may wrap), sex, age,
# category, party, the postal, total, and machine votes, two percentages, and the symbol (which may wrap), and last the
# constituency's totals, "TOTAL:" among them.

_NUMBER = re.compile(r"^\s*(\d+)\s*$")
_SERIAL = re.compile(r"^\s*(\d+)\s+(\S.*)$")
_FIGURE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _TOTAL(body: list[str], i: int) -> bool:
    """Whether the lines from `i` are the constituency's totals: the postal and total votes, then "TOTAL:"."""
    return i + 2 < len(body) and body[i + 2].strip() == "TOTAL:"


def pdf_seats(path: Path, states: set[str]) -> list[Seat]:
    """The PDF's constituencies in file order, with the states the PDF prints (`states` lists the names it may use)."""
    import pymupdf

    lines: list[str] = []
    for page in pymupdf.open(path):
        lines += page.get_text().split("\n")
    heads = [i for i, line in enumerate(lines) if line.strip() == "CONSTITUENCY :"]
    found: list[Seat] = []
    state = ""
    for n, at in enumerate(heads):
        number, name = lines[at - 2].strip(), lines[at - 1].strip()
        if number != "1" and not _NUMBER.match(number):
            raise ValueError(f"constituency head at line {at} has no number")
        if number == "1":
            # A page's running header can fall between the state and the constituency.
            state = next(x.strip() for x in lines[max(at - 40, 0) : at - 2][::-1] if x.strip() in states)
        end = (heads[n + 1] - 3) if n + 1 < len(heads) else len(lines)
        electors = _int(
            lines[at + 4 : at + 8][next(j for j, x in enumerate(lines[at + 4 : at + 8]) if _FIGURE.match(x))]
        )
        body = lines[at + 4 :]
        body = body[: end - at - 4]
        # The electors figure ends the head; candidates follow.
        i = next(j for j, x in enumerate(body) if _FIGURE.match(x)) + 1
        seat = Seat(state, name, electors)
        try:
            while i < len(body):
                first = _SERIAL.match(body[i])
                if not first:
                    i += 1
                    continue
                text = first.group(2).strip()
                i += 1
                if text == "None of the Above":
                    sex = None
                else:
                    parts = [text]
                    while body[i].strip() not in ("M", "F", "O", "T"):
                        parts.append(body[i].strip())
                        i += 1
                    text, sex = " ".join(parts), body[i].strip()
                    i += 3  # sex, age, category
                party = ""
                while not _FIGURE.match(body[i]):
                    party += body[i].strip()  # a long code wraps: "CPI(ML)" and "(L)"
                    i += 1
                postal, total, general = (_int(body[i + k]) for k in range(3))
                i += 5  # three counts, two percentages
                symbol: list[str] = []
                while i < len(body) and not _SERIAL.match(body[i]) and not _TOTAL(body, i):
                    symbol.append(body[i].strip())
                    i += 1
                seat.lines.append(
                    Line(
                        name=text,
                        sex={"M": "male", "F": "female"}.get(sex or ""),
                        party=party,
                        symbol=" ".join(symbol) or None,
                        general=general,
                        postal=postal,
                        total=total,
                    )
                )
        except ValueError, IndexError:
            seat.lines = []  # a constituency the text layer garbles reads as empty; callers check the printed totals
        for k, x in enumerate(body):
            if x.strip() == "TOTAL:" and k >= 2:
                seat.printed = (_int(body[k - 2]), _int(body[k - 1]), _int(body[k + 1]))
        found.append(seat)
    return found
