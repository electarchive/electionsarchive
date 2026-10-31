import csv
import html
import io
import json
import os
import re
import time
import zipfile
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.clarity_local import COUNTIES, STATES
from ea.sources.us.oe_primaries_2026 import ALIASES, FIPS, OFFICIAL, plain, town_counties
from ea.store import DATA, connect

SOURCE = "us.enr_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date.fromisoformat(os.environ.get("ENR_2026_DAY", "2026-11-03"))
ONLY = set(filter(None, os.environ.get("ENR_2026_STATES", "").split(",")))
HEADERS = {"User-Agent": "electionsarchive.com"}
EV = {
    "GA": ("results.sos.ga.gov", "Georgia"),
    "ID": ("results.voteidaho.gov", "id"),
    "UT": ("electionresults.utah.gov", "utah"),
    "RI": ("electionresults.ri.gov", "RhodeIsland"),
    "WA": ("results.votewa.gov", "washington"),
    "VA": ("app.enhancedvoting.com", "virginia"),
}
CLARITY = {
    "IA": "https://electionresults.iowa.gov/IA/",
    "SC": "https://www.enr-scvotes.org/SC/",
    "WV": "https://results.enr.clarityelections.com/WV/",
}
EV_OFFICES = re.compile(
    r"^(?:(?P<party0>REP|DEM) )?(?:(?P<senate>(?:U\.? ?S\.? |United States )Senat(?:e|or)|Senator in Congress)"
    r"|(?P<governor>Governor)"
    r"|(?:(?:U\.? ?S\.? (?:House|Representative)s?|United States Representative|Representative in Congress)\b[^\d]*"
    r"|Congressional District (?=\d+ - U\.? ?S\.? Representative$))"
    r"(?P<district>\d+)(?:st|nd|rd|th)?(?:\s+Congressional)?(?:\s+District)?(?: - U\.? ?S\.? Representative)?)"
    r"\s*(?:-\s*(?P<party1>Rep|Dem|Democratic|Republican|Libertarian))?\s*$",
    re.IGNORECASE,
)
LISTINGS = (
    "elections.json",
    "cid=arkansas",
    "/ballot-items",
    "Georgia",
    "/id",
    "/utah",
    "/RhodeIsland",
    "/washington",
    "/virginia",
    "Multiparish.htm",
)
EV_MEMBER = re.compile(r"Member, (?:United States Senate|House of Representatives \((\d+)(?:st|nd|rd|th) District\))")
SUFFIXES = ("jr", "sr", "ii", "iii", "iv")
YESNO = {"for": "yes", "against": "no"}
STOP = {"the", "of", "and", "for", "district", "county", "city", "town", "board", "office"}
FL_OFFICES = {"USS": "senate", "USR": "house", "GOV": "governor"}
IL_ELECTIONS = {2022: 62, 2024: 66, 2026: 70}
IL_CITIES = {
    "CITY OF CHICAGO": "Cook",
    "CITY OF BLOOMINGTON": "McLean",
    "CITY OF DANVILLE": "Vermilion",
    "CITY OF EAST ST. LOUIS": "St. Clair",
    "CITY OF GALESBURG": "Knox",
    "CITY OF ROCKFORD": "Winnebago",
}
IL_CONTEST = re.compile(r"UNITED STATES SENATOR|GOVERNOR.*|(\d+)(?:ST|ND|RD|TH) CONGRESS")
KS_CONTEST = re.compile(
    r"(?:United States (?P<senate>Senate)|United States House of Representatives (?P<district>\d+)|(?P<governor>Governor / Lt\. Governor))"
    r"(?: \((?P<party>\w)\))?"
)
MN_CONTEST = re.compile(r"(U\.S\. Representative|U\.S\. Senator|Governor & Lt Governor)(?: District (\d+))?")
MN_OFFICES = {"U.S. Representative": "house", "U.S. Senator": "senate", "Governor & Lt Governor": "governor"}
LA_BASE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob="
LA_RACE = re.compile(
    r"U\. S\. (?:(?P<senate>Senator)|Representative -- (?P<district>\d+)(?:st|nd|rd|th) Congressional District)"
    r"(?: -- (?P<party>\w+) Party)?"
)
NC_CONTEST = re.compile(r"US SENATE|NC GOVERNOR|US HOUSE OF REPRESENTATIVES DISTRICT (\d+)")
AR_CONTEST = re.compile(r"U\.S\. Senate|Governor|U\.S\. Congress District (\d+)")
AR_HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://arkansas.tally-enr.com"}
OK_BASE = "https://results.okelections.gov/OKERS/enrapi/"
OK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
OK_RACE = re.compile(r"FOR (GOVERNOR|UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE DISTRICT (\d+))")
PA_API = "https://www.electionreturns.pa.gov/api/ElectionReturn/"
PA_OFFICES = {"2": "senate", "3": "governor", "11": "house"}
NOTE = (
    "Unofficial election night results as the publisher's reporting site showed them at the time given. Counts change as "
    "ballots are counted and until the canvass certifies them; the certified canvass will publish as a separate result set."
)


def office_name(text: str) -> str:
    found = EV_MEMBER.fullmatch(text.strip())
    if found is None:
        return text.strip()
    return f"United States Representative {found[1]}" if found[1] else "United States Senator"


def person(label: str) -> str:
    words = [
        w for w in re.sub(r"[^a-z ]", " ", slug(label.replace("’", "'")).replace("-", " ")).split() if w not in SUFFIXES
    ]
    words = ["yes" if w in ("for", "yes") else "no" if w in ("against", "no") else w for w in words]
    return words[0][0] + words[-1] if len(words) > 1 else words[0] if words else ""


def get(client: httpx.Client, url: str, headers: dict[str, str] | None = None) -> bytes | None:
    for attempt in range(3):
        try:
            response = client.get(url, headers=headers)
        except httpx.TransportError:
            time.sleep(2 + attempt * 3)
            continue
        if response.status_code == 404:
            return None
        if response.status_code < 400:
            return response.content
        time.sleep(1 + attempt)
    return None


def fetch() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    pages: dict[str, dict[str, str]] = {}
    client = httpx.Client(follow_redirects=True, timeout=120, headers=HEADERS)

    def store(state: str, url: str, content: bytes, suffix: str) -> None:
        key = f"{url}@{stamp}"
        raw.store(
            SOURCE,
            key,
            content,
            suffix=suffix,
            retrieved_at=datetime.now(UTC),
            media_type="application/json" if suffix == ".json" else "text/tab-separated-values",
        )
        pages.setdefault(state, {})[url] = key

    for state, (host, jurisdiction) in EV.items():
        if ONLY and state not in ONLY:
            continue
        listing_url = f"https://{host}/results/public/api/jurisdictions/{jurisdiction}"
        content = get(client, listing_url)
        if content is None:
            print(f"{state}: no jurisdiction listing")
            continue
        store(state, listing_url, content, ".json")
        ids = [e["publicElectionId"] for e in json.loads(content)["elections"] if e["electionDate"] == DAY.isoformat()]
        if not ids:
            print(f"{state}: no election on {DAY}")
            continue
        base = f"https://{host}/results/public/api/elections/{jurisdiction}/{ids[0]}/"
        content = get(client, base + "ballot-items")
        if content is None:
            print(f"{state}: no ballot items")
            continue
        store(state, base + "ballot-items", content, ".json")
        for item in json.loads(content)["data"]:
            if EV_OFFICES.fullmatch(office_name(item["name"][0]["text"])):
                detail = get(client, f"{base}ballot-items/{item['id']}")
                if detail is not None:
                    store(state, f"{base}ballot-items/{item['id']}", detail, ".json")
    for state, base in CLARITY.items():
        if ONLY and state not in ONLY:
            continue
        listing = get(client, f"{base}elections.json")
        if listing is None:
            print(f"{state}: no election listing")
            continue
        store(state, f"{base}elections.json", listing, ".json")
        ids = (
            [os.environ[f"ENR_2026_{state}_EID"]]
            if f"ENR_2026_{state}_EID" in os.environ
            else [
                e["EID"]
                for e in json.loads(listing)
                if datetime.strptime(e["Date"].split()[0], "%m/%d/%Y").date() == DAY
                and not re.search(r"TEST|MOCK|TRAINING", e["ElectionName"].upper())
            ]
        )
        if not ids:
            print(f"{state}: no election on {DAY}")
            continue
        version = get(client, f"{base}{ids[0]}/current_ver.txt")
        content = version and get(client, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip")
        if content is None:
            print(f"{state}: no detail file")
        else:
            store(state, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip", content, ".zip")
    if not ONLY or "OK" in ONLY:
        login = client.put(
            f"{OK_BASE}login/",
            json={"Username": "appuser", "Password": "X098de!22k098Mgfd"},
            headers=OK_HEADERS,
        )
        auth = {**OK_HEADERS, "Authorization": f"Bearer {login.json()[1:]}"}
        listing = get(client, f"{OK_BASE}getelec/{DAY:%Y%m%d}/SW/xx", auth)
        races = json.loads(listing or "{}").get("races") or []
        if not races:
            print("OK: no races")
        for race in races:
            if OK_RACE.fullmatch(race["raceTitle"]):
                url = f"{OK_BASE}GetCntyResults/{DAY:%Y%m%d}/{race['raceID']}"
                content = get(client, url, auth)
                if content:
                    store(
                        "OK",
                        url,
                        json.dumps(
                            {
                                "race": race,
                                "lastUpdate": json.loads(listing)["lastUpdate"],
                                "results": json.loads(content),
                            }
                        ).encode(),
                        ".json",
                    )
    if not ONLY or "AR" in ONLY:
        url = "https://enr-results-api.totalresults.com/Election/GetElectionList?cid=arkansas"
        listing = get(client, url, AR_HEADERS)
        ids = [e["electionID"] for e in json.loads(listing or "[]") if e["electionDate"][:10] == DAY.isoformat()]
        content = ids and get(
            client, f"https://results.tally-enr.com/results/arkansas/{ids[0]}/FullDataFile.json", AR_HEADERS
        )
        if not content:
            print("AR: no data file")
        else:
            store("AR", url, listing, ".json")
            store("AR", f"https://results.tally-enr.com/results/arkansas/{ids[0]}/FullDataFile.json", content, ".json")
    missing: list[str] = []
    for fips, (tenant, *_) in COUNTIES.items():
        state = STATES[fips[:2]]
        if ONLY and state not in ONLY:
            continue
        base = f"https://results.enr.clarityelections.com/{state}/{tenant}/"
        listing = get(client, f"{base}elections.json")
        ids = (
            [os.environ[f"ENR_2026_EID_{state}_{tenant}"]]
            if f"ENR_2026_EID_{state}_{tenant}" in os.environ
            else [
                e["EID"]
                for e in json.loads(listing or "[]")
                if datetime.strptime(e["Date"].split()[0], "%m/%d/%Y").date() == DAY
                and not re.search(r"TEST|MOCK|TRAINING", e["ElectionName"].upper())
            ]
        )
        version = ids and get(client, f"{base}{ids[0]}/current_ver.txt")
        content = version and get(client, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip")
        if content:
            store(state, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip", content, ".zip")
        else:
            missing.append(f"{state}/{tenant}")
    if missing:
        print(f"county Clarity sites with no {DAY} file: {len(missing)}", ", ".join(missing[:12]))
    if not ONLY or "FL" in ONLY:
        official = "Y" if DAY < date.today() else "N"
        url = f"https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate={DAY.month}/{DAY.day}/{DAY.year}&OfficialResults={official}"
        content = get(client, url)
        if content is None:
            print("FL: no extract")
        else:
            store("FL", url, content, ".txt")
    if not ONLY or "IL" in ONLY:
        election = IL_ELECTIONS[DAY.year]
        base = f"https://elections.il.gov/Downloads/ElectionOperations/ElectionResults/ByOffice/{election}/{election}-"
        files = [
            f"150-UNITED%20STATES%20SENATOR-{DAY.year}GE",
            f"180-GOVERNOR%20AND%20LIEUTENANT%20GOVERNOR-{DAY.year}GE",
        ]
        for number in range(1, 18):
            ordinal = (
                f"{number}{'TH' if 10 <= number % 100 <= 20 else {1: 'ST', 2: 'ND', 3: 'RD'}.get(number % 10, 'TH')}"
            )
            files.append(f"{240 + 10 * number}-{ordinal}%20CONGRESS-{DAY.year}GE")
        for name in files:
            content = get(client, f"{base}{name}.csv")
            if content is None:
                print(f"IL: no file {name}")
            else:
                store("IL", f"{base}{name}.csv", content, ".csv")
    if not ONLY or "MN" in ONLY:
        base = f"https://electionresultsfiles.sos.mn.gov/{DAY:%Y%m%d}/"
        for names in (("USSenateCty.txt",), ("USHouseCty.txt",), ("GovernorCty.txt", "GovernorPct.txt")):
            for name in names:
                content = get(client, base + name)
                if content is not None:
                    store("MN", base + name, content, ".txt")
                    break
            else:
                print(f"MN: no file {names[0]}")
    if not ONLY or "LA" in ONLY:
        listing_url = f"{LA_BASE}{DAY:%Y%m%d}/RacesCandidates_Multiparish.htm"
        listing = get(client, listing_url)
        if listing is None:
            print("LA: no race listing")
        else:
            store("LA", listing_url, listing, ".json")
            for race in json.loads(listing)["Races"]["Race"]:
                if LA_RACE.fullmatch(race["SpecificTitle"].strip()):
                    url = f"{LA_BASE}{DAY:%Y%m%d}/csv/ByParish_{race['ID']}.csv"
                    content = get(client, url)
                    if content is None:
                        print(f"LA: no file for race {race['ID']}")
                    else:
                        store("LA", url, content, ".csv")
    if not ONLY or "KS" in ONLY:
        for name in ("national.html", "statewide.html"):
            url = f"https://www.sos.ks.gov/ent/{name}"
            content = get(client, url)
            if content is None or b"race-details" not in content:
                print(f"KS: no file {name}")
            else:
                store("KS", url, content, ".html")
    if not ONLY or "MD" in ONLY:
        base = f"https://elections.maryland.gov/elections/{DAY.year}/general_results/"
        for name in (
            f"gen_detail_results_{DAY.year}_1_1.html",
            *(f"gen_detail_results_{DAY.year}_4_{n}_District%20{n}.html" for n in range(1, 9)),
        ):
            content = get(client, base + name)
            if content is None or b"Vote for" not in content:
                print(f"MD: no file {name}")
            else:
                store("MD", base + name, content, ".html")
    if not ONLY or "NC" in ONLY:
        url = f"https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{DAY:%Y_%m_%d}/results_pct_{DAY:%Y%m%d}.zip"
        content = get(client, url)
        if content is None:
            print("NC: no zip")
        else:
            store("NC", url, content, ".zip")
    if not ONLY or "IN" in ONLY:
        url = f"https://enr.indianavoters.in.gov/archive/{DAY.year}General/data/OffCatC_1005_B.json"
        content = get(client, url)
        if content is None:
            print("IN: no house file")
        else:
            store("IN", url, content, ".json")
    if not ONLY or "PA" in ONLY:
        found = os.environ.get("ENR_2026_PA_ID")
        for election in [found] if found else range(113, 160):
            probe = get(
                client,
                f"{PA_API}GetOfficeData?officeId=11&methodName=GetOfficeData&electionid={election}&electiontype=G&isactive=0",
            )
            if probe and re.search(rf'"ElectionYear":\s*"{DAY.year}"', json.loads(probe) or ""):
                break
        else:
            election = None
            print("PA: no general election id")
        for office in PA_OFFICES if election else ():
            url = f"{PA_API}GetOfficeData?officeId={office}&methodName=GetOfficeData&electionid={election}&electiontype=G&isactive=0"
            content = get(client, url)
            if not content or content == b'""':
                continue
            store("PA", url, content, ".json")
            for district in re.findall(r"\$\$(\d+)\$\$", json.loads(content)):
                url = f"{PA_API}GetCountyBreak?officeId={office}&districtId={district}&methodName=GetCountyBreak&electionid={election}&electiontype=G&isactive=0"
                detail = get(client, url)
                if detail:
                    store("PA", url, detail, ".json")
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "pages.json").write_text(json.dumps(pages))
    print({state: len(urls) for state, urls in pages.items()})


def rows(state: str, url: str, path: Path):
    if url.endswith("detailxml.zip"):
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        stamped = re.fullmatch(r"(.+) ([ECMP])([SD])T", (root.findtext("Timestamp") or "").strip())
        tenant = re.search(r"/[A-Z]{2}/([A-Za-z_]+)/\d+/\d+/reports", url)
        as_of = (
            datetime.strptime(stamped[1], "%m/%d/%Y %I:%M:%S %p")
            .replace(
                tzinfo=timezone(timedelta(hours={"E": -5, "C": -6, "M": -7, "P": -8}[stamped[2]] + (stamped[3] == "D")))
            )
            .isoformat()
            if stamped
            else None
        )
        for contest in root.iter("Contest"):
            found = EV_OFFICES.fullmatch((contest.get("text") or "").strip())
            if tenant and found:
                continue
            for choice in contest.iter("Choice"):
                for kind in choice.iter("VoteType"):
                    for place in kind:
                        yield {
                            "office": "other"
                            if found is None
                            else "senate"
                            if found["senate"]
                            else "governor"
                            if found["governor"]
                            else "house",
                            "contest": (contest.get("text") or "").strip() if found is None else "",
                            "district": (found["district"] if found else None) or "",
                            "party": choice.get("party") or "",
                            "county": tenant[1].replace("_", " ")
                            if tenant
                            else (place.get("name") or "").removesuffix(" County"),
                            "candidate": re.sub(r"\s*(\(I\)|\*)$", "", choice.get("text") or "").strip(),
                            "write_in": "write-in" in (choice.get("text") or "").lower(),
                            "votes": int(place.get("votes") or 0),
                            "as_of": as_of,
                            "reporting": int(contest.get("precinctsReported") or 0),
                            "total": int(contest.get("precinctsParticipating") or 0),
                            "scope": (found["party0"] or found["party1"] or "") if found else "",
                        }
        return
    if state == "FL":
        seen = set()
        content = path.read_bytes()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1252")
        for row in csv.DictReader(text.splitlines(), delimiter="\t"):
            office = FL_OFFICES.get(row["RaceCode"])
            key = (
                row["RaceCode"],
                row["PartyCode"],
                row["Juris1num"],
                row["CountyCode"],
                row["CanNameLast"],
                row["CanNameFirst"],
                row["CanNameMiddle"],
            )
            if key in seen:
                continue
            seen.add(key)
            yield {
                "office": office or "other",
                "contest": ""
                if office
                else " ".join(part for part in (row["OfficeDesc"], row["Juris1num"], row["Juris2num"]) if part),
                "district": row["Juris1num"].lstrip("0"),
                "party": row["PartyName"],
                "county": row["CountyName"],
                "candidate": " ".join(
                    part
                    for part in (row["CanNameFirst"], row["CanNameMiddle"], row["CanNameLast"])
                    if part and part != "/"
                ),
                "write_in": row["PartyCode"] == "WRI",
                "votes": int(row["CanVotes"] or 0),
                "as_of": None,
                "reporting": int(row["PrecinctsReporting"] or 0),
                "total": int(row["Precincts"] or 0),
            }
        return
    if state == "IL":
        for row in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
            found = IL_CONTEST.fullmatch(row["ContestName"].strip())
            if found is None or row["EISCandidateID"] == "0":
                continue
            yield {
                "office": "senate" if "SENATOR" in row["ContestName"] else "governor" if found[1] is None else "house",
                "district": found[1] or "",
                "party": row["PartyName"].title(),
                "county": IL_CITIES.get(row["JurisName"], row["JurisName"].title()),
                "candidate": row["CandidateName"].strip(),
                "write_in": "write-in" in row["CandidateName"].lower(),
                "votes": int(row["VoteCount"] or 0),
                "as_of": None,
                "reporting": None,
                "total": None,
            }
        return
    if state == "LA":
        table = csv.reader(path.read_text(encoding="utf-8-sig").splitlines())
        header = next(table)
        for row in table:
            found = LA_RACE.fullmatch(row[0].strip())
            if found is None:
                continue
            for label, votes in zip(header[2:], row[2:]):
                named = re.fullmatch(r"(.*?)\s*\((\w+)\)", label.strip())
                yield {
                    "office": "senate" if found["senate"] else "house",
                    "district": found["district"] or "",
                    "party": named[2] if named else "",
                    "county": row[1],
                    "candidate": named[1] if named else label.strip(),
                    "write_in": "write-in" in label.lower(),
                    "votes": int(votes or 0),
                    "as_of": None,
                    "reporting": None,
                    "total": None,
                    "scope": found["party"] or "",
                }
        return
    if state == "MN":
        totals: dict[tuple[str, str, str, str, str], list[int]] = {}
        for line in path.read_text(encoding="latin-1").splitlines():
            cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
            found = MN_CONTEST.fullmatch(cells[4]) if len(cells) > 13 else None
            if found is None:
                continue
            key = (
                f"27{2 * int(cells[1]) - 1:03}",
                MN_OFFICES[found[1]],
                found[2] or "",
                cells[10],
                re.sub(r"\s+", " ", cells[7].split(" and ")[0]),
            )
            tally = totals.setdefault(key, [0, 0, 0])
            tally[0] += int(cells[13] or 0)
            if url.endswith("Cty.txt"):
                tally[1], tally[2] = int(cells[11] or 0), int(cells[12] or 0)
        for (county, office, district, party, candidate), (votes, reporting, total) in totals.items():
            yield {
                "office": office,
                "district": district,
                "party": party,
                "county": county,
                "candidate": candidate,
                "write_in": candidate.upper().startswith("WRITE-IN"),
                "votes": votes,
                "as_of": None,
                "reporting": reporting or None,
                "total": total or None,
            }
        return
    if state == "KS":
        text = path.read_text(encoding="utf-8")
        stamp = re.search(r"id = 'time-stamp' >\s*([^<]*?)\s*<", text)
        as_of = (
            datetime.strptime(stamp[1], "%m/%d/%Y %I:%M %p").replace(tzinfo=ZoneInfo("America/Chicago")).isoformat()
            if stamp
            else None
        )
        for block in text.split("<div class='race'>")[1:]:
            found = KS_CONTEST.fullmatch(html.unescape(re.search(r"<h3>(.*?)</h3>", block)[1]).strip())
            if found is None:
                continue
            counted = re.search(r"Precincts Reporting: (\d+) of (\d+)", block)
            for county in re.findall(r"<area [^>]*title=\"(.*?)\" />", block, re.DOTALL):
                tooltip = html.unescape(county)
                for label, votes in re.findall(
                    r"<td style='padding-right:15px'>(.*?)</td><td style='text-align:right'>([\d,]+)</td>", tooltip
                ):
                    named = re.fullmatch(r"([A-Z]{1,3})-(.*)", html.unescape(label).strip())
                    yield {
                        "office": "senate" if found["senate"] else "governor" if found["governor"] else "house",
                        "district": found["district"] or "",
                        "party": named[1] if named else "",
                        "county": re.search(r"<span class='county'>(.*?) County</span>", tooltip)[1],
                        "candidate": named[2] if named else html.unescape(label).strip(),
                        "write_in": False,
                        "votes": int(votes.replace(",", "")),
                        "as_of": as_of,
                        "reporting": int(counted[1]) if counted else None,
                        "total": int(counted[2]) if counted else None,
                        "scope": found["party"] or "",
                    }
        return
    if state == "MD":
        text = path.read_text(encoding="utf-8")
        office = re.search(r"<h2>\s*([^<]*?)\s*</h2>", text)[1]
        district = re.search(r"<h3>\s*District (\d+)", text)
        refreshed = re.search(r"Last refreshed: (\S+ \S+ [AP]M)", text)
        counted = re.search(r"\((\d+) of (\d+) election day precincts reported\)", text)
        as_of = (
            datetime.strptime(refreshed[1], "%m/%d/%Y %I:%M:%S %p")
            .replace(tzinfo=ZoneInfo("America/New_York"))
            .isoformat()
            if refreshed
            else None
        )
        for table in re.findall(r"<table.*?</table>", text, re.DOTALL):
            heads = []
            for head in re.findall(r"<th[^>]*scope=\"col\"[^>]*>(.*?)</th>", table, re.DOTALL)[1:]:
                parts = [
                    part for part in (re.sub(r"<[^>]*>", "", p).strip() for p in re.split(r"<br\s*/?>", head)) if part
                ]
                heads.append((parts[0].split(" and ")[0], parts[-1] if len(parts) > 1 else "", "(Write In)" in parts))
            for county_row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
                cells = [
                    re.sub(r"<[^>]*>", "", cell).strip()
                    for cell in re.findall(r"<td[^>]*>(.*?)</td>", county_row, re.DOTALL)
                ]
                if not cells or cells[0].startswith("Total"):
                    continue
                for (name, party, write_in), votes in zip(heads, cells[1:]):
                    yield {
                        "office": "governor" if office.startswith("Governor") else "house",
                        "district": district[1] if district else "",
                        "party": party,
                        "county": cells[0],
                        "candidate": name,
                        "write_in": write_in or name.startswith("Other Write-Ins"),
                        "votes": int(votes.replace(",", "")) if votes[:1].isdigit() else 0,
                        "as_of": as_of,
                        "reporting": int(counted[1]) if counted else None,
                        "total": int(counted[2]) if counted else None,
                    }
        return
    if state == "NC":
        totals: dict[tuple[str, str, str, str], int] = {}
        with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"), delimiter="\t"):
                key = (row["Contest Name"].strip(), row["County"], row["Choice"].strip(), row["Choice Party"].strip())
                totals[key] = totals.get(key, 0) + int(row["Total Votes"] or 0)
        for (contest, county, choice, party), votes in totals.items():
            found = NC_CONTEST.fullmatch(contest)
            yield {
                "office": "other"
                if found is None
                else "house"
                if found[1]
                else "senate"
                if contest == "US SENATE"
                else "governor",
                "contest": contest if found is None else "",
                "district": ((found[1] if found else None) or "").lstrip("0"),
                "party": party,
                "county": county.title(),
                "candidate": choice,
                "write_in": choice.lower().startswith("write-in"),
                "votes": votes,
                "as_of": None,
                "reporting": None,
                "total": None,
            }
        return
    if state == "IN":
        for region in json.loads(path.read_bytes().decode("utf-8-sig"))["Root"]["OfficeCategory"]["Regions"]["Region"]:
            races = region["Races"]["Race"]
            for race in races if isinstance(races, list) else [races]:
                for candidate in race["Candidates"]["Candidate"]:
                    yield {
                        "office": "house",
                        "district": region["MAP_JURISDICTION_NAME"],
                        "party": candidate["PARTY_ABBREV"],
                        "county": race["Jurisdiction"]["FIPS"],
                        "candidate": re.sub(r"\s*\(W/I\)$", "", candidate["NAME_ON_BALLOT"]),
                        "write_in": "(W/I)" in candidate["NAME_ON_BALLOT"],
                        "votes": int(candidate["TOTAL_VOTES"] or 0),
                        "as_of": None,
                        "reporting": None,
                        "total": None,
                    }
        return
    if state == "PA":
        found = re.search(r"officeId=(\d+)&districtId=\d+&methodName=GetCountyBreak", url)
        if found is None:
            return
        for district, counties in json.loads(json.loads(path.read_bytes()))["Election"].items():
            number = re.match(r"(\d+)", district)
            for county in counties:
                for name, candidates in county.items():
                    for candidate in candidates:
                        yield {
                            "office": PA_OFFICES[found[1]],
                            "district": number[1] if number else "",
                            "party": candidate["PartyName"],
                            "county": name.title(),
                            "candidate": candidate["CandidateName"].strip(),
                            "write_in": "write-in" in candidate["CandidateName"].lower(),
                            "votes": int(candidate["Votes"] or 0),
                            "as_of": None,
                            "reporting": None,
                            "total": None,
                        }
        return
    if state == "OK":
        stored = json.loads(path.read_bytes())
        race = stored["race"]
        found = OK_RACE.fullmatch(race["raceTitle"])
        for county in stored["results"]["summaryCandResults"]:
            for candidate, result in zip(race["raceCandidates"], county["candResults"], strict=True):
                yield {
                    "office": "senate"
                    if found[1] == "UNITED STATES SENATOR"
                    else "governor"
                    if found[1] == "GOVERNOR"
                    else "house",
                    "district": (found[2] or "").lstrip("0"),
                    "party": race["raceTitle2"],
                    "county": f"40{2 * int(county['countyID']) - 1:03}",
                    "candidate": candidate["candName"].title(),
                    "write_in": False,
                    "votes": int(result["totalVotes"]),
                    "as_of": datetime.fromisoformat(stored["lastUpdate"])
                    .replace(tzinfo=ZoneInfo("America/Chicago"))
                    .isoformat(),
                    "reporting": None,
                    "total": None,
                    "scope": race["raceTitle2"]
                    if race["raceTitle2"] in ("Republican", "Democrat", "Libertarian")
                    else "",
                }
        return
    if state == "AR":
        for contest in json.loads(path.read_bytes())["ContestData"]:
            found = AR_CONTEST.fullmatch(contest["ContestName"])
            for fips, location in contest["Locations"].items():
                for choice in [*location["Choices"], *location["WriteinChoices"]]:
                    named = re.fullmatch(r"([A-Z]{3}) (.+)", choice["Name"])
                    yield {
                        "office": "other"
                        if found is None
                        else "senate"
                        if contest["ContestName"] == "U.S. Senate"
                        else "governor"
                        if found[1] is None
                        else "house",
                        "contest": contest["ContestName"] if found is None else "",
                        "district": ((found[1] if found else None) or "").lstrip("0"),
                        "party": named[1] if named else "",
                        "county": fips,
                        "candidate": named[2] if named else choice["Name"],
                        "write_in": "write-in" in choice["Name"].lower(),
                        "votes": choice["TotalVotes"]
                        if "TotalVotes" in choice
                        else sum(vote["Votes"] for vote in choice["Votes"]),
                        "as_of": None,
                        "reporting": None,
                        "total": None,
                    }
        return
    detail = json.loads(path.read_bytes())
    found = EV_OFFICES.fullmatch(office_name(detail["name"][0]["text"]))
    if found is None or "breakdownResults" not in detail:
        return
    status = detail.get("reportingStatus") or {}
    as_of = status.get("asOf")
    for locality in detail["breakdownResults"]:
        place = locality["locality"]["name"][0]["text"]
        county = (
            town_counties("RI").get(plain(re.sub(r"\s+(city|town)$", "", place.lower())), place)
            if state == "RI"
            else f"51{locality['locality']['mapFeatureId']}"
            if state == "VA"
            else place.removesuffix(" County")
        )
        for option in locality["ballotOptions"]:
            yield {
                "office": "senate" if found["senate"] else "governor" if found["governor"] else "house",
                "district": found["district"] or "",
                "party": (((option["party"] or {}).get("name") or [{}])[0].get("text") or "").strip("()"),
                "county": county,
                "candidate": re.sub(r"\s*(\(I\)|\*)$", "", option["name"][0]["text"]).strip(),
                "write_in": bool(option["isWriteIn"]),
                "votes": int(option["voteCount"] or 0),
                "as_of": as_of,
                "reporting": status.get("reportingUnits"),
                "total": status.get("totalUnits"),
                "scope": found["party0"] or found["party1"] or "",
            }


def parse() -> None:
    pages = json.loads((PARSED / "pages.json").read_text())
    table = []
    for state, urls in pages.items():
        for url, key in urls.items():
            if url.endswith(LISTINGS):
                continue
            for row in rows(state, url, raw.cached(SOURCE, key).path):
                table.append({"state": state, "page": url, "scope": "", "contest": "", **row})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        table,
        schema={
            "state": pl.String,
            "page": pl.String,
            "scope": pl.String,
            "contest": pl.String,
            "office": pl.String,
            "district": pl.String,
            "party": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "write_in": pl.Boolean,
            "votes": pl.Int64,
            "as_of": pl.String,
            "reporting": pl.Int64,
            "total": pl.Int64,
        },
    )
    frame.write_parquet(PARSED / "results.parquet")
    print(frame.group_by("state").agg(pl.len(), pl.col("votes").sum()).sort("state"))


def emit():
    frame = pl.read_parquet(PARSED / "results.parquet").filter(pl.col("scope") == "")
    local = frame.filter(pl.col("office") == "other")
    frame = frame.filter(pl.col("office") != "other")
    pages = json.loads((PARSED / "pages.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Unofficial election night results of {DAY} for U.S. Senate, U.S. House, and governor by county, from the states' reporting sites",
            polity="US",
        )
        counties: dict[tuple[str, str], UUID] = {}
        codes: dict[tuple[str, str], str] = {}
        for code, text, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ %s order by u.code""",
            [f"^({'|'.join(FIPS.values())})"],
        ).fetchall():
            counties.setdefault((code[:2], plain(text)), id)
            counties[(code[:2], code)] = id
            codes.setdefault((code[:2], plain(text)), code)
        unmatched: list[str] = []
        sources: dict[str, UUID] = {}
        work: list[tuple[str, str, UUID, pl.DataFrame, UUID | None]] = []
        for (state, office, district), group in frame.group_by("state", "office", "district", maintain_order=True):
            contest_key = f"{DAY}:{state}:{office}" + (f":{district}" if district else "")
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.contest' and value = %s limit 1", [contest_key]
            ).fetchone()
            if found is None:
                unmatched.append(f"{contest_key}: no contest")
                continue
            work.append((state, contest_key, found[0], group, None))
        for state in local["state"].unique(maintain_order=True):
            published: dict[UUID, tuple[str, set[str]]] = {}
            titles: dict[UUID, set[str]] = {}
            for key, id in conn.execute(
                """select x.value, x.entity_id from external_id x
                   where x.scheme = 'us.contest' and x.value like %s""",
                [f"{DAY}:{state}:%"],
            ).fetchall():
                if not re.fullmatch(rf"{DAY}:{state}:(house:\d+|senate|governor)", key):
                    published[id] = (key, set())
            for contest, label in conn.execute(
                """select o.contest_id, coalesce(ca.ballot_name, o.ballot_label, '') from contest_option o
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.parent_option_id is null and o.kind <> 'write_in' and o.contest_id = any(%s)""",
                [list(published)],
            ).fetchall():
                published[contest][1].add(person(label))
            for contest, text in conn.execute(
                "select entity_id, text from name where entity_id = any(%s)", [list(published)]
            ).fetchall():
                titles.setdefault(contest, set()).update(slug(text).split("-"))
            towns: dict[str, set[str]] = {}
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id
                   where u.scheme_code = 'us.place' and u.code = any(%s)""",
                [list({key.split(":")[2] for key, _ in published.values()})],
            ).fetchall():
                towns.setdefault(code, set()).update(w for w in slug(text).split("-") if len(w) > 2 and w not in STOP)
            races: dict[tuple[str, frozenset[str]], list[str]] = {}
            for (name, county), group in local.filter(pl.col("state") == state).group_by("contest", "county"):
                people = frozenset(
                    person(candidate) for candidate in group.filter(~pl.col("write_in"))["candidate"].unique()
                )
                races.setdefault((name, people), []).append(county)
            matched = 0
            picks = []
            for (name, people), places in races.items():
                words = {
                    re.sub(r"^0+(?=\d)|(?<=\d)(st|nd|rd|th)$", "", w) for w in slug(name).split("-") if w not in STOP
                }
                scored = []
                for contest, (key, wanted) in published.items():
                    segment = key.split(":")[2]
                    if re.fullmatch(r"\d{5}", segment) and (
                        len(places) != 1 or codes.get((FIPS[state], plain(places[0]))) != segment
                    ):
                        continue
                    if len(places) == 1 and not re.match(r"\d|sd|house$|senate$", segment):
                        continue
                    if people <= {"yes", "no"} and segment in towns and not towns[segment] & words:
                        continue
                    overlap = len(people & wanted) / len(people | wanted) if people | wanted else 0
                    if overlap >= 0.6:
                        scored.append(
                            (
                                round(overlap, 2),
                                len(words & (titles.get(contest, set()) | set(re.split(r"[:-]", key)[2:]))),
                                contest,
                                key,
                            )
                        )
                scored.sort(key=lambda hit: (-hit[0], -hit[1]))
                best = [hit for hit in scored if hit[:2] == scored[0][:2]] if scored else []
                if len(best) != 1 or ((people <= {"yes", "no"} or len(people) < 2) and best[0][1] < 1):
                    unmatched.append(
                        f"local {state}: {name} ({', '.join(places[:2])}): {'ambiguous' if best else 'no contest'}"
                    )
                    continue
                picks.append((best[0], name, places))
            for (_, _, contest, key), name, places in sorted(picks, key=lambda pick: (-pick[0][0], -pick[0][1])):
                if any(entry[1] == key for entry in work):
                    unmatched.append(f"local {state}: {name} ({', '.join(places[:2])}): duplicate of {key}")
                    continue
                segment = key.split(":")[2]
                place = None
                if not re.fullmatch(r"\d{5}", segment) and re.match(r"\d|sd", segment):
                    found = conn.execute(
                        """select u.id from reporting_unit u
                           where u.area_id = (select district_area_id from contest where id = %s)
                              or u.scheme_code = 'us.place' and u.code = %s
                           order by u.scheme_code = 'us.place' and u.code = %s, u.scheme_code limit 1""",
                        [contest, segment, segment],
                    ).fetchone()
                    if found is None:
                        unmatched.append(f"local {state}: {name}: no unit for {key}")
                        continue
                    place = found[0]
                matched += 1
                work.append(
                    (
                        state,
                        key,
                        contest,
                        local.filter(
                            (pl.col("state") == state) & (pl.col("contest") == name) & pl.col("county").is_in(places)
                        ),
                        place,
                    )
                )
            print(f"local {state}: {matched} of {len(races)} feed races matched")
        for state, contest_key, contest, group, place in work:
            election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            if state not in sources:
                publisher, site, _ = OFFICIAL[state]
                title = f"Unofficial Election Night Results {DAY}: {state}"
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=site,
                        license="Public record",
                    )
                for key in pages[state].values():
                    cs.artifact(raw.cached(SOURCE, key), sources[state])
            options = conn.execute(
                """select o.id, o.kind, coalesce(ca.ballot_name, o.ballot_label, '') from contest_option o
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            named: list[tuple[UUID, list[str]]] = []
            scattered = None
            for option, kind, label in options:
                if kind == "write_in":
                    scattered = option
                else:
                    named.append(
                        (
                            option,
                            [
                                YESNO.get(w, w)
                                for w in re.sub(
                                    r"[^a-z ]", " ", slug(label.replace("’", "'")).replace("-", " ")
                                ).split()
                                if w not in SUFFIXES
                            ],
                        )
                    )
            ballots: dict[str, UUID] = {}
            for name, write_in in group.select("candidate", "write_in").unique().iter_rows():
                if write_in or re.search(r"write-?in|scattering|\(W\)$", name, re.IGNORECASE):
                    if scattered is None:
                        scattered = cs.key("us.option", f"{contest_key}:write_in:scattered")
                        if scattered in cs.minted:
                            cs.add(
                                "contest_option",
                                id=scattered,
                                contest_id=contest,
                                kind="write_in",
                                ballot_label="Scattered",
                            )
                    ballots[name] = scattered
                    continue
                tokens = {
                    YESNO.get(w, w)
                    for w in re.sub(r"[^a-z ]", " ", slug(name.replace("’", "'")).replace("-", " ")).split()
                    if w not in SUFFIXES
                }
                hits = sorted(
                    ((len(tokens & set(words)), option) for option, words in named if words and words[-1] in tokens),
                    key=lambda hit: -hit[0],
                )
                if hits and (len(hits) == 1 or hits[0][0] > hits[1][0]):
                    ballots[name] = hits[0][1]
                else:
                    unmatched.append(f"{contest_key}: no option for {name}")
            reporting = (
                group.group_by("county")
                .agg(pl.col("reporting").max(), pl.col("total").max())
                .select(pl.col("reporting").sum(), pl.col("total").sum())
                .row(0)
                if state == "FL"
                else group.select(pl.col("reporting").max(), pl.col("total").max()).row(0)
            )
            stamp = group["as_of"].drop_nulls().max()
            as_of = (
                datetime.fromisoformat(str(stamp))
                if stamp
                else datetime.strptime(next(iter(pages[state].values())).rsplit("@", 1)[1], "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=UTC
                )
            )
            sums: dict[tuple[UUID, UUID], int] = {}
            for county, name, votes in group.select("county", "candidate", "votes").iter_rows():
                option = ballots.get(name)
                unit = place or (
                    counties.get((FIPS[state], plain(ALIASES.get((state, plain(county)), county))))
                    or counties.get((FIPS[state], plain(county)))
                    or counties.get((FIPS[state], county))
                )
                if option is None:
                    continue
                if unit is None:
                    unmatched.append(f"{contest_key}: no county {county}")
                    continue
                sums[(unit, option)] = sums.get((unit, option), 0) + votes
            frames = conn.execute(
                """select r.id, r.k, r.is_default, r.as_of, r.completeness from result_set r
                   where r.contest_id = %s and r.source_id = %s and r.stage = 'preliminary' and r.status = 'active'
                   order by r.as_of desc""",
                [contest, sources[state]],
            ).fetchall()
            if not any(sums.values()) or (
                frames
                and (
                    frames[0][3] > as_of
                    or {
                        (unit, option): int(value)
                        for unit, option, value in conn.execute(
                            """select u.id, o.id, v.value from result_value v join reporting_unit u on u.k = v.unit_k
                           join contest_option o on o.k = v.option_k where v.result_set_k = %s and v.measure = 1""",
                            [frames[0][1]],
                        ).fetchall()
                    }
                    == sums
                )
            ):
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}@{as_of.astimezone(UTC):%Y%m%dT%H%M%SZ}")
            is_default = (
                conn.execute(
                    "select 1 from result_set where contest_id = %s and is_default and source_id <> %s limit 1",
                    [contest, sources[state]],
                ).fetchone()
                is None
            )
            if is_default:
                for frame, _, was_default, frame_as_of, completeness in frames:
                    if was_default and frame != result_set:
                        cs.add(
                            "result_set",
                            id=frame,
                            election_id=election,
                            contest_id=contest,
                            stage="preliminary",
                            kind="counted",
                            status="active",
                            source_id=sources[state],
                            as_of=frame_as_of,
                            is_default=False,
                            completeness=completeness,
                        )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=sources[state],
                as_of=as_of,
                is_default=is_default,
                completeness=reporting[0] / reporting[1] if reporting[1] else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            for (unit, option), votes in sums.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if reporting[1]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{reporting[0]} of {reporting[1]} reporting units had reported.",
                )
        for line in sorted(set(unmatched)):
            print("unmatched:", line)
        return cs.write()
