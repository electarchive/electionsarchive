"""Reads the tables of the electoral organ's results bulletins, which are laid out as plain text lines of counts.

A row is a name followed by seven counts (Sí, No, valid, blank, null, ballots cast, registered voters) and, in most
tables, the abstention. The name wraps over two lines in some tables, and a few rows are scrambled by a page break; the
module raises on any row that fails to add up, and the curated rows below carry their proof.
"""

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pymupdf

NUM = r"\d{1,3}(?:\.\d{3})*|\d+"
COUNTS = ("yes", "no", "valid", "blank", "null", "cast", "registered")
ROW = re.compile(rf"^\s*(?P<name>[A-Za-zÁÉÍÓÚÑáéíóúñ][^\d]*?)\s{{2,}}(?P<n>(?:(?:{NUM})\s+){{6,7}}(?:{NUM}))\s*$")
PERCENT = r"\d{1,3},\d{2}\s?%"
ETA = re.compile(
    rf"^\s*(?:(?P<dept>[A-Za-zÁÉÍÓÚáéíóú ]+?)\s{{2,}})?(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúÑñ .]+?)"
    rf"\s+(?P<mesas>{NUM})\s+{PERCENT}\s+(?P<reg>{NUM})\s+(?P<si>{NUM})\s+{PERCENT}\s+(?P<no>{NUM})\s+{PERCENT}"
    rf"\s+(?P<valid>{NUM})\s+(?P<blank>{NUM})\s+(?P<null>{NUM})\s+(?P<cast>{NUM})\s*$"
)


def lines(pdf: Path) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return out.split("\n")


def integer(text: str) -> int:
    return int(text.replace(".", ""))


def counts(numbers: str) -> dict[str, int]:
    values = [integer(n) for n in numbers.split()]
    return dict(zip(COUNTS, values[:7], strict=True))


def after(text: list[str], marker: str, start: int = 0) -> int:
    """The index of the first line from start on that holds the marker."""
    for i in range(start, len(text)):
        if re.search(marker, text[i]):
            return i
    raise LookupError(marker)


def rows(text: list[str], start: int, stop: str = r"Fuente") -> dict[str, dict[str, int]]:
    """The named rows of the table that begins after a line, up to the line that closes it."""
    found: dict[str, dict[str, int]] = {}
    for line in text[start:]:
        if re.search(stop, line) and found:
            break
        match = ROW.match(line)
        if match:
            name = re.sub(r"\s+", " ", match.group("name")).strip()
            if name in found:
                raise ValueError(f"{name} appears twice")
            found[name] = counts(match.group("n"))
    return found


def eta_rows(text: list[str]) -> list[tuple[str, dict[str, int]]]:
    """The one row of each autonomous territorial entity in the summary table, and the table's total row."""
    found = []
    for line in text:
        match = ETA.match(line)
        if match:
            name = re.sub(r"\s+", " ", match["name"]).strip()
            found.append(
                (
                    name,
                    {
                        "yes": integer(match["si"]),
                        "no": integer(match["no"]),
                        "valid": integer(match["valid"]),
                        "blank": integer(match["blank"]),
                        "null": integer(match["null"]),
                        "cast": integer(match["cast"]),
                        "registered": integer(match["reg"]),
                    },
                )
            )
    return found


TOTAL = re.compile(
    rf"^\s*Total\s+(?:{NUM})\s+{PERCENT}\s+(?P<reg>{NUM})\s+(?P<si>{NUM})\s+(?P<no>{NUM})\s+(?P<valid>{NUM})"
    rf"\s+(?P<blank>{NUM})\s+(?P<null>{NUM})\s+(?P<cast>{NUM})\s*$",
    re.MULTILINE,
)
TOTAL_ROW = re.compile(rf"^\s*Total\s+(?P<n>(?:(?:{NUM})\s+){{6,7}}(?:{NUM}))\s*$")


def eta_total(text: list[str]) -> dict[str, int]:
    """The total row of a summary table of autonomous territorial entities whose percentages are not printed."""
    match = TOTAL.search("\n".join(text))
    if match is None:
        raise LookupError("no total row")
    return {
        "yes": integer(match["si"]),
        "no": integer(match["no"]),
        "valid": integer(match["valid"]),
        "blank": integer(match["blank"]),
        "null": integer(match["null"]),
        "cast": integer(match["cast"]),
        "registered": integer(match["reg"]),
    }


def locality_total(text: list[str], entity: str) -> dict[str, int]:
    """The total of a municipality's table by locality, the first total row after its heading."""
    start = after(text, rf"MUNICIPIO DE {entity.upper()}: .*SEGÚN(?:\s|$)|MUNICIPIO DE {entity.upper()}: .*LOCALIDAD")
    for line in text[start : start + 25]:
        match = TOTAL_ROW.match(line)
        if match:
            return counts(match["n"])
    raise LookupError(entity)


class BoxRow(NamedTuple):
    page: int
    name: str
    counts: dict[str, int]


SCALE = 3


def visible_words(pdf: Path, number: int) -> list[tuple[float, float, str]]:
    """The words of a page (top, left, text) that show ink when the page is rendered.

    Some pages carry the text of neighboring pages, clipped out of sight, and the text layer does not tell them apart.
    """
    page = pymupdf.open(pdf)[number - 1]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), colorspace=pymupdf.csGRAY)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w)
    out = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        box = image[int(y0 * SCALE) : int(y1 * SCALE) + 1, int(x0 * SCALE) : int(x1 * SCALE) + 1]
        if box.size and int((box < 110).sum()) >= 2:
            out.append((y0, x0, text))
    return out


def box_rows(pdf: Path, pages: range) -> list[BoxRow]:
    """The rows of tables with a name column on the left and the counts to its right, read from the word boxes.

    A row's name is the visible words in the left columns nearest to its counts' line; a name that wraps keeps
    both lines. The last column (abstention) is cut off at the page edge in some tables and is not read.
    """
    number = re.compile(rf"^(?:{NUM})$")
    out: list[BoxRow] = []
    for page in pages:
        words = visible_words(pdf, page)
        lines: dict[int, list[tuple[float, str]]] = {}
        counted: list[tuple[float, list[str]]] = []
        for y, x, text in words:
            if x < 150 and not number.match(text):
                lines.setdefault(round(y), []).append((x, text))
        figures: dict[int, list[tuple[float, str]]] = {}
        for y, x, text in words:
            if x >= 150 and number.match(text):
                figures.setdefault(round(y), []).append((x, text))
        for y in sorted(figures):
            tokens = [text for _, text in sorted(figures[y])]
            if len(tokens) >= 7:
                counted.append((float(y), tokens[:7]))
        owner: dict[int, int] = {}
        for line in lines:
            near = [(abs(line - y - 1.5), i) for i, (y, _) in enumerate(counted) if -4 <= line - y <= 8]
            if near:
                owner[line] = min(near)[1]
        for i, (_, tokens) in enumerate(counted):
            mine = sorted(line for line, o in owner.items() if o == i)
            name = " ".join(text for line in mine for _, text in sorted(lines[line]))
            if not name:
                raise ValueError(f"page {page}: a row of counts {tokens} has no name")
            out.append(
                BoxRow(
                    page,
                    re.sub(r"\s+", " ", name).strip(),
                    dict(zip(COUNTS, (integer(t) for t in tokens), strict=True)),
                )
            )
    return out


def city_rows(text: list[str], start: int) -> list[tuple[str, dict[str, int]]]:
    """The rows of the tables of votes abroad by country and city, in print order, with each table's total row.

    A country's name that wraps over two lines is joined, and the row of Copenhagen, which the text layer scrambles with
    the page footer, takes the counts of Denmark, whose only city it is.
    """
    out: list[tuple[str, dict[str, int]]] = []
    for line in text[start:]:
        match = ROW.match(line)
        if match:
            name = re.sub(r"\s+", " ", match.group("name")).strip()
            if name == "República de":
                name = "República de Corea del Sur"
            out.append((name, counts(match.group("n"))))
        elif re.match(r"\s*Copenhage\s", line):
            out.append(("Copenhage", out[-1][1]))
    return out


def countries(
    rows: list[tuple[str, dict[str, int]]],
) -> list[tuple[str, dict[str, int], list[tuple[str, dict[str, int]]]]]:
    """Groups the rows of each continent's table into countries and their cities.

    Each table opens with its total row. A country row comes next; the cities that follow it add up to it, which is
    where its group ends. Every table's countries add up to its total row.
    """
    out = []
    tables: list[list[tuple[str, dict[str, int]]]] = []
    for name, c in rows:
        if name == "Total":
            tables.append([])
        tables[-1].append((name, c))
    for table in tables:
        total, block = table[0][1], table[1:]
        have = dict.fromkeys(total, 0)
        i = 0
        while i < len(block):
            country, want = block[i]
            seen = dict.fromkeys(want, 0)
            j = i + 1
            while seen != want:
                if j >= len(block):
                    raise ValueError(f"the cities of {country} do not add up to {want}")
                seen = {k: seen[k] + block[j][1][k] for k in seen}
                j += 1
            out.append((country, want, block[i + 1 : j]))
            have = {k: have[k] + want[k] for k in have}
            i = j
        if have != total:
            raise ValueError(f"the countries add up to {have}, not the table's {total}")
    return out
