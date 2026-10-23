import io
import json
import re
import time
import zipfile
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, elstats_legislature, medsl
from ea.store import DATA, connect

SOURCE = "us.nh_pri_elstats"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEAR = 2014
PARTIES = {"Democratic": "dp", "Republican": "rp"}


def contests() -> pl.DataFrame:
    return (
        pl.read_csv(
            raw.cached(elstats_legislature.SOURCE, elstats_legislature.search("NH", YEAR)).path, infer_schema=False
        )
        .filter(pl.col("election_type") == "Primary Election", pl.col("office_id").is_in(["8", "9"]))
        .select(
            pl.col("contest_id").cast(pl.Int64).alias("contest"),
            pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
            pl.when(pl.col("office_id") == "8").then(pl.lit("house")).otherwise(pl.lit("senate")).alias("chamber"),
            pl.col("district_name").alias("district"),
            pl.col("primary_party").replace_strict(PARTIES).alias("party"),
        )
        .unique()
        .sort("contest")
    )


def fetch() -> None:
    for (contest,) in contests().select("contest").iter_rows():
        body = {"query": elstats_legislature.QUERY, "variables": {"contestId": contest}}
        try:
            raw.cached(SOURCE, elstats_legislature.GRAPHQL["NH"], body)
        except LookupError:
            raw.fetch(SOURCE, elstats_legislature.GRAPHQL["NH"], suffix=".json", body=body)
            time.sleep(1)


def parse() -> None:
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False)
    towns: dict[str, str] = {}
    for geoid, name in gazetteer.filter(pl.col("USPS") == "NH").select("GEOID", "NAME").iter_rows():
        towns.setdefault(elstats.plain(name), geoid[:5])
        towns.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    towns.update({name: code for (state, name), code in elstats.FORMER.items() if state == "NH"})
    rows = []
    unknown: set[str] = set()
    for contest, day, chamber, district, party in contests().iter_rows():
        body = {"query": elstats_legislature.QUERY, "variables": {"contestId": contest}}
        data = json.loads(raw.cached(SOURCE, elstats_legislature.GRAPHQL["NH"], body).path.read_text())
        data = data.get("data", data)["contestGranularData"]
        names = {
            entry["candidateId"]: entry["candidate"]["displayName"]
            for entry in data["candidates"]
            if entry["candidate"]["pseudocandidate"] in (None, "ALL_OTHER_CANDIDATES")
        }
        writeins = {
            entry["candidateId"]
            for entry in data["candidates"]
            if entry["candidate"]["pseudocandidate"] == "ALL_OTHER_CANDIDATES"
        }
        seat = f"house:{district.lower().replace(' ', '-')}" if chamber == "house" else f"senate:{int(district)}"
        for town in data["divisions"]["children"]:
            name = town["division"]["name"]
            fips = towns.get(elstats.plain(name)) or towns.get(
                elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))
            )
            if fips is None:
                unknown.add(name)
                continue
            for leaf in [town] if not town["children"] else town["children"]:
                for row in leaf["granularRow"]:
                    if row["candidateId"] in names:
                        rows.append(
                            (
                                day,
                                f"{seat}:{party}",
                                fips,
                                "Write-ins" if row["candidateId"] in writeins else names[row["candidateId"]],
                                row["candidateId"] in writeins,
                                int(row["votes"] or 0),
                            )
                        )
    if unknown:
        raise ValueError(f"unknown towns: {sorted(unknown)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "writein", "votes"], orient="row")
        .group_by("day", "chamber", "code", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.lit(YEAR, pl.Int32),
            state=pl.lit("NH"),
            special=pl.lit(False),
            district=pl.lit(None, pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("NH"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2014 State House and State Senate primaries by county, summed from the towns and "
            "wards in New Hampshire Public Radio's election database",
            polity="US-NH",
        )
        source = cs.source(elstats.TENANTS["NH"][1], elstats.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {elstats.TITLE}")
        cs.artifact(raw.cached(elstats_legislature.SOURCE, elstats_legislature.search("NH", YEAR)), source)
        for (contest,) in contests().select("contest").iter_rows():
            body = {"query": elstats_legislature.QUERY, "variables": {"contestId": contest}}
            cs.artifact(raw.cached(SOURCE, elstats_legislature.GRAPHQL["NH"], body), source)
        medsl.attach(cs, conn, frame, {"NH": source}, strict=True)
        return cs.write()
