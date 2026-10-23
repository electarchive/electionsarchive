import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import md_sbe, medsl
from ea.store import DATA, connect

SOURCE = "us.md_leg_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2014, 6, 24)
PARTIES = {"Democratic": "dp", "Republican": "rp"}


def urls() -> dict[str, tuple[str, str]]:
    return {
        f"{md_sbe.ARCHIVE}{DAY.year}/election_data/{name.replace('_2014_General.csv', '')}_{party}_2014_Primary.csv": (
            name.removesuffix("_2014_General.csv").removesuffix("_County").replace("_", " "),
            suffix,
        )
        for name in md_sbe.files(DAY.year)
        for party, suffix in PARTIES.items()
    }


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".csv")


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
    for url, (county, suffix) in urls().items():
        text = raw.cached(SOURCE, url).path.read_bytes()
        if text.lstrip().startswith(b"<"):
            raise ValueError(f"{url} is not a results file")
        frame = pl.read_csv(io.StringIO(text.decode("cp1252")), infer_schema=False)
        frames.append(
            frame.rename({column: column.strip() for column in frame.columns})
            .with_columns(pl.col("Office Name").str.strip_chars(), pl.col("Office District").str.strip_chars())
            .filter(pl.col("Office Name").is_in(["House of Delegates", "State Senator"]))
            .select(
                year=pl.lit(DAY.year, pl.Int32),
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
                + f":{suffix}",
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("MD"),
                district=pl.lit(None, pl.Int16),
                code=pl.lit(fips[re.sub(r"[^a-z]", "", county.lower().replace("st.", "st"))]),
                kind=pl.lit("county"),
                candidate=pl.col("Candidate Name").str.strip_chars().str.to_uppercase(),
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes=pl.col("Total Votes").str.strip_chars().cast(pl.Float64, strict=False).cast(pl.Int64),
                source=pl.lit("MD"),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    print(frame.group_by(pl.col("chamber").str.extract(r":(\w+)$").alias("party")).agg(pl.col("chamber").n_unique()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 2014 House of Delegates and State Senate primaries by county from the State Board of "
            "Elections' results archive",
            polity="US-MD",
        )
        source = cs.source(md_sbe.PUBLISHER, md_sbe.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {md_sbe.TITLE}")
        for url in urls():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"MD": source},
            strict=True,
            authoritative="Source: the State Board of Elections' official county results.",
        )
        return cs.write()
