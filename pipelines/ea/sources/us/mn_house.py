import csv
import io
import re
import statistics
import sys
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw, scan
from ea.sources.us import house_books
from ea.store import DATA

SOURCE = "us.mn_house"
RAW = "us.mn_lrl"
VERSION = "1"
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Minnesota Election Results, {day:%Y}"
LICENSE = "Public record; the books carry no copyright notice, and the Legislative Reference Library posts no terms for its scans"
SUMMARY = "Minnesota's House results by county from the Secretary of State's results books"
STATE = "MN"
FIPS = "27"
ITEMS = {
    1972: (date(1972, 11, 7), range(11, 12)),
    1974: (date(1974, 11, 5), range(15, 16)),
    1976: (date(1976, 11, 2), range(7, 9)),
    1978: (date(1978, 11, 7), range(6, 8)),
    1980: (date(1980, 11, 4), range(6, 8)),
    1982: (date(1982, 11, 2), range(4, 6)),
    1984: (date(1984, 11, 6), range(4, 6)),
    1986: (date(1986, 11, 4), range(4, 6)),
    1988: (date(1988, 11, 8), range(4, 6)),
    1990: (date(1990, 11, 6), range(9, 11)),
    1992: (date(1992, 11, 3), range(9, 11)),
    1994: (date(1994, 11, 8), range(11, 13)),
    1996: (date(1996, 11, 5), range(13, 15)),
    1998: (date(1998, 11, 3), range(15, 19)),
}
VOLUMES = {
    year: (
        f"https://www.lrl.mn.gov/archive/sessions/electionresults/{day}-g-sec.pdf",
        "https://www.lrl.mn.gov/mngov/electionresults",
        day,
        pages,
    )
    for year, (day, pages) in ITEMS.items()
}
ERRATA = {(1976, 6), (1976, 7)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
COMMAS_YEARS = {1974, 1976, 1978, 1980, 1982}
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])
    path = DATA / "parsed" / SOURCE / "values.parquet"
    cells = pl.read_parquet(path).to_dicts()
    placed = {(cell["year"], cell["page"], cell["district"], cell["county"], cell["column"]): cell for cell in cells}
    for year, (url, _, _, pages) in VOLUMES.items():
        book = raw.cached(RAW, url)
        tokens: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
        for page in pages:
            for cache in (DATA / "parsed" / SOURCE / "ocr").glob(f"{book.sha256}-{page}-*.tsv"):
                scale = int(cache.name.split("-")[2]) / 300
                for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
                    if row["level"] == "5" and re.search(r"\d", row["text"]):
                        box = (
                            int(row["left"]) / scale,
                            int(row["top"]) / scale,
                            (int(row["left"]) + int(row["width"])) / scale,
                            (int(row["top"]) + int(row["height"])) / scale,
                        )
                        for digits in {
                            re.sub(r"\D", "", row["text"]),
                            re.sub(r"\D", "", row["text"].translate(scan.CONFUSABLE)),
                        }:
                            tokens.setdefault(page, []).append((digits, box))
        page, tables = 0, []
        for line in (DATA / "parsed" / SOURCE / "model" / f"{year}.txt").read_text().splitlines():
            if line.startswith("@"):
                page = int(line[1:])
            elif (head := line.split("|")[0].strip()).isdigit():
                tables.append((page, int(head), []))
            elif head != "Totals":
                tables[-1][2].append(head)
        for page, district, counties in tables:
            table = [
                [placed[key] for c in range(10) if (key := (year, page, district, county, c)) in placed]
                for county in counties
            ]
            trusted = [(i, cell) for i, row in enumerate(table) for cell in row if cell["located"] != "placed"]
            if not trusted:
                continue
            height = statistics.median(cell["box"][3] - cell["box"][1] for _, cell in trusted)
            digit = statistics.median(
                (cell["box"][2] - cell["box"][0])
                / len(f"{cell['votes']:,}" if year in COMMAS_YEARS else str(cell["votes"]))
                for _, cell in trusted
            )
            middles = [(i, (cell["box"][1] + cell["box"][3]) / 2) for i, cell in trusted]
            slopes = [(b - a) / (j - i) for i, a in middles for j, b in middles if j > i]
            pitch = statistics.median(slopes) if slopes else 0.0
            start_ = statistics.median(y - pitch * i for i, y in middles)
            rows: dict[int, float] = {}
            for i, row in enumerate(table):
                spots = [(cell["box"][1] + cell["box"][3]) / 2 for cell in row if cell["located"] != "placed"]
                support = max(([y for y in spots if abs(y - spot) < height / 2] for spot in spots), key=len, default=[])
                predicted = start_ + pitch * i
                if (len(support) >= 2 and (not pitch or abs(statistics.median(support) - predicted) < 2 * pitch)) or (
                    len(support) == 1 and (not pitch or abs(support[0] - predicted) < pitch / 2)
                ):
                    rows[i] = statistics.median(support)
            for i in range(len(table)):
                if i not in rows:
                    below, above = [k for k in rows if k < i], [k for k in rows if k > i]
                    rows[i] = (
                        rows[max(below)]
                        + (rows[min(above)] - rows[max(below)]) * (i - max(below)) / (min(above) - max(below))
                        if below and above
                        else rows[max(below)] + pitch * (i - max(below))
                        if below
                        else rows[min(above)] - pitch * (min(above) - i)
                        if above
                        else start_ + pitch * i
                    )
            found = {
                (i, cell["column"]): [
                    box
                    for digits, box in tokens.get(page, [])
                    if digits == str(cell["votes"]) and abs((box[1] + box[3]) / 2 - rows[i]) < height / 2
                ]
                for i, row in enumerate(table)
                for cell in row
            }
            columns: dict[int, float] = {}
            for c in sorted({cell["column"] for row in table for cell in row}):
                rights = [
                    box[2]
                    for (i, k), boxes in found.items()
                    if k == c
                    for box in boxes
                    if not columns or box[2] > max(columns.values()) + digit
                ]
                if rights:
                    columns[c] = statistics.median(
                        max(([r for r in rights if abs(r - spot) < 2 * digit] for spot in rights), key=len)
                    )
            for i, row in enumerate(table):
                for cell in row:
                    right = columns.get(cell["column"])
                    if right is None:
                        continue
                    near = sorted(
                        (box for box in found[(i, cell["column"])] if abs(box[2] - right) < 4 * digit),
                        key=lambda box: abs(box[2] - right),
                    )
                    if near:
                        cell["box"] = [round(v) for v in near[0]]
                        cell["located"] = "aligned" if cell["located"] == "placed" else cell["located"]
                    else:
                        width = digit * len(f"{cell['votes']:,}" if year in COMMAS_YEARS else str(cell["votes"]))
                        cell["box"] = [
                            round(right - width),
                            round(rows[i] - height / 2),
                            round(right),
                            round(rows[i] + height / 2),
                        ]
                        cell["located"] = "placed"
    frame = pl.DataFrame(cells, infer_schema_length=None)
    print(frame.group_by("located").len().sort("located"))
    frame.write_parquet(path)


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
