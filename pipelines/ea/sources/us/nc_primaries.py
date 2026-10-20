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

SOURCE = "us.nc_primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{day:%Y_%m_%d}/results_pct_{day:%Y%m%d}.zip"
DAYS = [
    date(2002, 9, 10),
    date(2004, 7, 20),
    date(2004, 8, 17),
    date(2006, 5, 2),
    date(2008, 5, 6),
    date(2010, 5, 4),
    date(2010, 6, 22),
    date(2012, 5, 8),
    date(2012, 7, 17),
    date(2014, 5, 6),
    date(2014, 7, 15),
    date(2016, 3, 15),
    date(2016, 6, 7),
    date(2018, 5, 8),
    date(2020, 3, 3),
    date(2020, 6, 23),
    date(2022, 5, 17),
    date(2022, 7, 26),
]
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Election results by precinct: primaries"
PARTIES = {
    "DEM": "democratic",
    "REP": "republican",
    "LIB": "libertarian",
    "GRE": "green",
    "CST": "constitution",
    "UNA": "unaffiliated",
}
COLUMNS = {
    "county": "county",
    "contest_name": "contest",
    "contest": "contest",
    "contest name": "contest",
    "name_on_ballot": "choice",
    "choice": "choice",
    "party_cd": "party",
    "party": "party",
    "choice party": "party",
    "ballot_count": "votes",
    "total votes": "votes",
}
HEADERLESS = {
    9: ["county", "election_dt", "precinct_abbrv", "precinct", "contest", "choice", "party", "votes", "ftp_date"],
    10: ["county", "election_dt", "precinct", "contest", "choice", "party", "a", "b", "c", "votes"],
}
CONTEST = re.compile(
    r"^(US SENATE|US HOUSE OF REPRESENTATIVES|US HOUSE|US CONGRESS|PRESIDENTIAL PREFERENCE)"
    r"(?: DISTRICT (\d+)| \((\d+)\w* DISTRICT\))?"
)


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
        separator = "\t" if b"\t" in first else ","
        width = len(first.decode("latin-1").rstrip("\r\n").split(separator))
        frame = pl.read_csv(
            io.BytesIO(text),
            separator=separator,
            has_header=headed,
            new_columns=None if headed else HEADERLESS[width],
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
            encoding="utf8-lossy",
        )
        frame = frame.rename(
            {column: COLUMNS[column.strip().lower()] for column in frame.columns if column.strip().lower() in COLUMNS}
        )
        frame = frame.unique(maintain_order=True)
        contest = pl.col("contest").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase()
        parts = contest.str.extract_groups(CONTEST.pattern)
        frames.append(
            frame.with_columns(parts=parts, name=contest)
            .filter(
                pl.col("parts").struct.field("1").is_not_null(),
                ~pl.col("name").str.contains("UNEXPIRED|SPECIAL"),
                pl.col("party").str.strip_chars().is_in(list(PARTIES)),
            )
            .select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(pl.col("parts").struct.field("1") == "US SENATE")
                .then(pl.lit("senate"))
                .when(pl.col("parts").struct.field("1") == "PRESIDENTIAL PREFERENCE")
                .then(pl.lit("president"))
                .otherwise(pl.lit("house"))
                + pl.lit(":")
                + pl.col("party").str.strip_chars().replace_strict(PARTIES),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("NC"),
                district=pl.coalesce(pl.col("parts").struct.field("2"), pl.col("parts").struct.field("3")).cast(
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
            summary="North Carolina's House, Senate, and presidential primaries and second primaries by county, 2002–2022",
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
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[01][0-9]-[0-9]{2}-[0-9]{2}:NC:(house:[0-9]+|senate|president):[a-z-]+$'
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
                    pl.lit("NC"),
                    pl.when(pl.col("district").is_not_null())
                    .then(
                        pl.concat_str(
                            pl.lit("house:"),
                            pl.col("district").cast(pl.String),
                            pl.lit(":"),
                            pl.col("chamber").str.split(":").list.last(),
                        )
                    )
                    .otherwise(pl.col("chamber")),
                    separator=":",
                ).is_in(list(held))
            ),
            {"NC": source},
            strict=True,
        )
        return cs.write()
