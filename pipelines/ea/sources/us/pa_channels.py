import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.pa_channels"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://www.electionreturns.pa.gov/api/ElectionReturn/"
LANDING = "https://www.electionreturns.pa.gov/"
PUBLISHER = "Pennsylvania Department of State"
TITLE = "Election returns by county and vote type, 2020, 2022, and 2024 general elections"
ELECTIONS = {date(2020, 11, 3): "83", date(2022, 11, 8): "94", date(2024, 11, 5): "105"}
OFFICES = {
    "1": "president",
    "2": "senate",
    "3": "governor",
    "5": "attorney-general",
    "6": "auditor-general",
    "7": "treasurer",
    "11": "house",
    "12": "senate",
    "13": "house",
}
CHANNELS = {"ElectionDayVotes": 1, "MailInVotes": 3, "ProvisionalVotes": 5}


def query(election: str) -> str:
    return f"electionid={election}&electiontype=G&isactive=0"


def office_url(office: str, election: str) -> str:
    return f"{API}GetOfficeData?officeId={office}&methodName=GetOfficeDetails&{query(election)}"


def county_url(office: str, district: str, election: str) -> str:
    return f"{API}GetCountyBreak?officeId={office}&districtId={district}&methodName=GetCountyBreak&{query(election)}"


def districts(office: str, election: str) -> list[str]:
    content = raw.cached(SOURCE, office_url(office, election)).path.read_bytes()
    text = json.loads(content) if content.strip() else ""
    return re.findall(r"\$\$(\d+)\$\$", text) if text else []


def urls() -> list[str]:
    found = []
    for election in ELECTIONS.values():
        for office in OFFICES:
            found.append(office_url(office, election))
            found += [county_url(office, district, election) for district in districts(office, election)]
    return found


def fetch() -> None:
    for election in ELECTIONS.values():
        for office in OFFICES:
            raw.fetch(SOURCE, office_url(office, election), suffix=".json")
            for district in districts(office, election):
                raw.fetch(SOURCE, county_url(office, district, election), suffix=".json")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["PA"]
    rows = []
    for day, election in ELECTIONS.items():
        for office, chamber in OFFICES.items():
            for district in districts(office, election):
                content = raw.cached(SOURCE, county_url(office, district, election)).path.read_bytes()
                for label, counties in json.loads(json.loads(content))["Election"].items():
                    number = re.match(r"(\d+)", label)
                    seat = int(number[1]) if number and office in {"11", "12", "13"} else None
                    for county in counties:
                        for name, candidates in county.items():
                            for candidate in candidates:
                                total = int(candidate["Votes"] or 0)
                                split = {channel: int(candidate[key] or 0) for key, channel in CHANNELS.items()}
                                if sum(split.values()) != total:
                                    raise ValueError(f"{day} {label} {name} {candidate['CandidateName']}: channels")
                                for channel, votes in split.items():
                                    rows.append(
                                        (
                                            day,
                                            chamber if office not in {"12", "13"} else f"{chamber}:{seat}:g",
                                            seat if office == "11" else None,
                                            name,
                                            candidate["CandidateName"].strip().upper(),
                                            channel,
                                            votes,
                                        )
                                    )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "county": pl.String,
            "candidate": pl.String,
            "channel": pl.Int16,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    frame = (
        frame.with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("PA"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "channel")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("PA"))
        .sort("day", "chamber", "district", "code", "candidate", "channel")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "channels.parquet")
    print(frame.group_by("year", "channel").agg(pl.col("votes").sum()).sort("year", "channel").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "channels.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Pennsylvania's 2020, 2022, and 2024 general elections by county and vote type (election day, mail-in, and provisional), from the Department of State's official returns",
            polity="US-PA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for url in urls():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"PA": source})
        return cs.write()
