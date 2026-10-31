import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.arkansas.gov/uploads/elections/"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Election results: general elections by county"
FILES = {1998: ("Gen98Ver5.zip", date(1998, 11, 3)), 2000: ("general2000c.zip", date(2000, 11, 7))}
OFFICE = re.compile(
    r"(?:State (Representative|Senate)|U\.S\. Congress) District\s+(\d+)\s*$|(State Senator|State Rep\w*) District\s+(\d+)",
    re.IGNORECASE,
)
TITLES = re.compile(r"^(representative|congressman|congresswoman|senator|rep\.|sen\.)\s+", re.IGNORECASE)


def fetch() -> None:
    for name, _ in FILES.values():
        raw.fetch(SOURCE, BASE + name, suffix=".zip")


def office(label: str) -> tuple[str, int | None] | None:
    found = OFFICE.search(label.strip())
    if not found:
        return None
    number = int(found[2] or found[4])
    kind = (found[1] or found[3] or "congress").lower()
    if kind.startswith("congress"):
        return "house", number
    return ("senate" if "senat" in kind else "house") + f":{number}:g", None


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '05%%' order by u.code"""
        ).fetchall():
            fips.setdefault(re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()), code)

    def county(cell: object) -> str | None:
        return fips.get(re.sub(r"[^a-z]", "", re.sub(r"(?i)\s+county\s*$", "", str(cell or "")).lower()))

    rows: list[tuple[int, str, int | None, date, str, str, int]] = []
    for year, (name, day) in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, BASE + name).path) as archive:
            member = next(m for m in archive.namelist() if not m.endswith("/"))
            data = archive.read(member)
        if year == 1998:
            path = DATA / "parsed" / SOURCE / "gen98.xls"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            table = fastexcel.read_excel(path).load_sheet("Summary", header_row=None).to_polars().rows()
            counties = {i: county(c) for i, c in enumerate(table[0]) if i > 1 and county(c)}
            current = None
            for line in table[1:]:
                label = str(line[0] or "").strip()
                if label and line[1] is None:
                    current = office(label)
                    continue
                if current is None or not label:
                    continue
                candidate = TITLES.sub("", re.sub(r"\s+", " ", label)).upper()
                for column, code in counties.items():
                    try:
                        votes = int(float(line[column]))
                    except TypeError, ValueError:
                        continue
                    rows.append((year, current[0], current[1], day, code, candidate, votes))
        else:
            lines = data.decode("latin-1").splitlines()
            counties = {i: county(c) for i, c in enumerate(lines[0].split("|")) if i > 2 and county(c)}
            for line in lines[1:]:
                cells = line.split("|")
                current = office(cells[0])
                if current is None:
                    continue
                candidate = TITLES.sub("", re.sub(r"\s+", " ", cells[1])).upper()
                for column, code in counties.items():
                    try:
                        votes = int(cells[column])
                    except IndexError, ValueError:
                        continue
                    rows.append((year, current[0], current[1], day, code, candidate, votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "district": pl.Int16,
                "day": pl.Date,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            special=pl.lit(False),
            state=pl.lit("AR"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("AR"),
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
            summary="Arkansas's 1998 and 2000 state legislative and U.S. House general elections by county",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.arkansas.gov/elections/research/election-results",
            license="Public record",
        )
        for name, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(cs, conn, frame, {"AR": source}, strict=True)
        return cs.write()
