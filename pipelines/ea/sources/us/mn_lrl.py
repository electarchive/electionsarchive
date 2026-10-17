import difflib
import html
import itertools
import re
import statistics
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mn_lrl"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.lrl.mn.gov/archive/sessions/electionresults/{}-g-sec.pdf"
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Minnesota Election Results, by county"
WRITE_IN = ("Write-in", None)
UNNAMED = ("American Party, no candidates specified", "A")
TABLES = {
    "1972-11-07": [
        (
            "president",
            7,
            8,
            {
                1: ("George McGovern", "DFL"),
                2: ("Richard Nixon", "R"),
                3: ("Louis Fisher", "IG"),
                4: ("Linda Jenness", "SW"),
                5: ("Gus Hall", "C"),
                6: ("John G. Schmitz", "AM"),
                7: ("Benjamin Spock", "PP"),
                8: WRITE_IN,
            },
        ),
        (
            "senate",
            9,
            10,
            {
                0: ("Walter F. Mondale", "DFL"),
                1: ("Phil Hansen", "R"),
                2: ("Karl H. Heck", "IG"),
            },
        ),
    ],
    "1974-11-05": [
        (
            "governor",
            7,
            8,
            {
                1: ("Wendell R. Anderson", "DFL"),
                2: ("John W. Johnson", "R"),
                3: ("Jane Van Deusen", "SW"),
                4: ("Erwin Marquit", "C"),
                5: ("Harry M. Pool", "AM"),
                6: ("Richard R. Kleinow", "LIB"),
                7: ("Genevieve Gunderson", "IG"),
                8: ("James G. Miles", None),
            },
        ),
        (
            "secretary-of-state",
            9,
            10,
            {
                0: ("Joan Anderson Growe", "DFL"),
                1: ("Arlen Erdahl", "R"),
                2: ("Daniel J. Slater", "AM"),
            },
        ),
        (
            "auditor",
            9,
            10,
            {
                3: ("Robert W. Mattson", "DFL"),
                4: ("Rolland F. Hatfield", "R"),
            },
        ),
        (
            "treasurer",
            9,
            10,
            {
                5: ("Jim Lord", "DFL"),
                6: ("J. Robert Stassen", "R"),
            },
        ),
        (
            "attorney-general",
            11,
            12,
            {
                0: ("Warren Spannaus", "DFL"),
                1: ("Dean A. Nyquist", "R"),
                2: ("Allan W. Lamkin", "AM"),
            },
        ),
    ],
    "1976-11-02": [
        (
            "president",
            4,
            6,
            {
                3: ("Gerald Ford", "IR"),
                4: ("Jimmy Carter", "DFL"),
                5: ("Peter Camejo", "SW"),
                6: ("Roger MacBride", "LIB"),
                7: ("Gus Hall", "C"),
                8: ("Jules Levin", "IG"),
                9: ("Tom Anderson", "AM"),
                10: ("Eugene McCarthy", None),
                11: ("Lyndon LaRouche", "IDB"),
                12: ("Frank Zeidler", "SP"),
                13: ("Margaret Wright", "PP"),
            },
        ),
        (
            "senate",
            4,
            6,
            {
                15: ("Jerry Brekke", "IR"),
                16: ("Hubert H. Humphrey", "DFL"),
                17: ("Bill Peterson", "SW"),
                18: ("Robin E. Miller", "LIB"),
                19: ("Matt Savola", "C"),
                20: ("Paul Helm", "AM"),
            },
        ),
    ],
    "1978-11-07": [
        (
            "senate:special",
            2,
            3,
            {
                3: ("Paul Helm", "AM"),
                4: ("David Durenberger", "IR"),
                5: ("Bob Short", "DFL"),
                6: ("Christine Frank", "SW"),
                7: ("Fred G. Hewitt", "LP"),
            },
        ),
        (
            "senate",
            2,
            3,
            {
                8: ("Sal Carlone", "AM"),
                9: ("Rudy Boschwitz", "IR"),
                10: ("Wendell Anderson", "DFL"),
                11: ("Bill Peterson", "SW"),
                12: ("Leonard Richards", "LP"),
                13: ("Jean T. Brust", "WP"),
                14: ("Brian John Coyle", "PI"),
            },
        ),
        (
            "governor",
            2,
            3,
            {
                15: ("Richard Pedersen", "AM"),
                16: ("Al Quie", "IR"),
                17: ("Rudy Perpich", "DFL"),
                18: ("Jill Lakowske", "SW"),
                19: ("Tom McDonald", "HG"),
                20: ("Robin E. Miller", "LP"),
                21: ("Edwin C. Pommerening", "SA"),
            },
        ),
        (
            "secretary-of-state",
            4,
            5,
            {
                0: ("Dianna Jenkins", "AM"),
                1: ("Gerald W. Brekke", "IR"),
                2: ("Joan Anderson Growe", "DFL"),
            },
        ),
        (
            "auditor",
            4,
            5,
            {
                3: ("Arne Carlson", "IR"),
                4: ("Robert W. Mattson", "DFL"),
                5: ("Helen S. Kruth", "C"),
            },
        ),
        (
            "treasurer",
            4,
            5,
            {
                6: ("Donald G. Hanson", "AM"),
                7: ("Rick Teske", "IR"),
                8: ("Jim Lord", "DFL"),
            },
        ),
        (
            "attorney-general",
            4,
            5,
            {
                9: ("Arnie Olsen", "AM"),
                10: ("Howard A. Knutson", "IR"),
                11: ("Warren Spannaus", "DFL"),
            },
        ),
    ],
    "1980-11-04": [
        (
            "president",
            2,
            3,
            {
                3: UNNAMED,
                4: ("Jimmy Carter", "DFL"),
                5: ("Ronald Reagan", "IR"),
                6: ("Ed Clark", "LIB"),
                7: ("Clifton DeBerry", "SW"),
                8: ("Deirdre Griswold", "WW"),
                9: ("Gus Hall", "C"),
                10: ("John Anderson", None),
                11: ("David McReynolds", "SP"),
                12: ("Barry Commoner", "CP"),
            },
        ),
    ],
    "1982-11-02": [
        (
            "senate",
            2,
            3,
            {
                3: ("Dave Durenberger", "IR"),
                4: ("Mark Dayton", "DFL"),
                5: ("Bill Onasch", "SW"),
                6: ("Jeffrey M. Miller", None),
                7: ("Fred G. Hewitt", "LP"),
            },
        ),
        (
            "governor",
            6,
            7,
            {
                0: ("Wheelock Whitney", "IR"),
                1: ("Rudy Perpich", "DFL"),
                2: ("Kathy Wheeler", "SW"),
                3: ("Tom McDonald", None),
                4: ("Franklin H. Haws", "LP"),
            },
        ),
        (
            "secretary-of-state",
            6,
            7,
            {
                5: ("Mark Hanson", "IR"),
                6: ("Joan Anderson Growe", "DFL"),
                7: ("Linda Taylor", "LP"),
            },
        ),
        (
            "auditor",
            8,
            9,
            {
                0: ("Arne Carlson", "IR"),
                1: ("Paul Wellstone", "DFL"),
            },
        ),
        (
            "treasurer",
            8,
            9,
            {
                2: ("Bonn Clayton", "IR"),
                3: ("Robert W. Mattson", "DFL"),
                4: ("Helen Kruth", "C"),
            },
        ),
        (
            "attorney-general",
            8,
            9,
            {
                5: ("Elliot Rothenberg", "IR"),
                6: ("Hubert H. Humphrey III", "DFL"),
                7: ("Samuel A. Faulk", "CPP"),
            },
        ),
    ],
    "1984-11-06": [
        (
            "president",
            2,
            3,
            {
                3: ("Ronald Reagan", "IR"),
                4: ("Walter F. Mondale", "DFL"),
                5: ("Mel Mason", "SWP"),
                6: ("David Bergland", "LIB"),
                7: ("Lyndon LaRouche", None),
                8: ("Gus Hall", None),
                9: ("Ed Winn", None),
                10: ("Bob Richards", None),
                11: ("Sonia Johnson", None),
                12: ("Dennis Serrette", None),
            },
        ),
        (
            "senate",
            2,
            3,
            {
                13: ("Rudy Boschwitz", "IR"),
                14: ("Joan Anderson Growe", "DFL"),
                15: ("Eleanor Garcia", "SWP"),
                16: ("Jeffrey M. Miller", None),
                17: ("Richard Putman", None),
            },
        ),
    ],
    "1986-11-04": [
        (
            "governor",
            2,
            3,
            {
                3: ("Rudy Perpich", "DFL"),
                4: ("Cal R. Ludeman", "IR"),
                5: ("Z. Bill Brust", None),
                6: ("Tom Jaax", "SWP"),
                7: ("Joseph A. Rohner III", "LIB"),
            },
        ),
        (
            "secretary-of-state",
            2,
            3,
            {
                8: ("Joan Anderson Growe", "DFL"),
                9: ("Don Koenig", "IR"),
            },
        ),
        (
            "auditor",
            2,
            3,
            {
                10: ("John Dooley", "DFL"),
                11: ("Arne Carlson", "IR"),
            },
        ),
        (
            "treasurer",
            2,
            3,
            {
                12: ("Michael A. McGrath", "DFL"),
                13: ("John Drew", "IR"),
                14: ("Helen Kruth", "C"),
            },
        ),
        (
            "attorney-general",
            2,
            3,
            {
                15: ("Hubert H. Humphrey III", "DFL"),
                16: ("Lew Freeman", "IR"),
                17: ("Derrick P. Grimmer", "GRP"),
            },
        ),
    ],
    "1988-11-08": [
        (
            "president",
            2,
            3,
            {
                3: ("George Bush", "IR"),
                4: ("Michael Dukakis", "DFL"),
                5: ("James Warren", "SWP"),
                6: ("Lenora Fulani", None),
                7: ("Lyndon LaRouche", None),
                8: ("Jack Herer", "GRP"),
                9: ("Ron Paul", "LIB"),
                10: ("Ed Winn", None),
                11: ("Delmar Dennis", None),
                12: ("David Duke", None),
                13: ("Eugene McCarthy", None),
            },
        ),
        (
            "senate",
            2,
            3,
            {
                14: ("Dave Durenberger", "IR"),
                15: ("Hubert H. Humphrey III", "DFL"),
                16: ("Derrick P. Grimmer", "GRP"),
                17: ("Wendy Lyons", "SWP"),
                18: ("Arlen Overvig", "LIB"),
                19: ("Polly Mann", None),
            },
        ),
    ],
    "1990-11-06": [
        (
            "senate",
            3,
            4,
            {4: ("Paul David Wellstone", "DFL"), 5: ("Rudy Boschwitz", "IR"), 6: ("Russell B. Bentley", "GRP")},
        ),
        (
            "governor",
            5,
            6,
            {
                0: ("Rudy Perpich", "DFL"),
                1: ("Arne Carlson", "IR"),
                2: ("Wendy Lyons", "SWP"),
                3: ("Ross S. Culverhouse", "GRP"),
                4: ("Heartwarrior Chosa", None),
                5: ("Jon Grunseth", "IR"),
            },
        ),
        (
            "secretary-of-state",
            5,
            6,
            {
                6: ("Joan Anderson Growe", "DFL"),
                7: ("David Jennings", "IR"),
                8: ("Candice Sjostrom", "GRP"),
            },
        ),
        (
            "auditor",
            7,
            8,
            {
                0: ("Mark Dayton", "DFL"),
                1: ("Bob Heinrich", "IR"),
            },
        ),
        (
            "treasurer",
            7,
            8,
            {
                2: ("Michael A. McGrath", "DFL"),
                3: ("John Burger", "IR"),
                4: ("Colleen Bonniwell", "GRP"),
            },
        ),
        (
            "attorney-general",
            7,
            8,
            {
                5: ("Hubert H. Humphrey III", "DFL"),
                6: ("Kevin E. Johnson", "IR"),
            },
        ),
    ],
    "1992-11-03": [
        (
            "president",
            3,
            4,
            {
                4: ("George Bush", "IR"),
                5: ("Bill Clinton", "DFL"),
                6: ("Jack Herer", "GRP"),
                7: ("James Warren", "SWP"),
                8: ("Ross Perot", None),
            },
        ),
        (
            "president",
            5,
            6,
            {
                0: ("John Hagelin", "NLP"),
                1: ("Lenora Fulani", "NAP"),
                2: ("James Gritz", "CP"),
                3: ("Howard Phillips", "MTP"),
                4: ("Lyndon LaRouche", "IER"),
                5: ("Andre Marrou", "LIB"),
                6: WRITE_IN,
            },
        ),
    ],
    "1994-11-08": [
        (
            "senate",
            3,
            4,
            {
                4: ("Rod Grams", "IR"),
                5: ("Ann Wynia", "DFL"),
                6: ("Dean M. Barkley", "IP"),
                7: ("Marea Himelgrin", "SWP"),
                8: ("Stephen Johnson", "NLP"),
                9: ("Candice E. Sjostrom", "GRP"),
            },
        ),
        (
            "governor",
            5,
            6,
            {
                0: ("Arne Carlson", "IR"),
                1: ("John Marty", "DFL"),
                2: ("Will Shetterly", "GRP"),
                3: ("Jon Hillson", "SWP"),
                4: ("Eric Arthur Olson", "LIB"),
                5: ("Leslie Davis", None),
            },
        ),
        (
            "secretary-of-state",
            5,
            6,
            {
                6: ("Dick Kimbler", "IR"),
                7: ("Joan Anderson Growe", "DFL"),
                8: ("Dale D. Wilkinson", "GRP"),
            },
        ),
        (
            "auditor",
            7,
            8,
            {
                0: ("Judi Dutcher", "IR"),
                1: ("Donald M. Moe", "DFL"),
                2: ("Steven C. Anderson", "GRP"),
            },
        ),
        (
            "treasurer",
            7,
            8,
            {
                3: ("James C. Olson", "IR"),
                4: ("Michael A. McGrath", "DFL"),
                5: ("Colleen Bonniwell", "GRP"),
            },
        ),
        (
            "attorney-general",
            7,
            8,
            {
                6: ("Sharon Anderson", "IR"),
                7: ("Hubert H. Humphrey III", "DFL"),
                8: ("Dean W. Amundson", "GRP"),
            },
        ),
    ],
    "1996-11-05": [
        (
            "president",
            3,
            4,
            {4: ("Ross Perot", "RP"), 5: ("Bob Dole", "R"), 6: ("Bill Clinton", "DFL"), 7: ("John Hagelin", "NLP")},
        ),
        (
            "president",
            5,
            6,
            {
                0: ("James Harris", "SWP"),
                1: ("Howard Phillips", "UST"),
                2: ("Harry Browne", "LIB"),
                3: ("Dennis Peron", "GRP"),
                4: ("John Birrenbach", "IG"),
                5: ("Jerome White", "SEP"),
                6: ("Ralph Nader", "GPM"),
                7: WRITE_IN,
            },
        ),
        (
            "senate",
            7,
            8,
            {
                0: ("Dean Barkley", "RP"),
                1: ("Rudy Boschwitz", "R"),
                2: ("Paul D. Wellstone", "DFL"),
                3: ("Tim Davis", "GRP"),
                4: ("Howard B. Hanson", None),
                5: ("Steve Johnson", "NLP"),
                6: ("Thomas A. Fiske", "SWP"),
                7: ("Roy Ezra Carlton", "LIB"),
                8: WRITE_IN,
            },
        ),
    ],
    "1998-11-03": [
        (
            "governor",
            3,
            4,
            {
                4: ("Jesse Ventura", "RP"),
                5: ("Norm Coleman", "R"),
                6: ("Hubert H. Humphrey III", "DFL"),
                7: ("Frank Germann", "LIB"),
                8: ("Chris Wright", None),
            },
        ),
        (
            "governor",
            5,
            6,
            {0: ("Thomas A. Fiske", "SWP"), 1: ("Fancy Ray McCloney", None), 2: ("Ken Pentel", "GPM"), 3: WRITE_IN},
        ),
        (
            "secretary-of-state",
            5,
            6,
            {
                4: ("Alan Shilepsky", "RP"),
                5: ("Mary Kiffmeyer", "R"),
                6: ("Edwina Garcia", "DFL"),
                7: ("Kenneth Iverson", "LIB"),
                8: WRITE_IN,
            },
        ),
        (
            "auditor",
            7,
            8,
            {
                0: ("Judi Dutcher", "R"),
                1: ("Nancy A. Larson", "DFL"),
                2: ("Joseph G. Peschek", "PM"),
                3: ("Bob Odden", "LIB"),
                4: ("Patricia G. V. Becker", "MTP"),
                5: WRITE_IN,
            },
        ),
        (
            "treasurer",
            7,
            8,
            {
                6: ("James N. Dunlop", "RP"),
                7: ("Kevin Knight", "R"),
                8: ("Carol Johnson", "DFL"),
                9: ("Mitchell Berg", "LIB"),
                10: WRITE_IN,
            },
        ),
        (
            "attorney-general",
            9,
            10,
            {
                0: ("Jim Mangan", "RP"),
                1: ("Charlie Weaver", "R"),
                2: ("Mike Hatch", "DFL"),
                3: ("Ruth A. Mason", "LIB"),
                4: WRITE_IN,
            },
        ),
    ],
}
READ = {
    ("1974-11-05", 11, "Warren Spannaus"): {
        "Aitkin": 2782,
        "Anoka": 28271,
        "Becker": 5057,
        "Beltrami": 4692,
        "Benton": 4459,
        "Big Stone": 1807,
        "Blue Earth": 10236,
        "Brown": 6041,
        "Carlton": 7262,
        "Carver": 5415,
        "Cass": 4217,
        "Chippewa": 4206,
        "Chisago": 4555,
        "Clay": 7871,
        "Clearwater": 1917,
        "Cook": 932,
        "Cottonwood": 3032,
        "Crow Wing": 7897,
        "Dakota": 30552,
        "Dodge": 2513,
        "Douglas": 5921,
        "Faribault": 4744,
        "Fillmore": 3872,
        "Freeborn": 7302,
        "Goodhue": 8107,
        "Grant": 1821,
        "Hennepin": 177542,
        "Houston": 3283,
        "Hubbard": 2809,
        "Isanti": 4003,
        "Itasca": 9980,
        "Jackson": 3169,
        "Kanabec": 2327,
        "Kandiyohi": 7264,
        "Kittson": 2050,
        "Koochiching": 4694,
        "Lac qui Parle": 2861,
        "Lake": 3551,
        "Lake of the Woods": 1080,
        "Le Sueur": 4951,
        "Lincoln": 2363,
        "Lyon": 5718,
        "McLeod": 4572,
        "Mahnomen": 1480,
        "Marshall": 3709,
        "Martin": 4627,
        "Meeker": 4471,
        "Mille Lacs": 4165,
        "Morrison": 6895,
        "Mower": 9716,
        "Murray": 2987,
        "Nicollet": 5223,
        "Nobles": 4662,
        "Norman": 2295,
        "Olmsted": 12477,
        "Otter Tail": 8069,
        "Pennington": 2968,
        "Pine": 4257,
        "Pipestone": 2659,
        "Polk": 7808,
        "Pope": 2876,
        "Ramsey": 91285,
        "Red Lake": 1435,
        "Redwood": 3449,
        "Renville": 5216,
        "Rice": 7990,
        "Rock": 1994,
        "Roseau": 2864,
        "St. Louis": 56330,
        "Scott": 7963,
        "Sherburne": 4498,
        "Sibley": 3491,
        "Stearns": 19207,
        "Steele": 4144,
        "Stevens": 2526,
        "Swift": 3991,
        "Todd": 4552,
        "Traverse": 2058,
        "Wabasha": 4423,
        "Wadena": 2604,
        "Waseca": 2949,
        "Washington": 18692,
        "Watonwan": 3062,
        "Wilkin": 1908,
        "Winona": 8854,
        "Wright": 8729,
        "Yellow Medicine": 3601,
    },
    ("1974-11-05", 11, "Dean A. Nyquist"): {
        "Aitkin": 1551,
        "Anoka": 11371,
        "Becker": 2969,
        "Beltrami": 2819,
        "Benton": 2215,
        "Big Stone": 762,
        "Blue Earth": 5486,
        "Brown": 3964,
        "Carlton": 2629,
        "Carver": 3912,
        "Cass": 3169,
        "Chippewa": 2038,
        "Chisago": 2312,
        "Clay": 4481,
        "Clearwater": 818,
        "Cook": 692,
        "Cottonwood": 2536,
        "Crow Wing": 4883,
        "Dakota": 13805,
        "Dodge": 1863,
        "Douglas": 3517,
        "Faribault": 3496,
        "Fillmore": 3154,
        "Freeborn": 5071,
        "Goodhue": 5495,
        "Grant": 1150,
        "Hennepin": 90732,
        "Houston": 2728,
        "Hubbard": 2042,
        "Isanti": 1824,
        "Itasca": 4247,
        "Jackson": 1582,
        "Kanabec": 1203,
        "Kandiyohi": 3319,
        "Kittson": 931,
        "Koochiching": 1815,
        "Lac qui Parle": 1440,
        "Lake": 1399,
        "Lake of the Woods": 567,
        "Le Sueur": 2542,
        "Lincoln": 943,
        "Lyon": 2630,
        "McLeod": 3800,
        "Mahnomen": 571,
        "Marshall": 1532,
        "Martin": 4001,
        "Meeker": 3256,
        "Mille Lacs": 2198,
        "Morrison": 2837,
        "Mower": 3954,
        "Murray": 1758,
        "Nicollet": 3004,
        "Nobles": 2411,
        "Norman": 1101,
        "Olmsted": 9070,
        "Otter Tail": 6401,
        "Pennington": 1296,
        "Pine": 1782,
        "Pipestone": 1863,
        "Polk": 3103,
        "Pope": 1331,
        "Ramsey": 34543,
        "Red Lake": 425,
        "Redwood": 2999,
        "Renville": 2714,
        "Rice": 4505,
        "Rock": 2004,
        "Roseau": 1989,
        "St. Louis": 18855,
        "Scott": 3108,
        "Sherburne": 2122,
        "Sibley": 2487,
        "Stearns": 9169,
        "Steele": 3293,
        "Stevens": 1421,
        "Swift": 1367,
        "Todd": 2621,
        "Traverse": 787,
        "Wabasha": 2631,
        "Wadena": 1979,
        "Waseca": 2746,
        "Washington": 8708,
        "Watonwan": 2237,
        "Wilkin": 1082,
        "Winona": 4577,
        "Wright": 4822,
        "Yellow Medicine": 1879,
    },
    ("1974-11-05", 11, "Allan W. Lamkin"): {
        "Aitkin": 219,
        "Anoka": 1661,
        "Becker": 255,
        "Beltrami": 424,
        "Benton": 549,
        "Big Stone": 58,
        "Blue Earth": 371,
        "Brown": 677,
        "Carlton": 315,
        "Carver": 1148,
        "Cass": 389,
        "Chippewa": 167,
        "Chisago": 311,
        "Clay": 121,
        "Clearwater": 147,
        "Cook": 37,
        "Cottonwood": 175,
        "Crow Wing": 929,
        "Dakota": 1129,
        "Dodge": 204,
        "Douglas": 474,
        "Faribault": 237,
        "Fillmore": 197,
        "Freeborn": 198,
        "Goodhue": 275,
        "Grant": 43,
        "Hennepin": 6372,
        "Houston": 157,
        "Hubbard": 203,
        "Isanti": 241,
        "Itasca": 695,
        "Jackson": 145,
        "Kanabec": 234,
        "Kandiyohi": 571,
        "Kittson": 73,
        "Koochiching": 241,
        "Lac qui Parle": 184,
        "Lake": 242,
        "Lake of the Woods": 81,
        "Le Sueur": 206,
        "Lincoln": 85,
        "Lyon": 247,
        "McLeod": 245,
        "Mahnomen": 71,
        "Marshall": 140,
        "Martin": 478,
        "Meeker": 369,
        "Mille Lacs": 303,
        "Morrison": 587,
        "Mower": 210,
        "Murray": 134,
        "Nicollet": 179,
        "Nobles": 196,
        "Norman": 91,
        "Olmsted": 782,
        "Otter Tail": 300,
        "Pennington": 177,
        "Pine": 497,
        "Pipestone": 96,
        "Polk": 417,
        "Pope": 153,
        "Ramsey": 3000,
        "Red Lake": 90,
        "Redwood": 346,
        "Renville": 261,
        "Rice": 226,
        "Rock": 64,
        "Roseau": 156,
        "St. Louis": 2597,
        "Scott": 406,
        "Sherburne": 359,
        "Sibley": 160,
        "Stearns": 2797,
        "Steele": 92,
        "Stevens": 84,
        "Swift": 134,
        "Todd": 421,
        "Traverse": 90,
        "Wabasha": 277,
        "Wadena": 190,
        "Waseca": 112,
        "Washington": 943,
        "Watonwan": 139,
        "Wilkin": 52,
        "Winona": 422,
        "Wright": 905,
        "Yellow Medicine": 182,
    },
    ("1978-11-07", 2, "Jean T. Brust"): {
        "Aitkin": 21,
        "Anoka": 126,
        "Becker": 37,
        "Beltrami": 30,
        "Benton": 41,
        "Big Stone": 8,
        "Blue Earth": 44,
        "Brown": 19,
        "Carlton": 55,
        "Carver": 34,
        "Cass": 28,
        "Chippewa": 17,
        "Chisago": 29,
        "Clay": 40,
        "Clearwater": 19,
        "Cook": 24,
        "Cottonwood": 8,
        "Crow Wing": 52,
        "Dakota": 133,
        "Dodge": 13,
        "Douglas": 30,
        "Faribault": 15,
        "Fillmore": 26,
        "Freeborn": 36,
        "Goodhue": 35,
        "Grant": 2,
        "Hennepin": 599,
        "Houston": 27,
        "Hubbard": 16,
        "Isanti": 21,
        "Itasca": 109,
        "Jackson": 14,
        "Kanabec": 9,
        "Kandiyohi": 23,
        "Kittson": 11,
        "Koochiching": 20,
        "Lac qui Parle": 8,
        "Lake": 41,
        "Lake of the Woods": 7,
        "Le Sueur": 21,
        "Lincoln": 10,
        "Lyon": 34,
        "Mahnomen": 18,
        "Marshall": 23,
        "Martin": 20,
        "McLeod": 28,
        "Meeker": 14,
        "Mille Lacs": 44,
        "Morrison": 34,
        "Mower": 37,
        "Murray": 16,
        "Nicollet": 14,
        "Nobles": 22,
        "Norman": 6,
        "Olmsted": 36,
        "Otter Tail": 37,
        "Pennington": 22,
        "Pine": 29,
        "Pipestone": 25,
        "Polk": 39,
        "Pope": 19,
        "Ramsey": 305,
        "Red Lake": 10,
        "Redwood": 14,
        "Renville": 23,
        "Rice": 45,
        "Rock": 7,
        "Roseau": 14,
        "St. Louis": 430,
        "Scott": 37,
        "Sherburne": 53,
        "Sibley": 15,
        "Stearns": 132,
        "Steele": 20,
        "Stevens": 13,
        "Swift": 18,
        "Todd": 33,
        "Traverse": 3,
        "Wabasha": 26,
        "Wadena": 12,
        "Waseca": 26,
        "Washington": 116,
        "Watonwan": 10,
        "Wilkin": 7,
        "Winona": 59,
        "Wright": 60,
        "Yellow Medicine": 28,
    },
}
FIXES = {
    **{
        ("1974-11-05", 7, county, 4): votes
        for county, votes in {
            "Becker": 14,
            "Benton": 20,
            "Blue Earth": 25,
            "Brown": 14,
            "Carlton": 32,
            "Chisago": 14,
            "Crow Wing": 29,
            "Dakota": 89,
            "Hennepin": 1497,
            "Isanti": 47,
            "Itasca": 34,
            "Kandiyohi": 24,
        }.items()
    },
    ("1974-11-05", 9, "Cass", 6): 3437,
    ("1974-11-05", 9, "Chippewa", 6): 2567,
    ("1974-11-05", 9, "Hennepin", 6): 120747,
    ("1976-11-02", 4, "Hennepin", 15): 119121,
    ("1976-11-02", 4, "Hennepin", 16): 316130,
    ("1976-11-02", 4, "Hennepin", 17): 2489,
    ("1976-11-02", 4, "Hennepin", 18): 1630,
    ("1976-11-02", 4, "Hennepin", 19): 723,
    ("1976-11-02", 4, "Hennepin", 20): 27435,
    ("1976-11-02", 4, "TOTAL", 15): 478602,
    ("1976-11-02", 4, "TOTAL", 19): 2214,
    ("1976-11-02", 4, "TOTAL", 20): 125612,
    ("1980-11-04", 2, "Otter Tail", 9): 14,
    ("1980-11-04", 2, "Otter Tail", 10): 1538,
    ("1980-11-04", 2, "Pennington", 9): 14,
    ("1980-11-04", 2, "Pennington", 10): 465,
    ("1992-11-03", 3, "Mahnomen", 8): 483,
    ("1998-11-03", 3, "Beltrami", 5): 5872,
    ("1998-11-03", 7, "Murray", 1): 2274,
    ("1998-11-03", 7, "Lincoln", 2): 57,
    ("1998-11-03", 9, "Mahnomen", 0): 69,
    ("1998-11-03", 7, "Lake of the Woods", 3): 23,
    ("1998-11-03", 7, "Pine", 3): 227,
    ("1998-11-03", 7, "Le Sueur", 4): 823,
    ("1998-11-03", 7, "Nicollet", 4): 757,
    ("1998-11-03", 7, "Nicollet", 7): 5287,
    ("1998-11-03", 7, "Nobles", 7): 3275,
    ("1998-11-03", 7, "Olmsted", 7): 21457,
    ("1998-11-03", 7, "Otter Tail", 7): 11678,
    ("1998-11-03", 7, "Freeborn", 8): 6722,
    ("1998-11-03", 7, "Lincoln", 8): 1578,
    ("1998-11-03", 7, "Pipestone", 8): 2067,
    ("1998-11-03", 7, "Lake of the Woods", 9): 27,
    ("1998-11-03", 7, "Le Sueur", 9): 227,
    ("1998-11-03", 7, "Marshall", 9): 67,
    ("1998-11-03", 3, "Isanti", 6): 2665,
    ("1998-11-03", 3, "Jackson", 6): 1728,
    ("1998-11-03", 3, "Lake", 6): 2472,
}
MISPRINTS = {
    ("1990-11-06", 5, "TOTAL", 6): 1094698,
    ("1972-11-07", 9, "TOTAL", 0): 981340,
    ("1976-11-02", 4, "TOTAL", 15): 478611,
    ("1980-11-04", 2, "TOTAL", 3): 6139,
    ("1980-11-04", 2, "TOTAL", 4): 954174,
    ("1982-11-02", 6, "Becker", 0): 5158,
    ("1982-11-02", 6, "TOTAL", 0): 715796,
    ("1990-11-06", 5, "Aitkin", 1): 2735,
    ("1998-11-03", 3, "Washington", 5): 32565,
}
SHIFTED = {("1990-11-06", 5): {"McLeod": "Martin", "Mahnomen": "McLeod", "Marshall": "Mahnomen", "Martin": "Marshall"}}
MISPRINTED = "The printed canvass shows {} in {} County; this set uses {:,}, which the statewide total and the other tables support."
NOTES = {
    "1990-11-06:MN:secretary-of-state": "Joan Anderson Growe's county figures sum to 1,094,698; the canvass prints "
    "a statewide total of 1,094,696. The same table prints Martin, McLeod, Mahnomen, and Marshall counties one row "
    "out of place; each row is assigned here to the county whose turnout it fits.",
    "1972-11-07:MN:senate": "Walter Mondale's county figures sum to 981,340; the canvass and the state canvassing "
    "board's certificate give a statewide total of 981,320.",
    "1974-11-05:MN:governor": "The canvass lists write-in votes for Tom McDonald (71) and others (77) for only a few "
    "counties; they are left out of this set.",
    "1976-11-02:MN:senate": "Jerry Brekke's county figures sum to 478,611; the canvass prints a statewide total of "
    "478,602.",
    "1980-11-04:MN:president": "The canvass prints statewide totals of 954,173 for Jimmy Carter and 6,138 for the "
    "American Party line; their county figures sum to 954,174 and 6,139.",
    "1982-11-02:MN:governor": MISPRINTED.format("1,158 votes for Wheelock Whitney", "Becker", 5158)
    + " The printed statewide total, 711,796, carries the same slip.",
    "1990-11-06:MN:governor": MISPRINTED.format("2,753 votes for Arne Carlson", "Aitkin", 2735)
    + " The canvass also prints the governor figures for Martin, McLeod, Mahnomen, and Marshall counties one row "
    "out of place; each row is assigned here to the county whose turnout it fits. Jon Grunseth withdrew as the "
    "Independent-Republican nominee on October 29, 1990, and the votes cast for him are kept as printed.",
    "1998-11-03:MN:governor": MISPRINTED.format("32,564 votes for Norm Coleman", "Washington", 32565),
}
OFFICES = {
    "secretary-of-state": "Secretary of State",
    "auditor": "State Auditor",
    "treasurer": "State Treasurer",
    "attorney-general": "Attorney General",
}
PARTIES = {
    "A": "MN:american",
    "AM": "MN:american",
    "C": "MN:communist",
    "CP": "MN:citizens",
    "SP": "socialist-party-usa",
    "WW": "workers-world-party",
    "DFL": "MN:dem-farmer-labor",
    "IR": "MN:independent-republican",
    "R": "republican-party",
    "RP": "reform-party",
    "LIB": "libertarian-party",
    "LP": "libertarian-party",
    "SWP": "socialist-workers-party",
    "SW": "socialist-workers-party",
    "NLP": "natural-law-party",
    "GRP": "MN:grassroots",
}
DEBUG = False
ZEROS = {"a", "o", "O", "D"}


def fetch() -> None:
    for day in TABLES:
        raw.fetch(SOURCE, URL.format(day), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = sorted(
            (
                text
                for (text,) in conn.execute(
                    """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like '27%'"""
                ).fetchall()
            ),
            key=lambda text: re.sub(r"^st", "saint", re.sub(r"^mc", "mac", re.sub(r"[^a-z]", "", text.lower()))),
        )
    if len(counties) != 87:
        raise ValueError("Minnesota needs 87 counties")
    orders = [
        sorted(
            counties,
            key=lambda text, mac=mac, saint=saint: re.sub(
                "^stl", saint, re.sub(r"^mc" if mac else "^$", "mac", re.sub(r"[^a-z]", "", text.lower()))
            ),
        )
        for mac in (True, False)
        for saint in ("saintl", "stl", "st l")
    ]

    def plain(text: str) -> str:
        return re.sub(r"[^a-z]", "", text.lower())

    keys = {plain(county): county for county in counties} | {"saintlouis": "St. Louis"}

    def county(text: str) -> str | None:
        if re.fullmatch(r"[a-z]{0,3}(state)?tot[a-z]{0,3}", plain(text)):
            return "TOTAL"
        found = difflib.get_close_matches(plain(text), keys, 1, 0.75)
        return keys[found[0]] if found else None

    rows: list[dict[str, object]] = []
    for day, tables in TABLES.items():
        document = raw.cached(SOURCE, URL.format(day)).path
        for office, first, last, columns in tables:
            cells: dict[str, list[str]] = {}
            previous = orders[0]
            for number in range(first, last + 1):
                text = subprocess.run(
                    ["pdftotext", "-bbox", "-f", str(number), "-l", str(number), str(document), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                words = [
                    (float(x0), float(y0), float(x1), html.unescape(word))
                    for x0, y0, x1, word in re.findall(
                        r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)<', text
                    )
                ]
                starts = [
                    word
                    for word in words
                    if len(plain(word[3])) >= 3
                    and (county(word[3]) or any(key.startswith(plain(word[3])) for key in keys))
                    and not re.search(r"\d", word[3])
                ]
                margin = min(word[0] for word in starts)
                top = min(word[1] for word in starts if word[0] < margin + 15) - 2
                if all(county(word[3]) == "TOTAL" or plain(word[3]) == "state" for word in starts):
                    top = min(word[1] for word in words if re.fullmatch(r"\d+", word[3])) - 2
                while above := [
                    word
                    for word in words
                    if top - 8 < word[1] < top and word[0] < margin + 70 and re.fullmatch(r"[A-Za-z.,']+", word[3])
                ]:
                    if county(" ".join(word[3] for word in sorted(above))) in (None, "TOTAL"):
                        break
                    top = min(word[1] for word in above) - 2
                left = [
                    word for word in words if word[0] < margin + 15 and word[1] > top and re.search("[A-Za-z]", word[3])
                ]
                grown = True
                while grown:
                    grown = False
                    for word in words:
                        if (
                            word not in left
                            and re.fullmatch(r"[A-Za-z.']+", word[3])
                            and any(abs(word[1] - other[1]) < 2 and 0 <= word[0] - other[2] < 6 for other in left)
                        ):
                            left.append(word)
                            grown = True
                lines: list[tuple[float, list[tuple[float, float, float, str]]]] = []
                for word in sorted(left, key=lambda word: word[1]):
                    if lines and abs(word[1] - lines[-1][0]) < 2.5:
                        lines[-1][1].append(word)
                    else:
                        lines.append((word[1], [word]))
                named = [
                    (y, county(" ".join(word[3] for word in group if not re.fullmatch(r"[.·…,]+", word[3]))))
                    for y, group in lines
                ]
                edge = max(word[2] for y, group in lines for word in group if county(" ".join(w[3] for w in group))) + 2
                tokens = sorted(
                    (
                        word
                        for word in words
                        if word[0] > edge
                        and word[1] > top
                        and (
                            re.search(r"\d", word[3])
                            or word[3] == "*"
                            or (len(word[3]) <= 3 and re.search("[A-Za-z]", word[3]))
                        )
                    ),
                    key=lambda word: word[2],
                )
                pending: list[list[tuple[float, float, float, str]]] = []
                for word in tokens:
                    if pending and word[2] - pending[-1][-1][2] < 9:
                        pending[-1].append(word)
                    else:
                        pending.append([word])
                found: list[list[tuple[float, float, float, str]]] = []
                while pending:
                    column = pending.pop(0)
                    heights = sorted(word[1] for word in column)
                    if len(column) > 3 and sum(1 for a, b in itertools.pairwise(heights) if b - a < 2) > 3:
                        across = sorted(column, key=lambda word: word[2])
                        cut = max(range(1, len(across)), key=lambda k: across[k][2] - across[k - 1][2])
                        pending[:0] = [across[:cut], across[cut:]]
                    else:
                        found.append(sorted(column, key=lambda word: word[1]))
                anchors: list[tuple[float, str]] = []
                order = counties
                best: tuple[int, float] = (0, 0.0)
                for candidate in orders:
                    chains: list[list[tuple[float, str]]] = []
                    for y, name in named:
                        if name is not None and name in candidate:
                            prior = [chain for chain in chains if candidate.index(chain[-1][1]) < candidate.index(name)]
                            chains.append([*max(prior, key=len, default=[]), (y, name)])
                    if not chains:
                        continue
                    chain = max(chains, key=len)
                    steps = [
                        (after - y) / (candidate.index(later) - candidate.index(name))
                        for (y, name), (after, later) in itertools.pairwise(chain)
                    ]
                    score = (len(chain), -max((abs(step - statistics.median(steps)) for step in steps), default=0))
                    if score > best:
                        best, anchors, order = score, chain, candidate
                if not anchors:
                    order = previous
                    start = max((order.index(name) for name in cells if name != "TOTAL"), default=-1) + 1
                    ending = [y for y, name in named if name == "TOTAL"]
                    anchors = list(
                        zip(
                            [word[1] for word in max(found, key=len) if not ending or word[1] < ending[0] - 3],
                            order[start:],
                            strict=True,
                        )
                    )
                previous = order
                grid = list(anchors)
                for (y, name), (after, later) in itertools.pairwise(anchors):
                    gap = order.index(later) - order.index(name)
                    grid.extend(
                        (y + (after - y) * step / gap, order[order.index(name) + step]) for step in range(1, gap)
                    )
                spacing = (anchors[-1][0] - anchors[0][0]) / max(
                    1, order.index(anchors[-1][1]) - order.index(anchors[0][1])
                )
                totals = [(y, name) for y, name in named if name == "TOTAL" and y > anchors[-1][0]]
                longest = max(found, key=len)
                shift = longest[0][1] - anchors[0][0]
                beyond = [
                    word[1] - shift
                    for word in longest
                    if word[1] - shift > anchors[-1][0] + spacing / 2
                    and (not totals or word[1] - shift < totals[0][0] - spacing / 2)
                ]
                grid.extend(
                    (y, order[order.index(anchors[-1][1]) + step])
                    for step, y in enumerate(beyond, 1)
                    if order.index(anchors[-1][1]) + step < len(order)
                )
                named = sorted([*grid, *totals[:1]])
                if DEBUG:
                    print([(round(y, 1), name) for y, name in named])
                heights = [y for y, _ in named]
                page: dict[str, list[str]] = {name: [] for _, name in named}
                for column in (column for column in found if len(column) > len(named) / 3):
                    shift = column[0][1] - heights[0]
                    placed: dict[str, list[str]] = {}
                    for word in column:
                        index = min(range(len(heights)), key=lambda k: abs(heights[k] + shift - word[1]))
                        if abs(heights[index] + shift - word[1]) <= 3.5:
                            placed.setdefault(named[index][1], []).append(
                                "0"
                                if word[3] in ZEROS
                                else "*"
                                if word[3] == "*"
                                else re.sub(r"[.,*·;:'+\-]", "", word[3])
                            )
                    for name, values in page.items():
                        values.append(" ".join(placed.get(name, ["?"])))
                for name, values in page.items():
                    name = SHIFTED.get((day, first), {}).get(name, name)
                    if name == "TOTAL":
                        cells["TOTAL"] = values
                    else:
                        cells.setdefault(name, []).extend(values)
            missing = [
                name
                for name in counties
                if name not in cells and any((day, first, candidate) not in READ for candidate, _ in columns.values())
            ]
            if missing or "TOTAL" not in cells:
                raise ValueError(f"{day} {office} pages {first}–{last}: no row for {missing or 'the total'}")
            if len(cells["TOTAL"]) <= max(columns):
                raise ValueError(f"{day} {office} pages {first}–{last}: the total row has {cells['TOTAL']}")
            for index, (candidate, party) in columns.items():
                total = int(
                    MISPRINTS.get(
                        (day, first, "TOTAL", index), FIXES.get((day, first, "TOTAL", index), cells["TOTAL"][index])
                    )
                )
                values = READ.get((day, first, candidate)) or {
                    name: MISPRINTS.get(
                        (day, first, name, index),
                        FIXES.get((day, first, name, index), cells[name][index] if index < len(cells[name]) else "?"),
                    )
                    for name in counties
                }
                absent = [name for name, value in values.items() if value == "*"]
                unread = [
                    name
                    for name, value in values.items()
                    if name not in absent and not re.fullmatch(r"\d+", str(value))
                ]
                rest = sum(int(value) for name, value in values.items() if name not in unread and name not in absent)
                if len(unread) == 1 and total - rest >= 0:
                    values[unread[0]] = total - rest
                elif unread or rest != total:
                    raise ValueError(
                        f"{day} {office} pages {first}–{last}, {candidate}: counties sum to {rest} of {total}, "
                        f"unread {[(name, values[name]) for name in unread]}"
                    )
                rows.extend(
                    {
                        "day": day,
                        "office": office,
                        "county": name,
                        "candidate": candidate,
                        "party": party,
                        "votes": int(value),
                    }
                    for name, value in values.items()
                    if name not in absent
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's president, U.S. Senate, governor, and statewide offices by county 1972–1998, "
            "from the Secretary of State's results archived by the Legislative Reference Library",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            text = re.sub(r"\([^)]*\)", " ", text)
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.lrl.mn.gov/mngov/electionresults",
            license="Public record",
        )
        for day in TABLES:
            cs.artifact(raw.cached(SOURCE, URL.format(day)), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '27%%'"""
            ).fetchall()
        )
        people: dict[str, set[UUID]] = {}
        years: dict[UUID, set[int]] = {}
        for person, text, year in conn.execute(
            """select distinct cd.person_id, n.text, left(x.value, 4)::int from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                 and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:MN:[a-z-]+(:special)?$'
               join name n on n.entity_id = cd.person_id and n.is_preferred"""
        ).fetchall():
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            years.setdefault(person, set()).add(year)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MN:'")
        )
        made: set[UUID] = set()
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:MN:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            found = [value for value in contests if value[:4] == day[:4] and value[10:] == key[10:]]
            if found:
                contest = contests[found[0]]
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s", [contest]
                ).fetchall()
            else:
                ((stamp, election, area),) = conn.execute(
                    """select left(x.value, 10), c.election_id, c.district_area_id from contest c
                       join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                       where x.value ~ %s""",
                    [f"^{day[:4]}-..-..:MN:governor$"],
                ).fetchall()
                office_id = cs.key("us.office", f"{office}:MN")
                if office_id in cs.minted and office_id not in made:
                    made.add(office_id)
                    cs.add("office", id=office_id, polity_id=cs.polity("US-MN"), kind="other")
                    cs.add(
                        "name",
                        entity_id=office_id,
                        text=f"{OFFICES[office].removeprefix('State ')} of Minnesota",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                contest = cs.key("us.contest", f"{stamp}:MN:{office}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{day[:4]} Minnesota {OFFICES[office]} election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/mn/{stamp}-general/{office}", entity_id=contest)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind <> 'others'""",
                [contest],
            ).fetchall()
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, party, votes in (
                lines.group_by("candidate", maintain_order=True)
                .agg(pl.col("party").first(), pl.col("votes").sum())
                .iter_rows()
            ):
                if candidate == UNNAMED[0]:
                    option = cs.key("us.option", f"{key}:legend:A")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="legend",
                        party_id=parties[party],
                        ballot_label=candidate,
                    )
                elif candidate == WRITE_IN[0]:
                    generic = [option for option, kind, _ in options if kind == "write_in"]
                    option = generic[0] if len(generic) == 1 else cs.key("us.option", f"{key}:write_in:mn_lrl")
                    if option in cs.minted:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-in"
                        )
                else:
                    matches = [
                        option
                        for option, kind, text in options
                        if kind != "write_in" and surname(text or "") == surname(candidate)
                    ]
                    if len(matches) > 1:
                        raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                    if matches:
                        option = matches[0]
                    else:
                        known = people.get(plain(candidate)) or people.get(
                            f"{plain(candidate.split()[0])}|{surname(candidate)}", set()
                        )
                        person = (
                            min(known, key=lambda one: min(abs(year - int(day[:4])) for year in years.get(one, {0})))
                            if known
                            else cs.key(f"{SOURCE}.person", f"MN:{slug(candidate)}")
                        )
                        if person in cs.minted and person not in made:
                            made.add(person)
                            people.setdefault(f"{plain(candidate.split()[0])}|{surname(candidate)}", set()).add(person)
                            cs.add("person", id=person)
                            cs.add(
                                "name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True
                            )
                        option = cs.key("us.option", f"{key}:{person}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="ticket" if office == "president" else "candidate",
                            party_id=parties.get(party or ""),
                            ballot_label=candidate,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=party is None,
                        )
                if option in totals:
                    raise ValueError(f"{key}: two candidates share an option with {candidate}")
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[candidate] = option
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
            if key in NOTES:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=NOTES[key])
            blank = [
                f"{candidate} in {', '.join(sorted(set(counties) - set(group['county'])))}"
                for (candidate,), group in lines.group_by("candidate", maintain_order=True)
                if group.height < len(counties)
            ]
            if blank:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The county canvassing boards supplied no figure for "
                    + "; ".join(blank)
                    + ". Those counties have no value for those candidates here.",
                )
            for name, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[name],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
