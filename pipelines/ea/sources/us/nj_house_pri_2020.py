import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nj_house_pri_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PDF = "https://www.nj.gov/state/elections/assets/pdf/election-results/2020/2020-official-primary-results-us-house.pdf"
DAY = date(2020, 7, 7)
ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth", "Eleventh", "Twelfth"]
WANTED = {(2, "republican"), (4, "democratic"), (8, "democratic")}
PUBLISHER = "New Jersey Division of Elections"
TITLE = "2020 Primary Election Official Results: Candidates for House of Representatives"


def fetch() -> None:
    raw.fetch(SOURCE, PDF, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    with connect() as conn:
        counties = {
            name.removesuffix(", NJ").removesuffix(" County").upper(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '34%%' and u.valid @> '2020-01-01'::date"""
            ).fetchall()
        }
    order = sorted(counties, key=len, reverse=True)
    district = candidate = party = None
    rows = []
    for line in text.splitlines():
        found = re.match(r"(\w+) Congressional District:", line)
        if found:
            district = ORDINALS.index(found[1]) + 1
            candidate = party = None
            continue
        if district is None or not line.strip():
            continue
        if re.match(r"\S", line) and not re.match(r"(\d\d/\d\d/\d{4}|Candidates for|For PRIMARY|Name\s|[A-Z][A-Z ]+\(part\)|.*Counties$)", line):
            candidate = re.sub(r"\s+P\.O\. BOX.*$", "", re.split(r"\s+\(w\)|\s{2,}|\s\*", line.strip())[0].strip())
            party = None
        if candidate is not None and party is None:
            match = re.search(r"\b(Democratic|Republican|Libertarian|Green|Independent|Socialist\w*|[A-Z][a-z]+ [A-Z][a-z]+)\s*$", line)
            if match and re.match(r"\S", line) or re.search(r"^\s*(Democratic|Republican)\s*$", line):
                party = (match[1] if match else line.strip()).lower()
        county = re.match(r"\s{20,}(" + "|".join(order) + r")\b.*?([\d,]+)\s*$", line)
        if county and candidate and party and not re.search(r", NJ \d{5}\s*$", line):
            rows.append((district, party, candidate, counties[county[1]], int(county[2].replace(",", ""))))
    frame = pl.DataFrame(
        rows,
        schema={"district": pl.Int16, "party": pl.String, "candidate": pl.String, "code": pl.String, "votes": pl.Int64},
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("district", "party", "candidate").agg(pl.col("votes").sum()).sort("district", "party").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's 2020 Democratic and Republican U.S. House primaries in the 2nd, 4th, and 8th districts by county from the Division of Elections",
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
        cs.artifact(raw.cached(SOURCE, PDF), source)
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            pl.concat_str(pl.col("district").cast(pl.String), pl.col("party"), separator=":").is_in(
                [f"{d}:{p}" for d, p in WANTED]
            )
        )
        medsl.attach(
            cs,
            conn,
            frame.select(
                year=pl.lit(2020, pl.Int32),
                chamber=pl.lit("house:") + pl.col("party"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("NJ"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("NJ"),
            ),
            {"NJ": source},
            strict=True,
        )
        return cs.write()
