from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.pa_dos_returns import BASE, DAYS, LANDING, PUBLISHER, TITLE, read
from ea.store import DATA

SOURCE = "us.pa_dos_president"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (1992, 1996)
METHOD = "County totals are sums of the Department of State's precinct returns for the general election (the bulk file extracted in 2005)."


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    for year in YEARS:
        frames.append(
            read(year)
            .filter(pl.col("office") == "USP")
            .select(
                pl.lit(str(DAYS[year])).alias("day"),
                pl.lit("president").alias("district"),
                (42000 + 2 * pl.col("county").cast(pl.Int64) - 1).cast(pl.String).alias("county"),
                pl.col("candidate").alias("column"),
                pl.col("votes").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames).group_by("day", "district", "county", "column").agg(pl.col("votes").sum())
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day").agg(pl.col("county").n_unique(), pl.col("column").n_unique(), pl.col("votes").sum()))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Pennsylvania's 1992 and 1996 presidential elections by county, rolled up from the Department of State's precinct returns",
        PUBLISHER,
        TITLE,
        LANDING,
        "PA",
        "42",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached("us.pa_dos_returns", BASE.format(year=year)) for year in YEARS],
        lambda key: METHOD,
        statewide=True,
    )
