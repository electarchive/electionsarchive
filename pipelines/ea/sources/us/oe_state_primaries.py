import io
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_state_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    ("IL", "2014-03-18"): ("1fa096f60367a0fe22dc380cc9799abd5228b89c", "2014/20140318__il__primary__county.csv"),
    ("OR", "2014-05-20"): ("76c478184965392b18afeaf8ab0746e5f401a2e9", "2014/20140520__or__primary__county.csv"),
    ("GA", "2014-05-20"): (
        "36b7453793848c02d8f41601d9304a66ad828dd0",
        "2014/20140520__ga__general__primary__county-level.csv",
    ),
    ("ID", "2014-05-20"): ("f950e6ac1c5e571d7bae0d1b01ddbb1606dcf126", "2014/20140520__id__primary__county.csv"),
}
VARIANTS = ("", "fsettled", "funsettled", "fpartunsettled")


def url(state: str, day: str) -> str:
    commit, path = FILES[(state, day)]
    return openelections.REPOSITORY.format(state=state.lower(), commit=commit, path=path)


def fetch() -> None:
    for state, day in FILES:
        raw.fetch(SOURCE, url(state, day), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, day in FILES:
        frame = pl.read_csv(
            io.BytesIO(raw.cached(SOURCE, url(state, day)).path.read_bytes().replace(b"\r\n", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        if "votes" not in frame.columns:
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in frame.columns
                    if column.endswith("_votes")
                ).cast(pl.String)
            )
        frames.append(
            frame.filter(
                pl.col("office").is_in(["State House", "State Representative", "State Senate"]),
                pl.col("party").str.slice(0, 1).str.to_uppercase().is_in(["D", "R"]),
                pl.col("district").str.contains(r"^\d+[AB]?$"),
            ).select(
                state=pl.lit(state),
                day=pl.lit(day).str.to_date(),
                chamber=pl.concat_str(
                    pl.when(pl.col("office") == "State Senate").then(pl.lit("senate")).otherwise(pl.lit("house")),
                    pl.col("district").str.extract(r"^(\d+)").cast(pl.Int32).cast(pl.String)
                    + pl.col("district").str.extract(r"([AB])$").replace_strict({"A": ":1", "B": ":2"}, default=""),
                    pl.when(pl.col("party").str.to_uppercase().str.starts_with("D"))
                    .then(pl.lit("dp"))
                    .otherwise(pl.lit("rp")),
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
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
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
            summary="State House and Senate primaries by county from OpenElections, 2014 (Georgia, Idaho, Illinois, and "
            "Oregon)",
            polity="US",
        )
        sources = {}
        for state in frame["state"].unique().sort():
            title = f"{state} primary results by county"
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
        keys = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^2014-[0-9]{2}-[0-9]{2}:[A-Z]{2}:(house|senate):[0-9]+(:[12])?:[dr]p'"
            ).fetchall()
        }
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^2014-[0-9]{2}-[0-9]{2}:[A-Z]{2}:(house|senate):[0-9]+(:[12])?:[dr]p'
                     and exists (select 1 from result_set rs join result_value v on v.result_set_k = rs.k
                                 join reporting_unit u on u.k = v.unit_k
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and rs.changeset_id not in (select id from changeset where pipeline = %s)
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct'))""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.with_columns(
            chamber=pl.struct("day", "state", "chamber").map_elements(
                lambda row: next(
                    (
                        f"{row['chamber']}{variant}"
                        for variant in VARIANTS
                        if f"{row['day']}:{row['state']}:{row['chamber']}{variant}" in keys
                    ),
                    row["chamber"],
                ),
                return_dtype=pl.String,
            )
        )
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(held))
            ),
            sources,
            extend=True,
        )
        return cs.write()
