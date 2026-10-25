import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.ny_onondaga"
VERSION = "1"
PLATFORM = "ny_onondaga"
SITE = "https://hubdocs.blob.core.windows.net/docs/ongov/rec/"
LISTING = "https://onondaga.gov/elections/election-results/"
FIPS = "36"
BOOKS = (
    (date(2019, 11, 5), SITE + "28266/attachments/GE19OFFICIALRESULTS.pdf"),
    (date(2020, 11, 3), SITE + "28267/attachments/GE20OFFICIALRESULTS.pdf"),
    (date(2021, 11, 2), SITE + "29133/attachments/GE21OFFICIALRESULTS.pdf"),
    (date(2022, 11, 8), SITE + "38200/attachments/GE22%20OFFICIAL%20RESULTS.pdf"),
    (date(2023, 11, 7), SITE + "44468/attachments/GE23%20Official%20Results.pdf"),
    (date(2024, 11, 5), SITE + "52654/attachments/GE24%20Official%20Results.pdf"),
    (date(2025, 11, 4), SITE + "60179/attachments/2025%20General%20Election.pdf"),
)
HEAD = re.compile(r"^\s*A WHOLE NUMBER")
TABLE = re.compile(r"^Wards/Towns")
TOKEN = re.compile(r"\S+(?: \S+)*")
ROW = re.compile(r"^(?P<label>\S.*?)?\s{2,}(?P<district>\d{2,3}[A-Z]?)\s{2,}(?P<numbers>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
TOTAL = re.compile(r"^(?:WARD|TOWN|GRAND) TOTAL\s+(?P<numbers>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
NUMBERS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6}
FOR = re.compile(
    r"\s*\(?VOTE FOR (\d+|ONE|TWO|THREE|FOUR|FIVE|SIX)\)?(?: - VOTE FOR (?:\d+|ONE|TWO|THREE|FOUR|FIVE|SIX))?\s*$"
)
CHOICE = re.compile(r"^(?P<name>.+?)(?: \((?P<party>[A-Z ]+)\))?$")


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for _, url in BOOKS:
        raw.fetch(SOURCE, url, suffix=".pdf")


def header(lines: list[str]) -> list[str]:
    entries: list[list[Any]] = []
    row = 0
    for number, line in enumerate(lines):
        for token in TOKEN.finditer(line):
            text = token.group()
            found = re.match(r"^([A-Z]) (\S.*)$", text)
            if found and found[1] == chr(ord("A") + len(entries)):
                entries.append([token.start(), found[2], number])
                row = number
            elif entries:
                current = [entry for entry in entries if entry[2] == row and entry[0] <= token.start()]
                owner = max(current, key=lambda entry: entry[0]) if current else entries[-1]
                owner[1] += " " + text
    return [entry[1] for entry in entries]


def contests(text: str) -> Iterator[dict[str, Any]]:
    lines = text.split("\n")
    seen: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    index = 0
    while index < len(lines):
        if not HEAD.match(lines[index]):
            index += 1
            continue
        start = index
        title = ""
        back = start - 1
        while back >= 0 and not title:
            title = lines[back].strip()
            back -= 1
        if title.startswith("Summary --"):
            index += 1
            continue
        end = start
        while end < len(lines) and not TABLE.match(lines[end]):
            end += 1
        names = header(lines[start:end])
        title = re.sub(r"\s*Rows Continued\s*$", "", title).strip()
        counted = (FOR.search(title) or [None, "1"])[1]
        vote_for = NUMBERS.get(counted) or int(counted)
        title = re.sub(
            r"\bCOUNCILORS?\b", "COUNCIL MEMBER", FOR.sub("", re.sub(r"\(VOTE FOR \w+\)", "", title)).strip(" -")
        )
        key = (title, tuple(names))
        entry = seen.setdefault(key, {"text": title, "names": names, "vote_for": vote_for, "rows": [], "grand": None})
        label = ""
        cursor = end + 1
        while cursor < len(lines) and not HEAD.match(lines[cursor]) and "Page " not in lines[cursor]:
            line = lines[cursor]
            cursor += 1
            if line.startswith("Summary --"):
                break
            total = TOTAL.match(line.strip())
            if total:
                if line.strip().startswith("GRAND"):
                    entry["grand"] = [int(n.replace(",", "")) for n in total["numbers"].split()]
                continue
            row = ROW.match(line)
            if row:
                label = (row["label"] or label).strip()
                numbers = [int(n.replace(",", "")) for n in row["numbers"].split()]
                entry["rows"].append((f"{label} {row['district']}", numbers))
        index = cursor
    for entry in seen.values():
        width = len(entry["names"])
        rows = [row for row in entry["rows"] if len(row[1]) >= width]
        if not rows or not entry["grand"] or len(entry["grand"]) < width:
            continue
        if [sum(values[n] for _, values in rows) for n in range(width)] != entry["grand"][:width]:
            print(f"{entry['text']}: precincts do not add up; skipped")
            continue
        yield {**entry, "rows": rows}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for day, url in BOOKS:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True
        ).stdout.decode(errors="replace")
        nodes = []
        for n, node in enumerate(contests(text)):
            choices = []
            for column, label in enumerate(node["names"]):
                if label.startswith("WHOLE NUMBER"):
                    continue
                found = CHOICE.match(label)
                write_in = label.upper().startswith("WRITE-IN")
                choices.append(
                    {
                        "text": "Write-ins" if write_in else found["name"],
                        "party": None if write_in else found["party"],
                        "total": str(sum(values[column] for _, values in node["rows"])),
                        "places": [(place, str(values[column])) for place, values in node["rows"]],
                    }
                )
            nodes.append({"key": str(n), "text": node["text"], "vote_for": node["vote_for"], "choices": choices})
        yield {
            "tenant": tenant,
            "election": str(day),
            "day": day,
            "title": f"{day.year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE) or link == LISTING)
