import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wi_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wisconsin Government Accountability Board"
TITLE = "2014 fall partisan primary: county by county results, all offices"
URL = (
    "https://web.archive.org/web/20140905001314id_/http://gab.wi.gov/sites/default/files/"
    "All%20Offices%20-%20County%20by%20County%20Results%20%288.12.14%29.xls"
)
DAY = date(2014, 8, 12)
OFFICE = re.compile(r"(STATE SENATE|ASSEMBLY) - DISTRICT (\d+) - (DEMOCRATIC|REPUBLICAN)")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xls")


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
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", folder, str(raw.cached(SOURCE, URL).path)],
            capture_output=True,
            check=True,
        )
        book = pl.read_excel(
            next(Path(folder).glob("*.xlsx")), sheet_id=0, engine="calamine", has_header=False, infer_schema_length=0
        )
    rows = []
    for sheet in book.values():
        cells = [[str(cell).strip() if cell is not None else "" for cell in row] for row in sheet.rows()]
        found = next((OFFICE.fullmatch(row[0]) for row in cells if OFFICE.fullmatch(row[0])), None)
        header = next((index for index, row in enumerate(cells) if row[0] == "County"), None)
        if found is None or header is None:
            continue
        chamber = f"{'senate' if found[1] == 'STATE SENATE' else 'assembly'}:{int(found[2])}:{found[3][0].lower()}p"
        names = cells[header + 1]
        parts, totals = [], None
        for row in cells[header + 2 :]:
            if row[0] == "Office Totals:":
                totals = row
            elif row[0]:
                parts.append((counties[re.sub(r"[^a-z]", "", row[0].lower())], row))
        columns = [column for column in range(3, len(names)) if names[column]]
        if totals is None or not any(names[column] != "SCATTERING" for column in columns):
            continue
        sums = [sum(int(row[column] or 0) for _, row in parts) for column in columns]
        if sums != [int(totals[column] or 0) for column in columns]:
            print(f"{chamber}: counties sum to {sums}, printed {[totals[column] for column in columns]}, skipped")
            continue
        rows.extend(
            (chamber, code, names[column], names[column] == "SCATTERING", int(row[column] or 0))
            for code, row in parts
            for column in columns
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
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
            summary="Wisconsin's 2014 State Senate and Assembly primaries by county (Government Accountability Board, via the Wayback Machine)",
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
