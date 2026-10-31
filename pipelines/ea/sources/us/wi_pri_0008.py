import html
import re
import statistics
import struct
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.scan import CONFUSABLE
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wi_pri_0008"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-007"
PUBLISHER = "Wisconsin Elections Board"
LEGIS = "https://docs.legis.wisconsin.gov/misc/lrb/blue_book"
BOOKS: dict[int, tuple[str, date, str, str, list[int]]] = {
    2004: (
        "Wisconsin Blue Book 2005-2006",
        date(2004, 9, 14),
        f"{LEGIS}/2005_2006/950_elections.pdf",
        "950_elections_2005_2006.pdf",
        [19, 21, 22, 23],
    ),
    2006: (
        "Wisconsin Blue Book 2007-2008",
        date(2006, 9, 12),
        f"{LEGIS}/2007_2008/955_elections.pdf",
        "955_elections_2007_2008.pdf",
        [11, 13, 14, 15],
    ),
    2008: (
        "Wisconsin Blue Book 2009-2010",
        date(2008, 9, 9),
        f"{LEGIS}/2009_2010/950_elections.pdf",
        "950_elections_2009_2010.pdf",
        [20, 21, 22],
    ),
}
PARTIES = {
    "Dem": "democratic",
    "Rep": "republican",
    "Lib": "libertarian",
    "Con": "constitution",
    "Ind": "independent",
    "Grn": "green",
    "WG": "wisconsin-green",
}
MISPRINTED = {(2004, 7, "Mike Miles")}
ORDINALS = ["firs", "seco", "thir", "four", "fift", "sixt", "seve", "eigh", "nint"]
Word = tuple[str, float, float, float, float]


def key(text: str) -> str:
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", text).lower().replace("st.", "saint ")).strip()


def fetch() -> None:
    for _, _, url, file, _ in BOOKS.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / file).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
                media_type="application/pdf",
            )


def read(path: Path, page: int) -> list[list[Word]]:
    text = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    words: list[Word] = [
        (html.unescape(m[5]), float(m[1]), float(m[2]), float(m[3]), float(m[4]))
        for m in re.finditer(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', text
        )
    ]
    words.sort(key=lambda w: (w[2] + w[4]) / 2)
    lines: list[list[Word]] = []
    for word in words:
        if lines and abs((word[2] + word[4]) / 2 - statistics.mean((w[2] + w[4]) / 2 for w in lines[-1])) < 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w[1]) for line in lines]


def tables(lines: list[list[Word]]) -> list[tuple[int | None, list[tuple[str, str]], list[tuple[str, list[Word]]]]]:
    found: list[tuple[int | None, list[tuple[str, str]], list[tuple[str, list[Word]]]]] = []
    at = 0
    while at < len(lines):
        text = " ".join(w[0] for w in lines[at])
        ordinal = re.search(r"(?i)\b(\w+)\s+congressional\s+district", text)
        district: int | None = None
        if re.search(r"(?i)county vote for united states senator", text):
            pass
        elif ordinal and key(ordinal[1])[:4] in ORDINALS:
            district = ORDINALS.index(key(ordinal[1])[:4]) + 1
        else:
            at += 1
            continue
        county = next(i for i in range(at + 1, len(lines)) if lines[i][0][0].lower().startswith("county"))
        marks = [(m[1][:3], (w[1] + w[3]) / 2) for w in lines[county][1:] if (m := re.match(r"\((\w+)\.?\)", w[0]))]
        columns: list[list[str]] = [[] for _ in marks]
        for line in lines[at + 1 : county]:
            for w in line:
                if w[0].lower() in {"september", "primary", "primary−continued"} or re.fullmatch(r"[\d,]+", w[0]):
                    continue
                near = min(range(len(marks)), key=lambda i: abs((w[1] + w[3]) / 2 - marks[i][1]))
                columns[near].append(w[0].rstrip("*"))
        candidates = [(PARTIES[mark], " ".join(words)) for (mark, _), words in zip(marks, columns, strict=True)]
        rows: list[tuple[str, list[Word]]] = []
        end = county + 1
        while end < len(lines) and len(lines[end]) > len(marks):
            label = re.sub(r"[\W_]*\(part\)[\W_]*$", "", " ".join(w[0] for w in lines[end][: -len(marks)]))
            rows.append((re.sub(r"[\W_]+$", "", label), lines[end][-len(marks) :]))
            end += 1
            if rows[-1][0].lower() == "total":
                break
        found.append((district, candidates, rows))
        at = end
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
    out: list[dict] = []
    problems: list[str] = []
    for year, (_, day, url, _, pages) in BOOKS.items():
        path = raw.cached(SOURCE, url).path
        for page in pages:
            for district, candidates, rows in tables(read(path, page)):
                where = f"{year} p{page} {'S' if district is None else district}"
                print(where, [f"{party[:3]}:{name}" for party, name in candidates])
                body = [row for row in rows if row[0].lower() != "total"]
                total = next(row for row in rows if row[0].lower() == "total")
                sums = [0] * len(candidates)
                for county, cells in body:
                    code = counties.get(key(county))
                    if code is None:
                        problems.append(f"{where}: unknown county {county!r}")
                        continue
                    for c, cell in enumerate(cells):
                        value = int(re.sub(r"\D", "", cell[0].translate(CONFUSABLE)))
                        sums[c] += value
                        out.append(
                            {
                                "year": year,
                                "chamber": f"{'senate' if district is None else 'house'}:{candidates[c][0]}",
                                "day": day,
                                "special": False,
                                "state": "WI",
                                "district": district,
                                "code": code,
                                "candidate": candidates[c][1].upper(),
                                "measure": 1,
                                "writein": False,
                                "votes": value,
                                "kind": "county",
                                "source": f"WI{year}",
                                "file": 0,
                                "page": page,
                                "x0": round(cell[1] * 300 / 72),
                                "y0": round(cell[2] * 300 / 72),
                                "x1": round(cell[3] * 300 / 72),
                                "y1": round(cell[4] * 300 / 72),
                            }
                        )
                for c, cell in enumerate(total[1]):
                    if (
                        sums[c] != int(re.sub(r"\D", "", cell[0]))
                        and (year, district, candidates[c][1]) not in MISPRINTED
                    ):
                        problems.append(f"{where} {candidates[c][1]}: sum {sums[c]} against printed {cell[0]}")
    for line in problems:
        print("PROBLEM", line)
    if problems:
        raise ValueError(f"{len(problems)} problems")
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
            summary="Wisconsin's 2004, 2006, and 2008 U.S. Senate and House primaries by county, read from the text layer of the Wisconsin Blue Books' election chapters",
            polity="US-WI",
        )
        sources: dict[str, object] = {}
        pages: dict[str, str] = {}
        sizes: dict[str, tuple[int, int]] = {}
        for year, (title, _, url, _, numbers) in BOOKS.items():
            source = cs.source(PUBLISHER, title)
            sources[f"WI{year}"] = source
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
            pdf = raw.cached(SOURCE, url)
            artifact = cs.artifact(pdf, source)
            for page in numbers:
                image = PAGES / f"{pdf.sha256}-{page}.png"
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
                            "-f",
                            str(page),
                            "-l",
                            str(page),
                            str(pdf.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                number = cs.page(artifact, page)
                pages[f"{year}:{page}"] = str(number)
                sizes[f"{year}:{page}"] = (width, height)
                cs.add(
                    "source_page",
                    id=number,
                    artifact_id=artifact,
                    page_number=page,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        key = pl.format("{}:{}", pl.col("year"), pl.col("page"))
        frame = frame.with_columns(
            page_id=key.replace_strict(pages, return_dtype=pl.String),
            **{
                edge: pl.col(edge) / key.replace_strict({k: v[axis] for k, v in sizes.items()})
                for edge, axis in (("x0", 0), ("y0", 1), ("x1", 0), ("y1", 1))
            },
        ).with_columns(page=pl.lit(1))
        medsl.attach(cs, conn, frame, sources, strict=True, restage=True)
        return cs.write()
