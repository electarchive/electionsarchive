import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.wi_pri_2014 import PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.wi_asm_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "2010 fall general election: Assembly county-by-county canvass"
URL = (
    "https://web.archive.org/web/2016id_/http://gab.wi.gov/sites/default/files/"
    "2010%20Fall%20General%20-%20Assembly%20County-by-County.pdf"
)
DAY = date(2010, 11, 2)
DISTRICTS = {27, 36, 70, 73, 89}
HEADING = re.compile(r"\s*Assembly - District (\d+)\s*")
ROW = re.compile(r"\s*([A-Z][A-Z. ]*?|Office Totals:)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)+)\s*")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    rows = []
    district = None
    header: list[tuple[float, str]] = []
    parts: list[tuple[str, list[int]]] = []
    for line in text.splitlines():
        if found := HEADING.fullmatch(line):
            district, header, parts = int(found[1]), [], []
            continue
        if district not in DISTRICTS:
            continue
        row = ROW.fullmatch(line)
        if row is None:
            header.extend(
                (chunk.start() + len(chunk[0]) / 2, chunk[0])
                for chunk in re.finditer(r"\S+(?: \S+)*", line)
                if chunk[0] not in ("Total Votes Cast", "SCATTERING", "County", "DEM", "REP", "IND", "LIB", "WGR")
                and not parts
            )
            continue
        numbers = [int(value.replace(",", "")) for value in row[2].split()]
        names = [name for _, name in sorted(header)] + ["SCATTERING"]
        if len(numbers) != len(names) + 1 or sum(numbers[1:]) != numbers[0]:
            raise ValueError(f"assembly {district}: {line.strip()} against {names}")
        if row[1] != "Office Totals:":
            parts.append((counties[re.sub(r"[^a-z]", "", row[1].lower())], numbers[1:]))
            continue
        if [sum(values[k] for _, values in parts) for k in range(len(names))] != numbers[1:]:
            raise ValueError(f"assembly {district}: counties do not add up to {numbers}")
        rows.extend(
            (f"assembly:{district}:g", code, name, values[k], name == "SCATTERING")
            for code, values in parts
            for k, name in enumerate(names)
        )
        district = None
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
        },
        orient="row",
    )
    print(frame.group_by("chamber", "candidate", maintain_order=True).agg(pl.col("votes").sum(), pl.len()))
    if frame["chamber"].n_unique() != len(DISTRICTS):
        raise ValueError("a district is missing")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(DAY),
        year=pl.lit(DAY.year, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("WI"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WI"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's 2010 Assembly generals by county for five districts OpenElections lacks (Government "
            "Accountability Board canvass, via the Wayback Machine)",
            polity="US-WI",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elections.wi.gov/elections/election-results",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WI": source}, strict=True)
        return cs.write()
