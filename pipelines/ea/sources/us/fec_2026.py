import csv
import html
import io
import json
import re
import subprocess
import zipfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid5

import fastexcel
import httpx
import polars as pl

from ea import raw, statewide_table
from ea.changeset import NAMESPACE, Changeset, slug
from ea.names import initials
from ea.store import DATA, connect

SOURCE = "us.fec_2026"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
CN26 = "https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"
BROWSER = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
FL_URL = "https://dos.elections.myflorida.com/candidates/extractCanList.asp?elecID=20261103-GEN"
OFFICIAL = {
    "FL": (
        "Florida Department of State, Division of Elections",
        "Florida 2026 general election candidate list",
        [FL_URL],
    ),
    "NC": (
        "North Carolina State Board of Elections",
        "North Carolina 2026 candidate listing",
        ["https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2026/Candidate%20Filing/Candidate_Listing_2026.csv"],
    ),
    "WY": (
        "Wyoming Secretary of State",
        "2026 Wyoming general election candidates",
        ["https://sos.wyo.gov/Elections/Docs/2026/2026_WY_General_Election_Candidates.csv"],
    ),
    "ME": (
        "Maine Department of the Secretary of State",
        "2026 general election candidate list",
        [
            "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/2026%20General%20Candidate%20List%20-%20FINAL.xlsx"
        ],
    ),
    "VT": (
        "Vermont Secretary of State",
        "2026 general election qualified candidates",
        [
            "https://outside.vermont.gov/dept/sos/Elections_Division/election_info_resources/candidates/2026_general_election_qualified_candidates.xlsx"
        ],
    ),
    "DE": (
        "Delaware Department of Elections",
        "2026 general election candidate list",
        ["https://elections.delaware.gov/candidates/candidatelist/genl_fcddt_2026.html"],
    ),
    "MT": (
        "Montana Secretary of State",
        "Federal general 2026 candidate list",
        ["https://candidatefiling.mt.gov/candidatefiling/CandidateList.aspx?e=450002987#csv"],
    ),
    "WA": (
        "Washington Secretary of State",
        "2026 general election candidate list",
        ["https://voter.votewa.gov/CandidateList.aspx?e=899#csv"],
    ),
    "SD": (
        "South Dakota Secretary of State",
        "2026 general election candidate list",
        ["https://vip.sdsos.gov/candidatelist.aspx?eid=774#browser"],
    ),
    "NJ": (
        "New Jersey Division of Elections",
        "Official list of candidates for the 2026 general election",
        [
            "https://www.nj.gov/state/elections/assets/pdf/election-results/2026/2026-official-general-candidates-us-house-0904.pdf",
            "https://www.nj.gov/state/elections/assets/pdf/election-results/2026/2026-official-general-candidates-us-senate.pdf",
        ],
    ),
    "NE": (
        "Nebraska Secretary of State",
        "Final statewide general candidate list, November 3, 2026 general election",
        [
            "https://sos.nebraska.gov/sites/default/files/doc/elections/2026/Final_Statewide_General_Candidate_Filing_List_9.11.26.pdf"
        ],
    ),
    "UT": (
        "Utah Lieutenant Governor",
        "Certification of candidates for the 2026 general election (amended)",
        ["https://www.utah.gov/pmn/files/1492833.pdf"],
    ),
    "DC": (
        "District of Columbia Board of Elections",
        "2026 general election candidates",
        [
            "https://dcboe.org/getContentAsset/1029b813-fb27-4055-9218-fbe40360aacb/5ebbd046-860b-4ee1-81c8-43d0e3bc446e/2026-General-CANDIDATES-07152026.pdf?language=en"
        ],
    ),
    "TX": (
        "Texas Secretary of State",
        "Ballot certification report, 2026 November general election",
        ["https://www.sos.texas.gov/elections/forms/2026-ballot-cert.pdf"],
    ),
    "CA": (
        "California Secretary of State",
        "Official certified list of candidates, general election, November 3, 2026",
        ["https://elections.cdn.sos.ca.gov/statewide-elections/2026-general/cert-list-candidates.pdf"],
    ),
    "MO": (
        "Missouri Secretary of State",
        "Certification of candidates and party emblems, general election, November 3, 2026",
        ["https://www.sos.mo.gov/CMSImages/ElectionCandidates/2026GeneralElectionCertifiedCandidates.pdf"],
    ),
    "TN": (
        "Tennessee Secretary of State",
        "2026 candidate lists: November 3, 2026 state and federal general election",
        [
            "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/Governor_Nov2026.xlsx",
            "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/USSenate_Nov2026.xlsx",
            "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/USHouse_Nov2026.xlsx",
        ],
    ),
    "AL": (
        "Alabama Secretary of State",
        "State certification of Republican and Democratic candidates, 2026 general election",
        [
            "https://www.sos.alabama.gov/sites/default/files/election-2026/2026GeneralElectionStateCertificationofRepublicanCandidates.pdf",
            "https://www.sos.alabama.gov/sites/default/files/election-2026/2026GeneralElectionStateCertificationofDemocraticCandidates.pdf",
        ],
    ),
    "SC": (
        "South Carolina State Election Commission",
        "Candidate listing, statewide general election, November 3, 2026",
        ["https://vrems.scvotes.sc.gov/Candidate/CandidateSearchDate?electionDate=11%2F03%2F2026#browser"],
    ),
    "KY": (
        "Kentucky Secretary of State",
        "Candidate filings, 2026 general election",
        ["https://web.sos.ky.gov/candidatefilings/Default.aspx?id=3#excel"],
    ),
    "LA": (
        "Louisiana Secretary of State",
        "Candidate inquiry, November 3, 2026 election",
        ["https://voterportal.sos.la.gov/CandidateInquiry?electionDate=20261103#browser"],
    ),
    "AK": (
        "Alaska Division of Elections",
        "Official election pamphlet, November 3, 2026 general election",
        [
            "https://www.elections.alaska.gov/election/2026/General/OEPbooks/2026%20Alaska%20General%20VP_Region%201_ENG_web.pdf",
            "https://www.elections.alaska.gov/election-candidates/#candidates",
        ],
    ),
    "CT": (
        "Connecticut Secretary of the State",
        "Election Management System, November 2026 State Election",
        [
            "https://ctemspublic.tgstg.net/ng-app/data/election/108/49421/Lookupdata.json",
            "https://ctemspublic.tgstg.net/ng-app/data/election/108/49421/stateVotes_Electiondata.json",
        ],
    ),
    "OK": (
        "Oklahoma State Election Board",
        "November 2026 list of elections",
        ["https://hosting.okelections.gov/electionlist.html"],
    ),
    "NY": (
        "New York State Board of Elections",
        "Certification for the November 3, 2026 general election",
        [
            "https://elections.ny.gov/system/files/documents/2026/10/accessible-2026-general-ballot-certification-10.7.2026.pdf#MF-126"
        ],
    ),
    "OH": (
        "Ohio Secretary of State",
        "Directive 2026-45, form of the official ballot for the November 3, 2026 general election",
        ["https://www.ohiosos.gov/elections/ohio-candidates/#MF-127"],
    ),
    "MA": (
        "Massachusetts Secretary of the Commonwealth",
        "2026 state election candidates",
        [
            "https://www.sec.state.ma.us/divisions/elections/research-and-statistics/2026-state-election-candidates.htm#MF-128"
        ],
    ),
    "GA": (
        "Georgia Secretary of State",
        "November 2026 federal and state candidates",
        [
            "https://sos.ga.gov/sites/default/files/2026-09/November%202026%20Federal%20and%20State%20Candidates%202026.zip#MF-129"
        ],
    ),
    "AZ": (
        "Arizona Secretary of State",
        "2026 general election candidate listing",
        ["https://apps.arizona.vote/electioninfo/Election/69#MF-130"],
    ),
    "IA": (
        "Iowa Secretary of State",
        "Candidate list, November 3, 2026 general election",
        [
            "https://sos.iowa.gov/sites/default/files/2026-08/2026%20General%20-%20Candidate%20List%20Database%20-%20All%20Elections_1.pdf#MF-131"
        ],
    ),
    "NH": (
        "New Hampshire Secretary of State",
        "2026 general election candidate list",
        ["https://mm.nh.gov/files/uploads/sos/docs/2026-ge-candidate-list.pdf#MF-132"],
    ),
    "RI": (
        "Rhode Island Department of State",
        "Declared candidates report, 2026",
        ["https://vote.sos.ri.gov/Forms/elections/Reports/Candidates.xlsx#MF-133"],
    ),
    "CO": (
        "Colorado Secretary of State",
        "2026 general election official candidate list",
        ["https://www.coloradosos.gov/pubs/elections/vote/files/2026/2026GeneralCandidateListOfficial.xlsx"],
    ),
    "WI": (
        "Wisconsin Elections Commission",
        "Candidates on ballot by election, 2026 general election",
        [
            "https://elections.wi.gov/sites/default/files/documents/Candidates%20on%20Ballot%20By%20Election_November%203%202026%20General%20Election.pdf"
        ],
    ),
    "MI": (
        "Michigan Department of State",
        "Official candidate listing, November 3, 2026 general election",
        [
            "https://mi-boe.entellitrak.com/etk-mi-boe-prod/page.request.do?electionType=GEN&electionYear=2026&page=page.miboePublicReport"
        ],
    ),
    "NM": (
        "New Mexico Secretary of State",
        "2026 general election contest and candidate list",
        ["https://candidateportal.servis.sos.state.nm.us/CandidateList.aspx"],
    ),
    "ND": (
        "North Dakota Secretary of State",
        "2026 general election contest and candidate list",
        ["https://vip.sos.nd.gov/candidatelist.aspx?eid=348#browser"],
    ),
    "MD": (
        "Maryland State Board of Elections",
        "2026 gubernatorial general election state candidates list",
        ["https://elections.maryland.gov/elections/2026/General_candidates/2026_GG_statewide_candidatelist.csv"],
    ),
    "IN": (
        "Indiana Election Division",
        "Candidate list, abbreviated, 2026 general election (as of 2026-09-22)",
        ["https://www.in.gov/sos/elections/files/Candidate_List_Abbreviated_20260922_011251PM.xlsx"],
    ),
    "IL": (
        "Illinois State Board of Elections",
        "Candidate list, general election 11/3/2026",
        ["https://www.elections.il.gov/ElectionOperations/CandidateList.aspx?ElectionID=71#browser"],
    ),
    "KS": (
        "Kansas Secretary of State",
        "Candidate list, 2026 general election",
        ["https://www.sos.ks.gov/elections/elections_upcoming_candidate.aspx#browser"],
    ),
    "AR": (
        "Arkansas Secretary of State",
        "2026 candidate search export",
        ["https://candidates.arkansas.gov/#MF-134"],
    ),
    "PA": (
        "Pennsylvania Department of State",
        "Official candidate listing (post primary), 2026 general election",
        ["https://www.pavoterservices.pa.gov/electioninfo/FooterLinkReport.aspx?ID=1420#MF-135"],
    ),
    "NV": (
        "Nevada Secretary of State",
        "2026 candidate filing list",
        ["https://www.nvsos.gov/SOSCandidateServices/AnonymousAccess/CEFDSearchUU/CertCandList.aspx#MF-136"],
    ),
    "MN": (
        "Minnesota Secretary of State",
        "Candidate filings, November 3, 2026 state general elections",
        ["https://candidates.sos.mn.gov/CandidateFilingResults.aspx#MF-137"],
    ),
    "WV": (
        "West Virginia Secretary of State",
        "2026 general candidate listing",
        ["https://candidates.wvsos.gov/regular#MF-138"],
    ),
    "MS": (
        "Mississippi Secretary of State",
        "Candidate qualifying list",
        ["https://sos.ms.gov/content/CandidateQualifying/default.aspx"],
    ),
    "HI": (
        "Hawaii Office of Elections",
        "2026 general election ballot proofs",
        ["https://elections.hawaii.gov/wp-content/uploads/2026-General-Ballot-Proofs.pdf"],
    ),
    "OR": (
        "Oregon Secretary of State",
        "Voters' pamphlet, November 3, 2026 general election: list of candidates and measures",
        [
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book1_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book2_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book3_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book4_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book5_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book6_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book7_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book8_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book9_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book10_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book11_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book12_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book13_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book14_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book15_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book16_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book17_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book18_web.pdf",
            "https://sos.oregon.gov/elections/Voters-Pamphlet/Documents/VP_G26-Book19_web.pdf",
        ],
    ),
    "AS": (
        "American Samoa Election Office",
        "Candidates for the 2026 midterm election",
        ["https://www.aselectionoffice.gov/candidates-information/candidates-for-the-2026-midterm-election"],
    ),
    "MP": (
        "Commonwealth Election Commission",
        "2026 certified candidates listing",
        ["https://www.votecnmi.gov.mp/candidate/2026-certified-candidates-listing"],
    ),
    "GU": (
        "Guam Election Commission",
        "Sample general election ballot, November 3, 2026",
        ["https://drive.google.com/uc?export=download&id=1RKr3bLuivj8n0n29XKbQVHEgdUYwILMN"],
    ),
    "VI": (
        "Elections System of the Virgin Islands",
        "2026 general election official listing of candidates",
        [
            "https://vivote.gov/wp-content/uploads/2026/08/2026-Election-Public-Offices-Aspirant-Listing-STX-OFFICIAL-August-2026.pdf",
            "https://vivote.gov/wp-content/uploads/2026/09/2026-General-Election-Listing-of-Candidates-St.Thomas-St.-John-District-Official-.pdf",
        ],
    ),
    "VA": (
        "Virginia Department of Elections",
        "November 3, 2026 general election candidate list: federal offices",
        [
            "https://www.elections.virginia.gov/casting-a-ballot/candidate-list/november-3-2026-gen-elect-federal-offices/"
        ],
    ),
}
INDEPENDENT = re.compile(
    r"(?i)^(independent|independent nomination|unaffiliated|unenrolled|undeclared|no party.*|npa|non|none|nonpartisan|non-partisan|petition|by petition|)$"
)
MAJOR = [
    (re.compile(r"(?i)farmer.?labor"), "MN:minnesota-democratic-farmer-labor-party"),
    (re.compile(r"(?i)\bdemocrat"), "democratic-party"),
    (re.compile(r"(?i)\brepublican"), "republican-party"),
    (re.compile(r"(?i)\blibertarian"), "libertarian-party"),
    (re.compile(r"(?i)^pacific green"), "OR:pacific-green-party"),
    (re.compile(r"(?i)\bgreen\b"), "green-party"),
    (re.compile(r"(?i)\bconstitution\b"), "constitution-party"),
]
OFFICE_ALIASES = {
    "agriculture-commissioner": "commissioner-of-agriculture",
    "insurance-and-fire-safety-commissioner": "commissioner-of-insurance",
    "labor-commissioner": "commissioner-of-labor",
    "commissioner-of-the-general-land-office": "land-commissioner",
    "comptroller-auditor": "auditor",
}
SUFFIX = re.compile(r"(?i)^(jr|sr|ii|iii|iv|v)\.?$")
DAY = date(2026, 11, 3)
REDISTRICTED = {"AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT"}
CLASS_THREE_SPECIALS = ("OH", "FL")
NO_GENERAL = ("PR",)
SUPPLEMENTED = {"RI", "AR", "NV", "MN"}
SYSTEM_OVERRIDES = {"AK": "top_four", "LA": "fptp", "LA:house": "two_round"}
ORDINALS = {1: "st", 2: "nd", 3: "rd"}
TRANSCRIBED = {
    "2026GeneralElectionStateCertificationofRepublicanCandidates.pdf": [
        ("senate", None, "Barry Moore"),
        ("house", 1, "Jerry Carl"),
        ("house", 2, "Rhett Marques"),
        ("house", 3, "Mike Rogers"),
        ("house", 4, "Robert B. Aderholt"),
        ("house", 5, "Dale W. Strong"),
        ("house", 6, "Gary Palmer"),
        ("house", 7, "Ammie Akin"),
        ("governor", None, "Tommy Tuberville"),
    ],
    "2026GeneralElectionStateCertificationofDemocraticCandidates.pdf": [
        ("senate", None, "Everett Wess"),
        ("house", 1, "Clyde Jones"),
        ("house", 2, "Shomari C. Figures"),
        ("house", 3, "Lee McInnis"),
        ("house", 4, "Amanda N. Pusczek"),
        ("house", 5, "Andrew Sneed"),
        ("house", 6, "Maurice Mercer"),
        ("house", 7, "Terri Sewell"),
        ("governor", None, "Doug Jones"),
    ],
}
ORDINAL_WORDS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
]


def cells(text: str) -> list[list[str]]:
    return [
        [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL)
    ]


def single_filers(rows: list[tuple[str, int | None, str, str]]) -> list[tuple[str, int | None, str, str]]:
    counts = Counter((office, district, party) for office, district, _, party in rows)
    return [row for row in rows if counts[row[0], row[1], row[3]] == 1 or INDEPENDENT.match(row[3])]


def read(state: str, path: Path) -> list[tuple[str, int | None, str, str]]:
    rows: list[tuple[str, int | None, str, str]] = []
    if state == "FL":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="latin-1")), delimiter="\t"):
            if row["StatusCode"] in ("QUA", "UNO") and row["PartyCode"] != "WRI":
                office = {"USR": "house", "USS": "senate", "GOV": "governor"}.get(row["OfficeCode"], row["OfficeDesc"])
                district = row["Juris1num"] or row["Juris2num"]
                text = " ".join(part for part in (row["NameFirst"], row["NameMiddle"], row["NameLast"]) if part)
                rows.append((office, int(district) if district.isdigit() else None, text, row["PartyDesc"]))
    if state == "NC":
        seen: set[tuple[str, str]] = set()
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="latin-1"))):
            if row["election_dt"] != "11/03/2026" or (row["contest_name"], row["name_on_ballot"]) in seen:
                continue
            seen.add((row["contest_name"], row["name_on_ballot"]))
            house = re.fullmatch(r"US HOUSE OF REPRESENTATIVES DISTRICT (\d+)", row["contest_name"])
            office = "house" if house else "senate" if row["contest_name"] == "US SENATE" else row["contest_name"]
            party = {
                "DEM": "Democratic",
                "REP": "Republican",
                "LIB": "Libertarian",
                "GRE": "Green",
                "UNA": "Unaffiliated",
            }.get(row["party_candidate"], row["party_candidate"])
            rows.append((office, int(house.group(1)) if house else None, row["name_on_ballot"], party))
    if state == "VA":
        for office, district, party, text, *_ in cells(path.read_text())[1:]:
            number = re.match(r"(\d+)", district)
            chamber = {"Member, United States Senate": "senate", "Member, House of Representatives": "house"}.get(
                office, office
            )
            rows.append((chamber, int(number.group(1)) if number else None, text, party))
    if state == "WY":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="latin-1"))):
            if not row["Date Withdrawn"]:
                office = {
                    "UNITED STATES SENATOR": "senate",
                    "UNITED STATES REPRESENTATIVE": "house",
                    "GOVERNOR": "governor",
                }.get(row["Office Sought"], row["Office Sought"])
                party = {
                    "REP": "Republican",
                    "DEM": "Democratic",
                    "LBR": "Libertarian",
                    "CT": "Constitution",
                    "IND": "Independent",
                }.get(row["Party Affiliation"], row["Party Affiliation"])
                rows.append((office, None, row["Ballot Name"], party))
    if state == "DE":
        for office, _, party, text, status, *_ in (row for row in cells(path.read_text()) if len(row) >= 5):
            if status == "Qualified":
                chamber = {"U.S. Senator": "senate", "Representative in Congress": "house", "Governor": "governor"}.get(
                    office, office
                )
                text = re.split(
                    r"\s+(?:WITHDRAWN|Mailing Address:|Residential Address:|Email #|Phone #|Website:)", text
                )[0]
                rows.append((chamber, None, text, party))
    if state in ("MT", "WA"):
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            if row["Status"] in ("FILED", "Active"):
                race = row["Race"].upper()
                chamber = (
                    "house"
                    if race in ("UNITED STATES REPRESENTATIVE", "U.S. REPRESENTATIVE")
                    else "senate"
                    if race in ("UNITED STATES SENATOR", "U.S. SENATOR")
                    else "governor"
                    if race == "GOVERNOR"
                    else row["Race"]
                )
                number = re.search(r"\d+", row["District"])
                party = {
                    "REP": "Republican",
                    "DEM": "Democratic",
                    "LIB": "Libertarian",
                    "GRN": "Green",
                    "IND": "Independent",
                }.get(row["Party Preference"], row["Party Preference"])
                rows.append(
                    (chamber, int(number[0]) if number and chamber == "house" else None, row["Name"].lstrip("*"), party)
                )
    if state == "SD":
        table = json.loads(path.read_text())["rows"] if path.suffix == ".json" else cells(path.read_text())
        for contest, text, party, _, _, _, withdrawn, *_ in (row for row in table if len(row) >= 7):
            if text and not withdrawn and contest != "Contest":
                chamber = {
                    "United States Senator": "senate",
                    "United States Representative": "house",
                    "Governor and Lieutenant Governor": "governor",
                }.get(contest, contest)
                rows.append(
                    (
                        chamber,
                        None,
                        text.split(" & ")[0],
                        {"REP": "Republican", "DEM": "Democratic", "LIB": "Libertarian", "IND": "Independent"}.get(
                            party, party
                        ),
                    )
                )
    if state == "AK" and path.suffix == ".pdf":
        flat = html.unescape(
            re.sub(
                r"\s*\|[\s|]*",
                " | ",
                re.sub(r"<[^>]+>", "|", raw.cached(SOURCE, OFFICIAL["AK"][2][1]).path.read_text()),
            )
        )
        labels = {
            frozenset(re.sub(r"[,“”\"]", " ", text.split(" / ")[0]).lower().split()): party.split(" / ")[0]
            for text, party in re.findall(
                r"\| (?:UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE|GOVERNOR / LIEUTENANT GOVERNOR) \| All \| ([^|]+?) \| ([^|]+?) \| (?=Certified|Withdrawn)",
                flat,
            )
        }
        page = subprocess.run(
            ["pdftotext", "-layout", "-f", "17", "-l", "17", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        offices: list[str] = []
        for line in page:
            if line.startswith("United States Senate"):
                offices = ["senate", "house"]
            elif line.startswith("Governor and Lieutenant Governor"):
                offices = ["governor"]
            elif line.startswith("Alaska State"):
                break
            elif offices and line.strip():
                for office, text in zip(offices, re.split(r"\s{2,}", line.strip())):
                    text = text.rstrip("*").split("/")[0]
                    rows.append((office, None, text, labels[frozenset(re.sub(r"[“”\"]", " ", text).lower().split())]))
        return rows
    if state == "AK":
        return []
    if state == "CT":
        votes = json.loads(path.read_text())
        if "candidateIds" in votes:
            return []
        lookup = json.loads(raw.cached(SOURCE, OFFICIAL["CT"][2][0]).path.read_text())
        offices = {key: value for entry in lookup["officeList"] for key, value in entry.items()}
        lines: dict[tuple[str, int | None, str], list[str]] = {}
        for office_id, entries in votes.items():
            office = offices[office_id]["NM"]
            chamber = (
                "house"
                if office.startswith("Representative in Congress")
                else {"Governor and Lieutenant Governor": "governor", "United States Senator": "senate"}.get(
                    office, office
                )
            )
            for entry in entries:
                for candidate_id in entry:
                    candidate = lookup["candidateIds"][candidate_id]
                    party = re.sub(
                        r"(?<!Independent) Party$", "", lookup["partyIds"].get(candidate["P"], {}).get("NM", "")
                    )
                    if party != "Write In":
                        key = (
                            chamber,
                            int(offices[office_id]["D"]) if chamber == "house" else None,
                            candidate["NM"].split(" and ")[0],
                        )
                        lines.setdefault(key, []).append(party)
        return [(chamber, district, text, "/".join(parties)) for (chamber, district, text), parties in lines.items()]
    if state == "OK":
        office: str | None = None
        district: int | None = None
        seen: set[tuple[str, int | None, str, str]] = set()
        for token in html.unescape(re.sub(r"\s*\|[\s|]*", " | ", re.sub(r"<[^>]+>", "|", path.read_text()))).split(
            " | "
        ):
            if match := re.fullmatch(r"(.+), (REPUBLICAN|DEMOCRAT|LIBERTARIAN|INDEPENDENT)", token.strip()):
                if office and (office, district, match[1], match[2]) not in seen:
                    seen.add((office, district, match[1], match[2]))
                    rows.append((office, district, match[1], match[2]))
            else:
                house = re.fullmatch(r"UNITED STATES REPRESENTATIVE - DISTRICT (\d+)", token.strip())
                office = (
                    "house"
                    if house
                    else {"UNITED STATES SENATOR": "senate", "GOVERNOR": "governor"}.get(token.strip(), token.strip())
                )
                district = int(house[1]) if house else None
        return rows
    if state == "NY":
        layout = subprocess.run(
            ["pdftotext", "-bbox-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        office: str | None = None
        district: int | None = None
        lines: dict[tuple[str, int | None, str], list[str]] = {}
        for page in re.findall(r"<page[^>]*>(.*?)</page>", layout, re.DOTALL):
            sections: list[tuple[str | None, int | None, list[tuple[float, float, str]]]] = [(office, district, [])]
            for left, top, bottom, block in re.findall(
                r'<block xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="([\d.]+)">(.*?)</block>', page, re.DOTALL
            ):
                texts = [
                    html.unescape(" ".join(re.findall(r">([^<]+)</word>", line)))
                    for line in re.findall(r"<line[^>]*>(.*?)</line>", block, re.DOTALL)
                ]
                texts = [text for text in texts if text not in ("Governor", "Lt. Governor", "Candidate Name", "Party")]
                if texts and texts[0] == "Office:":
                    race = texts[1]
                    office = {
                        "Governor and Lt. Governor": "governor",
                        "Representative in Congress": "house",
                        "United States Senator": "senate",
                    }.get(race, race)
                    found = [text for text in texts if text.startswith("District:")]
                    district = int(found[0].split()[-1]) if office == "house" and found else None
                    sections.append((office, district, []))
                elif texts and float(left) < 220 and not texts[0].startswith("Certification"):
                    sections[-1][2].append((float(left), (float(top) + float(bottom)) / 2, " ".join(texts)))
            for section_office, section_district, blocks in sections:
                parties = [(middle, text) for left, middle, text in blocks if left < 100]
                for left, middle, text in blocks:
                    if section_office and left >= 100 and parties:
                        party = min(parties, key=lambda entry: abs(entry[0] - middle))[1]
                        lines.setdefault((section_office, section_district, text), []).append(party)
        return [(office, district, text, "/".join(parties)) for (office, district, text), parties in lines.items()]
    if state in ("AS", "MP"):
        flat = html.unescape(re.sub(r"\s*\|[\s|]*", " | ", re.sub(r"<[^>]+>", "|", path.read_text())))
        if state == "AS":
            section = flat.split("DELEGATE TO THE UNITED STATES CONGRESS", 1)[1].split("HOUSE OF REPRESENTATIVES", 1)[0]
            return [("house", None, text, "") for text in section.strip(" |").split(" | ")]
        tickets, delegate = flat.split("Governor & Lieutenant Governor", 1)[1].split(
            "Delegate to the U.S. House of Representatives", 1
        )
        labels = {"IND": "Independent", "REP": "Republican", "DEM": "Democratic"}
        return [
            ("governor", None, text, labels[party])
            for text, party in re.findall(
                r"\| Governor \| ([^|]+?) \| Lieutenant Governor \| [^|]+? \| (\w+)(?= \|)", tickets
            )
        ] + [
            ("house", None, text, labels[party])
            for text, party in re.findall(
                r"([^|]+?) \| (IND|REP|DEM) \|", delegate.split(" · ", 1)[0].split("candidates |", 1)[1]
            )
        ]
    if state in ("GU", "VI"):
        lines = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        for index, line in enumerate(lines):
            if state == "VI" and (
                match := re.match(
                    r"^\s*\d+\s+(.+?)\s+(?:P\.O\.|\d{3,}\s).*?\s(Democrat|Independent|Republican|ICM)\s+(Delegate to Congress|Governor/Lt\. Governor)",
                    line,
                )
            ):
                rows.append(
                    ("house" if match[3].startswith("Delegate") else "governor", None, match[1].split("/")[0], match[2])
                )
            office = {
                "Governor & Lieutenant Governor of Guam": "governor",
                "Non-Voting Delegate to the U.S. House of Representatives": "house",
            }.get(line.strip())
            if state == "GU" and office:
                position = next(i for i in range(index + 1, len(lines)) if "REPUBLICAN" in lines[i])
                for party, text in zip(
                    re.split(r"\s{2,}", lines[position].strip()), re.split(r"\s{2,}", lines[position + 1].strip())
                ):
                    if party != "WRITE-IN":
                        last, first = text.split(", ", 1)
                        rows.append((office, None, f"{first} {last.title()}", party.title()))
        return rows
    if state == "AL":
        return []
    if state == "LA":
        for race, entries in json.loads(path.read_text())["races"].items():
            for text, party in entries:
                rows.append(
                    ("senate" if race == "senate" else "house", None if race == "senate" else int(race), text, party)
                )
        return rows
    if state == "SC":
        for _, race, _, text, _, party, _, status in json.loads(path.read_text())["rows"][1:]:
            if status == "Active":
                house = re.fullmatch(r"U\.S\. House of Representatives, District (\d+)", race)
                office = (
                    "house"
                    if house
                    else {"U.S. Senate": "senate", "Governor and Lieutenant Governor": "governor"}.get(race, race)
                )
                rows.append((office, int(house[1]) if house else None, text, party))
        return rows
    if state == "CO":
        for row in fastexcel.read_excel(path).load_sheet(0, dtypes="string").to_polars().iter_rows(named=True):
            if row["Write In?"] != "N" or not row["Office"]:
                continue
            office = {"US Senate": "senate", "US House of Representatives": "house", "Governor": "governor"}.get(
                row["Office"], f"{row['Office']}, {row['District']}" if row["District"] != "State" else row["Office"]
            )
            district = int(row["District"]) if office == "house" else None
            rows.append((office, district, row["Candidate Name"], row["Party"] or ""))
        return rows
    if state == "IN":
        for text, party, title, filed, *_ in (
            fastexcel.read_excel(path).load_sheet(0, dtypes="string").to_polars().rows()
        ):
            if not text or not filed or text == "BALLOT NAME" or not party or party.startswith("Write-In"):
                continue
            house = re.fullmatch(r"United States Representative, (\w+) District", title or "")
            office = "house" if house else title
            rows.append((office, ORDINAL_WORDS.index(house[1].lower()) + 1 if house else None, text, party))
        return rows
    if state == "MD":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            if row["Candidate Status"] != "Active" or row["Filing Type and Date"].startswith("Write-In"):
                continue
            house = re.fullmatch(r"Congressional District (\d+)", row["Contest Run By District Name and Number"])
            office = (
                "house"
                if row["Office Name"] == "Representative in Congress"
                else "governor"
                if row["Office Name"] == "Governor / Lt. Governor"
                else f"{row['Office Name']}, {row['Contest Run By District Name and Number']}"
            )
            last = row["Candidate Ballot Last Name and Suffix"]
            suffix = ""
            if ", " in last:
                last, suffix = last.split(", ", 1)
            text = " ".join(part for part in (row["Candidate First Name and Middle Name"], last, suffix) if part)
            rows.append((office, int(house[1]) if house else None, text, row["Office Political Party"]))
        return rows
    if state == "IL":
        for text, write_in, _, race, party, _, status, *_ in (
            row for row in json.loads(path.read_text())["rows"][1:] if len(row) == 7
        ):
            if not status.startswith("Active") or write_in or "Affil Comm:" in text:
                continue
            last, _, given = text.split("\n")[0].partition(", ")
            suffix = re.search(r" (Jr|Sr|II|III|IV)\.?$", last)
            if suffix:
                last = last[: suffix.start()]
            house = re.fullmatch(r"(\d+)\w\w CONGRESS", race)
            office = (
                "house"
                if house
                else {"UNITED STATES SENATOR": "senate", "GOVERNOR AND LIEUTENANT GOVERNOR": "governor"}.get(race, race)
            )
            text = " ".join(part for part in (given, last, suffix[0].strip() if suffix else "") if part)
            rows.append((office, int(house[1]) if house else None, text, party))
        return rows
    if state == "KS":
        for text, race, district, _, _, party, *_ in json.loads(path.read_text())["rows"][1:]:
            office = {
                "United States Senate": "senate",
                "United States House of Representatives": "house",
                "Governor / Lt. Governor": "governor",
            }.get(race, f"{race}, {district}" if district != "0" else race)
            rows.append((office, int(district) if office == "house" else None, re.split(r" / | and ", text)[0], party))
        return rows
    if state == "ND":
        for race, _, _, district, _, text, *rest in json.loads(path.read_text())["rows"][1:]:
            office = "house" if race == "Representative in Congress" else f"{race}, {district}" if district else race
            rows.append((office, None, text, rest[3]))
        return rows
    if state == "NM":
        for row in cells(path.read_text(errors="replace")):
            if len(row) >= 23 and row[21] == "Qualified":
                house = re.fullmatch(r"DISTRICT (\d+)", row[2]) if row[1] == "United States Representative" else None
                office = (
                    "house"
                    if house
                    else {"United States Senator": "senate", "Governor and Lieutenant Governor": "governor"}.get(
                        row[1], f"{row[1]}, {row[2]}" if row[2] else row[1]
                    )
                )
                party = {
                    "DEM": "Democratic",
                    "REP": "Republican",
                    "LIB": "Libertarian",
                    "FWD": "Forward",
                    "DTS": "Independent",
                }.get(row[8], row[8])
                rows.append((office, int(house[1]) if house else None, row[4].split(" AND ")[0], party))
        return rows
    if state == "MI":
        race = ""
        for row in cells(path.read_text(errors="replace")):
            filled = [cell for cell in row if cell]
            if len(filled) == 1 and re.search(r"\(\d+\) Positions?$", filled[0]):
                race = re.sub(r"\s+\d+ Year Term.*$|\s+\(\d+\) Positions?$", "", filled[0])
            elif race and len(filled) >= 4 and re.fullmatch(r"\d\d/\d\d/\d{4}", filled[-2]):
                if len(filled) == 5:
                    continue
                party, text = filled[0], filled[1].split(" / ")[0]
                last, _, given = text.partition(", ")
                house = re.fullmatch(r"(\d+)\w\w District Representative in Congress", race)
                office = (
                    "house"
                    if house
                    else {"U.S. Senate": "senate", "Governor / Lt. Governor": "governor"}.get(race, race)
                )
                suffix = re.search(r" (Jr\.|Sr\.|II|III|IV)$", last)
                if suffix:
                    last = last[: suffix.start()]
                text = " ".join(part for part in (given, last, suffix[1] if suffix else "") if part)
                rows.append((office, int(house[1]) if house else None, text, party))
        return rows
    if state == "MA":
        race = district_name = ""
        for tag, body in re.findall(
            r"<(h2|h3|p)\b[^>]*>(.*?)</\1>", re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL), re.DOTALL
        ):
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
            if tag == "h2":
                race, district_name = text, ""
            elif tag == "h3":
                district_name = text
            elif race and text:
                fields = [field.strip() for field in text.split(",")]
                if len(fields) < 2 and not re.fullmatch(r"\w+ and \w+", text):
                    continue
                if len(fields) != 2 and len(fields) < 4:
                    fields.append("")
                office = {
                    "Senator in Congress": "senate",
                    "Governor & Lieutenant Governor": "governor",
                    "Representative in Congress": "house",
                }.get(race, f"{race}, {district_name}" if district_name else race)
                district = (
                    ORDINAL_WORDS.index(district_name.split()[0].lower()) + 1
                    if office == "house" and district_name
                    else None
                )
                ticket = fields[0].split(" and ")[0]
                rows.append((office, district, {"James": "Andrea James"}.get(ticket, ticket), fields[-1]))
        return rows
    if state == "AZ":
        page = path.read_text()
        heads = list(re.finditer(r"<h3>(.*?)</h3>|<h4>(.*?)</h4>", page, re.DOTALL))
        race = ""
        for index, head in enumerate(heads):
            if head[1] is not None:
                race = re.sub(r"\s+", " ", html.unescape(head[1])).strip()
                continue
            end = heads[index + 1].start() if index + 1 < len(heads) else len(page)
            if "Write-In Candidate" in page[head.end() : end]:
                continue
            entry = re.fullmatch(r"(.+?) \((\w+)\)", re.sub(r"\s+", " ", html.unescape(head[2])).strip())
            if entry is None:
                continue
            last, _, given = entry[1].partition(", ")
            suffix = ""
            if ", " in given and SUFFIX.match(given.rsplit(", ", 1)[1]):
                given, suffix = given.rsplit(", ", 1)
            house = re.fullmatch(r"U\.S\. Representative in Congress - District No\. (\d+)", race)
            office = "house" if house else {"U.S. Senator": "senate", "Governor": "governor"}.get(race, race)
            party = {
                "REP": "Republican",
                "DEM": "Democratic",
                "LBT": "Libertarian",
                "GRN": "Green",
                "NOL": "No Labels",
            }.get(entry[2], "Independent")
            text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), f"{given} {last}".title())
            text = f"{text} {suffix if re.fullmatch(r'[IVX]+', suffix) else suffix.title()}".strip()
            rows.append((office, int(house[1]) if house else None, text, party))
        return rows
    if state == "GA":
        with zipfile.ZipFile(path) as archive:
            book = archive.read(next(entry for entry in archive.namelist() if entry.endswith(".xlsx")))
        for row in fastexcel.read_excel(book).load_sheet(0, dtypes="string").to_polars().iter_rows(named=True):
            race = row["Office"].strip()
            house = re.fullmatch(r"U\.S\. House of Representatives, District (\d+)", race)
            office = "house" if house else {"U.S. Senate": "senate", "Governor": "governor"}.get(race, race)
            party = {"R": "Republican", "D": "Democratic", "L": "Libertarian", "I": "Independent"}.get(
                row["Party"], row["Party"]
            )
            rows.append((office, int(house[1]) if house else None, row["Name on Ballot"].strip(), party))
        return rows
    if state == "AR":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            race = row["Position/Office"]
            house = re.fullmatch(r"U\.S\. Congress District (\d+)", race)
            office = "house" if house else {"U.S. Senate": "senate", "Governor": "governor"}.get(race, race)
            text = re.sub(
                r"^(State Representative|State Senator|State Treasurer|Representative|Rep\.|Senator|Congress(?:man|woman)"
                r"|Lieutenant Governor|Governor|Attorney General|Auditor of State|Secretary of State"
                r"|Justice of the Peace|Justice|Mayor|Sheriff) ",
                "",
                row["Candidate Name"],
            ).replace(", ", " ")
            party = "Nonpartisan" if row["Party Affiliation"] == "Non Partisan Judicial" else row["Party Affiliation"]
            rows.append((office, int(house[1]) if house else None, text, party))
        return single_filers(rows)
    if state == "PA":
        for row in cells(path.read_text(errors="replace")):
            if len(row) != 6 or row[0] != "-":
                continue
            _, text, race, district, party, _ = row
            house = re.fullmatch(r"(\d+)\w\w Congressional District", district)
            office = (
                "house"
                if race == "REPRESENTATIVE IN CONGRESS"
                else "governor"
                if race == "GOVERNOR"
                else f"{race}, {district}"
            )
            party = {
                "DEM": "Democratic",
                "REP": "Republican",
                "LIB": "Libertarian",
                "GRN": "Green",
                "IND": "Independent",
                "D/R": "Democratic/Republican",
            }.get(party, party)
            rows.append((office, int(house[1]) if house and office == "house" else None, text, party))
        return rows
    if state == "NV":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            if row["Filed Status"] != "Filed":
                continue
            race = row["Office Sought"]
            house = re.fullmatch(r"Representative In Congress, District (\d+)", race)
            office = "house" if house else "governor" if race == "Governor" else race
            last, _, given = row["Name to Appear on Ballot"].partition(", ")
            suffix = ""
            if re.fullmatch(r"(?i)(jr|sr|ii|iii|iv)\.?", given.split(", ")[0]):
                suffix, _, given = given.partition(", ")
            text = " ".join(part for part in (given.replace(", ", " "), last, suffix) if part) if given else last
            party = re.sub(r"(?<!Political) Party( of Nevada)?$", "", row["Party or Nonpartisan Office"])
            rows.append((office, int(house[1]) if house else None, text, party))
        return single_filers(rows)
    if state == "MN":
        race = ""
        for row in cells(path.read_text(errors="replace")):
            if len(row) == 2 and row[1]:
                race = row[1]
            elif len(row) == 5 and row[1] and race:
                house = re.fullmatch(r"U\.S\. Representative District (\d+)", race)
                office = (
                    "house"
                    if house
                    else {"U.S. Senator": "senate", "Governor & Lt Governor": "governor"}.get(race, race)
                )
                rows.append((office, int(house[1]) if house else None, row[1].split(" and ")[0], row[2]))
        return single_filers(rows)
    if state == "MS":
        for card in path.read_text(errors="replace").split("<div class='card officeCard'>")[1:]:
            race = re.sub(r"<[^>]+>", "", card.split("</div>")[0]).removeprefix("Candidates for ").strip()
            table = cells(card)
            if not table:
                continue
            header, *body = table
            for row in body:
                fields = dict(zip(header, row))
                office = {"United States Senate": "senate", "United States House of Representatives": "house"}.get(
                    race,
                    ", ".join(
                        [race, *(f"{key} {value}" for key, value in fields.items() if key not in ("Name", "Party"))]
                    ),
                )
                district = int(fields["District"]) if office == "house" else None
                rows.append((office, district, fields["Name"], fields.get("Party", "")))
        return rows
    if state == "HI":
        lines = subprocess.run(
            ["pdftotext", "-raw", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        race = ""
        seen: set[tuple[str, str]] = set()
        for index, line in enumerate(lines):
            if line.startswith("Vote For Not More Than"):
                race = lines[index - 1]
                if race.endswith(")") and "(" not in race:
                    race = f"{lines[index - 2]} {race}"
                continue
            candidate = re.fullmatch(r"(?:\(([DGLNR])\) )?([A-Z][A-Z'’ .-]+, .+)", line)
            if not race or not candidate or lines[index + 1 : index + 2] == ["For LIEUTENANT GOVERNOR"]:
                if not candidate and line not in ("For GOVERNOR", "For LIEUTENANT GOVERNOR"):
                    race = ""
                continue
            if (race, line) in seen:
                continue
            seen.add((race, line))
            house = re.fullmatch(r"U\.S\. Representative, Dist (I+)", race)
            office = "house" if house else "governor" if race == "Governor" else race
            last, _, given = candidate[2].partition(", ")
            suffix = re.fullmatch(r"(.+), (Jr\.|Sr\.|II|III|IV)", given)
            text = " ".join([suffix[1], last.title(), suffix[2]] if suffix else [given, last.title()])
            party = {"D": "Democratic", "R": "Republican", "G": "Green", "L": "Libertarian", "N": "Nonpartisan"}.get(
                candidate[1] or "", ""
            )
            rows.append((office, len(house[1]) if house else None, text, party))
        return rows
    if state == "OR":
        lines = subprocess.run(
            ["pdftotext", "-raw", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        spaced = re.sub(
            r"\s+",
            " ",
            subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout,
        )
        start = next(index for index, line in enumerate(lines) if line.endswith("List of Candidates & Measures"))
        race, seat, name, footnote = "", "", None, False
        for line in lines[start + 1 :]:
            if line.startswith("*Candidate"):
                footnote = True
            elif footnote:
                footnote = not line.startswith("pamphlet.")
            elif line.startswith("NonpartisanCandidates") or "Index of Candidates" in line:
                break
            elif line == "PartisanCandidates" or "List of Candidates & Measures" in line or line.isdigit():
                continue
            elif line in ("US Senator", "US Representative", "Governor", "State Senator", "State Representative") or (
                line.startswith("County ")
            ):
                race, seat = line, ""
            elif match := re.fullmatch(r"(\d+)(st|nd|rd|th) ?District|(\w+)County,Position(\d+)", line):
                seat = f"{match[1]}{match[2]} District" if match[1] else f"{match[3]} County, Position {match[4]}"
            elif name is None:
                name = line.rstrip("*")
                for split in re.finditer(r"(?<=[a-z]{2})(?=[A-Z])", name):
                    candidate = f"{name[: split.start()]} {name[split.start() :]}"
                    if candidate in spaced and name not in spaced:
                        name = candidate
                        break
            else:
                office = {"US Senator": "senate", "US Representative": "house", "Governor": "governor"}.get(
                    race, f"{race}, {seat}" if seat else race
                )
                district = int(re.sub(r"\D", "", seat)) if office == "house" else None
                rows.append((office, district, name, line.replace(", ", "/")))
                name = None
        return rows
    if state == "WV":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            race = row["Race"]
            house = re.fullmatch(r"U\.S\. HOUSE OF REPRESENTATIVES - DISTRICT (\d+)", race)
            office = "house" if house else "senate" if race == "U.S. SENATE" else race
            party = {"DEMOCRAT": "Democratic", "NON-PARTISAN": "Nonpartisan"}.get(row["Party"], row["Party"])
            rows.append((office, int(house[1]) if house else None, row["Name"], party))
        return rows
    if state == "RI":
        frame = fastexcel.read_excel(path).load_sheet(0, dtypes="string").to_polars()
        primary = frame.filter(pl.col("ELECTION DATE - NAME").str.contains("PRIMARY") & (pl.col("ON P.B") == "Yes"))
        unopposed = primary.group_by("OFFICE", "DIST#", "PARTY").len().filter(pl.col("len") == 1).drop("len")
        for row in pl.concat(
            [
                frame.filter((pl.col("ON E.B") == "Yes") & pl.col("ELECTION DATE - NAME").str.contains("GENERAL")),
                primary.join(unopposed, on=["OFFICE", "DIST#", "PARTY"]),
            ]
        ).iter_rows(named=True):
            house = re.fullmatch(r"REPRESENTATIVE IN CONGRESS DISTRICT (\d+)", row["OFFICE"])
            office = (
                "house"
                if house
                else {"SENATOR IN CONGRESS": "senate", "GOVERNOR": "governor"}.get(
                    row["OFFICE"], f"{row['OFFICE']}, {row['DIST#']}"
                )
            )
            text = " ".join(
                part for part in (row["FIRST NAME"], row["MIDDLE NAME"], row["LAST NAME"], row["SUFFIX"]) if part
            )
            rows.append((office, int(house[1]) if house else None, text, row["PARTY"]))
        return rows
    if path.suffix == ".pdf":
        lines = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        office: str | None = None
        district: int | None = None
        header = ""
        for line in lines:
            if state == "WI":
                stripped = line.strip()
                if match := re.match(r"^Office :\s+(.+?)(?:\s{2,}Incumbent:.*)?$", stripped):
                    race = match[1].strip()
                    house = re.fullmatch(r"REPRESENTATIVE IN CONGRESS DISTRICT (\d+)", race)
                    office, district = (
                        ("house", int(house[1]))
                        if house
                        else ({"GOVERNOR": "governor", "UNITED STATES SENATOR": "senate"}.get(race, race), None)
                    )
                elif office and (match := re.match(r"^\d+\s+(?:\d{7}\s+)?(\S.*?)\s{2,}(\S.*)$", stripped)):
                    rows.append((office, district, match[1].split(" / ")[0], match[2]))
                elif (
                    rows
                    and office
                    and len(line) - len(line.lstrip()) > 40
                    and len(re.split(r"\s{2,}", stripped)) == 1
                    and not stripped.startswith(("Total", "Printed", "Wisconsin", "Candidates", "2026", "Order", "ID"))
                ):
                    rows[-1] = (*rows[-1][:3], f"{rows[-1][3]} {stripped}")
                elif stripped.startswith("Total Number"):
                    office = None
            if state == "OH":
                stripped = line.strip()
                if re.match(r"^VI\.\s+CANDIDATES CERTIFIED", stripped):
                    header = "certified"
                elif re.match(r"^VII\.", stripped):
                    header = ""
                elif header and (match := re.match(r"^[A-Z]\.\s+(.+)$", stripped)):
                    race = match[1]
                    office = (
                        "governor"
                        if race == "Governor and Lieutenant Governor"
                        else "senate"
                        if race.startswith("U.S. Senator")
                        else race
                    )
                    party = ""
                elif header and (stripped.endswith(" Party") or stripped == "Other-party candidate"):
                    party = "Independent" if stripped == "Other-party candidate" else stripped
                elif header and office and stripped.startswith("•"):
                    rows.append((office, None, stripped.lstrip("• ").split(" and ")[0], party))
            if state == "IA" and (
                match := re.match(
                    r"^(\S.*?)?\s+(Republican|Democratic|Libertarian|No Party|Green|Party for Socialism and Liberation|\w[\w ]*? Party)\s+(\S.*?)(?:\s{2,}|$)",
                    line,
                )
            ):
                if match[1]:
                    race = match[1].strip()
                    house = re.fullmatch(r"United States Representative District (\d+)", race)
                    office, district = (
                        ("house", int(house[1]))
                        if house
                        else ({"United States Senator": "senate", "Governor": "governor"}.get(race, race), None)
                    )
                if office and match[3] != "--" and not match[3].startswith("Ballot Name"):
                    rows.append((office, district, match[3], match[2]))
            if state == "NH":
                stripped = line.strip()
                fields = re.split(r"\s{2,}", stripped)
                if not stripped or line.startswith(" ") or stripped.startswith(("Candidate Name", "OFFICE OF")):
                    continue
                if match := re.fullmatch(r"District (\d+)", stripped):
                    district = int(match[1])
                elif len(fields) == 1:
                    office = {
                        "Governor": "governor",
                        "United States Senator": "senate",
                        "Representative in Congress": "house",
                    }.get(stripped, stripped)
                    district = None
                elif office and len(fields) >= 4:
                    party = {
                        "DEM": "Democratic",
                        "REP": "Republican",
                        "LIB": "Libertarian",
                        "CON": "Constitution",
                        "IND": "Independent",
                    }.get(fields[-1], fields[-1])
                    rows.append((office, district, fields[0], party))
            if state == "NJ":
                if line.startswith("Candidate Totals"):
                    header = ""
                elif match := re.match(r"^(\w+) Congressional District", line):
                    office, district = "house", ORDINAL_WORDS.index(match[1].lower()) + 1
                elif "Candidates for US Senate" in line:
                    office, district = "senate", None
                elif line.startswith("Name ") and "Party" in line:
                    header = line
                elif header and re.match(r"^[A-Z][A-Z.'\- ]+[A-Z.](?: \*)?\s{2,}", line):
                    fields = re.split(r"\s{2,}", line.strip())
                    label = re.sub(r"^.*\bApt-(?:Unit)?\s*\S*\s+", "", fields[-1]) if len(fields) == 2 else fields[-1]
                    party = (
                        label.title()
                        if re.search(r"(?i)\b(democratic|republican|green|libertarian|party)\b", label)
                        else "Independent"
                    )
                    rows.append((office or "senate", district, fields[0].removesuffix(" *"), party))
            if state == "NE":
                fields = re.split(r"\s{2,}", line.strip())
                if line.startswith("For ") and len(fields) >= 6:
                    at = 2 if fields[1].startswith("District") else 1
                    race = fields[0]
                    chamber = {
                        "For United States Senator": "senate",
                        "For Representative in Congress": "house",
                        "For Governor and Lt. Governor": "governor",
                    }.get(race, race.removeprefix("For "))
                    number = int(fields[1].split()[-1]) if at == 2 else None
                    rows.append((chamber, number, fields[at + 3].split(" and ")[0], fields[at + 2]))
                elif rows and len(fields) == 1 and fields[0] and 50 <= len(line) - len(line.lstrip()) <= 75:
                    rows[-1] = (*rows[-1][:3], rows[-1][3] + " " + fields[0])
            if state == "UT":
                if match := re.match(r"^U\.S\. House of Representatives District (\d+)\.", line):
                    office, district = "house", int(match[1])
                elif (
                    re.match(r"^[A-Z][A-Z ]+$", line.strip())
                    and line.strip() not in ("STATE SENATOR",)
                    and len(line.strip()) > 12
                    or line.strip() == "STATE SENATOR"
                ):
                    office = None
                elif office and (match := re.match(r"^([A-Z][A-Z.'\-\" ]+[A-Z.])\s{3,}(\S.*)$", line)):
                    rows.append((office, district, match[1], match[2].strip()))
            if state == "TX":
                indent = len(line) - len(line.lstrip())
                if indent == 5 and line.strip():
                    race = line.strip()
                    house = re.fullmatch(r"U\. S\. REPRESENTATIVE DISTRICT (\d+)", race)
                    office, district = (
                        ("house", int(house[1]))
                        if house
                        else ({"U. S. SENATOR": "senate", "GOVERNOR": "governor"}.get(race, race), None)
                    )
                elif indent == 10 and office and len(fields := re.split(r"\s{2,}", line.strip())) == 2:
                    party = {
                        "REP": "Republican",
                        "DEM": "Democratic",
                        "LIB": "Libertarian",
                        "GRE": "Green",
                        "IND": "Independent",
                    }.get(fields[1], fields[1])
                    if (office, district, fields[0], party) not in rows:
                        rows.append((office, district, fields[0], party))
            if state == "CA":
                indent = len(line) - len(line.lstrip())
                if indent >= 25 and line.strip() and not re.match(r"^\d", line.strip()):
                    race = line.strip()
                    house = re.fullmatch(r"United States Representative District (\d+)", race)
                    office, district = (
                        ("house", int(house[1]))
                        if house
                        else ({"United States Senator": "senate", "Governor": "governor"}.get(race, race), None)
                    )
                elif indent == 8 and office and len(fields := re.split(r"\s{2,}", line.strip())) == 2:
                    rows.append((office, district, fields[0], fields[1]))
            if state == "MO":
                if match := re.match(r"^\s*(\w+) PARTY EMBLEM", line):
                    header = match[1].title()
                elif line.startswith("For "):
                    race = line.removeprefix("For ").strip()
                    office = "house" if race == "U.S. Representative" else race
                elif header and office and (match := re.match(r"^District (\d+), (.+)$", line.strip())):
                    rows.append((office, int(match[1]), match[2], header))
            if state == "DC":
                if (
                    match := re.match(
                        r"^(\S.*?) - (Republican|Democrat|Statehood Green|Independent|Libertarian|Nonpartisan)\b", line
                    )
                ) and office:
                    rows.append((office, 0 if office == "house" else None, match[1], match[2]))
                elif (
                    line.strip() and not line.startswith(" ") and " - " not in line and not line.startswith("Write-In")
                ):
                    office = "house" if line.strip() == "Delegate to the House of Representatives" else line.strip()
    if state == "TN":
        for row in fastexcel.read_excel(path).load_sheet(0).to_polars().iter_rows(named=True):
            house = re.fullmatch(r"United States House of Representatives District (\d+)", row["Office"])
            office = (
                "house"
                if house
                else {"United States Senate": "senate", "Governor": "governor"}.get(row["Office"], row["Office"])
            )
            rows.append((office, int(house[1]) if house else None, row["Candidate"], row["Party Name"]))
    if state == "KY":
        for row in fastexcel.read_excel(path).load_sheet(0, dtypes="string").to_polars().iter_rows(named=True):
            if row["Party"] != "Write-In" and not row["Withdrawn"] and not row["Disqualified"]:
                office = {"US Senator": "senate", "US Representative": "house", "Governor": "governor"}.get(
                    row["Office"], row["Office"]
                )
                text = " ".join(
                    part for part in (row["First Name"], row["Middle Name"], row["Last Name"], row["Suffix"]) if part
                )
                district = int(row["District"]) if str(row["District"] or "").isdigit() else None
                rows.append((office, district, text, row["Party"]))
    if state in ("ME", "VT"):
        frame = fastexcel.read_excel(path).load_sheet(0).to_polars()
        for row in frame.iter_rows(named=True):
            if state == "ME":
                office = {"US": "senate", "CG": "house", "GOV": "governor"}.get(row["Office"], row["Office"])
                text = " ".join(
                    part for part in (row["First Name"], row["Middle Name"], row["Last Name"], row["Suffix"]) if part
                )
                party = {"D": "Democratic", "R": "Republican", "G": "Green Independent", "L": "Libertarian"}.get(
                    row["Party"] or "", row["Party"] or ""
                )
                district = int(row["Dist"]) if str(row["Dist"] or "").isdigit() else None
            else:
                office = {"REPRESENTATIVE TO CONGRESS": "house", "GOVERNOR": "governor"}.get(
                    row["Contest"], row["Contest"]
                )
                text = row["Name On Ballot"]
                party = "/".join(
                    {"DEM": "Democratic", "REP": "Republican", "PROG": "Progressive"}.get(
                        part, re.sub(r"\b(And|Of|The)\b", lambda match: match[0].lower(), part.title())
                    )
                    for part in (row["Party"] or "").split("/")
                )
                district = int(row["District Name"]) if str(row["District Name"] or "").isdigit() else None
            rows.append((office, district, text, party))
    return rows


def fetch() -> None:
    raw.fetch(SOURCE, CN26, suffix=".zip")
    for _, _, urls in OFFICIAL.values():
        for url in urls:
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                if url.endswith("#browser"):
                    print(f"{url} is a browser scrape; store it with raw.store")
                    continue
                if "#MF-" in url:
                    delivered = next((DATA / "inbox" / "manual" / url.split("#")[1]).iterdir())
                    raw.store(
                        SOURCE,
                        url,
                        delivered.read_bytes(),
                        suffix=delivered.suffix,
                        retrieved_at=datetime.fromtimestamp(delivered.stat().st_mtime, UTC),
                        media_type="",
                    )
                    continue
            if url == FL_URL:
                response = httpx.post(
                    url.split("?")[0],
                    data={
                        "elecID": "20261103-GEN",
                        "office": "All",
                        "status": "All",
                        "cantype": "STA",
                        "FormsButton2": "Download",
                    },
                    headers=BROWSER,
                    timeout=300,
                )
            elif url.endswith("#csv"):
                with httpx.Client(headers=BROWSER, follow_redirects=True, timeout=300) as client:
                    page = client.get(url.removesuffix("#csv")).text
                    fields = {
                        field: html.unescape(value)
                        for field, value in re.findall(
                            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page
                        )
                    }
                    button = re.findall(r'name="([^"]*ExportToCsvButton[^"]*)"', page)[0]
                    response = client.post(url.removesuffix("#csv"), data={**fields, button: " "})
            elif url.endswith("#excel"):
                with httpx.Client(headers=BROWSER, follow_redirects=True, timeout=300) as client:
                    page = client.get(url.removesuffix("#excel")).text
                    fields = {
                        field: html.unescape(value)
                        for field, value in re.findall(
                            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page
                        )
                    }
                    response = client.post(
                        url.removesuffix("#excel"),
                        data={
                            **fields,
                            "ctl00$MainContent$ddlElection": "87",
                            "ctl00$MainContent$ddlParty": "",
                            "ctl00$MainContent$bExcel": "Excel",
                        },
                    )
            else:
                response = httpx.get(url, headers=BROWSER, follow_redirects=True, timeout=300, verify=False)
            response.raise_for_status()
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".pdf"
                if response.content.startswith(b"%PDF")
                else ".txt"
                if url == FL_URL
                else ".csv"
                if url.endswith("#csv")
                else Path(url.split("?")[0].rstrip("/")).suffix or ".html",
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", ""),
            )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "state": state,
            "office": office,
            "district": 0 if office == "house" and district is None else district,
            "name": initials(
                re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), re.sub(r"\s+", " ", text).strip().title())
                if text.isupper()
                else re.sub(r"\s+", " ", text).strip()
            ),
            "party": party.strip().title() if party.isupper() and len(party) > 4 else party.strip(),
            "url": url,
        }
        for state, (_, _, urls) in OFFICIAL.items()
        for url in urls
        for office, district, text, party in [
            *read(state, raw.cached(SOURCE, url).path),
            *(
                (office, district, text, "Republican" if "Republican" in url else "Democratic")
                for office, district, text in TRANSCRIBED.get(url.rsplit("/", 1)[-1], [])
            ),
        ]
    ]
    frame = pl.DataFrame(
        rows,
        schema={
            "state": pl.String,
            "office": pl.String,
            "district": pl.Int64,
            "name": pl.String,
            "party": pl.String,
            "url": pl.String,
        },
    )
    frame.write_parquet(PARSED / "candidates.parquet")
    print(
        frame.filter(pl.col("office").is_in(["house", "senate", "governor"]))
        .group_by("state", "office")
        .len()
        .sort("state", "office")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2026-11-03 general election: every House and Senate contest, the 36 governor contests, and the other statewide offices due as scheduled, with candidates from the states' certified lists, else the archive's own primary nominees",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        national = cs.key("us.election", f"{DAY}")
        cs.add(
            "election",
            id=national,
            polity_id=us,
            parent_id=None,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            status="scheduled",
        )
        name(national, "2026 United States general election")
        cs.add("slug", path=f"us/{DAY}-general", entity_id=national)

        systems = {key: cs.key("us.electoral_system", key) for key in SYSTEM_OVERRIDES.values()}
        elections: dict[str, UUID] = {}

        templates = conn.execute(
            """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id,
                      co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, e.polity_id
               from contest co
               join election e on e.id = co.election_id
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               where x.value ~ '^2024-11-05:[A-Z]{2}:house:[0-9]+$'
                  or x.value ~ '^2020-11-03:[A-Z]{2}:senate$'
                  or x.value ~ '^2022-11-08:(OH|FL):senate$'
                  or x.value ~ '^2022-11-08:[A-Z]{2}:governor$'"""
        ).fetchall()
        due = {
            office: st
            for st, office, _, when, joint, _ in statewide_table.schedule(conn, DAY.year)
            if when == DAY.year and not joint
        }
        offices: dict[str, str] = {}
        for *template, label in conn.execute(
            """select distinct on (co.office_id) x.value, co.kind, co.office_id, co.body_id, co.district_area_id,
                      co.seat_id, co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, e.polity_id, n.text
               from contest co
               join election e on e.id = co.election_id
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               join name n on n.entity_id = co.id and n.is_preferred
               where co.office_id = any(%s) and e.kind = 'general' and co.party_scope_id is null
                 and co.term is distinct from 'remainder' and x.value ~ '^[0-9-]+:[A-Z]{2}:[a-z-]+$'
               order by co.office_id, e.date_from desc""",
            [list(due)],
        ).fetchall():
            _, state, chamber = template[0].split(":")
            if f"{state}:{chamber}" not in offices:
                offices[f"{state}:{chamber}"] = label
                templates.append(tuple(template))
        latest_plan = conn.execute(
            "select id from boundary_set where scheme_code = 'us.cd' order by valid_from desc limit 1"
        ).fetchone()[0]
        state_names = dict(
            conn.execute(
                """select right(p.iso_code, 2), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )

        chambers = "|".join(["governor", "senate", r"house:[0-9]+", *sorted({key.split(":")[1] for key in offices})])
        nominees: dict[str, dict[tuple[str, UUID], UUID | None]] = {}
        for value, ballot, person, party_id in conn.execute(
            """select x.value, op.ballot_label, cy.person_id, op.party_id
               from external_id x
               join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
               join outcome o on o.result_set_id = rs.id and o.kind in ('won', 'advanced')
               join contest_option op on op.id = o.option_id
               join candidacy cy on cy.option_id = op.id and cy.role = 'head'
               where x.scheme = 'us.contest'
                 and (x.value ~ ('^2026-[0-9-]+:[A-Z]{2}:(' || %(chambers)s || '):(democratic|republican|minnesota-democratic-farmer-labor)$')
                      and o.kind = 'won'
                      or x.value ~ ('^2026-[0-9-]+:(CA|WA):(' || %(chambers)s || ')$') and o.kind = 'advanced')""",
            {"chambers": chambers},
        ):
            _, state, chamber, *rest = value.split(":")
            general = f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else "")
            known = nominees.setdefault(general, {})
            known[(ballot, person)] = known.get((ballot, person)) or party_id

        listed: dict[str, list[dict[str, str | int | None]]] = {}
        for row in pl.read_parquet(PARSED / "candidates.parquet").iter_rows(named=True):
            office = re.sub(r"-(statewide|state-of-[a-z]+)$", "", slug(row["office"]))
            office = re.sub(r"^(state|general)-", "", office)
            if office.startswith(("auditor", "treasurer", "comptroller")):
                office = re.sub(r"-of-(state|accounts|public-accounts)$", "", office)
            office = OFFICE_ALIASES.get(office, office)
            if row["office"] in ("house", "senate", "governor") or f"{row['state']}:{office}" in offices:
                key = f"{DAY}:{row['state']}:{row['office'] if row['office'] in ('house', 'senate', 'governor') else office}"
                listed.setdefault(key + (f":{row['district']}" if row["office"] == "house" else ""), []).append(row)

        def surname(text: str) -> str:
            words = [
                word for word in re.sub(r"[\"“][^\"”]*[\"”]|\([^)]*\)|,", " ", text).split() if not SUFFIX.match(word)
            ]
            return slug(words[-1]) if words else ""

        filers: dict[tuple[str, str, int | None], list[tuple[str, str, str]]] = {}
        given_initials: dict[str, set[str]] = {}
        with zipfile.ZipFile(raw.cached(SOURCE, CN26).path) as archive:
            for line in archive.read("cn.txt").decode("latin-1").splitlines():
                fec, text, _, year, state, office, district, _, status, *_ = line.split("|")
                if year == "2026" and status == "C" and office in ("H", "S"):
                    last, _, first = text.partition(",")
                    given_initials[fec] = {slug(word)[:1] for word in first.split() if not SUFFIX.match(word)}
                    chamber = "house" if office == "H" else "senate"
                    filers.setdefault((state, chamber, int(district or 0) if office == "H" else None), []).append(
                        (fec, surname(last), slug(first)[:1])
                    )
        fec_persons = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'fec.candidate'").fetchall()
        )
        dropped: set[str] = set()
        for fec, person, text in conn.execute(
            """select x.value, x.entity_id, n.text
               from external_id x
               join changeset c on c.id = x.changeset_id and c.pipeline = %s
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'fec.candidate'""",
            [SOURCE],
        ).fetchall():
            if fec in given_initials and {
                initial
                for word in re.findall(r"\w+", re.sub(r"\s\S+$", "", text.strip()))
                for initial in (
                    slug(word)[:1],
                    {"bill": "w", "bob": "r", "dick": "r", "ted": "e", "peggy": "m", "tony": "a", "sandy": "a"}.get(
                        word.lower(), ""
                    ),
                )
            }.isdisjoint(given_initials[fec]):
                print("fec id dropped:", fec, text)
                cs.add(
                    "external_id_drop",
                    entity_id=person,
                    scheme="fec.candidate",
                    value=fec,
                    reason="The FEC filer's given name does not match this person",
                )
                del fec_persons[fec]
                dropped.add(fec)
        fec_held = {entity for entity in fec_persons.values()}
        known: dict[str, list[tuple[UUID, str]]] = {}
        earlier: dict[str, list[tuple[UUID, str]]] = {}
        by_state: dict[str, list[tuple[UUID, str]]] = {}
        latest: dict[UUID, str] = {}
        for value, person, text in conn.execute(
            """select x.value, cy.person_id, n.text
               from external_id x
               join contest_option op on op.contest_id = x.entity_id
               join candidacy cy on cy.option_id = op.id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value ~ ('^20(1[8-9]|2[0-6])-[0-9-]+:[A-Z]{2}:(' || %s || ')(:|$)')""",
            [chambers],
        ).fetchall():
            day, state, chamber, *rest = value.split(":")
            latest[person] = max(latest.get(person, ""), day)
            by_state.setdefault(state, []).append((person, text))
            (known if day.startswith("2026") else earlier).setdefault(
                f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else ""), []
            ).append((person, text))
        for value, person, text in conn.execute(
            """select x.value, t.person_id, n.text
               from tenure t
               join external_id x on x.entity_id = t.office_id and x.scheme = 'us.office'
               join name n on n.entity_id = t.person_id and n.is_preferred
               where x.value ~ ':[A-Z]{2}$' and coalesce(t.to_date, t.from_date, '2026-01-01') >= '2010-01-01'"""
        ).fetchall():
            office, state = value.split(":")
            by_state.setdefault(state, []).append((person, text))
            earlier.setdefault(f"{DAY}:{state}:{office}", []).append((person, text))
        surnames = {surname(str(row["name"])) for rows in listed.values() for row in rows if surname(str(row["name"]))}
        statewide: dict[tuple[str, str], set[UUID]] = {}
        for state, person, text in conn.execute(
            """select distinct split_part(x.value, ':', 2), cy.person_id, n.text
               from name n
               join candidacy cy on cy.person_id = n.entity_id
               join contest_option op on op.id = cy.option_id
               join external_id x on x.entity_id = op.contest_id and x.scheme = 'us.contest'
               where n.is_preferred and x.value ~ '^20(1[4-9]|2[0-6])-'
                 and lower(regexp_replace(n.text, '^.* ', '')) = any(%s)""",
            [sorted(surnames)],
        ).fetchall():
            statewide.setdefault((state, slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", text))), set()).add(person)
        for state, person, text in conn.execute(
            """select distinct substring(x.value from '20[0-9]{2}-[0-9-]+:([A-Z]{2}):'), cy.person_id, n.text
               from name n
               join candidacy cy on cy.person_id = n.entity_id
               join contest_option op on op.id = cy.option_id
               join external_id x on x.entity_id = op.contest_id and x.scheme = 'us.contest'
               where n.is_preferred and x.value ~ '^([a-z_.]+:)?20[0-2][0-9]-[0-9-]+:[A-Z]{2}:'
                 and lower(regexp_replace(n.text, '^.* ', '')) = any(%s)""",
            [sorted(surnames)],
        ).fetchall():
            by_state.setdefault(state, []).append((person, text))
        held: dict[str, dict[UUID, tuple[UUID, str]]] = {}
        for value, option, candidacy, person in conn.execute(
            """select x.value, op.id, cy.id, cy.person_id
               from external_id x
               join contest_option op on op.contest_id = x.entity_id
               join changeset c on c.id = op.changeset_id and c.pipeline = %s
               left join candidacy cy on cy.option_id = op.id
               where x.scheme = 'us.contest' and x.value like %s""",
            [SOURCE, f"{DAY}:%"],
        ).fetchall():
            held.setdefault(value, {})[person] = (option, candidacy)
        option_keys = dict(
            conn.execute(
                "select entity_id, value from external_id where scheme = 'us.option' and value like %s", [f"{DAY}:%"]
            ).fetchall()
        )
        candidacy_keys = dict(
            conn.execute(
                "select entity_id, value from external_id where scheme = 'us.candidacy' and value like %s", [f"{DAY}:%"]
            ).fetchall()
        )
        own_notes = conn.execute(
            """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
               where c.pipeline = %s and n.kind = 'methodology'""",
            [SOURCE],
        ).fetchall()
        sources: dict[str, UUID] = {}
        parties: dict[str, UUID] = {}
        party_keys = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }

        def party_of(state: str, label: str) -> UUID | None:
            label = label.split("/")[0].strip()
            if INDEPENDENT.match(label):
                return None
            key = next((key for pattern, key in MAJOR if pattern.search(label)), None)
            if key is None:
                key = next(
                    (key for key in (f"{state}:{slug(label)}", f"{state}:{slug(label)}-party") if key in party_keys),
                    f"{state}:{slug(label)}",
                )
            if key not in parties:
                parties[key] = cs.key("us.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=cs.polity(f"US-{state}"))
                    name(parties[key], label)
            return parties[key]

        def person_of(state: str, key: str, chamber: str, district: int | None, text: str) -> UUID:
            last, first = surname(text), slug(re.sub(r"^\W+", "", text))[:1]
            given = slug(re.sub(r"^\W+", "", text)).split("-")[0]
            pool = {
                person
                for person, held_text in known.get(key, [])
                if surname(held_text) == last
                and (" " not in text.strip() or slug(re.sub(r"^\W+", "", held_text))[:1] == first)
            }
            if len(pool) > 1 or not pool:
                pool = {
                    person
                    for person, held_text in [*known.get(key, []), *earlier.get(key, [])]
                    if surname(held_text) == last
                    and slug(held_text)[:1] == first
                    and (min(len(given), len(slug(held_text).split("-")[0])) < 2 or slug(held_text)[:2] == given[:2])
                }
            filed = [fec for fec, filer_last, _ in filers.get((state, chamber, district), []) if filer_last == last]
            if len({fec_persons.get(fec, fec) for fec in filed}) > 1:
                filed = [
                    fec
                    for fec, filer_last, filer_first in filers.get((state, chamber, district), [])
                    if filer_last == last and filer_first == first
                ]
            matched = {fec_persons[fec] for fec in filed if fec in fec_persons}
            if not pool and len(matched) == 1:
                pool = matched
            plain = slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", text))
            if not pool and "-" in plain:
                pool = statewide.get((state, plain), set())
            if not pool:
                tokens = slug(text).split("-")
                pool = {
                    person
                    for person, held_text in by_state.get(state, [])
                    if len(surname(held_text)) > 2
                    and "".join(
                        token
                        for token in slug(re.sub(r"[\"“][^\"”]*[\"”]|\([^)]*\)", " ", text)).split("-")
                        if not SUFFIX.match(token)
                    ).endswith(surname(held_text).replace("-", ""))
                    and len(slug(held_text).split("-")[0]) > 1
                    and slug(held_text).split("-")[0] in tokens
                    and set(re.findall(r"\b(?:II|III|IV)\b", text)) <= set(re.findall(r"\b(?:II|III|IV)\b", held_text))
                }
            for preferred in ({*held.get(key, {})}, {person for _, person in nominees.get(key, {})}, matched, fec_held):
                if len(pool) > 1 and pool & preferred:
                    pool &= preferred
            if len(pool) > 1:
                pool = {max(pool, key=lambda person: (latest.get(person, ""), str(person)))}
            if len(pool) == 1:
                person = next(iter(pool))
                free = [fec for fec in filed if fec not in fec_persons and fec not in dropped]
                if len(free) == 1 and person not in fec_held:
                    cs.add("external_id", entity_id=person, scheme="fec.candidate", value=free[0])
                    fec_persons[free[0]] = person
                    fec_held.add(person)
                return person
            if len({*filed}) == 1 and filed[0] not in dropped:
                person = cs.key("fec.candidate", filed[0])
            else:
                person = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, text)
            known.setdefault(key, []).append((person, text))
            return person

        staged_contests: set[str] = set()
        counts = {"house": 0, "senate": 0, "governor": 0, "statewide": 0, "candidates": 0, "listed": 0, "dropped": 0}
        for (
            value,
            kind,
            office,
            body,
            area,
            seat,
            plan,
            magnitude,
            term,
            system,
            result_kind,
            polity,
        ) in sorted(templates):
            _, state, chamber, *rest = value.split(":")
            if state in NO_GENERAL:
                continue
            state_name = state_names[state]
            if state not in elections:
                elections[state] = cs.key("us.election", f"{DAY}:{state}")
                cs.add(
                    "election",
                    id=elections[state],
                    polity_id=polity,
                    parent_id=national,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    status="scheduled",
                )
                name(elections[state], f"2026 {state_name} general election")
                cs.add("slug", path=f"us/{state.lower()}/{DAY}-general", entity_id=elections[state])
            key = f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else "")
            if key in staged_contests:
                continue
            staged_contests.add(key)
            contest = cs.key("us.contest", key)
            special = chamber == "senate" and state in CLASS_THREE_SPECIALS
            cs.add(
                "contest",
                id=contest,
                election_id=elections[state],
                kind=kind,
                office_id=office,
                body_id=body,
                district_area_id=area,
                seat_id=seat,
                boundary_set_id=latest_plan if chamber == "house" and state not in REDISTRICTED else None,
                magnitude=magnitude,
                term="remainder" if special else term,
                round_number=1,
                electoral_system_id=systems[SYSTEM_OVERRIDES.get(f"{state}:{chamber}", SYSTEM_OVERRIDES[state])]
                if state in SYSTEM_OVERRIDES and chamber in ("house", "senate")
                else system,
                default_result_kind=result_kind,
            )
            counts[chamber if chamber in counts else "statewide"] += 1
            if chamber == "house":
                number = int(rest[0])
                suffix = "th" if 10 <= number % 100 <= 20 else ORDINALS.get(number % 10, "th")
                at_large = (
                    conn.execute(
                        "select 1 from external_id where scheme = 'us.contest' and value = %s",
                        [f"2024-11-05:{state}:house:2"],
                    ).fetchone()
                    is None
                )
                title = (
                    f"2026 U.S. House election in {state_name}'s at-large congressional district"
                    if at_large
                    else f"2026 U.S. House election in {state_name}'s {number}{suffix} congressional district"
                )
                path = f"us/{state.lower()}/{DAY}-general/house-{number}"
            elif chamber == "senate":
                title = f"2026 U.S. Senate {'special ' if special else ''}election in {state_name}"
                path = f"us/{state.lower()}/{DAY}-general/senate"
            elif chamber == "governor":
                title = f"2026 {state_name} gubernatorial election"
                path = f"us/{state.lower()}/{DAY}-general/governor"
            else:
                title = re.sub(rf"^\d{{4}} {re.escape(state_name)} ", "2026 ", offices[f"{state}:{chamber}"])
                path = f"us/{state.lower()}/{DAY}-general/{chamber}"
            name(contest, title)
            cs.add("slug", path=path, entity_id=contest)
            if chamber == "house" and state == "LA":
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md="Louisiana Act 7 of 2026 cancelled the closed party primaries for the U.S. House and moved the races to the open primary on 2026-11-03: every candidate runs on one ballot, a majority wins outright, and otherwise the top two meet in a general election on 2026-12-12.",
                )
            if chamber == "house" and state in REDISTRICTED:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md=f"{state_name} redrew its congressional districts for 2026. The contest is recorded by district number; the new plan's boundaries are not loaded yet.",
                )
            staged: set[UUID] = set()
            entries: list[tuple[str, UUID, UUID | None]] = []
            if state in OFFICIAL and key in listed:
                publisher, title, urls = OFFICIAL[state]
                if state not in sources:
                    sources[state] = cs.source(publisher, title)
                    if sources[state] in cs.minted:
                        cs.add(
                            "source",
                            id=sources[state],
                            kind="official_body",
                            publisher=publisher,
                            title=title,
                            url=urls[0],
                            license=None,
                        )
                    for url in urls:
                        cs.artifact(raw.cached(SOURCE, url), sources[state])
                for row in sorted(listed[key], key=lambda row: str(row["name"])):
                    text, label = str(row["name"]), str(row["party"])
                    person = person_of(state, key, chamber, int(rest[0]) if chamber == "house" else None, text)
                    if " " not in text:
                        text = next((held for held_person, held in known.get(key, []) if held_person == person), text)
                    entries.append(
                        (
                            text,
                            person,
                            party_of(state, label)
                            if label
                            else next(
                                (party for (_, nominee), party in nominees.get(key, {}).items() if nominee == person),
                                None,
                            ),
                        )
                    )
                counts["listed"] += 1
            if state in SUPPLEMENTED or key not in listed:
                entries += [
                    (ballot, person, party_id)
                    for (ballot, person), party_id in sorted(
                        nominees.get(key, {}).items(), key=lambda nominee: (nominee[0][0], str(nominee[0][1]))
                    )
                ]
            for ballot, person, party_id in entries:
                if person in staged:
                    continue
                staged.add(person)
                previous = held.get(key, {}).get(person)
                fresh = f"{key}:{slug(ballot)}"
                if not previous and (
                    uuid5(NAMESPACE, f"external_id:us.option\x1f{fresh}") in cs.retired
                    or fresh in {option_keys.get(option) for option, _ in held.get(key, {}).values()}
                ):
                    fresh += ":2026-10-27"
                option = cs.key("us.option", option_keys[previous[0]] if previous else fresh)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=ballot,
                )
                candidacy = cs.key("us.candidacy", candidacy_keys[previous[1]] if previous and previous[1] else fresh)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    is_independent=party_id is None and state != "AS",
                )
                if state in sources and key in listed:
                    cs.add("citation", entity_id=candidacy, source_id=sources[state], locator=None)
                counts["candidates"] += 1
            reason = (
                f"Not on the certified {state_name} 2026 general election candidate list"
                if state in sources and key in listed
                else f"No longer a nominee in the archive's {state_name} 2026 primary result sets"
            )
            for person, (option, candidacy) in held.get(key, {}).items():
                if person not in staged:
                    if candidacy is not None:
                        cs.add(
                            "entity_retirement",
                            entity_id=candidacy,
                            reason=reason,
                        )
                    cs.add("entity_retirement", entity_id=option, reason=reason)
                    counts["dropped"] += 1
            if state in SUPPLEMENTED and key in listed:
                body = f"Candidates are the {state_name} nominees recorded in the archive's own 2026 primary result sets, the unopposed primary candidates, and the independent candidates qualified for the general election ballot on the {OFFICIAL[state][0]}'s {OFFICIAL[state][1]}. Declared write-in candidates are not listed."
            elif state in sources and key in listed:
                body = f"Candidates are those on the {OFFICIAL[state][0]}'s {OFFICIAL[state][1]}, as certified for the 2026-11-03 ballot. Declared write-in candidates are not listed."
            elif key in nominees:
                body = f"Candidates are the {state_name} nominees recorded in the archive's own 2026 primary result sets ({'the two advancing candidates of the top-two primary' if state in ('CA', 'WA') else 'the major-party primary winners'}). Where a party's primary was uncontested or has not been loaded, that party's nominee is not yet listed. No ballot list from the state has been confirmed, so third-party and independent candidates are not yet listed."
            else:
                body = None
            if body is not None:
                cs.add("note", entity_id=contest, kind="methodology", body_md=body)
            for note, entity, text in own_notes:
                if (
                    entity == contest
                    and text != body
                    and not text.endswith(
                        ("the new plan's boundaries are not loaded yet.", "meet in a general election on 2026-12-12.")
                    )
                ):
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=contest,
                        body_md=text,
                        reason="The contest's candidates now come from another list",
                    )
        scheduled = {
            f"{DAY}:{value.split(':')[1]}:{value.split(':')[2]}"
            + (f":{value.split(':')[3]}" if value.split(":")[2] == "house" else "")
            for value, *_ in templates
        }
        print("listed but not scheduled:", sorted({*listed} - scheduled))
        for entity, retired in conn.execute(
            """select x.entity_id, e.retired_by from external_id x join entity e on e.id = x.entity_id
               where (x.scheme, x.value) in (select 'us.contest', unnest(%s::text[]) union select 'us.election', unnest(%s::text[]))""",
            [[f"{DAY}:{state}:house:0" for state in NO_GENERAL], [f"{DAY}:{state}" for state in NO_GENERAL]],
        ).fetchall():
            if retired is None:
                cs.add(
                    "entity_retirement",
                    entity_id=entity,
                    reason="Puerto Rico's Resident Commissioner serves a four-year term, elected in 2024; Puerto Rico holds no general election on 2026-11-03",
                )
        print(counts)
        return cs.write()
