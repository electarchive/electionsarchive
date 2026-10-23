import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.nj_federal import BASE, COUNTIES, ORDINALS, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.nj_general_2024"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2024, 11, 5)
FILES = ["2024/2024-official-general-results-us-senate.pdf", "2024/2024-official-general-results-us-house.pdf"]
TITLE = "Official general election results"


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    rows = []
    for path in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        office = "senate" if "senate" in path else "house"
        district, name, column = None, None, 0
        for line in text.splitlines():
            if header := re.match(r"(Name\s+)Address", line):
                column = len(header[1])
                name = None
            elif line.startswith("Candidate Totals"):
                break
            elif found := re.match(r"(\w+) Congressional District", line):
                district = ORDINALS.index(found[1]) + 1
            elif line[:1].strip() and not re.match(r"(Candidates|For |\d\d/|Total|Party|\(w\))", line) and line[column:].strip():
                name = re.sub(r"\s+\(w\)|\s*\*", "", line[:column]).strip().strip('"').upper()
            elif re.match(r"\s+Total\b", line):
                name = None
            elif name and (found := re.match(r"\s{40,}(" + "|".join(COUNTIES) + r")\s.*?([\d,]+)\s*$", line)):
                rows.append(
                    (2024, office, DAY, False, "NJ", district if office == "house" else None, COUNTIES[found[1]], name,
                     1, False, int(found[2].replace(",", "")))
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NJ"))
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("chamber", "district", "candidate").agg(pl.col("votes").sum(), pl.col("code").n_unique()).sort("chamber", "district", "votes").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's 2024 U.S. Senate and House generals by county from the Division of Elections' official lists",
            polity="US-NJ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.nj.gov/state/elections/election-information-2024.shtml",
            license="Public record",
        )
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NJ": source}, strict=True)
        return cs.write()
