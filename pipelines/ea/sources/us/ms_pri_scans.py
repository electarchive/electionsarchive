import re
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ms_scan
from ea.store import DATA, connect

SOURCE = "us.ms_pri_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
BASE = "https://www.sos.ms.gov/elections/electionResults/"
PUBLISHER = "Mississippi Secretary of State"
TITLE = "{party} primary certified results, {day:%B %-d, %Y}"
COUNTIES = 82
FILES: dict[tuple[int, str], tuple[str, date]] = {
    (2012, "democratic"): ("Democrat%20Certification_2012%20Primary.pdf", date(2012, 3, 13)),
}
GRIDDED = {
    (2012, "democratic"): {
        1: ("president", None, "OBAMA, BARACK"),
        3: ("senate", None, "GORE, ALBERT N., JR."),
        4: ("senate", None, "OATIS, WILL"),
        5: ("senate", None, "WEINER, ROGER"),
        7: ("house", 1, "MORRIS, BRAD"),
        9: ("house", 2, "MCTEER, HEATHER"),
        10: ("house", 2, "THOMPSON, BENNIE G."),
        12: ("house", 3, "BIGGS, CRYSTAL"),
    },
}
KEEP = {
    (2012, "democratic"): {("president", None), ("senate", None), ("house", 1), ("house", 2), ("house", 3)},
}
FIXES: dict[tuple[int, str, int, int, int], int] = {}


def url(year: int, party: str) -> str:
    return BASE + FILES[(year, party)][0]


def fetch() -> None:
    for year, party in FILES:
        raw.fetch(SOURCE, url(year, party), suffix=".pdf")


def geometry(image):
    ys, xs = ms_scan.rules(image)
    ys = [y for i, y in enumerate(ys) if not (i + 1 < len(ys) and ys[i + 1] - y < 30)]
    xs = [x for i, x in enumerate(xs) if not (i + 1 < len(xs) and xs[i + 1] - x < 70)]
    top = max(range(len(ys) - 1), key=lambda i: ys[i + 1] - ys[i]) + 1
    return ys[top - 1 : top + 1], ys[top:], xs


def parse() -> None:
    with connect() as conn:
        codes = sorted(
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.county' and code like '28%%'"
            ).fetchall()
        )
    if len(codes) != COUNTIES:
        raise ValueError(f"{len(codes)} counties")
    out: list[dict[str, object]] = []
    for (year, party), layout in GRIDDED.items():
        pdf = raw.cached(SOURCE, url(year, party)).path
        pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout)[1])
        county = 0
        rows: dict[int, list[tuple[int, int, list[int | None], tuple[int, int, int, int] | None, int, int]]] = {r: [] for r in layout}
        totals: dict[int, list[int | None]] = {}
        for page in range(2, pages + 1):
            image = ms_scan.render(pdf, page)
            height, width = image.shape
            _, grid_rows, xs = geometry(image)
            first = 1 if page == 2 else 0
            columns = list(range(first, len(xs) - 1))
            last = page == pages
            data_columns = columns[:-1] if last else columns
            grid = [
                (int(xs[c]), int(grid_rows[r]), int(xs[c + 1]), int(grid_rows[r + 1]))
                for r in layout
                for c in columns
            ]
            read = ms_scan.cells(image, grid)
            across = ms_scan.strips(image, grid_rows, xs, columns)
            read = [
                (across.get((c, r), []) + values, box)
                for (values, box), (r, c) in zip(read, [(r, c) for r in layout for c in columns], strict=True)
            ]
            for index, r in enumerate(layout):
                cells = read[index * len(columns) : (index + 1) * len(columns)]
                for offset, (values, box) in enumerate(cells[: len(data_columns)]):
                    rows[r].append((page, county + offset, values, box, width, height))
                if last:
                    totals[r] = cells[-1][0]
            county += len(data_columns)
        if county != COUNTIES:
            raise ValueError(f"{year} {party}: {county} counties")
        samples = []
        for cells in rows.values():
            for page, index, values, box, width, height in cells:
                seen = {v for v in values if v is not None}
                if box is not None and len(values) >= 6 and len(seen) == 1:
                    samples.append((len(str(next(iter(seen)))), box[2] - box[0]))
        slope, intercept = ms_scan.digit_model(samples)
        for r, (office, district, name) in layout.items():
            printed = [v for v in totals[r] if v is not None]
            total = max(set(printed), key=printed.count) if printed else None
            cells = rows[r]
            fixed = {
                i: FIXES[(year, party, page, r, i)]
                for i, (page, *_) in enumerate(cells)
                if (year, party, page, r, i) in FIXES
            }
            try:
                digits = [max(1, round((c[3][2] - c[3][0] - intercept) / slope)) if c[3] is not None else None for c in cells]
                values, open_cells = ms_scan.settle([c[2] for c in cells], total, fixed, digits)
            except ValueError as error:
                top = [max(set(v for v in c[2] if v is not None) or {0}, key=[x for x in c[2] if x is not None].count) for c in cells]
                print(year, party, "row", r, name, "held:", error, "total", total, "short by", total - sum(top))
                for i, c in enumerate(cells):
                    if len({v for v in c[2] if v is not None}) != 1 and c[3] is not None:
                        print("   ", f"{c[0]}/{r}/{i}", codes[c[1]], c[2])
                continue
            print(year, party, "row", r, name, "total", total, "settled", [(cells[i][0], i) for i in open_cells])
            for (page, index, _, box, width, height), value in zip(cells, values, strict=True):
                if value is None or box is None:
                    continue
                out.append(
                    {
                        "year": year,
                        "party": party,
                        "office": office,
                        "district": district,
                        "candidate": name,
                        "county": codes[index],
                        "votes": value,
                        "page": page,
                        "x0": box[0] / width,
                        "y0": box[1] / height,
                        "x1": box[2] / width,
                        "y1": box[3] / height,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).write_parquet(PARSED / "cells.parquet")
