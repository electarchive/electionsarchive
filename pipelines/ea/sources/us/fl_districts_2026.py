import html
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA, connect
from ea.typed import required

SOURCE = "us.fl_districts_2026"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "y26" / "fl"
MANUAL = DATA / "inbox" / "manual" / "MF-460"
MANUAL_DIRS = (MANUAL, DATA / "inbox" / "manual" / "MF-184", DATA / "inbox" / "manual" / "MF-483")
STJ = "2026_QUALIFIED_Candidates+08-18-26.pdf"
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
    "https://www.voteosceola.gov/en-us/current-candidates/": (
        "Osceola County Supervisor of Elections",
        "Current Candidates, November 3, 2026 General Election",
        "fl-osceola-2026-general-candidate-list.html",
        "Osceola",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=collier": (
        "Collier County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-collier-voterfocus-candidates.html",
        "Collier",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=escambia": (
        "Escambia County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-escambia-voterfocus-candidates.html",
        "Escambia",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=lake": (
        "Lake County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-lake-voterfocus-candidates.html",
        "Lake",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=manatee": (
        "Manatee County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-manatee-voterfocus-candidates.html",
        "Manatee",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=marion": (
        "Marion County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-marion-voterfocus-candidates.html",
        "Marion",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=sarasota": (
        "Sarasota County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-sarasota-voterfocus-candidates.html",
        "Sarasota",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=volusia": (
        "Volusia County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-volusia-voterfocus-candidates.html",
        "Volusia",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=stlucie": (
        "St. Lucie County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-stlucie-voterfocus-candidates.html",
        "St. Lucie",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=clay": (
        "Clay County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-clay-voterfocus-candidates.html",
        "Clay",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=sumter": (
        "Sumter County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-sumter-voterfocus-candidates.html",
        "Sumter",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=flagler": (
        "Flagler County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-flagler-voterfocus-candidates.html",
        "Flagler",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=charlotte": (
        "Charlotte County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-charlotte-voterfocus-candidates.html",
        "Charlotte",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=hernando": (
        "Hernando County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-hernando-voterfocus-candidates.html",
        "Hernando",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=leon": (
        "Leon County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-leon-voterfocus-candidates.html",
        "Leon",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=bay": (
        "Bay County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-bay-voterfocus-candidates.html",
        "Bay",
    ),
    "https://irp.cdn-website.com/ed388134/files/uploaded/2026_QUALIFIED_Candidates+08-18-26.pdf": (
        "St. Johns County Supervisor of Elections",
        "2026 Qualified Candidates, St. Johns County (8/18/2026)",
        STJ,
        "St. Johns",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=alachua": (
        "Alachua County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-alachua-voterfocus-candidates.html",
        "Alachua",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=baker": (
        "Baker County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-baker-voterfocus-candidates.html",
        "Baker",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=bradford": (
        "Bradford County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-bradford-voterfocus-candidates.html",
        "Bradford",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=calhoun": (
        "Calhoun County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-calhoun-voterfocus-candidates.html",
        "Calhoun",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=citrus": (
        "Citrus County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-citrus-voterfocus-candidates.html",
        "Citrus",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=columbia": (
        "Columbia County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-columbia-voterfocus-candidates.html",
        "Columbia",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=desoto": (
        "DeSoto County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-desoto-voterfocus-candidates.html",
        "DeSoto",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=dixie": (
        "Dixie County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-dixie-voterfocus-candidates.html",
        "Dixie",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=franklin": (
        "Franklin County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-franklin-voterfocus-candidates.html",
        "Franklin",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=gadsden": (
        "Gadsden County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-gadsden-voterfocus-candidates.html",
        "Gadsden",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=gilchrist": (
        "Gilchrist County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-gilchrist-voterfocus-candidates.html",
        "Gilchrist",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=glades": (
        "Glades County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-glades-voterfocus-candidates.html",
        "Glades",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=gulf": (
        "Gulf County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-gulf-voterfocus-candidates.html",
        "Gulf",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=hamilton": (
        "Hamilton County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-hamilton-voterfocus-candidates.html",
        "Hamilton",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=hardee": (
        "Hardee County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-hardee-voterfocus-candidates.html",
        "Hardee",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=hendry": (
        "Hendry County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-hendry-voterfocus-candidates.html",
        "Hendry",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=highlands": (
        "Highlands County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-highlands-voterfocus-candidates.html",
        "Highlands",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=holmes": (
        "Holmes County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-holmes-voterfocus-candidates.html",
        "Holmes",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=indianriver": (
        "Indian River County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-indianriver-voterfocus-candidates.html",
        "Indian River",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=jackson": (
        "Jackson County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-jackson-voterfocus-candidates.html",
        "Jackson",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=jefferson": (
        "Jefferson County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-jefferson-voterfocus-candidates.html",
        "Jefferson",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=lafayette": (
        "Lafayette County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-lafayette-voterfocus-candidates.html",
        "Lafayette",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=levy": (
        "Levy County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-levy-voterfocus-candidates.html",
        "Levy",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=liberty": (
        "Liberty County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-liberty-voterfocus-candidates.html",
        "Liberty",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=madison": (
        "Madison County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-madison-voterfocus-candidates.html",
        "Madison",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=martin": (
        "Martin County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-martin-voterfocus-candidates.html",
        "Martin",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=monroe": (
        "Monroe County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-monroe-voterfocus-candidates.html",
        "Monroe",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=nassau": (
        "Nassau County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-nassau-voterfocus-candidates.html",
        "Nassau",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=okaloosa": (
        "Okaloosa County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-okaloosa-voterfocus-candidates.html",
        "Okaloosa",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=okeechobee": (
        "Okeechobee County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-okeechobee-voterfocus-candidates.html",
        "Okeechobee",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=putnam": (
        "Putnam County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-putnam-voterfocus-candidates.html",
        "Putnam",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=santarosa": (
        "Santa Rosa County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-santarosa-voterfocus-candidates.html",
        "Santa Rosa",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=seminole": (
        "Seminole County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-seminole-voterfocus-candidates.html",
        "Seminole",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=suwannee": (
        "Suwannee County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-suwannee-voterfocus-candidates.html",
        "Suwannee",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=taylor": (
        "Taylor County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-taylor-voterfocus-candidates.html",
        "Taylor",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=union": (
        "Union County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-union-voterfocus-candidates.html",
        "Union",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=wakulla": (
        "Wakulla County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-wakulla-voterfocus-candidates.html",
        "Wakulla",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=walton": (
        "Walton County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-walton-voterfocus-candidates.html",
        "Walton",
    ),
    "https://www.voterfocus.com/CampaignFinance/candidate_pr.php?c=washington": (
        "Washington County Supervisor of Elections",
        "Candidate Reports, November 3, 2026 General Election",
        "fl-2026-general-washington-voterfocus-candidates.html",
        "Washington",
    ),
}
DISTRICT = re.compile(
    r"(?i)community development district|water control district|improvement district|drainage district|"
    r"recreation district|soil and water|port authority|inlet district"
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
            for folder in MANUAL_DIRS:
                if not path.exists():
                    path = folder / file
            if not path.exists() and url.startswith("https://www.voterfocus.com/"):
                raw.fetch(SOURCE, url, suffix=".html")
                continue
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
    rows: list[dict[str, str | int | None]] = []
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
        if not people:
            continue
        for name in people:
            rows.append(
                {"district": district, "seat": seat, "vote": int(vote[1]) if vote else 1, "name": name, "url": url}
            )
    return rows


def pasco(url: str) -> list[dict[str, str | int | None]]:
    rows = []
    district = ""
    header_seat = ""
    entry: dict[str, str] | None = None
    for line in pdf_text(url).splitlines():
        stripped = line.strip()
        if re.match(r"^[A-Z][\w .&'-]+ (?:Community Development )?District$", stripped) or stripped.endswith(
            "Development District"
        ):
            district = stripped
            header_seat = ""
            entry = None
            continue
        if re.match(r"^(?:Mosquito Control, Seat \d+|[A-Z][\w ]+ ISD)$", stripped):
            seat_header = re.search(r",\s*(Seat \d+)$", stripped)
            header_seat = seat_header[1] if seat_header is not None else ""
            district = clean_district(re.sub(r",\s*Seat \d+$", "", stripped))
            if district == "Mosquito Control":
                district = "Pasco County Mosquito Control District"
            entry = None
            continue
        found = re.match(r"^\s*([A-Z]{3})\s+(\S.*?, .*?)\s{2,}(\S.*?)\s{2,}(\S.*)$", line)
        if found:
            entry = {"name": flip(found[2]), "status": found[4].strip(), "seat": ""}
            if entry["status"].startswith(("Qualified", "Unopposed", "Elected")) and district:
                rows.append({"district": district, "seat": header_seat, "vote": 1, "name": entry["name"], "url": url})
                entry["row"] = "1"
            continue
        seat = re.match(r"^\s{20,}Seat (\d+)\s*$", line)
        if seat and entry is not None and entry.get("row"):
            rows[-1]["seat"] = f"Seat {seat[1]}"
    return rows


def cdd(text: str) -> str:
    text = re.sub(r"(?i)\bComm\.? Devel(?:opment)?\b", "Community Development", text)
    return re.sub(r"(?i)\bCDD\b|\bCommunity Development Dist\.?(?=\s|$)", "Community Development District", text)


def clean_district(text: str) -> str:
    text = re.sub(r"^Office:\s*", "", html.unescape(text).strip())
    text = re.sub(r"^(?:Supervisor|Trustee),\s*|\s*-$", "", text)
    text = re.sub(r"(?i)\s+(?:Member|Board)$", "", text)
    text = re.sub(r"(?i)\bDist\.?$", "District", text)
    text = re.sub(r"Community Development$", "Community Development District", text)
    text = re.sub(
        r"(?i)soil (?:and|&) water conservation(?: group)?|soil (?:and|&) water", "Soil and Water Conservation", text
    )
    text = re.sub(r"\s*\([^)]*\bDiv(?:ision)?\)", "", text)
    text = re.sub(r"\bFD\b", "Fire District", text)
    text = re.sub(r"(?i)\bMSBD\b", "Municipal Service District", text)
    text = re.sub(r"(?i)\bISD\b", "Independent Special District", text)
    text = re.sub(r"(?i)\bECUA\b", "Emerald Coast Utilities Authority", text)
    text = re.sub(r"(?i)\bNorthern PBC\b", "Northern Palm Beach County", text)
    text = re.sub(r"(?i)^Greeneway\b", "Greene Way", text)
    text = re.sub(r"(?i)\bspec\.", "Special", text)
    text = re.sub(r"(?i)\bcont\.", "Control", text)
    text = re.sub(r"(?i)\bres\.", "Rescue", text)
    text = re.sub(r"\s*&\s*", " and ", text)
    text = re.sub(r"Fire and Rescue Dist\.", "Fire and Rescue District", text)
    text = re.sub(r"Fire Control Dist\.", "Fire Control District", text)
    return " ".join(cdd(text).split())


VOTERFOCUS = re.compile(
    r"(?i)\bV?CDD\b|comm(?:unity|\.?) devel|soil (?:and|&) water|improvement dist|fire|port of|common facilities|"
    r"stewardship|mosquito|lakes management|services district|utility dependent|recreation district|inlet commission|"
    r"beach (?:&|and) park|dev(?:elopment|\.)? dist|port authority|(?:water|wastewater|sewer) (?:&|and) (?:sewer|water)? ?district|"
    r"water (?:board|district|control)|wastewater district|mosquito|utility board|\becua\b|\bmsbd\b|\bisd\b|"
    r"water authority|bridge authority|hyacinth|conservancy|drainage|\bFD\b"
)


def split_office(office: str) -> tuple[str, str]:
    text = re.sub(r"(?i)^Office:\s*", "", html.unescape(office).strip())
    text = re.sub(r"(?i)\s*-\s*gen(?:eral|\.)?\s*election only", "", text)
    text = re.sub(r"(?i)^Supervisor,\s*", "", text)
    text = re.sub(r"(?i)^CDD,\s*(.*?),\s*(?=(?:Seat|Group|Grp)\b)", r"\1 CDD, ", text)
    text = re.sub(r"(?i)\bVCDD\s*#?\s*(\d+)", r"Village Community Development District No. \1", text)
    text = re.sub(r"(?i)\bVILLAGES CDD\s*#\s*(\d+)", r"Village Community Development District No. \1", text)
    text = re.sub(r"(?i)\s*-?\s*Board of Supervisors?\b", "", text)
    text = re.sub(r"(?i)\bCDD\s+Supervisor\b", "CDD", text)
    text = re.sub(r"(?i)^CDD\s*-\s*(.*?)\s*-\s*(?=Seat)", r"\1 CDD, ", text)
    lettered = re.match(r"(?i)(.*?)\s*(?:Member\s*)?Seat\s+([A-D])\s*$", text)
    if lettered is not None:
        return clean_district(lettered[1]), f"Seat {lettered[2].upper()}"
    found = re.match(r"(?i)(.*?)(?:[ ,]+(?:Seat|Dist\.?|District|Group|Grp)\.?\s*|\s+-\s*|\s+)(\d+)\s*$", text)
    if found is None:
        return clean_district(text), ""
    return clean_district(re.sub(r"[ ,-]+$", "", found[1])), f"Seat {found[2]}"


def voterfocus(url: str, county: str) -> list[dict[str, str | int | None]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    groups: dict[str, list[str]] = {}
    for part in text.split('<div class="col-xs-12 officegroup">')[1:]:
        office = html.unescape(required(re.search(r'officename">(.*?)</div>', part, re.DOTALL))[1]).strip()
        if not VOTERFOCUS.search(office) or re.search(
            r"(?i)\bcounty (?:commission|judge|court|clerk|sheriff|school)|\bjudge\b|\bcircuit\b|\bschool board\b",
            office,
        ):
            continue
        for chunk in part.split("detailrow candidate")[1:]:
            name = re.search(r'cf_indent" role="gridcell">\s*(.*?)\s*</div>', chunk, re.DOTALL)
            status = re.search(r'<span class="for-screen-reader">status</span>(.*?)</div>', chunk, re.DOTALL)
            if name is None or status is None:
                continue
            status_text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", status[1])).split())
            if re.search(r"-\s*(?:Qualified|Runoff|Elected|Advanced|Unopposed)\s*\)$", status_text):
                person = re.sub(r"\s*\([A-Z]{2,3}\)$", "", " ".join(html.unescape(name[1]).split()))
                groups.setdefault(office, []).append(person)
    rows: list[dict[str, str | int | None]] = []
    for office, people in groups.items():
        if not people:
            continue
        district, seat = split_office(office)
        district = re.sub(r"(?i)\s*-?\s*district\s*-?$", "", district) if "Soil and Water" in district else district
        district = re.sub(r"(?i)\s+dist$", " District", district)
        if district.endswith("Soil and Water Conservation"):
            district = f"{county} {district} District"
        district = " ".join(
            word.capitalize() if word.isupper() and len(word) > 1 else word for word in district.split()
        )
        if district == "Mosquito Control":
            district = f"{county} County Mosquito Control District"
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
        people = re.findall(r'<tr class="Status-(?:Q|EU|E)">.*?<h4>(.*?)</h4>', part, re.DOTALL)
        if not people:
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


def st_johns(url: str) -> list[dict[str, str | int | None]]:
    rows = []
    district = ""
    seat = ""
    for line in pdf_text(url).splitlines():
        header = re.match(
            r"\s*(.*? Community Development District|Anastasia Mosquito Control District"
            r"|Municipal Service District of Ponte Vedra Beach|St\. Augustine Port, Waterway and Beach Commission)"
            r" – (?:Seat|Group) (\d+) / ",
            line,
        )
        if header is not None:
            district, seat = header[1], f"Seat {header[2]}"
            if district.startswith("Municipal Service District of "):
                district = "Ponte Vedra Beach Municipal Service District"
            elif district.startswith("St. Augustine Port"):
                district = "St Augustine Port"
        elif not line.strip() or " / " in line:
            district = ""
        elif district and "No One Qualified" not in line:
            name = re.split(r"\s{2,}", line.strip())[0]
            rows.append({"district": district, "seat": seat, "vote": 1, "name": name, "url": url})
    return rows


def parse() -> None:
    rows: list[dict[str, str | int | None]] = []
    for url, (_, _, _, county) in SOURCES.items():
        file = SOURCES[url][2]
        parsers = {"broward.pdf": broward, "brevard.html": brevard, "pasco.pdf": pasco, STJ: st_johns}
        if file in parsers:
            found = parsers[file](url)
        elif file.startswith("lee-"):
            found = lee(url)
        else:
            found = voterfocus(url, county)
        for row in found:
            row["county"] = county
        rows.extend(found)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


COUNTY_FIPS = {
    "Alachua": "12001",
    "Baker": "12003",
    "Bradford": "12007",
    "Calhoun": "12013",
    "Citrus": "12017",
    "Columbia": "12023",
    "DeSoto": "12027",
    "Dixie": "12029",
    "Franklin": "12037",
    "Gadsden": "12039",
    "Gilchrist": "12041",
    "Glades": "12043",
    "Gulf": "12045",
    "Hamilton": "12047",
    "Hardee": "12049",
    "Hendry": "12051",
    "Highlands": "12055",
    "Holmes": "12059",
    "Indian River": "12061",
    "Jackson": "12063",
    "Jefferson": "12065",
    "Lafayette": "12067",
    "Levy": "12075",
    "Liberty": "12077",
    "Madison": "12079",
    "Martin": "12085",
    "Monroe": "12087",
    "Nassau": "12089",
    "Okaloosa": "12091",
    "Okeechobee": "12093",
    "Putnam": "12107",
    "Santa Rosa": "12113",
    "Seminole": "12117",
    "Suwannee": "12121",
    "Taylor": "12123",
    "Union": "12125",
    "Wakulla": "12129",
    "Walton": "12131",
    "Washington": "12133",
    "Bay": "12005",
    "Brevard": "12009",
    "Broward": "12011",
    "Charlotte": "12015",
    "Clay": "12019",
    "Collier": "12021",
    "Duval": "12031",
    "Escambia": "12033",
    "Flagler": "12035",
    "Hernando": "12053",
    "Hillsborough": "12057",
    "Lake": "12069",
    "Lee": "12071",
    "Leon": "12073",
    "Manatee": "12081",
    "Marion": "12083",
    "Miami-Dade": "12086",
    "Orange": "12095",
    "Osceola": "12097",
    "Palm Beach": "12099",
    "Pasco": "12101",
    "Pinellas": "12103",
    "Polk": "12105",
    "Sarasota": "12115",
    "St. Johns": "12109",
    "St. Lucie": "12111",
    "Sumter": "12119",
    "Volusia": "12127",
}
GENERIC = frozenset({"community", "development", "district", "dist", "cdd", "the", "of", "and", "at", "no", "control"})
GENERIC |= frozenset({"rescue", "service", "services", "protection", "tax", "special", "co", "county", "town"})
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def words(name: str) -> frozenset[str]:
    text = re.sub(r"(?i)\([^)]*county\)|\bnew\b", "", name)
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("-", " "))
    return frozenset(ROMAN.get(word, word) for word in text.split() if word not in GENERIC)


ALIASES = {
    ("Monroe", "Monroe County Mosquito Control District"): "Florida Keys (monroe County) Mosquito Control District",
    ("Lee", "Captiva Fire District"): "Captiva Island Fire Control District",
}


def census_name(county: str, district: str, governments: dict[str, list[str]]) -> str:
    key = words(district)
    names = governments.get(COUNTY_FIPS[county], [])
    alias = ALIASES.get((county, district))
    if alias in names:
        return alias
    exact = {name for name in names if words(name) == key}
    if len(exact) == 1:
        return exact.pop()
    close = {
        name
        for name in names
        if key
        and words(name)
        and (words(name) <= key or key <= words(name))
        and len(words(name) & key) >= 2
        and not any(word.isdigit() for word in words(name) ^ key)
    }
    if close and not exact:
        best = min(len(words(name) ^ key) for name in close)
        nearest = {name for name in close if len(words(name) ^ key) == best}
        if len(nearest) == 1:
            return nearest.pop()
    return district


def canonical(frame: pl.DataFrame) -> pl.DataFrame:
    governments: dict[str, list[str]] = {}
    with connect() as conn:
        for county, name in conn.execute(
            "select county, name from coverage_government where state = 'FL' and kind in ('special', 'college')"
            " and id !~ '^polity:'"
        ):
            governments.setdefault(county, []).append(name)
    names = [census_name(county, district, governments) for district, county in zip(frame["district"], frame["county"])]
    return frame.with_columns(
        district=pl.Series(names),
        county=pl.Series([f"{county} County" for county in frame["county"]]),
        name=pl.col("name").str.replace(r"\.$", ""),
    )


def emit() -> Path:
    frame = canonical(pl.read_parquet(PARSED / "candidates.parquet")).unique(
        subset=["district", "seat", "name"], keep="first", maintain_order=True
    )
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
        tacit=pl.len().over("district", "seat") <= pl.col("vote"),
        role=pl.Series(roles),
        function=pl.Series(
            [next((value for pattern, value in FUNCTIONS if re.search(pattern, d)), None) for d in frame["district"]]
        ),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="FL",
        summary="Florida community development, water control, drainage, improvement, and recreation district board races on the November 3, 2026 ballot, with candidates from the Broward, Brevard, Pasco, Duval, Hillsborough, Lee, Miami-Dade, Orange, Palm Beach, Pinellas, Polk, Osceola, Collier, Escambia, Lake, Manatee, Marion, Sarasota, Volusia, St. Lucie, Clay, Sumter, Flagler, Charlotte, Hernando, Leon, Bay, and St. Johns County election offices",
        sources={url: (publisher, title) for url, (publisher, title, _, _) in SOURCES.items()},
        frame=frame,
    )
