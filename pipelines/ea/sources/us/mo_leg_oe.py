import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.mo_leg_oe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = {"State House": "house", "State Senate": "senate"}
FILES = {
    "2000/20001107__mo__general.csv": (date(2000, 11, 7), {}),
    "2014/20140805__mo__primary.csv": (
        date(2014, 8, 5),
        {"DEM": "dp", "REP": "rp", "LIB": "liberp", "CST": "constp"},
    ),
}
TITLE = "MO precinct-level results"


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path)


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["MO"]
    fips["kansascity"] = fips["jackson"]
    frames = []
    for path, (day, parties) in FILES.items():
        frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url(path)).path.read_bytes()), infer_schema_length=0)
        frame = frame.filter(pl.col("office").str.strip_chars().is_in(list(OFFICES)))
        frames.append(
            frame.select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.concat_str(
                    pl.col("office").str.strip_chars().replace_strict(OFFICES),
                    pl.col("district").str.strip_chars().cast(pl.Int32).cast(pl.String),
                    pl.col("party").replace_strict(parties) if parties else pl.lit("g"),
                    separator=":",
                ),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("MO"),
                district=pl.lit(None, pl.Int16),
                code=pl.col("county").str.strip_chars().str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
                kind=pl.lit("county"),
                candidate=pl.col("candidate").str.strip_chars().str.to_uppercase(),
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64),
                source=pl.lit("MO"),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.concat(frames).group_by(pl.exclude("votes"), maintain_order=True).agg(pl.col("votes").sum())
    print(frame.group_by("day").agg(pl.col("chamber").n_unique()).sort("day"))
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's 2000 State House and Senate generals and 2014 legislative primaries by county from "
            "OpenElections",
            polity="US-MO",
        )
        source = cs.source("OpenElections", TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="OpenElections",
                title=TITLE,
                url="https://github.com/openelections/openelections-data-mo",
                license=None,
            )
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        medsl.attach(cs, conn, frame, {"MO": source}, strict=True)
        return cs.write()
