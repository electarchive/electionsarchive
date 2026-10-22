import csv
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_pri_2022"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-025" / "2022_Primary_Federal.csv"
URL = "https://results.enr.clarityelections.com/AR/"
DAY = date(2022, 5, 24)
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Election results: 2022 preferential primary, federal contests by county"
WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE, URL, FILE.read_bytes(), suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv"
        )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%'"""
            ).fetchall()
        }
    rows = []
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf-8-sig")
    for record in csv.DictReader(text.splitlines()):
        if record["Location"] == "Statewide":
            continue
        contest = record["Contest Name"].upper()
        party = "democratic" if "DEM" in contest else "republican"
        if contest.startswith("U.S. SENATE"):
            chamber, district = "senate", None
        else:
            found = re.search(r"(?:DISTRICT|DST) (\w+)", contest)
            chamber, district = "house", WORDS.get(found[1]) or int(found[1])
        rows.append(
            (
                f"{chamber}:{party}",
                district,
                counties[re.sub(r"[^a-z]", "", record["Location"].lower())],
                record["Candidate Name"].upper(),
                int(record["Candidate Votes"]),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"chamber": pl.String, "district": pl.Int64, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        year=pl.lit(2022, pl.Int32),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("AR"),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("AR"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's May 24, 2022 U.S. Senate and House primaries by county from the Secretary of State's results export",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AR": source}, strict=True)
        return cs.write()
