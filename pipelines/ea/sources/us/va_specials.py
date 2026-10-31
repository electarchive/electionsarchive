from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl
from ea.sources.us.va_pres_primaries import PUBLISHER, SITE
from ea.store import DATA, connect

SOURCE = "us.va_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TENANT = "va2.elstats"
ENTRIES = {(2007, "Special Election", 1): True, (2014, "General Election", 7): False}
TITLE = "Election statistics archive: congressional special and runoff elections"


def fetch() -> None:
    for year in {entry[0] for entry in ENTRIES}:
        raw.fetch(SOURCE, elstats.url(TENANT, year), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties = {
            elstats.plain(text) + ("city" if int(code[2:]) >= 500 else ""): code
            for code, text in conn.execute(
                "select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short' "
                "where u.scheme_code = 'us.county' and u.code like '51%' and u.code <> '51515' order by u.code"
            ).fetchall()
        } | {name: code for (state, name), code in elstats.FORMER.items() if state == "VA"}
    frame = pl.concat(
        pl.read_csv(
            raw.cached(SOURCE, elstats.url(TENANT, year)).path, infer_schema=False, truncate_ragged_lines=True
        ).filter((pl.col("election_type") == kind) & (pl.col("district_name") == str(district)))
        for year, kind, district in ENTRIES
    )
    division = (
        pl.col("division_name")
        .str.replace(r"\s*\(.*\)$", "")
        .str.to_lowercase()
        .str.replace_all("saint ", "st ")
        .str.replace_all("&", "and")
        .str.replace_all(r"\bcounty\b", "")
        .str.replace_all(r"[^a-z]", "")
    )
    frame = (
        frame.filter((pl.col("office_name") == "U.S. House") & (pl.col("division_type") == "Locality"))
        .with_columns(
            special=(pl.col("election_type") == "Special Election")
            | ~pl.col("candidate_name").fill_null("").str.contains("Carr").any().over("contest_id"),
            code=division.replace_strict(counties, default=None),
            votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            candidate=pl.col("candidate_name")
            .fill_null("")
            .str.replace_all(r"\s+", " ")
            .str.strip_chars()
            .str.to_uppercase(),
        )
        .filter(pl.col("votes").is_not_null(), ~pl.col("candidate").str.contains("TOTAL VOTES|UNDERVOTE"))
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched localities: {lost['division_name'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
        chamber=pl.lit("house"),
        day=pl.col("election_date").str.slice(0, 10).str.to_date(),
        special=pl.col("special"),
        state=pl.lit("VA"),
        district=pl.col("district_name").cast(pl.Int16, strict=False),
        code="code",
        candidate=pl.when(pl.col("candidate").str.contains("WRITE")).then(pl.lit("WRITE-INS")).otherwise("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure").agg(
        pl.col("writein").any(), pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("VA")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia's December 11, 2007 First District special election and November 4, 2014 Seventh District general election by locality from the Department of Elections",
            polity="US-VA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        for year in {entry[0] for entry in ENTRIES}:
            cs.artifact(raw.cached(SOURCE, elstats.url(TENANT, year)), source)
        medsl.attach(cs, conn, frame, {"VA": source}, strict=True)
        return cs.write()
