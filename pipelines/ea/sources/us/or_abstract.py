import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.or_abstract"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://records.sos.state.or.us/ORSOSWebDrawer/Record/6937380/File/document"
DAY = date(1996, 11, 5)
PUBLISHER = "Oregon Secretary of State"
TITLE = "1996 General Election Official Results"
TABLES = {
    "United States President": (
        "president",
        None,
        ["Hagelin", "Hollis", "Dole", "Clinton", "Browne", "Phillips", "Perot", "Nader"],
    ),
    "United States Senator": (
        "senate",
        None,
        ["Hoyes", "Thompson", "Smith", "Kutcher", "Mohn", "Bruggere", "Phelps"],
    ),
    "1st Congressional District": ("house", 1, ["Furse", "Princ", "Johnson", "Witt"]),
    "2nd Congressional District": ("house", 2, ["Dugan", "Smith", "Wise"]),
    "3rd Congressional District": ("house", 3, ["Keating", "Knight", "Bruun", "Blumenauer", "Guillebeau"]),
    "4th Congressional District": ("house", 4, ["DeFazio", "Duemler", "Bonville", "Opus", "Nathan", "Newkirk"]),
    "5th Congressional District": ("house", 5, ["Hooley", "Duquesne", "Smith", "Bunn"]),
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows: list[dict[str, object]] = []
    table = None
    sums: list[int] = []
    for line in text.splitlines():
        if line.strip() in TABLES:
            table, sums = TABLES[line.strip()], []
            continue
        if table is None or not (row := re.fullmatch(r"([A-Z][A-Za-z ]+?)\s+((?:[\d,]+\s+)*[\d,]+)\s*", line)):
            continue
        chamber, district, names = table
        votes = [int(value.replace(",", "")) for value in row[2].split()]
        if len(votes) != len(names) + 1:
            raise ValueError(f"{chamber} {district} {row[1]}: {votes}")
        if row[1] == "TOTAL":
            if votes != [sum(column) for column in zip(*sums, strict=True)]:
                raise ValueError(f"{chamber} {district}: counties do not sum to the printed total")
            table = None
            continue
        sums.append(votes)
        rows.extend(
            {
                "chamber": chamber,
                "district": district,
                "code": codes[re.sub(r"[^a-z]", "", row[1].lower())],
                "candidate": name.upper(),
                "writein": name == "Write-ins",
                "votes": value,
            }
            for name, value in zip([*names, "Write-ins"], votes, strict=True)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("OR"),
            district=pl.col("district").cast(pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("OR"),
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
            summary="Oregon's 1996 president, U.S. Senate, and House general elections by county",
            polity="US-OR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, frame, {"OR": source}, strict=True)
        return cs.write()
