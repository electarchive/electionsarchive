import csv
import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.me_federal import COUNTIES, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.me_gov_senate"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Election results: general election tabulations by municipality"
WAYBACK = "https://web.archive.org/web/2015id_/http://www.maine.gov:80/sos/cec/elec/"
FILES = [
    ("2000g/gen00s.txt", date(2000, 11, 7), "senate", "old"),
    ("2002g/gen02gv.txt", date(2002, 11, 5), "governor", "wide"),
    ("2002g/gen02s.txt", date(2002, 11, 5), "senate", "wide"),
    ("2006g/USsen.txt", date(2006, 11, 7), "senate", "party"),
]


def fetch() -> None:
    for path, *_ in FILES:
        raw.fetch(SOURCE, WAYBACK + path, suffix=".txt")


def parse() -> None:
    rows = []
    for path, day, chamber, layout in FILES:
        text = raw.cached(SOURCE, WAYBACK + path).path.read_text("latin-1")
        if layout == "old":
            for row in csv.reader(io.StringIO(text.split("-" * 53)[-1].strip())):
                for start in (4, 6):
                    if len(row) > start + 1 and row[start].strip():
                        rows.append((day, chamber, COUNTIES[row[1]], row[start + 1].strip().upper(), int(row[start])))
            continue
        offset = 5 if layout == "wide" else 4
        for row in list(csv.reader(io.StringIO(text)))[1:]:
            county = row[2] if layout == "wide" else row[1]
            for start in range(offset, len(row) - 3, 4):
                name, votes = row[start].strip().upper(), row[start + 3].strip()
                if name and votes:
                    rows.append((day, chamber, COUNTIES[county], "WRITE-INS" if name == "OTHER" else name, int(votes)))
    frame = pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row")
    with pl.Config(tbl_rows=80):
        print(
            frame.group_by("day", "chamber", "candidate")
            .agg(pl.col("votes").sum(), pl.col("code").n_unique())
            .sort("day", "chamber", "votes")
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("day", "chamber", "code", "candidate").agg(pl.col("votes").sum()).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("ME"),
        writein=pl.col("candidate") == "WRITE-INS",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("ME"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2000 and 2002 U.S. Senate, 2002 governor, and 2006 U.S. Senate generals by county from the "
            "Secretary of State's tabulations by municipality",
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
                url="https://www.maine.gov/sos/cec/elec/results/index.html",
                license="Public record",
            )
        for path, *_ in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True, extend=True)
        return cs.write()
