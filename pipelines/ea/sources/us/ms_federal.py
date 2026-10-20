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

SOURCE = "us.ms_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.ms.gov/elections/electionResults/"
FILES = {
    date(2022, 6, 7): "2022%20Democratic%20Primary%20Official%20Results.pdf",
    date(2022, 6, 28): "2022%20June%2028th%20Republican%20Runoff%20Final%20Results.pdf",
}
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Election results: official recapitulation"
ROW = re.compile(r"^(\S.*?)\s{2,}(Republican|Democrat|Libertarian|Independent|Reform)\s+(.*)$")


def fetch() -> None:
    for name in FILES.values():
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%%'"""
            ).fetchall()
        }
    rows = []
    for day, name in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for page in text.split("Statewide Election Management System")[1:]:
            lines = page.splitlines()
            start = next((i for i, line in enumerate(lines) if "day of" in line), None)
            if start is None:
                continue
            header = []
            for line in lines[start + 1 :]:
                if re.search(r"US House|US Senate|President", line):
                    break
                header.extend((m.start(), m[0]) for m in re.finditer(r"\S+(?: \S+)*", line))
            header.sort()
            district = None
            for line in lines[start + 1 :]:
                if found := re.search(r"US House Of Rep (\d+)-", line):
                    district = int(found[1])
                    continue
                match = ROW.match(line)
                if match is None or district is None:
                    continue
                values = [m[0] for m in re.finditer(r"X|\d[\d,]*", match[3])]
                if len(values) != len(header):
                    raise ValueError(f"{day} {match[1]}: {len(values)} values against {len(header)} counties")
                party = {"Democrat": "democratic", "Republican": "republican"}[match[2]]
                rows.extend(
                    (
                        day.year,
                        f"house:{party}",
                        day,
                        False,
                        "MS",
                        district,
                        counties[re.sub(r"[^a-z]", "", county.lower()).replace("jeffdavis", "jeffersondavis")],
                        match[1].strip().upper(),
                        1,
                        False,
                        int(value.replace(",", "")),
                    )
                    for (_, county), value in zip(header, values, strict=True)
                    if value != "X" and county.lower() != "total"
                )
    PARSED.mkdir(parents=True, exist_ok=True)
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("MS")).write_parquet(PARSED / "counties.parquet")
    print(len(rows), "rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's 2022 U.S. House primaries and runoffs by county",
            polity="US-MS",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ms.gov/elections-voting/election-results",
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MS": source}, strict=True)
        return cs.write()
