import csv
import json
import re
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, oe_federal, openelections
from ea.store import DATA, connect

SOURCE = "us.elstats_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TENANTS = {"VA": elstats.TENANTS["VA"]}
YEARS = range(2000, 2026)
OFFICES = ["President", "Governor", "U.S. Senate", "U.S. House", "United States", "Congress"]
FLAWED = {"2010-11-02:VA:house:3"}
TALLIES = {"Total Votes Cast", "Undervotes", "Overvotes", "Total Ballots Cast", "Blank", "Over Votes", "Under Votes"}


def search(tenant: str, year: int) -> str:
    query = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=tenant) + urlencode({"search": json.dumps(query, separators=(",", ":"))})


def table(tenant: str, contest: str) -> str:
    return elstats.TABLE.format(tenant=tenant, contest=contest)


def chamber(office: str) -> str | None:
    text = office.lower()
    if re.match(r"^president", text):
        return "president"
    if text == "governor":
        return "governor"
    if openelections.SENATE.search(text):
        return "senate"
    if openelections.HOUSE.search(text):
        return "house"
    return None


def contests(state: str) -> pl.DataFrame:
    tenant = TENANTS[state][0]
    frames = [
        pl.read_csv(raw.cached(SOURCE, search(tenant, year)).path, infer_schema=False, truncate_ragged_lines=True)
        for year in YEARS
    ]
    return (
        pl.concat([frame for frame in frames if "contest_id" in frame.columns], how="diagonal_relaxed")
        .filter(~pl.col("election_type").str.contains("(?i)primary|convention|caucus"))
        .select(
            "contest_id",
            day=pl.col("election_date").str.slice(0, 10).str.to_date(),
            special=pl.col("election_type").str.contains("(?i)special"),
            chamber=pl.col("office_name").map_elements(chamber, return_dtype=pl.String),
            district=pl.col("district_name").str.extract(r"(\d+)").cast(pl.Int16, strict=False),
        )
        .filter(pl.col("chamber").is_not_null())
        .unique()
    )


def fetch() -> None:
    for state, (tenant, _, _) in TENANTS.items():
        for year in YEARS:
            try:
                raw.cached(SOURCE, search(tenant, year))
            except LookupError:
                raw.fetch(SOURCE, search(tenant, year), suffix=".csv")
                time.sleep(1)
        for contest in contests(state)["contest_id"]:
            try:
                raw.cached(SOURCE, table(tenant, contest))
            except LookupError:
                raw.fetch(SOURCE, table(tenant, contest), suffix=".csv")
                time.sleep(1)


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, (tenant, _, _) in TENANTS.items():
        for contest, day, special, office, district in contests(state).iter_rows():
            lines = raw.cached(SOURCE, table(tenant, contest)).path.read_text().splitlines()
            frame = pl.read_csv(
                "\n".join(lines[2:]).encode(), has_header=False, infer_schema=False, truncate_ragged_lines=True
            )
            names = [re.sub(r"\s+", " ", name).strip() for name in next(csv.reader(lines[:1]))]
            frame = frame.rename({column: f"c{index}" for index, column in enumerate(frame.columns)})
            locality = pl.when(pl.col("c0") == "Locality").then(pl.col("c1")).forward_fill()
            frame = frame.with_columns(locality=locality).filter(pl.col("c0") == "Precinct")
            whole = pl.col("c1").str.contains(r"(?i)central absentee|provisional|post[- ]election|^ab\b|absentee")
            frames.append(
                frame.unpivot(
                    [f"c{index}" for index, name in enumerate(names) if index >= 2 and name and name not in TALLIES],
                    index=["c1", "locality"],
                    variable_name="column",
                    value_name="votes",
                )
                .with_columns(
                    fips=pl.col("locality")
                    .str.to_lowercase()
                    .str.replace_all(r"\bcounty\b", "")
                    .str.replace_all(r"[^a-z]", "")
                    .replace_strict(fips[state], default=None),
                    candidate=pl.col("column").replace_strict({f"c{index}": name for index, name in enumerate(names)}),
                )
                .select(
                    year=pl.lit(day.year, pl.Int32),
                    chamber=pl.lit(office),
                    day=pl.lit(day),
                    special=pl.lit(special),
                    state=pl.lit(state),
                    district=pl.lit(district if office == "house" else None, pl.Int16),
                    fips="fips",
                    locality="locality",
                    code=pl.when(whole)
                    .then("fips")
                    .otherwise(pl.concat_str(pl.lit(f"{day.year}:{state}:"), "fips", pl.lit(":ES:"), "c1")),
                    candidate=pl.col("candidate").str.to_uppercase(),
                    measure=pl.lit(1, pl.Int16),
                    writein=pl.col("candidate").str.contains("(?i)write"),
                    votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
                    kind=pl.when(whole).then(pl.lit("county")).otherwise(pl.lit("precinct")),
                    source=pl.lit(state),
                )
            )
    frame = pl.concat(frames)
    lost = frame.filter(pl.col("fips").is_null())
    if lost.height:
        print(lost.group_by("state", "locality").agg(pl.col("votes").sum()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("fips").is_not_null()).drop("locality").with_columns(
        candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate")
    ).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="President, U.S. Senate, U.S. House, and governor elections 2000–2025 by precinct from the "
            "states' ElectionStats archives, for contests no other source holds by precinct",
            polity="US",
        )
        gaps = oe_federal.wanted(conn, SOURCE) - FLAWED
        base = pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "chamber",
            pl.col("district").cast(pl.String),
            separator=":",
            ignore_nulls=True,
        )
        frame = frame.filter(
            pl.when("special").then(base + ":special").otherwise(base).is_in(list(gaps))
            | base.is_in(list(gaps))
            | (pl.col("chamber") == "house")
            & pl.concat_str(pl.col("day").cast(pl.String), "state", pl.lit("house:0"), separator=":").is_in(list(gaps))
        )
        print(frame.group_by("state", "day", "chamber").agg(pl.col("district").n_unique()).sort("state", "day"))
        sources = {}
        for state, (tenant, publisher, site) in TENANTS.items():
            sources[state] = cs.source(publisher, elstats.TITLE)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=elstats.TITLE,
                    url=site,
                    license="Public record",
                )
            for contest in contests(state).filter(pl.col("day").dt.year().is_in(list(set(frame["year"]))))[
                "contest_id"
            ]:
                cs.artifact(raw.cached(SOURCE, table(tenant, contest)), sources[state])
        medsl.attach(cs, conn, frame, sources, grain="precinct")
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        minted = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            minted.select(
                "id",
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} precincts")
        return cs.write()
