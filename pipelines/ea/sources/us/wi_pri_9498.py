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


def boxes(
    book: str, page: int, tables: dict[int | None, list[list[str]]]
) -> dict[tuple[int | None, int, int], tuple[int, int, int, int]]:
    pdf = raw.cached(SOURCE, url(book, page)).path
    OCR.mkdir(parents=True, exist_ok=True)
    words = scan.words(pdf, 1, OCR / f"{book}-{page}.tsv")
    heads = sorted(w[1][1] for w in words if re.fullmatch(r"(?i)congressional|senator", w[0].strip(".,")))
    if len(heads) != len(tables):
        raise ValueError(f"{book} {page}: {len(heads)} headings for {len(tables)} tables")
    found: dict[tuple[int | None, int, int], tuple[int, int, int, int]] = {}
    for at, (district, rows) in enumerate(tables.items()):
        top, bottom = heads[at], (heads[at + 1] if at + 1 < len(heads) else 10**6)
        numbers = [
            (w[1], re.sub(r"\D", "", w[0].translate(scan.CONFUSABLE)))
            for w in words
            if top < w[1][1] < bottom
            and w[1][0] > 600
            and re.fullmatch(r"[\dOolI|]{1,3}(?:[,.][\dOolI|]{3})*\*?", w[0])
        ]
        width = len(rows[0])
        centers = sorted(((b[0] + b[2]) // 2 for b, _ in numbers))
        cuts = sorted(sorted(range(len(centers) - 1), key=lambda i: centers[i] - centers[i + 1])[: width - 1])
        edges = [(centers[i] + centers[i + 1]) // 2 for i in cuts]
        sizes: list[tuple[int, int, int]] = []
        for c in range(width):
            low = edges[c - 1] if c > 0 else 0
            high = edges[c] if c < width - 1 else 10**6
            column = sorted((n for n in numbers if low <= (n[0][0] + n[0][2]) // 2 < high), key=lambda n: n[0][1])
            wanted = [row[c] for row in rows]
            matcher = difflib.SequenceMatcher(None, [n[1] for n in column], wanted, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for k in range(i2 - i1):
                        found[(district, j1 + k, c)] = column[i1 + k][0]
            sizes.append(
                (
                    sorted((b[0] + b[2]) // 2 for b, _ in column)[len(column) // 2],
                    sorted(b[2] - b[0] for b, _ in column)[len(column) // 2],
                    sorted(b[3] - b[1] for b, _ in column)[len(column) // 2],
                )
            )
        mids: list[float | None] = []
        for r in range(len(rows)):
            ys = sorted(
                (found[(district, r, c)][1] + found[(district, r, c)][3]) / 2
                for c in range(width)
                if (district, r, c) in found
            )
            mids.append(ys[len(ys) // 2] if ys else None)
        known = [r for r, mid in enumerate(mids) if mid is not None]
        for r in range(len(rows)):
            if mids[r] is None:
                below = [k for k in known if k < r]
                above = [k for k in known if k > r]
                low, high = (below[-1], above[0]) if below and above else (known[-2:] if below else known[:2])
                mids[r] = mids[low] + (mids[high] - mids[low]) * (r - low) / (high - low)
        for r, mid in enumerate(mids):
            for c, (anchor, wide, tall) in enumerate(sizes):
                found.setdefault(
                    (district, r, c),
                    (anchor - wide // 2, round(mid) - tall // 2, anchor + wide // 2, round(mid) + tall // 2),
                )
    return found


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
    grouped: dict[tuple[str, int], dict[int | None, list[tuple[str, list[str]]]]] = {}
    for line in (Path(__file__).with_suffix(".tsv")).read_text().splitlines():
        book, page, district, county, *values = line.split("\t")
        grouped.setdefault((book, int(page)), {}).setdefault(None if district == "S" else int(district), []).append(
            (county, values)
        )
    out: list[dict] = []
    for (book, page), tables in grouped.items():
        located = boxes(
            book,
            page,
            {district: [values for county, values in rows if county != "TOTAL"] for district, rows in tables.items()},
        )
        for district, rows in tables.items():
            columns = dict(TABLES[(book, page)])[district]
            total = next(values for county, values in rows if county == "TOTAL")
            body = [row for row in rows if row[0] != "TOTAL"]
            for c, printed in enumerate(total):
                if sum(int(values[c]) for _, values in body) != int(printed):
                    raise ValueError(
                        f"{book} {page} district {district} column {c}: sum disagrees with the printed total"
                    )
            for r, (county, values) in enumerate(rows):
                if county == "TOTAL":
                    continue
                for c, value in enumerate(values):
                    party, name = columns[c]
                    box = located[(district, r, c)]
                    out.append(
                        {
                            "year": BOOKS[book][1].year,
                            "chamber": f"{'senate' if district is None else 'house'}:{PARTIES[party]}",
                            "day": BOOKS[book][1],
                            "special": False,
                            "state": "WI",
                            "district": district,
                            "code": counties[key(county.replace("_", " "))],
                            "candidate": name.upper(),
                            "measure": 1,
                            "writein": False,
                            "votes": int(value),
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
        print(f"{book} {page}: {sum(1 for o in out if o['page'] == f'{book}:{page}')} values")
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
        sizes: dict[str, tuple[int, int]] = {}
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
                        [
                            "pdftoppm",
                            "-r",
                            "300",
                            "-gray",
                            "-png",
                            "-singlefile",
                            str(pdf.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                number = cs.page(artifact, page)
                pages[f"{book}:{page}"] = str(number)
                sizes[f"{book}:{page}"] = (width, height)
                cs.add(
                    "source_page",
                    id=number,
                    artifact_id=artifact,
                    page_number=page,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        frame = frame.with_columns(
            page_id=pl.col("page").replace_strict(pages, return_dtype=pl.String),
            **{
                edge: pl.col(edge) / pl.col("page").replace_strict({k: v[axis] for k, v in sizes.items()})
                for edge, axis in (("x0", 0), ("y0", 1), ("x1", 0), ("y1", 1))
            },
        ).with_columns(page=pl.lit(1))
        medsl.attach(cs, conn, frame, sources, strict=True, restage=True)
        return cs.write()
