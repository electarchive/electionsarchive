import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_senate_2002_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alabama Secretary of State"
TITLE = "General election results, county level"
SITE = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/"
FILES = {date(2002, 11, 5): "2002GeneralElectionTotals.XLS", date(2004, 11, 2): "2004ge-countylevel_0.xls"}
CANDIDATES = {
    date(2002, 11, 5): ["PARKER", "ALLEN", "SESSIONS", "WRITE-INS"],
    date(2004, 11, 2): ["SOWELL", "SHELBY", "WRITE-INS"],
}


def fetch() -> None:
    for name in FILES.values():
        raw.fetch(SOURCE, SITE + name, suffix=Path(name).suffix.lower())


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    rows = []
    for day, name in FILES.items():
        sheet = pl.read_excel(
            raw.cached(SOURCE, SITE + name).path,
            has_header=False,
            sheet_name="USsenate" if day.year == 2002 else "Statewide Offices",
        )
        candidates = CANDIDATES[day]
        first = 1 if day.year == 2002 else 8
        found = []
        total: list[int] = []
        for row in sheet.iter_rows():
            label = (row[0] if day.year == 2002 else row[1]) or ""
            cells = row[first : first + len(candidates)]
            if label.strip().upper() in ("STATE TOTAL", "TOTAL"):
                total = [int(cell) for cell in cells]
            elif re.sub(r"[^a-z]", "", label.lower()) in counties and all(str(cell).strip().isdigit() for cell in cells):
                found.append((counties[re.sub(r"[^a-z]", "", label.lower())], [int(cell) for cell in cells]))
        if len(found) != 67:
            raise ValueError(f"{day}: {len(found)} counties")
        for column, candidate in enumerate(candidates):
            if sum(cells[column] for _, cells in found) != total[column]:
                raise ValueError(f"{day} {candidate}: counties do not add up to {total[column]}")
            rows.extend((day, code, candidate, cells[column]) for code, cells in found)
    frame = pl.DataFrame(rows, schema=["day", "code", "candidate", "votes"], orient="row")
    with pl.Config(tbl_rows=20):
        print(frame.group_by("day", "candidate").agg(pl.col("votes").sum()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        chamber=pl.lit("senate"),
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("AL"),
        writein=pl.col("candidate") == "WRITE-INS",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("AL"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2002 and 2004 U.S. Senate generals by county from the Secretary of State's county-level "
            "results",
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
                url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
                license="Public record",
            )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True, extend=True)
        return cs.write()
