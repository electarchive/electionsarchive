import collections
import difflib
import html
import io
import itertools
import json
import re
import statistics
import struct
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl
from PIL import Image
from psycopg.types.range import Range

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.fec"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
DOCUMENTS = "https://www.fec.gov/documents/"
FILES = {
    2002: "1620/2002fedresults.xls",
    2004: "1633/federalelections2004.xls",
    2006: "1642/federalelections2006.xls",
    2008: "1666/federalelections2008.xls",
    2010: "1675/federalelections2010.xls",
    2012: "1691/federalelections2012.xls",
    2014: "1700/federalelections2014.xls",
    2016: "1890/federalelections2016.xlsx",
    2018: "2706/federalelections2018.xlsx",
    2020: "4228/federalelections2020.xlsx",
    2022: "5676/federalelections2022.xlsx",
    2024: "5645/2024presgeresults.xlsx",
}
BOOKS = {
    1982: "1547/federalelections82.pdf",
    1984: "1548/federalelections84.pdf",
    1986: "1549/federalelections86.pdf",
    1988: "1550/federalelections88.pdf",
    1990: "1551/federalelections90.pdf",
    1992: "1552/federalelections92.pdf",
    1994: "1553/federalelections94.pdf",
    1996: "1554/federalelections96.pdf",
    1998: "1555/federalelections98.pdf",
    2000: "1556/federalelections00.pdf",
}
MASTERS = {
    year: f"https://www.fec.gov/files/bulk-downloads/{year}/cn{year % 100:02d}.zip" for year in (*BOOKS, 2024, 2026)
}
CLERK = {year: f"https://clerk.house.gov/member_info/electionInfo/{year}election.pdf" for year in range(1982, 1993, 2)}
STATISTICS = "https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf"
STATISTICS_TITLE = "Statistics of the Presidential and Congressional Election of November 5, 2024"
STATISTICS_OFFICES = {
    "FORUNITEDSTATESSENATOR": "senate",
    "FORUNITEDSTATESREPRESENTATIVE": "house",
    "FORDELEGATE": "house",
    "FORRESIDENTCOMMISSIONER": "house",
}
STATISTICS_WRITE_INS = {
    "Write-in",
    "Write-in (Scattering)",
    "Scattering",
    "All Others",
    "Miscellaneous",
    "Other Write-ins",
}
STATISTICS_UNCOUNTED = {
    "Blank",
    "Blanks",
    "Blank Votes",
    "Void",
    "Over Votes",
    "Under Votes",
    "Invalid",
    "Continuing Ballots",
    "Exhausted Ballots",
}
STATISTICS_LABELS = {
    "Democrat": "Democratic",
    "Common Sense": "Common Sense Party",
    "Unity Party": "Unity Party of Colorado",
    "United Utah": "United Utah Party",
    "Labour": "Labour Party",
    "Natural Law": "Natural Law Party",
    "Independent American": "Independent American Party",
    "American Constitution": "American Constitution Party",
    "Popular Democratic Party": "Partido Popular Democrático (Popular Democratic Party)",
    "New Progressive Party": "Partido Nuevo Progresista (New Progressive Party)",
    "Puerto Rican Independence Party": "Partido Independentista Puertorriqueño (Puerto Rican Independence Party)",
    "Citizen’s Victory Movement": "Movimiento Victoria Ciudadana (Citizens Victory Movement)",
    "Project Dignity": "Proyecto Dignidad (Dignity Project)",
}
STATISTICS_NAMES = {"Idaho Law - Carta Sierra": "Carta Sierra"}
STATISTICS_IDS = {"Aumua Amata Coleman Radewagen": "H4AS00036"}
FINAL_ROUNDS = {("ME", 2)}
RANKED_WINNERS = {("AK", 0): "Nick Begich"}
OWNED = {("CA", "senate")}
CANVASSES = {
    "2018-11-06:WI:house:4": (
        (
            "https://elections.wi.gov/sites/default/files/legacy/"
            "County%2520by%2520County%2520Report-2018%2520Gen%2520Election-Congress_0.pdf"
        ),
        "County by County Report, 2018 General Election, Representative in Congress",
        4,
        {
            "Moore": "Gwen S. Moore",
            "Rogers": "Tim Rogers",
            "Raymond": "Robert R. Raymond",
            "SCATTERING": "Scattered",
            "Total": None,
        },
    )
}
CANVASS_SOURCE = (
    "Wisconsin Elections Commission county-by-county canvass report for Representative in Congress, generated "
    "December 2, 2018. Page 4 covers District 4."
)
CANVASS_NOTE = (
    "These counts come from the Wisconsin Elections Commission canvass. The FEC's results for this race swap the votes "
    "of Tim Rogers (Republican, 59,091 here) and Robert R. Raymond (independent, 7,170 here)."
)
PRESIDENTIAL_PAGES = {1996: [*range(22, 34), 174], 2000: [*range(22, 37), 204, 206]}
PAGES = DATA / "pages" / SOURCE
DERIVED = 1
DIGITIZED = 8
APPORTIONMENT = {
    1982: {
        "AL": 7, "AK": 1, "AZ": 5, "AR": 4, "CA": 45, "CO": 6, "CT": 6, "DE": 1, "FL": 19, "GA": 10, "HI": 2, "ID": 2, "IL": 22,
        "IN": 10, "IA": 6, "KS": 5, "KY": 7, "LA": 8, "ME": 2, "MD": 8, "MA": 11, "MI": 18, "MN": 8, "MS": 5, "MO": 9, "MT": 2,
        "NE": 3, "NV": 2, "NH": 2, "NJ": 14, "NM": 3, "NY": 34, "NC": 11, "ND": 1, "OH": 21, "OK": 6, "OR": 5, "PA": 23, "RI": 2,
        "SC": 6, "SD": 1, "TN": 9, "TX": 27, "UT": 3, "VT": 1, "VA": 10, "WA": 8, "WV": 4, "WI": 9, "WY": 1,
    },
    1992: {
        "AL": 7, "AK": 1, "AZ": 6, "AR": 4, "CA": 52, "CO": 6, "CT": 6, "DE": 1, "FL": 23, "GA": 11, "HI": 2, "ID": 2, "IL": 20,
        "IN": 10, "IA": 5, "KS": 4, "KY": 6, "LA": 7, "ME": 2, "MD": 8, "MA": 10, "MI": 16, "MN": 8, "MS": 5, "MO": 9, "MT": 1,
        "NE": 3, "NV": 2, "NH": 2, "NJ": 13, "NM": 3, "NY": 31, "NC": 12, "ND": 1, "OH": 19, "OK": 6, "OR": 5, "PA": 21, "RI": 2,
        "SC": 6, "SD": 1, "TN": 9, "TX": 30, "UT": 3, "VT": 1, "VA": 11, "WA": 9, "WV": 3, "WI": 9, "WY": 1,
    },
}  # fmt: skip
JUNGLE = {
    1982: date(1982, 9, 11),
    1984: date(1984, 9, 29),
    1986: date(1986, 9, 27),
    1988: date(1988, 10, 1),
    1990: date(1990, 10, 6),
    1992: date(1992, 10, 3),
    1994: date(1994, 10, 1),
    1996: date(1996, 9, 21),
}
DECIDED_IN_NOVEMBER = {(1986, None), (1986, 7), (1986, 8), (1988, 8), (1990, 2), (1992, 4), (1992, 5), (1992, 6)}
DECLARED = {
    (1982, "LA", 3): ('W.J. "Billy" Tauzin', "Democratic"),
    (1982, "LA", 4): ('Charles "Buddy" Roemer', "Democratic"),
    (1984, "LA", 3): ('W.J. "Billy" Tauzin', "Democratic"),
    (1984, "LA", 4): ('Charles "Buddy" Roemer', "Democratic"),
    (1984, "LA", 5): ('Thomas J. "Jerry" Huckaby', "Democratic"),
    (1986, "LA", 1): ("Robert L. Livingston", "Republican"),
    (1986, "LA", 3): ('W.J. "Billy" Tauzin', "Democratic"),
    (1986, "LA", 4): ('Charles "Buddy" Roemer', "Democratic"),
    (1988, "LA", 6): ("Richard Baker", "Republican"),
    (1988, "LA", 7): ("James Hayes", "Democratic"),
    (1990, "LA", 6): ("Richard Baker", "Republican"),
    (1992, "FL", 21): ("Lincoln Diaz-Balart", "Republican"),
}
TRANSCRIBED: dict[tuple[int, str, int | None], list[int]] = {
    (1984, "OH", 17): [123014, 105449, 2198],
    (1986, "OH", 15): [97745, 55750],
    (1988, "NY", 14): [99179, 57503],
}
RESTORED = {(1984, "OH", 17): 1}
MISPRINTED = {
    (1988, "MA", 6): 225067,
    (1992, "FL", 3): 150033,
    (1994, "OK", 6): 155360,
    (1996, "AZ", 6): 225287,
}
MISPRINTED_PRIMARIES = {(1996, "HI", 2, "Democratic"): 229937}
UNDERCOUNTED = {
    (1988, "OH", 12): "John R. Kasich 204,892 and Mark P. Brown 50,782",
    (1988, "OH", 15): "Chalmers P. Wylie 154,694 and Mark S. Froehlich 51,172",
}
OLD_DISTRICTS = {(1992, "NY", 8): 17}
RENAMED = {
    (1982, "CA", "William E. (BDI) Dannemeyer"): "William E. (Bill) Dannemeyer",
    (1982, "HI", "Sperk Matsunaga"): "Spark Matsunaga",
    (1982, "IN", "Dati Burton"): "Dan Burton",
    (1982, "KS", "Kent"): "Kent Earnest",
    (1982, "KS", "William L. Nostar"): "William L. Kostar",
    (1982, "NM", "Bil Richardson"): "Bill Richardson",
    (1982, "NY", '"olm "'): "John J. LaFalce",
    (1982, "NY", "Carl F. Grlllo"): "Carl F. Grillo",
    (1982, "NY", "Charies E. Schumer"): "Charles E. Schumer",
    (1982, "NY", "John LeBoutilller"): "John LeBoutillier",
    (1982, "PA", "Bil Clinger"): "Bill Clinger",
    (1982, "PA", "Nicholas K. ydonieus"): "Nicholas Kydonieus",
    (1982, "TX", "Bruce liams"): "Bruce Iiams",
    (1982, "WV", "Nick Joe Rahall"): "Nick Joe Rahall II",
    (1984, "CA", "Tohn Paul Stark"): "John Paul Stark",
    (1984, "GA", "(Buddy) Darden George"): "George (Buddy) Darden",
    (1984, "NH", "ArneR Erickson"): "Arne R. Erickson",
    (1984, "PA", "Thomas J. Fullard II"): "Thomas J. Fullard III",
    (1984, "TX", "Tr. LaRouche Lyndon H."): "Lyndon H. LaRouche Jr.",
    (1984, "WI", "David L. laquinta"): "David L. Iaquinta",
    (1984, "WV", "Nick Joe Rahall"): "Nick Joe Rahall II",
    (1986, "FL", "Addison S. Gilbert II"): "Addison S. Gilbert III",
    (1986, "PA", "William H. Gray"): "William H. Gray III",
    (1988, "AR", "Tomy Robinson"): "Tommy Robinson",
    (1988, "CA", "Tan Lantos"): "Tom Lantos",
    (1988, "CA", "Ton Campbell"): "Tom Campbell",
    (1988, "KS", "Michael Ss Dukakis"): "Michael S. Dukakis",
    (1988, "MA", "Joseph D. Farly"): "Joseph D. Early",
    (1988, "NJ", "Joseph Azzzolina"): "Joseph Azzolina",
    (1988, "PA", "Iouis R. Parugini Jr."): "Louis R. Parugini Jr.",
    (1988, "WI", "James Mac S. Warren"): "James Mac Warren",
    (1990, "MN", "Tan Maitland"): "Ian Maitland",
    (1990, "NJ", 'Paul"Daniels" Danielczyk'): 'Paul "Daniels" Danielczyk',
    (1992, "AZ", "Winn Robert B."): "Robert B. Winn",
    (1992, "NC", "Norris. Weathers"): "Norris O. Weathers",
    (1992, "VA", '" Chapman J.L. "Jim IV'): 'J.L. "Jim" Chapman IV',
    (1992, "VA", '" Vickery Raymond E. "Ray Jr.'): 'Raymond E. "Ray" Vickery Jr.',
    (1992, "WA", ". Harvey Wolden"): "Harvey Wolden",
    (1994, "CA", "Enma Wong Mar"): "Emma Wong Mar",
    (1994, "CA", "Michael Der Manovel Jr."): "Michael Der Manouel Jr.",
    (1994, "CA", "Q. U. OOley'"): "Cal Dooley",
    (1994, "CA", "Tomy Randle"): "Tommy Randle",
    (1994, "CO", "David Skagps"): "David Skaggs",
    (1994, "CO", "Ton Simpson"): "Tom Simpson",
    (1994, "CO", "William F. Fepert"): "William F. Eggert",
    (1994, "FL", "Emie Mailhot"): "Ernie Mailhot",
    (1994, "IA", "Paul Umde"): "Paul Lunde",
    (1994, "IL", 'Jeffery J. "Jeff" Tomezak'): 'Jeffery J. "Jeff" Tomczak',
    (1994, "KS", "John A. Bares"): "John A. Barnes",
    (1994, "KY", "Charles L. Qwen"): "Charles L. Owen",
    (1994, "KY", "Edvard Whitfield"): "Edward Whitfield",
    (1994, "KY", "Tomy Klein"): "Tommy Klein",
    (1994, "MA", "Mitt Ramey"): "W. Mitt Romney",
    (1994, "MD", "Willian Brock"): "William Brock",
    (1994, "MI", "Fulton B. Faglin"): "Fulton B. Eaglin",
    (1994, "MN", "Marea Himelerin"): "Marea Himelgrin",
    (1994, "NC", "Same. B. Ostwalt"): "Samuel O. B. Ostwalt",
    (1994, "NV", "Nonman E. Hollingsworth"): "Norman E. Hollingsworth",
    (1994, "NY", "David A. Franezyk"): "David A. Franczyk",
    (1994, "NY", "Ferme M. Steckler"): "Ferne M. Steckler",
    (1994, "NY", "Jeffrey Q'Bannon"): "Jeffrey O'Bannon",
    (1994, "OH", "Joseph, J. Jacobs Jr."): "Joseph J. Jacobs Jr.",
    (1994, "OK", "Emest Istook"): "Ernest Istook",
    (1994, "OR", "Gene Nami"): "Gene Nanni",
    (1994, "PA", "Arthur E. Drev"): "Arthur E. Drew",
    (1994, "PA", "Donald C. Exnsberger"): "Donald C. Ernsberger",
    (1994, "RI", "ThOIIBS R. Post Jr."): "Thomas R. Post Jr.",
    (1994, "TX", "Demis Boerner"): "Dennis Boerner",
    (1994, "TX", "Emest J. Anderson Jr."): "Ernest J. Anderson Jr.",
    (1994, "TX", "John Longsvorth"): "John Longsworth",
    (1994, "TX", "LeEar Ann Bryant"): "LeEarl Ann Bryant",
    (1994, "UT", "Fnid Greene Waldholtz"): "Enid Greene Waldholtz",
    (1994, "VA", "Leslie L. Bymme"): "Leslie L. Byrne",
    (1994, "WI", "E'.dloond Galileo Hou-Seye"): "Edmond Galileo Hou-Seye",
    (1996, "AL", "Hugh McLnnish"): "Hugh McInnish",
    (1996, "CO", "Scott McLnnis"): "Scott McInnis",
    (1996, "FL", "Musheer Muhanunad"): "Musheer Muhammad",
    (1996, "FL", "Sandy Vvarshaw Freedman"): "Sandy Warshaw Freedman",
    (1996, "FL", "Vvarren H. France"): "Warren H. France",
    (1996, "IN", 'John Lawrence "L" Sillith'): 'John Lawrence "L" Smith',
    (1996, "IN", "Lee H. Halllilton"): "Lee H. Hamilton",
    (1996, "IN", "Mnoja Ajabu"): "Mmoja Ajabu",
    (1996, "KS", "Jill Dockmg"): "Jill Docking",
    (1996, "LA", "Peggy WIison"): "Peggy Wilson",
    (1996, "LA", "Sadie Robtrts-Joseph"): "Sadie Roberts-Joseph",
    (1996, "MS", 'Arion "Blackie" Coate'): 'Arlon "Blackie" Coate',
    (1996, "NC", "Russell Woilman"): "Russell Wollman",
    (1996, "NJ", "A. Bily Patricia"): "Patricia A. Bily",
    (1996, "NJ", "Steven R. Rotlunan"): "Steven R. Rothman",
    (1996, "OH", "Larry. Peoples"): "Larry O. Peoples",
    (1996, "TX", "Dick Amrey"): "Dick Armey",
    (1996, "UT", "II, Madison Alden Parker"): "Madison Alden Parker II",
    (1996, "UT", "Johan Komelis Rust"): "Johan Kornelis Rust",
    (1996, "WA", "Don Sruart"): "Don Stuart",
    (1996, "WI", "John I-Iagelin"): "John Hagelin",
    (1996, "WY", "John I-Iagelin"): "John Hagelin",
    (1998, "CA", "Barbara Joxer"): "Barbara Boxer",
    (1998, "CA", 'Herny E. "Bud" Manzler'): 'Henry E. "Bud" Manzler',
    (1998, "CA", "M. Degroat Linda"): "Linda M. Degroat",
    (1998, "CA", "Valene Janlois"): "Valerie Janlois",
    (1998, "CO", "Lany E. Johnson"): "Larry E. Johnson",
    (1998, "IL", "D. Phelps David"): "David D. Phelps",
    (1998, "IL", "Dom E. Van Cleave III"): "Dorn E. Van Cleave III",
    (1998, "IN", "Joe I. Jauptmann"): "Joe Hauptmann",
    (1998, "LA", "'Jim McCrery"): '"Jim" McCrery',
    (1998, "TN", "Gary I. I. Worden"): "Gary I. Worden",
    (1998, "TN", "Yan Hilleary"): "Van Hilleary",
    (1998, "TX", "Dick Arrney"): "Dick Armey",
    (1998, "VI", "of the Above Neither"): "Neither of the Above",
    (2000, "CA", "Ameze Washington"): "Arneze Washington",
    (2000, "CA", "Bill Hom"): "Bill Horn",
    (2000, "CA", "Craig Iarkacs"): "Craig Barkacs",
    (2000, "CA", "Harry Irowne"): "Harry Browne",
    (2000, "CA", "LarTy Engwall"): "Larry Engwall",
    (2000, "CA", "Rev. William M., Sr. Kenyon"): "Rev. William M. Kenyon Sr.",
    (2000, "CA", "Tahir I. Ihatti"): "Tahir I. Bhatti",
    (2000, "FL", "Ken. C. McCarthy"): "Ken C. McCarthy",
    (2000, "FL", 'O." "Nikki'): '"Nikki O."',
    (2000, "FL", "Sam Farting"): "Sam Farling",
    (2000, "ID", "Gerald Daigneau"): "Gerald Daigneault",
    (2000, "IL", "David Mc Reynolds"): "David McReynolds",
    (2000, "IN", "George Thomas Iolland"): "George Thomas Holland",
    (2000, "IN", "Na' Jlah Ali"): "Na' Ilah Ali",
    (2000, "KY", "Alan Randleman"): "Alan Handleman",
    (2000, "LA", "Michael P. Hanis"): "Michael P. Harris",
    (2000, "MO", "Jolrn Dady"): "John Dady",
    (2000, "MO", "Pat Iuchanan"): "Pat Buchanan",
    (2000, "MO", "Sharalyn I. Iarris"): "Sharalyn Harris",
    (2000, "MT", "Al Ciore"): "Al Gore",
    (2000, "NC", "Jess Vard"): "Jess Ward",
    (2000, "ND", "Joh Hagelin"): "John Hagelin",
    (2000, "NE", "Alan Jcobsen"): "Alan Jacobsen",
    (2000, "NE", "Harry I. 'ne"): "Harry Browne",
    (2000, "NE", "John Hageli"): "John Hagelin",
    (2000, "NE", "Ralph 'adcr"): "Ralph Nader",
    (2000, "NJ", "John Piekarski"): "John Pickarski",
    (2000, "NV", "Ilarry Browne"): "Harry Browne",
    (2000, "OR", "Patric J. Buchanan"): "Patrick J. Buchanan",
    (2000, "PA", "Royal Iart"): "Royal Hart",
    (2000, "PA", "Terry E. Van Home"): "Terry E. Van Horne",
    (2000, "TN", "Yan Hilleary"): "Van Hilleary",
    (2000, "TX", 'Ronnie "Reeferseed" Gjernre'): 'Ronnie "Reeferseed" Gjemre',
}
NOMINEES = {
    "Walter Mondale": "Walter F. Mondale",
    "David Bergland": "David P. Bergland",
    "Dennis Serrette": "Dennis L. Serrette",
    "Lyndon H. LaRouche": "Lyndon H. LaRouche Jr.",
    "Lyndon LaRouche": "Lyndon H. LaRouche Jr.",
    "Lyndon LaRouche Jr.": "Lyndon H. LaRouche Jr.",
    "Melvin T. Mason": "Mel Mason",
    "Robert Richards": "Bob Richards",
    "Earl Dodge": "Earl F. Dodge",
    "Ed Winn": "Edward Winn",
    "James Warren": "James Mac Warren",
    "John Warren": "James Mac Warren",
    "Ronald E. Paul": "Ron Paul",
    "Roanld E. Paul": "Ron Paul",
    "Lenora Fulani": "Lenora B. Fulani",
    'James "Bo" Gritz': "James Gritz",
    "H. Ross Perot": "Ross Perot",
    "Gloria Estella La Riva": "Gloria La Riva",
    "Perot": "Ross Perot",
    "A. Gore": "Al Gore",
    "Albert Gore": "Al Gore",
    "Pat Buchanan": "Patrick Buchanan",
    "Patrick J. Buchanan": "Patrick Buchanan",
    "Buchanan": "Patrick Buchanan",
    "Dr. John Hagelin": "John Hagelin",
    "John S. Hagelin": "John Hagelin",
    "Monica Gail Moorehead": "Monica Moorehead",
    "Monica G. Moorehead": "Monica Moorehead",
    "James Harris": "James E. Harris",
    "James Edward Harris": "James E. Harris",
    "Charles Collins": "Charles E. Collins",
    "Louie Gene Youngkeit": "Louie G. Youngkeit",
    "David Harold Birchler": "David H. Birchler",
    "Johan K. Rust": "Johan Kornelis Rust",
    "Johan Rust": "Johan Kornelis Rust",
    "Isabelle Masters": "Isabell Masters",
}
ELECTED_NOMINEES = {(2000, "George Bush"): "George W. Bush"}
BOOK_CANDIDATES = {
    "Patrick Buchanan": "P80000805",
    "Monica Moorehead": "P60004405",
    "James E. Harris": "P60004454",
    "Marsha Feinland": "S4CA00357",
}
ABBREVIATIONS = {
    "ARE": "Citizens Against Rising Electric Rates",
    "CAL": "Concerned Citizens Against LILCO",
    "CRV": "Conservative",
    "DEM": "Democratic",
    "ECP": "Effective Congress Party",
    "FGY": "Drug Fighter",
    "FTP": "Fair Trade Party",
    "IND": "Independent",
    "IPL": "Independent Progressive Line",
    "IVP": "Independent Voter",
    "LBL": "Liberal",
    "NAP": "New Alliance",
    "NEI": "Independent Neighbors",
    "PPA": "Public Power Alternative to LILCO",
    "RAL": "Ratepayers Against LILCO",
    "REP": "Republican",
    "RTL": "Right to Life",
    "VCH": "Vote Children 88",
}
MISREAD = {
    "(I LMI(IeJA Republican": ("Republican",),
    "I') m)cratic": ("Democratic",),
    "RIL": ("Right to Life",),
    "Democraki Sota": ("Democratic", "Independent Voter"),
    "Dem.": ("Democratic",),
    "Prolife Fiseal Crv.": ("Prolife Fiscal Crv.",),
    "Forthe People": ("For the People",),
    "Independents for Economic": ("Independents for Economic Recovery",),
    "Justice, Industry": ("Justice, Industry & Agriculture",),
    "Pennsylvanians for": ("Pennsylvanians for Perot",),
    "Campaign for a": ("Campaign for a New Tomorrow",),
}
CODES = {
    "ACP": "A Connecticut Party",
    "ADP": "A Delaware Party",
    "AF": "America First",
    "AFD": "Anti Federalist",
    "AIP": "American Independent",
    "AKI": "Alaskan Independence",
    "AM": "American Party",
    "AMC": "American Constitution Party",
    "AMH": "American Heritage Party",
    "AMS": "Americans",
    "ATP": "Ax Taxes Party",
    "ATX": "U.S. Taxpayers Party of Arkansas",
    "BP": "By Petition",
    "BR": "Buchanan Reform",
    "BST": "Best Party",
    "C": "Conservative",
    "C-NJ": "New Jersey Conservative",
    "CAM": "Constitutional American",
    "CC": "Change Congress",
    "CF": "Citizens First",
    "CNC": "Concerned Citizens",
    "CNJ": "New Jersey Conservative",
    "CON": "Constitution",
    "CPF": "Constitution Party of Florida",
    "CPP": "Concerns of People Party",
    "CST": "Constitutional",
    "CWS": "Citizens with Szabo Party",
    "D": "Democratic",
    "DA": "Democracy in Action Party",
    "DC": "D.C. Statehood",
    "DCG": "D.C. Statehood Green",
    "DDD": "Damn Drug Dealers",
    "DFL": "Democratic-Farmer-Labor",
    "DNL": "Democratic-Nonpartisan League",
    "EF": "Earth Federation",
    "FNP": "Future Now Party",
    "FP": "Fascist Party",
    "FRE": "Freedom Party",
    "FSW": "Florida Socialist Workers",
    "FUP": "Fed Up Party",
    "FUS": "Fusion",
    "FWC": "Francis Worley Congress",
    "GBJ": "God Bless Jersey",
    "GC": "Gun Control Party",
    "GCN": "Green Coalition",
    "GI": "Green Independent",
    "GMN": "Green Party Minnesota",
    "GPF": "Green Party of Florida",
    "GRA": "Green Party of Arkansas",
    "GRC": "Green Party of Colorado",
    "GRM": "Massachusetts Green Party",
    "GRN": "Green",
    "GRT": "Grassroots",
    "GRT-VT": "Vermont Grassroots",
    "HGR": "Hawaii Green",
    "I": "Independent",
    "I-P": "Independent Progressive",
    "I-R": "Independent-Republican",
    "IA": "Independent American",
    "IAP": "Independent American",
    "ICE": "In Common Effort",
    "ICM": "Independent Citizens Movement",
    "IDP": "Independence",
    "IFH": "Inflation Fighting Housewife",
    "IFU": "Independence Fusion Party",
    "IG": "Iowa Green",
    "IGR": "Independent Grassroots",
    "IMG": "Independent Maine Greens Party",
    "INS": "Independent Neighbors",
    "IP": "The Independent Party",
    "IPC": "Independent Peoples Coalition",
    "IPR": "Independent/Progressive",
    "JPR": "Jobs, Property Rights",
    "KAF": "Keep America First Party",
    "L": "Liberal",
    "LB": "Looking Back Party",
    "LBF": "Libertarian Party of Florida",
    "LBT": "Libertarian",
    "LBT-I": "Libertarian Party of Iowa",
    "LBT-IA": "Libertarian Party of Iowa",
    "LEC": "Liberty, Ecology, Community",
    "LFP": "LaRouche for President",
    "LIF": "Long Island First",
    "LMN": "Legal Marijuana Now",
    "LMP": "Legal Marijuana Party",
    "LU": "Liberty Union",
    "LWR": "LaRouche Was Right Party",
    "MNT": "Minnesota Taxpayers",
    "MRP": "Marijuana Reform Party",
    "MTX": "Mississippi Taxpayers",
    "N": "Nonpartisan",
    "NA": "New Alliance",
    "NJC": "New Jersey Conservative",
    "NJI": "New Jersey Independents",
    "NL": "Natural Law",
    "NL-I": "Natural Law Party of Iowa",
    "NLF": "Natural Law Party of Florida",
    "NP": "Nominated by Petition",
    "NPA": "No Party Affiliation",
    "NPP": "New Progressive Party",
    "NTX": "New Hampshire Taxpayers Party",
    "O": "Other",
    "P": "Prohibition",
    "PAC": "Pacific",
    "PAT": "Patriot Party",
    "PC": "Politicians Are Crooks",
    "PF": "Protecting Freedom",
    "PFP": "Peace and Freedom",
    "PG": "Pacific Green",
    "PHA": "Perot Hispano American Party",
    "PL": "Pro Life Party",
    "PLC": "Pro-Life Conservative",
    "PPD": "Popular Democratic Party",
    "PRI": "Puerto Rican Independence Party",
    "PRO": "Progressive",
    "R": "Republican",
    "RC": "Reform Congress",
    "REF": "Reform",
    "RES": "Resource Party",
    "RFM": "Reform Party Minnesota",
    "RP": "Running on Principles",
    "RTL": "Right to Life",
    "SAR": "Socialist Party of Arkansas",
    "SAV": "Save Medicare",
    "SC": "School Choice",
    "SEP": "Socialist Equality",
    "SFL": "Socialist Party of Florida",
    "SOC": "Socialist Party USA",
    "SSS": "Save Social Security",
    "STO": "Stop HMO Abuses",
    "SWC": "Socialist Workers Campaign",
    "SWP": "Socialist Workers",
    "TBA": "T.B.A. Green Party",
    "TCN": "Tax Cut Now Party",
    "TG": "Trust in God",
    "TL": "Term Limits",
    "TLL": "Truth, Life, Liberty",
    "TND": "Timesizing Not Downsizing",
    "TXP": "Taxpayers",
    "TXP-KY": "Taxpayers Party of Kentucky",
    "TXP-MS": "Mississippi Taxpayers",
    "U": "Unenrolled",
    "UC": "United Citizens",
    "UCO": "Unthinkable Courage",
    "UIP": "United Independents Party",
    "UM": "Umoja Party",
    "UN": "Unaffiliated",
    "USA": "Undauntable Stalwart Alliance",
    "USP": "U.S. Pacifist Party",
    "UST": "U.S. Taxpayers",
    "UWS": "United We Serve Party",
    "VR": "Virginia Reform Party",
    "VTX": "Virginia Taxpayers Party",
    "W": "Write-In",
    "WC": "Working Class",
    "WF": "Working Families",
    "WG": "Wisconsin Green",
    "WL": "Workers League",
    "WTP": "We the People",
    "WW": "Workers World",
    (1994, "CP"): "Capitalist",
    (1996, "CP"): "Colorado Prohibition Party",
    (1994, "CFC"): "Cash for Congress Party",
    (2000, "CFC"): "Conscience for Congress",
    (1996, "UIP"): "Unaffiliated Independent Party",
    (1996, "PS"): "Protect Seniors",
}
BLURRED = (
    (range(1994, 2001), r"[LlI\[|]?I?[.\s]*[BE8s][TtIlr1P'X]*", "LBT"),
    (range(1994, 2001), r"[LlI\[|]?[.\s]*[BE8][TIlr1']*-[IL1l]", "LBT-I"),
    (range(1994, 2001), r"R[IlT1]'?[LI1l]", "RTL"),
    (range(1994, 2001), r"GR[ITl1]-V[ITl1r]|oo-vr", "GRT-VT"),
    (range(1994, 2001), r"anc|oc|oN|CN", "CNC"),
    (range(1994, 2001), r"W[IT1l]P", "WTP"),
    (range(1994, 2001), r"0|\)", "O"),
    (range(1994, 1995), r"L[YWU]", "LU"),
    (range(1996, 2001), r"w|WwW|Vv", "W"),
)
FUSED = {"Democratic", "Republican", "Conservative", "Liberal", "Right to Life", "Independent"}
PARTICLES = {"de", "la", "van", "von", "der", "du", "di", "da", "del", "le", "y", "and", "of", "for", "to", "is"}
MISREAD_LETTERS = (
    ("m", "rn"),
    ("rn", "m"),
    ("Il", "H"),
    ("I", "l"),
    ("l", "I"),
    ("l", "H"),
    ("li", "h"),
    ("li", "ll"),
    ("h", "li"),
    ("cl", "d"),
    ("d", "cl"),
    ("ii", "n"),
    ("c", "e"),
    ("vv", "w"),
    ("JJ", "ll"),
    ("n", "m"),
    ("m", "n"),
    ("u", "n"),
    ("n", "u"),
)
MENDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bI\. l(?=[a-z])"), "H"),
    (re.compile(r"((?:Jr|Sr|D)\.)\s+I\.?$"), r"\1"),
    (re.compile(r",\s*(?:M\.D|Ph\.D|Esq)\.?(?=,|\s|$)"), ""),
    (re.compile(r",\s*(?:IIT|ITT|ITI|TII|IlI)\s*$"), ", III"),
    (re.compile(r",\s*(?:IT|TI)\s*$"), ", II"),
    (re.compile(r'(^|\s)"\s+'), r'\1"'),
    (re.compile(r',"'), '"'),
)
SPLIT_INITIAL = re.compile(r"\b([A-Z])\. ([a-z]{2,})")
FLAWED = re.compile(rf"(?<![\w'\"(])(?!(?:{'|'.join(sorted(PARTICLES))})\b)[a-z]\w*")
NICKNAME = re.compile(r'"[^"]*"|\([^)]*\)')
SUFFIXES = ("jr", "sr", "ii", "iii", "iv")
NAME_SHAPE = re.compile(
    r"((Mc|Mac|O'|D'|De|La|Le|Di|Van|St\.)?[A-Z][a-z]+('[a-z]+)?(-[A-Z][a-z]+)?,?|[A-Z]\.([A-Z]\.)*,?"
    r"|\"[A-Z][a-z.]+( [A-Z][a-z.]+)?\"|\([A-Z][a-z]+\)|Jr\.|Sr\.|II|III|IV)"
)


NOTA = "None of These Candidates"
WIDE = {
    "AYYADURAI": ("Shiva Ayyadurai", None),
    "BOWMAN": ("Jay Bowman", None),
    "DE LA CRUZ": ("Claudia De la Cruz", "Party for Socialism and Liberation"),
    "DUNCAN": ("Richard Duncan", None),
    "EBKE": ("Laura Ebke", None),
    "EVERYLOVE": ('Lucifer "Justin Case" Everylove', None),
    "FRUIT": ("Rachele Fruit", "Socialist Workers Party"),
    "GARRITY": ("Chris Garrity", None),
    "HARRIS": ("Kamala D. Harris", "Democratic"),
    "HUBER": ("Blake Huber", "Approval Voting Party"),
    "KENNEDY": ("Robert F. Kennedy Jr.", None),
    "KISHORE": ("Joseph Kishore", "Socialist Equality Party"),
    "OLIVER": ("Chase Oliver", "Libertarian"),
    "PRESTON": ("Mattie Preston", None),
    "SKOUSEN": ("Joel Skousen", "Constitution"),
    "SONSKI": ("Peter Sonski", "American Solidarity Party"),
    "STEIN": ("Jill Stein", "Green"),
    "STODDEN": ("Bill Stodden", "Socialist Party USA"),
    "SUPREME": ("Vermin Supreme", None),
    "TERRY": ("Randall Terry", None),
    "TRUMP": ("Donald J. Trump", "Republican"),
    "WELLS": ("Robby Wells", None),
    "WEST": ("Cornel West", None),
    "WOOD": ("Michael Wood", "Prohibition Party"),
    "NONE OF THESE CANDIDATES": (NOTA, None),
    "WRITE-INS (SCATTERED)": ("Scattered", "Write-In"),
}
LABELS = {
    "DEM": "Democratic",
    "REP": "Republican",
    "WW": "Workers World",
    "AVP": "Approval Voting Party",
    "COV": "Covenant Party",
    "DCG": "D.C. Statehood Green",
    "DFL": "Democratic-Farmer Labor",
    "LIB": "Libertarian",
    "GS": "God Save America",
    "IGR": "Independent Green",
    "MTP": "Mountain Party",
    "GOP": "GOP Party",
    "G": "Green",
    "TRR": "Trump Republican Party",
    "EWP": "Essential Workers Party",
    "ASP": "American Solidarity Party",
}
DUPLICATES = {(2020, "TX", "Scattered")}
PUBLISHER = "Federal Election Commission"
INDEPENDENT = {
    "Independent",
    "Unaffiliated",
    "Nonpartisan",
    "Nonaffiliated",
    "By Petition",
    "Nominated by Petition",
    "Nominated By Petition",
    "Petition",
    "No Party",
    "No Political Party",
    "No Party Affiliation",
    "No Affiliation",
    "Unenrolled",
    "Undeclared",
    "None",
    "Non-Party",
    "Non-Affiliated",
    "Petitioning Candidate",
    "No Party Preference",
    "Other",
    "No Slogan Filed",
    "No Slogan",
    "No Slogan Provided",
}
PARTIES = {
    "Democratic": "Democratic Party",
    "Republican": "Republican Party",
    "G.O.P. Party": "Republican Party",
    "Libertarian": "Libertarian Party",
    "Green": "Green Party",
    "Constitution": "Constitution Party",
    "Reform": "Reform Party",
    "Working Families": "Working Families Party",
    "Conservative": "Conservative Party",
    "Independence": "Independence Party",
    "Progressive": "Progressive Party",
    "Pacific Green": "Pacific Green Party",
    "American Independent": "American Independent Party",
    "Independent American": "Independent American Party",
    "Liberty Union": "Liberty Union Party",
    "Green-Rainbow": "Green-Rainbow Party",
    "Green Independent": "Green Independent Party",
    "Peace and Freedom": "Peace and Freedom Party",
    "Natural Law": "Natural Law Party",
    "Iowa Green": "Iowa Green Party",
    "Wisconsin Green": "Wisconsin Green Party",
    "Wisconsin Greens": "Wisconsin Green Party",
    "Socialism and Liberation Party": "Party for Socialism and Liberation",
    "Party for Socialism and Liberation-Florida": "Party for Socialism and Liberation - Florida",
    "Statehood Green": "D.C. Statehood Green Party",
    "D.C. Statehood Green": "D.C. Statehood Green Party",
    "Democratic-Farmer Labor": "Minnesota Democratic–Farmer–Labor Party",
    "Democratic-Nonpartisan League": "North Dakota Democratic–Nonpartisan League Party",
    "U.S. Taxpayers": "U.S. Taxpayers Party",
    "Populist": "Populist Party",
    "Alliance": "Alliance Party",
    "Workers World": "Workers World Party",
    "Alaskan Independence": "Alaskan Independence Party",
    "Democratic-Farmer-Labor": "Minnesota Democratic–Farmer–Labor Party",
    "Dem. Farmer Labor": "Minnesota Democratic–Farmer–Labor Party",
    "Democratic-Farm-Labor": "Minnesota Democratic–Farmer–Labor Party",
    "Ind. Republican": "Republican Party of Minnesota",
    "Independent-Republican": "Republican Party of Minnesota",
    "Socialist Workers": "Socialist Workers Party",
    "Socialist Equality": "Socialist Equality Party",
    "Legal Marijuana Now Party": "Legal Marijuana Now",
    "Mountain": "Mountain Party",
    "Independent Green": "Independent Green Party",
    "Partido Nuevo Progresista (New Progressive Party)": "New Progressive Party",
    "Partido Popular Democrático (Popular Democratic Party)": "Popular Democratic Party",
    "Partido Independentista Puertorriqueño (Puerto Rican Independence Party)": "Puerto Rican Independence Party",
    "Puerto Rican Independent Party": "Puerto Rican Independence Party",
    "Puerto Ricans for Puerto Rico": "Puerto Ricans for Puerto Rico Party",
    "Proyecto Dignidad (Dignity Project)": "Dignity Project",
    "Movimiento Victoria Ciudadana (Citizens Victory Movement)": "Citizens' Victory Movement",
    "American Shopping": "American Shopping Party",
    "We The People": "We the People",
    "GOP Party": "Republican Party",
    "Grand Old Party": "Republican Party",
    "Libertarian Party of Florida": "Libertarian Party",
    "Unity Party of America": "Unity Party America",
    "Veterans Party": "Veterans Party of America",
    "Constitution Party NJ": "Constitution Party",
}
NATIONAL = {
    "Democratic Party",
    "Republican Party",
    "Libertarian Party",
    "Green Party",
    "Constitution Party",
    "Reform Party",
    "Party for Socialism and Liberation",
    "Socialist Workers Party",
    "Socialist Party USA",
    "Socialist Equality Party",
    "Prohibition Party",
    "American Solidarity Party",
    "Workers World Party",
    "Natural Law Party",
    "Justice Party",
    "Alliance Party",
    "America’s Party",
    "Approval Voting Party",
    "American Delta Party",
    "Legal Marijuana Now",
    "Unity Party America",
    "Objectivist Party",
    "Boston Tea Party",
    "Veterans Party of America",
    "The Better Life",
    "We the People",
    "American Third Position",
    "Bread and Roses",
    "Better for America",
}
COLORS = {
    "Democratic Party": "#1a5fb4",
    "Republican Party": "#c01c28",
    "Libertarian Party": "#e5a50a",
    "Green Party": "#26a269",
}
AFFILIATES = {
    "Minnesota Democratic–Farmer–Labor Party": "Democratic Party",
    "North Dakota Democratic–Nonpartisan League Party": "Democratic Party",
    "Republican Party of Minnesota": "Republican Party",
    "NMI Republican Party Association": "Republican Party",
}
FORMER_NAMES = {
    "Republican Party of Minnesota": ("Independent-Republicans of Minnesota", date(1975, 11, 15), date(1995, 9, 23))
}
FOLDED = {
    "MN:dem-farmer-labor": "MN:minnesota-democratic-farmer-labor-party",
    "MN:democratic-farm-labor": "MN:minnesota-democratic-farmer-labor-party",
    "MN:ind-republican": "MN:republican-party-of-minnesota",
    "MN:independent-republican": "MN:republican-party-of-minnesota",
    "MA:republican-democratic": "republican-party",
    "PA:democratic-and-republican": "republican-party",
    "CA:republican-write-in": "republican-party",
}
COMPOUND = {
    "Republican/Democratic": ["Republican", "Democratic"],
    "Democratic and Republican": ["Republican", "Democratic"],
    "Republican (Write-In)": ["Republican"],
}
REORDERED = {(1984, "PA", "Gus Yatron"): ["Democratic", "Republican"]}
LRL = "https://www.lrl.mn.gov/history/caucus.aspx"
LRL_TITLE = "Party Control of the Minnesota House of Representatives and Senate"
LINKS = {
    "ronald-reagan": "ronald-reagan",
    "walter-f-mondale": "walter-f-mondale",
    "george-bush": "george-bush",
    "michael-s-dukakis": "michael-s-dukakis",
    "bill-clinton": "william-j-clinton",
    "bob-dole": "robert-dole",
    "al-gore": "albert-gore-jr",
    "george-w-bush": "george-w-bush",
    "P00003335": "george-w-bush",
    "P80000235": "john-f-kerry",
    "P80003338": "barack-obama",
    "P80002801": "john-mccain",
    "P80003353": "mitt-romney",
    "P80001571": "donald-j-trump",
    "P00003392": "hillary-clinton",
    "P80000722": "joseph-r-biden-jr",
    "P00000455": "george-bush",
    "P40002347": "john-edwards",
    "P00004754": "joseph-lieberman",
    "P80000748": "ron-paul",
    "P60007168": "bernie-sanders",
    "P60003670": "john-kasich",
    "P60003050": "colin-powell",
    "P60007242": "carly-fiorina",
    "P60012796": "elizabeth-warren",
    "kamala-d-harris": "kamala-d-harris",
    "H8WI01024": "paul-ryan",
    "H8VT01016": "bernie-sanders",
    "S4VT00033": "bernie-sanders",
    "S4IL00180": "barack-obama",
    "S6AZ00019": "john-mccain",
    "H6TX22101": "ron-paul",
    "S6UT00188": "scott-bradley",
    "S8WA00194": "maria-cantwell",
    "H6MN01174": "tim-walz",
    "S0NY00188": "hillary-clinton",
    "S8CT00022": "joseph-lieberman",
    "S6ME00159": "susan-collins",
    "S8DE00012": "joseph-r-biden-jr",
    "S4MA00069": "john-f-kerry",
    "S0CA00330": "carly-fiorina",
    "S2MA00170": "elizabeth-warren",
    "S2VA00142": "tim-kaine",
    "S8UT00176": "mitt-romney",
    "S2OH00436": "jd-vance",
    "H2AK00226": "sarah-palin",
    "S6CA00584": "kamala-d-harris",
}
ALIASES = {
    "H0NY25037": "P00012211",
    "S6NY00342": "P00012211",
    "S4IL00404": "P60003076",
    "H4MS01102": "P20005286",
    "H6VA05068": "P20004685",
    "H2GA11016": "P80004930",
    "H6OH23033": "P40002545",
    "H8OR03029": "P40003337",
    "S6VA00127": "P60008885",
    "S6NY00367": "P80005481",
    "H0NY15145": "P80005481",
    "S6FL00210": "P80005275",
    "S6OH00205": "P20005252",
    "H6TX10098": "P40002537",
    "H6FL18030": "P00004762",
    "S6ND00082": "P80005796",
    "H8KY06230": "P80005796",
    "H8CO01089": "P00007674",
    "H2CO02080": "P00007674",
    "S6AZ00340": "P00007674",
    "H8AZ08224": "P00007674",
    "S8RI00102": "P60008075",
    "S8NJ00418": "P80005077",
    "H6NJ09249": "P80005077",
    "H0CA08085": "P80005572",
    "S0CA00405": "P60004454",
    "S0KY00156": "P40003576",
    "S0FL00338": "P60006723",
    "H2CO01140": "P60023496",
    "S2CO00308": "P60023496",
    "H2FL20100": "P20002424",
    "S2TX00312": "P60006111",
    "H2HI02508": "P00009183",
    "S4KY00174": "P60007929",
    "S4TN00419": "P20002648",
    "H6IL10127": "P00014522",
    "S6FL00442": "P00008987",
    "S8NM00226": "P20002671",
    "H8NY21351": "P60005808",
    "S8MD00393": "P60022043",
    "H8TX02232": "P60023207",
    "H8OH11281": "P60007523",
    "S2UT00369": "P60022654",
    "S2MO00122": "P20005187",
    "S4FL00397": "rachele-fruit",
    "H2GA04185": "rachele-fruit",
    "S4CO00411": "bill-hammons",
    "S8MA00268": "shiva-ayyadurai",
    "S2GA00274": "chase-oliver",
}
NAMES = {
    "P00014613": 'Roque "Rocky" De La Fuente III',
    "S4NC00162": "Thom Tillis",
    "H2CA02142": "Doug LaMalfa",
    "H2OK01143": "Jim Bridenstine",
}
RUNNING_MATES = {
    (1984, "ronald-reagan"): "George Bush",
    (1984, "walter-f-mondale"): "Geraldine A. Ferraro",
    (1988, "george-bush"): "James Danforth Quayle",
    (1988, "michael-s-dukakis"): "Lloyd Bentsen",
    (1992, "bill-clinton"): "Albert Gore, Jr.",
    (1992, "george-bush"): "James Danforth Quayle",
    (2004, "P00003335"): "Richard B. Cheney",
    (2004, "P80000235"): "John Edwards",
    (2004, "P20000527"): "Peter Miguel Camejo",
    (2004, "P40002537"): "Richard V. Campagna",
    (2004, "P40003097"): "Pat LaMarche",
    (2004, "P40003261"): "Chuck Baldwin",
    (2008, "P80003338"): "Joseph R. Biden Jr.",
    (2008, "P80002801"): "Sarah Palin",
    (2008, "P20000527"): "Matt Gonzalez",
    (2008, "P80005580"): "Wayne Allyn Root",
    (2008, "P80005499"): "Darrell L. Castle",
    (2008, "P80004930"): "Rosa Clemente",
    (2012, "P80003338"): "Joseph R. Biden Jr.",
    (2012, "P80003353"): "Paul Ryan",
    (2012, "P20002671"): "Jim Gray",
    (2012, "P20003984"): "Cheri Honkala",
    (2012, "P20004685"): "Jim Clymer",
    (2016, "P80001571"): "Michael R. Pence",
    (2016, "P00003392"): "Tim Kaine",
    (2016, "P20002671"): "Bill Weld",
    (2016, "P20003984"): "Ajamu Baraka",
    (2016, "P60022654"): "Mindy Finn",
    (2016, "P60021102"): "Scott Bradley",
    (2020, "P80001571"): "Michael R. Pence",
    (2020, "P80000722"): "Kamala D. Harris",
    (2020, "P00013524"): "Spike Cohen",
    (2020, "P00012211"): "Angela Walker",
    (2024, "P80001571"): "JD Vance",
    (2024, "kamala-d-harris"): "Tim Walz",
    (2024, "P20003984"): "Butch Ware",
    (2024, "robert-f-kennedy-jr"): "Nicole Shanahan",
    (2024, "chase-oliver"): "Mike ter Maat",
    (2024, "claudia-de-la-cruz"): "Karina Garcia",
    (2024, "cornel-west"): "Melina Abdullah",
}
DISTRICTS = {"ME": ("23", 2, 1972), "NE": ("31", 3, 1992)}
SPLITS = {
    (2008, "3102"): "P80003338",
    (2016, "2302"): "P80001571",
    (2020, "2302"): "P80001571",
    (2020, "3102"): "P80000722",
    (2024, "2302"): "P80001571",
    (2024, "3102"): "kamala-d-harris",
}
FAITHLESS = {(1988, "WV"), (2000, "DC"), (2004, "MN"), (2016, "HI"), (2016, "TX"), (2016, "WA")}
CAVEATS = {
    (
        2020,
        "TX",
    ): 'The FEC\'s 2020 Texas table lists a "Scattered" write-in row of 460 votes. Those 460 votes equal the '
    "combined votes of five named write-in candidates in the same table, and the FEC's state total of 11,315,056 counts "
    'them only once. We left out the "Scattered" row so the candidate rows add up to the published total.',
    (
        2004,
        "NY",
    ): "New York reported 57,230 miscellaneous write-in, blank, and void votes as one combined figure. The FEC "
    "leaves this figure out of the state total, and so do we, because it mixes write-in votes with blank and void "
    "ballots.",
}
COLUMNS = (
    (r"STATE ABBREVIATION", "state"),
    (r"STATE", "named"),
    (r"DISTRICT|D", "district"),
    (r"FEC ID#?", "fec_id"),
    (r".*INCUMBENT.*|\(I\)", "incumbent"),
    (r"FIRST NAME|CANDIDATE NAME \(FIRST\)", "first"),
    (r"LAST NAME|CANDIDATE NAME \(LAST\)", "last"),
    (r"TOTAL VOTES", "label"),
    (r"PARTY", "party"),
    (r"GENERAL|GENERAL VOTES|GENERAL RESULTS", "general"),
    (r"GE RUNOFF|GE RUNOFF ELECTION VOTES.*|GENERAL RUNOFF RESULTS", "runoff"),
    (r"COMBINED GE PARTY TOTALS.*", "combined"),
    (r"GE WINNER INDICATOR", "winner"),
    (r"([1-9])\w\w ROUND RCV VOTES", "rcv{}"),
    (r"PRIMARY|PRIMARY RESULTS|PRIMARY VOTES", "primary"),
    (r"RUNOFF|RUNOFF RESULTS|RUNOFF VOTES", "primary_runoff"),
    (r"PE WINNER INDICATOR", "nominee"),
)
SWITCHES = {
    (2002, "H8PA10055"): "R/D",
    (2002, "H2VT00094"): "PRO/LU",
    (2004, "H2IA01048"): "NP",
    (2006, "S8CT00022"): "CFL",
    (2006, "S4VT00132"): "IND",
    (2008, "H8OR01171"): "IP",
    (2010, "S0CT00193"): "IND",
    (2010, "S0VT00106"): "IND",
    (2012, "S4VT00033"): "IND",
    (2020, "H0CT04211"): "IP",
}
SWAPPED = {(2006, "H8NY19058"): ("DEM", "WF"), (2008, "H8NY21203"): ("D", "WF")}
SPECIALS = {(2002, "MO"), (2020, "AZ")}
EXCLUDED = {(2010, "AK", "W (Challenged NOT Counted)")}
STRAY = {(2004, "S4WA00474")}
MISFILED = {(2016, "LA", "house", 5)}
TYPOS = {"2006:H6NY03031", "2020:H0NY14296"}
UNLINKED = {
    "2002:H0WV02096",
    "2006:S0TN00060",
    "2018:S4TN00450",
    "2018:S8MN00339",
    "2020:S4MI00298",
    "2022:H2NY14037",
    "2022:H8LA03037",
}
MISNAMED = {
    (2016, "H4WI04266"): "Robert R. Raymond",
    (2016, "H6WI01234"): "Spencer Zimmerman",
    (2020, "H0SD00088"): "Randy Luallin",
}
VACANCY = "Vacancy in Nomination"
BLANKET = {"CA": 2012, "WA": 2008, "AK": 2022}
BLANKETED = {(year, "WA") for year in range(1994, 2003, 2)} | {(1996, "AK"), (1998, "AK"), (1998, "CA"), (2000, "CA")}
REPLACEMENTS: dict[tuple[int, str, str, int | None], date] = {
    (2006, "OH", "house", 3): date(2006, 9, 15),
    (2006, "OH", "house", 18): date(2006, 9, 14),
    (2010, "OH", "house", 3): date(2010, 7, 13),
}
SPECIAL_PRIMARIES = {(2010, "WV", "senate", None): date(2010, 8, 28), (2012, "MI", "house", 11): date(2012, 9, 5)}
PRIMARY_RUNOFFS = {
    (1996, "GA"): date(1996, 8, 6),
    (1998, "AR"): date(1998, 6, 9),
    (2002, "SC"): date(2002, 6, 25),
    (2002, "TX"): date(2002, 4, 9),
}
SUMMARIZED: dict[object, str] = {"LIB": "LBT", "DFL": "D", (2012, "PA", "D"): "R", (2020, "LA", "D"): "R"}
SURNAMES = {"Bloomberg": "P00014530", "Romney": "P80003353"}
NAMESAKES = {"H8CA52052", "P00014613"}
MOVED_IDS = {"H2IL06090": "IL:niki-conforti"}
MISATTRIBUTED = {"2020:Democratic:P60016342": "P00014613"}
REUSED_IDS = {
    "2002:H2CA46073:Richard B. Owens": None,
    "2022:H2NY22139:Carolyn B. Maloney": "H2NY14037",
    "2022:H2IL06090:Kina Collins": "H0IL07167",
    '2022:H0IL07167:Jacqueline "Jacqui" Collins': "H2IL01315",
}
TWINS = {
    "P00003343": "S2NC00174",
    "P00003608": "S4GA11046",
    "P00003731": "S4NH00096",
    "P00006213": "H2MD06195",
    "P00006841": "S8AZ00296",
    "P00008532": "S0CA00496",
    "P00009423": "S6CA00584",
    "P00009621": "S2MA00170",
    "P00009795": "S4NJ00185",
    "P00009910": "H4CA33085",
    "P00011833": "S0CO00211",
    "P00011999": "S0MT00173",
    "P00012047": "S6MD03664",
    "P00012567": "S0PA00434",
    "P00013003": "H8MO02158",
    "P00013276": "H0IL08090",
    "P00013425": "S4CA00332",
    "P00013649": "H4SC01073",
    "P20000329": "S6AZ00191",
    "P20002721": "S4PA00063",
    "P20002762": "S4OR00172",
    "P20002978": "H6MN06074",
    "P20003364": "H0OK04133",
    "P20004537": "H4NY10098",
    "P20004875": "S8TN00410",
    "P40002057": "P00007674",
    "P40002859": "H8MN05148",
    "P40003279": "S6CA00543",
    "P60007119": "H8CO04109",
    "P60007176": "H8FL26062",
    "P60007697": "S0SC00149",
    "P60008281": "S8CA00507",
    "P60012143": "P80000722",
    "P60017563": "S8MO00145",
    "P60018165": "H2AK01091",
    "P60021136": "H2WY00133",
    "P80002983": "S0IL00188",
    "P80003239": "S4MN00023",
    "P80003288": "S6KS00122",
    "P80003379": "S8VA00255",
    "P80003395": "H2CA42023",
    "P80003429": "H8CO06054",
    "P80003635": "H6IL08139",
    "P80004021": "S2MN00159",
    "P80004641": "S8TX00236",
    "P80004872": "H4TX35017",
    "P80005044": "S2OK00095",
    "P80005390": "S6WV00139",
    "P80006117": "S6MN00267",
}
IOWA = {
    2004: date(2004, 1, 19),
    2008: date(2008, 1, 3),
    2012: date(2012, 1, 3),
    2016: date(2016, 2, 1),
    2020: date(2020, 2, 3),
}
DELEGATE_SEATS = {
    "DC": "Delegate from the District of Columbia",
    "PR": "Resident Commissioner of Puerto Rico",
    "GU": "Delegate from Guam",
    "VI": "Delegate from the Virgin Islands",
    "AS": "Delegate from American Samoa",
    "MP": "Delegate from the Northern Mariana Islands",
}
DELEGATES = {(2020, "democratic"): 3979}
UNAFFILIATED = (
    "California lets voters who declined to state a party ask for this party's ballot. The totals here include their "
    "votes, which the FEC reports on separate lines."
)
PARTY_RUN = [
    {"year": 2004, "state": "SC", "code": "D"},
    {"year": 2020, "state": "AK", "code": "D"},
    {"year": 2020, "state": "KS", "code": "D"},
]
WITHDREW = {(2002, "H8OK04037"), (2020, "H0TX02080"), (2022, "H2AK01174"), (2022, "H8TX03123")}
MISTOTALED = {(2006, "WA", "senate", None)}
STRAYED = {(2020, "WI", "02", "W(R)", "131"): "01"}
RUNOFFS = {
    (1992, "GA"): date(1992, 11, 24),
    (1994, "VI"): date(1994, 11, 22),
    (1996, "AS"): date(1996, 11, 19),
    (1996, "LA"): date(1996, 11, 5),
    (1996, "TX"): date(1996, 12, 10),
    (1996, "VI"): date(1996, 11, 19),
    (2000, "AS"): date(2000, 11, 21),
    (2002, "AS"): date(2002, 11, 19),
    (2002, "LA"): date(2002, 12, 7),
    (2004, "LA"): date(2004, 12, 4),
    (2006, "LA"): date(2006, 12, 9),
    (2006, "TX"): date(2006, 12, 12),
    (2008, "GA"): date(2008, 12, 2),
    (2012, "LA"): date(2012, 12, 8),
    (2014, "LA"): date(2014, 12, 6),
    (2016, "LA"): date(2016, 12, 10),
    (2018, "MS"): date(2018, 11, 27),
    (2020, "GA"): date(2021, 1, 5),
    (2020, "GU"): date(2020, 11, 17),
    (2020, "LA"): date(2020, 12, 5),
    (2022, "GA"): date(2022, 12, 6),
}
STARRED = {(1994, "FL")}
POSTPONED = {
    (1982, "GA", 4): date(1982, 11, 30),
    (1982, "GA", 5): date(1982, 11, 30),
    (2008, "LA", 2): date(2008, 12, 6),
    (2008, "LA", 4): date(2008, 12, 6),
}
COURT_ORDERED = {(2006, "TX", district) for district in (15, 21, 23, 25, 28)} | {
    (1996, "TX", district) for district in (3, 5, 6, 7, 8, 9, 18, 22, 24, 25, 26, 29, 30)
}
SEATS = {
    (1986, "NC"): 3,
    (1990, "HI"): 1,
    (1990, "IN"): 3,
    (1992, "CA"): 1,
    (1994, "OK"): 2,
    (1994, "TN"): 2,
    (1996, "KS"): 3,
    (2000, "GA"): 3,
    (2002, "MO"): 1,
    (2008, "MS"): 1,
    (2008, "WY"): 1,
    (2010, "DE"): 2,
    (2010, "IL"): 3,
    (2010, "NY"): 1,
    (2010, "WV"): 1,
    (2014, "HI"): 3,
    (2014, "OK"): 3,
    (2014, "SC"): 3,
    (2018, "MN"): 2,
    (2018, "MS"): 2,
    (2020, "AZ"): 3,
    (2020, "GA"): 3,
    (2022, "CA"): 3,
    (2022, "OK"): 2,
    (2024, "NE"): 2,
}
SYSTEMS = {
    "winner_take_all": ({"formula": "winner_take_all"}, "Winner take all"),
    "fptp": ({"formula": "fptp"}, "First past the post"),
    "two_round": ({"formula": "two_round", "majority": 0.5, "advance": 2}, "Two-round majority"),
    "two_round_45": ({"formula": "two_round", "majority": 0.45, "advance": 2}, "Two-round with a 45 percent threshold"),
    "top_two": ({"formula": "top_two", "advance": 2}, "Top-two primary"),
    "irv": ({"formula": "irv"}, "Ranked-choice voting"),
    "two_round_40": ({"formula": "two_round", "majority": 0.4, "advance": 2}, "Two-round with a 40 percent threshold"),
    "two_round_35": ({"formula": "two_round", "majority": 0.35, "advance": 2}, "Two-round with a 35 percent threshold"),
    "two_round_30": ({"formula": "two_round", "majority": 0.3, "advance": 2}, "Two-round with a 30 percent threshold"),
    "top_four": ({"formula": "top_two", "advance": 4}, "Top-four primary"),
}
RANKED = (
    "This page shows each round of Alaska's ranked-choice count. The FEC's table also lists first-choice totals from "
    "the election summary. Those totals include write-in votes, and they differ slightly from the first round of the "
    "count. We left them out."
)
NOTES = {
    "1982-11-30:GA:house:4": (
        "context",
        (
            "Redistricting litigation delayed this election. Georgia held it as a special general election on "
            "November 30, 1982, instead of on November 2."
        ),
    ),
    "1982-11-30:GA:house:5": (
        "context",
        (
            "Redistricting litigation delayed this election. Georgia held it as a special general election on "
            "November 30, 1982, instead of on November 2."
        ),
    ),
    "1984-11-06:OH:house:17": (
        "caveat",
        (
            "The FEC's book prints 48,935 votes for Lyle Williams, which doesn't fit its own total of 230,661. That "
            "total implies 105,449, the figure the Clerk of the House also reports, so this page shows 105,449."
        ),
    ),
    "1992-11-03:NY:house:17:special": (
        "context",
        (
            "This special election filled the rest of Ted Weiss's term and used the boundaries of the old 17th "
            "district. After redistricting, that area became the 8th district."
        ),
    ),
    "2018-11-06:NC:house:9": (
        "context",
        (
            "North Carolina's State Board of Elections never certified this result. After finding absentee-ballot fraud, it "
            "ordered a new election, held in 2019."
        ),
    ),
    "2010-11-02:AK:senate": (
        "caveat",
        (
            "The FEC's table lists 2,035 write-in ballots for Lisa Murkowski that were challenged and not counted. The "
            "FEC's state total of 257,538 includes them. We left them out, so the counted votes add up to 255,503."
        ),
    ),
    "2018-11-06:ME:house:2": (
        "caveat",
        (
            "These are the FEC's figures, which report first-choice votes only. Maine counted this race by ranked-choice "
            "voting, and no candidate won a majority of first choices; the Secretary of State's official rounds, in "
            "which Jared Golden overtook Bruce Poliquin, are the results shown by default."
        ),
    ),
    "2022-11-08:AK:senate": ("methodology", RANKED),
    "2022-11-08:AK:house:0": ("methodology", RANKED),
    "2024-11-05:AK:house:0": (
        "methodology",
        (
            "Alaska used ranked-choice voting for this race. The Clerk's statistics report first-choice votes only, "
            "so later rounds are not shown here. Nick Begich led the first round and won the race."
        ),
    ),
    "2024-11-05:ME:house:2": (
        "methodology",
        (
            "These are the Clerk's figures, which report only the second and final round of Maine's ranked-choice "
            "count, after write-in votes were set aside: 391,596 continuing ballots and 11,340 exhausted ballots. The "
            "Secretary of State's official rounds, with first choices, are the results shown by default."
        ),
    ),
}
RANKED_ELSEWHERE = {
    "2018-06-12:ME:house:2:democratic",
    "2018-11-06:ME:house:2",
    "2020-07-14:ME:house:2:republican",
    "2024-11-05:ME:house:2",
}
PRINTED_TOTAL = (
    "The FEC's book prints a total of {printed} votes, but the candidates' counts add up to {sum}. This page uses the "
    "sum."
)
CLERK_COUNTS = (
    "The Clerk of the House reports higher counts for this race than the FEC's printed book does. Here are the counts "
    "from the Clerk: {counts}. This page shows the counts as the FEC's book prints them."
)
OPEN_PRIMARY = (
    "Louisiana held an open primary on {date}, with every candidate on one ballot regardless of party. A candidate "
    "who won a majority, or who had no opponent, was elected without a November vote."
)
OPEN_RUNOFF = (
    "No candidate won a majority in Louisiana's open primary on {date}, so the top two met again on {november}. The "
    "FEC's book reports only this second round."
)
CONVENTION = (
    "No candidate reached 35 percent of the primary vote, so under Iowa law a party convention chooses the nominee. "
    "This page does not list a winner for the primary."
)
DISTRICT_NOTE = (
    "Maine and Nebraska give two electors to the statewide winner and one to the winner of each congressional "
    "district. The FEC tables don't report votes by district, so this page records only which ticket won each "
    "district's elector."
)
ANNULLED_PRIMARY = (
    "A federal court redrew this district after the primary and set its results aside. The candidates then ran on "
    "one ballot on {november}. Where no one won a majority, the top two met in a runoff on {runoff}."
)
BLANKET_PRIMARY = (
    "{state} held a blanket primary. Every candidate appeared on one ballot, and voters could pick any candidate, "
    "regardless of party. The candidate with the most votes in each party became that party's nominee."
)


@dataclass
class Cell:
    page: int
    box: scan.Box
    texts: list[str] = field(default_factory=list)


@dataclass
class Entry:
    names: dict[str, list[str]]
    votes: Cell
    share: Cell
    unopposed: bool
    layout: tuple[float, float, float, float, float, float, float] | None


@dataclass
class Race:
    year: int
    chamber: str
    state: str
    district: int | None
    page: int
    special: bool
    runoff: bool
    gap: tuple[int, int]
    entries: list[Entry] = field(default_factory=list)
    total: Cell | None = None
    printed: str = ""
    status: str = ""
    values: list[int] = field(default_factory=list)
    derived: int | None = None
    sole: bool = False
    misprinted: int | None = None


@dataclass
class Line:
    year: int
    page: int
    edges: list[int]
    cells: dict[tuple[str, str], tuple[str, scan.Box]]
    name: str = ""
    naming: scan.Box | None = None
    party: str = ""
    marking: scan.Box = (0, 0, 0, 0)
    incumbent: bool = False
    combined: bool = False
    tacit: bool = False
    unopposed: bool = False
    span: tuple[int, int] = (0, 0)
    fusion: bool = False
    meaning: tuple[str | None, bool, tuple[str, ...]] = (None, False, ())
    scope: str | None = None
    values: dict[str, tuple[int, int]] = field(default_factory=dict)
    totals: dict[str, tuple[int, int, int | None, scan.Box | None]] = field(default_factory=dict)


@dataclass
class Contest:
    year: int
    chamber: str
    state: str
    district: int | None
    term: str
    page: int
    dates: dict[str, str]
    addendum: bool
    lines: list[Line] = field(default_factory=list)
    parties: list[tuple[int, Line]] = field(default_factory=list)
    total: Line | None = None
    layout: dict[str, str] = field(default_factory=dict)


def count(text: str | None) -> int | None:
    return (
        int(float(text.strip("[]").replace(",", ""))) if text and re.fullmatch(r"\[?[\d,]+(\.0+)?\]?", text) else None
    )


def fetch() -> None:
    for path in (*FILES.values(), *BOOKS.values()):
        try:
            raw.cached(SOURCE, DOCUMENTS + path)
        except LookupError:
            raw.fetch(SOURCE, DOCUMENTS + path, suffix="." + path.rsplit(".", 1)[1])
            time.sleep(1)
    for url in (*MASTERS.values(), *CLERK.values(), STATISTICS, *(url for url, *_ in CANVASSES.values())):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
            time.sleep(1)


def parse() -> None:
    candidates: list[tuple[int, str, str | None, str, str, list[str], int, bool]] = []
    totals: dict[tuple[int, str], int] = {}
    lines: list[tuple[object, ...]] = []
    counted: dict[tuple[int, str, str, int | None, str | None], list[tuple[int | None, ...]]] = {}
    dropped: dict[tuple[int, str, str, int | None, str | None], int] = {}
    primaries: list[tuple[object, ...]] = []
    unbalanced: list[tuple[tuple[int, str, str, int | None, str | None], int | None, int | None, list[int]]] = []
    subtotals: dict[tuple[object, ...], list[int]] = {}
    places = {"D.C.": "DC"}
    calendars: list[tuple[int, str, str | None, str | None]] = []
    nominating: list[tuple[object, ...]] = []
    nominating_totals: dict[tuple[int, str], int] = {}
    nominating_summary: dict[tuple[int, str], int] = {}
    for year, path in FILES.items():
        reader = fastexcel.read_excel(raw.cached(SOURCE, DOCUMENTS + path).path)
        if year == 2024:
            frame = reader.load_sheet(0, header_row=None, dtypes="string").to_polars()
            header = [" ".join((cell or "").split()) for cell in frame.row(0)]
            for state, *cells in frame.slice(1).iter_rows():
                if state is None or len(state) != 2:
                    continue
                totals[(year, state)] = int(cells[-1])
                for column, cell in zip(header[4:-1], cells[3:-1], strict=True):
                    if cell is not None:
                        name, label = WIDE[column]
                        candidates.append(
                            (year, state, None, name, name, [] if label is None else [label], int(cell), False)
                        )
            continue

        sheets = reader.sheet_names
        labels = LABELS | {
            code.strip(): " ".join(label.split())
            for code, separator, label in reader.load_sheet_by_name(
                next(sheet for sheet in sheets if "party labels" in sheet.lower()), header_row=None, dtypes="string"
            )
            .to_polars()
            .iter_rows()
            if code is not None and label is not None and separator in ("=", None)
        }
        calendar = reader.load_sheet_by_name(
            next(sheet for sheet in sheets if "primary dates" in sheet.lower()), header_row=None, dtypes="string"
        ).to_polars()
        start = next(
            index
            for index, cells in enumerate(calendar.iter_rows())
            if any(cell and "PRIMARY" in cell.upper() for cell in cells)
            and any(cell and cell.strip().upper() == "STATE" for cell in cells)
        )
        heading = [(cell or "").strip().upper() for cell in calendar.row(start)]
        column = max(index for index, cell in enumerate(heading) if cell == "STATE")
        opening = next(index for index in range(column, len(heading)) if "PRIMARY" in heading[index])
        closing = next(index for index in range(column, len(heading)) if "RUNOFF" in heading[index])
        calendars.extend(
            (year, cells[column], cells[opening], cells[closing])
            for cells in calendar.slice(start + 1).iter_rows()
            if cells[column] is not None
        )
        for sheet in sheets:
            if not (("house" in sheet.lower() and "senate" in sheet.lower()) or "results by state" in sheet.lower()):
                continue
            frame = reader.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
            fields = [
                next(
                    (
                        field.format(*match.groups())
                        for pattern, field in COLUMNS
                        if (match := re.fullmatch(pattern, " ".join(str(cell).upper().split())))
                    ),
                    None,
                )
                for cell in frame.row(0)
            ]
            district, previous, block = None, None, [0, 0]
            pending: list[tuple[int, int]] = []
            for cells in frame.slice(1).iter_rows():
                row = {
                    field: " ".join(cell.split()) or None
                    for field, cell in zip(fields, cells, strict=True)
                    if field is not None and cell is not None
                }
                state, label, party = (
                    row.get("state") or row.get("named"),
                    row.get("label") or "",
                    row.get("party") or "",
                )
                if state is None or len(state) != 2:
                    continue
                if len(named := row.get("named") or "") > 2:
                    places[named.upper()] = state
                code = row.get("district") or (
                    district if state == previous and ("District" in label or not label) else None
                )
                code = STRAYED.get((year, state, code, party, row.get("primary")), code)
                if state != previous:
                    block = [0, 0]
                district, previous = code, state
                if code is None or code.startswith("H") or "Write-Ins" in label or "Combined" in party:
                    continue
                chamber = "senate" if code.upper().startswith("S") else "house"
                key = (
                    year,
                    state,
                    chamber,
                    None if chamber == "senate" else int(re.sub(r"\D.*", "", code)),
                    "unexpired"
                    if "UN" in code.upper()
                    or "*" in code
                    or "Unexpired" in label
                    or ((year, state) in SPECIALS and code == "S")
                    else "full"
                    if "FULL" in code.upper() or "Full" in label or not label
                    else None,
                )
                general, runoff = count(row.get("general")), count(row.get("runoff"))
                rounds = [count(row.get(f"rcv{number}")) for number in (1, 2, 3)]
                primary, second = count(row.get("primary")), count(row.get("primary_runoff"))
                first = re.split(r"/| and ", re.sub(r"[*#]", "", party), flags=re.IGNORECASE)[0].strip()
                wrapped = re.fullmatch(r"([WN]) ?\(([^)]+)\)?", first)
                affiliation = labels.get(wrapped[2] if wrapped else re.sub(r" ?\(.*", "", first))
                replacement = key[:4] in REPLACEMENTS and ("*" in code or row.get("incumbent") == "SPECIAL PRIMARY")
                if label:
                    if "Party" not in label:
                        if general is not None or runoff is not None:
                            counted.setdefault(key, []).append((general, runoff, *rounds))
                        if (
                            (primary, second) != (None, None)
                            and block != [0, 0]
                            and [primary or 0, second or 0] != block
                        ):
                            unbalanced.append((key, primary, second, block))
                        block = [0, 0]
                    elif "Caucus" in label:
                        block = [
                            block[0] - sum(votes for votes, _ in pending),
                            block[1] - sum(votes for _, votes in pending),
                        ]
                        del primaries[len(primaries) - len(pending) :]
                    elif "*" in (row.get("primary") or ""):
                        block[0] += count((row.get("primary") or "").strip("*")) or 0
                    elif primary is not None and first and year < BLANKET.get(state, 9999):
                        subtotals.setdefault(
                            (*key[:4], "full" if replacement else key[4] or "full", affiliation), []
                        ).append(primary)
                    pending = []
                    continue
                unopposed = row.get("general") == "Unopposed"
                tacit = (row.get("primary") or "").startswith("Unopposed")
                if general is None and runoff is None and not unopposed and primary is None and not tacit:
                    continue
                fec_id = None if row.get("fec_id") in (None, "n/a") else row["fec_id"]
                given, _, suffix = re.sub(
                    r',?("?),? (Jr\.|Sr\.|II|III|IV),?$', r"\1, \2", row.get("first") or ""
                ).partition(", ")
                last = re.sub(r"\s*[*#]+$", "", row.get("last") or "")
                name = MISNAMED.get((year, fec_id or "")) or (
                    re.sub(
                        r",( Jr\.| Sr\.| II| III| IV)$", r"\1", " ".join(part for part in (given, last, suffix) if part)
                    )
                    or "Scattered"
                )
                if primary is not None or tacit:
                    held = (
                        REPLACEMENTS[key[:4]]
                        if replacement
                        else SPECIAL_PRIMARIES.get(key[:4])
                        if key[4] == "unexpired"
                        else None
                    )
                    if not first and primaries and primaries[-1][:5] == key:
                        affiliation = primaries[-1][7]
                    if affiliation is None:
                        raise ValueError(f"{year} {state} {code}: no party label for {party}")
                    primaries.append(
                        (
                            *key[:4],
                            "full" if replacement else key[4],
                            fec_id,
                            re.sub(r" ?\(Opportunity to Ballot\)", "", name),
                            affiliation,
                            bool(wrapped and wrapped[1] == "W") or "Opportunity to Ballot" in name,
                            (row.get("incumbent") or "").startswith("(I)"),
                            primary,
                            tacit,
                            second,
                            row.get("nominee"),
                            held,
                        )
                    )
                    pending.append((primary or 0, second or 0))
                    block = [block[0] + (primary or 0), block[1] + (second or 0)]
                if general is None and runoff is None and not unopposed:
                    continue
                if (year, state, party) in EXCLUDED:
                    dropped[key] = dropped.get(key, 0) + (general or 0)
                    continue
                if (year, fec_id) in STRAY:
                    continue
                tokens = [token.strip() for token in re.sub(r"[*#]", "", party).split("/") if token.strip()]
                if len(tokens) > 1 and (tokens[-1] == "W" or re.fullmatch(r"[WN] ?\(.*\)", tokens[0])):
                    tokens = tokens[-1:] if tokens[-1] == "W" else tokens[1:]
                if (switch := SWITCHES.get((year, fec_id or ""))) is not None:
                    tokens = switch.split("/")
                if (pair := SWAPPED.get((year, fec_id or ""))) is not None:
                    if tokens not in ([pair[0]], [pair[1]]):
                        raise ValueError(f"{year} {fec_id}: no {pair[0]} or {pair[1]} line to swap in {tokens}")
                    tokens = [pair[1] if tokens == [pair[0]] else pair[0]]
                lines.append(
                    (
                        *key,
                        fec_id,
                        name,
                        [] if name == NOTA else [labels[re.sub(r" ?\(.*\)", "", token)] for token in tokens],
                        (row.get("incumbent") or "").startswith("(I)"),
                        general,
                        unopposed,
                        runoff,
                        count(row.get("combined")),
                        *rounds,
                        row.get("winner"),
                        "[" in (row.get("general") or ""),
                    )
                )
        sheet = next((sheet for sheet in sheets if "pres primary results" in sheet.lower()), None)
        if sheet is not None:
            frame = reader.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
            header = next(index for index, row in enumerate(frame.iter_rows()) if "FEC ID" in row)
            frame.columns = [
                str(cell).strip() if cell is not None else f"column{index}"
                for index, cell in enumerate(frame.row(header))
            ]
            block, final = 0, False
            for row in frame.slice(header + 1).iter_rows(named=True):
                state, code = row["STATE ABBREVIATION"], (row["PARTY"] or "").strip()
                final = final or (row["FIRST NAME"] or "").startswith("Final Round")
                label, value = (row["TOTAL VOTES"] or "").strip(), row["PRIMARY RESULTS"]
                if state is None or len(state) != 2 or row["LAST NAME"] == "Blank, Void, Scattering":
                    continue
                if label.startswith("Total"):
                    if label.startswith("Total Party") and count(value) != block:
                        raise ValueError(f"{year} {state} {code}: party total {value}, lines {block}")
                    if not label.startswith("Total Party"):
                        nominating_totals[(year, state)] = count(value) or 0
                        final = False
                    block = 0
                    continue
                if not code:
                    continue
                tacit = value == "Unopposed"
                votes = count(value)
                if votes is None and not tacit:
                    raise ValueError(f"{year} {state} {code}: no votes for {row['LAST NAME']}")
                block += votes or 0
                if final:
                    continue
                wrapped = re.fullmatch(r"(W|UN)\((\w+)\)", code)
                given, _, suffix = (row["FIRST NAME"] or "").partition(", ")
                name = " ".join(
                    " ".join(
                        part for part in (re.sub(r'^"(.*)"$', r"\1", given), row["LAST NAME"], suffix) if part
                    ).split()
                )
                nominating.append(
                    (
                        year,
                        state,
                        date.fromisoformat((row["PRIMARY DATE"] or "")[:10]),
                        labels[wrapped[2] if wrapped else code],
                        "UN" if wrapped and wrapped[1] == "UN" else wrapped[2] if wrapped else code,
                        SURNAMES.get(name) if row["FEC ID"] in (None, "n/a") else row["FEC ID"],
                        name,
                        bool(wrapped and wrapped[1] == "W"),
                        votes,
                        tacit,
                    )
                )
            summary = reader.load_sheet_by_name(
                next(sheet for sheet in sheets if "primary party summary" in sheet.lower()),
                header_row=None,
                dtypes="string",
            ).to_polars()
            for text, value in summary.iter_rows():
                if text is not None and (match := re.search(r"\((\w+)\)$", text.strip())):
                    nominating_summary[(year, SUMMARIZED.get(match[1]) or match[1])] = int(value)
        sheet = next((sheet for sheet in sheets if "pres general results" in sheet.lower()), None)
        if sheet is None:
            continue
        frame = reader.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
        header = next(index for index, row in enumerate(frame.iter_rows()) if "FEC ID" in row)
        frame.columns = [
            str(cell).strip() if cell is not None else f"column{index}" for index, cell in enumerate(frame.row(header))
        ]
        for row in frame.slice(header + 1).iter_rows(named=True):
            state = row["STATE ABBREVIATION"]
            if state is not None and "Total State Votes" in (row["TOTAL VOTES"] or ""):
                totals[(year, state)] = int(float(row["TOTAL VOTES #"]))
                continue
            if (
                state is None
                or row["GENERAL RESULTS"] is None
                or "Combined Parties:" in (row["PARTY"], row["TOTAL VOTES"])
            ):
                continue
            first, mate = row["FIRST NAME"] or "", None
            if "/" in first:
                first, mate = (part.strip() for part in first.split("/", 1))
                mate_last, _, mate_first = mate.partition(", ")
                mate = f"{mate_first} {mate_last}" if mate_first else mate_last
            given, _, suffix = first.partition(", ")
            name = " ".join(" ".join(part for part in (given, row["LAST NAME"], suffix) if part).split())
            if (year, state, name) in DUPLICATES:
                continue
            candidates.append(
                (
                    year,
                    state,
                    None if row["FEC ID"] in (None, "n/a") else row["FEC ID"],
                    name,
                    name if mate is None else f"{name} / {mate}",
                    [] if row["PARTY"] in (None, "None", "X") else [labels[code] for code in row["PARTY"].split("/")],
                    int(float(row["GENERAL RESULTS"].strip("[]").replace(",", ""))),
                    (row.get("WINNER INDICATOR") or "").startswith("W"),
                )
            )

    frame = pl.DataFrame(
        candidates,
        schema={
            "year": pl.Int64,
            "state": pl.String,
            "fec_id": pl.String,
            "name": pl.String,
            "ballot": pl.String,
            "parties": pl.List(pl.String),
            "votes": pl.Int64,
            "is_winner": pl.Boolean,
        },
        orient="row",
    )
    sums = frame.group_by("year", "state").agg(pl.col("votes").sum())
    mismatched = [
        (year, state, votes, totals.get((year, state)))
        for year, state, votes in sums.iter_rows()
        if totals.get((year, state)) != votes
    ]
    if mismatched or len(totals) != sums.height:
        raise ValueError(f"state totals differ: {sorted(mismatched)}; {len(totals)} totals for {sums.height} states")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "president.parquet")

    nominations = pl.DataFrame(
        nominating,
        schema={
            "year": pl.Int64,
            "state": pl.String,
            "day": pl.Date,
            "party": pl.String,
            "code": pl.String,
            "fec_id": pl.String,
            "name": pl.String,
            "write_in": pl.Boolean,
            "votes": pl.Int64,
            "unopposed": pl.Boolean,
        },
        orient="row",
    )
    sums = nominations.group_by("year", "state").agg(pl.col("votes").sum())
    mismatched = [
        (year, state, votes, nominating_totals.get((year, state)))
        for year, state, votes in sums.iter_rows()
        if nominating_totals.get((year, state)) != votes
    ]
    if mismatched or len(nominating_totals) != sums.height:
        raise ValueError(f"presidential primary state totals differ: {sorted(mismatched)}")
    coded: dict[tuple[int, str], int] = {}
    for year, state, code, votes in nominations.select("year", "state", "code", "votes").iter_rows():
        key = (year, SUMMARIZED.get((year, state, code)) or SUMMARIZED.get(code) or code)
        coded[key] = coded.get(key, 0) + (votes or 0)
    coded = {key: votes for key, votes in coded.items() if votes}
    if coded != nominating_summary:
        raise ValueError(
            f"presidential primary party totals differ: {sorted(set(coded.items()) ^ set(nominating_summary.items()))}"
        )
    nominations.filter(pl.col("state") != "PR", ~pl.struct("year", "state", "code").is_in(PARTY_RUN)).write_parquet(
        PARSED / "nominations.parquet"
    )

    congress = pl.DataFrame(
        lines,
        schema={
            "year": pl.Int64,
            "state": pl.String,
            "chamber": pl.String,
            "district": pl.Int64,
            "term": pl.String,
            "fec_id": pl.String,
            "name": pl.String,
            "parties": pl.List(pl.String),
            "is_incumbent": pl.Boolean,
            "votes": pl.Int64,
            "unopposed": pl.Boolean,
            "runoff": pl.Int64,
            "combined": pl.Int64,
            "rcv1": pl.Int64,
            "rcv2": pl.Int64,
            "rcv3": pl.Int64,
            "flag": pl.String,
            "bracketed": pl.Boolean,
        },
        orient="row",
    )
    contests = ("year", "state", "chamber", "district", "term")
    congress = (
        congress.with_columns(
            rollup=pl.col("fec_id").is_not_null()
            & (pl.len().over(*contests, "fec_id") > 1)
            & (
                (pl.col("parties").list.len() == 0)
                | (~pl.col("bracketed") & pl.col("bracketed").any().over(*contests, "fec_id"))
            )
        )
        .with_columns(
            combined=pl.coalesce("combined", pl.col("votes").filter("rollup").first().over(*contests, "fec_id"))
        )
        .filter(~pl.col("rollup"))
        .with_columns(
            words=pl.col("name").map_elements(
                lambda text: [word for word in slug(text).split("-") if len(word) > 2], return_dtype=pl.List(pl.String)
            ),
            weight=pl.col("votes").fill_null(0).sum().over("fec_id", "name"),
        )
        .with_columns(home=pl.col("words").sort_by("weight", descending=True).first().over("fec_id"))
        .with_columns(
            fec_id=pl.when(
                (pl.col("words").list.set_intersection("home").list.len() > 0)
                & ~pl.concat_str(pl.col("year").cast(pl.String), "fec_id", separator=":").is_in(UNLINKED)
            ).then("fec_id")
        )
        .drop("rollup", "bracketed", "words", "weight", "home")
    )
    primary = (
        pl.DataFrame(
            primaries,
            schema={
                "year": pl.Int64,
                "state": pl.String,
                "chamber": pl.String,
                "district": pl.Int64,
                "term": pl.String,
                "fec_id": pl.String,
                "name": pl.String,
                "party": pl.String,
                "write_in": pl.Boolean,
                "is_incumbent": pl.Boolean,
                "votes": pl.Int64,
                "unopposed": pl.Boolean,
                "runoff": pl.Int64,
                "flag": pl.String,
                "held": pl.Date,
            },
            orient="row",
        )
        .with_columns(
            words=pl.col("name").map_elements(
                lambda text: [word for word in slug(text).split("-") if len(word) > 2], return_dtype=pl.List(pl.String)
            ),
            weight=pl.col("votes").fill_null(0).sum().over("fec_id", "name"),
        )
        .with_columns(home=pl.col("words").sort_by("weight", descending=True).first().over("fec_id"))
        .with_columns(
            fec_id=pl.when(
                (pl.col("words").list.set_intersection("home").list.len() > 0)
                & ~pl.concat_str(pl.col("year").cast(pl.String), "fec_id", separator=":").is_in(UNLINKED)
            ).then("fec_id")
        )
        .drop("words", "weight", "home")
    )
    sums: dict[tuple[int, str, str, int | None, str | None], tuple[int | None, ...]] = {
        key: tuple(None if value == 0 else value for value in values)
        for key, values in (
            (tuple(row[:5]), row[5:])
            for row in congress.group_by(contests).agg(pl.col("votes", "runoff", "rcv1", "rcv2", "rcv3").sum()).rows()
        )
    }
    coded = {tuple(row[:6]): row[6] for row in primary.group_by(*contests, "party").agg(pl.col("votes").sum()).rows()}
    unequal = [(key, values, coded.get(key)) for key, values in subtotals.items() if sum(values) != coded.get(key)]
    mismatched = []
    for (year, state, chamber, district, term), reported in counted.items():
        options = [
            [part for other in terms if (part := (year, state, chamber, district, other)) in sums]
            for terms in ([term or "full"], ["full", "unexpired"])
        ]
        for values in reported:
            if not any(
                parts
                and all(
                    value is None
                    or (index == 1 and (year, state, chamber, district) in MISFILED)
                    or value
                    == sum(sums[part][index] or 0 for part in parts)
                    + sum(dropped.get(part, 0) for part in parts if index == 0)
                    for index, value in enumerate(values)
                )
                for parts in options
            ):
                mismatched.append(((year, state, chamber, district, term), values))
    combined = (
        congress.filter(pl.col("fec_id").is_not_null())
        .group_by(*contests, "fec_id")
        .agg(pl.col("votes").sum(), pl.col("combined").max())
        .filter(pl.col("combined").is_not_null() & (pl.col("combined") != pl.col("votes")))
        .filter(~pl.concat_str(pl.col("year").cast(pl.String), "fec_id", separator=":").is_in(TYPOS))
    )
    unbalanced = [line for line in unbalanced if line[0][:4] not in MISTOTALED]
    if mismatched or combined.height or unequal or unbalanced:
        raise ValueError(
            f"congressional totals differ: {mismatched}; combined lines differ: {combined.rows()}; "
            f"party primary totals differ: {unequal}; primary totals differ: {unbalanced}"
        )
    congress.write_parquet(PARSED / "congress.parquet")
    primary.write_parquet(PARSED / "primaries.parquet")
    workbook_ids = set(congress["fec_id"].drop_nulls()) | set(primary["fec_id"].drop_nulls())

    schedule: list[tuple[int, str, str | None, date | None, date | None]] = []
    for year, place, *cells in calendars:
        days: list[date | None] = []
        for cell in cells:
            if cell and (match := re.match(r"(\d{4})-(\d\d)-(\d\d)", cell)):
                stamped, month, day = map(int, match.groups())
                days.append(
                    date(
                        year if stamped > year or stamped < 2000 else stamped,
                        month,
                        stamped % 100 // 10 if stamped < 2000 else day,
                    )
                )
            elif cell and (match := re.match(r"(\d{1,2})/(\d{1,3})", cell)):
                day = int(match[2])
                days.append(date(year, int(match[1]), day // 10 if day > 31 else day))
            else:
                days.append(None)
        if days[0] is None:
            continue
        if days[1] is None:
            days[1] = PRIMARY_RUNOFFS.get((year, places.get(" ".join(place.split()).upper(), "")))
        if (state := places.get(" ".join(place.split()).upper())) is None:
            raise ValueError(f"{year}: no state is named {place}")
        schedule.append(
            (
                year,
                state,
                "senate" if "Senate" in (cells[0] or "") else "house" if "House" in (cells[0] or "") else None,
                days[0],
                days[1],
            )
        )
    dates = pl.DataFrame(
        schedule,
        schema={"year": pl.Int64, "state": pl.String, "chamber": pl.String, "primary": pl.Date, "runoff": pl.Date},
        orient="row",
    )

    named = {text: code for text, code in places.items() if len(text) > 3}
    statewords = sorted({part for text in named for part in text.split() if len(part) >= 4} | {"CONTINUED"})
    order = list(dict.fromkeys(named[text] for text in sorted(named, key=lambda text: text.replace(" ", ""))))
    full = {
        (held, state)
        for held, state, chamber, term in congress.select("year", "state", "chamber", "term").iter_rows()
        if chamber == "senate" and term == "full"
    }
    classes = {
        number: [code for code in order if (reference, code) in full]
        for number, reference in ((1, 2006), (2, 2002), (3, 2004))
    }
    books = {year: raw.cached(SOURCE, DOCUMENTS + path) for year, path in BOOKS.items()}
    compendium = raw.cached(SOURCE, STATISTICS)

    def digitish(text: str) -> bool:
        core = text.strip('«»()"“”_~-—:;*')
        return re.fullmatch(r"[\d,.'’`lI|\]\[!iOoSsBZz]+", core) is not None and (
            any(character.isdigit() for character in core) or len(core) <= 3
        )

    def state_of(text: str) -> str | None:
        text = re.sub(r"\(.*|continued.*|\{.*", "", text, flags=re.IGNORECASE).strip(" .,:;|-'_~*\"")
        text = re.sub(r"\s+", " ", text).upper()
        found = difflib.get_close_matches(text, list(named), n=1, cutoff=0.75) if len(text) >= 4 else []
        return named[found[0]] if found else None

    def cached(year: int, page: int, requests: list[tuple[str, scan.Box]]) -> list[list[str]]:
        path = PARSED / "ocr" / f"{books[year].sha256}-{page}.json"
        known: dict[str, list[str]] = json.loads(path.read_text()) if path.exists() else {}
        keys = [f"{kind}:{','.join(map(str, box))}" for kind, box in requests]
        missing = [(key, kind, box) for key, (kind, box) in zip(keys, requests, strict=True) if key not in known]
        if missing:
            with tempfile.TemporaryDirectory() as folder:
                image = scan.rendered(books[year].path, page, Path(folder))
                read = iter(
                    scan.read(image, [(box, kind == "digits") for _, kind, box in missing if kind != "gap"], "eng")
                )
                picture = Image.open(image).convert("L")
                for key, kind, box in missing:
                    if kind != "gap":
                        known[key] = next(read)
                        continue
                    buffer = io.BytesIO()
                    picture.crop(box).save(buffer, "PNG")
                    known[key] = [
                        subprocess.run(
                            ["tesseract", "stdin", "stdout", "--psm", psm, "-l", "eng"],
                            input=buffer.getvalue(),
                            capture_output=True,
                            check=True,
                        ).stdout.decode()
                        for psm in ("6", "7", "11")
                    ]
            path.write_text(json.dumps(known))
        return [known[key] for key in keys]

    races: list[Race] = []
    layers: dict[tuple[int, int], list[tuple[str, scan.Box]]] = {}
    for year, pdf in books.items():
        if year > 1992:
            continue
        info = subprocess.run(["pdfinfo", str(pdf.path)], capture_output=True, text=True, check=True).stdout
        section: str | None = None
        printed: str | None = None
        seat: int | None = None
        current: Race | None = None
        awaiting: Race | None = None
        special = runoff = False
        seen: set[str] = set()
        columns: tuple[float, float, float, float, float, float] | None = None
        for page in range(1, int(re.findall(r"Pages:\s+(\d+)", info)[0]) + 1):
            words = scan.words(pdf.path, page, PARSED / "ocr" / f"{pdf.sha256}-{page}.tsv")
            upper = " ".join(w[0] for w in words).upper()
            if "OFFICIAL GENERAL ELECTION" in upper and "UNITED STATES" in upper:
                current, printed, seat = None, None, None
                section = "president" if "PRESIDENT" in upper else "senate" if "SENATE" in upper else "house"
                continue
            slope = scan.skew(words)
            found = scan.lines(words, slope)
            header = next(
                (
                    index
                    for index, line in enumerate(found[:6])
                    if any(re.search(r"P\.?A[A-Za-z'.]{1,3}Y|Party", w[0]) for w in line)
                    and re.search(r"V[o0]t|VOT|V\W?O|ercent|ERCENT|E[RK]C|of|OF", " ".join(w[0] for w in line))
                ),
                None,
            )
            if (
                header is None
                or section is None
                or (section == "president" and "POPULAR" in upper)
                or "GUIDE" in " ".join(w[0] for w in found[header]).upper()
            ):
                continue
            signature = {digits for w in words if len(digits := re.sub(r"\D", "", w[0])) >= 4}
            if seen and signature and len(signature & seen) >= 0.8 * len(signature):
                continue
            seen = signature
            layer = layers[(year, page)] = scan.layer(pdf.path, page)
            head, below = found[header], found[header + 1 :]
            centers = [sum(scan.level(w, slope) for w in line) / len(line) for line in below]
            floor = max(w[1][3] for w in head)
            for extra in scan.lines(
                [(text, box, (0, 0, 0), 100.0) for text, box in layer if box[1] > floor + 10], slope
            ):
                center = sum(scan.level(w, slope) for w in extra) / len(extra)
                if all(abs(center - other) > 22 for other in centers) and any(
                    re.search(r"[A-Za-z]{3}", w[0]) for w in extra
                ):
                    below.append(extra)
            below.sort(key=lambda line: min(scan.level(w, slope) for w in line))
            party_x = next(w[1][0] for w in head if re.search(r"P\.?A[A-Za-z'.]{1,3}Y|Party", w[0]))
            titles = [w for w in head if w[1][2] < party_x]
            right = [w for w in head if w[1][0] > party_x + 50 and len(re.sub(r"[^A-Za-z]", "", w[0])) >= 3]
            shift = party_x - columns[2] if columns else 0
            if titles:
                lasts = [w for w in titles if re.search(r"AST|ast|ANDI|andi", w[0])]
                titles = titles[titles.index(lasts[0]) :] if lasts else titles
                last_x, first_x = titles[0][1][0], titles[2][1][0] if len(titles) >= 3 else titles[-1][1][0]
            elif columns:
                last_x, first_x = columns[0] + shift, columns[1] + shift
            else:
                continue
            if len(right) >= 2:
                votes_x, percent_x, percent_left = right[-2][1][2], right[-1][1][2], right[-1][1][0]
            elif columns:
                votes_x, percent_x, percent_left = columns[3] + shift, columns[4] + shift, columns[5] + shift
            else:
                continue
            starts = sorted(
                opener[1][0]
                for line in below
                if (opener := next((w for w in line if not digitish(w[0]) and not re.fullmatch(r"\W+", w[0])), None))
                is not None
                and opener[1][0] < first_x - 100
                and any(digitish(w[0]) and len(re.sub(r"\D", "", w[0])) >= 3 for w in line)
            )
            if len(starts) >= 3:
                last_x = starts[len(starts) // 2]
            columns = (last_x, first_x, party_x, votes_x, percent_x, percent_left)
            split = (votes_x + percent_left) / 2
            anchors = sorted(
                w[1][0]
                for line in below
                for w in line
                if re.match(r"(Democrat|Republic|Libertar|Wr|Independ|Conservat|Liberal|Dem\.|Ind\.|Socialist)", w[0])
                and first_x + 100 < w[1][0] < votes_x - 300
            )
            party_left = anchors[len(anchors) // 4] - 25 if anchors else party_x - 30
            marked = any(
                len(line) <= 5
                and "COLUMB" not in " ".join(w[0] for w in line).upper()
                and any(
                    difflib.SequenceMatcher(None, w[0].upper().strip(":"), "DISTRICT").ratio() >= 0.75 for w in line
                )
                for line in below
            )
            numbered = sum(
                any(last_x - 200 < w[1][2] < last_x - 8 and re.fullmatch(r"\d{1,2}\W*", w[0]) for w in line)
                for line in below
            )
            prefixed = section == "house" and not marked and numbered >= 2
            waiting: list[scan.Word] = []
            noted = False
            last_bottom = floor
            for line in below:
                line = [
                    w
                    for w in line
                    if last_x - 200 < w[1][2] < percent_x + 150
                    and (
                        w[3] >= 30
                        or digitish(w[0])
                        or (re.search(r"[A-Za-z]{3}", w[0]) and w[1][0] > first_x - 60)
                        or re.search(r"[Tt]ota|Vote|Write|DISTRICT|ombined", w[0])
                        or (
                            len(w[0]) >= 4
                            and difflib.get_close_matches(w[0].upper().strip(".,:;|_"), statewords, n=1, cutoff=0.75)
                        )
                    )
                    and "GOVERNMENT" not in w[0]
                    and (w[1][0] > party_x or not re.fullmatch(r"[|:;.,'\-_~*\"°=«»]{1,3}", w[0]))
                ]
                if not line:
                    continue
                low = min(w[1][1] - slope * ((w[1][0] + w[1][2]) / 2 - 1275) for w in line)
                high = max(w[1][3] - slope * ((w[1][0] + w[1][2]) / 2 - 1275) for w in line)
                top, bottom = round(low + slope * (votes_x - 1375)), round(high + slope * (votes_x - 1375))
                ptop, pbottom = round(low + slope * (percent_x - 1335)), round(high + slope * (percent_x - 1335))
                text = " ".join(w[0] for w in line)
                if bottom - top < 25 or not re.search(r"[A-Za-z0-9]", text):
                    continue
                if re.match(r"\W*RUN-?OFF", text.upper()) and len(text.split()) <= 6:
                    current, runoff, waiting = None, True, []
                    continue
                if re.search(r"SPECIAL|UNEXPIRED|FILL A VACANCY|FILL SEAT", text.upper()) and not re.search(
                    r"held", text, re.IGNORECASE
                ):
                    current, special, waiting = None, True, []
                    if re.search(r"D\W?[I1lL|]\W?S\W?T\W?R", text.upper()) and (
                        number := re.search(r"(\d{1,2})", text)
                    ):
                        seat = int(number[1])
                    continue
                numeric = any(
                    digitish(w[0]) and len(re.sub(r"\D", "", w[0])) >= 2 and votes_x - 420 < w[1][2] < split
                    for w in line
                )
                note = re.match(r"\W*NOTE", text.upper()) is not None
                if (
                    (len(line) > 10 and not numeric)
                    or len(line) > 16
                    or note
                    or (noted and not numeric and len(line) >= 5)
                    or re.search(r"Elections? held|held on|19[0-9][0-9]\)|\b19[89][0-9]\b", text)
                ):
                    waiting, noted = [], noted or note
                    continue
                noted = False
                middle = [w for w in line if w[1][2] > votes_x - 420 and (w[1][0] + w[1][2]) / 2 < split]
                zone = [w for w in middle if digitish(w[0])]
                pzone = [w for w in line if (w[1][0] + w[1][2]) / 2 > split]
                shares = " ".join(w[0] for w in pzone)
                unopposed = re.search(r"nopposed|NOPPOSED", text) is not None
                votes = (min(w[1][0] for w in zone), top, round(votes_x) + 30, bottom) if zone else None
                if awaiting is not None and votes is not None and not re.search(r"[A-Za-z]{2}", text):
                    awaiting.total = Cell(page, votes, [" ".join(w[0] for w in zone)])
                    awaiting = None
                    continue
                awaiting = None
                if re.search(r"T[oa]ta[l1I]\W*V|V[o0]tes?\s*:", text, re.IGNORECASE):
                    if current is not None:
                        held = [w for w in waiting if w[1][0] < party_left]
                        if current.entries and held:
                            y0, y1 = min(w[1][1] for w in waiting) - 4, max(w[1][3] for w in waiting) + 4
                            current.entries.append(
                                Entry(
                                    {
                                        "last": [" ".join(w[0] for w in held if w[1][0] < first_x - 30)],
                                        "first": [" ".join(w[0] for w in held if w[1][0] >= first_x - 30)],
                                        "party": [
                                            " ".join(
                                                w[0] for w in waiting if w[1][0] >= party_left and not digitish(w[0])
                                            )
                                        ],
                                    },
                                    Cell(page, (round(votes_x) - 330, y0, round(votes_x) + 30, y1), [""]),
                                    Cell(page, (round(split), y0, round(percent_x) + 40, y1), [""]),
                                    False,
                                    None,
                                )
                            )
                        if votes is None:
                            awaiting = current
                        current.total = Cell(
                            page,
                            votes or (round(votes_x) - 330, top, round(votes_x) + 30, bottom),
                            [" ".join(w[0] for w in zone)],
                        )
                    current, waiting, last_bottom = None, [], bottom
                    continue
                marker = (
                    None
                    if re.search(r"COLUMB|C0LUMB", text.upper())
                    else next(
                        (
                            index
                            for index, w in enumerate(line)
                            if difflib.SequenceMatcher(None, w[0].upper().strip(":"), "DISTRICT").ratio() >= 0.75
                        ),
                        None,
                    )
                )
                if marker is not None and len(line) <= 4 and not unopposed:
                    current, waiting = None, []
                    digits = re.sub(
                        r"\D", "", " ".join(w[0] for w in line[marker + 1 : marker + 2]).translate(scan.CONFUSABLE)
                    )
                    seat = int(digits[:2] or 0) or None
                    continue
                label = " ".join(w[0] for w in line if not digitish(w[0]) and len(w[0].strip(".,:;|'\"~-")) > 1)
                if (
                    not unopposed
                    and not any(len(re.sub(r"\D", "", w[0])) >= 3 for w in zone)
                    and (found_state := state_of(label)) is not None
                ):
                    if found_state != printed:
                        current, seat = None, None
                    printed, waiting = found_state, []
                    continue
                if not zone and not re.search(r"\d", shares) and not unopposed:
                    if re.search(r"[A-Za-z]{2}", text) and not re.search(r"[Tt]ota|DISTRICT", text):
                        waiting = waiting + line
                    continue
                prefix = sorted(
                    (w for w in waiting + line if last_x - 200 < w[1][2] < last_x - 8), key=lambda w: w[1][0]
                )
                lead = (
                    re.fullmatch(r"(\d{1,2}|[lI]{1,2}|[lI]\d|\d[lI])\W*", prefix[0][0]) if prefixed and prefix else None
                )
                if lead:
                    current = None
                    seat = int(re.sub(r"\D", "", lead[1].translate(scan.CONFUSABLE)))
                line = [w for w in line if w not in prefix]
                waiting = [w for w in waiting if w not in prefix]
                if current is None:
                    current = Race(year, section or "", printed or "", seat, page, special, runoff, (last_bottom, top))
                    races.append(current)
                    special = runoff = False
                last = " ".join(w[0] for w in line if w[1][0] < first_x - 30)
                first = " ".join(w[0] for w in line if first_x - 30 <= w[1][0] < party_left)
                held_last = " ".join(w[0] for w in waiting if w[1][0] < first_x - 30)
                held_first = " ".join(w[0] for w in waiting if first_x - 30 <= w[1][0] < party_left)
                if (held_last or held_first) and (last or first) and current.entries:
                    y0, y1 = min(w[1][1] for w in waiting) - 4, max(w[1][3] for w in waiting) + 4
                    current.entries.append(
                        Entry(
                            {
                                "last": [held_last],
                                "first": [held_first],
                                "party": [" ".join(w[0] for w in waiting if w[1][0] >= party_left)],
                            },
                            Cell(page, (round(votes_x) - 330, y0, round(votes_x) + 30, y1), [""]),
                            Cell(page, (round(split), y0, round(percent_x) + 40, y1), [""]),
                            False,
                            None,
                        )
                    )
                    waiting = []
                elif not (last and first):
                    last, first = last or held_last, first or held_first
                party = " ".join(
                    w[0]
                    for w in waiting + line
                    if w[1][0] >= party_left and w not in zone and w not in pzone and "nopposed" not in w[0]
                )
                layout = (
                    min(scan.level(w, slope) for w in waiting + line),
                    max(scan.level(w, slope) for w in waiting + line),
                    slope,
                    last_x,
                    first_x,
                    party_left,
                    split,
                )
                waiting = []
                if not re.search(r"[A-Za-z]{2}", last + party) or not (zone or unopposed or re.search(r"\d", shares)):
                    continue
                current.entries.append(
                    Entry(
                        {"last": [last], "first": [first], "party": [party]},
                        Cell(
                            page,
                            votes
                            or (
                                min([w[1][0] for w in middle] or [round(votes_x) - 330]),
                                top,
                                round(votes_x) + 30,
                                bottom,
                            ),
                            [" ".join(w[0] for w in zone)],
                        ),
                        Cell(page, (round(split), ptop, round(percent_x) + 40, pbottom), [shares]),
                        unopposed,
                        layout,
                    )
                )
                last_bottom = bottom
            if current is not None and all(entry.unopposed for entry in current.entries):
                current = None

        apportioned = APPORTIONMENT[1992 if year >= 1992 else 1982]
        senate = classes[(3, 1, 2)[(year - 2004) % 6 // 2]]
        mine = [race for race in races if race.year == year and race.entries]
        for race in mine:
            race.printed = race.state
        for index, race in enumerate(mine):
            if race.chamber == "house" and apportioned.get(race.state, 1) == 1:
                race.district = None
            before = mine[index - 1] if index else None
            if (
                before is None
                or before.printed != race.printed
                or before.chamber != race.chamber
                or race.special
                or race.runoff
                or (
                    race.chamber == "house"
                    and race.district is not None
                    and before.district is not None
                    and race.district > before.district
                )
            ):
                continue
            texts = (
                cached(year, race.page, [("gap", (450, max(race.gap[0] - 5, 0), 2100, race.gap[1] + 5))])[0]
                if race.gap[1] - race.gap[0] > 30
                else []
            )
            hits = [
                hit
                for text in texts
                for line in text.splitlines()
                if (hit := state_of(re.sub(r"[^A-Za-z ]", " ", line))) is not None
            ]
            if race.chamber in ("senate", "president"):
                pool = senate if race.chamber == "senate" else [code for code in order if code in {*apportioned, "DC"}]
                taken = {other.state for other in mine if other.chamber == race.chamber and other is not race}
                hit = hits[0] if hits else None
                if hit is None or hit == before.state or hit in taken:
                    hit = (
                        next(
                            (code for code in pool[pool.index(before.state) + 1 :] if code not in taken | {race.state}),
                            None,
                        )
                        if before.state in pool
                        else None
                    )
                if hit is not None:
                    race.state = hit
            elif hits and hits[0] != before.state:
                old = race.state
                for other in mine[index:]:
                    if other.chamber != "house" or other.state != old:
                        break
                    other.state = hits[0]
                    if apportioned.get(hits[0], 1) == 1:
                        other.district = None
        for race in mine:
            if race.chamber == "senate" and race.state not in senate:
                race.special = True
        groups: list[list[Race]] = []
        for race in mine:
            if race.chamber == "house" and groups and groups[-1][0].state == race.state:
                groups[-1].append(race)
            elif race.chamber == "house":
                groups.append([race])
        for group in groups:
            code = group[0].state
            if apportioned.get(code, 1) == 1:
                continue
            regular = [race for race in group if not race.special]
            expected = [number for number in range(1, apportioned[code] + 1) if (year, code, number) not in DECLARED]
            if [race.district for race in regular] == expected:
                continue
            if len(regular) != len(expected):
                raise ValueError(f"{year} {code}: districts {[race.district for race in regular]}, not {expected}")
            for race, number in zip(regular, expected, strict=True):
                race.district = number
        house = {
            (race.state, race.district or 0)
            for race in mine
            if race.chamber == "house" and not race.special and race.state in apportioned
        } | {(code, number) for held, code, number in DECLARED if held == year}
        seats = {
            (code, number) for code, size in apportioned.items() for number in (range(1, size + 1) if size > 1 else [0])
        }
        voted = {race.state for race in mine if race.chamber == "senate" and not race.special}
        chosen = {race.state for race in mine if race.chamber == "president"}
        electing = {*apportioned, "DC"} if year % 4 == 0 else set()
        if house != seats or voted != set(senate) or chosen != electing:
            raise ValueError(
                f"{year}: House seats {sorted(seats ^ house)}, Senate states {sorted(voted ^ set(senate))}, "
                f"or presidential states {sorted(chosen ^ electing)} differ"
            )

    races = [race for race in races if race.entries]
    for race in races:
        for cell in [
            *(cell for entry in race.entries for cell in (entry.votes, entry.share)),
            *([race.total] if race.total else []),
        ]:
            center, reach = (cell.box[1] + cell.box[3]) / 2, (cell.box[3] - cell.box[1]) * 0.4
            cell.texts.append(
                " ".join(
                    text
                    for text, box in sorted(layers[(race.year, cell.page)], key=lambda word: word[1][0])
                    if box[0] >= cell.box[0] - 8
                    and box[2] <= cell.box[2] + 12
                    and abs((box[1] + box[3]) / 2 - center) < reach
                )
            )
        for entry in race.entries:
            if entry.layout is None:
                continue
            low, high, slope, _, first_x, party_left, split = entry.layout
            found = sorted(
                (
                    (text, box, (box[1] + box[3]) / 2 - slope * ((box[0] + box[2]) / 2 - 1275))
                    for text, box in layers[(race.year, entry.votes.page)]
                ),
                key=lambda word: (round(word[2] / 50), word[1][0]),
            )
            found = [word for word in found if low - 18 <= word[2] <= high + 18]
            entry.names["last"].append(" ".join(text for text, box, _ in found if box[0] < first_x - 30))
            entry.names["first"].append(" ".join(text for text, box, _ in found if first_x - 30 <= box[0] < party_left))
            entry.names["party"].append(
                " ".join(
                    text
                    for text, box, _ in found
                    if party_left <= box[0]
                    and (box[0] + box[2]) / 2 < split
                    and not (digitish(text) and re.search(r"\d", text))
                )
            )

    def tally(texts: list[str]) -> dict[int, int]:
        found: dict[int, int] = {}
        for text in texts:
            for value, support in scan.numbers(text).items():
                found[value] = found.get(value, 0) + support
        return found

    def solve(entries: list[Entry], total: Cell) -> tuple[int, int, list[int], int | None] | None:
        best: tuple[int, int, list[int], int | None] | None = None
        shares = [next((p for text in e.share.texts if (p := scan.percent(text)) is not None), None) for e in entries]
        for value, support in sorted(tally(total.texts).items(), key=lambda pair: -pair[1])[:4]:
            if not value:
                continue
            fits = [
                sorted(
                    (
                        (
                            amount,
                            weight + (3 if share is not None and abs(100 * amount / value - share) <= 0.011 else 0),
                        )
                        for amount, weight in tally(entry.votes.texts).items()
                    ),
                    key=lambda pair: -pair[1],
                )[:4]
                for entry, share in zip(entries, shares, strict=True)
            ]
            for skip in [None, *range(len(entries))]:
                pools = [[(0, 0)] if index == skip else pool for index, pool in enumerate(fits)]
                if not all(pools):
                    continue
                for combo in itertools.product(*pools):
                    values = [amount for amount, _ in combo]
                    score = support + sum(weight for _, weight in combo)
                    if skip is not None:
                        derived, share = value - sum(values), shares[skip]
                        if derived < 0 or share is None or abs(100 * derived / value - share) > 0.011:
                            continue
                        values[skip], score = derived, score - 2
                    elif sum(values) != value:
                        continue
                    if best is None or score > best[0]:
                        best = (score, value, values, skip)
        return best

    disputed: list[Race] = []
    for race in races:
        entries = [entry for entry in race.entries if "ombined" not in entry.names["party"][0]]
        if all(entry.unopposed for entry in entries):
            race.status = "unopposed"
            continue
        if (
            race.total is None
            and len(entries) == 1
            and scan.percent(entries[0].share.texts[0] or entries[0].share.texts[1]) == 100.0
        ):
            race.total, race.sole = (
                Cell(entries[0].votes.page, entries[0].votes.box, list(entries[0].votes.texts)),
                True,
            )
        best = solve(entries, race.total) if race.total is not None else None
        direct = [
            max(found.items(), key=lambda pair: pair[1])[0] if (found := scan.numbers(entry.votes.texts[0])) else None
            for entry in entries
        ]
        if best is not None and best[3] is None and best[2] == direct:
            race.status, race.values = "read", list(best[2])
        else:
            disputed.append(race)
    asks: dict[tuple[int, int], list[Cell]] = {}
    for race in disputed:
        for cell in [
            *(entry.votes for entry in race.entries if "ombined" not in entry.names["party"][0]),
            *([race.total] if race.total else []),
        ]:
            asks.setdefault((race.year, cell.page), []).append(cell)
    for (year, page), cells in asks.items():
        boxes = [("digits", (cell.box[0] - 12, cell.box[1] - 6, cell.box[2] + 8, cell.box[3] + 6)) for cell in cells]
        for cell, texts in zip(cells, cached(year, page, boxes), strict=True):
            cell.texts.extend(texts)
    for race in disputed:
        entries = [entry for entry in race.entries if "ombined" not in entry.names["party"][0]]
        best = solve(entries, race.total) if race.total is not None else None
        if best is not None:
            race.status, race.values, race.derived = "solved", list(best[2]), best[3]
            continue
        shares = [next((p for text in e.share.texts if (p := scan.percent(text)) is not None), None) for e in entries]
        endings = [d for text in (race.total.texts if race.total else []) if len(d := re.sub(r"\D", "", text)) >= 3]
        if sum(share is not None for share in shares) < (1 if len(entries) == 2 else 2) and not endings:
            continue
        fit: tuple[int, int, list[int]] | None = None
        for combo in itertools.product(
            *(sorted(tally(entry.votes.texts).items(), key=lambda pair: -pair[1])[:3] for entry in entries)
        ):
            values = [amount for amount, _ in combo]
            total = sum(values)
            hits = (
                [abs(100 * v / total - p) <= 0.011 for v, p in zip(values, shares, strict=True) if p is not None]
                if total
                else []
            )
            if total and (
                (hits and all(hits)) or (any(str(total).endswith(d) for d in endings) and sum(hits) * 2 >= len(hits))
            ):
                score = sum(weight for _, weight in combo)
                if fit is None or score > fit[0]:
                    fit = (score, total, values)
        if fit is not None:
            race.status, race.values = "summed", list(fit[2])
            reading = (
                max(tally(race.total.texts).items(), key=lambda pair: pair[1], default=(0, 0)) if race.total else (0, 0)
            )
            if reading[1] >= 4 and reading[0] != fit[1] and not str(fit[1]).endswith(str(reading[0])):
                race.misprinted = reading[0]
    for race in races:
        if (key := (race.year, race.state, race.district)) in TRANSCRIBED:
            race.status, race.values = "transcribed", list(TRANSCRIBED[key])
    failed = [
        (race.year, race.state, race.district, race.page)
        for race in races
        if race.status != "unopposed"
        and len(race.values) != len([entry for entry in race.entries if "ombined" not in entry.names["party"][0]])
    ]
    misprinted = {(race.year, race.state, race.district): race.misprinted for race in races if race.misprinted}
    if failed or misprinted != {key: value for key, value in MISPRINTED.items() if key[0] <= 1992}:
        raise ValueError(f"book contests that do not add up: {failed}; misprinted totals {misprinted}")

    def tidy(text: str, person: bool) -> str:
        text = html.unescape(text).translate(
            str.maketrans(
                {
                    "“": '"',
                    "”": '"',
                    "„": '"',
                    "″": '"',
                    "‘": "'",
                    "’": "'",
                    "`": "'",
                    "—": "-",
                    "–": "-",
                    "~": "-",
                    "\\": " ",
                }
            )
        )
        text = re.sub(r"^\W*\d{1,2}\s*-+\s*", "", re.sub(r"\b1a\b", "la", text))
        if not person:
            text = " ".join(
                token
                for token in text.split()
                if token != "(continued)"
                and not (
                    len(core := re.sub(r"\W", "", token)) >= 4
                    and core.isupper()
                    and difflib.get_close_matches(core, statewords, n=1, cutoff=0.6)
                )
            )
            text = re.sub(r"\s*&\s*", " & ", text)
        text = re.sub(r"[^A-Za-z .,'\"()&/\-]", " ", text)
        text = re.sub(r"\s+([.,])", r"\1", text)
        text = re.sub(r"([a-z])([A-Z]\.)", r"\1 \2", text)
        if person:
            text = re.sub(
                r"\b(Democratic|Republican|Libertarian|Independent|Conservative|Socialist|Write-In)\b", " ", text
            )
            tokens = text.split()
            text = " ".join(
                token
                for index, token in enumerate(tokens)
                if not re.fullmatch(r"[a-z]", token)
                or token in PARTICLES
                or (index and tokens[index - 1].endswith(","))
            )
        text = re.sub(
            r"\b([A-Za-z]{2,}) ([a-z]+)\b",
            lambda match: match[0] if match[2] in PARTICLES else match[1] + match[2],
            text,
        )
        if person:
            text = re.sub(r"\b([A-Z]),(?=\s|$)", r"\1.", text)
        text = re.sub(r"^[.]+", "", re.sub(r"\s+", " ", text).strip(" ,-'|/&_")).strip()
        text = re.sub(r",,+", ",", text.replace('"', "") if text.count('"') % 2 else text)
        if person:
            text = re.sub(r",?\s*\b(?:dr|Jr|JR|jr|Jt|Ir)\b\.?\s*$", ", Jr.", text)
            text = re.sub(r",?\s*\b(?:Sr|SR)\b\.?\s*$", ", Sr.", text)
            text = re.sub(r",\s*(m|HI|TH|Ill|lll|IIl|IlI|lII|Il1)\s*$", ", III", text)
            text = re.sub(r",\s*(I|U|n|Il|ll|lI|H|Ii|il|11)\s*$", ", II", text)
            text = re.sub(r"\b0\.", "O.", re.sub(r"\.{2,}", ".", text))
            text = re.sub(r"(?<!['\w])([A-Z])(?=\s|$)", r"\1.", text)
            text = re.sub(r"(?<![A-Za-z'])([a-z])\.", lambda match: match[1].upper() + ".", text)
            text = re.sub(r"\bMe(?=[A-Z])", "Mc", text)
            text = re.sub(r"\bMc ?([a-z])", lambda match: "Mc" + match[1].upper(), text)
            text = " ".join(
                token
                for token in text.split()
                if not re.fullmatch(r"[a-z]{1,2}|[^A-Za-z\"]+", token) or token in PARTICLES
            )
        return text

    requests: dict[tuple[int, int], list[tuple[Entry, str, scan.Box]]] = {}
    for race in races:
        for entry in race.entries:
            if entry.layout is None:
                continue
            low, high, slope, last_x, first_x, party_left, _ = entry.layout
            for field_name, x0, x1 in (
                ("last", last_x - 15, first_x - 32),
                ("first", first_x - 35, party_left - 3),
                ("party", party_left - 5, entry.votes.box[0] - 6),
            ):
                readings = [tidy(text, field_name != "party") for text in entry.names[field_name]]
                if readings[0] != readings[1] and x1 - x0 > 20:
                    rise = slope * ((x0 + x1) / 2 - 1275)
                    box = (round(x0), round(low + rise) - 30, round(x1), round(high + rise) + 30)
                    requests.setdefault((race.year, entry.votes.page), []).append((entry, field_name, box))
    for (year, page), items in requests.items():
        for (entry, field_name, _), texts in zip(
            items, cached(year, page, [("name", box) for _, _, box in items]), strict=True
        ):
            entry.names[field_name].extend(texts)
    agreement: dict[tuple[int, int], list[bool]] = {}
    for race in races:
        for entry in race.entries:
            if len(readings := [tidy(text, True) for text in entry.names["last"]]) == 3:
                agreement.setdefault((race.year, entry.votes.page), []).append(readings[2] in readings[:2])
    for race in races:
        for entry in race.entries:
            hits = agreement.get((race.year, entry.votes.page), [])
            if len(hits) >= 4 and sum(hits) < 0.4 * len(hits):
                for readings in entry.names.values():
                    del readings[2:]
    lexicon = collections.Counter(
        token
        for race in races
        for entry in race.entries
        for token in {
            token.strip(",")
            for field_name in ("last", "first")
            for text in entry.names[field_name]
            for token in tidy(text, True).split()
        }
    )

    def rarest(text: str) -> int:
        return min((lexicon[token.strip(",")] for token in text.split()), default=0)

    def shaped(text: str) -> float:
        tokens = text.split()
        return sum(bool(NAME_SHAPE.fullmatch(t)) or t in PARTICLES for t in tokens) / len(tokens) if tokens else 0

    def plain(text: str) -> int:
        return min(
            (lexicon[t.strip(",")] for t in text.split() if not re.fullmatch(r"[A-Z]\.,?|Jr\.|Sr\.|II|III|IV", t)),
            default=99,
        )

    frequent = {
        label
        for label, size in collections.Counter(
            tidy(text, False) for race in races for entry in race.entries for text in entry.names["party"]
        ).items()
        if label and size >= 3
    }

    def vote(texts: list[str], person: bool) -> str:
        readings = [tidy(text, person) for text in texts]
        filled = [text for text in readings if text]
        if not filled:
            return ""
        best, support = collections.Counter(filled).most_common(1)[0]
        if support >= 2:
            return best
        if not person:
            return next((text for text in readings if text in frequent), readings[0] or filled[0])
        if len(texts) < 3:
            return readings[0]
        pool = (
            filled
            if readings[0]
            else [
                text
                for index, text in enumerate(readings)
                if text and shaped(text) == 1 and re.search(r"[A-Za-z]{2}", text) and (rarest(text) >= 2 or index == 1)
            ]
        )
        if not pool:
            return ""
        best = max(pool, key=lambda text: (shaped(text), plain(text)))
        shorter = best.split()
        longer = [
            text
            for text in pool
            if text != best
            and shaped(text) == 1
            and not re.search(r",\s*(?!(Jr\.|Sr\.|II|III|IV)$)\S+$", text)
            and any(
                all(
                    long.rstrip(",") == short.rstrip(",")
                    or (re.fullmatch(r"[A-Z]\.,?", short) is not None and long[0] == short[0])
                    for short, long in zip(shorter, text.split()[offset:], strict=False)
                )
                for offset in range(len(text.split()) - len(shorter) + 1)
            )
        ]
        return max(longer, key=len) if longer else best

    labels = collections.Counter(vote(entry.names["party"], False) for race in races for entry in race.entries)
    canon: dict[str, str | None] = {}
    for label, size in labels.most_common():
        if label:
            match = difflib.get_close_matches(
                label, [key for key, value in canon.items() if key == value], n=1, cutoff=0.85
            )
            canon[label] = canon[match[0]] if match else label if size >= 3 else None
    known = [label for label, value in canon.items() if label == value]
    vocabulary = sorted({*known, *ABBREVIATIONS, *FUSED})
    people: dict[int, tuple[str, list[str]]] = {}
    for race in races:
        for entry in race.entries:
            last, first = vote(entry.names["last"], True), vote(entry.names["first"], True)
            last = re.sub(r"(?<=[a-z]{2})\.$", "", last)
            if len(last.split()) >= 2:
                kept, firsts = [], first.split()
                for token in last.split():
                    twin = next(
                        (
                            index
                            for index, other in enumerate(firsts)
                            if difflib.SequenceMatcher(None, token.strip(","), other.strip(",")).ratio() >= 0.8
                        ),
                        None,
                    )
                    if twin is None:
                        kept.append(token)
                    elif lexicon[token.strip(",")] > lexicon[firsts[twin].strip(",")]:
                        firsts[twin] = token.strip(",")
                if kept:
                    last, first = " ".join(kept), " ".join(firsts)
            whole = f"{last} {first}".strip()
            if "," in whole and not re.match(r"[^,]*,\s*(Jr|Sr|II|III|IV)\b", whole):
                last, first = (part.strip() for part in whole.split(",", 1))
            elif (
                len(last.split()) >= 2
                and not any(t in PARTICLES or t in ("Vander", "Van", "St.", "De", "La", "Del") for t in last.split())
                and re.fullmatch(r"([A-Z]\.\s*)*(,?\s*(Jr\.|Sr\.|II|III|IV))?", first)
            ):
                head, _, rest = last.partition(" ")
                last, first = head, f"{rest} {first}".strip()
            suffix = re.search(r",?\s*\b(Jr|Sr|II|III|IV)\b\.?$", first)
            if suffix:
                first = first[: suffix.start()].strip(" ,")
            name = " ".join(
                part
                for part in (first, last, suffix and suffix[1] + ("." if suffix[1] in ("Jr", "Sr") else ""))
                if part
            )
            label = re.sub(
                r"(?<=[a-z]{4})\s*[.:]$",
                "",
                re.sub(r"\s*(Total Votes|UNOPPOSED|Unopposed).*$", "", vote(entry.names["party"], False)),
            )
            label = canon.get(label) or label
            if re.match(r"(and|to|of)\b", label) and (spill := re.search(r"\s*\b([A-Z][a-z]+)$", first)):
                first, label = first[: spill.start()], f"{spill[1]} {label}"
            parts = [
                part
                if part in vocabulary
                else (difflib.get_close_matches(part, vocabulary, n=1, cutoff=0.75) or [part])[0]
                for part in re.split(r"\s*(?:&|,)\s*", label)
                if part
            ]
            if len(parts) > 1 and (
                parts[0] in FUSED or parts[0] in ABBREVIATIONS or all(p in ABBREVIATIONS for p in parts)
            ):
                parties = [ABBREVIATIONS.get(part, part) for part in parts]
            else:
                match = (
                    label
                    if label in known
                    else (difflib.get_close_matches(label, known, n=1, cutoff=0.8) or [label])[0]
                )
                parties = [match] if match else []
            seen = {
                token.strip(",")
                for texts in entry.names.values()
                for text in texts
                for token in tidy(text, True).split()
            }
            tokens = name.split()
            for position, token in enumerate(tokens):
                options = [
                    option
                    for wrong, right in (("e", "c"), ("i", "l"))
                    for size in range(1, token.count(wrong) + 1)
                    for swapped in itertools.combinations([i for i, c in enumerate(token) if c == wrong], size)
                    if (option := "".join(right if i in swapped else c for i, c in enumerate(token))) in seen
                    and lexicon[option] >= lexicon[token]
                    or (wrong == "e" and lexicon[option] >= max(3, 2 * lexicon[token]))
                ]
                if options:
                    tokens[position] = max(options, key=lexicon.__getitem__)
            name = " ".join(tokens)
            people[id(entry)] = (name, [fixed for party in parties for fixed in MISREAD.get(party, (party,))])

    numerals = collections.Counter(
        (race.state, name)
        for race in races
        for entry in race.entries
        if (name := people[id(entry)][0]).endswith((" II", " III"))
    )
    for race in races:
        for entry in race.entries:
            name, parties = people[id(entry)]
            if name.endswith((" II", " III")):
                base = name.rsplit(" ", 1)[0]
                name = max((f"{base} III", f"{base} II"), key=lambda option: numerals[(race.state, option)])
                people[id(entry)] = (name, parties)

    PAGES.mkdir(parents=True, exist_ok=True)
    dimensions: dict[tuple[int, int], tuple[int, int]] = {}

    def located(year: int, cell: Cell) -> tuple[int, list[float]]:
        if (year, cell.page) not in dimensions:
            pdf = books.get(year, compendium)
            image = PAGES / f"{pdf.sha256}-{cell.page}.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(cell.page), "-l", str(cell.page), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            dimensions[(year, cell.page)] = (width, height)
        width, height = dimensions[(year, cell.page)]
        return cell.page, [
            max(0.0, cell.box[0] / width / 3),
            max(0.0, cell.box[1] / height / 3),
            min(1.0, cell.box[2] / width / 3),
            min(1.0, cell.box[3] / height / 3),
        ]

    output: list[dict[str, object]] = []
    renamed: set[tuple[int, str, str]] = set()
    for race in races:
        if race.runoff:
            continue
        key = (race.year, race.state, race.district)
        entries = [entry for entry in race.entries if "ombined" not in entry.names["party"][0]]
        later = {
            people[id(entry)][0]: (value, entry.votes)
            for other in races
            if other.runoff and (other.year, other.chamber, other.state) == (race.year, race.chamber, race.state)
            for entry, value in zip(other.entries, other.values, strict=True)
        }
        total = located(race.year, race.total) if race.total and not race.sole and race.status != "summed" else None
        before = ""
        for index, entry in enumerate(entries):
            name, parties = people[id(entry)]
            if (race.year, race.state, name) in RENAMED:
                renamed.add((race.year, race.state, name))
                name = RENAMED[(race.year, race.state, name)]
            if not name and parties == ["Write-In"]:
                name = "Scattered"
            elif not name and before:
                name = before
            elif "None of" in name:
                name, parties = NOTA, []
            elif name == "Other":
                name, parties = "Scattered", ["Write-In"]
            if not name:
                raise ValueError(f"{key}: a line without a candidate")
            before = name
            value = race.values[index] if race.values else None
            derived = index == race.derived or RESTORED.get(key) == index
            page, box = located(race.year, entry.votes) if value is not None and not derived else (None, None)
            after, cell = later.pop(name, (None, None))
            output.append(
                {
                    "year": race.year,
                    "state": race.state,
                    "chamber": race.chamber,
                    "district": None
                    if race.chamber in ("senate", "president")
                    else OLD_DISTRICTS.get(key, race.district or 0)
                    if race.special
                    else race.district or 0,
                    "term": "unexpired" if race.special else "full",
                    "name": name,
                    "parties": parties,
                    "votes": value,
                    "unopposed": entry.unopposed,
                    "runoff": after,
                    "votes_page": page,
                    "votes_box": box,
                    "votes_flags": DERIVED if derived else DIGITIZED if value is not None else 0,
                    "total_page": total and total[0],
                    "total_box": total and total[1],
                    "total_flags": DIGITIZED if total else DERIVED,
                    "runoff_flags": DIGITIZED if cell else None,
                    "runoff_page": cell and located(race.year, cell)[0],
                    "runoff_box": cell and located(race.year, cell)[1],
                }
            )
        if later:
            raise ValueError(f"{key}: runoff candidates {sorted(later)} are not in the general election")
    if unmatched := {key for key in RENAMED if key[0] <= 1992} - renamed:
        raise ValueError(f"renamings never matched: {sorted(unmatched)}")
    for (year, state, district), (name, party) in DECLARED.items():
        output.append(
            {"year": year, "state": state, "chamber": "house", "district": district, "term": "full", "name": name}
            | {"parties": [party], "unopposed": True, "votes_flags": 0, "total_flags": 0}
        )

    months = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    ballots = ("primary", "runoff", "general")
    numeric = re.compile(r"[\[\]\d,.'’`lIOoSsBZ|°*—-]+")
    spotted = re.compile(
        r"R|D|W|I|C|L|LBT|NL|REF|GRN|IDP|RTL|DFL|I-R|AIP|PFP|W\(\w{1,4}\)?|WwW|Ww|IND|UST|TAX|CON|SW|NLP|LIB"
    )
    marker = r"\W*[({\[][A-Za-z1|!][)}\]]\W*"

    def resembles(token: str, word: str, cutoff: float = 0.7) -> bool:
        return difflib.SequenceMatcher(None, token.upper().strip(":;,.*"), word).ratio() >= cutoff

    def bound(words: list[tuple[str, scan.Box]]) -> scan.Box:
        return (
            min(box[0] for _, box in words),
            min(box[1] for _, box in words),
            max(box[2] for _, box in words),
            max(box[3] for _, box in words),
        )

    docket: list[Contest] = []
    for year, pdf in books.items():
        if year <= 1992:
            continue
        info = subprocess.run(["pdfinfo", str(pdf.path)], capture_output=True, text=True, check=True).stdout
        section: str | None = None
        addenda = False
        centers: list[float] = []
        scanned: list[
            tuple[
                int, str, bool, list[list[scan.Word]], list[float], int | None, list[float | None], list[float | None]
            ]
        ] = []
        for page in range(1, int(re.findall(r"Pages:\s+(\d+)", info)[0]) + 1):
            words = scan.words(pdf.path, page, PARSED / "ocr" / f"{pdf.sha256}-{page}.tsv")
            slope = scan.skew(words)
            found = scan.lines(words, slope)
            upper = " ".join(w[0] for w in words).upper()
            heading = (
                "president"
                if "PRESIDENT" in upper
                else "senate"
                if "SENATE" in upper
                else "house"
                if "HOUSE" in upper
                else None
            )
            if "OFFICIAL" in upper and "RESULTS" in upper and len(words) < 40:
                section = heading or section
                continue
            top_line: int | None = None
            if "GUIDE TO" in upper:
                section = None
            elif (
                titled := any("ADDENDUM" in " ".join(w[0] for w in line).upper() for line in found[:4])
                or any(len(line) <= 2 and resembles(line[0][0], "ERRATA", 0.8) for line in found)
            ) or (addenda and section in ("senate", "house")):
                addenda = True
                section = heading or ("house" if titled else section)
                if section == "president":
                    continue
                stamped_line = next(
                    (
                        index
                        for index, line in enumerate(found)
                        if sum(re.fullmatch(months, w[0]) is not None for w in line) >= 2
                    ),
                    None,
                )
                if stamped_line is not None:
                    spans = [
                        (w[1][0] + found[stamped_line][index + 1][1][2]) / 2
                        for index, w in enumerate(found[stamped_line])
                        if re.fullmatch(months, w[0]) and index + 1 < len(found[stamped_line])
                    ]
                    centers = spans if len(spans) == 3 else [spans[0], (spans[0] + spans[-1]) / 2, spans[-1]]
                    top_line = stamped_line - 1
                else:
                    top_line = next(
                        (
                            index
                            for index, line in enumerate(found)
                            if len(line) <= 2 and resembles(line[0][0], "ERRATA", 0.8)
                        ),
                        None,
                    )
            elif (
                top_line := next(
                    (
                        index
                        for index, line in enumerate(found[:6])
                        if any(resembles(w[0], "ELECTION") for w in line)
                        and any(resembles(w[0], "GENERAL") for w in line)
                    ),
                    None,
                )
            ) is None:
                if scanned:
                    top_line = next(
                        (
                            index
                            for index, line in enumerate(found[:3])
                            if any(resembles(w[0], "CANDIDATE", 0.75) for w in line[:2])
                        ),
                        None,
                    )
            elif section in ("senate", "house"):
                headers = {
                    label: (w[1][0] + w[1][2]) / 2
                    for w in found[top_line]
                    for label in ballots
                    if resembles(w[0], label.upper(), 0.6)
                }
                if len(headers) != 3:
                    continue
                centers = [headers[label] for label in ballots]
            if top_line is None or section is None or section not in ("senate", "house"):
                continue
            layers[(year, page)] = scan.layer(pdf.path, page)
            body = found[top_line + 1 :]
            covered = [(min(w[1][1] for w in line), max(w[1][3] for w in line)) for line in body]
            top = max(w[1][3] for w in found[top_line])
            for line in scan.lines(
                [(html.unescape(text), box, (0, 0, 0), 0.0) for text, box in layers[(year, page)]], slope
            ):
                middle = sum((w[1][1] + w[1][3]) / 2 for w in line) / len(line)
                if (
                    len(line) >= 2
                    and middle > top + 10
                    and not any(low - 6 <= middle <= high + 6 for low, high in covered)
                ):
                    body.append(line)
            body.sort(key=lambda line: scan.level(line[0], slope))
            spots = [
                round(w[1][0] / 10) * 10
                for line in body
                for w in line
                if spotted.fullmatch(w[0]) and centers[0] - 600 < w[1][0] < centers[0] - 120
            ]
            vote_edges: list[list[int]] = [[], [], []]
            share_edges: list[list[int]] = [[], [], []]
            for line in body:
                for w in line:
                    if re.search(r"\d,\d{3}", w[0]) and not w[0].startswith("["):
                        vote_edges[min(range(3), key=lambda g: abs(w[1][2] - 80 - centers[g]))].append(w[1][2])
                    elif re.fullmatch(r"\d{1,3}\.\d\d", w[0]):
                        share_edges[min(range(3), key=lambda g: abs(w[1][2] - 260 - centers[g]))].append(w[1][2])
            scanned.append(
                (
                    page,
                    section,
                    addenda,
                    body,
                    list(centers),
                    collections.Counter(spots).most_common(1)[0][0] if spots else None,
                    [statistics.median(spread) if len(spread) >= 2 else None for spread in vote_edges],
                    [statistics.median(spread) if len(spread) >= 2 else None for spread in share_edges],
                )
            )

        voting = [
            statistics.median([edge - entry[4][g] for entry in scanned if (edge := entry[6][g]) is not None] or [80])
            for g in range(3)
        ]
        sharing = [
            statistics.median([edge - entry[4][g] for entry in scanned if (edge := entry[7][g]) is not None] or [260])
            for g in range(3)
        ]
        leaning = statistics.median([spot - entry[4][0] for entry in scanned if (spot := entry[5]) is not None])
        where: str | None = None
        ordinal: int | None = None
        term = "full"
        pending_contest: Contest | None = None
        posted: dict[str, str] = {}
        last_section: str | None = None
        for page, section, appended, body, centers, spot, vote_right, share_right in scanned:
            shift = statistics.median(
                [edge - centers[g] - voting[g] for g, edge in enumerate(vote_right) if edge is not None] or [0]
            )
            edges = [
                round(edge if edge is not None else centers[g] + offset + shift)
                for g in range(3)
                for edge, offset in ((vote_right[g], voting[g]), (share_right[g], sharing[g]))
            ]
            party_x = round(spot if spot is not None else centers[0] + leaning + shift)
            start = edges[0] - 330
            if section != last_section:
                last_section, where, ordinal, term, pending_contest = section, None, None, "full", None
            for line in body:
                text = " ".join(w[0] for w in line)
                merged: list[tuple[str, scan.Box]] = []
                for w in line:
                    if (
                        merged
                        and numeric.fullmatch(w[0])
                        and numeric.fullmatch(merged[-1][0])
                        and w[1][0] - merged[-1][1][2] < 14
                        and w[1][0] > start
                        and not (
                            w[0].startswith(".") and any(abs(merged[-1][1][2] - edge) <= 25 for edge in edges[::2])
                        )
                    ):
                        merged[-1] = (merged[-1][0] + w[0], bound([merged[-1], (w[0], w[1])]))
                    else:
                        merged.append((w[0], w[1]))
                pieces: dict[tuple[str, str], list[tuple[str, scan.Box]]] = {}
                leftover: list[tuple[str, scan.Box]] = []
                for word, box in merged:
                    right = box[2]
                    index = min(range(6), key=lambda i: abs(edges[i] - right))
                    near = abs(edges[index] - right)
                    if (
                        box[2] >= start
                        and near <= 140
                        and (
                            (
                                near <= 40
                                and len(word) >= 2
                                and not re.search(r"nopposed|ombined|Parties|[A-Za-z]{4,}", word)
                            )
                            or (numeric.fullmatch(word) and re.search(r"\d", word))
                        )
                    ):
                        pieces.setdefault((ballots[index // 2], ("votes", "share")[index % 2]), []).append((word, box))
                    elif not (re.fullmatch(r"[\d.,'`]+", word) and box[2] < party_x - 700):
                        leftover.append((word, box))
                cells = {key: (" ".join(word for word, _ in spread), bound(spread)) for key, spread in pieces.items()}
                tokens = [word for word, _ in leftover]
                lefts = [
                    word for word, box in leftover if box[2] < party_x - 15 and not re.fullmatch(r"[^\w(\[{§]+", word)
                ]
                joined = " ".join(tokens)
                if any(resembles(word, "CANDIDATE", 0.75) for word in tokens[:3]) and any(
                    resembles(word, "PARTY") or resembles(word, "NAME") for word in tokens
                ):
                    continue
                if re.search(r"T\w?[oa0]ta[l1I]", joined) and re.search(r"V[o0]t", joined):
                    if pending_contest is not None:
                        closing = Line(year, page, edges, cells)
                        if "Party" in joined:
                            pending_contest.parties.append((len(pending_contest.lines), closing))
                        else:
                            if not (section == "house" and pending_contest.district is not None and "State" in joined):
                                pending_contest.total = closing
                            pending_contest = None
                    continue
                if "NO DIS" in joined.upper() and not cells:
                    ordinal, pending_contest = -1, None
                    continue
                if (
                    lefts
                    and "COLUMB" not in joined.upper()
                    and (
                        resembles(lefts[0], "DISTRICT", 0.75)
                        or (
                            len(lefts) == 2
                            and re.fullmatch(r"\d{1,2}", lefts[1])
                            and len(lefts[0]) >= 5
                            and "," not in lefts[0]
                            and not cells
                        )
                    )
                ):
                    tail = itertools.takewhile(
                        lambda word: not re.search(r"[A-Za-z]{3}|[({]", word), [lefts[0][8:], *lefts[1:3]]
                    )
                    digits = re.sub(
                        r"\D",
                        "",
                        " ".join(tail).translate(str.maketrans({"§": "5", "t": "1"})).translate(scan.CONFUSABLE),
                    )
                    number = int(digits) if digits else (ordinal or 0) + 1
                    term = "unexpired" if "nexpired" in joined else "full"
                    if not ("ontinued" in joined and number == ordinal):
                        pending_contest = None
                    ordinal = number
                    continue
                if re.search(r"UNEXPIRED|FULL TERM", joined.upper()) and not cells:
                    term, pending_contest = "unexpired" if "UNEXPIRED" in joined.upper() else "full", None
                    continue
                if lefts and (not cells or re.search(months, text)):
                    cleaned = re.sub(
                        r"\(.*|continued.*|special.*|unexpired.*|-?insert.*|\*.*",
                        "",
                        " ".join(lefts),
                        flags=re.IGNORECASE,
                    ).strip(" .,:;|-'_~*\"")
                    cleaned = re.sub(r"\s+", " ", cleaned).upper().replace("W ASHINGTON", "WASHINGTON")
                    if len(cleaned) >= 3 and (
                        match := difflib.get_close_matches(cleaned, list(named), n=1, cutoff=0.8)
                    ):
                        if named[match[0]] != where:
                            term, ordinal, pending_contest, posted = "full", None, None, {}
                        if section == "senate" and re.search(r"(?i)unexpired|special", joined):
                            term, pending_contest = "unexpired", None
                        elif section == "senate" and re.search(months, text):
                            term = "full"
                        where = named[match[0]]
                        marks = sorted(
                            [(word, box) for word, box in leftover if box[0] > party_x]
                            + [piece for spread in pieces.values() for piece in spread if piece[1][0] > party_x],
                            key=lambda mark: mark[1][0],
                        )
                        if any(re.match(months, word) for word, _ in marks):
                            posted = {}
                        for index, (word, box) in enumerate(marks):
                            if re.match(months, word) and index + 1 < len(marks):
                                center = (box[0] + marks[index + 1][1][2]) / 2
                                column = min(
                                    range(3), key=lambda g: abs((edges[2 * g] + edges[2 * g + 1]) / 2 - 90 - center)
                                )
                                following = marks[index + 2][0] if index + 2 < len(marks) else ""
                                posted[ballots[column]] = f"{word} {marks[index + 1][0]}" + (
                                    f" {following}" if re.fullmatch(r"\W*(19|20)\d\d\W*", following) else ""
                                )
                        continue
                if not (cells or re.search(r"nopposed|Co\w{1,3}ined", joined)):
                    continue
                if (
                    re.fullmatch(r"\W*-?\s*\d+\s*-?\W*", text.strip())
                    or re.match(r"\W*[*#]", text)
                    or not re.search(r"[A-Za-z]{2}|W\(", joined)
                    or (
                        len(leftover) > 8
                        and sum(re.fullmatch(r"[a-z]{2,}[.,;:]?", word) is not None for word in tokens) >= 3
                    )
                ):
                    continue
                codes = [
                    (word, box)
                    for word, box in leftover
                    if party_x - 60 <= box[0] < party_x + 150 and not re.search(r"Co\w{1,3}ined|Parties|nopposed", word)
                ]
                naming = [
                    (word, box)
                    for index, (word, box) in enumerate(
                        [(word, box) for word, box in leftover if box[2] < party_x - 10]
                    )
                    if not (index == 0 and re.fullmatch(marker, word))
                    and not re.search(r"Co\w{1,3}ined|Parties", word)
                    and not (word == "I" and (word, box) in codes)
                ]
                name = " ".join(word for word, _ in naming) or (
                    "Scattered" if codes and codes[0][0].startswith("W") else ""
                )
                if (
                    re.search(r"\b" + months + r"\.?\s+\d{1,2}(st|nd|rd|th|,)", name)
                    or not re.match(r"[\W_]*[A-Za-z0]", name)
                    or not re.search(r"[A-Za-z]{3}", name)
                    or re.search(r"\d/\d", name)
                    or not re.search(r"[A-Z][a-z]", name)
                    or (
                        not re.search(r",|Scat|Other|None|Blank|Void|Write", name)
                        and not codes
                        and not re.search(r"Co\w{1,3}ined", joined)
                    )
                ):
                    continue
                if where is None:
                    raise ValueError(f"{year} page {page}: a candidate before any state")
                if pending_contest is None:
                    pending_contest = Contest(
                        year, section, where, ordinal if section == "house" else None, term, page, posted, appended
                    )
                    docket.append(pending_contest)
                span = [box for _, box in leftover] + [box for spread in pieces.values() for _, box in spread]
                low, high = min(box[1] for box in span), max(box[3] for box in span)
                pending_contest.lines.append(
                    Line(
                        year,
                        page,
                        edges,
                        cells,
                        name=name,
                        naming=bound(naming) if naming else None,
                        party=" ".join(word for word, _ in codes),
                        marking=bound(codes) if codes else (party_x - 60, low, party_x + 150, high),
                        incumbent=bool(lefts) and re.fullmatch(marker, lefts[0]) is not None,
                        combined=re.search(r"Co\w{1,3}ined", joined) is not None,
                        tacit=any("nopposed" in word and box[0] < edges[1] + 60 for word, box in leftover),
                        unopposed=any(
                            ("nopposed" in word or (year, where) in STARRED and word == "*") and box[0] > edges[3]
                            for word, box in leftover
                        ),
                        span=(low, high),
                    )
                )

    docket = [contest for contest in docket if contest.district != -1]
    for contest in docket:
        if not contest.dates:
            contest.dates = next(
                (
                    other.dates
                    for other in docket
                    if (other.year, other.state, other.chamber) == (contest.year, contest.state, contest.chamber)
                    and other.dates
                ),
                {},
            )
    runs: dict[tuple[int, str], list[Contest]] = {}
    for contest in docket:
        if (
            contest.chamber == "house"
            and contest.term == "full"
            and contest.district is not None
            and not contest.addendum
            and (contest.year, contest.state) != (1996, "TX")
        ):
            runs.setdefault((contest.year, contest.state), []).append(contest)
    for run in runs.values():
        for index, contest in enumerate(run):
            before = (run[index - 1].district or 0) if index else 0
            after = run[index + 1].district if index + 1 < len(run) else None
            number = contest.district or 0
            if number != before + 1 and (
                number <= before or number > APPORTIONMENT[1992].get(contest.state, 1) or after == before + 2
            ):
                contest.district = before + 1
    revised = {
        (contest.year, contest.chamber, contest.state, contest.district, contest.term)
        for contest in docket
        if contest.addendum
    }
    docket = [
        contest
        for contest in docket
        if contest.addendum
        or (contest.year, contest.chamber, contest.state, contest.district, contest.term) not in revised
    ]

    def dated(text: str | None, year: int) -> date | None:
        parts = (text or "").split()
        digits = (
            re.sub(r"\D", "", re.sub(r"(st|nd|rd|th)\W*$", "", parts[1]).translate(scan.CONFUSABLE))
            if len(parts) >= 2
            else ""
        )
        if not digits or not re.fullmatch(months, parts[0]):
            return None
        return date(
            int(re.sub(r"\D", "", parts[2])) if len(parts) >= 3 else year,
            "JanFebMarAprMayJunJulAugSepOctNovDec".index(parts[0][:3]) // 3 + 1,
            int(digits),
        )

    pooled = {(1994, "WA"), (1996, "WA"), (1998, "WA"), (1996, "AK"), (1998, "AK")}
    for contest in docket:
        stamped = {column: dated(text, contest.year) for column, text in contest.dates.items()}
        november = date(contest.year, 11, 2 + (-date(contest.year, 11, 1).weekday()) % 7)
        if (stamped.get("primary") or date.min) >= november or (contest.state == "LA" and contest.year in JUNGLE):
            contest.layout = {"primary": "general", "general": "second"}
        elif stamped.get("runoff") == november or (stamped.get("general") or date.min) > november:
            contest.layout = {"primary": "primary", "runoff": "general", "general": "second"}
        elif (contest.year, contest.state) in pooled:
            contest.layout = {"primary": "blanket", "general": "general"}
        else:
            contest.layout = {"primary": "primary", "runoff": "runoff", "general": "general"}
        names = [line.name.rstrip("#* ") for line in contest.lines if line.combined]
        for line in contest.lines:
            general = line.cells.get(("general", "votes"))
            line.fusion = not line.combined and (
                (general is not None and general[0][:1] in ("[", "{"))
                or any(difflib.SequenceMatcher(None, line.name.rstrip("#* "), other).ratio() >= 0.85 for other in names)
            )

    def layered(year: int, page: int, box: scan.Box) -> str:
        center, reach = (box[1] + box[3]) / 2, (box[3] - box[1]) * 0.6
        return html.unescape(
            " ".join(
                text
                for text, other in sorted(layers[(year, page)], key=lambda word: word[1][0])
                if other[0] >= box[0] - 25
                and other[2] <= box[2] + 25
                and abs((other[1] + other[3]) / 2 - center) < reach
            )
        )

    def decoded(text: str, year: int) -> tuple[str | None, bool, tuple[str, ...]] | None:
        text = re.sub(r"^[1l|\[]\(", "I(", re.sub(r"\s*\(\s*", "(", text.strip(" .,:;'‘’“”\"_|~*#§¢©@—-")))
        chosen, _, rest = text.partition(" and ")
        parts = text.split("/")
        if rest:
            sides = [chosen, *rest.split("/")]
        elif parts[0].startswith("W(") and len(parts) > 1:
            sides = parts
        else:
            sides = [parts[0], *parts]
        meanings: list[tuple[str, bool]] = []
        for side in sides:
            shown = re.sub(r"^[WV]\(?([A-Z]{1,3})\)$", r"W(\1)", side)
            wrapped = re.fullmatch(r"([WNIU])\(([A-Z]+(?:-[A-Z]+)?)\)", shown)
            code = wrapped[2] if wrapped else shown
            if (year, code) not in CODES and code not in CODES:
                code = next(
                    (fixed for years, pattern, fixed in BLURRED if year in years and re.fullmatch(pattern, code)), code
                )
            party = CODES.get((year, code)) or CODES.get(code)
            if party is None:
                return None
            meanings.append((party, wrapped[1] == "W" if wrapped else code == "W"))
        (party, written), *general = meanings
        return (
            None if party == "Nonpartisan" else party,
            written,
            tuple(dict.fromkeys("Write-In" if write else label for label, write in general if label != "Nonpartisan")),
        )

    rereads: dict[tuple[int, int], list[Line]] = {}
    for contest in docket:
        for line in contest.lines:
            if line.combined:
                continue
            texts = [line.party, layered(line.year, line.page, line.marking)]
            options = [found for text in texts if (found := decoded(text, line.year)) is not None]
            if len(options) == 2 and options[0] == options[1]:
                line.meaning = options[0]
            else:
                rereads.setdefault((line.year, line.page), []).append(line)
    unread: list[tuple[int, int, str, list[str]]] = []
    for (year, page), waiting_lines in rereads.items():
        boxes = [
            ("name", (line.marking[0] - 10, line.marking[1] - 6, line.marking[2] + 10, line.marking[3] + 6))
            for line in waiting_lines
        ]
        for line, texts in zip(waiting_lines, cached(year, page, boxes), strict=True):
            heard = [line.party, layered(line.year, line.page, line.marking), *texts]
            ordered = heard if year == 1994 else [heard[1], heard[0], *texts]
            options = [found for text in ordered if (found := decoded(text, year)) is not None]
            if options:
                line.meaning = max(options, key=lambda option: (options.count(option), -options.index(option)))
            elif re.match(r"(?i)\W*(scat|write|other|all oth|misc)", line.name):
                line.meaning = (None, True, ("Write-In",))
            elif re.match(r"(?i)\W*none of", line.name):
                line.meaning = (None, False, ())
            else:
                unread.append((year, page, line.name, heard))
    if unread:
        raise ValueError(f"book party labels that could not be read: {unread}")

    rereadings: dict[tuple[int, str, str], list[str]] = {}

    def readouts(line: Line, column: str, kind: str) -> list[str]:
        entry = line.cells.get((column, kind))
        heard = [] if entry is None else [entry[0], layered(line.year, line.page, entry[1])]
        share = line.cells.get((column, "share"))
        if (
            share is not None
            and share[1][0] < line.edges[ballots.index(column) * 2] - 15
            and (fused := re.fullmatch(r"(\d[\d,]*)[.\-\s]+(\d\d)", share[0]))
        ):
            heard = [text for text in heard if kind == "votes" or text != share[0]]
            heard.append(fused[1] if kind == "votes" else "." + fused[2])
        return [text.strip("[]").replace("$", "5") for text in heard + rereadings.get((id(line), column, kind), [])]

    def region(line: Line, column: str, kind: str) -> scan.Box:
        entry = line.cells.get((column, kind))
        edge = line.edges[ballots.index(column) * 2 + (kind == "share")]
        low, high = (entry[1][1], entry[1][3]) if entry is not None else line.span
        left, right = edge - (250 if kind == "votes" else 130), edge + 15
        if entry is not None:
            left, right = min(left, entry[1][0] - 15), max(right, entry[1][2] + 12)
        return (left, low - 8, right, high + 8)

    def share_of(texts: list[str]) -> set[float]:
        return {found for text in texts if (found := scan.percent(text)) is not None and found <= 100}

    def agrees(amount: int, reference: int | None, shares: set[float]) -> bool:
        return (
            reference is not None
            and reference > 0
            and any(abs(100 * amount / reference - share) <= 0.011 for share in shares)
        )

    def balance(
        pools: list[tuple[list[str], list[str]]], heard: list[str] | None, base: int | None = None
    ) -> tuple[int, int, list[int], int | None] | None:
        best: tuple[int, int, list[int], int | None] | None = None
        shares = [share_of(texts) for _, texts in pools]
        options: list[tuple[int | None, int]] = (
            [(value, support) for value, support in sorted(tally(heard).items(), key=lambda pair: -pair[1])[:4]]
            if heard
            else [(None, 0)]
        )
        for total, support in options:
            reference = base or total
            candidates = [
                [
                    (amount, weight + (3 if agrees(amount, reference, share) else 0))
                    for amount, weight in sorted(tally(texts).items(), key=lambda pair: -pair[1])[:4]
                ]
                for (texts, _), share in zip(pools, shares, strict=True)
            ]
            for skip in [None, *range(len(pools))]:
                if skip is not None and total is None:
                    continue
                choices = [[(0, 0)] if index == skip else pool for index, pool in enumerate(candidates)]
                if not all(choices):
                    continue
                for combo in itertools.islice(itertools.product(*choices), 20000):
                    values = [amount for amount, _ in combo]
                    score = support + sum(weight for _, weight in combo)
                    if skip is not None and total is not None:
                        derived = total - sum(values)
                        if derived < 0 or not agrees(derived, reference, shares[skip]):
                            continue
                        values[skip], score = derived, score - 2
                    elif total is not None and sum(values) != total:
                        continue
                    if total is None and not all(
                        not share or agrees(value, reference or sum(values), share)
                        for value, share in zip(values, shares, strict=True)
                    ):
                        continue
                    if best is None or score > best[0]:
                        best = (score, total if total is not None else sum(values), values, skip)
        return best

    families: list[list[Line]] = []
    printings: dict[tuple[object, ...], int] = {}
    unsolved: list[tuple[int, str, str | None, int | None, str, list[str]]] = []
    for contest in docket:
        mapping = contest.layout
        blocks: list[tuple[str, list[Line], Line | None]] = []
        for physical in ballots:
            if mapping.get(physical) not in ("general", "second", "blanket"):
                continue
            whole = [
                line
                for line in contest.lines
                if not (physical == "general" and mapping[physical] == "general" and line.fusion)
                and ((physical, "votes") in line.cells or (physical, "share") in line.cells)
            ]
            ending = contest.total if contest.total and (physical, "votes") in contest.total.cells else None
            if whole or ending:
                blocks.append((physical, whole, ending))
        for line in contest.lines:
            if line.combined:
                fused = [
                    other
                    for other in contest.lines
                    if other.fusion
                    and ("general", "votes") in other.cells
                    and difflib.SequenceMatcher(None, other.name.rstrip("#* "), line.name.rstrip("#* ")).ratio() >= 0.85
                ]
                if fused:
                    blocks.append(("fusion", fused, line))
                    families.append([line, *fused])
        for column in ("primary", "runoff"):
            if mapping.get(column) != column:
                continue
            ends = dict(contest.parties)
            gathered: list[Line] = []
            flushed: int | None = None
            running = {0.0}
            for index, line in enumerate(contest.lines):
                entry = line.cells.get((column, "votes"))
                if line.party == "W":
                    continue
                blank = entry is not None and not re.search(r"\d", entry[0] + layered(line.year, line.page, entry[1]))
                if (
                    entry is not None
                    and (column, "share") not in line.cells
                    and not blank
                    and not re.search(r"\d", layered(line.year, line.page, region(line, column, "share")))
                ):
                    if gathered and ((column, "share") in gathered[-1].cells or gathered[-1].party != line.party):
                        blocks.append((column, gathered, None))
                        gathered, flushed, running = [], len(blocks) - 1, {0.0}
                    gathered.append(line)
                    following = contest.lines[index + 1] if index + 1 < len(contest.lines) else None
                    if (
                        index + 1 in ends
                        or following is None
                        or (column, "share") in following.cells
                        or (column, "votes") not in following.cells
                    ):
                        blocks.append((column, gathered, None))
                        gathered, flushed, running = [], len(blocks) - 1, {0.0}
                    continue
                if (entry is not None or (column, "share") in line.cells) and not blank:
                    gathered.append(line)
                    if len(running) < 200:
                        running = {
                            round(total + share, 2)
                            for total in running
                            for share in share_of(readouts(line, column, "share")) or {0.0}
                        }
                if index + 1 in ends:
                    if gathered:
                        blocks.append((column, gathered, ends[index + 1]))
                    elif flushed is not None:
                        blocks[flushed] = (column, blocks[flushed][1], ends[index + 1])
                    gathered, flushed, running = [], None, {0.0}
                    continue
                following = next(
                    (
                        later
                        for later in contest.lines[index + 1 :]
                        if (column, "votes") in later.cells or (column, "share") in later.cells
                    ),
                    None,
                )
                seen_shares = collections.Counter(
                    found
                    for text in (readouts(following, column, "share") if following is not None else [])
                    if (found := scan.percent(text)) is not None and found <= 100
                )
                if seen_shares:
                    tail = any(99.97 <= total + seen_shares.most_common(1)[0][0] <= 100.03 for total in running)
                else:
                    tail = (
                        following is not None
                        and (
                            re.match(r"\W*[wW]", following.party)
                            or re.match(r"Scat|All Oth|Other|Write|None of", following.name)
                        )
                        is not None
                    )
                if gathered and not tail and any(99.97 <= total <= 100.03 for total in running):
                    blocks.append((column, gathered, None))
                    gathered, flushed, running = [], len(blocks) - 1, {0.0}
            if gathered:
                blocks.append((column, gathered, None))

        for column, members, closing in blocks:
            source = "general" if column == "fusion" else column
            role = "fusion" if column == "fusion" else mapping[column]
            scope = None
            if role in ("primary", "runoff"):
                counts = collections.Counter(
                    line.scope or line.meaning[0] for line in members if line.scope or line.meaning[0]
                )
                scope = counts.most_common(1)[0][0] if counts else None
                for line in members:
                    line.scope = line.scope or scope
            printed_cell = closing.cells.get((source, "votes")) if closing is not None else None
            pools: list[tuple[list[str], list[str]]] = []
            heard: list[str] | None = None
            best = None
            for attempt in range(2):
                pools = [
                    (readouts(line, source, "votes"), [] if column == "fusion" else readouts(line, source, "share"))
                    for line in members
                ]
                heard = (
                    [
                        text.strip("[]").replace("$", "5")
                        for text in (
                            printed_cell[0],
                            layered(contest.year, closing.page, printed_cell[1]),
                            *rereadings.get((id(closing), column, "votes"), []),
                        )
                    ]
                    if printed_cell is not None and closing is not None
                    else None
                )
                best = balance(pools, heard)
                if best is None and column == "primary" and heard is None:
                    whole = sum(
                        max(
                            tally(readouts(line, "primary", "votes")[:1]).items(),
                            key=lambda pair: pair[1],
                            default=(0, 0),
                        )[0]
                        for line in contest.lines
                        if ("primary", "votes") in line.cells
                    )
                    best = balance(pools, None, whole) if whole else None
                if best is not None or attempt:
                    break
                wanted: dict[int, list[tuple[tuple[int, str, str], scan.Box]]] = {}
                for line in members:
                    for kind in ("votes",) if column == "fusion" else ("votes", "share"):
                        wanted.setdefault(line.page, []).append(((id(line), source, kind), region(line, source, kind)))
                if printed_cell is not None and closing is not None:
                    box, edge = printed_cell[1], closing.edges[ballots.index(source) * 2]
                    wanted.setdefault(closing.page, []).append(
                        (
                            (id(closing), column, "votes"),
                            (min(box[0] - 15, edge - 250), box[1] - 8, max(box[2] + 12, edge + 15), box[3] + 8),
                        )
                    )
                for page, requested in wanted.items():
                    for (key, _), texts in zip(
                        requested, cached(contest.year, page, [("digits", box) for _, box in requested]), strict=True
                    ):
                        rereadings[key] = texts
            key = (
                (contest.year, contest.state, contest.district)
                if role == "general" and contest.chamber == "house"
                else (contest.year, contest.state, contest.district, scope)
                if role in ("primary", "runoff")
                else None
            )
            consistent = best is not None
            if best is None and heard and column != "fusion":
                best = balance(pools, None)
            if best is None and heard and (key in MISPRINTED or key in MISPRINTED_PRIMARIES):
                values = [max(tally(texts).items(), key=lambda pair: pair[1])[0] for texts, _ in pools]
                best = (0, sum(values), values, None)
            if best is None:
                unsolved.append(
                    (
                        contest.year,
                        contest.state,
                        contest.chamber,
                        contest.district,
                        column,
                        [line.name for line in members],
                    )
                )
                continue
            if not consistent and heard:
                stamp, support = max(tally(heard).items(), key=lambda pair: pair[1], default=(0, 0))
                if support >= 3 and key is not None:
                    printings[key] = stamp
            _, total, values, skip = best
            summed = (
                total,
                DIGITIZED if consistent and printed_cell is not None and closing is not None else DERIVED,
                closing.page if consistent and closing is not None else None,
                printed_cell[1] if consistent and printed_cell is not None else None,
            )
            for index, line in enumerate(members):
                line.values[source] = (values[index], DERIVED if index == skip else DIGITIZED)
                if column != "fusion":
                    line.totals[source] = summed
    expected = {key: value for key, value in [*MISPRINTED.items(), *MISPRINTED_PRIMARIES.items()] if key[0] >= 1994}
    if unsolved or printings != expected:
        raise ValueError(f"book contests that do not add up: {unsolved}; misprinted totals {printings}")

    for contest in docket:
        for line in contest.lines:
            heard = [line.name]
            if line.naming is not None:
                box = (line.naming[0], line.naming[1], max(line.naming[2], line.marking[0] - 5), line.naming[3])
                heard.append(html.unescape(layered(line.year, line.page, box)))
                if not re.search(r"\sI\.?\s*$", line.name):
                    heard[-1] = re.sub(r"\s+[I|l]\.?\s*$", "", heard[-1])
            legible: list[str] = []
            for text in heard:
                cleaned = tidy(re.sub(r"^" + marker, "", text), True)
                for pattern, replacement in MENDS:
                    cleaned = pattern.sub(replacement, cleaned)
                cleaned = SPLIT_INITIAL.sub(
                    lambda match: match[0] if match[2] in PARTICLES else match[1] + match[2], cleaned
                )
                if re.search(r"[A-Z][a-z]{2}", cleaned):
                    legible.append(cleaned)
            ordered = legible if contest.year == 1994 else legible[::-1]
            chosen = (
                min(ordered, key=lambda text: ("," not in text, len(FLAWED.findall(text)), ordered.index(text)))
                if ordered
                else line.name
            )
            if re.match(r"(?i)\W*(scat|[wv]rite|other|all oth|misc)", chosen) or (
                line.meaning[1] and any(re.match(r"(?i)\W*scat", text) for text in ordered)
            ):
                line.name = "Scattered"
            elif re.match(r"(?i)\W*none of", chosen):
                line.name = NOTA
            else:
                last, comma, rest = chosen.partition(",")
                if not comma:
                    last, _, rest = chosen.partition(" ")
                suffix = re.search(r",?\s*\b(Jr\.|Sr\.|II|III|IV)$", rest)
                line.name = " ".join(
                    piece
                    for piece in (
                        (rest[: suffix.start()] if suffix else rest).strip(" ,"),
                        last.strip(" ,"),
                        suffix[1] if suffix else "",
                    )
                    if piece
                )
    for family in families:
        common = collections.Counter(line.name for line in family).most_common(1)[0][0]
        for line in family:
            line.name = common
    for contest in docket:
        for line in contest.lines:
            if (contest.year, contest.state, line.name) in RENAMED:
                renamed.add((contest.year, contest.state, line.name))
                line.name = RENAMED[(contest.year, contest.state, line.name)]

    def pinned(line: Line, column: str) -> tuple[int, list[float]]:
        entry = line.cells.get((column, "votes"))
        return located(line.year, Cell(line.page, entry[1] if entry is not None else region(line, column, "votes")))

    calendar_counts: dict[tuple[int, str], collections.Counter[tuple[date | None, date | None]]] = {}
    for contest in docket:
        if (
            first := next((key for key, role in contest.layout.items() if role in ("primary", "blanket")), None)
        ) is None or not any(first in line.values or line.tacit for line in contest.lines):
            continue
        calendar_counts.setdefault((contest.year, contest.state), collections.Counter())[
            (
                dated(contest.dates.get(first), contest.year),
                dated(contest.dates.get("runoff"), contest.year) if contest.layout.get("runoff") == "runoff" else None,
            )
        ] += 1
    calendar_rows = {
        key: (
            counts.most_common(1)[0][0][0],
            next((runoff for (_, runoff), _ in counts.most_common() if runoff is not None), None)
            or PRIMARY_RUNOFFS.get(key),
        )
        for key, counts in calendar_counts.items()
    }
    if undated := [key for key, (opened, _) in calendar_rows.items() if opened is None]:
        raise ValueError(f"book primaries without a date: {undated}")
    book_primaries: list[dict[str, object]] = []
    for contest in docket:
        general = next(key for key, role in contest.layout.items() if role == "general")
        second = next((key for key, role in contest.layout.items() if role == "second"), None)
        first = next((key for key, role in contest.layout.items() if role in ("primary", "blanket")), None)
        runoff_column = "runoff" if contest.layout.get("runoff") == "runoff" else None
        district = (
            None
            if contest.chamber == "senate"
            else 0
            if APPORTIONMENT[1992].get(contest.state, 1) == 1
            else contest.district
        )
        summed = next((line.totals[general] for line in contest.lines if general in line.totals), None)
        for line in contest.lines:
            marked = line.unopposed if general == "general" else line.tacit if general == "primary" else False
            if line.combined or (general not in line.values and not marked):
                continue
            value, flags = line.values.get(general, (None, 0))
            after = line.values.get(second) if second else None
            votes_place = pinned(line, general) if value is not None and flags == DIGITIZED else (None, None)
            runoff_place = pinned(line, second) if second and after and after[1] == DIGITIZED else (None, None)
            total_place = (
                located(contest.year, Cell(summed[2], summed[3]))
                if summed and summed[2] is not None and summed[3] is not None
                else (None, None)
            )
            output.append(
                {
                    "year": contest.year,
                    "state": contest.state,
                    "chamber": contest.chamber,
                    "district": district,
                    "term": contest.term,
                    "name": line.name,
                    "parties": list(line.meaning[2]),
                    "votes": value,
                    "unopposed": value is None,
                    "runoff": after[0] if after else None,
                    "votes_page": votes_place[0],
                    "votes_box": votes_place[1],
                    "votes_flags": flags,
                    "total_page": total_place[0],
                    "total_box": total_place[1],
                    "total_flags": summed[1] if summed else 0,
                    "runoff_flags": after[1] if after else None,
                    "runoff_page": runoff_place[0],
                    "runoff_box": runoff_place[1],
                    "is_incumbent": line.incumbent,
                }
            )
        if first is None or (contest.year, contest.state) not in calendar_rows:
            continue
        opened, closed = calendar_rows[(contest.year, contest.state)]
        held = dated(contest.dates.get(first), contest.year)
        bundles: dict[tuple[str, str], list[Line]] = {}
        for line in contest.lines:
            scope = line.scope or line.meaning[0]
            if line.combined or not (first in line.values or line.tacit) or scope in (None, "Write-In"):
                continue
            bundles.setdefault((scope, line.name), []).append(line)
        if (
            held not in (opened, None)
            and runoff_column
            and any(runoff_column in line.values for bundle in bundles.values() for line in bundle)
            and dated(contest.dates.get("runoff"), contest.year) != closed
        ):
            raise ValueError(f"{contest.year} {contest.state} {contest.district}: a primary runoff off the calendar")
        for (scope, name), bundle in bundles.items():
            tallied = [line.values[first] for line in bundle if first in line.values]
            again = [line.values[runoff_column] for line in bundle if runoff_column and runoff_column in line.values]
            scoped = {
                line.totals.get(first) for (other, _), others in bundles.items() if other == scope for line in others
            }
            total = next(iter(scoped)) if len(scoped) == 1 and contest.layout[first] != "blanket" else None
            votes_place = (
                pinned(bundle[0], first)
                if len(bundle) == 1 and tallied and tallied[0][1] == DIGITIZED
                else (None, None)
            )
            runoff_place = (
                pinned(bundle[0], runoff_column)
                if runoff_column and len(bundle) == 1 and again and again[0][1] == DIGITIZED
                else (None, None)
            )
            total_place = (
                located(contest.year, Cell(total[2], total[3]))
                if total and total[2] is not None and total[3] is not None
                else (None, None)
            )
            book_primaries.append(
                {
                    "year": contest.year,
                    "state": contest.state,
                    "chamber": contest.chamber,
                    "district": district,
                    "term": contest.term,
                    "fec_id": None,
                    "name": name,
                    "party": scope,
                    "write_in": name == "Scattered" or all(line.meaning[1] for line in bundle),
                    "is_incumbent": any(line.incumbent for line in bundle),
                    "votes": sum(amount for amount, _ in tallied) if tallied else None,
                    "unopposed": not tallied,
                    "runoff": sum(amount for amount, _ in again) if again else None,
                    "flag": None,
                    "held": None if held in (opened, None) else held,
                    "votes_flags": (tallied[0][1] if len(bundle) == 1 else DERIVED) if tallied else 0,
                    "votes_page": votes_place[0],
                    "votes_box": votes_place[1],
                    "runoff_flags": (again[0][1] if len(bundle) == 1 else DERIVED) if again else None,
                    "runoff_page": runoff_place[0],
                    "runoff_box": runoff_place[1],
                    "total_flags": total[1] if total else DERIVED,
                    "total_page": total_place[0],
                    "total_box": total_place[1],
                }
            )
    dates = pl.concat(
        [
            dates,
            pl.DataFrame(
                [(year, state, None, opened, closed) for (year, state), (opened, closed) in calendar_rows.items()],
                schema=dates.schema,
                orient="row",
            ),
        ]
    )
    if dates.select("year", "state", "chamber").is_duplicated().any():
        raise ValueError(
            f"primary dates repeat: {dates.filter(dates.select('year', 'state', 'chamber').is_duplicated())}"
        )
    dates.write_parquet(PARSED / "dates.parquet")
    for year, pages in PRESIDENTIAL_PAGES.items():
        pdf = books[year]
        sheets: dict[str, list[tuple[int, list[scan.Word]]]] = {}
        footers: dict[str, tuple[int, list[scan.Word]]] = {}
        guides: dict[int, tuple[float, float]] = {}
        for page in pages:
            words = scan.words(pdf.path, page, PARSED / "ocr" / f"{pdf.sha256}-{page}.tsv")
            layers[(year, page)] = scan.layer(pdf.path, page)
            votes_x = statistics.median([w[1][2] for w in words if re.fullmatch(r"\d{1,3}(,\d{3})+", w[0])])
            guides[page] = (
                statistics.median(
                    [
                        w[1][0]
                        for w in words
                        if w[0] in ("R", "D", "LBT", "REF", "NL", "GRN") and w[1][2] < votes_x - 150
                    ]
                ),
                votes_x,
            )
            where = None
            for line in scan.lines(words, scan.skew(words)):
                text = " ".join(w[0] for w in line)
                if not re.search(r"\d", text) and (stated := state_of(text)) is not None:
                    where = stated
                    if "ontinued" not in text:
                        sheets[where] = []
                elif where is not None and re.search(r"(?i)total\W+(state\W+)?votes", text):
                    footers[where] = (page, line)
                    where = None
                elif where is not None and re.search(r"[A-Za-z]{2}.*\d", text):
                    sheets[where].append((page, line))
        if sheets.keys() != footers.keys():
            raise ValueError(f"{year} presidential tables without totals: {sheets.keys() ^ footers.keys()}")
        for where, entries in sheets.items():
            parts: list[tuple[int, str, scan.Box, str, scan.Box, scan.Box, scan.Box]] = []
            for page, line in entries:
                party_x, votes_x = guides[page]
                low, high = min(w[1][1] for w in line), max(w[1][3] for w in line)
                naming = [(w[0], w[1]) for w in line if w[1][2] < party_x - 10 and w[0] != "*"]
                parts.append(
                    (
                        page,
                        " ".join(text for text, _ in naming),
                        bound(naming),
                        " ".join(w[0] for w in line if party_x - 30 <= w[1][0] < party_x + 200),
                        (int(party_x) - 20, low - 6, int(party_x) + 210, high + 6),
                        (int(party_x) + 210, low - 8, int(votes_x) + 30, high + 8),
                        (int(votes_x) + 45, low - 8, int(votes_x) + 260, high + 8),
                    )
                )
            closing_page, closing_line = footers[where]
            closing_box = bound([(w[0], w[1]) for w in closing_line if re.search(r"\d", w[0])])
            reach = (closing_box[0] - 30, closing_box[1] - 8, closing_box[2] + 20, closing_box[3] + 8)
            pools = [
                (
                    [
                        "".join(
                            w[0] for w in line if w[1][0] >= guides[page][0] + 200 and w[1][2] <= guides[page][1] + 40
                        ),
                        layered(year, page, votes_box),
                    ],
                    [" ".join(w[0] for w in line if w[1][0] > guides[page][1] + 40), layered(year, page, share_box)],
                )
                for (page, line), (_, _, _, _, _, votes_box, share_box) in zip(entries, parts, strict=True)
            ]
            pools = [
                ([text.replace("$", "5") for text in votes], [text.replace("$", "5") for text in shares])
                for votes, shares in pools
            ]
            stamped = [
                "".join(w[0] for w in closing_line if re.search(r"\d", w[0])),
                layered(year, closing_page, reach),
            ]
            kept = [index for index, (votes, _) in enumerate(pools) if not any(text.startswith("[") for text in votes)]
            settled = balance([pools[index] for index in kept], stamped)
            if settled is None:
                wanted_boxes: dict[int, list[scan.Box]] = {closing_page: [reach]}
                for page, *_, votes_box, share_box in parts:
                    wanted_boxes.setdefault(page, []).extend([votes_box, share_box])
                heard_again = {
                    (page, box): texts
                    for page, boxes in wanted_boxes.items()
                    for box, texts in zip(boxes, cached(year, page, [("digits", box) for box in boxes]), strict=True)
                }
                settled = balance(
                    [
                        (votes + heard_again[(page, votes_box)], shares + heard_again[(page, share_box)])
                        for index, ((votes, shares), (page, *_, votes_box, share_box)) in enumerate(
                            zip(pools, parts, strict=True)
                        )
                        if index in kept
                    ],
                    stamped + heard_again[(closing_page, reach)],
                )
            if settled is None:
                raise ValueError(f"{year} {where}: presidential votes do not add up")
            _, whole, amounts, skipped = settled
            settled_values = {
                index: (amount, DERIVED if position == skipped else DIGITIZED)
                for position, (index, amount) in enumerate(zip(kept, amounts, strict=True))
            }
            combined_lines = {
                index
                for index, (_, line) in enumerate(entries)
                if re.search(r"Co\w{1,3}ined", " ".join(w[0] for w in line))
            }
            for index in combined_lines:
                nested = list(range(index + 1, next((other for other in kept if other > index), len(entries))))
                split = (
                    balance(
                        [([text.strip("[]") for text in pools[other][0]], []) for other in nested],
                        [f"{settled_values[index][0]:,}"],
                    )
                    if nested
                    else None
                )
                if split is None:
                    raise ValueError(f"{year} {where}: a fused presidential line does not split by party")
                for position, other in enumerate(nested):
                    settled_values[other] = (split[2][position], DERIVED if position == split[3] else DIGITIZED)
            total_place = located(year, Cell(closing_page, closing_box))
            for index, (page, spoken, name_box, code, code_box, votes_box, _) in enumerate(parts):
                if index in combined_lines:
                    continue
                guesses = [
                    found
                    for text in [layered(year, page, code_box), code]
                    if (found := decoded(text.strip("*"), year)) is not None
                ]
                if len(guesses) < 2 or guesses[0] != guesses[1]:
                    guesses += [
                        found
                        for text in cached(year, page, [("name", code_box)])[0]
                        if (found := decoded(text, year)) is not None
                    ]
                if not guesses and re.match(r"(?i)\W*none of", spoken):
                    guesses.append((None, False, ()))
                if not guesses:
                    raise ValueError(f"{year} {where}: a presidential party label that could not be read: {code}")
                label = max(guesses, key=lambda guess: (guesses.count(guess), -guesses.index(guess)))
                cleaned_names: list[str] = []
                for text in (html.unescape(layered(year, page, name_box)), spoken):
                    cleaned = tidy(text, True)
                    for pattern, replacement in MENDS:
                        cleaned = pattern.sub(replacement, cleaned)
                    cleaned = SPLIT_INITIAL.sub(
                        lambda match: match[0] if match[2] in PARTICLES else match[1] + match[2], cleaned
                    )
                    if re.search(r"[A-Z][a-z]{2}", cleaned):
                        cleaned_names.append(cleaned)
                chosen = min(
                    cleaned_names,
                    key=lambda text: ("," not in text, len(FLAWED.findall(text)), cleaned_names.index(text)),
                )
                last, comma, rest = chosen.partition(",")
                printed_name = (
                    NOTA
                    if re.match(r"(?i)\W*none of", spoken)
                    else " ".join(piece.strip(" ,*") for piece in (rest, last) if piece.strip(" ,*"))
                    if comma
                    else chosen.strip(" *")
                )
                if (year, where, printed_name) in RENAMED:
                    renamed.add((year, where, printed_name))
                    printed_name = RENAMED[(year, where, printed_name)]
                votes_place = located(year, Cell(page, votes_box))
                output.append(
                    {
                        "year": year,
                        "state": where,
                        "chamber": "president",
                        "district": None,
                        "term": "full",
                        "name": printed_name,
                        "parties": list(label[2]),
                        "votes": settled_values[index][0],
                        "unopposed": False,
                        "runoff": None,
                        "votes_page": votes_place[0] if settled_values[index][1] == DIGITIZED else None,
                        "votes_box": votes_place[1] if settled_values[index][1] == DIGITIZED else None,
                        "votes_flags": settled_values[index][1],
                        "total_page": total_place[0],
                        "total_box": total_place[1],
                        "total_flags": DIGITIZED,
                        "runoff_flags": None,
                        "runoff_page": None,
                        "runoff_box": None,
                        "is_incumbent": None,
                    }
                )
            if whole != sum(amounts):
                raise ValueError(f"{year} {where}: presidential total {whole} differs from {sum(amounts)}")
    if unmatched := {key for key in RENAMED if key[0] >= 1994} - renamed:
        raise ValueError(f"renamings never matched: {sorted(unmatched)}")
    squeezed = {text.replace(" ", ""): code for text, code in named.items()}
    clerk: list[dict[str, object]] = []
    tallies: dict[tuple[str, str, int | None], int] = {}
    state: str | None = None
    office: str | None = None
    term, district, last = (
        "full",
        None,
        int(
            re.findall(
                r"Pages:\s+(\d+)",
                subprocess.run(["pdfinfo", str(compendium.path)], capture_output=True, text=True, check=True).stdout,
            )[0]
        ),
    )
    for page in range(1, last + 1):
        if page > last:
            break
        words = [
            (text, box)
            for text, box in scan.layer(compendium.path, page)
            if not re.fullmatch(r"[.…]+", text) and (len(text) < 3 or box[3] - box[1] < box[2] - box[0])
        ]
        grouped: list[list[tuple[str, scan.Box]]] = []
        for word in sorted(words, key=lambda word: (word[1][1] + word[1][3], word[1][0])):
            if grouped and abs(word[1][1] + word[1][3] - grouped[-1][0][1][1] - grouped[-1][0][1][3]) < 24:
                grouped[-1].append(word)
            else:
                grouped.append([word])
        margin: int | None = None
        for row in grouped:
            row.sort(key=lambda word: word[1][0])
            bare = re.sub(r"\s", "", " ".join(text for text, _ in row)).replace("—Continued", "")
            if bare.replace("—CONTINUED", "") in squeezed:
                if squeezed[bare.replace("—CONTINUED", "")] != state:
                    state, office, term, district = squeezed[bare.replace("—CONTINUED", "")], None, "full", None
                    if state == "VI":
                        last = page
                continue
            if state is None:
                continue
            if bare.startswith(("RecapitulationofVotesCastin", "FOR")):
                office = STATISTICS_OFFICES.get(bare.replace("—CONTINUED", ""))
                district = 0 if office == "house" and state in ("AS", "DC", "GU", "MP", "PR", "VI") else district
                continue
            if office is None:
                continue
            if bare.startswith("(For"):
                term = "unexpired" if bare.startswith("(Forunexpired") else "full"
                continue
            if bare == "ATLARGE":
                district = 0
                continue
            text, box = row[-1]
            label = " ".join(text for text, _ in row[:-1])
            if (
                box[2] < 2300
                or not re.search(r"[A-Za-z]{2}", label)
                or not re.fullmatch(r"\d{1,3}(,\d{3})*|\(1\)", text)
            ):
                if re.search(r"\d,\d", label + text):
                    raise ValueError(f"Clerk 2024 page {page}: unread line {label} {text}")
                continue
            start = row[1 if re.fullmatch(r"\d+\.", row[0][0]) else 0][1][0]
            if numbered := re.match(r"(\d+)\. (.*)", label):
                district, label = int(numbered[1]), numbered[2]
            votes = None if text == "(1)" else int(text.replace(",", ""))
            contested = (state, office, district if office == "house" else None)
            if label == "Total":
                if votes != tallies.get(contested):
                    raise ValueError(
                        f"Clerk 2024 {contested}: the lines add up to {tallies.get(contested)}, not {votes}"
                    )
                continue
            tallies[contested] = tallies.get(contested, 0) + (votes or 0)
            if label in STATISTICS_UNCOUNTED or (state, office) in OWNED:
                continue
            segments = label.split(", ")
            if label in STATISTICS_WRITE_INS:
                name, segments = "Scattered", ["Write-in"]
            elif label == NOTA:
                name, segments = NOTA, []
            elif margin is not None and start > margin + 20 or margin is None and len(segments) == 1:
                name = str(clerk[-1]["name"])
            else:
                margin = start
                split = next(
                    index
                    for index in range(1, len(segments) + 1)
                    if index == len(segments) or segments[index].rstrip(".").lower() not in SUFFIXES
                )
                name, segments = ", ".join(segments[:split]), segments[split:]
                name = STATISTICS_NAMES.get(name, name)
            parties = [
                "Write-In"
                if party.startswith("Write-in")
                else "Independent Party"
                if (state, party) == ("CT", "Independent") and name == clerk[-1]["name"]
                else STATISTICS_LABELS.get(party, party)
                for party in segments
            ]
            final = (state, district) in FINAL_ROUNDS and office == "house"
            figure, place = (votes, located(2024, Cell(page, box))) if votes is not None else (None, (None, None))
            clerk.append(
                {
                    "year": 2024,
                    "state": state,
                    "chamber": office,
                    "district": district if office == "house" else None,
                    "term": term if office == "senate" else "full",
                    "name": name,
                    "parties": parties,
                    "votes": None if final else figure,
                    "unopposed": votes is None,
                    "rcv2": figure if final else None,
                    "flag": "W" if RANKED_WINNERS.get((state, district)) == name and office == "house" else None,
                    "votes_page": None if final else place[0],
                    "votes_box": None if final else place[1],
                    "votes_flags": None if final or votes is None else DIGITIZED,
                    "rcv2_page": place[0] if final else None,
                    "rcv2_box": place[1] if final else None,
                    "rcv2_flags": DIGITIZED if final else None,
                    "total_flags": DERIVED,
                    "is_incumbent": None,
                }
            )
    layout = subprocess.run(
        ["pdftotext", "-layout", str(compendium.path), "-"], capture_output=True, text=True, check=True
    ).stdout
    recapitulated: dict[tuple[str, str, int | None], int] = {}
    state = None
    for line in layout.splitlines():
        if heading := re.match(r"RecapitulationofVotesCastin(.*?)(—Continued)?$", re.sub(r"\s", "", line)):
            state = squeezed[heading[1].upper()]
            continue
        figures = [
            figure for figure in re.findall(r"\d{1,3}(?:,\d{3})+|\b\d+\b", line) if "," in figure or int(figure) >= 100
        ]
        if state is None or not figures:
            continue
        if numeral := re.match(r"\s*(\d+)(?:st|d|th) district", line):
            recapitulated[(state, "house", int(numeral[1]))] = int(figures[-1].replace(",", ""))
        elif re.match(r"\s*At large", line):
            recapitulated[(state, "house", 0)] = int(figures[-1].replace(",", ""))
        elif re.match(r"\s*Senator", line):
            recapitulated[(state, "senate", None)] = int(figures[-1].replace(",", ""))
    for contested, printed_total in recapitulated.items():
        if tallies.get(contested) != printed_total:
            raise ValueError(
                f"Clerk 2024 {contested}: the lines add up to {tallies.get(contested)}, not {printed_total}"
            )
    if [chamber for _, chamber, _ in tallies].count("house") != 441 or len(tallies) != 474:
        raise ValueError(f"Clerk 2024: {len(tallies)} races read")
    registry: dict[tuple[int, str, str], list[tuple[str, int, int, str, str]]] = {}
    surnames: dict[str, list[set[str]]] = {}
    roster: dict[tuple[str, str], list[tuple[str, int, set[str]]]] = {}
    for year, url in MASTERS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            for record in archive.read("cn.txt").decode("latin-1").splitlines():
                fec_id, filed, _, elected, place, office, numbered = record.split("|")[:7]
                if office in ("H", "S"):
                    surname, _, given_names = filed.partition(",")
                    family = [word for word in slug(surname).split("-") if word not in SUFFIXES]
                    surnames.setdefault(fec_id, []).append({*family, "".join(family)})
                    if year >= 2024:
                        roster.setdefault((place, office), []).append(
                            (fec_id, int(numbered or 0), set(slug(re.sub(NICKNAME, " ", filed)).split("-")))
                        )
                    registry.setdefault((min(year, 2024), place, office), []).append(
                        (
                            fec_id,
                            int(numbered or 0),
                            int(elected or 0),
                            "".join(family),
                            slug(re.sub(NICKNAME, " ", given_names))[:1],
                        )
                    )
    reused = {
        fec_id
        for fec_id, sets in surnames.items()
        if any(
            not one & other
            and max(difflib.SequenceMatcher(None, mine, theirs).ratio() for mine in one for theirs in other) < 0.8
            for one in sets
            for other in sets
        )
    }
    enrolled = 0
    repaired: list[tuple[str, str]] = []
    for row in [*output, *book_primaries, *clerk]:
        if row["chamber"] == "president" or row["name"] in ("Scattered", NOTA, VACANCY):
            continue
        office = "S" if row["chamber"] == "senate" else "H"
        if 1994 <= int(str(row["year"])) <= 2000:
            words = str(row["name"]).split(" ")
            known = {
                (family, given)
                for _, _, _, family, given in registry.get((int(str(row["year"])), str(row["state"]), office), [])
            }
            for position, word in enumerate(words):
                if (
                    len(word) < 3
                    or re.search(r"[()\"“”]", word)
                    or any(family == slug(word).replace("-", "") for family, _ in known)
                ):
                    continue
                fixes = {
                    variant
                    for seen, meant in MISREAD_LETTERS
                    for index in range(len(word))
                    if word.startswith(seen, index)
                    and (index == 0 or (seen, meant) != ("l", "I"))
                    and (variant := word[:index] + meant + word[index + len(seen) :])
                    and (slug(variant).replace("-", ""), slug(words[0])[:1]) in known
                }
                if len(fixes) == 1:
                    repaired.append((str(row["name"]), fixed := fixes.pop()))
                    words[position] = fixed
                    if fixed.startswith("H") and word.startswith("l") and position and words[position - 1] == "I.":
                        words[position - 1] = ""
            row["name"] = " ".join(word for word in words if word)
        spelled = [word for word in slug(re.sub(NICKNAME, " ", str(row["name"]))).split("-") if word not in SUFFIXES]
        initial = spelled[0][:1] if len(spelled) > 1 else slug(str(row["name"]))[:1]
        numbers = (0, 1) if office == "H" and not row["district"] else (int(str(row["district"] or 0)),)
        tails = ["".join(spelled[-size:]) for size in range(1, min(len(spelled), 4) + 1)]
        filings = [
            (fec_id, number, elected == row["year"], given == initial, family in tails)
            for fec_id, number, elected, family, given in registry.get(
                (int(str(row["year"])), str(row["state"]), office), []
            )
            if fec_id not in reused
            and (
                family in tails
                or (
                    elected == row["year"]
                    and given == initial
                    and max((difflib.SequenceMatcher(None, tail, family).ratio() for tail in tails), default=0) >= 0.85
                )
            )
        ]
        ranked = sorted(
            {
                (current, same, fec_id)
                for fec_id, number, current, same, _ in filings
                if number in numbers and (current or same)
            },
            reverse=True,
        ) or [(True, True, fec_id) for fec_id, _, current, same, exact in filings if current and same and exact]
        chosen_ids = {fec_id for current, same, fec_id in ranked if (current, same) == ranked[0][:2]}
        if len(chosen_ids) > 1 and row["year"] == 2024:
            chosen_ids = {min(chosen_ids & workbook_ids or chosen_ids)}
        if not chosen_ids and row["year"] == 2024:
            chosen_ids = {
                fec_id
                for fec_id, number, words in roster.get((str(row["state"]), office), [])
                if number in numbers and spelled[-1] in words
            }
        row["fec_id"] = chosen_ids.pop() if len(chosen_ids) == 1 else STATISTICS_IDS.get(str(row["name"]))
        enrolled += row["fec_id"] is not None
    print(f"{enrolled} of {len(output) + len(book_primaries) + len(clerk)} book lines carry an FEC candidate ID")
    print(f"{len(repaired)} misread surnames repaired: {sorted(set(repaired))}")
    pl.DataFrame(
        output,
        schema={
            "year": pl.Int64,
            "state": pl.String,
            "chamber": pl.String,
            "district": pl.Int64,
            "term": pl.String,
            "fec_id": pl.String,
            "name": pl.String,
            "parties": pl.List(pl.String),
            "votes": pl.Int64,
            "unopposed": pl.Boolean,
            "runoff": pl.Int64,
            "votes_page": pl.Int64,
            "votes_box": pl.List(pl.Float64),
            "votes_flags": pl.Int16,
            "total_page": pl.Int64,
            "total_box": pl.List(pl.Float64),
            "total_flags": pl.Int16,
            "runoff_flags": pl.Int16,
            "runoff_page": pl.Int64,
            "runoff_box": pl.List(pl.Float64),
            "is_incumbent": pl.Boolean,
        },
    ).write_parquet(PARSED / "books.parquet")
    pl.DataFrame(
        book_primaries,
        schema={
            "year": pl.Int64,
            "state": pl.String,
            "chamber": pl.String,
            "district": pl.Int64,
            "term": pl.String,
            "fec_id": pl.String,
            "name": pl.String,
            "party": pl.String,
            "write_in": pl.Boolean,
            "is_incumbent": pl.Boolean,
            "votes": pl.Int64,
            "unopposed": pl.Boolean,
            "runoff": pl.Int64,
            "flag": pl.String,
            "held": pl.Date,
            "votes_flags": pl.Int16,
            "votes_page": pl.Int64,
            "votes_box": pl.List(pl.Float64),
            "runoff_flags": pl.Int16,
            "runoff_page": pl.Int64,
            "runoff_box": pl.List(pl.Float64),
            "total_flags": pl.Int16,
            "total_page": pl.Int64,
            "total_box": pl.List(pl.Float64),
        },
    ).write_parquet(PARSED / "books_primaries.parquet")
    pl.DataFrame(
        clerk,
        schema={
            "year": pl.Int64,
            "state": pl.String,
            "chamber": pl.String,
            "district": pl.Int64,
            "term": pl.String,
            "fec_id": pl.String,
            "name": pl.String,
            "parties": pl.List(pl.String),
            "votes": pl.Int64,
            "unopposed": pl.Boolean,
            "rcv2": pl.Int64,
            "flag": pl.String,
            "votes_page": pl.Int64,
            "votes_box": pl.List(pl.Float64),
            "votes_flags": pl.Int16,
            "rcv2_page": pl.Int64,
            "rcv2_box": pl.List(pl.Float64),
            "rcv2_flags": pl.Int16,
            "total_flags": pl.Int16,
            "is_incumbent": pl.Boolean,
        },
    ).write_parquet(PARSED / "clerk.parquet")
    pl.DataFrame(
        [
            (year, page, f"pages/{SOURCE}/{books.get(year, compendium).sha256}-{page}.png", width, height)
            for (year, page), (width, height) in sorted(dimensions.items())
        ],
        schema={"year": pl.Int64, "page": pl.Int64, "image": pl.String, "width": pl.Int64, "height": pl.Int64},
        orient="row",
    ).write_parquet(PARSED / "pages.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "president.parquet")
    known = (
        frame.filter(pl.col("fec_id").is_not_null())
        .group_by("name", "fec_id")
        .len()
        .sort("len", "fec_id", descending=[True, False])
        .unique("name", keep="first", maintain_order=True)
        .select("name", known="fec_id")
    )
    frame = frame.join(known, on="name", how="left").with_columns(
        key=pl.when(pl.col("fec_id").is_not_null())
        .then(pl.col("fec_id"))
        .when(pl.col("known").is_not_null())
        .then(pl.col("known"))
        .when(pl.col("parties").list.contains("Write-In") | (pl.col("name") == NOTA))
        .then(None)
        .otherwise(pl.col("name").map_elements(slug, return_dtype=pl.String))
    )
    pages = pl.read_parquet(PARSED / "pages.parquet")
    books = pl.read_parquet(PARSED / "books.parquet")
    congress = pl.concat(
        [
            pl.read_parquet(PARSED / "congress.parquet"),
            books.filter(pl.col("chamber") != "president"),
            pl.read_parquet(PARSED / "clerk.parquet"),
        ],
        how="diagonal_relaxed",
    ).with_columns(
        parties=pl.struct("year", "state", "name", "parties").map_elements(
            lambda row: REORDERED.get(
                (row["year"], row["state"], row["name"]),
                [part for label in row["parties"] for part in COMPOUND.get(label, [label])],
            ),
            return_dtype=pl.List(pl.String),
        ),
        key=pl.when(pl.col("fec_id").is_not_null())
        .then(pl.col("fec_id"))
        .when(pl.col("parties").list.contains("Write-In") | pl.col("name").is_in([NOTA, VACANCY, "Scattered"]))
        .then(None)
        .otherwise(pl.concat_str("state", pl.col("name").map_elements(slug, return_dtype=pl.String), separator=":")),
    )
    primaries = (
        pl.concat(
            [pl.read_parquet(PARSED / "primaries.parquet"), pl.read_parquet(PARSED / "books_primaries.parquet")],
            how="diagonal_relaxed",
        )
        .with_columns(
            fec_id=pl.concat_str(pl.col("year").cast(pl.String), "fec_id", "name", separator=":").replace_strict(
                REUSED_IDS, default=pl.col("fec_id"), return_dtype=pl.String
            )
        )
        .with_columns(
            key=pl.when(pl.col("fec_id").is_not_null())
            .then(pl.col("fec_id"))
            .when(
                pl.col("name").is_in(
                    ["Scattered", "Scattered (Nonqualified Candidates)", "All Others", NOTA, "None of the Above"]
                )
                | pl.col("name").str.starts_with("Opportunity to Ballot")
            )
            .then(None)
            .otherwise(pl.concat_str("state", pl.col("name").map_elements(slug, return_dtype=pl.String), separator=":"))
        )
    )
    nominations = (
        pl.read_parquet(PARSED / "nominations.parquet")
        .with_columns(
            kind=pl.when(
                pl.col("name").str.contains(r'(?i)^"?(uncommitted|no preference|uninstructed|to be uncommitted)')
            )
            .then(pl.lit("uncommitted"))
            .when(pl.col("name").is_in(["None of the Names Shown", "No Candidate", "Candidate None"]))
            .then(pl.lit("none_of_the_above"))
            .when(pl.col("name").is_in(["Scattered", "Scattering", "All Others", "Miscellaneous", "Other"]))
            .then(pl.lit("write_in"))
        )
        .with_columns(
            key=pl.when(pl.col("kind").is_null()).then(
                pl.coalesce(
                    pl.concat_str(pl.col("year").cast(pl.String), "party", "fec_id", separator=":").replace_strict(
                        MISATTRIBUTED, default=pl.col("fec_id")
                    ),
                    pl.lit("us:") + pl.col("name").map_elements(slug, return_dtype=pl.String),
                )
            )
        )
    )
    frame = pl.concat(
        [
            frame,
            books.filter(pl.col("chamber") == "president")
            .with_columns(
                ballot=pl.col("name"),
                name=pl.concat_str(pl.col("year").cast(pl.String), "name", separator=":").replace_strict(
                    {f"{year}:{printed}": nominee for (year, printed), nominee in ELECTED_NOMINEES.items()},
                    default=pl.col("name").replace(NOMINEES),
                ),
            )
            .join(
                nominations.filter(pl.col("fec_id").is_not_null()).group_by("name").agg(known=pl.col("fec_id").min()),
                on="name",
                how="left",
            )
            .with_columns(handle=pl.col("name").map_elements(slug, return_dtype=pl.String))
            .with_columns(
                key=pl.when(pl.col("parties").list.contains("Write-In") | (pl.col("name") == NOTA))
                .then(None)
                .when(pl.col("handle").is_in(list(LINKS)))
                .then(pl.col("handle"))
                .otherwise(pl.coalesce("known", pl.col("name").replace_strict(BOOK_CANDIDATES, default=None), "handle"))
            ),
        ],
        how="diagonal_relaxed",
    )
    calendar = {
        (year, state, chamber): (opened, closed)
        for year, state, chamber, opened, closed in pl.read_parquet(PARSED / "dates.parquet").iter_rows()
    }
    fec_ids = set(frame["fec_id"].drop_nulls()) | set(congress["fec_id"].drop_nulls())
    days = {year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in (*FILES, *BOOKS)}

    with connect() as conn:
        states: dict[str, tuple[UUID, UUID, str, str]] = {
            iso.removeprefix("US-"): (unit, area, text, code)
            for text, iso, unit, area, code in conn.execute(
                """select n.text, p.iso_code, u.id, u.area_id, u.code from reporting_unit u
                   join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        electors: dict[tuple[int, str], int] = {}
        college: dict[tuple[int, str], dict[UUID, int]] = {}
        for year, iso, person, measure, value in conn.execute(
            """select split_part(k.value, ':', 1)::int, p.iso_code, c.person_id, v.measure, v.value::int
               from result_value v
               join result_set rs on rs.k = v.result_set_k
               join external_id k on k.entity_id = rs.id and k.scheme = 'us.nara.result_set'
               join reporting_unit u on u.k = v.unit_k
               join polity p on p.id = u.polity_id
               left join contest_option o on o.k = v.option_k
               left join candidacy c on c.option_id = o.id and c.role = 'head'
               where k.value ~ '^(198[4-9]|199[0-9]|20[0-9]{2}):president$' and v.measure in (2, 11) and v.value > 0"""
        ).fetchall():
            if measure == 2:
                electors[(year, iso.removeprefix("US-"))] = value
            else:
                college.setdefault((year, iso.removeprefix("US-")), {})[person] = value
        districts: dict[str, tuple[UUID, UUID, str, Range[date]]] = {
            code: (area, unit, text, valid)
            for code, area, unit, text, valid in conn.execute(
                """select u.code, u.area_id, u.id, n.text, u.valid from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official'
                   where u.scheme_code = 'us.cd'"""
            ).fetchall()
        }
        fec_persons: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'fec.candidate'").fetchall()
        )
        named: dict[str, set[UUID]] = {}
        for text, person in conn.execute(
            """select n.text, x.entity_id from external_id x
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'fec.candidate' and x.value like 'P%'"""
        ).fetchall():
            named.setdefault(slug(text), set()).add(person)
        figures = {text: next(iter(people)) for text, people in named.items() if len(people) == 1}
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by state, 1984–2024, House and Senate general results, 1982–2024, and primaries, 1994–2022",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        for (_, state, mangled), fixed in RENAMED.items():
            for name_id, holder, preferred in conn.execute(
                """select n.id, n.entity_id, n.is_preferred from external_id x
                   join name n on n.entity_id = x.entity_id and n.text = %s
                   where x.scheme = 'us.fec.person' and x.value = %s""",
                [mangled, f"{state}:{slug(mangled)}"],
            ).fetchall():
                cs.add(
                    "name", id=name_id, entity_id=holder, text=fixed, lang="en", kind="official", is_preferred=preferred
                )
                cs.renamed[f"{state}:{slug(fixed)}"] = f"{state}:{slug(mangled)}"

        twins: dict[frozenset[UUID], tuple[UUID, UUID]] = {}
        for old, new, old_head, new_head, old_person, new_person, old_count, new_count, split in conn.execute(
            """with line as (
                 select o.id, o.k, o.contest_id, o.ballot_label, c.published_at, y.id as head, y.person,
                        (select count(*) from candidacy z where z.person_id = y.person) as count,
                        exists (select from external_id x where x.entity_id = y.person and x.scheme = 'fec.candidate'
                                and x.value = any(%s)) as split
                 from contest_option o join changeset c on c.id = o.changeset_id
                 left join lateral (
                   select cy.id, coalesce(m.into_id, cy.person_id) as person from candidacy cy
                   left join entity_merge m on m.entity_id = cy.person_id
                   where cy.option_id = o.id and cy.role = 'head'
                 ) y on true
                 where c.pipeline = 'us.fec'
               )
               select a.id, b.id, a.head, b.head, a.person, b.person, a.count, b.count, a.split or b.split
               from line a join line b on b.contest_id = a.contest_id and b.ballot_label = a.ballot_label
                 and b.published_at > a.published_at
               where not exists (select from result_value v join result_set r on r.k = v.result_set_k
                                 where r.contest_id = b.contest_id and v.option_k = b.k)""",
            [[key.split(":")[1] for key in REUSED_IDS]],
        ).fetchall():
            cs.merge(new, old)
            if old_head is not None and new_head is not None:
                cs.merge(new_head, old_head)
            if old_person is not None and new_person is not None and old_person != new_person and not split:
                twins[frozenset((old_person, new_person))] = (
                    (new_person, old_person)
                    if (old_count, new_person) >= (new_count, old_person)
                    else (old_person, new_person)
                )
        if len({person for pair in twins for person in pair}) != 2 * len(twins):
            raise ValueError("a person pairs with two others")
        for duplicate, survivor in twins.values():
            cs.merge(duplicate, survivor)
        fec_persons = {key: cs.survivors.get(person, person) for key, person in fec_persons.items()}
        for fec_id, handle in MOVED_IDS.items():
            fec_persons[fec_id] = cs.key(f"{SOURCE}.person", handle)
            cs.add("external_id", entity_id=fec_persons[fec_id], scheme="fec.candidate", value=fec_id)
        figures = {text: cs.survivors.get(person, person) for text, person in figures.items()}

        elections: dict[str, UUID] = {}

        def election(
            key: str, polity: UUID, kind: str, day: date, title: str, path: str, parent: UUID | None = None
        ) -> UUID:
            if key not in elections:
                elections[key] = cs.key("us.election", key)
                cs.add(
                    "election",
                    id=elections[key],
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(elections[key], title)
                cs.add("slug", path=path, entity_id=elections[key])
            return elections[key]

        us = cs.polity("US")
        office = cs.key("us.office", "president")
        bodies = {kind: cs.key("us.body", kind) for kind in ("electoral_college", "house", "senate")}
        if cs.minted & set(bodies.values()):
            raise ValueError("the electoral college, House, and Senate bodies must already exist")
        nara = cs.source("National Archives and Records Administration", "Electoral College results")
        systems: dict[str, UUID] = {}
        for key, (rules, text) in SYSTEMS.items():
            systems[key] = cs.key("us.electoral_system", key)
            cs.add("electoral_system", id=systems[key], polity_id=us, rules=rules, rules_version=1)
            name(systems[key], text)
        sources: dict[int, UUID] = {}
        artifacts: dict[int, UUID] = {}
        for year, path in (
            *FILES.items(),
            *((year, BOOKS[year]) for year in sorted(set(pages["year"]) & BOOKS.keys())),
        ):
            title = (
                "Official 2024 Presidential General Election Results"
                if year == 2024
                else f"Federal Elections {year if year in FILES else f'{year % 100:02d}'}"
            )
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=DOCUMENTS + path,
                license="Public domain",
            )
            artifacts[year] = cs.artifact(raw.cached(SOURCE, DOCUMENTS + path), sources[year])
        for year, url in CLERK.items():
            day = 2 + (1 - date(year, 11, 2).weekday()) % 7
            title = (
                f"Statistics of the {'Presidential and ' if year % 4 == 0 else ''}Congressional Election of "
                f"November {day}, {year}"
            )
            clerk = cs.source("Clerk of the House of Representatives", title)
            cs.add(
                "source",
                id=clerk,
                kind="official_body",
                publisher="Clerk of the House of Representatives",
                title=title,
                url=url,
                license="Public domain",
            )
            cs.artifact(raw.cached(SOURCE, url), clerk)
        statistics = cs.source("Clerk of the House of Representatives", STATISTICS_TITLE)
        cs.add(
            "source",
            id=statistics,
            kind="official_body",
            publisher="Clerk of the House of Representatives",
            title=STATISTICS_TITLE,
            url=STATISTICS,
            license="Public domain",
        )
        printed = {**artifacts, 2024: cs.artifact(raw.cached(SOURCE, STATISTICS), statistics)}
        canvasses: dict[str, tuple[UUID, UUID, dict[str | None, tuple[int, list[float]]]]] = {}
        for contest_key, (url, title, number, columns) in CANVASSES.items():
            pdf = raw.cached(SOURCE, url)
            canvass = cs.source("Wisconsin Elections Commission", title)
            cs.add(
                "source",
                id=canvass,
                kind="official_body",
                publisher="Wisconsin Elections Commission",
                title=title,
                url=url,
                license="Public record",
                notes=CANVASS_SOURCE,
            )
            artifact = cs.artifact(pdf, canvass)
            page = cs.page(artifact, number)
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{pdf.sha256}-{number}.png",
                width=width,
                height=height,
            )
            words = scan.layer(pdf.path, number)
            if f"DISTRICT {contest_key.rsplit(':', 1)[1]}" not in " ".join(text for text, _ in words):
                raise ValueError(f"{contest_key}: page {number} is not this district")
            row = next(at[1] for text, at in words if text == "Totals:")
            totals = [(int(text.replace(",", "")), at) for text, at in words if at[1] == row and text[0].isdigit()]
            read: dict[str | None, tuple[int, list[float]]] = {}
            for text, header in words:
                if header[1] < row and text in columns:
                    value, cell = min(
                        totals, key=lambda total, header=header: abs(total[1][0] + total[1][2] - header[0] - header[2])
                    )
                    read[columns[text]] = (
                        value,
                        [
                            cell[0] / width / 3,
                            cell[1] / height / 3,
                            cell[2] / width / 3,
                            cell[3] / height / 3,
                        ],
                    )
            if len(read) != len(columns) or sum(value for label, (value, _) in read.items() if label) != read[None][0]:
                raise ValueError(f"{contest_key}: the canvass columns do not add up: {read}")
            canvasses[contest_key] = canvass, page, read
        for year, number, image, width, height in pages.iter_rows():
            cs.add(
                "source_page",
                id=cs.page(printed[year], number),
                artifact_id=printed[year],
                page_number=number,
                image_key=image,
                width=width,
                height=height,
            )

        parties: dict[tuple[str, str], UUID | None] = {}
        titles: dict[tuple[str, str], str] = {}
        slogans: set[tuple[str, str]] = set()
        created: set[UUID] = set()
        lrl = cs.source("Minnesota Legislative Reference Library", LRL_TITLE)
        cs.add(
            "source",
            id=lrl,
            kind="official_body",
            publisher="Minnesota Legislative Reference Library",
            title=LRL_TITLE,
            url=LRL,
            license="Public record",
        )
        for state, label in (
            pl.concat(
                [
                    frame.select("state", "parties"),
                    congress.select("state", "parties"),
                    primaries.select("state", parties=pl.concat_list("party")),
                    nominations.select("state", parties=pl.concat_list("party")),
                ]
            )
            .explode("parties")
            .drop_nulls()
            .unique()
            .sort("state", "parties")
            .iter_rows()
        ):
            if label in INDEPENDENT or label == "Write-In":
                parties[(state, label)] = None
                continue
            normalized = label.replace(" Of ", " of ").replace(" And ", " and ")
            canonical = PARTIES.get(normalized, normalized)
            titles[(state, label)] = canonical
            national = canonical in NATIONAL
            if state == "NJ" and not national:
                parties[(state, label)] = None
                slogans.add((state, label))
                continue
            party = cs.key("us.party", slug(canonical) if national else f"{state}:{slug(canonical)}")
            parties[(state, label)] = party
            if party in created:
                continue
            created.add(party)
            cs.add("party", id=party, kind="party", polity_id=us if national else cs.polity(f"US-{state}"))
            name(party, canonical)
            if canonical in COLORS:
                cs.add("party_color", party_id=party, color=COLORS[canonical], valid_from=None)
            if canonical in FORMER_NAMES:
                former, start, end = FORMER_NAMES[canonical]
                cs.add(
                    "name",
                    entity_id=party,
                    text=former,
                    lang="en",
                    kind="official",
                    is_preferred=False,
                    valid_from=start,
                    valid_to=end,
                    source_id=lrl,
                )
            if canonical in AFFILIATES:
                cs.add(
                    "party_affiliation",
                    party_id=party,
                    parent_party_id=cs.key("us.party", slug(AFFILIATES[canonical])),
                    kind="regional_branch",
                    valid_from=None,
                )
        for folded, into in FOLDED.items():
            if cs.key("us.party", folded) != cs.key("us.party", into):
                cs.merge(cs.key("us.party", folded), cs.key("us.party", into))
        cs.renamed["republican-party-of-minnesota"] = "independent-republican"

        persons: dict[str, UUID] = {}
        by_name: dict[str, UUID] = {}
        names = (
            frame.filter(pl.col("key").is_not_null())
            .group_by("key", "name")
            .len()
            .sort("len", "name", descending=[True, False])
            .unique("key", keep="first", maintain_order=True)
        )
        for key, text in names.select("key", "name").iter_rows():
            if key in fec_persons:
                persons[key] = fec_persons[key]
            elif key in LINKS:
                persons[key] = cs.key("us.person", LINKS[key])
                if persons[key] in cs.minted - created:
                    raise ValueError(f"{key} links to a missing person {LINKS[key]}")
                if key in fec_ids:
                    cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            elif key in fec_ids and slug(text) in by_name:
                persons[key] = by_name[slug(text)]
                cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            else:
                persons[key] = cs.key("fec.candidate", key) if key in fec_ids else cs.key(f"{SOURCE}.person", key)
                created.add(persons[key])
                cs.add("person", id=persons[key])
                name(persons[key], text)
            by_name.setdefault(slug(text), persons[key])
        mates: dict[str, UUID] = {}
        for text in sorted(set(RUNNING_MATES.values())):
            mates[text] = by_name.get(slug(text)) or cs.key("us.person", slug(text))
            if mates[text] in cs.minted and mates[text] not in created:
                created.add(mates[text])
                cs.add("person", id=mates[text])
                name(mates[text], text)

        variants: dict[str, list[tuple[str, str]]] = {}
        for key, text, state in (
            congress.filter(pl.col("key").is_not_null())
            .group_by("key", "name")
            .agg(pl.col("state").first(), pl.col("votes").fill_null(0).sum(), pl.len(), pl.col("year").max())
            .sort("votes", "len", "year", "name", descending=[True, True, True, False])
            .select("key", "name", "state")
            .iter_rows()
        ):
            variants.setdefault(key, []).append((state, text))
        if ALIASES.keys() - variants.keys():
            raise ValueError(f"aliases never matched: {sorted(ALIASES.keys() - variants.keys())}")
        by_state: dict[tuple[str, str], UUID] = {}
        for key in sorted(variants, key=lambda key: (key not in fec_persons, key not in fec_ids, key)):
            state, text = variants[key][0]
            merged = next(
                (
                    by_state[(place, form)]
                    for place, other in variants[key]
                    for form in (slug(other), slug(re.sub(NICKNAME, " ", other)))
                    if (place, form) in by_state
                ),
                None,
            )
            if key in NAMESAKES:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if key in fec_persons and fec_persons[key] != persons[key]:
                    cs.renamed[str(persons[key])] = str(fec_persons[key])
                cs.add("person", id=persons[key])
                name(persons[key], NAMES.get(key, text))
                cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            elif key in fec_persons:
                persons[key] = fec_persons[key]
            elif key in LINKS:
                persons[key] = cs.key("us.person", LINKS[key])
                if persons[key] in cs.minted - created:
                    raise ValueError(f"{key} links to a missing person {LINKS[key]}")
                cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            elif (target := persons[ALIASES[key]] if key in ALIASES else merged) is not None:
                persons[key] = target
                if key in fec_ids:
                    cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            else:
                persons[key] = cs.key("fec.candidate", key) if key in fec_ids else cs.key(f"{SOURCE}.person", key)
                cs.add("person", id=persons[key])
                name(persons[key], NAMES.get(key, text))
            for place, other in variants[key]:
                for form in (slug(other), slug(re.sub(NICKNAME, " ", other))):
                    by_state.setdefault((place, form), persons[key])

        classes = {
            (year, state, term): SEATS[(year, state)] if term == "unexpired" else (3, 1, 2)[(year - 2004) % 6 // 2]
            for year, state, term in congress.filter(pl.col("chamber") == "senate")
            .select("year", "state", "term")
            .unique()
            .iter_rows()
        }
        owned: dict[str, set[int]] = {}
        for (_, state, _), number in classes.items():
            owned.setdefault(state, set()).add(number)
        specials = {(year, state) for year, state, term in classes if term == "unexpired"}
        if len(owned) != 50 or any(len(numbers) != 2 for numbers in owned.values()) or SEATS.keys() != specials:
            raise ValueError(f"Senate classes do not add up: {owned}, {SEATS.keys() ^ specials}")
        senate_seats: dict[tuple[str, int], UUID] = {}
        plans: list[tuple[UUID, date, date | None]] = conn.execute(
            "select id, valid_from, valid_to from boundary_set where scheme_code = 'us.cd' order by valid_from desc"
        ).fetchall()
        for state, numbers in sorted(owned.items()):
            for number in sorted(numbers):
                senate_seats[(state, number)] = cs.key("us.seat", f"senate:{state}:{number}")
                cs.add(
                    "seat",
                    id=senate_seats[(state, number)],
                    body_id=bodies["senate"],
                    district_area_id=states[state][1],
                    label=f"{states[state][2]} Class {'I' * number}",
                    voting=True,
                    valid_from=None,
                )
        delegates: dict[str, UUID] = {}
        for state, label in DELEGATE_SEATS.items():
            delegates[state] = cs.key("us.seat", f"house:{state}")
            cs.add(
                "seat",
                id=delegates[state],
                body_id=bodies["house"],
                district_area_id=states[state][1],
                label=label,
                voting=False,
                valid_from=None,
            )

        used: set[tuple[int, str]] = set()
        for year in sorted(frame["year"].unique()):
            day = days[year]
            umbrella = election(
                str(day), us, "general", day, f"{year} United States general election", f"us/{day}-general"
            )
            national_key = f"{day}:president"
            national = cs.key("us.contest", national_key)
            cs.add(
                "contest",
                id=national,
                election_id=umbrella,
                kind="office",
                round_number=1,
                default_result_kind="counted",
                office_id=office,
                body_id=bodies["electoral_college"],
                magnitude=sum(seats for (held, _), seats in electors.items() if held == year),
            )
            name(national, f"{year} United States presidential election")
            cs.add("slug", path=f"us/{day}-general/president", entity_id=national)
            national_set = cs.key(f"{SOURCE}.result_set", national_key)
            cs.add(
                "result_set",
                id=national_set,
                election_id=umbrella,
                contest_id=national,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=0,
            )

            won: dict[tuple[str, str], int] = {}
            mains: dict[str, UUID | None] = {}
            for (state,), lines in frame.filter(pl.col("year") == year).group_by("state", maintain_order=True):
                unit, area, state_name, _ = states[state]
                prefix, count, since = DISTRICTS.get(state, ("", 0, year))
                count = count if year >= since else 0
                at_large = 2 if count else electors[(year, state)]
                parent = election(
                    f"{day}:{state}",
                    cs.polity(f"US-{state}"),
                    "general",
                    day,
                    f"{year} {state_name} general election",
                    f"us/{state.lower()}/{day}-general",
                    umbrella,
                )
                contest_key = f"{day}:{state}:president"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=parent,
                    kind="elector_slate",
                    round_number=1,
                    default_result_kind="counted",
                    body_id=bodies["electoral_college"],
                    district_area_id=area,
                    magnitude=at_large,
                    allocation_parent_id=national,
                    electoral_system_id=systems["winner_take_all"],
                )
                name(contest, f"{year} presidential election in {state_name}")
                cs.add("slug", path=f"us/{state.lower()}/{day}-general/president", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=parent,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[year],
                    is_default=True,
                    completeness=1,
                )
                if (year, state) in CAVEATS:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEATS[(year, state)])

                cells: list[tuple[UUID | None, int, int | None, int | None, list[float] | None]] = []
                seen: set[UUID] = set()
                totals: dict[str, int] = {}
                roots: dict[str, UUID] = {}
                for (key,), group in lines.filter(pl.col("key").is_not_null()).group_by("key", maintain_order=True):
                    rows = group.sort("votes", descending=True).rows(named=True)
                    option_key = f"{contest_key}:{persons[key]}"
                    root = cs.key("us.option", option_key)
                    labels = [label for row in rows for label in row["parties"]]
                    nominated = list(
                        dict.fromkeys(party for label in labels if (party := parties[(state, label)]) is not None)
                    )
                    slogan = next((label for label in labels if (state, label) in slogans), None)
                    mains[key] = nominated[0] if nominated else None
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="write_in" if all("Write-In" in row["parties"] for row in rows) else "ticket",
                        party_id=mains[key] if len(rows) == 1 else None,
                        ballot_label=f"{rows[0]['ballot']} ({slogan})" if slogan else rows[0]["ballot"],
                    )
                    head = cs.key("us.candidacy", f"{option_key}:head")
                    cs.add(
                        "candidacy",
                        id=head,
                        person_id=persons[key],
                        option_id=root,
                        role="head",
                        ballot_name=rows[0]["ballot"],
                        is_independent=not nominated
                        and any(label in INDEPENDENT or (state, label) in slogans for label in labels),
                    )
                    for party in nominated:
                        cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                    if (year, key) in RUNNING_MATES:
                        used.add((year, key))
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:running_mate"),
                            person_id=mates[RUNNING_MATES[(year, key)]],
                            option_id=root,
                            role="running_mate",
                            is_independent=False,
                        )
                    for row in rows:
                        cell = (row["votes"], row.get("votes_flags"), row.get("votes_page"), row.get("votes_box"))
                        if len(rows) == 1:
                            cells.append((root, *cell))
                            continue
                        label = f"{row['ballot']} ({', '.join(row['parties'])})" if row["parties"] else row["ballot"]
                        leaf = cs.key("us.option", f"{option_key}:{slug(label)}")
                        if leaf in seen:
                            raise ValueError(f"{contest_key}: duplicate line {label}")
                        seen.add(leaf)
                        cs.add(
                            "contest_option",
                            id=leaf,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="write_in" if "Write-In" in row["parties"] else "ticket",
                            party_id=next(
                                (party for label in row["parties"] if (party := parties[(state, label)]) is not None),
                                None,
                            ),
                            ballot_label=label,
                        )
                        cells.append((leaf, *cell))
                    totals[key] = sum(row["votes"] for row in rows)
                    roots[key] = root
                for row in lines.filter(pl.col("key").is_null()).iter_rows(named=True):
                    kind = "none_of_the_above" if row["name"] == NOTA else "write_in"
                    option = cs.key("us.option", f"{contest_key}:{kind}:{slug(row['ballot'])}")
                    if option in seen:
                        raise ValueError(f"{contest_key}: duplicate option {row['ballot']}")
                    seen.add(option)
                    cs.add("contest_option", id=option, contest_id=contest, kind=kind, ballot_label=row["ballot"])
                    cells.append(
                        (option, row["votes"], row.get("votes_flags"), row.get("votes_page"), row.get("votes_box"))
                    )
                if result_set in cs.minted:
                    first = lines.row(0, named=True)
                    cells.append(
                        (
                            None,
                            int(lines["votes"].sum()),
                            first.get("total_flags"),
                            first.get("total_page"),
                            first.get("total_box"),
                        )
                    )
                    for option, votes, flags, page, box in cells:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1 if option else 4,
                            count_round=0,
                            flags=flags or 0,
                            value=votes,
                        )
                        if page is not None and box is not None:
                            cs.add(
                                "value_locator",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=1 if option else 4,
                                count_round=0,
                                page_id=cs.page(artifacts[year], page),
                                source_id=None,
                                x0=box[0],
                                y0=box[1],
                                x1=box[2],
                                y1=box[3],
                            )

                ranked = sorted(totals, key=totals.__getitem__, reverse=True)
                winner = ranked[0]
                flagged = set(lines.filter(pl.col("is_winner"))["key"])
                if totals[winner] == totals[ranked[1]] or (flagged and flagged != {winner}):
                    raise ValueError(f"{contest_key}: unclear winner {totals[winner]}, {flagged}")
                cs.add("outcome", result_set_id=result_set, option_id=roots[winner], kind="won", seats=at_large)
                won[(state, winner)] = at_large

                for number in range(1, count + 1):
                    code = f"{prefix}{number:02d}"
                    district_key = f"{day}:{state}-{number:02d}:president"
                    district = cs.key("us.contest", district_key)
                    district_area, _, district_name, _ = districts[code]
                    cs.add(
                        "contest",
                        id=district,
                        election_id=parent,
                        kind="elector_slate",
                        round_number=1,
                        default_result_kind="counted",
                        body_id=bodies["electoral_college"],
                        district_area_id=district_area,
                        magnitude=1,
                        allocation_parent_id=national,
                        electoral_system_id=systems["winner_take_all"],
                    )
                    name(district, f"{year} presidential election in {district_name}")
                    cs.add(
                        "slug", path=f"us/{state.lower()}/{day}-general/president-district-{number}", entity_id=district
                    )
                    district_set = cs.key(f"{SOURCE}.result_set", district_key)
                    cs.add(
                        "result_set",
                        id=district_set,
                        election_id=parent,
                        contest_id=district,
                        stage="certified",
                        kind="counted",
                        status="active",
                        source_id=nara,
                        is_default=True,
                        completeness=0,
                    )
                    cs.add("note", entity_id=district_set, kind="methodology", body_md=DISTRICT_NOTE)
                    holder = SPLITS.get((year, code), winner)
                    option_key = f"{district_key}:{persons[holder]}"
                    option = cs.key("us.option", option_key)
                    cs.add("contest_option", id=option, contest_id=district, kind="ticket", party_id=mains[holder])
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:head"),
                        person_id=persons[holder],
                        option_id=option,
                        role="head",
                        is_independent=False,
                    )
                    if (year, holder) in RUNNING_MATES:
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:running_mate"),
                            person_id=mates[RUNNING_MATES[(year, holder)]],
                            option_id=option,
                            role="running_mate",
                            is_independent=False,
                        )
                    cs.add("outcome", result_set_id=district_set, option_id=option, kind="won", seats=1)
                    won[(state, holder)] = won.get((state, holder), 0) + 1

                pledged = {persons[key]: seats for (held, key), seats in won.items() if held == state}
                if (year, state) not in FAITHLESS and pledged != college[(year, state)]:
                    raise ValueError(f"{contest_key}: pledged {pledged} but the college cast {college[(year, state)]}")

            leaders: dict[str, int] = {}
            for (_, key), seats in won.items():
                leaders[key] = leaders.get(key, 0) + seats
            for key, seats in leaders.items():
                option_key = f"{national_key}:{persons[key]}"
                option = cs.key("us.option", option_key)
                cs.add("contest_option", id=option, contest_id=national, kind="ticket", party_id=mains[key])
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=persons[key],
                    option_id=option,
                    role="head",
                    is_independent=False,
                )
                if (year, key) in RUNNING_MATES:
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:running_mate"),
                        person_id=mates[RUNNING_MATES[(year, key)]],
                        option_id=option,
                        role="running_mate",
                        is_independent=False,
                    )
                cs.add("outcome", result_set_id=national_set, option_id=option, kind="won", seats=seats)

        if RUNNING_MATES.keys() - used:
            raise ValueError(f"running mates never matched: {sorted(RUNNING_MATES.keys() - used)}")

        congress = congress.with_columns(
            person=pl.col("key").replace_strict(
                {key: str(persons[key]) for key in variants}, default=None, return_dtype=pl.String
            )
        )
        for key, text, state in (
            primaries.filter(pl.col("key").is_not_null())
            .group_by("key", "name")
            .agg(pl.col("state").first(), pl.col("votes").fill_null(0).sum(), pl.col("year").max())
            .sort("votes", "year", "name", descending=[True, True, False])
            .unique("key", keep="first", maintain_order=True)
            .select("key", "name", "state")
            .iter_rows()
        ):
            if key in persons:
                continue
            if key in fec_persons:
                persons[key] = fec_persons[key]
            elif (state, slug(text)) in by_state:
                persons[key] = by_state[(state, slug(text))]
                if ":" not in key:
                    cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            elif ":" in key and slug(text) in figures:
                persons[key] = figures[slug(text)]
            else:
                persons[key] = cs.key("fec.candidate" if ":" not in key else f"{SOURCE}.person", key)
                cs.add("person", id=persons[key])
                name(persons[key], text)
            by_state.setdefault((state, slug(text)), persons[key])
        primaries = primaries.with_columns(
            person=pl.col("key").replace_strict(
                {key: str(person) for key, person in persons.items()}, default=None, return_dtype=pl.String
            )
        )
        entrants: dict[tuple[int, str, str, int | None, str], set[str]] = {
            tuple(row[:5]): set(row[5])
            for row in congress.filter(pl.col("person").is_not_null())
            .group_by("year", "state", "chamber", "district", "term")
            .agg(pl.col("person").unique())
            .rows()
        }
        noted: set[str] = set()
        openings: dict[tuple[int, str, str, int | None, str], UUID] = {}
        unmatched: list[tuple[str, set[str], set[str]]] = []
        for (year, state, chamber, district, term, held, label), lines in primaries.with_columns(
            scope=pl.when(
                pl.col("year") < pl.col("state").replace_strict(BLANKET, default=9999, return_dtype=pl.Int64)
            ).then(pl.col("party"))
        ).group_by("year", "state", "chamber", "district", "term", "held", "scope", maintain_order=True):
            unit, area, state_name, fips = states[state]
            polity = cs.polity(f"US-{state}")
            special = term == "unexpired"
            opened, closed = calendar.get((year, state, chamber)) or calendar[(year, state, None)]
            day = held or opened
            on = day if special else date(year + 1, 1, 3)
            plan = next(id for id, start, end in plans if start <= on and (end is None or on < end))
            seat = None if chamber == "senate" else delegates.get(state)
            if chamber == "senate":
                seat = senate_seats[(state, classes[(year, state, term)])]
                place, part, office_name = state_name, "senate", "Senate"
            else:
                code = f"{fips}{district:02d}"
                area, unit, place, _ = districts[code if code in districts else f"{fips}98"]
                part, office_name = f"house-{district}" if district else "house-at-large", "House"
            blanket = label is None
            title = "" if blanket else titles.get((state, label), label).removesuffix(" Party")
            if blanket:
                system = "top_four" if state == "AK" else "top_two"
            elif (year, state, chamber) == (2016, "NC", "house"):
                system = "fptp"
            elif state == "NC":
                system = "two_round_40" if year <= 2016 else "two_round_30"
            elif state == "SD":
                system = "two_round_35"
            elif state in ("AL", "AR", "GA", "LA", "MS", "OK", "SC", "TX") or (state == "FL" and year < 2002):
                system = "two_round"
            else:
                system = "fptp"
            tacit = bool(lines["unopposed"].all())
            if lines["unopposed"].any() and not tacit:
                raise ValueError(f"{year} {state} {chamber} {district} {label}: some primary candidates unopposed")
            stages = [(day, "primary", lines)]
            if (second := lines.filter(pl.col("runoff").is_not_null())).height:
                if closed is None or not system.startswith("two_round"):
                    raise ValueError(f"{year} {state} {chamber} {district} {label}: a runoff under {system}")
                stages.append((closed, "runoff", second))
            if {when for when, _, _ in stages} & {*RUNOFFS.values(), *POSTPONED.values()}:
                raise ValueError(f"{year} {state} {chamber} {district} {label}: a primary on a general election day")
            previous = None
            for number, (when, stage, rows) in enumerate(stages):
                main = stage == "primary" and not held and when == calendar.get((year, state, None), (None,))[0]
                occasion = "general" if when == days[year] else stage
                parent = (
                    election(
                        f"{when}:{state}",
                        polity,
                        "general",
                        when,
                        f"{year} {state_name} general election",
                        f"us/{state.lower()}/{when}-general",
                        election(
                            str(when),
                            us,
                            "general",
                            when,
                            f"{year} United States general election",
                            f"us/{when}-general",
                        ),
                    )
                    if occasion == "general"
                    else election(
                        f"{when}:{state}:{stage}",
                        polity,
                        stage,
                        when,
                        f"{year} {state_name} primary election"
                        if main
                        else f"{when:%B %-d, %Y} {state_name} {'special ' if held else ''}primary"
                        f"{' runoff' if stage == 'runoff' else ''} election",
                        f"us/{state.lower()}/{when}-{stage}",
                    )
                )
                contest_key = (
                    f"{when}:{state}:{chamber}"
                    + ("" if chamber == "senate" else f":{district}")
                    + (":special" if special else "")
                    + ("" if blanket else f":{slug(title)}")
                )
                contest = cs.key("us.contest", contest_key)
                if blanket:
                    openings[(year, state, chamber, district, term)] = contest
                cs.add(
                    "contest",
                    id=contest,
                    election_id=parent,
                    kind="body_seats",
                    body_id=bodies[chamber],
                    district_area_id=area,
                    seat_id=seat,
                    term="remainder" if special else "full",
                    boundary_set_id=None if chamber == "senate" else plan,
                    party_scope_id=None if blanket else parties[(state, label)],
                    magnitude=1,
                    round_number=number + 1,
                    previous_round_id=previous,
                    electoral_system_id=systems[system],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(
                    contest,
                    f"{year} U.S. {office_name} {'special election ' if special else ''}"
                    f"{title + ' ' if title else ''}primary{' runoff' if stage == 'runoff' else ''} in {place}",
                )
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{when}-{occasion}/{part}{'-special' if special else ''}"
                    + ("" if blanket else f"-{slug(title)}")
                    + ("" if occasion == stage else f"-primary-{stage}"),
                    entity_id=contest,
                )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                annulled = chamber == "house" and (year, state, district) in COURT_ORDERED
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=parent,
                    contest_id=contest,
                    stage="official" if annulled else "certified",
                    kind="tacit" if tacit else "counted",
                    status="annulled" if annulled else "active",
                    source_id=sources[year],
                    is_default=contest_key not in RANKED_ELSEWHERE,
                    completeness=None if tacit else 1,
                )
                if contest_key in NOTES:
                    noted.add(contest_key)
                    kind, body = NOTES[contest_key]
                    cs.add("note", entity_id=result_set, kind=kind, body_md=body)
                if annulled:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="context",
                        body_md=ANNULLED_PRIMARY.format(
                            november=f"{days[year]:%B %-d, %Y}", runoff=f"{RUNOFFS[(year, state)]:%B %-d, %Y}"
                        ),
                    )
                if (year, state) in BLANKETED:
                    cs.add(
                        "note", entity_id=result_set, kind="context", body_md=BLANKET_PRIMARY.format(state=state_name)
                    )
                if stage == "primary" and (year, state, district, label) in MISPRINTED_PRIMARIES:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=PRINTED_TOTAL.format(
                            printed=f"{MISPRINTED_PRIMARIES[(year, state, district, label)]:,}",
                            sum=f"{rows['votes'].sum():,}",
                        ),
                    )
                column = "votes" if stage == "primary" else "runoff"
                tallies: list[tuple[UUID, int, int, int | None, list[float] | None]] = []
                totals: dict[str, int] = {}
                roots: dict[str, UUID] = {}
                for (person,), group in rows.filter(pl.col("person").is_not_null()).group_by(
                    "person", maintain_order=True
                ):
                    lines_of = group.rows(named=True)
                    roots[person] = cs.key("us.option", f"{contest_key}:{person}")
                    cs.add(
                        "contest_option",
                        id=roots[person],
                        contest_id=contest,
                        kind="write_in" if all(row["write_in"] for row in lines_of) else "candidate",
                        party_id=parties[(state, lines_of[0]["party"])],
                        ballot_label=lines_of[0]["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=UUID(person),
                        option_id=roots[person],
                        role="head",
                        ballot_name=lines_of[0]["name"],
                        is_independent=lines_of[0]["party"] in INDEPENDENT,
                        is_incumbent=any(row["is_incumbent"] for row in lines_of),
                    )
                    totals[person] = sum(row[column] or 0 for row in lines_of)
                    single = lines_of[0]
                    tallies.append(
                        (roots[person], totals[person], 0, None, None)
                        if len(lines_of) > 1
                        else (
                            roots[person],
                            totals[person],
                            single[f"{column}_flags"] or 0,
                            single[f"{column}_page"],
                            single[f"{column}_box"],
                        )
                    )
                for row in rows.filter(pl.col("person").is_null()).iter_rows(named=True):
                    kind = "none_of_the_above" if row["name"] in (NOTA, "None of the Above") else "write_in"
                    option = cs.key("us.option", f"{contest_key}:{kind}:{slug(row['name'])}")
                    if option in (other for other, *_ in tallies):
                        raise ValueError(f"{contest_key}: duplicate option {row['name']}")
                    cs.add("contest_option", id=option, contest_id=contest, kind=kind, ballot_label=row["name"])
                    tallies.append(
                        (
                            option,
                            row[column] or 0,
                            row[f"{column}_flags"] or 0,
                            row[f"{column}_page"],
                            row[f"{column}_box"],
                        )
                    )
                if result_set in cs.minted and not tacit:
                    first = rows.row(0, named=True)
                    summed = (
                        (first["total_flags"] or 0, first["total_page"], first["total_box"])
                        if column == "votes"
                        else (0 if first["total_flags"] is None else DERIVED, None, None)
                    )
                    for option, votes, flags, page, box in [
                        *tallies,
                        (None, sum(votes for _, votes, *_ in tallies), *summed),
                    ]:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1 if option else 4,
                            count_round=0,
                            flags=flags,
                            value=votes,
                        )
                        if page is not None and box is not None:
                            cs.add(
                                "value_locator",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=1 if option else 4,
                                count_round=0,
                                page_id=cs.page(artifacts[year], page),
                                source_id=None,
                                x0=box[0],
                                y0=box[1],
                                x1=box[2],
                                y1=box[3],
                            )
                previous = contest
                if annulled:
                    continue
                withdrawn = {row["person"] for row in rows.iter_rows(named=True) if (year, row["fec_id"]) in WITHDREW}
                for person in withdrawn:
                    cs.add("outcome", result_set_id=result_set, option_id=roots[person], kind="withdrew")
                ranked = [
                    person for person in sorted(totals, key=totals.__getitem__, reverse=True) if person not in withdrawn
                ]
                flagged = {
                    row["person"]
                    for row in rows.iter_rows(named=True)
                    if row["person"] is not None and (row["flag"] or "").startswith("W")
                }
                if number + 1 < len(stages):
                    for person in stages[number + 1][2]["person"].drop_nulls().unique():
                        cs.add("outcome", result_set_id=result_set, option_id=roots[person], kind="advanced")
                    continue
                if blanket:
                    advancing = 4 if state == "AK" else 2
                    if len(ranked) > advancing and totals[ranked[advancing - 1]] == totals[ranked[advancing]]:
                        raise ValueError(f"{contest_key}: a tie at {ranked[advancing - 1]}")
                    chosen = set(ranked[:advancing])
                    if chosen != entrants.get((year, state, chamber, district, term), set()):
                        unmatched.append(
                            (contest_key, chosen, entrants.get((year, state, chamber, district, term), set()))
                        )
                    for person in ranked:
                        if person in chosen:
                            cs.add("outcome", result_set_id=result_set, option_id=roots[person], kind="advanced")
                    continue
                if not ranked or (len(ranked) > 1 and totals[ranked[0]] == totals[ranked[1]]):
                    continue
                winner = ranked[0]
                if flagged and winner not in flagged:
                    unmatched.append((contest_key, flagged, {winner}))
                if state == "IA" and not tacit and totals[winner] < 0.35 * sum(votes for _, votes, *_ in tallies):
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=CONVENTION)
                    continue
                if tacit and len(ranked) != 1:
                    raise ValueError(f"{contest_key}: {len(ranked)} unopposed candidates")
                cs.add("outcome", result_set_id=result_set, option_id=roots[winner], kind="won")
        if unmatched:
            raise ValueError(f"primary outcomes disagree with the FEC's flags or general lists: {unmatched}")

        known: dict[str, set[UUID]] = {text: {person} for text, person in figures.items()}
        for key, text in frame.filter(pl.col("key").is_not_null()).select("key", "name").unique().iter_rows():
            if key in persons:
                known.setdefault(slug(text), set()).add(persons[key])
        for key, text in (
            nominations.filter(pl.col("key").is_not_null())
            .group_by("key", "name")
            .agg(pl.col("votes").fill_null(0).sum())
            .with_columns(anonymous=pl.col("key").str.starts_with("us:"))
            .sort("anonymous", "votes", "name", descending=[False, True, False])
            .unique("key", keep="first", maintain_order=True)
            .select("key", "name")
            .iter_rows()
        ):
            if key in persons:
                continue
            if key in NAMESAKES:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if key in fec_persons and fec_persons[key] != persons[key]:
                    cs.renamed[str(persons[key])] = str(fec_persons[key])
                cs.add("person", id=persons[key])
                name(persons[key], NAMES.get(key, text))
                cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            elif key in fec_persons:
                persons[key] = fec_persons[key]
            elif key in TWINS:
                persons[key] = persons[TWINS[key]] if TWINS[key] in persons else fec_persons[TWINS[key]]
                cs.add("external_id", entity_id=persons[key], scheme="fec.candidate", value=key)
            elif key.startswith("us:") and len(known.get(slug(text), ())) == 1:
                persons[key] = next(iter(known[slug(text)]))
            else:
                persons[key] = cs.key(f"{SOURCE}.person" if key.startswith("us:") else "fec.candidate", key)
                cs.add("person", id=persons[key])
                name(persons[key], text)
            known.setdefault(slug(text), set()).add(persons[key])
        nominations = nominations.with_columns(
            person=pl.col("key").replace_strict(
                {key: str(person) for key, person in persons.items()}, default=None, return_dtype=pl.String
            ),
            nominee=pl.struct("state", "party").map_elements(
                lambda row: {"Democratic Party": "democratic", "Republican Party": "republican"}.get(
                    AFFILIATES.get(titles[(row["state"], row["party"])], titles[(row["state"], row["party"])])
                ),
                return_dtype=pl.String,
            ),
        )
        nationals: dict[tuple[int, str], UUID] = {}
        for (year, word), lines in nominations.filter(pl.col("nominee").is_not_null()).group_by(
            "year", "nominee", maintain_order=True
        ):
            start = min(IOWA[year], *lines["day"])
            umbrella = cs.key("us.election", f"{start}:{word}-presidential-primaries")
            cs.add(
                "election",
                id=umbrella,
                polity_id=us,
                parent_id=None,
                kind="primary",
                date_from=start,
                date_to=max(lines["day"]),
                status="held",
            )
            name(umbrella, f"{year} {word.title()} Party presidential primaries")
            cs.add("slug", path=f"us/{start}-{word}-presidential-primaries", entity_id=umbrella)
            nationals[(year, word)] = cs.key("us.contest", f"{start}:president:{word}")
            cs.add(
                "contest",
                id=nationals[(year, word)],
                election_id=umbrella,
                kind="delegate_allocation",
                office_id=office,
                party_scope_id=cs.key("us.party", f"{word}-party"),
                magnitude=DELEGATES.get((year, word)),
                round_number=1,
                default_result_kind="counted",
            )
            name(nationals[(year, word)], f"{year} {word.title()} presidential nomination")
            cs.add(
                "slug", path=f"us/{start}-{word}-presidential-primaries/president", entity_id=nationals[(year, word)]
            )
        for (year, state, label), lines in nominations.group_by("year", "state", "party", maintain_order=True):
            unit, area, state_name, _ = states[state]
            day = lines["day"].mode().sort()[0]
            if day == days[year] or f"{day}:{state}:runoff" in elections:
                raise ValueError(f"{year} {state} {label}: a presidential primary on a general or runoff day")
            parent = election(
                f"{day}:{state}:primary",
                cs.polity(f"US-{state}"),
                "primary",
                day,
                f"{day:%B %-d, %Y} {state_name} presidential primary election",
                f"us/{state.lower()}/{day}-primary",
            )
            title = titles[(state, label)].removesuffix(" Party")
            tacit = bool(lines["unopposed"].all())
            if lines["unopposed"].any() and not tacit:
                raise ValueError(f"{year} {state} {label}: some presidential primary candidates unopposed")
            contest_key = f"{day}:{state}:president:{slug(title)}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=parent,
                kind="delegate_allocation",
                office_id=office,
                district_area_id=area,
                party_scope_id=parties[(state, label)],
                magnitude=None,
                round_number=1,
                allocation_parent_id=nationals.get((year, lines["nominee"][0])),
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{year} {state_name} {title} presidential primary")
            cs.add("slug", path=f"us/{state.lower()}/{day}-primary/president-{slug(title)}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=parent,
                contest_id=contest,
                stage="certified",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=None if tacit else 1,
            )
            if "UN" in lines["code"]:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNAFFILIATED)
            values = []
            for (person,), group in lines.filter(pl.col("person").is_not_null()).group_by(
                "person", maintain_order=True
            ):
                option = cs.key("us.option", f"{contest_key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="write_in" if group["write_in"].all() else "candidate",
                    party_id=parties[(state, label)],
                    ballot_label=group["name"][0],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                    person_id=UUID(person),
                    option_id=option,
                    role="head",
                    ballot_name=group["name"][0],
                    is_independent=False,
                )
                values.append((option, int(group["votes"].fill_null(0).sum())))
                if tacit:
                    if lines.height != 1:
                        raise ValueError(f"{contest_key}: {lines.height} unopposed candidates")
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won")
            for row in lines.filter(pl.col("person").is_null()).iter_rows(named=True):
                option = cs.key("us.option", f"{contest_key}:{row['kind']}:{slug(row['name'])}")
                cs.add("contest_option", id=option, contest_id=contest, kind=row["kind"], ballot_label=row["name"])
                values.append((option, row["votes"]))
            if result_set in cs.minted and not tacit:
                for option, votes in values:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=4,
                    count_round=0,
                    flags=0,
                    value=sum(votes for _, votes in values),
                )
        runoffs: set[tuple[int, str]] = set()
        for (year, state, chamber, district, term), lines in congress.group_by(
            "year", "state", "chamber", "district", "term", maintain_order=True
        ):
            unit, area, state_name, fips = states[state]
            polity = cs.polity(f"US-{state}")
            special = term == "unexpired"
            november = state == "LA" and (year, district) in DECIDED_IN_NOVEMBER
            held = JUNGLE[year] if state == "LA" and year in JUNGLE and not november else days[year]
            held = POSTPONED.get((year, state, district), held)
            on = held if special else date(year + 1, 1, 3)
            plan = next(id for id, start, end in plans if start <= on and (end is None or on < end))
            seat = None if chamber == "senate" else delegates.get(state)
            if chamber == "senate":
                seat = senate_seats[(state, classes[(year, state, term)])]
                place, part, office_name = state_name, "senate", "Senate"
            else:
                code = f"{fips}{district:02d}"
                area, unit, place, valid = districts[code if code in districts else f"{fips}98"]
                if (held if special else date(year + 1, 1, 3)) not in valid:
                    raise ValueError(f"{year} {state}-{district}: {code} is not a district then")
                part, office_name = f"house-{district}" if district else "house-at-large", "House"
            if (state == "ME" and year >= 2018) or (state == "AK" and year >= 2022):
                system = "irv"
            elif (state == "CA" and year >= 2012) or (state == "WA" and year >= 2008):
                system = "top_two"
            elif state == "GA" and 1994 <= year <= 2004:
                system = "two_round_45"
            elif (
                state == "GA"
                or (state == "LA" and year not in (2008, 2010))
                or (special and state in ("MS", "TX"))
                or (year, state, district) in COURT_ORDERED
                or (year, state) in ((1994, "VI"), (1996, "AS"), (1996, "VI"), (2000, "AS"), (2002, "AS"), (2020, "GU"))
            ):
                system = "two_round"
            else:
                system = "fptp"
            preference = (
                system == "top_two"
                or (system == "irv" and state == "AK")
                or (
                    system.startswith("two_round")
                    and (state == "LA" or special or (year, state, district) in COURT_ORDERED)
                )
            )
            annulled = (lines["flag"] == "**").any()
            umbrella = election(
                str(days[year]),
                us,
                "general",
                days[year],
                f"{year} United States general election",
                f"us/{days[year]}-general",
            )
            stages = [(held, "runoff", lines)] if november else [(held, "general", lines)]
            runoff = lines.filter(pl.col("runoff").is_not_null())
            if runoff.height:
                if not system.startswith("two_round"):
                    raise ValueError(f"{year} {state} {chamber} {district}: a runoff under {system}")
                runoffs.add((year, state))
                stages.append((RUNOFFS[(year, state)], "runoff", runoff))

            previous = openings.get((year, state, chamber, district, term))
            for number, (day, stage, rows) in enumerate(stages):
                if stage == "general":
                    parent = election(
                        f"{day}:{state}",
                        polity,
                        "general",
                        day,
                        f"{year} {state_name} general election"
                        if day == days[year]
                        else f"{day:%B %Y} {state_name} general election",
                        f"us/{state.lower()}/{day}-general",
                        umbrella,
                    )
                    title = f"{year} U.S. {office_name} {'special election' if special else 'election'} in {place}"
                else:
                    completes = (
                        parent
                        if number
                        else election(
                            f"{JUNGLE[year]}:{state}",
                            polity,
                            "general",
                            JUNGLE[year],
                            f"{JUNGLE[year]:%B %Y} {state_name} general election",
                            f"us/{state.lower()}/{JUNGLE[year]}-general",
                            umbrella,
                        )
                    )
                    parent = election(
                        f"{day}:{state}:runoff",
                        polity,
                        "runoff",
                        day,
                        f"{day.year} {state_name} runoff election",
                        f"us/{state.lower()}/{day}-runoff",
                        completes,
                    )
                    title = (
                        f"{day.year} U.S. {office_name} {'special election runoff' if special else 'runoff'} in {place}"
                    )
                contest_key = (
                    f"{day}:{state}:{chamber}"
                    + ("" if chamber == "senate" else f":{district}")
                    + (":special" if special else "")
                )
                tacit = stage == "general" and bool(rows["unopposed"].all())
                if rows["unopposed"].any() and not tacit:
                    raise ValueError(f"{contest_key}: some candidates unopposed")
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=parent,
                    kind="body_seats",
                    body_id=bodies[chamber],
                    district_area_id=area,
                    seat_id=seat,
                    term="remainder" if special else "full",
                    boundary_set_id=None if chamber == "senate" else plan,
                    magnitude=1,
                    round_number=number
                    + (2 if system == "top_two" or (system == "irv" and state == "AK") or november else 1),
                    previous_round_id=previous,
                    electoral_system_id=systems[system],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, title)
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{stage}/{part}{'-special' if special else ''}",
                    entity_id=contest,
                )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=parent,
                    contest_id=contest,
                    stage="official" if annulled else "certified",
                    kind="tacit" if tacit else "counted",
                    status="annulled" if annulled else "superseded" if contest_key in canvasses else "active",
                    source_id=statistics if year == 2024 else sources[year],
                    is_default=contest_key not in canvasses and contest_key not in RANKED_ELSEWHERE,
                    completeness=None if tacit else 1,
                )
                if contest_key in NOTES:
                    noted.add(contest_key)
                    kind, text = NOTES[contest_key]
                    cs.add("note", entity_id=result_set, kind=kind, body_md=text)
                if state == "LA" and year in JUNGLE and (stage == "general" or november):
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="context",
                        body_md=(OPEN_RUNOFF if november else OPEN_PRIMARY).format(
                            date=f"{JUNGLE[year]:%B %-d, %Y}", november=f"{day:%B %-d, %Y}"
                        ),
                    )
                if chamber == "house" and (year, state, district) in MISPRINTED:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=PRINTED_TOTAL.format(
                            printed=f"{MISPRINTED[(year, state, district)]:,}", sum=f"{rows['votes'].sum():,}"
                        ),
                    )
                if chamber == "house" and (year, state, district) in UNDERCOUNTED:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=CLERK_COUNTS.format(counts=UNDERCOUNTED[(year, state, district)]),
                    )

                counts = [(0, "runoff" if stage == "runoff" and not november else "votes")]
                if system == "irv" and rows["rcv2"].is_not_null().any():
                    rows = rows.filter(pl.any_horizontal(pl.col("rcv1", "rcv2", "rcv3").is_not_null()))
                    counts = [
                        (1, "rcv1" if rows["rcv1"].is_not_null().any() else "votes"),
                        *(
                            (index, column)
                            for index, column in ((2, "rcv2"), (3, "rcv3"))
                            if rows[column].is_not_null().any()
                        ),
                    ]
                values: list[tuple[UUID | None, int, int | None, int | None, int | None, list[float] | None]] = []
                seen: set[UUID] = set()
                totals: dict[str, int] = {}
                roots: dict[str, UUID] = {}
                for (person,), group in rows.filter(pl.col("person").is_not_null()).group_by(
                    "person", maintain_order=True
                ):
                    lines_of = group.sort("votes", descending=True, nulls_last=True).rows(named=True)
                    option_key = f"{contest_key}:{person}"
                    root = cs.key("us.option", option_key)
                    labels = [label for row in lines_of for label in row["parties"]]
                    nominated = list(
                        dict.fromkeys(party for label in labels if (party := parties[(state, label)]) is not None)
                    )
                    slogan = next((label for label in labels if (state, label) in slogans), None)
                    ballot = lines_of[0]["name"]
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="write_in" if all("Write-In" in row["parties"] for row in lines_of) else "candidate",
                        party_id=(nominated[0] if nominated else None) if len(lines_of) == 1 else None,
                        ballot_label=f"{ballot} ({slogan})" if slogan else ballot,
                    )
                    head = cs.key("us.candidacy", f"{option_key}:head")
                    cs.add(
                        "candidacy",
                        id=head,
                        person_id=UUID(person),
                        option_id=root,
                        role="head",
                        ballot_name=ballot,
                        is_independent=not nominated
                        and any(label in INDEPENDENT or (state, label) in slogans for label in labels),
                        is_incumbent=None
                        if all(row["is_incumbent"] is None for row in lines_of)
                        else any(row["is_incumbent"] for row in lines_of),
                    )
                    if not preference:
                        for party in nominated:
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                    for row in lines_of:
                        option = root
                        if len(lines_of) > 1:
                            label = f"{row['name']} ({', '.join(row['parties'])})" if row["parties"] else row["name"]
                            option = cs.key("us.option", f"{option_key}:{slug(label)}")
                            if option in seen:
                                raise ValueError(f"{contest_key}: duplicate line {label}")
                            seen.add(option)
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                parent_option_id=root,
                                kind="write_in" if "Write-In" in row["parties"] else "candidate",
                                party_id=next(
                                    (
                                        party
                                        for label in row["parties"]
                                        if (party := parties[(state, label)]) is not None
                                    ),
                                    None,
                                ),
                                ballot_label=label,
                            )
                        values.extend(
                            (
                                option,
                                count_round,
                                row[column],
                                row.get(f"{column}_flags"),
                                row.get(f"{column}_page"),
                                row.get(f"{column}_box"),
                            )
                            for count_round, column in counts
                        )
                    totals[person] = sum(row[counts[-1][1]] or 0 for row in lines_of)
                    roots[person] = root
                for row in rows.filter(pl.col("person").is_null()).iter_rows(named=True):
                    kind = (
                        "none_of_the_above"
                        if row["name"] == NOTA
                        else "others"
                        if row["name"] == VACANCY
                        else "write_in"
                    )
                    option = cs.key("us.option", f"{contest_key}:{kind}:{slug(row['name'])}")
                    if option in seen and row["name"] == "Scattered":
                        option = cs.key("us.option", f"{contest_key}:{kind}:{slug(row['name'])}:{len(seen)}")
                    if option in seen:
                        raise ValueError(f"{contest_key}: duplicate option {row['name']}")
                    seen.add(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind=kind,
                        party_id=next(
                            (party for label in row["parties"] if (party := parties[(state, label)]) is not None), None
                        ),
                        ballot_label=row["name"],
                    )
                    values.extend(
                        (
                            option,
                            count_round,
                            row[column],
                            row.get(f"{column}_flags"),
                            row.get(f"{column}_page"),
                            row.get(f"{column}_box"),
                        )
                        for count_round, column in counts
                    )
                if result_set in cs.minted and not tacit:
                    first = rows.row(0, named=True)
                    column = counts[0][1]
                    values.append(
                        (None, counts[0][0], int(rows[column].sum()) if rows[column].is_not_null().any() else None)
                        + (
                            (first["total_flags"], first["total_page"], first["total_box"])
                            if column == "votes"
                            else (None if first["total_flags"] is None else DERIVED, None, None)
                        )
                    )
                    for option, count_round, value, flags, page, box in values:
                        if value is None or (count_round >= 2 and value == 0):
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1 if option else 4,
                            count_round=count_round,
                            flags=flags or 0,
                            value=value,
                        )
                        if page is not None and box is not None:
                            cs.add(
                                "value_locator",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=1 if option else 4,
                                count_round=count_round,
                                page_id=cs.page(printed[year], page),
                                source_id=None,
                                x0=box[0],
                                y0=box[1],
                                x1=box[2],
                                y1=box[3],
                            )

                ranked = sorted(totals, key=totals.__getitem__, reverse=True)
                share = totals[ranked[0]] / (rows["votes"].sum() or 1)
                majority = 0.45 if system == "two_round_45" else 0.5
                previous = contest
                if annulled:
                    continue
                if stage == "general" and len(stages) > 1:
                    if share >= majority:
                        raise ValueError(f"{contest_key}: a runoff after a {share:.1%} majority")
                    for person in runoff["person"].unique():
                        cs.add("outcome", result_set_id=result_set, option_id=roots[person], kind="advanced")
                    continue
                flagged = {
                    row["person"]
                    for row in rows.iter_rows(named=True)
                    if row["person"] is not None and (row["flag"] or "").startswith("W")
                }
                winner = next(iter(flagged)) if system == "irv" and len(flagged) == 1 else ranked[0]
                if (
                    (len(ranked) > 1 and system != "irv" and totals[ranked[0]] == totals[ranked[1]])
                    or (flagged and flagged != {winner})
                    or (len(counts) > 1 and totals[winner] != totals[ranked[0]])
                    or (tacit and len(ranked) != 1)
                    or (
                        not tacit
                        and stage == "general"
                        and (system.startswith("two_round") or (system == "irv" and not flagged))
                        and share < majority
                    )
                ):
                    raise ValueError(f"{contest_key}: unclear winner {totals}, {flagged}")
                cs.add("outcome", result_set_id=result_set, option_id=roots[winner], kind="elected", seats=1)
                if contest_key in canvasses:
                    canvass, page, read = canvasses[contest_key]
                    corrected = cs.key(f"{SOURCE}.result_set", f"{contest_key}:canvass")
                    cs.add(
                        "result_set",
                        id=corrected,
                        election_id=parent,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="active",
                        source_id=canvass,
                        is_default=True,
                        supersedes_id=result_set,
                        completeness=1,
                    )
                    cs.add("note", entity_id=corrected, kind="context", body_md=CANVASS_NOTE)
                    options = dict(
                        conn.execute(
                            """select ballot_label, id from contest_option
                               where contest_id = %s and parent_option_id is null""",
                            [contest],
                        ).fetchall()
                    )
                    if corrected in cs.minted:
                        for label, (value, at) in read.items():
                            option = None if label is None else options[label]
                            for table, extra in (
                                ("result_value", {"flags": 0, "value": value}),
                                (
                                    "value_locator",
                                    {
                                        "page_id": page,
                                        "source_id": None,
                                        "x0": at[0],
                                        "y0": at[1],
                                        "x1": at[2],
                                        "y1": at[3],
                                    },
                                ),
                            ):
                                cs.add(
                                    table,
                                    result_set_id=corrected,
                                    unit_id=unit,
                                    option_id=option,
                                    channel=0,
                                    measure=1 if option else 4,
                                    count_round=0,
                                    **extra,
                                )
                    cs.add("outcome", result_set_id=corrected, option_id=roots[winner], kind="elected", seats=1)
                if len(counts) > 1:
                    for person in ranked:
                        if person != winner:
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=roots[person],
                                kind="lost" if totals[person] else "eliminated",
                            )

        if RUNOFFS.keys() - runoffs or NOTES.keys() - noted:
            raise ValueError(f"unused runoffs {RUNOFFS.keys() - runoffs} or notes {NOTES.keys() - noted}")
        return cs.write()
