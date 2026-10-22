import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mi_cenr_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "http://web.archive.org/web/2020id_/http://miboecfr.nictusa.com/election/results/DATA/{}_MI_CENR_BY_COUNTY.xls"
FILES = [f"{year}GEN" for year in (2002, 2004, 2006, 2010, 2016, 2020)] + ["2014PRI"]
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
        seat = pl.col("OfficeDescription").str.extract(r"^(\d+)\w\w District (Representative in State Legislature|State Senator)", 1)
        frames.append(
            frame.filter(
                pl.col("OfficeCode(Text)").is_in(["07", "08"]),
                ~pl.col("OfficeDescription").str.contains("Partial Term"),
                pl.col("PartyDescription").is_in(["Democratic", "Republican"]) if primary else pl.lit(True),
            ).select(
                year=pl.col("ElectionDate").str.slice(0, 4).cast(pl.Int32),
                chamber=pl.concat_str(
                    pl.when(pl.col("OfficeCode(Text)") == "08").then(pl.lit("house")).otherwise(pl.lit("senate")),
                    seat,
                    pl.when(primary)
                    .then(pl.when(pl.col("PartyDescription") == "Democratic").then(pl.lit("dp")).otherwise(pl.lit("rp")))
                    .otherwise(pl.lit("g")),
                    separator=":",
                ),
                day=pl.col("ElectionDate").str.to_date(),
                special=pl.lit(False),
                state=pl.lit("MI"),
                district=pl.lit(None, pl.Int16),
                code=pl.col("CountyName")
                .str.to_lowercase()
                .str.replace(r"^st\.? ", "st ")
                .str.replace(r"^gd\. ", "grand ")
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.concat_str(
                    pl.col("CandidateFirstName"), pl.col("CandidateLastName"), separator=" ", ignore_nulls=True
                )
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
            summary="Michigan's 2002–2020 state House and Senate general elections and 2014 primaries by county",
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
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   where r.status = 'active' and x.value ~ '^20[012][0-9]-[0-9]{2}-[0-9]{2}:MI:(house|senate):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))"""
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(covered))
            ),
            {"MI": source},
            extend=True,
        )
        return cs.write()
