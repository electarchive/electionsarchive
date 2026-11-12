import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, nc_statewide, ncsbe
from ea.store import DATA, connect

SOURCE = "us.nc_channels"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [date(2022, 11, 8), date(2024, 11, 5)]
CHANNELS = {"election day": 1, "one stop": 2, "early voting": 2, "absentee by mail": 3, "provisional": 5}
STATEWIDE = {**nc_statewide.OFFICES, "GOVERNOR": "Governor"}
COURT = r"^(COURT OF APPEALS JUDGE|SUPREME COURT (?:ASSOCIATE|ASSOC) JUSTICE) SEAT 0*(\d+)$"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(ncsbe.SOURCE, ncsbe.FILES.format(day=day), suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    frames = []
    for day in DAYS:
        frame = ncsbe.read(day)
        name = (
            pl.col("contest")
            .str.strip_chars()
            .str.replace_all(r"\s+", " ")
            .str.to_uppercase()
            .str.strip_prefix("NC ")
            .str.replace(r"^US PRESIDENT$", "PRESIDENT")
        )
        parts = name.str.extract_groups(ncsbe.CONTEST.pattern)
        seat = name.str.extract_groups(ncsbe.STATE)
        court = name.str.extract_groups(COURT)
        title = name.replace_strict(STATEWIDE, default=None)
        channels = [column for column in frame.columns if column in CHANNELS]
        frames.append(
            frame.with_columns(parts=parts, seat=seat, court=court, title=title, name=name)
            .filter(
                pl.col("parts").struct.field("1").is_not_null()
                | pl.col("seat").struct.field("1").is_not_null()
                | pl.col("court").struct.field("1").is_not_null()
                | pl.col("title").is_not_null()
                | pl.col("name").str.starts_with("PRESIDENT")
            )
            .unpivot(
                channels,
                index=["county", "parts", "seat", "court", "title", "name", "choice"],
                variable_name="mode",
                value_name="count",
            )
            .select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(pl.col("seat").struct.field("1").is_not_null())
                .then(
                    pl.concat_str(
                        pl.col("seat").struct.field("1").str.to_lowercase(),
                        pl.col("seat").struct.field("2").cast(pl.Int16).cast(pl.String),
                        pl.lit("g"),
                        separator=":",
                    )
                )
                .when(pl.col("court").struct.field("1").is_not_null())
                .then(
                    pl.when(pl.col("court").struct.field("1").str.contains("APPEALS"))
                    .then(pl.lit("judge-of-the-court-of-appeals:seat-"))
                    .otherwise(pl.lit("justice-of-the-supreme-court:seat-"))
                    + pl.col("court").struct.field("2").cast(pl.Int16).cast(pl.String)
                )
                .when(pl.col("parts").struct.field("1") == "SENATE")
                .then(pl.lit("senate"))
                .when(pl.col("parts").struct.field("1").is_not_null())
                .then(pl.lit("house"))
                .when(pl.col("title").is_not_null())
                .then(pl.col("title").str.to_lowercase().str.replace_all(" ", "-"))
                .otherwise(pl.lit("president")),
                day=pl.lit(day),
                special=pl.col("name").str.contains("UNEXPIRED|SPECIAL"),
                state=pl.lit("NC"),
                district=pl.coalesce(pl.col("parts").struct.field("3"), pl.col("parts").struct.field("4")).cast(
                    pl.Int16
                ),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.when(pl.col("name").str.starts_with("PRESIDENT"))
                .then(
                    pl.col("choice")
                    .str.to_uppercase()
                    .str.replace(r"/.*$", "")
                    .str.replace(r"-[A-Z]\. .*$", "")
                    .str.strip_chars()
                )
                .otherwise(pl.col("choice").str.to_uppercase().str.strip_chars()),
                channel=pl.col("mode").replace_strict(CHANNELS, return_dtype=pl.Int16),
                votes=pl.col("count").str.replace_all(",", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    frame = (
        frame.with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "channel")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("NC"))
        .sort("day", "chamber", "district", "code", "candidate", "channel")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "channels.parquet")
    print(frame.group_by("year", "channel").agg(pl.col("votes").sum()).sort("year", "channel").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "channels.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's 2022 and 2024 general elections by county and ballot type (election day, early, mail, and provisional), from the State Board's certified precinct files",
            polity="US-NC",
        )
        source = cs.source(ncsbe.PUBLISHER, ncsbe.TITLE)
        if source in cs.minted:
            raise ValueError("no North Carolina State Board of Elections source")
        for day in DAYS:
            cs.artifact(raw.cached(ncsbe.SOURCE, ncsbe.FILES.format(day=day)), source)
        medsl.attach(cs, conn, frame, {"NC": source})
        return cs.write()
