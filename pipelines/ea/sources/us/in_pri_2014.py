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

SOURCE = "us.in_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2014, 5, 6)
OFFICES = {"State Representative": "house", "State Senator": "senate"}
PARTIES = {"Democratic": "dp", "Republican": "rp"}


def links() -> list[str]:
    return [
        EXPORT
        + urlencode(
            {
                "year": DAY.year,
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
        for office in OFFICES
        for start in range(0, len(NAMES), 10)
    ]


def fetch() -> None:
    for link in links():
        raw.fetch(SOURCE, link, suffix=".csv")


def parse() -> None:
    frames = []
    for link in links():
        text = raw.cached(SOURCE, link).path.read_text(encoding="utf-8-sig", errors="replace")
        head, *body = csv.reader(text.splitlines())
        if body:
            frames.append(pl.DataFrame(body, schema=head, orient="row"))
    frame = (
        pl.concat(frames)
        .filter(pl.col("Party").is_in(list(PARTIES)))
        .with_columns(
            chamber=pl.col("Office").replace_strict(OFFICES)
            + ":"
            + pl.col("District").str.extract(r"^(?:District )?0*(\d+)$", 1)
            + ":"
            + pl.col("Party").replace_strict(PARTIES),
            code=pl.col("County").str.to_lowercase().str.replace_all("[^a-z]", "").replace_strict(CODES),
            candidate=pl.col("Candidate").str.to_uppercase().str.replace_all(r"\s+", " "),
            writein=pl.col("Candidate").str.contains("(?i)write"),
            votes=pl.col("Votes").str.replace_all(",", "").cast(pl.Int64),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("chamber", "code", "candidate").agg(pl.col("writein").any(), pl.col("votes").sum()).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("IN"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IN"),
    ).write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("Office", "Party").agg(pl.col("chamber").n_unique(), pl.col("code").n_unique()))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's 2014 State House and Senate primaries by county (Election Division)",
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
        for link in links():
            cs.artifact(raw.cached(SOURCE, link), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IN": source}, strict=True)
        return cs.write()
