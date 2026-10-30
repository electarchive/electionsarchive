import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.ga_1992_president"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-391" / "ga" / "november_1992_generalelection_totals.pdf"
URL = "https://sos.ga.gov/elections/election_results/1992/pres.htm"
DAY = "1992-11-03"
PUBLISHER = "Georgia Secretary of State"
TITLE = "Georgia Election Results, 1992 General Election: President"
METHOD = (
    "County totals are from the Secretary of State's 1992 presidential results page, "
    "each candidate's county sum equal to the printed state total."
)


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".pdf",
        retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
        media_type="application/pdf",
    )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == "President")
    end = next(i for i in range(start, len(lines)) if "1992 PSC Runoff" in lines[i])
    section = lines[start:end]
    printed = next(
        [int(cell.replace(",", "")) for cell in line.split()]
        for line in section
        if re.fullmatch(r"\s*\d[\d,]{4,}(\s+\d[\d,]*){3}\s*", line)
    )
    rows: dict[str, list[int]] = {}
    for line in section:
        match = re.match(r"^\s{1,4}([A-Za-z][A-Za-z.' ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*){3})\s*$", line)
        if match is not None:
            rows[match[1]] = [int(cell.replace(",", "")) for cell in match[2].split()]
    unknown = {name for name in rows if re.sub(r"[^a-z]", "", name.lower()) not in codes}
    if unknown or len(rows) != 159:
        raise ValueError(f"{len(rows)} counties; unmatched {unknown}")
    for column, total in enumerate(printed):
        if sum(cells[column] for cells in rows.values()) != total:
            raise ValueError(f"column {column}: counties sum {sum(c[column] for c in rows.values())}, printed {total}")
    frame = pl.DataFrame(
        [
            (DAY, "president", codes[re.sub(r"[^a-z]", "", name.lower())], str(column), cells[column])
            for name, cells in rows.items()
            for column in range(4)
        ],
        schema=["day", "district", "county", "column", "votes"],
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("column").agg(pl.col("votes").sum()).sort("column"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Georgia's 1992 presidential general election by county from the Secretary of State's results page",
        PUBLISHER,
        TITLE,
        URL,
        "GA",
        "13",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: METHOD,
        statewide=True,
    )
