from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.phila_returns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Philadelphia City Commissioners"
TITLE = "Election results by ward and division"
LISTING = "https://vote.phila.gov/resources-data/past-election-results/archived-data-sets/"
BASE = "https://vote.phila.gov/files/"
METHOD = (
    "Division totals from the Philadelphia City Commissioners' results file for the election, matched to the "
    "contest by candidate and checked against its official totals."
)
WIDE = {
    date(2007, 5, 15): "election-results/2007_Primary_WD.csv",
    date(2007, 11, 6): "election-results/2007_General_WD.csv",
    date(2008, 4, 22): "election-results/2008_Primary_WD.csv",
    date(2008, 11, 4): "election-results/2008_General_WD.csv",
    date(2009, 5, 19): "election-results/2009_Primary_WD.csv",
    date(2009, 11, 3): "election-results/2009_General_WD.csv",
    date(2010, 5, 18): "election-results/2010_Primary_WD.csv",
    date(2010, 11, 2): "election-results/2010_General_WD.csv",
    date(2011, 5, 17): "election-results/2011_Primary_WD.csv",
    date(2011, 11, 8): "election-results/2011_General_WD.csv",
    date(2012, 4, 24): "election-results/2012_Primary_WD.csv",
    date(2012, 11, 6): "election-results/2012_General_WD.csv",
    date(2013, 5, 21): "election-results/2013_Primary_WD.csv",
}
LONG = {
    date(2018, 11, 6): "raw-data/2018_general.csv",
    date(2019, 11, 5): "raw-data/2019_general.csv",
    date(2020, 6, 2): "raw-data/2020_primary.csv",
}
SHEETS = [
    (date(2013, 11, 5), "election-results/2013_General_-_Results_for_Website_1.xlsx", "Top Ticket"),
    (
        date(2015, 5, 19),
        "election-results/2015_PRIMARY/2015_PRIMARY_-_WARD_RESULTS_-_PUBLIC.xlsx",
        "Precinct-level Data",
    ),
    (
        date(2015, 11, 3),
        "election-results/2015_GENERAL/2015_GENERAL_-_WARD_RESULTS_-_PUBLIC.xlsx",
        "Precint-level Data",
    ),
    (date(2017, 5, 16), "election-results/2017_PRIMARY/2017_PRIMARY_-_RESULTS_.xlsx", "Precinct-level Data"),
    (date(2017, 11, 7), "election-results/2017_GENERAL/2017_GENERAL_-_MAJOR_OFFICES.xlsx", "Precinct-level Detail"),
]
GRID = {
    date(2021, 11, 2): ("raw-data/2021_general.xlsx", "Totals", "PRECINCT NAME"),
    date(2021, 5, 18): ("raw-data/2021_primary.xlsx", "Totals", "Division"),
}
NAMES = {"CATEGORY": "contest", "RACE": "contest", "OFFICE": "contest", "SELECTION": "candidate"}
NAMES |= {"CANDIDATE": "candidate", "NAME": "candidate", "VOTES": "votes", "VOTE COUNT": "votes", "TOTAL": "votes"}


def sheet_rows() -> list[pl.DataFrame]:
    frames = []
    for day, name, sheet in SHEETS:
        frame = fastexcel.read_excel(raw.cached(SOURCE, BASE + name).path).load_sheet(sheet).to_polars()
        frame = frame.rename({column: column.upper() for column in frame.columns})
        frame = frame.rename({column: NAMES[column] for column in frame.columns if column in NAMES})
        if "WARD" in frame.columns and "DIVISION" in frame.columns:
            frame = frame.with_columns(
                precinct=pl.col("WARD").cast(pl.Float64, strict=False).cast(pl.Int64).cast(pl.String).str.zfill(2)
                + pl.col("DIVISION").cast(pl.Float64, strict=False).cast(pl.Int64).cast(pl.String).str.zfill(2)
            )
        else:
            frame = frame.with_columns(precinct=pl.col("PRECINCT").str.replace("-", ""))
        frames.append(
            frame.filter(pl.col("contest").is_not_null() & pl.col("votes").is_not_null())
            .group_by("precinct", "contest", "candidate", "PARTY")
            .agg(pl.col("votes").cast(pl.Float64).sum().cast(pl.Int64))
            .rename({"PARTY": "party"})
            .with_columns(
                day=pl.lit(day),
                url=pl.lit(BASE + name),
                ballot=pl.col("contest").str.extract(r"-(D|DEM|R|REP)$", 1),
                contest=pl.col("contest").str.replace(r"-(?:D|DEM|R|REP)$", "").str.replace_all(r"\s+", " "),
            )
        )
    for day, (name, sheet, key) in GRID.items():
        frame = fastexcel.read_excel(raw.cached(SOURCE, BASE + name).path).load_sheet(sheet).to_polars()
        meta = {key, "Ward", "Registration", "Turnout"}
        cells = [column for column in frame.columns if column.replace("\n", " ") != column or "-" in column]
        cells = [
            c for c in cells if c not in meta and not c.startswith(("Total", "Dem\n", "Rep\n", "Non\n", "State\n"))
        ]
        long = frame.select(pl.col(key).alias("precinct"), *cells).unpivot(
            cells, index="precinct", variable_name="column", value_name="votes"
        )
        long = long.with_columns(
            column=pl.col("column").str.replace_all(r"\s+", " "), votes=pl.col("votes").cast(pl.Float64).cast(pl.Int64)
        )
        if day.month == 5:
            parts = pl.col("column").str.extract_groups(r"^(?<contest>.+?) (?<ballot>DEM|REP) (?<candidate>.+)$")
        else:
            parts = pl.col("column").str.extract_groups(
                r"^(?<contest>[^-]+?) ?- ?(?<candidate>.+?)(?: (?<party>DEM|REP|LIB|GRN))?$"
            )
        long = (
            long.with_columns(parts.alias("p"))
            .unnest("p")
            .filter(pl.col("contest").is_not_null() & pl.col("votes").is_not_null())
        )
        frames.append(
            long.with_columns(
                day=pl.lit(day),
                url=pl.lit(BASE + name),
                precinct=pl.col("precinct").str.replace("-", ""),
                party=pl.col("party") if "party" in long.columns else pl.lit(None, pl.String),
                ballot=pl.col("ballot") if "ballot" in long.columns else pl.lit(None, pl.String),
            ).select("precinct", "contest", "candidate", "party", "votes", "day", "url", "ballot")
        )
    return frames


def fetch() -> None:
    for name in [*WIDE.values(), *LONG.values(), *(n for _, n, _ in SHEETS), *(n for n, _, _ in GRID.values())]:
        raw.fetch(SOURCE, BASE + name, suffix=Path(name).suffix)


def parse() -> None:
    frames = []
    for day, name in WIDE.items():
        url = BASE + name
        wide = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, encoding="utf8-lossy")
        wide = wide.rename({wide.columns[0]: "WARD"})
        frames.append(
            wide.unpivot(
                [column for column in wide.columns if column not in ("WARD", "DIVISION")],
                index=["WARD", "DIVISION"],
                variable_name="column",
                value_name="votes",
            ).with_columns(day=pl.lit(day), url=pl.lit(url))
        )
    wide_rows = (
        pl.concat(frames)
        .filter(pl.col("votes").is_not_null())
        .with_columns(
            contest=pl.col("column").str.extract(r"^.*? - (.+)$", 1),
            candidate=pl.col("column").str.extract(r"^(.*?) - .+$", 1),
        )
        .filter(pl.col("contest").is_not_null())
        .with_columns(
            party=pl.col("contest").str.extract(r"-(D|DEM|R|REP)$", 1),
            contest=pl.col("contest").str.replace(r"-(?:D|DEM|R|REP)$", ""),
        )
    )
    long_rows = []
    for day, name in LONG.items():
        url = BASE + name
        long_rows.append(
            pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, encoding="utf8-lossy")
            .rename({"OFFICE": "CATEGORY", "CANDIDATE": "SELECTION", "VOTES": "VOTE COUNT"}, strict=False)
            .group_by("WARD", "DIVISION", "CATEGORY", "SELECTION", "PARTY")
            .agg(pl.col("VOTE COUNT").cast(pl.Int64).sum().alias("votes"))
            .rename({"CATEGORY": "contest", "SELECTION": "candidate"})
            .with_columns(day=pl.lit(day), url=pl.lit(url), votes=pl.col("votes").cast(pl.String))
        )
    rows = pl.concat(
        [
            wide_rows.select(
                "day",
                "url",
                "contest",
                "WARD",
                "DIVISION",
                "candidate",
                "votes",
                pl.col("party").alias("ballot"),
                pl.lit(None, pl.String).alias("party"),
            ),
            pl.concat(long_rows).select(
                "day",
                "url",
                "contest",
                "WARD",
                "DIVISION",
                "candidate",
                "votes",
                pl.lit(None, pl.String).alias("ballot"),
                pl.col("PARTY").alias("party"),
            ),
        ]
    )
    rows = (
        rows.with_columns(
            precinct=pl.col("WARD").str.strip_chars().str.zfill(2) + pl.col("DIVISION").str.strip_chars().str.zfill(2),
            candidate=pl.col("candidate").str.replace_all(r"\s+", " ").str.strip_chars(),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
        )
        .filter(
            pl.col("votes").is_not_null()
            & ~pl.col("candidate").str.contains(r"(?i)write.?in|^(?:over|under)votes?$|^total")
        )
        .select("day", "url", "contest", "precinct", "candidate", "votes", "party", "ballot")
        .with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64))
    )
    extra = (
        pl.concat(sheet_rows(), how="diagonal_relaxed")
        .with_columns(candidate=pl.col("candidate").cast(pl.String).str.replace_all(r"\s+", " ").str.strip_chars())
        .filter(
            pl.col("votes").is_not_null()
            & ~pl.col("candidate").str.contains(r"(?i)write.?in|^(?:over|under)votes?$|^total|^no vote")
        )
        .select("day", "url", "contest", "precinct", "candidate", "votes", "party", "ballot")
        .with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64))
    )
    rows = pl.concat([rows, extra.select(rows.columns)])
    PARSED.mkdir(parents=True, exist_ok=True)
    rows.write_parquet(PARSED / "rows.parquet")
    print(rows.group_by("day").agg(pl.col("contest").n_unique(), pl.col("precinct").n_unique()).sort("day"))


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Philadelphia's contests by ward and division from the City Commissioners' results files",
        rows=pl.read_parquet(PARSED / "rows.parquet").with_columns(
            contest=pl.concat_str(["contest", pl.col("ballot").fill_null("")], separator="|")
        ),
        fips="42101",
        place="PHILADELPHIA",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
        state_code="PA",
    )
