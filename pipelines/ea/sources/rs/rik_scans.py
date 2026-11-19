"""Word boxes for the Republic Electoral Commission's scanned statistics publications of the parliamentary
elections of 1990 to 2000.

Every page of the five books is a 144 dpi JPEG with no text layer, and the books report by municipality, not by
polling station. `ocr` reads each page with RapidOCR, which keeps digits and decimal marks well but not Cyrillic
letters, and stores every recognized word with its page and its box in PDF points, so that a number parsed from it
can cite its page and box.
"""

import importlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ea import raw

SOURCE = "rs.rik"
BASE = "https://arhiva.rik.parlament.gov.rs/doc/arhiva/poslanici"
BOOKS = {
    "2000": "2000/3.%20Publikacija%202000%20np%20rzs.pdf",
    "1997": "1997/1997%20Publikacija%20statistike%20np.pdf",
    "1993": "1993/1993%20publikacija.pdf",
    "1992": "1992/publikacija%201992%20np%20rzs%20compress.pdf",
    "1990": "1990/3.%20-%201990%20publikacija%20rzs.compressed.pdf",
}
DPI = 200


def url(year: str) -> str:
    return f"{BASE}/{BOOKS[year]}"


def fetch(year: str) -> Path:
    return raw.fetch(SOURCE, url(year), suffix=".pdf").path


def ocr(year: str, out: Path, pages: range | None = None) -> None:
    """Write `{page: [[x0, y0, x1, y1, text, confidence], ...]}` for a book, boxes in PDF points.

    `pages` limits the read to those pages (1-based).
    """
    engine: Any = importlib.import_module("rapidocr_onnxruntime").RapidOCR()  # optional: `uv sync --group scans`
    pdf = fetch(year)
    scale = 72 / DPI
    book: dict[int, list[list[float | str]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        span = ["-f", str(pages.start), "-l", str(pages.stop - 1)] if pages else []
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", *span, str(pdf), f"{tmp}/p"], check=True)
        for image in sorted(Path(tmp).glob("p-*.png")):
            found, _ = engine(str(image))
            words: list[list[float | str]] = []
            for corners, text, confidence in found or []:
                xs, ys = [c[0] for c in corners], [c[1] for c in corners]
                box = [round(v * scale, 1) for v in (min(xs), min(ys), max(xs), max(ys))]
                words.append([*box, text, round(float(confidence), 3)])
            book[int(image.stem.split("-")[-1])] = words
    out.write_text(json.dumps(book, separators=(",", ":")))
