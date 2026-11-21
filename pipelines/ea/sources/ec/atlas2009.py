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
DPI = 300
IMAGE_DPI = 150  # the page images stored for the site, at the atlas's own resolution
ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}
WINDOW = 600  # the shortest run of dark pixels that counts as a rule
CORRECTIONS = Path(__file__).with_name("atlas2009_mayors.json")

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


def tables(image: Image.Image) -> list[Table]:
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
        if bottom - top < 60:
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
