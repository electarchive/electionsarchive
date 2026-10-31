import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.or_gov_senate_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Oregon Secretary of State"
TITLE = "2002 general election statistical summary"
URL = "https://records.sos.state.or.us/ORSOSWebDrawer/Record/8411079/File/document"
SECTIONS = {
    "governor": ("MANNIX", ["MANNIX", "COX", "KULONGOSKI"], 4),
    "senate": ("MABON", ["MABON", "BRADBURY", "SMITH", "FITZGERALD"], 5),
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    lines = text.split("\n")
    rows = []
    for chamber, (marker, candidates, width) in SECTIONS.items():
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip().isupper()
            and line.strip() in ("GOVERNOR", "UNITED STATES SENATOR")
            and marker.lower() in " ".join(lines[index + 1 : index + 4]).lower()
        )
        counties = []
        total: list[int] = []
        for line in lines[start + 3 :]:
            row = re.match(r"\s*([A-Z][A-Za-z ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*){%d})\s*$" % (width - 1), line)
            if row is None:
                continue
            cells = [int(cell.replace(",", "")) for cell in row[2].split()][: len(candidates)]
            if row[1].strip() == "TOTAL":
                total = cells
                break
            counties.append((fips[re.sub(r"[^a-z]", "", row[1].lower())], cells))
        if len(counties) != 36:
            raise ValueError(f"{chamber}: {len(counties)} counties")
        for column, candidate in enumerate(candidates):
            if sum(cells[column] for _, cells in counties) != total[column]:
                raise ValueError(f"{chamber} {candidate}: counties do not add up to {total[column]}")
            rows.extend((chamber, code, candidate, cells[column]) for code, cells in counties)
    frame = pl.DataFrame(rows, schema=["chamber", "code", "candidate", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(date(2002, 11, 5)),
        year=pl.lit(2002, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("OR"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("OR"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's 2002 governor and U.S. Senate generals by county from the Secretary of State's statistical summary",
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
                url="https://sos.oregon.gov/elections/pages/historical-data.aspx",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OR": source}, strict=True, extend=True)
        return cs.write()
