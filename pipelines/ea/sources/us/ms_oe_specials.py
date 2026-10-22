import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ms_oe_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "2008/20080422__ms__special__general__house__1.csv": (date(2008, 4, 22), "house", True),
    "2008/20080513__ms__special__general_runoff__house__1.csv": (date(2008, 5, 13), "house:special:runoff", False),
    "2018/20181127__ms__general_runoff__precinct.csv": (date(2018, 11, 27), "senate", True),
}
PUBLISHER = "OpenElections"
TITLE = "Mississippi election results"


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="ms", commit=openelections.COMMITS["MS"], path=path)


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["MS"]
    frames = []
    for path, (day, chamber, special) in FILES.items():
        frames.append(
            pl.read_csv(raw.cached(SOURCE, url(path)).path, infer_schema_length=0)
            .filter(pl.col("office").str.starts_with("U.S."), ~pl.col("county").str.contains("(?i)^total"))
            .group_by("county", "candidate", maintain_order=True)
            .agg(pl.col("votes").cast(pl.Int64).sum())
            .select(
                day=pl.lit(day),
                chamber=pl.lit(chamber),
                special=pl.lit(special),
                district=pl.lit(None if chamber == "senate" else 1, pl.Int16),
                code=pl.col("county").map_elements(
                    lambda name: codes[re.sub(r"[^a-z]", "", name.lower())], return_dtype=pl.String
                ),
                candidate="candidate",
                votes="votes",
            )
        )
    frame = pl.concat(frames)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("day", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's 2008 First District special election and runoff and 2018 U.S. Senate special runoff "
            "by county from OpenElections",
            polity="US-MS",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://github.com/openelections/openelections-data-ms",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber="chamber",
                day="day",
                special="special",
                state=pl.lit("MS"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MS"),
            ),
            {"MS": source},
            strict=True,
        )
        return cs.write()
