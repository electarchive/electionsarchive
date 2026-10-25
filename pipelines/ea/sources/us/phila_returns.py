import re
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
    date(2011, 2, 1): "election-results/2011_Special_WD.csv",
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
LONGX = [
    (
        date(2014, 11, 4),
        "election-results/2014_General_Election_-_Complete_Precinct-Level_Results.xls",
        None,
        {"PRECINCT": "precinct", "CATEGORY": "contest", "CANDIDATE": "candidate", "VOTE COUNT": "votes"},
    ),
    (
        date(2014, 5, 20),
        "election-results/2014_Primary_Election_-_Full_Results_by_Precinct.xlsx",
        "Sheet1",
        {"PRECINCT": "precinct", "CATEGORY": "contest", "PARTY": "party", "VOTE COUNT": "votes"},
    ),
    (date(2015, 3, 24), "election-results/2015_Special_Election_-_Results_by_Precinct.txt", None, "txt"),
    (
        date(2015, 8, 11),
        "election-results/2015-08_SPECIAL/2015_AUGUST_SPECIAL_ELECTION_-_FINAL_PRECINCT_RESULTS.xlsx",
        "Precinct-level Data",
        {"PRECINCT": "precinct", "OFFICE": "contest", "SELECTION": "candidate", "PARTY": "party", "VOTES": "votes"},
    ),
    (
        date(2016, 3, 15),
        "election-results/2016-03_SPECIAL/2016_March_Special_Election.xlsx",
        "Precinct-level data",
        {
            "WARD": "ward",
            "DIVISION": "division",
            "OFFICE": "contest",
            "CANDIDATE": "candidate",
            "PARTY": "party",
            "VOTES": "votes",
        },
    ),
    (
        date(2016, 4, 26),
        "election-results/2016_PRIMARY/2016_PRIMARY_-_RESULTS_-_PUBLIC.xlsx",
        "Precinct Results",
        {
            "WARD": "ward",
            "DIVISION": "division",
            "OFFICE": "contest",
            "NAME": "candidate",
            "PARTY": "party",
            "VOTES": "votes",
        },
    ),
    (
        date(2016, 11, 8),
        "election-results/2016_GENERAL/2016_GENERAL_-_RESULTS_-_RECANVASS.xlsx",
        "PRECINCT DATA",
        {
            "WARD": "ward",
            "DIVISION": "division",
            "OFFICE": "contest",
            "CANDIDATE": "candidate",
            "PARTY": "party",
            "VOTES": "votes",
        },
    ),
    (
        date(2017, 3, 21),
        "election-results/2017-03_SPECIAL/2017_MARCH_SPECIAL.xlsx",
        "Precinct-level Data",
        {
            "WARD": "ward",
            "DIVISION": "division",
            "OFFICE": "contest",
            "CANDIDATE": "candidate",
            "PARTY": "party",
            "VOTES": "votes",
        },
    ),
    (
        date(2018, 5, 15),
        "election-results/2018_PRIMARY/2018_PRIMARY_-_CITY_WARD_PRECINCT_OFFICIAL_RESULTS.xlsx",
        "Precinct-level Detail",
        {
            "WARD": "ward",
            "DIVISION": "division",
            "OFFICE": "contest",
            "SELECTION": "candidate",
            "PARTY": "party",
            "VOTES": "votes",
        },
    ),
    (
        date(2019, 3, 12),
        "raw-data/2019_special_(march).csv",
        None,
        {
            "WARD": "ward",
            "DIV": "division",
            "OFFICE": "contest",
            "CANDIDATE": "candidate",
            "PARTY": "party",
            "VOTES": "votes",
        },
    ),
    (
        date(2019, 5, 21),
        "election-results/2019_PRIMARY/2019_PRIMARY_-_RESULTS_-_UNOFFICIAL_ALL.xlsx",
        "Precinct-level Detail",
        {
            "WARD": "ward",
            "DIVISION": "division",
            "CATEGORY": "contest",
            "SELECTION": "candidate",
            "PARTY": "party",
            "VOTE COUNT": "votes",
        },
    ),
]
GRIDX = [
    (date(2020, 11, 3), "raw-data/2020_general.xlsx", "Total", "DIV"),
    (date(2022, 5, 17), "raw-data/2022_Primary_Results_Major_Office.xlsx", "Totals", "PRECINCT"),
    (date(2022, 11, 8), "raw-data/2022_general.xlsx", "Totals", "Division"),
    (date(2023, 5, 16), "media/2023_Primary_Results.xlsx", "Totals", "PRECINCT NAME"),
    (date(2023, 11, 7), "media/2023_General_Results.xlsx", "Totals", "PRECINCT NAME"),
    (date(2024, 2, 13), "media/2024_Special_Results.xlsx", "Totals", "PRECINCT NAME"),
    (date(2024, 4, 23), "media/24_Primary_Totals.xlsx", "Totals", "PRECINCT NAME"),
    (date(2024, 11, 5), "media/24_General_Totals.xlsx", "Totals", "Precinct Name"),
    (date(2025, 5, 20), "media/25_Primary_Totals.xlsx", "Totals", "PRECINCT NAME"),
    (date(2025, 11, 4), "media/25_General_Totals.xlsx", "Totals", "Precinct Name"),
    (date(2026, 5, 19), "media/2026_Primary_Results_Major_Office.xlsx", "Totals", "PRECINCT NAME"),
]
BASES = {"media/": "https://vote.phila.gov/"}
META = re.compile(r"(?i)^(public\s*count|registration|turnout|total\s|dem\s|rep\s|non\s|state\s*$|st\.?\s*$|\d{4}\s*$)")
SUMMARIES = [
    (
        date(2015, 5, 19),
        "election-results/2015_PRIMARY/2015_PRIMARY_-_WARD_RESULTS_-_PUBLIC.xlsx",
        "Democratic Primary",
    ),
    (
        date(2015, 5, 19),
        "election-results/2015_PRIMARY/2015_PRIMARY_-_WARD_RESULTS_-_PUBLIC.xlsx",
        "Republican Primary",
    ),
    (date(2015, 5, 19), "election-results/2015_PRIMARY/2015_PRIMARY_-_WARD_RESULTS_-_PUBLIC.xlsx", "Questions"),
    (date(2015, 5, 19), "election-results/2015_PRIMARY/2015_PRIMARY_-_WARD_RESULTS_-_PUBLIC.xlsx", "Special Election"),
    (
        date(2015, 11, 3),
        "election-results/2015_GENERAL/2015_GENERAL_-_WARD_RESULTS_-_PUBLIC.xlsx",
        "General Election Results",
    ),
    (date(2015, 11, 3), "election-results/2015_GENERAL/2015_GENERAL_-_WARD_RESULTS_-_PUBLIC.xlsx", "Retention Results"),
    (
        date(2015, 11, 3),
        "election-results/2015_GENERAL/2015_GENERAL_-_WARD_RESULTS_-_PUBLIC.xlsx",
        "Ballot Question Results",
    ),
    (
        date(2015, 8, 11),
        "election-results/2015-08_SPECIAL/2015_AUGUST_SPECIAL_ELECTION_-_FINAL_PRECINCT_RESULTS.xlsx",
        "Ward-level Results",
    ),
    (
        date(2016, 4, 26),
        "election-results/2016_PRIMARY/2016_PRIMARY_-_RESULTS_-_PUBLIC.xlsx",
        "City-Wide Election Results",
    ),
    (
        date(2016, 4, 26),
        "election-results/2016_PRIMARY/2016_PRIMARY_-_RESULTS_-_PUBLIC.xlsx",
        "City-Wide Questions Results",
    ),
    (date(2016, 11, 8), "election-results/2016_GENERAL/2016_GENERAL_-_RESULTS_-_RECANVASS.xlsx", "City-Wide Results"),
    (date(2017, 3, 21), "election-results/2017-03_SPECIAL/2017_MARCH_SPECIAL.xlsx", "Ward-level Summary"),
    (date(2017, 5, 16), "election-results/2017_PRIMARY/2017_PRIMARY_-_RESULTS_.xlsx", "City-wide Summary"),
    (date(2017, 11, 7), "election-results/2017_GENERAL/2017_GENERAL_-_MAJOR_OFFICES.xlsx", "City-wide Summary"),
    (
        date(2018, 5, 15),
        "election-results/2018_PRIMARY/2018_PRIMARY_-_CITY_WARD_PRECINCT_OFFICIAL_RESULTS.xlsx",
        "City-wide Summary",
    ),
    (
        date(2019, 5, 21),
        "election-results/2019_PRIMARY/2019_PRIMARY_-_RESULTS_-_UNOFFICIAL_ALL.xlsx",
        "City-Wide Summary",
    ),
]
PARTY_CODES = {"DEM", "REP", "LIB", "GRN", "KEY", "CST", "LBR", "IND", "NPA"}
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


def url_of(name: str) -> str:
    return BASES.get(name[:6], BASE) + name


def long_files() -> list[pl.DataFrame]:
    frames = []
    for day, name, sheet, columns in LONGX:
        path = raw.cached(SOURCE, url_of(name)).path
        if columns == "txt":
            frame = pl.read_csv(
                path, separator="\t", has_header=False, infer_schema=False, quote_char='"', encoding="utf8-lossy"
            )
            frame = frame.rename(
                dict(
                    zip(
                        frame.columns,
                        ["precinct", "contest", "party", "last", "first", "middle", "x", "votes"],
                        strict=True,
                    )
                )
            )
            frame = frame.with_columns(
                candidate=pl.concat_str(["first", "middle", "last"], separator=" ", ignore_nulls=True)
            )
        elif path.suffix == ".csv":
            frame = pl.read_csv(path, infer_schema=False, encoding="utf8-lossy").rename(columns)
        else:
            frame = fastexcel.read_excel(path).load_sheet(sheet or 0, dtypes="string").to_polars().rename(columns)
        if "precinct" not in frame.columns:
            frame = frame.with_columns(
                precinct=pl.col("ward").str.strip_chars().str.zfill(2)
                + pl.col("division").str.strip_chars().str.zfill(2)
            )
        if "FIRST NAME" in frame.columns:
            frame = frame.with_columns(
                candidate=pl.concat_str(["FIRST NAME", "MIDDLE NAME", "LAST NAME"], separator=" ", ignore_nulls=True)
            )
        frame = frame.with_columns(
            precinct=pl.col("precinct").str.replace("-", ""),
            party=pl.col("party") if "party" in frame.columns else pl.lit(None, pl.String),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Float64, strict=False).cast(pl.Int64),
        )
        frames.append(
            frame.filter(
                pl.col("contest").str.len_chars().gt(0)
                & pl.col("votes").is_not_null()
                & pl.col("candidate").str.len_chars().gt(0)
            )
            .group_by("precinct", "contest", "candidate", "party")
            .agg(pl.col("votes").sum())
            .with_columns(
                day=pl.lit(day),
                url=pl.lit(url_of(name)),
                ballot=pl.col("contest").str.extract(r"-(D|DEM|R|REP)$", 1),
                contest=pl.col("contest")
                .str.replace(r"-(?:D|DEM|R|REP)$", "")
                .str.replace_all(r"\s+", " ")
                .str.strip_chars(),
            )
        )
    return frames


def grid_rows() -> tuple[list[pl.DataFrame], pl.DataFrame]:
    frames = []
    printed = []
    for day, name, sheet, key in GRIDX:
        frame = fastexcel.read_excel(raw.cached(SOURCE, url_of(name)).path).load_sheet(sheet).to_polars()
        cells = []
        for column in frame.columns:
            if "\n" not in column and " - " not in column or META.match(column.strip()):
                continue
            head, _, tail = column.rpartition("\n")
            if not head.strip() or META.match(head.strip()):
                continue
            contest = re.sub(r"\s+", " ", head).strip().removesuffix("-").strip()
            tail = tail.strip()
            ballot = party = None
            found = re.match(r"^(DEM|REP) - (.+)$", tail)
            if found is not None:
                ballot, candidate = found[1], found[2]
            else:
                found = re.match(r"^(.+) (" + "|".join(PARTY_CODES) + ")$", tail)
                candidate, party = (found[1], found[2]) if found is not None else (tail, None)
            cells.append((column, contest, re.sub(r"\s+", " ", candidate).strip(), party, ballot))
        names = frame[key].cast(pl.String).str.strip_chars()
        divisions = names.str.contains(r"^\d\d-\d\d$")
        total = names.str.contains(r"(?i)^(county )?totals?$")
        long = frame.select(names.alias("precinct"), *(pl.col(c) for c, *_ in cells)).with_columns(
            division=divisions, total=total
        )
        meta = pl.DataFrame(
            cells,
            schema={name: pl.String for name in ("column", "contest", "candidate", "party", "ballot")},
            orient="row",
        ).with_columns(day=pl.lit(day), url=pl.lit(url_of(name)))
        votes = long.unpivot(
            [c for c, *_ in cells], index=["precinct", "division", "total"], variable_name="column", value_name="votes"
        )
        votes = votes.with_columns(votes=pl.col("votes").cast(pl.Float64, strict=False)).drop_nulls("votes")
        votes = votes.join(meta, on="column")
        others = (
            votes.filter(~pl.col("total") & ~pl.col("division"))
            .group_by("day", "contest", "ballot", "candidate")
            .agg(pl.col("votes").sum().cast(pl.Int64).alias("other"))
        )
        printed.append(
            votes.filter(pl.col("total"))
            .select("day", "contest", "ballot", "candidate", pl.col("votes").cast(pl.Int64).alias("printed"))
            .join(others, on=["day", "contest", "ballot", "candidate"], how="left", nulls_equal=True)
            .with_columns(pl.col("other").fill_null(0))
        )
        frames.append(
            votes.filter(pl.col("division") & ~pl.col("total"))
            .with_columns(precinct=pl.col("precinct").str.replace("-", ""), votes=pl.col("votes").cast(pl.Int64))
            .select("precinct", "contest", "candidate", "party", "votes", "day", "url", "ballot")
        )
    return frames, pl.concat(printed)


def summary_totals() -> pl.DataFrame:
    frames = []
    for day, name, sheet in SUMMARIES:
        frame = (
            fastexcel.read_excel(raw.cached(SOURCE, url_of(name)).path).load_sheet(sheet, header_row=None).to_polars()
        )
        label, total = frame.columns[0], frame.columns[-1]
        frames.append(
            frame.select(
                pl.lit(day).alias("day"),
                pl.col(label).cast(pl.String).str.replace_all(r"\s+", " ").str.strip_chars().alias("candidate"),
                pl.col(total).cast(pl.Float64, strict=False).cast(pl.Int64).alias("printed"),
            ).drop_nulls()
        )
    return pl.concat(frames)


def fetch() -> None:
    names = [*WIDE.values(), *LONG.values(), *(n for _, n, _ in SHEETS), *(n for n, _, _ in GRID.values())]
    names += [n for _, n, _, _ in LONGX] + [n for _, n, _, _ in GRIDX]
    names += [n for _, n, _ in SUMMARIES if n not in names]
    for name in names:
        raw.fetch(SOURCE, url_of(name), suffix=Path(name).suffix)


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
        .filter(pl.col("votes").is_not_null() & ~pl.col("candidate").str.contains(r"(?i)^(?:over|under)votes?$|^total"))
        .select("day", "url", "contest", "precinct", "candidate", "votes", "party", "ballot")
        .with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64))
    )
    grids, printed = grid_rows()
    extra = (
        pl.concat([*sheet_rows(), *long_files(), *grids], how="diagonal_relaxed")
        .with_columns(candidate=pl.col("candidate").cast(pl.String).str.replace_all(r"\s+", " ").str.strip_chars())
        .filter(
            pl.col("votes").is_not_null()
            & ~pl.col("candidate").str.contains(r"(?i)^(?:over|under)votes?$|^total|^no vote")
        )
        .select("day", "url", "contest", "precinct", "candidate", "votes", "party", "ballot")
        .with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64))
    )
    rows = pl.concat([rows, extra.select(rows.columns)])
    PARSED.mkdir(parents=True, exist_ok=True)
    rows.write_parquet(PARSED / "rows.parquet")
    printed.write_parquet(PARSED / "printed.parquet")
    summary_totals().write_parquet(PARSED / "summaries.parquet")
    print(rows.group_by("day").agg(pl.col("contest").n_unique(), pl.col("precinct").n_unique()).sort("day"))


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Philadelphia's contests by ward and division from the City Commissioners' results files",
        rows=pl.read_parquet(PARSED / "rows.parquet")
        .filter(~pl.col("candidate").str.contains(r"(?i)write.?in"))
        .with_columns(contest=pl.concat_str(["contest", pl.col("ballot").fill_null("")], separator="|")),
        fips="42101",
        place="PHILADELPHIA",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
        state_code="PA",
        names_only=True,
    )
