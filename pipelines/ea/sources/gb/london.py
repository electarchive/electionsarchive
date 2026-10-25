import json
import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import fastexcel

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh
from ea.store import connect

SOURCE = "gb.london"
VERSION = "5"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED = 1, 2, 3, 4, 5, 21
REPORT_2004 = (
    "https://data.london.gov.uk/download/e585w/94efc8af-b7ce-4d60-9b18-e587084e207f/GLA-mayoral-election-2004.pdf"
)
GLA = "Greater London Authority"
GLRO = "Greater London Returning Officer"
PARTIES = {
    "LAB": "Labour Party",
    "CON": "Conservative Party",
    "LD": "Liberal Democrats",
    "UKIP": "UK Independence Party",
    "R": "Respect",
    "BNP": "British National Party",
    "GRE": "Green Party",
    "CPA": "Christian Peoples Alliance",
    "IWCA": "Independent Working Class Association",
    "PMSS": "Pro-Motorist Small Shop",
    "NLP": "Natural Law Party",
    "CC": "Christian Choice",
    "LL": "Left List",
    "ED": "English Democrats",
}
PEOPLE = {
    "Ken Livingstone": "Q220723",
    "Boris Johnson": "Q180589",
    "Sadiq Khan": "Q334155",
    "Zac Goldsmith": "Q391562",
    "Steven Norris": "Q7615093",
    "Frank Dobson": "Q300182",
    "Susan Kramer": "Q332968",
    "Simon Hughes": "Q334313",
    "George Galloway": "Q332483",
    "Gerard Batten": "Q1510131",
    "Siân Berry": "Q7533470",
}
Candidate = tuple[str, str | None, int, int | None]
ELECTIONS: dict[int, tuple[date, str, str, str, int | None, list[Candidate]]] = {
    2000: (
        date(2000, 5, 4),
        REPORT_2004,
        GLA,
        "The 2004 London Elections",
        None,
        [
            ("Ken Livingstone", None, 667877, 776427),
            ("Steven Norris", "CON", 464434, 564137),
            ("Frank Dobson", "LAB", 223884, None),
            ("Susan Kramer", "LD", 203452, None),
            ("Balram Gidoomal", "CPA", 42060, None),
            ("Darren Johnson", "GRE", 38121, None),
            ("Michael Newland", "BNP", 33569, None),
            ("Nicholas Hockney", "UKIP", 16324, None),
            ("Geoffrey Ben-Nathan", "PMSS", 9956, None),
            ("Ashwinkumar Tanna", None, 9015, None),
            ("Geoffrey Clements", "NLP", 5470, None),
        ],
    ),
    2004: (
        date(2004, 6, 10),
        REPORT_2004,
        GLA,
        "The 2004 London Elections",
        None,
        [
            ("Ken Livingstone", "LAB", 685548, 828390),
            ("Steven Norris", "CON", 542423, 667180),
            ("Simon Hughes", "LD", 284647, None),
            ("Francis Maloney", "UKIP", 115666, None),
            ("Lindsey German", "R", 61731, None),
            ("Julian Leppert", "BNP", 58407, None),
            ("Darren Johnson", "GRE", 57332, None),
            ("Balram Gidoomal", "CPA", 41698, None),
            ("Lorna Reid", "IWCA", 9542, None),
            ("Puvanarani Nagalingam", None, 6692, None),
        ],
    ),
    2008: (
        date(2008, 5, 1),
        "https://data.london.gov.uk/download/2nplx/53d5718d-9e17-47fc-811e-6f95a4d3f293/GLA-mayoral-elections-2008.pdf",
        GLA,
        "The 2008 London Elections",
        None,
        [
            ("Boris Johnson", "CON", 1044067, 1169046),
            ("Ken Livingstone", "LAB", 894317, 1029406),
            ("Brian Paddick", "LD", 236752, None),
            ("Siân Berry", "GRE", 77396, None),
            ("Richard Barnbrook", "BNP", 69753, None),
            ("Alan Craig", "CC", 39266, None),
            ("Gerard Batten", "UKIP", 22435, None),
            ("Lindsey German", "LL", 16803, None),
            ("Matt O'Connor", "ED", 10700, None),
            ("Winston McKenzie", None, 5396, None),
        ],
    ),
    2012: (
        date(2012, 5, 3),
        "https://www.londonelects.org.uk/files/Results%20factsheets%202012.pdf",
        "London Elects",
        "2012 election results for the Mayor of London and the London Assembly",
        None,
        [
            ("Boris Johnson", "Conservative Party", 971931, 1054811),
            ("Ken Livingstone", "Labour Party", 889918, 992273),
            ("Jenny Jones", "Green Party", 98913, None),
            ("Brian Paddick", "Liberal Democrats", 91774, None),
            ("Siobhan Benita", None, 83914, None),
            ("Lawrence Webb", "UK Independence Party", 43274, None),
            ("Carlos Cortiglia", "British National Party", 28751, None),
        ],
    ),
    2016: (
        date(2016, 5, 5),
        "https://www.londonelects.org.uk/files/Mayor%20of%20London%202016.pdf",
        GLRO,
        "Election of the London Mayor: Declaration of Results of Poll, 5 May 2016",
        49871,
        [
            ("Sadiq Khan", "Labour Party", 1148716, 1310143),
            ("Zac Goldsmith", "The Conservative Party Candidate", 909755, 994614),
            ("Siân Berry", "Green Party", 150673, None),
            ("Caroline Pidgeon", "London Liberal Democrats", 120005, None),
            ("Peter Whittle", "UK Independence Party (UKIP)", 94373, None),
            ("Sophie Walker", "Women's Equality Party", 53055, None),
            ("George Galloway", "Respect (George Galloway)", 37007, None),
            ("Paul Golding", "Britain First - Putting British people first", 31372, None),
            ("Lee Harris", "Cannabis is Safer than Alcohol", 20537, None),
            ("David Furness", "British National Party", 13325, None),
            ("Prince Zylinski", None, 13202, None),
            ("Ankit Love", "One Love Party", 4941, None),
        ],
    ),
    2021: (
        date(2021, 5, 6),
        "https://www.londonelects.org.uk/files/Mayoral%20Final%20Results%202021.pdf",
        GLRO,
        "Election of the Mayor of London: Official Declaration of Results of Poll, 6 May 2021",
        114201,
        [
            ("Sadiq Khan", "Labour Party", 1013721, 1206034),
            ("Shaun Bailey", "Conservative Party Candidate", 893051, 977601),
            ("Siân Berry", "Green Party", 197976, None),
            ("Luisa Porritt", "Liberal Democrats", 111716, None),
            ("Niko Omilana", None, 49628, None),
            ("Laurence Fox", "The Reclaim Party", 47634, None),
            ("Brian Rose", "London Real Party", 31111, None),
            ("Richard Hewison", "Rejoin EU", 28012, None),
            ("Count Binface", "Count Binface for Mayor of London", 24775, None),
            ("Mandu Reid", "Vote Women's Equality Party on orange", 21182, None),
            ("Piers Corbyn", "Let London Live", 20604, None),
            ("Vanessa Hudson", "Animal Welfare Party-People, Animals, Environment", 16826, None),
            ("Peter Gammons", "UKIP", 14393, None),
            ("Farah London", None, 11869, None),
            ("David Kurten", "Heritage Party", 11025, None),
            ("Nims Obunge", None, 9682, None),
            ("Steve Kelleher", "Social Democratic Party", 8764, None),
            ("Kam Balayev", "Renew", 7774, None),
            ("Max Fosh", None, 6309, None),
            ("Valerie Brown", "The Burning Pink Party", 5305, None),
        ],
    ),
    2024: (
        date(2024, 5, 2),
        "https://www.londonelects.org.uk/files/total_mayoral_results_2024.pdf",
        GLRO,
        "Election of the Mayor of London: Official Declaration of Result of Poll, 2 May 2024",
        11127,
        [
            ("Sadiq Khan", "Labour Party", 1088225, None),
            ("Susan Hall", "The Conservative Party Candidate", 812397, None),
            ("Rob Blackie", "Liberal Democrat", 145184, None),
            ("Zoë Garbett", "The Green Party", 145114, None),
            ("Howard Cox", "ReformUK – London Deserves Better", 78865, None),
            ("Natalie Campbell", None, 47815, None),
            ("Amy Gallagher", "Social Democratic Party", 34449, None),
            ("Femy Amin", "Animal Welfare Party - People, Animals, Environment", 29280, None),
            ("Andreas Michli", None, 26121, None),
            ("Tarun Ghulati", None, 24702, None),
            ("Count Binface", "Count Binface for Mayor of London", 24260, None),
            ("Nick Scanlon", "Britain First - No To Immigration", 20519, None),
            ("Brian Rose", "London Real Party – Transform London", 7501, None),
        ],
    ),
}
SV_UNTIL = 2021
COUNTED: dict[int, tuple[int, int, int]] = {
    2000: (5089300, 1752303, 38141),
    2004: (5197792, 1920560, 56874),
    2008: (5435612, 2457918, 41032),
    2024: (6162428, 2495559, 11127),
}
CONSTITUENCIES = {
    "E32000001": "Barnet and Camden",
    "E32000002": "Bexley and Bromley",
    "E32000003": "Brent and Harrow",
    "E32000004": "City and East",
    "E32000005": "Croydon and Sutton",
    "E32000006": "Ealing and Hillingdon",
    "E32000007": "Enfield and Haringey",
    "E32000008": "Greenwich and Lewisham",
    "E32000009": "Havering and Redbridge",
    "E32000010": "Lambeth and Southwark",
    "E32000011": "Merton and Wandsworth",
    "E32000012": "North East",
    "E32000013": "South West",
    "E32000014": "West Central",
}
LAC_SHAPES = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/London_Assembly_Constituencies_Dec_2018_FCB_EN_2022"
    "/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"
)
LAC_NOTE = (
    "The 14 London Assembly constituencies have kept the boroughs the Greater London Authority Act 1999 gave them "
    "since the first election in 2000, so the Office for National Statistics' December 2018 full-extent boundaries "
    "are drawn for every election."
)
DATASTORE = "https://data.london.gov.uk/download"
FILE_2000 = f"{DATASTORE}/2l8n5/8b819146-fafb-4b75-86b4-619519857ad1/gla-elections-2000.xls"
LONDON_ELECTS = "https://www.londonelects.org.uk/files"
WARDS_2021 = f"{LONDON_ELECTS}/2021%20London%20Elects%20-%20Ward%20Level%20Data%20Release.xlsx"
Sheet = tuple[str, str, str, int, int, int | None, list[int], int, int, int, int | None]
SHEETS: dict[int, list[Sheet]] = {
    2004: [
        (
            "lad",
            f"{DATASTORE}/e585w/db2ee60f-6788-438f-85a5-ada35ef8a3e7/gla-elections-votes-party-borough-2004.xls",
            "2004",
            2,
            1,
            2,
            list(range(3, 13)),
            13,
            18,
            34,
            None,
        )
    ],
    2008: [
        (
            "lad",
            f"{DATASTORE}/2nplx/5b19e7ba-3014-4cd5-a13a-dca1c6c40a44/gla-elections-votes-party-borough-2008.xls",
            "2008",
            2,
            1,
            2,
            list(range(3, 13)),
            13,
            20,
            35,
            None,
        )
    ],
    2012: [
        (
            "lac",
            f"{DATASTORE}/e79kk/0de17f91-560c-42a9-9128-c2530a430972/gla-elections-2012-constituency.xls",
            "Mayoral",
            1,
            0,
            None,
            list(range(5, 12)),
            12,
            13,
            2,
            None,
        )
    ],
    2016: [
        (
            "lad",
            f"{DATASTORE}/emq4p/01f4ff3a-c562-4d61-977f-c2dfb36694ce/gla-elections-votes-all-2016.xlsx",
            "Boroughs",
            2,
            0,
            2,
            list(range(8, 20)),
            20,
            26,
            5,
            None,
        ),
        (
            "lac",
            f"{DATASTORE}/emq4p/01f4ff3a-c562-4d61-977f-c2dfb36694ce/gla-elections-votes-all-2016.xlsx",
            "Constituencies",
            2,
            1,
            None,
            list(range(6, 18)),
            18,
            24,
            3,
            None,
        ),
    ],
    2021: [("ward", WARDS_2021, "All", 1, 0, 3, list(range(5, 25)), 25, 31, 4, 32)],
    2024: [
        (
            "lac",
            f"{LONDON_ELECTS}/GLA%20Election%20May%202024%20Results%20Summary.xlsx",
            "1. Mayor of London",
            2,
            0,
            None,
            list(range(1, 14)),
            14,
            19,
            21,
            20,
        )
    ],
}
SHEET_TITLES = {
    FILE_2000: (GLA, "London Elections Results, 2000"),
    SHEETS[2004][0][1]: (GLA, "London Elections Results 2004, Borough"),
    SHEETS[2008][0][1]: (GLA, "London Elections Results 2008, Borough"),
    SHEETS[2012][0][1]: (GLA, "London Elections Results 2012, Constituency"),
    SHEETS[2016][0][1]: (GLA, "London Elections Results 2016, Wards, Boroughs, Constituency"),
    WARDS_2021: ("London Elects", "2021 London Elects Ward Level Data Release"),
    SHEETS[2024][0][1]: ("London Elects", "GLA Election May 2024 Results Summary"),
}
DECLARATIONS_2021 = {
    code: f"{LONDON_ELECTS}/{name.replace(' ', '%20')}%202021.pdf" for code, name in CONSTITUENCIES.items()
}
DECLARATIONS_2016 = {
    code: f"{LONDON_ELECTS}/{quote(name.replace(' and ', ' & '))}%202016.pdf" for code, name in CONSTITUENCIES.items()
}
CORRECTED = {
    (2008, "E09000016", "Boris Johnson"): 50506,
    (2008, "E09000030", "Ken Livingstone"): 37631,
    (2008, "E09000030", "Lindsey German"): 796,
}
CORRECTED_NOTE = (
    "The GLA's 2008 borough file gives Boris Johnson 50,507 votes in Havering, and Ken Livingstone 37,630 and "
    "Lindsey German 797 in Tower Hamlets. Its report, The 2008 London Elections, prints 50,506, 37,631, and 796, "
    "which sum to the declared London totals, so the report's figures are shown."
)
RESOLVED = {
    "The Conservative Party Candidate": "Conservative Party",
    "Conservative Party Candidate": "Conservative Party",
    "London Liberal Democrats": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "The Green Party": "Green Party",
    "UK Independence Party (UKIP)": "UK Independence Party",
    "UKIP": "UK Independence Party",
    "Respect (George Galloway)": "Respect",
    "Britain First - Putting British people first": "Britain First",
    "Britain First - No To Immigration": "Britain First",
    "ReformUK – London Deserves Better": "Reform UK",
    "Count Binface for Mayor of London": "Count Binface Party",
    "London Real Party – Transform London": "London Real Party",
    "Vote Women's Equality Party on orange": "Women's Equality Party",
    "Animal Welfare Party-People, Animals, Environment": "Animal Welfare Party",
    "Animal Welfare Party - People, Animals, Environment": "Animal Welfare Party",
    "Labour": "Labour Party",
    "The Labour Party": "Labour Party",
    "Labour and Co-operative Party": "Labour Party",
    "Conservative": "Conservative Party",
    "Conservatives": "Conservative Party",
    "Conservative And Unionist Party": "Conservative Party",
    "Conservative and Unionist Party": "Conservative Party",
    "Green": "Green Party",
    "Green Party candidate": "Green Party",
    'Green Party - "vote Green on orange"': "Green Party",
    "Green Party – “vote Green on orange”": "Green Party",
    "Caroline Pidgeon's London Liberal Democrats": "Liberal Democrats",
    "Caroline Pidgeon’s London Liberal Democrats": "Liberal Democrats",
    "Lib Dem": "Liberal Democrats",
    "United Kingdom Independence Party": "UK Independence Party",
    "UK Independent Party": "UK Independence Party",
    "BNP": "British National Party",
    "Respect - The Unity Coalition": "Respect",
    "Respect - The Unity Coalition (George Galloway)": "Respect",
    "Communist": "Communist Party of Britain",
    "ReformUK": "Reform UK",
    "ReformUK - London Deserves Better": "Reform UK",
    "Heritage Party - Free Speech and Liberty": "Heritage Party",
    "The Socialist Party of Great Britain": "Socialist Party of Great Britain",
    "The Socialist Party (GB)": "Socialist Party of Great Britain",
    "The Socialist Party (SP-GB)": "Socialist Party of Great Britain",
    "Residents’ Association of London": "Residents' Association of London",
    "The Christain Choice": "Christian Choice",
    "English Democrats Party": "English Democrats",
    "The House Party - Homes for Londoners": "The House Party",
    "One London (Leader Damian Hockney)": "One London",
    "The Conservative Party candidate": "Conservative Party",
}
ASSEMBLY_SEATS = 25
LIST_SEATS = 11
FACTSHEET_2000 = f"{LONDON_ELECTS}/Results%20factsheet%202000.pdf"
LIST_2016 = f"{LONDON_ELECTS}/London-wide%20Assembly%202016.pdf"
FACTSHEET_2016 = f"{LONDON_ELECTS}/Results%20Factsheet%202016.pdf"
LIST_2021 = f"{LONDON_ELECTS}/London%20Assembly%20Final%20Results%202021.pdf"
MEMBERS_QUERY = (
    "SELECT ?p ?pLabel WHERE { ?p p:P39/ps:P39 wd:Q56573014 . "
    'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }'
)
MEMBERS_WIKIDATA = f"https://query.wikidata.org/sparql?format=json&query={quote(MEMBERS_QUERY)}"
ASSEMBLY_TITLES = {
    FACTSHEET_2000: ("London Elects", "Results factsheet 2000"),
    LIST_2016: (GLRO, "2016 GLA elections: declaration of the London-wide Assembly Member results"),
    FACTSHEET_2016: ("London Elects", "Results Factsheet 2016"),
    LIST_2021: (GLRO, "GLA 2021 elections: declaration of the London-wide Assembly Member results"),
}
AMS_NOTE = (
    "The London Assembly has 25 members. Fourteen are elected by first past the post in 14 constituencies, and 11 "
    "are elected from London-wide lists. Each voter has two Assembly votes: one for a constituency candidate and "
    "one for a party list or an individual candidate across London. Parties and candidates with less than 5 percent "
    "of the London-wide vote are excluded. The 11 London-wide seats are allocated to the remaining parties using the "
    "D'Hondt method. This divides each party's London-wide votes by one more than the number of seats it already "
    "holds, including constituency seats, so the list seats offset disproportion in the constituency results. The "
    "rules schema cannot yet express this adjusted divisor, so the declared seats are stored as published."
)
LIST_2000 = {
    "LAB": "Labour Party",
    "CON": "Conservative Party",
    "LD": "Liberal Democrats",
    "GRE": "Green Party",
    "CPA": "Christian Peoples Alliance",
    "BNP": "British National Party",
    "UKIP": "UK Independence Party",
    "LSA": "London Socialist Alliance",
    "IND": "Peter Tatchell",
    "CATP": "Campaign Against Tube Privatisation",
    "SLAB": "Socialist Labour Party",
    "PMSS": "Pro-Motorist Small Shop",
    "NLP": "Natural Law Party",
    "COMM": "Communist Party of Britain",
}
SUFFIXES_2004 = {
    "Conservative Party": "Conservative Party",
    "Labour Party": "Labour Party",
    "Liberal Democrats": "Liberal Democrats",
    "Green Party": "Green Party",
    "Green": "Green Party",
    "UK Independence Party": "UK Independence Party",
    "UK Independent Party": "UK Independence Party",
    "Socialist Alternative/Respect": "Socialist Alternative/Respect",
    "Respect": "Respect",
    "Christian Peoples Alliance": "Christian Peoples Alliance",
    "Communist Party of Britain": "Communist Party of Britain",
    "Residents’ Association of London": "Residents' Association of London",
    "Third Way": "Third Way",
    "Independent": None,
}
ABBREVIATIONS_2008 = {
    "AC": "Animals Count",
    "BNP": "British National Party",
    "CPACP": "Christian Peoples Alliance",
    "Cons": "Conservative Party",
    "EngDem": "English Democrats",
    "FEP": "Free England Party",
    "Green": "Green Party",
    "ISQMH": "Independents to Save Queen Mary's Hospital",
    "Ind": None,
    "Lab": "Labour Party",
    "LeftList": "Left List",
    "LibDem": "Liberal Democrats",
    "NF": "National Front",
    "Res": "Respect",
    "SA": "Socialist Alternative",
    "SP": "Socialist Party",
    "UKIP": "UK Independence Party",
    "Veritas": "Veritas",
}
HAVERING_2008 = {
    "evans": 78493,
    "saund": 35468,
    "islam": 12443,
    "webb": 12203,
    "gunstock": 9126,
    "brookes": 6487,
    "warren": 5533,
    "thorogood": 3450,
    "vincent": 1473,
}
HAVERING_2008_LIST = [3846, 18973, 5358, 67603, 2577, 7747, 36785, 813, 10810, 175, 3622, 6298, 337, 386]
HAVERING_2008_FIGURES = (164676, 3246, 165330, 2594)
ASSEMBLY_PEOPLE = {
    "Robert James Macgillivray Neill": "Q577771",
    "Robert J.M. Neill": "Q577771",
    "Robert John Blackman": "Q263875",
    "Robert J. Blackman": "Q263875",
    "Trevor Phillips": "Q7839374",
    "Louise Bloom": "Q6688641",
    "Elly Baker": "Q106777868",
    "Thomas Frederick Turrell": "Q76363534",
    "Bassam Mahfouz": "Q125772957",
    "James Tacuma Small-Edwards": "Q125789915",
    "Alessandro Georgiou": "Q125777501",
}
ALIASES = {"Anne Marie Bates Clarke": "Anne Clarke"}
PERSON_NAMES = {"Huge De Burgh": "Hugo de Burgh"}
WIKIPEDIA = "https://en.wikipedia.org/wiki/"
FORMER_MEMBERS = "https://www.london.gov.uk/who-we-are/what-london-assembly-does/about-london-assembly/former-members-london-assembly"
RESULTS_2024 = "https://gla.moderngov.co.uk/documents/s111325/"
REPLACEMENTS: dict[tuple[int, str], tuple[str, date, str, date, str, list[str]]] = {
    (2000, "lammy"): ("Jennette Arnold", date(2000, 7, 4), "day", date(2000, 7, 4), "day", ["David_Lammy"]),
    (2000, "bloom"): (
        "Mike Tuffrey",
        date(2002, 2, 18),
        "day",
        date(2002, 2, 18),
        "day",
        ["Mike_Tuffrey", "Louise_Bloom"],
    ),
    (2000, "phillips"): (
        "Diana Johnson",
        date(2003, 3, 1),
        "day",
        date(2003, 2, 1),
        "month",
        ["Diana_Johnson", "Trevor_Phillips"],
    ),
    (2000, "anderson"): (
        "Noel Lynch",
        date(2003, 5, 2),
        "day",
        date(2003, 3, 30),
        "day",
        ["Noel_Lynch", "Victor_Anderson_(Green_politician)"],
    ),
    (2004, "featherstone"): (
        "Geoff Pope",
        date(2005, 6, 6),
        "day",
        date(2005, 6, 1),
        "month",
        ["Geoff_Pope_(politician)"],
    ),
    (2012, "borwick"): (
        "Kemi Badenoch",
        date(2015, 9, 16),
        "day",
        date(2015, 9, 16),
        "day",
        ["Victoria_Borwick", "Kemi_Badenoch"],
    ),
    (2016, "badenoch"): (
        "Susan Hall",
        date(2017, 6, 20),
        "day",
        date(2017, 6, 8),
        "day",
        ["Kemi_Badenoch", "Susan_Hall"],
    ),
    (2016, "copley"): (
        "Alison Moore",
        date(2020, 3, 25),
        "day",
        date(2020, 3, 22),
        "day",
        ["Tom_Copley", "Alison_Moore_(politician)"],
    ),
    (2016, "twycross"): (
        "Murad Qureshi",
        date(2020, 3, 25),
        "day",
        date(2020, 3, 22),
        "day",
        ["Fiona_Twycross", "Murad_Qureshi"],
    ),
    (2024, "berry"): ("Zoë Garbett", date(2024, 5, 7), "day", date(2024, 5, 7), "day", ["Zoë_Garbett"]),
}
WINNERS_2012 = {"LAB": "Labour", "CON": "Conservative", "GREEN": "Green Party", "LD": "Liberal Democrats"}
LIST_MEMBERS: dict[int, dict[str, list[str]]] = {
    2000: {
        "Liberal Democrats": ["Sally Hamwee", "Graham Tope", "Lynne Featherstone", "Louise Bloom"],
        "Green Party": ["Darren Johnson", "Victor Anderson", "Jenny Jones"],
        "Labour Party": ["Trevor Phillips", "Samantha Heath", "David Lammy"],
        "Conservative Party": ["Eric Ollerenshaw"],
    },
    2004: {
        "Liberal Democrats": [
            "Lynne Choona Featherstone",
            "Graham Norman Tope",
            "Sally Rachel Hamwee",
            "Michael William Tuffrey",
            "Dee Doocey",
        ],
        "Green Party": ["Jenny Jones", "Darren Johnson"],
        "UK Independence Party": ["Nicholas Damian Hockney", "Peter Kenneth Hulme Cross"],
        "Labour Party": ["Felicia Nicolette Gavron", "Murad Qureshi"],
    },
    2008: {
        "British National Party": ["Richard Barnbrook"],
        "Conservative Party": ["Andrew Boff", "Victoria Borwick", "Gareth Bacon"],
        "Green Party": ["Jenny Jones", "Darren Johnson"],
        "Labour Party": ["Nicolette Gavron", "Murad Qureshi"],
        "Liberal Democrats": ["Caroline Pidgeon", "Michael Tuffrey", "Dee Doocey"],
    },
    2016: {
        "Green Party": ["Sian Berry", "Caroline Russell"],
        "UK Independence Party": ["Peter Whittle", "David Kurten"],
        "Liberal Democrats": ["Caroline Pidgeon"],
        "Conservative Party": ["Kemi Badenoch", "Andrew Boff", "Shaun Bailey"],
        "Labour Party": ["Fiona Twycross", "Tom Copley", "Nicky Gavron"],
    },
}


def fetch() -> None:
    for url in [*(url for _, url, _, _, _, _ in ELECTIONS.values()), *DECLARATIONS_2021.values()]:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in SHEET_TITLES:
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)
    raw.fetch(SOURCE, LAC_SHAPES, suffix=".geojson")
    for url in [*ASSEMBLY_TITLES, *DECLARATIONS_2016.values()]:
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, MEMBERS_WIKIDATA, suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayor of London elections 2000-2024, London-wide first and second rounds from the Greater London "
            "Returning Officer's declarations and the GLA's election reports, with first choices by Assembly "
            "constituency and borough from the GLA's and London Elects' result files, and London Assembly elections "
            "2000-2024, constituency members and the London-wide list with its members, from the same files",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        gb = cs.polity("GB")
        london = cs.key("gb.polity", "E12000007")
        region = cs.area("gb.region", "E12000007")
        if region in cs.minted:
            raise ValueError("publish gb.regions first: no London region area")
        cs.add("polity", id=london, kind="region", iso_code=None)
        cs.add("polity_parent", polity_id=london, parent_id=cs.polity("GB-ENG"))
        cs.add("polity_territory", polity_id=london, area_id=region)
        name(london, "Greater London")
        cs.add("slug", path="gb/london", entity_id=london)
        office = cs.key("gb.office", "mayor:london")
        cs.add("office", id=office, polity_id=london, body_id=None, kind="mayor")
        name(office, "Mayor of London")
        terms: list[tuple[str, int, date, UUID, UUID, str]] = []
        unit = cs.unit("gb.region", "E12000007")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=london,
            scheme_code="gb.region",
            code="E12000007",
            area_relation="equals",
            area_id=region,
            kind="standard",
            valid_from=None,
        )
        name(unit, "London")
        if conn.execute("select 1 from scheme where code = 'gb.lac'").fetchone() is None:
            cs.add(
                "scheme", code="gb.lac", polity_id=gb, kind="electoral_district", rank=3, noun="Assembly constituency"
            )
        shapes = raw.cached(SOURCE, LAC_SHAPES)
        ons = cs.source(
            "Office for National Statistics", "London Assembly Constituencies (December 2018) Boundaries EN BFC"
        )
        cs.add(
            "source",
            id=ons,
            kind="official_body",
            publisher="Office for National Statistics",
            title="London Assembly Constituencies (December 2018) Boundaries EN BFC",
            url="https://geoportal.statistics.gov.uk/datasets/6ab20f2a06a546eea6709ffa890362ca",
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution="Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
            "Contains OS data © Crown copyright and database right",
        )
        cs.artifact(shapes, ons)
        first_day = ELECTIONS[2000][0]
        constituency_areas: dict[str, UUID] = {}
        constituency_units: dict[str, UUID] = {}
        for feature in json.loads(shapes.path.read_bytes())["features"]:
            code = feature["properties"]["lac18cd"]
            if feature["properties"]["lac18nm"] != CONSTITUENCIES[code]:
                raise ValueError(f"{code}: ONS names it {feature['properties']['lac18nm']}")
            area = constituency_areas[code] = cs.area("gb.lac", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.lac")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=region, valid_from=None, valid_to=None)
            name(area, CONSTITUENCIES[code])
            geom, point = conn.execute(
                """select encode(st_asewkb(st_multi(st_transform(g, 4326))), 'hex'),
                          encode(st_asewkb(st_transform(st_pointonsurface(g), 4326)), 'hex')
                   from (select st_multi(st_collectionextract(st_makevalid(st_simplifypreservetopology(
                           st_setsrid(st_geomfromgeojson(%s), 27700), 5)), 3)) as g) s""",
                [json.dumps(feature["geometry"])],
            ).fetchone()
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=first_day,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ons,
            )
            cs.add("note", entity_id=area, kind="context", body_md=LAC_NOTE)
            constituency_units[code] = cs.unit("gb.lac", code)
            cs.add(
                "reporting_unit",
                id=constituency_units[code],
                polity_id=london,
                scheme_code="gb.lac",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            name(constituency_units[code], CONSTITUENCIES[code])
        if len(constituency_areas) != len(CONSTITUENCIES):
            raise ValueError(f"{len(constituency_areas)} ONS shapes for {len(CONSTITUENCIES)} constituencies")
        plans = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'gb.lac'").fetchall())
        cs.add(
            "boundary_set",
            **({"id": plans["2000"]} if "2000" in plans else {}),
            scheme_code="gb.lac",
            vintage="2000",
            valid_from=first_day,
            valid_to=None,
            as_of=first_day,
        )
        constituency_codes = {leh.fold(text): code for code, text in CONSTITUENCIES.items()}
        constituency_codes["city and east london"] = "E32000004"
        borough_names = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'gb.lad' and c.code like 'E09%'"""
            ).fetchall()
        )
        borough_names["E09000001"] = "City of London"
        borough_codes = {leh.fold(text): code for code, text in borough_names.items()}
        borough_units: dict[str, UUID] = {}
        fptp = cs.key("gb.electoral_system", "fptp")
        sv = cs.key("gb.electoral_system", "sv")
        if fptp in cs.minted or sv in cs.minted:
            raise ValueError("publish gb.parliament and gb.mayors first")
        parties: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        sources: dict[str, UUID] = {}
        for year, (day, url, publisher, title, rejected, candidates) in ELECTIONS.items():
            artifact = raw.cached(SOURCE, url)
            text = subprocess.run(
                ["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True
            ).stdout.decode(errors="replace")
            printed = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)))
            figures = {votes for _, _, first, final in candidates for votes in (first, final) if votes is not None}
            if rejected is not None:
                figures.add(rejected)
            figures.update(COUNTED.get(year, ()))
            if missing := sorted(votes for votes in figures if str(votes) not in printed):
                raise ValueError(f"{year}: {missing} not printed in {url}")
            if url not in sources:
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(artifact, sources[url])
            source = sources[url]
            names = [full for full, *_ in candidates]
            surnames = {leh.fold(full).split()[-1]: full for full in names}
            grains: dict[str, dict[str, dict[str, int | None]]] = {"lac": {}, "lad": {}}
            files: dict[str, str] = {}
            derived: set[str] = set()
            specs = SHEETS.get(year, [])
            if year == 2000:
                book = fastexcel.read_excel(raw.cached(SOURCE, FILE_2000).path)
                mayor, turnout, spoils = (
                    book.load_sheet(sheet, header_row=None).to_polars().rows()
                    for sheet in ("Mayoral by constituency", "Turnout", "Spoils")
                )
                specs = [("lac", FILE_2000, "", 1, 0, None, list(range(1, 23, 2)), 0, 0, 0, None)]
                merged: dict[str, dict[str, object]] = {}
                for row in turnout:
                    if (code := constituency_codes.get(leh.fold(str(row[0] or "")))) is not None:
                        merged.setdefault(code, {}).update(registered=row[1], ballots=row[2])
                for row in spoils[6:20]:
                    if (code := constituency_codes.get(leh.fold(str(row[0] or "")))) is not None:
                        merged.setdefault(code, {}).update(invalid=row[6], valid=row[8])
            for grain, file, sheet, header, name_col, lac_col, columns, good, spoiled, electorate, counted in specs:
                rows = (
                    mayor
                    if year == 2000
                    else fastexcel.read_excel(raw.cached(SOURCE, file).path)
                    .load_sheet(sheet, header_row=None)
                    .to_polars()
                    .rows()
                )
                order = []
                for column in columns:
                    person = leh.fold(re.split(r" - |\n| \(", str(rows[header][column]))[0]).split()
                    found = [full for surname, full in surnames.items() if surname in person]
                    if len(found) != 1:
                        raise ValueError(f"{year} {sheet}: {rows[header][column]!r} matches {found}")
                    order.append(found[0])
                if sorted(order) != sorted(names):
                    raise ValueError(f"{year} {sheet}: columns {order} against {names}")
                whole = any(spec[0] == "lac" for spec in specs)
                for row in rows[header + 1 :]:
                    place = leh.fold(str(row[name_col] or ""))
                    if grain == "lac":
                        targets = [("lac", constituency_codes.get(place))]
                    else:
                        targets = [("lad", borough_codes.get(place))]
                        if not whole:
                            targets.append(("lac", constituency_codes.get(leh.fold(str(row[lac_col] or "")))))
                    if any(code is None for _, code in targets):
                        continue
                    if year == 2000:
                        found = merged[targets[0][1]]
                        figures = {"valid": found["valid"], "invalid": found["invalid"]}
                        figures |= {"registered": found["registered"], "ballots": found["ballots"]}
                    else:
                        figures = {"valid": row[good], "invalid": row[spoiled], "registered": row[electorate]}
                        figures["ballots"] = None if counted is None else row[counted]
                    figures |= {full: row[column] for full, column in zip(order, columns, strict=True)}
                    if grain == "lad":
                        figures |= {full: CORRECTED.get((year, targets[0][1], full), figures[full]) for full in order}
                    for target, code in targets:
                        entry = grains[target].setdefault(code, {})
                        for key, value in figures.items():
                            entry[key] = None if value is None else (entry.get(key) or 0) + round(float(value))
                        files[target] = file
                    if grain == "ward":
                        derived.add("lad")
                    if not whole:
                        derived.add("lac")
            for entry in grains["lad"].values() if "lad" in derived else []:
                entry["registered"] = None
            for grain, entries in grains.items():
                if not entries:
                    continue
                if len(entries) != (len(CONSTITUENCIES) if grain == "lac" else 33):
                    raise ValueError(f"{year} {grain}: {len(entries)} areas")
                for full, _, first, _ in candidates:
                    if (summed := sum(entry[full] or 0 for entry in entries.values())) != first:
                        raise ValueError(f"{year} {grain}: {full} sums to {summed}, declared {first}")
            if "lac" in derived:
                declarations = (
                    {code: DECLARATIONS_2021[code] for code in CONSTITUENCIES}
                    if year == 2021
                    else {code: url for code in CONSTITUENCIES}
                )
                for code, entry in grains["lac"].items():
                    text = subprocess.run(
                        ["pdftotext", "-layout", str(raw.cached(SOURCE, declarations[code]).path), "-"],
                        check=True,
                        capture_output=True,
                    ).stdout.decode(errors="replace")
                    shown = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)))
                    if year == 2021:
                        entry["registered"] = int(re.findall(r"Electorate +(\d+)", text)[-1])
                    if missing := sorted(
                        f"{key} {value}"
                        for key, value in entry.items()
                        if value is not None and str(value) not in shown
                    ):
                        raise ValueError(f"{year} {code}: {missing} not printed in {declarations[code]}")
                derived.discard("lac")
                if year == 2021:
                    files["lac"] = "declarations"
            national = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s", [f"{day}:local:gb"]
            ).fetchone()
            election = cs.key("gb.election", f"{day}:gla")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                parent_id=None if national is None else national[0],
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} London elections")
            cs.add("slug", path=f"gb/london/{day}-general", entity_id=election)
            supplementary = year <= SV_UNTIL
            contest_key = f"{day}:mayor:london"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=region,
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"Mayor of London, {year}")
            cs.add("slug", path=f"gb/london/{day}-general/mayor", entity_id=contest)
            result_set = cs.key("gb.london.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source)
            first_round = 1 if supplementary else 0
            registered, ballots, rejected = COUNTED.get(year, (None, None, rejected))
            valid = sum(first for _, _, first, _ in candidates)
            totals = {REGISTERED: registered, BALLOTS: ballots, VALID: valid, INVALID: rejected}
            cells = [
                (measure, first_round if measure == VALID else 0, value, 0)
                for measure, value in totals.items()
                if value is not None
            ]
            if year not in COUNTED:
                for measure, key in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (INVALID, "invalid")):
                    summed = [entry[key] for entry in grains["lac"].values()]
                    if totals[measure] is None and summed and None not in summed:
                        cells.append((measure, 0, sum(summed), 1))
                    elif (
                        totals[measure] is not None and summed and None not in summed and sum(summed) != totals[measure]
                    ):
                        raise ValueError(
                            f"{year}: constituencies sum {key} to {sum(summed)}, declared {totals[measure]}"
                        )
            if supplementary:
                exhausted = valid - sum(final for _, _, _, final in candidates if final is not None)
                cells.append((EXHAUSTED, 2, exhausted, 0))
            for measure, number, value, flags in cells:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=flags,
                    value=value,
                )
            options: dict[str, UUID] = {}
            winner = max(candidates, key=lambda row: (row[3] or 0) if supplementary else row[2])[0]
            for full, party_code, first, final in candidates:
                label = PARTIES.get(party_code, party_code) if year <= 2008 else party_code
                owner = None
                if label is not None:
                    label = RESOLVED.get(label, label)
                    if label not in parties:
                        found = cs.key(*leh.RESOLVED[label]) if label in leh.RESOLVED else known.get(leh.fold(label))
                        if found is None:
                            found = cs.key("gb.party", slug(label))
                            if found in cs.minted:
                                cs.add("party", id=found, kind="party", polity_id=gb)
                                name(found, label)
                        parties[label] = found
                    owner = parties[label]
                if full not in persons:
                    persons[full] = (
                        cs.key("wikidata", PEOPLE[full]) if full in PEOPLE else cs.key("gb.london.person", full)
                    )
                    if persons[full] in cs.minted:
                        if full in PEOPLE:
                            raise ValueError(f"{full}: no person with {PEOPLE[full]}")
                        cs.add("person", id=persons[full])
                        name(persons[full], full)
                option_key = f"{contest_key}:{slug(full)}"
                option = options[full] = cs.key("gb.london.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=owner,
                    ballot_label=full,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.london.candidacy", option_key),
                    person_id=persons[full],
                    option_id=option,
                    role="head",
                    ballot_name=full,
                    member_of_party_id=owner,
                    is_independent=owner is None,
                )
                rounds = {first_round: first}
                if supplementary:
                    rounds[2] = final or 0
                for number, votes in rounds.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
                if full == winner:
                    kinds = ("won", "elected")
                elif supplementary and final is None:
                    kinds = ("eliminated",)
                else:
                    kinds = ("lost",)
                for kind in kinds:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
                if full == winner:
                    terms.append(("mayor", year, day, persons[full], contest, full))
            for grain, entries in grains.items():
                if not entries:
                    continue
                file = files[grain]
                if file not in sources:
                    if file == "declarations":
                        publisher, title = "London Elects", "GLA 2021 Elections: constituency results and local totals"
                        page = "https://www.londonelects.org.uk/im-voter/election-results/results-2021"
                    else:
                        (publisher, title), page = SHEET_TITLES[file], file
                    sources[file] = cs.source(publisher, title)
                    cs.add("source", id=sources[file], kind="official_body", publisher=publisher, title=title, url=page)
                    for link in DECLARATIONS_2021.values() if file == "declarations" else [file]:
                        cs.artifact(raw.cached(SOURCE, link), sources[file])
                own = source if grain == "lac" and year in (2004, 2008) else sources[file]
                grain_set = cs.key("gb.london.result_set", f"{contest_key}:{grain}")
                cs.add(
                    "result_set",
                    id=grain_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=own,
                    is_default=False,
                    completeness=1,
                )
                cs.add("citation", entity_id=grain_set, source_id=own)
                if own != sources[file]:
                    cs.add("citation", entity_id=grain_set, source_id=sources[file])
                if grain == "lad" and any(key[0] == year for key in CORRECTED):
                    cs.add("note", entity_id=grain_set, kind="context", body_md=CORRECTED_NOTE)
                flags = 1 if grain in derived else 0
                for code, entry in entries.items():
                    if grain == "lac":
                        place = constituency_units[code]
                    else:
                        if code not in borough_units:
                            borough_units[code] = cs.unit("gb.lad", code)
                            if borough_units[code] in cs.minted:
                                area = cs.area("gb.lad", code)
                                if area in cs.minted:
                                    cs.add("area", id=area, scheme_code="gb.lad")
                                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                                    cs.add(
                                        "area_parent",
                                        area_id=area,
                                        parent_id=cs.area("gb.country", "ENG"),
                                        valid_from=None,
                                        valid_to=None,
                                    )
                                    name(area, "City of London")
                                cs.add(
                                    "reporting_unit",
                                    id=borough_units[code],
                                    polity_id=london,
                                    scheme_code="gb.lad",
                                    code=code,
                                    area_relation="equals",
                                    area_id=area,
                                    kind="standard",
                                    valid_from=None,
                                )
                                name(borough_units[code], borough_names[code])
                        place = borough_units[code]
                    values = [
                        (None, measure, first_round if measure == VALID else 0, entry[key])
                        for measure, key in (
                            (REGISTERED, "registered"),
                            (BALLOTS, "ballots"),
                            (VALID, "valid"),
                            (INVALID, "invalid"),
                        )
                    ]
                    values += [(options[full], VOTES, first_round, entry[full]) for full in names]
                    for option_id, measure, number, value in values:
                        if value is None:
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=grain_set,
                            unit_id=place,
                            option_id=option_id,
                            channel=0,
                            measure=measure,
                            count_round=number,
                            flags=flags,
                            value=value,
                        )
        body = cs.key("gb.body", "london-assembly")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=london, kind="unicameral")
            name(body, "London Assembly")
        cs.add("body_size", body_id=body, seats=ASSEMBLY_SEATS, valid_from=first_day)
        ams = cs.key("gb.electoral_system", "london-ams-list")
        if ams in cs.minted:
            cs.add(
                "electoral_system",
                id=ams,
                polity_id=london,
                rules={
                    "formula": "dhondt",
                    "threshold": {"percent": 5, "level": "party"},
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                },
                rules_version=1,
            )
            name(ams, "Additional member system, London-wide list (London Assembly)")
            cs.add("note", entity_id=ams, kind="methodology", body_md=AMS_NOTE)

        def party_of(label: str) -> UUID:
            label = RESOLVED.get(label, label)
            if label not in parties:
                found = cs.key(*leh.RESOLVED[label]) if label in leh.RESOLVED else known.get(leh.fold(label))
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted:
                        print(f"new party: {label}")
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                parties[label] = found
            return parties[label]

        def cite(url: str) -> UUID:
            if url == "declarations-2016" and url not in sources:
                publisher, title = "London Elects", "GLA 2016 Elections: constituency results and local totals"
                sources[url] = cs.source(publisher, title)
                page = "https://www.londonelects.org.uk/im-voter/election-results/results-2016"
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=page)
                for link in DECLARATIONS_2016.values():
                    cs.artifact(raw.cached(SOURCE, link), sources[url])
            if url not in sources:
                publisher, title = ASSEMBLY_TITLES.get(url) or SHEET_TITLES[url]
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(raw.cached(SOURCE, url), sources[url])
            return sources[url]

        def pdf(url: str) -> str:
            return subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True
            ).stdout.decode(errors="replace")

        def numbers(text: str) -> set[str]:
            return set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)))

        def capitals(words: list[str]) -> str:
            return re.sub(r"\bMc([a-z])", lambda m: f"Mc{m[1].upper()}", " ".join(words).title())

        members_by: dict[str, dict[str, str]] = {}
        labels_by: dict[str, str] = {}
        for binding in json.loads(raw.cached(SOURCE, MEMBERS_WIKIDATA).path.read_bytes())["results"]["bindings"]:
            qid = binding["p"]["value"].rsplit("/", 1)[1]
            labels_by[qid] = binding["pLabel"]["value"].split(",")[0]
            words = leh.fold(labels_by[qid]).split()
            members_by.setdefault(words[-1], {})[words[0]] = qid
        people: dict[tuple[str, str], UUID] = {}
        unmatched: list[str] = []

        def person_of(full: str, elected: bool) -> UUID:
            full = ALIASES.get(full, full)
            words = leh.fold(full).split()
            found = members_by.get(words[-1], {})
            qid = ASSEMBLY_PEOPLE.get(full) or found.get(words[0])
            if qid is None and elected:
                close = [q for first, q in found.items() if any(word[0] == first[0] for word in words[:-1])]
                qid = close[0] if len(close) == 1 else None
            if qid is None and elected:
                unmatched.append(full)
            key = ("wikidata", qid) if qid is not None else ("gb.london.person", full)
            if key not in people:
                people[key] = cs.key(*key)
                if people[key] in cs.minted:
                    cs.add("person", id=people[key])
                    label = PERSON_NAMES.get(full, full) if qid is None else labels_by.get(qid, full)
                    name(people[key], re.sub(r"^(?:Sir|Dame|Lord|Lady|Baroness|Baron) ", "", label))
            return people[key]

        for year, (day, *_) in ELECTIONS.items():
            candidates_by: dict[str, dict[str, tuple[str | None, int]]] = {code: {} for code in CONSTITUENCIES}
            seat_figures: dict[str, dict[str, int]] = {code: {} for code in CONSTITUENCIES}
            by_lac: dict[str, dict[str, int]] = {code: {} for code in CONSTITUENCIES}
            lac_figures: dict[str, dict[str, int]] = {code: {} for code in CONSTITUENCIES}
            individual: set[str] = set()
            london_figures: dict[str, int] = {}
            members: list[tuple[str, str]] = []
            positions: dict[str, list[tuple[int, str, bool]]] = {}
            printed: set[str] | None = None
            if year == 2000:
                book = fastexcel.read_excel(raw.cached(SOURCE, FILE_2000).path)
                rows = book.load_sheet("Constituency Assembly", header_row=None).to_polars().rows()
                legend = {str(row[0]).strip(): " ".join(str(row[1]).split()) for row in rows[23:] if row[0] and row[1]}
                for row in rows[3:17]:
                    code = constituency_codes[leh.fold(str(row[0]))]
                    for column in range(3, 35, 2):
                        if row[column] is None:
                            continue
                        text = " ".join(str(row[column]).replace(",", ", ").split())
                        surname, _, forenames = text.partition(", ")
                        label = legend[rows[1][column]]
                        candidates_by[code][f"{forenames} {surname}" if forenames else text] = (
                            None if label.startswith("Ind") else label,
                            int(row[column + 1]),
                        )
                spoils = book.load_sheet("Spoils", header_row=None).to_polars().rows()
                for row in spoils[28:42]:
                    seat_figures[constituency_codes[leh.fold(str(row[0]))]].update(
                        ballots=int(row[1]), invalid=int(row[6]), valid=int(row[8])
                    )
                for row in book.load_sheet("Turnout", header_row=None).to_polars().rows():
                    if (code := constituency_codes.get(leh.fold(str(row[0] or "")))) is not None:
                        seat_figures[code]["registered"] = int(row[1])
                rows = book.load_sheet("Assembly List by constituency", header_row=None).to_polars().rows()
                for row in rows[2:16]:
                    code = constituency_codes[leh.fold(str(row[0]))]
                    by_lac[code] = {LIST_2000[rows[0][c]]: int(row[c]) for c in range(1, 15) if row[c] is not None}
                for row in spoils[50:64]:
                    lac_figures[constituency_codes[leh.fold(str(row[0]))]] = {
                        "ballots": int(row[1]),
                        "invalid": int(row[6]),
                        "valid": int(row[8]),
                    }
                london_figures = {"ballots": int(spoils[64][1]), "invalid": int(spoils[64][6])}
                london_figures |= {"valid": int(spoils[64][8]), "registered": COUNTED[2000][0]}
                individual.add("Peter Tatchell")
                printed = numbers(pdf(FACTSHEET_2000))
                list_sources = [FILE_2000, FACTSHEET_2000]
                seat_sources = [FILE_2000]
            elif year in (2004, 2008):
                file = SHEETS[year][0][1]
                rows = fastexcel.read_excel(raw.cached(SOURCE, file).path).load_sheet(str(year), header_row=None)
                rows = rows.to_polars().rows()
                electorate, votes, good, spoiled, names, list_columns, list_good, list_spoiled = (
                    (34, range(70, 80), 80, 85, 86, range(54, 63), 63, 68)
                    if year == 2004
                    else (36, range(60, 72), 72, 78, 79, range(38, 52), 52, 58)
                )
                list_labels = {c: " ".join(str(rows[2][c]).split()) for c in list_columns}
                for c, label in list_labels.items():
                    if ", " in label:
                        surname, forenames = label.split(", ")
                        list_labels[c] = f"{forenames} {surname}"
                        individual.add(list_labels[c])
                for row in rows[3:]:
                    if (code := constituency_codes.get(leh.fold(str(row[2] or "")))) is None:
                        continue
                    if year == 2008 and row[1] == "Havering":
                        continue
                    entry = seat_figures[code]
                    for column in votes:
                        text = " ".join(str(row[column - votes[0] + names] or "").split())
                        if not text:
                            if row[column] not in (None, 0, "0", "NA"):
                                raise ValueError(f"{year} {code}: votes {row[column]} with no candidate")
                            continue
                        if year == 2004:
                            suffix = max((s for s in SUFFIXES_2004 if text.endswith(s)), key=len)
                            label = SUFFIXES_2004[suffix]
                            surname, _, forenames = text[: -len(suffix)].strip().partition(",")
                            full = " ".join(f"{forenames} {surname}".split())
                        else:
                            full, abbreviation = text.rsplit(" - ", 1)
                            label = ABBREVIATIONS_2008[abbreviation]
                        previous = candidates_by[code].get(full, (label, 0))[1]
                        candidates_by[code][full] = (label, previous + round(float(row[column] or 0)))
                    for key, column in (("valid", good), ("invalid", spoiled), ("registered", electorate)):
                        entry[key] = entry.get(key, 0) + round(float(row[column]))
                    for c, label in list_labels.items():
                        by_lac[code][label] = by_lac[code].get(label, 0) + round(float(row[c] or 0))
                    for key, column in (("valid", list_good), ("invalid", list_spoiled)):
                        lac_figures[code][key] = (lac_figures[code].get(key) or 0) + round(float(row[column]))
                london_figures = {"registered": COUNTED[year][0]}
                report = ELECTIONS[year][1]
                text = pdf(report)
                printed = numbers(text)
                if year == 2008:
                    surnames = {leh.fold(full).split()[-1]: full for full in candidates_by["E32000009"]}
                    candidates_by["E32000009"] = {
                        surnames[surname]: (candidates_by["E32000009"][surnames[surname]][0], value)
                        for surname, value in HAVERING_2008.items()
                    }
                    by_lac["E32000009"] = dict(zip(list_labels.values(), HAVERING_2008_LIST, strict=True))
                    valid, invalid, list_valid, list_invalid = HAVERING_2008_FIGURES
                    seat_figures["E32000009"].update(valid=valid, invalid=invalid)
                    lac_figures["E32000009"] = {"valid": list_valid, "invalid": list_invalid}
                    shown = [*HAVERING_2008.values(), *HAVERING_2008_LIST, *HAVERING_2008_FIGURES]
                    if missing := sorted(value for value in shown if str(value) not in printed):
                        raise ValueError(f"2008 Havering and Redbridge: {missing} not printed in {report}")
                for line in text.splitlines():
                    parts = re.split(r"\s{2,}", line.strip())
                    if (
                        line[:1].isalpha()
                        and len(parts) >= 8
                        and (code := constituency_codes.get(leh.fold(parts[0]))) is not None
                        and all(re.fullmatch(r"\d{1,3}(,\d{3})+", parts[i]) for i in (1, 2, 4, 6))
                        and all(re.fullmatch(r"\d+\.\d", parts[i]) for i in (3, 5, 7))
                        and "ballots" not in seat_figures[code]
                    ):
                        seat_figures[code].update(
                            registered=int(parts[1].replace(",", "")), ballots=int(parts[4].replace(",", ""))
                        )
                        lac_figures[code]["ballots"] = int(parts[6].replace(",", ""))
                for code, entry in seat_figures.items():
                    for figures in (entry, lac_figures[code]):
                        if figures["valid"] + figures["invalid"] != figures["ballots"]:
                            raise ValueError(f"{year} {code}: {figures} against the report's ballot papers")
                for code, standing in candidates_by.items():
                    for full, (_, value) in standing.items():
                        if str(value) not in printed:
                            raise ValueError(f"{year} {code}: {full} {value} not printed in {report}")
                list_sources = [file, report]
                seat_sources = [file, report]
            elif year == 2012:
                file = SHEETS[2012][0][1]
                book = fastexcel.read_excel(raw.cached(SOURCE, file).path)
                rows = book.load_sheet("Constituency member", header_row=None).to_polars().rows()
                for row in rows[2:16]:
                    code = constituency_codes[leh.fold(str(row[0]))]
                    for column in range(3, 17):
                        if row[column] is not None:
                            label = rows[1][column]
                            full = str(row[column + 34]).strip().rstrip("*").strip()
                            candidates_by[code][full] = (
                                None if label == "Independent/Other" else label,
                                int(row[column]),
                            )
                    seat_figures[code].update(valid=int(row[17]), invalid=int(row[18]))
                for row in book.load_sheet("Mayoral", header_row=None).to_polars().rows()[2:]:
                    if (code := constituency_codes.get(leh.fold(str(row[0] or "")))) is not None:
                        seat_figures[code]["registered"] = int(row[2])
                rows = book.load_sheet("Londonwide-list", header_row=None).to_polars().rows()
                list_labels = {c: str(rows[1][c]) for c in range(2, 15)}
                for c, label in list_labels.items():
                    if label.endswith(" (Independent)"):
                        list_labels[c] = label.removesuffix(" (Independent)")
                        individual.add(list_labels[c])
                for row in rows[2:16]:
                    code = constituency_codes[leh.fold(str(row[0]))]
                    by_lac[code] = {label: int(row[c]) for c, label in list_labels.items() if row[c] is not None}
                    lac_figures[code] = {"valid": int(row[15]), "invalid": int(row[16])}
                for row in book.load_sheet("Londonwide seat allocation", header_row=None).to_polars().rows()[3:14]:
                    members.append((WINNERS_2012[row[10]], str(row[11]).strip()))
                printed = numbers(pdf(ELECTIONS[2012][1]))
                list_sources = [file, ELECTIONS[2012][1]]
                seat_sources = [file]
            elif year in (2016, 2021):
                declarations, file, key = (
                    (DECLARATIONS_2016, LIST_2016, "declarations-2016")
                    if year == 2016
                    else (DECLARATIONS_2021, LIST_2021, "declarations")
                )
                for code, url in declarations.items():
                    text = pdf(url)
                    first = text.rindex("CONSTITUENCY MEMBER OF THE LONDON ASSEMBLY RESULTS")
                    section = text[first : text.index("Turnout", first)]
                    first = text.index("LONDON MEMBER (AT CONSTITUENCY LEVEL)")
                    rest = text[first : text.index("Turnout", first)]
                    current: list[str] | None = None
                    listed: list[tuple[list[str], int]] = []
                    for line in section[
                        section.index("(if any)") : section.index("Total number of good votes")
                    ].splitlines()[1:]:
                        if (match := re.match(r"^\s{1,4}(\S.*?)(?:\s{2,}(\S.*?))?\s{2,}(\d+)$", line)) is not None:
                            current = [match[1], match[2] or ""]
                            listed.append((current, int(match[3])))
                        elif line.strip() and current is not None:
                            if line.strip().isupper():
                                joint = "" if current[0].endswith("-") else " "
                                current[0] = f"{current[0]}{joint}{line.strip()}"
                            else:
                                current[1] = f"{current[1]} {line.strip()}"
                    for (written, label), value in listed:
                        words = written.split()
                        cut = next(i for i, word in enumerate(words) if word.upper() == word and len(word) > 1)
                        full = f"{' '.join(words[:cut])} {capitals(words[cut:])}"
                        label = label.strip()
                        candidates_by[code][full] = (None if label in ("", "Independent") else label, value)
                    seat_figures[code].update(
                        ballots=int(re.findall(r"ballot papers counted\s+(\d+)", section)[0]),
                        valid=int(re.findall(r"Total number of good votes\s+(\d+)", section)[0]),
                        invalid=int(re.findall(r"Total number of Rejected ballots\s+(\d+)", section)[0]),
                        registered=int(re.findall(r"System Logged Electorate\s+(\d+)", section)[0]),
                    )
                    lac_figures[code] = {
                        "ballots": int(re.findall(r"ballot papers counted\s+(\d+)", rest)[0]),
                        "valid": int(re.findall(r"Total number of good votes\s+(\d+)", rest)[0]),
                        "invalid": int(re.findall(r"\n\s+Total\s+(\d+)\n", rest)[0]),
                    }
                    by_lac[code] = {
                        match[1].strip(): int(match[2])
                        for match in re.finditer(
                            r"^\s{1,4}(\S.*?)\s{2,}(\d+)$", rest[rest.index("Independent") :], re.MULTILINE
                        )
                        if not match[1].startswith(("Total", "(", "System", "Turnout"))
                    }
                text = pdf(file)
                head = text[: text.index("LONDON MEMBER AGGREGATED")]
                totals = {
                    match[1].strip(): int(match[2])
                    for match in re.finditer(
                        r"^(\S.*?)\s{2,}(\d+)$",
                        head[head.index("Total Votes") : head.index("Total number of good")],
                        re.MULTILINE,
                    )
                }
                for code, printed_lines in by_lac.items():
                    by_lac[code] = {}
                    for label, value in printed_lines.items():
                        owner = max((t for t in totals if label.startswith(t)), key=len)
                        by_lac[code][owner] = by_lac[code].get(owner, 0) + value
                if year == 2021:
                    for match in re.finditer(
                        r"^\s+\d+ (\S.*?)\s{2,}(\S.*)$", head[head.index("filled") :], re.MULTILINE
                    ):
                        words = match[2].split()
                        cut = next(i for i, word in enumerate(words) if word.upper() != word)
                        members.append((match[1].strip(), f"{' '.join(words[cut:])} {capitals(words[:cut])}"))
                else:
                    factsheet = pdf(FACTSHEET_2016)
                    for names in LIST_MEMBERS[2016].values():
                        for full in names:
                            if full not in factsheet:
                                raise ValueError(f"2016: {full} not printed in {FACTSHEET_2016}")
                london_figures = {
                    "ballots": int(re.findall(r"ballot papers counted\s+(\d+)", text)[0]),
                    "invalid": int(re.findall(r"\n\s+Total\s+(\d+)\n", head)[0]),
                }
                if electorate := re.findall(r"System Logged Electorate\s+(\d+)", text):
                    london_figures["registered"] = int(electorate[0])
                printed = numbers(head)
                list_sources = [file, key, *([FACTSHEET_2016] if year == 2016 else [])]
                seat_sources = [key]
            else:
                file = SHEETS[2024][0][1]
                book = fastexcel.read_excel(raw.cached(SOURCE, file).path)
                for row in book.load_sheet("2. Constituency Assembly Pt 1", header_row=None).to_polars().rows()[2:]:
                    if row[0] is None or (code := constituency_codes.get(leh.fold(str(row[2] or "")))) is None:
                        continue
                    surname, _, forenames = str(row[0]).partition(", ")
                    label = " ".join(str(row[1]).split())
                    candidates_by[code][f"{forenames} {surname}".strip()] = (
                        None if label.endswith("Independent") else label,
                        int(row[3]),
                    )
                for row in book.load_sheet("3. Constituency Assembly Pt 2", header_row=None).to_polars().rows()[3:17]:
                    seat_figures[constituency_codes[leh.fold(str(row[0]))]].update(
                        valid=int(row[1]), invalid=int(row[7]), ballots=int(row[8]), registered=int(row[9])
                    )
                rows = book.load_sheet("4. London-wide Assembly", header_row=None).to_polars().rows()
                list_labels = {}
                for c in range(1, 16):
                    label = " ".join(str(rows[2][c]).split())
                    if (match := re.fullmatch(r"([A-Z'\- ]+), (.+?)(?: Independent)?", label)) is not None:
                        label = f"{match[2]} {capitals(match[1].split())}"
                        individual.add(label)
                    list_labels[c] = label
                for row in rows[3:17]:
                    code = constituency_codes[leh.fold(str(row[0]))]
                    by_lac[code] = {label: int(row[c]) for c, label in list_labels.items()}
                    lac_figures[code] = {"valid": int(row[16]), "invalid": int(row[22]), "ballots": int(row[23])}
                london_figures = {"invalid": int(rows[18][22]), "ballots": int(rows[18][23])}
                london_figures |= {"registered": int(rows[18][24]), "valid": int(rows[18][16])}
                by_party = {party_of(label): label for label in list_labels.values() if label not in individual}
                rows = book.load_sheet("7. London-wide Elected", header_row=None).to_polars().rows()
                for c in range(1, len(rows[0])):
                    header = " ".join(str(rows[0][c] or "").split())
                    if not header or re.match(r"[A-Z'\- ]+, ", header) is not None:
                        continue
                    owner = by_party[party_of(header)]
                    for row in rows[1:]:
                        cell = str(row[c] or "").strip()
                        if not cell:
                            continue
                        full = re.sub(r"\s*\(Elected .*\)$", "", cell)
                        elected = "Elected In Round" in cell
                        positions.setdefault(owner, []).append((int(row[0]), full, elected))
                        if elected:
                            members.append((owner, full))
                printed = None
                list_sources = [file]
                seat_sources = [file]
            for label, names in LIST_MEMBERS.get(year, {}).items():
                members += [(label, full) for full in names]
            if year in (2004, 2008):
                text = pdf(ELECTIONS[year][1])
                for _, full in members:
                    if full not in text:
                        raise ValueError(f"{year}: {full} not printed in {ELECTIONS[year][1]}")
            if year == 2000:
                text = pdf(FACTSHEET_2000)
                for _, full in members:
                    if full not in text:
                        raise ValueError(f"2000: {full} not printed in {FACTSHEET_2000}")
            if len(members) != LIST_SEATS:
                raise ValueError(f"{year}: {len(members)} London-wide members")
            for code, standing in candidates_by.items():
                if len(standing) < 2:
                    raise ValueError(f"{year} {code}: {len(standing)} standing")
                if (summed := sum(value for _, value in standing.values())) != seat_figures[code]["valid"]:
                    raise ValueError(f"{year} {code}: standing sum to {summed}, good {seat_figures[code]['valid']}")
                if sum(by_lac[code].values()) != lac_figures[code]["valid"]:
                    raise ValueError(f"{year} {code}: list votes sum to {sum(by_lac[code].values())}")
            list_votes: dict[str, int] = {}
            for counted in by_lac.values():
                for label, value in counted.items():
                    list_votes[label] = list_votes.get(label, 0) + value
            for key in ("valid", "invalid", "ballots"):
                parts = [figures[key] for figures in lac_figures.values() if key in figures]
                if len(parts) != len(lac_figures):
                    continue
                if key not in london_figures:
                    london_figures[key] = sum(parts)
                elif london_figures[key] != sum(parts):
                    raise ValueError(
                        f"{year}: constituency list {key} sums to {sum(parts)}, declared {london_figures[key]}"
                    )
            if london_figures["valid"] != sum(list_votes.values()):
                raise ValueError(
                    f"{year}: list votes sum to {sum(list_votes.values())}, good {london_figures['valid']}"
                )
            if year == 2021 and list_votes != totals:
                raise ValueError(f"2021: constituency list votes {list_votes} against {totals}")
            if printed is not None and (
                missing := sorted(f"{l} {v}" for l, v in list_votes.items() if str(v) not in printed)
            ):
                raise ValueError(f"{year}: London-wide votes {missing} not printed")
            winners: dict[str, str] = {}
            held: dict[UUID, int] = {}
            for code, standing in candidates_by.items():
                winners[code] = max(standing, key=lambda full: standing[full][1])
                label = standing[winners[code]][0]
                if label is not None:
                    held[party_of(label)] = held.get(party_of(label), 0) + 1
            eligible = {
                label: value
                for label, value in list_votes.items()
                if label not in individual and value * 20 >= london_figures["valid"]
            }
            won: dict[UUID, int] = {}
            for _ in range(LIST_SEATS):
                label = max(
                    eligible,
                    key=lambda label: (
                        eligible[label] / (held.get(party_of(label), 0) + won.get(party_of(label), 0) + 1)
                    ),
                )
                won[party_of(label)] = won.get(party_of(label), 0) + 1
            declared: dict[UUID, int] = {}
            for label, _ in members:
                declared[party_of(label)] = declared.get(party_of(label), 0) + 1
            if won != declared:
                raise ValueError(f"{year}: D'Hondt gives {won}, declared {declared}")
            election = cs.key("gb.election", f"{day}:gla")
            path = f"gb/london/{day}-general"
            for code, standing in candidates_by.items():
                entry = seat_figures[code]
                constituency = CONSTITUENCIES[code]
                contest_key = f"{day}:assembly:{slug(constituency)}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=constituency_areas[code],
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} London Assembly election in {constituency}")
                cs.add("slug", path=f"{path}/{slug(constituency)}", entity_id=contest)
                result_set = cs.key("gb.london.result_set", contest_key)
                own = [cite(url) for url in seat_sources]
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=own[0],
                    is_default=True,
                    completeness=1,
                )
                for source_id in own:
                    cs.add("citation", entity_id=result_set, source_id=source_id)
                cells: list[tuple[UUID | None, int, int | None]] = [
                    (None, measure, entry.get(key))
                    for measure, key in (
                        (REGISTERED, "registered"),
                        (BALLOTS, "ballots"),
                        (VALID, "valid"),
                        (INVALID, "invalid"),
                    )
                ]
                for full, (label, value) in standing.items():
                    owner = None if label is None else party_of(label)
                    person = person_of(full, full == winners[code])
                    if full == winners[code]:
                        terms.append(("assembly", year, day, person, contest, full))
                    option_key = f"{contest_key}:{slug(full)}"
                    option = cs.key("gb.london.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.london.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=full,
                        member_of_party_id=owner,
                        is_independent=owner is None,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if full == winners[code] else "lost",
                    )
                    cells.append((option, VOTES, value))
                for option_id, measure, value in cells:
                    if value is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=constituency_units[code],
                        option_id=option_id,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0 if printed is None or year not in (2004, 2008) or str(value) in printed else 1,
                        value=value,
                    )
            contest_key = f"{day}:assembly:london-wide"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=region,
                magnitude=LIST_SEATS,
                round_number=1,
                electoral_system_id=ams,
                default_result_kind="counted",
            )
            name(contest, f"{year} London Assembly election, London-wide list")
            cs.add("slug", path=f"{path}/london-wide-assembly", entity_id=contest)
            options = {}
            for label in list_votes:
                option = options[label] = cs.key("gb.london.option", f"{contest_key}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if label in individual else "list",
                    party_id=None if label in individual else party_of(label),
                    ballot_label=label,
                )
                if label in individual:
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.london.candidacy", f"{contest_key}:{slug(label)}"),
                        person_id=person_of(label, False),
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        member_of_party_id=None,
                        is_independent=True,
                    )
            by_party = {party_of(label): label for label in list_votes if label not in individual}
            seated: dict[str, list[str]] = {}
            for label, full in members:
                seated.setdefault(by_party[party_of(label)], []).append(full)
            for label in positions:
                if label not in options:
                    raise ValueError(f"{year}: list {label} has no votes")
            for suffix, votes_by, figures_by in (
                ("", {"": list_votes}, {"": london_figures}),
                (":lac", by_lac, lac_figures),
            ):
                result_set = cs.key("gb.london.result_set", f"{contest_key}{suffix}")
                own = [cite(url) for url in list_sources]
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=own[0],
                    is_default=suffix == "",
                    completeness=1,
                )
                for source_id in own:
                    cs.add("citation", entity_id=result_set, source_id=source_id)
                if suffix == "":
                    for label, names in seated.items():
                        cs.add(
                            "outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=len(names)
                        )
                    rosters = {
                        label: positions.get(label) or [(None, full, True) for full in seated.get(label, [])]
                        for label in options
                        if label not in individual
                    }
                    for label, entries in rosters.items():
                        for position, full, elected in entries:
                            candidacy = cs.key("gb.london.candidacy", f"{contest_key}:{slug(label)}:{slug(full)}")
                            person = person_of(full, elected)
                            if elected:
                                terms.append(("assembly", year, day, person, contest, full))
                            cs.add(
                                "candidacy",
                                id=candidacy,
                                person_id=person,
                                option_id=options[label],
                                role="list_member",
                                list_position=position,
                                ballot_name=full,
                                member_of_party_id=party_of(label),
                                is_independent=False,
                            )
                            if elected:
                                cs.add(
                                    "outcome",
                                    result_set_id=result_set,
                                    option_id=options[label],
                                    candidacy_id=candidacy,
                                    kind="elected",
                                )
                for code, found in votes_by.items():
                    place = unit if code == "" else constituency_units[code]
                    figures = figures_by[code]
                    cells = [
                        (None, measure, figures.get(key))
                        for measure, key in (
                            (REGISTERED, "registered"),
                            (BALLOTS, "ballots"),
                            (VALID, "valid"),
                            (INVALID, "invalid"),
                        )
                    ]
                    cells += [(options[label], VOTES, value) for label, value in found.items()]
                    for option_id, measure, value in cells:
                        if value is None:
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=place,
                            option_id=option_id,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0 if printed is None or year not in (2004, 2008) or str(value) in printed else 1,
                            value=value,
                        )
        wiki: dict[str, UUID] = {}
        for page in sorted({page for *_, pages in REPLACEMENTS.values() for page in pages}):
            title = page.replace("_", " ")
            wiki[page] = cs.source("Wikipedia", title)
            cs.add(
                "source",
                id=wiki[page],
                kind="community",
                publisher="Wikipedia",
                title=title,
                url=WIKIPEDIA + page,
                license="CC BY-SA 4.0",
                license_url="https://creativecommons.org/licenses/by-sa/4.0/",
                notes="Infobox dates of the London Assembly membership.",
            )
        former = cs.source(GLA, "Former Members of the London Assembly")
        cs.add(
            "source",
            id=former,
            kind="official_body",
            publisher=GLA,
            title="Former Members of the London Assembly",
            url=FORMER_MEMBERS,
        )
        results_2024 = cs.source(GLA, "Results of 2024 GLA Elections")
        cs.add(
            "source",
            id=results_2024,
            kind="official_body",
            publisher=GLA,
            title="Results of 2024 GLA Elections",
            url=RESULTS_2024,
            notes="Report to the London Assembly; footnote 1 records Zoë Garbett taking the seat Siân Berry was elected to.",
        )
        polls = sorted(day for day, *_ in ELECTIONS.values())
        used: set[tuple[int, str]] = set()
        for kind, year, day, person, contest, full in terms:
            later = [poll for poll in polls if poll > day]
            end = later[0] if later else None
            base = f"{kind}:{year}:{slug(full)}"
            tenure = cs.key("gb.london.tenure", base)
            place = (year, leh.fold(full).split()[-1])
            replaced = REPLACEMENTS.get(place) if kind == "assembly" else None
            row = {
                "id": tenure,
                "person_id": person,
                "office_id": office if kind == "mayor" else None,
                "body_id": body if kind == "assembly" else None,
                "from_date": day,
                "from_precision": "day",
                "to_date": end,
                "to_precision": "day",
                "acquired_via": "elected",
                "contest_id": contest,
                "ended_via": "term_end" if end else None,
            }
            if replaced is None:
                cs.add("tenure", **row)
                continue
            used.add(place)
            successor, start, start_precision, until, until_precision, pages = replaced
            cs.add("tenure", **{**row, "to_date": until, "to_precision": until_precision, "ended_via": "resigned"})
            cs.add("citation", entity_id=tenure, source_id=former)
            follow = cs.key("gb.london.tenure", f"{base}:successor")
            cs.add(
                "tenure",
                id=follow,
                person_id=person_of(successor, True),
                office_id=None,
                body_id=body,
                from_date=start,
                from_precision=start_precision,
                to_date=end,
                to_precision="day",
                acquired_via="list_replacement",
                contest_id=contest,
                ended_via="term_end" if end else None,
                succeeds_tenure_id=tenure,
            )
            for page in pages:
                cs.add("citation", entity_id=follow, source_id=wiki[page])
            cs.add("citation", entity_id=follow, source_id=results_2024 if year == 2024 else former)
        if missing := sorted(set(REPLACEMENTS) - used):
            raise ValueError(f"replacements with no elected list member: {missing}")
        if unmatched:
            print(f"elected with no Wikidata match: {unmatched}")
        return cs.write()
