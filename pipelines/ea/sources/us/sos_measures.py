import difflib
import gzip
import html
import itertools
import json
import re
import subprocess
import tempfile
import time
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import fl_sos, gov_counties, ri_countbook
from ea.store import DATA, connect

SOURCE = "us.sos_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRST, LAST = 2000, 2011
NCSL = "https://wabi-west-us-c-primary-api.analysis.windows.net/public/reports/querydata?synchronous=true"
NCSL_PAGE = "https://www.ncsl.org/elections-and-campaigns/statewide-ballot-measures-database"
NCSL_KEY = "b1042657-d1d3-4dc8-ad12-570a7dfc10b1"
NCSL_COLUMNS = (
    "StateName",
    "Year",
    "Election",
    "CombinedForID",
    "ID",
    "Title",
    "IRTypeDefinition",
    "IRStatusDefinition",
)
TX = "https://elections.sos.state.tx.us/"
TX_ELECTIONS = (85, 95, 103, 117, 132, 133, 147, 158)
CA = "https://elections.cdn.sos.ca.gov/sov/"
CA_FILES = {
    date(2000, 3, 7): "2000-primary/measures.pdf",
    date(2000, 11, 7): "2000-general/measures.pdf",
    date(2002, 3, 5): "2002-primary/measures.xls",
    date(2002, 11, 5): "2002-general/measures.xls",
    date(2003, 10, 7): "2003-special/measures.xls",
    date(2004, 3, 2): "2004-primary/measures.xls",
    date(2004, 11, 2): "2004-general/formatted_ballot_measures_detail.xls",
    date(2005, 11, 8): "2005-special/detail_props_formatted_pg1_7.xls",
    date(2006, 6, 6): "2006-primary/sov_detail_primary_props.xls",
    date(2006, 11, 7): "2006-general/measures.xls",
    date(2008, 2, 5): "2008-primary/ballotmeasures.xls",
    date(2008, 6, 3): "2008-statewide-direct-primary/bm08.xls",
    date(2008, 11, 4): "2008-general/57_65_ballot_measures.xls",
    date(2009, 5, 19): "2009-special/ssov/1-6-summary-by-county.xls",
    date(2010, 6, 8): "2010-primary/125-props.xls",
    date(2010, 11, 2): "2010-general/88-state-ballot-measures.xls",
}
AZ = "https://web.archive.org/web/2020id_/http://apps.azsos.gov/election/"
AZ_FILES = {
    date(2000, 11, 7): "2000/General/Canvass2000GE.pdf",
    date(2002, 11, 5): "2002/General/Canvass2002GE.pdf",
    date(2004, 11, 2): "2004/General/Canvass2004General.pdf",
    date(2006, 11, 7): "2006/General/Canvass2006GE.pdf",
    date(2008, 11, 4): "2008/General/Canvass2008GE.pdf",
    date(2010, 5, 18): "2010/may_Special/2010_Special_Election_Official_Canvass.pdf",
    date(2010, 11, 2): "2010/General/Canvass2010GE.pdf",
}
AZ_RECOUNTS = {(date(2010, 11, 2), "112"): "2010/GeneralRecount/Canvass2010Prop112.pdf"}
AZ_COUNTIES = (
    "Apache",
    "Cochise",
    "Coconino",
    "Gila",
    "Graham",
    "Greenlee",
    "La Paz",
    "Maricopa",
    "Mohave",
    "Navajo",
    "Pima",
    "Pinal",
    "Santa Cruz",
    "Yavapai",
    "Yuma",
)
WA = "https://web.archive.org/web/{}id_/http://www.sos.wa.gov/elections/"
WA_LISTS = {
    date(2000, 11, 7): ("20181203183848", 20),
    date(2001, 11, 6): ("20170310022211", 21),
    date(2002, 11, 5): ("20141226153037", 4),
    date(2003, 11, 4): ("20211128180613", 53),
    date(2006, 11, 7): ("20141025224427", 134),
}
WA_BILLS = {"2901": "53", "2969": "51"}
WA_RESULTS = "https://results.vote.wa.gov/results/"
WA_DATES = ("20071106", "20081104", "20091103", "20101102", "20111108")
WA_COUNTIES = (
    "Adams",
    "Asotin",
    "Benton",
    "Chelan",
    "Clallam",
    "Clark",
    "Columbia",
    "Cowlitz",
    "Douglas",
    "Ferry",
    "Franklin",
    "Garfield",
    "Grant",
    "Grays Harbor",
    "Island",
    "Jefferson",
    "King",
    "Kitsap",
    "Kittitas",
    "Klickitat",
    "Lewis",
    "Lincoln",
    "Mason",
    "Okanogan",
    "Pacific",
    "Pend Oreille",
    "Pierce",
    "San Juan",
    "Skagit",
    "Skamania",
    "Snohomish",
    "Spokane",
    "Stevens",
    "Thurston",
    "Wahkiakum",
    "Walla Walla",
    "Whatcom",
    "Whitman",
    "Yakima",
)
WA_ANSWERS = {"Yes": "YES", "No": "NO", "Approved": "YES", "Rejected": "NO", "Maintained": "YES", "Repealed": "NO"}
LA = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob="
LA_DATES = (
    "20001107",
    "20021105",
    "20031004",
    "20040918",
    "20041102",
    "20060930",
    "20061107",
    "20071020",
    "20081104",
    "20101002",
    "20101102",
    "20111022",
    "20111119",
)
OK = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/"
OK_FILES = (
    "2000-election-results/2000-general-election-results/00sq.pdf",
    "2002-election-results/2002-general-election-results/02sq.pdf",
    "2004-election-results/2004-general-election-results/04sq.pdf",
    "2006-election-results/2006-general-election-results/06sq.pdf",
    "2008-election-results/2008-general-election-results/08sq.pdf",
    "2010-election-results/2010-general-election-results/ge-results-county-20101102.pdf",
)
FL_DAYS = (
    date(2000, 11, 7),
    date(2002, 11, 5),
    date(2004, 11, 2),
    date(2006, 11, 7),
    date(2008, 11, 4),
    date(2010, 11, 2),
)
OR = "https://digitalcollections.library.oregon.gov/assets/downloadwiz/{}"
OR_WAYBACK = "https://web.archive.org/web/{}id_/http://www.sos.state.or.us/elections/"
OR_ABSTRACTS = {
    date(2000, 5, 16): (
        OR_WAYBACK.format("20000819120501") + "may162000/m77.htm",
        OR_WAYBACK.format("20000819120510") + "may162000/m78.htm",
        OR_WAYBACK.format("20000819120519") + "may162000/m79.htm",
        OR_WAYBACK.format("20000819120527") + "may162000/m80.htm",
        OR_WAYBACK.format("20000819120540") + "may162000/m81.htm",
        OR_WAYBACK.format("20000819120549") + "may162000/m82.htm",
    ),
    date(2000, 11, 7): (OR.format(344998),),
    date(2002, 5, 21): (
        OR_WAYBACK.format("20020810154424") + "may212002/abstracts/meas10.htm",
        OR_WAYBACK.format("20020810154416") + "may212002/abstracts/meas11.htm",
        OR_WAYBACK.format("20020810155153") + "may212002/abstracts/meas13.htm",
    ),
    date(2002, 9, 17): (OR.format(526060),),
    date(2002, 11, 5): (OR.format(345944), OR.format(344998)),
    date(2003, 1, 28): (OR.format(526092),),
    date(2003, 9, 16): (OR.format(526195),),
    date(2004, 2, 3): (OR.format(526246),),
    date(2004, 11, 2): (OR.format(347401),),
    date(2006, 11, 7): (OR.format(348404),),
    date(2007, 11, 6): (OR.format(526941),),
    date(2008, 5, 20): (
        OR_WAYBACK.format("20080626005746") + "may202008/results/m51.pdf",
        OR_WAYBACK.format("20080626010151") + "may202008/results/m52.pdf",
        OR_WAYBACK.format("20080702194311") + "may202008/m53recount_abstract.pdf",
    ),
    date(2008, 11, 4): (OR.format(350473),),
    date(2010, 1, 26): (OR.format(527415),),
    date(2010, 5, 18): (
        OR_WAYBACK.format("20101115235937") + "may182010/results/m68.pdf",
        OR_WAYBACK.format("20101115235937") + "may182010/results/m69.pdf",
    ),
    date(2010, 11, 2): (OR.format(354805),),
}
OR_COUNTIES = (
    "Baker",
    "Benton",
    "Clackamas",
    "Clatsop",
    "Columbia",
    "Coos",
    "Crook",
    "Curry",
    "Deschutes",
    "Douglas",
    "Gilliam",
    "Grant",
    "Harney",
    "Hood River",
    "Jackson",
    "Jefferson",
    "Josephine",
    "Klamath",
    "Lake",
    "Lane",
    "Lincoln",
    "Linn",
    "Malheur",
    "Marion",
    "Morrow",
    "Multnomah",
    "Polk",
    "Sherman",
    "Tillamook",
    "Umatilla",
    "Union",
    "Wallowa",
    "Wasco",
    "Washington",
    "Wheeler",
    "Yamhill",
)
ME = "https://lldc.mainelegislature.org/Open/Elections/Returns/"
ME_SHEETS = {
    date(2000, 11, 7): "2000/20001107/elec_20001107i.xlsx",
    date(2001, 11, 6): "2001/elec_20011106b.xlsx",
    date(2002, 6, 11): "2002/20020611/elec_20020611t.xlsx",
    date(2002, 11, 5): "2002/20021105/elec_20021105r.xlsx",
    date(2004, 6, 8): "2004/20040608/elec_20040608i.xlsx",
    date(2004, 11, 2): "2004/20041102/elec_20041102o.xlsx",
    date(2005, 11, 8): "2005/elec_20051108b.xlsx",
    date(2007, 6, 12): "2007/20070612/elec_20070612e.xlsx",
    date(2008, 6, 10): "2008/20080610/elec_20080610l.xlsx",
    date(2008, 11, 4): "2008/20081104/elec_20081104r.xlsx",
    date(2011, 11, 8): "2011/20111108/elec_20111108j.xlsx",
}
ME_TEXTS = {
    date(2003, 6, 10): (f"{ME}2003/20030610/elec_20030610c.pdf", (("1", "YES"), ("1", "NO"))),
    date(2003, 11, 4): (
        f"{ME}2003/20031104/elec_20031104b.pdf",
        (None, None, None, *((question, answer) for question in "23456" for answer in ("YES", "NO"))),
    ),
    date(2006, 11, 7): (
        f"{ME}2006/20061107/elec_20061107w.pdf",
        tuple((question, answer) for question in "12" for answer in ("YES", "NO")),
    ),
    date(2007, 11, 6): (
        f"{ME}2007/20071106/elec_20071106h.pdf",
        tuple((question, answer) for question in "12345" for answer in ("YES", "NO")),
    ),
    date(2009, 11, 3): (
        "https://web.archive.org/web/20150507180449id_/http://www.maine.gov/sos/cec/elec/results/2008-09/referendumbycounty.html",
        tuple((question, answer) for question in "1234567" for answer in ("YES", "NO")),
    ),
    date(2010, 6, 8): (
        f"{ME}2010/20100608/elec_20100608n.pdf",
        tuple((question, answer) for question in "12345" for answer in ("YES", "NO")),
    ),
    date(2010, 11, 2): (
        f"{ME}2010/20101102/elec_20101102w.pdf",
        tuple(column for question in "123" for column in ((question, "YES"), (question, "NO"), None)),
    ),
}
ME_COUNTIES = (
    "androscoggin",
    "aroostook",
    "cumberland",
    "franklin",
    "hancock",
    "kennebec",
    "knox",
    "lincoln",
    "oxford",
    "penobscot",
    "piscataquis",
    "sagadahoc",
    "somerset",
    "waldo",
    "washington",
    "york",
)
AL = "https://web.archive.org/web/{stamp}id_/http://sos.alabama.gov/sites/default/files/election-data/2017-06/"
AL_FILES = {
    date(2000, 11, 7): ("20170706141913", "2000g-cty.exe"),
    date(2002, 11, 5): ("20170706141828", "2002GeneralElectionTotals.XLS"),
    date(2003, 9, 9): ("20170706141819", "20030909countylevel.xls"),
    date(2004, 11, 2): ("20170706141810", "2004ge-countylevel_0.xls"),
}
AL_COUNTIES = {
    "autauga",
    "baldwin",
    "barbour",
    "bibb",
    "blount",
    "bullock",
    "butler",
    "calhoun",
    "chambers",
    "cherokee",
    "chilton",
    "choctaw",
    "clarke",
    "clay",
    "cleburne",
    "coffee",
    "colbert",
    "conecuh",
    "coosa",
    "covington",
    "crenshaw",
    "cullman",
    "dale",
    "dallas",
    "dekalb",
    "elmore",
    "escambia",
    "etowah",
    "fayette",
    "franklin",
    "geneva",
    "greene",
    "hale",
    "henry",
    "houston",
    "jackson",
    "jefferson",
    "lamar",
    "lauderdale",
    "lawrence",
    "lee",
    "limestone",
    "lowndes",
    "macon",
    "madison",
    "marengo",
    "marion",
    "marshall",
    "mobile",
    "monroe",
    "montgomery",
    "morgan",
    "perry",
    "pickens",
    "pike",
    "randolph",
    "russell",
    "st. clair",
    "shelby",
    "sumter",
    "talladega",
    "tallapoosa",
    "tuscaloosa",
    "walker",
    "washington",
    "wilcox",
    "winston",
}
NV = "https://web.archive.org/web/{}id_/http://sos.state.nv.us/elections/results/{}/ElectionSummary.asp"
NV_PAGES = {
    date(2000, 11, 7): ("20080206205544", "2000General"),
    date(2002, 11, 5): ("20080206210100", "2002General"),
    date(2004, 11, 2): ("20080206205932", "2004General"),
    date(2006, 11, 7): ("20080206205130", "2006StateWideGeneral"),
    date(2008, 11, 4): ("20090116025519", "2008StateWideGeneral"),
}
NV_COUNTIES = (
    "32510",
    "32001",
    "32003",
    "32005",
    "32007",
    "32009",
    "32011",
    "32013",
    "32015",
    "32017",
    "32019",
    "32021",
    "32023",
    "32027",
    "32029",
    "32031",
    "32033",
)
RI_BOOKS = {
    date(2000, 11, 7): "2000-Count-Book.pdf",
    date(2002, 11, 5): "2002-Count-Book.pdf",
    date(2004, 11, 2): "2004-Count-Book.pdf",
    date(2006, 11, 7): "2006-Count-Book.pdf",
    date(2010, 11, 2): "2010-Countbook.pdf",
}
NE = "https://sos.nebraska.gov/sites/default/files/doc/"
NE_BOOKS = {
    date(2004, 11, 2): "2004%20General.pdf",
    date(2008, 11, 4): "elections/2008/2008%20General%20Canvass%20Book.pdf",
    date(2010, 11, 2): "elections/2010/2010%20Gen%20Canvass%20Book%2011-30-Final.pdf",
    date(2008, 5, 13): "elections/2008/2008%20primary%20official%20results.pdf",
    date(2010, 5, 11): "elections/2010/Canvas%20PR10%20After%20Recount%20Final.pdf",
}
NE_SCANS = {
    "ne-measures-2000": "elections/2000/2000%20General.pdf",
    "ne-measures-2006": "2006%20General.pdf",
    "ne-measures-2006-primary": "2006%20Primary.pdf",
}
SCANS = Path(__file__).resolve().parents[3] / "scans"
BALLOT_LABELS = {("NE", date(2006, 11, 7), "422"): ("Retain", "Repeal")}
NE_HEADING = re.compile(
    r"^\s*(?:Proposed (?:Constitutional )?Amendment|Amendment Number|Initiative Measure|Referendum Measure|"
    r"Measure Number|No\.)\s*(?:No\.\s*)?(\d+[A-C]?)\b",
    re.IGNORECASE,
)
AR = "https://www.sos.arkansas.gov/uploads/elections/"
AR_BOOKS = {
    date(2000, 11, 7): "2000_Certified_General_Election_Results.pdf",
    date(2002, 11, 5): "2002_General.pdf",
    date(2004, 11, 2): "2004_General_Election_and_Non-Partisan_Judicial_Runoff_Certification_Report.pdf",
}
AR_SHEETS = {date(2010, 11, 2): "2010_General_Election_Results_-_Fed%2C_State_-_by_Polling_Location.xlsx"}
AR_PUBLICATIONS = {date(2006, 11, 7): "20070308_ARSOS_2006_Elections_Publication_Final__Revised.xls"}
AR_VOICES = {date(2008, 11, 4): "Voices%20of%20Ark%202008-WEB2.pdf"}
SD = "https://sdsos.gov/elections-voting/assets/Archive/Prior%20to%202026/"
SD_SHEETS = {date(2000, 11, 7): "00SWgenres.xls"}
SD_PRECINCTS = {date(2010, 11, 2): "pastelections_general2010_bqpct.xls"}
SD_SCANS = {
    "sd-measures-2008": "https://sdsos.gov/elections-voting/assets/Archive/2002-2011Assets/2008OfficialGeneralElectionStateCanvass.pdf"
}
MISPRINTS = {("CA", date(2000, 11, 7), "38", "NO"): 3000}
MISREPORTED = {("ME", date(2005, 11, 8), "6"), ("LA", date(2010, 10, 2), "2")}
STATES = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}
PUBLISHERS = {
    "TX": ("Texas Secretary of State", "Texas election history: returns by county, 1992–2019", TX),
    "CA": (
        "California Secretary of State",
        "Statement of Vote",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    "AL": ("Alabama Secretary of State", "Election results by county", "https://www.sos.alabama.gov/alabama-votes"),
    "AZ": ("Arizona Secretary of State", "State of Arizona Official Canvass", "https://azsos.gov/elections"),
    "WA": (
        "Washington Secretary of State",
        "Election results and county breakdowns",
        "https://www.sos.wa.gov/elections/data-research/election-data-and-maps/election-results-and-voters-pamphlets",
    ),
    "OK": (
        "Oklahoma State Election Board",
        "Official results by county",
        "https://oklahoma.gov/elections/elections-results.html",
    ),
    "FL": (fl_sos.PUBLISHER, fl_sos.TITLE, "https://results.elections.myflorida.com/"),
    "OH": gov_counties.OFFICIAL["OH"][:3],
    "NV": (
        "Nevada Secretary of State",
        "Official election results",
        "https://www.nvsos.gov/sos/elections/election-information/election-results",
    ),
    "AR": (
        "Arkansas Secretary of State",
        "Certified general election results",
        "https://www.sos.arkansas.gov/elections/research/election-results",
    ),
    "NE": (
        "Nebraska Secretary of State",
        "Official report of the Board of State Canvassers",
        "https://sos.nebraska.gov/previous-elections",
    ),
    "RI": (ri_countbook.PUBLISHER, ri_countbook.TITLE, "https://elections.ri.gov/publications/election-publications"),
    "LA": ("Louisiana Secretary of State", "Official election results", "https://voterportal.sos.la.gov/"),
    "OR": (
        "Oregon Secretary of State",
        "Official abstract of votes",
        "https://digitalcollections.library.oregon.gov/nodes/view/88649",
    ),
    "SD": (
        "South Dakota Secretary of State",
        "Official election returns",
        "https://sdsos.gov/elections-voting/election-resources/election-history/election-history-search.aspx",
    ),
    "ME": (
        "Maine Department of the Secretary of State",
        "Referendum election tabulations, 2000–2011",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/",
    ),
}
TYPES = {
    "Initiative": "initiative",
    "Popular Referendum": "optional_referendum",
    "Legislative Referendum": "mandatory_referendum",
    "Constitutional Revision Commission": "mandatory_referendum",
}
METHODOLOGY = (
    "County returns from the {publisher}'s official results. The measure's number, title, type, and outcome are from "
    "NCSL's Statewide Ballot Measures Database."
)


MAINE = (
    " Maine's county totals are summed from its town and precinct returns or read from its referendum tabulations, "
    "as kept by the Maine State Law and Legislative Reference Library."
)
LIMITED = (
    " Limited ballots, which Rhode Island counts by limited district rather than by town, are a unit of their own."
)
SCANNED = (
    " The county figures were read from the scanned canvass book by a vision model and checked against the book's "
    "printed state totals; the box shown on each figure is approximate."
)
OVERSEAS = " Overseas and military (UOCAVA) ballots, which Maine tabulates statewide, are a unit of their own."


def ncsl_query() -> dict[str, object]:
    return {
        "version": "1.0.0",
        "queries": [
            {
                "Query": {
                    "Commands": [
                        {
                            "SemanticQueryDataShapeCommand": {
                                "Query": {
                                    "Version": 2,
                                    "From": [{"Name": "a", "Entity": "All Years Table", "Type": 0}],
                                    "Select": [
                                        {
                                            "Column": {
                                                "Expression": {"SourceRef": {"Source": "a"}},
                                                "Property": column,
                                            },
                                            "Name": f"a.{column}",
                                        }
                                        for column in NCSL_COLUMNS
                                    ],
                                },
                                "Binding": {
                                    "Primary": {"Groupings": [{"Projections": list(range(len(NCSL_COLUMNS)))}]},
                                    "DataReduction": {"DataVolume": 3, "Primary": {"Window": {"Count": 30000}}},
                                    "Version": 1,
                                },
                            }
                        }
                    ]
                },
                "ApplicationContext": {
                    "DatasetId": "26a52b20-202c-4bf4-8184-f5ce3e49fc38",
                    "Sources": [{"ReportId": "fe633d7c-6c27-4488-9db3-a28c45966d68"}],
                },
            }
        ],
        "cancelQueries": [],
        "modelId": 1709732,
    }


def tx_races(election: int) -> list[tuple[int, str]]:
    text = raw.cached(SOURCE, f"{TX}elchist{election}_raceselect.htm").path.read_text(errors="replace")
    return [
        (int(race), title.strip())
        for race, title in re.findall(r"<OPTION value=\"?(\d+)\"?[^>]*>([^<\n]+)", text)
        if re.match(r"Prop\.? \d+ ", title.strip())
    ]


def fetch() -> None:
    for stamp, folder in NV_PAGES.values():
        raw.fetch(SOURCE, NV.format(stamp, folder), suffix=".html")
    for name in [*NE_BOOKS.values(), *NE_SCANS.values()]:
        raw.fetch(SOURCE, NE + name, suffix=".pdf")
    for name in [*AR_BOOKS.values(), *AR_VOICES.values()]:
        raw.fetch(SOURCE, AR + name, suffix=".pdf")
    for name in [*SD_SHEETS.values(), *SD_PRECINCTS.values()]:
        raw.fetch(SOURCE, SD + name, suffix=".xls")
        time.sleep(1)
    for url in SD_SCANS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for name in AR_SHEETS.values():
        raw.fetch(SOURCE, AR + name, suffix=".xlsx")
    for name in AR_PUBLICATIONS.values():
        raw.fetch(SOURCE, AR + name, suffix=".xls")
    try:
        raw.cached(SOURCE, NCSL_PAGE)
    except LookupError:
        response = httpx.post(
            NCSL,
            json=ncsl_query(),
            headers={"X-PowerBI-ResourceKey": NCSL_KEY, "Accept-Encoding": "gzip"},
            timeout=300,
        )
        response.raise_for_status()
        raw.store(
            SOURCE,
            NCSL_PAGE,
            response.content,
            suffix=".json",
            retrieved_at=datetime.now(UTC),
            media_type="application/json",
        )
    for election in TX_ELECTIONS:
        raw.fetch(SOURCE, f"{TX}elchist{election}_raceselect.htm", suffix=".htm")
        for race, _ in tx_races(election):
            url = f"{TX}elchist{election}_race{race}.htm"
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.fetch(SOURCE, url, suffix=".htm")
                time.sleep(1)
    for path in CA_FILES.values():
        raw.fetch(SOURCE, CA + path, suffix=Path(path).suffix)
        time.sleep(1)
    for stamp, file in AL_FILES.values():
        raw.fetch(SOURCE, AL.format(stamp=stamp) + file, suffix=Path(file).suffix.lower())
        time.sleep(1)
    for path in [*AZ_FILES.values(), *AZ_RECOUNTS.values()]:
        raw.fetch(SOURCE, AZ + path, suffix=".pdf")
        time.sleep(1)
    for stamp, election in WA_LISTS.values():
        url = WA.format(stamp) + f"results_report.aspx?e={election}&c=&c2=&t=&t2=5&p=&p2=&y="
        raw.fetch(SOURCE, url, suffix=".html")
        time.sleep(1)
        for measure in re.findall(
            rf"county_breakdown\.aspx\?e={election}&(?:amp;)?t=(\d+)", raw.cached(SOURCE, url).path.read_text()
        ):
            raw.fetch(
                SOURCE,
                WA.format("2016") + f"results_report_county_breakdown.aspx?e={election}&t={measure}",
                suffix=".html",
            )
            time.sleep(1)
    for day in WA_DATES:
        raw.fetch(SOURCE, f"{WA_RESULTS}{day}/measures-all.html", suffix=".html")
        for page in sorted(
            set(
                re.findall(
                    r'href="([^"]+_bycounty\.html)"',
                    raw.cached(SOURCE, f"{WA_RESULTS}{day}/measures-all.html").path.read_text(),
                    re.IGNORECASE,
                )
            )
        ):
            raw.fetch(SOURCE, f"{WA_RESULTS}{day}/{page.lower()}", suffix=".html")
            time.sleep(1)
    for day in LA_DATES:
        for name in ("ElectionRaces", "RacesCandidates_Multiparish", "Votes_Multiparish"):
            raw.fetch(SOURCE, f"{LA}{day}/{name}.htm", suffix=".json")
            time.sleep(1)
        for race in json.loads(raw.cached(SOURCE, f"{LA}{day}/ElectionRaces.htm").path.read_text())["Races"]["Race"]:
            if race["Level"] == "998":
                raw.fetch(SOURCE, f"{LA}{day}/VotesRaceByParish/Votes_{race['RaceID']}.htm", suffix=".json")
                time.sleep(1)
    for url in [*(ME + path for path in ME_SHEETS.values()), *(url for url, _ in ME_TEXTS.values())]:
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)
        time.sleep(1)
    for path in OK_FILES:
        raw.fetch(SOURCE, OK + path, suffix=".pdf")
        time.sleep(1)
    for url in dict.fromkeys(url for urls in OR_ABSTRACTS.values() for url in urls):
        raw.fetch(SOURCE, url, suffix=".htm" if url.endswith(".htm") else ".pdf")
        time.sleep(1)


def ncsl() -> pl.DataFrame:
    content = raw.cached(SOURCE, NCSL_PAGE).path.read_bytes()
    data = json.loads(gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content)
    shape = data["results"][0]["result"]["data"]["dsr"]["DS"][0]
    if shape.get("RT"):
        raise ValueError("NCSL returned a partial table")
    dictionaries = shape.get("ValueDicts", {})
    rows: list[dict[str, object]] = []
    previous: list[object] = [None] * len(NCSL_COLUMNS)
    schema: list[dict[str, object]] = []
    for block in shape["PH"]:
        for entries in block.values():
            for entry in entries:
                schema = entry.get("S", schema)
                values = iter(entry.get("C", []))
                row: list[object] = []
                for index in range(len(NCSL_COLUMNS)):
                    if entry.get("R", 0) >> index & 1:
                        value = previous[index]
                    elif entry.get("Ø", 0) >> index & 1:
                        value = None
                    else:
                        value = next(values)
                        name = schema[index].get("DN")
                        if name is not None and isinstance(value, int):
                            value = dictionaries[name][value]
                    row.append(value)
                previous = row
                rows.append(dict(zip(NCSL_COLUMNS, row, strict=True)))
    return (
        pl.DataFrame(rows, schema=dict.fromkeys(NCSL_COLUMNS, pl.String))
        .filter(
            pl.col("StateName").is_in(list(STATES)),
            pl.col("IRStatusDefinition").is_in(["Pass", "Fail"]),
            pl.col("Year").cast(pl.Int32).is_between(FIRST, LAST),
        )
        .unique("CombinedForID", keep="first", maintain_order=True)
        .select(
            state=pl.col("StateName").replace_strict(STATES),
            year=pl.col("Year").cast(pl.Int32),
            election=pl.col("Election"),
            measure=pl.col("ID").str.strip_chars(),
            number=pl.col("ID").str.extract(r"(\d+[A-Z]?)\s*(?::.*)?$"),
            title=pl.col("Title").str.replace_all(r"\s+", " ").str.strip_chars(),
            type=pl.col("IRTypeDefinition"),
            passed=pl.col("IRStatusDefinition") == "Pass",
        )
    )


def cells(row: str, tag: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(rf"<{tag}[^>]*>(.*?)(?=<{tag}[\s>]|</TR>|$)", row, re.IGNORECASE | re.DOTALL)
    ]


def texas() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for election in TX_ELECTIONS:
        for race, title in tx_races(election):
            url = f"{TX}elchist{election}_race{race}.htm"
            text = raw.cached(SOURCE, url).path.read_text(errors="replace")
            heading = re.search(r"<H3[^>]*>(\d+)/(\d+)/(\d{4})</H3>", text, re.IGNORECASE)
            if heading is None:
                raise ValueError(f"{url}: no date")
            day = date(int(heading[3]), int(heading[1]), int(heading[2]))
            lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
            labels = next(cells(line, "TH") for line in lines if re.search(r">IN FAVOR<", line, re.IGNORECASE))
            labels = [label.upper() for label in labels]
            columns = {labels.index("IN FAVOR"): "YES", labels.index("AGAINST"): "NO"}
            data = [cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
            total = next(row for row in data if row[0] == "ALL COUNTIES")
            counties = [row for row in data if row[0] != "ALL COUNTIES"]
            if len(counties) != 254:
                raise ValueError(f"{url}: {len(counties)} counties")
            match = re.match(r"Prop\.? (\d+) (.+)", title)
            assert match is not None
            for column, answer in columns.items():
                votes = [int(row[column].replace(",", "") or 0) for row in counties]
                if sum(votes) != int(total[column].replace(",", "")):
                    raise ValueError(f"{url} {answer}: counties sum to {sum(votes)}, not {total[column]}")
                rows.extend(
                    {
                        "state": "TX",
                        "day": day,
                        "number": match[1],
                        "label": match[2].strip(),
                        "url": url,
                        "county": row[0],
                        "answer": answer,
                        "votes": value,
                    }
                    for row, value in zip(counties, votes, strict=True)
                )
    return rows


def crosstab(
    book: fastexcel.ExcelReader,
    heading: re.Pattern[str],
    names: list[str],
    counties: set[str],
    votes: dict[tuple[str, str], dict[str, int]],
    totals: dict[tuple[str, str], int],
) -> None:
    for sheet in book.sheet_names:
        heads: dict[int, str | None] = {}
        labels: dict[int, tuple[str, str]] = {}
        for row in book.load_sheet(sheet, header_row=None).to_polars().rows():
            cells = [
                str(int(value)) if isinstance(value, float) and value.is_integer() else str(value or "").strip()
                for value in row
            ]
            found = {
                index: match[1] if (match := heading.search(cell)) else None
                for index, cell in enumerate(cells)
                if heading.search(cell) or re.search(r"(?i)\b(amendment|proposition)\b", cell)
            }
            if found:
                heads, labels = found, {}
                continue
            answers = {
                index: "YES" if cell.lower() in ("for", "yes") else "NO"
                for index, cell in enumerate(cells)
                if cell.lower() in ("for", "yes", "against", "no")
            }
            if answers and heads:
                ordered = sorted(heads)
                labels = {}
                for position, head in enumerate(ordered):
                    end = ordered[position + 1] if position + 1 < len(ordered) else len(cells)
                    for answer in ("YES", "NO"):
                        index = next((i for i in sorted(answers) if head <= i < end and answers[i] == answer), None)
                        prop = heads[head]
                        if index is not None and prop is not None:
                            labels[index] = (prop, answer)
                continue
            name = re.sub(r"\s+", " ", next((cell for cell in cells if cell and not cell.isdigit()), "").lower())
            if not labels or name not in names:
                continue
            for index, key in labels.items():
                value = cells[index].replace(",", "")
                if not re.fullmatch(r"\d+", value):
                    continue
                if name in counties:
                    votes.setdefault(key, {})[name] = int(value)
                else:
                    totals[key] = int(value)


def california() -> list[dict[str, object]]:
    counties = {
        "alameda",
        "alpine",
        "amador",
        "butte",
        "calaveras",
        "colusa",
        "contra costa",
        "del norte",
        "el dorado",
        "fresno",
        "glenn",
        "humboldt",
        "imperial",
        "inyo",
        "kern",
        "kings",
        "lake",
        "lassen",
        "los angeles",
        "madera",
        "marin",
        "mariposa",
        "mendocino",
        "merced",
        "modoc",
        "mono",
        "monterey",
        "napa",
        "nevada",
        "orange",
        "placer",
        "plumas",
        "riverside",
        "sacramento",
        "san benito",
        "san bernardino",
        "san diego",
        "san francisco",
        "san joaquin",
        "san luis obispo",
        "san mateo",
        "santa barbara",
        "santa clara",
        "santa cruz",
        "shasta",
        "sierra",
        "siskiyou",
        "solano",
        "sonoma",
        "stanislaus",
        "sutter",
        "tehama",
        "trinity",
        "tulare",
        "tuolumne",
        "ventura",
        "yolo",
        "yuba",
    }
    names = sorted(counties | {"state totals", "state total", "totals", "total"}, key=len, reverse=True)
    heading = re.compile(r"Proposition\s+(?:No\.\s*)?(\d+[A-Z]?)")
    rows: list[dict[str, object]] = []
    for day, path in CA_FILES.items():
        url = CA + path
        file = raw.cached(SOURCE, url).path
        votes: dict[tuple[str, str], dict[str, int]] = {}
        totals: dict[tuple[str, str], int] = {}
        if path.endswith(".pdf"):
            text = subprocess.run(["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True)
            props: list[str] = []
            pending = None
            for line in text.stdout.splitlines():
                if found := heading.findall(line):
                    props = found
                    continue
                low = re.sub(r"\s+", " ", line.strip().lower())
                name = next((name for name in names if low.startswith(name)), None)
                if name is None and pending and re.fullmatch(r"[-\d, ]+", low):
                    name, low = pending, f"{pending} {low}"
                pending = None
                if name is None or not props:
                    continue
                rest = low[len(name) :]
                if not rest.strip():
                    pending = name
                    continue
                if not re.fullmatch(r"[-\d, ]+", rest):
                    continue
                numbers = [int(value.replace(",", "")) for value in rest.split()]
                if len(numbers) != 3 * len(props):
                    raise ValueError(f"{url} {name}: {len(numbers)} figures for {props}")
                for index, prop in enumerate(props):
                    for offset, answer in enumerate(("YES", "NO")):
                        value = numbers[3 * index + offset]
                        if name in counties:
                            votes.setdefault((prop, answer), {})[name] = value
                        else:
                            totals[(prop, answer)] = value
        else:
            crosstab(fastexcel.read_excel(file), heading, names, counties, votes, totals)
        if {(prop, answer) for prop, _ in votes for answer in ("YES", "NO")} != set(votes):
            raise ValueError(f"{url}: a proposition lacks a yes or no column")
        for (prop, answer), found in votes.items():
            if len(found) != len(counties):
                raise ValueError(f"{url} {prop} {answer}: {len(found)} counties")
            gap = sum(found.values()) - totals[(prop, answer)]
            if gap != MISPRINTS.get(("CA", day, prop, answer), 0):
                raise ValueError(f"{url} {prop} {answer}: counties sum {gap:+} off the state total")
            rows.extend(
                {
                    "state": "CA",
                    "day": day,
                    "number": prop,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, value in found.items()
            )
    return rows


def alabama() -> list[dict[str, object]]:
    names = sorted(AL_COUNTIES | {"state total", "state totals", "total", "totals"}, key=len, reverse=True)
    heading = re.compile(r"^(?:Statewide\s+)?Amendment\s*(?:No\.|#)?\s*(\d+)\s*$", re.IGNORECASE)
    rows: list[dict[str, object]] = []
    for day, (stamp, file) in AL_FILES.items():
        url = AL.format(stamp=stamp) + file
        path = raw.cached(SOURCE, url).path
        votes: dict[tuple[str, str], dict[str, int]] = {}
        totals: dict[tuple[str, str], int] = {}
        if file.endswith(".exe"):
            crosstab(
                fastexcel.read_excel(zipfile.ZipFile(path).read("2000g-amend.xls")),
                heading,
                names,
                AL_COUNTIES,
                votes,
                totals,
            )
        elif day.year == 2002:
            columns: dict[int, str] = {}
            for row in fastexcel.read_excel(path).load_sheet("MASTER", header_row=None).to_polars().rows():
                cells = [
                    str(int(value)) if isinstance(value, float) and value.is_integer() else str(value or "").strip()
                    for value in row
                ]
                if "Autauga" in cells:
                    columns = {index: cell.lower() for index, cell in enumerate(cells) if cell.lower() in AL_COUNTIES}
                    continue
                match = re.fullmatch(r"Statewide Amendment #(\d+)", cells[1]) if len(cells) > 4 else None
                if match is None or cells[3] not in ("YES", "NO"):
                    continue
                key = (match[1], cells[3])
                votes[key] = {county: int(cells[index]) for index, county in columns.items()}
                totals[key] = int(cells[4])
        else:
            crosstab(fastexcel.read_excel(path), heading, names, AL_COUNTIES, votes, totals)
        if {(prop, answer) for prop, _ in votes for answer in ("YES", "NO")} != set(votes):
            raise ValueError(f"{url}: an amendment lacks a yes or no column")
        for (prop, answer), found in votes.items():
            if len(found) != len(AL_COUNTIES) or sum(found.values()) != totals[(prop, answer)]:
                raise ValueError(f"{url} {prop} {answer}: {len(found)} counties, {sum(found.values())} votes")
            rows.extend(
                {
                    "state": "AL",
                    "day": day,
                    "number": prop,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, value in found.items()
            )
    return rows


def arizona() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    recounted = {(day, prop): AZ + path for (day, prop), path in AZ_RECOUNTS.items()}
    for day, path in [*AZ_FILES.items(), *((day, path) for (day, _), path in AZ_RECOUNTS.items())]:
        url = AZ + path
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        )
        prop = None
        for line in text.stdout.splitlines():
            if match := re.match(r"\s*PROPOSITION\s+(\d+)\b", line):
                prop = match[1]
                continue
            match = re.match(r"\s*(YES|NO)\s*\*?\s+([\d,]+(?:\s+[\d,]+)*)\s*$", line)
            if match is None or prop is None or recounted.get((day, prop), url) != url:
                continue
            numbers = [int(value.replace(",", "")) for value in match[2].split()]
            if len(numbers) != len(AZ_COUNTIES) + 1 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"{url} {prop} {match[1]}: {numbers}")
            rows.extend(
                {
                    "state": "AZ",
                    "day": day,
                    "number": prop,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": match[1],
                    "votes": value,
                }
                for county, value in zip(AZ_COUNTIES, numbers, strict=False)
            )
    return rows


def maine() -> list[dict[str, object]]:
    names: dict[str, str] = {county.upper(): county for county in ME_COUNTIES} | {
        county[:3].upper(): county for county in ME_COUNTIES
    }
    found: dict[date, tuple[str, dict[str, dict[tuple[str, str], int]], dict[tuple[str, str], int]]] = {}
    for day, file in ME_SHEETS.items():
        table = [
            [
                str(int(value))
                if isinstance(value, float) and value.is_integer()
                else " ".join(str(value or "").split())
                for value in row
            ]
            for row in fastexcel.read_excel(raw.cached(SOURCE, ME + file).path)
            .load_sheet(0, header_row=None)
            .to_polars()
            .rows()
        ]
        columns: dict[int, tuple[str | None, str]] = {}
        spans: dict[str, int] = {}
        long = None
        for row in table[:6]:
            heads = [cell.upper() for cell in row]
            for index, cell in enumerate(heads):
                if match := re.fullmatch(r"Q?(\d+)_?(YES|NO)|(YES|NO)(\d+)", cell):
                    columns[index] = (match[1] or match[4], match[2] or match[3])
                elif match := re.match(r"QUESTION (\d+):", cell):
                    spans.setdefault(match[1], index)
            if spans and "YES" in heads:
                ordered = sorted(spans.items(), key=lambda item: item[1])
                for position, (question, start) in enumerate(ordered):
                    end = ordered[position + 1][1] if position + 1 < len(ordered) else len(heads)
                    for answer in ("YES", "NO"):
                        columns[heads.index(answer, start, end)] = (question, answer)
                spans = {}
            elif not columns and "YES" in heads and "NO" in heads:
                long = heads.index("QUESTNUM") if "QUESTNUM" in heads else None
                columns = {heads.index("YES"): (None, "YES"), heads.index("NO"): (None, "NO")}
        votes: dict[str, dict[tuple[str, str], int]] = {}
        totals: dict[tuple[str, str], int] = {}
        for row in table:
            where = next(
                (
                    index
                    for index, cell in enumerate(row[:3])
                    if cell.upper() in names or "UOCAVA" in cell.upper() or cell.upper() == "TOTAL"
                ),
                None,
            )
            if where is None:
                continue
            label = row[where].upper()
            county = names.get(label, "uocava" if "UOCAVA" in label else None)
            cells: dict[tuple[str, str], str] = {}
            if columns:
                for index, (question, answer) in columns.items():
                    cells[(question or (row[long] if long is not None else "1"), answer)] = row[index]
            else:
                width = 4 if row[where + 4].isalpha() else 3
                for start in range(where + 3, len(row) - width + 1, width):
                    if row[start]:
                        cells[(row[start], "YES")] = row[start + width - 2]
                        cells[(row[start], "NO")] = row[start + width - 1]
            for key, value in cells.items():
                if not value:
                    continue
                if county is None:
                    totals[key] = int(value)
                else:
                    votes.setdefault(county, {}).setdefault(key, 0)
                    votes[county][key] += int(value)
        found[day] = (ME + file, votes, totals)
    for day, (file, layout) in ME_TEXTS.items():
        path = raw.cached(SOURCE, file).path
        if file.endswith(".html"):
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", path.read_text(errors="replace"))
            text = re.sub(r"(?is)<tr\b.*?</tr>", lambda row: " ".join(row[0].split()) + "\n", text)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        else:
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout
        votes = {}
        totals = {}
        positional = re.search(r"(?i)county\s+totals?", text) is not None
        for line in text.splitlines():
            if match := re.match(r"(?i)\s*(?:([a-z]+)\s+)?county\s+totals?:?(.*)$", line):
                county = (match[1] or "").lower() or next((name for name in ME_COUNTIES if name not in votes), None)
                body = match[2]
            elif (match := re.match(r"\s*([A-Z]+|STATE UOCAVA)\s+([\d,\s]+)$", line)) and (
                match[1] == "STATE UOCAVA" or match[1] in names and not positional
            ):
                county = "uocava" if match[1] == "STATE UOCAVA" else names[match[1]]
                body = match[2]
            elif match := re.match(r"(?i)\s*(?:state(?:wide)?\s+totals?|totals)\b:?(.*)$", line):
                county, body = None, match[1]
            else:
                continue
            body = re.sub(r"(?<=,\d\d) ?[Il]\b", "1", body)
            body = re.sub(r"(?<=,\d) ?[Il] ?(?=\d\b)", "1", body)
            body = re.sub(r"\b[Il] ?(?=\d)", "1", body).replace("l", "1")
            body = re.sub(r"(?<=\d)\.(?=\d{3}\b)", ",", body)
            numbers = [int(value.replace(",", "")) for value in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", body)]
            if not numbers or (county is None and totals) or county in votes:
                continue
            if county is not None and county != "uocava" and county not in ME_COUNTIES:
                raise ValueError(f"{file}: {county} is not a Maine county")
            if not max(i for i, column in enumerate(layout) if column) < len(numbers) <= len(layout):
                if county is None:
                    continue
                raise ValueError(f"{file} {county}: {len(numbers)} numbers for {len(layout)} columns")
            counted = {column: value for column, value in zip(layout, numbers, strict=False) if column}
            if county is None:
                totals = counted
            else:
                votes[county] = counted
        found[day] = (file, votes, totals)
    rows: list[dict[str, object]] = []
    for day, (url, votes, totals) in found.items():
        keys = {key for counts in votes.values() for key in counts}
        for question, answer in sorted(keys):
            counts = {
                county: value[(question, answer)] for county, value in votes.items() if (question, answer) in value
            }
            if set(ME_COUNTIES) - set(counts):
                continue
            total = totals.get((question, answer))
            if total is not None and sum(counts.values()) != total:
                raise ValueError(
                    f"{url} {question} {answer}: counties sum {sum(counts.values()) - total:+} off the total"
                )
            rows.extend(
                {
                    "state": "ME",
                    "day": day,
                    "number": question,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, value in counts.items()
            )
    return rows


def louisiana() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in LA_DATES:
        races = {
            race["RaceID"]: race["OfficeTitleAndDesc"]
            for race in json.loads(raw.cached(SOURCE, f"{LA}{day}/ElectionRaces.htm").path.read_text())["Races"]["Race"]
            if race["Level"] == "998"
        }
        answers: dict[tuple[str, str], str] = {}
        totals: dict[tuple[str, str], int] = {}
        for name in ("RacesCandidates_Multiparish", "Votes_Multiparish"):
            pending: list[object] = [json.loads(raw.cached(SOURCE, f"{LA}{day}/{name}.htm").path.read_text())]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    if item.get("ID") in races and "Choice" in item:
                        for choice in item["Choice"]:
                            if "Desc" in choice:
                                answers[(item["ID"], choice["ID"])] = choice["Desc"]
                            else:
                                totals[(item["ID"], choice["ID"])] = int(choice["VoteTotal"])
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)
        for race, title in races.items():
            match = re.match(r"(?i)CA\s*(?:NO\.|#)\s*(\d+)", title)
            if match is None:
                raise ValueError(f"{day} {race}: no amendment number in {title!r}")
            url = f"{LA}{day}/VotesRaceByParish/Votes_{race}.htm"
            parishes = json.loads(raw.cached(SOURCE, url).path.read_text())["Parishes"]["Parish"]
            for (key, choice), label in answers.items():
                if key != race:
                    continue
                answer = {"FOR": "YES", "AGAINST": "NO"}[label.strip().upper()]
                found = {
                    f"22{(int(parish['ParishValue']) - 1) * 2 + 1:03}": int(entry["VoteTotal"])
                    for parish in parishes
                    for entry in parish["Choice"]
                    if entry["ID"] == choice
                }
                if len(found) != 64 or sum(found.values()) != totals[(race, choice)]:
                    raise ValueError(f"{url} {label}: {len(found)} parishes, {sum(found.values())} votes")
                rows.extend(
                    {
                        "state": "LA",
                        "day": date(int(day[:4]), int(day[4:6]), int(day[6:])),
                        "number": match[1],
                        "label": "",
                        "url": url,
                        "county": county,
                        "answer": answer,
                        "votes": value,
                    }
                    for county, value in found.items()
                )
    return rows


def washington() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    found: list[tuple[date, str, str, dict[str, dict[str, int]], dict[str, int]]] = []
    for day, (stamp, election) in WA_LISTS.items():
        text = raw.cached(
            SOURCE, WA.format(stamp) + f"results_report.aspx?e={election}&c=&c2=&t=&t2=5&p=&p2=&y="
        ).path.read_text(errors="replace")
        start = 0
        for link in re.finditer(rf"county_breakdown\.aspx\?e={election}&(?:amp;)?t=(\d+)", text):
            cells = [
                cell.strip()
                for cell in html.unescape(re.sub(r"<[^>]+>", "|", text[start : link.start()])).split("|")
                if cell.strip()
            ]
            start = link.end()
            heads = [
                match[1]
                for cell in cells
                if (
                    match := re.fullmatch(
                        r"(?:[A-Za-z]+ ){0,4}(?:Measure|Resolution|Bill|People)(?: No\.)? ?(\d+)", cell
                    )
                )
            ]
            totals = {
                WA_ANSWERS[cell]: int(cells[index + 1])
                for index, cell in enumerate(cells[:-1])
                if cell in WA_ANSWERS and cells[index + 1].isdigit()
            }
            url = WA.format("2016") + f"results_report_county_breakdown.aspx?e={election}&t={link[1]}"
            tokens = [
                cell.strip()
                for cell in html.unescape(re.sub(r"<[^>]+>", "|", raw.cached(SOURCE, url).path.read_text())).split("|")
                if cell.strip()
            ]
            votes: dict[str, dict[str, int]] = {}
            county = None
            for index, token in enumerate(tokens):
                if token in WA_COUNTIES:
                    county = token
                elif match := re.fullmatch(r"(Yes|No|Approved|Rejected)\s+(\d+)", token):
                    votes.setdefault(WA_ANSWERS[match[1]], {})[county or ""] = int(match[2])
                elif token in WA_ANSWERS and index + 1 < len(tokens) and tokens[index + 1].isdigit():
                    votes.setdefault(WA_ANSWERS[token], {})[county or ""] = int(tokens[index + 1])
            if not heads:
                raise ValueError(f"{url}: no measure heading")
            found.append((day, WA_BILLS.get(heads[-1], heads[-1]), url, votes, totals))
    for day in WA_DATES:
        index = raw.cached(SOURCE, f"{WA_RESULTS}{day}/measures-all.html").path.read_text()
        for page in sorted(set(re.findall(r'href="([^"]+_bycounty\.html)"', index, re.IGNORECASE))):
            url = f"{WA_RESULTS}{day}/{page.lower()}"
            text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, url).path.read_text())).split())
            answers = "|".join(WA_ANSWERS)
            votes = {}
            totals = {}
            for match in re.finditer(
                rf"(Total|[A-Z][a-z]+(?: (?!County\b)[A-Z][a-z]+)?) (?:County )?[\d.]+% ({answers}) ([\d,]+) [\d.]+% ({answers}) ([\d,]+)",
                text,
            ):
                for answer, value in ((match[2], match[3]), (match[4], match[5])):
                    if match[1] == "Total":
                        totals.setdefault(WA_ANSWERS[answer], int(value.replace(",", "")))
                    elif match[1] in WA_COUNTIES:
                        votes.setdefault(WA_ANSWERS[answer], {})[match[1]] = int(value.replace(",", ""))
            number = re.search(r"(?:measure|resolution|bill)-(\d+)", page, re.IGNORECASE)
            if number is None:
                raise ValueError(f"{url}: no measure number")
            found.append((date(int(day[:4]), int(day[4:6]), int(day[6:])), number[1], url, votes, totals))
    for day, number, url, votes, totals in found:
        for answer in ("YES", "NO"):
            counts = votes.get(answer, {})
            if set(counts) != set(WA_COUNTIES) or sum(counts.values()) != totals.get(answer):
                raise ValueError(
                    f"{url} {answer}: {len(counts)} counties, {sum(counts.values())} votes, total {totals.get(answer)}"
                )
            rows.extend(
                {
                    "state": "WA",
                    "day": day,
                    "number": number,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, value in counts.items()
            )
    return rows


def oklahoma() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in OK_FILES:
        url = OK + path
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.replace("\f", "\n")
        votes: dict[tuple[date, str], dict[str, tuple[int, int]]] = {}
        totals: dict[tuple[date, str], tuple[int, int]] = {}
        day = None
        for number, body in itertools.batched(re.split(r"STATE QUESTION (?:NO\. )?(\d+)", text)[1:], 2):
            if when := re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", body[:400]):
                day = datetime.strptime(when[1], "%B %d, %Y").date()
            elif when := re.search(r"FOR (\d{2}/\d{2}/\d{4})", text):
                day = datetime.strptime(when[1], "%m/%d/%Y").date()
            if day is None:
                raise ValueError(f"{url} {number}: no election date")
            found = votes.setdefault((day, number), {})
            for match in re.finditer(r"(?m)^([A-Z][A-Za-z.]+(?: [A-Z]+)?)\s+([\d,]+)\s+([\d,]+)\s+[\d,]+\s*$", body):
                found.setdefault(match[1], (int(match[2].replace(",", "")), int(match[3].replace(",", ""))))
            if total := re.search(r"STATE TOTAL:\s+([\d,]+)\s+([\d,]+)", body):
                totals[(day, number)] = (int(total[1].replace(",", "")), int(total[2].replace(",", "")))
        for (day, number), found in votes.items():
            sums = (sum(yes for yes, _ in found.values()), sum(no for _, no in found.values()))
            if len(found) != 77 or sums != totals.get((day, number)):
                raise ValueError(
                    f"{url} {number}: {len(found)} counties, {sums} votes, total {totals.get((day, number))}"
                )
            rows.extend(
                {
                    "state": "OK",
                    "day": day,
                    "number": number,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, (yes, no) in found.items()
                for answer, value in (("YES", yes), ("NO", no))
            )
    return rows


def florida() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in FL_DAYS:
        url = fl_sos.EXTRACT.format(day=day)
        found = (
            pl.read_csv(
                raw.cached(fl_sos.SOURCE, url).path,
                separator="\t",
                infer_schema=False,
                quote_char=None,
                encoding="utf8-lossy",
            )
            .filter(pl.col("RaceCode").str.contains(r"^A\d+$"))
            .select(
                number=pl.col("RaceCode").str.slice(1).cast(pl.Int32).cast(pl.String),
                label=pl.col("OfficeDesc").str.strip_chars(),
                county=pl.col("CountyName").str.strip_chars(),
                answer=pl.col("CanNameLast").str.to_uppercase().str.extract(r"^(YES|NO)\b"),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
        )
        for (number, label), measure in found.group_by("number", "label", maintain_order=True):
            if measure["county"].n_unique() < 67:
                continue
            if measure.height != 134 or set(measure["answer"]) != {"YES", "NO"}:
                raise ValueError(f"{url} {number}: {measure.height} county rows")
            rows.extend(
                {"state": "FL", "day": day, "number": number, "label": label, "url": url, **row}
                for row in measure.select("county", "answer", "votes").iter_rows(named=True)
            )
    return rows


def ohio() -> list[dict[str, object]]:
    issues = [f"STATE_ISSUE_{number}_{answer}" for number in "2345" for answer in ("YES", "NO")]
    counties = (
        pl.read_csv(raw.cached(gov_counties.SOURCE, gov_counties.OH_2006).path, infer_schema_length=0)
        .group_by("COUNTY_NAME")
        .agg(pl.col(column).cast(pl.Int64).sum() for column in issues)
    )
    if counties.height != 88:
        raise ValueError(f"{gov_counties.OH_2006}: {counties.height} counties")
    return [
        {
            "state": "OH",
            "day": date(2006, 11, 7),
            "number": column.split("_")[2],
            "label": "",
            "url": gov_counties.OH_2006,
            "county": row["COUNTY_NAME"],
            "answer": column.split("_")[3],
            "votes": row[column],
        }
        for row in counties.iter_rows(named=True)
        for column in issues
    ]


def nevada() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, (stamp, folder) in NV_PAGES.items():
        url = NV.format(stamp, folder)
        page = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
        page = re.sub(r"(?is)<(script|style).*?</\1>", "", page)
        tokens = [
            text for cell in re.split(r"<[^>]+>", page) if (text := re.sub(r"\s+", " ", html.unescape(cell)).strip())
        ]
        number = None
        found: dict[str, dict[str, list[int]]] = {}
        for at, token in enumerate(tokens):
            if heading := re.fullmatch(r"(?i)(?:state |ballot )?question(?: no\.)? ?(\d+)", token):
                number = heading[1]
            elif number is not None and token.upper() in ("YES", "NO") and token.upper() not in found.get(number, {}):
                cells = [int(cell.replace(",", "")) for cell in tokens[at + 2 : at + 20]]
                if sum(cells[1:]) != cells[0]:
                    raise ValueError(
                        f"{url} question {number} {token}: counties add up to {sum(cells[1:])}, not {cells[0]}"
                    )
                found.setdefault(number, {})[token.upper()] = cells[1:]
        for number, answers in found.items():
            if set(answers) != {"YES", "NO"}:
                raise ValueError(f"{url} question {number}: {sorted(answers)}")
            rows.extend(
                {
                    "state": "NV",
                    "day": day,
                    "number": number,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": votes,
                }
                for answer, values in answers.items()
                for county, votes in zip(NV_COUNTIES, values, strict=True)
            )
    return rows


def arkansas() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, name in AR_BOOKS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, AR + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.replace("\f", "\n")
        totals: dict[tuple[str, str], tuple[int, int]] = {}
        counties: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
        if day.year == 2000:
            for kind, number, yes, no, body in re.findall(
                r"(?m)^(Amendment|Initiated Act) (\d+)\s+Yes \[(\d+) total votes[^\n]*\s+No \[(\d+) total votes[^\n]*\n"
                r"((?:.*\n)*?)(?=^(?:Amendment|Initiated Act) \d+\s*$)",
                text,
            ):
                if (kind, number) in totals:
                    break
                totals[(kind, number)] = (int(yes), int(no))
                table = counties.setdefault((kind, number), {})
                for county, a, b in re.findall(r"(?m)^\s*(.+?) County\s+(\d+)\s+(\d+)\s*$", body):
                    table.setdefault(county, (int(a), int(b)))
        else:
            current: tuple[str, str, str | None] | None = None
            seen: dict[str, int] = {}
            for line in text.splitlines():
                if heading := re.match(
                    r"^(?:Proposed )?(Constitutional Amendment|Initiated Act|Referred Question) No\. (\d+)"
                    r"(?: \((.+?) County\))?\s*$",
                    line,
                ):
                    if not (heading[3] is None and current is not None and current[:2] == heading.groups()[:2]):
                        current, seen = (heading[1], heading[2], heading[3]), {}
                elif current is not None and (side := re.match(r"^\s*(For|Against)\s+([\d,]+)", line)):
                    seen[side[1]] = int(side[2].replace(",", ""))
                    if len(seen) == 2:
                        kind, number, county = current
                        votes = (seen["For"], seen["Against"])
                        if county is None:
                            totals.setdefault((kind, number), votes)
                        else:
                            counties.setdefault((kind, number), {})[county] = votes
                        current = None
        for (kind, number), total in totals.items():
            if total == (0, 0):
                continue
            found = counties.get((kind, number), {})
            summed = tuple(sum(votes[side] for votes in found.values()) for side in (0, 1))
            if len(found) != 75 or summed != total:
                raise ValueError(f"{name} {kind} {number}: {len(found)} counties add up to {summed}, not {total}")
            rows.extend(
                {
                    "state": "AR",
                    "day": day,
                    "number": number,
                    "label": kind if kind in ("Initiated Act", "Referred Question") else "Amendment",
                    "url": AR + name,
                    "county": county,
                    "answer": answer,
                    "votes": votes[side],
                }
                for county, votes in found.items()
                for side, answer in enumerate(("YES", "NO"))
            )
    for day, name in AR_VOICES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, AR + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for kind, number, body, total in re.findall(
            r"(?m)^\s*(?:Proposed )?(Constitutional Amendment|Initiated Act|Referred Question) (\d+)\s*$"
            r"((?:.*\n)*?)^\s*Totals\s+([\d,]+\s+[\d,]+)",
            text,
        ):
            found: dict[str, tuple[int, int]] = {}
            waiting: list[str] = []
            figures: list[int] = []
            for name_, figure in re.findall(r"([A-Z][A-Za-z.]*(?: [A-Z][A-Za-z.]*)*) County|(\d[\d,]*)", body):
                if name_:
                    if figures:
                        raise ValueError(f"{name} {kind} {number}: {name_} County interrupts {waiting}")
                    waiting.append(name_)
                elif waiting:
                    figures.append(int(figure.replace(",", "")))
                    if len(figures) == 3 * len(waiting):
                        for at, county in enumerate(waiting):
                            yes, no, both = figures[at :: len(waiting)]
                            if yes + no != both:
                                raise ValueError(f"{name} {kind} {number} {county}: {yes} and {no} are not {both}")
                            found[county] = (yes, no)
                        waiting, figures = [], []
            printed = tuple(int(part.replace(",", "")) for part in total.split())
            summed = tuple(sum(votes[side] for votes in found.values()) for side in (0, 1))
            if len(found) != 75 or summed != printed:
                raise ValueError(f"{name} {kind} {number}: {len(found)} counties add up to {summed}, not {printed}")
            rows.extend(
                {
                    "state": "AR",
                    "day": day,
                    "number": number,
                    "label": {"Constitutional Amendment": "Amendment", "Initiated Act": "Initiative Act"}.get(
                        kind, kind
                    ),
                    "url": AR + name,
                    "county": county,
                    "answer": answer,
                    "votes": votes[side],
                }
                for county, votes in found.items()
                for side, answer in enumerate(("YES", "NO"))
            )
    for day, name in AR_PUBLICATIONS.items():
        with tempfile.TemporaryDirectory() as folder:
            stored = raw.cached(SOURCE, AR + name).path
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", folder, str(stored)],
                capture_output=True,
                check=True,
            )
            book = fastexcel.read_excel(Path(folder) / f"{stored.stem}.xlsx")
            for label in ("Proposed Amendment", "Referred Question"):
                table = book.load_sheet_by_name(label, header_row=None).to_polars()
                number = table.row(0)[1].rsplit(" ", 1)[1]
                found = {
                    county.removesuffix(" County"): (int(yes), int(no))
                    for county, yes, no, *_ in table.rows()
                    if county is not None and county.endswith(" County")
                }
                total = next((int(yes), int(no)) for county, yes, no, *_ in table.rows() if county == "Totals")
                summed = tuple(sum(votes[side] for votes in found.values()) for side in (0, 1))
                if len(found) != 75 or summed != total:
                    raise ValueError(f"{name} {label}: {len(found)} counties add up to {summed}, not {total}")
                rows.extend(
                    {
                        "state": "AR",
                        "day": day,
                        "number": number,
                        "label": "Amendment" if label == "Proposed Amendment" else label,
                        "url": AR + name,
                        "county": county,
                        "answer": answer,
                        "votes": votes[side],
                    }
                    for county, votes in found.items()
                    for side, answer in enumerate(("YES", "NO"))
                )
    for day, name in AR_SHEETS.items():
        sheet = (
            fastexcel.read_excel(raw.cached(SOURCE, AR + name).path)
            .load_sheet_by_name("Ballot Measures", dtypes={"Votes": "int"})
            .to_polars()
            .group_by("Race", "County", "Candidate", maintain_order=True)
            .agg(pl.col("Votes").sum())
        )
        if sheet["County"].n_unique() != 75 or set(sheet["Candidate"]) != {"For", "Against"}:
            raise ValueError(f"{name}: unexpected counties or answers")
        rows.extend(
            {
                "state": "AR",
                "day": day,
                "number": race.rsplit(" ", 1)[1],
                "label": "Amendment",
                "url": AR + name,
                "county": county.removesuffix(" County"),
                "answer": "YES" if answer == "For" else "NO",
                "votes": votes,
            }
            for race, county, answer, votes in sheet.iter_rows()
        )
    return rows


def south_dakota() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, name in SD_SHEETS.items():
        sheet = (
            fastexcel.read_excel(raw.cached(SOURCE, SD + name).path)
            .load_sheet_by_name("Ballot Questions", header_row=None)
            .to_polars()
            .rows()
        )
        columns: list[str] = []
        for head in sheet[1][1:]:
            columns.append(
                columns[-1]
                if head is None
                else head
                if head.startswith("Initiated")
                else f"Constitutional Amendment {head}"
            )
        answers = [str(cell).upper() for cell in sheet[2][1:]]
        counties = [row for row in sheet[3:] if row[0] is not None and row[0] not in ("TOTALS", "PERCENTAGE")]
        (totals,) = [row for row in sheet if row[0] == "TOTALS"]
        if len(counties) != 66:
            raise ValueError(f"{name}: {len(counties)} counties")
        for at, (label, answer) in enumerate(zip(columns, answers, strict=True), 1):
            if sum(int(row[at]) for row in counties) != int(totals[at]):
                raise ValueError(f"{name} {label} {answer}: counties do not add up to {totals[at]}")
            rows.extend(
                {
                    "state": "SD",
                    "day": day,
                    "number": label.rsplit(" ", 1)[1],
                    "label": label,
                    "url": SD + name,
                    "county": row[0],
                    "answer": answer,
                    "votes": int(row[at]),
                }
                for row in counties
            )
    for day, name in SD_PRECINCTS.items():
        sheet = (
            fastexcel.read_excel(raw.cached(SOURCE, SD + name).path)
            .load_sheet(0, dtypes={"Votes": "int"})
            .to_polars()
            .with_columns(label=pl.col("Ballot Question").str.extract(r"^([^:]+):"))
            .group_by("label", "County", "Yes/No", maintain_order=True)
            .agg(pl.col("Votes").sum())
        )
        if sheet["County"].n_unique() != 66 or set(sheet["Yes/No"]) != {"Yes", "No"}:
            raise ValueError(f"{name}: unexpected counties or answers")
        rows.extend(
            {
                "state": "SD",
                "day": day,
                "number": label.rsplit(" ", 1)[1],
                "label": label,
                "url": SD + name,
                "county": county,
                "answer": answer.upper(),
                "votes": votes,
            }
            for label, county, answer, votes in sheet.iter_rows()
        )
    for name, url in SD_SCANS.items():
        contests = json.loads((SCANS / f"{name}.json").read_text())["contests"]
        if not (DATA / "parsed" / "us.scans" / name / "tables.parquet").exists():
            print(f"{name}: not read yet, skipped")
            continue
        scanned = pl.read_parquet(DATA / "parsed" / "us.scans" / name / "tables.parquet")
        for number, contest in contests.items():
            table = scanned.filter(pl.col("contest") == number)
            if sorted(table["candidate"].unique()) != ["NO", "YES"] or table.height != 132:
                print(f"{name} measure {number}: {table.height} cells passed, skipped")
                continue
            rows.extend(
                {
                    "state": "SD",
                    "day": date.fromisoformat(contest["day"]),
                    "number": number,
                    "label": f"Initiated Measure {number}"
                    if number.isdigit()
                    else f"Constitutional Amendment {number}",
                    "url": url,
                    "county": row["code"],
                    "answer": row["candidate"],
                    "votes": row["votes"],
                    "page": row["page"],
                    "x0": row["x0"],
                    "y0": row["y0"],
                    "x1": row["x1"],
                    "y1": row["y1"],
                }
                for row in table.iter_rows(named=True)
            )
    return rows


def nebraska() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, name in NE_BOOKS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, NE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        number = None
        table = ""
        tables: dict[str, dict[str, tuple[int, int]]] = {}
        totals: dict[str, tuple[int, int]] = {}
        reading = None
        for line in text.splitlines():
            if heading := NE_HEADING.match(line):
                number, reading = heading[1], None
            elif header := re.match(r"^\s*County\s+For\s+Against\b", line):
                if number is None or number in tables:
                    raise ValueError(f"{name}: a county table with no new measure heading ({number})")
                table, reading = number, tables.setdefault(number, {})
                line = line[header.end() :]
            if reading is not None:
                for county, yes, no in re.findall(
                    r"([A-Z][A-Za-z.]*(?: [A-Z][A-Za-z.]*)*)\s+([\d,]+)\s+([\d,]+)", line
                ):
                    votes = (int(yes.replace(",", "")), int(no.replace(",", "")))
                    if county.upper() == "TOTAL":
                        totals[table] = votes
                    else:
                        reading[county] = votes
                if len(reading) == 93 and table in totals:
                    reading = None
        for number, counties in tables.items():
            summed = tuple(sum(votes[side] for votes in counties.values()) for side in (0, 1))
            if len(counties) != 93 or summed != totals.get(number):
                raise ValueError(
                    f"{name} measure {number}: {len(counties)} counties add up to {summed}, not {totals.get(number)}"
                )
            rows.extend(
                {
                    "state": "NE",
                    "day": day,
                    "number": number,
                    "label": "",
                    "url": NE + name,
                    "county": county,
                    "answer": answer,
                    "votes": votes[side],
                }
                for county, votes in counties.items()
                for side, answer in enumerate(("YES", "NO"))
            )
    for name, path in NE_SCANS.items():
        contests = json.loads((SCANS / f"{name}.json").read_text())["contests"]
        scanned = pl.read_parquet(DATA / "parsed" / "us.scans" / name / "tables.parquet")
        for number, contest in contests.items():
            table = scanned.filter(pl.col("contest") == number)
            if sorted(table["candidate"].unique()) != ["NO", "YES"] or table.height != 186:
                print(f"{name} measure {number}: {table.height} cells passed, skipped")
                continue
            rows.extend(
                {
                    "state": "NE",
                    "day": date.fromisoformat(contest["day"]),
                    "number": number,
                    "label": "",
                    "url": NE + path,
                    "county": row["code"],
                    "answer": row["candidate"],
                    "votes": row["votes"],
                    "page": row["page"],
                    "x0": row["x0"],
                    "y0": row["y0"],
                    "x1": row["x1"],
                    "y1": row["y1"],
                }
                for row in table.iter_rows(named=True)
            )
    return rows


def rhode_island() -> list[dict[str, object]]:
    def town(text: str) -> str:
        text = re.sub(
            r"^([nsew])\.\s*", lambda m: {"n": "north ", "s": "south ", "e": "east ", "w": "west "}[m[1]], text.lower()
        )
        return re.sub(r"[^a-z]", "", text)

    towns = {town(name): fips for fips, names in ri_countbook.COUNTIES.items() for name in names}
    rows: list[dict[str, object]] = []
    for day, name in RI_BOOKS.items():
        url = ri_countbook.BASE + name
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(ri_countbook.SOURCE, url).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for chunk in re.split(r"STATE REFERENDA RESULTS|STATE REFERENDUM QUESTIONS", text)[1:]:
            lines = chunk.splitlines()
            if span := re.match(r":?\s*Questions?\s*(\d+)\s*[-—–]\s*(\d+)\b(?!.*(?:…|\.{4}))", lines[0]):
                numbers = [str(n) for n in range(int(span[1]), int(span[2]) + 1)]
            elif lines[0].strip() or re.search(r"…|\.{4}", chunk[:200]):
                continue
            else:
                numbers = re.findall(r"Question (\d+)", "\n".join(lines[:6]))
            if not numbers:
                continue
            width = 2 * len(numbers)
            counts: dict[str, list[int]] = {}
            stacked: list[str] = []
            buffer: list[int] = []
            total = None
            for line in lines[1:]:
                cells = re.findall(r"\d[\d,]*", line)
                label = town(
                    re.sub(r"[\d,;.:'!|]+\s*$|\s[\d,;.]+.*$", "", re.sub(r"^(?:[I|!l:~.]\s+)+", "", line.strip()))
                )
                if label in ("statetotal", "total", "totals"):
                    total = [int(cell.replace(",", "")) for cell in cells]
                    break
                if label == "limiteddistrict" and len(cells) == width + 1:
                    previous = counts.get("limited", [0] * width)
                    counts["limited"] = [a + int(b.replace(",", "")) for a, b in zip(previous, cells[1:], strict=True)]
                elif label in towns and not cells:
                    stacked.append(label)
                elif label in towns and len(cells) == width:
                    counts[label] = [int(cell.replace(",", "")) for cell in cells]
                elif stacked and not label and len(cells) == 1:
                    buffer.append(int(cells[0].replace(",", "")))
                    if len(buffer) == width * len(stacked):
                        for at, held in enumerate(stacked):
                            counts[held] = buffer[at :: len(stacked)]
                        stacked, buffer = [], []
            sums = [sum(values[column] for values in counts.values()) for column in range(width)]
            if len(counts.keys() - {"limited"}) != 39 or sums != total:
                raise ValueError(f"{name} questions {numbers}: {len(counts)} towns, {sums} against {total}")
            for at, number in enumerate(numbers):
                for fips in [*ri_countbook.COUNTIES, *(["limited"] if "limited" in counts else [])]:
                    for answer, column in (("YES", 2 * at), ("NO", 2 * at + 1)):
                        rows.append(
                            {
                                "state": "RI",
                                "day": day,
                                "number": number,
                                "label": "",
                                "url": url,
                                "county": fips,
                                "answer": answer,
                                "votes": sum(
                                    values[column] for held, values in counts.items() if towns.get(held, held) == fips
                                ),
                            }
                        )
    return rows


def oregon() -> list[dict[str, object]]:
    listed = ncsl().filter(pl.col("state") == "OR")
    words = re.compile(r"[a-z]{4,}")
    rows: list[dict[str, object]] = []
    for day, urls in OR_ABSTRACTS.items():
        pool = listed.filter(
            pl.col("year") == day.year,
            pl.col("election") == {11: "General", 5: "Primary"}.get(day.month, "Special"),
        )
        tables: dict[str, dict[tuple[tuple[str, int, int], ...], str]] = {}
        texts: dict[str, str] = {}
        for url in urls:
            path = raw.cached(SOURCE, url).path
            if url.endswith(".htm"):
                text = html.unescape(
                    re.sub(
                        r"<[^>]+>",
                        " ",
                        re.sub(r"(?is)<(style|script|xml)\b.*?</\1>", " ", path.read_text(errors="replace")),
                    )
                )
                texts[url] = re.sub(
                    rf"\b({'|'.join(OR_COUNTIES)}|Total|TOTAL|Return)\b", "\n\\1", " ".join(text.split())
                )
            else:
                texts[url] = subprocess.run(
                    ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
                ).stdout
        for url, printed, body in (
            (url, *pair)
            for url, text in texts.items()
            for pair in itertools.batched(re.split(r"STATE MEASURE \S+\.? *(\S+)", text)[1:], 2)
        ):
            header = re.search(r"County\s+\S*Yes\s+\S*No\b", body)
            if header is None:
                continue
            mine = set(words.findall(" ".join(body[: header.start()].split()).lower()))
            scored = sorted(
                (
                    (len(mine & set(words.findall(title.lower()))) / len(set(words.findall(title.lower()))), number)
                    for number, title in pool.select("number", "title").rows()
                ),
                reverse=True,
            )
            score = {number: overlap for overlap, number in scored}
            if score.get(printed, 0) >= 0.6:
                scored.insert(0, (score[printed], printed))
            elif not scored or scored[0][0] < 0.6 or len(scored) > 1 and scored[0][0] == scored[1][0]:
                continue
            votes: dict[str, tuple[int, int]] = {}
            total = None
            for line in body[header.end() :].splitlines():
                match = re.match(r"\s*(TOTAL|[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s(.*)", line)
                counts = (
                    [int(re.sub(r"\D", "", count)) for count in re.findall(r"\d[\d,.;]*", match[2])] if match else []
                )
                counts = counts[-2:] if len(counts) == 4 else counts
                if match is None or len(counts) != 2:
                    continue
                if match[1].upper() == "TOTAL":
                    total = (counts[0], counts[1])
                    break
                if county := difflib.get_close_matches(match[1], OR_COUNTIES, n=1, cutoff=0.7):
                    votes.setdefault(county[0], (counts[0], counts[1]))
            tied = len(votes) == len(OR_COUNTIES) and total == (
                sum(yes for yes, _ in votes.values()),
                sum(no for _, no in votes.values()),
            )
            found = tables.setdefault(scored[0][1], {})
            if tied:
                found.setdefault(tuple((county, yes, no) for county, (yes, no) in sorted(votes.items())), url)
        for number, found in tables.items():
            if len(found) != 1:
                raise ValueError(f"{day} Measure {number}: {len(found)} county tables that tie the total")
            ((table, url),) = found.items()
            rows.extend(
                {
                    "state": "OR",
                    "day": day,
                    "number": number,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, yes, no in table
                for answer, value in (("YES", yes), ("NO", no))
            )
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    listed = ncsl()
    frame = pl.DataFrame(
        [
            *texas(),
            *california(),
            *arizona(),
            *alabama(),
            *maine(),
            *louisiana(),
            *washington(),
            *oregon(),
            *florida(),
            *oklahoma(),
            *ohio(),
            *rhode_island(),
            *nevada(),
            *nebraska(),
            *arkansas(),
            *south_dakota(),
        ],
        infer_schema_length=None,
    )
    words = re.compile(r"[a-z]{4,}")
    matched: list[dict[str, object]] = []
    for (state, day, number, label), _ in frame.group_by("state", "day", "number", "label", maintain_order=True):
        mine = set(words.findall(label.lower()))
        pool = listed.filter(pl.col("state") == state, pl.col("year") == day.year)
        kind = (pl.col("election") == "General") == (day.month == 11)
        found = next(
            (
                rows
                for rows in (
                    pool.filter(kind, pl.col("measure") == label),
                    pool.filter(kind, pl.col("number") == number),
                    pool.filter(kind, pl.col("number").is_null()),
                    pool.filter(pl.col("number") == number),
                    pool.filter(pl.col("number").is_null()),
                )
                if rows.height
            ),
            pool.filter(kind) if label else pool.clear(),
        )
        if found.height != 1 or found["number"][0] is None and label:
            found = (
                found.with_columns(
                    overlap=pl.struct("title", "measure").map_elements(
                        lambda row, mine=mine: (
                            100 * len(mine & set(words.findall(row["title"].lower())))
                            + len(mine & set(words.findall((row["measure"] or "").lower())))
                        ),
                        return_dtype=pl.Int64,
                    )
                )
                .filter(pl.col("overlap") > 0)
                .sort("overlap", descending=True)
            )
            if found.height == 0 or found.height > 1 and found["overlap"][0] == found["overlap"][1]:
                raise ValueError(f"{state} {day} {number} {label!r}: {found.height} NCSL rows")
        entry = found.row(0, named=True)
        matched.append(
            {
                "state": state,
                "day": day,
                "number": number,
                "label": label,
                "measure": f"Question {number}"
                if state in ("ME", "NV", "RI")
                else entry["measure"]
                if entry["number"] or state == "SD"
                else f"Amendment No. {number}"
                if state == "LA"
                else f"Proposition {number}",
                "title": entry["title"],
                "type": entry["type"],
                "passed": entry["passed"],
            }
        )
    frame.write_parquet(PARSED / "counties.parquet")
    pl.DataFrame(matched).write_parquet(PARSED / "measures.parquet")
    print(f"{len(matched)} measures, {frame.height} county rows")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    measures = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot measures by county, 2000–2011, from state election offices' official returns",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ncsl_source = cs.source("National Conference of State Legislatures", "Statewide Ballot Measures Database")
        cs.add(
            "source",
            id=ncsl_source,
            kind="aggregator",
            publisher="National Conference of State Legislatures",
            title="Statewide Ballot Measures Database",
            url=NCSL_PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, NCSL_PAGE), ncsl_source)
        owned = {
            id
            for (id,) in conn.execute(
                "select s.id from source s join changeset c on c.id = s.changeset_id where c.pipeline = %s", [SOURCE]
            ).fetchall()
        }
        sources: dict[str, UUID] = {}
        for state in frame["state"].unique(maintain_order=True):
            publisher, title, url = PUBLISHERS[state]
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted or sources[state] in owned:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        artifacts: dict[str, UUID] = {}
        for state, url in frame.select("state", "url").unique(maintain_order=True).iter_rows():
            artifacts[url] = cs.artifact(
                raw.cached(
                    {"FL": fl_sos.SOURCE, "OH": gov_counties.SOURCE, "RI": ri_countbook.SOURCE}.get(state, SOURCE), url
                ),
                sources[state],
            )
        pages: dict[tuple[str, int], UUID] = {}
        for url, number in frame.filter(pl.col("page").is_not_null()).select("url", "page").unique().iter_rows():
            stored = raw.cached(SOURCE, url)
            image = DATA / "pages" / SOURCE / f"{stored.sha256}-{number}.png"
            if not image.exists():
                image.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-gray", "-png", "-singlefile", "-f", str(number), "-l", str(number)]
                    + [str(stored.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = Image.open(image).size
            pages[(url, number)] = cs.page(artifacts[url], number)
            cs.add(
                "source_page",
                id=pages[(url, number)],
                artifact_id=artifacts[url],
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        system = cs.key("us.electoral_system", "referendum")
        states: dict[str, tuple[UUID, str]] = {}
        fips: dict[str, str] = {}
        for abbreviation, area, code, text in conn.execute(
            """select substr(p.iso_code, 4), u.area_id, u.code, n.text from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               where u.scheme_code = 'us.state'"""
        ).fetchall():
            states[abbreviation] = (area, text)
            fips[abbreviation] = code
        counties: dict[tuple[str, str], str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id
               and n.kind in ('short', 'historical')
               where u.scheme_code = 'us.county' and (u.valid_from is null or u.valid_from <= '2011-12-31')
               and (u.valid_to is null or u.valid_to > '2000-01-01')
               order by n.kind = 'historical'"""
        ).fetchall():
            counties.setdefault(
                (code[:2], re.sub(r"[^a-z]", "", text.split(",")[0].removesuffix(" County").lower())), code
            )
        frame = frame.with_columns(
            code=pl.struct("state", "county").map_elements(
                lambda row: (
                    f"{row['state']}:{row['county']}"
                    if row["county"] in ("uocava", "limited")
                    else row["county"]
                    if row["county"].isdigit()
                    else counties.get((fips[row["state"]], re.sub(r"[^a-z]", "", row["county"].lower())))
                ),
                return_dtype=pl.String,
            )
        )
        missing = frame.filter(pl.col("code").is_null()).select("state", "county").unique()
        if missing.height:
            raise ValueError(f"unmatched counties: {missing.rows()}")
        for code in frame.filter(pl.col("code").str.contains(":"))["code"].unique():
            if conn.execute(
                "select 1 from reporting_unit where scheme_code = 'us.aggregate' and code = %s", [code]
            ).fetchone():
                continue
            polity, parent, area = conn.execute(
                """select p.id, u.id, u.area_id from polity p join polity_territory t on t.polity_id = p.id
                   join reporting_unit u on u.area_id = t.area_id and u.scheme_code = 'us.state' where p.iso_code = %s""",
                [f"US-{code.split(':')[0]}"],
            ).fetchone() or (None, None, None)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.aggregate", code),
                polity_id=polity,
                scheme_code="us.aggregate",
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=area,
                kind="aggregate",
            )
        elections = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and e.date_from between '2000-01-01' and '2011-12-31'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        paths = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election' and x.value ~ '^20(0[0-9]|1[01])-'"""
            ).fetchall()
        )
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        for entry in measures.iter_rows(named=True):
            state, day = entry["state"], entry["day"]
            rows = frame.filter(
                pl.col("state") == state,
                pl.col("day") == day,
                pl.col("number") == entry["number"],
                pl.col("label") == entry["label"],
            )
            election_key = next(
                (
                    key
                    for key in (f"{day}:{state}:special", f"{day}:{state}", f"{day}:{state}:primary")
                    if key in elections
                ),
                f"{day}:{state}:referendum",
            )
            election = cs.key("us.election", election_key)
            if election_key not in elections:
                elections[election_key] = election
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="referendum",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{states[state][1]} referendum of {day:%B} {day.day}, {day.year}")
                paths[election_key] = f"us/{state.lower()}/{day}-referendum"
                cs.add("slug", path=paths[election_key], entity_id=election)
            title = f"{states[state][1]} {entry['measure']}, {entry['title']}"
            contest_key = f"{day}:{state}:question:{slug(entry['measure'])}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=states[state][0],
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add(
                "ballot_question",
                contest_id=contest,
                type=TYPES.get(entry["type"], "plebiscite"),
            )
            name(contest, title)
            if contest in cs.minted:
                path = held.get(contest)
                if path is None:
                    base = f"{paths[election_key]}/{slug(title)[:60].rstrip('-') or 'question'}"
                    path, n = base, 1
                    while path in taken:
                        n += 1
                        path = f"{base}-{n}"
                    taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            options: dict[str, UUID] = {}
            for position, answer in enumerate(("YES", "NO")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=BALLOT_LABELS.get((state, day, entry["number"]), ("Yes", "No"))[position],
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHODOLOGY.format(publisher=PUBLISHERS[state][0])
                + (MAINE if state == "ME" else "")
                + (OVERSEAS if (rows["code"] == "ME:uocava").any() else "")
                + (LIMITED if (rows["code"] == "RI:limited").any() else "")
                + (SCANNED if rows["page"].is_not_null().any() else ""),
            )
            yes = int(rows.filter(pl.col("answer") == "YES")["votes"].sum())
            no = int(rows.filter(pl.col("answer") == "NO")["votes"].sum())
            passed = entry["passed"] != ((state, day, entry["number"]) in MISREPORTED)
            if passed != (yes > no):
                print(f"{contest_key}: NCSL says {'passed' if entry['passed'] else 'failed'} at {yes} to {no}")
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options["YES" if passed else "NO"],
                kind="won",
            )
            if result_set in cs.minted:
                for row in rows.iter_rows(named=True):
                    unit = cs.unit("us.aggregate" if ":" in row["code"] else "us.county", row["code"])
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=options[row["answer"]],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=row["votes"],
                    )
                    if row["page"] is not None:
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=options[row["answer"]],
                            channel=0,
                            measure=1,
                            count_round=0,
                            page_id=pages[(row["url"], row["page"])],
                            x0=row["x0"],
                            y0=row["y0"],
                            x1=row["x1"],
                            y1=row["y1"],
                        )
        return cs.write()
