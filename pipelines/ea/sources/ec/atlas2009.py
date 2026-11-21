"""Reads the 2014 mayor tables of the National Electoral Council's 2009-2014 electoral atlas from its page images.

The atlas has no text layer: each page is one image at 150 dpi. For each canton, its table of mayors elected in 2014
prints the canton, the winning organization, the mayor elected, the winner's votes, and the winner's share of the
valid votes, in a ruled table with one line a row. The pages are rendered at 300 dpi, the table's rules are found as
long runs of dark pixels, and each cell is read on its own with Tesseract. Numbers are read several ways and the
reading most passes agree on is kept; every reading was compared by eye with the printed page, and the cells Tesseract
misreads are corrected in ``atlas2009_mayors.json`` with the page's own reading.
"""

import collections
import csv
import functools
import io
import itertools
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

from ea import store
from ea.sources.ec import cne

ATLAS = f"{cne.BASE}/2022/05/CNE_ATLAS-ELECTORAL-DEL-ECUADOR-2009-2014.pdf"
# the page of each province's 2014 mayor table; the table's own page names no province, the 2009 page before it does
MAYOR_PAGES = {
    194: "Azuay",
    196: "Bolívar",
    198: "Cañar",
    200: "Carchi",
    202: "Chimborazo",
    204: "Cotopaxi",
    206: "El Oro",
    208: "Esmeraldas",
    210: "Galápagos",
    213: "Guayas",
    216: "Imbabura",
    218: "Loja",
    220: "Los Ríos",
    222: "Manabí",
    224: "Morona Santiago",
    226: "Napo",
    228: "Orellana",
    230: "Pastaza",
    232: "Pichincha",
    234: "Santa Elena",
    236: "Santo Domingo de los Tsáchilas",
    238: "Sucumbíos",
    240: "Tungurahua",
    242: "Zamora Chinchipe",
}
# the page of each province's 2009 mayor table, the page before its 2014 table (Guayas's table is two pages ahead)
MAYOR_PAGES_2009 = {page - (2 if page == 213 else 1): province for page, province in MAYOR_PAGES.items()}
DPI = 300
IMAGE_DPI = 150  # the page images stored for the site, at the atlas's own resolution
ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}
WINDOW = 600  # the shortest run of dark pixels that counts as a rule
CORRECTIONS = Path(__file__).with_name("atlas2009_mayors.json")
# the 2009 tables, whose every cell was read by eye from the page and entered here with the Council's canton code
TABLES_2009 = Path(__file__).with_name("atlas2009_mayors_2009.json")

type Box = tuple[int, int, int, int]


class Mayor(NamedTuple):
    page: int
    province: str
    code: str  # the Council's canton code
    canton: str
    sigla: str
    name: str
    votes: int
    share: str
    box: tuple[float, float, float, float]  # the votes cell, as fractions of the page's width and height


@functools.cache
def render(page: int) -> Image.Image:
    path = cne.artifact(ATLAS).path
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", path, out],
            check=True,
        )
        with Image.open(out.with_suffix(".png")) as image:
            return image.convert("L")


def image_key(page: int) -> str:
    return f"pages/{cne.SOURCE}/{cne.artifact(ATLAS).sha256}-{page}.png"


def page_image(page: int) -> tuple[int, int]:
    """Writes the page's image for the site, at the atlas's own resolution, and returns its width and height."""
    target = store.DATA / image_key(page)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "page"
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    str(IMAGE_DPI),
                    "-png",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    cne.artifact(ATLAS).path,
                    out,
                ],
                check=True,
            )
            out.with_suffix(".png").replace(target)
    with Image.open(target) as image:
        return image.width, image.height


def _runs(values: np.ndarray, gap: int = 4) -> list[tuple[int, int]]:
    runs: list[list[int]] = []
    for v in values:
        if runs and v - runs[-1][-1] <= gap:
            runs[-1].append(int(v))
        else:
            runs.append([int(v)])
    return [(r[0], r[-1]) for r in runs]


class Table(NamedTuple):
    rows: list[tuple[int, int]]  # each row's top and bottom
    columns: list[tuple[int, int]]  # each column's left and right
    header: tuple[int, int]


def _segments(row: np.ndarray) -> list[tuple[int, int]]:
    """The runs of a row of flags, as first and last index."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def tables(image: Image.Image, header: int = 60) -> list[Table]:
    """The ruled five-column tables on a page.

    A rule is a long run of dark pixels; the rules that share a table's left and right edges are its rules. Within
    those edges, the header is the first tall dark block, the rows are the light bands between the rules under it, and
    the columns are split by the vertical rules that cross every row.
    """
    dark = np.asarray(image) < 140
    total = np.cumsum(dark, axis=1, dtype=np.int32)
    full = (total[:, WINDOW:] - total[:, :-WINDOW]) >= WINDOW - 3
    spans: list[tuple[int, int, int]] = []
    for y in np.flatnonzero(full.any(axis=1)):
        spans += [(int(y), a, b + WINDOW) for a, b in _segments(full[y])]
    clusters: list[list[tuple[int, int, int]]] = []
    for span in sorted(spans, key=lambda s: (s[1], s[2])):
        for cluster in clusters:
            if abs(cluster[0][1] - span[1]) < 12 and abs(cluster[0][2] - span[2]) < 12:
                cluster.append(span)
                break
        else:
            clusters.append([span])
    found: list[Table] = []
    for cluster in clusters:
        rules = _runs(np.array(sorted({y for y, _, _ in cluster})))
        if len(rules) < 3:
            continue
        x0, x1 = cluster[0][1], cluster[0][2]
        shade = dark[:, x0 + 10 : x1 - 10].mean(axis=1)
        # the header: the tall dark block that the first rules belong to
        top, bottom = rules[0]
        while top > 0 and shade[top - 1] > 0.5:
            top -= 1
        while bottom + 1 < len(shade) and (shade[bottom + 1] > 0.5 or shade[bottom + 1 : bottom + 12].mean() > 0.5):
            bottom += 1
        if bottom - top < header:
            continue
        lines = [r for r in rules if r[0] > bottom]
        rows: list[tuple[int, int]] = []
        start = bottom + 1
        for a, b in lines:
            if not 25 < a - start < 140:
                break
            rows.append((start, a - 1))
            start = b + 1
        if not rows:
            continue
        body = dark[rows[0][0] + 4 : rows[-1][1] - 4, x0 - 6 : x1 + 6].mean(axis=0)
        verticals = [(a + x0 - 6, b + x0 - 6) for a, b in _runs(np.flatnonzero(body > 0.9))]
        if len(verticals) != 6:
            continue
        columns = [(a[1] + 1, b[0] - 1) for a, b in itertools.pairwise(verticals)]
        found.append(Table(rows, columns, (top, bottom)))
    return sorted(found, key=lambda t: t.header[0])


def _tesseract(crop: Image.Image, psm: str, language: str, whitelist: str | None) -> str:
    canvas = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    canvas.paste(crop, (20, 20))
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    config = ["-c", f"tessedit_char_whitelist={whitelist}"] if whitelist else []
    done = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", psm, "-l", language, *config, "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
        env=ENV,
    )
    words = [
        r["text"].strip()
        for r in csv.DictReader(io.StringIO(done.stdout.decode()), delimiter="\t", quoting=csv.QUOTE_NONE)
        if r["level"] == "5" and r["text"].strip()
    ]
    return " ".join(words)


def _crop(image: Image.Image, box: Box, scale: float, binary: bool) -> Image.Image:
    crop = image.crop((box[0] + 4, box[1] + 3, box[2] - 4, box[3] - 2))
    if binary:
        crop = crop.point(lambda v: 0 if v < 140 else 255)
    if scale != 1:
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
    return crop


NUMBER_READS = ((1.0, "7", False), (1.0, "7", True), (1.5, "7", False), (2.0, "7", True), (0.75, "7", False))


def _number(image: Image.Image, box: Box, whitelist: str, pattern: str) -> tuple[str, list[str]]:
    """The reading most passes agree on, among those shaped like the column's figures, and every pass's reading."""
    reads = [
        _tesseract(_crop(image, box, scale, binary), psm, "eng", whitelist).replace(" ", "")
        for scale, psm, binary in NUMBER_READS
    ]
    shaped = [r for r in reads if re.fullmatch(pattern, r)]
    best = collections.Counter(shaped).most_common(1)
    return (best[0][0] if best else ""), reads


def _text(image: Image.Image, box: Box, scale: float = 1.5, binary: bool = False, language: str = "spa") -> str:
    """A cell's words, over one line or two, read from the cell's full height so descenders stay whole, without the
    stray marks the cell's rules leave at either end."""
    tall = box[3] - box[1] > 70
    crop = image.crop((box[0] + 4, box[1], box[2] - 4, box[3] + 1))
    if binary:
        crop = crop.point(lambda v: 0 if v < 150 else 255)
    crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
    return EDGES.sub("", _tesseract(crop, "6" if tall else "7", language, None))


SIGLA_READS = ((2.0, True, "eng"), (2.0, False, "eng"), (1.5, False, "spa"), (1.0, False, "eng"))


def _sigla(image: Image.Image, box: Box) -> str:
    """An organization's abbreviation: the reading most passes agree on, in capitals, slashes, and hyphens."""
    reads = [re.sub(r"[^A-Z/-]", "", _text(image, box, *read).upper()).strip("/-") for read in SIGLA_READS]
    best = collections.Counter(r for r in reads if r).most_common()
    return max(best, key=lambda b: b[1])[0] if best else ""


EDGES = re.compile(r"^[\W_]+|[\W_]+$|^[a-z]{1,2} (?=[A-ZÁÉÍÓÚÑ])")
VOTES = r"\d{1,3}(?:\.\d{3})*"
SHARE = r"\d{1,3},\d%"


class Read(NamedTuple):
    row: int
    cells: tuple[str, str, str, str, str]  # canton, organization, mayor, votes, and share
    reads: tuple[list[str], list[str]]  # every pass's reading of the votes and the share
    boxes: list[Box]  # each cell's box on the page rendered at 300 dpi


def _year(image: Image.Image, table: Table) -> int:
    x0 = table.columns[3][0]
    header = image.crop((x0, table.header[0], table.columns[4][1], table.header[1]))
    inverted = header.point(lambda v: 255 if v < 150 else 0)
    text = _tesseract(inverted, "6", "spa", None)
    years = re.findall(r"20[01]\d", text)
    return int(years[0]) if years else 0


@functools.cache
def reads(page: int) -> list[Read]:
    """Each row of the page's 2014 mayor table as Tesseract reads it."""
    image = render(page)
    found: list[Read] = []
    for table in tables(image):
        year = _year(image, table)
        if year != 2014:
            raise ValueError(f"page {page}: a mayor table for {year}")
        boxes = [[(left, top, right, bottom) for left, right in table.columns] for top, bottom in table.rows]

        def one(i: int) -> Read:
            row = boxes[i]
            votes, vote_reads = _number(image, row[3], "0123456789.", VOTES)
            share, share_reads = _number(image, row[4], "0123456789,%", SHARE)
            cells = (_text(image, row[0]), _sigla(image, row[1]), _text(image, row[2]), votes, share)
            return Read(i + 1, cells, (vote_reads, share_reads), row)

        with ThreadPoolExecutor(os.cpu_count() or 2) as pool:
            found.extend(pool.map(one, range(len(boxes))))
    return found


FIELDS = ("canton", "sigla", "name", "votes", "share")


def mayors2009() -> list[Mayor]:
    """The elected mayor of each canton in 2009, with the winner's votes and share of the valid votes.

    The ruled tables are found on each page as for 2014, and ``atlas2009_mayors_2009.json`` holds for each row, by page
    and row number, the Council's code for its canton and every cell as read by eye from the page. The rows found on a
    page must be exactly those entered, and every figure must be shaped as the column prints it.
    """
    checked: dict[str, dict[str, object]] = json.loads(TABLES_2009.read_text(encoding="utf-8"))
    result: list[Mayor] = []
    for page, province in MAYOR_PAGES_2009.items():
        image = render(page)
        found = tables(image)
        if len(found) != 1:
            raise ValueError(f"page {page}: {len(found)} mayor tables")
        table = found[0]
        for i, (top, bottom) in enumerate(table.rows, 1):
            key = f"{page}/{i}"
            entry = checked.pop(key)
            cells = entry["read"]
            if not isinstance(cells, dict) or set(cells) != set(FIELDS):
                raise TypeError(f"{key}: cells are not the five fields")
            if not re.fullmatch(VOTES, cells["votes"]) or not re.fullmatch(SHARE, cells["share"]):
                raise ValueError(f"mayor row {key} reads {cells}")
            left, right = table.columns[3]
            box = (left / image.width, top / image.height, right / image.width, bottom / image.height)
            result.append(
                Mayor(
                    page,
                    province,
                    str(entry["code"]),
                    cells["canton"],
                    cells["sigla"],
                    cells["name"],
                    int(cells["votes"].replace(".", "")),
                    cells["share"],
                    box,
                )
            )
    if checked:
        raise ValueError(f"checked rows the tables do not have: {sorted(checked)}")
    return result


def mayors() -> list[Mayor]:
    """The elected mayor of each canton in 2014, with the winner's votes and share of the valid votes.

    ``atlas2009_mayors.json`` holds, for each table row by page and row number, the Council's code for its canton and
    the page's own reading of any cell Tesseract misreads, as checked by eye against the page.
    """
    checked: dict[str, dict[str, object]] = json.loads(CORRECTIONS.read_text(encoding="utf-8"))
    result: list[Mayor] = []
    for page, province in MAYOR_PAGES.items():
        image = render(page)
        for read in reads(page):
            key = f"{page}/{read.row}"
            entry = checked.pop(key)
            fixes = entry.get("read", {})
            if not isinstance(fixes, dict):
                raise TypeError(f"{key}: corrections are not a mapping")
            cells = {**dict(zip(FIELDS, read.cells, strict=True)), **fixes}
            if not re.fullmatch(VOTES, cells["votes"]) or not re.fullmatch(SHARE, cells["share"]):
                raise ValueError(f"mayor row {key} reads {cells}")
            if not all(cells.values()):
                raise ValueError(f"mayor row {key} has an empty cell: {cells}")
            left, top, right, bottom = read.boxes[3]
            box = (left / image.width, top / image.height, right / image.width, bottom / image.height)
            result.append(
                Mayor(
                    page,
                    province,
                    str(entry["code"]),
                    cells["canton"],
                    cells["sigla"],
                    cells["name"],
                    int(cells["votes"].replace(".", "")),
                    cells["share"],
                    box,
                )
            )
    if checked:
        raise ValueError(f"checked rows the tables do not have: {sorted(checked)}")
    return result


# a list table the rules do not find, by page: the top of its header; it shares the columns of the table above it
UNRULED = {141: 1930}


def _lines(image: Image.Image, top: int, column: tuple[int, int]) -> list[tuple[int, int]]:
    """The text lines of a list table's votes column, between its header and its shaded total row.

    The header is a mid-gray block, each list's votes are one line of dark figures on a light row, and the total row
    is a block darker still, so each row's figures stand alone in the column's profile of dark pixels.
    """
    cell = np.asarray(image)[:, column[0] + 6 : column[1] - 6]
    dark = (cell < 110).mean(axis=1)
    light = cell.mean(axis=1) > 185
    y = top
    while not light[y : y + 4].all():
        y += 1
    total = next(r for r in _segments(dark > 0.5) if r[1] - r[0] >= 12 and r[0] > y)
    lines: list[tuple[int, int]] = []
    for a, b in _segments(dark > 0.01):
        if a < y or b >= total[0]:
            continue
        if lines and a - lines[-1][1] <= 6:
            lines[-1] = (lines[-1][0], b)
        else:
            lines.append((a, b))
    return [(a, b) for a, b in lines if b - a >= 8 and dark[a : b + 1].mean() < 0.45]


@functools.cache
def list_boxes(page: int) -> list[list[tuple[float, float, float, float]]]:
    """The votes cell of each row of each list table on the page, top to bottom, as fractions of the page."""
    image = render(page)
    found = [(t.header[0], t.columns[2]) for t in sorted(tables(image, 20), key=lambda t: t.header[0])]
    if page in UNRULED:
        found.append((UNRULED[page], found[-1][1]))
    boxes = []
    for top, (x0, x1) in found:
        boxes.append(
            [
                (x0 / image.width, (a - 10) / image.height, x1 / image.width, (b + 10) / image.height)
                for a, b in _lines(image, top, (x0, x1))
            ]
        )
    return boxes


# the page of each province's 2014 prefect table, by the Council's province code; each follows its 2009 table, and the
# table's own page names no province, so the 2009 page before it does
PREFECT_PAGES = {
    144: "1",
    146: "2",
    148: "3",
    150: "4",
    152: "6",
    154: "5",
    156: "7",
    158: "8",
    160: "9",
    162: "10",
    164: "11",
    166: "12",
    168: "13",
    170: "14",
    172: "15",
    174: "22",
    176: "16",
    178: "17",
    180: "24",
    182: "23",
    184: "21",
    186: "18",
    188: "19",
}
# each province's 2009 prefect table is on the page before its 2014 table
PREFECT_PAGES_2009 = {page - 1: province for page, province in PREFECT_PAGES.items()}
PREFECTS = Path(__file__).with_name("atlas2009_prefects.json")
PREFECTS_2009 = Path(__file__).with_name("atlas2009_prefects_2009.json")
PREFECT_HEADER = 123  # the height of a prefect table's header, in pixels at 300 dpi


class PrefectTable(NamedTuple):
    rows: list[tuple[int, int]]  # each list's top and bottom
    columns: list[tuple[int, int]]  # the list numbers, the organization, the votes, and the share
    total: tuple[int, int]  # the total row's top and bottom


def prefect_table(image: Image.Image) -> PrefectTable:
    """The shaded four-column table of a prefect page.

    Its header is the first long dark block under the page's title, the total row is the next such block after the
    header, and the columns are split by the vertical rules. Each list's votes sit on one line, so the rows are the
    lines of the votes column, and a list whose organizations wrap onto two lines is still one row.
    """
    dark = np.asarray(image) < 175
    total = np.cumsum(dark, axis=1, dtype=np.int32)
    wide = (total[:, 700:] - total[:, :-700]) >= 0.75 * 700
    ys = np.flatnonzero(wide.any(axis=1))
    blocks = _runs(ys[(ys > 600) & (ys < 3000)], gap=3)
    top = blocks[0][0]
    header = top + PREFECT_HEADER
    footer = next(b[0] for b in blocks if b[0] > header and b[1] - b[0] >= 40)
    # the table's edges are the ends of the header's solid fill, not of the wide windows that find it, which also start
    # in the margin to its left, where a page's side tab has a rule of its own
    left, right = max(_segments(dark[top + 6]), key=lambda s: s[1] - s[0])
    right += 1
    shade = (np.asarray(image)[header + 8 : footer - 8, left - 10 : right + 10] < 165).mean(axis=0)
    verticals = [(a + left - 10, b + left - 10) for a, b in _segments(shade > 0.6)][:5]
    if len(verticals) != 5:
        raise ValueError(f"{len(verticals)} vertical rules in a prefect table")
    columns = [(a[1] + 1, b[0] - 1) for a, b in itertools.pairwise(verticals)]
    cell = np.asarray(image)[:, columns[2][0] + 6 : columns[2][1] - 6]
    figures = (cell < 110).mean(axis=1)
    lines = [(a, b) for a, b in _segments(figures > 0.01) if header + 4 < a and b < footer and b - a >= 8]
    merged: list[tuple[int, int]] = []
    for a, b in lines:
        if merged and a - merged[-1][1] <= 6:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    bounds = [header] + [(x[1] + y[0]) // 2 for x, y in itertools.pairwise(merged)] + [footer - 1]
    rows = [(a + 2, b - 2) for a, b in itertools.pairwise(bounds)]
    return PrefectTable(rows, columns, (footer, footer + 63))


@functools.cache
def prefect_reads(page: int) -> tuple[list[Read], Read]:
    """Each list row of the page's 2014 prefect table as Tesseract reads it, and the total row."""
    image = render(page)
    table = prefect_table(image)
    boxes = [[(left, top, right, bottom) for left, right in table.columns] for top, bottom in table.rows]

    def one(i: int) -> Read:
        row = boxes[i]
        votes, vote_reads = _number(image, row[2], "0123456789.", VOTES)
        share, share_reads = _number(image, row[3], "0123456789,%", SHARE)
        lists = re.sub(r"[^0-9-]", "", _text(image, row[0], binary=True, language="eng"))
        cells = (lists, _text(image, row[1], binary=True, language="eng"), "", votes, share)
        return Read(i + 1, cells, (vote_reads, share_reads), row)

    with ThreadPoolExecutor(os.cpu_count() or 2) as pool:
        found = list(pool.map(one, range(len(boxes))))
    top, bottom = table.total
    cols = [(left, top, right, bottom) for left, right in table.columns]
    votes, vote_reads = _number(image, cols[2], "0123456789.", VOTES)
    share, share_reads = _number(image, cols[3], "0123456789,%", SHARE)
    return found, Read(0, ("", "", "", votes, share), (vote_reads, share_reads), cols)


class Prefect(NamedTuple):
    page: int
    province: str  # the Council's province code
    total: int  # the table's printed total of valid votes
    total_box: tuple[float, float, float, float]  # its votes cell, as fractions of the page's width and height
    lists: list[tuple[str, str, int, str, tuple[float, float, float, float]]]  # number, organization, votes, share, box


def prefects(year: int = 2014) -> list[Prefect]:
    """The prefect table of each province for the year, with each list's votes cell on its page.

    ``atlas2009_prefects.json`` (2014) and ``atlas2009_prefects_2009.json`` (2009) hold, by page, every cell as read by eye from the page: the list numbers, the
    organizations, the votes, and the shares, and the printed total of valid votes. The rows found on a page must be
    exactly those entered, the votes must add up to the printed total, and each share must be the list's share of the
    total to one decimal.
    """
    source, pages = (PREFECTS_2009, PREFECT_PAGES_2009) if year == 2009 else (PREFECTS, PREFECT_PAGES)
    entries: dict[str, dict[str, object]] = json.loads(source.read_text(encoding="utf-8"))
    found: list[Prefect] = []
    for page, province in pages.items():
        entry = entries.pop(str(page))
        image = render(page)
        table = prefect_table(image)
        raw_lists = entry["lists"]
        if not isinstance(raw_lists, list):
            raise TypeError(f"page {page}: lists is not a list")
        if len(raw_lists) != len(table.rows):
            raise ValueError(f"page {page}: {len(table.rows)} rows on the page for {len(raw_lists)} lists")
        total = int(str(entry["total"]).replace(".", ""))
        votes_column = table.columns[2]

        def box(top: int, bottom: int, column: tuple[int, int] = votes_column) -> tuple[float, float, float, float]:
            return (column[0] / image.width, top / image.height, column[1] / image.width, bottom / image.height)

        lists = []
        for (number, sigla, votes, share), (top, bottom) in zip(raw_lists, table.rows, strict=True):
            if (
                not re.fullmatch(VOTES, votes)
                or not re.fullmatch(SHARE, share)
                or not re.fullmatch(r"\d+(-\d+)*", number)
            ):
                raise ValueError(f"page {page}: prefect row reads {number!r} {votes!r} {share!r}")
            count = int(votes.replace(".", ""))
            shown = f"{round(count * 1000 / total) / 10:.1f}".replace(".", ",") + "%"
            if shown != share:
                raise ValueError(f"page {page}: list {number} has {shown} of the votes, and the atlas prints {share}")
            lists.append((number, sigla, count, share, box(top, bottom)))
        if sum(row[2] for row in lists) != total:
            raise ValueError(f"page {page}: the lists' votes do not add up to the printed total")
        found.append(Prefect(page, province, total, box(*table.total), lists))
    if entries:
        raise ValueError(f"prefect pages the atlas does not have: {sorted(entries)}")
    return found
