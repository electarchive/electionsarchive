import html
import os
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES

HARDIN = os.environ.get("EA_HARDIN_URL", "https://hccoky.org/26GResults/display.html")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
LEGISLATURE = re.compile(r"STATE (SENATOR|REPRESENTATIVE) (\d+)(?:st|nd|rd|th) ", re.IGNORECASE)
NUMBER = re.compile(r"^[\d,]+$|^[\d.]+%$")
PARTY = re.compile(r"^[A-Z]{2,4}$")
VOTE_FOR = re.compile(r"\s*-\s*\(Vote for[^)]*\)\s*$", re.IGNORECASE)


def cells(content: bytes) -> list[str]:
    text = content.decode(errors="replace")
    body = re.sub(r"<style.*?</style>", "", text[text.find("<body") :], flags=re.DOTALL)
    return [x.strip() for x in html.unescape(re.sub(r"<[^>]+>", "|", body)).split("|") if x.strip()]


def held(tokens: list[str]) -> date:
    stamp = tokens[tokens.index("OFFICIAL BALLOT FOR HARDIN COUNTY") + 1]
    return datetime.strptime(stamp, "%m/%d/%Y").date()


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    content = get(client, HARDIN, HEADERS)
    if content is None:
        print("KY: no Hardin results page")
        return
    on = held(cells(content))
    if on != DAY:
        print(f"KY: Hardin results page is for {on}, not {DAY}")
        return
    store(HARDIN, content, ".html")


def rows(url: str, path: Path):
    if url != HARDIN:
        return
    tokens = cells(path.read_bytes())
    as_of = (
        datetime.strptime(
            f"{tokens[tokens.index('Run Date') + 1]} {tokens[tokens.index('Run Time') + 1]}", "%m/%d/%Y %I:%M %p"
        )
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .isoformat()
    )
    place = re.fullmatch(r"(\d+) of (\d+).*", tokens[tokens.index("Polling Places Reporting") + 1])
    reporting, total = int(place[1]), int(place[2])
    i = 0
    while i < len(tokens):
        if tokens[i : i + 3] != ["Choice", "Party", "Absentee Mail In"] and tokens[i : i + 2] != ["Choice", "Party"]:
            i += 1
            continue
        title = VOTE_FOR.sub("", tokens[i - 1]).strip()
        i = tokens.index("Total", i) + 1
        if title == "STRAIGHT PARTY":
            continue
        office = EV_OFFICES.fullmatch(title)
        seat = LEGISLATURE.match(title)
        while i < len(tokens) and tokens[i] != "Cast Votes:":
            names: list[str] = []
            while not NUMBER.match(tokens[i]):
                names.append(tokens[i])
                i += 1
            figures = tokens[i : i + 10]
            i += 10
            party = names.pop() if len(names) > 1 and PARTY.match(names[-1]) else ""
            name = " / ".join(names)
            yield {
                "office": ("senate" if seat[1].upper() == "SENATOR" else "house")
                if seat
                else "other"
                if office is None
                else "senate"
                if office["senate"]
                else "governor"
                if office["governor"]
                else "house",
                "contest": title if office is None and seat is None else "",
                "district": f"{seat[2]}:g" if seat else (office["district"] if office else None) or "",
                "party": party,
                "county": "Hardin",
                "candidate": name,
                "write_in": "write-in" in name.lower(),
                "votes": int(figures[8].replace(",", "")),
                "as_of": as_of,
                "reporting": reporting,
                "total": total,
            }
