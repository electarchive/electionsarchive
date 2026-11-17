"""The OCR pass over NEBE's scanned 2021 constituency result forms.

Every form is the same printed template: a header table (region, constituency, polling stations), a table of nine
ballot accounting lines, a results table of up to twelve candidate rows (more on some forms), and the winner's ballot
number. Each page is rendered at 200 dpi, levelled by the angle that makes its ruled lines sharpest, and its tables
are found from long dark pixel runs (``ea.scan.tables``). Each cell is then read on its own with Tesseract: digits
with the English model and a digit whitelist at three scales, and names with the Amharic and English models.

This module writes ``ocr.jsonl`` in a work folder, one record per page, with each cell's text and its box as a share
of the page. The vision pass reads the same pages independently, and ``nebe_reconcile`` joins the two.
"""

import itertools
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from ea import scan

DPI = 200
LATIN = ("afar", "oromia", "somali")
type Box = tuple[int, int, int, int]
type Table = tuple[list[int], list[int]]


def level(image: Image.Image) -> tuple[Image.Image, float]:
    """Rotate the page by the angle within four degrees that gives the sharpest horizontal projection."""
    small = image.reduce(4)
    dark = (np.asarray(small) < 160).astype(np.float32)
    best, angle = -1.0, 0.0
    for step in np.arange(-4.0, 4.01, 0.1):
        turned = np.asarray(Image.fromarray((dark * 255).astype(np.uint8)).rotate(step, fillcolor=0)) > 127
        score = float((turned.sum(axis=1).astype(np.float64) ** 2).sum())
        if score > best:
            best, angle = score, float(step)
    for step in np.arange(angle - 0.1, angle + 0.101, 0.02):
        turned = np.asarray(Image.fromarray((dark * 255).astype(np.uint8)).rotate(step, fillcolor=0)) > 127
        score = float((turned.sum(axis=1).astype(np.float64) ** 2).sum())
        if score > best:
            best, angle = score, float(step)
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255), angle


def _tables(image: Image.Image) -> list[tuple[list[int], list[int]]]:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "level.png"
        image.save(path)
        return scan.tables(path)


def _text(image: Image.Image, box: Box, languages: str) -> str:
    crop = image.crop((box[0] + 4, box[1] + 4, box[2] - 4, box[3] - 3))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    out = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-l", languages],
        input=_png(padded),
        capture_output=True,
        check=True,
    )
    return " ".join(out.stdout.decode().split())


def _png(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _digits(image: Image.Image, box: Box) -> list[str]:
    return [scan._ocr(image, box, True, scale, psm) for scale, psm in scan.PASSES]


def _share(box: Box, angle: float, size: tuple[int, int]) -> list[float]:
    """The cell's box on the unrotated page, as shares of its width and height."""
    width, height = size
    cx, cy = width / 2, height / 2
    theta = np.radians(angle)
    corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
    xs, ys = [], []
    for x, y in corners:
        dx, dy = x - cx, y - cy
        # PIL rotates counterclockwise by angle; undo it.
        xs.append(cx + dx * np.cos(theta) - dy * np.sin(theta))
        ys.append(cy + dx * np.sin(theta) + dy * np.cos(theta))
    return [
        round(max(0.0, min(xs)) / width, 4),
        round(max(0.0, min(ys)) / height, 4),
        round(min(width, max(xs)) / width, 4),
        round(min(height, max(ys)) / height, 4),
    ]


def page(path: Path) -> dict[str, object]:
    languages = "eng" if any(region in path.parent.name for region in LATIN) else "amh+eng"
    original = Image.open(path).convert("L")
    record: dict[str, object] = {"file": path.parent.name, "page": int(path.stem), "size": original.size}
    candidates = [original]
    if original.width > original.height:
        candidates = [original.rotate(90, expand=True), original.rotate(-90, expand=True)]
    else:
        candidates.append(original.rotate(180))
    found = None
    for turn, base in enumerate(candidates):
        image, angle = level(base)
        tables = _tables(image)
        accounting = [t for t in tables if 9 <= len(t[0]) - 1 <= 11 and len(t[1]) == 3]
        if accounting:
            found = (turn, base, image, angle, tables)
            break
    if found is None:
        for turn, base in enumerate(candidates):
            image, angle = level(base)
            shape = template(image)
            if shape is not None:
                tables = [shape["accounting"]] + ([shape["results"]] if "results" in shape else [])
                found = (turn, base, image, angle, tables)
                record["template"] = True
                break
    if found is None:
        record["error"] = "no accounting table"
        return record
    turn, base, image, angle, tables = found
    if original.width > original.height:
        record["turn"] = 90 if turn == 0 else -90
    elif turn:
        record["turn"] = 180
    record["angle"] = round(angle, 2)

    def share(box: Box) -> list[float]:
        return _share(box, angle, base.size)

    index = next(i for i, t in enumerate(tables) if 9 <= len(t[0]) - 1 <= 11 and len(t[1]) == 3)
    rows, cols = tables[index]
    lines = []
    for top, bottom in itertools.pairwise(rows):
        box = (cols[1], top, cols[2], bottom)
        lines.append({"box": share(box), "digits": _digits(image, box)})
    record["accounting"] = lines
    if index > 0:
        rows, cols = tables[index - 1]
        header = []
        for top, bottom in itertools.pairwise(rows):
            box = (cols[0], top, cols[-1], bottom)
            header.append({"box": share(box), "text": _text(image, box, "amh+eng")})
        record["header"] = header
        if len(rows) >= 3:
            record["stations"] = [
                {"box": share(b), "digits": _digits(image, b)}
                for b in ((cols[-2], rows[i], cols[-1], rows[i + 1]) for i in range(1, len(rows) - 1))
            ]
    results = [t for t in tables[index + 1 :] if len(t[1]) == 4 and len(t[0]) >= 3]
    if results:
        rows, cols = results[0]
        options = []
        for top, bottom in itertools.pairwise(rows[1:]):
            name = (cols[1], top, cols[2], bottom)
            votes = (cols[2], top, cols[3], bottom)
            number = (cols[0], top, cols[1], bottom)
            dark = np.asarray(image.crop((name[0] + 6, name[1] + 6, name[2] - 6, name[3] - 4))) < 128
            if dark.mean() < 0.004:
                options.append({"empty": True, "box": share(name)})
                continue
            options.append(
                {
                    "number": _digits(image, number),
                    "name": _text(image, name, languages),
                    "name_box": share(name),
                    "votes": _digits(image, votes),
                    "box": share(votes),
                }
            )
        record["results"] = options
        later = [t for t in tables[tables.index(results[0]) + 1 :] if len(t[0]) >= 3]
        if later:
            rows, cols = later[0]
            box = (cols[1], rows[0], cols[2], rows[1]) if len(cols) >= 3 else (cols[0], rows[0], cols[-1], rows[1])
            record["winner_number"] = _digits(image, box)
    return record


def _rulings(image: Image.Image) -> list[list[int]]:
    """Horizontal rulings as [y, x0, x1]: thin long components once gaps of a few pixels are closed, because many
    forms were printed small and scanned light, which breaks their rulings into dashes."""
    dark = np.asarray(image) < 200
    across = ndimage.binary_closing(dark, structure=np.ones((1, 9)))
    runs = ndimage.binary_opening(across, structure=np.ones((1, 160)))
    labels, _ = ndimage.label(runs)
    found = sorted(
        [(o[0].start + o[0].stop) // 2, o[1].start, o[1].stop]
        for o in ndimage.find_objects(labels)
        if o is not None and o[0].stop - o[0].start <= 8
    )
    merged: list[list[int]] = []
    for y, x0, x1 in found:
        if merged and y - merged[-1][0] <= 6:
            merged[-1] = [merged[-1][0], min(x0, merged[-1][1]), max(x1, merged[-1][2])]
        else:
            merged.append([y, x0, x1])
    return merged


def _runs(lines: list[list[int]], start: int, count: int | None) -> list[list[int]]:
    """The longest run of evenly spaced rulings from ``start`` (exactly ``count`` when given)."""
    run = [lines[start]]
    for line in lines[start + 1 :]:
        gap = line[0] - run[-1][0]
        if gap < 12:
            continue
        if len(run) >= 2 and abs(gap - (run[1][0] - run[0][0])) > 6:
            break
        if len(run) == 1 and gap > 80:
            break
        run.append(line)
        if count is not None and len(run) == count:
            break
    return run


def _vertical(image: Image.Image, top: int, bottom: int, lo: int, hi: int, rightmost: bool = False) -> int | None:
    """The x of the strongest (or the rightmost) vertical ruling between lo and hi over the rows top..bottom."""
    lo, hi = max(0, lo), min(image.width, hi)
    dark = np.asarray(image)[top + 3 : bottom - 2, lo:hi] < 200
    if dark.size == 0:
        return None
    closed = ndimage.binary_closing(dark, structure=np.ones((9, 1)))
    share = closed.mean(axis=0)
    if rightmost:
        strong = np.flatnonzero(share > 0.8)
        return lo + int(strong[-1]) if len(strong) else None
    x = int(share.argmax())
    return lo + x if share[x] > 0.8 else None


def template(image: Image.Image) -> dict[str, Table] | None:
    """The accounting and results tables found from the form's fixed shape: ten evenly spaced rulings for the nine
    accounting lines, then the results table's evenly spaced rulings below them."""
    lines = _rulings(image)
    for i in range(len(lines)):
        rows = _runs(lines, i, 10)
        if len(rows) != 10:
            continue
        right = int(np.median([line[2] for line in rows]))
        left = int(np.median([line[1] for line in rows]))
        right = _vertical(image, rows[0][0], rows[-1][0], right - 30, right + 500, rightmost=True) or right
        split = _vertical(image, rows[0][0], rows[-1][0], right - 450, right - 60)
        if split is None:
            continue
        tables: dict[str, Table] = {"accounting": ([r[0] for r in rows], [left, split, right])}
        later = [j for j in range(i + 10, len(lines)) if lines[j][0] - rows[-1][0] > 30]
        for j in later[:3]:
            found = _runs(lines, j, None)
            if len(found) >= 3:
                ys = [r[0] for r in found]
                rright = int(np.median([line[2] for line in found]))
                rright = _vertical(image, ys[1], ys[-1], rright - 30, rright + 500, rightmost=True) or rright
                rleft = int(np.median([line[1] for line in found]))
                votes = _vertical(image, ys[1], ys[-1], rright - 450, rright - 60)
                number = _vertical(image, ys[1], ys[-1], rleft + 20, rleft + 160)
                if votes is not None and number is not None:
                    tables["results"] = (ys, [rleft, number, votes, rright])
                    break
        return tables
    return None


def main() -> None:
    os.environ["OMP_THREAD_LIMIT"] = "1"
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    done = set()
    if out.exists():
        done = {(r["file"], r["page"]) for r in map(json.loads, out.read_text().splitlines())}
    pages = sorted(
        (
            p
            for folder in sorted(root.iterdir())
            for p in folder.glob("*.png")
            if (folder.name, int(p.stem)) not in done
        ),
        key=lambda p: (p.parent.name, int(p.stem)),
    )
    if len(sys.argv) > 3:  # only these "file page" pairs
        wanted = {tuple(line.split()) for line in Path(sys.argv[3]).read_text().splitlines()}
        pages = [p for p in pages if (p.parent.name, p.stem) in wanted]
    with ProcessPoolExecutor(4) as pool, out.open("a") as file:
        for record in pool.map(page, pages):
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()


if __name__ == "__main__":
    main()
