"""Reads the tables of the National Electoral Council's 2002-2007 electoral atlas from its text layer.

The atlas prints the presidential and Andean Parliament results nationally, and the provincial councillor and prefect
results by province, each table beside a map. A table's rows are found by their share of the vote, and a name or an
abbreviation that wraps over several lines belongs to the row whose figures sit closest to it.
"""

import functools
import re
import subprocess
from typing import NamedTuple

from ea.sources.ec import book2017, cne

ATLAS = f"{cne.BASE}/2022/05/ATLAS-ELECTORAL-DEL-ECUADOR-2002-2007.pdf"
NUM = re.compile(r"^\d{1,3}(?:\.\d{3})*$")
PCT = re.compile(r"^\d{1,3},\d%$")
LIST = re.compile(r"^\d{1,3}(?:-\d{1,3})*-?$")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PROVINCES = (
    "Azuay",
    "Bolívar",
    "Cañar",
    "Carchi",
    "Chimborazo",
    "Cotopaxi",
    "El Oro",
    "Esmeraldas",
    "Galápagos",
    "Guayas",
    "Imbabura",
    "Loja",
    "Los Ríos",
    "Manabí",
    "Morona Santiago",
    "Napo",
    "Orellana",
    "Pastaza",
    "Pichincha",
    "Sucumbíos",
    "Tungurahua",
    "Zamora Chinchipe",
)


class Word(NamedTuple):
    x0: float
    x1: float
    y: float
    text: str


class Row(NamedTuple):
    number: str
    sigla: str
    name: str
    votes: int
    seats: int | None


class Table(NamedTuple):
    page: int
    province: str
    rows: list[Row]
    valid: int
    blank: int
    null: int
    seats: int | None


def number(text: str) -> int:
    return int(text.replace(".", ""))


@functools.cache
def pages() -> list[list[Word]]:
    path = cne.artifact(ATLAS).path
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    result: list[list[Word]] = []
    for page in xml.split("<page ")[1:]:
        found = [
            Word(float(x0), float(x1), (float(y0) + float(y1)) / 2, book2017.unescape(text))
            for x0, y0, x1, y1, text in WORD.findall(page)
        ]
        result.append(found)
    return result


@functools.cache
def text(page: int) -> str:
    path = cne.artifact(ATLAS).path
    return subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _near(words: list[Word], y: float, x0: float, x1: float, reach: float = 2.0) -> list[Word]:
    return sorted((w for w in words if abs(w.y - y) <= reach and x0 <= w.x0 and w.x1 <= x1), key=lambda w: w.x0)


def _first(words: list[Word], text: str, after: float = 0.0) -> Word | None:
    found = [w for w in words if w.text == text and w.y > after]
    return min(found, key=lambda w: (w.y, w.x0)) if found else None


def _joined(words: list[Word]) -> str:
    """Words joined with spaces, except after a hyphen or slash that the line broke on."""
    result = ""
    for w in words:
        broken = result.endswith(("-", "/")) and not result.endswith(" -")
        result += ("" if not result or broken else " ") + w.text
    return result


def province_tables(page: int, candidates: bool) -> list[Table]:
    """The province tables on one page: a heading, a header row, rows of lists, and the totals under them."""
    words = pages()[page - 1]
    tables: list[Table] = []
    headers = sorted((w for w in words if w.text == "Lista"), key=lambda w: w.y)
    for lista in headers:
        row_words = [w for w in words if abs(w.y - lista.y) < 2.5]
        siglas = min((w for w in row_words if w.text.lower() == "siglas" and w.x0 > lista.x1), key=lambda w: w.x0)
        num = min((w for w in row_words if w.text == "Número" and w.x0 > siglas.x1), key=lambda w: w.x0)
        pct = min((w for w in row_words if w.text == "Porcentaje" and w.x0 > num.x1), key=lambda w: w.x0)
        left, right = lista.x0 - 20, pct.x1 + 70
        escanos = [w for w in words if w.text == "Escaños" and abs(w.y - lista.y) < 12 and pct.x1 < w.x0 < right]
        total = min(
            (w for w in words if w.text == "Total" and w.y > lista.y and left <= w.x0 < siglas.x0),
            key=lambda w: w.y,
        )
        # the province's name sits above the header, within the table's columns
        organizacion = max(
            (w for w in words if w.text == "Organización" and lista.y - 40 < w.y < lista.y and left <= w.x0 < right),
            key=lambda w: w.y,
        )
        above = sorted(
            (w for w in words if organizacion.y - 45 < w.y < organizacion.y - 3 and left <= w.x0 and w.x1 <= right),
            key=lambda w: (round(w.y), w.x0),
        )
        heading = " ".join(w.text for w in above)
        province = next((p for p in sorted(PROVINCES, key=len, reverse=True) if p in heading), None)
        if province is None:
            raise ValueError(f"page {page}: no province above the table at {lista.y:.0f} ({heading!r})")
        inside = [w for w in words if lista.y + 3 < w.y < total.y - 3 and left <= w.x0 and w.x1 <= right]
        anchors = sorted(
            (w for w in inside if PCT.match(w.text) and w.x0 > num.x0 and w.x1 <= pct.x1 + 5), key=lambda w: w.y
        )
        if candidates:
            starts = [w.x0 for w in inside if siglas.x1 < w.x0 < num.x0 - 15 and not NUM.match(w.text)]
            cut = min(starts)
        else:
            cut = num.x0 - 15
        parts: list[dict[str, list[Word]]] = [{"list": [], "sigla": [], "name": []} for _ in anchors]
        votes: list[int] = []
        seats: list[int | None] = []
        for anchor in anchors:
            same = _near(inside, anchor.y, num.x0 - 30, anchor.x0)
            figures = [w for w in same if NUM.match(w.text)]
            if len(figures) != 1:
                raise ValueError(f"page {page}: no votes beside {anchor.text} at {anchor.y:.0f}")
            votes.append(number(figures[0].text))
            if escanos:
                seat = _near(inside, anchor.y, anchor.x1 + 1, right)
                if len(seat) != 1 or not seat[0].text.isdigit():
                    raise ValueError(f"page {page}: no seats beside {anchor.text} at {anchor.y:.0f}")
                seats.append(int(seat[0].text))
            else:
                seats.append(None)
        for w in inside:
            if w.x0 >= num.x0 - 15 or not anchors:
                continue
            i = min(range(len(anchors)), key=lambda i: abs(anchors[i].y - w.y))
            if abs(anchors[i].y - w.y) > 14:
                raise ValueError(f"page {page}: {w.text!r} at {w.y:.0f} belongs to no row")
            if w.x0 < siglas.x0 - 5 and LIST.match(w.text):
                parts[i]["list"].append(w)
            elif w.x0 < cut:
                parts[i]["sigla"].append(w)
            else:
                parts[i]["name"].append(w)
        rows: list[Row] = []
        for part, vote, seat in zip(parts, votes, seats, strict=True):
            order = sorted(part["list"], key=lambda w: (w.y, w.x0))
            listed = "".join(w.text for w in order)
            if not order or not LIST.match(listed) or listed.endswith("-"):
                raise ValueError(f"page {page}: a row in {province} has list numbers {[w.text for w in order]}")
            sigla = _joined(sorted(part["sigla"], key=lambda w: (round(w.y), w.x0)))
            name = " ".join(w.text for w in sorted(part["name"], key=lambda w: (round(w.y), w.x0)))
            rows.append(Row(listed, re.sub(r"\s*/\s*", "/", sigla).replace("/ ", "/"), name, vote, seat))

        def figure(y: float) -> int:
            found = [w for w in _near(words, y, num.x0 - 30, pct.x0 + 5) if NUM.match(w.text)]
            if len(found) != 1:
                raise ValueError(f"page {page}: no total at {y:.0f}")
            return number(found[0].text)

        blank = min(
            (w for w in words if w.text == "blanco" and w.y > total.y and left <= w.x0 < num.x0), key=lambda w: w.y
        )
        null = min(
            (w for w in words if w.text == "nulos" and w.y > total.y and left <= w.x0 < num.x0), key=lambda w: w.y
        )
        total_seats = None
        if escanos:
            found = [w for w in _near(words, total.y, pct.x1 + 1, right) if w.text.isdigit()]
            total_seats = int(found[0].text) if found else None
        tables.append(Table(page, province, rows, figure(total.y), figure(blank.y), figure(null.y), total_seats))
    return tables


FIGURE = r"\d{1,3}(?:\.\d{3})*"
TICKET = re.compile(rf"^\s*(\d{{1,3}}(?:-\d{{1,3}})*)\s+(\S+)\s+(\S.*?\S)\s{{2,}}({FIGURE})\s+\d{{1,3}},\d%\s*$")
LISTED = re.compile(rf"^\s*(\d{{1,3}}(?:-\d{{1,3}})*)\s+(\S(?:.*?\S)?)\s{{2,}}({FIGURE})\s+\d{{1,3}},\d%\s+(\d+)\s*$")
ELECTED = re.compile(r"^\s*(\S+)\s{2,}(\S.*?\S)\s{2,}(\d{1,3}(?:-\d{1,3})*)\s{2,}(\S.*?\S)\s{2,}(HOMBRES|MUJERES)\s*$")
TOTALS = {"valid": "Total votos válidos", "blank": "Votos en blanco", "null": "Votos nulos"}


class National(NamedTuple):
    rows: list[Row]
    valid: int
    blank: int
    null: int
    seats: int | None
    elected: list[tuple[str, str, str]]  # list number, name, and sex of each member elected


def _totals(lines: list[str]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {name: [] for name in TOTALS}
    for line in lines:
        for name, label in TOTALS.items():
            match = re.match(rf"^\s*{label}\s+({FIGURE})\b", line)
            if match:
                found[name].append(number(match[1]))
    return found


def president(page: int) -> list[National]:
    """The first round and runoff tables of one presidential election, as one page prints them."""
    lines = text(page).splitlines()
    rounds: list[list[Row]] = [[]]
    for line in lines:
        match = TICKET.match(line)
        if match:
            rounds[-1].append(Row(match[1], match[2], match[3], number(match[4]), None))
        elif line.strip().startswith(TOTALS["valid"]):
            rounds.append([])
    totals = _totals(lines)
    if len(rounds) != 3 or rounds[2] or any(len(v) != 2 for v in totals.values()):
        raise ValueError(f"page {page}: expected two presidential tables")
    return [National(rounds[i], totals["valid"][i], totals["blank"][i], totals["null"][i], None, []) for i in range(2)]


def andean(page: int) -> National:
    """The national Andean Parliament table and its members elected."""
    lines = text(page).splitlines()
    rows = [
        Row(m[1], m[2], "", number(m[3]), int(m[4])) for m in (LISTED.match(line) for line in lines) if m is not None
    ]
    elected = [(m[3], m[4], m[5]) for m in (ELECTED.match(line) for line in lines) if m is not None]
    totals = _totals(lines)
    seats = next(
        int(m[1]) for m in (re.match(rf"^\s*{TOTALS['valid']}\s+{FIGURE}\s+\S+\s+(\d+)", x) for x in lines) if m
    )
    return National(rows, totals["valid"][0], totals["blank"][0], totals["null"][0], seats, elected)


SUMMARY = {2002: 20, 2004: 22, 2006: 24}


def summary(year: int) -> dict[str, int]:
    """Each province's registered voters, the national total, and the voters who took part, from a year's summary."""
    page = text(SUMMARY[year])
    found: dict[str, int] = {}
    for province in PROVINCES:
        match = re.search(rf"\b{province}\s+({FIGURE})\s+\d{{1,3}},\d%", page)
        if match is None:
            raise ValueError(f"no registered voters for {province} in {year}")
        found[province] = number(match[1])
    for label, name in (
        ("Electores", "registered"),
        ("Sufragantes", "cast"),
        ("Sufragantes primera vuelta", "cast"),
        ("Sufragantes segunda vuelta", "cast2"),
    ):
        match = re.search(rf"^\s*{label}\s+({FIGURE})\b", page, re.MULTILINE)
        if match:
            found[name] = number(match[1])
    return found
