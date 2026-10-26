import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity
from ea.store import DATA

SOURCE = "us.nj_hudson"
VERSION = "1"
PLATFORM = "nj_hudson"
INBOX = DATA / "inbox" / "manual" / "MF-095"
URL = "https://www.hudsoncountyclerk.org/elections-archives/#{name}"
FIPS = "34"
BOOKS = (
    (
        date(2023, 11, 7),
        "2023-general/Hudson-County-November-7-2023-General-Election-Official-Results-by-MWD-Results-with-Voting-Method-2.pdf",
    ),
    (date(2024, 11, 5), "2024-general/2024-General-Election-MWD-Results-with-Voting-Method-Details-Official.pdf"),
    (date(2025, 11, 4), "2025-general/MWD-Results-with-Contest-Level-and-voting-Method-Details.pdf"),
)
PAGE = re.compile(r"<page [^>]*>(.*?)</page>", re.S)
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
NUMBER = re.compile(r"^\d[\d,]*$")
DISTRICT = re.compile(r"^\d+[A-Z]?$")
PARTIES = {"DEM", "REP", "LIB", "GRN"}
KINDS = re.compile(r"^(?P<office>.*?) (?P<kind>CITY|TOWN|BOROUGH|TOWNSHIP|VILLAGE) OF (?P<place>.+)$")
WARD = re.compile(r"^(?:Ward Councilperson|Council - Ward \d+) (?P<place>.+?) Ward (?P<ward>\w+)$")


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    _, index = raw._index(SOURCE)
    for _, name in BOOKS:
        url = URL.format(name=Path(name).name)
        if url not in index:
            raw.store(
                SOURCE,
                url,
                (INBOX / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def pages(path: Path) -> Iterator[dict[str, Any]]:
    text = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True).stdout.decode(
        errors="replace"
    )
    carry: tuple[int, str, list[tuple[float, int]]] | None = None
    for block in PAGE.finditer(text):
        words = [
            ((float(a) + float(c)) / 2, (float(b) + float(d)) / 2, html.unescape(w))
            for a, b, c, d, w in WORD.findall(block[1])
        ]
        heads = [(x, y) for x, y, w in words if w == "For" and x > 250]
        if not heads:
            continue
        left, top = min(heads, key=lambda head: head[1])
        title = " ".join(w for x, y, w in sorted(words, key=lambda item: item[0]) if abs(y - top) < 3 and x > left)
        seats = next(
            (
                int(words[n + 2][2])
                for n, (x, y, w) in enumerate(words)
                if w == "Vote" and top < y < top + 30 and words[n + 2][2].isdigit()
            ),
            0,
        )
        lines: dict[int, list[tuple[float, str]]] = {}
        for x, y, w in words:
            if y > top + 25:
                lines.setdefault(round(y), []).append((x, w))
        raw_rows: list[tuple[int, str, list[tuple[float, int]]]] = []
        ordered_ys = sorted(lines)
        completed = None
        for y in ordered_ys:
            lead = sorted(lines[y])
            if DISTRICT.match(lead[0][1]) and lead[0][0] < 150 and "District" not in [w for x, w in lead]:
                if carry is not None:
                    completed = (f"{carry[1]} {lead[0][1]}", carry[2])
                break
        carry = None
        for position, y in enumerate(ordered_ys):
            line = sorted(lines[y])
            words_only = [w for x, w in line]
            if "District" not in words_only[:-3]:
                continue
            at = words_only.index("District")
            if line[at + 1][0] < 150:
                number, split = words_only[at + 1], at + 2
            elif position + 1 < len(ordered_ys) and (follow := sorted(lines[ordered_ys[position + 1]])):
                number, split = follow[0][1], at + 1
            else:
                numbers = line[at + 1 :]
                if len(numbers) >= 3 and all(NUMBER.match(w) for x, w in numbers):
                    carry = (y, " ".join(words_only[: at + 1]), [(x, int(w.replace(",", ""))) for x, w in numbers])
                continue
            numbers = line[split:]
            if len(numbers) >= 3 and all(NUMBER.match(w) for x, w in numbers) and DISTRICT.match(number):
                label = " ".join([*words_only[: at + 1], number])
                raw_rows.append((y, label, [(x, int(w.replace(",", ""))) for x, w in numbers]))
        if not raw_rows:
            continue
        if completed is not None:
            raw_rows.append((raw_rows[0][0], *completed))
        first_y = raw_rows[0][0]
        grid = [x for x, value in max(raw_rows, key=lambda entry: len(entry[2]))[2]]
        head = [
            (x, y, w)
            for x, y, w in words
            if x > 148 and top + 25 < y < first_y - 3 and w not in ("Registered", "Voters", "Ballots", "Cast", "Total")
            and not NUMBER.match(w)
        ]
        if not head:
            continue
        occupied = sorted({min(range(len(grid)), key=lambda n: abs(grid[n] - x)) for x, y, w in head})
        columns = [grid[n] for n in occupied]
        data = []
        for y, label, values in raw_rows:
            picked: dict[int, int] = {}
            for x, value in values:
                nearest = min(range(len(grid)), key=lambda n: abs(grid[n] - x))
                if abs(grid[nearest] - x) < 20:
                    picked[nearest] = value
            if all(n in picked for n in occupied):
                data.append((y, label, [picked[n] for n in occupied]))
        if not data:
            continue
        names: list[list[tuple[float, float, str]]] = [[] for _ in columns]
        for x, y, w in head:
            nearest = min(range(len(columns)), key=lambda n: abs(columns[n] - x))
            names[nearest].append((x, y, w))
        labels = []
        for items in names:
            ordered: list[str] = []
            line_x = None
            line_items: list[tuple[float, float, str]] = []
            for item in sorted(items):
                if line_x is not None and item[0] - line_x > 6:
                    ordered.extend(w for _, _, w in sorted(line_items, key=lambda entry: -entry[1]))
                    line_items = []
                if not line_items:
                    line_x = item[0]
                line_items.append(item)
            ordered.extend(w for _, _, w in sorted(line_items, key=lambda entry: -entry[1]))
            labels.append(re.sub(r"- ", "-", " ".join(ordered)))
        yield {"title": title, "seats": seats, "names": labels, "rows": data}


def contests(path: Path) -> Iterator[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for found in pages(path):
        entry = seen.setdefault(found["title"], {"seats": found["seats"], "names": found["names"], "rows": {}})
        if entry["names"] != found["names"]:
            entry["clash"] = True
            continue
        for _, label, values in found["rows"]:
            width = len(entry["names"])
            current = entry["rows"].setdefault(label, [0] * width)
            for n in range(width):
                current[n] += values[n]
    for title, entry in seen.items():
        if entry.get("clash"):
            print(f"{title}: header changed between pages; skipped")
            continue
        yield {"title": title, **entry}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for day, name in BOOKS:
        path = raw.cached(SOURCE, URL.format(name=Path(name).name)).path
        nodes = []
        for n, node in enumerate(contests(path)):
            text = node["title"]
            if found := WARD.match(text):
                text = f"{found['place']} Council Member Ward {found['ward']}"
            elif found := KINDS.match(text):
                text = f"{found['kind'].title()} of {found['place'].title()} {found['office']}"
            choices = []
            for position, label in enumerate(node["names"]):
                party = None
                person = label
                split = re.match(r"^([A-Z]{2,4}) (.+)$", label)
                if split:
                    party = split[1] if split[1] in PARTIES else None
                    person = split[2]
                if re.match(r"(?i)^write.?in$", person):
                    person, party = "Write-ins", None
                choices.append(
                    {
                        "text": person,
                        "party": party,
                        "total": str(sum(values[position] for values in node["rows"].values())),
                        "places": [(place, str(values[position])) for place, values in node["rows"].items()],
                    }
                )
            nodes.append({"key": str(n), "text": text, "vote_for": node["seats"], "choices": choices})
        yield {
            "tenant": tenant,
            "election": f"{day.year}:{Path(name).name[:40]}",
            "day": day,
            "title": f"{day.year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith("https://www.hudsoncountyclerk.org"))
