import csv
import io
import json
import re
import time
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta
from xml.etree import ElementTree

import httpx
import polars as pl
import shapefile

from ea import portals, raw
from ea.changeset import slug
from ea.store import DATA, connect

SOURCE = "us.enr_clarity"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "results.enr.clarityelections.com"
CENSUS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{fips}_{layer}_500k.zip"
LAYERS = ("place", "cousub", "unsd")
TITLE = "Election night reporting results"
STATE_NAMES = {"TX": "Texas", "PA": "Pennsylvania"}
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
    r"STATE PROP|STATE OF TEXAS|CONSTITUTIONAL|AMENDMENT|STRAIGHT|SECRETARY OF|AUDITOR GENERAL|STATE TREASURER|"
    r"(?<!MAGISTERIAL )\bDIST(?:RICT)? (?:CT )?JUDGE|JUDIC(?:IAL)? DIST|RETENTION|RETAIN|DELEGATE|NATIONAL COMMITTEE|"
    r"STATE CONVENTION|REFERENDA|REFERENDUM ITEM|BALLOTS CAST|REGISTERED VOTERS|TURNOUT|STATEWIDE|\bTEST\b"
)
QUESTION = re.compile(r"(?i)\s*(?:yes|no|for|against|si|bonds? yes|bonds? no)\b")
WRITE_IN = re.compile(r"(?i)\s*(?:unresolved |unassigned |qualified |uncertified )?write|scatter|personal choice")
VACANT = re.compile(r"(?i)\s*(?:no (?:nomination|candidate)|over ?votes|under ?votes|blank|unopposed)")
REMAINDER = re.compile(r"\bUNEX\w*|\bUNXP\w*|SHORT TERM|VACANCY|REMAINDER|PARTIAL TERM|TO FILL\b|\bFILL\b")
TERM = re.compile(r"\b(\d+|ONE|TWO|THREE|FOUR|FIVE|SIX)[ -]?(?:YR|YEAR)S?\b(?: TERM)?")
PARTY_OFFICES = (
    (r"PRECINCT CHAIR\w*|PCT CHAIR\w*|PRECINCT COMMITTEE CHAIR\w*", "precinct-chair", "Precinct Chair"),
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
    "Votes are the {publishers} election night reporting results by precinct, summed across the ways of voting each "
    "county reports. Every candidate's precinct votes add up to the county's printed total."
)
TOTALS = (
    "Votes are the {publishers} election night reporting results, counted as one total per county, because a "
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


def tenants() -> list[dict[str, str]]:
    with portals.REGISTRY.open() as file:
        return [
            row
            for row in csv.DictReader(file)
            if row["platform"] == "clarity" and row["status"] == "listed" and row["tenant"].startswith("https://")
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
        return text
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


def _shapes(fips: str, layer: str) -> list[tuple[dict[str, object], str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, CENSUS.format(fips=fips, layer=layer)).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    return [
        (record.as_dict(), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]


def parse() -> None:
    registry = {row["tenant"]: row for row in tenants()}
    files: dict[tuple[str, str], tuple[int, str]] = {}
    for url in raw._index(SOURCE)[1]:
        found = re.fullmatch(r"(.+/)(\d+)/(\d+)/reports/detailxml\.zip", url)
        if found and found[1] in registry and int(found[3]) > files.get((found[1], found[2]), (0, ""))[0]:
            files[(found[1], found[2])] = (int(found[3]), url)
    states = sorted({row["county_fips"][:2] for row in registry.values()})
    jurisdictions: dict[tuple[str, str], tuple[str, str, str]] = {}
    geoms: list[tuple[str, str, str]] = []
    for fips in states:
        for record, geom in _shapes(fips, "cousub"):
            proper, whole = str(record["NAME"]), str(record["NAMELSAD"])
            kind = whole[len(proper) :].strip().lower()
            if kind not in ("township", "borough", "town", "city"):
                continue
            display = f"{proper} Township" if kind == "township" else proper
            jurisdictions[("us.town", str(record["GEOID"]))] = (display, kind, proper)
            geoms.append(("us.town", str(record["GEOID"]), geom))
        for record, geom in _shapes(fips, "place"):
            lsad = str(record["LSAD"])
            if lsad not in ("21", "25", "43", "47"):
                continue
            kind = {"21": "borough", "25": "city", "43": "town", "47": "village"}[lsad]
            jurisdictions[("us.place", str(record["GEOID"]))] = (str(record["NAME"]), kind, str(record["NAME"]))
            geoms.append(("us.place", str(record["GEOID"]), geom))
        for record, geom in _shapes(fips, "unsd"):
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
    for (tenant, election), (_, link) in sorted(files.items()):
        row = registry[tenant]
        county, st = row["county_fips"], row["state"]
        county_name = row["county"].upper()
        with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        day = datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date()
        title = re.sub(r"\s+", " ", root.findtext("ElectionName") or "").strip()
        heading = _plain(title)
        if re.search(r"\bTEST\b|MOCK|PRACTICE|SAMPLE", heading):
            continue
        primary = "PRIMARY" in heading
        runoff = "RUNOFF" in heading or "RUN OFF" in heading
        heading_party = next((PARTIES[word] for word in heading.split() if word in PARTIES), None)
        stage = "official" if re.search(r"(?<!UN)OFFICIAL|CERTIFIED|CANVASS", heading) else "preliminary"
        names = sorted({entry[0] for entry in munis.get(county, [])}, key=len, reverse=True)
        school_names = sorted(schools.get(county, []), key=lambda entry: len(entry[0]), reverse=True)
        for node in root.iter("Contest"):
            text = re.sub(r"\s+", " ", node.get("text") or "").strip()
            plain = _plain(text)
            plain = re.sub(rf"\b{re.escape(county_name)} COUNTYWIDE\b", "", plain).strip()
            choices = [
                choice
                for choice in node.iter("Choice")
                if not VACANT.match(choice.get("text") or "") and (choice.get("totalVotes") or "0").isdigit()
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
            if SKIPPED.search(plain):
                skipped["state or federal"] += 1
                continue
            labels = [choice.get("text") or "" for choice in choices if not WRITE_IN.match(choice.get("text") or "")]
            if not labels:
                skipped["write-ins only"] += 1
                continue
            question = bool(labels) and all(QUESTION.match(label) for label in labels)
            if primary:
                tagged = Counter(_party(choice.get("party")) for choice in choices if choice.get("party"))
                party = party or heading_party or next((key for key, _ in tagged.most_common() if key), None)
                if party is None or question:
                    skipped["primary question" if question else "primary with no party"] += 1
                    continue
            else:
                party = None
            remainder = REMAINDER.search(plain) is not None
            term = TERM.search(plain)
            found = _classify(plain, st, county, county_name, names, school_names, munis.get(county, []))
            if found is None and (prefix := _prefix(heading, plain, names, school_names)):
                found = _classify(
                    f"{prefix} {plain}", st, county, county_name, names, school_names, munis.get(county, [])
                )
                found = found if found and found["scheme"] != "us.county" else None
            if found is None:
                skipped[re.sub(r"\d+", "N", plain)[:70]] += 1
                continue
            if question:
                found = {**found, "key": "question", "office": "", "body": False, "seat": None}
            elif found["key"] == "question":
                skipped["question with candidates"] += 1
                continue
            rows = []
            for choice in choices:
                label = re.sub(r"\s+", " ", choice.get("text") or "").strip()
                tag = _party(choice.get("party"))
                if (
                    WRITE_IN.match(label)
                    or primary
                    and party
                    and tag is None
                    and any(_party(other.get("party")) for other in choices)
                ):
                    label = "Write-ins"
                for kind_node in choice.iter("VoteType"):
                    for place in kind_node.iter("Precinct"):
                        votes = place.get("votes") or "0"
                        rows.append(
                            {
                                "county": county,
                                "election": election,
                                "contest": node.get("key"),
                                "choice": label,
                                "party": tag,
                                "place": re.sub(r"\s+", " ", (place.get("name") or "").upper()).strip(),
                                "votes": int(votes) if votes.isdigit() else 0,
                            }
                        )
                rows.append(
                    {
                        "county": county,
                        "election": election,
                        "contest": node.get("key"),
                        "choice": label,
                        "party": tag,
                        "place": None,
                        "votes": int(choice.get("totalVotes") or 0),
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
                    "contest": node.get("key"),
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
                    "magnitude": int(node.get("voteFor") or 1),
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
        .agg((pl.col("votes") == pl.col("counted")).all().alias("balanced"))
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
    for (_, _), rows in table.group_by("tenant", "election"):
        for _, group in rows.group_by("base"):
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
        pl.DataFrame(variants, schema=["tenant", "election", "contest", "variant"], orient="row"),
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
    PARSED.mkdir(parents=True, exist_ok=True)
    table.drop("repeated").write_parquet(PARSED / "contests.parquet")
    frame.join(table.select("county", "election", "contest"), on=["county", "election", "contest"]).write_parquet(
        PARSED / "lines.parquet"
    )
    shapes = {(scheme, code): geom for scheme, code, geom in geoms}
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
    ).write_parquet(PARSED / "polities.parquet")


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


def _classify(
    plain: str,
    st: str,
    county: str,
    county_name: str,
    names: list[str],
    school_names: list[tuple[str, str]],
    listed: list[tuple[str, str, str, str]],
) -> dict[str, object] | None:
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
    plain = re.sub(rf"\b{re.escape(county_name)} COUNTY\b", "COUNTY", plain)
    label = re.sub(r"\s+", " ", plain).strip()
    if SCHOOL.search(plain) and school_names:
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
                return {
                    "scheme": "us.school_district",
                    "code": code,
                    "key": "board-of-education",
                    "office": "Board of Trustees" if st == "TX" else "Board of School Directors",
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
    for office in COUNTY_OFFICES:
        found = re.search(rf"\b(?:{office[0]})\b", plain)
        if found and not any(found.start() < end and start < found.end() for start, end in spans):
            held.append(office)
            spans.append(found.span())
    if held:
        seat, rest = _seat(plain)
        _, key, office, body = held[0]
        if key == "court-of-common-pleas":
            seat, rest = None, re.sub(r"\d+", " ", rest)
        if seat is None and (bare := re.search(r"\b(\d+)\b(?! ?(?:YR|YEAR))", rest)):
            noun = "Court" if "court" in key else "Precinct"
            seat, rest = f"{noun} {int(bare[1])}", re.sub(rf"\b{bare[1]}\b", " ", rest)
        if _residue(rest, *(office[0] for office in held), r"JUDGE|COURT|CT|CRT|LAW|PCT|PRECINCT|COMM|CRIMINAL"):
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
                if _residue(rest, pattern):
                    return None
                return _county(st, county, key, office, body, seat, label)
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
    for pattern, key, office, body in MUNI_OFFICES:
        if re.search(rf"\b(?:{pattern})\b", rest):
            break
    else:
        if seat is None or kind == "township":
            return None
        pattern, key, office, body = "", "council", "Council", True
    if _residue(rest, pattern, "JUDGE") if pattern else _residue(rest):
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
