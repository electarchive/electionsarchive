"""Reads the municipal tables of the Republic Statistical Office's books on the republic referendums of 1992 and 1998.

The books are scans with no text layer: every page is a 144 dpi JPEG. ``read`` finds every word on a page with
RapidOCR, which keeps digits and decimal marks well but not Cyrillic letters, so a municipality is identified by its
printed number in the table and named from the list in ``rik_referendum``. ``rows`` turns the words of a table's pages
into one row per municipality, with the registered voters, the voters, and the two answers' counts, each with the page
and the box it was read from, and checks every row against the percentages the book prints beside the counts.
``rik_referendum`` reads the result from ``rik_referendum.json``, so building the packages needs no OCR. Run
``extract`` to read the scans again.
"""

import importlib
import re
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCALE = 72 / 144  # the JPEGs hold 144 dpi, and the boxes are kept in PDF points
ORDINAL = re.compile(r"^(\d{1,3})\s*[.,]?")

Word = tuple[int, float, float, float, float, str]  # page, x0, y0, x1, y1 in points, text


def read(pdf: Path, pages: Iterable[int]) -> list[Word]:
    """Every word RapidOCR finds on the given pages of a scan (1-based)."""
    from PIL import Image, ImageOps  # optional: `uv sync --group scans`

    engine: Any = importlib.import_module("rapidocr_onnxruntime").RapidOCR()
    words: list[Word] = []
    with tempfile.TemporaryDirectory() as work:
        for page in pages:
            subprocess.run(
                ["pdftoppm", "-r", "144", "-gray", "-png", "-f", str(page), "-l", str(page), str(pdf), f"{work}/p"],
                check=True,
            )
            image = next(Path(work).glob("p-*.png"))
            contrast = Path(work) / "contrast.png"
            ImageOps.autocontrast(Image.open(image).convert("L"), cutoff=1).save(contrast)
            image.unlink()
            found, _ = engine(str(contrast))
            for corners, text, _confidence in found or []:
                xs, ys = [c[0] for c in corners], [c[1] for c in corners]
                words.append((page, min(xs) * SCALE, min(ys) * SCALE, max(xs) * SCALE, max(ys) * SCALE, text))
    return words


def _centers(cells: dict[int, list[Word]]) -> list[float]:
    """The x of each of a table's seven numeric columns, from the rows that read as seven numbers."""
    xs: list[list[float]] = [[] for _ in range(7)]
    for ws in cells.values():
        if len(ws) == 7:
            for i, w in enumerate(sorted(ws, key=lambda w: w[1])):
                xs[i].append((w[1] + w[3]) / 2)
    return [sorted(c)[len(c) // 2] if c else 0.0 for c in xs]


def _box(w: Word) -> list[float]:
    return [w[0], *(round(v, 1) for v in w[1:5])]


def rows(
    words: list[Word],
    expect: int,
    base: str,
    decimals: tuple[int, int, int] = (2, 2, 2),
    fixes: dict[int, dict[str, int]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per numbered municipality, and what is wrong with the rows that do not read cleanly.

    ``base`` is what the answers' percentages are of: ``registered`` or ``voters``. ``fixes`` holds counts read by eye
    from the page for cells the OCR dropped or misread; such a cell keeps the box of its column on its row. A fix with
    ``skip`` set marks a row whose counts read right and whose printed percentage the OCR misread.
    """
    cells: dict[int, list[Word]] = {}
    anchored: dict[int, tuple[int, float, float]] = {}
    last = 0
    for page in sorted({w[0] for w in words}):
        on = [w for w in words if w[0] == page]
        anchors: list[tuple[int, float]] = []
        for w in on:
            m = ORDINAL.match(w[5].strip())
            if m and w[1] < 80 and 1 <= int(m[1]) <= expect:
                anchors.append((int(m[1]), (w[2] + w[4]) / 2))
        figures = sorted(
            (w for w in on if w[1] >= 150 and re.match(r"^[\d.,]+$", w[5].strip())), key=lambda w: (w[2] + w[4]) / 2
        )
        groups: list[list[Word]] = []
        for w in figures:
            if groups and (w[2] + w[4]) / 2 - sum((v[2] + v[4]) / 2 for v in groups[-1]) / len(groups[-1]) <= 6:
                groups[-1].append(w)
            else:
                groups.append([w])
        top = min((a[1] for a in anchors), default=0.0)
        for ws in (g for g in groups if len(g) >= 3):
            yc = sum((w[2] + w[4]) / 2 for w in ws) / len(ws)
            if yc < top - 8:  # the row of the Republic's totals that tops the first page of a table
                continue
            near = min(anchors, key=lambda a: abs(a[1] - yc), default=(0, 1e9))
            n = near[0] if abs(near[1] - yc) <= 8 and last < near[0] <= last + 3 else last + 1
            last = n
            if n <= expect and n not in cells:
                cells[n] = sorted(ws, key=lambda w: w[1])
                anchored[n] = (page, yc - 5, yc + 5)
    centers = _centers(cells)
    table: list[dict[str, Any]] = []
    problems: list[str] = []
    for n in range(1, expect + 1):
        if n not in cells:
            problems.append(f"{n}: missing")
            continue
        column: dict[int, Word] = {}
        for word in cells[n]:
            x = (word[1] + word[3]) / 2
            i = min(range(7), key=lambda i: abs(centers[i] - x))
            if abs(centers[i] - x) < 30 and i not in column:
                column[i] = word
        fixed = (fixes or {}).get(n, {})
        names = {0: "registered", 1: "voters", 3: "a", 5: "b"}
        row: dict[str, Any] = {"n": n}
        bad = False
        for i, name in names.items():
            w = column.get(i)
            ay = anchored[n]
            if name in fixed:
                x = centers[i]
                row[name] = [fixed[name], [ay[0], round(x - 20, 1), round(ay[1], 1), round(x + 20, 1), round(ay[2], 1)]]
            elif w and (digits := re.sub(r"\D", "", w[5])):
                row[name] = [int(digits), _box(w)]
            else:
                bad = True
        if bad:
            problems.append(f"{n}: {[w[5] for w in cells[n]]}")
            continue
        printed = []
        for k, i in enumerate((2, 4, 6)):
            w = column.get(i)
            text = re.sub(r"[^\d]", "", w[5]) if w else ""
            printed.append(int(text) / 10 ** decimals[k] if text else None)
        row["printed"] = printed
        reg, voted = row["registered"][0], row["voters"][0]
        whole = {"registered": reg, "voters": voted}[base]
        checks = ((voted, reg, printed[0]), (row["a"][0], whole, printed[1]), (row["b"][0], whole, printed[2]))
        for k, (count, ref, shown) in enumerate(checks):
            if (
                not fixed.get("skip")
                and shown is not None
                and ref
                and abs(100 * count / ref - shown) > 1.05 * 10 ** -decimals[k]
            ):
                problems.append(f"{n}: {reg} {voted} {row['a'][0]} {row['b'][0]} printed {printed}")
                break
        table.append(row)
    return table, problems
