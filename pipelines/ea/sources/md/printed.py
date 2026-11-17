"""The printed polling-station tables of the Central Electoral Commission's book on the early parliamentary election
of 28 November 2010. Each circumscription's table is split over pairs of pages: the left page holds the first 19
columns, the right page the last 29, and the first right page of a table is printed upside down, so its text is read
from its word coordinates."""

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

LEFT, RIGHT = 19, 29  # numeric columns 3-21 on the left pages and 22-50 on the right pages


@dataclass
class Row:
    number: str
    place: str
    values: list[int]


@dataclass
class Block:
    """One printed table: its rows in order, and its printed total row from the top and from the bottom."""

    name: str
    rows: list[Row]
    top: list[int] | None
    bottom: list[int] | None


def _text(path: Path, first: int, last: int) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split("\f")[: last - first + 1]


def _integer(token: str) -> bool:
    return re.fullmatch(r"-?\d+", token) is not None


def _left(page: str) -> list[tuple[str, str, list[int] | None]]:
    """The rows of a left page. A total row prints no cell for a column that adds to zero, so its values can be
    short, and it is then returned without them."""
    found: list[tuple[str, str, list[int] | None]] = []
    for line in page.split("\n"):
        tokens = line.split()
        if tokens and tokens[0].casefold() == "total" and all(_integer(token) for token in tokens[1:]):
            full = len(tokens) == LEFT + 1
            found.append(("Total", "", [int(token) for token in tokens[1:]] if full else None))
            continue
        if len(tokens) < LEFT + 1 or not all(_integer(token) for token in tokens[-LEFT:]):
            continue
        if tokens == [str(i) for i in range(1, 22)] or not tokens[0].isdigit():
            continue
        found.append((tokens[0], " ".join(tokens[1 : len(tokens) - LEFT]), [int(token) for token in tokens[-LEFT:]]))
    return found


def _right(page: str) -> list[list[int] | None]:
    """The rows of a right page. A total row can lack the cells of columns that add to zero, and is then None."""
    found: list[list[int] | None] = []
    for line in page.split("\n"):
        tokens = line.split()
        if len(tokens) < 5 or len(tokens) > RIGHT or not all(_integer(token) for token in tokens):
            continue
        if tokens != [str(i) for i in range(22, 51)]:
            found.append([int(token) for token in tokens] if len(tokens) == RIGHT else None)
    return found


def _flipped(path: Path, number: int) -> list[list[int | None]]:
    """A right page that is printed turned: a line per column, from column 50 down to column 22, each line holding
    the table's rows in order (its total rows included) along the page. Returns a row per table row."""
    out = subprocess.run(
        ["pdftotext", "-f", str(number), "-l", str(number), "-bbox", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = [
        (float(y), float(x), int(text))
        for x, y, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="[\d.]+">(.*?)</word>', out
        )
        if _integer(text)
    ]
    # The column numbers 22 to 50 stand in one line down the page, when the page prints them.
    by_x: dict[int, list[int]] = defaultdict(list)
    for _, x, value in found:
        by_x[round(x)].append(value)
    numbered = [x for x, values in by_x.items() if sorted(values) == list(range(22, 51))]
    tokens = sorted(token for token in found if not numbered or round(token[1]) != numbered[0])
    bands: list[list[tuple[float, int]]] = []
    previous = -100.0
    for y, x, value in tokens:
        if y - previous > 1.5:
            bands.append([])
        bands[-1].append((x, value))
        previous = y
    lines = [sorted(band) for band in bands if len(band) > 1]  # a lone number is the page number
    if len(lines) != RIGHT:
        raise ValueError(f"page {number} has {len(lines)} lines of values, not {RIGHT}")
    # The rows stand at the same places along every line, but a total that is zero is left blank.
    places: list[float] = []
    for x in sorted(x for line in lines for x, _ in line):
        if not places or x - places[-1] > 1.0:
            places.append(x)
    width = len(places)
    columns: list[list[int | None]] = []
    for line in reversed(lines):
        cells: list[int | None] = [None] * width
        for x, value in line:
            cells[min(range(width), key=lambda i: abs(places[i] - x))] = value
        columns.append(cells)
    if [column[0] for column in columns] == list(range(22, 51)):  # the column's number heads its own values
        columns = [column[1:] for column in columns]
    return [list(row) for row in zip(*columns, strict=True)]


def read(path: Path, first: int, last: int, name: str = "") -> Block:
    """A table that runs from page `first` to page `last`, in pairs of a left and a right page. The left pages print
    the table's total row before its first station and again after its last, and the right pages follow the same rows."""
    pages = _text(path, first, last)
    if len(pages) % 2:
        raise ValueError(f"pages {first} to {last} are not in pairs")
    left_rows: list[tuple[str, str, list[int] | None]] = []
    right_rows: list[list[int | None] | None] = []
    for index in range(0, len(pages), 2):
        left_rows += _left(pages[index])
        found = _right(pages[index + 1])
        right_rows.extend([list(row) if row else None for row in found] or _flipped(path, first + index + 1))
    if len(left_rows) != len(right_rows):
        raise ValueError(f"{name}: {len(left_rows)} rows on the left pages, {len(right_rows)} on the right")
    rows: list[Row] = []
    totals: list[list[int] | None] = []
    for position, ((number, place, a), b) in enumerate(zip(left_rows, right_rows, strict=True)):
        if number == "Total":
            totals.append(None if a is None or b is None else [*a, *(0 if value is None else value for value in b)])
            if position not in (0, len(left_rows) - 1):
                raise ValueError(f"{name}: a total row in the middle of the table")
        else:
            if b is None or None in b:
                raise ValueError(f"{name}: station {number} has a blank cell")
            if a is None:
                raise ValueError(f"{name}: station {number} has no values")
            rows.append(Row(number, place, [*a, *(value or 0 for value in b)]))
    top = totals[0] if left_rows[0][0] == "Total" else None
    bottom = totals[-1] if left_rows[-1][0] == "Total" and (len(totals) > 1 or top is None) else None
    return Block(name, rows, top, bottom)


def circumscriptions(path: Path, first: int, last: int) -> dict[int, Block]:
    """Every circumscription's table in the pages from `first` to `last`, by circumscription number."""
    pages = _text(path, first, last)
    starts = [
        (first + index, int(found.group(1)))
        for index, page in enumerate(pages)
        if (found := re.search(r"CIRCUMSCRIP[^\n]*?nr\.\s*(\d+)", page))
    ]
    ends = [start for start, _ in starts[1:]] + [last + 1]
    return {number: read(path, start, end - 1, str(number)) for (start, number), end in zip(starts, ends, strict=True)}
