import csv
import io
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ct_statewide_oe, elstats, medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.ct_elstats_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CONTESTS = {7174: (date(2006, 11, 7), "Comptroller")}
CROSSWALK = "2006/20061107__ct__general__town.csv"
PUBLISHER, TITLE = elstats.TENANTS["CT"][1], "Election statistics archive"
PARTIES = {"Democratic": "DEMOCRATIC", "Republican": "REPUBLICAN", "Libertarian": "LIBERTARIAN", "Green": "GREEN"}
METHOD = (
    "County totals are the sums of the town rows in the Secretary of the State's election statistics archive, "
    "with each town placed in its county by OpenElections' town returns. The winner shown is the candidate with the "
    "most votes in those sums."
)


def url(contest: int) -> str:
    return elstats.TABLE.format(tenant=elstats.TENANTS["CT"][0], contest=contest)


def fetch() -> None:
    for contest in CONTESTS:
        raw.fetch(SOURCE, url(contest), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["CT"]
    towns = dict(
        pl.read_csv(
            io.BytesIO(raw.cached(ct_statewide_oe.SOURCE, ct_statewide_oe.url(CROSSWALK)).path.read_bytes()),
            infer_schema_length=0,
        )
        .filter(pl.col("county").is_not_null(), pl.col("county") != "CONNECTICUT", pl.col("town") != "Total")
        .select("town", pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips))
        .unique()
        .iter_rows()
    )
    rows = []
    for contest, (day, office) in CONTESTS.items():
        names, parties, *lines = csv.reader(io.StringIO(raw.cached(SOURCE, url(contest)).path.read_text()))
        state = next(line for line in lines if line[0] == "State")
        for line in lines:
            if line[0] != "City/Town":
                continue
            for column in range(2, len(names)):
                if not parties[column]:
                    continue
                rows.append(
                    {
                        "year": day.year,
                        "day": day,
                        "title": office,
                        "county": towns[line[1]],
                        "candidate": " ".join(names[column].split()).upper(),
                        "party": PARTIES[parties[column]],
                        "votes": int(line[column]),
                        "printed": int(state[column]),
                    }
                )
    frame = pl.DataFrame(rows)
    check = frame.group_by("title", "candidate").agg(pl.col("votes").sum(), pl.col("printed").first())
    if (check["votes"] != check["printed"]).any():
        raise ValueError(f"town sums differ from the state row\n{check}")
    returns = (
        frame.group_by("year", "day", "title", "county", "candidate", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
        .select(
            pl.col("year").cast(pl.Int32),
            "day",
            state=pl.lit("CT"),
            title="title",
            post=pl.lit(None, pl.String),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            county="county",
            candidate="candidate",
            party="party",
            writein=pl.lit(False),
            measure=pl.lit(1, pl.Int16),
            votes="votes",
        )
    )
    races = returns.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(8),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(check)


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        community = cs.source("OpenElections", ct_statewide_oe.TITLE)
        for found, title in ((source, TITLE), (community, ct_statewide_oe.TITLE)):
            if found in cs.minted:
                raise ValueError(f"no source {title}")
        for contest in CONTESTS:
            cs.artifact(raw.cached(SOURCE, url(contest)), source)
        cs.artifact(raw.cached(ct_statewide_oe.SOURCE, ct_statewide_oe.url(CROSSWALK)), community)
        return {"CT": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Connecticut's 2006 comptroller election by county from the Secretary of the State's election "
        "statistics archive",
        cite=cite,
        method=METHOD,
        absent="The archive has no town rows for {counties} in this race, so those votes are missing from these totals.",
    )
