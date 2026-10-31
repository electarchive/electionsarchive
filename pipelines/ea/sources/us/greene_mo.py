import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity, kceb

SOURCE = "us.greene_mo"
VERSION = "1"
PLATFORM = "mo_greene"
MEDIA = "https://vote.greenecountymo.gov/wp-json/wp/v2/media?per_page=100&page={page}&mime_type=application/pdf&orderby=date&order=desc"
WANTED = re.compile(r"(?i)precinct|precint")
EXCLUDED = re.compile(r"(?i)sample|candidate|declaration|GMMOGR|pre-lat")


ROMAN = {
    1: "I",
    2: "II",
    3: "III",
    4: "IV",
    5: "V",
    6: "VI",
    7: "VII",
    8: "VIII",
    9: "IX",
    10: "X",
    11: "XI",
    12: "XII",
    13: "XIII",
    14: "XIV",
}


def _title(title: str) -> str:
    title = re.sub(r"(?i)^for\s+", "", title.strip())
    title = re.sub(r"\bR-(\d+)\b", lambda found: f"R-{ROMAN[int(found[1])]}", title)
    title = re.sub(r"(\d)\s?Y(?:R)?\b", r"\1 YR", title)
    title = re.sub(r"\s*-\s*(\d+ YR)\b", r" \1", title)
    title = re.sub(r"(?i)\b(north|south|east|west) ward\b", "Ward", title)
    if swapped := re.match(r"^(R-[IVX]+)\s+(.+?)\s+-\s+(.*)$", title):
        title = f"{swapped[2]} {swapped[1]} - {swapped[3]}"
    if re.search(r"\bR-[IVX]+\b", title) and "chool" not in title:
        title = re.sub(r"(\bR-[IVX]+\b)", r"\1 School District", title, count=1)
    return re.sub(r"(?i)\b([A-Za-z]+) Fire\b(?! Protection)", r"\1 Fire Protection District", title)


def _links() -> list[str]:
    found = []
    for page in range(1, 5):
        for item in json.loads(raw.cached(SOURCE, MEDIA.format(page=page)).path.read_text()):
            name = item["source_url"].rsplit("/", 1)[1]
            if WANTED.search(name) and not EXCLUDED.search(name):
                found.append(item["source_url"])
    return sorted(found)


def fetch() -> None:
    for page in range(1, 5):
        raw.fetch(SOURCE, MEDIA.format(page=page), suffix=".json")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="29", layer=layer), suffix=".zip")
    for link in _links():
        raw.fetch(SOURCE, link, suffix=".pdf")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    done: set[str] = set()
    for link in _links():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"], capture_output=True, check=False
        ).stdout.decode(errors="replace")
        pages = text.split("\f")
        head = pages[0][:600]
        found = kceb.DATE.search(head)
        if not found or "Results Report" not in head:
            print(f"{link}: layout or date not read; skipped")
            continue
        day = datetime.strptime(f"{found[1].title()} {found[2]} {found[3]}", "%B %d %Y").date()
        if code := re.search(r"\b(\d{2})(\d{2})(\d{2})(?:_\d+)?\b", head.split("\n")[1]):
            day = datetime.strptime(f"20{code[1]}{code[2]}{code[3]}", "%Y%m%d").date()
        if day.isoformat() in done:
            print(f"{link}: {day} already read; skipped")
            continue
        done.add(day.isoformat())
        title = re.sub(r"\s+", " ", head.split("\n")[1]).strip()
        contests: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for page in pages:
            lines = [line for line in page.splitlines() if line.strip()]
            if len(lines) < 5 or not lines[0].startswith(
                ("Summary Results Report", "Precinct Summary Results", "Summary Precinct Results")
            ):
                continue
            precinct = lines[3].strip()
            for node in kceb.electionware(page):
                if node["cast"] is not None and sum(int(choice["total"]) for choice in node["choices"]) != node["cast"]:
                    print(f"{day} {precinct} {node['text']}: candidates do not add to {node['cast']}; skipped")
                    continue
                key = _title(node["text"])
                if re.search(r"(?i)state auditor", key):
                    continue
                if key not in contests:
                    contests[key] = {"text": key, "vote_for": node["vote_for"], "choices": {}}
                    order.append(key)
                for choice in node["choices"]:
                    entry = contests[key]["choices"].setdefault(
                        choice["text"], {"text": choice["text"], "party": choice["party"], "votes": 0, "places": []}
                    )
                    entry["votes"] += int(choice["total"])
                    entry["places"].append((precinct, choice["total"]))
        yield {
            "tenant": tenant,
            "election": day.isoformat(),
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": [
                {
                    "key": str(index),
                    "text": key,
                    "vote_for": contests[key]["vote_for"],
                    "choices": [
                        {**choice, "total": str(choice.pop("votes"))} for choice in contests[key]["choices"].values()
                    ],
                }
                for index, key in enumerate(order)
            ],
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
