import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_primaries import COUNTIES
from ea.store import DATA, connect

SOURCE = "us.ne_pri_2012"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://sos.nebraska.gov/sites/default/files/doc/elections/2012/canvass-report-2012-primary.pdf"
DAY = date(2012, 5, 15)
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Primary election canvass"
CELL = re.compile(
    r"(?<![A-Za-z])(TOTAL|"
    + "|".join(sorted(map(re.escape, COUNTIES), key=len, reverse=True))
    + r")(?![A-Za-z])((?:\s+\d[\d,]*)+)"
)
DISTRICTS = {"One": 1, "Two": 2, "Three": 3}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if i > 150 and re.match(r"\s+President of the United States\s*$", line))
    end = next(i for i in range(start, len(lines)) if re.match(r"\s+Member of the U\.S\. House of Representatives Totals", lines[i]))
    chamber, party, district = "", "", None
    names: list[str] = []
    tallies: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for line in lines[start:end]:
        if re.match(r"\s+President of the United States\s*$", line):
            chamber, district = "president", None
        elif re.match(r"\s+Member of the United States Senate", line):
            chamber, district = "senate", None
        elif re.match(r"\s+Member of the U\.S\. House of Representatives\s*$", line):
            chamber, district = "house", None
        elif found := re.match(r"\s+(Republican|Democratic|Libertarian) Party Nomination", line):
            party, names = found[1].lower(), []
            if chamber == "house":
                district = None
        elif found := re.search(r"\bDistrict (One|Two|Three)\s*$", line):
            district, names = DISTRICTS[found[1]], []
        elif line.lstrip().startswith("County"):
            cells = list(CELL.finditer(line))
            head = line[: cells[0].start()] if cells else line
            names = [n for n in re.split(r"\s{2,}", head.strip()[len("County") :].strip()) if n]
            for cell in cells:
                tallies_row(tallies, chamber, district, party, names, cell, codes)
        elif names:
            for cell in CELL.finditer(line):
                tallies_row(tallies, chamber, district, party, names, cell, codes)
    rows = []
    for (chamber_, district_, party_), by_name in tallies.items():
        for name, by_county in by_name.items():
            for code, votes in by_county.items():
                rows.append((2012, f"{chamber_}:{party_}", DAY, False, "NE", district_, code, name.upper(), 1, False, votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
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
        )
        .with_columns(kind=pl.lit("county"), source=pl.lit("NE"))
        .write_parquet(PARSED / "counties.parquet")
    )
    print(len(rows), "candidate rows")


def tallies_row(tallies: defaultdict, chamber, district, party, names, cell, codes) -> None:
    if cell[1] == "TOTAL":
        return
    numbers = [int(v.replace(",", "")) for v in cell[2].split()]
    if len(numbers) != len(names):
        print(f"{chamber} {district} {party} {cell[1]}: {len(numbers)} numbers for {names}")
        return
    code = codes[re.sub(r"[^a-z]", "", cell[1].lower())]
    for name, value in zip(names, numbers, strict=True):
        tallies[(chamber, district, party)][name][code] += value


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2012 federal primaries by county",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.nebraska.gov/elections/election-results",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NE": source}, strict=True)
        return cs.write()
