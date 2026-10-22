import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.az_gen98"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "https://web.archive.org/web/20130920231217id_/http://azsos.gov/election/1998/General/Canvass1998GE.pdf"
PUBLISHER = "Arizona Secretary of State"
TITLE = "State of Arizona Official Canvass, 1998 General Election - November 3, 1998"
DAY = date(1998, 11, 3)


def fetch() -> None:
    raw.fetch(SOURCE, FILE, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '04%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILE).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    lines = text.splitlines()
    header = next(line for line in lines if line.strip().startswith("$SDFKH"))
    names = ["".join(chr(ord(char) + 29) for char in token) for token in header.split()]
    keys = [counties[re.sub(r"[^a-z]", "", name.lower())] for name in names[:-1]]
    if names[-1] != "TOTAL":
        raise ValueError(names)
    end = next(i for i, line in enumerate(lines) if line.startswith("STATE SENATOR - DISTRICT NO. 1"))
    chamber = None
    district = None
    rows: list[dict[str, object]] = []
    for line in lines[:end]:
        if line.startswith("U.S. SENATOR"):
            chamber, district = "senate", None
        elif line.startswith("GOVERNOR"):
            chamber, district = "governor", None
        elif (match := re.match(r"U\.S\. REPRESENTATIVE IN CONGRESS - DISTRICT NO\. (\d+)", line)) is not None:
            chamber, district = "house", int(match.group(1))
        elif chamber is not None and (row := re.match(r"\s*\(([A-Z]+)\) (.+?)\s{2,}(\S.*)$", line)) is not None:
            candidate = row.group(2).replace(" *", "").strip()
            writein = candidate.endswith("(Write-In)")
            candidate = candidate.removesuffix("(Write-In)").strip().replace("’", "'")
            candidate = re.sub(r'\s*"[^"]*"', "", candidate)
            values = row.group(3).split()
            if len(values) != len(names):
                raise ValueError(line)
            cells = [None if value == "---" else int(value.replace(",", "")) for value in values]
            present = [value for value in cells[:-1] if value is not None]
            if sum(present) != cells[-1]:
                raise ValueError(f"{line.strip()[:60]}: {sum(present)} != {cells[-1]}")
            for key, votes in zip(keys, cells[:-1], strict=True):
                if votes is not None:
                    rows.append(
                        {
                            "chamber": chamber,
                            "district": district,
                            "code": key,
                            "candidate": candidate.upper(),
                            "writein": writein,
                            "votes": votes,
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, schema_overrides={"district": pl.Int16})
        .with_columns(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("AZ"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("AZ"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona's 1998 Senate, governor, and House general elections by county",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://azsos.gov/elections/results-data/election-archive",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, FILE), source)
        medsl.attach(cs, conn, frame, {"AZ": source}, strict=True)
        return cs.write()
