import os
import re
import unicodedata
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.fl_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "precincts-fl"
PUBLISHER = "Florida Division of Elections"
TITLE = "Precinct-Level Election Results"
LANDING = "https://dos.fl.gov/elections/data-statistics/elections-data/precinct-level-election-results/"
FILES = {
    date(2012, 11, 6): "https://dos.fl.gov/media/697204/precinctlevelelectionresults2012gen.zip",
    date(2014, 11, 4): "https://dos.fl.gov/media/697201/precinctlevelelectionresults2014gen.zip",
}
OFFICES = {
    "PRESIDENT OF THE UNITED STATES": "president",
    "UNITED STATES SENATOR": "senate",
    "GOVERNOR": "governor",
    "ATTORNEY GENERAL": "attorney-general",
    "CHIEF FINANCIAL OFFICER": "chief-financial-officer",
    "COMMISSIONER OF AGRICULTURE": "commissioner-of-agriculture",
}
COLUMNS = [
    "abbrv",
    "county",
    "election",
    "date",
    "name",
    "number",
    "place",
    "registered",
    "a",
    "b",
    "c",
    "office",
    "district",
    "office_code",
    "choice",
    "party",
    "d",
    "candidate_id",
    "votes",
]


def fetch() -> None:
    for url in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / url.rsplit("/", 1)[1]).read_bytes(),
                suffix=".zip",
                retrieved_at=datetime(2026, 10, 24, tzinfo=UTC),
                media_type="application/zip",
            )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '12%%'"""
            ).fetchall()
        }
    frames = []
    for day, url in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            for member in archive.namelist():
                if not member.lower().endswith(".txt") or "PctResults" not in member:
                    continue
                frames.append(
                    pl.read_csv(
                        archive.read(member),
                        separator="\t",
                        has_header=False,
                        new_columns=COLUMNS,
                        infer_schema=False,
                        quote_char=None,
                        encoding="utf8-lossy",
                        truncate_ragged_lines=True,
                    ).with_columns(day=pl.lit(day))
                )
    frame = pl.concat(frames)
    office = pl.col("office").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase()
    district = pl.col("district").str.extract(r"(\d+)", 1).cast(pl.Int16)
    frame = (
        frame.with_columns(
            office=office,
            district=district,
            code=pl.col("county")
            .str.to_lowercase()
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(counties, default=None),
            place=pl.col("place").fill_null("").str.replace_all("\x00", "").str.strip_chars(),
        )
        .with_columns(
            chamber=pl.when(pl.col("office").is_in(["U.S. REPRESENTATIVE", "REPRESENTATIVE IN CONGRESS"]))
            .then(pl.lit("house"))
            .when(pl.col("office") == "STATE SENATOR")
            .then(pl.concat_str(pl.lit("senate:"), pl.col("district").cast(pl.String), pl.lit(":g")))
            .when(pl.col("office") == "STATE REPRESENTATIVE")
            .then(pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String), pl.lit(":g")))
            .when(pl.col("office").str.contains(r"^GOVERNOR AND"))
            .then(pl.lit("governor"))
            .otherwise(pl.col("office").replace_strict(OFFICES, default=None))
        )
        .filter(pl.col("chamber").is_not_null() & pl.col("votes").is_not_null())
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    frame = (
        frame.with_columns(
            candidate=pl.col("choice")
            .str.to_uppercase()
            .str.replace(r" ?/ ?.*$", "")
            .str.strip_chars()
            .replace({"NUMBER OF UNDER VOTES": "UNDERVOTES", "TIMES OVER VOTED": "OVERVOTES"}),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            county_code=pl.col("code"),
            code=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("FL"),
                "code",
                pl.col("county").str.to_uppercase(),
                "place",
                separator=":",
            ),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "district", "code", "county_code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("FL"),
            kind=pl.lit("precinct"),
            source=pl.lit("FL"),
            district=pl.when(pl.col("chamber") == "house").then("district"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("year").agg(pl.col("code").n_unique()).rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet").filter(pl.col("year") == int(os.environ["FL_YEAR"]))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's presidential, federal, statewide, and legislative general elections by precinct, 2012 and 2014",
            polity="US-FL",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for day, url in FILES.items():
            if day.year == int(os.environ["FL_YEAR"]):
                cs.artifact(raw.cached(SOURCE, url), source)
        cs.scope_units("us.precinct", "____-__-__:FL:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '12%%'"
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
                   where x.scheme = 'us.contest' and x.value ~ '^201[24]-..-..:FL:'""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("FL"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            ).is_in(list(filled))
        )
        units = (
            frame.group_by("code")
            .agg(pl.col("state").first(), pl.col("county_code").first(), pl.col("day").min())
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
                parent_id=pl.col("county_code").replace_strict(
                    {code: str(unit) for code, (unit, _) in counties.items()}
                ),
                area_relation=pl.lit("within"),
                area_id=pl.col("county_code").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        medsl.attach(cs, conn, frame.drop("county_code"), {"FL": source}, grain="precinct")
        return cs.write()
