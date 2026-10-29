import html
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import fastexcel
import polars as pl
import shapefile
from psycopg.rows import dict_row

from ea import raw
from ea.changeset import Changeset, slug
from ea.coverage_tables import tokens
from ea.sources.licenses import UNLICENSED
from ea.sources.us import cog
from ea.sources.us.electionsfl import FIPS as FLORIDA
from ea.sources.us.electionsfl import JACKSONVILLE, REPORTS, export
from ea.sources.us.medsl import RECODED
from ea.sources.us.medsl_state import DROPPED, EXPANDED, INDEPENDENT, NATIONAL, POOLED, PUBLISHER, finer, resolve
from ea.store import DATA, connect

SOURCE = "us.medsl_local"
GOVERNING = {
    "commission",
    "board-of-commissioners",
    "board-of-supervisors",
    "council",
    "commissioners-court",
    "county-commission",
    "county-legislature",
    "county-board",
    "board-of-county-commissioners",
    "legislature",
    "parish-council",
    "fiscal-court",
    "quorum-court",
}
SYNONYMS = {
    "county-executive": "executive",
    "executive": "executive",
    "chief-executive-officer": "executive",
    "chief-executive": "executive",
    "state-s-attorney": "states-attorney",
    "states-attorney": "states-attorney",
}
VERSION = "11"
ANSWER = r"(?i)^(?:yes|no)\b"
PARSED = DATA / "parsed" / SOURCE
ABBREVIATED = {
    "DAD": {"MIA": "MIAMI"},
    "CIT": {"CR": "CRYSTAL RIVER", "INV": "INVERNESS"},
    "POL": {"HC": "HAINES CITY", "LW": "LAKE WALES", "BT": "BARTOW", "FM": "FORT MEADE"},
    "VOL": {"NSB": "NEW SMYRNA BEACH", "DBS": "DAYTONA BEACH SHORES", "LH": "LAKE HELEN"},
    "CLA": {"GCS": "GREEN COVE SPRINGS", "KH": "KEYSTONE HEIGHTS"},
    "OKA": {"FW": "FORT WALTON BEACH", "NV": "NICEVILLE"},
    "MON": {"KW": "KEY WEST"},
    "MAN": {"HB": "HOLMES BEACH"},
}
PLACED = {
    **{f"ORA:{election}": "ORLANDO" for election in (3462, 3596, 3603, 3872, 3905)},
    **{f"LEO:{election}": "TALLAHASSEE" for election in (1791, 2665, 2878)},
}
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
PROSECUTORS = {
    "district-attorney",
    "prosecuting-attorney",
    "prosecutor",
    "states-attorney",
    "solicitor",
    "public-defender",
}
MARICOPA_CONSTABLES = {
    "CHRIS WIBLE": "Arcadia Biltmore",
    "PEDRO ANTONIO LOPEZ": "Country Meadows",
    "JAMES RICH": "Desert Ridge",
    "RHYS TORRES": "Dreamy Draw",
    "THOMAS RAY": "Highland",
    "DENICE M GARCIA": "Maryvale",
    "MATTHEW H MCRAE": "Moon Valley",
    "JON CURTIS": "North Mesa",
    "CARLOS F GASTELUM": "San Tan",
    "MICHAEL A FLORES": "Agua Fria",
}
SPLIT = {"2024-11-05:AZ:04013:constable"}
WITHDRAWN = (
    "Withdrawn: MEDSL's file lists Maricopa County's 2024 constable races under one countywide title. The archive now "
    "holds them as ten separate races, one for each justice precinct, as the county's official canvass names them."
)
RETIRED = (
    "Maricopa County's 2024 constable races, filed under one countywide title in MEDSL's file; the archive holds "
    "each justice precinct's race separately."
)
COMBINED = ("NY:conservative-common-sense-party", "NY:conservative-rule-of-law-party")
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
    ("county-board", "Board", True, r"COUNTY BOARD(?! OF EDUCATION)(?: MEMBERS?)?"),
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
            r"|(?:COUNTY )?SOIL (?:AND|&) WATER(?: DISTRICT)?(?: (?:SUPERVISORS?|DIRECTORS?|COMMISSIONERS?|COMMISSION))?"
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
TERM = r"Term (?:Commencing|Beginning) (\d\d?)/(\d\d?)/(\d{4})"
SCHOOLS = {"AL", "FL", "GA", "KY", "LA", "MD", "MS", "NV", "SC", "TN", "WV"}
SEAT = re.compile(
    r"(?:(?:DIST(?:RICT)?|PRECINCT|PCT|PLACE|POST|POSITION|POS|SEAT|GROUP|WARD|ZONE|DIVISION|BEAT|AREA|NO|NUMBER|#)\.? ?#? ?[A-Z]?\d+[A-Z]?"
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
NONPARTISAN = {"NONPARTISAN", "NON-PARTISAN", "NON PARTISAN", "NON", "NONC", "NONS", "NP", "OTHER", '""'}
UNAFFILIATED = {"NO", "INDEPENDENT-UNENROLLED", "NO PARTY PREFERENCE", "NO POLITICAL PARTY"}
METHOD = (
    "Totals are the sums of the precinct rows in MEDSL's 2024 precinct file for the state. The file does not mark "
    "winners, so the winners shown are the candidates with the most votes, as many as the contest elects."
)
FLORIDA_TITLE = "Election night reporting precinct results (CSV exports)"
FLORIDA_METHOD = (
    "Totals are the sums of the precinct rows in the county Supervisor of Elections' election night reporting export "
    "(VR Systems). The export does not mark winners, so the winners shown are the candidates with the most votes, as "
    "many as the contest elects."
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
    "FL": "12",
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
COMMISSIONS = [
    ("commission", "Commission", True, r"(?:(?:CITY|TOWN|VILLAGE) )?COMMISSION(?:ERS?)?(?: MEMBERS?)?"),
]
UNLISTED = re.compile(
    r"QUESTION|PROPOSITION|MEASURE|FIRE|LIBRARY|REGIONAL|CONSOLIDATED|SHALL|OPEN SPACE|\b\d{4} TERM|RECALL|DEPUTY"
    r"|PRO TEM|JUDGE|COURT|COUNTY|CHAIR|PRESIDENT"
)
VACANCY = re.compile(r"UNEXPIRED|VACANCY|\bT?FV\b|SHORT TERM")
SPECIAL_KINDS = [
    (
        "conservation",
        (
            r"(?:SOIL (?:AND |& )?(?:WATER )?)?(?:CONSERVATION|CONSER\.?)(?: ZONE)?(?: DISTRICT| DIST\.?)?"
            r"|SOIL ?(?:AND|&|/) ?WATER(?: CONSER\.?)?(?: (?:CONSERVATION )?DIST(?:RICT|\.)?)?"
            r"|WATERSHED CONSERVATION DISTRICT|SOIL AND CONSERVATION DISTRICT"
        ),
    ),
    (
        "hospital",
        (
            r"(?:PUBLIC |MEMORIAL |COUNTY )*HOSPITAL(?: DISTRICT| DIST\.?| AUTHORITY| BOARD DISTRICT)?"
            r"|SPECIAL HEALTH CARE DISTRICT"
        ),
    ),
    (
        "fire",
        (
            r"(?:RURAL |SPECIAL |VOLUNTEER )?FIRE(?: (?:CONTROL AND RESCUE|CONTROL|PROTECTION|RESCUE|AND RESCUE|& RESCUE"
            r"|AND MEDICAL|& MED|AND EMS|DEPARTMENT|SERVICES?|AND|EMERGENCY|MEDICAL))*(?: DISTRICT| DIST\.?)?"
            r"(?: (?:NO\.? ?|# ?)?\d+)?|FD ?\d+"
        ),
    ),
    ("power", r"(?:RURAL )?PUBLIC POWER(?: AND IRRIGATION)? DISTRICT|(?:ELECTRIC )?POWER AUTHORITY"),
    (
        "utility",
        (
            r"(?:WATER )?(?:PEOPLE'S UTILITY DISTRICT|PUD)|UTILIT(?:Y|IES)(?: DEPENDENT)? (?:DISTRICT|AUTHORITY)"
            r"|METROPOLITAN UTILITIES DISTRICT|MUNICIPAL UTILITY DISTRICT(?: (?:NO\.? ?)?\d+)?"
        ),
    ),
    (
        "improvement",
        (
            r"(?:GENERAL |COMMUNITY |MUNICIPAL SERVICES )?IMPROVEMENT (?:DISTRICT|ZONE)|COMMUNITY DEVELOPMENT DISTRICT"
            r"|DOMESTIC WATER IMPROVE(?:MENT)?(?: DISTRICT)?"
        ),
    ),
    (
        "sanitary",
        (
            r"(?:WATER (?:AND|&) )?(?:REGIONAL )?(?:SANITARY|SANITATION)(?: (?:AND|&) WATER)?(?: SEWER)?(?: DISTRICT| DIST\.?)?"
            r"|(?:REGIONAL )?SEWER(?: DISTRICT| DIST\.?)?|WATER (?:AND|&) SEWER(?: DISTRICT)?|LEVEE AND SANITARY DISTRICT"
        ),
    ),
    (
        "water",
        (
            r"(?:SPECIAL |BENEFITED )?WATER (?:CONTROL |CONSERVATION |DEVELOPMENT )?(?:DISTRICT|AUTHORITY|DIST\.?)"
            r"|DRAINAGE DISTRICT|FLOOD SAFETY AND WATER QUALITY DISTRICT"
        ),
    ),
    ("park", r"(?:PARK AND )?RECREATION DISTRICT|PARK DISTRICT"),
    ("library", r"LIBRARY DISTRICT"),
    ("levee", r"(?:DELTA )?LEVEE DISTRICT|RECLAMATION DISTRICT"),
    ("port", r"PORT AUTHORITY|PORT(?! OF)(?: WATERWAY AND BEACH COMMISSION)?|AIRPORT AUTHORITY"),
]
SPECIAL = re.compile(r"\b(?:" + "|".join(f"(?P<{kind}>{pattern})" for kind, pattern in SPECIAL_KINDS) + r")(?![A-Z])")
SPECIAL_SKIP = re.compile(
    r"COUNCIL|MAYOR|SCHOOL|EDUCATION|\bBOE\b|\bBD OF ED\b|CLERK|JUDGE|TOWNSHIP (?:TRUSTEE|COMMITTEE)|COMMITTEE"
    r"|FUNDING|FLUORIDATION|ANNEXATION"
    r"|R?E?[DV]ELOPMENT POWER|FIRE CODE|STATEWIDE|COMMISSIONER OF|VILLAGE|TOWN COMMITTEE|CITY COMMISSIONER|TRANSIT"
    r"|QUESTION|PROPOSITION|MEASURE|SHALL|RECALL|\bTAX\b|LEVY|BOND"
)
SPECIAL_ROLE = re.compile(
    r"^(?:(?:FOR MEMBERSHIP TO THE |MEMBERS? OF (?:THE )?)?(?:BOARD MEMBER\b-?|FIRE BOARD\s*-|(?:PERMANENT )?DIRECTORS?,?(?= )|MEMBER OF THE BOARD OF DIRECTORS"
    r"|BOARD OF DIRECTORS|(?:BRD|BOARD|BD) OF FIRE COMM\w*(?: S)?|FIRE DISTRICT COMMISSIONER|FIRE COMMISSIONER"
    r"|(?:SOIL )?CONSERVATION DISTRICT SUPERVISOR|DIRECTOR OF|MEMBER,|MEMBER OF)\s*-?\s*)+"
)
SPECIAL_TAIL = re.compile(
    r"(?:\s*\(VOTE FOR \d+\)|\s*-?\s*\(?UNEXPIRED[^)]*\)?|\s*-?\s*UNEXP\w* (?:\d-YR )?TERM|\s+TO FILL A VACANCY"
    r"|\s+(?:19|20)\d\d|\s+\d+ ?(?:YR|YEAR)S?(?: TERM)?|\s*-\s*\d YEAR TERM|\s*/\s*\d ?YR|\s+\d{8} THRU \d{8}|\s+T?FV"
    r"|\s+(?:BOARD MEMBERS?|BOARD OF (?:DIRECTORS|TRU?E?STEES|COMMISSIONERS|SUPERVISORS)|ADVISORY BOARD|BOARD|TRUSTEES?"
    r"|DIRECTORS?|DIR|SUPERVISORS?|COMMISSIONERS?|COMMISSION|COMMITTEE|MEMBERS?|LEVEE BOARD|BOARD DISTRICT TRUSTEE)"
    r"|\s+POS\.?)+$"
)
MAJORITY = {
    "0455000": "majority",
    "2270000": "majority",
    "4828068": "majority",
    "4841980": "majority",
    "4820344": "majority",
    "4814920": "majority",
    "0523290": "arkansas",
    "0535710": "arkansas",
    "0541000": "forty",
    "1253000": "majority",
    "1270600": "majority",
}
RUNOFF = (
    "No candidate reached the vote share this city requires to win outright, so the leading candidates advanced to a "
    "runoff."
)
SPECIAL_WORDS = {
    r"\bDIST\b\.?": "DISTRICT",
    r"\bCONSER\b\.?": "CONSERVATION",
    r"\bIMPROVE$": "IMPROVEMENT",
    r"\bMED\b": "MEDICAL",
    r"\s*&\s*": " AND ",
    r"\s*/\s*": " AND ",
    r"\bNO\.? ?(?=\d)": "",
    r"#\s*": "",
    r"^FD ?(\d+)$": r"FIRE DISTRICT \1",
}


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
                if explicit or state == "FL" or re.search(rf"\b{word}\b", outside):
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
    for key, title, body, pattern in MUNICIPAL + (COMMISSIONS if state == "FL" else []):
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


def _special(office: str, county: str) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", office.upper().replace("&AMP;", "&")).strip()
    if SPECIAL_SKIP.search(text):
        return None
    remainder = VACANCY.search(text) is not None
    text = re.sub(r"\bBORO\b", "BOROUGH", re.sub(r"\bTWP\b", "TOWNSHIP", text))
    role = SPECIAL_ROLE.match(text)
    text = re.sub(r"\s*\((?:UNEXPIRED TERM|VOTE (?:FOR )?\d+)\)", "", text)
    text = SPECIAL_TAIL.sub("", SPECIAL_ROLE.sub("", text)).strip(" ,-/:")
    text = re.sub(r"^(TOWNSHIP|BOROUGH|CITY|TOWN|VILLAGE) OF (.+)$", r"\2 \1", text)
    port = re.fullmatch(r"(?!.*IMPROVEMENT)(PORT OF (?:THE )?[A-Z .'-]+?)(?: DISTRICT)?", text)
    found = None if port else SPECIAL.search(text)
    if port:
        kind, phrase, head, tail = "port", "", port[1], ""
    elif found is not None and found.lastgroup is not None:
        kind, phrase, head, tail = found.lastgroup, found[0], text[: found.start()], text[found.end() :]
    elif role is not None and "FIRE" in role[0] and (bare := re.fullmatch(r"([A-Z .'-]+?)(?: (\d+))?", text)):
        kind, phrase, head, tail = "fire", "FIRE DISTRICT", bare[1], bare[2] or ""
    else:
        return None
    head, tail = re.sub(r"^(?:CITY|TOWN) OF\s+", "", head.strip(" ,-/:")), tail.strip(" ,-/:")
    tail = re.sub(r"^(?:BOARD OF [A-Z]+|BOARD|DIRECTOR|COMMISSIONER|TRUSTEE|MEMBER)S?\b\s*[-,]?\s*", "", tail)
    seat = re.match(r"((?:ZONE|AT[- ]LARGE|PRECINCT|PLACE|POSITION(?: NO\.?)?|SEAT|OFFICE) ?#? ?\d*)\s+(.+)$", head)
    if seat and not tail:
        tail, head = seat[1], seat[2]
    named = head not in ("", "COUNTY")
    if ", " in head and not tail:
        tail, head = head.split(", ", 1)
        nested = _special(f"{head} {phrase}", county)
        return None if nested is None else {**nested, "office": office, "rest": tail, "remainder": remainder}
    number = re.fullmatch(r"(?:BOARD )?NO\.? ?(\d+)", tail)
    if number:
        phrase, tail = f"{phrase} {number[1]}", ""
    if tail.startswith("OF "):
        phrase, tail, named = f"{phrase} {tail}", "", True
    if not named and tail and not SEAT.fullmatch(tail):
        head, tail, named = tail, "", True
        if number := re.fullmatch(r"(.+?) (\d+)", head):
            head, tail = number[1], number[2]
    if re.search(
        r"\b(?:BOARD|SUPERVISORS?|DIRECTORS?|COMMISS?IONE?R?S?|COMMISSON|PLACE|SEAT|ZONE|AT LARGE|POSITION|OFFICE|PRECINCT"
        r"|NO|MILLS|YRS|VOTE|PERMANENT|DISTRICT)\b|[()&#,]|^\d|^[A-Z]\d+$",
        head,
    ):
        return None
    if tail and SPECIAL.search(tail) and head in tail:
        tail = ""
    if tail and (not SEAT.fullmatch(tail) or SPECIAL.search(tail)):
        return None
    if not named:
        head = county.upper() if kind == "conservation" else f"{county.upper()} COUNTY"
    if head.endswith(" COUNTY") and phrase.startswith("COUNTY "):
        phrase = phrase.removeprefix("COUNTY ")
    for pattern, word in SPECIAL_WORDS.items():
        phrase = re.sub(pattern, word, phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    phrase = re.sub(r"^((?:(?!DISTRICT).)+?) (\d+)$", r"\1 DISTRICT \2", phrase) if "DISTRICT" not in phrase else phrase
    if phrase and not re.search(r"DISTRICT|AUTHORITY|ZONE|PORT|COMMISSION|DEPARTMENT|HOSPITAL|PUD|\d|OF .+$", phrase):
        phrase += " DISTRICT"
    place = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), f"{head} {phrase}".strip().title())
    place = re.sub(r"(?<=[a-z])'S\b", "'s", place)
    place = re.sub(r"(?<=\s)(Of|The|And)\b", lambda match: match[0].lower(), place)
    place = re.sub(
        r"\b(?:Pud|Ii|Iii|Iv|(?!St\b|Mt\b|Ft\b)[B-DF-HJ-NP-TV-Z][b-df-hj-np-tv-z]{1,3})\b",
        lambda match: match[0].upper(),
        place,
    )
    title = {"hospital": "Board of Trustees", "conservation": "Board of Supervisors"}.get(kind, "Board of Directors")
    return {
        "office": office,
        "code": f"{kind}:{slug(place)}",
        "scheme": "us.special_district",
        "place": place,
        "key": "special",
        "title": title,
        "body": True,
        "rest": tail or None,
        "remainder": remainder,
        "stem": f"{place} {title}",
        "named": named,
    }


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
            """with county as materialized (select c.code, g.geom from area a join area_code c on c.area_id = a.id
                 join area_geometry g on g.area_id = a.id
                 where a.scheme_code = 'us.county' and g.valid && daterange('2016-01-01', null))
               select f.code, array(select x.code from (select county.code,
                     st_area(st_intersection(county.geom, s.geom)) as shared
                   from county where left(county.code, 2) = left(f.code, 2) and county.geom && s.geom) x
                 where x.shared > 0.01 * s.area order by x.shared desc), f.option
               from unnest(%s::text[], %s::text[], %s::text[]) as f(code, geojson, option)
               cross join lateral (select geom, st_area(geom) as area
                 from st_makevalid(st_transform(st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)) as geom) s""",
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
        jurisdiction_name=pl.col("jurisdiction").str.to_uppercase(),
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
        pl.col("jurisdiction_name").str.to_uppercase(),
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
                jurisdiction_name=pl.lit(""),
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
    florida = (
        pl.read_parquet(raw.parsed("us.electionsfl") / "precincts.parquet")
        .filter(
            ~pl.col("title").str.contains("(?i)preference"),
            pl.col("day") < date.today(),
            (pl.col("county") != "DUV") | ~pl.col("election").is_in(list(JACKSONVILLE)),
        )
        .with_columns(
            party=pl.col("party").str.to_uppercase().fill_null(""),
            placed=pl.concat_str("county", pl.lit(":"), "election").replace_strict(PLACED, default=None),
        )
        .with_columns(
            contest=pl.when(
                pl.col("placed").is_not_null()
                & pl.col("contest").str.contains(r"(?i)^(?:interim )?(?:city comm|mayor\b)")
            )
            .then(
                pl.concat_str(
                    "placed",
                    pl.lit(" "),
                    pl.when(pl.col("placed") == "ORLANDO")
                    .then(
                        pl.col("contest")
                        .str.replace(r"(?i)^interim city commissioner(.*)$", "City Council$1 Unexpired")
                        .str.replace(r"(?i)^city commissioner", "City Council")
                    )
                    .otherwise("contest"),
                )
            )
            .otherwise("contest")
        )
        .drop("placed")
        .with_columns(
            contest=pl.concat_str("county", pl.lit("|"), "contest")
            .str.replace_many(
                {
                    f"{county}|{short} ": f"{county}|{long} "
                    for county, places in ABBREVIATED.items()
                    for short, long in places.items()
                }
            )
            .str.replace(r"^[^|]*\|", "")
        )
        .filter(
            ~pl.col("title").str.contains("(?i)primary")
            | pl.col("party").is_in(["", "NOP", "NON", "NP", "NPA"]).all().over("county", "election", "contest")
        )
    )
    electionsfl = [
        (
            "FL",
            frame.lazy().select(
                "day",
                pl.lit("FL").alias("state_po"),
                office=pl.col("contest")
                .str.to_uppercase()
                .str.replace(count, "")
                .str.replace(r"\bDISTRICT SUPERVISORS?\b", "DISTRICT")
                .str.strip_chars(),
                district=pl.lit(None, pl.String),
                special=pl.lit("FALSE"),
                county_fips="fips",
                county_name="county_name",
                precinct="precinct",
                jurisdiction_fips="fips",
                jurisdiction_name="county_name",
                stage=pl.lit("GEN"),
                mode=pl.lit("TOTAL"),
                candidate=pl.when(pl.col("candidate").str.contains(r"(?i)^write[- ]?ins?$"))
                .then(pl.lit("WRITE-IN"))
                .otherwise("candidate"),
                party_detailed=pl.col("party")
                .replace(OPENELECTIONS_PARTIES)
                .pipe(lambda party: pl.when(~party.is_in(["NON", "NP", "NPA", "NOP", ""])).then(party)),
                writein=pl.when(pl.col("candidate").str.contains(r"(?i)^write[- ]?ins?$"))
                .then(pl.lit("TRUE"))
                .otherwise(pl.lit("FALSE")),
                votes=pl.col("votes").cast(pl.String),
                magnitude=pl.col("contest")
                .str.extract(count)
                .str.to_uppercase()
                .replace(COUNTS)
                .cast(pl.Int32, strict=False),
                origin=pl.concat_str(pl.lit("electionsfl:"), "county"),
            ),
        )
        for _, frame in sorted(florida.partition_by("day", as_dict=True).items())
    ]
    for state, origin, scan in [
        *((state, "medsl", scan) for state, scan in inputs),
        *((state, "openelections", scan) for state, scan in openelections),
        *((state, "electionsfl", scan) for state, scan in electionsfl),
    ]:
        if state in COVERED:
            continue
        frame = (
            scan.with_columns(
                pl.col("origin") if origin == "electionsfl" else pl.lit(origin).alias("origin"),
                pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " "),
                pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
            .filter(
                pl.col("candidate").is_not_null(),
                pl.col("office").is_not_null(),
                pl.col("county_fips").is_not_null(),
                ~pl.col("candidate")
                .str.to_uppercase()
                .is_in([*DROPPED, *VACANT, "OVER VOTES", "UNDER VOTES", "BLANK"]),
            )
            .collect()
        )
        if frame.height == 0:
            continue
        held = frame["day"][0]
        year = held.year
        prefix = str(year) if held.month == 11 and held.day <= 8 and year % 2 == 0 else str(held)
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
            frame.with_columns(
                candidate=pl.col("candidate")
                .map_elements(
                    lambda text: html.unescape(re.sub(r"&([A-Za-z]+);", lambda entity: f"&{entity[1].lower()};", text)),
                    return_dtype=pl.String,
                )
                .str.replace(r"(?i)^write[- ]?in:\s*", "")
                .str.replace(r"(?i)\s*\((?:w/i|write[- ]?in)\)$", "")
                .str.replace(r"(?i)\s+write[- ]?in$", "")
                .str.replace(r'(?i)\s*"declared write[- ]?in"', "")
                .str.replace(r"(?i)\s+-\s+[\d,]+\s+valid votes$", "")
            )
            .with_columns(
                candidate=pl.when(
                    pl.col("candidate").str.to_lowercase().str.starts_with(pl.col("office").str.to_lowercase() + " ")
                )
                .then(pl.col("candidate").str.slice(pl.col("office").str.len_chars() + 1))
                .otherwise("candidate")
            )
            .filter(
                ~pl.col("candidate").str.contains(
                    r"(?i)^(?:no name|no one|no petition filed|no vote|(?:\w+ )?continued)$"
                )
            )
            .with_columns(total=(pl.col("mode") == "TOTAL").any().over(*precinct, "candidate", "party_detailed"))
            .filter(pl.col("total") == (pl.col("mode") == "TOTAL"))
            .with_columns(
                pooled=pl.col("candidate").str.to_uppercase().is_in([*POOLED, "SCATTERING"])
                | (pl.col("writein") == "TRUE")
                | pl.col("candidate").str.contains(r"(?i)\bwrite[- ]?ins?\b")
            )
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
            elif found is None and state != "WA" and (served := _special(office, county or "")) is not None:
                labels.append({**served, "county_fips": fips})
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
        special = frame.filter(pl.col("scheme") == "us.special_district")
        if special.height:
            merged = (
                special.filter(~pl.col("pooled"))
                .group_by("code", "county_fips", "office")
                .agg(pl.col("votes").sum(), pl.col("place").first(), pl.col("stem").first())
                .sort("county_fips", "votes", "office", descending=[False, True, False])
                .group_by("code", maintain_order=True)
                .agg(
                    pl.col("county_fips").first().alias("recoded"),
                    pl.col("office").first().alias("label"),
                    pl.col("place").first().alias("name"),
                    pl.col("stem").first().alias("heading"),
                )
                .with_columns(recoded=pl.concat_str("recoded", pl.lit(":"), "code"))
            )
            frame = pl.concat(
                [
                    frame.filter(pl.col("scheme") != "us.special_district"),
                    special.join(merged, on="code")
                    .with_columns(code="recoded", office="label", place="name", stem="heading")
                    .drop("recoded", "label", "name", "heading"),
                ],
                how="diagonal_relaxed",
            )
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
        shared = frame.join(spread, on=[*race, "county_fips"], nulls_equal=True)
        judicial = shared.filter(pl.col("key").is_in(list(PROSECUTORS)))
        for office, district in (
            shared.filter(~pl.col("key").is_in(list(PROSECUTORS))).select("office", "district").unique().iter_rows()
        ):
            problems.append(f"{year} {state}: {office} {district or ''} spans counties")
        frame = frame.join(spread, on=[*race, "county_fips"], how="anti", nulls_equal=True)
        if judicial.height:
            groups = (
                judicial.filter(~pl.col("pooled"))
                .group_by(*race, "county_fips")
                .agg(pl.col("candidate").unique().sort().str.join("|").alias("slate"), pl.col("county_name").first())
                .group_by(*race, "slate")
                .agg(pl.col("county_fips").sort(), pl.col("county_name").sort_by("county_fips"))
            )
            relabeled = []
            for office, district, special, _, fips, names in groups.iter_rows():
                number = re.search(r"\d+", district or "")
                ident = str(int(number[0])) if number else fips[0]
                ordinal = number and (
                    f"{int(number[0])}"
                    + (
                        "th"
                        if int(number[0]) % 100 in (11, 12, 13)
                        else {1: "st", 2: "nd", 3: "rd"}.get(int(number[0]) % 10, "th")
                    )
                )
                counties = [name.title() for name in names]
                place = (
                    f"{ordinal} Judicial District"
                    if number and "JUDICIAL" in (district or "").upper() or number and state == "AR"
                    else f"{ordinal} Judicial Circuit"
                    if number and state == "FL"
                    else f"Prosecutorial District {ident}"
                    if number and state == "ME"
                    else f"District Attorney District {ident}"
                    if number
                    else "Judicial District of "
                    + (
                        f"{counties[0]} and {counties[1]}"
                        if len(counties) == 2
                        else ", ".join(counties[:-1]) + f", and {counties[-1]}"
                    )
                    + " Counties"
                )
                relabeled += [
                    {
                        "office": office,
                        "district": district,
                        "special": special,
                        "county_fips": county,
                        "judicial": f"{state}:{ident}",
                        "district_name": place,
                    }
                    for county in fips
                ]
            judicial = judicial.join(
                pl.DataFrame(
                    relabeled,
                    schema={
                        name: pl.String
                        for name in ("office", "district", "special", "county_fips", "judicial", "district_name")
                    },
                ),
                on=[*race, "county_fips"],
                nulls_equal=True,
            ).with_columns(
                code=pl.col("judicial"),
                scheme=pl.lit("us.judicial_district"),
                place=pl.col("district_name"),
                stem=pl.when(pl.col("district_name").str.starts_with(pl.col("title")))
                .then("district_name")
                .otherwise(pl.concat_str(pl.col("title"), pl.lit(", "), pl.col("district_name"))),
            )
            frame = pl.concat([frame, judicial.drop("judicial", "district_name")], how="diagonal_relaxed")
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
        ).with_columns(seat=pl.when(pl.col("scheme") != "us.judicial_district").then("seat"))
        if year == 2024 and state == "AZ":
            maricopa = (pl.col("county_fips") == "04013") & (pl.col("key") == "constable") & pl.col("seat").is_null()
            justices = (
                frame.filter(maricopa & ~pl.col("pooled"))
                .select(
                    "jurisdiction_name",
                    "precinct",
                    justice=pl.col("candidate")
                    .str.to_uppercase()
                    .replace_strict(MARICOPA_CONSTABLES, default=None)
                    .map_elements(lambda name: f"{name} Justice Precinct", return_dtype=pl.String),
                )
                .unique(["jurisdiction_name", "precinct"])
            )
            frame = (
                frame.join(justices, on=["jurisdiction_name", "precinct"], how="left", nulls_equal=True)
                .with_columns(
                    seat=pl.when(maricopa).then("justice").otherwise("seat"),
                    remainder=pl.when(maricopa & (pl.col("justice") == "Agua Fria Justice Precinct"))
                    .then(True)
                    .otherwise("remainder"),
                )
                .drop("justice")
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
                pl.col("body")
                & pl.col("seat").is_null()
                & (
                    (year == 2016)
                    | (pl.col("origin") == "openelections")
                    | pl.col("origin").str.starts_with("electionsfl")
                )
            )
            .then("magnitude")
            .otherwise(pl.col("magnitude").fill_null(1)),
            district=pl.col("seat").is_not_null()
            & (
                (pl.col("precincts") < 0.9 * pl.col("precincts").max().over("code"))
                | (pl.col("code") == "1253000") & pl.col("seat").str.starts_with("District")
            ),
        )
        contests.append(summary.drop("offices", "precincts"))
        lines.append(
            frame.join(summary.select(contest), on=contest, nulls_equal=True)
            .with_columns(
                candidate=pl.when("pooled").then(pl.lit("WRITE-IN")).otherwise("candidate"),
                party_detailed=pl.when("pooled").then(None).otherwise("party_detailed"),
            )
            .with_columns(
                candidate=pl.when(
                    pl.col("candidate")
                    .str.to_lowercase()
                    .str.starts_with(pl.col("key").str.replace_all("-", " ") + " ")
                )
                .then(pl.col("candidate").str.slice(pl.col("key").str.len_chars() + 1))
                .otherwise("candidate")
                .str.replace(r"(?i)^(?:circuit clerk|county clerk|sheriff|assessor|constable|mayor) ", "")
            )
            .with_columns(
                unit=pl.when(
                    (pl.col("precinct").fill_null("").str.strip_chars() != "")
                    & ~pl.col("precinct").fill_null("").str.to_uppercase().str.contains("TOTAL")
                )
                .then(
                    pl.concat_str(
                        pl.lit(prefix),
                        "state_po",
                        pl.col("county_fips").replace(RECODED),
                        pl.col("jurisdiction_name").fill_null(""),
                        pl.col("precinct").str.strip_chars(),
                        separator=":",
                    )
                )
                .otherwise(pl.lit(""))
            )
            .group_by(*contest, "origin", "candidate", "pooled", "unit", "party_detailed")
            .agg(
                pl.when(pl.col("votes").is_null().all()).then(None).otherwise(pl.col("votes").sum()).alias("votes"),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    contest = ["day", "code", "key", "seat", "remainder"]
    every = pl.concat(contests, how="diagonal_relaxed")
    official = pl.col("origin").str.starts_with("electionsfl")
    superseded = every.filter(~official).join(every.filter(official).select(contest), on=contest, nulls_equal=True)
    every.join(superseded.select(*contest, "origin"), on=[*contest, "origin"], how="anti", nulls_equal=True).sort(
        "state_po", "code", "key", "seat", nulls_last=False
    ).write_parquet(PARSED / "contests.parquet")
    pl.concat(lines).join(
        superseded.select(*contest, "origin"), on=[*contest, "origin"], how="anti", nulls_equal=True
    ).write_parquet(PARSED / "lines.parquet")
    print(f"{superseded.height} MEDSL and OpenElections contests superseded by county election night reporting")
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
        zeroed = cs.zeroed() | cs.remainders(
            "An over vote, under vote, blank, or scattering line in MEDSL's files read as a candidate",
            r"^(over ?votes?|under ?votes?|blank|scattering)$",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        commencing: dict[UUID, list[tuple[UUID, str, str, str]]] = {}
        for old, entity, text, lang, kind in conn.execute(
            "select id, entity_id, text, lang, kind from name where is_preferred and text ~* 'term commencing \\d' "
        ).fetchall():
            commencing.setdefault(entity, []).append((old, text, lang, kind))

        def retitle(entity: UUID, text: str) -> None:
            cs.add("name", id=None, entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)
            for old, before, lang, kind in commencing.get(entity, []):
                cs.add("name", id=old, entity_id=entity, text=before, lang=lang, kind=kind, is_preferred=False)

        def title(text: str) -> str:
            text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
            return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            label = None if label is None else label.strip().upper().removesuffix(" PARTY")
            if not label or label in NONPARTISAN:
                return None, False
            label = EXPANDED.get(label, label)
            if label in INDEPENDENT or label in UNAFFILIATED or "PETITION" in label:
                return None, True
            if "/" in label and not all(party(state, part)[1] for part in label.split("/")):
                return next((id for part in label.split("/") if (id := party(state, part)[0]) is not None), None), False
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

        for (value,) in conn.execute(
            "select value from external_id where scheme = 'us.party' and value = any(%s)", [list(COMBINED)]
        ).fetchall():
            cs.add(
                "entity_retirement",
                entity_id=cs.key("us.party", value),
                reason="Two parties' fusion line in MEDSL's file, minted as one party. The line keeps its votes and "
                "names the first party; the candidate is nominated by each.",
            )
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
                    license=UNLICENSED,
                )
            cs.artifact(
                raw.cached(SOURCE, OPENELECTIONS.format(state=state.lower(), commit=commit, day=day)),
                contributed[state],
            )
        florida = {}
        held = pl.read_parquet(PARSED / "contests.parquet").filter(pl.col("origin").str.starts_with("electionsfl"))
        reports = pl.read_parquet(raw.parsed("us.electionsfl") / "precincts.parquet").select(
            "county", "election", "day"
        )
        for county in sorted({origin.split(":")[1] for origin in held["origin"]}):
            found = conn.execute(
                """select n.text from area_code c join polity_territory t on t.area_id = c.area_id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en' where c.code = %s""",
                [FLORIDA[county]],
            ).fetchone()
            publisher = f"{found[0] if found else county} Supervisor of Elections"
            source = florida[county] = cs.source(publisher, FLORIDA_TITLE)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=FLORIDA_TITLE,
                url=f"https://enr.electionsfl.org/{county}/",
                license="Public record",
            )
            days = set(held.filter(pl.col("origin") == f"electionsfl:{county}")["day"])
            for (number,) in (
                reports.filter(pl.col("county") == county, pl.col("day").is_in(days))
                .select("election")
                .unique()
                .sort("election")
                .iter_rows()
            ):
                cs.artifact(raw.cached("us.electionsfl", REPORTS.format(county=county, election=number)), source)
                cs.artifact(raw.cached("us.electionsfl", str(export(county, number))), source)
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
                [
                    [*DATASETS, *set(contests.filter(pl.col("state_po") == "FL")["day"])],
                    [*sources.values(), *contributed.values(), *florida.values()],
                ],
            ).fetchall()
        )
        taken |= {(day, re.sub(r"-members?$", "", value)) for day, value in taken}
        held_fl = set(
            conn.execute(
                """select to_char(e.date_from, 'YYYY-MM'), x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body') and x.value like 'FL:%%'
                   where r.source_id <> all(%s)""",
                [[*sources.values(), *contributed.values(), *florida.values()]],
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
        judicial = contests.filter(pl.col("scheme") == "us.judicial_district").select("state_po", "code", "place")
        if judicial.height:
            cs.add(
                "scheme",
                code="us.judicial_district",
                polity_id=cs.polity("US"),
                kind="electoral_district",
                rank=2,
                noun="judicial district",
            )
            states = {
                iso[3:]: (code, text)
                for iso, code, text in conn.execute(
                    """select p.iso_code, u.code, n.text from reporting_unit u join polity p on p.id = u.polity_id
                       join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                       where u.scheme_code = 'us.state'"""
                ).fetchall()
            }
        for state, code, display in judicial.unique().sort("code").iter_rows():
            polity = cs.key("us.polity", f"judicial:{code}")
            path = f"us/{state.lower()}/{slug(display)}"
            if slugs.get(path, polity) != polity:
                raise ValueError(f"{display} would take {path}")
            slugs[path] = polity
            polities[("us.judicial_district", code)] = (polity, display, path)
            if polity not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            name(polity, f"{states[state][1]} {display}")
            cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{state}"))
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.judicial_district", code)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", states[state][0]))
            name(area, f"{display}, {state}")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.judicial_district", code),
                polity_id=polity,
                scheme_code="us.judicial_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        special = contests.filter(pl.col("scheme") == "us.special_district").select("state_po", "code", "place")
        if special.height:
            cs.add(
                "scheme",
                code="us.special_district",
                polity_id=cs.polity("US"),
                kind="administrative",
                rank=3,
                noun="special district",
            )
        for state, code, display in special.unique(["code"]).sort("code").iter_rows():
            polity = cs.key("us.polity", f"special:{code}")
            county, _, path = polities[("us.county", code[:5])]
            path = f"{path}/{slug(display)}"
            if slugs.get(path, polity) != polity:
                raise ValueError(f"{display} would take {path}")
            slugs[path] = polity
            polities[("us.special_district", code)] = (polity, display, path)
            if polity not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="special_district", iso_code=None)
            name(polity, display)
            cs.add("polity_parent", polity_id=polity, parent_id=county)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.special_district", code)
            cs.add("area", id=area, scheme_code="us.special_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", code[:5]))
            name(area, f"{display}, {state}")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.special_district", code),
                polity_id=polity,
                scheme_code="us.special_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "two_round", "referendum")}
        firsts: list[tuple[UUID, UUID, UUID, date, str]] = []
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, block, and two_round systems must already exist")

        owners: dict[tuple[str, str], UUID] = {}
        untyped = dict(
            conn.execute(
                """select o.id, o.polity_id from office o join changeset c on c.id = o.changeset_id
                   join name n on n.entity_id = o.id and n.is_preferred
                   where c.pipeline = %s and o.kind = 'other' and n.text like 'Mayor of %%'""",
                [SOURCE],
            ).fetchall()
        )
        elections: dict[tuple[date, str], UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        values: dict[tuple[str, str, str], int] = {}
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20|22|24):'"
            ).fetchall()
        )
        parents: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        sets = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        }
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        grouped = {
            keys: rows for keys, rows in lines.group_by("day", "code", "key", "seat", "remainder", maintain_order=True)
        }
        surnamed = {
            group: rows["candidate"].unique().to_list()
            for group, rows in grouped.items()
            if rows.filter(~pl.col("pooled"))["candidate"].str.contains(r"^[A-Z'-]+$").any()
        }
        states = dict(zip(contests["code"], contests["state_po"], strict=True))
        same_day = finer({day for day, *_ in surnamed})
        history: dict[str, list[tuple[str, UUID | None]]] = defaultdict(list)
        held_by_contest: dict[tuple[date, str, UUID], list[tuple[str, UUID | None]]] = defaultdict(list)
        for day, value, contest, text, person in conn.execute(
            """select e.date_from, x.value, c.id, n.text, k.person_id from contest c
               join election e on e.id = c.election_id
               join external_id x on x.entity_id = coalesce(c.office_id, c.body_id) and x.scheme in ('us.office', 'us.body')
               join contest_option o on o.contest_id = c.id
               join candidacy k on k.option_id = o.id and k.role = 'head'
               join name n on n.entity_id = k.person_id and n.is_preferred
               join changeset g on g.id = k.changeset_id and g.pipeline <> %s
               where x.value = any(%s)""",
            [SOURCE, list({f"{states[code]}:{code}:{key}" for _, code, key, *_ in surnamed})],
        ).fetchall():
            history[value].append((text, person))
            held_by_contest[(day, value.split(":")[1], contest)].append((text, person))
        for (day, code, _), pool in held_by_contest.items():
            same_day[(day, code)].append(pool)
        surnames: set[str] = set()
        outside: dict[UUID, UUID] = dict(
            conn.execute(
                """select k.id, k.person_id from candidacy k join changeset g on g.id = k.changeset_id
                   join person p on p.id = k.person_id join changeset h on h.id = p.changeset_id
                   where k.role = 'head' and g.pipeline = %s and h.pipeline <> %s""",
                [SOURCE, SOURCE],
            ).fetchall()
        )
        saipe = {
            tokens(label): (f"{st}{district}", label)
            for st, district, label in fastexcel.read_excel(raw.cached(cog.SOURCE, cog.SAIPE).path.read_bytes())
            .load_sheet(0, header_row=2, dtypes="string")
            .to_polars()
            .select("State FIPS Code", "District ID", "Name")
            .iter_rows()
            if st == "21"
        }
        independents: dict[str, list[tuple[str, str, UUID | None]]] = defaultdict(list)
        for county, text, linked in conn.execute(
            """select county, name, polity_id from coverage_government
               where state = 'KY' and kind = 'school' and active and name ~* ' ind'"""
        ).fetchall():
            mine = tokens(text)
            found = [hit for theirs, hit in saipe.items() if theirs == mine] or [
                hit
                for hit in saipe.values()
                if "Independent" in hit[1] and hit[1].split()[0].split("-")[0] == text.split()[0]
            ]
            if len(found) == 1:
                independents[county].append((*found[0], linked))
        homes: dict[UUID, str] = dict(
            conn.execute(
                """select distinct on (p.id) p.id, s.path from polity p join slug s on s.entity_id = p.id
                   where p.kind = 'school_district' order by p.id, length(s.path)"""
            ).fetchall()
        )
        schooled: set[UUID] = set()
        districts_united: set[str] = set()
        survivors: dict[tuple[str, str], tuple[UUID, str, str]] = {}
        absorbed: dict[str, UUID] = {}
        for value, body, pipeline, text in conn.execute(
            """select x.value, b.id, g.pipeline, n.text
               from (select id, polity_id, changeset_id from body union all select id, polity_id, changeset_id from office) b
               join entity e on e.id = b.id and e.retired_by is null
               join polity p on p.id = b.polity_id and p.kind = 'county'
               join changeset g on g.id = b.changeset_id
               join external_id x on x.entity_id = b.id and x.scheme in ('us.body', 'us.office')
               left join name n on n.entity_id = b.id and n.is_preferred and n.lang = 'en'
               where x.value ~ '^[A-Z]{2}:[0-9]{5}:[a-z-]+$'
                 and not exists (select from entity_merge m where m.entity_id = b.id)
               order by (select count(*) from contest c where b.id in (c.body_id, c.office_id)), b.id"""
        ).fetchall():
            state, code, tail = value.split(":")
            role = (
                ("council" if state == "IN" and tail == "council" else "governing")
                if tail in GOVERNING
                else SYNONYMS.get(tail)
            )
            if role is not None and pipeline == SOURCE:
                absorbed[value] = body
            elif role is not None:
                survivors[(code, role)] = (body, tail, text)
        local_areas = dict(
            conn.execute(
                "select c.code, c.area_id from area_code c join area a on a.id = c.area_id"
                " where a.scheme_code = 'us.local_district'"
            ).fetchall()
        )
        local_units = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.local_district'").fetchall()
        )
        resolved = {
            (*group, name): full
            for group, names in surnamed.items()
            for name, full in resolve(
                names, same_day.get((group[0], group[1]), []), history[f"{states[group[1]]}:{group[1]}:{group[2]}"]
            ).items()
        }
        starts: dict[tuple[date, str, str], int] = {}
        for row in contests.iter_rows(named=True):
            if re.fullmatch(TERM, row["seat"] or "", re.IGNORECASE):
                group = (row["day"], row["code"], row["key"])
                starts[group] = starts.get(group, 0) + 1
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
            body_code, label, territory = code, seat, scheme
            if state == "KY" and key == "board-of-education":
                school = (
                    (*saipe[tokens(f"{place} School District")], None)
                    if seat and tokens(f"{place} School District") in saipe
                    else independents[code][0]
                    if not seat and len(independents[code]) == 1
                    else None
                )
                if school is not None:
                    body_code, place, territory = school[0], school[1], "us.school_district"
                    label = seat and re.sub(r"^(?:Educational )?(?:District|Division)\b", "Educational District", seat)
                    polity = school[2] if school[2] in homes else cs.key("us.polity", f"school:{body_code}")
                    path = homes.get(polity) or f"us/ky/{slug(place)}"
                    if polity in cs.minted and polity not in schooled:
                        if path in slugs:
                            raise ValueError(f"slug taken: {path} for school:{body_code}")
                        schooled.add(polity)
                        cs.add("polity", id=polity, kind="school_district", iso_code=None)
                        name(polity, place)
                        cs.add("polity_parent", polity_id=polity, parent_id=cs.polity("US-KY"))
                        cs.add("slug", path=path, entity_id=polity)
                        territory_area = cs.area(territory, body_code)
                        if territory_area in cs.minted:
                            cs.add("area", id=territory_area, scheme_code=territory)
                            cs.add("area_code", area_id=territory_area, code=body_code, valid_from=None)
                            cs.add("area_parent", area_id=territory_area, parent_id=cs.area("us.state", "21"))
                            name(territory_area, f"{place}, KY")
                        cs.add("polity_territory", polity_id=polity, area_id=territory_area)
                        cs.add(
                            "reporting_unit",
                            id=cs.unit(territory, body_code),
                            polity_id=polity,
                            scheme_code=territory,
                            code=body_code,
                            area_relation="equals",
                            area_id=territory_area,
                            kind="standard",
                            valid_from=None,
                        )
                    cs.renamed[f"{day}:{state}:{body_code}:{key}" + (f":{slug(label)}" if label else "")] = (
                        f"{day}:{state}:{code}:{key}" + (f":{slug(seat)}" if seat else "")
                    )
            official = row["origin"].startswith("electionsfl")
            if official and (f"{day:%Y-%m}", f"{state}:{code}:{key}") in held_fl:
                continue
            parent = cs.key("us.election", f"{day}:{state}")
            majority = state in ("GA", "LA") or official and day.month == 8
            if parent in cs.minted and official and parent not in elections.values():
                elections[(day, "FL")] = parent
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity("US-FL"),
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Florida local elections")
                cs.add("slug", path=f"us/fl/{day}-local", entity_id=parent)
            elif parent in cs.minted and not official:
                raise ValueError(f"no election {day}:{state}")
            survivor = (
                survivors.get(
                    (
                        code,
                        ("council" if state == "IN" and key == "council" else "governing")
                        if row["body"]
                        else SYNONYMS.get(key, ""),
                    )
                )
                if scheme == "us.county" and (key in GOVERNING if row["body"] else key in SYNONYMS)
                else None
            )
            if (day, f"{state}:{code}:{key}") in taken or survivor and (day, f"{state}:{code}:{survivor[1]}") in taken:
                continue
            contest_key = (
                f"{day}:{state}:{body_code}:{key}"
                + (f":{slug(label)}" if label else "")
                + (":remainder" if row["remainder"] else "")
            )
            contest_path = (
                f"{path}/{day}-general/{key}"
                + (f"-{slug(label)}" if label else "")
                + ("-unexpired" if row["remainder"] else "")
            )
            if contest_path in slugs and slugs[contest_path] != known.get(contest_key):
                continue
            if (day, body_code) not in elections:
                election = elections[(day, body_code)] = cs.key(
                    "us.election", f"{day}:{state}:{'sd' if territory == 'us.school_district' else ''}{body_code}"
                )
                if election in cs.minted:
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=parent,
                        kind="general",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(election, f"{day.year} {place} general election")
                    cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
            election = elections[(day, body_code)]
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
            if (code, key) not in owners and survivor is not None:
                owners[(code, key)] = survivor[0]
                if f"{state}:{code}:{key}" in absorbed:
                    cs.merge(absorbed[f"{state}:{code}:{key}"], survivor[0])
                if key == "council" and (survivor[2] or "").endswith("Board of Commissioners"):
                    name(survivor[0], office_title)
            if (body_code, key) not in owners:
                owner = owners[(body_code, key)] = cs.key(
                    "us.body" if body else "us.office", f"{state}:{body_code}:{key}"
                )
                mayoral = key == "mayor" and bool(row["stem"] or office_title.startswith("Mayor of "))
                if owner in cs.minted:
                    if body:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add("office", id=owner, polity_id=polity, kind="mayor" if mayoral else "other")
                    name(owner, office_title)
                elif mayoral and owner in untyped:
                    cs.add("office", id=owner, polity_id=untyped[owner], kind="mayor")
            whole, unit = cs.area(territory, body_code), cs.unit(territory, body_code)
            area = whole
            if row["district"]:
                district = (
                    f"{code[:2]}:{code}:{key}:{slug(seat)}"
                    if body_code == code
                    else f"{state}:{body_code}:{key}:{slug(label)}"
                )
                theirs = None if survivor is None else f"{code[:2]}:{code}:{survivor[1]}:{slug(seat)}"
                if theirs is not None and theirs in local_areas and theirs != district:
                    if district in local_areas:
                        cs.merge(local_areas[district], local_areas[theirs])
                    if district in local_units and theirs in local_units:
                        cs.merge(local_units[district], local_units[theirs])
                    district = theirs
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if district not in districts:
                    districts.add(district)
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{office_title}, {label}")
                if unit in cs.minted and district not in districts_united:
                    districts_united.add(district)
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
            named = grouped[(day, code, key, seat, row["remainder"])].filter(~pl.col("pooled"))["candidate"]
            measure = named.len() > 0 and bool(named.str.contains(ANSWER).all())
            magnitude = None if measure else row["magnitude"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question" if measure else "body_seats" if body else "office",
                office_id=None if body or measure else owners[(body_code, key)],
                body_id=owners[(body_code, key)] if body and not measure else None,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems[
                    "referendum"
                    if measure
                    else "two_round"
                    if magnitude == 1 and (majority or code in MAJORITY)
                    else "fptp"
                    if magnitude == 1
                    else "block"
                ],
                default_result_kind="counted",
                term=None if measure else "remainder" if row["remainder"] else "full",
            )
            if measure:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            term = re.fullmatch(TERM, seat or "", re.IGNORECASE)
            start = None if term is None else date(int(term[3]), int(term[1]), int(term[2]))
            begins = None if start is None else f"{start:%B} {start.day}, {start.year}"
            (name if start is None else retitle)(
                contest,
                (f"{day.year} {city} mayoral election" if key == "mayor" else f"{day.year} {office_title} election")
                + (
                    ""
                    if not seat or start is not None and starts[(day, code, key)] == 1
                    else f" (term from {begins})"
                    if start is not None
                    else f", {label}"
                )
                + (" (unexpired term)" if row["remainder"] else ""),
            )
            if start is not None and starts[(day, code, key)] == 1:
                cs.add(
                    "note",
                    id=uuid5(contest, "term"),
                    entity_id=contest,
                    kind="methodology",
                    body_md=f"The term begins {begins}.",
                )
            cs.add("slug", path=contest_path, entity_id=contest)
            if row["district"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT)
            if row["masked"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=MASKED)
            origin = row["origin"]
            source = (
                sources[day]
                if origin == "medsl"
                else florida[origin.split(":")[1]]
                if origin.startswith("electionsfl")
                else contributed[state]
            )
            counted = (
                grouped[(day, code, key, seat, row["remainder"])].filter(
                    pl.lit(measure) | pl.col("pooled") | ~pl.col("candidate").str.contains(ANSWER)
                )
            ).with_columns(
                token=pl.col("candidate").map_elements(slug, return_dtype=pl.String),
                line=pl.col("party_detailed")
                .map_elements(lambda label, state=state: str(party(state, label)[0] or ""), return_dtype=pl.String)
                .fill_null(""),
            )
            fused = set(
                counted.filter(~pl.col("pooled"), pl.col("line") != "")
                .group_by("token")
                .agg(pl.col("line").n_unique())
                .filter(pl.col("line") > 1)["token"]
            )
            stem = f"{contest_key}:precinct"
            previous = (
                cs.key(f"{SOURCE}.result_set", stem)
                if fused and stem in sets
                else cs.key(f"{SOURCE}.result_set", contest_key)
                if contest_key in sets
                else None
            )
            if previous is not None:
                cs.add(
                    "result_set",
                    id=previous,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=source,
                    is_default=False,
                    completeness=None if row["masked"] else 0 if counted["votes"].fill_null(0).sum() == 0 else 1,
                )
            result_set = cs.key(f"{SOURCE}.result_set", f"{stem}:lines" if fused else stem)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                supersedes_id=previous,
                completeness=None if row["masked"] else 0 if counted["votes"].fill_null(0).sum() == 0 else 1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.replace("2024", str(day.year))
                if origin == "medsl"
                else FLORIDA_METHOD
                if origin.startswith("electionsfl")
                else CONTRIBUTED,
            )
            rows = (
                counted.group_by("token", "pooled")
                .agg(
                    pl.col("candidate").sort_by("votes", descending=True, nulls_last=True).first(),
                    pl.col("party_detailed")
                    .sort_by("votes", "party_detailed", descending=True, nulls_last=True)
                    .drop_nulls()
                    .first(),
                    pl.when(pl.col("votes").is_null().all()).then(None).otherwise(pl.col("votes").sum()).alias("votes"),
                )
                .sort("votes", "candidate", descending=[True, False], nulls_last=True)
            )
            totals: list[tuple[int, UUID]] = []
            options: dict[tuple[str, bool, str], UUID] = {}
            for token, candidate, pooled, label, votes in rows.select(
                "token", "candidate", "pooled", "party_detailed", "votes"
            ).iter_rows():
                if pooled:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                elif measure:
                    answer = "Yes" if re.match(r"(?i)yes", candidate) else "No"
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "Yes" else 2,
                        ballot_label=answer,
                    )
                    totals.append((votes or 0, option))
                else:
                    full, known_person = resolved.get(
                        (day, code, key, seat, row["remainder"], candidate), (candidate, None)
                    )
                    if full != candidate:
                        surnames.add(f"{state}:{code}:{slug(candidate)}")
                    ballot = title(full) if full.isupper() else full
                    party_id, independent = (None, False) if token in fused else party(state, label)
                    person_key = (code, slug(full))
                    head = cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head")
                    if person_key not in persons:
                        persons[person_key] = (
                            known_person
                            or outside.get(head)
                            or cs.key(f"{SOURCE}.person", f"{state}:{code}:{slug(full)}")
                        )
                        if known_person is None:
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
                        id=head,
                        person_id=persons[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=independent,
                    )
                    totals.append((votes or 0, option))
                    nominees: list[UUID | None] = (
                        [party(state, part)[0] for part in label.split("/")]
                        if token not in fused and "/" in (label or "")
                        else []
                    )
                    if token in fused:
                        for line, labels in (
                            counted.filter(pl.col("token") == token)
                            .group_by("line")
                            .agg(pl.col("party_detailed").drop_nulls().unique().sort(), pl.col("votes").sum())
                            .sort("votes", "line", descending=[True, False])
                            .select("line", "party_detailed")
                            .iter_rows()
                        ):
                            label = labels[0] if labels else None
                            line_party = party(state, label)[0] if line else None
                            child = cs.key("us.option", f"{contest_key}:{slug(candidate)}:{slug(label or 'other')}")
                            cs.add(
                                "contest_option",
                                id=child,
                                contest_id=contest,
                                parent_option_id=option,
                                kind="ticket",
                                party_id=line_party,
                                ballot_label=f"{ballot} ({title(label) if label else 'Other lines'})",
                            )
                            if line:
                                nominees.extend(party(state, part)[0] for part in (label or "").split("/"))
                            options[(token, pooled, line)] = child
                    for nominee in dict.fromkeys(nominees):
                        if nominee is not None:
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=nominee, kind="nominated")
                options[(token, pooled, "")] = option
            if result_set in cs.minted or result_set in zeroed:
                for token, pooled, line, place, votes in (
                    counted.filter(pl.col("votes").is_not_null())
                    .select("token", "pooled", "line", "unit", "votes")
                    .iter_rows()
                ):
                    county = place.split(":")[2] if place else ""
                    target = unit
                    if county in parents:
                        target = precincts.get(place) or cs.unit("us.precinct", place)
                        if place not in precincts and place not in minted:
                            minted[place] = (county, day)
                        if area != parents[county][1] and votes > 0:
                            memberships.add((target, area, day))
                    cell = (
                        str(result_set),
                        str(target),
                        str(options[(token, pooled, line if token in fused else "")]),
                    )
                    values[cell] = values.get(cell, 0) + votes
            if magnitude is None or len(totals) > magnitude and totals[magnitude - 1][0] == totals[magnitude][0]:
                continue
            cast = rows["votes"].sum() or 0
            lead = [votes for votes, _ in totals[:2]] + [0, 0]
            rule = (
                MAJORITY.get(code) or ("majority" if majority else None) if magnitude == 1 and len(totals) > 1 else None
            )
            if rule is not None and not (
                lead[0] * 2 > cast
                or rule == "arkansas"
                and lead[0] * 10 >= cast * 4
                and (lead[0] - lead[1]) * 5 >= cast
                or rule == "forty"
                and lead[0] * 10 >= cast * 4
            ):
                for _, option in totals[:2]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                cs.add("note", entity_id=result_set, kind="context", body_md=RUNOFF)
                firsts.append((contest, owners[(code, key)], area, day, city))
                continue
            for _, option in totals[:magnitude]:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        chained: dict[str, int] = {}
        two_round = systems["two_round"]
        for first, owner, area, day, city in firsts:
            later = (
                conn.cursor(row_factory=dict_row)
                .execute(
                    """select c.* from contest c join election e on e.id = c.election_id
                   join entity n on n.id = c.id and n.retired_by is null
                   where (c.office_id = %(owner)s or c.body_id = %(owner)s)
                     and c.district_area_id is not distinct from %(area)s
                     and e.date_from > %(day)s and e.date_from <= %(day)s::date + 150 and c.id <> %(first)s
                   order by e.date_from limit 1""",
                    {"owner": owner, "area": area, "day": day, "first": first},
                )
                .fetchone()
            )
            if later is None:
                dates = {row["id"]: date.fromisoformat(str(row["date_from"])) for row in cs.rows.get("election", [])}
                later = min(
                    (
                        row
                        for row in cs.rows.get("contest", [])
                        if owner in (row.get("office_id"), row.get("body_id"))
                        and row.get("district_area_id") == area
                        and row["id"] != first
                        and day < dates.get(row["election_id"], day) <= day + timedelta(days=150)
                    ),
                    key=lambda row: dates[row["election_id"]],
                    default=None,
                )
            chained.setdefault(city, 0)
            if later is None:
                print(f"  runoff not held in the archive: {city} {day} {first}")
                continue
            chained[city] += 1
            runoff = {"previous_round_id": first, "round_number": 2, "electoral_system_id": two_round}
            staged = next((row for row in cs.rows.get("contest", []) if row["id"] == later["id"]), None)
            if staged is not None:
                staged.update(runoff)
                continue
            cs.add(
                "contest",
                **{
                    column: value
                    for column, value in later.items()
                    if column not in ("k", "changeset_id", "recorded_from", "notional")
                }
                | runoff,
            )
        print(f"{len(firsts)} first rounds short of the threshold; runoffs chained by city: {chained}")
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    (
                        str(cs.unit("us.precinct", place)),
                        str(cs.polity(f"US-{place.split(':')[1]}")),
                        "us.precinct",
                        place,
                        str(parents[county][0]),
                        "within",
                        str(parents[county][1]),
                        "standard",
                        day,
                        day + timedelta(days=1),
                    )
                    for place, (county, day) in minted.items()
                ],
                schema={
                    "id": pl.String,
                    "polity_id": pl.String,
                    "scheme_code": pl.String,
                    "code": pl.String,
                    "parent_id": pl.String,
                    "area_relation": pl.String,
                    "area_id": pl.String,
                    "kind": pl.String,
                    "valid_from": pl.Date,
                    "valid_to": pl.Date,
                },
                orient="row",
            ),
        )
        linked = {(target, area) for target, area, _ in memberships}
        stale = [
            pair
            for pair in conn.execute(
                """select ua.unit_id, ua.area_id from reporting_unit_area ua
                   join changeset g on g.id = ua.changeset_id and g.pipeline = %s
                   join reporting_unit u on u.id = ua.unit_id
                   where not exists (
                     select from contest c join result_set r on r.contest_id = c.id
                     join result_value v on v.result_set_k = r.k
                     where c.district_area_id = ua.area_id and v.unit_k = u.k and v.measure = 1 and v.value > 0)""",
                [SOURCE],
            ).fetchall()
            if pair not in linked
        ]
        for unit, area in stale:
            cs.add("unit_area_drop", unit_id=unit, area_id=area, reason="no votes in the district's contests")
        print(f"{len(memberships)} memberships; {len(stale)} dropped")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [(str(target), str(area), day, day + timedelta(days=1)) for target, area, day in memberships],
                schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                orient="row",
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                [(*cell, value) for cell, value in values.items()],
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        used = set(persons.values())
        for (person,) in conn.execute(
            """select x.entity_id from external_id x
               where x.scheme = %s and x.value = any(%s)
                 and not exists (select from entity_merge m where m.into_id = x.entity_id)
                 and not exists (
                   select from candidacy d join changeset c on c.id = d.changeset_id
                   where d.person_id = x.entity_id and c.pipeline <> %s
                 )""",
            [f"{SOURCE}.person", sorted(surnames), SOURCE],
        ).fetchall():
            if person not in used:
                cs.add(
                    "entity_retirement",
                    entity_id=person,
                    reason="A surname-only name in MEDSL's files, resolved to the full name",
                )
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "supersedes_id", "completeness")
        for row in conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                where x.value = any(%s) and r.status <> 'withdrawn'""",
            [sorted(SPLIT)],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="withdrawn", is_default=False)
            cs.add("note", id=uuid5(row[0], "withdrawn"), entity_id=row[0], kind="caveat", body_md=WITHDRAWN)
        for (contest,) in conn.execute(
            """select x.entity_id from external_id x where x.scheme = 'us.contest' and x.value = any(%s)
               and exists (select from result_set r where r.contest_id = x.entity_id)
               and not exists (select from result_set r where r.contest_id = x.entity_id and r.status <> 'withdrawn')""",
            [sorted(SPLIT)],
        ).fetchall():
            cs.add("entity_retirement", entity_id=contest, reason=RETIRED)
        listed = {row["id"] for row in cs.rows.get("contest_option", [])}
        for option, contest, parent, kind, ballot, combined, value, text, head in conn.execute(
            """select o.id, o.contest_id, o.parent_option_id, o.kind::text, o.ballot_label, o.party_id, x.value, n.text,
                      k.id
               from contest_option o join changeset g on g.id = o.changeset_id
               join external_id x on x.entity_id = o.party_id and x.scheme = 'us.party' and x.value = any(%s)
               join name n on n.entity_id = o.party_id and n.is_preferred
               left join candidacy k on k.option_id = coalesce(o.parent_option_id, o.id) and k.role = 'head'
               where g.pipeline = %s""",
            [list(COMBINED), SOURCE],
        ).fetchall():
            if option in listed:
                continue
            state, label = value.split(":")[0], text.upper().removesuffix(" PARTY")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                parent_option_id=parent,
                kind=kind,
                party_id=party(state, label)[0],
                ballot_label=ballot,
            )
            nominees = [
                *(
                    nominee
                    for (nominee,) in conn.execute(
                        "select party_id from candidacy_nomination where candidacy_id = %s and party_id <> %s",
                        [head, combined],
                    ).fetchall()
                ),
                *(party(state, part)[0] for part in label.split("/")),
            ]
            for nominee in dict.fromkeys(nominees):
                if head is not None and nominee is not None:
                    cs.add("candidacy_nomination", candidacy_id=head, party_id=nominee, kind="nominated")
        cs.pool("A scattering line in MEDSL's files read as a candidate")
        cs.remainders(
            "A write-in mark, vote tally, placeholder, office title, or ballot answer in MEDSL's files read as a candidate",
            r"^(?:yes|no)\y|\((?:w/i|write[- ]?in)\)$|^write[- ]?in\y|\swrite[- ]?in$|declared write|valid votes"
            r"|^(?:no name|no one|no petition filed|no vote|commission continued)$|&[a-z]+;"
            r"|^(?:circuit clerk|county clerk|sheriff|assessor|constable|mayor) ",
        )
        cs.clear()
        return cs.write()
