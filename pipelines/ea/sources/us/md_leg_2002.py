import io
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import md_sbe, medsl
from ea.store import DATA, connect

SOURCE = "us.md_leg_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = f"{md_sbe.ARCHIVE}2002/results/g_all_offices.txt"
DAY = date(2002, 11, 5)
COLUMNS = ["office", "district", "county", "last", "middle", "first", "party", "winner", "vote_for", "votes", "rest"]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".txt")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))
            + ("city" if int(code[2:]) >= 500 else ""): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '24%' order by u.code"""
            ).fetchall()
        }
    frame = pl.read_csv(
        io.StringIO(raw.cached(SOURCE, URL).path.read_bytes().decode("cp1252")),
        separator="|",
        has_header=False,
        new_columns=COLUMNS,
        infer_schema=False,
        null_values="\\N",
        quote_char=None,
    ).with_columns(pl.all().str.strip_chars())
    frame = frame.filter(pl.col("office").is_in(["House of Delegates", "State Senator"])).select(
        year=pl.lit(DAY.year, pl.Int32),
        chamber=pl.when(pl.col("office") == "State Senator")
        .then(pl.concat_str(pl.lit("senate:"), pl.col("district").cast(pl.Int32).cast(pl.String)))
        .otherwise(
            pl.concat_str(
                pl.lit("house-of-delegates:"),
                pl.col("district").str.extract(r"^(\d+)").cast(pl.Int32).cast(pl.String),
                pl.when(pl.col("district").str.contains(r"[A-Z]$"))
                .then(pl.lit("-") + pl.col("district").str.extract(r"([A-Z])$").str.to_lowercase())
                .otherwise(pl.lit("")),
            )
        )
        + ":g",
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("MD"),
        district=pl.lit(None, pl.Int16),
        code=pl.col("county")
        .str.to_lowercase()
        .str.replace(r"^saint |^st\. ", "st ")
        .str.replace(r" county$", "")
        .str.replace_all(r"[^a-z]", "")
        .replace_strict(fips),
        kind=pl.lit("county"),
        candidate=pl.when(pl.col("last").str.starts_with("zz"))
        .then(pl.lit("WRITE-INS"))
        .otherwise(pl.concat_str(pl.col("first"), pl.col("last"), separator=" ").str.to_uppercase()),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("last").str.starts_with("zz"),
        votes=pl.col("votes").cast(pl.Int64),
        source=pl.lit("MD"),
    )
    print(frame.group_by(office=pl.col("chamber").str.extract(r"^([a-z-]+)")).agg(pl.col("chamber").n_unique()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 2002 House of Delegates and State Senate generals by county from the State Board of "
            "Elections' results archive",
            polity="US-MD",
        )
        source = cs.source(md_sbe.PUBLISHER, md_sbe.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {md_sbe.TITLE}")
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"MD": source},
            strict=True,
            authoritative="Source: the State Board of Elections' official county results.",
        )
        return cs.write()
