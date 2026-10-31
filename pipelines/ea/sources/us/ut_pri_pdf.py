import re
import subprocess
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ut_pri_pdf"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://vote.utah.gov/wp-content/uploads/2023/09/"
INBOX = DATA / "inbox" / "manual" / "MF-329"
FILES = {
    "2002Pri.pdf": (date(2002, 6, 25), 1),
    "2004Pri.pdf": (date(2004, 6, 22), 1),
}
COLUMNS = {
    "2002Pri.pdf": [
        ("house:republican", 1, "Rob Bishop"),
        ("house:republican", 1, "Kevin S. Garn"),
        ("house:democratic", 1, "Donald Dunn"),
        ("house:democratic", 1, "Dave Thomas"),
        ("house:republican", 2, "Tim Bridgewater"),
        ("house:republican", 2, "John Swallow"),
    ],
    "2004Pri.pdf": [
        None,
        None,
        ("house:republican", 2, "Tim Bridgewater"),
        ("house:republican", 2, "John Swallow"),
        ("house:republican", 3, "Chris Cannon"),
        ("house:republican", 3, "Matt Throckmorton"),
        *[None] * 4,
    ],
}
PRESIDENT = "2008WSPprimary.pdf"
NAMES = {
    "democratic": ["Joe Biden", "Hillary Clinton", "Christopher J. Dodd", "John Edwards", "Mike Gravel", "Dennis Kucinich", "Frank Lynch", "Barack Obama", "Bill Richardson"],
    "republican": ["Rudy Giuliani", "Mike Huckabee", "Duncan Hunter", "Alan Keyes", "John McCain", "Ron Paul", "Mitt Romney", "Tom Tancredo", "Fred Thompson"],
}  # fmt: skip
WORD = re.compile(r'<word xMin="[\d.]+" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>')
PUBLISHER = "Utah Lieutenant Governor's Office of Elections"
TITLE = "Official primary results by county"


def fetch() -> None:
    for name in [*FILES, PRESIDENT]:
        raw.store(
            SOURCE,
            BASE + name,
            (INBOX / name).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.removesuffix(", UT").removesuffix(" County").lower()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '49%%' and u.valid @> '2000-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for name, (day, page) in FILES.items():
        html = subprocess.run(
            ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(raw.cached(SOURCE, BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        lines: dict[float, list[tuple[float, str]]] = {}
        for y, x, text in sorted(
            ((float(y), float(x), unescape(text)) for y, x, text in WORD.findall(html)), key=lambda word: word[0]
        ):
            lines.setdefault(next((key for key in lines if abs(key - y) < 2.5), y), []).append((x, text))
        total = next(y for y, words in lines.items() if words[0][1] == "TOTAL")
        anchors = [x for x, text in lines[total] if re.fullmatch(r"[\d,]+", text)][2:]
        for y, words in lines.items():
            code = codes.get(re.sub(r"[^a-z]", "", "".join(text for _, text in words if text.isalpha()).lower()))
            if code is None or y > total:
                continue
            for x, text in words:
                if re.fullmatch(r"[\d,]+", text):
                    column = min(range(len(anchors)), key=lambda index: abs(anchors[index] - x))
                    if abs(anchors[column] - x) < 6 and COLUMNS[name][column] is not None:
                        chamber, district, candidate = COLUMNS[name][column]
                        rows.append((day, chamber, district, code, candidate, int(text.replace(",", ""))))
        printed = [int(text.replace(",", "")) for _, text in lines[total] if re.fullmatch(r"[\d,]+", text)][2:]
        for column, spec in enumerate(COLUMNS[name]):
            if spec is None:
                continue
            counted = sum(row[5] for row in rows if row[0] == day and row[1:3] == spec[:2] and row[4] == spec[2])
            if counted != printed[column]:
                raise ValueError(f"{name} {spec[2]}: counties sum to {counted}, printed {printed[column]}")
    for line in Path(__file__).with_name("ut_pri_2008.tsv").read_text().splitlines():
        party, county, *cells = line.split("\t")
        code = codes[re.sub(r"[^a-z]", "", county.lower())]
        rows.extend(
            (date(2008, 2, 5), f"president:{party}", None, code, candidate, int(cell))
            for candidate, cell in zip(NAMES[party], cells, strict=True)
        )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "chamber", "district", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.len())
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's 2002 and 2004 U.S. House primaries and 2008 presidential primaries by county from the Office of Elections' official canvasses",
            polity="US-UT",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://vote.utah.gov/historical-election-results/",
                license="Public record",
            )
        for name in [*FILES, PRESIDENT]:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber="chamber",
                day="day",
                special=pl.lit(False),
                state=pl.lit("UT"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("UT"),
            ),
            {"UT": source},
            strict=True,
            restage=True,
        )
        return cs.write()
