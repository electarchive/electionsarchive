import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nj_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
FILES = [
    (date(2020, 7, 7), "2020/2020-official-primary-results-us-president-amended-0826.pdf"),
    (date(2020, 7, 7), "2020/2020-official-primary-results-us-senate-amended-0826.pdf"),
    (date(2020, 7, 7), "2020/2020-official-primary-results-us-house-amended-0826.pdf"),
    (date(2022, 6, 7), "2022/2022-official-primary-results-us-house.pdf"),
]
NAMES = [
    "Atlantic",
    "Bergen",
    "Burlington",
    "Camden",
    "Cape May",
    "Cumberland",
    "Essex",
    "Gloucester",
    "Hudson",
    "Hunterdon",
    "Mercer",
    "Middlesex",
    "Monmouth",
    "Morris",
    "Ocean",
    "Passaic",
    "Salem",
    "Somerset",
    "Sussex",
    "Union",
    "Warren",
]
COUNTIES = {name.upper(): f"34{2 * i + 1:03}" for i, name in enumerate(NAMES)}
ORDINALS = [
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
]
PARTIES = {"Democratic": "democratic", "Republican": "republican", "Libertarian": "libertarian", "Green": "green"}
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official primary election results"


def fetch() -> None:
    for _, path in FILES:
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    rows = []
    for day, path in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        office, district, name, party = None, None, None, None
        for line in text.splitlines():
            if found := re.match(
                r"\s*Candidates for (President|House of Representatives|United States Senate|U\.?S\.? Senate)", line
            ):
                office = (
                    "president" if found[1] == "President" else "house" if found[1].startswith("House") else "senate"
                )
            elif found := re.match(r"(\w+) Congressional District", line):
                district = ORDINALS.index(found[1]) + 1
            elif (
                line[:1].isalpha()
                and not re.match(r"(Name|Candidates|For PRIMARY|Total)", line)
                and line.rstrip().endswith(tuple(PARTIES))
            ):
                name = re.split(r"\s+\(w\)|\s+\*|\s{2,}", line.strip(), maxsplit=1)[0]
                party = PARTIES[line.rstrip().rsplit(" ", 1)[1]]
            elif name and (found := re.match(r"\s+(" + "|".join(COUNTIES) + r")\s.*?([\d,]+)\s*$", line)):
                rows.append(
                    (
                        day.year,
                        f"{office}:{party}",
                        day,
                        False,
                        "NJ",
                        district if office == "house" else None,
                        COUNTIES[found[1]],
                        name.upper(),
                        1,
                        False,
                        int(found[2].replace(",", "")),
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NJ")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's 2020 and 2022 president, U.S. Senate, and House primaries by county",
            polity="US-NJ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.nj.gov/state/elections/election-information-results.shtml",
            license="Public record",
        )
        for _, path in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NJ": source}, strict=True)
        return cs.write()
