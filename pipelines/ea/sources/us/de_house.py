import csv
import io
import re
import statistics
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl
from PIL import Image

from ea import scan
from ea.sources.us import house_books
from ea.store import DATA

SOURCE = "us.de_house"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Delaware Commissioner of Elections"
TITLE = "State of Delaware Official Results of General Election, {day:%Y}"
LICENSE = "Public record"
SUMMARY = "Delaware's House results by county from the Commissioner of Elections' official results"
STATE = "DE"
FIPS = "10"
ITEMS = {
    1978: ("micro_IA40706902_0148", date(1978, 11, 7), 3),
    1980: ("micro_IA40706905_0233", date(1980, 11, 4), 3),
    1982: ("micro_IA40706912_0200", date(1982, 11, 2), 3),
    1984: ("micro_IA40706918_0386", date(1984, 11, 6), 3),
    1986: ("micro_IA40706925_0143", date(1986, 11, 4), 2),
    1988: ("micro_IA40706931_0338", date(1988, 11, 8), 2),
    1990: ("micro_IA40706938_0316", date(1990, 11, 6), 2),
    1992: ("micro_IA40706945_0235", date(1992, 11, 3), 2),
    1994: ("micro_IA40706952_0239", date(1994, 11, 8), 1),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, [page])
    for year, (item, day, page) in ITEMS.items()
}
COUNTIES = {"New Castle": "New Castle", "Wilmington": "New Castle", "Rural New Castle": "New Castle"}
HEADS = {
    "New Castle": "castle",
    "Wilmington": "wilmington",
    "Rural New Castle": "rural",
    "Kent": "kent",
    "Sussex": "sussex",
}
REVISION = 1
RENAMED: str | None = None
TURNED: set[tuple[int, int]] = set()


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    parsed = DATA / "parsed" / SOURCE
    rows: list[dict[str, object]] = []
    for year, (_, _, _, (page,)) in VOLUMES.items():
        first = house_books._tables(parsed / "model" / f"{year}.txt")
        if first != house_books._tables(parsed / "model" / f"{year}.v2.txt"):
            raise ValueError(f"{year}: the two reads disagree")
        (table,) = first
        lines = [(county, votes) for county, votes in table["rows"] if county != "Totals"]
        totals = next(votes for county, votes in table["rows"] if county == "Totals")
        for c, label in enumerate(table["columns"]):
            if sum(votes[c] for _, votes in lines) != totals[c]:
                raise ValueError(f"{year} {label}: the counties do not add up to {totals[c]}")
        book = house_books._artifact(sys.modules[__name__], year)
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
        heads = {
            county: min((box for name, box in words if name == HEADS[county]), key=lambda box: box[1])
            for county, _ in lines
        }
        grid: dict[int, list[tuple[int, int, int, int] | None]] = {}
        for c, label in enumerate(table["columns"]):
            surname = house_books._plain(label.split()[-1])
            grid[c] = [None] * len(lines)
            for _, name in [(name, box) for name, box in words if name == surname and box[2] < 0.35 * width]:
                middle = (name[1] + name[3]) / 2
                found = []
                for county, votes in lines:
                    center = (heads[county][0] + heads[county][2]) / 2
                    near = [
                        box
                        for digits, box in tokens
                        if digits == str(votes[c])
                        and abs((box[1] + box[3]) / 2 - middle) < 1.4 * (name[3] - name[1])
                        and abs((box[0] + box[2]) / 2 - center) < 0.6 * width / (len(lines) + 2)
                    ]
                    found.append(min(near, key=lambda box: box[2] - box[0]) if near else None)
                if sum(box is not None for box in found) > sum(box is not None for box in grid[c]):
                    grid[c] = found
        for c, label in enumerate(table["columns"]):
            row = [box for box in grid[c] if box is not None]
            if not row:
                raise ValueError(f"{year} {label}: no box on its line")
            for k, (county, votes) in enumerate(lines):
                box, located = grid[c][k], "row"
                if box is None:
                    above = [grid[d][k] for d in grid if grid[d][k] is not None]
                    if not above:
                        raise ValueError(f"{year} {label} {county}: no box in its column")
                    right = max(b[2] for b in above)
                    digit = statistics.median(
                        (b[2] - b[0]) / len(f"{v[d]:,}")
                        for d in grid
                        for (_, v), b in zip(lines, grid[d])
                        if b is not None
                    )
                    top, bottom = min(b[1] for b in row), max(b[3] for b in row)
                    box, located = (round(right - digit * (len(f"{votes[c]:,}") + 0.5)), top, right, bottom), "placed"
                rows.append(
                    {
                        "year": year,
                        "page": page,
                        "district": 0,
                        "county": COUNTIES.get(county, county),
                        "column": c,
                        "label": label,
                        "votes": votes[c],
                        "box": list(box),
                        "located": located,
                    }
                )
    frame = pl.DataFrame(rows)
    print(frame.group_by("year").len().sort("year"))
    frame.write_parquet(parsed / "values.parquet")


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
