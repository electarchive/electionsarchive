import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.tn_senate_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-033"
URL = "https://sos.tn.gov/elections/results"
PUBLISHER = "Tennessee Secretary of State"
TITLE = "August 7, 2014 {party} primary results by county"
DAY = date(2014, 8, 7)
FILES = {"democratic": "20140807_DemocraticPrimaryCounty.pdf", "republican": "20140807_RepublicanPrimaryCounty.pdf"}


def fetch() -> None:
    for name in FILES.values():
        try:
            raw.cached(SOURCE, f"{URL}#{name}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{name}",
                (INBOX / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TN"]
    rows = []
    for party, name in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, f"{URL}#{name}").path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        start = next(i for i, line in enumerate(text) if line.strip() == "United States Senate")
        names = {}
        index = start + 1
        while match := re.fullmatch(r"\s*(\d+)\. (.+)", text[index]):
            names[int(match[1])] = match[2].strip()
            index += 1
        totals: list[int] = []
        sums = [0] * len(names)
        for line in text[index + 1 :]:
            if line.startswith("STATEWIDE TOTALS"):
                totals = [int(value.replace(",", "")) for value in line.split()[2:]]
                break
            found = re.fullmatch(r"(\D+?)\s{2,}([\d,]+(?:\s+[\d,]+)*)", line.strip())
            if found is None:
                continue
            values = [int(value.replace(",", "")) for value in found[2].split()]
            if len(values) != len(names):
                raise ValueError(f"{party} {found[1]}: {len(values)} values for {len(names)} candidates")
            for column, value in enumerate(values):
                sums[column] += value
                rows.append((party, found[1].strip(), names[column + 1], value))
        if sums != totals:
            raise ValueError(f"{party}: county sums {sums} against printed {totals}")
    frame = pl.DataFrame(rows, schema=["party", "county", "candidate", "votes"], orient="row").with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        state=pl.lit("TN"),
        day=pl.lit(DAY),
        year=pl.lit(2014, pl.Int32),
        chamber=pl.concat_str(pl.lit("senate:"), pl.col("party")),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        code=pl.col("code"),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
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
            summary="Tennessee's August 7, 2014 U.S. Senate primaries by county from the Secretary of State's official results",
            polity="US-TN",
        )
        source = cs.source(PUBLISHER, TITLE.format(party="Democratic and Republican"))
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE.format(party="Democratic and Republican"),
            url=URL,
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, f"{URL}#{name}"), source)
        medsl.attach(cs, conn, frame, {"TN": source}, extend=True)
        return cs.write()
