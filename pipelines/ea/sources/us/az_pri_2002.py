import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.az_pri_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://web.archive.org/web/20041026113534id_/http://www.azsos.gov/election/2002/Primary/Canvass2002PE.pdf"
DAY = date(2002, 9, 10)
PUBLISHER = "Arizona Secretary of State"
TITLE = "State of Arizona Official Canvass, 2002 Primary Election - September 10, 2002"
PARTIES = {"DEM": "democratic", "REP": "republican", "LBT": "libertarian", "GRN": "green", "IND": "independent"}
IGNORED = {"STATE OF ARIZONA OFFICIAL CANVASS", "REGISTRATION", "BALLOTS CAST", "PERCENT TURNOUT", "PRECINCTS"}
COUNTIES = [
    "Apache",
    "Cochise",
    "Coconino",
    "Gila",
    "Graham",
    "Greenlee",
    "La Paz",
    "Maricopa",
    "Mohave",
    "Navajo",
    "Pima",
    "Pinal",
    "Santa Cruz",
    "Yavapai",
    "Yuma",
]
HOUSE = re.compile(r"U\.S\. REPRESENTATIVE IN CONGRESS - DISTRICT NO\. (\d+)")
ROW = re.compile(r"\s*\((\w+)\)\s+(.*?)\s{2,}((?:(?:---|[\d,]+)\s*)+)")


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        pdf = subprocess.run(
            ["curl", "-sL", "-m", "300", "--resolve", "web.archive.org:443:207.241.237.2", URL],
            capture_output=True,
            check=True,
        ).stdout
        raw.store(
            SOURCE,
            URL,
            pdf,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.lower()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '04%%'"""
            ).fetchall()
        }
    rows = []
    district = None
    for line in text.splitlines():
        stripped = line.strip()
        if found := HOUSE.fullmatch(stripped):
            district = int(found[1])
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9 .,\-/&'()]{6,}", stripped) and stripped not in IGNORED:
            district = None
            continue
        if district is None or not (row := ROW.fullmatch(line)):
            continue
        party = PARTIES[row[1]]
        name = row[2]
        writein = "(Write-In)" in name
        candidate = " ".join(name.replace("*", "").replace("(Write-In)", "").split()).upper()
        cells = row[3].split()
        if len(cells) != len(COUNTIES) + 1:
            raise ValueError(f"district {district} {name}: {len(cells)} cells")
        values = [None if cell == "---" else int(cell.replace(",", "")) for cell in cells]
        if sum(v or 0 for v in values[:-1]) != values[-1]:
            raise ValueError(f"district {district} {name}: counties do not add up to the total")
        for county, value in zip(COUNTIES, values[:-1], strict=True):
            if value is not None:
                rows.append(
                    (
                        2002,
                        f"house:{party}",
                        DAY,
                        False,
                        "AZ",
                        district,
                        codes[re.sub(r"[^a-z]", "", county.lower())],
                        candidate,
                        1,
                        writein,
                        value,
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("AZ")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona's 2002 U.S. House primaries by county from the Secretary of State's official canvass",
            polity="US-AZ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://azsos.gov/elections/election-information",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AZ": source}, strict=True)
        return cs.write()
