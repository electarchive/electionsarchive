import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from ea.store import Connection

FILE = "https://drive.google.com/uc?export=download&id=14aF8IpDgLDc-qawFgNGSuVlVr0slazeU"
CONTESTS = {
    "Governor & Lieutenant Governor of Guam": "2026-11-03:GU:governor",
    "Delegate to Congress": "2026-11-03:GU:house:0",
    "Guam Legislature": "2026-11-03:GU:66:legislature",
    "Attorney General of Guam": "2026-11-03:GU:66:attorney-general",
    "Consolidated Commission on Utilities": "2026-11-03:GU:66:consolidated-commission-on-utilities",
    "Guam Education Board": "2026-11-03:GU:66:education-board",
}
STAMP = re.compile(r"Election Summary - (\d\d/\d\d/\d{4} \d\d:\d\d[AP]M)")
ROW = re.compile(r"^(?:(?:REP|DEM)\s+)?(.*?)\s{2,}([\d,]+)\s+[\d.]+%$")
VOTES = re.compile(r"^([\d,]+)\s+[\d.]+%$")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(FILE, follow_redirects=True)
    if response.status_code != 200 or not response.content.startswith(b"%PDF"):
        return {}
    return {FILE: (response.content, ".pdf")}


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z]+", text) if w not in SUFFIXES]


def name_key(name: str) -> tuple[str, str]:
    if "/" in name:
        return words(name.split("/")[0])[0], ""
    last, _, given = name.partition(",")
    return "".join(words(last)), (words(given) or [""])[0]


def label_key(label: str) -> tuple[str, str]:
    tokens = words(label)
    return tokens[-1], tokens[0]


def read(path: Path) -> tuple[str | None, dict[str, list[tuple[str, int]]]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    found = STAMP.search(text)
    stamp = (
        datetime.strptime(found[1], "%m/%d/%Y %I:%M%p").replace(tzinfo=ZoneInfo("Pacific/Guam")).isoformat()
        if found
        else None
    )
    contests: dict[str, list[tuple[str, int]]] = {}
    current: list[tuple[str, int]] | None = None
    pending = None
    for line in (raw.strip() for raw in text.splitlines()):
        if line in CONTESTS:
            current = contests.setdefault(line, [])
        elif current is None or not line:
            continue
        elif line.startswith(("Total Votes Cast", "Election Summary")):
            current = None
        elif line.startswith(("Vote For", "TOTAL VOTE", "Write-In Totals")):
            continue
        elif (row := ROW.match(line)) is not None:
            current.append((row[1], int(row[2].replace(",", ""))))
        elif (votes := VOTES.match(line)) is not None and pending is not None:
            current.append((pending, int(votes[1].replace(",", ""))))
            pending = None
            continue
        elif "," in line or "/" in line:
            pending = re.sub(r"^(?:REP|DEM)\s+", "", line)
            continue
        pending = None
    return stamp, contests


def plan(
    conn: Connection, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    unit = conn.execute(
        """select u.id from reporting_unit u join polity_territory t on t.area_id = u.area_id
           join polity p on p.id = t.polity_id where p.iso_code = 'US-GU' and u.scheme_code = 'us.state'"""
    ).fetchall()[0][0]
    tallies = []
    unmatched = []
    for path in paths.values():
        stamp, contests = read(path)
        for title, rows in contests.items():
            contest_key = CONTESTS[title]
            options = conn.execute(
                """select o.contest_id, o.id, o.ballot_label from external_id e
                   join contest_option o on o.contest_id = e.entity_id
                   where e.scheme = 'us.contest' and e.value = %s and o.ballot_label is not null""",
                [contest_key],
            ).fetchall()
            if not options:
                unmatched.append(f"no contest: {contest_key}")
                continue
            keys = {label_key(label): option for _, option, label in options}
            sums: dict[tuple[UUID, UUID], int] = {}
            for name, votes in rows:
                last, given = name_key(name)
                hits = [o for (l, g), o in keys.items() if l == last and (not given or g == given)]
                if len(hits) != 1:
                    unmatched.append(f"{contest_key}: {name}")
                    continue
                sums[(unit, hits[0])] = votes
            tallies.append(("GU", contest_key, options[0][0], sums, (0, 0), stamp))
    return tallies, unmatched
