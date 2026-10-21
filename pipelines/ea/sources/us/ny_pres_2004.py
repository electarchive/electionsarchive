import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ny_pres_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/2020id_/https://www.elections.ny.gov/NYSBOE/elections/2004/"
PUBLISHER = "New York State Board of Elections"
TITLE = "Presidential primary results by county"
DAY = date(2004, 3, 2)
FILES = {
    "democratic": (
        "2004democraticpp.pdf",
        [
            "DENNIS J. KUCINICH",
            "JOE LIEBERMAN",
            "JOHN EDWARDS",
            "LYNDON H. LAROUCHE, JR.",
            "HOWARD DEAN",
            "AL C. SHARPTON",
            "JOHN F. KERRY",
            "DICK GEPHARDT",
            "WESLEY CLARK",
            None,
        ],
    ),
}
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z. ]*?)\s+(\d[\d,]*(?:\s+\d[\d,]*)+)\s*$")


def fetch() -> None:
    for name, _ in FILES.values():
        raw.fetch(SOURCE, WAYBACK + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
    rows = []
    for party, (name, candidates) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, WAYBACK + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        found = 0
        for line in text.split("\n"):
            match = ROW.match(line)
            key = re.sub(r"[^a-z]", "", match.group(1).lower()) if match else ""
            if key not in counties:
                continue
            numbers = [int(n.replace(",", "")) for n in match.group(2).split()]
            if len(numbers) != len(candidates) + 1 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"{name}: {match.group(1)} does not add up: {numbers}")
            found += 1
            for candidate, votes in zip(candidates, numbers, strict=False):
                if candidate is not None:
                    rows.append((party, counties[key], candidate, votes))
        if found != 62:
            raise ValueError(f"{name}: {found} counties")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["party", "code", "candidate", "votes"], orient="row").with_columns(
        year=pl.lit(2004, pl.Int32),
        chamber="president:" + pl.col("party"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("NY"),
        district=pl.lit(None, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("NY"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's March 2, 2004 presidential primaries by county",
            polity="US-NY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.ny.gov/ElectionResults.html",
            license="Public record",
        )
        for name, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, WAYBACK + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NY": source}, strict=True)
        return cs.write()
