"""The OCR pass over NEBE's scanned 2021 regional council constituency result forms.

The council form is the House form with one more header line, the constituency's seats, and a results table that
often runs over several pages: a page that continues a form has no accounting table, only more candidate rows (and
then the table of the winners' ballot numbers). Each page is read as ``nebe_scan.page`` reads a House form; a page
without an accounting table is read for every table of three columns (ballot number, candidate, votes) instead.

Writes ``ocr.jsonl`` in a work folder, one record per page; ``nebe_rc_reconcile`` joins it with the vision readings.
Run as ``uv run python -m ea.sources.et.nebe_rc_scan <pages> <out.jsonl>``, where ``<pages>`` holds one folder of
200 dpi greyscale page images per form file, named ``<poll>_<region>_rc``.
"""

import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

from ea.sources.et import nebe_scan


def continuation(path: Path) -> dict[str, object]:
    """Every three-column table on a page that continues a form, read row by row."""
    languages = "eng" if any(region in path.parent.name for region in nebe_scan.LATIN) else "amh+eng"
    original = Image.open(path).convert("L")
    record: dict[str, object] = {"file": path.parent.name, "page": int(path.stem), "size": original.size}
    image, angle = nebe_scan.level(original)
    record["angle"] = round(angle, 2)
    found = []
    for rows, cols in nebe_scan._tables(image):
        if len(cols) != 4 or len(rows) < 2:
            continue
        options = []
        for top, bottom in itertools.pairwise(rows):
            number = (cols[0], top, cols[1], bottom)
            name = (cols[1], top, cols[2], bottom)
            votes = (cols[2], top, cols[3], bottom)
            options.append(
                {
                    "number": nebe_scan._digits(image, number),
                    "name": nebe_scan._text(image, name, languages),
                    "votes": nebe_scan._digits(image, votes),
                    "box": nebe_scan._share(votes, angle, original.size),
                }
            )
        found.append(options)
    record["continued"] = found
    return record


def page(path: Path) -> dict[str, object]:
    try:
        record = nebe_scan.page(path)
    except ValueError as error:  # a degenerate table on a skewed photograph
        return {"file": path.parent.name, "page": int(path.stem), "error": str(error)}
    if record.get("error") == "no accounting table":
        return continuation(path)
    return record


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
        key=lambda p: (len(list(p.parent.glob("*.png"))), p.parent.name, int(p.stem)),
    )
    with ProcessPoolExecutor(4) as pool, out.open("a") as file:
        for record in pool.map(page, pages):
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()


if __name__ == "__main__":
    main()
