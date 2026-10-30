from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import az_gen98, usrep_county

SOURCE = "us.az_gen98_governor"
VERSION = "1"
METHOD = (
    "County totals are from the Secretary of State's official canvass (the Wayback Machine's copy, read from its text "
    "layer), and each candidate's county sum equals the state total the canvass prints. Katherine Gallant, Scott Alan "
    "Malcomson, and the three write-in candidates are pooled as other votes."
)


def fetch() -> None:
    raw.fetch(az_gen98.SOURCE, az_gen98.FILE, suffix=".pdf")


def parse() -> None:
    return None


def emit() -> Path:
    frame = (
        pl.read_parquet(az_gen98.PARSED / "counties.parquet")
        .filter(pl.col("chamber") == "governor")
        .select(
            day=pl.col("day").cast(pl.String),
            district=pl.lit("governor"),
            county=pl.col("code"),
            column=pl.when(pl.col("candidate").is_in(["JANE DEE HULL", "PAUL JOHNSON"]))
            .then(pl.col("candidate"))
            .otherwise(pl.lit("OTHERS")),
            votes=pl.col("votes"),
        )
        .group_by("day", "district", "county", "column")
        .agg(pl.col("votes").sum())
    )
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Arizona's 1998 governor general election by county from the Secretary of State's official canvass",
        az_gen98.PUBLISHER,
        az_gen98.TITLE,
        "https://azsos.gov/election/1998/General/Canvass1998GE.pdf",
        "AZ",
        "04",
        frame,
        [raw.cached(az_gen98.SOURCE, az_gen98.FILE)],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
