import csv
import io
import json
import re
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from xml.etree import ElementTree

import httpx
import polars as pl
import shapefile

from ea import portals, raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.enr_clarity"
VERSION = "1"
HOST = "results.enr.clarityelections.com"
CENSUS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{fips}_{layer}_500k.zip"
LAYERS = ("place", "cousub", "unsd")
TITLE = "Election night reporting results"
DAY_FIXES = {("https://results.enr.clarityelections.com/IL/McHenry/", "109015"): date(2021, 4, 6)}
STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
NUMBERS = {
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "SIX": "6",
    "SEVEN": "7",
    "EIGHT": "8",
    "NINE": "9",
    "TEN": "10",
}
NUMBER = r"(\d+[A-Z]?|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|[A-Z])\b"
PARTIES = {
    "R": "republican",
    "REP": "republican",
    "REPUBLICAN": "republican",
    "D": "democratic",
    "DEM": "democratic",
    "DEMOCRAT": "democratic",
    "DEMOCRATIC": "democratic",
}
SKIPPED = re.compile(
    r"\bPRESIDENT\b(?! OF (?:THE )?(?:COUNCIL|BOARD))|UNITED STATES|\bU ?S\b|CONGRESS|GOVERNOR|ATTORNEY GENERAL|"
    r"COMPTROLLER OF|LAND OFFICE|GENERAL LAND|AGRICULTURE|RAILROAD|SUPREME|APPEALS?\b|\bAPP\b|\bCT OF\b|"
    r"SUPERIOR COURT|COMMONWEALTH COURT|SENATOR|STATE SENAT|REPRESENTATIVE|GENERAL ASSEMBLY|STATE BOARD|\bS?BOE\b|"
    r"STATE PROP|STATE REP\b|STATE LEGISLATURE|\bCIRCUIT\b|JUDGE OF DISTRICT COURT|STATE OF TEXAS|CONSTITUTIONAL|AMENDMENT|STRAIGHT|SECRETARY OF|AUDITOR GENERAL|STATE TREASURER|"
    r"(?<!MAGISTERIAL )\bDIST(?:RICT)? (?:CT )?JUDGE|JUDIC(?:IAL)? DIST|RETENTION|RETAIN|DELEGATE|NATIONAL COMMITTEE|"
    r"STATE CONVENTION|REFERENDA|REFERENDUM ITEM|BALLOTS CAST|REGISTERED VOTERS|TURNOUT|STATEWIDE|\bTEST\b"
)
QUESTION = re.compile(r"(?i)\s*(?:yes|no|for|against|si|bonds? yes|bonds? no)\b")
WRITE_IN = re.compile(r"(?i)\s*(?:unresolved |unassigned |qualified |uncertified )?write|scatter|personal choice")
VACANT = re.compile(r"(?i)\s*(?:no (?:nomination|candidate)|over ?votes|under ?votes|blank|unopposed)")
REMAINDER = re.compile(r"\bUNEX\w*|\bUNXP\w*|SHORT TERM|VACANCY|REMAINDER|PARTIAL TERM|TO FILL\b|\bFILL\b")
TERM = re.compile(r"\b(\d+|ONE|TWO|THREE|FOUR|FIVE|SIX)[ -]?(?:YR|YEAR)S?\b(?: TERM)?")
PARTY_OFFICES = (
    (
        r"(?:PRECINCT|PCT) (?:COMMITTEE )?CHAIR\w*|CHAIR\w*,? (?=(?:PRECINCT|PCT)\b)",
        "precinct-chair",
        "Precinct Chair",
    ),
    (r"COUNTY CHAIR\w*|COUNTY PARTY CHAIR\w*", "county-chair", "County Chair"),
    (r"(?:MEMBER OF )?STATE COMMITTEE\w*", "state-committee", "State Committee Member"),
    (r"COMMITTEE ?MAN|COMMITTEEMEN", "committeeman", "Committeeman"),
    (r"COMMITTEE ?WOMAN|COMMITTEEWOMEN", "committeewoman", "Committeewoman"),
    (r"(?:COUNTY )?COMMITTEE ?(?:PERSON|PEOPLE|MEMBER)S?|COUNTY COMMITTEE", "committee-person", "Committee Person"),
)
OFFICERS = (
    (r"JUDGE OF ELECTIONS?|ELECTION JUDGE", "judge-of-elections", "Judge of Elections"),
    (
        r"(?:MAJORITY |MINORITY )?INSPECTOR OF ELECTIONS?|(?:MAJORITY |MINORITY )INSPECTOR",
        "inspector-of-elections",
        "Inspector of Elections",
    ),
)
COUNTY_OFFICES = (
    (r"SHERIFF", "sheriff", "Sheriff", False),
    (r"STATE ?S ATTORNEY", "state-s-attorney", "State's Attorney", False),
    (r"CIRCUIT CLERK", "circuit-clerk", "Circuit Clerk", False),
    (r"COUNTY BOARD(?: MEMBER)?", "board-of-commissioners", "Board of Commissioners", True),
    (
        r"REGIONAL SUPERINTENDENT(?: OF SCHOOLS)?",
        "regional-superintendent",
        "Regional Superintendent of Schools",
        False,
    ),
    (r"(?:COUNTY )?PROSECUTING ATTORNEY", "prosecuting-attorney", "Prosecuting Attorney", False),
    (r"(?:COUNTY )?DRAIN COMMISSIONER", "drain-commissioner", "Drain Commissioner", False),
    (r"(?:COUNTY )?ROAD COMMISSIONER", "road-commissioner", "Road Commissioner", False),
    (r"(?:COUNTY )?REGISTER OF DEEDS", "register-of-deeds", "Register of Deeds", False),
    (r"JUDGE OF PROBATE(?: COURT)?", "probate-court", "Judge, Probate Court", False),
    (r"COUNTY JUDGE", "county-judge", "County Judge", False),
    (r"COUNTY CRIMINAL (?:COURT|CT)(?:[ -]AT[ -]LAW)?", "county-criminal-court", "Judge, County Criminal Court", False),
    (r"(?:COUNTY )?(?:COURT|CT)\.?[ -]AT[ -]LAW", "county-court-at-law", "Judge, County Court at Law", False),
    (r"PROBATE (?:COURT|CT)", "probate-court", "Judge, Probate Court", False),
    (r"JUSTICE OF (?:THE )?PEACE|\bJ ?O ?P\b|\bJ ?P\b", "justice-of-the-peace", "Justice of the Peace", False),
    (r"CRIMINAL DISTRICT ATTORNEY", "criminal-district-attorney", "Criminal District Attorney", False),
    (r"DISTRICT ATTORNEY|\bD ?A\b", "district-attorney", "District Attorney", False),
    (r"COUNTY ATTORNEY", "county-attorney", "County Attorney", False),
    (r"COUNTY (?:AND|&) DISTRICT CLERK", "county-and-district-clerk", "County and District Clerk", False),
    (r"DISTRICT CLERK", "district-clerk", "District Clerk", False),
    (r"COUNTY CLERK", "clerk", "County Clerk", False),
    (
        r"(?:COUNTY )?TAX ASSESSOR[- ]?COLLECTOR|ASSESSOR[- ]COLLECTOR",
        "tax-assessor-collector",
        "Tax Assessor-Collector",
        False,
    ),
    (r"COUNTY SURVEYOR|SURVEYOR", "surveyor", "Surveyor", False),
    (r"PROTHONOTARY", "prothonotary", "Prothonotary", False),
    (r"REGISTER (?:AND|&) RECORDER", "register-and-recorder", "Register and Recorder", False),
    (r"REGISTER OF WILLS", "register-of-wills", "Register of Wills", False),
    (r"RECORDER OF DEEDS", "recorder-of-deeds", "Recorder of Deeds", False),
    (r"CLERK OF (?:THE )?ORPHANS'? ?(?:COURT)?", "clerk-of-orphans-court", "Clerk of the Orphans' Court", False),
    (r"CLERK OF (?:THE )?COURTS?", "clerk-of-courts", "Clerk of Courts", False),
    (r"JURY COMMISSIONERS?", "jury-commissioners", "Jury Commissioners", True),
    (r"CORONER", "coroner", "Coroner", False),
    (
        r"(?:JUDGE OF (?:THE )?)?COURT OF COMMON PLEAS|COMMON PLEAS",
        "court-of-common-pleas",
        "Court of Common Pleas",
        True,
    ),
    (r"MAGISTERIAL DISTRICT JUDGE|DISTRICT JUSTICE", "magisterial-district-judge", "Magisterial District Judge", False),
    (r"COUNTY EXECUTIVE", "county-executive", "County Executive", False),
    (r"COUNTY COUNCIL", "council", "County Council", True),
    (r"COUNTY (?:COMMISSIONERS?|COMM)|COMMISSIONERS? (?:PCT|PRECINCT)|\bCOMM PCT\b", "commissioners", "", True),
    (r"COUNTY TREASURER", "treasurer", "Treasurer", False),
    (r"COUNTY CONTROLLER|COUNTY COMPTROLLER", "controller", "Controller", False),
    (r"COUNTY AUDITORS?", "auditors", "Auditors", True),
)
LOOSE = (
    (r"CONSTABLE", "constable", "Constable", False),
    (r"TREASURER", "treasurer", "Treasurer", False),
    (r"CONTROLLER", "controller", "Controller", False),
    (r"COMMISSIONERS?", "commissioners", "", True),
    (r"AUDITORS?", "auditors", "Auditors", True),
)
MI_MUNI_OFFICES = (
    (r"HIGHWAY COMMISSIONER", "highway-commissioner", "Highway Commissioner", False),
    (r"SUPERVISOR", "supervisor", "Supervisor", False),
    (r"CLERK", "clerk", "Clerk", False),
    (r"(?<!COUNCIL )PRESIDENT", "president", "President", False),
)
MUNI_OFFICES = (
    (r"COUNCIL PRES\w*|PRESIDENT OF (?:THE )?COUNCIL", "council-president", "Council President", False),
    (r"MAYOR", "mayor", "Mayor", False),
    (r"SUPERVISORS?", "board-of-supervisors", "Board of Supervisors", True),
    (r"AUDITORS?", "auditors", "Auditors", True),
    (r"(?:TAX )?COLLECTOR", "collector", "Tax Collector", False),
    (r"TREASURER", "treasurer", "Treasurer", False),
    (r"CONTROLLER", "controller", "Controller", False),
    (r"CONSTABLE", "constable", "Constable", False),
    (r"ASSESSOR", "assessor", "Assessor", False),
    (r"MARSHAL", "marshal", "Marshal", False),
    (r"MUNICIPAL (?:COURT|CRT|CT|JUDGE)", "municipal-court", "Judge, Municipal Court", False),
    (r"TRUSTEES?", "board-of-trustees", "Board of Trustees", True),
    (
        (
            r"ALDER\w*|(?:CITY |TOWN |BOROUGH )?COUNCIL ?(?:MEMBERS?|MAN|WOMAN|PERSON)?|COUNCILMEMBERS?|COMMISSIONERS?|REP|"
            r"MEMBERS? OF (?:THE )?(?:CITY |TOWN |BOROUGH )?COUNCIL"
        ),
        "council",
        "Council",
        True,
    ),
)
NY_COUNTY_OFFICES = (
    (r"COUNTY LEG\w*\b|LEGISLATOR|LEGISLATIVE", "legislature", "County Legislature", True),
    (r"FAMILY COURT(?: JUDGE)?", "family-court", "Judge, Family Court", False),
    (r"SURROGATE(?:S|'S)?(?: COURT)?(?: JUDGE)?", "surrogate", "Surrogate", False),
)
NY_MUNI_OFFICES = (
    (r"(?:TOWN |VILLAGE )?JUSTICE", "justice", "Justice", False),
    (
        r"(?:CHIEF )?(?:JUDGE OF THE )?(?:CITY )?COURT JUDGE|CHIEF JUDGE OF THE (?:CITY )?COURT|CITY JUDGE|CITY COURT",
        "city-court",
        "Judge, City Court",
        False,
    ),
    (
        r"(?:TOWN )?SUPT\.? OF HIGHWAYS?|(?:TOWN )?SUPERINTENDENT OF HIGHWAYS?|HIGHWAY SUPERINTENDENT|HIGHWAY SUPT",
        "highway-superintendent",
        "Highway Superintendent",
        False,
    ),
    (r"RECEIVER OF TAXES(?: (?:AND|&) ASSESSMENTS)?", "receiver-of-taxes", "Receiver of Taxes", False),
    (r"(?:TOWN |VILLAGE |CITY )?CLERK(?: (?:AND|&) (?:TAX )?COLLECTOR)?|CLERK", "clerk", "Clerk", False),
    (r"(?:TAX )?COLLECTOR", "collector", "Tax Collector", False),
    (r"ASSESSOR", "assessor", "Assessor", False),
    (r"(?:TOWN )?SUPERVISOR", "supervisor", "Supervisor", False),
    (r"MAYOR", "mayor", "Mayor", False),
    (r"(?:CITY )?COMPTROLLER", "controller", "Controller", False),
    (r"(?:CITY )?TREASURER", "treasurer", "Treasurer", False),
    (r"(?:CHIEF )?(?:CITY )?AUDITOR", "auditor", "Auditor", False),
    (r"TRUSTEES?", "board-of-trustees", "Board of Trustees", True),
    (
        r"(?:COMMON |CITY |TOWN )?COUNCILM[AE]N|COUNCILWOMAN|(?:COMMON |CITY |TOWN )?COUNCIL ?MEMBERS?|COUNCILPERSON|ALDERM\w+|TOWN BOARD|COMMON COUNCIL|COUNCIL",
        "council",
        "Council",
        True,
    ),
)
SPECIALS = (
    (r"MUNICIPAL UTILITY DISTRICT|\bM ?U ?D\b", "utility", "Municipal Utility District"),
    (r"MUNICIPAL UTILITY AUTHORITY", "utility", "Municipal Utility Authority"),
    (r"SPECIAL UTILITY DISTRICT|\bSUD\b", "utility", "Special Utility District"),
    (
        r"WATER CONTROL (?:AND|&) IMPROVEMENT DISTRICT|\bW ?C ?I ?D\b",
        "improvement",
        "Water Control and Improvement District",
    ),
    (r"FRESH WATER SUPPLY DISTRICT|\bF ?W ?S ?D\b", "water", "Fresh Water Supply District"),
    (r"EMERGENCY SERVICES? DISTRICT|\bE ?S ?D\b", "fire", "Emergency Services District"),
    (r"HOSPITAL DISTRICT", "hospital", "Hospital District"),
    (r"GROUNDWATER CONSERVATION DISTRICT", "conservation", "Groundwater Conservation District"),
    (r"(?:SOIL (?:AND|&) WATER )?CONSERVATION DISTRICT", "conservation", "Conservation District"),
    (r"DRAINAGE DISTRICT", "drainage", "Drainage District"),
    (r"NAVIGATION DISTRICT", "port", "Navigation District"),
    (r"LEVEE IMPROVEMENT DISTRICT|\bLID\b", "levee", "Levee Improvement District"),
    (r"(?:PUBLIC |MUNICIPAL )?IMPROVEMENT DISTRICT|\bPID\b", "improvement", "Improvement District"),
    (r"LIBRARY DISTRICT", "library", "Library District"),
    (
        r"COMMUNITY COLLEGE(?: DISTRICT)?|JUNIOR COLLEGE(?: DISTRICT)?|COLLEGE DISTRICT|\bACC\b",
        "college",
        "Community College District",
    ),
    (r"WATER (?:SUPPLY |IMPROVEMENT )?DISTRICT|WATER AUTHORITY|RIVER AUTHORITY", "water", "Water District"),
)
SPECIAL_ROLES = re.compile(
    r"\b(?:BOARD OF (?:DIRECTORS|TRUSTEES|SUPERVISORS|COMMISSIONERS)|DIRECTORS? ELECTION|DIRECTORS?|TRUSTEES?|"
    r"SUPERVISORS?|COMMISSIONERS?|MEMBERS?|BOARD|ELECTION)\b"
)
SCHOOL = re.compile(r"ISD\b|SCHOOL|\bSCH\b")
SCHOOL_WORDS = re.compile(
    r"\b(?:INDEPENDENT|CONSOLIDATED|COMMON|AREA|SCHOOLS?|SCH|DISTRICT|DIST|ISD|CISD|SD|JOINT|UNIFIED|COMMUNITY|REGIONAL|COUNTY)\b"
)
SEATS = (
    (r"(?:PRECINCT|PCT|PREC)", "Precinct"),
    (r"(?:PLACE|PL)", "Place"),
    (r"(?:POSITION|POS|SEAT)", "Position"),
    (r"(?:SINGLE MEMBER DISTRICT|DISTRICT|DIST|SMD)", "District"),
    (r"(?:WARD|WD)", "Ward"),
    (r"REGION", "Region"),
    (r"ZONE", "Zone"),
)
FILLER = re.compile(
    r"\b(?:OF|THE|FOR|AND|MEMBERS?|ELECTIONS?|GENERAL|REGULAR|SPECIAL|JOINT|BOARD|NO|NUMBER|CITY|TOWN|VILLAGE|"
    r"BOROUGH|BORO|TOWNSHIP|TWP|COUNTY|COUNTYWIDE|VOTE|TERM|TO|A|AT LARGE|AT-LARGE|ATLARGE|NONPARTISAN|OFFICE|"
    r"COUNCIL|DIRECTORS?|SEAT|MUNICIPAL|ELECT|IN|AN|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|NEW|YEAR|YEARS|DISTRICT)\b|[-#/]"
)
METHOD = (
    "Votes are the election night reporting results posted by {publishers}, by precinct, summed across the ways of "
    "voting each county reports. Every candidate's precinct votes add up to the county's printed total."
)
TOTALS = (
    "Votes are the election night reporting results posted by {publishers}, counted as one total per county where a "
    "county's precincts do not add up to its printed total or are already in the archive under other names."
)
PARTIAL = (
    "Only part of this jurisdiction's vote is here: the counties that report it on their election night sites "
    "cover about {share} of its land area. Votes cast in {missing} are missing, so no winner is recorded."
)
UNOFFICIAL = (
    "The county's site does not mark these results as official or certified; they are the last count it posted, "
    "at least two weeks after election day."
)
WITHDRAWN = "Withdrawn: a corrected reading of the counties' election night files no longer loads this race this way."


PARTY_KEYS = {"precinct-chair", "county-chair", "state-committee", "committeeman", "committeewoman", "committee-person"}
OFFICER_KEYS = {"judge-of-elections", "inspector-of-elections"}


def tenants(platform: str = "clarity") -> list[dict[str, str]]:
    with portals.REGISTRY.open() as file:
        return [
            row
            for row in csv.DictReader(file)
            if row["platform"] == platform and row["status"] == "listed" and row["tenant"].startswith("https://")
        ]


def fetch() -> None:
    _, index = raw._index(SOURCE)
    cutoff = date.today() - timedelta(days=14)
    settled = date.today() - timedelta(days=120)
    for fips in sorted({row["county_fips"][:2] for row in tenants()}):
        for layer in LAYERS:
            raw.fetch(SOURCE, CENSUS.format(fips=fips, layer=layer), suffix=".zip")
    failures = 0
    for row in tenants():
        tenant = row["tenant"]
        status, body, _ = portals.get(f"{tenant}elections.json")
        if status != 200:
            print(f"{tenant}: {status}")
            continue
        fetched = skipped = 0
        for listed in json.loads(body):
            day = datetime.strptime(listed["Date"].split()[0], "%m/%d/%Y").date()
            election = listed["EID"]
            if day > cutoff:
                skipped += 1
                continue
            if day < settled and any(key.startswith(f"{tenant}{election}/") for key in index):
                continue
            status, version, _ = portals.get(f"{tenant}{election}/current_ver.txt")
            if status != 200 or not version.strip().isdigit():
                print(f"{tenant}{election}: version {status}")
                continue
            link = f"{tenant}{election}/{version.decode().strip()}/reports/detailxml.zip"
            if link in index:
                continue
            try:
                raw.fetch(SOURCE, link, suffix=".zip")
                fetched, failures = fetched + 1, 0
            except httpx.HTTPError as error:
                print(f"{link}: {str(error).splitlines()[0]}")
                if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code >= 500:
                    failures += 1
            portals.last[HOST] = time.monotonic()
            if HOST in portals.stopped or failures >= 3:
                raise RuntimeError(f"{HOST} asked us to stop")
        print(f"{row['state']} {row['county']}: {fetched} fetched, {skipped} too recent", flush=True)


def _party(value: str | None) -> str | None:
    return PARTIES.get(re.sub(r"[^A-Z]", "", (value or "").upper()))


def _plain(text: str) -> str:
    text = text.upper().replace("&AMP;", "&")
    text = re.sub(r"(\d)(?:ST|ND|RD|TH)\b", r"\1", text)
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9&#/-]+", " ", text)).strip(" -/")


def _title(text: str) -> str:
    if text != text.upper():
        text = re.sub(r"\b(Mc|Mac|De|Di|La|Le|O')([A-Z]{2,})\b", lambda match: match[1] + match[2].title(), text)
        return re.sub(
            r"\b(?!(?:II|III|IV|JR|SR)\b)([A-Z])([A-Z'’-]+)\b", lambda match: match[1] + match[2].lower(), text
        )
    text = re.sub(r"\b([A-Z][A-Z'’-]+)\b", lambda match: match[1].title() if len(match[1]) > 1 else match[1], text)
    text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text)
    return re.sub(r"(?<=\s)(Of|The|And|For)\b", lambda match: match[0].lower(), text)


def _seat(text: str) -> tuple[str | None, str]:
    parts = []
    if docket := re.search(r"\b(\d{2}) ?- ?(\d) ?- ?(\d{2,3})\b", text):
        return f"{docket[1]}-{docket[2]}-{docket[3]}", text.replace(docket[0], " ")
    if court := re.search(r"\b(?:COURT|CRT|CT)(?:[ -]AT[ -]LAW)? (?:NO |NUMBER |# ?)?(\d+)\b", text):
        parts.append(f"Court {court[1]}")
        text = text.replace(court[0], " COURT ")
    for pattern, label in SEATS:
        found = re.search(rf"\b{pattern}\b ?(?:NO\b|NUMBER\b|NUM\b|#)? ?#? ?{NUMBER}", text)
        if found is None or found[1] in ("A", "I") and label not in ("Place", "Position", "District"):
            continue
        number = NUMBERS.get(found[1], found[1])
        parts.append(f"{label} {number.lstrip('0') or '0'}")
        text = re.sub(rf"\b{pattern}\b ?(?:NO\b|NUMBER\b|NUM\b|#)? ?#? ?{re.escape(found[1])}\b", " ", text)
    if not parts and re.search(r"\bAT[ -]?LARGE\b", text):
        parts.append("At-Large")
    return (", ".join(parts) or None), re.sub(r"\s+", " ", text)


def _residue(text: str, *patterns: str) -> str:
    for pattern in patterns:
        text = re.sub(rf"\b(?:{pattern})\b", " ", text)
    text = TERM.sub(" ", REMAINDER.sub(" ", text))
    return re.sub(r"\s+", " ", FILLER.sub(" ", text)).strip()


def _shapes(fips: str, layer: str, source: str = SOURCE) -> list[tuple[Mapping[str, object], str]]:
    with zipfile.ZipFile(raw.cached(source, CENSUS.format(fips=fips, layer=layer)).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    return [
        (record.as_dict(), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    files: dict[tuple[str, str], tuple[int, str]] = {}
    for url in raw._index(SOURCE)[1]:
        found = re.fullmatch(r"(.+/)(\d+)/(\d+)/reports/detailxml\.zip", url)
        if found and found[1] in registry and int(found[3]) > files.get((found[1], found[2]), (0, ""))[0]:
            files[(found[1], found[2])] = (int(found[3]), url)
    for (tenant, election), (_, link) in sorted(files.items()):
        with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        yield {
            "tenant": tenant,
            "election": election,
            "day": datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date(),
            "title": root.findtext("ElectionName") or "",
            "official": False,
            "primary": False,
            "contests": [
                {
                    "key": node.get("key"),
                    "text": node.get("text") or "",
                    "vote_for": int(node.get("voteFor") or 1),
                    "choices": [
                        {
                            "text": choice.get("text") or "",
                            "party": choice.get("party"),
                            "total": choice.get("totalVotes") or "0",
                            "places": [
                                (place.get("name") or "", place.get("votes") or "0")
                                for kind in choice.iter("VoteType")
                                for place in kind.iter("Precinct")
                            ],
                        }
                        for choice in node.iter("Choice")
                    ],
                }
                for node in root.iter("Contest")
            ],
        }


def parse(
    source: str = SOURCE,
    platform: str = "clarity",
    read: Callable[[Mapping[str, Mapping[str, str]]], Iterator[dict[str, Any]]] = _read,
) -> None:
    registry = {row["tenant"]: row for row in tenants(platform)}
    states = sorted({row["county_fips"][:2] for row in registry.values()})
    jurisdictions: dict[tuple[str, str], tuple[str, str, str]] = {}
    geoms: list[tuple[str, str, str]] = []
    for fips in states:
        for record, geom in _shapes(fips, "cousub", source):
            proper, whole = str(record["NAME"]), str(record["NAMELSAD"])
            kind = whole[len(proper) :].strip().lower().replace("charter township", "township")
            if kind not in ("township", "borough", "town", "city"):
                continue
            display = f"{proper} Township" if kind == "township" else proper
            jurisdictions[("us.town", str(record["GEOID"]))] = (display, kind, proper)
            geoms.append(("us.town", str(record["GEOID"]), geom))
        for record, geom in _shapes(fips, "place", source):
            lsad = str(record["LSAD"])
            if lsad not in ("21", "25", "43", "47"):
                continue
            kind = {"21": "borough", "25": "city", "43": "town", "47": "village"}[lsad]
            jurisdictions[("us.place", str(record["GEOID"]))] = (str(record["NAME"]), kind, str(record["NAME"]))
            geoms.append(("us.place", str(record["GEOID"]), geom))
        for record, geom in _shapes(fips, "unsd", source):
            jurisdictions[("us.school_district", str(record["GEOID"]))] = (
                str(record["NAME"]),
                "school",
                str(record["NAME"]),
            )
            geoms.append(("us.school_district", str(record["GEOID"]), geom))
    shares: dict[tuple[str, str], dict[str, float]] = {}
    with connect() as conn:
        conn.execute("create temp table shape (scheme text, code text, geojson text)")
        with conn.cursor().copy("copy shape (scheme, code, geojson) from stdin") as copy:
            for row in geoms:
                copy.write_row(row)
        conn.execute(
            """create temp table jurisdiction as select scheme, code, st_makevalid(st_setsrid(st_geomfromgeojson(geojson), 4326)) geom
               from shape"""
        )
        conn.execute("create index on jurisdiction using gist (geom)")
        for scheme, code, county, share in conn.execute(
            """with county as (
                 select distinct on (c.code) c.code, g.geom from area a
                 join area_code c on c.area_id = a.id
                 join area_geometry g on g.area_id = a.id
                 where a.scheme_code = 'us.county' and left(c.code, 2) = any(%s) and c.valid_to is null
                 order by c.code, g.valid_from desc nulls last)
               select j.scheme, j.code, county.code,
                      st_area(st_intersection(j.geom, county.geom)) / nullif(st_area(j.geom), 0)
               from jurisdiction j join county on st_intersects(j.geom, county.geom)""",
            [states],
        ).fetchall():
            if share is not None and share > 0.002:
                shares.setdefault((scheme, code), {})[county] = round(float(share), 4)
    munis: dict[str, list[tuple[str, str, str, str]]] = {}
    schools: dict[str, list[tuple[str, str]]] = {}
    for (scheme, code), counties in shares.items():
        display, kind, proper = jurisdictions[(scheme, code)]
        for county in counties:
            if scheme == "us.town" and county != code[:5]:
                continue
            if scheme == "us.school_district":
                bare = re.sub(r"\s+", " ", SCHOOL_WORDS.sub(" ", _plain(display))).strip()
                schools.setdefault(county, []).append((bare, code))
                if county.startswith("36") and bare.endswith(" CITY"):
                    schools[county].append((bare[:-5], code))
                initials = "".join(word[0] for word in _plain(display).split())
                if len(initials) >= 5 and initials.endswith("ISD"):
                    schools[county].append((initials, code))
            else:
                munis.setdefault(county, []).append((_plain(proper), scheme, code, kind))
    for county, listed in munis.items():
        places = {(bare, kind) for bare, scheme, _, kind in listed if scheme == "us.place"}
        listed[:] = [entry for entry in listed if entry[1] == "us.place" or (entry[0], entry[3]) not in places]
    contests: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    skipped: Counter[str] = Counter()
    for record in read(registry):
        tenant, election, day = record["tenant"], record["election"], record["day"]
        day = DAY_FIXES.get((tenant, election), day)
        row = registry[tenant]
        county, st = row["county_fips"], row["state"]
        county_name = _plain(row["county"])
        title = re.sub(r"\s+", " ", record["title"]).strip()
        heading = _plain(title)
        if re.search(r"\bTEST\b|MOCK|PRACTICE|SAMPLE", heading):
            continue
        primary = record["primary"] or "PRIMARY" in heading
        runoff = "RUNOFF" in heading or "RUN OFF" in heading
        heading_party = next((PARTIES[word] for word in heading.split() if word in PARTIES), None)
        stage = (
            "official"
            if record["official"] or re.search(r"(?<!UN)OFFICIAL|CERTIFIED|CANVASS", heading)
            else "preliminary"
        )
        names = sorted({entry[0] for entry in munis.get(county, [])}, key=len, reverse=True)
        school_names = sorted(schools.get(county, []), key=lambda entry: len(entry[0]), reverse=True)
        for node in record["contests"]:
            text = re.sub(r"\s+", " ", node["text"]).strip()
            plain = _plain(text)
            plain = re.sub(rf"\b{re.escape(county_name)} COUNTYWIDE\b", "", plain).strip()
            choices = [
                choice for choice in node["choices"] if not VACANT.match(choice["text"]) and choice["total"].isdigit()
            ]
            if not choices:
                continue
            party = None
            words = r"REP|DEM|REPUBLICAN|DEMOCRATIC|DEMOCRAT" + (r"|R|D" if primary else "")
            if marker := re.match(rf"^({words})\b ?-? ?", plain) or re.search(rf" ?-? ?\b({words})(?: PARTY)?$", plain):
                party = PARTIES[marker[1]]
                plain = (plain[: marker.start()] + " " + plain[marker.end() :]).strip()
            elif primary and (marker := re.search(r"\b(REPUBLICAN|DEMOCRATIC)\b", plain)):
                party = PARTIES[marker[1]]
                plain = re.sub(r"\s+", " ", plain[: marker.start()] + " " + plain[marker.end() :]).strip()
            if SKIPPED.search(plain) or st == "IL" and re.fullmatch(r"(?:FOR )?TREASURER", plain):
                skipped["state or federal"] += 1
                continue
            labels = [choice["text"] for choice in choices if not WRITE_IN.match(choice["text"])]
            if not labels:
                skipped["write-ins only"] += 1
                continue
            question = bool(labels) and all(QUESTION.match(label) for label in labels)
            if primary:
                tagged = Counter(_party(choice["party"]) for choice in choices if choice["party"])
                party = party or heading_party or next((key for key, _ in tagged.most_common() if key), None)
                if party is None or question:
                    skipped["primary question" if question else "primary with no party"] += 1
                    continue
            else:
                party = None
            remainder = REMAINDER.search(plain) is not None
            term = TERM.search(plain)
            found = _classify(plain, st, county, county_name, names, school_names, munis.get(county, []))
            if (found is None or found.get("loose")) and (prefix := _prefix(heading, plain, names, school_names)):
                found = (
                    retry
                    if (
                        retry := _classify(
                            f"{prefix} {plain}", st, county, county_name, names, school_names, munis.get(county, [])
                        )
                    )
                    and retry["scheme"] != "us.county"
                    else found
                )
            if found is None:
                skipped[re.sub(r"\d+", "N", plain)[:70]] += 1
                continue
            if question:
                found = {**found, "key": "question", "office": "", "body": False, "seat": None}
            elif found["key"] == "question":
                skipped["question with candidates"] += 1
                continue
            rows: list[dict[str, object]] = []
            for choice in choices:
                label = re.sub(r"\s+", " ", choice["text"]).strip()
                tag = _party(choice["party"])
                if prefix := re.match(r"(?:\(([RD])\)|(DEM|REP|LIB|GRN|IND))\s+(?=\S)", label):
                    tag = tag or _party(prefix[1] or prefix[2])
                    label = label[prefix.end() :]
                if (
                    WRITE_IN.match(label)
                    or primary
                    and party
                    and tag is None
                    and any(_party(other["party"]) for other in choices)
                ):
                    label = "Write-ins"
                for place, votes in choice["places"]:
                    rows.append(
                        {
                            "county": county,
                            "election": election,
                            "contest": node["key"],
                            "choice": label,
                            "party": tag,
                            "place": re.sub(r"\s+", " ", place.upper()).strip(),
                            "votes": int(votes) if votes.isdigit() else 0,
                        }
                    )
                rows.append(
                    {
                        "county": county,
                        "election": election,
                        "contest": node["key"],
                        "choice": label,
                        "party": tag,
                        "place": None,
                        "votes": int(choice["total"]),
                    }
                )
            lines.extend(rows)
            contests.append(
                {
                    "state": st,
                    "county": county,
                    "county_name": row["county"],
                    "tenant": tenant,
                    "election": election,
                    "contest": node["key"],
                    "day": day,
                    "title": title,
                    "text": text,
                    "stage": stage,
                    "primary": primary,
                    "runoff": runoff,
                    "special": "SPECIAL" in heading,
                    "party": party,
                    "remainder": remainder,
                    "term": f"{NUMBERS.get(term[1], term[1])}-year term" if term else None,
                    "magnitude": node["vote_for"],
                    **found,
                }
            )
    print(f"skipped {sum(skipped.values())} contests; most common:")
    for text, count in skipped.most_common(40):
        print(f"  {count:5} {text}")
    frame = (
        pl.DataFrame(lines, schema_overrides={"votes": pl.Int64, "place": pl.String, "party": pl.String})
        .with_columns(same=pl.col("choice").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", ""))
        .group_by("county", "election", "contest", "same", "place", maintain_order=True)
        .agg(pl.col("choice").first(), pl.col("party").drop_nulls().first(), pl.col("votes").sum())
        .drop("same")
    )
    table = pl.DataFrame(contests, infer_schema_length=None)
    totals = frame.filter(pl.col("place").is_null()).drop("place")
    counted = (
        frame.filter(pl.col("place").is_not_null())
        .group_by("county", "election", "contest", "choice")
        .agg(pl.col("votes").sum().alias("counted"))
    )
    checked = (
        totals.join(counted, on=["county", "election", "contest", "choice"], how="left")
        .group_by("county", "election", "contest")
        .agg((pl.col("votes") == pl.col("counted")).fill_null(False).all().alias("balanced"))
    )
    table = table.join(checked, on=["county", "election", "contest"], how="left")
    base = pl.concat_str(
        pl.col("state"),
        pl.col("code"),
        pl.col("key"),
        pl.when(pl.col("key") == "question").then(pl.col("label").map_elements(slug, return_dtype=pl.String)),
        pl.col("seat").map_elements(slug, return_dtype=pl.String),
        pl.when(pl.col("remainder")).then(pl.lit("remainder")),
        pl.col("party"),
        separator=":",
        ignore_nulls=True,
    )
    table = table.with_columns(base=base)
    variants = []
    for (_, _), files_rows in table.group_by("tenant", "election"):
        for _, group in files_rows.group_by("base"):
            if group.height == 1:
                continue
            terms = group["term"].to_list()
            ambiguous = 0
            for contest, term in zip(group["contest"].to_list(), terms, strict=True):
                if term and terms.count(term) == 1:
                    variants.append((group["tenant"][0], group["election"][0], contest, slug(term)))
                else:
                    ambiguous += 1
                    if ambiguous > 1:
                        variants.append((group["tenant"][0], group["election"][0], contest, f"contest-{ambiguous}"))
    table = table.join(
        pl.DataFrame(
            variants,
            schema={"tenant": pl.String, "election": pl.String, "contest": pl.String, "variant": pl.String},
            orient="row",
        ),
        on=["tenant", "election", "contest"],
        how="left",
    ).with_columns(
        contest_key=pl.concat_str(
            pl.col("day").cast(pl.String),
            pl.col("base"),
            pl.when(pl.col("runoff")).then(pl.lit("runoff")),
            pl.col("variant"),
            separator=":",
            ignore_nulls=True,
        )
    )
    table = table.with_columns(
        repeated=pl.col("contest_key").is_duplicated().over("tenant", "election"),
    )
    if table["repeated"].any():
        print(f"dropped {table['repeated'].sum()} contests whose keys repeat within one file")
        table = table.filter(~pl.col("repeated"))
    print(
        f"{table.height} contests ({table['contest_key'].n_unique()} after merging counties), "
        f"{table.filter('balanced').height} with precincts that add up; by scheme: "
        + ", ".join(
            f"{scheme} {count}"
            for scheme, count in table.group_by("scheme").len().sort("len", descending=True).iter_rows()
        )
    )
    parsed = DATA / "parsed" / source
    parsed.mkdir(parents=True, exist_ok=True)
    table.drop("repeated").write_parquet(parsed / "contests.parquet")
    frame.join(table.select("county", "election", "contest"), on=["county", "election", "contest"]).write_parquet(
        parsed / "lines.parquet"
    )
    shapes = {(scheme, code): geom for scheme, code, geom in geoms}
    if "display" not in table.columns:
        table = table.with_columns(display=pl.lit(None, pl.String))
    used = table.filter(pl.col("scheme") != "us.county").unique(["scheme", "code"]).sort("scheme", "code")
    pl.DataFrame(
        [
            (
                scheme,
                code,
                jurisdictions[(scheme, code)][0] if (scheme, code) in jurisdictions else display,
                shapes.get((scheme, code)),
                json.dumps(shares.get((scheme, code), {code[:5]: 1.0})),
            )
            for scheme, code, display in used.select("scheme", "code", "display").iter_rows()
        ],
        schema=["scheme", "code", "display", "geom", "shares"],
        orient="row",
    ).write_parquet(parsed / "polities.parquet")


def _prefix(heading: str, plain: str, names: list[str], school_names: list[tuple[str, str]]) -> str | None:
    heading = re.sub(
        r"\b(?:\d+|JAN\w*|FEB\w*|MAR\w*|APR\w*|MAY|JUNE?|JULY?|AUG\w*|SEPT?\w*|OCT\w*|NOV\w*|DEC\w*)\b", " ", heading
    )
    muni = next((name for name in names if re.search(rf"\b{re.escape(name)}\b", heading)), None)
    bare = SCHOOL_WORDS.sub(" ", heading)
    school = next(
        (
            name
            for name, _ in school_names
            if name and SCHOOL.search(heading) and re.search(rf"\b{re.escape(name)}\b", bare)
        ),
        None,
    )
    if re.search(r"TRUSTEE|SCHOOL|BOARD", plain):
        school = school or next((name for name, _ in school_names if name == muni), None)
        return f"{school} ISD" if school else None
    if muni is None:
        return None
    kind = re.search(
        rf"\b(CITY|TOWN|VILLAGE|BOROUGH|TOWNSHIP) OF {re.escape(muni)}\b|\b{re.escape(muni)} (CITY|TOWN|VILLAGE|BOROUGH|TOWNSHIP)\b",
        heading,
    )
    return f"{kind[1] or kind[2]} OF {muni}" if kind else muni


def _precinct_key(name: str) -> tuple[str, str] | None:
    plain = re.sub(r"\s+", " ", name.upper().replace(",", " ").replace("&AMP;", "&").replace(" CHARTER", "")).strip()
    if match := re.fullmatch(r"(.+?) (\d+[A-Z]?) WARD \d+", plain):
        return match.group(1), match.group(2)
    if match := re.fullmatch(r"CITY OF (.+?)(?: WARD \d+)?(?: PRECINCT (\d+[A-Z]?)(?:-[A-Z]+)?)?", plain):
        return (f"{match.group(1)} CITY", match.group(2)) if match.group(2) else None
    if match := re.fullmatch(r"(.+?)(?: WARD \d+)? PRECINCT (\d+[A-Z]?)", plain):
        return match.group(1), match.group(2)
    if match := re.fullmatch(r"(.+? (?:CITY|TOWNSHIP|VILLAGE))(?: WARD \d+)? (\d+[A-Z]?)", plain):
        return match.group(1), match.group(2)
    return None


def _classify(
    plain: str,
    st: str,
    county: str,
    county_name: str,
    names: list[str],
    school_names: list[tuple[str, str]],
    listed: list[tuple[str, str, str, str]],
) -> dict[str, object] | None:
    plain = re.sub(r"\b(PRECINCT|PCT) (\d+) (CHAIR\w*)", r"\3 \1 \2", plain)
    plain = re.sub(r"\bCHARTER (?=TOWNSHIP|TWP)", "", plain)
    if st in ("IL", "MI"):
        plain = re.sub(r"\b(\d+) (DISTRICT|DIST)\b", r"\2 \1", plain)
    if st == "NY":
        plain = re.sub(r"\s+", " ", TERM.sub(" ", plain))
        plain = re.sub(r"\b(\d+) (WARD|DISTRICT|DIST)\b", r"\2 \1", plain)
    for options, scope in ((PARTY_OFFICES, "party"), (OFFICERS, "officer")):
        for pattern, key, office in options:
            if found := re.search(rf"\b(?:{pattern})\b", plain):
                rest = re.sub(r"\s+", " ", (plain[: found.start()] + " " + plain[found.end() :])).strip(" ,-/")
                rest = re.sub(
                    rf"\b(?:{re.escape(county_name)}(?: COUNTY)?|COUNTY|LOCAL|REPUBLICAN|DEMOCRATIC|REP|DEM|MEMBER OF|FOR|THE|OF)\b",
                    " ",
                    rest,
                )
                rest = re.sub(r"\s+", " ", rest).strip(" ,-/")
                if key == "state-committee":
                    seat = f"District {district[1]}" if (district := re.search(r"\b(\d+) DISTRICT\b", rest)) else None
                elif bare := re.fullmatch(r"(?:PRECINCT|PCT|PREC)?\.? ?(?:NO\.?|#)? ?(\d+[A-Z]?)", rest):
                    seat = f"Precinct {bare[1].lstrip('0') or '0'}"
                else:
                    seat = _title(rest) or None
                if scope == "officer" and seat is None:
                    return None
                return {
                    "scheme": "us.county",
                    "code": county,
                    "key": key,
                    "office": office,
                    "body": False,
                    "seat": seat,
                    "label": None,
                }
    plain = re.sub(rf"\b{re.escape(county_name)} (?:COUNTY|CO)\b", "COUNTY", plain)
    label = re.sub(r"\s+", " ", plain).strip()
    if (SCHOOL.search(plain) or st == "NY" and "EDUCATION" in plain) and school_names:
        bare = re.sub(r"\s+", " ", SCHOOL_WORDS.sub(" ", plain)).strip()
        for name, code in school_names:
            if name and re.search(
                rf"\b{re.escape(name)}\b", bare if " " in name or not name.endswith("ISD") else plain
            ):
                if name.endswith("ISD") and " " not in name:
                    bare = re.sub(rf"\b{name}\b", " ", plain)
                rest = re.sub(rf"\b{re.escape(name)}\b", " ", bare)
                seat, rest = _seat(rest)
                if seat is None and (region := re.search(r"\b(\d+[A-Z]?)\b(?! ?(?:YR|YEAR))", rest)):
                    seat = f"Region {region[1]}"
                if seat is None and st == "NY":
                    seat = _title(_residue(rest, "EDUCATION|ED|SCHOOLS?")) or None
                return {
                    "scheme": "us.school_district",
                    "code": code,
                    "key": "board-of-education",
                    "office": {"TX": "Board of Trustees", "NY": "Board of Education"}.get(
                        st, "Board of School Directors"
                    ),
                    "body": True,
                    "seat": seat,
                    "label": label,
                }
    initials = "".join(word[0] for word in county_name.split())
    if len(initials) > 1:
        plain = re.sub(rf"\b{initials}C\b", "COUNTY", plain)
    for pattern, kind, noun in SPECIALS:
        if found := next(reversed(list(re.finditer(rf"\b(?:{pattern})", plain))), None):
            head = SPECIAL_ROLES.sub(" ", plain[: found.start()])
            tail = plain[found.end() :]
            number = re.match(r"^ ?(?:NO\.?|NUMBER|#)? ?#? ?(\d+[A-Z]?(?:-[A-Z0-9]+)?)\b", tail)
            if number:
                tail = tail[number.end() :]
            seat, head = _seat(head)
            if seat is None:
                seat, tail = _seat(tail)
            head = re.sub(
                r"\b(?:PROPOSITION|PROP) [A-Z0-9]+\b|\b(?:CITY|TOWN|VILLAGE) OF [A-Z]+\b|^.*\bFOR\b", " ", head
            )
            head = re.sub(r"\s+", " ", re.sub(r"\b(?:OF|THE|FOR|ELECTION|DIRECTORS?)\b", " ", head)).strip(" ,-/")
            if head in ("", "COUNTY") or head.startswith("COUNTY "):
                head = f"{county_name} {head or 'COUNTY'}" if head.startswith("COUNTY") else f"{county_name} COUNTY"
            if kind == "college" and re.search(r"\bACC\b|AUSTIN", plain):
                head, noun = "AUSTIN", "Community College District"
            display = f"{_title(head)} {noun}" + (f" {number[1]}" if number else "")
            return {
                "scheme": "us.special_district",
                "code": f"{county}:{kind}:{slug(display)}",
                "display": display,
                "key": "special",
                "office": {
                    "college": "Board of Trustees",
                    "fire": "Board of Commissioners",
                    "hospital": "Board of Managers",
                }.get(kind, "Board of Directors"),
                "body": True,
                "seat": seat,
                "label": label,
            }
    held, spans = [], []
    for office in (*NY_COUNTY_OFFICES, *COUNTY_OFFICES) if st == "NY" else COUNTY_OFFICES:
        found = re.search(rf"\b(?:{office[0]})\b", plain)
        if found and not any(found.start() < end and start < found.end() for start, end in spans):
            held.append(office)
            spans.append(found.span())
    if held:
        seat, rest = _seat(plain)
        _, key, office, body = held[0]
        if key == "court-of-common-pleas":
            seat, rest = None, re.sub(r"\d+", " ", rest)
        if seat and (number := re.search(r"(\d+)$", seat)):
            rest = re.sub(rf"\b0*{number[1]}\b", " ", rest)
        if seat is None and (bare := re.search(r"\b(\d+)\b(?! ?(?:YR|YEAR))", rest)):
            noun = "Court" if "court" in key else "Precinct"
            seat, rest = f"{noun} {int(bare[1])}", re.sub(rf"\b{bare[1]}\b", " ", rest)
        if _residue(
            rest,
            *(office[0] for office in held),
            r"JUDGE|COURT|CT|CRT|LAW|PCT|PRECINCT|COMM|CRIMINAL",
            re.escape(county_name),
            r"TERM ENDING [0-9/]+",
            r"FULL",
            r"UNEXPIRED",
            r"VOTE FOR \d+",
        ):
            return None
        if len(held) > 1 and not any(office[3] for office in held):
            key = "-and-".join(office[1] for office in held)
            office = " and ".join(office[2] for office in held)
        return _county(st, county, key, office, body, seat, label)
    match = next((name for name in names if re.search(rf"\b{re.escape(name)}\b", plain)), None)
    if match is None:
        for pattern, key, office, body in LOOSE:
            if re.search(r"\b(?:CITY|TOWN|BOROUGH|BORO|TOWNSHIP|TWP|VILLAGE)\b", plain):
                break
            if re.search(rf"\b(?:{pattern})\b", plain) and (st == "TX" or key != "constable"):
                seat, rest = _seat(plain)
                if _residue(rest, pattern) or st == "TX" and seat and not seat.startswith("Precinct"):
                    return None
                return {**_county(st, county, key, office, body, seat, label), "loose": True}
        if re.search(r"\bCOUNTY\b", plain) and re.search(r"\bPROP|QUESTION|BOND|REFERENDUM|ISSUE", plain):
            return _county(st, county, "question", "", False, None, label)
        return None
    after = re.escape(match)
    candidates = [entry for entry in listed if entry[0] == match]
    wanted = None
    if re.search(rf"\b{after} (?:TOWNSHIP|TWP)\b|\b(?:TOWNSHIP|TWP) OF {after}\b", plain):
        wanted = "township"
    elif re.search(rf"\bCITY OF {after}\b|\b{after} CITY\b", plain):
        wanted = "city"
    elif re.search(rf"\bVILLAGE OF {after}\b|\b{after} VILLAGE\b", plain):
        wanted = "village"
    elif re.search(rf"\bBOROUGH OF {after}\b|\b{after} (?:BOROUGH|BORO)\b", plain):
        wanted = "borough"
    elif re.search(rf"\bTOWN OF {after}\b|\b{after} TOWN\b", plain):
        wanted = "town"
    if wanted is not None and any(entry[3] == wanted for entry in candidates):
        candidates = [entry for entry in candidates if entry[3] == wanted]
    if len(candidates) > 1:
        candidates = [entry for entry in candidates if entry[1] == "us.place"] or candidates
    if len(candidates) != 1:
        return None
    _, scheme, code, kind = candidates[0]
    rest = re.sub(
        rf"\b(?:(?:CITY|TOWN|VILLAGE|BOROUGH|TOWNSHIP|TWP) OF )?{after}(?: (?:CITY|TOWN|VILLAGE|BOROUGH|BORO|TOWNSHIP|TWP))?\b",
        " ",
        plain,
        count=1,
    )
    seat = None
    if ward := re.search(rf"\b{after}(?: (?:BOROUGH|BORO|TOWNSHIP|TWP|CITY))? (\d+)\b(?! ?(?:YR|YEAR))", plain):
        seat = f"Ward {int(ward[1])}"
        rest = re.sub(rf"(?<!\d)\b{ward[1]}\b(?! ?(?:YR|YEAR))", " ", rest, count=1)
    if re.search(r"\bPROP|QUESTION|BOND|REFERENDUM|CHARTER AMENDMENT|ORDINANCE|ISSUE|MEASURE", rest):
        return {
            "scheme": scheme,
            "code": code,
            "key": "question",
            "office": "",
            "body": False,
            "seat": None,
            "label": label,
        }
    found_seat, rest = _seat(rest)
    if found_seat is None and seat is None and (bare := re.search(r"\b(\d+)\b(?! ?(?:YR|YEAR))", rest)):
        found_seat = f"{'Ward' if st == 'PA' else 'Place'} {int(bare[1])}"
        rest = rest.replace(bare[0], " ", 1)
    seat = ", ".join(part for part in (seat, found_seat) if part) or None
    offices = (
        NY_MUNI_OFFICES if st == "NY" else (*MI_MUNI_OFFICES, *MUNI_OFFICES) if st in ("IL", "MI") else MUNI_OFFICES
    )
    for pattern, key, office, body in offices:
        if re.search(rf"\b(?:{pattern})\b", rest):
            break
    else:
        if seat is None or kind == "township":
            return None
        pattern, key, office, body = "", "council", "Council", True
    left = _residue(rest, pattern, "JUDGE") if pattern else _residue(rest)
    if left and st == "NY" and key == "council" and seat is None:
        seat, left = _title(left), ""
    if left:
        return None
    if kind == "township" and key == "council":
        key, office = "board-of-commissioners", "Board of Commissioners"
    if key == "board-of-trustees" and kind in ("city", "borough"):
        key, office = "council", "Council"
    return {"scheme": scheme, "code": code, "key": key, "office": office, "body": body, "seat": seat, "label": label}


def _county(st: str, county: str, key: str, office: str, body: bool, seat: str | None, label: str) -> dict[str, object]:
    if key == "commissioners":
        key, office = (
            ("commissioners-court", "Commissioners Court")
            if st == "TX"
            else ("board-of-commissioners", "Board of Commissioners")
        )
    return {
        "scheme": "us.county",
        "code": county,
        "key": key,
        "office": office,
        "body": body,
        "seat": seat,
        "label": label,
    }


def emit(
    source: str = SOURCE,
    platform: str = "clarity",
    owns: Callable[[str, str], bool] = lambda link, tenant: link.startswith(tenant) and link.endswith("detailxml.zip"),
) -> Path:
    parsed = DATA / "parsed" / source
    listed = tenants(platform)
    county_names = {row["county_fips"]: row["county"].upper() for row in listed}
    contests = pl.read_parquet(parsed / "contests.parquet")
    lines = pl.read_parquet(parsed / "lines.parquet")
    made = {
        (scheme, code): (display, geom, json.loads(shares))
        for scheme, code, display, geom, shares in pl.read_parquet(parsed / "polities.parquet").iter_rows()
    }
    sizes = (
        lines.filter(pl.col("place").is_null())
        .group_by("county", "election", "contest")
        .agg(pl.col("votes").sum().alias("size"))
    )
    contests = (
        contests.join(sizes, on=["county", "election", "contest"], how="left")
        .sort("size", descending=True)
        .unique(["contest_key", "county"], keep="first")
        .sort("day", "contest_key", "county")
    )
    grouped = {keys: rows for keys, rows in lines.group_by("county", "election", "contest")}
    states = sorted(set(contests["state"].to_list()))
    fips = sorted({county[:2] for county in contests["county"].to_list()})
    counties_listed = sorted(set(contests["county"].to_list()))
    pattern = "|".join(states)
    with connect() as conn:
        polities: dict[str, tuple[UUID, str, str]] = {}
        for scheme, code, polity, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district', 'us.special_district')
                 and left(c.code, 2) = any(%s)""",
            [fips],
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, text, path)
        slugs = dict(
            conn.execute(
                "select path, entity_id from slug where path like any(%s)", [[f"us/{st.lower()}/%" for st in states]]
            ).fetchall()
        )
        taken = set(
            conn.execute(
                """select e.date_from, x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join changeset g on g.id = r.changeset_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where split_part(x.value, ':', 1) = any(%s) and g.pipeline <> %s""",
                [states, source],
            ).fetchall()
        )
        asked = set(
            conn.execute(
                """select e.date_from, a.code from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join changeset g on g.id = r.changeset_id
                   join area_code a on a.area_id = c.district_area_id
                   where c.kind = 'ballot_question' and g.pipeline <> %s and left(a.code, 2) = any(%s)""",
                [source, fips],
            ).fetchall()
        )
        held = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id
                   join changeset g on g.id = r.changeset_id
                   where x.scheme = 'us.contest' and g.pipeline <> %s and split_part(x.value, ':', 2) = any(%s)""",
                [source, states],
            ).fetchall()
        }
        precincts: dict[str, UUID] = dict(
            conn.execute(
                """select code, id from reporting_unit where scheme_code = 'us.precinct'
                   and split_part(code, ':', 3) = any(%s)""",
                [counties_listed],
            ).fetchall()
        )
        foreign = {":".join(code.split(":")[:3]) for code in precincts}
        county_units: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                """select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code = any(%s)
                   and valid_to is null""",
                [counties_listed],
            ).fetchall()
        }
        parents: dict[str, str] = dict(
            conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                     where x.scheme = 'us.election'
                       and x.value ~ %s""",
                [rf"^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}:({pattern})(:(primary|runoff|special))?$"],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=source,
            version=VERSION,
            summary="County, municipal, school, special district, and party offices and local questions from county election night reporting sites",
            polity="US",
        )
        cs.renamed["2021-04-06:IL"] = "2021-03-29:IL"

        def name(entity: UUID, text: str) -> None:
            cs.add("name", id=None, entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "referendum")}
        parties = {key: cs.key("us.party", f"{key}-party") for key in ("republican", "democratic")}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the fptp, block, and referendum systems and the two parties must already exist")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for code in fips:
            for layer in LAYERS:
                cs.artifact(raw.cached(source, CENSUS.format(fips=code, layer=layer)), census)
        sources: dict[str, UUID] = {}
        publishers: dict[str, str] = {}
        for row in listed:
            county = row["county_fips"]
            if county not in counties_listed:
                continue
            publishers[county] = f"{row['county']} County, {STATE_NAMES[row['state']]}"
            sources[county] = cs.source(publishers[county], TITLE)
            if sources[county] in cs.minted:
                cs.add(
                    "source",
                    id=sources[county],
                    kind="official_body",
                    publisher=publishers[county],
                    title=TITLE,
                    url=row["tenant"],
                    license="Public record",
                )
        for link in raw._index(source)[1]:
            county = next((row["county_fips"] for row in listed if owns(link, row["tenant"])), None)
            if county in sources:
                cs.artifact(raw.cached(source, link), sources[county])
        used = set(contests.select("scheme", "code").unique().iter_rows())
        for (scheme, code), (display, geom, _) in sorted(made.items()):
            if f"{scheme}:{code}" in polities or (scheme, code) not in used:
                continue
            st = next(row["state"] for row in listed if row["county_fips"][:2] == code[:2])
            prefix = {
                "us.town": "town",
                "us.place": "place",
                "us.school_district": "school",
                "us.special_district": "special",
            }[scheme]
            polity = cs.key("us.polity", f"{prefix}:{code}")
            county_path = polities[f"us.county:{code[:5]}"][2] if f"us.county:{code[:5]}" in polities else None
            path = (
                f"{county_path}/{slug(display)}"
                if scheme == "us.special_district"
                else f"us/{st.lower()}/{slug(display)}"
            )
            for suffix in (
                "",
                f"-{slug(polities[f'us.county:{code[:5]}'][1])}" if scheme == "us.town" and county_path else "",
                "-township" if scheme == "us.town" else "-village",
            ):
                if slugs.get(f"{path}{suffix}", polity) == polity:
                    path = f"{path}{suffix}"
                    break
            else:
                print(f"{display} would take {path}; skipped")
                continue
            slugs[path] = polity
            polities[f"{scheme}:{code}"] = (polity, display, path)
            if polity not in cs.minted:
                continue
            cs.add(
                "polity",
                id=polity,
                kind={"us.school_district": "school_district", "us.special_district": "special_district"}.get(
                    scheme, "municipality"
                ),
                iso_code=None,
            )
            name(polity, display)
            parent = polities[f"us.county:{code[:5]}"][0] if scheme == "us.special_district" else cs.polity(f"US-{st}")
            cs.add("polity_parent", polity_id=polity, parent_id=parent)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, code)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=cs.area("us.county", code[:5])
                if scheme in ("us.town", "us.special_district")
                else cs.area("us.state", code[:2]),
            )
            name(area, f"{display}, {st}")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            if geom is not None:
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    geom=geom,
                    label_point=None,
                    quality="official",
                    source_id=census,
                )
            cs.add(
                "reporting_unit",
                id=cs.unit(scheme, code),
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        for scheme, code in used:
            held = polities.get(f"{scheme}:{code}")
            if (
                code[:2] in ("34", "36", "39", "42")
                and held is not None
                and held[0] not in cs.minted
                and cs.area(scheme, code) not in cs.minted
                and cs.unit(scheme, code) in cs.minted
            ):
                cs.add(
                    "reporting_unit",
                    id=cs.unit(scheme, code),
                    polity_id=held[0],
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=cs.area(scheme, code),
                    kind="standard",
                    valid_from=None,
                )
        firsts: dict[str, list[tuple[date, str]]] = {}
        for key, day, runoff in contests.select("contest_key", "day", "runoff").unique().iter_rows():
            if not runoff:
                firsts.setdefault(key.split(":", 1)[1], []).append((day, key))
        previous: dict[str, str] = {}
        for key, day in contests.filter("runoff").select("contest_key", "day").unique().iter_rows():
            base = key.split(":", 1)[1].replace(":runoff", "", 1)
            earlier = [entry for entry in firsts.get(base, []) if day - timedelta(days=120) <= entry[0] < day]
            if earlier:
                previous[key] = max(earlier)[1]
        advancing: dict[str, set[str]] = {}
        for key, first in previous.items():
            labels = lines.join(
                contests.filter(pl.col("contest_key") == key).select("county", "election", "contest"),
                on=["county", "election", "contest"],
            )
            advancing[first] = {
                slug(_title(label)) for label in labels["choice"].unique().to_list() if label != "Write-ins"
            }
        owners: set[UUID] = set()
        elections: set[UUID] = set()
        persons: set[UUID] = set()
        districts: set[str] = set()
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        values: list[tuple[str, str, str, int]] = []
        emitted: list[UUID] = []
        done: dict[str, UUID] = {}
        skipped: Counter[str] = Counter()
        for key, rows in contests.group_by("contest_key", maintain_order=True):
            contest_key = str(key[0])
            first = rows.row(0, named=True)
            scheme, code, office_key, seat, day, st = (
                first[k] for k in ("scheme", "code", "key", "seat", "day", "state")
            )
            question = office_key == "question"
            party, primary, runoff = first["party"], first["primary"], first["runoff"]
            if f"{scheme}:{code}" not in polities:
                skipped["no polity"] += 1
                continue
            polity, place, path = polities[f"{scheme}:{code}"]
            owner_key = f"{st}:{code}:{office_key}" + (f"-{party}" if office_key in PARTY_KEYS else "")
            aliases = {
                owner_key,
                f"{st}:{code}:"
                + {"commissioners-court": "commission", "council": "city-council"}.get(office_key, office_key),
            }
            if (
                contest_key in held
                or (not question and any((day, alias) in taken for alias in aliases))
                or (question and (day, code) in asked)
            ):
                skipped["held by another pipeline"] += 1
                continue
            shares = made[(scheme, code)][2] if (scheme, code) in made else {code[:5]: 1.0}
            carried = set(rows["county"].to_list())
            whole = sum(shares.values()) or 1.0
            missing = sorted(county for county, share in shares.items() if share >= 0.01 and county not in carried)
            completeness = 1.0 if not missing else round(sum(shares.get(county, 0.0) for county in carried) / whole, 3)
            if completeness < 0.5:
                skipped["under half its area reported"] += 1
                continue
            kind = (
                "primary"
                if primary and not runoff
                else "runoff"
                if runoff
                else "special"
                if first["special"]
                else "general"
                if day.month == 11 and day.day <= 8
                else "local"
            )
            suffix = ":primary" if kind == "primary" else ":runoff" if runoff else ""
            options_keys = [f"{day}:{st}{suffix}"] + (
                [] if kind == "primary" else [f"{day}:{st}", f"{day}:{st}:runoff", f"{day}:{st}:special"]
            )
            parent_key = next((option for option in options_keys if option in parents), options_keys[0])
            parent = cs.key("us.election", parent_key)
            if parent in cs.minted and parent not in elections:
                elections.add(parent)
                parents[parent_key] = kind
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity(f"US-{st}"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    parent,
                    f"{day.year} {STATE_NAMES[st]} "
                    + {"general": "general election", "primary": "primary elections", "runoff": "runoff elections"}.get(
                        kind, f"{kind} elections"
                    ),
                )
                umbrella_path = f"us/{st.lower()}/{day}-{kind}"
                if slugs.get(umbrella_path, parent) != parent:
                    umbrella_path = f"{umbrella_path}-elections"
                slugs[umbrella_path] = parent
                cs.add("slug", path=umbrella_path, entity_id=parent)
            kind = parents.get(parent_key, kind) if kind not in ("primary", "runoff") else kind
            election = cs.key("us.election", f"{day}:{st}:{code}{suffix}")
            if (election in cs.minted or st == "IL" and day == date(2021, 4, 6)) and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind} election")
                if slugs.get(f"{path}/{day}-{kind}", election) == election:
                    slugs[f"{path}/{day}-{kind}"] = election
                    cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=election)
            whole_area = cs.area(scheme, code)
            area, unit = whole_area, cs.unit(scheme, code)
            label = str(first["label"] or first["text"])
            party_name = (party or "").capitalize()
            proposition = re.search(
                r"\b(?:PROPOSITION|PROP|REFERENDUM|QUESTION|MEASURE)\.? (?:NO\.? |#)?([A-Z0-9]{1,3})\b", label.upper()
            )
            contest_path = (
                f"{path}/{day}-{kind}/"
                + (
                    (f"proposition-{proposition[1].lower()}" if proposition else slug(label)[:60].rstrip("-"))
                    if question
                    else office_key
                )
                + (f"-{party}" if party else "")
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if first["remainder"] else "")
                + (f"-{first['variant']}" if first["variant"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            contest_path = next(
                (
                    candidate
                    for candidate in (contest_path, *(f"{contest_path}-{n}" for n in range(2, 10)))
                    if slugs.get(candidate, contest) == contest
                ),
                "",
            )
            if not contest_path:
                skipped["slug taken"] += 1
                continue
            slugs[contest_path] = contest
            if question:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=area,
                    electoral_system_id=systems["referendum"],
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                name(
                    contest,
                    f"{day.year} {place}: {_title(re.sub(r'\s*\((?:VOTE|Vote) [^)]*\)', '', str(first['text'])))}",
                )
            else:
                office_title = (
                    f"Mayor of {place}"
                    if office_key == "mayor"
                    else f"{place} {party_name} {first['office']}"
                    if office_key in PARTY_KEYS
                    else f"{place} {str(first['office']).removeprefix('County ')}"
                    if st == "NY" and place.endswith(" County")
                    else f"{place} {first['office']}"
                )
                owner = cs.key("us.body" if first["body"] else "us.office", owner_key)
                if owner in cs.minted and owner not in owners:
                    owners.add(owner)
                    if first["body"]:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add("office", id=owner, polity_id=polity, kind="mayor" if office_key == "mayor" else "other")
                    name(owner, office_title)
                if seat and (
                    seat.startswith(("Ward", "District", "Precinct", "Region", "Zone"))
                    or office_key in PARTY_KEYS
                    or office_key in OFFICER_KEYS
                    or re.fullmatch(r"\d{2}-\d-\d{2,3}", seat)
                ):
                    district = f"{code[:2]}:{code}:{office_key}:{slug(seat)}"
                    area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                    if area in cs.minted and district not in districts:
                        districts.add(district)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole_area, valid_from=None)
                        name(area, f"{place} {first['office']}, {seat}")
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=district,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
                magnitude = int(first["magnitude"]) or None
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if first["body"] else "office",
                    office_id=None if first["body"] else owner,
                    body_id=owner if first["body"] else None,
                    district_area_id=area,
                    party_scope_id=parties[party] if party else None,
                    magnitude=magnitude,
                    round_number=2 if runoff else 1,
                    previous_round_id=done.get(previous.get(contest_key, "")),
                    electoral_system_id=systems["fptp" if magnitude == 1 else "block"] if magnitude else None,
                    default_result_kind="counted",
                    term="remainder" if first["remainder"] else "full",
                )
                name(
                    contest,
                    (
                        f"{day.year} {office_title} primary"
                        if office_key in PARTY_KEYS
                        else f"{day.year} {party_name} primary for {office_title}"
                        if party
                        else f"{day.year} {office_title} election"
                    )
                    + (f", {seat}" if seat else "")
                    + (" (unexpired term)" if first["remainder"] else "")
                    + (f" ({first['variant'].replace('-', ' ')})" if first["variant"] else "")
                    + (" runoff" if runoff else ""),
                )
            cs.add("slug", path=contest_path, entity_id=contest)
            emitted.append(contest)
            done[contest_key] = contest
            result_set = cs.key(f"{source}.result_set", contest_key)
            main = max(carried, key=lambda county: shares.get(county, 0.0))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official" if all(stage == "official" for stage in rows["stage"].to_list()) else "preliminary",
                kind="counted",
                status="active",
                source_id=sources[main],
                is_default=True,
                completeness=completeness,
            )
            counted = pl.concat(
                [
                    grouped[(county, election_id, xml)]
                    for county, election_id, xml in rows.select("county", "election", "contest").iter_rows()
                ]
            )
            aliases: dict[tuple[str, str], str] = {}
            for county in carried:
                prefix = f"{day.year}:{st}:{county}:{county_names[county]}:"
                known: dict[tuple[str, str] | None, list[str]] = {}
                for code in precincts:
                    if code.startswith(prefix):
                        known.setdefault(_precinct_key(code[len(prefix) :]), []).append(code[len(prefix) :])
                for place_name in counted.filter(pl.col("county") == county)["place"].drop_nulls().unique():
                    if prefix + place_name not in precincts:
                        found = known.get(_precinct_key(place_name)) if known else None
                        if found is not None and len(found) == 1:
                            aliases[(county, place_name)] = found[0]
            if aliases:
                counted = (
                    counted.join(
                        pl.DataFrame(
                            [(county, place_name, alias) for (county, place_name), alias in aliases.items()],
                            schema=["county", "place", "alias"],
                            orient="row",
                        ),
                        on=["county", "place"],
                        how="left",
                    )
                    .with_columns(place=pl.coalesce("alias", "place"))
                    .drop("alias")
                )
            balanced = dict(zip(rows["county"].to_list(), rows["balanced"].to_list(), strict=True))
            precise: dict[str, bool] = {}
            for county in carried:
                prefix = f"{day.year}:{st}:{county}"
                names = (
                    counted.filter(
                        (pl.col("county") == county)
                        & pl.col("place").is_not_null()
                        & ~pl.col("place").str.contains(r"\((?:OUT OF COUNTY|[A-Z. ]+ COUNTY)\)\s*$")
                    )["place"]
                    .unique()
                    .to_list()
                )
                jurisdiction = county_names[county]
                precise[county] = bool(balanced[county]) and (
                    prefix not in foreign
                    or all(f"{prefix}:{jurisdiction}:{place_name}" in precincts for place_name in names)
                )
            named = " and ".join(sorted(publishers[county].split(",")[0] for county in carried))
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=(METHOD if all(precise.values()) else TOTALS).format(publishers=named),
            )
            if "official" not in rows["stage"].to_list():
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNOFFICIAL)
            if missing:
                lacking = " and ".join(
                    polities[f"us.county:{county}"][1] if f"us.county:{county}" in polities else county
                    for county in missing
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=PARTIAL.format(share=f"{completeness:.0%}", missing=lacking),
                )
            totals = (
                counted.filter(pl.col("place").is_null())
                .with_columns(same=pl.col("choice").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", ""))
                .group_by("same")
                .agg(pl.col("choice").first(), pl.col("party").drop_nulls().first(), pl.col("votes").sum())
                .sort("votes", "choice", descending=[True, False])
            )
            options: dict[str, UUID] = {}
            ranked: list[tuple[int, UUID, str]] = []
            for same, choice, choice_party, votes in totals.select("same", "choice", "party", "votes").iter_rows():
                if question:
                    answer = "yes" if re.match(r"(?i)\s*(?:yes|for|si|bonds? yes)\b", choice) else "no"
                    option = options[same] = cs.key("us.option", f"{contest_key}:{answer}")
                    if any(held == option for _, held, _ in ranked):
                        ranked = [
                            (total + votes if held == option else total, held, label) for total, held, label in ranked
                        ]
                        continue
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "yes" else 2,
                        ballot_label=answer.capitalize(),
                    )
                elif choice == "Write-ins":
                    options[same] = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add(
                        "contest_option",
                        id=options[same],
                        contest_id=contest,
                        kind="write_in",
                        ballot_label="Write-ins",
                    )
                    continue
                else:
                    ballot = _title(re.sub(r"\s*\((?:DEM|REP|IND|NON|NP|NPA|LIB|GRN|D|R)\)$", "", choice))
                    person = cs.key(f"{source}.person", f"{st}:{code}:{slug(ballot)}")
                    if person not in persons:
                        persons.add(person)
                        if person in cs.minted:
                            cs.add("person", id=person)
                        name(person, ballot)
                    option = options[same] = cs.key("us.option", f"{contest_key}:{slug(ballot)}")
                    tag = party or choice_party
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(tag) if tag else None,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(ballot)}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=False,
                    )
                ranked.append((votes, option, slug(_title(choice))))
            if not missing and ranked:
                if question:
                    if len(ranked) == 2 and ranked[0][0] != ranked[1][0]:
                        cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won")
                elif contest_key in advancing:
                    for _, option, person_slug in ranked:
                        if person_slug in advancing[contest_key]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced")
                else:
                    cut = int(first["magnitude"])
                    total = sum(votes for votes, _, _ in ranked)
                    majority = st != "TX" or runoff or cut > 1 or ranked[0][0] * 2 > total
                    if cut > 0 and majority and (len(ranked) <= cut or ranked[cut - 1][0] != ranked[cut][0]):
                        for _, option, _ in ranked[:cut]:
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=option,
                                kind="won" if party else "elected",
                                seats=1,
                            )
            for county in carried:
                part = counted.filter(pl.col("county") == county).with_columns(
                    same=pl.col("choice").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", "")
                )
                if not precise[county]:
                    target = unit if len(carried) == 1 and not missing else county_units[county][0]
                    for same, votes in (
                        part.filter(pl.col("place").is_null()).group_by("same").agg(pl.col("votes").sum()).iter_rows()
                    ):
                        values.append((str(result_set), str(target), str(options[same]), votes))
                    continue
                prefix = f"{day.year}:{st}:{county}:{county_names[county]}"
                for same, place_name, votes in (
                    part.filter(pl.col("place").is_not_null())
                    .group_by("same", "place")
                    .agg(pl.col("votes").sum())
                    .iter_rows()
                ):
                    place_code = f"{prefix}:{place_name}"
                    target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                    if place_code not in precincts:
                        minted.setdefault(place_code, (county, day))
                    memberships.add((target, area, day))
                    values.append((str(result_set), str(target), str(options[same]), votes))
        print(
            f"{len(emitted)} contests emitted; skipped: "
            + ", ".join(f"{reason} {count}" for reason, count in skipped.most_common())
        )
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
        for row in conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                join changeset g on g.id = r.changeset_id
                join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                where g.pipeline = %s and r.status = 'active' and r.contest_id <> all(%s)
                  and split_part(x.value, ':', 2) = any(%s)""",
            [source, emitted, states],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="withdrawn")
            cs.add("note", entity_id=row[0], kind="caveat", body_md=WITHDRAWN)
        states_of = {row["county_fips"]: row["state"] for row in listed}
        for place_code, (county, day) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=cs.polity(f"US-{states_of[county]}"),
                scheme_code="us.precinct",
                code=place_code,
                parent_id=county_units[county][0],
                area_relation="within",
                area_id=county_units[county][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area", unit_id=target, area_id=area, valid_from=day, valid_to=day + timedelta(days=1)
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            )
            .group_by("result_set_id", "unit_id", "option_id", maintain_order=True)
            .agg(pl.col("value").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
