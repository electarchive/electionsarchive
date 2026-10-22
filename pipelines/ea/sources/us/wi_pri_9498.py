import difflib
import re
import struct
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wi_pri_9498"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
OCR = PARSED / "ocr"
INBOX = DATA / "inbox" / "manual" / "MF-022"
PUBLISHER = "Wisconsin Elections Board"
PARTIES = {"D": "democratic", "R": "republican", "L": "libertarian", "T": "u-s-taxpayers"}
BOOKS = {
    "WI-Blue-Book-1995-96": ("AYPHDP7TNI56WH8E", date(1994, 9, 13), "Wisconsin Blue Book 1995-1996"),
    "WI-Blue-Book-1997-98": ("AV57G6QVOUSNPG8T", date(1996, 9, 10), "Wisconsin Blue Book 1997-1998"),
    "WI-Blue-Book-1999-2000": ("A4K4QRBLSRUNJE8R", date(1998, 9, 8), "Wisconsin Blue Book 1999-2000"),
}
SENATE = None
TABLES: dict[tuple[str, int], list[tuple[int | None, list[tuple[str, str]]]]] = {
    ("WI-Blue-Book-1995-96", 896): [
        (
            SENATE,
            [
                ("D", "Edmond Galileo Hou-Seye"),
                ("D", "Herb Kohl"),
                ("L", "James Dean"),
                ("R", "Thomas M. Fitzpatrick"),
                ("R", "Matthew Gunderson"),
                ("R", "Robert T. Welch"),
                ("R", "Cate Zeuske"),
            ],
        )
    ],
    ("WI-Blue-Book-1995-96", 897): [
        (1, [("D", "Peter W. Barca"), ("L", "Edward J. Kozak"), ("R", "Mark W. Neumann")]),
        (2, [("D", "Thomas C. Hecht"), ("R", "Scott L. Klug"), ("T", "John J. Stumpf")]),
        (
            3,
            [
                ("D", "Peter Bear"),
                ("D", "Karl Krueger"),
                ("D", "Lee Rasch"),
                ("D", "Harvey Stower"),
                ("R", "Donald M. Brill"),
                ("R", "Steve Gunderson"),
                ("T", "Chuck Lee"),
            ],
        ),
    ],
    ("WI-Blue-Book-1995-96", 898): [
        (4, [("D", "Gerald Kleczka"), ("R", "Tom Reynolds"), ("T", "James Harold Hause")]),
        (5, [("D", "Tom Barrett"), ("R", "Stephen B. Hollingshead"), ("R", "Paul D. Melotik")]),
        (6, [("R", "Thomas E. Petri")]),
        (7, [("D", "David R. Obey"), ("R", "Charles R. Wenzel"), ("R", "Scott West")]),
    ],
    ("WI-Blue-Book-1995-96", 899): [
        (8, [("D", "Stan Gruszynski"), ("R", "Nancy J. Nusbaum"), ("R", "Toby Roth")]),
        (9, [("R", "F. James Sensenbrenner, Jr.")]),
    ],
    ("WI-Blue-Book-1997-98", 878): [
        (
            1,
            [
                ("D", "Douglas La Follette"),
                ("D", "Jerry Maiers"),
                ("D", "Lydia Spottswood"),
                ("D", "Jeffrey C. Thomas"),
                ("R", "Mark W. Neumann"),
            ],
        ),
        (2, [("D", "Patrick J. O'Brien"), ("D", "Paul R. Soglin"), ("L", "Ben Masel"), ("R", "Scott L. Klug")]),
        (
            3,
            [
                ("D", "Tim Bakken"),
                ("D", "Ron Kind"),
                ("D", "Joe Monahan"),
                ("D", "Lee Rasch"),
                ("D", "Mark Weinhold"),
                ("R", "James E. Harsdorf"),
                ("R", "Myron Holley"),
            ],
        ),
    ],
    ("WI-Blue-Book-1997-98", 879): [
        (
            4,
            [
                ("D", "Roman R. Blenski"),
                ("D", "Jerry Kleczka"),
                ("R", "Mac Davis"),
                ("R", "Tom Reynolds"),
                ("R", "Gary G. Wetzel"),
            ],
        ),
        (5, [("D", "Tom Barrett"), ("R", "Paul D. Melotik"), ("T", "James D. Soderna")]),
        (6, [("D", "Al Lindskoog"), ("L", "James Dean"), ("R", "Thomas E. Petri"), ("T", "Timothy Farness")]),
        (
            7,
            [
                ("D", "David R. Obey"),
                ("R", "Don Schlomann"),
                ("R", "Charles R. Wenzel"),
                ("R", "Scott West"),
                ("R", "Dave Zien"),
            ],
        ),
    ],
    ("WI-Blue-Book-1997-98", 880): [
        (8, [("D", "Stan Gruszynski"), ("D", "Jay Johnson"), ("R", "Chuck Dettman"), ("R", "David Prosser")]),
        (9, [("D", "Floyd Brenholt"), ("R", "F. James Sensenbrenner, Jr.")]),
    ],
    ("WI-Blue-Book-1999-2000", 857): [
        (
            SENATE,
            [("D", "Russ Feingold"), ("L", "Tom Ender"), ("R", "Mark W. Neumann"), ("T", "Robert R. Raymond")],
        )
    ],
    ("WI-Blue-Book-1999-2000", 859): [
        (
            1,
            [
                ("D", "Lydia Carol Spottswood"),
                ("D", "Jeffrey C. Thomas"),
                ("R", "Michael J. Logan"),
                ("R", "Paul Ryan"),
            ],
        ),
        (
            2,
            [
                ("D", "Tammy Baldwin"),
                ("D", "Patrick J. O'Brien"),
                ("D", "Rick Phelps"),
                ("D", "Joseph S. Wineke"),
                ("R", "Meredith Bakke"),
                ("R", "Don Carrig"),
                ("R", "Nick Fuhrman"),
                ("R", "Ron Greer"),
                ("R", "Josephine W. Musser"),
                ("R", "John Sharpless"),
            ],
        ),
        (3, [("D", "Ron Kind"), ("R", "Troy A. Brechler")]),
    ],
    ("WI-Blue-Book-1999-2000", 860): [
        (4, [("D", "Roman R. Blenski"), ("D", "Jerry Kleczka"), ("R", "Tom Reynolds")]),
        (5, [("D", "Tom Barrett"), ("R", "Jack Melvin")]),
        (6, [("R", "Thomas E. Petri"), ("T", "Timothy J. Farness")]),
        (7, [("D", "David R. Obey"), ("R", "Scott West")]),
    ],
    ("WI-Blue-Book-1999-2000", 861): [
        (8, [("D", "Jay Johnson"), ("R", "Chuck Dettman"), ("R", "Mark Green")]),
        (9, [("R", "F. James Sensenbrenner, Jr."), ("T", "Anthony E. Deiss")]),
    ],
}
TITLE = re.compile(r"(?i)congressional\s+district|united\s+states\s+senator|^\W*county\b")
NUMBER = re.compile(r"^\W{0,2}\d[\d,]*\*?\W?$")
LEAD = re.compile(r"[A-Za-z][A-Za-z. ]*")


def url(book: str, page: int) -> str:
    return f"https://search.library.wisc.edu/digital/{BOOKS[book][0]}?page={page}"


def key(text: str) -> str:
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", text).lower().replace("st.", "saint ")).strip()


def fetch() -> None:
    for book, page in TABLES:
        try:
            raw.cached(SOURCE, url(book, page))
        except LookupError:
            path = INBOX / f"{book}_p{page}.pdf"
            raw.store(
                SOURCE,
                url(book, page),
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 22, tzinfo=UTC),
                media_type="application/pdf",
            )


def cell(text: str) -> int | None:
    digits = re.sub(r"\D", "", text.translate(scan.CONFUSABLE))
    return int(digits) if digits else None


def read(book: str, page: int, counties: dict[str, str]) -> tuple[list[dict], list[str]]:
    pdf = raw.cached(SOURCE, url(book, page)).path
    OCR.mkdir(parents=True, exist_ok=True)
    words = scan.words(pdf, 1, OCR / f"{book}-{page}.tsv")
    slope = scan.skew(words)
    lines = scan.lines(words, slope, 14)
    blocks: list[list[list[scan.Word]]] = []
    for line in lines:
        joined = " ".join(w[0] for w in line)
        if re.search(r"(?i)congressional\s+district|united\s+states\s+senator", joined) and (
            len(line) < 9 or "VOTE FOR" in joined.upper()
        ):
            if "VOTE FOR MEMBERS" in joined.upper():
                continue
            blocks.append([])
        elif blocks:
            blocks[-1].append(line)
    specs = TABLES[(book, page)]
    notes: list[str] = []
    if len(blocks) != len(specs):
        return [], [f"{book} {page}: {len(blocks)} blocks for {len(specs)} tables"]
    out: list[dict] = []
    for block, (district, columns) in zip(blocks, specs, strict=True):
        label = f"{book} {page} district {district}"
        start = next(
            (i for i, line in enumerate(block) if re.search(r"\((Dem|Rep|Lib|Tax)", " ".join(w[0] for w in line))),
            None,
        )
        if start is None:
            notes.append(f"{label}: no header")
            continue
        rows = []
        for line in block[start + 1 :]:
            cells = [w for w in line if NUMBER.match(w[0]) and w[1][0] > 600]
            if not cells:
                continue
            text = " ".join(w[0] for w in line if w not in cells)
            rows.append({"name": text, "cells": cells})
        if not rows:
            notes.append(f"{label}: no rows")
            continue
        total = next((row for row in rows if re.match(r"(?i)^\W*t[o0]t", row["name"])), None)
        if total is not None:
            rows.remove(total)
        width = len(columns)
        clean = [row for row in (*rows, *([total] if total else [])) if len(row["cells"]) == width]
        if not clean:
            best = max(rows, key=lambda row: len(row["cells"]))
            notes.append(f"{label}: no row with {width} cells (best {len(best['cells'])})")
            continue
        anchors = [
            sorted((row["cells"][c][1][0] + row["cells"][c][1][2]) / 2 for row in clean)[len(clean) // 2]
            for c in range(width)
        ]
        known: list[str | None] = []
        for row in rows:
            lead = LEAD.match(row["name"].strip())
            plain = key(re.sub(r"(?i)\(?part\)?.*", "", lead[0])) if lead else ""
            hit = difflib.get_close_matches(plain, list(counties), 1, 0.8)
            known.append(hit[0] if hit else None)
        ordered = sorted(counties)
        for i, name in enumerate(known):
            if name is not None:
                continue
            before = next((k for k in reversed(known[:i]) if k), "")
            after = next((k for k in known[i + 1 :] if k), "~")
            pool = [c for c in ordered if before < c < after and c not in known]
            if len(pool) == 1:
                known[i] = pool[0]
            else:
                notes.append(f"{label}: row {i} {rows[i]['name']!r} between {before} and {after}: {pool}")
        for row, name in zip(rows, known, strict=True):
            if name is None:
                continue
            placed: dict[int, list[scan.Word]] = {}
            for word in row["cells"]:
                at = min(range(width), key=lambda c: abs(anchors[c] - (word[1][0] + word[1][2]) / 2))
                placed.setdefault(at, []).append(word)
            for c, found in placed.items():
                if len(found) > 1:
                    notes.append(f"{label}: {name} column {c} has {[w[0] for w in found]}")
                    continue
                value = cell(found[0][0])
                if value is None:
                    continue
                row.setdefault("values", {})[c] = (value, found[0][1], found[0][0])
            row["county"] = name
            for c in range(width):
                if c not in row.get("values", {}):
                    notes.append(f"{label}: {name} column {c} missing")
        sums = [0] * width
        for row in rows:
            for c, (value, _, _) in row.get("values", {}).items():
                sums[c] += value
        if total is not None:
            printed = {}
            for word in total["cells"]:
                at = min(range(width), key=lambda c: abs(anchors[c] - (word[1][0] + word[1][2]) / 2))
                printed[at] = cell(word[0])
            for c in range(width):
                if printed.get(c) != sums[c]:
                    notes.append(f"{label}: column {c} sums to {sums[c]:,}, printed {printed.get(c)}")
        for row in rows:
            for c, (value, box, text) in row.get("values", {}).items():
                party, name = columns[c]
                out.append(
                    {
                        "year": BOOKS[book][1].year,
                        "chamber": f"{'senate' if district is None else 'house'}:{PARTIES[party]}",
                        "day": BOOKS[book][1],
                        "special": False,
                        "state": "WI",
                        "district": district,
                        "code": counties[row["county"]],
                        "candidate": name.upper(),
                        "measure": 1,
                        "writein": False,
                        "votes": value,
                        "kind": "county",
                        "source": f"WI{BOOKS[book][1].year}",
                        "file": 0,
                        "page": f"{book}:{page}",
                        "x0": box[0],
                        "y0": box[1],
                        "x1": box[2],
                        "y1": box[3],
                    }
                )
    return out, notes


def parse() -> None:
    with connect() as conn:
        counties = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        }
    out: list[dict] = []
    for book, page in TABLES:
        rows, notes = read(book, page, counties)
        out.extend(rows)
        for note in notes:
            print(note)
        print(f"{book} {page}: {len(rows)} values")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).with_columns(
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
            summary="Wisconsin's 1994, 1996, and 1998 U.S. Senate and House primaries by county, read from page scans of the Wisconsin Blue Books; the pages were saved from the UW Digital Collections by a person in a browser",
            polity="US-WI",
        )
        sources: dict[str, object] = {}
        pages: dict[str, str] = {}
        for book, (item, day, title) in BOOKS.items():
            source = cs.source(PUBLISHER, title)
            sources[f"WI{day.year}"] = source
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"https://search.library.wisc.edu/digital/{item}",
                license="Public record",
            )
            for name, page in TABLES:
                if name != book:
                    continue
                pdf = raw.cached(SOURCE, url(book, page))
                artifact = cs.artifact(pdf, source)
                image = PAGES / f"{pdf.sha256}-1.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "300", "-gray", "-png", "-singlefile", str(pdf.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                number = cs.page(artifact, page)
                pages[f"{book}:{page}"] = str(number)
                cs.add(
                    "source_page",
                    id=number,
                    artifact_id=artifact,
                    page_number=page,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        frame = frame.with_columns(page_id=pl.col("page").replace_strict(pages, return_dtype=pl.String)).with_columns(
            page=pl.lit(1)
        )
        medsl.attach(cs, conn, frame, sources, strict=True)
        return cs.write()
