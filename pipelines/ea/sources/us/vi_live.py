import html
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.mp_cec_live import pick, words
from ea.store import Connection

PAGE = "https://vivote.gov/?elections=2026-general-election"
REPORT = re.compile(r"summary|unofficial|results|zero report", re.IGNORECASE)
SKIP = re.compile(
    r"detail|precinct|absentee|provisional|turn ?out|press|write-?in|lots|listing|aspirant|voting center|certification",
    re.IGNORECASE,
)
CONTESTS = {
    "delegate to the house of representatives": "2026-11-03:VI:house:0",
    "governor lieutenant governor": "2026-11-03:VI:governor",
    "legislature of the virgin islands at large": "2026-11-03:VI:78:legislature:at-large",
    "legislature of the virgin islands st croix": "2026-11-03:VI:78:legislature:st-croix-district",
    "legislature of the virgin islands st thomas st john": "2026-11-03:VI:78:legislature:st-thomas-district",
    "board of education st croix": "2026-11-03:VI:78:board-of-education:st-croix-district",
    "board of education st thomas st john": "2026-11-03:VI:78:board-of-education:st-thomas-district",
    "board of elections st croix": "2026-11-03:VI:78:board-of-elections:st-croix-district",
    "board of elections stj st thomas st john": "2026-11-03:VI:78:board-of-elections:st-john-district",
    "board of elections stt st thomas st john": "2026-11-03:VI:78:board-of-elections:st-thomas-district",
}
STAMP = re.compile(r"Election Summary - (\d\d/\d\d/\d{4} \d\d:\d\d[AP]M)")
DISTRICTS = {"STX": re.compile(r"\bSTX\b|St\.? Croix"), "STT": re.compile(r"\bSTT\b|St\.? Thomas")}
ROW = re.compile(r"^(?:[A-Z]{3}\s+)?(.*?\S)\s{2,}([\d,]+)(?:\s|$)")
OTHER = ("Write-In", "Total Votes", "Ballots Cast", "Overvotes", "Undervotes", "Registered", "Voter Turnout")


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(PAGE)
    if response.status_code != 200:
        return {}
    pages: dict[str, tuple[bytes, str]] = {}
    for href, text in re.findall(r'href="([^"]+\.pdf)"[^>]*>(.*?)</a>', response.text, re.DOTALL):
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        url = urljoin(PAGE, href)
        if "/2026/11/" not in url or url in pages or not REPORT.search(label) or SKIP.search(f"{label} {url}"):
            continue
        found = client.get(url)
        if found.status_code == 200 and found.content.startswith(b"%PDF"):
            pages[url] = (found.content, ".pdf")
    return pages


def read(path: Path) -> tuple[str, str | None, dict[str, list[tuple[str, int]]]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    head = text.lstrip().split("\n", 1)[0]
    scope = next((name for name, pattern in DISTRICTS.items() if pattern.search(head)), "")
    found = STAMP.search(text)
    stamp = (
        datetime.strptime(found[1], "%m/%d/%Y %I:%M%p").replace(tzinfo=ZoneInfo("America/St_Thomas")).isoformat()
        if found
        else None
    )
    contests: dict[str, list[tuple[str, int]]] = {}
    current: list[tuple[str, int]] | None = None
    previous = ""
    for line in (raw.strip() for raw in text.splitlines()):
        if not line:
            continue
        if line.startswith("Vote For"):
            current = contests.setdefault(previous, [])
        elif line.startswith(("Total Votes Cast", "Election Summary")):
            current = None
        elif current is not None and not line.startswith(OTHER) and (row := ROW.match(line)) is not None:
            current.append((row[1], int(row[2].replace(",", ""))))
        previous = line
    return scope, stamp, contests


def plan(
    conn: Connection, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    unit = conn.execute(
        """select u.id from reporting_unit u join polity_territory t on t.area_id = u.area_id
           join polity p on p.id = t.polity_id where p.iso_code = 'US-VI' and u.scheme_code = 'us.state'"""
    ).fetchall()[0][0]
    seen: dict[str, dict[str, tuple[int, str | None, list[tuple[str, int]]]]] = {}
    unmatched = []
    for path in paths.values():
        scope, stamp, contests = read(path)
        for title, rows in contests.items():
            contest_key = CONTESTS.get(" ".join(re.findall(r"[a-z]+", title.lower())))
            if contest_key is None:
                unmatched.append(f"{title}: no contest mapping")
                continue
            total = sum(votes for _, votes in rows)
            scopes = seen.setdefault(contest_key, {})
            if scope not in scopes or total > scopes[scope][0]:
                scopes[scope] = (total, stamp, rows)
    tallies = []
    for contest_key, scopes in seen.items():
        merged: dict[str, int] = {}
        for scope, (_, _, rows) in scopes.items():
            for name, votes in rows if scope else ():
                merged[name] = merged.get(name, 0) + votes
        whole = scopes.get("", (-1, None, []))
        parts = [stamp for scope, (_, stamp, _) in scopes.items() if scope and stamp]
        stamp, rows = (
            (whole[1], whole[2])
            if whole[0] >= sum(merged.values())
            else (min(parts, default=None), list(merged.items()))
        )
        options = conn.execute(
            """select o.contest_id, o.id, coalesce(ca.ballot_name, o.ballot_label) from external_id e
               join contest_option o on o.contest_id = e.entity_id and o.parent_option_id is null
               left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
               where e.scheme = 'us.contest' and e.value = %s and o.kind <> 'write_in'""",
            [contest_key],
        ).fetchall()
        if not options:
            unmatched.append(f"no contest: {contest_key}")
            continue
        labels = {option: words(label or "") for _, option, label in options}
        sums: dict[tuple[UUID, UUID], int] = {}
        for name, votes in rows:
            option = pick(name, labels)
            if option is None:
                unmatched.append(f"{contest_key}: {name}")
                continue
            sums[(unit, option)] = votes
        if sums:
            tallies.append(("VI", contest_key, options[0][0], sums, (0, 0), stamp))
    return tallies, unmatched
