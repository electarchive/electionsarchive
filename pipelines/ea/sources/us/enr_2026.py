import csv
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
}
CLARITY = {
    "IA": "https://electionresults.iowa.gov/IA/",
    "SC": "https://www.enr-scvotes.org/SC/",
    "WV": "https://results.enr.clarityelections.com/WV/",
}
EV_OFFICES = re.compile(
    r"^(?:(?P<party0>REP|DEM) )?(?:(?P<senate>(?:U\.? ?S\.? |United States )Senat(?:e|or)|Senator in Congress)"
    r"|(?P<governor>Governor)"
    r"|(?:U\.? ?S\.? (?:House|Representative)s?|United States Representative|Representative in Congress)\b[^\d]*(?P<district>\d+)(?:st|nd|rd|th)?(?:\s+Congressional)?(?:\s+District)?)"
    r"\s*(?:-\s*(?P<party1>Rep|Dem|Democratic|Republican|Libertarian))?\s*$",
    re.IGNORECASE,
)
LISTINGS = ("elections.json", "cid=arkansas", "/ballot-items", "Georgia", "/id", "/utah", "/RhodeIsland", "/washington")
SUFFIXES = ("jr", "sr", "ii", "iii", "iv")
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
            if EV_OFFICES.fullmatch(item["name"][0]["text"].strip()):
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
            if office is None or key in seen:
                continue
            seen.add(key)
            yield {
                "office": office,
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
    if state == "NC":
        with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"), delimiter="\t"):
                found = NC_CONTEST.fullmatch(row["Contest Name"].strip())
                if found is None:
                    continue
                yield {
                    "office": "house" if found[1] else "senate" if row["Contest Name"] == "US SENATE" else "governor",
                    "district": (found[1] or "").lstrip("0"),
                    "party": row["Choice Party"].strip(),
                    "county": row["County"].title(),
                    "candidate": row["Choice"].strip(),
                    "write_in": row["Choice"].lower().startswith("write-in"),
                    "votes": int(row["Total Votes"] or 0),
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
            if found is None:
                continue
            for fips, location in contest["Locations"].items():
                for choice in [*location["Choices"], *location["WriteinChoices"]]:
                    named = re.fullmatch(r"([A-Z]{3}) (.+)", choice["Name"])
                    yield {
                        "office": "senate"
                        if contest["ContestName"] == "U.S. Senate"
                        else "governor"
                        if found[1] is None
                        else "house",
                        "district": (found[1] or "").lstrip("0"),
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
    if state in CLARITY:
        if not url.endswith("detailxml.zip"):
            return
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        stamped = re.fullmatch(r"(.+) ([ECMP])([SD])T", (root.findtext("Timestamp") or "").strip())
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
            if found is None:
                continue
            for choice in contest.iter("Choice"):
                for kind in choice.iter("VoteType"):
                    for place in kind:
                        yield {
                            "office": "senate" if found["senate"] else "governor" if found["governor"] else "house",
                            "district": found["district"] or "",
                            "party": choice.get("party") or "",
                            "county": (place.get("name") or "").removesuffix(" County"),
                            "candidate": re.sub(r"\s*(\(I\)|\*)$", "", choice.get("text") or "").strip(),
                            "write_in": "write-in" in (choice.get("text") or "").lower(),
                            "votes": int(place.get("votes") or 0),
                            "as_of": as_of,
                            "reporting": int(contest.get("precinctsReported") or 0),
                            "total": int(contest.get("precinctsParticipating") or 0),
                            "scope": found["party0"] or found["party1"] or "",
                        }
        return
    detail = json.loads(path.read_bytes())
    found = EV_OFFICES.fullmatch(detail["name"][0]["text"].strip())
    if found is None or "breakdownResults" not in detail:
        return
    status = detail.get("reportingStatus") or {}
    as_of = status.get("asOf")
    for locality in detail["breakdownResults"]:
        place = locality["locality"]["name"][0]["text"]
        county = (
            town_counties("RI").get(plain(re.sub(r"\s+(city|town)$", "", place.lower())), place)
            if state == "RI"
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
                table.append({"state": state, "page": url, "scope": "", **row})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        table,
        schema={
            "state": pl.String,
            "page": pl.String,
            "scope": pl.String,
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
        for code, text, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ %s order by u.code""",
            [f"^({'|'.join(FIPS.values())})"],
        ).fetchall():
            counties.setdefault((code[:2], plain(text)), id)
            counties[(code[:2], code)] = id
        unmatched: list[str] = []
        sources: dict[str, UUID] = {}
        for (state, office, district), group in frame.group_by("state", "office", "district", maintain_order=True):
            contest_key = f"{DAY}:{state}:{office}" + (f":{district}" if district else "")
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.contest' and value = %s limit 1", [contest_key]
            ).fetchone()
            if found is None:
                unmatched.append(f"{contest_key}: no contest")
                continue
            contest = found[0]
            election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
                                w
                                for w in re.sub(r"[^a-z ]", " ", slug(label).replace("-", " ")).split()
                                if w not in SUFFIXES
                            ],
                        )
                    )
            ballots: dict[str, UUID] = {}
            for name, write_in in group.select("candidate", "write_in").unique().iter_rows():
                if write_in or re.search(r"write-?in|scattering", name, re.IGNORECASE):
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
                tokens = {w for w in re.sub(r"[^a-z ]", " ", slug(name).replace("-", " ")).split() if w not in SUFFIXES}
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
            as_of = group["as_of"].drop_nulls().max()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=sources[state],
                as_of=datetime.fromisoformat(as_of) if as_of else datetime.now(UTC),
                is_default=conn.execute(
                    "select 1 from result_set where contest_id = %s and is_default and id <> %s limit 1",
                    [contest, result_set],
                ).fetchone()
                is None,
                completeness=reporting[0] / reporting[1] if reporting[1] else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            sums: dict[tuple[UUID, UUID], int] = {}
            for county, name, votes in group.select("county", "candidate", "votes").iter_rows():
                option = ballots.get(name)
                unit = (
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
