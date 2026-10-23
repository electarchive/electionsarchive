from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ca_sen_2022"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.cdn.sos.ca.gov/sov/2022-general/sov/csv-candidates.xlsx"
DAY = date(2022, 11, 8)
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, November 8, 2022 General Election, candidate results by county"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["CA"]
    frame = pl.read_excel(raw.cached(SOURCE, URL).path, engine="calamine", infer_schema_length=0).filter(
        pl.col("Contest Name").str.starts_with("State Senate District ")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = frame.select(
        year=pl.lit(DAY.year, pl.Int32),
        chamber=pl.concat_str(
            pl.lit("senate:"),
            pl.col("Contest Name").str.extract(r"(\d+)$").cast(pl.Int32).cast(pl.String),
            pl.lit(":g"),
        ),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("CA"),
        district=pl.lit(None, pl.Int16),
        code=pl.col("County Name").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
        kind=pl.lit("county"),
        candidate=pl.col("Candidate Name").str.strip_chars().str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("Write-in Flag") == "Y",
        votes=pl.col("Vote Total").cast(pl.Int64),
        source=pl.lit("CA"),
    )
    print(frame.group_by("chamber").agg(pl.col("code").n_unique(), pl.col("votes").sum()).sort("chamber"))
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 2022 State Senate generals by county from the Secretary of State's Statement of Vote",
            polity="US-CA",
        )
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
        medsl.attach(
            cs,
            conn,
            frame,
            {"CA": source},
            strict=True,
            authoritative="Source: the Secretary of State's Statement of Vote.",
        )
        return cs.write()
