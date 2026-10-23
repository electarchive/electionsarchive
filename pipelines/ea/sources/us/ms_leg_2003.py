import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ms_leg_2003"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Mississippi Secretary of State"
TITLE = "2003 general election recap: total votes reported by county"
LANDING = "http://www.sos.state.ms.us/elections/2003Elections.asp"
BASE = "https://web.archive.org/web/{}id_/http://www.sos.state.ms.us/elections/2003GeneralReCap/Certified/{}"
FILES = {
    "20041023123551": "12State%20Senate%20(Multi-County%20Districts).pdf",
    "20041023122656": "13State%20House%20of%20Representatives%20(Multi-County%20Districts).pdf",
}
DAY = date(2003, 11, 4)
OFFICE = re.compile(r"^\s+STATE (SENATOR|REPRESENTATIVE) D(\d+)\s*$")
CANDIDATE = re.compile(r"^\s+(\S.*?)\s{2,}([\d,]+)\s+[\d.]+%\s*$")
TOTAL = re.compile(r"Total for County\s+([\d,]+)\s*$")


def fetch() -> None:
    for timestamp, name in FILES.items():
        raw.fetch(SOURCE, BASE.format(timestamp, name), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%%'"""
            ).fetchall()
        }
    rows = []
    for timestamp, name in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE.format(timestamp, name)).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        seats: dict[str, tuple[list[tuple[str, str, int]], dict[str, int], list[str]]] = {}
        key: str | None = None
        county: str | None = None
        lines: list[tuple[str, int]] = []
        state = False
        for line in text.splitlines():
            if found := OFFICE.match(line):
                key = f"{'senate' if found[1] == 'SENATOR' else 'house'}:{int(found[2])}:g"
                seats.setdefault(key, ([], {}, []))
                county, lines, state = None, [], False
            elif key is None:
                continue
            elif line.startswith("State Totals"):
                state = True
            elif state and (found := CANDIDATE.match(line)):
                seats[key][1][found[1]] = int(found[2].replace(",", ""))
            elif state:
                continue
            elif found := TOTAL.search(line):
                if county is None or sum(votes for _, votes in lines) != int(found[1].replace(",", "")):
                    seats[key][2].append(f"{county} lines {lines} total {found[1]}")
                else:
                    seats[key][0].extend((county, candidate, votes) for candidate, votes in lines)
                county, lines = None, []
            elif line[:1].isalpha() and (code := counties.get(re.sub(r"[^a-z]", "", line.lower()))):
                county, lines = code, []
            elif county is not None and (found := CANDIDATE.match(line)):
                lines.append((found[1], int(found[2].replace(",", ""))))
        for key, (entries, totals, problems) in seats.items():
            sums: dict[str, int] = {}
            for _, candidate, votes in entries:
                sums[candidate] = sums.get(candidate, 0) + votes
            if problems or not entries or sums != totals:
                print(f"{DAY} {key}: {problems or (sums, totals)}, skipped")
                continue
            rows.extend((key, code, candidate.upper(), votes) for code, candidate, votes in entries)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    ).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("MS"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("MS"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's 2003 multi-county legislative generals by county (Secretary of State's certified recap, via the Wayback Machine)",
            polity="US-MS",
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
        for timestamp, name in FILES.items():
            cs.artifact(raw.cached(SOURCE, BASE.format(timestamp, name)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MS": source}, strict=True)
        return cs.write()
