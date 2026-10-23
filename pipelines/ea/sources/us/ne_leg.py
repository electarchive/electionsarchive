import re
import subprocess
import unicodedata
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_house_generals import BASE, BOOKS, LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ne_leg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "General election canvass: Member of the Legislature"
YEARS = (2004, 2010)
UNITS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = {"twenty": 20, "thirty": 30, "forty": 40}
WORDS = (
    {word: k for k, word in enumerate(UNITS, 1)}
    | {word: k for k, word in enumerate(TEENS, 10)}
    | TENS
    | {f"{ten}-{unit}": value + k for ten, value in TENS.items() for k, unit in enumerate(UNITS, 1)}
)
DISTRICT = re.compile(r"\s*(?:Legislative )?District ([A-Za-z-]+|\d+)\W*")
ROW = re.compile(r"\s*([A-Z][A-Za-z ]+?)\*?\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*")


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, BASE + BOOKS[year][1], suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = []
    for year in YEARS:
        day, path = BOOKS[year]
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.splitlines()
        start = next(k for k, line in enumerate(lines) if re.fullmatch(r"\s*Member of the Legislature\s*", line))
        blocks: list[tuple[int, list[str], list[tuple[str, list[int]]], list[list[int]]]] = []
        for line in lines[start + 1 :]:
            if found := DISTRICT.fullmatch(line):
                word = found[1].lower()
                number = int(word) if word.isdigit() else WORDS.get(word)
                if number is None or (blocks and number <= blocks[-1][0]):
                    break
                blocks.append((number, [], [], []))
            elif not blocks:
                continue
            elif line.strip().startswith("County"):
                blocks[-1][1][:] = re.split(r"\s{2,}", line.strip())[1:]
            elif blocks[-1][1] and (found := ROW.fullmatch(line)):
                district, names, parts, total = blocks[-1]
                votes = [int(n.replace(",", "")) for n in found[2].split()]
                if len(votes) != len(names):
                    print(f"{year} legislature {district}: {found[1]} has {len(votes)} numbers for {len(names)} names")
                    names.clear()
                elif found[1] == "TOTAL":
                    total.append(votes)
                elif (key := re.sub(r"[^a-z]", "", found[1].lower())) not in counties:
                    break
                else:
                    parts.append((counties[key], votes))
        for district, names, parts, total in blocks:
            if not names or not parts or any(len(votes) != len(names) for _, votes in parts):
                continue
            sums = [sum(votes[k] for _, votes in parts) for k in range(len(names))]
            if total and sums != total[0]:
                print(f"{year} legislature {district}: {names} sum {sums} printed {total[0]}, skipped")
                continue
            rows.extend(
                (day, f"legislature:{district}:g", code, names[k], votes[k])
                for code, votes in parts
                for k in range(len(names))
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    )
    print(frame.group_by("day").agg(pl.col("chamber").n_unique()).sort("day"))
    frame.with_columns(
        candidate=pl.col("candidate").str.to_uppercase().str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("NE"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("NE"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's Legislature generals by county, 2004 and 2010 (Secretary of State canvass books)",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, BASE + BOOKS[year][1]), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NE": source}, strict=True)
        return cs.write()
