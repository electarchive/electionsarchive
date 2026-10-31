import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.ny_saratoga"
VERSION = "1"
PLATFORM = "ny_saratoga"
SITE = "https://www.saratogacountyny.gov"
LISTING = SITE + "/departments/board-of-elections/election-results/archive/"
FIPS = "36"
FILE = re.compile(
    r'href="(https://www\.saratogacountyny\.gov/wp/wp-content/uploads/[^"]*BOE-General-Election-Results-By-District-(20\d\d)\.pdf)"'
)
CELL = re.compile(r"\S+(?: \S+)*")
NUMBER = re.compile(r"\d[\d,]*")
ROW = re.compile(r"^(?P<label>W:\d+ D:\d+)\s")
SECTION = re.compile(r"^(?P<place>\S.*?) - Page \d+\s*$")
TITLE = re.compile(r"^(?P<where>County Wide|\S.*?) - (?P<office>[A-Z0-9][^\n]+?)\s*$")
SINGLE = re.compile(
    r"SUPERVISOR|TOWN CLERK|HIGHWAY SUPERINTENDENT|TAX COLLECTOR|RECEIVER OF TAXES|MAYOR|SURROGATE|SHERIFF|DISTRICT ATTORNEY|COUNTY CLERK|COMPTROLLER"
)
SKIP_COLUMNS = {"Whole Number", "Blank Votes", "Void Votes", "Blank", "Void"}


def files() -> list[tuple[str, int]]:
    listing = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    return sorted({(found[1], int(found[2])) for found in FILE.finditer(listing)}, key=lambda entry: entry[1])


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for link, _ in files():
        raw.fetch(SOURCE, link, suffix=".pdf")


def columns(party: str, names: list[str]) -> list[tuple[int, int, str, str | None]]:
    heads = [(m.start(), m.end(), m.group()) for m in CELL.finditer(party)]
    found = []
    for start, end, label in heads:
        found.append([start, end, label, []])
    for line in names:
        for m in CELL.finditer(line):
            center = (m.start() + m.end()) / 2
            nearest = min(range(len(heads)), key=lambda n: abs((heads[n][0] + heads[n][1]) / 2 - center))
            found[nearest][3].append(m.group())
    return [
        (start, end, label, None if label in SKIP_COLUMNS else " ".join(words) or None)
        for start, end, label, words in found
    ]


def contests(text: str) -> Iterator[dict[str, Any]]:
    lines = text.split("\n")
    current: dict[str, Any] | None = None
    head: list[tuple[int, int, str, str | None]] = []
    place = ""
    seen: dict[str, dict[str, Any]] = {}
    for index, line in enumerate(lines):
        if line.strip() in ("GENERAL ELECTION", "PRIMARY ELECTION") and index + 1 < len(lines):
            found = TITLE.match(lines[index + 1])
            if found:
                key = f"{found['where']} - {found['office']}"
                current = seen.setdefault(
                    key, {"text": found["office"], "where": found["where"], "rows": [], "heads": []}
                )
                head = []
            continue
        if current is None:
            continue
        if "Whole Number" in line and index + 1 < len(lines):
            block = []
            for other in lines[index + 1 :]:
                if not other.strip():
                    break
                block.append(other)
            head = columns(line, block)
            current["heads"].append(head)
            continue
        section = SECTION.match(line)
        if section:
            place = section["place"].strip()
            continue
        row = ROW.match(line)
        if row and head:
            values: dict[int, int] = {}
            for number in NUMBER.finditer(line[row.end() :]):
                end = row.end() + number.end()
                nearest = min(range(len(head)), key=lambda n: abs(head[n][1] - end))
                if abs(head[nearest][1] - end) <= 5:
                    values[nearest] = int(number.group().replace(",", ""))
            current["rows"].append((f"{place} {row['label']}", head, values))
    for entry in seen.values():
        options: dict[tuple[str, str], list[tuple[str, int]]] = {}
        for label, head, values in entry["rows"]:
            for n, (_, _, party, name) in enumerate(head):
                if name is not None:
                    options.setdefault((name, party), []).append((label, values.get(n, 0)))
        if not options:
            continue
        single = bool(SINGLE.search(entry["text"]))
        yield {"text": entry["text"], "where": entry["where"], "options": options, "vote_for": 1 if single else 0}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for link, year in files():
        path = raw.cached(SOURCE, link).path
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode(
            errors="replace"
        )
        stamp = re.search(r"General Election (\d\d)/(\d\d)/(\d{4})", text)
        if stamp:
            day = date(int(stamp[3]), int(stamp[1]), int(stamp[2]))
        else:
            first = date(year, 11, 1)
            day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        nodes = []
        for n, node in enumerate(contests(text)):
            choices = []
            for (name, party), rows in node["options"].items():
                label = "Write-ins" if party == "WRT" else name
                choices.append(
                    {
                        "text": label,
                        "party": None if party == "WRT" else party,
                        "total": str(sum(votes for _, votes in rows)),
                        "places": [(place, str(votes)) for place, votes in rows],
                    }
                )
            nodes.append(
                {
                    "key": str(n),
                    "text": re.sub(r"(?i)\bSpgs\b", "Springs", node["text"]),
                    "vote_for": node["vote_for"],
                    "choices": choices,
                }
            )
        yield {
            "tenant": tenant,
            "election": str(year),
            "day": day,
            "title": f"{year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE))
