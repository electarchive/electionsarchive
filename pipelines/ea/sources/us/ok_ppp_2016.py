import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ok_ppp_2016"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = (
    "https://oklahoma.gov/content/dam/ok/en/elections/election-results/2016-election-results/2016-ppp-election-results/"
)
DAY = date(2016, 3, 1)
FILES = {
    "democratic": "2016-march-ppp-results-by-cd-dem.pdf",
    "republican": "2016-march-ppp-results-by-cd-rep.pdf",
}
GROUPS = {
    "democratic": [
        ["MICHAEL A. STEINBERG", "BERNIE SANDERS", "KEITH JUDD", "HILLARY CLINTON", "ROQUE ROCKY DE LA FUENTE"],
        ["MARTIN J. O'MALLEY", "STAR LOCKE"],
    ],
    "republican": [
        ["DONALD J. TRUMP", "RAND PAUL", "CARLY FIORINA", "MARCO RUBIO", "RICK SANTORUM"],
        ["TED CRUZ", "JEB BUSH", "CHRIS CHRISTIE", "MIKE HUCKABEE", "BEN CARSON"],
        ["LINDSEY GRAHAM", "JOHN R. KASICH"],
    ],
}
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Election results: presidential preferential primary by precinct"


def fetch() -> None:
    for name in FILES.values():
        try:
            raw.cached(SOURCE, BASE + name)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "-A", raw.USER_AGENT, BASE + name], capture_output=True, check=True
            ).stdout
            raw.store(
                SOURCE,
                BASE + name,
                body,
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
    rows = []
    for party, name in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        names: list[str] = []
        district = ""
        sums: dict[tuple[str, int, int], int] = {}
        for line in text.splitlines():
            if found := re.match(r"CONGRESSIONAL DISTRICT (\d+)", line):
                district = found[1]
            elif found := next((g for g in GROUPS[party] if g[0] in line), None):
                names = found
            elif found := re.match(r"(.+?) COUNTY (?:ABSENTEE )?PCT(?: \d+)?\s+([\d,]+(?:\s+[\d,]+)*)\s*$", line):
                values = [int(v.replace(",", "")) for v in found[2].split()]
                if len(values) != len(names):
                    raise ValueError(f"{line!r}: {len(values)} values for {names}")
                county = counties[re.sub(r"[^a-z]", "", found[1].lower())]
                for candidate, votes in zip(names, values, strict=True):
                    rows.append((county, candidate, party, votes))
                    key = (district, party, candidate)
                    sums[key] = sums.get(key, 0) + votes
            elif found := re.match(r"Total:\s+([\d,]+(?:\s+[\d,]+)*)\s*$", line):
                printed = [int(v.replace(",", "")) for v in found[1].split()]
                counted = [sums.get((district, party, candidate), 0) for candidate in names]
                if printed != counted:
                    print(party, district, names, "printed", printed, "counted", counted)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["code", "candidate", "party", "votes"], orient="row").group_by(
        "code", "candidate", "party"
    ).agg(pl.col("votes").sum()).with_columns(
        year=pl.lit(2016, pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), pl.col("party")),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("OK"),
        district=pl.lit(None, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("OK"),
    ).drop("party").write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's March 1, 2016 presidential preferential primaries by county, summed from the State Election Board's precinct certificates",
            polity="US-OK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://oklahoma.gov/elections/elections-results/election-results/2016-election-results/2016-march-ppp-election.html",
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OK": source}, strict=True)
        return cs.write()
