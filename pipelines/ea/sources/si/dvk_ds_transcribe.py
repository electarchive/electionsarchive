"""Turn the State Election Commission's National Council reports into line transcriptions.

The reports are PDFs. Some carry a text layer; most are scans. `transcribe` reads the text layer with poppler's
`pdftotext -bbox-layout` where a page has one, and otherwise renders the page at 200 dpi and reads it with PaddleOCR's
Latin model, which keeps the letters with diacritics. Each line is stored with its box in pixels at 200 dpi, so every
figure can be traced to a place on its page. The result is committed as `dvk_ds_lines.json`, and the parser reads
only that file, so a build needs neither poppler nor an OCR engine. Run it with
`uv run --with paddleocr --with paddlepaddle python -m ea.sources.si.dvk_ds_transcribe`.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

from ea import raw

SOURCE = "si.dvk"
OUT = Path(__file__).with_name("dvk_ds_lines.json")
DPI = 200
SCALE = DPI / 72
XHTML = "{http://www.w3.org/1999/xhtml}"
FORCE_OCR: set[str] = set()

Line = list[int | str]
Page = dict[str, object]


def sizes(path: Path) -> list[tuple[int, int]]:
    """The size of each page in pixels at 200 dpi."""
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "9999", str(path)], check=True, capture_output=True, text=True)
    found = re.findall(r"Page\s+\d+ size:\s+([\d.]+) x ([\d.]+) pts", out.stdout)
    return [(round(float(w) * SCALE), round(float(h) * SCALE)) for w, h in found]


def text_pages(path: Path) -> list[list[Line]]:
    """The lines of each page that has a text layer, with boxes in 200 dpi pixels; an empty list for a scan."""
    out = subprocess.run(
        ["pdftotext", "-bbox-layout", str(path), "-"], check=True, capture_output=True, text=True
    ).stdout
    root = ElementTree.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", out))
    pages: list[list[Line]] = []
    for page in root.iter(f"{XHTML}page"):
        lines: list[Line] = []
        for line in page.iter(f"{XHTML}line"):
            words = [w.text or "" for w in line.iter(f"{XHTML}word")]
            text = " ".join(words).strip()
            if not text:
                continue
            box = [float(line.attrib[k]) * SCALE for k in ("yMin", "xMin", "yMax", "xMax")]
            lines.append([*(round(v) for v in box), text])
        pages.append(lines)
    return pages


def scan_pages(path: Path, only: set[int]) -> dict[int, list[Line]]:
    """Read the given pages (1-based) of a PDF with the Latin OCR model."""
    from paddleocr import PaddleOCR  # ty: ignore[unresolved-import]

    engine = PaddleOCR(
        text_recognition_model_name="latin_PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )
    found: dict[int, list[Line]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", str(path), f"{tmp}/p"], check=True)
        for image in sorted(Path(tmp).glob("p-*.png")):
            number = int(image.stem.split("-")[1])
            if number not in only:
                continue
            result = engine.predict(str(image))[0]
            found[number] = [
                [int(b[1]), int(b[0]), int(b[3]), int(b[2]), t]
                for t, b in zip(result["rec_texts"], result["rec_boxes"], strict=True)
            ]
    return found


def transcribe(urls: list[str]) -> None:
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    for url in urls:
        if url in done:
            continue
        path = raw.cached(SOURCE, url).path
        pages = text_pages(path)
        blank = {i + 1 for i, lines in enumerate(pages) if sum(len(str(line[4])) for line in lines) < 40}
        if url in FORCE_OCR:
            blank = set(range(1, len(pages) + 1))
        if blank:
            for number, lines in scan_pages(path, blank).items():
                pages[number - 1] = lines
        done[url] = [{"w": w, "h": h, "lines": lines} for (w, h), lines in zip(sizes(path), pages, strict=True)]
        print(url, len(pages), "pages", len(blank), "read by OCR", flush=True)
        OUT.write_text(json.dumps(done, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    from ea.sources.si import dvk_ds

    FORCE_OCR.update(dvk_ds.FORCE_OCR)
    transcribe(sys.argv[1:] or dvk_ds.report_urls())
