import csv
import io
import re
import subprocess
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_leg_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alabama Secretary of State"
TITLE = "2014 general election: precinct-level results"
LANDING = "https://www.sos.alabama.gov/alabama-votes/voter/election-data"
URL = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/2014General-precinctLevel.zip"
DAY = date(2014, 11, 4)
OFFICE = re.compile(
    r"STATE (REPRESENTATIVE|SENATOR),?\s+(?:D\d+\s+)?DISTRICT(?: NO\.)?\s*(\d+)|State (Rep|Sen)\.?\s*(?:Dist\.?)?\s*(\d+)",
    re.IGNORECASE,
)
DROPPED = {"over votes", "under votes"}
RELABELED = {
    ("DeKalb", "State Rep. Dist 9"): "State Sen. Dist 9",
    ("DeKalb", "State Rep. Dist 10"): "State Sen. Dist 10",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


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
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        for name in archive.namelist():
            county = re.sub(r"^2014-General-|\s*2014 General Precinct|\.(xlsx?|csv)$", "", name.rsplit("/", 1)[-1])
            code = counties[re.sub(r"[^a-z]", "", county.lower())]
            if name.endswith(".csv"):
                table = csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")))
                cells = [["Contest Title", "", "", "Total"]] + [
                    [
                        row["Contest Title"].strip(),
                        row["Party Code"].strip(),
                        row["Candidate Name"].strip(),
                        row["Registered Voters"],
                    ]
                    for row in table
                ]
            else:
                data = archive.read(name)
                if name.endswith(".xls"):
                    with tempfile.TemporaryDirectory() as folder:
                        (Path(folder) / "book.xls").write_bytes(data)
                        subprocess.run(
                            ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", folder, f"{folder}/book.xls"],
                            capture_output=True,
                            check=True,
                        )
                        data = (Path(folder) / "book.xlsx").read_bytes()
                book = pl.read_excel(data, engine="calamine", has_header=False, infer_schema_length=0)
                cells = [[str(cell).strip() if cell is not None else "" for cell in row] for row in book.rows()]
            if cells[0][0] == "Contest Title":
                for row in cells[1:]:
                    found = OFFICE.fullmatch(row[0])
                    if found and row[2].lower() not in DROPPED:
                        total = cells[0][3].startswith("Total")
                        votes = sum(int(float(cell)) for cell in (row[3:4] if total else row[3:]) if cell)
                        chamber = "house" if found[1].upper() == "REPRESENTATIVE" else "senate"
                        rows.append((f"{chamber}:{int(found[2])}:g", code, row[2], row[2] == "Write-In", votes))
                continue
            titles, current = [], ""
            for cell in cells[0]:
                current = cell or current
                titles.append(current)
            totals = re.compile(r"(CALCULATED|REPORTED)( TOTALS)?", re.IGNORECASE)
            reported = next((row for row in cells if re.fullmatch(r"REPORTED( TOTALS)?", row[0], re.IGNORECASE)), None)
            precincts = [row for row in cells[2:] if row[0] and not totals.fullmatch(row[0])]
            for column, title in enumerate(titles):
                found = OFFICE.fullmatch(RELABELED.get((county, title), title))
                if not found or column == 0 or not cells[1][column]:
                    continue
                summed = sum(int(float(row[column])) for row in precincts if re.fullmatch(r"[\d.]+", row[column]))
                votes = int(float(reported[column])) if reported and reported[column] else summed
                if votes != summed:
                    print(f"{county} {title} {cells[1][column]}: precincts {summed}, reported {votes}")
                candidate = re.sub(r"\s*\([\w. ]+\)\s*$", "", cells[1][column])
                chamber = "house" if found[3].lower() == "rep" else "senate"
                rows.append((f"{chamber}:{int(found[4])}:g", code, candidate, candidate.lower() == "write-in", votes))
    rows = [
        (chamber, code, re.sub(r",?\s+(JR|SR|II|III|IV)\.?$", "", " ".join(candidate.upper().split())), writein, votes)
        for chamber, code, candidate, writein, votes in rows
    ]
    full: dict[tuple[str, str], set[str]] = {}
    for chamber, _, candidate, _, _ in rows:
        if " " in candidate:
            full.setdefault((chamber, candidate.split()[-1]), set()).add(candidate)
    rows = [
        (chamber, code, next(iter(names)), writein, votes)
        if " " not in candidate and len(names := full.get((chamber, candidate), set())) == 1
        else (chamber, code, candidate, writein, votes)
        for chamber, code, candidate, writein, votes in rows
    ]
    PARSED.mkdir(parents=True, exist_ok=True)
    (
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
        )
        .group_by("chamber", "code", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            district=pl.lit(None, pl.Int16),
            special=pl.lit(False),
            state=pl.lit("AL"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2014 legislative generals by county from the Secretary of State's precinct-level workbooks",
            polity="US-AL",
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
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True)
        return cs.write()
