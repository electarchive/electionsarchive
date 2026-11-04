import csv
import difflib
import io
import json
import re
from pathlib import Path
from uuid import UUID

import httpx

from ea.sources.us.mp_cec_live import pick, words
from ea.store import Connection

INFO = "https://electionresults.dcboe.org/electionResults/getElectionInfo/2026-General-Election"
DAY = "2026-11-03"
WARDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8}
CONTESTS = {
    "DELEGATE TO THE HOUSE OF REPRESENTATIVES FROM THE DISTRICT OF COLUMBIA": "DC:house:0",
    "MAYOR OF THE DISTRICT OF COLUMBIA": "DC:mayor",
    "CHAIRMAN OF THE COUNCIL OF THE DISTRICT OF COLUMBIA": "DC:1150000:council:chair",
    "AT-LARGE MEMBER OF THE COUNCIL OF THE DISTRICT OF COLUMBIA": "DC:1150000:council:at-large",
    "ATTORNEY GENERAL FOR THE DISTRICT OF COLUMBIA": "DC:attorney-general",
    "UNITED STATES SENATOR": "DC:shadow-united-states-senator",
    "UNITED STATES REPRESENTATIVE CITYWIDE": "DC:shadow-united-states-representative",
}
WARD = re.compile(r"^WARD (\w+) MEMBER OF THE (COUNCIL OF THE DISTRICT OF COLUMBIA|STATE BOARD OF EDUCATION)$")
ANC = re.compile(r"^ANC-(\d(?:/\d)?[A-Z]\d\d)(?: \w+)?$")
MEASURE = re.compile(r"^INITIATIVE MEASURE NO\. (\d+)$")
SKIP = ("OVER VOTES", "UNDER VOTES")


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    info = client.get(INFO)
    if info.status_code != 200:
        return {}
    link = info.json().get("CsvLink")
    if not link:
        return {}
    found = client.get(link)
    if found.status_code != 200 or not found.content.lstrip(b"\xef\xbb\xbf").startswith(b"ElectionDate"):
        return {}
    return {INFO: (info.content, ".json"), link: (found.content, ".csv")}


def key(title: str) -> str | None:
    if title in CONTESTS:
        return f"{DAY}:{CONTESTS[title]}"
    if (ward := WARD.match(title)) and ward[1] in WARDS:
        body = "council" if ward[2].startswith("COUNCIL") else "state-board-of-education"
        return f"{DAY}:DC:1150000:{body}:ward-{WARDS[ward[1]]}"
    if anc := ANC.match(title):
        return f"{DAY}:DC:11:anc:{anc[1].lower().replace('/', '-')}"
    if measure := MEASURE.match(title):
        return f"{DAY}:DC:question:initiative-{measure[1]}"
    return None


def name(candidate: str, party: str) -> str:
    if party and candidate.startswith(f"{party} "):
        return candidate.removeprefix(f"{party} ")
    return candidate.split(",")[0] if candidate.startswith(("Yes,", "No,")) else candidate


def near(candidate: str, labels: dict[UUID, list[str]]) -> UUID | None:
    close = [
        option
        for option, label in labels.items()
        if difflib.SequenceMatcher(None, " ".join(words(candidate)), " ".join(label)).ratio() >= 0.9
    ]
    return close[0] if len(close) == 1 else None


def plan(
    conn: Connection, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    info = json.loads(paths[INFO].read_bytes())
    stamp = info.get("LastUpdated")
    reporting = (int(info.get("PrecinctsCounted") or 0), int(info.get("TotalPrecincts") or 0))
    unit = conn.execute("select id from reporting_unit where scheme_code = 'us.state' and code = '11'").fetchall()[0][0]
    rows: dict[str, dict[str, int]] = {}
    for path in paths.values():
        if path.suffix != ".csv":
            continue
        for row in csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))):
            if row["Candidate"] in SKIP or not row["Candidate"] or int(row["ContestNumber"]) < 0:
                continue
            totals = rows.setdefault(row["ContestName"], {})
            candidate = name(row["Candidate"], row["Party"])
            totals[candidate] = totals.get(candidate, 0) + int(row["Votes"] or 0)
    tallies = []
    unmatched = []
    for title, totals in rows.items():
        contest_key = key(title)
        if contest_key is None:
            unmatched.append(f"{title}: no contest mapping")
            continue
        options = conn.execute(
            """select o.contest_id, o.id, o.kind, coalesce(ca.ballot_name, o.ballot_label) from external_id e
               join contest_option o on o.contest_id = e.entity_id and o.parent_option_id is null
               left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
               where e.scheme = 'us.contest' and e.value = %s""",
            [contest_key],
        ).fetchall()
        if not options:
            unmatched.append(f"no contest: {contest_key}")
            continue
        labels = {option: words(label or "") for _, option, kind, label in options if kind != "write_in"}
        write_in = next((option for _, option, kind, _ in options if kind == "write_in"), None)
        sums: dict[tuple[UUID, UUID], int] = {}
        for candidate, votes in totals.items():
            option = write_in if candidate == "Write-in" else pick(candidate, labels) or near(candidate, labels)
            if option is None:
                if candidate != "Write-in":
                    unmatched.append(f"{contest_key}: {candidate}")
                continue
            sums[(unit, option)] = sums.get((unit, option), 0) + votes
        if sums:
            tallies.append(("DC", contest_key, options[0][0], sums, reporting, stamp))
    return tallies, unmatched
