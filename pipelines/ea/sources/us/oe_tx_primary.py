import io
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_tx_primary"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
COMMIT = "c7185327aa987ecc23fefdde0ee53f5238f5da70"
FILES = {
    "2014-03-04": "2014/20140304__tx__primary__county.csv",
    "2014-05-27": "2014/20140527__tx__primary__runoff__county.csv",
}
VARIANTS = ("", "fsettled", "funsettled", "fpartunsettled", "runoff")


def url(day: str) -> str:
    return openelections.REPOSITORY.format(state="tx", commit=COMMIT, path=FILES[day])


def fetch() -> None:
    for day in FILES:
        raw.fetch(SOURCE, url(day), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TX"]
    frames = []
    for day in FILES:
        frame = pl.read_csv(
            io.BytesIO(raw.cached(SOURCE, url(day)).path.read_bytes().replace(b"\r\n", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        frames.append(
            frame.filter(
                pl.col("office").is_in(["State Representative", "State Senate"]),
                pl.col("party").is_in(["DEM", "REP"]),
                pl.col("district").str.contains(r"^\d+$"),
            ).select(
                day=pl.lit(day).str.to_date(),
                chamber=pl.concat_str(
                    pl.when(pl.col("office") == "State Representative").then(pl.lit("house")).otherwise(pl.lit("senate")),
                    pl.col("district").cast(pl.Int32).cast(pl.String),
                    pl.when(pl.col("party") == "DEM").then(pl.lit("dp")).otherwise(pl.lit("rp")),
                    separator=":",
                ),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips, default=None),
                county=pl.col("county"),
                candidate=pl.col("candidate").str.strip_chars(),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            measure=pl.col("candidate").str.to_uppercase().replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").str.to_uppercase().is_in(list(medsl.POOLED))
            | pl.col("candidate").str.to_uppercase().str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("chamber", "day", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("TX"),
            district=pl.lit(None, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("TX"),
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
            summary="Texas's 2014 state House and Senate primaries and runoffs by county from OpenElections",
            polity="US-TX",
        )
        source = cs.source("OpenElections", "TX primary results by county")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="OpenElections",
            title="TX primary results by county",
            url="https://github.com/openelections/openelections-data-tx",
            license=None,
        )
        for day in FILES:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        keys = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^2014-0[35]-[0-9]{2}:TX:(house|senate):[0-9]+:[dr]p'"
            ).fetchall()
        }
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^2014-0[35]-[0-9]{2}:TX:(house|senate):[0-9]+:[dr]p'
                     and exists (select 1 from result_set rs join result_value v on v.result_set_k = rs.k
                                 join reporting_unit u on u.k = v.unit_k
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct'))"""
            ).fetchall()
        }
        frame = frame.with_columns(
            chamber=pl.struct("day", "chamber").map_elements(
                lambda row: next(
                    (
                        f"{row['chamber']}{variant}"
                        for variant in (("runoff",) if str(row["day"]).endswith("05-27") else VARIANTS[:4])
                        if f"{row['day']}:TX:{row['chamber']}{variant}" in keys
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
                ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("TX"), pl.col("chamber"), separator=":").is_in(
                    list(held)
                )
            ),
            {"TX": source},
            extend=True,
        )
        return cs.write()
