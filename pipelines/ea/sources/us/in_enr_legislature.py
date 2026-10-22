import csv
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.in_enr_history import EXPORT, LANDING, TITLE
from ea.sources.us.in_federal import CODES, NAMES, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.in_enr_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {
    2002: date(2002, 11, 5),
    2004: date(2004, 11, 2),
    2006: date(2006, 11, 7),
    2008: date(2008, 11, 4),
    2010: date(2010, 11, 2),
}
OFFICES = {"State Representative": "house", "State Senator": "senate"}


def links(year: int) -> list[str]:
    return [
        EXPORT
        + urlencode(
            {
                "year": year,
                "electionType": "General",
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
        for office in OFFICES
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
            head, *body = csv.reader(text.splitlines())
            if body:
                frames.append(pl.DataFrame(body, schema=head, orient="row").with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day)))
    frame = pl.concat(frames).with_columns(
        chamber=pl.col("Office").replace_strict(OFFICES)
        + ":"
        + pl.col("District").str.extract(r"^(?:District )?0*(\d+)$", 1)
        + ":g",
        code=pl.col("County").str.to_lowercase().str.replace_all("[^a-z]", "").replace_strict(CODES),
        candidate=pl.col("Candidate").str.to_uppercase().str.replace_all(r"\s+", " "),
        writein=pl.col("Candidate").str.contains("(?i)write"),
        votes=pl.col("Votes").str.replace_all(",", "").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("year", "day", "chamber", "code", "candidate").agg(
        pl.col("writein").any(), pl.col("votes").sum()
    ).with_columns(
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("IN"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IN"),
    ).write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "Office").agg(pl.col("chamber").n_unique(), pl.col("code").n_unique()).sort("year", "Office"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's state legislative generals by county, 2002-2010 (Election Division)",
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
