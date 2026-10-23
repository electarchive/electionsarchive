import csv
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_house_2010_18"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RAW = "us.openelections"
URL = "https://raw.githubusercontent.com/openelections/openelections-data-oh/c98f2f7f32c8f1b35bff344ed9b784d0eff31c8d/2010/20101102__oh__general__precinct.csv"
TITLE = "OH 2010 general election results"


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
    seen: set[tuple[str, ...]] = set()
    rows = []
    for row in csv.DictReader(raw.cached(RAW, URL).path.open(encoding="utf-8")):
        if row["office"] != "U.S. Representative" or row["district"] != "18":
            continue
        key = (row["county"], row["precinct code"], row["precinct name"], row["candidate"], row["votes"])
        if key in seen:
            continue
        seen.add(key)
        last, first = row["candidate"].split(", ")
        rows.append(
            (fips[re.sub(r"[^a-z]", "", row["county"].lower())], f"{first} {last}".upper(), int(float(row["votes"])))
        )
    frame = (
        pl.DataFrame(rows, schema=["code", "candidate", "votes"], orient="row")
        .group_by("code", "candidate")
        .agg(pl.col("votes").sum())
    )
    print(frame.group_by("candidate").agg(pl.col("votes").sum()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.lit(2010, pl.Int32),
        chamber=pl.lit("house"),
        day=pl.lit(date(2010, 11, 2)),
        special=pl.lit(False),
        state=pl.lit("OH"),
        district=pl.lit(18, pl.Int16),
        kind=pl.lit("county"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        source=pl.lit("OH"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's 2010 18th District general by county from OpenElections' precinct file, with the exact "
            "duplicate rows it holds for Carroll County dropped",
            polity="US",
        )
        source = cs.source("OpenElections", TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="OpenElections",
                title=TITLE,
                url="https://github.com/openelections/openelections-data-oh",
                license=None,
            )
        cs.artifact(raw.cached(RAW, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OH": source}, strict=True, extend=True)
        return cs.write()
