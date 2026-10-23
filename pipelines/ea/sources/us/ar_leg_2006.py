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

SOURCE = "us.ar_leg_2006"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Arkansas Secretary of State"
TITLE = "2006 Elections Publication"
LANDING = "https://www.sos.arkansas.gov/elections/research/election-results"
URL = "https://www.sos.arkansas.gov/uploads/elections/20070308_ARSOS_2006_Elections_Publication_Final__Revised.xls"
DAY = date(2006, 11, 7)
SHEETS = ("Sen.", "Reps.", "Reps. 2", "Reps. 3", "Reps. 4", "Reps . 5", "Reps. 6", "Reps. 7")
OFFICE = re.compile(r"State (Senate|Representative) District (\d+)")
PARTY = re.compile(r"\s+(?:Democrat|Democratic|Republican|Green|Libertarian|Independent)\s*$", re.IGNORECASE)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xls")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%'"""
            ).fetchall()
        }
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", folder, str(raw.cached(SOURCE, URL).path)],
            capture_output=True,
            check=True,
        )
        book = pl.read_excel(
            next(Path(folder).glob("*.xlsx")), sheet_id=0, engine="calamine", has_header=False, raise_if_empty=False
        )
    rows = []
    for sheet in SHEETS:
        key: str | None = None
        names: list[str] = []
        parts: list[tuple[str, list[int]]] = []
        for row in book[sheet].rows():
            cells = [str(cell).strip() if cell is not None else "" for cell in row]
            label = re.sub(r"[^a-z]", "", cells[0].lower().removesuffix(" county"))
            if found := OFFICE.search(" ".join(cells)):
                key, names, parts = f"{'senate' if found[1] == 'Senate' else 'house'}:{int(found[2])}:g", [], []
            elif key is None:
                continue
            elif cells[0].startswith("Summary of"):
                names = [PARTY.sub("", cell) for cell in cells[1 : cells.index("Grand Totals")]]
            elif names and label == "totals":
                votes = [int(float(cell)) for cell in cells[1 : 1 + len(names)]]
                sums = [sum(values[c] for _, values in parts) for c in range(len(names))]
                if sums != votes:
                    print(f"{DAY} {key}: counties sum to {sums}, printed {votes}, skipped")
                else:
                    rows.extend((key, code, name, values[c]) for code, values in parts for c, name in enumerate(names))
                key, names, parts = None, [], []
            elif names and label in counties:
                parts.append((counties[label], [int(float(cell or 0)) for cell in cells[1 : 1 + len(names)]]))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    ).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("AR"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("AR"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's 2006 legislative generals by county (Secretary of State's 2006 Elections Publication workbook)",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AR": source}, strict=True)
        return cs.write()
