import csv
import io
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import DAY

KIND = "Primary" if DAY.month < 10 else "General"
URLS = (
    f"https://files.hawaii.gov/elections/files/results/{DAY.year}/{KIND}/media.txt",
    f"https://elections.hawaii.gov/wp-content/results/{DAY.year}%20{KIND}/media.txt",
)
PARTIES = {
    "D": "Democratic",
    "R": "Republican",
    "L": "Libertarian",
    "G": "Green",
    "S": "Socialist",
    "SL": "Socialism and Liberation",
}
STATE = re.compile(
    r"U\.S\. Senator|Governor|Lieutenant Governor|At-Large Trustee|(?:Molokai|Kauai|Maui|Hawaii|Oahu) Resident Trustee|Question #\d+"
)
HOUSE = re.compile(r"U\.S\. Representative, Dist (I+)")
SEAT = re.compile(r"State (Representative|Senator), Dist (\d+)")


def county(precinct: str) -> str | None:
    if not precinct[:2].isdigit():
        return None
    number = int(precinct[:2])
    return "Hawaii" if number <= 8 else "Maui" if number <= 14 else "Kauai" if number <= 17 else "Honolulu"


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    for url in URLS:
        content = get(client, url)
        if content and len(content) > 1000:
            store(url, content, ".txt")
            return
    print(f"HI: no {DAY} {KIND.lower()} results file")


def decode(content: bytes) -> list[list[str]]:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = content.decode("utf-16")
    elif content[1:2] == b"\x00":
        text = content.decode("utf-16-le")
    else:
        text = content.decode("utf-8-sig", errors="replace")
    text = text.lstrip("﻿")
    lines = [line for line in text.splitlines() if "Precinct_Name" in line or line.count(",") + line.count("\t") > 8]
    delimiter = "\t" if lines and lines[0].count("\t") > lines[0].count(",") else ","
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def rows(url: str, path: Path, urls: dict[str, Path]):
    if not url.endswith("/media.txt"):
        return
    table = decode(path.read_bytes())
    header = next((n for n, row in enumerate(table) if any("Precinct_Name" in cell for cell in row)), None)
    if header is None:
        return
    totals: dict[tuple, int] = defaultdict(int)
    done: dict[tuple, set[str]] = defaultdict(set)
    seen: dict[tuple, set[str]] = defaultdict(set)
    for row in table[header + 1 :]:
        if len(row) < 15 or not row[7]:
            continue
        place = county(row[0])
        title = row[7].strip()
        if place is None:
            if any(int(cell or 0) for cell in row[13:15]):
                print(f"HI: votes in precinct {row[0]!r}, {title}")
            continue
        if not (STATE.fullmatch(title) or HOUSE.fullmatch(title) or SEAT.fullmatch(title)):
            continue
        party = row[8].strip()
        key = (place, row[6], title, party, row[10])
        totals[key] += int(row[13] or 0) + int(row[14] or 0)
        seen[key].add(row[0])
        if row[5].strip() == "1":
            done[key].add(row[0])
    for key, votes in totals.items():
        place, _, title, party, name = key
        name = " ".join(name.replace("\n", " ").split())
        prefix = re.match(r"\((\w+)\) ", name)
        name = name[prefix.end() :] if prefix else name
        last, _, first = name.partition(", ")
        first, *suffix = first.split(", ")
        candidate = " ".join([first, last, *suffix]).strip() if first else name
        label = PARTIES.get(prefix[1]) if prefix else PARTIES.get(party)
        seat = SEAT.fullmatch(title)
        house = HOUSE.fullmatch(title)
        office, district = (
            ("senate", "")
            if title == "U.S. Senator"
            else ("governor", "")
            if title == "Governor"
            else ("house", str(len(house[1])))
            if house
            else (("senate" if seat[1] == "Senator" else "house"), f"{int(seat[2])}:g")
            if seat
            else ("state", "")
        )
        yield {
            "office": office,
            "contest": title if office == "state" else "",
            "district": district,
            "party": label or "",
            "scope": PARTIES.get(party, "") if party else "",
            "county": place,
            "candidate": candidate,
            "write_in": "write-in" in name.lower() or name.startswith("(W)"),
            "votes": votes,
            "as_of": None,
            "reporting": len(done[key]),
            "total": len(seen[key]),
        }
