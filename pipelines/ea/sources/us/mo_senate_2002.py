import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mo_senate_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
TITLE = "Official election returns, general election of November 5, 2002, by county"
URL = "https://web.archive.org/web/2015id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/CountyGeneral2002.pdf"
CANDIDATES = {
    "Carnahan, Jean": "CARNAHAN",
    "Talent, Jim": "TALENT",
    "Millay, Tamara A.": "MILLAY",
    "Romano, Daniel (digger)": "ROMANO",
}
TOTALS = {"CARNAHAN": 913778, "TALENT": 935032}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%%' and u.code <> '29510'"""
            ).fetchall()
        }
    counties["stlouiscity"] = "29510"
    counties["stlouiscounty"] = counties["stlouis"]
    counties["kansascity"] = counties["jackson"]
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout.split("\n")
    tallies: dict[tuple[str, str], int] = {}
    seen = set()
    unit = ""
    for index, line in enumerate(lines):
        if re.fullmatch(r"\s{3,8}[A-Z][A-Z .'-]+", line) and any(
            text.strip().startswith("Office Candidate") for text in lines[index + 1 : index + 7]
        ):
            unit = line.strip()
            seen.add(unit)
        if line.strip() != "U.S. Senator":
            continue
        position = index + 1
        while not lines[position].strip().startswith("Total Votes"):
            row = re.fullmatch(r"\s+(.+?)\s{2,}[A-Z]+\s+([\d,]+)\s+[\d.]+%", lines[position])
            if row is not None and row[1] in CANDIDATES:
                key = (counties[re.sub(r"[^a-z]", "", unit.lower())], CANDIDATES[row[1]])
                tallies[key] = tallies.get(key, 0) + int(row[2].replace(",", ""))
            position += 1
    frame = pl.DataFrame(
        [(code, name, votes) for (code, name), votes in tallies.items()],
        schema=["code", "candidate", "votes"],
        orient="row",
    )
    print(len(seen), frame["code"].n_unique())
    sums = dict(frame.group_by("candidate").agg(pl.col("votes").sum()).iter_rows())
    for name, expected in TOTALS.items():
        if sums[name] != expected:
            raise ValueError(f"{name}: counties add up to {sums[name]}, not {expected}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(date(2002, 11, 5)),
        chamber=pl.lit("senate"),
        year=pl.lit(2002, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(True),
        state=pl.lit("MO"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MO"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's 2002 U.S. Senate special general by county from the Secretary of State's official "
            "election returns",
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
                url="https://www.sos.mo.gov/elections/results",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MO": source}, strict=True, extend=True)
        return cs.write()
