import io
import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_statewide_gaps"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {"AR": date(2022, 11, 8), "IA": date(2022, 11, 8), "KY": date(2019, 11, 5), "ND": date(2018, 11, 6)}
OFFICES = {
    "AR": {
        "Lieutenant Governor": "Lieutenant Governor",
        "Attorney General": "Attorney General",
        "Secretary of State": "Secretary of State",
        "State Treasurer": "Treasurer",
        "Auditor of State": "Auditor",
        "Commissioner of State Lands": "Commissioner of State Lands",
    },
    "IA": {
        "Auditor of State": "Auditor",
        "Treasurer of State": "Treasurer",
        "Secretary of Agriculture": "Secretary of Agriculture",
    },
    "KY": {
        "Secretary of State": "Secretary of State",
        "Attorney General": "Attorney General",
        "Auditor of Public Accounts": "Auditor",
        "State Treasurer": "Treasurer",
        "Commissioner of Agriculture": "Commissioner of Agriculture",
    },
    "ND": {"Tax Commissioner": "Tax Commissioner"},
}
TYPOS = {
    "GREGORTY D. STUMBO": "GREGORY D. STUMBO",
    "SHERIE DONAHUE": "SHERI DONAHUE",
    "RYAN R. QUARLES": "RYAN F. QUARLES",
    "LIEUTENANT GOVERNOR TIM GRIFFIN": "TIM GRIFFIN",
    "TREASURER OF STATE DENNIS MILLIGAN": "DENNIS MILLIGAN",
    "TOMMY LAND COMMISSIONER OF STATE LANDS": "TOMMY LAND",
    "ATTORNEY GENERAL LESLIE RUTLEDGE": "LESLIE RUTLEDGE",
    "SECRETARY OF STATE JOHN THURSTON": "JOHN THURSTON",
    "STATE REPRESENTATIVE MARK LOWERY": "MARK LOWERY",
}
PARTIES = {
    "DEM": "DEMOCRATIC",
    "D": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "R": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "L": "LIBERTARIAN",
    "LBT": "LIBERTARIAN",
}
COUNTIES = {"AR": 75, "IA": 99, "KY": 120, "ND": 53}
METHOD = (
    "County totals are sums of OpenElections' transcriptions of the official precinct returns (Iowa and Kentucky, one "
    "file per county) or of the official county returns (North Dakota), at a pinned commit, with over- and under-votes "
    "left out. Arkansas's files have no rows for Phillips County. Kentucky's sums run 0.03% to 0.1% below the State Board of Elections' certified statewide totals (for "
    "attorney general, 822,834 and 601,938 against 823,346 and 602,272), so a precinct or two is missing from "
    "OpenElections' files. The winner shown is the candidate with the most votes in those totals."
)


def paths(state: str) -> list[str]:
    tree = json.loads(
        raw.cached(
            openelections.SOURCE,
            openelections.TREE.format(state=state.lower(), commit=openelections.COMMITS[state]),
        ).path.read_text()
    )
    stamp = f"{DAYS[state]:%Y%m%d}__{state.lower()}__general"
    found = [entry["path"] for entry in tree["tree"] if entry["path"].rsplit("/", 1)[-1].startswith(stamp)]
    if state == "ND":
        return [next(path for path in found if path.endswith("__county.csv"))]
    return sorted(path for path in found if path.endswith("__precinct.csv") and path.count("__") == 4)


def url(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path)


def fetch() -> None:
    for state in DAYS:
        for path in paths(state):
            raw.fetch(SOURCE, url(state, path), suffix=".csv")


def read(state: str, path: str) -> pl.DataFrame:
    data = raw.cached(SOURCE, url(state, path)).path.read_bytes()
    frame = pl.read_csv(io.BytesIO(data), infer_schema_length=0, truncate_ragged_lines=True)
    return frame.rename({column: column.strip().lower() for column in frame.columns})


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, day in DAYS.items():
        frame = pl.concat([read(state, path) for path in paths(state)], how="diagonal_relaxed").filter(
            pl.col("office").is_in(list(OFFICES[state]))
            & ~pl.col("candidate").str.to_uppercase().str.contains(openelections.NOT_COUNTED)
        )
        if "precinct" in frame.columns and frame.filter(pl.col("precinct").str.contains(r"(?i)^total")).height:
            raise ValueError(f"{state}: total rows among the precincts")
        frames.append(
            frame.select(
                year=pl.lit(day.year, pl.Int32),
                day=pl.lit(day),
                state=pl.lit(state),
                title=pl.col("office").replace_strict(OFFICES[state]),
                post=pl.lit(None, pl.String),
                special=pl.lit(False),
                county=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips[state]),
                writein=pl.col("candidate").str.to_lowercase().str.contains("write"),
                candidate=pl.col("candidate")
                .str.strip_chars()
                .str.replace_all(r"\s+", " ")
                .str.to_uppercase()
                .replace(TYPOS),
                party=pl.col("party").replace_strict(PARTIES, default=None),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64).cast(pl.Int64),
            ).with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        )
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = (
        pl.concat(frames)
        .group_by(*contest, "county", "candidate", "party", "writein", maintain_order=True)
        .agg(pl.col("votes").sum())
        .with_columns(
            magnitude=pl.lit(1, pl.Int16), kind=pl.lit("county"), code=pl.lit("county"), measure=pl.lit(1, pl.Int16)
        )
    )
    races = returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.col("state").first().replace_strict(COUNTIES),
    )
    short = races.filter(
        pl.col("present").list.len() != pl.col("counties") - pl.when(pl.col("state") == "AR").then(1).otherwise(0)
    )
    if short.height:
        raise ValueError(f"not every county: {short}")
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(races.select(*contest[:4], "counties", pl.col("present").list.len()))
    with pl.Config(tbl_rows=40):
        print(
            returns.group_by("state", "title", "candidate", "party")
            .agg(pl.col("votes").sum())
            .sort("state", "title", "votes")
        )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        sources = {}
        for state, day in DAYS.items():
            title = f"{state} {day.year} general election results"
            sources[state] = cs.source("OpenElections", title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
            for path in paths(state):
                cs.artifact(raw.cached(SOURCE, url(state, path)), sources[state])
        return sources

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Arkansas's 2022 lieutenant governor, attorney general, secretary of state, treasurer, auditor, "
        "and land commissioner, Iowa's 2022 auditor, treasurer, and secretary of agriculture, Kentucky's 2019 secretary of state, "
        "attorney general, auditor, treasurer, and agriculture commissioner, and North Dakota's 2018 tax commissioner "
        "by county from OpenElections' transcriptions of the official returns",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
        declared={
            **{
                f"2022-11-08:AR:{office}": (
                    winner,
                    f"{winner} led by far more than Phillips County's missing vote and was certified elected.",
                )
                for office, winner in [
                    ("lieutenant-governor", "Leslie Rutledge"),
                    ("attorney-general", "Tim Griffin"),
                    ("secretary-of-state", "John Thurston"),
                    ("treasurer", "Mark Lowery"),
                    ("auditor", "Dennis Milligan"),
                    ("commissioner-of-state-lands", "Tommy Land"),
                ]
            },
            "2022-11-08:IA:auditor": (
                "Rob Sand",
                "Rob Sand was certified elected by the State Canvassing Board, 600,719 votes to Todd Halbur's 597,826.",
            ),
        },
    )
