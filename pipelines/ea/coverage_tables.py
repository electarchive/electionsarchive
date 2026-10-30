import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from difflib import SequenceMatcher

import polars as pl
from psycopg.types.json import Json

from ea import coverage, coverage_countries, raw, readiness, statewide_table
from ea.sources.us.clerk import LEGISLATORS
from ea.store import ROOT, connect

LOCK = 7_302_019
STATE_LEVELS = (
    "president",
    "us_senate",
    "us_house",
    "governor",
    "statewide_exec",
    "judicial",
    "state_upper",
    "state_lower",
    "statewide_measure",
)
BALANCES = {
    "3011390": "3011397",
    "4752004": "4752006",
    "1303436": "1303440",
    "2148003": "2148006",
    "2028410": "2028412",
}
OFFSETS = {"us_house": 2, "state_lower": 2, "us_senate": 6}
JUDICIAL = {
    **dict.fromkeys(
        [
            "AL",
            "AK",
            "AZ",
            "AR",
            "CA",
            "CO",
            "FL",
            "GA",
            "ID",
            "IL",
            "IN",
            "IA",
            "KS",
            "KY",
            "LA",
            "MD",
            "MI",
            "MN",
            "MS",
            "MO",
            "MT",
            "NE",
            "NV",
            "NM",
            "NC",
            "ND",
            "OH",
            "OK",
            "OR",
            "SD",
            "TN",
            "TX",
            "UT",
            "WA",
            "WV",
            "WY",
        ],
        2,
    ),
    "PA": 2,
    "WI": 1,
}
KNOWN = {("GA", 2025, "statewide_exec", "general"): 2}
SWITCHED = {("MI", "county"): (2024, (4, 0))}
HELD_IN = {("FL", "school"): 8, ("TN", "school"): 8}
CANCELED = {
    "06037": (
        "Los Angeles County Registrar-Recorder/County Clerk, tentative list of canceled elections for November 3, 2026 (appointment in lieu of election due to insufficiency of candidates), https://content.lavote.gov/docs/rrcc/documents/canceled-elections-november-2026-tentative.pdf",
        [
            "El Rancho",
            "El Segundo",
            "Manhattan Beach",
            "San Marino",
            "Walnut Valley",
            "West Covina",
            "El Monte City",
            "Gorman Joint",
            "Hawthorne",
            "Hermosa Beach City",
            "Rosemead",
        ],
    ),
}
DISTRICT_CALENDAR = {
    ("MD", "Anne Arundel County Public Schools"): (
        4,
        0,
        11,
        "Maryland State Board of Elections, Anne Arundel County sample general ballot for November 3, 2026: no Board of Education contest appears, and the state's 2026 general candidate list carries none, https://www.elections.maryland.gov/elections/2026/general_ballots/AnneArundel.pdf",
    ),
    ("TX", "Killeen Independent School District"): (
        1,
        0,
        5,
        "Killeen ISD election results page: the May 2, 2026 trustee election covered Places 4 and 5, https://www.killeenisd.org/article/2861919",
    ),
    ("TX", "Galena Park Independent School District"): (
        1,
        0,
        5,
        "Galena Park ISD elections page: trustee elections are held each year on the May uniform election date, with 2026 results dated May 2, 2026, https://www.galenaparkisd.com/district-info/gpisd-elections",
    ),
    ("TX", "Tyler"): (
        1,
        0,
        5,
        "City of Tyler City Clerk elections page: Mayor and Council are elected in May, even-numbered districts in even years and odd-numbered districts in odd years, with a May 2, 2026 general election, https://www.cityoftyler.org/City-Government/Mayor-and-Council/City-Clerk/Elections",
    ),
    ("TX", "Midland"): (
        3,
        2,
        11,
        "City of Midland: the next scheduled City Council elections are set for November 2027, https://golf.midlandtexas.gov/CivicAlerts.asp?AID=1843",
    ),
    ("GA", "Athens-Clarke County"): (
        4,
        2,
        5,
        "Athens-Clarke County elections page: local elections were held May 19, 2026 with a June 16 runoff, and the November 3 ballot carries only a special election for Board of Education District 8, https://www.accgov.com/elections",
    ),
    ("MN", "Anoka-Hennepin Sch Dist 11"): (
        2,
        1,
        11,
        "Anoka-Hennepin School District school board page: Districts 1, 2, and 5 four-year terms expire in 2027, so the district elects in odd-numbered years, https://www.ahschools.us/schoolboard",
    ),
    ("TX", "Katy Independent School District"): (
        1,
        0,
        5,
        "Katy ISD Board election page and canvass: trustee Positions 3, 4, and 5 were decided at the May 2, 2026 election, certified May 11, https://www.katyisd.org/board/election/home",
    ),
    ("OK", "Tulsa County"): (
        4,
        2,
        6,
        "NonDoc, Tulsa County candidate filing: 6 of 20 county offices contested, 14 elected by default, and the District 1 commissioner and treasurer races decided in the June 16, 2026 Republican primary, https://nondoc.com/2026/04/03/tulsa-county-candidate-filing-6-elections-set-14-elected-by-default/",
    ),
    ("GA", "Gwinnett County School District"): (
        2,
        0,
        5,
        "Gwinnett County Board of Registrations and Elections: Board of Education Districts 2 and 4 qualify for the nonpartisan general election held with the May 19, 2026 general primary (runoffs June 16 and December 1), https://www.gwinnettcounty.com/government/departments/elections/running-for-office",
    ),
    ("NC", "Charlotte-Mecklenburg Schools"): (
        2,
        1,
        11,
        "North Carolina Session Law 1993-167: Charlotte-Mecklenburg Board of Education members serve four-year terms and are elected in odd-numbered years, the three at-large seats from 1995 and the six district seats from 1997, so the six district seats were on the ballot on November 4, 2025, https://www.ncleg.gov/EnactedLegislation/SessionLaws/HTML/1993-1994/SL1993-167.html",
    ),
    ("TX", "Cypress-Fairbanks Independent School District"): (
        2,
        1,
        11,
        "Cypress-Fairbanks ISD board elections page: next trustee election November 2, 2027, https://www.cfisd.net/our-district/board-of-trustees/board-elections",
    ),
    ("NJ", "Union City"): (
        4,
        2,
        5,
        "City of Union City public notice: nonpartisan municipal election Tuesday, May 12, 2026, five commissioners for four-year terms, https://www.ucnj.com/News/View/2532/public-notice",
    ),
    ("NJ", "North Bergen"): (
        4,
        3,
        5,
        "Township of North Bergen, FAQ on the mayoral transition: next municipal election May 11, 2027, https://www.northbergen.org/News/View/12009/frequently-asked-questions-on-the-mayoral-transition",
    ),
    ("NY", "Buffalo"): (
        4,
        1,
        11,
        "Wikipedia, 2025 Buffalo mayoral election (Common Council and mayor elected November 4, 2025 for four-year terms; Buffalo City Charter), https://en.wikipedia.org/wiki/2025_Buffalo_mayoral_election",
    ),
    ("NJ", "Newark City School System"): (
        1,
        0,
        4,
        "Chalkbeat Newark: four board seats filled at the April 21, 2026 school election, https://www.chalkbeat.org/newark/2026/03/17/voters-choose-between-nine-candidates-four-seats-board-of-education-election-april/",
    ),
    ("PA", "Crawford County"): (
        4,
        3,
        11,
        "Crawford County commissioners (three seats, four-year terms) were elected November 7, 2023; County Commissioners Association of Pennsylvania, https://ccapweb.pacounties.org/getattachment/11a99fb1-af07-42d4-8b1f-16967730f5aa/Commissioner-Elections-2023G-FINAL.pdf",
    ),
    ("ME", "Biddeford"): (
        2,
        1,
        11,
        "Biddeford mayor and council elected in November of odd years; mayor LaFountain elected November 4, 2025, term to December 31, 2027, https://spectrumlocalnews.com/me/maine/politics/2025/11/05/biddeford-chooses-new-mayor--passes-bonds-for-sewer-and-recreation-projects",
    ),
    ("NJ", "Edison"): (
        4,
        1,
        11,
        "Edison mayor and township council elected together in November of odd years on four-year terms, most recently November 4, 2025, https://www.aol.com/articles/heres-where-edison-mayor-township-131513200.html",
    ),
    ("NJ", "Woodbridge"): (
        4,
        3,
        11,
        "Woodbridge mayor elected to a four-year term November 7, 2023; at-large council seat runs to December 31, 2027, https://www.ballotready.org/people/kyle-anderson",
    ),
    ("NJ", "Old Bridge Township"): (
        2,
        1,
        11,
        "Old Bridge mayor elected for four years November 7, 2023 (https://newbrunswicktoday.com/2023/11/a-dozen-mayor-elections-in-county-but-almost-half-are-uncontested/); ward council seats elected in the offset odd years, https://newjerseyglobe.com/local/merwin-to-face-henry-in-old-bridge/",
    ),
    ("MA", "Berkshire County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 2000; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Essex County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 1999; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Franklin County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 1997; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Hampden County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 2000; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Hampshire County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 1999; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Middlesex County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 1997; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Suffolk County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 1999; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
    ("MA", "Worcester County"): (
        6,
        4,
        11,
        "Massachusetts abolished county government in 1998; the county sheriff, register of probate, and register of deeds remain elected on a six-year cycle (M.G.L. c. 37 § 1; sheriffs last elected 2022), https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter37/Section1",
    ),
}
CALENDAR: dict[tuple[str, str], tuple[int, int, str | None] | tuple[int, int, str | None, int]] = {
    ("AK", "county"): (1, 0, "Alaska Stat. § 29.26.040, https://ak.elaws.us/as/29.26.040"),
    ("AK", "municipality"): (1, 0, "Alaska Stat. § 29.26.040, https://ak.elaws.us/as/29.26.040"),
    ("AK", "school"): (
        1,
        0,
        "Alaska Division of Elections, REAA elections: held every year on the first Tuesday in October, https://www.elections.alaska.gov/reaa/",
    ),
    ("AL", "county"): (2, 0, None),
    ("AL", "municipality"): (
        4,
        0,
        "Alabama Code § 11-46-21(a) (2000 to 2020; from 2025 the cycle is 2025, 2029), https://law.justia.com/codes/alabama/title-11/title-2/chapter-46/article-2/section-11-46-21/",
    ),
    ("AL", "school"): (2, 0, None),
    ("AR", "county"): (
        2,
        0,
        "Ark. Code Ann. § 7-5-102, https://law.justia.com/codes/arkansas/title-7/chapter-5/subchapter-1/section-7-5-102/",
    ),
    ("AR", "municipality"): (
        2,
        0,
        "Ark. Code Ann. §§ 14-44-103, 14-44-105, 14-44-115; § 14-43-201, https://www.armunileague.org/wp-content/uploads/2024/07/Municipal-Law-in-Arkansas-FAQ_2021_11_WEB.pdf",
    ),
    ("AR", "school"): (
        1,
        0,
        "Ark. Code Ann. § 6-14-102(a)(1)(A) (as amended by Act 503 of 2025), https://law.justia.com/codes/arkansas/2017/title-6/subtitle-2/chapter-14/section-6-14-102-d-1/",
    ),
    ("AZ", "county"): (
        4,
        0,
        "Ariz. Rev. Stat. § 11-211(A); Ariz. Const. art. 12, § 3, https://www.azleg.gov/ars/11/00211.htm",
    ),
    ("AZ", "school"): (
        2,
        0,
        "Ariz. Rev. Stat. § 15-424(A); Ariz. Const. art. 7, § 11, https://www.azleg.gov/ars/15/00424.htm",
    ),
    ("CA", "county"): (
        2,
        0,
        "Cal. Gov. Code § 24200; Cal. Elec. Code § 1300, https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?lawCode=GOV&division=2.&title=3.&part=1.&chapter=5.&article=",
    ),
    ("CA", "municipality"): (
        2,
        0,
        "Cal. Elec. Code §§ 1000, 1301, https://california.public.law/codes/elections_code_section_1301",
    ),
    ("CA", "school"): (
        2,
        0,
        "Cal. Elec. Code § 1302(a), (b), https://codes.findlaw.com/ca/elections-code/elec-sect-1302/",
    ),
    ("CA", "special"): (
        2,
        0,
        "Cal. Elec. Code § 1303(a), (b), https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?article=&chapter=4.&division=1.&lawCode=ELEC&part=&title=",
    ),
    ("CO", "county"): (
        2,
        0,
        "Colo. Rev. Stat. § 1-1-104(17), https://content.leg.colorado.gov/sites/default/files/r26-925-special-district-information-and-elections-accessible.pdf",
    ),
    ("CO", "school"): (2, 1, "Colo. Rev. Stat. § 22-31-104(1), https://colorado.public.law/statutes/crs_22-31-104"),
    ("CO", "special"): (
        2,
        1,
        "Colo. Rev. Stat. § 1-13.5-111(1), https://colorado.public.law/statutes/crs%5F1-13.5-111",
    ),
    ("CT", "municipality"): (
        2,
        1,
        "Conn. Gen. Stat. § 9-164(a)(1)(A), https://www.cga.ct.gov/current/pub/chap_146.htm",
    ),
    ("CT", "school"): (
        2,
        1,
        "Conn. Gen. Stat. §§ 10-46(a), (b); 9-164, https://www.cga.ct.gov/2005/rpt/2005-R-0097.htm",
    ),
    ("CT", "township"): (2, 1, "Conn. Gen. Stat. § 9-164(a)(1)(A), https://www.cga.ct.gov/current/pub/chap_146.htm"),
    ("DE", "municipality"): (1, 0, None),
    ("DE", "school"): (1, 0, "Del. Code tit. 14, § 1072, https://delcode.delaware.gov/title14/c010/sc04/"),
    ("FL", "county"): (2, 0, "Fla. Stat. § 100.041(2)(a), https://flsenate.gov/Laws/Statutes/2026/100.041"),
    ("FL", "school"): (
        2,
        0,
        "Fla. Stat. §§ 100.041(3)(a), (b); 100.031, https://flsenate.gov/Laws/Statutes/2026/100.041",
    ),
    ("FL", "special"): (
        2,
        0,
        "Fla. Stat. § 100.031; § 189.04, https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0100-0199%2F0100%2FSections%2F0100.031.html",
    ),
    ("GA", "county"): (2, 0, None),
    ("GA", "municipality"): (2, 1, "Ga. Code Ann. § 21-2-9(c), (d), https://georgiacommons.org/code/21-2-9"),
    ("GA", "school"): (
        2,
        0,
        "Ga. Code Ann. § 21-2-139 (nonpartisan school board elections at the May general primary unless local law says otherwise), https://law.justia.com/codes/georgia/title-21/chapter-2/article-4/section-21-2-139/",
        5,
    ),
    ("HI", "county"): (
        2,
        0,
        "Hawaii Office of Elections, primary and general elections (HRS ch. 11), https://elections.hawaii.gov/elections-in-hawaii/",
    ),
    ("IA", "county"): (2, 0, "Iowa Code §§ 39.1, 39.18, https://www.legis.iowa.gov/docs/code/39.pdf"),
    ("IA", "municipality"): (2, 1, "Iowa Code § 376.1(1), https://www.legis.iowa.gov/docs/code/376.pdf"),
    ("IA", "school"): (2, 1, "Iowa Code § 277.1, https://www.legis.iowa.gov/docs/code/277.1.pdf"),
    ("ID", "county"): (
        2,
        0,
        "Idaho Secretary of State, general elections; Idaho Code title 31 (county commissioners, not read), https://voteidaho.gov/election-information/",
    ),
    ("ID", "municipality"): (
        2,
        1,
        "Idaho Code § 50-405(1), https://legislature.idaho.gov/statutesrules/idstat/title50/t50ch4/sect50-405/",
    ),
    ("ID", "school"): (
        2,
        1,
        "Idaho Code § 33-503(1), https://legislature.idaho.gov/statutesrules/idstat/title33/t33ch5/sect33-503/",
    ),
    ("IL", "county"): (
        2,
        0,
        "10 ILCS 5/2A-1.2(a)(5), https://ilga.gov/documents/legislation/ilcs/documents/001000050K2A-1.2.htm",
    ),
    ("IL", "municipality"): (
        2,
        1,
        "10 ILCS 5/2A-1.1(b); 10 ILCS 5/2A-1.2(c)(1), https://www.ilga.gov/documents/legislation/ilcs/documents/001000050K2A-1.1.htm",
    ),
    ("IL", "school"): (
        2,
        1,
        "10 ILCS 5/2A-1.1(b); 10 ILCS 5/2A-1.2, https://elections.il.gov/agencyforms/1%20ELECTION%20CALENDAR/2025%20Election%20Calendar%20Issued%2007292024.pdf",
    ),
    ("IL", "special"): (2, 1, None),
    ("IL", "township"): (4, 1, None),
    ("IN", "county"): (
        2,
        0,
        "Ind. Code § 3-10 (general election, even years); county officers per Indiana Election Division 2026 calendar, https://www.in.gov/sos/elections/files/2026-Election-Calendar-Election-Administrators-Edition-Revised-March-2026.pdf",
    ),
    ("IN", "municipality"): (
        4,
        3,
        "Ind. Code § 3-10-6-5, https://law.justia.com/codes/indiana/title-3/article-10/chapter-6/section-3-10-6-5/",
    ),
    ("IN", "school"): (
        2,
        0,
        "Ind. Code § 20-23-4-29.1(b), https://law.justia.com/codes/indiana/title-20/article-23/chapter-4/section-20-23-4-29-1/",
    ),
    ("IN", "township"): (
        4,
        2,
        "Ind. Code §§ 3-10-2-13, 36-6-4-2, 36-6-6-2, https://www.in.gov/sboa/files/Township-Manual-Chapter-2.pdf",
    ),
    ("KS", "county"): (
        2,
        0,
        "Kan. Stat. Ann. §§ 19-202(c), (d); 19-204 (general elections in November of even-numbered years), https://ksrevisor.gov/statutes/chapters/ch19/019_002_0002.html",
    ),
    ("KS", "municipality"): (
        2,
        1,
        "Kan. Stat. Ann. § 25-21a01(a), (h), https://kslegislature.gov/b2025_26/laws/025_000_0000_chapter/025_021a_0000_article/025_021a_0001_section/025_021a_0001_k",
    ),
    ("KS", "school"): (
        2,
        1,
        "Kan. Stat. Ann. § 25-2010, https://www.ksrevisor.gov/statutes/chapters/ch25/025_020_0010.html",
    ),
    ("KS", "township"): (
        2,
        0,
        "Kan. Stat. Ann. § 25-1601, https://www.ksrevisor.gov/statutes/chapters/ch25/025_016_0001.html",
    ),
    ("KY", "county"): (
        4,
        2,
        "Ky. Const. § 99; KRS 67.700; 2026 State Board of Elections calendar, https://legislature.ky.gov/LRC/Publications/Informational%20Bulletins/ib115.pdf",
    ),
    ("KY", "municipality"): (
        2,
        0,
        "Ky. Const. § 167, https://apps.legislature.ky.gov/Law/Constitution/Constitution/ViewConstitution?rsn=193",
    ),
    ("KY", "school"): (
        2,
        0,
        "Ky. Rev. Stat. § 160.200(1), (2), https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=56916",
    ),
    ("LA", "county"): (4, 3, "La. Rev. Stat. § 33:1221(A), https://legis.la.gov/legis/Law.aspx?d=88684"),
    ("LA", "municipality"): (
        4,
        1,
        "La. Rev. Stat. § 33:383(A)(1); § 18:402(D)(1), https://www.legis.la.gov/Legis/Law.aspx?d=90382",
    ),
    ("LA", "school"): (4, 2, "La. Rev. Stat. § 17:52(A)(1), (A)(2), https://legis.la.gov/legis/Law.aspx?d=81132"),
    ("MA", "municipality"): (
        2,
        1,
        "Mass. Gen. Laws ch. 43, §§ 15(a)-(c), 109, https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter43/Section109",
    ),
    ("MA", "school"): (
        1,
        0,
        "Mass. Gen. Laws ch. 41, § 1, https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter41/Section1",
    ),
    ("MA", "special"): (1, 0, None),
    ("MA", "township"): (
        1,
        0,
        "Mass. Gen. Laws ch. 41, § 1, https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter41/section1",
    ),
    ("MD", "county"): (
        2,
        0,
        "Md. Code, Elec. Law § 8-301 (general elections), https://msa.maryland.gov/msa/mdmanual/41electp/html/forth.html",
    ),
    ("MD", "school"): (
        2,
        0,
        "Md. Code, Elec. Law § 8-301 (general elections), https://msa.maryland.gov/msa/mdmanual/41electp/html/forth.html",
    ),
    ("ME", "municipality"): (
        1,
        0,
        "30-A M.R.S. § 2525 (Annual meeting), https://legislature.maine.gov/statutes/30-a/title30-Asec2525.html",
    ),
    ("ME", "school"): (
        1,
        0,
        "30-A M.R.S. § 2525(1)(C); 20-A M.R.S. § 1653, https://legislature.maine.gov/statutes/30-a/title30-Asec2525.html",
    ),
    ("ME", "special"): (1, 0, None),
    ("ME", "township"): (
        1,
        0,
        "30-A M.R.S. § 2525 (Annual meeting), https://legislature.maine.gov/statutes/30-a/title30-Asec2525.html",
    ),
    ("MI", "county"): (
        2,
        0,
        "Mich. Const. art. II, § 5, https://www.legislature.mi.gov/Laws/MCL?objectName=mcl-Article-II-5",
    ),
    ("MI", "municipality"): (2, 1, "MCL 168.642(1)(a), https://www.legislature.mi.gov/Laws/MCL?objectName=MCL-168-642"),
    ("MI", "school"): (2, 0, None),
    ("MI", "township"): (4, 0, None),
    ("MN", "county"): (
        2,
        0,
        "Minn. Stat. § 204D.03 and § 204B.09 (state general election); SOS county guide, https://www.sos.mn.gov/media/4251/county-election-administration-guide.pdf",
    ),
    ("MN", "municipality"): (
        2,
        0,
        "Minn. Stat. § 205.07, subd. 1, https://www.revisor.mn.gov/statutes/2025/cite/205.07",
    ),
    ("MN", "school"): (2, 0, "Minn. Stat. § 205A.04, subd. 1, https://www.revisor.mn.gov/statutes/cite/205A.04"),
    ("MN", "township"): (
        1,
        0,
        "Minn. Stat. § 205.075, subd. 1 (second Tuesday in March); § 365.51, subd. 3, https://www.revisor.mn.gov/statutes/cite/205.075",
        3,
    ),
    ("MO", "county"): (2, 0, "RSMo § 115.121.1, https://revisor.mo.gov/main/OneSection.aspx?section=115.121"),
    ("MO", "municipality"): (1, 0, "RSMo § 115.121.3, https://revisor.mo.gov/main/OneSection.aspx?section=115.121"),
    ("MO", "school"): (1, 0, "RSMo § 115.121.3, https://revisor.mo.gov/main/OneSection.aspx?section=115.121"),
    ("MS", "county"): (
        4,
        3,
        "Miss. Code Ann. § 23-15-213(2) (county election commissioners); Miss. Const. § 102 and Code §§ 19-3-1, 23-15-193 (supervisors), https://law.justia.com/codes/mississippi/title-23/chapter-15/article-7/section-23-15-213/",
    ),
    ("MS", "municipality"): (
        4,
        1,
        "Miss. Code Ann. § 23-15-173(1), https://www.sos.ms.gov/content/documents/Elections/2021/2021%20Municipal%20Elections%20Overview%20(Full%20Page).pdf",
    ),
    ("MT", "county"): (
        2,
        0,
        "Mont. Code Ann. § 13-1-104(2), https://mca.legmt.gov/bills/MCA/title_0130/chapter_0010/part_0010/section_0040/0130-0010-0010-0040.html",
    ),
    ("MT", "municipality"): (
        2,
        1,
        "Mont. Code Ann. § 13-1-104(3), https://mca.legmt.gov/bills/mca/title_0130/chapter_0010/part_0010/section_0040/0130-0010-0010-0040.html",
    ),
    ("MT", "school"): (
        1,
        0,
        "Mont. Code Ann. § 20-20-105(1), https://mca.legmt.gov/bills/2015/mca/20/20/20-20-105.htm",
    ),
    ("NC", "county"): (
        2,
        0,
        "N.C. Gen. Stat. § 163-1(c) (statewide general elections, even years); county commissioners per NCpedia, https://www.ncpedia.org/article/board-county-commissioners",
    ),
    ("NC", "municipality"): (
        2,
        1,
        "N.C. Gen. Stat. § 163-279(a), https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_163/GS_163-279.html",
    ),
    ("NC", "school"): (
        2,
        0,
        "N.C. Gen. Stat. § 115C-37(a), https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_115C/GS_115C-37.html",
    ),
    ("CA", "Soil and water conservation"): (
        2,
        0,
        "Cal. Pub. Res. Code § 9314, https://www.conservation.ca.gov/dlrp/RCD/Documents/DIVISION%209.pdf",
    ),
    ("FL", "Soil and water conservation"): (
        2,
        0,
        "Fla. Stat. § 582.18, https://www.flsenate.gov/Laws/Statutes/2025/582.18",
    ),
    ("IA", "Soil and water conservation"): (
        2,
        0,
        "Iowa Code § 161A.5, https://www.legis.iowa.gov/docs/code/2026/161A.5.pdf",
    ),
    ("ID", "Soil and water conservation"): (
        2,
        0,
        "Idaho Code § 22-2721, https://swc.idaho.gov/wp-content/uploads/2025/01/ELECTION-GUIDANCE-DOC.pdf",
    ),
    ("KY", "Soil and water conservation"): (
        2,
        0,
        "KRS 262.210, https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=12731",
    ),
    ("MN", "Soil and water conservation"): (
        2,
        0,
        "Minn. Stat. § 103C.305, https://www.revisor.mn.gov/statutes/cite/103C.305",
    ),
    ("MT", "Soil and water conservation"): (
        2,
        0,
        "MCA § 76-15-305, https://leg.mt.gov/bills/mca/title_0760/chapter_0150/part_0030/section_0050/0760-0150-0030-0050.html",
    ),
    ("ND", "Soil and water conservation"): (2, 0, "N.D.C.C. § 4.1-20-18, https://ndlegis.gov/cencode/t04-1c20.pdf"),
    ("NE", "Soil and water conservation"): (
        2,
        0,
        "Neb. Rev. Stat. § 32-513, https://www.nebraskalegislature.gov/laws/statutes.php?statute=32-513",
    ),
    ("NV", "Soil and water conservation"): (2, 0, "NRS 548.285, https://www.leg.state.nv.us/nrs/nrs-548.html"),
    ("OR", "Soil and water conservation"): (
        2,
        0,
        "ORS 568.560, https://www.oregonlegislature.gov/bills_laws/ors/ors568.html",
    ),
    ("SC", "Soil and water conservation"): (
        2,
        0,
        "S.C. Code § 48-9-1220, https://www.scstatehouse.gov/code/t48c009.php",
    ),
    ("SD", "Soil and water conservation"): (2, 0, "SDCL 38-8-39, https://sdlegislature.gov/Statutes/38-8-39"),
    ("VA", "Soil and water conservation"): (
        4,
        3,
        "Va. Code § 10.1-523(D), https://law.lis.virginia.gov/vacode/title10.1/chapter5/section10.1-523/",
    ),
    ("WV", "Soil and water conservation"): (2, 0, "W. Va. Code § 19-21A-6, https://code.wvlegislature.gov/19-21A-6/"),
    ("WY", "Soil and water conservation"): (
        2,
        0,
        "W.S. §§ 11-16-120, 22-2-101(a)(ix), https://wyoleg.gov/statutes/compress/title11.pdf",
    ),
    ("NC", "Soil and water conservation"): (
        2,
        0,
        "N.C. Gen. Stat. § 139-6, https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_139/GS_139-6.html",
    ),
    ("ND", "county"): (2, 0, None),
    ("ND", "municipality"): (2, 0, "N.D. Cent. Code § 40-21-02, https://ndlegis.gov/cencode/t40c21.pdf"),
    ("ND", "school"): (
        1,
        0,
        "N.D. Cent. Code § 15.1-09-22(1), https://codes.findlaw.com/nd/title-15-1-elementary-and-secondary-education/nd-cent-code-sect-15-1-09-22/",
    ),
    ("ND", "township"): (1, 0, "N.D. Cent. Code § 58-05-02, https://ndlegis.gov/cencode/t58c05.pdf"),
    ("NE", "county"): (2, 0, None),
    ("NE", "municipality"): (2, 0, None),
    ("NE", "school"): (
        2,
        0,
        "Neb. Rev. Stat. § 32-543(2) (Class I, II, III); § 32-544 (Class IV, city election); § 32-545 (Class V), https://nebraskalegislature.gov/laws/statutes.php?statute=32-543",
    ),
    ("NE", "special"): (
        2,
        0,
        "Neb. Rev. Stat. § 32-404(3), https://nebraskalegislature.gov/laws/statutes.php?statute=32-404",
    ),
    ("NE", "township"): (2, 0, None),
    ("NH", "municipality"): (2, 1, None),
    ("NH", "school"): (1, 0, None),
    ("NH", "special"): (1, 0, None),
    ("NH", "township"): (1, 0, "N.H. RSA 669:1, I, https://gc.nh.gov/rsa/html/LXIII/669/669-1.htm"),
    ("NJ", "county"): (1, 0, None),
    ("NJ", "municipality"): (
        1,
        0,
        "N.J. Stat. § 40:45-1, https://law.justia.com/codes/new-jersey/title-40/section-40-45-1/",
    ),
    ("NJ", "school"): (1, 0, "N.J. Stat. § 19:60-1(a), https://pub.njleg.state.nj.us/Bills/2018/PL18/20_.HTM"),
    ("NJ", "special"): (1, 0, None),
    ("NJ", "township"): (
        1,
        0,
        "N.J. Stat. § 40:45-1, https://law.justia.com/codes/new-jersey/title-40/section-40-45-1/",
    ),
    ("NM", "county"): (2, 0, None),
    ("NM", "municipality"): (
        2,
        1,
        "N.M. Stat. § 1-22-3, https://codes.findlaw.com/nm/chapter-1-elections/nm-st-sect-1-22-3/",
    ),
    ("NM", "school"): (
        2,
        1,
        "N.M. Stat. § 1-22-3, https://codes.findlaw.com/nm/chapter-1-elections/nm-st-sect-1-22-3/",
    ),
    ("NV", "county"): (
        2,
        0,
        "Nev. Rev. Stat. § 293.12755 (general election), https://www.leg.state.nv.us/nrs/nrs-293.html",
    ),
    ("NV", "municipality"): (
        2,
        0,
        "Nev. Rev. Stat. §§ 293C.140, 293C.145, https://www.leg.state.nv.us/nrs/NRS-293C.html",
    ),
    ("NV", "school"): (2, 0, None),
    ("NY", "municipality"): (1, 0, None),
    ("NY", "school"): (
        1,
        0,
        "N.Y. Educ. Law § 2002(1) (third Tuesday in May); § 2601-a(2), https://www.nysenate.gov/legislation/laws/EDN/2002",
        5,
    ),
    ("NY", "special"): (1, 0, None),
    ("NY", "village"): (
        1,
        0,
        "N.Y. Elec. Law § 15-104(1)(a) (third Tuesday in March, unless a village moves it to June or November), https://www.nysenate.gov/legislation/laws/ELN/15-104",
        3,
    ),
    ("NY", "township"): (
        2,
        1,
        "N.Y. Town Law § 80 (odd years; L. 2023, ch. 741 moves town elections to even years from 2026), https://assembly.state.ny.us/leg/?Text=Y&bn=A8822&default_fld=&leg_video=&term=2023",
    ),
    ("OH", "county"): (
        2,
        0,
        "Ohio Rev. Code § 3501.02(C); Ohio Const. art. XVII, § 1, https://codes.ohio.gov/ohio-revised-code/section-3501.02",
    ),
    ("OH", "municipality"): (
        2,
        1,
        "Ohio Rev. Code § 3501.02(D); § 3501.01(B), https://codes.ohio.gov/ohio-revised-code/section-3501.02",
    ),
    ("OH", "school"): (2, 1, "Ohio Rev. Code § 3501.02(D), https://codes.ohio.gov/ohio-revised-code/section-3501.02"),
    ("OH", "township"): (2, 1, "Ohio Rev. Code § 3501.02(D), https://codes.ohio.gov/ohio-revised-code/section-3501.02"),
    ("OK", "county"): (
        2,
        0,
        "Okla. Stat. tit. 19, § 19-131(A)-(C); Okla. Const. art. XVII, http://ok.elaws.us/os/19-131",
    ),
    ("OK", "school"): (
        1,
        0,
        "Okla. Stat. tit. 26, § 13A-103(A)(1), https://www.oscn.net/applications/oscn/DeliverDocument.asp?CiteID=78700",
    ),
    ("OR", "county"): (
        2,
        0,
        "Or. Rev. Stat. § 254.056(1), https://www.oregonlegislature.gov/bills_laws/ors/ors254.html",
    ),
    ("OR", "municipality"): (
        2,
        0,
        "Or. Rev. Stat. § 254.035(2), https://www.oregonlegislature.gov/bills_laws/ors/ors254.html",
    ),
    ("OR", "school"): (
        2,
        1,
        "Or. Rev. Stat. § 255.335(1), https://www.oregonlegislature.gov/bills_laws/ors/ors255.html",
    ),
    ("OR", "special"): (
        2,
        1,
        "Or. Rev. Stat. § 255.335(1); § 255.345, https://www.oregonlegislature.gov/bills_laws/ors/ors255.html",
    ),
    ("PA", "county"): (
        2,
        1,
        "25 P.S. § 2752, https://codes.findlaw.com/pa/title-25-ps-elections-electoral-districts/pa-st-sect-25-2752/",
    ),
    ("PA", "municipality"): (
        2,
        1,
        "25 P.S. § 2752, https://codes.findlaw.com/pa/title-25-ps-elections-electoral-districts/pa-st-sect-25-2752/",
    ),
    ("PA", "school"): (
        2,
        1,
        "25 P.S. § 2752, https://codes.findlaw.com/pa/title-25-ps-elections-electoral-districts/pa-st-sect-25-2752/",
    ),
    ("PA", "township"): (
        2,
        1,
        "25 P.S. § 2752, https://codes.findlaw.com/pa/title-25-ps-elections-electoral-districts/pa-st-sect-25-2752/",
    ),
    ("RI", "municipality"): (
        2,
        0,
        "R.I. Gen. Laws § 17-18-5; § 16-2-5, https://webserver.rilegislature.gov/statutes/TITLE17/17-18/17-18-5.htm",
    ),
    ("RI", "school"): (
        2,
        0,
        "R.I. Gen. Laws § 16-2-5; § 17-18-5, https://webserver.rilegislature.gov/Statutes/TITLE16/16-2/16-2-5.htm",
    ),
    ("RI", "township"): (
        2,
        0,
        "R.I. Gen. Laws § 17-18-5, https://webserver.rilegislature.gov/statutes/TITLE17/17-18/17-18-5.htm",
    ),
    ("SC", "county"): (
        2,
        0,
        "S.C. Code § 7-13-10; § 7-13-20, https://www.scstatehouse.gov/Archives/CodeofLaws2018/t07c013.php",
    ),
    ("SC", "school"): (
        2,
        0,
        "S.C. Code § 7-13-15(B)(2)(c); § 7-13-10, https://www.scstatehouse.gov/Archives/CodeofLaws2018/t07c013.php",
    ),
    ("SD", "county"): (2, 0, "S.D. Codified Laws § 7-8-1, https://sdlegislature.gov/Statutes/7-8"),
    ("SD", "municipality"): (1, 0, "S.D. Codified Laws §§ 9-13-1, 9-13-1.3, https://sdlegislature.gov/Statutes/9-13"),
    ("SD", "school"): (1, 0, "S.D. Codified Laws § 13-7-10, https://sdlegislature.gov/Statutes/13-7"),
    ("SD", "township"): (1, 0, "S.D. Codified Laws § 8-3-12; ch. 8-3, https://sdlegislature.gov/Statutes/8-3-12"),
    ("TN", "county"): (2, 0, "T.C.A. § 2-3-202, https://www.ctas.tennessee.edu/eli/dates-regular-elections"),
    ("TN", "school"): (
        2,
        0,
        "T.C.A. § 49-2-201(a)(1), (d); § 2-3-202, https://law.justia.com/codes/tennessee/title-49/chapter-2/part-2/section-49-2-201/",
    ),
    ("TX", "county"): (2, 0, "Tex. Elec. Code § 41.002, https://statutes.capitol.texas.gov/Docs/EL/htm/EL.41.htm"),
    ("TX", "municipality"): (1, 0, None),
    ("TX", "special"): (
        2,
        0,
        "Tex. Elec. Code § 41.001(a)(2)–(3); § 41.0052, https://www.sos.texas.gov/elections/laws/changing-election-dates-local-political-subdivisions.shtml",
    ),
    ("UT", "county"): (
        2,
        0,
        "Utah Code § 20A-1-201(1), (2)(a)(vi), https://le.utah.gov/xcode/Title20A/Chapter1/20A-1-S201.html?v=C20A-1-S201_2023022720230227",
    ),
    ("UT", "municipality"): (
        2,
        1,
        "Utah Code § 20A-1-202(1), https://le.utah.gov/xcode/Title20A/Chapter1/C20A-1-S202_2024050220240621.pdf",
    ),
    ("UT", "school"): (
        2,
        0,
        "Utah Code § 20A-1-201(2)(a)(vii)–(viii); § 20A-14-202, https://le.utah.gov/xcode/Title20A/Chapter1/20A-1-S201.html?v=C20A-1-S201_2023022720230227",
    ),
    ("VA", "county"): (
        4,
        3,
        "Va. Code § 24.2-218, https://law.lis.virginia.gov/vacodefull/title24.2/chapter2/article5/",
    ),
    ("VA", "municipality"): (2, 0, None),
    ("VA", "school"): (
        2,
        1,
        "Va. Code § 24.2-223, https://law.lis.virginia.gov/vacodefull/title24.2/chapter2/article5/",
    ),
    ("VT", "municipality"): (
        1,
        0,
        "17 V.S.A. § 2640(a), https://legislature.vermont.gov/statutes/section/17/055/02640",
    ),
    ("VT", "school"): (1, 0, "16 V.S.A. § 423(a), https://legislature.vermont.gov/statutes/fullchapter/16/009"),
    ("VT", "special"): (1, 0, None),
    ("VT", "township"): (1, 0, "17 V.S.A. § 2640(a), https://legislature.vermont.gov/statutes/section/17/055/02640"),
    ("WA", "county"): (
        2,
        0,
        "RCW 29A.04.321(1); RCW 36.16.010, https://app.leg.wa.gov/rcw/default.aspx?cite=29a.04.330",
    ),
    ("WA", "municipality"): (2, 1, "RCW 29A.04.330(1), https://app.leg.wa.gov/rcw/default.aspx?cite=29a.04.330"),
    ("WA", "school"): (2, 1, "RCW 29A.04.330(1), https://app.leg.wa.gov/rcw/default.aspx?cite=29a.04.330"),
    ("WA", "special"): (2, 1, "RCW 29A.04.330(1)(b), https://app.leg.wa.gov/rcw/default.aspx?cite=29a.04.330"),
    ("WI", "county"): (
        2,
        0,
        "Wis. Stat. § 59.10(3)(d), https://docs.legis.wisconsin.gov/scroll/up/274/statutes/statutes/59",
    ),
    ("WI", "municipality"): (1, 0, None),
    ("WI", "school"): (
        1,
        0,
        "Wis. Stat. § 120.06(3), (7)(b), https://docs.legis.wisconsin.gov/statutes/statutes/120/i/06/9",
    ),
    ("WI", "township"): (
        2,
        1,
        "Wis. Stat. § 60.30(1)(a), (4)(a), https://law.justia.com/codes/wisconsin/chapter-60/section-60-30/",
    ),
    ("WV", "county"): (2, 0, "W. Va. Code § 3-1-31(a), https://code.wvlegislature.gov/pdf/3-1-31/"),
    ("WV", "school"): (2, 0, "W. Va. Code § 18-5-1b, https://code.wvlegislature.gov/18-5-1b/"),
    ("WY", "county"): (
        2,
        0,
        "Wyo. Stat. §§ 22-2-104(a), 22-2-105(a)(ii), https://wyoleg.gov/statutes/compress/title22.pdf",
    ),
    ("WY", "municipality"): (2, 0, None),
    ("WY", "school"): (2, 0, "Wyo. Stat. § 22-2-104(e), https://wyoleg.gov/statutes/compress/title22.pdf"),
}
ACRONYMS = {"MUD", "ISD", "CISD", "PUD", "USD", "CSD", "WCID", "GCD", "CDD", "SID", "MSD", "UD", "LID", "FWSD"}
ABBREVIATIONS = {
    "SCH": "SCHOOL",
    "SCHS": "SCHOOL",
    "SCHOOLS": "SCHOOL",
    "SCHL": "SCHOOL",
    "PUB": "PUBLIC",
    "HGHTS": "HEIGHTS",
    "HGTS": "HEIGHTS",
    "DIST": "DISTRICT",
    "DISTRICTS": "DISTRICT",
    "DST": "DISTRICT",
    "UNIF": "UNIFIED",
    "UNI": "UNIFIED",
    "UN": "UNION",
    "ELEM": "ELEMENTARY",
    "EL": "ELEMENTARY",
    "JT": "JOINT",
    "VAL": "VALLEY",
    "VLY": "VALLEY",
    "CO": "COUNTY",
    "CNTY": "COUNTY",
    "TWP": "TOWNSHIP",
    "BORO": "BOROUGH",
    "SPGS": "SPRINGS",
    "TWNSHP": "TOWNSHIP",
    "HS": "HIGH",
    "COM": "COMMUNITY",
    "COMM": "COMMUNITY",
    "CMTY": "COMMUNITY",
    "CMNTY": "COMMUNITY",
    "CONS": "CONSOLIDATED",
    "CONSOL": "CONSOLIDATED",
    "MT": "MOUNT",
    "ST": "SAINT",
    "FT": "FORT",
    "CTR": "CENTER",
    "REG": "REGIONAL",
    "RGNL": "REGIONAL",
    "HOSP": "HOSPITAL",
    "PK": "PARK",
    "REC": "RECREATION",
    "WTR": "WATER",
    "IMPR": "IMPROVEMENT",
    "IMP": "IMPROVEMENT",
    "AUTH": "AUTHORITY",
    "INDEP": "INDEPENDENT",
    "IND": "INDEPENDENT",
    "N": "NORTH",
    "S": "SOUTH",
    "E": "EAST",
    "W": "WEST",
    "CENT": "CENTRAL",
    "CEN": "CENTRAL",
    "CTRL": "CENTRAL",
    "ISD": "INDEPENDENT",
    "CISD": "CONSOLIDATED INDEPENDENT",
    "USD": "UNIFIED",
    "CSD": "COMMUNITY",
    "MUD": "MUNICIPAL UTILITY",
    "PUD": "PUBLIC UTILITY",
    "FPD": "FIRE",
    "WCID": "WATER CONTROL IMPROVEMENT",
    "CDD": "COMMUNITY DEVELOPMENT",
    "GCD": "GROUNDWATER CONSERVATION",
}
STOP = {
    "SCHOOL",
    "DISTRICT",
    "PUBLIC",
    "PROTECTION",
    "OF",
    "THE",
    "NO",
    "NUMBER",
    "AND",
    "SYSTEM",
    "CORPORATION",
    "CORP",
}
PREFIX = re.compile(
    r"^((?:CHARTER )?TOWNSHIP|CITY AND COUNTY|CITY|TOWN|VILLAGE|BOROUGH|COUNTY|PLANTATION|"
    r"(?:METRO|METROPOLITAN|UNIFIED|CONSOLIDATED) GOVERNMENT) OF (.+)$"
)
COLLEGE = re.compile(
    r"^(?!.*\b(?:SCH|SCHOOLS?|ISD|ACADEMY)\b).*(?:COLLEGE|CMTY COL|JUNIOR COL|UNIVERSITY)", re.IGNORECASE
)

LOCAL_SQL = """
create temp table lc as
select coalesce(o.polity_id, b.polity_id, e.polity_id) as polity_id, cc.id, cc.year,
    extract(month from e.date_from)::int as month,
    cc.level <> 'local_measure' and cc.stage in ('general', 'primary', 'runoff') as regular, cc.stage,
    coalesce(cg.precinct, false) or cc.tacit as precinct, cc.upcoming, coalesce(rdy.ok, false) as ready
from cc
left join rdy on rdy.id = cc.id
join contest c on c.id = cc.id
join election e on e.id = c.election_id
left join office o on o.id = c.office_id
left join body b on b.id = c.body_id
left join cg on cg.contest_id = cc.id
where cc.level in ('county', 'municipal', 'school', 'special_district', 'local_measure')
"""

HELD_SQL = """
select lc.polity_id, lc.year, count(*) filter (where not lc.upcoming),
    count(*) filter (where lc.precinct and not lc.upcoming), count(*) filter (where lc.regular),
    mode() within group (order by lc.month) filter (where lc.regular),
    count(*) filter (where lc.regular and lc.stage = 'general'),
    count(*) filter (where lc.upcoming), count(*) filter (where lc.upcoming and lc.ready)
from lc group by 1, 2
"""

LOCAL_SHAPES_SQL = """
select lc.polity_id, lc.year, count(distinct ug.k), count(distinct ug.k) filter (where ug.shaped)
from lc join rs on rs.contest_id = lc.id join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
where ug.grain = 'precinct'
group by 1, 2
"""

SHAPES_SQL = """
create temp table sg as
select cc.id, cc.st, cc.year, cc.level, e.date_from as d,
    case when cc.level in ('us_house', 'state_upper', 'state_lower') then c.district_area_id
        else coalesce(c.district_area_id, (select pt.area_id from polity_territory pt
            where pt.polity_id = e.polity_id and pt.valid @> e.date_from limit 1)) end as area_id
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
where cc.st <> 'US' and cc.level not in ('county', 'municipal', 'school', 'special_district', 'local_measure');

analyze sg;

create temp table su as
select sg.id, bool_and(u.area_relation = 'equals'
        and exists (select from area_geometry g where g.area_id = u.area_id and g.valid @> sg.d)) as shaped
from sg
join result_set r on r.contest_id = sg.id and r.is_default
join rsu on rsu.result_set_k = r.k
join reporting_unit u on u.k = rsu.unit_k
    and u.scheme_code in ('us.county', 'us.former_county', 'us.town', 'us.ak_house_district')
group by sg.id;

analyze su;
"""

SHAPES_COUNT_SQL = """
select sg.st, sg.year, sg.level, count(*),
    count(*) filter (where t.shape = 'complete'),
    count(*) filter (where t.shape = 'unshaped'),
    count(*) filter (where t.shape = 'units'),
    count(*) filter (where t.shape = 'undrawn')
from sg left join su on su.id = sg.id
cross join lateral (select case
    when sg.area_id is null or not exists (select from area_geometry g where g.area_id = sg.area_id) then 'undrawn'
    when not exists (select from area_geometry g where g.area_id = sg.area_id and g.valid @> sg.d) then 'unshaped'
    when not coalesce(su.shaped, true) then 'units'
    else 'complete' end as shape) t
group by 1, 2, 3
"""

POLITIES_SQL = """
select p.id, p.kind::text, e.value, n.text, ps.st
from polity p
join name n on n.entity_id = p.id and n.is_preferred
left join external_id e on e.entity_id = p.id and e.scheme = 'us.polity'
left join pstate ps on ps.id = p.id
where p.id in (select id from uspol) and p.kind not in ('sovereign_state', 'federal_unit', 'region')
order by p.id, e.value, n.text
"""


TURNOUT_SQL = """
with m as (
    select r.election_id, bool_or(cm.measure = 2) as registered, bool_or(cm.measure in (3, 14)) as ballots,
        bool_or(cm.measure in (24, 25)) as survey
    from result_set r join coverage_cache_measure cm on cm.result_set_k = r.k
    where r.contest_id is null and r.status = 'active'
    group by 1
), days as (
    select substr(p.iso_code, 4) as st, e.date_from, e.kind::text as kind,
        bool_or(coalesce(m.registered, false)) as registered, bool_or(coalesce(m.ballots, false)) as ballots,
        bool_or(coalesce(m.survey, false)) as survey
    from election e
    join polity p on p.id = e.polity_id and p.kind in ('federal_unit', 'region') and p.iso_code = 'US-' || %(st)s
    left join m on m.election_id = e.id
    where e.status <> 'scheduled' and e.date_from < current_date
        and e.kind in ('general', 'primary', 'runoff', 'referendum')
        and (extract(year from e.date_from) >= 2000 or e.kind = 'general' and extract(year from e.date_from) >= 1948)
        and exists (select from contest c where c.election_id = e.id and not c.notional)
    group by 1, 2, 3
)
select st, extract(year from date_from)::int, kind,
    count(*) filter (where ballots and (registered or st = 'ND' and date_from >= '1952-01-01')),
    count(*) filter (where (ballots or registered) and not (ballots and (registered or st = 'ND' and date_from >= '1952-01-01'))),
    count(*) filter (where survey and not (ballots or registered)),
    count(*) filter (where not (ballots or registered or survey))
from days group by 1, 2, 3
"""


def cycle(weights: dict[int, int]) -> tuple[int, int] | None:
    total = sum(weights.values())
    for period in (4, 2):
        shares: Counter = Counter()
        for year, weight in weights.items():
            shares[year % period] += weight
        phase, n = shares.most_common(1)[0]
        if n >= 0.9 * total:
            return period, phase
    return None


def tokens(text: str) -> frozenset[str]:
    text = re.sub(r"\([^)]*\)", " ", text.upper().replace("'", "").replace("’", ""))
    text = re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m[0].replace(".", ""), text)
    match = PREFIX.match(text)
    words = re.sub(r"[^A-Z0-9 ]", " ", (match[2] if match else text).replace("&", " AND ").replace("#", " ")).split()
    out = []
    for word in words:
        out += [w for w in ABBREVIATIONS.get(word, word).split() if w not in STOP]
    return frozenset(re.sub(r"^0+(?=\d)", "", w) for w in out)


def request() -> None:
    if (ROOT / "data" / "logs" / "coverage.paused").exists():
        return
    with connect(autocommit=True) as conn:
        recent = conn.execute(
            "update coverage_build set requested_at = now() returning built_at > now() - interval '3 hours'"
        ).fetchone()
    if recent is not None and recent[0]:
        return
    subprocess.Popen(
        [sys.executable, "-c", "from ea.cli import main; main()", "coverage", "refresh"],
        cwd=ROOT / "pipelines",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


WORKERS = 8
STATE_INPUTS = (
    "pipelines/ea/coverage.py",
    "pipelines/ea/coverage_tables.py",
    "pipelines/ea/statewide_table.py",
    "pipelines/ea/readiness.py",
    f"pipelines/ea/readiness/{readiness.DAY}.json",
    "pipelines/ea/sources/us/ncsl_measures.csv",
    ".agents/spec/finest-reported.csv",
    ".agents/spec/coverage-gaps.tsv",
)
COUNTRY_INPUTS = (
    "pipelines/ea/coverage.py",
    "pipelines/ea/coverage_countries.py",
    "pipelines/ea/readiness.py",
    f"pipelines/ea/readiness/{readiness.DAY}.json",
    ".agents/spec/finest-reported.csv",
    ".agents/spec/coverage-gaps.tsv",
)

PART_SQL = """
create table if not exists coverage_cache_part (stage text not null, part text not null, inputs text not null,
    built_at timestamptz not null, seconds real not null, rows json not null, primary key (stage, part))
"""

AREA_SQL = (
    """insert into coverage_cache_area select distinct area_id from area_geometry where recorded_from > %(since)s
       on conflict do nothing""",
    """delete from coverage_cache_area a
       where a.area_id in (select area_id from area_geometry_history where upper(recorded) > %(since)s)
           and not exists (select from area_geometry g where g.area_id = a.area_id)""",
)

STAMP_SQL = """
select least(now(), min(xact_start)) from pg_stat_activity
where datname = current_database() and backend_type = 'client backend' and pid <> pg_backend_pid()
"""

AREAS_SQL = """
with recursive areas as (
    select area_id as id, recorded_from as at from area_geometry where recorded_from > %(since)s
    union all select area_id, upper(recorded) from area_geometry_history where upper(recorded) > %(since)s
    union all select area_id, recorded_from from area_code where recorded_from > %(since)s
    union all select area_id, upper(recorded) from area_code_history where upper(recorded) > %(since)s
    union all select id, recorded_from from area where recorded_from > %(since)s
    union all select id, upper(recorded) from area_history where upper(recorded) > %(since)s
)"""

CHANGED_SQL = (
    AREAS_SQL
    + """,
k as (
    select id, election_id, office_id, body_id, recorded_from as at from contest where recorded_from > %(since)s
    union all select id, election_id, office_id, body_id, upper(recorded) from contest_history
    where upper(recorded) > %(since)s
    union all select c.id, c.election_id, c.office_id, c.body_id, t.at from (
        select contest_id as id, recorded_from as at from result_set where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from result_set_history where upper(recorded) > %(since)s
        union all select contest_id, recorded_from from contest_option where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from contest_option_history where upper(recorded) > %(since)s
        union all select o.contest_id, y.at from (
            select option_id, recorded_from as at from candidacy where recorded_from > %(since)s
            union all select option_id, upper(recorded) from candidacy_history where upper(recorded) > %(since)s
        ) y join contest_option o on o.id = y.option_id
        union all select r.contest_id, x.at from (
            select result_set_id, recorded_from as at from outcome where recorded_from > %(since)s
            union all select result_set_id, upper(recorded) from outcome_history where upper(recorded) > %(since)s
        ) x join result_set r on r.id = x.result_set_id
        union all select contest_id, recorded_from from tenure where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from tenure_history where upper(recorded) > %(since)s
        union all select contest_id, recorded_from from ballot_question_text where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from ballot_question_text_history where upper(recorded) > %(since)s
        union all select entity_id, recorded_from from label where recorded_from > %(since)s
        union all select entity_id, upper(recorded) from label_history where upper(recorded) > %(since)s
        union all select entity_id, recorded_from from external_id
        where recorded_from > %(since)s and scheme = 'us.contest'
        union all select entity_id, upper(recorded) from external_id_history
        where upper(recorded) > %(since)s and scheme = 'us.contest'
        union all select r.contest_id, s.at from (
            select id, recorded_from as at from source where recorded_from > %(since)s
            union all select id, upper(recorded) from source_history where upper(recorded) > %(since)s
        ) s join result_set r on r.source_id = s.id
        union all select c.id, a.at from areas a join contest c on c.district_area_id = a.id
    ) t join contest c on c.id = t.id
),
named as (
    select entity_id, recorded_from as at from name where recorded_from > %(since)s
    union all select entity_id, upper(recorded) from name_history where upper(recorded) > %(since)s
    union all select entity_id, recorded_from from external_id where recorded_from > %(since)s
    union all select entity_id, upper(recorded) from external_id_history where upper(recorded) > %(since)s
),
touched as (
    select polity_id, max(at) as at, bool_or(wide) as wide from (
        select e.polity_id, k.at, false as wide from k join election e on e.id = k.election_id
        union all select o.polity_id, k.at, false from k join office o on o.id = k.office_id
        union all select b.polity_id, k.at, false from k join body b on b.id = k.body_id
        union all select e.polity_id, r.at, false from (
            select election_id, recorded_from as at from result_set where recorded_from > %(since)s and contest_id is null
            union all select election_id, upper(recorded) from result_set_history
            where upper(recorded) > %(since)s and contest_id is null
        ) r join election e on e.id = r.election_id
        union all select polity_id, recorded_from, false from election where recorded_from > %(since)s
        union all select polity_id, upper(recorded), false from election_history where upper(recorded) > %(since)s
        union all select polity_id, recorded_from, false from reporting_unit where recorded_from > %(since)s
        union all select polity_id, upper(recorded), false from reporting_unit_history where upper(recorded) > %(since)s
        union all select u.polity_id, a.at, false from areas a join reporting_unit u on u.area_id = a.id
        union all select t.polity_id, a.at, false from areas a join polity_territory t on t.area_id = a.id
        union all select id, recorded_from, false from polity where recorded_from > %(since)s
        union all select id, upper(recorded), false from polity_history where upper(recorded) > %(since)s
        union all select unnest(array[polity_id, parent_id]), recorded_from, false from polity_parent
        where recorded_from > %(since)s
        union all select unnest(array[polity_id, parent_id]), upper(recorded), false from polity_parent_history
        where upper(recorded) > %(since)s
        union all select polity_id, recorded_from, false from polity_territory where recorded_from > %(since)s
        union all select polity_id, upper(recorded), false from polity_territory_history where upper(recorded) > %(since)s
        union all select p.id, n.at, false from named n join polity p on p.id = n.entity_id
        union all select o.polity_id, n.at, true from named n join office o on o.id = n.entity_id
        union all select b.polity_id, n.at, true from named n join body b on b.id = n.entity_id
        union all select polity_id, recorded_from, true from office where recorded_from > %(since)s
        union all select polity_id, upper(recorded), true from office_history where upper(recorded) > %(since)s
        union all select polity_id, recorded_from, true from body where recorded_from > %(since)s
        union all select polity_id, upper(recorded), true from body_history where upper(recorded) > %(since)s
        union all select b.polity_id, s.at, true from (
            select body_id, recorded_from as at from seat where recorded_from > %(since)s
            union all select body_id, upper(recorded) from seat_history where upper(recorded) > %(since)s
        ) s join body b on b.id = s.body_id
    ) x
    where polity_id is not null
    group by 1
),
up(id, anc) as (
    select polity_id, polity_id from touched
    union select up.id, pp.parent_id from up join polity_parent pp on pp.polity_id = up.anc
)
select t.at, t.wide, array_agg(p.iso_code) filter (where p.iso_code is not null),
    min(substr(p.iso_code, 4)) filter (where p.kind in ('federal_unit', 'region'))
from touched t join up on up.id = t.polity_id join polity p on p.id = up.anc
group by t.polity_id, t.at, t.wide
"""
)

DISTRICTS_CHANGED_SQL = (
    AREAS_SQL
    + """
select substr(ac.code, 1, 2), max(c.at)
from areas c join area a on a.id = c.id and a.scheme_code = 'us.cd' join area_code ac on ac.area_id = a.id
group by 1
"""
)

OFFICE_YEARS_SQL = "select state, office_id::text, class, year, joint_ticket, basis from statewide_office_year"


def digest(paths: tuple[str, ...]) -> str:
    sha = hashlib.sha256()
    for path in paths:
        sha.update(path.encode())
        sha.update((ROOT / path).read_bytes())
    return sha.hexdigest()[:16]


def refresh(full: bool = False) -> str:
    built = []
    with connect(autocommit=True) as conn:
        conn.execute("update coverage_build set requested_at = now()")
        while True:
            locked = conn.execute("select pg_try_advisory_lock(%s)", [LOCK]).fetchone()
            if locked is None or not locked[0]:
                return "; ".join(built) or "a refresh is already running and will pick up this request"
            while (
                started := conn.execute(
                    """update coverage_build set started_at = now()
                       where built_at is null or requested_at > built_at returning started_at"""
                ).fetchone()
            ) is not None:
                stamp = coverage_countries.inputs()
                commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
                ).stdout.strip()
                built.append(build(full))
                full = False
                conn.execute(
                    "update coverage_build set built_at = %s, commit = %s, inputs = %s",
                    [started[0], commit, json.dumps(stamp)],
                )
                coverage_countries.record(conn)
            conn.execute("select pg_advisory_unlock(%s)", [LOCK])
            pending = conn.execute("select built_at is null or requested_at > built_at from coverage_build").fetchone()
            if pending is None or not pending[0]:
                return "; ".join(built)


def measure(st: str, members: list[dict], today: date) -> tuple[str, dict, float]:
    started = time.monotonic()
    with connect() as conn:
        cells = coverage.matrix(conn, [st], members, last=today.year)
        conn.execute("create temp table gap (st text, level text, stage text, y0 int, y1 int, ids uuid[])")
        conn.cursor().executemany(
            "insert into gap values (%s, %s, %s, %s, %s, %s)",
            [
                (row["state"], row["level"], row["stage"], row["from"], row["to"], row["ids"])
                for row in coverage.registry()
                if row["polity"] == "us" and row["grain"] in ("results", "county", "precinct")
            ],
        )
        gaps = [list(row) for row in conn.execute(coverage.GAPS_SQL)]
        units = [row for row in conn.execute(coverage.UNITS_SQL) if row[0] is not None]
        conn.execute(LOCAL_SQL)
        held: dict[str, dict[int, list]] = defaultdict(dict)
        for polity, year, n, precinct, regular, month, generals, up, ready in conn.execute(HELD_SQL):
            held[str(polity)][year] = [n, precinct, regular, month, 0, 0, generals, up, ready]
        for polity, year, n, shaped in conn.execute(LOCAL_SHAPES_SQL):
            held[str(polity)][year][4:6] = [n, shaped]
        for statement in SHAPES_SQL.split(";\n\n"):
            conn.execute(statement)
        shapes = list(conn.execute(SHAPES_COUNT_SQL))
        turnout = list(conn.execute(TURNOUT_SQL, {"st": st}))
    return (
        st,
        {"cells": cells, "units": units, "held": held, "shapes": shapes, "turnout": turnout, "gaps": gaps},
        time.monotonic() - started,
    )


def country(cc: str) -> tuple[str, dict, float]:
    started = time.monotonic()
    with connect() as conn:
        goal_rows, polity_row = coverage_countries.part(conn, cc)
    return cc, {"goals": goal_rows, "polity": polity_row}, time.monotonic() - started


def build(full: bool = False) -> str:
    started = time.monotonic()
    today = date.today()
    members = [
        person
        for kind in ("historical", "current")
        for person in json.loads(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)).path.read_text())
    ]
    states_code = digest(STATE_INPUTS) + ":" + hashlib.sha256(json.dumps(members).encode()).hexdigest()[:16]
    countries_code = digest(COUNTRY_INPUTS)
    cog_files = sorted(raw.parsed("us.cog").glob("*.parquet"))
    local_code = (
        digest(("pipelines/ea/coverage_tables.py",))
        + ":"
        + ":".join(f"{f.name}{f.stat().st_mtime_ns}" for f in cog_files)
    )
    timings: dict[str, float] = {}
    with connect(autocommit=True) as conn:
        conn.execute(PART_SQL)
        stamp: datetime = next(iter(conn.execute(STAMP_SQL)))[0]
        parts = {
            (stage, part): (inputs, built_at, seconds)
            for stage, part, inputs, built_at, seconds in conn.execute(
                "select stage, part, inputs, built_at, seconds from coverage_cache_part"
            )
        }
        clock = time.monotonic()
        conn.execute(coverage.CACHE_SQL)
        conn.execute("create table if not exists coverage_cache_area (area_id uuid primary key)")
        for statement in AREA_SQL:
            conn.execute(statement, {"since": parts.get(("cache", "area"), ("", "-infinity"))[1]})
        conn.execute("analyze coverage_cache_area")
        conn.execute(
            """insert into coverage_cache_part values ('cache', 'area', '', %s, 0, '{}')
               on conflict (stage, part) do update set built_at = excluded.built_at""",
            [stamp],
        )
        timings["cache"] = time.monotonic() - clock
        clock = time.monotonic()
        before = set(conn.execute(OFFICE_YEARS_SQL))
        with conn.transaction():
            statewide_table.rebuild(conn)
        offices = {row[0] for row in before ^ set(conn.execute(OFFICE_YEARS_SQL))}
        timings["statewide"] = time.monotonic() - clock
        for statement in coverage.CONTESTS_SQL.split(";\n\n")[:2]:
            conn.execute(statement)
        states = {fips: (st, name) for st, fips, name in conn.execute(coverage.STATES_SQL)}
        scope = sorted({st for (st,) in conn.execute("select distinct st from pstate where st is not null")} | {"US"})
        wanted = {("state", st): f"{states_code}:{today}:{readiness.local_today('US-' + st)}" for st in scope}
        for cc in ["us", *coverage_countries.countries()]:
            zones = sorted(
                {str(readiness.local_today(iso)) for iso in readiness.ZONES if cc == "eu" or iso[:2] == cc.upper()}
                | {str(readiness.local_today(cc.upper()))}
            )
            extra = "" if cc == "us" else digest((f"pipelines/ea/denominators/{cc}.json",))
            wanted[("country", cc)] = f"{countries_code}:{extra}:{today}:{','.join(zones)}"
        wanted[("local", "")] = f"{local_code}:{today}"
        clock = time.monotonic()
        moved: dict[tuple[str, str], datetime] = {}
        since = min((built for (stage, _), (_, built, _) in parts.items() if stage != "cache"), default=None)
        if since is not None and not full:
            isos = {coverage_countries.load(cc)["polity"]: cc for cc in coverage_countries.countries()}
            for at, wide, ancestors, st in conn.execute(CHANGED_SQL, {"since": since}):
                keys = [("country", isos[iso]) for iso in set(ancestors or []) if iso in isos]
                if "US" in (ancestors or []):
                    keys += [("state", "*" if wide and st is None else st or "US"), ("country", "us"), ("local", "")]
                for key in keys:
                    moved[key] = max(moved.get(key, at), at)
            for code, at in conn.execute(DISTRICTS_CHANGED_SQL, {"since": since}):
                if code in states:
                    key = ("state", states[code][0])
                    moved[key] = max(moved.get(key, at), at)
        for st in offices:
            moved[("state", st)] = stamp
        timings["changes"] = time.monotonic() - clock

        def stale(key: tuple[str, str]) -> bool:
            if full or key not in parts or parts[key][0] != wanted[key]:
                return True
            built = parts[key][1]
            return any(at >= built for at in (moved.get(key), moved.get((key[0], "*"))) if at is not None)

        todo = [st for st in scope if stale(("state", st))]
        countries = [cc for stage, cc in wanted if stage == "country" and stale(("country", cc))]
        clock = time.monotonic()
        with ThreadPoolExecutor(WORKERS) as pool:
            jobs = [
                pool.submit(measure, part, members, today) if stage == "state" else pool.submit(country, part)
                for stage, part in sorted(
                    [("state", st) for st in todo] + [("country", cc) for cc in countries],
                    key=lambda key: -parts.get(key, ("", None, 0.0))[2],
                )
            ]
            for job in as_completed(jobs):
                part, rows, seconds = job.result()
                conn.execute(
                    """insert into coverage_cache_part values (%s, %s, %s, %s, %s, %s)
                       on conflict (stage, part) do update set inputs = excluded.inputs, built_at = excluded.built_at,
                           seconds = excluded.seconds, rows = excluded.rows""",
                    [
                        "state" if "cells" in rows else "country",
                        part,
                        wanted[("state" if "cells" in rows else "country", part)],
                        stamp,
                        seconds,
                        Json(rows),
                    ],
                )
        timings["parts"] = time.monotonic() - clock
        conn.execute("delete from coverage_cache_part where stage = 'state' and part <> all(%s)", [scope])
        written = parts.get(("local", ""), ("", None, 0.0))[1]
        newer = {
            stage
            for (stage, _), (_, built, _) in parts.items()
            if stage in ("state", "country") and (written is None or built > written)
        }
        if not todo and not countries and not newer and not stale(("local", "")):
            return "nothing has changed since the last build"
        clock = time.monotonic()
        country_goals: list[tuple] = []
        country_polities: list[tuple] = []
        for (rows,) in conn.execute("select rows from coverage_cache_part where stage = 'country' order by part"):
            country_goals += [tuple(row) for row in rows["goals"]]
            country_polities.append(tuple(rows["polity"]))
        if not todo and "state" not in newer and not stale(("local", "")):
            (known,) = conn.execute(
                "select coalesce(rows -> 'known', '[]') from coverage_cache_part where stage = 'local'"
            ).fetchone() or ([],)
            with conn.transaction():
                coverage_countries.write(
                    conn,
                    country_goals,
                    [
                        (*row[:9], json.dumps(json.loads(row[9]) + known, ensure_ascii=False), row[10])
                        if row[0] == "us"
                        else row
                        for row in country_polities
                    ],
                )
                conn.execute("update coverage_cache_part set built_at = %s where stage = 'local'", [stamp])
            return f"{len(countries)} countries rebuilt in {time.monotonic() - started:.0f}s"
        cells: list[dict] = []
        units: list[tuple] = []
        held: dict[str, dict[int, list]] = defaultdict(dict)
        shapes: list[tuple] = []
        turnout: list[tuple] = []
        gaps: list[list] = []
        for (rows,) in conn.execute("select rows from coverage_cache_part where stage = 'state' order by part"):
            cells += rows["cells"]
            gaps += rows["gaps"]
            units += rows["units"]
            shapes += rows["shapes"]
            turnout += rows["turnout"]
            for polity, years in rows["held"].items():
                for year, values in years.items():
                    held[polity][int(year)] = values
        polities = {str(pid): (kind, code, name, st) for pid, kind, code, name, st in conn.execute(POLITIES_SQL)}
        timings["load"] = time.monotonic() - clock
        clock = time.monotonic()

    by_key = {(c["state"], c["year"], c["level"], c["stage"]): c for c in cells}
    for stage in ("primary", "general"):
        for (st, year, level, cell_stage), cell in list(by_key.items()):
            if (
                cell_stage != stage
                or level not in STATE_LEVELS
                or level == "statewide_measure"
                or year != today.year - OFFSETS.get(level, 4)
            ):
                continue
            key = (st, today.year, level, stage)
            current = by_key.setdefault(
                key,
                {
                    "state": st,
                    "year": today.year,
                    "level": level,
                    "stage": stage,
                    "expected": "",
                    "held": 0,
                    "county_or_finer": 0,
                    "precinct": 0,
                    "precincts": 0,
                    "shaped_precincts": 0,
                    "upcoming": 0,
                    "pending": 0,
                    "target": coverage.target(st, level, today.year),
                },
            )
            current["expected"] = max(current["expected"] or 0, cell["held"])

    known = dict(KNOWN)
    for st, period in JUDICIAL.items():
        for year in range(2000 + (st == "PA"), today.year + 1, period):
            retained = any(
                "us.retention" in c.get("pipelines", "")
                for (s, y, level, _), c in by_key.items()
                if s == st and y == year and level == "statewide_measure"
            )
            known.setdefault((st, year, "judicial", "general"), 0 if retained else 1)
    for (st, year, level, stage), due in known.items():
        if due == 0:
            continue
        current = by_key.setdefault(
            (st, year, level, stage),
            {
                "state": st,
                "year": year,
                "level": level,
                "stage": stage,
                "expected": "",
                "held": 0,
                "county_or_finer": 0,
                "precinct": 0,
                "precincts": 0,
                "shaped_precincts": 0,
                "target": coverage.target(st, level, year),
            },
        )
        current["expected"] = max(current["expected"] or 0, due, current["held"])

    cog = pl.read_parquet(raw.parsed("us.cog") / "governments.parquet")
    codes = {code: pid for pid, (_, code, _, _) in polities.items() if code is not None}
    link: dict[str, str] = {}
    for gid, kind, st, county, place in cog.select("id", "kind", "state_fips", "county_fips", "place_fips").iter_rows():
        candidates = [f"county:{st}{county}"] if kind == "county" else []
        if kind in ("municipality", "township") and place is not None:
            candidates = [f"place:{BALANCES.get(st + place, st + place)}", f"town:{st}{county}{place}"]
            if county is not None and county >= "500":
                candidates.append(f"county:{st}{county}")
            if st + place == "3651000":
                candidates.append("new-york-city")
        found = next((codes[c] for c in candidates if c in codes), None)
        if found is not None:
            link[gid] = found
    towns: dict[tuple[str, str], list[str]] = defaultdict(list)
    for code, pid in codes.items():
        if code.startswith("town:"):
            towns[(code[5:7], code[10:])].append(pid)
    for gid, kind, st, place in cog.select("id", "kind", "state_fips", "place_fips").iter_rows():
        if gid not in link and kind in ("municipality", "township") and len(towns.get((st, place or ""), [])) == 1:
            link[gid] = towns[(st, place)][0]
    pools: dict[tuple[str, str], list[tuple[str, str | None, frozenset[str]]]] = defaultdict(list)
    for gid, kind, name, st, county in cog.select("id", "kind", "name", "state_fips", "county_fips").iter_rows():
        if gid not in link:
            pools[(st, {"dependent_school": "school", "township": "municipality"}.get(kind, kind))].append(
                (gid, county, tokens(name))
            )
    taken = set(link.values())
    for code, pid in codes.items():
        prefix, _, rest = code.partition(":")
        if pid in taken or prefix not in ("school", "special", "place", "town", "county"):
            continue
        county = rest[2:5] if prefix == "special" else None
        mine = tokens(polities[pid][2])
        pool = pools.get((rest[:2], prefix if prefix in ("school", "special") else "municipality"), [])
        if not any(w.isdigit() for w in mine):
            pool = [(g, c, frozenset(w for w in t if not (w.isdigit() and len(w) >= 3))) for g, c, t in pool]
        digits = {w for w in mine if w.isdigit()}
        pool = [g for g in pool if county is None or g[1] == county]
        found = [g for g in pool if g[2] == mine]
        if len(found) != 1:
            ranked = sorted(
                ((len(mine & g[2]) / len(mine | g[2]), g) for g in pool if {w for w in g[2] if w.isdigit()} == digits),
                key=lambda pair: -pair[0],
            )
            if ranked and ranked[0][0] >= 0.75 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0]):
                found = [ranked[0][1]]
            else:
                found = [g for _, g in ranked if mine < g[2] and "COLLEGE" not in g[2] - mine]
        if len(found) == 1 and found[0][0] not in link:
            link[found[0][0]] = pid
            taken.add(pid)
    plain = frozenset(
        (
            "SCHOOL",
            "DISTRICT",
            "CITY",
            "PUBLIC",
            "COUNTY",
            "INDEPENDENT",
            "CONSOLIDATED",
            "COMMUNITY",
            "TOWNSHIP",
            "AREA",
            "ELEMENTARY",
            "JOINT",
            "SPECIAL",
        )
    )
    for code, pid in codes.items():
        if pid in taken or not code.startswith("school:"):
            continue
        mine = {w for w in tokens(polities[pid][2]) if not w.isdigit()} - plain
        found = [
            g[0]
            for g in pools.get((code[7:9], "school"), [])
            if g[0] not in link and mine and {w for w in g[2] if not w.isdigit()} - plain == mine
        ]
        if len(found) == 1:
            link[found[0]] = pid
            taken.add(pid)
    for code, pid in codes.items():
        if pid in taken or not code.startswith("school:"):
            continue
        mine = {w for w in tokens(polities[pid][2]) if not w.isdigit()} - plain
        ranked = []
        for g in pools.get((code[7:9], "school"), []):
            theirs = {w for w in g[2] if not w.isdigit()} - plain
            if g[0] in link or not mine or not theirs or theirs & {"COLLEGE", "COL", "UNIVERSITY"}:
                continue
            small, big = sorted((mine, theirs), key=len)
            score = (
                0.9
                if small < big and len(big - small) <= 2 and sum(map(len, small)) >= 5
                else SequenceMatcher(None, "".join(sorted(mine)), "".join(sorted(theirs))).ratio()
            )
            ranked.append((score, g[0]))
        ranked.sort(reverse=True)
        if ranked and ranked[0][0] >= 0.85 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0] - 0.05):
            link[ranked[0][1]] = pid
            taken.add(pid)
    link["23508201400000"] = "cf3230d1-cf93-5584-bb33-3458b297c168"
    link["05504300800000"] = "5f1810f8-9163-5a5e-aed2-ccd9fc4b1532"

    governments: list[dict] = []
    for gid, kind, name, st, county, function, population, population_basis, active, elected in cog.select(
        "id",
        "kind",
        "name",
        "state_fips",
        "county_fips",
        "function",
        "population",
        "population_basis",
        "active",
        "elected",
    ).iter_rows():
        pid = link.get(gid)
        # Census of Governments leaves the Jersey City School System (31000000030100) without a county; Jersey City is in Hudson County (FIPS 34017).
        if gid == "31000000030100":
            county = "017"
        match = PREFIX.match(name)
        words = (
            name
            if match is None or "GOVERNMENT" in match[1] or match[1] == "CITY AND COUNTY"
            else f"{match[2]} {match[1]}"
            if match[1] in ("COUNTY", "TOWNSHIP", "CHARTER TOWNSHIP")
            else match[2]
        ).split()
        governments.append(
            {
                "id": gid,
                "state": states[st][0],
                "county": None if county is None else st + county,
                "kind": "college"
                if kind in ("school", "dependent_school") and COLLEGE.search(name)
                else {"dependent_school": "school"}.get(kind, kind),
                "name": polities[pid][2]
                if pid is not None
                else " ".join(
                    w
                    if w in ACRONYMS
                    else w.lower()
                    if i > 0 and w in ("OF", "AND", "THE", "AT", "ON", "IN")
                    else "-".join(part.capitalize() for part in w.split("-"))
                    for i, w in enumerate(words)
                ),
                "function": None if function is None else function.split(" - ", 1)[-1].capitalize(),
                "village": match is not None and match[1] == "VILLAGE",
                "population": population,
                "population_basis": population_basis,
                "active": active,
                # NJ county vocational boards are appointed by the county executive or commissioners (N.J.S.A. 18A:54-16, https://pub.njleg.state.nj.us/statutes), not elected.
                "elected": elected
                and not (st == "34" and kind in ("school", "dependent_school") and re.search(r"VOCATIONAL", name)),
                "polity_id": pid,
            }
        )
    for pid, years in held.items():
        if pid in taken or pid not in polities:
            continue
        kind, code, name, st = polities[pid]
        prefix = (code or "").partition(":")[0]
        governments.append(
            {
                "id": f"polity:{pid}",
                "state": st or "US",
                "county": None,
                "kind": "township"
                if prefix == "town" and kind == "municipality"
                else "college"
                if kind == "school_district" and COLLEGE.search(name)
                else {"county": "county", "municipality": "municipality", "school_district": "school"}.get(
                    kind, "special" if kind == "special_district" else "other"
                ),
                "name": name,
                "function": None,
                "village": False,
                "population": None,
                "population_basis": None,
                "active": True,
                "elected": True,
                "polity_id": pid,
            }
        )

    patterns: dict[tuple[str, str], Counter] = defaultdict(Counter)
    months: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for g in governments:
        years = held.get(g["polity_id"] or "", {})
        regular = sorted(y for y, v in years.items() if v[2] and 2000 <= y <= today.year)
        g["month"] = Counter(years[y][3] for y in regular).most_common(1)[0][0] if regular else None
        g["period"] = g["phase"] = g["era"] = None
        if len(regular) >= 2:
            weights = {y: years[y][2] for y in regular}
            g["period"], g["phase"] = cycle(weights) or (1, 0)
            g["basis"] = "own"
            if g["period"] == 1:
                splits = [
                    (
                        sum(weights[y] for y in regular[:i] if (y - early[1]) % early[0] == 0)
                        + sum(weights[y] for y in regular[i:] if (y - late[1]) % late[0] == 0),
                        i,
                        early,
                        late,
                    )
                    for i in range(2, len(regular) - 1)
                    if (early := cycle({y: weights[y] for y in regular[:i]})) is not None
                    and (late := cycle({y: weights[y] for y in regular[i:]})) is not None
                    and early != late
                ]
                if splits:
                    _, i, early, late = max(splits)
                    g["period"], g["phase"] = late
                    g["era"] = (regular[i - 1], regular[i], early)
                    g["basis"] = "own, two eras"
            patterns[(g["state"], g["kind"])][(g["period"], g["phase"])] += 1
        elif regular:
            g["phase"] = regular[0]
            g["basis"] = "own year, state pattern"
        else:
            g["basis"] = "state pattern"
        if g["month"] is not None:
            months[(g["state"], g["kind"])][g["month"]] += 1

    years_out: list[tuple] = []
    ahead: dict[tuple[str, int], bool] = {}
    for g in governments:
        group = (g["state"], g["kind"])
        calendar = CALENDAR.get(
            (g["state"], g["function"] or ""),
            CALENDAR.get(group, CALENDAR.get((g["state"], "school")))
            if g["kind"] == "college"
            else CALENDAR.get((g["state"], "village") if g["village"] else group, CALENDAR.get(group)),
        )
        if g["period"] is None:
            period, phase, cite = calendar[:3] if calendar is not None else (None, None, None)
            common = [p for p, _ in patterns[group].most_common() if period is None or p[0] == period]
            if g["phase"] is not None:
                if period is None:
                    counted = Counter(p for p, _ in patterns[group].elements()).most_common(1)
                    period = counted[0][0] if counted else 2
                g["period"], g["phase"] = period, g["phase"] % period
            elif period is not None:
                g["period"], g["phase"] = period, phase
                g["basis"] = "state calendar" if cite else "estimated state calendar"
            elif common:
                g["period"], g["phase"] = common[0]
            else:
                g["period"], g["phase"], g["basis"] = 2, 0, "unknown"
        if (
            calendar is not None
            and calendar[0] == 2
            and calendar[2]
            and g["period"] == 1
            and g["era"] is None
            and g["basis"].startswith("own")
        ):
            g["period"], g["phase"] = calendar[:2]
            g["basis"] = "state calendar, own history conflicted"
        if g["month"] is None and calendar is not None and len(calendar) == 4:
            g["month"] = calendar[3]
        elif g["month"] is None and months[group]:
            g["month"] = months[group].most_common(1)[0][0]
        if group in SWITCHED and (g["period"], g["phase"]) != SWITCHED[group][1]:
            g["era"] = (SWITCHED[group][0] - 1, SWITCHED[group][0], (g["period"], g["phase"]))
            g["period"], g["phase"] = SWITCHED[group][1]
            g["basis"] = "state calendar, changed"
        if (g["state"], g["name"]) in DISTRICT_CALENDAR:
            g["period"], g["phase"], g["month"], _ = DISTRICT_CALENDAR[(g["state"], g["name"])]
            g["era"] = None
            g["basis"] = "district calendar"
        g["month"] = HELD_IN.get(group, g["month"])
        years = held.get(g["polity_id"] or "", {})
        for year in sorted(set(years) | set(range(2000, today.year + 1))):
            era = g["era"]
            due = (g["period"], g["phase"]) if era is None or year >= era[1] else era[2] if year <= era[0] else None
            expected = (
                g["active"]
                and g["elected"]
                and not g["id"].startswith("polity:")
                and 2000 <= year <= today.year
                and due is not None
                and (year - due[1]) % due[0] == 0
                and (year < today.year or (g["month"] is not None and g["month"] < today.month))
            )
            n, precinct, _, _, precincts, shaped, generals, up, ready = years.get(year, [0, 0, 0, None, 0, 0, 0, 0, 0])
            earlier = years.get(year - 4, [0] * 9)[6] if year < today.year else 0
            if n == 0 and up and 2000 <= year <= today.year:
                ahead[(g["id"], year)] = ready > 0
            if expected or n:
                years_out.append(
                    (
                        g["id"],
                        year,
                        expected,
                        n,
                        expected and (n == 0 or generals < earlier),
                        precinct,
                        precincts,
                        shaped,
                    )
                )
    names = dict(states.values())
    registry = coverage.registry()
    hits: dict[int, set[tuple[str, str]]] = defaultdict(set)
    reached: Counter = Counter()
    tags: dict[tuple, Counter] = defaultdict(Counter)
    for st, year, level, stage, contest, election, county_bucket, precinct_bucket in gaps:
        for goal, bucket, grains in (
            ("county", county_bucket, ("results", "county")),
            ("precinct", precinct_bucket, ("results", "county", "precinct") if county_bucket else ("precinct",)),
        ):
            found = bucket and coverage.gap(registry, grains, "us", st, level, stage, year, contest)
            if found:
                tags[(st, year, level, stage)][(goal, found["category"], bucket)] += 1
                hits[found["i"]].add((contest, election))

    def move(cell: list[int], index: int, n: int, found: dict | None, absent: bool = False) -> None:
        if found is None or n <= 0:
            return
        reached[found["i"]] += n if found["i"] >= 0 else 0
        if absent and found["category"] == "known_gap":
            return
        cell[index] -= n
        cell[22 + coverage.CATEGORIES.index(found["category"])] += n
        cell[0] -= n if found["category"] == "not_applicable" else 0

    goals: dict[tuple[str, str, str, int], list[int]] = defaultdict(lambda: [0] * 25)
    for st, year, level, total, complete, unshaped, unit_shapes, undrawn in shapes:
        if level not in STATE_LEVELS or st not in names:
            continue
        cell = goals[("shapes" if year >= 2000 else "shapes_history", st, level, year)]
        cell[0] += total
        cell[1] += complete
        cell[10] += unshaped
        cell[11] += unit_shapes
        cell[12] += undrawn
        move(cell, 12, undrawn, coverage.gap(registry, ("shapes",), "us", st, level, "", year))
    for (st, year, level, stage), c in by_key.items():
        if level not in STATE_LEVELS or st not in names:
            continue
        count = c["held"]
        base = max(0 if c["expected"] == "" else c["expected"], count)
        county = c["county_or_finer"]
        partial = c.get("county_partial", 0)
        bare = c.get("no_results", 0)
        upcoming = c.get("upcoming", 0)
        pending = c.get("pending", 0)
        totals = max(0, count - county - partial - bare - upcoming - pending)
        precinct = c["precinct"]
        major = c.get("major_only", 0)
        published = c.get("published", 0)
        whole = coverage.target(st, level, year) == "total"
        if whole:
            county = precinct = count - bare - upcoming - pending
            partial = totals = major = published = 0
        tagged = tags.get((st, year, level, stage), Counter())
        if year >= 2000:
            for goal, complete, coarse in (
                ("county", county, 0),
                ("precinct", precinct, max(0, county - precinct - major)),
            ):
                cell = goals[(goal, st, level, year)]
                for i, value in enumerate(
                    [base, complete, base - count, bare, totals, partial, coarse, c.get("with_turnout", 0)]
                ):
                    cell[i] += value
                cell[20] += upcoming
                cell[21] += pending
                if goal == "precinct":
                    cell[8] += c["precincts"]
                    cell[9] += c["shaped_precincts"]
                    cell[18] += major
                    cell[19] += published
                else:
                    cell[13] += c.get("icpsr_only", 0)
                grains = ("results", "county") if goal == "county" else ("results", "county", "precinct")
                for (tag, category, bucket), n in tagged.items():
                    if tag == goal and (bucket == "bare" or not whole):
                        index = {"bare": 3, "totals": 4, "partial": 5, "coarse": 6, "major": 18}[bucket]
                        move(cell, index, n, {"category": category, "i": -1})
                move(cell, 2, base - count, coverage.gap(registry, grains, "us", st, level, stage, year), True)
        elif coverage.target(st, level, year) == "county" or level in ("state_upper", "state_lower"):
            cell = goals[("history", st, level, year)]
            for i, value in enumerate([base, county, base - count, bare, totals, partial, 0, c.get("with_turnout", 0)]):
                cell[i] += value
            cell[13] += c.get("icpsr_only", 0)
            for (tag, category, bucket), n in tagged.items():
                if tag == "county" and (bucket == "bare" or not whole):
                    move(cell, {"bare": 3, "totals": 4, "partial": 5}[bucket], n, {"category": category, "i": -1})
            move(
                cell, 2, base - count, coverage.gap(registry, ("results", "county"), "us", st, level, stage, year), True
            )
    for st, year, n, shaped in units:
        if st in names:
            cell = goals[("precinct_shapes", st, "precinct", year)]
            cell[0] += n
            cell[1] += shaped
            cell[10] += n - shaped
    for st, year, kind, complete, partial, coarse, absent in turnout:
        if st not in names:
            continue
        cell = goals[("turnout", st, kind, year)]
        cell[0] += complete + partial + coarse + absent
        cell[1] += complete
        cell[2] += absent
        cell[5] += partial
        cell[6] += coarse
        move(cell, 2, absent, coverage.gap(registry, ("turnout",), "us", st, "", kind, year))
    kinds = {g["id"]: (g["state"], g["kind"], g["population"] or 0, g["population_basis"]) for g in governments}
    for gid, year in ahead.keys() - {(row[0], row[1]) for row in years_out}:
        st, kind, population, _ = kinds[gid]
        if st in names:
            cell = goals[("local", st, kind, year)]
            cell[0] += 1
            cell[20 if ahead[(gid, year)] else 21] += 1
            cell[14] += population
    for gid, year, expected, n, _, _, precincts, shaped in years_out:
        st, kind, population, population_basis = kinds[gid]
        if st not in names or not 2000 <= year <= today.year or not (expected or n):
            continue
        cell = goals[("local", st, kind, year)]
        cell[0] += 1
        cell[1] += n > 0
        if n == 0 and (gid, year) in ahead:
            cell[20 if ahead[(gid, year)] else 21] += 1
            cell[14] += population
            continue
        cell[2] += n == 0
        cell[8] += precincts
        cell[9] += shaped
        cell[14] += population
        cell[15] += population if n > 0 else 0
        found = None if n > 0 else coverage.gap(registry, ("results",), "us", st, kind, "", year)
        move(cell, 2, 1, found)
        cell[16] += population if n == 0 and found is None else 0
        cell[14] -= population if found is not None and found["category"] == "not_applicable" else 0
        cell[17] += population if population_basis in ("place", "county") else 0

    with connect() as conn, conn.transaction():
        conn.execute("delete from coverage_goal where polity = 'us'")
        conn.execute("truncate coverage_government_year, coverage_government")
        with conn.cursor().copy(
            "copy coverage_government (id, state, county, kind, name, function, population, population_basis, active,"
            " polity_id, period, phase, month, basis) from stdin"
        ) as copy:
            for g in governments:
                copy.write_row(
                    [
                        g[k]
                        for k in (
                            "id",
                            "state",
                            "county",
                            "kind",
                            "name",
                            "function",
                            "population",
                            "population_basis",
                            "active",
                            "polity_id",
                            "period",
                            "phase",
                            "month",
                            "basis",
                        )
                    ]
                )
        with conn.cursor().copy(
            "copy coverage_government_year (government_id, year, expected, held, missing, precinct, precincts, shaped)"
            " from stdin"
        ) as copy:
            for row in years_out:
                copy.write_row(row)
        timings["local"] = time.monotonic() - clock
        clock = time.monotonic()
        ready, counted = readiness.goals(conn, readiness.DAY)
        goals |= ready
        reached.update(counted)
        timings["readiness"] = time.monotonic() - clock
        with conn.cursor().copy(
            "copy coverage_goal (goal, state, level, year, total, complete, absent, bare, totals, partial, coarse,"
            " turnout, precincts, shaped, unshaped, units, undrawn, icpsr, population_total, population_complete,"
            " population_absent, population_estimated, major, published, upcoming, pending, known_gap, blocked,"
            " inapplicable) from stdin"
        ) as copy:
            for key, values in goals.items():
                copy.write_row([*key, *values])
        known = [
            coverage.note(row, hits[row["i"]], len({contest for contest, _ in hits[row["i"]]}) + reached[row["i"]])
            for row in registry
            if row["polity"] == "us"
        ]
        coverage_countries.write(
            conn,
            country_goals,
            [
                (*row[:9], json.dumps(json.loads(row[9]) + known, ensure_ascii=False), row[10])
                if row[0] == "us"
                else row
                for row in country_polities
            ],
        )
        conn.execute(
            """insert into coverage_cache_part values ('local', '', %s, %s, %s, %s)
               on conflict (stage, part) do update set inputs = excluded.inputs, built_at = excluded.built_at,
                   seconds = excluded.seconds, rows = excluded.rows""",
            [wanted[("local", "")], stamp, timings["local"], Json({"known": known})],
        )
    return (
        f"{len(todo)} of {len(scope)} states and {len(countries)} of {len(coverage_countries.countries()) + 1} "
        f"countries rebuilt; {len(governments):,} governments ({len(link):,} linked), {len(years_out):,} government "
        f"years, {len(goals):,} goal rows, {len(country_goals):,} other countries' goal rows in "
        f"{time.monotonic() - started:.0f}s ("
        + ", ".join(f"{stage} {seconds:.0f}s" for stage, seconds in timings.items())
        + ")"
    )
