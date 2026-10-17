import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA

SOURCE = "us.dc_boe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RESULTS = "https://electionresults.dcboe.org/Downloads/Reports/November_4_2014_General_Election_Certified_Results.csv"
CITYWIDE = "https://electionresults.dcboe.org/cityWide/getCityWide/2014-General-Election"
CONTEST = "ATTORNEY GENERAL DISTRICT OF COLUMBIA"
PUBLISHER = "District of Columbia Board of Elections"
TITLE = "Election results"
PARTIES = {"DEM": "DEMOCRATIC", "REP": "REPUBLICAN", "STG": "D.C. STATEHOOD GREEN", "LIB": "LIBERTARIAN"}
METHOD = (
    "Totals are the sums of the precinct rows in the District of Columbia Board of Elections' certified results file "
    "and match the citywide totals published by the Board."
)
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".csv")
    raw.fetch(SOURCE, CITYWIDE, suffix=".json")


def parse() -> None:
    frame = (
        pl.read_csv(raw.cached(SOURCE, RESULTS).path, infer_schema=False)
        .filter(pl.col("CONTEST_NAME") == CONTEST, ~pl.col("CANDIDATE").is_in(["OVER VOTES", "UNDER VOTES"]))
        .group_by("CANDIDATE", "PARTY")
        .agg(pl.col("VOTES").cast(pl.Int64).sum())
    )
    (published,) = [
        {entry["Contestant"]: entry["Votes"] for entry in contest["ElectionData"]}
        for contest in json.loads(raw.cached(SOURCE, CITYWIDE).path.read_text())
        if contest["OfficeName"] == CONTEST
    ]
    if dict(frame.select("CANDIDATE", "VOTES").iter_rows()) != {
        name: votes for name, votes in published.items() if name not in ("OVER VOTES", "UNDER VOTES")
    }:
        raise ValueError(f"precinct sums differ from the citywide totals: {published}")
    frame = frame.select(
        year=pl.lit(2014, pl.Int32),
        day=pl.lit(date(2014, 11, 4)),
        state=pl.lit("DC"),
        title=pl.lit("Attorney General"),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county=pl.lit("11001"),
        candidate=pl.when(pl.col("CANDIDATE") == "WRITE-IN").then(pl.lit("WRITE-INS")).otherwise("CANDIDATE"),
        party=pl.col("PARTY").replace_strict(PARTIES, default=None),
        writein=pl.col("CANDIDATE") == "WRITE-IN",
        measure=pl.lit(1, pl.Int16),
        votes="VOTES",
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(contest).agg(
        pl.col("magnitude").max(), present=pl.lit(["11001"]), counties=pl.lit(1, pl.Int64)
    ).write_parquet(PARSED / "races.parquet")
    print(frame.sort("votes", descending=True).select("candidate", "party", "votes").rows())


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
                url="https://electionresults.dcboe.org/",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, RESULTS), source)
        cs.artifact(raw.cached(SOURCE, CITYWIDE), source)
        return {"DC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="The District of Columbia's first attorney general election, 2014, from the Board of Elections' "
        "certified results",
        cite=cite,
        method=METHOD,
        close=CLOSE,
    )
