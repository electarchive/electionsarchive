import csv
import io
import itertools
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

type Box = tuple[int, int, int, int]
type Table = tuple[list[int], list[int]]
type Word = tuple[str, Box, tuple[int, int, int], float]

PASSES = ((2, "7"), (3, "7"), (1, "7"))
CONFUSABLE = str.maketrans(
    {
        "l": "1", "I": "1", "|": "1", "]": "1", "[": "1", "!": "1", "i": "1", "O": "0", "o": "0", "D": "0",
        "Q": "0", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9", "'": ",", "’": ",", "`": ",",
    }
)  # fmt: skip


def tables(page: Path) -> list[Table]:
    dark = np.asarray(Image.open(page).convert("L")) < 200
    window = 250
    cumulative = np.cumsum(dark, axis=1, dtype=np.int32)
    full = cumulative[:, window:] - cumulative[:, :-window] >= window - 2
    lines: list[list[int]] = []
    for y in np.flatnonzero(full.any(axis=1)):
        xs = np.flatnonzero(full[y])
        x0, x1 = int(xs[0]), int(xs[-1]) + window
        if lines and y - lines[-1][1] <= 4:
            lines[-1][1:] = [int(y), min(lines[-1][2], x0), max(lines[-1][3], x1)]
        else:
            lines.append([int(y), int(y), x0, x1])
    groups: list[list[list[int]]] = []
    for line in lines:
        previous = groups[-1][-1] if groups else None
        if (
            previous is not None
            and abs(line[2] - previous[2]) < 20
            and abs(line[3] - previous[3]) < 20
            and line[0] - previous[1] > 8
            and dark[previous[1] + 3 : line[0] - 2, previous[2] : previous[2] + 12].any(axis=1).mean() > 0.9
        ):
            groups[-1].append(line)
        else:
            groups.append([line])
    found: list[Table] = []
    for group in groups:
        if len(group) < 2:
            continue
        x0, x1 = min(line[2] for line in group), max(line[3] for line in group)
        top = group[1][1] if len(group) > 2 else group[0][1]
        share = dark[top + 3 : group[-1][0] - 2, x0 - 6 : x1 + 6].mean(axis=0)
        columns: list[list[int]] = []
        for x in np.flatnonzero(share > 0.9):
            if columns and x - columns[-1][-1] <= 6:
                columns[-1].append(int(x))
            else:
                columns.append([int(x)])
        found.append(
            (
                [(line[0] + line[1]) // 2 for line in group],
                [x0 - 6 + (column[0] + column[-1]) // 2 for column in columns],
            )
        )
    return found


def _ocr(image: Image.Image, box: Box, digits: bool, scale: int, psm: str, language: str = "tur") -> str:
    crop = image.crop((box[0] + 5, box[1] + 5, box[2] - 5, box[3] - 3))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    buffer = io.BytesIO()
    padded.save(buffer, "PNG")
    config = ["-l", "eng", "-c", "tessedit_char_whitelist=0123456789.,%"] if digits else ["-l", language]
    return (
        subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", psm, *config],
            input=buffer.getvalue(),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def read(page: Path, cells: list[tuple[Box, bool]], language: str = "tur") -> list[list[str]]:
    image = Image.open(page).convert("L")
    with ThreadPoolExecutor(12) as pool:
        return list(
            pool.map(
                lambda cell: [
                    _ocr(image, cell[0], cell[1], scale, psm, language) for scale, psm in PASSES[: 3 if cell[1] else 1]
                ],
                cells,
            )
        )


def skew(words: list[Word]) -> float:
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for word in words:
        if word[3] > 60 and len(word[0]) > 1:
            grouped.setdefault(word[2], []).append(word)
    slopes = []
    for line in grouped.values():
        first, last = min(line, key=lambda w: w[1][0]), max(line, key=lambda w: w[1][2])
        run = (last[1][0] + last[1][2] - first[1][0] - first[1][2]) / 2
        if run > 800:
            slopes.append((last[1][1] + last[1][3] - first[1][1] - first[1][3]) / 2 / run)
    return sorted(slopes)[len(slopes) // 2] if len(slopes) >= 3 else 0.0


def level(word: Word, slope: float) -> float:
    return (word[1][1] + word[1][3]) / 2 - slope * ((word[1][0] + word[1][2]) / 2 - 1275)


def lines(words: list[Word], slope: float, gap: int = 18) -> list[list[Word]]:
    found: list[tuple[float, list[Word]]] = []
    for word in sorted(words, key=lambda w: level(w, slope)):
        if found and abs(level(word, slope) - found[-1][0]) < gap:
            found[-1][1].append(word)
            found[-1] = (sum(level(w, slope) for w in found[-1][1]) / len(found[-1][1]), found[-1][1])
        else:
            found.append((level(word, slope), [word]))
    return [sorted(line, key=lambda w: w[1][0]) for _, line in found]


def numbers(text: str) -> dict[int, int]:
    groups = [group for part in re.split(r"[\s,.]+", text.translate(CONFUSABLE)) if (group := re.sub(r"\D", "", part))]
    if not groups:
        return {}
    joined = "".join(groups)
    found = {int(joined): 2}
    for mask in itertools.product((False, True), repeat=min(len(groups), 6)):
        kept = [group for group, drop in zip(groups, mask, strict=False) if not (drop and len(group) == 1)]
        if kept and kept != groups and all(len(group) == 3 for group in kept[1:]):
            found.setdefault(int("".join(kept)), 1)
    for cut in range(len(joined) - 4, 0, -4):
        if joined[cut] in "12":
            found.setdefault(int(joined[:cut] + joined[cut + 1 :]), 1)
    return found


def percent(text: str) -> float | None:
    text = re.sub(r"(\d)\s+\.", r"\1.", text.translate(CONFUSABLE).replace(",", "."))
    if match := re.search(r"(\d{0,3})\.(\d\d)", text):
        return float(f"{match[1] or 0}.{match[2]}")
    digits = re.sub(r"\D", "", text)
    if len(digits) == 2:
        return float(f"0.{digits}")
    return float(f"{digits[:-2]}.{digits[-2:]}") if 3 <= len(digits) <= 5 else None


def words(pdf: Path, page: int, cache: Path, language: str = "eng") -> list[Word]:
    if not cache.exists():
        with tempfile.TemporaryDirectory() as folder:
            cache.write_text(
                subprocess.run(
                    [
                        "tesseract",
                        str(rendered(pdf, page, Path(folder))),
                        "stdout",
                        "--psm",
                        "6",
                        "-l",
                        language,
                        "tsv",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
    return [
        (
            row["text"],
            (
                int(row["left"]),
                int(row["top"]),
                int(row["left"]) + int(row["width"]),
                int(row["top"]) + int(row["height"]),
            ),
            (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
            float(row["conf"]),
        )
        for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
        if row["level"] == "5" and row["text"].strip()
    ]


def layer(pdf: Path, page: int) -> list[tuple[str, Box]]:
    xhtml = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        (
            text,
            (
                round(float(x0) * 300 / 72),
                round(float(y0) * 300 / 72),
                round(float(x1) * 300 / 72),
                round(float(y1) * 300 / 72),
            ),
        )
        for x0, y0, x1, y1, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', xhtml
        )
    ]


def rendered(pdf: Path, page: int, folder: Path) -> Path:
    image = folder / f"{page}"
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            "300",
            "-gray",
            "-png",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            str(pdf),
            str(image),
        ],
        check=True,
    )
    return image.with_suffix(".png")
