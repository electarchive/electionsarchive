import html
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ceda, govcand, govmint, local_2026
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect

SOURCE = "us.ca_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
COUNTIES = {
    "06073": "San Diego",
    "06059": "Orange",
    "06067": "Sacramento",
    "06037": "Los Angeles",
    "06065": "Riverside",
    "06071": "San Bernardino",
    "06085": "Santa Clara",
    "06001": "Alameda",
    "06013": "Contra Costa",
    "06019": "Fresno",
    "06029": "Kern",
    "06107": "Tulare",
    "06097": "Sonoma",
    "06041": "Marin",
    "06053": "Monterey",
    "06077": "San Joaquin",
    "06007": "Butte",
    "06017": "El Dorado",
    "06061": "Placer",
    "06079": "San Luis Obispo",
    "06081": "San Mateo",
    "06031": "Kings",
    "06047": "Merced",
    "06083": "Santa Barbara",
    "06099": "Stanislaus",
    "06111": "Ventura",
    "06089": "Shasta",
    "06101": "Sutter",
    "06055": "Napa",
    "06095": "Solano",
    "06023": "Humboldt",
    "06025": "Imperial",
    "06087": "Santa Cruz",
    "06075": "San Francisco",
    "06113": "Yolo",
    "06105": "Trinity",
    "06093": "Siskiyou",
    "06009": "Calaveras",
    "06005": "Amador",
    "06103": "Tehama",
    "06035": "Lassen",
    "06015": "Del Norte",
    "06027": "Inyo",
    "06045": "Mendocino",
    "06033": "Lake",
    "06039": "Madera",
    "06011": "Colusa",
    "06051": "Mono",
    "06115": "Yuba",
    "06091": "Sierra",
    "06109": "Tuolumne",
    "06069": "San Benito",
}
LISTED = {
    "06009": "CA-06009-calaveras",
    "06005": "CA-06005-sutter-creek",
    "06103": "CA-06103-corning",
    "06035": "CA-06035-westwood",
    "06093": "CA-06093-siskiyou-cities",
    "06027": "CA-06027-bishop",
    "06015": "CA-06015-crescent-city",
}
KEPT = re.compile(
    r"FIRE|WATER|UTILIT|HEALTH|IRRIGATION|WASTEWATER|SERVICES|SRVCS|PARK|LIBRARY|SANITATION|SANITARY|CEMETERY|SEWER|CONSERVATION|FLOOD"
    r"|COLLEGE|HOSPITAL|VECTOR|MOSQUITO|RESOURCE|TRANSIT|HARBOR|AIRPORT|DRAINAGE|LEVEE|LIGHTING|MEMORIAL|POLICE|PEST|RECREATION"
    r"|OPEN SPACE|RAPID|REGIONAL|HIGHWAY|WEED|ROAD|COMMUNITY|\bPORT\b|HEATHCARE|RESORT|IMPROVEMENT|GEOLOGIC"
)
DROPPED = re.compile(
    r"SCHL|SCHOOL|UNIFIED|ELEMENTARY|UNION HIGH|HIGH SCH|OFFICE OF EDUCATION|BOARD OF EDUCATION|COMM PLAN|PLANNING|SUPERVISORS|ASSESSOR|JUDGE|COURT(?!LAND)|^CITY|^TOWN OF|CITY OF|CITY COUNCIL|CONGRESS(?! VALLEY)|ASSEMBLY|SENATE|COMMITTEE|PLAN GRP|PLAN GROUP|COMMUNITY PLAN|SPONSOR GROUP|COUNCIL|MAYOR|CLERK|TREASURER|SHERIFF|ATTORNEY|CORONER|AUDITOR"
)
WORDS = (
    (r"\bMUNI\b", "MUNICIPAL"),
    (r"\bGROSSMNT-CUY\b", "GROSSMONT-CUYAMACA"),
    (r"\bCOM(?:M|MUNITY)? COLLEGE\b", "COMMUNITY COLLEGE"),
    (r"\bCONS\b", "CONSOLIDATED"),
    (r"\bPROT\b", "PROTECTION"),
    (r"\bSRVCS\b", "SERVICES"),
    (r"\bHEATLH\b", "HEALTH"),
    (r"\bHEATHCARE\b", "HEALTHCARE"),
    (r"\bDIST\b", "DISTRICT"),
    (r"\bCTY\b", "COUNTY"),
    (r"\bREC\b", "RECREATION"),
)
SEAT = re.compile(
    r"^(.+?)\s+-\s+(DIV(?:ISION)?\.?|ZONE|AREA|TRUSTEE AREA|DISTRICT)\s+(?:NO\.\s+)?(\w+)(?:\s+\(Short Term\))?$",
    re.IGNORECASE,
)
SEAT_WORDS = {"ZONE": "Zone", "AREA": "Area", "TRUSTEE AREA": "Trustee Area", "DISTRICT": "District"}
PERSON = re.compile(r"^([A-Z][^,]*?), (.+?)(?: - ([A-Z]{2,4}))?\s{3,}\S.*\s(YES|NO)\s*$")


ALIASES: dict[str, str] = {}


def usable(label: str) -> bool:
    return KEPT.search(label.upper()) is not None and DROPPED.search(label.upper()) is None


def full_name(label: str) -> str:
    for short, full in WORDS:
        label = re.sub(short, lambda found: full if found[0].isupper() else full.title(), label, flags=re.IGNORECASE)
    text = cased(label).replace(" Del ", " del ").replace(" And ", " and ").replace(" Of ", " of ")
    text = text if re.search(r"\b(?:District|Authority|Agency|System)\b|County$", text) else f"{text} District"
    return ALIASES.get(text, text)


def feed(fips: str) -> tuple[str, str, str]:
    if fips in local_2026.PUBLISHERS:
        return (local_2026.FILES[fips], *local_2026.PUBLISHERS[fips][1:])
    return local_2026.FEEDS[f"CA-{fips}"]


INBOX = {
    "06089": "shasta.pdf",
}
OWN = {"06111", *INBOX}


WRAPPED_HEAD = re.compile(
    r"(Contest: \d{4} - [^\n]*?Governing Board)\s*\n\s+Vote for\s*\n\s+Member,\s*\n\s+(\d+) (TRA \d+)\s+(On Ballot: \w+)"
)


def lines(fips: str) -> list[str]:
    if fips in INBOX_DOCS:
        text = subprocess.run(
            ["pdftotext", "-layout", str(inbox_doc(fips)), "-"], capture_output=True, check=True
        ).stdout.decode()
        return re.sub(r"(?<=[a-z])(Vote for)", r"  \1", text).splitlines()
    url = feed(fips)[0]
    if fips in INBOX:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            file = DATA / "inbox" / "y26" / "ca" / INBOX[fips]
            raw.store(
                SOURCE,
                url,
                file.read_bytes(),
                suffix=file.suffix,
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="application/pdf",
            )
    path = raw.cached(SOURCE if fips in OWN else local_2026.SOURCE, url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    return WRAPPED_HEAD.sub(r"\1 Member, \3   Vote for \2   \4", text).splitlines()


def san_diego() -> list[dict[str, str | int | bool]]:
    rows = []
    text = lines("06073")
    keep = False
    for index, line in enumerate(text):
        if found := re.match(r" Election = (YES|NO)\s+Vote for (\d+)", line):
            heading = next(previous.strip() for previous in reversed(text[:index]) if previous.strip())
            seats = int(found[2])
            unexpired = "Short Term" in text[index + 1] or "(Short Term)" in heading
            keep = KEPT.search(heading) is not None and DROPPED.search(heading) is None
            canceled = found[1] == "NO"
            seat = SEAT.match(re.sub(r"\s*\(Short Term\)", "", heading))
            body, label = (
                (seat[1], f"{SEAT_WORDS.get(seat[2].upper().rstrip('.'), 'Division')} {seat[3]}")
                if seat
                else (heading, "")
            )
            body = re.sub(r"\s*\(.*$", "", body)
        elif keep and (person := PERSON.match(line)) and person[4] == "YES":
            rows.append(
                {
                    "county": "San Diego",
                    "district": full_name(body),
                    "seat": label or ("Short Term" if unexpired else ""),
                    "role": "Director",
                    "seats": seats,
                    "name": cased(f"{person[2]} {person[1]}"),
                    "unexpired": unexpired,
                    "tacit": canceled,
                }
            )
    return rows


def orange() -> list[dict[str, str | int | bool]]:
    rows = []
    heading = seat = role = ""
    seats = 1
    keep = unexpired = False
    for line in lines("06059"):
        if found := re.match(r"^Nonpartisan (.+) (Director|Trustee)$", line):
            heading, role = found[1], found[2]
            keep = re.search(r"SCHOOL|COLLEGE|^COUNTY|CITY OF|COUNCIL", heading) is None
            seat, unexpired = "", False
        elif keep and (found := re.match(r"^\s+\*?\s*\d{4}\s+(.+?)\s*$", line)):
            number = re.search(r"Div(?:ision|\.)? (\d+)", found[1])
            seat = f"Division {number[1]}" if number else ""
            unexpired = "Short Term" in found[1]
            if heading.startswith("MUNICIPAL WATER"):
                heading = "Municipal Water District of Orange County"
        elif keep and (found := re.search(r"\s(\d+)\s+\d+\s+\d+\s+ON BALLOT\s*$", line)):
            seats = int(found[1])
        elif keep and (found := re.fullmatch(r"(?:\s{8}Candidate\(s\):\s+| {33})([A-Z][A-Z .'\"-]{3,})", line)):
            rows.append(
                {
                    "county": "Orange",
                    "district": full_name(re.sub(r"\bDIST\.", "DISTRICT", heading)),
                    "seat": seat or ("Short Term" if unexpired else ""),
                    "role": role,
                    "seats": seats,
                    "name": cased(found[1]),
                    "unexpired": unexpired,
                }
            )
    return rows


SACRAMENTO = re.compile(
    r"^(?P<body>.+?),? (?P<role>Director|Trustee)(?:,? (?P<seat>(?:Division|District|Ward) \d+))?(?P<short>\s*-\s*Short Term)?\s*$"
)


def sacramento() -> list[dict[str, str | int | bool]]:
    rows = []
    page = raw.cached(local_2026.SOURCE, feed("06067")[0]).path.read_text()
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", page)))
    text = text.replace("\xa0", " ")
    text = [line.strip() for line in text.splitlines() if line.strip()]
    contest = None
    seats = 1
    live = False
    for index, line in enumerate(text):
        if found := re.match(r"Contest: \d+\s*-\s*(.+)$", line):
            contest = SACRAMENTO.match(" ".join(found[1].split()))
            if contest is not None and not KEPT.search(contest["body"].upper()):
                contest = None
            seats, live = 1, False
        elif contest is None:
            continue
        elif found := re.match(r"Vote for (\d+)", line):
            seats = int(found[1])
        elif line.startswith("On Ballot:"):
            live = line.endswith("Yes")
        elif line == "Qualified" and re.fullmatch(r"\d+", text[index - 1]):
            body = contest["body"]
            if DROPPED.search(body.upper()) or re.search(r"(?i)^city of|school|college|^el dorado", body):
                continue
            seat = contest["seat"] or ""
            rows.append(
                {
                    "county": "Sacramento",
                    "district": full_name(body),
                    "seat": seat or ("Short Term" if contest["short"] else ""),
                    "role": contest["role"],
                    "seats": seats,
                    "name": cased(re.sub(r"\s{2,}.*$", "", text[index + 1])),
                    "unexpired": contest["short"] is not None,
                    "tacit": not live,
                }
            )
    return rows


def los_angeles() -> list[dict[str, str | int | bool]]:
    rows = []
    text = lines("06037")
    keep = False
    expect = False
    for index, line in enumerate(text):
        if found := re.match(r"(\d{5})-(.+?)\s{2,}Vote for (\d+)", line):
            more = (
                text[index + 1]
                if re.match(r"[A-Za-z0-9]", text[index + 1]) and not re.match(r"CONTEST|PARTY|NON", text[index + 1])
                else ""
            )
            heading = " ".join(f"{found[2]} {more}".split())
            seats = int(found[3])
            unexpired = "SPECIAL ELECTION" in heading
            heading = heading.replace("SPECIAL ELECTION ", "")
            parts = re.match(
                r"(.+?) Member, Board of (Directors|Trustees)(?:, (?:Division|Trustee Area No\.) (\d+))?",
                heading,
                re.IGNORECASE,
            )
            keep = (
                parts is not None
                and KEPT.search(parts[1].upper() + " LIBRARY") is not None
                and DROPPED.search(parts[1]) is None
            )
            if keep:
                body = parts[1]
                role = "Director" if parts[2].lower() == "directors" else "Trustee"
                seat = f"{'Trustee Area' if role == 'Trustee' else 'Division'} {int(parts[3])}" if parts[3] else ""
            expect = False
        elif keep and not line.strip():
            expect = True
        elif keep and expect and (found := re.match(r" {15,25}\*?([A-Z][A-Z .,'\"ÁÉÍÓÚÑ()-]+?)(?:\s{2,}.*)?$", line)):
            if not re.match(r"(PARTY PREFERENCE|NON PARTISAN|NONPARTISAN)", found[1]):
                rows.append(
                    {
                        "county": "Los Angeles",
                        "district": full_name(body),
                        "seat": seat or ("Short Term" if unexpired else ""),
                        "role": role,
                        "seats": seats,
                        "name": cased(found[1]),
                        "unexpired": unexpired,
                    }
                )
            expect = False
    return rows


PROOF_HEAD = re.compile(
    r"^(?:Non-?Partisan|NONPARTISAN OFFICES) (?:DISTRICT|SCHOOL) (?P<body>.+?) (?:For )?(?P<role>Harbor Commissioner|Director|Trustee|Governing Board Member|Member, Board of (?:Directors|Trustees))(?P<rest>.*)$",
    re.IGNORECASE,
)
PROOF_SEAT = re.compile(r"(Division|Zone|District Area|Trustee Area|Area|Seat|At Large)(?: No\.?)? ?(\d+)")


def proof_list(
    fips: str, county: str, indent: int, qualified_only: bool = False, doc: str = ""
) -> list[dict[str, str | int | bool]]:
    qualified: set[tuple[tuple[str, str, str, bool], str]] = set()
    last = None
    contests: dict[tuple[str, str, str, bool], list[str]] = {}
    incumbents: dict[tuple[str, str, str, bool], int] = {}
    canceled: dict[tuple[str, str, str, bool], bool] = {}
    votes: dict[tuple[str, str, str, bool], int] = {}
    current = None
    text = lines(fips)
    gap = f" {{{indent}}}"
    for index, line in enumerate(text):
        if found := PROOF_HEAD.match(line):
            rest = found["rest"]
            seat = PROOF_SEAT.search(rest)
            label = (
                f"{seat[1].replace('District Area', 'Area')} {seat[2]}"
                if seat
                else ("Veteran" if "Veteran" in rest else "")
            )
            role = (
                "Commissioner"
                if "commissioner" in found["role"].lower()
                else "Trustee"
                if re.search(r"trustee|governing", found["role"].lower())
                else "Director"
            )
            current = (
                full_name(found["body"].replace("COMMUNITY SERVICE DISTRICT", "COMMUNITY SERVICES DISTRICT")),
                role,
                label,
                re.search(r"Short Term|\bST\b", rest) is not None,
            )
            contests.setdefault(current, [])
            incumbents.setdefault(current, 0)
        elif current is None:
            continue
        elif last is not None and re.match(r"\s+Qualified Date:", line):
            qualified.add(last)
        elif found := re.search(r"\s(\d+)\s+\d+\s+\d+\s+(NOT ON BALLOT|ON BALLOT)\s*$", line):
            canceled.setdefault(current, found[2] == "NOT ON BALLOT")
            votes.setdefault(current, int(found[1]))
        elif found := re.match(r"\s+Candidate\(s\):\s+(\S.*?)\s*$", line):
            name = cased(found[1]) if found[1].isupper() else found[1]
            if name not in contests[current]:
                contests[current].append(name)
            last = (current, name)
        elif re.match(r"\s+Incumbent\(s\):", line) or re.match(
            gap + r"\S.*\s{3,}(?:Elected|Appointed in Lieu|Appointed to Vacancy|Vacated)\s*$", line
        ):
            incumbents[current] += 1
        elif (
            re.match(gap + r"\S", line)
            and not re.match(
                gap
                + r"(?:Requirements|Sigs|Declaration|Nomination|Candidate|Code|Incumbent|Appointed|Director|Vacant)",
                line,
            )
            and (re.match(r"\s*$", text[index - 1]) or "Shared with" in text[index - 1])
        ):
            name = re.sub(r"\s{2,}.*$", "", line.strip())
            name = cased(name) if name.isupper() else name
            if name not in contests[current]:
                contests[current].append(name)
            last = (current, name)
    rows = []
    for key, names in contests.items():
        district, role, label, short = key
        if qualified_only:
            names = [name for name in names if (key, name) in qualified]
        seats = 1 if label else votes.get(key) or max(incumbents[key], 1)
        if not names or DROPPED.search(district.upper()):
            continue
        for name in names:
            rows.append(
                {
                    "county": county,
                    "district": district,
                    "seat": label or ("Short Term" if short else ""),
                    "role": role,
                    "seats": seats,
                    "name": name,
                    "unexpired": short,
                    "tacit": canceled.get(key, False) and (not qualified_only or len(names) <= seats),
                    "doc": doc,
                }
            )
    return rows


def ventura() -> list[dict[str, str | int | bool]]:
    return [
        {**row, "district": full_name(str(row["district"]).replace(" For Library District", ""))}
        for row in proof_list("06111", "Ventura", 33, qualified_only=True)
    ]


def riverside() -> list[dict[str, str | int | bool]]:
    return proof_list("06065", "Riverside", 33)


def fresno() -> list[dict[str, str | int | bool]]:
    return proof_list("06019", "Fresno", 30)


def shasta() -> list[Row]:
    return qualified_list(
        "06089",
        "Shasta",
        (
            (r"\bBurney Fire - 2 yr\b", "Burney Fire Protection District (Short Term)"),
            (r"\bFall River Vly Fire- 2 yr\b", "Fall River Valley Fire Protection District (Short Term)"),
            (r"District - 2 yr\b", "District (Short Term)"),
        ),
        True,
    )


HUMBOLDT_BODIES = (
    ("HUMBOLDT BAY HARBOR", "Humboldt Bay Harbor, Recreation and Conservation District"),
    ("HUMBOLDT BAY MUNICIPAL", "Humboldt Bay Municipal Water District"),
    ("RESORT IMPROVEMNT DST#1", "Resort Improvement District No. 1"),
    ("SO HUMBOLDT CHD", "South Humboldt Community Healthcare District"),
    ("GARBERVILLE SANITARY", "Garberville Sanitary District"),
)
HUMBOLDT_HEAD = re.compile(r"^\s*(6\d{3}) (.+?)\s*$")
HUMBOLDT_CAND = re.compile(r"^\s*\d+ {5,}(\S.*?)\s*$")


HUMBOLDT = local_2026.FEEDS["CA-06023-s"]


def humboldt() -> list[Row]:
    try:
        raw.cached(SOURCE, HUMBOLDT[0])
    except LookupError:
        file = DATA / "inbox" / "y26" / "ca" / "humboldt.pdf"
        raw.store(
            SOURCE,
            HUMBOLDT[0],
            file.read_bytes(),
            suffix=file.suffix,
            retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
            media_type="application/pdf",
        )
    path = raw.cached(SOURCE, HUMBOLDT[0]).path
    rows: list[Row] = []
    for page in range(1, 11):
        for x in (0, 396):
            text = subprocess.run(
                [
                    "pdftotext",
                    "-layout",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-x",
                    str(x),
                    "-y",
                    "0",
                    "-W",
                    "396",
                    "-H",
                    "612",
                    str(path),
                    "-",
                ],
                capture_output=True,
                check=True,
            ).stdout.decode()
            blocks: list[tuple[tuple[str, str, bool], list[str]]] = []
            for line in text.splitlines():
                if found := HUMBOLDT_HEAD.match(line):
                    label = " ".join(found[2].replace(",", " ").split())
                    division = re.search(r"DIV(?:ISION)? (\d+)", label)
                    short = label.endswith("2-YR")
                    seat = f"Division {division[1]}" if division else ("Short Term" if short else "")
                    for prefix, district in HUMBOLDT_BODIES:
                        if label.startswith(prefix.replace(",", "")):
                            break
                    else:
                        body, kind = re.split(r" (CSD|FPD)\b", found[2].split(",")[0])[:2]
                        district = (
                            f"{cased(body)} {'Community Services' if kind == 'CSD' else 'Fire Protection'} District"
                        )
                    blocks.append(((district, seat, short), []))
                elif blocks and (found := HUMBOLDT_CAND.match(line)):
                    blocks[-1][1].append(found[1])
            for (district, seat, short), names in blocks:
                rows.extend(
                    {
                        "county": "Humboldt",
                        "district": district,
                        "seat": seat,
                        "role": "Director",
                        "seats": 1 if seat or len(names) == 1 else len(names),
                        "name": name,
                        "unexpired": short,
                        "tacit": False,
                        "doc": "06023-s",
                    }
                    for name in names
                )
    return rows


IMPERIAL = (
    "https://elections.imperialcounty.org/wp-content/uploads/2026/08/CandidateStatus_Website-1-17.pdf",
    "Imperial County Registrar of Voters",
    "Imperial County Candidate Status, November 3, 2026 General Election (updated 08/20/2026)",
)
SANTA_CRUZ = (
    "https://votescount.santacruzcountyca.gov/Home/Elections/November3,2026CaliforniaGeneralElection/ContactCandidates.aspx",
    "Santa Cruz County Clerk and Registrar of Voters",
    "Contact Candidates: contests that will appear on the ballot, November 3, 2026 General Election",
)
NEVADA = (
    "https://www.nevadacountyca.gov/CivicAlerts.aspx?AID=local-candidates-announced-for-november-ballot",
    "Nevada County Elections",
    "Local Candidates Announced for November Ballot, posted August 13, 2026",
)
SANTA_CRUZ_WATCH = (
    "https://votescount.santacruzcountyca.gov/Home/Elections/November3,2026CaliforniaGeneralElection/CandidateWatch.aspx",
    "Santa Cruz County Clerk and Registrar of Voters",
    "Candidate Watch: filed candidates by district, November 3, 2026 General Election",
)
YOLO_SPECIAL = (
    "https://elections.yolocounty.gov/424/Special-Districts",
    "Yolo County Assessor/Clerk-Recorder/Elections",
    "Candidate Filings: Special Districts, November 3, 2026 General Election",
)
LAKE_SPECIAL = (
    (
        "https://www.lakecountyca.gov/DocumentCenter/View/17509",
        "Lake County Registrar of Voters",
        "Candidate List, Qualified Candidates Only, School and Special Districts, November 3, 2026 General Election",
    ),
    "lake_special_0814.pdf",
    "application/pdf",
)
LAKE_FINAL = (
    (
        "https://www.lakecountyca.gov/DocumentCenter/View/18067",
        "Lake County Registrar of Voters",
        "Candidate List, Qualified Candidates Only, School Districts, November 3, 2026 General Election",
    ),
    "lake_final_qualified.pdf",
    "application/pdf",
)
MADERA_PROOF = (
    (
        "https://votemadera.com/wp-content/uploads/2026/08/List-of-Candidates-08-12-2026.pdf",
        "Madera County Elections Division",
        "Contest/Candidate Proof List: Contests on Ballot with Qualified Candidates, November 3, 2026 Consolidated General Election",
    ),
    "madera_proof_0812.pdf",
    "application/pdf",
)
MENDOCINO_CERTIFICATES = (
    (
        "https://mendocino.granicusideas.com/meetings/590/agenda_items",
        "Mendocino County Board of Supervisors",
        "Certificates of Appointment under Elections Code Section 10515 for Special Districts, September 1, 2026",
    ),
    "mendocino_cert_10515.pdf",
    "application/pdf",
)
MENDOCINO_ELECTION = (
    (
        "https://www.mendocinocounty.gov/government/assessor-county-clerk-recorder-registrar-of-voters/elections",
        "Mendocino County Elections",
        "Candidate List, To Election, November 3, 2026 General Election",
    ),
    "mendocino_to_election.pdf",
    "application/pdf",
)


def manual(
    folder: str, name: str, url: str, publisher: str, title: str, media_type: str = "application/pdf"
) -> tuple[tuple[str, str, str], str, str]:
    return (url, publisher, title), f"manual/{folder}/{name}", media_type


INBOX_DOCS = {
    "06113-s": (YOLO_SPECIAL, "yolo_special.html", "text/html"),
    "06113-c": (local_2026.FEEDS["CA-06113-s"], "yolo_schools.html", "text/html"),
    "06087-w": (SANTA_CRUZ_WATCH, "santacruz_watch.md", "text/markdown"),
    "06025-s": (IMPERIAL, "imperial_0820.pdf", "application/pdf"),
    "06071-schools": (
        (
            "https://uploads.rov.sbcounty.gov/rov/News/2026/1103/PublicNotice_AppointmentsInLieuSchools.pdf",
            "San Bernardino County Registrar of Voters",
            "November 3, 2026 General Election: School District Appointments in Lieu of Election",
        ),
        "sb_in_lieu.pdf",
        "application/pdf",
    ),
    "06057-s": (NEVADA, "nevada_candidates.html", "text/html"),
    "06087-s": (SANTA_CRUZ, "santacruz_contact.html", "text/html"),
    "06083-o": (
        (
            "https://content.civicplus.com/api/assets/68de6016-df0c-423c-85f5-d67e98023e7a",
            "County of Santa Barbara Elections Division",
            "Qualified Off Ballot Contest/Candidate List for Schools and Special Districts, November 3, 2026 General Election",
        ),
        "santabarbara_offballot.pdf",
        "application/pdf",
    ),
    "06033-s": LAKE_SPECIAL,
    "06033-q": LAKE_FINAL,
    "06039-s": MADERA_PROOF,
    "06045-s": MENDOCINO_CERTIFICATES,
    "06045-e": MENDOCINO_ELECTION,
    "06099-l": (
        (
            "https://www.stancounty.com/bos/minutes/2026/min10-13-26.pdf",
            "Stanislaus County Board of Supervisors",
            "Minutes, October 13, 2026: appointments in lieu of election for the November 3, 2026 General Election",
        ),
        "stanislaus_lieu_1013.pdf",
        "application/pdf",
    ),
    "06079-l": (
        (
            "https://www.slocounty.ca.gov/departments/clerk-recorder/forms-documents/elections-and-voting/current-elections/2026-11-03/list-of-contests-with-and-without-sufficient-candidates-for-the-november-3,-2026,-general-election",
            "County of San Luis Obispo Clerk-Recorder",
            "List of Contests With and Without Sufficient Candidates for the November 3, 2026 General Election",
        ),
        "slo_sufficient.pdf",
        "application/pdf",
    ),
    "06011-s": manual(
        "MF-530",
        "2026 Nov Candidate Watch_202608171247463221.pdf",
        "https://www.countyofcolusaca.gov/1092/Elections",
        "Colusa County Clerk-Recorder",
        "Candidate Filing Status Report, November 3, 2026 General Election",
    ),
    "06011-n": manual(
        "MF-530",
        "NOTICE OF ELECTION November 3, 2026   General Election.pdf",
        "https://www.countyofcolusaca.gov/1091/Notices",
        "Colusa County Clerk-Recorder",
        "Notice of Election, November 3, 2026 General Election",
    ),
    "06001-n": manual(
        "MF-539",
        "ca-alameda-2026-11-03-general-not-on-ballot-candidate-list.html",
        "https://www.acvote.alamedacountyca.gov/candidates/candidate-list",
        "Alameda County Registrar of Voters",
        "Candidate List: General Election, November 3, 2026, contests not on the ballot",
        "text/html",
    ),
    "06051-s": manual(
        "MF-531",
        "mono-2026-general-candidates.html",
        "https://monocounty.ca.gov/2493/Candidates",
        "Mono County Elections",
        "Local Candidates List for the November 3, 2026 General Election",
        "text/html",
    ),
    "06051-g": manual(
        "MF-531",
        "candidates_guide_nov_3_2026_general_v2.pdf",
        "https://monocounty.ca.gov/2493/Candidates#candidates_guide_nov_3_2026_general_v2",
        "Mono County Elections",
        "Candidate's Guide, November 3, 2026 General Election",
    ),
    "06115-s": manual(
        "MF-532",
        "LocalCandidateList.pdf",
        "https://www.yuba.gov/departments/elections/upcoming_election.php",
        "Yuba County Clerk-Recorder",
        "Candidate Filing Status Report, November 3, 2026 General Election",
    ),
    "06115-n": manual(
        "MF-532",
        "WEB_NOE.pdf",
        "https://www.yuba.gov/departments/elections/upcoming_election.php#notice-of-election",
        "Yuba County Clerk-Recorder",
        "Notice of Election, November 3, 2026 General Election",
    ),
    "06091-s": manual(
        "MF-535",
        "Sierra County Local Candidate List_202609281142594073.pdf",
        "https://www.sierracounty.ca.gov/804/Upcoming-Elections",
        "Sierra County Clerk-Recorder",
        "Local Candidate List, November 3, 2026 General Election",
    ),
    "06091-n": manual(
        "MF-535",
        "Election Press Release.pdf",
        "https://www.sierracounty.ca.gov/804/Upcoming-Elections#election-press-release",
        "Sierra County Clerk-Recorder",
        "Offices on the Ballot, November 3, 2026 General Election",
    ),
    "06109-s": (
        (
            "https://www.tuolumnecounty.ca.gov/",
            "Tuolumne County Elections Department",
            "Candidate Filing Report, November 3, 2026 General Election (as of September 16, 2026)",
        ),
        "tuolumne_filing.pdf",
        "application/pdf",
    ),
    "06069-s": manual(
        "MF-538",
        "cfmr037_rosterofcandidates 08312026.pdf",
        "https://www.sanbenitocounty-ca-cre.gov/elections/candidates-campaigns/list-of-candidates-november-2026",
        "San Benito County Registrar of Voters",
        "Roster of Candidates, November 3, 2026 General Election",
    ),
    "06069-n": manual(
        "MF-538",
        "san-benito-2026-11-03-statewide-general-election.html",
        "https://www.sanbenitocounty-ca-cre.gov/elections/candidates-campaigns/november-3-2026-statewide-general-election",
        "San Benito County Registrar of Voters",
        "Contests and Important Information, November 3, 2026 Statewide General Election",
        "text/html",
    ),
    "06105-s": (
        (
            "https://www.trinitycounty.org/DocumentCenter/View/4190",
            "Trinity County Clerk/Recorder/Assessor",
            "Certified List of Candidates, School and Special Districts, November 3, 2026 General Election",
        ),
        "trinity_certified.pdf",
        "application/pdf",
    ),
}
IMPERIAL_HEAD = re.compile(
    r"^\s*(?P<title>\S.*?)\s+-\s+Elect (?P<seats>\d+)(?:\s*\((?P<partial>Partial Term)\))?(?:\s+-\s+CANDIDATE FILING.*)?\s*$"
)
IMPERIAL_SEAT = re.compile(r"^(?P<body>.*?)(?:,|\s-)\s*(?P<kind>District|Division|Trustee Area|Area) (?P<number>\d+)$")


def inbox_doc(key: str) -> Path:
    (url, *_), name, media_type = INBOX_DOCS[key]
    try:
        return raw.cached(SOURCE, url).path
    except LookupError:
        file = DATA / "inbox" / (name if name.startswith("manual/") else f"y26/ca/{name}")
        raw.store(
            SOURCE,
            url,
            file.read_bytes(),
            suffix=file.suffix,
            retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
            media_type=media_type,
        )
        return raw.cached(SOURCE, url).path


def imperial() -> list[Row]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(inbox_doc("06025-s")), "-"], capture_output=True, check=True
    ).stdout.decode()
    rows: list[Row] = []
    current: tuple[str, str, str, int, bool] | None = None
    names: list[str] = []
    canceled = False

    def flush() -> None:
        if current is not None:
            district, role, seat, seats, short = current
            rows.extend(
                {
                    "county": "Imperial",
                    "district": district,
                    "seat": seat,
                    "role": role,
                    "seats": seats,
                    "name": name,
                    "unexpired": short,
                    "tacit": canceled or len(names) <= seats,
                    "doc": "06025-s",
                }
                for name in names
            )

    for line in text.splitlines():
        if line.strip() == "BALLOT MEASURES":
            break
        if found := IMPERIAL_HEAD.match(line):
            flush()
            names = []
            canceled = False
            title = found["title"]
            split = IMPERIAL_SEAT.match(title)
            body = split["body"] if split else title
            short = found["partial"] is not None
            if usable(body):
                label = f"{split['kind']} {split['number']}" if split else ""
                current = (
                    full_name(body),
                    "Trustee" if "College" in body else "Director",
                    label or ("Short Term" if short else ""),
                    1 if label else int(found["seats"]),
                    short,
                )
            else:
                current = None
        elif current is None:
            continue
        elif "NO ELECTION" in line:
            canceled = True
        elif (
            line.strip()
            and len(line) - len(line.lstrip()) < 50
            and not re.search(r"CANDIDATE|EXTENDED|Imperial County|November 3|Updated|Indicates", line)
        ):
            name = re.split(r"\s{2,}", line.strip())[0].rstrip("*").strip()
            if re.match(r"[A-Z]", name):
                names.append(name)
    flush()
    return rows


def santa_cruz() -> list[Row]:
    markup = inbox_doc("06087-s").read_text(errors="replace")
    markup = re.sub(r"</(?:tr|h[1-6]|p|div)>", "\n", markup).replace("</td>", " |")
    text = html.unescape(re.sub(r"<[^>]*>", "", markup))
    rows: list[Row] = []
    section = ""
    current: tuple[str, str, str, int] | None = None
    cells: list[str] = []
    for line in (item.strip().replace("\xa0", " ") for item in text.splitlines()):
        if line in {
            "Boards of Education & Community Colleges",
            "School Districts",
            "Fire Districts",
            "Water Districts",
        }:
            section = line
            current = None
        elif head := re.match(r"^(.+?) ?- (?:(\d+) Seats?|(Trustee Area|Division|Area) (\d+))$", line):
            body = head[1].strip()
            if section != "School Districts" and usable(body):
                label = f"{head[3]} {head[4]}" if head[3] else ""
                current = (
                    full_name(body.upper()),
                    "Trustee" if "College" in body else "Director",
                    label,
                    int(head[2] or 1),
                )
                cells = []
            else:
                current = None
        elif current is not None and line.endswith("|"):
            cells.append(line.removesuffix("|").strip())
            if len(cells) == 6:
                if cells[0] != "Candidate Name":
                    district, role, seat, seats = current
                    rows.append(
                        {
                            "county": "Santa Cruz",
                            "district": district,
                            "seat": seat,
                            "role": role,
                            "seats": seats,
                            "name": cells[0],
                            "unexpired": False,
                            "tacit": False,
                            "doc": "06087-s",
                        }
                    )
                cells = []
    return rows


def santa_cruz_watch(contested: set[str]) -> list[Row]:
    rows: list[Row] = []
    current: tuple[str, str, str, int] | None = None
    for line in (item.strip() for item in inbox_doc("06087-w").read_text().splitlines()):
        if head := re.match(r"^### (.+?)(?: \(Short Term\))? - (?:(\d+) Seats?|(Division|Zone|Ward) (\w+))$", line):
            body = head[1].strip()
            short = "(Short Term)" in line
            if usable(body) and full_name(body.upper()) not in contested:
                label = f"{head[3]} {head[4]}" if head[3] else ("Short Term" if short else "")
                current = (
                    full_name(body.upper()),
                    "Trustee" if "College" in body else "Director",
                    label,
                    int(head[2] or 1),
                )
            else:
                current = None
        elif current is not None and line.startswith("| ") and not line.startswith(("| Candidate", "| ---", "| |")):
            district, role, seat, seats = current
            rows.append(
                {
                    "county": "Santa Cruz",
                    "district": district,
                    "seat": seat,
                    "role": role,
                    "seats": seats,
                    "name": line.split("|")[1].strip(),
                    "unexpired": seat == "Short Term",
                    "tacit": True,
                    "doc": "06087-w",
                }
            )
    return rows


SUTTER_HEAD = re.compile(r"^([A-Z][A-Z0-9 ]+?) \((\d+) SEATS?\)( SHORT TERM)?$")


def sutter() -> list[Row]:
    rows: list[Row] = []
    current: tuple[str, str, int, bool] | None = None
    for line in lines("06101"):
        if found := SUTTER_HEAD.match(line.strip()):
            heading, seats = found[1], int(found[2])
            area = re.search(r" TRUSTEE AREA (\d+)$", heading)
            body = heading[: area.start()] if area else heading
            current = (
                (full_name(cased(body)), f"Trustee Area {area[1]}" if area else "", seats, found[3] is not None)
                if usable(body)
                else None
            )
        elif current is not None and (cells := re.split(r"\s{2,}", line.strip())) and len(cells) == 5:
            district, seat, seats, short = current
            rows.append(
                {
                    "county": "Sutter",
                    "district": district,
                    "seat": seat or ("Short Term" if short else ""),
                    "role": "Trustee" if "College" in district else "Director",
                    "seats": 1 if seat else seats,
                    "name": f"{cells[1]} {cells[2]}",
                    "unexpired": short,
                    "tacit": False,
                }
            )
    counts: dict[tuple[object, object], int] = {}
    for row in rows:
        counts[row["district"], row["seat"]] = counts.get((row["district"], row["seat"]), 0) + 1
    return [{**row, "tacit": counts[row["district"], row["seat"]] <= int(row["seats"])} for row in rows]


def napa() -> list[Row]:
    return qualified_list("06055", "Napa", ((r"Napa Valley College", "Napa Valley Community College"),), True)


SOLANO_BODIES = {
    "SOLANO COMMUNITY COLLEGE": ("Solano Community College District", "Trustee"),
    "CORDELIA FIRE PROTECTION DISTRICT": ("Cordelia Fire Protection District", "Director"),
    "RURAL NORTH VACAVILLE WATER DISTRICT": ("Rural North Vacaville Water District", "Director"),
    "SOLANO IRRIGATION DISTRICT": ("Solano Irrigation District", "Director"),
}
SOLANO_SEAT = re.compile(r"^ (Trustee Area|Division Director) (\d+) - Vote for (\d+)\s")


def solano() -> list[Row]:
    rows: list[Row] = []
    body: tuple[str, str] | None = None
    seat, seats, ballot = "", 0, True
    for line in lines("06095"):
        if body is not None and not SOLANO_SEAT.match(line) and (vote := re.search(r"Vote for (\d+)", line)):
            seats = int(vote[1])
        if (head := re.match(r"^ ([A-Z][A-Z ]+?)(?:\s{2,}|$)", line)) and head[1] != "I":
            body = SOLANO_BODIES.get(head[1])
            seat, ballot = "", "OFF THE BALLOT" not in line
            seats = 0
        elif found := SOLANO_SEAT.match(line):
            seat = f"{'Trustee Area' if found[1] == 'Trustee Area' else 'Division'} {found[2]}"
            seats, ballot = int(found[3]), "OFF THE BALLOT" not in line
        elif body is not None and re.match(r"^ [A-Z][a-z]", line) and "DID NOT FILE" not in line:
            cells = re.split(r"\s{2,}", line.strip())
            vote = re.search(r"Vote for (\d+)", line)
            if vote:
                seats = int(vote[1])
            if len(cells) >= 2 and re.fullmatch(r"I|", cells[1]) is not None or len(cells) >= 3:
                rows.append(
                    {
                        "county": "Solano",
                        "district": body[0],
                        "seat": seat,
                        "role": body[1],
                        "seats": seats,
                        "name": cells[0],
                        "unexpired": False,
                        "tacit": not ballot,
                    }
                )
    most: dict[tuple[object, object], int] = {}
    for row in rows:
        key = (row["district"], row["seat"])
        most[key] = max(most.get(key, 0), int(row["seats"]))
    return [{**row, "seats": most[row["district"], row["seat"]]} for row in rows]


def merced() -> list[Row]:
    return [
        row
        for row in qualified_list(
            "06047",
            "Merced",
            (
                (r"\bCSD\b", "Community Services District"),
                (r"\bCENTRAL CA\b", "Central California"),
                (r"\bSO\. ", "South "),
                (r"\bDIV\b", "Division"),
                (r"&", "and"),
                (r"\bWEST SIDE\b", "WESTSIDE"),
                (r"\b(MERCED|YOSEMITE) COLLEGE\b", r"\1 COMMUNITY COLLEGE"),
            ),
            True,
        )
    ]


LIEU = {
    "06071-lieu": (
        "https://uploads.rov.sbcounty.gov/rov/News/2026/1103/PublicNotice_AppointmentsInLieuSpecials.pdf",
        "San Bernardino County Registrar of Voters",
        "November 3, 2026 General Election: Special Districts Appointments in Lieu of Election",
    ),
    "06097-lieu": (
        "https://sonoma-county.legistar1.com/sonoma-county/attachments/c4f6c4a5-2440-48a9-820e-de3a47461463.pdf",
        "Sonoma County Board of Supervisors",
        "Resolution Appointing Nominees to the Office of District Director of Their Respective Special Districts, September 15, 2026",
    ),
}
LIEU_NAME = re.compile(r"^(?P<name>\S.*?)\s{2,}(?:(?P<text>\S.*?)\s{2,})?(?P<term>\d) years?\s*$")
LIEU_OFFICE = re.compile(
    r"^(?P<body>.+?) Member,? (?:Board of (?:Directors|Trustees)|Governing Board),?\s*(?P<rest>.*)$", re.IGNORECASE
)


def lieu_name(name: str) -> str:
    last, _, first = name.partition(", ")
    return cased(f"{first} {last}") if name.isupper() and first else name


def san_bernardino_lieu() -> list[Row]:
    rows: list[Row] = []
    for key, path in (
        ("06071-lieu", raw.cached(SOURCE, LIEU["06071-lieu"][0]).path),
        ("06071-schools", inbox_doc("06071-schools")),
    ):
        text = (
            subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
            .stdout.decode()
            .splitlines()
        )
        entries = []
        for index, line in enumerate(text):
            found = LIEU_NAME.match(line.removeprefix(" "))
            if found is None or line.startswith(("SPECIAL", "OCTOBER", "PAGE", "SCHOOL")):
                continue
            office = found["text"] or " ".join(
                part.strip() for part in (text[index - 1], text[index + 1]) if part.strip()
            )
            entries.append((lieu_name(found["name"].strip()), " ".join(office.split()), found["term"]))
        keyed: dict[tuple[str, str, bool], list[str]] = {}
        for name, office, term in entries:
            split = LIEU_OFFICE.match(office)
            if split is None or not usable(split["body"]):
                continue
            seat = re.search(r"(Division|Trustee Area|Area) (\w+)", split["rest"])
            short = "Short Term" in split["rest"] or term == "2"
            keyed.setdefault((split["body"], f"{seat[1]} {seat[2]}" if seat else "", short), []).append(name)
        rows += [
            {
                "county": "San Bernardino",
                "district": full_name(body),
                "seat": label or ("Short Term" if short else ""),
                "role": "Trustee" if "College" in body else "Director",
                "seats": 1 if label else len(names),
                "name": name,
                "unexpired": short,
                "tacit": True,
                "doc": key,
            }
            for (body, label, short), names in keyed.items()
            for name in names
        ]
    return rows


SONOMA_LIEU_SEATS = {
    "Cazadero Community Services District": 2,
    "Graton Community Services District": 2,
    "Sonoma Valley Health Care District": 2,
    "Rains Creek/Hiatt Rd County Water District": 3,
    "Coast Life Support District": 3,
}


def sonoma_lieu() -> list[Row]:
    path = raw.fetch(SOURCE, LIEU["06097-lieu"][0], suffix=".pdf").path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    rows: list[Row] = []
    head = None
    for line in text.splitlines():
        if found := re.match(r"^\s+(.+?), (\d)-Year Term$", line):
            head = (found[1], found[2] == "2")
        elif (found := re.match(r"^\s+•\s+(\S.*)$", line)) and head is not None:
            body, _, zone = head[0].partition(", ")
            seats = None if head[1] else SONOMA_LIEU_SEATS.get(body)
            rows.append(
                {
                    "county": "Sonoma",
                    "district": full_name(body),
                    "seat": zone or ("Short Term" if head[1] else ""),
                    "role": "Director",
                    "seats": 1 if zone else seats or 0,
                    "name": cased(found[1].strip()).replace("Demartini", "DeMartini"),
                    "unexpired": head[1],
                    "tacit": True,
                    "doc": "06097-lieu",
                }
            )
    counts = {}
    for row in rows:
        counts[row["district"], row["seat"]] = counts.get((row["district"], row["seat"]), 0) + 1
    return [{**row, "seats": row["seats"] or counts[row["district"], row["seat"]]} for row in rows]


def kings() -> list[Row]:
    return proof_list("06031", "Kings", 32)


def santa_barbara() -> list[Row]:
    return [
        *proof_list("06083-s", "Santa Barbara", 32, doc="06083-s"),
        *proof_list("06083-o", "Santa Barbara", 32, doc="06083-o"),
    ]


TRINITY_JUNK = {
    "Burnt",
    "Burnt Ranch",
    "Ranch",
    "Burnt Ranch Elementary",
    "Burnt Ranch Elementary School District",
    "Ranch Elementary School District",
}
TRINITY_OPEN = re.compile(r"^Open Positions:\s+(\d+)\s+Candidates Filed:\s+(\d+)\s+(NOT on|ON) Ballot$")


def trinity() -> list[Row]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(inbox_doc("06105-s")), "-"], capture_output=True, check=True
    ).stdout.decode()
    body: list[str] = []
    for page in text.split("\f"):
        started = False
        for raw_line in page.splitlines():
            line = re.sub(r"\s{2,}Elementary School District.*$", "", raw_line.rstrip())
            if raw_line.startswith("Nominee"):
                started = True
            elif (
                started
                and line.strip()
                and " ".join(line.split()) not in TRINITY_JUNK
                and not re.fullmatch(r"\s*\d+ of \d+", line)
            ):
                body.append(line)
    opens = [index for index, line in enumerate(body) if TRINITY_OPEN.match(" ".join(line.split()))]
    rows: list[Row] = []
    for number, index in enumerate(opens):
        stop = opens[number + 1] - 1 if number + 1 < len(opens) else len(body)
        found = TRINITY_OPEN.match(" ".join(body[index].split()))
        title = " ".join(body[index - 1].split())
        term = re.search(r" - (Full|Short) Term$", title)
        name = re.sub(r"\s*- (?:Full|Short) Term$", "", title).replace("&", "and").replace("Communites", "Communities")
        if int(found[2]) == 0 or not usable(name):
            continue
        names = [
            cell
            for line in body[index + 1 : stop]
            if re.fullmatch(r"[A-Z][A-Za-z.'’() -]+ [A-Za-z.'’()-]+", cell := re.split(r"\s{2,}", line.strip())[0])
        ]
        if len(names) != int(found[2]):
            raise SystemExit(f"trinity: {title}: {len(names)} names for {found[2]} filed")
        short = term is not None and term[1] == "Short"
        rows.extend(
            {
                "county": "Trinity",
                "district": full_name(name),
                "seat": "Short Term" if short else "",
                "role": "Director",
                "seats": int(found[1]),
                "name": candidate,
                "unexpired": short,
                "tacit": found[3] == "NOT on",
                "doc": "06105-s",
            }
            for candidate in names
        )
    return rows


SISKIYOU_HEAD = re.compile(
    r"^(?P<title>.+?) – (?:(?P<kind>Area|Division|District) (?P<number>\w+) – )?(?P<term>Full|Short) Term – Vote for (?P<seats>\d+)$"
)


def siskiyou() -> list[Row]:
    rows: list[Row] = []
    current: re.Match[str] | None = None
    names: list[str] = []

    def flush() -> None:
        if current is not None and names:
            short = current["term"] == "Short"
            label = f"{current['kind']} {current['number']}" if current["kind"] else ""
            seats = int(current["seats"])
            rows.extend(
                {
                    "county": "Siskiyou",
                    "district": full_name(current["title"]),
                    "seat": label or ("Short Term" if short else ""),
                    "role": "Trustee" if "College" in current["title"] else "Director",
                    "seats": 1 if label else seats,
                    "name": name,
                    "unexpired": short,
                    "tacit": len(names) <= (1 if label else seats),
                    "doc": LISTED["06093"],
                }
                for name in names
            )

    for line in lines(LISTED["06093"]):
        line = line.strip()
        if found := SISKIYOU_HEAD.match(line):
            flush()
            current, names = (found if usable(found["title"]) else None), []
        elif current is not None and line and not line.startswith(("Ballot Designation:", "Email:", "Phone:")):
            if not line.startswith(("Final Siskiyou", "Local Offices", "November 3", "As of")):
                names.append(line)
    flush()
    return rows


def inyo() -> list[Row]:
    groups: dict[tuple[str, str, str], list[str]] = {}
    filled: dict[tuple[str, str, str], int] = {}
    section = ""
    for line in lines(LISTED["06027"]):
        if line.strip() in (
            "Hospital Districts",
            "Community Services Districts",
            "Fire Districts",
            "Sanitary Districts",
        ):
            section = line.strip()
        elif section and (
            found := re.match(
                r"^\s*(?P<title>\S.*? Board Member)(?:, (?P<zone>Zone \d+))?\s{2,}(?P<term>[24])(?:\s{2,}(?P<name>\S.*?))?(?:\s{2,}\S.*)?$",
                line,
            )
        ):
            key = (found["title"].removesuffix(" Board Member"), found["zone"] or "", found["term"])
            groups.setdefault(key, [])
            filled[key] = filled.get(key, 0) + 1
            if found["name"]:
                groups[key].append(found["name"].rstrip("*").strip())
    rows: list[Row] = []
    for (district, zone, term), names in groups.items():
        if names:
            rows.extend(
                {
                    "county": "Inyo",
                    "district": full_name(district),
                    "seat": zone or ("Short Term" if term == "2" else ""),
                    "role": "Director",
                    "seats": filled[district, zone, term],
                    "name": name,
                    "unexpired": term == "2",
                    "tacit": True,
                    "doc": LISTED["06027"],
                }
                for name in names
            )
    return rows


DEL_NORTE_PERSON = re.compile(r"^([A-Z][A-Z'’. -]+), (.+?)\s{3,}(?:YES|NO)\s+(YES|NO)\s+(?:YES|NO)\s*$")


def del_norte() -> list[Row]:
    rows: list[Row] = []
    text = lines(LISTED["06015"])
    current: tuple[str, int, bool, bool] | None = None
    for index, line in enumerate(text):
        if found := re.match(r"^\s*Election = (YES|NO)\s+Vote for (\d+)", line):
            title = next(previous.strip() for previous in reversed(text[:index]) if previous.strip())
            short = "Short Term" in text[index + 1]
            current = (title, int(found[2]), short, found[1] == "NO") if usable(title) else None
        elif current is not None and (person := DEL_NORTE_PERSON.match(line)) and person[3] == "YES":
            title, seats, short, canceled = current
            rows.append(
                {
                    "county": "Del Norte",
                    "district": full_name(title),
                    "seat": "Short Term" if short else "",
                    "role": "Commissioner" if "HARBOR" in title else "Director",
                    "seats": seats,
                    "name": cased(f"{person[2]} {person[1]}"),
                    "unexpired": short,
                    "tacit": canceled,
                    "doc": LISTED["06015"],
                }
            )
    return rows


NUMBER_WORDS = ("One", "Two", "Three", "Four", "Five", "Six", "Seven")
LISTED_EXPAND = (
    (r"\bCSD\b", "Community Services District"),
    (r"\bFPD\b", "Fire Protection District"),
    (r"\bHCD\b", "Health Care District"),
    (r"\bPUD\b", "Public Utility District"),
    (r"\bVMD\b", "Veterans Memorial District"),
    (r"\bCCD\b", "Community College District"),
    (r"\bCCWD\b", "Calaveras County Water District"),
    (r"\bREC & PARK\b", "Recreation and Park District"),
    (
        r"(?i),? \bDivision (One|Two|Three|Four|Five|Six|Seven)\b",
        lambda found: f" Division {NUMBER_WORDS.index(found[1].title()) + 1}",
    ),
    (r"\bST$", "Short Term"),
)


def tagged(rows: list[Row], key: str) -> list[Row]:
    return [{**row, "doc": key} for row in rows]


def calaveras() -> list[Row]:
    return tagged(qualified_list(LISTED["06009"], "Calaveras", LISTED_EXPAND, True), LISTED["06009"])


def amador() -> list[Row]:
    return tagged(qualified_list(LISTED["06005"], "Amador", LISTED_EXPAND, True), LISTED["06005"])


def tehama() -> list[Row]:
    return tagged(qualified_list(LISTED["06103"], "Tehama", LISTED_EXPAND), LISTED["06103"])


def lassen() -> list[Row]:
    return [
        {**row, "seats": 2}
        if row["district"].startswith("Lassen Community College") and row["seat"] == "Trustee Area 1"
        else row
        for row in tagged(qualified_list(LISTED["06035"], "Lassen", LISTED_EXPAND), LISTED["06035"])
    ]


LAKE_EXPAND = (
    (r"\bMendo-Lake Comm\b", "Mendocino-Lake Community"),
    (r"\bDist\b", "District"),
    (r"\bCo Water\b", "County Water"),
    (r"\s*\(PT\)", " Short Term"),
    (r"\s*-\s*4 year term", ""),
    (r"\s*-\s*2 year term", " Short Term"),
)
LAKE_SEATS = {
    ("Northshore Fire Protection District", ""): "At Large",
    ("Northshore Fire Protection District", "Contest 7320"): "Lucerne Zone",
    ("Northshore Fire Protection District", "Contest 7330"): "Nice Zone",
}


def lake() -> list[Row]:
    special = tagged(qualified_list("06033-s", "Lake", LAKE_EXPAND), "06033-s")
    college = [
        row
        for row in tagged(qualified_list("06033-q", "Lake", LAKE_EXPAND, True), "06033-q")
        if "College" in str(row["district"])
    ]
    return [
        {**row, "seat": LAKE_SEATS.get((str(row["district"]), str(row["seat"])), row["seat"])}
        for row in [*special, *college]
    ]


MADERA_HEAD = re.compile(r"^NON-PARTISAN DISTRICT (.+?) Director(?:, Division (\d+))?$")
MADERA_NAME = re.compile(r"^(?:\s+Candidate\(s\):| {20,40})\s*([A-Z][A-Z.' -]+?)\s*$")


def madera() -> list[Row]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(inbox_doc("06039-s")), "-"], capture_output=True, check=True
    ).stdout.decode()
    contests: dict[tuple[str, str], list[str]] = {}
    key: tuple[str, str] | None = None
    for line in text.splitlines():
        if found := MADERA_HEAD.match(line.strip()):
            key = (full_name(cased(found[1])), f"Division {found[2]}" if found[2] else "")
            contests.setdefault(key, [])
        elif line.startswith("NON-PARTISAN"):
            key = None
        elif key is not None and (found := MADERA_NAME.match(line)) and "Requirements" not in line:
            name = cased(found[1])
            if name not in contests[key]:
                contests[key].append(name)
    return [
        {
            "county": "Madera",
            "district": district,
            "seat": seat,
            "role": "Director",
            "seats": len(names) if not seat else 1,
            "name": name,
            "unexpired": False,
            "tacit": True,
            "doc": "06039-s",
        }
        for (district, seat), names in contests.items()
        for name in names
    ]


MENDOCINO_NAMES = {
    "MENDOCINO COAST RECREATION & PARK DISTRICT": "Mendocino Coast Recreation and Park District",
    "REDWOOD VALLEY/CALPELLA FIRE PROTECTION DISTRICT": "Redwood Valley-Calpella Fire Protection District",
}


def mendocino() -> list[Row]:
    path = DATA / "inbox" / "y26" / "ca" / "mendocino_in_lieu.tsv"
    groups: dict[tuple[str, bool], list[str]] = {}
    for record in pl.read_csv(path, separator="\t", infer_schema_length=0).iter_rows(named=True):
        if not record["candidate"]:
            continue
        groups.setdefault((record["district"], record["term_end"] == "2028"), []).append(record["candidate"])
    rows: list[Row] = [
        {
            "county": "Mendocino",
            "district": MENDOCINO_NAMES.get(district) or full_name(district),
            "seat": "Short Term" if short else "",
            "role": "Director",
            "seats": len(names),
            "name": name,
            "unexpired": short,
            "tacit": True,
            "doc": "06045-s",
        }
        for (district, short), names in groups.items()
        for name in names
    ]
    rows.extend(
        {
            "county": "Mendocino",
            "district": "Potter Valley Irrigation District",
            "seat": "Division 4",
            "role": "Director",
            "seats": 1,
            "name": name,
            "unexpired": True,
            "tacit": False,
            "doc": "06045-e",
        }
        for name in ("Steven Giuntini", "Jarred Hildebrand")
    )
    return rows


def stanislaus() -> list[Row]:
    rows = []
    section = ""
    contest: tuple[str, str, int] | None = None
    text = lines("06099")
    for index, line in enumerate(text):
        if line.strip() and index + 1 < len(text) and text[index + 1].lstrip().startswith("BALLOT DESIGNATION"):
            section = line.strip()
            contest = None
            continue
        if section != "SPECIAL DISTRICTS" and "COLLEGE" not in section:
            continue
        if head := re.match(r"^(.+?)\s+\(Vote for (\d+)\)\s*$", line):
            title = re.sub(r"\s+", " ", head[1]).strip()
            seat = re.search(r"\s-\s(DIVISION|ZONE|AREA|TRUSTEE AREA|DISTRICT|WARD)\s+(\w+)", title)
            short = "SHORT TERM" in title
            body = re.split(r"\s-\s", title)[0]
            label = f"{seat[1].title()} {seat[2]}" if seat else ("Short Term" if short else "")
            contest = (body, label, int(head[2])) if usable(body) else None
            contest_short = short
        elif contest is not None and line.strip() and not line.startswith(" "):
            name = re.split(r"\s{2,}", line.strip())[0].lstrip("*").strip()
            if name and not re.match(r"(?i)(ballot designation|contact|\*incumbent|page )", name):
                rows.append(
                    {
                        "county": "Stanislaus",
                        "district": full_name(contest[0]),
                        "seat": contest[1],
                        "role": "Trustee" if "COLLEGE" in contest[0] else "Director",
                        "seats": contest[2],
                        "name": cased(name) if name.isupper() else name,
                        "unexpired": contest_short,
                    }
                )
    return rows


SAN_BERNARDINO = re.compile(
    r"^\s+(?:Member|Director|Trustee), Board of (?P<role>Directors|Trustees)(?: Division (?P<seat>\d+))?(?P<short> - Short Term)?\s*$"
)


def san_bernardino() -> list[dict[str, str | int | bool]]:
    rows = []
    text = lines("06071")
    contest = None
    seats = 1
    live = False
    for index, line in enumerate(text):
        if found := re.match(r"\s+Number to be elected\s+(\d+)", line):
            body = text[index - 3].strip()
            role = SAN_BERNARDINO.match(text[index - 2])
            contest = (
                role
                if role is not None
                and KEPT.search(body.upper())
                and not DROPPED.search(body.upper())
                and not re.search(r"(?i)school|san gorgonio|beaumont|indian wells|east kern", body)
                else None
            )
            contest_body = body
            seats, live = int(found[1]), False
        elif contest is None:
            continue
        elif found := re.match(r"\s+Will contest appear on the ballot\s+(Yes|No)", line):
            live = found[1] == "Yes"
        elif found := re.match(r"^([A-Z][A-Z .,'\"ÑÁÉÍÓÚ-]+?)(?: - (?:Appointed )?Incumbent)?\s*$", line):
            if index + 1 < len(text) and re.match(r"(Approved Ballot Designation|Mailing Address)", text[index + 1]):
                rows.append(
                    {
                        "county": "San Bernardino",
                        "district": full_name(contest_body),
                        "seat": f"Division {contest['seat']}"
                        if contest["seat"]
                        else ("Short Term" if contest["short"] else ""),
                        "role": "Director" if contest["role"] == "Directors" else "Trustee",
                        "seats": seats,
                        "name": cased(found[1]),
                        "unexpired": contest["short"] is not None,
                        "tacit": not live,
                    }
                )
    return rows


SANTA_CLARA = re.compile(r"^(?P<body>.+?),? (?P<kind>District|Ward|D)\s?(?P<number>\d+)$")


def santa_clara() -> list[dict[str, str | int | bool]]:
    contests: dict[tuple[str, str], list[str]] = {}
    incumbents: dict[tuple[str, str], int] = {}
    ballot: dict[tuple[str, str], bool] = {}
    current = None
    for index, line in enumerate(lines("06085")):
        if found := re.match(r"^6\d{3}\s+(\S.*?)\s{2,}On Ballot: (Yes|No)\s+Vote For: (\d+)", line):
            label = found[1]
            if seat := SANTA_CLARA.match(label):
                current = (seat["body"], f"{'Ward' if seat['kind'] == 'Ward' else 'District'} {seat['number']}")
            else:
                current = (label, "")
            contests.setdefault(current, [])
            incumbents[current] = int(found[3])
            ballot[current] = found[2] == "Yes"
        elif current is None:
            continue
        elif found := re.match(r"\s+Qualified\s+\d+\s+(\S.*?)\s{2,}Candidate Stmt", line):
            contests[current].append(found[1])
    rows = []
    for key, names in contests.items():
        seats = incumbents[key]
        if seats == 0 or not usable(key[0]):
            continue
        for name in names:
            rows.append(
                {
                    "county": "Santa Clara",
                    "district": full_name(key[0]),
                    "seat": key[1],
                    "role": "Director",
                    "seats": seats,
                    "name": name,
                    "unexpired": False,
                    "tacit": not ballot[key],
                }
            )
    return rows


ALAMEDA_NAMES = {
    "EBMUD": "East Bay Municipal Utility District",
    "BART": "San Francisco Bay Area Rapid Transit District",
    "AC Transit District": "Alameda-Contra Costa Transit District",
    "Dublin San Ramon": "Dublin San Ramon Services District",
}
ALAMEDA = re.compile(
    r"^(?:Member, Board Of Directors?, )?(?P<body>.+?)(?: Directors?| Trustee)?"
    r"(?:,? (?P<kind>Ward|District|Area|Dist) #?(?P<number>\d+))?(?P<short>, Short Term)?$"
)
ALAMEDA_KEPT = re.compile(
    r"(?i)water|EBMUD|sanitary|fire|park|health|irrigation|BART|AC Transit|Dublin San Ramon|community college"
)


def alameda(off_ballot: bool = False) -> list[dict[str, str | int | bool]]:
    rows = []
    page = (
        inbox_doc("06001-n").read_text()
        if off_ballot
        else raw.cached(local_2026.SOURCE, feed("06001")[0]).path.read_text()
    )
    status = "Not On Ballot" if off_ballot else "On Ballot"
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", page)))
    text = [line.strip() for line in text.splitlines() if line.strip()]
    contest = None
    seats = 1
    seen: set[tuple[str, str, str]] = set()
    for index, line in enumerate(text):
        if line.endswith(" -") and text[index + 1 : index + 2] == [status]:
            found = ALAMEDA.match(line[:-2])
            contest = (
                found
                if found is not None
                and ALAMEDA_KEPT.search(found["body"])
                and not re.search(
                    r"(?i)school|(?<!community )college|education|city(?! of alameda healthcare)|unified|congress|assembly|senate|equalization",
                    line,
                )
                else None
            )
            seats = int(re.match(r"Vote For: (\d+)", text[index + 2])[1]) if contest is not None else 1
            continue
        if contest is None:
            continue
        name = None
        if off_ballot:
            if line == "CANDIDATE" and text[index + 2] == "BALLOT DESIGNATION":
                agency = text[text.index("Filing Agency:", index) + 1]
                name = text[index + 1] if agency == "Alameda County" else None
        elif (
            line == "CANDIDATE"
            and text[index + 1] != "BALLOT DESIGNATION"
            or line == "DETAILS"
            and text[index - 4] == "CANDIDATE"
        ):
            name = text[index + 1]
        if name is not None:
            body = ALAMEDA_NAMES.get(contest["body"], contest["body"])
            seat = (
                f"{'Ward' if contest['kind'] == 'Ward' else 'Area' if contest['kind'] == 'Area' else 'District' if contest['kind'] == 'District' else 'Division'} {contest['number']}"
                if contest["kind"]
                else ""
            )
            key = (body, seat, name)
            if key not in seen:
                seen.add(key)
                rows.append(
                    {
                        "county": "Alameda",
                        "district": full_name(re.sub(r"\bDist\b", "District", re.sub(r"\s+-\s+", "-", body)))
                        if body == contest["body"]
                        else body,
                        "seat": seat or ("Short Term" if contest["short"] else ""),
                        "role": "Trustee" if "College" in body else "Director",
                        "seats": seats,
                        "name": " ".join(cased(name).split()),
                        "unexpired": contest["short"] is not None,
                        **({"tacit": True, "doc": "06001-n"} if off_ballot else {}),
                    }
                )
    return rows


def contra_costa() -> list[dict[str, str | int | bool]]:
    rows = []
    page = raw.cached(local_2026.SOURCE, feed("06013")[0]).path.read_text()
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", page)))
    text = [line.strip() for line in text.splitlines() if line.strip()]
    seen: set[tuple[str, str, str]] = set()
    for index, line in enumerate(text):
        if not re.match(r"November 3, 2026, General Election\s+11/03/2026", line):
            continue
        title = text[index + 1]
        if (
            not KEPT.search(title.upper())
            or re.search(r"(?i)school|college|east bay|dublin san ramon|city", title)
            or not (found := re.match(r"Vote for (\d+)", text[index + 2]))
        ):
            continue
        parts = re.split(r",\s+|\s+-\s+", title)
        body = parts[0].replace("Mt.", "Mountain")
        seat = next(
            (
                f"{match[1]} {match[2]}"
                for part in parts
                if (match := re.fullmatch(r"(Division|Area|Ward|Zone) (\d+)", part))
            ),
            "",
        )
        cursor = index + 3
        while cursor < len(text) and not text[cursor].startswith(("Candidates", "Federal")):
            if text[cursor + 1 : cursor + 2] and text[cursor + 1].startswith(("Party Preference", "Non partisan")):
                key = (body, seat, text[cursor])
                if key not in seen:
                    seen.add(key)
                    rows.append(
                        {
                            "county": "Contra Costa",
                            "district": full_name(body),
                            "seat": seat,
                            "role": "Director",
                            "seats": int(found[1]),
                            "name": text[cursor],
                            "unexpired": False,
                        }
                    )
                cursor += 2
            else:
                cursor += 1
    return rows


Row = dict[str, str | int | bool]
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
BLOCK_SEAT = re.compile(
    r",? (?:(?:District|Division|Zone|Area|Ward|Seat) (\d+))?(?:, | - )?(Short Term|Unexpired|\d Year)?$"
)


def text_of(fips: str) -> list[str]:
    body = raw.cached(local_2026.SOURCE, feed(fips)[0]).path.read_text(errors="ignore")
    body = re.sub(r"<(script|style).*?</\1>", "", body, flags=re.DOTALL)
    return [line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "\n", body)).splitlines() if line.strip()]


def rows_of(county: str, district: str, tail: str, seats: int, names: list[str], tacit: bool = False) -> list[Row]:
    found = re.search(r"(?:District|Division|Zone|Area|Ward|Seat|Trustee Area) (\d+)", tail)
    short = re.search(r"Short Term|Unexpired|2 Year", tail) is not None
    label = found[0] if found else ("Short Term" if short else "")
    seen = list(dict.fromkeys(names))
    return [
        {
            "county": county,
            "district": full_name(district),
            "seat": label,
            "role": "Director",
            "seats": 1 if found else seats,
            "name": name,
            "unexpired": short,
            "tacit": tacit,
        }
        for name in seen
    ]


def sonoma() -> list[Row]:
    text = text_of("06097")
    rows = []
    for index, line in enumerate(text):
        found = re.match(r"^Director, (.+)$", line)
        if not found or index < 1 or not text[index + 1].startswith("(Vote for"):
            continue
        votes = NUMBERS[text[index + 1].split()[-1].rstrip(")").lower()]
        names = []
        cursor = index + 5
        while text[cursor] != "▲ TOP ▲" and not text[cursor].startswith("Director, "):
            if text[cursor + 2] in ("County Guide", "None submitted"):
                names.append(cased(text[cursor]))
                cursor += 3
            else:
                cursor += 1
        rows += rows_of("Sonoma", found[1], "", votes, names)
    return rows


NEVADA_LINE = re.compile(r"^(?P<head>.+?), vote for (?P<seats>\w+)(?:, full term)? \((?P<names>.+)\)$")


def nevada() -> list[Row]:
    rows: list[Row] = []
    for line in page_lines(inbox_doc("06057-s")):
        found = NEVADA_LINE.match(line)
        if found is None:
            continue
        seat = re.search(r"(?:,| ) ?(Trustee Area|Division) (\d+)$", found["head"])
        body = found["head"][: seat.start()] if seat else found["head"]
        if not usable(body):
            continue
        votes = found["seats"].lower()
        rows += [
            {
                "county": "Nevada",
                "district": full_name(body),
                "seat": f"{seat[1]} {seat[2]}" if seat else "",
                "role": "Trustee" if "College" in body else "Director",
                "seats": int(votes) if votes.isdigit() else NUMBERS[votes],
                "name": name,
                "unexpired": False,
                "tacit": False,
                "doc": "06057-s",
            }
            for name in (item.strip(" ,") for item in found["names"].split(", "))
            if name
        ]
    return rows


def marin() -> list[Row]:
    text = text_of("06041")
    start = next(i for i, line in enumerate(text) if line == "Return to Top")
    rows = []
    index = start
    while index < len(text) - 1:
        head = re.match(
            r"^(.+? (?:District(?: No\. \d+)?|Authority|Agency)) (?:Director|Governing Board Member)(?:,? (.*))?$",
            text[index],
        )
        if not head or text[index + 1] != "Seats Open":
            index += 1
            continue
        seats = int(text[index + 6])
        cursor = index + 8
        while text[cursor] != "Filed" or text[cursor - 1] != "Statement":
            cursor += 1
        cursor += 1
        names = []
        expect = True
        while text[cursor] != "Return to Top" and cursor < len(text) - 1:
            line = text[cursor]
            if re.fullmatch(r"\d+/\d+/\d{4}", line):
                expect = True
            elif expect:
                last, _, first = line.rstrip("*").partition(", ")
                first, _, suffix = first.partition(", ")
                names.append(" ".join(part for part in (first, last, suffix) if part))
                expect = False
            cursor += 1
        if "School" not in head[1]:
            rows += [
                {**row, "role": "Trustee" if "College" in head[1] else row["role"]}
                for row in rows_of("Marin", head[1], head[2] or "", seats, names)
            ]
        index = cursor
    return rows


def monterey() -> list[Row]:
    body = raw.cached(local_2026.SOURCE, feed("06053")[0]).path.read_text(errors="ignore")
    rows = []
    for item in re.findall(r'<div class="accordion-items.*?(?=<div class="accordion-items|\Z)', body, flags=re.DOTALL):
        if 'data-bs-parent="#DISTRICT"' not in item:
            continue
        head = html.unescape(re.search(r"<button[^>]*>(.*?)</button>", item, flags=re.DOTALL)[1]).strip()
        status = re.search(r"background-color: \w+;\">([^<]+)<", item)
        votes = re.search(r"Vote for (?:no more than )?(\w+)", item)
        if status is None or status[1].strip() not in ("On Ballot", "Appointed In-Lieu of Election") or votes is None:
            continue
        seats = int(votes[1]) if votes[1].isdigit() else NUMBERS[votes[1].lower()]
        split = re.match(r"^(.+?) (?:Director|Harbor Commissioner|Trustee)(.*)$", head, flags=re.IGNORECASE)
        names = [html.unescape(name).strip() for name in re.findall(r"<h5[^>]*>(.*?)</h5>", item, flags=re.DOTALL)]
        if split:
            rows += rows_of("Monterey", split[1], split[2], seats, names, status[1].strip() != "On Ballot")
    return rows


SJ_HEAD = re.compile(r"^(\d{4})\s+(.+?)\s{3,}On Ballot: (Yes|No)\s+Vote For: (\d+)")
SJ_CAND = re.compile(r"^\s+Qualified\s+\d+\s+(\S.*?)\s{2,}Candidate Stmt")


def san_joaquin() -> list[Row]:
    rows = []
    contest = None
    names: list[str] = []

    def flush() -> None:
        if contest is not None:
            split = re.match(r"^(.+? (?:District|Authority|Agency)),?(?: (.*))?$", contest[1])
            if split and usable(split[1]):
                rows.extend(
                    rows_of("San Joaquin", split[1], split[2] or "", int(contest[3]), names, contest[2] == "No")
                )

    for line in lines("06077"):
        if found := SJ_HEAD.match(line):
            flush()
            contest = found.groups() if int(found[1]) >= 4000 else None
            names = []
        elif contest and (found := SJ_CAND.match(line)):
            names.append(cased(found[1]))
    flush()
    return rows


def kern() -> list[Row]:
    return proof_list("06029", "Kern", 29)


def tulare() -> list[Row]:
    return proof_list("06107", "Tulare", 33)


QUALIFIED_HEAD = re.compile(
    r"^\s*(?:Contest:\s+)?(\d{4})\s+(?:-\s+)?(.+?)\s{2,}(?:On Ballot: (Yes|No)\s+Vote [Ff]or: (\d+)|Vote for\s+(\d+)\s+On Ballot: (Yes|No)|On Ballot: (Yes|No)|Vote for\s+(\d+))\s*$"
)
QUALIFIED_ROLE = re.compile(
    r"\b(?:Member, Board of (?:Directors|Trustees|Commissioners)|Member, Governing Board|Governing Board Members?|Directors?|Trustees?(?! Area)|Board Members?|Commissioners?)\b",
    re.IGNORECASE,
)
QUALIFIED_SEAT = re.compile(
    r"(Division|Zone|Area|Ward|Seat|Subdivision|Trustee Area|TRA|TA|District)\s*(?:No\.?\s*)?(\d+|(?-i:[A-G])\b)",
    re.IGNORECASE,
)
QUALIFIED_CAND = re.compile(
    r"^\s+Qualified:?\s+\d+\s+(\S.*?)\s{2,}Candidate Stmt|^\s+\d+\s+Qualified\s+(\S.*?)\s{2,}(?:Yes|No)\s{2,}"
)


def qualified_list(
    fips: str, county: str, expand: tuple[tuple[str, str], ...] = (), roleless: bool = False
) -> list[Row]:
    rows = []
    current: tuple[str, str, str, int, bool, bool, str] | None = None
    names: list[str] = []
    used: set[tuple[str, str]] = set()

    def flush() -> None:
        if current is not None:
            district, role, label, seats, short, ballot, code = current
            seat = label or ("Short Term" if short else "")
            if (district, seat) in used:
                seat = f"Contest {code}"
            used.add((district, seat))
            for name in dict.fromkeys(names):
                rows.append(
                    {
                        "county": county,
                        "district": district,
                        "seat": seat,
                        "role": role,
                        "seats": seats,
                        "name": name,
                        "unexpired": short,
                        "tacit": not ballot,
                    }
                )

    for line in lines(fips):
        if line.startswith("Contest:") and not QUALIFIED_HEAD.match(line):
            flush()
            current, names = None, []
        elif found := QUALIFIED_HEAD.match(line):
            flush()
            current, names = None, []
            heading = " ".join(found[2].split())
            for pattern, replacement in expand:
                heading = re.sub(pattern, replacement, heading)
            role_found = QUALIFIED_ROLE.search(heading)
            if re.match(r"(?i)city of|county board", heading) or (role_found is None and not roleless):
                continue
            before = (
                heading[: role_found.start()]
                if role_found
                else re.sub(r"\s*(?:\(.*?\)|-?\s*Short Term\b)", "", heading)
            )
            rest = heading[role_found.end() :] if role_found else ""
            in_before = QUALIFIED_SEAT.search(before)
            seat = in_before or QUALIFIED_SEAT.search(rest)
            body = (before[: in_before.start()] if in_before else before).strip(" ,-")
            if not usable(body):
                continue
            label = ""
            if seat:
                kind = (
                    seat[1]
                    .title()
                    .replace("Ta", "Trustee Area")
                    .replace("Tra", "Trustee Area")
                    .replace("Subdivision", "Division")
                )
                label = f"{kind} {seat[2]}"
            ballot = (found[3] or found[6] or found[7]) == "Yes"
            seats = int(found[4] or found[5] or found[8] or 1)
            role = (
                "Trustee"
                if re.search(r"(?i)trustee|governing board|college", (role_found[0] if role_found else "") + body)
                else "Director"
            )
            current = (
                full_name(cased(body.upper()) if re.search(r"[A-Z]{3,}", body) else body),
                role,
                label,
                1 if label and seats > 1 else seats,
                re.search(r"(?i)short term|unexpired", heading) is not None,
                ballot,
                found[1],
            )
        elif current is not None and (found := QUALIFIED_CAND.match(line)):
            name = found[1] or found[2]
            names.append(re.sub(r"\b(Ii|Iii|Iv)\b", lambda found: found[0].upper(), cased(name)))
    flush()
    return rows


def page_lines(path: Path) -> list[str]:
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", path.read_text())))
    return [line.strip() for line in text.splitlines() if line.strip()]


YOLO_HEAD = re.compile(
    r"^(?P<body>.+?), (?P<role>Director|Commissioner|Governing Board Member)(?:, (?P<seat>.+?))? - (?P<ballot>ON|OFF) THE BALLOT$"
)
YOLO_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
YOLO_OUT = re.compile(r"Declined|Withdrawn")


def yolo() -> list[Row]:
    rows: list[Row] = []
    for key in ("06113-c", "06113-s"):
        current: re.Match[str] | None = None
        seats = 1
        block: list[str] = []
        for line in page_lines(inbox_doc(key)):
            if found := YOLO_HEAD.match(line):
                current = found if usable(found["body"]) else None
                seats = 1
                block = []
            elif current is None:
                continue
            elif found := re.match(r"Vote for (?:no more than )?\w+ \((\d+)\)", line):
                seats = int(found[1])
            elif line == "Misc.":
                block = []
            elif (
                line == "Qualified"
                or YOLO_OUT.match(line)
                or (line == "Did Not File" and not any(YOLO_DATE.fullmatch(item) for item in block))
            ):
                if line == "Qualified" and block:
                    seat = current["seat"] or ""
                    short = re.search(r"(?i)short term|unexpired", seat) is not None
                    rows.append(
                        {
                            "county": "Yolo",
                            "district": full_name(current["body"]),
                            "seat": "Short Term" if short else seat,
                            "role": "Commissioner"
                            if current["role"] == "Commissioner"
                            else "Trustee"
                            if "College" in current["body"]
                            else "Director",
                            "seats": 1 if seat and not short else seats,
                            "name": re.sub(r"\s*\((?:I|A/I|W/I)\)$", "", block[0]),
                            "unexpired": short,
                            "tacit": current["ballot"] == "OFF",
                            "doc": key,
                        }
                    )
                block = []
            elif line not in ("Candidate", "County of Residence"):
                block.append(line)
    return rows


SF_HEAD = re.compile(r"^(?P<title>[A-Z][^.]*?)(?: \((?P<seats>\d+) seats?\))?$")


def san_francisco() -> list[Row]:
    rows: list[Row] = []
    text = page_lines(raw.cached(local_2026.SOURCE, feed("06075")[0]).path)
    current: tuple[str, str, str, int, bool] | None = None
    for index, line in enumerate(text):
        after = text[index + 1] if index + 1 < len(text) else ""
        if re.match(r"\d+ (?:people|person) (?:have|has) (?:qualified|filed)", after):
            title = SF_HEAD.match(line)
            current = None
            if title is None:
                continue
            seats = int(title["seats"] or 1)
            if title["title"].startswith("Community College Board"):
                short = "Partial Term" in title["title"]
                current = (
                    "San Francisco Community College District",
                    "Trustee",
                    "Short Term" if short else "",
                    seats,
                    short,
                )
            elif found := re.match(r"BART Board, (District \d+)$", title["title"]):
                current = (ALAMEDA_NAMES["BART"], "Director", found[1], 1, False)
        elif current is not None and after.startswith("Declaration of Candidacy Filed"):
            district, role, seat, seats, short = current
            rows.append(
                {
                    "county": "San Francisco",
                    "district": district,
                    "seat": seat,
                    "role": role,
                    "seats": seats,
                    "name": line,
                    "unexpired": short,
                    "tacit": False,
                }
            )
    return rows


LISTED_BOARDS = (
    (
        "Colusa",
        "06011-s",
        (
            ("Arbuckle Park and Recreation District", "", 3, ("Kade Wilson",)),
            ("Maxwell Park and Recreation District", "", 3, ("Kyle Miller", "Crystal Hill")),
            ("Arbuckle Public Utility District", "", 3, ("Johnathan Ring", "Kevin Wood")),
            ("Arbuckle College City Fire Protection District", "", 2, ("David Burgess",)),
            ("Bear Valley Indian Valley Fire Protection District", "", 2, ("Francis Elliott", "Bridget Kaplan")),
            ("Maxwell Fire Protection District", "", 3, ("Allen Beck",)),
        ),
    ),
    (
        "Mono",
        "06051-s",
        (
            (
                "Southern Mono Healthcare District",
                "",
                2,
                ("Joanne Hunt", "Sophie Bidet", "Yuri Parisky", "Matthew Hall", "Daniel Ranson"),
            ),
        ),
    ),
    (
        "Yuba",
        "06115-s",
        (
            ("Dobbins Oregon House Fire Protection District", "", 2, ("Dustin Strain", "Bart Young", "Meghan Hill")),
            ("Linda County Water District", "Division 1", 1, ("Richard Brannan", "Samuel Fuhrer")),
            ("Linda County Water District", "Division 5", 1, ("Gordon Stevens", "Ransom Shackelford")),
            ("Olivehurst Public Utility District", "Division 2", 1, ("John Floe", "Tom McWhorter")),
            ("Olivehurst Public Utility District", "Division 3", 1, ("Mary Jane Griego", "John Jones")),
            ("Smartsville Fire Protection District", "", 2, ("John Fuschich", "Daniel Zuber", "Vaughn O’Malley")),
        ),
    ),
    (
        "Sierra",
        "06091-s",
        (
            ("Alleghany County Water District", "", 3, ("Robbin DeWeese",)),
            ("Sierra City Fire Protection District", "", 2, ("Sigurd Ostrom",)),
            ("Eastern Plumas Healthcare District", "", 3, ("Robert Duncan", "Andy Genasci", "Marcia Hughes")),
        ),
    ),
    (
        "Tuolumne",
        "06109-s",
        (
            (
                "Groveland Community Services District",
                "",
                3,
                (
                    "John Armstrong",
                    "Robert Swan",
                    "Bruce M. Thompson",
                    "Donald Felts",
                    "Bruce Dudley",
                    "Robert W. Turney Sr",
                    "Steve Perreira",
                ),
            ),
            ("Tuolumne Utilities District", "Division 1", 1, ("Tom Crosby", "Ron W. Ringen")),
        ),
    ),
    (
        "Stanislaus",
        "06099-l",
        (
            ("Denair Community Services District", "", 3, ("Nate Couchman", "Joel Sanders")),
            ("Keyes Community Services District", "", 3, ("Johnathon Parker",)),
            ("Knights Ferry Community Services District", "", 3, ("Tracy Griesner", "Sally Goehring")),
            ("Salida Sanitary District", "", 3, ("Gary Dew", "Ashley M. Moore")),
            ("Hughson Fire Protection District", "", 2, ("Miguel Osequera", "Gus Villarreal")),
            ("Westport Fire Protection District", "", 2, ("Ross W. Lee", "Ed Amador")),
            ("Oakdale Irrigation District", "", 3, ("Tom Orvis", "Brad DeBoer")),
            ("Patterson Irrigation District", "", 2, ("John C. Azevedo", "Dan Robinson")),
            ("Turlock Irrigation District", "", 3, ("David J. Yonan", "Joe Alamo", "Ron Macedo")),
            (
                "Del Puerto Health Care District",
                "",
                5,
                ("Reyna Gomez", "Ana Andrade", "Sylvia Ramirez", "Becky Campo"),
            ),
            (
                "Oak Valley Hospital District",
                "",
                4,
                ("Danielle Sanders", "Ann Bairos", "Dan Cummins", "Billie Jean Scott"),
            ),
        ),
    ),
    (
        "San Luis Obispo",
        "06079-l",
        (
            (
                "Heritage Ranch Community Services District",
                "",
                3,
                ("Michael Camou", "Masen Yaffee", "Bill Barker, Jr."),
            ),
            ("Los Osos Community Services District", "Division 1", 1, ("Chuck Cesna",)),
            ("Los Osos Community Services District", "Division 4", 1, ("Chris Womack",)),
            ("San Simeon Community Services District", "Area B", 1, ("Michael Donahue",)),
            ("San Simeon Community Services District", "Area E", 1, ("Karina Tiwana",)),
            ("Cambria Community Healthcare District", "Division 2", 1, ("Laurie Mileur",)),
            ("Cambria Community Healthcare District", "Division 3", 1, ("Bruce Nii",)),
            ("Port San Luis Harbor District", "Division 3", 1, ("Mary Matakovich",)),
            ("Port San Luis Harbor District", "Division 5", 1, ("Drew Brandy",)),
            ("Santa Margarita Fire Protection District", "", 2, ("Elizabeth Gorrill", "Joel Switzer")),
        ),
    ),
)
SAN_BENITO_EXPAND = (
    (r"^Aromas Water District", "Aromas County Water District"),
    (r"^Sunnyslope Water", "Sunnyslope County Water"),
    (r"^Tres Pinos Water", "Tres Pinos County Water"),
    (r"^Aromas Fire Dist\.", "Aromas Tri-County Fire Protection District"),
)


def listed_boards() -> list[Row]:
    rows = []
    for county, key, boards in LISTED_BOARDS:
        text = " ".join(" ".join(page_lines(inbox_doc(key)) if key == "06051-s" else lines(key)).split()).casefold()
        for district, seat, seats, names in boards:
            for name in names:
                assert name.casefold() in text, (county, name)
                rows.append(
                    {
                        "county": county,
                        "district": district,
                        "seat": seat,
                        "role": "Commissioner" if "Fire" in district else "Director",
                        "seats": seats,
                        "name": name,
                        "unexpired": False,
                        "tacit": len(names) <= seats,
                        "doc": key,
                    }
                )
    return rows


def san_benito() -> list[Row]:
    return [
        row
        for row in tagged(
            qualified_list("06069-s", "San Benito", (*LISTED_EXPAND, *SAN_BENITO_EXPAND), True), "06069-s"
        )
        if "College" not in str(row["district"])
    ]


def candidates() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"tacit": False, "doc": "", **row}
            for row in [
                *san_diego(),
                *orange(),
                *sacramento(),
                *los_angeles(),
                *riverside(),
                *san_bernardino(),
                *santa_clara(),
                *alameda(),
                *alameda(off_ballot=True),
                *contra_costa(),
                *fresno(),
                *kern(),
                *tulare(),
                *sonoma(),
                *marin(),
                *[row for row in monterey() if row["district"] != "Aromas Water District"],
                *san_joaquin(),
                *qualified_list("06007", "Butte"),
                *qualified_list("06017", "El Dorado"),
                *qualified_list("06061", "Placer"),
                *qualified_list("06079", "San Luis Obispo"),
                *qualified_list("06081", "San Mateo"),
                *merced(),
                *kings(),
                *shasta(),
                *humboldt(),
                *imperial(),
                *santa_cruz(),
                *santa_cruz_watch({row["district"] for row in santa_cruz()}),
                *sutter(),
                *napa(),
                *solano(),
                *santa_barbara(),
                *trinity(),
                *siskiyou(),
                *inyo(),
                *del_norte(),
                *calaveras(),
                *amador(),
                *tehama(),
                *lassen(),
                *nevada(),
                *stanislaus(),
                *lake(),
                *madera(),
                *mendocino(),
                *ventura(),
                *san_bernardino_lieu(),
                *sonoma_lieu(),
                *san_francisco(),
                *yolo(),
                *listed_boards(),
                *san_benito(),
            ]
        ]
    ).unique(["county", "district", "seat", "name"], keep="first", maintain_order=True)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = candidates()
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame.select("county", "district").unique().height)


def single_county(conn: Connection, frame: pl.DataFrame) -> pl.DataFrame:
    homes = govcand.counties(conn, "06")
    order = list(COUNTIES.values())
    pairs = frame.select("county", "district").unique()
    shared = pairs.group_by("district").agg(pl.col("county")).filter(pl.col("county").list.len() > 1)
    keep = set()
    for district, found in shared.iter_rows():
        existing = [
            county
            for county in found
            if govmint.fold(district) in govmint.held(conn, homes[ceda._core(f"{county} County")][0])
        ]
        keep.add(f"{district}|{(existing or sorted(found, key=order.index))[0]}")
    shared_names = set(shared["district"])
    return frame.filter(
        ~pl.col("district").is_in(shared_names) | pl.concat_str(["district", "county"], separator="|").is_in(keep)
    )


def emit() -> Path:
    with connect() as conn:
        frame = single_county(conn, pl.read_parquet(PARSED / "candidates.parquet"))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California special district board races on the November 3, 2026 ballot, with candidates from county registrar candidate lists",
            polity="US-CA",
        )
        docs = (
            {
                fips: (*feed(fips), SOURCE if fips in OWN else local_2026.SOURCE)
                for fips in COUNTIES
                if f"{fips}-s" not in INBOX_DOCS and fips not in LISTED
            }
            | {key: (*feed(key), local_2026.SOURCE) for key in (*LISTED.values(), "06083-s")}
            | {
                key: (*value, SOURCE)
                for key, value in (
                    LIEU | {"06023-s": HUMBOLDT} | {key: item[0] for key, item in INBOX_DOCS.items()}
                ).items()
            }
        )
        sources = {}
        for key, (url, *publisher, bucket) in docs.items():
            if key in INBOX_DOCS:
                inbox_doc(key)
            source = cs.source(*publisher)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher[0],
                    title=publisher[1],
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(bucket, url), source)
            sources[key] = str(source)
        names = {county: fips for fips, county in COUNTIES.items()}
        frame = frame.with_columns(
            pl.col("doc")
            .replace("", None)
            .fill_null(pl.col("county").replace_strict(names, default=None))
            .replace_strict(sources)
            .alias("source")
        )
        kept = govcand.races(conn, cs, SOURCE, "CA", DAY, frame, UUID(int=0), "06")
        print(f"{kept} candidates")
        return cs.write()
