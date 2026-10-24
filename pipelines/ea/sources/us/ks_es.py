import difflib
import json
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_es"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kansas Secretary of State"
TITLE = "Election Statistics, State of Kansas, general elections 1920-1986"
LANDING = "https://cdm16884.contentdm.oclc.org/digital/collection/p16884coll129"
URL = "https://kgi.contentdm.oclc.org/digital/api/collection/p16884coll129/id/{ptr}/download"
POINTERS = {
    1920: 17, 1922: 19, 1924: 21, 1926: 23, 1928: 25, 1930: 27, 1932: 29, 1934: 31, 1936: 33, 1938: 35, 1940: 37,
    1942: 39, 1944: 41, 1946: 43, 1948: 45, 1950: 47, 1952: 49, 1954: 51, 1956: 53, 1958: 55, 1960: 57, 1962: 58,
    1964: 59, 1966: 60, 1968: 61, 1970: 62, 1972: 63, 1974: 64, 1976: 65, 1978: 66, 1980: 67, 1982: 68, 1984: 69,
    1986: 70,
}
COUNTIES = (
    "Allen Anderson Atchison Barber Barton Bourbon Brown Butler Chase Chautauqua Cherokee Cheyenne Clark Clay Cloud "
    "Coffey Comanche Cowley Crawford Decatur Dickinson Doniphan Douglas Edwards Elk Ellis Ellsworth Finney Ford "
    "Franklin Geary Gove Graham Grant Gray Greeley Greenwood Hamilton Harper Harvey Haskell Hodgeman Jackson "
    "Jefferson Jewell Johnson Kearny Kingman Kiowa Labette Lane Leavenworth Lincoln Linn Logan Lyon Marion Marshall "
    "McPherson Meade Miami Mitchell Montgomery Morris Morton Nemaha Neosho Ness Norton Osage Osborne Ottawa Pawnee "
    "Phillips Pottawatomie Pratt Rawlins Reno Republic Rice Riley Rooks Rush Russell Saline Scott Sedgwick Seward "
    "Shawnee Sheridan Sherman Smith Stafford Stanton Stevens Sumner Thomas Trego Wabaunsee Wallace Washington "
    "Wichita Wilson Woodson Wyandotte"
).split()
BY_NAME = {name.lower(): name for name in COUNTIES}
DIGITS = str.maketrans({"G": "6", "O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8", "i": "1", "|": "1"})
ORDINALS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8}
PARTIES = {
    "rep": "Republican",
    "dem": "Democrat",
    "pop": "Populist",
    "proh": "Prohibition",
    "prohib": "Prohibition",
    "soc": "Socialist",
    "socialist": "Socialist",
    "ind": "Independent",
    "union": "Union",
}


def path(year: int) -> Path:
    return raw.cached(SOURCE, URL.format(ptr=POINTERS[year])).path


def fetch() -> None:
    for year, ptr in POINTERS.items():
        raw.fetch(SOURCE, URL.format(ptr=ptr), suffix=".pdf")


def county_of(text: str) -> str | None:
    letters = re.sub(r"[^A-Za-z]", "", text).lower()
    if len(letters) < 3:
        return None
    if letters in BY_NAME:
        return BY_NAME[letters]
    close = difflib.get_close_matches(letters, list(BY_NAME), n=2, cutoff=0.8)
    if len(close) == 1:
        return BY_NAME[close[0]]
    if len(close) == 2:
        first, second = (difflib.SequenceMatcher(None, letters, c).ratio() for c in close)
        if first - second > 0.1:
            return BY_NAME[close[0]]
    return None


def number(token: str) -> int | None:
    digits = re.sub(r"[.,'`_ ]", "", token.translate(DIGITS))
    return int(digits) if digits.isdigit() else None


def split_row(line: str) -> tuple[str, list[tuple[int, int, str]]] | None:
    match = re.match(r"^(\s*)(\S.*?)(?:\s*\.{2,}|\s{3,}|\s*\. \.)", line)
    if match is None:
        return None
    rest_start = match.end()
    rest = line[rest_start:]
    stripped = rest.lstrip(" .,_-'`*:")
    offset = rest_start + (len(rest) - len(stripped))
    found = [
        (offset + m.start(), offset + m.end(), m.group().strip())
        for m in re.finditer(r"\S+(?: \S+)*", stripped)
    ]
    if not found:
        return None
    return match.group(2), found


def office_of(heading: str) -> str | None:
    flat = re.sub(r"[^A-Z]", "", heading.upper())
    if "LIEUTENANT" in flat or "ATTORNEY" in flat or "JUSTICE" in flat or "JUDGE" in flat or "AMENDMENT" in flat:
        return None
    if "GOVERNOR" in flat:
        return "governor"
    if "UNITEDSTATESSENATOR" in flat or "UNITEDSTATESSENATE" in flat or "UNITEDSTATESEN" in flat:
        return "senate"
    if "CONGRESS" in flat or "REPRESENTATIVESIN" in flat:
        return "house"
    if "PRESIDENT" in flat and "ELECTORS" in flat or flat.endswith("PRESIDENTANDVICEPRESIDENT"):
        return "president"
    return None


def label_of(chunks: list[str]) -> tuple[str, str | None]:
    text = " ".join(chunks)
    text = re.sub(r"\s+", " ", text).strip(" ,.")
    party = None
    match = re.search(r"\b([A-Za-z]+)\.?$", text)
    if match and match.group(1).lower() in PARTIES:
        party = PARTIES[match.group(1).lower()]
        text = text[: match.start()].strip(" ,.")
    return text, party


def tables(pages: list[str]) -> list[dict[str, object]]:
    done: list[dict[str, object]] = []
    open_tables: dict[tuple[str, int | None], dict[str, object]] = {}
    office: str | None = None
    district: int | None = None
    special = False
    current: dict[str, object] | None = None
    header: list[tuple[int, int, str]] = []
    phase = "general"
    for page, text in enumerate(pages, 1):
        for line in text.splitlines():
            flat = re.sub(r"[^A-Z]", "", line.upper()) if line.strip() else ""
            if "PRIMARYELECTION" in flat or "GENERALELECTION" in flat:
                phase = "general" if "GENERALELECTION" in flat else "primary"
                office, current, header = None, None, []
                continue
            if re.match(r"^\s*(FOR|FOB|F0R)\b", line.strip().upper() + " ") and len(line.strip()) > 8:
                found = office_of(line)
                office, district, special = found, None, "UNEXPIRED" in flat or "VACANCY" in flat
                continued = re.search(r"[-—–•]\s*C\s*o\s*n", line) is not None or "ONTINU" in flat or "ONCLUD" in flat
                current = None
                header = []
                if office is not None and continued:
                    current = open_tables.get((office, district))
                continue
            if "CONGRESSIONALDISTRICT" in flat and office == "house":
                word = flat.split("CONGRESSIONALDISTRICT")[0][-8:]
                found_district = next((n for w, n in ORDINALS.items() if word.endswith(w)), None)
                district = found_district
                special = special or "UNEXPIRED" in flat
                current = open_tables.get((office, district)) if "ONTINUED" in flat else None
                header = []
                continue
            if office is None:
                continue
            if re.match(r"^\s*T\s*o\s*t\s*a\s*l\s*s?\b", line, re.I):
                parsed = split_row("Totals" + "   " + line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else "")
                if current is not None and "cells" in current:
                    values = [number(t) for _, _, t in parsed[1]] if parsed else []
                    current["totals"] = values
                    done.append(current)
                    open_tables.pop((office, district), None)
                    current = None
                    header = []
                continue
            row = split_row(line)
            name = county_of(row[0]) if row else None
            if row is not None and name is not None and all(number(t) is not None for _, _, t in row[1]):
                if current is None:
                    current = {
                        "phase": phase,
                        "office": office,
                        "district": district,
                        "special": special,
                        "page": page,
                        "header": header,
                        "spans": [(a, b) for a, b, _ in row[1]],
                        "cells": [],
                    }
                    open_tables[(office, district)] = current
                current["cells"].append((name, [number(t) for _, _, t in row[1]]))
                continue
            if current is None and line.strip():
                for m in re.finditer(r"\S+(?: \S+)*", line):
                    header.append((m.start(), m.end(), m.group()))
    return done


def columns(table: dict[str, object]) -> list[tuple[str, str | None]]:
    spans: list[tuple[int, int]] = table["spans"]
    groups: list[list[tuple[int, str]]] = [[] for _ in spans]
    for start, end, text in table["header"]:
        text = re.sub(r"C\s*o\s*u\s*n\s*t\s*i\s*e\s*s\s*\.?", "", text, flags=re.I).strip()
        if not text or re.search(r"term|Position|Regular|Unexpired", text, re.I):
            continue
        centre = (start + end) / 2
        nearest = min(range(len(spans)), key=lambda i: abs((spans[i][0] + spans[i][1]) / 2 - centre))
        groups[nearest].append((start, text))
    return [label_of([text for _, text in group]) for group in groups]


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    frames = []
    report = []
    for year in POINTERS:
        pdf = path(year)
        pages = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout.split("\f")
        day = date(year, 11, 1)
        while day.weekday() != 0:
            day += timedelta(days=1)
        day += timedelta(days=1)
        for table in tables(pages):
            labels = columns(table)
            cells: list[tuple[str, list[int]]] = table["cells"]
            totals: list[int | None] = table.get("totals", [])
            width = len(labels)
            counties = [name for name, _ in cells]
            note = f"{year} {table['office']} d{table['district']} p{table['page']}"
            if table["special"] or table["phase"] != "general":
                report.append(f"{note}: special or primary, skipped")
                continue
            if len(set(counties)) != len(counties) or counties != sorted(counties):
                report.append(f"{note}: counties repeat or run out of order")
                continue
            if any(len(values) != width for _, values in cells) or len(totals) != width:
                report.append(f"{note}: row width differs from {width}, totals {len(totals)}")
                continue
            if any(not label for label, _ in labels) or len({label for label, _ in labels}) != width:
                report.append(f"{note}: labels {labels}")
                continue
            bad = [
                labels[i][0]
                for i in range(width)
                if sum(values[i] for _, values in cells) != totals[i]
            ]
            if bad:
                report.append(f"{note}: does not tie for {bad}")
                continue
            report.append(f"{note}: ties, {width} columns, {len(cells)} counties")
            frames.append(
                pl.DataFrame(
                    [
                        (
                            year,
                            table["office"],
                            day,
                            table["district"],
                            codes[county.lower()],
                            labels[i][0].upper(),
                            labels[i][1] or "",
                            values[i],
                        )
                        for county, values in cells
                        for i in range(width)
                    ],
                    schema={
                        "year": pl.Int32,
                        "chamber": pl.String,
                        "day": pl.Date,
                        "district": pl.Int16,
                        "code": pl.String,
                        "candidate": pl.String,
                        "party": pl.String,
                        "votes": pl.Int64,
                    },
                    orient="row",
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "report.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    (
        pl.concat(frames)
        .with_columns(
            writein=pl.lit(False),
            special=pl.lit(False),
            state=pl.lit("KS"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("KS"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's president, governor, Senate, and House general elections by county, 1920-1986, from the Secretary of State's Election Statistics volumes",
            polity="US-KS",
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
        for year in frame["year"].unique().sort():
            cs.artifact(raw.cached(SOURCE, URL.format(ptr=POINTERS[year])), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"KS": source},
            strict=True,
            authoritative="The Secretary of State's Election Statistics volume for the general election, read from its text layer; every column adds up to the printed total.",
        )
        return cs.write()
