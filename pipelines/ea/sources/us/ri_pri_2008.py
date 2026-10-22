import io
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ri_federal
from ea.store import DATA, connect

SOURCE = "us.ri_pri_2008"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.ri.gov/election/results/2008/statewide_primary/data/ripri2008l.zip"
DAY = date(2008, 9, 9)
PARTIES = {"DEM": "democratic", "REP": "republican"}
TOWNS = {town.replace("_", " "): code for town, code in ri_federal.TOWNS.items()}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        text = archive.read(archive.namelist()[0]).decode("latin-1")
    rows = []
    for line in text.splitlines():
        title = line[57:113].strip()
        if title == "SENATOR IN CONGRESS":
            chamber, district = "senate", None
        elif title.startswith("REPRESENTATIVE IN CONGRESS DISTRICT"):
            chamber, district = "house", int(title.rsplit(" ", 1)[1])
        else:
            continue
        party = PARTIES.get(line[47:50])
        precinct = line[151:181].strip().lower()
        town = max((t for t in TOWNS if precinct.startswith(t)), key=len, default=None)
        if town is None:
            raise ValueError(f"no town for {precinct}")
        rows.append(
            (
                DAY.year,
                f"{chamber}:{party}",
                DAY,
                False,
                "RI",
                district,
                TOWNS[town],
                line[113:151].strip().upper(),
                1,
                False,
                int(line[11:17]),
            )
        )
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
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("RI")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's September 2008 U.S. Senate and House primaries by county, summed from the precinct file",
            polity="US-RI",
        )
        source = cs.source(ri_federal.PUBLISHER, "Election results: precinct file")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ri_federal.PUBLISHER,
            title="Election results: precinct file",
            url="https://www.ri.gov/election/results/2008/statewide_primary/data/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"RI": source}, strict=True)
        return cs.write()
