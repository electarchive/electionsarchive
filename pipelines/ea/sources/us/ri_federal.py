import json
from datetime import datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ri_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://rigov.s3.amazonaws.com/election/results/"
FOLDERS = [
    "2012/presidential_preference_primary",
    "2012/statewide_primary",
    "2014/statewide_primary",
    "2016/presidential_preference_primary",
    "2016/statewide_primary",
    "2018/statewide_primary",
    "2020/presidential_preference_primary",
    "2020/statewide_primary",
    "2022/statewide_primary",
]
COUNTIES = {
    "44001": "barrington bristol warren",
    "44003": "coventry east_greenwich warwick west_greenwich west_warwick",
    "44005": "jamestown little_compton middletown newport portsmouth tiverton",
    "44007": "burrillville central_falls cranston cumberland east_providence foster glocester johnston lincoln north_providence north_smithfield pawtucket providence scituate smithfield woonsocket",
    "44009": "charlestown exeter hopkinton new_shoreham narragansett north_kingstown richmond south_kingstown westerly",
}
TOWNS = {town: code for code, towns in COUNTIES.items() for town in towns.split()}
MISSING = {("2020/statewide_primary", town) for town in ("barrington", "warren", "lincoln")}
PARTIES = {"DEM": "democratic", "REP": "republican"}
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Election results by city and town"


def fetch() -> None:
    for folder in FOLDERS:
        for town in [*TOWNS, "statewide"]:
            try:
                raw.fetch(SOURCE, f"{BASE}{folder}/{town}.json", suffix=".json")
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 403:
                    raise
                print("no file:", folder, town)


def parse() -> None:
    rows = []
    for folder in FOLDERS:
        totals: dict[tuple[str, str, str], int] = {}
        statewide: dict[tuple[str, str, str], int] = {}
        for town in [*TOWNS, "statewide"]:
            if (folder, town) in MISSING:
                continue
            data = json.loads(raw.cached(SOURCE, f"{BASE}{folder}/{town}.json").path.read_text())
            day = datetime.strptime(data["election_date"], "%B %d, %Y").date()
            for contest in data["contests"]:
                name = contest["name"].upper()
                if "GENERAL ASSEMBLY" in name or "DELEGATE" in name:
                    continue
                if name.endswith("PRESIDENTIAL CANDIDATE"):
                    chamber, district = "president", None
                elif "SENATOR IN CONGRESS" in name or name.endswith("U.S. SENATOR"):
                    chamber, district = "senate", None
                elif "REPRESENTATIVE IN CONGRESS DISTRICT" in name:
                    chamber, district = "house", int(name.rsplit(" ", 1)[1])
                else:
                    continue
                for candidate in contest["candidates"]:
                    party = PARTIES.get(candidate.get("party_code") or contest.get("contest_party_designation"))
                    if party is None:
                        continue
                    key = (f"{chamber}:{party}", str(district), candidate["name"].rstrip("*").upper())
                    votes = int(candidate["votes"].replace(",", ""))
                    if town == "statewide":
                        statewide[key] = statewide.get(key, 0) + votes
                        continue
                    totals[key] = totals.get(key, 0) + votes
                    rows.append((day.year, key[0], day, False, "RI", district, TOWNS[town], key[2], 1, False, votes))
        if totals != statewide:
            print(
                folder,
                "towns differ from the statewide file:",
                {
                    k: (totals.get(k), statewide.get(k))
                    for k in set(totals) | set(statewide)
                    if totals.get(k) != statewide.get(k)
                },
            )
    PARSED.mkdir(parents=True, exist_ok=True)
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
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("RI")).write_parquet(PARSED / "counties.parquet")
    print(len(rows), "rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2012 to 2022 president, U.S. Senate, and House primaries by county",
            polity="US-RI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ri.gov/election/results/",
            license="Public record",
        )
        for folder in FOLDERS:
            for town in [*TOWNS, "statewide"]:
                if (folder, town) in MISSING:
                    continue
                cs.artifact(raw.cached(SOURCE, f"{BASE}{folder}/{town}.json"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"RI": source}, strict=True)
        return cs.write()
