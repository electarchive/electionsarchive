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

SOURCE = "us.mt_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {2020: ("https://sosmt.gov/wp-content/uploads/2020PrimaryReportStateCanvass.pdf", date(2020, 6, 2))}
PUBLISHER = "Montana Secretary of State"
TITLE = "Statewide primary election canvass"
PARTIES = {"Democrat": "democratic", "Republican": "republican", "Green": "green", "Libertarian": "libertarian"}
OFFICES = re.compile(r"^(PRESIDENT|UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE|UNITED STATES|REPRESENTATIVE|SENATOR)\b")
ROW = re.compile(r"^([A-Z][A-Za-z.&' ]+?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")


def fetch() -> None:
    for url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%' order by u.code"""
            ).fetchall()
        }
    rows = []
    for year, (url, day) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for page in text.split("\f"):
            lines = [line for line in page.splitlines() if line.strip()]
            head: list[str] = []
            body: list[tuple[str, list[re.Match[str]]]] = []
            for line in lines:
                found = ROW.match(line)
                name = re.sub(r"[^a-z]", "", found[1].lower().replace("&", "and")) if found else None
                if found is not None and name in fips:
                    body.append((name, [m for m in re.finditer(r"\d[\d,]*", line) if m.start() >= found.start(2)]))
                elif not body and not line.strip().startswith("MONTANA SECRETARY") and "CANVASS" not in line:
                    head.append(line)
            if not body:
                continue
            party = next((PARTIES[p] for line in head for p in PARTIES if re.search(rf"\b{p}\b", line)), None)
            titles = [line.strip() for line in head if OFFICES.match(line.strip())]
            office = " ".join(titles)
            kind = (
                "president"
                if office.startswith("PRESIDENT")
                else "senate"
                if "SENATOR" in office
                else "house"
                if "REPRESENTATIVE" in office
                else None
            )
            if kind is None or party is None:
                continue
            count = len(body[0][1])
            starts = [min(row[1][i].start() for row in body if len(row[1]) == count) for i in range(count)]
            words: list[list[str]] = [[] for _ in range(count)]
            for line in head:
                stripped = line.strip()
                if OFFICES.match(stripped) or stripped in PARTIES or stripped.startswith(("2020 ", "REPRESENTATIVE ")):
                    continue
                for m in re.finditer(r"\S+", line):
                    if m[0] in PARTIES:
                        continue
                    center = (m.start() + m.end()) / 2
                    words[max((i for i in range(count) if starts[i] <= center), default=0)].append(m[0])
            names = [" ".join(w).upper() for w in words]
            for county, values in body:
                if len(values) != count:
                    continue
                for candidate, value in zip(names, values, strict=True):
                    rows.append(
                        (year, f"{kind}:{party}", day, fips[county], candidate, int(value[0].replace(",", "")))
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"year": pl.Int32, "chamber": pl.String, "day": pl.Date, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        special=pl.lit(False),
        state=pl.lit("MT"),
        district=pl.lit(None, dtype=pl.Int16),
        measure=pl.lit(1, dtype=pl.Int16),
        writein=pl.col("candidate").str.contains("NO PREFERENCE|WRITE"),
        kind=pl.lit("county"),
        source=pl.lit("MT"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's 2020 president, U.S. Senate, and U.S. House primaries by county",
            polity="US-MT",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sosmt.gov/elections/results/",
            license="Public record",
        )
        for url, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MT": source}, strict=True)
        return cs.write()
