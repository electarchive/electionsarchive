import csv
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ga_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    date(2014, 5, 20): "2014/20140520__ga__general__primary__county-level.csv",
    date(2014, 7, 22): "2014/20140722__ga__general__primary__runoff__county-level.csv",
    date(2020, 6, 9): "2020/20200609__ga__primary__general.csv",
    date(2020, 8, 11): "2020/20200811__ga__primary__general__runoff.csv",
}
PARTIES = {"Democrat": "democratic", "Democratic": "democratic", "Republican": "republican"}
VOTES = ("election_day_votes", "advanced_votes", "absentee_by_mail_votes", "provisional_votes")
PUBLISHER = "OpenElections"
TITLE = "Georgia primary and runoff results by county, 2014 and 2020"


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="ga", commit=openelections.COMMITS["GA"], path=path)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["GA"]
    totals: dict[tuple[date, str, int | None, str, str, str], int] = {}
    for day, path in FILES.items():
        with raw.cached(SOURCE, url(path)).path.open(newline="") as handle:
            for entry in csv.DictReader(handle):
                office = entry["office"].strip().lower()
                if office not in ("u.s. house", "u.s. senate") or entry["party"] not in PARTIES:
                    continue
                key = (
                    day,
                    "house" if office == "u.s. house" else "senate",
                    int(entry["district"]) if office == "u.s. house" else None,
                    PARTIES[entry["party"]],
                    codes[re.sub(r"[^a-z]", "", entry["county"].lower().replace("saint ", "st "))],
                    re.sub(r"\s+", " ", entry["candidate"]).strip().upper(),
                )
                totals[key] = totals.get(key, 0) + sum(int(entry[column] or 0) for column in VOTES)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [(*key[:3], f"{key[1]}:{key[3]}", *key[4:], votes) for key, votes in totals.items()],
        schema={
            "day": pl.Date,
            "office": pl.String,
            "district": pl.Int16,
            "chamber": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).drop("office").with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("GA"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("OE"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 2014 and 2020 House and Senate primaries and runoffs by county from OpenElections",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://github.com/openelections/openelections-data-ga",
            license="No license stated: facts published with attribution, raw files not redistributed",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OE": source}, strict=True)
        return cs.write()
