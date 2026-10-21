import json
import re
from datetime import datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.dc_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://electionresults.dcboe.org"
INFO = HOST + "/electionResults/getElectionInfo/{year}-Primary-Election"
YEARS = range(2012, 2025, 2)
PUBLISHER = "District of Columbia Board of Elections"
TITLE = "Election results"
DELEGATE = re.compile(r"DELEGATE (TO )?(THE )?(U\.S\. )?HOUSE OF REPRESENTATIVES")
PRESIDENT = re.compile(r"PRESIDENTIAL PREFERENCE")
PARTIES = {
    "DEM": "democratic",
    "DEMOCRATIC": "democratic",
    "REP": "republican",
    "REPUBLICAN": "republican",
    "LIB": "libertarian",
    "STG": "d-c-statehood-green",
    "GRN": "d-c-statehood-green",
}


def link(year: int) -> str:
    return json.loads(raw.cached(SOURCE, INFO.format(year=year)).path.read_text())["CsvLink"]


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, INFO.format(year=year), suffix=".json")
        raw.fetch(SOURCE, link(year), suffix=".csv")


def parse() -> None:
    rows: list[tuple] = []
    for year in YEARS:
        frame = pl.read_csv(raw.cached(SOURCE, link(year)).path, infer_schema=False, encoding="utf8-lossy")
        frame = frame.rename({c: re.sub(r"[^a-z]", "", c.lower()).removesuffix("number") for c in frame.columns})
        frame = frame.with_columns(pl.col(pl.String).str.strip_chars()).with_columns(
            contestname=pl.col("contestname").str.replace_all(r"\s+", " ")
        )
        for (contest, date), lines in frame.group_by("contestname", "electiondate", maintain_order=True):
            if DELEGATE.search(contest) and "CONVENTION" not in contest:
                chamber = "house"
            elif PRESIDENT.search(contest):
                chamber = "president"
            else:
                continue
            day = datetime.strptime(date.split()[0], "%m/%d/%Y").date()
            named = PARTIES.get(contest.split()[0])
            lines = lines.filter(
                pl.col("precinct").str.contains(r"^\d+$"), ~pl.col("candidate").str.contains("OVER|UNDER")
            ).with_columns(party=pl.lit(named) if named else pl.col("party").replace_strict(PARTIES, default=None))
            for (party,), sums in lines.drop_nulls("party").group_by("party"):
                label = "green" if chamber == "president" and party == "d-c-statehood-green" and year == 2020 else party
                rows.extend(
                    (
                        year,
                        f"{chamber}:{label}",
                        day,
                        False,
                        "DC",
                        0 if chamber == "house" else None,
                        "11001",
                        candidate.upper(),
                        1,
                        candidate.upper() == "WRITE-IN",
                        votes,
                    )
                    for candidate, votes in sums.group_by("candidate")
                    .agg(pl.col("votes").cast(pl.Int64).sum())
                    .iter_rows()
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "day": pl.Date,
                "special": pl.Boolean,
                "state": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "measure": pl.Int16,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(kind=pl.lit("county"), source=pl.lit("DC"))
        .write_parquet(PARSED / "counties.parquet")
    )
    print(len(rows), "candidate rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The District of Columbia's 2012-2024 delegate and presidential primaries from the Board of Elections' certified results",
            polity="US-DC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=HOST,
            license="Public record",
        )
        for year in YEARS:
            for url in (INFO.format(year=year), link(year)):
                cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"DC": source}, strict=True)
        return cs.write()
