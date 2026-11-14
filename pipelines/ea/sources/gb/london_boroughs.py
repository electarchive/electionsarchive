import base64
import csv
import difflib
import functools
import html
import io
import itertools
import re
import struct
import subprocess
import tempfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.names import foreign_preferred
from ea.sources.gb import leap, leh
from ea.store import DATA, connect

SOURCE = "gb.london_boroughs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
PUBLISHER = "London Research Centre"
Word = tuple[float, float, float, float, str]
TITLES = {
    1990: "London Borough Council Elections 3 May 1990",
    1994: "London Borough Council Elections 5 May 1994, including results from the European Elections",
    1998: "London Borough Council Elections May 1998, including the Greater London Authority referendum results",
}
PARTY_NAMES = {
    "Liberal Democrat": "Liberal Democrats",
    "Labour and Co-operative": "Labour & Co-Operative Party",
    "Monster Raving Looney": "Monster Raving Loony Party",
}
METHODOLOGY = (
    "Read from the text layer of the London Research Centre's scanned report. The report lists each ward's "
    "candidates in vote order, and the candidates with the most votes, as many as the ward's seats, are shown as "
    "elected. Every party's London-wide votes, candidates, and seats add up to the report's party table, each ward's "
    "seats and electorate come from its ward voting statistics, and each ward's ballot papers equal its valid plus "
    "rejected papers. Candidate names are printed as surname and initials or forenames; a few the text layer garbled "
    "were checked against the page images."
)
NOTES = {
    1994: " In the 1994 report the text layer reads some 7s as 1s; every figure was checked against the party table "
    "and corrected on the page image where they disagreed. The report's summary page gives 30 fewer ballot papers "
    "than its ward voting statistics, which are stored as printed."
}
METHODOLOGY_1990 = (
    "Read by character recognition of the London Research Centre's scanned report, which has no usable text layer: "
    "each page was read at several resolutions and a figure was accepted only where the readings agreed, and the rest "
    "were checked by eye against the page images. The report lists each ward's candidates in vote order, and the "
    "candidates with the most votes, as many as the ward's seats, are shown as elected. Every party's London-wide "
    "votes, candidates, and seats add up to the report's party table, each ward's seats and electorate come from its "
    "ward voting statistics, and each ward's ballot papers equal its valid plus rejected papers. The statistics "
    "print Croydon's Heathfield ward with fewer valid ballot papers than its leading candidates' votes; both are "
    "stored as printed."
)
DATASET = "https://data.london.gov.uk/dataset/london-borough-council-elections-1998-24r9z"
DOWNLOAD = "https://data.london.gov.uk/download/24r9z"
BOOKS = {
    1990: (date(1990, 5, 3), f"{DOWNLOAD}/afda4d3b-5211-4410-886a-16af9d569c8c/LBCE_1990-5-3.pdf"),
    1994: (date(1994, 5, 5), f"{DOWNLOAD}/8a8faadf-3660-4617-864c-f4aa683f20e8/LBCE_1994-5-5.pdf"),
    1998: (date(1998, 5, 7), f"{DOWNLOAD}/6559d43c-4ea0-4b58-b705-94f84c3a549a/LBCE_1998-5-7.pdf"),
}
BOROUGHS = {
    "Barking and Dagenham": "E09000002",
    "Barnet": "E09000003",
    "Bexley": "E09000004",
    "Brent": "E09000005",
    "Bromley": "E09000006",
    "Camden": "E09000007",
    "Croydon": "E09000008",
    "Ealing": "E09000009",
    "Enfield": "E09000010",
    "Greenwich": "E09000011",
    "Hackney": "E09000012",
    "Hammersmith and Fulham": "E09000013",
    "Haringey": "E09000014",
    "Harrow": "E09000015",
    "Havering": "E09000016",
    "Hillingdon": "E09000017",
    "Hounslow": "E09000018",
    "Islington": "E09000019",
    "Kensington and Chelsea": "E09000020",
    "Kingston upon Thames": "E09000021",
    "Lambeth": "E09000022",
    "Lewisham": "E09000023",
    "Merton": "E09000024",
    "Newham": "E09000025",
    "Redbridge": "E09000026",
    "Richmond upon Thames": "E09000027",
    "Southwark": "E09000028",
    "Sutton": "E09000029",
    "Tower Hamlets": "E09000030",
    "Waltham Forest": "E09000031",
    "Wandsworth": "E09000032",
    "Westminster": "E09000033",
}
OCR_CODES = {
    "INO": "IND",
    "Ir~D": "IND",
    "CFe": "CFC",
    "SOP": "SDP",
    "CON.": "CON",
    "LO": "LD",
    "lD": "LD",
    "lO": "LD",
    "ILiB": "ILIB",
}
SCANNED = {1990: (10, 86, 109, 142)}
PARTIES = {
    1990: {
        "LAB": "Labour",
        "LABCP": "Labour and Co-operative",
        "CON": "Conservative",
        "CONAU": "Conservative",
        "LD": "Liberal Democrat",
        "LDFT": "Liberal Democrat",
        "LDHT": "Liberal Democrat",
        "LDSDP": "Liberal Democrat",
        "LDST": "Liberal Democrat",
        "SDPLD": "Liberal Democrat",
        "LDBT": "Liberal Democrat",
        "GRE": "Green",
        "SDP": "Social Democratic Party",
        "REA": "Residents' Association",
        "VAL": "Valley Party",
        "INDRE": "Independent Resident",
        "IND": "Independent",
        "TENRES": "Tenants and Residents",
        "CAMC": "Camden Charter",
        "INDLLT": "Independent Labour",
        "INDCON": "Independent Conservative",
        "CC": "Community Campaign",
        "INDLIB": "Independent Liberal",
        "INDLAB": "Independent Labour",
        "COMM": "Communist",
        "BN": "British National Party",
        "CREP": "Community Representative",
        "INDAPT": "Independent against Poll Tax",
        "ICP": "International Communist Party",
        "DC": "Democrats and Commonwealth",
        "TLR": "Tenants, Leaseholders and Residents",
        "RLGG": "Monster Raving Looney",
        "F": "Fellowship",
        "ISCG": "Isleworth South Community Group",
        "FRM": "Friends of Rainham Marshes",
        "INDTEN": "Independent Tenant",
        "ACLA": "Asian Council on Local Affairs",
        "REAP": "Residents' Action Party",
        "SOC": "Socialist",
        "COMMGB": "Communist Party of Great Britain",
        "NW": "Nursery Workers' Candidate",
        "CORP": "Corrective Party",
        "PC": "People's Choice",
        "INDI": "Independent Islamic",
        "TEN": "Tenants' Representative",
        "DCOMM": "Democratic Communist",
    },
    1994: {
        "LAB": "Labour",
        "LABCP": "Labour and Co-operative",
        "CON": "Conservative",
        "LD": "Liberal Democrat",
        "REA": "Residents' Association",
        "IRE": "Independent Resident",
        "TR": "Tenants and Residents",
        "GRE": "Green",
        "IND": "Independent",
        "BNP": "British National Party",
        "ILD": "Independent Liberal Democrat",
        "SDP": "Social Democratic Party",
        "ILAB": "Independent Labour",
        "IDEM": "Independent Democrat",
        "MLAB": "Militant Labour",
        "LFT": "Liberal Focus Team",
        "CMC": "Camden Charter",
        "TW": "Third Way",
        "F": "Fellowship",
        "IC": "Isleworth Community",
        "HC": "Hampstead Charter",
        "NLP": "Natural Law Party",
        "COM": "Communist",
        "II": "Island Independent",
        "PC": "The People's Choice",
        "CGB": "Communist Party of Great Britain",
        "KC": "Kilburn Charter",
        "MRL": "Monster Raving Looney",
        "NF": "National Front",
        "CFC": "Chocolate Fudge Cake Party",
    },
    1998: {
        "LAB": "Labour",
        "LABCP": "Labour and Co-operative",
        "CON": "Conservative",
        "LD": "Liberal Democrat",
        "GRE": "Green",
        "REA": "Residents' Association",
        "IRE": "Independent Resident",
        "TR": "Tenants and Residents",
        "IND": "Independent",
        "SLAB": "Socialist Labour",
        "BNP": "British National Party",
        "ILD": "Independent Liberal Democrat",
        "IC": "Isleworth Community",
        "NIND": "Newham Independent",
        "SP": "Socialist Party",
        "ILAB": "Independent Labour",
        "ID": "Independent Democrat",
        "TW": "Third Way",
        "SA": "Socialist Alliance",
        "UKIND": "UK Independent",
        "ND": "National Democrats",
        "NF": "National Front",
        "ICON": "Independent Conservative",
        "F": "Fellowship",
        "MRL": "Monster Raving Looney",
        "COM": "Communist",
        "POP": "Poplar Party",
        "IGRE": "Independent Green",
        "GP": "Greenwich People against cuts",
        "SACOM": "Socialist Alliance/Communist Party",
        "LIB": "Liberal Party",
        "C": "Corrective Party",
        "NLP": "Natural Law Party",
        "ILIB": "Independent Liberal",
    },
}
TOTALS = {
    1990: {
        "LAB": (2320553, 1878, 898),
        "LABCP": (57277, 36, 27),
        "CON": (2322959, 1849, 729),
        "CONAU": (2847, 2, 2),
        "LD": (487986, 914, 107),
        "LDFT": (319539, 284, 114),
        "LDHT": (6926, 6, 3),
        "LDSDP": (3385, 3, 2),
        "LDST": (1975, 4, 2),
        "SDPLD": (1648, 1, 1),
        "LDBT": (1394, 2, 0),
        "GRE": (151838, 431, 0),
        "SDP": (43531, 113, 4),
        "REA": (39828, 28, 19),
        "VAL": (14838, 60, 0),
        "INDRE": (14378, 12, 6),
        "IND": (13539, 37, 0),
        "TENRES": (8861, 19, 0),
        "CAMC": (4162, 22, 0),
        "INDLLT": (2028, 3, 0),
        "INDCON": (1710, 3, 0),
        "CC": (1061, 4, 0),
        "INDLIB": (1022, 3, 0),
        "INDLAB": (878, 5, 0),
        "COMM": (852, 9, 0),
        "BN": (851, 7, 0),
        "CREP": (769, 2, 0),
        "INDAPT": (656, 3, 0),
        "ICP": (554, 3, 0),
        "DC": (549, 7, 0),
        "TLR": (527, 4, 0),
        "RLGG": (445, 6, 0),
        "F": (415, 1, 0),
        "ISCG": (398, 1, 0),
        "FRM": (271, 2, 0),
        "INDTEN": (248, 1, 0),
        "ACLA": (246, 3, 0),
        "REAP": (237, 2, 0),
        "SOC": (109, 3, 0),
        "COMMGB": (101, 1, 0),
        "NW": (82, 1, 0),
        "CORP": (79, 3, 0),
        "PC": (79, 1, 0),
        "INDI": (77, 1, 0),
        "TEN": (77, 1, 0),
        "DCOMM": (66, 1, 0),
    },
    1994: {
        "LAB": (2304065, 1888, 1019),
        "LABCP": (44576, 29, 24),
        "CON": (1756760, 1839, 519),
        "LD": (1194207, 1621, 323),
        "REA": (46654, 35, 21),
        "IRE": (12636, 11, 6),
        "TR": (2624, 7, 0),
        "GRE": (52602, 219, 0),
        "IND": (17280, 67, 0),
        "BNP": (13731, 19, 0),
        "ILD": (10204, 17, 0),
        "SDP": (6020, 7, 4),
        "ILAB": (5230, 10, 1),
        "IDEM": (2882, 18, 0),
        "MLAB": (2001, 7, 0),
        "LFT": (1497, 2, 0),
        "CMC": (1342, 5, 0),
        "TW": (977, 3, 0),
        "F": (468, 1, 0),
        "IC": (452, 1, 0),
        "HC": (442, 2, 0),
        "NLP": (426, 10, 0),
        "COM": (388, 5, 0),
        "II": (386, 1, 0),
        "PC": (355, 3, 0),
        "CGB": (336, 5, 0),
        "KC": (296, 1, 0),
        "MRL": (231, 3, 0),
        "NF": (203, 1, 0),
        "CFC": (175, 1, 0),
    },
    1998: {
        "LAB": (1755944, 1905, 1040),
        "LABCP": (13649, 12, 10),
        "CON": (1383664, 1805, 538),
        "LD": (866386, 1580, 301),
        "GRE": (61702, 257, 2),
        "REA": (44221, 45, 20),
        "IRE": (16284, 28, 5),
        "TR": (275, 2, 0),
        "IND": (12047, 51, 0),
        "SLAB": (10663, 43, 0),
        "BNP": (5738, 30, 0),
        "ILD": (3758, 6, 0),
        "IC": (3726, 9, 1),
        "NIND": (2490, 9, 0),
        "SP": (1774, 8, 0),
        "ILAB": (1710, 6, 0),
        "ID": (1058, 4, 0),
        "TW": (851, 2, 0),
        "SA": (561, 6, 0),
        "UKIND": (539, 3, 0),
        "ND": (535, 3, 0),
        "NF": (438, 2, 0),
        "ICON": (425, 2, 0),
        "F": (375, 1, 0),
        "MRL": (359, 4, 0),
        "COM": (339, 3, 0),
        "POP": (323, 3, 0),
        "IGRE": (279, 1, 0),
        "GP": (198, 3, 0),
        "SACOM": (185, 3, 0),
        "LIB": (117, 1, 0),
        "C": (74, 1, 0),
        "NLP": (41, 1, 0),
        "ILIB": (15, 1, 0),
    },
}
LINES = {
    (14, "Lockington, Betty M. LD ty"): "Lockington, Betty M. LD 449",
    (21, "Gibbons, Jean F. LAB $27"): "Gibbons, Jean F. LAB 527",
    (22, "Deshmukh, Sadashivrao N. LAB LAT?"): "Deshmukh, Sadashivrao N. LAB 1,177",
    (27, "Rowley, Simon J. LD 44]"): "Rowley, Simon J. LD 441",
    (40, "Aitken, Penelope A. SDP Zit"): "Aitken, Penelope A. SDP 277",
    (44, "Ferguson, Jonas LAB Fz"): "Ferguson, Jonas LAB 712",
    (56, "Macdonald-Weissenborn, Leslie M.LD 233"): "Macdonald-Weissenborn, Leslie M. LD 233",
    (57, "Mutimer, William S. LAB 55/"): "Mutimer, William S. LAB 557",
    (57, "Twigg, David J. LDFT L279"): "Twigg, David J. LDFT 1,279",
    (59, "Waring, Amanda J.D. LAB __si1940"): "Waring, Amanda J.D. LAB 1,940",
    (62, "Poole, Beverly H. CON 51]"): "Poole, Beverly H. CON 511",
    (63, "Searle, David CON L177"): "Searle, David CON 1,177",
    (63, "Sommerfeld, Inge-Lore LD 225)"): "Sommerfeld, Inge-Lore LD 225",
    (79, "McNair, Christine M. LDFT Tl"): "McNair, Christine M. LDFT 751",
    (79, "Anas, Muhammed 297"): "Anas, Muhammed CC 297",
    (79, "Islam, Sirajul LAB LVF"): "Islam, Sirajul LAB 1,177",
    (86, "Haynes, Harry CON L317"): "Haynes, Harry CON 1,317",
    (27, "Perera, Panadure A.C LAB 1471"): "Perera, Panadure A.C LAB 1,871",
    (40, "Webb, Edward C. CON 1355"): "Webb, Edward C. CON 1,353",
    (40, "Chestnutt, Kathleen LD 243"): "Chestnutt, Kathleen LD 233",
    (53, "Tibbs, David C. SDP 590"): "Tibbs, David C. LDSDP 590",
    (72, "Cobb, Steven A. LAB 7718"): "Cobb, Steven A. LAB 778",
    (75, "Daws, Kevin LDFT 152"): "Daws, Kevin LDFT 752",
    (84, "Crawford, James C.M.L. CON S212"): "Crawford, James C.M.L. CON 3,212",
}
WARD_SEATS: dict[tuple[int, str, str], int] = {(1990, "Barking and Dagenham", "Village"): 3}
MISPRINTED = {(1990, "Croydon", "Heathfield")}
TOTAL_FIXES = {("Southwark", 4): 67954, ("Richmond upon Thames", 2): 52}
VOTING: dict[tuple[str, str], dict[int | str, int | None]] = {
    ("Redbridge", "Fairlop"): {0: 371, 1: 11000, 2: 3, 3: 8586, 4: 4265, 5: 497, 6: 4220, 7: 45},
    ("Camden", "Grafton Ad"): {0: 44, 1: 6900, 2: 2, 3: 4291, 4: 2002, 5: 467, 6: 1985, 7: 17},
    ("Greenwich", "Blackheath"): {0: 149, 1: 6000, 2: 2, 3: 5068, 4: 2824, 5: 557, 6: 2784, 7: 40},
    ("Hackney", "Haggerston"): {4: 2016},
    ("Havering", "Gidea Park"): {4: 2838},
    ("Southwark", "Liddle"): {"pct": None},
    ("Bexley", "Thamesmead East"): {5: 333},
    ("Harrow", "Roxbourne"): {5: 551},
}
WARDS = {
    "Addlscombe": "Addiscombe",
    "Amos": "Arnos",
    "Redel iffe": "Redcliffe",
    "Surblton Hili": "Surbiton Hill",
    "Wood cote": "Woodcote",
    "Wood cote and Coulsdon West": "Woodcote and Coulsdon West",
}
NAMES = {
    "Farbe~ Barbara A.": "Farbey, Barbara A.",
    "Fanusie, L10yda": "Fanusie, Lloyda",
    "Freeman, Robert 1": "Freeman, Robert J.",
    "~E~~nge, Suzanne": "L'Estrange, Suzanne",
    "Marchant, Margaret M1.": "Marchant, Margaret M L.",
    "Smith, Edward M.1.": "Smith, Edward M.I.",
    "Ware, Owen H.1.": "Ware, Owen H.I.",
    "Griffiths, Jonathan H1.": "Griffiths, Jonathan H.L.",
    "Theodorus, Tarik 11.": "Theodorus, Tarik I.L.",
    "Graber,lngeborg": "Graber, Ingeborg",
    "Khan,lmran": "Khan, Imran",
    "cummins. Mark": "Cummins, Mark",
}
SUMMARY = {1994: (5838, 1917, 4872088, 2246209, 2240669)}
SEATS = {("Bexley", "Falconwood"): 1}
FIXES = {
    ("Brent", "Queen's Park", "Crabb"): (857, "LAB"),
    ("Brent", "Queen's Park", "cummins"): (751, "LD"),
    ("Brent", "St Raphael's", "Etienne"): (1179, "LAB"),
    ("Brent", "Stonebridge", "Moloney"): (794, "LAB"),
    ("Brent", "Stonebridge", "Persaud"): (751, "LAB"),
    ("Bromley", "Lawrie Park and Kent House", "Foister"): (1277, "CON"),
    ("Merton", "Lower Morden", "Gallant"): (1417, "LAB"),
    ("Greenwich", "Burrage", "Duvall"): (743, "LAB"),
}
DIGITS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"})
GLC_PUBLISHER = "Greater London Council"
GLC = {
    1973: (date(1973, 4, 12), f"{DOWNLOAD}/0f08a697-1ad0-43fb-a74a-8694413960f7/GLCE_1973-4-12.pdf"),
    1977: (date(1977, 5, 5), f"{DOWNLOAD}/7cda0b8a-b910-49e6-b28d-5c8beb5859ac/GLCE_1977-5-5.pdf"),
    1981: (date(1981, 5, 7), f"{DOWNLOAD}/48ee651e-78fe-4a29-a8b6-606921ca51ee/GLCE_1981-5-7.pdf"),
}
GLC_TOTALS = {
    1973: (5313470, 1957661, {"LAB": 58, "CON": 32, "LIB": 2}),
    1977: (5183668, 2242064, {"CON": 64, "LAB": 28}),
    1981: (5086997, 2250118, {"LAB": 50, "CON": 41, "LIB": 1}),
}
GLC_PARTIES = {
    1973: {
        "CON": ("Conservative", 92, 743123),
        "UCON": ("Unofficial Conservative", 1, 485),
        "LAB": ("Labour", 92, 928034),
        "INDLAB": ("Independent Labour", 1, 227),
        "LIB": ("Liberal", 60, 244703),
        "COMM": ("Communist", 28, 12319),
        "SPGB": ("Socialist Party of Great Britain", 11, 1612),
        "IND": ("Independent", 8, 2923),
        "ACT": ("Action Party for Britain First", 6, 3063),
        "NATFR": ("National Front", 6, 9536),
        "ARPSWR": ("Air Road Public Safety White Resident", 1, 57),
        "IAMR": ("Anti-Mass Redevelopment", 1, 195),
        "AMSCIP": ("Anti-Motorway, Support Community Involvement in Planning", 1, 143),
        "GJHH": ("Great Joint Happiness Homes for All", 1, 60),
        "IAR": ("Independent Anti-Redevelopment", 1, 72),
        "ICP": ("Independent Centre Party", 1, 202),
        "IPAGE": ("Independent Parent Action Group for Education", 1, 325),
        "IRCA": ("Independent Ratepayers and Citizens Association", 1, 5110),
        "LRDLG": ("Lambeth Residents for Democratic Local Government", 1, 103),
        "NIP": ("National Independence Party", 1, 2924),
        "NPC": ("Non-Party Conservationist", 1, 1908),
        "SRR": ("Surbiton Residents and Ratepayers", 1, 303),
        "RR": ("The Ratepayers Representative", 1, 234),
    },
    1977: {
        "CON": ("Conservative", 92, 1177390),
        "INDCON": ("Independent Conservative", 1, 1025),
        "LAB": ("Labour", 91, 726854),
        "LABCOOP": ("Labour and Co-operative", 1, 10340),
        "NATFR": ("National Front", 91, 119060),
        "LIB": ("Liberal", 90, 174405),
        "INDLIB": ("Independent Liberal", 3, 878),
        "ABGLC": ("Abolish GLC", 31, 7869),
        "COMM": ("Communist", 24, 8267),
        "NP": ("National Party", 22, 8300),
        "ENGNAT": ("English Nationalist", 5, 685),
        "IND": ("Independent", 4, 605),
        "SIM": ("Socialist (International Marxist)", 4, 1930),
        "SPGB": ("Socialist Party of Great Britain", 4, 502),
        "PAP": ("People and Agrarian Party", 2, 1621),
        "CH": ("Co-operative Housing", 1, 67),
        "DMWRHO": ("Democratic Monarchist White Resident Home Owner", 1, 51),
        "E": ("Ecology", 1, 298),
        "FP": ("Fellowship Party", 1, 552),
        "MP": ("Medical Practitioner", 1, 290),
        "NB": ("New Britain", 1, 254),
        "NETCAT": ("North East Tottenham Campaign Against Traffic", 1, 646),
        "SR": ("Sales Representative", 1, 175),
    },
    1981: {
        "LAB": ("Labour", 87, 891409),
        "LABCP": ("Labour and Co-operative", 5, 48048),
        "CON": ("Conservative", 91, 890432),
        "CONRB": ("Conservative Right to Buy", 1, 3802),
        "LIB": ("Liberal", 83, 303368),
        "LIBFT": ("Liberal Focus Team", 2, 10176),
        "LIBF": ("Liberal Focus", 2, 6497),
        "LIBSD": ("Liberal and Social Democrat", 1, 2243),
        "LIBR": ("Liberal and Radical", 1, 1572),
        "NF": ("National Front", 41, 21582),
        "E": ("Ecology", 38, 17515),
        "SD": ("Social Democrat", 5, 12738),
        "NNF": ("New National Front", 18, 5877),
        "COMM": ("Communist", 16, 5275),
        "CM": ("Constitutional Movement", 23, 4041),
        "SDA": ("Social Democratic Alliance", 1, 3068),
        "WRP": ("Workers Revolutionary Party", 16, 2753),
        "INDSMC": ("Independent - Southall's Mr. Clean", 1, 2619),
        "SDFP": ("Social Democrat for Paddington", 1, 1845),
        "SDLM": ("Social Democrat (Labour Moderate)", 1, 1737),
        "EPR": ("Enoch Powell is Right", 4, 1731),
        "SLAG": ("Save London Action Group", 13, 1727),
        "SDP": ("Social Democratic Party", 1, 1544),
        "ABGLC": ("Abolish the GLC", 7, 1158),
        "ELWAR": ("East London Workers Against Racism", 3, 867),
        "NFCM": ("National Front Constitutional Movement", 2, 816),
        "INP": ("Irish National Party", 3, 793),
        "CSD": ("Council for Social Democracy", 1, 650),
        "FPAP": ("Fellowship Party and Anti-Pollution", 1, 637),
        "IND": ("Independent", 2, 622),
        "ABR": ("Abolition of Rates", 3, 503),
        "INDCON": ("Independent Conservative", 1, 362),
        "NBP": ("New Britain Party", 1, 346),
        "RAW": ("Ratepayers Watchdog", 1, 317),
        "FEACM": ("Full Employment and Anti-Common Market", 1, 201),
        "INDPR": ("Independent - Protect Rights of the Individual", 1, 164),
        "T": ("Teacher", 1, 162),
        "WP": ("Workers Party", 1, 145),
        "SLA": ("Save London Alliance", 1, 126),
        "RCOMM": ("Revolutionary Communist", 1, 116),
        "RP": ("Republican Party", 1, 111),
        "INDAB": ("Independent (Abolitionist)", 1, 103),
        "SPGB": ("Socialist Party of Great Britain", 1, 83),
        "AB": ("Abolitionist", 1, 82),
        "PSDMWR": ("Public Safety, Democratic Monarchist, White Resident", 1, 82),
        "FRAC": ("Fair Rates, Anti-Corruption", 1, 73),
    },
}
GLC_RESOLVED = {
    "Conservative": ("gb.parliament.party", "7"),
    "Labour": ("wikidata", "Q9630"),
    "Labour and Co-operative": ("wikidata", "Q9630"),
    "Liberal": ("gb.parliament.party", "18"),
    "National Front": ("gb.party", "national-front"),
    "Communist": ("gb.party", "communist-party-of-great-britain"),
    "Communist Party of Great Britain": ("gb.party", "communist-party-of-great-britain"),
    "Socialist Party of Great Britain": ("gb.party", "socialist-party-of-great-britain"),
    "Workers Revolutionary Party": ("gb.party", "workers-revolutionary-party"),
    "Social Democratic Party": ("gb.party", "social-democratic-party-1981"),
    "Green": ("gb.party", "green-party"),
}
GLC_OCR = {
    (1973, "OFFCON"): "CON",
    (1981, "INOCON"): "INDCON",
    (1981, "CCMM"): "COMM",
    (1981, "SOFP"): "SDFP",
}
GLC_LINES = {
    (1973, 10, "SOUTH (53,922)"): "CITY OF LONDON AND WESTMINSTER SOUTH (53,922)",
    (1977, 11, "SOUTH (51,393)"): "CITY OF LONDON AND WESTMINSTER SOUTH (51,393)",
    (1977, 8, "Egerton, J.R.S. Con peo WF 4"): "Egerton, J.R.S. Con 7,217",
    (1977, 11, "Porritt, J.E. = 298"): "Porritt, J.E. E 298",
    (1981, 6, "Ryan, Patrick S. LIB 35947"): "Ryan, Patrick S. LIB 3,947",
    (1981, 7, "EDMONTON (59,788)"): "EDMONTON (59,288)",
    (1981, 7, "Smith, William C. CON 7544"): "Smith, William C. CON 7,544",
    (1981, 9, "Morrell, Frances M. LAB 7+296"): "Morrell, Frances M. LAB 7,296",
    (1981, 9, "Green, Len csp 650"): "Green, Len CSD 650",
    (1981, 9, "Fox, Roger 6. SOLM 1,737"): "Fox, Roger G. SDLM 1,737",
    (1981, 9, "Kramer, Hilary J. LIB 72479"): "Kramer, Hilary J. LIB 7,479",
    (1981, 10, "Mearing-Smith, Nicholas P. CON 25444"): "Mearing-Smith, Nicholas P. CON 2,444",
    (1981, 12, "Spencer, Elizabeth N. CON 25458"): "Spencer, Elizabeth N. CON 2,458",
}
GLC_DIVISIONS = {
    "Deptford": "Lewisham Deptford",
    "Hammersmith North": "North Hammersmith",
    "Richmond": "Richmond upon Thames",
    "Southgate": "Enfield Southgate",
}
GLC_SHORT = {(1981, "CON"): 3}
GLC_DROPPED = {
    (1981, 12, "Bramall, Sir Ashley LAB 9,162"): ["Williams, Brian LIBFT 7,591"],
}
GLC_NAMES = {
    (1973, "Cox tia"): "Cox, L.J.",
    (1973, "Bonham, Mrs. 1.M.C., J.P."): "Bonham, Mrs. I.M.C., J.P.",
    (1973, "Bell, W.A.0.J."): "Bell, W.A.O.J.",
    (1973, "Mcintosh, A.R."): "McIntosh, A.R.",
    (1973, "Goodwin, Sir Reginald, C.B.E..D.L."): "Goodwin, Sir Reginald, C.B.E., D.L.",
    (1973, "Plummer, Sir Desmond, T.D., J.P..D.L."): "Plummer, Sir Desmond, T.D., J.P., D.L.",
    (1977, "Cameron, A.II.S."): "Cameron, A.I.S.",
    (1977, "Wykes, Mirs. J.K."): "Wykes, Mrs. J.K.",
    (1977, "Davies, W, M.N."): "Davies, W.M.N.",
    (1977, "Dah!, D.J."): "Dahl, D.J.",
    (1977, "Goodwin, Sir Reginald, C:B:E., -D.L."): "Goodwin, Sir Reginald, C.B.E., D.L.",
    (1981, "Billins, Roger 6G."): "Billins, Roger G.",
    (1981, "Saul, Ellen I a"): "Saul, Ellen E.",
    (1981, "Stubbs, Jane"): "Stubbs, Jane O.",
    (1981, "NcNeeney, Barry K."): "McNeeney, Barry K.",
    (1981, "Fryer, Reg MM."): "Fryer, Reg M.",
}
GLC_NOTES = {
    1981: " Table 1 prints one digit of Edmonton's electorate illegibly; the electorate of 59,288 is the figure in "
    "Table 2. The Conservative candidates' votes add up to 890,429, three fewer than the 890,432 in the party table; "
    "both the page images and the report's own text layer give the votes stored here. One line, Brian Williams "
    "(Liberal Focus Team) in Bethnal Green and Bow, was missed by the recognition and is taken from the text layer.",
}
GLC_METHODOLOGY = (
    "Read from the Greater London Council's scanned report by optical character recognition of each page at 400 dpi, "
    "one column at a time. Each electoral division returned one councillor, and the candidate with the most votes "
    "is the one the report shows as elected. Every party's candidates and votes add up to the report's table of "
    "candidates and votes by party, the divisions' votes add up to the total of valid ballot papers, their "
    "electorates add up to the total electorate, and the elected candidates match the printed composition of the "
    "council. The electoral divisions are the parliamentary constituencies of 1974 to 1983."
)

EARLY = {
    1964: (date(1964, 4, 9), f"{DOWNLOAD}/df4ffa06-63f1-4d4e-9617-f9faa0e64853/GLC_1964-4-9.pdf", (5, 7), 8),
    1967: (date(1967, 4, 13), f"{DOWNLOAD}/c14ad292-2609-414a-a997-b613fe0d1794/GLC_1967-4-13.pdf", (5, 8), 9),
    1970: (date(1970, 4, 9), f"{DOWNLOAD}/2fb55c6a-48f9-4d38-96df-ade095871445/GLCE_1970-4-9.pdf", (5, 9), 11),
}
EARLY_TOTALS = {
    1964: (5466756, 2413600, 7543699, {"CON": 36, "LAB": 64}),
    1967: (5319023, 2187789, 6862340, {"CON": 82, "LAB": 18}),
    1970: (5524384, 1943780, 6166936, {"CON": 65, "LAB": 35}),
}
EARLY_PARTIES = {
    1964: {
        "CON": ("Conservative", 100, 3147805),
        "LAB": ("Labour", 100, 3467634),
        "LIB": ("Liberal", 86, 775385),
        "COMM": ("Communist", 36, 102649),
        "IND": ("Independent", 9, 19983),
        "RA": ("Ratepayers' Association", 3, 17574),
        "NEWLIB": ("New Liberal", 3, 7421),
        "CHRIND": ("Christian Independent", 1, 2895),
        "UM": ("Union Movement", 2, 2353),
    },
    1967: {
        "CON": ("Conservative", 100, 3726692),
        "LAB": ("Labour", 100, 2374991),
        "LIB": ("Liberal", 100, 622019),
        "COMM": ("Communist", 38, 78958),
        "SPGB": ("Socialist Party of Great Britain", 14, 13021),
        "IND": ("Independent", 10, 14614),
        "CWP": ("Commonwealth Party of Great Britain", 4, 2854),
        "NUCT": ("National Union of Council Tenants", 4, 7909),
        "ITRPA": ("Islington Tenants and Ratepayers Political Association", 3, 3733),
        "NF": ("National Front", 3, 5519),
        "UM": ("Union Movement", 3, 5127),
        "U": ("Unionist", 2, 3019),
        "INDC": ("Independent Carnaby", 2, 1640),
        "ELP": ("European Labour Party", 1, 664),
        "JHNFP": ("John Hampden New Freedom Party", 1, 1580),
    },
    1970: {
        "CON": ("Conservative", 100, 3183781),
        "LAB": ("Labour", 100, 2494118),
        "LIB": ("Liberal", 98, 335818),
        "ULIB": ("Unofficial Liberal", 6, 4432),
        "HBR": ("Homes Before Roads", 85, 81215),
        "COMM": ("Communist", 40, 42906),
        "UM": ("Union Movement", 32, 10432),
        "SPGB": ("Socialist Party of Great Britain", 14, 5945),
        "IND": ("Independent", 5, 2614),
        "BC": ("Bread and Circus", 3, 595),
        "ARPSWR": ("Air Road Public Safety White Resident", 1, 366),
        "ANP": ("All-Night Party", 1, 133),
        "CNPA": ("Campaign for Non-Political Awareness", 1, 154),
        "JHNFP": ("John Hampden New Freedom Party", 1, 552),
        "MPD": ("Movement of People's Democracy", 1, 405),
        "POW": ("Powellite", 1, 854),
        "RR": ("Rent Rebel", 1, 1551),
        "YORR": ("Your Own Resident and Ratepayer", 1, 1065),
    },
}
EARLY_SHORT = {(1967, "LAB"): 300, (1967, "INDC"): -100}
EARLY_CODES = {"CAN": "CON", "NEWLIB": "NEWLIB", "CHRLND": "CHRIND", "UMM": "UM"}
EARLY_OTHERS = {
    ("BARKING", "Bailey H. J."): "MPD",
    ("BROMLEY", "Hansford-Miller F. H. Dr."): "JHNFP",
    ("HAMMERSMITH", "Coney M. P."): "POW",
    ("HARINGEY", "Cooper F. J. Miss"): "BC",
    ("HARINGEY", "Borowski K."): "BC",
    ("HARINGEY", "Kibble S. L."): "BC",
    ("HARINGEY", "Whittaker M."): "CNPA",
    ("HARINGEY", "Kerner P."): "ANP",
    ("KINGSTON UPON THAMES", "Scruby E."): "YORR",
    ("LAMBETH", "Boaks W. G."): "ARPSWR",
    ("TOWER HAMLETS", "O'Dell W."): "RR",
}
EARLY_LINES = {
    (1967, 6, 0): [
        "H. W. Cutler, O.B.E. .. (Con.) 39,791",
        "Captain W. E. Jones .. (Con.) 39,405",
    ],
    (1967, 6, 1): [
        "D. Currie .. .. .. (Lib.) 3,959",
        "H. B. Collins .. .. (Comm.) 1,458",
    ],
}
EARLY_NAMES = {
    "Charles Prendergast,B.E. M.": "Charles Prendergast, B.E.M.",
    "IIItyd Harrington": "Illtyd Harrington",
    "Mrs E. L. Prod ham": "Mrs E. L. Prodham",
    "Mrs Marjorie Mcintosh, B.A·t": "Mrs Marjorie McIntosh, B.A.",
    "Mrs A. MeV. Forbes-Cockell": "Mrs A. McV. Forbes-Cockell",
    "Mrs A. L1. Grieves": "Mrs A. Ll. Grieves",
    "Lt.-Col. Andrew Jardine D.L., J.P.": "Lt.-Col. Andrew Jardine, D.L., J.P.",
    "Wh ittaker M.": "Whittaker M.",
    "O'DeIIW.": "O'Dell W.",
    "Mrs P. F.lnman": "Mrs P. F. Inman",
    "William W.livingston": "William W. Livingston",
    "R. H.lnsoll": "R. H. Insoll",
    "Mrs J.1. Tatch": "Mrs J. I. Tatch",
    "D. A. Co berman": "D. A. Coberman",
    "L. A. Vito ria": "L. A. Vitoria",
    "Miss B. Chan non": "Miss B. Channon",
    "Edgar E. Reed.": "Edgar E. Reed",
    "D. M. Fielding, J.P•": "D. M. Fielding, J.P.",
    "Mrs Evelyn Denington, C.B.E., Han. F.R.I.B.A.": "Mrs Evelyn Denington, C.B.E., Hon. F.R.I.B.A.",
    "KingW. A.": "King W. A.",
    "Taylor Ruby G. N. Mrs.O.B.E.": "Taylor Ruby G. N. Mrs., O.B.E.",
    "Rowlandson SirGraham, M.B.E., J.P.": "Rowlandson Sir Graham, M.B.E., J.P.",
    "Foley G·. P.": "Foley G. P.",
    "Jardine Andrew Lt-Col. D.L., J.P.": "Jardine Andrew Lt-Col., D.L., J.P.",
    "Denington Evelyn Mrs. C.B.E., Han F.R.I.B.A.": "Denington Evelyn Mrs., C.B.E., Hon. F.R.I.B.A.",
    "Grieves Anna Mrs. LI": "Grieves Anna Mrs. Ll.",
    "Lang 0: M.": "Lang D. M.",
    "Hodson E. l.": "Hodson E. L.",
    "Bottom ley J.": "Bottomley J.",
    "Bryden W. M": "Bryden W. M.",
    "McLennan G": "McLennan G.",
    "Plummer Desmond T.D., J.P.": "Plummer Desmond, T.D., J.P.",
    "Goodwin Sir Reginald, C.B.E., D.l.": "Goodwin Sir Reginald, C.B.E., D.L.",
}
EARLY_SEATS = {
    "E09000002": 2,
    "E09000003": 4,
    "E09000004": 3,
    "E09000005": 4,
    "E09000006": 4,
    "E09000007": 3,
    "E09000008": 4,
    "E09000009": 4,
    "E09000010": 3,
    "E09000011": 3,
    "E09000012": 3,
    "E09000013": 3,
    "E09000014": 3,
    "E09000015": 3,
    "E09000016": 3,
    "E09000017": 3,
    "E09000018": 3,
    "E09000019": 3,
    "E09000020": 3,
    "E09000021": 2,
    "E09000022": 4,
    "E09000023": 4,
    "E09000024": 2,
    "E09000025": 3,
    "E09000026": 3,
    "E09000027": 2,
    "E09000028": 4,
    "E09000029": 2,
    "E09000030": 2,
    "E09000031": 3,
    "E09000032": 4,
    "westminster-city-of-london": 4,
}
EARLY_AREA = "westminster-city-of-london"
EARLY_SOURCES = {
    1964: ("London County Council", "General Election of Greater London Councillors, 9 April 1964"),
    1967: ("Greater London Council", "General Election of Greater London Councillors, 13 April 1967"),
    1970: ("Greater London Council", "Greater London Council Election, 9 April 1970"),
}
EARLY_AREA_NAME = "City of Westminster with the City of London and the Temples"
EARLY_TITLES = {
    "Mrs",
    "Mrs.",
    "Miss",
    "Sir",
    "Dr.",
    "(Dr.)",
    "Colonel",
    "Lt.-Col.",
    "Lt-Cmdr.",
    "Captain",
    "Lieut-Colonel",
}
EARLY_METHODOLOGY = (
    "Read from the text layer of the Greater London Council's scanned report, with each line placed by its position "
    "on the page. Each London borough elected two, three, or four councillors by block vote, and the City of London "
    "and the Temples voted with the City of Westminster. Every party's candidates and votes add up to the report's "
    "table of votes by party, the candidates' votes add up to the printed total of votes counted, each borough's "
    "electorate and number voting come from the report's table of electors and add up to its printed totals, and "
    "the elected candidates match the printed composition of the council."
)
EARLY_NOTES = {
    1964: " The report shows the elected candidates in heavy type, set flush with the borough's heading; the other "
    "candidates are indented. Each borough's number of elected candidates matches its seats in the 1970 report.",
    1967: " The report shows the elected candidates in heavy type, set flush with the borough's heading; the other "
    "candidates are indented. Two lines on page 6 are unreadable in the text layer and were read from the page "
    "image. The report's table of votes by party gives Labour 2,374,991 votes and Independent Carnaby 1,640, but its "
    "candidates' votes, read twice and checked on the page images, add up to 2,374,691 and 1,740; the candidates' "
    "votes are stored as printed. The Greenwich by-election of 29 June 1967, printed on page 8, is not part of this election.",
    1970: " The report prints the elected candidates in bold above a gap; how many each borough elected, and of "
    "which party, is taken from the report's summary of results by borough. Eleven candidates are marked only as "
    "other; each is given the description in the report's table of votes by party whose candidate count and votes "
    "they alone match. Candidates marked U. Lib. stood as Unofficial Liberals.",
}


ILEA_DAY = date(1986, 5, 8)
ILEA_URL = f"{DOWNLOAD}/93e12583-576d-419a-a227-fa431a67e4c0/ILEADE_1986-5-8.pdf"
ILEA_PUBLISHER = "London Residuary Body"
ILEA_TITLE = "Inner London Education Authority Direct Elections, 8 May 1986"
ILEA_PAGES = (6, 8)
ILEA_STATS = {"electorate": 10, "votes": 11, "rejected": 12}
ILEA_ERRATA = (2, {"bowandpoplar": "60,320"})
ILEA_TOTALS = (1728604, 1407312, 18888, {"LAB": 45, "CON": 11, "ALLIANCE": 2})
ILEA_PARTIES = {
    "LAB": ("Labour", 58, 664520),
    "CON": ("Conservative", 58, 427826),
    "SDPLIB": ("Social Democratic Party", 30, 153435),
    "LIBSDP": ("Liberal", 21, 91276),
    "LIBFT": ("Liberal Focus Team", 4, 32952),
    "LIBAFT": ("Liberal Alliance Focus Team", 3, 23017),
    "GRE": ("Green", 7, 7155),
    "IND": ("Independent", 3, 2306),
    "INDLAB": ("Independent Labour", 1, 1714),
    "COMM": ("Communist", 2, 1654),
    "RLS": ("Reform London Schools", 2, 710),
    "NF": ("National Front", 1, 425),
    "H": ("Humanist", 1, 322),
}
ILEA_ALLIANCE = {"SDPLIB", "LIBSDP", "LIBFT", "LIBAFT"}
ILEA_CODES = {"SDPUB": "SDPLIB", "UBSDP": "LIBSDP", "LIBAFF": "LIBAFT"}
ILEA_NAMES = {
    "Soskin, David G.F.AX": "Soskin, David G.F.A.X.",
    "MaHone, Ronald S.": "Mallone, Ronald S.",
    "Bames, Rosemary S.": "Barnes, Rosemary S.",
    "Kearney, Neil 1.": "Kearney, Neil J.",
    "McGregor, Edn'l D.": "McGregor, Edna D.",
    "Pringle, Alexander 1.S.": "Pringle, Alexander J.S.",
    "Berry, Christopher 1.": "Berry, Christopher J.",
    "Tuck, Robin TJ.": "Tuck, Robin T.J.",
    "Argyropulo, Dernitri C.": "Argyropulo, Demitri C.",
    "Monk:, Stuart A.": "Monk, Stuart A.",
    "Brown, CUve W.B.": "Brown, Clive W.B.",
    "lng, Wilfred D.": "Ing, Wilfred D.",
    "Brarnall, Ashley": "Bramall, Ashley",
}
ILEA_METHODOLOGY = (
    "Read from the text layer of the London Residuary Body's report, with each line placed by its position on the "
    "page; names the text layer garbled were checked against the page images. Each of Inner London's 29 "
    "parliamentary constituencies of 1983 elected two members by block vote, and the report shows the elected "
    "candidates in bold type, set flush with the constituency's heading. Every party's candidates and votes add up "
    "to the report's table of candidates and votes by party, each constituency's votes by Alliance, Conservative, "
    "Labour, and other candidates match its row in Table 2, the rejected ballot papers add up to the printed total, "
    "and the elected candidates match the printed membership of the authority. The report codes the Alliance "
    "candidates SDPLIB (Social Democratic Party), LIBSDP (Liberal), and Liberal Focus Team or Liberal Alliance Focus "
    "Team; all of them stood for the SDP–Liberal Alliance. A page of errata bound into the report corrects the "
    "electorate of Bow and Poplar to 60,320 and Inner London's to 1,728,604; the corrected figures are stored. The "
    "report gives no number voting by constituency. It marks the result in Southwark and Bermondsey, which the Alliance "
    "won, with an inquiry pending."
)

GLC_BY_PAGES = (20, 21)
GLC_BY_PARTIES = {
    code: ILEA_PARTIES[code][0] for code in ("LAB", "CON", "SDPLIB", "LIBSDP", "LIBAFT", "NF", "COMM", "RLS")
} | {
    "E": "Ecology",
    "ABGLC": "Abolish the GLC",
    "COMMGB": "Communist Party of Great Britain",
    "GLAML": "Gaitskell Labourite against Marxist Labour",
    "INDCON": "Independent Conservative",
    "PAA": "Pro-Abolition Alliance",
    "RCOMM": "Revolutionary Communist",
}
GLC_BY_CODES = ILEA_CODES | {"SDPLID": "SDPLIB", "LFFISDP": "LIBSDP"}
GLC_BY_NAMES = {
    "Wall, AlanJ.": "Wall, Alan J.",
    "Russell, Peter EN.": "Russell, Peter F.N.",
    "Pearson, Stephen 1.": "Pearson, Stephen J.",
    "Harris, Steve 1.": "Harris, Steve J.",
    "Cook:, David J.S.": "Cook, David J.S.",
    "Colerick:, George A.": "Colerick, George A.",
}
GLC_BY_DIVISIONS = {"Lewisham": "Lewisham West"}
GLC_BY_METHODOLOGY = (
    "Read from the text layer of Appendix B of the London Residuary Body's report on the Inner London Education "
    "Authority elections of 1986, which lists every by-election to the Greater London Council from May 1981 until "
    "the council's abolition, with the names checked against the page images. Each electoral division returned one "
    "councillor, and the report shows the elected candidate in bold type, set flush with the division's heading; "
    "the elected candidate polled the most votes in every case. The report prints each division's electorate and "
    "percentage poll but no number voting, so no turnout count is stored. The electoral divisions are the "
    "parliamentary constituencies of 1974 to 1983. The report heads the by-election of 20 September 1984 in "
    "Lewisham simply as Lewisham; it was caused by the resignation of Alan Herbert, who was elected for Lewisham "
    "West in 1981, and is stored as Lewisham West."
)
GLC_BY_EARLY = {1967: 8, 1973: 18, 1977: 16, 1981: 17}
GLC_BY_EARLY_AREAS = {1964: 1964, 1967: 1967, 1973: 1970}
GLC_BY_EARLY_HEADS = {
    "HOLBORN AND ST PANCRAS S~TH": "HOLBORN AND ST PANCRAS SOUTH",
    "CROYDON NOPTH EAST": "CROYDON NORTH EAST",
    "LEWISHAH WEST": "LEWISHAM WEST",
}
GLC_BY_EARLY_NAMES = {
    "Simpson, D. H.": "Simpson, D.H.",
    "Renouf, A.JW.": "Renouf, A.J.W.",
    "Cridland, VW. Mrs.": "Cridland, V.W. Mrs.",
    "Freeman, M. L.": "Freeman, M.L.",
    "BUl')ney, Helen": "Bunney, Helen",
    "Her·bert, Alan L.": "Herbert, Alan L.",
    "M. J. Coiner": "M. J. Colner",
}
GLC_BY_EARLY_PARTIES = {
    "CON": "Conservative",
    "CAN": "Conservative",
    "LAB": "Labour",
    "LIB": "Liberal",
    "COMM": "Communist",
    "IND": "Independent",
    "INDLAB": "Independent Labour",
    "FP": "Fellowship Party",
    "SPGB": "Socialist Party of Great Britain",
    "NATFR": "National Front",
    "NF": "National Front",
    "CONACM": "Conservative against the Common Market",
    "CANACM": "Conservative against the Common Market",
    "UM": "Union Movement",
    "PW": "Protest for Wingate",
}
GLC_BY_TACIT = (1964, 6, "HAMMERSMITH")
GLC_BY_EARLY_METHODOLOGY = {
    1964: "Read from the text layer of the London County Council's report on the Greater London Council election of "
    "1964, checked against the page image. A footnote on page 6, marked against Marjorie McIntosh, elected for "
    "Hammersmith, records that she died on 6 May 1964 and that Iris M. C. Bonham was elected at an uncontested "
    "by-election on 18 June 1964. The report gives no party for her, so none is stored.",
    1967: "Read from the text layer of the Greater London Council's report on its election of 1967, checked against "
    "the page image. Page 8 prints the Greenwich by-election of 29 June 1967, held after the resignation of Sheila "
    "Bradley, with the elected candidate set flush and the others indented. One of Greenwich's three seats was "
    "filled; the report prints no electorate or number voting for it.",
    1973: "Read from the text layer of the appendix to the Greater London Council's report on its election of 1973, "
    "which lists the by-elections from April 1970 to April 1973, checked against the page image. Each filled one of "
    "a borough's seats. The report shows the elected candidate in bold type, and that candidate polled the most "
    "votes; it prints a percentage voting but no electorate or number voting, so neither is stored.",
    1977: "Read from the text layer of the appendix to the Greater London Council's report on its election of 1977, "
    "which lists the by-elections from April 1973 to May 1977, checked against the page image. The electoral "
    "divisions are the parliamentary constituencies of 1974 to 1983. The report shows the elected candidate in bold "
    "type, and that candidate polled the most votes; it prints a percentage voting but no electorate or number "
    "voting, so neither is stored.",
    1981: "Read from the text layer of the appendix to the Greater London Council's report on its election of 1981, "
    "which lists the by-elections from May 1977 to May 1981, checked against the page image. The electoral "
    "divisions are the parliamentary constituencies of 1974 to 1983. The report shows the elected candidate first, "
    "and that candidate polled the most votes; it prints a percentage poll but no electorate or number voting, so "
    "neither is stored.",
}

BY_MODEL = "claude-haiku-5-5"
BY_LONGEST = 1568
BY_SYSTEM = (
    "You read the by-elections section of one borough from the London Research Centre's report on the London "
    "borough council elections of May 1998. The section has two columns: read the whole left column from top to "
    "bottom, then the whole right column. A by-election can start at the foot of the left column and continue at "
    "the top of the right column, so return the rows in reading order and do not repeat any. Row kinds: ward is a "
    "ward name in bold with a status such as No change or LAB gain from CON at the right; date is the polling day "
    "printed under it; candidate is a line with the name (surname, forenames), the party code, and the votes; "
    "cause is the line such as Resignation of Smith, John A (LAB) or Death of ... that closes the by-election. "
    "Copy names exactly as printed. The italic figure 1 has a long flag and can look like a 7: a 7 has a flat top "
    "bar, a 1 does not. Give votes as printed with their commas, or the word printed instead of a figure (such as "
    "Unopposed). Say whether each candidate line is printed in bold (the elected are bold). Skip the section "
    "heading, the running footer, and the page number."
)
BY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["ward", "date", "candidate", "cause"]},
                    "text": {"type": "string"},
                    "status": {"type": ["string", "null"]},
                    "party": {"type": ["string", "null"]},
                    "votes": {"type": ["string", "null"]},
                    "bold": {"type": ["boolean", "null"]},
                },
                "required": ["kind", "text", "status", "party", "votes", "bold"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rows"],
    "additionalProperties": False,
}


def fetch() -> None:
    for _, url in [*BOOKS.values(), *GLC.values()]:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for _, url, _, _ in EARLY.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    print(batch.ensure(SOURCE, "lbce1998-by", by_requests())[1])


def by_regions(book: list[list[Word]]) -> list[tuple[str, int, float, float]]:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    out: list[tuple[str, int, float, float]] = []
    borough = None
    within = False
    for index, page in enumerate(book):
        body = [w for w in page if 40 < w[0] < 560]
        rows = lines(body)
        text = [" ".join(w[4] for w in row) for row in rows]
        if text and re.match(r"(?:Rejected b|B)allot papers|Ward voting statistics", text[0]):
            break
        if len(text) > 1 and fold(text[0]) in folded and re.search(r"administration|control", text[1], re.IGNORECASE):
            borough, within = folded[fold(text[0])], False
            continue
        split = min(
            (w[1] for row in rows for w in row if re.fullmatch(r"By-elections?", w[4]) and w[0] < 60), default=None
        )
        if split is not None:
            within = True
        if not within or borough is None:
            continue
        top = 0.0 if split is None else split - 3
        words = [w for w in body if w[1] > top and w[3] < 795]
        if words:
            out.append((borough, index + 1, top, max(w[3] for w in words) + 6))
    return out


def by_key(sha256: str, page: int, read: str) -> str:
    return f"anthropic-batch:{BY_MODEL}/by-{read}/{sha256}/{page}"


def by_images(path: Path, page: int, width: float, top: float, bottom: float) -> list[bytes]:
    png = subprocess.run(
        ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(path)],
        check=True,
        capture_output=True,
    ).stdout
    image = Image.open(io.BytesIO(png))
    scale = image.width / width
    out = []
    for left, right in ((36, 563), (36, 302), (298, 563)):
        crop = image.crop((int(left * scale), int(top * scale), int(right * scale), int(bottom * scale)))
        crop.thumbnail((BY_LONGEST, BY_LONGEST))
        buffer = io.BytesIO()
        crop.save(buffer, "PNG")
        out.append(buffer.getvalue())
    return out


def by_requests() -> dict[str, batch.Want]:
    artifact = raw.cached(SOURCE, BOOKS[1998][1])
    sizes, book = pages(artifact.path)
    price_in, price_out = batch.PRICES[BY_MODEL]
    wanted: dict[str, batch.Want] = {}
    for borough, page, top, bottom in by_regions(book):
        whole, left, right = by_images(artifact.path, page, sizes[page - 1][0], top, bottom)
        for read, images, prompt in (
            ("whole", [whole], f"{borough}, page {page}: read the rows of this section."),
            (
                "columns",
                [left, right],
                f"{borough}, page {page}: the first image is the left column and the second the right column; "
                + "read the rows.",
            ),
        ):
            content: list[ImageBlockParam | TextBlockParam] = [
                ImageBlockParam(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
                )
                for data in images
            ]
            content.append(TextBlockParam(type="text", text=prompt))
            params = MessageCreateParamsNonStreaming(
                model=BY_MODEL,
                max_tokens=8000,
                system=BY_SYSTEM,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": BY_SCHEMA}},
            )
            pixels = sum(Image.open(io.BytesIO(data)).size[0] * Image.open(io.BytesIO(data)).size[1] for data in images)
            wanted[by_key(artifact.sha256, page, read)] = (params, (pixels / 750 * price_in + 3000 * price_out) / 1e6)
    return wanted


def pages(path: Path) -> tuple[list[tuple[float, float]], list[list[tuple[float, float, float, float, str]]]]:
    text = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    sizes = [(float(w), float(h)) for w, h in re.findall(r'<page width="([\d.]+)" height="([\d.]+)"', text)]
    return sizes, [
        [
            (float(a), float(b), float(c), float(d), html.unescape(w))
            for a, b, c, d, w in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', page
            )
        ]
        for page in text.split("<page ")[1:]
    ]


def lines(words: list[tuple[float, float, float, float, str]]) -> list[list[tuple[float, float, float, float, str]]]:
    rows: list[list[tuple[float, float, float, float, str]]] = []
    for word in sorted(words, key=lambda w: w[3]):
        if rows and abs(rows[-1][-1][3] - word[3]) <= 3:
            rows[-1].append(word)
        else:
            rows.append([word])
    out = []
    for row in rows:
        joined: list[tuple[float, float, float, float, str]] = []
        for word in sorted(row, key=lambda w: w[0]):
            if joined and re.fullmatch(r"\d{1,3},", joined[-1][4]) and re.fullmatch(r"\d{3}", word[4]):
                last = joined.pop()
                word = (last[0], min(last[1], word[1]), word[2], max(last[3], word[3]), last[4] + word[4])
            joined.append(word)
        out.append(joined)
    return out


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("&", "and"))


def number(text: str) -> int | None:
    text = text.translate(DIGITS)
    if not re.fullmatch(r"\d{1,3}(?:[,.]\d{3})*", text):
        return None
    return int(re.sub(r"[,.]", "", text))


def statistics(
    book: list[list[tuple[float, float, float, float, str]]], sizes: list[tuple[float, float]]
) -> pl.DataFrame:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    tables: list[tuple[str, list[tuple[str, str, list[int | None], int, list[float]]]]] = []
    current = {"turnout": None, "rejected": None}
    for index, page in enumerate(book):
        width, height = sizes[index]
        body = [w for w in page if 40 < w[0] < 555]
        text = " ".join(w[4] for w in body)
        if not re.match(r"(?:Rejected b|B)allot papers", text) or "per 1,000" in text:
            continue
        kind = "rejected" if "Rejected ballot papers" in text else "turnout"
        borough = current[kind]
        rows: list[tuple[str, str, list[int | None], int, list[float]]] = []
        for row in lines(body):
            label = [w for w in row if w[0] < 190]
            values = [w for w in row if w[0] >= 190]
            name = re.sub(r"\s*-\s*continued$", "", " ".join(w[4] for w in label))
            if fold(name) in folded and not values:
                borough = current[kind] = folded[fold(name)]
                continue
            if "Research Centre" in " ".join(w[4] for w in row):
                continue
            if borough is None or not label or not values or name in ("Ward", "London"):
                continue
            box = [
                row[0][0] / width,
                min(w[1] for w in row) / height,
                row[-1][2] / width,
                max(w[3] for w in row) / height,
            ]
            numbers = [number(w[4]) if "." not in w[4] or "," in w[4] else None for w in values]
            if "uncontested" in [w[4] for w in values]:
                rows.append((borough, name, [numbers[0]], index + 1, box))
                continue
            if fold(name) in folded and (numbers[0] or 0) > 10 or "(excluding" in name:
                continue
            rows.append((borough, name, numbers, index + 1, box))
        tables.append((kind, rows))
    out = []
    left = [row for kind, rows in tables if kind == "rejected" for row in rows]
    for borough, ward, values, page, box in (row for kind, rows in tables if kind == "turnout" for row in rows):
        twins = [row for row in left if row[0] == borough]
        best = max(twins, key=lambda row: difflib.SequenceMatcher(None, fold(ward), fold(row[1])).ratio(), default=None)
        if len(values) == 1:
            out.append({"borough": borough, "ward": ward, "seats": values[0]})
            if best is not None and len(best[2]) == 1:
                left.remove(best)
            continue
        if best is None or difflib.SequenceMatcher(None, fold(ward), fold(best[1])).ratio() < 0.75:
            print(f"no rejected ballot row: {borough} {ward}")
            continue
        left.remove(best)
        counts = best[2]
        if fold(ward) != fold(best[1]):
            print(f"statistics rows matched by likeness: {borough} {ward} / {best[1]}")
        if len(values) != 6 or len(counts) != 8:
            print(f"unreadable statistics row: {borough} {ward} {values} {counts}")
            continue
        seats, electorate, voting, _, day, postal = values
        *_, invalid, _, valid, _ = counts
        out.append(
            {
                "borough": borough,
                "ward": ward,
                "seats": seats,
                "electorate": electorate,
                "ballots": voting,
                "on_day": day,
                "postal": postal,
                "invalid": invalid,
                "valid": valid,
                "turnout_page": page,
                "turnout_box": box,
                "rejected_page": best[3],
                "rejected_box": best[4],
            }
        )
    return pl.DataFrame(out)


def results(
    book: list[list[tuple[float, float, float, float, str]]], codes: set[str], sizes: list[tuple[float, float]]
) -> pl.DataFrame:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    out = []
    borough = None
    section = None
    ward = None
    order = 0
    for index, page in enumerate(book):
        size = sizes[index]
        body = [w for w in page if 40 < w[0] < 560]
        rows = lines(body)
        text = [" ".join(w[4] for w in row) for row in rows]
        if text and re.match(r"(?:Rejected b|B)allot papers", text[0]):
            break
        if len(text) > 1 and fold(text[0]) in folded and re.search(r"administration|control", text[1], re.IGNORECASE):
            borough, section, ward = folded[fold(text[0])], None, None
            continue
        if borough is None:
            continue
        if section is None:
            section = "main"
        if section != "main":
            continue
        split = min(
            (w[1] for row in rows for w in row if re.fullmatch(r"By-elections?", w[4]) and w[0] < 60), default=None
        )
        if split is not None:
            section = "by"
            body = [w for w in body if w[1] < split - 2]
        for column in (0, 1):
            for row in lines([w for w in body if (w[0] < 300) == (column == 0)]):
                words = [w[4] for w in row]
                line = " ".join(words)
                if line == "Results" or "Research Centre" in line:
                    continue
                if len(words) >= 3 and words[-2] in OCR_CODES:
                    words[-2] = OCR_CODES[words[-2]]
                votes = None if words[-1] == "Unopposed" else number(words[-1])
                if len(words) >= 3 and words[-2] in codes and (votes is not None or words[-1] == "Unopposed"):
                    if ward is None:
                        raise ValueError(f"candidate before any ward on page {index + 1}: {line}")
                    order += 1
                    out.append(
                        ward
                        | {
                            "order": order,
                            "name": " ".join(words[:-2]),
                            "party": words[-2],
                            "votes": votes,
                            "raw_votes": words[-1],
                            "line_page": index + 1,
                            "y": round(row[0][1], 1),
                            "bbox": [
                                row[0][0] / size[0],
                                min(w[1] for w in row) / size[1],
                                row[-1][2] / size[0],
                                max(w[3] for w in row) / size[1],
                            ],
                        }
                    )
                    continue
                if number(words[-1]) is not None and len(words) >= 3:
                    print(f"unread candidate line on page {index + 1}: {line}")
                    continue
                status = [w for w in row if w[0] >= (215 if column == 0 else 470)]
                if status and row[0][0] < (60 if column == 0 else 315):
                    name = [w[4] for w in row if w[0] < status[0][0]]
                    if re.search(r"\d\s*[A-Z]+\s+gains?\s+from", " ".join(name)):
                        name = re.sub(r"\s*\d+\s*[A-Z]+\s+gains?\s+from.*$", "", " ".join(name)).split()
                    while "gain" in " ".join(w[4] for w in status) and re.fullmatch(r"\d+[A-Z]*", name[-1]):
                        name.pop()
                    ward = {
                        "borough": borough,
                        "ward": " ".join(name),
                        "status": " ".join(w[4] for w in status),
                        "page": index + 1,
                        "column": column,
                    }
                    order = 0
    return pl.DataFrame(out)


STATUS = re.compile(
    r"\bno changes?\b|\bnew wards?\b|\brevised wards?\b|\bgains? fro[mn]\b|\bextra seats?\b|\bunchanged\b",
    re.IGNORECASE,
)


def rejected_row(row: list[Word], width: float, height: float) -> dict | None:
    cells = [w for w in row if re.fullmatch(r"[\dOoalIS,.]+", w[4])]
    for start in range(len(cells) - 7):
        figures = cells[start : start + 8]
        text = [w[4].translate(DIGITS).replace("a", "0").replace(",", "") for w in figures[:7]]
        if figures[-1][0] - figures[0][0] < 400 and all(t.isdigit() for t in text):
            if sum(int(t) for t in text[:4]) != int(text[4]):
                continue
            return {
                "rejected": int(text[4]),
                "valid": int(text[6]),
                "box": [
                    figures[0][0] / width,
                    min(w[1] for w in figures) / height,
                    figures[7][2] / width,
                    max(w[3] for w in figures) / height,
                ],
            }
    return None


def statistics_1994(book: list[list[Word]], sizes: list[tuple[float, float]]) -> pl.DataFrame:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    out = []
    later: list[dict] = []
    borough = None
    for index, page in enumerate(book):
        width, height = sizes[index]
        body = [w for w in page if 40 < w[0] < 560]
        text = " ".join(w[4] for w in body)
        if not 193 <= index < 225:
            continue
        rejected_page = "Rejected ballot papers" in text[:200]
        rows = lines(body)
        starts = sorted(
            row[0][0] for row in rows if len(row) >= 6 and all(re.fullmatch(r"[\d,.]+", w[4]) for w in row[-5:])
        )
        if starts:
            body = [w for w in body if w[0] > starts[len(starts) // 2] - 15]
        for row in lines(body):
            if rejected_page and (found := rejected_row(row, width, height)) is not None:
                later.append(found | {"page": index + 1})
                continue
            split = len(row)
            while split > 1 and re.fullmatch(r"[\d,.]+", row[split - 1][4]):
                split -= 1
            label, values = row[:split], row[split:]
            name = re.sub(r"\s*-\s*continued$", "", " ".join(w[4] for w in label))
            if fold(name) in folded and not values:
                borough = folded[fold(name)]
                continue
            if (
                rejected_page
                or borough is None
                or not label
                or not values
                or name in ("Ward", "London", "Greater London")
            ):
                continue
            if "Research Centre" in " ".join(w[4] for w in row):
                continue
            numbers = [int(w[4].replace(",", "")) if re.fullmatch(r"\d[\d,]*", w[4]) else None for w in values]
            if "uncontested" in [w[4].lower() for w in values]:
                out.append({"borough": borough, "ward": name, "seats": numbers[0]})
                continue
            if len(values) == 5 and (borough, name) in SEATS:
                numbers = [SEATS[borough, name], *numbers]
                values = [values[0], *values]
            if len(values) != 6:
                if len(values) > 2:
                    print(f"unreadable statistics row: {borough} {name} {[w[4] for w in values]}")
                continue
            seats, electorate, voting, _, polling, postal = numbers
            box = [
                row[0][0] / width,
                min(w[1] for w in row) / height,
                row[-1][2] / width,
                max(w[3] for w in row) / height,
            ]
            if folded.get(fold(name)) == borough and numbers[0] is not None and numbers[0] > 3:
                continue
            if None in (*numbers[:3], *numbers[4:]):
                raise ValueError(f"{borough} {name}: unread figure in {[w[4] for w in values]}")
            if voting != (polling or 0) + (postal or 0):
                raise ValueError(
                    f"{borough} {name}: {voting} ballot papers, {polling} at polling stations and {postal} postal"
                )
            out.append(
                {
                    "borough": borough,
                    "ward": name,
                    "seats": seats,
                    "electorate": electorate,
                    "ballots": voting,
                    "turnout_page": index + 1,
                    "turnout_box": box,
                }
            )
    at = 0
    total = 0
    for name in dict.fromkeys(row["borough"] for row in out):
        wards = [row for row in out if row["borough"] == name]
        mine = later[at : at + len(wards) + 1]
        at += len(wards) + 1
        total += mine[-1]["valid"] if mine else 0
        if len(mine) != len(wards) + 1 or mine[-1]["valid"] != sum(r["valid"] for r in mine[:-1]):
            raise ValueError(f"{name}: rejected ballot papers rows do not sum to the borough total")
        for row, found in zip(wards, mine, strict=False):
            if row["ballots"] != found["valid"] + found["rejected"]:
                raise ValueError(
                    f"{name} {row['ward']}: {row['ballots']} ballot papers, {found['valid']} valid and "
                    f"{found['rejected']} rejected"
                )
            row |= {
                "valid": found["valid"],
                "invalid": found["rejected"],
                "rejected_page": found["page"],
                "rejected_box": found["box"],
            }
    if len(later) - at > 1 or len(later) > at and later[at]["valid"] != total:
        raise ValueError(f"{len(later) - at} unmatched rejected ballot papers rows")
    return pl.DataFrame(out, infer_schema_length=None)


def expected(stats: pl.DataFrame) -> dict[str, list[str]]:
    return {borough: group["ward"].to_list() for (borough,), group in stats.group_by("borough", maintain_order=True)}


def column_split(body: list[Word]) -> float:
    edges = sorted(w[2] for w in body if re.fullmatch(r"\d{1,3}(?:,\d{3})*", w[4]) and w[0] > 150)
    groups: list[list[float]] = []
    for edge in edges:
        if groups and edge - groups[-1][-1] < 8:
            groups[-1].append(edge)
        else:
            groups.append([edge])
    columns = [g for g in groups if len(g) >= 3]
    if len(columns) >= 2:
        return columns[0][-1] + 3
    return 1e9 if not columns or columns[0][0] < 400 else 0.0


def results_1994(
    book: list[list[Word]], codes: set[str], sizes: list[tuple[float, float]], expected: dict[str, list[str]]
) -> pl.DataFrame:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    out = []
    borough = None
    ward = None
    order = 0
    reading = False
    seen = {name: 0 for name in BOROUGHS}
    for index, page in enumerate(book):
        size = sizes[index]
        body = [w for w in page if 8 < w[0] < 545]
        rows = lines(body)
        text = [" ".join(w[4] for w in row) for row in rows]
        if text and "Ward Voting Statistics" in text[0]:
            break
        if len(text) > 1 and fold(text[0]) in folded and re.search(r"administration|control", text[1], re.IGNORECASE):
            borough, ward, reading = folded[fold(text[0])], None, False
            continue
        if borough is None:
            continue
        top = min((w[1] for w in body if re.fullmatch(r"\W*Results", w[4])), default=None)
        if top is not None:
            reading = True
            body = [w for w in body if w[1] > top - 2 and not re.fullmatch(r"\W*Results", w[4])]
        if not reading:
            continue
        split = column_split(body)
        for column in (0, 1):
            column_rows = lines([w for w in body if (w[0] < split) == (column == 0)])
            for at, row in enumerate(column_rows):
                row = [w for w in row if re.search(r"[A-Za-z0-9]", w[4]) and w[4] != "r-"]
                if not row:
                    continue
                words = [w[4] for w in row]
                line = " ".join(words)
                if (
                    line == "Results"
                    or "Research Centre" in line
                    or re.search(r"Elections? May 1994|Eledions May 1994", line)
                ):
                    continue
                if STATUS.search(line):
                    if number(words[-1]) is not None:
                        print(f"status line with a figure on page {index + 1}: {line}")
                    continue
                kept = len(words)
                while kept > 3 and (
                    number(words[kept - 1]) is None or OCR_CODES.get(words[kept - 2], words[kept - 2]) not in codes
                ):
                    kept -= 1
                words, row = words[:kept], row[:kept]
                if len(words) >= 3 and words[-2] in OCR_CODES:
                    words[-2] = OCR_CODES[words[-2]]
                votes = None if words[-1] == "Unopposed" else number(words[-1])
                if len(words) >= 3 and words[-2] in codes and (votes is not None or words[-1] == "Unopposed"):
                    if ward is None:
                        raise ValueError(f"candidate before any ward on page {index + 1}: {line}")
                    order += 1
                    out.append(
                        ward
                        | {
                            "order": order,
                            "name": " ".join(words[:-2]),
                            "party": words[-2],
                            "votes": votes,
                            "raw_votes": words[-1],
                            "line_page": index + 1,
                            "y": round(row[0][1], 1),
                            "bbox": [
                                row[0][0] / size[0],
                                min(w[1] for w in row) / size[1],
                                row[-1][2] / size[0],
                                max(w[3] for w in row) / size[1],
                            ],
                        }
                    )
                    continue
                if STATUS.search(line):
                    continue
                following = next(
                    (
                        " ".join(w[4] for w in later)
                        for later in column_rows[at + 1 :]
                        if re.search(r"[a-z]{3}", " ".join(w[4] for w in later))
                    ),
                    "",
                )
                if STATUS.search(following):
                    pending = expected[borough][seen[borough] : seen[borough] + 3]
                    shape = fold(line)
                    exact = shape in [fold(name) for name in pending]
                    empty = ward is not None and order == 0 and ward["page"] == index + 1
                    if not pending or not exact and (len(line) <= 3 or empty or shape == fold(borough)):
                        continue
                    scores = [
                        (
                            fold(name) == shape,
                            difflib.SequenceMatcher(None, shape, fold(name)).find_longest_match().size
                            / max(len(fold(name)), 1),
                            difflib.SequenceMatcher(None, shape, fold(name)).ratio(),
                        )
                        for name in pending
                    ]
                    if max(scores)[1] < 0.7:
                        print(f"ward header read by position on page {index + 1}: {line} as {pending[0]}")
                        chosen = 0
                    else:
                        chosen = scores.index(max(scores))
                    seen[borough] += chosen + 1
                    ward = {
                        "borough": borough,
                        "ward": pending[chosen],
                        "printed": line,
                        "page": index + 1,
                        "column": column,
                    }
                    order = 0
                    continue
                if len(words) < 3 or not re.search(r"[A-Za-z]{3}", line):
                    continue
                print(f"unread line on page {index + 1}: {line}")
    return pl.DataFrame(out)


def person(text: str) -> str:
    text = re.sub(r"\s*;\s*", ", ", text.replace("~", "")).strip()
    surname, _, rest = text.partition(",")
    words = []
    for word in rest.split():
        if re.fullmatch(r"[A-Z0-9.]+", word) and re.search(r"[A-Z.]", word):
            word = word.translate(str.maketrans({"0": "O", "5": "S"}))
        words.append(word)
    return f"{surname.strip()}, {' '.join(words)}" if words else surname.strip()


def fixed(found: pl.DataFrame) -> pl.DataFrame:
    rows = found.to_dicts()
    used = set()
    for row in rows:
        key = (row["borough"], row["ward"], re.split(r"[,.]", row["name"])[0].strip())
        if key in FIXES:
            row["votes"], row["party"] = FIXES[key]
            used.add(key)
    if used != set(FIXES):
        raise ValueError(f"corrections not applied: {sorted(set(FIXES) - used)}")
    return pl.DataFrame(rows, infer_schema_length=None)


READ_FIXES = {
    r"\blan\b": "Ian",
    r"\blain\b": "Iain",
    r"\bErn?est\b": "Ernest",
    r"\bEmest\b": "Ernest",
    r"\bMihael\b": "Michael",
    r"Bailcy": "Bailey",
    r"Comwall": "Cornwall",
    r"MclIsaac": "McIsaac",
    r"Mazhar-UI": "Mazhar-Ul",
    r" 1\.$": " J.",
    r"§S\.": "S.",
    r"A:S\.": "A.S.",
}


def dotted(name: str) -> str:
    for pattern, fixed in READ_FIXES.items():
        name = re.sub(pattern, fixed, name)
    surname, comma, rest = name.partition(",")
    words = []
    for word in rest.split():
        if re.fullmatch(r"[A-Z](?:[A-Z.]*[A-Z])\.?,?", word) and not word.startswith("Mc"):
            word = ".".join(re.findall(r"[A-Z]", word)) + "."
        elif re.fullmatch(r"[A-Z]-[A-Z]{2,}", word):
            word = word[:2] + ".".join(word[2:]) + "."
        elif re.fullmatch(r"[A-Z],", word):
            word = word[0] + "."
        words.append(word)
    return surname + comma + " " + " ".join(words) if words else name


@functools.cache
def text_layer(path: Path) -> tuple[list[tuple[float, float]], list[list[tuple[float, float, float, float, str]]]]:
    return pages(path)


def scanned_lines(
    artifact: raw.Artifact, sheet: int, columns: bool, dpi: int = 400
) -> tuple[tuple[int, int], list[list[Word]]]:
    size, page = scan(artifact, sheet, dpi=dpi)
    if not columns:
        return size, sorted(page, key=lambda line: min(w[1] for w in line))
    sizes, book = text_layer(artifact.path)
    scale = size[0] / sizes[sheet - 1][0]
    words = [w for w in book[sheet - 1] if re.search(r"\w", w[4])]
    middle = sizes[sheet - 1][0] / 2
    clear = [x for x in range(int(middle * 0.76), int(middle * 1.24)) if not any(w[0] <= x <= w[2] for w in words)]
    runs = [list(run) for _, run in itertools.groupby(clear, key=lambda x: x - clear.index(x))]
    nearest = min(runs, key=lambda run: min(abs(x - middle) for x in run))
    split = int(scale * (nearest[0] + nearest[-1]) / 2)
    out: list[list[Word]] = []
    for crop in ((0, split), (split, size[0])):
        _, column = scan(artifact, sheet, crop, 80 * dpi // 400, dpi)
        out += sorted(column, key=lambda line: min(w[1] for w in line))
    return size, out


def box(line: list[Word], size: tuple[int, int]) -> list[float]:
    return [
        min(w[0] for w in line) / size[0],
        min(w[1] for w in line) / size[1],
        max(w[2] for w in line) / size[0],
        max(w[3] for w in line) / size[1],
    ]


def count(text: str) -> int | None:
    text = text.translate(DIGITS).replace("$", "5").replace(":", ",")
    return int(re.sub(r"[,.]", "", text)) if re.fullmatch(r"\d{1,3}(?:[,.]?\d{3})*|\d{1,3}", text) else None


def results_scanned(artifact: raw.Artifact, codes: set[str], first: int, last: int, dpi: int = 400) -> pl.DataFrame:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    out = []
    borough = ward = None
    order = 0
    after_head = False
    for sheet in range(first, last + 1):
        size, rows = scanned_lines(artifact, sheet, True, dpi)
        for row in rows:
            tokens = [w[4].replace("|", "I").replace("’", "'") for w in row if re.search(r"[A-Za-z0-9]", w[4])]
            while len(tokens) > 3 and tokens[-1] == "I":
                tokens.pop()
            line = LINES.get((sheet, " ".join(tokens)), " ".join(tokens))
            tokens = line.split()
            heading = re.sub(r"\s*[-—–]*\s*continued$", "", line, flags=re.IGNORECASE)
            if fold(heading) in folded:
                borough, ward = folded[fold(heading)], None
                continue
            if not tokens or "Research Centre" in line or "Table 2" in line:
                continue
            if after_head and not re.search(r"\d", line) and re.fullmatch(r"[A-Z][A-Za-z' ]+", line):
                continue
            after_head = False
            if re.fullmatch(r"[A-Z][A-Z0-9 .'&()-]*[A-Z.)],", line):
                ward = {"borough": borough, "ward": line[:-1], "page": sheet, "column": 0, "status": ""}
                order = 0
                after_head = True
                continue
            if len(tokens) >= 3 and (tokens[-1].lower() == "unopposed" or count(tokens[-1]) is not None):
                votes = None if tokens[-1].lower() == "unopposed" else count(tokens[-1])
                code = tokens[-2].upper()
                if code in codes and ward is not None:
                    order += 1
                    out.append(
                        ward
                        | {
                            "order": order,
                            "name": " ".join(tokens[:-2]),
                            "party": code,
                            "votes": votes,
                            "line_page": sheet,
                            "bbox": box(row, size),
                        }
                    )
                    continue
            head = re.fullmatch(r"([A-Z][A-Z0-9 .'&()-]*[A-Z.)'V]),+\s+(\S.*)", line)
            if head and borough is not None:
                ward = {"borough": borough, "ward": head.group(1), "page": sheet, "column": 0, "status": ""}
                order = 0
                after_head = True
                continue
            print(f"unread line on page {sheet}: {line}")
    return pl.DataFrame(out, schema_overrides={"votes": pl.Int64})


def cells_of(tokens: list[str]) -> list[int | None]:
    cells = [t for t in tokens if t != "*"]
    values = [count(t) if "." not in t or "," in t else None for t in cells]
    if len(cells) > 5:
        tenths = cells[5].translate(DIGITS).replace(".", "")
        values[5] = int(tenths) if tenths.isdigit() and len(tenths) >= 2 else None
    return values


def rereads(artifact: raw.Artifact, sheet: int) -> list[list[str]]:
    folder = PARSED / "ocr"
    out = []
    for dpi, psm in ((300, 4), (600, 4), (400, 6)):
        txt = folder / f"{artifact.sha256}-{sheet}-{dpi}-{psm}.txt"
        if not txt.exists():
            with tempfile.TemporaryDirectory() as tmp:
                image = Path(tmp) / "page"
                subprocess.run(
                    ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(sheet), "-l", str(sheet), "-singlefile"]
                    + [str(artifact.path), str(image)],
                    check=True,
                )
                subprocess.run(
                    ["tesseract", f"{image}.png", str(image), "--psm", str(psm), "tsv"], check=True, capture_output=True
                )
                found = list(csv.DictReader((image.with_suffix(".tsv")).open(), delimiter="\t", quoting=csv.QUOTE_NONE))
            lines_: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
            for r in found:
                if r["level"] == "5" and r["text"].strip():
                    lines_.setdefault((r["block_num"], r["par_num"], r["line_num"]), []).append(
                        (int(r["left"]), r["text"].strip())
                    )
            txt.write_text("\n".join(" ".join(t for _, t in sorted(v)) for v in lines_.values()))
        out.append(txt.read_text().splitlines())
    return out


def agreed(own: list[int | None], others: list[list[int | None]]) -> list[int | None]:
    voted = list(own)
    for i in range(len(own)):
        tally: dict[int, int] = {}
        for reading in [own, *others]:
            if i < len(reading) and (cell := reading[i]) is not None:
                tally[cell] = tally.get(cell, 0) + 1
        if tally:
            best = max(tally.values())
            if own[i] is None or tally.get(own[i], 0) < best:
                voted[i] = next(v for v, n in tally.items() if n == best)
    return voted


def statistics_scanned(artifact: raw.Artifact, first: int, last: int) -> pl.DataFrame:
    folded = {fold(b): b for b in BOROUGHS} | {"cityofwestminster": "Westminster"}
    left: list[tuple[str, str, list[int | None], int, list[float], bool]] = []
    right: list[tuple[str, list[int | None], int, list[float], bool]] = []
    borough = None
    for sheet in range(first, last + 1):
        size, rows = scanned_lines(artifact, sheet, False)
        text = " ".join(" ".join(w[4] for w in row) for row in rows[:8])
        alternatives = rereads(artifact, sheet)
        if "Rejected ballot papers" in text:
            kind = "right"
        elif "Ballot papers" in text:
            kind = "left"
        else:
            raise ValueError(f"page {sheet} is neither half of Table 5")
        for row in rows:
            tokens = [w[4] for w in row]
            line = " ".join(tokens)
            if fold(line) in folded and kind == "left":
                borough = folded[fold(line)]
                continue
            if not re.search(r"\d", line) or re.match(r"[(\d*]|Table|Ward|Area", line) or "Research Centre" in line:
                continue
            total = re.search(r"\btotal\b", line) is not None
            if kind == "left":
                if borough is None:
                    continue
                start = next((i for i, t in enumerate(tokens) if i > 0 and re.match(r"[\d*]", t)), None)
                if start is None:
                    continue
                name = " ".join(tokens[:start]).removesuffix(" total").strip()
                values = cells_of(tokens[start:])
                if len(values) == 8 and not total:
                    prefix = fold(name)
                    others = []
                    for reading in alternatives:
                        for other in reading:
                            words = other.split()
                            if fold(" ".join(words[: len(name.split())])) == prefix:
                                found = cells_of(words[len(name.split()) :])
                                if len(found) == 8:
                                    others.append(found)
                    values = agreed(values, others)
                left.append((borough, name, values, sheet, box(row, size), total))
            elif len(tokens) >= 4:
                percent = tokens[-1].replace(".", "")
                right.append(
                    (
                        line,
                        [count(tokens[-2]), int(percent) if percent.isdigit() else None],
                        sheet,
                        box(row, size),
                        total,
                    )
                )
    out = []
    groups = [list(g) for _, g in itertools.groupby(left, key=lambda row: row[0])]
    pieces: list[list[tuple[str, list[int | None], int, list[float], bool]]] = [[]]
    for row in right:
        pieces[-1].append(row)
        if row[4]:
            pieces.append([])
    tail = groups[-1]
    ends = [i for i, row in enumerate(tail) if row[5]]
    if len(ends) == 2:
        groups[-1:] = [tail[: ends[0] + 1], [("Greater London", *row[1:]) for row in tail[ends[0] + 1 :]]]
    if len(groups) != len(pieces) - 1:
        raise ValueError(f"{len(groups)} boroughs on the left of Table 5, {len(pieces) - 1} on the right")
    for group, piece in zip(groups, pieces, strict=False):
        rows = [row for row in group if row[5] or row[2][3] is not None or row[2][4] is not None]
        if len(rows) != len(piece):
            raise ValueError(f"{group[0][0]}: {len(rows)} rows on the left of Table 5, {len(piece)} on the right")
        for (borough, name, values, sheet, bbox, total), (line, tail, page, rbox, rtotal) in zip(
            rows, piece, strict=True
        ):
            if fold(line)[:3] != fold(name)[:3]:
                print(f"right-hand row {line!r} against {borough} {name}")
            out.append(
                {
                    "borough": borough,
                    "ward": name,
                    "total": total,
                    "values": values,
                    "valid": tail[0],
                    "valid_pct": tail[1],
                    "page": sheet,
                    "box": bbox,
                    "rejected_page": page,
                    "rejected_box": rbox,
                }
            )
        for borough, name, values, sheet, bbox, total in (row for row in group if row not in rows):
            out.append({"borough": borough, "ward": name, "total": False, "values": values, "page": sheet, "box": bbox})
    print(f"{len(left)} left rows, {len(right)} right rows")
    return pl.DataFrame(out)


def statistics_checked(artifact: raw.Artifact, first: int, last: int) -> pl.DataFrame:
    table = statistics_scanned(artifact, first, last)
    out = []
    problems = []
    for borough, rows in table.group_by("borough", maintain_order=True):
        wards = [r for r in rows.iter_rows(named=True) if not r["total"]]
        total = next((r for r in rows.iter_rows(named=True) if r["total"]), None)
        fixed_cells = []
        for r in wards:
            v = (r["values"] + [None] * 8)[:8]
            key = (borough[0], r["ward"])
            fixed = VOTING.get(key, {})
            v = [fixed.get(i, x) for i, x in enumerate(v)]
            fixed_cells.append(v)
            valid = fixed.get("valid", r["valid"])
            pct = fixed["pct"] if "pct" in fixed else r["valid_pct"]
            row = {
                "borough": borough[0],
                "ward": r["ward"],
                "seats": v[2],
                "electorate": v[3],
                "ballots": v[4],
                "on_day": v[6],
                "postal": v[7],
                "valid": valid,
                "invalid": None if v[4] is None or valid is None else v[4] - valid,
                "turnout_page": r["page"],
                "turnout_box": r["box"],
                "rejected_page": r["rejected_page"],
                "rejected_box": r["rejected_box"],
            }
            out.append(row)
            if v[3] is None:
                continue
            if None in (v[3], v[4], v[6], v[7], valid) or v[6] + v[7] != v[4]:
                problems.append(f"{key}: {v} valid {valid}")
            elif v[5] is not None and abs(v[4] / v[3] * 1000 - v[5]) > 1.01:
                problems.append(f"{key}: turnout {v[4] / v[3] * 100:.1f} against {v[5] / 10}")
            elif pct is not None and abs(valid / v[4] * 1000 - pct) > 1.01:
                problems.append(f"{key}: valid {valid} of {v[4]} against {pct}")
        if borough[0] == "Greater London":
            continue
        if total is not None:
            t = (total["values"] + [None] * 8)[:8]
            t = [TOTAL_FIXES.get((borough[0], i), x) for i, x in enumerate(t)]
            sums = {i: sum(c[i] or 0 for c in fixed_cells) for i in (2, 3, 4, 6, 7)}
            for i, got in sums.items():
                if got != t[i]:
                    problems.append(f"{borough[0]} column {i + 1}: wards add up to {got}, printed {t[i]}")
    found = pl.DataFrame(out)
    summary = {
        "electorate": (4960826, found["electorate"].sum()),
        "ballots": (2389455, found["ballots"].sum()),
        "on_day": (2360229, found["on_day"].sum()),
        "postal": (29226, found["postal"].sum()),
        "valid": (2383990, found["valid"].sum()),
        "invalid": (5465, found["invalid"].sum()),
    }
    for column, (printed, got) in summary.items():
        if printed != got:
            problems.append(f"{column}: wards add up to {got}, printed {printed}")
    if problems:
        raise ValueError("\n".join(problems))
    return found


def reorder(heads: list[str], rows: list[dict]) -> list[dict]:
    letters = str.maketrans({"0": "o", "5": "s", "1": "i"})
    left = list(rows)
    out = []
    for head, row in zip(heads, rows, strict=True):
        printed = fold(re.sub(r"[^\w')]+$", "", head).translate(letters))
        match = max(
            left,
            key=lambda r: (
                difflib.SequenceMatcher(None, printed, fold(WARDS.get(r["ward"], r["ward"]))).ratio()
                + (row is r) * 0.01
            ),
        )
        left.remove(match)
        out.append(match)
    return out


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    candidates, wards = [], []
    problems: list[str] = []
    for year, (day, url) in BOOKS.items():
        artifact = raw.cached(SOURCE, url)
        if year in SCANNED:
            first, last, left, right = SCANNED[year]
            stats = statistics_checked(artifact, left, right)
            found = results_scanned(artifact, set(PARTIES[year]), first, last)
        else:
            sizes, book = pages(artifact.path)
            stats = statistics_1994(book, sizes) if year == 1994 else statistics(book, sizes)
            found = (
                results_1994(book, set(PARTIES[year]), sizes, expected(stats))
                if year == 1994
                else results(book, set(PARTIES[year]), sizes)
            )
            if year == 1994:
                found = fixed(found)
        found = found.with_columns(name_printed=pl.col("name")).with_columns(
            pl.col("name").map_elements(
                lambda n: NAMES.get(n, dotted(person(n)) if year in SCANNED else person(n)), return_dtype=pl.String
            )
        )
        printed = TOTALS[year]
        groups = found.group_by("party").agg(votes=pl.col("votes").sum(), count=pl.len())
        seen = {code: (votes, count) for code, votes, count in groups.iter_rows()}
        differing = [
            f"{code}: {seen.get(code)} against printed {printed[code][:2]}"
            for code in printed
            if seen.get(code) != printed[code][:2]
        ]
        if differing:
            raise ValueError(f"{year} party table:\n" + "\n".join(differing))
        heads = found.select("borough", "ward", "page", "column").unique(maintain_order=True)
        elected: dict[str, int] = {}
        for borough, code in BOROUGHS.items():
            mine = heads.filter(pl.col("borough") == borough)
            listed = stats.filter(pl.col("borough") == borough)
            if mine.height != listed.height:
                raise ValueError(
                    f"{year} {borough}: {mine.height} wards in the results, {listed.height} in the statistics"
                )
            rows_ = listed.rows(named=True)
            if year in SCANNED:
                rows_ = reorder(mine["ward"].to_list(), rows_)
            for number_, (head, row) in enumerate(zip(mine.iter_rows(named=True), rows_, strict=True)):
                printed_name = re.sub(r"[^\w')]+$", "", head["ward"])
                name = WARDS.get(row["ward"], row["ward"])
                if re.search(r"\d", name):
                    name = printed_name
                letters = str.maketrans({"0": "o", "5": "s", "1": "i"})
                if difflib.SequenceMatcher(None, fold(printed_name.translate(letters)), fold(name)).ratio() < 0.7:
                    raise ValueError(f"{year} {borough}: ward {printed_name} does not match {row['ward']}")
                lines_ = found.filter(
                    (pl.col("borough") == borough)
                    & (pl.col("ward") == head["ward"])
                    & (pl.col("page") == head["page"])
                    & (pl.col("column") == head["column"])
                ).sort("order")
                votes = lines_["votes"].to_list()
                tacit = all(v is None for v in votes)
                seats = len(votes) if tacit else WARD_SEATS.get((year, borough, name), row["seats"])
                if not tacit:
                    if any(a < b for a, b in itertools.pairwise(votes)):
                        problems.append(f"{year} {borough} {name}: votes out of order {votes}")
                    if len(votes) > seats and votes[seats - 1] == votes[seats]:
                        problems.append(f"{year} {borough} {name}: tie for the last seat")
                    if max(votes) > row["valid"] and (year, borough, name) not in MISPRINTED:
                        problems.append(f"{year} {borough} {name}: a candidate has more votes than valid ballots")
                if len(votes) < seats:
                    raise ValueError(f"{year} {borough} {name}: {len(votes)} candidates for {seats} seats")
                wards.append(
                    {
                        "year": year,
                        "day": day,
                        "borough": borough,
                        "la_code": code,
                        "number": number_ + 1,
                        "ward": name,
                        "seats": seats,
                        "tacit": tacit,
                        "electorate": row.get("electorate"),
                        "ballots": row.get("ballots"),
                        "valid": row.get("valid"),
                        "invalid": row.get("invalid"),
                        "page": head["page"],
                        "turnout_page": row.get("turnout_page"),
                        "turnout_box": row.get("turnout_box"),
                        "rejected_page": row.get("rejected_page"),
                        "rejected_box": row.get("rejected_box"),
                    }
                )
                for rank, line in enumerate(lines_.iter_rows(named=True)):
                    won = rank < seats
                    elected[line["party"]] = elected.get(line["party"], 0) + won
                    candidates.append(
                        {
                            "year": year,
                            "la_code": code,
                            "ward": name,
                            "number": rank + 1,
                            "name": line["name"],
                            "printed": line["name_printed"],
                            "party": line["party"],
                            "votes": line["votes"],
                            "elected": won,
                            "page": line["line_page"],
                            "bbox": line["bbox"],
                        }
                    )
        if year in SUMMARY:
            mine = pl.DataFrame([w for w in wards if w["year"] == year])
            held = mine.filter(~pl.col("tacit"))
            figures = (
                found.height,
                mine["seats"].sum(),
                held["electorate"].sum(),
                held["ballots"].sum(),
                held["valid"].sum(),
            )
            if figures != SUMMARY[year]:
                raise ValueError(f"{year} totals {figures} differ from the printed summary {SUMMARY[year]}")
        wrong = {
            code: (elected.get(code, 0), seats)
            for code, (_, _, seats) in printed.items()
            if elected.get(code, 0) != seats
        }
        if wrong:
            raise ValueError(f"{year} elected by party differs from the printed table: {wrong}")
    if problems:
        raise ValueError("\n".join(problems))
    pl.DataFrame(wards).write_parquet(PARSED / "wards.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    print(f"{len(wards)} wards and {len(candidates)} candidates")
    parse_glc()


def scan(
    artifact: raw.Artifact, number: int, crop: tuple[int, int] | None = None, pad: int = 0, dpi: int = 400
) -> tuple[tuple[int, int], list[list[Word]]]:
    folder = PARSED / "ocr"
    folder.mkdir(parents=True, exist_ok=True)
    tsv = (
        folder
        / f"{artifact.sha256}-{number}-{crop[0] if crop else 'page'}{'-padded' if pad else ''}{f'-{dpi}' if dpi != 400 else ''}.tsv"
    )
    if not tsv.exists():
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page"
            window = ["-x", str(crop[0]), "-y", "0", "-W", str(crop[1] - crop[0]), "-H", "100000"] if crop else []
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                + window
                + [str(artifact.path), str(image)],
                check=True,
            )
            if pad:
                subprocess.run(
                    ["magick", f"{image}.png", "-bordercolor", "white", "-border", f"{pad}x{pad}", f"{image}.png"],
                    check=True,
                )
            subprocess.run(
                ["tesseract", f"{image}.png", str(tsv.with_suffix("")), "--psm", "6" if crop else "4", "tsv"],
                check=True,
                capture_output=True,
            )
    rows = list(csv.DictReader(tsv.open(), delimiter="\t", quoting=csv.QUOTE_NONE))
    top = next(r for r in rows if r["level"] == "1")
    offset = (crop[0] if crop else 0) - pad
    grouped: dict[tuple[str, str, str], list[Word]] = {}
    for r in rows:
        if r["level"] == "5" and r["text"].strip():
            left, upper = int(r["left"]) + offset, int(r["top"]) - pad
            grouped.setdefault((r["block_num"], r["par_num"], r["line_num"]), []).append(
                (left, upper, left + int(r["width"]), upper + int(r["height"]), r["text"].strip())
            )
    return (int(top["width"]) + offset, int(top["height"])), list(grouped.values())


def glc_book(year: int, artifact: raw.Artifact) -> tuple[list[dict], list[dict]]:
    divisions: list[dict] = []
    candidates: list[dict] = []
    codes = GLC_PARTIES[year]
    started = False
    division: dict | None = None
    pending = ""
    for sheet in range(1, pdf_pages(artifact.path) + 1):
        (width, height), page = scan(artifact, sheet)
        text = " ".join(w[4] for line in page for w in line)
        if not started:
            started = bool(re.search(r"TABLE 1\b", text))
            if not started:
                continue
        elif re.search(r"TABLE [23]\b", text):
            break
        words: list[Word] = [w for line in page for w in line]
        split = min(
            range(int(width * 0.38), int(width * 0.62), 5),
            key=lambda x: (sum(w[0] <= x <= w[2] for w in words), abs(x - width / 2)),
        )
        for crop in ((0, split), (split, width)):
            _, column = scan(artifact, sheet, crop)
            entries: list[tuple[str, list[float]]] = []
            for line in sorted(column, key=lambda line: min(w[1] for w in line)):
                tokens = [w[4].replace("|", "I") for w in line]
                while tokens and (not re.search(r"[A-Za-z0-9]", tokens[-1]) or tokens[-1] == "I" and len(tokens) > 1):
                    tokens.pop()
                tokens = [t.replace(",»", ",").replace("»", ",") for t in tokens if t not in ("'", "‘")]
                joined = GLC_LINES.get((year, sheet, " ".join(tokens)), " ".join(tokens))
                box = [
                    min(w[0] for w in line) / width,
                    min(w[1] for w in line) / height,
                    max(w[2] for w in line) / width,
                    max(w[3] for w in line) / height,
                ]
                entries.append((joined, box))
                for dropped in GLC_DROPPED.get((year, sheet, joined), []):
                    entries.append((dropped, locate(artifact.path, sheet, dropped)))
            for joined, box in entries:
                tokens = joined.split()
                if "Continued" in joined or "ontinued" in joined:
                    continue
                head = re.fullmatch(
                    r"[^A-Z]*([A-Z][A-Z .,'&—-]*[A-Z])\s*[(\[{]\s*([\dOoIl,. ]+?)\s*[)\]}1]?[.,]?\s*[.*]?", joined
                )
                if head:
                    electorate = number(head.group(2).replace(" ", ","))
                    if electorate is None:
                        raise ValueError(f"{year} page {sheet}: unread electorate in {joined}")
                    division = {
                        "year": year,
                        "printed": re.sub(r"\s*[—-]\s*", "-", head.group(1).strip()),
                        "electorate": electorate,
                        "page": sheet,
                        "bbox": box,
                    }
                    divisions.append(division)
                    pending = ""
                    continue
                if len(tokens) >= 3 and re.fullmatch(r"\d{1,2}", tokens[-2]) and re.fullmatch(r"\d{3}", tokens[-1]):
                    tokens[-2:] = [tokens[-2] + "," + tokens[-1]]
                votes = number(re.sub(r"^[^\dOolI]+|[^\dOolI]+$", "", tokens[-1])) if tokens else None
                party = None
                for width_ in (2, 1, 3):
                    if len(tokens) > width_ + 1:
                        code = re.sub(r"[^A-Za-z]", "", "".join(tokens[-1 - width_ : -1])).upper()
                        code = GLC_OCR.get((year, code), code)
                        if code in codes:
                            party, name = code, " ".join(tokens[: -1 - width_])
                            break
                if party is not None and votes is not None:
                    name = f"{pending} {name}" if pending else name
                    pending = ""
                    name = re.sub(r",(?=\S)", ", ", re.sub(r"\s+,", ",", name.strip(" ,'‘’:"))).replace("’", "'")
                    surname, comma, rest = name.partition(",")
                    if comma and re.search(r"[A-Z.]1|1\.|[A-Z.]0\.", rest):
                        rest = rest.translate(str.maketrans({"1": "I", "0": "O"}))
                    given = rest.split()
                    for at, word in enumerate(given):
                        if year == 1981 and word == "WH.":
                            given[at] = "W."
                        elif year < 1981 and re.fullmatch(r"[A-Z]{2}\.", word):
                            given[at] = f"{word[0]}.{word[1]}."
                    if given and re.fullmatch(r"[A-Z]", given[-1]):
                        given[-1] += "."
                    name = f"{surname}{comma} {' '.join(given)}" if comma else name
                    if division is None:
                        raise ValueError(f"{year} page {sheet}: candidate before any division: {joined}")
                    candidates.append(
                        {
                            "year": year,
                            "division": division["printed"],
                            "name": GLC_NAMES.get((year, name), name),
                            "party": party,
                            "votes": votes,
                            "page": sheet,
                            "bbox": box,
                        }
                    )
                elif division is not None and joined.endswith(",") and re.match(r"[A-Z][a-z]", joined):
                    pending = joined
                elif re.search(r"\d", joined) and len(tokens) >= 2:
                    print(f"unread line {year} page {sheet}: {joined!r}")
    return divisions, candidates


def locate(path: Path, sheet: int, line: str) -> list[float]:
    sizes, book = pages(path)
    first, last = line.split()[0], line.split()[-1]
    width, height = sizes[sheet - 1]
    for row in lines(book[sheet - 1]):
        for start, word in enumerate(row):
            if word[4] == first:
                end = next((w for w in row[start:] if w[4] == last), None)
                if end is not None:
                    return [word[0] / width, word[1] / height, end[2] / width, end[3] / height]
    raise ValueError(f"page {sheet}: {line} is not in the text layer")


def pdf_pages(path: Path) -> int:
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
    found = re.search(r"Pages:\s+(\d+)", info)
    if found is None:
        raise ValueError(f"{path}: no page count")
    return int(found.group(1))


def parse_glc() -> None:
    divisions, candidates = [], []
    for year, (_, url) in GLC.items():
        found, lines_ = glc_book(year, raw.cached(SOURCE, url))
        electorate, valid, seats = GLC_TOTALS[year]
        names = [d["printed"] for d in found]
        problems = []
        if len(found) != 92 or len(set(names)) != 92:
            problems.append(f"{len(found)} divisions, {len(set(names))} distinct")
        if sum(d["electorate"] for d in found) != electorate:
            problems.append(f"electorate {sum(d['electorate'] for d in found)}, printed {electorate}")
        if sum(c["votes"] for c in lines_) + sum(v for (y, _), v in GLC_SHORT.items() if y == year) != valid:
            problems.append(f"votes {sum(c['votes'] for c in lines_)}, printed {valid}")
        for code, (label, count, votes) in GLC_PARTIES[year].items():
            mine = [c["votes"] for c in lines_ if c["party"] == code]
            if (len(mine), sum(mine) + GLC_SHORT.get((year, code), 0)) != (count, votes):
                problems.append(f"{code}: {len(mine)} candidates and {sum(mine)} votes, printed {count} and {votes}")
        won: dict[str, int] = {}
        for d in found:
            mine = [c for c in lines_ if c["division"] == d["printed"]]
            votes = [c["votes"] for c in mine]
            if len(votes) < 2 or votes[0] <= votes[1] or any(a < b for a, b in itertools.pairwise(votes)):
                problems.append(f"{d['printed']}: votes {votes}")
            for rank, c in enumerate(mine):
                c["number"] = rank + 1
                c["elected"] = rank == 0
            if mine:
                family = "LAB" if mine[0]["party"].startswith("LAB") else mine[0]["party"]
                won[family] = won.get(family, 0) + 1
        if won != seats:
            problems.append(f"elected {won}, printed {seats}")
        for c in lines_:
            if not re.fullmatch(r"[A-Z][A-Za-z'’ -]+, [A-Z][A-Za-z.,'’ -]*", c["name"]):
                problems.append(f"name {c['name']!r} on page {c['page']}")
        if problems:
            raise ValueError(f"{year}:\n" + "\n".join(problems))
        divisions += found
        candidates += lines_
    pl.DataFrame(divisions).write_parquet(PARSED / "glc_divisions.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "glc_candidates.parquet")
    print(f"{len(divisions)} GLC divisions and {len(candidates)} candidates")
    areas, standing = parse_glc_early()
    pl.DataFrame(areas).write_parquet(PARSED / "glc_areas.parquet")
    pl.DataFrame(standing).write_parquet(PARSED / "glc_early_candidates.parquet")
    print(f"{len(areas)} GLC electoral areas and {len(standing)} candidates, 1964 to 1970")
    parse_ilea()
    parse_glc_by()
    parse_glc_by_early()


SKIPPED = re.compile(
    r"TABLE|Number of votes|Greater London Counci|Successful candidates|heavy type|General Election|in bold|"
    r"votes cast in each|Candidates with party|election.{0,3}\(continued\)|\((Royal|ROYAL) (Borough|BOROUGH)\)|"
    r"^\((?i:with the)|^(added|TEMPLES ADDED|Temples added)\)$|Died|by-election|^\d+$|^I$|^\d+ April 19"
)


def early_name(text: str) -> str:
    tokens = text.split()
    while tokens and not re.search(r"[A-Za-z0-9]", tokens[-1]):
        tokens.pop()
    if tokens and tokens[-1].endswith(".."):
        tokens[-1] = tokens[-1].rstrip(".")
        if re.fullmatch(r"([A-Za-z]{1,3}\.)+[A-Za-z]{1,3}", tokens[-1]):
            tokens[-1] += "."
    text = " ".join(tokens).rstrip(" ,·").replace("’", "'")
    text = EARLY_NAMES.get(text, text)
    head, comma, tail = text.partition(",")
    return re.sub(r"(?<=\b[A-Z]\.)(?=[A-Z])", " ", head) + comma + tail


def early_person(year: int, text: str) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", text)
    head = text.split(",")[0].split()
    if year == 1970:
        head = [
            word
            for word in head
            if word.rstrip(".") not in {"Mrs", "Miss", "Dr", "Sir", "The", "Hon", "Rev", "Canon", "Lt-Col"}
        ]
        head = [*head[1:], head[0]]
    else:
        while len(head) > 2 and head[0] in EARLY_TITLES | {"The", "Rev."}:
            head = head[1:]
        if len(head) == 2 and head[0] in EARLY_TITLES:
            head = head[1:]
    return " ".join(re.sub(r"\.(?=$)", "", word) if re.fullmatch(r"Mrs\.|Dr\.", word) else word for word in head)


def glc_early(year: int, artifact: raw.Artifact) -> tuple[list[dict], list[dict]]:
    sizes, book = pages(artifact.path)
    first, last = EARLY[year][2]
    codes = EARLY_PARTIES[year]
    areas: dict[str, dict] = {}
    candidates: list[dict] = []
    area: dict | None = None
    for sheet in range(first, last + 1):
        width, height = sizes[sheet - 1]
        for side in (0, 1):
            pending: tuple[float, str, list[float]] | None = None
            column = [w for w in book[sheet - 1] if (w[0] < width / 2) == (side == 0)]
            for row in lines(column):
                box = [
                    min(w[0] for w in row) / width,
                    min(w[1] for w in row) / height,
                    max(w[2] for w in row) / width,
                    max(w[3] for w in row) / height,
                ]
                joined = " ".join(w[4] for w in row)
                for text in EARLY_LINES.get((year, sheet, side), [joined]) if "~" in joined else [joined]:
                    if "By-Election" in text:
                        return list(areas.values()), candidates
                    if SKIPPED.search(text) or not re.search(r"[A-Za-z0-9]", text):
                        continue
                    text = text.replace("RrCHMOND", "RICHMOND")
                    heading = re.fullmatch(
                        r"[t†]?([A-Z][A-Za-z ,.&'-]*?)(?: \((?:City|CITY) (?:of|OF)\))?(?: - Continued)?", text
                    )
                    if heading and heading.group(1).isupper() and len(heading.group(1)) > 3:
                        printed = heading.group(1).replace("RED BRIDGE", "REDBRIDGE")
                        printed = re.sub(
                            r"\s+", " ", printed.replace("CRO YDON", "CROYDON").replace("S UTTON", "SUTTON")
                        )
                        area = areas.setdefault(
                            printed, {"year": year, "printed": printed, "x": row[0][0], "page": sheet}
                        )
                        pending = None
                        continue
                    found = (
                        re.fullmatch(r"(.*?)\s+(\S+)\s+(\d[\d,.]*)", text)
                        if year == 1970
                        else re.fullmatch(r"(.*)\(([^)]*)\)\s*(\d[\d,.]*)", text)
                    )
                    if area is None:
                        continue
                    if found is None:
                        pending = (
                            (pending[0], f"{pending[1]} {text}", pending[2]) if pending else (row[0][0], text, box)
                        )
                        continue
                    start, prefix, top = pending or (row[0][0], "", box)
                    pending = None
                    name = f"{prefix} {found.group(1)}".strip() if not prefix.endswith("-") else prefix + found.group(1)
                    name = early_name(name)
                    code = re.sub(r"[^A-Za-z0-9]", "", found.group(2)).upper()
                    if year == 1970 and code == "LIB" and name.endswith(" U."):
                        name, code = name.removesuffix(" U."), "ULIB"
                    if year == 1970 and code in {"O", "0", "A"}:
                        code = EARLY_OTHERS.get((area["printed"], name), code)
                    code = EARLY_CODES.get(code, code)
                    votes = number(found.group(3))
                    if code not in codes or votes is None:
                        raise ValueError(f"{year} page {sheet}: unread line {text!r}")
                    candidates.append(
                        {
                            "year": year,
                            "area": area["printed"],
                            "name": name,
                            "party": code,
                            "votes": votes,
                            "flush": start - area["x"] < 9,
                            "page": sheet,
                            "bbox": [min(top[0], box[0]), top[1], max(top[2], box[2]), box[3]],
                        }
                    )
    return list(areas.values()), candidates


def early_code(printed: str) -> str:
    folded = fold(printed)
    if "westminster" in folded:
        return EARLY_AREA
    found = {fold(k): v for k, v in BOROUGHS.items()} | {"barking": "E09000002", "hammersmith": "E09000013"}
    return found[folded]


def early_statistics(year: int, book: list[list[Word]], sizes: list[tuple[float, float]]) -> dict[str, dict]:
    sheet = EARLY[year][3]
    width, height = sizes[sheet - 1]
    pattern = (
        r"([A-Za-z][A-Za-z &']*?)\s+[\d,]+[.,]\d\s+\S+\s+(\d{2,3},\d{3})\s+(\d{2,3},\d{3})\s+\d.*"
        if year == 1970
        else r"([A-Za-z][A-Za-z ,.'&]*?)[\s.,·\"]*\s(\d{2,3},\d{3})\s+(\d{2,3},\d{3})(?:\s*\{?\(a\))?\s+\d.*"
    )
    found: dict[str, dict] = {}
    for row in lines(book[sheet - 1]):
        match = re.fullmatch(pattern, " ".join(w[4] for w in row))
        if match is None or re.search(r"London \(c\)|^Total", match.group(1)):
            continue
        code = early_code(match.group(1))
        figures = []
        for text in match.group(2, 3):
            word = next(w for w in row if w[4] == text)
            figures.append((number(text), [word[0] / width, word[1] / height, word[2] / width, word[3] / height]))
        found[code] = {"electorate": figures[0][0], "electorate_bbox": figures[0][1]}
        found[code] |= {"voting": figures[1][0], "voting_bbox": figures[1][1], "stats_page": sheet}
    if year == 1970:
        split = [
            row
            for row in lines(book[sheet])
            if re.fullmatch(r"(-|\d+) (-|\d+) \S+ \S+ \S+ \S+ \S+ \S+", " ".join(w[4] for w in row))
        ]
        for code, row in zip(found, split[:32], strict=True):
            con, lab = (0 if w[4] == "-" else int(w[4]) for w in row[:2])
            found[code]["split"] = {"CON": con, "LAB": lab}
    return found


def parse_glc_early() -> tuple[list[dict], list[dict]]:
    areas, candidates = [], []
    for year, (_, url, _, _) in EARLY.items():
        artifact = raw.cached(SOURCE, url)
        sizes, book = pages(artifact.path)
        stats = early_statistics(year, book, sizes)
        found, lines_ = glc_early(year, artifact)
        electorate, voting, total, composition = EARLY_TOTALS[year]
        problems = []
        if len(found) != 32 or set(stats) != set(EARLY_SEATS):
            problems.append(f"{len(found)} areas, statistics for {len(stats)}")
        if sum(a["electorate"] for a in stats.values()) != electorate:
            problems.append(f"electorate {sum(a['electorate'] for a in stats.values())}, printed {electorate}")
        if sum(a["voting"] for a in stats.values()) != voting:
            problems.append(f"voting {sum(a['voting'] for a in stats.values())}, printed {voting}")
        short = sum(v for (y, _), v in EARLY_SHORT.items() if y == year)
        if sum(c["votes"] for c in lines_) + short != total:
            problems.append(f"votes {sum(c['votes'] for c in lines_)}, printed {total}")
        for code, (label, count, votes) in EARLY_PARTIES[year].items():
            mine = [c["votes"] for c in lines_ if c["party"] == code]
            if (len(mine), sum(mine) + EARLY_SHORT.get((year, code), 0)) != (count, votes):
                problems.append(f"{code}: {len(mine)} candidates and {sum(mine)} votes, printed {count} and {votes}")
        won: dict[str, int] = {}
        for area in found:
            code = early_code(area["printed"])
            seats = EARLY_SEATS[code]
            mine = [c for c in lines_ if c["area"] == area["printed"]]
            for rank, c in enumerate(mine):
                c["number"] = rank + 1
                c["elected"] = rank < seats if year == 1970 else c["flush"]
            elected = [c for c in mine if c["elected"]]
            others = [c["votes"] for c in mine if not c["elected"]]
            if len(elected) != seats or min(c["votes"] for c in elected) <= max(others):
                problems.append(f"{area['printed']}: {len(elected)} elected of {seats}")
            parties = {p: sum(c["party"] == p for c in elected) for p in ("CON", "LAB")}
            if year == 1970 and parties != stats[code]["split"]:
                problems.append(f"{area['printed']}: elected {parties}, printed {stats[code]['split']}")
            for c in elected:
                won[c["party"]] = won.get(c["party"], 0) + 1
            areas.append(area | stats[code] | {"code": code, "seats": seats})
        if won != composition:
            problems.append(f"elected {won}, printed {composition}")
        if problems:
            raise ValueError(f"{year}:\n" + "\n".join(problems))
        candidates += lines_
    return areas, candidates


def ilea_box(row: list[Word], size: tuple[float, float]) -> list[float]:
    width, height = size
    return [
        min(w[0] for w in row) / width,
        min(w[1] for w in row) / height,
        max(w[2] for w in row) / width,
        max(w[3] for w in row) / height,
    ]


def parse_ilea() -> None:
    artifact = raw.cached(SOURCE, ILEA_URL)
    sizes, book = pages(artifact.path)
    seats: dict[str, dict] = {}
    candidates: list[dict] = []
    for sheet in range(ILEA_PAGES[0], ILEA_PAGES[1] + 1):
        width = sizes[sheet - 1][0]
        for side in (0, 1):
            seat: dict | None = None
            column = [w for w in book[sheet - 1] if (w[0] < width / 2) == (side == 0)]
            for row in lines(column):
                text = " ".join(w[4] for w in row).replace("Ho1born", "Holborn")
                heading = re.fullmatch(r"([A-Z][A-Z&,.' ]+?)(?: \*)?", text)
                if heading and len(heading.group(1)) > 5:
                    printed = heading.group(1)
                    seat = seats.setdefault(fold(printed), {"printed": printed, "page": sheet, "x": row[0][0]})
                    continue
                found = re.fullmatch(r"(.+?)\s+(LIB AFT|[A-Za-z]+)\s+(\d[\d,]*)", text)
                if seat is None or found is None:
                    continue
                name = re.sub(r"[\s<]+$", "", found.group(1))
                name = ILEA_NAMES.get(name, name)
                code = found.group(2).replace(" ", "").upper()
                code = ILEA_CODES.get(code, code)
                votes = number(found.group(3))
                if code not in ILEA_PARTIES or votes is None or not re.fullmatch(r"[A-Z][A-Za-z'’ -]+, [A-Z].*", name):
                    raise ValueError(f"ILEA page {sheet}: unread line {text!r}")
                candidates.append(
                    {
                        "seat": fold(seat["printed"]),
                        "name": name,
                        "party": code,
                        "votes": votes,
                        "flush": row[0][0] - seat["x"] < 4,
                        "page": sheet,
                        "bbox": ilea_box(row, sizes[sheet - 1]),
                    }
                )
    figures: dict[str, dict[str, tuple[list[int | None], list[list[float]]]]] = {}
    for kind, sheet in ILEA_STATS.items():
        figures[kind] = {}
        for row in lines(book[sheet - 1]):
            text = " ".join(w[4] for w in row)
            label = re.sub(r"[\d,.\s]+$", "", text)
            close = difflib.get_close_matches(fold(label.replace("1", "l")), seats, n=1, cutoff=0.8)
            if re.search(r"[A-Z]{4}", label) or not close:
                continue
            key = close[0]
            cells = [w for w in row if re.fullmatch(r"[\d,]+", w[4])]
            figures[kind][key] = ([number(w[4]) for w in cells], [ilea_box([w], sizes[sheet - 1]) for w in cells])
    errata_sheet, errata = ILEA_ERRATA
    electorate, total, rejected, membership = ILEA_TOTALS
    problems = []
    if len(seats) != 29 or any(len(figures[k]) != 29 for k in figures):
        problems.append(f"{len(seats)} constituencies, {[len(v) for v in figures.values()]} statistics rows")
    rows: list[dict] = []
    won: dict[str, int] = {}
    for key, seat in seats.items():
        mine = [c for c in candidates if c["seat"] == key]
        for rank, c in enumerate(sorted(mine, key=lambda c: -c["votes"])):
            c["number"] = rank + 1
            c["elected"] = c["flush"]
        elected = [c for c in mine if c["elected"]]
        if len(elected) != 2 or min(c["votes"] for c in elected) <= max(c["votes"] for c in mine if not c["elected"]):
            problems.append(f"{seat['printed']}: {len(elected)} elected")
        for c in elected:
            family = "ALLIANCE" if c["party"] in ILEA_ALLIANCE else c["party"]
            won[family] = won.get(family, 0) + 1
        groups = [
            sum(c["votes"] for c in mine if c["party"] in ILEA_ALLIANCE),
            sum(c["votes"] for c in mine if c["party"] == "CON"),
            sum(c["votes"] for c in mine if c["party"] == "LAB"),
            sum(c["votes"] for c in mine if c["party"] not in ILEA_ALLIANCE | {"CON", "LAB"}),
        ]
        groups = [*groups[:3], *([groups[3]] if groups[3] else []), sum(groups)]
        if figures["votes"][key][0] != groups:
            problems.append(f"{seat['printed']}: votes {groups}, Table 2 {figures['votes'][key][0]}")
        registered, registered_box = figures["electorate"][key][0][-1], figures["electorate"][key][1][-1]
        registered_page = ILEA_STATS["electorate"]
        if key in errata:
            registered_page = errata_sheet
            registered = number(errata[key])
            registered_box = locate(artifact.path, errata_sheet, errata[key])
        rows.append(
            seat
            | {
                "key": key,
                "electorate": registered,
                "electorate_page": registered_page,
                "electorate_bbox": registered_box,
                "rejected": figures["rejected"][key][0][-1],
                "rejected_bbox": figures["rejected"][key][1][-1],
            }
        )
    if sum(r["electorate"] for r in rows) != electorate:
        problems.append(f"electorate {sum(r['electorate'] for r in rows)}, printed {electorate}")
    if sum(r["rejected"] for r in rows) != rejected:
        problems.append(f"rejected {sum(r['rejected'] for r in rows)}, printed {rejected}")
    if sum(c["votes"] for c in candidates) != total:
        problems.append(f"votes {sum(c['votes'] for c in candidates)}, printed {total}")
    for code, (_, count, votes) in ILEA_PARTIES.items():
        mine = [c["votes"] for c in candidates if c["party"] == code]
        if (len(mine), sum(mine)) != (count, votes):
            problems.append(f"{code}: {len(mine)} candidates and {sum(mine)} votes, printed {count} and {votes}")
    if won != membership:
        problems.append(f"elected {won}, printed {membership}")
    if problems:
        raise ValueError("ILEA 1986:\n" + "\n".join(problems))
    pl.DataFrame(rows).write_parquet(PARSED / "ilea_constituencies.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "ilea_candidates.parquet")
    print(f"{len(rows)} ILEA constituencies and {len(candidates)} candidates, 1986")


def parse_glc_by() -> None:
    artifact = raw.cached(SOURCE, ILEA_URL)
    sizes, book = pages(artifact.path)
    held = pl.read_parquet(PARSED / "glc_candidates.parquet").filter(pl.col("year") == 1981, pl.col("elected"))
    sitting = {fold(r["division"]): r["name"].split(",")[0] for r in held.iter_rows(named=True)}
    divisions: list[dict] = []
    candidates: list[dict] = []
    problems = []
    for sheet in range(GLC_BY_PAGES[0], GLC_BY_PAGES[1] + 1):
        width = sizes[sheet - 1][0]
        for side in (0, 1):
            division: dict | None = None
            column = [w for w in book[sheet - 1] if (w[0] < width / 2) == (side == 0)]
            for row in lines(column):
                text = " ".join(w[4] for w in row)
                if re.fullmatch(r"[A-Z][A-Z ]{4,}", text):
                    division = {"printed": text, "page": sheet, "x": row[0][0]}
                    divisions.append(division)
                    continue
                if division is None:
                    continue
                dated = re.fullmatch(r"\((\d+)\.(\d+)\.(\d+),\s*[\d. ]+%?,\s*([\d,]+)\)", text)
                if dated:
                    d, m, y, registered = dated.groups()
                    division |= {
                        "day": date(1900 + int(y), int(m), int(d)),
                        "electorate": number(registered),
                        "electorate_bbox": ilea_box([row[-1]], sizes[sheet - 1]),
                    }
                    continue
                cause = re.fullmatch(r"Resignation of (.+) \(([A-Z]+)\)", text)
                if cause:
                    division["cause"] = (cause.group(1), cause.group(2))
                    continue
                found = re.fullmatch(r"(.+?)\s+([A-Za-z]+)(?:\s*\*)?\s+(\d[\d,]*)", text)
                if found is None or "day" not in division or "cause" in division:
                    continue
                name = GLC_BY_NAMES.get(found.group(1), found.group(1))
                code = found.group(2).upper()
                code = GLC_BY_CODES.get(code, code)
                votes = number(found.group(3))
                if (
                    code not in GLC_BY_PARTIES
                    or votes is None
                    or not re.fullmatch(r"[A-Z][A-Za-z'’ -]+, [A-Z].*", name)
                ):
                    raise ValueError(f"GLC by-elections page {sheet}: unread line {text!r}")
                candidates.append(
                    {
                        "division": division["printed"],
                        "day": division["day"],
                        "name": name,
                        "party": code,
                        "votes": votes,
                        "elected": row[0][0] - division["x"] < 4,
                        "page": sheet,
                        "bbox": ilea_box(row, sizes[sheet - 1]),
                    }
                )
    for division in divisions:
        division.pop("x")
        mine = [c for c in candidates if c["division"] == division["printed"]]
        for rank, c in enumerate(sorted(mine, key=lambda c: -c["votes"])):
            c["number"] = rank + 1
        elected = [c for c in mine if c["elected"]]
        if len(elected) != 1 or elected[0]["number"] != 1 or "cause" not in division:
            problems.append(f"{division['printed']}: {len(elected)} elected, {len(mine)} candidates")
            continue
        printed = division["printed"].title().replace(" And ", " and ")
        held_by = sitting.get(fold(GLC_BY_DIVISIONS.get(printed, printed)))
        if held_by is None or not division["cause"][0].startswith(held_by):
            problems.append(f"{division['printed']}: caused by {division['cause'][0]}, 1981 winner {held_by}")
        division["cause"] = f"{division['cause'][0]} ({division['cause'][1]})"
    if problems:
        raise ValueError("GLC by-elections:\n" + "\n".join(problems))
    pl.DataFrame(divisions).write_parquet(PARSED / "glc_by_divisions.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "glc_by_candidates.parquet")
    print(f"{len(divisions)} GLC by-elections and {len(candidates)} candidates, 1981 to 1985")


def parse_glc_by_early() -> None:
    areas = pl.read_parquet(PARSED / "glc_areas.parquet")
    divisions: list[dict] = []
    candidates: list[dict] = []
    year, sheet, printed = GLC_BY_TACIT
    sizes, book = pages(raw.cached(SOURCE, EARLY[year][1]).path)
    for row in lines(book[sheet - 1]):
        found = re.fullmatch(
            r"tDied (\d+ \w+ \d{4}); (.+?), was elected at an uncontested by-election on (\d+) (\w+) (\d{4})\.",
            " ".join(w[4] for w in row),
        )
        if found:
            day = datetime.strptime(" ".join(found.group(3, 4, 5)), "%d %B %Y").date()
            divisions.append({"report": year, "printed": printed, "day": day, "page": sheet})
            candidates.append(
                {
                    "printed": printed,
                    "day": day,
                    "name": found.group(2),
                    "party": None,
                    "votes": None,
                    "elected": True,
                    "number": 1,
                    "page": sheet,
                    "bbox": ilea_box(row, sizes[sheet - 1]),
                }
            )
    if not divisions:
        raise ValueError("GLC 1964: no uncontested by-election footnote")
    for year, sheet in GLC_BY_EARLY.items():
        url = EARLY[year][1] if year in EARLY else GLC[year][1]
        sizes, book = pages(raw.cached(SOURCE, url).path)
        division: dict | None = None
        for row in lines(book[sheet - 1]):
            text = " ".join(w[4] for w in row)
            heading = re.fullmatch(
                r"([A-Za-z~ ]+?)(?:, [A-Za-z ]+)? \((\d+)\.(\d+)\.(\d{2}|\d{4})[,.] ?[\d.]+%?\)", text
            ) or re.fullmatch(r"([A-Z ]+) \(By-Election\)", text)
            if heading:
                printed = heading.group(1).upper()
                day = (
                    date(1900 + int(heading.group(4)[-2:]), int(heading.group(3)), int(heading.group(2)))
                    if heading.lastindex == 4
                    else None
                )
                division = {
                    "report": year,
                    "printed": GLC_BY_EARLY_HEADS.get(printed, printed),
                    "day": day,
                    "page": sheet,
                }
                divisions.append(division)
                continue
            if division is None:
                continue
            held = re.fullmatch(r"\(Held (\d+) (\w+) (\d{4})\)", text)
            if held:
                division["day"] = datetime.strptime(" ".join(held.groups()), "%d %B %Y").date()
                continue
            found = re.fullmatch(r"(.+?)(?: \.\.)* \((\w+)\.\) ([\d,]+)", text) or re.fullmatch(
                r"(.+?) ([Cc][oa]n ACM|[A-Za-z.]+)\*? ([\d.,]+)", text
            )
            if found is None:
                if re.match(r"[*-]|Printed|Special|I I$|[A-Z]+$|I[A-Z]+I$|\d+$", text):
                    continue
                raise ValueError(f"GLC by-elections {year} page {sheet}: unread line {text!r}")
            name = re.sub(r"^([A-Z][A-Za-z'·)]+)\. ", r"\1, ", found.group(1)) if year == 1981 else found.group(1)
            party = re.sub(r"[^A-Z]", "", found.group(2).upper())
            votes = number(found.group(3))
            if party not in GLC_BY_EARLY_PARTIES or votes is None:
                raise ValueError(f"GLC by-elections {year} page {sheet}: unread line {text!r}")
            candidates.append(
                {
                    "printed": division["printed"],
                    "day": division["day"],
                    "name": GLC_BY_EARLY_NAMES.get(name, name),
                    "party": GLC_BY_EARLY_PARTIES[party],
                    "votes": votes,
                    "elected": not any(
                        c["day"] == division["day"] and c["printed"] == division["printed"] for c in candidates
                    ),
                    "page": sheet,
                    "bbox": ilea_box(row, sizes[sheet - 1]),
                }
            )
    for division in divisions:
        mine = [c for c in candidates if c["printed"] == division["printed"] and c["day"] == division["day"]]
        if division["day"] is None or not mine:
            raise ValueError(f"GLC by-election {division['printed']}: no date or candidates")
        if mine[0]["votes"] is not None:
            for rank, c in enumerate(sorted(mine, key=lambda c: -c["votes"])):
                c["number"] = rank + 1
            if mine[0]["number"] != 1:
                raise ValueError(f"GLC by-election {division['printed']}: first candidate did not poll the most")
        if division["report"] not in GLC_BY_EARLY_AREAS:
            division["code"] = None
            continue
        match = areas.filter(
            pl.col("year") == GLC_BY_EARLY_AREAS[division["report"]],
            pl.col("printed") == division["printed"],
        )
        if match.height != 1:
            raise ValueError(f"GLC by-election {division['printed']}: no borough area")
        division["code"] = match["code"][0]
    pl.DataFrame(divisions).write_parquet(PARSED / "glc_by_early_divisions.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "glc_by_early_candidates.parquet")
    print(f"{len(divisions)} GLC by-elections and {len(candidates)} candidates, 1964 to 1981")


def emit() -> Path:
    wards = pl.read_parquet(PARSED / "wards.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    for year in BOOKS:
        listed = candidates.filter(pl.col("year") == year).height
        printed = sum(count for _, count, _ in TOTALS[year].values())
        if listed != printed or wards.filter(pl.col("year") == year).is_empty():
            raise ValueError(f"{year}: {listed} parsed candidates against {printed} printed; run ea parse {SOURCE}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="London borough council elections "
            + ", ".join(map(str, sorted(BOOKS)))
            + " by ward, and Greater London Council elections "
            + ", ".join(map(str, sorted(GLC)))
            + " by electoral division and of 1964, 1967, and 1970 by borough, the Greater London Council "
            "by-elections of 1964 to 1985, and the Inner London Education Authority election of 1986 by constituency, from the London Research Centre's, the London County "
            "Council's, the Greater London Council's, and the London Residuary Body's reports",
            polity="GB",
        )
        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, text: str) -> None:
            if entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.parliament first")
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        others = {
            contest
            for (contest,) in conn.execute(
                "select rs.contest_id from result_set rs join changeset c on c.id = rs.changeset_id "
                "where c.pipeline <> %s and rs.is_default",
                [SOURCE],
            ).fetchall()
        }
        party_ids: dict[str, UUID | None] = {}

        def party(year: int, code: str) -> UUID | None:
            if code == "IND":
                return None
            label = PARTIES[year][code]
            if label not in party_ids:
                found = (
                    cs.key(*leap.RESOLVED[label])
                    if label in leap.RESOLVED
                    else known.get(leh.fold(PARTY_NAMES.get(label, label)))
                )
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted and found not in party_ids.values():
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                party_ids[label] = found
            return party_ids[label]

        for year, (day, url) in BOOKS.items():
            source = cs.source(PUBLISHER, TITLES[year])
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="compendium",
                    publisher=PUBLISHER,
                    title=TITLES[year],
                    url=DATASET,
                    license="Published by the London Research Centre; the Greater London Authority lists it on the "
                    "London Datastore with no licence stated",
                    license_url=None,
                    attribution=f"{PUBLISHER}, {TITLES[year]}.",
                )
            artifact = raw.cached(SOURCE, url)
            artifact_id = cs.artifact(artifact, source)
            page_ids: dict[int, UUID] = {}

            def page(number: int) -> UUID:
                if number not in page_ids:
                    image = PAGES / f"{artifact.sha256}-{number}.png"
                    if not image.exists():
                        PAGES.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                            + [str(artifact.path), str(image.with_suffix(""))],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page_ids[number] = cs.page(artifact_id, number)
                    cs.add(
                        "source_page",
                        id=page_ids[number],
                        artifact_id=artifact_id,
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                return page_ids[number]

            national = cs.key("gb.election", f"{day}:local:gb")
            if national in cs.minted:
                cs.add(
                    "election",
                    id=national,
                    polity_id=gb,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(national, f"{year} local elections in England")
                cs.add("slug", path=f"gb/{day}-local", entity_id=national)
            held = wards.filter(pl.col("year") == year)
            lines = candidates.filter(pl.col("year") == year)
            for code in held["la_code"].unique(maintain_order=True):
                area = cs.area("gb.lad", code)
                polity = cs.key("gb.polity", code)
                body = cs.key("gb.body", code)
                if cs.minted & {area, polity, body}:
                    raise ValueError(f"{code}: publish gb.leap first")
                found = conn.execute(
                    "select s.path, n.text from slug s join name n on n.entity_id = s.entity_id and n.is_preferred"
                    " where s.entity_id = %s order by s.is_current desc limit 1",
                    [polity],
                ).fetchone()
                if found is None:
                    raise ValueError(f"{code}: no slug or name for the council")
                path, council = found
                election = cs.key("gb.election", f"{day}:local:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {council} election")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                cs.add(
                    "note",
                    entity_id=election,
                    kind="methodology",
                    body_md=METHODOLOGY_1990 if year in SCANNED else METHODOLOGY + NOTES.get(year, ""),
                )
                for ward in held.filter(pl.col("la_code") == code).iter_rows(named=True):
                    display = ward["ward"]
                    ward_key = f"{code}:{slug(display)}:{year}"
                    ward_area = cs.area("gb.ward", ward_key)
                    if ward_area in cs.minted:
                        cs.add("area", id=ward_area, scheme_code="gb.ward")
                        cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                        name(ward_area, display)
                    unit = cs.unit("gb.ward", ward_key)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="gb.ward",
                            code=ward_key,
                            area_relation="equals",
                            area_id=ward_area,
                            kind="standard",
                            valid_from=None,
                        )
                        name(unit, display)
                    seats = ward["seats"]
                    tacit = ward["tacit"]
                    contest_key = f"{day}:local:{ward_key}"
                    contest = cs.key("gb.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats",
                        body_id=body,
                        district_area_id=ward_area,
                        magnitude=seats,
                        round_number=1,
                        electoral_system_id=systems["fptp" if seats == 1 else "block"],
                        default_result_kind="tacit" if tacit else "counted",
                    )
                    name(contest, f"{display} ward, {council}, {year}")
                    cs.add("slug", path=f"{path}/{day}-local/{slug(display)}", entity_id=contest)
                    result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="tacit" if tacit else "counted",
                        status="active",
                        source_id=source,
                        is_default=contest not in others,
                        completeness=1,
                    )
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"page": ward["page"], "borough": ward["borough"], "ward": display},
                    )

                    def value(option: UUID | None, measure: int, figure: int, number: int, box: list[float]) -> None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=DIGITIZED,
                            value=figure,
                        )
                        x0, y0, x1, y1 = box
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            page_id=page(number),
                            source_id=None,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )

                    if not tacit:
                        value(None, REGISTERED, ward["electorate"], ward["turnout_page"], ward["turnout_box"])
                        value(None, BALLOTS, ward["ballots"], ward["turnout_page"], ward["turnout_box"])
                        value(None, VALID, ward["valid"], ward["rejected_page"], ward["rejected_box"])
                        value(None, INVALID, ward["invalid"], ward["rejected_page"], ward["rejected_box"])
                    for line in lines.filter((pl.col("la_code") == code) & (pl.col("ward") == display)).iter_rows(
                        named=True
                    ):
                        option_key = f"{contest_key}:{line['number']}"
                        owner = party(year, line["party"])
                        option = cs.key(f"{SOURCE}.option", option_key)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=owner,
                            ballot_label=line["name"],
                        )
                        person = cs.key(f"{SOURCE}.person", option_key)
                        cs.add("person", id=person)
                        surname, _, forenames = line["name"].partition(", ")
                        name(person, f"{forenames} {surname}".strip())
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", option_key),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=line["name"],
                            member_of_party_id=owner,
                            is_independent=line["party"] == "IND",
                        )
                        if not tacit:
                            value(option, VOTES, line["votes"], line["page"], line["bbox"])
                        if line["elected"]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        london = cs.key("gb.polity", "E12000007")
        council = cs.key("gb.body", "greater-london-council")
        if london in cs.minted:
            raise ValueError("publish gb.london first")
        if council in cs.minted:
            cs.add("body", id=council, polity_id=london, kind="unicameral")
            name(council, "Greater London Council")
        cs.add("body_size", body_id=council, seats=92, valid_from=GLC[1973][0], valid_to=date(1986, 4, 1))
        divisions = pl.read_parquet(PARSED / "glc_divisions.parquet")
        standing = pl.read_parquet(PARSED / "glc_candidates.parquet")
        constituencies = {
            fold(text): (code, text)
            for code, text in conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id"
                " join name n on n.entity_id = a.id and n.is_preferred"
                " where a.scheme_code = 'gb.pcon' and c.code like '1974:eng-%%'"
            ).fetchall()
        }
        glc_parties: dict[str, UUID | None] = {}
        units: set[UUID] = set()

        def glc_party(label: str) -> UUID | None:
            if label == "Independent":
                return None
            if label not in glc_parties:
                found = cs.key(*GLC_RESOLVED[label]) if label in GLC_RESOLVED else known.get(leh.fold(label))
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted and found not in glc_parties.values():
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                glc_parties[label] = found
            return glc_parties[label]

        reports: dict[int, tuple[UUID, Callable[[int], UUID]]] = {}

        def pager(artifact: raw.Artifact, artifact_id: UUID) -> Callable[[int], UUID]:
            @functools.cache
            def page(number: int) -> UUID:
                image = PAGES / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(artifact.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                found = cs.page(artifact_id, number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifact_id,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                return found

            return page

        for year, (day, url) in GLC.items():
            title = f"Greater London Council Election {year}"
            source = cs.source(GLC_PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="compendium",
                    publisher=GLC_PUBLISHER,
                    title=title,
                    url=DATASET,
                    license="Published by the Greater London Council; the Greater London Authority lists it on the "
                    "London Datastore with no licence stated",
                    license_url=None,
                    attribution=f"{GLC_PUBLISHER}, {title}.",
                )
            artifact = raw.cached(SOURCE, url)
            artifact_id = cs.artifact(artifact, source)
            glc_page = pager(artifact, artifact_id)
            reports[year] = (source, glc_page)
            election = cs.key("gb.election", f"{day}:glc")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} Greater London Council election")
            cs.add("slug", path=f"gb/london/{day}-glc", entity_id=election)
            cs.add("note", entity_id=election, kind="methodology", body_md=GLC_METHODOLOGY + GLC_NOTES.get(year, ""))
            lines = standing.filter(pl.col("year") == year)
            for division in divisions.filter(pl.col("year") == year).iter_rows(named=True):
                printed = division["printed"].title().replace(" And ", " and ").replace(" Of ", " of ")
                code, display = constituencies[fold(GLC_DIVISIONS.get(printed, printed))]
                area = cs.area("gb.pcon", code)
                if area in cs.minted:
                    raise ValueError(f"{code}: no 1974 constituency area")
                unit = cs.unit("gb.pcon", code)
                if unit in cs.minted and unit not in units:
                    units.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=gb,
                        scheme_code="gb.pcon",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                contest_key = f"{day}:glc:{code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=council,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=systems["fptp"],
                    default_result_kind="counted",
                )
                name(contest, f"{display}, Greater London Council, {year}")
                cs.add("slug", path=f"gb/london/{day}-glc/{slug(display)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=contest not in others,
                    completeness=1,
                )
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"page": division["page"], "division": division["printed"]},
                )

                def glc_value(option: UUID | None, measure: int, figure: int, number: int, box: list[float]) -> None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=DIGITIZED,
                        value=figure,
                    )
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        page_id=glc_page(number),
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )

                glc_value(None, REGISTERED, division["electorate"], division["page"], division["bbox"])
                for line in lines.filter(pl.col("division") == division["printed"]).iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    label = GLC_PARTIES[year][line["party"]][0]
                    owner = glc_party(label)
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"],
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    surname, _, rest = line["name"].partition(", ")
                    forenames = rest.split(", ")[0]
                    name(person, f"{forenames} {surname}".strip())
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        member_of_party_id=owner,
                        is_independent=label == "Independent",
                    )
                    glc_value(option, VOTES, line["votes"], line["page"], line["bbox"])
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        first, end = EARLY[1964][0], GLC[1973][0]
        cs.add("body_size", body_id=council, seats=100, valid_from=first, valid_to=end)
        if conn.execute("select 1 from scheme where code = 'gb.glc_area'").fetchone() is None:
            cs.add("scheme", code="gb.glc_area", polity_id=gb, kind="electoral_district", rank=2, noun="electoral area")
        joined = cs.area("gb.glc_area", EARLY_AREA)
        if joined in cs.minted:
            cs.add("area", id=joined, scheme_code="gb.glc_area")
            name(joined, EARLY_AREA_NAME)
            cs.add("area_code", area_id=joined, code=EARLY_AREA, valid_from=first, valid_to=end)
            cs.add(
                "area_parent", area_id=joined, parent_id=cs.area("gb.country", "ENG"), valid_from=None, valid_to=None
            )
        for code in ("E09000033", "E09000001"):
            cs.add("area_membership", area_id=cs.area("gb.lad", code), parent_id=joined, valid_from=first, valid_to=end)
        joined_unit = cs.unit("gb.glc_area", EARLY_AREA)
        if joined_unit in cs.minted:
            cs.add(
                "reporting_unit",
                id=joined_unit,
                polity_id=gb,
                scheme_code="gb.glc_area",
                code=EARLY_AREA,
                area_relation="equals",
                area_id=joined,
                kind="standard",
                valid_from=None,
            )
            name(joined_unit, EARLY_AREA_NAME)
        areas = pl.read_parquet(PARSED / "glc_areas.parquet")
        standing = pl.read_parquet(PARSED / "glc_early_candidates.parquet")
        for year, (day, url, _, _) in EARLY.items():
            publisher, title = EARLY_SOURCES[year]
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="compendium",
                    publisher=publisher,
                    title=title,
                    url=DATASET,
                    license=f"Published by the {publisher}; the Greater London Authority lists it on the London "
                    "Datastore with no licence stated",
                    license_url=None,
                    attribution=f"{publisher}, {title}.",
                )
            artifact = raw.cached(SOURCE, url)
            artifact_id = cs.artifact(artifact, source)
            early_page = pager(artifact, artifact_id)
            reports[year] = (source, early_page)
            election = cs.key("gb.election", f"{day}:glc")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} Greater London Council election")
            cs.add("slug", path=f"gb/london/{day}-glc", entity_id=election)
            cs.add("note", entity_id=election, kind="methodology", body_md=EARLY_METHODOLOGY + EARLY_NOTES[year])
            lines = standing.filter(pl.col("year") == year)
            for area_row in areas.filter(pl.col("year") == year).iter_rows(named=True):
                code = area_row["code"]
                joint = code == EARLY_AREA
                area = joined if joint else cs.area("gb.lad", code)
                unit = joined_unit if joint else cs.unit("gb.lad", code)
                if not joint and (area in cs.minted or unit in cs.minted):
                    raise ValueError(f"{code}: publish gb.leap first")
                display = (
                    "Westminster"
                    if joint
                    else area_row["printed"].title().replace(" And ", " and ").replace(" Upon ", " upon ")
                )
                contest_key = f"{day}:glc:{code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=council,
                    district_area_id=area,
                    magnitude=area_row["seats"],
                    round_number=1,
                    electoral_system_id=systems["block"],
                    default_result_kind="counted",
                )
                name(contest, f"{display}, Greater London Council, {year}")
                cs.add("slug", path=f"gb/london/{day}-glc/{slug(display)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=contest not in others,
                    completeness=1,
                )
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"page": area_row["page"], "area": area_row["printed"]},
                )

                def early_value(option: UUID | None, measure: int, figure: int, number: int, box: list[float]) -> None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=DIGITIZED,
                        value=figure,
                    )
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        page_id=early_page(number),
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )

                early_value(
                    None, REGISTERED, area_row["electorate"], area_row["stats_page"], area_row["electorate_bbox"]
                )
                early_value(None, BALLOTS, area_row["voting"], area_row["stats_page"], area_row["voting_bbox"])
                for line in lines.filter(pl.col("area") == area_row["printed"]).iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    label = EARLY_PARTIES[year][line["party"]][0]
                    owner = glc_party(label)
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"],
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    name(person, early_person(year, line["name"]))
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        member_of_party_id=owner,
                        is_independent=label == "Independent",
                    )
                    early_value(option, VOTES, line["votes"], line["page"], line["bbox"])
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        authority = cs.key("gb.body", "inner-london-education-authority")
        if authority in cs.minted:
            cs.add("body", id=authority, polity_id=london, kind="unicameral")
            name(authority, "Inner London Education Authority")
        cs.add("body_size", body_id=authority, seats=58, valid_from=ILEA_DAY, valid_to=date(1990, 4, 1))
        source = cs.source(ILEA_PUBLISHER, ILEA_TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="compendium",
                publisher=ILEA_PUBLISHER,
                title=ILEA_TITLE,
                url=DATASET,
                license=f"Published by the {ILEA_PUBLISHER}; the Greater London Authority lists it on the London "
                "Datastore with no licence stated",
                license_url=None,
                attribution=f"{ILEA_PUBLISHER}, {ILEA_TITLE}.",
            )
        artifact = raw.cached(SOURCE, ILEA_URL)
        artifact_id = cs.artifact(artifact, source)
        ilea_page = pager(artifact, artifact_id)

        election = cs.key("gb.election", f"{ILEA_DAY}:ilea")
        cs.add(
            "election",
            id=election,
            polity_id=london,
            kind="general",
            date_from=ILEA_DAY,
            date_to=ILEA_DAY,
            status="held",
        )
        name(election, "1986 Inner London Education Authority election")
        cs.add("slug", path=f"gb/london/{ILEA_DAY}-ilea", entity_id=election)
        cs.add("note", entity_id=election, kind="methodology", body_md=ILEA_METHODOLOGY)
        alliance = cs.key(f"{SOURCE}.alliance", f"{ILEA_DAY}:sdp-liberal")
        cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
        cs.add("name", entity_id=alliance, text="SDP–Liberal Alliance", lang="en", kind="official", is_preferred=True)
        for member in ("Liberal", "Social Democratic Party"):
            cs.add("alliance_member", alliance_id=alliance, party_id=glc_party(member), role=None)
        cs.add("citation", entity_id=alliance, source_id=source, locator={"page": 4})
        pcon_1983 = {
            fold(text): (code, text)
            for code, text in conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id"
                " join name n on n.entity_id = a.id and n.is_preferred"
                " where a.scheme_code = 'gb.pcon' and c.code like '1983:eng-%%'"
            ).fetchall()
        }
        standing = pl.read_parquet(PARSED / "ilea_candidates.parquet")
        for seat in pl.read_parquet(PARSED / "ilea_constituencies.parquet").iter_rows(named=True):
            code, display = pcon_1983[seat["key"]]
            area = cs.area("gb.pcon", code)
            if area in cs.minted:
                raise ValueError(f"{code}: no 1983 constituency area")
            unit = cs.unit("gb.pcon", code)
            if unit in cs.minted and unit not in units:
                units.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=gb,
                    scheme_code="gb.pcon",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            contest_key = f"{ILEA_DAY}:ilea:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=authority,
                district_area_id=area,
                magnitude=2,
                round_number=1,
                electoral_system_id=systems["block"],
                default_result_kind="counted",
            )
            name(contest, f"{display}, Inner London Education Authority, 1986")
            cs.add("slug", path=f"gb/london/{ILEA_DAY}-ilea/{slug(display)}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=contest not in others,
                completeness=1,
            )
            cs.add(
                "citation",
                entity_id=result_set,
                source_id=source,
                locator={"page": seat["page"], "constituency": seat["printed"]},
            )

            def ilea_value(option: UUID | None, measure: int, figure: int, number: int, box: list[float]) -> None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=DIGITIZED,
                    value=figure,
                )
                x0, y0, x1, y1 = box
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=ilea_page(number),
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )

            ilea_value(None, REGISTERED, seat["electorate"], seat["electorate_page"], seat["electorate_bbox"])
            ilea_value(None, INVALID, seat["rejected"], ILEA_STATS["rejected"], seat["rejected_bbox"])
            for line in standing.filter(pl.col("seat") == seat["key"]).iter_rows(named=True):
                option_key = f"{contest_key}:{line['number']}"
                label = ILEA_PARTIES[line["party"]][0]
                owner = glc_party(label)
                option = cs.key(f"{SOURCE}.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=owner,
                    alliance_id=alliance if line["party"] in ILEA_ALLIANCE else None,
                    ballot_label=line["name"],
                )
                person = cs.key(f"{SOURCE}.person", option_key)
                cs.add("person", id=person)
                surname, _, forenames = line["name"].partition(", ")
                name(person, f"{forenames.removeprefix('Dr ')} {surname}")
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=line["name"],
                    member_of_party_id=owner,
                    is_independent=label == "Independent",
                )
                ilea_value(option, VOTES, line["votes"], line["page"], line["bbox"])
                if line["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        held = pl.read_parquet(PARSED / "glc_by_divisions.parquet")
        standing = pl.read_parquet(PARSED / "glc_by_candidates.parquet")
        for (day,), on_day in held.sort("day").group_by("day", maintain_order=True):
            spoken = f"{day:%B} {day.day}, {day.year}"
            seats = []
            for division in on_day.iter_rows(named=True):
                printed = division["printed"].title().replace(" And ", " and ")
                seats.append((division, *constituencies[fold(GLC_BY_DIVISIONS.get(printed, printed))]))
            election = cs.key("gb.election", f"{day}:glc-by")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                kind="by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(
                election,
                f"{seats[0][2]} Greater London Council by-election, {spoken}"
                if len(seats) == 1
                else f"Greater London Council by-elections, {spoken}",
            )
            cs.add("slug", path=f"gb/london/{day}-glc-by-election", entity_id=election)
            causes = " ".join(f"{display} was vacated by the resignation of {d['cause']}." for d, _, display in seats)
            cs.add("note", entity_id=election, kind="methodology", body_md=f"{GLC_BY_METHODOLOGY} {causes}")
            for division, code, display in seats:
                area = cs.area("gb.pcon", code)
                unit = cs.unit("gb.pcon", code)
                if unit in cs.minted and unit not in units:
                    raise ValueError(f"{code}: no 1974 constituency unit")
                contest_key = f"{day}:glc:{code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=council,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=systems["fptp"],
                    default_result_kind="counted",
                )
                name(contest, f"{display}, Greater London Council by-election, {day.year}")
                cs.add("slug", path=f"gb/london/{day}-glc-by-election/{slug(display)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=contest not in others,
                    completeness=1,
                )
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"page": division["page"], "appendix": "B", "division": division["printed"]},
                )

                def by_value(option: UUID | None, measure: int, figure: int, number: int, box: list[float]) -> None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=DIGITIZED,
                        value=figure,
                    )
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        page_id=ilea_page(number),
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )

                by_value(None, REGISTERED, division["electorate"], division["page"], division["electorate_bbox"])
                for line in standing.filter(pl.col("division") == division["printed"]).iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    owner = glc_party(GLC_BY_PARTIES[line["party"]])
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"],
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    surname, _, forenames = line["name"].partition(", ")
                    name(person, f"{forenames} {surname}")
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        member_of_party_id=owner,
                        is_independent=False,
                    )
                    by_value(option, VOTES, line["votes"], line["page"], line["bbox"])
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        held = pl.read_parquet(PARSED / "glc_by_early_divisions.parquet")
        standing = pl.read_parquet(PARSED / "glc_by_early_candidates.parquet")
        for (day,), on_day in held.sort("day").group_by("day", maintain_order=True):
            spoken = f"{day:%B} {day.day}, {day.year}"
            seats = []
            for division in on_day.iter_rows(named=True):
                printed = division["printed"].title().replace(" And ", " and ").replace(" Upon ", " upon ")
                if division["code"] is None:
                    code, display = constituencies[fold(printed)]
                    seats.append((division, "gb.pcon", code, display))
                else:
                    seats.append((division, "gb.lad", division["code"], printed))
            election = cs.key("gb.election", f"{day}:glc-by")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                kind="by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(
                election,
                f"{seats[0][3]} Greater London Council by-election, {spoken}"
                if len(seats) == 1
                else f"Greater London Council by-elections, {spoken}",
            )
            cs.add("slug", path=f"gb/london/{day}-glc-by-election", entity_id=election)
            cs.add(
                "note",
                entity_id=election,
                kind="methodology",
                body_md=GLC_BY_EARLY_METHODOLOGY[seats[0][0]["report"]],
            )
            for division, scheme, code, display in seats:
                source, report_page = reports[division["report"]]
                area = cs.area(scheme, code)
                unit = cs.unit(scheme, code)
                if area in cs.minted or (unit in cs.minted and unit not in units):
                    raise ValueError(f"{code}: no {scheme} area or unit")
                tacit = standing.filter(pl.col("printed") == division["printed"], pl.col("day") == day)
                tacit = tacit["votes"].is_null().all()
                contest_key = f"{day}:glc:{code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=council,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=systems["fptp"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{display}, Greater London Council by-election, {day.year}")
                cs.add("slug", path=f"gb/london/{day}-glc-by-election/{slug(display)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=contest not in others,
                    completeness=1,
                )
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"page": division["page"], "division": division["printed"]},
                )
                lines_ = standing.filter(pl.col("printed") == division["printed"], pl.col("day") == day)
                for line in lines_.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    label = line["party"]
                    owner = None if label is None else glc_party(label)
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"],
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    surname, _, rest = line["name"].partition(", ")
                    if division["report"] in EARLY:
                        spoken_name = surname.removeprefix("Mrs ")
                    else:
                        spoken_name = " ".join(w for w in [*rest.split(), surname] if w not in {"Mrs.", "Miss"})
                    name(person, spoken_name)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        member_of_party_id=owner,
                        is_independent=label == "Independent",
                    )
                    if line["votes"] is not None:
                        x0, y0, x1, y1 = line["bbox"]
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=DIGITIZED,
                            value=line["votes"],
                        )
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            page_id=report_page(line["page"]),
                            source_id=None,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
