import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_pri_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/"
FILES = {"democratic": "06062000DEM.pdf", "republican": "06062000REP.pdf"}
DAY = date(2000, 6, 6)
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election data: 2000 primary elections"
TABLES = {
    "democratic": (
        ("Artur Davis", "Earl Hilliard", "Wayne Sowell"),
        {
            "Choctaw": (1072, 1541, 301),
            "Clarke": (77, 150, 58),
            "Dallas": (2927, 6508, 1613),
            "Greene": (1163, 1403, 210),
            "Hale": (1196, 2948, 532),
            "Jefferson": (6462, 6998, 370),
            "Lowndes": (1112, 2040, 273),
            "Marengo": (1687, 2282, 480),
            "Montgomery": (1080, 3281, 205),
            "Perry": (931, 2544, 278),
            "Pickens": (224, 304, 66),
            "Sumter": (497, 1770, 86),
            "Tuscaloosa": (1667, 2038, 300),
            "Wilcox": (878, 2442, 383),
        },
        (20973, 36249, 5155),
    ),
    "republican": (
        ("Milton Bethune", "Ed Martin"),
        {
            "Clarke": (5, 7),
            "Choctaw": (22, 60),
            "Dallas": (34, 109),
            "Greene": (45, 56),
            "Hale": (8, 20),
            "Jefferson": (780, 1153),
            "Lowndes": (21, 22),
            "Marengo": (46, 78),
            "Montgomery": (228, 309),
            "Perry": (13, 16),
            "Pickens": (2, 0),
            "Sumter": (50, 77),
            "Tuscaloosa": (660, 1096),
            "Wilcox": (9, 15),
        },
        (1923, 3018),
    ),
}


def fetch() -> None:
    for file in FILES.values():
        try:
            raw.cached(SOURCE, BASE + file)
        except LookupError:
            raw.fetch(SOURCE, BASE + file, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    rows = []
    for party, (names, counties, totals) in TABLES.items():
        for column, name in enumerate(names):
            if sum(votes[column] for votes in counties.values()) != totals[column]:
                raise ValueError(f"{party} {name}: county rows do not add up to the printed total")
            for county, votes in counties.items():
                rows.append((party, name.upper(), fips[county.lower()], votes[column]))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={"party": pl.String, "candidate": pl.String, "code": pl.String, "votes": pl.Int64},
            orient="row",
        )
        .with_columns(
            day=pl.lit(DAY),
            year=pl.lit(2000, pl.Int32),
            chamber=pl.lit("house:") + pl.col("party"),
            special=pl.lit(False),
            state=pl.lit("AL"),
            district=pl.lit(7, pl.Int16),
            measure=pl.lit(1, pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .drop("party")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2000 7th District House primaries by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        for file in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + file), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"AL": source},
            authoritative="Source: the Secretary of State's county tables for the June 6, 2000 primary, whose rows add up to each candidate's printed total.",
        )
        return cs.write()
