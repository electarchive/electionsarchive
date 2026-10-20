import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.in_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://enr.indianavoters.in.gov/archive/2022Primary/download/AllOfficeResults.csv"
DAY = date(2022, 5, 3)
NAMES = (
    "Adams,Allen,Bartholomew,Benton,Blackford,Boone,Brown,Carroll,Cass,Clark,Clay,Clinton,Crawford,Daviess,Dearborn,"
    "Decatur,DeKalb,Delaware,Dubois,Elkhart,Fayette,Floyd,Fountain,Franklin,Fulton,Gibson,Grant,Greene,Hamilton,"
    "Hancock,Harrison,Hendricks,Henry,Howard,Huntington,Jackson,Jasper,Jay,Jefferson,Jennings,Johnson,Knox,Kosciusko,"
    "LaGrange,Lake,LaPorte,Lawrence,Madison,Marion,Marshall,Martin,Miami,Monroe,Montgomery,Morgan,Newton,Noble,Ohio,"
    "Orange,Owen,Parke,Perry,Pike,Porter,Posey,Pulaski,Putnam,Randolph,Ripley,Rush,St. Joseph,Scott,Shelby,Spencer,"
    "Starke,Steuben,Sullivan,Switzerland,Tippecanoe,Tipton,Union,Vanderburgh,Vermillion,Vigo,Wabash,Warren,Warrick,"
    "Washington,Wayne,Wells,White,Whitley"
).split(",")
CODES = {re.sub(r"[^a-z]", "", name.lower()): f"18{2 * i + 1:03}" for i, name in enumerate(NAMES)}
ORDINALS = "First Second Third Fourth Fifth Sixth Seventh Eighth Ninth".split()
PARTIES = {"Democratic": "democratic", "Republican": "republican", "Libertarian": "libertarian"}
PUBLISHER = "Indiana Election Division"
TITLE = "Election night reporting results archive"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def parse() -> None:
    frame = (
        pl.read_csv(raw.cached(SOURCE, URL).path, infer_schema=False, encoding="utf8-lossy")
        .filter(
            pl.col("Office").str.starts_with("United States")
            & pl.col("PoliticalParty").is_in(list(PARTIES))
            & ~pl.col("NameonBallot").str.contains("(?i)write")
        )
        .with_columns(
            chamber=pl.when(pl.col("Office").str.contains("Senator"))
            .then(pl.lit("senate:"))
            .otherwise(pl.lit("house:"))
            + pl.col("PoliticalParty").replace(PARTIES),
            district=pl.col("Office")
            .str.extract(r"Representative, (\w+) District", 1)
            .replace_strict({o: i + 1 for i, o in enumerate(ORDINALS)}, default=None, return_dtype=pl.Int16),
            code=pl.col("ReportingCountyName").str.to_lowercase().str.replace_all("[^a-z]", "").replace_strict(CODES),
            candidate=pl.col("NameonBallot").str.to_uppercase(),
            votes=pl.col("TotalVotes").cast(pl.Int64),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("chamber", "district", "code", "candidate").agg(pl.col("votes").sum()).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("IN"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("IN"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's 2022 U.S. Senate and House primaries by county",
            polity="US-IN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://enr.indianavoters.in.gov/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IN": source}, strict=True)
        return cs.write()
