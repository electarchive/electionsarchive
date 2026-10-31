import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ri_pri_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.ri.gov/sites/g/files/xkgbur756/files/publications/Election_Publications/Countbooks/2010-Countbook.pdf"
DAY = date(2010, 9, 14)
CODES = {"BRISTOL": "44001", "KENT": "44003", "NEWPORT": "44005", "PROVIDENCE": "44007", "WASHINGTON": "44009"}
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "2010 Countbook"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    names = {
        (1, "democratic"): ["David N. Cicilline", "Anthony P. Gemma", "David A. Segal", "William J. Lynch"],
        (1, "republican"): ["John J. Loughlin, II", "Kara D. Russo"],
        (2, "democratic"): ["James R. Langevin", "Elizabeth M. Dennigan", "Ernest A. Greco"],
        (2, "republican"): ["Mark S. Zaccaria", "William J. Clegg, III", "Michael J. Gardiner", "Donald F. Robbio"],
    }
    out: list[tuple[date, str, int, str, str, int]] = []
    for (district, party), candidates in names.items():
        start = next(
            i
            for i, line in enumerate(lines)
            if re.search(rf"US CONGRESS - District {district}\s+.\s+{party.upper()[:3]}\w+ RESULTS", line)
        )
        county = ""
        for line in lines[start + 1 : start + 60]:
            if "STATE TOTALS" in line:
                break
            if found := re.match(r"\s*([A-Z]+) COUNTY\s*$", line):
                county = found[1]
            elif "County Total" in line:
                numbers = [int(value) for value in re.findall(r"\d+", line.split("Total")[1])]
                for name, votes in zip(candidates, numbers, strict=True):
                    out.append((DAY, f"house:{party}", district, name, CODES[county], votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        out,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "candidate": pl.String,
            "code": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        year=pl.lit(2010, dtype=pl.Int32),
        special=pl.lit(False),
        state=pl.lit("RI"),
        measure=pl.lit(1, dtype=pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("RI"),
    ).write_parquet(PARSED / "counties.parquet")
    print(len(out), "rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2010 U.S. House primaries by county",
            polity="US-RI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.ri.gov/publications/election-publications/countbooks",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"RI": source}, strict=True)
        return cs.write()
