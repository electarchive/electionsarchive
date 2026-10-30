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

from ea import raw, readiness, statewide_table
from ea.changeset import NAMESPACE, Changeset, slug
from ea.names import initials
from ea.sources.us.oe_primaries_2026 import FIPS
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
            "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/TNSenate_Nov2026.xlsx#MF-140",
            "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/TNHouse_Nov2026.xlsx#MF-140",
        ],
    ),
    "AL": (
        "Alabama Secretary of State",
        "State certification of Republican and Democratic candidates, 2026 general election",
        [
            "https://www.sos.alabama.gov/sites/default/files/election-2026/2026GeneralElectionStateCertificationofRepublicanCandidates.pdf",
            "https://www.sos.alabama.gov/sites/default/files/election-2026/2026GeneralElectionStateCertificationofDemocraticCandidates.pdf",
            "https://www.sos.alabama.gov/sites/default/files/election-2026/Amended_2026_Republican_Certification.pdf",
            "https://www.sos.alabama.gov/sites/default/files/election-2026/AmendedCertificationofDemocraticPartyCandidates.pdf",
            "https://www.sos.alabama.gov/sites/default/files/election-2026/2026GeneralElection-StateCertificationofIndependentCandidate.pdf",
        ],
    ),
    "SC": (
        "South Carolina State Election Commission",
        "Candidate listing, statewide general election, November 3, 2026",
        [
            "https://vrems.scvotes.sc.gov/Candidate/CandidateSearchDate?electionDate=11%2F03%2F2026#browser",
            "https://vrems.scvotes.sc.gov/Candidate/CandidateSearchDate?electionDate=11%2F03%2F2026&SelectedElections=22596&statewide=1#browser",
        ],
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
            "https://www.elections.alaska.gov/candidates/?election=26genr",
            "https://www.elections.alaska.gov/candidates/?election=26genr&frm-page-407=2",
            "https://www.elections.alaska.gov/candidates/?election=26genr&frm-page-407=3",
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
    "ID": (
        "Idaho Secretary of State",
        "Final candidates list, November 3, 2026 general election",
        ["https://run.voteidaho.gov/search#MF-139"],
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
    "lt-governor": "lieutenant-governor",
    "mayor-of-the-district-of-columbia": "mayor",
}
SUFFIX = re.compile(r"(?i)^(jr|sr|ii|iii|iv|v)\.?$")
DAY = date(2026, 11, 3)
REDISTRICTED = {"AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT"}
REDRAWN_LOADED = {"AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT"}
CLASS_THREE_SPECIALS = ("OH", "FL")
NO_GENERAL = ("PR",)
SUPPLEMENTED = {"RI", "AR", "NV", "MN"}
UNEXPIRED_2024 = {
    ("OH", "senate:33:g"),
    ("UT", "senate:12:g"),
    *(("IL", f"senate:{district}:g") for district in (5, 20, 27, 53)),
}
REMEDIAL_2024 = {("ND", "house:9:gs"): ("house:9-a:g", "house:9-b:g"), ("UT", "senate:21:gs"): ()}
HOLDOVER_2020 = {("MT", "upper")}
VACANCY_2026 = {
    ("CO", "senate:17:g"),
    ("CO", "senate:21:g"),
    ("CO", "senate:29:g"),
    ("CO", "senate:31:g"),
    ("WY", "senate:6:g"),
    ("NM", "senate:33:g"),
}
SPECIAL_2026 = {
    ("AR", "senate:1"): "senate:1",
    ("AR", "house:8"): "house:8",
    ("FL", "senate:21"): "senate:21",
    ("HI", "senate:18"): "senate:18",
    ("HI", "senate:19"): "senate:19",
    ("KS", "senate:24"): "senate:24",
    ("KS", "senate:25"): "senate:25",
    ("NE", "legislature:41"): "legislature:41",
    ("ND", "house:20"): "house:20",
    ("ND", "house:26"): "house:26",
    ("ND", "house:42"): "house:42",
    ("OK", "senate:17"): "senate:17",
    ("SC", "senate:15"): "senate:15",
    ("TX", "senate:22"): "senate:22",
    ("TX", "house:93"): "house:93",
    ("VA", "house-of-delegates:20"): "house-of-delegates:20",
    ("WV", "senate:3:1"): "senate:3",
    ("WV", "senate:17:1"): "senate:17",
}
TX_SPECIAL = "https://goelect.txelections.civixapps.com/api-ivis-cbp/api/cbp/findQualifiedCandidates#{}"
SPECIAL_LISTS = {
    "FL": (
        "Florida Department of State, Division of Elections",
        "Candidate listing, 2026 special election, State Senate District 21",
        [FL_URL.replace("-GEN", "-S01")],
    ),
    "SC": (
        "South Carolina State Election Commission",
        "Candidate listing, State Senate District 15 special election, November 3, 2026",
        ["https://vrems.scvotes.sc.gov/Candidate/CandidateSearch?electionId=22758#browser"],
    ),
    "TX": (
        "Texas Secretary of State",
        "Qualified candidates, 2026 special elections for Senate District 22 and House District 93",
        [TX_SPECIAL.format(66618), TX_SPECIAL.format(66734)],
    ),
    "VA": (
        "Virginia Department of Elections",
        "November 3, 2026 House of Delegates District 20 special election candidate list",
        [
            "https://www.elections.virginia.gov/media/castyourballot/candidatelist/2026/2026-November-HD20-Candidate-List.xlsx"
        ],
    ),
}
SPLIT = {
    "HI:trustee-of-the-office-of-hawaiian-affairs": {
        "At-Large Trustee": ("", 3, "at-large"),
        "Maui Resident Trustee": ("maui", 1, "Maui resident"),
        "Oahu Resident Trustee": ("oahu", 1, "Oahu resident"),
    },
    "AL:public-service-commissioner": {
        "Public Service Commissioner, Place 1": ("place-1", 1, "Place 1"),
        "Public Service Commissioner, Place 2": ("place-2", 1, "Place 2"),
    },
    "GA:public-service-commissioner": {
        "Public Service Commissioner, District 3": ("district-3", 1, "District 3"),
        "Public Service Commissioner, District 5": ("district-5", 1, "District 5"),
    },
}
TACIT_SOURCES = {
    "HI": (
        "Hawaii Office of Elections",
        "2026 primary election summary report",
        ["https://elections.hawaii.gov/wp-content/results/2026%20Primary/summary.txt"],
        "Hawaii declares a nominee elected after the primary when no other candidate remains for the general election (Haw. Rev. Stat. §12-41(a)), so the seat is not on the general election ballot. The nominee is the only candidate left after the 2026-08-08 primary.",
    ),
    "OK-filing": (
        "Oklahoma State Election Board",
        "2026 candidate list book and candidate withdrawals",
        [
            "https://oklahoma.gov/elections/candidates/2026-candidate-filing-information.html",
            "https://oklahoma.gov/elections/candidates/2026-candidate-filing-information/2026-candidate-withdrawals.html",
        ],
        "Oklahoma leaves a race off the ballot when only one candidate remains after the April 1–3, 2026 filing period and withdrawals, and declares that candidate elected, so no votes are cast for it.",
    ),
    "OK-primary": (
        "Oklahoma State Election Board",
        "June 16, 2026 primary election results by county",
        ["https://results.okelections.gov/OKER/?elecDate=20260616"],
        "Every candidate left for this seat belonged to one party, so that party's 2026-06-16 primary decided it; Oklahoma leaves the seat off the general election ballot and declares the nominee elected.",
    ),
    "OK-runoff": (
        "Oklahoma State Election Board",
        "August 25, 2026 primary runoff election results by county",
        ["https://results.okelections.gov/OKER/?elecDate=20260825"],
        "Every candidate left for this seat belonged to one party, so that party's 2026-08-25 primary runoff decided it; Oklahoma leaves the seat off the general election ballot and declares the nominee elected.",
    ),
}
TACIT = {
    "HI": {
        "house:1:g": ("Matthias Kusch", "democratic-party", "HI"),
        "house:6:g": ("Kirstin Kahaloa", "democratic-party", "HI"),
        "house:7:g": ("Nicole Lowen", "democratic-party", "HI"),
        "house:9:g": ("Justin H. Woodson", "democratic-party", "HI"),
        "house:15:g": ("Nadine K. Nakamura", "democratic-party", "HI"),
        "house:19:g": ("Mark Jun Hashem", "democratic-party", "HI"),
        "house:21:g": ("Derek Turbin", "democratic-party", "HI"),
        "house:22:g": ("Andrew Takuya Garrett", "democratic-party", "HI"),
        "house:24:g": ("Adrian Tam", "democratic-party", "HI"),
        "house:25:g": ("Kim Coco Iwamoto", "democratic-party", "HI"),
        "house:27:g": ("Jenna W.Y.C. Takenouchi", "democratic-party", "HI"),
        "house:28:g": ("Michael (Cov) Ratcliffe", "democratic-party", "HI"),
        "house:33:g": ("Sam Satoru Kong", "democratic-party", "HI"),
        "house:35:g": ("Cory M. Chun", "democratic-party", "HI"),
        "house:36:g": ("Daisy Balais Hartsfield", "democratic-party", "HI"),
        "house:37:g": ("Trish La Chica", "democratic-party", "HI"),
        "house:38:g": ("Lauren Cheape Matsumoto", "republican-party", "HI"),
        "house:41:g": ("David A. Alcos III", "republican-party", "HI"),
        "house:48:g": ("Lisa C. Kitagawa", "democratic-party", "HI"),
        "house:51:g": ("Lisa Marten", "democratic-party", "HI"),
        "senate:5:g": ("Troy N. Hashimoto", "democratic-party", "HI"),
        "senate:8:g": ("Ronald D. Kouchi", "democratic-party", "HI"),
        "senate:10:g": ("Jackson D. Sayama", "democratic-party", "HI"),
        "senate:11:g": ("Carol Fukunaga", "democratic-party", "HI"),
        "senate:14:g": ("Donna Mercado Kim", "democratic-party", "HI"),
        "senate:15:g": ("Glenn Wakai", "democratic-party", "HI"),
        "senate:17:g": ("Donovan Dela Cruz", "democratic-party", "HI"),
        "senate:21:g": ("Mike Gabbard", "democratic-party", "HI"),
        "senate:25:g": ("Chris Lee", "democratic-party", "HI"),
    },
    "OK": {
        "auditor-and-inspector": ("Melissa Capps", "republican-party", "OK-filing"),
        "house:1:g": ("Chris White", "republican-party", "OK-runoff"),
        "house:2:g": ("Jim Olsen", "republican-party", "OK-filing"),
        "house:5:g": ("Josh West", "republican-party", "OK-filing"),
        "house:6:g": ("Rusty Cornwell", "republican-party", "OK-primary"),
        "house:7:g": ("Steve Bashore", "republican-party", "OK-filing"),
        "house:8:g": ("Tom Gann", "republican-party", "OK-primary"),
        "house:11:g": ("John B. Kane", "republican-party", "OK-primary"),
        "house:13:g": ("Neil Hays", "republican-party", "OK-filing"),
        "house:15:g": ("Tim Turner", "republican-party", "OK-filing"),
        "house:17:g": ("Jim Grego", "republican-party", "OK-filing"),
        "house:18:g": ("David Smith", "republican-party", "OK-runoff"),
        "house:19:g": ("Michael A. Brittingham", "republican-party", "OK-runoff"),
        "house:21:g": ("Cody Maynard", "republican-party", "OK-filing"),
        "house:22:g": ("Ryan Eaves", "republican-party", "OK-filing"),
        "house:25:g": ("Ronny Johns", "republican-party", "OK-filing"),
        "house:29:g": ("Kyle Hilbert", "republican-party", "OK-primary"),
        "house:31:g": ("Collin Duel", "republican-party", "OK-primary"),
        "house:35:g": ("Dillon Travis", "republican-party", "OK-primary"),
        "house:37:g": ("Spencer Grace", "republican-party", "OK-primary"),
        "house:39:g": ("Erick Harris", "republican-party", "OK-primary"),
        "house:40:g": ("Kinsley Jordan", "republican-party", "OK-primary"),
        "house:42:g": ("Cindy Roe", "republican-party", "OK-primary"),
        "house:43:g": ("Jay Steagall", "republican-party", "OK-filing"),
        "house:44:g": ("Jared Deck", "democratic-party", "OK-filing"),
        "house:46:g": ("Jacob Rosecrants", "democratic-party", "OK-filing"),
        "house:48:g": ("Tammy Townley", "republican-party", "OK-filing"),
        "house:50:g": ("Stacy Jo Adams", "republican-party", "OK-filing"),
        "house:51:g": ("Cole Stevens", "republican-party", "OK-primary"),
        "house:52:g": ("Gerrid Kendrix", "republican-party", "OK-filing"),
        "house:55:g": ("Nick Archer", "republican-party", "OK-filing"),
        "house:57:g": ("Anthony S. Moore", "republican-party", "OK-filing"),
        "house:59:g": ("Mike Dobrinski", "republican-party", "OK-filing"),
        "house:61:g": ("Kenton Patzkowsky", "republican-party", "OK-filing"),
        "house:65:g": ("Sam Mitchell", "republican-party", "OK-primary"),
        "house:67:g": ("Rob Hall", "republican-party", "OK-filing"),
        "house:70:g": ("Suzanne E. Schreiber", "democratic-party", "OK-filing"),
        "house:71:g": ("Amanda Clinton", "democratic-party", "OK-filing"),
        "house:73:g": ("Ron Stewart", "democratic-party", "OK-primary"),
        "house:77:g": ("Kristina Gabriel", "democratic-party", "OK-primary"),
        "house:88:g": ("Ellen Pogemiller", "democratic-party", "OK-filing"),
        "house:89:g": ("Arturo Alonso", "democratic-party", "OK-filing"),
        "house:92:g": ("Sam Wargin Grimaldo", "democratic-party", "OK-primary"),
        "house:93:g": ("Mickey Dollens", "democratic-party", "OK-filing"),
        "house:94:g": ("Andy Fugate", "democratic-party", "OK-filing"),
        "house:97:g": ("Aletia Haynes Timmons", "democratic-party", "OK-primary"),
        "house:101:g": ("Robert Manger", "republican-party", "OK-filing"),
        "senate:14:g": ("Jerry Alvord", "republican-party", "OK-filing"),
        "senate:16:g": ("Mary B. Boren", "democratic-party", "OK-filing"),
        "senate:20:g": ("Chuck Hall", "republican-party", "OK-primary"),
        "senate:26:g": ("Jessica Winegeart", "republican-party", "OK-runoff"),
        "senate:30:g": ("Julia Kirt", "democratic-party", "OK-filing"),
        "senate:38:g": ("Rick Vernon", "republican-party", "OK-primary"),
        "senate:40:g": ("Carri Hicks", "democratic-party", "OK-filing"),
        "senate:44:g": ("Michael Brooks", "democratic-party", "OK-filing"),
        "senate:46:g": ("Mark Mann", "democratic-party", "OK-filing"),
        "senate:48:g": ("Nikki Nice", "democratic-party", "OK-filing"),
    },
}
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
    "AmendedCertificationofDemocraticPartyCandidates.pdf": [
        ("Lieutenant Governor", None, "Phillip Ensler"),
        ("Attorney General", None, "Jeff McLaughlin"),
        ("Secretary of State", None, "Wayne Rogers"),
        ("Treasurer", None, "Rosilyn Houston"),
        ("Auditor", None, "Violet Edwards"),
        ("Commissioner of Agriculture and Industries", None, "Ron Sparks"),
        ("Public Service Commissioner, Place 1", None, "James O. Gordon"),
        ("Public Service Commissioner, Place 2", None, "Sheila D. McNeil"),
        ("State Senator, District 2", None, "Rudolph Valentino Drake"),
        ("State Senator, District 3", None, "John Dickey"),
        ("State Senator, District 6", None, "Bobby Martin"),
        ("State Senator, District 7", None, "Jared Sluss"),
        ("State Senator, District 8", None, "Katie Kramer"),
        ("State Senator, District 11", None, "Donald J. Mottern"),
        ("State Senator, District 13", None, "Jamie Forsyth"),
        ("State Senator, District 14", None, "Mandie Ledkins"),
        ("State Senator, District 16", None, "Spencer Stone"),
        ("State Senator, District 18", None, "Rodger M. Smitherman"),
        ("State Senator, District 19", None, "Merika Coleman"),
        ("State Senator, District 20", None, "Linda Coleman-Madison"),
        ("State Senator, District 22", None, "Ashley Sharpe"),
        ("State Senator, District 23", None, "Robert Stewart"),
        ("State Senator, District 24", None, "Bobby Singleton"),
        ("State Senator, District 25", None, "Phadra Carson Foster"),
        ("State Senator, District 26", None, "Kirk Hatcher"),
        ("State Senator, District 28", None, "Billy Beasley"),
        ("State Senator, District 32", None, "Janet Appleby"),
        ("State Senator, District 33", None, "Vivian Davis Figures"),
        ("State Representative, District 2", None, "Rick Pressnell"),
        ("State Representative, District 4", None, "Hanu Karlapalem"),
        ("State Representative, District 5", None, "Jessie Barcala"),
        ("State Representative, District 8", None, "Janine Maria Jordan"),
        ("State Representative, District 10", None, "Marilyn Lands"),
        ("State Representative, District 15", None, "Nell Brown"),
        ("State Representative, District 16", None, "Christian Martin"),
        ("State Representative, District 19", None, "Laura Hall"),
        ("State Representative, District 20", None, "James Linderholm"),
        ("State Representative, District 21", None, "Forrest Satterfield"),
        ("State Representative, District 25", None, "Allison T Montgomery"),
        ("State Representative, District 28", None, "Robert Louis Hunter"),
        ("State Representative, District 32", None, "Debra D Foster"),
        ("State Representative, District 37", None, "Michelle French"),
        ("State Representative, District 38", None, "Hazel Floyd"),
        ("State Representative, District 40", None, "Pam Howard"),
        ("State Representative, District 41", None, "David J.A. Morgan"),
        ("State Representative, District 43", None, "Taylor Cook"),
        ("State Representative, District 45", None, "Toni Kornegay Vaughn"),
        ("State Representative, District 47", None, "Jim Toomey"),
        ("State Representative, District 50", None, "Alan Thomas"),
        ("State Representative, District 52", None, "GiGi Hayes"),
        ("State Representative, District 53", None, "Anthony Daniels"),
        ("State Representative, District 54", None, "Neil Rafferty"),
        ("State Representative, District 55", None, "Travis Hendrix"),
        ("State Representative, District 56", None, "Ontario J Tillman"),
        ("State Representative, District 57", None, "Patrick Sellers"),
        ("State Representative, District 58", None, "Rolanda Hollis"),
        ("State Representative, District 59", None, "Mary Moore"),
        ("State Representative, District 60", None, "Alicia Escott Lumpkin"),
        ("State Representative, District 63", None, "Judith Taylor"),
        ("State Representative, District 67", None, "Prince Chestnut"),
        ("State Representative, District 68", None, 'Thomas E "Action" Jackson'),
        ("State Representative, District 69", None, "Kelvin J Lawrence"),
        ("State Representative, District 70", None, "Christopher John England"),
        ("State Representative, District 71", None, 'Artis "A.J." McCampbell'),
        ("State Representative, District 72", None, "Curtis L Travis"),
        ("State Representative, District 74", None, "Jasmyne Crimiel"),
        ("State Representative, District 75", None, "Tisha Dickson Nickson"),
        ("State Representative, District 76", None, 'Patrice "Penni" McClammy'),
        ("State Representative, District 77", None, "TaShina Morris"),
        ("State Representative, District 78", None, "Kenyatté Hassell"),
        ("State Representative, District 80", None, "Manfred Malone"),
        ("State Representative, District 82", None, "Pebblin W. Warren"),
        ("State Representative, District 83", None, "Jeremy A. Gray"),
        ("State Representative, District 84", None, "Berry Forte"),
        ("State Representative, District 85", None, "Aristotle Onassis Kirkland"),
        ("State Representative, District 96", None, "Terri Osborne"),
        ("State Representative, District 97", None, "Adline C. Clarke"),
        ("State Representative, District 98", None, "Napoleon Bracy, Jr."),
        ("State Representative, District 99", None, "Sam Jones"),
        ("State Representative, District 101", None, "Joan Friedlander"),
        ("State Representative, District 103", None, "Barbara Drummond"),
    ],
    "2026GeneralElection-StateCertificationofIndependentCandidate.pdf": [
        ("State Senator, District 10", None, "Jesse Battles"),
        ("State Senator, District 21", None, "Jodie Woodrow Minor, Jr."),
        ("State Representative, District 7", None, "Trent Ford"),
        ("State Representative, District 7", None, 'Angelo "Doc" Mancuso'),
        ("State Representative, District 35", None, "Joseph Cleveland"),
        ("State Representative, District 65", None, "Elaine Beech"),
    ],
}
ORDINAL_WORDS = [
    *[
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
        "thirteenth",
        "fourteenth",
        "fifteenth",
        "sixteenth",
        "seventeenth",
        "eighteenth",
        "nineteenth",
    ],
    *(
        f"{tens}{suffix}"
        for tens in ("twent", "thirt")
        for suffix in (
            "ieth",
            *(
                f"y-{unit}"
                for unit in ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"]
            ),
        )
    ),
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
    if state == "VA" and path.suffix == ".xlsx":
        for row in fastexcel.read_excel(path).load_sheet(0, dtypes="string").to_polars().iter_rows(named=True):
            entry = (
                f"{row['Office Title']} (special)",
                int(re.sub(r"\D", "", row["District"])),
                row["Candidate Name"],
                row["Candidate Party"],
            )
            if entry not in rows:
                rows.append(entry)
        return rows
    if state == "TX" and path.suffix == ".json":
        for row in json.loads(path.read_text()):
            if row["cdStatus"] == "A" and row["flActive"]:
                party = {"R": "Republican", "D": "Democratic", "L": "Libertarian", "G": "Green"}.get(
                    row["cdParty"], "Independent"
                )
                rows.append((row["txOfficeName"], None, row["txFullNameBallot"], party))
        return rows
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
                if row["District Type"].lower() == "legislative" and number:
                    race, _, position = row["Race"].partition(" Pos. ")
                    chamber = f"{race}, District {number[0]}" + (f", Position {position}" if position else "")
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
        for contest, text, party, _, seat, _, withdrawn, *_ in (row for row in table if len(row) >= 7):
            if text and not withdrawn and contest != "Contest":
                chamber = {
                    "United States Senator": "senate",
                    "United States Representative": "house",
                    "Governor and Lieutenant Governor": "governor",
                }.get(contest, f"{contest}, {seat}" if contest.startswith("State ") and seat else contest)
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
    if state == "AK" and "<h2>2026 General Election</h2>" in path.read_text():
        for section in path.read_text().split('<section class="candidates-table">')[1:]:
            heading = re.search(r"<h4>(?:<a[^>]*></a>)?(HOUSE|SENATE) DISTRICT (\w+)</h4>", section)
            for flag, text, party in re.findall(
                r'<span style="color: #e50000; font-weight: bold;">([^<]*)</span>\s*<a id="[^"]*"></a><strong>([^<]+)</strong><br>\s*<small>\(([^)]*)\)\s*\(Certified\)</small>',
                section,
            ):
                if heading and not flag:
                    last, _, first = html.unescape(text).partition(", ")
                    office = "State Representative" if heading[1] == "HOUSE" else "State Senator"
                    rows.append((f"{office}, District {heading[2]}", None, f"{first} {last}", party))
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
        split = False
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
                if texts == ["Office:", "District:", "Counties:", "Vote For:"]:
                    split = True
                    continue
                if split:
                    split = False
                    texts = ["Office:", texts[0], f"District: {texts[1]}"]
                if texts and re.fullmatch(r"\w*Office: .+", texts[0]):
                    texts = ["Office:", texts[0].split(": ", 1)[1], *texts[1:]]
                if texts and texts[0] == "Office:":
                    race = texts[1]
                    office = {
                        "Governor and Lt. Governor": "governor",
                        "Representative in Congress": "house",
                        "United States Senator": "senate",
                    }.get(race, race)
                    found = [text for text in texts if text.startswith("District:")]
                    district = (
                        int(found[0].split()[-1])
                        if office not in ("senate", "governor") and found and found[0].split()[-1].isdigit()
                        else None
                    )
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
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for line in text.splitlines():
            match = re.match(
                r"^(Lt\. Governor|Attorney General|Secretary of State|State Treasurer|State Auditor|Commissioner of Agriculture and Industries|Public Service Commission Place \d|Alabama State Senate District \d+|Alabama House of Representatives District \d+)\s{2,}(\S.*)$",
                line,
            )
            if match:
                office = re.sub(
                    r"^Alabama (State Senate|House of Representatives) District",
                    lambda m: "State Senator, District" if m[1] == "State Senate" else "State Representative, District",
                    match[1],
                ).replace("Public Service Commission Place", "Public Service Commissioner, Place")
                rows.append((office, None, match[2].strip(), "Republican"))
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
        for _, race, _, text, _, party, _, status in (
            row if len(row) == 8 else ["", *row]
            for row in json.loads(path.read_text())["rows"][1:]
            if len(row) in (7, 8)
        ):
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
            if match := re.match(r"(Jr|Sr|II|III|IV)\.?, (.+)", given):
                last, given = f"{last} {match[1]}", match[2]
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
                r"|Justice of the Peace|Justice|Mayor|Sheriff|School Board Director) ",
                "",
                row["Candidate Name"],
            ).replace(", ", " ")
            if race.startswith(("State Senate", "State Representative")) and row["Filing Date"] >= "2026-07-01":
                office = f"{race} (special)"
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
            elif (
                not line.strip()
                or line == "PartisanCandidates"
                or "List of Candidates & Measures" in line
                or line.isdigit()
            ):
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
    if state == "ID":
        for row in fastexcel.read_excel(path).load_sheet(0, dtypes="string").to_polars().iter_rows(named=True):
            if row["Withdrawal Date"] or row["Write-In"]:
                continue
            race, district, seat = row["Office"].strip(), row["District"] or "", row["Seat/Zone"] or ""
            office = {
                "United States Senator": "senate",
                "United States Representative": "house",
                "Governor": "governor",
            }.get(
                race,
                ", ".join(
                    part for part in (race, district and f"District {district}", seat and f"Seat {seat}") if part
                ),
            )
            party = "Nonpartisan" if row["Party Affiliation"] == "Non-Partisan" else row["Party Affiliation"] or ""
            rows.append((office, int(district) if office == "house" else None, row["Ballot Name"], party))
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
                elif office and (match := re.match(r"^\d+\s+(?:\d{7,8}\s+)?(\S.*?)\s{2,}(\S.*)$", stripped)):
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
                    }.get(
                        stripped,
                        re.sub(r"^(\w+) County$", lambda match: f"State Representative, {match[1].title()}", stripped),
                    )
                    district = None
                elif office and len(fields) >= 4:
                    party = {
                        "DEM": "Democratic",
                        "REP": "Republican",
                        "LIB": "Libertarian",
                        "CON": "Constitution",
                        "IND": "Independent",
                    }.get(fields[-1].split()[-1].upper(), fields[-1])
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
                        "For Member of the Legislature": "State Senator",
                    }.get(race, race.removeprefix("For "))
                    number = int(fields[1].split()[-1]) if at == 2 else None
                    if chamber == "State Senator":
                        rows.append((chamber, number, fields[at + 2], ""))
                    else:
                        rows.append((chamber, number, fields[at + 3].split(" and ")[0], fields[at + 2]))
                elif (
                    rows
                    and rows[-1][0] != "State Senator"
                    and len(fields) == 1
                    and fields[0]
                    and 50 <= len(line) - len(line.lstrip()) <= 75
                ):
                    rows[-1] = (*rows[-1][:3], rows[-1][3] + " " + fields[0])
            if state == "UT":
                if match := re.match(
                    r"^\s*(U\.S\. House|Utah Senate|Utah House)(?:\s*of\s*Rep\S*)?\s+Dis\S*\s+(\d+)\.",
                    line,
                ):
                    office = {
                        "U.S. House": "house",
                        "Utah Senate": "State Senator",
                        "Utah House": "State Representative",
                    }[match[1]]
                    district = int(match[2])
                elif re.match(r"^[A-Z][A-Z ]+$", line.strip()) and len(line.strip()) > 12:
                    office = None
                elif office and (match := re.match(r"^\s*([A-Z\"][A-Z.'\-\" ]+[A-Z.])\s{3,}(\S.*)$", line)):
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
                elif header and office == "State Auditor" and line.strip():
                    rows.append((office, None, line.strip(), header))
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
            rows.append(
                (
                    office,
                    int(house[1]) if house else None,
                    row["Candidate"],
                    row.get("Party Name") or row["Party"] or "",
                )
            )
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
                office = {
                    "US": "senate",
                    "CG": "house",
                    "GOV": "governor",
                    "SS": "State Senator",
                    "SR": "State Representative",
                }.get(row["Office"], row["Office"])
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
                if row["Contest"] in ("STATE SENATOR", "STATE REPRESENTATIVE"):
                    office += ", " + " ".join(
                        {
                            "ADD": "Addison",
                            "BEN": "Bennington",
                            "CAL": "Caledonia",
                            "CHI": "Chittenden",
                            "ESX": "Essex",
                            "FRA": "Franklin",
                            "GI": "Grand Isle",
                            "LAM": "Lamoille",
                            "ORA": "Orange",
                            "ORL": "Orleans",
                            "RUT": "Rutland",
                            "WAS": "Washington",
                            "WDH": "Windham",
                            "WDR": "Windsor",
                            "CT": "Central",
                            "N": "North",
                            "SE": "Southeast",
                        }.get(part, part)
                        for part in row["District Name"].split()
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
    for _, _, urls in [*OFFICIAL.values(), *SPECIAL_LISTS.values()]:
        for url in urls:
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                if url.endswith("#browser"):
                    print(f"{url} is a browser scrape; store it with raw.store")
                    continue
                if "#MF-" in url:
                    folder = DATA / "inbox" / "manual" / url.split("#")[1]
                    named = folder / url.split("#")[0].rsplit("/", 1)[-1]
                    delivered = named if named.exists() else next(folder.iterdir())
                    raw.store(
                        SOURCE,
                        url,
                        delivered.read_bytes(),
                        suffix=delivered.suffix,
                        retrieved_at=datetime.fromtimestamp(delivered.stat().st_mtime, UTC),
                        media_type="",
                    )
                    continue
            if url.startswith(FL_URL.split("?")[0]):
                response = httpx.post(
                    url.split("?")[0],
                    data={
                        "elecID": url.split("=")[-1],
                        "office": "All",
                        "status": "All",
                        "cantype": "STA",
                        "FormsButton2": "Download",
                    },
                    headers=BROWSER,
                    timeout=300,
                )
            elif url.startswith(TX_SPECIAL.split("#")[0]):
                response = httpx.post(
                    url.split("#")[0],
                    json={
                        "electionYear": 2026,
                        "electionId": int(url.split("#")[1]),
                        "party": None,
                        "officeId": None,
                        "officeType": None,
                        "status": None,
                        "countyId": None,
                    },
                    headers={**BROWSER, "Accept": "application/json"},
                    timeout=60,
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
                if url.startswith(FL_URL.split("?")[0])
                else ".json"
                if url.startswith(TX_SPECIAL.split("#")[0])
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
            "office": f"{office} (special)"
            if special and not re.search(r"(?i)vacancy|special|unexpired", office)
            else office,
            "district": 0 if office == "house" and district is None else district,
            "name": initials(
                re.sub(
                    r"\bMc([a-z])",
                    lambda match: "Mc" + match[1].upper(),
                    re.sub(r"\s+", " ", text).strip().rstrip("*").title(),
                )
                if text.isupper()
                else re.sub(r"\s+", " ", text).strip().rstrip("*")
            ),
            "party": party.strip().title() if party.isupper() and len(party) > 4 else party.strip(),
            "url": url,
        }
        for state, (_, _, urls), special in [
            *((state, entry, False) for state, entry in OFFICIAL.items()),
            *((state, entry, True) for state, entry in SPECIAL_LISTS.items()),
        ]
        for url in urls
        for office, district, text, party in [
            *read(state, raw.cached(SOURCE, url).path),
            *(
                (
                    office,
                    district,
                    text,
                    "Republican" if "Republican" in url else "Democratic" if "Democratic" in url else "Independent",
                )
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
            for st, office, _, when, joint, _, _ in statewide_table.schedule(conn, DAY.year)
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
                 and co.term is distinct from 'remainder'
                 and x.value ~ '^(wikipedia:(?=[0-9-]+:DC:1150000:mayor$))?[0-9-]+:[A-Z]{2}:(1150000:)?[a-z-]+(:place-[0-9]+)?$'
               order by co.office_id, e.date_from desc""",
            [list(due)],
        ).fetchall():
            template[0] = re.sub(r"^wikipedia:|:1150000|:place-\d+$", "", template[0])
            label = re.sub(
                r" \(Place \d+\)$", "", label.replace(" Washington mayoral", " District of Columbia mayoral")
            )
            _, state, chamber = template[0].split(":")
            if f"{state}:{chamber}" not in offices:
                offices[f"{state}:{chamber}"] = label
                if f"{state}:{chamber}" in SPLIT:
                    templates.extend(
                        (":".join(filter(None, (template[0], seat))), *template[1:7], magnitude, *template[8:])
                        for seat, magnitude, _ in SPLIT[f"{state}:{chamber}"].values()
                    )
                else:
                    templates.append(tuple(template))
        chambered: dict[tuple[str, str, str], dict[str, tuple[tuple, str, str]]] = {}
        for *template, label, path in conn.execute(
            """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id,
                      co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, e.polity_id, n.text, s.path
               from contest co
               join election e on e.id = co.election_id
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               join name n on n.entity_id = co.id and n.is_preferred
               join slug s on s.entity_id = co.id
               where e.date_from in ('2020-11-03', '2022-11-08', '2024-11-05', '2025-11-04') and co.party_scope_id is null
                 and x.value ~ '^[0-9-]+:[A-Z]{2}:(house|senate|assembly|house-of-delegates|legislature):.+:gs?$'"""
        ).fetchall():
            day, state, seat = template[0].split(":", 2)
            if day.startswith("2024") and (state, seat) in UNEXPIRED_2024:
                continue
            level = "upper" if seat.startswith(("senate:", "legislature:")) else "lower"
            if seat.endswith(":gs"):
                if (state, seat) not in REMEDIAL_2024:
                    continue
                template[8] = "full"
                seat, day = seat.removesuffix("s"), "2022"
                label, path = label.replace(" special election", " election"), path.removesuffix("-special-election")
            chambered.setdefault((state, level, day[:4]), {})[seat] = (tuple(template), label, path)
        for (state, _), replaced in REMEDIAL_2024.items():
            for seat in replaced:
                chambered[(state, "lower", "2022")].pop(seat)
        legislative: dict[str, tuple[str, str, str]] = {}
        seats: dict[tuple[str, str, str], str] = {}
        for part in readiness.spec(str(DAY))["levels"]:
            if part["key"] not in ("state_upper", "state_lower"):
                continue
            level = part["key"].removeprefix("state_")
            for state, want in part["states"].items():
                recent = chambered.get((state, level, "2024"), {})
                full = {**chambered.get((state, level, "2022"), {}), **recent}
                picked = full
                if sum(entry[0][7] or 1 for entry in full.values()) != want:
                    picked = {seat: entry for seat, entry in full.items() if seat not in recent}
                    parities = {int(re.sub(r"\D", "", seat.split(":")[1]) or 0) % 2 for seat in recent}
                    if sum(entry[0][7] or 1 for entry in picked.values()) > want and len(parities) == 1:
                        picked = {
                            seat: entry
                            for seat, entry in full.items()
                            if int(re.sub(r"\D", "", seat.split(":")[1]) or 0) % 2 not in parities
                        }
                if (state, level) in HOLDOVER_2020:
                    picked |= {
                        seat: entry
                        for seat, entry in chambered.get((state, level, "2020"), {}).items()
                        if seat not in full
                    }
                if sum(entry[0][7] or 1 for entry in picked.values()) != want:
                    print(
                        "legislative seats differ:",
                        state,
                        level,
                        sum(entry[0][7] or 1 for entry in picked.values()),
                        want,
                    )
                for seat, (template, label, path) in picked.items():
                    if (state, seat) in VACANCY_2026:
                        continue
                    key = f"{DAY}:{state}:{seat}"
                    legislative[template[0]] = (
                        key,
                        re.sub(r"^\d{4}", "2026", label),
                        re.sub(r"\d{4}-\d\d-\d\d-general", f"{DAY}-general", path),
                    )
                    district = re.sub(r"^[a-z-]+:|:g$", "", seat).lower().replace(":", "-")
                    seats[(state, level, district)] = key
                    templates.append(template)
        for state, seat in sorted(VACANCY_2026):
            level = "upper" if seat.startswith(("senate:", "legislature:")) else "lower"
            template, label, path = chambered[(state, level, "2024")][seat]
            key = f"{DAY}:{state}:{seat}"
            legislative[template[0]] = (
                key,
                re.sub(r"^\d{4}", "2026", label.replace(" election", " special election")),
                re.sub(r"\d{4}-\d\d-\d\d-general", f"{DAY}-general", path) + "-special-election",
            )
            seats[(state, level, re.sub(r"^[a-z-]+:|:g$", "", seat).lower().replace(":", "-"))] = key
            templates.append((*template[:8], "remainder", *template[9:]))
        special_seats: dict[tuple[str, str, str], str] = {}
        for (state, seat), held_seat in sorted(SPECIAL_2026.items()):
            level = "upper" if seat.startswith(("senate:", "legislature:")) else "lower"
            template, label, path = next(
                chambered[(state, level, year)][f"{held_seat}:g"]
                for year in ("2025", "2024", "2022", "2020")
                if f"{held_seat}:g" in chambered.get((state, level, year), {})
            )
            key = f"{DAY}:{state}:{seat}:gs"
            legislative[f"{template[0]}|special"] = (
                key,
                re.sub(r"^\d{4}", "2026", label.replace(" election", " special election")),
                re.sub(r"\d{4}-\d\d-\d\d-general", f"{DAY}-general", path) + "-special-election",
            )
            special_seats[(state, level, seat.split(":", 1)[1].replace(":", "-"))] = key
            templates.append((f"{template[0]}|special", *template[1:7], 1, "remainder", *template[9:]))
        latest_plan = conn.execute(
            "select id from boundary_set where scheme_code = 'us.cd' and vintage = '119'"
        ).fetchone()[0]
        new_plan = conn.execute("select id from boundary_set where scheme_code = 'us.cd' and vintage = '120'").fetchone()[0]
        sitting: dict[str, set[UUID]] = {"house": set(), "senate": set()}
        members: dict[tuple[str, str], list[list[str]]] = {}
        states = {fips: state for state, fips in FIPS.items()}
        for chamber, person, held_name, fips in conn.execute(
            """select case when n.text = 'United States Senate' then 'senate' else 'house' end, t.person_id, pn.text,
                      (select left(ac.code, 2) from area_code ac where ac.area_id = s.apportioned_area_id limit 1)
               from tenure t join name n on n.entity_id = t.body_id and n.is_preferred
               join seat s on s.id = t.seat_id
               join name pn on pn.entity_id = t.person_id and pn.is_preferred and pn.lang = 'en'
               where n.text in ('United States House of Representatives', 'United States Senate')
                 and t.from_date <= %(day)s and (t.to_date is null or t.to_date >= %(day)s)""",
            {"day": DAY},
        ):
            sitting[chamber].add(person)
            members.setdefault((chamber, states.get(fips, "")), []).append(
                [token for token in slug(re.sub(r'"[^"]*"', " ", held_name)).split("-") if not SUFFIX.fullmatch(token)]
            )
        state_names = dict(
            conn.execute(
                """select right(p.iso_code, 2), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )

        chambers = "|".join(["governor", "senate", r"house:[0-9]+", *sorted({key.split(":")[1] for key in offices})])
        nominees: dict[str, dict[tuple[str, UUID], UUID | None]] = {}
        nominated: dict[tuple[str, UUID], UUID] = {}
        for value, ballot, person, party_id, certified in conn.execute(
            """select x.value, op.ballot_label, cy.person_id, op.party_id,
                   (select s.id from source s where s.id = rs.source_id and s.kind = 'official_body')
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
            if certified is not None:
                nominated[(general, person)] = certified

        for value, ballot, person, party_id, certified in conn.execute(
            """select x.value, op.ballot_label, cy.person_id, op.party_id,
                   (select s.id from source s where s.id = rs.source_id and s.kind = 'official_body')
               from external_id x
               join contest co on co.id = x.entity_id and co.body_id is not null
               join name b on b.entity_id = co.body_id and b.is_preferred and b.text !~ 'United States'
               join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
               join outcome o on o.result_set_id = rs.id and o.kind = 'won'
               join contest_option op on op.id = o.option_id
               join candidacy cy on cy.option_id = op.id and cy.role = 'head'
               where x.scheme = 'us.contest'
                 and x.value ~ '^2026-[0-9-]+:[A-Z]{2}:(house|senate|assembly|house-of-delegates|legislature):.+:(democratic|republican|minnesota-democratic-farmer-labor|dp|rp|liberp)$'"""
        ):
            _, state, seat = value.rsplit(":", 1)[0].split(":", 2)
            general = f"{DAY}:{state}:{seat}:g"
            if general in {entry[0] for entry in legislative.values()}:
                known = nominees.setdefault(general, {})
                known[(ballot, person)] = known.get((ballot, person)) or party_id
                if certified is not None:
                    nominated[(general, person)] = certified

        numbered: dict[tuple[str, str, str], list[str]] = {}
        special_numbered: dict[tuple[str, str, str], list[str]] = {}
        for pool, grouped in ((seats, numbered), (special_seats, special_numbered)):
            for state, level, seat in pool:
                if re.fullmatch(r"\d+-\w+", seat):
                    grouped.setdefault((state, level, seat.split("-")[0]), []).append(seat)
        listed: dict[str, list[dict[str, str | int | None]]] = {}
        for row in pl.read_parquet(PARSED / "candidates.parquet").iter_rows(named=True):
            split = next(
                (
                    f"{DAY}:{key}" + (f":{seats[row['office']][0]}" if seats[row["office"]][0] else "")
                    for key, seats in SPLIT.items()
                    if key.startswith(f"{row['state']}:") and row["office"] in seats
                ),
                None,
            )
            if split is not None:
                listed.setdefault(split, []).append(row)
                continue
            office = re.sub(r"-(statewide|state-of-[a-z]+)$", "", slug(row["office"]))
            office = re.sub(r"^(state|general)-", "", office)
            if office.startswith(("auditor", "treasurer", "comptroller")):
                office = re.sub(r"-of-(state|accounts|public-accounts)$", "", office)
            office = OFFICE_ALIASES.get(office, office)
            if row["office"] in ("house", "senate", "governor") or f"{row['state']}:{office}" in offices:
                key = f"{DAY}:{row['state']}:{row['office'] if row['office'] in ('house', 'senate', 'governor') else office}"
                listed.setdefault(key + (f":{row['district']}" if row["office"] == "house" else ""), []).append(row)
                continue
            text = str(row["office"]).lower()
            if re.search(
                r"u\.s\.|united states|congress|county|city|town|school|(?<!general )court|board|ward|council|precinct|committee|meeting",
                text,
            ):
                continue
            special = re.search(r"vacancy|special|unexpired", text) is not None
            level = (
                "upper"
                if "senat" in text
                else "lower"
                if re.search(r"represent|assembly|house|delegates|legislat", text)
                else None
            )
            number = re.search(
                r"(\d+)(?:st|nd|rd|th)?\s*(?:,?\s*(?:seat|position)\s+)?([a-z]|\d+)?\b", re.sub(r"\d+-year", "", text)
            )
            if level and not number and row["district"] is not None:
                number = re.match(r"(\d+)()", str(row["district"]))
            if not level:
                continue
            candidates = []
            if number:
                district = str(int(number[1])) + (f"-{number[2]}" if number[2] else "")
                prefixed = (special_numbered if special else numbered).get(
                    (row["state"], level, str(int(number[1]))), []
                )
                candidates = [
                    district,
                    re.sub(r"-([a-z])$", lambda match: f"-{ord(match[1]) - 96}", district),
                    str(int(number[1])),
                    *(prefixed if len(prefixed) == 1 else []),
                ]
            if "," in text:
                words = re.findall(r"[a-z]+(?:-[a-z]+)?|\d+", text.split(",", 1)[1])
                ordinal = next(
                    (str(ORDINAL_WORDS.index(word) + 1) for word in words if word in ORDINAL_WORDS),
                    next((word for word in words if word.isdigit()), str(row["district"] or "")),
                )
                joined = "".join(
                    word.replace("-", "")
                    for word in words
                    if word not in ORDINAL_WORDS and not word.isdigit() and word not in ("district", "and", "the", "of")
                )
                if joined:
                    candidates += [f"{joined}-{ordinal}" if ordinal else joined, joined]
            key = next(
                (
                    pool[row["state"], level, seat]
                    for pool in ((special_seats,) if special else (seats, special_seats))
                    for seat in candidates
                    if (row["state"], level, seat) in pool
                ),
                None,
            )
            if key:
                listed.setdefault(key, []).append(row)

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
               where x.scheme = 'us.contest' and x.value ~ ('^20(1[8-9]|2[0-6])-[0-9-]+:[A-Z]{2}:(' || %s || ')(:|$)')
                 and x.value !~ ':g(:|$)'""",
            [chambers],
        ).fetchall():
            day, state, chamber, *rest = value.split(":")
            latest[person] = max(latest.get(person, ""), day)
            by_state.setdefault(state, []).append((person, text))
            (known if day.startswith("2026") else earlier).setdefault(
                f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else ""), []
            ).append((person, text))
        for value, person, text in conn.execute(
            """select x.value, cy.person_id, n.text
               from external_id x
               join contest_option op on op.contest_id = x.entity_id
               join candidacy cy on cy.option_id = op.id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value = any(%s)""",
            [[held.removesuffix("|special") for held in legislative]],
        ).fetchall():
            latest[person] = max(latest.get(person, ""), value[:10])
            by_state.setdefault(value.split(":")[1], []).append((person, text))
            for held in (value, f"{value}|special"):
                if held in legislative:
                    earlier.setdefault(legislative[held][0], []).append((person, text))
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
               where x.scheme = 'us.contest' and x.value like %s
                 and not exists (select from entity_retirement r where r.entity_id = op.id)""",
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
                    and surname(held_text).replace("-", "")
                    in {
                        "".join(bare[index:])
                        for bare in [
                            [
                                token
                                for token in slug(re.sub(r"[\"“][^\"”]*[\"”]|\([^)]*\)", " ", text)).split("-")
                                if not SUFFIX.match(token)
                            ]
                        ]
                        for index in range(1, len(bare))
                    }
                    and len(slug(held_text).split("-")[0]) > 1
                    and slug(held_text).split("-")[0] in tokens
                    and (
                        slug(held_text).split("-")[0] == tokens[0]
                        or person in {earlier_person for earlier_person, _ in earlier.get(key, [])}
                    )
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
        counts = {
            "house": 0,
            "senate": 0,
            "governor": 0,
            "statewide": 0,
            "legislative": 0,
            "candidates": 0,
            "listed": 0,
            "dropped": 0,
        }
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
            if value in legislative:
                chamber = "legislative"
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
            key = legislative[value][0] if chamber == "legislative" else ":".join([str(DAY), state, chamber, *rest])
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
                boundary_set_id=plan
                if chamber == "legislative"
                else new_plan
                if chamber == "house" and state in REDRAWN_LOADED
                else latest_plan
                if chamber == "house" and state not in REDISTRICTED
                else None,
                magnitude=magnitude,
                term="remainder" if special else term,
                round_number=1,
                electoral_system_id=systems[SYSTEM_OVERRIDES.get(f"{state}:{chamber}", SYSTEM_OVERRIDES[state])]
                if state in SYSTEM_OVERRIDES and chamber in ("house", "senate")
                else cs.key("us.electoral_system", "block")
                if chamber != "legislative" and (magnitude or 1) > 1
                else system,
                default_result_kind="tacit" if key.split(":", 2)[2] in TACIT.get(state, {}) else result_kind,
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
            elif chamber == "legislative":
                _, title, path = legislative[value]
            elif chamber == "governor":
                title = f"2026 {state_name} gubernatorial election"
                path = f"us/{state.lower()}/{DAY}-general/governor"
            else:
                title = re.sub(r"^\d{4} ", "2026 ", offices[f"{state}:{chamber}"])
                path = f"us/{state.lower()}/{DAY}-general/{chamber}"
                if f"{state}:{chamber}" in SPLIT:
                    label = next(
                        text for seat, _, text in SPLIT[f"{state}:{chamber}"].values() if seat == ":".join(rest)
                    )
                    title = title.replace(" election", f" election ({label})")
                    path = "-".join([path, *rest])
            name(contest, title)
            cs.add("slug", path=path, entity_id=contest)
            if chamber == "house" and state == "LA":
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md="Louisiana Act 7 of 2026 cancelled the closed party primaries for the U.S. House and moved the races to the open primary on 2026-11-03: every candidate runs on one ballot, a majority wins outright, and otherwise the top two meet in a general election on 2026-12-12.",
                )
            if chamber == "house" and state in REDISTRICTED and state not in REDRAWN_LOADED:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md=f"{state_name} redrew its congressional districts for 2026. The contest is recorded by district number; the new plan's boundaries are not loaded yet.",
                )
            staged: set[UUID] = set()
            entries: list[tuple[str, UUID, UUID | None]] = []
            listing = None
            if state in OFFICIAL and key in listed:
                listing = (
                    f"special:{state}" if listed[key][0]["url"] in SPECIAL_LISTS.get(state, ("", "", []))[2] else state
                )
                publisher, title, urls = SPECIAL_LISTS[state] if listing != state else OFFICIAL[state]
                if listing not in sources:
                    sources[listing] = cs.source(publisher, title)
                    if sources[listing] in cs.minted:
                        cs.add(
                            "source",
                            id=sources[listing],
                            kind="official_body",
                            publisher=publisher,
                            title=title,
                            url=urls[0],
                            license=None,
                        )
                    for url in urls:
                        cs.artifact(raw.cached(SOURCE, url), sources[listing])
                if all(row["name"] == "No Candidate Qualified" for row in listed[key]):
                    cs.add(
                        "label",
                        entity_id=contest,
                        kind="ballot",
                        value="no_candidate_qualified",
                        source_id=sources[listing],
                        valid_from=DAY,
                    )
                for row in sorted(listed[key], key=lambda row: str(row["name"])):
                    text, label = str(row["name"]), str(row["party"])
                    if text == "No Candidate Qualified":
                        continue
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
            tacit = TACIT.get(state, {}).get(key.split(":", 2)[2])
            if tacit is not None:
                entries.append((tacit[0], person_of(state, key, chamber, None, tacit[0]), cs.key("us.party", tacit[1])))
            elif state in SUPPLEMENTED or key not in listed:
                entries += [
                    (ballot, person, party_id)
                    for (ballot, person), party_id in sorted(
                        nominees.get(key, {}).items(), key=lambda nominee: (nominee[0][0], str(nominee[0][1]))
                    )
                ]
            for ballot, person, party_id in entries:
                person = next(
                    (
                        held_person
                        for held_person, (held_option, _) in held.get(key, {}).items()
                        if held_option not in cs.retired
                        and option_keys.get(held_option, "").removesuffix(":2026-10-27") == f"{key}:{slug(ballot)}"
                    ),
                    person,
                )
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
                    is_incumbent=(
                        person in sitting[key.split(":")[2]]
                        or any(
                            member[-1] == tokens[-1] and member[0][:3] == tokens[0][:3]
                            for member in members.get((key.split(":")[2], state), [])
                            if (
                                tokens := [
                                    token
                                    for token in slug(re.sub(r'"[^"]*"', " ", ballot)).split("-")
                                    if not SUFFIX.fullmatch(token)
                                ]
                            )
                        )
                    )
                    if re.fullmatch(r"[\d-]+:[A-Z]{2}:(house:\d+|senate(:special)?)", key)
                    else None,
                )
                if listing is not None:
                    cs.add("citation", entity_id=candidacy, source_id=sources[listing], locator=None)
                elif (key, person) in nominated:
                    cs.add("citation", entity_id=candidacy, source_id=nominated[(key, person)], locator=None)
                counts["candidates"] += 1
                if tacit is not None:
                    publisher, title, addresses, context = TACIT_SOURCES[tacit[2]]
                    if f"tacit:{tacit[2]}" not in sources:
                        sources[f"tacit:{tacit[2]}"] = cs.source(publisher, title)
                        if sources[f"tacit:{tacit[2]}"] in cs.minted:
                            cs.add(
                                "source",
                                id=sources[f"tacit:{tacit[2]}"],
                                kind="official_body",
                                publisher=publisher,
                                title=title,
                                url=addresses[0],
                                license="Public record",
                            )
                        for address in addresses:
                            cs.artifact(raw.cached(SOURCE, address), sources[f"tacit:{tacit[2]}"])
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:tacit")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=elections[state],
                        contest_id=contest,
                        stage="certified",
                        kind="tacit",
                        status="active",
                        source_id=sources[f"tacit:{tacit[2]}"],
                        is_default=True,
                        completeness=None,
                    )
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                    cs.add("note", entity_id=result_set, kind="context", body_md=context)
                    cs.add("citation", entity_id=candidacy, source_id=sources[f"tacit:{tacit[2]}"], locator=None)
            reason = (
                f"Not on the certified {state_name} 2026 general election candidate list"
                if listing is not None
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
            if state in SUPPLEMENTED and listing is not None and not key.endswith(":gs"):
                body = f"Candidates are the {state_name} nominees recorded in the archive's own 2026 primary result sets, the unopposed primary candidates, and the independent candidates qualified for the general election ballot on the {publisher}'s {title}. Declared write-in candidates are not listed."
            elif listing is not None:
                body = f"Candidates are those on the {publisher}'s {title}, as certified for the 2026-11-03 ballot. Declared write-in candidates are not listed."
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
            if value not in legislative
        } | {entry[0] for entry in legislative.values()}
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
        written = {str(row["id"]) for row in cs.rows.get("contest", [])}
        for entity, value in conn.execute(
            """select co.id, x.value from contest co join changeset c on c.id = co.changeset_id
               join entity e on e.id = co.id and e.retired_by is null
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               where c.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if str(entity) not in written:
                print("retiring", value)
                cs.add(
                    "entity_retirement",
                    entity_id=entity,
                    reason="The contest is no longer on the ballot as scheduled; its seats are now held as other contests",
                )
        print(counts)
        return cs.write()
