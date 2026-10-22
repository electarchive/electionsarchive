import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

DPI = 200
INK = 150
EMPTY = 25


def render(pdf: Path, page: int, dpi: int = DPI, turn: int = 0) -> np.ndarray:
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(pdf), f"{folder}/p"],
            check=True,
        )
        image = Image.open(f"{folder}/p.png").convert("L")
        if turn:
            image = image.rotate(turn, expand=True)
        return np.array(image)


def clusters(indices: np.ndarray) -> list[float]:
    found: list[list[int]] = []
    for index in indices:
        if found and index - found[-1][-1] <= 3:
            found[-1].append(int(index))
        else:
            found.append([int(index)])
    return [(group[0] + group[-1]) / 2 for group in found]


def rules(image: np.ndarray, share: float = 0.6) -> tuple[list[float], list[float]]:
    dark = image < INK
    rows, columns = dark.sum(1), dark.sum(0)
    return (
        clusters(np.where(rows > share * rows.max())[0]),
        clusters(np.where(columns > share * columns.max())[0]),
    )


def read(crop: Image.Image, scale: int, psm: int, whitelist: str = "0123456789", binarize: int = 0) -> str:
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    if binarize:
        crop = crop.point(lambda value: 255 if value > binarize else 0)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        padded.save(handle.name)
        command = ["tesseract", handle.name, "-", "--psm", str(psm), "-c", "load_system_dawg=0", "-c", "load_freq_dawg=0"]
        if whitelist:
            command += ["-c", f"tessedit_char_whitelist={whitelist}"]
        return subprocess.run(command, capture_output=True, text=True, env={"OMP_THREAD_LIMIT": "1", "PATH": "/usr/bin"}).stdout.strip()


def cell_job(job):
    data, size, (x0, y0, x1, y1) = job
    image = np.frombuffer(data, dtype=np.uint8).reshape(size[1], size[0])
    inner = image[y0 + 6 : y1 - 5, x0 + 8 : x1 - 7].copy()
    dark = inner < INK
    edge = np.zeros(inner.shape[1], dtype=bool)
    edge[:7] = edge[-7:] = True
    inner[:, (dark.mean(0) > 0.6) & edge] = 255
    rim = np.zeros(inner.shape[0], dtype=bool)
    rim[:5] = rim[-5:] = True
    inner[(dark.mean(1) > 0.8) & rim, :] = 255
    dark = inner < INK
    if dark.sum() < EMPTY:
        return [], None
    ys, xs = np.where(dark)
    box = (x0 + 8 + int(xs.min()), y0 + 6 + int(ys.min()), x0 + 8 + int(xs.max()) + 1, y0 + 6 + int(ys.max()) + 1)
    crop = Image.fromarray(inner)
    values: list[int | None] = []
    for scale, psm in READS:
        text = read(crop, scale, psm).replace(" ", "")
        values.append(int(text) if text.isdigit() else None)
    return values, box


READS = ((3, 7), (4, 7), (2, 7), (3, 8), (5, 7))


def cells(image: np.ndarray, grid: list[tuple[int, int, int, int]]):
    data = image.tobytes()
    size = (image.shape[1], image.shape[0])
    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(cell_job, [(data, size, box) for box in grid], chunksize=16))


def settle(reads: list[list[int | None]], total: int | None, fixed: dict[int, int], digits: list[int | None] | None = None) -> tuple[list[int | None], list[int]]:
    options: list[list[int | None]] = []
    for index, values in enumerate(reads):
        if index in fixed:
            options.append([fixed[index]])
            continue
        seen = [v for v in values if v is not None]
        ranked = sorted(set(seen), key=lambda v: (-seen.count(v), v))
        if digits is not None and digits[index] is not None:
            fits = [v for v in ranked if len(str(v)) == digits[index]]
            ranked = fits or ranked
        if ranked and seen.count(ranked[0]) >= max(4, 0.6 * len(seen)):
            ranked = ranked[:1]
        options.append(ranked or [None])
    open_cells = [index for index, choice in enumerate(options) if len(choice) > 1]
    base = sum(choice[0] or 0 for index, choice in enumerate(options) if index not in open_cells)
    if total is None:
        return [choice[0] for choice in options], open_cells
    reach: dict[int, list[tuple[int, ...]]] = {base: [()]}
    for index in open_cells:
        nxt: dict[int, list[tuple[int, ...]]] = {}
        for sofar, paths in reach.items():
            for choice, value in enumerate(options[index]):
                got = sofar + (value or 0)
                if got <= total:
                    nxt.setdefault(got, [])
                    if len(nxt[got]) < 2:
                        nxt[got].extend(path + (choice,) for path in paths[: 2 - len(nxt[got])])
        reach = nxt
    solutions = reach.get(total, [])
    if len(solutions) != 1:
        raise ValueError(f"{len(solutions)} solutions against {total}")
    settled = [choice[0] for choice in options]
    for index, pick in zip(open_cells, solutions[0], strict=True):
        settled[index] = options[index][pick]
    return settled, open_cells


def strips(image: np.ndarray, rows: list[float], xs: list[float], columns: list[int]) -> dict[tuple[int, int], list[int]]:
    import csv
    import io

    found: dict[tuple[int, int], list[int]] = {}
    for c in columns:
        strip = image[int(rows[0]) : int(rows[-1]), int(xs[c]) - 3 : int(xs[c + 1]) + 4].copy()
        strip[:, (strip < INK).mean(0) > 0.2] = 255
        for y in rows:
            top = int(y - rows[0])
            strip[max(0, top - 4) : top + 5, :] = 255
        for scale, psm in ((3, 6), (4, 6), (2, 6), (3, 4)):
            crop = Image.fromarray(strip).resize((strip.shape[1] * scale, strip.shape[0] * scale), Image.LANCZOS)
            padded = Image.new("L", (crop.width + 60, crop.height + 60), 255)
            padded.paste(crop, (30, 30))
            with tempfile.NamedTemporaryFile(suffix=".png") as handle:
                padded.save(handle.name)
                text = subprocess.run(
                    ["tesseract", handle.name, "-", "--psm", str(psm), "-c", "tessedit_char_whitelist=0123456789", "tsv"],
                    capture_output=True,
                    text=True,
                    env={"OMP_THREAD_LIMIT": "1", "PATH": "/usr/bin"},
                ).stdout
            for line in text.splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) == 12 and parts[0] == "5" and parts[11].strip().isdigit():
                    centre = (int(parts[7]) + int(parts[9]) / 2 - 30) / scale + rows[0]
                    row = max(i for i in range(len(rows) - 1) if rows[i] <= centre)
                    found.setdefault((c, row), []).append(int(parts[11]))
    return found


def digit_model(samples: list[tuple[int, int]]) -> tuple[float, float]:
    xs = np.array([n for n, _ in samples], dtype=float)
    ys = np.array([w for _, w in samples], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)
