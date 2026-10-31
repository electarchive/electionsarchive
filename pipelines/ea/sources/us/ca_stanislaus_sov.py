import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_stanislaus_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Stanislaus County Registrar of Voters"
TITLE = "Statement of Vote, precinct results"
LISTING = "https://www.stanvote.gov/Election-Results/Past-Results"
URL = "https://www.stanvote.gov/pdf/past-results/{day:%m-%d-%Y}-sov.pdf"
DAYS = [
    date(2016, 6, 7),
    date(2016, 11, 8),
    date(2018, 6, 5),
    date(2018, 11, 6),
    date(2020, 3, 3),
    date(2020, 11, 3),
    date(2022, 6, 7),
    date(2022, 11, 8),
    date(2024, 3, 5),
    date(2024, 11, 5),
    date(2026, 6, 2),
]
METHOD = (
    "Precinct totals from Stanislaus County's Statement of Vote (cumulative totals per precinct), matched to the "
    "contest by candidate and checked against its official totals."
)
REPORT = re.compile(r"Results Report - .*Page \d+\s*$")
COLUMNS = re.compile(r"Registration|Ballots Cast|Turnout \(%\)")
ROW = re.compile(r"^(\d{5,7})\s+([\d,]+)\s+([\d,]+)\s+[\d.]+\s+((?:[\d,]+\s*)+)$")


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, URL.format(day=day), suffix=".pdf")


def headings(lines: list[str]) -> list[str]:
    fragments: list[tuple[int, str, int]] = []
    for number, line in enumerate(lines):
        blanked = COLUMNS.sub(lambda match: " " * len(match[0]), line)
        for found in re.finditer(r"\S+(?: \S+)*", blanked):
            fragments.append((found.start(), found[0], number))
    merged: list[tuple[int, str, int]] = []
    for start, text, number in sorted(fragments):
        if merged and abs(merged[-1][0] - start) <= 4 and merged[-1][2] != number:
            merged[-1] = (
                merged[-1][0],
                f"{merged[-1][1]} {text}" if merged[-1][2] < number else f"{text} {merged[-1][1]}",
                number,
            )
        else:
            merged.append((start, text, number))
    return [text for _, text, _ in merged]


def parse() -> None:
    records = []
    for day in DAYS:
        url = URL.format(day=day)
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        for page in text.split("\f"):
            lines = page.splitlines()
            totals = next((i for i, line in enumerate(lines) if line.strip() == "Cumulative Totals"), None)
            first = next((i for i, line in enumerate(lines) if REPORT.search(line)), None)
            marker = next((i for i, line in enumerate(lines) if line.startswith("Precinct")), None)
            if totals is None or first is None or marker is None or not first < totals < marker:
                continue
            contest = " ".join(line.strip() for line in lines[first + 1 : totals] if line.strip())
            names = headings(lines[totals + 1 : marker])
            for line in lines[marker + 1 :]:
                found = ROW.match(line.strip())
                if found is None:
                    continue
                counts = [int(value.replace(",", "")) for value in found[4].split()]
                if len(counts) != len(names):
                    break
                for name, votes in zip(names, counts, strict=True):
                    records.append(
                        (
                            day,
                            url,
                            contest,
                            found[1],
                            int(found[2].replace(",", "")),
                            int(found[3].replace(",", "")),
                            name,
                            votes,
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        records,
        schema=["day", "url", "contest", "precinct", "registered", "ballots", "candidate", "votes"],
        orient="row",
    )
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day").agg(pl.col("contest").n_unique(), pl.col("precinct").n_unique(), pl.len()).sort("day"))


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Stanislaus County's contests by precinct from the Statements of Vote",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="06099",
        place="STANISLAUS",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
