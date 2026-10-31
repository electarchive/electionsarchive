import io
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.mi_oe_2018"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PATH = "2018/20181106__mi__general__precinct.csv"
DAY = date(2018, 11, 6)
PARTIES = {
    "DEM": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "NPA": "NO PARTY AFFILIATION",
    "UST": "U.S. TAXPAYERS",
}
METHOD = (
    "County totals are the sums of the precinct rows in OpenElections' file for Michigan's November 6, 2018 general "
    "election, which compiles the state's precinct returns. The winner shown is the candidate with the most votes in "
    "those sums."
)


def url() -> str:
    return openelections.REPOSITORY.format(state="mi", commit=openelections.COMMITS["MI"], path=PATH)


def fetch() -> None:
    raw.fetch(SOURCE, url(), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["MI"]
    frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url()).path.read_bytes()), infer_schema_length=0)
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = (
        frame.filter(pl.col("office") == "Attorney General")
        .select(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            state=pl.lit("MI"),
            title=pl.lit("Attorney General"),
            post=pl.lit(None, pl.String),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            county=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
            candidate=pl.col("candidate").str.strip_chars().str.to_uppercase(),
            party=pl.col("party").replace_strict(PARTIES),
            writein=pl.lit(False),
            measure=pl.lit(1, pl.Int16),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
        )
        .group_by(
            *contest,
            "magnitude",
            "kind",
            "code",
            "county",
            "candidate",
            "party",
            "writein",
            "measure",
            maintain_order=True,
        )
        .agg(pl.col("votes").sum())
    )
    races = returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(83),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(races.select(*contest[:4], "counties", pl.col("present").list.len()))
    print(returns.group_by("candidate", "party").agg(pl.col("votes").sum()).sort("votes"))


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source("OpenElections", "MI precinct-level results")
        if source in cs.minted:
            raise ValueError("no source MI precinct-level results")
        cs.artifact(raw.cached(SOURCE, url()), source)
        return {"MI": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Michigan's 2018 attorney general general by county",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
    )
