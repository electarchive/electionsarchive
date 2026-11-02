import html
import json
import re
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import fastexcel
import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES

API = "https://app.enhancedvoting.com/results/public/api/"
CDN = "https://app.enhancedvoting.com/cdn/results/"
COUNTIES = {"Westchester": "westchester-county-ny", "Suffolk": "suffolk-county-ny", "Monroe": "monroe-county-ny"}
NASSAU = "https://app.nassaucountyny.gov/BOE/results/election2.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
LEGISLATURE = re.compile(
    r"(?P<chamber>State Senator|Member of (?:the )?Assembly),? (?:District )?(?P<district>\d+)(?:st|nd|rd|th)?(?: Senatorial| Assembly)?(?: District)?",
    re.IGNORECASE,
)
STATEWIDE = re.compile(r"(?:State )?Comptroller|Attorney General", re.IGNORECASE)
TOTALS = {"ballots cast", "over votes", "under votes", "total votes cast", "total ballots cast", "blank"}
CODE = re.compile(r"^[A-Z]{3} (?=\S+ )")
NASSAU_LINE = re.compile(r"^\s+(\S.*?)(?:\s*\.)(?:\s+\.)+\s+([\d,]+)\b")
NASSAU_PRECINCTS = re.compile(r"WITH ([\d,]+) OF ([\d,]+) PRECINCTS")
NASSAU_PARTY = re.compile(r"\*{4,} \((.+)\) \*{4,}")
PDF_LINE = re.compile(
    r"^(?:(?P<code>[A-Z]{3}) )?(?P<name>\S.*?)\s{2,}(?:(?P<party>[A-Za-z][A-Za-z ]*?)\s{2,})?(?P<votes>[\d,]+)$"
)


def classify(title: str) -> tuple[str, str, str]:
    office = EV_OFFICES.fullmatch(title)
    seat = LEGISLATURE.fullmatch(title)
    if seat:
        return ("senate" if seat["chamber"].lower().startswith("state") else "assembly"), "", f"{seat['district']}:g"
    if office:
        return (
            "senate" if office["senate"] else "governor" if office["governor"] else "house",
            "",
            office["district"] or "",
        )
    return ("state" if STATEWIDE.fullmatch(title) else "other"), title, ""


def person(label: str) -> str:
    label = html.unescape(label).replace(" AND ", " / ")
    label = CODE.sub("", label) if label != label.upper() else label
    return re.sub(r"\s*/\s*", " / ", " ".join(label.split()))


def pdf_text(content: bytes) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=content, capture_output=True, check=True
    ).stdout.decode(errors="replace")


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    for county, slug in COUNTIES.items():
        listing = get(client, f"{API}jurisdictions/{slug}", HEADERS)
        found = next(
            (
                e["publicElectionId"]
                for e in json.loads(listing or "{}").get("elections") or []
                if e["electionDate"][:10] == DAY.isoformat()
            ),
            None,
        )
        url = f"{API}elections/{slug}/{found}"
        election = found and get(client, url, HEADERS)
        if not election:
            print(f"NY: no {DAY} election for {county}")
            continue
        detail = json.loads(election)
        reports = {r["reportName"]: r["blobName"] for c in detail["publicReportCategories"] for r in c["reports"]}
        excel = reports.get("All Results Excel")
        blob = excel or reports.get("Summary Results by Contest with Candidate Totals")
        content = blob and get(client, f"{CDN}{detail['jurisdictionId']}/{quote(blob)}", HEADERS)
        if not content:
            print(f"NY: no results file for {county}")
            continue
        store(f"{API}jurisdictions/{slug}", listing, ".json")
        store(url, election, ".json")
        store(f"{url}/report", content, ".xlsx" if excel else ".pdf")
    page = get(client, NASSAU, HEADERS)
    if page and f"{DAY:%B} {DAY.day}, {DAY.year}".upper() in page.decode(errors="replace").upper():
        store(NASSAU, page, ".html")
    else:
        print(f"NY: no {DAY} page for Nassau")


def reporting(detail: dict) -> tuple[int | None, int | None]:
    statuses = detail.get("precinctReportingStatus") or []
    done = sum(
        s["value"] or 0
        for s in statuses
        if s["reportingStatusClientTextKey"]
        in ("reporting-status-fully-reported", "reporting-status-election-night-complete")
    )
    total = sum(s["value"] or 0 for s in statuses)
    return (done, total) if total else (None, None)


def row(county: str, title: str, candidate: str, party: str, votes: int, as_of: str | None, seen: tuple) -> dict:
    office, contest, district = classify(title)
    return {
        "office": office,
        "contest": contest,
        "district": district,
        "party": party,
        "county": county,
        "candidate": candidate,
        "write_in": candidate.lower().startswith("write-in"),
        "votes": votes,
        "as_of": as_of,
        "reporting": seen[0],
        "total": seen[1],
    }


def rows(url: str, path: Path, urls: dict[str, Path]):
    if url == NASSAU:
        yield from nassau(path)
        return
    found = re.fullmatch(API + r"elections/([^/]+)/[^/]+/report", url)
    if found is None:
        return
    county = next((name for name, slug in COUNTIES.items() if slug == found[1]), None)
    election = urls.get(url.removesuffix("/report"))
    if county is None or election is None:
        return
    detail = json.loads(election.read_bytes())
    stamp = detail.get("asOf") or detail.get("lastUpdated")
    as_of = datetime.fromisoformat(stamp).isoformat() if stamp else None
    seen = reporting(detail)
    if path.suffix == ".xlsx":
        frame = fastexcel.read_excel(path).load_sheet_by_name("Summary Results", header_row=0).to_polars()
        votes: dict[tuple[str, str], list] = {}
        for title, name, party, total in frame.select("Office Name", "Ballot Name", "Party", "Total").iter_rows():
            title = " ".join(html.unescape(title or "").split())
            if not title or not name or name.strip().lower() in TOTALS:
                continue
            entry = votes.setdefault((title, person(name)), [party or "", 0])
            entry[1] += int(total or 0)
        for (title, candidate), (party, count) in votes.items():
            yield row(county, title, candidate, party, count, as_of, seen)
        return
    title = None
    header = 0
    candidates: dict[tuple[str, str], list] = {}
    for line in pdf_text(path.read_bytes()).splitlines():
        text = line.rstrip()
        if "Summary Results Report" in text:
            header = 2
            continue
        if not text.strip() or "Last Updated" in text:
            continue
        if header:
            header -= 1
            continue
        if text.startswith(("Monroe County", "Vote For")) or re.fullmatch(r"\s*TOTAL\s*", text):
            continue
        if re.fullmatch(r"\S.*", text) and not PDF_LINE.match(text):
            title = " ".join(text.split())
            continue
        if title is None:
            continue
        match = PDF_LINE.match(text.strip() if text.startswith(" ") else text)
        if match is None:
            continue
        name = match["name"].strip()
        count = int(match["votes"].replace(",", ""))
        lowered = name.lower()
        if lowered in TOTALS:
            continue
        if match["party"] == "Total" or name.endswith(" Total"):
            label = name.removesuffix(" Total").strip()
            if label:
                candidates[(title, person(label))] = ["", count]
            continue
        if match["code"] or match["party"]:
            entry = candidates.setdefault(
                (title, person(f"{match['code'] or ''} {name}".strip())), [match["party"] or "", 0]
            )
            entry[1] += count
        elif lowered.startswith("write-in"):
            candidates[(title, "Write-in")] = ["", count]
    for (title, candidate), (party, count) in candidates.items():
        yield row("Monroe", title, candidate, party, count, as_of, seen)


def nassau(path: Path):
    text = path.read_text(errors="replace")
    stamp = None
    party = ""
    title = ""
    seen: tuple[int | None, int | None] = (None, None)
    lines = [html.unescape(line) for line in re.sub(r"</?(?:HTML|PRE)>", "", text, flags=re.IGNORECASE).splitlines()]
    for n, line in enumerate(lines):
        if found := NASSAU_PARTY.search(line):
            party = found[1]
        elif "(VOTE FOR)" in line:
            title = next((lines[k].strip() for k in range(n - 1, 0, -1) if lines[k].strip()), "")
        elif found := NASSAU_PRECINCTS.search(line):
            seen = (int(found[1].replace(",", "")), int(found[2].replace(",", "")))
        elif (found := NASSAU_LINE.match(line)) and title and not line.strip().startswith("Total"):
            candidate = " ".join(found[1].replace("*", "").split())
            if candidate.lower() in TOTALS:
                continue
            yield row(
                "Nassau",
                title,
                "Write-in" if candidate.upper() == "WRITE-IN" else candidate,
                party,
                int(found[2].replace(",", "")),
                stamp,
                seen,
            )
