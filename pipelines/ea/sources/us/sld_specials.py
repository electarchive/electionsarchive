import csv
import io
import json
import re
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.klarner import LOWER, UPPER
from ea.store import DATA, connect

SOURCE = "us.sld_specials"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
FLORIDA = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate={0.month}/{0.day}/{0.year}&OfficialResults=Y"
IOWA = "https://electionresults.iowa.gov/IA/{}/json/en/summary.json"
SOUTH_CAROLINA = "https://www.enr-scvotes.org/SC/{}/json/en/summary.json"
PENNSYLVANIA = (
    "https://www.electionreturns.pa.gov/api/ElectionReturn/GetOfficeData?officeId=0&methodName=GetOfficeDetails"
    "&electionid={}&electiontype=S&isactive=0"
)
WASHINGTON = "https://results.vote.wa.gov/results/{0:%Y%m%d}/export/{0:%Y%m%d}_legislative.csv"
CONNECTICUT = "https://ct.elstats.civera.com/api/download_contest/{}_table.csv?split_party=false"
GEORGIA = "https://results.sos.ga.gov/results/public/api/elections/Georgia/{}/ballot-items"
RHODE_ISLAND = "https://electionresults.ri.gov/results/public/api/elections/rhodeisland/{}/ballot-items"
LOUISIANA = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob={:%Y%m%d}/Votes_Multiparish.htm"
MASSACHUSETTS = "https://electionstats.state.ma.us/elections/download/{}/precincts_include:0/"
VIRGINIA = "https://va2.elstats.civera.com/api/download_contest/{}_table.csv?split_party=false"
GENERALS = (
    {
        f"{day}:VA": "https://va2.elstats.civera.com/api/download_search.csv?"
        + urlencode(
            {
                "search": json.dumps(
                    {
                        "global": {"years": {"from": day.year, "to": day.year}},
                        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
                        "contests": {"candidates": [], "divisions": [], "offices": [{"id": 8}, {"id": 9}]},
                        "specialElectionsOnly": False,
                        "voterStats": False,
                        "stages": [],
                    },
                    separators=(",", ":"),
                )
            }
        )
        for day in (date(2023, 11, 7), date(2025, 11, 4))
    }
    | {
        f"{day}:NJ:{chamber}": f"https://www.nj.gov/state/elections/assets/pdf/election-results/{day.year}/"
        f"{day.year}-official-general-results-{chamber}.pdf"
        for day, chamber in (
            (date(2023, 11, 7), "state-senate"),
            (date(2023, 11, 7), "general-assembly"),
            (date(2025, 11, 4), "general-assembly"),
        )
    }
    | {
        "2023-10-14:LA": LOUISIANA.format(date(2023, 10, 14)),
        "2023-11-18:LA:runoff": LOUISIANA.format(date(2023, 11, 18)),
        "2023-11-07:MS:single": "https://www.sos.ms.gov/elections/electionresults/2023General/State%20Certified%20Results/"
        "Legislative%20Office%20Composed%20of%20a%20Single%20County.pdf",
        "2023-11-07:MS:multi": "https://www.sos.ms.gov/elections/electionresults/2023General/State%20Certified%20Results/"
        "State%20District%20Offices%20and%20Multi-County%20Legislative%20District%20Offices.pdf",
    }
)
COUNTY_SHEETS = {
    "2023-01-10:MS:house:23": [
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election/Grenada.pdf",
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election/Lafayette.pdf",
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election/Webster.pdf",
    ],
    "2023-01-31:MS:house:23:runoff": [
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election%20Runoff/Grenada.pdf",
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election%20Runoff/Lafayette.pdf",
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election%20Runoff/Webster.pdf",
    ],
    "2025-03-25:MS:house:23": [
        "https://www.sos.ms.gov/elections/electionresults/2025LegislativeSpecial/HD%2023%20Special%20Election/Lafayette.pdf",
        "https://www.sos.ms.gov/elections/electionresults/2025LegislativeSpecial/HD%2023%20Special%20Election/Pontotoc%20County.pdf",
        "https://www.sos.ms.gov/elections/electionresults/2025LegislativeSpecial/HD%2023%20Special%20Election/Webster_Special.pdf",
    ],
}
MISSISSIPPI_QUALIFYING = (
    "https://www.sos.ms.gov/Content/documents/Elections/candidate%20qualifying/2023%20Qualifying%20List%202023.6.3.pdf"
)
GEORGIA_QUALIFYING = (
    "https://web.archive.org/web/2025id_/https://sos.ga.gov/sites/default/files/2024-06/List_of_Candidates_v5.zip"
)
GEORGIA_SPECIAL_2026 = "https://mvp.sos.ga.gov/s/qualifying-candidate-information?election=2026-05-12-special"
GEORGIA_QUALIFIED = {
    "2024-04-09:GA:house:139": (GEORGIA_QUALIFYING, "State House Representative, District 139"),
    "2024-05-07:GA:house:139:runoff": (GEORGIA_QUALIFYING, "State House Representative, District 139"),
    "2026-05-12:GA:house:177": (GEORGIA_SPECIAL_2026, "State House, District 177"),
    "2026-06-09:GA:house:177:runoff": (GEORGIA_SPECIAL_2026, "State House, District 177"),
}
MISSISSIPPI_READINGS = {
    "Dianne BD. Black": "Dianne D. Black",
    "Layd B. 'Rob' Roberson II": "Loyd B. 'Rob' Roberson II",
    "John ©. Read": "John O. Read",
    "Robert L. Johnson IIE": "Robert L. Johnson III",
    "Lestey Smith": "Lesley Smith",
    "Jeffery S$. Hale": "Jeffery S. Hale",
    "W. I. 'Doc! Harris Jr.": "W. I. 'Doc' Harris Jr.",
    "Josh Harkins 414,672": "Josh Harkins 14,672",
    "Scott Delano 7,615": "Scott DeLano 7,615",
    "Total for District 50 7,645": "Total for District 50 7,615",
}
MISSISSIPPI_PARTIES = {
    "house:6:Michael Chandler": "Democrat",
    "house:41:Kabir Karriem": "Democrat",
    "house:58:Jonathan McMillan": "Republican",
    "house:74:Lee Yancey": "Republican",
    "house:97:Ben Thompson": "Democrat",
    "house:114:Jeffrey S. 'Jeff' Guice": "Republican",
    "senate:23:Briggs Hopson": "Republican",
}
NJ_ORDINALS = [
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
]
NJ_TENS = {"twentieth": 20, "thirtieth": 30, "fortieth": 40, "twenty": 20, "thirty": 30}
NJ_COUNTIES = (
    "ATLANTIC|BERGEN|BURLINGTON|CAMDEN|CAPE MAY|CUMBERLAND|ESSEX|GLOUCESTER|HUDSON|HUNTERDON|MERCER|MIDDLESEX"
    "|MONMOUTH|MORRIS|OCEAN|PASSAIC|SALEM|SOMERSET|SUSSEX|UNION|WARREN"
)
PUBLISHERS = {
    "AR": "Arkansas Secretary of State",
    "TN:2023-03-14": "Shelby County Election Commission",
    "2026-02-03:NY:senate:61": "Erie County Board of Elections",
    "2025-11-04:NY:assembly:115": "New York State Board of Elections",
    "TN:2023-09-14": "Davidson County Election Commission",
    "MA": "Massachusetts Secretary of the Commonwealth",
    "NH": "New Hampshire Secretary of State",
    "MS": "Mississippi Secretary of State",
    "IN": "Indiana Election Division",
    "MI": "Michigan Bureau of Elections",
    "WI": "Wisconsin Elections Commission",
    "TX": "Texas Secretary of State",
    "MN": "Minnesota Secretary of State",
    "OK": "Oklahoma State Election Board",
    "KY": "Kentucky State Board of Elections",
    "VA": "Virginia Department of Elections",
    "AL": "Alabama Secretary of State",
    "CA": "California Secretary of State",
    "DE": "Delaware Department of Elections",
    "GA": "Georgia Secretary of State",
    "LA": "Louisiana Secretary of State",
    "ME": "Maine State Law and Legislative Reference Library",
    "RI": "Rhode Island Board of Elections",
    "CT": "Connecticut Secretary of the State",
    "FL": "Florida Division of Elections",
    "IA": "Iowa Secretary of State",
    "NJ": "New Jersey Division of Elections",
    "NY": "New York City Board of Elections",
    "PA": "Pennsylvania Department of State",
    "SC": "South Carolina Election Commission",
    "WA": "Washington Secretary of State",
}
SYSTEMS = {"GA": "two_round", "LA": "two_round", "MS": "two_round", "TX": "two_round", "CA": "top_two", "WA": "top_two"}
SPECIALS: dict[str, tuple[str, str, str | None]] = {
    "2023-05-16:KY:senate:28": (
        "transcribed",
        "https://elect.ky.gov/results/2020-2029/Documents/5-16-23%20Certification%20of%20Election%20Results%20for%2028th%20Senatorial%20District%20State%20Senate%20Special%20Election.pdf",
        None,
    ),
    "2023-11-07:KY:house:93": (
        "transcribed",
        "https://elect.ky.gov/results/2020-2029/2023ElectionReports/State%20Representative%2093rd%20Representative%20Distict%20Vote%20Totals.pdf",
        None,
    ),
    "2024-03-19:KY:house:24": (
        "transcribed",
        "https://elect.ky.gov/Documents/Certification%20of%20Election%20Results%20for%202024%20Special%20Election%2024th%20Representative%20District.pdf",
        None,
    ),
    "2024-03-19:KY:house:26": (
        "transcribed",
        "https://elect.ky.gov/Documents/Certification%20of%20Election%20Results%20for%202024%20Special%20Election%2026th%20Representative%20District.pdf",
        None,
    ),
    "2023-12-12:OK:senate:32": ("transcribed", "https://results.okelections.gov/OKER/?elecDate=20231212", None),
    "2024-02-13:OK:house:39": ("transcribed", "https://results.okelections.gov/OKER/?elecDate=20240213", None),
    "2025-05-13:OK:senate:8": ("transcribed", "https://results.okelections.gov/OKER/?elecDate=20250513", None),
    "2025-06-10:OK:house:71": ("transcribed", "https://results.okelections.gov/OKER/?elecDate=20250610", None),
    "2025-06-10:OK:house:74": ("transcribed", "https://results.okelections.gov/OKER/?elecDate=20250610", None),
    "2026-02-10:OK:house:35": ("transcribed", "https://results.okelections.gov/OKER/?elecDate=20260210", None),
    "2023-12-05:MN:house:52-b": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=159&scenario=StateRepresentative",
        None,
    ),
    "2024-03-19:MN:house:27-b": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=165&scenario=StateRepresentative",
        None,
    ),
    "2025-01-28:MN:senate:60": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=178&scenario=StateSenate",
        None,
    ),
    "2025-03-11:MN:house:40-b": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=181&scenario=StateRepresentative",
        None,
    ),
    "2025-04-29:MN:senate:6": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=184&scenario=StateSenate",
        None,
    ),
    "2025-09-16:MN:house:34-b": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=189&scenario=StateRepresentative",
        None,
    ),
    "2025-11-04:MN:senate:29": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=187&scenario=StateSenate",
        None,
    ),
    "2025-11-04:MN:senate:47": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=187&scenario=StateSenate",
        None,
    ),
    "2026-01-27:MN:house:47-a": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=194&scenario=StateRepresentative",
        None,
    ),
    "2026-01-27:MN:house:64-a": (
        "transcribed",
        "https://electionresults.sos.mn.gov/Results/Index?ersElectionId=194&scenario=StateRepresentative",
        None,
    ),
    "2023-11-07:TX:house:2": (
        "transcribed",
        "https://results.texas-election.com/static/data/election/49379/117/Districted.json",
        None,
    ),
    "2024-01-30:TX:house:2:runoff": (
        "transcribed",
        "https://results.texas-election.com/static/data/election/49681/667/Districted.json",
        None,
    ),
    "2024-05-04:TX:senate:15": (
        "transcribed",
        "https://results.texas-election.com/static/data/election/49698/114/Districted.json",
        None,
    ),
    "2025-11-04:TX:senate:9": (
        "transcribed",
        "https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/51830",
        None,
    ),
    "2026-01-31:TX:senate:9:runoff": (
        "transcribed",
        "https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/54613",
        None,
    ),
    "2026-05-02:TX:senate:4": (
        "transcribed",
        "https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/56181",
        None,
    ),
    "2025-01-28:IA:senate:35": ("clarity", IOWA.format("123074/356352"), "State Senate District 35"),
    "2025-03-11:IA:house:100": ("clarity", IOWA.format("123279/356897"), "State Representative District 100"),
    "2025-04-29:IA:house:78": (
        "transcribed",
        "https://sos.iowa.gov/sites/default/files/2025-05/CanvassSummaryReport%202025_HD78.pdf",
        None,
    ),
    "2025-08-26:IA:senate:1": ("clarity", IOWA.format("124080/359928"), "State Senate District 1"),
    "2025-12-09:IA:house:7": ("clarity", IOWA.format("125502/367702"), "State Representative District 7"),
    "2025-12-30:IA:senate:16": ("clarity", IOWA.format("125665/367717"), "Senate District 16"),
    "2025-03-25:SC:house:113": ("clarity", SOUTH_CAROLINA.format("123437/357172"), "District  113"),
    "2025-06-03:SC:house:50": ("clarity", SOUTH_CAROLINA.format("123663/358891"), "District 50"),
    "2025-12-23:SC:senate:12": ("clarity", SOUTH_CAROLINA.format("125770/367661"), "Senate, District  12"),
    "2025-12-23:SC:house:21": ("clarity", SOUTH_CAROLINA.format("125770/367661"), "Representatives, District  21"),
    "2025-12-23:SC:house:88": ("clarity", SOUTH_CAROLINA.format("125770/367661"), "Representatives, District  88"),
    "2025-06-10:FL:house:3": ("florida", FLORIDA.format(date(2025, 6, 10)), None),
    "2025-06-10:FL:house:32": ("florida", FLORIDA.format(date(2025, 6, 10)), None),
    "2025-06-10:FL:senate:19": ("florida", FLORIDA.format(date(2025, 6, 10)), None),
    "2025-09-02:FL:house:40": ("florida", FLORIDA.format(date(2025, 9, 2)), None),
    "2025-09-02:FL:senate:15": ("florida", FLORIDA.format(date(2025, 9, 2)), None),
    "2025-12-09:FL:house:90": ("florida", FLORIDA.format(date(2025, 12, 9)), None),
    "2025-12-09:FL:senate:11": ("florida", FLORIDA.format(date(2025, 12, 9)), None),
    "2025-03-25:PA:house:35": ("pennsylvania", PENNSYLVANIA.format(110), None),
    "2025-03-25:PA:senate:36": ("pennsylvania", PENNSYLVANIA.format(109), None),
    "2025-02-25:CT:house:40": ("elstats", CONNECTICUT.format(38457), None),
    "2025-02-25:CT:senate:21": ("elstats", CONNECTICUT.format(38459), None),
    "2025-04-22:CT:house:113": ("elstats", CONNECTICUT.format(38460), None),
    "2025-11-04:WA:senate:5": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 5 - state senator"),
    "2025-11-04:WA:senate:26": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 26 - state senator"),
    "2025-11-04:WA:senate:33": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 33 - state senator"),
    "2025-11-04:WA:senate:34": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 34 - state senator"),
    "2025-11-04:WA:senate:48": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 48 - state senator"),
    "2025-11-04:WA:house:33:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 33 - state representative pos. 1",
    ),
    "2025-11-04:WA:house:34:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 34 - state representative pos. 1",
    ),
    "2025-11-04:WA:house:41:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 41 - state representative pos. 1",
    ),
    "2025-11-04:WA:house:48:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 48 - state representative pos. 1",
    ),
    "2025-11-04:NJ:senate:35": (
        "transcribed",
        "https://www.nj.gov/state/elections/assets/pdf/election-results/2025/2025-official-general-results-state-senate-35ld.pdf",
        None,
    ),
    "2025-05-20:NY:senate:22": (
        "transcribed",
        (
            "https://vote.nyc/sites/default/files/pdf/election_results/2025/20250520Special%20Election/"
            "00302000022Kings%20State%20Senator%2022nd%20Senatorial%20District%20Recap.csv"
        ),
        None,
    ),
    "2025-06-24:AL:senate:5": (
        "transcribed",
        "https://www.sos.alabama.gov/sites/default/files/election-2025/StateCertificationofSpecialGeneralElection-SD-5.pdf",
        None,
    ),
    "2025-08-26:AL:house:11": (
        "transcribed",
        "https://www.sos.alabama.gov/sites/default/files/election-2025/StateCertificationofGeneralElectionResultsHD11.pdf",
        None,
    ),
    "2025-02-25:CA:assembly:32": (
        "transcribed",
        "https://elections.cdn.sos.ca.gov/special-elections/2025-ad32/ad32-official-canvass.xlsx",
        None,
    ),
    "2025-02-25:CA:senate:36": (
        "transcribed",
        "https://elections.cdn.sos.ca.gov/special-elections/2025-sd36/sd36-official-canvass.xlsx",
        None,
    ),
    "2025-02-15:DE:senate:1": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20250215_sd01/2025SpecialElection_SD01_OfficialResults.pdf",
        None,
    ),
    "2025-02-15:DE:senate:5": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20250215_sd05/2025SpecialElection_SD05_OfficialResults.pdf",
        None,
    ),
    "2023-04-04:WI:senate:8": (
        "transcribed",
        "https://elections.wi.gov/sites/default/files/documents/County%20by%20County%20Report_SD%208%20special%20election.pdf",
        None,
    ),
    "2023-07-18:WI:assembly:24": (
        "transcribed",
        "https://elections.wi.gov/sites/default/files/documents/County%20by%20County%20Report_for%20canvass%20for%20AD%2024%20special.pdf",
        None,
    ),
    "2024-07-30:WI:senate:4": (
        "transcribed",
        "https://elections.wi.gov/sites/default/files/documents/Ward%20by%20Ward%20Report_SD%204%20Special%20Election.xlsx",
        None,
    ),
    "2024-04-16:MI:house:13": (
        "transcribed",
        "https://mvic.sos.state.mi.us/votehistory/Index?type=C&electionDate=4-16-2024",
        None,
    ),
    "2024-04-16:MI:house:25": (
        "transcribed",
        "https://mvic.sos.state.mi.us/votehistory/Index?type=C&electionDate=4-16-2024",
        None,
    ),
    "2026-05-05:MI:senate:35": (
        "transcribed",
        "https://mvic.sos.state.mi.us/votehistory/Index?type=C&electionDate=5-5-2026",
        None,
    ),
    "2023-12-21:DE:house:37": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20231221_rd37/2023SpecialElection_RD37.pdf",
        None,
    ),
    "2023-01-10:MS:house:23": (
        "transcribed",
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election/Calhoun.pdf",
        None,
    ),
    "2023-01-31:MS:house:23:runoff": (
        "transcribed",
        "https://www.sos.ms.gov/elections/electionresults/2023StateSpecial/HD%2023%20Special%20Election%20Runoff/Calhoun.pdf",
        None,
    ),
    "2025-03-25:MS:house:23": (
        "transcribed",
        "https://www.sos.ms.gov/elections/electionresults/2025LegislativeSpecial/HD%2023%20Special%20Election/Calhoun_Results%20(2).pdf",
        None,
    ),
    "2024-11-05:IN:house:27:general": (
        "transcribed",
        "https://enr.indianavoters.in.gov/archive/2024General/download/AllOfficeResults.csv",
        None,
    ),
    "2025-03-25:MS:house:82": (
        "transcribed",
        "https://www.sos.ms.gov/elections/electionresults/2025LegislativeSpecial/HD%2082%20Special%20Election/Lauderdale.pdf",
        None,
    ),
    "2025-08-05:DE:house:20": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20250805_rd20/XB2025_OfficialResults.pdf",
        None,
    ),
    "2025-02-25:ME:house:24": (
        "transcribed",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2025/20250225/elec_20250225b.pdf",
        None,
    ),
    "2025-08-26:GA:senate:21": ("enhanced", GEORGIA.format("SpecialElection2625"), "Senate - District 21"),
    "2025-09-23:GA:senate:21:runoff": (
        "enhanced",
        GEORGIA.format("SpecialElectionDistrict21Runoff92325"),
        "Senate - District 21",
    ),
    "2025-11-04:GA:house:106": (
        "enhanced",
        GEORGIA.format("MunicipalGeneralSpecialElectionPSC11042025"),
        "Representatives - District 106",
    ),
    "2025-11-18:GA:senate:35": (
        "enhanced",
        GEORGIA.format("November18-2025-SD35SpecialElection"),
        "Senate - District 35",
    ),
    "2025-12-02:GA:house:106:runoff": (
        "enhanced",
        GEORGIA.format("December2nd2025Runoffs"),
        "Representatives - District 106",
    ),
    "2025-12-09:GA:house:23": (
        "enhanced",
        GEORGIA.format("December9th2025SpecialElectoinStateHouse23and121"),
        "Representatives - District 23",
    ),
    "2025-12-09:GA:house:121": (
        "enhanced",
        GEORGIA.format("December9th2025SpecialElectoinStateHouse23and121"),
        "Representatives - District 121",
    ),
    "2025-12-16:GA:senate:35:runoff": (
        "enhanced",
        GEORGIA.format("12162025SD35SpecialElectionRunoff"),
        "Senate - District 35",
    ),
    "2026-01-06:GA:house:23:runoff": ("enhanced", GEORGIA.format("Jan6HD23SERunoff"), "Representatives - District 23"),
    "2025-08-05:RI:senate:4": ("enhanced", RHODE_ISLAND.format("specaug25"), "Senator in General Assembly District 4"),
    "2025-02-15:LA:senate:14": ("louisiana", LOUISIANA.format(date(2025, 2, 15)), "14th Senatorial District"),
    "2025-02-15:LA:senate:23": ("louisiana", LOUISIANA.format(date(2025, 2, 15)), "23rd Senatorial District"),
    "2025-05-03:LA:house:67": ("louisiana", LOUISIANA.format(date(2025, 5, 3)), "67th Representative District"),
    "2025-05-13:MA:house:essex-6": ("elstats", MASSACHUSETTS.format(171339), None),
    "2025-06-10:MA:house:bristol-3": ("elstats", MASSACHUSETTS.format(171342), None),
    "2025-06-24:NH:house:strafford-12": (
        "transcribed",
        "https://www.sos.nh.gov/strafford-county-district-12-june-24-2025",
        None,
    ),
    "2025-11-04:NH:house:coos-5": (
        "transcribed",
        "https://www.sos.nh.gov/coos-county-district-5-november-4-2025",
        None,
    ),
    "2025-01-07:VA:house:26": ("elstats", VIRGINIA.format(161237), None),
    "2025-01-07:VA:senate:10": ("elstats", VIRGINIA.format(161235), None),
    "2025-01-07:VA:senate:32": ("elstats", VIRGINIA.format(161236), None),
    "2023-01-03:GA:house:7": ("enhanced", GEORGIA.format("2023JanHD7Spec"), "State House Dist 7"),
    "2023-01-31:GA:senate:11": ("enhanced", GEORGIA.format("2023JanSpec"), "State Senate 11 (Special)"),
    "2023-01-31:GA:house:7:runoff": ("enhanced", GEORGIA.format("2023JanSpec"), "State House Dist 7"),
    "2023-01-31:GA:house:119": ("enhanced", GEORGIA.format("2023JanSpec"), "Representatives - District 119"),
    "2023-02-28:GA:house:119:runoff": ("enhanced", GEORGIA.format("2023FebHD119Run"), "Representatives - District 119"),
    "2023-03-21:GA:house:75": ("enhanced", GEORGIA.format("2023MarHD75Spec"), "State House District 75"),
    "2023-05-16:GA:house:68": ("enhanced", GEORGIA.format("2023MayHD68Spec"), "State House Dist 68"),
    "2023-06-13:GA:house:68:runoff": ("enhanced", GEORGIA.format("2023JunHD68Run"), "State House Dist 68"),
    "2024-02-13:GA:senate:30": ("enhanced", GEORGIA.format("2024FebSpec"), "State Senate 30"),
    "2024-02-13:GA:house:125": ("enhanced", GEORGIA.format("2024FebSpec"), "Representatives - District 125"),
    "2024-03-12:GA:house:125:runoff": ("enhanced", GEORGIA.format("2024MarHD125Run"), "Representatives - District 125"),
    "2024-04-09:GA:house:139": ("enhanced", GEORGIA.format("2024AprHD139Spec"), "Representatives - District 139"),
    "2024-05-07:GA:house:139:runoff": (
        "enhanced",
        GEORGIA.format("2024MayHD139SpecRun"),
        "Representatives - District 139",
    ),
    "2026-01-20:GA:senate:18": ("enhanced", GEORGIA.format("January20SpecialElection"), "Senate - District 18"),
    "2026-02-17:GA:senate:18:runoff": (
        "enhanced",
        GEORGIA.format("Feb1726StateSenateDistrict18"),
        "Senate - District 18",
    ),
    "2026-03-10:GA:senate:53": ("enhanced", GEORGIA.format("March102026SpecialElection"), "Senate - District 53"),
    "2026-03-10:GA:house:94": (
        "enhanced",
        GEORGIA.format("March102026SpecialElection"),
        "Representatives - District 94",
    ),
    "2026-03-10:GA:house:130": (
        "enhanced",
        GEORGIA.format("March102026SpecialElection"),
        "Representatives - District 130",
    ),
    "2026-04-07:GA:senate:53:runoff": ("enhanced", GEORGIA.format("40726SpecialElection"), "Senate - District 53"),
    "2026-04-07:GA:house:94:runoff": (
        "enhanced",
        GEORGIA.format("40726SpecialElection"),
        "Representatives - District 94",
    ),
    "2026-04-07:GA:house:130:runoff": (
        "enhanced",
        GEORGIA.format("40726SpecialElection"),
        "Representatives - District 130",
    ),
    "2026-05-12:GA:house:177": ("enhanced", GEORGIA.format("51226SpecialElection"), "Representatives - District 177"),
    "2026-05-19:GA:senate:7": ("enhanced", GEORGIA.format("GeneralPrimary51926"), "Special State Senate - District 7"),
    "2026-06-09:GA:house:177:runoff": (
        "enhanced",
        GEORGIA.format("06092026SpecialElectionRunoff"),
        "Representatives - District 177",
    ),
    "2026-06-16:GA:senate:7:runoff": (
        "enhanced",
        GEORGIA.format("06162026GeneralPrimaryRunoff"),
        "Special State Senate - District 7",
    ),
    "2023-01-31:PA:senate:27": ("pennsylvania", PENNSYLVANIA.format(96), None),
    "2023-02-07:PA:house:32": ("pennsylvania", PENNSYLVANIA.format(97), None),
    "2023-02-07:PA:house:34": ("pennsylvania", PENNSYLVANIA.format(98), None),
    "2023-02-07:PA:house:35": ("pennsylvania", PENNSYLVANIA.format(99), None),
    "2023-05-16:PA:house:108": ("pennsylvania", PENNSYLVANIA.format(101), None),
    "2023-05-16:PA:house:163": ("pennsylvania", PENNSYLVANIA.format(102), None),
    "2023-09-19:PA:house:21": ("pennsylvania", PENNSYLVANIA.format(103), None),
    "2024-02-13:PA:house:140": ("pennsylvania", PENNSYLVANIA.format(104), None),
    "2024-04-23:PA:house:139": ("pennsylvania", PENNSYLVANIA.format(106), None),
    "2024-09-17:PA:house:195": ("pennsylvania", PENNSYLVANIA.format(107), None),
    "2024-09-17:PA:house:201": ("pennsylvania", PENNSYLVANIA.format(108), None),
    "2026-02-24:PA:house:42": ("pennsylvania", PENNSYLVANIA.format(113), None),
    "2026-02-24:PA:house:22": ("pennsylvania", PENNSYLVANIA.format(114), None),
    "2026-03-17:PA:house:79": ("pennsylvania", PENNSYLVANIA.format(115), None),
    "2026-03-17:PA:house:193": ("pennsylvania", PENNSYLVANIA.format(116), None),
    "2026-05-19:PA:house:196": ("pennsylvania", PENNSYLVANIA.format(118), None),
    "2026-08-18:PA:house:12": ("pennsylvania", PENNSYLVANIA.format(119), None),
    "2023-05-16:FL:house:24": ("florida", FLORIDA.format(date(2023, 5, 16)), None),
    "2023-12-05:FL:house:118": ("florida", FLORIDA.format(date(2023, 12, 5)), None),
    "2024-01-16:FL:house:35": ("florida", FLORIDA.format(date(2024, 1, 16)), None),
    "2026-03-24:FL:house:51": ("florida", FLORIDA.format(date(2026, 3, 24)), None),
    "2026-03-24:FL:house:87": ("florida", FLORIDA.format(date(2026, 3, 24)), None),
    "2026-03-24:FL:senate:14": ("florida", FLORIDA.format(date(2026, 3, 24)), None),
    "2023-01-10:VA:senate:7": ("elstats", VIRGINIA.format(160291), None),
    "2023-01-10:VA:house:24": ("elstats", VIRGINIA.format(160292), None),
    "2023-01-10:VA:house:35": ("elstats", VIRGINIA.format(160283), None),
    "2023-03-28:VA:senate:9": ("elstats", VIRGINIA.format(160942), None),
    "2024-01-09:VA:senate:9": ("elstats", VIRGINIA.format(160922), None),
    "2024-01-09:VA:house:48": ("elstats", VIRGINIA.format(160923), None),
    "2026-01-06:VA:senate:15": ("elstats", VIRGINIA.format(164992), None),
    "2026-01-06:VA:house:77": ("elstats", VIRGINIA.format(164993), None),
    "2026-01-13:VA:house:11": ("elstats", VIRGINIA.format(164994), None),
    "2026-01-13:VA:house:23": ("elstats", VIRGINIA.format(164995), None),
    "2026-01-20:VA:house:17": ("elstats", VIRGINIA.format(165667), None),
    "2026-02-10:VA:senate:39": ("elstats", VIRGINIA.format(165669), None),
    "2026-02-10:VA:house:5": ("elstats", VIRGINIA.format(165670), None),
    "2026-03-17:VA:house:98": ("elstats", VIRGINIA.format(165673), None),
    "2023-02-28:CT:house:6": ("elstats", CONNECTICUT.format(35494), None),
    "2023-02-28:CT:house:100": ("elstats", CONNECTICUT.format(35495), None),
    "2023-02-28:CT:house:148": ("elstats", CONNECTICUT.format(35496), None),
    "2024-01-23:CT:house:115": ("elstats", CONNECTICUT.format(38452), None),
    "2026-01-06:CT:house:25": ("elstats", CONNECTICUT.format(44017), None),
    "2026-01-13:CT:house:139": ("elstats", CONNECTICUT.format(44015), None),
    "2023-05-30:MA:house:suffolk-9": ("elstats", MASSACHUSETTS.format(160299), None),
    "2023-05-30:MA:house:suffolk-10": ("elstats", MASSACHUSETTS.format(160300), None),
    "2023-11-07:MA:senate:worcesterhampshire": ("elstats", MASSACHUSETTS.format(160410), None),
    "2024-03-05:MA:house:worcester-6": ("elstats", MASSACHUSETTS.format(160911), None),
    "2026-03-03:MA:senate:middlesex-1": ("elstats", MASSACHUSETTS.format(171923), None),
    "2026-03-31:MA:house:essex-5": ("elstats", MASSACHUSETTS.format(171924), None),
    "2026-02-07:LA:senate:3": ("louisiana", LOUISIANA.format(date(2026, 2, 7)), "3rd Senatorial District"),
    "2026-02-07:LA:house:37": ("louisiana", LOUISIANA.format(date(2026, 2, 7)), "37th Representative District"),
    "2026-02-07:LA:house:60": ("louisiana", LOUISIANA.format(date(2026, 2, 7)), "60th Representative District"),
    "2026-02-07:LA:house:97": ("louisiana", LOUISIANA.format(date(2026, 2, 7)), "97th Representative District"),
    "2026-02-07:LA:house:100": ("louisiana", LOUISIANA.format(date(2026, 2, 7)), "100th Representative District"),
    "2026-03-14:LA:senate:3:runoff": ("louisiana", LOUISIANA.format(date(2026, 3, 14)), "3rd Senatorial District"),
    "2026-03-14:LA:house:69": ("louisiana", LOUISIANA.format(date(2026, 3, 14)), "69th Representative District"),
    "2026-03-14:LA:house:100:runoff": (
        "louisiana",
        LOUISIANA.format(date(2026, 3, 14)),
        "100th Representative District",
    ),
    "2023-05-16:NH:house:hillsborough-3": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-special-elections/hillsborough-county-district-3-nashua-ward-4-may-16-2023",
        None,
    ),
    "2023-08-22:NH:house:grafton-16": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-2024-special-elections/grafton-county-district-16-enfield-august-22-2023",
        None,
    ),
    "2023-09-19:NH:house:rockingham-1": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-special-elections/rockingham-county-dist-1-northwood-nottingham-sept-19-2023",
        None,
    ),
    "2023-11-07:NH:house:hillsborough-3": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-2024-special-elections/hillsborough-county-district-3-nashua-ward-4-november-7-2023",
        None,
    ),
    "2024-01-23:NH:house:coos-1": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-2024-special-elections/coos-county-district-1-january-23-2024",
        None,
    ),
    "2024-01-23:NH:house:coos-6": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-2024-special-elections/coos-county-district-6-january-23-2024",
        None,
    ),
    "2024-03-12:NH:house:rockingham-21": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-2024-special-elections/rockingham-county-district-21-march-12-2024",
        None,
    ),
    "2024-03-12:NH:house:strafford-11": (
        "transcribed",
        "https://www.sos.nh.gov/elections/2023-2024-special-elections/strafford-county-district-11-march-12-2024",
        None,
    ),
    "2026-03-10:NH:house:carroll-7": (
        "transcribed",
        "https://www.sos.nh.gov/carroll-county-district-7-march-10-2026",
        None,
    ),
    "2023-03-14:TN:house:86": (
        "transcribed",
        "https://www.electionsshelbytn.gov/wp-content/uploads/2025/09/3.14.23-Election-Summary-OFFICIAL-RESULTS.pdf",
        None,
    ),
    "2023-09-14:TN:house:51": (
        "transcribed",
        "https://www.nashville.gov/sites/default/files/2024-03/OFFICIAL_Election_Results_by_Precinct_September_14_2023_Metropolitan_Runoff_and_District_51_Special_General.pdf?ct=1711729654",
        None,
    ),
    "2026-03-03:AR:house:70": (
        "transcribed",
        "https://enr-results-api.totalresults.com/Contest/GetContestResults?cId=arkansas&electionID=55355810-8dde-40ff-a1d2-5b8675226873&contestType=County",
        None,
    ),
    "2026-03-03:AR:senate:26": (
        "transcribed",
        "https://enr-results-api.totalresults.com/Contest/GetSingleContestResults?cId=arkansas&electionID=7f77a178-af02-40ec-92db-c5cc50882c68&contestType=State%20Senate&contestID=3252a2e9-592e-423f-8ff6-50fe267ba655",
        None,
    ),
    "2023-11-07:ME:house:50": (
        "transcribed",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2023/20231107/elec_20231107e.pdf",
        None,
    ),
    "2024-03-05:ME:house:122": (
        "transcribed",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2024/20240305/elec_20240305f.pdf",
        None,
    ),
    "2026-02-24:ME:house:94": (
        "transcribed",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2026/20260224/elec_20260224a.pdf",
        None,
    ),
    "2023-09-12:NY:assembly:27": (
        "transcribed",
        "https://vote.nyc/sites/default/files/pdf/election_results/2023/20230912Special%20Election/00402100027Queens%20Member%20of%20the%20Assembly%2027th%20Assembly%20District%20Recap.csv",
        None,
    ),
    "2024-02-13:NY:assembly:77": (
        "transcribed",
        "https://vote.nyc/sites/default/files/pdf/election_results/2024/20240213Special%20Election/00202100077Bronx%20Member%20of%20the%20Assembly%2077th%20Assembly%20District%20Recap.csv",
        None,
    ),
    "2026-02-03:NY:assembly:36": (
        "transcribed",
        "https://vote.nyc/sites/default/files/pdf/election_results/2026/20260203Special%20Election/00402200036Queens%20Member%20of%20the%20Assembly%2036th%20Assembly%20District%20Recap.csv",
        None,
    ),
    "2026-02-03:NY:assembly:74": (
        "transcribed",
        "https://vote.nyc/sites/default/files/pdf/election_results/2026/20260203Special%20Election/00102200074New%20York%20Member%20of%20the%20Assembly%2074th%20Assembly%20District%20Recap.csv",
        None,
    ),
    "2026-02-03:NY:senate:47": (
        "transcribed",
        "https://vote.nyc/sites/default/files/pdf/election_results/2026/20260203Special%20Election/00102100047New%20York%20State%20Senator%2047th%20Senatorial%20District%20Recap.csv",
        None,
    ),
    "2025-11-04:NY:assembly:115": ("transcribed", "https://results.elections.ny.gov/contest/5859", None),
    "2026-02-03:NY:senate:61": (
        "transcribed",
        "https://elections.erie.gov/Files/Election%20Results/2026/2026%20Special%20Canvass%20Book.pdf",
        None,
    ),
    "2023-11-07:RI:senate:1": (
        "transcribed",
        "https://rigov.s3.amazonaws.com/election/results/2023/general_election/ga_senator.json",
        None,
    ),
    "2024-01-09:AL:house:16": (
        "transcribed",
        "https://www.sos.alabama.gov/sites/default/files/election-2024/State%20Certification%20of%20General%20Election-HD16.pdf",
        None,
    ),
    "2023-11-07:SC:senate:42": ("clarity", SOUTH_CAROLINA.format("118372/324411"), "State Senate District 42"),
    "2024-01-02:SC:senate:19": ("clarity", SOUTH_CAROLINA.format("119333/325468"), "Senate, District  19"),
    "2024-04-02:SC:house:109": ("clarity", SOUTH_CAROLINA.format("120699/334766"), "Representatives District 109"),
    "2026-01-06:SC:house:98": ("clarity", SOUTH_CAROLINA.format("125869/367712"), "Representatives, District 98"),
}
TRANSCRIBED: dict[str, list[tuple[str | None, str | None, int]]] = {
    "2023-05-16:KY:senate:28": [
        ("Greg Elkins", "Republican", 7899),
        ("Robert Sainte", "Democratic", 4968),
        ("Richard Henderson", None, 3001),
    ],
    "2023-11-07:KY:house:93": [("Adrielle Camuel", "Democratic", 7924), ("Kyle Whalen", "Republican", 5841)],
    "2024-03-19:KY:house:24": [
        ("Courtney Gilbert", "Republican", 1025),
        ("John Pennington", "Democratic", 372),
        ("Craig Astor", None, 244),
    ],
    "2024-03-19:KY:house:26": [("Peyton Griffee", "Republican", 75)],
    "2023-12-12:OK:senate:32": [("Dusty Deevers", "REP", 3105), ("Larry Bush", "DEM", 2491)],
    "2024-02-13:OK:house:39": [
        ("Richard Prawdzienski", "LIB", 224),
        ("Erick Harris", "REP", 2507),
        ("Regan Raff", "DEM", 2246),
    ],
    "2025-05-13:OK:senate:8": [
        ("Bryan Logan", "REP", 3083),
        ("Nathan Brewer", "DEM", 1484),
        ("Steve Sanford", "IND", 436),
    ],
    "2025-06-10:OK:house:71": [("Beverly A. Atteberry", "REP", 326), ("Amanda Clinton", "DEM", 1805)],
    "2025-06-10:OK:house:74": [("Kevin Wayne Norwood", "REP", 1727), ("Amy Hossain", "DEM", 945)],
    "2026-02-10:OK:house:35": [("Dillon Travis", "REP", 2907), ("Luke Kruse", "DEM", 1624)],
    "2023-12-05:MN:house:52-b": [
        ("Bianca Virnig", "DFL", 3853),
        ("Charles Kuchlenz", "LIB", 59),
        ("Cynthia Lonnquist", "R", 2705),
        (None, None, 1),
    ],
    "2024-03-19:MN:house:27-b": [("Bryan Lawrence", "R", 1752), ("Brad Brown", "DFL", 319), (None, None, 2)],
    "2025-01-28:MN:senate:60": [("Doron Clark", "DFL", 7783), ("Abigail Wolters", "R", 746), (None, None, 32)],
    "2025-03-11:MN:house:40-b": [("Paul Wikstrom", "R", 3966), ("David Gottfried", "DFL", 9352), (None, None, 13)],
    "2025-04-29:MN:senate:6": [("Denise Slipy", "DFL", 8376), ("Keri Heintzeman", "R", 12752), (None, None, 30)],
    "2025-09-16:MN:house:34-b": [("Xp Lee", "DFL", 4331), ("Ruth Bittner", "R", 2785), (None, None, 5)],
    "2025-11-04:MN:senate:29": [("Louis McNutt", "DFL", 7459), ("Michael Holmstrom Jr", "R", 12328), (None, None, 22)],
    "2025-11-04:MN:senate:47": [
        ("Dwight Dorau", "R", 8383),
        ("Amanda Hemmingsen-Jaeger", "DFL", 13527),
        (None, None, 18),
    ],
    "2026-01-27:MN:house:47-a": [("Shelley Buck", "DFL", 1913), (None, None, 48)],
    "2026-01-27:MN:house:64-a": [("Meg Luger-Nikolai", "DFL", 5557), ("Dan Walsh", "R", 254), (None, None, 21)],
    "2023-11-07:TX:house:2": [
        ("Jill Dutton", "REP", 7156),
        ("Heath Hyde", "REP", 6081),
        ("Brent Money", "REP", 9011),
        ("Doug Roszhart", "REP", 2221),
        ("Krista Schild", "REP", 721),
        ("Kristen Washington", "DEM", 3170),
    ],
    "2024-01-30:TX:house:2:runoff": [("Jill Dutton", "REP", 6836), ("Brent Money", "REP", 6726)],
    "2024-05-04:TX:senate:15": [("Molly Cook", "DEM", 9370), ("Jarvis D. Johnson", "DEM", 7052)],
    "2025-11-04:TX:senate:9": [
        ("John Huffman", "REP", 19608),
        ("Taylor Rehmet", "DEM", 56565),
        ("Leigh Wambsganss", "REP", 42739),
    ],
    "2026-01-31:TX:senate:9:runoff": [("Taylor Rehmet", "DEM", 54473), ("Leigh Wambsganss", "REP", 40648)],
    "2026-05-02:TX:senate:4": [("Ron C. Angeletti", "DEM", 8700), ("Brett W. Ligon", "REP", 26130)],
    "2025-06-24:AL:senate:5": [
        ("Matt Woods", "Republican", 7715),
        ("Ryan Cagle", "Democratic", 1240),
        (None, None, 14),
    ],
    "2025-08-26:AL:house:11": [
        ("Heath Allbright", "Republican", 2783),
        ("Alexandria Braswell", "Democratic", 352),
        (None, None, 15),
    ],
    "2025-02-25:CA:assembly:32": [
        ("Stan W. Ellis", "Republican", 39410),
        ("Chris Cruz-Boone", "Democratic", 17474),
        ("Holli Willibey", "Republican", 2665),
        ("William Brown Jr.", "Libertarian", 1427),
    ],
    "2025-02-25:CA:senate:36": [
        ("Tony Strickland", "Republican", 81133),
        ("Jimmy D. Pham", "Democratic", 43730),
        ("Julie Diep", "Democratic", 22647),
        ("John Briscoe", "Republican", 10588),
    ],
    "2025-02-15:DE:senate:1": [
        ("Dan Cruce", "Democratic", 3654),
        ("Steven Washington", "Republican", 990),
        ('Riley "Liv" Figliola', "Nonpartisan", 88),
        (None, None, 7),
    ],
    "2025-02-15:DE:senate:5": [
        ("Ray Seigfried", "Democratic", 3780),
        ("Brent Burdge", "Republican", 2015),
        (None, None, 12),
    ],
    "2023-04-04:WI:senate:8": [
        ("Jodi Habush Sinykin", "Democratic", 37200),
        ("Dan Knodl", "Republican", 38492),
        (None, None, 59),
    ],
    "2023-07-18:WI:assembly:24": [
        ("Bob Tatterson", "Democratic", 5568),
        ("Paul Melotik", "Republican", 6455),
        (None, None, 3),
    ],
    "2024-07-30:WI:senate:4": [("Dora Drake", "Democratic", 4843), (None, None, 172)],
    "2024-04-16:MI:house:13": [("Mai Xiong", "Democratic", 5741), ("Ronald Singer", "Republican", 3007)],
    "2024-04-16:MI:house:25": [
        ("Peter Herzberg", "Democratic", 6373),
        ("Josh Powell", "Republican", 4096),
        ("Robert Stano", "U.S. Taxpayers", 220),
    ],
    "2026-05-05:MI:senate:35": [
        ("Chedrick Greene", "Democratic", 36583),
        ("Jason Tunney", "Republican", 24491),
        ("Ali K. Sledz", "Libertarian", 1058),
    ],
    "2023-12-21:DE:house:37": [
        ("Jane Hovington", "Democratic", 1068),
        ("Valerie Jones Giltner", "Republican", 1805),
        (None, None, 4),
    ],
    "2023-01-10:MS:house:23": [
        ("Perry Van Bailey", None, 900),
        ("Andy Clark", None, 317),
        ("Andrew 'Andy' Stepp", None, 1142),
    ],
    "2023-01-31:MS:house:23:runoff": [("Perry Van Bailey", None, 1195), ("Andrew 'Andy' Stepp", None, 1188)],
    "2025-03-25:MS:house:23": [
        ("Perry Van Bailey", None, 1190),
        ("Colby Bollinger", None, 939),
        ("Andy Clark", None, 171),
        ("Danny Lampley", None, 30),
        ("Travis E. Wright", None, 583),
    ],
    "2024-11-05:IN:house:27:general": [
        ("Sheila J. Klinker", "Democratic", 13398),
        ("Oscar Alvarez", "Republican", 8383),
    ],
    "2025-03-25:MS:house:82": [
        ("Joseph A. Denson", None, 410),
        ("Gregory Elliott", None, 556),
        ("Joseph Joe Norwood", None, 413),
    ],
    "2025-08-05:DE:house:20": [
        ("Alonna Berry", "Democratic", 4653),
        ("Nikki Miller", "Republican", 4536),
        (None, None, 4),
    ],
    "2025-02-25:ME:house:24": [("Sean Faircloth", "Democratic", 1431), ("Carolyn A. Fish", "Republican", 567)],
    "2025-06-24:NH:house:strafford-12": [("Billie Butler", "Democratic", 964), ("Ken Hilton", "Republican", 774)],
    "2025-11-04:NH:house:coos-5": [("Corinne Cascadden", "Democratic", 873), ("Marc D. Tremblay", "Republican", 878)],
    "2023-05-16:NH:house:hillsborough-3": [
        ("David M. Narkunas", "Republican", 60),
        ("Marc W. Plamondon", "Democratic", 151),
    ],
    "2023-08-22:NH:house:grafton-16": [("David Fracht", "Democratic", 495), ("John P. Keane", "Republican", 194)],
    "2023-09-19:NH:house:rockingham-1": [("Hal Rafter", "Democratic", 1571), ("James Guzofski", "Republican", 1240)],
    "2023-11-07:NH:house:hillsborough-3": [
        ("Paige Beauchemin", "Democratic", 409),
        ("David M. Narkunas", "Republican", 265),
    ],
    "2024-01-23:NH:house:coos-1": [("Cathleen A. Fountain", "Democratic", 851), ("Sean C. Durkin", "Republican", 1289)],
    "2024-01-23:NH:house:coos-6": [("Edith Tucker", "Democratic", 643), ("Michael P. Murphy", "Republican", 750)],
    "2024-03-12:NH:house:rockingham-21": [
        ("Jennifer Mandelbaum", "Democratic", 417),
        ("Carol A. Bush", "Republican", 245),
    ],
    "2024-03-12:NH:house:strafford-11": [("Erik Johnson", "Democratic", 1028), ("Joseph E. Bazo", "Republican", 525)],
    "2026-03-10:NH:house:carroll-7": [("Bobbi Boudman", "Democratic", 2207), ("Dale Fincher", "Republican", 2042)],
    "2023-03-14:TN:house:86": [("Justin J. Pearson", "Democratic", 443), (None, None, 10)],
    "2023-09-14:TN:house:51": [
        ("Aftyn Behn", "Democratic", 10654),
        ("David C. Hooven", "Republican", 3030),
        ("Annabelle Lee", "Independent", 408),
        (None, None, 159),
    ],
    "2026-03-03:AR:house:70": [("Alex Holladay", "Democratic", 4035), ("Bo Renshaw", "Republican", 2995)],
    "2026-03-03:AR:senate:26": [("Brad C. Simon", None, 8930), ("Adam Watson", None, 4011)],
    "2023-11-07:ME:house:50": [("David A. Sinclair", "Democratic", 2188)],
    "2024-03-05:ME:house:122": [
        ("Matthew D. Beck", "Democratic", 1218),
        ("Tristram J. Howard", "Not Enrolled", 176),
        ("Brendan K. Williams", "Unenrolled", 239),
    ],
    "2026-02-24:ME:house:94": [("Scott A. Harriman", "Democratic", 572), ("Janet I. Beaudoin", "Republican", 503)],
    "2023-09-12:NY:assembly:27": [
        ("Sam Berger", "Democratic", 2555),
        ("David P. Hirsch", "Republican", 1796),
        ("David P. Hirsch", "Conservative", 261),
        (None, None, 12),
    ],
    "2024-02-13:NY:assembly:77": [
        ("Landon C. Dais", "Democratic", 1143),
        ("Norman Sobe McGill", "Republican", 244),
        ("Norman Sobe McGill", "Conservative", 64),
        (None, None, 74),
    ],
    "2026-02-03:NY:assembly:36": [
        ("Diana C. Moreno", "Democratic", 3389),
        ("Diana C. Moreno", "Working Families", 3083),
        ("Rana Abdelhamid", "Queens For All", 1491),
        ("Mary Jobaida", "People First", 665),
        (None, None, 106),
    ],
    "2026-02-03:NY:assembly:74": [
        ("Keith Powers", "Democratic", 4203),
        ("Joseph W. Foley", "Republican", 719),
        ("Joseph W. Foley", "Conservative", 120),
        (None, None, 51),
    ],
    "2026-02-03:NY:senate:47": [
        ("Erik Bottcher", "Democratic", 11076),
        ("Erik Bottcher", "Working Families", 2249),
        ("Charlotte Friedman", "Republican", 1117),
        (None, None, 96),
    ],
    "2025-11-04:NY:assembly:115": [
        ("Michael S. Cashman", "Democratic", 18164),
        ("Michael S. Cashman", "Working Families", 1633),
        ("Brent M. Davison", "Republican", 16230),
        ("Brent M. Davison", "Conservative", 1813),
        (None, None, 25),
    ],
    "2026-02-03:NY:senate:61": [
        ("Jeremy J. Zellner", "Democratic", 19341),
        ("Dan R. Gagliardo", "Republican", 9853),
        ("Dan R. Gagliardo", "Conservative", 3033),
        (None, None, 175),
    ],
    "2023-11-07:RI:senate:1": [("Jacob Bissaillon", "DEM", 904), ("Niyoka Powell", "REP", 179), (None, None, 9)],
    "2024-01-09:AL:house:16": [
        ("John H. Underwood", "Democratic", 469),
        ("Bryan Brinyark", "Republican", 2372),
        (None, None, 4),
    ],
    "2025-04-29:IA:house:78": [("Angel Ramirez", "DEM", 2747), ("Bernie Hayes", "REP", 721), (None, None, 6)],
    "2025-11-04:NJ:senate:35": [
        ("Benjie E. Wimberly", "Democratic", 31676),
        ("Frank G. Filippelli", "Republican", 11957),
    ],
    "2025-05-20:NY:senate:22": [
        ("Sam Sutton", "Democratic", 8970),
        ("Nachman Carl Caller", "Republican", 3736),
        ("Nachman Carl Caller", "Conservative", 536),
        (None, None, 140),
    ],
}
PARTIES = {
    "democratic": "democratic-party",
    "democrat": "democratic-party",
    "dem": "democratic-party",
    "dfl": "democratic-party",
    "democratic-farmer-labor": "democratic-party",
    "republican": "republican-party",
    "rep": "republican-party",
    "r": "republican-party",
    "gop": "republican-party",
    "libertarian": "libertarian-party",
    "lbt": "libertarian-party",
    "MI:u.s. taxpayers": "MI:u-s-taxpayers-party",
    "lib": "libertarian-party",
    "lpf": "libertarian-party",
    "green": "green-party",
    "grn": "green-party",
    "gre": "green-party",
    "NY:conservative": "NY:conservative-party",
    "NY:working families": "NY:working-families-party",
    "NY:people first": "NY:people-first-party",
    "VA:forward": "VA:forward-party",
    "SC:unc": "SC:united-citizens",
    "SC:united citizens": "SC:united-citizens",
}
INDEPENDENT = {
    "",
    "npa",
    "non",
    "ind",
    "np",
    "nop",
    "una",
    "independent",
    "nonpartisan",
    "no party preference",
    "petitioning candidate",
    "noparty",
    "nopty",
    "unaffiliated",
    "unenrolled",
    "not enrolled",
    "queens for all",
}
SUFFIXES = {
    "clarity": ".json",
    "florida": ".txt",
    "pennsylvania": ".json",
    "washington": ".csv",
    "elstats": ".csv",
    "enhanced": ".json",
    "louisiana": ".json",
    "transcribed": "",
}
WRITE_INS = re.compile(r"^(write-?ins?|scatter(ing)?|write in)$", re.IGNORECASE)


def fetch() -> None:
    for system, url, _ in SPECIALS.values():
        raw.fetch(SOURCE, url, suffix=SUFFIXES[system] or Path(url.split("?")[0]).suffix)
        if system == "louisiana":
            raw.fetch(SOURCE, url.replace("Votes_", "RacesCandidates_"), suffix=".json")
    raw.fetch(SOURCE, MISSISSIPPI_QUALIFYING, suffix=".pdf")
    raw.fetch(SOURCE, GEORGIA_QUALIFYING, suffix=".zip")
    try:
        raw.cached(SOURCE, GEORGIA_SPECIAL_2026)
    except LookupError:
        raw.store(
            SOURCE,
            GEORGIA_SPECIAL_2026,
            (DATA / "inbox" / "manual" / "MF-046" / "2026-05-12_special" / "Qualified Candidates.csv").read_bytes(),
            suffix=".csv",
            retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
            media_type="text/csv",
        )
    for url in (url for sheets in COUNTY_SHEETS.values() for url in sheets):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for election, url in GENERALS.items():
        if ":LA" in election:
            raw.fetch(SOURCE, url, suffix=".json")
            raw.fetch(SOURCE, url.replace("Votes_", "RacesCandidates_"), suffix=".json")
        else:
            raw.fetch(SOURCE, url, suffix=Path(url.split("?")[0]).suffix)


def _title(text: str) -> str:
    text = " ".join(text.split())
    if text.isupper() or text.islower():
        text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
    return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)


def _party(state: str, text: str | None) -> str | None:
    label = re.sub(r"^\((?:prefers )?(.+?)(?: party)?\)$", r"\1", (text or "").strip().lower()).removesuffix(" party")
    if label in INDEPENDENT:
        return None
    found = PARTIES.get(f"{state}:{label}") or PARTIES.get(label)
    if found is None:
        raise ValueError(f"{state}: unknown party {text!r}")
    return found


def parse() -> None:
    rows: list[dict[str, object]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, GEORGIA_QUALIFYING).path) as archive:
        qualified = {
            (
                GEORGIA_QUALIFYING,
                re.sub(r" \(\w\)$", "", office),
                [
                    word
                    for word in re.sub(r"[^A-Z ]", "", name.upper()).split()
                    if word not in ("JR", "SR", "II", "III", "IV")
                ][-1],
            ): party
            for name, office, party in pl.read_excel(archive.read(archive.namelist()[0]), infer_schema_length=0)
            .select("Candidate Name", "Office Name", "Candidate Party")
            .iter_rows()
            if name and office
        }
    with raw.cached(SOURCE, GEORGIA_SPECIAL_2026).path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            qualified[
                (
                    GEORGIA_SPECIAL_2026,
                    row["CONTEST NAME"],
                    [
                        word
                        for word in re.sub(r"[^A-Z ]", "", row["CANDIDATE NAME"].upper()).split()
                        if word not in ("JR", "SR", "II", "III", "IV")
                    ][-1],
                )
            ] = row["POLITICAL PARTY"]
    for key, (system, url, select) in SPECIALS.items():
        state = key.split(":")[1]
        found: list[tuple[str | None, str | None, int]] = []
        path = raw.cached(SOURCE, url).path
        if system == "clarity":
            contests = [contest for contest in json.loads(path.read_text()) if select and select in contest["C"]]
            if len(contests) != 1:
                raise ValueError(f"{key}: {len(contests)} contests match {select!r}")
            for label, party, votes in zip(contests[0]["CH"], contests[0]["P"], contests[0]["V"], strict=True):
                label = re.sub(r"^(DEM|REP|LIB|GRN|CON|IND|NON|PET|UNC) ", "", label)
                found.append((None, None, int(votes)) if WRITE_INS.match(label) else (_title(label), party, int(votes)))
        elif system == "florida":
            code = "STS" if ":senate:" in key else "STR"
            totals: dict[tuple[str, str], int] = defaultdict(int)
            for row in csv.DictReader(io.StringIO(path.read_text()), delimiter="\t"):
                if row["RaceCode"] == code and int(row["Juris1num"]) == int(key.split(":")[3]):
                    label = " ".join(f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split())
                    totals[(label, row["PartyCode"])] += int(row["CanVotes"])
            found = [
                (label, None if party == "WRI" else party, votes)
                for (label, party), votes in totals.items()
                if party != "WRI" or (label and not WRITE_INS.match(label))
            ]
            if writes := sum(
                votes
                for (label, party), votes in totals.items()
                if party == "WRI" and (not label or WRITE_INS.match(label))
            ):
                found.append((None, None, writes))
        elif system == "pennsylvania":
            for offices in json.loads(json.loads(path.read_text()))["Election"].values():
                for office in offices:
                    for districts in office.values():
                        for district in districts:
                            for candidate in district["Candidates"]:
                                found.append(
                                    (
                                        _title(
                                            re.sub(
                                                r"(?i)^(.+?), (.+?)(?: (Jr|Sr|II|III|IV))?$",
                                                lambda m: " ".join(filter(None, (m[2], m[1], m[3]))),
                                                candidate["CandidateName"],
                                            )
                                        ),
                                        candidate["PartyName"],
                                        int(candidate["Votes"]),
                                    )
                                )
        elif system == "washington":
            assert select is not None
            for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
                if row["Race"].lower().endswith(select) and row["Race"].lower().startswith("legislative district"):
                    written = row["Candidate"].upper() == "WRITE-IN"
                    found.append(
                        (None, None, int(row["Votes"]))
                        if written
                        else (row["Candidate"], row["Party"], int(row["Votes"]))
                    )
        elif system == "elstats":
            table = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
            totals_row = next(row for row in table[2:] if row[0].startswith(("State ", "TOTALS")))
            writes = 0
            parties = table[1] + [""] * (len(table[0]) - len(table[1]))
            for label, party, votes in zip(table[0][1:], parties[1:], totals_row[1:], strict=False):
                if label in ("Same Day Registration (SDR)", "Total Votes Cast", "Total Ballots Cast", "Blanks", ""):
                    continue
                if not (WRITE_INS.match(label) or label == "All Others" or party.lower() in ("write in", "write-in")):
                    found.append((" ".join(label.split()), party, int(votes.replace(",", ""))))
                else:
                    writes += int(votes.replace(",", ""))
            if writes:
                found.append((None, None, writes))
        elif system == "enhanced":
            assert select is not None
            items = [
                item
                for item in json.loads(path.read_text())["data"]
                if item["name"][0]["text"].split("/")[0].strip().endswith(select)
            ]
            if len(items) != 1:
                raise ValueError(f"{key}: {len(items)} contests match {select!r}")
            for option in items[0]["summaryResults"]["ballotOptions"]:
                text = re.sub(r'"\s*([^"]*?)\s*"', r'"\1"', option["name"][0]["text"].replace('""', '"')).strip()
                match = re.fullmatch(r"(?:(DEM|REP|LIB|GRN|IND|NON) )?(.+?)(?: \((\w+)\)| - (\w+))?", text)
                assert match is not None
                label = match[2]
                party = (option.get("party") or {}).get("abbreviation") or match[1] or match[3] or match[4]
                found.append(
                    (None, None, option["voteCount"])
                    if option["isWriteIn"] or WRITE_INS.match(label)
                    else (label, party, option["voteCount"])
                )
        elif system == "louisiana":
            assert select is not None
            races = json.loads(raw.cached(SOURCE, url.replace("Votes_", "RacesCandidates_")).path.read_text())
            race = next(race for race in races["Races"]["Race"] if race["SpecificTitle"].endswith(select))
            choices = {choice["ID"]: choice["Desc"] for choice in race["Choice"]}
            counted = next(item for item in json.loads(path.read_text())["Races"]["Race"] if item["ID"] == race["ID"])
            for choice in counted["Choice"]:
                match = re.fullmatch(r"(.+) \((\w+)\)", choices[choice["ID"]])
                assert match is not None
                found.append((match[1], match[2], int(choice["VoteTotal"])))
        else:
            found = TRANSCRIBED[key]
        if not found:
            raise ValueError(f"{key}: no candidates")
        for label, party, votes in found:
            if key in GEORGIA_QUALIFIED and label is not None:
                party = party or qualified.get(
                    (
                        *GEORGIA_QUALIFIED[key],
                        [
                            word
                            for word in re.sub(r"[^A-Z ]", "", label.upper()).split()
                            if word not in ("JR", "SR", "II", "III", "IV")
                        ][-1],
                    )
                )
            rows.append({"key": key, "url": url, "candidate": label, "party": _party(state, party), "votes": votes})
    for election, url in GENERALS.items():
        day, state = election.split(":")[:2]
        if state == "MS":
            qualified: dict[str, list[tuple[str, str]]] = defaultdict(list)
            chamber = None
            for line in subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, MISSISSIPPI_QUALIFYING).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines():
                if line.startswith("Candidates for"):
                    chamber = "senate" if "State Senator" in line else "house" if "House of Rep" in line else None
                elif chamber and (
                    match := re.match(
                        r"^\s*.+?\s{2,}(\S.*?)\s+(\d+)\s+(Repu?blican|Democrat|Independent|Libertarian|Green)\b", line
                    )
                ):
                    party = "Republican" if match[3].startswith("Rep") else match[3]
                    qualified[f"{chamber}:{match[2]}"].append((match[1].split(",")[0], party))
            with tempfile.TemporaryDirectory() as folder:
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-gray", str(raw.cached(SOURCE, url).path), f"{folder}/page"], check=True
                )
                text = "\n".join(
                    subprocess.run(
                        ["tesseract", str(page), "-", "--psm", "6"], capture_output=True, text=True, check=True
                    ).stdout
                    for page in sorted(Path(folder).glob("page-*.pgm"))
                )
            chamber = None
            candidates: list[tuple[str, int]] = []
            for line in text.splitlines():
                line = re.sub(r"[‘’]", "'", line.strip().strip("|!‘' ").strip())
                for wrong, right in MISSISSIPPI_READINGS.items():
                    line = line.replace(wrong, right)
                if match := re.match(r"^State (Senate|House)(?: Of Rep)? \S+, District", line):
                    chamber, candidates = match[1].lower(), []
                elif re.match(r"^(District Attorney|Public Service|Transportation)", line):
                    chamber = None
                elif chamber and (match := re.match(r"^Total for District (\S+)\s+([\d,.]+)", line)):
                    district = int(re.sub(r"\D", "", match[1].translate(str.maketrans("Oi$S", "0155"))))
                    if sum(votes for _, votes in candidates) != int(re.sub(r"\D", "", match[2])):
                        raise ValueError(f"MS {chamber} {district}: candidates do not sum to the total")
                    for candidate, votes in candidates:
                        tokens = {token.lower().strip(".,'\"") for token in re.split(r"[\s-]+", candidate)}
                        parties = {
                            party
                            for last, party in qualified[f"{chamber}:{district}"]
                            if all(token.lower().strip(".,'\"") in tokens for token in re.split(r"[\s-]+", last))
                        }
                        party = MISSISSIPPI_PARTIES.get(f"{chamber}:{district}:{candidate}")
                        if party is None and len(parties) != 1:
                            raise ValueError(f"MS {chamber} {district}: no single party for {candidate}")
                        rows.append(
                            {
                                "key": f"{day}:MS:{chamber}:{district}:general",
                                "url": url,
                                "candidate": candidate,
                                "party": _party(state, party or parties.pop()),
                                "votes": votes,
                            }
                        )
                    chamber = None
                elif chamber and (match := re.match(r"^(.+?)\s+([\d,.]+)\s+[\d.,]+\s*%", line)):
                    candidates.append((match[1].strip(), int(re.sub(r"\D", "", match[2]))))
            continue
        if state == "LA":
            races = json.loads(raw.cached(SOURCE, url.replace("Votes_", "RacesCandidates_")).path.read_text())
            counted = {
                race["ID"]: race for race in json.loads(raw.cached(SOURCE, url).path.read_text())["Races"]["Race"]
            }
            for race in races["Races"]["Race"]:
                if not (match := re.match(r"State (Senator|Representative) -- +(\d+)", race["SpecificTitle"])):
                    continue
                key = f"{day}:LA:{'senate' if match[1] == 'Senator' else 'house'}:{match[2]}:" + (
                    "general-runoff" if election.endswith("runoff") else "general"
                )
                choices = {choice["ID"]: choice["Desc"] for choice in race["Choice"]}
                for choice in counted[race["ID"]]["Choice"]:
                    named = re.fullmatch(r"(.+) \((\w+)\)", choices[choice["ID"]])
                    assert named is not None
                    rows.append(
                        {
                            "key": key,
                            "url": url,
                            "candidate": named[1],
                            "party": _party(state, named[2]),
                            "votes": int(choice["VoteTotal"]),
                        }
                    )
            continue
        if state == "NJ":
            chamber = "senate" if election.endswith("senate") else "assembly"
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            district: int | None = None
            label: str | None = None
            slogan: str | None = None
            wrap = False
            for line in text.splitlines():
                follows, wrap = wrap, False
                if match := re.match(r"^([A-Za-z-]+) Legislative District:", line):
                    word = match[1].lower()
                    if word in NJ_ORDINALS:
                        district = NJ_ORDINALS.index(word) + 1
                    elif word in NJ_TENS:
                        district = NJ_TENS[word]
                    else:
                        tens, ones = word.split("-")
                        district = NJ_TENS[tens] + NJ_ORDINALS.index(ones) + 1
                    label = None
                    continue
                if (
                    district is None
                    or not line.strip()
                    or re.match(r"^(\d\d/\d\d/\d{4}|Name\s)", line)
                    or line.rstrip().endswith("Counties")
                ):
                    continue
                if match := re.match(r"^\s+Total\s+([\d,]+)$", line):
                    if label:
                        candidate = _title(re.sub(r"\s*\(w.*|[\s*]+$", "", label))
                        party = (
                            slogan
                            if slogan in ("DEMOCRATIC", "REPUBLICAN", "LIBERTARIAN PARTY", "GREEN PARTY")
                            else None
                        )
                        rows.append(
                            {
                                "key": f"{day}:NJ:{chamber}:{district}:general",
                                "url": url,
                                "candidate": candidate,
                                "party": _party(state, party),
                                "votes": int(match[1].replace(",", "")),
                            }
                        )
                        label = None
                    continue
                if match := re.match(rf"^\s{{40,}}(?:{NJ_COUNTIES})\s+(.+?)\s+[\d,]+$", line):
                    slogan = slogan or match[1]
                    continue
                if line[0] == " ":
                    continue
                part = re.split(r"\s\(w\)|\s\*|\s{2,}|\s(?=\d)|\sP\.O\.", line)[0].strip()
                if label is None:
                    label, slogan, wrap = part, None, True
                elif follows and not line.startswith("(bracketed"):
                    label += part if label.endswith("-") else " " + part
            continue
        totals: dict[tuple[str, str | None, str | None], int] = defaultdict(int)
        for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))):
            if (
                row["election_date"].startswith(day)
                and row["office_name"] in ("State Representative", "State Senate")
                and row["division_type"] == "Locality"
                and row["candidate_name"] not in ("Total Votes Cast", "Total Ballots Cast", "Undervotes", "Overvotes")
            ):
                chamber = "senate" if row["office_name"] == "State Senate" else "house"
                label = None if WRITE_INS.match(row["candidate_name"]) else row["candidate_name"]
                totals[
                    (
                        f"{day}:{state}:{chamber}:{row['district_name']}:general",
                        label,
                        row["candidate_party_name"] if label else None,
                    )
                ] += int(row["votes"])
        for (key, label, party), votes in totals.items():
            rows.append({"key": key, "url": url, "candidate": label, "party": _party(state, party), "votes": votes})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"key": pl.String, "url": pl.String, "candidate": pl.String, "party": pl.String, "votes": pl.Int64},
    ).sort("key", maintain_order=True).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        elections_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")
        }
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        published = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"])
        }
        known: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for body, person, ballot in conn.execute(
            """select distinct b.value, cd.person_id, cd.ballot_name
               from candidacy cd join contest_option o on o.id = cd.option_id
               join contest ct on ct.id = o.contest_id join election e on e.id = ct.election_id
               join external_id b on b.entity_id = ct.body_id and b.scheme = 'us.body'
               where e.date_from >= '2014-01-01' and cd.role = 'head' and cd.ballot_name is not null
                 and b.value ~ '^[A-Z]{2}:(senate|house|assembly|house-of-delegates|general-assembly|legislature)$'"""
        ).fetchall():
            known[(body.split(":")[0], slug(ballot))].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative specials and odd-year generals from each state's official results",
            polity="US",
        )
        published -= {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s and entity_id = any(%s::uuid[])",
                [
                    f"{SOURCE}.result_set",
                    list(cs.remainders("An undervote line in Virginia's results read as a candidate")),
                ],
            )
        }

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def unique(path: str, entity: UUID) -> str:
            if slugs.setdefault(path, entity) != entity:
                raise ValueError(f"slug {path} is taken")
            return path

        systems = {code: cs.key("us.electoral_system", code) for code in ("fptp", "two_round", "top_two", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, two-round, top-two, and block systems must already exist")
        sources: dict[tuple[str, date], UUID] = {}
        cited: set[str] = set()
        persons: dict[tuple[str, str], UUID] = {}
        contests: dict[str, UUID] = {}
        minted_parties: set[str] = set()
        for (key,), group in lines.group_by("key", maintain_order=True):
            assert isinstance(key, str)
            url = group["url"][0]
            parts = key.split(":")
            day, state, chamber, code = date.fromisoformat(parts[0]), parts[1], parts[2], parts[3]
            post = parts[4] if len(parts) > 4 and parts[4] not in ("runoff", "general", "general-runoff") else None
            runoff = key.endswith("runoff")
            regular = parts[-1].startswith("general")
            kind = "general" if regular else "special"
            publisher = PUBLISHERS.get(key) or PUBLISHERS.get(f"{state}:{day}") or PUBLISHERS[state]
            if (publisher, day) not in sources:
                title = f"Official results, {day:%B} {day.day}, {day.year} {kind} election"
                sources[(publisher, day)] = cs.source(publisher, title)
                cs.add(
                    "source",
                    id=sources[(publisher, day)],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
            source = sources[(publisher, day)]
            for cite in (
                (url, MISSISSIPPI_QUALIFYING)
                if regular and state == "MS"
                else (url, GEORGIA_QUALIFIED[key][0])
                if key in GEORGIA_QUALIFIED
                else (url, *COUNTY_SHEETS.get(key, ()))
            ):
                if cite not in cited:
                    cited.add(cite)
                    cs.artifact(raw.cached(SOURCE, cite), source)
            if chamber == "senate":
                body_name, body_key, scheme = UPPER.get(state, "Senate"), f"{state}:senate", "us.sldu"
            else:
                body_name = LOWER.get(state, "House of Representatives")
                body_key = f"{state}:{slug(body_name.removeprefix('State ').removesuffix(' of Representatives'))}"
                scheme = "us.sldl"
            body = cs.key("us.body", body_key)
            if body in cs.minted:
                raise ValueError(f"{key}: no body {body_key}")
            body_slug = body_key.split(":")[1]
            full = f"{state}:{code}"
            area, unit = cs.area(scheme, full), cs.unit(scheme, full)
            if area in cs.minted or unit in cs.minted:
                raise ValueError(f"{key}: no district {scheme} {full}")
            general = f"{day}:{state}"
            on_general = not (regular and runoff) and (regular or general in elections_known)
            election_key = general if on_general else f"{day}:{state}:{'runoff' if runoff else 'special'}"
            label = (
                ("runoff" if runoff else "election") if regular else "special election" + (" runoff" if runoff else "")
            )
            election = cs.key("us.election", election_key)
            if election in cs.minted and election_key not in elections_known:
                elections_known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="runoff" if runoff else "general" if regular else "special",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    election,
                    f"{day.year} {states[state]} general election"
                    if regular
                    else f"{day:%B} {day.day}, {day.year} {states[state]} {'runoff' if runoff else 'special'} election",
                )
                cs.add(
                    "slug",
                    path=unique(
                        f"us/{state.lower()}/{day}-{'runoff' if runoff else 'general' if regular else 'special'}",
                        election,
                    ),
                    entity_id=election,
                )
            sums = group.group_by("candidate").agg(pl.col("votes").sum())["votes"].to_list()
            if regular and state == "LA":
                etype = "larunoff" if runoff else "lafsettled" if max(sums) * 2 > sum(sums) else "lafunsettled"
            elif regular:
                etype = "g"
            else:
                etype = ("gs" if on_general else "s") + ("runoff" if runoff else "")
            contest_key = f"{day}:{state}:{body_slug}:{code}" + (f":{post}" if post else "") + f":{etype}"
            contest = contests[key] = cs.key("us.contest", contest_key)
            if contest_key in published and key not in GEORGIA_QUALIFIED:
                continue
            previous = None
            if runoff:
                first = max(
                    (
                        other
                        for other in contests
                        if other < key and other.split(":")[1:] == re.sub(r"[:-]runoff$", "", key).split(":")[1:]
                    ),
                    default=None,
                )
                previous = contests[first] if first else None
            seats = 2 if state == "NJ" and body_slug == "general-assembly" and regular else 1
            electoral_system = (
                "block" if seats > 1 else "fptp" if regular and state == "MS" else SYSTEMS.get(state, "fptp")
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=seats,
                term="full" if regular else "remainder",
                round_number=2 if previous else 1,
                previous_round_id=previous,
                electoral_system_id=systems[electoral_system],
                default_result_kind="counted",
            )
            shown = (
                f"District {code.upper().replace('-', '')}" if code[:1].isdigit() else _title(code.replace("-", " "))
            )
            where = shown + (f", Position {post}" if post else "")
            name(
                contest,
                f"{day.year} {states[state]} {body_name} {label}, {where}",
            )
            cs.add(
                "slug",
                path=unique(
                    f"us/{state.lower()}/{election_key.split(':', 2)[0]}-{'general' if on_general else 'runoff' if runoff else 'special'}"
                    f"/{'state-' if body_slug in ('senate', 'house') else ''}{body_slug}-{code}"
                    + (f"-post-{post}" if post else "")
                    + ("" if regular else "-special-election")
                    + ("-runoff" if runoff else ""),
                    contest,
                ),
                entity_id=contest,
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
                source_id=source,
                is_default=True,
                completeness=1,
            )
            remainder = group["candidate"].is_in(["Undervotes", "Overvotes"]).fill_null(False)
            total = int(group.filter(~remainder)["votes"].sum())
            ranked: list[tuple[int, UUID]] = []
            values: list[tuple[UUID | None, int, int]] = [
                (None, 6 if candidate == "Undervotes" else 5, int(votes))
                for candidate, votes in group.filter(remainder).select("candidate", "votes").iter_rows()
            ]
            for (candidate,), rows in group.filter(~remainder).group_by("candidate", maintain_order=True):
                if candidate is None:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    values.append((option, 1, int(rows["votes"].sum())))
                    continue
                assert isinstance(candidate, str)
                lines_ = list(rows.select("party", "votes").iter_rows())
                parties = [None if party is None else cs.key("us.party", party) for party, _ in lines_]
                for (party_key, _), party in zip(lines_, parties, strict=True):
                    if party is not None and party in cs.minted and party_key not in minted_parties:
                        minted_parties.add(party_key)
                        cs.add("party", id=party, kind="party", polity_id=cs.polity(f"US-{state}"))
                        name(party, party_key.split(":")[-1].replace("-", " ").title())
                matches = known.get((state, slug(candidate)), set())
                person = persons.get((state, slug(candidate))) or (
                    next(iter(matches))
                    if len(matches) == 1
                    else cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                )
                if person in cs.minted and (state, slug(candidate)) not in persons:
                    cs.add("person", id=person)
                    name(person, candidate)
                persons[(state, slug(candidate))] = person
                option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[0],
                    ballot_label=candidate,
                )
                head = cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head")
                cs.add(
                    "candidacy",
                    id=head,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=all(party is None for party in parties),
                )
                if len(lines_) > 1:
                    for (party_key, votes), party in zip(lines_, parties, strict=True):
                        line = cs.key("us.option", f"{contest_key}:{slug(candidate)}:{party_key or 'no-party'}")
                        if party is not None:
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                        cs.add(
                            "contest_option",
                            id=line,
                            contest_id=contest,
                            parent_option_id=option,
                            kind="candidate",
                            party_id=party,
                            ballot_label=f"{candidate} ({_title((party_key or 'no party').split(':')[-1].replace('-', ' '))})",
                        )
                        values.append((line, 1, votes))
                else:
                    values.append((option, 1, lines_[0][1]))
                ranked.append((int(rows["votes"].sum()), option))
            ranked.sort(key=lambda pair: -pair[0])
            if len(ranked) > seats and ranked[seats - 1][0] == ranked[seats][0]:
                raise ValueError(f"{key}: tie")
            if electoral_system == "two_round" and not runoff and ranked[0][0] * 2 <= total:
                for _, option in ranked[:2]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced")
            else:
                for _, option in ranked[:seats]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            for option, measure, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
