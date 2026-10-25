import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity, kceb

SOURCE = "us.stl_boe"
VERSION = "1"
PLATFORM = "mo_stl_boe"
LISTING = "https://www.stlouis-mo.gov/government/departments/board-election-commissioners/elections/results/past-years.cfm"
EXCLUDED = re.compile(r"(?i)precinct|pct|ward|wd\d|sovc|breakdown|28wards|unofficial")
STATE = re.compile(
    r"(?i)^(?:for\s+)?(?:u\.?s\.?|st\.?|state|lt|secr?|secretary|governor|president and|president$|pres(?: dem| rep)?$|presidential|constitutional|amendment|"
    r"gov|atty|attorney|con amend|amend\b|con convention|judge|supreme|court|retain)\b|\b(?:senator|attorney general|auditor|state treasurer|st treasurer|appeals|circuit judge|retain)\b"
)
WARD = re.compile(r"(?i)\b(?:WD|WARD|W)\s?(\d+)\b")
MEASURE = re.compile(r"(?i)prop|amend|question|issue|measure|bond|ordinance|charter|tax|refer|\bCON\b")
CUMULATIVE_ROW = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?:(?P<party>[A-Z]{2,4})\s{2,})?(?:\d[\d,]*\s+[\d.]+%\s+)*(?P<total>\d[\d,]*)\s+[\d.]+%\s*$"
)
CAST = re.compile(r"^\s*Cast Votes:.*?(\d[\d,]*)\s+100\.00%\s*$")


COUNTY_SIDE = re.compile(r"(?i)sheriff|rec(?:order|ord)? (?:of )?deeds|circ(?:uit)? att|prosecuting att|circuit clerk|coroner")
SCHOOL_BOARD = re.compile(r"(?i)board of ed|bd of ed")
REWRITES = (
    (re.compile(r"(?i)\bPRES(?:IDENT)?(?: OF)?(?: THE)? BOA(?:RD OF ALDERMEN)?\b"), "Council President"),
    (re.compile(r"(?i)\bCOLL(?:ECT(?:OR)?)? OF REV(?:ENUE)?\b|\bCOLLECT REV\b"), "Tax Collector"),
    (re.compile(r"(?i)\bCOMPTROLLER\b"), "Controller"),
    (re.compile(r"(?i)\bCIRC(?:UIT)? ATT(?:ORNE)?Y\b"), "Prosecuting Attorney"),
    (re.compile(r"(?i)\bREC(?:ORDER)? OF DEEDS\b|\bRECORD DEEDS\b"), "Recorder of Deeds"),
)


def _title(title: str) -> str:
    title = re.sub(r"\s+(DEM|REP|LIB|CON|GRN|GRE)\s+\1$", r" \1", title.strip())
    party = re.search(r"\b(DEM|REP|LIB|GRN|GRE)\b", title)
    if party:
        title = title[: party.start()] + " " + title[party.end() :]
    for pattern, replacement in REWRITES:
        title = pattern.sub(replacement, title)
    title = re.sub(r"(?i)\bCOM(?:MITTEE)? ?MAN\b", "Committeeman", title)
    title = re.sub(r"(?i)\bCOM(?:MITTEE)? ?WOMAN\b", "Committeewoman", title)
    title = re.sub(r"(?i)\b(?:BD OF EDUCATION|BOARD OF ED)\b", "Board of Education", title)
    if (ward := WARD.search(title)) and re.search(r"(?i)alderm|committee", title):
        title = f"{WARD.sub(' ', title)} Ward {ward[1]}"
    title = re.sub(r"[\s-]+$", "", re.sub(r"\s+", " ", title).strip())
    return f"{title} {party[1]}" if party else title


def _links() -> list[str]:
    page = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    links = {urljoin(LISTING, href) for href in re.findall(r'href="([^"]*\.pdf)"', page, re.I)}
    return sorted(link for link in links if not EXCLUDED.search(link.rsplit("/", 1)[1]))


def _precinct_links() -> list[str]:
    page = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    links = {urljoin(LISTING, href) for href in re.findall(r'href="([^"]*\.pdf)"', page, re.I)}
    return sorted(
        link
        for link in links
        if re.match(r"(?i)(?:jan|feb|mar|apr|may|jun|jul|aug|sep|nov|dec)2[3-9]-.*(?:precinct|pct)", link.rsplit("/", 1)[1])
    )


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="29", layer=layer), suffix=".zip")
    for link in (*_links(), *_precinct_links()):
        raw.fetch(SOURCE, link, suffix=".pdf")


def cumulative(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*Choice\s+Party\b", line) or index == 0:
            continue
        title = next((lines[back].strip() for back in range(index - 1, max(index - 4, -1), -1) if lines[back].strip() and not lines[back].strip().startswith("Absentee Voting -")), "")
        choices: list[dict[str, Any]] = []
        cast = None
        for other in lines[index + 1 : index + 60]:
            if counted := CAST.match(other):
                cast = int(counted[1].replace(",", ""))
                break
            row = CUMULATIVE_ROW.match(other)
            if not row or other.strip().startswith(("Choice", "Undervotes", "Overvotes", "Rejected")):
                continue
            name = re.sub(r"\s+", " ", row["name"]).strip()
            write_in = name.lower().startswith("write-in") or name.endswith("(W)")
            choices.append(
                {
                    "text": "Write-ins" if name.lower().startswith("write-in") else name,
                    "party": kceb.PARTIES.get(row["party"] or ""),
                    "total": row["total"].replace(",", ""),
                    "places": [],
                }
            )
        if choices and title:
            yield {"text": title, "vote_for": 1, "choices": choices, "cast": cast}


def precinct(text: str) -> Iterator[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for page in text.split("\f"):
        found = re.search(r"^(Ward \d+ Precinct \d+)", page, re.M)
        if not found:
            continue
        for node in cumulative(page):
            if node["cast"] is not None and sum(int(choice["total"]) for choice in node["choices"]) != node["cast"]:
                continue
            entry = merged.setdefault(node["text"], {"text": node["text"], "vote_for": 1, "choices": {}, "cast": None})
            for choice in node["choices"]:
                row = entry["choices"].setdefault(choice["text"], {**choice, "total": 0, "places": []})
                row["total"] += int(choice["total"])
                row["places"].append((found[1], choice["total"]))
    for entry in merged.values():
        yield {**entry, "choices": [{**choice, "total": str(choice["total"])} for choice in entry["choices"].values()]}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    precinct_days: set[Any] = set()
    for link in (*_precinct_links(), *_links()):
        path = raw.cached(SOURCE, link).path
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True).stdout.decode(errors="replace")
        head = text[:4000]
        if "Election Summary Report" in head[:300]:
            parser = kceb.gems
        elif "Cumulative Results Report" in head[:300]:
            parser = cumulative
        elif "Precinct Results Report" in head[:300]:
            parser = precinct
        else:
            print(f"{link}: layout not read; skipped")
            continue
        found = kceb.DATE.search(head)
        if found:
            day = datetime.strptime(f"{found[1].title()} {found[2]} {found[3]}", "%B %d %Y").date()
        elif numeric := re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", head):
            day = datetime.strptime(numeric[0], "%m/%d/%Y").date()
        else:
            print(f"{link}: no date; skipped")
            continue
        if parser is not precinct and day in precinct_days:
            continue
        if parser is precinct:
            precinct_days.add(day)
        title = next(
            (
                segment
                for line in head.splitlines()[:12]
                for segment in re.split(r"\s{2,}", line.strip())
                if re.search(r"(?i)(election|primary)$", segment) and not segment.startswith("Election Day")
            ),
            "Election",
        )
        nodes = []
        seen: set[str] = set()
        for node in parser(text):
            node["text"] = _title(node["text"])
            if STATE.search(node["text"]) or node["text"] in seen or node["text"].startswith("Vote For"):
                continue
            labels = {choice["text"].upper() for choice in node["choices"] if choice["text"] != "Write-ins"}
            if labels <= {"YES", "NO"} and not MEASURE.search(node["text"]):
                continue
            total = sum(int(choice["total"]) for choice in node["choices"])
            if parser is cumulative and node["cast"] is not None and total != node["cast"]:
                print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                continue
            seen.add(node["text"])
            text = _title(node["text"])
            if SCHOOL_BOARD.search(text):
                text = f"St. Louis City School District {text}"
            elif not COUNTY_SIDE.search(text):
                text = f"City of St. Louis, {text}"
            nodes.append({**node, "text": text, "key": str(len(nodes))})
        yield {
            "tenant": tenant,
            "election": link.rsplit("/", 1)[1][:-4],
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
