import io
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ncsbe"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{day:%Y_%m_%d}/results_pct_{day:%Y%m%d}.zip"
DAYS = [date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in range(2000, 2015, 2)]
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Election results by precinct"
HEADERLESS = [
    "county",
    "election_dt",
    "precinct_abbrv",
    "precinct",
    "contest_name",
    "name_on_ballot",
    "party_cd",
    "ballot_count",
    "ftp_date",
]
COLUMNS = {
    "county": "county",
    "contest_name": "contest",
    "contest": "contest",
    "contest name": "contest",
    "name_on_ballot": "choice",
    "choice": "choice",
    "ballot_count": "votes",
    "total votes": "votes",
}
CONTEST = re.compile(r"^US (SENATE|HOUSE OF REP\w*\.?|HOUSE|CONGRESS)( DISTRICT (\d+)| \((\d+)\w* DISTRICT\))?")
STATE = r"^(?:NC )?(?:STATE )?(HOUSE|SENATE)(?: OF REPRESENTATIVES)?(?: DISTRICT)? \(?0*(\d+)\)?$"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, FILES.format(day=day), suffix=".zip")


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
        with zipfile.ZipFile(raw.cached(SOURCE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0])
        first = text.split(b"\n", 1)[0]
        headed = first.lower().startswith((b'"county"', b"county"))
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            has_header=headed,
            new_columns=None if headed else HEADERLESS,
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        frame = frame.rename(
            {column: COLUMNS[column.strip().lower()] for column in frame.columns if column.strip().lower() in COLUMNS}
        )
        name = pl.col("contest").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase()
        parts = name.str.extract_groups(CONTEST.pattern)
        seat = name.str.extract_groups(STATE)
        frames.append(
            frame.with_columns(parts=parts, seat=seat)
            .filter(pl.col("parts").struct.field("1").is_not_null() | pl.col("seat").struct.field("1").is_not_null())
            .select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(pl.col("seat").struct.field("1").is_not_null())
                .then(
                    pl.concat_str(
                        pl.col("seat").struct.field("1").str.to_lowercase(),
                        pl.col("seat").struct.field("2"),
                        pl.lit("g"),
                        separator=":",
                    )
                )
                .when(pl.col("parts").struct.field("1") == "SENATE")
                .then(pl.lit("senate"))
                .otherwise(pl.lit("house")),
                day=pl.lit(day),
                special=pl.col("contest").str.contains("UNEXPIRED|SPECIAL"),
                state=pl.lit("NC"),
                district=pl.coalesce(pl.col("parts").struct.field("3"), pl.col("parts").struct.field("4")).cast(
                    pl.Int16
                ),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.col("choice").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("NC"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's House and Senate and state House and Senate general elections by county, 2000–2014",
            polity="US-NC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
            license="Public record",
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, FILES.format(day=day)), source)
        held = {
            value
            for (value,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   join changeset c on c.id = r.changeset_id
                   where r.status = 'active' and c.pipeline <> %s and x.value ~ ':NC:(house|senate):[0-9]+:g$'
                     and coalesce(r.completeness, 1) >= 0.98
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct'))""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.col("state"), "chamber", separator=":").is_in(list(held))
        )
        medsl.attach(
            cs, conn, frame, {"NC": source}, partial="the State Board's precinct file has no absentee or one-stop rows"
        )
        return cs.write()
