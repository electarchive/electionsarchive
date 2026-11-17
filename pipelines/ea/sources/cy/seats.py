"""Cyprus: declared seats of the House of Representatives and European Parliament elections.

The Ministry's results site prints votes only, so the seats of each list come from the English Wikipedia articles on
the elections, pinned to the revision current at retrieval. The article's national results table names each list with
its votes and seats; the constituency table of the House articles gives the seats of each district. The articles for
the House elections of 2001, 2006, and 2011 give no district seats, so those elections carry none. Every row is matched to a ballot line of the Ministry's island total by its exact votes, and the seats
must add up to the House's 56 or the delegation's six, to each district's seats, and to each list's national seats.
"""

import re
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

import httpx

from ea import raw

SOURCE = "cy.moi"
WIKI = "https://en.wikipedia.org/w/index.php?"
PAGES = {
    date(2004, 6, 13): "2004 European Parliament election in Cyprus",
    date(2009, 6, 7): "2009 European Parliament election in Cyprus",
    date(2014, 5, 25): "2014 European Parliament election in Cyprus",
    date(2016, 5, 22): "2016 Cypriot legislative election",
    date(2019, 5, 26): "2019 European Parliament election in Cyprus",
    date(2021, 5, 30): "2021 Cypriot legislative election",
    date(2024, 6, 9): "2024 European Parliament election in Cyprus",
    date(2026, 5, 24): "2026 Cypriot legislative election",
}
# The constituency table of each House article: the heading above it, and the cells per list (votes, share, seats, or
# share and seats).
TABLES = {
    date(2016, 5, 22): ("By district", 3),
    date(2021, 5, 30): ("Distribution by constituency", 2),
    date(2026, 5, 24): ("Distribution by constituency", 2),
}
DISTRICTS = {"Nicosia": 1, "Kyrenia": 2, "Famagusta": 3, "Larnaca": 4, "Limassol": 5, "Paphos": 6}
SEATS = {"parliament": 56, "euro": 6}


@dataclass(frozen=True)
class Row:
    target: str
    votes: int
    seats: int


def url(title: str) -> str:
    return WIKI + urlencode({"title": title.replace(" ", "_"), "action": "raw"})


def fetch() -> None:
    for title in PAGES.values():
        address = url(title)
        if address in raw.keys(SOURCE):
            continue
        for attempt in range(8):
            try:
                raw.fetch(SOURCE, address, suffix=".wikitext")
                break
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 429 or attempt == 7:
                    raise
                time.sleep(10 * (attempt + 1))
        time.sleep(3)


def artifact(day: date) -> raw.Artifact:
    return raw.cached(SOURCE, url(PAGES[day]))


def text(day: date) -> str:
    return artifact(day).path.read_text()


def _target(cell: str) -> str:
    link = re.search(r"\[\[([^\]|]+)", cell)
    return (link.group(1) if link else re.sub(r"\{\{.*?\}\}|<[^>]+>", "", cell)).strip()


def national(day: date) -> list[Row]:
    """The article's results table: each list's link target, votes, and seats."""
    page = text(day)
    start = page.index("{{Election results")
    block = page[start : page.index("\n}}", start)]
    rows = [
        Row(
            _target(match.group(2)),
            int(match.group(3).replace(",", "")),
            int(match.group(4)),
        )
        for match in re.finditer(r"^\|party(\d+)=(.+?)\|votes\1=([\d,]+)\|seats\1=(\d+)", block, re.MULTILINE)
    ]
    if not rows:
        raise ValueError(f"{day}: no results table")
    return rows


def _cells(line: str) -> str:
    return line[1:].rsplit("|", 1)[-1]


def _number(cell: str) -> int:
    return int(re.sub(r"[^\d]", "", cell))


def districts(day: date, rows: list[Row]) -> dict[int, dict[str, int]]:
    """Seats of each list in each district, keyed by the list's link target in the national table."""
    heading, stride = TABLES[day]
    page = text(day)
    found = re.search(rf"={{2,}}\s*{heading}\s*={{2,}}", page)
    if found is None:
        raise ValueError(f"{day}: no constituency table")
    start = found.start()
    table = page[start : page.index("\n|}", start)]
    winners = [row for row in rows if row.seats > 0]
    out: dict[int, dict[str, int]] = {}
    for chunk in table.split("\n|-")[1:]:
        lines = [line for line in chunk.strip().splitlines() if line.startswith("|")]
        if not lines:
            continue
        name = re.search(r"\[\[([A-Za-z]+) District", lines[0])
        if name is None:
            continue
        cells = [_cells(line) for line in lines[1:]]
        seat_cells = cells[stride - 1 :: stride][: len(winners)]
        if len(seat_cells) != len(winners):
            raise ValueError(f"{day}: {name.group(1)} has {len(seat_cells)} list columns for {len(winners)} winners")
        out[DISTRICTS[name.group(1)]] = {
            row.target: _number(cell) for row, cell in zip(winners, seat_cells, strict=True)
        }
    return out


def check(day: date, family: str, rows: list[Row], by_district: dict[int, dict[str, int]] | None) -> None:
    """The seats the article declares must add up to the body's seats and to each list's national seats."""
    total = sum(row.seats for row in rows)
    if total != SEATS[family]:
        raise ValueError(f"{day}: the lists hold {total} seats, not {SEATS[family]}")
    if by_district is None:
        return
    for row in rows:
        got = sum(seats.get(row.target, 0) for seats in by_district.values())
        if got != row.seats:
            raise ValueError(f"{day}: {row.target} has {row.seats} seats nationally but {got} in its districts")
