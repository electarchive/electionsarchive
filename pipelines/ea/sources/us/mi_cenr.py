import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mi_cenr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    date(
        1998, 11, 3
    ): "http://web.archive.org/web/2020id_/http://miboecfr.nictusa.com/election/results/DATA/1998GEN_MI_CENR_BY_COUNTY.xls",
}
PUBLISHER = "Michigan Department of State"
TITLE = "Official general election results by county"


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".txt")


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
    for day, url in FILES.items():
        frame = pl.read_csv(raw.cached(SOURCE, url).path, separator="\t", infer_schema=False, quote_char=None)
        office = pl.col("OfficeDescription")
        district = office.str.extract(r"^(\d+)\w\w District Representative in Congress")
        frames.append(
            frame.filter(
                district.is_not_null()
                | (pl.col("OfficeCode(Text)") == "02")
                | office.str.starts_with("United States Senator")
                | office.str.starts_with("President")
            ).select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(district.is_not_null())
                .then(pl.lit("house"))
                .when((pl.col("OfficeCode(Text)") == "02"))
                .then(pl.lit("governor"))
                .when(office.str.starts_with("President"))
                .then(pl.lit("president"))
                .otherwise(pl.lit("senate")),
                day=pl.lit(day),
                special=pl.lit(False),
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
            summary="Michigan's 1998 governor and House general elections by county",
            polity="US-MI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://miboecfr.nictusa.com/election/results/98GEN/",
            license="Public record",
        )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"MI": source}, strict=True)
        return cs.write()
