import csv
import html
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import coverage, coverage_tables, raw
from ea.changeset import Changeset, slug
from ea.sources.us import cog, govmint, mn_local
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect

SOURCE = "us.local_2026"
VERSION = "1"
DAY = date(2026, 11, 3)
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_place_national.zip"
COUSUB_GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_cousubs_national.zip"
BALLOTS = "https://web.sos.ky.gov/ballots/{}%202026G.pdf"
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
    "ME": (
        "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/2026%20General%20Candidate%20List%20-%20FINAL.xlsx",
        "Maine Secretary of State",
        "2026 General Candidate List",
    ),
    "VT": (
        "https://outside.vermont.gov/dept/sos/Elections_Division/election_info_resources/candidates/2026_general_election_qualified_candidates.xlsx",
        "Vermont Secretary of State",
        "2026 General Election Qualified Candidates",
    ),
    "OK": (
        "https://hosting.okelections.gov/electionlist.html",
        "Oklahoma State Election Board",
        "November 2026 List of Elections",
    ),
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
    "KY": (
        "https://web.sos.ky.gov/CandidateFilings/countyfilings.aspx",
        "Kentucky Secretary of State",
        "Candidate Filings with the County Clerk: Excel Spreadsheet of All County Candidates, 2026 General Election",
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
    "UT-49049": (
        "https://www.utahcounty.gov/cms/uploads/Utah_County_2026_General_Election_Candidate_Certification_4e705c1719.pdf",
        "Utah County Clerk",
        "2026 General Election Candidate Certification",
    ),
    "UT-49011": (
        "https://www.daviscountyutah.gov/Sitefinity/WebsiteTemplates/ClerkAuditor/2026%20General%20Notice.pdf",
        "Davis County Clerk",
        "Certification of Candidates and Ballot Propositions for the Regular General Election, November 3, 2026",
    ),
    "AZ-04021": (
        "https://www.pinal.gov/DocumentCenter/View/32952",
        "Pinal County Elections",
        "2026 General Election County and Local Candidate List",
    ),
    "MO-29095": (
        "https://www.jcebmo.org/election-information/on-the-ballot/",
        "Jackson County Board of Election Commissioners",
        "Notice of General Election and Sample Ballot, November 3, 2026",
    ),
    "MO-29095-k": (
        "https://www.kceb.org/elections/ballot/",
        "Kansas City Board of Election Commissioners",
        "Sample Ballot, General Election, November 3, 2026",
    ),
    "IL-17097": (
        "https://www.lakecountyil.gov/DocumentCenter/View/13059",
        "Lake County Clerk",
        "Candidate List, General Election 11/3/2026",
    ),
    "IL-17197": (
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/will-il.connect.clarityelections.com/Elections/2026GeneralElection/Candidates/Countywide.pdf",
        "Will County Clerk",
        "General Election, November 3, 2026: Candidate List, Countywide Offices",
    ),
    "CO-08005": (
        "https://files.arapahoeco.gov/Clerk%20and%20Recorder/ArapahoeVotes/Documents-Articles/2026%20Sample%20Ballots/2026%20Arapahoe%20County%20General%20Election%20Sample%20Ballot%20-%20English.pdf",
        "Arapahoe County Clerk and Recorder",
        "Sample General Election Ballot, Tuesday, November 3, 2026",
    ),
    "GA": (
        "https://results.sos.ga.gov/results/public/api/elections/Georgia/GeneralPrimary51926/data#counties",
        "Georgia Secretary of State",
        "May 19, 2026 General Primary and June 16, 2026 General Primary Runoff: Official Results by County",
    ),
    "IL-17089": (
        "https://applications.kanecountyil.gov/ElectionWebsite/en-US/Candidates/List/31/NP",
        "Kane County Clerk",
        "Candidates for the 2026 General Election",
    ),
    "IL-17143": (
        "https://www.peoriaelections.gov/DocumentCenter/View/1454/2026-General-Election---Sample-Ballot",
        "Peoria County Board of Election Commissioners",
        "Sample Ballot, 2026 General Election",
    ),
    "IL-17113": (
        "https://www.mcleancountyil.gov/DocumentCenter/View/32912/2026-General-McLean-County-Specimen-Ballot",
        "McLean County Clerk",
        "Specimen Ballot, 2026 General Election",
    ),
    "IL-17163": (
        "https://www.stclaircountyil.gov/departments/county-clerk",
        "St. Clair County Clerk",
        "Specimen Ballot, 2026 General Election",
    ),
    "IL-17019": (
        "https://www.champaigncountyil.gov/",
        "Champaign County Clerk",
        "Publication Ballot, 2026 General Election",
    ),
    "IL-17093": (
        "https://www.kendallcountyil.gov/",
        "Kendall County Clerk",
        "County Candidates, 2026 General Election",
    ),
    "IL-17119": (
        "https://www.madisoncountyil.gov/document_center/CountyClerk/2026%20Election/Candidate%20list.pdf",
        "Madison County Clerk",
        "2026 General Election: Candidate List (9/30/2026)",
    ),
    "IL-17201": (
        "https://wincoil.gov/media/attachments/2026/09/25/november-2026-candidate-list.pdf",
        "Winnebago County Clerk",
        "2026 General Election: List of Candidates (9/25/2026)",
    ),
    "IL-17167": (
        "https://sangamondepartments.blob.core.windows.net/dnnfiles/Clerk/Elections/Candidates/2026%20Election/11-3-26%20Co%20Wide%20and%20Co%20Board%20Candidate%20Listing.pdf",
        "Sangamon County Clerk",
        "Sangamon County Candidates for November 3, 2026 General Election",
    ),
    "MO-29183": (
        "https://www.sccmo.org/DocumentCenter/View/28912/Candidates-SCCMO-only-for-Nov-2026---20260826",
        "St. Charles County Election Authority",
        "Filed Candidates for 2026 General Election, as of 08/26/2026",
    ),
    "MO-29099": (
        "https://www.jeffcomo.gov/DocumentCenter/View/22933/November-3-2026-General-Election-County-Wide-Sample-Ballot-PDF",
        "Jefferson County Clerk",
        "Sample Ballot, General Election, Tuesday, November 3, 2026",
    ),
    "MO-29077": (
        "https://vote.greenecountymo.gov/wp-content/uploads/2026/09/Candidates_and_Ballot_Issues_Composite_November_2026.pdf",
        "Greene County Clerk",
        "November 3, 2026 General Election: Candidates and Ballot Issues",
    ),
    "MO-29189": (
        "https://extcontent.stlouisco.com/BOE/FIO/NovemberFIO.pdf",
        "St. Louis County Board of Elections",
        "November 3, 2026 General Election: Ballot Content Report (09/16/2026)",
    ),
    "MO-29510": (
        "https://www.stlouis-mo.gov/government/departments/board-election-commissioners/documents/upload/Nov26-All-Races-Sample-Ballot.pdf",
        "St. Louis City Board of Election Commissioners",
        "November 3, 2026 General Election: All Races Sample Ballot",
    ),
    "OH-39153": (
        "https://boe.ohio.gov/summit/c/upload/Election_CandidateList.pdf",
        "Summit County Board of Elections",
        "General Election, November 3, 2026: Candidate List (subject to withdrawal, 10/16/26)",
    ),
    "OH-39095": (
        "https://boe.ohio.gov/lucas/#26NGCandidatesList.pdf",
        "Lucas County Board of Elections",
        "General Election, November 3, 2026: Candidates List (09/11/26)",
    ),
    "OH-39113": (
        "https://montgomery.boe.ohio.gov/#10.16.2026-Candidate-Petition-Report.pdf",
        "Montgomery County Board of Elections",
        "2026 Montgomery County Candidate Report (October 16, 2026)",
    ),
    "IL-17111": (
        "https://www.mchenrycountyil.gov/county-government/departments-j-z/county-clerk/elections#20260824-candidatelist.pdf",
        "McHenry County Clerk",
        "Certified Candidate List, General, 11/3/2026",
    ),
    "GA-13121": (
        "https://www.fultoncountyga.gov/-/media/2026-Election/November-3-Election/Sample-Ballot--Consolidated--Nov-32026-General--Special-Election--1082026.pdf",
        "Fulton County Department of Registration and Elections",
        "November 3, 2026 General Special Election: Consolidated Sample Ballot",
    ),
    "GA-13135": (
        "https://www.gwinnettcounty.com/documents/d/gwinnett-county/nov-3-general-election-special-election-consolidated-sample-ballot-english-asian-lang",
        "Gwinnett County Voter Registrations and Elections",
        "November 3 General Election and Special Election: Consolidated Sample Ballot",
    ),
    "GA-13067": (
        "https://assets.cobbcounty.gov/files/2026-09/SAMPLE%20BALLOT%20DRAFT%20v4%20WEB.pdf",
        "Cobb County Elections and Registration",
        "November 3, 2026 General and Special Election: Sample Ballot (draft v4)",
    ),
    "GA-13089": (
        "https://dekalbcountyga.gov/sites/default/files/2026-10/NOV_2026%20_GENERAL.SPECIAL%20Sample-booklet-am.pdf",
        "DeKalb County Voter Registration and Elections",
        "Sample Ballot, General / Special Election, November 3, 2026",
    ),
    "GA-13063": (
        "https://www.claytoncountyga.gov/wp-content/uploads/2026/09/11.3.26-Composite-Sample-Ballot-Final.pdf",
        "Clayton County Elections and Registration",
        "November 3, 2026 Composite Sample Ballot",
    ),
    "GA-13223": (
        "https://www.paulding.gov/DocumentCenter/View/19236/Paulding-County-Composite-Sample-Ballot-November-3-2026-General-Election",
        "Paulding County Elections and Registration",
        "Composite Sample Ballot, November 3, 2026 General Election",
    ),
    "GA-13153": (
        "https://www.houstoncountyga.gov/skins/userfiles/files/November%202026%20Sample%20Ballot.pdf",
        "Houston County Board of Elections",
        "November 2026 Sample Ballot",
    ),
    "GA-13073": (
        "https://www.columbiacountyga.gov/DocumentCenter/View/10721/OFFICIAL-COLUMBIA-COUNTY-COMPOSITE--SAMPLE-BALLOT--NOVEMBER-3-2026-GENERAL--SPECIAL-ELECTION",
        "Columbia County Board of Elections",
        "Official Composite Sample Ballot, November 3, 2026 General and Special Election",
    ),
    "GA-13245": (
        "https://www.augustaga.gov/DocumentCenter/View/21864/Composite-Sample-Ballot-Nov-2026",
        "Augusta-Richmond County Board of Elections",
        "Composite Sample Ballot, November 2026",
    ),
    "AZ-04013-s": (
        "https://mcss.knack.com/school-districts#2026-filed-candidates/",
        "Maricopa County School Superintendent",
        "Elections and Districts Data Dashboard: 2026 Filed Candidates and 2026 Offices To Be Elected",
    ),
    "AL": (
        "https://www.sos.alabama.gov/alabama-votes/2026-general-election-sample-ballots",
        "Alabama Secretary of State",
        "2026 General Election Sample Ballots, November 3, 2026 (61 county ballots)",
    ),
    "CO": (
        "https://results.enr.clarityelections.com/CO/126592/web.377440/#/summary#counties",
        "Colorado Secretary of State",
        "June 30, 2026 Primary Election: Official Results by County",
    ),
    "IA": (
        "https://electionresults.iowa.gov/IA/126082/web.377216/#/summary#counties",
        "Iowa Secretary of State",
        "June 2, 2026 Primary Election: Official Results by County",
    ),
    "WI": (
        "https://elections.wi.gov/2026#File All Contests and Candidates_November 3, 2026.xlsx",
        "Wisconsin Elections Commission",
        "All Contests and Candidates, November 3, 2026 General Election",
    ),
    "ID": (
        "https://api-run.voteidaho.gov/api/FiledCandidates/SearchCandidates#1e38d17c-a176-47b8-91d1-368ba38ccdec",
        "Idaho Secretary of State",
        "Filed Candidates: 11/03/2026 General Election, Final List",
    ),
    "WA": (
        "https://voter.votewa.gov/CandidateList.aspx?e=899#export-by-county",
        "Washington Secretary of State",
        "Candidate List: General 2026 (11/03/2026), CSV export for each county",
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
        "https://www.waynecountymi.gov/Government/Elected-Officials/Clerk/Elections/Election-Results-Candidates/November-3rd-2026-General-Election",
        "Wayne County Clerk",
        "November 3, 2026 General Election: Official Candidate List",
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
    "MI-26081": (
        "https://www.kentcountymi.gov/DocumentCenter/View/8870",
        "Kent County Clerk",
        "Official Candidates and Proposals List, November 3, 2026 General Election",
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
    "FL-12057": (
        "https://www.votehillsborough.gov/DocumentCenter/View/2615/2026-General-Election-All-Races-Sample-Ballot-PDF",
        "Hillsborough County Supervisor of Elections",
        "Official General Election Ballot, November 3, 2026: All Races Sample Ballot",
    ),
    "FL-12031": (
        "https://www.duvalelections.gov/DocumentCenter/View/1644/2026-General-All-Races",
        "Duval County Supervisor of Elections",
        "General Election, November 3, 2026: All-Races Sample Ballot",
    ),
    "FL-12071": (
        "https://www.lee.vote/243/Sample-Ballots",
        "Lee County Supervisor of Elections",
        "All-Inclusive Sample Ballot, November 3, 2026 General Election",
    ),
    "FL-12073": (
        "https://www.leonvotes.gov/Records/Sample-Ballots",
        "Leon County Supervisor of Elections",
        "2026 General Election Guide: Sample Ballot, November 3, 2026",
    ),
    "CA-06001": (
        "https://alamedacountyca.gov/rov_app/candidatelist",
        "Alameda County Registrar of Voters",
        "Candidate List: General Election, November 3, 2026 (Official)",
    ),
    "CA-06097": (
        "https://sonomacounty.gov/administrative-support-and-fiscal-services/registrar-of-voters/elections/november-3-2026-general-election-candidates-on-the-ballot",
        "Sonoma County Registrar of Voters",
        "November 3, 2026 General Election: Candidates on the Ballot",
    ),
    "CA-06075": (
        "https://www.sf.gov/candidates-november-3-2026-general-election",
        "San Francisco Department of Elections",
        "Candidates, November 3, 2026, General Election",
    ),
    "CA-06085": (
        "https://vote.santaclaracounty.gov/november-3-2026-general-election-resources",
        "Santa Clara County Registrar of Voters",
        "Qualified List of Local Candidates: November 3, 2026 General Election",
    ),
    "CA-06081": (
        "https://smcacre.gov/",
        "San Mateo County Assessor-County Clerk-Recorder and Elections",
        "Roster of Candidates: Statewide General Election, November 3, 2026",
    ),
    "CA-06061": (
        "https://www.placer.ca.gov/",
        "Placer County Elections",
        "Qualified Candidate List: November 3, 2026 General Election",
    ),
    "CA-06029": (
        "https://www.kernvote.com/elections/upcoming-elections/november-3-2026-general-election",
        "Kern County Elections",
        "Contest/Candidate Proof List: November 3, 2026 General Election",
    ),
    "CA-06019": (
        "https://www.fresnocountyca.gov/",
        "Fresno County Clerk/Registrar of Voters",
        "Contest/Candidate Proof List: Consolidated Statewide General Election, November 3, 2026",
    ),
    "CA-06107": (
        "https://tularecounty.ca.gov/elections/registrar-of-voters/current-election-information",
        "Tulare County Registrar of Voters",
        "Contest/Candidate Proof List: November 3, 2026 General Election",
    ),
    "NJ-34031": (
        "https://www.passaiccountynj.org/residents/election-and-voting-information/county-clerk-elections-division",
        "Passaic County Clerk",
        "List of County and Local Candidates for the General Election, November 3, 2026",
    ),
    "NJ-34035": (
        "https://www.somersetcountynj.gov/government/elected-officials/county-clerk/election-division",
        "Somerset County Clerk",
        "2026 General Election Candidates",
    ),
    "NJ-34041": (
        "https://www.warrencountyvotes.com/elections/current-election-candidates",
        "Warren County Clerk",
        "2026 Warren County General Election Candidates: County and Municipal",
    ),
    "NJ-34023": (
        "https://www.middlesexcountynj.gov/government/departments/department-of-community-services/office-of-county-clerk/election-services",
        "Middlesex County Clerk",
        "Official Candidate List, General Election 2026",
    ),
    "NJ-34017": (
        "https://www.hudsoncountyclerk.org/wp-content/uploads/2026/08/2026-November-3-General-Elecction-Official-Master-List.pdf",
        "Hudson County Clerk",
        "November 3, 2026, General Election Official Master List",
    ),
    "CA-06013": (
        "https://www.contracostavote.gov/election/november-3-2026-general-election/",
        "Contra Costa County Clerk-Recorder-Elections",
        "November 3, 2026, General Election: List of Candidates and Measures",
    ),
    "NJ-34013": (
        "https://www.essexclerk.com/_Content/pdf/Essex-2026-Sample-Ballots.pdf",
        "Essex County Clerk",
        "Official General Election Sample Ballot, November 3, 2026",
    ),
    "NJ-34021": (
        "https://www.mercercounty.org/government/county-clerk/elections/election-sample-ballots",
        "Mercer County Clerk",
        "2026 Official General Election Sample Ballots, November 3, 2026 (twelve municipal ballots)",
    ),
    "NJ-34003": (
        "https://docs.google.com/spreadsheets/d/1uS4o-ogeeIrZMyfxYs0Eg5WDcSYPuIrkMskOTXKJDP4/edit?usp=sharing",
        "Bergen County Clerk",
        "2026 General Election Candidate List (Unofficial)",
    ),
    "IL-17043": (
        "https://www.dupagecounty.gov/elections/",
        "DuPage County Clerk Election Division",
        "2026 General Official Candidate Listing",
    ),
    "AZ-04019": (
        "https://www.pima.gov/2026-general-election-official-candidate-list",
        "Pima County Elections Department",
        "2026 General Election Official Candidate List",
    ),
    "NV-32003": (
        "https://www.clarkcountynv.gov/government/departments/elections/",
        "Clark County Election Department",
        "Offices Up for Election and Candidates, 2026 General Election",
    ),
    "MO-29071": (
        "https://www.franklinmo.org/",
        "Franklin County Clerk",
        "What Is on the Ballot, General Election, November 3, 2026",
    ),
    "MO-29165": (
        "https://www.plattecountymo.gov/",
        "Platte County Board of Election Commissioners",
        "Election Proof, General Election, November 3, 2026",
    ),
    "MO-29043": (
        "https://www.christiancountymo.gov/offices/county-clerk/sample-ballots/",
        "Christian County Clerk",
        "Sample Ballots, General Election, November 3, 2026",
    ),
    "MO-29097": (
        "https://www.jaspercounty.org/",
        "Jasper County Clerk",
        "Sample Ballots, General Election, November 3, 2026",
    ),
    "WV-54003": (
        "https://www.berkeleywv.org/542/Elections",
        "Berkeley County Clerk",
        "Publication Ballot, General Election, November 3, 2026",
    ),
    "WV-54039": (
        "https://kanawha.us/county-clerk/election-center/",
        "Kanawha County Clerk",
        "2026 General Election, Election Center",
    ),
    "WY-56005": (
        "https://www.campbellcountywy.gov/DocumentCenter/View/28182",
        "Campbell County Clerk",
        "2026 General Election Candidates",
    ),
    "WY-56007": (
        "https://carboncountywy.gov/DocumentCenter/View/8605/2026-General-Election-Candidates-as-of-090426",
        "Carbon County Clerk",
        "2026 General Election Candidates",
    ),
    "WY-56035": (
        "https://sublettecountywy.gov/DocumentCenter/View/7980/2026-General-Election-Candidates-Daily-Update",
        "Sublette County Clerk",
        "2026 General Election Candidates",
    ),
    "WY-56039": (
        "https://www.tetoncountywy.gov/DocumentCenter/View/41951/Contact-cadidate-list",
        "Teton County Clerk",
        "2026 General Election Candidates",
    ),
    "WY-56011": (
        "https://cms2.revize.com/revize/crookscountynew/elected_officials/clerk/Election/2026/Candidate%27s%20Names%20&%20Addresses.pdf",
        "Crook County Clerk",
        "2026 General Election Candidates",
    ),
    "WY-56041": (
        "https://uintacountywy.gov/DocumentCenter/View/8640/2026-General-Election-Candidates",
        "Uinta County Clerk",
        "2026 General Election Candidates",
    ),
    "MT-30031": (
        "https://gallatinmt.gov/DocumentCenter/View/2423/County-Candidates-2026",
        "Gallatin County Clerk and Recorder",
        "2026 County Candidates (last updated 8/10)",
    ),
    "MT-30013": (
        "https://www.cascadecountymt.gov/DocumentCenter/View/8146/2026-Federal-General-County-Candidate-Certification",
        "Cascade County Clerk and Recorder",
        "2026 Federal General Election: Certified Local Candidate List (August 20, 2026)",
    ),
    "WY-56025": (
        "https://www.natronacounty-wy.gov/DocumentCenter/View/13912/Natrona-County-2026-General-Candidate-Roster",
        "Natrona County Clerk",
        "Natrona County 2026 General Election Candidate Roster (9/11/2026)",
    ),
    "MT-30067": (
        "https://www.parkcountymt.gov/uploads/files/pages/171/2026-County-Candidates.pdf",
        "Park County Clerk and Recorder",
        "2026 Park County Candidates",
    ),
    "TX-48491-rr": (
        "https://www.wilco.org/Departments/Elections",
        "Williamson County Elections",
        "November 3, 2026 Joint General and Special Election Comprehensive Sample Ballot",
    ),
    "TX-48201": (
        "https://www.harrisvotes.com/",
        "Harris County Clerk",
        "November 3, 2026 General and Special Elections Sample Ballot",
    ),
    "MO-29225": (
        "https://webstercountymo.gov/wp-content/uploads/2026/09/Webster-Publication-110326.pdf",
        "Webster County Clerk",
        "Sample Ballot, General Election, Tuesday, November 3, 2026",
    ),
    "IL-17037": (
        "https://dekalbcountyclerkil.gov/wp-content/uploads/2026/07/11032026LocalCandidates.pdf",
        "DeKalb County Clerk and Recorder",
        "November 3, 2026 General Election - Local Candidates (updated 6/3/2026)",
    ),
    "TX-48085": (
        "https://www.wylieisd.net/our-district/school-board/board-elections",
        "Wylie Independent School District",
        "Board Election Information: November 3, 2026",
    ),
    "UT-49053": (
        "https://www.washco.utah.gov/wp-content/uploads/2026/09/2026-General-Election-Certification-of-County-Candidates-Notice.pdf",
        "Washington County Clerk/Auditor",
        "Certification of County Candidates and Ballot Propositions, 2026 General Election",
    ),
    "UT-49005": (
        "https://www.cachecounty.gov/elections/elections-home/election-notice.html",
        "Cache County Clerk",
        "Certification for Regular General Election, November 3, 2026",
    ),
    "UT-49057": (
        "https://www.weberelections.gov/",
        "Weber County Clerk/Auditor",
        "List of Candidates, 2026 General Election",
    ),
    "TX-48491": (
        "https://www.leanderisd.org/board-elections/",
        "Leander Independent School District",
        "Board of Trustees Elections, November 3, 2026",
    ),
    "TX-48141": (
        "https://www.epcounty.com/elections/",
        "El Paso County Elections Department",
        "Canutillo Independent School District sample ballot, November 3, 2026",
    ),
    "UT-49035": (
        "https://www.saltlakecounty.gov/clerk/elections/",
        "Salt Lake County Clerk",
        "Certification of Candidates, 2026 General Election",
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
    "CA-06077": (
        "https://www.sjgov.org/docs/default-source/registrar-of-voters-documents/elections/gubernatorial-general-election-2026/candidate-services/candidate-roster-(35).pdf",
        "San Joaquin County Registrar of Voters",
        "Candidate List: November 3, 2026 General Election (Qualified Candidates Only)",
    ),
    "CA-06089": (
        "https://elections.shastacounty.gov/wp-content/uploads/2026/08/Final-Qualifed-Candidate-List-8-25-26.pdf",
        "Shasta County Clerk/Registrar of Voters",
        "Final Qualified Candidate List: November 3, 2026 General Election",
    ),
    "CA-06007": (
        "https://buttevotes.ca.gov/DocumentCenter/View/4207/cfmcfmr029_candidatecertification",
        "Butte County Clerk-Recorder/Registrar of Voters",
        "Final Qualified Candidate List: General Election, November 3, 2026",
    ),
    "CA-06067": (
        "https://elections.saccounty.gov/content/vre/us/en/campaign-services/candidate-report.html",
        "Sacramento County Voter Registration and Elections",
        "Roster of Candidates: November 3, 2026 General Election (contests on ballot, qualified candidates)",
    ),
    "CA-06039": (
        "https://votemadera.com/wp-content/uploads/2026/09/Contests-on-Ballot-with-Qualified-Candidates.pdf",
        "Madera County Elections Division",
        "Contest/Candidate Proof List: Contests on Ballot with Qualified Candidates, November 3, 2026 Consolidated General Election",
    ),
    "CA-06023": (
        "https://humboldtgov.org/DocumentCenter/View/156936/Qualified-Candidate-List---City-Contests",
        "Humboldt County Elections Office",
        "Qualified Candidate List: City Contests, November 3, 2026 General Election",
    ),
    "CA-06023-s": (
        "https://humboldtgov.org/DocumentCenter/View/156758/List-of-Qualified-Candidates---Statewide-General-Election-2026",
        "Humboldt County Elections Office",
        "List of Qualified Candidates: Contests Going to Election, November 3, 2026 General Election",
    ),
    "CA-06025": (
        "https://elections.imperialcounty.org/wp-content/uploads/2026/08/CandidateStatus_Website-1-14.pdf",
        "Imperial County Registrar of Voters",
        "Imperial County Candidate Status, November 3, 2026 General Election (updated as of 08/14/2026)",
    ),
    "CA-06095": (
        "https://content.solanocounty.gov/sites/default/files/2026-07/CANDIDATE_FILED_LOG_Nov_3_2026_7.13.26.pdf",
        "Solano County Registrar of Voters",
        "Candidate Filed Log, November 3, 2026 General Election (as of 8/20/2026)",
    ),
    "CA-06047": (
        "https://www.countyofmerced.com/DocumentCenter/View/48056/Candidate-List",
        "Merced County Registrar of Voters",
        "Final Qualified Candidate List, November 3, 2026 General Election (printed 8/25/2026)",
    ),
    "CA-06055": (
        "https://www.napacounty.gov/",
        "Napa County Registrar of Voters",
        "Roster of Candidates, General Election 11/3/2026 (qualified candidates)",
    ),
    "CA-06017": (
        "https://www.eldoradocounty.ca.gov/",
        "El Dorado County Elections Department",
        "Candidate List, General Election 11/3/2026 (qualified candidates only)",
    ),
    "CA-06053": (
        "https://www.countyofmonterey.gov/",
        "Monterey County Elections Department",
        "Candidate List, November 3, 2026 General Election",
    ),
    "CA-06031": (
        "https://www.countyofkingsca.gov/",
        "Kings County Elections Department",
        "Contest/Candidate Proof List, November 3, 2026 General Election",
    ),
    "CA-06101": (
        "https://www.suttercounty.org/",
        "Sutter County Elections",
        "Candidates for the November 2026 General Election (as of 8/12/2026)",
    ),
    "CA-06041": (
        "https://www.marincounty.gov/departments/elections/november-3-2026-general-election/information-and-about-candidates-110326/status-local-candidates-office-110326",
        "Marin County Elections Department",
        "Status of Local Candidates by Office, November 3, 2026 General Election",
    ),
    "CA-06113": (
        "https://elections.yolocounty.gov/423/Municipal-City",
        "Yolo County Assessor/Clerk-Recorder/Elections",
        "Candidate Filings: Municipal / City, November 3, 2026 General Election",
    ),
    "CA-06113-s": (
        "https://elections.yolocounty.gov/397/School-Districts",
        "Yolo County Assessor/Clerk-Recorder/Elections",
        "Candidate Filings: School Districts, November 3, 2026 General Election",
    ),
    "CA-06111": (
        "https://clerkrecorder.venturacounty.gov/wp-content/uploads/2026/06/cfmcfmr001_contcanddataproof_08-12-2026.pdf",
        "Ventura County Clerk and Recorder/Registrar of Voters",
        "Contest/Candidate Proof List, November 3, 2026 General Election (printed 8/12/2026; candidates qualified, pending certification of the ballot)",
    ),
    "CA-06083": (
        "https://content.civicplus.com/api/assets/7c6fb4a3-0c1e-4cbe-b382-b906599d7977",
        "County of Santa Barbara Elections Division",
        "Qualified On Ballot Contest/Candidate List for Cities, November 3, 2026 General Election",
    ),
    "CA-06083-s": (
        "https://content.civicplus.com/api/assets/1c1115e9-7446-48b3-9cea-5ad1eb88b2e6",
        "County of Santa Barbara Elections Division",
        "Qualified Contest/Candidate List for Schools and Special Districts, November 3, 2026 General Election",
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
VOTERFOCUS = {
    "alachua_54": ("Alachua", "", "2026 Gubernatorial and City of Gainesville"),
    "baker_33": ("Baker", "", "General Election"),
    "bay_35": ("Bay", "", "2026 Primary/General Election"),
    "bradford_23": ("Bradford", "", "2026 General"),
    "brevard_30": ("Brevard", "", "2026 General Election"),
    "calhoun_22": ("Calhoun", "", "2026 Primary/General Election"),
    "charlotte_27": ("Charlotte", "", "2026 Election Cycle"),
    "citrus_21": ("Citrus", "", "2026 General Election"),
    "clay_94": ("Clay", "", "2026 Election Cycle"),
    "collier_15": ("Collier", "", "2026 General Election"),
    "columbia_29": ("Columbia", "", "2026 GENERAL ELECTION"),
    "desoto_6": ("DeSoto", "", "2026 General Election"),
    "dixie_13": ("Dixie", "", "2026 Primary/General Election"),
    "duval_40": ("Duval", "", "2026 General Election"),
    "duval_44": ("Duval", "", "2026 Special Unitary Election Cycle"),
    "escambia_26": ("Escambia", "Pensacola", "2026 General Election"),
    "flagler_41": ("Flagler", "", "2026 Candidates"),
    "franklin_26": ("Franklin", "", "2026 Election Cycle"),
    "gadsden_37": ("Gadsden", "", "2026 General Election"),
    "gilchrist_24": ("Gilchrist", "", "2026 Primary/General Election"),
    "glades_6": ("Glades", "", "2026 Election Cycle"),
    "gulf_61": ("Gulf", "", "2026 Candidate Reporting"),
    "hamilton_14": ("Hamilton", "", "2026 General Election"),
    "hardee_16": ("Hardee", "", "2026 Election Cycle"),
    "hendry_17": ("Hendry", "", "2026 Election"),
    "hernando_32": ("Hernando", "", "2026 CANDIDATE ELECTION CYCLE"),
    "highlands_10": ("Highlands", "", "General 2026"),
    "hillsborough_27": ("Hillsborough", "", "2026 Candidate Election Cycle"),
    "holmes_11": ("Holmes", "", "2026 General Election"),
    "indianriver_18": ("Indian River", "", "2026 General Election"),
    "jackson_19": ("Jackson", "", "2026 General Election"),
    "jefferson_8": ("Jefferson", "", "2026 Election Cycle"),
    "lafayette_8": ("Lafayette", "", "2026 Election Cycle"),
    "lake_17": ("Lake", "", "2026 General Election"),
    "lee_34": ("Lee", "", "2026 CANDIDATES"),
    "lee_35": ("Lee", "", "2026 MUNICIPAL"),
    "levy_23": ("Levy", "", "2026 Election"),
    "liberty_25": ("Liberty", "", "2026 Election"),
    "madison_8": ("Madison", "", "2026 Primary/General Election"),
    "manatee_219": ("Manatee", "", "2026 Municipal Elections"),
    "marion_49": ("Marion", "Belleview", "City of Belleview"),
    "marion_50": ("Marion", "Dunnellon", "City of Dunnellon"),
    "marion_47": ("Marion", "", "General Election"),
    "martin_16": ("Martin", "", "2026 Election Cycle"),
    "monroe_49": ("Monroe", "", "2026, County, State, Federal Elections"),
    "nassau_46": ("Nassau", "", "2026 General Election Cycle"),
    "nassau_48": ("Nassau", "", "2026 Municipal Election Cycle"),
    "okaloosa_220": ("Okaloosa", "", "2026 Election Cycle"),
    "okaloosa_223": ("Okaloosa", "", "Special Election- School Board District 1"),
    "okeechobee_32": ("Okeechobee", "", "2026 Candidate"),
    "orange_105": ("Orange", "", "Election- 11/3/2026"),
    "osceola_17": ("Osceola", "", "General Election 2026"),
    "palmbeach_12": ("Palm Beach", "", "2026 Election Cycle"),
    "pinellas_35": ("Pinellas", "", "2026 Candidate Election Cycle"),
    "polk_22": ("Polk", "", "2026 General Election"),
    "putnam_34": ("Putnam", "Palatka", "2026 Election Cycle"),
    "santarosa_11": ("Santa Rosa", "", "2026 General Election"),
    "sarasota_134": ("Sarasota", "", "2026 Primary & General Elections"),
    "sarasota_153": ("Sarasota", "North Port", "City of North Port General Election"),
    "sarasota_149": ("Sarasota", "Sarasota", "City of Sarasota General Election"),
    "sarasota_147": ("Sarasota", "Venice", "City of Venice General Election"),
    "seminole_126": ("Seminole", "Altamonte Springs", "2026 Altamonte Springs General"),
    "seminole_124": ("Seminole", "Casselberry", "2026 CASSELBERRY GENERAL"),
    "seminole_116": ("Seminole", "", "2026 GENERAL ELECTION"),
    "seminole_129": ("Seminole", "Lake Mary", "2026 LAKE MARY GENERAL"),
    "seminole_122": ("Seminole", "Longwood", "2026 LONGWOOD GENERAL"),
    "seminole_128": ("Seminole", "Oviedo", "2026 OVIEDO GENERAL ELECTION"),
    "seminole_127": ("Seminole", "Sanford", "2026 Sanford General"),
    "seminole_125": ("Seminole", "Winter Springs", "2026 Winter Springs General"),
    "stjohns_20": ("St. Johns", "", "2026 Election Cycle"),
    "stlucie_26": ("St. Lucie", "", "General 2026"),
    "sumter_26": ("Sumter", "", "2026 GENERAL ELECTION (Candidates)"),
    "suwannee_29": ("Suwannee", "", "2026 GENERAL ELECTION"),
    "taylor_16": ("Taylor", "Perry", "General Election - 2026"),
    "union_11": ("Union", "", "2026 Elections"),
    "volusia_36": ("Volusia", "", "General Election 11/3/26 (8/18/26)"),
    "wakulla_15": ("Wakulla", "", "2026 Election Cycle"),
    "walton_42": ("Walton", "", "2026 County Elections"),
    "washington_10": ("Washington", "", "2026 General Election"),
}
FEEDS.update(
    {
        f"FL-vf-{group}": (
            f"https://www.voterfocus.com/CampaignFinance/candidate_pr.php?el={group.split('_')[1]}&c={group.split('_')[0]}",
            f"{county} County Supervisor of Elections",
            f"Candidate Reports: {label}",
        )
        for group, (county, _, label) in VOTERFOCUS.items()
    }
)
OCVR = {
    "OR-41053": (
        "Polk",
        "https://www.polkcountyor.gov/DocumentCenter/View/6955/Ballot-Content-for-November-3-2026-Election",
        "Polk County Clerk Elections",
    ),
    "OR-41039": (
        "Lane",
        "https://www.lanecounty.org/UserFiles/Servers/Server_3585797/File/County%20Clerk/Elections/Dated%20Elections/November%203,%202026/GE26_E_019%20Web.pdf",
        "Lane County Elections",
    ),
    "OR-41051": (
        "Multnomah",
        "https://multco.us/file/ballot_content_-_nov._2026/download",
        "Multnomah County Elections",
    ),
}
FEEDS.update(
    {
        key: (
            url,
            publisher,
            "Ballot Content, November 3, 2026 General Election (Oregon Centralized Voter Registration report E-019)",
        )
        for key, (_, url, publisher) in OCVR.items()
    }
    | {
        "OR-41051-rcv": (
            "https://multco.us/file/ballot_content_rcv_-_nov._2026/download",
            "Multnomah County Elections",
            "Ballot Content RCV, November 3, 2026 General Election (Oregon Centralized Voter Registration report E-019)",
        ),
        "OR-41005": (
            "https://dochub.clackamas.us/documents/drupal/03f4f9db-a1ab-4a1f-9ce1-16059d49a513",
            "Clackamas County Elections",
            "Voters' Pamphlet, November 3, 2026 General Election: Table of Contents (Candidates)",
        ),
        "OR-41047": (
            "https://www.co.marion.or.us/CO/elections/Results/Documents/20261103/November%203%202026%20General%20Election%20VP_FINAL_ONLINEVER.pdf",
            "Marion County Clerk, Elections Division",
            "Voters' Pamphlet, November 3, 2026 General Election: Sample Ballot",
        ),
        "OR-41009": (
            "https://www.columbiacountyor.gov/media/Election/2026-11-03/0-2026_Columbia-General_VP_v5-FINAL.pdf",
            "Columbia County Elections",
            "Voters' Pamphlet, November 3, 2026 General Election: Index of Candidates on Ballot",
        ),
        "OR-41043": (
            "https://www.linncountyor.gov/media/61426",
            "Linn County Clerk",
            "List of Candidates for Linn County Oregon, November 3, 2026 General Election",
        ),
        "OR-41043-albany": (
            "https://albanyoregon.gov/elections",
            "City of Albany",
            "Candidate Filings (monday.com board)",
        ),
        "OR-41043-lebanon": (
            "https://www.ci.lebanon.or.us/",
            "City of Lebanon",
            "Register of City Candidates, November 2026 General Election",
        ),
        "OR-41043-sweet-home": (
            "https://www.sweethomeor.gov/",
            "City of Sweet Home",
            "City Council Elections: Who has filed to run",
        ),
        "OR-41043-harrisburg": (
            "https://www.ci.harrisburg.or.us/",
            "City of Harrisburg",
            "2026 General Election Certified Candidates",
        ),
        "OR-41043-tangent": (
            "https://www.tangentor.gov/post/2026-municipal-election",
            "City of Tangent",
            "2026 Municipal Election",
        ),
        "KS-20091": (
            "https://www.jocoelection.org/sites/default/files/2026-10/11-03-2026%20-%20Notice%20of%20Election%20%28candidates%29%20-%20REVISED.pdf",
            "Johnson County Election Office",
            "Notice of Election (Candidates), November 3, 2026 General Election (revised)",
        ),
        "KS-20091-prairie-village": (
            "https://www.jocoelection.org/sites/default/files/2026-10/11-03-2026%20-%20Notice%20of%20Election%20%28candidates%29%20-%20REVISED.pdf",
            "Johnson County Election Office",
            "Notice of Election (Candidates), November 3, 2026 General Election (revised)",
        ),
        "FL-12009": (
            "https://www.votebrevard.gov/Candidate-Information/2026-Candidates",
            "Brevard County Supervisor of Elections",
            "2026 Candidates",
        ),
        "TX-48453": (
        "https://www.austinisd.org/board/elections",
        "Austin Independent School District",
        "2026 Trustee Elections",
    ),
    "DE-10003": (
        "https://elections.delaware.gov/candidates/candidatelist/genl_fcddt_2026.html",
        "Delaware Department of Elections",
        "General Election 11/3/2026: Filed Candidates by Office",
    ),
    "DE-10001": (
        "https://elections.delaware.gov/candidates/candidatelist/genl_fcddt_2026.html",
        "Delaware Department of Elections",
        "General Election 11/3/2026: Filed Candidates by Office",
    ),
    "DE-10005": (
        "https://elections.delaware.gov/candidates/candidatelist/genl_fcddt_2026.html",
        "Delaware Department of Elections",
        "General Election 11/3/2026: Filed Candidates by Office",
    ),
    "NE-31055": (
        "https://www.votedouglascounty-ne.gov/",
        "Douglas County Election Commission",
        "Candidates Up For Election: 2026 General",
    ),
    "KS-20173": (
        "https://www.sedgwickcounty.org/elections/",
        "Sedgwick County Election Office",
        "Notice of General Election, November 3, 2026",
    ),
    "OH-39165": (
        "https://vote.warrencountyohio.gov/doc/Voting/100_Day_notice_for_November_3_2026.pdf",
        "Warren County Board of Elections",
        "Election Notice for the Federal Write-In Absentee Ballot, November 3, 2026 General Election",
    ),
    "OH-39041": (
        "https://vote.delawarecountyohio.gov/wp-content/uploads/2026/10/46-DAY-NOV-2026-FWAB-PDF1.pdf",
        "Delaware County Board of Elections",
        "Election Notice for the Federal Write-In Absentee Ballot, November 3, 2026 General Election (revised 10/2/2026)",
    ),
    "OH-39155": (
        "https://boe.co.trumbull.oh.gov/pdfs/100%20Day%20Notice.pdf",
        "Trumbull County Board of Elections",
        "Election Notice for the Federal Write-In Absentee Ballot, November 3, 2026 General Election",
    ),
    "IL-17161": (
        "https://rockislandcountyil.gov/DocumentCenter/View/2818",
        "Rock Island County Clerk",
        "List of Candidates for the November 3, 2026 General Election",
    ),
    "OR-41043-lyons": (
            "https://www.cityoflyons.org/",
            "City of Lyons",
            "General Election Certified Candidates",
        ),
    "OR-41043-millersburg": (
            "https://www.millersburgoregon.gov/1469/November-2026-City-Council-Election",
            "City of Millersburg",
            "November 2026 City Council Election",
        ),
        "OR-41019": (
            "https://www.douglascountyor.gov/DocumentCenter/View/30712/Nov-2026-Ballot-Content-Report-E-019",
            "Douglas County Clerk",
            "Proof Ballot Content (Report E-019), November 3, 2026 General Election",
        ),
        "OR-41017": (
            "https://www.deschutescounty.gov/1593/November-3-2026-General-Election",
            "Deschutes County Clerk",
            "November 3, 2026 General Election: Filed Candidates",
        ),
        "OR-41017-bend": (
            "https://bendoregon.gov/city-council/elections/",
            "City of Bend",
            "City Council Elections: Candidate Filings",
        ),
        "OR-41017-la-pine": (
            "https://www.lapineoregon.gov/660/Elections",
            "City of La Pine",
            "City Council Elections: 2026 General Election Current Candidates",
        ),
        "OR-41017-sisters": (
            "https://www.nuggetnews.com/story/2026/10/14/news/kollerer-makes-bid-for-city-council-seat/39640.html",
            "The Nugget Newspaper",
            "Kollerer makes bid for City Council seat (four candidates for three Sisters council seats)",
        ),
        "OR-41017-redmond": (
            "https://www.redmondoregon.gov/government/city-council/running-for-elected-office",
            "City of Redmond",
            "Running for Elected Office: Current Mayor and Council Election Candidate Filings",
        ),
        "OR-41029": (
            "https://www.jacksoncountyor.gov/Document%20Center/Departments/Clerk/Elections/General%20Elections/Nov%203,%202026/List%20of%20City%20Candidates%20(November%202026).pdf",
            "Jackson County Elections",
            "List of City Candidates, November 3, 2026 General Election",
        ),
        "OR-41041": (
            "https://co.lincoln.or.us/DocumentCenter/View/8365/List-of-Candidates-and-Measures-for-the-November-2026-General-Election?bidId=",
            "Lincoln County Clerk",
            "List of Candidates & Measures, November 3, 2026 General Election",
        ),
        "OR-41035": (
            "https://www.klamathcounty.org/DocumentCenter/View/2313/City-Candidates-PDF",
            "Klamath County Clerk / Elections",
            "City Candidates, November 3, 2026 General Election",
        ),
        "OR-41003": (
            "https://re.bentoncountyor.gov/current-election/candidates/",
            "Benton County Records and Elections",
            "November 3 General Election: Open Positions and Candidate Filings",
        ),
        "OR-41003-rcv": (
            "https://re.bentoncountyor.gov/current-election/candidates/",
            "Benton County Records and Elections",
            "November 3 General Election: Open Positions and Candidate Filings (ranked choice contests)",
        ),
        "OR-41067": (
            "https://www.washingtoncountyor.gov/elections/current-election",
            "Washington County Elections Division",
            "November 3, 2026 General Election: Candidate Positions",
        ),
    }
    | {
        f"OR-41067-{city}": (
            f"https://www.washingtoncountyor.gov/elections/documents/city-{document}/download?inline",
            f"City of {city.replace('-', ' ').title()}",
            "Certification of Candidates, November 3, 2026 General Election (filed with Washington County Elections)",
        )
        for city, document in (
            *(
                (city, f"{city}-certified-candidates-nov-2026")
                for city in (
                    "banks",
                    "cornelius",
                    "durham",
                    "forest-grove",
                    "gaston",
                    "hillsboro",
                    "king-city",
                    "north-plains",
                    "rivergrove",
                    "sherwood",
                    "tigard",
                    "tualatin",
                )
            ),
            ("wilsonville", "tualatin-certified-candidates-nov-2026-0"),
        )
    }
)
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
NO_FILING = "No Candidate Filed"
ROLES = frozenset(
    [
        "commissioner",
        "commission",
        "constable",
        "magistrate",
        "sheriff",
        "clerk",
        "treasurer",
        "assessor",
        "coroner",
        "attorney",
        "judge",
        "justice",
        "mayor",
        "council",
        "supervisor",
        "recorder",
        "auditor",
        "surveyor",
        "collector",
        "executive",
        "administrator",
        "jailer",
        "prosecutor",
        "solicitor",
        "alderman",
        "legislator",
    ]
)
ROLE = {"commission": "commissioner"}
GA_STATE = (
    r"US |Governor|Lieutenant|Secretary of State|Attorney General|State School|Commissioner of|PSC|State Senate"
    r"|State House|State Representative|Party Question|Justice|Judge|Lt\.|District Attorney"
)
GA_BALLOTS = {"Fulton", "Gwinnett", "Cobb", "DeKalb", "Clayton", "Paulding", "Houston", "Columbia", "Richmond"}
ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
DELIVERED = {
    "OR-41043": DATA / "inbox" / "y26" / "or" / "linn.pdf",
    "OR-41043-albany": DATA / "inbox" / "y26" / "or" / "albany.txt",
    "OR-41043-lebanon": DATA / "inbox" / "y26" / "or" / "lebanon.html",
    "OR-41043-sweet-home": DATA / "inbox" / "y26" / "or" / "sweet-home.html",
    "OR-41043-harrisburg": DATA / "inbox" / "y26" / "or" / "harrisburg.html",
    "OR-41043-tangent": DATA / "inbox" / "y26" / "or" / "tangent.html",
    "OR-41043-lyons": DATA / "inbox" / "y26" / "or" / "lyons.html",
    "OR-41043-millersburg": DATA / "inbox" / "y26" / "or" / "millersburg.txt",
    "OH-39165": DATA / "inbox" / "y26" / "oh" / "warren_cands.pdf",
    "OH-39041": DATA / "inbox" / "y26" / "oh" / "delaware_cands.pdf",
    "OH-39155": DATA / "inbox" / "y26" / "oh" / "trumbull_cands.pdf",
    "IL-17161": DATA / "inbox" / "y26" / "il" / "rock_island_cands.pdf",
    "DE-10003": DATA / "inbox" / "y26" / "de" / "state.html",
    "DE-10001": DATA / "inbox" / "y26" / "de" / "state.html",
    "DE-10005": DATA / "inbox" / "y26" / "de" / "state.html",
    "NE-31055": DATA / "inbox" / "y26" / "ne" / "douglas.html",
    "KS-20173": DATA / "inbox" / "y26" / "ks" / "sedgwick.html",
    "TX-48453": DATA / "inbox" / "y26" / "tx" / "aisd.html",
    "FL-12009": DATA / "inbox" / "y26" / "fl" / "brevard.html",
    "KS-20091": DATA / "inbox" / "y26" / "ks" / "johnson.pdf",
    "KS-20091-prairie-village": DATA / "inbox" / "y26" / "ks" / "johnson.pdf",
    "OR-41019": DATA / "inbox" / "y26" / "or" / "douglas.pdf",
    "OR-41017": DATA / "inbox" / "y26" / "or" / "deschutes.html",
    "OR-41017-bend": DATA / "inbox" / "y26" / "or" / "bend.html",
    "OR-41017-la-pine": DATA / "inbox" / "y26" / "or" / "la-pine.html",
    "OR-41017-redmond": DATA / "inbox" / "y26" / "or" / "redmond.html",
    "OR-41017-sisters": DATA / "inbox" / "y26" / "or" / "sisters.html",
    "OR-41003": DATA / "inbox" / "y26" / "or" / "benton.html",
    "OR-41003-rcv": DATA / "inbox" / "y26" / "or" / "benton.html",
    "OR-41029": DATA / "inbox" / "y26" / "or" / "jackson_city.pdf",
    "OR-41041": DATA / "inbox" / "y26" / "or" / "lincoln.pdf",
    "OR-41035": DATA / "inbox" / "y26" / "or" / "kl_city.pdf",
    "04013": DATA
    / "inbox"
    / "manual"
    / "MF-153"
    / "2026 General Election Maricopa County Candidates On Ballot - Remediated.pdf",
    "17031c": DATA / "inbox" / "manual" / "MF-154" / "GetAllCandidates.json",
    "SC": DATA / "inbox" / "y26" / "sc_candidates_2026-11-03_all.csv",
    "KY": DATA / "inbox" / "y26" / "ky" / "countycandidates_20261028.xls",
    "NM": DATA / "inbox" / "y26" / "nm_candidates_2026_all.csv",
    "ME": DATA / "inbox" / "y26" / "ne" / "me_general_candidate_list_2026.xlsx",
    "VT": DATA / "inbox" / "y26" / "ne" / "vt_ge_qualified_candidates_2026.xlsx",
    "CA-06095": DATA / "inbox" / "y26" / "ca" / "solano_filed_log.pdf",
    "CA-06047": DATA / "inbox" / "y26" / "ca" / "merced_final.pdf",
    "CA-06055": DATA / "inbox" / "y26" / "ca" / "napa_qualified.pdf",
    "CA-06017": DATA / "inbox" / "y26" / "ca" / "eldorado_list.pdf",
    "CA-06053": DATA / "inbox" / "y26" / "ca" / "monterey_list.html",
    "CA-06031": DATA / "inbox" / "y26" / "ca" / "kings_proof.pdf",
    "CA-06101": DATA / "inbox" / "y26" / "ca" / "sutter_list.pdf",
    "CA-06041": DATA / "inbox" / "y26" / "ca" / "marin_local_status.html",
    "NJ-34009": DATA / "inbox" / "y26" / "nj" / "cape_may.pdf",
    "NJ-34011": DATA / "inbox" / "y26" / "nj" / "cumberland.xlsx",
    "NJ-34019-r": DATA / "inbox" / "y26" / "nj" / "hunterdon_rep.pdf",
    "NJ-34019-i": DATA / "inbox" / "y26" / "nj" / "hunterdon_independent.pdf",
    "NJ-34025": DATA / "inbox" / "y26" / "nj" / "monmouth_municipal.pdf",
    "NJ-34025-c": DATA / "inbox" / "y26" / "nj" / "monmouth.pdf",
    "NJ-34027": DATA / "inbox" / "y26" / "nj" / "morris.pdf",
    "NJ-34033": DATA / "inbox" / "y26" / "nj" / "salem_candidates.pdf",
    "MI-26017": DATA / "inbox" / "y26" / "mi" / "bay.pdf",
    "MI-26049": DATA / "inbox" / "y26" / "mi" / "genesee.pdf",
    "MI-26091": DATA / "inbox" / "y26" / "mi" / "lenawee.pdf",
    "MI-26163": DATA / "inbox" / "manual" / "MF-199" / "official-candidate-list_november-3-2026.pdf",
    "MI-26099": DATA / "inbox" / "y26" / "mi" / "macomb.pdf",
    "MI-26147": DATA / "inbox" / "y26" / "mi" / "stclair.pdf",
    "MI-26055": DATA / "inbox" / "y26" / "mi" / "grandtraverse.pdf",
    "MI-26065": DATA / "inbox" / "y26" / "mi" / "ingham.pdf",
    "MI-26093": DATA / "inbox" / "y26" / "mi" / "livingston.pdf",
    "MI-26081": DATA / "inbox" / "manual" / "MF-200" / "11032026 Candidates and Proposals_202610261659007429.pdf",
    "MI-26161": DATA / "inbox" / "y26" / "mi" / "washtenaw.pdf",
    "MI-26139": DATA / "inbox" / "y26" / "mi" / "ottawa.pdf",
    "MI-26005": DATA / "inbox" / "y26" / "mi" / "allegan.pdf",
    "FL-12011": DATA / "inbox" / "y26" / "fl" / "broward.pdf",
    "FL-12101": DATA / "inbox" / "y26" / "fl" / "pasco.pdf",
    "FL-12081": DATA / "inbox" / "y26" / "fl" / "manatee.pdf",
    "CA-06001": DATA / "inbox" / "y26" / "ca" / "alameda.html",
    "CA-06097": DATA / "inbox" / "y26" / "ca" / "sonoma.html",
    "CA-06075": DATA / "inbox" / "y26" / "ca" / "sf.html",
    "CA-06085": DATA / "inbox" / "manual" / "MF-188" / "qualified-list-of-local-candidates-9-28-26.pdf",
    "CA-06081": DATA / "inbox" / "manual" / "MF-194" / "52_candidateroster0903.pdf",
    "CA-06061": DATA / "inbox" / "manual" / "MF-195" / "can_watch27.pdf",
    "CA-06029": DATA / "inbox" / "manual" / "MF-190" / "Candidate List.pdf",
    "CA-06019": DATA / "inbox" / "manual" / "MF-191" / "officesupforelectioncandidatelist.pdf",
    "CA-06107": DATA / "inbox" / "manual" / "MF-196" / "NOVGENUOCL.pdf",
    "NJ-34031": DATA / "inbox" / "manual" / "MF-167" / "Updated List of county and Local Candidates Gen 2026.pdf",
    "NJ-34035": DATA / "inbox" / "manual" / "MF-168" / "2026 General Election Candidates 9.2.26 (excel).xlsx",
    "NJ-34041": DATA
    / "inbox"
    / "manual"
    / "MF-169"
    / "2026 general County  Municipal candidates list for distribution.pdf",
    "NJ-34023": DATA / "inbox" / "manual" / "MF-197" / "Official Candidate List 2026 General Election.pdf",
    "NJ-34017": DATA / "inbox" / "y26" / "nj" / "hudson.pdf",
    "CA-06013": DATA / "inbox" / "manual" / "MF-189" / "contra-costa-2026-general-candidates-and-measures.html",
    "NJ-34013": DATA / "inbox" / "y26" / "nj" / "essex.pdf",
    "NJ-34003": DATA / "inbox" / "y26" / "nj" / "bergen.xlsx",
    "NJ-34021": DATA / "inbox" / "y26" / "nj" / "mercer.pdf",
    "WA": DATA / "inbox" / "y26" / "wa" / "votewa_general_2026_by_county.json",
    "ID": DATA / "inbox" / "y26" / "id" / "idaho_2026_general_filed_candidates.json",
    "WI": DATA / "inbox" / "y26" / "wi" / "all_contests_2026-11-03.xlsx",
    "IA": DATA / "inbox" / "y26" / "ia" / "clarity_2026_primary.json",
    "CO": DATA / "inbox" / "y26" / "co" / "clarity_2026_primary.json",
    "AL": DATA / "inbox" / "y26" / "al" / "sample_ballots.pdf",
    "UT-49049": DATA / "inbox" / "y26" / "ut" / "utah_county.pdf",
    "UT-49011": DATA / "inbox" / "y26" / "ut" / "davis.pdf",
    "IL-17197": DATA / "inbox" / "y26" / "il" / "will.pdf",
    "AZ-04021": DATA / "inbox" / "manual" / "MF-203" / "2026 General Election County Local Candidate List (PDF).pdf",
    "MO-29095": DATA / "inbox" / "manual" / "MF-202" / "11.03.26-Notice-of-Election-Sample-Ballot-Poster.pdf",
    "MO-29095-k": DATA / "inbox" / "manual" / "MF-202" / "Sample Ballot 11-26 FINAL WEB REV 9-14-2026.pdf",
    "IL-17097": DATA / "inbox" / "manual" / "MF-201" / "candidatelist9.9.26_202609090842449785.pdf",
    "CO-08005": DATA / "inbox" / "y26" / "co" / "arapahoe.pdf",
    "AZ-04013-s": DATA / "inbox" / "y26" / "az" / "maricopa_schools.json",
    "GA": DATA / "inbox" / "y26" / "ga" / "ev_2026_primaries.json",
    "IL-17089": DATA / "inbox" / "y26" / "il" / "kane.html",
    "IL-17201": DATA / "inbox" / "y26" / "il" / "winnebago.pdf",
    "IL-17119": DATA / "inbox" / "y26" / "il" / "madison.pdf",
    "IL-17143": DATA / "inbox" / "y26" / "il" / "peoria.pdf",
    "IL-17113": DATA / "inbox" / "y26" / "il" / "mclean.pdf",
    "IL-17163": DATA / "inbox" / "manual" / "MF-263" / "2026 General Election Voters Guide.pdf",
    "IL-17019": DATA / "inbox" / "manual" / "MF-263" / "26GILCHM-Chopped 1 (1).pdf",
    "IL-17093": DATA / "inbox" / "manual" / "MF-263" / "GE2026 County Candidate List.pdf",
    "IL-17167": DATA / "inbox" / "y26" / "il" / "sangamon.pdf",
    "MO-29183": DATA / "inbox" / "y26" / "mo" / "stcharles.pdf",
    "MO-29099": DATA / "inbox" / "y26" / "mo" / "jefferson.pdf",
    "MO-29077": DATA / "inbox" / "y26" / "mo" / "greene.pdf",
    "MO-29189": DATA / "inbox" / "y26" / "mo" / "stlouiscounty.pdf",
    "MO-29510": DATA / "inbox" / "y26" / "mo" / "stlouiscity.pdf",
    "OH-39153": DATA / "inbox" / "y26" / "oh" / "summit.pdf",
    "OH-39095": DATA / "inbox" / "y26" / "oh" / "lucas.pdf",
    "OH-39113": DATA / "inbox" / "y26" / "oh" / "montgomery.pdf",
    "IL-17111": DATA / "inbox" / "y26" / "il" / "mchenry.pdf",
    "GA-13121": DATA / "inbox" / "y26" / "ga" / "fulton.pdf",
    "GA-13135": DATA / "inbox" / "y26" / "ga" / "gwinnett.pdf",
    "GA-13067": DATA / "inbox" / "y26" / "ga" / "cobb.pdf",
    "GA-13089": DATA / "inbox" / "y26" / "ga" / "dekalb.pdf",
    "GA-13063": DATA / "inbox" / "y26" / "ga" / "clayton.pdf",
    "GA-13223": DATA / "inbox" / "y26" / "ga" / "paulding.pdf",
    "GA-13153": DATA / "inbox" / "y26" / "ga" / "houston.pdf",
    "GA-13073": DATA / "inbox" / "y26" / "ga" / "columbia.pdf",
    "GA-13245": DATA / "inbox" / "y26" / "ga" / "augusta.pdf",
    "IL-17043": DATA / "inbox" / "y26" / "il" / "dupage.pdf",
    "AZ-04019": DATA / "inbox" / "y26" / "az" / "pima.pdf",
    "NV-32003": DATA / "inbox" / "y26" / "nv" / "clark.pdf",
    "UT-49035": DATA / "inbox" / "y26" / "ut" / "salt_lake.pdf",
    "UT-49057": DATA / "inbox" / "y26" / "ut" / "weber.html",
    "MO-29071": DATA / "inbox" / "manual" / "MF-264" / "2026-Nov Whats on Ballot.pdf",
    "MO-29165": DATA / "inbox" / "manual" / "MF-264" / "Election_Proof_Report_136.pdf",
    "MO-29043": DATA / "inbox" / "manual" / "MF-264" / "SAMPLE-BT-1-5.pdf",
    "MO-29097": DATA / "inbox" / "manual" / "MF-264" / "d9f579_55f86f639dc64d3c87994d6d910ebe21.pdf",
    "WV-54003": DATA / "inbox" / "y26" / "wv" / "berkeley.pdf",
    "WV-54039": DATA / "inbox" / "y26" / "wv" / "kanawha.html",
    "WY-56005": DATA / "inbox" / "y26" / "wy" / "campbell.pdf",
    "WY-56007": DATA / "inbox" / "y26" / "wy" / "carbon.pdf",
    "WY-56035": DATA / "inbox" / "y26" / "wy" / "sublette.pdf",
    "WY-56039": DATA / "inbox" / "y26" / "wy" / "teton.pdf",
    "WY-56011": DATA / "inbox" / "y26" / "wy" / "crook.pdf",
    "WY-56041": DATA / "inbox" / "y26" / "wy" / "uinta.pdf",
    "MT-30031": DATA / "inbox" / "y26" / "mt" / "gallatin.pdf",
    "MT-30013": DATA / "inbox" / "y26" / "mt" / "cascade.pdf",
    "WY-56025": DATA / "inbox" / "y26" / "wy" / "natrona.pdf",
    "MT-30067": DATA / "inbox" / "y26" / "mt" / "park.pdf",
    "TX-48491-rr": DATA / "inbox" / "manual" / "MF-282" / "20261103 November JGSE - COMPREHENSIVE BALLOT.pdf",
    "TX-48201": DATA / "inbox" / "manual" / "MF-282" / "1126_Yellow Sample Ballot ENG-SPA for Website Posting.pdf",
    "MO-29225": DATA / "inbox" / "y26" / "mo" / "webster.pdf",
    "IL-17037": DATA / "inbox" / "y26" / "il" / "dekalb.pdf",
    "TX-48085": DATA / "inbox" / "y26" / "tx" / "wylie.html",
    "UT-49053": DATA / "inbox" / "y26" / "ut" / "washington.pdf",
    "UT-49005": DATA / "inbox" / "y26" / "ut" / "cache.pdf",
    "TX-48491": DATA / "inbox" / "y26" / "tx" / "leander.html",
    "TX-48141": DATA / "inbox" / "manual" / "MF-260" / "4._Canutillo_ISD_Ballot.pdf",
    "FL-12057": DATA / "inbox" / "manual" / "MF-177" / "2026 General Election_All Races Sample Ballot.pdf",
    "FL-12031": DATA / "inbox" / "manual" / "MF-178" / "2026 General All Races_202609210829090089.pdf",
    "FL-12071": DATA / "inbox" / "manual" / "MF-182" / "2026 General Election - All Inclusive Sample Ballot.pdf",
    "FL-12073": DATA / "inbox" / "manual" / "MF-183" / "2026 GEN Election Guide.pdf",
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
TERRITORIES = {"GU": "Guam", "VI": "U.S. Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands"}
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
NE_SOURCES = {
    "MA": (
        "Massachusetts Secretary of the Commonwealth",
        "2026 State Election Candidates",
        "https://www.sec.state.ma.us/divisions/elections/research-and-statistics/2026-state-election-candidates.htm",
        "ne/ma_2026-state-election-candidates.html",
    ),
    "NH": (
        "New Hampshire Secretary of State",
        "2026 General Election Candidate List (09/17/2026)",
        "https://mm.nh.gov/files/uploads/sos/docs/2026-ge-candidate-list.pdf",
        "ne/nh_2026-ge-candidate-list.pdf",
    ),
    "RI": (
        "Rhode Island Secretary of State",
        "Candidates and Declarations of Candidacy, 2026",
        "https://vote.sos.ri.gov/Candidates",
        "ne/ri_Candidates.xlsx",
    ),
    "ME-municipal": (
        "Maine municipal clerks",
        "Portland, Lewiston, Bangor, and South Portland: sample ballots and candidate lists, November 3, 2026",
        "https://www.portlandmaine.gov/",
        "ne/me_municipal.zip",
    ),
    "RI-towns": (
        "Rhode Island Secretary of State",
        "2026 General Election Candidates: Central Falls, Coventry, Hopkinton, Middletown, Narragansett, South Kingstown, Warwick",
        "https://vote.sos.ri.gov/Candidates",
        "ne/ri_towns.zip",
    ),
    "NJ-34011-s": (
        "Cumberland County Clerk",
        "2026 General Election Sample Ballots by municipality",
        "https://cumberlandcountyclerknj.gov/",
        "nj/cumberland_ballots.pdf",
    ),
    "NJ-34029-s": (
        "Ocean County Clerk",
        "2026 General Election Official School Board Candidate Certification Sheet, updated 9-14-2026",
        "https://www.co.ocean.nj.us/WebContentFiles/954890cf-db00-4b5b-b2e3-52d4b843aa40.pdf",
        "nj/ocean_school_certification.pdf",
    ),
    "NJ-34017-s": (
        "Hudson County Clerk",
        "General Election sample ballot, City of Jersey City, November 3, 2026 (attested October 27, 2026)",
        "https://www.hudsoncountyclerk.org/",
        "nj/jersey_city_f09.pdf",
    ),
    "NJ-34017-h-s": (
        "Hudson County Clerk",
        "General Election sample ballots for Bayonne, East Newark, Guttenberg, Hoboken, Kearny, and Secaucus, November 3, 2026 (attested October 27, 2026)",
        "https://www.hudsoncountyclerk.org/",
        "nj/hudson_school_ballots.pdf",
    ),
    "CA-06071-s": (
        "San Bernardino County Registrar of Voters",
        "November 3, 2026 General Election: School District Appointments in Lieu of Election",
        "https://uploads.rov.sbcounty.gov/rov/News/2026/1103/PublicNotice_AppointmentsInLieuSchools.pdf",
        "ca/sb_in_lieu.pdf",
    ),
    "NJ-34037-s": (
        "Sussex County Clerk",
        "2026 School Board Election, unofficial candidate list (updated 7/27/2026)",
        "https://www.sussex.nj.us/DocumentCenter/View/4563/2026-School-Board-Election---UNOFFICIAL-Candidate-list",
        "nj/sussex_school.pdf",
    ),
    "NJ-34023-s": (
        "Middlesex County Clerk",
        "General Election 2026 Official Candidates List, school offices (updated 8/31/2026)",
        "https://www.middlesexcountynj.gov/government/departments/department-of-community-services/office-of-county-clerk/election-services",
        "nj/middlesex.pdf",
    ),
    "NJ-34007": (
        "Camden County Clerk",
        "Official General Election sample ballots, November 3, 2026, Camden County municipalities",
        "https://www.camdencounty.com/service/voting-and-elections/sample-ballots/",
        "nj/camden_sample_ballots.zip",
    ),
    "NJ-34007-s": (
        "Camden County Clerk",
        "Official General Election sample ballots, November 3, 2026, Camden County school districts",
        "https://www.camdencounty.com/service/voting-and-elections/sample-ballots/",
        "nj/camden_sample_ballots.zip",
    ),
    "NJ-34019-s": (
        "Hunterdon County Clerk",
        "Annual School Election, November 3, 2026 (unofficial)",
        "https://www.co.hunterdon.nj.us/DocumentCenter/View/20403/UnOfficial-School-Board-Candidates-2026-PDF",
        "nj/hunterdon_school.pdf",
    ),
    "NJ-34025-s": (
        "Monmouth County Clerk",
        "General Election, November 3, 2026: Official List, Board of Education Candidates (9/1/2026)",
        "https://www.monmouthcountyvotes.gov/wp-content/uploads/2026/09/2026-11-03-General-Election-Candidates-Board-of-Education5.pdf",
        "nj/monmouth_school.pdf",
    ),
    "NJ-34027-s": (
        "Morris County Clerk",
        "Candidates for School Board, General Election, November 3, 2026",
        "https://www.morriscountyclerk.org/files/sharedassets/clerk/v/17/elections/past-results/2026-general-school-candidates.pdf",
        "nj/morris_school.pdf",
    ),
    "NJ-34039-s": (
        "Union County Clerk",
        "Candidate List for the School Board Election, November 3, 2026 (8/12/2026)",
        "https://unioncountyvotes.com/wp-content/uploads/2026/08/School-Board-2026-Candidate-List-8-12-26_.pdf",
        "nj/union_school.pdf",
    ),
    "NJ-34033-s": (
        "Salem County Clerk",
        "2026 General Election Candidates",
        "https://www.salemcountynj.gov/wp-content/uploads/2026/07/2026-Candidate-List-1.pdf",
        "nj/salem_candidates.pdf",
    ),
    "NJ-34041-s": (
        "Warren County Clerk",
        "2026 School Board and Fire Commissioner Candidates",
        "https://www.warrencountyvotes.com/elections/current-election-candidates",
        "nj/warren_school.pdf",
    ),
    "NJ-34001-s": (
        "Atlantic County Clerk",
        "Candidates for the 2026 School Election (8/13/2026)",
        "https://www.atlanticcountyclerk.org/wp-content/uploads/2026/08/Official-2026-School-Board-Candidates-List-8-13-26.pdf",
        "nj/atlantic_school.pdf",
    ),
    "NJ-34031-s": (
        "Passaic County Clerk",
        "List of Local and Regional School Candidates for the General Election, November 3, 2026",
        "https://www.passaiccountynj.org/home/showpublisheddocument/12407",
        "nj/passaic_school.pdf",
    ),
    "NJ-34035-s": (
        "Somerset County Clerk",
        "2026 General Election School Board and Fire Commissioner Candidates",
        "https://www.somersetcountynj.gov/government/elected-officials/county-clerk/election-division",
        "nj/somerset_school.xlsx",
    ),
    "AZ-04019-s": (
        "Pima County School Superintendent",
        "2026 Candidate Filing",
        "https://www.schools.pima.gov/elections/2026-candidate-filing",
        "az/pima_schools.html",
    ),
    "AZ-04015-s": (
        "Mohave County School Superintendent",
        "2026 Candidate Filing",
        "https://www.mohave.gov/departments/school-superintendent/documents/2026-candidate-filing/",
        "az/mohave_schools.html",
    ),
    "AZ-04027-s": (
        "Yuma County Education Service Agency",
        "2026 Statement of Interest Filings (updated 7/7/2026)",
        "https://yumaesa.org/documents/elections/Statement_of_Interest_Filings_copy_6.pdf",
        "az/yuma_schools.pdf",
    ),
    "AZ-04005-s": (
        "Coconino County School Superintendent",
        "2026 Governing Board Candidates",
        "https://www.coconino.az.gov/3224/2026-Governing-Board-Candidates",
        "az/coconino_schools.html",
    ),
    "AZ-04017-s": (
        "Navajo County School Superintendent",
        "2026 Filed Candidates (updated July 7, 2026)",
        "https://www.navajocountyaz.gov/816/2026-Filed-Candidates",
        "az/navajo_schools.html",
    ),
    "AZ-04007-s": (
        "Gila County School Superintendent",
        "2026 Candidate Statements of Interest (updated 7/27/2026)",
        "https://www.gilacountyaz.gov/government/school_superintendent/school_district_maps/index.php",
        "az/gila_schools.html",
    ),
    "AZ-04001-s": (
        "Apache County School Superintendent",
        "2026 School Board Candidates",
        "https://www.apachecountyaz.gov/School-Board-Elections",
        "az/apache_schools.pdf",
    ),
    "AZ-04003-s": (
        "Cochise County School Superintendent",
        "2026 Candidate Filing (7/9/26)",
        "https://www.cochise.az.gov/",
        "az/cochise_schools.pdf",
    ),
    "AZ-04023-s": (
        "Santa Cruz County School Superintendent",
        "2026 Candidate Filing (7/29/26)",
        "https://www.santacruzcountyaz.gov/624/Elections-Information",
        "az/santa-cruz_schools.pdf",
    ),
    "AZ-04011": (
        "Greenlee County Elections Department",
        "2026 Official List of Candidates",
        "https://greenlee.az.gov/ova_dep/elections/",
        "az/greenlee.pdf",
    ),
    "AZ-04009-s": (
        "Graham County Elections",
        "Candidates and Propositions, 2026 General Election",
        "https://www.graham.az.gov/",
        "az/graham_schools.html",
    ),
    "AZ-04012-s": (
        "La Paz County Elections Department",
        "2026 General Election Local Candidates (8-17-26)",
        "https://lapaz.gov/",
        "az/la_paz_local.pdf",
    ),
    "AZ-04021-s": (
        "Pinal County School Office",
        "2026 Election Candidate Filing Status",
        "https://www.pinalcso.org/",
        "az/pinal_schools.html",
    ),
    "AZ-04025-s": (
        "Yavapai County Education Service Agency",
        "Election Services, 2026 school district candidate races",
        "https://www.yavapaicoesa.gov/election-services",
        "az/yavapai_schools.html",
    ),
    "MI-26145": (
        "Saginaw County Clerk",
        "Unofficial Candidate List, November 3, 2026 General Election",
        "https://www.saginawcounty.com/",
        "mi/saginaw.pdf",
    ),
    "MI-26025": (
        "Calhoun County Clerk",
        "Official Candidate List, 11/03/2026 State General",
        "https://www.calhouncountymi.gov/",
        "mi/calhoun.pdf",
    ),
    "MI-26111": (
        "Midland County Clerk",
        "Sample Ballots, November 3, 2026 General Election",
        "https://www.co.midland.mi.us/",
        "mi/midland.pdf",
    ),
    "MI-26087": (
        "Lapeer County Clerk",
        "Lapeer County Schools Candidates for Member of Board of Education, November 3, 2026",
        "https://www.lapeercounty.org/",
        "mi/lapeer_schools.doc",
    ),
    "MI-26107": (
        "Mecosta County Clerk",
        "Official Candidate List, 11/03/2026 State General",
        "https://www.mecostacounty.org/",
        "mi/mecosta.pdf",
    ),
    "MI-26023": (
        "Branch County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.countyofbranch.com/",
        "mi/branch.pdf",
    ),
    "MI-26027": (
        "Cass County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.casscountymi.org/",
        "mi/cass.pdf",
    ),
    "MI-26035": (
        "Clare County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.clarecounty-mi.gov/",
        "mi/clare.pdf",
    ),
    "MI-26037": (
        "Clinton County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.clinton-county.org/",
        "mi/clinton.pdf",
    ),
    "MI-26057": (
        "Gratiot County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.gratiotmi.com/",
        "mi/gratiot.pdf",
    ),
    "MI-26059": (
        "Hillsdale County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.co.hillsdale.mi.us/",
        "mi/hillsdale.pdf",
    ),
    "MI-26069": (
        "Iosco County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.ioscocounty.org/",
        "mi/iosco.pdf",
    ),
    "MI-26123": (
        "Newaygo County Clerk",
        "2026 General Election Candidates",
        "https://www.newaygocountymi.gov/",
        "mi/newaygo.html",
    ),
    "MI-26133": (
        "Osceola County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.osceolacountymi.gov/",
        "mi/osceola.pdf",
    ),
    "MI-26149": (
        "St. Joseph County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.stjosephcountymi.gov/",
        "mi/st-joseph.pdf",
    ),
    "MI-26165": (
        "Wexford County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.wexfordcounty.org/",
        "mi/wexford.pdf",
    ),
    "MI-26143": (
        "Roscommon County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.roscommoncountymi.gov/",
        "mi/roscommon.pdf",
    ),
    "MI-26155": (
        "Shiawassee County Clerk",
        "Candidate List, November 3, 2026 General Election",
        "https://www.shiawasseecounty.org/",
        "mi/shiawassee.pdf",
    ),
    "MI-26159": (
        "Van Buren County Clerk",
        "Non-Partisan Candidates, November 3, 2026 General Election",
        "https://www.vanburencountymi.gov/",
        "mi/van-buren.xlsx",
    ),
    "MI-26073": (
        "Isabella County Clerk",
        "Sample Ballots, November 3, 2026 General Election",
        "https://www.isabellacounty.org/",
        "mi/isabella.pdf",
    ),
}
for county, url in {
    "Nassau": "https://www.nassaucountyny.gov/DocumentCenter/View/54522/Candidate-List-as-of-09182026",
    "Erie": "https://elections.erie.gov/pdfs/2026%20General%20Candidates%20List%20For%20Web.pdf",
    "Monroe": "https://www.monroecounty.gov/files/boe/Documents/GE26%20Candidates%20List%20-%20CERTIFICATION%2020260911%20-%20WEB.pdf",
    "Onondaga": "https://onondaga.gov/elections/wp-content/uploads/sites/191/2026/09/GE26-Candidates-List-NO-ADDRESSES-3.pdf",
    "Albany": "https://content.civicplus.com/api/assets/cd5f13cf-73f6-4151-bf22-82fa793f04e1",
    "Dutchess": "https://elections.dutchessny.gov/wp-content/uploads/2026/09/Candidate-Notice-Website-1.pdf",
    "Broome": "https://www.broomevotes.com/_files/ugd/be9a23_1289c9ca6ac143fe92ebbc505c8667c7.pdf",
    "Rensselaer": "https://www.rensco.com/DocumentCenter/View/6785/Rensselaer-County-Candidates-List-General-2026",
    "Chautauqua": "https://chautauquacountyny.gov/sites/default/files/inline-files/GE26%20Candidate%20Certification%20for%20Website.pdf",
    "Putnam": "https://boe.putnamcountyny.gov/wp-content/uploads/2026/09/2026-LIST-CANDIDATES-PROPOSITIONS-GENERAL.pdf",
    "Cattaraugus": "https://www.cattco.gov/sites/default/files/2026-09/Cattaraugus-Certification-GE26-signed--opt.pdf",
    "Niagara": "https://elections.niagaracounty.gov/wp-content/uploads/2026/09/20260915163539-ge26-ballot-order-offices-and-candidates-for-november-3-2026.pdf",
    "Ulster": "https://elections.ulstercountyny.gov/wp-content/uploads/2026/10/ULS-Ballots.pdf",
    "Warren": "https://www.warrencountyny.gov/sites/default/files/boe/election/2026/2026%20Certification%20of%20Candidates%20and%20propositions%20-%20General(2).pdf",
    "Jefferson": "https://www.jeffersoncountyny.gov/media/Elections/2026%20General%20Election%20Certification/GE26%20Ballot%20Certification%20-%20executed.pdf",
    "Hamilton": "https://www.hamiltoncountyny.gov/sites/default/files/2025-09/2026%20Petition%20Report%20Final.pdf",
    "Westchester": "https://citizenparticipation.westchestercountyny.gov/images/stories/pdfs/2026/2026-candidate-list-no-address.pdf",
    "Herkimer": "https://herkimercountyny.gov/download/document.php?docid=5766",
    "Orange": "https://www.orangecountyny.gov/DocumentCenter/View/38730",
    "Rockland": "https://ny.rocklandcountyny.gov/home/showpublisheddocument/3957/639268124361400000",
    "Suffolk": "https://suffolkcountyny.gov/Departments/BOE/2026-General-Election-Candidate-List",
    "Ontario": "https://www.ontariocountyny.gov/DocumentCenter/View/64056",
    "Tompkins": "https://www.tompkinscountyny.gov/",
    "Columbia": "https://elections.columbiacountyny.com/",
    "Oneida": "https://oneidacountynyvotes.gov/",
    "Washington": "https://www.washingtoncountyny.gov/DocumentCenter/View/31734",
    "Chenango": "https://www.chenangocountyny.gov/DocumentCenter/View/7428",
    "Clinton": "https://www.clintoncountyny.gov/",
    "Steuben": "https://www.steubencountyny.gov/469/Board-of-Elections",
    "Allegany": "https://www.alleganyco.gov/wp-content/uploads/2026-November-General-Election-Candidates-B.pdf",
    "Chemung": "https://www.chemungcountyny.gov/DocumentCenter/View/20773/2026---Offices-Open-for-Election-and-the-Candidates-",
    "Schoharie": "https://www.schohariecounty-ny.gov/Documents/Departments/Board%20of%20Elections/2026%20Election%20Documents/Candidates%20for%20Website%20GE26.pdf",
    "Otsego": "https://www.otsegocounty.com/departments/a-c/board_of_elections/candidates.php",
    "Seneca": "https://www.senecacountyny.gov/163/Board-of-Elections",
    "Oswego": "https://www.oswegocountyny.gov/Candidates%20GE26.docx",
    "Montgomery": "https://www.montgomerycountyny.gov/documents/boe/FiledPetitions.pdf",
    "Essex": "https://www.co.essex.ny.us/",
    "Schuyler": "https://www.schuylercounty.us/DocumentCenter/View/16302/2026-GE-Candidates-List",
    "Sullivan": "https://www.sullivanny.gov/sites/default/files/departments/boe/Sullivan%20County%20GE26%20Ballot%20Certification%20for%20website.pdf",
    "Delaware": "https://www.co.delaware.ny.us/departments/elections/",
    "Madison": "https://www.madisoncounty.ny.gov/3291/Sample-Ballots",
    "Livingston": "https://www.livingstoncountyny.gov/685/Sample-Ballots",
    "Saratoga": "https://www.saratogacountyny.gov/departments/board-of-elections/",
    "Lewis": "https://lewiscountyny.gov/public_notices/sample-ballots-general-election/",
    "Orleans": "https://www.orleanscountyny.gov/government/elections/sample_ballots.php",
    "Tioga": "https://www.tiogacountyny.com/departments/board-of-elections/",
    "Greene": "https://greenecountyny.gov/sample-ballots/",
    "Wyoming": "https://www.wyomingcountyny.gov/912/Sample-Ballots",
    "Wayne": "https://waynecountyny.gov/277/Elections",
    "Cayuga": "https://www.cayugacounty.gov/1594",
    "Cortland": "https://www.cortlandcountyny.gov/1301/Sample-Ballots-for-Upcoming-Elections",
    "Schenectady": "https://www.dailygazette.com/classifieds/legals/ad-2641098/pdfdisplayad_126cdecb-769f-568c-9e08-1a979c3b6418.html",
    "Genesee": "https://www.geneseeny.gov/Department-Content/Board-of-Elections/Election-Day-Sample-Ballots",
    "Yates": "https://www.yatescountyny.gov/318/Sample-Ballots",
    "Franklin": "https://www.franklincountyny.gov/departments/elections/",
    "Fulton": "https://www.fultoncountyny.gov/departments/board-of-elections",
    "St. Lawrence": "https://www.stlawco.org/Board-of-Elections",
}.items():
    NE_SOURCES[f"NY-{county}"] = (
        f"{county} County Board of Elections",
        "2026 General Election: Candidates (unofficial)"
        if county == "Westchester"
        else "2026 General Election: Candidates",
        url,
        f"ny/{county.lower()}.{'html' if county in ('Suffolk', 'Otsego', 'Seneca', 'Schenectady') else 'docx' if county == 'Oswego' else 'zip' if county in ('Delaware', 'Madison', 'Livingston', 'Saratoga', 'Lewis', 'Orleans', 'Tioga', 'Greene', 'Wyoming', 'Wayne', 'Cayuga', 'Cortland', 'Genesee', 'Yates', 'Franklin', 'Fulton', 'St. Lawrence') else 'pdf'}",
    )
for fips, county in {
    "32001": "Churchill",
    "32005": "Douglas",
    "32007": "Elko",
    "32009": "Esmeralda",
    "32011": "Eureka",
    "32013": "Humboldt",
    "32015": "Lander",
    "32017": "Lincoln",
    "32019": "Lyon",
    "32021": "Mineral",
    "32023": "Nye",
    "32027": "Pershing",
    "32029": "Storey",
    "32031": "Washoe",
    "32033": "White Pine",
    "32510": "Carson City",
}.items():
    NE_SOURCES[f"NV-{fips}"] = (
        "Nevada Secretary of State",
        "2026 General Election: Statewide Certified List of Candidates",
        "https://www.nvsos.gov/sos/elections/election-information/2026-election-information",
        "nv/certified.pdf",
    )
NE_SOURCES["AR-05119"] = (
    "Pulaski County Board of Election Commissioners",
    "Notice of Election, November 3, 2026 General Election",
    "https://www.votepulaskiar.gov/",
    "ar/pulaski.pdf",
)
NE_SOURCES["AR-05143"] = (
    "Washington County Election Commission",
    "November 3, 2026 General Election Information",
    "https://www.washingtoncountyar.gov/government/departments-a-e/election-commission/november-3-2026-general-election-information",
    "ar/washington.html",
)
for page in sorted((DATA / "inbox" / "y26" / "la").glob("*.html")):
    NE_SOURCES[f"LA-{page.stem}"] = (
        "Louisiana Secretary of State",
        "Candidate Inquiry, November 3, 2026 Election",
        f"https://voterportal.sos.la.gov/CandidateInquiry/ParishCandidate/Index?electionId=344&electionDate=11%2F03%2F2026#{page.stem}",
        f"la/{page.name}",
    )
for fips in sorted({line.split("\t")[0] for line in Path(__file__).with_name("local_2026_states.tsv").read_text().splitlines() if line.startswith("ND-")}):
    NE_SOURCES[fips] = (
        "North Dakota Secretary of State",
        "Candidate List, November 3, 2026 General Election",
        f"https://vip.sos.nd.gov/candidatelist.aspx?eid=348#{fips}",
        "nd/candidates.html",
    )
for fips, county, page in (
    ("01115", "St. Clair", "StClair-Sample"),
    ("01121", "Talladega", "Talladega_Sample"),
    ("01093", "Marion", "Marion_Sample"),
    ("01087", "Macon", "Macon_Sample"),
    ("01067", "Henry", "Henry_Sample"),
    ("01131", "Wilcox", "Wilcox_Sample"),
    ("01073", "Jefferson", "Jefferson-Sample"),
    ("01097", "Mobile", "Mobile-Sample"),
    ("01089", "Madison", "Madison-Sample"),
    ("01101", "Montgomery", "Montgomery-Sample"),
    ("01117", "Shelby", "Shelby-Sample"),
    ("01003", "Baldwin", "Baldwin-Sample"),
    ("01125", "Tuscaloosa", "Tuscaloosa-Sample"),
    ("01081", "Lee", "Lee-Sample"),
):
    NE_SOURCES[f"AL-{fips}"] = (
        "Alabama Secretary of State",
        f"2026 General Election Sample Ballot, {county} County, November 3, 2026",
        f"https://www.sos.alabama.gov/sites/default/files/sample-ballots/2026/gen/{page}.pdf",
        f"al/{county.replace('. ', '').lower()}.pdf" if county != "St. Clair" else "al/stclair.pdf",
    )
for fips in sorted({line.split("\t")[0] for line in Path(__file__).with_name("local_2026_states.tsv").read_text().splitlines() if line.startswith("SD-")}):
    NE_SOURCES[fips] = (
        "South Dakota Secretary of State",
        "Candidate List, November 3, 2026 General Election",
        f"https://vip.sdsos.gov/candidatelist.aspx?eid=774#{fips}",
        "sd/candidates.csv",
    )
for key, publisher, title, url, local in (
    ("MO-29037", "Cass County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://www.casscounty.com/DocumentCenter/View/4496/November-2026-Sample-Ballotpdf", "mo/cass.pdf"),
    ("MO-29021", "Buchanan County Clerk", "Sample Ballot Style 1, General Election, November 3, 2026", "https://www.co.buchanan.mo.us/media/Document%20Center/County%20Clerk/Nov%203%202026%20Sample%20Ballots/Nov%203%202026%20Ballot%20Style%201.pdf", "mo/buchanan.pdf"),
    ("MO-29031", "Cape Girardeau County Clerk", "Publication of Sample Ballot, General Election, November 3, 2026", "https://www.capecounty.us/wp-content/uploads/2026/09/Cape-Girardeau-Publication-110326.pdf", "mo/cape.pdf"),
    ("MO-29051", "Cole County Clerk", "Combined Sample Ballot, General Election, November 3, 2026", "https://colecounty.org/DocumentCenter/View/14428/November-3-2026-Cole-County-Combined-Sample-Ballot", "mo/cole.pdf"),
    ("MO-29187", "St. Francois County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://www.sfcgov.org/download/clerk/elections/Sample-Ballot-General-Election-November-3-2026.pdf", "mo/stfrancois.pdf"),
    ("MO-29213", "Taney County Clerk", "Official Sample Compilation Ballot, General Election, November 3, 2026", "https://media.taneycounty.org/pdf.php?id=5758", "mo/taney.pdf"),
    ("MO-29161", "Phelps County Clerk", "Sample Ballots, General Election, November 3, 2026", "https://www.phelpscounty.org/wp-content/uploads/2026/09/Phelps-Samples-To-Print-2026.pdf", "mo/phelps.pdf"),
    ("IL-17095", "Knox County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://cms2.revize.com/revize/knoxcounty/Specimen%20Ballot%20-%20GE2026.pdf", "il/knox.pdf"),
    ("IL-17081", "Jefferson County Clerk", "Official Ballot, General Election, November 3, 2026", "https://cms2.revize.com/revize/jeffersoncounty/Document%20center/County%20Clerks%20Docs/Elections/2026%20General/November%202026%20PUBLICATION%20Ballot.pdf", "il/jefferson.pdf"),
    ("IL-17027", "Clinton County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://clintonco.illinois.gov/wp-content/uploads/2026-GE-ballot-pages.pdf", "il/clinton.pdf"),
    ("MO-29169", "Pulaski County Clerk", "Combined Sample Ballot, General Election, November 3, 2026", "https://www.pulaskivotes.org/p/what-on-ballot.html", "mo/pulaski.pdf"),
    ("MO-29027", "Callaway County Clerk", "Official Ballot Style 1, General Election, November 3, 2026", "https://callawaycountyclerk.com/wp-content/uploads/2026/09/BP-70C31_20260921_112602.pdf", "mo/callaway.pdf"),
    ("IL-17115", "Macon County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://maconcounty.illinois.gov/download/specimen-ballot/", "il/macon.pdf"),
    ("MO-29101", "Johnson County Clerk", "General Election November 3, 2026 candidates and ballot measures", "https://www.jocomo.gov/m/newsflash/Home/Detail/64", "mo/johnson.html"),
    ("UT-49021", "Iron County Clerk", "2026 General Election Certification", "https://www.utah.gov/pmn/files/1484019.pdf", "ut/iron.pdf"),
    ("UT-49019", "Grand County Clerk/Auditor", "2026 General Election Certification", "https://www.grandcountyutah.net/DocumentCenter/View/25854/2026-Grand-General-Election-Certification", "ut/grand.pdf"),
    ("UT-49015", "Emery County Clerk/Auditor", "Public Notice: 2026 General Election Certification", "https://emery.utah.gov/wp-content/uploads/2026/08/CandidateCertification.pdf", "ut/emery.pdf"),
    ("UT-49001", "Beaver County Clerk/Auditor", "Notice of 2026 General Election Candidates", "https://www.utah.gov/pmn/files/1482333.pdf", "ut/beaver.pdf"),
    ("UT-49027", "Millard County Clerk", "General Election Certification of County Candidates, November 3, 2026", "https://www.utah.gov/pmn/files/1481673.pdf", "ut/millard.pdf"),
    ("UT-49045", "Tooele County Clerk", "2026 General Election Certification", "https://www.utah.gov/pmn/files/1482453.pdf", "ut/tooele.pdf"),
    ("UT-49003", "Box Elder County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.boxeldercountyut.gov/DocumentCenter/View/2460/2026-General-Election-Sample-Publication-Ballot-PDF", "ut/boxelder.pdf"),
    ("UT-49039", "Sanpete County Clerk", "2026 General Election Certification", "https://www.utah.gov/pmn/files/1483373.pdf", "ut/sanpete.pdf"),
    ("UT-49013", "Duchesne County Clerk", "Certification of County Candidates for General Election, November 3, 2026", "https://www.utah.gov/pmn/files/1482457.pdf", "ut/duchesne.pdf"),
    ("UT-49047", "Uintah County Clerk-Auditor", "Official Sample Ballot, General Election, November 3, 2026", "https://www.utah.gov/pmn/files/1484353.pdf", "ut/uintah.pdf"),
    ("UT-49051", "Wasatch County Clerk/Auditor", "2026 General Election Certification", "https://www.utah.gov/pmn/files/1483613.pdf", "ut/wasatch.pdf"),
    ("UT-49041", "Sevier County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.utah.gov/pmn/files/1494267.pdf", "ut/sevier.pdf"),
    ("UT-49043", "Summit County Clerk", "2026 General Election Certification", "https://www.utah.gov/pmn/files/1483415.pdf", "ut/summit.pdf"),
    ("UT-49023", "Juab County Clerk", "2026 General Election Certification", "https://juabcounty.gov/wp-content/uploads/2026/09/2026_general_certification.pdf", "ut/juab.pdf"),
    ("UT-49025", "Kane County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://www.utah.gov/pmn/files/1485015.docx", "ut/kane.docx"),
    ("UT-49017", "Garfield County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.garfield.utah.gov/files/assets/garfieldcout/v/1/documents/auditor-clerk/26gutgar_publication_2_watermark.pdf", "ut/garfield.pdf"),
    ("TX-48201-harris-sd", "Harris County Clerk", "Sample ballot, November 3, 2026 General and Special Elections (special districts)", "https://www.harrisvotes.com/", "tx/harris_sample.pdf"),
    ("TX-48085-collin-sd", "Collin County Elections Administration", "November 3, 2026 General Election Sample Ballot (special districts)", "https://www.collincountytx.gov/elections", "tx/collin.pdf"),
    ("TX-48453-sd", "Travis County Clerk", "November 3, 2026 General Election Sample Ballot (special districts)", "https://votetravis.gov/wp-content/uploads/G26-Bedsheet-Ballot-Corrected-Final2.pdf", "tx/travis.pdf"),
    ("WA-53011-sd", "Clark County Elections", "November 3, 2026 General Election Sample Ballot (special districts)", "https://clark.wa.gov/elections", "wa/clark.pdf"),
    ("WA-53057-sd", "Skagit County Elections", "November 3, 2026 General Election Sample Ballot (special districts; the ballot prints PUD Commissioner Position 3, the voters' pamphlet prints Position 1)", "https://www.skagitcounty.net/elections", "wa/skagit.pdf"),
    ("WA-53063-sd", "Spokane County Elections", "November 3, 2026 General Election Sample Ballot (special districts)", "https://www.spokanecounty.gov/elections", "wa/spokane.pdf"),
    ("WA-53067-sd", "Thurston County Auditor", "November 3, 2026 General Election Sample Ballot (special districts)", "https://www.thurstonvotes.org", "wa/thurston.pdf"),
    ("WA-53073-sd", "Whatcom County Auditor", "November 3, 2026 General Election Sample Ballot (special districts)", "https://www.whatcomcounty.us/elections", "wa/whatcom.pdf"),
    ("TX-48085-collin-cities", "Collin County Elections Administration", "November 3, 2026 General Election Sample Ballot (Anna, Josephine, Lavon, Lowry Crossing, Princeton, Weston, and Wylie)", "https://www.collincountytx.gov/elections", "tx/collin.pdf"),
    ("TX-48453-nov-cities", "Travis County Clerk", "November 3, 2026 General Election Sample Ballot (Jonestown, Lago Vista, Manor, Sunset Valley, and Volente)", "https://votetravis.gov/", "tx/travis_nov_cities.pdf"),
    ("TX-48453-tc", "Travis County Clerk", "November 3, 2026 General Election Sample Ballot (Del Valle and Manor ISD)", "https://votetravis.gov/wp-content/uploads/G26-Bedsheet-Ballot-Corrected-Final2.pdf", "tx/travis.pdf"),
    ("TX-48221", "Tolar Independent School District", "November 2026 Tolar ISD Trustee Candidates", "https://www.tolarisd.org/district-information/school-board/elections/election-candidates", "tx/tolar.html"),
    ("TX-48453-lv", "Lago Vista Independent School District", "Order of Cancellation of Trustee Election, November 3, 2026", "https://www.lagovistaisd.net/upload/page/0037/docs/2026%20Trustee%20Election/2026%20Order%20of%20Cancellation%20Trustee.pdf", "tx/lago_vista.pdf"),
    ("TX-48355-fb", "Flour Bluff Independent School District", "FBISD Board of Trustee Election, November 3, 2026", "https://flourbluffschools.net/board-elections/", "tx/flour_bluff.html"),
    ("IL-17049", "Effingham County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://effinghamcountyil.gov/wp-content/uploads/2026/09/FULL-SPECIMEN-General-Election-November-3-2026.pdf", "il/effingham.pdf"),
    ("IL-17105", "Livingston County Clerk", "Publication Specimen Ballot, General Election, November 3, 2026", "https://cms3.revize.com/revize/livingstocountyil/Documents/Department/Election/2026%20General%20Election/26nov3illivingston-PublicationSpecimenBallot%20(1).pdf", "il/livingston.pdf"),
    ("IL-17085", "Jo Daviess County Clerk", "11.03.26 General Candidates", "https://www.jodaviesscountyil.gov/DocumentCenter/View/1018/11-03-26-General-Election-Candidate-list-PDF", "il/jodaviess.pdf"),
    ("IL-17157", "Randolph County Clerk", "General Election Ballot, November 3, 2026", "https://randolphcountyil.gov/wp-content/uploads/2026-GE-RANDOLPH.pdf", "il/randolph.pdf"),
    ("IL-17165", "Saline County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://salinecounty.illinois.gov/wp-content/uploads/Sample-Ballot-11.3.26.pdf", "il/saline.pdf"),
    ("MO-29007", "Audrain County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.audraincounty.org/s/Audrain-Publication-110326.pdf", "mo/audrain.pdf"),
    ("MO-29015", "Benton County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://www.bentoncomo.com/documents/elections/2026_nov_3_sample%20ballot.pdf", "mo/benton.pdf"),
    ("MO-29131", "Miller County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://cms2.revize.com/revize/millercounty/Miller%20Publication%20110326.pdf", "mo/miller.pdf"),
    ("MO-29177", "Ray County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://raycountymo.com/wp-content/uploads/2026/09/Ray-Publication-11.03.26.pdf", "mo/ray.pdf"),
    ("MO-29217", "Vernon County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://vernoncountymo.org/wp-content/uploads/Sample-Ballot-2.pdf", "mo/vernon.pdf"),
    ("MO-29119", "McDonald County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://mcdonaldcountymo.gov/wp-content/uploads/simple-file-list/Elections/2026/November-3-2026-Publication-Ballot.pdf", "mo/mcdonald.pdf"),
    ("MO-29029", "Camden County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://camdencountymo.gov/wp-content/uploads/Camden-Publication-110326.pdf", "mo/camden.pdf"),
    ("MO-29091", "Howell County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://howellcountymo.gov/wp-content/uploads/2026/09/November-2026-Howell-Publication-110326.pdf", "mo/howell.pdf"),
    ("MO-29109", "Lawrence County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.lawrencecountymo.org/_files/ugd/e487e4_0c15257fb8ac4707ba145896818a89c3.pdf", "mo/lawrence.pdf"),
    ("MO-29009", "Barry County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://8396ea3c-e47f-4ac8-8b61-6db9ad914af3.filesusr.com/ugd/0c3f9f_5e1167ae7da9495dad85e4909a8de27d.pdf", "mo/barry.pdf"),
    ("MO-29167", "Polk County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://polkcountymo.gov/wp-content/uploads/2026/09/BT-1-Countywide.pdf", "mo/polk.pdf"),
    ("MO-29209", "Stone County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://stonecountyclerk.com/media/uploads/downloads/Stone_Publication_110326.pdf", "mo/stone.pdf"),
    ("MO-29069", "Dunklin County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://dunklincounty.gov/wp-content/uploads/2026/09/Sample-ballot-1.pdf", "mo/dunklin.pdf"),
    ("MO-29201", "Scott County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.scottcountymo.com/cmsAdmin/uploads/2/november-scott-publication-110326.pdf", "mo/scott.pdf"),
    ("MO-29113", "Lincoln County Clerk", "Election Authority's Certification, General Election, November 3, 2026", "https://www.lincolncomo.gov/sample-ballots", "mo/lincoln.pdf"),
    ("MO-29145", "Newton County Clerk", "Official General Election Ballot (sample), Newton County, November 3, 2026", "https://www.ncclerk.org/electionsvoters/", "mo/newton.pdf"),
    ("IL-17117", "Macoupin County Clerk", "Candidate List, 2026 General Election", "https://www.macoupinvotes.gov/election-information/sample-ballot/?electionId=23", "il/macoupin_candidates.html"),
    ("NE-31153", "Sarpy County Election Commission", "Notice of General Election, November 3, 2026", "https://www.sarpy.gov/DocumentCenter/View/9683", "ne/sarpy_notice.pdf"),
    ("KS-20121", "Miami County Clerk", "Official Candidate List for General 2026", "https://www.miamicountyks.gov/DocumentCenter/View/15339", "ks/miami.pdf"),
    ("KS-20079", "Harvey County Election Office", "Notice of Candidates Filed, General Election, November 3, 2026", "https://www.hvcoksvote.gov/_files/ugd/4d48e8_7e8cb25c163f48aeaca28c682929de59.pdf", "ks/harvey.pdf"),
    ("IL-17179", "Tazewell County Clerk", "Official Ballot, General Election, November 3, 2026", "https://tazewell-il.gov/wp-content/uploads/2026/09/Binder1.pdf", "il/tazewell_ge.pdf"),
    ("MO-29141", "Morgan County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://www.morgancountymo.gov/plugins/show_image.php?id=399", "mo/morgan.pdf"),
    ("MO-29105", "Laclede County Clerk", "Sample Ballot Style 1 (District 141), General Election, November 3, 2026", "https://lacledecountyclerk.org/whats-on-the-ballot/", "mo/laclede.pdf"),
    ("IL-17091", "Kankakee County Clerk", "Master Specimen Ballot, General Election, November 3, 2026", "https://www.kankakeecountyclerk.gov/wp-content/uploads/Master-Specimen-Ballot.pdf", "il/kankakee.pdf"),
    ("TX-48339-cisd", "Conroe ISD", "Trustee Elections, November 3, 2026 General Election", "https://www.conroeisd.net/page/trustee-elections/", "tx/conroe.html"),
    ("NM-35013", "Doña Ana County Clerk", "2026 General Election Super Sample Ballot", "https://www.donaanacountyelections.com/wp-content/uploads/2026/10/Super-Sample_GN-2026.pdf", "nm/donaana.pdf"),
    ("WV-54061", "Monongalia County Clerk", "List of Contests and Candidates on the November 3, 2026 General Election Ballot", "https://www.monongaliacountyclerk.com/myfiles/elections/2026General/List_of_Contests_and_Candidates_on_the_26G_ballot.pdf", "wv/monongalia.pdf"),
    ("HI-15009", "Hawaii Office of Elections", "Candidate Filing List, 2026 Elections", "https://elections.hawaii.gov/candidate-filing/", "hi/candidates.csv"),
    ("HI-15007", "Hawaii Office of Elections", "Candidate Filing List, 2026 Elections", "https://elections.hawaii.gov/candidate-filing/", "hi/candidates.csv"),
    ("WI-55039", "Wisconsin Elections Commission", "All Contests and Candidates, November 3, 2026", "https://elections.wi.gov/elections-voting/2026/general", "wi/contests.xlsx"),
    ("MO-29047", "Clay County Election Board", "General Election Sample Ballot, November 3, 2026", "https://www.voteclaycountymo.gov/elections", "mo/clay.pdf"),
    ("WY-56003", "Big Horn County Clerk", "Big Horn County 2026 General Candidate Roster", "https://www.bighorncountywy.gov/images/Big_Horn_County_GENERAL_Candidate_Listpdf.pdf", "wy/bighorn.pdf"),
    ("WY-56005", "Campbell County Clerk", "Campbell County 2026 General Election Candidates", "https://www.campbellcountywy.gov/DocumentCenter/View/28182", "wy/campbell.pdf"),
    ("WY-56015", "Goshen County Clerk", "Goshen County 2026 General Candidate Filing List", "https://www.goshencounty.org/DocumentCenter/View/1524/2026-General-Candidate-Filing-List-PDF", "wy/goshen.pdf"),
    ("IL-17073", "Henry County Clerk", "Henry County General Election 2026 Sample Ballot", "https://www.henrycty.com/DocumentCenter/View/1202/HENRY-COUNTY-GENERAL-ELECTION-2026-SAMPLE-BALLOT", "il/henry.pdf"),
    ("IL-17177", "Stephenson County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://www.stephensoncountyil.gov/26nov3ilstephenson-Publication%20(1)%20(1).pdf", "il/stephenson.pdf"),
    ("IL-17055", "Franklin County Clerk", "2026 General Election Ballot", "https://franklincountyil.gov/wp-content/uploads/2026/10/2026-GE-FRANKLIN-Updated.pdf", "il/franklin.pdf"),
    ("WV-54077", "Preston County Clerk", "Sample Ballot, General Election 2026", "https://www.prestoncountyclerkwv.com/wp-content/uploads/2026/10/SAMPLE-BALLOT-Preston-WV-General-2026.pdf", "wv/preston.pdf"),
    ("IL-17199", "Williamson County Clerk", "Official Ballot, General Election, November 3, 2026", "https://www.williamsoncountyil.gov/wp-content/uploads/2026/09/sample-ballot-001.pdf", "il/williamson.pdf"),
    ("WY-56029", "Park County Clerk", "2026 Park County general election partisan and municipal candidate filings", "https://parkcounty-wy.gov/wp-content/uploads/2026/10/General-Candidate-Filings-10.13.26.pdf", "wy/park.pdf"),
    ("IL-17011", "Bureau County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Bureau/2026-General-Election-Publication.pdf", "il/bureau.pdf"),
    ("IL-17021", "Christian County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Christian/Christian-County-SPECIMEN-BALLOT-November-3-2026.pdf", "il/christian.pdf"),
    ("IL-17041", "Douglas County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Douglas/Publication.pdf", "il/douglas.pdf"),
    ("IL-17051", "Fayette County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Fayette/publication_2026_general_election_ballot.pdf", "il/fayette.pdf"),
    ("IL-17057", "Fulton County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Fulton/2026-General-Election-SPECIMENBALLOT.pdf", "il/fulton.pdf"),
    ("IL-17075", "Iroquois County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Iroquois/SPECIMENBALLOT_Style1_English_8.5x14_Paper.pdf", "il/iroquois.pdf"),
    ("IL-17083", "Jersey County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Jersey/General-2026-Publication-Ballot.pdf", "il/jersey.pdf"),
    ("IL-17103", "Lee County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Lee/Speciman Ballot.11.03.26.pdf", "il/lee.pdf"),
    ("IL-17107", "Logan County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Logan/26nov3illogan-Publication.pdf", "il/logan.pdf"),
    ("IL-17135", "Montgomery County Clerk", "Candidate list, General Election, November 3, 2026", "manual-fetch:MF-354/Montgomery/GeneralElectionCandidates3-11-2026.pdf", "il/montgomery_candidates.pdf"),
    ("IL-17145", "Perry County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Perry/2026-General-Election-Sample-Ballot.pdf", "il/perry.pdf"),
    ("IL-17173", "Shelby County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Shelby/2026 Nov 3 countywide sample ballot.pdf", "il/shelby.pdf"),
    ("IL-17203", "Woodford County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-354/Woodford/Specimen Ballot for publication.pdf", "il/woodford.pdf"),
    ("MO-29003", "Andrew County Clerk", "Official Ballot, General Election, November 3, 2026", "https://andrewcounty.org/wp-content/uploads/20260924124009241.pdf", "mo/andrew.pdf"),
    ("MO-29013", "Bates County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://batescounty.net/wp-content/uploads/2026/10/Bates-Publication-110326.pdf", "mo/bates.pdf"),
    ("MO-29023", "Butler County Clerk", "Official General Election Ballot, November 3, 2026", "https://butlercountymo.com/wp-content/uploads/2026/09/General-Election-Sample-Ballots-compressed.pdf", "mo/butler.pdf"),
    ("MO-29053", "Cooper County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://clerk.coopercountymo.gov/uploads/6/3/6/9/63696319/publication_ballot_november_3_2026.pdf", "mo/cooper.pdf"),
    ("MO-29107", "Lafayette County Clerk", "Official General Election Ballot, November 3, 2026", "https://www.lafayettecountymo.gov/SAMPLE%20BALLOTS%20NOV%202026%20GREEN.pdf", "mo/lafayette.pdf"),
    ("MO-29163", "Pike County Clerk", "Official General Election Ballot, November 3, 2026", "https://pikemocountyclerk.org/download/november-32026-general-election/", "mo/pike.pdf"),
    ("MO-29195", "Saline County Clerk", "General Election, November 3, 2026, Candidates and Ballot Questions", "https://www.salinecountyclerk.com/_files/ugd/1d7947_baf0a1add58a4366898a3fdaa77cb3ea.pdf", "mo/saline.pdf"),
    ("MO-29221", "Washington County Clerk", "Official Ballot, General Election, November 3, 2026", "https://www.washcoclerkmo.gov/wp-content/uploads/2026/10/Nov-3-2026-Sample-Ballots.pdf", "mo/washington.pdf"),
    ("MO-29049", "Clinton County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://clintoncomo.org/wp-content/uploads/2026/09/Clinton-Publication-110326.pdf", "mo/clinton.pdf"),
    ("MO-29229", "Wright County Clerk", "Official General Election Ballot, November 3, 2026", "https://eadn-wc01-3570615.nxedge.io/wp-content/uploads/Publication-Ballot.pdf", "mo/wright.pdf"),
    ("MT-30063", "Missoula County Elections Office", "Summary Results Report, Federal Primary Election, June 2, 2026 (Official Final)", "https://www.missoulacounty.gov/media/fozbj21m/20260602-official-final-results.pdf", "mt/missoula_gen.pdf"),
    ("OK-40001", "Oklahoma State Election Board", "Adair County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Adair/adair_gen.csv", "ok/adair_gen.csv"),
    ("OK-40003", "Oklahoma State Election Board", "Alfalfa County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Alfalfa/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40005", "Oklahoma State Election Board", "Atoka County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Atoka/atoka_gen.csv", "ok/atoka_gen.csv"),
    ("OK-40007", "Oklahoma State Election Board", "Beaver County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Beaver/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40009", "Oklahoma State Election Board", "Beckham County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Beckham/beckham_gen.csv", "ok/beckham_gen.csv"),
    ("OK-40011", "Oklahoma State Election Board", "Blaine County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Blaine/blaine_gen.csv", "ok/blaine_gen.csv"),
    ("OK-40013", "Oklahoma State Election Board", "Bryan County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Bryan/bryan_gen.csv", "ok/bryan_gen.csv"),
    ("OK-40015", "Oklahoma State Election Board", "Caddo County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Caddo/caddo_gen.csv", "ok/caddo_gen.csv"),
    ("OK-40017", "Oklahoma State Election Board", "Canadian County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Canadian/canadian_gen.csv", "ok/canadian_gen.csv"),
    ("OK-40019", "Oklahoma State Election Board", "Carter County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Carter/carter_gen.csv", "ok/carter_gen.csv"),
    ("OK-40021", "Oklahoma State Election Board", "Cherokee County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Cherokee/cherokee_gen.csv", "ok/cherokee_gen.csv"),
    ("OK-40023", "Oklahoma State Election Board", "Choctaw County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Choctaw/choctaw_gen.csv", "ok/choctaw_gen.csv"),
    ("OK-40025", "Oklahoma State Election Board", "Cimarron County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Cimarron/cimarron_gen.csv", "ok/cimarron_gen.csv"),
    ("OK-40027", "Oklahoma State Election Board", "Cleveland County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Cleveland/cleveland_gen.csv", "ok/cleveland_gen.csv"),
    ("OK-40029", "Oklahoma State Election Board", "Coal County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Coal/coal_gen.csv", "ok/coal_gen.csv"),
    ("OK-40031", "Oklahoma State Election Board", "Comanche County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Comanche/comanche_gen.csv", "ok/comanche_gen.csv"),
    ("OK-40033", "Oklahoma State Election Board", "Cotton County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Cotton/cotton_gen.csv", "ok/cotton_gen.csv"),
    ("OK-40035", "Oklahoma State Election Board", "Craig County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Craig/craig_gen.csv", "ok/craig_gen.csv"),
    ("OK-40037", "Oklahoma State Election Board", "Creek County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Creek/creek_gen.csv", "ok/creek_gen.csv"),
    ("OK-40039", "Oklahoma State Election Board", "Custer County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Custer/custer_gen.csv", "ok/custer_gen.csv"),
    ("OK-40041", "Oklahoma State Election Board", "Delaware County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Delaware/delaware_gen.csv", "ok/delaware_gen.csv"),
    ("OK-40043", "Oklahoma State Election Board", "Dewey County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Dewey/dewey_gen.csv", "ok/dewey_gen.csv"),
    ("OK-40045", "Oklahoma State Election Board", "Ellis County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Ellis/ellis_gen.csv", "ok/ellis_gen.csv"),
    ("OK-40047", "Oklahoma State Election Board", "Garfield County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Garfield/garfield_gen.csv", "ok/garfield_gen.csv"),
    ("OK-40049", "Oklahoma State Election Board", "Garvin County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Garvin/garvin_gen.csv", "ok/garvin_gen.csv"),
    ("OK-40051", "Oklahoma State Election Board", "Grady County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Grady/grady_gen.csv", "ok/grady_gen.csv"),
    ("OK-40053", "Oklahoma State Election Board", "Grant County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Grant/grant_gen.csv", "ok/grant_gen.csv"),
    ("OK-40055", "Oklahoma State Election Board", "Greer County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Greer/greer_gen.csv", "ok/greer_gen.csv"),
    ("OK-40057", "Oklahoma State Election Board", "Harmon County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Harmon/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40059", "Oklahoma State Election Board", "Harper County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Harper/harper_gen.csv", "ok/harper_gen.csv"),
    ("OK-40061", "Oklahoma State Election Board", "Haskell County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Haskell/haskell_gen.csv", "ok/haskell_gen.csv"),
    ("OK-40063", "Oklahoma State Election Board", "Hughes County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Hughes/hughes_gen.csv", "ok/hughes_gen.csv"),
    ("OK-40065", "Oklahoma State Election Board", "Jackson County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Jackson/jackson_gen.csv", "ok/jackson_gen.csv"),
    ("OK-40067", "Oklahoma State Election Board", "Jefferson County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Jefferson/jefferson_gen.csv", "ok/jefferson_gen.csv"),
    ("OK-40069", "Oklahoma State Election Board", "Johnston County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Johnston/johnston_gen.csv", "ok/johnston_gen.csv"),
    ("OK-40071", "Oklahoma State Election Board", "Kay County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Kay/kay_gen.csv", "ok/kay_gen.csv"),
    ("OK-40073", "Oklahoma State Election Board", "Kingfisher County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Kingfisher/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40075", "Oklahoma State Election Board", "Kiowa County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Kiowa/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40077", "Oklahoma State Election Board", "Latimer County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Latimer/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40079", "Oklahoma State Election Board", "Le Flore County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Le Flore/le_flore_gen.csv", "ok/le_flore_gen.csv"),
    ("OK-40081", "Oklahoma State Election Board", "Lincoln County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Lincoln/lincoln_gen.csv", "ok/lincoln_gen.csv"),
    ("OK-40083", "Oklahoma State Election Board", "Logan County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Logan/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40085", "Oklahoma State Election Board", "Love County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Love/love_gen.csv", "ok/love_gen.csv"),
    ("OK-40093", "Oklahoma State Election Board", "Major County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Major/major_gen.csv", "ok/major_gen.csv"),
    ("OK-40095", "Oklahoma State Election Board", "Marshall County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Marshall/marshall_gen.csv", "ok/marshall_gen.csv"),
    ("OK-40097", "Oklahoma State Election Board", "Mayes County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Mayes/mayes_gen.csv", "ok/mayes_gen.csv"),
    ("OK-40087", "Oklahoma State Election Board", "McClain County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/McClain/mcclain_gen.csv", "ok/mcclain_gen.csv"),
    ("OK-40089", "Oklahoma State Election Board", "McCurtain County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/McCurtain/mccurtain_gen.csv", "ok/mccurtain_gen.csv"),
    ("OK-40091", "Oklahoma State Election Board", "McIntosh County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/McIntosh/mcintosh_gen.csv", "ok/mcintosh_gen.csv"),
    ("OK-40099", "Oklahoma State Election Board", "Murray County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Murray/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40101", "Oklahoma State Election Board", "Muskogee County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Muskogee/muskogee_gen.csv", "ok/muskogee_gen.csv"),
    ("OK-40103", "Oklahoma State Election Board", "Noble County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Noble/noble_gen.csv", "ok/noble_gen.csv"),
    ("OK-40105", "Oklahoma State Election Board", "Nowata County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Nowata/nowata_gen.csv", "ok/nowata_gen.csv"),
    ("OK-40107", "Oklahoma State Election Board", "Okfuskee County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Okfuskee/okfuskee_gen.csv", "ok/okfuskee_gen.csv"),
    ("OK-40109", "Oklahoma State Election Board", "Oklahoma County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Oklahoma/oklahoma_gen.csv", "ok/oklahoma_gen.csv"),
    ("OK-40111", "Oklahoma State Election Board", "Okmulgee County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Okmulgee/okmulgee_gen.csv", "ok/okmulgee_gen.csv"),
    ("OK-40113", "Oklahoma State Election Board", "Osage County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Osage/osage_gen.csv", "ok/osage_gen.csv"),
    ("OK-40115", "Oklahoma State Election Board", "Ottawa County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Ottawa/ottawa_gen.csv", "ok/ottawa_gen.csv"),
    ("OK-40117", "Oklahoma State Election Board", "Pawnee County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Pawnee/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40119", "Oklahoma State Election Board", "Payne County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Payne/payne_gen.csv", "ok/payne_gen.csv"),
    ("OK-40121", "Oklahoma State Election Board", "Pittsburg County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Pittsburg/pittsburg_gen.csv", "ok/pittsburg_gen.csv"),
    ("OK-40123", "Oklahoma State Election Board", "Pontotoc County offices: Candidates filed April 1-3, 2026 (no primary or runoff held)", "manual-fetch:MF-358/Pontotoc/FilingsExport_20261030011129.pdf", "ok/filings_all_counties.pdf"),
    ("OK-40125", "Oklahoma State Election Board", "Pottawatomie County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Pottawatomie/pottawatomie_gen.csv", "ok/pottawatomie_gen.csv"),
    ("OK-40127", "Oklahoma State Election Board", "Pushmataha County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Pushmataha/pushmataha_gen.csv", "ok/pushmataha_gen.csv"),
    ("OK-40129", "Oklahoma State Election Board", "Roger Mills County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Roger Mills/roger_mills_gen.csv", "ok/roger_mills_gen.csv"),
    ("OK-40131", "Oklahoma State Election Board", "Rogers County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Rogers/rogers_gen.csv", "ok/rogers_gen.csv"),
    ("OK-40133", "Oklahoma State Election Board", "Seminole County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Seminole/seminole_gen.csv", "ok/seminole_gen.csv"),
    ("OK-40135", "Oklahoma State Election Board", "Sequoyah County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Sequoyah/sequoyah_gen.csv", "ok/sequoyah_gen.csv"),
    ("OK-40137", "Oklahoma State Election Board", "Stephens County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Stephens/stephens_gen.csv", "ok/stephens_gen.csv"),
    ("OK-40139", "Oklahoma State Election Board", "Texas County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Texas/texas_gen.csv", "ok/texas_gen.csv"),
    ("OK-40141", "Oklahoma State Election Board", "Tillman County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Tillman/tillman_gen.csv", "ok/tillman_gen.csv"),
    ("OK-40143", "Oklahoma State Election Board", "Tulsa County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Tulsa/tulsa_gen.csv", "ok/tulsa_gen.csv"),
    ("OK-40145", "Oklahoma State Election Board", "Wagoner County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Wagoner/wagoner_gen.csv", "ok/wagoner_gen.csv"),
    ("OK-40147", "Oklahoma State Election Board", "Washington County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Washington/washington_gen.csv", "ok/washington_gen.csv"),
    ("OK-40149", "Oklahoma State Election Board", "Washita County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Washita/washita_gen.csv", "ok/washita_gen.csv"),
    ("OK-40151", "Oklahoma State Election Board", "Woods County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Woods/woods_gen.csv", "ok/woods_gen.csv"),
    ("OK-40153", "Oklahoma State Election Board", "Woodward County offices: Candidates filed April 1-3, 2026, with June 16, 2026 primary and August 25, 2026 runoff results", "manual-fetch:MF-358/Woodward/woodward_gen.csv", "ok/woodward_gen.csv"),
    ("MO-29019", "Boone County Clerk", "Notice of General Election, November 3, 2026", "https://www.boonemo.gov/clerk/webpublish/elections/notices/election-notice.pdf", "mo/boone_gen.pdf"),
    ("MO-29071", "Franklin County Clerk", "General Election, November 3, 2026: what is on the November ballot", "https://mo-franklincounty.civicplus.com/DocumentCenter/View/484", "mo/franklin_gen.pdf"),
    ("MO-29011", "Barton County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://img1.wsimg.com/blobby/go/7396ed82-50d2-4707-9031-b5a480348d96/Barton%20Publication%20110326.pdf", "mo/barton_gen.pdf"),
    ("MO-29085", "Hickory County Clerk", "Official Sample Ballots, General Election, November 3, 2026", "https://hickorycomo.net/wp-content/uploads/2026/09/November-3-2026-Sample-Ballots.pdf", "mo/hickory_gen.pdf"),
    ("MO-29111", "Lewis County Clerk", "General Election Sample Ballot, November 3, 2026", "https://lewiscountymo.gov/wp-content/uploads/2026/10/General_Election_Sample_Ballot_November_2026.pdf", "mo/lewis_gen.pdf"),
    ("MO-29137", "Monroe County Clerk", "Notice of Election and Sample Ballot, November 3, 2026", "https://monroecountymo.gov/wp-content/uploads/2026/10/11326sampleballot-rev101526.pdf", "mo/monroe_gen.pdf"),
    ("MO-29173", "Ralls County Clerk", "Official Ballot, General Election, November 3, 2026", "https://rallscountymo.gov/wp-content/uploads/2026/09/Nov-2026-General-Election-Sample-Ballot.pdf", "mo/ralls_gen.pdf"),
    ("MO-29121", "Macon County Clerk", "General Election Sample Ballot, November 3, 2026", "https://irp.cdn-website.com/830d7b71/files/uploaded/2026+General+Sample+Ballot.pdf", "mo/macon_gen.pdf"),
    ("MO-29151", "Osage County Clerk", "Official General Election Ballot, November 3, 2026", "https://www.osagecountygov.com/Documents/County%20Clerk/2026%20Nov%20Sample.pdf", "mo/osage_gen.pdf"),
    ("MO-29155", "Pemiscot County Clerk", "Official General Election Ballot, November 3, 2026", "https://pemiscotcounty.org/cmsAdmin/uploads/2/./pemiscot-samples-2-sides.pdf", "mo/pemiscot_gen.pdf"),
    ("MT-30081", "Ravalli County Elections Office", "Summary Results Report, Federal Primary Election, June 2, 2026 (Official Results)", "https://ravallicounty.gov/DocumentCenter/View/10946", "mt/ravalli_results.pdf"),
    ("IL-17015", "Carroll County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-360/Carroll/Speciman Ballot Nov2026.pdf", "il/carroll_gen.pdf"),
    ("IL-17033", "Crawford County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-360/Crawford/26nov3ilcrawford-Publication.pdf", "il/crawford_gen.pdf"),
    ("IL-17045", "Edgar County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-360/Edgar/26nov3iledgar-sample-ballot.pdf", "il/edgar_gen.pdf"),
    ("IL-17139", "Moultrie County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-360/Moultrie/SAMPLE BALLOT - 11.3.26.pdf", "il/moultrie_gen.pdf"),
    ("IL-17147", "Piatt County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-360/Piatt/26nov3ilpiatt-Publication.pdf", "il/piatt_gen.pdf"),
    ("IL-17181", "Union County Clerk", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-360/Union/2026-Sample-Ballot.pdf", "il/union_gen.pdf"),
    ("MO-29061", "Daviess County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Daviess/Daviess-Publication-110326.pdf", "mo/daviess_gen.pdf"),
    ("MO-29063", "DeKalb County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/DeKalb/36b6763af8001ff0650f4767cc185020.pdf", "mo/dekalb_gen.pdf"),
    ("MO-29089", "Howard County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Howard/Howard Publication 110326.pdf", "mo/howard_gen.pdf"),
    ("MO-29093", "Iron County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Iron/SAMPLE-Ballot-working.pdf", "mo/iron_gen.pdf"),
    ("MO-29115", "Linn County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Linn/November3SampleBallot.pdf", "mo/linn_gen.pdf"),
    ("MO-29133", "Mississippi County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Mississippi/BALLOT.pdf", "mo/mississippi_gen.pdf"),
    ("MO-29033", "Carroll County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Carroll/Nov.32026-Sample-Ballot-2.pdf", "mo/carroll_gen.pdf"),
    ("MO-29123", "Madison County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/Madison/2026_11_03 MC District 116 General Election Sample Ballot", "mo/madison_gen.pdf"),
    ("MO-29185", "St. Clair County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-361/St. Clair/nov_3_26_ballot_front.pdf.pdf", "mo/stclair_gen.pdf"),
    ("MO-29073", "Gasconade County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-362/Gasconade/Publication-Ballot.pdf", "mo/gasconade_gen.pdf"),
    ("MO-29083", "Henry County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-362/Henry/PDF BALLOT 1.pdf", "mo/henry_gen.pdf"),
    ("MO-29157", "Perry County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-362/Perry/Perry Publication 110326.pdf", "mo/perry_gen.pdf"),
    ("MO-29219", "Warren County Clerk", "Sample Ballot, General Election, November 3, 2026", "manual-fetch:MF-362/Warren/Ballot 1 - 2nd Congressional District.pdf", "mo/warren_gen.pdf"),
    ("WV-54005", "West Virginia Secretary of State", "Boone County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Boone%20261103%20General%20-%2014inch.pdf", "wv/boone_gen.pdf"),
    ("WV-54011", "West Virginia Secretary of State", "Cabell County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Cabell%20261103%20General%20-%2014inch.pdf", "wv/cabell_gen.pdf"),
    ("WV-54021", "West Virginia Secretary of State", "Gilmer County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Gilmer%20261103%20General%20-%2014inch.pdf", "wv/gilmer_gen.pdf"),
    ("WV-54023", "West Virginia Secretary of State", "Grant County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Grant%20261103%20General%20-%2014inch.pdf", "wv/grant_gen.pdf"),
    ("WV-54031", "West Virginia Secretary of State", "Hardy County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Hardy%20261103%20General%20-%2014inch.pdf", "wv/hardy_gen.pdf"),
    ("WV-54037", "West Virginia Secretary of State", "Jefferson County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Jefferson%20261103%20General%20-%2014inch.pdf", "wv/jefferson_gen.pdf"),
    ("WV-54057", "West Virginia Secretary of State", "Mineral County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Mineral%20261103%20General%20-%2014inch.pdf", "wv/mineral_gen.pdf"),
    ("WV-54075", "West Virginia Secretary of State", "Pocahontas County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Pocahontas%20261103%20General%20-%2014inch.pdf", "wv/pocahontas_gen.pdf"),
    ("WV-54083", "West Virginia Secretary of State", "Randolph County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Randolph%20261103%20General%20-%2014inch.pdf", "wv/randolph_gen.pdf"),
    ("WV-54091", "West Virginia Secretary of State", "Taylor County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Taylor%20261103%20General%20-%2014inch.pdf", "wv/taylor_gen.pdf"),
    ("WV-54093", "West Virginia Secretary of State", "Tucker County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Tucker%20261103%20General%20-%2014inch.pdf", "wv/tucker_gen.pdf"),
    ("WV-54097", "West Virginia Secretary of State", "Upshur County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Upshur%20261103%20General%20-%2014inch.pdf", "wv/upshur_gen.pdf"),
    ("WV-54099", "West Virginia Secretary of State", "Wayne County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Wayne%20261103%20General%20-%2014inch.pdf", "wv/wayne_gen.pdf"),
    ("CO-08093", "Park County Clerk and Recorder", "Official Ballot for 2026 General Election, Precinct 1 sample, November 3, 2026", "https://www.parkcountyco.gov/DocumentCenter/View/10109/01-Sample-Ballot", "co/park_gen.pdf"),
    ("CO-08097", "Pitkin County Clerk and Recorder", "Official Ballot for 2026 General Election, Pitkin County, November 3, 2026", "https://www.pitkinvotes.com/_files/ugd/3441d8_258593275a0a4040a05a9e4ba5b24f11.pdf", "co/pitkin_gen.pdf"),
    ("UT-49007", "Carbon County Clerk/Auditor", "2026 General Election Candidates", "https://www.carbon.utah.gov/wp-content/uploads/2026/09/2026-General-Election-Candidate-List.pdf", "ut/carbon_gen.pdf"),
    ("UT-49009", "Daggett County Clerk", "2026 General Election Sample Ballot", "https://www.daggettcounty.gov/DocumentCenter/View/12453/2026-Ballot-Sample-2", "ut/daggett_gen.pdf"),
    ("UT-49029", "Morgan County Clerk/Auditor", "General Election 2026 Candidate Information", "https://irp.cdn-website.com/016dd32e/files/uploaded/General+Election+2026+Info.pdf", "ut/morgan_gen.pdf"),
    ("UT-49031", "Piute County Clerk/Auditor", "2026 General Election Candidate Certification", "https://piute.gov/wp-content/uploads/2026/09/2026-General-Election-Candidate-Certification.pdf", "ut/piute_gen.pdf"),
    ("UT-49033", "Rich County Clerk/Auditor", "2026 General Election Certification with Candidates", "https://www.richcounty.gov/wp-content/uploads/2026-General-Election-Certification-with-Candidates-and-LGO-Attachment.pdf", "ut/rich_gen.pdf"),
    ("UT-49055", "Wayne County", "2026 Candidates for Office", "https://waynecountyutah.gov/news/2026-candidates-for-office", "ut/wayne_gen.html"),
    ("GU-66", "Guam Election Commission", "2026 General Election Sample Ballot", "https://gec.guam.gov/2026-general-election-info/", "gu/gu_2026-general_sample-ballot_gec.pdf"),
    ("VI-78-stx", "Virgin Islands Elections System", "2026 General Election Candidates, St. Croix District", "https://vivote.gov/?elections=2026-general-election", "vi/2026-general-official-listing-candidates-stx-district.pdf"),
    ("VI-78-stt", "Virgin Islands Elections System", "2026 General Election Candidates, St. Thomas-St. John District", "https://vivote.gov/?elections=2026-general-election", "vi/2026-general-official-listing-candidates-stt-stj-district.pdf"),
    ("AS-60", "American Samoa Election Office", "2026 Midterm Certified Candidates", "https://www.aselectionoffice.gov/candidates-information/candidates-for-the-2026-midterm-election", "as/2026-midterm-certified-candidates-aselectionoffice.html"),
    ("MP-69", "Commonwealth Election Commission", "2026 General Election Certified Candidates Listing", "https://www.votecnmi.gov.mp/candidate/2026-certified-candidates-listing", "mp/2026-ge-certified-candidates-listing.html"),
    ("WY-56003-towns", "Big Horn County Clerk", "Big Horn County 2026 General Candidate Roster, municipal offices", "https://www.bighorncountywy.gov/images/Big_Horn_County_GENERAL_Candidate_Listpdf.pdf", "wy/bighorn.pdf"),
    ("WY-56005-towns", "Campbell County Clerk", "Campbell County 2026 General Election Candidates, municipal offices", "https://www.campbellcountywy.gov/DocumentCenter/View/28182", "wy/campbell.pdf"),
    ("WY-56011-towns", "Crook County Clerk", "Crook County 2026 General Election Candidates, municipal offices", "https://cms2.revize.com/revize/crookscountynew/elected_officials/clerk/Election/2026/Candidate%27s%20Names%20&%20Addresses.pdf", "wy/crook.pdf"),
    ("WY-56015-towns", "Goshen County Clerk", "Goshen County 2026 General Candidate Filing List, municipal offices", "https://www.goshencounty.org/DocumentCenter/View/1524/2026-General-Candidate-Filing-List-PDF", "wy/goshen.pdf"),
    ("WY-56039-towns", "Teton County Clerk", "Teton County 2026 General Election Candidates, municipal offices", "https://www.tetoncountywy.gov/DocumentCenter/View/41951/Contact-cadidate-list", "wy/teton.pdf"),
    ("WY-56041-towns", "Uinta County Clerk", "Uinta County 2026 General Election Candidates, municipal offices", "https://uintacountywy.gov/DocumentCenter/View/8640/2026-General-Election-Candidates", "wy/uinta.pdf"),
    ("WY-56029-towns", "Park County Clerk", "2026 Park County general election partisan and municipal candidate filings", "https://parkcounty-wy.gov/wp-content/uploads/2026/10/General-Candidate-Filings-10.13.26.pdf", "wy/park.pdf"),
    ("MT-30001", "Beaverhead County Clerk and Recorder", "2026 Federal General Candidates for Election (updated 7/30/2026)", "https://beaverheadcountymt.gov/wp-content/uploads/2026/08/2026-Federal-General-Candidates-for-Election-1.pdf", "mt/beaverhead.pdf"),
    ("MT-30007", "Broadwater County Clerk and Recorder", "Notice to the Public, 2026 general election certified candidates", "https://www.broadwatercountymt.gov/departments/government/elections/index.php", "mt/broadwater.html"),
    ("MT-30009", "Carbon County Elections", "Official General Election Ballot, Precinct 1, November 3, 2026", "https://carbonmt.gov/wp-content/uploads/2026/09/Precinct-1.pdf", "mt/carbon_p1.pdf"),
    ("MT-30053", "Lincoln County Elections", "2026 Current Candidate Biographies", "https://lincolncountymt.gov/current-candidate-biographies/", "mt/lincoln.html"),
    ("MT-30083", "Richland County Elections", "Official General Election Ballot, Publication Ballot, November 3, 2026", "https://www.richland.org/uploads/1/4/3/4/143407415/mt_richland_261103_publication_ballot.pdf", "mt/richland.pdf"),
    ("MT-30087", "Rosebud County Elections Administrator", "Upcoming Elections, 2026 candidates", "https://rosebudcountymt.gov/departments/elections-administrator/", "mt/rosebud.html"),
    ("MT-30097", "Sweet Grass County Clerk and Recorder", "Candidate Filings (updated 6/24/2026)", "https://sgcountymt.gov/wp-content/uploads/2026/06/CANDIDATE-FILINGS-UPDATE.pdf", "mt/sweetgrass.pdf"),
    ("WV-54033", "Harrison County Clerk", "2026 General Election Official Ballot, Harrison County", "manual-fetch:MF-371", "../manual/MF-371/Harrison-WV/2026_General_Sample_Ballot.pdf"),
    ("WV-54079", "Putnam County Clerk", "2026 General Election Sample Ballots, Putnam County", "manual-fetch:MF-371", "../manual/MF-371/Putnam-WV/2026-Gen-Sample-Pre-1.pdf"),
    ("WV-54079-towns", "Putnam County Clerk", "2026 General Election Sample Ballots, Putnam County town and city ballots", "manual-fetch:MF-371", "../manual/MF-371/Putnam-WV/2026-Gen-Sample-Pre-1.pdf"),
    ("IL-17187", "Warren County Clerk and Recorder", "Specimen Ballot, General Election, November 3, 2026", "manual-fetch:MF-371", "../manual/MF-371/Warren-IL/SPECIMENBALLOT_Style1_English_8.5x14_Paper (1).pdf"),
    ("TX-48141-vinton", "El Paso County Elections Department", "Village of Vinton sample ballot, November 3, 2026", "https://www.epcounty.com/elections/", "tx/vinton.pdf"),
    ("TX-48209-buda", "Hays County Elections Administrator", "Combined Sample Ballot, City of Buda, November 3, 2026", "manual-fetch:MF-392", "../manual/MF-392/hays/Hays County Combined Sample Ballot 11x17.pdf"),
    ("TX-48209-woodcreek", "Hays County Elections Administrator", "Combined Sample Ballot, City of Woodcreek, November 3, 2026", "manual-fetch:MF-392", "../manual/MF-392/hays/Hays County Combined Sample Ballot 11x17.pdf"),
    ("TX-48209-bearcreek", "Hays County Elections Administrator", "Combined Sample Ballot, Village of Bear Creek, November 3, 2026", "manual-fetch:MF-392", "../manual/MF-392/hays/Hays County Combined Sample Ballot 11x17.pdf"),
    ("TX-48491-jarrell", "Williamson County Elections", "Comprehensive Sample Ballot, City of Jarrell, November 3, 2026", "manual-fetch:MF-392", "../manual/MF-392/williamson/20261103 November JGSE - COMPREHENSIVE BALLOT.pdf"),
    ("TX-48141-fabens", "El Paso County Elections Department", "Fabens Independent School District sample ballot, November 3, 2026", "https://www.epcounty.com/elections/", "tx/fabens_isd.pdf"),
    ("TX-48141-tornillo", "El Paso County Elections Department", "Tornillo Independent School District sample ballot, November 3, 2026", "https://www.epcounty.com/elections/", "tx/tornillo_isd.pdf"),
    ("TX-48215-weslaco", "Hidalgo County Elections Department", "Sample ballot, Weslaco city and ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_weslaco.pdf"),
    ("TX-48215-edcouch", "Hidalgo County Elections Department", "Sample ballot, Edcouch city and Edcouch-Elsa ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_edcouch.pdf"),
    ("TX-48215-donna", "Hidalgo County Elections Department", "Sample ballot, Donna and South Texas ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_donna.pdf"),
    ("TX-48215-lavilla", "Hidalgo County Elections Department", "Sample ballot, La Villa ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_lavilla.pdf"),
    ("TX-48215-montealto", "Hidalgo County Elections Department", "Sample ballot, Monte Alto ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_montealto.pdf"),
    ("TX-48215-mercedes", "Hidalgo County Elections Department", "Sample ballot, Mercedes ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_mercedes.pdf"),
    ("TX-48215-hidalgoisd", "Hidalgo County Elections Department", "Sample ballot, Hidalgo ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_hidalgoisd.pdf"),
    ("TX-48215-lajoya", "Hidalgo County Elections Department", "Sample ballot, La Joya ISD, November 3, 2026", "https://www.hidalgocounty.us/Elections", "tx/hidalgo_lajoya.pdf"),
    ("TX-48201-harris-lc", "Harris County Clerk", "Sample ballot, November 3, 2026 General and Special Elections (League City)", "https://www.harrisvotes.com/", "tx/harris_sample.pdf"),
    ("TX-48201-harris-crosby", "Harris County Clerk", "Sample ballot, November 3, 2026 General and Special Elections (Crosby ISD)", "https://www.harrisvotes.com/", "tx/harris_sample.pdf"),
    ("TX-48355-cc", "City of Corpus Christi", "General Election Candidate List, November 3, 2026 (as of August 18, 2026)", "https://www.cctexas.com/departments/city-secretary", "tx/corpus_christi.pdf"),
    ("TX-48121-dentonpv", "Denton County Elections", "Preview Ballot, General and Special Elections, November 3, 2026", "https://www.votedenton.gov/current-and-upcoming-election-information/", "tx/denton_preview.pdf"),
    ("TX-48439-carroll", "Carroll Independent School District", "Order of Cancellation of Trustee Election, November 3, 2026", "https://www.tarrantcountytx.gov/en/elections/current-election-information.html", "tx/carroll_cancel.pdf"),
    ("WV-54047", "West Virginia Secretary of State", "McDowell County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=McDowell", "wv/mcdowell_gen.pdf"),
    ("WV-54081", "West Virginia Secretary of State", "Raleigh County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Raleigh", "wv/raleigh_gen.pdf"),
    ("WV-54089", "West Virginia Secretary of State", "Summers County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Summers", "wv/summers_gen.pdf"),
    ("WV-54105", "West Virginia Secretary of State", "Wirt County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Wirt", "wv/wirt_gen.pdf"),
    ("TX-48453-pflug", "Travis County Clerk", "Joint General and Special Elections sample ballot, November 3, 2026 (Pflugerville and Pflugerville ISD)", "https://votetravis.gov/wp-content/uploads/G26-Bedsheet-Ballot-Final-FINAL.pdf", "tx/pflugerville_bedsheet.pdf"),
    ("TX-48085-pisd", "Collin County Elections Administration", "Joint General and Special Election all-race sample ballot, November 3, 2026 (Princeton ISD)", "https://www.collincountytx.gov/docs/default-source/elections/sample-ballots/20261103_joint-general-and-special-election_all-race-sample-ballot_final.pdf", "tx/princeton_isd.pdf"),
    ("TX-48167-sfisd", "Galveston County Elections", "November 2026 General Election ballot and measures (Santa Fe ISD)", "https://galvestonvotes.org/wp-content/uploads/2026/09/November-2026-General-Election-Ballot-and-Measures.pdf", "tx/galveston_ballot.pdf"),
    ("TX-48209-sanmarcos", "City of San Marcos", "Current Candidates, November 3, 2026 City Election", "https://www.sanmarcostx.gov/4117/Current-Candidates", "tx/san_marcos.html"),
    ("IL-17125", "Mason County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://masoncountyil.gov/wp-content/uploads/2026/09/26nov3ilmason-Publication.pdf", "il/mason.pdf"),
    ("IL-17053", "Ford County Clerk and Recorder", "Specimen Ballot, General Election, November 3, 2026", "https://fordcounty.illinois.gov/wp-content/uploads/2026/09/SAMPLE-ALL-ONE-DOC.pdf", "il/ford.pdf"),
    ("IL-17189", "Washington County Clerk and Recorder", "Publication Ballot, General Election, November 3, 2026", "https://washingtonco.illinois.gov/wp-content/uploads/PUBLICATION_2026_General.pdf", "il/washington.pdf"),
    ("KS-20177", "Shawnee County Election Office", "Candidate Filing Information, November 3, 2026 General Election", "https://candidatefiling.us/Info/Default?st=KS&s=1097283794&j=3472565347&e=1911002665&det=Y", "ks/shawnee.html"),
    ("KS-20045", "Douglas County Clerk", "Candidate Filing Information, November 3, 2026 General Election", "https://candidatefiling.us/Info/?st=KS&jx=CBC90&ex=D8859&det=Y", "ks/douglas.html"),
    ("KS-20035", "Cowley County Clerk", "Notice of General Election: List of Candidates, November 2026", "https://www.cowleycountyks.gov/DocumentCenter/View/3597/Notice-List-of-Candidates-Nov-2026", "ks/cowley.pdf"),
    ("GA-13245-aug", "Augusta-Richmond County Board of Elections", "Qualified Candidates for the November 3, 2026 General and Special Election", "https://www.augustaga.gov/DocumentCenter/View/21746/Qualified-Candidates", "ga/augusta.pdf"),
    ("KS-20037", "Crawford County Clerk", "Notice of General Election, November 3, 2026", "https://www.crawfordcountykansas.org/uploads/6/2/7/8/62784259/gn2026_general_notice_of_election.docx", "ks/crawford.docx"),
    ("KS-20057", "Ford County Clerk", "Candidate Names for Ballots, November 3, 2026 General Election (official, as of 9/16/2026)", "http://www.fordcounty.net/DocumentCenter/View/25214/Candidate-List-General-2026", "ks/ford.pdf"),
    ("KS-20113", "McPherson County Clerk", "Candidate Filing Report, November 3, 2026 General Election", "https://www.mcphersoncountyks.us/DocumentCenter/View/9235/2026-Candidate-List", "ks/mcpherson.pdf"),
    ("UT-49007-sch", "Carbon County Clerk", "Candidate Information, 2026 General Election", "https://www.carbon.utah.gov/candidate-information/", "ut/carbon.html"),
    ("IL-17025", "Clay County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://claycounty.illinois.gov/wp-content/uploads/2026/09/County-Wide-Specimen-Ballot-Nov-3-2026.pdf", "il/clay.pdf"),
    ("KS-20055", "Finney County Election Officer", "Notice of General Election: Candidates, November 3, 2026", "https://www.finneycountyks.gov/DocumentCenter/View/8186/2026GN-Notice---Notice-of-Election---Candidates", "ks/finney.pdf"),
    ("KS-20125", "Montgomery County Election Officer", "Certification of Candidates and Notice of General Election, November 3, 2026", "https://www.mgcountyks.org/DocumentCenter/View/1518/Notice-of-General-Election-Nov-3rd-20260001", "ks/montgomery.pdf"),
    ("IL-17031-chi", "Chicago Board of Election Commissioners", "Candidate Filings in Ballot Order, November 3, 2026 (status as of October 27, 2026)", "https://www.chicagoelections.gov/getting-ballot/candidates", "il/chicago_boe.pdf"),
    ("MT-30111", "Yellowstone County Election Administrator", "November 2026 General Election Candidate List (memo to the County Commissioners, August 10, 2026)", "https://www.yellowstonecountymt.gov/BOCCDocs/2026/BOCCDIS/20260813_1601/16873_Letter_to_Commissioners_regarding_candidates.pdf", "mt/yellowstone_gen.pdf"),
    ("WV-54001", "West Virginia Secretary of State", "Barbour County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Barbour%20261103%20General%20-%2014inch.pdf", "wv/barbour_gen.pdf"),
    ("WV-54007", "West Virginia Secretary of State", "Braxton County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Braxton%20261103%20General%20-%2014inch.pdf", "wv/braxton_gen.pdf"),
    ("WV-54009", "West Virginia Secretary of State", "Brooke County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Brooke%20261103%20General%20-%2014inch.pdf", "wv/brooke_gen.pdf"),
    ("WV-54013", "West Virginia Secretary of State", "Calhoun County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Calhoun%20261103%20General%20-%2014inch.pdf", "wv/calhoun_gen.pdf"),
    ("WV-54015", "West Virginia Secretary of State", "Clay County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Clay%20261103%20General%20-%2014inch.pdf", "wv/clay_gen.pdf"),
    ("WV-54017", "West Virginia Secretary of State", "Doddridge County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Doddridge%20261103%20General%20-%2014inch.pdf", "wv/doddridge_gen.pdf"),
    ("WV-54027", "West Virginia Secretary of State", "Hampshire County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Hampshire%20261103%20General%20-%2014inch.pdf", "wv/hampshire_gen.pdf"),
    ("WV-54035", "West Virginia Secretary of State", "Jackson County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Jackson%20261103%20General%20-%2014inch.pdf", "wv/jackson_gen.pdf"),
    ("WV-54041", "West Virginia Secretary of State", "Lewis County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Lewis%20261103%20General%20-%2014inch.pdf", "wv/lewis_gen.pdf"),
    ("WV-54043", "West Virginia Secretary of State", "Lincoln County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Lincoln%20261103%20General%20-%2014inch.pdf", "wv/lincoln_gen.pdf"),
    ("WV-54053", "West Virginia Secretary of State", "Mason County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Mason%20261103%20General%20-%2014inch.pdf", "wv/mason_gen.pdf"),
    ("WV-54059", "West Virginia Secretary of State", "Mingo County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Mingo%20261103%20General%20-%2014inch.pdf", "wv/mingo_gen.pdf"),
    ("WV-54063", "West Virginia Secretary of State", "Monroe County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Monroe%20261103%20General%20-%2014inch.pdf", "wv/monroe_gen.pdf"),
    ("WV-54065", "West Virginia Secretary of State", "Morgan County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Morgan%20261103%20General%20-%2014inch.pdf", "wv/morgan_gen.pdf"),
    ("WV-54067", "West Virginia Secretary of State", "Nicholas County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Nicholas%20261103%20General%20-%2014inch.pdf", "wv/nicholas_gen.pdf"),
    ("WV-54071", "West Virginia Secretary of State", "Pendleton County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Pendleton%20261103%20General%20-%2014inch.pdf", "wv/pendleton_gen.pdf"),
    ("WV-54073", "West Virginia Secretary of State", "Pleasants County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Pleasants%20261103%20General%20-%2014inch.pdf", "wv/pleasants_gen.pdf"),
    ("WV-54077", "West Virginia Secretary of State", "Preston County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Preston%20261103%20General%20-%2014inch.pdf", "wv/preston_gen.pdf"),
    ("WV-54085", "West Virginia Secretary of State", "Ritchie County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Ritchie%20261103%20General%20-%2014inch.pdf", "wv/ritchie_gen.pdf"),
    ("WV-54087", "West Virginia Secretary of State", "Roane County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Roane%20261103%20General%20-%2014inch.pdf", "wv/roane_gen.pdf"),
    ("WV-54095", "West Virginia Secretary of State", "Tyler County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Tyler%20261103%20General%20-%2014inch.pdf", "wv/tyler_gen.pdf"),
    ("WV-54101", "West Virginia Secretary of State", "Webster County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Webster%20261103%20General%20-%2014inch.pdf", "wv/webster_gen.pdf"),
    ("WV-54103", "West Virginia Secretary of State", "Wetzel County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Wetzel%20261103%20General%20-%2014inch.pdf", "wv/wetzel_gen.pdf"),
    ("WV-54109", "West Virginia Secretary of State", "Wyoming County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Wyoming%20261103%20General%20-%2014inch.pdf", "wv/wyoming_gen.pdf"),
    ("WV-54055", "West Virginia Secretary of State", "Mercer County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Mercer%20261103%20General%20-%2014inch.pdf", "wv/mercer_gen.pdf"),
    ("WV-54069", "West Virginia Secretary of State", "Ohio County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Ohio%20261103%20General%20-%2014inch.pdf", "wv/ohio_gen.pdf"),
    ("WV-54019", "West Virginia Secretary of State", "Fayette County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Fayette%20261103%20General%20-%2014inch.pdf", "wv/fayette_gen.pdf"),
    ("WV-54025", "West Virginia Secretary of State", "Greenbrier County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Greenbrier%20261103%20General%20-%2014inch.pdf", "wv/greenbrier_gen.pdf"),
    ("WV-54045", "West Virginia Secretary of State", "Logan County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Logan%20261103%20General%20-%2014inch.pdf", "wv/logan_gen.pdf"),
    ("WV-54029", "West Virginia Secretary of State", "Hancock County General Election Sample Ballots, November 3, 2026", "https://apps.sos.wv.gov/elections/2026GeneralSampleBallots/SAMPLE%20-%20WV%20Hancock%20261103%20General%20-%2014inch.pdf", "wv/hancock_gen.pdf"),
    ("IL-17005", "Bond County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://bondcountyil.gov/wp-content/uploads/November-3-2026-General-Election-Sample-Ballot.pdf", "il/bond_gen.pdf"),
    ("IL-17067", "Hancock County Clerk", "Official Ballot (publication), General Election, November 3, 2026", "https://hancockcounty-il.gov/wp-content/uploads/2026/09/IL-Hancock-261103-General-PUBLICATION.pdf", "il/hancock_gen.pdf"),
    ("IL-17159", "Richland County Clerk", "Publication Ballot, 2026 General Election", "https://richlandcounty.illinois.gov/wp-content/uploads/2026/10/PUBLICATION_2026_General_Richland.pdf", "il/richland_gen.pdf"),
    ("IL-17191", "Wayne County Clerk", "Official Sample Ballot, General Election, November 3, 2026", "https://waynecountyil.gov/wp-content/uploads/2026/10/2026-GE-WAYNE-OFFICIAL-SAMPLE-BALLOT.pdf", "il/wayne_gen.pdf"),
    ("IL-17023", "Clark County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://www.clarkcountyil.org/media/2026nov3ilclark-Publication.pdf", "il/clark_gen.pdf"),
    ("IL-17149", "Pike County Clerk", "Publication Ballot, 2026 General Election", "https://www.pikecountyil.org/wp-content/uploads/2026/09/2026-General-Election-Publication-Ballot-1.pdf", "il/pike_gen.pdf"),
    ("IL-17039", "De Witt County Clerk and Recorder", "Local Candidates, November 3, 2026 General Election", "https://dewittcountyil.gov/Documents/Departments/County%20Clerk%20and%20Recorder/NOV%203%202026%20GENERAL%20ELECTION%20LOCAL%20CANDIDATES.pdf", "il/dewitt_gen_candidates.pdf"),
    ("IL-17099", "La Salle County Clerk", "Specimen Ballot, General Election, November 3, 2026", "https://www.lasallecountyil.gov/DocumentCenter/View/4565/Specimen-Ballot-PDF", "il/lasalle.pdf"),
    ("IL-17133", "Monroe County Clerk", "Publication Ballot, General Election, November 3, 2026", "https://www.mococlerk.com/files/2026%20GE%20Publication%20Ballot.pdf", "il/monroe.pdf"),
    ("MO-29127", "Marion County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://marioncounty-mo.gov/wp-content/uploads/2026/09/November-3-2026-General-Election-Sample-Ballot.pdf", "mo/marion.pdf"),
    ("MO-29207", "Stoddard County Clerk", "Sample Ballot BT 1, General Election, November 3, 2026", "https://stoddardcountyclerk.gov/wp-content/uploads/2026/09/Sample-Ballot-1.pdf", "mo/stoddard.pdf"),
    ("MO-29001", "Adair County Clerk", "Sample Ballot BT 1, General Election, November 3, 2026", "https://adaircountymissouri.com/wp-content/uploads/2026/09/Adair-Sample-BT1.pdf", "mo/adair.pdf"),
    ("MO-29215", "Texas County Clerk", "Sample Ballot, General Election, November 3, 2026", "https://eadn-wc01-3627135.nxedge.io/wp-content/uploads/November-2026-Sample-Ballot.pdf", "mo/texas.pdf"),
    ("TX-48157-fbisd", "Fort Bend ISD", "Board Elections, November 3, 2026 Trustee General Election", "https://www.fortbendisd.gov/our-district/leadership/board-of-trustees/board-elections", "tx/fbisd.html"),
    ("TX-48215-ecisd", "Edinburg CISD", "School Board Candidates, November 3, 2026 Election", "https://4.files.edl.io/5137/10/16/26/141722-ce5d89de-f0e6-4db4-b79b-63f4f2091cec.pdf", "tx/edinburg.pdf"),
    ("MT-30029", "Flathead County Elections", "2026 General Election, Flathead County Offices", "https://flatheadcounty.gov/application/files/6517/8551/6200/Candidate_Information.pdf", "mt/flathead.pdf"),
    ("MT-30049", "Lewis and Clark County Clerk and Recorder", "Publication Ballot, 2026 General Election", "https://www.lccountymt.gov/files/assets/county/v/1/treasurer-clerk-and-recorder/documents/elections/sample-ballots/2026-general-publication-ballot.pdf", "mt/lewisclark.pdf"),
    ("WY-56021", "Laramie County Clerk", "2026 General Election Candidate Roster, County Offices", "https://www.laramiecountywy.gov/files/assets/public/v/1/clerk/documents/candidate-rosters/2026/final-county-offices-candidates-general.pdf", "wy/laramie.pdf"),
    ("WY-56025", "Natrona County Clerk", "2026 General Election Candidate Roster", "https://www.natronacounty-wy.gov/DocumentCenter/View/13878/2026-General-Election-Candidates-Filed-PDF", "wy/natrona.pdf"),
    ("WY-56001", "Albany County Clerk", "Offices For Election and Candidates Who Have Filed, 2026", "https://www.albanycountywy.gov/DocumentCenter/View/7786", "wy/albany.html"),
    ("MT-30067", "Park County", "2026 Park County Candidates", "https://www.parkcountymt.gov/uploads/files/pages/171/2026-County-Candidates.pdf", "mt/park.pdf"),
    ("WV-54039-charleston", "City of Charleston", "2026 Election Information: Candidate Updates, November 3, 2026 General Election", "https://www.charlestonwv.gov/government/election-2026", "wv/charleston.html"),
    ("TX-48201-baytown", "City of Baytown", "Candidates for November 3, 2026 Election", "https://baytown.org/1554/Candidates-for-November-3-2026-Election", "tx/baytown.html"),
    ("TX-48209-kyle", "City of Kyle", "2026 Election: Submitted Candidacy Filings, Mayor and Council Districts 2 and 4", "https://www.cityofkyle.gov/city-hall/elections/2026-election/", "tx/kyle.html"),
    ("AR-05131-fort-smith", "City of Fort Smith", "Candidates for Mayor and Directors-at-Large, media notification of August 5, 2026", "https://content.govdelivery.com/accounts/ARFORTSMITH/bulletins/423bddb", "ar/fort_smith.html"),
    ("FL-12086-coral-gables", "Miami-Dade County Elections Department", "Official Sample Ballot, Coral Gables General Biennial and Special Elections, November 3, 2026", "https://www.miamidade.gov/elections/library/sample-ballots/2026-11-03-coral-gables-general-biennial-and-special-elections-sample-ballot.pdf", "fl/coral_gables.pdf"),
    ("FL-12086-municipal", "Miami-Dade County Elections Department", "Official Sample Ballot, General Election, November 3, 2026 (municipal contests)", "https://www.miamidade.gov/elections/library/2026-11-03-general-election-sample-ballot.pdf", "fl/miami_dade_nov_municipal.pdf"),
    ("AR-05143-municipal", "Washington County Election Commission", "November 3, 2026 General Election Information: Municipal General Election candidates (updated August 21, 2026)", "https://www.washingtoncountyar.gov/government/departments-a-e/election-commission/november-3-2026-general-election-information", "ar/washington_municipal.html"),
    ("KY-21067-fayette", "Fayette County Clerk", "November 3, 2026 General Election: Candidates in Ballot Order, Fayette County", "https://fayettekyclerk.gov/elections/candidates/", "ky/fayette.pdf"),
    ("KY-21111-jefferson", "Jefferson County Clerk", "2026 Jefferson County Candidate Filings, revised August 10, 2026", "https://www.jeffersoncountyclerk.org/files/assets/clerk/v/1/elections/documents/2026-candidate-filing-8-10-26.pdf", "ky/jefferson.pdf"),
    ("NE-31109-lancaster", "Lancaster County Election Commission", "Lancaster County List of Candidates, General Election, November 3, 2026", "https://www.lancaster.ne.gov/DocumentCenter/View/30701", "ne/lancaster.pdf"),
    ("CA-06033-clearlake", "City of Clearlake", "Official Candidate List, Clearlake City Council, August 14, 2026", "https://www.lakecountyca.gov/", "ca/lake_cities.pdf"),
    ("CA-06033-lakeport", "City of Lakeport", "Official Candidate List, Lakeport City Council, August 11, 2026", "https://www.lakecountyca.gov/", "ca/lake_cities_lakeport.pdf"),
    ("CA-06045-fort-bragg", "City of Fort Bragg", "Eight Candidates Qualify for Three Seats on Fort Bragg City Council, August 11, 2026", "https://www.city.fortbragg.com/", "ca/mendocino_cities.pdf"),
    ("TX-48041-college-station", "City of College Station", "General and Special Election November 3, 2026: Candidates for Office", "https://cstx-us.govstack.com/your-government/elections/", "tx/college_station.html"),
    ("WY-56021-cheyenne", "Laramie County Clerk", "2026 General Election Candidate Roster, Municipal Offices (final)", "https://www.laramiecountywy.gov/County-Government/Elected-Officials/County-Clerk/Elections/2026-Election-Information", "wy/laramie_municipal.pdf"),
    ("WV-54039-kanawha", "West Virginia Secretary of State", "Official sample ballot, Kanawha County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Kanawha", "wv/kanawha_sample_ballot.pdf"),
    ("WV-54049-marion", "West Virginia Secretary of State", "Official sample ballot, Marion County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Marion", "wv/marion_sample_ballot.pdf"),
    ("WV-54051-marshall", "West Virginia Secretary of State", "Official sample ballot, Marshall County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Marshall", "wv/marshall_sample_ballot.pdf"),
    ("WV-54019-fayette", "West Virginia Secretary of State", "Official sample ballot, Fayette County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Fayette", "wv/fayette_sample_ballot.pdf"),
    ("WV-54045-logan", "West Virginia Secretary of State", "Official sample ballot, Logan County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Logan", "wv/logan_sample_ballot.pdf"),
    ("WV-54101-webster", "West Virginia Secretary of State", "Official sample ballot, Webster County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Webster", "wv/webster_sample_ballot.pdf"),
    ("WV-54107-wood", "West Virginia Secretary of State", "Official sample ballot, Wood County, November 3, 2026 General Election", "https://apps.sos.wv.gov/elections/SampleBallots/Home/GetBallot?County=Wood", "wv/wood_sample_ballot.pdf"),
    ("AR-05045-faulkner", "Faulkner County Clerk", "2026 General List of Candidates, municipal offices", "https://www.votefaulknerar.gov/wp-content/uploads/2026/09/Candidates.pdf", "ar/faulkner_municipal.pdf"),
    ("KS-20161-townships", "Riley County Clerk", "2026 General Official Candidate List, Township Clerk (updated 08/24/2026)", "https://www.rileycountyks.gov/DocumentCenter/View/27407", "ks/riley_townships.pdf"),
    ("KS-20061-townships", "Geary County Clerk", "2026 General Official Candidate List, Township Clerk (updated 08/25/2026)", "https://www.gearycounty.gov/1487/General-Candidate-List---2026-Local-Offices", "ks/geary_townships.pdf"),
    ("KS-20091-townships", "Johnson County Election Office", "Notice of Election, general election November 3, 2026, township contests", "https://www.jocoelection.org/sites/default/files/2026-10/11-03-2026%20-%20Notice%20of%20Election%20%28candidates%29%20-%20REVISED.pdf", "ks/johnson_townships.pdf"),
    ("KS-20173-townships", "Sedgwick County Election Office", "2026 General Election candidate list, Township Clerk", "https://www.sedgwickcounty.org/elections/current-candidates/?OfficeId=68", "ks/sedgwick_townships.html"),
    ("AR-05119-pulaski", "Pulaski County Election Commission", "11/3/2026 General Election Ballot Position Draw (conducted 8/24/2026)", "https://votepulaskiar.gov/wp-content/uploads/11032026_Ballot-Draw-Results.pdf", "ar/pulaski_ballot_draw.pdf"),
    ("WY-56007-towns", "Carbon County Clerk", "2026 General Election Candidates as of 09.04.2026, municipal offices", "https://carboncountywy.gov/DocumentCenter/View/8605/2026-General-Election-Candidates-as-of-090426", "wy/carbon.pdf"),
    ("WY-56025-towns", "Natrona County Clerk", "2026 General Election Candidate Roster, municipal offices", "https://www.natronacounty-wy.gov/DocumentCenter/View/13878/2026-General-Election-Candidates-Filed-PDF", "wy/natrona.pdf"),
    ("WY-56037-towns", "Sweetwater County Clerk", "Official General Election Publishing Ballot, Sweetwater County, November 3, 2026", "https://www.sweetwatercountywy.gov/General%202026%20Publication%20Ballot.pdf?t=202610131618280", "wy/sweetwater_publication_ballot.pdf"),
    ("CA-06069-hollister", "Hollister City Clerk", "Elections, candidates on the ballot, November 3, 2026", "https://hollister.ca.gov/", "ca/hollister.html"),
    ("CA-06037-alhambra", "Alhambra City Clerk", "Notice of nominees for public office, November 3, 2026", "https://www.cityofalhambra.org/", "ca/alhambra_nominees.pdf"),
    ("AR-05033-crawford", "Crawford County Election Commission", "Notice of Election, 2026 General Election", "https://crawford-county-elections.org/site/images/user-files/notice-of-election-26-general.pdf", "ar/crawford_notice-2026general.pdf"),
    ("AR-05005-baxter", "Baxter County Clerk", "Notice of Election, 2026 General Election", "https://baxtercountyar.gov/elections/Notice-of-Election.pdf", "ar/baxter_notice-2026general.pdf"),
    ("AR-05125-saline", "Saline County Clerk", "Notice of General Election, November 3, 2026", "https://salinecounty.org/Documents/Government/County%20Clerk/Elections/2026%20Elections/Election%20Notice%20for%20General%20Election%202026.pdf", "ar/saline_notice_general.pdf"),
    ("AR-05051-garland", "Garland County Election Commission", "Notice of Election, November 3, 2026", "https://www.garlandcountyvote.org/uploads/6/3/0/4/63045401/2026g_notice_of_election.pdf", "ar/garland_notice_general.pdf"),
    ("AR-05069-jefferson", "Jefferson County Clerk", "2026 General Election Candidates List", "https://www.jeffersoncountyar.gov/government/county-clerk/voter-registration-and-elections", "ar/jefferson_general_candidates.docx"),
    ("AR-05145-white", "White County Election Commission", "Notice of Election, 2026 General Election", "https://whitecounty.ar.gov/", "ar/white_notice-2026general.docx"),
    ("FL-12115-venice", "City of Venice City Clerk", "Qualified Candidate Listing, November 3, 2026 Election", "https://www.venicegov.com/government/city-clerk", "fl/sarasota_venice_qualified_candidates.pdf"),
    ("FL-12015-punta-gorda", "City of Punta Gorda City Clerk", "Candidate Information and Forms, General Election November 3, 2026", "https://www.ci.punta-gorda.fl.us/government/city-clerk/elections-and-voting/candidate-information-copy", "fl/punta_gorda_candidates.html"),
    ("FL-12085-stuart", "City of Stuart", "2026 Qualified Candidate Information", "https://www.stuartfl.gov/839/2026-Qualified-Candidate-Information", "fl/stuart_qualified_candidates.html"),
    ("KS-20201-townships", "Washington County Clerk", "Notice of General Election, November 3, 2026", "https://washingtoncountyks.gov/wp-content/uploads/2026/09/NOTICE-OF-GENERAL-ELECTION.docx", "ks/washington_notice.docx"),
    ("KS-20155-townships", "Reno County Clerk", "Legal Notice, General Election November 3, 2026", "https://www.renocountyks.gov/media/Document Center/Departments/Clerks Office/Election/Legal Notice General candidates.pdf", "ks/reno_notice.pdf"),
    ("KS-20041-townships", "Dickinson County Clerk", "Notice of General Election, November 3, 2026", "https://www.dkcoks.gov/media/Document Center/County Clerk/Notice of General Election.pdf", "ks/dickinson_notice.pdf"),
    ("KS-20141-townships", "Osborne County Clerk", "General Election Notice, November 3, 2026", "https://osbornecounty.gov/news_and_information/2026_election/GENERAL_ELECTION_NOTICE.docx", "ks/osborne_notice.docx"),
    ("KS-20009-townships", "Barton County Clerk", "Notice of General Election, November 3, 2026 (published September 29, 2026)", "https://www.bartoncounty.org/vnews/display.v/SEC/Elections%7CCurrent%20Elections", "ks/barton_notice.docx"),
    ("KS-20145-townships", "Pawnee County Clerk", "Notice of General Election, November 3, 2026", "https://www.pawneecountykansas.com/DocumentCenter/View/2122/2026-General---Notice-of-Election", "ks/pawnee_notice.pdf"),
    ("KS-20095-townships", "Kingman County Clerk", "Notice of General Election, November 3, 2026", "https://www.kingmancoks.org/DocumentCenter/View/929", "ks/kingman_notice.pdf"),
    ("KS-20131-townships", "Nemaha County Election Officer", "Notice of General Election, November 3, 2026", "https://www.nmcoks.us/elections", "ks/nemaha_notice.pdf"),
    ("KS-20147-townships", "Phillips County Clerk", "2026 Kansas General Election Candidate List", "https://www.phillipscountyks.org/elections-candidatelist", "ks/phillips_notice.html"),
    ("KS-20185-townships", "Stafford County Clerk", "Notice of General Election, November 3, 2026", "https://www.staffordcounty.org/uploads/1/3/3/6/133660997/2026_genl_newspaper_pub.pdf", "ks/stafford_notice.pdf"),
    ("KS-20191-townships", "Sumner County Clerk", "County Clerk's Notice, November 3, 2026", "https://www.co.sumner.ks.us/Candidate%20pub%202026.pdf", "ks/sumner_notice.pdf"),
    ("NE-31019-buffalo", "Buffalo County Election Commissioner", "2026 General Election Candidate List", "https://buffalocounty.ne.gov/Portals/0/adam/Content/mgWb3Nnhl0WyG-jsGQjG2A/Link/GN26%20Candidate%20List.pdf", "ne/buffalo_cities.pdf"),
    ("NE-31141-platte", "Platte County Election Commissioner", "Countywide Candidate List, General Election November 3, 2026 (August 4, 2026)", "https://plattecounty.ne.gov/wp-content/uploads/2026/08/Countywide-Candidate-List-08.04.26.pdf", "ne/platte_cities.pdf"),
    ("NE-31111-lincoln", "Lincoln County Election Commissioner", "2026 Election Candidates as of August 3, 2026", "https://lincolncountyne.gov/wp-content/uploads/2026/08/2026-Candidates-List-1.pdf", "ne/lincoln_cities.pdf"),
    ("OR-41045-malheur", "Malheur County Clerk", "November 3, 2026 General Election Candidate Filings (as of August 27, 2026)", "https://www.malheurco.org/county-clerk/election-information/candidate-information/", "or/malheur_cities.pdf"),
    ("FL-12103-seminole", "City of Seminole City Clerk", "Election Information, Election Day November 3, 2026", "https://www.myseminole.com/409/Election-Information", "fl/pinellas_seminole_city_election_information.html"),
    ("FL-12069-tavares", "City of Tavares City Clerk", "Letter to the Lake County Supervisor of Elections: 2026 qualified candidates", "https://www.tavaresfl.gov/DocumentCenter/View/9462/2026-Letter-to-SOE-re-Qualified-Candidates", "fl/lake_tavares_qualified_candidates.pdf"),
    ("FL-12069-clermont", "City of Clermont City Clerk", "Clermont City Council qualifying results, June 12, 2026", "https://www.clermontfl.gov/m/newsflash/Home/Detail/367", "fl/lake_clermont_qualifying_results_2026.html"),
    ("FL-12103-dunedin", "Dunedin City Clerk", "Candidates for the City of Dunedin November 3, 2026 General Municipal Election, as of July 27, 2026", "https://www.dunedin.gov/files/assets/city/v/1/city-clerk/documents/election-docs-2026/as-of-7.27.26-2026-candidate-list-with-details.pdf", "fl/pinellas_dunedin_candidates_as_of_2026-07-27.pdf"),
    ("CA-06087-cities", "Santa Cruz County Clerk", "Candidates for the November 3, 2026 California General Election, city offices", "https://votescount.santacruzcountyca.gov/Home/Elections/November3,2026CaliforniaGeneralElection/ContactCandidates.aspx", "ca/santacruz_contact.html"),
    ("ME-23011-waterville", "Waterville City Clerk", "Sample ballots, Wards 1-7, regular and special municipal election, November 3, 2026", "https://www.watervillemaine.org/", "ne/waterville.zip"),
    ("ME-23031-saco", "Saco City Clerk", "Sample ballots, Wards 1-7, November 3, 2026 general and referendum election", "https://www.sacomaine.org/City%20Clerk/Elections/Sample%20Ballots/", "ne/saco.zip"),
    ("NE-31025-cass", "Cass County Clerk", "2026 Candidate Filings Received 8-3-26", "https://www.casscountyne.gov/utility/openPDF/cacone/2026_Candidate_Filings_Received_8-3-26.pdf?alt=media", "ne/cass_municipal.pdf"),
    ("OR-41071-cities", "Yamhill County Clerk", "City certifications of candidates, November 3, 2026 General Election", "https://yamhillcounty.gov/1301/November-3-2026-General-Election", "or/yamhill_cities.pdf"),
    ("OR-41011-cities", "Coos County Elections Office", "City certifications of candidates, November 3, 2026 General Election", "https://cooscounty-or.specialdistrict.org/november-3-2026-general-election", "or/coos_cities.pdf"),
    ("AR-05007-benton", "Benton County Clerk", "2026 Municipal Candidate Listing, General Election November 3, 2026 (updated August 5, 2026)", "https://bentoncountyar.gov/vote/wp-content/documents/sites/37/2026/08/2026-MUNICIPAL-PETITION-FILINGS.pdf", "ar/benton_municipal.pdf"),
    ("KY-21067-lexington", "Fayette County Clerk", "Fayette County Filed Candidate Information, November 3, 2026 General Election", "https://www.fayettecountyclerk.com/web/elections/FiledCandidateInformationforWebsite.pdf", "ky/lexington.pdf"),
    ("FL-12111-port-st-lucie", "City of Port St. Lucie", "Elections: 2026 Candidates", "https://www.cityofpsl.com/Government/Your-City-Government/Departments/City-Clerk/Elections", "fl/port_st_lucie.html"),
    ("CA-06107-tulare", "City of Tulare", "General Municipal Election, November 3, 2026", "https://www.tulare.ca.gov/government/elections/november-3-2026-general-municipal-election", "ca/tulare.html"),
    ("CA-06037-pomona", "City of Pomona", "2026 Elections Information", "https://www.pomonaca.gov/government/departments/city-clerk/elections-information", "ca/pomona.html"),
    ("CA-06065-palm-desert", "City of Palm Desert", "2026 Elections: General Municipal Election, November 3, 2026", "https://www.palmdesert.gov/government/elections", "ca/palm_desert.html"),
    ("OR-41033-grants-pass", "Grants Pass City Recorder", "Nonpartisan Filings for City Council Positions, General Election November 3, 2026 (certified August 26, 2026)", "https://cms9files.revize.com/josephinecountyor/City%20of%20GP%20candidates.pdf", "or/grantspass_council_nov2026_candidates.pdf"),
    ("OR-41071-mcminnville", "City of McMinnville", "November 3, 2026 General Election", "https://www.mcminnvilleoregon.gov/cityrecorder/page/november-3-2026-general-election", "or/mcminnville_nov3_2026_election.html"),
    ("MN-27145-st-cloud", "City of St. Cloud", "Local Election Filings", "https://ci.stcloud.mn.us/846/Local-Election-Filings", "mn/stcloud_local_election_filings.html"),
    ("AZ-04003-sierra-vista", "City of Sierra Vista City Clerk", "Mayor and City Council Member Candidates, November 3, 2026 General Election (08-18-26)", "https://www.sierravistaaz.gov/our-city/departments/city-clerk/elections/campaign-for-office", "az/sierra_vista_candidates.pdf"),
):
    NE_SOURCES[key] = (publisher, title, url, local)
for key, (publisher, title, url, local) in NE_SOURCES.items():
    PUBLISHERS[key] = (key[:2], publisher, title)
    FILES[key] = url
    DELIVERED[key] = DATA / "inbox" / "y26" / local
KY_CITY_SPELLINGS = {
    ("BOONE", "Erlanger"): "",
    ("BELL", "Middlesboro City Coumcil"): "Middlesboro",
    ("BULLITT", "Lebanon Jct"): "Lebanon Junction",
    ("HENRY", "Campbllsburg"): "Campbellsburg",
    ("JEFFERSON", "J"): "Jeffersontown",
    ("JEFFERSON", "Gleniew Hills"): "Glenview Hills",
    ("JEFFERSON", "Propsect"): "Prospect",
    ("JEFFERSON", "Mockinbird Valley"): "Mockingbird Valley",
    ("JEFFERSON", "Moorelane"): "Moorland",
    ("JEFFERSON", "Meadowvale"): "Meadow Vale",
    ("JEFFERSON", "Meadowview"): "Meadowview Estates",
    ("JEFFERSON", "Green Springs"): "Green Spring",
    ("KENTON", "Ft Mitchell"): "Fort Mitchell",
    ("KENTON", "Ft Wright"): "Fort Wright",
    ("LARUE", "Hodgenvillie"): "Hodgenville",
    ("LETCHER", "If Neon"): "Neon",
    ("LOGAN", "Aubrun"): "Auburn",
    ("MEADE", "Branbenburg"): "Brandenburg",
    ("MUHLENBERG", "Drakeboro"): "Drakesboro",
    ("OLDHAM", "Lagrange"): "La Grange",
    ("OLDHAM", "Riverbluff"): "River Bluff",
    ("OLDHAM", "Orchard Grass"): "Orchard Grass Hills",
    ("PIKE", "Coal Run"): "Coal Run Village",
    ("PULASKI", "Feruson"): "Ferguson",
    ("TODD", "Gurthrie"): "Guthrie",
    ("TRIMBLE", "Trimble Milton"): "Milton",
}
TACIT_FEEDS = {"CA-06071-s", "TX-48439-carroll", "TX-48453-lv", "TX-48355-fb"}
NEW_PARTIES = {
    "NY:middle-party": "Middle Party",
    "NY:working-families-perseverance": "Working Families/Perseverance",
    "NY:the-peoples": "The Peoples",
    "NY:listening-party": "Listening Party",
    "NY:hometown-roots": "Hometown Roots",
    "NY:safe-roads": "Safe Roads",
    "NY:community-pride": "Community Pride",
    "NY:community-citizen": "Community Citizen",
    "NY:law-and-order": "Law and Order",
    "NY:new-moon": "New Moon",
    "FL:ecology-party-of-florida": "Ecology Party of Florida",
    "FL:independent-party-of-florida": "Independent Party of Florida",
    "FL:jeffersonian-party-of-florida": "Jeffersonian Party of Florida",
    "forward-party": "Forward Party",
}
NEW_PARTIES_ADDED: set[str] = set()
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
        """select distinct on (c.office_id, c.body_id, c.district_area_id, c.seat_id, named)
                  regexp_replace(x.value, '^[0-9-]+:', ''), n.text
           from contest c
           join election e on e.id = c.election_id
           join entity t on t.id = c.id and t.retired_by is null
           join changeset pc on pc.id = c.changeset_id and pc.pipeline not in ('us.fl_primary_2026', 'us.ga_boe_2026')
           join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
           join name n on n.entity_id = c.id and n.is_preferred and n.lang = 'en'
           left join office f on f.id = c.office_id
           left join body b on b.id = c.body_id
           join polity op on op.id = coalesce(f.polity_id, b.polity_id, e.polity_id)
           cross join lateral (
               select case when c.seat_id is null and seg !~ '\\d|at-large|(^|-)[a-z]($|-)' then seg end as named
               from substring(x.value from '^[0-9-]+:[^:]+:[^:]+:[^:]+:([^:]+)') seg
           ) k
           where (x.value ~ %s or x.value ~ %s) and e.date_from >= '2016-01-01' and e.date_from < %s and not c.notional
               and c.party_scope_id is null and c.kind <> 'ballot_question'
               and op.kind in ('county', 'municipality', 'school_district', 'special_district')
           order by c.office_id, c.body_id, c.district_area_id, c.seat_id, named, e.date_from desc, x.value ~ 'at-large', x.value""",
        [f"(^|:){state}:", "^ceda:" if state == "CA" else "^$", DAY],
    ):
        county = re.match(rf"{state}:(\d{{5}})(?:\d{{5}})?(?::|$)", tail)
        pool[county[1] if county else ""].append((tail, *words(f"{text} {tail.split(':', 2)[-1].replace('-', ' ')}")))
    found = {}
    scores: dict[tuple[str, str], float] = {}
    for county, contest in wanted:
        mine, marks = words(contest)
        roles = {ROLE.get(word, word) for word in mine & ROLES}
        scored = sorted(
            (
                (len(mine & theirs) / len(mine | theirs), tail)
                for tail, theirs, their_marks in pool[county] + pool[""]
                if their_marks == marks
                and not (
                    (
                        named := re.match(
                            r"[A-Z]{2}:\d{2}-([a-z-]+?)-(?:community|unified|joint|union|elementary|high|school)", tail
                        )
                    )
                    and set(named[1].split("-")) - set(re.findall(r"[a-z]+", contest.lower()))
                )
                and bool({"education", "school"} & mine) == bool({"education", "school"} & theirs)
                and not (roles and theirs & ROLES and not roles & {ROLE.get(word, word) for word in theirs})
            ),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            found[(county, contest)] = (scored[0][1], scored[0][1])
            scores[(county, contest)] = scored[0][0]
            continue
        if len(marks) != 1 or not (mark := next(iter(marks))).isalnum() or mark == "atlarge":
            continue
        siblings = sorted(
            (
                (len(mine & theirs) / len(mine | theirs), tail)
                for tail, theirs, their_marks in pool[county]
                if len(their_marks) == 1
                and re.search(rf"-{next(iter(their_marks))}$", tail)
                and bool({"education", "school"} & mine) == bool({"education", "school"} & theirs)
                and not (roles and theirs & ROLES and not roles & {ROLE.get(word, word) for word in theirs})
            ),
            reverse=True,
        )
        best = [tail for score, tail in siblings if score == siblings[0][0]] if siblings else []
        if best and siblings[0][0] >= 0.6 and len({tail.rsplit("-", 1)[0] for tail in best}) == 1:
            found[(county, contest)] = (best[0], f"{best[0].rsplit('-', 1)[0]}-{mark}")
            scores[(county, contest)] = siblings[0][0]
    top: dict[str, tuple[float, str]] = {}
    for wanted_key, (_, tail) in found.items():
        top[tail] = max(top.get(tail, (0.0, "")), (scores[wanted_key], wanted_key[1]))
    return {
        wanted_key: value
        for wanted_key, value in found.items()
        if scores[wanted_key] == top[value[1]][0] or words(top[value[1]][1])[0] <= words(wanted_key[1])[0]
    }


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
                        ".html": "text/html",
                        ".xls": "application/vnd.ms-excel",
                        ".json": "application/json",
                    }.get(DELIVERED[state].suffix, "text/csv"),
                )
            continue
        raw.fetch(
            SOURCE,
            url,
            suffix=".html"
            if "voterfocus" in url or "yolocounty" in url or url.endswith("current-election")
            else Path(url).suffix or ".pdf",
        )
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
                    media_type="application/json"
                    if fips == "17031c"
                    else "text/html"
                    if DELIVERED[fips].suffix == ".html"
                    else "application/zip"
                    if DELIVERED[fips].suffix == ".zip"
                    else "application/pdf",
                )
            continue
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except httpx.HTTPError as error:
            print(f"{url}: {error}")
    for county in pl.read_excel(raw.cached(SOURCE, FEEDS["KY"][0]).path, infer_schema_length=0)["County"].unique():
        try:
            raw.fetch(SOURCE, BALLOTS.format(str(county).replace(" ", "%20")), suffix=".pdf")
        except httpx.HTTPError as error:
            print(f"{county}: {error}")


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
                "name": cased(re.sub(r"\s+\d{2,6}\s+\S.*$", "", re.sub(r"\s+", " ", name).strip())),
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
                candidate[1] == candidate[2],
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
    pinned: set[str] = set()

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
            ("forward", "forward-party"),
        ):
            if word.startswith(prefix):
                return key, False
        return None, word in (
            "independent",
            "unaffiliated",
            "no party",
            "non-partisan independent",
            "petition",
            "nonpartisan",
        )

    for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, FEEDS["MD"][0]).path.read_text(encoding="utf-8-sig"))):
        if row["Candidate Status"] != "Active" or "Write-In" in row["Filing Type and Date"]:
            continue
        where = row["Contest Run By District Name and Number"]
        county = row["Candidate Residential Jurisdiction"] if "District" in where or where.startswith("Municipality") else where
        county = re.sub(r"(?i) county$", "", county).replace("Saint ", "St. ")
        title = row["Office Name"]
        town = re.match(r"(Council|Mayor) - City of (.+)", title)
        contest = (
            f"{town[2]} {'City Council' if town[1] == 'Council' else 'Mayor'}"
            if town
            else f"{'Baltimore' if county == 'Baltimore City' else county + ' County'} {title} {re.sub(r'^.*?(District|Precinct)', r'\1', where) if 'District' in where else ''}"
        )
        named, independent = party(row["Office Political Party"])
        feeds["MD"].append(
            {
                "county": county,
                "contest": contest,
                "seats": {"Baltimore City": 2, "Calvert": 2, "Howard": 2, "Montgomery": 1, "Queen Anne's": 1}.get(county, 0)
                if title.startswith("Board of Education") and "District" not in where
                else 0,
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
                "contest": (
                    f"{str(row['Locality']).title()} Public Schools "
                    if re.search(r"(?i)school board", title)
                    else f"City of {str(row['Locality']).title().removesuffix(' City')} "
                    if str(row["Locality"]).endswith(" CITY") and re.search(r"(?i)city council|mayor", title)
                    else ""
                )
                + f"{title} {'' if district.upper() == str(row['Locality']).upper() else district}",
                "seats": int(row["Number of Seats"] or 0),
                "name": row["Candidate Name"],
                "party": named,
                "independent": independent,
                "unexpired": row["Special Office Unexpired Term End Date"] is not None,
            }
        )

    category = None
    filings = pl.read_excel(raw.cached(SOURCE, FEEDS["KY"][0]).path, infer_schema_length=0).filter(
        pl.col("Withdrawn").is_null()
        & pl.col("Disqualified").is_null()
        & pl.col("Deceased").is_null()
        & (pl.col("Party") != "WRITE-IN")
    )
    titles = {
        "COUNTY JUDGE / EXECUTIVE": "Judge/Executive",
        "SHERIFF": "Sheriff",
        "COUNTY CLERK": "Clerk",
        "COUNTY ATTORNEY": "Attorney",
        "JAILER": "Jailer",
        "CORONER": "Coroner",
        "PROPERTY VALUATION ADMINISTRATOR": "Property Valuation Administrator",
        "SURVEYOR": "Surveyor",
        "CIRCUIT COURT CLERK": "Circuit Court Clerk",
        "MAGISTRATE / JUSTICE OF THE PEACE": "Magistrate",
        "CONSTABLE": "Constable",
        "COUNTY COMMISSIONER": "Commissioner",
    }
    saipe = (
        fastexcel.read_excel(raw.cached(cog.SOURCE, cog.SAIPE).path.read_bytes())
        .load_sheet(0, header_row=2, dtypes="string")
        .to_polars()
    )
    kentucky = {
        coverage_tables.tokens(label): (f"{st}{code}", label)
        for st, code, label in saipe.select("State FIPS Code", "District ID", "Name").iter_rows()
        if st == "21"
    }
    schools: dict[str, tuple[str, str, str]] = {}
    spelled = {
        w: n
        for n, w in enumerate(
            ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
        )
    }
    councils: dict[tuple[str, frozenset[str]], tuple[str, int]] = {}
    warded: set[tuple[str, frozenset[str]]] = set()
    for county in filings["County"].unique():
        try:
            ballot_pdf = raw.cached(SOURCE, BALLOTS.format(str(county).replace(" ", "%20"))).path
        except LookupError:
            continue
        printed = (
            subprocess.run(["pdftotext", "-raw", str(ballot_pdf), "-"], capture_output=True, check=False)
            .stdout.decode()
            .splitlines()
        )
        for i, line in enumerate(printed):
            asks = re.search(r"(?i)\(?Vote ?for (?:up to )?(\w+)(?: candidates?)?\)?$", line.strip())
            if asks is None:
                continue
            ahead = line.strip()[: asks.start()].strip()
            above = ahead or " ".join(printed[max(0, i - 3) : i])
            if not re.search(r"(?i)council|commissioner", above) or re.search(
                r"(?i)county commission|fiscal|judge|urban county", above
            ):
                continue
            for earlier in [ahead] if ahead else printed[max(0, i - 8) : i][::-1]:
                city_key = re.search(
                    r"(?i)City of\s+([A-Za-z'. -]+?)(?:,|\s+CITY\b|$|\s+(?:COUNCIL|COMMISSION))", earlier
                ) or re.search(
                    r"^(?:NONPARTISAN CITY BALLOT, )?([A-Z][A-Z'. -]+?) (?:CITY )?(?:COUNCIL|COMMISSIONERS?)\b", earlier
                )
                if city_key and city_key[1].strip().upper() not in ("CITY", "NONPARTISAN CITY BALLOT"):
                    break
            else:
                continue
            elect = spelled.get(asks[1].lower()) or (int(asks[1]) if asks[1].isdigit() else 0)
            if re.search(r"(?i)\bward\s+\w+", above) and elect == 1:
                warded.add((str(county), coverage_tables.tokens(city_key[1])))
            elif elect:
                councils[(str(county), coverage_tables.tokens(city_key[1]))] = (
                    "City Commission" if re.search(r"(?i)commission", above) else "City Council",
                    elect,
                )
    magnitudes: dict[tuple[str, str], int] = {}
    spellings: dict[tuple[str, frozenset[str]], str] = {}
    contests = []
    for county, post, district, description in filings.select(
        "County", "Office", "District", "Office Description"
    ).iter_rows():
        place = str(county).title().replace("Mccracken", "McCracken").replace("Mclean", "McLean")
        city = " ".join(
            re.sub(
                r"\bCITY (?=COUN|COM|LEGISLATIVE|MAYOR|OF)|^CITY\b|\bCITY$|-\s*\w+ CO$|\b(COUN\w*|MEMBERS?|COM\w*|MAYOR|OF|LEGISLATIVE|BODY)\b|[^A-Z ]",
                " ",
                str(description or "").upper(),
            ).split()
        ).title()
        city = KY_CITY_SPELLINGS.get((str(county), city), city)
        if city:
            city = spellings.setdefault(
                (str(county), coverage_tables.tokens(re.sub(r"\s+City$", "", city))),
                re.sub(r"\bMt\b", "Mount", re.sub(r"\bSt\b", "Saint", re.sub(r"\s+City$", "", city))),
            )
        found = kentucky.get(coverage_tables.tokens(f"{place} County School District"))
        if post in titles and (district is None) != (titles[str(post)] in ("Magistrate", "Constable", "Commissioner")):
            contests.append(f"{place} County {titles[str(post)]}" + (f" District {district}" if district else ""))
        elif (
            post == "SCHOOL BOARD MEMBER"
            and district is not None
            and found is not None
            and "INDEPENDENT" not in str(description or "").upper()
        ):
            contest = f"{place} County Board of Education, Educational District {district}"
            schools[contest] = (f"Board of Education, Educational District {district}", found[1], f"school:{found[0]}")
            contests.append(contest)
        elif post == "MAYOR" and city:
            contests.append(f"City of {city} Mayor")
        elif post == "CITY LEGISLATIVE BODY" and city:
            city_key = (str(county), coverage_tables.tokens(city))
            chamber, elect = councils.get(
                city_key,
                ("City Commission", 4) if re.search(r"\bCOM", str(description).upper()) else ("City Council", 0),
            )
            contest = (
                f"City of {city} City Council, Ward {district}"
                if district and city_key in warded
                else f"City of {city} {chamber}"
            )
            magnitudes[(str(county), contest)] = 1 if city_key in warded else elect
            contests.append(contest)
        else:
            contests.append(None)
    for (county, contest), group in (
        filings.with_columns(pl.Series("Contest", contests, dtype=pl.String))
        .filter(pl.col("Contest").is_not_null())
        .group_by("County", "Contest")
    ):
        if group.filter(pl.col("Party").is_in(["REPUBLICAN PARTY", "DEMOCRATIC PARTY"]))["Party"].is_duplicated().any():
            continue
        place = str(county).title().replace("Mccracken", "McCracken").replace("Mclean", "McLean")
        for row in group.iter_rows(named=True):
            suffix = str(row["Suffix"] or "").strip()
            nickname = suffix if suffix.startswith('"') else ""
            named, independent = party(row["Party"])
            feeds["KY"].append(
                {
                    "county": place,
                    "contest": contest,
                    "seats": magnitudes.get((str(county), str(contest)), 1),
                    "name": " ".join(
                        cased(part)
                        for part in (
                            row["First Name"],
                            nickname or row["Middle Name"],
                            row["Last Name"],
                            suffix.rstrip(".").title() + "." * (suffix[:2] in ("JR", "SR"))
                            if re.fullmatch(r"(JR|SR|II|III|IV)\.?", suffix)
                            else "",
                        )
                        if part
                    ),
                    "party": named,
                    "independent": independent,
                    "unexpired": row["Unexpired"] is not None,
                }
            )

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
                "independent": independent
                or named is None
                and bool(re.search(r"(?i)non.?partisan|write-in|taxpayer watchdog|^[()\s]*$", str(cells[1] or ""))),
                "unexpired": False,
            }
        )

    for key, (county, url, _) in OCVR.items():
        report = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        section, contest, depth, seats, unexpired, open_ = "", "", 0, 1, False, False
        for line in report.splitlines():
            depth_here = len(line) - len(line.lstrip(" "))
            body = line.strip()
            if not body or re.match(
                r"County: |Ballot Content|Report No|Election :|Oregon Centralized|Number Candidates Filed|Contest Total|:$",
                body,
            ):
                continue
            if depth_here == 1:
                section, contest = body, ""
                continue
            if section not in ("City Offices", "County Offices", "District Offices") or depth_here <= 3:
                continue
            if found := re.match(r"(.*?)\s{2,}Vote for (\d+)", line.strip()):
                contest, depth, seats, unexpired, open_ = found[1], depth_here, int(found[2]), "Unexpired" in line, True
                continue
            if not contest:
                continue
            if depth_here == depth and open_:
                contest = f"{contest}{'' if contest.endswith('-') else ' '}{body}"
                continue
            if depth_here > 40:
                continue
            open_ = False
            if section == "District Offices" and not re.search(r"(?i)school", contest):
                continue
            name, *rest = re.split(r"\s{2,}", body)
            name = re.sub(r"\s+-\s+Incumbent$", "", name)
            named, independent = party(rest[0] if rest else None)
            feeds[key].append(
                {
                    "county": county,
                    "contest": re.sub(r"^(\w[\w .]*? County),", r"\1", contest)
                    if section != "County Offices" or contest.startswith(f"{county} County")
                    else f"{county} County {contest}",
                    "seats": seats,
                    "name": name,
                    "party": named,
                    "independent": independent,
                    "unexpired": unexpired,
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

    for county, listing in json.loads(raw.cached(SOURCE, FEEDS["CO"][0]).path.read_text()).items():
        if county == "Arapahoe":
            continue
        for contest in listing["contests"]:
            found = re.fullmatch(rf"({re.escape(county)} County .+) - ([^-]+?) Party", contest["C"])
            ranked = sorted(
                (votes, name)
                for name, votes in zip(contest["CH"], contest["V"], strict=True)
                if not re.search(r"(?i)write.?in", name)
            )
            if found is None or not ranked or re.search(r"(?i)district attorney|regent", found[1]):
                continue
            named, independent = party(found[2])
            feeds["CO"].append(
                {
                    "county": county,
                    "contest": found[1].replace(" - ", " "),
                    "seats": 1,
                    "name": cased(ranked[-1][1]),
                    "party": named,
                    "independent": independent,
                    "unexpired": re.search(r"(?i)vacancy|unexpired", found[1]) is not None,
                }
            )

    for county, listing in json.loads(raw.cached(SOURCE, FEEDS["IA"][0]).path.read_text()).items():
        for contest in listing["contests"]:
            found = re.fullmatch(r"(County .+?) - (Rep|Dem)\.", contest["C"])
            if found is None:
                continue
            seats = int(contest["VF"] or 1)
            ranked = sorted(
                (
                    (votes, name)
                    for name, label, votes in zip(contest["CH"], contest["P"], contest["V"], strict=True)
                    if label
                ),
                reverse=True,
            )
            for votes, name in ranked[:seats]:
                if votes * seats * 100 >= contest["T"] * 35:
                    feeds["IA"].append(
                        {
                            "county": county,
                            "contest": f"{county} {re.sub(r'(?i)\s*-?\s*(?:to fill (?:a )?vacancy|\d+ year term)$', '', found[1])}",
                            "seats": seats,
                            "name": cased(name),
                            "party": "republican-party" if found[2] == "Rep" else "democratic-party",
                            "independent": False,
                            "unexpired": re.search(r"(?i)vacancy|unexpired", found[1]) is not None,
                        }
                    )

    for row in pl.read_excel(raw.cached(SOURCE, FEEDS["WI"][0]).path, infer_schema_length=0).iter_rows(named=True):
        if row["IsReferendum"] or not str(row["District"]).endswith(" County") or " County " not in row["Contest"]:
            continue
        named, independent = party(str(row["Political Party"] or "").removeprefix("Wisconsin "))
        feeds["WI"].append(
            {
                "county": row["District"].removesuffix(" County"),
                "contest": re.sub(r"Circuit Courts$", "Circuit Court", row["Contest"]),
                "seats": 1,
                "name": cased(row["Name On Ballot"]),
                "party": named,
                "independent": independent,
                "unexpired": re.search(r"(?i)unexpired|vacancy", row["Contest"]) is not None,
            }
        )

    for row in json.loads(raw.cached(SOURCE, FEEDS["ID"][0]).path.read_text())["candidates"]:
        if (
            row["districtType"] != "County"
            or row["filingStatus"] != "Approved"
            or row["isWriteIn"]
            or row["officeName"].startswith("Magistrate")
        ):
            continue
        post = row["officeName"].strip().removeprefix("County ")
        named, independent = party(row["partyName"])
        feeds["ID"].append(
            {
                "county": row["countyName"],
                "contest": f"{row['countyName']} County {post}" + (f" District {row['seat']}" if row["seat"] else ""),
                "seats": 1,
                "name": cased(row["candidateName"]),
                "party": named,
                "independent": independent,
                "unexpired": False,
            }
        )

    for county, listing in json.loads(raw.cached(SOURCE, FEEDS["WA"][0]).path.read_text()).items():
        for row in csv.DictReader(io.StringIO(listing)):
            kind, race = row["District Type"].lower(), " ".join(row["Race"].split())
            if kind not in (
                "countywide",
                "county",
                "council",
                "commissioner",
                "city council",
                "city/town",
            ) or re.search(r"(?i)court|judge|director of community", race):
                continue
            race = re.sub(rf"(?i)^(?:metropolitan )?(?:{county} )?county |^{county} ", "", race)
            race = re.sub(r"(?i)\bcouncilor\b", "Council", re.sub(r"(?i)\s*[;,]\s*|\s+-\s+|\bresidency\b", " ", race))
            race = race.title() if race.isupper() else race
            number = r"(?:\s*no\.)?\s*#?\s*0*(\d+)$"
            race = (
                re.sub(rf"(?i)\s+(?:pos\.|position){number}", r" Position \1", race)
                if re.search(rf"(?i)\s(?:pos\.|position){number}", race)
                else re.sub(rf"(?i)(?:^|\s+)(?:(?:dist\.?|district)\s*)?{number}", r" District \1", race)
            )
            race = " ".join(race.split())
            if re.fullmatch(r"(?i)district \d+", race):
                race = f"Commissioner {race}"
            town = re.match(r"(?i)(?:city of )?(.+?)(?: city council district \d+)?$", row["District"])
            contest = (
                f"{str(town[1]).title()} City {'Council ' if not race.startswith('Council') else ''}{race}"
                if kind.startswith("city")
                else f"{county} County {race}"
            )
            preference = row["Party Preference"].strip().upper()
            named, independent = party(
                "Democratic"
                if "DEMOCRAT" in preference
                else "Republican"
                if preference == "GOP" or "REPUBLICAN" in preference
                else preference
            )
            feeds["WA"].append(
                {
                    "county": county,
                    "contest": contest,
                    "seats": 1,
                    "name": row["Name"],
                    "party": named,
                    "independent": independent
                    or preference in ("", "INDEPENDENT", "INDEPENDANT", "IND", "STATES NO PARTY PREFERENCE"),
                    "unexpired": row["Term Type"] == "Unexpired",
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
                if re.match(r"[A-Za-z0-9]", lines[index + 1])
                and not lines[index + 1].startswith("CONTEST")
                and not re.match(r"\d{4,5}-", lines[index + 1])
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

    section, jurisdiction, contest, seats, unexpired = "", "", "", 0, False
    for line in text("MI-26081", "-layout").splitlines():
        if found := re.fullmatch(r"\s{5}([A-Z][A-Z ]+?)\s*|\s{40,}([A-Z][A-Z ]+?)\s*", line):
            section, jurisdiction, contest = (found[1] or found[2]), "", ""
        elif section not in {"TOWNSHIP", "CITY", "VILLAGE", "COMMUNITY COLLEGE", "LOCAL SCHOOL DISTRICTS"}:
            continue
        elif found := re.fullmatch(r"\s{5}((?:City|Village) of [A-Za-z .'-]+|[A-Z][A-Za-z.' -]+ Tow\w+)\s*", line):
            jurisdiction, contest = re.sub(r"Tow\w+$", "Township", found[1]), ""
        elif found := re.match(
            r"\s{5}(\S.*?)(?:\s+-)?\s{2,}(\d+ Year Term|Partial Term \w+ \S+)\s*$", line
        ) or re.match(r"\s{5}(\S.*?) - (Partial Term \w+ \S+)\s*$", line):
            contest = (
                found[1] if section in {"COMMUNITY COLLEGE", "LOCAL SCHOOL DISTRICTS"} else f"{jurisdiction} {found[1]}"
            )
            unexpired = found[2].startswith("Partial")
        elif found := re.match(r"\s+Vote for not more than (\d+)", line):
            seats = int(found[1])
        elif (
            contest
            and not re.search(r"Qualified Write-In|Withdrew", line)
            and (
                found := re.match(
                    r"\s{5}(DEM|REP|LIB|UST|GRN|NLP|WCP|\s{3})\s{6,14}([A-Z][^\d]+?)(?:\s{2,}|\s*$)", line
                )
            )
        ):
            named, independent = party(
                {"DEM": "Democratic", "REP": "Republican", "LIB": "Libertarian"}.get(found[1].strip(), "")
            )
            feeds["MI-26081"].append(
                {
                    "county": "Kent",
                    "contest": re.sub(r"^Board Member (.+)$", r"\1 Board Member", contest),
                    "seats": seats,
                    "name": cased(found[2]),
                    "party": named,
                    "independent": independent,
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
                "contest": "Ocean County Board of County Commissioners"
                if found[1] == "Ocean County"
                else f"{found[1]} {found[2]}",
                "whole": found[1] == "Ocean County",
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

    seats: dict[str, int] = defaultdict(int)
    school: list[tuple[str, str]] = []
    started = False
    for line in text("NJ-34015", "-layout").splitlines():
        if line.startswith("Order Slogan") and "First Name" in line:
            started = True
            continue
        if not started or not re.match(r"\s*\d+\s", line):
            continue
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) < 3 or " - " in cells[1]:
            continue
        place = cells[1]
        seats[place] += 1
        given, family = cells[2], cells[3] if len(cells) > 3 else ""
        if given and family and not nobody.search(f"{given} {family}"):
            school.append((place, f"{given} {family}"))
    for place, person in school:
        feeds["NJ-34015"].append(
            {
                "county": "Gloucester",
                "contest": f"{place.title()} Board of Education",
                "seats": seats[place],
                "name": cased(person),
                "party": None,
                "independent": False,
                "unexpired": False,
            }
        )

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
    union_lines = text("NJ-34039", "-layout").splitlines()
    for number, line in enumerate(union_lines):
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
            if (wrapped := re.match(r"\s*(?:\d{5})?\s{30,}(Committee|Council)\s*$", union_lines[number + 1])) and post.endswith("Township"):
                post = f"{post} {wrapped[1]}"
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
                    "contest": cell["ENTITY"].replace("Eastamtpon", "Eastampton").replace("Bulington", "Burlington"),
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

    unfilled = re.compile(
        r"(?i)no nom|no petition|to be provided|no (local|municipal) offices|^n/a$|^name of candidate$"
    )
    tallies: dict[tuple[str, bool], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for line in text("NJ-34031", "-layout").splitlines():
        found = re.match(
            r"(Countywide|[A-Z][A-Z .'-]*?(?:BOROUGH|TOWNSHIP|CITY|TOWN|VILLAGE))\s+(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s{2,}.*?\s\d{5}\s+(\S.*?)(?:\s{2,}|$)",
            line,
        )
        if found is None or unfilled.search(line):
            continue
        unexpired = bool(re.search(r"(?i)unexp", found[2]))
        contest = (
            f"Passaic County {re.sub(r'(?i)^county |\s*unexp\w* term', '', found[2])}"
            if found[1] == "Countywide"
            else f"{found[1].title()} {found[2]}"
        )
        tallies[(contest, unexpired)][found[5]] += 1
        named, independent = party(found[5])
        feeds["NJ-34031"].append(
            {
                "county": "Passaic",
                "contest": contest,
                "seats": 0,
                "name": cased(f"{found[3]} {found[4]}"),
                "party": named,
                "independent": independent or named is None,
                "unexpired": unexpired,
            }
        )
    for row in feeds["NJ-34031"]:
        row["seats"] = max(tallies[(row["contest"], row["unexpired"])].values())

    for sheet, frame in pl.read_excel(
        raw.cached(SOURCE, FEEDS["NJ-34035"][0]).path, sheet_id=0, has_header=False
    ).items():
        if sheet in ("All", "Federal"):
            continue
        for cells in frame.iter_rows():
            where, office, term, vote, label, name = (str(cell or "").strip() for cell in cells[:6])
            if not vote.lower().startswith("vote for") or not name or unfilled.search(name) or federal.search(office):
                continue
            named, independent = party(label)
            feeds["NJ-34035"].append(
                {
                    "county": "Somerset",
                    "contest": f"Somerset County {office.removeprefix('County ')}"
                    if sheet == "County"
                    else f"{sheet} {office}{f' {where}' if re.match(r'(?i)(ward|district) ', where) else ''}",
                    "seats": NUMBERS.get(vote.split()[-1].title(), 1),
                    "name": cased(name),
                    "party": named,
                    "independent": independent or named is None,
                    "unexpired": "nexpired" in term,
                }
            )

    town = office = label = ""
    seats, unexpired = 1, False
    for line in text("NJ-34041", "-layout").splitlines():
        if found := re.match(r"(\S.*?)\s{2,}Vote for (\d+)\s{2,}(\S.*?)\s{2,}(\S.*?)\s*$", line):
            town, seats, office, unexpired = found[1], int(found[2]), found[3], "nexpired" in found[4]
            town = {"Wash Boro": "Washington Borough", "Wash Twp": "Washington Township"}.get(town, town)
            label = ""
            continue
        found = re.match(
            r"\s+(?:(REPUBLICAN|DEMOCRAT|NOMINATED BY PETITION)\S*:)?\s+(\S.*?)(?:\s{2,}|\s+\S+@\S+|$)", line
        )
        if not town or found is None:
            continue
        label = found[1] or label
        if unfilled.search(found[2]) or "@" in found[2]:
            continue
        named, independent = party(label)
        feeds["NJ-34041"].append(
            {
                "county": "Warren",
                "contest": f"Warren County {office}" if town == "County" else f"{town} {office}",
                "seats": seats,
                "name": cased(found[2]),
                "party": named,
                "independent": independent or named is None,
                "unexpired": unexpired,
            }
        )

    town = office = pending = ""
    seats, unexpired = 1, False
    for line in text("NJ-34023", "-layout").splitlines():
        if found := re.match(r"(?:(\S.*?)\s+)?\d+ YEAR (UNEXPIRED )?TERM VOTE FOR (\d+)", line):
            if found[1]:
                office, town = found[1], pending.title() if pending else town
            else:
                office = pending
            seats, unexpired, pending = int(found[3]), bool(found[2]), ""
            continue
        if re.match(r"[A-Z][A-Z .'-]+$", line) and not re.search("NOMINATION|OFFICES", line):
            pending = line.strip()
            continue
        found = re.match(r"[A-Z]\s{3,}(\S.*?)\s{2,}(\S.*?)\s{2,}(Republican|Democratic|Democrat|Independent)\b", line)
        if found is None or unfilled.search(found[1]) or federal.search(office) or "CONGRESSIONAL" in office:
            continue
        named, independent = party(found[3])
        feeds["NJ-34023"].append(
            {
                "county": "Middlesex",
                "contest": f"Middlesex County {office.title().removeprefix('County ')}"
                if office.startswith("COUNTY")
                else f"{town} {office.title()}",
                "seats": seats,
                "name": cased(f"{found[1]} {found[2]}"),
                "party": named,
                "independent": independent,
                "unexpired": unexpired,
            }
        )

    contest, label, expect, seats = "", "", "", 1
    for line in text("NJ-34017", "-layout").splitlines():
        if not line.strip() or "@" in line or re.search(r"Clerk’s Office|OFFICIAL Master List", line):
            continue
        if re.match(r"\s{10,}\S", line):
            heading = re.sub(r"\s*\((\d+) [Ss]eats?\)", "", line)
            seats = int(found[1]) if (found := re.search(r"\((\d+) [Ss]eats?\)", line)) else 1
            heading = re.sub(r"\b(Democratic|Republican|Independent|Dem|Rep|Ind)\b|–", " ", heading)
            contest = re.sub(r"\b(\d)(?:st|nd|rd|th) Ward\b", r"Council Ward \1", " ".join(heading.split()))
            expect = ""
        elif found := re.match(r"Slogan:\s*(.*)", line):
            label, expect = found[1].strip(), "" if unfilled.search(found[1]) else "office"
        elif line.startswith("Ballot Position"):
            expect = "office"
        elif expect == "office":
            expect = "name"
        elif expect == "name":
            expect = ""
            if re.search(r"(?i)senat|congress", contest):
                continue
            named, independent = party(label)
            feeds["NJ-34017"].append(
                {
                    "county": "Hudson",
                    "contest": contest,
                    "seats": seats,
                    "name": cased(line.strip()),
                    "party": named,
                    "independent": independent or named is None,
                    "unexpired": bool(re.search(r"(?i)unexpired", contest)),
                }
            )

    sheets = pl.read_excel(
        raw.cached(SOURCE, FEEDS["NJ-34003"][0]).path, sheet_id=0, has_header=False, infer_schema_length=0
    )
    office = ""
    for cells in sheets["Countywide"].iter_rows():
        label, post, vote, _, first, last, name = (str(cell or "").strip() for cell in cells[:7])
        office = post or office
        if not vote or vote == "VOTE FOR" or not last:
            continue
        named, independent = party(label)
        feeds["NJ-34003"].append(
            {
                "county": "Bergen",
                "contest": "Bergen County "
                + re.sub(r"(?i)^for (members of the board of county commissioners|county |)", "", office).title()
                if "COMMISSIONERS" not in office
                else "Bergen County Commissioner",
                "seats": NUMBERS.get(vote.title(), 1),
                "name": cased(name or f"{first} {last}"),
                "party": named,
                "independent": independent or named is None,
                "unexpired": "UNEXP" in office,
            }
        )
    for sheet, label in (("Local Rep.", "Republican"), ("Local Dem.", "Democratic"), ("Indep. & NP", "")):
        for cells in sheets[sheet].iter_rows():
            values = [str(cell or "").strip() for cell in cells]
            town, post, term, vote = values[:4]
            name = values[-1] or " ".join(values[5:7])
            if not town or town.startswith("MUNICIPALITY") or not vote or unfilled.search(name) or not name:
                continue
            slogan = values[7] if not label else label
            named, independent = party(slogan or "")
            feeds["NJ-34003"].append(
                {
                    "county": "Bergen",
                    "contest": f"{town.title()} {re.sub(r'(?i)\s*unexp.*$', '', post).title()}",
                    "seats": 1 if "UNEXP" in f"{term} {post}".upper() else NUMBERS.get(vote.title(), 1),
                    "name": cased(name),
                    "party": named,
                    "independent": independent or named is None and bool(slogan),
                    "unexpired": "UNEXP" in f"{term} {post}".upper(),
                }
            )

    for cells in sheets["Local BOE"].iter_rows():
        town, term, vote, _, first, last = (str(cell or "").strip() for cell in cells[:6])
        if not town or town.startswith(("MUNICIPALITY", "THIS LIST", "FOR MEMBERSHIP")) or not last:
            continue
        feeds["NJ-34003"].append(
            {
                "county": "Bergen",
                "contest": f"{town.title()} Board of Education",
                "seats": 1 if "UNEXP" in term.upper() else NUMBERS.get(vote.title(), 1),
                "name": cased(f"{first} {last}"),
                "party": None,
                "independent": False,
                "unexpired": "UNEXP" in term.upper(),
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
            elif post and re.fullmatch(r"(?i)\s*(?:\S+\s+)?no candidate filed\s*", line):
                feeds[key].append(
                    {
                        "county": county,
                        "contest": post,
                        "seats": seats,
                        "name": NO_FILING,
                        "party": None,
                        "independent": False,
                        "unexpired": unexpired,
                    }
                )
            elif post and (found := re.match(r"\s{0,4}(\S(?:.*?\S)?)\s{2,}(\S.*?)(?:\s{2,}|$)", line)):
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

    ev = json.loads(raw.cached(SOURCE, FEEDS["GA"][0]).path.read_text())
    for short, county in ev["primary"].items():
        place = county["county"].removesuffix(" County")
        if place in GA_BALLOTS:
            continue
        for item in county["ballotItems"]:
            found = re.fullmatch(r"(.+?) - (Rep|Dem)", item["name"])
            if found is None or re.match(GA_STATE, found[1]) or "Elections Board" in found[1]:
                continue
            options = [option for option in item["options"] if not option["writeIn"]]
            if len(options) > 1 and max(option["votes"] for option in options) * 2 <= sum(
                option["votes"] for option in options
            ):
                options = next(
                    (
                        [option for option in runoff["options"] if not option["writeIn"]]
                        for runoff in ev["runoff"].get(short, {}).get("ballotItems", [])
                        if runoff["name"] == item["name"]
                    ),
                    [],
                )
            if not options:
                continue
            winner = max(options, key=lambda option: option["votes"])
            title = re.sub(
                r"(?i)\s*/\s*para .*|\s*\((?:unexpired|special)[^)]*\)|\s*-?\s*(?:unexpired term|special election)",
                "",
                found[1],
            )
            title = re.sub(r"\bBOE\b", "Board of Education", " ".join(title.replace(" - ", " ").split()))
            if not title.startswith(("County ", place)):
                title = f"County {title}"
            feeds["GA"].append(
                {
                    "county": place,
                    "contest": title if title.startswith(place) else f"{place} {title}",
                    "seats": int(re.sub(r"\D", "", item["voteFor"]) or 1),
                    "name": cased(re.sub(r"\s*\(I\)$", "", winner["name"])),
                    "party": "republican-party" if found[2] == "Rep" else "democratic-party",
                    "independent": False,
                    "unexpired": re.search(r"(?i)unexpired|special", found[1]) is not None,
                }
            )

    tsv_rows = [
        (state, number, row)
        for state in (
            "oh",
            "fl",
            "ca",
            "nj",
            "counties",
            "ne",
            "states",
            *sorted(path.stem.removeprefix("local_2026_") for path in Path(__file__).parent.glob("local_2026_lane_*.tsv")),
        )
        for number, row in enumerate(
            csv.DictReader(
                Path(__file__).with_name(f"local_2026_{state}.tsv").read_text().splitlines(), delimiter="\t"
            ),
            start=2,
        )
    ]
    named_parties = {row["party"] for _, _, row in tsv_rows if ":" in row["party"]}
    with connect() as conn:
        known_parties = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.party' and value = any(%s)", [list(named_parties)]
            )
        }
    unknown = [
        f"local_2026_{state}.tsv row {number}: {row['party']}"
        for state, number, row in tsv_rows
        if ":" in row["party"] and row["party"] not in known_parties and row["party"] not in NEW_PARTIES
    ]
    if unknown:
        raise ValueError("party not in the archive:\n" + "\n".join(unknown))

    for _, _, row in tsv_rows:
        named, independent = (row["party"], False) if ":" in row["party"] else party(row["party"])
        feeds[
            f"NY-{row['county']}"
            if row["key"] == "NY"
            else row["key"] + "-s"
            if row["key"] + "-s" in NE_SOURCES and "Board of Education" in row["contest"]
            else row["key"]
        ].append(
            {
                "county": row.get("county") or row["contest"].split(" County")[0],
                "contest": row["contest"],
                "seats": int(row.get("seats") or 1),
                "name": cased(row["name"]),
                "party": named,
                "independent": independent,
                "unexpired": row["unexpired"] == "1",
                "office": row.get("office_name") or "",
                "area": row.get("constituency_area") or "",
                "mint": row.get("ward_mint") == "1",
                "district": row.get("district") or "",
            }
        )

    for group, (county, town, _) in VOTERFOCUS.items():
        key = f"FL-vf-{group}"
        page = raw.cached(SOURCE, FEEDS[key][0]).path.read_bytes().decode("utf-8", errors="replace")
        offices: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        post = None
        for block in re.split(r'(?=<div class="col-xs-12 officename">)|(?=<div class="col-xs-12 detailrow)', page):
            if heading := re.match(r'<div class="col-xs-12 officename">\s*Office:\s*(.*?)\s*<', block):
                post = " ".join(html.unescape(heading[1]).split())
            elif (status := re.search(r"\(Active-<span[^>]*>(.*?)</span>\s*\)", block)) and (
                cell := re.search(r'role="gridcell">\s*(.*?)\s*</div>', block, re.DOTALL)
            ):
                name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell[1])).replace("(N/P)", "").split())
                found = re.match(r"(.*?)\s*\(([A-Z]{2,4})\)$", name)
                offices[str(post)].append((found[1] if found else name, found[2] if found else "", status[1].strip()))
        for post, people in offices.items():
            on = [person for person in people if person[2] in ("Qualified", "Advanced", "Runoff")]
            parties = Counter(person[1] for person in on if person[1] in ("REP", "DEM"))
            title = re.sub(
                r"(?i)\bsch\b",
                "School",
                re.sub(
                    r"(?i)^co\b",
                    "County",
                    re.sub(r"(?i)^bd of county comm(?:issioners?)?\b", "County Commissioner", post),
                ),
            )
            title = re.sub(r"(?i)\b(?:dist|dst|pct)\b\.?", "District", title)
            title = re.sub(r"(?i)\s*-\s*(?:single m|at large)$|^member,\s*", "", title)
            title = re.sub(r"(?i)\s*-\s*(\d+)$", r" District \1", title)
            title = " ".join(re.sub(r"\s*[-,]\s*", " ", title).split())
            countywide = re.search(
                r"(?i)^(county|board of county|school board|supervisor of elections|clerk|sheriff|property appraiser"
                r"|tax collector)",
                title,
            )
            if (
                not on
                or "Elected" in {person[2] for person in people}
                or any(count > 1 for count in parties.values())
                or re.search(
                    r"(?i)\bCDD\b|develop|\bdst\b|stewardship|mosq|ecua|\bfd\b|foundation|trustee|supervisors?\b|soil"
                    r"|water|judge|court|hospital|fire|district \d+ (?:board|seat)|improvement|port|authority|library",
                    post,
                )
                or len(on) > 2
                and len({person[1] for person in on}) < len(on)
                and (
                    countywide
                    and not {person[2] for person in on} & {"Advanced", "Runoff"}
                    or not countywide
                    and not re.search(r"(?i)\b(seat|district|ward|zone|group|mayor)\b", title)
                )
            ):
                continue
            if countywide:
                title = f"{county} County {re.sub(r'(?i)^county ', '', title)}"
            elif town and not title.lower().startswith(town.lower()):
                title = f"{town} {title}"
            for name, label, _ in on:
                named, independent = party(
                    {
                        "REP": "Republican",
                        "DEM": "Democratic",
                        "LPF": "Libertarian",
                        "LIB": "Libertarian",
                        "GRE": "Green",
                    }.get(label)
                )
                named = {
                    "ECO": "FL:ecology-party-of-florida",
                    "IND": "FL:independent-party-of-florida",
                    "JEF": "FL:jeffersonian-party-of-florida",
                }.get(label, named)
                feeds[key].append(
                    {
                        "county": county,
                        "contest": title,
                        "seats": 1,
                        "name": cased(name),
                        "party": named,
                        "independent": independent or named is None,
                        "unexpired": False,
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
            if row["name"] == NO_FILING
            or not nobody.search(str(row["name"]))
            and not re.search(r"(?i)withdr", str(row["name"]))
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
        index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]] = defaultdict(list)
        for st, district, label in saipe.select("State FIPS Code", "District ID", "Name").iter_rows():
            index[(st, coverage_tables.tokens(label))].append((f"{st}{district}", label))
        unowned: dict[str, list[tuple[str, str, str, str, str]]] = defaultdict(list)
        owned = dict(
            conn.execute(
                """select distinct on (x.value) x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id
                   where x.scheme = 'us.polity' and x.value ~ '^(place|town|school):' order by x.value, length(s.path)"""
            ).fetchall()
        )
        for st, county_code, label in conn.execute(
            """select g.state, g.county, g.name from coverage_government g
               where g.kind = 'school' and g.polity_id is null and g.county is not null and g.state in ('MI', 'CA', 'NJ', 'TX', 'UT', 'AZ', 'MD', 'VA', 'RI', 'LA', 'NC', 'IN', 'IL')"""
        ).fetchall():
            label = {"Rowan County Sch": "Rowan-Salisbury Schools"}.get(label, label)
            mine = frozenset(token for token in coverage_tables.tokens(label) if not token.isdigit())
            hits = [
                hit
                for (code_st, theirs), found in index.items()
                if code_st == county_code[:2]
                and (
                    frozenset(token for token in theirs if not token.isdigit()) == mine
                    if st == "IL"
                    else theirs == mine or theirs < mine or mine < theirs
                )
                for hit in found
            ]
            if not hits:
                generic = {"COUNTY", "CONSOLIDATED", "COMMUNITY", "METROPOLITAN", "UNITED", "AREA", "REGIONAL", "UNIFIED", "TOWNSHIP", "CITY"}
                core = frozenset(mine - generic)
                hits = [
                    hit
                    for (code_st, theirs), found in index.items()
                    if code_st == county_code[:2] and core and frozenset(theirs - generic) == core
                    for hit in found
                ]
            alike = [hit for hit in hits if coverage_tables.tokens(hit[1]) == mine]
            if len(alike or hits) == 1:
                code, district = (alike or hits)[0]
                unowned[st].append(
                    (
                        county_code,
                        district,
                        f"school:{code}",
                        owned.get(f"school:{code}") or f"us/{st.lower()}/{slug(district)}",
                        "school",
                    )
                )
        with zipfile.ZipFile(raw.fetch(SOURCE, GAZETTEER, suffix=".zip").path) as archive:
            gazetteer = pl.read_csv(archive.read(archive.namelist()[0]), separator="\t", infer_schema=False).rename(
                lambda column: column.strip()
            )
        cities: dict[tuple[str, frozenset[str]], list[tuple[str, str, str]]] = defaultdict(list)
        for st, geoid, full in (
            gazetteer.filter(
                pl.col("USPS").is_in(["OR", "FL", "MI", "KY", "OK", "NV", "AR", "AZ", "MD", "LA", "IN", "NE", "WY", "NY"]) & (pl.col("FUNCSTAT") == "A")
            )
            .select("USPS", "GEOID", "NAME")
            .iter_rows()
        ):
            label = re.sub(r" (city|town|village)$", "", full)
            cities[(st, coverage_tables.tokens(label))].append((geoid, label, full))
            if st == "KY" and label.endswith(" City"):
                cities[(st, coverage_tables.tokens(label.removesuffix(" City")))].append((geoid, label, full))
        claimed = {
            path
            for (path,) in conn.execute("select path from slug where path ~ '^us/(or|fl|mi|ky|ok|nv|ar|az|md|la|in|ne|wy|ny)/[^/]+$'")
        }
        for st, county_code, label in conn.execute(
            """select g.state, g.county, g.name from coverage_government g
               where g.kind = 'municipality' and g.polity_id is null and g.county is not null and g.state in ('OR', 'FL', 'MI', 'KY', 'OK', 'NV', 'AR', 'AZ', 'MD', 'LA', 'IN', 'NE', 'WY', 'NY')"""
        ).fetchall():
            hits = cities.get((st, coverage_tables.tokens(label)), [])
            if len(hits) == 1:
                geoid, district, full = hits[0]
                path = f"us/{st.lower()}/{slug(district)}"
                unowned[st].append(
                    (
                        county_code,
                        district,
                        f"place:{geoid}",
                        owned.get(f"place:{geoid}") or (f"us/{st.lower()}/{slug(full)}" if path in claimed else path),
                        "municipality",
                    )
                )
        with zipfile.ZipFile(raw.fetch(SOURCE, COUSUB_GAZETTEER, suffix=".zip").path) as archive:
            cousubs_all = pl.read_csv(archive.read(archive.namelist()[0]), separator="\t", infer_schema=False).rename(
                lambda column: column.strip()
            )
        cousubs: dict[tuple[str, frozenset[str]], list[tuple[str, str]]] = defaultdict(list)
        for geoid, full in cousubs_all.filter((pl.col("USPS") == "KS") & (pl.col("FUNCSTAT") == "A")).select("GEOID", "NAME").iter_rows():
            cousubs[(geoid[:5], coverage_tables.tokens(re.sub(r" township$", "", full)))].append((geoid, full))
        counties_ks = {
            code: re.sub(r" County$", "", text.rsplit(",", 1)[0])
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
        for county_code, label in conn.execute(
            """select g.county, g.name from coverage_government g
               where g.kind = 'township' and g.polity_id is null and g.state = 'KS' and g.county is not null"""
        ).fetchall():
            hits = cousubs.get((county_code, coverage_tables.tokens(re.sub(r" Township$", "", label))), [])
            if len(hits) == 1 and county_code in counties_ks:
                geoid, full = hits[0]
                unowned["KS"].append(
                    (
                        county_code,
                        full,
                        f"town:{geoid}",
                        owned.get(f"town:{geoid}") or f"us/ks/{slug(full)}-{slug(counties_ks[county_code])}-county",
                        "township",
                    )
                )
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
                conn, state, {(str(row["fips"]), str(row["contest"])) for row in found if row.get("tail") is None and not row.get("mint")}
            )
            used: dict[str, str] = {}
            missed: set[str] = set()
            for row in [row for row in found if row.get("district")]:
                display = str(row["district"])
                office = str(row["contest"]).removeprefix(display).strip(" ,")
                head, _, seat = office.partition(", ")
                head = re.sub(r"^Directors?$", "Board of Directors", head)
                tail = f"{state}:{row['fips']}-{slug(display)}:board" + (f":{slug(seat)}" if seat else "")
                title = f"{head}, {seat}" if seat else head
                minted[tail] = (title, display, f"special:{row['fips']}:{slug(display)}", "")
                pinned.add(tail)
                templated[tail] = ""
                if used.setdefault(tail, str(row["contest"])) != row["contest"]:
                    missed.add(f"{row['county']}: {row['contest']}")
                    continue
                add(
                    key,
                    tail,
                    str(row["contest"]),
                    int(str(row["seats"])),
                    str(row["name"]),
                    None if row["party"] is None else str(row["party"]),
                    bool(row["independent"]),
                    str(row.get("feed") or key),
                )
            found = [row for row in found if not row.get("district")]
            towns: dict[str, list[tuple[list[str], str, str, str, str]]] = defaultdict(list)
            if "-" in key and key != "MN-county" or key in ("KY", "OK", "MD", "VA", "RI", "NC", "IN"):
                for county_code, label, owner, path, kind in [
                    *conn.execute(
                        """select distinct on (g.id) g.county, g.name, x.value, s.path, g.kind from coverage_government g
                       join external_id x on x.entity_id = g.polity_id and x.scheme = 'us.polity'
                           and x.value ~ '^(place|town|school):'
                       join slug s on s.entity_id = g.polity_id
                       where g.state = %s and (g.kind in ('municipality', 'township') or g.kind = 'school' and g.state in ('MI', 'CA', 'NJ', 'TX', 'UT', 'AZ', 'MD', 'VA', 'RI', 'LA', 'NC', 'IN', 'IL'))
                       order by g.id, x.value desc, s.path""",
                        [state],
                    ),
                    *unowned.get(state, []),
                ]:
                    stem = [
                        "mount" if word == "mt" else word
                        for word in re.findall(
                            r"[a-z0-9]+",
                            unicodedata.normalize("NFKD", re.sub(r"\s*\([^)]*\bCounty\)$", "", label) if kind != "school" else label)
                            .encode("ascii", "ignore")
                            .decode()
                            .lower()
                            .replace("'", "")
                            .replace("&", " and "),
                        )
                    ]
                    towns[county_code].append(
                        (
                            [
                                word
                                for word in stem
                                if not (kind == "school" and word.isdigit())
                                and word
                                not in (
                                    (
                                        "public",
                                        "schools",
                                        "school",
                                        "district",
                                        "community",
                                        "area",
                                        "consolidated",
                                        "elementary",
                                        "regional",
                                        "corporation",
                                        "city",
                                        "county",
                                        "metropolitan",
                                        "united",
                                        "sch",
                                        "dist",
                                        "pub",
                                    )
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
                if state == "CA":
                    held = {
                        path
                        for (path,) in conn.execute("select path from slug where path ~ '^us/ca/[^/]+/2026-11-03-general$'")
                    }
                    pools = dict(towns)
                    for county_code, pool in pools.items():
                        own = {tuple(town[0]) for town in pool if town[1] == "school"}
                        towns[county_code] = [
                            *pool,
                            *(
                                town
                                for other, others in pools.items()
                                if other != county_code
                                for town in others
                                if town[1] == "school" and tuple(town[0]) not in own and f"{town[4]}/2026-11-03-general" not in held
                            ),
                        ]
            for row in found:
                template, tail = tails.get((str(row["fips"]), str(row["contest"])), (None, None))
                if row.get("tail") is not None:
                    template = tail = str(row["tail"]) if row["tail"] in exact else None
                if key == "KY" and str(row["contest"]) in schools:
                    title, district, owner = schools[str(row["contest"])]
                    template, tail = "", f"KY:{owner.split(':')[1]}:board-of-education:{slug(title.split(', ')[1])}"
                    minted[tail] = (title, district, owner, owned.get(owner) or f"us/ky/{slug(district)}")
                if state in TERRITORIES:
                    head, _, seat = str(row["contest"]).partition(", ")
                    template, tail = "", f"{state}:{prefix}:{slug(head)}" + (f":{slug(seat)}" if seat else "")
                    minted[tail] = (str(row["contest"]), TERRITORIES[state], f"territory:{state}", f"us/{state.lower()}")
                    pinned.add(tail)
                place = places.get(str(row["fips"]), "").rsplit(",", 1)[0] or (
                    "Carson City" if row["fips"] == "32510" else ""
                )
                if row.get("area") or row.get("whole"):
                    office = re.sub(r"\s+", " ", str(row["contest"])).strip()
                    if row.get("whole"):
                        title = re.sub(rf"^{re.escape(place)}\s+", "", office)
                        template, tail = "", f"{state}:{row['fips']}:{slug(title)}"
                        minted[tail] = (title, place, "", "")
                    elif office.endswith("Board of Education"):
                        hits = index.get((prefix, coverage_tables.tokens(str(row["office"] or office.removesuffix(" Board of Education")))), [])
                        if len(hits) != 1:
                            missed.add(f"{row['county']}: {row['contest']} {row['area']}")
                            continue
                        code, district = hits[0]
                        owner = f"school:{code}"
                        template, tail = "", f"{state}:{code}:board-of-education:{slug(str(row['area']))}"
                        minted[tail] = (
                            f"Board of Education, {row['area']}",
                            district,
                            owner,
                            owned.get(owner) or f"us/{state.lower()}/{slug(district)}",
                        )
                        pinned.add(tail)
                    elif place and row["fips"]:
                        title = re.sub(rf"^{re.escape(place)}\s+", "", office)
                        template, tail = "", f"{state}:{row['fips']}:{slug(title)}:{slug(str(row['area']))}"
                        minted[tail] = (f"{title}, {row['area']}", place, "", "")
                        pinned.add(tail)
                tokens = [
                    "mount" if token == "mt" else "st" if token == "saint" else token
                    for token in re.findall(
                        r"[a-z0-9]+",
                        re.sub(
                            r"\bcharter township\b",
                            "township",
                            re.sub(
                                r"(?i)\btwp\b",
                                "township",
                                unicodedata.normalize("NFKD", str(row["contest"]))
                                .encode("ascii", "ignore")
                                .decode()
                                .lower()
                                .replace("'", "")
                                .replace("&", " and "),
                            ),
                        ),
                    )
                ]
                if tokens[:3] == ["governing", "board", "member"]:
                    tokens = tokens[3:]
                for formal in (["public", "schools", "of", "the", "city", "of"], ["school", "district", "of", "the", "city", "of"]):
                    if state == "MI" and tokens[: len(formal)] == formal:
                        tokens = [*tokens[len(formal) :], "schools"]
                fits = []
                for stem, sort, label, owner, path in (
                    [town for pool in towns.values() for town in pool]
                    if state in ("OR", "IN")
                    else [
                        town
                        for county_code, pool in towns.items()
                        for town in pool
                        if county_code == str(row["fips"]) or town[1] == "school"
                    ]
                    if state == "MI"
                    else [
                        (variant, *rest)
                        for full, *rest in towns.get(str(row["fips"]), [])
                        for variant in (
                            [full, [word for word in full if word != "and"]] if "and" in full else [full]
                        )
                        + (
                            [[word for word in full if word not in ("borough", "township", "city", "town", "village")]]
                            if rest[0] == "school" and state == "NJ"
                            else []
                        )
                    ]
                ):
                    for start in (
                        ([], *([word, "of"] for word in ("city", "village", "town", "township", "borough")))
                        if sort != "school"
                        else ([],)
                    ):
                        lead = start + ["st" if word == "saint" else word for word in stem]
                        skip = int(state == "IN" and sort == "school" and tokens[: len(lead)] != lead)
                        if stem and tokens[skip : skip + len(lead)] == lead:
                            rest = tokens[skip + len(lead) :]
                            if rest[:1] == ["county"] and sort != "school":
                                continue
                            said = (
                                rest[0]
                                if not start and sort != "school" and rest and rest[0] in ("township", "borough", "city", "town", "village")
                                else start[0]
                                if start
                                else ""
                            )
                            if (
                                state == "NY"
                                and not said
                                and rest
                                and (
                                    sort in ("", "village", "city")
                                    and rest[0] in ("supervisor", "highway", "assessor", "tax")
                                    or sort == "township"
                                    and rest[0] in ("mayor", "trustee")
                                )
                            ):
                                continue
                            if (
                                said
                                and (
                                    sort
                                    and said != sort
                                    and (said, sort) != ("town", "township")
                                    or not sort
                                    and (said == "township" or state == "NY" and said == "town")
                                )
                                or (sort == "school" or state in ("MI", "CA", "NJ", "TX", "UT", "AZ", "MD", "VA", "RI", "NC", "IN", "IL"))
                                and (sort == "school")
                                != bool(re.search(r"\b(school|schools|sch|education)\b", " ".join(rest)))
                            ):
                                continue
                            fits.append(
                                (len(stem), bool(said), rest[1:] if said and not start else rest, label, owner, path)
                            )
                if state == "IL" and re.search(r"(?i)\bregional (superintendent|office)\b", str(row["contest"])):
                    fits = []
                fits = [fit for fit in fits if fit[0] == max(f[0] for f in fits)]
                if len(fits) > 1:
                    fits = [fit for fit in fits if fit[1]] if len({fit[4] for fit in fits if fit[1]}) == 1 else fits
                if (
                    state == "NJ"
                    and row.get("tail") is None
                    and template
                    and len({fit[4] for fit in fits}) == 1
                    and not template.startswith(f"NJ:{fits[0][4].split(':', 1)[1]}:")
                ):
                    template = tail = None
                if (
                    template is None
                    and (
                        state in ("NJ", "MI", "FL", "OR", "NV", "AR", "NY", "RI", "ME", "MA", "IN", "NE", "WY")
                        or state == "KS"
                        and "Township" in str(row["contest"])
                        or state in ("KY", "OK")
                        and str(row["contest"]).startswith("City of ")
                        or state in ("CA", "TX", "UT", "AZ", "MD", "VA", "NC", "IL")
                        and bool(fits)
                    )
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
                    post = post.replace("receiver of taxes", "tax collector")
                    role = (
                        "board-of-education"
                        if owner.startswith("school:")
                        else next(
                            (
                                role
                                for pattern, role in (
                                    (
                                        (
                                            r"^(?:township |village |city |town )?(supervisor|clerk|treasurer|moderator|administrator)$"
                                            r"|^(?:town )?(?:clerk and )?tax collector$"
                                        ),
                                        "office",
                                    ),
                                    (r"^(?:town )?highway superintendent$", "highway-superintendent"),
                                    (r"^(?:town |village )?justice$", "justice"),
                                    (r"^(?:city )?court judge$", "judge"),
                                    (r"^village president$|^president$", "president"),
                                    (r"(?<!president of )(?<!deputy )\bmayor\b", "mayor"),
                                    (r"\bboard of education\b|\bschool board\b|\bboe\b", "board-of-education"),
                                    (r"\bcommittee\b", "committee"),
                                    (r"\bdirectors?\b", "board-of-directors"),
                                    (
                                        r"\bcouncil\b|\bcouncilors?\b|\bcouncilm[ae]n\b|\bcouncilmembers?\b|\bcouncilpersons?\b|\balderm[ae]n\b",
                                        "council",
                                    ),
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
                        r"\bward (\d+)\b|\b(\d+)(?:st|nd|rd|th)? ward\b|\bw(\d+)\b|\b(north|south|east|west|central) ward\b"
                        r"|\bward (f\d+)\b",
                        post,
                    )
                    seat = (
                        (
                            f"Ward {(ward[1] or ward[2] or ward[3] or ward[5]).upper()}"
                            + (
                                f" Position {pos[1]}"
                                if state in ("AR", "NV") and (pos := re.search(r"\bposition (\d+)\b", post))
                                else ""
                            )
                            if ward[4] is None
                            else f"{ward[4].title()} Ward"
                        )
                        if ward and role in ("council", "commission", "board-of-directors")
                        else f"{other[1].title()} {other[2]}"
                        if (other := re.search(r"\b(seat|district|group|place|zone|position) (\d+)\b", post))
                        and role in ("council", "commission", "board-of-directors")
                        else "At-Large"
                        if role in ("council", "commission", "board-of-directors") and re.search(r"\bat large\b", post)
                        else f"{slot[1].title()} {slot[2]}"
                        if state == "TX" and role == "board-of-education" and (slot := re.search(r"\b(place|position|district) (\d+)\b", post))
                        else f"{area[1].replace('area', 'trustee area').title()} {area[2].upper()}"
                        if state in ("CA", "UT", "MD", "IN", "NC")
                        and (
                            area := re.search(
                                r"\b(area|district|seat)(?: no)? 0*(\d+|(?<=area )[a-z](?= |$)|[a-g](?= |$))", post
                            )
                        )
                        else " ".join(rest[rest.index("board") + 1 :]).title()
                        if state == "IN" and role == "board-of-education" and "board" in rest
                        else chair[1].title()
                        if state == "IL" and role == "board-of-education" and (chair := re.search(r"\b(president|subdistrict \w+)\b", post))
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
                                "board-of-directors": "Board of Directors",
                                "supervisor": "Supervisor",
                                "clerk": "Clerk",
                                "moderator": "Moderator",
                                "administrator": "Administrator",
                                "collector": "Tax Collector",
                                "highway-superintendent": "Highway Superintendent",
                                "justice": "Justice",
                                "judge": "Court Judge",
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
                    and (
                        int(str(row["seats"]) or 0) <= 1
                        or re.search(r"(?i)\bcounty (council|commission|board district)\b", str(row["contest"])) is not None
                    )
                    and not LOCAL_BODY.search(
                        re.sub(re.escape(place) if state in ("MD", "LA", "WY") else "^$", "", str(row["contest"]).replace("Carson City", "Carson"), flags=re.IGNORECASE)
                    )
                    and (
                        "-" not in key
                        or key == "MN-county"
                        or re.match(rf"(?i){re.escape(place)}\b", str(row["contest"])) is not None
                    )
                ):
                    title = re.sub(
                        rf"(?i)^\s*(?:{re.escape(place)}(?:\s+county)?|{re.escape(place.removesuffix(' County'))}(?:\s+county)?)\s+",
                        "",
                        re.sub(r"\s+", " ", str(row["contest"])).strip(),
                    )
                    title = re.sub(
                        r"\b(Of|The|And|At)\b",
                        lambda found: found[1].lower(),
                        " ".join(
                            word
                            if word.isupper() and len(word) <= 3 and word != "AND" and not title.isupper()
                            else "/".join(part.capitalize() for part in word.split("/"))
                            for word in re.sub(r"(?i)\bby commissioner district district\b", "District", title).split()
                        ),
                    )
                    template, tail = "", f"{state}:{row['fips']}:{slug(title)}"
                    minted[tail] = (title, place, "", "")
                if tail is not None and not row["unexpired"]:
                    tail = tail.removesuffix(":remainder")
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
                    base = minted[tail.removesuffix(":unexpired")]
                    if ", " in base[0] or not base[2]:
                        missed.add(f"{row['county']}: {row['contest']} unexpired")
                        continue
                    minted[tail] = (f"{base[0]} (Unexpired Term)", *base[1:])
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
            pinned=pl.col("tail").is_in(sorted(pinned)),
        )
    )
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.group_by("fips").agg(pl.col("tail").n_unique().alias("contests"), pl.len().alias("candidates")))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    tail_set = set(frame["tail"])
    merged = {
        row["tail"]
        for row in frame.group_by("tail").agg(pl.col("unexpired").n_unique().alias("n")).filter(pl.col("n") > 1).iter_rows(named=True)
        if f"{row['tail']}:unexpired" in tail_set
    }
    frame = (
        frame.with_columns(
            tail=pl.when(pl.col("tail").str.replace(r":unexpired$", "").is_in(merged))
            .then(pl.col("tail").str.replace(r":unexpired$", ""))
            .otherwise(pl.col("tail"))
        )
        .with_columns(
            fips=pl.when(pl.col("tail").is_in(merged) & pl.col("unexpired"))
            .then(pl.col("fips").first().over("tail", "unexpired"))
            .otherwise(pl.col("fips"))
        )
        .filter(~pl.col("tail").is_in(merged) | pl.struct("tail", "unexpired", "name").is_first_distinct())
    )
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

        for tail, contest in conn.execute(
            """select substr(x.value, 12), x.entity_id from external_id x join changeset c on c.id = x.changeset_id
               join entity t on t.id = x.entity_id and t.retired_by is null
               where x.scheme = 'us.contest' and x.value like %s and c.pipeline = %s""",
            [f"{DAY}:%:remainder", SOURCE],
        ).fetchall():
            if tail.removesuffix(":remainder") not in set(frame["tail"]) or tail in set(frame["tail"]):
                continue
            reason = "This full-term race was filed under the office's last unexpired-term contest; it moves to the full-term seat."
            for (candidacy,) in conn.execute(
                "select ca.id from candidacy ca join contest_option o on o.id = ca.option_id where o.contest_id = %s",
                [contest],
            ).fetchall():
                cs.add("entity_retirement", entity_id=candidacy, reason=reason)
            cs.add("entity_retirement", entity_id=contest, reason=reason)

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
               join changeset pc on pc.id = co.changeset_id and pc.pipeline not in ('us.fl_primary_2026', 'us.ga_boe_2026')
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
        counts = {
            "contests": 0,
            "chained": 0,
            "created": 0,
            "candidates": 0,
            "matched": 0,
            "no template": 0,
            "no filing": 0,
            "dropped": 0,
            "stale contests": 0,
        }
        created: set[str] = set()
        made: set[object] = set()
        homes: dict[str, str] = {}
        homeless: set[str] = set()
        special_slugs: dict[str, dict[str, UUID]] = {}
        split_tails = {
            (row["fips"], row["tail"])
            for row in frame.group_by(["fips", "tail"])
            .agg(pl.col("unexpired").n_unique().alias("n"))
            .filter(pl.col("n") > 1)
            .iter_rows(named=True)
        }
        for (fips, tail, template, _), group in frame.group_by(
            ["fips", "tail", "template", "unexpired"], maintain_order=True
        ):
            if template == "" and group["owner"][0]:
                title, place, owner, home = (str(group[column][0]) for column in ("title", "place", "owner", "home"))
                st, code, role = tail.split(":")[:3]
                if owner.startswith(("school:", "place:")) and (
                    owner in homeless
                    or conn.execute(
                        "select 1 from external_id where scheme = 'us.polity' and value = %s", [owner]
                    ).fetchone()
                    is None
                    and conn.execute("select 1 from slug where path = %s", [home]).fetchone() is not None
                ):
                    homeless.add(owner)
                    counts["no template"] += 1
                    print(f"slug taken: {tail} {home}")
                    continue
                territory = owner.startswith("territory:")
                special = None
                if owner.startswith("special:"):
                    county = conn.execute(
                        """select t.polity_id, t.area_id, s.path, c.code from area_code c
                           join area a on a.id = c.area_id and a.scheme_code = 'us.county'
                           join polity_territory t on t.area_id = a.id
                           join polity p on p.id = t.polity_id and p.kind = 'county'
                           join slug s on s.entity_id = p.id and s.is_current
                           where c.code = %s limit 1""",
                        [owner.split(":")[1]],
                    ).fetchone()
                    if st not in special_slugs:
                        special_slugs[st] = dict(
                            conn.execute("select path, entity_id from slug where path like %s", [f"us/{st.lower()}/%"]).fetchall()
                        )
                    special = None if county is None else govmint.special(conn, cs, st, county, place, special_slugs[st])
                    if special is None:
                        counts["no template"] += 1
                        print(f"no special district: {tail} {owner}")
                        continue
                    home = special[3]
                polity = special[0] if special else cs.polity(f"US-{st}") if territory else cs.key("us.polity", owner)
                body = role not in (
                    "mayor", "vice-mayor", "supervisor", "clerk", "treasurer", "president", "judge",
                    "attorney-general", "public-auditor", "justice",
                )
                if polity in cs.minted and polity not in made and owner.startswith(("school:", "place:")):
                    made.add(polity)
                    geoid = owner.split(":")[1]
                    scheme, polity_kind = (
                        ("us.school_district", "school_district")
                        if owner.startswith("school:")
                        else ("us.place", "municipality")
                    )
                    cs.add("polity", id=polity, kind=polity_kind, iso_code=None)
                    name(polity, place)
                    cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{st}"))
                    cs.add("slug", path=home, entity_id=polity)
                    district_area = cs.area(scheme, geoid)
                    if district_area in cs.minted:
                        cs.add("area", id=district_area, scheme_code=scheme)
                        cs.add("area_code", area_id=district_area, code=geoid, valid_from=None)
                        cs.add("area_parent", area_id=district_area, parent_id=cs.area("us.state", geoid[:2]))
                        name(district_area, f"{place}, {st}")
                    cs.add("polity_territory", polity_id=polity, area_id=district_area)
                    cs.add(
                        "reporting_unit",
                        id=cs.unit(scheme, geoid),
                        polity_id=polity,
                        scheme_code=scheme,
                        code=geoid,
                        area_relation="equals",
                        area_id=district_area,
                        kind="standard",
                        valid_from=None,
                    )
                whole = (
                    (special[1],)
                    if special
                    else (cs.area("us.state", code),)
                    if territory
                    else (cs.area("us.school_district" if owner.startswith("school:") else "us.place", owner.split(":")[1]),)
                    if polity in cs.minted
                    else conn.execute(
                        "select t.area_id from polity_territory t join polity p on p.id = t.polity_id"
                        " join external_id x on x.entity_id = p.id and x.scheme = 'us.polity' and x.value = %s"
                        " order by t.valid_from desc nulls last limit 1",
                        [owner],
                    ).fetchone()
                )
                if whole is None:
                    counts["no template"] += 1
                    print(f"no territory: {tail} {owner}")
                    continue
                holder = cs.key("us.body" if body else "us.office", f"{st}:{code}:{role}")
                if holder in cs.minted and holder not in made:
                    made.add(holder)
                    if body:
                        cs.add(
                            "body",
                            id=holder,
                            polity_id=polity,
                            kind={"legislature": "unicameral", "senate": "upper_house", "house": "lower_house"}.get(role, "council")
                            if territory
                            else "council",
                        )
                        name(holder, f"{place} {title.split(',')[0]}")
                    else:
                        cs.add("office", id=holder, polity_id=polity, kind="mayor" if role == "mayor" else "other")
                        name(holder, f"Mayor of {place}" if role == "mayor" else f"{place} {title}")
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
                known_size = bool(group["seats"][0]) or not body or ", " in title
                templates[tail] = (
                    tail,
                    None,
                    "body_seats" if body else "office",
                    None if body else holder,
                    holder if body else None,
                    area,
                    None,
                    None,
                    (int(group["seats"][0] or 1) if group["pinned"][0] or role == "justice" else 1) if known_size else None,
                    "full",
                    cs.key("us.electoral_system", "fptp") if known_size else None,
                    "counted",
                    DAY,
                    polity,
                    f"2026 {place} {title} election",
                    f"{home}/{DAY}-general/{role}"
                    + (f"-{slug(title.split(', ', 1)[1])}" if ", " in title else "")
                    + ("-unexpired" if tail.endswith(":unexpired") else ""),
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
                seats = int(group["seats"][0] or 1)
                seat = title.split(", ", 1)[1] if group["pinned"][0] else None
                holder = tail.rsplit(":", 1)[0] if seat else tail
                office = cs.key("us.office" if seats == 1 else "us.body", holder)
                if office in cs.minted and office not in made:
                    made.add(office)
                    if seats == 1:
                        cs.add("office", id=office, polity_id=polity, kind="other")
                    else:
                        cs.add("body", id=office, polity_id=polity, kind="council")
                    name(office, f"{place} {title.split(', ')[0].removeprefix('County ')}")
                area = cs.area("us.county", county)
                if seat:
                    area = cs.area("us.local_district", tail)
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=tail, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", county), valid_from=None)
                        name(area, f"{place} {title.split(', ')[0]}, {seat}")
                templates[tail] = (
                    tail,
                    None,
                    "office" if seats == 1 else "body_seats",
                    office if seats == 1 else None,
                    None if seats == 1 else office,
                    None if seats == 1 else area,
                    None,
                    None,
                    seats,
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
                elif template == f"{tail}:remainder":
                    base[1], base[9] = None, "full"
                    base[14] = re.sub(r"(?i),? \(?unexpired term\)?| special(?= election)", "", base[14])
                    base[15] = re.sub(r"-(unexpired|remainder)(-term)?(?=-|$)", "", base[15])
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
            if area is None and office_id is None and body_id is not None:
                area = next(
                    iter(
                        conn.execute(
                            """select pt.area_id from body b join polity_territory pt on pt.polity_id = b.polity_id
                               where b.id = %s and pt.valid @> %s::date limit 1""",
                            [body_id, DAY],
                        )
                    ),
                    (None,),
                )[0]
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
            if str(group["feed"][0]).endswith("-rcv"):
                system = cs.key(
                    "us.electoral_system",
                    ("portland:" if tail.startswith(("OR:4159000:", "OR:portland:")) else "")
                    + ("stv" if int(group["seats"][0] or 1) > 1 else "irv"),
                )
            split = bool(group["unexpired"][0]) and (fips, tail) in split_tails
            key = f"{DAY}:{tail}" + (":unexpired" if split else "")
            try:
                contest = cs.key("us.contest", key)
            except ValueError:
                key = f"{key}:reissued"
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
                default_result_kind="tacit" if str(group["feed"][0]) in TACIT_FEEDS else result_kind,
            )
            text = re.sub(r"^\d{4} ", "2026 ", title)
            if group["unexpired"][0] and term == "full":
                text = text.replace(" election", " special election", 1)
            if chained:
                text = text.replace(
                    " election", " general municipal election" if fips == "0644000" else " runoff election", 1
                )
            cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
            cs.add(
                "slug",
                path=re.sub(r"/[0-9-]+-\w+/", f"/{DAY}-general/", path) + ("-unexpired" if split else ""),
                entity_id=contest,
            )
            counts["contests"] += 1
            if tail in created:
                counts["created"] += 1
                cs.add("citation", entity_id=contest, source_id=sources[str(group["feed"][0])], locator=None)
            counts["chained"] += chained
            if set(group["name"]) == {NO_FILING}:
                cs.add(
                    "label",
                    entity_id=contest,
                    kind="ballot",
                    value="no_candidate_qualified",
                    source_id=sources[str(group["feed"][0])],
                    valid_from=DAY,
                )
                counts["no filing"] += 1
            for row in group.iter_rows(named=True):
                if row["name"] == NO_FILING:
                    continue
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
                    person = cs.key(
                        f"{SOURCE}.person",
                        f"{template if template == f'{tail}:remainder' else tail}:{slug(row['name'])}",
                    )
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, row["name"])
                party = cs.key("us.party", row["party"]) if row["party"] else None
                if party in cs.minted and row["party"] in NEW_PARTIES and row["party"] not in NEW_PARTIES_ADDED:
                    NEW_PARTIES_ADDED.add(row["party"])
                    cs.add("party", id=party, kind="party", polity_id=cs.polity("US" if row["party"] == "forward-party" else "US-NY"))
                    name(party, NEW_PARTIES[row["party"]])
                try:
                    option = cs.key("us.option", f"{key}:{slug(row['name'])}")
                except ValueError:
                    option = cs.key("us.option", f"{key}:{slug(row['name'])}:reissued")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=row["name"],
                )
                try:
                    candidacy = cs.key("us.candidacy", f"{key}:{slug(row['name'])}")
                except ValueError:
                    candidacy = cs.key("us.candidacy", f"{key}:{slug(row['name'])}:reissued")
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
        options = {str(row["id"]) for row in cs.rows.get("contest_option", [])}
        for option, candidacy, value in conn.execute(
            """select o.id, ca.id, x.value from contest_option o join changeset c on c.id = o.changeset_id
               join entity e on e.id = o.id and e.retired_by is null
               left join candidacy ca on ca.option_id = o.id
               left join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
               where c.pipeline = %s and o.contest_id = any(%s::uuid[])""",
            [SOURCE, [str(row["id"]) for row in cs.rows.get("contest", [])]],
        ).fetchall():
            if str(option) not in options:
                print("dropping", value)
                reason = "No longer on the newest official candidate list for this race"
                if candidacy is not None:
                    cs.add("entity_retirement", entity_id=candidacy, reason=reason)
                cs.add("entity_retirement", entity_id=option, reason=reason)
                counts["dropped"] += 1
        emitted = [str(row["id"]) for row in cs.rows.get("contest", [])]
        reason = "No longer on any official candidate list or sample ballot for this election"
        for option, candidacy, value in conn.execute(
            """select o.id, ca.id, x.value from contest_option o join changeset c on c.id = o.changeset_id
               join entity e on e.id = o.id and e.retired_by is null
               join contest co on co.id = o.contest_id join election el on el.id = co.election_id
               left join candidacy ca on ca.option_id = o.id
               left join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
               where c.pipeline = %s and el.date_from = %s and o.contest_id <> all(%s::uuid[])""",
            [SOURCE, DAY, emitted],
        ).fetchall():
            print("dropping stale", value)
            if candidacy is not None:
                cs.add("entity_retirement", entity_id=candidacy, reason=reason)
            cs.add("entity_retirement", entity_id=option, reason=reason)
            counts["dropped"] += 1
        for contest, value in conn.execute(
            """select co.id, x.value from contest co join entity e on e.id = co.id and e.retired_by is null
               join election el on el.id = co.election_id
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               join changeset c on c.id = x.changeset_id
               where c.pipeline = %s and el.date_from = %s and co.id <> all(%s::uuid[])
                   and not exists (
                       select from contest_option o join entity oe on oe.id = o.id and oe.retired_by is null
                       join changeset oc on oc.id = o.changeset_id
                       where o.contest_id = co.id and oc.pipeline <> %s
                   )""",
            [SOURCE, DAY, emitted, SOURCE],
        ).fetchall():
            print("retiring stale contest", value)
            cs.add("entity_retirement", entity_id=contest, reason=reason)
            counts["stale contests"] += 1
        print(counts)
        return cs.write()
