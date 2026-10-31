import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.nov3_manual_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Official county ballot measures and sample ballots, November 3, 2026 election"
SCHOOLS = {
    ("NJ", "Rumson School District"): "3414370",
    ("NJ", "Washington Township School District"): "3417040",
    ("TX", "Georgetown Independent School District"): "4820600",
    ("TX", "Irving Independent School District"): "4824420",
    ("TX", "Northside Independent School District"): "4833120",
    ("TX", "Schertz-Cibolo-Universal City Independent School District"): "4839480",
    ("TX", "South San Antonio Independent School District"): "4840680",
    ("CA", "Fallbrook Union Elementary School District"): "0613500",
    ("CA", "Julian Union High School District"): "0619110",
    ("CA", "Galt Joint Union High School District"): "0614820",
    ("CA", "Jefferson Union High School District"): "0618930",
    ("CA", "Perris Union High School District"): "0630210",
    ("CA", "Acalanes Union High School District"): "0601650",
    ("CA", "Liberty Union High School District"): "0621600",
    ("CA", "West Contra Costa Unified School District"): "0632550",
    ("WA", "Kiona-Benton City School District No. 52"): "5304020",
    ("WA", "Yelm Community Schools"): "5310140",
    ("WA", "Auburn School District No. 408"): "5300300",
    ("WA", "Nine Mile Falls School District No. 325-179"): "5305640",
    ("WA", "Quillayute Valley School District No. 402"): "5307020",
    ("ID", "Genesee Joint School District No. 282"): "1601200",
    ("ID", "Highland Joint School District No. 305"): "1601440",
    ("ID", "Moscow School District No. 281"): "1602220",
    ("AZ", "Prescott Unified School District No. 1"): "0406730",
    ("AZ", "Wickenburg Unified School District No. 9"): "0409190",
    ("AZ", "Humboldt Unified School District No. 22"): "0403870",
    ("AZ", "Camp Verde Unified School District No. 28"): "0400813",
    ("AZ", "Cottonwood-Oak Creek Elementary School District No. 6"): "0402370",
}
MANUAL = DATA / "inbox" / "manual"
ROWS_FILE = Path(__file__).with_name("nov3_manual_2026.json")
COUNTIES = {
    "35001": (
        "NM",
        "https://www.bernco.gov/public-works/capital-improvement-program/general-obligation-bonds/",
        "MF-387/bernalillo/bernalillo-general-obligation-bonds.html",
    ),
    "05007": (
        "AR",
        "https://bentoncountyar.gov/election-commission/",
        "MF-387/benton-ar/benton-ar-election-commission.html",
    ),
    "04013": (
        "AZ",
        "https://elections.maricopa.gov/candidates/county-ballot-measures.html",
        "MF-387/maricopa/maricopa-county-ballot-measures.html",
    ),
    "04019": (
        "AZ",
        "https://www.recorder.pima.gov/PressRelease/2026/11.3%20General%20Election%20What%20Voters%20Need%20to%20Know.pdf",
        "MF-387/pima/11.3 General Election What Voters Need to Know.pdf",
    ),
    "04021": (
        "AZ",
        "https://www.pinal.gov/1847/2026-General-Election-Sample-Ballots",
        "MF-387/pinal/pinal-2026-general-election-sample-ballots.html",
    ),
    "45019": ("SC", "https://vote.charlestoncounty.gov/", "MF-390/sc-charleston-2026-11-03-local-questions.html"),
    "45043": ("SC", "https://www.gtcountysc.gov/upcoming-elections/", "MF-390/22110326_abs_sample (3).pdf"),
    "13035": ("GA", "https://www.buttscounty.org/elections", "MF-390/ga-butts-2026-11-03-referendum-notice.html"),
    "13255": (
        "GA",
        "https://www.spaldingcountyelections.com/",
        "MF-390/RESOLUTION-CORRECTED-WITH-BOER-CHAIR-SIGNATURE.pdf",
    ),
    "31055": ("NE", "https://www.votedouglascounty-ne.gov/", "MF-390/GN26SampleBallot.pdf"),
    "31109": ("NE", "https://www.lancaster.ne.gov/", "MF-390/LCEC General Sample Ballot.pdf"),
    "31079": ("NE", "https://files.hallcountyne.gov/", "MF-390/26GNEHALL_7_PUB.pdf"),
    "48135": (
        "TX",
        "https://www.ectorcountytx.gov/271/Elections",
        "MF-392/ector/Pct. 201, 202, 203, 204.3, 206.5, 209_ybmswlfq61.pdf",
    ),
    "04023": (
        "AZ",
        "https://www.nogalesaz.gov/336/Proposition-418---Alternative-Expenditur",
        "MF-404/nogales-az-2026-general-proposition-418.html",
    ),
    "06059": (
        "CA",
        "https://ocvote.org/elections/2026-general-election-measures-on-the-ballot",
        "MF-nov3-ca/orange/notice-of-election-nov2026.pdf",
    ),
    "06073": (
        "CA",
        "https://www.sdvote.com/content/dam/rov/en/election/nov-3-2026-gubernatorial-general-election/argument-deadline-measures.pdf",
        "MF-nov3-ca/san-diego/argument-deadline-measures.pdf",
    ),
    "06067": (
        "CA",
        "https://elections.saccounty.gov/content/vre/us/en/election-information/current-measure-information.html",
        "MF-nov3-ca/sacramento/current-measure-information.html",
    ),
    "06081": (
        "CA",
        "https://www.smcacre.gov/elections/november-3-2026-statewide-general-election",
        "MF-nov3-ca/san-mateo/county-amendments-resolutions.pdf",
    ),
    "04015": (
        "AZ",
        "https://www.bullheadcityaz.gov/leadership-government/elections/",
        "bullhead/publicity-pamphlet-26.pdf",
    ),
    "48209": (
        "TX",
        "https://www.hayscountytx.gov/elections",
        "MF-392/hays/Hays County Combined Sample Ballot 11x17.pdf",
    ),
    "48453": ("TX", "https://countyclerk.traviscountytx.gov/elections/", "../y26/tx/travis.pdf"),
    "48167": (
        "TX",
        "https://www.galvestoncountytx.gov/our-county/county-clerk/elections",
        "MF-392/galveston/November-2026-General-Election-Ballot-and-Measures.pdf",
    ),
    "48201": ("TX", "https://www.harrisvotes.com/", "../y26/tx/harris_sample.pdf"),
    "48085": ("TX", "https://www.collincountytx.gov/Elections", "../y26/tx/collin.pdf"),
    "48121": ("TX", "https://www.dentoncounty.gov/elections", "../y26/tx/denton_preview.pdf"),
    "48141": ("TX", "https://www.epcounty.com/elections/", "../y26/tx/tornillo_isd.pdf"),
    "06001": (
        "CA",
        "https://acvote.alamedacountyca.gov/election-information/elections",
        "MF-423/alameda-2026-11-03-measures.html",
    ),
    "06065": ("CA", "https://voteinfo.net/", "MF-423/Measure A - Riverside County Transportation Commission.pdf"),
    "06085": ("CA", "https://vote.santaclaracounty.gov/", "MF-423/santa-clara-2026-11-03-list-of-local-measures.html"),
    "06111": ("CA", "https://clerkrecorder.venturacounty.gov/elections/", "MF-423/ventura-2026-11-03-measures.html"),
    "06019": (
        "CA",
        "https://www.fresnocountyca.gov/Departments/County-ClerkRegistrar-of-Voters/Elections/Election-Information/2026/November-3-2026-Consolidated-Statewide-General-Election",
        "MF-423/fresno-2026-11-03-measures.html",
    ),
    "06013": ("CA", "https://www.cocovote.us/", "MF-427/26Nov03_Measure-Wording-List-1.pdf"),
    "44001": (
        "RI",
        "https://www.barrington.ri.gov/DocumentCenter/View/2978/BOC-Booklet-Questions-Info-revised-10-6",
        "RI-web/barrington-boc-booklet.pdf",
    ),
    "44005": (
        "RI",
        "https://portsmouthri.gov/DocumentCenter/View/7981/Portsmouth-Local-Ballot-Question-Explanation-2026",
        "RI-web/portsmouth-local-questions.pdf",
    ),
    "38015": (
        "ND",
        "https://www.bismarckschools.org/fs/resource-manager/view/11ac6ea4-ff0d-4486-84da-16289d7f918f",
        "ND-web/bps-official-ballot.pdf",
    ),
    "53005": (
        "WA",
        "https://www.bentoncountywa.gov/Documents/Government/Elected%20Officials/Auditor/Elections/Current%20Election/Sample%20Ballot.pdf",
        "N3M-web/wa_benton_sample.pdf",
    ),
    "53021": ("WA", "https://www.franklincountywa.gov/DocumentCenter/View/4553", "N3M-web/wa_franklin_vp_nov26.pdf"),
    "04025": (
        "AZ",
        "https://www.azcleanelections.gov/november-3-2026/yavapai",
        "N3M-web/yavapai-countywide-sample.pdf",
    ),
    "05051": (
        "AR",
        "https://www.garlandcountyvote.org/uploads/6/3/0/4/63045401/local_issues.pdf",
        "N3M-web/garland-local-issues.pdf",
    ),
    "05131": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/september/2026-arkansas-ballot-issues-voter-guide-now-available-online.aspx",
        "N3M-web/lavaca-2026.pdf",
    ),
    "05031": (
        "AR",
        "https://jonesbororightnow.com/wp-content/blogs.dir/286/files/2026/07/JB-Public-Safety-Sales-Use-Tax-Election-County-Clerk-Filing.pdf",
        "N3M-web/jonesboro-psst.pdf",
    ),
    "05125": (
        "AR",
        "https://arkansasadvocate.com/wp-content/uploads/2026/04/2026-Saline-County-library-funding-cut-proposal.pdf",
        "N3M-web/ar/ar-saline.pdf",
    ),
    "05089": (
        "AR",
        "https://irp.cdn-website.com/091beef6/files/uploaded/Ordinance+2026-07.pdf",
        "N3M-web/ar/ar-marion.pdf",
    ),
    "16069": (
        "ID",
        "https://nezpercecountyelections.gov/wp-content/uploads/SampleBallot_Nov2026.pdf",
        "N3M-web/id/nezperce-sample-ballot.pdf",
    ),
    "16057": (
        "ID",
        "https://www.latahcountyid.gov/auditor/elections",
        "MF-431/Latah/LATAH COUNTY VOTER GUIDE NOV 2026.PDF",
    ),
    "35049": (
        "NM",
        "https://www.santafecountynm.gov/uploads/documents/2026_Constitutional_Amendments_and_Bond_Questions_General_Election.pdf",
        "N3M-web/nm/santafe-county-bond-questions.pdf",
    ),
    "05075": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Lawrence%20County%20election%202026.pdf",
        "N3M-web/ar/ar-lawrence.pdf",
    ),
    "05107": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Phillips%20County%20hospital%20election%202026.pdf",
        "N3M-web/ar/ar-phillips.pdf",
    ),
    "05137": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Stone%20County%202026.pdf",
        "N3M-web/ar/ar-stone.pdf",
    ),
    "05059": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Bismark%20Fire%20Protection%20Association%20ballot%20election%202026.pdf",
        "N3M-web/ar/ar-hotspring.pdf",
    ),
    "05037": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Parkin%20ballot%20issue%20election%202026.pdf",
        "N3M-web/ar/ar-cross.pdf",
    ),
    "05035": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Crittenden%20County%202026.pdf",
        "N3M-web/ar/ar-crittenden.pdf",
    ),
    "05057": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Hempstead%20County%20election%202026.pdf",
        "N3M-web/ar/ar-hempstead.pdf",
    ),
    "05015": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Carroll%20County%20Ballot%20Proof%20Eureka%20Springs%20City%20Limits.pdf",
        "N3M-web/ar/ar-carroll.pdf",
    ),
    "05021": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Corning%20Ballot%20Issue%20General%202026.pdf",
        "N3M-web/ar/ar-clay.pdf",
    ),
    "05063": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Pleasant%20Plains%20ballot%20issue%20election%202026.pdf",
        "N3M-web/ar/ar-independence.pdf",
    ),
    "05139": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Strong%20ballot%20issue%20election%202026.pdf",
        "N3M-web/ar/ar-union.pdf",
    ),
    "05121": (
        "AR",
        "https://www.uaex.uada.edu/business-communities/ced-blog/posts/2026/august/Randolph%20County%20ballot%20issue%20election%202026.pdf",
        "N3M-web/ar/ar-randolph.pdf",
    ),
    "48491": (
        "TX",
        "https://www.wilcotx.gov/elections",
        "MF-392/williamson/20261103 November JGSE - COMPREHENSIVE BALLOT.pdf",
    ),
    "53053": ("WA", "https://www.piercecountywa.gov/328/Elections", "MF-428/Sample Ballot_15.pdf"),
    "53009": ("WA", "https://clallamcountywa.gov/2002", "MF-428/2026 General Sample Ballot_w7x8zqq7zwj.pdf"),
    "53025": ("WA", "https://www.grantcountywa.gov/1374", "MF-428/November 2026 SAMPLE BALLOT.pdf"),
    "53015": ("WA", "https://www.co.cowlitz.wa.us/2357/Elections", "MF-428/G126 Sample Ballot.pdf"),
    "53065": ("WA", "https://www.stevenscountywa.gov/document/38435", "MF-428/1315012300092526pm.pdf"),
    "53041": ("WA", "https://lewiscountywa.gov/", "MF-428/Nov_2026_Sample_Ballot.pdf"),
    "53029": ("WA", "https://www.islandcountywa.gov/", "MF-428/Sample_Ballot_202609291007164234 (1).pdf"),
    "53063": (
        "WA",
        "https://www.spokanecounty.gov/3418/Ballot-Resolutions",
        "MF-441/2026 regular ems tax levy resolution.pdf",
    ),
    "48113": ("TX", "https://www.dallascounty.org/government/county-clerk/", "N3M-web/tx/dallas-bond-election.pdf"),
    "34029": ("NJ", "https://www.lacey-nj.org/", "N3M-web/nj/lacey-doc669.pdf"),
    "34037": ("NJ", "https://www.sussexcountyclerk.org/", "N3M-web/nj/4744.pdf"),
    "34039": ("NJ", "https://ucnj.org/board-of-elections/", "N3M-web/nj/f7.pdf"),
    "34025-mun": (
        "NJ",
        "https://www.monmouthcountyvotes.com/",
        "MF-438/2026-11-03-General-Election-Questions-Municipal.pdf",
    ),
    "34025-boe": (
        "NJ",
        "https://www.monmouthcountyvotes.com/",
        "MF-438/2026-11-03-General-Election-Questions-Board-of-Education2.pdf",
    ),
    "34003-dumont": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/Dumont-26GEN.pdf"),
    "34003-flakes": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/Franklin-Lakes-26GEN.pdf"),
    "34003-nmil": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/New-Milford-26GEN.pdf"),
    "34003-orad": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/Oradell-26GEN.pdf"),
    "34003-pridge": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/Park-Ridge-26GEN.pdf"),
    "34003-tenafly": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/Tenafly-26GEN.pdf"),
    "34003-wyck": ("NJ", "https://www.bergencountyclerk.org/", "MF-438/Wyckoff-26GEN.pdf"),
    "34017-enew": ("NJ", "https://www.hudsoncountyclerk.org/", "MF-438/2026-F02-East-Newark.pdf"),
    "34017-gutt": ("NJ", "https://www.hudsoncountyclerk.org/", "MF-438/2026-F03-Guttenberg.pdf"),
    "34031": ("NJ", "https://www.passaiccountynj.org/", "MF-438/Passaic 2026g Sample Pompton Lakes.pdf"),
    "34041-allam": ("NJ", "https://www.warrencountynj.gov/", "MF-438/Allamuchy BOE Question  26 General.pdf"),
    "34041-belv": ("NJ", "https://www.warrencountynj.gov/", "MF-438/Belvidere Public Question  26 General.pdf"),
    "34041-mans": ("NJ", "https://www.warrencountynj.gov/", "MF-438/Mansfield Public Question  26 General.pdf"),
    "34041": ("NJ", "https://www.warrencountynj.gov/", "MF-438/Warren County Public Question  26 General.pdf"),
    "34035-branch": (
        "NJ",
        "https://www.co.somerset.nj.us/government/county-clerk",
        "MF-438/Branchburg Question 2026.docx",
    ),
    "34035-warren": ("NJ", "https://www.co.somerset.nj.us/government/county-clerk", "MF-438/Warren Question 2026.docx"),
    "34005": ("NJ", "https://www.co.burlington.nj.us/", "MF-438/Burlington Samples ALL Gen26.pdf"),
    "34013": ("NJ", "https://www.essexclerk.com/", "MF-438/Essex-2026-Sample-Ballots.pdf"),
    "34007": ("NJ", "https://www.camdencounty.com/", "MF-438/LAUREL_SPRINGS.pdf"),
    "48355": (
        "TX",
        "https://www.corpuschristitx.gov/media/dhllgpuy/2026-cocc-notice-of-general-and-special-election-updated-english.pdf",
        "N3M-web/tx4/cc-notice.pdf",
    ),
    "34007-coll": ("NJ", "https://www.camdencounty.com/", "MF-438/COLLINGSWOOD.pdf"),
    "34019": ("NJ", "https://www.co.hunterdon.nj.us/", "MF-440/public questions website.pdf"),
    "34021-hb": ("NJ", "https://nj-mercercounty.civicplus.com/", "MF-440/F07 Hopewell Borough General 2026.pdf"),
    "34021": ("NJ", "https://nj-mercercounty.civicplus.com/", "MF-440/F08 Hopewell Township General 2026.pdf"),
    "34023-ob": (
        "NJ",
        "https://www.middlesexcountynj.gov/",
        "MF-440/2026 General Election  Old Bridge Sample Ballots.pdf",
    ),
    "34023-say": (
        "NJ",
        "https://www.middlesexcountynj.gov/",
        "MF-440/2026 General Election  Sayreville Sample Ballots.pdf",
    ),
    "34011-br": (
        "NJ",
        "https://www.cumberlandcountynj.gov/",
        "MF-440/Bridgeton-Sample-Ballot-2026-General-Election.pdf",
    ),
    "34011": ("NJ", "https://www.cumberlandcountynj.gov/", "MF-440/Downe-Sample-Ballot-2026-General-Election.pdf"),
    "48439": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_notices/06_NRH-1126_Notice_of_Election_Eng.pdf",
        "N3M-web/tx2/nrh_eng.pdf",
    ),
    "48029-comal": (
        "TX",
        "https://elections.bexar.org/#comal-isd-order-of-bond-election",
        "MF-433/CISD Notice of Order of Bond Election signed with exhibits FINAL 9.16.26_1.pdf",
    ),
    "48029-nisd": (
        "TX",
        "https://elections.bexar.org/#northside-isd-order-of-bond-election",
        "MF-433/Northside Independent School District-Order of Bond Election.pdf",
    ),
    "48029-nisd-tax": (
        "TX",
        "https://elections.bexar.org/#northside-isd-tax-increase-ballot",
        "MF-433/Northside Independent School District-Tax Increase Ballot.pdf",
    ),
    "48029-scuc": (
        "TX",
        "https://elections.bexar.org/#schertz-cibolo-universal-city-isd-vatre-order",
        "MF-433/Schertz-Cibolo Universal City Independent School District-Vatre Order.pdf",
    ),
    "48029-ssan": (
        "TX",
        "https://elections.bexar.org/#south-san-antonio-isd-vatre-order",
        "MF-433/South San Indenpendent School District -Order of Voter-Approval Tax Ratification Election.pdf",
    ),
    "04027": (
        "AZ",
        "https://www.yumacountyaz.gov/government/elections#full-text-ballot-insert-2026-general",
        "MF-434/RB261266 Yuma Full Text 1126 17x11_PROOF_260831_v03.pdf",
    ),
    "04013-wick": (
        "AZ",
        "https://recorder.maricopa.gov/#wickenburg-usd-override-pamphlet-2026",
        "MF-434/VIP-2026-Wickenburg-USD-OR-Final-Pamphlet.pdf",
    ),
    "48121-flm": (
        "TX",
        "https://www.tarrantcountytx.gov/en/elections.html#flower-mound-notice-of-special-election-2026",
        "MF-434/NOTICE OF SPECIAL ELECTION - TRANSLATED - FINAL UPDATED_202610071731278860.pdf",
    ),
    "48439-ems-v": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_notices/08EMS_VATRE.zip",
        "MF-434-ems/08EMS_VATRE/EAGLE MOUNTAIN-SAGINAW ISD NOV 2026 - Final Notice of Voter-Approval Tax Rate Election.pdf",
    ),
    "48439-ems-b": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_notices/08EMS_BOND.zip",
        "MF-434/EMSISDOrderofBondElection2026_11_03_English.pdf",
    ),
    "48439-kel": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_notices/08_KEL_Notice_of_Bond_Election.pdf",
        "N3M-web/tx3/kel.pdf",
    ),
    "48113-dc": (
        "TX",
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/dallas-tx.connect.clarityelections.com/SampleBallots/2026/261103GeneralJoint/110.pdf",
        "N3M-web/tx5/dallas-county-ballot-110.pdf",
    ),
    "48113-gar": (
        "TX",
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/dallas-tx.connect.clarityelections.com/SampleBallots/2026/261103GeneralJoint/130.pdf",
        "N3M-web/tx5/dallas-county-ballot-130.pdf",
    ),
    "48113-wyl": (
        "TX",
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/dallas-tx.connect.clarityelections.com/SampleBallots/2026/261103GeneralJoint/134.pdf",
        "N3M-web/tx5/dallas-county-ballot-134.pdf",
    ),
    "48113-mes": (
        "TX",
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/dallas-tx.connect.clarityelections.com/SampleBallots/2026/261103GeneralJoint/197.pdf",
        "N3M-web/tx5/dallas-county-ballot-197.pdf",
    ),
    "48157-m244": (
        "TX",
        "https://www.fortbendcountytx.gov/sites/default/files/document-central/document-central/elections-documents/election-notices/2026/MUD-244_1.pdf",
        "N3M-web/tx6/2026_MUD-244_1.pdf",
    ),
    "48157-w13": (
        "TX",
        "https://www.fortbendcountytx.gov/sites/default/files/document-central/document-central/elections-documents/election-notices/2026/WCID-13_1.pdf",
        "N3M-web/tx6/2026_WCID-13_1.pdf",
    ),
    "48157-m287a": (
        "TX",
        "https://www.fortbendcountytx.gov/sites/default/files/document-central/document-central/elections-documents/election-notices/2026/MUD-287A.pdf",
        "N3M-web/tx6/2026_MUD-287A.pdf",
    ),
    "48157-m288": (
        "TX",
        "https://www.fortbendcountytx.gov/sites/default/files/document-central/document-central/elections-documents/election-notices/2026/MUD-288_1.pdf",
        "N3M-web/tx6/2026_MUD-288_1.pdf",
    ),
    "53025-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=13",
        "N3M-web/wa3/c13.html",
    ),
    "53027-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=14",
        "N3M-web/wa3/c14.html",
    ),
    "53029-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=15",
        "N3M-web/wa3/c15.html",
    ),
    "53039-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=20",
        "N3M-web/wa3/c20.html",
    ),
    "53045-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=23",
        "N3M-web/wa3/c23.html",
    ),
    "53047-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=24",
        "N3M-web/wa3/c24.html",
    ),
    "53049-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=25",
        "N3M-web/wa3/c25.html",
    ),
    "53051-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=26",
        "N3M-web/wa3/c26.html",
    ),
    "53055-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=28",
        "N3M-web/wa3/c28.html",
    ),
    "53071-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=36",
        "N3M-web/wa3/c36.html",
    ),
    "53075-vw": (
        "WA",
        "https://voter.votewa.gov/GenericVoterGuide.aspx?e=899&c=38",
        "N3M-web/wa3/c38.html",
    ),
    "53007-ba": (
        "WA",
        "https://www.co.chelan.wa.us/elections/pages/november-3-2026-general-election",
        "N3M-web/wa2/chelan_ballot.pdf",
    ),
    "53017-ba": (
        "WA",
        "https://www.douglascountywa.gov/DocumentCenter/View/13224/Sample-Ballot_Gen26",
        "N3M-web/wa2/douglas_ballot.pdf",
    ),
}


def fetch() -> None:
    for _, url, file in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / file
            suffix = path.suffix
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf" if suffix == ".pdf" else "text/html",
            )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "county": row["county_fips"],
            "state": row["state"],
            "publisher": row["publisher"],
            "entity": row["place"],
            "label": f"{row['number']}: {row['title']}" if row["number"] is not None else row["title"],
            "wording": row["text"],
        }
        for row in json.loads(ROWS_FILE.read_text())
    ]
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Local referenda on the November 3, 2026 ballot in Arizona, New Mexico, Arkansas, Georgia, South Carolina, Nebraska, Texas, California, Rhode Island, North Dakota, Washington, Idaho, and Arizona, with official wording",
            polity="US",
        )
        civils = {state: civmint.Civil(conn, cs, fips[:2], state) for fips, (state, _, _) in COUNTIES.items()}
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        added: set[UUID] = set()
        for county, publisher in frame.select("county", "publisher").unique("county").iter_rows():
            _, url, _ = COUNTIES[county]
            sources[county] = cs.source(publisher, TITLE)
            if sources[county] not in added:
                added.add(sources[county])
                cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            state = row["state"]
            civil = civils[state]
            found = civil.found
            parent = cs.key("us.election", f"{DAY}:{state}")
            url = COUNTIES[row["county"]][1]
            entity = row["entity"]
            county_key = f"us.county:{row['county'][:5]}"
            source = sources[row["county"]]
            option = None
            if re.match(r"(?i)(city|town|village|borough) of ", entity):
                bare = re.sub(r"(?i)^(city|town|village|borough) of ", "", entity)
                option = civil.place(bare) or civil.towns.get((row["county"][:5], civmint.town_core(bare)))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif entity.endswith(" Township"):
                option = civil.town(entity, row["county"][:5])
            elif (state, entity) in SCHOOLS:
                option = f"us.school_district:{SCHOOLS[state, entity]}"
                if option not in found:
                    option = civil.mint("us.school_district", SCHOOLS[state, entity], entity)
            elif re.search(r"School District|CUSD", entity):
                option = civil.school(entity)
            elif f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(
                    conn, cs, state, (county, county_area, county_path, county_code), entity, civil.slugs
                )
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = civil.slugs.get(f"{path}/{DAY}-general") or cs.key("us.election", f"{DAY}:{state}:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            contest_key = f"{DAY}:{state}:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if civil.slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            civil.slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {place}: {label}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=url,
                source_id=source,
            )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
