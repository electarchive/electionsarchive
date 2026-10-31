import re
import unicodedata
from datetime import timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, nc_statewide, ncsbe
from ea.store import DATA, connect

SOURCE = "us.ncsbe_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = ncsbe.DAYS
COUNTYWIDE = r"ABSENTEE|PROVISIONAL|ONE.?STOP|CURBSIDE|TRANSFER|MAIL"
STATEWIDE = {**nc_statewide.OFFICES, "GOVERNOR": "Governor"}


def fetch() -> None:
    ncsbe.fetch()


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    frames = []
    for day in DAYS:
        frame = ncsbe.read(day)
        name = (
            pl.col("contest").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase().str.strip_prefix("NC ")
        )
        parts = name.str.extract_groups(ncsbe.CONTEST.pattern)
        seat = name.str.extract_groups(ncsbe.STATE)
        title = name.replace_strict(STATEWIDE, default=None)
        precinct = pl.col("precinct").str.strip_chars()
        if "precinct_abbrv" in frame.columns:
            precinct = pl.concat_str(
                pl.col("precinct_abbrv").str.strip_chars(), precinct, separator=" "
            ).str.strip_chars()
        frames.append(
            frame.with_columns(parts=parts, seat=seat, title=title, name=name, place=precinct)
            .filter(
                pl.col("parts").struct.field("1").is_not_null()
                | pl.col("seat").struct.field("1").is_not_null()
                | pl.col("title").is_not_null()
                | pl.col("name").str.starts_with("PRESIDENT")
            )
            .select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(pl.col("seat").struct.field("1").is_not_null())
                .then(
                    pl.concat_str(
                        pl.col("seat").struct.field("1").str.to_lowercase(),
                        pl.col("seat").struct.field("2").cast(pl.Int16).cast(pl.String),
                        pl.lit("g"),
                        separator=":",
                    )
                )
                .when(pl.col("parts").struct.field("1") == "SENATE")
                .then(pl.lit("senate"))
                .when(pl.col("parts").struct.field("1").is_not_null())
                .then(pl.lit("house"))
                .when(pl.col("title").is_not_null())
                .then(pl.col("title").str.to_lowercase().str.replace_all(" ", "-"))
                .otherwise(pl.lit("president")),
                day=pl.lit(day),
                special=pl.col("contest").str.contains("UNEXPIRED|SPECIAL"),
                state=pl.lit("NC"),
                district=pl.coalesce(pl.col("parts").struct.field("3"), pl.col("parts").struct.field("4")).cast(
                    pl.Int16
                ),
                county=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                countyname=pl.col("county").str.strip_chars().str.to_uppercase(),
                place="place",
                candidate=pl.when(pl.col("name").str.starts_with("PRESIDENT"))
                .then(
                    pl.col("choice")
                    .str.to_uppercase()
                    .str.replace(r"/.*$", "")
                    .str.replace(r"-[A-Z]\. .*$", "")
                    .str.strip_chars()
                )
                .otherwise(pl.col("choice").str.to_uppercase().str.strip_chars()),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    lost = frame.filter(pl.col("county").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    frame = (
        frame.with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            kind=pl.when(pl.col("place").str.to_uppercase().str.contains(COUNTYWIDE))
            .then(pl.lit("county"))
            .otherwise(pl.lit("precinct")),
        )
        .with_columns(
            candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"),
            code=pl.when(pl.col("kind") == "county")
            .then("county")
            .otherwise(
                pl.concat_str(
                    pl.col("day").cast(pl.String), pl.lit("NC"), "county", "countyname", "place", separator=":"
                )
            ),
        )
        .filter(pl.col("measure") > 0)
        .group_by(
            "year", "chamber", "day", "special", "state", "district", "kind", "code", "county", "candidate", "measure"
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.lit("NC"))
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(
        frame.filter(pl.col("kind") == "precinct").group_by("year").agg(pl.col("code").n_unique()).sort("year").rows()
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's presidential, federal, statewide, and state legislative general elections by precinct, 2000–2014",
            polity="US-NC",
        )
        source = cs.source(ncsbe.PUBLISHER, ncsbe.TITLE)
        if source in cs.minted:
            raise ValueError("no North Carolina State Board of Elections source")
        for day in DAYS:
            cs.artifact(raw.cached(ncsbe.SOURCE, ncsbe.FILES.format(day=day)), source)
        cs.scope_units("us.precinct", "____-__-__:NC:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '37%'"
            ).fetchall()
        }
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                     and coalesce(r.completeness, 1) >= 0.98
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   where x.scheme = 'us.contest' and x.value ~ '^20(0[0-9]|1[0-4])-..-..:NC:'""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.with_columns(
            key=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("NC"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            )
        )
        frame = frame.filter(~pl.col("key").is_in(list(filled))).drop("key")
        units = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("county").first(), pl.col("day").min())
            .sort("code")
        )
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("county").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        medsl.attach(cs, conn, frame.drop("county"), {"NC": source}, grain="precinct")
        return cs.write()
