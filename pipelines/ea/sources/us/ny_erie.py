import re
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import fastexcel
import httpx

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.ny_erie"
VERSION = "1"
PLATFORM = "ny_erie"
SITE = "https://elections.erie.gov"
LISTING = SITE + "/ElectionArch"
FIPS = "36"
FILE = re.compile(r"\./Files/Election Results/((?P<year>\d{4})/(?:(?P<stamp>\d{8})/)?(?P<name>[^\"/]+\.xlsx?))\"")
WANTED = re.compile(r"(?i)general|school[ -]?b(?:oar)?d|bflo|canvass[ -]book|general")
UNWANTED = re.compile(
    r"(?i)primary|prim\b|\b(?:dem|rep|con|gre|wor|ind|ref|wep)\b|(?:^|[-/ ])(?:\d-)?(?:DEM|REP|CON|GRE|WOR|IND|REF|WEP)\.|"
    r"special|congress|consolidation|DistsReporting|conservative|democrat|republican|liberal|independence|working"
)
TOTAL = re.compile(r"(?i)total")
ED = re.compile(r"^(?:[A-Z]{2,6}(?: \d+)? ?\d+(?:-\d+)?[A-Z]?|\d+(?:st|nd|rd|th) District\b.*)$", re.IGNORECASE)
COUNTY_TOTAL = re.compile(r"(?i)^(?:erie county total|office total|total|grand total)$")
SKIP_COLUMNS = re.compile(
    r"(?i)^(?:blank|void|total|grand total|spec pres voters|fed voters|registered|blank, void.*)$"
)
GAP = re.compile(r"\s{2,}|\n")
VOTE_FOR = re.compile(r"(?i)\(?vote for (?:any )?(one|two|three|four|five|\d+)\)?")
COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def files() -> Iterator[tuple[str, str, date]]:
    listing = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    seen: set[str] = set()
    for found in FILE.finditer(listing):
        name, year = found["name"], int(found["year"])
        if year < 2000 or not WANTED.search(name) or UNWANTED.search(name) or name in seen:
            continue
        seen.add(name)
        link = f"{SITE}/Files/Election Results/{found[1]}"
        if found["stamp"]:
            day = datetime.strptime(found["stamp"], "%m%d%Y").date()
        elif re.search(r"(?i)school", name):
            day = date(year, 5, 1)
            day += timedelta(days=(1 - day.weekday()) % 7)
        else:
            day = date(year, 11, 1)
            day += timedelta(days=(7 - day.weekday()) % 7 + 1)
        yield f"{year}:{name}", quote(link, safe=":/()"), day


def fetch() -> None:
    _, index = raw._index(SOURCE)
    if LISTING not in index:
        with httpx.Client(follow_redirects=True, headers={"User-Agent": raw.USER_AGENT}, timeout=60) as client:
            response = client.get(LISTING)
            response.raise_for_status()
            raw.store(
                SOURCE,
                LISTING,
                response.content,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for _, link, _ in files():
        raw.fetch(SOURCE, link, suffix=".xlsx" if link.lower().endswith("xlsx") else ".xls")


def number(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def sheet(rows: list[list[Any]], index: int) -> dict[str, Any] | None:
    head = next((n for n, row in enumerate(rows[:3]) if row and row[0] and str(row[0]).strip() and len(row) > 2), None)
    if head is None or not any(str(cell).strip().upper() == "TOTAL" for cell in rows[head] if cell is not None):
        return None
    cells = [str(cell) if cell is not None else "" for cell in rows[head]]
    title = [part.strip() for part in GAP.split(cells[0].replace("\n", "  ")) if part.strip()]
    if not title:
        return None
    vote_for = 1
    parts = []
    for part in title:
        if found := VOTE_FOR.search(part):
            vote_for = COUNTS.get(found[1].lower()) or int(found[1])
            part = VOTE_FOR.sub("", part).strip()
        if part:
            parts.append(part)
    columns = []
    for column, cell in enumerate(cells[1:], 1):
        pieces = [piece.strip() for piece in GAP.split(cell.strip()) if piece.strip()]
        if not pieces or SKIP_COLUMNS.match(pieces[0]) or SKIP_COLUMNS.match(cell.strip()):
            continue
        columns.append((column, pieces[0], pieces[1] if len(pieces) > 1 else None))
    section = None
    sections: list[str] = []
    places: list[tuple[str, list[int | None]]] = []
    printed: list[int | None] | None = None
    for row in rows[head + 1 :]:
        label = str(row[0]).strip() if row and row[0] is not None else ""
        values = [number(row[column]) if column < len(row) else None for column, _, _ in columns]
        if not label:
            continue
        if all(value is None for value in values):
            if label.isdigit() or label.startswith("20") and len(label) == 4:
                continue
            section = label
            if label not in sections:
                sections.append(label)
            continue
        if TOTAL.search(label):
            if COUNTY_TOTAL.match(label):
                printed = values
            continue
        if ED.match(label):
            district = re.match(r"(\d+)", label)
            places.append((f"{section} {district[1]}" if district and section else label, values))
    named = " ".join(parts).upper()
    if not re.search(r"\b(?:TOWN|CITY|VILLAGE) OF\b|\bCOUNTY\b", named) and (
        len(sections) == 1
        and sections[0].upper() not in named
        or len(sections) <= 12
        and re.match(r"(?i)(?:town|city|village) of ", sections[0] if sections else "")
    ):
        parts.insert(0, sections[0])
    return {
        "key": str(index),
        "title": parts,
        "vote_for": vote_for,
        "columns": columns,
        "places": places,
        "printed": printed,
    }


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for name, link, day in files():
        book = fastexcel.read_excel(str(raw.cached(SOURCE, link).path))
        contests = []
        for index, title in enumerate(book.sheet_names):
            table = book.load_sheet(index, header_row=None).to_polars()
            found = sheet([list(row) for row in table.iter_rows()], index)
            if found is None or not found["places"]:
                continue
            sums = [sum(values[n] or 0 for _, values in found["places"]) for n in range(len(found["columns"]))]
            if found["printed"] is not None and [value or 0 for value in found["printed"]] != sums:
                print(f"{name} {title}: precincts add to {sums}, printed {found['printed']}; skipped")
                continue
            choices = []
            for n, (_, label, party) in enumerate(found["columns"]):
                choices.append(
                    {
                        "text": label,
                        "party": party,
                        "total": str(sums[n]),
                        "places": [(place, str(values[n] or 0)) for place, values in found["places"]],
                    }
                )
            if day.month == 5 and not re.search(r"(?i)buffalo", " ".join(found["title"])):
                found["title"].insert(0, "Buffalo")
            contests.append(
                {
                    "key": found["key"],
                    "text": re.sub(r"\bTFV\b", "To Fill Vacancy", " ".join(found["title"])),
                    "vote_for": found["vote_for"],
                    "choices": choices,
                }
            )
        yield {
            "tenant": tenant,
            "election": name,
            "day": day,
            "title": f"{day.year} {'Buffalo school board' if day.month == 5 else 'general'} election",
            "official": True,
            "primary": False,
            "contests": contests,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE))
