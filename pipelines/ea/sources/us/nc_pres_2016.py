import io
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.nc_primaries import FILES, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.nc_pres_2016"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2016, 3, 15)
PARTIES = {"DEM": "democratic", "REP": "republican", "LIB": "libertarian"}


def fetch() -> None:
    raw.fetch(SOURCE, FILES.format(day=DAY), suffix=".zip")


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
    with zipfile.ZipFile(raw.cached(SOURCE, FILES.format(day=DAY)).path) as archive:
        text = archive.read(archive.namelist()[0])
    frame = (
        pl.read_csv(io.BytesIO(text), separator="\t", infer_schema=False, encoding="utf8-lossy", truncate_ragged_lines=True)
        .filter(pl.col("Contest Name").str.contains(r"^US PRESIDENT \((DEM|REP|LIB)\)$"))
        .with_columns(
            party=pl.col("Choice Party").str.strip_chars().replace_strict(PARTIES),
            writein=pl.col("Choice").str.to_uppercase().str.contains("WRITE"),
            candidate=pl.col("Choice").str.strip_chars().str.to_uppercase(),
            votes=pl.col("Total Votes").str.replace_all(",", "").cast(pl.Int64),
            code=pl.col("County")
            .str.to_lowercase()
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(counties, default=None),
        )
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['County'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate")).group_by(
        "party", "code", "candidate"
    ).agg(pl.col("writein").any(), pl.col("votes").sum()).select(
        year=pl.lit(2016, pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), "party"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("NC"),
        district=pl.lit(None, pl.Int16),
        code="code",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein="writein",
        votes="votes",
        kind=pl.lit("county"),
        source=pl.lit("NC"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's March 15, 2016 presidential primaries by county from the State Board of Elections' precinct results",
            polity="US-NC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, FILES.format(day=DAY)), source)
        medsl.attach(cs, conn, frame, {"NC": source}, strict=True)
        return cs.write()
