import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.ny_westchester"
VERSION = "1"
PLATFORM = "ny_westchester"
SITE = "https://citizenparticipation.westchestercountyny.gov"
LISTING = SITE + "/election-dates/enrollment-figures-and-election-results"
FIPS = "36"
LINK = re.compile(r'<a[^>]*href="([^"]+\.pdf)"[^>]*>\s*(\d{4}) General Election Canvass Book', re.I)
TITLE = re.compile(r"^\s*(?:\d{4} GENERAL(?: ELECTION)?\s{3,})?(?P<title>[A-Z]\S*(?: \S+)*?)\s{3,}\d+ OF \d+\s*$")
ROW = re.compile(r"^(?P<label>\S.*?)\s+(?P<code>\d{5,6})\s+(?P<numbers>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
TOTAL = re.compile(r"^\s*TOTAL:\s+(?P<numbers>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
CELL = re.compile(r"\S+(?: \S+)*")
SINGLE = re.compile(
    r"MAYOR|SUPERVISOR|COUNCIL DISTRICT|LEGISLA\w+ DISTRICT|TOWN CLERK|CLERK|RECEIVER|SUPERINTENDENT|PRESIDENT|COMPTROLLER|SHERIFF|"
    r"DISTRICT ATTORNEY|TAX COLLECTOR|SURROGATE|COUNTY EXECUTIVE|COUNTY CLERK"
)
TAIL = re.compile(r"TOTAL CANVASS|BLANK ?-? ?VOID|TOTAL BALLOT")


def files() -> list[tuple[str, int]]:
    listing = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    return sorted({(urljoin(SITE, found[1]).replace(" ", "%20"), int(found[2])) for found in LINK.finditer(listing)})


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for link, _ in files():
        raw.fetch(SOURCE, link, suffix=".pdf")


def numbers(text: str) -> list[int]:
    return [int(token.replace(",", "")) for token in text.split()]


def page(lines: list[str]) -> dict[str, Any] | None:
    start = next((n for n, line in enumerate(lines) if TITLE.match(line)), None)
    twd = next((n for n, line in enumerate(lines) if line.strip().startswith("TWD")), None)
    if start is None or twd is None or twd < start:
        return None
    title = TITLE.match(lines[start])["title"].strip()
    head = [line for line in lines[start + 1 : twd + 1] if line.strip()]
    parties: list[tuple[float, str]] = []
    names: list[list[tuple[float, str]]] = []
    inline = [
        ((match.start() + match.end()) / 2, match.group())
        for match in CELL.finditer(lines[twd].replace("TWD", "   ", 1))
        if not TAIL.search(match.group())
    ]
    if inline:
        parties = [(center, "") for center, _ in inline]
        names = [inline]
    else:
        parties = [
            ((match.start() + match.end()) / 2, match.group())
            for match in CELL.finditer(head[0] if head else "")
            if not TAIL.search(match.group())
        ]
        for line in head[1:-1]:
            names.append(
                [
                    ((match.start() + match.end()) / 2, match.group())
                    for match in CELL.finditer(TAIL.sub(lambda hit: " " * len(hit.group()), line))
                    if match.group().strip()
                ]
            )
    if not parties:
        return None
    columns: list[list[str]] = [[] for _ in parties]
    for line in names:
        for center, text in line:
            nearest = min(range(len(parties)), key=lambda n: abs(parties[n][0] - center))
            columns[nearest].append(text)
    rows: list[tuple[str, list[int]]] = []
    printed: list[int] | None = None
    for line in lines[twd + 1 :]:
        total = TOTAL.match(line)
        if total:
            printed = numbers(total["numbers"])
            break
        row = ROW.match(line)
        if row:
            rows.append((re.sub(r"\s+", " ", row["label"]).strip(), numbers(row["numbers"])))
    if not rows:
        return None
    return {
        "title": title,
        "parties": [party for _, party in parties],
        "names": [" ".join(words) for words in columns],
        "rows": rows,
        "printed": printed,
    }


def contests(text: str) -> Iterator[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for body in text.split("\f"):
        found = page(body.split("\n"))
        if found is None:
            continue
        key = found["title"]
        entry = seen.setdefault(key, {"title": key, "columns": None, "rows": [], "good": True})
        width = len(found["parties"])
        if any(len(values) != width + 3 and len(values) != width for _, values in found["rows"]):
            entry["good"] = "row width"
            continue
        signature = list(zip(found["parties"], found["names"], strict=True))
        if entry["columns"] is None:
            entry["columns"] = signature
        elif entry["columns"] != signature:
            entry["good"] = f"columns {entry['columns']} vs {signature}"
            continue
        sums = [sum(values[n] for _, values in found["rows"]) for n in range(len(found["rows"][0][1]))]
        if found["printed"] is not None and found["printed"] != sums:
            entry["good"] = f"sums {sums} vs {found['printed']}"
            continue
        entry["rows"].extend(found["rows"])
    for entry in seen.values():
        if entry["good"] and entry["rows"]:
            yield entry
        elif entry["rows"]:
            print(f"{entry['title']}: {entry['good']}; skipped")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for link, year in files():
        path = raw.cached(SOURCE, link).path
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode(
            errors="replace"
        )
        first = date(year, 11, 1)
        day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        nodes = []
        for n, node in enumerate(contests(text)):
            width = len(node["columns"])
            options: dict[str, tuple[str | None, list[int]]] = {}
            for column, (party, name) in enumerate(node["columns"]):
                if not name or name in ("BLANK", "VOID"):
                    continue
                label = "Write-ins" if party == "W/I" or name in ("IRREGULAR", "SCATTERING") else name
                votes = [values[column] for _, values in node["rows"]]
                previous = options.get(label)
                options[label] = (
                    previous[0] if previous else (party or None) if label != "Write-ins" else None,
                    [a + b for a, b in zip(previous[1], votes, strict=True)] if previous else votes,
                )
            if not options or width == 0:
                continue
            nodes.append(
                {
                    "key": str(n),
                    "text": node["title"],
                    "vote_for": 1 if SINGLE.search(node["title"]) else 0,
                    "choices": [
                        {
                            "text": label,
                            "party": party,
                            "total": str(sum(votes)),
                            "places": [
                                (place, str(vote)) for (place, _), vote in zip(node["rows"], votes, strict=True)
                            ],
                        }
                        for label, (party, votes) in options.items()
                    ],
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
