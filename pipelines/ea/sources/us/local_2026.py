import csv
import html
import io
import json
import re
import subprocess
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path

import httpx
import polars as pl

from ea import coverage, raw
from ea.changeset import Changeset, slug
from ea.sources.us import mn_local
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect

SOURCE = "us.local_2026"
VERSION = "1"
DAY = date(2026, 11, 3)
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "06037": "https://content.lavote.gov/docs/rrcc/documents/final-list-of-qualified-candidates-to-appear-on-the-ballot9fcf3b64-1a33-4882-9e17-8cbc3b3c8d76.pdf",
    "0644000": "https://cityclerk.lacity.org/election/2026_General_Municipal_Election_List_of_Certified_Candidates.pdf",
    "06073": "https://www.sdvote.com/content/dam/rov/en/election/nov-3-2026-gubernatorial-general-election/candidate-list.pdf",
    "06059": "https://ocvote.gov/fileadmin/user_upload/data/candlog/GEN2026/candlog.pdf",
    "53033": "https://cdn.kingcounty.gov/-/media/king-county/depts/elections/how-to-vote/voters-pamphlets/2026/11/local-edition.pdf",
    "17031": "https://cboeprod.blob.core.usgovcloudapi.net/prod/2026-09/Candidate%20List_20260911-1.pdf",
    "48201": "https://hctxdnnstg.blob.core.windows.net/harrisvotes/prd/SampleBallot/2026/11-3/1126_Yellow%20Sample%20Ballot%20ENG-SPA%20for%20Website%20Posting.pdf",
    "48113": "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/dallas-tx.connect.clarityelections.com/SampleBallots/2026/261103GeneralJoint/1.pdf",
    "12086": "https://www.miamidade.gov/elections/library/2026-11-03-general-election-master-ballot.pdf",
    "3651000": "https://www.vote.nyc/sites/default/files/pdf/candidates/2026/9_11_2026%2010_26_11%20AM_General%20Contest%20List%20Web%20Version.pdf",
    "04013": "https://elections.maricopa.gov/asset/jcr:1673a1e7-bbed-4d76-94fe-ae0ccc1ce9d5/2026%20General%20Election%20Maricopa%20County%20Candidates%20On%20Ballot%20-%20Remediated.pdf",
    "17031c": "https://www.cookcountyclerkil.gov/elections/current-elections/view-all-candidates#GetAllCandidates.json",
}
FEEDS = {
    "NC": (
        "https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2026/Candidate%20Filing/Candidate_Listing_2026.csv",
        "North Carolina State Board of Elections",
        "2026 Candidate Listing",
    ),
    "MD": (
        "https://elections.maryland.gov/elections/2026/general_candidates/2026_GG_all_counties_candidatelist.csv",
        "Maryland State Board of Elections",
        "2026 Gubernatorial General Election: Candidate List, All Counties",
    ),
    "VA": (
        "https://www.elections.virginia.gov/media/castyourballot/candidatelist/2026/2026-November-Local-Offices-Candidate-List-(rev-10-9-2026).xlsx",
        "Virginia Department of Elections",
        "November 3, 2026 General Election: Local Offices Candidate List (rev. 10-9-2026)",
    ),
    "TX": (
        "https://www.sos.texas.gov/elections/forms/2026-ballot-cert.pdf",
        "Texas Secretary of State",
        "Ballot Certification Report, 2026 November General Election",
    ),
    "CA-06037": (
        "https://content.lavote.gov/docs/rrcc/documents/final-list-of-qualified-candidates-to-appear-on-the-ballot9fcf3b64-1a33-4882-9e17-8cbc3b3c8d76.pdf",
        "Los Angeles County Registrar-Recorder/County Clerk",
        "General Election, November 3, 2026: Final List of Qualified Candidates to Appear on the Ballot",
    ),
    "SC": (
        "https://vrems.scvotes.sc.gov/Candidate/CandidateSearch?electionId=22596#ExportSearchResults",
        "South Carolina Election Commission",
        "Candidate Listing: 2026 Statewide General Election, November 3, 2026",
    ),
    "NM": (
        "https://candidateportal.servis.sos.state.nm.us/CandidateList.aspx#export",
        "New Mexico Secretary of State",
        "Candidate List: 2026 General Election",
    ),
    "IN": (
        "https://www.in.gov/sos/elections/files/Candidate_List_Abbreviated_20260922_011251PM.xlsx",
        "Indiana Election Division",
        "Candidate List, Abbreviated: 2026 General Election, All Counties",
    ),
    "CA-06073": (
        "https://www.sdvote.com/content/dam/rov/en/election/nov-3-2026-gubernatorial-general-election/candidate-list.pdf",
        "County of San Diego Registrar of Voters",
        "November 3, 2026 General Election: Candidate List",
    ),
    "CA-06059": (
        "https://ocvote.gov/fileadmin/user_upload/data/candlog/GEN2026/candlog.pdf",
        "Orange County Registrar of Voters",
        "General Election, November 3, 2026: Contest/Candidate Proof List",
    ),
    "CA-06065": (
        "https://voteinfo.net/sites/g/files/aldnop371/files/2026-09/Final%20candidate%20list%20nov%202026%20eng.pdf",
        "Riverside County Registrar of Voters",
        "November 3, 2026 General Election: Final Candidate List (Contest/Candidate Proof List)",
    ),
    "CA-06071": (
        "https://uploads.rov.sbcounty.gov/ROV/Elections/2026/1103/Report_CandidateList.pdf",
        "San Bernardino County Registrar of Voters",
        "November 3, 2026 General Election: Candidate List",
    ),
    "MI-26125": (
        "https://cdn.oaklandcountymi.gov/oakland-sitefinity-prod/docs/default-source/clerk/clerk-elections/elections-voting/11032026-candidate-list.pdf",
        "Oakland County Clerk",
        "Official Candidate List, November 3, 2026 General Election",
    ),
    "MI-26017": (
        "https://www.baycountymi.gov/Documents/Departments/Clerk/Elections/Election%20Notices/November%203,%202026%20Official%20Candidates%20&amp;%20Proposals.pdf",
        "Bay County Clerk",
        "Official Candidate List, 11/03/2026 State General",
    ),
    "MI-26049": (
        "http://www.geneseecountymi.gov/2026%20Candidate%20List.pdf?t=202610221428180",
        "Genesee County Clerk",
        "November 3rd, 2026 Candidate List",
    ),
    "MI-26091": (
        "https://www.electionlenawee.com/app/download/14987887430/Official+Candidate+Listing+November+3%2C+2026+General+Election.pdf",
        "Lenawee County Clerk",
        "Official Candidate Listing, November 3, 2026 General Election",
    ),
    "MI-26163": (
        "https://www.waynecountymi.gov/files/assets/mainsite/v/1/clerk/documents/elections/unofficial_candidate_list_11032026828.pdf",
        "Wayne County Clerk",
        "November 3, 2026 General Election: Unofficial Candidate List",
    ),
    "MI-26099": (
        "https://macombgovdocs.org/clerk/pdfs/elect-2026-11-03-Official-Candidate-List-printable.pdf",
        "Macomb County Clerk",
        "Official Candidate List for November 2026 Election",
    ),
    "MI-26147": (
        "https://stclaircountyclerk.org/Public/File/november_2026_candidate_list_pdf",
        "St. Clair County Clerk",
        "Unofficial Candidate List for November 2026",
    ),
    "MI-26055": (
        "https://www.gtcountymi.gov/DocumentCenter/View/25783/General-Election-2026-Candidates",
        "Grand Traverse County Clerk",
        "November 3, 2026 General Election Candidate and Proposal List",
    ),
    "MI-26065": (
        "https://www.dropbox.com/scl/fi/5b4627tqe8txa3n6x8jxs/Ingham-November-Candidate-List-2026.pdf",
        "Ingham County Clerk",
        "Ingham County Candidate List, November 3, 2026 General Election",
    ),
    "MI-26093": (
        "https://milivcounty.gov/wp-content/uploads/Candidate-Filings-November-2026.pdf",
        "Livingston County Clerk",
        "Official List: Candidate Filings for November 3, 2026 General Election",
    ),
    "MI-26161": (
        "https://content.civicplus.com/api/assets/mi-washtenawcounty/27e0db62-cc1a-41cd-8654-e0a8a5c4f6d0",
        "Washtenaw County Clerk",
        "Official List of Candidates, November 3, 2026 Election",
    ),
    "MI-26139": (
        "https://app.miottawa.org/ElectionManagement/viewPublicFile.action?viewFile=%2Fmnt%2Fottawa-apps%2Fcontent%2FappImages%2FElectionManagement%2FcandidateFile-226.pdf",
        "Ottawa County Clerk",
        "Unofficial Candidate Listing, 2026 Election Cycle",
    ),
    "MI-26045": (
        "https://www.eatoncounty.org/DocumentCenter/View/10690",
        "Eaton County Clerk",
        "City Candidate Listing, November 3, 2026 Election",
    ),
    "MI-26045-s": (
        "https://www.eatoncounty.org/DocumentCenter/View/10652",
        "Eaton County Clerk",
        "School Board Candidate Listing, November 3, 2026 Election",
    ),
    "MI-26045-t": (
        "https://www.eatoncounty.org/DocumentCenter/View/10686",
        "Eaton County Clerk",
        "Township Candidate Listing, November 3, 2026 Election",
    ),
    "MI-26075": (
        "https://www.co.jackson.mi.us/DocumentCenter/View/23703/Jackson-County-School-Boards-Unofficial-Candidate-List-Nov-3-2026",
        "Jackson County Clerk",
        "Jackson County School Board Member Unofficial Candidate List, November 3, 2026",
    ),
    "MI-26075-v": (
        "https://www.co.jackson.mi.us/DocumentCenter/View/23704/Jackson-County-Village-Positions-Unofficial-Candidate-List-November-3-2026",
        "Jackson County Clerk",
        "Jackson County Unofficial Candidate List: Village Offices, November 3, 2026",
    ),
    "MI-26077": (
        "https://www.kalcounty.gov/DocumentCenter/View/6469/Unofficial-November-2026-Candidate-List?bidId=",
        "Kalamazoo County Clerk",
        "Official November 2026 Candidate List (updated 10/26/2026)",
    ),
    "MI-26021": (
        "https://www.berriencounty.org/DocumentCenter/View/24628",
        "Berrien County Clerk",
        "Unofficial Candidate List, 11/03/2026 State General (as of 9/3/2026)",
    ),
    "MI-26005": (
        "https://www.allegancounty.org/departments/elections",
        "Allegan County Clerk",
        "Candidate List, November 3, 2026 General Election",
    ),
    "OH-39017": (
        "https://cms2.revize.com/revize/repository/butlercountyboe/2026/November/PetitionActivityReport.pdf?874=",
        "Butler County Board of Elections",
        "Candidate & Petition Activity, November 3, 2026 General Election",
    ),
    "OH-39035": (
        "https://boe.cuyahogacounty.gov/docs/default-source/boe/candidates-page/candidate-list.pdf?sfvrsn=4b1792c0_419",
        "Cuyahoga County Board of Elections",
        "November 3, 2026 General Election Candidate List",
    ),
    "OH-39049": (
        "https://www.vote.franklincountyohio.gov/getmedia/e0a4ca45-6893-45c1-8190-87696f8804b4/2026-General-Candidate-List-5",
        "Franklin County Board of Elections",
        "2026 General Election Candidate List",
    ),
    "OH-39061": (
        "https://votehamiltoncountyohio.gov/wp-content/uploads/2026/09/November-2026-All-Certified-Candidates-Profile-1-1.pdf",
        "Hamilton County Board of Elections",
        "November 2026 General Election Candidate List (certified)",
    ),
    "OH-39085": (
        "https://wpassets.lakecountyohio.gov/wp-content/uploads/sites/12/2026/10/15161638/2026-Candidate-Filings.pdf",
        "Lake County Board of Elections",
        "2026 Candidate Filings",
    ),
    "OH-39093": (
        "https://www.voteloraincountyohio.gov/_files/ugd/2568d0_ef2bd7f0adc244659d5357bfa1879d07.pdf",
        "Lorain County Board of Elections",
        "November 3, 2026 General Election Candidates",
    ),
    "OH-39099": (
        "https://vote.mahoningcountyoh.gov/DocumentCenter/View/2640/Filings-for-November-3-2026-General-Election?bidId=",
        "Mahoning County Board of Elections",
        "Filings for November 3, 2026 General Election",
    ),
    "OH-39151": (
        "https://starkcountyohio.gov/Board%20of%20Elections/Candidates%20List%20General.pdf",
        "Stark County Board of Elections",
        "Candidates List, General Election",
    ),
    "FL-12011": (
        "https://browardvotes.gov/sites/default/files/list-of-candidates-for-november-elections.pdf",
        "Broward County Supervisor of Elections",
        "List of Candidates and Contests, November 3, 2026 General Election",
    ),
    "FL-12101": (
        "https://www.pascovotes.gov/Documents/Candidates/List%20Of%202026%20Candidates/Tot2026.pdf",
        "Pasco County Supervisor of Elections",
        "2026 Local Candidates, Pasco County (9/16/2026)",
    ),
    "FL-12081": (
        "https://www.votemanatee.gov/download/64/2026-general-election/9497/2026-generic-general-sample-ballot",
        "Manatee County Supervisor of Elections",
        "Manatee County Sample Ballot, General Election, November 3, 2026 (all voters)",
    ),
    "CA-06099": (
        "https://www.stanvote.gov/pdf/candidate-list.pdf",
        "Stanislaus County Registrar of Voters",
        "General Election, November 3, 2026: Unofficial Candidate List",
    ),
    "CA-06079": (
        "https://www.slocounty.ca.gov/departments/clerk-recorder/forms-documents/elections-and-voting/current-elections/2026-11-03/unofficial-candidates-list",
        "County of San Luis Obispo Elections Division",
        "Certified Candidate List for the November 3, 2026 General Election",
    ),
    "NJ-34029": (
        "https://www.co.ocean.nj.us/WebContentFiles/a6ad1195-1bbf-442f-bbd0-b4398929f0ef.pdf",
        "Ocean County Clerk",
        "2026 General Election Official Candidate Certification Sheet",
    ),
    "NJ-34001": (
        "https://www.atlanticcountyclerk.org/wp-content/uploads/2026/08/Candidates-List-2026-General-8-12-26-1.pdf",
        "Atlantic County Clerk",
        "2026 General Election: Official Candidates List",
    ),
    "NJ-34005": (
        "https://www.burlingtoncountynj.gov/DocumentCenter/View/25709/General-Election-Candidate-List---Partisan-Offices-PDF",
        "Burlington County Clerk",
        "Burlington County 2026 General Election Candidates List (8/31/2026)",
    ),
    "NJ-34015": (
        "https://www.gloucestercountynj.gov/DocumentCenter/View/19422/OFFICIAL-CANDIDATE-LIST-GENERAL-ELECTION-2026?bidId=",
        "Gloucester County Clerk",
        "2026 General Election Candidate List (revision 08.27.26)",
    ),
    "NJ-34009": (
        "https://www.capemaycountyvotes.com/wp-content/uploads/2026/08/2026-General-Candidates-WEB.pdf",
        "Cape May County Clerk",
        "2026 Official General Election Candidates, November 3, 2026",
    ),
    "NJ-34011": (
        "https://cumberlandcountyclerknj.gov/wp-content/uploads/2026/07/CANDIDATE-LIST-WEBSITE.xlsx",
        "Cumberland County Clerk",
        "Candidates for 2026 General Election",
    ),
    "NJ-34019": (
        "https://www.co.hunterdon.nj.us/DocumentCenter/View/20397/2026-GENERAL-DEMS-PDF",
        "Hunterdon County Clerk",
        "2026 General: Democratic Candidates",
    ),
    "NJ-34019-r": (
        "https://www.co.hunterdon.nj.us/DocumentCenter/View/20499",
        "Hunterdon County Clerk",
        "2026 General: Republican Candidates",
    ),
    "NJ-34019-i": (
        "https://www.co.hunterdon.nj.us/DocumentCenter/View/20398/G26-Independents-PDF",
        "Hunterdon County Clerk",
        "2026 General: Independent Candidates",
    ),
    "NJ-34025": (
        "https://www.monmouthcountyvotes.gov/wp-content/uploads/2026/09/2026-11-03-General-Election-Candidates-Municipal5.pdf",
        "Monmouth County Clerk",
        "General Election, November 3, 2026: Official List, Municipal Candidates (9/1/2026)",
    ),
    "NJ-34025-c": (
        "https://www.monmouthcountyvotes.gov/wp-content/uploads/2026/09/2026-11-03-General-Election-Candidates-Top-of-Ticket3.pdf",
        "Monmouth County Clerk",
        "General Election, November 3, 2026: Official List, Federal and County Candidates (9/1/2026)",
    ),
    "NJ-34027": (
        "https://www.morriscountyclerk.org/files/sharedassets/clerk/v/12/elections/past-results/2026-general-candidates.pdf",
        "Morris County Clerk",
        "General Election Candidates, November 3, 2026",
    ),
    "NJ-34033": (
        "https://www.salemcountynj.gov/wp-content/uploads/2026/07/2026-Candidate-List-1.pdf",
        "Salem County Clerk",
        "2026 General Election Candidates",
    ),
    "NJ-34037": (
        "https://www.sussexcountyclerk.org/DocumentCenter/View/4636/2026-Nov-3-GE---Official-Candidate-List---Sussex-WEB",
        "Sussex County Clerk",
        "Official Candidate List: Nov 3, 2026 General Election (updated 9/4/2026)",
    ),
    "NJ-34039": (
        "https://unioncountyvotes.com/wp-content/uploads/2026/09/GENERAL-2026-CANDIDATE-LIST-9-3.pdf",
        "Union County Clerk",
        "Candidate List for the General Election, November 3, 2026 (9/3/2026)",
    ),
    "MN": (
        "https://electionresultsfiles.sos.mn.gov/20261103/local.txt",
        "Minnesota Secretary of State",
        "Election results files, November 3, 2026: city and township races",
    ),
    "MN-county": (
        "https://electionresultsfiles.sos.mn.gov/20261103/cntyRaceQuestions.txt",
        "Minnesota Secretary of State",
        "Election results files, November 3, 2026: county races",
    ),
    "MN-school": (
        "https://electionresultsfiles.sos.mn.gov/20261103/SDRaceQuestions.txt",
        "Minnesota Secretary of State",
        "Election results files, November 3, 2026: school district races",
    ),
}
LOCAL_BODY = re.compile(
    r"(?i)school|\btown\b|\bcity\b|village|township|education|trustee|soil|water|hospital|fire|library|sanitary"
    r"|conservation|cemetery|\bparks?\b|airport|\bport\b|utility|levee|drainage|college|recreation"
    r"|public service|house of delegates|state senat|congress"
)
IN_SKIP = {
    "US REPRESENTATIVE",
    "STATE REPRESENTATIVE",
    "STATE SENATOR",
    "SECRETARY OF STATE",
    "STATE COMPTROLLER (AUDITOR OF STATE)",
    "TREASURER OF STATE",
    "OTHER",
}
PRIMARIES = {"NC": "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2026_03_03/results_pct_20260303.zip"}
SYNONYMS = {
    "mayoral": "mayor",
    "commissioners": "commissioner",
    "comm": "commissioner",
    "supervisors": "supervisor",
    "trustees": "trustee",
    "members": "member",
    "councilmember": "council",
    "aldermen": "alderman",
    "bd": "board",
    "edu": "education",
    "ed": "education",
    "twp": "township",
    "co": "county",
    "area": "district",
    "ward": "district",
    "division": "district",
    "zone": "district",
    "precinct": "district",
    "dist": "district",
    "position": "seat",
    "pos": "seat",
}
FILLER = {
    "local",
    "ceda",
    "municipal",
    "no",
    "num",
    "election",
    "general",
    "nonpartisan",
    "partisan",
    "unexpired",
    "term",
    "the",
    "of",
    "for",
    "and",
    "member",
    "seat",
}
ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
DELIVERED = {
    "04013": DATA
    / "inbox"
    / "manual"
    / "MF-153"
    / "2026 General Election Maricopa County Candidates On Ballot - Remediated.pdf",
    "17031c": DATA / "inbox" / "manual" / "MF-154" / "GetAllCandidates.json",
    "SC": DATA / "inbox" / "y26" / "sc_candidates_2026-11-03_all.csv",
    "NM": DATA / "inbox" / "y26" / "nm_candidates_2026_all.csv",
    "NJ-34009": DATA / "inbox" / "y26" / "nj" / "cape_may.pdf",
    "NJ-34011": DATA / "inbox" / "y26" / "nj" / "cumberland.xlsx",
    "NJ-34019-r": DATA / "inbox" / "y26" / "nj" / "hunterdon_rep.pdf",
    "NJ-34019-i": DATA / "inbox" / "y26" / "nj" / "hunterdon_independent.pdf",
    "NJ-34025": DATA / "inbox" / "y26" / "nj" / "monmouth_municipal.pdf",
    "NJ-34025-c": DATA / "inbox" / "y26" / "nj" / "monmouth.pdf",
    "NJ-34027": DATA / "inbox" / "y26" / "nj" / "morris.pdf",
    "MI-26017": DATA / "inbox" / "y26" / "mi" / "bay.pdf",
    "MI-26049": DATA / "inbox" / "y26" / "mi" / "genesee.pdf",
    "MI-26091": DATA / "inbox" / "y26" / "mi" / "lenawee.pdf",
    "MI-26163": DATA / "inbox" / "y26" / "mi" / "wayne.pdf",
    "MI-26099": DATA / "inbox" / "y26" / "mi" / "macomb.pdf",
    "MI-26147": DATA / "inbox" / "y26" / "mi" / "stclair.pdf",
    "MI-26055": DATA / "inbox" / "y26" / "mi" / "grandtraverse.pdf",
    "MI-26065": DATA / "inbox" / "y26" / "mi" / "ingham.pdf",
    "MI-26093": DATA / "inbox" / "y26" / "mi" / "livingston.pdf",
    "MI-26161": DATA / "inbox" / "y26" / "mi" / "washtenaw.pdf",
    "MI-26139": DATA / "inbox" / "y26" / "mi" / "ottawa.pdf",
    "MI-26005": DATA / "inbox" / "y26" / "mi" / "allegan.pdf",
    "FL-12011": DATA / "inbox" / "y26" / "fl" / "broward.pdf",
    "FL-12101": DATA / "inbox" / "y26" / "fl" / "pasco.pdf",
    "FL-12081": DATA / "inbox" / "y26" / "fl" / "manatee.pdf",
    "OH-39017": DATA / "inbox" / "y26" / "oh" / "butler.pdf",
    "OH-39035": DATA / "inbox" / "y26" / "oh" / "cuyahoga.pdf",
    "OH-39049": DATA / "inbox" / "y26" / "oh" / "franklin.pdf",
    "OH-39061": DATA / "inbox" / "y26" / "oh" / "hamilton.pdf",
    "OH-39085": DATA / "inbox" / "y26" / "oh" / "lake.pdf",
    "OH-39093": DATA / "inbox" / "y26" / "oh" / "lorain.pdf",
    "OH-39099": DATA / "inbox" / "y26" / "oh" / "mahoning.pdf",
    "OH-39151": DATA / "inbox" / "y26" / "oh" / "stark.pdf",
    "MI-26077": DATA / "inbox" / "y26" / "mi" / "kalamazoo.pdf",
    "MI-26021": DATA / "inbox" / "y26" / "mi" / "berrien.pdf",
    "MI-26045": DATA / "inbox" / "y26" / "mi" / "eaton_city_village.pdf",
    "MI-26045-s": DATA / "inbox" / "y26" / "mi" / "eaton_school_board.pdf",
    "MI-26045-t": DATA / "inbox" / "y26" / "mi" / "eaton_township.pdf",
    "MI-26075": DATA / "inbox" / "y26" / "mi" / "jackson_school.pdf",
    "MI-26075-v": DATA / "inbox" / "y26" / "mi" / "jackson_village.pdf",
}
PUBLISHERS = {
    "0644000": (
        "CA",
        "Los Angeles City Clerk",
        "2026 General Municipal Election, November 3, 2026: Certified List of Candidates",
    ),
    "17031": (
        "IL",
        "Chicago Board of Election Commissioners",
        "Candidate Filings in Ballot Order, November 3, 2026 (offices whose districts include part of Chicago)",
    ),
    "48201": ("TX", "Harris County Elections", "General and Special Elections, November 3, 2026: Sample Ballot"),
    "48113": ("TX", "Dallas County Elections", "General and Joint Election, November 3, 2026: Official Ballot"),
    "12086": ("FL", "Miami-Dade County Elections Department", "Official General Election Ballot, November 3, 2026"),
    "06073": ("CA", "County of San Diego Registrar of Voters", "November 3, 2026 General Election: Candidate List"),
    "06059": (
        "CA",
        "Orange County Registrar of Voters",
        "General Election, November 3, 2026: Contest/Candidate Proof List",
    ),
    "3651000": (
        "NY",
        "Board of Elections in the City of New York",
        "The Contest List, General Election 2026 (tentative, printed 9/11/2026)",
    ),
    "04013": ("AZ", "Maricopa County Elections Department", "Official County Candidates, 2026 General Election"),
    "17031c": (
        "IL",
        "Cook County Clerk",
        "View All Candidates, November 3, 2026 Gubernatorial General Election",
    ),
}
COUNTIES = {"06073": "San Diego County", "06059": "Orange County"}
OFFICES = {
    "0644000": [
        (r"MAYOR", "CA:0644000:mayor"),
        (r"CITY ATTORNEY", "CA:0644000:city-attorney"),
        (r"CONTROLLER", "CA:0644000:city-controller"),
        (r"MEMBER OF THE CITY COUNCIL, DISTRICT NO\. (\d+)", "CA:0644000:council:{}"),
    ],
    "17031": [
        (r"County Clerk", "IL:17031:clerk"),
        (r"County Sheriff", "IL:17031:sheriff"),
        (r"County Treasurer", "IL:17031:treasurer"),
        (r"County Assessor", "IL:17031:assessor"),
        (r"County Commissioner, (\d+)\w\w District", "IL:17031:board-of-commissioners:district-{}"),
        (r"Board of Review, (\d+)\w\w District", "IL:17031:board-of-review:district-{}"),
        (
            r"Metropolitan Water Reclamation District Commissioners Full 6-Year Term",
            "IL:17031:reclamation:metropolitan-water-reclamation-district-of-greater-chicago:special",
        ),
        (
            r"Metropolitan Water Reclamation District Commissioners Unexpired 2-Year Term",
            "IL:17031:reclamation:metropolitan-water-reclamation-district-of-greater-chicago:special:2-year-term",
        ),
    ],
    "48201": [
        (r"County Judge", "TX:48201:county-judge"),
        (r"County Clerk", "TX:48201:clerk"),
        (r"District Clerk", "TX:48201:district-clerk"),
        (r"County Treasurer", "TX:48201:treasurer"),
        (r"County Attorney - Unexpired Term", "TX:48201:county-attorney"),
        (r"County Commissioner, Precinct (\d+)", "TX:48201:commissioners-court:precinct-{}"),
    ],
    "48113": [
        (r"County Judge", "TX:48113:county-judge"),
        (r"County Clerk", "TX:48113:clerk"),
        (r"District Clerk", "TX:48113:district-clerk"),
        (r"County Treasurer", "TX:48113:treasurer"),
        (r"Criminal District Attorney", "TX:48113:district-attorney"),
    ],
    "12086": [
        (r"County Commissioner, District (\d+)", "FL:12086:commission:district-{}"),
        (r"School Board Member, District (\d+)", "FL:12086:board-of-education:district-{}"),
    ],
    "06073": [(r"BOARD OF SUPERVISORS - DISTRICT NO\. (\d+)", "CA:06073:board-of-supervisors:{}")],
    "06059": [
        (r"Nonpartisan COUNTY OF ORANGE County Supervisor (\d+)\w\w District", "CA:06059:board-of-supervisors:{}")
    ],
    "17031c": [
        (r"(6|14|15|17)\w\w County Board District Commissioner", "IL:17031:board-of-commissioners:district-{}"),
    ],
    "04013": [
        (r"CLERK OF THE SUPERIOR COURT COUNTY", "AZ:04013:superior-court-clerk"),
        (r"JUSTICE OF THE PEACE ([A-Z -]+)", "AZ:04013:justice-of-the-peace:{}"),
        (r"CONSTABLE ([A-Z -]+)", "AZ:04013:constable:{}"),
    ],
    "3651000": [
        (
            r"Member of the City Council - (\d+)\w\w Council District, # to be elected: 1",
            "new-york-city:council-district-{}",
        )
    ],
}
PARTIES = {
    "Republican": "republican-party",
    "Rep": "republican-party",
    "Democratic": "democratic-party",
    "Dem": "democratic-party",
    "Libertarian": "libertarian-party",
    "Lib": "libertarian-party",
    "Green": "green-party",
    "Grn": "green-party",
    "LBT": "libertarian-party",
}
NUMBERS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
WATERMARK = re.compile(r"^(?:[A-Z]{1,2} )+(?=\S\S)|(?: (?!JR|SR|II|IV)[A-Z]{1,2})+$")


def words(text: str) -> tuple[frozenset[str], frozenset[str]]:
    text = re.sub(r"\bat[- ]large\b", " atlarge ", text.lower().replace("&", " and "))
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", text)
    found = [
        SYNONYMS.get(word, word)
        for word in re.findall(r"[a-z]+|\d+", text)
        if not re.fullmatch(r"(19|20)\d\d|\d{5,}", word)
    ]
    found = [
        str(int(word))
        if word.isdigit()
        else str(ROMAN.index(word) + 1)
        if word in ROMAN and index > 0 and found[index - 1] in ("district", "seat")
        else word
        for index, word in enumerate(found)
    ]
    marks = frozenset(word for word in found if word.isdigit() or len(word) == 1 or word == "atlarge")
    return frozenset(word for word in found if word not in FILLER and word not in marks), marks


def resolve(conn, state: str, wanted: set[tuple[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    pool: dict[str, list[tuple[str, frozenset[str], frozenset[str]]]] = defaultdict(list)
    for tail, text in conn.execute(
        """select distinct on (c.office_id, c.body_id, c.district_area_id, c.seat_id)
                  regexp_replace(x.value, '^[0-9-]+:', ''), n.text
           from contest c
           join election e on e.id = c.election_id
           join entity t on t.id = c.id and t.retired_by is null
           join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
           join name n on n.entity_id = c.id and n.is_preferred and n.lang = 'en'
           left join office f on f.id = c.office_id
           left join body b on b.id = c.body_id
           join polity op on op.id = coalesce(f.polity_id, b.polity_id, e.polity_id)
           where (x.value ~ %s or x.value ~ %s) and e.date_from >= '2016-01-01' and e.date_from < %s and not c.notional
               and c.party_scope_id is null and c.kind <> 'ballot_question'
               and op.kind in ('county', 'municipality', 'school_district', 'special_district')
           order by c.office_id, c.body_id, c.district_area_id, c.seat_id, e.date_from desc""",
        [f"(^|:){state}:", "^ceda:" if state == "CA" else "^$", DAY],
    ):
        county = re.match(rf"{state}:(\d{{5}})(?:\d{{5}})?(?::|$)", tail)
        pool[county[1] if county else ""].append((tail, *words(f"{text} {tail.split(':', 2)[-1].replace('-', ' ')}")))
    found = {}
    for county, contest in wanted:
        mine, marks = words(contest)
        scored = sorted(
            (
                (len(mine & theirs) / len(mine | theirs), tail)
                for tail, theirs, their_marks in pool[county] + pool[""]
                if their_marks == marks
            ),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            found[(county, contest)] = (scored[0][1], scored[0][1])
            continue
        if len(marks) != 1 or not (mark := next(iter(marks))).isalnum() or mark == "atlarge":
            continue
        siblings = sorted(
            (
                (len(mine & theirs) / len(mine | theirs), tail)
                for tail, theirs, their_marks in pool[county]
                if len(their_marks) == 1 and re.search(rf"-{next(iter(their_marks))}$", tail)
            ),
            reverse=True,
        )
        best = [tail for score, tail in siblings if score == siblings[0][0]] if siblings else []
        if best and siblings[0][0] >= 0.6 and len({tail.rsplit("-", 1)[0] for tail in best}) == 1:
            found[(county, contest)] = (best[0], f"{best[0].rsplit('-', 1)[0]}-{mark}")
    return found


def fetch() -> None:
    for state, (url, *_) in FEEDS.items():
        if state in DELIVERED:
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.store(
                    SOURCE,
                    url,
                    DELIVERED[state].read_bytes(),
                    suffix=DELIVERED[state].suffix,
                    retrieved_at=datetime.fromtimestamp(DELIVERED[state].stat().st_mtime, UTC),
                    media_type={
                        ".pdf": "application/pdf",
                        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    }.get(DELIVERED[state].suffix, "text/csv"),
                )
            continue
        raw.fetch(SOURCE, url, suffix=Path(url).suffix or ".pdf")
        if state in PRIMARIES:
            raw.fetch(SOURCE, PRIMARIES[state], suffix=".zip")
    for fips, url in FILES.items():
        if fips in DELIVERED:
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.store(
                    SOURCE,
                    url,
                    DELIVERED[fips].read_bytes(),
                    suffix=DELIVERED[fips].suffix,
                    retrieved_at=datetime.fromtimestamp(DELIVERED[fips].stat().st_mtime, UTC),
                    media_type="application/json" if fips == "17031c" else "application/pdf",
                )
            continue
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except httpx.HTTPError as error:
            print(f"{url}: {error}")


def parse() -> None:
    rows: list[dict[str, object]] = []

    def office(fips: str, heading: str) -> str | None:
        for pattern, tail in OFFICES[fips]:
            found = re.fullmatch(pattern, heading.strip())
            if found is not None:
                return tail.format(*(slug(group) for group in found.groups()))
        return None

    def add(
        fips: str,
        tail: str,
        heading: str,
        seats: int,
        name: str,
        party: str | None,
        independent: bool = False,
        feed: str | None = None,
    ) -> None:
        rows.append(
            {
                "fips": fips,
                "feed": feed or fips,
                "tail": tail,
                "heading": heading.strip(),
                "seats": seats,
                "name": cased(re.sub(r"\s+", " ", name).strip()),
                "party": party,
                "independent": independent,
                "unexpired": bool(re.search(r"(?i)unexpired", heading)),
            }
        )

    def text(fips: str, *flags: str) -> str:
        path = raw.cached(SOURCE, FEEDS[fips][0] if fips in FEEDS else FILES[fips]).path
        return subprocess.run(["pdftotext", *flags, str(path), "-"], capture_output=True, check=True).stdout.decode()

    tail = None
    for line in text("0644000", "-layout").splitlines():
        candidate = re.match(r"^([A-Z][A-Z .,'\"()-]+?)\s{2,}\S", line)
        if line.strip() and not candidate:
            tail = office("0644000", line.strip())
        elif candidate and tail is not None:
            add("0644000", tail, "", 1, candidate[1], None)

    lines = text("17031", "-layout").splitlines()
    for index, line in enumerate(lines):
        vote = re.match(r"^\s+Vote for (?:not more than )?(\w+)\s*$", line)
        if vote is None or (tail := office("17031", lines[index - 1])) is None:
            continue
        for other in lines[index + 1 :]:
            candidate = re.match(r"^\s*\(\d+\)\s+(.+?)(?:\s+\(([^()]+)\))?\s{2,}\w+\s*$", other)
            if candidate is None:
                if other.strip() and not re.match(r"^\s*(Write-in|Ballot No\.|Page \d)", other.strip()):
                    break
                continue
            add(
                "17031",
                tail,
                lines[index - 1],
                NUMBERS[vote[1]],
                candidate[1],
                PARTIES.get(str(candidate[2]).split()[0]),
            )

    for fips in ("48201", "48113"):
        words = subprocess.run(
            ["pdftotext", "-bbox-layout", str(raw.cached(SOURCE, FILES[fips]).path), "-"],
            capture_output=True,
            check=True,
        ).stdout.decode()
        pages = [
            [
                (float(x), float(y), html.unescape(word))
                for x, y, word in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', page)
            ]
            for page in words.split("<page ")[1:]
        ]
        grid: list[int] = []
        for x in sorted(
            {
                round(x)
                for page in pages
                for (x, y, word), (_, other_y, following) in pairwise(page)
                if word == "Vote" and abs(y - other_y) < 1 and following in ("for", "por")
            }
        ):
            if not grid or x - grid[-1] > 40:
                grid.append(x)
        lines = []
        for page in pages:
            for index, left in enumerate(grid):
                right = grid[index + 1] - 5 if index + 1 < len(grid) else 10_000
                found_rows: dict[float, list[tuple[float, str]]] = {}
                for x, y, word in page:
                    if (index == 0 or x >= left - 5) and x < right:
                        key = next((row for row in found_rows if abs(row - y) < 2), y)
                        found_rows.setdefault(key, []).append((x, word))
                lines += [
                    WATERMARK.sub("", " ".join(word for _, word in sorted(found_rows[row])))
                    for row in sorted(found_rows)
                ]
        lines = [line for line in lines if line and not re.fullmatch(r"[A-Z]{1,2}", line)]
        for index, line in enumerate(lines):
            if (tail := office(fips, line)) is None:
                continue
            found = []
            started = False
            for other in lines[index + 1 :]:
                if (
                    other.startswith(("Vote ", "Votar", "Bỏ phiếu"))
                    or not started
                    and not re.search(r"[A-Z]{2}", other)
                ):
                    started = started or other.startswith("Vote ")
                    continue
                if not started:
                    continue
                if fips == "48113":
                    candidate = re.fullmatch(r"(.+?) (Rep|Dem|Lib|Grn|Ind)", other)
                    if candidate is None:
                        if found:
                            break
                        continue
                    found.append((candidate[1], PARTIES.get(candidate[2])))
                elif re.fullmatch(r"[A-Z][A-Z .,'\"-]+", other):
                    found.append((other, None))
                elif re.fullmatch(r"(\w+) Party|Independent", other) and found and found[-1][1] is None:
                    found[-1] = (found[-1][0], PARTIES.get(other.split()[0], ""))
                elif not other.startswith("Partido"):
                    break
            for name, party in found:
                if party is not None:
                    add(fips, tail, line, 1, name, party or None)

    lines = text("12086", "-layout").splitlines()
    for index, line in enumerate(lines):
        if (tail := office("12086", line)) is None:
            continue
        for other in lines[index + 1 : index + 20]:
            candidate = re.fullmatch(r"(\S.+?)\s{2,}\d+", other.strip())
            if candidate is not None:
                add("12086", tail, line, 1, candidate[1], None)
            elif other.strip() == "" and any(row["tail"] == tail for row in rows):
                break

    tail = None
    for line in text("06073", "-layout").splitlines():
        if re.fullmatch(r"[A-Z][^a-z]*", line.rstrip()) and "N/A" not in line:
            tail = office("06073", line)
            heading = line
        elif tail is not None and (candidate := re.match(r"^([A-Z][A-Z .'\"-]+), ([A-Z][A-Z .'\"-]+?)\s{2,}N/A", line)):
            add("06073", tail, heading, 1, f"{candidate[2]} {candidate[1]}", None)

    tail = None
    for line in text("06059", "-layout").splitlines():
        if re.match(r"^(Nonpartisan|Voter Nominated|Partisan|Party Nominated) ", line):
            tail = office("06059", line)
            heading = line
        elif tail is not None and (
            candidate := re.fullmatch(r"\s{8,}(?:Candidate\(s\):)?\s+([A-Z][A-Z .'\"-]+)", line)
        ):
            add("06059", tail, heading, 1, candidate[1], None)

    tail = None
    for line in text("3651000", "-layout").splitlines():
        if re.match(r"^ \S", line):
            tail = office("3651000", line)
            heading = line
        elif tail is not None and (candidate := re.match(r"^  (\S.*?)\s{2,}(\S.*?)\s{2,}\S", line)):
            add(
                "3651000",
                tail,
                f"{heading} (unexpired term)",
                1,
                candidate[2],
                PARTIES.get(candidate[1]) if candidate[1] != candidate[2] else None,
            )

    for race in json.loads(raw.cached(SOURCE, FILES["17031c"]).path.read_bytes())["Candidates"]:
        heading = f"{race['Name']} {race['Office']} {race['Vacancy']}"
        if (tail := office("17031c", heading)) is None:
            continue
        for item in race["item"]:
            add(
                "17031c",
                tail,
                heading,
                race["Vote"],
                item["Name"].strip("'").replace("''", '"'),
                PARTIES.get(item["Pty"].title()),
            )

    for line in text("04013", "-layout").splitlines():
        row = re.fullmatch(r"(.+?) / .+?\s{2,}(.+?)\s{2,}(.+?), (.+?)\s{2,}(DEM|REP|LBT|GRN|IND)\s*", line)
        if row is not None and (tail := office("04013", f"{row[1]} {row[2].split(' / ')[0]}")) is not None:
            last = re.sub(r" (JR|SR|II|III|IV)$", r", \1", row[3])
            add(
                "04013",
                tail,
                row[1],
                1,
                re.sub(
                    r'"([A-Z]+)"',
                    lambda match: f'"{match[1].title()}"',
                    cased(f"{row[4]} {last.split(', ')[0]}" + "".join(f" {part}" for part in last.split(", ")[1:])),
                ),
                PARTIES.get(row[5].title(), PARTIES.get(row[5])),
            )

    feeds: dict[str, list[dict[str, object]]] = defaultdict(list)
    templated: dict[str, str] = {}
    minted: dict[str, tuple[str, str, str, str]] = {}

    with zipfile.ZipFile(raw.cached(SOURCE, PRIMARIES["NC"]).path) as archive:
        primary = archive.read(archive.namelist()[0]).decode("utf-8", "replace")
    totals: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seats_won: dict[tuple[str, str], int] = {}
    for row in csv.DictReader(io.StringIO(primary), delimiter="\t"):
        party = re.search(r"\((DEM|REP|LIB|GRE|CST)\)$", row["Contest Name"])
        if party is None:
            continue
        key = (re.sub(r"\s*\((DEM|REP|LIB|GRE|CST)\)$", "", row["Contest Name"]).strip(), party[1])
        totals[key][row["Choice"].strip()] += int(row["Total Votes"] or 0)
        seats_won[key] = int(row["Vote For"] or 1)
    winners = {
        key: set(sorted(votes, key=lambda name: -votes[name])[: seats_won[key]]) for key, votes in totals.items()
    }
    for row in csv.DictReader(
        io.StringIO(raw.cached(SOURCE, FEEDS["NC"][0]).path.read_text(encoding="utf-8", errors="replace"))
    ):
        contest = re.sub(r"\s+", " ", row["contest_name"]).strip()
        if re.match(r"(US|NC) |DISTRICT ATTORNEY", contest):
            continue
        key = (contest, row["party_contest"] or row["party_candidate"])
        if row["election_dt"] != "11/03/2026" and key in winners and row["name_on_ballot"].strip() not in winners[key]:
            continue
        partisan = row["is_partisan"] == "TRUE"
        feeds["NC"].append(
            {
                "county": row["county_name"],
                "contest": contest,
                "seats": int(row["vote_for"] or 1),
                "name": row["name_on_ballot"],
                "party": {
                    "DEM": "democratic-party",
                    "REP": "republican-party",
                    "LIB": "libertarian-party",
                    "GRE": "green-party",
                    "CST": "constitution-party",
                }.get(row["party_candidate"])
                if partisan
                else None,
                "independent": partisan and row["party_candidate"] in ("UNA", "NON", ""),
                "unexpired": row["is_unexpired"] == "TRUE",
            }
        )

    def party(text: str | None) -> tuple[str | None, bool]:
        word = (text or "").strip().lower()
        for prefix, key in (
            ("democrat", "democratic-party"),
            ("republican", "republican-party"),
            ("libertarian", "libertarian-party"),
            ("green", "green-party"),
            ("constitution", "constitution-party"),
        ):
            if word.startswith(prefix):
                return key, False
        return None, word in ("independent", "unaffiliated", "no party", "non-partisan independent", "petition")

    for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, FEEDS["MD"][0]).path.read_text(encoding="utf-8-sig"))):
        if row["Candidate Status"] != "Active":
            continue
        where = row["Contest Run By District Name and Number"]
        county = where if "District" not in where else row["Candidate Residential Jurisdiction"]
        county = re.sub(r"(?i) county$", "", county)
        title = row["Office Name"]
        town = re.match(r"(Council|Mayor) - City of (.+)", title)
        contest = (
            f"{town[2]} {'City Council' if town[1] == 'Council' else 'Mayor'}"
            if town
            else f"{county} County {title} {re.sub(r'^.*?(District|Precinct)', r'\1', where) if 'District' in where else ''}"
        )
        named, independent = party(row["Office Political Party"])
        feeds["MD"].append(
            {
                "county": county,
                "contest": contest,
                "seats": 0,
                "name": f"{row['Candidate First Name and Middle Name']} {row['Candidate Ballot Last Name and Suffix']}",
                "party": named,
                "independent": independent,
                "unexpired": False,
            }
        )

    for row in pl.read_excel(raw.cached(SOURCE, FEEDS["VA"][0]).path).iter_rows(named=True):
        title = str(row["Office Title"])
        district = str(row["District"] or "")
        named, independent = party(row["Candidate Party"])
        feeds["VA"].append(
            {
                "county": row["Locality"],
                "contest": f"{title} {'' if district.upper() == str(row['Locality']).upper() else district}",
                "seats": int(row["Number of Seats"] or 0),
                "name": row["Candidate Name"],
                "party": named,
                "independent": independent,
                "unexpired": row["Special Office Unexpired Term End Date"] is not None,
            }
        )

    category = None
    for cells in pl.read_excel(raw.cached(SOURCE, FEEDS["IN"][0]).path, has_header=False).iter_rows():
        if cells[0] == "OFFICE CATEGORY:":
            category = cells[1]
            continue
        if not cells[2] or cells[0] == "BALLOT NAME" or category in IN_SKIP:
            continue
        title = str(cells[2])
        county = re.search(r", (\w[\w .]*?) County\b", title)
        seats = re.search(r"\((\d+) seats?\)", title)
        named, independent = party(cells[1])
        feeds["IN"].append(
            {
                "county": county[1] if county else "",
                "contest": re.sub(r"\(\d+ seats?\)|, \w[\w .]*? County\b", "", title),
                "seats": int(seats[1]) if seats else 0,
                "name": cells[0],
                "party": named,
                "independent": independent,
                "unexpired": False,
            }
        )

    for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, FEEDS["SC"][0]).path.read_text(encoding="utf-8-sig"))):
        title = row["Office"].strip()
        if row["Status"] != "Active" or re.match(
            r"(U\.S\.|State |Governor|Lieutenant|Secretary of State|Attorney General|Comptroller|Adjutant"
            r"|Commissioner of Agriculture|Superintendent of Education|Solicitor|State Treasurer)",
            title,
        ):
            continue
        counties = row["Associated Counties"].split(",")
        named, independent = party(row["Party"])
        feeds["SC"].append(
            {
                "county": counties[0] if len(counties) == 1 else row["Filing Location"],
                "contest": f"{counties[0] if len(counties) == 1 else ''} County {title} {row['District']}",
                "seats": 0,
                "name": f"{row['Ballot Name (first - middle)']} {row['Ballot Name (last - suffix)']}",
                "party": named,
                "independent": independent,
                "unexpired": False,
            }
        )

    for cells in csv.reader(io.StringIO(raw.cached(SOURCE, FEEDS["NM"][0]).path.read_text(encoding="utf-8-sig"))):
        if (
            len(cells) < 18
            or "Qualified" not in cells[-4:]
            or not re.match(r"(County|Probate|Magistrate|Metropolitan)", cells[0])
        ):
            continue
        head, code = cells[: len(cells) - 13], cells[len(cells) - 13].strip()
        suffix = head.pop().strip() if head[-1].strip() in ("JR", "SR", "II", "III", "IV") else ""
        named, independent = party({"DEM": "Democratic", "REP": "Republican", "LIB": "Libertarian"}.get(code, code))
        feeds["NM"].append(
            {
                "county": head[-4],
                "contest": f"{head[-4]} County {head[0]} {' '.join(part.strip() for part in head[1:-4])}",
                "seats": 0,
                "name": cased(" ".join(part for part in [*head[-3:], suffix] if part)),
                "party": named,
                "independent": independent or code in ("DTS", "IND"),
                "unexpired": False,
            }
        )

    with connect() as conn:
        exact = {
            tail
            for (tail,) in conn.execute(
                """select distinct regexp_replace(value, '^[0-9-]+:', '') from external_id
                   where scheme = 'us.contest' and value ~ '^[0-9-]+:MN:27[0-9]{5}' and value < %s""",
                [f"{DAY}"],
            )
        }
    codes = {
        tail.split(":")[1][-5:]: tail.split(":")[1]
        for tail in exact
        if tail.split(":")[2] in ("council", "mayor", "town-board", "clerk", "treasurer", "clerk-treasurer")
    }
    leas = {
        row["state_leaid"].removeprefix("MN-"): row["leaid"]
        for row in json.loads(raw.cached(mn_local.SOURCE, mn_local.DIRECTORY).path.read_text())["results"]
    }
    names = {
        cells[0]: cells[1]
        for cells in csv.reader(
            io.StringIO(
                raw.cached(mn_local.SOURCE, mn_local.url(mn_local.COUNTIES, "MCDTbl.txt")).path.read_text(
                    encoding="utf-8", errors="replace"
                )
            ),
            delimiter=";",
        )
    }
    for key in ("MN", "MN-county", "MN-school"):
        for cells in csv.reader(
            io.StringIO(raw.cached(SOURCE, FEEDS[key][0]).path.read_text(encoding="utf-8", errors="replace")),
            delimiter=";",
        ):
            if len(cells) < 11 or cells[10] == "WI" or "QUESTION" in cells[4].upper():
                continue
            title = re.sub(r"\s+", " ", cells[4]).strip()
            upper = title.upper()
            stem = re.sub(r"\s*\([^)]*\)", "", title).strip()
            found = re.search(
                r"(?i)\b(ward \d+|district \d+|position \d+|seat [a-z0-9]+|at large)\b", stem
            ) or re.search(r"(?i)\b((?!member\b)[a-z]+ district)$", stem)
            seat = (
                ":" + slug(re.sub(r"(?i)^at large$", "At-Large", found[1].title().replace("At Large", "At-Large")))
                if found
                else ""
            )
            special = ":special" if upper.startswith("SPECIAL ELECTION") else ""
            tail = None
            if key == "MN-school":
                school = re.search(r"\((ISD|SSD) ?#\s*(\d+)\)", upper)
                code = school and leas.get(f"{mn_local.TYPES[school[1]]}{int(school[2]):04d}")
                tail = code and f"MN:{code}:board-of-education{seat}{special}"
            elif key == "MN":
                held = next(
                    (
                        held
                        for held, pattern in (
                            ("mayor", r"MAYOR\b"),
                            ("council", r"COUNCIL MEMBER\b"),
                            ("town-board", r"TOWN SUPERVISOR\b"),
                            ("clerk", r"(?:TOWN|CITY) CLERK\b(?! ?- ?TREASURER)"),
                            ("clerk-treasurer", r"(?:TOWN|CITY) CLERK ?- ?TREASURER\b"),
                            ("treasurer", r"(?:TOWN|CITY) TREASURER\b"),
                        )
                        if re.match(rf"(?:SPECIAL ELECTION FOR )?{pattern}", upper)
                    ),
                    None,
                )
                tail = held and cells[5] in codes and f"MN:{codes[cells[5]]}:{held}{seat}{special}"
            magnitude = re.search(r"\(ELECT (\d+)\)", upper)
            feeds[key].append(
                {
                    "county": names.get(cells[1], "") if key == "MN-county" else "",
                    "contest": f"{names.get(cells[1], '')} County {title}"
                    if key == "MN-county"
                    else f"{cells[5]} {title}",
                    "seats": int(magnitude[1]) if magnitude else 0,
                    "name": cells[7],
                    "party": None,
                    "independent": False,
                    "unexpired": False,
                    "tail": tail or None,
                }
            )

    heading = ""
    lines = text("CA-06037", "-layout").splitlines()
    for index, line in enumerate(lines):
        if found := re.match(r"(\d{4,5})-(.+?)\s{2,}Vote for (?:no more than )?(\d+)", line):
            more = (
                lines[index + 1]
                if re.match(r"[A-Za-z]", lines[index + 1]) and not lines[index + 1].startswith("CONTEST")
                else ""
            )
            heading = f"{found[2]} {more}".strip()
            seats = int(found[3])
            keep = not re.match(
                r"(UNITED STATES|MEMBER OF THE STATE|STATE SENATOR|GOVERNOR|LIEUTENANT|SECRETARY OF STATE|CONTROLLER"
                r"|TREASURER$|ATTORNEY GENERAL|INSURANCE|MEMBER, STATE BOARD|SUPERINTENDENT OF PUBLIC|For |JUDGE OF THE SUPERIOR|LOS ANGELES CITY)",
                heading,
            )
        elif (
            heading
            and keep
            and not lines[index - 1].strip()
            and (found := re.match(r" {15,25}([A-Z][A-Z .,'\"()-]+?)(?:\s{2,}|\s*$)", line))
            and not re.match(r"(PARTY PREFERENCE|NON PARTISAN|NONPARTISAN)", found[1])
        ):
            feeds["CA-06037"].append(
                {
                    "county": "Los Angeles",
                    "contest": re.sub(r"\bGENERAL MUNICIPAL ELECTION\b|\bCITY OF\b", " ", heading),
                    "seats": seats,
                    "name": cased(found[1]),
                    "party": None,
                    "independent": False,
                    "unexpired": bool(re.search(r"(?i)short term|unexpired", heading)),
                }
            )

    lines = text("CA-06073", "-layout").splitlines()
    for index, line in enumerate(lines):
        if found := re.match(r" Election = (YES|NO)\s+Vote for (\d+)", line):
            heading = next(previous.strip() for previous in reversed(lines[:index]) if previous.strip())
            seats = int(found[2])
            unexpired = "Short Term" in lines[index + 1]
            keep = found[1] == "YES" and not re.match(
                r"(GOVERNOR|LIEUTENANT|SECRETARY OF STATE|CONTROLLER|TREASURER$|ATTORNEY GENERAL|INSURANCE|STATE |U\.S\."
                r"|UNITED STATES|BOARD OF SUPERVISORS|.*JUDGE|.*JUSTICE|SUPERIOR COURT|.*PLANNING)",
                heading,
            )
        elif (
            keep
            and (found := re.match(r"([A-Z][^,]*?), (.+?)(?: - ([A-Z]{2,4}))?\s{3,}\S.*\s(YES|NO)\s*$", line))
            and found[4] == "YES"
        ):
            contest = heading
            for short, full in (
                (r"\bCOMM COLLEGE\b", "COMMUNITY COLLEGE"),
                (r"\bELEM\b", "ELEMENTARY"),
                (r"\bSCHL\b", "SCHOOL"),
                (r"\bUNIF\b", "UNIFIED"),
            ):
                contest = re.sub(short, full, contest)
            feeds["CA-06073"].append(
                {
                    "county": "San Diego",
                    "contest": contest,
                    "seats": seats,
                    "name": cased(f"{found[2]} {found[1]}"),
                    "party": None,
                    "independent": False,
                    "unexpired": unexpired,
                }
            )

    for key, place, floor in (("CA-06059", "Orange", 3000), ("CA-06065", "Riverside", 2000)):
        heading = ""
        expect = False
        for line in text(key, "-layout").splitlines():
            if found := re.match(r"\s{2,}\*?\s*(\d{4})\s+([A-Z].+?)\s*$", line):
                heading = (
                    found[2]
                    if int(found[1]) >= floor
                    and not re.match(r"(Superintendent|Supervisor|Judge|Associate Justice)", found[2])
                    else ""
                )
                continue
            found = re.match(r"(?:\s+(Candidate\(s\):)\s+|\s{20,40})(\S.*?)\s*$", line)
            if found is None or re.search(r"\s{3}", found[2]):
                continue
            if re.match(
                r"(Requirements Status|Sigs In|Declaration|Official Filing|Electronic Candidate|Code of Fair"
                r"|Nomination Papers|Request Opt|Candidate Statement|Online Application|Filing |NOM\b"
                r"|Ballot Designation|Qualified Date|User Codes|Cand ID|Fees)",
                found[2],
            ):
                expect = True
                continue
            if heading and (expect or found[1]):
                contest = re.sub(r",\s*(?:Full Term|Short Term|FT|ST)$", "", heading)
                for short, full in (
                    (r"\bMbr Gov Bd\b", "Governing Board Member"),
                    (r"\bTA\b", "Trustee Area"),
                    (r"\bSch\b", "School"),
                    (r"\bDist\b", "District"),
                    (r"\bComm\b", "Community"),
                    (r"\bColl\b", "College"),
                    (r"\bRcho\b", "Rancho"),
                    (r"\bUSD\b", "Unified School District"),
                    (r"\bHS\b", "High School"),
                    (r"\bOC\b", "Orange County"),
                    (r"\bDiv\.?", "Division"),
                    (r"\bDir\b", "Director"),
                    (r"\bMuni\b", "Municipal"),
                    (r"\bRec\b", "Recreation"),
                ):
                    contest = re.sub(short, full, contest)
                feeds[key].append(
                    {
                        "county": place,
                        "contest": contest,
                        "seats": 0,
                        "name": cased(
                            re.sub(
                                r", (Democratic|Republican|Libertarian|Green|Peace and Freedom|American Independent"
                                r"|No Party Preference)$",
                                "",
                                found[2],
                            )
                        ),
                        "party": None,
                        "independent": False,
                        "unexpired": bool(re.search(r"(Short Term|, ST)$", heading)),
                    }
                )
            expect = False

    lines = [line.replace("\f", "") for line in text("CA-06071", "-layout").splitlines() if line.strip()]
    jurisdiction, heading, pending, keep, seats = "", "", "", False, 0
    for index, line in enumerate(lines):
        if line.strip().startswith("Number to be elected"):
            heading = lines[index - 1].strip()
            if re.fullmatch(r"\s{1,6}\S.*", lines[index - 2]) and not re.match(
                r"\s*(No|Yes|N/A|Last Updated|Number to be|Candidates |Candidacy|Will contest)\b", lines[index - 2]
            ):
                jurisdiction = lines[index - 2].strip()
            seats = int(line.split()[-1])
        elif line.strip().startswith("Will contest appear"):
            keep = line.split()[-1] == "Yes" and not re.match(
                r"(State of California|United States|Superior Court)", jurisdiction
            )
        elif (
            re.fullmatch(r"[A-Z][A-Z .,'\"()-]+?(?: - Incumbent)?", line)
            and index + 1 < len(lines)
            and re.match(r"(Party Preference|Approved Ballot Designation)", lines[index + 1])
        ):
            pending = line.removesuffix(" - Incumbent")
        elif pending and re.match(r"\s*(No|Yes|N/A)\b", line):
            if keep and line.split()[-1] == "Yes":
                feeds["CA-06071"].append(
                    {
                        "county": "San Bernardino",
                        "contest": f"{jurisdiction} {heading}",
                        "seats": seats,
                        "name": cased(pending),
                        "party": None,
                        "independent": False,
                        "unexpired": bool(re.search(r"(?i)short term|unexpired", heading)),
                    }
                )
            pending = ""

    jurisdiction, contest, seats, unexpired = "", "", 0, False
    for line in text("MI-26125", "-layout").splitlines():
        if not line.strip() or re.match(r"\s*(Updated|Official Candidate List|November 3|Petitions)", line):
            continue
        if "Section" in line:
            jurisdiction, contest = "", ""
        elif found := re.match(r"(\S.*?)\s{2,}Vote for not more than (\d+)\s+(.*?)\s*$", line):
            contest = (
                ""
                if re.search(
                    r"Judge|Justice|Governor|Senator|Representative|State Board|University|Attorney General"
                    r"|Secretary of State|Proposal",
                    found[1],
                )
                else f"{jurisdiction or 'Oakland County'} {found[1]}"
            )
            seats, unexpired = int(found[2]), "Term Ending" in found[3]
        elif re.fullmatch(r"[A-Z][A-Za-z.'’ -]+", line):
            jurisdiction, contest = line.strip(), ""
        elif contest and (
            found := re.match(r"(Dem|Rep|Lib|Ust|Grn|Wc|Nlp|Npa)?\s+([A-Z][^,\d]+?)(?:\s{2,}|\s*$)", line)
        ):
            named, independent = party(
                {"Dem": "Democratic", "Rep": "Republican", "Lib": "Libertarian", "Grn": "Green"}.get(found[1] or "", "")
            )
            feeds["MI-26125"].append(
                {
                    "county": "Oakland",
                    "contest": contest,
                    "seats": seats,
                    "name": cased(re.sub(r"\s*\(Incumbent\)", "", found[2])),
                    "party": named,
                    "independent": independent or found[1] == "Npa",
                    "unexpired": unexpired,
                }
            )

    jurisdiction, contest, seats = "", "", 0
    for line in text("CA-06099", "-layout").splitlines():
        if found := re.match(r"(CITY OF [A-Z .'-]+?)(?:\s+Contact\b|\s*$)", line):
            jurisdiction, contest = found[1], ""
        elif found := re.match(r"(\S.*?)\s*\(Vote for (\d+)\)", line):
            contest = f"{jurisdiction} {found[1]}" if re.match(r"(COUNCIL|MAYOR|CITY )", found[1]) else found[1]
            seats = int(found[2])
        elif not line.strip() or re.match(r"[A-Z ]+$", line.strip()):
            contest = contest if not line.strip() else ""
        elif contest and (found := re.match(r"\*?([A-Z][A-Z .,'\"()-]+?)\s{2,}\S", line)):
            feeds["CA-06099"].append(
                {
                    "county": "Stanislaus",
                    "contest": re.sub(r"\s*-\s*SHORT TERM", "", contest),
                    "seats": seats,
                    "name": cased(found[1]),
                    "party": None,
                    "independent": False,
                    "unexpired": "SHORT TERM" in contest,
                }
            )

    contest, seats = "", 0
    for line in text("CA-06079", "-layout").splitlines():
        if found := re.match(r"\d{4}\s{2,}(\S.*?)(?:\s{2,}On Ballot: (Yes|No)\s+Vote For: (\d+))?\s*$", line):
            contest = found[1] if found[2] != "No" else ""
            seats = int(found[3] or 0)
        elif contest and (
            found := re.match(
                r"\s+Qualified\s+\d+\s+(\S.*?)(?: (?:DEM|REP|LIB|GRN|AIP|PF|NPP))?\s{2,}Candidate Stmt", line
            )
        ):
            feeds["CA-06079"].append(
                {
                    "county": "San Luis Obispo",
                    "contest": re.sub(r"\bTA(\d+)\b", r"Trustee Area \1", contest),
                    "seats": seats,
                    "name": cased(found[1]),
                    "party": None,
                    "independent": False,
                    "unexpired": bool(re.search(r"(?i)short term|unexpired", contest)),
                }
            )

    for line in text("NJ-34029", "-layout").splitlines():
        found = re.match(
            r"(\S.*?)\s{2,}([A-Z][A-Z ,'-]+?)\s{2,}(?:(UNEXP\.)\s+)?(\d+)\s+(\d+)\s+(\S.*?)\s{2,}(\S.*?)\s{2,}([A-Z][a-z]+(?: [A-Za-z]+)*)\b",
            line,
        )
        if found is None or "NO NOMINATION" in line:
            continue
        named, independent = party(found[8])
        feeds["NJ-34029"].append(
            {
                "county": "Ocean",
                "contest": f"{found[1]} {found[2]}",
                "seats": int(found[5]),
                "name": cased(f"{found[6]} {found[7]}"),
                "party": named,
                "independent": independent,
                "unexpired": found[3] is not None,
            }
        )

    nobody = re.compile(
        r"(?i)no nom\w* made|no petition filed|no candidates? filed|^none$|can be found with|^vacant|^name on ballot$|\(write-in\)|^november \d|^page \d+ of"
    )
    federal = re.compile(
        r"(?i)^(federal|state|cd \d+|united states|u\.s\.|house of rep|member of (the )?(house|united states))"
    )
    codes = {"dem": "Democratic", "rep": "Republican", "ind": "Independent", "np": "Nonpartisan"}

    town = post = ""
    seats = 0
    for line in text("NJ-34001", "-layout").splitlines():
        found = re.match(r"(\S.*?)?\s+V(\d)\s+(\S.*?)\s{2,}([A-Z]{3})\s+(\S.*?)(?:\s{2,}|$)", line)
        more = re.match(r"\s{20,}([A-Z]{3})\s+(\S.*?)(?:\s{2,}|$)", line)
        if found:
            town, seats, post, code, name = (found[1] or town).strip(), int(found[2]), found[3], found[4], found[5]
        elif more and post:
            code, name = more[1], more[2]
        else:
            if re.match(r"\S", line) and not re.match(r"\s*Vote For", line):
                post = ""
            continue
        if town == "Federal" or nobody.search(name):
            continue
        named, independent = party(codes.get(code.lower(), code))
        post = re.sub(r"(?i)\s*-\s*\d+ yr unexpired term", " unexpired", post)
        feeds["NJ-34001"].append(
            {
                "county": "Atlantic",
                "contest": re.sub(r"^(?!Atlantic County)", "Atlantic County ", post.removesuffix(" unexpired"))
                if town == "County"
                else f"{town} {post.removesuffix(' unexpired')}",
                "seats": seats,
                "name": cased(name),
                "party": named,
                "independent": independent,
                "unexpired": post.endswith(" unexpired"),
            }
        )

    orders: dict[tuple[object, object], set[str]] = defaultdict(set)
    for line in text("NJ-34015", "-layout").splitlines():
        found = re.match(
            r"(\d+)[A-Z]\s+(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)(?:\s{2,}(\S+(?: \S+)*))?(?:\s{2,}|$)", line
        )
        if found is None or re.match(r"CD \d", found[4]) or found[4] == "COUNTYWIDE" and "COMMISSIONER" not in found[3]:
            continue
        contest = (
            f"{'Gloucester County' if found[4] == 'COUNTYWIDE' else found[4]} {found[3].replace(' UNEXPIRED', '')}"
        )
        orders[(contest, "UNEXPIRED" in found[3])].add(found[1])
        if nobody.search(found[5]):
            continue
        named, independent = party(found[2])
        feeds["NJ-34015"].append(
            {
                "county": "Gloucester",
                "contest": contest,
                "seats": 0,
                "name": cased(f"{found[5]} {found[6] or ''}"),
                "party": named,
                "independent": independent or named is None and "EDUCATION" not in found[3],
                "unexpired": "UNEXPIRED" in found[3],
            }
        )
    for row in feeds["NJ-34015"]:
        row["seats"] = len(orders[(row["contest"], row["unexpired"])])

    town = ""
    for line in text("NJ-34037", "-layout").splitlines():
        if found := re.match(r"([A-Z][A-Z .'-]+?)\s{2,}# Seats", line):
            town = found[1].title()
        elif town and (
            found := re.match(r"(\S.*?)\s{2,}(\d+)\s+(?:\d+\s+)?(\S.*?)\s{2,}([A-Z]{2,3})\s+(?:NJ\s+)?", line)
        ):
            if nobody.search(found[3]) or town == "Sussex County" and federal.search(found[1]):
                continue
            named, independent = party(codes.get(found[4].lower(), found[4]))
            feeds["NJ-34037"].append(
                {
                    "county": "Sussex",
                    "contest": f"{town} {re.sub(r'(?i)\s*\([^)]*\)', '', found[1])}",
                    "seats": int(found[2]),
                    "name": cased(re.sub(r"\s*\(.*?\)", "", found[3])),
                    "party": named,
                    "independent": independent or found[4] == "IND",
                    "unexpired": "nexpired" in found[1],
                }
            )

    town = post = ""
    seats, unexpired = 0, False
    for line in text("NJ-34033", "-layout").splitlines():
        if found := re.match(r"\s*(\S.*?) – (\d+) year (unexpired )?term – Vote for (\d+)", line):
            if found[1] == "School Board":
                post = f"{town} Board of Education"
            else:
                town = found[1]
                suffix = {"Township": "Committee", "Borough": "Council", "City": "Council"}.get(town.split()[-1])
                post = f"{town} {suffix}" if suffix else town.replace("County", "Salem County", 1)
            seats, unexpired = int(found[4]), found[3] is not None
        elif re.match(r"\S", line):
            post = ""
        elif post and (found := re.match(r"\s+(?:(\S.*?)\s{2,})?(\S.*?)\s*$", line)):
            if nobody.search(found[2]) or federal.search(post):
                continue
            named, independent = party(found[1])
            feeds["NJ-34033"].append(
                {
                    "county": "Salem",
                    "contest": post,
                    "seats": seats,
                    "name": cased(found[2]),
                    "party": named,
                    "independent": independent,
                    "unexpired": unexpired,
                }
            )

    town = ""
    for line in text("NJ-34039", "-layout").splitlines():
        if found := re.match(r"\s{30,}((?:TOWNSHIP|CITY|BOROUGH|TOWN|VILLAGE) OF [A-Z .'-]+?)\s*$", line):
            town = found[1].title()
        elif found := re.match(
            r"\s{30,}(SHERIFF|COUNTY CLERK|MEMBERS OF BOARD OF COUNTY COMMISSIONERS|SURROGATE)\s*$", line
        ):
            town = f"Union County {found[1].title().replace('Members Of ', '')}"
        elif town and (
            found := re.match(
                r"\s*(?:\d+[A-Z]\s+)?(\S.*?)\s{2,}(?:\S.*?\s{2,})?(Dem|Rep|Ind|[A-Z][a-z]{2})\s{2,}(\S.*?)?(?:\s{2,}|$)",
                line,
            )
        ):
            if nobody.search(found[1]):
                continue
            named, independent = party(codes.get(found[2].lower(), found[2]))
            post = re.sub(r"(?i)^members? of |(?<=\d)(st|nd|rd|th)\b|\s*-$", "", found[3] or "")
            feeds["NJ-34039"].append(
                {
                    "county": "Union",
                    "contest": town if town.startswith("Union County") else f"{town} {post}",
                    "seats": 0,
                    "name": cased(found[1]),
                    "party": named,
                    "independent": independent,
                    "unexpired": False,
                }
            )

    lines: dict[tuple[str, ...], list[tuple[float, float, str]]] = defaultdict(list)
    for cell in csv.DictReader(io.StringIO(text("NJ-34005", "-tsv")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if cell["level"] == "5":
            lines[(cell["page_num"], cell["par_num"], cell["block_num"], cell["line_num"])].append(
                (float(cell["left"]), float(cell["top"]), cell["text"])
            )
    pages: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
    for (page, *_), words in lines.items():
        pages[int(page)].append((words[0][0], words[0][1], " ".join(word[2] for word in words)))
    heads: list[tuple[int, str]] = []
    for page in sorted(pages):
        cells = [c for c in pages[page] if not re.match(r"Burlington County 2026|\d+/\d+/\d{4}$|\d+:\d+ [AP]M$", c[2])]
        top = next((y for x, y, t in cells if t == "JURISDICTION"), None)
        if top is not None:
            heads = sorted({round(x): t for x, y, t in cells if top - 1 <= y <= top + 5}.items())
            cells = [c for c in cells if c[1] > top + 5 and c[2] not in ("VACANCIES", "POSITION")]
        last = 0.0
        for bottom in sorted(y for x, y, t in cells if x < heads[1][0] - 5):
            record: dict[str, list[str]] = defaultdict(list)
            for x, y, t in sorted(cells, key=lambda c: (c[1], c[0])):
                if last < y <= bottom + 1:
                    record[max((h for h in heads if h[0] <= x + 15), key=lambda h: h[0])[1]].append(t)
            last = bottom + 1
            cell = {key: " ".join(value) for key, value in record.items()}
            name = re.sub(r"\s+", " ", " ".join(cell.get(k, "") for k in ("FIRST NAME", "MI", "LAST NAME", "SUFFIX")))
            kind = cell.get("JURISDICTION", "")
            if (
                kind not in ("COUNTY", "MUNICIPAL", "MUNICIPAL NONPARTISAN", "SCHOOL")
                or not name.strip()
                or nobody.search(name.strip())
            ):
                continue
            named, independent = party(cell.get("Party"))
            feeds["NJ-34005"].append(
                {
                    "county": "Burlington",
                    "contest": cell["ENTITY"],
                    "seats": int(cell.get("VACANCIES") or 0),
                    "name": cased(name),
                    "party": named,
                    "independent": independent or named is None and kind == "MUNICIPAL",
                    "unexpired": cell.get("Term") == "Unexpired",
                }
            )

    for line in text("NJ-34027", "-layout").splitlines():
        found = re.match(
            r"(\S.*?)\s{2,}(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(?:(W\d+)\s{2,})?(\S.*?)(?:\s{2,}(\S.*?))?\s{2,}(Republican|Democratic|Independent)\b",
            line,
        )
        if found is None or nobody.search(found[6]) or federal.search(found[2]):
            continue
        named, independent = party(found[8])
        feeds["NJ-34027"].append(
            {
                "county": "Morris",
                "contest": f"{found[1]} {re.sub(r'(?i)^member of the |\s*-?\s*unexpired.*$', '', found[2])}",
                "seats": int(found[4]),
                "name": cased(f"{found[6]} {found[7] or ''}"),
                "party": named,
                "independent": independent,
                "unexpired": "UNEXPIRED" in found[2].upper(),
            }
        )

    for key, county in (("NJ-34025", "Monmouth"), ("NJ-34025-c", "Monmouth")):
        town = post = ""
        seats, unexpired = 0, False
        for line in text(key, "-layout").splitlines():
            if found := re.match(
                r"(?:FOR )?(\S.*?)\s*[-–]+\s*(.*?)\s*[-–]+\s*Vote [Ff]or (\w+)\s*$",
                line.replace("AT-LARGE", "AT LARGE"),
            ):
                post = found[1] if not town else f"{town} {found[1]}"
                seats, unexpired = NUMBERS.get(found[3].title(), 1), "nexpired" in found[2]
                if key.endswith("-c"):
                    post = f"Monmouth County {re.sub(r'(?i)^members? of the ', '', found[1])}"
            elif not key.endswith("-c") and (found := re.match(r"([A-Z][A-Z .'-]+?)(?:\s+[–-].*)?\s*$", line)):
                town, post = found[1].title(), ""
            elif post and (found := re.match(r"(\S.*?)\s+\(([A-Z])\)\s*(.*)$", line)):
                if nobody.search(found[1]) or federal.search(re.sub(r"^Monmouth County ", "", post)):
                    continue
                named, independent = party(
                    {"D": "Democratic", "R": "Republican", "I": "Independent"}.get(found[2], found[3])
                )
                feeds["NJ-34025"].append(
                    {
                        "feed": key,
                        "county": county,
                        "contest": post,
                        "seats": seats,
                        "name": cased(found[1]),
                        "party": named,
                        "independent": independent,
                        "unexpired": unexpired,
                    }
                )

    pending: dict[str, str] | None = None
    for line in text("NJ-34009", "-layout").splitlines():
        if found := re.match(
            r"\s+((?:Township|City|Borough|County|Town) of \S.*?|United States)\s{2,}(\S.*?)\s{2,}(\S.*?)(?:\s{2,}|$)",
            line,
        ):
            pending = {"where": found[1], "post": found[2], "name": found[3]}
        elif pending and (found := re.match(r"\s+(\S.*?)\s{2,}(?:(\d+) Year )?(Unexpired )?.*?Vote for (\w+)", line)):
            where, post, name = pending["where"], pending["post"], re.sub(r"\s*\*+$", "", pending["name"])
            pending = None
            if where == "United States" or not name or nobody.search(name):
                continue
            named, independent = party(found[1])
            feeds["NJ-34009"].append(
                {
                    "county": "Cape May",
                    "contest": f"{'Cape May County' if where == 'County of Cape May' else where} {re.sub(r'(?i)^member of the ', '', post)}",
                    "seats": NUMBERS.get(found[4].title(), 1),
                    "name": cased(name),
                    "party": named,
                    "independent": independent,
                    "unexpired": found[3] is not None,
                }
            )

    for key, label in (("NJ-34019", "Democratic"), ("NJ-34019-r", "Republican"), ("NJ-34019-i", "Independent")):
        town = post = ""
        seats = 0
        for line in text(key, "-layout").splitlines():
            found = re.match(
                r"(\S.*?)?\s{2,}(\S.*?)\s{2,}\d+ Year Term\s{2,}Vote for (\w+)\s{2,}(\S.*?)(?:\s{2,}|$)", line
            )
            more = re.match(r"\s{40,}([A-Z][A-Za-z .,'\"()-]+?)(?:\s{2,}|$)", line)
            if found:
                town = found[1].strip() if found[1] else town
                post, seats, name = found[2], NUMBERS.get(found[3].title(), 1), found[4]
            elif more and post:
                name = more[1]
            else:
                if re.match(r"\S", line):
                    post = ""
                continue
            if nobody.search(name) or re.search(r"\d|@", name):
                continue
            named, independent = party(label)
            feeds["NJ-34019"].append(
                {
                    "feed": key,
                    "county": "Hunterdon",
                    "contest": f"{town} {post}" if town else f"Hunterdon County {post}",
                    "seats": seats,
                    "name": cased(name),
                    "party": named,
                    "independent": independent,
                    "unexpired": False,
                }
            )

    post, seats = "", 0
    for cells in pl.read_excel(raw.cached(SOURCE, FEEDS["NJ-34011"][0]).path, has_header=False).iter_rows():
        values = [str(cell).strip() for cell in cells if cell is not None and str(cell).strip()]
        if len(values) == 1 and (found := re.search(r"- *(\d+) SEATS?$", values[0])):
            post, seats = values[0], int(found[1])
        elif post and len(cells) >= 8 and cells[4] is not None and cells[0] is not None:
            where, title = str(cells[5]).strip(), str(cells[4]).strip()
            if where == "COUNTYWIDE" and title.startswith("US "):
                continue
            named, independent = party(str(cells[6] or ""))
            feeds["NJ-34011"].append(
                {
                    "county": "Cumberland",
                    "contest": f"Cumberland County {title}" if where == "COUNTYWIDE" else f"{where} {title}",
                    "seats": seats,
                    "name": cased(" ".join(str(cell).strip() for cell in cells[:4] if cell is not None)),
                    "party": named,
                    "independent": independent,
                    "unexpired": "UNEXPIRED" in post,
                }
            )

    for key, county in (("MI-26017", "Bay"), ("MI-26091", "Lenawee"), ("MI-26021", "Berrien")):
        post, seats, unexpired = "", 0, False
        for line in text(key, "-layout").splitlines():
            if found := re.match(r"([A-Z][A-Z0-9 .,'&/-]*?) \(([A-Z][A-Z .,'&-]+?)\)\s*$", line):
                title = re.sub(
                    r"^VIL ", "VILLAGE ", re.sub(r"^TWP ", "TOWNSHIP ", re.sub(r"^SCH BRD$", "SCHOOL BOARD", found[1]))
                )
                post = f"{found[2]} {title}"
            elif found := re.match(r"(?:(\d+) Year Term|Partial Term.*?) - Vote for not more than (\d+)", line):
                seats, unexpired = int(found[2]), found[1] is None
            elif re.match(r"[A-Z][A-Z .,'&/()-]+$", line.strip()) and not line.startswith((" ", "PARTY")):
                post = ""
            elif post and (
                found := re.match(
                    r"(DEM|REP|LIB|UST|GRN|WCP|NLP|NPA|NP|IND)\s+(\d\d/\d\d/\d{4}\s+)?(\S.*?)(?:\s{2,}|$)", line
                )
            ):
                if found[2] or nobody.search(found[3]) or re.search(r"(?i)withdr|disqualif", line):
                    continue
                named, independent = party(
                    {"DEM": "Democratic", "REP": "Republican", "LIB": "Libertarian", "GRN": "Green"}.get(found[1], "")
                )
                feeds[key].append(
                    {
                        "county": county,
                        "contest": post,
                        "seats": seats,
                        "name": cased(found[3]),
                        "party": named,
                        "independent": independent or found[1] in ("NPA", "IND"),
                        "unexpired": unexpired,
                    }
                )

    for key, county in (("MI-26163", "Wayne"), ("MI-26049", "Genesee")):
        post, seats, unexpired = "", 0, False
        for line in text(key, "-layout").splitlines():
            if found := re.match(r"\s*(\S.*?) - (\S.*?) - (.*?)\((\d+)\)(?: POSITIONS?)?\s*$", line):
                post = (
                    f"{found[2]} {found[1]}"
                    if not re.search(r"\d", found[2]) and not re.search(r"JUDGE|JUSTICE|COURT", line)
                    else ""
                )
                seats, unexpired = int(found[4]), "PARTIAL" in found[3]
            elif re.match(r"\s{20,}[A-Z][A-Z0-9 .,'&/()-]+$", line):
                post = ""
            elif post and (found := re.match(r"(\S(?:.*?\S)?)\s{2,}(\S.*?)(?:\s{2,}|$)", line)):
                if nobody.search(found[2]) or re.search(
                    r"(?i)no candidate filed|withdr|disqualif|page \d+ of|^november", line
                ):
                    continue
                named, independent = party(found[1])
                feeds[key].append(
                    {
                        "county": county,
                        "contest": post,
                        "seats": seats,
                        "name": cased(found[2]),
                        "party": named,
                        "independent": independent or found[1] in ("NPA", "No Party Affiliation"),
                        "unexpired": unexpired,
                    }
                )

    counts = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
    codes = {"D": "Democratic", "DEM": "Democratic", "R": "Republican", "REP": "Republican", "LIB": "Libertarian"}
    for key, county in (("MI-26099", "Macomb"), ("MI-26147", "St. Clair")):
        town = post = ""
        seats, unexpired = 0, False
        for whole in text(key, "-layout").splitlines():
            line = whole.strip()
            if not line or re.match(r"(?i)st\. clair county, michigan$|unofficial candidate list", line):
                continue
            if found := re.match(r"(?i)(\S.*?)\s*-\s*board member\s*-?\s*\((\w+)\b([^)]*)\)(.*)", line):
                town = ""
                post = "" if re.search(r"(?i)files w|county", found[4]) else f"{found[1]} Board Member"
                seats, unexpired = counts.get(found[2].lower(), 0), "partial" in found[3].lower()
            elif found := re.match(
                r"(?i)(\S.*?)\s*\(((?:one|two|three|four|five|six|seven)\b[^)]*)\)", line
            ) or re.match(r"(\S.*?)\s{2,}((?:One|Two|Three|Four|Five|Six|Seven) .*term.*)$", line):
                head = re.sub(r"\s*-\s*$", "", found[1])
                seats, unexpired = counts[found[2].split()[0].split("-")[0].lower()], "partial" in found[2].lower()
                if re.search(
                    r"(?i)judge|justice|court|college|sc4|governor|secretary of state|attorney general|regent|trustee of|board of governors|state board|senator|representative|congress",
                    head,
                ):
                    post = ""
                elif head.startswith("County"):
                    post = f"{county} {head}"
                elif head.startswith("Ward"):
                    post = f"{town} Council {head}" if town else ""
                else:
                    post = f"{town} {head}" if town else ""
            elif (
                re.match(r"[A-Z][A-Z .'-]+?(?:\s{2,}\.+)?$", line)
                and not whole.startswith(" ")
                or (
                    "  " not in line
                    and (
                        whole.startswith(" " * 20)
                        or re.match(
                            r"(?i)((?:city|village|charter township) of \S.*?|\S.*? (?:township|city))(?:\s*\(.*\))?$",
                            line,
                        )
                    )
                )
            ):
                town = (
                    ""
                    if re.search(r"(?i)section|office|nonpartisan|judicial|partisan$", line)
                    else re.sub(r"\s*\(.*$|[\s.]+$", "", line).title()
                )
                post = ""
            elif post and (
                found := re.match(r"(\S.*?)\s+(DEM|REP|LIB|UST|GRN|GP|WC|WCP|NLP|NPA|NP|D|R)\s+\S", line)
                or re.match(r"(\S.*?)\s{2,}\S", line)
            ):
                if re.search(r"(?i)withdr|disqualif", line) or nobody.search(found[1]):
                    continue
                code = found[2] if found.re.groups > 1 else ""
                named, independent = party(codes.get(code, ""))
                feeds[key].append(
                    {
                        "county": county,
                        "contest": post,
                        "seats": seats,
                        "name": cased(re.sub(r"\s*formerly:.*", "", found[1])),
                        "party": named,
                        "independent": independent or code == "NPA",
                        "unexpired": unexpired,
                    }
                )

    read = (
        "Eaton",
        "Livingston",
        "Jackson",
        "Macomb",
        "Oakland",
        "Wayne",
        "Genesee",
        "Washtenaw",
        "St. Clair",
        "Monroe",
        "Ottawa",
    )
    post, seats, unexpired = "", 0, False
    terms: dict[str, str] = {}
    for line in text("MI-26065", "-layout").splitlines():
        if found := re.match(
            r"\s{10,}(RECALL ELECTION - )?(\S.*?) - (.*?)\s*-?\s*VOTE FOR NOT MORE THAN (\d+)(.*)", line
        ):
            post, term = found[2], found[3]
            seats, unexpired = int(found[4]), "PARTIAL" in term
            elsewhere = re.search(r"Filed with (\S.*?) County", found[5])
            if (
                found[1]
                or elsewhere
                and elsewhere[1] in read
                or re.search(r"JUDGE|COURT|REPRESENTATIVE|SENATOR|CONGRESS|COLLEGE", post)
                or not unexpired
                and terms.setdefault(post, term) != term
            ):
                post = ""
        elif re.match(r"\s{10,}\S", line) and "VOTE FOR" not in line and not re.match(r"\s{10,}\S.*\s{2,}", line):
            continue
        elif post and (
            found := re.match(
                r"\s*(\S.*?)\s{2,}.*?\s(NPA|Democratic|Republican|Libertarian|Green|U\.S\. Taxpayers|Working Class|Natural Law)\s",
                line,
            )
        ):
            if re.search(r"(?i)write-in|disqualif|withdr", line):
                continue
            named, independent = party(found[2])
            feeds["MI-26065"].append(
                {
                    "county": "Ingham",
                    "contest": post,
                    "seats": seats,
                    "name": cased(found[1]),
                    "party": named,
                    "independent": independent or found[2] == "NPA" and "TOWNSHIP" in post,
                    "unexpired": unexpired,
                }
            )

    post, seats, unexpired = "", 0, False
    for line in text("MI-26093", "-layout").splitlines():
        found = re.search(
            r"\s(Democratic|Republican|Nonpartisan|Libertarian|U\.S\. Taxpayers|Green|Working Class|Natural Law)\s{2,}(\S.*?)\s{2,}(\S.*?)(?:\s{2,}|$)",
            line,
        )
        if found is None:
            continue
        if head := re.match(r"(\S.*?)\s{2,}(?:(\S.*?)\s{2,})?(\d+)\s+(\S.*?)\s*$", line[: found.start()]):
            seats, unexpired = int(head[3]), "/" in head[4]
            post = (
                ""
                if re.search(
                    r"(?i)judge|justice|college|university|state board|governor|senator|representative|congress|secretary|attorney",
                    head[1],
                )
                else f"Livingston {head[1]} {head[2] or ''}"
                if head[1].startswith("County")
                else f"{head[2]} {re.sub(r'^Local School District ', '', head[1])}"
                if head[2]
                else ""
            )
        if not post or "(WI)" in found[3]:
            continue
        named, independent = party(found[1])
        feeds["MI-26093"].append(
            {
                "county": "Livingston",
                "contest": post.strip(),
                "seats": seats,
                "name": cased(f"{found[2]} {found[3]}"),
                "party": named,
                "independent": independent,
                "unexpired": unexpired,
            }
        )

    post, seats, unexpired = "", 0, False
    for line in text("MI-26161", "-layout").splitlines():
        if found := re.match(
            r"(?i)(\S.*?)\s*-?\s*((?:four|six|two|eight|\d) year term|partial term.*?)\s*-\s*vote for not more than (\d+)(.*)",
            line,
        ):
            seats, unexpired = int(found[3]), "partial" in found[2].lower()
            post = (
                ""
                if re.search(
                    r"(?i)files at|judge|justice|college|university|state|governor|senat|representative|congress|secretary|attorney",
                    line,
                )
                else re.sub(r"\s*-\s*", " ", found[1]).replace("Twp.", "Township")
            )
        elif (
            post
            and not line.startswith("Write-In")
            and (
                found := re.search(
                    r"\s(Democratic|Republican|Libertarian|U\.S\. Taxpayers|Green|Working Class|Natural Law|No party affiliation|Nonpartisan)\s{2,}(\S.*?)(?:\s{2,}|$)",
                    line,
                )
            )
        ):
            if re.search(r"(?i)withdr|disqualif", line):
                continue
            named, independent = party(found[1])
            feeds["MI-26161"].append(
                {
                    "county": "Washtenaw",
                    "contest": post,
                    "seats": seats,
                    "name": cased(found[2]),
                    "party": named,
                    "independent": independent or found[1] == "No party affiliation",
                    "unexpired": unexpired,
                }
            )

    for line in text("MI-26139", "-layout").splitlines():
        found = re.match(
            r"\s*(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s{2,}(Democratic|Republican|Nonpartisan|Libertarian|U\.S\. Taxpayers|Green|Natural Law|Working Class|Partisan)\s+(\S.*?)\s{2,}(\d+) positions?\s+(?:Yes|No)\s+(\S.*?)\s{2,}(\S.*?)(?:\s{2,}|$)",
            line,
        )
        if (
            found is None
            or re.match(r"United States|State of Michigan", found[1])
            or re.search(r"(?i)judge|college", line)
        ):
            continue
        named, independent = party(found[4])
        feeds["MI-26139"].append(
            {
                "county": "Ottawa",
                "contest": f"{found[1]} {found[2]}{f' {found[3]}' if found[1] == 'Ottawa County' else ''}",
                "seats": int(found[6]),
                "name": cased(f"{found[7]} {found[8]}"),
                "party": named,
                "independent": independent,
                "unexpired": "Partial" in found[5],
            }
        )

    for line in text("MI-26045", "-layout").splitlines():
        if found := re.match(
            r"(\S.*?)\s{2,}(\S.*?)\s{2,}(?:(\S+)\s{2,})?(\d+|Pa\w+ Term Ending \S+)\s{2,}(\d+)\s{2,}(\S.*?)(?:\s{2,}|$)",
            line,
        ):
            ward = re.sub(r"\D", "", found[3] or "")
            feeds["MI-26045"].append(
                {
                    "county": "Eaton",
                    "contest": f"{found[2]} {re.sub(r' - ', ' ', found[1])}{f' Ward {ward}' if ward else ''}",
                    "seats": int(found[5]),
                    "name": cased(found[6]),
                    "party": None,
                    "independent": False,
                    "unexpired": "Term" in found[4],
                    "feed": "MI-26045",
                }
            )
    for line in text("MI-26045-s", "-layout").splitlines():
        if found := re.match(
            r"([A-Z][A-Z .'-]+?)\s{2,}(\d+ Year Term|\d\d?/\d\d?/\d{4})\s+(\d+)\s+(\S.*?)(?:\s{2,}|$)", line
        ):
            feeds["MI-26045"].append(
                {
                    "county": "Eaton",
                    "contest": f"{found[1]} Board Member",
                    "seats": int(found[3]),
                    "name": cased(found[4]),
                    "party": None,
                    "independent": False,
                    "unexpired": "/" in found[2],
                    "feed": "MI-26045-s",
                }
            )
    for line in text("MI-26045-t", "-layout").splitlines():
        if found := re.match(
            r"(\S+)\s{2,}([A-Z][A-Z ]+?)\s{2,}Partial Term.*?\s(\d+)\s+([A-Z]{3})\s+(\S.*?)(?:\s{2,}|$)", line
        ):
            named, independent = party(codes.get(found[4], ""))
            feeds["MI-26045"].append(
                {
                    "county": "Eaton",
                    "contest": f"{found[2].title()} Township {found[1]}",
                    "seats": int(found[3]),
                    "name": cased(found[5]),
                    "party": named,
                    "independent": independent,
                    "unexpired": True,
                    "feed": "MI-26045-t",
                }
            )

    terms = {}
    for key in ("MI-26075", "MI-26075-v"):
        post, seats, unexpired = "", 0, False
        for line in re.sub(r"ENDING\s*\n\s*(\d)", r"ENDING \1", text(key, "-layout")).splitlines():
            if found := re.match(r"\s+(\S.*?) – (?:(.*?) – )?VOTE FOR NOT MORE THAN (\d+)", line):
                term = found[2] or ""
                post = f"{found[1]} BOARD MEMBER" if term.startswith("BOARD MEMBER") else found[1]
                seats, unexpired = int(found[3]), "PARTIAL" in term
                if "COLLEGE" in post or not unexpired and terms.setdefault(post, term) != term:
                    post = ""
            elif post and (found := re.match(r"\s{10,}(\S.*?)\s{2,}\S", line)):
                if re.search(r"(?i)write-in|withdr|disqualif", line):
                    continue
                feeds["MI-26075"].append(
                    {
                        "county": "Jackson",
                        "contest": post,
                        "seats": seats,
                        "name": cased(found[1]),
                        "party": None,
                        "independent": False,
                        "unexpired": unexpired,
                        "feed": key,
                    }
                )

    section = place = post = ""
    seats, unexpired = 0, False
    for line in text("MI-26077", "-layout").splitlines():
        found = re.search(
            r"\s(Democrat\w*|Republican|Non-?[Pp]artisan|Libertarian|Green\w*|U\.S\.\s?Taxpayers\S*|Working Class|Natural Law|No Party Affiliation)(?: Party)?\s{2,}(\S.*?)\s{2,}(\S.*?)(?:\s{2,}|$)",
            line,
        )
        if found is None:
            if re.match(r"[A-Z][A-Z ]+$", line.strip()):
                section = line.strip()
            elif line.strip() and "  " not in line.strip() and not line.startswith((" ", "Updated")):
                place = "" if "Recall" in line else line.strip()
            continue
        if head := re.match(r"(\S.*?)\s{2,}(\d+)\s+(\S.*?)\s*$", line[: found.start()]):
            seats, unexpired = int(head[2]), "Partial" in head[3]
            post = (
                f"{place} {head[1]}"
                if place and section in ("CITY", "VILLAGE", "TOWNSHIP", "LOCAL SCHOOL DISTRICT")
                else ""
            )
        if post:
            named, independent = party(found[1])
            feeds["MI-26077"].append(
                {
                    "county": "Kalamazoo",
                    "contest": post,
                    "seats": seats,
                    "name": cased(f"{found[2]} {found[3]}"),
                    "party": named,
                    "independent": independent,
                    "unexpired": unexpired,
                }
            )

    section = place = post = ""
    for line in text("MI-26005", "-layout").splitlines():
        found = re.search(
            r"\s(non-partisan|Republican|Democratic|Independent|Libertarian|Green)\s{2,}(\S.*?)(?:\s{2,}|$)",
            line,
            re.IGNORECASE,
        )
        if found is None:
            if re.match(
                r"(?i)city council positions|township partial|village positions|in county schools|out of county", line
            ):
                section, place = line.split()[0], ""
            elif line.strip() and "  " not in line.strip() and not line.startswith(" ") and not re.search(r"\d", line):
                place = line.strip()
            continue
        head = re.match(r"(\S.*?)\s{2,}(\d+)\s+(\S.*?)\s*$", line[: found.start()]) or re.match(
            r"()\s+(\d+)\s+(\S.*?)\s*$", line[: found.start()]
        )
        if head:
            seats, unexpired = int(head[2]), re.search(r"partial|\d{4}", head[3]) is not None
            post = (
                ""
                if section in ("", "Out")
                else f"{place} {head[1]}"
                if head[1] and section in ("Township", "Village")
                else head[1] or (post if section in ("Township", "Village") else place)
            )
        if post:
            named, independent = party(found[1])
            feeds["MI-26005"].append(
                {
                    "county": "Allegan",
                    "contest": re.sub(r" - ", " ", post),
                    "seats": seats,
                    "name": cased(found[2]),
                    "party": named,
                    "independent": independent,
                    "unexpired": unexpired,
                }
            )

    for row in [
        row
        for state in ("oh", "fl")
        for row in csv.DictReader(
            Path(__file__).with_name(f"local_2026_{state}.tsv").read_text().splitlines(), delimiter="\t"
        )
    ]:
        named, independent = party(row["party"])
        feeds[row["key"]].append(
            {
                "county": row["contest"].split(" County")[0],
                "contest": row["contest"],
                "seats": 1,
                "name": row["name"],
                "party": named,
                "independent": independent,
                "unexpired": row["unexpired"] == "1",
            }
        )

    for line in text("FL-12011", "-layout").splitlines():
        found = re.match(r"(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s*$", line)
        if (
            found is None
            or found[2] in ("YES", "NO", "Full Name")
            or re.match(
                r"(?i)united states|representative|governor|attorney|chief financial|commissioner of|state |supreme|district court|circuit|.*water control|.*amendment|.*referendum|.*question",
                found[1],
            )
            or not re.search(r"(?i)mayor|seat|district|group|zone", found[1])
        ):
            continue
        named, independent = party(found[3])
        feeds["FL-12011"].append(
            {
                "county": "Broward",
                "contest": re.sub(r"^School Board", "Broward County School Board", found[1].replace(",", "")),
                "seats": 1,
                "name": cased(found[2]),
                "party": named,
                "independent": independent or found[3] == "No Party Affiliation",
                "unexpired": False,
            }
        )

    post = ""
    for line in text("FL-12101", "-layout").splitlines():
        if re.match(r" \S", line) and not re.match(r" (?:Party|[A-Z]{3})\s", line):
            post = (
                f"Pasco {re.sub(r'(?<=Commissioner)s', '', line.strip().replace(',', ''))}"
                if re.match(r" (County Commissioners|School Board Member)", line)
                else ""
            )
        elif post and (
            found := re.match(
                r" (DEM|REP|NPA|LPF|IND|GRE|CPF)\s+(\S.*?), (\S.*?)\s{2,}.*\s(Qualified|Advanced to)", line
            )
        ):
            named, independent = party(
                {"DEM": "Democratic", "REP": "Republican", "LPF": "Libertarian"}.get(found[1], "")
            )
            feeds["FL-12101"].append(
                {
                    "county": "Pasco",
                    "contest": post,
                    "seats": 1,
                    "name": cased(f"{found[3]} {found[2]}"),
                    "party": named,
                    "independent": independent or found[1] in ("NPA", "IND") and "Commissioner" in post,
                    "unexpired": False,
                }
            )

    post, seats, unexpired = "", 0, False
    for line in text("MI-26055", "-layout").splitlines():
        line = line.strip()
        if line == "PROPOSALS":
            break
        if re.match(r"[A-Z]{2,}(?: [A-Z.'-]+)+", line) and re.search(
            r"Vote for|Partial Term|\(Nonpartisan\)|year term", line
        ):
            post = "" if "2 year term" in line else re.split(r"\s+[–-]\s+|\s+\(|\s+\d year term|\s+Vote for", line)[0]
            unexpired = "Partial" in line
            seats = int(found[1]) if (found := re.search(r"Vote for (\d+)", line)) else 0
        elif found := re.search(r"Vote for (\d+)", line):
            seats = int(found[1])
        elif post and (found := re.match(r"([A-Z][a-z]\S*(?: \S+)+?)(?:\s+[–-]\s+(\w+))?$", line)):
            named, independent = party(found[2])
            feeds["MI-26055"].append(
                {
                    "county": "Grand Traverse",
                    "contest": post,
                    "seats": seats,
                    "name": cased(found[1]),
                    "party": named,
                    "independent": independent,
                    "unexpired": unexpired,
                }
            )

    for key in [key for key in feeds if key[:3] in ("MI-", "NJ-", "FL-", "OH-")]:
        feeds[key] = [
            row
            for row in feeds[key]
            if not nobody.search(str(row["name"])) and not re.search(r"(?i)withdr", str(row["name"]))
        ]

    for key in [key for key in feeds if key.startswith("NJ-")]:
        tally: dict[tuple[object, object], defaultdict[object, int]] = defaultdict(lambda: defaultdict(int))
        for row in feeds[key]:
            row["contest"] = re.sub(
                r"(?i)^(\S.*? county) (?:member of the )?(?:board of )?(?:county )?commis+ioners?\b",
                r"\1 Commission",
                str(row["contest"]),
            )
            tally[(row["contest"], row["unexpired"])][row["party"] or row["independent"]] += 1
        for row in feeds[key]:
            if not row["seats"]:
                row["seats"] = max(tally[(row["contest"], row["unexpired"])].values())

    county = ""
    heading = ""
    for line in text("TX", "-layout").splitlines():
        if found := re.match(r"County {3,}(\S.*?)\s*$", line):
            county = found[1]
        elif re.match(r"     \S", line):
            heading = line.strip()
        elif (found := re.match(r" {10}(\S.*?)\s{2,}(REP|DEM|LIB|GRE|IND)\s*$", line)) and re.match(
            r"(COUNTY |DISTRICT CLERK|DISTRICT AND COUNTY CLERK|JUSTICE OF THE PEACE|JUDGE, COUNTY|JUDGE, PROBATE"
            r"|CRIMINAL DISTRICT (ATTORNEY|JUDGE)|SHERIFF|HARRIS COUNTY DEPARTMENT)",
            heading,
        ):
            feeds["TX"].append(
                {
                    "county": county,
                    "contest": f"{county} COUNTY {re.sub(r'\s*\(UNEXPIRED TERM\)', '', heading)}",
                    "seats": 0,
                    "name": found[1],
                    "party": {
                        "REP": "republican-party",
                        "DEM": "democratic-party",
                        "LIB": "libertarian-party",
                        "GRE": "green-party",
                    }.get(found[2]),
                    "independent": found[2] == "IND",
                    "unexpired": "UNEXPIRED" in heading,
                }
            )

    with connect() as conn:
        taken = {
            tail
            for (tail,) in conn.execute(
                """select substr(x.value, 12) from external_id x
                   join contest c on c.id = x.entity_id join changeset cs on cs.id = c.changeset_id
                   where x.scheme = 'us.contest' and x.value like %s and cs.pipeline <> %s""",
                [f"{DAY}:%", SOURCE],
            )
        } | {str(row["tail"]) for row in rows}
        for key, found in feeds.items():
            state = key.split("-")[0]
            prefix = next(fips for st, fips, _ in conn.execute(coverage.STATES_SQL) if st == state)
            places = dict(
                conn.execute(
                    """select u.code, n.text from reporting_unit u
                       join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                       where u.scheme_code = 'us.county' and u.code like %s""",
                    [f"{prefix}%"],
                ).fetchall()
            )
            counties = {re.sub(r"[^A-Z]", "", text.upper().rsplit(",", 1)[0]): code for code, text in places.items()}
            for row in found:
                plain = re.sub(r"[^A-Z]", "", str(row["county"]).upper())
                row["fips"] = (
                    counties.get(plain) or counties.get(f"{plain}COUNTY") or counties.get(f"{plain}PARISH", "")
                )
                if not row["fips"]:
                    inside = re.sub(r"[^A-Z]", "", str(row["contest"]).upper())
                    row["fips"] = next(
                        (
                            code
                            for name, code in sorted(counties.items(), key=lambda item: -len(item[0]))
                            if name in inside
                        ),
                        "",
                    )
            tails = resolve(
                conn, state, {(str(row["fips"]), str(row["contest"])) for row in found if row.get("tail") is None}
            )
            used: dict[str, str] = {}
            missed: set[str] = set()
            towns: dict[str, list[tuple[list[str], str, str, str, str]]] = defaultdict(list)
            if "-" in key and key != "MN-county":
                for county_code, label, owner, path, kind in conn.execute(
                    """select distinct on (g.id) g.county, g.name, x.value, s.path, g.kind from coverage_government g
                       join external_id x on x.entity_id = g.polity_id and x.scheme = 'us.polity'
                           and x.value ~ '^(place|town|school):'
                       join slug s on s.entity_id = g.polity_id
                       where g.state = %s and (g.kind in ('municipality', 'township') or g.kind = 'school' and g.state = 'MI')
                       order by g.id, x.value desc, s.path""",
                    [state],
                ):
                    stem = re.findall(r"[a-z0-9]+", label.lower().replace("&", " and "))
                    towns[county_code].append(
                        (
                            [
                                word
                                for word in stem
                                if word
                                not in (
                                    ("public", "schools", "school", "district", "community", "area", "consolidated")
                                    if kind == "school"
                                    else ("township", "borough", "city", "town", "village", "charter")
                                )
                            ],
                            "school"
                            if kind == "school"
                            else next(
                                (
                                    word
                                    for word in reversed(stem)
                                    if word in ("township", "borough", "city", "town", "village")
                                ),
                                "township" if kind == "township" else "",
                            ),
                            label,
                            owner,
                            path,
                        )
                    )
            for row in found:
                template, tail = tails.get((str(row["fips"]), str(row["contest"])), (None, None))
                if row.get("tail") is not None:
                    template = tail = str(row["tail"]) if row["tail"] in exact else None
                place = places.get(str(row["fips"]), "").rsplit(",", 1)[0]
                tokens = re.findall(
                    r"[a-z0-9]+",
                    re.sub(
                        r"\bcharter township\b",
                        "township",
                        re.sub(r"(?i)\btwp\b", "township", str(row["contest"]).lower().replace("&", " and ")),
                    ),
                )
                fits = []
                for stem, sort, label, owner, path in towns.get(str(row["fips"]), []):
                    for start in (
                        ([], *([word, "of"] for word in ("city", "village", "town", "township", "borough")))
                        if sort != "school"
                        else ([],)
                    ):
                        lead = start + stem
                        if stem and tokens[: len(lead)] == lead:
                            rest = tokens[len(lead) :]
                            said = (
                                rest[0]
                                if not start and rest and rest[0] in ("township", "borough", "city", "town", "village")
                                else start[0]
                                if start
                                else ""
                            )
                            if (
                                said
                                and (sort and said != sort or not sort and said == "township")
                                or (sort == "school" or state == "MI")
                                and (sort == "school")
                                != bool(re.search(r"\b(school|schools|sch|education)\b", " ".join(rest)))
                            ):
                                continue
                            fits.append(
                                (len(stem), bool(said), rest[1:] if said and not start else rest, label, owner, path)
                            )
                fits = [fit for fit in fits if fit[0] == max(f[0] for f in fits)]
                if len(fits) > 1:
                    fits = [fit for fit in fits if fit[1]] if len({fit[4] for fit in fits if fit[1]}) == 1 else fits
                if (
                    template is None
                    and state in ("NJ", "MI", "FL")
                    and len({fit[4] for fit in fits}) == 1
                    and (
                        fits[0][4].startswith("school:")
                        or not re.search(
                            r"\b(school|unified|college|district(?! \d)|area|primary|nominating|special)\b",
                            " ".join(fits[0][2]),
                        )
                    )
                ):
                    _, _, rest, label, owner, path = fits[0]
                    post = " ".join(
                        re.sub(r"\b(member|members|of the|partial term|full term)\b", " ", " ".join(rest)).split()
                    )
                    role = (
                        "board-of-education"
                        if owner.startswith("school:")
                        else next(
                            (
                                role
                                for pattern, role in (
                                    (r"^(?:township |village |city )?(supervisor|clerk|treasurer)$", "office"),
                                    (r"^village president$|^president$", "president"),
                                    (r"(?<!president of )(?<!deputy )\bmayor\b", "mayor"),
                                    (r"\bboard of education\b|\bschool board\b|\bboe\b", "board-of-education"),
                                    (r"\bcommittee\b", "committee"),
                                    (r"\bcouncil\b|\bcouncilm[ae]n\b|\bcouncilmember\b|\balderm[ae]n\b", "council"),
                                    (r"\bcommission(?:er|ers)?\b", "commission"),
                                    (r"\btrustees?\b", "board-of-trustees"),
                                )
                                if re.search(pattern, post)
                                and (
                                    role in ("office", "president")
                                    or not re.search(r"president|clerk|tax|collector|fire|library", post)
                                )
                            ),
                            None,
                        )
                    )
                    if role == "office":
                        role = post.split()[-1]
                    ward = re.search(
                        r"\bward (\d+)\b|\b(\d+)(?:st|nd|rd|th)? ward\b|\bw(\d+)\b|\b(north|south|east|west|central) ward\b",
                        post,
                    )
                    seat = (
                        (f"Ward {ward[1] or ward[2] or ward[3]}" if ward[4] is None else f"{ward[4].title()} Ward")
                        if ward and role in ("council", "commission")
                        else f"{other[1].title()} {other[2]}"
                        if (other := re.search(r"\b(seat|district|group|place) (\d+)\b", post))
                        and role in ("council", "commission")
                        else "At-Large"
                        if role in ("council", "commission") and re.search(r"\bat large\b", post)
                        else ""
                    )
                    if role is not None:
                        code = owner.split(":", 1)[1]
                        template, tail = "", f"{state}:{code}:{role}" + (f":{slug(seat)}" if seat else "")
                        minted[tail] = (
                            {
                                "mayor": "mayoral",
                                "board-of-education": "Board of Education",
                                "committee": "Committee",
                                "council": "Council",
                                "commission": "Commission",
                                "board-of-trustees": "Board of Trustees",
                                "supervisor": "Supervisor",
                                "clerk": "Clerk",
                                "treasurer": "Treasurer",
                                "president": "President",
                            }[role]
                            + (f", {seat}" if seat else ""),
                            label,
                            owner,
                            path,
                        )
                if (
                    template is None
                    and place
                    and int(str(row["seats"]) or 0) <= 1
                    and not LOCAL_BODY.search(str(row["contest"]))
                    and (
                        "-" not in key
                        or key == "MN-county"
                        or re.match(rf"(?i){re.escape(place)}\b", str(row["contest"])) is not None
                    )
                ):
                    title = re.sub(
                        rf"(?i)^\s*(?:{re.escape(place)}|{re.escape(place.removesuffix(' County'))}(?:\s+county)?)\s+",
                        "",
                        re.sub(r"\s+", " ", str(row["contest"])).strip(),
                    )
                    title = re.sub(
                        r"\b(Of|The|And|At)\b",
                        lambda found: found[1].lower(),
                        " ".join(
                            word
                            if word.isupper() and len(word) <= 3 and word != "AND" and not title.isupper()
                            else word.capitalize()
                            for word in re.sub(r"(?i)\bby commissioner district district\b", "District", title).split()
                        ),
                    )
                    template, tail = "", f"{state}:{row['fips']}:{slug(title)}"
                    minted[tail] = (title, place, "", "")
                if (
                    tail is not None
                    and used.setdefault(tail, str(row["contest"])) != row["contest"]
                    and row["unexpired"]
                ):
                    tail = f"{tail}:unexpired"
                if (
                    tail is not None
                    and template == ""
                    and tail.removesuffix(":unexpired") in minted
                    and tail.endswith(":unexpired")
                ):
                    missed.add(f"{row['county']}: {row['contest']} unexpired")
                    continue
                if (
                    template is None
                    or tail is None
                    or tail in taken
                    or used.setdefault(tail, str(row["contest"])) != row["contest"]
                ):
                    missed.add(f"{row['county']}: {row['contest']}")
                    continue
                templated[tail] = template
                add(
                    key,
                    tail,
                    f"{row['contest']}{' unexpired' if row['unexpired'] else ''}",
                    int(str(row["seats"])),
                    str(row["name"]),
                    None if row["party"] is None else str(row["party"]),
                    bool(row["independent"]),
                    str(row.get("feed") or key),
                )
            taken |= set(used)
            print(f"{key}: {len(used)} contests matched, {len(missed)} not matched")
            (PARSED / f"unmatched_{key}.txt").parent.mkdir(parents=True, exist_ok=True)
            (PARSED / f"unmatched_{key}.txt").write_text("\n".join(sorted(missed)))

    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.DataFrame(rows)
        .with_columns(person=pl.col("name").map_elements(slug, return_dtype=pl.String))
        .unique(["tail", "person"], keep="first", maintain_order=True)
        .with_columns(
            template=pl.col("tail").replace(templated),
            title=pl.col("tail").replace_strict({tail: value[0] for tail, value in minted.items()}, default=None),
            place=pl.col("tail").replace_strict({tail: value[1] for tail, value in minted.items()}, default=None),
            owner=pl.col("tail").replace_strict({tail: value[2] for tail, value in minted.items()}, default=None),
            home=pl.col("tail").replace_strict({tail: value[3] for tail, value in minted.items()}, default=None),
        )
    )
    with connect() as conn:
        held = {
            frozenset(slug(label) for label in labels if label): tail
            for tail, labels in conn.execute(
                """select substr(x.value, 12), array_agg(distinct o.ballot_label) from external_id x
                   join contest c on c.id = x.entity_id join changeset cs on cs.id = c.changeset_id
                   join entity t on t.id = c.id and t.retired_by is null
                   join contest_option o on o.contest_id = c.id
                   where x.scheme = 'us.contest' and x.value like %s and cs.pipeline = %s
                   group by 1""",
                [f"{DAY}:%", SOURCE],
            )
        }
    kept = set(frame["tail"])
    lost = {
        (":".join(tail.split(":")[:2]), person)
        for persons, tail in held.items()
        if tail not in kept
        for person in persons
    }
    moved = {
        tail
        for (tail,), group in frame.group_by(["tail"])
        if tail not in held.values()
        and not lost.isdisjoint((":".join(str(tail).split(":")[:2]), person) for person in group["person"])
    }
    print(f"{len(moved)} contests left on the tail this pipeline already published")
    frame = frame.filter(~pl.col("tail").is_in(moved))
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.group_by("fips").agg(pl.col("tail").n_unique().alias("contests"), pl.len().alias("candidates")))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    tails = sorted(set(frame["template"]))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Local contests on the 2026-11-03 ballot, with candidates from official candidate lists and sample ballots: statewide candidate files (North Carolina), each contest matched to the same office's last contest in the archive; Los Angeles city runoffs, Cook County offices, Harris and Dallas county offices, and Miami-Dade runoffs, the San Diego and Orange county supervisor races, Maricopa County's clerk, justices of the peace, and constables, and the New York City Council District 3 special",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        templates: dict[str, tuple] = {}
        for row in conn.execute(
            """select distinct on (x.value) regexp_replace(x.value, '^[0-9-]+:', ''), co.id, co.kind, co.office_id, co.body_id,
                      co.district_area_id, co.seat_id, co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, el.date_from, el.polity_id, n.text, s.path, en.text, es.path,
                      regexp_replace(ex.value, '^[0-9-]+:', '')
               from external_id x join contest co on co.id = x.entity_id join entity e on e.id = co.id and e.retired_by is null
               join election el on el.id = co.election_id
               left join name n on n.entity_id = co.id and n.is_preferred and n.lang = 'en'
               left join slug s on s.entity_id = co.id and s.is_current
               left join name en on en.entity_id = el.id and en.is_preferred and en.lang = 'en'
               left join slug es on es.entity_id = el.id and es.is_current
               join external_id ex on ex.entity_id = el.id and ex.scheme = 'us.election'
               where x.scheme = 'us.contest' and regexp_replace(x.value, '^[0-9-]+:', '') = any(%s) and el.date_from < %s
               order by x.value, el.date_from desc""",
            [tails, DAY],
        ).fetchall():
            if row[0] not in templates or row[12] > templates[row[0]][12]:
                templates[row[0]] = row
        known: dict[str, list[tuple[object, str]]] = {}
        for tail, person, text in conn.execute(
            """select regexp_replace(x.value, '^[0-9-]+:', ''), ca.person_id, coalesce(ca.ballot_name, o.ballot_label, pn.text)
               from external_id x join contest_option o on o.contest_id = x.entity_id
               join candidacy ca on ca.option_id = o.id join entity e on e.id = ca.id and e.retired_by is null
               left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
               where x.scheme = 'us.contest' and regexp_replace(x.value, '^[0-9-]+:', '') = any(%s)""",
            [tails],
        ).fetchall():
            known.setdefault(tail, []).append((person, text))

        sources = {}
        elections = {}
        counts = {"contests": 0, "chained": 0, "created": 0, "candidates": 0, "matched": 0, "no template": 0}
        created: set[str] = set()
        made: set[object] = set()
        homes: dict[str, str] = {}
        for (fips, tail, template), group in frame.group_by(["fips", "tail", "template"], maintain_order=True):
            if template == "" and group["owner"][0]:
                title, place, owner, home = (str(group[column][0]) for column in ("title", "place", "owner", "home"))
                st, code, role = tail.split(":")[:3]
                polity = cs.key("us.polity", owner)
                body = role not in ("mayor", "supervisor", "clerk", "treasurer", "president")
                holder = cs.key("us.body" if body else "us.office", f"{st}:{code}:{role}")
                if holder in cs.minted and holder not in made:
                    made.add(holder)
                    if body:
                        cs.add("body", id=holder, polity_id=polity, kind="council")
                        name(holder, f"{place} {title.split(',')[0]}")
                    else:
                        cs.add("office", id=holder, polity_id=polity, kind="mayor" if role == "mayor" else "other")
                        name(holder, f"Mayor of {place}" if role == "mayor" else f"{place} {title}")
                whole = conn.execute(
                    "select t.area_id from polity_territory t join polity p on p.id = t.polity_id"
                    " join external_id x on x.entity_id = p.id and x.scheme = 'us.polity' and x.value = %s"
                    " order by t.valid_from desc nulls last limit 1",
                    [owner],
                ).fetchone()
                if whole is None:
                    counts["no template"] += 1
                    print(f"no territory: {tail} {owner}")
                    continue
                area = whole[0]
                if ", " in title:
                    seat = title.split(", ", 1)[1]
                    district = f"{st}:{code}:{role}:{slug(seat)}"
                    area = cs.area("us.local_district", district)
                    if area in cs.minted and area not in made:
                        made.add(area)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole[0], valid_from=None)
                        name(area, f"{place} {title.split(',')[0]}, {seat}")
                templates[tail] = (
                    tail,
                    None,
                    "body_seats" if body else "office",
                    None if body else holder,
                    holder if body else None,
                    area,
                    None,
                    None,
                    1,
                    "full",
                    cs.key("us.electoral_system", "fptp"),
                    "counted",
                    DAY,
                    polity,
                    f"2026 {place} {title} election",
                    f"{home}/{DAY}-general/{role}" + (f"-{slug(title.split(', ', 1)[1])}" if ", " in title else ""),
                    f"2026 {place} general election",
                    f"{home}/{DAY}-general",
                    f"{st}:{code}",
                )
                created.add(tail)
                known[tail] = conn.execute(
                    """select ca.person_id, coalesce(ca.ballot_name, o.ballot_label, pn.text)
                       from contest co join contest_option o on o.contest_id = co.id
                       join candidacy ca on ca.option_id = o.id join entity e on e.id = ca.id and e.retired_by is null
                       left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
                       where co.office_id = %s or co.body_id = %s""",
                    [holder, holder],
                ).fetchall()
            elif template == "":
                title, place = str(group["title"][0]), str(group["place"][0])
                st, county = tail.split(":")[:2]
                polity = cs.key("us.polity", f"county:{county}")
                office = cs.key("us.office", tail)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    name(office, f"{place} {title.removeprefix('County ')}")
                templates[tail] = (
                    tail,
                    None,
                    "office",
                    office,
                    None,
                    None,
                    None,
                    None,
                    1,
                    "full",
                    cs.key("us.electoral_system", "fptp"),
                    "counted",
                    DAY,
                    polity,
                    f"2026 {place} {title} election",
                    f"us/{st.lower()}/{slug(place)}/{DAY}-general/{slug(title)}",
                    f"2026 {place} general election",
                    f"us/{st.lower()}/{slug(place)}/{DAY}-general",
                    f"{st}:{county}",
                )
                created.add(tail)
                known[tail] = []
            elif template != tail and template in templates:
                base = list(templates[template])
                if tail.endswith(":unexpired"):
                    base[15] = f"{base[15]}-unexpired"
                    known[tail] = known.get(template, [])
                else:
                    old, new = template.rsplit("-", 1)[1], tail.rsplit("-", 1)[1]
                    base[14] = re.sub(
                        rf"\b{re.escape(old)}$", new.upper() if len(new) == 1 else new, base[14], flags=re.IGNORECASE
                    )
                    base[15] = re.sub(rf"-{re.escape(old)}$", f"-{new}", base[15])
                    if not base[14].endswith(new.upper() if len(new) == 1 else new) or not base[15].endswith(f"-{new}"):
                        counts["no template"] += 1
                        print(f"no sibling name: {tail} from {template}: {base[14]} {base[15]}")
                        continue
                    base[1] = None
                templates[tail] = tuple(base)
            if tail not in templates:
                county = re.fullmatch(r"CA:(\d{5}):board-of-supervisors:(\d+)", tail)
                if county is None:
                    counts["no template"] += 1
                    print(f"no template: {tail}")
                    continue
                label = COUNTIES[county[1]]
                polity = cs.key("us.polity", f"county:{county[1]}")
                body = cs.key("us.body", f"CA:{county[1]}:board-of-supervisors")
                if body in cs.minted:
                    cs.add("body", id=body, polity_id=polity, kind="council")
                    name(body, f"{label} Board of Supervisors")
                code = f"06:{county[1]}:board-of-supervisors:district-{county[2]}"
                area = cs.area("us.local_district", code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add(
                        "area_parent",
                        area_id=area,
                        parent_id=next(
                            iter(
                                conn.execute(
                                    "select area_id from reporting_unit where scheme_code = 'us.county' and code = %s",
                                    [county[1]],
                                )
                            )
                        )[0],
                    )
                    name(area, f"{label} supervisorial district {county[2]}")
                templates[tail] = (
                    tail,
                    None,
                    "body_seats",
                    None,
                    body,
                    area,
                    None,
                    None,
                    1,
                    "full",
                    cs.key("us.electoral_system", "fptp"),
                    "counted",
                    DAY,
                    polity,
                    f"2026 {label} Board of Supervisors, supervisorial district {county[2]} election",
                    f"us/ca/{slug(label)}/{DAY}-general/board-of-supervisors-{county[2]}",
                    f"2026 {label} general election",
                    f"us/ca/{slug(label)}/{DAY}-general",
                    f"CA:{county[1]}",
                )
                created.add(tail)
                known[tail] = conn.execute(
                    """select ca.person_id, coalesce(ca.ballot_name, o.ballot_label, pn.text)
                       from contest co join contest_option o on o.contest_id = co.id
                       join candidacy ca on ca.option_id = o.id join entity e on e.id = ca.id and e.retired_by is null
                       left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
                       where co.district_area_id = %s""",
                    [area],
                ).fetchall()
            (
                _,
                previous,
                kind,
                office_id,
                body_id,
                area,
                seat,
                plan,
                magnitude,
                term,
                system,
                result_kind,
                held_on,
                polity,
                title,
                path,
                election_title,
                election_path,
                scope,
            ) = templates[tail]
            state = fips.split("-")[0] if fips in FEEDS else PUBLISHERS[fips][0]
            for feed in group["feed"].unique(maintain_order=True):
                if feed not in sources:
                    _, publisher, source_title = (
                        (feed.split("-")[0], *FEEDS[feed][1:]) if feed in FEEDS else PUBLISHERS[feed]
                    )
                    source = cs.source(publisher, source_title)
                    if source not in sources.values():
                        if source in cs.minted:
                            cs.add(
                                "source",
                                id=source,
                                kind="official_body",
                                publisher=publisher,
                                title=source_title,
                                url=FEEDS[feed][0] if feed in FEEDS else FILES[feed],
                                license="Public record",
                            )
                        cs.artifact(raw.cached(SOURCE, FEEDS[feed][0] if feed in FEEDS else FILES[feed]), source)
                    sources[feed] = source
            home = re.sub(r"/[0-9-]+-\w+$", f"/{DAY}-general", election_path)
            if scope not in elections and home in homes:
                elections[scope] = elections[homes[home]]
            if scope not in elections:
                held = conn.execute(
                    "select x.value from slug s join external_id x on x.entity_id = s.entity_id and x.scheme = 'us.election'"
                    " where s.path = %s",
                    [home],
                ).fetchone()
                if held is not None and held[0] != f"{DAY}:{scope}":
                    elections[scope] = elections.get(held[0][11:]) or cs.key("us.election", held[0])
                    homes[home] = scope
            if scope not in elections:
                homes[home] = scope
                elections[scope] = cs.key("us.election", f"{DAY}:{scope}")
                cs.add(
                    "election",
                    id=elections[scope],
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{DAY}:{state}"),
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    status="scheduled",
                )
                name(
                    elections[scope],
                    re.sub(r"^\d{4} (.+?) \w+ elections?$", r"2026 \1 general election", election_title),
                )
                cs.add(
                    "slug", path=re.sub(r"/[0-9-]+-\w+$", f"/{DAY}-general", election_path), entity_id=elections[scope]
                )
            chained = previous is not None and held_on.year == DAY.year
            key = f"{DAY}:{tail}"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[scope],
                kind=kind,
                office_id=office_id,
                body_id=body_id,
                district_area_id=area,
                seat_id=seat,
                boundary_set_id=plan,
                magnitude=int(group["seats"][0]) if kind == "body_seats" and group["seats"][0] else magnitude,
                term="remainder" if group["unexpired"][0] else term,
                round_number=2 if chained else 1,
                previous_round_id=previous if chained else None,
                electoral_system_id=system,
                default_result_kind=result_kind,
            )
            text = re.sub(r"^\d{4} ", "2026 ", title)
            if group["unexpired"][0] and term == "full":
                text = text.replace(" election", " special election", 1)
            if chained:
                text = text.replace(
                    " election", " general municipal election" if fips == "0644000" else " runoff election", 1
                )
            cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=re.sub(r"/[0-9-]+-\w+/", f"/{DAY}-general/", path), entity_id=contest)
            counts["contests"] += 1
            if tail in created:
                counts["created"] += 1
                cs.add("citation", entity_id=contest, source_id=sources[str(group["feed"][0])], locator=None)
            counts["chained"] += chained
            for row in group.iter_rows(named=True):
                tokens = [
                    token
                    for token in slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", row["name"])).split("-")
                    if token not in ("jr", "sr", "ii", "iii", "iv")
                ]
                pool = {
                    person
                    for person, held_text in known.get(tail, [])
                    if held_text
                    and (
                        held := [
                            token
                            for token in slug(held_text).split("-")
                            if token not in ("jr", "sr", "ii", "iii", "iv")
                        ]
                    )
                    and held[-1] == tokens[-1]
                    and held[0][:1] == tokens[0][:1]
                    and (
                        fips not in FEEDS
                        or min(len(held[0]), len(tokens[0])) == 1
                        or held[0] in tokens
                        or tokens[0] in held
                    )
                }
                if len(pool) == 1:
                    person = next(iter(pool))
                    counts["matched"] += 1
                else:
                    person = cs.key(f"{SOURCE}.person", f"{tail}:{slug(row['name'])}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, row["name"])
                party = cs.key("us.party", row["party"]) if row["party"] else None
                option = cs.key("us.option", f"{key}:{slug(row['name'])}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=row["name"],
                )
                candidacy = cs.key("us.candidacy", f"{key}:{slug(row['name'])}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    is_independent=bool(row["independent"]),
                )
                cs.add("citation", entity_id=candidacy, source_id=sources[row["feed"]], locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
