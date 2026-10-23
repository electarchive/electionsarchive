import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.nj_sos import COUNTIES
from ea.store import DATA, connect

SOURCE = "us.nj_primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
FILES = {
    (date(2000, 6, 6), "president"): "2000/2000-primary-elect-president-candidate-tallies.pdf",
    (date(2000, 6, 6), "house"): "2000/2000-primary-elect-house-candidate-tallies.pdf",
    (date(2002, 6, 4), "senate"): "2002/2002pri-elect-senate-candidate-tally.pdf",
    (date(2002, 6, 4), "house"): "2002/2002pri-elect-us-house-candidate_tally.pdf",
    (date(2004, 6, 8), "president"): "2004/2004pri-elect_pres-candidate_tally.pdf",
    (date(2004, 6, 8), "house"): "2004/2004pri-elect_house-candidate_tally.pdf",
    (date(2006, 6, 6), "senate"): "2006/2006-official-primary-election-senate-tallies.pdf",
    (date(2006, 6, 6), "house"): "2006/2006-official-primary-election-house-tallies.pdf",
    (date(2008, 6, 3), "senate"): "2008/2008-official-pri-elect-senate-candidate-tallies-071108.pdf",
    (date(2008, 6, 3), "house"): "2008/2008-official-pri-elect-house-candidate-tallies-071108.pdf",
    (date(2008, 2, 5), "president"): "2008/2008-presidential-official-primary-results-032808.pdf",
    (date(2020, 7, 7), "president"): "2020/2020-official-primary-results-us-president-amended-0826.pdf",
    (date(2020, 7, 7), "senate"): "2020/2020-official-primary-results-us-senate-amended-0826.pdf",
    (date(2020, 7, 7), "house"): "2020/2020-official-primary-results-us-house-amended-0826.pdf",
    (date(2022, 6, 7), "house"): "2022/2022-official-primary-results-us-house.pdf",
}
ORDINALS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
]
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official election results: primaries"
COUNTY = re.compile(r"(" + "|".join(COUNTIES) + r")(?: \(part\))?\s+(?:.*?\s)?([\d,]+)\s*$", re.IGNORECASE)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '34%%'"""
            ).fetchall()
        }
    rows: list[tuple[date, str, int | None, str, str, int]] = []
    for (day, kind), path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        columns: tuple[int, int, int] | None = None
        district: int | None = None
        candidate = party = ""
        votes: dict[str, int] = {}
        for line in text.splitlines():
            found = re.search(r"(\w+) Congressional District", line)
            if found:
                district = ORDINALS.index(found[1].lower()) + 1
            if re.match(r"\s*Name\b", line) and "County" in line:
                party_at = line.find("Designation") if "Designation" in line else line.find("Party")
                columns = (party_at, line.find("County"), line.find("Address") if "Address" in line and day.year >= 2020 else party_at)
                continue
            if columns is None:
                continue
            party_column, county_column, name_column = columns
            total = re.match(r"\s*Total\s+([\d,]+)\s*$", line)
            if total:
                if candidate and sum(votes.values()) != int(total[1].replace(",", "")):
                    raise ValueError(f"{path}: {candidate}'s counties don't sum to the total: {votes} vs {total[1]}")
                if candidate:
                    rows.extend(
                        (
                            day,
                            f"{kind}:{party.lower()}",
                            district if kind == "house" else None,
                            candidate,
                            county,
                            count,
                        )
                        for county, count in votes.items()
                    )
                candidate, votes = "", {}
                continue
            name = line[:name_column].strip()
            label = line[party_column:county_column].strip()
            if name_column != party_column:
                tail = re.search(r"\b(Democratic|Republican)\s*$", line)
                label = tail[1] if tail else ""
            if name and line[:1] != " " and label in ("Democratic", "Republican"):
                candidate = re.sub(r"\s*\((?:w|W)\)|\s*\*", "", name).strip().upper()
                party = label
            if candidate:
                match = COUNTY.search(line[county_column:])
                if match:
                    votes[fips[re.sub(r"[^a-z]", "", match[1].lower())]] = votes.get(
                        fips[re.sub(r"[^a-z]", "", match[1].lower())], 0
                    ) + int(match[2].replace(",", ""))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("NJ"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("NJ"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's 2000–2008 House, Senate, and presidential primaries by county",
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
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").filter(pl.col("year") < 2020),
            {"NJ": source},
            strict=True,
            partial="the Division of Elections posted these county tallies before certification, and the certified totals are higher",
        )
        return cs.write()
