import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ky_pres_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PDF = "https://elect.ky.gov/results/2020-2029/Documents/2020%20Primary%20Election%20Results.pdf"
DAY = date(2020, 6, 23)
COLUMNS = {
    "republican": ["Donald J. Trump", "Uncommitted"],
    "democratic": [
        "Joseph R. Biden",
        "Bernie Sanders",
        "Uncommitted",
        "Elizabeth Warren",
        "Pete Buttigieg",
        "Andrew Yang",
        "Tulsi Gabbard",
        "Amy Klobuchar",
        "Tom Steyer",
        "Michael Bennet",
        "Deval Patrick",
    ],
}
PUBLISHER = "Kentucky Secretary of State"
TITLE = "2020 Primary Election Results"


def fetch() -> None:
    raw.fetch(SOURCE, PDF, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    with connect() as conn:
        counties = {
            name.removesuffix(", KY").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '21%%' and u.valid @> '2020-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for party, names in COLUMNS.items():
        heads = [m.start() for m in re.finditer(r"President of the United States\s+" + party.title() + " Party", text)]
        section = text[heads[0] : text.index("Total Votes", heads[0])]
        seen = set()
        for line in section.splitlines():
            found = re.fullmatch(r"\s*([A-Za-z][A-Za-z .']*?)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*", line)
            if found is None or found[1].lower() not in counties:
                continue
            votes = found[2].split()
            if len(votes) != len(names):
                raise ValueError(f"{party} {found[1]}: {len(votes)} values for {len(names)} candidates")
            seen.add(found[1])
            for candidate, value in zip(names, votes, strict=True):
                rows.append((party, counties[found[1].lower()], candidate, int(value.replace(",", ""))))
        if len(seen) != 120:
            raise ValueError(f"{party}: {len(seen)} counties")
    frame = pl.DataFrame(
        rows, schema={"party": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("party", "candidate").agg(pl.col("votes").sum()).sort("party", "candidate").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2020 Democratic and Republican presidential primaries by county from the Secretary of State's official results",
            polity="US-KY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elect.ky.gov/results/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PDF), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(DAY.year, pl.Int32),
                chamber=pl.lit("president:") + pl.col("party"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("KY"),
                district=pl.lit(None, pl.Int16),
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("KY"),
            ),
            {"KY": source},
            strict=True,
        )
        return cs.write()
