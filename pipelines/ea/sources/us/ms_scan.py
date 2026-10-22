import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

DPI = 200
INK = 150


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


def long_runs(dark: np.ndarray, minlen: int) -> np.ndarray:
    out = np.zeros_like(dark)
    for i, row in enumerate(dark):
        edges = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0], strict=True):
            if b - a >= minlen:
                out[i, a:b] = True
    return out


def erase(image: np.ndarray, minlen: int = 80, grow: int = 3) -> np.ndarray:
    dark = image < 170
    mask = long_runs(dark, minlen) | long_runs(dark.T.copy(), minlen).T
    for _ in range(grow):
        mask = mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0) | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)
    out = image.copy()
    out[mask] = 255
    return out


def words(image: np.ndarray, psms: tuple[int, ...] = (6, 11, 12)) -> list[tuple[int, float, float, str]]:
    found: list[tuple[int, float, float, str]] = []
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        Image.fromarray(image).save(handle.name)
        for psm in psms:
            text = subprocess.run(
                ["tesseract", handle.name, "-", "--psm", str(psm), "-c", "tessedit_char_whitelist=0123456789", "tsv"],
                capture_output=True,
                text=True,
                env={"OMP_THREAD_LIMIT": "2", "PATH": "/usr/bin"},
            ).stdout
            for line in text.splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) == 12 and parts[0] == "5" and parts[11].strip().isdigit():
                    found.append((psm, int(parts[6]) + int(parts[8]) / 2, int(parts[7]) + int(parts[9]) / 2, parts[11].strip()))
    return found
