import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ok_pri_9x"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Election results by county, 1996 and 1998 primaries"
BASE = "https://oklahoma.gov/elections/elections-results/election-results/"
PAGES = [
    (
        "1996-election-results/96pdsen",
        date(1996, 8, 27),
        "democratic",
        r"(?:DEMOCRATIC|REPUBLICAN) RACE FOR U\.S\. SENATOR",
    ),
    (
        "1996-election-results/1996-rep-race-for-us-senator",
        date(1996, 8, 27),
        "republican",
        r"(?:DEMOCRATIC|REPUBLICAN) RACE FOR U\.S\. SENATOR",
    ),
    (
        "1998-election-results/98dempri",
        date(1998, 8, 25),
        "democratic",
        r"UNITED STATES (?:SENATOR|REPRESENTATIVE, DISTRICT \d+)",
    ),
    (
        "1998-election-results/98reppri",
        date(1998, 8, 25),
        "republican",
        r"UNITED STATES (?:SENATOR|REPRESENTATIVE, DISTRICT \d+)",
    ),
    (
        "1998-election-results/98demrun",
        date(1998, 9, 15),
        "democratic",
        r"UNITED STATES (?:SENATOR|REPRESENTATIVE, DISTRICT \d+)",
    ),
    (
        "1998-election-results/98reprun",
        date(1998, 9, 15),
        "republican",
        r"UNITED STATES (?:SENATOR|REPRESENTATIVE, DISTRICT \d+)",
    ),
]


NAMES = {"1996-election-results/96pdsen": ["DON MCCORKELL", "JIM BOREN", "DAVID LOUIS ANNANDERS"]}


def url(page: str) -> str:
    return f"{BASE}{page}.html"


def fetch() -> None:
    for page, *_ in PAGES:
        raw.fetch(SOURCE, url(page), suffix=".html")


def table(block: str, label: str) -> tuple[list[str], list[tuple[str, list[int]]], list[int] | None]:
    lines = block.splitlines()
    rule = next(n for n, line in enumerate(lines) if "===" in line)
    spans = [(m.start(), m.end()) for m in re.finditer(r"=+", lines[rule]) if m.start() > 0]
    start = rule
    while start > 0 and lines[start - 1].strip():
        start -= 1
    names = [[] for _ in spans]
    for line in lines[start:rule]:
        for word in re.finditer(r"\S+", line):
            if word.end() <= spans[0][0]:
                continue
            centre = (word.start() + word.end()) / 2
            column = min(range(len(spans)), key=lambda n: abs(centre - (spans[n][0] + spans[n][1]) / 2))
            names[column].append(word.group())
    candidates = [" ".join(parts).upper() for parts in names[:-1]]
    rows = []
    total = None
    for line in lines[rule + 1 :]:
        if not (row := re.fullmatch(r"(\S.*?)\s+((?:[\d,]+\s+)*[\d,]+)\s*", line)):
            continue
        votes = [int(value.replace(",", "")) for value in row[2].split()]
        if len(votes) != len(candidates) + 1:
            raise ValueError(f"{label} {row[1]}: {len(votes)} figures for {len(candidates)} candidates")
        if sum(votes[:-1]) != votes[-1]:
            raise ValueError(f"{label} {row[1]}: {votes} do not sum")
        if re.match(r"(?:STATE )?TOTALS?\b", row[1]):
            total = votes
        else:
            rows.append((row[1], votes))
    return candidates, rows, total


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
    out = []
    for page, day, party, offices in PAGES:
        text = html.unescape(raw.cached(SOURCE, url(page)).path.read_text()).replace("\\n", "\n").replace('\\"', '"')
        marks = [(m.start(), m.group(1).strip()) for m in re.finditer(r"<h3>(?:<strong>)?([^<]+)", text)]
        done: set[str] = set()
        for n, (offset, title) in enumerate(marks):
            if not re.fullmatch(offices, title) or title in done:
                continue
            done.add(title)
            end = marks[n + 1][0] if n + 1 < len(marks) else len(text)
            block = re.sub(r"<[^>]+>", "", text[offset:end])
            label = f"{day} {party} {title}"
            candidates, rows, total = table(block, label)
            candidates = NAMES.get(page, candidates)
            if total is not None:
                for column in range(len(candidates) + 1):
                    if sum(votes[column] for _, votes in rows) != total[column]:
                        print(
                            f"{label}: column {column} counties sum to {sum(votes[column] for _, votes in rows)}, printed {total[column]}"
                        )
            district = re.search(r"DISTRICT (\d+)", title)
            chamber = f"{'house' if district else 'senate'}:{party}"
            for county, votes in rows:
                code = counties[re.sub(r"[^a-z]", "", county.lower())]
                for candidate, value in zip(candidates, votes, strict=False):
                    out.append((day, chamber, int(district[1]) if district else None, code, candidate, value))
            print(label, [c for c in candidates], len(rows), "counties", "total" if total else "no total")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        out,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet").select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("OK"),
        district="district",
        code="code",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
        kind=pl.lit("county"),
        source=pl.lit("OK"),
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 1996 and 1998 U.S. Senate and House primaries and runoffs by county from the State Election Board's published results",
            polity="US-OK",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://oklahoma.gov/elections/elections-results/election-results.html",
                license="Public record",
            )
        for page, *_ in PAGES:
            cs.artifact(raw.cached(SOURCE, url(page)), source)
        medsl.attach(cs, conn, frame, {"OK": source}, strict=True)
        return cs.write()
