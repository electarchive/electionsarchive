import io
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_state_generals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    ("NM", "2000-11-07"): ("20001107__nm__general__county.csv", "2000"),
    ("NM", "2002-11-05"): ("20021105__nm__general__county.csv", "2002"),
    ("NM", "2010-11-02"): ("20101102__nm__general.csv", "2010"),
    ("NM", "2012-11-06"): ("20121106__nm__general.csv", "2012"),
    ("TN", "2000-11-07"): ("20001107__tn__general__county.csv", "2000"),
    ("TN", "2002-11-05"): ("20021105__tn__general__county.csv", "2002"),
    ("CO", "2002-11-05"): ("20021105__co__general.csv", "2002"),
}
HOUSE = {"State House", "STATE REPRESENTATIVE"}
SENATE = {"State Senate", "STATE SENATOR"}


def url(state: str, day: str) -> str:
    name, folder = FILES[(state, day)]
    return openelections.REPOSITORY.format(
        state=state.lower(), commit=openelections.COMMITS[state], path=f"{folder}/{name}"
    )


def fetch() -> None:
    for state, day in FILES:
        raw.fetch(SOURCE, url(state, day), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, day in FILES:
        frame = pl.read_csv(
            io.BytesIO(
                raw.cached(SOURCE, url(state, day)).path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            ),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        frames.append(
            frame.filter(
                pl.col("office").is_in([*HOUSE, *SENATE]),
                pl.col("district").str.contains(r"^\d+$"),
                pl.col("county").is_not_null(),
                ~pl.col("county").str.to_uppercase().str.contains("TOTAL"),
            ).select(
                state=pl.lit(state),
                day=pl.lit(day).str.to_date(),
                chamber=pl.concat_str(
                    pl.when(pl.col("office").is_in(list(HOUSE))).then(pl.lit("house")).otherwise(pl.lit("senate")),
                    pl.col("district").cast(pl.Int32).cast(pl.String),
                    pl.lit("g"),
                    separator=":",
                ),
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
        .group_by("state", "chamber", "day", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            district=pl.lit(None, pl.Int16),
            kind=pl.lit("county"),
            source=pl.col("state"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State House and Senate general elections by county from OpenElections: New Mexico 2000, 2002, 2010, and 2012, Tennessee 2000 and 2002, and Colorado 2002",
            polity="US",
        )
        sources = {}
        for state in frame["state"].unique().sort():
            title = f"{state} general election results by county"
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
        for state, day in FILES:
            cs.artifact(raw.cached(SOURCE, url(state, day)), sources[state])
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[0-1][0-9]-[0-9]{2}-[0-9]{2}:[A-Z]{2}:(house|senate):[0-9]+:g'
                     and exists (select 1 from result_set rs join result_value v on v.result_set_k = rs.k
                                 join reporting_unit u on u.k = v.unit_k
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))"""
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(held))
            ),
            sources,
            extend=True,
            partial="the OpenElections county file does not account for every vote in the official total",
        )
        return cs.write()
