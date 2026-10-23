import re
import unicodedata
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import md_sbe, medsl
from ea.store import DATA, connect

SOURCE = "us.md_leg_sbe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = [2006, 2010, 2014]


def fetch() -> None:
    md_sbe.fetch()


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '24%' order by u.code"""
        ).fetchall():
            plain = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower().replace("saint ", "st "))
            if int(code[2:]) >= 500:
                fips[f"{plain}city"] = code
            fips.setdefault(plain, code)
    frames = []
    for year in YEARS:
        for name in md_sbe.files(year):
            county = re.sub(md_sbe.COUNTY_FILES[year], r"\1", name).replace("_", " ")
            frame = pl.read_csv(
                raw.cached(md_sbe.SOURCE, f"{md_sbe.ARCHIVE}{year}/election_data/{name}").path,
                infer_schema=False,
                encoding="utf8-lossy",
            )
            frames.append(
                frame.rename({column: column.strip() for column in frame.columns})
                .with_columns(pl.col("Office Name").str.strip_chars(), pl.col("Office District").str.strip_chars())
                .filter(pl.col("Office Name").is_in(["House of Delegates", "State Senator"]))
                .select(
                    year=pl.lit(year, pl.Int32),
                    chamber=pl.when(pl.col("Office Name") == "State Senator")
                    .then(pl.concat_str(pl.lit("senate:"), pl.col("Office District").cast(pl.Int32).cast(pl.String)))
                    .otherwise(
                        pl.concat_str(
                            pl.lit("house-of-delegates:"),
                            pl.col("Office District").str.extract(r"^(\d+)").cast(pl.Int32).cast(pl.String),
                            pl.when(pl.col("Office District").str.contains(r"[A-Z]$"))
                            .then(pl.lit("-") + pl.col("Office District").str.extract(r"([A-Z])$").str.to_lowercase())
                            .otherwise(pl.lit("")),
                        )
                    )
                    + ":g",
                    day=pl.lit(md_sbe.general(year)),
                    special=pl.lit(False),
                    state=pl.lit("MD"),
                    district=pl.lit(None, pl.Int16),
                    code=pl.lit(
                        fips[
                            re.sub(
                                r"[^a-z]",
                                "",
                                county.lower().replace("saint ", "st ").replace("st.", "st").replace("county", ""),
                            )
                        ]
                    ),
                    kind=pl.lit("county"),
                    candidate=pl.col("Candidate Name").str.strip_chars().str.to_uppercase(),
                    measure=pl.lit(1, pl.Int16),
                    writein=pl.col("Write-In?").str.strip_chars() == "Y",
                    votes=pl.col("Total Votes").str.strip_chars().cast(pl.Float64, strict=False).cast(pl.Int64),
                    source=pl.lit("MD"),
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).filter(pl.col("chamber").is_not_null(), pl.col("votes").is_not_null()).group_by(
        "year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure", "source"
    ).agg(pl.col("writein").any(), pl.col("votes").sum()).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        filled = [
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status <> 'withdrawn'
                   where x.scheme = 'us.contest' and x.value ~ '^20(06|10|14)-11-0[0-9]:MD:(house-of-delegates|senate):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k and u.scheme_code in ('us.precinct', 'us.county', 'us.county_part'))"""
            ).fetchall()
        ]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 2006, 2010, and 2014 House of Delegates and State Senate generals by county from the "
            "State Board of Elections' results archive",
            polity="US-MD",
        )
        source = cs.source(md_sbe.PUBLISHER, md_sbe.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {md_sbe.TITLE}")
        for year in YEARS:
            for name in md_sbe.files(year):
                cs.artifact(raw.cached(md_sbe.SOURCE, f"{md_sbe.ARCHIVE}{year}/election_data/{name}"), source)
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("MD"), "chamber", separator=":").is_in(filled)
        )
        medsl.attach(
            cs,
            conn,
            frame,
            {"MD": source},
            strict=True,
            authoritative="Source: the State Board of Elections' official county results.",
        )
        return cs.write()
