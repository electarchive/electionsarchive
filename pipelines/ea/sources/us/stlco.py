import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.stlco"
VERSION = "2"
PLATFORM = "mo_stlco"
ROOT = "https://extcontent.stlouisco.com/BOE/eResults/"
PARTIES = {"REP": "Republican", "DEM": "Democratic", "LIB": "Libertarian", "CON": "Constitution", "GRN": "Green"}


def _files() -> dict[str, str]:
    found = {}
    for code in re.findall(r"eResults/(el\d{6})/", raw.cached(SOURCE, ROOT).path.read_text(errors="replace")):
        listing = raw.fetch(SOURCE, f"{ROOT}{code}/", suffix=".html").path.read_text(errors="replace")
        if csv_file := re.search(r'HREF="(/BOE/eResults/' + code + r'/[^"]*\.csv)"', listing, re.I):
            found[code] = f"https://extcontent.stlouisco.com{csv_file[1]}"
    return found


def fetch() -> None:
    raw.fetch(SOURCE, ROOT, suffix=".html")
    for fips in ("29",):
        for layer in enr_clarity.LAYERS:
            raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=fips, layer=layer), suffix=".zip")
    for link in _files().values():
        raw.fetch(SOURCE, link, suffix=".csv")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for code, link in sorted(_files().items()):
        day = date(2000 + int(code[2:4]), int(code[4:6]), int(code[6:8]))
        contests: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        orders: dict[str, list[str]] = defaultdict(list)
        text = raw.cached(SOURCE, link).path.read_bytes().decode("latin-1")
        for row in csv.DictReader(io.StringIO(text)):
            title = row["Contest Title"].strip()
            choice = re.sub(r"(?i)\s*\(disqualified[^)]*\)", "", row["Choice Name"]).strip()
            votes = row["Total Votes"].strip()
            if not votes.lstrip("-").isdigit():
                continue
            entry = contests[title].setdefault(
                choice,
                {
                    "text": choice,
                    "party": PARTIES.get(row.get("Choice Party") or row.get("Contest Party") or ""),
                    "votes": 0,
                    "places": [],
                },
            )
            entry["votes"] += int(votes)
            entry["places"].append((row["#Precinct"].strip(), votes))
        yield {
            "tenant": tenant,
            "election": code,
            "day": day,
            "title": "Primary Election" if day.month == 8 else "Election",
            "official": True,
            "primary": False,
            "contests": [
                {
                    "key": str(index),
                    "text": title,
                    "vote_for": 1,
                    "choices": [{**choice, "total": str(choice.pop("votes"))} for choice in choices.values()],
                }
                for index, (title, choices) in enumerate(contests.items())
            ],
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
