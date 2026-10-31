import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nv_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.nvsos.gov/silverstate{year}pri/_xml/USandNV.xml"
DAYS = {2018: date(2018, 6, 12), 2020: date(2020, 6, 9), 2022: date(2022, 6, 14), 2024: date(2024, 6, 11)}
COUNTIES = {
    "CarsonCity": "32510",
    "Churchill": "32001",
    "Clark": "32003",
    "Douglas": "32005",
    "Elko": "32007",
    "Esmeralda": "32009",
    "Eureka": "32011",
    "Humboldt": "32013",
    "Lander": "32015",
    "Lincoln": "32017",
    "Lyon": "32019",
    "Mineral": "32021",
    "Nye": "32023",
    "Pershing": "32027",
    "Storey": "32029",
    "Washoe": "32031",
    "WhitePine": "32033",
}
PARTIES = {
    "Democratic": "democratic",
    "Republican": "republican",
    "Libertarian": "libertarian",
    "Independent American": "independent_american",
}
PUBLISHER = "Nevada Secretary of State"
TITLE = "Silver State primary election results"


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, BASE.format(year=year), suffix=".xml")


def parse() -> None:
    rows = []
    for year, day in DAYS.items():
        seen = set()
        for race in ET.fromstring(
            raw.cached(SOURCE, BASE.format(year=year)).path.read_bytes().decode("utf-8-sig")
        ).iter("Race"):
            found = re.fullmatch(
                r"(United States Senator|U\.S\. Representative in Congress, District (\d+)) \(([\w ]+)\)",
                race.get("RaceTitle"),
            )
            if found is None or found[3] not in PARTIES or race.get("RaceID") in seen:
                continue
            seen.add(race.get("RaceID"))
            for candidate in race.iter("Candidate"):
                name = candidate.get("CandidateName")
                if candidate.get("CandidateID") == "-1":
                    continue
                if "," in name:
                    last, first = name.split(",", 1)
                    name = f"{first.strip()} {last.strip()}"
                for county, code in COUNTIES.items():
                    if candidate.get(f"{county}Votes") in (None, "N/A"):
                        continue
                    rows.append(
                        (
                            year,
                            f"{'senate' if found[2] is None else 'house'}:{PARTIES[found[3]]}",
                            day,
                            False,
                            "NV",
                            int(found[2]) if found[2] else None,
                            code,
                            re.sub(r"\s+", " ", name).upper(),
                            1,
                            False,
                            int(candidate.get(f"{county}Votes").replace(",", "")),
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NV")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nevada's 2018, 2020, 2022, and 2024 U.S. Senate and House primaries by county",
            polity="US-NV",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.nvsos.gov/elections/election-information/archived-election-results",
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, BASE.format(year=year)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NV": source}, strict=True)
        return cs.write()
