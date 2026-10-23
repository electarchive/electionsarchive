import io
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.ct_statewide_oe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "2002/20021105__ct__general__town.csv": date(2002, 11, 5),
    "2006/20061107__ct__general__town.csv": date(2006, 11, 7),
}
TITLE = "CT precinct-level results"
OFFICES = {
    "Attorney General": "Attorney General",
    "Comptroller": "Comptroller",
    "Secretary of State": "Secretary of the State",
    "Treasurer": "Treasurer",
}
METHOD = (
    "County totals are the sums of the town rows in OpenElections' Connecticut returns for the year, transcribed "
    "from the Secretary of the State's statement of vote. The winner shown is the candidate with the most votes in "
    "those sums."
)
PARTIES = {"Dem": "DEMOCRATIC", "Rep": "REPUBLICAN", "Lbt": "LIBERTARIAN", "Green": "GREEN", "CC": "CONCERNED CITIZENS"}


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="ct", commit=openelections.COMMITS["CT"], path=path)


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["CT"]
    frames = []
    for path, day in FILES.items():
        frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url(path)).path.read_bytes()), infer_schema_length=0)
        frames.append(
            frame.filter(
                pl.col("office").is_in(list(OFFICES)),
                pl.col("county").is_not_null(),
                pl.col("county") != "CONNECTICUT",
                pl.col("town") != "Total",
            ).select(
                year=pl.lit(day.year, pl.Int32),
                day=pl.lit(day),
                state=pl.lit("CT"),
                title=pl.col("office").replace_strict(OFFICES),
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
                votes=pl.col("votes").cast(pl.Int64),
            )
        )
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = (
        pl.concat(frames)
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
        counties=pl.lit(8),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(returns.group_by("year", "title", "candidate").agg(pl.col("votes").sum()).sort("year", "title", "votes"))


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source("OpenElections", TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {TITLE}")
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        return {"CT": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Connecticut's 2002 attorney general, comptroller, and treasurer and 2006 attorney general, comptroller, "
        "secretary of the state, and treasurer elections by county from OpenElections' town returns",
        cite=cite,
        method=METHOD,
        absent="OpenElections' file has no rows for {counties} in this race, so those votes are missing from these totals.",
    )
