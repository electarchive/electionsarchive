import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wy_leg_2006"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wyoming Secretary of State"
TITLE = "2006 general election: official precinct-by-precinct summaries"
SITE = "https://sos.wyo.gov/Elections/Docs/2006/06Results/06General/"
COUNTIES = [
    "AL",
    "BH",
    "CA",
    "CB",
    "CK",
    "CV",
    "FR",
    "GO",
    "HT",
    "JO",
    "LA",
    "LI",
    "NA",
    "NI",
    "PK",
    "PL",
    "SB",
    "SN",
    "SW",
    "TE",
    "UI",
    "WA",
    "WE",
]
DAY = date(2006, 11, 7)
OFFICE = re.compile(r"(House|Senate) District (\d+)")


def url(county: str) -> str:
    return f"{SITE}{county}_PbP_Candidate_Summary.pdf"


def fetch() -> None:
    for county in COUNTIES:
        raw.fetch(SOURCE, url(county), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '56%%'"""
            ).fetchall()
        }
    rows = []
    for county in COUNTIES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(county)).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        name = re.search(r"^\s*(.+?) County Official", text, re.MULTILINE)[1]
        code = codes[re.sub(r"[^a-z]", "", name.lower())]
        offices: list[tuple[float, str]] = []
        candidates: list[tuple[float, str, str]] = []
        for line in text.splitlines():
            chunks = [(found.start(), found[0]) for found in re.finditer(r"\S+(?: \S+)*", line)]
            if any(OFFICE.fullmatch(chunk) for _, chunk in chunks):
                offices = [(start + len(chunk) / 2, chunk) for start, chunk in chunks]
                candidates = []
            elif offices and chunks and all(re.search(r" - \w+$", chunk) for _, chunk in chunks):
                for start, chunk in chunks:
                    center = start + len(chunk) / 2
                    office = min(offices, key=lambda pair: abs(pair[0] - center))[1]
                    candidates.append((center, office, re.sub(r" - \w+$", "", chunk)))
            elif candidates and line.startswith("Total"):
                votes = [int(number) for number in re.findall(r"\d+", line)]
                if len(votes) != len(candidates):
                    print(f"{name}: {len(candidates)} candidates, {len(votes)} totals, skipped")
                else:
                    for (_, office, candidate), count in zip(candidates, votes, strict=True):
                        if found := OFFICE.fullmatch(office):
                            rows.append((f"{found[1].lower()}:{int(found[2])}:g", code, candidate.upper(), count))
                offices, candidates = [], []
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    ).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("WY"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("WY"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wyoming's 2006 legislative generals by county from the Secretary of State's precinct-by-precinct summaries",
            polity="US-WY",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE,
                license="Public record",
            )
        for county in COUNTIES:
            cs.artifact(raw.cached(SOURCE, url(county)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WY": source}, strict=True)
        return cs.write()
