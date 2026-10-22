import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_late"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = [
    ("TN", date(2022, 11, 8), "2022/20221108__tn__general__county.csv", "Governor", "governor", None, None, False),
    ("TN", date(2025, 12, 2), "2025/20251202__tn__special__general__county.csv", "U.S. House", "house", 7, None, True),
    (
        "TX",
        date(2016, 3, 1),
        "2016/20160301__tx__democratic__primary__county.csv",
        "President/VicePresident",
        "president:democratic",
        None,
        None,
        False,
    ),
    (
        "TX",
        date(2016, 3, 1),
        "2016/20160301__tx__republican__primary__county.csv",
        "President/VicePresident",
        "president:republican",
        None,
        None,
        False,
    ),
    (
        "TX",
        date(2018, 3, 6),
        "2018/counties/20180306__tx__democratic__primary__county.csv",
        "U.S. Senate",
        "senate:democratic",
        None,
        None,
        False,
    ),
    (
        "TX",
        date(2018, 3, 6),
        "2018/counties/20180306__tx__republican__primary__county.csv",
        "U.S. Senate",
        "senate:republican",
        None,
        None,
        False,
    ),
    (
        "MI",
        date(2020, 8, 4),
        "2020/20200804__mi__primary__county.csv",
        "U.S. House",
        "house:democratic",
        1,
        "DEM",
        False,
    ),
]


def url(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path)


def fetch() -> None:
    for state, _, path, *_ in FILES:
        raw.fetch(SOURCE, url(state, path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, day, path, office, chamber, district, party, special in FILES:
        text = raw.cached(SOURCE, url(state, path)).path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        frame = pl.read_csv(io.BytesIO(text), infer_schema_length=0, truncate_ragged_lines=True).filter(
            pl.col("office") == office,
            pl.col("county").is_not_null(),
            ~pl.col("county").str.to_uppercase().str.contains("TOTAL"),
        )
        if district is not None:
            frame = frame.filter(pl.col("district") == str(district))
        if party is not None:
            frame = frame.filter(pl.col("party") == party)
        frames.append(
            frame.select(
                state=pl.lit(state),
                day=pl.lit(day),
                chamber=pl.lit(chamber),
                district=pl.lit(district, pl.Int16),
                special=pl.lit(special),
                county=pl.col("county"),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips[state], default=None),
                candidate=pl.col("candidate").str.strip_chars(),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null(), pl.col("candidate").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost.select('state', 'county').unique().rows()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            measure=pl.col("candidate")
            .str.to_uppercase()
            .replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").str.to_uppercase().is_in([*medsl.POOLED, "MISC."])
            | pl.col("candidate").str.to_uppercase().str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("state", "chamber", "day", "district", "special", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(year=pl.col("day").dt.year().cast(pl.Int32), kind=pl.lit("county"), source=pl.col("state"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee's 2022 governor and 2025 special U.S. House election, Texas's 2016 presidential and 2018 U.S. Senate primaries, and Michigan's 2020 1st District Democratic primary by county from OpenElections",
            polity="US",
        )
        sources = {}
        for state in frame["state"].unique().sort():
            title = f"{state} election results by county"
            sources[state] = cs.source("OpenElections", title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
        for state, _, path, *_ in FILES:
            cs.artifact(raw.cached(SOURCE, url(state, path)), sources[state])
        medsl.attach(
            cs,
            conn,
            frame,
            sources,
            extend=True,
            partial="the OpenElections county file does not account for every vote in the official total",
        )
        return cs.write()
