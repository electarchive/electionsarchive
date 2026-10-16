import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.medsl_state import DROPPED, EXPANDED, INDEPENDENT, NATIONAL, POOLED, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.medsl_local"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
LISTING = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/NYTPDU"
LISTING2022 = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/UYQIEP"
LISTING2020 = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/NT66Z3"
ACCESS = "https://dataverse.harvard.edu/api/access/datafile"
LOCAL2016 = f"{ACCESS}/3345356?format=original"
LOCAL2018 = f"{ACCESS}/6692669"
OPENELECTIONS = (
    "https://raw.githubusercontent.com/openelections/openelections-data-{state}/{commit}/{day:%Y}/"
    "{day:%Y%m%d}__{state}__general__precinct.csv"
)
OPENELECTIONS_FILES = {
    ("IN", date(2020, 11, 3)): "b130b78cd7b55e57e766623772424f60c0e640bd",
    ("MN", date(2020, 11, 3)): "589a76681b2b1a8c23162a90fcc225eb066e23f5",
    ("NV", date(2020, 11, 3)): "109e5c7d6be95f15c6bad5194b415ebcdf75d7fc",
    ("TX", date(2020, 11, 3)): "c7185327aa987ecc23fefdde0ee53f5238f5da70",
    ("WV", date(2020, 11, 3)): "49d32c279d0e02d740c8af4fcb5b5eee380fbed4",
    ("FL", date(2022, 11, 8)): "cc8116224e23e9342658eda5d9b0b76cbf3c2912",
    ("MN", date(2022, 11, 8)): "589a76681b2b1a8c23162a90fcc225eb066e23f5",
    ("NV", date(2022, 11, 8)): "109e5c7d6be95f15c6bad5194b415ebcdf75d7fc",
    ("SD", date(2022, 11, 8)): "9e01b9aea628e9ae17ada6656b649daf319fd212",
    ("TX", date(2022, 11, 8)): "c7185327aa987ecc23fefdde0ee53f5238f5da70",
    ("UT", date(2022, 11, 8)): "a023ab4d87c4a93f0913f505c6fcbad1a585db57",
    ("WV", date(2022, 11, 8)): "49d32c279d0e02d740c8af4fcb5b5eee380fbed4",
}
OPENELECTIONS_PARTIES = {
    "DEM": "DEMOCRAT",
    "REP": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "GRN": "GREEN",
    "GRE": "GREEN",
    "IND": "INDEPENDENT",
    "CON": "CONSTITUTION",
}
DATASETS = {
    date(2024, 11, 5): ("Precinct-Level Returns 2024 by Individual State", "https://doi.org/10.7910/DVN/NYTPDU"),
    date(2022, 11, 8): ("Precinct-Level Returns 2022 by Individual State", "https://doi.org/10.7910/DVN/UYQIEP"),
    date(2020, 11, 3): ("Precinct-Level Returns 2020 by Individual State", "https://doi.org/10.7910/DVN/NT66Z3"),
    date(2018, 11, 6): ("Local Precinct-Level Returns 2018", "https://doi.org/10.7910/DVN/CHYXUP"),
    date(2016, 11, 8): ("Local Precinct-Level Returns 2016", "https://doi.org/10.7910/DVN/Q8OHRS"),
}
COUNTS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7}
COVERED = {"AK", "CA", "CT", "DC", "ID", "NC", "NH", "NM", "VA", "VT"}
COUNTY = r"(?:COUNTY|PARISH)"
OFFICES = [
    ("sheriff", "Sheriff", False, r"(?:HIGH )?SHERIFF"),
    ("coroner", "Coroner", False, r"CORONER"),
    ("medical-examiner", "Medical Examiner", False, r"MEDICAL EXAMINER"),
    (
        "commission-chair",
        "Commission Chair",
        False,
        (
            r"CHAIR(?:MAN|PERSON)?,? (?:OF )?(?:THE )?(?:COUNTY )?COMMISSION|(?:COUNTY )?COMMISSION(?:ERS?)? CHAIR(?:MAN|PERSON)?"
            r"|CHAIR(?:MAN|PERSON)? (?:OF )?(?:THE )?BOARD OF (?:COUNTY )?COMMISSIONERS|BOARD OF COMMISSIONERS CHAIR(?:MAN)?"
        ),
    ),
    (
        "council-president",
        "Council President",
        False,
        r"(?:COUNTY |CITY )?COUNCIL PRESIDENT|PRESIDENT,? (?:OF )?(?:THE )?(?:COUNTY |CITY )?COUNCIL",
    ),
    ("council-chair", "Council Chair", False, r"COUNTY (?:COUNCIL )?CHAIR(?:MAN|PERSON)?"),
    ("sole-commissioner", "Sole Commissioner", False, r"SOLE COMMISSION(?:ER)?"),
    ("license-commissioner", "License Commissioner", False, r"(?:COUNTY )?LICENSE COMMISSIONER"),
    ("election-commission", "Election Commission", True, r"(?:COUNTY )?ELECTION COMMISSION(?:ERS?)?"),
    (
        "commission",
        "Commission",
        True,
        r"(?:COUNTY )?COMMISS?IO?N(?:ERS?)?|(?:MEMBERS? OF )?(?:THE )?BOARD OF (?:COUNTY )?COMMISSIONERS?",
    ),
    ("board-of-supervisors", "Board of Supervisors", True, r"(?:COUNTY )?(?:BOARD OF )?SUPERVISORS?"),
    ("council", "Council", True, r"COUNTY COUNCIL(?: ?(?:MEMBERS?|MAN|WOMAN|PERSON|ORS?))?|COUNTY COUNCILOR"),
    ("legislature", "Legislature", True, r"COUNTY LEGISLA(?:TURE|TORS?)"),
    ("county-board", "Board", True, r"COUNTY BOARD(?: MEMBERS?)?"),
    ("quorum-court", "Quorum Court", True, r"QUORUM COURT(?: JUSTICES?)?"),
    ("justice-of-the-peace", "Justice of the Peace", False, r"JUSTICES? OF THE PEACE"),
    ("magistrate", "Magistrate", False, r"MAGISTRATE"),
    ("constable", "Constable", False, r"CONSTABLE"),
    ("executive", "Executive", False, r"COUNTY EXECUTIVE|EXECUTIVE"),
    ("chief-executive-officer", "Chief Executive Officer", False, r"CHIEF EXECUTIVE OFFICER"),
    ("chief-executive", "Chief Executive", False, r"CHIEF EXECUTIVE"),
    ("mayor", "Mayor", False, r"COUNTY MAYOR"),
    ("judge-executive", "Judge/Executive", False, r"COUNTY JUDGE ?[/-] ?EXECUTIVE"),
    ("county-judge", "County Judge", False, r"COUNTY JUDGE"),
    ("probate-judge", "Probate Judge", False, r"JUDGE OF PROBATE|PROBATE JUDGE"),
    ("register-of-probate", "Register of Probate", False, r"REGISTER OF PROBATE"),
    ("register-of-wills", "Register of Wills", False, r"REGISTER OF WILLS"),
    ("surrogate", "Surrogate", False, r"SURROGATE"),
    (
        "circuit-clerk",
        "Circuit Clerk",
        False,
        r"CIRCUIT (?:COURT )?CLERK(?: (?:AND|&) RECORDER)?|CLERK OF (?:THE )?CIRCUIT COURT(?: (?:AND|&) COMPTROLLER)?",
    ),
    (
        "superior-court-clerk",
        "Clerk of Superior Court",
        False,
        r"SUPERIOR COURT CLERK|CLERK OF (?:THE )?SUPERIOR COURT",
    ),
    ("state-court-clerk", "Clerk of State Court", False, r"STATE COURT CLERK|CLERK OF (?:THE )?STATE COURT"),
    ("common-pleas-clerk", "Clerk of the Court of Common Pleas", False, r"CLERK OF (?:THE )?COURT OF COMMON PLEAS"),
    (
        "district-court-clerk",
        "Clerk of the District Court",
        False,
        r"(?:COUNTY )?CLERK OF (?:THE )?DISTRICT COURT|DISTRICT COURT CLERK",
    ),
    ("clerk-of-courts", "Clerk of Courts", False, r"(?:COUNTY )?CLERK OF (?:THE )?COURTS?|COURT CLERK"),
    ("county-and-district-clerk", "County and District Clerk", False, r"COUNTY (?:AND|&) DISTRICT CLERK"),
    ("district-clerk", "District Clerk", False, r"DISTRICT CLERK"),
    ("clerk-of-the-peace", "Clerk of the Peace", False, r"CLERK OF THE PEACE"),
    ("clerk-and-recorder", "Clerk and Recorder", False, r"(?:COUNTY )?CLERK (?:AND|&) RECORDER"),
    ("supervisor-of-elections", "Supervisor of Elections", False, r"SUPERVISOR OF ELECTIONS"),
    ("clerk", "Clerk", False, r"COUNTY CLERK|CLERK"),
    ("treasurer", "Treasurer", False, r"(?:COUNTY )?TREASURER"),
    ("fiscal-officer", "Fiscal Officer", False, r"(?:COUNTY )?FISCAL OFFICER"),
    ("tax-assessor-collector", "Tax Assessor-Collector", False, r"TAX ASSESSOR[- ]COLLECTOR"),
    ("tax-collector", "Tax Collector", False, r"(?:COUNTY )?TAX COLLECTOR"),
    ("tax-commissioner", "Tax Commissioner", False, r"TAX COMMISSIONER"),
    ("revenue-commissioner", "Revenue Commissioner", False, r"REVENUE COMMISSIONER"),
    ("property-appraiser", "Property Appraiser", False, r"PROPERTY APPRAISER"),
    (
        "property-valuation-administrator",
        "Property Valuation Administrator",
        False,
        r"PROPERTY VALUATION ADMINISTRATOR",
    ),
    ("assessor", "Assessor", False, r"(?:COUNTY )?(?:TAX )?ASSESSOR"),
    ("register-of-deeds", "Register of Deeds", False, r"REGISTER OF DEEDS"),
    ("recorder-of-deeds", "Recorder of Deeds", False, r"RECORDER OF DEEDS"),
    ("register", "Register", False, r"REGISTER"),
    ("recorder", "Recorder", False, r"(?:COUNTY )?RECORDER"),
    ("auditor-and-recorder", "Auditor and Recorder", False, r"(?:COUNTY )?AUDITOR (?:AND|&) RECORDER"),
    ("auditor", "Auditor", False, r"(?:COUNTY )?AUDITOR"),
    ("comptroller", "Comptroller", False, r"(?:COUNTY )?COMPTROLLER"),
    ("prosecuting-attorney", "Prosecuting Attorney", False, r"PROSECUTING ATTORNEY"),
    ("states-attorney", "State's Attorney", False, r"STATE'?S ATTORNEY"),
    ("district-attorney", "District Attorney", False, r"DISTRICT ATTORNEY"),
    ("county-attorney", "County Attorney", False, r"(?:COUNTY )?ATTORNEY"),
    ("prosecutor", "Prosecutor", False, r"(?:COUNTY )?PROSECUTOR"),
    ("solicitor", "Solicitor", False, r"SOLICITOR(?:[- ]GENERAL)?"),
    ("public-defender", "Public Defender", False, r"PUBLIC DEFENDER"),
    ("public-administrator", "Public Administrator", False, r"PUBLIC ADMINISTRATOR"),
    ("surveyor", "Surveyor", False, r"(?:COUNTY )?SURVEYOR"),
    ("engineer", "Engineer", False, r"(?:COUNTY )?ENGINEER"),
    ("jailer", "Jailer", False, r"(?:COUNTY )?JAILER"),
    (
        "superintendent-of-schools",
        "Superintendent of Schools",
        False,
        (
            r"(?:COUNTY )?(?:SCHOOL )?SUPERINTENDENT(?: OF (?:PUBLIC )?(?:SCHOOLS|EDUCATION|PUBLIC INSTRUCTION))?"
            r"(?:,? (?:OF )?(?:THE )?(?:COUNTY )?BOARD OF EDUCATION)?"
        ),
    ),
    (
        "board-of-education-chair",
        "Board of Education Chair",
        False,
        r"(?:COUNTY )?BOARD OF EDUCATION CHAIR(?:MAN|PERSON)?",
    ),
    (
        "board-of-education",
        "Board of Education",
        True,
        r"(?:COUNTY )?(?:BOARD OF EDUCATION|SCHOOL BOARD)(?: MEMBERS?| TRUSTEES?)?",
    ),
    (
        "soil-and-water",
        "Soil and Water Conservation District",
        True,
        (
            r"(?:(?:COUNTY )?SOIL (?:AND |& )?(?:WATER )?)?CONSERVATION(?: DISTRICT| DIST\.?)?"
            r"(?: (?:BOARD )?(?:SUPERVISORS?|DIRECTORS?|COMMISSIONERS?|BOARD))?"
            r"|(?:COUNTY )?SOIL (?:AND|&) WATER(?: (?:SUPERVISORS?|DIRECTORS?|COMMISSIONERS?))?"
        ),
    ),
]
UNIFIED = [
    ("mayor", "Mayor", False, r"(?:CITY )?MAYOR"),
    ("city-council", "City Council", True, r"(?:CITY )?COUNCIL ?(?:MEMBERS?|MAN|WOMAN|PERSON)?|COUNCILMEMBER"),
]
TOWNSHIPS = {"IL", "MA", "ME", "MI", "MN", "NJ", "NY", "PA", "RI", "WI"}
COUNTYWIDE = {
    "sheriff",
    "coroner",
    "register-of-probate",
    "register-of-wills",
    "surrogate",
    "district-attorney",
    "probate-judge",
    "soil-and-water",
    "county-and-district-clerk",
}
SCHOOLS = {"AL", "FL", "GA", "KY", "LA", "MD", "MS", "NV", "SC", "TN", "WV"}
SEAT = re.compile(
    r"(?:(?:DIST(?:RICT)?|PRECINCT|PCT|PLACE|POST|POSITION|POS|SEAT|WARD|ZONE|DIVISION|BEAT|AREA|NO|NUMBER|#)\.? ?#? ?[A-Z]?\d+[A-Z]?"
    r"|\d+(?:ST|ND|RD|TH)? (?:DIST(?:RICT)?\.?|PRECINCT|WARD|ZONE|DIVISION|BEAT|[A-Z]+ DISTRICT)|\d+|[A-Z]"
    r"|[A-Z][A-Z .'-]* (?:TOWNSHIP|DISTRICT|PRECINCT|DIVISION|WARD)(?: \d+)?|AT[- ]LARGE(?: [A-Z0-9])?"
    r"|NORTH|SOUTH|EAST|WEST|CENTRAL|TERM (?:COMMENCING|BEGINNING) \d\d?/\d\d?/\d{4})"
)
BANNED = re.compile(r"SCHOOL|INDEPENDENT|CITY|TOWN\b|VILLAGE|COLLEGE|JUDICIAL|CIRCUIT|SUPERVISOR|MEMBER|BOARD|DIRECTOR")
REMAINDER = re.compile(
    r",? ?-? ?\(?(?:UNEXPIRED(?: TERM(?: ENDING [\d/]+)?)?|TO FILL (?:A |THE )?VACANCY|SHORT TERM|SPECIAL)\)?$"
)
VACANT = {"NO CANDIDATE FILED", "NO CANDIDATE", "NO CANDIDATES", "NO NOMINATION", "VACANT", "NONE"}
EXCLUDED = re.compile(
    r"RECALL|PRESIDENT|CHAIR|VICE|JUDGE|CLERK|TREASURER|ATTORNEY|AUDITOR|BOARD|SCHOOL|QUESTION|PROPOSITION|AMENDMENT"
    r"|CHARTER|MEASURE|COUNTY|TOWNSHIP|ADVISORY|SHALL"
)
NONPARTISAN = {"NONPARTISAN", "NON-PARTISAN", "NP", "OTHER", '""'}
UNAFFILIATED = {"NO", "INDEPENDENT-UNENROLLED", "NO PARTY PREFERENCE"}
METHOD = (
    "Totals are the sums of the precinct rows in MEDSL's 2024 precinct file for the state. The file does not mark "
    "winners, so the winners shown are the candidates with the most votes, as many as the contest elects."
)
CONTRIBUTED = (
    "Totals are the sums of the precinct rows in OpenElections' precinct file for the state, which its volunteers "
    "transcribed from county results. The file does not mark winners, so the winners shown are the candidates with "
    "the most votes, as many as the contest elects."
)
MASKED = (
    "Some precincts in the source file hide small vote counts to protect voter privacy; "
    "these totals run slightly below the certified results."
)
DISTRICT = (
    "Only part of the county votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)
TOWNS = {
    "IA": "19",
    "IL": "17",
    "IN": "18",
    "KS": "20",
    "MA": "25",
    "ME": "23",
    "MI": "26",
    "MN": "27",
    "MO": "29",
    "ND": "38",
    "NE": "31",
    "NJ": "34",
    "NY": "36",
    "OH": "39",
    "PA": "42",
    "RI": "44",
    "SD": "46",
    "WI": "55",
}
WHOLE = {"MA", "ME", "NJ", "RI"}
SUBDIVISIONS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{}_cousub_500k.zip"
INCORPORATED = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{}_place_500k.zip"
KINDS = {"township", "charter township", "town", "borough", "village", "city", "plantation"}
SUFFIXED = {"township", "charter township", "borough", "village", "plantation"}
MUNICIPAL = [
    ("mayor", "Mayor", False, r"MAYOR"),
    ("school-committee", "School Committee", True, r"SCHOOL COMMITTEE(?: MEMBERS?)?"),
    (
        "board-of-education",
        "Board of Education",
        True,
        (
            r"(?:MEMBERS? (?:OF|TO) |MEMBERSHIP TO )?(?:THE )?(?:LOCAL )?(?:BOARD OF ED(?:UCATION)?|BOE|SCHOOL BOARD)"
            r"(?: MEMBERS?)?"
        ),
    ),
    (
        "committee",
        "Committee",
        True,
        r"(?:MEMBERS? (?:OF )?(?:THE )?)?COMMITTEE(?: ?(?:MEMBERS?|M[AE]N|WOMAN|PERSONS?))?",
    ),
    (
        "council",
        "Council",
        True,
        (
            r"(?:MEMBERS? (?:OF )?(?:THE )?)?(?:COMMON )?COUNCIL(?: ?(?:MEMBERS?|M[AE]N|WOMAN|PERSONS?|ORS?))?"
            r"|COUNCIL(?:MEMBER|MAN|PERSON|OR)S?"
        ),
    ),
    ("board", "Board", True, r"(?:MEMBERS? (?:OF )?(?:THE )?)?BOARD(?: MEMBERS?)?"),
    ("board-of-trustees", "Board of Trustees", True, r"(?:BOARD OF )?TRUSTEES?"),
    ("supervisor", "Supervisor", False, r"SUPERVISOR"),
    ("justice", "Justice", False, r"JUSTICE"),
    ("clerk-treasurer", "Clerk-Treasurer", False, r"CLERK ?[-/] ?TREASURER|CLERK (?:AND|&) TREASURER"),
    ("clerk", "Clerk", False, r"CLERK"),
    ("treasurer", "Treasurer", False, r"TREASURER"),
    (
        "highway-superintendent",
        "Superintendent of Highways",
        False,
        r"(?:SUPERINTENDENT|SUPT) OF HIGHWAYS|HIGHWAY SUPERINTENDENT",
    ),
    ("assessor", "Assessor", False, r"ASSESSORS?"),
    ("tax-collector", "Tax Collector", False, r"TAX COLLECTOR|RECEIVER OF TAXES"),
    ("moderator", "Moderator", False, r"MODERATOR"),
    ("administrator", "Administrator", False, r"ADMINISTRATOR"),
]
UNLISTED = re.compile(
    r"QUESTION|PROPOSITION|MEASURE|FIRE|LIBRARY|REGIONAL|CONSOLIDATED|SHALL|OPEN SPACE|\b\d{4} TERM|RECALL|DEPUTY"
    r"|PRO TEM|JUDGE|COURT|COUNTY|CHAIR|PRESIDENT"
)
VACANCY = re.compile(r"UNEXPIRED|VACANCY|\bT?FV\b|SHORT TERM")


def fetch() -> None:
    raw.fetch(SOURCE, LISTING2020, suffix=".json")
    for listing, local in ((LISTING, False), (LISTING2022, True)):
        raw.fetch(SOURCE, listing, suffix=".json")
        for state, url in _files(listing, local).items():
            raw.fetch(SOURCE, url, suffix=".zip" if state.endswith(".zip") else ".csv")
    raw.fetch(SOURCE, LOCAL2016, suffix=".csv")
    for (state, day), commit in OPENELECTIONS_FILES.items():
        raw.fetch(SOURCE, OPENELECTIONS.format(state=state.lower(), commit=commit, day=day), suffix=".csv")
    for fips in TOWNS.values():
        raw.fetch(SOURCE, SUBDIVISIONS.format(fips), suffix=".zip")
        raw.fetch(SOURCE, INCORPORATED.format(fips), suffix=".zip")


def _files(listing: str, local: bool) -> dict[str, str]:
    files = {}
    for file in json.loads(raw.cached(SOURCE, listing).path.read_text())["data"]["latestVersion"]["files"]:
        data = file["dataFile"]
        name = data["filename"].replace("_", "-")
        if not name.endswith((".csv", ".tab", ".zip")) or "sorted" in name or local != ("-local-" in name):
            continue
        state = name.split("-")[1].upper() + (".zip" if name.endswith(".zip") else "")
        files[state] = f"{ACCESS}/{data['id']}" + ("?format=original" if name.endswith(".tab") else "")
    return files


def _match(state: str, text: str, unified: bool) -> tuple[str, str, bool, str | None] | None:
    for key, title, body, pattern in (UNIFIED if unified else []) + OFFICES:
        if state in TOWNSHIPS and key not in COUNTYWIDE and "COUNTY" not in text:
            continue
        if key == "board-of-education" and state not in SCHOOLS and "COUNTY" not in text:
            continue
        if key == "board-of-supervisors" and state not in {"AZ", "IA", "MS"} and not re.search(r"COUNTY|BOARD", text):
            continue
        found = re.fullmatch(rf"(?:MEMBERS?,? (?:OF )?(?:THE )?)?(?:COUNTY )?(?:{pattern})(?:\s*[-,:]?\s*(.*))?", text)
        if found is None:
            continue
        rest = re.sub(r"[()\"]", "", found[1] or "").strip(" ,-")
        if rest and (not SEAT.fullmatch(rest) or BANNED.search(rest)):
            continue
        return key, title, body, rest or None
    return None


def _classify(state: str, office: str, county: str) -> tuple[str, str, bool, str | None, bool] | None:
    text = re.sub(r"\s+", " ", office.upper()).strip()
    name = re.escape(county.upper())
    unified = state == "HI" or county.upper().endswith(" CITY") or county.upper() in {"DENVER", "BROOMFIELD"}
    text = re.sub(rf"\b{name} {COUNTY}\b", "COUNTY", text)
    text = re.sub(rf"\b{name}-(\d+)$", r"\1", text)
    for _ in range(2):
        text = re.sub(rf"^{name}\b|\b{name}$", "", text).strip(" ,-")
    text = re.sub(r"\b(COUNTY|COMMISSIONER|COUNCIL) \1\b", r"\1", text)
    text = re.sub(r"^(?:NONPARTISAN|SPECIAL ELECTION FOR) ", "", text)
    text = re.sub(r",? ?COUNTY$", "", re.sub(r"\s+", " ", text)).strip(" ,-")
    remainder = REMAINDER.search(text) is not None or "SPECIAL ELECTION" in office.upper()
    text = REMAINDER.sub("", text).strip(" ,-")
    found = _match(state, text, unified)
    if found is None and re.search(r"\s*/\s*|\s+-\s+", text):
        parts = [_match(state, part, unified) for part in re.split(r"\s*/\s*|\s+-\s+", text)]
        if all(part is not None and not part[2] and part[3] is None for part in parts):
            found = (
                "-".join(part[0] for part in parts if part),
                "/".join(part[1] for part in parts if part),
                False,
                None,
            )
    return None if found is None else (*found, remainder)


def _city(office: str, places: dict[str, tuple[str, str]]) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", office.upper()).strip()
    if not re.search(r"MAYOR|COUNCIL|ALDERM", text) or EXCLUDED.search(text):
        return None
    key = "mayor" if "MAYOR" in text else "council"
    remainder = REMAINDER.search(text) is not None
    text = REMAINDER.sub("", text)
    seats = re.findall(
        r"\b(?:WARD|DISTRICT|DIST\.?|POSITION(?: NO\.?)?|POS\.?|SEAT|PLACE) ?#? ?[A-Z]?\d+[A-Z]?\b|\bAT[- ]LARGE\b",
        text,
    )
    for seat in seats:
        text = text.replace(seat, " ")
    text = re.sub(r"\b(?:CITY|TOWN|VILLAGE) OF\b", " ", text)
    text = re.sub(
        r"\b(?:MAYOR|CITY COUNCIL|COUNCIL ?(?:MEMBERS?|M[AE]N|WOMAN|PERSONS?)|COUNCILMEMBERS?|COUNCILORS?|COUNCIL|ALDERM[AE]N"
        r"|\d+ ?(?:YRS?|YEARS?)(?: TERM)?)\b",
        " ",
        text,
    )
    found = places.get(re.sub(r"[^A-Z]", "", text).removesuffix("CITY"))
    if found is None or len(seats) > 1:
        return None
    return {
        "office": office,
        "code": found[0],
        "place": found[1],
        "key": key,
        "title": "Mayor" if key == "mayor" else "City Council",
        "body": key == "council",
        "rest": seats[0] if seats else None,
        "remainder": remainder,
    }


def _town(
    state: str, office: str, county: str, options: list[tuple[str, str, str, str, str, str]]
) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", office.upper()).strip()
    if UNLISTED.search(text):
        return None
    text = re.sub(r"\bBORO\b", "BOROUGH", re.sub(r"\bTWP\b", "TOWNSHIP", re.sub(r"[.']", "", text)))
    remainder = VACANCY.search(text) is not None
    text = re.sub(
        r"\(?(?:UNEXPIRED(?: TERM)?|TO FILL (?:A |THE )?VACANCY|VACANCY|\bT?FV\b|SHORT TERM)\)?"
        r"|\b\d+[- ]?(?:YRS?|YEARS?)(?: TERM)?\b|\(\d\)",
        " ",
        text,
    )
    hits = []
    for code, scheme, base, kind, display, proper in options:
        word = kind.upper()
        for form, explicit in ((f"{word} OF {base}", True), (f"{base} {word}", True), (base, False)):
            for match in re.finditer(rf"(?<![A-Z]){re.escape(form)}(?![A-Z])", text):
                outside = text[: match.start()] + " " + text[match.end() :]
                if explicit or re.search(rf"\b{word}\b", outside):
                    hits.append((explicit, len(form), code, scheme, proper, kind, display, match.start(), match.end()))
    if not hits:
        return None
    hits.sort(key=lambda hit: (hit[0], hit[1]), reverse=True)
    best = hits[0]
    if any(hit[2] != best[2] and hit[:2] == best[:2] for hit in hits):
        return None
    _, _, code, scheme, proper, kind, display, _, _ = best
    for start, end in sorted({(hit[7], hit[8]) for hit in hits if hit[2] == code}, key=lambda span: -span[1]):
        text = text[:start] + " " * (end - start) + text[end:]
    text = re.sub(rf"\b(?:{re.escape(county.upper())}|TOWNSHIP|TOWN|BOROUGH|VILLAGE|CITY)\b|[()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,-:")
    for key, title, body, pattern in MUNICIPAL:
        found = re.fullmatch(rf"(?:{pattern})(?:\s*[-,:]?\s*(.*))?", text)
        if found is None:
            continue
        rest = (found[1] or "").strip(" ,-") or None
        if rest is not None and (not SEAT.fullmatch(rest) or BANNED.search(rest)):
            return None
        if state == "NY" and kind == "town" and key == "council":
            key, title = "board", "Board"
        if state == "IN" and key == "board-of-trustees":
            key, title, body = "trustee", "Trustee", False
        stem = f"{proper} Town" if kind == "town" else f"{proper} City" if kind == "city" else display
        return {
            "office": office,
            "code": code,
            "scheme": scheme,
            "place": display,
            "key": key,
            "title": title,
            "body": body,
            "rest": rest,
            "remainder": remainder,
            "stem": f"Mayor of {proper}" if key == "mayor" else f"{stem} {title}",
        }
    return None


def _seat(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\bDIST\b\.?", "DISTRICT", text.upper().strip())
    text = re.sub(r"\bPCT\b\.?", "PRECINCT", text)
    text = re.sub(r"\s*#\s*", " ", text).strip()
    text = re.sub(r"\bPOS\b\.?", "POSITION", text)
    text = re.sub(r"^(?:NO|NUMBER|#)\.? ?#? ?", "", text)
    text = re.sub(r"\b(?:NO|NUMBER)\.? (?=\d)", "", text)
    text = re.sub(r"\b0+(\d)", r"\1", text)
    if text.isdigit():
        text = f"DISTRICT {text}"
    text = re.sub(r"^(\d+)(?:ST|ND|RD|TH)? (.+)$", r"\2 \1", text)
    if re.fullmatch(r"[A-Z]", text):
        text = f"DISTRICT {text}"
    if re.fullmatch(r"AT[- ]LARGE", text):
        return "At-Large"
    return re.sub(
        r"\b([A-Z]+)\b",
        lambda word: word[1] if re.fullmatch(r"[IVX]+|[A-Z]", word[1]) else word[1].capitalize(),
        re.sub(r"\bAT[- ]LARGE\b", "AT-LARGE", text),
    )


def parse() -> None:
    with connect() as conn:
        places: dict[str, dict[str, tuple[str, str]]] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.polity_id and n.is_preferred and n.lang = 'en'
               where u.scheme_code = 'us.place' and u.code ~ '^[0-9]{7}$'"""
        ).fetchall():
            places.setdefault(code[:2], {})[
                re.sub(r"[^A-Z]", "", re.sub(r"^(?:CITY|TOWN|VILLAGE) OF ", "", text.upper())).removesuffix("CITY")
            ] = (code, text)
        counties = {
            (
                code[:2],
                re.sub(r"[^A-Z]", "", re.sub(r" (?:COUNTY|PARISH)$", "", text.upper()).replace("SAINT", "ST")),
            ): code
            for code, text in conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code in ('us.place', 'us.town') and code ~ '^[0-9]+$'"
            ).fetchall()
        }
        municipalities: dict[str, list[tuple[str, str, str, str, str, str]]] = {}
        shapes: dict[str, tuple[str, str, str, str]] = {}
        incorporated: list[tuple[str, str, str, str]] = []
        for state, fips in TOWNS.items():
            for url in (SUBDIVISIONS.format(fips), INCORPORATED.format(fips)):
                with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                    parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
                reader = shapefile.Reader(
                    shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
                )
                for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                    if record is None or shape is None:
                        continue
                    proper, whole = str(record["NAME"]), str(record["NAMELSAD"])
                    kind = whole[len(proper) :].strip().lower() if whole.startswith(proper) else ""
                    code = str(record["GEOID"])
                    subdivision = url == SUBDIVISIONS.format(fips)
                    if kind not in KINDS or subdivision != (
                        state in WHOLE
                        or kind in ("township", "charter township", "plantation")
                        or kind == "town"
                        and state in ("NY", "WI")
                    ):
                        continue
                    scheme = "us.town" if subdivision else "us.place"
                    if subdivision and kind == "city":
                        found = places.get(fips, {}).get(re.sub(r"[^A-Z]", "", proper.upper()))
                        if found is not None:
                            code, scheme = found[0], "us.place"
                    option = (
                        code,
                        scheme,
                        re.sub(r"[.']", "", proper.upper()),
                        kind,
                        f"{proper} {kind.title()}" if kind in SUFFIXED else proper,
                        proper,
                    )
                    if code not in existing:
                        shapes[code] = (option[4], state, json.dumps(shape.__geo_interface__), code[:5])
                    if subdivision:
                        municipalities.setdefault(str(record["GEOID"])[:5], []).append(option)
                    else:
                        incorporated.append((code, json.dumps(shape.__geo_interface__), state, json.dumps(option)))
        for code, within, option in conn.execute(
            """select f.code, array(select c.code from area a join area_code c on c.area_id = a.id
                     join area_geometry g on g.area_id = a.id
                     where a.scheme_code = 'us.county' and left(c.code, 2) = left(f.code, 2) and g.geom && s.geom
                       and st_area(st_intersection(g.geom, s.geom)) > 0.01 * st_area(s.geom)
                     order by st_area(st_intersection(g.geom, s.geom)) desc), f.option
               from unnest(%s::text[], %s::text[], %s::text[]) as f(code, geojson, option)
               cross join lateral (select st_makevalid(st_transform(st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326))
                 as geom) s""",
            [
                [code for code, _, _, _ in incorporated],
                [geojson for _, geojson, _, _ in incorporated],
                [option for _, _, _, option in incorporated],
            ],
        ).fetchall():
            if code in shapes and within:
                shapes[code] = (*shapes[code][:3], within[0])
            for county in within:
                municipalities.setdefault(county, []).append(tuple(json.loads(option)))
    contests: list[pl.DataFrame] = []
    lines: list[pl.DataFrame] = []
    problems: list[str] = []
    count = r"(?i)\s*\((?:VOTE FOR|ELECT)(?: NO MORE THAN)? (\w+)\)"
    local2016 = pl.scan_csv(
        raw.cached(SOURCE, LOCAL2016).path, infer_schema=False, encoding="utf8-lossy", null_values=["", "NA"]
    ).select(
        day=pl.lit(date(2016, 11, 8)),
        state_po="state_postal",
        office=pl.col("office").str.to_uppercase().str.replace(count, "").str.replace(r"^FOR ", "").str.strip_chars(),
        district=pl.when(pl.col("state_postal") != "WV").then(pl.col("district").str.to_uppercase()),
        special="special",
        county_fips="county_fips",
        county_name=pl.col("county_name").str.to_uppercase().str.replace(r" (?:COUNTY|PARISH)$", ""),
        precinct="precinct",
        jurisdiction_fips="jurisdiction",
        stage=pl.lit("GEN"),
        mode=pl.col("mode").str.to_uppercase(),
        candidate=pl.when(pl.col("candidate") == "[Write-in]").then(pl.lit("WRITE-IN")).otherwise("candidate"),
        party_detailed=pl.col("party")
        .str.to_uppercase()
        .replace({"DEMOCRATIC": "DEMOCRAT"})
        .replace(
            ["NONE", "UNKNOWN", "OTHER", "OTHER PARTY", "INT", "BOT", "NOPTY", "NON PARTISAN", "NO AFFILIATION"], None
        )
        .pipe(lambda party: pl.when(~party.str.contains("/")).then(party)),
        writein="writein",
        votes="votes",
        magnitude=pl.col("office").str.extract(count).str.to_uppercase().replace(COUNTS).cast(pl.Int32, strict=False),
    )
    columns = [
        "state_po",
        "office",
        "district",
        pl.col("special").str.to_uppercase(),
        "county_fips",
        "county_name",
        "precinct",
        "jurisdiction_fips",
        "stage",
        "mode",
        "candidate",
        "party_detailed",
        pl.col("writein").str.to_uppercase(),
        "votes",
        pl.col("magnitude").cast(pl.Float64, strict=False).cast(pl.Int32),
    ]
    local2018 = pl.scan_csv(raw.cached(SOURCE, LOCAL2018).path, infer_schema=False).select(
        pl.lit(date(2018, 11, 6)).alias("day"), *columns
    )
    inputs = [
        *(
            (
                state.removesuffix(".zip"),
                (
                    pl.read_csv(
                        zipfile.ZipFile(path).read(zipfile.ZipFile(path).namelist()[0]), infer_schema=False
                    ).lazy()
                    if path.suffix == ".zip"
                    else pl.scan_csv(path, infer_schema=False)
                )
                .filter(
                    pl.col("dataverse").is_null() if local else pl.col("dataverse") == "LOCAL",
                    pl.col("stage").is_in(["GEN", "GEN RECOUNT"]),
                )
                .select(pl.lit(day).alias("day"), *columns),
            )
            for listing, local, day in (
                (LISTING, False, date(2024, 11, 5)),
                (LISTING2022, True, date(2022, 11, 8)),
                (LISTING2020, False, date(2020, 11, 3)),
            )
            for state, url in sorted(_files(listing, local).items())
            if listing != LISTING2020 or state == "OR"
            if (path := raw.cached(SOURCE, url).path)
        ),
        *(
            (state, scan.filter(pl.col("state_po") == state))
            for scan in (local2016, local2018)
            for state in sorted(scan.select("state_po").unique().collect()["state_po"])
        ),
    ]
    openelections = [
        (
            state,
            pl.scan_csv(
                raw.cached(SOURCE, OPENELECTIONS.format(state=state.lower(), commit=commit, day=day)).path,
                infer_schema=False,
                encoding="utf8-lossy",
            )
            .rename(lambda column: column.lstrip("\ufeff"))
            .filter(
                ~pl.col("candidate").str.contains(r"(?i)^(?:over|under) ?votes?$|^(?:rejected|unassigned) write|^total")
            )
            .with_columns(
                county_fips=pl.col("county")
                .str.to_uppercase()
                .str.replace(r" (?:COUNTY|PARISH)$", "")
                .str.replace("SAINT", "ST")
                .str.replace_all(r"[^A-Z]", "")
                .replace_strict(
                    {
                        name: code
                        for (fips, name), code in counties.items()
                        if fips
                        == {
                            **TOWNS,
                            "FL": "12",
                            "MT": "30",
                            "NV": "32",
                            "TX": "48",
                            "UT": "49",
                            "WV": "54",
                            "WY": "56",
                        }[state]
                    },
                    default=None,
                )
            )
            .select(
                pl.lit(day).alias("day"),
                pl.lit(state).alias("state_po"),
                office=pl.col("office").str.to_uppercase().str.replace(count, "").str.strip_chars(),
                district=pl.when(~pl.col("district").is_in(["NA", ""])).then(pl.col("district").str.to_uppercase()),
                special=pl.lit("FALSE"),
                county_fips="county_fips",
                county_name=pl.col("county").str.to_uppercase().str.replace(r" (?:COUNTY|PARISH)$", ""),
                precinct="precinct",
                jurisdiction_fips="county_fips",
                stage=pl.lit("GEN"),
                mode=pl.lit("TOTAL"),
                candidate="candidate",
                party_detailed=pl.col("party")
                .str.to_uppercase()
                .replace(OPENELECTIONS_PARTIES)
                .pipe(lambda party: pl.when(~party.is_in(["NON", "NA", "NP", "NPA", ""])).then(party)),
                writein=pl.when(pl.col("candidate").str.contains(r"(?i)write[- ]?in"))
                .then(pl.lit("TRUE"))
                .otherwise(pl.lit("FALSE")),
                votes="votes",
                magnitude=pl.col("office")
                .str.extract(count)
                .str.to_uppercase()
                .replace(COUNTS)
                .cast(pl.Int32, strict=False),
            ),
        )
        for (state, day), commit in OPENELECTIONS_FILES.items()
    ]
    for state, origin, scan in [
        *((state, "medsl", scan) for state, scan in inputs),
        *((state, "openelections", scan) for state, scan in openelections),
    ]:
        if state in COVERED:
            continue
        frame = (
            scan.with_columns(
                pl.lit(origin).alias("origin"),
                pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " "),
                pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
            .filter(
                pl.col("candidate").is_not_null(),
                pl.col("office").is_not_null(),
                pl.col("county_fips").is_not_null(),
                ~pl.col("candidate").str.to_uppercase().is_in([*DROPPED, *VACANT]),
            )
            .collect()
        )
        if frame.height == 0:
            continue
        year = frame["day"][0].year
        frame = frame.with_columns(pl.col("county_fips").str.zfill(5), pl.col("special").fill_null("FALSE"))
        recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select("office", "district", "county_fips").unique()
        frame = pl.concat(
            [
                frame.join(recounted, on=["office", "district", "county_fips"], how="anti", nulls_equal=True),
                frame.filter(pl.col("stage") == "GEN RECOUNT"),
            ]
        )
        precinct = ["office", "district", "special", "county_fips", "jurisdiction_fips", "precinct"]
        frame = (
            frame.with_columns(total=(pl.col("mode") == "TOTAL").any().over(*precinct, "candidate", "party_detailed"))
            .filter(pl.col("total") == (pl.col("mode") == "TOTAL"))
            .with_columns(pooled=pl.col("candidate").is_in(list(POOLED)) | (pl.col("writein") == "TRUE"))
        )
        labels = []
        towns = {}
        for office, county, fips in frame.select("office", "county_name", "county_fips").unique().iter_rows():
            found = _classify(state, office, county or "")
            if found is not None:
                key, title, body, rest, remainder = found
                labels.append(
                    {
                        "office": office,
                        "county_fips": fips,
                        "code": fips,
                        "scheme": "us.county",
                        "place": county,
                        "key": key,
                        "title": title,
                        "body": body,
                        "rest": rest,
                        "remainder": remainder,
                        "stem": None,
                    }
                )
            elif office not in towns:
                towns[office] = _city(office, places.get(fips[:2], {}))
            if found is None and (town := towns[office]) is not None:
                labels.append({**town, "county_fips": fips, "scheme": "us.place", "stem": None})
            elif (
                found is None and (town := _town(state, office, county or "", municipalities.get(fips, []))) is not None
            ):
                labels.append({**town, "county_fips": fips})
        if not labels:
            continue
        frame = frame.join(pl.DataFrame(labels, infer_schema_length=None), on=["office", "county_fips"])
        chosen = (
            frame.group_by("county_fips", "code", "key", "rest", "remainder", "office")
            .agg(pl.struct("jurisdiction_fips", "precinct").n_unique().alias("width"))
            .sort("width", "office", descending=[True, False])
            .unique(["county_fips", "code", "key", "rest", "remainder"], keep="first", maintain_order=True)
            .select("county_fips", "office")
        )
        frame = frame.join(chosen, on=["county_fips", "office"])
        race = ["office", "district", "special"]
        candidates = frame.filter(~pl.col("pooled"), pl.col("scheme") == "us.county")
        spread = (
            candidates.join(
                candidates.group_by(*race, "candidate").agg(pl.col("county_fips").n_unique().alias("counties")),
                on=[*race, "candidate"],
                nulls_equal=True,
            )
            .group_by(*race, "county_fips")
            .agg((pl.col("counties") > 1).all().alias("shared"))
            .filter("shared")
            .select(*race, "county_fips")
        )
        for office, district in spread.select("office", "district").unique().iter_rows():
            problems.append(f"{year} {state}: {office} {district or ''} spans counties")
        frame = frame.join(spread, on=[*race, "county_fips"], how="anti", nulls_equal=True)
        frame = frame.with_columns(
            seat=pl.struct("rest", "district", "county_name").map_elements(
                lambda row: (
                    _seat(row["rest"])
                    or _seat(
                        district
                        if (
                            district := re.sub(
                                rf"^{re.escape((row['county_name'] or '').upper())}\b\s*",
                                "",
                                (row["district"] or "").upper(),
                            )
                        )
                        and SEAT.fullmatch(district)
                        and not BANNED.search(district)
                        else None
                    )
                ),
                return_dtype=pl.String,
            ),
            remainder=pl.col("remainder") | (pl.col("special") == "TRUE"),
        )
        contest = ["day", "code", "key", "seat", "remainder"]
        summary = frame.group_by(contest).agg(
            pl.col("state_po").first(),
            pl.col("scheme").first(),
            pl.col("place").first(),
            pl.col("title").first(),
            pl.col("body").first(),
            pl.col("stem").first(),
            pl.col("origin").first(),
            pl.col("office").n_unique().alias("offices"),
            pl.col("office").first(),
            pl.col("magnitude").max(),
            pl.col("votes").is_null().any().alias("masked"),
            pl.struct("jurisdiction_fips", "precinct").n_unique().alias("precincts"),
        )
        doubled = summary.filter(pl.col("offices") > 1)
        for row in doubled.iter_rows(named=True):
            problems.append(f"{year} {state}: {row['place']} {row['key']} {row['seat']} has several office labels")
        summary = summary.filter(pl.col("offices") == 1).with_columns(
            magnitude=pl.when(
                pl.col("body") & pl.col("seat").is_null() & ((year == 2016) | (pl.col("origin") == "openelections"))
            )
            .then("magnitude")
            .otherwise(pl.col("magnitude").fill_null(1)),
            district=pl.col("seat").is_not_null()
            & (pl.col("precincts") < 0.9 * pl.col("precincts").max().over("code")),
        )
        contests.append(summary.drop("offices", "precincts"))
        lines.append(
            frame.join(summary.select(contest), on=contest, nulls_equal=True)
            .with_columns(
                candidate=pl.when("pooled").then(pl.lit("WRITE-IN")).otherwise("candidate"),
                party_detailed=pl.when("pooled").then(None).otherwise("party_detailed"),
            )
            .group_by(*contest, "candidate", "pooled")
            .agg(pl.col("party_detailed").drop_nulls().mode().first(), pl.col("votes").sum())
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(contests, how="diagonal_relaxed").sort("state_po", "code", "key", "seat", nulls_last=False).write_parquet(
        PARSED / "contests.parquet"
    )
    pl.concat(lines).write_parquet(PARSED / "lines.parquet")
    used = set(pl.read_parquet(PARSED / "contests.parquet").filter(pl.col("scheme") != "us.county")["code"])
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select f.code, f.display, f.state, f.county, encode(st_asewkb(s.geom), 'hex'),
                     encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                   from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[])
                     as f(code, display, state, county, geojson)
                   cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(
                     st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3)) as geom) s""",
                [
                    [code for code in shapes if code in used],
                    *([shapes[code][part] for code in shapes if code in used] for part in (0, 1, 3, 2)),
                ],
            ).fetchall(),
            schema=["code", "display", "state", "county", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "municipalities.parquet")
    (PARSED / "problems.txt").write_text("\n".join(problems))


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        polities = {
            (scheme, code): (polity, text, path)
            for scheme, code, polity, text, path in conn.execute(
                """select a.scheme_code, c.code, t.polity_id, n.text, s.path from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code in ('us.county', 'us.place', 'us.town')"""
            ).fetchall()
        }
        parties_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        known = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County and municipal offices elected in November 2016 through 2024, from MEDSL's and OpenElections' "
            "precinct files",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
            return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            label = None if label is None else label.strip().removesuffix(" PARTY")
            if not label or label in NONPARTISAN:
                return None, False
            label = EXPANDED.get(label, label)
            if label in INDEPENDENT or label in UNAFFILIATED or "PETITION" in label:
                return None, True
            if label in NATIONAL:
                return cs.key("us.party", NATIONAL[label]), False
            text = (title(label.removesuffix(" PARTY")) + " Party").replace("'S ", "'s ")
            candidates = [f"{state}:{slug(text)}", f"{state}:{slug(text.removesuffix(' Party'))}"]
            key = next((key for key in candidates if key in parties_known), candidates[0])
            found = cs.key("us.party", key)
            if found in cs.minted and key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(found, text)
            return found, False

        sources = {}
        for day, (text, url) in DATASETS.items():
            source = sources[day] = cs.source(PUBLISHER, text)
            cs.add(
                "source",
                id=source,
                kind="academic",
                publisher=PUBLISHER,
                title=text,
                url=url,
                license="CC0 1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
        for listing, local, day in (
            (LISTING, False, date(2024, 11, 5)),
            (LISTING2022, True, date(2022, 11, 8)),
            (LISTING2020, False, date(2020, 11, 3)),
        ):
            cs.artifact(raw.cached(SOURCE, listing), sources[day])
            files = {state.removesuffix(".zip"): url for state, url in _files(listing, local).items()}
            for state in sorted(set(contests.filter(pl.col("day") == day, pl.col("origin") == "medsl")["state_po"])):
                cs.artifact(raw.cached(SOURCE, files[state]), sources[day])
        contributed = {}
        for (state, day), commit in OPENELECTIONS_FILES.items():
            if state not in contributed:
                source = contributed[state] = cs.source("OpenElections", f"{state} precinct-level results")
                cs.add(
                    "source",
                    id=source,
                    kind="community",
                    publisher="OpenElections",
                    title=f"{state} precinct-level results",
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                )
            cs.artifact(
                raw.cached(SOURCE, OPENELECTIONS.format(state=state.lower(), commit=commit, day=day)),
                contributed[state],
            )
        cs.artifact(raw.cached(SOURCE, LOCAL2016), sources[date(2016, 11, 8)])
        cs.artifact(raw.cached(SOURCE, LOCAL2018), sources[date(2018, 11, 6)])
        taken = set(
            conn.execute(
                """select e.date_from, x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where e.date_from = any(%s) and r.source_id <> all(%s)""",
                [list(DATASETS), [*sources.values(), *contributed.values()]],
            ).fetchall()
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        municipalities = pl.read_parquet(PARSED / "municipalities.parquet")
        for state in sorted(set(municipalities["state"])):
            for url in (SUBDIVISIONS.format(TOWNS[state]), INCORPORATED.format(TOWNS[state])):
                cs.artifact(raw.cached(SOURCE, url), census)
        for code, display, state, county, geom, point in municipalities.iter_rows():
            town = len(code) == 10
            scheme = "us.town" if town else "us.place"
            if (scheme, code) in polities:
                continue
            polity = cs.key("us.polity", f"town:{code}" if town else f"place:{code}")
            path = f"us/{state.lower()}/{slug(display)}"
            for fallback in (
                path
                if re.search(r"-(?:township|town|village|borough|city|plantation)$", path)
                else f"{path}-{'town' if town else 'city'}",
                f"{path}-{slug(polities[('us.county', county)][1])}",
            ):
                if path in slugs:
                    path = fallback
            if path in slugs:
                raise ValueError(f"{display} would take {path}")
            slugs[path] = polity
            polities[(scheme, code)] = (polity, display, path)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, display)
            cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{state}"))
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, code)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=cs.area("us.county", county) if town else cs.area("us.state", code[:2]),
            )
            name(area, f"{display}, {state}")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
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
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and block systems must already exist")

        owners: dict[tuple[str, str], UUID] = {}
        elections: dict[tuple[date, str], UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        grouped = {
            keys: rows for keys, rows in lines.group_by("day", "code", "key", "seat", "remainder", maintain_order=True)
        }
        for row in contests.iter_rows(named=True):
            day, state, scheme, code, key, seat = (
                row["day"],
                row["state_po"],
                row["scheme"],
                row["code"],
                row["key"],
                row["seat"],
            )
            if (scheme, code) not in polities:
                continue
            polity, place, path = polities[(scheme, code)]
            parent = cs.key("us.election", f"{day}:{state}")
            if parent in cs.minted:
                raise ValueError(f"no election {day}:{state}")
            if (day, f"{state}:{code}:{key}") in taken:
                continue
            contest_key = (
                f"{day}:{state}:{code}:{key}"
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
            )
            contest_path = (
                f"{path}/{day}-general/{key}"
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else "")
            )
            if contest_path in slugs and slugs[contest_path] != known.get(contest_key):
                continue
            if (day, code) not in elections:
                election = elections[(day, code)] = cs.key("us.election", f"{day}:{state}:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {place} general election")
                cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
            election = elections[(day, code)]
            body = row["body"]
            city = place.removeprefix("City of ")
            office_title = row["stem"] or (
                f"Mayor of {city}"
                if key == "mayor" and (city != place or scheme == "us.place")
                else f"{city} City Council"
                if key in ("city-council", "council") and (city != place or scheme == "us.place")
                else f"{place} Council"
                if key == "city-council"
                else f"{place} {row['title']}"
            )
            if (code, key) not in owners:
                owner = owners[(code, key)] = cs.key("us.body" if body else "us.office", f"{state}:{code}:{key}")
                if owner in cs.minted:
                    if body:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add(
                            "office",
                            id=owner,
                            polity_id=polity,
                            kind="mayor" if key == "mayor" and row["stem"] else "other",
                        )
                    name(owner, office_title)
            whole, unit = cs.area(scheme, code), cs.unit(scheme, code)
            area = whole
            if row["district"]:
                district = f"{code[:2]}:{code}:{key}:{slug(seat)}"
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{office_title}, {seat}")
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
            contest = cs.key("us.contest", contest_key)
            slugs[contest_path] = contest
            magnitude = row["magnitude"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if body else "office",
                office_id=None if body else owners[(code, key)],
                body_id=owners[(code, key)] if body else None,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                default_result_kind="counted",
                term="remainder" if row["remainder"] else "full",
            )
            name(
                contest,
                (f"{day.year} {city} mayoral election" if key == "mayor" else f"{day.year} {office_title} election")
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else ""),
            )
            cs.add("slug", path=contest_path, entity_id=contest)
            if row["district"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT)
            if row["masked"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=MASKED)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[day] if row["origin"] == "medsl" else contributed[state],
                is_default=True,
                completeness=None if row["masked"] else 1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.replace("2024", str(day.year)) if row["origin"] == "medsl" else CONTRIBUTED,
            )
            rows = (
                grouped[(day, code, key, seat, row["remainder"])]
                .group_by(pl.col("candidate").map_elements(slug, return_dtype=pl.String).alias("token"), "pooled")
                .agg(
                    pl.col("candidate").sort_by("votes", descending=True, nulls_last=True).first(),
                    pl.col("party_detailed").drop_nulls().first(),
                    pl.when(pl.col("votes").is_null().any()).then(None).otherwise(pl.col("votes").sum()).alias("votes"),
                )
                .sort("votes", "candidate", descending=[True, False], nulls_last=True)
            )
            totals: list[tuple[int, UUID]] = []
            for candidate, pooled, label, votes in rows.select(
                "candidate", "pooled", "party_detailed", "votes"
            ).iter_rows():
                if pooled:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    ballot = title(candidate)
                    party_id, independent = party(state, label)
                    person_key = (code, slug(candidate))
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", f"{state}:{code}:{slug(candidate)}")
                        cs.add("person", id=persons[person_key])
                        if persons[person_key] in cs.minted:
                            name(persons[person_key], ballot)
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                        person_id=persons[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=independent,
                    )
                    totals.append((votes or 0, option))
                if votes is not None and result_set in cs.minted:
                    values.append((str(result_set), str(unit), str(option), votes))
            if magnitude is None or len(totals) > magnitude and totals[magnitude - 1][0] == totals[magnitude][0]:
                continue
            for _, option in totals[:magnitude]:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
