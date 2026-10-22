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

SOURCE = "us.ok_house_generals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/{0}-election-results/{0}-general-election-results/{1}usrep.pdf"
LANDING = "https://oklahoma.gov/elections/elections-results/election-results.html"
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Official results: United States Representative by county"
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2006: date(2006, 11, 7)}
TITLE_LINE = re.compile(r"UNITED STATES REPRESENTATIVE, DISTRICT (\d+)")
ROW = re.compile(r"^([A-Za-z][A-Za-z .]*?):?\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")


def url(year: int) -> str:
    return BASE.format(year, str(year)[2:])


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


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
    for year, day in DAYS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year)).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        blocks: list[tuple[int, list[str]]] = []
        for line in text.splitlines():
            if found := TITLE_LINE.search(line):
                blocks.append((int(found[1]), []))
            elif blocks:
                blocks[-1][1].append(line)
        for district, lines in blocks:
            header: list[str] = []
            places: list[tuple[str, list[int]]] = []
            totals: list[tuple[int, int]] = []
            for line in lines:
                found = ROW.match(line)
                if found is None:
                    if not places and line.strip() and "=" not in line and "Election" not in line:
                        header.append(line)
                    continue
                if found[1].upper().startswith("STATE TOTAL"):
                    totals = [(m.end(), int(m[0].replace(",", ""))) for m in re.finditer(r"[\d,]+", line)]
                    break
                places.append((counties[re.sub(r"[^a-z]", "", found[1].lower())], [int(v.replace(",", "")) for v in found[2].split()]))
            ends = [end for end, _ in totals]
            labels: dict[int, list[str]] = {end: [] for end in ends}
            for line in header:
                for phrase in re.finditer(r"\S+(?: \S+)*", line):
                    if column := next((end for end in ends if end > phrase.start() + 2), None):
                        labels[column].append(phrase[0])
            names = [re.sub(r"\s*\(\w+\)$", "", " ".join(labels[end])) for end in ends[:-1]]
            sums = [sum(cells[i] for _, cells in places) for i in range(len(ends))]
            if any(len(cells) != len(ends) for _, cells in places) or sums != [value for _, value in totals]:
                print(f"{year} District {district}: counties sum to {sums}, printed {totals}, skipped")
                continue
            rows.extend(
                (year, day, district, code, name, cells[i])
                for code, cells in places
                for i, name in enumerate(names)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        chamber=pl.lit("house"),
        special=pl.lit(False),
        state=pl.lit("OK"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("OK"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's U.S. House generals by county, 2000, 2002, and 2006 (State Election Board)",
            polity="US-OK",
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
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OK": source}, strict=True)
        return cs.write()
