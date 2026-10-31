import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.nd_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Dakota Secretary of State"
TITLE = "Official abstract of votes cast at the 2008 general election"
URL = (
    "https://vip.sos.nd.gov/pdfs/Abstracts%20by%20Year/2000%20through%202008%20Statewide%20Election%20Results/2008/"
    "General%20Election%2011-04-2008.pdf"
)
DAY = date(2008, 11, 4)
RACES = (("Mary Maring", None), ("Daniel J Crothers", "Unexpired 4-Year Term"))
ROW = re.compile(r"^(?P<county>[A-Z][A-Za-z. ]+?)\s+(?P<precincts>\d+)\s+(?P<numbers>[\d,]+(?:\s+[\d,]+){7})\s*$")
METHOD = "County totals come from the Secretary of State's official abstract of the November 4, 2008 general election."
MISSING = "The abstract has no row for {counties} in this race; those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {re.sub(r"[^a-z]", "", name): code for name, code in openelections.counties(conn)["ND"].items()}
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    start = text.index("Mary Maring       ")
    rows = []
    totals = None
    for line in text[start:].splitlines():
        found = ROW.match(line.strip())
        if found is None:
            continue
        figures = [int(value.replace(",", "")) for value in found["numbers"].split()]
        if found["county"] == "Total":
            totals = figures
            break
        key = re.sub(r"[^a-z]", "", found["county"].lower())
        for (name, post), votes in zip(RACES, figures[3:5], strict=True):
            rows.append((name.upper(), post, votes, fips[key]))
    if totals is None or len(rows) != 106:
        raise ValueError(f"{len(rows)} county rows, totals {totals}")
    for index, (name, _) in enumerate(RACES):
        if sum(row[2] for row in rows if row[0] == name.upper()) != totals[3 + index]:
            raise ValueError(f"{name}: the counties do not add up to the printed total")
    frame = pl.DataFrame(
        rows, schema={"candidate": pl.String, "post": pl.String, "votes": pl.Int64, "county": pl.String}, orient="row"
    ).select(
        year=pl.lit(2008, pl.Int32),
        day=pl.lit(DAY),
        state=pl.lit("ND"),
        title=pl.lit("Justice of the Supreme Court"),
        post="post",
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party=pl.lit(None, pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(53),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("candidate").agg(pl.col("votes").sum(), pl.col("county").n_unique()).rows())


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
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
        return {"ND": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Dakota's two Supreme Court justice contests of 2008 by county from the Secretary of State's "
        "official abstract",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
