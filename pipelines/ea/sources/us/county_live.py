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

from ea.sources.us.enr_2026 import ballot_channel, table

LOCAL = re.compile(r"(?i)\bmayor\b|^(city|town|village)\b|\bcity council\b")
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
DETAIL = re.compile(r'<td class="ChoiceColumn">\s*([^<]*?)\s*<div.*?</td>(.*?)</tr>', re.DOTALL)
CELL = re.compile(r'<td class="DetailResultsColumn[^"]*">\s*([^<]*?)\s*</td>')
Race = tuple[str, list[tuple[str, list[tuple[str, int]]]], tuple[int, int]]


def collect(client: httpx.Client, kind: str, url: str) -> dict[str, tuple[bytes, str]]:
    response = client.get(url)
    response.raise_for_status()
    return {url: (response.content, SUFFIX[kind])}


def split(total: int, groups: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return groups if len(groups) > 1 and sum(count for _, count in groups) == total else [("", total)]


def name(text: str) -> str:
    return re.sub(r"\s*\(\w+\)$", "", html.unescape(text)).strip()


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
        heads = [
            re.sub(r"<[^>]+>|\s+", " ", head).strip()
            for head in re.findall(r"<th[^>]*>(.*?)</th>", block.split("</thead>")[0], re.DOTALL)
        ]
        ballots = heads[1 : heads.index("Total Votes")] if "Total Votes" in heads else []
        details = {
            name(choice): [
                (ballot, int(cell.replace(",", "")) if cell.replace(",", "").isdigit() else -1)
                for ballot, cell in zip(ballots, CELL.findall(cells))
            ]
            for choice, cells in DETAIL.findall(block)
        }
        lines = [
            (name(choice), split(int(votes.replace(",", "")), details.get(name(choice), [])))
            for choice, votes in LINE.findall(block)
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
                    (
                        " ".join((row.get("candidateNameTextBox4") or "").split()),
                        split(
                            int(total.get("vot8") or 0),
                            [
                                (part.get("countingGroupName") or "", int(part.get("vot7") or 0))
                                for part in row.iter(f"{NS}cgGroup")
                            ],
                        ),
                    )
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
            [(line["Candidate"]["Name"].strip(), [("", line["Total"])]) for line in office["Votes"].values()],
            (0, 0),
        )
        for office in data["Offices"].values()
    ], stamp


READERS = {"electionsfl": electionsfl, "summary_xml": summary_xml, "montgomery_tn": montgomery_tn}


def rows(kind: str, state: str, county: str, url: str, path: Path) -> Iterator[dict[str, object]]:
    races, stamp = READERS[kind](path)
    for contest, lines, (reporting, total) in races:
        if not LOCAL.search(contest) or not lines or {choice.lower() for choice, _ in lines} <= {"yes", "no"}:
            continue
        for choice, groups in lines:
            for ballot, votes in groups:
                yield {
                    "state": state,
                    "page": url,
                    "scope": "",
                    "contest": contest,
                    "office": "other",
                    "district": "",
                    "party": None,
                    "county": county,
                    "candidate": choice,
                    "write_in": "write-in" in choice.lower(),
                    "votes": votes,
                    "ballot": ballot,
                    "as_of": stamp,
                    "reporting": reporting,
                    "total": total,
                }


def frame(kind: str, state: str, county: str, paths: dict[str, Path]) -> pl.DataFrame:
    found = [row for url, path in paths.items() for row in rows(kind, state, county, url, path)]
    return pl.DataFrame(found, schema=table({}).schema).with_columns(channel=ballot_channel())
