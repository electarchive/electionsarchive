from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mt_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://web.archive.org/web/2022id_/https://sosmt.gov/docs/769/results/47403/2018-primary-precinct-by-precinct-votes.xlsx"
DAY = date(2018, 6, 5)
PARTIES = {"DEM": "democratic", "REP": "republican", "GREEN": "green"}
PUBLISHER = "Montana Secretary of State"
TITLE = "2018 primary election precinct-by-precinct votes"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")


def parse() -> None:
    with connect() as conn:
        fips = {
            name.upper().replace(" & ", " AND "): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%' order by u.code"""
            ).fetchall()
        }
    votes = (
        fastexcel.read_excel(str(raw.cached(SOURCE, URL).path))
        .load_sheet(0, header_row=4)
        .to_polars()
        .filter(pl.col("RaceName").is_in(["UNITED STATES SENATOR", "UNITED STATES REPRESENTATIVE"]))
    )
    if votes["Votes"].null_count() > 0:
        raise ValueError("a precinct row has no votes")
    rows = [
        (
            DAY.year,
            f"{'senate' if race == 'UNITED STATES SENATOR' else 'house'}:{PARTIES[party]}",
            DAY,
            False,
            "MT",
            None if race == "UNITED STATES SENATOR" else 1,
            fips[county.upper().replace(" & ", " AND ")],
            candidate,
            1,
            False,
            int(total),
        )
        for race, party, county, candidate, total in votes.group_by("RaceName", "PartyCode", "CountyName", "NameOnBallot")
        .agg(pl.col("Votes").sum())
        .sort("RaceName", "PartyCode", "CountyName", "NameOnBallot")
        .rows()
    ]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("MT")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's 2018 U.S. Senate and U.S. House primaries by county",
            polity="US-MT",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sosmt.gov/elections/results/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MT": source}, strict=True)
        return cs.write()
