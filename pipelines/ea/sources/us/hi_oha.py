import io
import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA

SOURCE = "us.hi_oha"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SUMMARY = "https://files.hawaii.gov/elections/files/results/{year}/general/summary.txt"
YEARS = range(2004, 2015, 2)
OFFSETS = {2004: 39, 2006: 29}
PUBLISHER = "Hawaii Office of Elections"
TITLE = "Election results"
METHOD = (
    "Statewide totals are from the Hawaii Office of Elections' final summary file, which includes absentee mail, "
    "absentee walk-in, and election day votes."
)
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, SUMMARY.format(year=year), suffix=".txt")
        time.sleep(20)


def parse() -> None:
    frames = []
    for year in YEARS:
        text = raw.cached(SOURCE, SUMMARY.format(year=year)).path.read_bytes()
        if not text.startswith(b"Contest ID"):
            lines = [
                (
                    line[OFFSETS[year] : 95].strip(),
                    int(line[4:7]),
                    line[95:158].strip(),
                    int(line[11:18]),
                    int(line[159:161]),
                )
                for line in text.decode("latin-1").splitlines()
                if re.match(r"OHA\b|.*Trustee", line[OFFSETS[year] : 95])
            ]
            frames.append(
                pl.DataFrame(lines, schema=["office", "choice", "name", "votes", "magnitude"], orient="row")
                .filter(pl.col("choice") < 998)
                .with_columns(year=pl.lit(year, pl.Int32))
            )
            continue
        frame = pl.read_csv(io.BytesIO(text), infer_schema=False).with_columns(
            pl.col(column).cast(pl.Int64)
            for column in [
                "Absentee Mail Blank Votes",
                "Absentee Walk-in Blank Votes",
                "Election Blank Votes",
                "Absentee Mail Votes",
                "Absentee Walk-in Votes",
                "Election Votes",
                "Total Votes",
            ]
        )
        if frame.filter(
            pl.col("Total Votes")
            != pl.col("Absentee Mail Votes") + pl.col("Absentee Walk-in Votes") + pl.col("Election Votes")
        ).height:
            raise ValueError(f"{year}: candidate totals do not add up")
        contests = frame.group_by("Contest ID", "Contest Title").agg(
            marks=pl.col("Total Votes").sum()
            + pl.col("Absentee Mail Blank Votes").first()
            + pl.col("Absentee Walk-in Blank Votes").first()
            + pl.col("Election Blank Votes").first()
        )
        ballots = contests.sort(pl.col("Contest ID").cast(pl.Int32))["marks"][0]
        frames.append(
            frame.filter(pl.col("Contest Title").str.contains("Trustee"))
            .join(contests, on=["Contest ID", "Contest Title"])
            .select(
                pl.col("Contest Title").alias("office"),
                pl.col("Candidate Name").alias("name"),
                pl.col("Total Votes").alias("votes"),
                magnitude=(pl.col("marks") / ballots).round().cast(pl.Int64),
                year=pl.lit(year, pl.Int32),
            )
        )
    frame = pl.concat([part.select("year", "office", "name", "votes", "magnitude") for part in frames]).select(
        year="year",
        day=pl.col("year").map_elements(
            lambda year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), return_dtype=pl.Date
        ),
        state=pl.lit("HI"),
        title=pl.lit("Trustee of the Office of Hawaiian Affairs"),
        post=pl.coalesce(
            pl.col("office").str.extract(r"^OHA - (\w+)$"), pl.col("office").str.extract(r"^(\w+) Resident Trustee$")
        ).str.to_uppercase(),
        special=pl.col("office").str.contains("(?i)vacan|unexpired"),
        magnitude=pl.col("magnitude").cast(pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county=pl.lit("HI"),
        candidate=pl.col("name")
        .str.replace(r"^\([A-Z]\) ", "")
        .str.replace(r"^([^,]+), (.+?)(?: (IV|III|II|Jr|Sr)\.?)?$", "$2 $1 $3")
        .str.strip_chars()
        .str.to_uppercase(),
        party=pl.lit(None, pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        present=pl.lit([], pl.List(pl.String)),
        counties=pl.lit(None, pl.Int64),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by([*contest, "magnitude"])
        .agg(pl.col("candidate").sort_by("votes", descending=True), pl.col("votes").sort(descending=True))
        .sort("year", "post")
        .rows()
    )


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
                url="https://elections.hawaii.gov/election-results/",
                license="Public record",
            )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, SUMMARY.format(year=year)), source)
        return {"HI": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Hawaii's Office of Hawaiian Affairs trustee elections 2004–2014 from the Office of Elections' final "
        "summary files",
        cite=cite,
        method=METHOD,
        close=CLOSE,
    )
