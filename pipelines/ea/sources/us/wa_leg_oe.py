import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.wa_leg_oe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {"2006/20061107__wa__general.csv": date(2006, 11, 7)}
TITLE = "WA precinct-level results"


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="wa", commit=openelections.COMMITS["WA"], path=path)


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["WA"]
    frames = []
    for path, day in FILES.items():
        frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url(path)).path.read_bytes()), infer_schema_length=0)
        frames.append(
            frame.filter(
                pl.col("reporting_level") == "county",
                pl.col("officename").str.contains(r"^State (Representative|Senate) District #\d+$"),
            ).select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(pl.col("officename").str.starts_with("State Senate"))
                .then(pl.concat_str(pl.lit("senate:"), pl.col("officename").str.extract(r"#(\d+)$"), pl.lit(":g")))
                .otherwise(
                    pl.concat_str(
                        pl.lit("house:"),
                        pl.col("officename").str.extract(r"#(\d+)$"),
                        pl.lit(":"),
                        pl.col("officeposition").str.extract(r"#(\d)$"),
                        pl.lit(":g"),
                    )
                ),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("WA"),
                district=pl.lit(None, pl.Int16),
                code=pl.col("jurisdiction").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
                kind=pl.lit("county"),
                candidate=pl.col("ballotname").str.strip_chars().str.to_uppercase(),
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64),
                source=pl.lit("WA"),
            )
        )
    frame = pl.concat(frames)
    if frame["chamber"].is_null().any():
        raise ValueError("a State Representative row has no position")
    print(frame.group_by(office=pl.col("chamber").str.split(":").list.first()).agg(pl.col("chamber").n_unique()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2006 State House and Senate generals by county from OpenElections",
            polity="US-WA",
        )
        source = cs.source("OpenElections", TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="OpenElections",
                title=TITLE,
                url="https://github.com/openelections/openelections-data-wa",
                license=None,
            )
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        medsl.attach(cs, conn, frame, {"WA": source}, strict=True)
        return cs.write()
