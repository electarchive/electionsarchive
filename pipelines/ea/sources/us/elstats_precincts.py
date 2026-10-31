import csv
import io
import json
import re
import time
import zipfile
from datetime import date, timedelta
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
MA_CHAMBERS = {1: "president", 3: "governor", 5: "house", 6: "senate"}
MA_STAGES = {"General Election": False, "Special General Election": True}
TALLIES = {
    "Total Votes Cast",
    "Undervotes",
    "Overvotes",
    "Total Ballots Cast",
    "Blank",
    "Blanks",
    "Over Votes",
    "Under Votes",
}


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


def ma_contests() -> list[tuple[str, int, bool, str, int | None]]:
    found = []
    for office, chamber in MA_CHAMBERS.items():
        listing = raw.cached(SOURCE, f"{elstats.MASSACHUSETTS}search/year_from:2000/year_to:2025/office_id:{office}")
        for election, year, district, stage in elstats.MA_ROW.findall(listing.path.read_text()):
            if stage in MA_STAGES:
                number = re.match(r"(\d+)", district)
                found.append((election, int(year), MA_STAGES[stage], chamber, int(number[1]) if number else None))
    return found


def ma_table(election: str) -> str:
    return f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:1/"


def fetch() -> None:
    for office in MA_CHAMBERS:
        url = f"{elstats.MASSACHUSETTS}search/year_from:2000/year_to:2025/office_id:{office}"
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".html")
            time.sleep(1)
    for election, *_ in ma_contests():
        try:
            raw.cached(SOURCE, ma_table(election))
        except LookupError:
            raw.fetch(SOURCE, ma_table(election), suffix=".csv")
            time.sleep(1)
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
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "MA")
    places: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        places.setdefault(elstats.plain(name), geoid[:5])
        places.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    with connect() as conn:
        specials = [key for key in oe_federal.wanted(conn, SOURCE, "MA") if key.endswith(":special")]
    for election, year, special, chamber, district in ma_contests():
        lines = raw.cached(SOURCE, ma_table(election)).path.read_text().splitlines()
        names = [re.sub(r"\s+", " ", name).strip() for name in next(csv.reader(lines[:1]))]
        grid = pl.read_csv(
            "\n".join(lines[2:]).encode(), has_header=False, infer_schema=False, truncate_ragged_lines=True
        )
        grid = grid.rename({column: f"c{index}" for index, column in enumerate(grid.columns)})
        columns = [f"c{index}" for index, name in enumerate(names) if index >= 3 and name and name not in TALLIES]
        if special:
            keys = [
                key
                for key in specials
                if key[:4] == str(year)
                and f":{chamber}" in key
                and (chamber != "house" or f":house:{district}:" in key)
            ]
            if len(keys) != 1:
                continue
            day = date.fromisoformat(keys[0][:10])
        else:
            day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
        town = (
            pl.col("c0")
            .str.replace(r"^N\. ", "North ")
            .str.replace(r"^S\. ", "South ")
            .str.replace(r"^E\. ", "East ")
            .str.replace(r"^W\. ", "West ")
        )
        fips = town.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(places, default=None)
        frames.append(
            grid.filter(pl.col("c0").is_not_null(), pl.col("c0") != "TOTALS", pl.col("c2").is_not_null())
            .unpivot(columns, index=["c0", "c1", "c2"], variable_name="column", value_name="votes")
            .with_columns(
                locality=town,
                fips=fips,
                candidate=pl.col("column").replace_strict({f"c{index}": name for index, name in enumerate(names)}),
            )
            .select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.lit(chamber),
                day=pl.lit(day),
                special=pl.lit(special),
                state=pl.lit("MA"),
                district=pl.lit(district if chamber == "house" else None, pl.Int16),
                fips="fips",
                locality="locality",
                code=pl.concat_str(
                    pl.lit(f"{day.year}:MA:"),
                    "fips",
                    pl.lit(":ES:"),
                    "locality",
                    pl.when(pl.col("c1") != "-").then(pl.lit(" Ward ") + pl.col("c1")).otherwise(pl.lit("")),
                    pl.lit(" Precinct "),
                    "c2",
                ),
                candidate=pl.col("candidate").str.to_uppercase().str.replace(r"/.*$", "").str.strip_chars(),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write|all others"),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
                kind=pl.lit("precinct"),
                source=pl.lit("MA"),
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
        if "MA" in frame["state"].to_list():
            sources["MA"] = cs.source(elstats.MA_PUBLISHER, elstats.TITLE)
            if sources["MA"] in cs.minted:
                cs.add(
                    "source",
                    id=sources["MA"],
                    kind="official_body",
                    publisher=elstats.MA_PUBLISHER,
                    title=elstats.TITLE,
                    url="https://electionstats.state.ma.us",
                    license="Public record",
                )
            kept = set(frame.filter(pl.col("state") == "MA")["year"])
            for election, year, *_ in ma_contests():
                if year in kept:
                    cs.artifact(raw.cached(SOURCE, ma_table(election)), sources["MA"])
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
