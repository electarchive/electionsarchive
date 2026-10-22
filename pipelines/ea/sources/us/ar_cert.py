import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_cert"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.arkansas.gov/uploads/elections/"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Certification reports: 2002 and 2004 general elections"
LANDING = "https://www.sos.arkansas.gov/elections/research/election-results"
FILES = {
    2002: ("2002_General.pdf", date(2002, 11, 5)),
    2004: ("2004_General_Election_and_Non-Partisan_Judicial_Runoff_Certification_Report.pdf", date(2004, 11, 2)),
}
HEADING = re.compile(r"^(State Representative|State Senate|U\.S\. Congress) District (\d+) \((.+?) County\)\s*$")
CANDIDATE = re.compile(r"^(.+?) - (?:[A-Za-z.]+(?: [A-Za-z.]+)*?)\s+([\d,]+)\s+[\d.]+%\s*$")
TITLES = re.compile(
    r"^(state representative|state senator|representative|senator|congressman|congresswoman|mayor|rep\.|sen\.)\s+",
    re.IGNORECASE,
)


def fetch() -> None:
    for name, _ in FILES.values():
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%' order by u.code"""
            ).fetchall()
        }
    rows: list[tuple[int, str, int | None, date, str, str, int]] = []
    for year, (name, day) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        current: tuple[str, int | None, str] | None = None
        visited: set[tuple[str, int | None, str]] = set()
        for line in text.splitlines():
            found = HEADING.match(line.strip())
            if found:
                number = int(found[2])
                if found[1] == "U.S. Congress":
                    current = ("house", number, fips[re.sub(r"[^a-z]", "", found[3].lower())])
                else:
                    kind = "house" if found[1].endswith("Representative") else "senate"
                    current = (f"{kind}:{number}:g", None, fips[re.sub(r"[^a-z]", "", found[3].lower())])
                if current in visited:
                    current = None
                else:
                    visited.add(current)
                continue
            if current is None:
                continue
            if re.match(r"^(Over|Under)\b", line.strip()):
                continue
            candidate = CANDIDATE.match(line.strip())
            if candidate:
                label = candidate[1]
                while (stripped := TITLES.sub("", label)) != label:
                    label = stripped
                rows.append(
                    (
                        year,
                        current[0],
                        current[1],
                        day,
                        current[2],
                        re.sub(r"\s+", " ", label).upper(),
                        int(candidate[2].replace(",", "")),
                    )
                )
            elif re.match(r"^[A-Z][A-Za-z.() ]+( \d+)?( \(.*\))?$", line.strip()) and not line.startswith(" "):
                current = None
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
    print(len(rows), "rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's 2002 and 2004 legislative and US House generals by county",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="Public record",
        )
        for name, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AR": source}, strict=True)
        return cs.write()
