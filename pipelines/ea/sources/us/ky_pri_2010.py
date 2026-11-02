import csv
import difflib
import itertools
import re
import statistics
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ky_pri_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
OCR = PARSED / "ocr"
INBOX = DATA / "inbox" / "manual" / "MF-436"
NAME = "off2010pri.pdf"
URL = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2010-2019/2010/off2010pri.pdf"
DAY = date(2010, 5, 18)
PUBLISHER = "Kentucky Secretary of State"
TITLE = "Official 2010 primary election results"
SECTIONS = {
    ("senate", 0, "republican"): {
        "Rand Paul": 206986,
        'C.M. "Trey" Grayson': 124864,
        "Bill Johnson": 7861,
        "John Stephenson": 6885,
        "Gurley L. Martin": 2850,
        "Jon J. Scribner": 2829,
    },
    ("senate", 0, "democratic"): {
        "Jack Conway": 229433,
        "Daniel Mongiardo": 225260,
        "Darlene F. Price": 28531,
        "James Buckmaster": 20561,
        "Maurice M. Sweeney": 17874,
    },
    ("house", 3, "republican"): {
        "Todd Lally": 22048,
        "Larry Hausman": 10674,
        "Jeff Reetz": 7167,
        "Brooks Wicker": 2595,
    },
    ("house", 5, "democratic"): {'James E. "Jim" Holbert': 27555, "Kenneth Stepp": 23320, "David Prince": 16331},
    ("house", 6, "republican"): {
        'Garland "Andy" Barr': 31255,
        "Mike Templeman": 4789,
        "Matt Lockett": 4070,
        "John T. Kemper III": 3454,
        "George Pendergrass": 3438,
        "Perry Wilson Barnes": 1880,
    },
}
PAGE_RANGE = range(2, 14)
HAND_FILE = Path(__file__).with_name("ky_pri_2010_hand.tsv")
METHOD = (
    "County figures were transcribed from the Secretary of State's scanned official results and checked against "
    "optical character recognition of the page image, which located each figure's row and column. Every column's "
    "county figures sum to the contest's state total printed on the page, and each figure stores its page and "
    "bounding box."
)


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            (INBOX / NAME).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp((INBOX / NAME).stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def hand() -> dict[tuple[str, int, str], dict[str, tuple[int, list[int]]]]:
    found: dict[tuple[str, int, str], dict[str, tuple[int, list[int]]]] = {}
    with HAND_FILE.open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            key = (row["chamber"], int(row["district"]), row["party"])
            found.setdefault(key, {})[norm(row["county"])] = (
                int(row["page"]),
                [int(v) for v in row["votes"].split(",")],
            )
    return found


def best(text: str) -> int | None:
    found = scan.numbers(re.sub(r"^\D+|\D+$", "", text))
    return max(found.items(), key=lambda kv: kv[1])[0] if found else None


def read(pdf: Path) -> dict[tuple[str, int, str], list[dict]]:
    OCR.mkdir(parents=True, exist_ok=True)
    sections: dict[tuple[str, int, str], list[dict]] = {key: [] for key in SECTIONS}
    current: tuple[str, int, str] | None = None
    chamber = None
    district = 0
    for number in PAGE_RANGE:
        words = scan.words(pdf, number, OCR / f"{number}.tsv")
        slope = scan.skew(words)
        for line in scan.lines(words, slope, 14):
            joined = " ".join(w[0] for w in line)
            if "US Senator" in joined:
                chamber, district = "senate", 0
            if found := re.search(r"(\d)(?:st|nd|rd|th)\s+Congressional", joined):
                chamber, district = "house", int(found[1])
            if chamber is not None and "Republican Party" in joined:
                current = (chamber, district, "republican")
            elif chamber is not None and "Democratic Party" in joined:
                current = (chamber, district, "democratic")
            if current is None or current not in sections or not re.fullmatch(r"[A-Za-z'.]{3,}", line[0][0]):
                continue
            label = line[0]
            cells = [w for w in line[1:] if re.search(r"\d", w[0]) and w[1][0] > label[1][2] + 40]
            if cells:
                name = label[0] if label[0] != "Total" else "Total"
                sections[current].append({"page": number, "label": name, "cells": cells, "box": label[1]})
    return sections


def parse() -> None:
    pdf = raw.cached(SOURCE, URL).path
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%%'"""
            ).fetchall()
        }
    names = sorted(codes)
    frames: list[dict[str, Any]] = []
    for (chamber, district, party), rows in read(pdf).items():
        candidates = list(SECTIONS[(chamber, district, party)])
        totals = list(SECTIONS[(chamber, district, party)].values())
        width = len(candidates)
        full = [row for row in rows if len(row["cells"]) == width]
        anchors = [statistics.median(row["cells"][i][1][2] for row in full) for i in range(width)]
        table: list[dict] = []
        used: set[str] = set()
        for row in rows:
            if row["label"] == "Total":
                continue
            match = max(
                (n for n in names if n not in used),
                key=lambda n: difflib.SequenceMatcher(None, n, re.sub(r"[^a-z]", "", row["label"].lower())).ratio(),
            )
            ratio = difflib.SequenceMatcher(None, match, re.sub(r"[^a-z]", "", row["label"].lower())).ratio()
            if ratio < 0.6:
                print(f"{chamber} {district} {party} page {row['page']}: row {row['label']} matches no county")
                continue
            used.add(match)
            cells: dict[int, tuple[str, scan.Box]] = {}
            for word in row["cells"]:
                column = min(range(width), key=lambda i: abs(anchors[i] - word[1][2]))
                text = (cells[column][0] if column in cells else "") + re.sub(r"^\D+|\D+$", "", word[0])
                box = word[1] if column not in cells else (cells[column][1][0], *word[1][1:])
                cells[column] = (text, box)
            table.append({"county": match, "page": row["page"], "box": row["box"], "cells": cells})
        manual = hand().get((chamber, district, party), {})
        for county, (page, _) in manual.items():
            if county not in codes:
                raise KeyError(county)
            if county not in {row["county"] for row in table}:
                before = max(
                    (r for r in table if r["page"] == page and r["county"] < county),
                    key=lambda r: r["county"],
                    default=None,
                )
                pitch = (
                    statistics.median(b["box"][1] - a["box"][1] for a, b in itertools.pairwise(table))
                    if len(table) > 1
                    else 40
                )
                anchor = before["box"] if before else table[0]["box"]
                shift = int(pitch) if before else 0
                box = (anchor[0], anchor[1] + shift, anchor[2], anchor[3] + shift)
                table.append({"county": county, "page": page, "box": box, "cells": {}})
        values: dict[tuple[str, int], tuple[int, scan.Box]] = {}
        halves = [
            (
                int((anchors[i - 1] + anchors[i]) / 2) if i else int(anchors[0] - 140),
                int((anchors[i] + anchors[i + 1]) / 2) if i + 1 < width else int(anchors[i] + 90),
            )
            for i in range(width)
        ]
        for row in table:
            seen = [box for _, box in row["cells"].values()]
            top = int(statistics.median(box[1] for box in seen)) if seen else row["box"][1]
            bottom = int(statistics.median(box[3] for box in seen)) if seen else row["box"][3]
            for column in range(width):
                cell = row["cells"].get(column)
                first = best(cell[0]) if cell else None
                values[(row["county"], column)] = (
                    -1 if first is None else first,
                    cell[1] if cell else (halves[column][0], top, halves[column][1], bottom),
                )
        for row in table:
            if row["county"] not in manual:
                continue
            seen = [box for _, box in row["cells"].values()]
            top = int(statistics.median(box[1] for box in seen)) if seen else row["box"][1]
            bottom = int(statistics.median(box[3] for box in seen)) if seen else row["box"][3]
            for column, value in enumerate(manual[row["county"]][1]):
                ocr_value, box = values[(row["county"], column)]
                values[(row["county"], column)] = (
                    value,
                    box if ocr_value == value else (halves[column][0], top, halves[column][1], bottom),
                )
        for row in table:
            for column in range(width):
                votes, box = values[(row["county"], column)]
                frames.append(
                    {
                        "year": 2010,
                        "chamber": f"{chamber}:{party}",
                        "day": DAY,
                        "special": False,
                        "state": "KY",
                        "district": district or None,
                        "code": codes[row["county"]],
                        "candidate": candidates[column],
                        "measure": 1,
                        "writein": False,
                        "votes": votes,
                        "kind": "county",
                        "source": "KY",
                        "file": 0,
                        "page": row["page"],
                        "x0": box[0],
                        "y0": box[1],
                        "x1": box[2],
                        "y1": box[3],
                    }
                )
        sums = [
            sum(
                f["votes"]
                for f in frames
                if f["chamber"] == f"{chamber}:{party}" and f["district"] == (district or None) and f["candidate"] == c
            )
            for c in candidates
        ]
        print(chamber, district, party, "counties", len(table), "sums match totals:", sums == totals, sums, totals)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(frames, infer_schema_length=None).with_columns(
        year=pl.col("year").cast(pl.Int32),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2010 U.S. Senate and House primaries by county, read from the Secretary of State's scanned official results",
            polity="US-KY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elect.ky.gov/results/2010-2019/Pages/2010primaryandgeneralelectionresults.aspx",
            license="Public record",
        )
        pdf = raw.cached(SOURCE, URL)
        artifact = cs.artifact(pdf, source)
        pages: dict[int, str] = {}
        sizes: dict[int, tuple[int, int]] = {}
        for number in sorted(frame["page"].unique().to_list()):
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            page = cs.page(artifact, number)
            pages[number] = str(page)
            sizes[number] = (width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        frame = frame.with_columns(
            page_id=pl.col("page").map_elements(lambda n: pages[n], return_dtype=pl.String),
            **{
                edge: pl.struct("page", edge).map_elements(
                    lambda r, edge=edge, axis=axis: r[edge] / sizes[r["page"]][axis],
                    return_dtype=pl.Float64,
                )
                for edge, axis in (("x0", 0), ("y0", 1), ("x1", 0), ("y1", 1))
            },
        )
        medsl.attach(cs, conn, frame, {"KY": source}, strict=True, restage=True)
        return cs.write()
