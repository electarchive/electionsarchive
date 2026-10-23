import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections, wv_sos
from ea.store import DATA, connect

SOURCE = "us.wv_house_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
GENERALS = [election for election, (_, stage) in wv_sos.ELECTIONS.items() if stage == "general"]


def fetch() -> None:
    pass


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["WV"]
    rows = []
    for election in GENERALS:
        day = wv_sos.ELECTIONS[election][0]
        header, *body = (
            raw.cached(wv_sos.SOURCE, wv_sos.URL.format(election))
            .path.read_text(encoding="utf-8-sig", errors="replace")
            .splitlines()
        )
        columns = header.split(",")
        for line in body:
            row = dict(zip(columns, line.split(","), strict=True))
            if row["OfficeCode"] != "CA" or row["CountyName"] == "":
                continue
            rows.append(
                (
                    day,
                    f"house:{int(row['District'])}",
                    int(row["District"]),
                    row["OfficialResults"] == "Yes",
                    fips[re.sub(r"[^a-z]", "", row["CountyName"].lower())],
                    re.sub(r"\s+", " ", row["Name"]).strip().upper(),
                    int(row["Votes"]),
                )
            )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "official": pl.Boolean,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    frame = (
        frame.filter(pl.col("official") == pl.col("official").max().over("day", "chamber", "code"))
        .group_by("day", "chamber", "district", "code", "candidate")
        .agg(pl.col("votes").sum())
        .with_columns(
            chamber=pl.lit("house"),
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("WV"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("WV"),
        )
    )
    print(
        frame.group_by("day", "district").agg(pl.col("code").n_unique(), pl.col("votes").sum()).sort("day", "district")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's U.S. House generals 2008–2016 by county from the Secretary of State's results files",
            polity="US-WV",
        )
        source = cs.source(wv_sos.PUBLISHER, wv_sos.TITLE)
        if source in cs.minted:
            raise ValueError("the Secretary of State's results source must already exist")
        for election in GENERALS:
            cs.artifact(raw.cached(wv_sos.SOURCE, wv_sos.URL.format(election)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WV": source}, strict=True)
        return cs.write()
