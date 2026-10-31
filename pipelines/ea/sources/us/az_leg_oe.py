import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.az_leg_oe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
GENERAL = {"State Representative": "house", "State House": "house", "State Senate": "senate"}
PRIMARY = {"DEM": "dp", "REP": "rp", "LBT": "liberp", "AEL": "americans-elect"}
FILES = {
    **{
        path: (date(int(path[5:9]), int(path[9:11]), int(path[11:13])), GENERAL, {})
        for path in [
            "2000/20001107__az__general.csv",
            "2002/20021105__az__general.csv",
            "2004/20041102__az__general.csv",
            "2006/20061107__az__general.csv",
            "2008/20081104__az__general.csv",
            "2010/20101102__az__general.csv",
            "2012/20121106__az__general.csv",
            "2014/20141104__az__general.csv",
        ]
    },
    "2002/20020907__az__primary.csv": (
        date(2002, 9, 10),
        {"U.S. House": "house"},
        {"DEM": "democratic", "REP": "republican", "LBT": "libertarian"},
    ),
    "2014/20140826__az__primary.csv": (date(2014, 8, 26), GENERAL, PRIMARY),
}
TITLE = "AZ precinct-level results"


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="az", commit=openelections.COMMITS["AZ"], path=path)


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["AZ"]
    frames = []
    for path, (day, offices, parties) in FILES.items():
        frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url(path)).path.read_bytes()), infer_schema_length=0)
        frame = frame.filter(pl.col("office").str.strip_chars().is_in(list(offices)))
        if parties:
            frame = frame.filter(pl.col("party").is_in(list(parties)))
        frames.append(
            frame.select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.concat_str(
                    pl.col("office").str.strip_chars().replace_strict(offices),
                    pl.col("district").str.strip_chars().cast(pl.Int32).cast(pl.String),
                    pl.col("party").replace_strict(parties) if parties else pl.lit("g"),
                    separator=":",
                ),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("AZ"),
                district=pl.lit(None, pl.Int16),
                code=pl.col("county")
                .str.strip_chars()
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips),
                kind=pl.lit("county"),
                candidate=pl.col("candidate").str.strip_chars().str.to_uppercase(),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("write-in").fill_null("").str.strip_chars() != "",
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64),
                source=pl.lit("AZ"),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.concat(frames)
    print(frame.group_by("day").agg(pl.col("chamber").n_unique()).sort("day"))
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona's 2000 to 2014 State House and Senate generals, 2014 legislative primaries, and 2002 U.S. House "
            "primaries by county from OpenElections",
            polity="US-AZ",
        )
        source = cs.source("OpenElections", TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="OpenElections",
                title=TITLE,
                url="https://github.com/openelections/openelections-data-az",
                license=None,
            )
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        medsl.attach(cs, conn, frame, {"AZ": source}, strict=True)
        return cs.write()
