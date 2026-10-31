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
}
KEPT = re.compile(
    r"FIRE|WATER|UTILIT|HEALTH|IRRIGATION|WASTEWATER|SERVICES|SRVCS|PARK|LIBRARY|SANITATION|SANITARY|CEMETERY|SEWER|CONSERVATION|FLOOD"
    r"|COLLEGE|HOSPITAL|VECTOR|MOSQUITO|RESOURCE|TRANSIT|HARBOR|AIRPORT|DRAINAGE|LEVEE|LIGHTING|MEMORIAL|POLICE|PEST|RECREATION"
    r"|OPEN SPACE|RAPID|REGIONAL|HIGHWAY|WEED|ROAD|COMMUNITY"
)
DROPPED = re.compile(
    r"SCHL|SCHOOL|UNIFIED|ELEMENTARY|UNION HIGH|HIGH SCH|OFFICE OF EDUCATION|BOARD OF EDUCATION|COMM PLAN|PLANNING|SUPERVISORS|ASSESSOR|JUDGE|COURT|^CITY|^TOWN OF|CITY OF|CITY COUNCIL|CONGRESS(?! VALLEY)|ASSEMBLY|SENATE|COMMITTEE|PLAN GRP|PLAN GROUP|COMMUNITY PLAN|SPONSOR GROUP|COUNCIL|MAYOR|CLERK|TREASURER|SHERIFF|ATTORNEY|CORONER|AUDITOR"
)
WORDS = (
    (r"\bMUNI\b", "MUNICIPAL"),
    (r"\bGROSSMNT-CUY\b", "GROSSMONT-CUYAMACA"),
    (r"\bCOM(?:M|MUNITY)? COLLEGE\b", "COMMUNITY COLLEGE"),
    (r"\bCONS\b", "CONSOLIDATED"),
    (r"\bPROT\b", "PROTECTION"),
    (r"\bSRVCS\b", "SERVICES"),
    (r"\bHEATLH\b", "HEALTH"),
)
SEAT = re.compile(
    r"^(.+?)\s+-\s+(DIV(?:ISION)?\.?|ZONE|AREA|TRUSTEE AREA|DISTRICT)\s+(?:NO\.\s+)?(\w+)(?:\s+\(Short Term\))?$",
    re.IGNORECASE,
)
SEAT_WORDS = {"ZONE": "Zone", "AREA": "Area", "TRUSTEE AREA": "Trustee Area", "DISTRICT": "District"}
PERSON = re.compile(r"^([A-Z][^,]*?), (.+?)(?: - ([A-Z]{2,4}))?\s{3,}\S.*\s(YES|NO)\s*$")


def usable(label: str) -> bool:
    return KEPT.search(label.upper()) is not None and DROPPED.search(label.upper()) is None


def full_name(label: str) -> str:
    for short, full in WORDS:
        label = re.sub(short, full, label)
    text = cased(label).replace(" Del ", " del ").replace(" And ", " and ").replace(" Of ", " of ")
    return text if re.search(r"\b(?:District|Authority|Agency|System)\b|County$", text) else f"{text} District"


def feed(fips: str) -> tuple[str, str, str]:
    if fips in local_2026.PUBLISHERS:
        return (local_2026.FILES[fips], *local_2026.PUBLISHERS[fips][1:])
    return local_2026.FEEDS[f"CA-{fips}"]


INBOX = {
    "06089": "shasta.pdf",
}
OWN = {"06111", *INBOX}


def lines(fips: str) -> list[str]:
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
    return (
        subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
        .stdout.decode()
        .splitlines()
    )


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
    r"^(?:Non-?Partisan|NONPARTISAN OFFICES) (?:DISTRICT|SCHOOL) (?P<body>.+?) (?:For )?(?P<role>Director|Trustee|Governing Board Member|Member, Board of (?:Directors|Trustees))(?P<rest>.*)$",
    re.IGNORECASE,
)
PROOF_SEAT = re.compile(r"(Division|Zone|District Area|Trustee Area|Area|Seat|At Large)(?: No\.?)? ?(\d+)")


def proof_list(fips: str, county: str, indent: int, qualified_only: bool = False) -> list[dict[str, str | int | bool]]:
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
            role = "Trustee" if re.search(r"trustee|governing", found["role"].lower()) else "Director"
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
INBOX_DOCS = {
    "06025-s": (IMPERIAL, "imperial_0820.pdf", "application/pdf"),
    "06087-s": (SANTA_CRUZ, "santacruz_contact.html", "text/html"),
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
        file = DATA / "inbox" / "y26" / "ca" / name
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
                current = (full_name(body), "Trustee" if "College" in body else "Director", label, int(head[2] or 1))
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
    return qualified_list("06055", "Napa")


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
}
LIEU_NAME = re.compile(r"^(?P<name>\S.*?)\s{2,}(?:(?P<text>\S.*?)\s{2,})?(?P<term>\d) years?\s*$")
LIEU_OFFICE = re.compile(
    r"^(?P<body>.+?) Member, Board of (?P<role>Directors|Trustees),?\s*(?P<rest>.*)$", re.IGNORECASE
)


def san_bernardino_lieu() -> list[Row]:
    path = raw.cached(SOURCE, LIEU["06071-lieu"][0]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    text = text.splitlines()
    entries = []
    for index, line in enumerate(text):
        found = LIEU_NAME.match(line)
        if found is None or line.startswith(("SPECIAL", "OCTOBER", "PAGE")):
            continue
        office = found["text"] or " ".join(part.strip() for part in (text[index - 1], text[index + 1]) if part.strip())
        entries.append((found["name"].strip(), " ".join(office.split()), found["term"]))
    keyed: dict[tuple[str, str, bool], list[str]] = {}
    for name, office, term in entries:
        split = LIEU_OFFICE.match(office)
        if split is None or not usable(split["body"]):
            continue
        seat = re.search(r"(Division|Trustee Area) (\d+)", split["rest"])
        short = "Short Term" in split["rest"] or term == "2"
        keyed.setdefault((split["body"], f"{seat[1]} {seat[2]}" if seat else "", short), []).append(name)
    return [
        {
            "county": "San Bernardino",
            "district": full_name(body),
            "seat": label or ("Short Term" if short else ""),
            "role": "Director",
            "seats": 1 if label else len(names),
            "name": name,
            "unexpired": short,
            "tacit": True,
            "doc": "06071-lieu",
        }
        for (body, label, short), names in keyed.items()
        for name in names
    ]


def kings() -> list[Row]:
    return proof_list("06031", "Kings", 32)


def santa_barbara() -> list[Row]:
    return proof_list("06083-s", "Santa Barbara", 32)


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
    r"^(?:Member, Board Of Directors?, )?(?P<body>.+?)(?: Directors?)?"
    r"(?:,? (?P<kind>Ward|District|Area|Dist) #?(?P<number>\d+))?(?P<short>, Short Term)?$"
)
ALAMEDA_KEPT = re.compile(r"(?i)water|EBMUD|sanitary|fire|park|health|irrigation|BART|AC Transit|Dublin San Ramon")


def alameda() -> list[dict[str, str | int | bool]]:
    rows = []
    text = html.unescape(
        re.sub(
            r"<[^>]+>",
            "\n",
            re.sub(
                r"(?s)<(script|style).*?</\1>", "", raw.cached(local_2026.SOURCE, feed("06001")[0]).path.read_text()
            ),
        )
    )
    text = [line.strip() for line in text.splitlines() if line.strip()]
    contest = None
    seats = 1
    seen: set[tuple[str, str, str]] = set()
    for index, line in enumerate(text):
        if line.endswith(" -") and text[index + 1 : index + 2] == ["On Ballot"]:
            found = ALAMEDA.match(line[:-2])
            contest = (
                found
                if found is not None
                and ALAMEDA_KEPT.search(found["body"])
                and not re.search(
                    r"(?i)school|college|education|city|unified|congress|assembly|senate|equalization", line
                )
                else None
            )
            seats = int(re.match(r"Vote For: (\d+)", text[index + 2])[1]) if contest is not None else 1
            continue
        if contest is None:
            continue
        name = None
        if (
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
                        "district": full_name(re.sub(r"\bDist\b", "District", body))
                        if body == contest["body"]
                        else body,
                        "seat": seat or ("Short Term" if contest["short"] else ""),
                        "role": "Director",
                        "seats": seats,
                        "name": " ".join(cased(name).split()),
                        "unexpired": contest["short"] is not None,
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


def marin() -> list[Row]:
    text = text_of("06041")
    start = next(i for i, line in enumerate(text) if line == "Return to Top")
    rows = []
    index = start
    while index < len(text) - 1:
        head = re.match(r"^(.+? (?:District|Authority|Agency)) Director(?:,? (.*))?$", text[index])
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
        if "School" not in head[1] and "College" not in head[1]:
            rows += rows_of("Marin", head[1], head[2] or "", seats, names)
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
            split = re.match(r"^(.+? (?:District|Authority|Agency)),? (.*)$", contest[1])
            if split and usable(split[1]):
                rows.extend(rows_of("San Joaquin", split[1], split[2], int(contest[3]), names, contest[2] == "No"))

    for line in lines("06077"):
        if found := SJ_HEAD.match(line):
            flush()
            contest = found.groups() if int(found[1]) >= 5000 else None
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
    r"(Division|Zone|Area|Ward|Seat|Subdivision|Trustee Area|TA|District)\s*(?:No\.?\s*)?(\d+|(?-i:[A-G])\b)",
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
        if found := QUALIFIED_HEAD.match(line):
            flush()
            current, names = None, []
            heading = " ".join(found[2].split())
            for pattern, replacement in expand:
                heading = re.sub(pattern, replacement, heading)
            role_found = QUALIFIED_ROLE.search(heading)
            if re.match(r"(?i)city of|county board", heading) or (role_found is None and not roleless):
                continue
            before = heading[: role_found.start()] if role_found else re.sub(r"\s*\(.*?\)", "", heading)
            rest = heading[role_found.end() :] if role_found else ""
            in_before = QUALIFIED_SEAT.search(before)
            seat = in_before or QUALIFIED_SEAT.search(rest)
            body = (before[: in_before.start()] if in_before else before).strip(" ,-")
            if not usable(body):
                continue
            label = ""
            if seat:
                kind = seat[1].title().replace("Ta", "Trustee Area").replace("Subdivision", "Division")
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
            names.append(cased(name) if name.isupper() else name)
    flush()
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
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
                *contra_costa(),
                *fresno(),
                *kern(),
                *tulare(),
                *sonoma(),
                *marin(),
                *monterey(),
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
                *sutter(),
                *napa(),
                *solano(),
                *santa_barbara(),
                *stanislaus(),
                *ventura(),
                *san_bernardino_lieu(),
            ]
        ]
    ).unique(["county", "district", "seat", "name"], keep="first", maintain_order=True)
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
        docs = {
            fips: (*feed(fips), SOURCE if fips in OWN else local_2026.SOURCE)
            for fips in COUNTIES
            if f"{fips}-s" not in INBOX_DOCS
        } | {
            key: (*value, SOURCE)
            for key, value in (
                LIEU | {"06023-s": HUMBOLDT} | {key: item[0] for key, item in INBOX_DOCS.items()}
            ).items()
        }
        sources = {}
        for key, (url, *publisher, bucket) in docs.items():
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
            .fill_null(pl.col("county").replace_strict(names))
            .replace_strict(sources)
            .alias("source")
        )
        kept = govcand.races(conn, cs, SOURCE, "CA", DAY, frame, UUID(int=0), "06")
        print(f"{kept} candidates")
        return cs.write()
