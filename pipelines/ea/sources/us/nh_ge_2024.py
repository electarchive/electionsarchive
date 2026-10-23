import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nh_ge_2024"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-029" / "2024-ge-governor_3.xls"
URL = "https://www.sos.nh.gov/2024-general-election-results#2024-ge-governor_3.xls"
DAY = date(2024, 11, 5)
PUBLISHER = "New Hampshire Secretary of State"
TITLE = "2024 general election results, governor, by town"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            INBOX.read_bytes(),
            suffix=".xls",
            retrieved_at=datetime.fromtimestamp(INBOX.stat().st_mtime, UTC),
            media_type="application/vnd.ms-excel",
        )


def parse() -> None:
    path = raw.cached(SOURCE, URL).path
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
    summary = {
        row[0].strip().lower(): [int(cell) for cell in row[1:]]
        for row in pl.read_excel(path, sheet_name="summary gov", has_header=False, infer_schema_length=0).rows()[3:]
        if row[0] and row[1] and row[0].strip()
    }
    rows = []
    for county in sorted(codes):
        sheet = pl.read_excel(path, sheet_name=f"{county} gov", has_header=False, infer_schema_length=0).rows()
        names = [" ".join(cell.split()) for cell in sheet[2][1:]]
        towns = [row for row in sheet[3:] if row[0] and row[0].strip() and row[1]]
        total = next(row for row in towns if row[0].strip().upper() == "TOTALS")
        body = [[int(cell or 0) for cell in row[1:]] for row in towns if row[0].strip().upper() != "TOTALS"]
        printed = [int(cell) for cell in total[1:]]
        if [sum(column) for column in zip(*body, strict=True)] != printed or printed != summary[county]:
            raise ValueError(f"{county}: towns, county sheet, and summary disagree")
        for name, votes in zip(names, printed, strict=True):
            if name in {"Undervotes", "Overvotes"}:
                continue
            candidate = "WRITE-INS" if name == "Write-Ins" else re.sub(r",\s*[dlr]$", "", name).upper()
            rows.append((2024, "governor", DAY, False, "NH", None, codes[county], candidate, 1, name == "Write-Ins", votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NH")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2024 governor election by county, summed from the Secretary of State's town returns",
            polity="US-NH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.nh.gov/2024-general-election-results",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NH": source}, strict=True)
        return cs.write()
