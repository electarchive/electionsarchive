import csv
import io
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wa_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 8, 2)
URL = "https://results.vote.wa.gov/results/20220802/export/20220802_allcounties.csv"
NAMES = [
    "Adams",
    "Asotin",
    "Benton",
    "Chelan",
    "Clallam",
    "Clark",
    "Columbia",
    "Cowlitz",
    "Douglas",
    "Ferry",
    "Franklin",
    "Garfield",
    "Grant",
    "Grays Harbor",
    "Island",
    "Jefferson",
    "King",
    "Kitsap",
    "Kittitas",
    "Klickitat",
    "Lewis",
    "Lincoln",
    "Mason",
    "Okanogan",
    "Pacific",
    "Pend Oreille",
    "Pierce",
    "San Juan",
    "Skagit",
    "Skamania",
    "Snohomish",
    "Spokane",
    "Stevens",
    "Thurston",
    "Wahkiakum",
    "Walla Walla",
    "Whatcom",
    "Whitman",
    "Yakima",
]
CODES = {re.sub(r"[^a-z]", "", name.lower()): f"53{2 * i + 1:03}" for i, name in enumerate(NAMES)}
RACE = re.compile(r"(?i)congressional district (\d+) u\.s\. representative|(united states u\.s\. senator)")
PUBLISHER = "Washington Secretary of State"
TITLE = "Primary election results"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def parse() -> None:
    rows = []
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf-8-sig")
    for line in csv.DictReader(io.StringIO(text)):
        found = RACE.fullmatch(line["Race"])
        if found is None:
            continue
        rows.append(
            (
                DAY.year,
                "senate" if found[2] else "house",
                DAY,
                False,
                "WA",
                int(found[1]) if found[1] else None,
                CODES[re.sub(r"[^a-z]", "", line["County"].lower())],
                line["Candidate"],
                1,
                "write-in" in line["Candidate"].lower(),
                int(line["Votes"] or 0),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("WA")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2022 U.S. Senate and House top-two primaries by county",
            polity="US-WA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.vote.wa.gov/results/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WA": source}, strict=True)
        return cs.write()
