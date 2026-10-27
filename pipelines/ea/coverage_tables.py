import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import date

import polars as pl

from ea import coverage, coverage_countries, raw, readiness, statewide_table
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
CALENDAR: dict[tuple[str, str], tuple[int, int, str | None]] = {
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
    ("GA", "school"): (2, 0, None),
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
        "Minn. Stat. § 205.075, subd. 1; § 365.51, subd. 3, https://www.revisor.mn.gov/statutes/cite/205.075",
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
    ("NY", "school"): (1, 0, None),
    ("NY", "special"): (1, 0, None),
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
    coalesce(cg.precinct, false) or cc.tacit as precinct
from cc
join contest c on c.id = cc.id
join election e on e.id = c.election_id
left join office o on o.id = c.office_id
left join body b on b.id = c.body_id
left join cg on cg.contest_id = cc.id
where cc.level in ('county', 'municipal', 'school', 'special_district', 'local_measure')
"""

HELD_SQL = """
select lc.polity_id, lc.year, count(*), count(*) filter (where lc.precinct), count(*) filter (where lc.regular),
    mode() within group (order by lc.month) filter (where lc.regular),
    count(*) filter (where lc.regular and lc.stage = 'general')
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
    join polity p on p.id = e.polity_id and p.kind in ('federal_unit', 'region') and p.iso_code like 'US-%%'
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
    match = PREFIX.match(text)
    words = re.sub(r"[^A-Z0-9 ]", " ", (match[2] if match else text).replace("&", " AND ").replace("#", " ")).split()
    out = []
    for word in words:
        out += [w for w in ABBREVIATIONS.get(word, word).split() if w not in STOP]
    return frozenset(re.sub(r"^0+(?=\d)", "", w) for w in out)


def request() -> None:
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


FINGERPRINT_SQL = """
select concat_ws(':', current_date, count(*) filter (where status = 'published'), max(published_at),
    count(*) filter (where status = 'rejected'))
from changeset
"""


def fingerprint(conn, stamp: dict[str, str]) -> str:
    row = conn.execute(FINGERPRINT_SQL).fetchone()
    files = sorted(raw.parsed("us.cog").glob("*.parquet"))
    return ":".join([row[0] if row else "", *stamp.values(), *(f"{f.name}{f.stat().st_mtime_ns}" for f in files)])


def refresh() -> str:
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
                conn.execute("create table if not exists coverage_cache_state (fingerprint text not null)")
                stamp = coverage_countries.inputs()
                commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
                ).stdout.strip()
                current = fingerprint(conn, stamp)
                previous = conn.execute("select fingerprint from coverage_cache_state").fetchone()
                if previous is not None and previous[0] == current:
                    built.append("nothing has changed since the last build")
                else:
                    built.append(build())
                    conn.execute("delete from coverage_cache_state")
                    conn.execute("insert into coverage_cache_state values (%s)", [current])
                conn.execute(
                    "update coverage_build set built_at = %s, commit = %s, inputs = %s",
                    [started[0], commit, json.dumps(stamp)],
                )
                coverage_countries.record(conn)
            conn.execute("select pg_advisory_unlock(%s)", [LOCK])
            pending = conn.execute("select built_at is null or requested_at > built_at from coverage_build").fetchone()
            if pending is None or not pending[0]:
                return "; ".join(built)


def build() -> str:
    started = time.monotonic()
    today = date.today()
    with connect() as conn:
        statewide_table.rebuild(conn)
        cells = coverage.matrix(conn, last=today.year)
        states = {fips: (st, name) for st, fips, name in conn.execute(coverage.STATES_SQL)}
        conn.execute(LOCAL_SQL)
        held: dict[str, dict[int, list]] = defaultdict(dict)
        for polity, year, n, precinct, regular, month, generals in conn.execute(HELD_SQL):
            held[str(polity)][year] = [n, precinct, regular, month, 0, 0, generals]
        for polity, year, n, shaped in conn.execute(LOCAL_SHAPES_SQL):
            held[str(polity)][year][4:6] = [n, shaped]
        polities = {str(pid): (kind, code, name, st) for pid, kind, code, name, st in conn.execute(POLITIES_SQL)}
        for statement in SHAPES_SQL.split(";\n\n"):
            conn.execute(statement)
        shapes = list(conn.execute(SHAPES_COUNT_SQL))
        turnout = list(conn.execute(TURNOUT_SQL))

        by_key = {(c["state"], c["year"], c["level"], c["stage"]): c for c in cells}
        season = {
            "primary": date(today.year, 9, 20),
            "general": date(today.year, 11, 2 + -date(today.year, 11, 1).weekday() % 7),
        }
        for stage, day in season.items():
            if today <= day:
                continue
            for (st, year, level, cell_stage), cell in list(by_key.items()):
                if cell_stage != stage or level not in STATE_LEVELS or year != today.year - OFFSETS.get(level, 4):
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
                        "target": coverage.target(st, level, today.year),
                    },
                )
                current["expected"] = max(current["expected"] or 0, cell["held"])

        known = dict(KNOWN)
        for st, period in JUDICIAL.items():
            for year in range(2000 + (st == "PA"), today.year + 1, period):
                if year == today.year and today <= (date(year, 4, 15) if st == "WI" else season["general"]):
                    continue
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
                "population": population,
                "population_basis": population_basis,
                "active": active,
                "elected": elected,
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
    for g in governments:
        group = (g["state"], g["kind"])
        if g["period"] is None:
            period, phase, cite = CALENDAR.get(
                (g["state"], g["function"] or ""),
                CALENDAR.get(group, CALENDAR.get((g["state"], "school"), (None, None, None)))
                if g["kind"] == "college"
                else CALENDAR.get(group, (None, None, None)),
            )
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
        if g["month"] is None and months[group]:
            g["month"] = months[group].most_common(1)[0][0]
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
            n, precinct, _, _, precincts, shaped, generals = years.get(year, [0, 0, 0, None, 0, 0, 0])
            earlier = years.get(year - 4, [0] * 7)[6] if year < today.year else 0
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
    goals: dict[tuple[str, str, str, int], list[int]] = defaultdict(lambda: [0] * 20)
    for st, year, level, total, complete, unshaped, units, undrawn in shapes:
        if level not in STATE_LEVELS or st not in names:
            continue
        cell = goals[("shapes" if year >= 2000 else "shapes_history", st, level, year)]
        cell[0] += total
        cell[1] += complete
        cell[10] += unshaped
        cell[11] += units
        cell[12] += undrawn
    for (st, year, level, _), c in by_key.items():
        if level not in STATE_LEVELS or st not in names:
            continue
        count = c["held"]
        base = max(0 if c["expected"] == "" else c["expected"], count)
        county = c["county_or_finer"]
        partial = c.get("county_partial", 0)
        bare = c.get("no_results", 0)
        totals = max(0, count - county - partial - bare)
        precinct = c["precinct"]
        major = c.get("major_only", 0)
        published = c.get("published", 0)
        if coverage.target(st, level, year) == "total":
            county = precinct = count - bare
            partial = totals = major = published = 0
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
                if goal == "precinct":
                    cell[8] += c["precincts"]
                    cell[9] += c["shaped_precincts"]
                    cell[18] += major
                    cell[19] += published
                else:
                    cell[13] += c.get("icpsr_only", 0)
        elif coverage.target(st, level, year) == "county" or level in ("state_upper", "state_lower"):
            cell = goals[("history", st, level, year)]
            for i, value in enumerate([base, county, base - count, bare, totals, partial, 0, c.get("with_turnout", 0)]):
                cell[i] += value
            cell[13] += c.get("icpsr_only", 0)
    for st, year, kind, complete, partial, coarse, absent in turnout:
        if st not in names:
            continue
        cell = goals[("turnout", st, kind, year)]
        cell[0] += complete + partial + coarse + absent
        cell[1] += complete
        cell[2] += absent
        cell[5] += partial
        cell[6] += coarse
    kinds = {g["id"]: (g["state"], g["kind"], g["population"] or 0, g["population_basis"]) for g in governments}
    for gid, year, expected, n, _, _, precincts, shaped in years_out:
        st, kind, population, population_basis = kinds[gid]
        if st not in names or not 2000 <= year <= today.year or not (expected or n):
            continue
        cell = goals[("local", st, kind, year)]
        cell[0] += 1
        cell[1] += n > 0
        cell[2] += n == 0
        cell[8] += precincts
        cell[9] += shaped
        cell[14] += population
        cell[15] += population if n > 0 else 0
        cell[16] += population if n == 0 else 0
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
        goals |= readiness.goals(conn, readiness.DAY)
        with conn.cursor().copy(
            "copy coverage_goal (goal, state, level, year, total, complete, absent, bare, totals, partial, coarse,"
            " turnout, precincts, shaped, unshaped, units, undrawn, icpsr, population_total, population_complete,"
            " population_absent, population_estimated, major, published) from stdin"
        ) as copy:
            for key, values in goals.items():
                copy.write_row([*key, *values])
    with connect() as conn:
        conn.execute(coverage.CACHE_SQL)
        country_goals, country_polities = coverage_countries.build(conn)
        with conn.transaction():
            coverage_countries.write(conn, country_goals, country_polities)
    return (
        f"{len(governments):,} governments ({len(link):,} linked), {len(years_out):,} government years, "
        f"{len(goals):,} goal rows, {len(country_polities)} other countries with {len(country_goals):,} goal rows "
        f"in {time.monotonic() - started:.0f}s"
    )
