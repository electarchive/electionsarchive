import io
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.in_federal import CODES, NAMES, PARTIES, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.in_enr_history"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
EXPORT = "https://indianavoters.in.gov/ENRHistorical/ExportElectionsToCSV?"
LANDING = "https://indianavoters.in.gov/ENRHistorical/ElectionResults"
TITLE = "Historical election results"
DAYS = {
    2002: date(2002, 5, 7),
    2004: date(2004, 5, 4),
    2006: date(2006, 5, 2),
    2008: date(2008, 5, 6),
    2010: date(2010, 5, 4),
    2012: date(2012, 5, 8),
    2014: date(2014, 5, 6),
    2016: date(2016, 5, 3),
    2018: date(2018, 5, 8),
}
OFFICES = {"United States Representative": "house", "United States Senator": "senate", "President": "president"}
RENAMED = {"US Representative": "house", "US Senator": "senate"}


def links(year: int) -> list[str]:
    return [
        EXPORT
        + urlencode(
            {
                "year": year,
                "electionType": "Primary",
                "candidateName": "",
                "county": ",".join(NAMES[start : start + 10]),
                "office": office,
                "party": "",
                "sortBy": "",
                "sortDir": "",
                "filterCandidateName": "",
                "filterElectionType": "false",
            }
        )
        for office in (RENAMED if year >= 2018 else OFFICES)
        for start in range(0, len(NAMES), 10)
    ]


def fetch() -> None:
    for year in DAYS:
        for link in links(year):
            raw.fetch(SOURCE, link, suffix=".csv")


def parse() -> None:
    frames = []
    for year, day in DAYS.items():
        for link in links(year):
            text = raw.cached(SOURCE, link).path.read_text(encoding="utf-8-sig", errors="replace")
            frame = pl.read_csv(io.StringIO(text), infer_schema=False)
            if frame.height:
                frames.append(frame.with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day)))
    frame = (
        pl.concat(frames)
        .filter(pl.col("Party").is_in(list(PARTIES)) & ~pl.col("Candidate").str.contains("(?i)write"))
        .with_columns(
            chamber=pl.col("Office").replace_strict(OFFICES | RENAMED) + ":" + pl.col("Party").replace(PARTIES),
            district=pl.col("District").str.extract(r"^(?:District )?(\d+)$", 1).cast(pl.Int16),
            code=pl.col("County").str.to_lowercase().str.replace_all("[^a-z]", "").replace_strict(CODES),
            candidate=pl.col("Candidate").str.to_uppercase(),
            votes=pl.col("Votes").str.replace_all(",", "").cast(pl.Int64),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("year", "day", "chamber", "district", "code", "candidate").agg(pl.col("votes").sum()).with_columns(
        special=pl.lit(False),
        state=pl.lit("IN"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("IN"),
    ).write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "chamber").agg(pl.col("district").n_unique(), pl.col("code").n_unique()).sort("year", "chamber"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's U.S. House, Senate, and presidential primaries by county, 2002-2018 (Election Division)",
            polity="US-IN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for year in DAYS:
            for link in links(year):
                cs.artifact(raw.cached(SOURCE, link), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IN": source}, strict=True)
        return cs.write()
