import csv
import html
import io
import json
import re
import subprocess
import time
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode
from uuid import UUID
from xml.etree import ElementTree

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import initials
from ea.sources.us import (
    elstats,
    fl_sos,
    me_legislature,
    medsl,
    nyc_general,
    tx_elchist,
    ut_canvass,
    ut_primaries,
    wa_local,
)
from ea.sources.us.city_precincts import WA_COUNTIES
from ea.sources.us.county_changes import ct_regions
from ea.sources.us.ncsbe import COLUMNS, FILES, HEADERLESS, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.state_primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
NC_DAYS = {
    date(2002, 9, 10): None,
    date(2004, 7, 20): None,
    date(2004, 8, 17): date(2004, 7, 20),
    date(2006, 5, 2): None,
    date(2006, 5, 30): date(2006, 5, 2),
    date(2008, 5, 6): None,
    date(2010, 5, 4): None,
    date(2010, 6, 22): date(2010, 5, 4),
    date(2012, 5, 8): None,
    date(2012, 7, 17): date(2012, 5, 8),
    date(2014, 5, 6): None,
    date(2014, 7, 15): date(2014, 5, 6),
    date(2016, 3, 15): None,
    date(2018, 5, 8): None,
    date(2020, 3, 3): None,
    date(2020, 6, 23): date(2020, 3, 3),
    date(2022, 5, 17): None,
}
MN_DAYS = [date(2016, 8, 9), date(2018, 8, 14), date(2020, 8, 11), date(2022, 8, 9)]
MN_FILES = "https://electionresultsfiles.sos.mn.gov/{day:%Y%m%d}/{name}.txt"
WA_DAYS = [
    date(2015, 8, 4),
    date(2016, 8, 2),
    date(2017, 8, 1),
    date(2018, 8, 7),
    date(2019, 8, 6),
    date(2020, 8, 4),
    date(2022, 8, 2),
]
CA_SOV = "https://elections.cdn.sos.ca.gov/sov/{path}"
CA_KINDS = [
    (r"us.?s(en|-ft)|uss", "us-senate"),
    (r"congress|us.?rep|-cd$", "house"),
    (r"assem|-ad$", "assembly"),
    (r"senat|stsen", "senate"),
    (r"lt|lg|lieutenant", "lieutenant-governor"),
    (r"gov", "governor"),
    (r"sos|secretary", "secretary-of-state"),
    (r"cont", "controller"),
    (r"trea|tres", "treasurer"),
    (r"ag$|attorney", "attorney-general"),
    (r"ic$|ins|commissioner", "insurance-commissioner"),
]
CA_TITLE = re.compile(
    r"(\d+)(?:st|nd|rd|th) (?:Congressional|Senatorial|State Senate|Senate|Assembly) District", re.IGNORECASE
)
CA_PARTY = re.compile(r"(DEM|REP|LIB|NPP|GRN|PF|AI|REF|NL|Libertarian|Natural Law|Green|Reform)(?: \(W/I\))?")
CA_CODES = {"PF": "PAF", "AI": "AIP", "Libertarian": "LIB", "Natural Law": "NL", "Green": "GRN", "Reform": "REF"}
CA_STATEWIDE = (
    "governor",
    "lieutenant-governor",
    "secretary-of-state",
    "controller",
    "treasurer",
    "attorney-general",
    "insurance-commissioner",
)
CA_DAYS = {
    date(2002, 3, 5): (
        "2002-primary/{}.xls",
        ["gov", "ltgov", "sos", "controller", "treasurer", "ag", "ic", "congress", "state-senate", "state-assemb"],
    ),
    date(2004, 3, 2): ("2004-primary/{}.xls", ["us_senate_formatted", "congress", "senate", "assembly"]),
    date(2006, 6, 6): (
        "2006-primary/sov_detail_primary_{}.xls",
        ["gov", "ltgov", "sos", "controller", "treas", "ag", "inscomm", "ussenate", "congress", "senate", "assembly"],
    ),
    date(2008, 6, 3): (
        "2008-statewide-direct-primary/{}.xls",
        ["us_reps08primary", "stsen08primary", "stassem08primary"],
    ),
    date(2010, 6, 8): (
        "2010-primary/{}.xls",
        [
            "40-51-gov",
            "52-57-lg",
            "58-60-sos",
            "61-63-cont",
            "64-66-tres",
            "67-72-ag",
            "73-75-ins",
            "79-84-ussen",
            "85-95-cd",
            "96-100-senate",
            "101-118-ad",
        ],
    ),
    date(2012, 6, 5): (
        "2012-primary/{}.xls",
        [
            "70-us-senate-formatted",
            "82-us-reps-formatted",
            "97-state-senators-formatted",
            "102-state-assembly1-80formattted",
        ],
    ),
    date(2014, 6, 3): (
        "2014-primary/xls/{}.xls",
        [
            "33-governor",
            "42-lieutenant-governor",
            "45-secretary-of-state",
            "48-controller",
            "51-treasurer",
            "54-attorney-general",
            "57-insurance-commissioner",
            "63-congress",
            "78-state-senator",
            "84-state-assemblymember",
        ],
    ),
    date(2016, 6, 7): (
        "2016-primary/{}.xls",
        [
            "75-us-senate-formatted",
            "90-us-reps-formatted",
            "105-state-senators-formatted",
            "110-state-assembly-formatted",
        ],
    ),
    date(2018, 6, 5): (
        "2018-primary/sov/{}.xls",
        [
            "31-governor",
            "43-lieutenant-governor",
            "49-secretary-of-state",
            "52-controller",
            "55-treasurer",
            "58-attorney-general",
            "61-insurance-commissioner",
            "67-us-senators",
            "82-congress",
            "101-state-senator",
            "107-state-assemblymember",
        ],
    ),
    date(2020, 3, 3): (
        "2020-primary/sov/{}.xls",
        ["126-us-rep-congress", "143-state-senators-formatted", "148-state-assembly-formatted"],
    ),
    date(2022, 6, 7): (
        "2022-primary/sov/{}.xlsx",
        [
            "30-governor",
            "42-lieutenant-governor",
            "48-secretary-of-state",
            "51-controller",
            "54-treasurer",
            "57-ag",
            "60-commissioner",
            "68-uss-ft",
            "86-congress",
            "99-state-senator",
            "104-state-assemblymember",
        ],
    ),
}
ELSTATS = {
    "CO": {13: "house", 5: "senate"},
    "CT": {49: "house", 48: "senate"},
    "ID": {569: "house", 570: "house", 5: "senate"},
    "NH": {8: "house", 9: "senate"},
    "NY": {14: "assembly", 5: "senate"},
    "VT": {8: "house", 9: "senate"},
    "VA": {8: "house-of-delegates", 9: "senate"},
    "MA": {8: "house", 9: "senate"},
    "NM": {23: "house", 95: "senate"},
}
ELSTATS = {state: offices for state, offices in ELSTATS.items() if state not in elstats.HELD}
ELSTATS_YEARS = range(2000, 2023)
MA_YEARS = range(1994, 2023)
ELSTATS_DATES = {
    ("ID", date(2000, 5, 24)): date(2000, 5, 23),
    ("NH", date(2022, 9, 11)): date(2022, 9, 13),
    ("NY", date(2014, 9, 14)): date(2014, 9, 9),
}
MA_DAYS = {
    1994: date(1994, 9, 20),
    1996: date(1996, 9, 17),
    1998: date(1998, 9, 15),
    2000: date(2000, 9, 19),
    2002: date(2002, 9, 17),
    2004: date(2004, 9, 14),
    2006: date(2006, 9, 19),
    2008: date(2008, 9, 16),
    2010: date(2010, 9, 14),
    2012: date(2012, 9, 6),
    2014: date(2014, 9, 9),
    2016: date(2016, 9, 8),
    2018: date(2018, 9, 4),
    2020: date(2020, 9, 1),
    2022: date(2022, 9, 6),
}
MA_FEDERAL = {5: "congress", 6: "us-senate", 1: "president"}
MA_PRESIDENT = {
    2000: date(2000, 3, 7),
    2004: date(2004, 3, 2),
    2008: date(2008, 2, 5),
    2012: date(2012, 3, 6),
    2016: date(2016, 3, 1),
    2020: date(2020, 3, 3),
}
MA_DOWNLOAD = elstats.MASSACHUSETTS + "download/{}/precincts_include:0/"
MA_EMPTY = re.compile(r'id="election-id-(\d+)"(?:(?!id="election-id-).)*?class="no_candidates"', re.DOTALL)
ELSTATS_PARTIES = {"Democratic": "DEM", "Republican": "REP", "Libertarian": "LIB", "Green": "GRE"}
ELSTATS_LOCAL = [
    "Conservative",
    "Working Families",
    "Independence",
    "Women's Equality",
    "Reform",
    "Progressive",
    "Liberty Union",
    "Green-Rainbow",
]
WA_OFFICES = {
    "GOVERNOR": "governor",
    "LT. GOVERNOR": "lieutenant-governor",
    "SECRETARY OF STATE": "secretary-of-state",
    "STATE TREASURER": "treasurer",
    "STATE AUDITOR": "auditor",
    "ATTORNEY GENERAL": "attorney-general",
    "COMMISSIONER OF PUBLIC LANDS": "commissioner-of-public-lands",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "superintendent-of-public-instruction",
    "INSURANCE COMMISSIONER": "insurance-commissioner",
}
WA_LEGISLATIVE = re.compile(
    r"LEGISLATIVE DISTRICT 0*(\d+) (?:STATE )?(SENATOR|REPRESENTATIVE)(?:,? (?:POS\.?|POSITION) ?(\d))?"
)
WA_STATEWIDE = re.compile(rf"(?:WASHINGTON STATE )?({'|'.join(re.escape(office) for office in WA_OFFICES)})")
PREFERENCES = {
    "democratic": "DEM",
    "democrat": "DEM",
    "republican": "REP",
    "gop": "REP",
    "libertarian": "LIB",
    "green": "GRE",
    "constitution": "CST",
}
STATES = {
    "NC": (
        "37",
        "North Carolina",
        PUBLISHER,
        TITLE,
        "https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
    ),
    "WA": ("53", "Washington", wa_local.PUBLISHER, wa_local.TITLE, "https://results.vote.wa.gov"),
    "CO": ("08", "Colorado", elstats.TENANTS["CO"][1], elstats.TITLE, elstats.TENANTS["CO"][2]),
    "CT": ("09", "Connecticut", elstats.TENANTS["CT"][1], elstats.TITLE, elstats.TENANTS["CT"][2]),
    "ID": ("16", "Idaho", elstats.TENANTS["ID"][1], elstats.TITLE, elstats.TENANTS["ID"][2]),
    "NH": ("33", "New Hampshire", elstats.TENANTS["NH"][1], elstats.TITLE, elstats.TENANTS["NH"][2]),
    "NY": ("36", "New York", elstats.TENANTS["NY"][1], elstats.TITLE, elstats.TENANTS["NY"][2]),
    "VT": ("50", "Vermont", elstats.TENANTS["VT"][1], elstats.TITLE, elstats.TENANTS["VT"][2]),
    "VA": ("51", "Virginia", elstats.TENANTS["VA"][1], elstats.TITLE, elstats.TENANTS["VA"][2]),
    "NM": ("35", "New Mexico", elstats.TENANTS["NM"][1], elstats.TITLE, elstats.TENANTS["NM"][2]),
    "MA": ("25", "Massachusetts", elstats.MA_PUBLISHER, elstats.TITLE, "https://electionstats.state.ma.us"),
    "ME": (
        "23",
        "Maine",
        me_legislature.PUBLISHER,
        "Primary Election Tabulations",
        "https://www.maine.gov/sos/cec/elec/results/",
    ),
    "CA": (
        "06",
        "California",
        "California Secretary of State",
        "Statement of Vote",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    "IL": (
        "17",
        "Illinois",
        "Illinois State Board of Elections",
        "Election vote totals",
        "https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
    ),
    "FL": (
        "12",
        "Florida",
        fl_sos.PUBLISHER,
        "Election results extracts and detail reports: primaries by county, 2000–2022",
        "https://results.elections.myflorida.com/",
    ),
    "AL": (
        "01",
        "Alabama",
        "Alabama Secretary of State",
        "Election data: 2022 primary and primary runoff precinct results",
        "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
    ),
    "IA": (
        "19",
        "Iowa",
        "Iowa Secretary of State",
        "Election results: primary precinct vote totals by county, 2010–2016",
        "https://sos.iowa.gov/archived-election-results-and-statistics",
    ),
    "IN": (
        "18",
        "Indiana",
        "Indiana Election Division",
        "Election night reporting results archive",
        "https://enr.indianavoters.in.gov/",
    ),
    "PA": (
        "42",
        "Pennsylvania",
        "Pennsylvania Department of State",
        "Election returns: primaries by county, 2000–2022",
        "https://www.electionreturns.pa.gov/",
    ),
    "SC": (
        "45",
        "South Carolina",
        "South Carolina Election Commission",
        "Election night reporting: primary detail results, 2010–2022",
        "https://www.enr-scvotes.org/",
    ),
    "KY": (
        "21",
        "Kentucky",
        "Kentucky State Board of Elections",
        "Election results: primary county reports, 2000–2008",
        "https://elect.ky.gov/",
    ),
    "TX": (
        "48",
        "Texas",
        "Texas Secretary of State",
        "Texas election history: legislative primaries by county, 2000–2014",
        "https://elections.sos.state.tx.us/",
    ),
    "UT": (
        "49",
        "Utah",
        ut_canvass.PUBLISHER,
        ut_canvass.TITLE,
        "https://vote.utah.gov/historical-election-results/",
    ),
    "MN": (
        "27",
        "Minnesota",
        "Minnesota Secretary of State",
        "Election results media files",
        "https://electionresults.sos.mn.gov",
    ),
}
OFFICES = {
    "GOVERNOR": ("governor", "gubernatorial"),
    "GOVERNOR & LT GOVERNOR": ("governor", "gubernatorial"),
    "LIEUTENANT GOVERNOR": ("lieutenant-governor", "Lieutenant Governor"),
    "ATTORNEY GENERAL": ("attorney-general", "Attorney General"),
    "SECRETARY OF STATE": ("secretary-of-state", "Secretary of State"),
    "AUDITOR": ("auditor", "State Auditor"),
    "AUDITOR GENERAL": ("auditor-general", "Auditor General"),
    "TREASURER": ("treasurer", "State Treasurer"),
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": (
        "superintendent-of-public-instruction",
        "Superintendent of Public Instruction",
    ),
    "COMMISSIONER OF AGRICULTURE": ("commissioner-of-agriculture", "Commissioner of Agriculture"),
    "AUDITOR OF STATE": ("auditor", "State Auditor"),
    "TREASURER OF STATE": ("treasurer", "State Treasurer"),
    "SECRETARY OF AGRICULTURE": ("secretary-of-agriculture", "Secretary of Agriculture"),
    "COMMISSIONER OF AGRICULTURE AND INDUSTRIES": (
        "commissioner-of-agriculture-and-industries",
        "Commissioner of Agriculture and Industries",
    ),
    "COMMISSIONER OF EDUCATION": ("commissioner-of-education", "Commissioner of Education"),
    "CHIEF FINANCIAL OFFICER": ("chief-financial-officer", "Chief Financial Officer"),
    "COMMISSIONER OF INSURANCE": ("commissioner-of-insurance", "Commissioner of Insurance"),
    "COMMISSIONER OF LABOR": ("commissioner-of-labor", "Commissioner of Labor"),
    "COMMISSIONER OF PUBLIC LANDS": ("commissioner-of-public-lands", "Commissioner of Public Lands"),
    "INSURANCE COMMISSIONER": ("insurance-commissioner", "Insurance Commissioner"),
    "CONTROLLER": ("controller", "Controller"),
    "COMPTROLLER": ("comptroller", "Comptroller"),
    "COMPTROLLER GENERAL": ("comptroller-general", "Comptroller General"),
    "SUPERINTENDENT OF EDUCATION": ("superintendent-of-education", "Superintendent of Education"),
    "SUPREME COURT ASSOCIATE JUSTICE": ("justice-of-the-supreme-court", "Supreme Court Associate Justice"),
    "SUPREME COURT CHIEF JUSTICE": ("chief-justice-of-the-supreme-court", "Supreme Court Chief Justice"),
    "COURT OF APPEALS JUDGE": ("judge-of-the-court-of-appeals", "Court of Appeals Judge"),
    "APPELLATE COURT JUDGE": ("judge-of-the-appellate-court", "Appellate Court Judge"),
    "GOVERNOR AND LIEUTENANT GOVERNOR": ("governor", "gubernatorial"),
}
LEGISLATIVE = re.compile(r"NC (?:STATE )?(HOUSE|SENATE)(?: OF REPRESENTATIVES)?(?: DISTRICT 0*(\d+)| \(0*(\d+)\))")
MN_LEGISLATIVE = re.compile(r"STATE (REPRESENTATIVE|SENATOR) DISTRICT 0*(\d+)([AB]?)")
STATEWIDE = re.compile(rf"(?:NC )?(?:STATE )?({'|'.join(OFFICES)})")
JUDICIAL = re.compile(
    r"(?:NC )?(SUPREME COURT(?: ASSOCIATE JUSTICE| CHIEF JUSTICE)?|COURT OF APPEALS JUDGE)"
    r"(?: \((\w+)\)| - (\w+) SEAT| SEAT 0*(\d+))?"
)
SUFFIX = re.compile(r"\s*(?:- |\()(DEM|REP|LIB|GRE|CST)\)?$")
PSEUDO = re.compile(r"ABSENTEE.*|PROV.*|TRANSFERS?|CURBSIDE|ONE ?STOP.*|ONESTOP.*|OS|OS[ _-].*")
PARTIES = {
    "DEM": ("democratic-party", "democratic", "dp", "Democratic"),
    "REP": ("republican-party", "republican", "rp", "Republican"),
    "LIB": ("libertarian-party", "libertarian", "liberp", "Libertarian"),
    "LBT": ("libertarian-party", "libertarian", "liberp", "Libertarian"),
    "GRE": ("green-party", "green", "greenp", "Green"),
    "GIP": ("green-party", "green-independent", "greenp", "Green Independent"),
    "CST": ("constitution-party", "constitution", "constp", "Constitution"),
    "DFL": ("democratic-party", "democratic-farmer-labor", "dp", "Democratic-Farmer-Labor"),
    "R": ("republican-party", "republican", "rp", "Republican"),
    "IP": ("MN:independence-party", "independence", "indepp", "Independence"),
    "LMN": ("MN:legalize-marijuana-now-party", "legal-marijuana-now", "lmnp", "Legal Marijuana Now"),
    "": (None, None, "ttp", "top-two"),
    "GRN": ("green-party", "green", "greenp", "Green"),
    "AIP": ("CA:american-independent-party", "american-independent", "amerindepp", "American Independent"),
    "PAF": ("CA:peace-and-freedom-party", "peace-and-freedom", "peacefreedomp", "Peace and Freedom"),
    "NY:Conservative": ("NY:conservative-party", "conservative", "conservativep", "Conservative"),
    "NY:Working Families": ("NY:working-families-party", "working-families", "workp", "Working Families"),
    "CT:Working Families": ("CT:working-families-party", "working-families", "workp", "Working Families"),
    "VT:Working Families": ("VT:working-families-party", "working-families", "workp", "Working Families"),
    "NY:Independence": ("NY:independence-party", "independence", "indepp", "Independence"),
    "NY:Women's Equality": ("NY:women-s-equality-party", "womens-equality", "womensequalityp", "Women's Equality"),
    "NY:Reform": ("NY:reform-party", "reform", "reformp", "Reform"),
    "VT:Progressive": ("VT:progressive-party", "progressive", "progp", "Progressive"),
    "VT:Liberty Union": ("VT:liberty-union-party", "liberty-union", "libertyunionp", "Liberty Union"),
    "MA:Green-Rainbow": ("MA:green-rainbow-party", "green-rainbow", "greenp", "Green-Rainbow"),
    "GLC": (
        "MN:grassroots-legalize-cannabis-party",
        "grassroots-legalize-cannabis",
        "glcp",
        "Grassroots–Legalize Cannabis",
    ),
}
UNALLOCATED = 32
CA_METHOD = "Totals are the county totals of the Secretary of State's Statement of Vote. {rule}"
WA_METHOD = (
    "Totals are the precinct rows of the Secretary of State's statewide precinct export where they add up to its "
    "county results, and the county results elsewhere. The top two candidates advance to the general election."
)
ELSTATS_METHOD = (
    "Totals are the town, county, or locality rows of the {publisher}'s election statistics archive, summed by "
    "county within the district; the archive marks each division's leader, not the contest's, so the winners shown are the candidates with the most votes, as many as there are seats."
)
MN_METHOD = (
    "Totals are the sums of the precinct rows in the Secretary of State's all-races file. The file does not mark "
    "winners, so the winner shown is the candidate with the most votes."
)
JUDICIAL_METHOD = (
    "Totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file; votes the file reports only county-wide are kept at the county level. The primary is "
    "nonpartisan and the file does not mark winners, so the two shown as advancing are the top two, who go on to "
    "the general election."
)
METHOD = (
    "Totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file; votes the file reports only county-wide are kept at the county level. The file does not mark "
    "winners, so the winner shown is the leading candidate when that candidate passed the {threshold} percent a "
    "primary needs, or when no runoff followed; when a runoff followed, the top two are shown as advancing."
)

ME_DAYS = {
    2000: date(2000, 6, 13),
    2002: date(2002, 6, 11),
    2004: date(2004, 6, 8),
    2006: date(2006, 6, 13),
    2008: date(2008, 6, 10),
    2010: date(2010, 6, 8),
    2012: date(2012, 6, 12),
    2016: date(2016, 6, 14),
    2018: date(2018, 6, 12),
    2020: date(2020, 7, 14),
    2022: date(2022, 6, 14),
    2024: date(2024, 6, 11),
}
ME_WAYBACK = "https://web.archive.org/web/{}id_/https://www.maine.gov/sos/cec/elec/{}"
ME_FILES = [
    ("20121018003921", "2000g/2000p-dem-sr.txt", "house", "DEM"),
    ("20121018002956", "2000g/2000p-gre-sr.txt", "house", "GIP"),
    ("20121018003803", "2000g/2000p-rep-sr.txt", "house", "REP"),
    ("20121018003129", "2000g/2000p-dem-ss.txt", "senate", "DEM"),
    ("20121018004143", "2000g/2000p-gre-ss.txt", "senate", "GIP"),
    ("20121018003620", "2000g/2000p-rep-ss.txt", "senate", "REP"),
    ("20121017225740", "2002p/pri02srd.txt", "house", "DEM"),
    ("20121017230617", "2002p/pri02srg.txt", "house", "GIP"),
    ("20121017225629", "2002p/pri02srr.txt", "house", "REP"),
    ("20121017230334", "2002p/pri02ssd.txt", "senate", "DEM"),
    ("20121017224021", "2002p/pri02ssg.txt", "senate", "GIP"),
    ("20121017230241", "2002p/pri02ssr.txt", "senate", "REP"),
    ("20141017110346", "2004/pri04srd.txt", "house", "DEM"),
    ("20141006205259", "2004/pri04srg.txt", "house", "GIP"),
    ("20141015143259", "2004/pri04srr.txt", "house", "REP"),
    ("20141006211453", "2004/pri04ssd.txt", "senate", "DEM"),
    ("20141006211457", "2004/pri04ssg.txt", "senate", "GIP"),
    ("20141006213056", "2004/pri04ssr.txt", "senate", "REP"),
    ("20141021002949", "2006p/pri06srd.txt", "house", "DEM"),
    ("20141021010515", "2006p/pri06srg.txt", "house", "GIP"),
    ("20141021002135", "2006p/pri06srr.txt", "house", "REP"),
    ("20141021001825", "2006p/pri06ssd.txt", "senate", "DEM"),
    ("20141021010520", "2006p/pri06ssg.txt", "senate", "GIP"),
    ("20141021005409", "2006p/pri06ssr.txt", "senate", "REP"),
    ("20121104160750", "2008/tabs-can-sr-dem-6-08.txt", "house", "DEM"),
    ("20121104160759", "2008/tabs-can-sr-gre-6-08.txt", "house", "GIP"),
    ("20121104160810", "2008/tabs-can-sr-rep-6-08.txt", "house", "REP"),
    ("20121104160830", "2008/tabs-can-ss-dem-6-08.txt", "senate", "DEM"),
    ("20121104160840", "2008/tabs-can-ss-gre-6-08.txt", "senate", "GIP"),
    ("20121104160851", "2008/tabs-can-ss-rep-6-08.txt", "senate", "REP"),
    ("20150405014036", "2010/tabssrdem0610.html", "house", "DEM"),
    ("20150405085926", "2010/tabssrgrn0610.html", "house", "GIP"),
    ("20150405012144", "2010/tabssrrep0610.html", "house", "REP"),
    ("20150405023225", "2010/tabsssdem0610.html", "senate", "DEM"),
    ("20150405084705", "2010/tabsssgrn.html", "senate", "GIP"),
    ("20150405020342", "2010/tabsssrep0610.html", "senate", "REP"),
    ("20140310133220", "2012/d-repleg.txt", "house", "DEM"),
    ("20140310123911", "2012/g-repleg.txt", "house", "GIP"),
    ("20140310131157", "2012/r-repleg.txt", "house", "REP"),
    ("20140310131146", "2012/d-statesen.txt", "senate", "DEM"),
    ("20140310131616", "2012/g-statesen.txt", "senate", "GIP"),
    ("20140310124133", "2012/r-statesen.txt", "senate", "REP"),
    ("20250321194028", "results/2016/State%20Rep%20DEM.xlsx", "house", "DEM"),
    ("20250320045127", "results/2016/State%20Rep%20GRE.xlsx", "house", "GIP"),
    ("20250319192354", "results/2016/State%20Rep%20REP.xlsx", "house", "REP"),
    ("20250324173048", "results/2016/State%20Senate%20DEM.xlsx", "senate", "DEM"),
    ("20250324135524", "results/2016/State%20Senate%20GRE.xlsx", "senate", "GIP"),
    ("20250320232455", "results/2016/State%20Senate%20REP.xlsx", "senate", "REP"),
    ("20250404031332", "results/2018/staterepd.xlsx", "house", "DEM"),
    ("20250405041042", "results/2018/staterepgi.xlsx", "house", "GIP"),
    ("20250404053939", "results/2018/staterepl.xlsx", "house", "LIB"),
    ("20250404024739", "results/2018/staterepr.xlsx", "house", "REP"),
    ("20250404050530", "results/2018/statesend.xlsx", "senate", "DEM"),
    ("20250404023524", "results/2018/statesenr.xlsx", "senate", "REP"),
    ("20250324023325", "results/2020/srdemnr.xlsx", "house", "DEM"),
    ("20250325035436", "results/2020/srgreennr.xlsx", "house", "GIP"),
    ("20250328232007", "results/2020/srrepnr.xlsx", "house", "REP"),
    ("20250324023315", "results/2020/ssdemnr.xlsx", "senate", "DEM"),
    ("20250328232003", "results/2020/ssrepnr.xlsx", "senate", "REP"),
    ("20250324070338", "results/2022/dem/replegd622.xlsx", "house", "DEM"),
    ("20250318204713", "results/2022/green/greenreptoleg622.xlsx", "house", "GIP"),
    ("20250321080350", "results/2022/rep/replegr622.xlsx", "house", "REP"),
    ("20250321080350", "results/2022/dem/ssd622.xlsx", "senate", "DEM"),
    ("20250324070337", "results/2022/rep/ssr622.xlsx", "senate", "REP"),
    ("20241103073940", "results/2024/5%20Rep%20to%20the%20Legislature%20Dem-%20FINAL.xlsx", "house", "DEM"),
    ("20241103073943", "results/2024/5%20Rep%20to%20the%20Legislature%20Rep%20-%20FINAL.xlsx", "house", "REP"),
    ("20241103073927", "results/2024/4%20State%20Senate%20Dem%20-%20FINAL.xlsx", "senate", "DEM"),
    ("20241103073934", "results/2024/4%20State%20Senate%20Rep%20-%20FINAL.xlsx", "senate", "REP"),
]
ME_NAME = (
    pl.when(pl.col("name").str.to_uppercase() == pl.col("name"))
    .then(pl.col("name").str.to_titlecase())
    .otherwise(pl.col("name"))
    .str.strip_chars()
    .str.replace(r"(?i)^([^,]+?)\s*,\s*(.+?)(?:,?\s+(jr\.?|sr\.?|ii|iii|iv))?$", "${2} ${1} ${3}")
    .str.replace_all(r"\s+", " ")
    .str.strip_chars(" ,")
)
ME_METHOD = (
    "Totals are the town rows of the Secretary of State's primary tabulations, summed by county within the district; "
    "overseas and military ballots the state counts centrally stay on the district. The files do not mark winners, so "
    "the winner shown is the candidate with the most votes, or, from 2018, where ranked-choice voting applies to three "
    "or more candidates, the candidate with a majority of first choices. Votes are first choices."
)
WON_MAJORITY = "Derived: a majority of first choices in the certified contest totals."
UT_FILES = {
    date(2000, 6, 27): ("2023/09/2000Pri.xls", ["2000 Primary Election"]),
    date(2010, 6, 22): ("2023/09/2010-Primary-Canvass-Reports.xls", ["Primary Multi County Races"]),
    date(2012, 6, 26): (
        "2023/09/2012-Primary-Canvass-Reports-1.xlsx",
        ["Primary Multi County Races", "Utah Leg Single"],
    ),
    date(2016, 6, 28): (
        "2023/09/2016-Primary-Election-State-Canvass-2-1.xlsx",
        ["Utah Leg. - Multi County", "Utah Leg. - Single County"],
    ),
    date(2018, 6, 26): (
        "2023/09/2018-Primary-Election-State-Canvass-1.xlsx",
        ["Utah Leg. - Multi County", "Utah Leg. - Single County"],
    ),
    date(2020, 6, 30): (
        "2021/02/2020-Primary-Election-State-Canvass.xlsx",
        ["Legislature - Multi-County", "Legislature - Single County"],
    ),
}
UT_LEGISLATIVE = re.compile(r"(?<!U\.S\. )(Senate|House)(?: of Representatives)?(?: District)?\s*#?\s*(\d+)")
UT_EXPORT = re.compile(r"(Republican|Democratic) For State (Senate|House) (\d+)\s*")
UT_PARTIES = {"Republican": "REP", "Democratic": "DEM", "Libertarian": "LIB", "Constitution": "CST"}
KY_DAYS = {
    2000: date(2000, 5, 23),
    2002: date(2002, 5, 28),
    2004: date(2004, 5, 18),
    2006: date(2006, 5, 16),
    2007: date(2007, 5, 22),
    2008: date(2008, 5, 20),
}
KY_PAGE = "https://elect.ky.gov/results/2000-2009/Pages/{}primaryandgeneralelectionresults.aspx"
KY_OFFICES = {
    "D07": "SECRETARY OF STATE",
    "D08": "ATTORNEY GENERAL",
    "D09": "AUDITOR",
    "D10": "TREASURER",
    "D12": "COMMISSIONER OF AGRICULTURE",
    "D14": "senate",
    "D15": "house",
}
KY_LINE = re.compile(r"^ {3,}([DR]?) {2,}(\S.*?) {2,}([\d,]*) +[\d.]+% +\d+ *$", re.MULTILINE)
KY_METHOD = (
    "Totals are the State Board of Elections' official election night tally from its county reports, each "
    "candidate's county rows checked against the statewide report by office; a county the district splits holds "
    "only the district's part. The reports do not mark winners, so the winner shown is the leading candidate."
)
FL_DAYS = {
    date(2000, 9, 5): None,
    date(2000, 10, 3): date(2000, 9, 5),
    date(2002, 9, 10): None,
    date(2004, 8, 31): None,
    date(2006, 9, 5): None,
    date(2008, 8, 26): None,
    date(2010, 8, 24): None,
    date(2012, 8, 14): None,
    date(2014, 8, 26): None,
    date(2016, 8, 30): None,
    date(2018, 8, 28): None,
    date(2020, 8, 18): None,
    date(2022, 8, 23): None,
}
FL_RACES = {
    "USR": "congress",
    "USS": "us-senate",
    "STR": "house",
    "STS": "senate",
    "GOV": "GOVERNOR",
    "ATG": "ATTORNEY GENERAL",
    "AGR": "COMMISSIONER OF AGRICULTURE",
    "CFO": "CHIEF FINANCIAL OFFICER",
    "EDU": "COMMISSIONER OF EDUCATION",
    "CMP": "COMPTROLLER",
    "TRE": "TREASURER",
    "SEC": "SECRETARY OF STATE",
}
FL_DETAIL = (
    "https://results.elections.myflorida.com/DetailRpt.Asp?ELECTIONDATE={day:%-m/%-d/%Y}&RACE={race}&PARTY={party}"
    "&DIST={district}&GRP=&DATAMODE="
)
FL_METHOD = (
    "Totals are the county rows of the Division of Elections' official results extract, each race checked county by "
    "county against its detail report and that report's total; a county the district splits holds only the "
    "district's part. The extract does not mark winners, so the winner shown is the leading candidate{rule}."
)
TX_DAYS = {
    date(2000, 3, 14): None,
    date(2000, 4, 11): date(2000, 3, 14),
    date(2002, 3, 12): None,
    date(2002, 4, 9): date(2002, 3, 12),
    date(2004, 3, 9): None,
    date(2004, 4, 13): date(2004, 3, 9),
    date(2006, 3, 7): None,
    date(2006, 4, 11): date(2006, 3, 7),
    date(2008, 3, 4): None,
    date(2008, 4, 8): date(2008, 3, 4),
    date(2010, 3, 2): None,
    date(2010, 4, 13): date(2010, 3, 2),
    date(2012, 5, 29): None,
    date(2012, 7, 31): date(2012, 5, 29),
    date(2014, 3, 4): None,
    date(2014, 5, 27): date(2014, 3, 4),
}
TX_RACE = re.compile(r"State Representative District (\d+)|State Senator, District (\d+)")
TX_METHOD = (
    "Totals are the county rows of the Secretary of State's election history page for the race, each candidate's "
    "rows checked against its All Counties total; a county the district splits holds only the district's part. The "
    "page does not mark winners, so the winner shown is the candidate with a majority, or the runoff's leader, and "
    "when a runoff followed, the top two are shown as advancing."
)
AL_DAYS = {date(2022, 5, 24): None, date(2022, 6, 21): date(2022, 5, 24)}
AL_FILES = {
    date(2022, 5, 24): "https://www.sos.alabama.gov/sites/default/files/election-data/2022-06/"
    "2022%20Primary%20Precinct%20Results.zip",
    date(2022, 6, 21): "https://www.sos.alabama.gov/sites/default/files/election-data/2022-07/"
    "2022%20Primary%20Runoff%20Precinct%20Results.zip",
}
AL_RACE = re.compile(
    r"STATE (REPRESENTATIVE|SENATOR), DISTRICT (\d+)|(GOVERNOR|LIEUTENANT GOVERNOR|ATTORNEY GENERAL|SECRETARY OF STATE"
    r"|STATE TREASURER|STATE AUDITOR|COMMISSIONER OF AGRICULTURE AND INDUSTRIES)"
)
AL_METHOD = (
    "Totals are the sums of the precinct, absentee, and provisional columns of the Secretary of State's county "
    "precinct results files; the files print no contest totals to check them against, and a county the district "
    "splits holds only the district's part. The files do not mark winners, so the winner shown is the candidate "
    "with a majority, or the runoff's leader, and when a runoff followed, the top two are shown as advancing."
)
IA_DAYS = {2010: date(2010, 6, 8), 2012: date(2012, 6, 5), 2014: date(2014, 6, 3), 2016: date(2016, 6, 7)}
IA_CDX = (
    "https://web.archive.org/cdx/search/cdx?url=sos.iowa.gov/elections/results/xls/{}/primary/&matchType=prefix"
    "&fl=timestamp,original,statuscode,mimetype&limit=100000"
)
IA_RACE = re.compile(
    r"(?:STATE (REPRESENTATIVE|REP\.|SENATOR) (?:DISTRICT|DIST\.) (\d+)|(GOVERNOR|SECRETARY OF STATE|AUDITOR OF STATE"
    r"|TREASURER OF STATE|SECRETARY OF AGRICULTURE|ATTORNEY GENERAL)) - (DEM|REP|D|R|DEMOCRATIC PARTY|REPUBLICAN PARTY)"
)
IA_FEDERAL = re.compile(
    r"(?:U\.S\.|UNITED STATES) (?:(SENATOR)|(?:REPRESENTATIVE|HOUSE OF REPRESENTATIVES|REP\.) (?:DISTRICT|DIST\.) (\d+))"
    r" - (DEM|REP|D|R|DEMOCRATIC PARTY|REPUBLICAN PARTY)"
)
IA_METHOD = (
    "Totals are the county totals of the Secretary of State's county precinct results files, read from the Wayback "
    "Machine's captures, each county's precinct rows checked against its printed total, except in nine county files "
    "whose rows for some races skip the precincts outside the district and no longer line up with the header, where "
    "the printed county total is taken as is; a county the district splits holds only the district's part. The files do not mark winners, so the winner shown is derived: the candidate "
    "with the most votes, when that candidate has at least the 35 percent of the votes Iowa requires. Below that, a "
    "party convention chooses the nominee, and no winner is shown."
)
WON_CONVENTION = (
    "Derived: the leader of the certified contest totals, with at least the 35 percent that avoids a nominating "
    "convention."
)
IN_DAYS = {2016: date(2016, 5, 3), 2018: date(2018, 5, 8), 2020: date(2020, 6, 2), 2022: date(2022, 5, 3)}
IN_ARCHIVE = "https://enr.indianavoters.in.gov/archive/{}Primary/data/OffCatC_{}_{}.json"
IN_VERSIONS = {2016: "A", 2018: "A", 2020: "B", 2022: "B"}
IN_CATEGORIES = {
    2016: {1202: "senate", 1201: "house"},
    2018: {1938: "senate", 1937: "house"},
    2020: {1009: "GOVERNOR", 1018: "senate", 1039: "house"},
    2022: {1009: "GOVERNOR", 1018: "senate", 1039: "house"},
}
IN_FILES = [
    (year, category, label) for year, categories in IN_CATEGORIES.items() for category, label in categories.items()
]
IN_METHOD = (
    "Totals are the county rows of the Election Division's election night results archive, each candidate's rows "
    "checked against the archive's statewide summary; a county the district splits holds only the district's part. "
    "The winner shown is derived, not the archive's flag: the candidate with the most votes."
)
PA_ELECTIONS = {
    date(2000, 4, 4): 9,
    date(2002, 5, 21): 11,
    date(2004, 4, 27): 13,
    date(2006, 5, 16): 15,
    date(2008, 4, 22): 17,
    date(2010, 5, 18): 19,
    date(2012, 4, 24): 27,
    date(2014, 5, 20): 41,
    date(2016, 4, 26): 54,
    date(2018, 5, 15): 63,
    date(2020, 6, 2): 83,
    date(2022, 5, 17): 94,
}
PA_OFFICES = {
    1: "president",
    2: "us-senate",
    11: "congress",
    3: "GOVERNOR",
    4: "LIEUTENANT GOVERNOR",
    5: "ATTORNEY GENERAL",
    6: "AUDITOR GENERAL",
    7: "TREASURER",
    12: "senate",
    13: "house",
}
PA_API = "https://www.electionreturns.pa.gov/api/ElectionReturn/"
PA_OFFICE = PA_API + "GetOfficeData?officeId={}&methodName=GetOfficeData&electionid={}&electiontype=P&isactive=0"
PA_COUNTY = (
    PA_API
    + "GetCountyBreak?officeId={}&districtId={}&methodName=GetCountyBreak&electionid={}&electiontype=P&isactive=0"
)
PA_METHOD = (
    "Totals are the county rows of the Department of State's election returns for the district, each candidate's "
    "rows checked against the returns' district total; a county the district splits holds only the district's part. "
    "The returns do not mark winners, so the winner shown is the candidate with the most votes."
)
SC_DAYS = {
    date(2010, 6, 8): None,
    date(2010, 6, 22): date(2010, 6, 8),
    date(2012, 6, 12): None,
    date(2012, 6, 26): date(2012, 6, 12),
    date(2014, 6, 10): None,
    date(2014, 6, 24): date(2014, 6, 10),
    date(2016, 6, 14): None,
    date(2016, 6, 28): date(2016, 6, 14),
    date(2018, 6, 12): None,
    date(2018, 6, 26): date(2018, 6, 12),
    date(2020, 6, 9): None,
    date(2020, 6, 23): date(2020, 6, 9),
    date(2022, 6, 14): None,
    date(2022, 6, 28): date(2022, 6, 14),
}
SC_FILES = {
    date(2010, 6, 8): ("16117",),
    date(2010, 6, 22): ("16213",),
    date(2012, 6, 12): ("39142", "40325"),
    date(2012, 6, 26): ("40277", "40548"),
    date(2014, 6, 10): ("51763", "52123"),
    date(2014, 6, 24): ("52366",),
    date(2016, 6, 14): ("62359", "62748"),
    date(2016, 6, 28): ("62799",),
    date(2018, 6, 12): ("75708", "76290"),
    date(2018, 6, 26): ("76440",),
    date(2020, 6, 9): ("103402", "104192"),
    date(2020, 6, 23): ("104240", "104404"),
    date(2022, 6, 14): ("114143", "115138"),
    date(2022, 6, 28): ("115187", "115243"),
}
SC_SITE = "https://www.enr-scvotes.org/SC/{}/"
SC_RACE = re.compile(
    r"State (House of Representatives|Senate),? District (\d+) ?- ?(DEM|REP)"
    r"|(Governor|Lieutenant Governor|Secretary of State|(?:State )?Treasurer|Attorney General|Comptroller General"
    r"|(?:State )?Superintendent of Education|Commissioner of Agriculture) ?- ?(DEM|REP)"
)
SC_METHOD = (
    "Totals are the county rows of the State Election Commission's detail results file, each candidate's rows "
    "checked against its total, with the recount's file in place of the first count where the Commission "
    "recounted the contest; a county the district splits holds only the district's part. The file does not mark "
    "winners, so the winner shown is the candidate with a majority, or the runoff's leader, and when a runoff "
    "followed, the top two are shown as advancing."
)
IL_DAYS = {
    2000: date(2000, 3, 21),
    2002: date(2002, 3, 19),
    2004: date(2004, 3, 16),
    2006: date(2006, 3, 21),
    2008: date(2008, 2, 5),
    2010: date(2010, 2, 2),
    2012: date(2012, 3, 20),
    2014: date(2014, 3, 18),
    2016: date(2016, 3, 15),
    2018: date(2018, 3, 20),
    2020: date(2020, 3, 17),
    2022: date(2022, 6, 28),
}
IL_DOWNLOAD = "https://elections.il.gov/NewDocDisplay.aspx?{}"
IL_FILES = {
    2000: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZZw3h4ar3NE4CX%2bu5lLO94twHzufQ58vYlkMCNtiT%2bQdcHGqEYcC76CbrjgtiNCE9OdyRxnyYqKgQXnIZXgm9P0%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZZw3h4ar3NE4CX%2bu5lLO94s5VwxtnxnL3VC0goT8Xc4%2bcpzhL9soUlXkd%2btIUPkyyv74AaIjCozFfujtZIZcqa993sfx8bQGXA%3d%3d",
    ),
    2002: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeeBDZh5aFV%2f5Fj6mxlHEmXEac%2b7FDWzASHsRW%2fwdRIOZN%2bm5LhvUKX%2bpoElUyimQSE%2bVXPDSEKizg8ayVXVwzE%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeeBDZh5aFV%2f5Fj6mxlHEmUjDI54OVMzUYhwPAtDMfy78bBFapmbnQvhJNFxtOZcnKns%2fUBy2Pfu2hWxgBB%2fRa%2fKv7ESylauVw%3d%3d",
    ),
    2004: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZbBAaynJaYDeehzrVcUaY%2f6dQuJ6YjQnX%2b71Qjck0nD%2bHTkhSqy3seM4FQJRsPS50i8GGymPg3z4HmLxqaWPlew%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZbBAaynJaYDeehzrVcUaY%2f5m4n1hiFA9SAh0DW2mSW4lKoOvvGdWaIh2ni5xUKVyrQNpHt6JHA41kJXR52dHgerH52n86VxSvw%3d%3d",
    ),
    2006: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZYCUKSb2ZB%2fCxykq03t7B1JOgGbXUywd8voRsbUZilaq7s8%2fEtlljJr0loIczjwBrXgglSkhu6EvAL%2fo3i4nuSg%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZYCUKSb2ZB%2fCxykq03t7B1Kk%2bqKdEb0phCJOtRg6etlxRPCHc0xvEv47T0uWjr1tzGOyL3dcQpphwiS3WZdBFy1ND5DtxhXFYg%3d%3d",
    ),
    2008: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZVcE%2fKSRRVYPmEiR0E1M2978KTDoe9Wt%2b1W5aF%2bRXzu69bF8AGYhjosV0r3rabq5iug%2bdSlcSWf5",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZVcE%2fKSRRVYPmEiR0E1M294%2fG9VxI%2byEMFthnmNvGlJJcozgLlLtqW2zJpTGV8i%2bEdEPI4FrYhKBoufL0t0YW6s%3d",
    ),
    2010: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZchKLBtq0PZpEVaLjqq6OleE8DkYy3MmTp6H9k5sJxom1Y1PPZKwyR4dyASY9ZwI%2bDIGSET1QO%2fp",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZchKLBtq0PZpEVaLjqq6OlfG226W57pkA7fg3O957p7AE4vK23HfJEZ87y1N3BzH%2fNoXHu6HqyDwk89aCA5toVo%3d",
    ),
    2012: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Zer3TqjMLZFBpyGuzFVLVTjMzwFBag7lME1L7hxIQQoc5HhRXX%2bWhi%2bgmy8ULWmB6YnC6IwQ9qo7",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Zer3TqjMLZFBpyGuzFVLVTjvgR4qMJ4ABVTDfVQr7Rp1jEqtyHVsrZpEtiUUIcIs8722MEB3Xraswmia%2bRMCYB8%3d",
    ),
    2014: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZasjAerqxs3fRrf2XYrBXtEbKmVLSjufof2IpO8z4VfYshl93ka8t4VUe%2b%2bKCz2u1NwHX2KbNrDv",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZasjAerqxs3fRrf2XYrBXtGGnkcw1G96QzlBYJgOnLM7MW14dtZ3u%2b2v4AWTtX5CQ6CcIX51s8LWibLvk%2bAvXhs%3d",
    ),
    2016: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZcyCkMA7x3sXBKi6Ks4uZustlNRzKk7R7ljbA1VA2W573EZlHuKfVuEiC1xOdiy9jipiK%2fw54q6%2b",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZcyCkMA7x3sXBKi6Ks4uZuu%2fl7C4HQEu06X05BGg9mYwvQ5glmvqMKPEStc0j%2fcewAwSHyZhxz9CRsY%2bPfFUxuQ%3d",
    ),
    2018: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZQftp%2fSyhp5PFMq2OvjCNY5I2g1KvY54trRpa2jTf6%2fHHJtgkIMya6%2feEoiTEYKMdmjC14wEnY8G",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZQftp%2fSyhp5PFMq2OvjCNY6CJOHWnP0uhQfvR5RntaiWX3C%2bBte9YZp2ygdyNM2gyp1NAVhsPJ%2b%2feEUvv1w%2bYaI%3d",
    ),
    2020: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeVciCixC%2bz1ZNJL5NtW4BzqozO%2bIMmNKOu4%2bAw5AhmApODsF8NFLz8KnZXanpC%2fx9w31mSz9l5v",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeVciCixC%2bz1ZNJL5NtW4Bxejud%2fndV9iL%2bJYWPjg8O2CWwcntZg%2fS%2flS6liUFtBq%2bIRyPM63t2HkOOPnvLRDeE%3d",
    ),
    2022: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZSMw8T%2bTP0Bu5MYScFngpt5KKJsa1cQ0Ak2xTeElyH2R6HzvNMlMKxgJQMjsktjYGuSvEqhKQS1%2b",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZSMw8T%2bTP0Bu5MYScFngpt7DT1jIISrO8vbwEz2j1UaUFk4Ught6jyIEJO1%2flfBqWXHLZ4FGOg24NU92Hn1kynhR%2fg1AlG3%2fHg%3d%3d",
    ),
}
IL_METHOD = (
    "Totals are the State Board of Elections' official primary vote by county, each candidate's county rows checked "
    "against the Board's statewide totals file. The files do not mark winners, so the winner shown is the candidate "
    "with the most votes."
)

RECOUNT = (
    "No winner is shown where the leader's margin is within 1 percent of the contest's votes, close enough for a "
    "recount, unless the leader is the party's nominee on the general election ballot."
)
WON_LEAD = "Derived: the plurality leader of the certified contest totals."
WON_ALONE = "Derived: the only named candidate in the certified contest totals."
WON_NOMINEE = (
    "Derived: the leader of the certified contest totals and the party's nominee on the general election ballot."
)
WON_THRESHOLD = (
    "Derived: the leader of the certified contest totals, with more than the {threshold} percent that avoids a second "
    "primary."
)
ADVANCED_TOP = "Derived: one of the top two of the certified contest totals."
ADVANCED_RUNOFF = "Derived: one of the top two of the certified contest totals, and a second primary followed."
WITHDREW = {
    "2010-06-08:SC:house:69:rp": (
        {"Danny Frazier", "Gary W Taylor", "Daniel E Ernie Peagler", "T R Parrish"},
        (
            "Withdrew before the June 22 second primary; the State Election Commission declared Rick Quinn the nominee on "
            'receiving withdrawal letters from his four opponents (Associated Press, "Rick Quinn wins House Dist. 69 GOP '
            "nomination without runoff,\" June 2010). Lexington County's runoff returns list no District 69 race."
        ),
    ),
    "2000-09-05:FL:house:30:rp": (
        {"Peter DiLavore"},
        (
            "Withdrew before the October 3 second primary, leaving Mike Haridopolos, who led with 47.8 percent, the "
            "nominee; the Division of Elections' results extract for the October 3, 2000 second primary lists no House "
            "District 30 race."
        ),
    ),
}

NY_WRITEIN = (
    "Derived: the leading write-in candidate in the State Board of Elections' certified primary results, which name "
    "each write-in; the city Board's report by election district gives write-ins as one line."
)
NY_UNATTRIBUTED = (
    "No winner is shown: the leading write-in line in the State Board of Elections' certified primary results is "
    '"Unattributable Write-In", not a candidate.'
)
VT_CHITTENDEN = (
    "Baruth, Vyhovsky, and Gulick were the top three in the Secretary of State's certified totals; the recount at "
    "Chittenden County Superior Court on August 19, 2022 kept Gulick ahead of Erhard Mahnke, 3,948 to 3,944, and all "
    "three were Democratic nominees in November."
)
VT_WINDHAM = "Coester and Kenyon were the top two in the Secretary of State's certified totals."
DECLARED: dict[str, list[tuple[str, str, str]]] = {
    "2006-08-08:CO:senate:22:rp": [
        (
            "Mike Kopp",
            "won",
            "Kopp led the Secretary of State's certified totals by 80 votes and was the Republican nominee in November.",
        )
    ],
    "2006-09-12:VT:house:orange-2:rp": [
        (
            "Tom Barkley",
            "won",
            "Barkley finished first in the Secretary of State's certified totals, 74 votes ahead of third place.",
        )
    ],
    "2016-03-15:NC:house:92:rp": [
        (
            "Charles Jeter",
            "won",
            (
                "Jeter led the State Board of Elections' certified totals by 35 votes, and Mecklenburg County's recount on "
                "March 24, 2016 confirmed the margin (WFAE, March 24, 2016)."
            ),
        ),
        (
            "Charles Jeter",
            "declined",
            (
                "Withdrew from the November race on July 25, 2016, when he resigned his seat; the Mecklenburg County "
                "Republican Party named Beth Danae Caulfield to the ballot (WFAE, July 25 and August 22, 2016)."
            ),
        ),
    ],
    "2017-06-13:VA:house-of-delegates:2:dp": [
        (
            "Jennifer Denise Carroll Foy",
            "won",
            (
                "Carroll Foy led the Department of Elections' certified totals by 12 votes; the recount in Prince William "
                "and Stafford counties on July 20, 2017 kept her ahead by 14 votes, and she was the Democratic nominee in "
                "November."
            ),
        )
    ],
    "2018-08-14:CT:house:38:dp": [
        (
            "Baird Welch-Collins",
            "won",
            (
                "Welch-Collins led the Secretary of the State's certified totals by 8 votes and was the Democratic nominee "
                "in November."
            ),
        )
    ],
    "2018-08-14:CT:senate:16:dp": [
        (
            "Vickie Orsini Nardello",
            "won",
            (
                "Orsini Nardello led the Secretary of the State's certified totals by 37 votes and was the Democratic "
                "nominee in November."
            ),
        )
    ],
    "2022-08-09:VT:senate:chittendencentral:dp": [
        ("Phil Baruth", "won", VT_CHITTENDEN),
        ("Tanya Vyhovsky", "won", VT_CHITTENDEN),
        ("Martine Larocque Gulick", "won", VT_CHITTENDEN),
    ],
    "2022-08-09:VT:senate:windham:rp": [
        ("Mark Coester", "won", VT_WINDHAM),
        ("Richard Kenyon", "won", VT_WINDHAM),
        (
            "Mark Coester",
            "declined",
            (
                "Ran in November as an independent; the Windham County Republican Committee named Richard Morton to the "
                "second Republican line (VTDigger, August 17, 2022)."
            ),
        ),
    ],
}
UNDECLARED = {
    "2006-09-12:VT:house:orange-2:rp": (
        "Only Tom Barkley is shown as a winner: David Atkinson led Bob Wolffe by one vote for the second seat in the "
        "Secretary of State's certified totals, and no recount or declaration has been found."
    ),
    "2012-08-14:CT:house:5:dp": (
        "No winner is shown. The Secretary of the State's recanvass, whose totals this set holds, gave Leo Canty 774 "
        "votes and Brandon McGee 773, but a court-ordered recount found a 774 to 774 tie, and the court ordered a new "
        "primary on October 2, 2012, which McGee won."
    ),
    "2020-09-01:MA:house:bristol-12:dp": (
        "No winner is shown: David A. French III leads the named write-ins in the Secretary of the Commonwealth's "
        "certified totals, but no Democrat appeared on the November ballot."
    ),
    "2018-09-13:NY:assembly:30:womensequalityp": NY_UNATTRIBUTED,
    "2018-09-13:NY:senate:12:reformp": NY_UNATTRIBUTED,
    "2018-09-13:NY:assembly:43:indepp": (
        "No winner is shown: the leading write-in in the State Board of Elections' certified primary results is Mario "
        "Cuomo, who died in 2015."
    ),
}

WITHHELD = (
    'The archive withholds some of this primary\'s county counts by voting method for voter privacy, shown as "*", '
    "covering at most {ballots} ballots by its own ballot totals, so each candidate's total may be short by up to that "
    "many votes; no winner is shown unless the leader's margin is larger."
)
EMPTY = "The source lists this primary with zero votes for every candidate, so its returns are missing; the set is withdrawn."
NYC_SITE = "https://www.vote.nyc"
NYC_TITLE = "Election Results Summary"
NYC_PAGE = NYC_SITE + "/page/election-results-summary-{}"
NYC_YEARS = (2014, 2016, 2018, 2020, 2022)
NYC_FILE = re.compile(
    r'href="(/sites/default/files/(?:pdf|downloads/csv)/election_results/\d{4}/(\d{8})Primary Election/\d{11}'
    r'(Crossover|New York|Bronx|Kings|Queens|Richmond) ([^"/<>]+?) '
    r"(Representative in Congress|State Senator|Member of the Assembly) "
    r'(\d+)(?:st|nd|rd|th) (?:Congressional|Senatorial|Assembly) District) EDLevel\.csv"'
)
NYC_METHOD = (
    "Election district returns from the Board of Elections in the City of New York's certified ED-level reports; "
    "summed by borough, every candidate's votes match the Board's certified recap. Election districts the Board "
    "combined into another for the count hold no votes and are left out, and write-ins are one line, as the report "
    "gives them."
)
NYC_COUNTERS = [
    "Public Counter",
    "Emergency",
    "Manually Counted Emergency",
    "Absentee/Military",
    "Absentee / Military",
    "Federal",
    "Affidavit",
]
NYC_TOTALS = [
    "Total Ballots",
    "Less - Inapplicable Federal/Special Presidential Ballots",
    "Total Applicable Ballots",
    "Total Votes",
    "Unrecorded",
]
NY_TITLE = "Certified primary election results"
NY_FEDERAL = {
    date(2014, 6, 24): (
        "20140813073848",
        "http://www.elections.ny.gov/NYSBOE/elections/2014/Primary/2014FederalPrimaryResults.pdf",
    ),
    date(2016, 6, 28): (
        "20160822134930",
        "http://www.elections.ny.gov/NYSBOE/elections/2016/Primary/FederalPrimaryResults.pdf",
    ),
    date(2018, 6, 26): (
        "20181008172249",
        "http://www.elections.ny.gov:80/NYSBOE/Elections/2018/FederalPrimary/2018CertifiedFederalPrimaryResult.pdf",
    ),
    date(2020, 6, 23): (
        "20200816214138",
        "https://www.elections.ny.gov/NYSBOE/Elections/2020/Primary/CertifiedJune232020StatePrimaryResults.pdf",
    ),
    date(2022, 8, 23): (
        "20220916224340",
        "https://www.elections.ny.gov/NYSBOE/Elections/2022/Primary/2022AugFedAndStatePrimaryResults.xlsx",
    ),
}
NY_LINES = {
    "DEM": "democratic",
    "REP": "republican",
    "CON": "conservative",
    "WOR": "working-families",
    "WEP": "women-s-equality",
    "IND": "independence",
    "GRE": "green",
    "REF": "reform",
    "LBT": "libertarian",
    "LIB": "libertarian",
}
NYC_OFFICES = {"Representative in Congress": "house", "State Senator": "senate", "Member of the Assembly": "assembly"}


def nyc_files() -> dict[tuple[date, str, str, str], str]:
    found: dict[tuple[date, str, str, str], dict[str, str]] = {}
    for year in NYC_YEARS:
        page = html.unescape(raw.cached(SOURCE, NYC_PAGE.format(year)).path.read_text()).replace("%20", " ")
        for path, day, borough, party, office, district in NYC_FILE.findall(page):
            found.setdefault(
                (date(int(day[:4]), int(day[4:6]), int(day[6:])), party, NYC_OFFICES[office], district), {}
            )[borough] = NYC_SITE + quote(path)
    files = {}
    for contest, boroughs in found.items():
        if "Crossover" not in boroughs and len(boroughs) > 1:
            raise ValueError(f"{contest}: {sorted(boroughs)} files and no crossover file")
        files[contest] = boroughs.get("Crossover") or next(iter(boroughs.values()))
    return files


def search(state: str, office: int, year: int) -> str:
    if state == "MA":
        return f"{elstats.MASSACHUSETTS}search/year_from:{year}/year_to:{year}/office_id:{office}/stage:Primaries"
    query = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"id": office}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {"search": json.dumps(query, separators=(",", ":"))}
    )


def fetch() -> None:
    for stamp, link in NY_FEDERAL.values():
        raw.fetch(SOURCE, f"https://web.archive.org/web/{stamp}id_/{link}", suffix=Path(link).suffix)
    for year in NYC_YEARS:
        raw.fetch(SOURCE, NYC_PAGE.format(year), suffix=".html")
    for stem in nyc_files().values():
        for suffix in ("EDLevel", "Recap"):
            raw.fetch(SOURCE, f"{stem}%20{suffix}.csv", suffix=".csv")
    for stamp, path, *_ in ME_FILES:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, ME_WAYBACK.format(stamp, path), suffix=Path(path).suffix)
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, ME_WAYBACK.format(stamp, path), suffix=Path(path).suffix)
    for county, total in IL_FILES.values():
        for token in (county, total):
            raw.fetch(SOURCE, IL_DOWNLOAD.format(token), suffix=".txt")
    for election in tx_elchist.PRIMARIES:
        for race, title in tx_elchist.races(election):
            if TX_RACE.fullmatch(title):
                url = f"{tx_elchist.SITE}elchist{election}_race{race}.htm"
                try:
                    raw.cached(SOURCE, url)
                except LookupError:
                    raw.fetch(SOURCE, url, suffix=".htm")
                    time.sleep(1)
    for year, category, _ in IN_FILES:
        raw.fetch(SOURCE, IN_ARCHIVE.format(year, category, IN_VERSIONS[year]), suffix=".json")
    for url in AL_FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    for year in IA_DAYS:
        raw.fetch(SOURCE, IA_CDX.format(year), suffix=".txt")
    for url in iowa().values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            for attempt in range(6):
                time.sleep(2 + 30 * attempt)
                try:
                    raw.fetch(SOURCE, url, suffix=Path(url).suffix)
                    break
                except httpx.HTTPError:
                    if attempt == 5:
                        raise
    for election in PA_ELECTIONS.values():
        for office in PA_OFFICES:
            listing = json.loads(raw.fetch(SOURCE, PA_OFFICE.format(office, election), suffix=".json").path.read_text())
            for blocks in json.loads(listing)["Election"].values() if listing else ():
                for block in blocks:
                    for heading in block:
                        url = PA_COUNTY.format(office, heading.split("$$")[1], election)
                        try:
                            raw.cached(SOURCE, url)
                        except LookupError:
                            raw.fetch(SOURCE, url, suffix=".json")
                            time.sleep(0.3)
    for ids in SC_FILES.values():
        for election in ids:
            try:
                version = raw.cached(SOURCE, SC_SITE.format(election) + "current_ver.txt")
            except LookupError:
                version = raw.fetch(SOURCE, SC_SITE.format(election) + "current_ver.txt", suffix=".txt")
            url = SC_SITE.format(election) + version.path.read_text().strip() + "/reports/detailxml.zip"
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.fetch(SOURCE, url, suffix=".zip")
                time.sleep(1)
    for year in KY_DAYS:
        page = raw.fetch(SOURCE, KY_PAGE.format(year), suffix=".htm")
        for path in re.findall(
            rf'href="(/SiteCollectionDocuments/Election%20Results/2000-2009/{year}/[^"]*\.txt)"',
            page.path.read_text(errors="replace"),
            re.IGNORECASE,
        ):
            if re.search("pri", path, re.IGNORECASE) and not re.search(
                r"reg%20stat|regstat|by%20cong|by%20candidate|by%20county|bycounty", path, re.IGNORECASE
            ):
                try:
                    raw.cached(SOURCE, f"https://elect.ky.gov{path}")
                except LookupError:
                    raw.fetch(SOURCE, f"https://elect.ky.gov{path}", suffix=".txt")
                    time.sleep(0.5)
    for day in FL_DAYS:
        extract = raw.fetch(SOURCE, fl_sos.EXTRACT.format(day=day), suffix=".txt")
        for race, party, district in sorted(
            {
                (row[3], row[1], row[7] if row[3] in ("STR", "STS", "USR") else "")
                for row in csv.reader(io.StringIO(extract.path.read_bytes().decode("cp1252")), delimiter="\t")
                if row and row[3] in FL_RACES
            }
        ):
            url = FL_DETAIL.format(day=day, race=race, party=party, district=district)
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.fetch(SOURCE, url, suffix=".htm")
                time.sleep(1)
    for day in NC_DAYS:
        raw.fetch(SOURCE, FILES.format(day=day), suffix=".zip")
    for day in MN_DAYS:
        for name in ("allracesbyprecinct", "PrctTbl"):
            raw.fetch(SOURCE, MN_FILES.format(day=day, name=name), suffix=".txt")
    for path, names in CA_DAYS.values():
        for name in names:
            raw.fetch(SOURCE, CA_SOV.format(path=path.format(name)), suffix=Path(path).suffix)
    for state, offices in ELSTATS.items():
        for office in offices | (MA_FEDERAL if state == "MA" else {}):
            for year in MA_YEARS if state == "MA" else ELSTATS_YEARS:
                if state != "MA":
                    raw.fetch(SOURCE, search(state, office, year), suffix=".csv")
                    continue
                if year not in (MA_PRESIDENT if office == 1 else MA_DAYS):
                    continue
                listing = raw.fetch(SOURCE, search(state, office, year), suffix=".html").path.read_text()
                empty = set(MA_EMPTY.findall(listing))
                for election, _, _, stage in elstats.MA_ROW.findall(listing):
                    if election not in empty and "Special" not in stage:
                        raw.fetch(SOURCE, MA_DOWNLOAD.format(election), suffix=".csv")


def iowa() -> dict[tuple[int, str], str]:
    found: dict[tuple[int, str], tuple[str, str]] = {}
    for year in IA_DAYS:
        for line in raw.cached(SOURCE, IA_CDX.format(year)).path.read_text().splitlines():
            parts = line.split()
            matched = len(parts) == 4 and re.search(r"/primary/([^/]+)\.xlsx?$", parts[1])
            if matched and parts[2] == "200":
                county = re.sub(r"[^a-z]", "", unquote(matched[1]).lower())
                found[(year, county)] = max(found.get((year, county), ("", "")), (parts[0], parts[1]))
    return {key: f"https://web.archive.org/web/{stamp}id_/{url}" for key, (stamp, url) in sorted(found.items())}


def listed(value: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else [value]


def parse() -> None:
    with connect() as conn:
        counties: dict[tuple[str, str], str] = {}
        titles: set[str] = set()
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^(01|37|53|06|08|09|12|16|17|18|19|21|33|35|36|42|45|48|49|50|51)' order by u.code"""
        ).fetchall():
            plain = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())
            if code.startswith("36"):
                titles.add(text)
            if code.startswith("51") and int(code[2:]) >= 500:
                counties[(code[:2], f"{plain}city")] = code
            counties.setdefault((code[:2], plain), code)
        generals: dict[tuple[str, int, str], dict[str, str]] = {}
        for (value,) in conn.execute(
            """select value from external_id where scheme = 'us.contest' and value ~ %s""",
            [f"^20[0-2][0-9]-1[01]-[0-9]{{2}}:({'|'.join(ELSTATS)}):[a-z-]+:.+:g$"],
        ).fetchall():
            day, state, body, *rest = value.split(":")
            code = ":".join(rest[:-1])
            generals.setdefault((state, int(day[:4]), body), {})[re.sub(r"[^a-z0-9]", "", code)] = code
        areas = {
            code
            for (code,) in conn.execute(
                "select c.code from area a join area_code c on c.area_id = a.id where a.scheme_code in ('us.sldl', 'us.sldu')"
            ).fetchall()
        }
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in(list(ELSTATS)))
    towns: dict[tuple[str, str], str] = {}
    geoids: dict[str, str] = {}
    for state, geoid, name in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        towns.setdefault((state, elstats.plain(name)), geoid[:5])
        towns.setdefault((state, elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))), geoid[:5])
        if state == "MA":
            geoids.setdefault(elstats.plain(name), geoid)
            geoids.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid)
    regions = ct_regions()
    for (state, name), code in elstats.FORMER.items():
        towns.setdefault((state, name), code)
    towns[("VT", "essexjunction")] = towns[("VT", "essex")]
    frames = []
    ma_federal = []
    for state, offices in ELSTATS.items():
        fips = STATES[state][0]
        for office, body in (offices | (MA_FEDERAL if state == "MA" else {})).items():
            for year in MA_YEARS if state == "MA" else ELSTATS_YEARS:
                if state == "MA":
                    days = MA_PRESIDENT if office == 1 else MA_DAYS
                    if year not in days:
                        continue
                    listing = raw.cached(SOURCE, search(state, office, year)).path.read_text()
                    empty = set(MA_EMPTY.findall(listing))
                    tables = []
                    for election, _, district, stage in elstats.MA_ROW.findall(listing):
                        if election in empty or "Special" in stage:
                            continue
                        table = (
                            pl.read_csv(
                                raw.cached(SOURCE, MA_DOWNLOAD.format(election)).path,
                                infer_schema=False,
                                skip_rows_after_header=1,
                            )
                            .rename({"County/City": "City/Town"}, strict=False)
                            .drop("Total Votes Cast", strict=False)
                        )
                        long = (
                            table.filter(pl.col("City/Town").is_not_null())
                            .unpivot(
                                [column for column in table.columns[3:] if column],
                                index="City/Town",
                                variable_name="candidate_name",
                            )
                            .select(
                                division_name=pl.col("City/Town")
                                .str.replace(r"^N\. ", "North ")
                                .str.replace(r"^S\. ", "South ")
                                .str.replace(r"^E\. ", "East ")
                                .str.replace(r"^W\. ", "West "),
                                candidate_name="candidate_name",
                                votes=pl.col("value").str.replace_all(",", "").cast(pl.Int64),
                            )
                        )
                        summed = (
                            long.filter(pl.col("division_name") != "TOTALS")
                            .group_by("candidate_name")
                            .agg(pl.col("votes").sum())
                        )
                        if (
                            not long.filter(pl.col("division_name") == "TOTALS")
                            .drop("division_name")
                            .join(summed, on="candidate_name", how="full")
                            .filter(pl.col("votes") != pl.col("votes_right"))
                            .is_empty()
                        ):
                            print(f"{year} MA {body} {district} {stage}: towns do not add up to the totals, skipped")
                            continue
                        tables.append(
                            long.filter(pl.col("division_name") != "TOTALS").with_columns(
                                election_date=pl.lit(days[year].isoformat()),
                                election_type=pl.lit("State Primary"),
                                primary_party=pl.lit(
                                    stage.removesuffix(" Primary").replace("Green-rainbow", "Green-Rainbow")
                                ),
                                office_id=pl.lit(str(office)),
                                office_modifier=pl.lit(None, pl.String),
                                district_name=pl.lit(district),
                                division_type=pl.lit("City/Town"),
                                number_seats=pl.lit("1"),
                            )
                        )
                    frame = pl.concat(tables) if tables else pl.DataFrame()
                    if office in MA_FEDERAL:
                        ma_federal.extend(tables)
                        continue
                else:
                    frame = pl.read_csv(raw.cached(SOURCE, search(state, office, year)).path, infer_schema=False)
                if "office_id" not in frame.columns:
                    continue
                frame = frame.filter(
                    pl.col("office_id") == str(office),
                    pl.col("election_type").str.contains("(?i)primary"),
                    ~pl.col("election_type").str.contains("(?i)special|presidential"),
                    pl.col("office_modifier").is_null(),
                    pl.col("division_type").is_in(["County", "City/Town", "Locality"]),
                    pl.col("primary_party").is_not_null(),
                )
                if frame.is_empty():
                    continue
                known = generals.get((state, year, body), {})
                codes = {}
                for district in frame["district_name"].unique().to_list():
                    plain = re.sub(r"[^a-z0-9]", "", district.lower()) + {569: "1", 570: "2"}.get(office, "")
                    lettered = re.sub(
                        r"(\d+)-(\d+)$", lambda found: f"{found[1]}-{chr(96 + int(found[2]))}", district.lower()
                    )
                    code = next(
                        (
                            known[key]
                            for key in (
                                plain,
                                re.sub(r"[^a-z0-9]", "", lettered),
                                plain.removesuffix("1"),
                                re.sub(
                                    r"^(\d+)(?:st|nd|rd|th)(.+)$",
                                    r"\2\1",
                                    re.sub(
                                        r"[^a-z0-9]",
                                        "",
                                        re.sub(
                                            r"^(first|second|third|fourth|fifth|sixth)\b",
                                            lambda found: {
                                                "first": "1st",
                                                "second": "2nd",
                                                "third": "3rd",
                                                "fourth": "4th",
                                                "fifth": "5th",
                                                "sixth": "6th",
                                            }[found[1]],
                                            re.sub(r"\band\b", "", district.lower().replace("&", "and")),
                                        ),
                                    ),
                                ),
                            )
                            if key in known
                        ),
                        None,
                    )
                    if code is None or f"{state}:{code.split(':')[0]}" not in areas:
                        print(f"{year} {state} {body} {district}: no November seat")
                        continue
                    codes[district] = code
                pseudo = (
                    pl.col("candidate_name")
                    .str.to_lowercase()
                    .str.contains(
                        r"^(total (votes|ballots)( cast)?|blanks?|blank/void|void|undervotes|overvotes|registered voters|# enrolled|number checked as having voted|unknown candidate)$"
                    )
                )
                pooled = (
                    pl.col("candidate_name")
                    .str.to_lowercase()
                    .str.contains(r"^(all other votes|write-ins?|scattering|all others|other write-ins|write in)$")
                )
                places: dict[str, str | None] = {}
                for kind, name in frame.select("division_type", "division_name").unique().iter_rows():
                    bare = re.sub(r"\s*\(.*\)$", "", name)
                    places[name] = (
                        regions.get(elstats.plain(bare))
                        if state == "CT" and year >= 2022 and kind == "City/Town"
                        else towns.get((state, elstats.plain(bare)))
                        or towns.get((state, elstats.plain(re.sub(elstats.SUFFIXES, "", bare.lower()))))
                        if kind == "City/Town"
                        else counties.get(
                            (fips, re.sub(r"[^a-z]", "", re.sub(r"(?i)\b(county|locality)\b", "", bare).lower()))
                        )
                    )
                withheld: dict[str, int | None] = {}
                if frame["votes"].cast(pl.String).eq("*").any():
                    ballots = {
                        tuple(key): votes
                        for *key, votes in frame.filter(
                            pl.col("candidate_name").str.to_lowercase() == "total ballots cast"
                        )
                        .select("district_name", "primary_party", "division_name", "vote_channel", "votes")
                        .iter_rows()
                    }
                    for key in (
                        frame.filter(pl.col("votes") == "*")
                        .select("district_name", "primary_party", "division_name", "vote_channel")
                        .unique()
                        .iter_rows()
                    ):
                        bound = ballots.get(key)
                        so_far = withheld.get(key[0] + ":" + key[1], 0)
                        withheld[key[0] + ":" + key[1]] = (
                            None if bound in (None, "*") or so_far is None else so_far + int(bound)
                        )
                    for key, bound in withheld.items():
                        if bound is None:
                            print(f"{year} {state} {body} {key}: withheld counts with no ballot total, skipped")
                    frame = frame.filter(
                        ~(pl.col("district_name") + ":" + pl.col("primary_party")).is_in(
                            [key for key, bound in withheld.items() if bound is None]
                        )
                    )
                lines = frame.filter(~pseudo, pl.col("district_name").is_in(list(codes))).with_columns(
                    fips=pl.col("division_name").replace_strict(places, default=None, return_dtype=pl.String)
                )
                lost = lines.filter(pl.col("fips").is_null())
                for district, places in (
                    lost.group_by("district_name").agg(pl.col("division_name").unique()).iter_rows()
                ):
                    print(f"{year} {state} {body} {district}: no county for {places}, skipped")
                lines = lines.filter(~pl.col("district_name").is_in(lost["district_name"].unique().to_list()))
                lines = lines.with_columns(
                    party=pl.col("primary_party")
                    .replace(ELSTATS_PARTIES)
                    .replace({name: f"{state}:{name}" for name in ELSTATS_LOCAL})
                )
                other = lines.filter(~pl.col("party").is_in(list(PARTIES)))
                if other.height:
                    print(
                        f"{year} {state} {body}: {other['primary_party'].unique().sort().to_list()} primaries not read"
                    )
                frames.append(
                    lines.filter(pl.col("party").is_in(list(PARTIES)))
                    .select(
                        state=pl.lit(state),
                        day=pl.col("election_date")
                        .str.slice(0, 10)
                        .str.to_date()
                        .replace({wrong: right for (where, wrong), right in ELSTATS_DATES.items() if where == state}),
                        office=pl.lit(body),
                        code=pl.col("district_name").replace_strict(codes),
                        party="party",
                        label="party",
                        fips="fips",
                        scheme=pl.lit("us.town" if state == "MA" else "us.county"),
                        unit=pl.col("division_name").replace_strict(
                            {
                                name: geoids.get(elstats.plain(name))
                                or geoids.get(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())))
                                for name in lines["division_name"].unique()
                            },
                            return_dtype=pl.String,
                        )
                        if state == "MA"
                        else pl.col("fips"),
                        choice=pl.when(pooled)
                        .then(pl.lit("Write-ins"))
                        .otherwise(pl.col("candidate_name").str.strip_chars()),
                        writein=pooled,
                        votes=pl.col("votes").cast(pl.String).replace("*", None).cast(pl.Float64).cast(pl.Int64),
                        withheld=(pl.col("district_name") + ":" + pl.col("primary_party")).replace_strict(
                            withheld, default=0, return_dtype=pl.Int64
                        ),
                        seats=pl.col("number_seats").cast(pl.Int16),
                        seat="district_name",
                    )
                    .with_columns(
                        pl.col("choice")
                        .max()
                        .over(
                            "day", "code", "party", pl.col("choice").str.to_lowercase().str.replace_all("[^a-z0-9]", "")
                        )
                    )
                )
    me: list[tuple[int, str, str, str, str | None, str, str, int]] = []
    for stamp, path, office, party in ME_FILES:
        year = int(re.findall(r"20\d\d", path)[0])
        data = raw.cached(SOURCE, ME_WAYBACK.format(stamp, path)).path
        if year <= 2006:
            frame = pl.read_csv(data, infer_schema=False, encoding="latin1")
            frame.columns = [column.strip().lower() for column in frame.columns]
            for number in [
                column.removeprefix("fullname") for column in frame.columns if column.startswith("fullname")
            ]:
                for district, county, town, name, votes in frame.select(
                    "district",
                    pl.col("county") if "county" in frame.columns else pl.lit(None, pl.String),
                    "town",
                    f"fullname{number}",
                    f"votes{number}",
                ).iter_rows():
                    if name:
                        me.append((year, office, party, district, county, town, name, int(votes or 0)))
        elif year == 2008:
            for district, county, town, name, votes in (
                pl.read_csv(data, infer_schema=False, encoding="latin1", truncate_ragged_lines=True)
                .select(pl.nth(1), pl.nth(2), pl.nth(4), pl.nth(6), pl.nth(7))
                .iter_rows()
            ):
                me.append((year, office, party, district, county, town, name, int(votes)))
        elif year == 2010:
            district, heads = None, []
            for row in re.findall(r"(?is)<tr.*?</tr>", data.read_text(encoding="latin-1")):
                cells = [
                    [line.strip() for line in html.unescape(re.sub(r"(?s)<[^>]+>", "\n", cell)).splitlines()]
                    for cell in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row)
                ]
                cells = [[line for line in cell if line] for cell in cells]
                flat = [" ".join(" ".join(cell).split()) for cell in cells]
                if found := re.fullmatch(r"DISTRICT (\d+)", flat[-1] if flat else ""):
                    district, heads = found[1], []
                elif district is None or not flat or flat[0] == "Municipality" or flat[0].lower().startswith("total"):
                    continue
                elif flat[0] == "":
                    heads = [" ".join(" ".join(cell[:-1]).split()) for cell in cells[1:]]
                elif len(flat) == len(heads) + 1 and all(re.fullmatch(r"[\d,]+", votes) for votes in flat[1:]):
                    for name, votes in zip(heads, flat[1:], strict=True):
                        me.append((year, office, party, district, None, flat[0], name, int(votes.replace(",", ""))))
        else:
            rows = (
                [line.split("|") for line in data.read_text(encoding="latin-1").splitlines()]
                if year == 2012
                else [
                    ["" if cell is None else cell for cell in row]
                    for row in fastexcel.read_excel(data)
                    .load_sheet(0, header_row=None, dtypes="string")
                    .to_polars()
                    .iter_rows()
                ]
            )
            heading: dict[int, str] = {}
            expect = True
            for row in rows:
                cells = [cell.strip() for cell in row]
                if len(cells) > 3 and re.fullmatch(r"\d+(?:\.0)?", cells[0]):
                    for column, name in heading.items():
                        if column < len(cells) and cells[column]:
                            me.append(
                                (
                                    year,
                                    office,
                                    party,
                                    cells[0],
                                    cells[1] or None,
                                    cells[2],
                                    name,
                                    int(float(cells[column].replace(",", ""))),
                                )
                            )
                    expect = True
                    continue
                texts = {
                    column: cell
                    for column, cell in enumerate(cells)
                    if column >= 3 and cell and not re.fullmatch(r"[\d,.%]+", cell)
                }
                if expect and texts:
                    heading = {
                        column: re.sub(r"(?i)\s*\(declared write-in\)$", "", cell)
                        for column, cell in texts.items()
                        if not re.fullmatch(r"(?i)blank|total ballots cast|tbc|total votes cast", cell)
                    }
                    expect = False
    me_frame = pl.DataFrame(
        me,
        schema={
            "year": pl.Int64,
            "office": pl.String,
            "party": pl.String,
            "district": pl.String,
            "county": pl.String,
            "town": pl.String,
            "name": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        district=pl.col("district").cast(pl.Float64).cast(pl.Int64).cast(pl.String),
        fips=pl.col("county").str.strip_chars().replace_strict(me_legislature.COUNTIES, default=None),
        place=pl.col("town")
        .str.to_uppercase()
        .str.replace_all(r"\bST\b\.?", "SAINT")
        .str.replace_all(r"\bPLANTATION\b", "PLT")
        .str.replace(r"^NO\.?\s*(\d)", "T${1}")
        .str.replace_all(r"\b(?:TOWNSHIPS?|TWPS?)\b", "")
        .str.replace_all(r"[^A-Z0-9]", ""),
        uocava=pl.col("town").str.to_uppercase().str.contains("UOCAVA"),
        choice=ME_NAME,
    )
    known: dict[str, set[str]] = {}
    for place, fips in me_frame.filter(pl.col("fips").is_not_null()).select("place", "fips").unique().iter_rows():
        known.setdefault(place, set()).add(fips)
    resolved = {}
    for year, office, district, place in (
        me_frame.filter(pl.col("fips").is_null(), ~pl.col("uocava"))
        .select("year", "office", "district", "place")
        .unique()
        .iter_rows()
    ):
        homes = known.get(place, set())
        neighbors = me_frame.filter(pl.col("year") == year, pl.col("office") == office, pl.col("district") == district)[
            "place"
        ].to_list()
        if len(homes) > 1:
            homes = {max(homes, key=lambda fips: sum(known.get(other, set()) == {fips} for other in neighbors))}
        elif not homes:
            homes = {fips for other in neighbors if len(known.get(other, set())) == 1 for fips in known[other]}
            if len(homes) > 1:
                homes = set()
        if homes:
            resolved[(year, office, district, place)] = next(iter(homes))
        else:
            print(f"{year} ME {office} {district}: no county for {place}")
    me_frame = me_frame.with_columns(
        fips=pl.coalesce(
            "fips",
            pl.struct("year", "office", "district", "place").map_elements(
                lambda row: resolved.get((row["year"], row["office"], row["district"], row["place"])),
                return_dtype=pl.String,
            ),
        )
    )
    lost = me_frame.filter(pl.col("fips").is_null(), ~pl.col("uocava"))
    if lost.height:
        print(f"{lost.select('year', 'office', 'district').n_unique()} Maine districts with an unplaced town skipped")
    frames.append(
        me_frame.join(lost.select("year", "office", "district").unique(), on=["year", "office", "district"], how="anti")
        .with_columns(
            pl.col("choice")
            .max()
            .over(
                "year",
                "office",
                "district",
                "party",
                pl.col("choice").str.to_lowercase().str.replace_all("[^a-z0-9]", ""),
            )
        )
        .select(
            state=pl.lit("ME"),
            day=pl.col("year").replace_strict(ME_DAYS, return_dtype=pl.Date),
            office="office",
            code="district",
            party="party",
            label="party",
            fips="fips",
            scheme=pl.when("uocava").then(pl.lit("us.aggregate")).otherwise(pl.lit("us.county")),
            unit=pl.when("uocava").then(pl.lit("ME:uocava")).otherwise("fips"),
            choice="choice",
            writein=pl.lit(False),
            votes="votes",
            seats=pl.lit(1, pl.Int16),
            seat=pl.lit(None, pl.String),
        )
    )
    for day in NC_DAYS:
        with zipfile.ZipFile(raw.cached(SOURCE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0]).replace(b"\x00", b"")
        first = text.split(b"\n", 1)[0]
        headed = first.lower().lstrip(b'"').startswith(b"county")
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            has_header=headed,
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        if not headed:
            frame.columns = (
                HEADERLESS
                if frame.width == 9
                else [
                    "county",
                    "date",
                    "precinct",
                    "contest",
                    "choice",
                    "party",
                    "eday",
                    "absentee",
                    "provisional",
                    "votes",
                ]
            )
        frame = frame.rename(
            {
                column: {"party_cd": "party", "choice party": "party", "precinct": "precinct"}.get(
                    column.strip().lower(), COLUMNS.get(column.strip().lower(), column)
                )
                for column in frame.columns
            }
        )
        frame = frame.select(
            pl.col("county").str.strip_chars(),
            pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " "),
            pl.col("contest").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("choice").str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("party").fill_null("").str.strip_chars().str.to_uppercase(),
            pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
        ).filter(pl.col("choice").is_not_null(), pl.col("votes").is_not_null())
        labels = []
        for contest in frame["contest"].unique():
            suffix = SUFFIX.search(contest)
            bare = SUFFIX.sub("", contest)
            if found := LEGISLATIVE.fullmatch(bare):
                office, code = found[1].lower(), str(int(found[2] or found[3]))
            elif found := JUDICIAL.fullmatch(bare):
                seat = f"seat-{int(found[4])}" if found[4] else (found[2] or found[3] or "").lower()
                office = OFFICES[found[1] if found[1] != "SUPREME COURT" else "SUPREME COURT ASSOCIATE JUSTICE"][0]
                office, code = f"{office}:{seat}" if seat else office, None
                if suffix is None and 2004 <= day.year <= 2016:
                    labels.append({"contest": contest, "office": office, "code": code, "named": ""})
                    continue
            elif found := STATEWIDE.fullmatch(bare):
                office, code = OFFICES[found[1]][0], None
            else:
                continue
            labels.append({"contest": contest, "office": office, "code": code, "named": suffix[1] if suffix else None})
        if not labels:
            continue
        frame = frame.join(pl.DataFrame(labels, schema_overrides={"code": pl.String, "named": pl.String}), on="contest")
        writein = pl.col("choice").str.contains(r"(?i)write-?\s*in")
        frame = frame.with_columns(
            party=pl.coalesce("named", pl.when(pl.col("party").is_in(list(PARTIES))).then("party")),
            writein=writein,
        )
        sole = frame.filter(pl.col("party").is_not_null()).group_by("contest").agg(sole=pl.col("party").unique())
        frame = (
            frame.join(sole, on="contest", how="left")
            .with_columns(
                party=pl.coalesce(
                    "party",
                    pl.when(pl.col("writein") & (pl.col("sole").list.len() == 1)).then(pl.col("sole").list.first()),
                )
            )
            .drop("sole")
        )
        lost = frame.filter(pl.col("party").is_null())
        if lost.height:
            print(
                day, "rows without a party:", lost.group_by("contest", "choice").agg(pl.col("votes").sum()).rows()[:10]
            )
        pseudo = pl.col("precinct").str.to_uppercase().str.contains(f"^(?:{PSEUDO.pattern})$")
        frames.append(
            frame.filter(pl.col("party").is_not_null())
            .with_columns(
                fips=pl.col("county").map_elements(
                    lambda county: counties[("37", re.sub(r"[^a-z]", "", county.lower()))], return_dtype=pl.String
                )
            )
            .select(
                state=pl.lit("NC"),
                day=pl.lit(day),
                office="office",
                code="code",
                party="party",
                label="party",
                fips="fips",
                scheme=pl.when(pseudo).then(pl.lit("us.aggregate")).otherwise(pl.lit("us.precinct")),
                unit=pl.when(pseudo)
                .then(pl.concat_str(pl.lit("NC"), "fips", pl.lit("county-wide"), separator=":"))
                .otherwise(
                    pl.concat_str(
                        pl.lit(f"{day}:NC"), "fips", pl.col("county").str.to_uppercase(), "precinct", separator=":"
                    )
                ),
                choice=pl.when("writein").then(pl.lit("Write-ins")).otherwise("choice"),
                writein="writein",
                votes="votes",
            )
        )
    for day in MN_DAYS:
        names = pl.read_csv(
            io.BytesIO(
                raw.cached(SOURCE, MN_FILES.format(day=day, name="PrctTbl")).path.read_bytes().decode("cp1252").encode()
            ),
            separator=";",
            has_header=False,
            infer_schema=False,
        ).select(county="column_0", precinct="column_1", name=pl.col("column_2").str.strip_chars())
        frame = (
            pl.read_csv(
                io.BytesIO(
                    raw.cached(SOURCE, MN_FILES.format(day=day, name="allracesbyprecinct"))
                    .path.read_bytes()
                    .decode("cp1252")
                    .encode()
                ),
                separator=";",
                has_header=False,
                infer_schema=False,
                quote_char=None,
            )
            .select(
                county="column_1",
                precinct="column_2",
                contest=pl.col("column_4").str.to_uppercase().str.strip_chars(),
                choice=pl.col("column_7").str.strip_chars(),
                party=pl.col("column_10").str.strip_chars(),
                votes=pl.col("column_13").cast(pl.Int64),
            )
            .join(names, on=["county", "precinct"], how="left", validate="m:1")
        )
        labels = []
        for contest in frame["contest"].unique():
            if found := MN_LEGISLATIVE.fullmatch(contest):
                office = "house" if found[1] == "REPRESENTATIVE" else "senate"
                code = str(int(found[2])) + (f"-{found[3].lower()}" if found[3] else "")
            elif found := STATEWIDE.fullmatch(contest):
                office, code = OFFICES[found[1]][0], None
            else:
                continue
            labels.append({"contest": contest, "office": office, "code": code})
        frame = frame.join(pl.DataFrame(labels, schema_overrides={"code": pl.String}), on="contest")
        unknown = frame.filter(~pl.col("party").is_in(list(PARTIES)))
        if unknown.height:
            print(
                day,
                "rows of other parties:",
                unknown.group_by("contest", "party").agg(pl.col("votes").sum()).rows()[:10],
            )
        lost = frame.filter(pl.col("name").is_null())
        if lost.height:
            raise ValueError(f"{day}: precincts missing from the precinct table: {lost.head(5).rows()}")
        fips = pl.lit("27") + (pl.col("county").cast(pl.Int64) * 2 - 1).cast(pl.String).str.zfill(3)
        frames.append(
            frame.filter(pl.col("party").is_in(list(PARTIES))).select(
                state=pl.lit("MN"),
                day=pl.lit(day),
                office="office",
                code="code",
                party="party",
                label="party",
                fips=fips,
                scheme=pl.lit("us.precinct"),
                unit=pl.concat_str(
                    pl.lit(f"{day}:MN"), fips, pl.lit(""), pl.col("name").str.to_uppercase(), separator=":"
                ),
                choice="choice",
                writein=pl.lit(False),
                votes="votes",
            )
        )
    washington = {name: code for (prefix, name), code in counties.items() if prefix == "53"}
    for day in WA_DAYS:
        race = pl.col("Race").str.to_uppercase().str.replace_all(r"\s+-\s+|\s+", " ").str.strip_chars().alias("race")
        choice = (
            pl.col("Candidate")
            .map_elements(html.unescape, return_dtype=pl.String)
            .str.replace_all(r"\s+", " ")
            .str.strip_chars()
            .alias("choice")
        )
        totals = pl.read_csv(raw.cached(wa_local.SOURCE, wa_local.FILES[day]).path, infer_schema_length=0).select(
            race,
            choice,
            fips=pl.col("County")
            .str.to_lowercase()
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(washington, default=None),
            preference=pl.col("Party")
            .str.extract(r"\(Prefers (.+?) ?Party\)")
            .str.to_lowercase()
            .str.replace_all(r"\s+", " ")
            .str.strip_chars(),
            votes=pl.col("Votes").cast(pl.Int64),
        )
        rows = (
            pl.read_csv(
                raw.cached(wa_local.SOURCE, wa_local.PRECINCTS.format(day=day)).path,
                infer_schema=False,
                encoding="utf8-lossy",
            )
            .select(
                race,
                choice,
                fips=pl.col("CountyCode")
                .replace_strict(WA_COUNTIES, default=None)
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(washington, default=None),
                precinct=pl.col("PrecinctName").str.strip_chars(),
                votes=pl.col("Votes").cast(pl.Int64),
            )
            .filter(pl.col("precinct") != "Total")
        )
        labels = []
        for contest in pl.concat([totals["race"], rows["race"]]).unique():
            if found := WA_LEGISLATIVE.fullmatch(contest):
                office = "senate" if found[2] == "SENATOR" else "house"
                code = found[1] + (f":{found[3]}" if office == "house" else "")
            elif found := WA_STATEWIDE.fullmatch(contest):
                office, code = WA_OFFICES[found[1]], None
            else:
                continue
            labels.append({"race": contest, "office": office, "code": code})
        if not labels:
            continue
        named = pl.DataFrame(labels, schema_overrides={"code": pl.String})
        totals = totals.join(named, on="race").filter(~pl.col("choice").str.contains(r"(?i)rejected"))
        rows = rows.join(named, on="race").filter(~pl.col("choice").str.contains(r"(?i)rejected"))
        if totals["fips"].is_null().any() or rows["fips"].is_null().any():
            raise ValueError(f"{day}: unmatched Washington counties")
        sums = rows.group_by("office", "code", "fips", "choice").agg(pl.col("votes").sum())
        exact = (
            totals.join(sums, on=["office", "code", "fips", "choice"], how="left", nulls_equal=True)
            .group_by("office", "code", "fips")
            .agg(exact=(pl.col("votes") == pl.col("votes_right")).fill_null(False).all())
            .filter("exact")
            .drop("exact")
        )
        preferences = totals.group_by("office", "code", "choice").agg(pl.col("preference").drop_nulls().first())
        lines = pl.concat(
            [
                rows.join(exact, on=["office", "code", "fips"], nulls_equal=True).with_columns(
                    scheme=pl.when(pl.col("precinct") == "Countywide")
                    .then(pl.lit("us.county"))
                    .otherwise(pl.lit("us.precinct")),
                    unit=pl.when(pl.col("precinct") == "Countywide")
                    .then("fips")
                    .otherwise(pl.concat_str(pl.lit(f"{day}:WA"), "fips", pl.lit(""), "precinct", separator=":")),
                ),
                totals.join(exact, on=["office", "code", "fips"], how="anti", nulls_equal=True)
                .drop("preference")
                .with_columns(scheme=pl.lit("us.county"), unit=pl.col("fips")),
            ],
            how="diagonal",
        ).join(preferences, on=["office", "code", "choice"], how="left", nulls_equal=True)
        print(
            day,
            "Washington counties by precinct:",
            exact.height,
            "of",
            totals.select("office", "code", "fips").unique().height,
        )
        writein = pl.col("choice").str.contains(r"(?i)write-?\s*in")
        frames.append(
            lines.select(
                state=pl.lit("WA"),
                day=pl.lit(day),
                office="office",
                code="code",
                party=pl.lit(""),
                label=pl.when(writein)
                .then(None)
                .otherwise(pl.col("preference").replace_strict(PREFERENCES, default=None)),
                fips="fips",
                scheme="scheme",
                unit="unit",
                choice=pl.when(writein).then(pl.lit("Write-ins")).otherwise("choice"),
                writein=writein,
                votes="votes",
            )
        )
    rows = []
    for day, (path, names) in CA_DAYS.items():
        for name in names:
            office = next(office for pattern, office in CA_KINDS if re.search(pattern, name))
            sheet = fastexcel.read_excel(raw.cached(SOURCE, CA_SOV.format(path=path.format(name))).path)
            district = columns = previous = None
            for row in sheet.load_sheet(0, header_row=None).to_polars().iter_rows():
                cells = [None if cell is None else re.sub(r"\s+", " ", str(cell)).strip() or None for cell in row]
                filled = [(index, cell) for index, cell in enumerate(cells) if cell is not None]
                if len(filled) == 1 and (title := CA_TITLE.match(filled[0][1])):
                    district, columns = title[1], None
                elif (
                    previous is not None
                    and filled
                    and filled[0][0] > 0
                    and all(CA_PARTY.fullmatch(cell) for _, cell in filled)
                ):
                    columns = [
                        (
                            index,
                            re.sub(r"\s*\(write-in\)$", "", who).rstrip("*").strip(),
                            CA_CODES.get(found[1], found[1]),
                            cell.endswith("(W/I)") or who.endswith("(write-in)"),
                        )
                        for index, cell in filled
                        if (found := CA_PARTY.fullmatch(cell))
                        and index < len(previous)
                        and (who := previous[index]) is not None
                        and not who.lower().startswith("votes not cast")
                    ]
                elif columns and (fips := counties.get(("06", re.sub(r"[^a-z]", "", (cells[0] or "").lower())))):
                    rows.extend(
                        (day, office, district, party, writein, fips, choice, int(float(value)))
                        for index, choice, party, writein in columns
                        if (value := cells[index]) is not None
                    )
                previous = cells
    ca = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "office": pl.String,
            "code": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "fips": pl.String,
            "choice": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(code=pl.col("code").cast(pl.Int64).cast(pl.String))
    for (day, office), lines in ca.group_by("day", "office"):
        print(
            day, office, lines["code"].n_unique(), "districts,", lines.select("code", "choice").n_unique(), "candidates"
        )
    nyc = []
    for (day, party, office, district), stem in nyc_files().items():
        code = ELSTATS_PARTIES.get(party, f"NY:{party}")
        if code not in PARTIES:
            raise ValueError(f"{stem}: unknown party {party}")
        table = list(csv.reader(io.StringIO(raw.cached(SOURCE, f"{stem}%20EDLevel.csv").path.read_text("utf-8-sig"))))
        records = (
            [dict(zip(table[0], row, strict=True)) for row in table[1:]]
            if table[0][0] == "AD" and table[0][-1] == "Tally"
            else [dict(zip(row[: len(row) // 2], row[len(row) // 2 :], strict=True)) for row in table]
        )
        rows = pl.DataFrame(records).select(
            fips=pl.col("County").replace_strict(nyc_general.COUNTIES),
            ed=pl.col("AD") + pl.col("ED"),
            status="EDAD Status",
            choice=pl.col("Unit Name").str.strip_chars(),
            votes=pl.col("Tally").str.replace_all(",", "").cast(pl.Int64),
            heading=pl.col("Office/Position Title")
            + ":"
            + pl.col("District Key").cast(pl.Int64).cast(pl.String)
            + ":"
            + pl.col("Party/Independent Body"),
        )
        if set(rows["heading"]) != {
            f"{office_title}:{int(district)}:{party}" for office_title, body in NYC_OFFICES.items() if body == office
        }:
            raise ValueError(f"{stem}: the file holds {set(rows['heading'])}")
        if rows.filter(
            pl.col("status") != "IN-PLAY", ~pl.col("status").str.contains(r"^COMBINED INTO \d{3}/\d{2}$")
        ).height:
            raise ValueError(f"{stem}: unknown ED statuses {rows['status'].unique().to_list()}")
        if rows.filter(pl.col("status") != "IN-PLAY", pl.col("votes") != 0).height:
            raise ValueError(f"{stem}: a combined election district holds votes")
        borough = None
        tallies = []
        for row in csv.reader(io.StringIO(raw.cached(SOURCE, f"{stem}%20Recap.csv").path.read_text("utf-8-sig"))):
            if len(row) >= 6:
                if found := re.fullmatch(r"(?:Total for .* - )?(New York|Bronx|Kings|Queens|Richmond) County", row[3]):
                    tallies.append((found[1], row[4], row[5]))
                continue
            for cell in row:
                if found := re.search(r"(?:^|\n)(New York|Bronx|Kings|Queens|Richmond) County(?:\n|$)", cell):
                    borough = found[1]
                elif cell.startswith("Total for "):
                    borough = None
            if borough is not None and len(row) == 2 and re.fullmatch(r"[\d,]+", row[1]):
                tallies.append((borough, row[0], row[1]))
        recap = (
            pl.DataFrame(tallies, schema=["fips", "choice", "votes"], orient="row")
            .select(
                fips=pl.col("fips").replace_strict(nyc_general.COUNTIES),
                choice=pl.when(pl.col("choice").str.ends_with("(Write-In)"))
                .then(pl.lit(nyc_general.SCATTERED))
                .otherwise(pl.col("choice").str.strip_chars()),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
            )
            .filter(~pl.col("choice").is_in([*NYC_COUNTERS, *NYC_TOTALS]))
            .group_by("fips", "choice")
            .agg(pl.col("votes").sum())
        )
        differences = (
            rows.filter(~pl.col("choice").is_in(NYC_COUNTERS))
            .group_by("fips", "choice")
            .agg(pl.col("votes").sum())
            .join(recap, on=["fips", "choice"], how="full", coalesce=True, suffix="_recap")
            .filter(pl.col("votes").fill_null(0) != pl.col("votes_recap").fill_null(0))
        )
        if differences.height:
            raise ValueError(f"{stem}: the ED-level report differs from the recap:\n{differences}")
        nyc.append(
            rows.filter(pl.col("status") == "IN-PLAY", ~pl.col("choice").is_in(NYC_COUNTERS)).select(
                state=pl.lit("NY"),
                day=pl.lit(day),
                office=pl.lit(office),
                code=pl.lit(str(int(district))),
                party=pl.lit(code),
                label=pl.lit(code),
                fips="fips",
                scheme=pl.lit("us.precinct"),
                unit=pl.concat_str(pl.lit(f"{day}:NY"), "fips", "ed", separator=":"),
                choice=pl.when(pl.col("choice") == nyc_general.SCATTERED).then(pl.lit("Write-ins")).otherwise("choice"),
                writein=pl.col("choice") == nyc_general.SCATTERED,
                votes="votes",
                seats=pl.lit(1, pl.Int16),
                seat=pl.lit(str(int(district))),
                chamber=pl.lit(f"house:{slug(party)}"),
            )
        )
    city = pl.concat(nyc)
    contest = ["day", "office", "code", "party"]
    signature = (
        pl.col("choice")
        .str.replace_all(r"\(.*?\)|\".*?\"|,", " ")
        .str.to_lowercase()
        .str.split(" ")
        .list.eval(pl.element().filter(~pl.element().str.strip_chars(".").is_in(["", "jr", "sr", "ii", "iii", "iv"])))
    )
    signature = signature.list.first().str.slice(0, 1) + ":" + signature.list.last().str.replace_all("[^a-z0-9]", "")
    archive = pl.concat(frames, how="diagonal_relaxed").filter(pl.col("state") == "NY")
    overlap = (
        archive.group_by(contest)
        .agg(inside=pl.col("fips").is_in(list(nyc_general.COUNTIES.values())).all())
        .join(city.select(contest).unique(), on=contest)
    )
    print(f"NYC: {overlap.height} contests also in the archive, {overlap['inside'].sum()} wholly inside the city")
    names = (
        archive.join(overlap.filter("inside"), on=contest, how="semi")
        .filter(~pl.col("writein"))
        .group_by(*contest, "choice")
        .agg(pl.col("votes").sum())
        .with_columns(signature=signature)
    )
    totals = (
        city.join(overlap.filter("inside"), on=contest, how="semi")
        .filter(~pl.col("writein"))
        .group_by(*contest, "choice")
        .agg(pl.col("votes").sum())
        .with_columns(signature=signature)
        .join(names, on=[*contest, "signature"], how="left", suffix="_archive")
    )
    for row in totals.filter(pl.col("votes") != pl.col("votes_archive").fill_null(-1)).iter_rows(named=True):
        print(
            f"{row['day']} NY {row['office']} {row['code']} {row['party']}: {row['choice']} {row['votes']} "
            f"by election district against {row['choice_archive']} {row['votes_archive']} in the archive"
        )
    if totals.select(*contest, "choice").is_duplicated().any():
        raise ValueError("an election district candidate matches two archive names")
    city = (
        city.join(overlap.filter(~pl.col("inside")), on=contest, how="anti")
        .join(totals.select(*contest, "choice", "choice_archive"), on=[*contest, "choice"], how="left")
        .with_columns(choice=pl.coalesce("choice_archive", "choice"))
        .drop("choice_archive")
    )
    replaced = overlap.filter("inside").select(*contest, state=pl.lit("NY"), scheme=pl.lit("us.county"))
    frames.append(city.filter(pl.col("office") != "house").drop("chamber"))
    federal = ca.filter(pl.col("office").is_in(["house", "us-senate"]))
    ny_fips = {plain: fips for (state, plain), fips in counties.items() if state == "36"}
    ny_county = rf"(?:Part of )?({'|'.join(re.escape(title) for title in sorted(titles, key=len, reverse=True))})"
    certified = []
    for day, (stamp, link) in NY_FEDERAL.items():
        path = raw.cached(SOURCE, f"https://web.archive.org/web/{stamp}id_/{link}").path
        if path.suffix == ".xlsx":
            book = fastexcel.read_excel(path)
            for sheet in book.sheet_names:
                if not (found := re.fullmatch(r"CD (\d+) ([A-Z]{3})", sheet)):
                    continue
                table = book.load_sheet(sheet, header_row=None).to_polars().rows()
                heads = [
                    ny_fips[re.sub(r"[^a-z]", "", re.sub(r"^Part of |(?: County)?\s*Vote Results$", "", cell).lower())]
                    for cell in table[1][1:-1]
                ]
                for choice, *cells in table[2:]:
                    if choice in ("Blank", "Void", "Total Votes by County"):
                        continue
                    if int(cells[-1]) != sum(int(cell or 0) for cell in cells[:-1]):
                        raise ValueError(f"{day} {sheet}: {choice}'s counties do not add up")
                    certified.extend(
                        (
                            day,
                            int(found[1]),
                            NY_LINES[found[2]],
                            re.sub(r"\s*\([A-Z]{3}\)$", "", choice).strip(),
                            choice == "Scattering",
                            fips,
                            int(cell or 0),
                        )
                        for fips, cell in zip(heads, cells[:-1], strict=True)
                    )
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
        ).stdout.splitlines()
        for index, line in enumerate(text):
            if found := re.match(
                r"(?:Representative in Congress District (\d+)|(\d+)\w\w Congressional District) - (\w[\w' ]*?) - ",
                line,
            ):
                district = int(found[1] or found[2])
                block = []
                for following in text[index + 1 :]:
                    if re.search(r"Primary \w+ \d{1,2}, \d{4}$", following):
                        break
                    block.append(following)
                pattern = r"^(.+?)\s{2,}((?:[\d,]+\*?\s+)*[\d,]+\*?)$"
                first = next(position for position, row in enumerate(block) if re.match(pattern, row))
                ends = [number.end() for number in re.finditer(r"(?<!\S)[\d,]+\*?(?!\S)", block[first])]
                columns: list[list[str]] = [[] for _ in ends]
                for row in block[:first]:
                    for word in re.finditer(r"\S+", row):
                        columns[next(k for k, end in enumerate(ends) if word.start() < end)].append(word[0])
                heads = []
                for words in columns[:-1]:
                    county = re.search(ny_county, " ".join(words).replace("Herkiner", "Herkimer"))
                    if county is None:
                        raise ValueError(f"{day} NY-{district}: no county in {words}")
                    heads.append(ny_fips[re.sub(r"[^a-z]", "", county[1].lower())])
                for row in block:
                    candidate = re.match(pattern, row)
                    if candidate is None or candidate[1].strip().startswith(("Blank", "Void", "Total Votes")):
                        continue
                    numbers = [int(number.replace(",", "").rstrip("*")) for number in candidate[2].split()]
                    if len(numbers) != len(heads) + 1 or sum(numbers[:-1]) != numbers[-1]:
                        raise ValueError(f"{day} NY-{district}: {row.strip()} does not fit {heads}")
                    choice = re.sub(r"\s*\([A-Z]{3}\)$", "", candidate[1]).strip()
                    certified.extend(
                        (day, district, slug(found[3]), choice, choice == "Scattering", fips, votes)
                        for fips, votes in zip(heads, numbers[:-1], strict=True)
                    )
            elif found := re.fullmatch(r"(\d+)\w\w Congressional District\s*", line):
                block = []
                for following in text[index + 1 :]:
                    if re.fullmatch(r"\d+\w\w Congressional District\s*", following):
                        break
                    block.append(following)
                    if re.match(r"\s*(?:RECAP|Total)\s", following):
                        break
                rows = [
                    (row, match) for row in block if (match := re.match(rf"{ny_county}\s+((?:[\d,]+\s+)*[\d,]+)$", row))
                ]
                codes = next(row.split() for row in block if re.fullmatch(r"\s*(?:[A-Z]{3}\s+)*[A-Z]{3}\s*", row))
                if len(set(codes)) != 1 or not rows:
                    raise ValueError(f"{day} NY-{found[1]}: lines {codes}")
                ends = [number.end() for number in re.finditer(r"(?<!\S)[\d,]+(?!\S)", rows[0][0])]
                start = next(position for position, row in enumerate(block) if row.startswith("County"))
                words: list[list[str]] = [[] for _ in ends]
                for row in block[max(0, start - 2) : start + 1]:
                    for piece in re.finditer(r"\S+(?: \S+)*", row.removeprefix("County").rjust(len(row))):
                        nearest = min(range(len(ends)), key=lambda column: abs(ends[column] - piece.end()))
                        if abs(ends[nearest] - piece.end()) <= 3:
                            words[nearest].append(piece[0])
                total = next((row for row in block if re.match(r"\s*(?:RECAP|Total)\s", row)), rows[0][0])
                sums = [int(number.replace(",", "")) for number in re.findall(r"(?<!\S)[\d,]+(?!\S)", total)]
                for column, label in enumerate(words):
                    county = [int(match[2].split()[column].replace(",", "")) for _, match in rows]
                    if len(sums) != len(ends) or sum(county) != sums[column] or not label:
                        raise ValueError(f"{day} NY-{found[1]}: column {column} does not add up")
                    certified.extend(
                        (
                            day,
                            int(found[1]),
                            NY_LINES[codes[0]],
                            " ".join(label),
                            "opportunity" in " ".join(label).lower(),
                            ny_fips[re.sub(r"[^a-z]", "", match[1].lower())],
                            votes,
                        )
                        for (_, match), votes in zip(rows, county, strict=True)
                    )
    print(f"{len({row[:3] for row in certified})} federal primaries in the State Board's certified files")
    house = city.filter(pl.col("office") == "house").select(
        year=pl.col("day").dt.year(),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state="state",
        district=pl.col("code").cast(pl.Int64),
        candidate="choice",
        writein="writein",
        votes="votes",
        measure=pl.lit(1, pl.Int16),
        code="unit",
        kind=pl.lit("precinct"),
        source=pl.lit("NYC"),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    congress = pl.concat(
        [
            federal.select(
                year=pl.col("day").dt.year(),
                chamber=pl.concat_str(
                    pl.col("office").replace({"us-senate": "senate"}),
                    pl.when(pl.col("day").dt.year() < 2012).then(
                        pl.concat_str(
                            pl.lit(":"),
                            pl.col("party").replace_strict(
                                {code: word for code, (_, word, *_) in PARTIES.items()}, default=None
                            ),
                        )
                    ),
                    ignore_nulls=True,
                ),
                day="day",
                special=pl.lit(False),
                state=pl.lit("CA"),
                district=pl.col("code").cast(pl.Int64),
                candidate="choice",
                writein="writein",
                votes="votes",
                measure=pl.lit(1, pl.Int16),
                code="fips",
                kind=pl.lit("county"),
                source=pl.lit("CA"),
            ).filter(pl.col("chamber").str.contains(":") | (pl.col("year") >= 2012)),
            house,
            pl.DataFrame(
                certified,
                schema={
                    "day": pl.Date,
                    "district": pl.Int64,
                    "party": pl.String,
                    "candidate": pl.String,
                    "writein": pl.Boolean,
                    "code": pl.String,
                    "votes": pl.Int64,
                },
                orient="row",
            ).select(
                year=pl.col("day").dt.year(),
                chamber=pl.lit("house:") + pl.col("party"),
                day="day",
                special=pl.lit(False),
                state=pl.lit("NY"),
                district="district",
                candidate="candidate",
                writein="writein",
                votes="votes",
                measure=pl.lit(1, pl.Int16),
                code="code",
                kind=pl.lit("county"),
                source=pl.lit("NYSBOE"),
            ),
        ]
    )
    illinois = re.compile(
        r"(\d+)(?:ST|ND|RD|TH) (REPRESENTATIVE|SENATE)|([1-5])(?:ST|ND|RD|TH) (SUPREME|APPELLATE) - (.+) VACANCY|"
        + "|".join(OFFICES)
    )
    il: list[tuple[date, str, str | None, str, str, str, int, str | None]] = []
    il_federal_title = re.compile(r"(\d+)(?:ST|ND|RD|TH) CONGRESS|UNITED STATES SENATOR|PRESIDENT")
    il_federal: list[tuple[date, str, int | None, str, str, str, int]] = []
    for year, (county, total) in IL_FILES.items():
        books = []
        for token in (county, total):
            text = raw.cached(SOURCE, IL_DOWNLOAD.format(token)).path.read_text(encoding="latin-1")
            rows = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text.split("\n", 1)[0] else ","))
            heads = [cell.strip() for cell in rows[0]]
            books.append(
                [dict(zip(heads, [cell.strip() for cell in row], strict=True)) for row in rows[1:] if any(row)]
            )
        sums: dict[tuple[str, str], int] = {}
        for row in books[0]:
            if (federal := il_federal_title.fullmatch(row["OfficeName"])) is not None:
                sums[(row["OfficeName"], row["CandidateID"])] = sums.get(
                    (row["OfficeName"], row["CandidateID"]), 0
                ) + int(row["Votes"])
                il_federal.append(
                    (
                        IL_DAYS[year],
                        "congress" if federal[1] else "us-senate" if "SENATOR" in row["OfficeName"] else "president",
                        int(federal[1]) if federal[1] else None,
                        row["PartyAbbrev"],
                        counties[("17", re.sub(r"[^a-z]", "", row["County"].lower()))],
                        re.sub(
                            r"\b(Ii|Iii|Iv)\b",
                            lambda numeral: numeral[1].upper(),
                            re.sub(
                                r"\bMc([a-z])",
                                lambda prefix: "Mc" + prefix[1].upper(),
                                re.sub(r"\s+", " ", f"{row['CanFirstName']} {row['CanLastName']}").strip().title(),
                            ),
                        ),
                        int(row["Votes"]),
                    )
                )
                continue
            if (found := illinois.fullmatch(row["OfficeName"])) is None:
                continue
            sums[(row["OfficeName"], row["CandidateID"])] = sums.get((row["OfficeName"], row["CandidateID"]), 0) + int(
                row["Votes"]
            )
            name = re.sub(r"\s+", " ", f"{row['CanFirstName']} {row['CanLastName']}").strip().title()
            vacancy = re.sub(
                r"\b(Ii|Iii|Iv)\b",
                lambda numeral: numeral[1].upper(),
                re.sub(r"\bMc([a-z])", lambda prefix: "Mc" + prefix[1].upper(), (found[5] or "").title()),
            )
            il.append(
                (
                    IL_DAYS[year],
                    ("house" if found[2] == "REPRESENTATIVE" else "senate")
                    if found[1]
                    else f"{'justice-of-the-supreme-court' if found[4] == 'SUPREME' else 'judge-of-the-appellate-court'}:{found[3]}:{slug(vacancy)}"
                    if found[3]
                    else OFFICES[found[0]][0],
                    str(int(found[1])) if found[1] else None,
                    row["PartyAbbrev"],
                    counties[("17", re.sub(r"[^a-z]", "", row["County"].lower()))],
                    re.sub(
                        r"\b(Ii|Iii|Iv)\b",
                        lambda numeral: numeral[1].upper(),
                        re.sub(r"\bMc([a-z])", lambda prefix: "Mc" + prefix[1].upper(), name),
                    ),
                    int(row["Votes"]),
                    vacancy or None,
                )
            )
        if sums != {
            (row["OfficeName"], row["CandidateID"]): int(row["Votes"])
            for row in books[1]
            if illinois.fullmatch(row["OfficeName"]) or il_federal_title.fullmatch(row["OfficeName"])
        }:
            raise ValueError(f"{year} Illinois: the county rows do not match the statewide totals")
    frames.append(
        pl.DataFrame(
            il, schema=["day", "office", "code", "party", "fips", "choice", "votes", "seat"], orient="row"
        ).with_columns(
            state=pl.lit("IL"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    ky: list[tuple[date, str, str | None, str, str, str, int]] = []
    for year, day in KY_DAYS.items():
        files = [
            f"https://elect.ky.gov{path}"
            for path in re.findall(
                rf'href="(/SiteCollectionDocuments/Election%20Results/2000-2009/{year}/[^"]*\.txt)"',
                raw.cached(SOURCE, KY_PAGE.format(year)).path.read_text(errors="replace"),
                re.IGNORECASE,
            )
            if re.search("pri", path, re.IGNORECASE)
            and not re.search(r"reg%20stat|regstat|by%20cong|by%20candidate|by%20county|bycounty", path, re.IGNORECASE)
        ]
        office = next(url for url in files if re.search(r"by%20office|byoffice", url, re.IGNORECASE))
        totals = {
            (block[:11], party, re.sub(r"\s+", " ", name).strip()): int(votes.replace(",", "") or 0)
            for block in raw.cached(SOURCE, office).path.read_text(errors="replace").split("OFF/DST/DIV: ")[1:]
            if block[:3] in KY_OFFICES
            for party, name, votes in KY_LINE.findall(block)
        }
        sums: dict[tuple[str, str, str], int] = {}
        for url in files:
            if url == office:
                continue
            text = raw.cached(SOURCE, url).path.read_text(errors="replace")
            banner = re.search(r"^\s*(\S.*?)\s+COUNTY REPORT", text, re.MULTILINE)
            if banner is None:
                raise ValueError(f"{url}: no county heading")
            fips = counties[("21", re.sub(r"[^a-z]", "", banner[1].lower()))]
            for block in text.split("OFF/DST/DIV: ")[1:]:
                if block[:3] not in KY_OFFICES:
                    continue
                for party, name, votes in KY_LINE.findall(block):
                    choice = re.sub(r"\s+", " ", name).strip()
                    sums[(block[:11], party, choice)] = sums.get((block[:11], party, choice), 0) + int(
                        votes.replace(",", "") or 0
                    )
                    ky.append(
                        (
                            day,
                            KY_OFFICES[block[:3]] if block[:3] in ("D14", "D15") else OFFICES[KY_OFFICES[block[:3]]][0],
                            str(int(block[4:7])) if block[:3] in ("D14", "D15") else None,
                            {"D": "DEM", "R": "REP"}[party],
                            fips,
                            choice,
                            int(votes.replace(",", "") or 0),
                        )
                    )
        if sums != totals:
            raise ValueError(f"{year} Kentucky: the county reports do not match the statewide report by office")
    frames.append(
        pl.DataFrame(
            ky, schema=["day", "office", "code", "party", "fips", "choice", "votes"], orient="row"
        ).with_columns(
            state=pl.lit("KY"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    fl: list[tuple[date, str, str | None, str, str, str, int]] = []
    counties[("12", "dade")] = counties[("12", "miamidade")]
    for day in FL_DAYS:
        extract = (
            pl.read_csv(
                io.BytesIO(
                    raw.cached(SOURCE, fl_sos.EXTRACT.format(day=day)).path.read_bytes().decode("cp1252").encode()
                ),
                separator="\t",
                infer_schema_length=0,
                quote_char=None,
            )
            .filter(pl.col("RaceCode").is_in(list(FL_RACES)))
            .with_columns(
                district=pl.when(pl.col("RaceCode").is_in(["STR", "STS", "USR"]))
                .then("Juris1num")
                .otherwise(pl.lit("")),
                choice=pl.concat_str(
                    [
                        pl.col(column).fill_null("").str.replace_all(",", "").str.strip_chars()
                        for column in ("CanNameFirst", "CanNameMiddle", "CanNameLast")
                    ],
                    separator=" ",
                )
                .str.replace_all(r"\s+", " ")
                .str.strip_chars(),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
        )
        extract = extract.unique(["PartyCode", "RaceCode", "CountyName", "district", "choice", "votes"])
        if extract.select(
            pl.struct("PartyCode", "RaceCode", "CountyName", "district", "choice").is_duplicated().any()
        ).item():
            raise ValueError(f"{day} Florida: a candidate has two different county rows")
        for (race, party, district), rows in extract.group_by("RaceCode", "PartyCode", "district"):
            assert isinstance(race, str) and isinstance(party, str) and isinstance(district, str)
            text = raw.cached(
                SOURCE, FL_DETAIL.format(day=day, race=race, party=party, district=district)
            ).path.read_text(encoding="latin-1")
            report: dict[str, list[int]] = {}
            for line in re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL):
                found = [
                    html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                    for cell in re.findall(r"<TD[^>]*>(.*?)</TD>", line, re.IGNORECASE | re.DOTALL)
                ]
                if len(found) > 1 and all(re.fullmatch(r"[\d,]+", cell) for cell in found[1:]):
                    report[re.sub(r"[^a-z]", "", found[0].lower())] = sorted(
                        int(cell.replace(",", "")) for cell in found[1:]
                    )
            mine = {
                re.sub(r"[^a-z]", "", county.lower()): sorted(group["votes"].to_list())
                for (county,), group in rows.group_by("CountyName")
            }
            mine["total"] = sorted(rows.group_by("choice").agg(pl.col("votes").sum())["votes"].to_list())
            if mine != report:
                raise ValueError(
                    f"{day} Florida {race} {party} {district}: the extract does not match the detail report"
                )
            fl.extend(
                (
                    day,
                    FL_RACES[race] if race in ("STR", "STS", "USR", "USS") else OFFICES[FL_RACES[race]][0],
                    str(int(district)) if district else None,
                    party,
                    counties[("12", re.sub(r"[^a-z]", "", county.lower()))],
                    choice,
                    votes,
                )
                for county, choice, votes in rows.select("CountyName", "choice", "votes").iter_rows()
            )
    fl_frame = pl.DataFrame(
        fl, schema=["day", "office", "code", "party", "fips", "choice", "votes"], orient="row", infer_schema_length=None
    )
    fl_federal = fl_frame.filter(pl.col("office").is_in(["congress", "us-senate"]))
    frames.append(
        fl_frame.filter(~pl.col("office").is_in(["congress", "us-senate"])).with_columns(
            state=pl.lit("FL"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    tx: list[tuple[date, str, str, str, str, str, int]] = []
    for election in tx_elchist.PRIMARIES:
        for race, title in tx_elchist.races(election):
            if (found := TX_RACE.fullmatch(title)) is None:
                continue
            text = raw.cached(SOURCE, f"{tx_elchist.SITE}elchist{election}_race{race}.htm").path.read_text(
                errors="replace"
            )
            stamp = re.search(r"<H3[^>]*>(\d+)/(\d+)/(\d{4})</H3>", text, re.IGNORECASE)
            if stamp is None:
                raise ValueError(f"{election} {race}: no heading")
            day = date(int(stamp[3]), int(stamp[1]), int(stamp[2]))
            if day not in TX_DAYS:
                raise ValueError(f"{election} {race}: {day} is not a Texas primary day")
            lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
            headers = [tx_elchist.cells(line, "TH") for line in lines if re.search(r"<TH", line, re.IGNORECASE)]
            data = [tx_elchist.cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
            parties = headers[-1]
            total = next((row for row in data if row[0] == "ALL COUNTIES"), data[0] if len(data) == 1 else None)
            if total is None:
                raise ValueError(f"{election} {race}: no All Counties row")
            for column in range(1, parties.index("Votes")):
                if parties[column] not in ("DEM", "REP"):
                    raise ValueError(f"{election} {race}: party {parties[column]}")
                choice = " ".join(
                    row[column] for row in headers[:-1] if column < len(row) and row[column] not in ("...", "Total")
                )
                votes = [(row[0], int(row[column].replace(",", "") or 0)) for row in data if row[0] != "ALL COUNTIES"]
                if sum(value for _, value in votes) != int(total[column].replace(",", "")):
                    raise ValueError(f"{election} {race} {choice}: the county rows do not match All Counties")
                tx.extend(
                    (
                        day,
                        "house" if found[1] else "senate",
                        str(int(found[1] or found[2])),
                        parties[column],
                        counties[("48", re.sub(r"[^a-z]", "", county.lower()))],
                        re.sub(r"\s+", " ", choice).strip(),
                        value,
                    )
                    for county, value in votes
                )
    frames.append(
        pl.DataFrame(
            tx, schema=["day", "office", "code", "party", "fips", "choice", "votes"], orient="row"
        ).with_columns(
            state=pl.lit("TX"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    hawkeye: dict[tuple[date, str, str | None, str, str, str, bool], int] = {}
    ia_federal: dict[tuple[date, str, str | None, str, str, str, bool], int] = {}
    ia_unread: set[str] = set()
    ia_compact: set[tuple[int, str, str]] = set()
    for (ia_year, ia_plain), ia_url in iowa().items():
        ia_county = counties[("19", ia_plain)]
        ia_book = fastexcel.read_excel(raw.cached(SOURCE, ia_url).path)
        ia_sheet = next(
            ia_rows
            for ia_rows in (
                ia_book.load_sheet(ia_at, header_row=None).to_polars() for ia_at in range(len(ia_book.sheet_names))
            )
            if len(ia_rows)
        )
        ia_grid = [
            ["" if ia_cell is None else re.sub(r"\s+", " ", str(ia_cell)).strip() for ia_cell in ia_row]
            for ia_row in ia_sheet.iter_rows()
        ]
        ia_entries: list[tuple[str, str, int]] = []
        if ia_grid[0][0] == "Race":
            ia_header: list[str] = []
            ia_sums: list[int] = []
            ia_race = ""
            ia_headless = False
            ia_kept = [ia_row for ia_row in ia_grid if ia_row[0]]
            for ia_last, ia_row in zip([[""], *ia_kept], ia_kept, strict=False):
                if ia_row[0] == ia_last[0] == "Grand Totals":
                    continue
                if ia_row[0] == "Race":
                    if any(ia_sums):
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_race} has no grand totals")
                    ia_header = ia_row[: ia_row.index("Precinct Totals") + 1]
                    ia_sums = [0] * (len(ia_header) - 3)
                elif ia_last[0] == "Grand Totals":
                    ia_race, ia_headless = ia_row[0], True
                    if IA_RACE.fullmatch(ia_race.upper()):
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_race} has no header")
                elif ia_headless:
                    ia_headless = ia_row[0] != "Grand Totals"
                elif ia_row[0] == "Grand Totals":
                    if [int(float(ia_cell or 0)) for ia_cell in ia_row[3 : len(ia_header)]] != ia_sums:
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_race} does not add up to its grand totals")
                    ia_entries.extend(
                        (ia_race, ia_name, ia_total)
                        for ia_name, ia_total in zip(ia_header[3:-1], ia_sums[:-1], strict=True)
                    )
                    ia_sums = [0] * len(ia_sums)
                else:
                    ia_race = ia_row[0]
                    ia_values = [int(float(ia_cell or 0)) for ia_cell in ia_row[3 : len(ia_header)]]
                    if sum(ia_values[:-1]) != ia_values[-1]:
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_race} {ia_row[2]} does not add up")
                    ia_sums = [ia_total + ia_value for ia_total, ia_value in zip(ia_sums, ia_values, strict=True)]
            if any(ia_sums):
                raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_race} has no grand totals")
        elif ia_grid[0][0] == "RaceTitle":
            ia_header = ia_grid[0]
            ia_totals = [
                ia_at
                for ia_at, ia_label in enumerate(ia_header)
                if ia_label == f"{next(ia_name for ia_name in ia_header[3:] if '-' in ia_name).split('-')[0]} Total"
            ]
            if len(ia_totals) != 1 or any(
                ia_label and not re.search(r"[ -](Absentee|Polling|Total)$", ia_label) for ia_label in ia_header[3:]
            ):
                raise ValueError(f"Iowa {ia_year} {ia_plain}: unexpected columns")
            ia_split = {
                ia_label.rsplit(" ", 1)[0] for ia_label in ia_header if re.search(r" (Absentee|Polling)$", ia_label)
            }
            ia_parts = [
                ia_at
                for ia_at, ia_label in enumerate(ia_header[3:], 3)
                if ia_label
                and ia_at != ia_totals[0]
                and ia_label.rsplit(" ", 1)[0] not in ia_split
                or re.search(r" (Absentee|Polling)$", ia_label)
            ]
            for ia_row in ia_grid[1:]:
                if not ia_row[0]:
                    continue
                ia_total = int(float(ia_row[ia_totals[0]] or 0))
                if sum(int(float(ia_row[ia_at] or 0)) for ia_at in ia_parts) != ia_total:
                    ia_total = int(float(ia_row[ia_totals[0]] or [ia_cell for ia_cell in ia_row if ia_cell][-1]))
                    ia_compact.add((ia_year, ia_plain, ia_row[0]))
                ia_entries.append((ia_row[0], ia_row[1], ia_total))
        else:
            ia_blocks: dict[str, tuple[list[str], list[int]]] = {}
            ia_done: set[str] = set()
            ia_open: str | None = None
            ia_names: list[str] | None = None
            for ia_row in ia_grid:
                ia_cells = [ia_cell for ia_cell in ia_row if ia_cell]
                if len(ia_cells) == 2 and ia_cells[1] == "Total" and " - " in ia_cells[0]:
                    ia_open, ia_names = ia_cells[0], None
                elif ia_open is not None and ia_names is None:
                    ia_names = ia_cells
                    if ia_blocks.setdefault(ia_open, (ia_names, [0] * (len(ia_names) + 1)))[0] != ia_names:
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_open} changes its columns")
                elif ia_open is not None and ia_names is not None and ia_cells and ia_cells[0] == "Total":
                    if [int(float(ia_cell)) for ia_cell in ia_cells[1:]] != ia_blocks[ia_open][1]:
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_open} does not add up to its total")
                    ia_entries.extend(
                        (ia_open, ia_name, ia_total)
                        for ia_name, ia_total in zip(ia_names, ia_blocks[ia_open][1][:-1], strict=True)
                    )
                    ia_done.add(ia_open)
                    ia_open = None
                elif ia_open is not None and ia_names is not None and len(ia_cells) == len(ia_names) + 2:
                    ia_values = [int(float(ia_cell)) for ia_cell in ia_cells[1:]]
                    if sum(ia_values[:-1]) != ia_values[-1]:
                        raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_open} {ia_cells[0]} does not add up")
                    ia_blocks[ia_open] = (
                        ia_names,
                        [
                            ia_total + ia_value
                            for ia_total, ia_value in zip(ia_blocks[ia_open][1], ia_values, strict=True)
                        ],
                    )
            if set(ia_blocks) != ia_done:
                raise ValueError(f"Iowa {ia_year} {ia_plain}: {sorted(set(ia_blocks) - ia_done)} have no totals")
        for ia_title, ia_candidate, ia_votes in ia_entries:
            if ia_candidate.upper() in ("OVER VOTES", "UNDER VOTES", "BLANK"):
                continue
            ia_upper = IA_FEDERAL.fullmatch(ia_title.upper())
            if ia_upper is not None:
                ia_writein = re.fullmatch(r"(?i)write-?in", ia_candidate) is not None
                ia_key = (
                    IA_DAYS[ia_year],
                    "us-senate" if ia_upper[1] else "congress",
                    None if ia_upper[1] else str(int(ia_upper[2])),
                    "DEM" if ia_upper[3].startswith("D") else "REP",
                    ia_county,
                    "WRITE-INS" if ia_writein else re.sub(r"\s+", " ", ia_candidate).strip().upper(),
                    ia_writein,
                )
                if ia_key in ia_federal:
                    raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_title} {ia_candidate} appears twice")
                ia_federal[ia_key] = ia_votes
                continue
            ia_found = IA_RACE.fullmatch(ia_title.upper())
            if ia_found is None:
                ia_unread.add(ia_title)
                continue
            ia_writein = re.fullmatch(r"(?i)write-?in", ia_candidate) is not None
            ia_choice = re.sub(r"\s+", " ", ia_candidate).strip()
            if ia_choice.isupper():
                ia_choice = re.sub(
                    r"\b(Ii|Iii|Iv)\b",
                    lambda numeral: numeral[1].upper(),
                    re.sub(r"\bMc([a-z])", lambda prefix: "Mc" + prefix[1].upper(), ia_choice.title()),
                )
            ia_key = (
                IA_DAYS[ia_year],
                ("senate" if ia_found[1] == "SENATOR" else "house") if ia_found[1] else OFFICES[ia_found[3]][0],
                str(int(ia_found[2])) if ia_found[1] else None,
                "DEM" if ia_found[4].startswith("D") else "REP",
                ia_county,
                "Write-ins" if ia_writein else ia_choice,
                ia_writein,
            )
            if ia_key in hawkeye:
                raise ValueError(f"Iowa {ia_year} {ia_plain}: {ia_title} {ia_candidate} appears twice")
            hawkeye[ia_key] = ia_votes
    ia_missing = {(ia_year, ia_plain) for ia_year in IA_DAYS for _, ia_plain in iowa()} - set(iowa())
    if ia_missing != {(2016, "obrien")}:
        raise ValueError(f"Iowa county files missing: {sorted(ia_missing)}")
    print("Iowa titles not read:", sorted(ia_unread))
    print("Iowa rows whose precincts do not line up with the header:", sorted(ia_compact))
    ia_obrien = {
        (ia_office, ia_code)
        for ia_day, ia_office, ia_code, _, ia_fips, _, _ in hawkeye
        if ia_day == IA_DAYS[2014] and ia_fips == counties[("19", "obrien")]
    }
    iowa_frame = pl.DataFrame(
        [
            (*ia_key, ia_votes)
            for ia_key, ia_votes in hawkeye.items()
            if not (ia_key[0] == IA_DAYS[2016] and (ia_key[2] is None or (ia_key[1], ia_key[2]) in ia_obrien))
        ],
        schema={
            "day": pl.Date,
            "office": pl.String,
            "code": pl.String,
            "party": pl.String,
            "fips": pl.String,
            "choice": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    )
    ia_fourth = {
        ia_code
        for ia_day, ia_office, ia_code, _, ia_fips, _, _ in ia_federal
        if ia_day == IA_DAYS[2014] and ia_office == "congress" and ia_fips == counties[("19", "obrien")]
    }
    ia_upper_frame = pl.DataFrame(
        [
            (*ia_key[:6], ia_votes)
            for ia_key, ia_votes in ia_federal.items()
            if not (ia_key[0] == IA_DAYS[2016] and (ia_key[2] is None or ia_key[2] in ia_fourth))
        ],
        schema={
            "day": pl.Date,
            "office": pl.String,
            "code": pl.String,
            "party": pl.String,
            "fips": pl.String,
            "choice": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    frames.append(
        iowa_frame.filter((~pl.col("writein")).any().over("day", "office", "code", "party")).with_columns(
            state=pl.lit("IA"),
            label=pl.when(~pl.col("writein")).then("party"),
            scheme=pl.lit("us.county"),
            unit="fips",
            seats=pl.lit(1, pl.Int16),
        )
    )
    alabama: dict[tuple[date, str, str | None, str, str, str], int] = {}
    for day, url in AL_FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            for member in archive.namelist():
                county = counties[
                    ("01", re.sub(r"[^a-z]", "", re.sub(r".*Primary-(?:Runoff-)?|\.xls$", "", member).lower()))
                ]
                sheet = fastexcel.read_excel(archive.read(member)).load_sheet(0, header_row=None).to_polars()
                for title, party, candidate, *cells in sheet.iter_rows():
                    found = AL_RACE.fullmatch(re.sub(r"\s+", " ", title or "").strip())
                    if found is None or candidate.strip() in ("Over Votes", "Under Votes"):
                        continue
                    key = (
                        day,
                        ("house" if found[1] == "REPRESENTATIVE" else "senate")
                        if found[1]
                        else OFFICES[found[3].removeprefix("STATE ")][0],
                        str(int(found[2])) if found[1] else None,
                        party.strip(),
                        county,
                        re.sub(r"\s+", " ", candidate).strip(),
                    )
                    if key in alabama:
                        raise ValueError(f"{member} {title}: {candidate} appears twice")
                    alabama[key] = sum(int(cell) for cell in cells if cell is not None and cell.strip())
    frames.append(
        pl.DataFrame(
            [(*key, votes) for key, votes in alabama.items()],
            schema={
                "day": pl.Date,
                "office": pl.String,
                "code": pl.String,
                "party": pl.String,
                "fips": pl.String,
                "choice": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        ).with_columns(
            state=pl.lit("AL"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    hoosier: list[tuple[date, str, str | None, str, str, str, int]] = []
    for year, category, label in IN_FILES:
        in_day = IN_DAYS[year]
        root = json.loads(
            raw.cached(SOURCE, IN_ARCHIVE.format(year, category, IN_VERSIONS[year]))
            .path.read_bytes()
            .decode("utf-8-sig")
        )["Root"]
        totals = {
            (race["OFFICE_TITLE"], entry["PARTY"], entry["NAME_ON_BALLOT"].lower()): int(entry["TOTAL"])
            for race in listed(root["StatewideSummary"]["Race"])
            for entry in listed(race["Candidates"]["Candidate"])
        }
        sums: dict[tuple[str, str, str], int] = {}
        for region in listed(root["OfficeCategory"]["Regions"]["Region"]):
            for race in listed(region["Races"]["Race"]):
                for entry in listed(race["Candidates"]["Candidate"]):
                    key = (race["OFFICE_TITLE"], entry["PARTY_ABBREV"], entry["NAME_ON_BALLOT"].lower())
                    sums[key] = sums.get(key, 0) + int(entry["TOTAL_VOTES"])
                    numbers = re.findall(r"\d+", race["OFFICE_TITLE"])
                    ordinals = [n for n, word in enumerate(elstats.ORDINALS, 1) if word in race["OFFICE_TITLE"].lower()]
                    district = (
                        str(int(numbers[-1]))
                        if numbers
                        else str(ordinals[0])
                        if ordinals and label == "house"
                        else None
                    )
                    hoosier.append(
                        (
                            in_day,
                            label if label in ("house", "senate") else OFFICES[label][0],
                            district,
                            {"D": "DEM", "R": "REP"}[entry["PARTY_ABBREV"]],
                            race["Jurisdiction"]["FIPS"]
                            if "FIPS" in race["Jurisdiction"] and label in ("house", "senate")
                            else counties[("18", re.sub(r"[^a-z]", "", region["MAP_JURISDICTION_NAME"].lower()))],
                            re.sub(r"\s+", " ", entry["NAME_ON_BALLOT"]).strip(),
                            int(entry["TOTAL_VOTES"]),
                        )
                    )
        if sums != totals:
            raise ValueError(f"Indiana {label}: the county rows do not match the statewide summary")
    frames.append(
        pl.DataFrame(
            hoosier,
            schema={
                "day": pl.Date,
                "office": pl.String,
                "code": pl.String,
                "party": pl.String,
                "fips": pl.String,
                "choice": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        ).with_columns(
            state=pl.lit("IN"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    pa: list[tuple[date, str, str | None, str, str, str, int]] = []
    for day, election in PA_ELECTIONS.items():
        for office, label in PA_OFFICES.items():
            listing = json.loads(raw.cached(SOURCE, PA_OFFICE.format(office, election)).path.read_text())
            for blocks in (
                json.loads(
                    re.sub(
                        r'(?<="CandidateName":")(.*?)(?=","(?:Votes|Percentage)":)',
                        lambda found: found[0].replace('"', '\\"'),
                        listing,
                    )
                )["Election"].values()
                if listing
                else ()
            ):
                for block in blocks:
                    for heading, items in block.items():
                        totals: dict[tuple[str, str], int] = {}
                        for item in items:
                            for side in item["Candidates"]:
                                for party, entries in side.items():
                                    for entry in entries:
                                        key = (
                                            party,
                                            re.sub(r"\s+", " ", entry["CandidateName"].replace('"', "")).strip(),
                                        )
                                        if key in totals:
                                            raise ValueError(f"{day} PA {label} {heading}: {key[1]} appears twice")
                                        totals[key] = int(entry["Votes"])
                        district = heading.split("$$")[1]
                        sums: dict[tuple[str, str], int] = {}
                        breaks = json.loads(
                            re.sub(
                                r'(?<="CandidateName":")(.*?)(?=","(?:Votes|Percentage)":)',
                                lambda found: found[0].replace('"', '\\"'),
                                json.loads(
                                    raw.cached(SOURCE, PA_COUNTY.format(office, district, election)).path.read_text()
                                ),
                            )
                        )
                        for chunks in breaks["Election"].values():
                            for chunk in chunks:
                                for county, bands in chunk.items():
                                    for band in bands:
                                        for party, entries in band.items():
                                            for entry in entries:
                                                person = re.sub(
                                                    r"\s*\((?:DEM|REP)\)$",
                                                    "",
                                                    re.sub(
                                                        r"\s+", " ", entry["CandidateName"].replace('"', "")
                                                    ).strip(),
                                                )
                                                sums[(party, person)] = sums.get((party, person), 0) + int(
                                                    entry["Votes"]
                                                )
                                                parts = [part.strip() for part in person.split(",")]
                                                tail = [
                                                    word
                                                    for word in " ".join(parts[1:]).replace(".", "").split()
                                                    if word in ("JR", "SR", "II", "III", "IV")
                                                ]
                                                given = [
                                                    word
                                                    for word in parts[-1].split()
                                                    if word.rstrip(".") not in ("JR", "SR", "II", "III", "IV")
                                                ]
                                                pa.append(
                                                    (
                                                        day,
                                                        label
                                                        if label
                                                        in ("house", "senate", "congress", "president", "us-senate")
                                                        else OFFICES[label][0],
                                                        str(int(re.findall(r"\d+", items[0]["District"])[0]))
                                                        if label in ("house", "senate", "congress")
                                                        else None,
                                                        {"Democratic": "DEM", "Republican": "REP"}[party],
                                                        counties[("42", re.sub(r"[^a-z]", "", county.lower()))],
                                                        " ".join(
                                                            [
                                                                " ".join([*given, parts[0]]).title(),
                                                                *(
                                                                    word.title() if word in ("JR", "SR") else word
                                                                    for word in tail
                                                                ),
                                                            ]
                                                        ),
                                                        int(entry["Votes"]),
                                                    )
                                                )
                        if sums != totals:
                            raise ValueError(
                                f"{day} PA {label} {heading}: the county rows do not match the district total"
                            )
    pa_frame = pl.DataFrame(
        pa,
        schema={
            "day": pl.Date,
            "office": pl.String,
            "code": pl.String,
            "party": pl.String,
            "fips": pl.String,
            "choice": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    pa_federal = pa_frame.filter(pl.col("office").is_in(["congress", "president", "us-senate"]))
    congress = pl.concat(
        [
            congress,
            *(
                federal.select(
                    year=pl.col("day").dt.year(),
                    chamber=pl.col("office").replace({"congress": "house", "us-senate": "senate"})
                    + pl.lit(":")
                    + pl.col("party").replace_strict(
                        {code: word for code, (_, word, *_) in PARTIES.items()},
                        default=pl.col("party").str.to_lowercase(),
                    ),
                    day="day",
                    special=pl.lit(False),
                    state=pl.lit(state),
                    district=pl.col("code").cast(pl.Int64),
                    candidate="choice",
                    writein=pl.lit(False),
                    votes="votes",
                    measure=pl.lit(1, pl.Int16),
                    code="fips",
                    kind=pl.lit("county"),
                    source=pl.lit(state),
                )
                for state, federal in (
                    ("PA", pa_federal),
                    ("FL", fl_federal),
                    ("IA", ia_upper_frame),
                    (
                        "IL",
                        pl.DataFrame(
                            il_federal,
                            schema=["day", "office", "code", "party", "fips", "choice", "votes"],
                            orient="row",
                        ),
                    ),
                )
            ),
        ]
    )
    ma_frame = pl.concat(ma_federal).with_columns(
        code=pl.col("division_name").map_elements(
            lambda name: (
                towns.get(("MA", elstats.plain(name)))
                or towns.get(("MA", elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))))
            ),
            return_dtype=pl.String,
        )
    )
    if ma_frame["code"].is_null().any():
        raise ValueError(
            f"MA towns with no county: {ma_frame.filter(pl.col('code').is_null())['division_name'].unique().to_list()}"
        )
    congress = pl.concat(
        [
            congress,
            ma_frame.filter(~pl.col("candidate_name").is_in(["Blanks", "Total Votes Cast"]))
            .group_by("election_date", "office_id", "district_name", "primary_party", "candidate_name", "code")
            .agg(pl.col("votes").sum())
            .select(
                year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
                chamber=pl.col("office_id")
                .replace_strict({str(key): value for key, value in MA_FEDERAL.items()})
                .replace({"congress": "house", "us-senate": "senate"})
                + pl.lit(":")
                + pl.col("primary_party").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", "-"),
                day=pl.col("election_date").str.to_date(),
                special=pl.lit(False),
                state=pl.lit("MA"),
                district=pl.col("district_name").str.extract(r"^(\d+)").cast(pl.Int64),
                candidate=pl.col("candidate_name").str.to_uppercase(),
                writein=pl.col("candidate_name") == "All Others",
                votes="votes",
                measure=pl.lit(1, pl.Int16),
                code="code",
                kind=pl.lit("county"),
                source=pl.lit("MA"),
            ),
        ],
        how="vertical_relaxed",
    )
    congress.write_parquet(PARSED / "congress.parquet")
    frames.append(
        pa_frame.filter(~pl.col("office").is_in(["congress", "president", "us-senate"])).with_columns(
            state=pl.lit("PA"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    sc: list[tuple[date, str, str | None, str, str, str, int]] = []
    for day, ids in SC_FILES.items():
        tallied: dict[tuple[str, str | None, str], list[tuple[str, str, int]]] = {}
        for election in ids:
            site = SC_SITE.format(election)
            version = raw.cached(SOURCE, site + "current_ver.txt").path.read_text().strip()
            with zipfile.ZipFile(raw.cached(SOURCE, f"{site}{version}/reports/detailxml.zip").path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            seen: set[tuple[str, str | None, str]] = set()
            for race in root.iter("Contest"):
                title = re.sub(r"\s+", " ", race.get("text", "")).strip()
                if (match := SC_RACE.fullmatch(title)) is None:
                    continue
                key = (
                    ("house" if match[1].startswith("House") else "senate", str(int(match[2])), match[3])
                    if match[1]
                    else (OFFICES[match[4].upper().removeprefix("STATE ")][0], None, match[5])
                )
                if key in seen:
                    raise ValueError(f"{election} {title}: the contest appears twice")
                if election != ids[0] and key not in tallied:
                    raise ValueError(f"{election} {title}: a recount of a contest the first count lacks")
                seen.add(key)
                rows: list[tuple[str, str, int]] = []
                for choice in race.iter("Choice"):
                    counts: dict[str, int] = {}
                    for kind in choice.iter("VoteType"):
                        parts = [
                            (county.get("name", ""), int(county.get("votes", "0")))
                            for county in kind
                            if county.tag in ("County", "Precinct")
                        ]
                        if sum(value for _, value in parts) != int(kind.get("votes", "0")):
                            raise ValueError(f"{election} {title}: the county rows do not match the vote type total")
                        for county, value in parts:
                            counts[county] = counts.get(county, 0) + value
                    if sum(counts.values()) != int(choice.get("totalVotes", "0")):
                        raise ValueError(f"{election} {title}: the county rows do not match the candidate's total")
                    candidate = re.sub(r"\s*\(Withdrew\)$", "", re.sub(r"\s+", " ", choice.get("text", ""))).strip()
                    rows.extend((county, candidate, value) for county, value in counts.items())
                tallied[key] = rows
        sc.extend(
            (day, office, code, party, counties[("45", re.sub(r"[^a-z]", "", county.lower()))], candidate, value)
            for (office, code, party), rows in tallied.items()
            for county, candidate, value in rows
        )
    frames.append(
        pl.DataFrame(
            sc,
            schema={
                "day": pl.Date,
                "office": pl.String,
                "code": pl.String,
                "party": pl.String,
                "fips": pl.String,
                "choice": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        ).with_columns(
            state=pl.lit("SC"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    utah = []
    for day, (path, sheets) in UT_FILES.items():
        book = fastexcel.read_excel(raw.cached(ut_canvass.SOURCE, ut_canvass.BASE + path).path)
        for sheet in sheets:
            cells = [
                [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
                for row in book.load_sheet(sheet, header_row=None, dtypes="string").to_polars().iter_rows()
            ]
            first = next(index for index, row in enumerate(cells) if row[0].upper().startswith("BEAVER"))
            last = next(index for index, row in enumerate(cells) if row[0].upper().startswith("TOTAL"))
            heads = [
                {column: text for column, text in enumerate(row) if text and column > 0} for row in cells[: first - 1]
            ]
            for column, candidate in enumerate(cells[first - 1]):
                if column == 0 or not candidate or re.search(r"(?i)regist|ballots|voting|% of|percent", candidate):
                    continue
                label = " ".join(
                    row[max(start for start in row if start <= column)]
                    for row in heads
                    if any(start <= column for start in row)
                )
                found = UT_LEGISLATIVE.search(label)
                if found is None:
                    continue
                quoted = re.search(r'"(\w)"', candidate)
                party = next(
                    (code for word, code in UT_PARTIES.items() if word in label),
                    {"R": "REP", "D": "DEM", "L": "LIB", "C": "CST"}.get(quoted[1]) if quoted else None,
                )
                if party is None:
                    raise ValueError(f"{day} {sheet} {candidate}: no party in {label!r}")
                counts = {
                    counties[("49", re.sub(r"[^a-z]", "", row[0].lower().removesuffix(" county")))]: int(
                        float(row[column])
                    )
                    for row in cells[first:last]
                    if row[column]
                }
                if sum(counts.values()) != int(float(cells[last][column])) if cells[last][column] else len(counts) != 1:
                    raise ValueError(f"{day} {sheet} {candidate}: the counties do not add up to the total")
                utah.extend(
                    (
                        day,
                        found[1].lower(),
                        found[2].lstrip("0"),
                        party,
                        fips,
                        re.sub(r'\s*"\w"', "", candidate).strip(),
                        votes,
                    )
                    for fips, votes in counts.items()
                )
    for day, path in ut_primaries.FILES.items():
        export = json.loads(raw.cached(ut_primaries.SOURCE, ut_primaries.BASE + path).path.read_bytes())
        for item in export["results"]["ballotItems"]:
            found = UT_EXPORT.fullmatch(item["name"])
            if found is None:
                continue
            counted = {option["name"].strip(): 0 for option in item["ballotOptions"]}
            for county in export["localResults"]:
                for local in (local for local in county["ballotItems"] if local["name"] == item["name"]):
                    fips = counties[("49", re.sub(r"[^a-z]", "", county["name"].lower().removesuffix(" county")))]
                    for option in local["ballotOptions"]:
                        counted[option["name"].strip()] += option["voteCount"]
                        utah.append(
                            (
                                day,
                                found[2].lower(),
                                found[3],
                                UT_PARTIES[found[1]],
                                fips,
                                option["name"].strip(),
                                option["voteCount"],
                            )
                        )
            if counted != {option["name"].strip(): option["voteCount"] for option in item["ballotOptions"]}:
                raise ValueError(f"{day} {item['name']}: the counties do not add up to the total")
    frames.append(
        pl.DataFrame(
            utah,
            schema={
                "day": pl.Date,
                "office": pl.String,
                "code": pl.String,
                "party": pl.String,
                "fips": pl.String,
                "choice": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        ).with_columns(
            state=pl.lit("UT"),
            label="party",
            scheme=pl.lit("us.county"),
            unit="fips",
            writein=pl.lit(False),
            seats=pl.lit(1, pl.Int16),
        )
    )
    partisan = pl.col("day").dt.year() < 2012
    frames.append(
        ca.filter(~pl.col("office").is_in(["house", "us-senate"]))
        .with_columns(party=pl.when(partisan).then("party").otherwise(pl.lit("")), label="party")
        .filter(pl.col("party").is_in(list(PARTIES)))
        .select(
            state=pl.lit("CA"),
            day="day",
            office="office",
            code="code",
            party="party",
            label=pl.when(pl.col("label").is_in(list(PARTIES))).then("label"),
            fips="fips",
            scheme=pl.lit("us.county"),
            unit="fips",
            choice="choice",
            writein=pl.lit(False),
            votes="votes",
        )
    )
    (
        pl.concat(frames, how="diagonal_relaxed")
        .join(replaced, on=["state", *contest, "scheme"], how="anti", nulls_equal=True)
        .group_by("state", "day", "office", "code", "party", "fips", "scheme", "unit", "choice", "writein")
        .agg(
            pl.col("label").drop_nulls().first(),
            pl.col("votes").sum(),
            pl.col("seats").max(),
            pl.col("seat").first(),
            pl.col("withheld").max(),
        )
        .sort("state", "day", "office", "code", "party", "fips", "unit", "choice")
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative and statewide primaries and runoffs from state canvasses: "
            "North Carolina 2002–2022, Minnesota 2016–2022, Washington 2015–2022, and New York City 2014–2022 "
            "by precinct, California 2002–2022 by county, Colorado, Connecticut, Idaho, New Mexico, "
            "New York, Vermont, and Virginia 2000–2022 by county and Massachusetts 1994–2022 by town from their "
            "ElectionStats archives, and Maine "
            "2000–2024 by county from the Secretary of State's tabulations, Illinois 2000–2022 by county from the "
            "State Board of Elections' vote totals, Texas 2000–2014 by county from the Secretary of State's "
            "election history, Florida 2000–2022 by county from the Division of Elections' results extracts, "
            "Kentucky 2000–2008 by county from the State Board of Elections' county reports, South Carolina 2010–2022 by "
            "county from the State Election Commission's detail results, Pennsylvania 2000–2022 by county from the "
            "Department of State's election returns, Indiana 2020 by county from the Election Division's results "
            "archive, Alabama 2022 by county from the Secretary of State's precinct results, Iowa 2010–2016 by county "
            "from the Secretary of State's county precinct results, and Utah's legislative primaries of 2000, "
            "2010–2012, and 2016–2024 by county from the Lieutenant Governor's canvasses",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for state, (_, _, publisher, title, url) in STATES.items():
            source = sources[state] = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        for stamp, path, *_ in ME_FILES:
            cs.artifact(raw.cached(SOURCE, ME_WAYBACK.format(stamp, path)), sources["ME"])
        sources["NYC"] = cs.source(nyc_general.PUBLISHER, NYC_TITLE)
        if sources["NYC"] in cs.minted:
            cs.add(
                "source",
                id=sources["NYC"],
                kind="official_body",
                publisher=nyc_general.PUBLISHER,
                title=NYC_TITLE,
                url=NYC_SITE + "/page/election-results-summary",
                license="Public record",
            )
        for year in NYC_YEARS:
            cs.artifact(raw.cached(SOURCE, NYC_PAGE.format(year)), sources["NYC"])
        for stem in nyc_files().values():
            for suffix in ("EDLevel", "Recap"):
                cs.artifact(raw.cached(SOURCE, f"{stem}%20{suffix}.csv"), sources["NYC"])
        sources["NYSBOE"] = cs.source(STATES["NY"][2], NY_TITLE)
        if sources["NYSBOE"] in cs.minted:
            cs.add(
                "source",
                id=sources["NYSBOE"],
                kind="official_body",
                publisher=STATES["NY"][2],
                title=NY_TITLE,
                url="https://www.elections.ny.gov",
                license="Public record",
            )
        for stamp, link in NY_FEDERAL.values():
            cs.artifact(raw.cached(SOURCE, f"https://web.archive.org/web/{stamp}id_/{link}"), sources["NYSBOE"])
        for county, total in IL_FILES.values():
            for token in (county, total):
                cs.artifact(raw.cached(SOURCE, IL_DOWNLOAD.format(token)), sources["IL"])
        for day in NC_DAYS:
            cs.artifact(raw.cached(SOURCE, FILES.format(day=day)), sources["NC"])
        for year in KY_DAYS:
            page = raw.cached(SOURCE, KY_PAGE.format(year))
            cs.artifact(page, sources["KY"])
            for path in re.findall(
                rf'href="(/SiteCollectionDocuments/Election%20Results/2000-2009/{year}/[^"]*\.txt)"',
                page.path.read_text(errors="replace"),
                re.IGNORECASE,
            ):
                if re.search("pri", path, re.IGNORECASE) and not re.search(
                    r"reg%20stat|regstat|by%20cong|by%20candidate|by%20county|bycounty", path, re.IGNORECASE
                ):
                    cs.artifact(raw.cached(SOURCE, f"https://elect.ky.gov{path}"), sources["KY"])
        for day in FL_DAYS:
            extract = raw.cached(SOURCE, fl_sos.EXTRACT.format(day=day))
            cs.artifact(extract, sources["FL"])
            for race, party, district in {
                (row[3], row[1], row[7] if row[3] in ("STR", "STS", "USR") else "")
                for row in csv.reader(io.StringIO(extract.path.read_bytes().decode("cp1252")), delimiter="\t")
                if row and row[3] in FL_RACES
            }:
                cs.artifact(
                    raw.cached(SOURCE, FL_DETAIL.format(day=day, race=race, party=party, district=district)),
                    sources["FL"],
                )
        for election in tx_elchist.PRIMARIES:
            for race, title in tx_elchist.races(election):
                if TX_RACE.fullmatch(title):
                    cs.artifact(raw.cached(SOURCE, f"{tx_elchist.SITE}elchist{election}_race{race}.htm"), sources["TX"])
        for year, category, _ in IN_FILES:
            cs.artifact(raw.cached(SOURCE, IN_ARCHIVE.format(year, category, IN_VERSIONS[year])), sources["IN"])
        for url in AL_FILES.values():
            cs.artifact(raw.cached(SOURCE, url), sources["AL"])
        for url in iowa().values():
            cs.artifact(raw.cached(SOURCE, url), sources["IA"])
        for election in PA_ELECTIONS.values():
            for office in PA_OFFICES:
                listing = raw.cached(SOURCE, PA_OFFICE.format(office, election))
                text = json.loads(listing.path.read_text())
                if text:
                    cs.artifact(listing, sources["PA"])
                for blocks in json.loads(text)["Election"].values() if text else ():
                    for block in blocks:
                        for heading in block:
                            cs.artifact(
                                raw.cached(SOURCE, PA_COUNTY.format(office, heading.split("$$")[1], election)),
                                sources["PA"],
                            )
        for ids in SC_FILES.values():
            for election in ids:
                version = raw.cached(SOURCE, SC_SITE.format(election) + "current_ver.txt")
                cs.artifact(version, sources["SC"])
                cs.artifact(
                    raw.cached(
                        SOURCE,
                        SC_SITE.format(election) + version.path.read_text().strip() + "/reports/detailxml.zip",
                    ),
                    sources["SC"],
                )
        for day in MN_DAYS:
            for file in ("allracesbyprecinct", "PrctTbl"):
                cs.artifact(raw.cached(SOURCE, MN_FILES.format(day=day, name=file)), sources["MN"])
        for path, _ in UT_FILES.values():
            cs.artifact(raw.cached(ut_canvass.SOURCE, ut_canvass.BASE + path), sources["UT"])
        for path in ut_primaries.FILES.values():
            cs.artifact(raw.cached(ut_primaries.SOURCE, ut_primaries.BASE + path), sources["UT"])
        for day in WA_DAYS:
            for url in (wa_local.FILES[day], wa_local.PRECINCTS.format(day=day)):
                cs.artifact(raw.cached(wa_local.SOURCE, url), sources["WA"])
        held = set()
        for state, offices in ELSTATS.items():
            for office in offices | (MA_FEDERAL if state == "MA" else {}):
                for year in MA_YEARS if state == "MA" else ELSTATS_YEARS:
                    if state == "MA" and year not in (MA_PRESIDENT if office == 1 else MA_DAYS):
                        continue
                    artifact = raw.cached(SOURCE, search(state, office, year))
                    if artifact.storage_key not in held:
                        held.add(artifact.storage_key)
                        cs.artifact(artifact, sources[state])
                    if state == "MA":
                        listing = artifact.path.read_text()
                        empty = set(MA_EMPTY.findall(listing))
                        for election, _, _, stage in elstats.MA_ROW.findall(listing):
                            if election in empty or "Special" in stage:
                                continue
                            download = raw.cached(SOURCE, MA_DOWNLOAD.format(election))
                            if download.storage_key not in held:
                                held.add(download.storage_key)
                                cs.artifact(download, sources[state])
        for path, names in CA_DAYS.values():
            for file in names:
                cs.artifact(raw.cached(SOURCE, CA_SOV.format(path=path.format(file))), sources["CA"])
        polities = {state: cs.polity(f"US-{state}") for state in STATES}
        published = {
            value: row
            for value, *row in conn.execute(
                """select x.value, r.id, r.election_id, r.contest_id, r.stage, r.kind, r.source_id, r.completeness,
                          r.supersedes_id, r.status
                   from external_id x join result_set r on r.id = x.entity_id
                   where x.scheme = %s""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        }
        stale = {
            match[1]: row
            for value, row in published.items()
            if (match := re.fullmatch(r"(.+):parts(?::\d+)?", value)) and row[8] == "active"
        }
        earlier: dict[UUID, list[tuple[UUID, str, int | None, str | None]]] = {}
        for result_set, option, kind, seats, notes in conn.execute(
            """select o.result_set_id, o.option_id, o.kind, o.seats, o.notes from outcome o
               join external_id x on x.entity_id = o.result_set_id and x.scheme = %s""",
            [f"{SOURCE}.result_set"],
        ).fetchall():
            earlier.setdefault(result_set, []).append((option, kind, seats, notes))
        named: dict[UUID, list[tuple[UUID, UUID | None, str | None]]] = {}
        for contest, option, party, label in conn.execute(
            """select o.contest_id, o.id, o.party_id, o.ballot_label from contest_option o
               join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
               join changeset g on g.id = o.changeset_id
               where x.value ~ '^20[0-9-]+:NY:' and g.pipeline = %s and exists (
                 select 1 from candidacy d where d.option_id = o.id)""",
            [SOURCE],
        ).fetchall():
            named.setdefault(contest, []).append((option, party, label))
        methods: dict[UUID, str] = dict(
            conn.execute(
                """select n.entity_id, n.body_md from note n
                   join external_id x on x.entity_id = n.entity_id and x.scheme = %s
                   where n.kind = 'methodology'""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        )
        yielded = {
            result_set
            for (result_set,) in conn.execute(
                """select r.id from result_set r join changeset g on g.id = r.changeset_id
                   where r.status = 'superseded' and g.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        keys = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e join contest c on c.id = e.entity_id
                   join changeset h on h.id = c.changeset_id
                   where e.scheme = 'us.contest' and e.value ~ %s and h.pipeline <> %s""",
                [f"^20[0-9-]+:({'|'.join(STATES)}):", SOURCE],
            ).fetchall()
        }
        slugs: dict[str, UUID] = dict(
            conn.execute(
                "select path, entity_id from slug where path ~ %s", [f"^us/({'|'.join(STATES).lower()})/"]
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code ~ %s",
                [f"^({'|'.join(fips for fips, *_ in STATES.values())})"],
            ).fetchall()
        }
        nominees: dict[tuple[str, int, str, str | None, UUID, str, str], set[UUID]] = {}
        for value, ballot, person, party in conn.execute(
            """select e.value, coalesce(d.ballot_name, n.text, o.ballot_label), d.person_id, o.party_id
               from external_id e
               join contest_option o on o.contest_id = e.entity_id
               join candidacy d on d.option_id = o.id and d.role = 'head'
               left join name n on n.entity_id = d.person_id and n.is_preferred and n.lang = 'en'
               where e.scheme = 'us.contest'
                 and e.value ~ %s and o.party_id is not null""",
            [f"^20[0-2][0-9]-11-[0-9]{{2}}:({'|'.join(STATES)}):[a-z-]+(:[a-z0-9-]+(:[0-9])?:g)?$"],
        ).fetchall():
            parts = value.split(":")
            words = [
                word
                for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", ballot).lower().split()
                if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
            ]
            if words:
                signature = (
                    parts[1],
                    int(parts[0][:4]),
                    parts[2],
                    (parts[3] + (f":{parts[4]}" if len(parts) == 6 else "")) if len(parts) > 3 else None,
                    party,
                    words[0][0],
                    slug(words[-1]),
                )
                nominees.setdefault(signature, set()).add(person)
        parties = {code: cs.key("us.party", key) for code, (key, *_) in PARTIES.items() if key}
        systems = {
            key: cs.key("us.electoral_system", key)
            for key in ("fptp", "top_two", "two_round", "two_round_40", "two_round_30", "block", "irv")
        }
        offices = {
            (state, office): cs.key("us.office", f"{office}:{state}")
            for state in STATES
            for office in {office for office, _ in OFFICES.values()}
            if not lines.filter(
                pl.col("state") == state, pl.col("office").str.split(":").list.first() == office
            ).is_empty()
        }
        chambers: dict[str, str] = dict(
            conn.execute(
                """select e.value, n.text from external_id e
                   join name n on n.entity_id = e.entity_id and n.is_preferred and n.lang = 'en'
                   where e.scheme = 'us.body' and e.value ~ %s""",
                [f"^({'|'.join(STATES)}):(house|senate|assembly|house-of-delegates)$"],
            ).fetchall()
        )
        bodies = {
            (state, office): cs.key("us.body", f"{state}:{office}")
            for state, office in lines.filter(pl.col("code").is_not_null())
            .select("state", "office")
            .unique()
            .iter_rows()
        }
        lieutenants = {state: offices.get((state, "lieutenant-governor")) for state in ("IL", "PA")}
        for state, lieutenant in lieutenants.items():
            if lieutenant is not None and lieutenant in cs.minted:
                cs.add("office", id=lieutenant, polity_id=cs.polity(f"US-{state}"), kind="other")
                name(lieutenant, f"Lieutenant Governor of {STATES[state][1]}")
        if cs.minted & {*parties.values(), *systems.values(), *offices.values(), *bodies.values()} - {
            *lieutenants.values()
        }:
            raise ValueError("the parties, systems, offices, and bodies must already exist")
        titles = dict(OFFICES.values())
        elections: dict[tuple[str, date], UUID] = {}
        for state, day, first in [
            *(("NC", day, first) for day, first in NC_DAYS.items()),
            *(("MN", day, None) for day in MN_DAYS),
            *(("WA", day, None) for day in WA_DAYS),
            *(("CA", day, None) for day in CA_DAYS),
            *(("ME", day, None) for day in ME_DAYS.values()),
            *(("IL", day, None) for day in IL_DAYS.values()),
            *(("TX", day, first) for day, first in TX_DAYS.items()),
            *(("SC", day, first) for day, first in SC_DAYS.items()),
            *(("PA", day, None) for day in PA_ELECTIONS),
            *(("IN", day, None) for day in IN_DAYS.values()),
            *(("AL", day, first) for day, first in AL_DAYS.items()),
            *(("IA", day, None) for day in IA_DAYS.values()),
            *(("FL", day, first) for day, first in FL_DAYS.items()),
            *(("KY", day, None) for day in KY_DAYS.values()),
            *(("UT", day, None) for day in [*UT_FILES, *ut_primaries.FILES]),
            *(
                (state, day, None)
                for state, day in lines.filter(pl.col("state").is_in(list(ELSTATS)))
                .select("state", "day")
                .unique()
                .sort("state", "day")
                .iter_rows()
            ),
        ]:
            kind = "runoff" if first else "primary"
            election = elections[(state, day)] = cs.key("us.election", f"{day}:{state}:{kind}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polities[state],
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    election,
                    f"{day:%B} {day.day}, {day.year} {STATES[state][1]} primary {'runoff ' if first else ''}election",
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=election)
        rounds = {"NC": NC_DAYS, "TX": TX_DAYS, "FL": FL_DAYS, "SC": SC_DAYS, "AL": AL_DAYS}
        later = {(state, first, day) for state, days in rounds.items() for day, first in days.items() if first}
        contests = {
            group: rows.sort(["votes", "choice"], descending=[True, False])
            for group, rows in lines.group_by("state", "day", "office", "code", "party", "choice", "writein")
            .agg(
                pl.col("label").drop_nulls().first(),
                pl.col("votes").sum(),
                pl.col("seats").max(),
                pl.col("seat").first(),
                pl.col("withheld").max(),
            )
            .group_by("state", "day", "office", "code", "party")
        }
        held = {key for key in contests if key[0] in rounds}
        parts = {
            tuple(row)
            for row in lines.filter(
                pl.col("code").is_not_null(), pl.col("scheme").is_in(["us.county", "us.aggregate", "us.town"])
            )
            .select("state", "day", "office", "code", "party")
            .unique()
            .iter_rows()
        }
        persons: dict[str, UUID] = {}
        options: dict[tuple[str, date, str, str | None, str, str], UUID] = {}
        sets: dict[tuple[str, date, str, str | None, str], UUID] = {}
        made: dict[tuple[str, date, str, str | None, str], UUID] = {}
        bases: set[str] = set()
        skipped = 0
        for (state, day, office, code, party), rows in sorted(
            contests.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            assert isinstance(state, str) and isinstance(day, date) and isinstance(office, str)
            assert isinstance(party, str) and (code is None or isinstance(code, str))
            first = rounds.get(state, {}).get(day)
            fips, place = STATES[state][0], STATES[state][1]
            _, word, etype, label = PARTIES[party]
            if code is not None:
                base = f"{day}:{state}:{office}:{code}:{etype}" + ("runoff" if first else "")
                taken = re.compile(rf"{re.escape(base)}(f\w*)?")
            else:
                base = f"{day}:{state}:{office}" + (f":{word}" if word else "")
                taken = re.compile(re.escape(base))
            if any(taken.fullmatch(value) for value in keys):
                skipped += 1
                continue
            bases.add(base)
            empty = bool(rows.filter(~pl.col("writein")).height) and rows["votes"].sum() == 0
            old = published.get(f"{base}:precinct")
            if empty and old is None:
                continue
            contest = made[(state, day, office, code, party)] = cs.key("us.contest", base)
            previous = None
            if first is not None:
                previous = made.get((state, first, office, code, party))
                if previous is None:
                    prior = (
                        f"{first}:{state}:{office}:{code}:{etype}"
                        if code is not None
                        else f"{first}:{state}:{office}:{word}"
                    )
                    previous = next(
                        (
                            cs.key("us.contest", value)
                            for value in sorted(keys)
                            if re.fullmatch(rf"{re.escape(prior)}(f\w*)?", value)
                        ),
                        None,
                    )
            kind = "runoff" if first else "primary"
            stage = f"{'nonpartisan' if state == 'NC' and party == '' else label} primary" + (
                " runoff" if first else ""
            )
            magnitude: int = rows.select(pl.col("seats").fill_null(1).max()).item()
            district, _, post = (code or "").partition(":")
            remainder = (state == "WA" and day.year % 2 == 1) or (
                state == "IL" and office == "comptroller" and day.year == 2016
            )
            cs.add(
                "contest",
                id=contest,
                election_id=elections[(state, day)],
                kind="office" if code is None else "body_seats",
                office_id=offices[(state, office.split(":")[0])] if code is None else None,
                body_id=bodies[(state, office)] if code is not None else None,
                district_area_id=cs.area("us.judicial_district", f"IL:judicial-{office.split(':')[1]}")
                if state == "IL" and ":" in office
                else cs.area("us.state", fips)
                if code is None
                else cs.area(
                    "us.sldl" if office in ("house", "assembly", "house-of-delegates") else "us.sldu",
                    f"{state}:{district}",
                ),
                magnitude=magnitude,
                term="remainder" if remainder else "full",
                round_number=2 if first else 1,
                previous_round_id=previous,
                electoral_system_id=systems[
                    "top_two"
                    if party == ""
                    else "block"
                    if magnitude > 1
                    else "irv"
                    if state == "ME" and day.year >= 2018
                    else "fptp"
                    if state in ("MN", "CA", "ME", "IL", "KY", "PA", "IN", "IA", "UT", *ELSTATS)
                    else "two_round"
                    if state in ("TX", "SC", "AL") or (state == "FL" and day.year == 2000)
                    else "fptp"
                    if state == "FL"
                    else "two_round_40"
                    if day.year < 2018
                    else "two_round_30"
                ],
                default_result_kind="counted",
                party_scope_id=parties.get(party),
            )
            if code is None:
                seat = office.partition(":")[2]
                name(
                    contest,
                    f"{day.year} {place} "
                    + (
                        f"{'Supreme Court Justice' if office.startswith('justice') else 'Appellate Court Judge'}, "
                        f"{('First', 'Second', 'Third', 'Fourth', 'Fifth')[int(seat[0]) - 1]} District "
                        f"({rows['seat'][0]} vacancy),"
                        if state == "IL" and seat
                        else titles[office.split(":")[0]]
                    )
                    + (
                        ""
                        if state == "IL"
                        else f", Seat {seat.removeprefix('seat-')},"
                        if seat.startswith("seat-")
                        else f" ({seat.title()} seat)"
                        if seat
                        else ""
                    )
                    + f" {stage}",
                )
                path = f"us/{state.lower()}/{day}-{kind}/{office.replace(':', '-')}" + (f"-{word}" if word else "")
            else:
                name(
                    contest,
                    f"{day.year} {chambers[f'{state}:{office}']} {stage}, District {rows['seat'][0] or district.replace('-', '').upper()}"
                    + (f", Position {post}" if post else "")
                    + (" (unexpired term)" if remainder else ""),
                )
                seat = rows["seat"][0] or ""
                path = (
                    f"us/{state.lower()}/{day}-{kind}/{'state-' if office in ('house', 'senate') else ''}{office}-"
                    + (
                        re.sub(
                            r"(?<=[a-z])(?=barnstable|berkshire|bristol|dukes|essex|franklin|hampden|hampshire|middlesex|nantucket|norfolk|plymouth|suffolk|worcester)",
                            "-",
                            district,
                        )
                        if state == "MA"
                        else slug(seat)
                        if re.sub(r"[^a-z0-9]", "", seat.lower()) == district.replace("-", "")
                        else district
                    )
                    + (f"-post-{post}" if post else "")
                    + (f"-{word}" if word else "")
                )
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            parted = (state, day, office, code, party) in parts
            if old is not None and (empty or parted):
                cs.add(
                    "result_set",
                    id=old[0],
                    election_id=old[1],
                    contest_id=old[2],
                    stage=old[3],
                    kind=old[4],
                    status="withdrawn" if empty else "superseded",
                    source_id=old[5],
                    is_default=False,
                    completeness=old[6],
                    supersedes_id=old[7],
                )
                for option, outcome, seats, notes in earlier.get(old[0], []):
                    cs.add("outcome", result_set_id=old[0], option_id=option, kind=outcome, seats=seats, notes=notes)
                if empty:
                    cs.add("note", entity_id=old[0], kind="caveat", body_md=EMPTY)
            threshold = 50 if state in ("TX", "FL", "SC", "AL") else 40 if day.year < 2018 else 30
            total = rows["votes"].sum()
            ranked = rows.filter(~pl.col("writein"))
            nominated: dict[str, bool] = {}
            shared = [
                (words[0][0], slug(words[-1]))
                for words in (
                    [
                        word
                        for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", text).lower().split()
                        if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    for text in (
                        re.sub(r" and .+", "", choice) if state == "MN" and office == "governor" else choice
                        for choice in rows["choice"]
                    )
                )
                if words
            ]
            for choice, writein, preference in rows.select("choice", "writein", "label").iter_rows():
                if writein:
                    option = cs.key("us.option", f"{base}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    options[(state, day, office, code, party, choice)] = option
                    continue
                ticket = re.fullmatch(r"(.+?) and (.+)", choice) if state == "MN" and office == "governor" else None
                names = [ticket[1], ticket[2]] if ticket else [choice]
                people = []
                for role, text in zip(("head", "running_mate"), names, strict=False):
                    words = [
                        word
                        for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", text).lower().split()
                        if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    found = (
                        nominees.get(
                            (state, day.year, office, code, parties.get(preference), words[0][0], slug(words[-1])),
                            set(),
                        )
                        if role == "head"
                        and preference in parties
                        and shared.count((words[0][0], slug(words[-1]))) == 1
                        else set()
                    )
                    local = f"{state}:{office if role == 'head' else 'lieutenant-governor'}:{code or ''}:{party}:{slug(text)}"
                    if len(found) == 1:
                        person = next(iter(found))
                    else:
                        person = persons.get(local) or cs.key(f"{SOURCE}.person", local)
                        if person in cs.minted and local not in persons:
                            cs.add("person", id=person)
                            name(person, initials(text))
                    persons[local] = person
                    if role == "head":
                        nominated[choice] = len(found) == 1
                    people.append((role, text, person))
                option = cs.key("us.option", f"{base}:{people[0][2]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="ticket" if ticket else "candidate",
                    party_id=parties.get(preference),
                    ballot_label=initials(choice),
                )
                for role, text, person in people:
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{base}:{person}:{role}"),
                        person_id=person,
                        option_id=option,
                        role=role,
                        ballot_name=initials(text),
                        is_independent=False,
                    )
                options[(state, day, office, code, party, choice)] = option
            if state == "NY" and not parted:
                built = {options[(state, day, office, code, party, choice)] for choice in rows["choice"]}
                for option, party_id, label in named.get(contest, []):
                    if option not in built:
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="write_in",
                            party_id=party_id,
                            ballot_label=label,
                        )
            if empty:
                continue
            tallies = ranked["votes"].to_list()
            names = ranked["choice"].to_list()
            wins: list[tuple[str, str, str]] = []
            if party == "":
                if len(tallies) <= 2 or (tallies[1] - tallies[2]) * 100 > total:
                    wins = [(choice, "advanced", ADVANCED_TOP) for choice in names[:2]]
                elif (tallies[0] - tallies[2]) * 100 > total:
                    wins = [(names[0], "advanced", ADVANCED_TOP)]
            elif state == "ME" and day.year >= 2018 and len(tallies) > 2:
                if tallies[0] * 2 > sum(tallies) and (tallies[0] - tallies[1]) * 100 > total:
                    wins = [(names[0], "won", WON_MAJORITY)]
                elif tallies[0] > tallies[1] and nominated.get(names[0]) and day.year < 2024:
                    wins = [(names[0], "won", WON_NOMINEE)]
            elif state == "IA":
                if len(tallies) == 1 and tallies[0] * 100 >= 35 * total:
                    wins = [(names[0], "won", WON_ALONE)]
                elif len(tallies) > 1 and tallies[0] * 100 >= 35 * total:
                    if (tallies[0] - tallies[1]) * 100 > total:
                        wins = [(names[0], "won", WON_CONVENTION)]
                    elif tallies[0] > tallies[1] and nominated.get(names[0]):
                        wins = [(names[0], "won", WON_NOMINEE)]
            elif state in ("MN", "CA", "ME", "IL", "KY", "PA", "IN", "UT", *ELSTATS) or (
                state == "FL" and day.year > 2000
            ):
                if len(tallies) <= magnitude:
                    wins = [(choice, "won", WON_ALONE) for choice in names]
                elif (tallies[magnitude - 1] - tallies[magnitude]) * 100 > total and tallies[magnitude - 1] - tallies[
                    magnitude
                ] > (rows["withheld"].max() or 0):
                    wins = [(choice, "won", WON_LEAD) for choice in names[:magnitude]]
                elif (
                    tallies[magnitude - 1] > tallies[magnitude]
                    and all(nominated.get(choice) for choice in names[:magnitude])
                    and (magnitude == 1 or not any(nominated.get(choice) for choice in names[magnitude:]))
                ):
                    wins = [(choice, "won", WON_NOMINEE) for choice in names[:magnitude]]
            elif tallies:
                runoff = state in rounds and (
                    any(
                        (state, day, later_day) in later and (state, later_day, office, code, party) in held
                        for later_day in rounds[state]
                    )
                    or any(
                        re.fullmatch(
                            rf"{later_day}:{state}:{office}:"
                            + (f"{code}:{etype}runoff" if code is not None else str(word)),
                            value,
                        )
                        for later_day in rounds[state]
                        if (state, day, later_day) in later
                        for value in keys
                    )
                )
                clear = len(tallies) == 1 or (tallies[0] - tallies[1]) * 100 > total
                if first is None and runoff and len(tallies) > 1:
                    if len(tallies) == 2 or (tallies[1] - tallies[2]) * 100 > total:
                        wins = [(choice, "advanced", ADVANCED_RUNOFF) for choice in names[:2]]
                    elif (tallies[0] - tallies[2]) * 100 > total:
                        wins = [(names[0], "advanced", ADVANCED_RUNOFF)]
                elif len(tallies) == 1:
                    wins = [(names[0], "won", WON_ALONE)]
                elif clear and first is not None:
                    wins = [(names[0], "won", WON_LEAD)]
                elif clear and tallies[0] * 100 > threshold * total:
                    wins = [(names[0], "won", WON_THRESHOLD.format(threshold=threshold))]
                elif tallies[0] > tallies[1] and nominated.get(names[0]):
                    wins = [(names[0], "won", WON_NOMINEE)]
            if base in WITHDREW:
                wins += [(choice, "withdrew", WITHDREW[base][1]) for choice in names if choice in WITHDREW[base][0]]
            if base in DECLARED:
                wins = DECLARED[base]
            wanted = {
                (options[(state, day, office, code, party, choice)], kind, None, notes) for choice, kind, notes in wins
            }
            if state == "NY" and not parted and ranked.is_empty() and base not in UNDECLARED:
                revision = 1
                while f"{base}:parts:{revision + 1}" in published:
                    revision += 1
                prior = published.get(f"{base}:parts" + (f":{revision}" if revision > 1 else ""))
                wanted = {
                    (option, kind, None, NY_WRITEIN)
                    for option, kind, _, _ in (earlier.get(prior[0], []) if prior is not None else [])
                }
            grain = "parts" if parted else "precinct"
            revision = 1
            while f"{base}:{grain}:{revision + 1}" in published:
                revision += 1
            latest = f"{base}:{grain}" + (f":{revision}" if revision > 1 else "")
            current = published.get(latest)
            forced = None
            if state == "CT" and day.year >= 2022:
                if f"{base}:regions" in published:
                    latest = f"{base}:regions"
                    current = published[latest]
                else:
                    forced = f"{base}:regions"
            supersedes = old[0] if parted and old is not None else None
            replaced = None if parted else stale.get(base)
            if replaced is not None:
                latest = f"{base}:{grain}" if current is None else f"{base}:{grain}:{revision + 1}"
                supersedes = replaced[0]
                cs.add(
                    "result_set",
                    id=replaced[0],
                    election_id=replaced[1],
                    contest_id=replaced[2],
                    stage=replaced[3],
                    kind=replaced[4],
                    status="superseded",
                    source_id=replaced[5],
                    is_default=False,
                    completeness=replaced[6],
                    supersedes_id=replaced[7],
                )
                for option, outcome, seats, notes in earlier.get(replaced[0], []):
                    cs.add(
                        "outcome", result_set_id=replaced[0], option_id=option, kind=outcome, seats=seats, notes=notes
                    )
            elif current is not None and forced is None and set(earlier.get(current[0], [])) == wanted:
                supersedes = current[7]
            elif current is not None:
                latest = forced or f"{base}:{grain}:{revision + 1}"
                supersedes = current[0]
                cs.add(
                    "result_set",
                    id=current[0],
                    election_id=current[1],
                    contest_id=current[2],
                    stage=current[3],
                    kind=current[4],
                    status="superseded",
                    source_id=current[5],
                    is_default=False,
                    completeness=current[6],
                    supersedes_id=current[7],
                )
                for option, outcome, seats, notes in earlier.get(current[0], []):
                    cs.add(
                        "outcome", result_set_id=current[0], option_id=option, kind=outcome, seats=seats, notes=notes
                    )
            result_set = sets[(state, day, office, code, party)] = cs.key(f"{SOURCE}.result_set", latest)
            ed_level = state == "NY" and not parted
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[(state, day)],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="superseded" if result_set in yielded else "active",
                source_id=sources["NYC" if ed_level else state],
                is_default=result_set not in yielded,
                completeness=1,
                supersedes_id=supersedes,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=methods.get(result_set)
                or (
                    NYC_METHOD
                    if ed_level
                    else WA_METHOD
                    if state == "WA"
                    else CA_METHOD.format(
                        rule="The top two candidates advance to the general election."
                        if party == ""
                        else "The file does not mark winners, so the winner shown is the candidate with the most votes."
                    )
                    if state == "CA"
                    else MN_METHOD
                    if state == "MN"
                    else ME_METHOD
                    if state == "ME"
                    else IL_METHOD
                    if state == "IL"
                    else TX_METHOD
                    if state == "TX"
                    else KY_METHOD
                    if state == "KY"
                    else SC_METHOD
                    if state == "SC"
                    else PA_METHOD
                    if state == "PA"
                    else IN_METHOD
                    if state == "IN"
                    else AL_METHOD
                    if state == "AL"
                    else IA_METHOD
                    if state == "IA"
                    else FL_METHOD.format(
                        rule=", with more than half the votes, or the leader of the second primary; when a second "
                        "primary followed, the top two are shown as advancing"
                        if day.year == 2000
                        else ""
                    )
                    if state == "FL"
                    else ELSTATS_METHOD.format(publisher=STATES[state][2])
                    if state in ELSTATS
                    else JUDICIAL_METHOD
                    if party == ""
                    else METHOD.format(threshold=threshold)
                )
                + " "
                + RECOUNT,
            )
            if rows["withheld"].max():
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=WITHHELD.format(ballots=rows["withheld"].max()),
                )
            if base in UNDECLARED:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNDECLARED[base])
            for option, kind, _, notes in sorted(wanted):
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, notes=notes)
        congress = pl.read_parquet(PARSED / "congress.parquet")
        city = congress.filter(pl.col("source") == "NYC")
        bases |= {
            f"{day}:NY:house:{district}:{chamber.removeprefix('house:')}"
            for day, district, chamber in city.select("day", "district", "chamber").unique().iter_rows()
        }
        for value, row in published.items():
            match = re.fullmatch(r"(.+):(?:precinct|parts)(?::\d+)?", value)
            if match is not None and match[1] not in bases and row[8] == "active":
                cs.add(
                    "result_set",
                    id=row[0],
                    election_id=row[1],
                    contest_id=row[2],
                    stage=row[3],
                    kind=row[4],
                    status="withdrawn",
                    source_id=row[5],
                    is_default=False,
                    completeness=row[6],
                    supersedes_id=row[7],
                )
                for option, outcome, seats, notes in earlier.get(row[0], []):
                    cs.add("outcome", result_set_id=row[0], option_id=option, kind=outcome, seats=seats, notes=notes)
                cs.add("note", entity_id=row[0], kind="caveat", body_md=EMPTY)
        print(f"{len(made)} contests, {skipped} already held")
        retowned = {
            str(result_set)
            for (result_set,) in conn.execute(
                """select r.id from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest' and x.value like '%%:MA:%%'
                   join changeset g on g.id = r.changeset_id and g.pipeline = %s
                   where r.status = 'active' and exists (
                     select from result_value v join reporting_unit u on u.k = v.unit_k
                     where v.result_set_k = r.k and u.scheme_code <> 'us.town')""",
                [SOURCE],
            ).fetchall()
        } | {
            str(id)
            for id in cs.remainders("A blank-ballot line in Iowa's primary results read as a candidate", r"^blank$")
        }
        frame = lines.join(
            pl.DataFrame(
                [
                    (state, day, office, code, party, choice, str(option), str(sets[(state, day, office, code, party)]))
                    for (state, day, office, code, party, choice), option in options.items()
                    if (state, day, office, code, party) in sets
                ],
                schema={
                    "state": pl.String,
                    "day": pl.Date,
                    "office": pl.String,
                    "code": pl.String,
                    "party": pl.String,
                    "choice": pl.String,
                    "option_id": pl.String,
                    "result_set_id": pl.String,
                },
                orient="row",
            ),
            on=["state", "day", "office", "code", "party", "choice"],
            nulls_equal=True,
        ).filter(
            pl.col("result_set_id").is_in([str(value) for value in sets.values() if value in cs.minted])
            | pl.col("result_set_id").is_in(list(retowned))
        )
        part = pl.col("code").is_not_null() & pl.col("scheme").is_in(["us.county", "us.aggregate"])
        frame = frame.with_columns(
            flags=pl.when(pl.col("scheme") == "us.aggregate").then(UNALLOCATED).otherwise(0).cast(pl.Int16),
            unit=pl.when(part)
            .then(pl.concat_str("unit", pl.col("day").cast(pl.String), "office", "code", separator=":"))
            .otherwise("unit"),
            scheme=pl.when(part & (pl.col("scheme") == "us.county")).then(pl.lit("us.county_part")).otherwise("scheme"),
            part=part,
        )
        with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
            places = dict(
                pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False)
                .filter(pl.col("USPS") == "MA")
                .select("GEOID", pl.col("NAME").str.replace(r" (city|town)$", ""))
                .iter_rows()
            )
        units: dict[str, UUID] = {}
        reused = 0
        for state, day, fips, scheme, unit, flags, office, code, parted in (
            frame.select("state", "day", "fips", "scheme", "unit", "flags", "office", "code", "part")
            .unique()
            .iter_rows()
        ):
            if unit in units:
                continue
            units[unit] = counties[fips][0] if scheme == "us.county" else cs.unit(scheme, unit)
            if parted:
                cs.add(
                    "reporting_unit_area",
                    unit_id=units[unit],
                    area_id=cs.area(
                        "us.sldl" if office in ("house", "assembly", "house-of-delegates") else "us.sldu",
                        f"{state}:{code.partition(':')[0]}",
                    ),
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
            if units[unit] not in cs.minted:
                reused += 1
                continue
            cs.add(
                "reporting_unit",
                id=units[unit],
                polity_id=polities[state],
                scheme_code=scheme,
                code=unit,
                parent_id=counties[fips][0] if fips else None,
                area_relation="within" if fips else "unlocated",
                area_id=counties[fips][1] if fips else None,
                kind="aggregate" if flags else "standard",
                valid_from=None if flags and not parted or scheme == "us.town" else day,
                valid_to=None if flags and not parted or scheme == "us.town" else day + timedelta(days=1),
            )
            if scheme == "us.town":
                name(units[unit], places[unit])
        print(f"{len(units)} units, {reused} already held")
        precincts = (
            lines.filter(pl.col("scheme") == "us.precinct", pl.col("code").is_not_null())
            .join(
                pl.DataFrame(
                    list(sets),
                    schema={
                        "state": pl.String,
                        "day": pl.Date,
                        "office": pl.String,
                        "code": pl.String,
                        "party": pl.String,
                    },
                    orient="row",
                ),
                on=["state", "day", "office", "code", "party"],
                how="semi",
            )
            .select("state", "day", "office", "unit", district=pl.col("code").str.split(":").list.first())
            .unique()
        )
        split = (
            precincts.group_by("state", "day", "office", "unit")
            .agg(pl.col("district").n_unique())
            .filter(pl.col("district") > 1)
            .join(precincts, on=["state", "day", "office", "unit"])
            .select("state", "day", "office", district="district_right")
            .unique()
        )
        member = precincts.join(split, on=["state", "day", "office", "district"], how="anti")
        print(f"{member.height} precinct memberships, {split.height} districts with a split precinct left out")
        for state, day, office, unit, district in member.iter_rows():
            cs.add(
                "reporting_unit_area",
                unit_id=units.get(unit) or cs.unit("us.precinct", unit),
                area_id=cs.area(
                    "us.sldl" if office in ("house", "assembly", "house-of-delegates") else "us.sldu",
                    f"{state}:{district}",
                ),
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        cs.frame(
            "result_value",
            frame.with_columns(unit_id=pl.col("unit").replace_strict({key: str(value) for key, value in units.items()}))
            .group_by("result_set_id", "unit_id", "option_id", "flags")
            .agg(value=pl.col("votes").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
            ),
        )
        held = [
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[0-2][0-9]-0[2-6]-[0-9]{2}:CA:(house|senate)'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id
                                   and s.publisher not in ('Federal Election Commission', %s))""",
                [STATES["CA"][2]],
            ).fetchall()
        ]
        medsl.attach(
            cs,
            conn,
            congress.filter(
                pl.col("state") == "CA",
                ~pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit("CA"),
                    pl.when(pl.col("district").is_not_null())
                    .then(
                        pl.concat_str(
                            pl.lit("house:"),
                            pl.col("district").cast(pl.String),
                            pl.col("chamber").str.strip_prefix("house"),
                        )
                    )
                    .otherwise("chamber"),
                    separator=":",
                ).is_in(held),
            ),
            sources,
        )
        medsl.attach(cs, conn, city, sources, grain="precinct")
        for state in ("PA", "MA", "FL", "IA", "IL"):
            held = [
                value
                for (value,) in conn.execute(
                    """select e.value from external_id e
                       where e.scheme = 'us.contest' and e.value ~ %s
                         and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                     where rs.contest_id = e.entity_id and rs.status = 'active'
                                       and s.publisher <> 'Federal Election Commission' and s.id <> %s)""",
                    [
                        f"^20[0-2][0-9]-0[2-9]-[0-9]{{2}}:{state}:(house:[0-9]+|senate|president):[a-z-]+$",
                        sources[state],
                    ],
                ).fetchall()
            ]
            medsl.attach(
                cs,
                conn,
                congress.filter(
                    pl.col("source") == state,
                    ~pl.concat_str(
                        pl.col("day").cast(pl.String),
                        pl.lit(state),
                        pl.when(pl.col("district").is_not_null())
                        .then(pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String), pl.lit(":")))
                        .otherwise(pl.lit("")),
                        pl.col("chamber").str.strip_prefix("house:"),
                        separator=":",
                    )
                    .str.replace("::", ":")
                    .is_in(held),
                ),
                sources,
            )
        held = [
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[0-2][0-9]-0[6-8]-[0-9]{2}:NY:house:'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and s.publisher <> 'Federal Election Commission' and s.id <> %s)""",
                [sources["NYSBOE"]],
            ).fetchall()
        ]
        medsl.attach(
            cs,
            conn,
            congress.filter(
                pl.col("source") == "NYSBOE",
                ~pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit("NY:house"),
                    pl.col("district").cast(pl.String),
                    pl.col("chamber").str.strip_prefix("house:"),
                    separator=":",
                ).is_in(held),
            ),
            sources,
        )
        used = {value for frame in cs.frames.get("result_value", []) for value in frame["unit_id"].unique().to_list()}
        for day, unit in city.select("day", "code").unique().sort("code").iter_rows():
            precinct = cs.unit("us.precinct", unit)
            if precinct not in cs.minted or str(precinct) not in used or unit in units:
                continue
            units[unit] = precinct
            fips = unit.split(":")[2]
            cs.add(
                "reporting_unit",
                id=precinct,
                polity_id=polities["NY"],
                scheme_code="us.precinct",
                code=unit,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        return cs.write()
