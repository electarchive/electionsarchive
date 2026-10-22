import csv
import difflib
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.wi_gen_9498"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-026"
PUBLISHER = "Wisconsin Elections Board"
BOOKS = {
    "1995-96": ("AYPHDP7TNI56WH8E", date(1994, 11, 8), "Wisconsin Blue Book 1995-1996"),
    "1997-98": ("AV57G6QVOUSNPG8T", date(1996, 11, 5), "Wisconsin Blue Book 1997-1998"),
    "1999-2000": ("A4K4QRBLSRUNJE8R", date(1998, 11, 3), "Wisconsin Blue Book 1999-2000"),
}
PAGE_LIST = {
    "1995-96": {916: 1, 917: 6, 918: 9},
    "1997-98": {881: 1, 882: 4, 883: 8},
    "1999-2000": {862: 1, 863: 4, 864: 8},
}
OVERRIDES: dict[tuple[int, int, str, int], int] = {}
SUMMARY = "Wisconsin's U.S. House general elections of 1994, 1996, and 1998 by county, read from page scans of the Wisconsin Blue Books"
METHOD = (
    "County totals are read from page scans of the Wisconsin Blue Book's vote tables, saved from the UW Digital Collections by a "
    "person in a browser. Each candidate's county sum equals the district total printed on the page, and each value carries its page box. "
    "Scattered votes are omitted in the book."
)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*")
CONFUSABLE = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8", "'": ","})


def url(book: str, page: int) -> str:
    return f"https://search.library.wisc.edu/digital/{BOOKS[book][0]}?page={page}"


def source_name(book: str, page: int) -> str:
    return f"WI-Gen-Blue-Book-{book}_p{page}.jpg"


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("st.", "saint"))


def fetch() -> None:
    for book, pages in PAGE_LIST.items():
        for page in pages:
            try:
                raw.cached(SOURCE, url(book, page))
            except LookupError:
                raw.store(
                    SOURCE,
                    url(book, page),
                    (INBOX / source_name(book, page)).read_bytes(),
                    suffix=".jpg",
                    retrieved_at=datetime(2026, 10, 22, tzinfo=UTC),
                    media_type="image/jpeg",
                )


def clean(path: Path, target: Path) -> None:
    image = np.array(Image.open(path).convert("L"))
    dark = image < 150
    out = image.copy()
    for y in range(dark.shape[0]):
        edges = np.diff(np.concatenate([[0], dark[y].astype(np.int8), [0]]))
        for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0], strict=True):
            if end - start >= 220:
                out[y, start:end] = 255
    for x in np.where(dark.sum(axis=0) > 2500)[0]:
        out[:, x] = 255
    half = Image.fromarray(out)
    half.resize((half.width // 2, half.height // 2), Image.LANCZOS).save(target)


def words(path: Path) -> list[tuple[int, int, int, int, str]]:
    found: list[tuple[int, int, int, int, str]] = []
    with tempfile.TemporaryDirectory() as folder:
        small = Path(folder) / "half.png"
        clean(path, small)
        for mode in (4, 11):
            subprocess.run(["tesseract", str(small), str(Path(folder) / f"o{mode}"), "--psm", str(mode), "-l", "eng", "tsv"], capture_output=True, check=True)
            with (Path(folder) / f"o{mode}.tsv").open() as handle:
                for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
                    text = (row["text"] or "").strip()
                    if text and float(row["conf"]) >= 0:
                        found.append((int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"]), text))
    kept: list[tuple[int, int, int, int, str]] = []
    for word in sorted(found, key=lambda w: (w[1], w[0])):
        if any(abs(word[0] - k[0]) < 12 and abs(word[1] - k[1]) < 10 and word[4] == k[4] for k in kept):
            continue
        kept.append(word)
    return kept


def number(text: str) -> int | None:
    cleaned = re.sub(r"[^\w,]", "", text.translate(CONFUSABLE)).rstrip(",")
    return int(cleaned.replace(",", "")) if NUMBER.fullmatch(cleaned) else None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        counties = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    problems: list[str] = []
    for book, pages in PAGE_LIST.items():
        year = BOOKS[book][1].year
        for page, first in pages.items():
            path = raw.cached(SOURCE, url(book, page)).path
            ws = sorted(words(path), key=lambda w: w[1] + w[3] / 2)
            lines: list[list[tuple[int, int, int, int, str]]] = []
            centers: list[float] = []
            for word in ws:
                center = word[1] + word[3] / 2
                if lines and abs(center - centers[-1]) <= 9:
                    lines[-1].append(word)
                    centers[-1] = (centers[-1] + center) / 2
                else:
                    lines.append([word])
                    centers.append(center)
            district = first - 1
            body: list[tuple[str, list[tuple[int, int, int, int, str]]]] = []
            label = ""
            pending_total = False
            for line in lines:
                line.sort()
                text = " ".join(w[4] for w in line)
                if re.search(r"Congressional|^\W*District\b", text) and not re.search(r"CONGRESS|Source|VOTE", text) and not re.search(r"\d", text):
                    if re.search(r"Congressional", text) and not (body and not any(w[4].upper() == "TOTAL" for _, ln in body for w in ln)):
                        district += 1
                        body, label, pending_total = [], "", False
                    continue
                numeric = [w for w in line if w[0] > 700 and number(w[4]) is not None]
                names = [w[4] for w in line if w[0] <= 700 and re.search(r"[A-Za-z]{3,}", w[4])]
                if any(w[4].upper() == "TOTAL" for w in line):
                    pending_total = True
                if pending_total and numeric:
                    edges = {}
                    for word in numeric:
                        value = number(word[4])
                        edges.setdefault(round((word[0] + word[2]) / 25), []).append((word, value))
                    columns = sorted(edges.items())
                    printed = [max(items, key=lambda item: len(str(item[1])))[1] for _, items in columns]
                    right = [max(w[0] + w[2] for w, _ in items) for _, items in columns]
                    table: dict[tuple[str, int], tuple[int, list[int]]] = {}
                    for county_label, row in body:
                        code = None
                        words_in = re.sub(r"\(.*?\)|\.{2,}.*", " ", county_label)
                        guess = key(words_in)
                        if len(guess) >= 3:
                            best = difflib.get_close_matches(guess, list(counties), n=1, cutoff=0.6)
                            if not best:
                                best = [name for name in counties if guess.endswith(name) or guess.startswith(name)][:1]
                            code = counties[best[0]] if best else None
                        if code is None:
                            problems.append(f"{year} d{district}: unknown county {county_label!r}")
                            continue
                        cells: dict[int, tuple[int, list[int]]] = {}
                        for word in sorted((w for w in row if w[0] > 700 and number(w[4]) is not None), key=lambda w: w[0]):
                            column = min(range(len(right)), key=lambda i: abs(right[i] - (word[0] + word[2])))
                            if abs(right[column] - (word[0] + word[2])) > 40:
                                continue
                            value = number(word[4])
                            if column not in cells or len(str(value)) > len(str(cells[column][0])):
                                cells[column] = (value, [word[0] * 2, word[1] * 2, (word[0] + word[2]) * 2, (word[1] + word[3]) * 2])
                        for column, (value, box) in cells.items():
                            value = OVERRIDES.get((year, district, code, column), value)
                            rows.append({"year": year, "district": district, "county": code, "column": column, "votes": value, "printed": printed[column], "page": page, "book": book, "box": box})
                    for column in range(len(right)):
                        mine = [r for r in rows if r["year"] == year and r["district"] == district and r["column"] == column]
                        if sum(int(r["votes"]) for r in mine) != printed[column]:
                            problems.append(f"{year} d{district} col{column}: sum {sum(int(r['votes']) for r in mine)} printed {printed[column]} ({len(mine)} counties: {sorted(str(r['county']) for r in mine)})")
                    body, label, pending_total = [], "", False
                    continue
                if pending_total:
                    continue
                if names and numeric:
                    body.append((" ".join(names), line))
                    label = ""
                elif names and not numeric and not re.search(r"\(Dem|\(Rep|County|Rep\.|Dem\.", text):
                    label = " ".join(names)
                elif numeric and label:
                    body.append((label, line))
                    label = ""
                elif numeric and body and not names:
                    pass
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print("\n".join(problems))
    print(frame.group_by("year", "district").agg(pl.col("county").n_unique(), pl.col("column").n_unique()).sort("year", "district"))
