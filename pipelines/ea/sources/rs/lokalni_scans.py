"""Turns the readings of the local commissions' scanned reports into ``lokalni_scans.json``.

Most reports in the register are scans with no text layer, or with a garbled one. Each report's figures were read from
its page images (rendered at 100 to 200 dpi), checked against the report's own arithmetic, and written as a reading:
one JSON object per report with the totals, each list's ballot number, name, votes and seats, and the page each figure
is printed on. ``build`` joins those readings to the register's records, chooses one report per unit and election day,
and finds the box of every figure on its page with Tesseract (the page rendered at 300 dpi, sparse text), so each
stored number keeps the page and the box it was read from. A figure Tesseract cannot find on its page keeps the whole
page as its box. ``ea.sources.rs.lokalni`` reads only the JSON this writes, so building the packages needs no OCR.

The readings are kept in ``lokalni_readings.json``. Run ``uv run python -m ea.sources.rs.lokalni_scans`` to write
``lokalni_scans.json`` again from them.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.rs import lokalni

DPI = 300
SCALE = 100 / DPI  # boxes are kept in pixels of the page rendered at 100 dpi
CITY_MUNICIPALITY_ROUNDS = {559265, 572417, 668735}
# readings left out: Preševo's 2024 report, whose scan drops the row of list 1 between its pages
INCOMPLETE = {"bea07d52829d"}
FIGURES = ("registered", "voters", "cast", "invalid", "valid")


WORK = raw.DATA / "work" / "rs.lokalni-ocr"


def words(path: Path, page: int) -> list[tuple[str, float, float, float, float]]:
    """The numbers Tesseract finds on one page, with their boxes at 100 dpi, kept in a work folder between runs."""
    kept = WORK / f"{path.stem}-{page}.json"
    if kept.exists():
        return [tuple(w) for w in json.loads(kept.read_text())]  # type: ignore[misc]
    found = _words(path, page)
    WORK.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(found))
    return found


def _words(path: Path, page: int) -> list[tuple[str, float, float, float, float]]:
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-f", str(page), "-l", str(page), "-png", str(path), f"{work}/p"],
            check=True,
        )
        image = next(Path(work).glob("p-*.png"))
        found: list[tuple[str, float, float, float, float]] = []
        for mode in ("11", "6"):
            out = subprocess.run(
                ["tesseract", str(image), "-", "-l", "srp", "--psm", mode, "tsv"],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            ).stdout
            for line in out.splitlines()[1:]:
                cells = line.split("\t")
                if len(cells) < 12:
                    continue
                digits = re.sub(r"[.,\s]", "", cells[11])
                if not digits.isdigit():
                    continue
                left, top, width, height = (int(c) for c in cells[6:10])
                found.append(
                    (
                        str(int(digits)),
                        round(left * SCALE, 1),
                        round(top * SCALE, 1),
                        round((left + width) * SCALE, 1),
                        round((top + height) * SCALE, 1),
                    )
                )
        return found


def page_box(path: Path, page: int) -> list[float]:
    width, height = lokalni._sizes(path)[page]
    return [page, 0.0, 0.0, round(width * 100 / 72, 1), round(height * 100 / 72, 1)]


def locate(path: Path, items: list[tuple[int, int]]) -> list[list[float]]:
    """A box for each (page, value), in reading order: the first match below the previous figure on the page."""
    cache: dict[int, list[tuple[str, float, float, float, float]]] = {}
    out: list[list[float]] = []
    last: dict[int, float] = defaultdict(lambda: -1.0)
    for page, value in items:
        if page not in cache:
            cache[page] = sorted(set(words(path, page)), key=lambda w: (w[2], w[1]))
        hits = [w for w in cache[page] if w[0] == str(value)]
        below = [w for w in hits if w[2] > last[page] - 5]
        hit = (below or hits or [None])[0]
        if hit is None:
            out.append(page_box(path, page))
            continue
        last[page] = hit[2]
        out.append([page, hit[1], hit[2], hit[3], hit[4]])
    return out


def build(readings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_file: dict[str, tuple[lokalni.Record, str]] = {}
    index = json.loads((raw.DATA / "raw" / lokalni.SOURCE / "index.json").read_text())
    for round_id in lokalni.ROUNDS:
        for record in lokalni.records(round_id):
            for link in record.links:
                url = lokalni.document_url(link)
                if url in index:
                    by_file[str(index[url]["file"])] = (record, url)
    sheets: dict[tuple[int, str], str] = {}
    for record, url in by_file.values():
        if url.lower().endswith((".xls", ".xlsx")) and "БИРАЧК" in record.name.upper():
            sheets[(record.round_id, record.municipality)] = url
    chosen: dict[tuple[str, str], tuple[int, str, dict[str, Any]]] = {}
    for reading in readings:
        if reading.get("kind") not in ("total", "narrative") or not reading.get("lists"):
            continue
        if not all(isinstance(reading.get(f), int) for f in ("voters", "valid")):
            continue
        checks = reading.get("checks") or {}
        if reading["file"][:12] in INCOMPLETE:
            continue
        record, url = by_file[reading["file"]]
        go = record.round_id in CITY_MUNICIPALITY_ROUNDS or "градске општине" in record.bearer.lower()
        code = lokalni.code_of(record.municipality, record.round_id)
        day = reading["election_day"]
        rank = (2 if reading["kind"] == "total" else 0) + (1 if checks.get("invalid_plus_valid_equals_cast") else 0)
        score = rank * 10**10 + record.posted
        entry = {
            "reading": reading,
            "code": code,
            "tier": "go" if go else "jls",
            "url": url,
            "stations": sheets.get((record.round_id, record.municipality)),
        }
        if (code, day) not in chosen or chosen[(code, day)][0] < score:
            chosen[(code, day)] = (score, url, entry)
    out: dict[str, dict[str, Any]] = {}

    def one(item: tuple[tuple[str, str], tuple[int, str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
        (code, day), (_, url, entry) = item
        r = entry["reading"]
        path = raw.cached(lokalni.SOURCE, url).path
        pages = r.get("pages") or {}
        figures = [f for f in FIGURES if isinstance(r.get(f), int)]
        items = [(int(pages.get(f) or 1), int(r[f])) for f in figures]
        items += [(int(row.get("page") or 1), int(row["votes"])) for row in r["lists"]]
        boxes = locate(path, items)
        totals = {f: [r[f], boxes[i]] for i, f in enumerate(figures)}
        lists = [
            {
                "n": row["n"],
                "name": " ".join(row["name"].split()),
                "votes": [row["votes"], boxes[len(figures) + i]],
                "seats": row.get("seats"),
            }
            for i, row in enumerate(r["lists"])
        ]
        return url, {
            "code": code,
            "tier": entry["tier"],
            "body": " ".join(str(r.get("body") or "").split()),
            "day": day,
            "file": r["file"],
            "totals": totals,
            "lists": lists,
            "repeat_vote": bool(r.get("repeat_vote")),
            "stations": entry["stations"],
            "reading_notes": r.get("notes") or "",
        }

    with ThreadPoolExecutor(4) as pool:
        for url, entry in pool.map(one, sorted(chosen.items())):
            out[url] = entry
            found = sum(
                b[1] + b[2] > 0
                for b in [*(v[1] for v in entry["totals"].values()), *(r["votes"][1] for r in entry["lists"])]
            )
            print(
                f"{entry['day']} {entry['code']}: {found}/{len(entry['totals']) + len(entry['lists'])} boxes",
                flush=True,
            )
    return out


READINGS = Path(__file__).with_name("lokalni_readings.json")
TABLES = Path(__file__).with_name("lokalni_tables.json")


def attach(out: dict[str, dict[str, Any]], tables: list[dict[str, Any]]) -> None:
    """Add each polling-station table whose columns sum exactly to its report's totals to that report."""
    index = json.loads((raw.DATA / "raw" / lokalni.SOURCE / "index.json").read_text())
    files = {str(e["file"])[:12]: url for url, e in index.items()}
    by_unit = {(e["code"], e["day"]): e for e in out.values()}
    records = {
        lokalni.document_url(link): record
        for round_id in lokalni.ROUNDS
        for record in lokalni.records(round_id)
        for link in record.links
    }
    for table in tables:
        if table.get("status") != "ok" or not table.get("stations"):
            continue
        url = files[table["file"][:12]]
        record = records[url]
        entry = by_unit.get((lokalni.code_of(record.municipality, record.round_id), table["election_day"]))
        if entry is None:
            continue
        numbers = {row["n"] for row in entry["lists"]}
        rows = table["stations"]
        sums = {n: sum(int(r["lists"].get(str(n)) or 0) for r in rows) for n in numbers}
        if any(sums[row["n"]] != row["votes"][0] for row in entry["lists"]):
            continue
        if len({r["number"] for r in rows}) != len(rows):
            continue
        entry["table"] = [
            {
                "number": int(r["number"]),
                "name": " ".join(r["name"].split()) if r.get("name") else None,
                "page": r.get("page"),
                **{k: r.get(k) for k in FIGURES},
                "lists": {str(n): int(v) for n, v in r["lists"].items() if v is not None},
            }
            for r in rows
        ]
        entry["table_url"] = url


def main(paths: list[str]) -> None:
    readings: list[dict[str, Any]] = []
    for path in paths or [str(READINGS)]:
        readings += json.loads(Path(path).read_text())
    out = build(readings)
    if TABLES.exists():
        attach(out, json.loads(TABLES.read_text()))
    lokalni.SCANS.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
