import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.az_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://cdn1.arizona.vote/data/"
FILES = {
    date(2020, 3, 17): "28/0/all_county_races_28_0_en_7254.json",
    date(2020, 8, 4): "17/0/all_county_races_17_0_en_10076.json",
    date(2022, 8, 2): "32/0/all_county_races_32_0_en_10074.json",
    date(2024, 3, 19): "59/0/all_county_races_59_0_en_10675.json",
    date(2024, 7, 30): "46/0/all_county_races_46_0_en_11261.json",
}
COUNTIES = {
    "Apache": "04001",
    "Cochise": "04003",
    "Coconino": "04005",
    "Gila": "04007",
    "Graham": "04009",
    "Greenlee": "04011",
    "La Paz": "04012",
    "Maricopa": "04013",
    "Mohave": "04015",
    "Navajo": "04017",
    "Pima": "04019",
    "Pinal": "04021",
    "Santa Cruz": "04023",
    "Yavapai": "04025",
    "Yuma": "04027",
}
PARTIES = {"DEM": "democratic", "REP": "republican", "LBT": "libertarian", "GRN": "green"}
PUBLISHER = "Arizona Secretary of State"
TITLE = "Arizona election results"


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".json")


def parse() -> None:
    rows = []
    for day, path in FILES.items():
        for row in json.loads(raw.cached(SOURCE, BASE + path).path.read_text()):
            found = re.match(r"(U\.S\. Senator|President of the United States|U\.S\. Representative in Congress - District No\. (\d+)) \((\w+)\)", row["ContestName"])
            name = row["ChoiceName"]
            if found is None or found[3] not in PARTIES or name.lower().startswith("write-in"):
                continue
            office = "senate" if found[1] == "U.S. Senator" else "president" if found[2] is None and found[1].startswith("President") else "house"
            rows.append(
                (
                    day.year,
                    f"{office}{":special" if office == "senate" and day.year == 2020 else ""}:{PARTIES[found[3]]}",
                    day,
                    False,
                    "AZ",
                    int(found[2]) if office == "house" else None,
                    COUNTIES[row["CountyName"]],
                    name.upper(),
                    1,
                    False,
                    row["ChoiceTotalVotes"],
                )
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("AZ")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona's 2020, 2022, and 2024 president, U.S. Senate, and House primaries by county",
            polity="US-AZ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.arizona.vote/",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AZ": source}, strict=True)
        return cs.write()
