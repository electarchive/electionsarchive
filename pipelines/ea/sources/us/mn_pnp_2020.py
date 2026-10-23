import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_pnp_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://electionresultsfiles.sos.mn.gov/20200303/"
NAMES = ["PNPCounty", "Cntytbl", "PNPStatewide"]
DAY = date(2020, 3, 3)
PARTIES = {"DFL": "minnesota-democratic-farmer-labor", "R": "republican"}
PUBLISHER = "Minnesota Secretary of State"
NOTE = (
    "The Secretary of State's file lists only Donald J. Trump and write-ins on the Republican ballot, so votes the "
    "FEC credits to William Weld are not broken out."
)
TITLE = "Presidential nomination primary results by county"


def fetch() -> None:
    for name in NAMES:
        raw.fetch(SOURCE, f"{BASE}{name}.txt", suffix=".txt")


def read(name: str) -> list[list[str]]:
    text = raw.cached(SOURCE, f"{BASE}{name}.txt").path.read_text(encoding="latin-1")
    return [[cell.strip() for cell in line.split(";")] for line in text.splitlines() if line.strip()]


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '27%%'"""
            ).fetchall()
        }
    counties = {number: fips[re.sub(r"[^a-z]", "", name.lower())] for number, name, *_ in read("Cntytbl")}
    rows = []
    for cells in read("PNPCounty"):
        candidate = "WRITE-INS" if cells[7] == "Write-In" else cells[7].upper()
        rows.append((counties[cells[1]], f"president:{PARTIES[cells[10]]}", candidate, int(cells[13])))
    frame = pl.DataFrame(rows, schema=["code", "chamber", "candidate", "votes"], orient="row")
    for cells in read("PNPStatewide"):
        candidate = "WRITE-INS" if cells[7] == "Write-In" else cells[7].upper()
        counted = frame.filter(
            (pl.col("chamber") == f"president:{PARTIES[cells[10]]}") & (pl.col("candidate") == candidate)
        )["votes"].sum()
        if counted != int(cells[13]):
            raise ValueError(f"{candidate}: counties add up to {counted}, printed {cells[13]}")
    print(frame.group_by("chamber", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("chamber", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(DAY),
        year=pl.lit(2020, pl.Int32),
        special=pl.lit(False),
        state=pl.lit("MN"),
        district=pl.lit(None, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate") == "WRITE-INS",
        kind=pl.lit("county"),
        source=pl.lit("MN"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's March 3, 2020 presidential nomination primaries by county from the Secretary of "
            "State's results files",
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
                url="https://electionresults.sos.mn.gov/132",
                license="Public record",
            )
        for name in NAMES:
            cs.artifact(raw.cached(SOURCE, f"{BASE}{name}.txt"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MN": source}, authoritative=NOTE, extend=True)
        return cs.write()
