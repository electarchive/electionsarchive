import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ms_gov_2003"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Total votes reported by county for the 2003 general election"
URL = "https://www.sos.ms.gov/elections/electionresults/2003General/Certified%20Results/Governor.pdf"
TOTALS = {"BARBOUR": 470404}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout.split("\n")
    tallies: dict[tuple[str, str], int] = {}
    unit = ""
    for line in lines:
        if re.fullmatch(r"[A-Z][A-Za-z.' ]+", line.rstrip()):
            unit = line.strip()
            if unit == "State Totals":
                break
        elif row := re.fullmatch(r"\s+([A-Z][A-Za-z.' ]+?)\s{2,}([\d,]+)\s+[\d.]+%", line):
            key = (counties[re.sub(r"[^a-z]", "", unit.lower())], row[1].split()[-1].upper())
            tallies[key] = tallies.get(key, 0) + int(row[2].replace(",", ""))
    frame = pl.DataFrame(
        [(code, name, votes) for (code, name), votes in tallies.items()],
        schema=["code", "candidate", "votes"],
        orient="row",
    )
    print(frame["code"].n_unique(), frame.group_by("candidate").agg(pl.col("votes").sum()))
    sums = dict(frame.group_by("candidate").agg(pl.col("votes").sum()).iter_rows())
    for name, expected in TOTALS.items():
        if sums[name] != expected:
            raise ValueError(f"{name}: counties add up to {sums[name]}, not {expected}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(date(2003, 11, 4)),
        chamber=pl.lit("governor"),
        year=pl.lit(2003, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("MS"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MS"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's 2003 governor general by county from the Secretary of State's official "
            "election returns",
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
                url="https://www.sos.ms.gov/elections-voting/election-results",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MS": source}, strict=True, extend=True)
        return cs.write()
