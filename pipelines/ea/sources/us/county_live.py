import html
import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea.sources.us.enr_2026 import table

LOCAL = re.compile(r"(?i)\bmayor\b|^(city|town|village)\b")
SUFFIX = {"electionsfl": ".html", "summary_xml": ".xml", "montgomery_tn": ".json"}
NS = "{ElectionSummaryReportRPT}"
UPDATED = re.compile(r'id="LastUpdated">\s*\(Website last updated at: ([^)]+)\)')
LINE = re.compile(
    r"ChoiceColumn[^>]*>\s*([^<]*?)\s*(?:<div.*?)?</div>.*?ResultsColumn[^>]*>\s*[\d.]+&#37;\s*</div>"
    r'\s*<div class="ResultsColumn[^>]*>\s*([\d,]+)\s*</div>',
    re.DOTALL,
)
PRECINCTS = re.compile(r'numPrecinctsReported">(\d+)<.*?numPrecinctsParticipating">(\d+)<', re.DOTALL)
REPORTED = re.compile(r"Precincts Reported: (\d+) of (\d+)")
Race = tuple[str, list[tuple[str, int]], tuple[int, int]]


def collect(client: httpx.Client, kind: str, url: str) -> dict[str, tuple[bytes, str]]:
    response = client.get(url)
    response.raise_for_status()
    return {url: (response.content, SUFFIX[kind])}


def electionsfl(path: Path) -> tuple[list[Race], str | None]:
    text = path.read_text(errors="replace")
    stamped = UPDATED.search(text)
    stamp = (
        datetime.strptime(stamped[1], "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=ZoneInfo("America/New_York")).isoformat()
        if stamped
        else None
    )
    races = []
    for block in text.split("<label>")[1:]:
        lines = [
            (re.sub(r"\s*\(\w+\)$", "", html.unescape(name)).strip(), int(votes.replace(",", "")))
            for name, votes in LINE.findall(block)
        ]
        counted = PRECINCTS.search(block)
        reporting = (int(counted[1]), int(counted[2])) if counted else (0, 0)
        races.append((html.unescape(block[: block.find("</label>")]).strip(), lines, reporting))
    return races, stamp


def summary_xml(path: Path) -> tuple[list[Race], str | None]:
    races = []
    for group in ElementTree.parse(path).getroot().iter(f"{NS}ContestIdGroup"):
        counted = next(
            (
                found
                for details in group.iter(f"{NS}Details")
                if (found := REPORTED.search(details.get("Textbox2") or ""))
            ),
            None,
        )
        races.append(
            (
                re.sub(r"\s*\(Vote for.*", "", group.get("contestId") or "").strip(),
                [
                    (" ".join((row.get("candidateNameTextBox4") or "").split()), int(total.get("vot8") or 0))
                    for row in group.iter(f"{NS}candidateNameTextBox4")
                    if (total := row.find(f"{NS}Textbox13")) is not None
                ],
                (int(counted[1]), int(counted[2])) if counted else (0, 0),
            )
        )
    return races, None


def montgomery_tn(path: Path) -> tuple[list[Race], str | None]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    stamp = (
        datetime.strptime(data["LastUpdate"], "%m/%d/%Y, %I:%M:%S%p").replace(tzinfo=ZoneInfo("America/Chicago"))
    ).isoformat()
    return [
        (
            office["Name"].strip(),
            [(line["Candidate"]["Name"].strip(), line["Total"]) for line in office["Votes"].values()],
            (0, 0),
        )
        for office in data["Offices"].values()
    ], stamp


READERS = {"electionsfl": electionsfl, "summary_xml": summary_xml, "montgomery_tn": montgomery_tn}


def rows(kind: str, state: str, county: str, url: str, path: Path) -> Iterator[dict[str, object]]:
    races, stamp = READERS[kind](path)
    for contest, lines, (reporting, total) in races:
        if not LOCAL.search(contest) or not lines or {name.lower() for name, _ in lines} <= {"yes", "no"}:
            continue
        for name, votes in lines:
            yield {
                "state": state,
                "page": url,
                "scope": "",
                "contest": contest,
                "office": "other",
                "district": "",
                "party": None,
                "county": county,
                "candidate": name,
                "write_in": "write-in" in name.lower(),
                "votes": votes,
                "as_of": stamp,
                "reporting": reporting,
                "total": total,
            }


def frame(kind: str, state: str, county: str, paths: dict[str, Path]) -> pl.DataFrame:
    found = [row for url, path in paths.items() for row in rows(kind, state, county, url, path)]
    return pl.DataFrame(found, schema=table({}).schema)
