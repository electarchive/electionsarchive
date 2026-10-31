import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.tn_primaries_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2000, 8, 3)
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "August 3, 2000 primary election results: U.S. Senate and U.S. House of Representatives by county"
FILE = "https://web.archive.org/web/2023id_/https://sharetngov.tnsosfiles.com/sos/election/results/2000-8/us-count.pdf"
CANDIDATE = re.compile(r"(\d+) \. ")
ROW = re.compile(r"^\s*((?:\d+\s+)*)([A-Za-z][A-Za-z .'-]*?)((?:\s+\d+)*)\s*$")


def fetch() -> None:
    raw.fetch(SOURCE, FILE, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TN"]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILE).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = []
    totals: dict[tuple[int | None, str, str], int] = {}
    sums: dict[tuple[int | None, str, str], int] = {}
    district = None
    split = 0
    names: dict[str, list[str]] = {"democratic": [], "republican": []}
    for line in text.splitlines():
        if "Democratic" in line and "Republican" in line:
            split = line.index("Republican") - 4
            district = -1 if "District" in line else None
            names = {"democratic": [], "republican": []}
            continue
        if district == -1 and (found := re.search(r"Party\s+(\d+)\s+Party", line)):
            district = int(found.group(1))
            continue
        marks = list(CANDIDATE.finditer(line))
        if marks:
            for index, mark in enumerate(marks):
                end = marks[index + 1].start() if index + 1 < len(marks) else len(line)
                names["republican" if mark.start() >= split else "democratic"].append(line[mark.end() : end].strip())
            continue
        match = ROW.match(line)
        if match is None or not match.group(3).strip():
            continue
        votes = [int(value) for value in (match.group(1) + match.group(3)).split()]
        if len(votes) != len(names["democratic"]) + len(names["republican"]):
            raise ValueError(f"{district} {match.group(2)}: {len(votes)} columns")
        columns = [
            (party, candidate, votes[offset + index])
            for party, offset in (("democratic", 0), ("republican", len(names["democratic"])))
            for index, candidate in enumerate(names[party])
        ]
        county = match.group(2).strip()
        for party, candidate, count in columns:
            if county in ("District", "Statewide"):
                totals[(district, party, candidate)] = count
            else:
                sums[(district, party, candidate)] = sums.get((district, party, candidate), 0) + count
                rows.append((district, party, candidate, county, count))
    if totals.keys() != sums.keys():
        raise ValueError(f"totals for {sorted(totals.keys() ^ sums.keys(), key=str)}")
    for key, total in totals.items():
        if sums[key] != total:
            raise ValueError(f"{key}: counties sum to {sums[key]}, printed {total}")
    frame = pl.DataFrame(
        rows,
        schema={
            "district": pl.Int64,
            "party": pl.String,
            "candidate": pl.String,
            "county": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.lit(2000, pl.Int32),
        chamber=pl.concat_str(
            pl.when(pl.col("district").is_null()).then(pl.lit("senate")).otherwise(pl.lit("house")),
            pl.col("party"),
            separator=":",
        ),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("TN"),
        district=pl.col("district").cast(pl.Int16),
        code=pl.col("code"),
        candidate=pl.when(pl.col("candidate").str.starts_with("Write-Ins"))
        .then(pl.lit("WRITE-INS"))
        .otherwise(pl.col("candidate").str.to_uppercase()),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.starts_with("Write-Ins"),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("TN"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee's August 3, 2000 U.S. Senate and U.S. House primaries by county, from the Division of Elections' official results",
            polity="US-TN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sharetngov.tnsosfiles.com/sos/election/results/2000-08/index.htm",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, FILE), source)
        medsl.attach(cs, conn, frame, {"TN": source}, strict=True)
        return cs.write()
