import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nc_primaries_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "https://web.archive.org/web/2005id_/http://www.app.sboe.state.nc.us/NCSBE/Elec/Results/y2000elect/may2/congress.xls"
DAY = date(2000, 5, 2)
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Official results for the May 2, 2000 primary: U.S. House"
PARTIES = {"(D)": "democratic", "(R)": "republican"}


def fetch() -> None:
    raw.fetch(SOURCE, FILE, suffix=".xls")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    rows = []
    for sheet_id in range(1, 7):
        sheet = pl.read_excel(
            raw.cached(SOURCE, FILE).path,
            engine="calamine",
            has_header=False,
            sheet_id=sheet_id,
            infer_schema_length=0,
        )
        lines = sheet.rows()
        district = (1, 2, 4, 7, 9, 12)[sheet_id - 1]
        names = [cell.strip() for cell in lines[0][2:] if cell]
        parties = [PARTIES[cell.strip()] for cell in lines[1][2:] if cell]
        width = len(names)
        county = None
        subtotal: list[int] = []
        precincts = [0] * width
        sums = [0] * width
        printed: list[int] | None = None
        for line in lines[2:]:
            values = [int(cell.replace(",", "")) if cell and cell.strip() else 0 for cell in line[2 : 2 + width]]
            if (line[0] or line[1] or "").strip().lower() == "total":
                printed = values
                continue
            if (line[0] or "").strip():
                if county is not None and subtotal != precincts and any(precincts):
                    print(f"{district} {county}: precincts sum to {precincts}, county row {subtotal}")
                key = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", line[0]).lower())
                if key not in counties:
                    raise ValueError(f"unmatched county {line[0]}")
                county, subtotal, precincts = counties[key], values, [0] * width
                sums = [a + b for a, b in zip(sums, values, strict=True)]
                for name, party, count in zip(names, parties, values, strict=True):
                    rows.append((district, party, name, county, count))
            else:
                precincts = [a + b for a, b in zip(precincts, values, strict=True)]
        if subtotal != precincts and any(precincts):
            print(f"{district} {county}: precincts sum to {precincts}, county row {subtotal}")
        if printed != sums:
            raise ValueError(f"{district}: counties sum to {sums}, printed total {printed}")
    frame = pl.DataFrame(rows, schema=["district", "party", "candidate", "code", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.lit(2000, pl.Int32),
        chamber=pl.concat_str(pl.lit("house:"), pl.col("party")),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("NC"),
        district=pl.col("district").cast(pl.Int16),
        code=pl.col("code"),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("NC"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's May 2, 2000 U.S. House primaries in districts 1, 2, 4, 7, 9, and 12 by county from the State Board of Elections",
            polity="US-NC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, FILE), source)
        medsl.attach(cs, conn, frame, {"NC": source}, strict=True)
        return cs.write()
