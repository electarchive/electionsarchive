from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.leg_primary_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATES = ["ID", "NY", "VT"]


def fetch() -> None:
    pass


def parse() -> None:
    lines = pl.read_parquet(raw.parsed(state_primaries.SOURCE) / "lines.parquet").filter(
        pl.col("state").is_in(STATES),
        pl.col("office").is_in(["house", "senate", "assembly"]),
        pl.col("code").is_not_null(),
        pl.col("scheme") == "us.county",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        lines.with_columns(
            chamber=pl.concat_str(
                "office",
                "code",
                pl.col("party").replace_strict({party: row[2] for party, row in state_primaries.PARTIES.items()}),
                separator=":",
            ),
            candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise(pl.col("choice").str.to_uppercase()),
        )
        .group_by("state", "day", "chamber", "unit", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .select(
            year=pl.col("day").dt.year().cast(pl.Int32),
            chamber="chamber",
            day="day",
            special=pl.lit(False),
            state="state",
            district=pl.lit(None, pl.Int16),
            code="unit",
            candidate="candidate",
            measure=pl.lit(1, pl.Int16),
            writein="writein",
            votes="votes",
            kind=pl.lit("county"),
            source="state",
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
            summary="State House, Senate, and Assembly primaries by county from the ElectionStats archives of Idaho, "
            "New York, and Vermont, for contests held only as district totals",
            polity="US",
        )
        totals = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x
                   where x.scheme = 'us.contest' and x.value ~ '^20[0-9-]{8}:(ID|NY|VT):(house|senate|assembly):'
                     and exists (select from result_set r where r.contest_id = x.entity_id and r.status <> 'withdrawn')
                     and not exists (select from result_set r join result_value v on v.result_set_k = r.k
                                     join reporting_unit u on u.k = v.unit_k
                                     where r.contest_id = x.entity_id and r.status <> 'withdrawn'
                                       and u.scheme_code in ('us.county', 'us.town', 'us.county_part', 'us.precinct'))"""
            ).fetchall()
        }
        frame = frame.filter(
            pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(totals))
        )
        sources = {}
        for state in STATES:
            sources[state] = cs.source(elstats.TENANTS[state][1], elstats.TITLE)
            if sources[state] in cs.minted:
                raise ValueError(f"no source {elstats.TENANTS[state][1]}")
            for office in state_primaries.ELSTATS[state]:
                for year in frame.filter(pl.col("state") == state)["year"].unique().sort():
                    cs.artifact(
                        raw.cached(state_primaries.SOURCE, state_primaries.search(state, office, year)), sources[state]
                    )
        medsl.attach(cs, conn, frame, sources, extend=True)
        return cs.write()
