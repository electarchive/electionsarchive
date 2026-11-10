import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint, measure_rules
from ea.store import DATA, connect

SOURCE = "us.nov3_manual_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Official county ballot measures and sample ballots, November 3, 2026 election"
SCHOOLS = {
    ("ID", "American Falls Joint School District No. 381"): "1600060",
    ("TX", "Nederland Independent School District"): "4832280",
    ("CA", "Modesto City High School District"): "0625150",
    ("TX", "Nixon-Smiley Consolidated Independent School District"): "4832740",
    ("TX", "Poth Independent School District"): "4835550",
    ("TX", "Stockdale Independent School District"): "4841610",
    ("TX", "Falls City Independent School District"): "4819020",
    ("TX", "Leon Independent School District"): "4827180",
    ("TX", "Weimar Independent School District"): "4844820",
    ("TX", "Graham Independent School District"): "4821360",
    ("TX", "Tulia Independent School District"): "4843320",
    ("TX", "Groom Independent School District"): "4821840",
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
    ("WA", "Dixie School District No. 101"): "5302160",
    ("WA", "Eastmont School District No. 206"): "5302310",
    ("WA", "McCleary School District No. 65"): "5304890",
    ("WA", "Pioneer School District No. 402"): "5306750",
    ("WA", "Southside School District No. 42"): "5308220",
    ("WA", "Wenatchee School District No. 246"): "5309660",
    ("TX", "Mesquite Independent School District"): "4830390",
    ("TX", "Lubbock-Cooper Independent School District"): "4815180",
    ("TX", "Lorenzo Independent School District"): "4828230",
    ("TX", "Sweeny Independent School District"): "4841970",
    ("TX", "Bryan Independent School District"): "4811790",
    ("TX", "Navasota Independent School District"): "4832190",
    ("TX", "Robinson Independent School District"): "4837410",
    ("ID", "Genesee Joint School District No. 282"): "1601200",
    ("ID", "Highland Joint School District No. 305"): "1601440",
    ("ID", "Moscow School District No. 281"): "1602220",
    ("ID", "Post Falls School District No. 273"): "1602670",
    ("ID", "Twin Falls School District No. 411"): "1603240",
    ("ID", "Idaho Falls School District No. 91"): "1601530",
    ("ID", "Butte County Joint School District No. 111"): "1600490",
    ("CO", "Eagle County School District RE50J"): "0803540",
    ("CO", "Archuleta County School District 50JT"): "0802190",
    ("CO", "Holyoke School District RE-1J"): "0804710",
    ("CO", "Telluride School District R-1"): "0806870",
    ("AZ", "Prescott Unified School District No. 1"): "0406730",
    ("AZ", "Vail Unified School District No. 20"): "0408850",
    ("AZ", "Amphitheater School District No. 10"): "0400680",
    ("AZ", "Sahuarita Unified School District No. 30"): "0407300",
    ("AZ", "Tanque Verde Unified School District No. 13"): "0408280",
    ("AZ", "Wickenburg Unified School District No. 9"): "0409190",
    ("AZ", "Humboldt Unified School District No. 22"): "0403870",
    ("AZ", "Camp Verde Unified School District No. 28"): "0400813",
    ("AZ", "Cottonwood-Oak Creek Elementary School District No. 6"): "0402370",
    ("AZ", "Bisbee Unified School District No. 2"): "0401180",
    ("AZ", "Palominas Elementary School District No. 49"): "0405880",
    ("AZ", "St. David Unified School District No. 21"): "0408020",
    ("AZ", "Flagstaff Unified School District No. 1"): "0402860",
    ("AZ", "Williams Unified School District No. 2"): "0409310",
    ("AZ", "Payson Unified School District No. 10"): "0406070",
    ("AZ", "Safford Unified School District No. 1"): "0407240",
    ("AZ", "Blue Ridge Unified School District No. 32"): "0406580",
    ("AZ", "Agua Fria Union High School District No. 216"): "0400450",
    ("AZ", "Alhambra Elementary School District No. 68"): "0400600",
    ("AZ", "Arlington Elementary School District No. 47"): "0400840",
    ("AZ", "Balsz Elementary School District No. 31"): "0401050",
    ("AZ", "Cartwright Elementary School District No. 83"): "0401680",
    ("AZ", "Cave Creek Unified School District No. 93"): "0400001",
    ("AZ", "Creighton Elementary School District No. 14"): "0402430",
    ("AZ", "Dysart Unified School District No. 89"): "0402690",
    ("AZ", "Fountain Hills Unified School District No. 98"): "0403040",
    ("AZ", "Gilbert Unified School District No. 41"): "0403400",
    ("AZ", "Glendale Union High School District No. 205"): "0403450",
    ("AZ", "Isaac Elementary School District No. 5"): "0403960",
    ("AZ", "Litchfield Elementary School District No. 79"): "0404380",
    ("AZ", "Littleton Elementary School District No. 65"): "0404440",
    ("AZ", "Mesa Unified School District No. 4"): "0404970",
    ("AZ", "Osborn Elementary School District No. 8"): "0405670",
    ("AZ", "Paradise Valley Unified School District No. 69"): "0405930",
    ("AZ", "Peoria Unified School District No. 11"): "0406250",
    ("AZ", "Phoenix Elementary School District No. 1"): "0406300",
    ("AZ", "Riverside Elementary School District No. 2"): "0407020",
    ("AZ", "Saddle Mountain Unified School District No. 90"): "0407170",
    ("AZ", "Scottsdale Unified School District No. 48"): "0407570",
    ("AZ", "Tolleson Union High School District No. 214"): "0408520",
    ("AZ", "Union Elementary School District No. 62"): "0408820",
    ("AZ", "Washington Elementary School District No. 6"): "0409060",
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
    "04019-insert": (
        "AZ",
        "https://www.recorder.pima.gov/",
        "N3M-web/az2/pima_a.pdf",
    ),
    "04015-insert": (
        "AZ",
        "https://www.mohave.gov/",
        "N3M-web/az2/mohave_full.pdf",
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
    "48339": (
        "TX",
        "https://elections.mctx.org/samplebal.asp?ElectionId=GEN110326&curLang=English",
        "../y26/tx/montgomery_sample.pdf",
    ),
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
    "06061": (
        "CA",
        "https://www.placercountyelections.gov/Uploads/documents/11032026/11032026_Measures_Appear_on_Ballot.pdf",
        "N3M-web/ca625/placer_measures.pdf",
    ),
    "06079": (
        "CA",
        "https://slocounty.ca.gov/departments/clerk-recorder/all-services/elections-and-voting/elections-conducted-by-county-of-san-luis-obispo/november-3,-2026,-general-election",
        "N3M-web/ca625/slo_nov3.html",
    ),
    "06097": (
        "CA",
        "https://sonomacounty.gov/administrative-support-and-fiscal-services/registrar-of-voters/elections/november-3-2026-general-election-local-measures-that-were-filed",
        "MF-541/sonoma/SoCo_Nov2026GenElec_9311_MeasAB_SoCoJrCollDist_Bond_EN.pdf",
    ),
    "06041": (
        "CA",
        "https://www.marincounty.gov/departments/elections/november-3-2026-general-election/information-about-measures-110326/list-measures-110326",
        "MF-541/marin/marin-2026-11-03-measures.html",
    ),
    "06053": (
        "CA",
        "https://www.countyofmonterey.gov/government/departments-i-z/elections",
        "MF-541/monterey/monterey-2026-11-03-list-of-local-measures.html",
    ),
    "06029": ("CA", "https://www.kernvote.com/", "MF-541/kern/Kern County Measure Letter Assignments.pdf"),
    "06107": ("CA", "https://tularecounty.ca.gov/elections/", "MF-541/tulare/tulare-2026-11-03-measures.html"),
    "06077": ("CA", "https://www.sjcrov.org/", "MF-541/san-joaquin/sj-composite-eng-(1).pdf"),
    "06095": (
        "CA",
        "https://www.solanocounty.gov/government/registrar-voters",
        "MF-541/solano/Notice_of_Measures.pdf",
    ),
    "06083": ("CA", "https://sbcvote.com/", "MF-541/santa-barbara/bt000040.pdf"),
    "06099": (
        "CA",
        "https://www.stanvote.gov/pdf/public_notices/20261103_notice_of_election_publication-measure-k-modesto.pdf",
        "MF-541/stanislaus/20261103_notice_of_election_publication-measure-k-modesto.pdf",
    ),
    "44001": (
        "RI",
        "https://www.barrington.ri.gov/DocumentCenter/View/2978/BOC-Booklet-Questions-Info-revised-10-6",
        "RI-web/barrington-boc-booklet.pdf",
    ),
    "44003-cv": ("RI", "https://vote.sos.ri.gov/Home/PollingPlaces?ActiveFlag=2", "MF-542/0601.pdf"),
    "44005": (
        "RI",
        "https://portsmouthri.gov/DocumentCenter/View/7981/Portsmouth-Local-Ballot-Question-Explanation-2026",
        "RI-web/portsmouth-local-questions.pdf",
    ),
    "44005-lc": (
        "RI",
        "https://www.littlecomptonri.org/news_detail_T28_R81.php",
        "RI-web/littlecompton-questions.html",
    ),
    "44005-jt": ("RI", "https://vote.sos.ri.gov/Home/PollingPlaces?ActiveFlag=2", "MF-542/1501.pdf"),
    "44007": (
        "RI",
        "https://www.providenceri.gov/wp-content/uploads/2026/09/November-2026-Local-Question-Explanantions.pdf",
        "RI-web/providence-local-questions.pdf",
    ),
    "44007-cr": (
        "RI",
        "https://cranstonri.gov/_resources/common/userfiles/file/Board%20of%20Canvassers/2026%20Election%20Items/Cranston%202026%20Voter%20Guide.pdf",
        "RI-web/cranston-voter-guide.pdf",
    ),
    "44007-cf": ("RI", "https://vote.sos.ri.gov/Home/PollingPlaces?ActiveFlag=2", "MF-542/0401.pdf"),
    "44007-pw": (
        "RI",
        "https://pawtucketri.gov/wp-content/uploads/2026/10/2026-Bond-Booklet-for-webpage-1.pdf",
        "RI-web/pawtucket-bond-booklet.pdf",
    ),
    "44007-sm": (
        "RI",
        "https://www.smithfieldri.gov/",
        "MF-481/smithfield/Smithfield State and Local Questions.pdf",
    ),
    "44007-jo": ("RI", "https://vote.sos.ri.gov/Home/PollingPlaces?ActiveFlag=2", "MF-542/1601.pdf"),
    "44007-sc": ("RI", "https://vote.sos.ri.gov/Home/PollingPlaces?ActiveFlag=2", "MF-542/3001.pdf"),
    "44007-fo": (
        "RI",
        "https://risos-legacy-files-public.s3.amazonaws.com/documents/elections/ballots/2026/11/03/1201.pdf",
        "MF-542/1201.pdf",
    ),
    "44007-gl": (
        "RI",
        "https://risos-legacy-files-public.s3.amazonaws.com/documents/elections/ballots/2026/11/03/1301.pdf",
        "MF-542/1301.pdf",
    ),
    "44009-na": (
        "RI",
        "https://risos-legacy-files-public.s3.amazonaws.com/documents/elections/ballots/2026/11/03/2001.pdf",
        "MF-542/2001.pdf",
    ),
    "44007-ns": (
        "RI",
        "https://risos-legacy-files-public.s3.amazonaws.com/documents/elections/ballots/2026/11/03/2501.pdf",
        "MF-542/2501.pdf",
    ),
    "44009-sk": (
        "RI",
        "https://risos-legacy-files-public.s3.amazonaws.com/documents/elections/ballots/2026/11/03/3201.pdf",
        "MF-542/3201.pdf",
    ),
    "44005-tv": (
        "RI",
        "https://risos-legacy-files-public.s3.amazonaws.com/documents/elections/ballots/2026/11/03/3301.pdf",
        "MF-542/3301.pdf",
    ),
    "44009": (
        "RI",
        "https://www.richmondri.gov/DocumentCenter/View/4674/Chariho-Public-Notice-1022026",
        "RI-web/chariho-public-notice.pdf",
    ),
    "44009-ch": (
        "RI",
        "https://www.charlestownri.gov/",
        "MF-480/charlestown/October_2026_Pipeline_-6(1).pdf",
    ),
    "44009-hk": (
        "RI",
        "https://www.thewesterlysun.com/classifieds/community/announcements/legal/town-of-hopkinton-local-referendum-question/ad_0d68755c-52ac-5597-b590-c66345354519.html",
        "RI-web/hopkinton-local-question-7.html",
    ),
    "44009-we": ("RI", "https://vote.sos.ri.gov/Home/PollingPlaces?ActiveFlag=2", "MF-542/3601.pdf"),
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
    "35013": (
        "NM",
        "https://www.lascruces.gov/go-bond",
        "N3M-web/nm/lascruces-go-bond.html",
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
    "48113-up": (
        "TX",
        "https://www.dallascountyvotes.org/#university-park",
        "MF-463/dallas/CoUP-Notice-of-Special-Election-November-3-2026.pdf",
    ),
    "48303-lc": (
        "TX",
        "https://votelubbock.gov/elections-office/news-and-legal-notices/#lubbock-cooper-isd",
        "MF-463/lubbock/LCISD-2026-VATRE-Notice-of-Election.pdf",
    ),
    "48303-lz": (
        "TX",
        "https://votelubbock.gov/elections-office/news-and-legal-notices/#lorenzo-isd-bonds",
        "MF-463/lubbock/Notice-of-Election-Lorenzo-County-ISD-November-2026-Bond-Election-and-MTN-Refund.pdf",
    ),
    "48303-lzv": (
        "TX",
        "https://votelubbock.gov/elections-office/news-and-legal-notices/#lorenzo-isd-vatre",
        "MF-463/lubbock/Notice-of-Election-Lorenzo-County-ISD-November-2026-VATRE.pdf",
    ),
    "48039-swy": (
        "TX",
        "https://www.brazoriacountyclerktx.gov/departments/elections/public-notices#sweeny-isd",
        "MF-463/brazoria/Sweeny ISD Notice of Special Election.pdf",
    ),
    "48039-fpt": (
        "TX",
        "https://www.brazoriacountyclerktx.gov/departments/elections/public-notices#freeport",
        "MF-463/brazoria/City of Freeport  Notice of Recall Election.pdf",
    ),
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
    "04003-ins": (
        "AZ",
        "https://www.cochise.az.gov/elections",
        "MF-503/cochise/RB261305 Cochise Full Text 11-26 17x16.5_PROOF_260831_v03.pdf",
    ),
    "04003-city": (
        "AZ",
        "https://www.bisbeeaz.gov/",
        "MF-503/cochise/City of Bisbee FINAL PRINT FILE 2026 General Election 110326.pdf",
    ),
    "04005-flag": (
        "AZ",
        "https://www.flagstaff.az.gov/",
        "MF-503/coconino/City of Flagstaff Informational Pamphlet - 2026 General Election.pdf",
    ),
    "04005-page": (
        "AZ",
        "https://www.cityofpage.org/",
        "MF-503/coconino/City of Page Informational Pamphlet - 2026 General Election.pdf",
    ),
    "04005-fusd": (
        "AZ",
        "https://www.fusd1.org/",
        "MF-503/coconino/Flagstaff USD Informational Pamphlet - 2026 General Election.pdf",
    ),
    "04005-wusd": (
        "AZ",
        "https://www.williams.k12.az.us/",
        "MF-503/coconino/Williams USD Informational Pamphlet - 2026 General Election.pdf",
    ),
    "04007-ins": (
        "AZ",
        "https://www.gilacountyaz.gov/government/elections/",
        "MF-503/gila/2026 Tablecloth.pdf",
    ),
    "04009-ins": (
        "AZ",
        "https://www.graham.az.gov/elections",
        "MF-503/graham/2026 General Tablecloth_202609161326100840.pdf",
    ),
    "04017-ins": (
        "AZ",
        "https://www.navajocountyaz.gov/Departments/Elections",
        "MF-503/navajo/264314a_tablecloth_HR_prf.pdf",
    ),
    "04023-ins": (
        "AZ",
        "https://www.santacruzcountyaz.gov/",
        "MF-503/santa-cruz/264533a_Tablecloth_HR_prf_202609161650400208.pdf",
    ),
    "04013-pam": (
        "AZ",
        "https://elections.maricopa.gov/",
        "MF-503/maricopa/2026 General Election Publicity Pamphlet - Maricopa County - Website Version.pdf",
    ),
    "04013-toll": (
        "AZ",
        "https://www.tollesonaz.gov/",
        "MF-503/maricopa/City of Tolleson Publicity Pamphlet 2026 General Election.pdf",
    ),
    "04013-aguafriaunionh": (
        "AZ",
        "https://schoolsup.org/agua-fria-2026-ballot",
        "N3M-web/az-schoolsup/AguaFriaUSD_2026.pdf",
    ),
    "04013-alhambraelemen": (
        "AZ",
        "https://schoolsup.org/alhambra-2026-ballot",
        "N3M-web/az-schoolsup/AlhambraESD2026.pdf",
    ),
    "04013-arlingtoneleme": (
        "AZ",
        "https://schoolsup.org/arlington-2026-ballot",
        "N3M-web/az-schoolsup/ArlingtonESD2026.pdf",
    ),
    "04013-balszelementar": (
        "AZ",
        "https://schoolsup.org/balsz-2026-ballot",
        "N3M-web/az-schoolsup/BalszESD2026.pdf",
    ),
    "04013-cartwrightelem": (
        "AZ",
        "https://schoolsup.org/cartwright-2026-ballot",
        "N3M-web/az-schoolsup/CartwrightESD2026.pdf",
    ),
    "04013-cavecreekunifi": (
        "AZ",
        "https://schoolsup.org/cave-creek-2026-ballot",
        "N3M-web/az-schoolsup/CaveCreekUSD2026.pdf",
    ),
    "04013-creightoneleme": (
        "AZ",
        "https://schoolsup.org/creighton-2026-ballot",
        "N3M-web/az-schoolsup/CreightonESD2026.pdf",
    ),
    "04013-dysartunified": (
        "AZ",
        "https://schoolsup.org/dysart-2026-ballot",
        "N3M-web/az-schoolsup/DysartUSD2026.pdf",
    ),
    "04013-fountainhillsu": (
        "AZ",
        "https://schoolsup.org/fountain-hills-2026-ballot",
        "N3M-web/az-schoolsup/FountainHillsUSD2026.pdf",
    ),
    "04013-gilbertunified": (
        "AZ",
        "https://schoolsup.org/gilbert-2026-ballot",
        "N3M-web/az-schoolsup/GilbertUSD2026.pdf",
    ),
    "04013-glendaleunionh": (
        "AZ",
        "https://schoolsup.org/glendale-2026-ballot",
        "N3M-web/az-schoolsup/GlendaleUnionHSD2026.pdf",
    ),
    "04013-isaacelementar": (
        "AZ",
        "https://schoolsup.org/isaac-2026-ballot",
        "N3M-web/az-schoolsup/IsaacESD2026.pdf",
    ),
    "04013-litchfieldelem": (
        "AZ",
        "https://schoolsup.org/litchfield-2026-ballot",
        "N3M-web/az-schoolsup/LitchfieldESD2026.pdf",
    ),
    "04013-littletoneleme": (
        "AZ",
        "https://schoolsup.org/littleton-2026-ballot",
        "N3M-web/az-schoolsup/LittletonESD2026.pdf",
    ),
    "04013-mesaunified": (
        "AZ",
        "https://schoolsup.org/mesa-2026-ballot",
        "N3M-web/az-schoolsup/MesaUSD2026.pdf",
    ),
    "04013-osbornelementa": (
        "AZ",
        "https://schoolsup.org/osborn-2026-ballot",
        "N3M-web/az-schoolsup/OsbornESD2026.pdf",
    ),
    "04013-paradisevalley": (
        "AZ",
        "https://schoolsup.org/paradise-valley-2026-ballot",
        "N3M-web/az-schoolsup/ParadiseValleyUSD2026.pdf",
    ),
    "04013-peoriaunified": (
        "AZ",
        "https://schoolsup.org/peoria-2026-ballot",
        "N3M-web/az-schoolsup/PeoriaUSD2026.pdf",
    ),
    "04013-phoenixelement": (
        "AZ",
        "https://schoolsup.org/phoenix-elementary-2026-ballot",
        "N3M-web/az-schoolsup/PhoenixESD2026.pdf",
    ),
    "04013-riversideeleme": (
        "AZ",
        "https://schoolsup.org/riverside-2026-ballot",
        "N3M-web/az-schoolsup/RiversideESD2026.pdf",
    ),
    "04013-saddlemountain": (
        "AZ",
        "https://schoolsup.org/saddle-mountain-2026-ballot",
        "N3M-web/az-schoolsup/SaddleMountainUSD2026.pdf",
    ),
    "04013-scottsdaleunif": (
        "AZ",
        "https://schoolsup.org/scottsdale-2026-ballot",
        "N3M-web/az-schoolsup/ScottsdaleUSD2026.pdf",
    ),
    "04013-tollesonunionh": (
        "AZ",
        "https://schoolsup.org/tolleson-2026-ballot",
        "N3M-web/az-schoolsup/TollesonUnionHSD2026.pdf",
    ),
    "04013-unionelementar": (
        "AZ",
        "https://schoolsup.org/union-2026-ballot",
        "N3M-web/az-schoolsup/UnionESD2026.pdf",
    ),
    "04013-washingtonelem": (
        "AZ",
        "https://schoolsup.org/washington-2026-ballot",
        "N3M-web/az-schoolsup/WashingtonESD2026.pdf",
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
    "48439-frh15": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_orders/FRH_Election_Orders.zip#ord-2026-15",
        "N3M-web/tx7/tarrant/frh_pdf/ord15.pdf",
    ),
    "48439-frh16": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_orders/FRH_Election_Orders.zip#ord-2026-16",
        "N3M-web/tx7/tarrant/frh_pdf/ord16.pdf",
    ),
    "48439-tfw3": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_notices/Notice_of_Elction_TFW003.pdf",
        "N3M-web/tx7/tarrant/Notice_of_Elction_TFW003.pdf",
    ),
    "48439-ptc": (
        "TX",
        "https://www.tarrantcountytx.gov/content/dam/main/elections/2026/1126/notices/entity_notices/Notice_of%20Election_P-TC_MUD_1.pdf",
        "N3M-web/tx7/tarrant/Notice_of_Election_P-TC_MUD_1.pdf",
    ),
    "48061-bro": (
        "TX",
        "https://www.cameroncountytx.gov/elections/wp-content/uploads/2026/10/NOTICE-OF-ELECTION.pdf",
        "N3M-web/tx8/f15.pdf",
    ),
    "48097-gai": (
        "TX",
        "https://www.gainesville.tx.us/DocumentCenter/View/12791/1623-08-2026---Order-of-November-2026-Charter-Election",
        "N3M-web/tx8/f27.pdf",
    ),
    "48027-kil": (
        "TX",
        "https://www.killeentexas.gov/DocumentCenter/View/14507/1-16f-NOTICE-OF-1132026-Special-Election-PDF",
        "N3M-web/tx8/f38.pdf",
    ),
    "48257-for": (
        "TX",
        "https://www.forneytx.gov/DocumentCenter/View/10470/Notice-of-Bond-Election---November-3-2026---English-signed",
        "N3M-web/tx8/f36.pdf",
    ),
    "48139-mid": (
        "TX",
        "https://www.midlothian.tx.us/1097/November-3-2026---Special-Election",
        "N3M-web/tx8/f33.html",
    ),
    "48041-bry": (
        "TX",
        "https://media-002-us.cdn.govstack.com/bryantx-004-us/media/4gmbxkvi/notice-of-special-election-2026-signed.pdf",
        "N3M-web/tx8/f03.pdf",
    ),
    "48147-leo": (
        "TX",
        "https://www.co.fannin.tx.us/upload/page/7064/City%20of%20Leonard%20-%20notice%20-%20English.pdf",
        "N3M-web/tx8/f19.pdf",
    ),
    "48177-ns": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/1033/Nixon-Smiley%20CISD%20November%202026%20-%20Notice%20of%20Election.pdf",
        "N3M-web/tx8/f06.pdf",
    ),
    "48493-poth": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/2254/docs/Elections/11.03.2026%20General/Poth%20ISD%20Notice%20of%20Election%20%20English.pdf",
        "N3M-web/tx8/f08.pdf",
    ),
    "48493-stk": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/2254/docs/Elections/11.03.2026%20General/Stockdale%20ISD%20order.pdf",
        "N3M-web/tx8/f09.pdf",
    ),
    "48255-fc": (
        "TX",
        "https://www.co.karnes.tx.us/upload/page/1033/2026%20Falls%20City%20ISD%20Notice%20of%20Election.pdf",
        "N3M-web/tx8/f20.pdf",
    ),
    "48289-leon": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/4964/11032026/Leon%20ISD%20-%20Bond%20Election%20Order%20Nov%202026%20English.pdf",
        "N3M-web/tx8/f12.pdf",
    ),
    "48089-wei": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/6210/26-11-3/Weimar%20ISD%20November%202026%20Bond%20Election%20Order.pdf",
        "N3M-web/tx8/f13.pdf",
    ),
    "48503-gra": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/2592/Graham%20ISD%20November%202026%20Bond%20Election%20%20Election%20Order.pdf",
        "N3M-web/tx8/f10.pdf",
    ),
    "48097-vv": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/3107/docs/2026/November3/ValleyViewISDNoticeofElection26.pdf",
        "N3M-web/tx8/f11.pdf",
    ),
    "48437-tul": (
        "TX",
        "https://www.co.swisher.tx.us/upload/page/7290/2026/Tulia%20ISD%202026%20VATRE%20Notice%20of%20Election%20ENGLISH.pdf",
        "N3M-web/tx8/f22.pdf",
    ),
    "48065-gro": (
        "TX",
        "https://www.co.carson.tx.us/upload/page/1423/2026/2026%20Nov/11032026%20notice%20of%20gisd%20tax%20rate%20election.pdf",
        "N3M-web/tx8/f17.pdf",
    ),
    "48161-fhd": (
        "TX",
        "https://www.co.navarro.tx.us/upload/page/0134/docs/Elections/2026/Nov3/Fairfield%20Hospital%20District%20Notice%20of%20Election.pdf",
        "N3M-web/tx8/f21.pdf",
    ),
    "48497-wesd3": (
        "TX",
        "https://www.co.wise.tx.us/DocumentCenter/View/7959/Notice-of-Special-Election---ESD-No-3",
        "N3M-web/tx8/f26.pdf",
    ),
    "48355-nesd1": (
        "TX",
        "https://www.annavillefire.com/files/3f50f7cfd/Notice+of+Election_Publication_all+language.pdf",
        "N3M-web/tx8/f14.pdf",
    ),
    "48467-vzesd2": (
        "TX",
        "https://www.vanzandtcounty.org/upload/page/12440/2026/vzcesd_2_4003_notice_of_election_publication_all_language_002.pdf",
        "N3M-web/tx8/f28.pdf",
    ),
    "48483-wesd1": (
        "TX",
        "https://www.wheelercounty.texas.gov/upload/page/0104/2026/2026-11-03/ESD/ESD-Order.pdf",
        "N3M-web/tx8/f29.pdf",
    ),
    "48457-tesd3": (
        "TX",
        "https://img1.wsimg.com/blobby/go/3a994b69-8076-4945-bf02-ff38eaf01bc8/downloads/b268676d-2fd1-47ef-b66a-f75419ac4cbe/ESD-3%20Notice%20of%20Election.pdf",
        "N3M-web/tx8/f05.pdf",
    ),
    "48477-wash": (
        "TX",
        "https://newtools.cira.state.tx.us/upload/page/10698/2026/2026%2011%2003/Notice%20of%20Bond%20Election%20Washington%20Co%20Nov%202026.pdf",
        "N3M-web/tx8/f07.pdf",
    ),
    "48481-wcid2": (
        "TX",
        "https://www.co.wharton.tx.us/upload/page/7008/Wharton%20WCID%20No.%202%20Notice%20of%20Election%202026.pdf",
        "N3M-web/tx8/f25.pdf",
    ),
    "48055-chm": (
        "TX",
        "https://elections.caldwellcounty.texas.gov/files/elections-site/public-documents/2026-nov-3-general/Chisholm%20MUD%20No%201_Notice%20of%20Election.pdf",
        "N3M-web/tx8/f04.pdf",
    ),
    "48473-w69": (
        "TX",
        "https://www.co.waller.tx.us/upload/page/0286/WAL069%20Notice%20of%20Elec%20FINAL%20VERSION.pdf",
        "N3M-web/tx8/f24.pdf",
    ),
    "48473-w70": (
        "TX",
        "https://www.co.waller.tx.us/upload/page/0286/Notice%20of%20Confirmation%20Directors%20Bonds%20and%20Operation%20and%20Maintenance%20Tax%20Elections%20November%202026.pdf",
        "N3M-web/tx8/f23.pdf",
    ),
    "48257-ter": (
        "TX",
        "https://www.cityofterrell.org/DocumentCenter/View/7477/Ordinance-No-3187",
        "N3M-web/tx9/terrell.pdf",
    ),
    "48041-bisd": (
        "TX",
        "https://elections.brazoscountytx.gov/wp-content/uploads/2026/09/BRYAN-ISD-NOV-2026-Notice-of-Voter-Approval-Tax-Rate-Election-accessible.pdf",
        "N3M-web/tx9/BRYAN.pdf",
    ),
    "48041-nisd": (
        "TX",
        "https://elections.brazoscountytx.gov/wp-content/uploads/2026/09/Amended-Navasota-ISD-November-2026-VATRE-Notice-of-Election-English-and-Spanish-9-21-accessible.pdf",
        "N3M-web/tx9/Navasota.pdf",
    ),
    "48479-lar": (
        "TX",
        "https://www.webbcountytx.gov/DocumentCenter/View/11708/413-02",
        "N3M-web/tx9/w/11708.pdf",
    ),
    "48231-esd1": (
        "TX",
        "https://www.huntcounty.net/upload/page/0142/2026/Order%20of%20Election%20for%20Emergency%20Services%20District%20Number%201.pdf",
        "N3M-web/tx9/hu/esd1.pdf",
    ),
    "48231-esd2": (
        "TX",
        "https://www.huntcounty.net/upload/page/0142/2026/Order%20of%20Election%20for%20Emergency%20Services%20District%20Number%202.pdf",
        "N3M-web/tx9/hu/esd2.pdf",
    ),
    "48231-ccemud": (
        "TX",
        "https://www.huntcounty.net/upload/page/0142/docs/CCEMUD_NoticeofElection.pdf",
        "N3M-web/tx9/hu/ccemud_n.pdf",
    ),
    "48257-poe": (
        "TX",
        "https://www.poetrytexas.org/_files/ugd/95337b_80b2f34315834fcdb0c31814a1bba50d.pdf",
        "N3M-web/tx9/hu/poetry.pdf",
    ),
    "48309-rob": (
        "TX",
        "https://www.co.falls.tx.us/upload/page/0087/ROBINSON%20ISD%20NOV%202026%20-%20Notice%20of%20Bond%20Election.pdf",
        "N3M-web/tx9/mc/robinson.pdf",
    ),
    "48309-wlv": (
        "TX",
        "https://www.mclennan.gov/1350/November-3-2026-Joint-General-Election",
        "MF-478/WLVMMD Notice of Confirmation, Bond, Directors, and Maintenance Tax Election - November 2026 (18997).pdf",
    ),
    "48309-bel": (
        "TX",
        "https://bellmeadtx.gov/540/November-2026-Sales-Tax-Election",
        "MF-473/Agenda  packet - 07.14.2026_Redacted.pdf",
    ),
    "48215": (
        "TX",
        "https://www.hidalgocounty.us/DocumentCenter/View/72989",
        "N3M-web/tx9/hid/72989.pdf",
    ),
    "48061-sbm": (
        "TX",
        "https://www.cameroncountytx.gov/elections/wp-content/uploads/2026/09/San-Benito-Municipal-Utility-District-No.-1-Confirmation-and-Directors-Election-09182026.pdf",
        "N3M-web/tx9/cam/sb2.pdf",
    ),
    "48029-sid": (
        "TX",
        "https://home.bexar.org/generalsampleballot-specialimprovementdistricts.pdf",
        "N3M-web/tx7/bexar/sid.pdf",
    ),
    "48029-sa": (
        "TX",
        "https://home.bexar.org/electionsresources/ballots/Sample_1.pdf#city-of-san-antonio",
        "N3M-web/tx7/bexar/styles/S1.pdf",
    ),
    "48029-ec": (
        "TX",
        "https://home.bexar.org/electionsresources/ballots/Sample_129.pdf",
        "N3M-web/tx7/bexar/styles/S129.pdf",
    ),
    "48029-saisd": (
        "TX",
        "https://home.bexar.org/electionsresources/ballots/Sample_1.pdf",
        "N3M-web/tx7/bexar/styles/S1.pdf",
    ),
    "42101": (
        "PA",
        "https://vote.phila.gov/media/2026G_Ballot_Questions.pdf",
        "N3M-web/pa/phila-ballot-questions.pdf",
    ),
    "32003": (
        "NV",
        "https://www.bcnv.org/ballotquestions",
        "N3M-web/nv/bouldercity-ballot-questions.html",
    ),
    "16031": (
        "ID",
        "https://www.cassia.gov/media/Elections/Candidate%20Filing/Candidate%20Filings%20Archive/Ballot%20Measures/2026_11_03/American%20Falls%20Joint%20School%20District%20No%20381%20Supplemental%20Levy.pdf",
        "N3M-web/id/cassia-americanfalls-381.pdf",
    ),
    "16083-tf411": (
        "ID",
        "https://twinfallscounty.org/wp-content/uploads/Twin-Falls-School-2.pdf",
        "N3M-web/id/twinfalls-411-sample-ballot.pdf",
    ),
    "16055-pf273": (
        "ID",
        "https://resources.finalsite.net/images/v1783609718/pfsdcom/c2ul9eaf3ungduoyoymk/Nov2026sampleballot.pdf",
        "N3M-web/id/postfalls-273-sample-ballot.pdf",
    ),
    "16019-if91": (
        "ID",
        "https://results.voteidaho.gov/results/public/bonneville-county-id/elections/nov2026/ballot-items/01000000-c793-678f-a861-08df2aee377f",
        "N3M-web/id/voteidaho-idahofalls-91.html",
    ),
    "16023-jsd111": (
        "ID",
        "https://results.voteidaho.gov/results/public/butte-county-id/elections/nov2026/ballot-items/01000000-ed5e-4374-a252-08df2631c215",
        "N3M-web/id/voteidaho-butte-111.html",
    ),
    "08037-re50j": (
        "CO",
        "https://www.garfieldcountyco.gov/wp-content/uploads/sites/6/2026-Eagle-County-School-District-RE50J-TABOR-Notice.pdf",
        "N3M-web/co/garfield-eagle-re50j-tabor.pdf",
    ),
    "08007-sb": (
        "CO",
        "https://www.archuletacounty.gov/wp-content/uploads/2026-Archuleta-County-General-Election-Sample-Ballot.pdf",
        "N3M-web/co/archuleta-sample-ballot.pdf",
    ),
    "08095-re1j": (
        "CO",
        "https://phillipscounty.colorado.gov/sites/g/files/lrnvjt1351/files/November-2026-Sample-Ballot-RE-1-J-and-City-of-Holyoke.pdf",
        "MF-584/November-2026-Sample-Ballot-RE-1-J-and-City-of-Holyoke.pdf",
    ),
    "08113-r1": (
        "CO",
        "https://www.sanmiguelcountyco.gov/DocumentCenter/View/14796/Sample-Ballot-for-the-2026-General-Election-R-1-School-District",
        "N3M-web/co/sanmiguel-telluride-r1-sample-ballot.pdf",
    ),
    "16083": (
        "ID",
        "https://www.cassia.gov/media/Elections/Candidate%20Filing/Candidate%20Filings%20Archive/Ballot%20Measures/2026_11_03/Rock%20Creek%20Rural%20Fire%20Protection%20Levy.pdf",
        "N3M-web/id/cassia-rockcreek-fire.pdf",
    ),
    "48027-tem": (
        "TX",
        "https://www.bellcountytx.com/county_government/elections/#48027-tem",
        "MF-501/Bell/City of Temple - Resolution calling bond election.pdf",
    ),
    "48187-mud17": (
        "TX",
        "https://www.co.guadalupe.tx.us/page/elections#48187-mud17",
        "MF-501/Guadalupe/1 - Notice of Election_Guadalupe MUD 17 ENGSPA rev 9-15-26.pdf",
    ),
    "48187-mud16": (
        "TX",
        "https://www.co.guadalupe.tx.us/page/elections#48187-mud16",
        "MF-501/Guadalupe/GC 16.pdf",
    ),
    "48187-ltr": (
        "TX",
        "https://www.co.guadalupe.tx.us/page/elections#48187-ltr",
        "MF-501/Guadalupe/Liberty Trails MUD.pdf",
    ),
    "48187-brd": (
        "TX",
        "https://www.co.guadalupe.tx.us/page/elections#48187-brd",
        "MF-501/Guadalupe/Notice of Election - English and Spanish - Bolton Road MUD.pdf",
    ),
    "48187-seg": (
        "TX",
        "https://www.co.guadalupe.tx.us/page/elections#48187-seg",
        "MF-501/Guadalupe/seguin isd.pdf",
    ),
    "48291-lib": (
        "TX",
        "https://www.libertycountytx.gov/page/elections#48291-lib",
        "MF-501/Liberty/City of Liberty Notice of Special Election 11-3-2026.pdf",
    ),
    "48291-hul": (
        "TX",
        "https://www.libertycountytx.gov/page/elections#48291-hul",
        "MF-501/Liberty/2026.08.27 Hull Fresh Water Supply District Notice of Election.pdf",
    ),
    "48291-bb": (
        "TX",
        "https://www.libertycountytx.gov/page/elections#48291-bb",
        "MF-501/Liberty/Liberty County Sample Ballots.pdf",
    ),
    "48291-lg1": (
        "TX",
        "https://www.libertycountytx.gov/page/elections#48291-lg1",
        "MF-501/Liberty/Liberty Grand MUD 1 - Notice of Election.pdf",
    ),
    "48291-lg1a": (
        "TX",
        "https://www.libertycountytx.gov/page/elections#48291-lg1a",
        "MF-501/Liberty/Liberty Grand MUD 1A - Notice of Election.pdf",
    ),
    "48091-wid3b": (
        "TX",
        "https://www.co.comal.tx.us/Elections.htm#48091-wid3b",
        "MF-501/Comal/Comal County WID No. 3B Notice of Confirmation, Directors, Bonds, Operation and Maintenance.pdf",
    ),
    "48397-fate": (
        "TX",
        "https://www.rockwallcountytexas.com/193/Elections#48397-fate",
        "MF-501/Rockwall/General-Election-Comprehensive-Sample-Ballot-11.03.2026.pdf",
    ),
    "48367-wp": (
        "TX",
        "https://www.parkercountytx.com/166/Elections#48367-wp",
        "MF-501/Parker/BS 18.pdf",
    ),
    "48441-abi": (
        "TX",
        "https://www.taylorcountytexas.org/elections#48441-abi",
        "MF-501/Taylor/Style 3.pdf",
    ),
    "48245-ned": (
        "TX",
        "https://www.co.jefferson.tx.us/elections/#48245-ned",
        "MF-501/Jefferson/Nov-2026-Sample-Ballot.pdf",
    ),
    "48251-cle": (
        "TX",
        "https://www.johnsoncountytx.org/departments/elections#48251-cle",
        "MF-501/Johnson/Comm 4  PCT 21 CLE.pdf",
    ),
    "48021": (
        "TX",
        "https://www.co.bastrop.tx.us/page/bastrop.elections#48021",
        "MF-501/Bastrop/Complete_with_Docusign_Order_a_Special_Elect.pdf",
    ),
    "48021-bas": (
        "TX",
        "https://www.co.bastrop.tx.us/page/bastrop.elections#48021-bas",
        "MF-501/Bastrop/Executed-Order-of-Election_City-of-Bastrop.pdf",
    ),
    "48021-smi": (
        "TX",
        "https://www.co.bastrop.tx.us/page/bastrop.elections#48021-smi",
        "MF-501/Bastrop/Signed-Resolution-2026-07-577-Calling-a-Special-Election_COS.pdf",
    ),
    "48021-esd2": (
        "TX",
        "https://www.co.bastrop.tx.us/page/bastrop.elections#48021-esd2",
        "MF-501/Bastrop/Order-Calling-ELection-Regarding-Annexation-Colorado-Bend-LLC-2026-06-16-signed_ESD2.pdf",
    ),
    "48021-bisd": (
        "TX",
        "https://www.co.bastrop.tx.us/page/bastrop.elections#48021-bisd",
        "MF-501/Bastrop/Order-of-Election_BISD.pdf",
    ),
    "48329-mcud": (
        "TX",
        "https://www.co.midland.tx.us/157/Elections#48329-mcud",
        "MF-501/Midland/Generic-Sample-Ballot-for-Midland-County-Voters-1-1.pdf",
    ),
    "48329": (
        "TX",
        "https://www.co.midland.tx.us/157/Elections#48329",
        "MF-501/Midland/Generic-Sample-Ballot-for-Midland-County-Voters-Living-in-City-of-Odessa-1.pdf",
    ),
}


def check_counties() -> None:
    unmapped = sorted({row["county_fips"] for row in json.loads(ROWS_FILE.read_text())} - set(COUNTIES))
    if unmapped:
        raise ValueError(f"nov3_manual_2026.json keys with no COUNTIES entry: {', '.join(unmapped)}")


def fetch() -> None:
    check_counties()
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
    check_counties()
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
    check_counties()
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Local referenda on the November 3, 2026 ballot in Arizona, New Mexico, Arkansas, Georgia, South Carolina, Nebraska, Texas, California, Rhode Island, North Dakota, Washington, Idaho, Arizona, and Colorado, with official wording",
            polity="US",
        )
        civils = {state: civmint.Civil(conn, cs, fips[:2], state) for fips, (state, _, _) in COUNTIES.items()}
        systems = measure_rules.systems(
            cs,
            {
                "referendum",
                "referendum-ca-two-thirds",
                "referendum-ca-55",
                "referendum-wa-60-validated",
                "referendum-id-55",
                "referendum-id-60",
            },
        )
        sources: dict[str, UUID] = {}
        added: set[UUID] = set()
        for county, publisher in (
            frame.select("county", "publisher").unique("county", keep="first", maintain_order=True).iter_rows()
        ):
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
                town = civil.towns.get((row["county"][:5], civmint.town_core(bare)))
                option = town if state == "RI" and town is not None else civil.place(bare) or town
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
                    status="held" if DAY <= date.today() else "scheduled",
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
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            held = civil.slugs.get(contest_path)
            if held is not None:
                current = conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value = %s", [contest_key]
                ).fetchone()
                if current is None or current[0] != held:
                    print("slug taken:", contest_path)
                    continue
            contest = cs.key("us.contest", contest_key)
            civil.slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=systems[measure_rules.local(state, row["entity"], label, row["wording"] or "")],
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
