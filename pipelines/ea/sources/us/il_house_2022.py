import csv
import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.il_house_2022"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 11, 8)
PUBLISHER = "Illinois State Board of Elections"
TITLE = "November 8, 2022 general election: U.S. House results by precinct"
FILE = "https://www.elections.il.gov/Downloads/ElectionOperations/ElectionResults/ByOffice/62/62-{code}-{district}%20CONGRESS-2022GE.csv"
DISTRICTS = {3: (270, "3RD"), 4: (280, "4TH"), 6: (300, "6TH")}
COUNTIES = {"CITYOFCHICAGO": "17031", "COOK": "17031", "DUPAGE": "17043", "KANE": "17089", "WILL": "17197"}


def url(district: int) -> str:
    code, ordinal = DISTRICTS[district]
    return FILE.format(code=code, district=ordinal)


def fetch() -> None:
    for district in DISTRICTS:
        raw.fetch(SOURCE, url(district), suffix=".csv")


def parse() -> None:
    rows = []
    for district in DISTRICTS:
        text = raw.cached(SOURCE, url(district)).path.read_text(encoding="latin-1")
        for line in csv.DictReader(io.StringIO(text)):
            key = "".join(character for character in line["JurisName"].upper() if character.isalpha())
            rows.append((district, COUNTIES[key], line["CandidateName"].strip().upper(), int(line["VoteCount"])))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["district", "code", "candidate", "votes"], orient="row").group_by(
        "district", "code", "candidate"
    ).agg(pl.col("votes").sum()).select(
        year=pl.lit(2022, pl.Int32),
        chamber=pl.lit("house"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("IL"),
        district=pl.col("district").cast(pl.Int16),
        code="code",
        candidate=pl.when(pl.col("candidate").str.contains("WRITE")).then(pl.lit("WRITE-INS")).otherwise("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
        kind=pl.lit("county"),
        source=pl.lit("IL"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's November 8, 2022 U.S. House elections in districts 3, 4, and 6 by county from the State Board of Elections' precinct results",
            polity="US-IL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.il.gov/ElectionOperations/ElectionVoteTotals.aspx",
            license="Public record",
        )
        for district in DISTRICTS:
            cs.artifact(raw.cached(SOURCE, url(district)), source)
        medsl.attach(cs, conn, frame, {"IL": source}, strict=True)
        return cs.write()
