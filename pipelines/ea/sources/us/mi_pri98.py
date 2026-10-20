import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mi_pri98"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "http://web.archive.org/web/2020id_/http://miboecfr.nictusa.com/election/results/DATA/{}_MI_CENR_BY_COUNTY.xls"
FILES = ["1998PRI"]
PUBLISHER = "Michigan Department of State"
TITLE = "Official general election results by county"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, FILE.format(name), suffix=".txt")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%'"""
            ).fetchall()
        }
    frames = []
    for name in FILES:
        frame = pl.read_csv(
            raw.cached(SOURCE, FILE.format(name)).path, separator="\t", infer_schema=False, quote_char=None
        )
        primary = name.endswith("PRI")
        office = pl.col("OfficeDescription")
        district = office.str.extract(r"^(\d+)\w\w District Representative in Congress")
        frames.append(
            frame.filter(
                district.is_not_null()
                | ((pl.col("OfficeCode(Text)") == "02") & (not primary))
                | office.str.starts_with("United States Senator")
                | office.str.starts_with("President")
            ).select(
                year=pl.col("ElectionDate").str.slice(0, 4).cast(pl.Int32),
                chamber=pl.concat_str(
                    pl.when(district.is_not_null())
                    .then(pl.lit("house"))
                    .when(pl.col("OfficeCode(Text)") == "02")
                    .then(pl.lit("governor"))
                    .when(office.str.starts_with("President"))
                    .then(pl.lit("president"))
                    .otherwise(pl.lit("senate")),
                    pl.when(primary).then(
                        pl.lit(":") + pl.col("PartyDescription").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", "-")
                    ),
                    ignore_nulls=True,
                ),
                day=pl.col("ElectionDate").str.to_date(),
                special=office.str.contains("Partial Term"),
                state=pl.lit("MI"),
                district=district.cast(pl.Int16),
                code=pl.col("CountyName")
                .str.to_lowercase()
                .str.replace(r"^st\.? ", "st ")
                .str.replace(r"^gd\. ", "grand ")
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.concat_str(
                    pl.col("CandidateFirstName"), pl.col("CandidateLastName"), separator=" ", ignore_nulls=True
                )
                .str.to_uppercase()
                .str.strip_chars(),
                writein=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("") == "W",
                votes=pl.col("CandidateVotes").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames)
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(measure=pl.lit(1, pl.Int16), kind=pl.lit("county"), source=pl.lit("MI"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's 1998 House and Senate primaries by county",
            polity="US-MI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://miboecfr.nictusa.com/election/results/",
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, FILE.format(name)), source)
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^199[0-9]-0[5-9]-[0-9]{2}:MI:(house:[0-9]+|senate):[a-z-]+$'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and s.publisher <> 'Federal Election Commission' and s.id <> %s)""",
                [source],
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit("MI"),
                    pl.when(pl.col("district").is_not_null()).then(
                        pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String))
                    ),
                    pl.col("chamber").str.strip_prefix("house:").str.strip_prefix("house"),
                    separator=":",
                    ignore_nulls=True,
                ).is_in(held)
            ),
            {"MI": source},
            strict=True,
        )
        return cs.write()
