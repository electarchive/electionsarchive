import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.hi_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://files.hawaii.gov/elections/files/results/"
DAYS = {
    date(2000, 9, 23): "2000/primary",
    date(2004, 9, 18): "2004/primary",
    date(2006, 9, 23): "2006/primary",
    date(2008, 9, 20): "2008/primary",
    date(2010, 9, 18): "2010/primary",
    date(2012, 8, 11): "2012/primary",
    date(2014, 8, 9): "2014/primary",
    date(2016, 8, 13): "2016/primary",
    date(2018, 8, 11): "2018/primary",
    date(2020, 8, 8): "2020/primary",
    date(2022, 8, 13): "2022/primary",
    date(2024, 8, 10): "2024/Primary",
}
COUNTIES = {"coh": "15001", "cch": "15003", "cok": "15007", "com": "15009"}
PARTIES = {
    "R": "republican",
    "D": "democratic",
    "L": "libertarian",
    "G": "green",
    "N": "nonpartisan",
    "C": "constitution",
    "NL": "natural-law",
}
PUBLISHER = "Hawaii Office of Elections"
TITLE = "Primary election summary reports by county"
HEADER = re.compile(r"^(U\.?S\.? Senator|U\.?S\.? Representative, Dist (I+|\d)|President[^-]*) - ([A-Z]+)$")
GUTTER = re.compile(r" {3,}")
ROMAN = {"I": 1, "II": 2}
OLD_HEADER = re.compile(r"^\((\w+)\) (U\.S\. Senator|U\.S\. Representative(?:(?:,| -) \[D (\d)\])?)$")
OLD_ROW = re.compile(r"^([\d,]+) (.+?) [\d.]+%$")
OLD_PARTIES = {"R": "R", "RE": "R", "D": "D", "DE": "D", "L": "L", "LI": "L", "G": "G", "GR": "G", "N": "N", "NP": "N", "C": "C", "NL": "NL"}


def fetch() -> None:
    for day, folder in DAYS.items():
        for county in COUNTIES:
            raw.fetch(SOURCE, f"{BASE}{folder}/{county}.pdf", suffix=".pdf")


def parse() -> None:
    rows = []
    for day, folder in DAYS.items():
        for county, code in COUNTIES.items():
            text = subprocess.run(
                ["pdftotext", "-layout" if day.year <= 2020 else "-raw", str(raw.cached(SOURCE, f"{BASE}{folder}/{county}.pdf").path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            states: dict[int, tuple[str | None, int | None] | None] = {}
            cells = []
            for line in text.splitlines():
                if day.year == 2020:
                    cells.extend((i, line[i * 65 : (i + 1) * 65].strip()) for i in range(3))
                elif day.year > 2020:
                    cells.append((0, line.strip()))
            if day.year in (2000, 2004):
                text = subprocess.run(
                    ["pdftotext", "-raw", str(raw.cached(SOURCE, f"{BASE}{folder}/{county}.pdf").path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                for line in text.splitlines():
                    if header := OLD_HEADER.match(line.strip()):
                        letter = OLD_PARTIES.get(header[1], "X")
                        district = "I" * int(header[3]) if header[3] else ""
                        line = (
                            f"U.S. Senator - {letter}"
                            if header[2].endswith("Senator")
                            else f"U.S. Representative, Dist {district} - {letter}"
                        )
                    elif re.match(r"^\(\w+\) ", line.strip()) or line.strip().startswith(("Councilmember", "Mayor")):
                        line = "Other - X"
                    elif row := OLD_ROW.match(line.strip()):
                        line = "Blank Votes" if re.fullmatch(r"(BLANK|OVER) VOTES", row[2]) else f"{row[2]} {row[1]} 0.0%"
                    cells.append((0, line))
            elif day.year < 2020:
                cells = []
                for page in text.split("\f"):
                    lines = page.splitlines()
                    body = lines[next((i for i, line in enumerate(lines) if "FINAL SUMMARY REPORT" in line), -1) + 1 :]
                    width = max((len(line) for line in body), default=0)
                    padded = [line.ljust(width) for line in body]
                    head = next((line for line in padded if line.strip()), "")
                    starts = [0, *(found.end() for found in GUTTER.finditer(head.rstrip()))]
                    spans = list(zip(starts, [*starts[1:], width]))
                    for index, (left, right) in enumerate(spans):
                        pending = None
                        for line in padded:
                            cell = line[left:right].strip()
                            if re.fullmatch(r"[\d,]+\s+[\d.]+%", cell):
                                pending = cell
                                continue
                            if pending and cell and not cell.startswith(("Blank Votes", "Over Votes")) and not re.search(r"\d%$|\d of \d", cell):
                                cell = f"{cell} {pending}"
                            if cell:
                                pending = None
                            cells.append((index, cell))
            for index, line in cells:
                current = states.get(index)
                if found := HEADER.match(line):
                    office = "senate" if "Senator" in found[1] else "house" if "Representative" in found[1] else "president"
                    current = (
                        f"{office}:{PARTIES[found[3]]}" if found[3] in PARTIES else None,
                        ROMAN.get(found[2]) or int(found[2]) if found[2] else None,
                    )
                elif re.match(r"^[A-Z].* - [A-Z]+$", line) or line.startswith(("Page ", "Printed")):
                    current = None
                elif current and current[0] and not line.startswith(("Blank Votes", "Over Votes")):
                    if match := re.fullmatch(r"(.+?)\s+([\d,]+)\s+[\d.]+%", line):
                        rows.append(
                            (
                                day.year,
                                current[0],
                                day,
                                False,
                                "HI",
                                current[1],
                                code,
                                match[1].upper(),
                                1,
                                False,
                                int(match[2].replace(",", "")),
                            )
                        )
                states[index] = current
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("HI")).write_parquet(PARSED / "counties.parquet")
    print(len(rows), "rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's 2000, 2004, and 2006–2024 president, U.S. Senate, and House primaries by county",
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
        for day, folder in DAYS.items():
            for county in COUNTIES:
                cs.artifact(raw.cached(SOURCE, f"{BASE}{folder}/{county}.pdf"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"HI": source}, strict=True)
        return cs.write()
