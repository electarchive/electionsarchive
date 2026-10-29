import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nj_house_special_2024"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PDF = "https://nj.gov/state/elections/assets/pdf/election-results/2024/2024-official-special-general-results-us-house-10cd.pdf"
DAY = date(2024, 9, 18)
PUBLISHER = "New Jersey Division of Elections"
TITLE = "2024 Special General Election Official Results: Candidates for House of Representatives"


def fetch() -> None:
    raw.fetch(SOURCE, PDF, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    with connect() as conn:
        counties = {
            name.removesuffix(", NJ").removesuffix(" County").upper(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '34%%' and u.valid @> '2024-01-01'::date"""
            ).fetchall()
        }
    rows = []
    candidate = None
    for line in text.splitlines():
        if re.match(r"[A-Z][A-Z .'-]+\S(\s{2,}|\s+\(w\))", line) and not re.match(r"Name\s|Tenth\s", line):
            candidate = re.split(r"\s+\(w\)|\s{2,}", line.strip())[0].title()
        found = re.match(r"\s{20,}(" + "|".join(counties) + r")\b.*?([\d,]+)\s*$", line)
        if found and candidate:
            rows.append((candidate, counties[found[1]], int(found[2].replace(",", ""))))
    frame = pl.DataFrame(rows, schema={"candidate": pl.String, "code": pl.String, "votes": pl.Int64}, orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("candidate").agg(pl.col("code").n_unique(), pl.col("votes").sum()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's 2024 special general election for the 10th District U.S. House seat by county from the Division of Elections",
            polity="US-NJ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.nj.gov/state/elections/election-information-results.shtml",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PDF), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(2024, pl.Int32),
                chamber=pl.lit("house"),
                day=pl.lit(DAY),
                special=pl.lit(True),
                state=pl.lit("NJ"),
                district=pl.lit(10, pl.Int16),
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("NJ"),
            ),
            {"NJ": source},
            strict=True,
        )
        return cs.write()
