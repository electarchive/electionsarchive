from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import statewide_totals
from ea.store import DATA

SOURCE = "us.ks_pri_2014_sw"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = {
    "g": ("governor", "Governor"),
    "s": ("secretary-of-state", "Secretary of State"),
    "i": ("commissioner-of-insurance", "Commissioner of Insurance"),
}
URL = "https://sos.ks.gov/elections/14elec/2014_Official_Primary_Election_Results_by_county.pdf"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    tables = pl.read_parquet(DATA / "parsed" / "us.scans" / "ks-pri-2014" / "tables.parquet")
    lines = (
        tables.group_by("contest", "candidate")
        .agg(pl.col("votes").sum(), pl.col("page").min())
        .select(
            source=pl.lit("ks-pri-2014"),
            state=pl.lit("KS"),
            date=pl.lit("2014-08-05"),
            office=pl.col("contest").replace_strict({key: slug for key, (slug, _) in OFFICES.items()}),
            title=pl.col("contest").replace_strict({key: title for key, (_, title) in OFFICES.items()}),
            name=pl.col("candidate"),
            party=pl.lit("Republican"),
            votes=pl.col("votes"),
            winner=(pl.col("votes") == pl.col("votes").max().over("contest")).cast(pl.Int64),
            page=pl.col("page"),
            stage=pl.lit("primary"),
            scope=pl.lit("Republican"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    lines.write_parquet(PARSED / "lines.parquet")
    print(lines.select("office", "name", "votes", "winner"))


def emit() -> Path:
    book = {
        "ks-pri-2014": {
            "publisher": "Kansas Secretary of State",
            "title": "2014 primary election official results by county",
            "url": URL,
            "landing": "https://sos.ks.gov/elections/election-results.html",
            "license": "Public record",
            "page_offset": 0,
            "kind": "official_body",
        }
    }
    return statewide_totals.emit(
        SOURCE,
        VERSION,
        "Kansas's 2014 Republican primaries for governor, secretary of state, and commissioner of insurance by state total from the county tables",
        pl.read_parquet(PARSED / "lines.parquet"),
        book,
        "Statewide total summed from the Secretary of State's county table.",
    )
