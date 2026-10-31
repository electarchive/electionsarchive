import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wy_gov_senate"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wyoming Secretary of State"
TITLE = "Statewide candidates' abstract of official general election results"
SITE = "https://sos.wyo.gov/Elections/Docs/"
FILES = {
    date(2002, 11, 5): "2002/02Results/02General/02_General%20Election_SW_Candidates.pdf",
    date(2006, 11, 7): "2006/06Results/06General/SW_Candidates_Summary.pdf",
}
CONTESTS = {
    date(2002, 11, 5): {"senate": {"Corcoran", "Enzi"}, "governor": {"Freudenthal", "Dawson", "Bebout"}},
    date(2006, 11, 7): {"senate": {"Groutage", "Thomas"}},
}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '56%%'"""
            ).fetchall()
        }
    rows = []
    for day, path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        names: list[str] = []
        counties: list[tuple[str, list[int]]] = []
        for line in text.splitlines():
            if found := re.findall(r"([A-Z][A-Za-z.' ]*?[a-z]) - [A-Z]\b", line):
                names, counties = [name.split()[-1] for name in found], []
                continue
            row = re.fullmatch(r"([A-Z][A-Za-z ]+?)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*", line)
            if row is None or not names:
                continue
            cells = [int(cell.replace(",", "")) for cell in row[2].split()]
            cells = cells[-len(names) :]
            if row[1].strip() in ("Official Totals", "Total"):
                for column, name in enumerate(names):
                    for chamber, wanted in CONTESTS[day].items():
                        if name in wanted:
                            if sum(votes[column] for _, votes in counties) != cells[column]:
                                raise ValueError(f"{day} {name}: counties do not add up to {cells[column]}")
                            rows.extend(
                                (day, chamber, fips[re.sub(r"[^a-z]", "", county.lower())], name.upper(), votes[column])
                                for county, votes in counties
                            )
            else:
                counties.append((row[1].strip(), cells))
    frame = pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row")
    with pl.Config(tbl_rows=40):
        print(
            frame.group_by("day", "chamber", "candidate")
            .agg(pl.col("votes").sum(), pl.len())
            .sort("day", "chamber", "votes")
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("WY"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WY"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wyoming's 2002 governor and U.S. Senate and 2006 U.S. Senate generals by county from the Secretary "
            "of State's statewide candidates' abstracts",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://sos.wyo.gov/Elections/State/ElectionResults.aspx",
                license="Public record",
            )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WY": source}, strict=True, extend=True)
        return cs.write()
