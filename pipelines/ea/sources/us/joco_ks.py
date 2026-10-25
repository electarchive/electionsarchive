import csv
import io
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.joco_ks"
VERSION = "1"
PLATFORM = "ks_joco"
ARCHIVE = "https://www.jocoelection.org/archive"
SKIPPED = re.compile(r"^(OVER|UNDER) VOTES$|^BALLOTS CAST$", re.I)


def _events() -> list[str]:
    page = raw.cached(SOURCE, ARCHIVE).path.read_text(errors="replace")
    return sorted({urljoin(ARCHIVE, path) for path in re.findall(r'href="(/events-elections/[^"]+)"', page)})


def _files() -> dict[str, str]:
    found = {}
    for event in _events():
        page = raw.fetch(SOURCE, event, suffix=".html").path.read_text(errors="replace")
        for href in dict.fromkeys(re.findall(r'href="([^"]*\.csv)"', page, re.I)):
            if "Committee" not in href:
                found[event] = urljoin(event, quote(unquote(href), safe="/:%"))
                break
    return found


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="20", layer=layer), suffix=".zip")
    for link in _files().values():
        raw.fetch(SOURCE, link, suffix=".csv")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for event, link in sorted(_files().items()):
        slug = event.rsplit("/", 1)[1]
        found = re.match(r"([a-z]+)-(\d{1,2})-(\d{4})-(.*?)(?:-\d)?$", slug)
        day = datetime.strptime(f"{found[1].title()} {found[2]} {found[3]}", "%B %d %Y").date()
        rows = list(csv.reader(io.StringIO(raw.cached(SOURCE, link).path.read_bytes().decode("utf-8-sig", errors="replace"))))
        name_col = next(index for index, cell in enumerate(rows[0]) if cell.strip().upper() == "PRECINCT NAME")
        first = next(index for index, row in enumerate(rows) if index > 0 and re.search(r"\d", row[name_col]) and row[name_col].upper() != "PRECINCT NAME")
        titles, names = rows[0], rows[first - 1]
        columns: dict[str, list[tuple[int, str]]] = {}
        for index in range(name_col + 1, len(titles)):
            if not titles[index].strip() or SKIPPED.match(names[index].strip()) or titles[index].upper().startswith(("REGISTERED", "BALLOTS", "TURNOUT")):
                continue
            columns.setdefault(titles[index].strip(), []).append((index, names[index].strip()))
        totals: dict[tuple[str, str], int] = {}
        places: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for row in rows[first:]:
            if len(row) <= name_col or not row[name_col].strip() or row[name_col].strip().upper().startswith("TOTAL"):
                continue
            for title, cells in columns.items():
                for index, name in cells:
                    value = row[index].strip() if index < len(row) else ""
                    if value.isdigit():
                        totals[(title, name)] = totals.get((title, name), 0) + int(value)
                        places.setdefault((title, name), []).append((row[name_col].strip(), value))
        contests = []
        for title, cells in columns.items():
            choices = [
                {"text": name, "party": None, "total": str(totals[(title, name)]), "places": places[(title, name)]}
                for _, name in cells
                if (title, name) in totals
            ]
            if choices:
                contests.append({"key": str(len(contests)), "text": title, "vote_for": 1, "choices": choices})
        yield {
            "tenant": tenant,
            "election": slug,
            "day": day,
            "title": "Primary Election" if "primary" in slug else "Election",
            "official": True,
            "primary": False,
            "contests": contests,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
