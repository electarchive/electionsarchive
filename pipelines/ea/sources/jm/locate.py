"""Finds where each figure of the scanned constituency tables is printed on its page.

The reports have no text layer. Each page is rendered at 200 dpi, and the column of figures is cut out of it. Its
lines of ink are found from the row profile, and each line is read on its own with Tesseract, restricted to digits. The
lines of a page are then aligned in order with the figures the transcription in `scans/` holds for that page, and an
aligned line becomes the figure's bounding box. A line whose reading is not close to the figure it is aligned with is
left without a box. The boxes are normalized to the page and written to `locators/`; the packages read those files.

This module is a tool that writes `locators/`; the packages read those files, not the scans.
"""

import csv
import difflib
import io
import itertools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

DPI = 200
HERE = Path(__file__).parent
OUT = HERE / "locators"


def render(pdf: Path, page: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(pdf), f"{folder}/p"],
            check=True,
        )
        return Image.open(f"{folder}/p.png").convert("L")


def skew(image: Image.Image) -> float:
    small = (np.asarray(image.resize((image.width // 5, image.height // 5))) < 160).astype(np.uint8)

    def score(step: float) -> float:
        return float(np.var(ndimage.rotate(small, step, reshape=False, order=0).sum(axis=1).astype(float)))

    coarse = max((float(a) for a in np.arange(-3.0, 3.01, 0.5)), key=score)
    return round(max((coarse + float(a) for a in np.arange(-0.4, 0.41, 0.1)), key=score), 1)


def straighten(image: Image.Image) -> tuple[Image.Image, float]:
    angle = skew(image)
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255), angle


def unrotate(box: tuple[int, int, int, int], angle: float, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """The box on the original page, for a box found on the page after it was rotated by `angle` degrees."""
    cx, cy = size[0] / 2, size[1] / 2
    turn = np.radians(angle)  # the page was rotated counterclockwise by `angle`; rotate the corners back
    corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
    xs, ys = [], []
    for x, y in corners:
        dx, dy = x - cx, y - cy
        xs.append(cx + dx * np.cos(turn) - dy * np.sin(turn))
        ys.append(cy + dx * np.sin(turn) + dy * np.cos(turn))
    return min(xs), min(ys), max(xs), max(ys)


def read(image: Image.Image, psm: str, whitelist: str | None = None) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory() as folder:
        image.save(f"{folder}/c.png")
        command = ["tesseract", f"{folder}/c.png", "stdout", "--psm", psm, "-l", "eng"]
        if whitelist:
            command += ["-c", f"tessedit_char_whitelist={whitelist}"]
        out = subprocess.run([*command, "tsv"], capture_output=True, text=True, check=True).stdout
    rows = csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
    return [r for r in rows if (r.get("text") or "").strip()]


def column(image: Image.Image) -> tuple[int, int]:
    """The horizontal extent of the column of totals, from the right edges of the comma-grouped numbers on the page."""
    rights = [
        int(r["left"]) + int(r["width"])
        for r in read(image, "6")
        if re.fullmatch(r"\d{1,3}(,\d{3})+", r["text"].strip(" .,:;|'\"") or "x") and int(r["left"]) > image.width // 2
    ]
    if not rights:
        raise ValueError("no figures found on the page")
    edge = int(np.median(rights))
    return edge - 230, edge + 30


def lines(image: Image.Image, left: int, right: int) -> list[tuple[int, int, int, int]]:
    """The lines of ink in the column, as (x0, y0, x1, y1) in pixels."""
    pixels = np.asarray(image)[:, left:right] < 175
    # Drop the long vertical rules and the specks the scanner left, so only the figures remain.
    labels, _ = ndimage.label(pixels, structure=np.ones((3, 3)))
    boxes = ndimage.find_objects(labels)
    keep = np.zeros_like(pixels)
    for i, box in enumerate(boxes, 1):
        height = box[0].stop - box[0].start
        width = box[1].stop - box[1].start
        if 6 <= height <= 45 and width <= 40:
            keep[box][labels[box] == i] = True
    profile = keep.sum(axis=1)
    out: list[tuple[int, int, int, int]] = []
    start: int | None = None
    gap = 0
    for y in range(len(profile) + 1):
        ink = y < len(profile) and profile[y] > 0
        if ink:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 1:
                end = y - gap + 1
                columns = np.flatnonzero(keep[start:end].any(axis=0))
                # The figure is the rightmost group of ink; a party abbreviation to its left is a separate group.
                breaks = np.flatnonzero(np.diff(columns) > 22) if len(columns) else []
                if len(breaks):
                    columns = columns[breaks[-1] + 1 :]
                if end - start >= 10 and len(columns) >= 8:
                    out.append((left + int(columns[0]), start, left + int(columns[-1]) + 1, end))
                start = None
    return split(out)


def split(found: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Cuts a block of ink that runs over several rows, where specks joined the lines, at the usual row pitch."""
    steps = [b[1] - a[1] for a, b in itertools.pairwise(found) if 25 <= b[1] - a[1] <= 45]
    if not steps:
        return found
    pitch = float(np.median(steps))
    out: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in found:
        rows = round((y1 - y0 + pitch * 0.25) / pitch)
        if rows <= 1 or y1 - y0 < pitch * 1.6:
            out.append((x0, y0, x1, y1))
            continue
        step = (y1 - y0) / rows
        out += [(x0, round(y0 + i * step), x1, round(y0 + (i + 1) * step)) for i in range(rows)]
    return out


def digits(image: Image.Image, box: tuple[int, int, int, int]) -> str:
    x0, y0, x1, y1 = box
    crop = image.crop((max(x0 - 12, 0), max(y0 - 8, 0), x1 + 12, y1 + 8))
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    return "".join(re.sub(r"\D", "", r["text"]) for r in read(padded, "7", "0123456789,"))


def align(seen: list[str], want: list[str]) -> list[tuple[int, int]]:
    """Pairs (line, figure) of an order-preserving alignment that maximizes the agreement of the readings."""
    n, m = len(seen), len(want)
    score = np.zeros((n + 1, m + 1))
    back = np.zeros((n + 1, m + 1), dtype=np.int8)
    for i in range(1, n + 1):
        score[i, 0] = score[i - 1, 0] - 0.3
        back[i, 0] = 1
    for j in range(1, m + 1):
        score[0, j] = score[0, j - 1] - 2.0
        back[0, j] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            ratio = difflib.SequenceMatcher(None, seen[i - 1], want[j - 1]).ratio() if seen[i - 1] else 0.3
            pair = 4.0 if seen[i - 1] == want[j - 1] else (ratio * 2 - 1)
            options = [score[i - 1, j - 1] + pair, score[i - 1, j] - 0.3, score[i, j - 1] - 2.0]
            best = int(np.argmax(options))
            score[i, j] = options[best]
            back[i, j] = best
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        if back[i, j] == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif back[i, j] == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def figures(c: dict) -> list[tuple[str, int | None]]:
    """The figures of a constituency in printed order, with their measure keys."""
    return [
        ("electors", c["electors"]),
        ("cast", c["cast"]),
        ("accepted", c["accepted"]),
        ("rejected", c["rejected"]),
        *((f"votes:{i}", x["votes"]) for i, x in enumerate(c["candidates"], 1)),
    ]


def locate(day: str, pdf: Path, kinds: tuple[str, ...] = ("electors", "cast", "accepted", "rejected", "votes")) -> dict:
    raw = json.loads((HERE / "scans" / f"{day}.json").read_text(encoding="utf-8"))
    result: dict[str, dict[str, list[float]]] = {}
    stats = {"exact": 0, "near": 0, "missing": 0}
    for page in sorted({c["page"] for c in raw["constituencies"]}):
        original = render(pdf, page)
        image, angle = straighten(original)
        left, right = column(image)
        seen = [(box, digits(image, box)) for box in lines(image, left, right)]
        want: list[tuple[int, str, int]] = []
        for c in raw["constituencies"]:
            if c["page"] != page:
                continue
            for key, value in figures(c):
                if value is not None and key.split(":")[0] in kinds:
                    want.append((c["number"], key, value))
        pairs = align([s[1] for s in seen], [str(w[2]) for w in want])
        found = set()
        for i, j in pairs:
            box, text = seen[i]
            number, key, value = want[j]
            if text != str(value) and difflib.SequenceMatcher(None, text, str(value)).ratio() < 0.5 and text:
                continue
            stats["exact" if text == str(value) else "near"] += 1
            found.add(j)
            x0, y0, x1, y1 = unrotate(box, angle, image.size)
            result.setdefault(str(number), {})[key] = [
                page,
                round(x0 / image.width, 4),
                round(y0 / image.height, 4),
                round(x1 / image.width, 4),
                round(y1 / image.height, 4),
            ]
        stats["missing"] += len(want) - len(found)
        print(f"{day} page {page}: {len(found)} of {len(want)} figures on {len(seen)} lines", file=sys.stderr)
    print(f"{day}: {stats}", file=sys.stderr)
    return result


def rows(image: Image.Image) -> list[list[tuple[str, tuple[int, int, int, int]]]]:
    """The comma-grouped or plain whole numbers of a page's tokens, in rows of the table, each with its box."""
    found = []
    for r in read(image, "6"):
        text = r["text"].strip(" .,:;|'\"")
        if re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", text):
            x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            found.append((text.replace(",", ""), (x, y, x + w, y + h)))
    found.sort(key=lambda t: t[1][1] + t[1][3])
    grouped: list[list[tuple[str, tuple[int, int, int, int]]]] = []
    for token in found:
        centre = (token[1][1] + token[1][3]) / 2
        if grouped and abs(centre - np.mean([(t[1][1] + t[1][3]) / 2 for t in grouped[-1]])) < 30:
            grouped[-1].append(token)
        else:
            grouped.append([token])
    return [sorted(g, key=lambda t: t[1][0]) for g in grouped if len(g) >= 2]


def locate_rows(day: str, pdf: Path, turned: tuple[int, ...]) -> dict:
    """Locates figures in tables with one constituency to a row, on pages read in whole numbers; `turned` lists the
    pages printed sideways, which are turned a quarter clockwise first. A figure is boxed only where a number with
    exactly its digits is printed in the constituency's row."""
    raw = json.loads((HERE / "scans" / f"{day}.json").read_text(encoding="utf-8"))
    result: dict[str, dict[str, list[float]]] = {}
    stats = {"found": 0, "missing": 0}
    pages = sorted({c["page"] for c in raw["constituencies"]} | {c["stats_page"] for c in raw["constituencies"]})
    for page in pages:
        original = render(pdf, page)
        image = original.rotate(-90, expand=True) if page in turned else original
        table = rows(image)
        owners = [
            c
            for c in raw["constituencies"]
            if page in (c["page"], c["stats_page"]) and any(v is not None for _, v in figures(c))
        ]

        # Align the constituencies of the page with the rows that hold their figures, in order.
        def count(c: dict, row: list) -> int:
            have = {t[0] for t in row}
            return sum(1 for _, v in figures(c) if v is not None and str(v) in have)

        used: set[int] = set()
        position = 0
        for c in owners:
            best = max(range(position, min(position + 4, len(table))), key=lambda i: count(c, table[i]), default=None)
            if best is None or count(c, table[best]) < 2 or best in used:
                stats["missing"] += sum(1 for _, v in figures(c) if v is not None)
                continue
            used.add(best)
            position = best + 1
            free = list(table[best])
            for key, value in figures(c):
                if value is None:
                    continue
                kind = key.split(":")[0]
                if (kind == "votes") != (page == c["page"]) and c["page"] != c["stats_page"]:
                    continue
                hit = next((t for t in free if t[0] == str(value)), None)
                if hit is None:
                    stats["missing"] += 1
                    continue
                free.remove(hit)
                x0, y0, x1, y1 = hit[1]
                if page in turned:
                    height = original.height
                    x0, y0, x1, y1 = y0, height - x1, y1, height - x0
                result.setdefault(str(c["number"]), {})[key] = [
                    page,
                    round(x0 / original.width, 4),
                    round(y0 / original.height, 4),
                    round(x1 / original.width, 4),
                    round(y1 / original.height, 4),
                ]
                stats["found"] += 1
        print(f"{day} page {page}: {len(table)} rows for {len(owners)} constituencies", file=sys.stderr)
    print(f"{day}: {stats}", file=sys.stderr)
    return result


def main() -> None:
    day, pdf = sys.argv[1], Path(sys.argv[2])
    OUT.mkdir(exist_ok=True)
    sideways = {"1993-03-30": (3, 4, 5, 6)}
    found = locate_rows(day, pdf, sideways[day]) if day in sideways else locate(day, pdf)
    (OUT / f"{day}.json").write_text(json.dumps(found, indent=0) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
