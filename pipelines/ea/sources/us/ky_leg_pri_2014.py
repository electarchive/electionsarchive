import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ky_leg_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kentucky Secretary of State"
TITLE = "Official 2014 primary election results"
URL = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2010-2019/2014/2014%20Primary%20Election%20Results.pdf"
DAY = date(2014, 5, 20)
DISTRICT = re.compile(r"(\d+)\w\w (Senatorial|Representative) District")
PARTY = re.compile(r"(Democratic|Republican) Party")
ROW = re.compile(r"\s+([A-Z][A-Za-z .]+?)\s{2,}([\d,]+(?:\s+[\d,]+)*)")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("Commonwealth")]
    rows = []
    index = 0
    while index < len(lines):
        found = DISTRICT.fullmatch(lines[index].strip())
        party = PARTY.fullmatch(lines[index + 1].strip()) if found else None
        if not party:
            index += 1
            continue
        chamber = f"{'senate' if found[2] == 'Senatorial' else 'house'}:{int(found[1])}:{party[1][0].lower()}p"
        given = [(m.start() + len(m[0]) / 2, m[0]) for m in re.finditer(r"\S+(?: \S+)*", lines[index + 2])]
        surnames = [(m.start() + len(m[0]) / 2, m[0]) for m in re.finditer(r"\S+(?: \S+)*", lines[index + 3])]
        names = [[] for _ in surnames]
        for center, chunk in given:
            names[min(range(len(surnames)), key=lambda k: abs(surnames[k][0] - center))].append(chunk)
        candidates = [
            re.sub(r",?\s+(JR|SR|II|III|IV)\.?$", "", " ".join([*first, surname]).upper())
            for first, (_, surname) in zip(names, surnames, strict=True)
        ]
        index += 4
        parts, sums = [], [0] * len(candidates)
        while index < len(lines) and (row := ROW.fullmatch(lines[index])):
            votes = [int(number.replace(",", "")) for number in row[2].split()]
            if row[1] == "Total Votes":
                if votes != sums:
                    print(f"{chamber}: counties sum to {sums}, printed {votes}, skipped")
                    parts = []
                break
            parts.append((counties[re.sub(r"[^a-z]", "", row[1].lower())], votes))
            sums = [a + b for a, b in zip(sums, votes, strict=True)]
            index += 1
        rows.extend(
            (chamber, code, candidate, count)
            for code, votes in parts
            for candidate, count in zip(candidates, votes, strict=True)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    print(frame.select(pl.col("chamber").n_unique(), pl.col("code").n_unique()))
    frame.with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("KY"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("KY"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2014 State House and Senate primaries by county from the Secretary of State's official results",
            polity="US-KY",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elect.ky.gov/results/Pages/default.aspx",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"KY": source}, strict=True)
        return cs.write()
