import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA

SOURCE = "us.ri_gen_9x"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elections.ri.gov/sites/g/files/xkgbur756/files/publications/Election_Publications/Countbooks/"
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Count books: vote by counties, 1992, 1994, and 1998 general elections"
URL = "https://elections.ri.gov/publications/election-publications"
COUNTIES = {"bristol": "44001", "kent": "44003", "newport": "44005", "providence": "44007", "washington": "44009"}
TABLES = [
    ("1992-Count-Book.pdf", 41, "1992-11-03", "president", 8, 8),
    ("1992-Count-Book.pdf", 47, "1992-11-03", "governor", 8, 5),
    ("1994-Count-Book.pdf", 33, "1994-11-08", "senate", 2, 2),
    ("1994-Count-Book.pdf", 34, "1994-11-08", "governor", 6, 3),
    ("1998-Count-Book.pdf", 84, "1998-11-03", "governor", 4, 4),
]
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?![\w])")


def fetch() -> None:
    for name in sorted({table[0] for table in TABLES}):
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def numbers(text: str, count: int) -> list[int] | None:
    text = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ",", text)
    text = re.sub(r"(?<=\d)\.(?=\d{3}\b)", ",", text)
    found = [int(token.replace(",", "")) for token in NUMBER.findall(text) if token.strip(",")]
    return found[:count] if len(found) >= count else None


def read(name: str, page: int, count: int, label: str) -> dict[str, list[int]]:
    path = raw.cached(SOURCE, BASE + name).path
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page + 1), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    county = None
    towns: dict[str, list[list[int]]] = {}
    totals: dict[str, list[int]] = {}
    printed = None
    for line in text.splitlines():
        stripped = line.strip()
        if head := re.match(r"([A-Za-z]+)[ .]?COUNTY\b(?!\s*TOTAL)", stripped, re.IGNORECASE):
            if head[1].lower() in COUNTIES:
                county = head[1].lower()
                towns.setdefault(county, [])
                continue
        if county is None:
            continue
        if re.match(r"STATE\s+TOTALS?", stripped, re.IGNORECASE):
            printed = numbers(re.sub(r"^[^\d]*", "", stripped), count)
            break
        if re.match(r"COUNTY\s+TOTALS?", stripped, re.IGNORECASE):
            totals[county] = numbers(re.sub(r"^[^\d]*", "", stripped), count)
            continue
        if re.match(r"[A-Za-z]{4}", stripped) and (values := numbers(re.sub(r"^[A-Za-z .'~:]*[.•\s]\s", "", stripped), count)):
            towns[county].append(values)
    if printed is None or set(totals) != set(COUNTIES) or any(value is None for value in totals.values()):
        raise ValueError(f"{label}: counties {sorted(totals)} printed {printed}")
    for position, total in enumerate(printed):
        if sum(values[position] for values in totals.values()) != total:
            raise ValueError(f"{label} column {position}: counties sum to {sum(v[position] for v in totals.values())}, printed {total}")
    for name_, rows in towns.items():
        if rows and all(sum(row[position] for row in rows) == totals[name_][position] for position in range(count)):
            continue
        print(f"{label} {name_}: {len(rows)} town rows do not all tie the county total")
    return totals


def parse() -> None:
    lines = []
    for name, page, day, office, count, keep in TABLES:
        label = f"{day} {office}"
        try:
            totals = read(name, page, count, label)
        except ValueError as error:
            print("NO EMIT:", error)
            continue
        for county, values in totals.items():
            for position, votes in enumerate(values[:keep]):
                lines.append((day, office, COUNTIES[county], f"{office}{position}", votes))
        print(label, "ties the printed state totals;", keep, "columns")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(lines, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Rhode Island's 1992 president and governor, 1994 Senate and governor, and 1998 governor general elections by county from the Board of Elections' count books",
        PUBLISHER,
        TITLE,
        URL,
        "RI",
        "44",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, BASE + name) for name in sorted({table[0] for table in TABLES})],
        lambda key: "County totals are from the Board of Elections' count book 'Vote by Counties' tables, whose five county totals add up to each printed state total.",
        statewide=True,
    )
