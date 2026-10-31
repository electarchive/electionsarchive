import csv
import io
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import ModuleType

import numpy as np
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.sources.us import house_books

SOURCE = "us.mt_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Montana Secretary of State"
TITLE = "1994 Statewide General Canvass, November 8, 1994"
LICENSE = "Public record"
SUMMARY = "Montana's House results by county from the Secretary of State's statewide canvass on the SRI microfiche"
STATE = "MT"
FIPS = "30"
ITEMS = {
    1994: ("micro_IA40706953_0045", date(1994, 11, 8), 1),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, [page])
    for year, (item, day, page) in ITEMS.items()
}
REVISION = 1
RENAMED: str | None = None
TURNED: set[tuple[int, int]] = set()
NAMES = {("MT", "Lewis & Clark"): "Lewis and Clark"}


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def locate(source: ModuleType) -> None:
    parsed = raw.parsed(source.SOURCE)
    rows: list[dict[str, object]] = []
    for year, (_, _, _, (page,)) in source.VOLUMES.items():
        first = house_books._tables(parsed / "model" / f"{year}.txt")
        if first != house_books._tables(parsed / "model" / f"{year}.v2.txt"):
            raise ValueError(f"{year}: the two reads disagree")
        (table,) = first
        lines = [(county, votes) for county, votes in table["rows"] if county != "Totals"]
        totals = next(votes for county, votes in table["rows"] if county == "Totals")
        for c, label in enumerate(table["columns"]):
            if sum(votes[c] for _, votes in lines) != totals[c]:
                raise ValueError(f"{year} {label}: the counties do not add up to {totals[c]}")
        book = house_books._artifact(source, year)
        tokens: list[tuple[str, tuple[int, int, int, int]]] = []
        words: list[tuple[str, tuple[int, int, int, int]]] = []
        for psm in ("6", "11"):
            for threshold in (None, 150, 190, 230):
                cache = (
                    parsed / "ocr" / f"{book.sha256}-{page}-300-{psm}{'' if threshold is None else f'-{threshold}'}.tsv"
                )
                if not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as folder:
                        image = Path(folder) / "page.png"
                        house_books._render(book, page, 300, image, False)
                        if threshold is not None:
                            with Image.open(image) as rendered:
                                rendered.point(lambda v, cut=threshold: 255 if v > cut else 0).save(image)
                        cache.write_text(
                            subprocess.run(
                                ["tesseract", str(image), "stdout", "--psm", psm, "tsv"],
                                capture_output=True,
                                text=True,
                                check=True,
                            ).stdout
                        )
                for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
                    if row["level"] != "5" or not row["text"].strip():
                        continue
                    left, top = int(row["left"]), int(row["top"])
                    box = (left, top, left + int(row["width"]), top + int(row["height"]))
                    if plain := house_books._plain(row["text"]):
                        words.append((plain, box))
                    if re.search(r"\d", row["text"]):
                        for digits in {
                            re.sub(r"\D", "", row["text"]),
                            re.sub(r"\D", "", row["text"].translate(scan.CONFUSABLE)),
                        }:
                            tokens.append((digits, box))
        width = max(box[2] for _, box in words)
        anchors: list[tuple[int, float]] = []
        for k, (county, _) in enumerate(lines):
            last = house_books._plain(county.split()[-1])
            found = [box for name, box in words if name == last and box[0] < 0.2 * width]
            if found:
                anchors.append((k, (found[0][1] + found[0][3]) / 2))
        indexes = np.array([k for k, _ in anchors], dtype=float)
        heights = np.array([y for _, y in anchors])
        slope, offset = np.polyfit(indexes, heights, 1)
        for _ in range(2):
            keep = np.abs(heights - (slope * indexes + offset)) < 0.4 * abs(slope)
            slope, offset = np.polyfit(indexes[keep], heights[keep], 1)
        pitch = abs(slope)
        centers: dict[int, float] = {}
        for c in range(len(table["columns"])):
            seen = [
                (box[0] + box[2]) / 2
                for k, (_, votes) in enumerate(lines)
                for digits, box in tokens
                if digits == str(votes[c]) and abs((box[1] + box[3]) / 2 - (slope * k + offset)) < 0.35 * pitch
            ]
            if not seen:
                raise ValueError(f"{year} column {c}: no value found")
            seen.sort()
            centers[c] = seen[len(seen) // 2]
        spacing = min(abs(centers[a] - centers[b]) for a in centers for b in centers if a < b)
        half = 0.45 * spacing
        grid: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        for c, center in centers.items():
            for k, (_, votes) in enumerate(lines):
                near = [
                    box
                    for digits, box in tokens
                    if digits == str(votes[c])
                    and abs((box[1] + box[3]) / 2 - (slope * k + offset)) < 0.35 * pitch
                    and abs((box[0] + box[2]) / 2 - center) < half
                ]
                if near:
                    grid[(c, k)] = min(near, key=lambda box: box[2] - box[0])
        for c, label in enumerate(table["columns"]):
            rights = [box[2] for (d, _), box in grid.items() if d == c]
            right = int(np.median(rights))
            digit = float(np.median([(box[2] - box[0]) / len(str(lines[k][1][d])) for (d, k), box in grid.items()]))
            high = float(np.median([box[3] - box[1] for box in grid.values()]))
            for k, (county, votes) in enumerate(lines):
                box, located = grid.get((c, k)), "row"
                if box is None:
                    middle = slope * k + offset
                    span = digit * (len(f"{votes[c]}") + 0.3)
                    box = (round(right - span), round(middle - high / 2), right, round(middle + high / 2))
                    located = "placed"
                rows.append(
                    {
                        "year": year,
                        "page": page,
                        "district": 0,
                        "county": county,
                        "column": c,
                        "label": label,
                        "votes": votes[c],
                        "box": list(box),
                        "located": located,
                    }
                )
    frame = pl.DataFrame(rows)
    print(frame.group_by("year", "located").len().sort("year", "located"))
    frame.write_parquet(parsed / "values.parquet")


def parse() -> None:
    locate(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
