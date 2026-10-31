import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_senate_2008"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alabama Secretary of State"
TITLE = "Statewide offices, general election results, November 4, 2008"
URL = (
    "https://www.sos.alabama.gov/sites/default/files/voter-pdfs/2008/"
    "statecert-2008-general-election-11-25-2008-without-write-in-appendix.pdf"
)
DAY = date(2008, 11, 4)
CANDIDATES = ["FIGURES", "SESSIONS", "WRITE-INS"]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    found = {}
    total: list[int] = []
    senate = False
    for line in text.splitlines():
        if line.startswith("County "):
            senate = "Figures" in line
        if not senate:
            continue
        match = re.match(r"^(TOTAL|[A-Za-z. ]+?)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$", line)
        if match is None:
            continue
        label = match.group(1).strip()
        numbers = [int(n.replace(",", "")) for n in match.group(2).split()]
        cells = numbers[:3] if len(numbers) >= 6 else numbers[:2] + [0] if len(numbers) == 4 else None
        if cells is None:
            continue
        if label == "TOTAL":
            total = cells
        elif re.sub(r"[^a-z]", "", label.lower()) in counties:
            found[counties[re.sub(r"[^a-z]", "", label.lower())]] = cells
    if len(found) != 67:
        raise ValueError(f"{len(found)} counties")
    rows = []
    for column, candidate in enumerate(CANDIDATES):
        if sum(cells[column] for cells in found.values()) != total[column]:
            raise ValueError(f"{candidate}: counties do not add up to {total[column]}")
        rows.extend((DAY, code, candidate, cells[column]) for code, cells in found.items())
    frame = pl.DataFrame(rows, schema=["day", "code", "candidate", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        chamber=pl.lit("senate"),
        year=pl.lit(2008, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("AL"),
        writein=pl.col("candidate") == "WRITE-INS",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("AL"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2008 U.S. Senate general by county from the Secretary of State's certified results",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True, extend=True)
        return cs.write()
