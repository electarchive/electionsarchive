from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.phila_returns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Philadelphia City Commissioners"
TITLE = "Election results by ward and division"
LISTING = "https://vote.phila.gov/resources-data/past-election-results/archived-data-sets/"
BASE = "https://vote.phila.gov/files/"
METHOD = (
    "Division totals from the Philadelphia City Commissioners' results file for the election, matched to the "
    "contest by candidate and checked against its official totals."
)
WIDE = {
    date(2007, 5, 15): "election-results/2007_Primary_WD.csv",
    date(2007, 11, 6): "election-results/2007_General_WD.csv",
    date(2008, 4, 22): "election-results/2008_Primary_WD.csv",
    date(2008, 11, 4): "election-results/2008_General_WD.csv",
    date(2009, 5, 19): "election-results/2009_Primary_WD.csv",
    date(2009, 11, 3): "election-results/2009_General_WD.csv",
    date(2010, 5, 18): "election-results/2010_Primary_WD.csv",
    date(2010, 11, 2): "election-results/2010_General_WD.csv",
    date(2011, 5, 17): "election-results/2011_Primary_WD.csv",
    date(2011, 11, 8): "election-results/2011_General_WD.csv",
    date(2012, 4, 24): "election-results/2012_Primary_WD.csv",
    date(2012, 11, 6): "election-results/2012_General_WD.csv",
    date(2013, 5, 21): "election-results/2013_Primary_WD.csv",
}
LONG = {
    date(2018, 11, 6): "raw-data/2018_general.csv",
    date(2019, 11, 5): "raw-data/2019_general.csv",
    date(2020, 6, 2): "raw-data/2020_primary.csv",
}


def fetch() -> None:
    for name in [*WIDE.values(), *LONG.values()]:
        raw.fetch(SOURCE, BASE + name, suffix=Path(name).suffix)


def parse() -> None:
    frames = []
    for day, name in WIDE.items():
        url = BASE + name
        wide = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, encoding="utf8-lossy")
        wide = wide.rename({wide.columns[0]: "WARD"})
        frames.append(
            wide.unpivot(
                [column for column in wide.columns if column not in ("WARD", "DIVISION")],
                index=["WARD", "DIVISION"],
                variable_name="column",
                value_name="votes",
            ).with_columns(day=pl.lit(day), url=pl.lit(url))
        )
    wide_rows = (
        pl.concat(frames)
        .filter(pl.col("votes").is_not_null())
        .with_columns(
            contest=pl.col("column").str.extract(r"^.* - (.+)$", 1),
            candidate=pl.col("column").str.extract(r"^(.*) - [^-]+$", 1),
        )
        .filter(pl.col("contest").is_not_null())
    )
    long_rows = []
    for day, name in LONG.items():
        url = BASE + name
        long_rows.append(
            pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, encoding="utf8-lossy")
            .rename({"OFFICE": "CATEGORY", "CANDIDATE": "SELECTION", "VOTES": "VOTE COUNT"}, strict=False)
            .group_by("WARD", "DIVISION", "CATEGORY", "SELECTION", "PARTY")
            .agg(pl.col("VOTE COUNT").cast(pl.Int64).sum().alias("votes"))
            .rename({"CATEGORY": "contest", "SELECTION": "candidate"})
            .with_columns(day=pl.lit(day), url=pl.lit(url), votes=pl.col("votes").cast(pl.String))
        )
    rows = pl.concat(
        [
            wide_rows.select("day", "url", "contest", "WARD", "DIVISION", "candidate", "votes"),
            pl.concat(long_rows).select("day", "url", "contest", "WARD", "DIVISION", "candidate", "votes"),
        ]
    )
    rows = (
        rows.with_columns(
            precinct=pl.col("WARD").str.strip_chars().str.zfill(2) + pl.col("DIVISION").str.strip_chars().str.zfill(2),
            candidate=pl.col("candidate").str.replace_all(r"\s+", " ").str.strip_chars(),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
        )
        .filter(
            pl.col("votes").is_not_null()
            & ~pl.col("candidate").str.contains(r"(?i)write.?in|^(?:over|under)votes?$|^total")
        )
        .select("day", "url", "contest", "precinct", "candidate", "votes")
        .with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64))
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    rows.write_parquet(PARSED / "rows.parquet")
    print(rows.group_by("day").agg(pl.col("contest").n_unique(), pl.col("precinct").n_unique()).sort("day"))


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Philadelphia's contests by ward and division from the City Commissioners' results files",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="42101",
        place="PHILADELPHIA",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
        state_code="PA",
    )
