import html
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.fl_districts_2026"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "y26" / "fl"
MANUAL = DATA / "inbox" / "manual" / "MF-460"
SOURCES = {
    "https://browardvotes.gov/sites/default/files/list-of-candidates-for-november-elections.pdf": (
        "Broward County Supervisor of Elections",
        "List of Candidates and Contests, November 3, 2026 General Election",
        "broward.pdf",
        "Broward",
    ),
    "https://www.votebrevard.gov/Candidate-Information/2026-Candidates": (
        "Brevard County Supervisor of Elections",
        "2026 Candidates",
        "brevard.html",
        "Brevard",
    ),
    "https://www.pascovotes.gov/Documents/Candidates/List%20Of%202026%20Candidates/Tot2026.pdf": (
        "Pasco County Supervisor of Elections",
        "2026 Local Candidates, Pasco County (9/16/2026)",
        "pasco.pdf",
        "Pasco",
    ),
    "https://www.duvalelections.gov/254/Active-Candidates-Committees": (
        "Duval County Supervisor of Elections",
        "Active Candidates, 2026 General Election",
        "duval-2026-11-03-candidate-list.html",
        "Duval",
    ),
    "https://www.votehillsborough.gov/202/Local-Candidates-Committees": (
        "Hillsborough County Supervisor of Elections",
        "Local Candidates, 2026 Candidate Election Cycle",
        "hillsborough-2026-11-03-candidate-list.html",
        "Hillsborough",
    ),
    "https://www.lee.vote/DocumentCenter/View/728/2026-Candidate-List_092426": (
        "Lee County Supervisor of Elections",
        "2026 Candidate List (9/24/2026)",
        "lee-2026-candidate-list_092426.html",
        "Lee",
    ),
    "https://www.votemiamidade.gov/elections/candidates-campaigns/candidate-information.page": (
        "Miami-Dade County Elections Department",
        "Candidate Information, General Election 2026",
        "miamidade-2026-11-03-candidate-list.html",
        "Miami-Dade",
    ),
    "https://voteorangefl.gov/current-candidates/candidate-list/": (
        "Orange County Supervisor of Elections",
        "Current Candidates, November 3, 2026 Election",
        "orange-2026-11-03-candidate-list.html",
        "Orange",
    ),
    "https://www.votepalmbeach.gov/181/Announced-Candidates": (
        "Palm Beach County Supervisor of Elections",
        "Announced Candidates, 2026 Election Cycle",
        "palmbeach-2026-11-03-announced-candidates.html",
        "Palm Beach",
    ),
    "https://www.votepinellas.gov/220/Candidate-Information": (
        "Pinellas County Supervisor of Elections",
        "Candidate Information, 2026 Candidate Election Cycle",
        "pinellas-2026-11-03-candidate-list.html",
        "Pinellas",
    ),
    "https://www.polkelections.gov/171/Candidate-Campaign-Finance-Reports": (
        "Polk County Supervisor of Elections",
        "Candidates, 2026 General Election",
        "polk-2026-11-03-candidate-list.html",
        "Polk",
    ),
}
DISTRICT = re.compile(
    r"(?i)community development district|water control district|improvement district|drainage district|"
    r"recreation district|soil and water"
)
OUT = re.compile(r"(?i)withdr|defeated|elected in primary|disqualif|did not qualify|no qualified")
FUNCTIONS = (
    (r"(?i)community development", "community-development"),
    (r"(?i)improvement", "improvement"),
)


def fetch() -> None:
    for url, (_, _, file, _) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = INBOX / file
            if not path.exists():
                path = MANUAL / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf" if path.suffix == ".pdf" else "text/html",
            )


def pdf_text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def flip(name: str) -> str:
    last, _, first = name.partition(", ")
    return " ".join(f"{first} {last}".split())


def split_title(title: str) -> tuple[str, str]:
    district, _, seat = title.partition(", ")
    return district.strip(), seat.strip()


def broward(url: str) -> list[dict[str, str | int | None]]:
    rows = []
    for line in pdf_text(url).splitlines():
        found = re.match(r"(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s*$", line)
        if found is None or not DISTRICT.search(found[1]) or found[2] == "Write-in":
            continue
        district, seat = split_title(found[1])
        rows.append({"district": district, "seat": seat, "vote": 1, "name": found[2], "url": url})
    return rows


def brevard(url: str) -> list[dict[str, str | int | None]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    rows = []
    for part in text.split("<h3>")[1:]:
        title = html.unescape(re.sub(r"<[^>]+>", "", part.split("</h3>")[0])).strip()
        if not DISTRICT.search(title):
            continue
        vote = re.search(r"\((\d+) Seats? Available\)", title)
        title = re.sub(r"\s*\(\d+ Seats? Available\)", "", title)
        title = re.sub(r" (?:Supervisor|Trustee)(?=,|$)", "", title)
        district, seat = split_title(title)
        people = []
        for name, status in re.findall(
            r"<tr>\s*<td[^>]*><strong>(.*?)</strong>.*?</td>\s*<td[^>]*>(.*?)</td>", part, re.DOTALL
        ):
            name = " ".join(html.unescape(re.sub(r"<[^>]+>", "", name)).split())
            status = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", status)).split())
            if name != "Candidate" and "Qualified" in status and not OUT.search(status):
                people.append(name)
        if len(people) < 2 and not (vote and people):
            continue
        for name in people:
            rows.append(
                {"district": district, "seat": seat, "vote": int(vote[1]) if vote else 1, "name": name, "url": url}
            )
    return rows


def pasco(url: str) -> list[dict[str, str | int | None]]:
    rows = []
    district = ""
    entry: dict[str, str] | None = None
    for line in pdf_text(url).splitlines():
        stripped = line.strip()
        if re.match(r"^[A-Z][\w .&'-]+ (?:Community Development )?District$", stripped) or stripped.endswith(
            "Development District"
        ):
            district = stripped
            entry = None
            continue
        found = re.match(r"^\s*([A-Z]{3})\s+(\S.*?, .*?)\s{2,}(\S+)\s{2,}(\S.*)$", line)
        if found:
            entry = {"name": flip(found[2]), "status": found[4].strip(), "seat": ""}
            if entry["status"].startswith("Qualified") and district:
                rows.append({"district": district, "seat": "", "vote": 1, "name": entry["name"], "url": url})
                entry["row"] = "1"
            continue
        seat = re.match(r"^\s{20,}Seat (\d+)\s*$", line)
        if seat and entry is not None and entry.get("row"):
            rows[-1]["seat"] = f"Seat {seat[1]}"
    return rows


def cdd(text: str) -> str:
    return re.sub(r"(?i)\bCDD\b|\bCommunity Development Dist\.?(?=\s|$)", "Community Development District", text)


def clean_district(text: str) -> str:
    text = re.sub(r"^Office:\s*", "", html.unescape(text).strip())
    text = re.sub(r"^Supervisor,\s*|\s*-$", "", text)
    text = re.sub(r"(?i)\bDist\.?$", "District", text)
    text = re.sub(r"Community Development$", "Community Development District", text)
    text = re.sub(r"(?i)soil (?:and|&) water conservation(?: group)?|soil (?:and|&) water", "Soil and Water Conservation", text)
    text = re.sub(r"(?i)\bspec\.", "Special", text)
    text = re.sub(r"(?i)\bcont\.", "Control", text)
    text = re.sub(r"(?i)\bres\.", "Rescue", text)
    text = re.sub(r"\s*&\s*", " and ", text)
    text = re.sub(r"Fire and Rescue Dist\.", "Fire and Rescue District", text)
    text = re.sub(r"Fire Control Dist\.", "Fire Control District", text)
    return " ".join(cdd(text).split())


VOTERFOCUS = re.compile(r"(?i)\bCDD\b|community development|soil (?:and|&) water|improvement dist|fire|port of")


def voterfocus(url: str) -> list[dict[str, str | int | None]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    groups: dict[str, list[str]] = {}
    for part in text.split('<div class="col-xs-12 officegroup">')[1:]:
        office = html.unescape(re.search(r'officename">(.*?)</div>', part, re.DOTALL)[1]).strip()
        if not VOTERFOCUS.search(office) or re.search(r"(?i)county|judge", office):
            continue
        for chunk in part.split("detailrow candidate")[1:]:
            name = re.search(r'cf_indent" role="gridcell">\s*(.*?)\s*</div>', chunk, re.DOTALL)
            status = re.search(r'<span class="for-screen-reader">status</span>(.*?)</div>', chunk, re.DOTALL)
            if name is None or status is None:
                continue
            status_text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", status[1])).split())
            if re.search(r"-(?:Qualified|Runoff|Elected|Advanced)\)$", status_text):
                person = re.sub(r"\s*\([A-Z]{2,3}\)$", "", " ".join(html.unescape(name[1]).split()))
                groups.setdefault(office, []).append(person)
    rows = []
    for office, people in groups.items():
        if len(people) < 2:
            continue
        found = re.match(r"(.*?)[ ,]+(?:Seat|Dist\.|District|Group)\s*(\d+)\s*$", office)
        district, seat = (clean_district(found[1]), f"Seat {found[2]}") if found else (clean_district(office), "")
        if district.endswith("Soil and Water Conservation"):
            district += " District"
        if district.startswith("Port of Palm Beach"):
            district = "Port of Palm Beach District"
        for person in people:
            rows.append({"district": district, "seat": seat, "vote": 1, "name": person, "url": url})
    return rows


def lee(url: str) -> list[dict[str, str | int | None]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    rows = []
    for part in re.split(r'<tr class="dtrg-group[^>]*><td[^>]*>', text)[1:]:
        header = html.unescape(re.sub(r"<[^>]+>", "", part.split("</td>")[0].split("<span")[0])).strip()
        found = re.match(r"(?:Community Development District - )?(.*?),?\s+(\d)$", header)
        if found is None or not re.search(r"(?i)community development|fire|erosion|mosquito|library", header):
            continue
        people = re.findall(r'<tr class="Status-Q">.*?<h4>(.*?)</h4>', part, re.DOTALL)
        if len(people) < 2:
            continue
        title = found[1].title().replace("Cc", "CC").replace("Iona-Mcgregor", "Iona-McGregor")
        if header.startswith("Community"):
            district = f"{title} Community Development District"
        elif title.endswith("Fire"):
            district = f"{title} District"
        elif title.endswith("Erosion"):
            district = f"{title} Prevention District"
        else:
            district = title
        for person in people:
            name = re.sub(r"\s*\([A-Z]{2,3}\)$", "", " ".join(html.unescape(person).split()))
            rows.append({"district": district, "seat": f"Seat {found[2]}", "vote": 1, "name": name, "url": url})
    return rows


def parse() -> None:
    rows: list[dict[str, str | int | None]] = []
    for url, (_, _, _, county) in SOURCES.items():
        file = SOURCES[url][2]
        found = {"broward.pdf": broward, "brevard.html": brevard, "pasco.pdf": pasco}.get(file, voterfocus)(url)
        if file.startswith("lee-"):
            found = lee(url)
        for row in found:
            row["county"] = county
        rows.extend(found)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    roles = []
    for district in frame["district"]:
        if re.search(r"(?i)community development|soil and water|water control|improvement", district):
            roles.append("Supervisor")
        elif "Recreation" in district:
            roles.append("Trustee")
        else:
            roles.append("Commissioner")
    frame = frame.with_columns(
        remainder=pl.lit(False),
        role=pl.Series(roles),
        function=pl.Series(
            [next((value for pattern, value in FUNCTIONS if re.search(pattern, d)), None) for d in frame["district"]]
        ),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="FL",
        summary="Florida community development, water control, drainage, improvement, and recreation district board races on the November 3, 2026 ballot, with candidates from the Broward, Brevard, Pasco, Duval, Hillsborough, Lee, Miami-Dade, Orange, Palm Beach, Pinellas, and Polk County election offices",
        sources={url: (publisher, title) for url, (publisher, title, _, _) in SOURCES.items()},
        frame=frame,
    )
