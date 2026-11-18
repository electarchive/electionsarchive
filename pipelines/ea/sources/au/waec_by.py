"""Western Australian Legislative Assembly by-elections from the Western Australian Electoral Commission (WAEC).

The commission publishes an election report for each by-election, linked from its reports page. Each prints the
candidates in ballot order and first preference votes for each candidate; most print them at each polling place, with
the declaration votes by type, in a table laid out its own way. The 2006 Victoria Park, 2007 Peel, 2010 Armadale,
2018 Darling Range, and 2022 North West Central tables have a text layer and are read here with `pdftotext -layout`.
The 2008 Murdoch, 2009 Fremantle and Willagee, 2014 Vasse, and 2018 Cottesloe tables are pictures; their rows are
keyed in below from the printed pages, and the same arithmetic checks that read the text tables run over them. The
2023 Rockingham report prints each candidate's total and each place's total votes, the 2001 Nedlands report only the
district's totals, and the 2001 Merredin report no votes for the candidates at all. One package holds each
by-election, plus a `by-registry` package for the parties and report files they add to the `au.waec` registry.
"""

import itertools
import re
from dataclasses import dataclass
from datetime import date

from ea import changeset, package, raw
from ea.package import Package

from . import waec
from .waec import (
    ASSEMBLY,
    AUSTRALIA,
    CAST,
    INVALID,
    REGISTERED,
    SOURCE,
    SYSTEM,
    VALID,
    VOTES,
    WAEC,
    Candidate,
    Place,
    key,
    put,
    url,
)

DOCS = "/sites/default/files/content/documents"
NWC = "/sites/default/files/NWC By Election report - Final_0.pdf"


@dataclass(frozen=True)
class Bye:
    day: str
    district: str
    plan: str  # the boundary determination the district belongs to
    report: str
    candidates: tuple[tuple[str, str, str], ...]  # surname, given names, party as the report prints it
    enrolled: int
    winner: int  # the index of the declared member
    pair: tuple[int, int, int, int] | None  # the final two candidates' indexes and their votes
    cause: str = ""
    labels: tuple[
        str, ...
    ] = ()  # the table's rows: a polling place, "~" a declaration row, "=" a subtotal, "!" the total
    formal_column: bool = True  # whether the table prints a column of formal votes
    first: str = ""  # text in the table's first row
    rows: str = ""  # the rows' numbers, keyed in from a picture, one row a line
    declared: tuple[int, ...] = ()  # candidate votes and then informal votes, when there is no table
    cast: int | None = None  # total votes, when no table prints it
    extra: tuple[tuple[str, int, bool], ...] = ()  # (label, votes cast, aggregate) a report prints without a count
    note: str = ""


# Candidates (surname, given names, party as printed), in ballot order -------------------------------------------

LIBERAL, LABOR, GREENS, ONE_NATION = "Liberal", "Australian Labor Party", "Greens (WA)", "ONE NATION"
CDP, IND = "Christian Democratic Party WA", "Independent"

VICTORIA_PARK = (
    ("OWENS", "Andrew", IND),
    ("TATTERSALL", "John", ""),
    ("MARGETTS", "Dee", GREENS),
    ("van LIESHOUT", "Teresa", IND),
    ("WARD", "Mike", IND),
    ("BATEMAN", "Sue", ONE_NATION),
    ("DUNN", "James", "Daylight Saving Party"),
    ("WYATT", "Ben", LABOR),
    ("GREAVES", "Peter", "FAMILY FIRST"),
    ("HEGGERS", "Bill", CDP),
    ("STEVENSON", "Bruce", LIBERAL),
)
PEEL = (
    ("McCARTHY", "Brian", "CITIZENS ELECTORAL COUNCIL"),
    ("WOODWARD", "Robert", IND),
    ("TREMAIN", "Brent", CDP),
    ("KETTLE", "Gerard", IND),
    ("PAPALIA", "Paul", LABOR),
    ("BRADSHAW", "Craig", ONE_NATION),
    ("JECKS", "Dawn", GREENS),
    ("COLEMAN", "Graeme", LIBERAL),
)
MURDOCH = (
    ("PORTER", "Christian", LIBERAL),
    ("CHEW", "Ka-ren", CDP),
    ("GILMOUR", "Neil", ONE_NATION),
    ("HARPER", "Hsien", GREENS),
)
FREMANTLE = (
    ("VARGA", "Nik", IND),
    ("TOTTEN", "Rob", "Citizens Electoral Council"),
    ("TER HORST", "Jan", IND),
    ("ZAGAMI", "Carmelo", IND),
    ("BONI", "Steve", IND),
    ("DU PLESSIS", "Andriétte", "WAFamilyfirst.com"),
    ("TAGLIAFERRI", "Peter", LABOR),
    ("HOLLETT", "Julie", CDP),
    ("LORRIMAR", "Rosemary Anne", IND),
    ("CARLES", "Adele", GREENS),
    ("WAINWRIGHT", "Sam", ""),
)
WILLAGEE = (
    ("CHEW", "Henri", CDP),
    ("TINLEY", "Peter", LABOR),
    ("HARPER", "Hsien", GREENS),
    ("GEORGATOS", "Gerry", IND),
)
ARMADALE = (
    ("BUTI", "Tony", LABOR),
    ("VAN BURGEL", "Jamie", CDP),
    ("TUCAK", "John D", IND),
    ("DAVIES", "Owen", GREENS),
)
VASSE = (
    ("BARNETT", "Wayne", "Australian Christians"),
    ("GORDON", "Peter", "THE NATIONALS"),
    ("JOHNSON", "Peter", IND),
    ("METTAM", "Libby", "Liberal Party"),
    ("BALDOCK", "Michael", "The Greens (WA)"),
    ("VAN LIESHOUT", "Teresa", IND),
)
COTTESLOE = (
    ("HONEY", "David", "Liberal Party"),
    ("TINLEY", "Cam", "Micro Business Party"),
    ("THOMAS", "Michael", IND),
    ("NORRIS", "Ron", "WESTERN AUSTRALIAN PARTY"),
    ("BOLAND", "Greg", "The Greens (WA)"),
    ("TUCAK", "Michael", IND),
    ("MALOV", "Dmitry", IND),
)
DARLING_RANGE = (
    ("GOODRICK", "Russell", "WESTERN AUSTRALIA PARTY"),
    ("PYLE", "Anthony", "The Greens (WA)"),
    ("THOMAS-WURTH", "Jehni", "Animal Justice Party"),
    ("HAYDEN", "Alyssa", "Liberal Party"),
    ("WATT", "John", "FLUORIDE FREE WA"),
    ("CADDIES", "Rod", "Pauline Hanson's One Nation"),
    ("LAWRENCE", "Tania", "WA Labor"),
    ("EIKELBOOM", "Eric", "Australian Christians"),
    ("OSTLE", "Stuart", "Shooters, Fishers and Farmers"),
    ("SHAW", "Doug", IND),
    ("O'BYRNE", "George", IND),
)
NORTH_WEST_CENTRAL = (
    ("GLAHN–BERTELSEN", "Niels", "The Greens (WA) Inc"),
    ("RANDLE", "Andrea", "WESTERN AUSTRALIA PARTY"),
    ("BAKER", "Peter", "Small Business Party"),
    ("BEARD", "Merome", "National Party of Australia (WA) Inc"),
    ("LAURENT", "Gerald", "Pauline Hanson's One Nation"),
    ("LOCKYER", "Leanne", "Legalise Cannabis Western Australia Party"),
    ("McCOULL", "Jake Adkins", "Liberal Democratic Party"),
    ("FELS", "Anthony", "WESTERN AUSTRALIA PARTY"),
    ("HORSMAN", "Aaron", "NO MANDATORY VACCINATION PARTY"),
    ("DUNNE", "Peter D", IND),
    ("STOKES", "Tony", IND),
    ("BASTON", "Will", "The Liberal Party of Australia (Western Australia Division) Inc"),
)
ROCKINGHAM = (
    ("MARSHALL", "Magenta", "WA Labor"),
    ("COTTAM", "Rae", "Legalise Cannabis Western Australia Party"),
    ("HUDSON", "Peter", "Liberal Party"),
    ("KNAPP", "Janetia", ""),
    ("DE JONG", "Madeleine", "The Greens (WA)"),
    ("GALLETLY", "Clive", IND),
    ("EDWARDS", "Hayley", IND),
    ("CRICHTON", "Mike", "Australian Christians"),
    ("DUNNE", "Peter D", IND),
)
NEDLANDS = (
    ("WALKER", "Sue", LIBERAL),
    ("CORRIGAN", "Simon", LABOR),
    ("COLLIN", "Robin", IND),
    ("WALKER", "Steve", GREENS),
    ("McDONALD", "Karen", IND),
    ("ASH", "Frank", IND),
    ("EDGAR", "Bill", "Pauline Hanson’s ONE NATION"),
    ("BUCKLE", "Ashley", "Democrat"),
)
MERREDIN = (
    ("MEOTTI", "Jason", "Democrat"),
    ("GRYLLS", "Brendon", "National Party"),
    ("WEST", "Darren", "ALP"),
    ("BURGES", "Joanne", "Liberal"),
    ("FALLS", "Jamie", "Pauline Hanson’s ONE NATION"),
)

# Rows ----------------------------------------------------------------------------------------------------------

DECLARATION = (
    "~Early Votes (by post)",
    "~Early Votes (in person)",
    "~Provisional Votes",
    "~Special Institutions, Hospitals & Remotes",
)
ENDING = ("=Declaration and Mobile Votes Total", "!Total First Preferences Votes")

VICTORIA_PARK_ROWS = (
    "Bentley Community Centre",
    "Bentley Community Kindergarten",
    "East Victoria Park Primary School",
    "Gibbs Street Primary School",
    "Harold Hawthorn Centre",
    "Homestead Seniors Centre",
    "Lathlain Primary School",
    "Leisure Life Centre",
    "Millen Primary School",
    "Queens Park Primary School",
    "Sevenoaks Senior College",
    "South Bentley Community Care Centre",
    "Wilson Community Hall",
    "=Polling Place Totals",
    *DECLARATION,
    *ENDING,
)
PEEL_ROWS = (
    "Calista Primary School",
    "Charthouse Primary School",
    "Coastal Community Centre",
    "Cooloongup Primary School",
    "Hillman Primary School",
    "Koorana Primary School",
    "Leda Primary School",
    "Port Kennedy Primary School",
    "Rockingham Baptist Church",
    "Rockingham Lakes Primary School",
    "Safety Bay Senior High School",
    "Secret Harbour Primary School",
    "Singleton Community Hall",
    "Tranby College",
    "Warnbro Community High School",
    "=Polling Place Totals",
    "~Absent Votes",
    *DECLARATION,
    *ENDING,
)
ARMADALE_ROWS = (
    "Armadale Primary School",
    "Armadale-Kelmscott Senior Citizens Centre",
    "Cecil Andrews Senior High School",
    "Challis Primary School",
    "Grovelands Primary School",
    "Gwynne Park Primary School",
    "Kelmscott Senior High School",
    "Kingsley Primary School",
    "Neerigen Brook Primary School",
    "St Mary in the Valley Anglican Church Hall",
    "Westfield Park Primary School",
    "=Polling Place Totals",
    *DECLARATION,
    *ENDING,
)
DARLING_RANGE_ROWS = (
    "Armadale Primary School",
    "Bedfordale District Hall",
    "Bruno Gianetti Hall",
    "Byford Village Shopping Centre",
    "Karragullen District Hall",
    "Kelmscott Primary School",
    "Kwinana South Bush Fire Brigade",
    "Marri Grove Primary School",
    "Mundaring Hall",
    "Mundijong Primary School",
    "Oakford Community Hall",
    "Pickering Brook Primary School",
    "Roleystone Hall",
    "Serpentine Jarrahdale Community Recreation Centre",
    "Serpentine Primary School",
    "West Byford Primary School",
    "~Special Institutions, Hospitals & Remotes",
    "~Absent Votes",
    "~Early Votes (by post)",
    "~Early Votes (in person)",
    "~Provisional Votes",
    "!Total Votes",
)
NORTH_WEST_CENTRAL_ROWS = (
    "Carnarvon Community College",
    "Carnarvon Woolshed",
    "Coral Bay Learning Centre",
    "Cue Primary School",
    "Exmouth Shire Hall",
    "Kalbarri District High School",
    "Meekatharra Shire Hall",
    "Mount Magnet District High School",
    "Onslow Primary School",
    "Pannawonica Primary School",
    "Paraburdoo Primary School",
    "Shark Bay School",
    "Tom Price Recreation Centre",
    "Wiluna Remote Community School",
    "Yalgoo Core Stadium Recreation Centre",
    "~Special Institutions, Hospitals and Remotes",
    "~Absent Votes",
    "~Early Votes by Post",
    "~Early Votes in Person",
    "~Provisional Votes",
    "!Total Votes",
)
MURDOCH_ROWS = (
    "Airforce Association",
    "Bateman Primary School",
    "Brentwood Primary School",
    "Bull Creek Primary School",
    "Kardinya Primary School",
    "Leeming Primary School",
    "Murdoch University - Murdoch Campus Car Park",
    "Oberthur Primary School",
    "Rossmoyne Primary School",
    "West Leeming Primary School",
    "Winthrop Primary School",
    "=Polling Place Totals",
    *DECLARATION,
    *ENDING,
)
# Votes for each candidate in ballot order, then formal, informal, and total votes, as the report's picture prints them.
MURDOCH_NUMBERS = """
692 106 38 217 1053 33 1086
1044 135 57 452 1688 57 1745
1182 110 45 554 1891 63 1954
860 105 61 395 1421 60 1481
1109 167 116 722 2114 82 2196
1198 117 72 542 1929 57 1986
247 33 15 163 458 20 478
683 96 23 388 1190 42 1232
854 103 39 298 1294 40 1334
1178 132 50 490 1850 74 1924
2325 344 53 748 3470 115 3585
11372 1448 569 4969 18358 643 19001
616 44 38 236 934 20 954
858 101 42 267 1268 21 1289
2 0 0 1 3 0 3
273 11 10 29 323 12 335
1749 156 90 533 2528 53 2581
13121 1604 659 5502 20886 696 21582
"""
FREMANTLE_ROWS = (
    "Anglican Church Hall",
    "Beaconsfield Primary School",
    "Bicton Primary School",
    "Christ The King School",
    "East Fremantle Primary School",
    "Fremantle Primary School",
    "Palmyra Primary School",
    "Phoenix Primary School",
    "Richmond Primary School",
    "Rottnest Island Picture Hall",
    "St Patricks Primary School",
    "White Gum Valley Primary School",
    "=Polling Place Totals",
    "~Absent Votes",
    *DECLARATION,
    *ENDING,
)
FREMANTLE_NUMBERS = """
45 4 5 55 20 6 330 31 14 404 17 931 37 968
46 5 26 80 23 11 766 20 9 1017 37 2040 80 2120
55 2 8 91 19 12 306 26 9 376 16 920 37 957
41 4 11 88 28 34 926 23 8 569 44 1776 111 1887
77 7 14 91 19 10 739 17 13 1168 70 2225 86 2311
29 4 10 65 35 5 500 21 9 941 47 1666 45 1711
24 0 9 21 15 14 297 14 6 356 12 768 37 805
21 2 11 40 16 5 387 17 12 254 12 777 65 842
116 6 15 256 54 16 735 67 20 976 44 2305 85 2390
2 0 2 2 0 0 21 0 2 49 4 82 6 88
22 3 4 27 18 4 302 12 3 368 25 788 28 816
34 1 9 56 23 15 557 19 8 657 39 1418 59 1477
512 38 124 872 270 132 5866 267 113 7135 367 15696 676 16372
60 7 28 33 22 20 436 25 20 446 29 1126 93 1219
50 3 18 46 19 18 724 21 16 444 24 1383 23 1406
71 8 18 47 27 21 567 19 20 683 33 1514 73 1587
2 0 0 1 1 0 3 0 0 0 0 7 3 10
6 0 0 0 1 3 36 7 2 14 1 70 20 90
189 18 64 127 70 62 1766 72 58 1587 87 4100 212 4312
701 56 188 999 340 194 7632 339 171 8722 454 19796 888 20684
"""
WILLAGEE_ROWS = (
    "Anglican Church of The Holy Cross Hall",
    "Caralee Community School",
    "Coolbellup Community School",
    "East Hamilton Hill Primary School",
    "Hilton Primary School",
    "Melville Senior High School",
    "North Lake Senior Campus",
    "Palmyra Primary School",
    "Phoenix Primary School",
    "Samson Primary School",
    "Southern Districts Senior Citizens Centre",
    "Southern Districts Senior Citizens Centre (Drive-in)",
    "Southwell Primary School",
    "=Polling Place Totals",
    *DECLARATION,
    *ENDING,
)
WILLAGEE_NUMBERS = """
59 307 296 83 745 47 792
113 788 495 173 1569 68 1637
121 1170 614 158 2063 94 2157
65 600 363 48 1076 72 1148
69 603 576 76 1324 60 1384
102 674 452 142 1370 86 1456
75 523 402 99 1099 60 1159
13 234 167 35 449 18 467
36 395 159 50 640 55 695
145 979 446 143 1713 100 1813
87 665 396 154 1302 73 1375
3 80 29 11 123 3 126
36 421 171 36 664 48 712
924 7439 4566 1208 14137 784 14921
138 1064 323 190 1715 39 1754
93 500 244 108 945 24 969
0 5 5 1 11 1 12
20 152 24 22 218 15 233
251 1721 596 321 2889 79 2968
1175 9160 5162 1529 17026 863 17889
"""
VASSE_ROWS = (
    "Acton Park Hall",
    "Busselton Community Resource Centre",
    "Busselton Primary School",
    "Busselton Senior High School",
    "Carbunup Hall",
    "Cowaramup Primary School",
    "Dunsborough Primary School",
    "Naturaliste Community Centre",
    "Rosa Brook Hall",
    "Vasse Primary School",
    "West Busselton Primary School",
    "Yallingup Hall",
    "Yoongarillup Hall",
    "~Special Institutions, Hospitals & Remotes",
    "=Polling Place Total",
    "~Early Votes (by post)",
    "~Early Votes (in person)",
    "~Provisional Votes",
    "=Count Centre Total",
    "!Total First Preferences Votes",
)
VASSE_NUMBERS = """
5 89 10 125 16 4 249 5 254
58 429 63 495 214 13 1272 44 1316
43 441 90 596 223 21 1414 38 1452
125 717 153 817 320 34 2166 98 2264
19 77 25 175 88 9 393 16 409
15 214 19 298 241 9 796 24 820
13 87 2 241 145 2 490 9 499
23 318 30 820 466 17 1674 51 1725
4 72 7 134 85 0 302 16 318
84 481 91 655 298 27 1636 52 1688
70 682 124 869 308 44 2097 66 2163
10 101 16 256 227 5 615 23 638
15 110 38 150 24 6 343 8 351
5 18 4 75 15 1 118 6 124
489 3836 672 5706 2670 192 13565 456 14021
55 351 36 825 250 29 1546 24 1570
142 1469 165 2273 647 53 4749 89 4838
0 0 0 1 0 0 1 0 1
197 1820 201 3099 897 82 6296 113 6409
686 5656 873 8805 3567 274 19861 569 20430
"""
COTTESLOE_ROWS = (
    "Cottesloe Civic Centre Lesser Hall",
    "Cottesloe Primary School",
    "Freshwater Bay Primary School",
    "Mosman Park Primary School",
    "Mount Claremont Primary School",
    "North Cottesloe Primary School",
    "North Fremantle Primary School",
    "Saint Hilda's Anglican School for Girls - Chidley Campus",
    "Swanbourne Primary School",
    "~Special Institutions, Hospitals & Remotes",
    "~Absent Votes",
    "~Early Votes (by post)",
    "~Early Votes (in person)",
    "~Provisional Votes",
    "!Total Votes",
)
# Votes for each candidate in ballot order, then informal and total votes; the report prints no formal column.
COTTESLOE_NUMBERS = """
755 37 32 106 251 115 7 22 1325
785 37 15 89 217 55 3 25 1226
762 78 31 118 252 74 13 29 1357
581 42 33 176 355 62 4 28 1281
1166 82 45 165 422 98 16 47 2041
854 43 41 76 287 118 6 21 1446
346 32 28 98 305 76 7 28 920
577 29 16 102 132 26 3 21 906
980 54 26 91 326 101 11 26 1615
126 8 3 14 20 1 1 13 186
0 0 0 0 0 0 0 0 0
1729 82 67 259 358 87 26 46 2654
2202 81 64 342 623 164 15 58 3549
9 0 1 0 7 0 0 0 17
10872 605 402 1636 3555 977 112 364 18523
"""

CAUSES = {
    "2001-11-24": "The vacancy followed the resignation of the Hon. Hendy Cowan on 16 October 2001.",
    "2006-03-11": "The vacancy followed the resignation of the Hon. Geoffrey Gallop on 25 January 2006.",
    "2007-02-03": "The vacancy followed the resignation of the Hon. Norman Richard Marlborough on 9 November 2006.",
    "2008-02-23": "The vacancy followed the death of the Hon. Trevor Raymond Sprigg on 17 January 2008.",
    "2009-05-16": "The vacancy followed the resignation of the Hon. James Andrew McGinty on 3 April 2009.",
    "2009-11-28": "The vacancy followed the resignation of the Hon. Alan John Carpenter on 2 October 2009.",
    "2010-10-02": "The vacancy followed the resignation of the Hon. Alannah MacTiernan on 20 July 2010.",
    "2014-10-18": "The vacancy followed the resignation of the Hon. Troy Buswell on 3 September 2014.",
    "2023-07-29": "The vacancy followed the resignation of the Hon. Mark McGowan.",
}

BYES = [
    Bye(
        "2001-06-09",
        "Nedlands",
        "1994",
        f"{DOCS}/2001_Nedlands_By-election_Report.pdf",
        NEDLANDS,
        23_723,
        0,
        (0, 3, 8439, 7362),
        declared=(6887, 2912, 1928, 2214, 254, 93, 583, 960, 372),
    ),
    Bye(
        "2001-11-24",
        "Merredin",
        "1994",
        f"{DOCS}/2001_Merredin_By-election_Report.pdf",
        MERREDIN,
        12_447,
        1,
        None,
        cast=10_744,
        note="The report prints the number of electors, the votes recorded, the candidates, and the winner's "
        "majority of 1,393 votes, but not the votes for each candidate.",
    ),
    Bye(
        "2006-03-11",
        "Victoria Park",
        "2003",
        f"{DOCS}/2006_VictoriaPark_By-election_Report.pdf",
        VICTORIA_PARK,
        25_580,
        7,
        (7, 10, 9632, 6111),
        labels=VICTORIA_PARK_ROWS,
        first="Bentley Community Centre",
    ),
    Bye(
        "2007-02-03",
        "Peel",
        "2003",
        f"{DOCS}/2007_Peel_By-election_Report.pdf",
        PEEL,
        27_907,
        4,
        (4, 7, 13811, 7570),
        labels=PEEL_ROWS,
        first="Calista Primary School",
    ),
    Bye(
        "2008-02-23",
        "Murdoch",
        "2003",
        f"{DOCS}/2008_Murdoch_By-election_Report.pdf",
        MURDOCH,
        28_882,
        0,
        (0, 3, 14488, 6392),
        labels=MURDOCH_ROWS,
        rows=MURDOCH_NUMBERS,
    ),
    Bye(
        "2009-05-16",
        "Fremantle",
        "2007",
        f"{DOCS}/2009_Fremantle_By-election_Report.pdf",
        FREMANTLE,
        23_719,
        9,
        (6, 9, 9100, 10664),
        labels=FREMANTLE_ROWS,
        rows=FREMANTLE_NUMBERS,
    ),
    Bye(
        "2009-11-28",
        "Willagee",
        "2007",
        f"{DOCS}/2009_Willagee_By-election_Report.pdf",
        WILLAGEE,
        23_082,
        1,
        (1, 2, 10316, 6705),
        labels=WILLAGEE_ROWS,
        rows=WILLAGEE_NUMBERS,
    ),
    Bye(
        "2010-10-02",
        "Armadale",
        "2007",
        f"{DOCS}/2010_Armadale_By-election_Report.pdf",
        ARMADALE,
        24_314,
        0,
        (0, 1, 12159, 5140),
        labels=ARMADALE_ROWS,
        first="Armadale Primary School",
    ),
    Bye(
        "2014-10-18",
        "Vasse",
        "2011",
        f"{DOCS}/Vasse%202014%20By-election%20Report.PDF",
        VASSE,
        24_739,
        3,
        (1, 3, 9328, 10520),
        labels=VASSE_ROWS,
        rows=VASSE_NUMBERS,
    ),
    Bye(
        "2018-03-17",
        "Cottesloe",
        "2015",
        "/sites/default/files/content/2018_Cottesloe_By_election_Report_online3.pdf",
        COTTESLOE,
        27_831,
        0,
        (0, 4, 12738, 5416),
        labels=COTTESLOE_ROWS,
        formal_column=False,
        rows=COTTESLOE_NUMBERS,
    ),
    Bye(
        "2018-06-23",
        "Darling Range",
        "2015",
        "/sites/default/files/content/Documents 2018 by-election/2018_DAR_By_election_Report_final_online.pdf",
        DARLING_RANGE,
        32_571,
        3,
        (3, 6, 13039, 11331),
        labels=DARLING_RANGE_ROWS,
        formal_column=False,
        first="Armadale Primary School",
    ),
    Bye(
        "2022-09-17",
        "North West Central",
        "2019",
        NWC,
        NORTH_WEST_CENTRAL,
        11_189,
        3,
        (3, 11, 3071, 2008),
        labels=NORTH_WEST_CENTRAL_ROWS,
        formal_column=False,
        first="Carnarvon",
    ),
    Bye(
        "2023-07-29",
        "Rockingham",
        "2019",
        "/sites/default/files/RBE Report.pdf",
        ROCKINGHAM,
        30_213,
        0,
        (0, 6, 13412, 8443),
        declared=(10791, 1487, 3868, 262, 1081, 193, 3488, 534, 170, 630),
        extra=(
            ("Bungaree Primary School", 551, False),
            ("Charthouse Primary School", 1278, False),
            ("Cooloongup Primary School", 700, False),
            ("East Waikiki Primary School", 388, False),
            ("Hillman Primary School", 583, False),
            ("Rockingham Beach Primary School", 737, False),
            ("Rockingham Polling Centre", 712, False),
            ("Rockingham Senior High School", 780, False),
            ("Safety Bay Primary School", 1578, False),
            ("Safety Bay Senior High School", 951, False),
            ("Early votes (in person)", 10_714, True),
            ("Early votes (by post)", 3_350, True),
            ("Mobile polling votes", 122, True),
            ("Provisional votes", 60, True),
        ),
    ),
]


# Reading -------------------------------------------------------------------------------------------------------

INTEGER = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+$")
PERCENT = re.compile(r"^\d+(?:\.\d+)?%$")


def tail_ints(line: str, width: int) -> list[int] | None:
    """The first `width` whole numbers of a line's trailing run of numbers and percentages, when it has that many."""
    words = line.split()
    start = len(words)
    while start > 0 and (INTEGER.match(words[start - 1]) or PERCENT.match(words[start - 1])):
        start -= 1
    found = [waec.integer(w) for w in words[start:] if INTEGER.match(w)]
    if len(found) == width - 1 and start > 0:
        glued = re.search(r"\D(\d+)$", words[start - 1])  # a place's name set against the first number: "Sch7"
        if glued:
            found.insert(0, int(glued[1]))
    return found[:width] if len(found) >= width else None


def text_rows(link: str, first: str, width: int, count: int) -> list[list[int]]:
    lines = waec.layout(link).splitlines()
    start = next(i for i, line in enumerate(lines) if first in line)
    out: list[list[int]] = []
    for line in lines[start:]:
        found = tail_ints(line, width)
        axis = (
            found is not None
            and found[0] == 0
            and found[1] > 0
            and len({b - a for a, b in itertools.pairwise(found)}) == 1
        )
        if found is not None and not axis:  # a chart's scale, 0 1000 2000 ..., is not a row
            out.append(found)
            if len(out) == count:
                return out
    raise ValueError(f"{link}: {len(out)} rows of {count}")


@dataclass
class Table:
    places: list[Place]
    declared: Place
    sums: dict[str, Place]


def table(b: Bye) -> Table:
    n = len(b.candidates)
    width = n + (3 if b.formal_column else 2)
    if b.rows:
        rows = [[waec.integer(w) for w in line.split()] for line in b.rows.strip().splitlines()]
    else:
        rows = text_rows(url(b.report), b.first, width, len(b.labels))
    if len(rows) != len(b.labels) or any(len(r) != width for r in rows):
        raise ValueError(f"{b.day}: {len(rows)} rows for {len(b.labels)} labels")
    name = f"{b.district} {b.day}"
    places: list[Place] = []
    sums: dict[str, Place] = {}
    declared: Place | None = None
    group: list[Place] = []
    for label, row in zip(b.labels, rows, strict=True):
        votes = row[:n]
        formal, informal, total = row[n:] if b.formal_column else (sum(votes), row[n], row[n + 1])
        place = Place(label[1:] if label[0] in "~=!" else label, votes, formal, informal, total)
        if label[0] == "=":
            for column in range(n + 3):
                cells = [[*g.votes, g.formal, g.informal, g.total][column] for g in group]
                if sum(cells) != [*votes, formal, informal, total][column]:
                    raise ValueError(f"{name}: {place.name} column {column + 1} adds up to {sum(cells)}")
            sums[place.name] = place
            group = []
        elif label[0] == "!":
            declared = place
        else:
            place.kind = "aggregate" if label[0] == "~" else "standard"
            places.append(place)
            group.append(place)
    if declared is None:
        raise ValueError(f"{name}: no district total")
    waec.check(name, places, declared, n, sums)
    return Table(places, declared, sums)


# Parties -------------------------------------------------------------------------------------------------------

BY_NAMES = {
    "Liberal Party": "Liberal Party",
    "The Liberal Party of Australia (Western Australia Division) Inc": "Liberal Party",
    "ALP": "WA Labor",
    "The Greens (WA) Inc": "The Greens (WA)",
    "National Party": "The Nationals WA",
    "National Party of Australia (WA) Inc": "The Nationals WA",
    "Democrat": "Australian Democrats",
    "WESTERN AUSTRALIAN PARTY": "Western Australia Party",
    "NO MANDATORY VACCINATION PARTY": "No Mandatory Vaccination",
    "Citizens Electoral Council": "Citizens Electoral Council",
}


def party_name(printed: str) -> str | None:
    printed = printed.replace("’", "'").strip()
    if printed in waec.NO_PARTY:
        return None
    return BY_NAMES.get(printed) or waec.PARTY_NAMES.get(printed, printed)


def by_parties() -> dict[str, set[str]]:
    """Each party's name and the names the by-election candidate lists print for it."""
    out: dict[str, set[str]] = {}
    for b in BYES:
        for _, _, printed in b.candidates:
            name = party_name(printed)
            if name is not None:
                out.setdefault(name, set()).add(printed.replace("’", "'").strip())
    return out


def new_parties() -> dict[str, set[str]]:
    known = waec.printed_parties()
    return {name: printed for name, printed in by_parties().items() if name not in known}


# Packages ------------------------------------------------------------------------------------------------------


def files() -> list[str]:
    return [url(b.report) for b in BYES]


def packages() -> list[str]:
    return [f"{SOURCE}/by-registry", *(f"{SOURCE}/by-{b.day}" for b in BYES)]


def registry() -> Package:
    names = new_parties()
    p = Package(
        f"{SOURCE}/by-registry",
        SOURCE,
        f"The {len(names)} parties on the Western Australian Electoral Commission's by-election candidate lists "
        f"that its general election lists do not have, with the {len(BYES)} by-election report files",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(AUSTRALIA)
    for link in files():
        p.artifact(raw.cached(SOURCE, link), WAEC)
    for name, printed in sorted(names.items()):
        id = waec.party_id(name)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
        for other in sorted(printed - {name}):
            p.name(id, other, "en", "historical", False)
    return p


METHOD = (
    "First preference votes for each candidate at each polling place, from the Western Australian Electoral "
    "Commission's report on the by-election. Absent, early (postal and in person), provisional, and special "
    "institution, hospital, and remote votes are reported once for the district and stored as one unit each. Informal "
    "ballot papers are those that do not count, and valid votes are the formal ballot papers. The enrolment is stored "
    "on a separate unit of the district. The report's final two-candidate preferred count is given in a note, because "
    "the archive stores a count only at the first preference stage. Official outcomes are stored as declared."
)
PICTURE = (
    " The report prints this table as a picture, so its figures were keyed in from the printed page and checked "
    "against the table's own row and column sums."
)
NO_FORMAL = " The table prints no column of formal votes, so the archive stores the sum of each row's candidate votes."
TOTALS = (
    "The report prints each candidate's votes and the informal votes for the whole district, and gives no count for "
    "each polling place. The enrolment is stored on a separate unit of the district. The final two-candidate "
    "preferred count is given in a note. Official outcomes are stored as declared."
)
PLACE_TOTALS = (
    "The report prints each candidate's votes and the informal votes for the whole district, and the total votes "
    "cast at each polling place and by each early and mobile method, but not each candidate's votes at each polling "
    "place. Each polling place and method is stored as one unit with its total votes. The enrolment is stored on a "
    "separate unit of the district. The final two-candidate preferred count is given in a note. Official outcomes "
    "are stored as declared."
)


def by_election(b: Bye) -> Package:
    when = date.fromisoformat(b.day)
    slug = changeset.slug(b.district)
    p = Package(
        f"{SOURCE}/by-{b.day}",
        SOURCE,
        f"The {when.year} {b.district} by-election for the Western Australian Legislative Assembly"
        + (", first preferences by polling place" if b.labels else "")
        + ", from the Western Australian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/by-registry"],
    )
    p.external.add(AUSTRALIA)
    p.uses(raw.cached(SOURCE, url(b.report)))
    election_id = key("election", b.day, "by", slug)
    path = f"au/wa/{b.day}-by-election-{slug}"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="by_election",
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{when.year} {b.district} state by-election", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    cause = CAUSES.get(b.day)
    if cause:
        p.add("note", id=key("note", election_id), entity_id=election_id, kind="context", body_md=cause, lang="en")
    district = waec.area("au-wa.district", f"{b.plan}:{slug}")
    contest = key("contest", b.day, "by", slug)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=ASSEMBLY,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{b.district}, Legislative Assembly", "en", "official")
    p.add("slug", path=f"{path}/assembly-{slug}", entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=WAEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD + (PICTURE if b.rows else "") + ("" if b.formal_column else NO_FORMAL)
    if not b.labels:
        method = (PLACE_TOTALS if b.extra else TOTALS) if b.declared else b.note
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    cands = [Candidate(s, g, party) for s, g, party in b.candidates]
    options: list[str] = []
    for position, c in enumerate(cands, 1):
        option = key("option", contest, position)
        options.append(option)
        name = party_name(c.party)
        party = waec.party_id(name) if name else None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=c.ballot,
        )
        person = key("person", contest, position)
        p.add("person", id=person)
        p.name(person, c.display, "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", contest, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.ballot,
            member_of_party_id=party,
            is_independent=party is None,
        )
    declared: Place | None = None
    if b.labels:
        found = table(b)
        declared = found.declared
        seen: dict[str, int] = {}
        for place in found.places:
            seen[place.name] = seen.get(place.name, 0) + 1
            code = f"{b.day}:by:{slug}:{changeset.slug(place.name)}"
            if seen[place.name] > 1:
                code += f"-{seen[place.name]}"
            unit = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRALIA,
                scheme_code="au-wa.polling_place",
                code=code,
                area_relation="within",
                area_id=district,
                kind=place.kind,
            )
            p.name(unit, place.name if place.kind == "standard" else f"{b.district}: {place.name}", "en", "official")
            for option, value in zip(options, place.votes, strict=True):
                put(p, result_set, unit, option, VOTES, value)
            put(p, result_set, unit, None, VALID, place.formal)
            put(p, result_set, unit, None, INVALID, place.informal)
            put(p, result_set, unit, None, CAST, place.total)
    elif b.declared:
        votes, informal = list(b.declared[:-1]), b.declared[-1]
        declared = Place("Total", votes, sum(votes), informal, sum(votes) + informal)
    if not b.labels:
        count = key("unit", b.day, "by", slug, "count")
        p.add(
            "reporting_unit",
            id=count,
            polity_id=AUSTRALIA,
            scheme_code="au-wa.polling_place",
            code=f"{b.day}:by:{slug}:count",
            area_relation="within",
            area_id=district,
            kind="aggregate",
        )
        p.name(count, f"{b.district}: district count", "en", "official")
        if declared is not None:
            for option, value in zip(options, declared.votes, strict=True):
                put(p, result_set, count, option, VOTES, value)
            put(p, result_set, count, None, VALID, declared.formal)
            put(p, result_set, count, None, INVALID, declared.informal)
            if not b.extra:
                put(p, result_set, count, None, CAST, declared.total)
        elif b.cast is not None:
            put(p, result_set, count, None, CAST, b.cast)
    for label, count, aggregate in b.extra:
        code = f"{b.day}:by:{slug}:{changeset.slug(label)}"
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRALIA,
            scheme_code="au-wa.polling_place",
            code=code,
            area_relation="within",
            area_id=district,
            kind="aggregate" if aggregate else "standard",
        )
        p.name(unit, f"{b.district}: {label}" if aggregate else label, "en", "official")
        put(p, result_set, unit, None, CAST, count)
    roll = key("unit", b.day, "by", slug, "roll")
    p.add(
        "reporting_unit",
        id=roll,
        polity_id=AUSTRALIA,
        scheme_code="au-wa.polling_place",
        code=f"{b.day}:by:{slug}:roll",
        area_relation="within",
        area_id=district,
        kind="aggregate",
    )
    p.name(roll, f"{b.district}: electoral roll", "en", "official")
    put(p, result_set, roll, None, REGISTERED, b.enrolled)
    p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=b.enrolled)
    if declared is not None:
        for measure, value in ((CAST, declared.total), (INVALID, declared.informal), (VALID, declared.formal)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, declared.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    elif b.cast is not None:
        p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=b.cast)
    if b.pair is not None:
        first, second, one, two = b.pair
        if b.winner != (first if one > two else second):
            raise ValueError(f"{b.day}: the final count does not elect the declared member")
        body = (
            f"After the distribution of preferences the two remaining candidates held "
            f"{cands[first].display} {one:,} and {cands[second].display} {two:,} votes."
        )
        formal = declared.formal if declared is not None else None
        if formal is not None and formal > one + two:
            body += f" {formal - one - two:,} ballot papers exhausted before the final count."
        p.add(
            "note", id=key("note", result_set, "final"), entity_id=result_set, kind="context", body_md=body, lang="en"
        )
    if not b.declared and not b.labels:
        p.add(
            "note",
            id=key("note", result_set, "final"),
            entity_id=result_set,
            kind="context",
            body_md=f"{cands[b.winner].display} was declared elected with a majority of 1,393 votes.",
            lang="en",
        )
    finalists = {b.pair[0], b.pair[1]} if b.pair else set()
    for index, option in enumerate(options):
        kind = "elected" if index == b.winner else "lost" if index in finalists or b.pair is None else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    return p


def fetch() -> None:
    for link in files():
        raw.fetch(SOURCE, link, suffix=".pdf")


def parse() -> None:
    registry().write()
    for b in BYES:
        by_election(b).write()


def emit() -> None:
    package.run("load", packages())
