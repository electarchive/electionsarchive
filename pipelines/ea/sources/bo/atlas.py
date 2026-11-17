"""Reads the result tables of the Atlas Electoral de Bolivia, volume II, from the word boxes of its PDF text layer.

Each table row holds the counts of one municipality (or one locality) in a fixed order of columns. The names of
provinces, municipalities, and localities wrap over several lines and sit above or below the line of numbers, so a
word of text goes to the row of numbers nearest to it, in the column whose header starts to its left.
"""

import html
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

NUMBER = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$|^\d+$")
PERCENT = re.compile(r"^\d+(?:,\d+)?%$")
TITLE = re.compile(r"Resultados? por (municipio|departamento|localidad|provincia)")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
HEADERS = {
    "sí": "yes",
    "si": "yes",
    "no": "no",
    "válidos": "valid",
    "validos": "valid",
    "blancos": "blank",
    "blanco": "blank",
    "nulos": "null",
    "nulo": "null",
    "insritos": "registered",
    "emitidos": "cast",
    "inscritos": "registered",
    "participación": "turnout",
    "particip.": "turnout",
    "abstención": "abstention",
}
COUNTS = ("yes", "no", "valid", "blank", "null", "cast", "registered")


class Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2


class Row(NamedTuple):
    page: int
    department: str
    heading: str
    names: tuple[str, ...]  # province, municipality, locality as far as the table has them
    counts: dict[str, int]
    level: str  # department, municipality, or locality; a row that totals its table is level total


def pages(pdf: Path) -> list[list[Word]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", str(pdf), "-"], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return [
        [Word(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(page)]
        for page in out.split("<page ")[1:]
    ]


def lines(words: list[Word]) -> list[list[Word]]:
    found: list[list[Word]] = []
    top = -99.0
    for word in sorted(words, key=lambda w: (round(w.y0), w.x0)):
        if not found or abs(word.y0 - top) > 2:
            found.append([])
            top = word.y0
        found[-1].append(word)
    return [sorted(line, key=lambda w: w.x0) for line in found]


def integer(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def table(page_number: int, words: list[Word]) -> list[Row]:
    """The rows of the one results table on a page, or nothing for a page without one."""
    page_lines = lines(words)
    title = next((line for line in page_lines if TITLE.search(" ".join(w.text for w in line))), None)
    if title is None:  # a table headed only by the name of its referendum
        title = next((line for line in page_lines if any(w.text == "Referéndum" for w in line)), None)
    if title is None:
        return []
    heading = clean(" ".join(w.text for w in title))
    department = heading.split(". ")[0] if ". Referéndum" in heading else ""
    header = next(
        (
            line
            for line in page_lines
            if line[0].y0 > title[0].y0
            and sum(w.text.lower() in HEADERS for w in line) >= 5
            and any(w.text.lower() in {"municipio", "departamento", "provincia"} for w in line)
        ),
        None,
    )
    if header is None:
        return []
    if not TITLE.search(heading):
        departments = not any(w.text.lower() in {"provincia", "municipio", "sección"} for w in header)
        heading += ". Resultados por " + ("departamento" if departments else "municipio")
    order = [HEADERS[w.text.lower()] for w in header if w.text.lower() in HEADERS]
    first_count = min(w.x0 for w in header if w.text.lower() in HEADERS) - 35
    if "yes" not in order:  # the dirimidor tables head their first two columns with the areas, not Sí and No
        order = ["yes", "no", *order]
        first_count -= 100
    labels = [w for w in header if w.text.lower() in {"provincia", "municipio", "localidad", "departamento", "sección"}]
    columns = [w.x0 - 20 for w in labels]
    if len(labels) > 1:  # names of provinces start at the first header; those of municipalities a little to the right
        columns[1] = labels[0].x0 + 30
    if len(labels) > 2:
        columns[2] = labels[2].x0 - 12
    localities = len(labels) > 2 and labels[2].text.lower() == "localidad"
    percents = max(w.x1 for w in header if HEADERS.get(w.text.lower()) == "registered") + 8
    numeric = [
        w
        for w in words
        if w.y0 > header[0].y1 and first_count <= w.x0 < percents and (NUMBER.match(w.text) or PERCENT.match(w.text))
    ]
    groups: list[list[Word]] = []
    for word in sorted(numeric, key=lambda w: w.y):
        if groups and abs(groups[-1][0].y - word.y) <= 2.5:
            groups[-1].append(word)
        else:
            groups.append([word])
    rows = [sorted(group, key=lambda w: w.x0) for group in groups if len(group) > 3]
    rows.sort(key=lambda r: r[0].y)
    if not rows:
        return []
    y_end = max(w.y1 for r in rows for w in r) + 14
    names: list[list[list[Word]]] = [[[] for _ in columns] for _ in rows]
    for word in words:
        if word.y0 <= header[0].y1 or word.y0 > y_end or word.x1 > first_count + 15 or NUMBER.match(word.text):
            continue
        if word.x0 < columns[0]:
            continue
        column = max(i for i, x in enumerate(columns) if word.x0 >= x)
        nearest = min(range(len(rows)), key=lambda i: abs(rows[i][0].y - word.y))
        if abs(rows[nearest][0].y - word.y) > 22:
            continue
        names[nearest][column].append(word)
    if localities:
        names = grouped(rows, names)
    found = []
    for row, cells in zip(rows, names, strict=True):
        values = [integer(w.text) for w in row if NUMBER.match(w.text)]
        if len(values) != len(order) - 2:
            raise ValueError(f"page {page_number}: {len(values)} counts in a row of {order}")
        counts = dict(zip([name for name in order if name not in {"turnout", "abstention"}], values, strict=True))
        texts = tuple(clean(" ".join(w.text for line in lines(cell) for w in line)) for cell in cells)
        label = " ".join(t for t in texts if t)
        if label.isupper() and len(label) > 2 or label in {"Bolivia", "TOTAL"}:
            level = "total"
        else:
            level = (
                "department"
                if "por departamento" in heading
                else "locality"
                if "localidad" in heading
                else "municipality"
            )
        found.append(Row(page_number, department, heading, texts, counts, level))
    return found


def grouped(rows: list[list[Word]], names: list[list[list[Word]]]) -> list[list[list[Word]]]:
    """Spreads the labels of the outer columns of a three-column table over the rows they head.

    A province or a municipality is printed once, level with the middle of the rows it covers, so each label owns the
    rows from the first one not yet owned to the row as far below the label as the group's first row lies above it.
    """
    y = [row[0].y for row in rows]
    spread = [[[] for _ in cells] for cells in names]
    for column in range(len(names[0]) - 1):
        labels = []
        for i, cells in enumerate(names):
            for line in lines(cells[column]):
                labels.append((line[0].y, line))
        labels.sort(key=lambda item: item[0])
        start = 0
        for n, (at, line) in enumerate(labels):
            if start >= len(y):
                break
            reach = 2 * at - y[start]
            end = start
            while end + 1 < len(y) and y[end + 1] <= reach + 3:
                end += 1
            if n == len(labels) - 1:
                end = len(y) - 1
            for i in range(start, end + 1):
                spread[i][column] = [*spread[i][column], *line]
            start = end + 1
    for i, cells in enumerate(names):
        spread[i][-1] = cells[-1]
    return spread
