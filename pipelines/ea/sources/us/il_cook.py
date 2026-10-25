import html
import io
import re
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.il_cook"
PLATFORM = "il_cook"
LISTING = "https://www.cookcountyclerkil.gov/elections/results-and-election-data/election-data/precinct-canvasses"
FILES = "https://www.cookcountyclerkil.gov/sites/default/files"
ELECTIONS = {
    "2023-01/Precinct%20Canvass_20220719%20%28June%29.xlsx": (
        date(2022, 6, 28),
        "June 28, 2022 Primary Election",
        True,
    ),
    "2023-01/Precinct%20Canvass_20221128%20%28November%29.xlsx": (
        date(2022, 11, 8),
        "November 8, 2022 General Election",
        False,
    ),
    "2026-01/precinct-canvass---031924.xlsx": (date(2024, 3, 19), "March 19, 2024 Primary Election", True),
    "2026-01/precinct-canvass_20241105.xlsx": (date(2024, 11, 5), "November 5, 2024 General Election", False),
}
PARTIES = {"DEM": "Democratic", "REP": "Republican"}
CHICAGO = "https://chicagoelections.gov/elections/results/{election}"
CHICAGO_DOWNLOAD = (
    "https://chicagoelections.gov/elections/results/{election}/download?contest={contest}&ward=&precinct="
)
CHICAGO_ELECTIONS = {
    date(2022, 6, 28): {"Democratic": 252, "Republican": 253, None: 255},
    date(2022, 11, 8): {None: 156},
    date(2024, 3, 19): {"Democratic": 205, "Republican": 206, None: 208},
    date(2024, 11, 5): {None: 41},
}
COUNTYWIDE = re.compile(
    r"Cook County|County Board|Board of Review|Water Reclamation|Appellate Court|Judge, .*(?:Circuit|Subcircuit)"
)
INDEX = re.compile(r"(?:^|\s{2,})(\d+) (.+?)(?=\s{2,}\d+ |$)")


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="17", layer=layer), suffix=".zip")
    for path in ELECTIONS:
        raw.fetch(SOURCE, f"{FILES}/{path}", suffix=".xlsx")
    for elections in CHICAGO_ELECTIONS.values():
        for election in elections.values():
            for contest, _ in _chicago_contests(election):
                raw.fetch(SOURCE, CHICAGO_DOWNLOAD.format(election=election, contest=contest), suffix=".xls")


def _chicago_contests(election: int) -> list[tuple[str, str]]:
    page = raw.fetch(SOURCE, CHICAGO.format(election=election), suffix=".html").path.read_text(errors="replace")
    block = re.search(r'name="contest".*?</select>', page, re.DOTALL)
    if block is None:
        raise ValueError(f"Chicago election {election}: no contest list")
    return [
        (value, label)
        for value, label in (
            (value, html.unescape(label).strip())
            for value, label in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)', block[0])
        )
        if COUNTYWIDE.search(label)
    ]


def _chicago(day: date) -> dict[tuple[str | None, str], dict[str, tuple[str, str]]]:
    found: dict[tuple[str | None, str], dict[str, tuple[str, str]]] = {}
    for party, election in CHICAGO_ELECTIONS[day].items():
        for contest, label in _chicago_contests(election):
            workbook = raw.cached(SOURCE, CHICAGO_DOWNLOAD.format(election=election, contest=contest)).path.read_bytes()
            if workbook[:4] not in (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"):
                continue
            label = re.sub(r"\s-\s(?:DEM|REP)$", "", label)
            sheet = pl.read_excel(
                io.BytesIO(workbook),
                has_header=False,
                infer_schema_length=0,
            ).rows()
            names, votes = sheet[0], sheet[1]
            found[(party, label)] = {
                re.sub(r"\W+", "", str(name).casefold()): (str(name), str(votes[i]).replace(",", ""))
                for i, name in enumerate(names)
                if i > 0 and name and name != "%" and str(votes[i]).replace(",", "").isdigit()
            }
    return found


def _name(title: str) -> str:
    title = re.sub(r"^(Clerk|Treasurer|Sheriff|Assessor|Board President), Cook County$", r"Cook County \1", title)
    return re.sub(
        r"^Commissioner, County Board (\d+)\w\w District(?: (\d)yr)?$",
        lambda found: f"Cook County Commissioner District {found[1]}" + (f" {found[2]} year term" if found[2] else ""),
        title,
    )


def _contests(path: Path, day: date) -> Iterator[dict[str, Any]]:
    chicago = _chicago(day)
    book = fastexcel.read_excel(str(path))
    seen: set[tuple[str | None, str]] = set()
    for name in book.sheet_names[1:]:
        sheet = book.load_sheet(name, header_row=None).to_polars()
        rows = [[None if value is None else str(value).strip() for value in row] for row in sheet.rows()]
        header = next((i for i, row in enumerate(rows) if len(row) > 2 and row[2] == "Registered Voters"), None)
        total = next((i for i, row in enumerate(rows) if row[0] == "Contest Total"), None)
        if header is None:
            continue
        townships = [row for row in rows[header + 1 :] if row[1] == "Total"]
        if total is None and not townships:
            continue
        indexed = any(value and value.isdigit() for value in rows[header - 1][3:])
        marks = rows[header - 1] if indexed else [None] * len(rows[header])
        titles = {
            int(number): text for number, text in INDEX.findall(rows[header - 2 if indexed else header - 1][2] or "")
        }
        contest = 1
        columns: dict[int, list[tuple[int, str]]] = {}
        for column in range(7, len(rows[header])):
            if marks[column]:
                contest = int(marks[column]) if marks[column].isdigit() else contest
            if rows[header][column]:
                columns.setdefault(contest, []).append((column, rows[header][column]))
        for number, candidates in columns.items():
            title = titles.get(number) or titles.get(1)
            if title is None:
                continue
            party = None
            if found := re.search(r"\s-\s(DEM|REP)$", title):
                party, title = PARTIES[found[1]], title[: found.start()]
            choices = []
            for column, label in candidates:
                votes = (
                    rows[total][column]
                    if total is not None
                    else str(sum(int(row[column]) for row in townships if row[column] and row[column].isdigit()))
                )
                if votes is None or not votes.isdigit():
                    continue
                if tagged := re.match(r"^([A-Z]{3}) - (.+)$", label):
                    label, tag = tagged[2], tagged[1]
                else:
                    tag = None
                choices.append({"text": label, "party": tag, "total": votes, "places": []})
            if (city := chicago.get((party, title))) is not None:
                merged = {re.sub(r"\W+", "", choice["text"].casefold()): choice for choice in choices}
                for key, (label, votes) in city.items():
                    if key in merged:
                        merged[key]["total"] = str(int(merged[key]["total"]) + int(votes))
                    else:
                        merged[key] = {"text": label, "party": None, "total": votes, "places": []}
                choices = list(merged.values())
            seen.add((party, title))
            if choices:
                yield {
                    "key": f"{name}:{number}",
                    "text": f"{_name(title)} {party}" if party else _name(title),
                    "vote_for": 1,
                    "choices": choices,
                }

    for (party, title), city in chicago.items():
        if (party, title) not in seen and city:
            yield {
                "key": f"chicago:{party}:{title}",
                "text": f"{_name(title)} {party}" if party else _name(title),
                "vote_for": 1,
                "choices": [
                    {"text": label, "party": None, "total": votes, "places": []} for label, votes in city.values()
                ],
            }


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for path, (day, title, primary) in ELECTIONS.items():
        yield {
            "tenant": LISTING,
            "election": path,
            "day": day,
            "title": title,
            "official": True,
            "primary": primary,
            "contests": list(_contests(raw.cached(SOURCE, f"{FILES}/{path}").path, day)),
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.endswith(".xlsx"))
