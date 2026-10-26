import csv
import html
import io
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ea import raw
from ea.sources.us import enr_clarity, kceb

SOURCE = "us.wyco_ks"
VERSION = "1"
PLATFORM = "ks_wyco"
ARCHIVE = "https://wycovoteskck.gov/election-results-archive"
SKIPPED = re.compile(r"^(OVER|UNDER) VOTES$", re.I)
ANCHOR = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>\s*([^<]*?\d{4} [^<]*? - [A-Z][a-z]+ \d{1,2}, \d{4})\s*</a>')


def _events() -> dict[str, tuple[str, str]]:
    page = raw.cached(SOURCE, ARCHIVE).path.read_text(errors="replace")
    found = {}
    for href, label in ANCHOR.findall(page):
        if href.lower().endswith(".pdf") or "result" not in href:
            continue
        path = urlparse(href).path
        found[path] = (urljoin(ARCHIVE, path), html.unescape(label).strip())
    return found


def _csvs() -> dict[str, tuple[str, str]]:
    found = {}
    for path, (url, label) in _events().items():
        page = raw.cached(SOURCE, url).path.read_text(errors="replace")
        if link := re.search(r'href="([^"]*ByPrecinct[^"]*\.csv|[^"]*StatementOfVotesCast[^"]*\.csv|[^"]*SOVC[^"]*\.csv|[^"]*_Export[^"]*\.csv)"', page, re.I):
            found[path] = (urljoin(ARCHIVE, link[1]), label)
    return found


def _pdfs() -> dict[str, tuple[str, str]]:
    page = raw.cached(SOURCE, ARCHIVE).path.read_text(errors="replace")
    found = {}
    for href, label in ANCHOR.findall(page):
        if href.lower().endswith(".pdf") and href.startswith("/") and int(re.search(r"\d{4}$", label)[0]) >= 2016:
            found[href] = (urljoin(ARCHIVE, href.replace(" ", "%20")), html.unescape(label).strip())
    return found


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="20", layer=layer), suffix=".zip")
    for url, _ in _events().values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url, _ in _csvs().values():
        raw.fetch(SOURCE, url, suffix=".csv")
    for url, _ in _pdfs().values():
        raw.fetch(SOURCE, url, suffix=".pdf")


NAMES = (
    (re.compile(r"(?i)^for\s+"), ""),
    (re.compile(r"(?i)\b(?:unified government|UG)\s+sheriff\b"), "Wyandotte County Sheriff"),
    (re.compile(r"(?i)\b(?:unified government|UG)\s+chief executive/mayor\b"), "Kansas City Mayor"),
    (re.compile(r"(?i)\b(?:unified government|UG)\s+commissioner\b"), "Kansas City Council Member"),
    (re.compile(r"(?i)\bUSD #(202|203|204|500)\b"), lambda found: f"{DISTRICTS[found[1]]} Unified School District {found[1]}"),
    (re.compile(r"(?i)\bward (I{1,3}|IV)\b"), lambda found: f"Ward {len(found[1]) if found[1].upper() != 'IV' else 4}"),
    (re.compile(r"(?i)\b(mayor|city council[\w -]*?) (edwardsville|lake quivira)$"), lambda found: found[1]),
    (re.compile(r"(?i)\bunified school district (?:no\.? ?)?(202|203|204|500)\b"), lambda found: f"{DISTRICTS[found[1]]} Unified School District {found[1]}"),
)
DISTRICTS = {"202": "Turner", "203": "Piper", "204": "Bonner Springs", "500": "Kansas City"}
ROW = re.compile(r"^\s*(?P<name>\S.*?)[\s.]*\s(?P<total>\d[\d,]*)\s+[\d. •]*$")
PARTY = re.compile(r"\s*\((Dem|Rep|Lib|Con|Gre|Ind)\)\s*$", re.I)


def _title(title: str) -> str:
    for pattern, replacement in NAMES:
        title = pattern.sub(replacement, title)
    return title.strip()


def dotted(text: str, joined: bool = False) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*\(VOTE FOR\s*\)\s*(\d+)\s*$", line, re.I) or index == 0:
            continue
        choices: list[dict[str, Any]] = []
        for other in lines[index + 1 : index + 60]:
            stripped = other.strip()
            if not stripped or re.match(r"(?i)\(VOTE FOR\s*\)", stripped):
                if choices:
                    break
                continue
            if stripped.startswith("(WITH"):
                continue
            row = ROW.match(other)
            if not row:
                break
            name = row["name"].strip(" .")
            party = PARTY.search(name)
            if party:
                name = name[: party.start()].strip(" .")
            write_in = name.upper().startswith("WRITE")
            choices.append(
                {
                    "text": "Write-ins" if write_in else name,
                    "party": kceb.PARTIES.get(party[1].upper()) if party else None,
                    "total": row["total"].replace(",", ""),
                    "places": [],
                }
            )
        title = next((lines[back].strip() for back in range(index - 1, max(index - 4, -1), -1) if lines[back].strip()), "")
        if joined:
            above = []
            for back in range(index - 1, max(index - 3, -1), -1):
                candidate = lines[back].strip()
                if (
                    not candidate
                    or candidate.startswith(("(WITH", "NO CANDIDATE", "INVALID", "VOTER TURNOUT", "REMOVED"))
                    or re.search(r"(?i)\.html?\[|(\. ){2}", candidate)
                    or ROW.match(lines[back])
                ):
                    break
                above.insert(0, candidate)
            title = " - ".join(above) or next(
                (lines[back].strip() for back in range(index - 1, max(index - 6, -1), -1) if lines[back].strip() and not re.search(r"(?i)\.html?\[", lines[back])),
                "",
            )
        if choices and title:
            yield {"text": title, "vote_for": int(re.search(r"\d+", line)[0]), "choices": choices, "cast": None}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    yield from _read_pdfs(tenant)
    for path, (url, label) in sorted(_csvs().items()):
        day = datetime.strptime(label.rsplit(" - ", 1)[1], "%B %d, %Y").date()
        rows = list(csv.reader(io.StringIO(raw.cached(SOURCE, url).path.read_bytes().decode("utf-8-sig", errors="replace"))))
        titles, parties, names = rows[0], rows[1], rows[2]
        columns: dict[str, list[tuple[int, str, str | None]]] = {}
        for index in range(3, len(titles)):
            title = titles[index].strip()
            if not title or title.upper().startswith(("REGISTERED", "BALLOTS", "TURNOUT")) or SKIPPED.match(names[index].strip()):
                continue
            columns.setdefault(title, []).append((index, names[index].strip(), None))
        totals: dict[tuple[str, int], int] = {}
        places: dict[tuple[str, int], list[tuple[str, str]]] = {}
        for row in rows[3:]:
            if len(row) < 3 or not row[2].strip() or "TOTAL" in row[2].upper():
                continue
            for title, cells in columns.items():
                for index, _, _ in cells:
                    value = row[index].strip() if index < len(row) else ""
                    if value.isdigit():
                        totals[(title, index)] = totals.get((title, index), 0) + int(value)
                        places.setdefault((title, index), []).append((row[2].strip(), value))
        contests = []
        for title, cells in columns.items():
            choices = [
                {"text": name, "party": None, "total": str(totals[(title, index)]), "places": places[(title, index)]}
                for index, name, _ in cells
                if (title, index) in totals
            ]
            if choices:
                contests.append({"key": str(len(contests)), "text": _title(title), "vote_for": 1, "choices": choices})
        primary = any(cell.strip() in ("DEM", "REP") for cell in parties[3:])
        yield {
            "tenant": tenant,
            "election": re.sub(r"\W+", "-", label.lower()).strip("-"),
            "day": day,
            "title": label + (" Primary" if primary and "Primary" not in label else ""),
            "official": True,
            "primary": primary,
            "contests": contests,
        }


def _read_pdfs(tenant: str) -> Iterator[dict[str, Any]]:
    for href, (url, label) in sorted(_pdfs().items()):
        text = subprocess.run(["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True).stdout.decode(errors="replace")
        head = text[:800]
        if "Election Summary Report" in head and "Jurisdiction Wide" in head:
            parser = kceb.gems
        elif "Electionware" in text[:3000] or "Summary Results Report" in head:
            parser = kceb.electionware
        elif "SUMMARY REPORT" in head:
            parser = dotted
        else:
            print(f"{label}: no text layer or unknown layout; skipped")
            continue
        day = datetime.strptime(label.rsplit(" - ", 1)[1], "%B %d, %Y").date()
        nodes = []
        seen: set[str] = set()
        for node in parser(text):
            title = _title(node["text"])
            if title in seen:
                continue
            seen.add(title)
            nodes.append({**node, "text": title, "key": str(len(nodes))})
        yield {
            "tenant": tenant,
            "election": re.sub(r"\W+", "-", label.lower()).strip("-"),
            "day": day,
            "title": label,
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
