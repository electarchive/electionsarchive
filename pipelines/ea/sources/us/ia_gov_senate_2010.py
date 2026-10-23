import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ia_gov_senate_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Iowa Secretary of State"
TITLE = "Official results report, 2010 general election"
SITE = "https://web.archive.org/web/2015id_/https://sos.iowa.gov/elections/pdf/2010/"
FILES = {
    "governor": ("govorr.pdf", ["CULVER", "BRANSTAD", "NARCISSE", "COOPER", "ROSENFELD", "HUGHES"]),
    "senate": ("senorr.pdf", ["CONLIN", "GRASSLEY", "HEIDERSCHEIT"]),
}


def fetch() -> None:
    for name, _ in FILES.values():
        raw.fetch(SOURCE, SITE + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%'"""
            ).fetchall()
        }
    rows = []
    for chamber, (name, candidates) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + name).path)] + ["-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        counties = []
        total: list[int] = []
        for line in text.splitlines():
            row = re.match(r"([A-Z][A-Za-z.' ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$", line)
            if row is None:
                continue
            cells = [int(cell.replace(",", "")) for cell in row[2].split()][: len(candidates)]
            if row[1] == "Grand Totals":
                total = cells
            else:
                counties.append((fips[re.sub(r"[^a-z]", "", row[1].lower())], cells))
        if len(counties) != 99:
            raise ValueError(f"{chamber}: {len(counties)} counties")
        for column, candidate in enumerate(candidates):
            if sum(cells[column] for _, cells in counties) != total[column]:
                raise ValueError(f"{chamber} {candidate}: counties do not add up to {total[column]}")
            rows.extend((chamber, code, candidate, cells[column]) for code, cells in counties)
    frame = pl.DataFrame(rows, schema=["chamber", "code", "candidate", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(date(2010, 11, 2)),
        year=pl.lit(2010, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("IA"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2010 governor and U.S. Senate generals by county from the Secretary of State's statewide results",
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
                url="https://sos.iowa.gov/archived-election-results-and-statistics",
                license="Public record",
            )
        for name, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IA": source}, strict=True, extend=True)
        return cs.write()
