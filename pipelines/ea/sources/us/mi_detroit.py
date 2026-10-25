import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ea import portals, raw
from ea.sources.us import enr_clarity

SOURCE = "us.mi_detroit"
PLATFORM = "mi_detroit"
LISTING = "https://detroitmi.gov/departments/elections/election-results-maps"
ELECTIONS = {
    "68666": (date(2022, 11, 8), "2022 November General Election Summary Report"),
    "102976": (date(2024, 11, 5), "November 5, 2024 State General Election Results"),
}
SCHOOL = re.compile(r"(?i)school")
NUMBER = re.compile(r"^-?[\d,]+$")


def pdf(media: str) -> str | None:
    status, body, _ = portals.get(f"https://detroitmi.gov/media/{media}")
    found = (
        re.search(r'(?:href|src)="([^"]+\.pdf)"', body.decode("utf-8", "replace"), re.IGNORECASE)
        if status == 200
        else None
    )
    return f"https://detroitmi.gov{found[1]}" if found else None


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="26", layer=layer), suffix=".zip")
    for media in ELECTIONS:
        if link := pdf(media):
            raw.fetch(SOURCE, link, suffix=".pdf")
        else:
            raise RuntimeError(f"no results PDF found on detroitmi.gov/media/{media}")


def contests(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        header = re.fullmatch(r"\s*(.+?)\s*\(Vote for (\d+)\)\s*", line)
        if header is None or not SCHOOL.search(header[1]) or "Precincts Reported" in header[1]:
            continue
        choices: list[dict[str, Any]] = []
        total = None
        position = index + 1
        while position < len(lines):
            current = lines[position].strip()
            position += 1
            if current.startswith("Total Votes"):
                total = int(current.split()[-1].replace(",", ""))
                break
            if re.fullmatch(r"(.+?)\s*\(Vote for \d+\)", current):
                break
            tokens = re.split(r"\s{2,}", current)
            numbers = [token for token in tokens if NUMBER.match(token)]
            if not numbers or tokens[0] in ("Times Cast", "Candidate"):
                continue
            name = tokens[0]
            if NUMBER.match(name):
                previous = choices.pop() if choices and choices[-1]["total"] is None else None
                fragment = lines[position - 2].strip() if previous is None else previous["text"]
                continuation = lines[position].strip()
                name = f"{fragment} {continuation}"
                position += 1
            party = (
                tokens[1] if len(tokens) > 1 and not NUMBER.match(tokens[1]) and not tokens[1].endswith("%") else None
            )
            choices.append(
                {
                    "text": name,
                    "party": None if party in (None, "NP") else party,
                    "total": numbers[-1].replace(",", ""),
                    "places": [],
                }
            )
        if total is not None and choices and sum(int(choice["total"]) for choice in choices) == total:
            yield {"key": f"{start}", "text": header[1], "vote_for": int(header[2]), "choices": choices}
            start += 1


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for media, (day, title) in ELECTIONS.items():
        link = pdf(media)
        if link is None:
            continue
        path = raw.cached(SOURCE, link).path
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        yield {
            "tenant": LISTING,
            "election": media,
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": list(contests(text)),
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.endswith(".pdf"))
