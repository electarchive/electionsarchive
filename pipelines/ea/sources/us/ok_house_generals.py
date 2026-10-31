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
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/{0}-election-results/{0}-general-election-results/{1}{2}.pdf"
LANDING = "https://oklahoma.gov/elections/elections-results/election-results.html"
PUBLISHER = "Oklahoma State Election Board"
TITLES = {
    "OK": "Official results: United States Representative by county",
    "OKL": "Official results: state legislature by county",
}
DAYS = {
    2000: date(2000, 11, 7),
    2002: date(2002, 11, 5),
    2004: date(2004, 11, 2),
    2006: date(2006, 11, 7),
    2008: date(2008, 11, 4),
    2010: date(2010, 11, 2),
}
FILES = {
    2000: ("usrep", "sh", "ss"),
    2002: ("usrep", "rep", "sen"),
    2004: ("sh", "ss"),
    2006: ("usrep", "sh", "ss"),
    2008: ("usrep", "sh", "ss"),
}
TITLE_LINE = re.compile(r"(UNITED STATES REPRESENTATIVE|STATE REPRESENTATIVE|STATE SENATOR), DISTRICT (\d+)")
CHAMBERS = {
    "UNITED STATES REPRESENTATIVE": "house",
    "STATE REPRESENTATIVE": "house:{}:g",
    "STATE SENATOR": "senate:{}:g",
}
URL_2010 = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/2010-election-results/2010-general-election-results/ge-results-county-20101102.pdf"
TITLE_2010 = re.compile(r"Race:\s+FOR (U\.S\. REPRESENTATIVE|STATE REPRESENTATIVE|STATE SENATOR), DISTRICT NO\. (\d+)")
ROW = re.compile(r"^([A-Za-z][A-Za-z .]*?):?\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")


def url(year: int, name: str) -> str:
    return BASE.format(year, str(year)[2:], name)


def pdf_text(link: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def blocks_2010(text: str) -> list[tuple[str, int, list[str], list[tuple[str, list[int]]], list[int]]]:
    found: dict[tuple[str, int], tuple[list[str], list[tuple[str, list[int]]], list[int]]] = {}
    key = None
    spans: list[tuple[int, int]] = []
    names: list[str] = []
    head = upper = above = ""
    for line in text.splitlines():
        previous, above = above, line
        if title := TITLE_2010.match(line):
            key = (title[1], int(title[2]))
            found.setdefault(key, ([], [], []))
            spans = []
        elif line.startswith("Race:"):
            key = None
        elif key is None:
            continue
        elif line.startswith("County") and not spans:
            head = line
            upper = previous
        elif line.startswith("====="):
            spans = [m.span() for m in re.finditer(r"=+", line)]
            columns = spans[1:-1]
            names = [
                " ".join(part[max(a - 1, 0) : b + 1].strip() for part in (upper, head)).strip() for a, b in columns
            ]
            if not found[key][0]:
                found[key][0].extend(names)
        elif spans:
            row = re.match(r"^([A-Za-z][A-Za-z .]*?):?\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$", line)
            if row is None:
                continue
            cells = [int(v.replace(",", "")) for v in row[2].split()]
            if row[1].upper().startswith("STATE TOTAL"):
                found[key][2][:] = cells
            else:
                found[key][1].append((row[1], cells))
    return [(office, district, *rest) for (office, district), rest in found.items()]


def tally(
    year: int,
    day: date,
    office: str,
    district: int,
    names: list[str],
    places: list[tuple[str, list[int]]],
    totals: list[int],
) -> list[tuple[int, date, str, int | None, str, str, int, str]]:
    sums = [sum(cells[i] for _, cells in places) for i in range(len(totals))]
    if any(len(cells) != len(totals) for _, cells in places) or sums != totals or len(names) != len(totals) - 1:
        print(f"{year} {office} {district}: counties sum to {sums}, printed {totals}, skipped")
        return []
    federal = office.startswith(("UNITED", "U.S."))
    return [
        (
            year,
            day,
            CHAMBERS[office.replace("U.S.", "UNITED STATES")].format(district),
            district if federal else None,
            code,
            name,
            cells[i],
            "OK" if federal else "OKL",
        )
        for code, cells in places
        for i, name in enumerate(names)
    ]


def fetch() -> None:
    for year, names in FILES.items():
        for name in names:
            raw.fetch(SOURCE, url(year, name), suffix=".pdf")
    raw.fetch(SOURCE, URL_2010, suffix=".pdf")


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
        text = pdf_text(url(year, file))
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
                places.append(
                    (
                        counties[re.sub(r"[^a-z]", "", found[1].lower())],
                        [int(v.replace(",", "")) for v in found[2].split()],
                    )
                )
            ends = [end for end, _ in totals]
            labels: dict[int, list[str]] = {end: [] for end in ends}
            for line in header:
                for phrase in re.finditer(r"\S+(?: \S+)*", line):
                    if column := next((end for end in ends if end > phrase.start() + 2), None):
                        labels[column].append(phrase[0])
            names = [re.sub(r"\s*\(\w+\)$", "", " ".join(labels[end])) for end in ends[:-1]]
            rows.extend(tally(year, day, office, district, names, places, [value for _, value in totals]))
    for office, district, names, places, totals in blocks_2010(pdf_text(URL_2010)):
        coded = [(counties[re.sub(r"[^a-z]", "", name.lower())], cells) for name, cells in places]
        rows.extend(tally(2010, DAYS[2010], office, district, names, coded, totals))
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
            summary="Oklahoma's U.S. House generals by county, 2000-2010, and its legislative generals by county, 2000-2010 (State Election Board)",
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
