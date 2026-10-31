import json
import re
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.sc_elstats_pri"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SEARCH = "https://sc.elstats.civera.com/api/download_search.csv?"
YEARS = [2010, 2012, 2014]
BY_CANDIDATE = {2016: "Sanders", 2020: "Biden"}
OFFICES = {"U.S. House": "house", "U.S. Senate": "senate", "Presidential Preference": "president"}
PUBLISHER = "South Carolina State Election Commission"
TITLE = "South Carolina Elections Database"
SITE = "https://electionhistory.scvotes.gov"


def url(year: int, candidate: str | None = None) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {
            "candidates": [{"free": candidate}] if candidate else [],
            "divisions": [],
            "offices": [] if candidate else [{"free": office} for office in ("U.S. House", "U.S. Senate", "President")],
        },
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH + urlencode({"search": json.dumps(search, separators=(",", ":"))})


WIDE = {"6235": ("2016-02-27", "Democratic")}
FILES = (
    [f"{SITE}/api/download_contest/{contest}_table.csv?split_party=false" for contest in WIDE]
    + [url(year) for year in YEARS]
    + [url(year, candidate) for year, candidate in BY_CANDIDATE.items()]
)


def fetch() -> None:
    for file in FILES:
        try:
            raw.cached(SOURCE, file)
        except LookupError:
            raw.fetch(SOURCE, file, suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    frames = []
    for file in FILES:
        if "download_contest" in file:
            day, party = WIDE[file.split("/")[-1].split("_")[0]]
            table = pl.read_csv(
                raw.cached(SOURCE, file).path, has_header=False, infer_schema=False, truncate_ragged_lines=True
            )
            names = table.row(0)
            frame = pl.DataFrame(
                [
                    {
                        "election_date": day,
                        "election_type": "Primary",
                        "office_name": "Presidential Preference",
                        "primary_party": party,
                        "district_name": "",
                        "division_type": "County",
                        "division_name": row[1],
                        "candidate_name": name,
                        "votes": row[column],
                    }
                    for row in table.iter_rows()
                    if row[0] == "County"
                    for column, name in enumerate(names)
                    if column > 1 and name is not None
                ],
                infer_schema_length=None,
            )
        else:
            frame = pl.read_csv(raw.cached(SOURCE, file).path, infer_schema=False, truncate_ragged_lines=True)
        frames.append(
            frame.filter(
                pl.col("election_type").is_in(["Primary", "Primary Runoff"]),
                pl.col("office_name").is_in(list(OFFICES)),
                pl.col("division_type") == "County",
                ~pl.col("candidate_name").is_in(["Total Votes Cast", "Overvotes/Undervotes", "Total Ballots Cast"]),
            )
        )
    frame = pl.concat(frames, how="diagonal")
    unknown = sorted({name for name in frame["division_name"] if re.sub(r"[^a-z]", "", name.lower()) not in counties})
    if unknown:
        raise ValueError(f"unmatched counties: {unknown}")
    frame = (
        frame.with_columns(
            day=pl.col("election_date").str.slice(0, 10).str.to_date(),
            chamber=pl.col("office_name").replace_strict(OFFICES) + ":" + pl.col("primary_party").str.to_lowercase(),
            district=pl.when(pl.col("office_name") == "U.S. House")
            .then(pl.col("district_name").cast(pl.Int16, strict=False))
            .otherwise(None),
            code=pl.col("division_name").map_elements(
                lambda name: counties[re.sub(r"[^a-z]", "", name.lower())], return_dtype=pl.String
            ),
            candidate=pl.col("candidate_name").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
            writein=pl.col("candidate_name").str.contains("(?i)write"),
            votes=pl.col("votes").cast(pl.Int64),
        )
        .group_by("day", "chamber", "district", "code", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("SC"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("SC"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("day", "chamber", "district").agg(pl.col("votes").sum()).sort("day", "chamber", "district"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's 2012, 2016, and 2020 presidential preference primaries by county",
            polity="US-SC",
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
        for file in FILES:
            cs.artifact(raw.cached(SOURCE, file), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").filter(pl.col("chamber").str.starts_with("president")),
            {"SC": source},
            strict=True,
        )
        return cs.write()
