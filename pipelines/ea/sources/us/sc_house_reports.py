import difflib
import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.sc_report import COUNTIES, FIXES, LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.sc_house_reports"
CACHE = "us.sc_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "{year} Election Report"
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_{}.pdf"
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7)}
PAGES = {2000: (37, 38), 2002: (39, 40), 2004: (47, 48), 2006: (52, 57)}
LEGISLATURE = {2000: (39, 63), 2002: (46, 63), 2004: (49, 73)}
CLARITY = "https://www.enr-scvotes.org/SC/19077/40477/reports/detailxml.zip"
CLARITY_TITLE = "2010 General Election: detail results"
CODES = {name.upper(): f"45{2 * i + 1:03}" for i, name in enumerate(COUNTIES)}
TITLE_LINE = re.compile(r"(?:REPRESENTATIVE IN CONGRESS|House of Representatives)\s+DISTRICT\s+0*(\d)", re.IGNORECASE)
LEGISLATURE_LINE = re.compile(r"(SENATE|HOUS\S*\s+OF\s+\.?R\S*)\s+DISTRICT\s+0*(\d+)\s*$", re.IGNORECASE)
CLARITY_TITLES = re.compile(r"(U\.S\. House|State House) of Representatives District (\d+)")
ROW = re.compile(r"^\s*(\D*?)\s{2,}(?=\S*\d)(\S.*)$")
SKIP = {"#", "OF", "# OF", "COUNTY", "PRECINCTS"}


def county(text: str) -> str | None:
    if "TOTAL" in text.upper():
        return "TOTAL"
    text = re.sub(r"[^A-Z]", "", text.upper().removesuffix(" COUNTY"))
    scores = sorted(((difflib.SequenceMatcher(None, text, name).ratio(), name) for name in CODES), reverse=True)
    return scores[0][1] if scores[0][0] >= 0.7 and scores[0][0] - scores[1][0] >= 0.05 else None


def blocks(text: str, legislature: bool) -> list[tuple[str, int, list[str]]]:
    found: list[tuple[str, int, list[str]]] = []
    for line in text.splitlines():
        if legislature and (title := LEGISLATURE_LINE.search(line)):
            found.append(("senate" if "SENATE" in title[1].upper() else "state", int(title[2]), []))
        elif not legislature and (title := TITLE_LINE.search(line)):
            found.append(("house", int(title[1]), []))
        elif "===" in line or "OFFICIAL RESULTS" in line.upper():
            found.append(("", 0, []))
        elif found:
            found[-1][2].append(line)
    return [block for block in found if block[1]]


def read(lines: list[str]) -> list[tuple[str, str, bool, int]] | str:
    header: list[str] = []
    pending: list[tuple[str, list[tuple[int, int]]]] = []
    rows: list[tuple[str, str, bool, int]] = []
    summed: dict[str, int] = {}
    totals: dict[str, int] = {}
    for line in lines:
        if not line.strip() or "WINNER" in line.upper().replace(" ", "") or "PERCENTAGES" in line.upper():
            continue
        match = ROW.match(line)
        if match is None or not match[1].strip() and not pending:
            if not pending:
                header.append(line)
            continue
        start = match.start(2)
        rest = re.sub(r"(\S)\s?([,.])\s?(\S{3})", r"\1\2\3", match[2])
        cells = [
            (start + m.end(), int(re.sub(r"[,.]", "", m[0].translate(FIXES))))
            for m in re.finditer(r"\S+", rest)
            if re.fullmatch(r"\d+(?:[,.]\d{3})*", m[0].translate(FIXES))
        ]
        if not match[1].strip():
            pending[-1][1].extend(cells)
            continue
        place = county(match[1])
        if place is None:
            return f"unread county {match[1]!r}"
        if place != "TOTAL":
            pending.append((place, cells))
            continue
        columns = [end for end, _ in cells]
        labels: dict[int, list[str]] = {end: [] for end in columns}
        for text in header[:-1]:
            for phrase in re.finditer(r"\S+(?: \S+)*", text):
                if phrase[0].upper() in SKIP:
                    continue
                if column := next((end for end in columns if end > phrase.start() + 2), None):
                    labels[column].append(phrase[0].upper())
        names = {}
        for end in columns:
            label = " ".join(labels[end])
            names[end] = "WRITE-INS" if "WRITE" in label or "W/I" in label else label
        for end, value in cells:
            if names[end] != "TOTAL":
                totals[names[end]] = totals.get(names[end], 0) + value
        if len({place for place, _ in pending}) < len(pending):
            return "a county twice"
        for place, values in pending:
            placed = [(next((c for c in columns if abs(c - end) <= 5), None), value) for end, value in values]
            if len({column for column, _ in placed if column is not None}) < sum(c is not None for c, _ in placed):
                return f"{place}: two values in one column"
            for column, value in placed:
                if column is None:
                    if value > 999:
                        return f"{place}: {value} in no column"
                    continue
                if names[column] == "TOTAL":
                    continue
                rows.append((place, names[column], names[column] == "WRITE-INS", value))
                summed[names[column]] = summed.get(names[column], 0) + value
        pending, header = [], []
    if not rows or summed != totals:
        return f"counties sum to {summed}, totals {totals}"
    return rows


def fetch() -> None:
    raw.fetch(SOURCE, CLARITY, suffix=".zip")


def parse() -> None:
    out = []
    with zipfile.ZipFile(raw.cached(SOURCE, CLARITY).path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xml"))
    for contest in root.iter("Contest"):
        if found := CLARITY_TITLES.fullmatch(contest.get("text", "")):
            chamber, district = ("house", int(found[2])) if found[1] == "U.S. House" else (f"house:{found[2]}:g", None)
            for choice in contest.iter("Choice"):
                writein = choice.get("text", "").upper() == "WRITE-IN"
                for place in choice.iter("County"):
                    out.append(
                        (
                            2010,
                            date(2010, 11, 2),
                            chamber,
                            district,
                            CODES[place.get("name", "").upper()],
                            "WRITE-INS" if writein else choice.get("text", "").upper(),
                            writein,
                            int(place.get("votes", "0")),
                        )
                    )
    for year, ranges in PAGES.items():
        for legislature, (first, last) in ((False, ranges), *(((True, LEGISLATURE[year]),) if year in LEGISLATURE else ())):
            text = subprocess.run(
                ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(raw.cached(CACHE, URL.format(year)).path), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
            for office, district, lines in blocks(text, legislature):
                rows = read(lines)
                if isinstance(rows, str):
                    print(f"{year} {office} {district}: {rows}, skipped")
                    continue
                chamber = "house" if office == "house" else f"{'senate' if office == 'senate' else 'house'}:{district}:g"
                out.extend(
                    (year, DAYS[year], chamber, district if office == "house" else None, CODES[place], name, writein, votes)
                    for place, name, writein, votes in rows
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        out,
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        special=pl.lit(False),
        state=pl.lit("SC"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("SC"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's U.S. House and legislative generals by county, 2000-2010 (State Election Commission election reports and 2010 detail results)",
            polity="US-SC",
        )
        for year in sorted(frame["year"].unique()):
            title, url = (CLARITY_TITLE, CLARITY) if year == 2010 else (TITLE.format(year=year), LANDING)
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, CLARITY) if year == 2010 else raw.cached(CACHE, URL.format(year)), source)
            medsl.attach(cs, conn, frame.filter(pl.col("year") == year), {"SC": source}, strict=True)
        return cs.write()
