import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea.sources.us.enr_2026 import table

BASE = "https://results.lavote.gov/ElectionResults"
ELECTION = 4348
CONTESTS = f"{BASE}/GetElectionData?electionID={ELECTION}"
COUNTS = f"{BASE}/GetCounterData?electionID={ELECTION}"
GROUPS = {
    "County Measures",
    "Sheriff",
    "Judge Superior Court",
    "Cities",
    "Schools",
    "Library Districts",
    "Water Districts",
    "Recreation and Park",
}


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    pages = {}
    for url in (CONTESTS, COUNTS):
        response = client.get(url)
        response.raise_for_status()
        pages[url] = (
            re.sub(rb'"TimeStamp":"[^"]*"', b'"TimeStamp":""', response.content)
            if url == CONTESTS
            else response.content,
            ".json",
        )
    return pages


def frame(paths: dict[str, Path]) -> pl.DataFrame:
    contests = json.loads(paths[CONTESTS].read_bytes())["Data"]["ContestGroups"]
    counts = json.loads(paths[COUNTS].read_bytes())
    stamp = (
        datetime.strptime(counts["TimeStamp"], "%m/%d/%Y %I:%M:%S %p")
        .replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        .isoformat()
    )
    votes: dict[int, int] = {}
    write_ins: dict[int, int] = {}
    for row in counts["Data"]:
        if row["ReferenceType"] == "CAND":
            votes[row["ReferenceID"]] = int(row["Value"])
        elif row["ReferenceType"].startswith("WRTN"):
            write_ins[row["ReferenceID"]] = write_ins.get(row["ReferenceID"], 0) + int(row["Value"])
    found = []
    for group in contests:
        if group["Name"] not in GROUPS:
            continue
        for contest in group["Contests"]:
            lines = [(person["Name"], votes.get(person["ID"], 0), False) for person in contest["Candidates"]]
            if contest["ID"] in write_ins:
                lines.append(("Write-in", write_ins[contest["ID"]], True))
            for name, count, write_in in lines:
                found.append(
                    {
                        "state": "CA",
                        "page": COUNTS,
                        "scope": "",
                        "contest": contest["Title"],
                        "office": "other",
                        "district": "",
                        "party": None,
                        "county": "Los Angeles",
                        "candidate": name,
                        "write_in": write_in,
                        "votes": count,
                        "as_of": stamp,
                        "reporting": 0,
                        "total": 0,
                    }
                )
    return pl.DataFrame(found, schema=table({}).schema)
