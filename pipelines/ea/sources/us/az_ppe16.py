import csv
import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.az_ppe16"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-310"
URL = "https://apps.azsos.gov/results/2016/PPE/{}.txt"
PUBLISHER = "Arizona Secretary of State"
TITLE = "2016 Presidential Preference Election, county precinct files"
DAY = date(2016, 3, 22)
COUNTIES = {
    "Apache": "04001",
    "Cochise": "04003",
    "Coconino": "04005",
    "Gila": "04007",
    "Graham": "04009",
    "Greenlee": "04011",
    "LaPaz": "04012",
    "Maricopa": "04013",
    "Mohave": "04015",
    "Navajo": "04017",
    "Pima": "04019",
    "Pinal": "04021",
    "SantaCruz": "04023",
    "Yavapai": "04025",
    "Yuma": "04027",
}
GEMS = {"Apache", "Coconino", "Gila", "LaPaz", "SantaCruz", "Yuma"}
PARTIES = (("gre", "green"), ("grn", "green"), ("dem", "democratic"), ("rep", "republican"), ("lib", "libertarian"))


def fetch() -> None:
    for name in COUNTIES:
        raw.store(
            SOURCE,
            URL.format(name),
            (INBOX / f"{name}.txt").read_bytes(),
            suffix=".txt",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="text/plain",
        )


def parse() -> None:
    rows: list[tuple[str, str, str, int]] = []
    for name, code in COUNTIES.items():
        lines = raw.cached(SOURCE, URL.format(name)).path.read_text(errors="replace").splitlines()
        found: list[tuple[str, str, int]] = []
        if name in GEMS:
            for row in csv.reader(lines):
                if (
                    len(row) >= 7
                    and re.search(r"pres", row[3], re.IGNORECASE)
                    and int(row[4]) < 999990
                    and row[0] != "999999"
                ):
                    found.append((row[3], row[5], int(row[6])))
        elif name == "Maricopa":
            for row in csv.reader(lines, delimiter="\t"):
                if (
                    len(row) > 9
                    and re.search(r"pres", row[8], re.IGNORECASE)
                    and row[1]
                    and row[4] != "-2"
                    and row[9].isdigit()
                ):
                    found.append((row[8], row[1], int(row[9])))
        elif name == "Yavapai":
            for row in csv.reader(lines):
                if len(row) > 11 and row[0] == "1" and re.search(r"pres", row[6], re.IGNORECASE):
                    found.append((row[6], row[7], int(row[10])))
        else:
            unity = name == "Graham"
            for line in lines:
                contest = line[57:113] if unity else line[111:167]
                if (
                    len(line) > 205 - 55 * unity
                    and re.search(r"pres", contest, re.IGNORECASE)
                    and "BALLOTS" not in contest
                ):
                    party = contest if unity else line[101:104] + contest
                    choice = line[113:151] if unity else line[167:205]
                    found.append((party, choice.strip(), int(line[11:17])))
        for contest, choice, votes in found:
            if re.search(r"over|under|^total", choice, re.IGNORECASE):
                continue
            party = next(value for key, value in PARTIES if re.search(rf"\b{key}", contest, re.IGNORECASE))
            rows.append((code, party, re.sub(r"\s+", " ", choice.replace("''", '"')).upper(), votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["code", "party", "candidate", "votes"], orient="row").group_by(
        "code", "party", "candidate"
    ).agg(pl.col("votes").sum()).select(
        year=pl.lit(2016, pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), "party"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("AZ"),
        district=pl.lit(None, pl.Int16),
        code="code",
        candidate="candidate",
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("AZ"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona's 2016 presidential preference primaries by county from the Secretary of State's precinct files",
            polity="US-AZ",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://apps.azsos.gov/results/2016/PPE/",
                license="Public record",
            )
        for name in COUNTIES:
            cs.artifact(raw.cached(SOURCE, URL.format(name)), source)
        medsl.attach(cs, conn, frame, {"AZ": source}, strict=True)
        return cs.write()
