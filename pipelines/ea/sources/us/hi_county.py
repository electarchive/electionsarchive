import html
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.hi_county"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://files.hawaii.gov/elections/files/results/{year}/general/{county}.pdf"
YEARS = {2000: date(2000, 11, 7), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7), 2008: date(2008, 11, 4), 2010: date(2010, 11, 2), 2012: date(2012, 11, 6), 2014: date(2014, 11, 4)}
COUNTIES = {"coh": "15001", "cch": "15003", "cok": "15007", "com": "15009"}
OHA = "trustee-of-the-office-of-hawaiian-affairs"
PUBLISHER = "Hawaii Office of Elections"
TITLE = "General election summary reports by county"
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
NUM = re.compile(r"^[\d,]+$")
PCT = re.compile(r"^\d{1,3}\.\d%?$")
MARK = re.compile(r"\d+ of \d+")
NOISE = re.compile(
    r"^={3,}|PAGE \d|Page \d|Printed on|Final Report|FINAL SUMMARY|SUMMARY REPORT|^Number \d|Open Primary|ELECTION - |PRIMARY ELECTION|General 20|GENERAL ELECTION|Hawaii County|Honolulu|Kauai|Maui|Number To Vote"
)
HOUSE = re.compile(r"U\.?S\.? Rep\w*\.?(?:,| -)?\s*(?:Dist\.?\s*(I{1,2}|\d)|\[D (\d)\])\s*$", re.IGNORECASE)
RESIDENT = re.compile(r"^(?:OHA - (HAWAII|OAHU|MAUI|KAUAI|MOLOKAI) \(1\)|(Hawaii|Oahu|Maui|Kauai|Molokai) Resident Trustee)$", re.IGNORECASE)
AT_LARGE = re.compile(r'^(?:OHA - AT LARGE \(1\)|(?:"|&quot;)?At[- ]Large(?:"|&quot;)? Trustee(?: Number To Vote For: 3)?)$', re.IGNORECASE)


def fetch() -> None:
    for year in YEARS:
        for county in COUNTIES:
            raw.fetch(SOURCE, BASE.format(year=year, county=county), suffix=".pdf")


def blocks(path: Path) -> list[tuple[str, list[tuple[str, int]]]]:
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    found: list[tuple[str, list[tuple[str, int]]]] = []
    for page in re.split(r"<page ", xml)[1:]:
        words = [(float(a), float(b), float(c), t) for a, b, c, _, t in WORD.findall(page)]
        pcts = sorted(x1 for _, _, x1, t in words if PCT.match(t))
        groups: list[list[float]] = []
        for x in pcts:
            if groups and x - groups[-1][-1] < 40:
                groups[-1].append(x)
            else:
                groups.append([x])
        centers = sorted(sorted(group)[len(group) // 2] for group in groups if len(group) >= 2)
        columns: dict[int, list[list]] = {}
        for x0, y, x1, token in sorted(words, key=lambda w: (w[1], w[0])):
            mid = (x0 + x1) / 2
            column = next(
                (i for i, c in enumerate(centers) if (centers[i - 1] + 8 if i else -1e9) < mid <= c + 8), None
            )
            if column is None:
                continue
            lines = columns.setdefault(column, [])
            if lines and abs(lines[-1][0] - y) < 3:
                lines[-1][1].append((x0, token))
            else:
                lines.append([y, [(x0, token)]])
        for column in sorted(columns):
            header: list[str] = []
            current: list[tuple[str, int]] | None = None
            items: list[tuple[str, float, list[str]]] = []
            for y, line in columns[column]:
                tokens = [token for _, token in sorted(line)]
                joined = " ".join(tokens)
                if MARK.search(joined):
                    head = joined.split(MARK.search(joined)[0])[0].strip()
                    if head and not NOISE.search(head):
                        items.append(("text", y, [head]))
                    items.append(("mark", y, []))
                elif len(tokens) >= 2 and PCT.match(tokens[-1]) and NUM.match(tokens[-2]):
                    items.append(("entry", y, [" ".join(tokens[:-2]).strip(), tokens[-2].replace(",", "")]))
                elif len(tokens) == 1 and PCT.match(tokens[0]):
                    items.append(("mark", y, []))
                elif not NOISE.search(joined):
                    items.append(("text", y, [joined]))
            k = 0
            while k < len(items):
                kind, y, data = items[k]
                if kind == "entry":
                    name = data[0]
                    if not name and k + 1 < len(items) and items[k + 1][0] == "text" and items[k + 1][1] - y < 25:
                        name = items[k + 1][2][0]
                        k += 1
                    if current is None:
                        current = []
                        found.append((" ".join(header), current))
                    current.append((name, int(data[1])))
                    if name.upper().startswith("OVER VOTES"):
                        current = None
                        header = []
                elif kind == "text":
                    if current is not None:
                        current = None
                        header = []
                    header.append(data[0])
                k += 1
    return found


def parse() -> None:
    rows = []
    for year, day in YEARS.items():
        for county, code in COUNTIES.items():
            for header, entries in blocks(raw.cached(SOURCE, BASE.format(year=year, county=county)).path):
                header = html.unescape(header).strip()
                if re.search(r"vac", header, re.IGNORECASE):
                    continue
                if found := HOUSE.search(header):
                    chamber, district = "house", {"I": 1, "II": 2}.get(found[1] or "") or int(found[1] or found[2])
                elif found := RESIDENT.match(header):
                    chamber, district = f"{OHA}:{(found[1] or found[2]).lower()}", None
                elif AT_LARGE.match(header):
                    chamber, district = OHA, None
                else:
                    continue
                for name, votes in entries:
                    name = re.sub(r"^\([A-Z ]{1,2}\)\s*", "", html.unescape(name)).strip()
                    if name.upper() in {"BLANK VOTES:", "BLANK VOTES", "OVER VOTES:", "OVER VOTES"} or not name:
                        continue
                    rows.append((year, chamber, day, False, "HI", district, code, name.upper(), 1, False, votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
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
    )
    frame.with_columns(kind=pl.lit("county"), source=pl.lit("HI")).write_parquet(PARSED / "counties.parquet")
    print(len(rows), "rows;", frame.select("year", "chamber", "district").unique().height, "contests")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's 2000 and 2004–2014 U.S. House and Office of Hawaiian Affairs trustee generals by county",
            polity="US-HI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.hawaii.gov/resources/election-results/",
            license="Public record",
        )
        for year in YEARS:
            for county in COUNTIES:
                cs.artifact(raw.cached(SOURCE, BASE.format(year=year, county=county)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"HI": source}, strict=True)
        return cs.write()
