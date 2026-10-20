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

SOURCE = "us.ky_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2010-2019/"
FILES = {
    2012: (f"{BASE}2012/2012offpriresults.pdf", date(2012, 5, 22)),
    2014: (f"{BASE}2014/2014%20Primary%20Election%20Results.pdf", date(2014, 5, 20)),
}
PUBLISHER = "Kentucky Secretary of State"
TITLE = "Official primary election results"
PARTY = re.compile(r"(Republican|Democratic) Party")
DISTRICT = re.compile(r"(\d+)(?:st|nd|rd|th) Congressional District")
CELL = re.compile(r"\S+(?: \S+)*")
NUMBER = re.compile(r"\d[\d,]*")


def fetch() -> None:
    for url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '21%%' order by u.code"""
        ).fetchall():
            fips.setdefault(re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()), code)
    rows: list[tuple[int, str, str, int | None, str, int]] = []
    for year, (url, _) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        office = party = ""
        district: int | None = None
        header: list[str] = []
        columns: list[str] = []
        counties: dict[str, list[int]] = {}
        started = False

        def flush() -> None:
            nonlocal counties
            kind = (
                "president"
                if office.startswith("President")
                else "senate"
                if office.startswith("United States Senator")
                else "house"
                if office.startswith("United States Representative")
                else None
            )
            if kind is not None and columns and counties:
                for index, candidate in enumerate(columns):
                    for name, values in counties.items():
                        rows.append(
                            (year, f"{kind}:{party.lower()}", fips[name], district, candidate.upper(), values[index])
                        )
            counties = {}

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "For the office of":
                flush()
                office, party, district, columns, started = "", "", None, [], False
                header = []
                continue
            if not office and not stripped.startswith("Commonwealth"):
                office = stripped
                continue
            if stripped.startswith(("Commonwealth of Kentucky", "Official ")):
                header = []
                started = False
                continue
            found = PARTY.fullmatch(stripped)
            if found:
                flush()
                party, columns, header, started = found[1], [], [], False
                continue
            found = DISTRICT.fullmatch(stripped)
            if found:
                flush()
                district, party, columns, header, started = int(found[1]), "", [], [], False
                continue
            if re.fullmatch(
                r"(?:\d+(?:st|nd|rd|th) (?:Senatorial|Representative) District|[A-Z][A-Za-z' .]+ District.*)", stripped
            ):
                flush()
                office = "skip"
                continue
            if stripped.startswith("Total Votes"):
                started = False
                continue
            numbers = NUMBER.findall(stripped)
            label = re.split(r"\s{2,}|\s(?=\d)", stripped, maxsplit=1)[0]
            if (
                numbers
                and re.fullmatch(r"[A-Za-z][A-Za-z' .-]*", label)
                and len(numbers) == len(re.split(r"\s+", stripped[len(label) :].strip()))
            ):
                name = re.sub(r"[^a-z]", "", label.lower())
                if name not in fips:
                    continue
                if not started:
                    started = True
                    columns = []
                    lines = header[-4:]
                    anchors = [index for index, text in enumerate(lines) if len(CELL.findall(text)) == len(numbers)]
                    if anchors:
                        anchor = anchors[-1]
                        found = list(CELL.finditer(lines[anchor]))
                        columns = [m[0] for m in found]
                        centers = [(m.start() + m.end()) / 2 for m in found]
                        for index, text in enumerate(lines):
                            if index == anchor:
                                continue
                            for m in CELL.finditer(text):
                                near = min(
                                    range(len(centers)), key=lambda c: abs(centers[c] - (m.start() + m.end()) / 2)
                                )
                                columns[near] = (
                                    f"{m[0]} {columns[near]}"
                                    if index < anchor
                                    else f"{columns[near]}{'' if columns[near].endswith('-') else ' '}{m[0]}"
                                )
                    else:
                        print(f"{year} {office} {district} {party}: no header row of {len(numbers)} cells")
                if columns:
                    counties[name] = [int(value.replace(",", "")) for value in numbers]
                continue
            if not started:
                header.append(line)
        flush()
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "code": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            day=pl.col("year").replace_strict({year: day for year, (_, day) in FILES.items()}, return_dtype=pl.Date),
            special=pl.lit(False),
            state=pl.lit("KY"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.col("candidate").str.contains("UNCOMMITTED|WRITE"),
            kind=pl.lit("county"),
            source=pl.lit("KY"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2012 and 2014 House, Senate, and presidential primaries by county",
            polity="US-KY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elect.ky.gov/results/Pages/default.aspx",
            license="Public record",
        )
        for url, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"KY": source}, strict=True)
        return cs.write()
