import html
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import govcand, local_2026
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect

SOURCE = "us.ca_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
COUNTIES = {"06073": "San Diego", "06059": "Orange", "06067": "Sacramento", "06037": "Los Angeles", "06065": "Riverside", "06071": "San Bernardino", "06085": "Santa Clara", "06001": "Alameda", "06013": "Contra Costa", "06019": "Fresno"}
KEPT = re.compile(
    r"FIRE|WATER|UTILIT|HEALTH|IRRIGATION|WASTEWATER|SERVICES|SRVCS|PARK|LIBRARY|SANITATION|SANITARY|CEMETERY|SEWER|CONSERVATION|FLOOD"
)
DROPPED = re.compile(r"SCHL|SCHOOL|COLLEGE|COMM PLAN|PLANNING|SUPERVISORS|ASSESSOR|JUDGE|COURT|^CITY")
WORDS = (
    (r"\bMUNI\b", "MUNICIPAL"),
    (r"\bCONS\b", "CONSOLIDATED"),
    (r"\bPROT\b", "PROTECTION"),
    (r"\bSRVCS\b", "SERVICES"),
    (r"\bHEATLH\b", "HEALTH"),
)
SEAT = re.compile(
    r"^(.+?)\s+-\s+(?:DIV(?:ISION)?\.?|ZONE|AREA)\s+(?:NO\.\s+)?(\w+)(?:\s+\(Short Term\))?$", re.IGNORECASE
)
PERSON = re.compile(r"^([A-Z][^,]*?), (.+?)(?: - ([A-Z]{2,4}))?\s{3,}\S.*\s(YES|NO)\s*$")


def full_name(label: str) -> str:
    for short, full in WORDS:
        label = re.sub(short, full, label)
    text = cased(label).replace(" Del ", " del ").replace(" And ", " and ").replace(" Of ", " of ")
    return text if re.search(r"\b(?:District|Authority|Agency)\b|County$", text) else f"{text} District"


def feed(fips: str) -> tuple[str, str, str]:
    if fips in local_2026.PUBLISHERS:
        return (local_2026.FILES[fips], *local_2026.PUBLISHERS[fips][1:])
    return local_2026.FEEDS[f"CA-{fips}"]


def lines(fips: str) -> list[str]:
    path = raw.cached(local_2026.SOURCE, feed(fips)[0]).path
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
            keep = found[1] == "YES" and KEPT.search(heading) is not None and DROPPED.search(heading) is None
            seat = SEAT.match(re.sub(r"\s*\(Short Term\)", "", heading))
            body, label = (
                (seat[1], f"{'Zone' if 'ZONE' in heading.upper() else 'Division'} {seat[2]}") if seat else (heading, "")
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
        elif live and line == "Qualified" and re.fullmatch(r"\d+", text[index - 1]):
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
    r"^(?:Non-?Partisan|NONPARTISAN OFFICES) (?:DISTRICT) (?P<body>.+?) (?P<role>Director|Trustee|Member, Board of (?:Directors|Trustees))(?P<rest>.*)$",
    re.IGNORECASE,
)
PROOF_SEAT = re.compile(r"(Division|Zone|District Area|Area|At Large)(?: No\.?)? ?(\d+)")


def proof_list(fips: str, county: str, indent: int) -> list[dict[str, str | int | bool]]:
    contests: dict[tuple[str, str, str, bool], list[str]] = {}
    incumbents: dict[tuple[str, str, str, bool], int] = {}
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
            role = "Trustee" if "trustee" in found["role"].lower() else "Director"
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
        elif found := re.match(r"\s+Candidate\(s\):\s+(\S.*?)\s*$", line):
            name = cased(found[1]) if found[1].isupper() else found[1]
            if name not in contests[current]:
                contests[current].append(name)
        elif re.match(r"\s+Incumbent\(s\):", line) or re.match(
            gap + r"\S.*\s{3,}(?:Elected|Appointed in Lieu|Appointed to Vacancy|Vacated)\s*$", line
        ):
            incumbents[current] += 1
        elif (
            re.match(gap + r"\S", line)
            and not re.match(
                gap + r"(?:Requirements|Sigs|Declaration|Nomination|Candidate|Code|Incumbent|Appointed|Director|Vacant)",
                line,
            )
            and (re.match(r"\s*$", text[index - 1]) or "Shared with" in text[index - 1])
        ):
            name = re.sub(r"\s{2,}.*$", "", line.strip())
            name = cased(name) if name.isupper() else name
            if name not in contests[current]:
                contests[current].append(name)
    rows = []
    for key, names in contests.items():
        district, role, label, short = key
        seats = 1 if label else max(incumbents[key], 1)
        if len(names) <= seats:
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
                }
            )
    return rows


def riverside() -> list[dict[str, str | int | bool]]:
    return proof_list("06065", "Riverside", 33)


def fresno() -> list[dict[str, str | int | bool]]:
    return proof_list("06019", "Fresno", 30)


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
        elif live and (found := re.match(r"^([A-Z][A-Z .,'\"ÑÁÉÍÓÚ-]+?)(?: - (?:Appointed )?Incumbent)?\s*$", line)):
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
                    }
                )
    return rows


SANTA_CLARA = re.compile(r"^(?P<body>.+?),? (?P<kind>District|Ward|D)\s?(?P<number>\d+)$")


def santa_clara() -> list[dict[str, str | int | bool]]:
    contests: dict[tuple[str, str], list[str]] = {}
    incumbents: dict[tuple[str, str], int] = {}
    current = None
    for index, line in enumerate(lines("06085")):
        if found := re.match(r"^6\d{3}\s+(\S.*?)\s{2,}On Ballot: (Yes|No)\s+Vote For: (\d+)", line):
            label = found[1]
            if seat := SANTA_CLARA.match(label):
                current = (seat["body"], f"{'Ward' if seat['kind'] == 'Ward' else 'District'} {seat['number']}")
            else:
                current = (label, "")
            contests.setdefault(current, [])
            incumbents[current] = int(found[3]) if found[2] == "Yes" else 0
        elif current is None:
            continue
        elif found := re.match(r"\s+Qualified\s+\d+\s+(\S.*?)\s{2,}Candidate Stmt", line):
            contests[current].append(found[1])
    rows = []
    for key, names in contests.items():
        seats = incumbents[key]
        if seats == 0 or len(names) <= seats:
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
        re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", raw.cached(local_2026.SOURCE, feed("06001")[0]).path.read_text()))
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
                and not re.search(r"(?i)school|college|education|city|unified|congress|assembly|senate|equalization", line)
                else None
            )
            seats = int(re.match(r"Vote For: (\d+)", text[index + 2])[1]) if contest is not None else 1
            continue
        if contest is None:
            continue
        name = None
        if line == "CANDIDATE" and text[index + 1] != "BALLOT DESIGNATION":
            name = text[index + 1]
        elif line == "DETAILS" and text[index - 4] == "CANDIDATE":
            name = text[index + 1]
        if name is not None:
            body = ALAMEDA_NAMES.get(contest["body"], contest["body"])
            seat = f"{'Ward' if contest['kind'] == 'Ward' else 'Area' if contest['kind'] == 'Area' else 'District' if contest['kind'] == 'District' else 'Division'} {contest['number']}" if contest["kind"] else ""
            key = (body, seat, name)
            if key not in seen:
                seen.add(key)
                rows.append(
                    {
                        "county": "Alameda",
                        "district": full_name(re.sub(r"\bDist\b", "District", body)) if body == contest["body"] else body,
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
        seat = next((f"{match[1]} {match[2]}" for part in parts if (match := re.fullmatch(r"(Division|Area|Ward|Zone) (\d+)", part))), "")
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


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame([*san_diego(), *orange(), *sacramento(), *los_angeles(), *riverside(), *san_bernardino(), *santa_clara(), *alameda(), *contra_costa(), *fresno()]).unique(
        ["county", "district", "seat", "name"], keep="first", maintain_order=True
    )
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame.select("county", "district").unique().height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California special district board races on the November 3, 2026 ballot, with candidates from county registrar candidate lists",
            polity="US-CA",
        )
        kept = 0
        for fips, county in COUNTIES.items():
            url, *publisher = feed(fips)
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
            cs.artifact(raw.cached(local_2026.SOURCE, url), source)
            kept += govcand.races(conn, cs, SOURCE, "CA", DAY, frame.filter(pl.col("county") == county), source, "06")
        print(f"{kept} candidates")
        return cs.write()
