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
BASE = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/{0}-election-results/{0}-general-election-results/{1}{2}.pdf"
LANDING = "https://oklahoma.gov/elections/elections-results/election-results.html"
PUBLISHER = "Oklahoma State Election Board"
TITLES = {
    "OK": "Official results: United States Representative by county",
    "OKL": "Official results: state legislature by county",
}
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7)}
FILES = {2000: ("usrep", "sh", "ss"), 2002: ("usrep", "rep", "sen"), 2004: ("sh", "ss"), 2006: ("usrep", "sh", "ss")}
TITLE_LINE = re.compile(r"(UNITED STATES REPRESENTATIVE|STATE REPRESENTATIVE|STATE SENATOR), DISTRICT (\d+)")
CHAMBERS = {"UNITED STATES REPRESENTATIVE": "house", "STATE REPRESENTATIVE": "house:{}:g", "STATE SENATOR": "senate:{}:g"}
ROW = re.compile(r"^([A-Za-z][A-Za-z .]*?):?\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")


def url(year: int, name: str) -> str:
    return BASE.format(year, str(year)[2:], name)


def fetch() -> None:
    for year, names in FILES.items():
        for name in names:
            raw.fetch(SOURCE, url(year, name), suffix=".pdf")


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
    for year, day, file in ((year, DAYS[year], name) for year, names in FILES.items() for name in names):
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year, file)).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        blocks: list[tuple[str, int, list[str]]] = []
        for line in text.splitlines():
            if found := TITLE_LINE.search(line):
                blocks.append((found[1], int(found[2]), []))
            elif blocks:
                blocks[-1][2].append(line)
        for office, district, lines in blocks:
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
                print(f"{year} {office} {district}: counties sum to {sums}, printed {totals}, skipped")
                continue
            federal = office == "UNITED STATES REPRESENTATIVE"
            rows.extend(
                (
                    year,
                    day,
                    CHAMBERS[office].format(district),
                    district if federal else None,
                    code,
                    name,
                    cells[i],
                    "OK" if federal else "OKL",
                )
                for code, cells in places
                for i, name in enumerate(names)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "source": pl.String,
        },
        orient="row",
    ).with_columns(
        special=pl.lit(False),
        state=pl.lit("OK"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's U.S. House generals by county, 2000, 2002, and 2006, and its legislative generals by county, 2000-2006 (State Election Board)",
            polity="US-OK",
        )
        sources = {key: cs.source(PUBLISHER, title) for key, title in TITLES.items()}
        for key, source in sources.items():
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=TITLES[key],
                    url=LANDING,
                    license="Public record",
                )
        for year, names in FILES.items():
            for name in names:
                cs.artifact(raw.cached(SOURCE, url(year, name)), sources["OK" if name == "usrep" else "OKL"])
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), sources, strict=True)
        return cs.write()
