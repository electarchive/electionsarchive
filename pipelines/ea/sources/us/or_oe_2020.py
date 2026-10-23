import io
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.or_oe_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PATH = "2020/20201103__or__general__precinct.csv"
DAY = date(2020, 11, 3)
OFFICES = {
    "Secretary of State": "Secretary of State",
    "State Treasurer": "Treasurer",
    "Attorney General": "Attorney General",
}
PEOPLE = {
    "ELLEN ROSENBLUM": ("ELLEN ROSENBLUM", "DEMOCRATIC"),
    "ELLEN ROSEBLUM": ("ELLEN ROSENBLUM", "DEMOCRATIC"),
    "MICHAEL CROSS": ("MICHAEL CROSS", "REPUBLICAN"),
    "LARS D H HEDBOR": ("LARS D.H. HEDBOR", "LIBERTARIAN"),
    "LARS D. H. HEDBOR": ("LARS D.H. HEDBOR", "LIBERTARIAN"),
    "LARS D.H. HEDBOR": ("LARS D.H. HEDBOR", "LIBERTARIAN"),
    "LARS HEDBOR": ("LARS D.H. HEDBOR", "LIBERTARIAN"),
    "SHEMIA FAGAN": ("SHEMIA FAGAN", "DEMOCRATIC"),
    "KIM THATCHER": ("KIM THATCHER", "REPUBLICAN"),
    "KYLE MARKLEY": ("KYLE MARKLEY", "LIBERTARIAN"),
    "KYLE MARKEY": ("KYLE MARKLEY", "LIBERTARIAN"),
    "NATHALIE PARAVICINI": ("NATHALIE PARAVICINI", "PACIFIC GREEN"),
    "NATALIE PARAVICINI": ("NATHALIE PARAVICINI", "PACIFIC GREEN"),
    "NATHALIE PARACIVINI": ("NATHALIE PARAVICINI", "PACIFIC GREEN"),
    "NATHALIE PARAVACINI": ("NATHALIE PARAVICINI", "PACIFIC GREEN"),
    "TOBIAS READ": ("TOBIAS READ", "DEMOCRATIC"),
    "TOBIAS REED": ("TOBIAS READ", "DEMOCRATIC"),
    "JEFF GUDMAN": ("JEFF GUDMAN", "REPUBLICAN"),
    "CHRIS HENRY": ("CHRIS HENRY", "INDEPENDENT"),
    "CHRIS HEN": ("CHRIS HENRY", "INDEPENDENT"),
    "IND CHRIS HENRY": ("CHRIS HENRY", "INDEPENDENT"),
    "MICHAEL P MARSH": ("MICHAEL P. MARSH", "CONSTITUTION"),
    "MICHAEL MARSH": ("MICHAEL P. MARSH", "CONSTITUTION"),
    "MICHAEL P. MARSH": ("MICHAEL P. MARSH", "CONSTITUTION"),
    "MICHAEL P. MARSCH": ("MICHAEL P. MARSH", "CONSTITUTION"),
}
METHOD = (
    "County totals are the sums of the precinct rows in OpenElections' file for Oregon's November 3, 2020 general "
    "election, which compiles the state's precinct returns; spelling variants of a candidate's name are merged. The "
    "winner shown is the candidate with the most votes in those sums."
)
COLUMNS = ["year", "day", "state", "title", "county", "candidate", "party", "writein", "votes"]


def url() -> str:
    return openelections.REPOSITORY.format(state="or", commit=openelections.COMMITS["OR"], path=PATH)


def fetch() -> None:
    raw.fetch(SOURCE, url(), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["OR"]
    frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url()).path.read_bytes()), infer_schema_length=0)
    frame = frame.filter(pl.col("office").is_in(list(OFFICES))).with_columns(
        name=pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase()
    )
    skipped = frame.filter(
        ~pl.col("name").str.contains(r"^(?:OVER(?:\s?VOTES?)?|UNDER\s?VOTES?)$|^WRI")
        & ~pl.col("name").is_in(list(PEOPLE))
    )
    if skipped.height:
        raise ValueError(f"unmapped candidates: {skipped['name'].unique().to_list()}")
    returns = (
        frame.filter(~pl.col("name").str.contains(r"^(?:OVER(?:\s?VOTES?)?|UNDER\s?VOTES?)$"))
        .with_columns(
            writein=~pl.col("name").is_in(list(PEOPLE)),
            candidate=pl.col("name").replace_strict(
                {name: person for name, (person, _) in PEOPLE.items()}, default="WRITE-IN"
            ),
            party=pl.col("name").replace_strict({name: label for name, (_, label) in PEOPLE.items()}, default=None),
        )
        .select(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            state=pl.lit("OR"),
            title=pl.col("office").replace_strict(OFFICES),
            county=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
            candidate="candidate",
            party="party",
            writein="writein",
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
        )
        .with_columns(
            post=pl.lit(None, pl.String),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            measure=pl.lit(1, pl.Int16),
        )
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = returns.group_by(
        *contest, "magnitude", "kind", "code", "county", "candidate", "party", "writein", "measure", maintain_order=True
    ).agg(pl.col("votes").sum())
    races = returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(36),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(races.select(*contest[:4], "counties", pl.col("present").list.len()))
    print(returns.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes"))


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source("OpenElections", "OR precinct-level results")
        if source in cs.minted:
            raise ValueError("no source OR precinct-level results")
        cs.artifact(raw.cached(SOURCE, url()), source)
        return {"OR": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Oregon's 2020 secretary of state, treasurer, and attorney general generals by county",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
    )
