import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.mi_cenr import FILE, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.mi_ppr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = ["2008PPR", "2012PPR"]
TITLE = "Official presidential primary results by county"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, FILE.format(name), suffix=".txt")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%'"""
            ).fetchall()
        }
    frame = pl.concat(
        pl.read_csv(
            raw.cached(SOURCE, FILE.format(name)).path, separator="\t", infer_schema=False, quote_char=None, truncate_ragged_lines=True
        ).filter(pl.col("OfficeDescription").str.starts_with("President"))
        for name in FILES
    ).select(
        year=pl.col("ElectionDate").str.slice(0, 4).cast(pl.Int32),
        chamber=pl.concat_str(
            pl.lit("president:"), pl.col("PartyDescription").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", "-")
        ),
        day=pl.col("ElectionDate").str.to_date(),
        special=pl.lit(False),
        state=pl.lit("MI"),
        district=pl.lit(None, pl.Int16),
        code=pl.col("CountyName")
        .str.to_lowercase()
        .str.replace(r"^st\.? ", "st ")
        .str.replace(r"^gd\. ", "grand ")
        .str.replace_all(r"[^a-z]", "")
        .replace_strict(counties, default=None),
        candidate=pl.concat_str(
            pl.col("CandidateFirstName"), pl.col("CandidateLastName"), separator=" ", ignore_nulls=True
        )
        .str.to_uppercase()
        .str.strip_chars(),
        writein=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("") == "W",
        votes=pl.col("CandidateVotes").cast(pl.Int64),
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate")).group_by(
        "year", "chamber", "day", "special", "state", "district", "code", "candidate"
    ).agg(pl.col("writein").any(), pl.col("votes").sum()).with_columns(
        measure=pl.lit(1, pl.Int16), kind=pl.lit("county"), source=pl.lit("MI")
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's 2008 and 2012 presidential primaries by county",
            polity="US-MI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://miboecfr.nictusa.com/election/results/",
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, FILE.format(name)), source)
        medsl.attach(cs, conn, frame, {"MI": source}, strict=True)
        return cs.write()
