import csv
import difflib
import gzip
import html
import io
import itertools
import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
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
from ea.sources.us import ak_sovc, fl_sos, gov_counties, ri_countbook
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
CA_1998 = {
    (date(1998, 6, 2), "219"): ("Ballot Measures. Application.", "Legislative Referendum"),
    (date(1998, 6, 2), "220"): ("Courts. Superior and Municipal Court Consolidation.", "Legislative Referendum"),
    (date(1998, 6, 2), "221"): ("Subordinate Judicial Officers. Discipline.", "Legislative Referendum"),
    (date(1998, 6, 2), "222"): ("Murder. Peace Officer Victim. Sentence Credits.", "Legislative Referendum"),
    (date(1998, 6, 2), "223"): ("Schools. Spending Limits on Administration.", "Initiative"),
    (date(1998, 6, 2), "224"): ("State-Funded Design and Engineering Services.", "Initiative"),
    (date(1998, 6, 2), "225"): ("Limiting Congressional Terms. Proposed U.S. Constitutional Amendment.", "Initiative"),
    (date(1998, 6, 2), "226"): ("Political Contributions by Employees, Union Members, Foreign Entities.", "Initiative"),
    (date(1998, 6, 2), "227"): ("English Language in Public Schools.", "Initiative"),
    (date(1998, 11, 3), "1A"): (
        "Class Size Reduction Kindergarten-University Public Education Facilities Bond Act of 1998",
        "Legislative Referendum",
    ),
    (date(1998, 11, 3), "1"): ("Property Taxes: Contaminated Property", "Legislative Referendum"),
    (date(1998, 11, 3), "2"): ("Transportation: Funding", "Legislative Referendum"),
    (date(1998, 11, 3), "3"): ("Partisan Presidential Primary Elections", "Legislative Referendum"),
    (date(1998, 11, 3), "4"): ("Trapping Practices. Bans Use of Specified Traps and Animal Poisons.", "Initiative"),
    (date(1998, 11, 3), "5"): ("Tribal-State Gaming Compacts. Tribal Casinos.", "Initiative"),
    (date(1998, 11, 3), "6"): (
        "Criminal Law. Prohibition on Slaughter of Horses and Sale of Horsemeat for Human Consumption.",
        "Initiative",
    ),
    (date(1998, 11, 3), "7"): ("Air Quality Improvement. Tax Credits.", "Initiative"),
    (date(1998, 11, 3), "8"): (
        "Public Schools. Permanent Class Size Reduction. Parent-Teacher Councils. Teacher Credentialing. Pupil Suspension for Drug Possession. Chief Inspector's Office.",
        "Initiative",
    ),
    (date(1998, 11, 3), "9"): ("Electric Utilities. Assessments. Bonds.", "Initiative"),
    (date(1998, 11, 3), "10"): (
        "State and County Early Childhood Development Programs. Additional Tobacco Surtax.",
        "Initiative",
    ),
    (date(1998, 11, 3), "11"): ("Local Sales and Use Taxes - Revenue Sharing", "Legislative Referendum"),
}

CA_FILES = {
    date(1998, 6, 2): "1998-primary/1998-sov-primary.pdf",
    date(1998, 11, 3): "1998-general/sov1998-general.pdf",
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
    date(2022, 11, 8): (
        "https://web.archive.org/web/20221205195258id_/"
        "https://azsos.gov/sites/default/files/2022Dec05_General_Election_Canvass_Web.pdf"
    ),
}
AZ_2022 = {
    "128": ("Relating to initiative and referendum (state legislature authority)", "Legislative Referendum", False),
    "129": ("Relating to initiative measures", "Legislative Referendum", True),
    "130": ("Relating to property tax exemptions", "Legislative Referendum", True),
    "131": ("Relating to the executive department", "Legislative Referendum", True),
    "132": (
        "Relating to initiative and referendum measures (60% approval for tax proposals)",
        "Legislative Referendum",
        True,
    ),
    "209": ("Relating to predatory debt collection protection", "Initiative", True),
    "211": (
        "Relating to the disclosure of the original source of monies used for campaign media spending",
        "Initiative",
        True,
    ),
    "308": ("Relating to the classification of students for tuition purposes", "Legislative Referendum", True),
    "309": ("Relating to voter identification", "Legislative Referendum", False),
    "310": ("Relating to taxation benefitting fire districts", "Legislative Referendum", False),
}
NY = "https://web.archive.org/web/{}id_/https://www.elections.ny.gov/NYSBOE/elections/{}"
NY_MEASURES = {
    (date(2014, 11, 4), "1"): (
        "Proposal 1",
        "Revising the state's redistricting procedure",
        True,
        "20221110162420",
        "2014/general/2014GeneralElectionProp1.xlsx",
    ),
    (date(2014, 11, 4), "2"): (
        "Proposal 2",
        "Permitting electronic distribution of state legislative bills",
        True,
        "20221110162406",
        "2014/general/2014GeneralElectionProp2.xlsx",
    ),
    (date(2014, 11, 4), "3"): (
        "Proposal 3",
        "The Smart Schools Bond Act of 2014",
        True,
        "20221110162404",
        "2014/general/2014GeneralElectionProp3.xlsx",
    ),
    (date(2017, 11, 7), "1"): (
        "Proposal 1",
        "Constitutional convention",
        False,
        "20210424131033",
        "2017/general/2017GeneralElectionProp1.xls",
    ),
    (date(2017, 11, 7), "2"): (
        "Proposal 2",
        "Allowing the complete or partial forfeiture of a public officer's pension if convicted of a certain type of felony",
        True,
        "20210424131041",
        "2017/general/2017GeneralElectionProp2.xls",
    ),
    (date(2017, 11, 7), "3"): (
        "Proposal 3",
        "Authorizing the use of forest preserve land for specified purposes",
        True,
        "20210424131048",
        "2017/general/2017GeneralElectionProp3.xls",
    ),
    (date(2021, 11, 2), "1"): (
        "Proposition 1",
        "Amending the apportionment and redistricting process",
        False,
        "20230111005827",
        "2021/General/GE2021_BallotProp1.xlsx",
    ),
    (date(2021, 11, 2), "2"): (
        "Proposition 2",
        "Right to clean air, clean water, and a healthful environment",
        True,
        "20230111005839",
        "2021/General/GE2021_BallotProp2.xlsx",
    ),
    (date(2021, 11, 2), "3"): (
        "Proposition 3",
        "Eliminating the ten-day advance voter registration requirement",
        False,
        "20230111005849",
        "2021/General/GE2021_BallotProp3.xlsx",
    ),
    (date(2021, 11, 2), "4"): (
        "Proposition 4",
        "Authorizing no-excuse absentee ballot voting",
        False,
        "20230111005854",
        "2021/General/GE2021_BallotProp4.xlsx",
    ),
    (date(2021, 11, 2), "5"): (
        "Proposition 5",
        "Increasing the jurisdiction of the New York City Civil Court",
        True,
        "20230111005858",
        "2021/General/GE2021_BallotProp5.xlsx",
    ),
    (date(2022, 11, 8), "1"): (
        "Proposition 1",
        "Clean Water, Clean Air, and Green Jobs Environmental Bond Act of 2022",
        True,
        "20230427193813",
        "2022/General/2022proposition1Results.xlsx",
    ),
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
WA_OLD = "https://web.archive.org/web/{}id_/https://wei.sos.wa.gov/agency/osos/en/press_and_research/PreviousElections/"
WA_OLD_FILES = {
    date(2004, 11, 2): (
        "20170211042058",
        "2004/Documents/2004%20General%20Election/2004%20Measure%20Results.xls",
        {"I-872": "872", "I-884": "884", "I-892": "892", "R-55": "55", "I-297": "297"},
    ),
    date(2005, 11, 8): (
        "20170128174533",
        "2005/Documents/2005%20General%20folder/2005Gen%20Measures.XLS",
        {
            "Fuel": "912",
            "Neg Healthcare": "330",
            "Clean Air": "901",
            "Perf Audits": "900",
            "Jud Conduct": "8207",
            "Medical": "336",
        },
    ),
}
WA_BILLS = {"2901": "53", "2969": "51"}
WA_RESULTS = "https://results.vote.wa.gov/results/"
WA_DATES = ("20071106", "20081104", "20091103", "20101102", "20111108", "20121106", "20221108")
DERIVED_HAND = {
    (date(2012, 11, 6), "1185"): (
        "Initiative 1185",
        "Concerning tax and fee increases imposed by state government",
        "Initiative",
    ),
    (date(2012, 11, 6), "1240"): (
        "Initiative 1240",
        "Concerning the creation of a public charter school system",
        "Initiative",
    ),
    (date(2012, 11, 6), "74"): (
        "Referendum Measure 74",
        "Concerning marriage for same-sex couples",
        "Popular Referendum",
    ),
    (date(2012, 11, 6), "502"): ("Initiative 502", "Concerning marijuana", "Initiative"),
    (date(2012, 11, 6), "8221"): (
        "SJR 8221",
        "Concerning implementing the Commission on State Debt recommendations regarding Washington's debt limit",
        "Legislative Referendum",
    ),
    (date(2012, 11, 6), "8223"): (
        "SJR 8223",
        "Concerning investments by the University of Washington and Washington State University",
        "Legislative Referendum",
    ),
    (date(2012, 11, 6), "1"): (
        "Advisory Vote 1",
        "Concerning a B&O tax deduction for certain financial institutions' interest on residential loans (ESSB 6635)",
        "Advisory Vote",
    ),
    (date(2012, 11, 6), "2"): (
        "Advisory Vote 2",
        "Concerning expiration of a tax on possession of petroleum products (SHB 2590)",
        "Advisory Vote",
    ),
    (date(2022, 11, 8), "39"): ("Advisory Vote 39", "Concerning the aircraft fuel tax (ESSB 5974)", "Advisory Vote"),
    (date(2022, 12, 10), "1"): (
        "Amendment No. 1",
        "Requires U.S. citizenship in order to register and vote in Louisiana",
        "Legislative Referendum",
    ),
    (date(2022, 12, 10), "2"): (
        "Amendment No. 2",
        "Requires Senate confirmation of gubernatorially appointed members of the State Civil Service Commission",
        "Legislative Referendum",
    ),
    (date(2022, 12, 10), "3"): (
        "Amendment No. 3",
        "Requires Senate confirmation of certain members of the State Police Commission",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "111"): (
        "Measure 111",
        "State must ensure affordable healthcare access, balanced against requirement to fund schools, other essential services",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "112"): (
        "Measure 112",
        "Removes language allowing slavery and involuntary servitude as punishment for crime",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "113"): (
        "Measure 113",
        "Legislators with ten unexcused absences from floor sessions disqualified from holding next term of office",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "114"): (
        "Measure 114",
        "Requires permit to acquire firearms; police maintain permit/firearm database; criminally prohibits certain ammunition magazines",
        "Initiative",
    ),
    (date(2022, 11, 8), "1"): (
        "Question 1",
        "Court of Appeals and Court of Special Appeals: renaming",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "2"): (
        "Question 2",
        "Eligibility to serve as a state senator or delegate: place of abode",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "3"): ("Question 3", "Civil jury trials: amount in controversy", "Legislative Referendum"),
    (date(2022, 11, 8), "4"): (
        "Question 4",
        "Legalization of adult use and possession of cannabis",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "5"): (
        "Question 5",
        "Circuit Court for Howard County: judges sitting as the Orphans' Court",
        "Legislative Referendum",
    ),
    (date(2012, 11, 6), "A"): (
        "Constitutional Amendment A",
        "Deposit of severance tax revenue into the Permanent State Trust Fund",
        "Legislative Referendum",
    ),
    (date(2012, 11, 6), "B"): (
        "Constitutional Amendment B",
        "Property tax exemption for military personnel on active duty",
        "Legislative Referendum",
    ),
    (date(2022, 11, 8), "40"): (
        "Advisory Vote 40",
        "Concerning premiums for transportation network company drivers' workers' compensation (ESHB 2076)",
        "Advisory Vote",
    ),
}
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
    "20221210",
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
MI = "https://web.archive.org/web/2id_/https://mielections.us/election/results/DATA/{}_MI_CENR_BY_COUNTY.xls"
MI_PROPOSALS = {
    ("2004GEN", "04-1"): (
        "Constitutional amendment requiring voter approval of gambling and lottery expansion",
        "Initiative",
    ),
    ("2004GEN", "04-2"): (
        "Constitutional amendment recognizing marriage as between one man and one woman",
        "Initiative",
    ),
    ("2006GEN", "06-1"): (
        "Constitutional amendment dedicating use of conservation and recreation funds",
        "Legislative Referendum",
    ),
    ("2006GEN", "06-2"): ("Constitutional amendment banning affirmative action programs", "Initiative"),
    ("2006GEN", "06-3"): (
        "Referendum on Public Act 160 of 2005, the mourning dove hunting season",
        "Popular Referendum",
    ),
    ("2006GEN", "06-4"): ("Constitutional amendment restricting the use of eminent domain", "Legislative Referendum"),
    ("2006GEN", "06-5"): ("Constitutional amendment establishing mandatory school funding", "Initiative"),
    ("2008GEN", "08-1"): ("Initiated law allowing the medical use of marijuana", "Initiative"),
    ("2008GEN", "08-2"): ("Constitutional amendment on human embryonic stem cell research", "Initiative"),
    ("2010GEN", "10-1"): ("Question on convening a constitutional convention", "Legislative Referendum"),
    ("2010GEN", "10-2"): (
        "Constitutional amendment banning felons from certain public offices and positions",
        "Legislative Referendum",
    ),
    ("2012GEN", "12-1"): ("Referendum on Public Act 4 of 2011, the Emergency Manager Law", "Popular Referendum"),
    ("2012GEN", "12-2"): ("Constitutional amendment protecting collective bargaining rights", "Initiative"),
    ("2012GEN", "12-3"): ("Constitutional amendment establishing a renewable energy standard", "Initiative"),
    ("2012GEN", "12-4"): ("Constitutional amendment establishing the Michigan Quality Home Care Council", "Initiative"),
    ("2012GEN", "12-5"): (
        "Constitutional amendment requiring a two-thirds legislative vote or voter approval for new taxes",
        "Initiative",
    ),
    ("2012GEN", "12-6"): (
        "Constitutional amendment requiring voter approval for new international bridges and tunnels",
        "Initiative",
    ),
    ("2014PRI", "14-1"): ("Referendum on Public Act 80 of 2014", "Popular Referendum"),
    ("2014GEN", "14-1"): ("Referendum on Public Act 520 of 2012", "Popular Referendum"),
    ("2014GEN", "14-2"): ("Referendum on Public Act 21 of 2013", "Popular Referendum"),
    ("2015SG1", "15-1"): (
        "Constitutional amendment increasing the sales and use tax to fund roads and schools",
        "Legislative Referendum",
    ),
    ("2018GEN", "18-1"): ("Initiated law to regulate marijuana like alcohol", "Initiative"),
    ("2018GEN", "18-2"): ("Constitutional amendment creating an independent redistricting commission", "Initiative"),
    ("2018GEN", "18-3"): ("Constitutional amendment on voting rights and election administration", "Initiative"),
    ("2020GEN", "20-1"): (
        "Constitutional amendment on oil and gas royalty funds for parks and conservation",
        "Legislative Referendum",
    ),
    ("2020GEN", "20-2"): (
        "Constitutional amendment requiring a search warrant for electronic data and communications",
        "Legislative Referendum",
    ),
}
MI_OLD = "http://web.archive.org/web/2id_/http://miboecfr.nictusa.com/election/results/{}GEN/9000000{}.html"
MI_OLD_PROPOSALS = (
    (
        date(2000, 11, 7),
        "00",
        1,
        "00-1",
        "Constitutional amendment permitting indirect state support of nonpublic school students",
        "Initiative",
    ),
    (
        date(2000, 11, 7),
        "00",
        2,
        "00-2",
        "Constitutional amendment requiring a two-thirds legislative vote for laws affecting local governments",
        "Initiative",
    ),
    (
        date(2002, 11, 5),
        "02",
        1,
        "02-1",
        "Referendum on Public Act 269 of 2001 amending the Michigan election law",
        "Popular Referendum",
    ),
    (
        date(2002, 11, 5),
        "02",
        2,
        "02-2",
        "Bonds for sewage treatment, storm water, and water pollution projects",
        "Legislative Referendum",
    ),
    (
        date(2002, 11, 5),
        "02",
        3,
        "02-3",
        "Constitutional amendment granting state classified employees collective bargaining with binding arbitration",
        "Initiative",
    ),
    (
        date(2002, 11, 5),
        "02",
        4,
        "02-4",
        "Constitutional amendment reallocating tobacco settlement revenue",
        "Initiative",
    ),
)
NC = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{}/results_pct_{}.zip"
NC_DAYS = (date(2004, 11, 2), date(2010, 11, 2))
NC_2000 = "https://web.archive.org/web/20100129210216id_/www.app.sboe.state.nc.us/NCSBE/Elec/Results/y2000elect/"
NC_2002 = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2002_11_05/results_statewide_20021105.pdf"
NC_2002_TOTALS = (1283375, 507426)
WV = "https://web.archive.org/web/20130612072655id_/http://www.sos.wv.gov/elections/history/electionreturns/Documents/{0}/{0}%20Constitutional%20Amendment{1}.pdf"
WV_FILES = {date(2000, 11, 7): "%20Gen", date(2002, 11, 5): "", date(2004, 11, 2): ""}
WV_2005 = WV.split("Documents/")[0] + "Documents/2005%20-%20Special/2005%20Special%20Election.pdf"
WI = "https://web.archive.org/web/{}id_/https://elections.wi.gov/sites/elections.wi.gov/files/{}"
WI_FILES = {
    date(2000, 11, 7): (
        "20200114182421",
        "2000_Referendum_County_Returns.pdf",
        ("Extend Right to Vote to Adult Children of US Citizens",),
    ),
    date(2006, 11, 7): (
        "20200114182227",
        "2006_FallElection_Referendum_CountyReturns.pdf",
        ("Definition of Marriage", "Death Penalty-Advisory"),
    ),
}
IA = "https://web.archive.org/web/20141231002203id_/http://sos.iowa.gov/elections/"
IA_2008 = IA + "pdf/2008/OfficialCanvass2008General.pdf"
IA_2010 = IA + "results/xls/2010/general/statewidepct.xlsx"
MS = "https://web.archive.org/web/2022id_/https://www.sos.ms.gov/links/elections/results/statewide/Statewide%20Initiative%20Measure%20{}%20-%20General%20Election%202011%20Results.pdf"
MS_2011 = ("26", "27", "31")
MS_2003 = "https://sos.ms.gov/elections/electionresults/2003General/Certified%20Results/Senate%20Concurrent%20Resolution%20522.pdf"
INBOX = DATA / "inbox" / "manual"
KY_PAGE = "https://elect.ky.gov/results/#{}"
KY_FILES = {
    date(2000, 11, 7): "MF-429/2000-11/ky-2000-general-county-results.txt",
    date(2002, 11, 5): "MF-429/2002-11/ky-2002-general-county-results.txt",
    date(2004, 11, 2): "MF-429/2004-11/ky-2004-general-county-results.txt",
}
MN_PAGE = "https://electionresults.sos.mn.gov/{}"
MN_FILES = {
    date(2006, 11, 7): (
        "MF-442/mn-2006-general-constitutional-amendment-by-county.txt",
        "Dedicate motor vehicle sales tax revenue to transportation",
    ),
    date(2008, 11, 4): (
        "MF-442/mn-2008-general-constitutional-amendment-by-county.txt",
        "Clean Water, Land and Legacy Amendment",
    ),
}
MN_COUNTIES = [
    "Aitkin",
    "Anoka",
    "Becker",
    "Beltrami",
    "Benton",
    "Big_Stone",
    "Blue_Earth",
    "Brown",
    "Carlton",
    "Carver",
    "Cass",
    "Chippewa",
    "Chisago",
    "Clay",
    "Clearwater",
    "Cook",
    "Cottonwood",
    "Crow_Wing",
    "Dakota",
    "Dodge",
    "Douglas",
    "Faribault",
    "Fillmore",
    "Freeborn",
    "Goodhue",
    "Grant",
    "Hennepin",
    "Houston",
    "Hubbard",
    "Isanti",
    "Itasca",
    "Jackson",
    "Kanabec",
    "Kandiyohi",
    "Kittson",
    "Koochiching",
    "Lac_qui_Parle",
    "Lake",
    "Lake_of_the_Woods",
    "Le_Sueur",
    "Lincoln",
    "Lyon",
    "McLeod",
    "Mahnomen",
    "Marshall",
    "Martin",
    "Meeker",
    "Mille_Lacs",
    "Morrison",
    "Mower",
    "Murray",
    "Nicollet",
    "Nobles",
    "Norman",
    "Olmsted",
    "Otter_Tail",
    "Pennington",
    "Pine",
    "Pipestone",
    "Polk",
    "Pope",
    "Ramsey",
    "Red_Lake",
    "Redwood",
    "Renville",
    "Rice",
    "Rock",
    "Roseau",
    "Scott",
    "Sherburne",
    "Sibley",
    "St._Louis",
    "Stearns",
    "Steele",
    "Stevens",
    "Swift",
    "Todd",
    "Traverse",
    "Wabasha",
    "Wadena",
    "Waseca",
    "Washington",
    "Watonwan",
    "Wilkin",
    "Winona",
    "Wright",
    "Yellow_Medicine",
]
TN_PAGE = "https://sos.tn.gov/elections/election-results#{}"
TN_FILES = {
    date(2002, 11, 5): ("MF-430/2002-11/amendments.pdf", ("1", "2")),
    date(2006, 11, 7): ("MF-430/2006-11/RptCtyCon1andCon2.pdf", ("1", "2")),
    date(2010, 11, 2): ("MF-430/2010-11/CACounty.pdf", ("1",)),
}
KS = "https://sos.ks.gov/elections/22elec/"
KS_2022 = (
    (
        date(2022, 8, 2),
        "2022-Primary-Election-Constitutional-Amendment-Results-By-Precinct.xlsx",
        "1",
        "Constitutional amendment on abortion and legislative authority (Value Them Both)",
        (385014, 557837),
    ),
    (
        date(2022, 11, 8),
        "2022-General-Election-Constitutional-Amendment-Question-1-Rules-and-Regulations-Results-By-Precinct.xlsx",
        "1",
        "Constitutional amendment on legislative oversight of rules and regulations",
        (472074, 480719),
    ),
    (
        date(2022, 11, 8),
        "2022-General-Election-Constitutional-Amendment-Question-2-Sheriff-Election-and-Recall-Results-By-Precinct.xlsx",
        "2",
        "Constitutional amendment on sheriff election and recall",
        (588417, 366167),
    ),
)
KS_2000 = "https://web.archive.org/web/20130102101017/http://www.kssos.org/elmpast/2kgqs.exe"
KS_2005 = "https://sos.ks.gov/elections/05elec/2005_Constitutional_Amendment_results_by_county.xls"
KS_2010 = "https://kssos.org/elections/10elec/2010_General_Election_Results.pdf"
KS_2010_TOTALS = {"1": (720102, 95957), "2": (503143, 302966)}
KS_GAPS = {(date(2022, 8, 2), "1", "YES"): -3, (date(2022, 8, 2), "1", "NO"): -16}
UT_2007 = "https://vote.utah.gov/wp-content/uploads/2023/09/2007Muni_Special.pdf"
UT_FILES = {
    date(2000, 11, 7): "2000Gen.pdf",
    date(2002, 11, 5): "2002Gen.pdf",
    date(2004, 11, 2): "2004Gen.pdf",
    date(2006, 11, 7): "2006Gen.pdf",
    date(2008, 11, 4): "2008Gen.xls",
    date(2010, 11, 2): "2010Gen.xls",
    date(2012, 11, 6): "2012-General-Canvass-Report.xls",
}
UT_COUNTIES = (
    "Beaver",
    "Box Elder",
    "Cache",
    "Carbon",
    "Daggett",
    "Davis",
    "Duchesne",
    "Emery",
    "Garfield",
    "Grand",
    "Iron",
    "Juab",
    "Kane",
    "Millard",
    "Morgan",
    "Piute",
    "Rich",
    "Salt Lake",
    "San Juan",
    "Sanpete",
    "Sevier",
    "Summit",
    "Tooele",
    "Uintah",
    "Utah",
    "Wasatch",
    "Washington",
    "Wayne",
    "Weber",
)
WA_HISTORY = {
    "Referendum Bill": "https://web.archive.org/web/20251017055222id_/https://apps.sos.wa.gov/elections/initiatives/statistics_referendumbills.aspx",
    "Referendum Measure": "https://web.archive.org/web/20251017055222id_/https://apps.sos.wa.gov/elections/initiatives/statistics_referendummeasures.aspx",
}
OR_HISTORY = (
    "https://web.archive.org/web/20251003191748id_/https://sos.oregon.gov/blue-book/Documents/elections/initiative.pdf"
)
OR_2022 = "https://web.archive.org/web/20221219164703id_/https://sos.oregon.gov/elections/Documents/results/november-general-2022.pdf"
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
    date(2010, 11, 2): ("20170706", "2010-general-precinctLevel.zip"),
}
AL_CERTS = {
    date(
        2006, 11, 7
    ): "https://sos.alabama.gov/sites/default/files/voter-pdfs/2006/statecert-2006-general-election-11-29-2006-without-write-in-appendix.pdf",
    date(
        2008, 11, 4
    ): "https://sos.alabama.gov/sites/default/files/voter-pdfs/2008/statecert-2008-general-election-11-25-2008-without-write-in-appendix.pdf",
}
NJ = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
NJ_FILES = {
    date(2000, 11, 7): "2000/2000-ballot-questions-tallies.pdf",
    date(2003, 11, 4): "2003/2003gen-elect_pubques_tally.pdf",
    date(2005, 11, 8): "2005/2005_Official_public_questions_tallies.pdf",
    date(2006, 11, 7): "2006/2006_official-public-question_tallies.pdf",
    date(2007, 11, 6): "2007/2007-official-general-election-tallies(pub-ques)-12.3.07.pdf",
    date(2008, 11, 4): "2008/2008-official-gen-elect-tallies-public-ques-120208.pdf",
    date(2009, 11, 3): "2009/2009-official-public-question-tallies-120109.pdf",
    date(2010, 11, 2): "2010/2010-official-gen-elect-tallies-public-question-112410.pdf",
    date(2011, 11, 8): "2011/2011-official-gen-elect-public-question-results.pdf",
}
MD = "https://elections.maryland.gov/elections/archive/"
MD_FILES = {
    date(2000, 11, 7): ("2000/results/pregnque.html",),
    date(2002, 11, 5): ("2002/results/g_all_offices.txt",),
    date(2006, 11, 7): ("2006/results/general/Statewide_Ballot_Question_Results.html",),
    date(2008, 11, 4): tuple(f"2008/results/general/gen_detail_qresults_2008_4_000{n}S-.html" for n in (1, 2)),
    date(2010, 11, 2): tuple(f"2010/results/General/gen_detail_qresults_2010_2_000{n}S-.html" for n in (1, 2, 3)),
    date(2022, 11, 8): tuple(f"2022/general_results/gen_detail_qresults_2022_{n}_1.html" for n in range(1, 6)),
}
MD_COUNTIES = frozenset(
    (
        "Allegany",
        "Anne Arundel",
        "Baltimore",
        "Baltimore City",
        "Calvert",
        "Caroline",
        "Carroll",
        "Cecil",
        "Charles",
        "Dorchester",
        "Frederick",
        "Garrett",
        "Harford",
        "Howard",
        "Kent",
        "Montgomery",
        "Prince George's",
        "Queen Anne's",
        "St. Mary's",
        "Somerset",
        "Talbot",
        "Washington",
        "Wicomico",
        "Worcester",
    )
)
AK_STATEWIDE = {
    date(2002, 8, 27): "results (2).pdf",
    date(2004, 11, 2): "results.pdf",
    date(2006, 8, 22): "results (3).pdf",
    date(2008, 8, 26): "results (4).pdf",
    date(2008, 11, 4): "results (1).pdf",
    date(2010, 8, 24): "results (5).pdf",
}
AK_DISTRICTS = {
    date(2002, 8, 27): "sovc{} (1).pdf",
    date(2006, 8, 22): "hd{} (2).pdf",
    date(2008, 8, 26): "hd{} (3).pdf",
    date(2008, 11, 4): "hd{} (1).pdf",
    date(2010, 8, 24): "hd{} (4).pdf",
}
AK_2000 = "https://web.archive.org/web/2id_/http://www.elections.alaska.gov/results/00GENR/data/results.txt"
AL_2007 = "https://sos.alabama.gov/sites/default/files/voter-pdfs/2007/statecert-cae-canvass-06-27-2007.pdf"
AL_2007_TOTALS = {
    "1": ((189032, 50111), ((0.7028, 0.7753, 0.7186, 0.8481), (0.7028, 0.6962, 0.7186, 0.7721))),
    "2": ((200698, 38797), ((0.7028, 0.5381, 0.7186, 0.6263), (0.7028, 0.4561, 0.7186, 0.5351))),
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
MO = (
    "https://web.archive.org/web/2015id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/CountyGeneral{}.pdf"
)
MO_PART2 = (
    "https://web.archive.org/web/2015id_/http://sos.mo.gov/cmsimages/ElectionResultsStatistics/CountyGeneral{}Part2.pdf"
)
MO_DAYS = (
    date(2000, 11, 7),
    date(2002, 11, 5),
    date(2004, 11, 2),
    date(2006, 11, 7),
    date(2008, 11, 4),
    date(2010, 11, 2),
)
MO_PRIMARY = (
    "https://web.archive.org/web/2015id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/CountyPrimary{}.pdf"
)
MO_PRIMARY_PART2 = (
    "https://web.archive.org/web/2015id_/http://sos.mo.gov/cmsimages/ElectionResultsStatistics/CountyPrimary{}Part2.pdf"
)
MO_PRIMARIES = (date(2002, 8, 6), date(2004, 8, 3), date(2006, 8, 8), date(2010, 8, 3))
MO_CODES = {"KANSAS CITY": "29095", "JACKSON": "29095", "ST LOUIS CITY": "29510", "ST LOUIS COUNTY": "29189"}
MT = "https://sosmt.gov/"
MT_FILES = {
    date(2000, 11, 7): "Portals/142/Elections/archives/2000s/2000/2000-GenState.pdf",
    date(2002, 11, 5): "wp-content/uploads/attachments/2002-GenState.pdf",
    date(2004, 11, 2): "Portals/142/Elections/archives/2000s/2004/2004-GenState.pdf",
    date(2006, 11, 7): "wp-content/uploads/attachments/2006_State_General_Final_Results.pdf",
    date(2008, 11, 4): "wp-content/uploads/attachments/2008_State_General.pdf",
    date(2010, 11, 2): "wp-content/uploads/attachments/2010_General_Election_Official_Statewide_Canvass.pdf",
}
WY = "https://web.archive.org/web/2id_/https://sos.wyo.gov/Elections/Docs/"
WY_FILES = {
    date(2000, 11, 7): (
        "2000/00Results/00General/00_General_SW_Candidate_Judicial_Issues.pdf",
        [[("Amendment B", 4)]],
    ),
    date(2002, 11, 5): (
        "2002/02Results/02General/02_General%20Election_SW_Candidates.pdf",
        [[(f"Constitutional Amendment {letter}", 2 * index) for index, letter in enumerate("ABCD")]],
    ),
    date(2004, 11, 2): (
        "2004/04Results/04General/04_General_SW_Candidates.pdf",
        [[(f"Constitutional Amendment {letter}", 2 * index) for index, letter in enumerate("ABCD")]],
    ),
    date(2006, 11, 7): (
        "2006/06Results/06General/SW_Const.Amendments_Summary.pdf",
        [
            [("Constitutional Amendment A", 0), ("Constitutional Amendment B", 2)],
            [("Constitutional Amendment C", 0)],
        ],
    ),
    date(2008, 11, 4): (
        "2008/08Results/General/SW-ConstitutionalAmendments.pdf",
        [[("Constitutional Amendment A", 0), ("Constitutional Amendment B", 4)]],
    ),
}
WY_COUNTIES = frozenset(
    [
        "Albany",
        "Big Horn",
        "Campbell",
        "Carbon",
        "Converse",
        "Crook",
        "Fremont",
        "Goshen",
        "Hot Springs",
        "Johnson",
        "Laramie",
        "Lincoln",
        "Natrona",
        "Niobrara",
        "Park",
        "Platte",
        "Sheridan",
        "Sublette",
        "Sweetwater",
        "Teton",
        "Uinta",
        "Washakie",
        "Weston",
    ]
)
ND = "https://www.sos.nd.gov/sites/default/files/documents/elections/election-results-pdfs/{}-general.pdf"
ND_DAYS = {date(2004, 11, 2): 1, date(2006, 11, 7): 3, date(2008, 11, 4): 4, date(2010, 11, 2): 2}
ND_ABSTRACTS = (
    "https://vip.sos.nd.gov/pdfs/Abstracts%20by%20Year%5C2000%20through%202008%20Statewide%20Election%20Results%5C"
)
ND_OLD = {
    date(2000, 6, 13): ("2000%5CPrimary%20Election%2006-13-2000", 4, 1),
    date(2000, 11, 7): ("2000%5CGeneral%20Election%2011-07-2000", 1, 1),
    date(2002, 6, 11): ("2002%5CPrimary%20Election%2006-11-2002", 2, 0),
    date(2002, 11, 5): ("2002%5CGeneral%20Election%2011-05-2002", 3, 0),
}
ND_PRIMARIES = {
    date(2006, 6, 13): ("2006%5CPrimary%20Election%2006-13-2006", 2),
    date(2008, 6, 10): ("2008%5CPrimary%20Election%2006-10-2008", 1),
}
ND_2004_PRIMARY = (
    date(2004, 6, 8),
    ND_ABSTRACTS + "2004%5CPrimary%20Election%2006-08-2004",
    ((31462, 44039), (54048, 23812)),
)
ND_2004_NOTE = (
    " Only the statewide totals are recorded: the county labels in the Secretary of State's scanned abstract are too "
    "degraded to read, so the figures are the printed Total row."
)
SC = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_{}.pdf"
SC_2010 = "https://www.enr-scvotes.org/SC/19077/39934/reports/detailxml.zip"
GA = "https://web.archive.org/web/{}id_/http://sos.georgia.gov/elections/election_results/{}.htm"
GA_PAGES = {
    date(2008, 11, 4): (
        ("20081208213608", "2008_1104/270", "Constitutional Amendment 1"),
        ("20081208213613", "2008_1104/271", "Constitutional Amendment 2"),
        ("20081208213618", "2008_1104/272", "Constitutional Amendment 3"),
    ),
    date(2010, 11, 2): (
        ("20101207040853", "2010_1102/09000", "Constitutional Amendment 1"),
        ("20101107190426", "2010_1102/09001", "Constitutional Amendment 2"),
        ("20101107190454", "2010_1102/09002", "Constitutional Amendment 3"),
        ("20101106161831", "2010_1102/09003", "Constitutional Amendment 4"),
        ("20101107190519", "2010_1102/09004", "Constitutional Amendment 5"),
        ("20101107190434", "2010_1102/09050", "Referendum A"),
    ),
}
GA_TOTALS = {
    date(2000, 11, 7): ("20100528021925", "2000_1107/summary"),
    date(2002, 11, 5): ("20100528031621", "2002_1105/summary"),
    date(2004, 11, 2): ("20100515034712", "2004_1102/summary"),
    date(2006, 11, 7): ("20100515034733", "2006_1107/swall"),
}
GA_2006 = (
    "Constitutional Amendment 1",
    "Constitutional Amendment 2",
    "Constitutional Amendment 3",
    "Referendum A",
    "Referendum B",
    "Referendum C",
    "Referendum D",
    "Referendum E",
    "Referendum F",
)
SC_DAYS = {
    date(2000, 11, 7): 3,
    date(2002, 11, 5): 2,
    date(2004, 11, 2): 2,
    date(2006, 11, 7): 7,
    date(2008, 11, 4): 3,
    date(2010, 11, 2): 4,
}
AL_2022_URL = (
    "https://www.sos.alabama.gov/sites/default/files/election-data/2022-11/"
    "Final%20Canvass%20of%20Results%20(canvassed%20by%20state%20canvassing%20board%2011-28-2022).pdf"
)
AL_2022 = {
    "C": ("Recompiled Constitution Ratification Question", "Legislative Referendum", True),
    "1": ("Allow Denial of Bail for Offenses Enumerated by State Legislature", "Legislative Referendum", True),
}
SC_2022 = {
    "1": ("General Reserve Fund Increase", "Legislative Referendum", True),
    "2": ("Capital Reserve Fund Increase", "Legislative Referendum", True),
}
SC_2022_FILES = {"1": 2352, "2": 2351}
SC_ELSTATS = "https://sc.elstats.civera.com/api/download_contest/{}_table.csv?split_party=false"
SC_COUNTIES = [
    "Abbeville",
    "Aiken",
    "Allendale",
    "Anderson",
    "Bamberg",
    "Barnwell",
    "Beaufort",
    "Berkeley",
    "Calhoun",
    "Charleston",
    "Cherokee",
    "Chester",
    "Chesterfield",
    "Clarendon",
    "Colleton",
    "Darlington",
    "Dillon",
    "Dorchester",
    "Edgefield",
    "Fairfield",
    "Florence",
    "Georgetown",
    "Greenville",
    "Greenwood",
    "Hampton",
    "Horry",
    "Jasper",
    "Kershaw",
    "Lancaster",
    "Laurens",
    "Lee",
    "Lexington",
    "Marion",
    "Marlboro",
    "McCormick",
    "Newberry",
    "Oconee",
    "Orangeburg",
    "Pickens",
    "Richland",
    "Saluda",
    "Spartanburg",
    "Sumter",
    "Union",
    "Williamsburg",
    "York",
]
HI = "https://files.hawaii.gov/elections/files/results/{}/general/{}.pdf"
HI_DAYS = {
    2000: date(2000, 11, 7),
    2002: date(2002, 11, 5),
    2004: date(2004, 11, 2),
    2006: date(2006, 11, 7),
    2008: date(2008, 11, 4),
    2010: date(2010, 11, 2),
}
HI_COUNTIES = {"coh": "Hawaii", "cch": "Honolulu", "cok": "Kauai", "com": "Maui"}
HI_MEASURES = {
    2000: {
        "UH SELF": ("1", "Constitutional Amendment 1"),
        "TAX REVIEW": ("2", "Constitutional Amendment 2"),
        "STAGGERED": ("3", "Constitutional Amendment 3"),
    },
    2002: {
        "RESIDENCY": ("1", "Constitutional Amendment 1"),
        "NOT-FOR-PROFIT": ("2", "Constitutional Amendment 2"),
        "FELONY": ("3", "Constitutional Amendment 3"),
    },
    2004: {
        "Sexual Assault": ("1", "Constitutional Amendment 1"),
        "Public Right": ("2", "Constitutional Amendment 2"),
        "Rights of Accused": ("3", "Constitutional Amendment 3"),
        "Felony": ("4", "Constitutional Amendment 4"),
    },
    2006: {
        "Board of Regents": ("1", "Constitutional Amendment 1"),
        "Salary": ("2", "Constitutional Amendment 2"),
        "Justices": ("3", "Constitutional Amendment 3"),
        "Sexual Assault": ("4", "Constitutional Amendment 4"),
        "Agricultural": ("5", "Constitutional Amendment 5"),
    },
    2008: {
        "Age Qualification": ("", "Constitutional Amendment"),
        "Constitutional Convention": ("", "Constitutional Convention Question"),
    },
    2010: {"Board of": ("2376", "HB 2376"), "Tax Rebate": ("2807", "SB 2807")},
}
NM_DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5)}
OH_PAGES = "https://web.archive.org/web/2012id_/http://www.sos.state.oh.us/sos/elections/Research/electResultsMain/"
OH_ISSUES = [
    (date(2000, 11, 7), "1", "2000ElectionsResults/StateIssue1_11072000"),
    (date(2002, 11, 5), "1", "2002Results/issue1"),
    (date(2003, 9, 9), "1", "2003Results/Issue1"),
    (date(2004, 11, 2), "1", "2004ElectionsResults/04-1102Issue1"),
    *((date(2005, 11, 8), str(n), f"2005ElectionsResults/05-1108Issue{n}") for n in range(1, 6)),
    *((date(2008, 11, 4), str(n), f"2008ElectionResults/issue{n}_110408") for n in (1, 2, 3, 5, 6)),
    *((date(2009, 11, 3), str(n), f"2009ElectionResults/20091103issue{n}") for n in (1, 2, 3)),
    *((date(2011, 11, 8), str(n), f"2011results/20111108Issue{n}") for n in (1, 2, 3)),
]
NM_PAGES = Path(__file__).with_name("nm_county_pages.tsv")
MT_BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
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
AR_TALLY = "https://results.tally-enr.com/results/arkansas/1844/FullDataFile.json"
AR_TALLY_DAY = date(2022, 11, 8)
AR_HISTORY = AR + "Initiatives_and_Amendments_1938-2018_1.pdf"
AR_HIGHWAY_BONDS = date(2011, 11, 8)
AR_2022 = {
    "1": ("Convene the General Assembly in Extraordinary Session by Written Proclamation", "Legislative Referendum"),
    "2": ("Constitutional Amendment and Ballot Initiative Reform Amendment", "Legislative Referendum"),
    "3": ("Arkansas Religious Freedom Amendment", "Legislative Referendum"),
    "4": ("Adult Use of Cannabis and Licensed Commercial Cannabis Facilities", "Initiative"),
}
IL_2010 = "https://www.elections.il.gov/Downloads/ElectionOperations/VoteTotals/2010GEOfficialVote.pdf"
IN = "https://indianavoters.in.gov/ENRHistorical/ExportReferendumsToCSV?year={}&sortBy=&sortDir=&county={}"
IN_DAYS = (date(2004, 11, 2), date(2010, 11, 2), date(2024, 11, 5))
IN_QUESTIONS = {
    "Article 10, Section 1": ("1", "Property Tax Exemptions"),
    "Article 6, Section 2": ("2", "Terms of County Officers"),
    "Article 5, Section 10": ("3", "Line of Gubernatorial Succession"),
    "SHALL PROPERTY TAXES BE LIMITED": ("1", "Property Tax Cap"),
    "remove the state superintendent": (
        "1",
        "Remove Superintendent of Public Instruction from the Line of Gubernatorial Succession",
    ),
}
IN_ALIAS = "LA PORTE"
IN_COUNTIES = (
    "ADAMS",
    "ALLEN",
    "BARTHOLOMEW",
    "BENTON",
    "BLACKFORD",
    "BOONE",
    "BROWN",
    "CARROLL",
    "CASS",
    "CLARK",
    "CLAY",
    "CLINTON",
    "CRAWFORD",
    "DAVIESS",
    "DEARBORN",
    "DECATUR",
    "DEKALB",
    "DELAWARE",
    "DUBOIS",
    "ELKHART",
    "FAYETTE",
    "FLOYD",
    "FOUNTAIN",
    "FRANKLIN",
    "FULTON",
    "GIBSON",
    "GRANT",
    "GREENE",
    "HAMILTON",
    "HANCOCK",
    "HARRISON",
    "HENDRICKS",
    "HENRY",
    "HOWARD",
    "HUNTINGTON",
    "JACKSON",
    "JASPER",
    "JAY",
    "JEFFERSON",
    "JENNINGS",
    "JOHNSON",
    "KNOX",
    "KOSCIUSKO",
    "LAGRANGE",
    "LAKE",
    "LAPORTE",
    "LAWRENCE",
    "MADISON",
    "MARION",
    "MARSHALL",
    "MARTIN",
    "MIAMI",
    "MONROE",
    "MONTGOMERY",
    "MORGAN",
    "NEWTON",
    "NOBLE",
    "OHIO",
    "ORANGE",
    "OWEN",
    "PARKE",
    "PERRY",
    "PIKE",
    "PORTER",
    "POSEY",
    "PULASKI",
    "PUTNAM",
    "RANDOLPH",
    "RIPLEY",
    "RUSH",
    "SCOTT",
    "SHELBY",
    "SPENCER",
    "ST. JOSEPH",
    "STARKE",
    "STEUBEN",
    "SULLIVAN",
    "SWITZERLAND",
    "TIPPECANOE",
    "TIPTON",
    "UNION",
    "VANDERBURGH",
    "VERMILLION",
    "VIGO",
    "WABASH",
    "WARREN",
    "WARRICK",
    "WASHINGTON",
    "WAYNE",
    "WELLS",
    "WHITE",
    "WHITLEY",
)
SD = "https://sdsos.gov/elections-voting/assets/Archive/Prior%20to%202026/"
SD_SHEETS = {date(2000, 11, 7): "00SWgenres.xls"}
SD_PRECINCTS = {date(2010, 11, 2): "pastelections_general2010_bqpct.xls"}
SD_OLD = "https://web.archive.org/web/{}id_/http://www.state.sd.us:80/sos/{}"
SD_NEW = "https://web.archive.org/web/{}id_/http://www.sdsos.gov/electionsvoteregistration/{}"
SD_PAGES = {
    date(2001, 6, 5): (SD_NEW.format("20110612195100", "pastelections_electioninfo01_specialelecreturns.shtm"),),
    date(2002, 11, 5): (
        SD_OLD.format("20030831122516", "2002/02amendAB.htm"),
        SD_OLD.format("20030831122357", "2002/02amendC.htm"),
        SD_OLD.format("20030831123531", "2002/02init1.htm"),
    ),
    date(2004, 11, 2): (SD_NEW.format("20110416214306", "pastelections_electioninfo04_GEballotquestionreturns.shtm"),),
    date(2006, 11, 7): (SD_NEW.format("20110416214157", "pastelections_electioninfo06_GEballotquestions.shtm"),),
}
SD_SCANS = {
    "sd-measures-2008": "https://sdsos.gov/elections-voting/assets/Archive/2002-2011Assets/2008OfficialGeneralElectionStateCanvass.pdf",
    "sd-measures-2012": "https://sdsos.gov/docs/2012ElectionReturnsBooklet.pdf",
}
SD_2012 = {
    "M": (
        "An Amendment to the South Dakota Constitution regarding certain provisions relating to corporations.",
        "Legislative Referendum",
        False,
    ),
    "N": (
        "An Amendment to the South Dakota Constitution repealing certain reimbursement restrictions for travel by legislators to and from a legislative session.",
        "Legislative Referendum",
        False,
    ),
    "O": (
        "An Amendment to the South Dakota Constitution changing the method for distributions from the cement plant trust fund.",
        "Legislative Referendum",
        True,
    ),
    "P": (
        "An Amendment to the South Dakota Constitution adding balanced budget requirements.",
        "Legislative Referendum",
        True,
    ),
    "15": (
        "An initiated measure to increase state general sales and use taxes for additional K-12 public education and Medicaid funding.",
        "Initiative",
        False,
    ),
    "14": ("An Act to establish the Large Project Development Fund.", "Popular Referendum", False),
    "16": (
        "An education reform act to establish a teacher scholarship program, math and science teacher bonuses, merit bonuses, uniform evaluations, and the elimination of state teacher tenure requirements.",
        "Popular Referendum",
        False,
    ),
}
MISPRINTS = {("CA", date(2000, 11, 7), "38", "NO"): 3000}
MISREPORTED = {
    ("ME", date(2005, 11, 8), "6"),
    ("LA", date(2010, 10, 2), "2"),
    ("AL", date(2010, 11, 2), "2"),
    ("AL", date(2010, 11, 2), "4"),
}
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
    "IL": (
        "Illinois State Board of Elections",
        "Official vote totals",
        "https://www.elections.il.gov/ElectionOperations/ElectionVoteTotals.aspx",
    ),
    "IN": (
        "Indiana Election Division",
        "Historical election results: referendums",
        "https://indianavoters.in.gov/ENRHistorical/ElectionResults",
    ),
    "MN": (
        "Minnesota Secretary of State",
        "Constitutional amendment results by county",
        "https://electionresults.sos.mn.gov/",
    ),
    "TN": (
        "Tennessee Secretary of State",
        "Certified county results",
        "https://sos.tn.gov/elections/election-results",
    ),
    "KY": (
        "Kentucky State Board of Elections",
        "Official election night tally results by county",
        "https://elect.ky.gov/results/",
    ),
    "MS": (
        "Mississippi Secretary of State",
        "Total votes reported by county",
        "https://www.sos.ms.gov/elections-voting/election-results",
    ),
    "IA": (
        "Iowa Secretary of State",
        "Official canvass and precinct results",
        "https://sos.iowa.gov/elections/results/index.html",
    ),
    "WI": (
        "Wisconsin Elections Commission",
        "Canvass results by county",
        "https://elections.wi.gov/elections-voting/results",
    ),
    "WV": (
        "West Virginia Secretary of State",
        "Official election returns by county",
        "https://sos.wv.gov/elections/Pages/ElectionResults.aspx",
    ),
    "NC": (
        "North Carolina State Board of Elections",
        "Official election results by precinct",
        "https://www.ncsbe.gov/results-data/election-results",
    ),
    "KS": (
        "Kansas Secretary of State",
        "Official election results by precinct",
        "https://sos.ks.gov/elections/election-results.html",
    ),
    "MI": (
        "Michigan Bureau of Elections",
        "Official election results by county",
        "https://mielections.us/election/results/",
    ),
    "UT": (
        "Utah Lieutenant Governor's Office of Elections",
        "Official canvass by county",
        "https://vote.utah.gov/past-election-results/",
    ),
    "MO": (
        "Missouri Secretary of State",
        "Official election returns by county",
        "https://www.sos.mo.gov/elections/results",
    ),
    "MT": (
        "Montana Secretary of State",
        "Statewide general election canvass",
        "https://sosmt.gov/elections/results/",
    ),
    "WY": (
        "Wyoming Secretary of State",
        "Statewide issues and candidates abstract",
        "https://sos.wyo.gov/Elections/",
    ),
    "ND": (
        "North Dakota Secretary of State",
        "Abstract of votes cast at the general election",
        "https://www.sos.nd.gov/elections/election-results",
    ),
    "SC": (
        "South Carolina State Election Commission",
        "Report of the State Election Commission",
        "https://scvotes.gov/",
    ),
    "HI": (
        "Hawaii Office of Elections",
        "General election county summary reports",
        "https://elections.hawaii.gov/election-results/",
    ),
    "NM": (
        "New Mexico Secretary of State",
        "General election results by county",
        "https://www.sos.nm.gov/sos-archive/election-results-archive/",
    ),
    "TX": ("Texas Secretary of State", "Texas election history: returns by county, 1992–2019", TX),
    "CA": (
        "California Secretary of State",
        "Statement of Vote",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    "AL": ("Alabama Secretary of State", "Election results by county", "https://www.sos.alabama.gov/alabama-votes"),
    "NY": (
        "New York State Board of Elections",
        "Proposal and proposition election returns",
        "https://www.elections.ny.gov/ElectionResults.html",
    ),
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
    "NJ": (
        "New Jersey Division of Elections",
        "Official general election results: public questions",
        "https://www.nj.gov/state/elections/election-information-results.shtml",
    ),
    "MD": (
        "Maryland State Board of Elections",
        "Official general election results: ballot questions",
        "https://elections.maryland.gov/elections/archive/index.html",
    ),
    "AK": (ak_sovc.PUBLISHER, ak_sovc.TITLE, "https://www.elections.alaska.gov/election-results/"),
    "OH": gov_counties.OFFICIAL["OH"][:3],
    "GA": (
        "Georgia Secretary of State",
        "Official election results",
        "https://sos.ga.gov/page/historical-elections-results",
    ),
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
GA_PRELIMINARY = (
    " The county figures are the state's page as captured on November 6 or 7, 2010, before certification; the "
    "certified statewide totals, posted November 15, differ by up to 3,500 votes."
)
STATEWIDE_ONLY = (
    " Only the statewide totals are recorded: the state's archived results pages for this election carry no county "
    "table for ballot questions."
)
KS_2010_NOTE = (
    " Only the statewide totals are recorded, from the Secretary of State's official vote totals. The county pages "
    "the office posted are unofficial election-night returns, and the county table was never certified."
)
AK_STATEWIDE_NOTE = (
    " Only the statewide totals are recorded, taken from the Division of Elections' statewide results sheet. The"
    " state's per-district statements of votes cast for this election are not loaded yet."
)
AL_2007_NOTE = (
    " Only the statewide totals are recorded: the county tables in the Secretary of State's scanned canvass are too "
    "degraded to read, so the figures are the printed Total row, entered by hand from page 3; the box shown on each "
    "figure is approximate."
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


def store_manual(url: str, file: str, media_type: str) -> None:
    try:
        raw.cached(SOURCE, url)
    except LookupError:
        raw.store(
            SOURCE,
            url,
            (INBOX / file).read_bytes(),
            suffix=Path(file).suffix,
            retrieved_at=datetime.fromtimestamp((INBOX / file).stat().st_mtime, UTC),
            media_type=media_type,
        )


def fetch() -> None:
    for stamp, folder in NV_PAGES.values():
        raw.fetch(SOURCE, NV.format(stamp, folder), suffix=".html")
    for stamp, page in GA_TOTALS.values():
        raw.fetch(SOURCE, GA.format(stamp, page), suffix=".htm")
        time.sleep(1)
    for pages in GA_PAGES.values():
        for stamp, page, _ in pages:
            raw.fetch(SOURCE, GA.format(stamp, page), suffix=".htm")
            time.sleep(1)
    for name in [*NE_BOOKS.values(), *NE_SCANS.values()]:
        raw.fetch(SOURCE, NE + name, suffix=".pdf")
    for name in [*AR_BOOKS.values(), *AR_VOICES.values()]:
        raw.fetch(SOURCE, AR + name, suffix=".pdf")
    for name in [*SD_SHEETS.values(), *SD_PRECINCTS.values()]:
        raw.fetch(SOURCE, SD + name, suffix=".xls")
        time.sleep(1)
    for url in SD_SCANS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in (url for urls in SD_PAGES.values() for url in urls):
        raw.fetch(SOURCE, url, suffix=".htm")
        time.sleep(1)
    for day in MO_DAYS:
        for url in (MO, MO_PART2) if day.year >= 2004 else (MO,):
            raw.fetch(SOURCE, url.format(day.year), suffix=".pdf")
            time.sleep(1)
    for day in MO_PRIMARIES:
        for url in (MO_PRIMARY, MO_PRIMARY_PART2) if day.year >= 2004 else (MO_PRIMARY,):
            raw.fetch(SOURCE, url.format(day.year), suffix=".pdf")
            time.sleep(1)
    for _, _, page in OH_ISSUES:
        raw.fetch(SOURCE, f"{OH_PAGES}{page}.aspx", suffix=".html")
        time.sleep(2)
    for day in SC_DAYS:
        if day.year == 2010:
            raw.fetch(SOURCE, SC_2010, suffix=".zip")
        else:
            raw.fetch(SOURCE, SC.format(day.year), suffix=".pdf")
        time.sleep(1)
    for _, _, _, stamp, path in NY_MEASURES.values():
        raw.fetch(SOURCE, NY.format(stamp, path), suffix=Path(path).suffix)
        time.sleep(1)
    raw.fetch(SOURCE, OR_HISTORY, suffix=".pdf")
    for url in WA_HISTORY.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for day in ND_DAYS:
        raw.fetch(SOURCE, ND.format(day.isoformat()), suffix=".pdf")
        time.sleep(1)
    for name, *_ in [*ND_OLD.values(), *ND_PRIMARIES.values()]:
        raw.fetch(SOURCE, ND_ABSTRACTS + name + ".pdf", suffix=".pdf")
        time.sleep(1)
    raw.fetch(SOURCE, ND_2004_PRIMARY[1] + ".pdf", suffix=".pdf")
    for line in NM_PAGES.read_text().splitlines()[1:]:
        raw.fetch(SOURCE, line.split("\t")[1], suffix=".html")
        time.sleep(1)
    for year in HI_MEASURES:
        for code in HI_COUNTIES:
            raw.fetch(SOURCE, HI.format(year, code), suffix=".pdf")
            time.sleep(1)
    for name in MT_FILES.values():
        try:
            raw.cached(SOURCE, MT + name)
        except LookupError:
            raw.store(
                SOURCE,
                MT + name,
                httpx.get(MT + name, headers=MT_BROWSER, follow_redirects=True, timeout=120).raise_for_status().content,
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )
        time.sleep(1)
    for name in AR_SHEETS.values():
        raw.fetch(SOURCE, AR + name, suffix=".xlsx")
    raw.fetch(SOURCE, IL_2010, suffix=".pdf")
    raw.fetch(SOURCE, AR_TALLY, suffix=".json")
    raw.fetch(SOURCE, AR_HISTORY, suffix=".pdf")
    for day in IN_DAYS:
        for county in ("", *IN_COUNTIES, IN_ALIAS):
            raw.fetch(SOURCE, IN.format(day.year, county.replace(" ", "+")), suffix=".csv")
            time.sleep(0.3)
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
    for url in [*AL_CERTS.values(), AL_2007, *(NJ + path for path in NJ_FILES.values())]:
        raw.fetch(SOURCE, url, suffix=".pdf")
        time.sleep(1)
    for paths in MD_FILES.values():
        for path in paths:
            raw.fetch(SOURCE, MD + path, suffix=Path(path).suffix)
            time.sleep(1)
    raw.fetch(SOURCE, UT_2007, suffix=".pdf")
    for number in MS_2011:
        raw.fetch(SOURCE, MS.format(number), suffix=".pdf")
    raw.fetch(SOURCE, MS_2003, suffix=".pdf")
    for day, file in KY_FILES.items():
        store_manual(KY_PAGE.format(day.year), file, "text/plain")
    for day, (file, _) in MN_FILES.items():
        store_manual(MN_PAGE.format(f"{day.year}"), file, "text/plain")
    for day, (file, _) in TN_FILES.items():
        store_manual(TN_PAGE.format(day.year), file, "application/pdf")
    raw.fetch(SOURCE, IA_2008, suffix=".pdf")
    raw.fetch(SOURCE, IA_2010, suffix=".xlsx")
    for stamp, file, _ in WI_FILES.values():
        raw.fetch(SOURCE, WI.format(stamp, file), suffix=".pdf")
        time.sleep(1)
    for day, suffix in WV_FILES.items():
        raw.fetch(SOURCE, WV.format(day.year, suffix), suffix=".pdf")
        time.sleep(1)
    raw.fetch(SOURCE, WV_2005, suffix=".pdf")
    raw.fetch(SOURCE, AL_2022_URL, suffix=".pdf")
    for contest in SC_2022_FILES.values():
        raw.fetch(SOURCE, SC_ELSTATS.format(contest), suffix=".csv")
    for day in NC_DAYS:
        raw.fetch(SOURCE, NC.format(f"{day:%Y_%m_%d}", f"{day:%Y%m%d}"), suffix=".zip")
        time.sleep(1)
    raw.fetch(SOURCE, NC_2002, suffix=".pdf")
    index = raw.fetch(SOURCE, NC_2000 + "staterl.htm", suffix=".htm")
    for page in nc_2000_pages(index.path.read_text(errors="replace")):
        raw.fetch(SOURCE, NC_2000 + page, suffix=".htm")
        time.sleep(1)
    store_manual(KS_2000, "MF-446/ks-2000-general-questions-by-precinct.xls", "application/vnd.ms-excel")
    store_manual(KS_2005, "MF-446/2005_Constitutional_Amendment_results_by_county.xls", "application/vnd.ms-excel")
    store_manual(KS_2010, "MF-446/ks-2010-general-official-vote-totals.pdf", "application/pdf")
    for _, file, *_ in KS_2022:
        raw.fetch(SOURCE, KS + file, suffix=".xlsx")
        time.sleep(1)
    for name in dict.fromkeys(file for file, _ in MI_PROPOSALS):
        raw.fetch(SOURCE, MI.format(name), suffix=".tsv")
        time.sleep(1)
    for _, year, index, *_ in MI_OLD_PROPOSALS:
        raw.fetch(SOURCE, MI_OLD.format(year, index), suffix=".html")
        time.sleep(1)
    raw.fetch(SOURCE, AK_2000, suffix=".txt")
    for path, _ in WY_FILES.values():
        raw.fetch(SOURCE, WY + path, suffix=".pdf")
        time.sleep(1)
    for name in UT_FILES.values():
        raw.store(
            SOURCE,
            f"manual://MF-355/{name}",
            (DATA / "inbox" / "manual" / "MF-355" / name).read_bytes(),
            suffix=Path(name).suffix,
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/octet-stream",
        )
    for day, name in AK_STATEWIDE.items():
        raw.store(
            SOURCE,
            f"manual://MF-359/{day.isoformat()}/{name}",
            (DATA / "inbox" / "manual" / "MF-359" / day.isoformat() / name).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/pdf",
        )
    for day, pattern in AK_DISTRICTS.items():
        for district in range(1, 41):
            raw.store(
                SOURCE,
                f"manual://MF-359/{day.isoformat()}/{pattern.format(district)}",
                (DATA / "inbox" / "manual" / "MF-359" / day.isoformat() / pattern.format(district)).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
                media_type="application/pdf",
            )
    for district in range(1, 41):
        raw.store(
            SOURCE,
            f"manual://MF-359/2000-11-07/sovc{district}.pdf",
            (DATA / "inbox" / "manual" / "MF-359" / "2000-11-07" / f"sovc{district}.pdf").read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/pdf",
        )
    for path in [*AZ_FILES.values(), *AZ_RECOUNTS.values()]:
        raw.fetch(SOURCE, path if path.startswith("https:") else AZ + path, suffix=".pdf")
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
    for stamp, path, _ in WA_OLD_FILES.values():
        raw.fetch(SOURCE, WA_OLD.format(stamp) + path, suffix=".xls")
        time.sleep(2)
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
    for url in dict.fromkeys([*(url for urls in OR_ABSTRACTS.values() for url in urls), OR_2022]):
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
            lines: list[str] = []
            for sheet in text.stdout.split("\f"):
                sheet_lines = sheet.splitlines()
                if "State Ballot Measures" in text.stdout and "State Ballot Measures" not in sheet:
                    continue
                found = sorted(
                    (match.start(), match.group(1)) for line in sheet_lines for match in heading.finditer(line)
                )
                lines.append("\f" + ",".join(prop for _, prop in found))
                lines.extend(sheet_lines)
            for line in lines:
                if line.startswith("\f"):
                    props = line[1:].split(",") if len(line) > 1 else []
                    continue
                if line.lstrip().startswith("Proposition") and heading.search(line):
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
                    "label": f"Proposition {prop}" if (day, prop) in CA_1998 else "",
                    "title": CA_1998.get((day, prop), ("", ""))[0] or None,
                    "kind": CA_1998.get((day, prop), ("", ""))[1] or None,
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
    urls = {day: AL.format(stamp=stamp) + file for day, (stamp, file) in AL_FILES.items()} | AL_CERTS
    for day, url in urls.items():
        file = url.rsplit("/", 1)[1]
        path = raw.cached(SOURCE, url).path
        votes: dict[tuple[str, str], dict[str, int]] = {}
        totals: dict[tuple[str, str], int] = {}
        if file.endswith(".pdf"):
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout
            for page in text.split("\f"):
                if "Constitutional Amendments" not in page:
                    continue
                props = re.findall(r"Statewide (?:Amendment |#)(\d+)", page)
                if not props:
                    continue
                for line in page.splitlines():
                    match = re.fullmatch(r"([A-Za-z. ]+?)\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*", line)
                    if match is None:
                        continue
                    name = match[1].lower().replace("st clair", "st. clair")
                    figures = [int(figure.replace(",", "")) for figure in match[2].split()]
                    for index, prop in enumerate(props):
                        pair = figures[2 * index : 2 * index + 2]
                        if name in AL_COUNTIES:
                            for answer, value in zip(("YES", "NO"), pair, strict=True):
                                votes.setdefault((prop, answer), {})[name] = value
                        elif name == "total":
                            for answer, value in zip(("YES", "NO"), pair, strict=True):
                                totals[(prop, answer)] = value
        elif file.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    county = Path(member).stem.lower()
                    if county not in AL_COUNTIES:
                        continue
                    content = archive.read(member)
                    if content.startswith(b"\xef\xbb\xbf"):
                        namespace = "{urn:schemas-microsoft-com:office:spreadsheet}"
                        workbook = ET.fromstring(content.decode("utf-8-sig").split("?>", 2)[2])
                        sheets = [
                            [
                                [(data.text or "").strip() for data in row.iter(namespace + "Data")]
                                for row in sheet.iter(namespace + "Row")
                            ]
                            for sheet in workbook.iter(namespace + "Worksheet")
                        ]
                    else:
                        book = fastexcel.read_excel(content)
                        sheets = [
                            [
                                [str(cell or "").strip() for cell in row]
                                for row in book.load_sheet(name, header_row=None).to_polars().rows()
                            ]
                            for name in book.sheet_names
                        ]
                    for sheet_rows in sheets:
                        match = re.search(
                            r"^PROPOSED STATEWIDE AMENDMENT NUMBER \w+ \((\d+)\)",
                            " ".join(sheet_rows[0]) if sheet_rows else "",
                        )
                        if match is None:
                            continue
                        totals_row = next(row for row in sheet_rows if row[:1] == ["Totals:"])
                        votes.setdefault((match[1], "YES"), {})[county] = int(totals_row[2])
                        votes.setdefault((match[1], "NO"), {})[county] = int(totals_row[4])
                        totals[(match[1], "YES")] = totals.get((match[1], "YES"), 0) + int(totals_row[2])
                        totals[(match[1], "NO")] = totals.get((match[1], "NO"), 0) + int(totals_row[4])
        elif file.endswith(".exe"):
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
    for number, (counts, boxes) in AL_2007_TOTALS.items():
        for answer, votes, (x0, y0, x1, y1) in zip(("YES", "NO"), counts, boxes, strict=True):
            rows.append(
                {
                    "state": "AL",
                    "day": date(2007, 6, 5),
                    "number": number,
                    "label": "",
                    "url": AL_2007,
                    "county": "statewide",
                    "answer": answer,
                    "votes": votes,
                    "page": 3,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                }
            )
    return rows


def new_jersey() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    row = re.compile(
        r"^\s*(Atlantic|Bergen|Burlington|Camden|Cape May|Cumberland|Essex|Gloucester|Hudson|Hunterdon|Mercer|Middlesex|Monmouth|Morris|Ocean|Passaic|Salem|Somerset|Sussex|Union|Warren|Total)\s+([\d,]+)\s+([\d,]+)\s*$"
    )
    for day, path in NJ_FILES.items():
        url = NJ + path
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        found: dict[str, dict[str, tuple[int, int]]] = {}
        number = ""
        for line in text.splitlines():
            if heading := re.match(r"PUBLIC QUESTION NO\. (\d+)", line):
                number = heading[1]
            elif (match := row.match(line)) and number:
                found.setdefault(number, {})[match[1]] = (
                    int(match[2].replace(",", "")),
                    int(match[3].replace(",", "")),
                )
        for number, table in found.items():
            printed = table.pop("Total")
            if len(table) != 21 or tuple(sum(votes[side] for votes in table.values()) for side in (0, 1)) != printed:
                raise ValueError(f"NJ {day} question {number}: {len(table)} counties do not sum to {printed}")
            for county, votes in table.items():
                for answer, count in zip(("YES", "NO"), votes, strict=True):
                    rows.append(
                        {
                            "state": "NJ",
                            "day": day,
                            "number": number,
                            "label": "Public Question 1" if day.year == 2010 else "",
                            "url": url,
                            "county": county,
                            "answer": answer,
                            "votes": count,
                        }
                    )
    return rows


def maryland() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, paths in MD_FILES.items():
        for path in paths:
            url = MD + path
            text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
            found: dict[str, dict[str, tuple[int, int]]] = {}
            if path.endswith(".txt"):
                for line in text.splitlines():
                    fields = [field.strip() for field in line.split("|")]
                    if fields[0] == "Statewide Ballot Question":
                        found.setdefault(fields[3].removeprefix("Question "), {})[fields[2].removesuffix(" County")] = (
                            int(fields[-2]),
                            int(fields[-1]),
                        )
            else:
                cells = [
                    cell.strip()
                    for cell in html.unescape(re.sub(r"<[^>]+>", "|", text)).replace("\xa0", " ").split("|")
                    if cell.strip()
                ]
                number = ""
                for index, cell in enumerate(cells):
                    if heading := re.match(r"(?:Question|Constitutional Amendment) (\d+)\b", cell):
                        number = heading[1]
                    elif number and index + 2 < len(cells) and re.fullmatch(r"[\d,]+(?: \(.*\))?", cells[index + 1]):
                        name = cell.removesuffix(" County").replace("Saint ", "St. ")
                        total = name.lower().startswith("total")
                        if total or name in MD_COUNTIES:
                            yes, no = (
                                int(re.match(r"[\d,]+", cells[index + offset])[0].replace(",", "")) for offset in (1, 2)
                            )
                            found.setdefault(number, {})["Total" if total else name] = (yes, no)
            for number, table in found.items():
                printed = table.pop("Total", None)
                if (
                    len(table) != 24
                    or printed is not None
                    and tuple(sum(votes[side] for votes in table.values()) for side in (0, 1)) != printed
                ):
                    raise ValueError(f"MD {day} question {number}: {len(table)} counties do not sum to {printed}")
                for county, votes in table.items():
                    for answer, count in zip(("YES", "NO"), votes, strict=True):
                        rows.append(
                            {
                                "state": "MD",
                                "day": day,
                                "number": str(int(number)),
                                "label": DERIVED_HAND.get((day, str(int(number))), ("",))[0],
                                "url": url,
                                "county": county,
                                "answer": answer,
                                "votes": count,
                            }
                        )
    return rows


def alaska() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, name in AK_STATEWIDE.items():
        url = f"manual://MF-359/{day.isoformat()}/{name}"
        markup = subprocess.run(
            ["pdftotext", "-bbox", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for sheet in markup.split("<page ")[1:]:
            words = [
                (float(x0), float(y0), html.unescape(text))
                for x0, y0, text in re.findall(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="[\d.]+">(.*?)</word>', sheet
                )
            ]
            for index, (x, y, text) in enumerate(words):
                if text == "NO." and words[index - 1][2] == "MEASURE" and index + 1 < len(words):
                    number, label = words[index + 1][2], ""
                elif text == "PROPOSITION" and words[index - 1][2] == "BOND" and words[index + 1][2] in "ABC":
                    number, label = "", "Bond Question"
                else:
                    continue
                x = words[index - 1][0]
                votes: dict[str, int] = {}
                for answer in ("YES", "NO"):
                    below = sorted(
                        (w for w in words if w[2] == answer and w[1] > y and abs(w[0] - x) < 150), key=lambda w: w[1]
                    )
                    line = sorted(
                        (w for w in words if abs(w[1] - below[0][1]) < 1 and w[0] > below[0][0]), key=lambda w: w[0]
                    )
                    votes[answer] = int(line[0][2].replace(",", ""))
                for answer, count in votes.items():
                    rows.append(
                        {
                            "state": "AK",
                            "day": day,
                            "number": number,
                            "label": label,
                            "url": url,
                            "county": "statewide",
                            "answer": answer,
                            "votes": count,
                        }
                    )
    for day, pattern in AK_DISTRICTS.items():
        statewide = {
            (row["number"], row["label"], row["answer"]): row["votes"]
            for row in rows
            if row["day"] == day and row["county"] == "statewide"
        }
        summed: dict[tuple[object, object, object], int] = {}
        found: list[dict[str, object]] = []
        for district in range(1, 41):
            url = f"manual://MF-359/{day.isoformat()}/{pattern.format(district)}"
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            latest: dict[tuple[str, str], list[int]] = {}
            for page in text.split("\f"):
                lines = page.splitlines()
                header = next(
                    (line for line in lines[:25] if re.search(r"MEASURE NO\. \d|BOND PROPOSITION A", line)), None
                )
                total = [line for line in lines if re.match(r"\s*Total\s+\d", line)]
                if header is None or not total:
                    continue
                cells = [int(cell.replace(",", "")) for cell in re.findall(r"(?<![\d.])\d[\d,]*(?![\d.%])", total[-1])]
                if cells[0] == 0:
                    continue
                starts = [i for i, cell in enumerate(cells) if cell == cells[0]]
                groups = [cells[a:b] for a, b in zip(starts, [*starts[1:], len(cells)], strict=True)]
                titles = re.split(r"\s{2,}", header.strip())
                if len(groups) != len(titles):
                    raise ValueError(f"{url}: {len(groups)} groups under {titles}")
                for title, group in zip(titles, groups, strict=True):
                    if match := re.search(r"MEASURE NO\. (\d+)|BOND PROPOSITION A", title):
                        latest[(match[1] or "", "" if match[1] else "Bond Question")] = group
            for (number, label), (_, votes, yes, no) in latest.items():
                if votes != yes + no:
                    raise ValueError(f"{url} {number or label}: {yes} + {no} != {votes}")
                for answer, count in (("YES", yes), ("NO", no)):
                    summed[number, label, answer] = summed.get((number, label, answer), 0) + count
                    found.append(
                        {
                            "state": "AK",
                            "day": day,
                            "number": number,
                            "label": label,
                            "url": url,
                            "county": f"{day.isoformat()}:{district:02}",
                            "answer": answer,
                            "votes": count,
                        }
                    )
        if summed != statewide:
            raise ValueError(f"AK {day} district sums differ from the statewide results: {summed} vs {statewide}")
        rows = [row for row in rows if not (row["day"] == day and row["county"] == "statewide")] + found
    measure_votes: dict[int, dict[str, int]] = {}
    current = 0
    for line in raw.cached(SOURCE, AK_2000).path.read_text(encoding="latin-1").splitlines():
        if line[:1].isdigit():
            if re.match(r'\d+ \d+ "(?:Const\. Am\.|Initiative|Referendum)', line):
                current += 1
                measure_votes[current] = {}
            else:
                current = 0
        elif current and (answer := re.fullmatch(r'\s+\d+ \d+ "(YES|NO)" "" 0 (\d+)', line)):
            measure_votes[current][answer[1]] = int(answer[2])
    district_votes: dict[int, dict[str, int]] = {number: {"YES": 0, "NO": 0} for number in measure_votes}
    for district in range(1, 41):
        url = f"manual://MF-359/2000-11-07/sovc{district}.pdf"
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        triples: list[list[int]] = []
        for page in text.split("\f"):
            lines = page.splitlines()
            sub = [line for line in lines if line.strip().startswith("SubTotal")]
            pairs = sum(len(re.findall(r"\bYES\b", line)) for line in lines[:25])
            if not sub or not pairs:
                continue
            cells = [
                int(cell.replace(",", ""))
                for cell in re.findall(r"(?<![\d.])\d[\d,]*(?![\d.%])", sub[-1].replace("SubTotal", ""))
            ]
            triples.extend(cells[len(cells) - 3 * pairs :][i : i + 3] for i in range(0, 3 * pairs, 3))
        for number, (total, yes, no) in enumerate(triples[: len(measure_votes)], start=1):
            if total != yes + no:
                raise ValueError(f"AK 2000 district {district} measure {number}: {yes} + {no} != {total}")
            for answer, count in (("YES", yes), ("NO", no)):
                district_votes[number][answer] += count
                rows.append(
                    {
                        "state": "AK",
                        "day": date(2000, 11, 7),
                        "number": str(number),
                        "label": "",
                        "url": url,
                        "county": f"2000-11-07:{district:02}",
                        "answer": answer,
                        "votes": count,
                    }
                )
    if district_votes != measure_votes:
        raise ValueError("AK 2000 district sums differ from the statewide results")
    for year, district, path in ak_sovc.files():
        if year == 2000:
            continue
        url = f"manual://MF-266/{year}/{path.name}"
        for title, _, columns in ak_sovc.read(raw.cached(ak_sovc.SOURCE, url).path, year):
            measure = re.fullmatch(r"(?:BALLOT )?MEASURE (?:NO\. |#)(\d+)\b.*", title or "")
            bond = re.fullmatch(r"(?:BONDING )?PROPOSITION ([ABC])\b.*", title or "")
            if measure is None and bond is None:
                continue
            for answer, column in columns:
                rows.append(
                    {
                        "state": "AK",
                        "day": ak_sovc.DAYS[year],
                        "number": measure[1] if measure else "",
                        "label": "" if measure else f"Bonding Proposition {bond[1]}",
                        "url": url,
                        "county": f"{ak_sovc.DAYS[year].isoformat()}:{district:02}",
                        "answer": answer,
                        "votes": column["Total"],
                    }
                )
    return rows


def arizona() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    recounted = {(day, prop): AZ + path for (day, prop), path in AZ_RECOUNTS.items()}
    for day, path in [*AZ_FILES.items(), *((day, path) for (day, _), path in AZ_RECOUNTS.items())]:
        url = path if path.startswith("https:") else AZ + path
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
                answer = {"FOR": "YES", "AGAINST": "NO", "YES": "YES", "NO": "NO"}[label.strip().upper()]
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
                        "label": DERIVED_HAND.get((date(int(day[:4]), int(day[4:6]), int(day[6:])), match[1]), ("",))[
                            0
                        ],
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
            number = re.search(r"advisory-vote-no-(\d+)|(?:measure|resolution|bill)-(?:no-)?(\d+)", page, re.IGNORECASE)
            if number is None:
                raise ValueError(f"{url}: no measure number")
            found.append((date(int(day[:4]), int(day[4:6]), int(day[6:])), number[1] or number[2], url, votes, totals))
    for day, (stamp, path, sheets) in WA_OLD_FILES.items():
        url = WA_OLD.format(stamp) + path
        workbook = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
        for sheet, number in sheets.items():
            votes = {"YES": {}, "NO": {}}
            totals = {}
            for row in workbook.load_sheet_by_name(sheet, header_row=None).to_polars().iter_rows():
                cells = [cell for cell in row if cell is not None and str(cell).strip()]
                name = next((str(cell) for cell in cells if not re.fullmatch(r"[\d.]+", str(cell))), None)
                figures = [float(cell) for cell in cells if re.fullmatch(r"[\d.]+", str(cell))]
                if len(figures) != 4 or name is None:
                    continue
                if name == "Total":
                    totals = {"YES": int(figures[0]), "NO": int(figures[2])}
                elif name in WA_COUNTIES:
                    votes["YES"][name] = int(figures[0])
                    votes["NO"][name] = int(figures[2])
            if not totals:
                totals = {answer: sum(counts.values()) for answer, counts in votes.items()}
            found.append((day, number, url, votes, totals))
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
                    "label": DERIVED_HAND.get((day, number), ("",))[0],
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


def ohio_issues() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, number, page in OH_ISSUES:
        url = f"{OH_PAGES}{page}.aspx"
        text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", "replace")
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        tokens = [
            cell for part in re.split(r"<[^>]+>", text) if (cell := re.sub(r"\s+", " ", html.unescape(part)).strip())
        ]
        width = 5 if day.year == 2000 else 2
        lines: dict[str, list[int]] = {}
        name = None
        for token in tokens[tokens.index("Adams", tokens.index("County")) :]:
            if re.fullmatch(r"[\d,]+", token):
                lines[name].append(int(token.replace(",", "")))
            elif token[0].isalpha() and not token.startswith("Stay"):
                name = token
                lines[name] = []
            elif not token.endswith("%") and not re.fullmatch(r"[\d.]+|\*", token):
                break
            if name is not None and name.upper().startswith("TOTAL") and len(lines[name]) >= width:
                break
        total = next((figures for name, figures in lines.items() if name.upper().startswith("TOTAL")), None)
        counties = {name: figures for name, figures in lines.items() if not name.upper().startswith("TOTAL")}
        if total is None:
            total = counties[next(reversed(counties))][width:]
        if len(counties) != 88:
            raise ValueError(f"OH {url}: {len(counties)} counties")
        yes_at, no_at = (3, 4) if width == 5 else (0, 1)
        counties = {name: [figures[yes_at], figures[no_at]] for name, figures in counties.items()}
        if [sum(pair[side] for pair in counties.values()) for side in (0, 1)] != total[yes_at : no_at + 1]:
            raise ValueError(f"OH {url}: counties do not add up to the total")
        rows.extend(
            {
                "state": "OH",
                "day": day,
                "number": number,
                "label": "",
                "url": url,
                "county": county,
                "answer": answer,
                "votes": pair[side],
            }
            for county, pair in counties.items()
            for side, answer in enumerate(("YES", "NO"))
        )
    return rows


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
        found: dict[str, dict[str, tuple[list[int], str]]] = {}
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


def illinois() -> list[dict[str, object]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, IL_2010).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    counts = r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%\s+[\d.]+%"
    section = text[text.index("CONSTITUTIONAL AMENDMENT PROPOSAL") :]
    section = section[: re.search(rf"(?m)^\s+TOTAL\s+{counts}", section).end()]
    tallies = {
        county.title(): (int(yes.replace(",", "")), int(no.replace(",", "")))
        for county, _, _, yes, no in re.findall(rf"(?m)^\s*(?:[ivx]+\s+)?([A-Z][A-Za-z .'-]+?)\s+{counts}\s*$", section)
        if county != "TOTAL"
    }
    total = re.search(rf"(?m)^\s+TOTAL\s+{counts}", section)
    if len(tallies) != 102 or [sum(sides[side] for sides in tallies.values()) for side in (0, 1)] != [
        int(total[3].replace(",", "")),
        int(total[4].replace(",", "")),
    ]:
        raise ValueError("Illinois 2010 amendment: counties do not add up to the printed total")
    return [
        {
            "state": "IL",
            "day": date(2010, 11, 2),
            "number": "1",
            "label": "Constitutional Amendment",
            "url": IL_2010,
            "county": county,
            "answer": answer,
            "votes": sides[side],
            "title": "Recall of the Governor",
            "kind": "Legislative Referendum",
        }
        for county, sides in tallies.items()
        for side, answer in enumerate(("YES", "NO"))
    ]


def indiana() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in IN_DAYS:
        tallies: dict[str, dict[str, list[int]]] = {}
        for county in ("", *IN_COUNTIES, IN_ALIAS):
            url = IN.format(day.year, county.replace(" ", "+"))
            for row in csv.reader(io.StringIO(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))):
                if (
                    row[0] == "Statewide"
                    and row[1].startswith(f"{day:%B} {day.day}, {day.year} General")
                    and "Amendment" in row[2]
                ):
                    question = next(key for key in IN_QUESTIONS if key in row[3])
                    tallies.setdefault(question, {})["LAPORTE" if county == IN_ALIAS else county] = [
                        int(row[4]),
                        int(row[5]),
                    ]
        for question, by_county in tallies.items():
            number, title = IN_QUESTIONS[question]
            total = by_county.pop("")
            if len(by_county) != 92 or [sum(sides[side] for sides in by_county.values()) for side in (0, 1)] != total:
                raise ValueError(f"{day} Public Question {number}: counties do not add up to the statewide total")
            rows.extend(
                {
                    "state": "IN",
                    "day": day,
                    "number": number,
                    "label": f"Public Question {number}",
                    "url": IN.format(
                        day.year, (IN_ALIAS if county == "LAPORTE" and day.year < 2024 else county).replace(" ", "+")
                    ),
                    "county": county.title(),
                    "answer": answer,
                    "votes": sides[side],
                    "title": title,
                    "kind": "Legislative Referendum",
                }
                for county, sides in by_county.items()
                for side, answer in enumerate(("YES", "NO"))
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
    history = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, AR_HISTORY).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    bonds = re.search(
        r"Highway Grant Anticipation and Tax\s+YES\s+([\d,]+) \(.*\n\s*Special Highway Bond.*?\s+NO\s+([\d,]+) \(",
        history,
    )
    rows.extend(
        {
            "state": "AR",
            "day": AR_HIGHWAY_BONDS,
            "number": "1",
            "label": "Referred Question",
            "url": AR_HISTORY,
            "county": "statewide",
            "answer": answer,
            "votes": int(bonds[group].replace(",", "")),
            "title": "Arkansas Highway Financing Act of 2011",
            "kind": "Legislative Referendum",
        }
        for group, answer in ((1, "YES"), (2, "NO"))
    )
    tally = json.loads(raw.cached(SOURCE, AR_TALLY).path.read_text())
    for contest in tally["ContestData"]:
        if not (found := re.match(r"Issue No\. (\d): ", contest["ContestName"])):
            continue
        number = found[1]
        counted: dict[str, list[int]] = {}
        for unit in contest["Counties"]:
            sides = counted.setdefault(unit["CountyName"].title(), [0, 0])
            for candidate in unit["Candidates"]:
                sides[candidate["Name"] == "AGAINST"] += candidate["TotalVotes"]
        total = {candidate["Name"]: candidate["TotalVotes"] for candidate in contest["Candidates"]}
        if (
            len(counted) != 75
            or sum(sides[0] for sides in counted.values()) != total["FOR"]
            or sum(sides[1] for sides in counted.values()) != total["AGAINST"]
        ):
            raise ValueError(f"{AR_TALLY} Issue {number}: counties do not add up to the reported totals")
        rows.extend(
            {
                "state": "AR",
                "day": AR_TALLY_DAY,
                "number": number,
                "label": f"Issue {number}",
                "url": AR_TALLY,
                "county": county,
                "answer": answer,
                "votes": sides[side],
                "title": AR_2022[number][0],
                "kind": AR_2022[number][1],
            }
            for county, sides in counted.items()
            for side, answer in enumerate(("YES", "NO"))
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
    for day, urls in SD_PAGES.items():
        for url in urls:
            page = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
            page = re.sub(r"(?is)<(script|style).*?</\1>", "", page)
            tokens = [
                text
                for cell in re.split(r"<[^>]+>", page)
                if (text := re.sub(r"\s+", " ", html.unescape(cell)).strip())
            ]
            labels: list[str] = []
            kind = ""
            at = 0
            while at < len(tokens):
                token = tokens[at]
                if (whole := re.fullmatch(r"(?i)initiat\w* measure (\d{1,2})", token)) and token not in tokens[
                    : at - 1
                ]:
                    labels.append(f"Initiated Measure {whole[1]}")
                elif re.search(r"(?i)amend|initiat|ref+er", token):
                    kind = token
                elif re.fullmatch(r"[A-Z]|\d{1,2}", token) and (
                    re.search(r"(?i)amend|initiat|ref+er", tokens[at - 1])
                    or re.fullmatch(r"[A-Z]|\d{1,2}", tokens[at - 1])
                ):
                    labels.append(
                        f"Constitutional Amendment {token}"
                        if token.isalpha()
                        else f"Referred Law {token}"
                        if re.search(r"(?i)ref+er", kind)
                        else f"Initiated Measure {token}"
                    )
                elif token in ("AURORA", "Aurora") and not tokens[at - 1].replace(",", "").isdigit():
                    merged: dict[str, list[int]] = {}
                    while tokens[at] != "PERCENTAGE" and tokens[at + 1].replace(",", "").isdigit():
                        end = at + 1
                        while end < len(tokens) and tokens[end].replace(",", "").isdigit():
                            end += 1
                        merged.setdefault(tokens[at], []).extend(
                            int(cell.replace(",", "")) for cell in tokens[at + 1 : end]
                        )
                        at = end
                    totals = merged.pop("TOTALS")
                    counties = list(merged.items())
                    width = len(totals)
                    taken, labels = labels[: width // 2], labels[width // 2 :]
                    if (
                        len(taken) * 2 != width
                        or len(counties) != 66
                        or [sum(column) for column in zip(*(v for _, v in counties), strict=True)] != totals
                    ):
                        raise ValueError(f"{url}: {taken}, {len(counties)} counties, or columns off their totals")
                    rows.extend(
                        {
                            "state": "SD",
                            "day": day,
                            "number": label.rsplit(" ", 1)[1],
                            "label": label,
                            "url": url,
                            "county": county,
                            "answer": answer,
                            "votes": values[2 * position + offset],
                        }
                        for position, label in enumerate(taken)
                        for offset, answer in enumerate(("YES", "NO"))
                        for county, values in counties
                    )
                at += 1
            if labels:
                raise ValueError(f"{url}: {labels} left without a table")
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
                    "label": contest["label"],
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


def washington_history() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for kind, url in WA_HISTORY.items():
        page = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
        page = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", page, flags=re.DOTALL)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
        parts = re.split(r"REFERENDUM (?:BILL|MEASURE) NO\. (\d+(?: [A-Z])?)", text)
        for number, block in zip(parts[1::2], parts[2::2], strict=True):
            held = re.search(r"(?:held on|at the) ([A-Z][a-z]+ \d+, ?\d{4})", block)
            vote = re.search(r"For\s*[-–—\s]*([\d,]+)\s*Against\s*[-–—\s]*([\d,]+)", block)
            if held is None or vote is None:
                continue
            day = datetime.strptime(re.sub(r",(?=\d)", ", ", held[1]), "%B %d, %Y").date()
            if day.year >= 2000:
                continue
            head = re.split(r"\bFiled\b|\bThe measure was\b", block)[0]
            title = (re.search(r"\((.*)\)", head) or re.search(r"(.*)", head))[1].strip()
            title = re.sub(r"^Chapter [^,]+, Laws of [\d-]+(?:,[^,(]*Session)?\.?,?\s*", "", title).strip(" .")
            number = number.replace(" ", "")
            for answer, count in zip(("YES", "NO"), vote.groups(), strict=True):
                rows.append(
                    {
                        "state": "WA",
                        "day": day,
                        "number": f"{kind[0]}{number}",
                        "label": f"{kind} {number}",
                        "url": url,
                        "county": "statewide",
                        "answer": answer,
                        "votes": int(count.replace(",", "")),
                        "title": title,
                        "kind": "Legislative Referendum" if kind.endswith("Bill") else "Popular Referendum",
                    }
                )
    return rows


def oregon_history() -> list[dict[str, object]]:
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, OR_HISTORY).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    blocks: list[tuple[date, list[str]]] = []
    day = None
    for line in lines[next(i for i, line in enumerate(lines) if line.strip() == "June 2, 1902") :]:
        text = line.strip()
        if not text or text.isdigit() or text.startswith("Election Date/Measure"):
            continue
        if heading := re.fullmatch(r"([A-Z][a-z]+ \d+, \d{4})(?: \(.*\))?", text):
            day = datetime.strptime(heading[1], "%B %d, %Y").date()
            if day.year >= 2000:
                break
        elif re.match(r"\d+[A-E]?\.\s*\S", text):
            blocks.append((day, [text]))
        elif blocks:
            blocks[-1][1].append(text)
    rows: list[dict[str, object]] = []
    seen: set[tuple[date, str]] = set()
    for day, block in blocks:
        votes = None
        words = []
        for text in block:
            if (found := re.search(r"\s\*?(\d{1,3}(?:,\d{3})*)\s+(\d{1,3}(?:,\d{3})*)$", text)) and votes is None:
                votes = (int(found[1].replace(",", "")), int(found[2].replace(",", "")))
                text = text[: found.start()]
            words.append(text.strip())
        title = " ".join(words)
        number, title = re.match(r"(\d+[A-E]?)\.\s*(.*)", title).groups()
        suffix = re.search(r"\s*—([LIR])\d?$", title)
        if votes is None or suffix is None:
            continue
        title = title[: suffix.start()]
        label = f"Measure {number}"
        if (day, number) in seen:
            label += " (second listed)"
            number += "-2"
        seen.add((day, number))
        for answer, count in zip(("YES", "NO"), votes, strict=True):
            rows.append(
                {
                    "state": "OR",
                    "day": day,
                    "number": number,
                    "label": label,
                    "url": OR_HISTORY,
                    "county": "statewide",
                    "answer": answer,
                    "votes": count,
                    "title": title,
                    "kind": "Advisory Vote"
                    if title.startswith("Advisory Vote")
                    else {"L": "Legislative Referendum", "I": "Initiative", "R": "Popular Referendum"}[suffix[1]],
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
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, OR_2022).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    for number, body, yes, no in re.findall(
        r"Measure (\d+)\n.*?\nCounty\s+\*?Yes\s+\*?No\n(.*?)\n\s+Total\s+([\d,]+)\s+([\d,]+)", text, re.DOTALL
    ):
        table = re.findall(r"^(\w[\w ]*?)\s+([\d,]+)\s+([\d,]+)$", body, re.MULTILINE)
        if (
            [county for county, _, _ in table] != list(OR_COUNTIES)
            or sum(int(value.replace(",", "")) for _, value, _ in table) != int(yes.replace(",", ""))
            or sum(int(value.replace(",", "")) for _, _, value in table) != int(no.replace(",", ""))
        ):
            raise ValueError(f"{OR_2022} Measure {number}: {len(table)} counties")
        rows.extend(
            {
                "state": "OR",
                "day": date(2022, 11, 8),
                "number": number,
                "label": f"Measure {number}",
                "url": OR_2022,
                "county": county,
                "answer": answer,
                "votes": int(value.replace(",", "")),
            }
            for county, *votes in table
            for answer, value in zip(("YES", "NO"), votes, strict=True)
        )
    return rows


def missouri() -> list[dict[str, object]]:
    value = re.compile(r"\b(Yes|No)\s+([\d,]+)\b")
    heading = re.compile(
        r"^\s*(?:Constitutional Amendment(?: No\.)? (\d+)|Proposition ([A-Z])|(Constitutional Convention))\b"
    )
    rows: list[dict[str, object]] = []
    for day, url in (
        *((day, url.format(day.year)) for day in MO_DAYS for url in (MO, MO_PART2) if day.year >= 2004 or url == MO),
        *(
            (day, url.format(day.year))
            for day in MO_PRIMARIES
            for url in (MO_PRIMARY, MO_PRIMARY_PART2)
            if day.year >= 2004 or url == MO_PRIMARY
        ),
    ):
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\n")
        tallies: dict[tuple[str, str, str], list[int]] = {}
        county = current = None
        block: dict[str, int] = {}
        for index, line in enumerate(lines):
            if re.fullmatch(r"\s{0,8}[A-Z][A-Z .'&-]+", line) and any(
                text.strip().startswith("Office") for text in lines[index + 1 : index + 9]
            ):
                name = re.sub(r"[^A-Z ]", "", line).strip()
                county, current, block = MO_CODES.get(name, name), None, {}
            elif current is not None and (found := value.search(line)):
                if found[1] in block:
                    raise ValueError(f"MO {day} {county} {current}: {found[1]} twice")
                block[found[1]] = int(found[2].replace(",", ""))
            elif found := heading.match(line):
                current = found[1] or found[2] or "CC"
                label = (
                    f"Constitutional Amendment {found[1]}"
                    if found[1]
                    else f"Proposition {found[2]}"
                    if found[2]
                    else "Constitutional Convention"
                )
                block = {}
            elif current is not None and "Total Votes" in line:
                figures = re.findall(
                    r"\d[\d,]*",
                    " ".join(
                        [line.split("Total Votes")[1]]
                        + [text for text in lines[index + 1 : index + 8] if re.fullmatch(r"\s*\d[\d,]*\s*", text)]
                    ),
                )
                if not figures:
                    raise ValueError(f"MO {day} {county} {label}: no total")
                total = figures[0]
                if set(block) != {"Yes", "No"} or sum(block.values()) != int(total.replace(",", "")):
                    raise ValueError(f"MO {day} {county} {label}: {block} against {total}")
                for answer, votes in block.items():
                    key = (county, current, answer)
                    tallies.setdefault(key, [label, 0])[1] += votes
                current = None
        for (code, number, answer), (label, votes) in tallies.items():
            rows.append(
                {
                    "state": "MO",
                    "day": day,
                    "number": number,
                    "label": label,
                    "url": url,
                    "county": code,
                    "answer": answer.upper(),
                    "votes": votes,
                }
            )
    return rows


def alabama_2022() -> list[dict[str, object]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, AL_2022_URL).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    start = next(at for at, line in enumerate(text) if "Proposed Statewide Amendment Number One (1)" in line)
    end = next(at for at, line in enumerate(text) if "Proposed Statewide Amendment Number Two (2)" in line)
    figures = re.compile(r"^(\S[A-Za-z. ]*?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")
    found = [match for line in text[start:end] if (match := figures.match(line))]
    totals = next(match for match in found if match[1] == "Total")
    counties = [match for match in found if match[1] != "Total"]
    if len(counties) != 67:
        raise ValueError(f"AL 2022: {len(counties)} counties")
    rows: list[dict[str, object]] = []
    for number, label, columns in (("C", "Constitution Ratification Question", (2, 3)), ("1", "Amendment 1", (4, 5))):
        for at, answer in zip(columns, ("YES", "NO"), strict=True):
            if sum(int(match[at].replace(",", "")) for match in counties) != int(totals[at].replace(",", "")):
                raise ValueError(f"AL 2022 {label} {answer}: counties do not add up to the state total")
            rows.extend(
                {
                    "state": "AL",
                    "day": date(2022, 11, 8),
                    "number": number,
                    "label": label,
                    "url": AL_2022_URL,
                    "county": match[1],
                    "answer": answer,
                    "votes": int(match[at].replace(",", "")),
                }
                for match in counties
            )
    return rows


def south_carolina_2022() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for number, contest in SC_2022_FILES.items():
        url = SC_ELSTATS.format(contest)
        counties: dict[str, tuple[int, int]] = {}
        state: tuple[int, int] | None = None
        for line in csv.reader(raw.cached(SOURCE, url).path.read_text().splitlines()):
            if line[0] == "County":
                counties[line[1]] = (int(line[2]), int(line[3]))
            elif line[0] == "State":
                state = (int(line[2]), int(line[3]))
        if len(counties) != 46 or state is None:
            raise ValueError(f"{url}: {len(counties)} counties")
        if tuple(sum(votes[at] for votes in counties.values()) for at in (0, 1)) != state:
            raise ValueError(f"{url}: counties do not add up to the state total")
        rows.extend(
            {
                "state": "SC",
                "day": date(2022, 11, 8),
                "number": number,
                "label": f"Amendment {number}",
                "url": url,
                "county": county,
                "answer": answer,
                "votes": votes[at],
            }
            for county, votes in counties.items()
            for answer, at in (("YES", 0), ("NO", 1))
        )
    return rows


def south_carolina() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, count in SC_DAYS.items():
        url = SC_2010 if day.year == 2010 else SC.format(day.year)
        tables: list[tuple[str, dict[str, list[int]]]] = []
        lines = (
            []
            if day.year == 2010
            else subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split("\n")
        )
        if day.year == 2010:
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                for contest in ET.fromstring(archive.read("detail.xml")).iter("Contest"):
                    text = contest.get("text") or ""
                    if contest.get("isQuestion") != "true" or not text.startswith("Amendment"):
                        continue
                    table: dict[str, list[int]] = {}
                    for choice in contest.findall("Choice"):
                        side = {"Yes": 0, "No": 1}[choice.get("text") or ""]
                        for county in choice.findall("VoteType/County"):
                            table.setdefault(county.get("name") or "", [0, 0])[side] += int(county.get("votes") or 0)
                    if len(table) != 46:
                        raise ValueError(f"SC {day} {text}: {len(table)} counties")
                    tables.append((text.removeprefix("Amendment "), table))
        elif day.year >= 2006:
            starts = [
                at
                for at, line in enumerate(lines)
                if (day.year == 2006 and line.strip() == "COUNTY                *WINNER* Yes      No             TOTAL")
                or (day.year == 2008 and re.match(r"\s*Amendment No\. \d \(Vote For 1\)", line))
            ]
            names = ["1", "2a", "2b", "3a", "3b", "4", "5"] if day.year == 2006 else ["1", "2", "3"]
            for name, start in zip(names, starts, strict=True):
                table: dict[str, list[int]] = {}
                for line in lines[start + 1 :]:
                    if figures := re.fullmatch(r"\s*([A-Za-z]+)(?: County)?\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", line):
                        votes = [int(figures[at].replace(",", "")) for at in (2, 3, 4)]
                        if votes[0] + votes[1] != votes[2]:
                            raise ValueError(f"SC {day} {name} {figures[1]}: yes and no do not add up")
                        table[figures[1]] = votes[:2]
                    elif re.match(r"\s*(STATE TOTAL|Totals)", line):
                        if len(table) != 46:
                            raise ValueError(f"SC {day} {name}: {len(table)} counties")
                        for column in range(2):
                            if sum(votes[column] for votes in table.values()) != int(
                                re.findall(r"[\d,]{3,}", line)[column].replace(",", "")
                            ):
                                raise ValueError(f"SC {day} {name}: column {column} does not match the state total")
                        break
                tables.append((name, table))
        else:
            start = next(at for at, line in enumerate(lines) if re.search(r"AMENDMENT # 1\s+AMENDMENT # 2", line))
            table = {}
            suspect: dict[tuple[str, int], bool] = {}
            for line in lines[start:]:
                tail = re.sub(r"[^A-Za-z0-9 ,.]", " ", line)
                if re.match(r"\s*STATE TOTAL", tail):
                    total = [
                        int(re.sub(r"\D", "", part)) for part in re.split(r"\s{2,}", tail.split("TOTAL", 1)[1].strip())
                    ]
                    break
                if figures := re.match(r"\s*([A-Za-z][A-Za-z ]*?)\s{2,}([\dIl][\dIl,. ]*)$", tail):
                    parts = re.split(r"\s{2,}", re.sub(r" ,", ",", figures[2].strip()))
                    if len(parts) != 2 * count:
                        continue
                    matched = [county for county in SC_COUNTIES if county.upper() == figures[1].strip().upper()] or [
                        county
                        for county in SC_COUNTIES
                        if county not in table and county.upper().endswith(figures[1].strip().upper())
                    ]
                    if len(matched) != 1:
                        raise ValueError(f"SC {day}: cannot place {figures[1]}")
                    table[matched[0]] = [
                        int(re.sub(r"\D", "", part.replace("I", "1").replace("l", "1"))) for part in parts
                    ]
                    for at, part in enumerate(parts):
                        if not re.fullmatch(r"\d{1,3}([,.]\d{3})*", part):
                            suspect[(matched[0], at)] = True
            else:
                raise ValueError(f"SC {day}: no state total")
            for county, column in suspect:
                if sum(1 for _, other in suspect if other == column) != 1:
                    raise ValueError(f"SC {day}: more than one damaged figure in column {column}: {sorted(suspect)}")
                table[county][column] = total[column] - sum(
                    votes[column] for name, votes in table.items() if name != county
                )
            if day.year == 2000:
                head = lines[start : start + 120]
                lone = [
                    int(re.sub(r"\D", "", line))
                    for line in head
                    if re.fullmatch(r"\s*\d[\d,]*\s*", line) and line.strip() != "0"
                ]
                last = next(
                    [int(re.sub(r"\D", "", part)) for part in line.split()]
                    for line in head
                    if re.fullmatch(r"\s*\d[\d,]*(\s{2,}\d[\d,]*){3}\s*", line)
                )
                for at, county in enumerate(SC_COUNTIES[:3]):
                    table[county] = [lone[at], lone[4 + at], lone[8 + at], lone[11 + at], lone[14 + at], lone[17 + at]]
                table["Anderson"] = [lone[3], lone[7], *last]
            if len(table) != 46:
                raise ValueError(f"SC {day}: {len(table)} counties: {sorted(set(SC_COUNTIES) - set(table))}")
            for column in range(2 * count):
                if sum(votes[column] for votes in table.values()) != total[column]:
                    raise ValueError(f"SC {day}: column {column} does not add up to {total[column]}")
            for measure in range(count):
                tables.append(
                    (
                        str(measure + 1),
                        {county: votes[2 * measure : 2 * measure + 2] for county, votes in table.items()},
                    )
                )
        rows.extend(
            {
                "state": "SC",
                "day": day,
                "number": re.sub(r"\D", "", name) if name.isdigit() else "",
                "label": f"Constitutional Amendment {name}" if day.year == 2006 else f"Amendment {name}",
                "url": url,
                "county": county,
                "answer": answer,
                "votes": votes[side],
            }
            for name, table in tables
            for county, votes in table.items()
            for side, answer in enumerate(("YES", "NO"))
        )
    return rows


def wyoming() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, (path, tables) in WY_FILES.items():
        url = WY + path
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\n")
        blocks: list[dict[str, list[int]]] = [{}]
        for line in lines[next(i for i, line in enumerate(lines) if re.search(r"Amendment [A-D]", line)) :]:
            if figures := re.fullmatch(r"\s*([A-Z][A-Za-z ]+?)\s{2,}((?:(?:\d[\d,]*|NA)\s*)+)", line):
                name = figures[1].strip()
                if name in WY_COUNTIES or name in ("Total", "Official Totals"):
                    blocks[-1]["Total" if name.startswith(("Total", "Official")) else name] = [
                        -1 if part == "NA" else int(part.replace(",", "")) for part in figures[2].split()
                    ]
                    if name.startswith(("Total", "Official")):
                        blocks.append({})
        blocks = [block for block in blocks if block]
        if len(blocks) != len(tables):
            raise ValueError(f"WY {day}: {len(blocks)} tables, expected {len(tables)}")
        for block, spec in zip(blocks, tables, strict=True):
            printed = block.pop("Total", None)
            if len(block) != len(WY_COUNTIES):
                raise ValueError(f"WY {day}: {len(block)} counties")
            for label, offset in spec:
                for side in (0, 1):
                    if (
                        printed is not None
                        and sum(votes[offset + side] for votes in block.values()) != printed[offset + side]
                    ):
                        raise ValueError(f"WY {day} {label}: counties do not sum to {printed[offset + side]}")
                for county, votes in block.items():
                    for answer, count in zip(("YES", "NO"), votes[offset : offset + 2], strict=True):
                        rows.append(
                            {
                                "state": "WY",
                                "day": day,
                                "number": label[-1],
                                "label": label,
                                "url": url,
                                "county": county,
                                "answer": answer,
                                "votes": count,
                            }
                        )
    return rows


def new_york() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (day, number), (label, _, _, stamp, path) in NY_MEASURES.items():
        url = NY.format(stamp, path)
        sheet = fastexcel.read_excel(raw.cached(SOURCE, url).path).load_sheet(0, header_row=None).to_polars()
        counties: dict[str, tuple[int, int]] = {}
        statewide: tuple[int, int] | None = None
        for row in sheet.rows():
            cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip() != ""]
            figures = [int(cell) for cell in cells[1:] if cell.isdigit()]
            if len(cells) < 4 or len(figures) != len(cells) - 1:
                continue
            if cells[0] in ("Statewide Total", "Total Votes by Party"):
                statewide = (figures[0], figures[1])
            elif not cells[0].startswith("Total"):
                counties[cells[0].removesuffix(" County")] = (figures[0], figures[1])
        if len(counties) != 62 or statewide is None:
            raise ValueError(f"{url}: {len(counties)} counties")
        if tuple(sum(votes[side] for votes in counties.values()) for side in (0, 1)) != statewide:
            raise ValueError(f"{url}: counties do not sum to {statewide}")
        for county, votes in counties.items():
            for answer, count in zip(("YES", "NO"), votes, strict=True):
                rows.append(
                    {
                        "state": "NY",
                        "day": day,
                        "number": number,
                        "label": label,
                        "url": url,
                        "county": county,
                        "answer": answer,
                        "votes": count,
                    }
                )
    return rows


def michigan() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in dict.fromkeys(file for file, _ in MI_PROPOSALS):
        url = MI.format(name)
        lines = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace").splitlines()
        header = lines[0].split("\t")
        for line in lines[1:]:
            fields = dict(zip(header, line.split("\t"), strict=False))
            if fields["OfficeCode(Text)"] != "90":
                continue
            number = re.search(r"\d{2}-\d", fields["OfficeDescription"])[0]
            title, kind = MI_PROPOSALS[name, number]
            rows.append(
                {
                    "state": "MI",
                    "day": date.fromisoformat(fields["ElectionDate"]),
                    "number": number,
                    "label": f"Proposal {number}",
                    "url": url,
                    "county": fields["CountyName"].title().replace("Gd.", "Grand"),
                    "answer": fields["CandidateLastName"].upper(),
                    "votes": int(fields["CandidateVotes"]),
                    "title": title,
                    "kind": kind,
                }
            )
    return rows


def nc_2000_pages(index: str) -> list[str]:
    return re.findall(r'href="(general/[^"]+\.htm)"', index)


def north_carolina_early() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = raw.cached(SOURCE, NC_2000 + "staterl.htm").path.read_text(errors="replace")
    pages = nc_2000_pages(index)
    names = dict(re.findall(r'href="(general/[^"]+\.htm)"[^>]*><i>([^<]+)</i>', index))
    if len(pages) != 100:
        raise ValueError(f"NC 2000: {len(pages)} county pages")
    for page in pages:
        text = re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, NC_2000 + page).path.read_text(errors="replace"))
        )
        county = re.search(r"County: ([A-Z .'-]+?) Election Date", text) or re.match("(.+)", names.get(page, ""))
        votes = re.search(
            r"Higher Ed\. Improvement Bonds Against (?:&nbsp; )?([\d,]+)(?: \d+%)? (?:&nbsp; )?For (?:&nbsp; )?([\d,]+)",
            text,
        )
        if county is None or votes is None:
            raise ValueError(f"NC 2000 {page}: bond result not found")
        rows.extend(
            {
                "state": "NC",
                "day": date(2000, 11, 7),
                "number": "1",
                "label": "Measure",
                "url": NC_2000 + page,
                "county": county[1].title().replace("Tyrell", "Tyrrell"),
                "answer": answer,
                "votes": int(votes[at].replace(",", "")),
            }
            for answer, at in (("YES", 2), ("NO", 1))
        )
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, NC_2002).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    for figure in NC_2002_TOTALS:
        if f"{figure:,}" not in text.split("CT OF APPEALS")[0]:
            raise ValueError(f"NC 2002: total {figure:,} not found")
    rows.extend(
        {
            "state": "NC",
            "day": date(2002, 11, 5),
            "number": "1",
            "label": "Amendment 1",
            "url": NC_2002,
            "county": "statewide",
            "answer": answer,
            "votes": NC_2002_TOTALS[at],
        }
        for answer, at in (("YES", 0), ("NO", 1))
    )
    return rows


def north_carolina() -> list[dict[str, object]]:
    roman = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}
    rows: list[dict[str, object]] = []
    for day in NC_DAYS:
        url = NC.format(f"{day:%Y_%m_%d}", f"{day:%Y%m%d}")
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            table = pl.read_csv(
                archive.read(archive.namelist()[0]),
                infer_schema=False,
                encoding="utf8-lossy",
                truncate_ragged_lines=True,
            )
        if day.year == 2004:
            table = table.rename({"contest_name": "contest", "name_on_ballot": "choice", "ballot_count": "total votes"})
        table = table.filter(pl.col("contest").str.to_uppercase().str.contains("CONSTITUTIONAL AMENDMENT"))
        for contest in table["contest"].unique().sort().to_list():
            suffix = contest.upper().removeprefix("NC ").removeprefix("CONSTITUTIONAL AMENDMENT").strip()
            number = roman.get(suffix, "1")
            label = f"Constitutional Amendment {suffix}".strip()
            sums = (
                table.filter(pl.col("contest") == contest)
                .with_columns(votes=pl.col("total votes").cast(pl.Int64))
                .group_by("county", "choice")
                .agg(pl.col("votes").sum())
            )
            if sums["county"].n_unique() != 100:
                raise ValueError(f"{url} {contest}: {sums['county'].n_unique()} counties")
            rows.extend(
                {
                    "state": "NC",
                    "day": day,
                    "number": number,
                    "label": label,
                    "url": url,
                    "county": county.title(),
                    "answer": {"For": "YES", "Against": "NO"}[choice.strip()],
                    "votes": votes,
                }
                for county, choice, votes in sums.iter_rows()
            )
    return rows


def west_virginia() -> list[dict[str, object]]:
    heading = re.compile(r"Amendment\s*#\s*(\d+)")
    line = re.compile(r"^\s*([A-Z][A-Za-z.' ]+?)\s{2,}([\d,]+)\s+([\d,]+)\s*$")
    rows: list[dict[str, object]] = []
    for day, suffix in [*WV_FILES.items(), (date(2005, 6, 25), None)]:
        url = WV_2005 if suffix is None else WV.format(day.year, suffix)
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        number = ""
        counties: dict[str, set[str]] = {}
        official: dict[str, tuple[int, int]] = {}
        start = len(rows)
        for text_line in text.splitlines():
            found = heading.search(text_line)
            if found:
                number = found[1]
                continue
            match = line.match(text_line)
            if match is None or match[1] == "County":
                continue
            if match[1] == "Totals":
                official[number] = (int(match[2].replace(",", "")), int(match[3].replace(",", "")))
                continue
            counties.setdefault(number, set()).add(match[1])
            for answer, votes in (("YES", match[2]), ("NO", match[3])):
                rows.append(
                    {
                        "state": "WV",
                        "day": day,
                        "number": number,
                        "label": f"Amendment {number}",
                        "url": url,
                        "county": match[1],
                        "answer": answer,
                        "votes": int(votes.replace(",", "")),
                    }
                )
        for amendment, names in counties.items():
            if len(names) != 55:
                raise ValueError(f"{url} amendment {amendment}: {len(names)} counties")
        for amendment, totals in official.items():
            sums = tuple(
                sum(row["votes"] for row in rows[start:] if row["number"] == amendment and row["answer"] == answer)
                for answer in ("YES", "NO")
            )
            if sums != totals:
                raise ValueError(f"{url} amendment {amendment}: counties {sums} differ from Totals {totals}")
    return rows


def wisconsin() -> list[dict[str, object]]:
    line = re.compile(r"^\s*([A-Z][A-Za-z. ]+?) County\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")
    final = re.compile(r"Office Totals\s*:\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)")
    rows: list[dict[str, object]] = []
    for day, (stamp, file, titles) in WI_FILES.items():
        url = WI.format(stamp, file)
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        position = 0
        counties: set[str] = set()
        start = len(rows)
        for text_line in text.splitlines():
            match = line.match(text_line)
            if match is not None:
                counties.add(match[1])
                for answer, votes in (("YES", match[3]), ("NO", match[4])):
                    rows.append(
                        {
                            "state": "WI",
                            "day": day,
                            "number": str(position + 1),
                            "label": titles[position],
                            "url": url,
                            "county": match[1],
                            "answer": answer,
                            "votes": int(votes.replace(",", "")),
                        }
                    )
                continue
            total = final.search(text_line)
            if total is None:
                continue
            sums = tuple(
                sum(
                    row["votes"]
                    for row in rows[start:]
                    if row["number"] == str(position + 1) and row["answer"] == answer
                )
                for answer in ("YES", "NO")
            )
            if len(counties) != 72 or sums != (int(total[2].replace(",", "")), int(total[3].replace(",", ""))):
                raise ValueError(
                    f"{url} {titles[position]}: {len(counties)} counties, sums {sums} differ from {total.groups()}"
                )
            counties = set()
            position += 1
    return rows


def iowa() -> list[dict[str, object]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, IA_2008).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    counties: dict[str, tuple[int, int]] = {}
    totals: tuple[int, int] | None = None
    reading = False
    for text_line in text.splitlines():
        if text_line.startswith("Constitutional Amendment - 1"):
            reading = True
        elif reading and (match := re.match(r"^([A-Z][A-Za-z.' ]+?):?\s+(\d+)\s+(\d+)\s+\d+\s*$", text_line)):
            pair = (int(match[2]), int(match[3]))
            if match[1] == "Total":
                totals = pair
                break
            counties[match[1]] = pair
    if len(counties) != 99 or tuple(sum(pair[side] for pair in counties.values()) for side in (0, 1)) != totals:
        raise ValueError(f"{IA_2008}: {len(counties)} counties do not add up to {totals}")
    rows: list[dict[str, object]] = [
        {
            "state": "IA",
            "day": date(2008, 11, 4),
            "number": "1",
            "label": "Constitutional Amendment Question",
            "url": IA_2008,
            "county": county,
            "answer": answer,
            "votes": pair[side],
        }
        for county, pair in counties.items()
        for side, answer in enumerate(("YES", "NO"))
    ]
    table = (
        fastexcel.read_excel(str(raw.cached(SOURCE, IA_2010).path)).load_sheet_by_idx(0, header_row=None).to_polars()
    )
    table = table.rename({column: f"c{index}" for index, column in enumerate(table.columns)})
    for office, number in (("Constitutional Amendment 1", "1"), ("Constitutional Convention Question", "2")):
        sums = (
            table.filter(pl.col("c0") == office, pl.col("c1") != "County")
            .with_columns(yes=pl.col("c3").cast(pl.Int64), no=pl.col("c4").cast(pl.Int64))
            .group_by("c1")
            .agg(pl.col("yes").sum(), pl.col("no").sum())
        )
        if sums.height != 99:
            raise ValueError(f"{IA_2010} {office}: {sums.height} counties")
        rows.extend(
            {
                "state": "IA",
                "day": date(2010, 11, 2),
                "number": number,
                "label": office,
                "url": IA_2010,
                "county": county,
                "answer": answer,
                "votes": votes,
            }
            for county, yes, no in sums.iter_rows()
            for answer, votes in (("YES", yes), ("NO", no))
        )
    return rows


def mississippi() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for number in MS_2011:
        url = MS.format(number)
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        county = ""
        counties: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        for text_line in text.splitlines():
            if match := re.match(r"^(\S.*?)\s+Measure No\. \d+", text_line):
                county = match[1]
            elif match := re.match(r"^\s+Measure No\. \d+", text_line):
                county = ""
            elif match := re.match(r"^\s+(YES|NO)\s+([\d,]+)\s+[\d.]+%", text_line):
                (counties.setdefault(county, {}) if county else totals)[match[1]] = int(match[2].replace(",", ""))
        sums = {answer: sum(votes[answer] for votes in counties.values()) for answer in ("YES", "NO")}
        if len(counties) != 82 or sums != totals:
            raise ValueError(f"{url}: {len(counties)} counties add up to {sums}, not {totals}")
        rows.extend(
            {
                "state": "MS",
                "day": date(2011, 11, 8),
                "number": number,
                "label": f"Initiative Measure {number}",
                "url": url,
                "county": place,
                "answer": answer,
                "votes": votes[answer],
            }
            for place, votes in counties.items()
            for answer in ("YES", "NO")
        )
    return rows


def mississippi_2003() -> list[dict[str, object]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, MS_2003).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    county = ""
    counties: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for text_line in text.splitlines():
        if match := re.match(r"^(\S.*?)\s*$", text_line):
            if not re.match(r"^(MISSISSIPPI|Total Votes|AMENDMENT|COUNTY|State Totals|Wednesday)", match[1]):
                county = match[1]
            elif match[1] == "State Totals":
                county = ""
        if match := re.match(
            r"^\s+Amendment #1 (?:SCR 522|YES|NO).*?(YES|NO)\s+([\d,]+)\s+[\d.]+%", text_line
        ) or re.match(r"^\s+Amendment #1 (YES|NO) Senate Concurrent Res 522\s+([\d,]+)\s+[\d.]+%", text_line):
            (counties.setdefault(county, {}) if county else totals)[match[1]] = int(match[2].replace(",", ""))
    sums = {answer: sum(votes[answer] for votes in counties.values()) for answer in ("YES", "NO")}
    if len(counties) != 82 or sums != totals:
        raise ValueError(f"{MS_2003}: {len(counties)} counties add up to {sums}, not {totals}")
    return [
        {
            "state": "MS",
            "day": date(2003, 11, 4),
            "number": "1",
            "label": "Amendment 1",
            "url": MS_2003,
            "county": place,
            "answer": answer,
            "votes": votes[answer],
        }
        for place, votes in counties.items()
        for answer in ("YES", "NO")
    ]


def kentucky() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, file in KY_FILES.items():
        url = KY_PAGE.format(day.year)
        text = raw.cached(SOURCE, url).path.read_text(errors="replace")
        county = ""
        counties: dict[str, dict[str, tuple[int, int]]] = {}
        for text_line in text.splitlines():
            if match := re.match(r"^\s+([A-Z][A-Z .'-]+?)\s+COUNTY REPORT OF", text_line):
                county = match[1]
            elif match := re.match(r"^\s+Constitutional Amendment\s*#\s*(\d+)\s+([\d,]+)\s+([\d,]+)\s*$", text_line):
                counties.setdefault(match[1], {})[county] = (
                    int(match[2].replace(",", "")),
                    int(match[3].replace(",", "")),
                )
        for number, by_county in counties.items():
            if len(by_county) != 120:
                raise ValueError(f"{file} amendment {number}: {len(by_county)} counties")
            rows.extend(
                {
                    "state": "KY",
                    "day": day,
                    "number": number,
                    "label": f"Constitutional Amendment {number}",
                    "url": url,
                    "county": place.title(),
                    "answer": answer,
                    "votes": pair[side],
                }
                for place, pair in by_county.items()
                for side, answer in enumerate(("YES", "NO"))
            )
    return rows


def tennessee() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, (file, numbers) in TN_FILES.items():
        url = TN_PAGE.format(day.year)
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        figure = r"\s+([\d,]+)" * (2 * len(numbers))
        counties: dict[str, list[int]] = {}
        totals: list[int] = []
        for text_line in text.splitlines():
            if match := re.match(rf"^\s*([A-Z][A-Z .'-]+?){figure}\s*$", text_line):
                values = [int(value.replace(",", "")) for value in match.groups()[1:]]
                if match[1] == "STATEWIDE TOTALS":
                    totals = values
                else:
                    counties[match[1]] = values
        sums = [sum(values[index] for values in counties.values()) for index in range(2 * len(numbers))]
        if len(counties) != 95 or sums != totals:
            raise ValueError(f"{file}: {len(counties)} counties add up to {sums}, not {totals}")
        for index, number in enumerate(numbers):
            rows.extend(
                {
                    "state": "TN",
                    "day": day,
                    "number": number,
                    "label": f"Constitutional Amendment {number}",
                    "url": url,
                    "county": place.title(),
                    "answer": answer,
                    "votes": values[2 * index + side],
                }
                for place, values in counties.items()
                for side, answer in enumerate(("YES", "NO"))
            )
    return rows


def minnesota() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, (file, title) in MN_FILES.items():
        url = MN_PAGE.format(f"{day.year}")
        tallies: dict[str, dict[str, int]] = {}
        for line in raw.cached(SOURCE, url).path.read_text().splitlines():
            cells = line.split(";")
            if len(cells) < 16:
                continue
            side = cells[7].split()[0]
            county = MN_COUNTIES[int(cells[1]) - 1].replace("_", " ")
            sides = tallies.setdefault(county, {})
            sides[side] = int(cells[13])
            sides["total"] = int(cells[15])
            sides["sum"] = sides.get("sum", 0) + int(cells[13])
        if len(tallies) != 87 or any(sides["sum"] != sides["total"] for sides in tallies.values()):
            raise ValueError(f"{file}: {len(tallies)} counties, or sides that do not add up to the printed totals")
        rows.extend(
            {
                "state": "MN",
                "day": day,
                "number": "1",
                "label": "Constitutional Amendment 1",
                "url": url,
                "county": county,
                "answer": answer,
                "votes": sides[answer if answer == "YES" else "NO"],
                "title": title,
                "kind": "Legislative Referendum",
            }
            for county, sides in tallies.items()
            for answer in ("YES", "NO")
        )
    return rows


def kansas() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, file, number, title, printed in KS_2022:
        url = KS + file
        book = fastexcel.read_excel(raw.cached(SOURCE, url).path)
        tallies: dict[str, list[int]] = {}
        for name in book.sheet_names:
            sheet = book.load_sheet(name, header_row=None, dtypes="string").to_polars()
            if name == book.sheet_names[0]:
                for row in sheet.iter_rows():
                    county, candidate, votes = row[0], row[3], row[5]
                    if county in (None, "County"):
                        continue
                    tallies.setdefault(county, [0, 0])[int(candidate.endswith('"NO"'))] += int(votes or 0)
                continue
            total = next(row for row in sheet.iter_rows() if "COUNTY TOTALS" in row)
            tallies[name.title()] = [int(total[-2]), int(total[-1])]
        for index, answer in enumerate(("YES", "NO")):
            if sum(sides[index] for sides in tallies.values()) != printed[index] + KS_GAPS.get(
                (day, number, answer), 0
            ):
                raise ValueError(f"{url} {answer}: counties do not sum to the printed total")
        rows.extend(
            {
                "state": "KS",
                "day": day,
                "number": number,
                "label": f"Constitutional Amendment {number}",
                "url": url,
                "county": county,
                "answer": answer,
                "votes": sides[index],
                "title": title,
                "kind": "Legislative Referendum",
            }
            for county, sides in tallies.items()
            for index, answer in enumerate(("YES", "NO"))
        )
    return rows


def kansas_early() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def sheet(url: str) -> list[tuple[object, ...]]:
        book = fastexcel.read_excel(raw.cached(SOURCE, url).path)
        return list(book.load_sheet(0, header_row=None, dtypes="string").to_polars().iter_rows())

    def add(day: date, number: str, label: str, url: str, tallies: dict[str, list[int]], printed: tuple[int, int]):
        for index in range(2):
            if sum(sides[index] for sides in tallies.values()) != printed[index]:
                raise ValueError(f"{url} {label}: counties do not sum to the printed total")
        rows.extend(
            {
                "state": "KS",
                "day": day,
                "number": number,
                "label": label,
                "url": url,
                "county": county,
                "answer": answer,
                "votes": sides[index],
            }
            for county, sides in tallies.items()
            for index, answer in enumerate(("YES", "NO"))
        )

    lines = sheet(KS_2000)
    totals = next(line for line in lines if line[2] == "District Totals")
    counties = {
        line[0].removesuffix(" County"): [int(value) for value in line[3:7]]
        for line in lines
        if line[0] is not None and line[1] is None and line[2] is None
    }
    if len(counties) != 105:
        raise ValueError(f"{KS_2000}: {len(counties)} counties")
    for position, (number, label) in enumerate((("1", "Chapter 184"), ("2", "Chapter 190"))):
        add(
            date(2000, 11, 7),
            number,
            label,
            KS_2000,
            {county: votes[2 * position : 2 * position + 2] for county, votes in counties.items()},
            (int(totals[3 + 2 * position]), int(totals[4 + 2 * position])),
        )

    tallies = {}
    for line in sheet(KS_2005):
        for column in (0, 4, 8):
            if line[column] is not None and line[column + 1] is not None and line[column + 1].isdigit():
                tallies[line[column].replace("Pottwatomie", "Pottawatomie")] = [
                    int(line[column + 1]),
                    int(line[column + 2]),
                ]
    printed = tallies.pop("TOTAL")
    if len(tallies) != 105:
        raise ValueError(f"{KS_2005}: {len(tallies)} counties")
    add(
        date(2005, 4, 5),
        "",
        "Constitutional Amendment",
        KS_2005,
        tallies,
        (printed[0], printed[1]),
    )

    for number, votes in KS_2010_TOTALS.items():
        add(
            date(2010, 11, 2),
            number,
            f"Constitutional Amendment {number}",
            KS_2010,
            {"statewide": list(votes)},
            votes,
        )
    return rows


def michigan_old() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, year, index, number, title, kind in MI_OLD_PROPOSALS:
        url = MI_OLD.format(year, index)
        text = re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace"))
        found = re.findall(r"^\s*(\d{2}) ([A-Z][A-Z. ]+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$", text, re.MULTILINE)
        total = re.search(r"Totals\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text)
        if len(found) != 83 or total is None:
            raise ValueError(f"{url}: {len(found)} counties")
        for column, answer in ((3, "YES"), (4, "NO")):
            if sum(int(row[column].replace(",", "")) for row in found) != int(total[column - 1].replace(",", "")):
                raise ValueError(f"{url} {answer}: counties do not sum to the printed total")
        rows.extend(
            {
                "state": "MI",
                "day": day,
                "number": number,
                "label": f"Proposal {number}",
                "url": url,
                "county": row[1].title().replace("Gd.", "Grand"),
                "answer": answer,
                "votes": int(row[column].replace(",", "")),
                "title": title,
                "kind": kind,
            }
            for row in found
            for column, answer in ((3, "YES"), (4, "NO"))
        )
    return rows


def utah() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, name in UT_FILES.items():
        url = f"manual://MF-355/{name}"
        path = raw.cached(SOURCE, url).path
        tables: list[tuple[str, str, list[str], list[int]]] = []
        if name.endswith(".xls"):
            sheet = {2008: "Amendments", 2010: "Constitutional Amd.", 2012: "Constitutional Amendments"}[day.year]
            cells = [
                [str(cell).strip() if cell is not None else "" for cell in row]
                for row in fastexcel.read_excel(path)
                .load_sheet_by_name(sheet, header_row=None, dtypes="string")
                .to_polars()
                .iter_rows()
            ]
            body = [
                row
                for row in cells
                if row[0].upper() != "COUNTY" and row[0].replace(",", "").strip() and row[1].isdigit()
            ]
            letters = [re.search(r"Amendment (?:Number )?([A-E])$", label)[1] for label in cells[0][1:] if label]
            names = [row[0] for row in body if row[0].upper() != "TOTAL"]
            total = next(row for row in body if row[0].upper() == "TOTAL")
            for index, letter in enumerate(letters):
                tables.append(
                    (
                        letter,
                        f"{'Amendment' if day.year == 2010 else 'Constitutional Amendment'} {letter}",
                        names,
                        [int(row[4 + 2 * index + side]) for row in [*body[: len(names)], total] for side in (0, 1)],
                    )
                )
        elif day.year == 2000:
            pages = {}
            for page, left in ((13, 56), (14, 0)):
                text = subprocess.run(
                    ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                pages[page] = [
                    [int(match[0]) for match in re.finditer(r"\d+", line) if match.end() >= left]
                    for line in text.splitlines()
                    if not re.search(r"[A-Za-z.]", line) and re.search(r"\d", line)
                ]
            for index, (number, label) in enumerate(
                (("1", "Proposition 1"), ("2", "Proposition 2"), ("A", "Initiative A"), ("B", "Initiative B"))
            ):
                column = [(pages[13], 0), (pages[13], 2), (pages[14], 1), (pages[14], 3)][index]
                against = [(pages[13], 1), (pages[14], 0), (pages[14], 2), (pages[14], 4)][index]
                if len(pages[13]) != 30 or len(pages[14]) != 30:
                    raise ValueError(f"{url}: {len(pages[13])} and {len(pages[14])} rows")
                tables.append(
                    (
                        number,
                        label,
                        list(UT_COUNTIES),
                        [
                            value
                            for yes, no in zip(column[0], against[0], strict=True)
                            for value in (yes[column[1]], no[against[1]])
                        ],
                    )
                )
        else:
            lines = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            fence = 0
            for start, line in enumerate(lines):
                if re.search(r"(?i)^\s*percent", line):
                    fence = start
                if not line.startswith("Beaver"):
                    continue
                measures = re.findall(r"(Amendment|Initiative)\s+(?:Number\s+)?(\d+)", " ".join(lines[fence:start]))
                block = []
                for row in lines[start:]:
                    match = re.match(r"\s*([A-Za-z]+(?: [A-Za-z]+)?)\s+([\d,%. ]+)$", row)
                    if match is None:
                        break
                    block.append(
                        (match[1], [int(value.replace(",", "")) for value in match[2].split() if "%" not in value])
                    )
                    if match[1].upper() == "TOTAL":
                        break
                if not measures or block[-1][0].upper() != "TOTAL":
                    continue
                for index, (kind, number) in enumerate(measures):
                    tables.append(
                        (
                            number,
                            f"Constitutional Amendment {number}" if kind == "Amendment" else f"Initiative {number}",
                            [county for county, _ in block[:-1]],
                            [figures[2 + 2 * index + side] for _, figures in block for side in (0, 1)],
                        )
                    )
        for number, label, names, values in tables:
            votes = dict(zip([*names, "TOTAL"], zip(values[0::2], values[1::2], strict=True), strict=True))
            total = votes.pop("TOTAL")
            if (
                list(votes) != list(UT_COUNTIES)
                or sum(yes for yes, _ in votes.values()) != total[0]
                or sum(no for _, no in votes.values()) != total[1]
            ):
                raise ValueError(f"{url} {label}: counties do not sum to {total}")
            rows.extend(
                {
                    "state": "UT",
                    "day": day,
                    "number": number,
                    "label": label,
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, pair in votes.items()
                for answer, value in zip(("YES", "NO"), pair, strict=True)
            )
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, UT_2007).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    table = {
        match[1]: (int(match[2].replace(",", "")), int(match[3].replace(",", "")))
        for line in text.splitlines()
        if (match := re.match(r"(\w[\w ]*?)\s+[\d,]+\s+[\d,]+\s+[\d.]+%\s+([\d,]+)\s+([\d,]+)$", line.strip()))
    }
    printed = table.pop("TOTAL")
    if (
        list(table) != list(UT_COUNTIES)
        or tuple(sum(votes[side] for votes in table.values()) for side in (0, 1)) != printed
    ):
        raise ValueError("UT 2007 referendum counties do not sum to the printed total")
    for county, votes in table.items():
        for answer, count in zip(("YES", "NO"), votes, strict=True):
            rows.append(
                {
                    "state": "UT",
                    "day": date(2007, 11, 6),
                    "number": "1",
                    "label": "Referendum 1",
                    "url": UT_2007,
                    "county": county,
                    "answer": answer,
                    "votes": count,
                    "title": "Referendum on House Bill 148, the education voucher law (Parent Choice in Education Act)",
                    "kind": "Popular Referendum",
                }
            )
    return rows


def north_dakota() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    sheets = {
        **{day: (ND.format(day.isoformat()), count, None) for day, count in ND_DAYS.items()},
        **{day: (ND_ABSTRACTS + name + ".pdf", count, None) for day, (name, count) in ND_PRIMARIES.items()},
        **{day: (ND_ABSTRACTS + name + ".pdf", count, at) for day, (name, count, at) in ND_OLD.items()},
    }
    for day, (url, count, at) in sheets.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\n")
        table: dict[str, list[int]] = {}
        if at is None:
            start = max(i for i, line in enumerate(lines) if re.search(r"MEASURES\s+BALLOT", line))
        else:
            starts = [i for i, line in enumerate(lines) if re.match(r"[\s|]*Adams\s+\d", line)]
            start = starts[at]
        for line in lines[start:]:
            if figures := re.fullmatch(r"\s*([A-Za-z][A-Za-z .]+?)\s+(\d[\d,. ]*)", re.sub(r"[|\[\]_]", " ", line)):
                numbers = [int(re.sub(r"\D", "", part)) for part in figures[2].split() if re.sub(r"\D", "", part)]
                if len(numbers) < 2 * count:
                    continue
                if figures[1].strip() == "Total":
                    total = numbers[-2 * count :]
                    break
                table[figures[1].strip().replace("Mclntosh", "McIntosh")] = numbers[-2 * count :]
        else:
            raise ValueError(f"ND {day}: no Total row")
        if len(table) != 53:
            raise ValueError(f"ND {day}: {len(table)} counties")
        for column in range(2 * count):
            if sum(votes[column] for votes in table.values()) != total[column]:
                raise ValueError(f"ND {day}: column {column} does not add up to {total[column]}")
        rows.extend(
            {
                "state": "ND",
                "day": day,
                "number": str(measure + 1),
                "label": f"Measure No. {measure + 1}",
                "url": url,
                "county": county,
                "answer": answer,
                "votes": votes[2 * measure + side],
            }
            for measure in range(count)
            for county, votes in table.items()
            for side, answer in enumerate(("YES", "NO"))
        )
    day, url, totals = ND_2004_PRIMARY
    url += ".pdf"
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    for measure, pair in enumerate(totals):
        for figure in pair:
            if f"{figure:,}" not in text:
                raise ValueError(f"ND {day}: total {figure:,} not found in the abstract")
        rows.extend(
            {
                "state": "ND",
                "day": day,
                "number": str(measure + 1),
                "label": f"Measure No. {measure + 1}",
                "url": url,
                "county": "statewide",
                "answer": answer,
                "votes": pair[side],
            }
            for side, answer in enumerate(("YES", "NO"))
        )
    return rows


def hawaii() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for year, measures in HI_MEASURES.items():
        found: dict[str, dict[str, tuple[list[int], str]]] = {}
        for code, county in HI_COUNTIES.items():
            url = HI.format(year, code)
            xml = subprocess.run(
                ["pdftotext", "-bbox", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for page in xml.split("<page")[1:]:
                words: list[tuple[float, float, float, float, str]] = []
                for m in re.finditer(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', page
                ):
                    text = html.unescape(m[5])
                    step = (float(m[3]) - float(m[1])) / len(text)
                    for part in re.finditer(r"[^│├┤╞╡═─┼╪┬┴╤╧╔╗╚╝║╒╕╛╘╤]+", text):
                        words.append(
                            (
                                float(m[1]) + step * part.start(),
                                float(m[2]),
                                float(m[1]) + step * part.end(),
                                float(m[4]),
                                part[0],
                            )
                        )
                for at, (x, y, _, _, text) in enumerate(words):
                    if (
                        text.upper() not in ("AMEND:", "AMENDMENT:", "CON:")
                        or at == 0
                        or words[at - 1][4].upper() not in ("CON", "CONSTITUTIONAL")
                    ):
                        continue
                    answers = {
                        word[4]: word
                        for word in sorted(words, key=lambda word: word[1], reverse=True)
                        if word[4] in ("YES", "NO") and 0 <= word[1] - y <= 70 and abs(word[0] - x) <= 90
                    }
                    if len(answers) != 2:
                        raise ValueError(f"HI {year} {county}: no answers under the header at {x}, {y}")
                    answer_top = min(word[1] for word in answers.values())
                    head = " ".join(
                        word[4]
                        for word in sorted(words, key=lambda word: (round(word[1]), word[0]))
                        if x - 3 <= word[0] < x + 150 and y - 1 <= word[1] < answer_top
                    )
                    matched = [name for name in measures if name.lower() in head.lower()]
                    if len(matched) != 1:
                        raise ValueError(f"HI {year} {county}: cannot place {head!r}")
                    votes: list[int] = []
                    for answer in ("YES", "NO"):
                        marker = answers[answer]
                        numbers = sorted(
                            (
                                word
                                for word in words
                                if re.fullmatch(r"\d[\d,]*", word[4])
                                and abs(word[1] - marker[1]) <= 6
                                and marker[2] - 1 <= word[0] < marker[2] + 260
                            ),
                            key=lambda word: (word[0], abs(word[1] - marker[1])),
                        )
                        if not numbers:
                            raise ValueError(f"HI {year} {county} {matched[0]}: no {answer} figure")
                        votes.append(int(numbers[0][4].replace(",", "")))
                    if county in found.setdefault(matched[0], {}):
                        raise ValueError(f"HI {year} {county}: {matched[0]} appears twice")
                    found[matched[0]][county] = (votes, url)
        for name, (number, label) in measures.items():
            if len(found.get(name, {})) != len(HI_COUNTIES):
                raise ValueError(f"HI {year} {name}: counties {sorted(found.get(name, {}))}")
            rows.extend(
                {
                    "state": "HI",
                    "day": HI_DAYS[year],
                    "number": number,
                    "label": label,
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": votes[side],
                }
                for county, (votes, url) in found[name].items()
                for side, answer in enumerate(("YES", "NO"))
            )
    return rows


def new_mexico() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in NM_PAGES.read_text().splitlines()[1:]:
        year, url = line.split("\t")
        text = re.sub(
            r"\s+",
            " ",
            re.sub(
                r"<[^>]+>",
                " ",
                re.sub(
                    r"<script.*?</script>|<style.*?</style>",
                    "",
                    raw.cached(SOURCE, url).path.read_text(errors="replace"),
                    flags=re.DOTALL,
                ),
            ).replace("&nbsp;", " "),
        )
        county = re.search(r"([A-Z][A-Z. ]+?) County Official", text)
        if county is None:
            raise ValueError(f"NM {url}: no county name")
        figures = re.findall(
            r"(?:(\d)(?:ST|ND|RD|TH) CONSTITUTIONAL AMENDMENT|GO BOND ([A-E])) ([\d,]+) (\d+)% \(For\) ([\d,]+) (\d+)% \(Against\)",
            text,
        )
        if len(figures) != (5 if year == "2000" else 14):
            raise ValueError(f"NM {url}: {len(figures)} measures")
        for amendment, bond, yes_text, yes_percent, no_text, no_percent in figures:
            votes = [int(yes_text.replace(",", "")), int(no_text.replace(",", ""))]
            if (
                abs(100 * votes[0] / sum(votes) - int(yes_percent)) > 1
                or abs(100 * votes[1] / sum(votes) - int(no_percent)) > 1
            ):
                raise ValueError(f"NM {url} {amendment or bond}: figures do not match the printed percents")
            rows.extend(
                {
                    "state": "NM",
                    "day": NM_DAYS[int(year)],
                    "number": amendment,
                    "label": f"Constitutional Amendment {amendment}" if amendment else f"Bond Question {bond}",
                    "url": url,
                    "county": county[1]
                    .title()
                    .replace("Dona Ana", "Doña Ana")
                    .replace("Mckinley", "McKinley")
                    .replace("De Baca", "De Baca"),
                    "answer": answer,
                    "votes": votes[side],
                }
                for side, answer in enumerate(("YES", "NO"))
            )
    return rows


def montana() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, name in MT_FILES.items():
        url = MT + name
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\n")
        tables: list[tuple[list[str], dict[str, list[int]], list[int]]] = []
        if day.year == 2010:
            titles = {"CONSTITUTIONAL CONVENTION": "CC", "CONSTITUTIONAL INITIATIVE": "CI", "INITIATIVE": "I"}
            for index, line in enumerate(lines):
                if (
                    found := re.match(r"^\s*(CONSTITUTIONAL CONVENTION|CONSTITUTIONAL INITIATIVE|INITIATIVE)\b", line)
                ) and (number := re.search(r"NO\.\s*(\d+)", " ".join(lines[index : index + 3]))):
                    tables.append(([f"{titles[found[1]]}-{number[1]}"], {}, []))
                    for follow in lines[index + 1 :]:
                        if figures := re.fullmatch(r"\s*(.+?)\s{2,}([\d,]+)\s+([\d,]+)", follow):
                            counts = [int(figures[2].replace(",", "")), int(figures[3].replace(",", ""))]
                            if figures[1] == "Total":
                                tables[-1][2].extend(counts)
                                break
                            tables[-1][1][figures[1]] = counts
        else:
            pattern = re.compile(r"\b(?:LR|IR|CI|CC|C|I)-\d+\b|HB 474")
            heads = [
                list(dict.fromkeys("IR-117" if text == "HB 474" else text for text in pattern.findall(line)))
                for line in lines
            ]
            index = max(range(len(lines)), key=lambda at: len(heads[at]))
            ids = heads[index]
            width = 2 * len(ids)
            table: dict[str, list[int]] = {}
            total: list[int] = []
            for follow in lines[index + 1 :]:
                figures = re.findall(r"\d[\d,]*", follow)
                if "%" in follow or len(figures) < width:
                    continue
                counts = [int(figure.replace(",", "")) for figure in figures[-width:]]
                label = re.match(r"^\s*([A-Za-z][A-Za-z.&' ]*?)\s{2,}", follow)
                if label is None or label[1] == "Total":
                    total = counts
                else:
                    table[label[1].replace("&", "and")] = counts
                if len(table) == 56 and total:
                    break
            tables = [
                ([id], {c: v[2 * at : 2 * at + 2] for c, v in table.items()}, total[2 * at : 2 * at + 2])
                for at, id in enumerate(ids)
            ]
        if not tables:
            raise ValueError(f"MT {day}: no tables")
        for ids, table, total in tables:
            summed = [sum(votes[side] for votes in table.values()) for side in (0, 1)]
            if len(table) != 56 or summed != total:
                raise ValueError(f"MT {day} {ids[0]}: {len(table)} counties add up to {summed}, not {total}")
            rows.extend(
                {
                    "state": "MT",
                    "day": day,
                    "number": re.sub(r"\D", "", ids[0]),
                    "label": ids[0],
                    "url": url,
                    "county": county.replace("&", "and"),
                    "answer": answer,
                    "votes": votes[side],
                }
                for county, votes in table.items()
                for side, answer in enumerate(("YES", "NO"))
            )
    return rows


def georgia() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, pages in GA_PAGES.items():
        for stamp, page, label in pages:
            url = GA.format(stamp, page)
            text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
            text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
            tokens = [
                found
                for cell in re.split(r"<[^>]+>", text)
                if (found := re.sub(r"\s+", " ", html.unescape(cell)).strip())
            ]
            start = tokens.index("County")
            names, votes = [], []
            for token in tokens[start + 1 :]:
                if token not in ("PR", "TP", "PPR") and re.fullmatch(r"[A-Z][A-Z .'-]+", token):
                    names.append(token)
                    votes.append([])
                elif names and re.fullmatch(r"[\d,]+", token):
                    votes[-1].append(int(token.replace(",", "")))
                elif token.startswith(("Totals", "Official")):
                    break
            for name, cells in zip(names, votes, strict=True):
                yes, no, total = cells[-3:]
                if yes + no != total:
                    raise ValueError(f"{url} {name}: {yes} + {no} is not {total}")
                for answer, count in (("YES", yes), ("NO", no)):
                    rows.append(
                        {
                            "state": "GA",
                            "day": day,
                            "number": page,
                            "label": label,
                            "url": url,
                            "county": name.title(),
                            "answer": answer,
                            "votes": count,
                        }
                    )
    for day, (stamp, page) in GA_TOTALS.items():
        url = GA.format(stamp, page)
        text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
        if day.year == 2006:
            found = re.findall(
                r"(?:Constitutional Amendments|Statewide Referendums), .*?100% of precincts reporting Votes Percentage "
                r"Yes \(Non-Partisan\) ([\d,]+) [\d.]+% No \(Non-Partisan\) ([\d,]+)",
                text,
            )
            labels = GA_2006
        else:
            found = re.findall(
                r"(AMENDMENT #\d|REFERENDUM [A-E]) - YES ([\d,]+) [\d.]+ % \1 - NO ([\d,]+)",
                text,
            )
            labels = tuple(
                f"Constitutional Amendment {name[-1]}" if name.startswith("AMENDMENT") else f"Referendum {name[-1]}"
                for name, _, _ in found
            )
            found = [(yes, no) for _, yes, no in found]
        if len(found) != len(labels):
            raise ValueError(f"{url}: {len(found)} questions for {len(labels)} labels")
        for label, (yes, no) in zip(labels, found, strict=True):
            for answer, count in (("YES", yes), ("NO", no)):
                rows.append(
                    {
                        "state": "GA",
                        "day": day,
                        "number": f"{page}:{label}",
                        "label": label,
                        "url": url,
                        "county": "statewide",
                        "answer": answer,
                        "votes": int(count.replace(",", "")),
                    }
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
            *oregon_history(),
            *washington_history(),
            *florida(),
            *oklahoma(),
            *ohio(),
            *ohio_issues(),
            *rhode_island(),
            *nevada(),
            *georgia(),
            *alaska(),
            *new_jersey(),
            *maryland(),
            *nebraska(),
            *arkansas(),
            *illinois(),
            *indiana(),
            *south_dakota(),
            *missouri(),
            *montana(),
            *north_dakota(),
            *new_york(),
            *michigan(),
            *michigan_old(),
            *kansas(),
            *kansas_early(),
            *minnesota(),
            *north_carolina(),
            *north_carolina_early(),
            *west_virginia(),
            *wisconsin(),
            *iowa(),
            *mississippi(),
            *mississippi_2003(),
            *kentucky(),
            *tennessee(),
            *utah(),
            *wyoming(),
            *south_carolina(),
            *south_carolina_2022(),
            *alabama_2022(),
            *hawaii(),
            *new_mexico(),
        ],
        infer_schema_length=None,
    )
    words = re.compile(r"[a-z]{4,}")
    matched: list[dict[str, object]] = []
    for (state, day, number, label), group in frame.group_by("state", "day", "number", "label", maintain_order=True):
        if group["title"][0] is not None:
            matched.append(
                {
                    "state": state,
                    "day": day,
                    "number": number,
                    "label": label,
                    "measure": label,
                    "title": group["title"][0],
                    "type": group["kind"][0],
                    "passed": group.filter(pl.col("answer") == "YES")["votes"].sum()
                    > group.filter(pl.col("answer") == "NO")["votes"].sum(),
                }
            )
            continue
        if state == "NY" or (state, day.year) in (
            ("SD", 2012),
            ("AZ", 2022),
            ("WA", 2012),
            ("WA", 2022),
            ("LA", 2022),
            ("OR", 2022),
            ("UT", 2012),
            ("MD", 2022),
            ("SC", 2022),
            ("AL", 2022),
        ):
            title, kind, passed = (
                (NY_MEASURES[day, number][1], "Legislative Referendum", NY_MEASURES[day, number][2])
                if state == "NY"
                else (
                    DERIVED_HAND[day, number][1],
                    DERIVED_HAND[day, number][2],
                    group.filter(pl.col("answer") == "YES")["votes"].sum()
                    > group.filter(pl.col("answer") == "NO")["votes"].sum(),
                )
                if state in ("WA", "LA", "OR", "UT", "MD")
                else {"SD": SD_2012, "SC": SC_2022, "AL": AL_2022}.get(state, AZ_2022)[number]
            )
            matched.append(
                {
                    "state": state,
                    "day": day,
                    "number": number,
                    "label": label,
                    "measure": label or f"Proposition {number}",
                    "title": title,
                    "type": kind,
                    "passed": passed,
                }
            )
            continue
        mine = set(words.findall(label.lower()))
        pool = listed.filter(pl.col("state") == state, pl.col("year") == day.year)
        kind = (pl.col("election") == "General") == (day.month == 11)
        found = next(
            (
                rows
                for rows in (
                    pool.filter(kind, pl.col("measure") == label),
                    pool.filter(kind, pl.col("number") == number),
                    pool.filter(kind, pl.lit(state) == "SD", pl.col("measure").str.ends_with(f" {number}")),
                    pool.filter(kind, pl.col("number").is_null()),
                    pool.filter(pl.col("number") == number),
                    pool.filter(pl.col("number").is_null()),
                )
                if rows.height
            ),
            pool.filter(kind) if label else pool.clear(),
        )
        if found.height != 1 or found["number"][0] is None and label and found["measure"][0] != label:
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
                if state in ("ME", "MD", "NV", "RI")
                else f"Public Question {number}"
                if state == "NJ"
                else entry["measure"]
                if entry["number"] or state in ("SD", "MO", "SC", "HI", "NM", "GA", "AK", "WY", "UT", "IA", "NC")
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
            summary="Statewide ballot measures by county, 2000–2024, from state election offices' official returns",
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
                    {
                        "FL": fl_sos.SOURCE,
                        "RI": ri_countbook.SOURCE,
                        "AK": ak_sovc.SOURCE if "MF-266" in url else SOURCE,
                    }.get(state, gov_counties.SOURCE if url == gov_counties.OH_2006 else SOURCE),
                    url,
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
                    else fips[row["state"]]
                    if row["county"] == "statewide"
                    else row["county"]
                    if row["state"] == "AK" or row["county"].isdigit()
                    else counties.get((fips[row["state"]], re.sub(r"[^a-z]", "", row["county"].lower())))
                ),
                return_dtype=pl.String,
            )
        )
        missing = frame.filter(pl.col("code").is_null()).select("state", "county").unique()
        if missing.height:
            raise ValueError(f"unmatched counties: {missing.rows()}")
        for code in frame.filter(pl.col("code").str.contains(":"), pl.col("state") != "AK")["code"].unique():
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
                   where x.scheme = 'us.election' and e.date_from between '2000-01-01' and '2026-12-31'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        paths = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election' and x.value ~ '^20[0-2][0-9]-'"""
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
                    for key in (
                        f"{day}:{state}:referendum",
                        f"{day}:{state}:special",
                        f"{day}:{state}",
                        f"{day}:{state}:primary",
                    )
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
            preliminary = state == "GA" and day.year == 2010 and entry["number"] != "2010_1102/09000"
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary" if preliminary else "certified",
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
                + (SCANNED if rows["page"].is_not_null().any() and state != "AL" else "")
                + (GA_PRELIMINARY if preliminary else "")
                + (
                    (
                        AL_2007_NOTE
                        if state == "AL"
                        else AK_STATEWIDE_NOTE
                        if state == "AK"
                        else ND_2004_NOTE
                        if state == "ND"
                        else KS_2010_NOTE
                        if state == "KS"
                        else STATEWIDE_ONLY
                    )
                    if (rows["county"] == "statewide").any()
                    else ""
                ),
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
                    unit = cs.unit(
                        "us.ak_house_district"
                        if row["state"] == "AK" and row["county"] != "statewide"
                        else "us.aggregate"
                        if ":" in row["code"]
                        else "us.state"
                        if row["county"] == "statewide"
                        else "us.county",
                        row["code"],
                    )
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
