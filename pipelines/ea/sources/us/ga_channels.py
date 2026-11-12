import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ga_clarity, ga_federal, medsl
from ea.store import DATA, connect

SOURCE = "us.ga_channels"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
EXPORT = "https://results.sos.ga.gov/cdn/results/Georgia/export-{name}.json"
ELECTIONS = {"2022NovGen": date(2022, 11, 8), "2024NovGen": date(2024, 11, 5)}
CHANNELS = {"election day": 1, "advance voting": 2, "advanced voting": 2, "absentee by mail": 3, "provisional": 5}
STATEWIDE = {**ga_clarity.STATEWIDE, "PRESIDENT OF THE US": "president", "US SENATE": "senate"}


def fetch() -> None:
    for name in ELECTIONS:
        raw.fetch(SOURCE, EXPORT.format(name=name), suffix=".json")


def contest(text: str) -> tuple[str, int | None] | None:
    name = re.sub(r"\s+", " ", text.strip().upper())
    if name in STATEWIDE:
        return STATEWIDE[name], None
    if matched := re.fullmatch(r"US HOUSE OF REPRESENTATIVES - DISTRICT (\d+)", name):
        return "house", int(matched[1])
    if matched := re.fullmatch(r"STATE SENATE - DISTRICT (\d+)", name):
        return f"senate:{int(matched[1])}:g", None
    if matched := re.fullmatch(r"STATE HOUSE OF REPRESENTATIVES - DISTRICT (\d+)", name):
        return f"house:{int(matched[1])}:g", None
    return None


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
    records = []
    for name, day in ELECTIONS.items():
        export = json.loads(raw.cached(SOURCE, EXPORT.format(name=name)).path.read_bytes())
        for locality in export["localResults"]:
            county = re.sub(r"[^a-z]", "", locality["name"].lower().removesuffix(" county"))
            for item in locality["ballotItems"]:
                found = contest(item["name"])
                if found is None:
                    continue
                for option in item["ballotOptions"]:
                    for group in option["groupResults"] or []:
                        records.append(
                            {
                                "day": day,
                                "chamber": found[0],
                                "district": found[1],
                                "county": county,
                                "choice": option["name"],
                                "mode": group["groupName"],
                                "votes": int(group["voteCount"] or 0),
                            }
                        )
    frame = pl.DataFrame(records, infer_schema_length=None)
    frame = frame.with_columns(
        code=pl.col("county").replace_strict(counties, default=None),
        channel=pl.col("mode")
        .str.to_lowercase()
        .str.strip_suffix(" votes")
        .replace_strict(CHANNELS, default=None, return_dtype=pl.Int16),
    )
    for column in ("code", "channel"):
        lost = frame.filter(pl.col(column).is_null())
        if lost.height:
            raise ValueError(f"unmatched {column}: {lost.select('county', 'mode').unique().head(5).rows()}")
    frame = (
        frame.with_columns(
            candidate=pl.col("choice").str.to_uppercase().str.replace(r"(\s*\([^)]*\))+\s*$", "").str.strip_chars()
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "district", "code", "candidate", "measure", "channel")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.col("district").cast(pl.Int16),
            special=pl.lit(False),
            state=pl.lit("GA"),
            kind=pl.lit("county"),
            source=pl.lit("GA"),
        )
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
            summary="Georgia's 2022 and 2024 general elections by county and vote type (election day, advance, mail, and provisional), from the Secretary of State's certified results exports",
            polity="US-GA",
        )
        source = cs.source(ga_federal.PUBLISHER, ga_federal.TITLE)
        if source in cs.minted:
            raise ValueError("no Georgia Secretary of State results source")
        for name in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, EXPORT.format(name=name)), source)
        medsl.attach(cs, conn, frame, {"GA": source})
        return cs.write()
