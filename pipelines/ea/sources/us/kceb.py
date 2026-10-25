import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.kceb"
VERSION = "1"
PLATFORM = "mo_kceb"
LISTING = "https://www.kceb.org/elections/election-results/"
DOCUMENT = re.compile(r"(?i)summary|official_results|canvass")
EXCLUDED = re.compile(r"(?i)precinct|sovc|write|sample|poll_log|rail")
DATE = re.compile(
    r"(?i)(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s*(\d{4})"
)
ROW = re.compile(r"^\s*(?P<name>\S.*?)\s{2,}(?P<total>[\d,]+)(?:\s+[\d,.]+%?){0,5}\s*$")
CAST = re.compile(r"^\s*Total Votes Cast\s+([\d,]+)")
GEMS_ROW = re.compile(r"^(?P<name>\S.*?)\s+(?:[A-Z]{3}\s+)?(?P<total>\d+)\s+[\d.]+%\s*$")
GEMS_NOISE = re.compile(r"^(Total|Number of Precincts|Precincts Reporting|Total Votes|Times Counted)\b")
PARTIES = {"DEM": "Democratic", "REP": "Republican", "LIB": "Libertarian", "CON": "Constitution", "GRE": "Green"}


OTHER = re.compile(
    r"(?i)jackson|co at|co leg|county|cir crt|circ|school district|hickman|raytown|grandview|lee'?s summit|center|independence|"
    r"\bR-?\d|\bC-?\d|mcc|trustee|library|tdd|rail|state|u\.?s\.?\b|senat|governor|auditor|treasurer|attorney|amend|president|judge ?\w* ?(circuit|court of)"
)
SCHOOL = re.compile(r"(?i)^(kc\s*(sub|at)|kcps|kansas city public)|school board|director (sub|at)")
CITY = re.compile(r"(?i)mayor|council|\bcity\b|^kc\b|municipal judge|^prop|^question|^q\d")


def _entity(title: str) -> str:
    if SCHOOL.search(title) and not re.search(r"(?i)hickman|raytown|grandview|lee'?s|center|independence", title):
        return f"Kansas City 33 School District, {title}"
    if CITY.search(title) and not OTHER.search(title):
        return f"City of Kansas City, {title}"
    return title


def _links() -> list[str]:
    page = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    links = {urljoin(LISTING, href) for href in re.findall(r'href="([^"]*\.pdf)"', page)}
    return sorted(link for link in links if DOCUMENT.search(link) and not EXCLUDED.search(link))


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="29", layer=layer), suffix=".zip")
    for link in _links():
        raw.fetch(SOURCE, link, suffix=".pdf")


def electionware(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*Vote For (\d+)\s*$", line) or index == 0:
            continue
        title = re.sub(r"^FOR\s+", "", lines[index - 1].strip(), flags=re.I)
        choices: list[dict[str, Any]] = []
        cast = None
        for other in lines[index + 1 : index + 80]:
            stripped = other.strip()
            if counted := CAST.match(other):
                cast = int(counted[1].replace(",", ""))
                break
            if re.match(r"^(Summary Results Report|Vote For|Election Summary)\b", stripped) or stripped.endswith("Kansas City") and choices:
                break
            row = ROW.match(other)
            if not row or stripped.startswith(("TOTAL", "Day", "Write-In:", "Not Assigned", "Overvotes", "Undervotes", "Registered")):
                continue
            name = "Write-ins" if stripped.startswith("Write-In Totals") else row["name"].strip()
            party = re.match(r"^(DEM|REP|LIB|CON|GRE)\s+(.+)$", name, re.I)
            if party:
                name = party[2]
            choices.append({"text": name, "party": PARTIES[party[1].upper()] if party else None, "total": row["total"].replace(",", ""), "places": []})
        if choices:
            yield {"text": title, "vote_for": int(re.search(r"\d+", line)[0]), "choices": choices, "cast": cast}


def gems(text: str) -> Iterator[dict[str, Any]]:
    for page in text.split("\f"):
        lines = page.splitlines()
        start = next((i for i, line in enumerate(lines) if line.startswith("Registered Voters")), None)
        if start is None:
            continue
        body = [line.rstrip() for line in lines[start + 1 :] if line.strip()]
        split = next(
            (cut for cut in range(54, 68) if all(len(line) <= cut or line[cut] == " " and line[cut - 1] == " " for line in body)),
            None,
        )
        columns = [[line[:split] for line in body], [line[split:] for line in body]] if split else [body]
        for column in columns:
            title = None
            choices: list[dict[str, Any]] = []
            cast = None
            for line in [*column, "END"]:
                stripped = line.strip()
                if not stripped:
                    continue
                row = GEMS_ROW.match(stripped)
                if row and title is not None:
                    name = "Write-ins" if stripped.startswith("Write-in Votes") else row["name"].strip()
                    party = re.search(r"\s([A-Z]{3})\s+\d+\s+[\d.]+%$", stripped)
                    choices.append(
                        {"text": name, "party": PARTIES.get(party[1]) if party else None, "total": row["total"], "places": []}
                    )
                    continue
                if counted := re.match(r"^Total Votes\s+(\d+)$", stripped):
                    cast = int(counted[1])
                    continue
                if GEMS_NOISE.match(stripped):
                    continue
                if title is not None and choices:
                    yield {"text": title, "vote_for": 1, "choices": choices, "cast": cast}
                title, choices, cast = stripped, [], None


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for link in _links():
        path = raw.cached(SOURCE, link).path
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode(
            errors="replace"
        )
        head = text[:600]
        if "Summary Results Report" in head:
            parser, title = electionware, re.sub(r"\s+", " ", head.split("\n")[1]).strip()
        elif "Election Summary Report" in head[:200] and "Jurisdiction Wide" in head:
            parser, title = gems, " ".join(line.strip() for line in head.splitlines()[:8] if "Election" in line and "Report" not in line)
        else:
            print(f"{link}: layout not read; skipped")
            continue
        found = DATE.search(head)
        if not found:
            print(f"{link}: no date; skipped")
            continue
        day = datetime.strptime(f"{found[1].title()} {found[2]} {found[3]}", "%B %d %Y").date()
        nodes = []
        seen: set[str] = set()
        for node in parser(text):
            total = sum(int(choice["total"]) for choice in node["choices"])
            short = parser is gems and total < (node["cast"] or 0) and not any(c["text"] == "Write-ins" for c in node["choices"])
            if node["cast"] is not None and total != node["cast"] and not short:
                print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                continue
            if node["text"] in seen:
                continue
            seen.add(node["text"])
            nodes.append({**node, "text": _entity(node["text"]), "key": str(len(nodes))})
        yield {
            "tenant": tenant,
            "election": day.isoformat(),
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True, floor=0.4)
