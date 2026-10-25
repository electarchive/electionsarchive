import re
from datetime import date

import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "us.electionsfl"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
REPORTS = "https://enr.electionsfl.org/{county}/{election}/Reports"
EXPORT = re.compile(
    r"https://s3\.amazonaws\.com/results\.voterfocus\.com/enr/exports/reports/{county}/{election}/"
    r"(?:PrecinctResults|CandidateResultsbyPrecinct)[^\"]+\.csv"
)
COUNTIES = {
    "DUV": (
        "12031",
        "Duval County Supervisor of Elections",
        {
            1229: date(2014, 12, 16),
            1245: date(2015, 2, 17),
            1251: date(2015, 3, 24),
            1276: date(2015, 5, 19),
            2085: date(2019, 3, 19),
            2125: date(2019, 5, 14),
            3089: date(2021, 12, 7),
            3124: date(2022, 2, 22),
            3353: date(2023, 3, 21),
            3385: date(2023, 5, 16),
        },
    ),
}
COLUMNS = {
    "Contest": "contest",
    "Candidate_Issue": "candidate",
    "Candidate Issue": "candidate",
    "Party": "party",
    "PrecinctCode": "precinct",
    "Precinct Name": "precinct",
    "TotalVotes": "votes",
    "Total Votes": "votes",
}


def export(county: str, election: int) -> str:
    page = raw.cached(SOURCE, REPORTS.format(county=county, election=election)).path.read_text(errors="replace")
    found = re.search(EXPORT.pattern.format(county=county, election=election), page)
    if found is None:
        raise ValueError(f"{county} {election}: no precinct results export on the Reports page")
    return found[0]


def fetch() -> None:
    for county, (_, _, elections) in COUNTIES.items():
        for election in elections:
            raw.fetch(SOURCE, REPORTS.format(county=county, election=election), suffix=".html")
            raw.fetch(SOURCE, export(county, election), suffix=".csv")


def parse() -> None:
    frames = []
    for county, (fips, _, elections) in COUNTIES.items():
        for election, day in elections.items():
            frame = pl.read_csv(raw.cached(SOURCE, export(county, election)).path, infer_schema=False)
            frame = frame.select(
                [pl.col(column).alias(name) for column, name in COLUMNS.items() if column in frame.columns]
            )
            frames.append(
                frame.with_columns(
                    pl.col("contest").str.strip_chars().str.replace_all(r"\s+", " "),
                    pl.col("candidate").str.strip_chars(),
                    pl.col("precinct").str.strip_chars(),
                    pl.col("votes").cast(pl.Int64),
                    county=pl.lit(county),
                    fips=pl.lit(fips),
                    election=pl.lit(election),
                    day=pl.lit(day),
                ).unique(maintain_order=True)
            )
    frame = pl.concat(frames)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("county", "day").agg(pl.col("contest").n_unique().alias("contests"), pl.len()).sort("day"))
