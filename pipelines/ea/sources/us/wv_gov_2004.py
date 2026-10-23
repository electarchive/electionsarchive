import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wv_gov_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "West Virginia Secretary of State"
TITLE = "2004 general election governor results"
URL = "https://web.archive.org/web/2015id_/https://sos.wv.gov/elections/Documents/HistElecDocs/2004/2004%20Governor%20Gen.pdf"
CANDIDATES = ["MANCHIN", "JOHNSON", "WARNER", "MCCLURE"]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower().replace("&", "and")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    page = text
    rows = []
    total: list[int] = []
    for line in page.splitlines():
        row = re.match(r"([A-Z][A-Za-z.&' ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*){3})\s*$", line)
        if row is None:
            continue
        cells = [int(cell.replace(",", "")) for cell in row[2].split()][:4]
        if row[1] in ("Total", "Totals"):
            total = cells
        else:
            rows.extend(("%s" % fips[re.sub(r"[^a-z]", "", row[1].lower().replace("&", "and"))], name, votes) for name, votes in zip(CANDIDATES, cells))
    frame = pl.DataFrame(rows, schema=["code", "candidate", "votes"], orient="row")
    sums = frame.group_by("candidate").agg(pl.col("votes").sum())
    for name, expected in zip(CANDIDATES, total):
        if sums.filter(pl.col("candidate") == name)["votes"][0] != expected or frame["code"].n_unique() != 55:
            raise ValueError(f"{name}: counties do not add up to {expected}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(date(2004, 11, 2)),
        chamber=pl.lit("governor"),
        year=pl.lit(2004, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("WV"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WV"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's 2004 governor general by county from the Secretary of State's statewide results",
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
                url="https://sos.wv.gov/elections/election-data/historical-election-results-and-turnout",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WV": source}, strict=True, extend=True)
        return cs.write()
