import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.swdb"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CODES = "https://statewidedatabase.org/pub/data/G{year:02d}/c{county:03d}/{county:03d}.codes"
YEARS = range(4, 15, 2)
COUNTIES = range(1, 116, 2)
PUBLISHER = "Statewide Database, University of California, Berkeley"
TITLE = "Statement of Vote data by county"
MERGED = {"CINDY SHEEHAN LEA SHERMAN": "CINDY SHEEHAN"}
LINE = re.compile(r"^(USS|CNG(\d\d))([A-Z]{3})(?:\d\d)?\s+(.*?)\s+(\d+)\s*$")


def fetch() -> None:
    for year in YEARS:
        for county in COUNTIES:
            raw.fetch(SOURCE, CODES.format(year=year, county=county), suffix=".txt")


def parse() -> None:
    rows: list[tuple[int, str, int | None, str, str, int]] = []
    for year in YEARS:
        for county in COUNTIES:
            text = raw.cached(SOURCE, CODES.format(year=year, county=county)).path.read_text(errors="replace")
            for line in text.splitlines():
                found = LINE.match(line)
                if found is None or found.group(3) == "VNC":
                    continue
                candidate = found.group(4).rstrip("*").strip().upper()
                rows.append(
                    (
                        2000 + year,
                        "senate" if found.group(1) == "USS" else "house",
                        None if found.group(2) is None else int(found.group(2)),
                        f"06{county:03d}",
                        MERGED.get(candidate, candidate),
                        int(found.group(5)),
                    )
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            day=pl.col("year").map_elements(
                lambda year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), return_dtype=pl.Date
            ),
            special=pl.lit(False),
            state=pl.lit("CA"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("CA"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's House and Senate general elections by county, 2004–2014",
            polity="US-CA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://statewidedatabase.org/election.html",
            license="No license stated: facts published with attribution, raw files not redistributed",
        )
        for year in YEARS:
            for county in COUNTIES:
                cs.artifact(raw.cached(SOURCE, CODES.format(year=year, county=county)), source)
        medsl.attach(cs, conn, frame, {"CA": source}, redone=frozenset({"2008-11-04:CA:house:8"}))
        return cs.write()
