import re
import unicodedata
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/"
FILES = {
    "democratic": (f"{BASE}d-certresults-06-21-02.xls", 2),
    "republican": (f"{BASE}r-certresults-6-10-02.xls", 0),
}
DAY = date(2002, 6, 4)
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election data: 2002 primary elections"


def fetch() -> None:
    for url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xls")


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '01%%' order by u.code"""
        ).fetchall():
            fips.setdefault(re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()), code)
    rows: list[tuple[str, str, int | None, str, int]] = []
    for party, (url, sheet) in FILES.items():
        table = (
            fastexcel.read_excel(raw.cached(SOURCE, url).path).load_sheet(sheet, header_row=None).to_polars().rows()
        )
        office_row, name_row = (0, 1) if party == "democratic" else (1, 2)
        start = 3 if party == "democratic" else 3
        stop = next(index for index, row in enumerate(table) if (row[0] or "").strip() == "Totals")
        office = ""
        for column in range(1, len(table[0])):
            office = (table[office_row][column] or office).strip()
            candidate = (table[name_row][column] or "").strip()
            found = re.fullmatch(r"(?:US Rep District |U\.S\. Congress \()(\d+)\)?", office)
            if found:
                chamber, district = f"house:{party}", int(found[1])
            elif office == "United States Senator":
                chamber, district = f"senate:{party}", None
            else:
                continue
            if not candidate:
                continue
            for row in table[start:stop]:
                county = re.sub(r"[^a-z]", "", (row[0] or "").lower())
                if county:
                    rows.append((chamber, fips[county], district, candidate.upper(), int(float(row[column] or 0))))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "chamber": pl.String,
                "code": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.lit(2002, dtype=pl.Int32),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("AL"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2002 House and Senate primaries by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        for url, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True)
        return cs.write()
