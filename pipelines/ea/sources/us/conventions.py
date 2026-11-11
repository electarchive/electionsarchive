import base64
import io
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from itertools import product
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.wikipedia_primaries import API
from ea.store import DATA, connect

SOURCE = "us.conventions"
VERSION = "1"
VOTES, DELEGATION = 1, 30
PLACES = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia (U.S. state)": "GA",
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
    "Washington, D.C.": "DC",
    "Puerto Rico": "PR",
    "Guam": "GU",
    "American Samoa": "AS",
    "Virgin Islands, U.S.": "VI",
    "Northern Marianas": "MP",
}
REPORTED = (
    "The delegations of Alaska, the District of Columbia, and Utah announced votes for candidates who had left the "
    "race (Alaska: 11 Trump, 12 Cruz, and 5 Rubio; the District of Columbia: 10 Rubio and 9 Kasich; Utah: 40 Cruz). "
    "Under the party's rules those delegates were bound to the remaining candidate, and the secretary recorded every "
    "one of them for Trump, as stored here."
)
ABROAD_NOTE = (
    "Democrats Abroad is stored as an unlocated delegation, apart from the states and territories. Eight of its "
    "superdelegates cast half votes, so its 21 delegates cast 17 votes, and its total here is the 17 votes. One "
    "superdelegate position assigned to no state did not vote and is not counted in any delegation."
)
ABROAD_NOTE_2020 = (
    "Democrats Abroad is stored as an unlocated delegation, apart from the states and territories. Eight of its "
    "superdelegates cast half votes, so its 21 delegates cast 17 votes, and its total here is the 17 votes. Two "
    "superdelegate positions assigned to no state did not vote and are not counted in any delegation. Delegates "
    "voted remotely, and the convention's ceremonial roll call of the states announced the result."
)
LISTED = (
    "The article's table gives each delegation's votes by candidate. A delegation's total is the delegates it held, "
    "which the table shows as the votes cast for candidates plus its abstentions."
)
LISTED_2024 = (
    "The article's table gives each delegation's delegates and its votes for Trump and Haley; the delegates not "
    "voting for either are stored as abstentions. The Chair announced 2,387 votes for Trump, while the delegations' "
    "votes add up to 2,388, as stored here."
)
CAPTIONS_2020 = (
    "https://vimarsana.com/article/cspan-20200824-132500-republican-national-convention-nomination-vote-in-charlotte-nc",
    "https://vimarsana.com/article/cspan-20200825-070700-republican-national-convention-nomination-vote-in-charlotte-nc-part-2",
)
CAPTIONS_METHOD = (
    "The roll call of the states on the presidential nomination, one ballot per contest, with each delegation's "
    "votes as the convention secretary announced them. The party has published no official report of the proceedings "
    "or per-state tally for this convention, so the votes are read from the caption transcript of C-SPAN's broadcast "
    "of the roll call, in which each delegation chair and then the secretary state the delegation's votes, until an "
    "official tally is found. Every delegation voted for the one candidate, so its total is its votes."
)
CAPTIONS_CAVEAT = (
    "The captions garble some names and numbers, so each delegation's votes are the figure the secretary's "
    "announcement and the delegation chair's statement agree on; New Mexico's secretary announcement dropped the "
    "number, and its chair's 22 is stored. The 56 delegations add up to the 2,550 votes the chair announced."
)
CONVENTIONS: list[dict[str, Any]] = [
    {
        "key": "2016-02-01:president:republican",
        "party": "republican",
        "title": "2016 Republican National Convention",
        "table": "Republican presidential nomination ballot",
        "days": (date(2016, 7, 18), date(2016, 7, 21)),
        "ballots": [date(2016, 7, 19)],
        "required": 1237,
        "nominee": "Trump",
        "city": "Cleveland",
        "caveat": REPORTED,
    },
    {
        "key": "2016-02-01:president:democratic",
        "party": "democratic",
        "title": "2016 Democratic National Convention",
        "table": "Democratic presidential nomination ballot",
        "days": (date(2016, 7, 25), date(2016, 7, 28)),
        "ballots": [date(2016, 7, 26)],
        "required": 2383,
        "nominee": "Clinton",
        "city": "Philadelphia",
        "caveat": ABROAD_NOTE,
    },
    {
        "key": "2020-02-03:president:democratic",
        "party": "democratic",
        "title": "2020 Democratic National Convention",
        "table": "Democratic presidential nomination ballot",
        "days": (date(2020, 8, 17), date(2020, 8, 20)),
        "ballots": [date(2020, 8, 18)],
        "required": 1991,
        "nominee": "Biden",
        "city": "Milwaukee",
        "caveat": ABROAD_NOTE_2020,
    },
    {
        "key": "2012-01-03:president:republican",
        "party": "republican",
        "title": "2012 Republican National Convention",
        "marker": '{| class="wikitable"\n!Candidates',
        "days": (date(2012, 8, 27), date(2012, 8, 30)),
        "ballots": [date(2012, 8, 28)],
        "required": 1144,
        "nominee": "Romney",
        "city": "Tampa",
        "caveat": LISTED,
    },
    {
        "key": "2024-01-15:president:republican",
        "party": "republican",
        "title": "2024 Republican National Convention",
        "marker": "=== Results by state or territory ===",
        "days": (date(2024, 7, 15), date(2024, 7, 18)),
        "ballots": [date(2024, 7, 15)],
        "required": 1215,
        "nominee": "Trump",
        "city": "Milwaukee",
        "caveat": LISTED_2024,
    },
    {
        "key": "2020-02-03:president:republican",
        "party": "republican",
        "title": "2020 Republican National Convention",
        "captions": CAPTIONS_2020,
        "tsv": "conventions_rnc_2020.tsv",
        "days": (date(2020, 8, 24), date(2020, 8, 27)),
        "ballots": [date(2020, 8, 24)],
        "required": 1276,
        "nominee": "Trump",
        "city": "Charlotte",
        "caveat": CAPTIONS_CAVEAT,
    },
]
ABROAD = "DA"
LISTED_PLACES = {"Georgia": "GA", "Virgin Islands": "VI", "Northern Mariana Islands": "MP"}
METHOD = (
    "The roll call of the states on the presidential nomination, one ballot per contest, with each delegation's "
    "votes as the convention secretary recorded them. The party has published no official report of the proceedings "
    "for this convention, so the votes are transcribed from the roll-call table in the English Wikipedia's article on "
    "the convention, pinned to the revision current when it was fetched, until an official tally is found. Each "
    "delegation's total is the delegates it held, abstentions included; abstentions are that total less the votes "
    "recorded for candidates."
)


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext|revid", "format": "json", "formatversion": 2}
    )


LEAF = "https://archive.org/download/{item}/page/n{leaf}.jpg"
EXPLOREUK = "https://exploreuk.uky.edu/dips/{item}/data/books_b92_77_27211776_{item}/{leaf:04d}.jpg"
GBOOKS = "https://books.google.com/books?id={gbooks}&pg=PA{leaf}"
HATHI = "https://babel.hathitrust.org/cgi/pt?id={htid}&seq={leaf}"
DNC_2024_IMAGE = "https://democrats.org/wp-content/uploads/2024/08/StatebyStateBreakdown.jpg"
DNC_2024_RELEASE = (
    "https://democrats.org/news/dnc-chair-harrison-and-dncc-chair-moore-announce-roll-call-voting-window-has-closed-"
    "lay-out-next-steps-for-vp-harris-to-officially-clinch-nomination/"
)
SHEET_2008 = "https://www.thegreenpapers.com/P08/D-VoteTallySheet-2008-09-02.pdf"
RNC = "sim_republican-national-convention-proceedings_{}"
TABLES: list[dict[str, Any]] = [
    {"table": "1968:republican:first", "item": RNC.format("august-5-8-1968"), "leaves": (409, 410)},
    {"table": "1968:republican:final", "item": RNC.format("august-5-8-1968"), "leaves": (419, 420)},
    {"table": "1972:republican", "item": RNC.format("august-21-23-1972_30"), "leaves": (361, 362)},
    {"table": "1976:republican", "item": RNC.format("august-16-19-1976_31"), "leaves": (451, 452)},
    {"table": "1980:republican", "item": RNC.format("july-14-17-1980"), "leaves": (479, 480)},
    {
        "table": "1984:republican",
        "item": RNC.format("august-20-23-1984_33"),
        "leaves": (444, 445),
        "columns": ("votes", "Reagan", None, "Abstention", None, None, None),
    },
    {
        "table": "1988:republican",
        "item": RNC.format("august-15-18-1988_34"),
        "leaves": tuple(range(539, 561)),
        "ballot": 1,
        "transcribed": "conventions_new_orleans_1988.tsv",
    },
    {
        "table": "2008:democratic",
        "pdf": SHEET_2008,
        "leaves": (1,),
        "ballot": 1,
        "transcribed": "conventions_denver_2008.tsv",
        "short": True,
    },
    {
        "table": "2024:democratic",
        "image": DNC_2024_IMAGE,
        "release": DNC_2024_RELEASE,
        "leaves": (1,),
        "ballot": 1,
        "transcribed": "conventions_dnc_2024.tsv",
        "short": True,
    },
]


def volume(
    item: str, year: str, offset: int, back: int, pages: dict[str, int | list[int]], party: str = "republican"
) -> list[dict[str, Any]]:
    specs = []
    for name, found in pages.items():
        found = [found] if isinstance(found, int) else found
        leaves = sorted({page - offset - step for page in found for step in range(back + 1)})
        specs.append({"table": f"{year}:{party}:{name}", "item": item, "leaves": tuple(leaves)})
    return specs


def ballots(
    item: str, year: str, windows: dict[int, tuple[int, ...]], scatter: bool = False, party: str = "republican"
) -> list[dict[str, Any]]:
    return [
        {
            **({"scatter": True} if scatter else {}),
            "table": f"{year}:{party}" if len(windows) == 1 else f"{year}:{party}:b{n}",
            "item": item,
            "leaves": tuple(range(low, high + 1)),
            "ballot": n,
        }
        for n, (low, high) in windows.items()
    ]


def leaves(
    item: str, year: str, found: dict[str, tuple[int, ...]], scatter: bool = False, party: str = "republican"
) -> list[dict[str, Any]]:
    return [
        {
            **({"scatter": True} if scatter else {}),
            "table": f"{year}:{party}" if name == "" else f"{year}:{party}:{name}",
            "item": item,
            "leaves": spec,
        }
        for name, spec in found.items()
    ]


TABLES += [
    {"table": "1924:republican", "htid": "mdp.39015030802287", "leaves": (219, 220)},
    {"table": "1928:republican", "htid": "mdp.39015030802295", "leaves": (284,)},
]
TABLES += [
    *leaves(
        "officialreportof00repuiala",
        "1920",
        {
            "b1": (227, 230),
            "b2": (232, 233),
            "b4": (240, 241),
            "b7": (252, 253),
            "b8": (259, 260),
            "b9": (262, 263),
        },
        scatter=True,
    ),
    *(
        {
            "scatter": True,
            "table": f"1920:republican:{name}",
            "item": "officialreportof00repuiala",
            "leaves": found,
            "reads": only,
        }
        for name, found, only in (
            ("b3", (235, 236), {236: "scatter"}),
            ("b5", (247, 248), {248: "scatter"}),
            ("b6", (248, 249), {248: "table"}),
            ("b10", (269, 270), {270: "scatter"}),
        )
    ),
    *leaves("officialreportsi00repuiala", "1916", {"b1": (212, 213)}, scatter=True),
    {
        "scatter": True,
        "table": "1916:republican:b2",
        "item": "officialreportsi00repuiala",
        "leaves": (215, 216),
        "ballot": 2,
    },
    {"table": "1916:republican:b3", "item": "officialreportsi00repuiala", "leaves": (234, 235), "ballot": 3},
    *ballots("officialreportfi00repuiala", "1912", {1: (445, 446)}),
    *ballots("officialreporto00repu", "1908", {1: (239, 239)}),
    *leaves("officialproceedi00repuiala", "1904", {"": (212,)}),
    *leaves("12threpubnaticon00blumrich", "1900", {"": (157,)}),
    *leaves("officialproceedi00repu", "1896", {"": (132,)}),
    *ballots("proceedingsoften00repu", "1892", {1: (148, 148)}),
    *leaves(
        "officialproceedi00repu_0",
        "1888",
        {"b2": (380,), "b3": (383, 384), "b5": (397,), "b7": (410,)},
    ),
    {"table": "1888:republican:b1", "item": "officialproceedi00repu_0", "leaves": (376, 377), "parts": {377: "top"}},
    *ballots("officialproceedi00repu_0", "1888", {4: (395, 395), 6: (407, 407), 8: (416, 416)}),
    *leaves("proceedingsofeig00repu", "1884", {"b2": (156,), "b3": (159,)}),
    {
        "table": "1884:republican:b1",
        "item": "proceedingsofeig00repu",
        "leaves": (150, 151),
        "parts": {150: "b1", 151: "head"},
    },
    {"table": "1884:republican:b4", "item": "proceedingsofeig00repu", "leaves": (171, 172), "ballot": 4},
    *ballots(
        "proceedingsofrep00inrepu",
        "1880",
        {1: (215, 215), 12: (238, 239), 16: (244, 245), 23: (257, 258), 26: (263, 263), 30: (276, 277)},
    ),
    *leaves(
        "proceedingsofrep00inrepu",
        "1880",
        {
            "b2": (219, 220),
            "b3": (223, 224),
            "b4": (225,),
            "b5": (226, 227),
            "b6": (228,),
            "b7": (229, 230),
            "b8": (231,),
            "b9": (232, 233),
            "b10": (234,),
            "b11": (235, 236),
            "b13": (240,),
            "b14": (241, 242),
            "b15": (243,),
            "b17": (246, 247),
            "b18": (248,),
            "b19": (251, 252),
            "b20": (253,),
            "b21": (254, 255),
            "b22": (256,),
            "b24": (259,),
            "b25": (261,),
            "b27": (264, 265),
            "b28": (266, 267),
            "b29": (269, 270),
            "b31": (279, 280),
            "b32": (281, 282),
            "b33": (283,),
            "b34": (285,),
            "b35": (286, 287),
            "b36": (288,),
        },
    ),
    *leaves(
        "proceedingsrepu00nelsgoog",
        "1876",
        {"b1": (84,), "b2": (87,), "b5": (103, 104), "b6": (105, 106), "b7": (108, 109)},
    ),
    *ballots("proceedingsrepu00nelsgoog", "1876", {3: (100, 101), 4: (101, 102)}),
    *leaves(
        "proceedingsoffir00inrepu",
        "1860",
        {"b1": (154,), "b2": (157,), "b3": (158,)},
    ),
    {
        "table": "1856:republican:informal",
        "item": "proceedingsoffir00inrepu",
        "leaves": (58,),
        "columns": (None, "Fremont", "McLean", "Banks", "Sumner", "Seward"),
        "electors": True,
    },
    {
        "table": "1856:republican:formal",
        "item": "proceedingsoffir00inrepu",
        "leaves": (63,),
        "columns": (None, "Fremont", "McLean", "Seward"),
        "electors": True,
    },
    {
        "table": "1864:republican",
        "item": "proceedingsoffir00inrepu",
        "leaves": (236, 237, 238, 239),
        "narrated": True,
        "announced": True,
    },
    {"table": "1872:republican", "item": "presidentialele00conggoog", "leaves": (42,), "columns": ("votes", "Grant")},
    {"table": "1868:republican", "item": "presidentialelec00repu", "leaves": (97,), "columns": ("Grant",)},
]
DNC_1912 = ((203, 204, 205), (207, 208), (211, 212), (212, 213, 214), (217, 218), (219, 220), (221, 222), (222, 223, 224), (225, 226), (227, 228), (230, 231, 232), (233, 234), (237, 238), (248, 249), (250, 251), (253, 254), (257, 258), (259, 260), (260, 261, 262), (262, 263), (266, 267), (270, 271, 272), (272, 273), (276, 277), (278, 279), (283, 284), (293, 294), (297, 298), (305, 306), (308, 309), (311, 312), (312, 313, 314), (315, 316), (318, 319), (320, 321), (322, 323), (323, 324, 325), (327, 328), (330, 331), (331, 332, 333), (333, 334, 335), (336, 337), (344, 345), (348, 349), (351, 352), (358, 359))  # fmt: skip
CHARLESTON = {1: 79, 2: 80, 3: 80, 4: 80, 5: 80, 6: 80, 7: 81, 8: 81, 9: 81, 10: 82, 11: 82, 12: 82, 13: 83, 14: 83, 15: 83, 16: 83, 17: 84, 18: 84, 19: 84, 20: 84, 21: 84, 22: 84, 23: 85, 24: 86, 25: 86, 26: 86, 27: 86, 28: 86, 29: 87, 30: 87, 31: 87, 32: 87, 33: 87, 34: 88, 35: 88, 36: 89, 37: 89, 38: 89, 39: 90, 40: 90, 41: 90, 42: 90, 43: 90, 44: 91, 45: 91, 46: 91, 47: 91, 48: 91, 49: 92, 50: 92, 51: 92, 52: 92, 53: 92, 54: 93, 55: 93, 56: 93, 57: 93}  # fmt: skip
DNC_1868 = (78, 80, 83, 86, 99, 102, 109, 112, 114, 117, 119, 122, 125, 127, 130, 132, 135, 138, 147, 150, 152)  # fmt: skip
TABLES += [
    *(
        {
            "table": f"1912:democratic:b{n}",
            "item": "officialreportof00demoiala",
            "leaves": found,
            "parts": {leaf: "head" for leaf in found if leaf in after}
            | {leaf: f"b{n}" for leaf in found if leaf in previous or (n, leaf) in {(19, 262), (40, 333)}},
        }
        for n, (previous, found, after) in enumerate(
            zip(((),) + DNC_1912[:-1], DNC_1912, DNC_1912[1:] + ((),), strict=True), start=1
        )
    ),
    {
        "table": "1900:democratic",
        "item": "official00demo",
        "leaves": (165, 166, 167),
        "parts": {165: "full"},
    },
    *leaves(
        "officialproce00de",
        "1896",
        {"b1": (330,), "b2": (335,), "b3": (338,), "b4": (340,), "b5": (346,)},
        party="democratic",
    ),
    *leaves("cu31924002684375", "1892", {"": (182,)}, party="democratic"),
    *leaves("officialproceedi0000demo_u0f5", "1884", {"b1": (266,), "b2": (280,)}, party="democratic"),
    *leaves("officialp00demo", "1880", {"b1": (124,), "b2": (139,)}, party="democratic"),
    *leaves("officialproc00demo", "1876", {"b1": (165,), "b2": (167,)}, party="democratic"),
    *leaves("officialproceed01convgoog", "1872", {"": (66,)}, scatter=True, party="democratic"),
    *leaves(
        "officialproceedi00demo",
        "1868",
        {f"b{n}": (leaf,) for n, leaf in enumerate(DNC_1868, start=1)},
        party="democratic",
    ),
    *leaves("officialproceed00demo", "1864", {"": (43,)}, party="democratic"),
    *(
        spec | {"transcribed": "conventions_charleston.tsv"}
        for spec in ballots(
            "officialpr00demo",
            "1860",
            {n: (start, min(start + 1, 93)) for n, start in CHARLESTON.items()},
            party="democratic",
        )
    ),
    *leaves("officialpr00demo", "1860", {"baltimore-b1": (168,), "baltimore-b2": (173,)}, party="democratic"),
    *leaves("officialproceedi00indemo", "1856", {"b13": (41,), "b14": (42,)}, party="democratic"),
    *(
        spec | {"crop": ("tl", "tr", "bl", "br")[(spec["ballot"] - 1) % 4]}
        if spec["ballot"] in (2, 6, 7, 8, 9, 12)
        else spec
        for spec in ballots(
            "officialproceedi00indemo",
            "1856",
            {n: ((n - 1) // 4 + 38,) * 2 for n in range(1, 13)} | {15: (44, 44), 16: (44, 44)},
            party="democratic",
        )
    ),
    {
        "table": "1856:democratic:b17",
        "item": "officialproceedi00indemo",
        "leaves": tuple(range(45, 58)),
        "narrated": True,
        "only": True,
    },
]
CAPTIONS_1852 = {1: 29, 2: 30, 3: 30, 4: 31, 5: 31, 6: 32, 7: 32, 8: 32, 9: 35, 10: 36, 11: 36, 12: 37, 13: 37, 14: 37, 15: 38, 16: 38, 17: 39, 18: 40, 19: 41, 20: 42, 21: 42, 22: 43, 23: 43, 24: 43, 25: 44, 26: 44, 27: 45, 28: 45, 29: 46, 30: 46, 31: 47, 32: 47, 33: 47, 34: 49, 35: 52, 36: 52, 37: 53, 38: 53, 39: 54, 40: 54, 41: 54, 42: 55, 43: 55, 44: 56, 45: 56, 46: 56, 47: 57, 48: 57}  # fmt: skip
TABLES += [
    spec | {"transcribed": "conventions_baltimore_1852.tsv"}
    for spec in ballots(
        "proceedingsofde00demo",
        "1852",
        {n: (leaf, leaf + 1) for n, leaf in CAPTIONS_1852.items()},
        party="democratic",
    )
]
TABLES += [
    {
        "table": "1852:democratic:b49",
        "item": "proceedingsofde00demo",
        "leaves": (65,),
        "ballot": 49,
        "scatter": True,
        "bycandidate": True,
    }
]
TABLES += [
    {
        "table": f"1848:democratic:b{n}",
        "gbooks": "tQIbAAAAYAAJ",
        "pdf_page": {13: 20, 14: 21, 15: 22},
        "leaves": leaves_,
        "ballot": n,
        "transcribed": "conventions_baltimore_1848.tsv",
    }
    for n, leaves_ in {1: (13,), 2: (14,), 3: (14,), 4: (15,)}.items()
]
TABLES += [
    {
        "table": "1908:democratic",
        "gbooks": "BZ1PAAAAYAAJ",
        "mf": "MF-609",
        "pdf_page": {246: 263, 247: 264},
        "leaves": (246, 247),
        "ballot": 1,
        "transcribed": "conventions_denver_1908.tsv",
        "short": True,
    }
]
EVENING_STAR_1932 = "https://chroniclingamerica.loc.gov/lccn/sn83045462/1932-07-02/ed-1/seq-4/"
TABLES += [
    {
        "table": "1932:democratic:b4",
        "manual": "MF-611/1932-07-02",
        "url": EVENING_STAR_1932,
        "leaves": (1,),
        "ballot": 4,
        "transcribed": "conventions_chicago_1932.tsv",
        "short": True,
    }
]
NILES = "sim_niles-national-register_{}"
TABLES += [
    {
        "table": "1835:democratic",
        "item": NILES.format("1835-05-30_48_1236"),
        "leaves": (11,),
        "ballot": 1,
        "transcribed": "conventions_baltimore_1835.tsv",
    },
    {
        "table": "1835:democratic:vp",
        "item": NILES.format("1835-05-30_48_1236"),
        "leaves": (11,),
        "ballot": 1,
        "transcribed": "conventions_baltimore_1835_vp.tsv",
    },
    {
        "table": "1832:democratic:vp",
        "item": "summaryproceedi00convgoog",
        "leaves": (8, 9),
        "ballot": 1,
        "transcribed": "conventions_baltimore_1832.tsv",
    },
    *(
        spec | {"transcribed": "conventions_baltimore_1844.tsv"}
        for spec in ballots(
            NILES.format("1844-06-01_66_1705"), "1844", dict.fromkeys(range(1, 10), (2, 2)), party="democratic"
        )
    ),
]
VICE_TABLES = {
    "1848:democratic:vp-b1": {"gbooks": "tQIbAAAAYAAJ", "pdf_page": {17: 24}, "leaves": (17,), "ballot": 1, "transcribed": "conventions_baltimore_1848_vp.tsv"},
    "1848:democratic:vp-b2": {"gbooks": "tQIbAAAAYAAJ", "pdf_page": {18: 25}, "leaves": (18,), "ballot": 2, "transcribed": "conventions_baltimore_1848_vp.tsv"},
    "1852:democratic:vp-b1": {"item": "proceedingsofde00demo", "leaves": (67,), "ballot": 1, "transcribed": "conventions_baltimore_1852_vp.tsv"},
    "1852:democratic:vp-b2": {"item": "proceedingsofde00demo", "leaves": (67, 68), "ballot": 2, "transcribed": "conventions_baltimore_1852_vp.tsv"},
    "1856:democratic:vp": {"item": "officialproceedi00indemo", "leaves": (65,), "ballot": 1, "transcribed": "conventions_cincinnati_1856_vp.tsv"},
    "1860:democratic:vp": {"item": "officialpr00demo", "leaves": (178,), "ballot": 1, "transcribed": "conventions_baltimore_1860_vp.tsv"},
    "1864:democratic:vp": {"item": "officialproceed00demo", "leaves": (52,), "ballot": 1, "transcribed": "conventions_chicago_1864_vp.tsv"},
    "1872:democratic:vp": {"item": "officialproceed01convgoog", "leaves": (71,), "ballot": 1, "transcribed": "conventions_baltimore_1872_vp.tsv"},
    "1876:democratic:vp": {"item": "officialproc00demo", "leaves": (179, 180), "ballot": 1, "transcribed": "conventions_st_louis_1876_vp.tsv"},
    "1856:republican:vp": {"item": "proceedingsoffir00inrepu", "leaves": (70,), "ballot": 1, "transcribed": "conventions_philadelphia_1856_vp.tsv"},
    "1860:republican:vp-b1": {"item": "proceedingsoffir00inrepu", "leaves": (165, 166), "ballot": 1, "transcribed": "conventions_chicago_1860_vp.tsv"},
    "1860:republican:vp-b2": {"item": "proceedingsoffir00inrepu", "leaves": (166,), "ballot": 2, "transcribed": "conventions_chicago_1860_vp.tsv"},
    "1864:republican:vp": {"item": "proceedingsoffir00inrepu", "leaves": (247,), "ballot": 1, "transcribed": "conventions_baltimore_1864_vp.tsv"},
    "1868:republican:vp-b1": {"item": "presidentialelec00repu", "leaves": (127,), "ballot": 1, "transcribed": "conventions_chicago_1868_vp.tsv"},
    "1868:republican:vp-b2": {"item": "presidentialelec00repu", "leaves": (129,), "ballot": 2, "transcribed": "conventions_chicago_1868_vp.tsv"},
    "1868:republican:vp-b3": {"item": "presidentialelec00repu", "leaves": (132,), "ballot": 3, "transcribed": "conventions_chicago_1868_vp.tsv", "short": True},
    "1880:republican:vp": {"item": "proceedingsofrep00inrepu", "leaves": (311,), "ballot": 1, "transcribed": "conventions_chicago_1880_vp.tsv", "short": True},
    "1884:republican:vp": {"item": "proceedingsofeig00repu", "leaves": (186, 187), "ballot": 1, "transcribed": "conventions_chicago_1884_vp.tsv", "short": True},
    "1888:republican:vp": {"item": "officialproceedi00repu_0", "leaves": (449, 450), "ballot": 1, "transcribed": "conventions_chicago_1888_vp.tsv", "short": True},
}  # fmt: skip
TABLES += [{"table": name} | spec for name, spec in VICE_TABLES.items()]
TABLES += [
    {"table": "1904:democratic", "uky": "xt7tx921cw1r", "leaves": (259,)},
    {"table": "1920:democratic:b1", "htid": "mdp.39015030799285", "leaves": (293, 294, 295), "rotate": True},
    {"table": "1920:democratic:b44", "htid": "mdp.39015030799285", "leaves": (504, 505)},
    {"table": "1924:democratic:b1", "htid": "mdp.39015030799251", "leaves": (356, 357, 358), "rotate": True},
    {"table": "1948:democratic", "htid": "mdp.39015030799574", "leaves": tuple(range(314, 325)), "narrated": True},
    {"table": "1940:democratic", "htid": "uc1.$b619005", "leaves": (225, 226)},
    {"table": "1944:democratic", "htid": "uc1.$b619006", "leaves": (125, 126, 127, 128, 129, 130), "narrated": True},
    {"table": "1956:democratic", "htid": "uc1.$b619008", "leaves": (463, 464)},
]
FIRST_1864 = (
    "The proceedings print two tabulations of the one ballot. As first called (page 43), the roll gave George B. "
    "McClellan 174, Thomas H. Seymour 38, Horatio Seymour 12, Charles O'Conor \u00bd, and 1\u00bd blank, of 226. Kentucky "
    "and California then cast their votes for McClellan and Ohio revised its vote to 15 for McClellan and 6 for "
    "Seymour before the result was announced; the revised ballot, stored here, is the one the convention declared."
)
CHARLESTON_RULING = (
    "The Chair ruled that a nomination needed two-thirds of the full convention of 303, or 202 votes, though "
    "most of the delegations of eight Southern states had withdrawn and about 252 votes were cast. No candidate "
    "reached 202 in the 57 ballots, and on May 3 the convention adjourned to meet again in Baltimore."
)
BALTIMORE_RULING = (
    "The convention, reassembled in Baltimore, voted by the delegates present, 190\u00bd on the first ballot and 194\u00bd on the "
    "second. The Chair held that the original rule, two-thirds of the votes cast, governed and that the Charleston "
    "instruction to count 202 had not changed it; the needed figure stored here is two-thirds of the votes cast on "
    "the ballot. After the second ballot the convention resolved that Douglas, having received two-thirds of all the "
    "votes given, was the regular nominee, and the President declared him nominated by the unanimous vote of the convention."
)
BALTIMORE_1852 = (
    "The proceedings give the rule as two-thirds of the votes. South Carolina cast no vote, and the other 30 "
    "delegations held 288 votes, so the needed figure stored here is 192. No candidate reached it before the "
    "forty-ninth ballot, on which Franklin Pierce was nominated."
)
EVENING_STAR_1932_CAVEAT = (
    "The archive has not located a free copy of the official proceedings' roll call, so this ballot is the "
    "state-by-state table the Washington Evening Star printed on July 2, 1932 (page A-4). It is a default to be "
    "superseded by the official proceedings. The table prints the fourth ballot only; the first three ballots by "
    "state are not loaded. Pennsylvania's 76 votes include 5½ not recorded for a candidate. The printed totals are "
    "945 for Franklin D. Roosevelt, 190½ for Alfred E. Smith, 5½ for Newton D. Baker, 3½ for Albert C. Ritchie, 3 for "
    "George White, and 1 for James M. Cox, 1,148½ votes cast of 1,154; two-thirds, 770, were needed."
)
NILES_1835 = (
    "The archive has found no free copy of a separate report of this convention's proceedings; the roll is the "
    "state-by-state count as Niles' "
    "Weekly Register reprinted it from the official statement in the Baltimore Republican (page 228). Every "
    "delegation's votes went to Martin Van Buren, 265 in all; the count gives no votes held by each delegation, "
    "and the delegations were entitled to as many votes as their states had presidential electors."
)
NILES_1844 = (
    "The archive has found no free copy of a separate report of this convention's proceedings; the roll is the "
    "table of all nine ballots that "
    "Niles' National Register printed (page 211), which gives 266 votes to the 25 delegations present, South "
    "Carolina not being represented, and 178 votes as needed to nominate. The table has columns only for the "
    "leading candidates, so a delegation's row can add to less than its votes; the rows are stored as printed."
)
VICE_METHOD = (
    "The ballot of the states on the vice-presidential nomination, as reported in {}, read from the scanned page "
    "and checked against the printed totals."
)
BALTIMORE_1832 = (
    "The convention took no ballot for president: it resolved to concur in the nominations Andrew Jackson had "
    "already received, and its only ballot was this one for vice president. The counts are those the narrative of "
    "the proceedings gives for each candidate by state (pages 6 and 7); the footnote table of each state's "
    "delegates and votes is blank in the scanned copy. Missouri was not represented. Each delegation cast as many "
    "votes as its state would have presidential electors under the new apportionment, and two-thirds of the 283 "
    "votes given, 189, were needed. The delegates of the District of Columbia, who had no vote, were afterward "
    "allowed to record theirs for Martin Van Buren; that vote is not stored."
)
NILES_1835_VICE = (
    "The archive has found no free copy of a separate report of this convention's proceedings; the count is the "
    "state-by-state table of the ballot for vice president as Niles' Weekly Register printed it (page 228). The "
    "table gives no votes held by each delegation. Two-thirds of the 265 votes given, 177, were needed to nominate."
)
PROCEEDINGS_VICE = VICE_METHOD.format("the convention's official proceedings")
REPUBLICAN_VICE_1856 = "The informal ballot before this one is printed only as totals, without a count by state, so it is not stored: William L. Dayton 253, Abraham Lincoln 110, Nathaniel P. Banks 46, David Wilmot 43, Charles Sumner 35, Jacob Collamer 15, John A. King 9, Samuel C. Pomeroy 8, Thomas H. Ford 7, Cassius M. Clay 3, Henry C. Carey 3, Joshua R. Giddings 2, W. S. Johnson 2, Henry Wilson 2, and Aaron S. Pennington 1. The table stored here is the formal ballot as called; Pennsylvania's 2 scattering votes are not stored. The delegations that had voted for others then changed their votes, and Dayton was declared nominated unanimously, with 561 votes. The proceedings print no totals for this table and no figure needed to nominate."
REPUBLICAN_VICE_1860 = (
    "The proceedings print no figure needed to nominate. Hannibal Hamlin was declared nominated on the second ballot."
)
REPUBLICAN_VICE_1864 = "The table is the ballot after the delegations' corrections; Andrew Johnson was then declared nominated. The proceedings print no figure needed to nominate."
REPUBLICAN_VICE_1868 = {
    1: "The figure needed stored here, 325, is the one the proceedings give with the final vote, a majority of the 648 votes cast.",
    2: "The figure needed stored here, 325, is the one the proceedings give with the final vote, a majority of the 648 votes cast. The proceedings narrate the changes of vote that followed this roll call but print no further count by state until the final vote.",
    3: "This is the final vote after changes, which the proceedings tabulate by state; it is numbered 3 here because it is the third count by state the volume prints, and other accounts number the ballots differently. Of 650 votes, 648 were cast and 325 were needed; Alabama cast 16 of its 18.",
}
REPUBLICAN_VICE_SHORT = "Some delegations cast fewer votes than they held; the difference is not stored."
PROCEEDINGS: list[dict[str, Any]] = [
    {"tables": {1: "1856:republican:informal", 2: "1856:republican:formal"}, "title": "Proceedings of the First Three Republican National Conventions of 1856, 1860 and 1864", "publisher": "Charles W. Johnson", "ordinal": "First", "city": "Philadelphia", "days": (date(1856, 6, 17), date(1856, 6, 19)), "ballots": {1: date(1856, 6, 18), 2: date(1856, 6, 19)}, "deciding": 2, "vice": {"tables": {1: "1856:republican:vp"}, "ballots": {1: date(1856, 6, 19)}, "deciding": 1, "method": PROCEEDINGS_VICE, "caveat": REPUBLICAN_VICE_1856}},
    {"tables": {1: "1864:republican"}, "title": "Proceedings of the First Three Republican National Conventions of 1856, 1860 and 1864", "publisher": "Charles W. Johnson", "ordinal": "Third", "city": "Baltimore", "days": (date(1864, 6, 7), date(1864, 6, 8)), "ballots": {1: date(1864, 6, 8)}, "deciding": 1, "vice": {"tables": {1: "1864:republican:vp"}, "ballots": {1: date(1864, 6, 8)}, "deciding": 1, "method": PROCEEDINGS_VICE, "caveat": REPUBLICAN_VICE_1864}},
    {"tables": {1: "1872:republican"}, "ordinal": "Fifth", "city": "Philadelphia", "days": (date(1872, 6, 5), date(1872, 6, 6)), "ballots": {1: date(1872, 6, 6)}, "deciding": 1, "held": 1},
    {"tables": {1: "1868:republican"}, "ordinal": "Fourth", "city": "Chicago", "days": (date(1868, 5, 20), date(1868, 5, 21)), "ballots": {1: date(1868, 5, 21)}, "deciding": 1, "held": 1, "vice": {"tables": {n: f"1868:republican:vp-b{n}" for n in (1, 2, 3)}, "ballots": dict.fromkeys((1, 2, 3), date(1868, 5, 21)), "deciding": 3, "required": 325, "method": PROCEEDINGS_VICE, "caveat": REPUBLICAN_VICE_1868}},
    {"tables": {n: f"1860:republican:b{n}" for n in (1, 2, 3)}, "ordinal": "Second", "city": "Chicago", "days": (date(1860, 5, 16), date(1860, 5, 18)), "ballots": dict.fromkeys(range(1, 4), date(1860, 5, 18)), "deciding": 3, "vice": {"tables": {1: "1860:republican:vp-b1", 2: "1860:republican:vp-b2"}, "ballots": dict.fromkeys((1, 2), date(1860, 5, 18)), "deciding": 2, "method": PROCEEDINGS_VICE, "caveat": {2: REPUBLICAN_VICE_1860}}},
    {"tables": {n: f"1876:republican:b{n}" for n in range(1, 8)}, "ordinal": "Sixth", "city": "Cincinnati", "days": (date(1876, 6, 14), date(1876, 6, 16)), "ballots": dict.fromkeys(range(1, 8), date(1876, 6, 16)), "deciding": 7, "held": 7},
    {"tables": {n: f"1884:republican:b{n}" for n in range(1, 5)}, "ordinal": "Eighth", "city": "Chicago", "days": (date(1884, 6, 3), date(1884, 6, 6)), "ballots": dict.fromkeys(range(1, 5), date(1884, 6, 6)), "deciding": 4, "vice": {"tables": {1: "1884:republican:vp"}, "ballots": {1: date(1884, 6, 6)}, "deciding": 1, "required": 411, "method": PROCEEDINGS_VICE, "caveat": "Of 820 votes, 780 were cast and 411 were needed, the figures the proceedings give. " + REPUBLICAN_VICE_SHORT}},
    {"tables": {n: f"1880:republican:b{n}" for n in range(1, 37)}, "ordinal": "Seventh", "city": "Chicago", "days": (date(1880, 6, 2), date(1880, 6, 8)), "ballots": dict.fromkeys(range(1, 29), date(1880, 6, 7)) | dict.fromkeys(range(29, 37), date(1880, 6, 8)), "deciding": 36, "held": 36, "vice": {"tables": {1: "1880:republican:vp"}, "ballots": {1: date(1880, 6, 8)}, "deciding": 1, "required": 376, "method": PROCEEDINGS_VICE, "caveat": "Of 756 votes, 751 were cast and 376 were needed, the figures the proceedings give. " + REPUBLICAN_VICE_SHORT}},
    {"tables": {n: f"1888:republican:b{n}" for n in (1, 2, 3, 4, 5, 6, 7, 8)}, "ordinal": "Ninth", "city": "Chicago", "days": (date(1888, 6, 19), date(1888, 6, 25)), "ballots": {1: date(1888, 6, 22), 2: date(1888, 6, 22), 3: date(1888, 6, 22), 4: date(1888, 6, 23), 5: date(1888, 6, 23), 6: date(1888, 6, 25), 7: date(1888, 6, 25), 8: date(1888, 6, 25)}, "deciding": 8, "vice": {"tables": {1: "1888:republican:vp"}, "ballots": {1: date(1888, 6, 25)}, "deciding": 1, "required": 414, "method": PROCEEDINGS_VICE, "caveat": "Of 832 votes, 826 were cast and 414 were needed, the figures the proceedings give. " + REPUBLICAN_VICE_SHORT}},
    {"tables": {1: "1896:republican"}, "ordinal": "Eleventh", "city": "St. Louis", "days": (date(1896, 6, 16), date(1896, 6, 18)), "ballots": {1: date(1896, 6, 18)}, "deciding": 1, "caveat": "The table gives no votes held by each delegation, so none are stored. Its footnotes say the delegations of Colorado and Idaho had bolted the convention, Montana's other five votes were four blank and one for J. Donald Cameron, three of Nevada's delegates were absent, and one New York vote was passed; those votes are not in the table's columns and are not stored."},
    {"tables": {1: "1900:republican"}, "ordinal": "Twelfth", "city": "Philadelphia", "days": (date(1900, 6, 19), date(1900, 6, 21)), "ballots": {1: date(1900, 6, 21)}, "deciding": 1},
    {"tables": {1: "1892:republican"}, "ordinal": "Tenth", "city": "Minneapolis", "days": (date(1892, 6, 7), date(1892, 6, 10)), "ballots": {1: date(1892, 6, 10)}, "deciding": 1},
    {"tables": {1: "1904:republican"}, "ordinal": "Thirteenth", "city": "Chicago", "days": (date(1904, 6, 21), date(1904, 6, 23)), "ballots": {1: date(1904, 6, 23)}, "deciding": 1},
    {"tables": {1: "1908:republican"}, "ordinal": "Fourteenth", "city": "Chicago", "days": (date(1908, 6, 16), date(1908, 6, 19)), "ballots": {1: date(1908, 6, 19)}, "deciding": 1},
    {"tables": {1: "1912:republican"}, "ordinal": "Fifteenth", "city": "Chicago", "days": (date(1912, 6, 18), date(1912, 6, 22)), "ballots": {1: date(1912, 6, 22)}, "deciding": 1},
    {"tables": {n: f"1916:republican:b{n}" for n in (1, 2, 3)}, "ordinal": "Sixteenth", "city": "Chicago", "days": (date(1916, 6, 7), date(1916, 6, 10)), "ballots": dict.fromkeys(range(1, 4), date(1916, 6, 10)), "deciding": 3},
    {"tables": {n: f"1920:republican:b{n}" for n in range(1, 11)}, "ordinal": "Seventeenth", "city": "Chicago", "days": (date(1920, 6, 8), date(1920, 6, 12)), "ballots": dict.fromkeys(range(1, 5), date(1920, 6, 11)) | dict.fromkeys(range(5, 11), date(1920, 6, 12)), "deciding": 10, "caveat": "On the tenth ballot several delegations changed their votes after they were first called and before the result was announced; the tabulation stored here is the one the convention declared."},
    {"tables": {1: "1924:republican"}, "ordinal": "Eighteenth", "city": "Cleveland", "days": (date(1924, 6, 10), date(1924, 6, 12)), "ballots": {1: date(1924, 6, 12)}, "deciding": 1},
    {"tables": {1: "1928:republican"}, "ordinal": "Nineteenth", "city": "Kansas City", "days": (date(1928, 6, 12), date(1928, 6, 15)), "ballots": {1: date(1928, 6, 14)}, "deciding": 1},
    {"tables": {1: "1968:republican:final"}, "first": "1968:republican:first", "ordinal": "Twenty-Ninth", "city": "Miami Beach", "days": (date(1968, 8, 5), date(1968, 8, 8)), "ballots": {1: date(1968, 8, 7)}, "deciding": 1},
    {"tables": {1: "1972:republican"}, "ordinal": "Thirtieth", "city": "Miami Beach", "days": (date(1972, 8, 21), date(1972, 8, 23)), "ballots": {1: date(1972, 8, 22)}, "deciding": 1},
    {"tables": {1: "1976:republican"}, "ordinal": "Thirty-First", "city": "Kansas City", "days": (date(1976, 8, 16), date(1976, 8, 19)), "ballots": {1: date(1976, 8, 18)}, "deciding": 1},
    {"tables": {1: "1980:republican"}, "ordinal": "Thirty-Second", "city": "Detroit", "days": (date(1980, 7, 14), date(1980, 7, 17)), "ballots": {1: date(1980, 7, 16)}, "deciding": 1},
    {"tables": {1: "1984:republican"}, "ordinal": "Thirty-Third", "city": "Dallas", "days": (date(1984, 8, 20), date(1984, 8, 23)), "ballots": {1: date(1984, 8, 22)}, "deciding": 1},
    {"tables": {1: "1988:republican"}, "ordinal": "Thirty-Fourth", "city": "New Orleans", "days": (date(1988, 8, 15), date(1988, 8, 18)), "ballots": {1: date(1988, 8, 17)}, "deciding": 1, "caveat": "The proceedings print no tabulation. The roll is transcribed from the narrated call (pages 504 to 525): the Secretary announced each delegation's votes, the Assistant Secretary repeated each vote as cast, and nine delegations passed and voted on the second call. Hawaii's votes were not announced by the Secretary; its 20 are the figure the delegation gave when it cast them. The Chair announced 2,277 votes for George Bush, and the nomination was then made by acclamation."},
]  # fmt: skip
SHEET_METHOD = (
    "The roll call of the states on the presidential nomination, as tabulated in the Final Vote Tally compiled by "
    "the Democratic National Committee's Office of the Secretary, a one-page sheet read from its text layer and "
    "checked against the page image. The sheet gives each delegation's allocated votes, the votes it cast, and the "
    "votes for each candidate."
)
SHEET_CAVEAT = (
    "The sheet is the Secretary's compiled tally, not a transcript of the roll call as it was called. Democrats "
    "Abroad is stored as an unlocated delegation, apart from the states and territories, and its 11 votes include "
    "half votes. Where a delegation cast fewer votes than it was allocated, the difference is its abstentions."
)
DNC_2024_METHOD = (
    "The electronic roll call of the delegates on the presidential nomination, as the Democratic National Committee "
    "published it on August 5, 2024, when the voting window closed: one image, State by State Breakdown, read by eye "
    "and checked against its printed totals. It gives each jurisdiction's ballots cast for Kamala Harris and its "
    "total ballots cast."
)
DNC_2024_CAVEAT = (
    "The table gives no split of the ballots not cast for Harris, so the rest of each delegation's total ballots cast "
    "are stored as abstentions: they were present votes and other choices that the release does not itemize. The "
    "ballots cast add up to 4,619, of which 4,567 were for Harris, the figure the release announces. Democrats "
    "Abroad is stored as an unlocated delegation, apart from the states and territories. The convention's "
    "ceremonial roll call on August 6, 2024, in Chicago did not change the result."
)
DENVER_1908 = (
    "The roll call is the printed tally of the first ballot. Pennsylvania's 68 votes were polled: 49½ for Bryan, 9½ "
    "for Gray, and 3 for Johnson, with 6 not cast; Vermont's 8 included one not voting. The printed totals are "
    "888½ for William Jennings Bryan, 46 for John A. Johnson, and 59½ for George Gray, of 1,002 delegates, 8 not voting."
)
DNC = "Official Proceedings of the National Democratic Convention, Held "
NEWS_LICENSE = "All rights reserved: a news article cited for one fact, not redistributed"
BALTIMORE_1848 = (
    "The two-thirds rule was applied to the votes given: on the first and second ballots 251 votes were cast, and 168 "
    "were needed; on the third and fourth, 254, and 170. New York's 36 votes were not cast on any ballot, the table marking it as not voting, and Florida's "
    "3 were not cast on the first two ballots, the table printing declined voting and then excused. The table gives "
    "no column for votes held, so only votes for candidates are stored, and the figures for John C. Calhoun "
    "(South Carolina) and William J. Worth (Tennessee and Kentucky) on the first ballots, and for Worth on the "
    "second, are printed in the state names, not in columns."
)
FOURTH_1848 = (
    " The table's printed total for Lewis Cass, 179 votes, is the count before states changed their votes. After the "
    "nomination was declared, the delegations that had not voted for Cass changed to make it unanimous, and the "
    "proceedings then give the whole ballot as 254 votes, 242 for Cass, 8 for Levi Woodbury, and 4 for James "
    "Buchanan. The rows stored here are the 179 as printed in the table."
)
VICE_1848 = {
    1: "New York's delegation declined to vote. The proceedings give 252 votes and 168 needed to nominate.",
    2: "The table is the roll as first called, with New York not voting and Virginia's delegation withdrawn for consultation. Pennsylvania then changed its 26 votes to William O. Butler and Virginia announced its 17 for him, and the convention declared Butler unanimously nominated. The proceedings print no figure needed for this ballot; the one stored here, 158, is two-thirds of the 236 votes in the table.",
}
VICE_1852 = "The proceedings list each candidate's votes by state. South Carolina did not vote, and 286 votes were cast on the first ballot; the figure needed stored here, 192, is the one the proceedings give."
VICE_1856 = "Only the first ballot is stored. Maine's 8 votes and New Hampshire's 5 were changed to John C. Breckinridge before the vote was announced; the rows stored here give them to Breckinridge, as the printed totals do. The second ballot is narrated only, as unanimous for Breckinridge with 296 votes, with no count by state, so it is not stored. The needed figure stored here, 198, is two-thirds of the 296 votes."
VICE_1860 = "The ballot was taken at Baltimore after the withdrawn delegations had left. Pennsylvania cast one blank vote, so 199\u00bd votes were cast and 198\u00bd went to Benjamin Fitzpatrick. Fitzpatrick declined the nomination, and Herschel V. Johnson was named in his place."
VICE_1864 = "Only the first ballot is tabulated, of 226 votes with 151 needed. Massachusetts' row includes \u00bd vote blank, which is not stored. After withdrawals the second ballot was unanimous for George H. Pendleton; no count by state of it is printed, so it is not stored."
VICE_1872 = "The table gives 732 votes, 713 for B. Gratz Brown and 6 for John W. Stevenson (Delaware). Florida cast 2 votes blank, New Jersey 9, and West Virginia 2; the blank votes are not stored. The figure needed stored here, 488, is two-thirds of the 732 votes."
VICE_1876 = "Ohio cast 8 of its 44 votes blank; the blank votes are not stored. Thomas A. Hendricks received 730 of the 738 votes, and the figure needed stored here, 492, is two-thirds of the 738."
PROCEEDINGS += [
    {"party": "democratic", "tables": {n: f"1848:democratic:b{n}" for n in range(1, 5)}, "title": "The Proceedings of the Democratic National Convention, Held at Baltimore, May 22, 1848", "publisher": "Democratic National Convention", "city": "Baltimore", "days": (date(1848, 5, 22), date(1848, 5, 25)), "ballots": dict.fromkeys(range(1, 5), date(1848, 5, 25)), "deciding": 4, "required": {1: 168, 2: 168, 3: 170, 4: 170}, "caveat": {1: BALTIMORE_1848, 2: BALTIMORE_1848, 3: BALTIMORE_1848, 4: BALTIMORE_1848 + FOURTH_1848}, "vice": {"tables": {1: "1848:democratic:vp-b1", 2: "1848:democratic:vp-b2"}, "ballots": dict.fromkeys((1, 2), date(1848, 5, 25)), "deciding": 2, "shifted": True, "required": {1: 168, 2: 158}, "method": PROCEEDINGS_VICE, "caveat": VICE_1848}},
    {"party": "democratic", "tables": {1: "1908:democratic"}, "title": "Official Report of the Proceedings of the Democratic National Convention, Held in Denver, Colorado, July 7, 8, 9 and 10, 1908", "publisher": "Western Newspaper Union", "city": "Denver", "days": (date(1908, 7, 7), date(1908, 7, 10)), "ballots": {1: date(1908, 7, 10)}, "deciding": 1, "caveat": DENVER_1908},
    {"party": "democratic", "tables": {4: "1932:democratic:b4"}, "title": "The Evening Star (Washington, D.C.), July 2, 1932: Fourth Ballot", "publisher": "The Evening Star", "kind": "news_agency", "city": "Chicago", "days": (date(1932, 6, 27), date(1932, 7, 2)), "ballots": {4: date(1932, 7, 2)}, "deciding": 4, "required": 770, "caveat": EVENING_STAR_1932_CAVEAT},
    {"party": "democratic", "tables": {1: "1835:democratic"}, "title": "Niles' Weekly Register, May 30, 1835: Presidential Convention", "publisher": "Niles' Weekly Register", "kind": "news_agency", "city": "Baltimore", "days": (date(1835, 5, 20), date(1835, 5, 22)), "ballots": {1: date(1835, 5, 22)}, "deciding": 1, "caveat": NILES_1835, "vice": {"tables": {1: "1835:democratic:vp"}, "ballots": {1: date(1835, 5, 22)}, "deciding": 1, "required": 177, "method": VICE_METHOD.format("Niles' Weekly Register's reprint of the official statement"), "caveat": NILES_1835_VICE}},
    {"party": "democratic", "tables": {}, "vice": {"tables": {1: "1832:democratic:vp"}, "ballots": {1: date(1832, 5, 22)}, "deciding": 1, "required": 189, "method": VICE_METHOD.format("the convention's summary of its proceedings"), "caveat": BALTIMORE_1832}, "title": "Summary of the Proceedings of a Convention of Republican Delegates, from the Several States in the Union, for the Purpose of Nominating a Candidate for the Office of Vice-President of the United States; Held at Baltimore, in the State of Maryland, May, 1832", "publisher": "Packard and Van Benthuysen", "city": "Baltimore", "days": (date(1832, 5, 21), date(1832, 5, 23))},
    {"party": "democratic", "tables": {n: f"1844:democratic:b{n}" for n in range(1, 10)}, "title": "Niles' National Register, June 1, 1844: Democratic National Convention", "publisher": "Niles' National Register", "kind": "news_agency", "city": "Baltimore", "days": (date(1844, 5, 27), date(1844, 5, 30)), "ballots": dict.fromkeys(range(1, 8), date(1844, 5, 28)) | dict.fromkeys(range(8, 10), date(1844, 5, 29)), "deciding": 9, "caveat": NILES_1844},
    {"party": "democratic", "tables": {n: f"1852:democratic:b{n}" for n in range(1, 50)}, "title": "Proceedings of the Democratic National Convention, Held at Baltimore, June 1–5, 1852, for the Nomination of Candidates for President and Vice President of the United States", "publisher": "R. Armstrong", "city": "Baltimore", "days": (date(1852, 6, 1), date(1852, 6, 5)), "ballots": dict.fromkeys(range(1, 18), date(1852, 6, 3)) | dict.fromkeys(range(18, 34), date(1852, 6, 4)) | dict.fromkeys(range(34, 50), date(1852, 6, 5)), "deciding": 49, "required": 192, "caveat": BALTIMORE_1852, "vice": {"tables": {1: "1852:democratic:vp-b1", 2: "1852:democratic:vp-b2"}, "ballots": dict.fromkeys((1, 2), date(1852, 6, 5)), "deciding": 2, "required": 192, "method": PROCEEDINGS_VICE, "caveat": VICE_1852}},
    {"party": "democratic", "tables": {n: f"1856:democratic:b{n}" for n in range(1, 18)}, "title": DNC + "in Cincinnati, June 2–6, 1856", "publisher": "Enquirer Company Steam Printing Establishment", "city": "Cincinnati", "days": (date(1856, 6, 2), date(1856, 6, 6)), "ballots": dict.fromkeys(range(1, 15), date(1856, 6, 5)) | dict.fromkeys(range(15, 18), date(1856, 6, 6)), "deciding": 17, "held": 1, "vice": {"tables": {1: "1856:democratic:vp"}, "ballots": {1: date(1856, 6, 6)}, "deciding": 2, "required": 198, "method": PROCEEDINGS_VICE, "caveat": VICE_1856}},
    {"party": "democratic", "tables": {n: f"1860:democratic:b{n}" for n in range(1, 58)} | {58: "1860:democratic:baltimore-b1", 59: "1860:democratic:baltimore-b2"}, "title": "Official Proceedings of the Democratic National Convention, Held in 1860, at Charleston and Baltimore", "publisher": "Nevin's Print", "city": "Charleston", "days": (date(1860, 4, 23), date(1860, 6, 23)), "ballots": dict.fromkeys(range(1, 13), date(1860, 5, 1)) | dict.fromkeys(range(13, 58), date(1860, 5, 2)) | dict.fromkeys((58, 59), date(1860, 6, 23)), "deciding": 59, "cities": dict.fromkeys((58, 59), "Baltimore"), "required": dict.fromkeys(range(1, 58), 202) | {58: 127, 59: 130}, "caveat": dict.fromkeys(range(1, 58), CHARLESTON_RULING) | dict.fromkeys((58, 59), BALTIMORE_RULING), "vice": {"tables": {1: "1860:democratic:vp"}, "ballots": {1: date(1860, 6, 23)}, "cities": {1: "Baltimore"}, "deciding": 1, "required": 133, "method": PROCEEDINGS_VICE, "caveat": VICE_1860}},
    {"party": "democratic", "tables": {1: "1864:democratic"}, "title": "Official Proceedings of the Democratic National Convention, Held in 1864 at Chicago", "publisher": "Times Steam Book and Job Printing House", "city": "Chicago", "days": (date(1864, 8, 29), date(1864, 8, 31)), "ballots": {1: date(1864, 8, 31)}, "deciding": 1, "held": 1, "caveat": FIRST_1864, "vice": {"tables": {1: "1864:democratic:vp"}, "ballots": {1: date(1864, 8, 31)}, "deciding": 2, "required": 151, "method": PROCEEDINGS_VICE, "caveat": VICE_1864}},
    {"party": "democratic", "tables": {n: f"1868:democratic:b{n}" for n in range(1, 22)}, "title": DNC + "at New York, July 4–9, 1868", "publisher": "Rockwell & Rollins", "city": "New York", "days": (date(1868, 7, 4), date(1868, 7, 9)), "ballots": dict.fromkeys(range(1, 7), date(1868, 7, 7)) | dict.fromkeys(range(7, 19), date(1868, 7, 8)) | dict.fromkeys(range(19, 23), date(1868, 7, 9)), "deciding": 22},
    {"party": "democratic", "tables": {1: "1872:democratic"}, "title": DNC + "at Baltimore, July 9, 1872", "publisher": "Rockwell & Churchill", "city": "Baltimore", "days": (date(1872, 7, 9), date(1872, 7, 10)), "ballots": {1: date(1872, 7, 10)}, "deciding": 1, "caveat": "The scattering list prints 'Groesbeck 2' on its own line under 'West Virginia, blank 2', without a state; those 2 votes are not stored.", "vice": {"tables": {1: "1872:democratic:vp"}, "ballots": {1: date(1872, 7, 10)}, "deciding": 1, "required": 488, "method": PROCEEDINGS_VICE, "caveat": VICE_1872}},
    {"party": "democratic", "tables": {1: "1876:democratic:b1", 2: "1876:democratic:b2"}, "title": DNC + "in St. Louis, Mo., June 27th, 28th and 29th, 1876", "publisher": "Woodward, Tiernan & Hale", "city": "St. Louis", "days": (date(1876, 6, 27), date(1876, 6, 29)), "ballots": dict.fromkeys(range(1, 3), date(1876, 6, 28)), "deciding": 2, "held": 2, "vice": {"tables": {1: "1876:democratic:vp"}, "ballots": {1: date(1876, 6, 29)}, "deciding": 1, "required": 492, "method": PROCEEDINGS_VICE, "caveat": VICE_1876}},
    {"party": "democratic", "tables": {1: "1880:democratic:b1", 2: "1880:democratic:b2"}, "title": DNC + "in Cincinnati, O., June 22d, 23d, and 24th, 1880", "publisher": "Daily Journal Book and Job Rooms", "city": "Cincinnati", "days": (date(1880, 6, 22), date(1880, 6, 24)), "ballots": {1: date(1880, 6, 23), 2: date(1880, 6, 24)}, "deciding": 2, "held": 2},
    {"party": "democratic", "tables": {1: "1884:democratic:b1", 2: "1884:democratic:b2"}, "title": DNC + "in Chicago, Ill., July 8th, 9th, 10th and 11th, 1884", "publisher": "Nabu Press", "city": "Chicago", "days": (date(1884, 7, 8), date(1884, 7, 11)), "ballots": {1: date(1884, 7, 10), 2: date(1884, 7, 11)}, "deciding": 2, "shifted": True, "caveat": "Cleveland's count here is the second roll call as called, short of the two-thirds needed; delegations then changed their votes before the nomination was declared, and those changes are not stored in the table."},
    {"party": "democratic", "tables": {1: "1892:democratic"}, "title": DNC + "in Chicago, Ill., June 21st, 22nd and 23rd, 1892", "publisher": "Cameron, Amberg & Co.", "city": "Chicago", "days": (date(1892, 6, 21), date(1892, 6, 23)), "ballots": {1: date(1892, 6, 22)}, "deciding": 1},
    {"party": "democratic", "tables": {n: f"1896:democratic:b{n}" for n in range(1, 6)}, "title": "Official Proceedings of the Democratic National Convention Held in Chicago, Ill., July 7th, 8th, 9th, 10th and 11th, 1896", "publisher": "Wilson, Humphreys & Co.", "city": "Chicago", "days": (date(1896, 7, 7), date(1896, 7, 11)), "ballots": dict.fromkeys(range(1, 6), date(1896, 7, 10)), "deciding": 5},
    {"party": "democratic", "tables": {n: f"1912:democratic:b{n}" for n in range(1, 47) if n != 2}, "title": "Official Report of the Proceedings of the Democratic National Convention, Held in Baltimore, Maryland, June 25, 26, 27, 28, 29 and July 1 and 2, 1912", "publisher": "The Peterson Linotyping Co.", "city": "Baltimore", "days": (date(1912, 6, 25), date(1912, 7, 2)), "ballots": {1: date(1912, 6, 27)} | dict.fromkeys(range(2, 13), date(1912, 6, 28)) | dict.fromkeys(range(13, 27), date(1912, 6, 29)) | dict.fromkeys(range(27, 43), date(1912, 7, 1)) | dict.fromkeys(range(43, 47), date(1912, 7, 2)), "deciding": 46},
    {"party": "democratic", "tables": {1: "1900:democratic"}, "title": "Official Proceedings of the Democratic National Convention, Held in Kansas City, Mo., July 4th, 5th and 6th, 1900", "publisher": "McClellan Printing Co.", "city": "Kansas City", "days": (date(1900, 7, 4), date(1900, 7, 6)), "ballots": {1: date(1900, 7, 5)}, "deciding": 1},
    {"party": "democratic", "tables": {1: "1904:democratic"}, "title": "Official report of the proceedings of the Democratic National Convention held in St. Louis, Missouri, July 6, 7, 8, and 9, 1904", "publisher": "University of Kentucky Libraries, ExploreUK", "city": "St. Louis", "days": (date(1904, 7, 6), date(1904, 7, 9)), "ballots": {1: date(1904, 7, 9)}, "deciding": 1, "caveat": "Ohio's 46 votes for Parker stand under the unit rule: a poll of the delegation gave Parker 28, Hearst 6, McClellan 9, Cockrell 2, and Olney 1, and the chair let the announced vote stand. The table is the roll after the Idaho, Nevada, West Virginia, and Washington delegations changed their votes."},
    {"party": "democratic", "tables": {1: "1920:democratic:b1", 44: "1920:democratic:b44"}, "title": "Official report of the proceedings of the Democratic national convention, held in San Francisco, California, June 28, 29, 30, July 1, 2, 3, 5, and 6, 1920, resulting in the nomination of Hon. James M. Cox (of Ohio) for president and Hon. Franklin D. Roosevelt (of New York) for vice-president", "publisher": "Bookwalter-Ball Printing Co.", "city": "San Francisco", "days": (date(1920, 6, 28), date(1920, 7, 6)), "ballots": {1: date(1920, 7, 2), 44: date(1920, 7, 6)}, "deciding": 44, "acclamation": True},
    {"party": "democratic", "tables": {1: "1924:democratic:b1"}, "title": "Official report of the proceedings of the Democratic National Convention held in Madison Square Garden, New York City, June 24, 25, 26, 27, 28, 30, July 1, 2, 3, 4, 5, 7, 8, and 9, 1924, resulting in the nomination of John W. Davis (of West Virginia) for president and Charles W. Bryan (of Nebraska) for vice-president", "publisher": "Bookwalter-Ball-Greathouse Printing Co.", "city": "New York", "days": (date(1924, 6, 24), date(1924, 7, 9)), "ballots": {1: date(1924, 6, 30)}, "deciding": 103},
    {"party": "democratic", "tables": {1: "1940:democratic"}, "title": "Official Proceedings of the Democratic National Convention, Chicago, Illinois, July 15–18, 1940", "publisher": "Democratic National Committee", "city": "Chicago", "days": (date(1940, 7, 15), date(1940, 7, 18)), "ballots": {1: date(1940, 7, 17)}, "deciding": 1},
    {"party": "democratic", "tables": {1: "1944:democratic"}, "title": "Official Proceedings of the Democratic National Convention, Chicago, Illinois, July 19–21, 1944", "publisher": "Democratic National Committee", "city": "Chicago", "days": (date(1944, 7, 19), date(1944, 7, 21)), "ballots": {1: date(1944, 7, 20)}, "deciding": 1},
    {"party": "democratic", "tables": {1: "1948:democratic"}, "title": "Democracy at work, being the official report of the Democratic National Convention, Philadelphia, Pennsylvania, July 12 to July 14, inclusive, 1948, resulting in the nomination of Harry S. Truman of Missouri for President and Alben W. Barkley of Kentucky for Vice-President", "publisher": "Local Democratic Political Committee of Pennsylvania", "city": "Philadelphia", "days": (date(1948, 7, 12), date(1948, 7, 14)), "ballots": {1: date(1948, 7, 14)}, "deciding": 1},
    {"party": "democratic", "tables": {1: "2008:democratic"}, "title": "Roll Call Vote for President of the United States, 2008 Democratic National Convention: Final Vote Tally, as compiled by the Office of the Secretary", "publisher": "Democratic National Committee, Office of the Secretary", "city": "Denver", "days": (date(2008, 8, 25), date(2008, 8, 28)), "ballots": {1: date(2008, 8, 27)}, "deciding": 1, "method": SHEET_METHOD, "caveat": SHEET_CAVEAT},
    {"party": "democratic", "tables": {1: "2024:democratic"}, "title": "2024 Electronic Roll Call: State by State Breakdown", "publisher": "Democratic National Committee", "city": "Chicago", "days": (date(2024, 8, 19), date(2024, 8, 22)), "ballots": {1: date(2024, 8, 5)}, "umbrella": "2024-01-23:president:democratic", "deciding": 1, "required": None, "method": DNC_2024_METHOD, "caveat": DNC_2024_CAVEAT},
    {"party": "democratic", "tables": {1: "1956:democratic"}, "title": "Official report of the proceedings of the Democratic National Convention and committee, held at Chicago, Illinois, August 13 to August 17, 1956, resulting in the nomination of Adlai E. Stevenson of Illinois for President and Estes Kefauver of Tennessee for Vice President", "publisher": "Democratic National Committee", "city": "Chicago", "days": (date(1956, 8, 13), date(1956, 8, 17)), "ballots": {1: date(1956, 8, 16)}, "deciding": 1},
]  # fmt: skip
TWO_THIRDS = range(1832, 1936)
REPORTS = {
    "republican": "the official report of the convention's proceedings published by the Republican National Committee",
    "democratic": "the convention's official proceedings",
}
CANDIDATES = {
    "1856:republican:Lincoln": ("Abraham Lincoln", "Q91"),
    "1856:republican:King": ("John A. King", "Q443879"),
    "1856:republican:Ford": ("Thomas H. Ford", None),
    "1860:republican:Clay": ("Cassius M. Clay", None),
    "1860:republican:Reeder": ("Andrew H. Reeder", "Q505177"),
    "1860:republican:Hickman": ("John Hickman", None),
    "1860:republican:Hamlin": ("Hannibal Hamlin", "Q273546"),
    "1860:republican:Read": ("John M. Read", None),
    "1860:republican:Davis": ("Henry Winter Davis", "Q1027386"),
    "1860:republican:Houston": ("Sam Houston", "Q216547"),
    "1864:republican:Johnson": ("Andrew Johnson", "Q8612"),
    "1864:republican:Dickinson": ("Daniel S. Dickinson", "Q1162460"),
    "1864:republican:Hamlin": ("Hannibal Hamlin", "Q273546"),
    "1864:republican:Tod": ("David Tod", "Q880651"),
    "1868:republican:Wilson": ("Henry Wilson", "Q313302"),
    "1868:republican:Colfax": ("Schuyler Colfax", "Q310852"),
    "1868:republican:Fenton": ("Reuben E. Fenton", "Q443870"),
    "1868:republican:Hamlin": ("Hannibal Hamlin", "Q273546"),
    "1868:republican:Curtin": ("Andrew G. Curtin", "Q504966"),
    "1868:republican:Harlan": ("James Harlan", "Q376747"),
    "1868:republican:Pomeroy": ("Samuel C. Pomeroy", "Q6053012"),
    "1868:republican:Kelley": ("William D. Kelley", "Q3568533"),
    "1868:republican:Speed": ("James Speed", None),
    "1868:republican:Creswell": ("John A. J. Creswell", "Q1298209"),
    "1880:republican:Woodford": ("Stewart L. Woodford", "Q428605"),
    "1880:republican:Maynard": ("Horace Maynard", "Q132330"),
    "1880:republican:Settle": ("Thomas Settle", None),
    "1880:republican:Bruce": ("Blanche K. Bruce", "Q881412"),
    "1880:republican:Davis": ("Edmund J. Davis", "Q729594"),
    "1880:republican:Alcorn": ("James L. Alcorn", "Q886035"),
    "1888:republican:Morton": ("Levi P. Morton", "Q298997"),
    "1888:republican:Bradley": ("William O. Bradley", None),
    "1888:republican:Bruce": ("Blanche K. Bruce", "Q881412"),
    "1888:republican:Thomas": ("Walter S. Thomas", None),
    "2024:democratic:Harris": ("Kamala Harris", "Q10853588"),
    "2008:democratic:Clinton": ("Hillary Clinton", "Q6294"),
    "2008:democratic:Obama": ("Barack Obama", "Q76"),
    "Nixon": ("Richard M. Nixon", "Q9588"),
    "N. Rockefeller": ("Nelson A. Rockefeller", "Q11237"),
    "Reagan": ("Ronald Reagan", "Q9960"),
    "McCloskey": ("Paul N. McCloskey Jr.", "Q2073295"),
    "Ford": ("Gerald R. Ford", "Q9582"),
    "Richardson": ("Elliot L. Richardson", "Q719808"),
    "Anderson": ("John B. Anderson", "Q936548"),
    "Bush": ("George Bush", "Q23505"),
    "republican:Coolidge": ("Calvin Coolidge", "Q36023"),
    "republican:Wood": ("Leonard Wood", "Q887716"),
    "republican:Harding": ("Warren G. Harding", "Q35286"),
    "republican:Sproul": ("William C. Sproul", "Q537771"),
    "republican:Butler": ("Nicholas Murray Butler", "Q271517"),
    "republican:Poindexter": ("Miles Poindexter", "Q1934657"),
    "republican:Sutherland": ("Howard Sutherland", "Q473723"),
    "republican:Knox": ("Philander C. Knox", "Q707811"),
    "republican:Borah": ("William E. Borah", "Q895819"),
    "republican:Kellogg": ("Frank B. Kellogg", "Q193009"),
    "republican:W. L. Ward": ("William L. Ward", None),
    "republican:Ward": ("William L. Ward", "Q3539740"),
    "republican:Pritchard": ("Jeter C. Pritchard", "Q1688490"),
    "republican:Warren": ("Charles B. Warren", "Q5075344"),
    "republican:Lenroot": ("Irvine L. Lenroot", "Q1673264"),
    "republican:Hays": ("Will H. Hays", "Q2094258"),
    "republican:MacGregor": ("H. F. MacGregor", None),
    "republican:du Pont": ("T. Coleman du Pont", "Q2383282"),
    "republican:La Follette": ("Robert M. La Follette", "Q878682"),
    "republican:Johnson": ("Hiram W. Johnson", "Q143644"),
    "1904:republican:For Theodore Roosevelt": ("Theodore Roosevelt", "Q33866"),
    "republican:Roosevelt": ("Theodore Roosevelt", "Q33866"),
    "republican:Taft": ("William Howard Taft", "Q35648"),
    "republican:Cannon": ("Joseph G. Cannon", "Q984248"),
    "republican:Fairbanks": ("Charles W. Fairbanks", "Q270539"),
    "republican:Fairbank": ("Charles W. Fairbanks", "Q270539"),
    "republican:LaFollette": ("Robert M. La Follette", "Q878682"),
    "republican:Charles E. Hughes": ("Charles E. Hughes", "Q313252"),
    "republican:Cummins": ("Albert B. Cummins", "Q377161"),
    "republican:Root": ("Elihu Root", "Q220619"),
    "republican:Weeks": ("John W. Weeks", "Q1371272"),
    "republican:Burton": ("Theodore E. Burton", "Q252449"),
    "republican:Brumbaugh": ("Martin G. Brumbaugh", "Q658426"),
    "republican:Willis": ("Frank B. Willis", None),
    "republican:McCall": ("Samuel W. McCall", None),
    "republican:Wanamaker": ("John Wanamaker", None),
    "republican:Lodge": ("Henry Cabot Lodge", None),
    "republican:Hoover": ("Herbert Hoover", "Q35236"),
    "republican:Lowden": ("Frank O. Lowden", "Q584973"),
    "republican:Curtis": ("Charles Curtis", "Q271023"),
    "republican:Watson": ("James E. Watson", "Q6133417"),
    "republican:Norris": ("George W. Norris", "Q435947"),
    "republican:Goff": ("Guy D. Goff", "Q1557460"),
    "republican:Dawes": ("Charles G. Dawes", "Q184565"),
    "republican:Hughes": ("Charles E. Hughes", "Q313252"),
    "Armstrong": ("Anne Armstrong", None),
    "Grant": ("Ulysses S. Grant", "Q34836"),
    "Blaine": ("James G. Blaine", "Q448778"),
    "Morton": ("Oliver P. Morton", "Q880811"),
    "1896:Morton": ("Levi P. Morton", "Q298997"),
    "Bristow": ("Benjamin H. Bristow", None),
    "Conkling": ("Roscoe Conkling", "Q5611358"),
    "Hayes": ("Rutherford B. Hayes", "Q35686"),
    "Hartranft": ("John F. Hartranft", "Q373157"),
    "Jewell": ("Marshall Jewell", "Q880411"),
    "Wheeler": ("William A. Wheeler", "Q310835"),
    "Washburne": ("Elihu B. Washburne", "Q731492"),
    "Arthur": ("Chester A. Arthur", "Q35498"),
    "Edmunds": ("George F. Edmunds", "Q1507329"),
    "Logan": ("John A. Logan", "Q954410"),
    "J. Sherman": ("John Sherman", "Q445392"),
    "Sherman": ("John Sherman", "Q445392"),
    "W. T. Sherman": ("William T. Sherman", None),
    "Hawley": ("Joseph R. Hawley", "Q240188"),
    "Lincoln": ("Robert T. Lincoln", None),
    "1860:Lincoln": ("Abraham Lincoln", "Q91"),
    "1864:Lincoln": ("Abraham Lincoln", "Q91"),
    "Banks": ("Nathaniel P. Banks", "Q714106"),
    "1888:Grant": ("Frederick Dent Grant", None),
    "1888:Douglas": ("Frederick Douglass", None),
    "1860:Reed": ("Andrew H. Reeder", None),
    "Seward": ("William H. Seward", "Q297308"),
    "Cameron": ("Simon Cameron", "Q1348975"),
    "republican:Chase": ("Salmon P. Chase", "Q433051"),
    "Bates": ("Edward Bates", "Q1291670"),
    "McLean": ("John McLean", "Q1371498"),
    "Dayton": ("William L. Dayton", "Q331182"),
    "Collamer": ("Jacob Collamer", "Q924931"),
    "Wade": ("Benjamin Wade", "Q817729"),
    "Fremont": ("John C. Frémont", "Q169011"),
    "Sumner": ("Charles Sumner", "Q1066198"),
    "C.M. Clay": ("Cassius M. Clay", None),
    "C.Clay": ("Cassius M. Clay", None),
    "McCrary": ("George W. McCrary", "Q720251"),
    "E. J. Davis": ("Edmund J. Davis", "Q729594"),
    "Sheridan": ("Philip Sheridan", None),
    "Windom": ("William Windom", "Q1374474"),
    "Garfield": ("James A. Garfield", "Q34597"),
    "Alger": ("Russell A. Alger", "Q880655"),
    "Gresham": ("Walter Q. Gresham", "Q1402324"),
    "Depew": ("Chauncey M. Depew", "Q5088222"),
    "Harrison": ("Benjamin Harrison", "Q35678"),
    "Allison": ("William B. Allison", "Q5552354"),
    "Rusk": ("Jeremiah M. Rusk", "Q887084"),
    "Phelps": ("William Walter Phelps", "Q2580976"),
    "Ingalls": ("John J. Ingalls", "Q433158"),
    "McKinley": ("William McKinley", "Q35041"),
    "For William McKinley": ("William McKinley", "Q35041"),
    "Miller": ("Warner Miller", "Q2549479"),
    "Foraker": ("Joseph B. Foraker", "Q881341"),
    "Haymond": ("Creed Haymond", None),
    "Reed": ("Thomas B. Reed", "Q2094078"),
    "Quay": ("Matthew S. Quay", "Q6059259"),
    "democratic:James Buchanan": ("James Buchanan", "Q12325"),
    "democratic:Franklin Pierce": ("Franklin Pierce", "Q12312"),
    "democratic:F. Pierce": ("Franklin Pierce", "Q12312"),
    "1832:democratic:Van Buren": ("Martin Van Buren", "Q11820"),
    "1832:democratic:Johnson": ("Richard Mentor Johnson", "Q109463"),
    "1832:democratic:Barbour": ("Philip P. Barbour", "Q726427"),
    "1835:democratic:Van Buren": ("Martin Van Buren", "Q11820"),
    "1835:democratic:Johnson": ("Richard Mentor Johnson", "Q109463"),
    "1835:democratic:Rives": ("William C. Rives", "Q3568477"),
    "1844:democratic:Van Buren": ("Martin Van Buren", "Q11820"),
    "1844:democratic:Cass": ("Lewis Cass", "Q503119"),
    "1844:democratic:Johnson": ("Richard Mentor Johnson", "Q109463"),
    "1844:democratic:Stewart": ("Charles Stewart", None),
    "1844:democratic:Woodbury": ("Levi Woodbury", "Q579228"),
    "1844:democratic:Buchanan": ("James Buchanan", "Q12325"),
    "1844:democratic:Calhoun": ("John C. Calhoun", "Q207191"),
    "1844:democratic:Polk": ("James K. Polk", "Q11891"),
    "1848:democratic:Cass": ("Lewis Cass", "Q503119"),
    "1848:democratic:Buchanan": ("James Buchanan", "Q12325"),
    "1848:democratic:Woodbury": ("Levi Woodbury", "Q579228"),
    "1848:democratic:Calhoun": ("John C. Calhoun", "Q207191"),
    "1848:democratic:Dallas": ("George M. Dallas", None),
    "1848:democratic:Worth": ("William J. Worth", None),
    "1848:democratic:Butler": ("William O. Butler", "Q693499"),
    "1848:democratic:Quitman": ("John A. Quitman", "Q441838"),
    "1848:democratic:Mason": ("John Y. Mason", "Q1258123"),
    "1848:democratic:King": ("William R. King", "Q298985"),
    "1848:democratic:Davis": ("Jefferson Davis", "Q162269"),
    "1848:democratic:McKay": ("James Iver McKay", "Q1358019"),
    "1852:democratic:Downs": ("Solomon W. Downs", "Q2299278"),
    "1852:democratic:Pillow": ("Gideon J. Pillow", "Q2603914"),
    "1852:democratic:Atchison": ("David R. Atchison", "Q1176322"),
    "1852:democratic:Strange": ("Robert Strange", "Q1728001"),
    "1852:democratic:Rusk": ("Thomas J. Rusk", "Q2290948"),
    "1852:democratic:Davis": ("Jefferson Davis", "Q162269"),
    "1852:democratic:Cobb": ("Howell Cobb", "Q878291"),
    "1856:democratic:Quitman": ("John A. Quitman", "Q441838"),
    "1856:democratic:Boyd": ("Linn Boyd", "Q1826895"),
    "1856:democratic:Fitzpatrick": ("Benjamin Fitzpatrick", "Q817483"),
    "1856:democratic:Brown": ("Aaron V. Brown", "Q302874"),
    "1856:democratic:Dobbin": ("James C. Dobbin", "Q373382"),
    "1856:democratic:Johnson": ("Herschel V. Johnson", "Q357353"),
    "1856:democratic:Breckinridge": ("John C. Breckinridge", "Q273212"),
    "1856:democratic:Bayard": ("James A. Bayard Jr.", "Q1665539"),
    "1856:democratic:Polk": ("Trusten Polk", "Q377459"),
    "1856:democratic:Butler": ("William O. Butler", "Q693499"),
    "1856:democratic:Rusk": ("Thomas J. Rusk", "Q2290948"),
    "1860:democratic:Fitzpatrick": ("Benjamin Fitzpatrick", "Q817483"),
    "1864:democratic:Voorhees": ("Daniel W. Voorhees", "Q5219007"),
    "1864:democratic:Cass": ("George W. Cass", "Q5545951"),
    "1864:democratic:Guthrie": ("James Guthrie", "Q1680480"),
    "1864:democratic:Dodge": ("Augustus C. Dodge", "Q770219"),
    "1864:democratic:Caton": ("John D. Caton", "Q6228117"),
    "1864:democratic:Powell": ("Lazarus W. Powell", "Q734742"),
    "1864:democratic:Phelps": ("John S. Phelps", "Q181083"),
    "1872:democratic:Brown": ("B. Gratz Brown", "Q376825"),
    "1872:democratic:Stevenson": ("John W. Stevenson", "Q359526"),
    "1852:democratic:Cass": ("Lewis Cass", "Q503119"),
    "1852:democratic:Buchanan": ("James Buchanan", "Q12325"),
    "1852:democratic:Marcy": ("William L. Marcy", "Q389258"),
    "1852:democratic:Douglas": ("Stephen A. Douglas", "Q433174"),
    "1852:democratic:Lane": ("Joseph Lane", "Q372848"),
    "1852:democratic:Houston": ("Sam Houston", "Q216547"),
    "1852:democratic:Weller": ("John B. Weller", "Q277673"),
    "1852:democratic:Dodge": ("Henry Dodge", "Q881703"),
    "1852:democratic:Butler": ("William O. Butler", "Q693499"),
    "1852:democratic:Dickinson": ("Daniel S. Dickinson", "Q1162460"),
    "1852:democratic:Pierce": ("Franklin Pierce", "Q12312"),
    "1852:democratic:King": ("William R. King", "Q298985"),
    "1852:democratic:Boyd": ("Linn Boyd", "Q1826895"),
    "1852:democratic:Ingersoll": ("R. J. Ingersoll", "Q1613625"),
    "1860:democratic:Douglas": ("Stephen A. Douglas", "Q433174"),
    "1860:democratic:Guthrie": ("James Guthrie", "Q1680480"),
    "1860:democratic:Hunter": ("Robert M. T. Hunter", "Q1355895"),
    "1860:democratic:Johnson": ("Andrew Johnson", "Q8612"),
    "1860:democratic:Lane": ("Joseph Lane", "Q372848"),
    "1860:democratic:Dickinson": ("Daniel S. Dickinson", "Q1162460"),
    "1860:democratic:Davis": ("Jefferson Davis", "Q162269"),
    "1860:democratic:Toucey": ("Isaac Toucey", "Q881600"),
    "1860:democratic:Pearce": ("James Pearce", "Q368739"),
    "1860:democratic:Stephen A. Douglas": ("Stephen A. Douglas", "Q433174"),
    "1860:democratic:James Guthrie": ("James Guthrie", "Q1680480"),
    "1860:democratic:Daniel S. Dickinson": ("Daniel S. Dickinson", "Q1162460"),
    "1860:democratic:Horatio Seymour": ("Horatio Seymour", "Q887320"),
    "1860:democratic:John C. Breckinridge": ("John C. Breckinridge", "Q273212"),
    "1860:democratic:Henry A. Wise": ("Henry A. Wise", "Q878516"),
    "1860:democratic:Thomas S. Bocock": ("Thomas S. Bocock", "Q1734209"),
    "democratic:Stephen A. Douglas": ("Stephen A. Douglas", "Q433174"),
    "democratic:Lewis Cass": ("Lewis Cass", "Q503119"),
    "democratic:George B. McClellan": ("George B. McClellan", "Q310192"),
    "democratic:McClellan": ("George B. McClellan", "Q310192"),
    "democratic:Thomas H. Seymour": ("Thomas H. Seymour", "Q885448"),
    "democratic:T. H. Seymour": ("Thomas H. Seymour", "Q885448"),
    "democratic:Seymour": ("Horatio Seymour", "Q887320"),
    "democratic:Pendleton": ("George H. Pendleton", "Q552459"),
    "democratic:A. Johnson": ("Andrew Johnson", "Q8612"),
    "democratic:R. Johnson": ("Reverdy Johnson", "Q1373189"),
    "democratic:Hancock": ("Winfield S. Hancock", "Q435517"),
    "democratic:Church": ("Sanford E. Church", "Q2221573"),
    "democratic:English": ("James E. English", "Q885957"),
    "democratic:Packer": ("Asa Packer", "Q4803147"),
    "democratic:Parker": ("Joel Parker", "Q886702"),
    "democratic:Doolittle": ("James R. Doolittle", "Q1681056"),
    "democratic:Blair": ("Francis P. Blair Jr.", "Q1350196"),
    "democratic:Hendricks": ("Thomas A. Hendricks", "Q310843"),
    "democratic:Field": ("Stephen J. Field", None),
    "democratic:Adams": ("Charles Francis Adams", "Q75174"),
    "democratic:Chase": ("Salmon P. Chase", "Q433051"),
    "democratic:Hoffman": ("John T. Hoffman", "Q880736"),
    "democratic:T. Ewing, Jr": ("Thomas Ewing Jr.", "Q3525101"),
    "democratic:Tilden": ("Samuel J. Tilden", "Q281566"),
    "democratic:Allen": ("William Allen", "Q880659"),
    "democratic:Ewing": ("Thomas Ewing Jr.", "Q3525101"),
    "democratic:Jewett": ("Hugh J. Jewett", "Q5883292"),
    "democratic:Lothrop": ("George V. N. Lothrop", "Q5545421"),
    "democratic:Loveland": ("William A. H. Loveland", "Q8003964"),
    "democratic:Payne": ("Henry B. Payne", "Q1606477"),
    "democratic:Pennoyer": ("Sylvester Pennoyer", "Q1591582"),
    "democratic:Teller": ("Henry M. Teller", "Q1575475"),
    "democratic:Tillman": ("Benjamin Tillman", "Q736903"),
    "republican:Fitler": ("Edwin H. Fitler", "Q5346510"),
    "democratic:Horace Greeley": ("Horace Greeley", "Q436790"),
    "democratic:Black": ("Jeremiah S. Black", "Q742459"),
    "democratic:Bayard": ("Thomas F. Bayard", "Q708034"),
    "democratic:Thurman": ("Allen G. Thurman", "Q1850376"),
    "democratic:Broadhead": ("James O. Broadhead", "Q1680065"),
    "democratic:Cleveland": ("Grover Cleveland", "Q35171"),
    "democratic:Randall": ("Samuel J. Randall", "Q942678"),
    "democratic:McDonald": ("Joseph E. McDonald", "Q436858"),
    "democratic:Carlisle": ("John G. Carlisle", "Q1379029"),
    "democratic:Flower": ("Roswell P. Flower", "Q881163"),
    "democratic:Hoadly": ("George Hoadly", "Q373780"),
    "democratic:Hill": ("David B. Hill", "Q888410"),
    "democratic:Boies": ("Horace Boies", "Q889115"),
    "democratic:Gorman": ("Arthur P. Gorman", None),
    "democratic:Stevenson": ("Adlai E. Stevenson", "Q310839"),
    "1956:democratic:Stevenson": ("Adlai E. Stevenson II", "Q311719"),
    "democratic:Morrison": ("William R. Morrison", "Q6799712"),
    "democratic:Campbell": ("James E. Campbell", "Q881053"),
    "democratic:Russell": ("William E. Russell", "Q680460"),
    "democratic:Whitney": ("William C. Whitney", None),
    "democratic:Pattison": ("Robert E. Pattison", "Q883224"),
    "democratic:Bryan": ("William Jennings Bryan", "Q205282"),
    "democratic:Bland": ("Richard P. Bland", "Q531828"),
    "democratic:Matthews": ("Claude Matthews", "Q1096925"),
    "democratic:Blackburn": ("Joseph C. S. Blackburn", None),
    "democratic:McLean": ("John R. McLean", None),
    "democratic:Turpie": ("David Turpie", "Q260534"),
    "democratic:Clark": ("Champ Clark", "Q465651"),
    "democratic:Wilson": ("Woodrow Wilson", "Q34296"),
    "democratic:Harmon": ("Judson Harmon", "Q617264"),
    "democratic:Underwood": ("Oscar W. Underwood", "Q454609"),
    "democratic:Marshall": ("Thomas R. Marshall", "Q299001"),
    "democratic:Marshal": ("Thomas R. Marshall", "Q299001"),
    "democratic:Baldwin": ("Simeon E. Baldwin", "Q886146"),
    "democratic:Sulzer": ("William Sulzer", "Q881099"),
    "democratic:Kern": ("John W. Kern", "Q454957"),
    "democratic:Kern.*": ("John W. Kern", "Q454957"),
    "democratic:Foss": ("Eugene N. Foss", None),
    "democratic:Gaynor": ("William J. Gaynor", None),
    "democratic:J. H. Lewis": ("J. Hamilton Lewis", "Q462565"),
    "democratic:James": ("Ollie M. James", "Q381568"),
    "democratic:Harriman": ("W. Averell Harriman", "Q345531"),
    "1956:democratic:Johnson": ("Lyndon B. Johnson", "Q9640"),
    "democratic:Symington": ("Stuart Symington", "Q962660"),
    "democratic:Chandler": ("A. B. Chandler", "Q573029"),
    "1956:democratic:Davis": ("James C. Davis", "Q1680229"),
    "democratic:Battle": ("John S. Battle", "Q684712"),
    "democratic:Timmerman": ("George Bell Timmerman Jr.", "Q362853"),
    "democratic:Lausche": ("Frank J. Lausche", "Q888606"),
    "democratic:McCormack": ("John W. McCormack", "Q377602"),
    "1920:democratic:McAdoo": ("William G. McAdoo", "Q741058"),
    "1920:democratic:Cox": ("James M. Cox", "Q440117"),
    "1920:democratic:Palmer": ("A. Mitchell Palmer", "Q1354363"),
    "1920:democratic:Cummings": ("Homer S. Cummings", None),
    "1920:democratic:Gerard": ("James W. Gerard", None),
    "1920:democratic:Owen": ("Robert L. Owen", "Q456154"),
    "1920:democratic:Hitchcock": ("Gilbert M. Hitchcock", "Q1487231"),
    "1920:democratic:Meredith": ("Edwin T. Meredith", None),
    "1920:democratic:Smith": ("Alfred E. Smith", "Q430714"),
    "1920:democratic:Edwards": ("Edward I. Edwards", "Q436902"),
    "1920:democratic:Davis": ("John W. Davis", "Q1332779"),
    "1920:democratic:Glass": ("Carter Glass", "Q1045879"),
    "1920:democratic:Simmons": ("Furnifold M. Simmons", "Q1475074"),
    "1920:democratic:Harrison": ("Francis Burton Harrison", "Q612408"),
    "1920:democratic:Williams": ("John Sharp Williams", "Q1701734"),
    "1920:democratic:Wood": ("Leonard Wood", None),
    "1920:democratic:Hearst": ("William Randolph Hearst", "Q294931"),
    "1920:democratic:Colby": ("Bainbridge Colby", None),
    "1920:democratic:Daniels": ("Josephus Daniels", None),
    "1924:democratic:McAdoo": ("William G. McAdoo", "Q741058"),
    "1924:democratic:Smith": ("Alfred E. Smith", "Q430714"),
    "1924:democratic:Underwood": ("Oscar W. Underwood", "Q454609"),
    "1924:democratic:Robinson": ("Joseph T. Robinson", "Q888196"),
    "1924:democratic:Saulsbury": ("Willard Saulsbury Jr.", "Q1394349"),
    "1924:democratic:Houston": ("David F. Houston", "Q1174390"),
    "1924:democratic:Ralston": ("Samuel M. Ralston", "Q884561"),
    "1924:democratic:Davis (Kansas)": ("Jonathan M. Davis", "Q929317"),
    "1924:democratic:Ritchie": ("Albert C. Ritchie", "Q887807"),
    "1924:democratic:Ferris": ("Woodbridge N. Ferris", "Q880349"),
    "1924:democratic:Cox": ("James M. Cox", "Q440117"),
    "1924:democratic:Bryan": ("Charles W. Bryan", "Q887229"),
    "1924:democratic:Brown": ("Brown", None),
    "1924:democratic:Silzer": ("George S. Silzer", "Q884298"),
    "1924:democratic:Glass": ("Carter Glass", "Q1045879"),
    "1924:democratic:Davis (W. Va.)": ("John W. Davis", "Q1332779"),
    "1924:democratic:Sweet": ("William E. Sweet", "Q884020"),
    "1924:democratic:Harrison": ("Pat Harrison", "Q369437"),
    "1924:democratic:Thompson": ("Thompson", None),
    "1924:democratic:Kendrick": ("John B. Kendrick", "Q437232"),
    "1932:democratic:Smith": ("Alfred E. Smith", "Q430714"),
    "1932:democratic:Ritchie": ("Albert C. Ritchie", None),
    "1932:democratic:Baker": ("Newton D. Baker", None),
    "1932:democratic:White": ("George White", None),
    "1932:democratic:Cox": ("James M. Cox", "Q440117"),
    "democratic:Roosevelt": ("Franklin D. Roosevelt", "Q8007"),
    "democratic:Farley": ("James A. Farley", "Q954859"),
    "democratic:Tydings": ("Millard Tydings", "Q354099"),
    "democratic:Garner": ("John Nance Garner", "Q298991"),
    "democratic:Hull": ("Cordell Hull", "Q202979"),
    "democratic:Byrd": ("Harry F. Byrd", "Q775382"),
    "1904:Parker": ("Alton B. Parker", "Q443866"),
    "1904:Hearst": ("William Randolph Hearst", "Q294931"),
    "1904:Gray": ("George Gray", "Q249041"),
    "1908:democratic:Gray": ("George Gray", "Q249041"),
    "1908:democratic:Johnson": ("John A. Johnson", None),
    "1904:Miles": ("Nelson A. Miles", "Q549005"),
    "1904:Cockrell": ("Francis M. Cockrell", "Q1259413"),
    "1904:Olney": ("Richard Olney", None),
    "1904:Wall": ("Edward C. Wall", None),
    "1904:McClellan": ("George B. McClellan Jr.", "Q5536568"),
    "1904:Towne": ("Charles A. Towne", "Q1063591"),
    "1904:Pattison": ("Robert E. Pattison", "Q883224"),
    "1904:Williams": ("John Sharp Williams", "Q1701734"),
    "1904:Coler": ("Bird S. Coler", None),
    "1948:democratic:Truman": ("Harry S. Truman", "Q11613"),
    "1948:democratic:Russell": ("Richard B. Russell Jr.", "Q718415"),
    "1948:democratic:McNutt": ("Paul V. McNutt", "Q887892"),
}
UNPLACED = {"DT": "Dakota Territory", "IT": "Indian Territory", "PH": "Philippine Islands", "CZ": "Canal Zone"}
ABSTAINED = {
    "votes",
    "Present and Not Voting",
    "Abstention",
    "not voting",
    "Not voting",
    "Not Voting",
    "Not Voting or Absent",
    "Absent",
}
OFFICIAL = (
    "The roll call of the states on the presidential nomination, as tabulated in {}, read from the scanned volume "
    "page by page."
)
HELD_TOTALS = (
    " Each delegation's total is the votes it held, abstentions included; abstentions are that total less the "
    "votes recorded for candidates."
)
HELD = (
    " The table gives no votes held by each delegation; each delegation's total here is the votes it cast on {}, "
    "on which every delegate voted."
)
FIRST_1968 = (
    "The proceedings print two tabulations of the one ballot. As first called, the roll gave {}. Delegations then "
    "changed their votes before the chair announced the result, and the final tabulation, stored here, is the one "
    "the convention declared."
)
READS: dict[str, Literal["low", "medium"]] = {"a": "low", "b": "medium"}
QUADRANTS = {
    "tl": (0.0, 0.04, 0.5, 0.525),
    "tr": (0.5, 0.04, 1.0, 0.525),
    "bl": (0.0, 0.515, 0.5, 0.98),
    "br": (0.5, 0.515, 1.0, 0.98),
    "top": (0.0, 0.04, 1.0, 0.47),
}
PROMPT = "1"
BALLOT = (
    " Read only the tabulation of the {} ballot for the presidential nomination (where the convention took a "
    "single ballot, the roll call that nominated the candidate for president). The page may hold only the beginning "
    "or the end of that tabulation, or none of it; rows at the top of the page that continue a table from the "
    "previous page belong to the ballot before the first one captioned on this page. Leave out every other "
    "ballot's tabulation, any poll of a single delegation by name, and any vote of yeas and nays on a motion. If no "
    "part of that ballot's tabulation is on this page, return an empty header and no rows."
)
ROLL = (
    "This page is from the official report of the proceedings of a national party convention. Read the tabulation "
    "of the ballot for president: one row per state or territory, in the order printed, including any totals row "
    "(labeled as printed, such as 'Totals'). The header lists only the numeric columns, left to right (the column "
    "of votes or delegates each state holds, then each candidate, abstention, or other column, labels as printed); "
    "never the state column. Each row's values hold one string per header column, in the same order, empty where "
    "the cell is blank. Read the rows and columns that continue the table from a previous page in the same way. If "
    "the page holds no such table, return an empty header and no rows."
)
NARRATED = (
    "This page is from the official report of the proceedings of a national party convention, which narrates the "
    "roll call on the presidential nomination state by state: the chairman of each delegation announces how many "
    "votes it casts for each candidate. Read only those announcements, one row per state or territory in the order "
    "announced. The header lists the candidates named, left to right, by surname as printed, never the state "
    "column; each row's values hold one string per header column, in the same order, empty where the delegation "
    "cast no votes for that candidate. Write votes as digits, spelling out no numbers, and where a delegation "
    "changes its vote before the result is announced, record the changed vote. Where the delegation's votes held "
    "are stated, put them in a first column labeled 'Votes'. If the page holds no announcements, return an empty "
    "header and no rows."
)
SCATTER = (
    "This page is from the official report of the proceedings of a national party convention. After the "
    "tabulation of a ballot for president, a paragraph lists the votes that were not tabulated above, one entry "
    "per candidate, naming the states that cast them (for example 'COLEMAN DU PONT: 5 votes from Delaware, 1 vote "
    "from Maine'), and may list the delegations not voting. Read only the entries for candidates. Return one row "
    "per candidate entry, labeled with the candidate's surname as printed ('du Pont', 'La Follette'), with the "
    "vertical extent of that entry's lines on the page as y0 and y1. The header lists the states named in any "
    "entry, in the order first named, each with x0 0 and x1 1000. Each row's values hold one string per header "
    "column, in the same order: the votes that candidate had from that state as digits (a fraction such as 1/2 "
    "after any whole number), empty where the state cast none for that candidate. Where an entry gives the votes of "
    "a state without a number ('the 6 votes of Delaware'), use that number. Leave out totals and the "
    "delegations not voting. If the page holds no such paragraph, return an empty header and no rows."
)
BYCANDIDATE = (
    "This page is from the official report of the proceedings of a national party convention, which prints each "
    "ballot for president as one paragraph per candidate, naming the states that voted for that candidate and "
    "ending with the candidate's total (for example 'For Lewis Cass—Maine, 4; New Hampshire, 4; ...—96'). Read "
    "only the paragraphs of the {} ballot, under its caption. The page may hold only the beginning or the end of "
    "that ballot, or none of it; paragraphs at the top of the page that continue from the previous page belong to "
    "the ballot before the first one captioned on this page. Leave out every other ballot, any narration of how a "
    "delegation voted, and any announced result. Return one row per candidate paragraph, labeled with the "
    "candidate's surname as printed, without 'For', with the vertical extent of that paragraph's lines on the page "
    "as y0 and y1. The header lists the states named in any paragraph, in the order first named, each with x0 0 "
    "and x1 1000, then a last column labeled 'Total'. Each row's values hold one string per header column, in the "
    "same order: the votes that candidate had from that state as digits (a fraction such as 1/2 after any whole "
    "number), empty where the state cast none for that candidate, and under 'Total' the total printed at the end "
    "of the paragraph, empty where none is printed. If no part of that ballot is on this page, return an empty "
    "header and no rows."
)
ABBREVIATIONS = {
    "ala": "AL", "ariz": "AZ", "ark": "AR", "calif": "CA", "cal": "CA", "colo": "CO", "conn": "CT", "del": "DE",
    "dc": "DC", "fla": "FL", "ga": "GA", "ill": "IL", "ind": "IN", "kans": "KS", "kan": "KS", "ky": "KY", "la": "LA",
    "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN", "miss": "MS", "mo": "MO", "mont": "MT", "nebr": "NE",
    "neb": "NE", "nev": "NV", "nh": "NH", "nj": "NJ", "nmex": "NM", "nm": "NM", "ny": "NY", "nc": "NC", "nd": "ND",
    "ndak": "ND", "okla": "OK", "ore": "OR", "oreg": "OR", "pa": "PA", "pr": "PR", "ri": "RI", "sc": "SC",
    "sd": "SD", "sdak": "SD", "tenn": "TN", "tex": "TX", "vt": "VT", "va": "VA", "vi": "VI", "wash": "WA",
    "wva": "WV", "wis": "WI", "wisc": "WI", "wyo": "WY", "virginislands": "VI", "puertorico": "PR",
    "districtofcolumbia": "DC", "distofcolumbia": "DC", "portorico": "PR", "philippineislands": "PH",
    "philippines": "PH", "philippinies": "PH", "phillippines": "PH", "dakota": "DT", "indianterritory": "IT", "dalaware": "DE",
    "southsarolina": "SC", "canalzone": "CZ", "nocar": "NC", "nodak": "ND", "socar": "SC", "sodak": "SD",
    "nhampshire": "NH", "ncarolina": "NC", "ndakota": "ND", "scarolina": "SC", "sdakota": "SD", "wvirginia": "WV",
    "distofcol": "DC", "dofc": "DC", "virginis": "VI", "panamacanalzone": "CZ", "njersey": "NJ", "nmexico": "NM", "nyork": "NY", "risland": "RI",
}  # fmt: skip
COLUMNS = {
    "1916:republican:b1": (
        "votes",
        "Hughes",
        "Root",
        "Weeks",
        "Fairbanks",
        "Sherman",
        "Burton",
        "Cummins",
        "Roosevelt",
    ),
    "1920:republican:b5": ("votes", "Wood", "Lowden", None, "Johnson", "Harding", "Sproul"),
    "1900:democratic": ("votes", "Bryan"),
    "1940:democratic": ("votes", "Roosevelt", "Farley", "Tydings", "Garner", "Hull"),
    "1928:republican": (
        "votes",
        "Hoover",
        "Lowden",
        "Curtis",
        "Watson",
        "Norris",
        "Goff",
        "Coolidge",
        "Dawes",
        "Not voting",
    ),
}
LABELS = {
    "Washbarne": "Washburne",
    "Poinddexter": "Poindexter",
    "Elaine": "Blaine",
    "Mcclellan": "McClellan",
    "Macgregor": "MacGregor",
    "Du Pont": "du Pont",
    "La Follette": "La Follette",
    "T.H.Seymour": "T. H. Seymour",
    "Mcdonald": "McDonald",
    "Pennroyer": "Pennoyer",
    "Pennrover": "Pennoyer",
    "Owens": "Owen",
}
RELABEL = {("1920:republican:b5", 247, "OR", 2): "RI"}
CORRECTIONS = {
    "1968:republican:first": [
        (410, "OH", (0.17, 0.362, 0.8, 0.382), {"Stassen": 1, "Lindsay": 0}, "1 for Stassen, set under Lindsay in one read; the printed totals (Stassen 2, Lindsay 1) and the Minnesota row give Lindsay only Minnesota's 1"),
    ],
    "1852:democratic:b49": [
        (65, "LA", (0.2, 0.822, 0.59, 0.885), {"Cass": 0, "Douglas": 0, "Butler": 0, "Houston": 0}, "none; both reads set Ohio's scattering votes one column early, under Louisiana"),
        (65, "OH", (0.2, 0.822, 0.42, 0.838), {"Cass": 2}, "2, printed for Ohio"),
        (65, "OH", (0.2, 0.839, 0.44, 0.853), {"Douglas": 2}, "2, printed for Ohio"),
        (65, "OH", (0.2, 0.854, 0.43, 0.868), {"Butler": 1}, "1, printed for Ohio"),
        (65, "OH", (0.2, 0.870, 0.41, 0.885), {"Houston": 1}, "1, printed for Ohio"),
    ],
    "1904:democratic": [
        (259, "CO", (0.443, 0.226, 0.862, 0.238), {"Wall": 0, "Towne": 0, "McClellan": 1}, "1, set in the unlabeled column between Wall and Towne, which the printed totals (McClellan 3) and the roll as called give to McClellan"),
        (259, "MO", (0.443, 0.468, 0.862, 0.480), {"Cockrell": 36}, "36, all for Cockrell; one read drops a digit"),
        (259, "OR", (0.443, 0.602, 0.862, 0.614), {"Wall": 0, "McClellan": 1}, "1, set in the unlabeled column between Wall and Towne, which the printed totals (McClellan 3) and the roll as called give to McClellan"),
        (259, "WI", (0.443, 0.763, 0.862, 0.775), {"Olney": 0, "Wall": 26}, "26, set under Wall, whose printed total of 27 counts it"),
        (259, "OK", (0.443, 0.870, 0.862, 0.882), {"Wall": 0, "Towne": 0, "Olney": 1, "McClellan": 1}, "1 under Olney and 1 set in the unlabeled column between Wall and Towne, which the printed totals (McClellan 3) and the roll as called give to McClellan"),
    ],
    "1940:democratic": [
        (226, "NY", (0.180, 0.470, 0.950, 0.484), {"votes": 94, "Roosevelt": 64.5, "Farley": 25, "Hull": 1, "not voting": 3.5}, "94 held, 90½ recorded; the printed totals (1,095½ of 1,100) leave 4½ not voting"),
        (226, "WA", (0.180, 0.658, 0.950, 0.672), {"votes": 16, "Roosevelt": 15, "not voting": 1}, "16 held, 15 recorded; the printed totals (1,095½ of 1,100) leave 4½ not voting"),
        (226, "VA", (0.180, 0.644, 0.950, 0.658), {"votes": 22, "Roosevelt": 5 + 14 / 15, "Farley": 3.4, "Garner": 8, "Hull": 4 + 2 / 3}, "5¹⁴⁄₁₅, 3⅖, 8, and 4⅔, which the delegation of 22 and the printed Hull total of 5⅔ count; one read runs the Roosevelt figure into 51 4/15 and another sets Hull at 4⅘"),
    ],
    "1944:democratic": [
        (129, "AK", (0.160, 0.205, 0.760, 0.228), {"votes": 6, "Roosevelt": 6}, "its full 6 for Roosevelt, as the chair cast them once told he could vote the absent proxy; he first announced 5"),
        (130, "TX", (0.160, 0.295, 0.780, 0.365), {"votes": 48, "Roosevelt": 36, "Byrd": 12}, "36 for Roosevelt and 12 for Byrd, as the chair recorded it: 24 for Roosevelt from one delegation and 12 each from the regular delegation"),
    ],
    "1920:democratic:b1": [
        (293, "DE", (0.330, 0.507, 0.880, 0.527), {"votes": 6, "McAdoo": 4, "Marshall": 4, "Wood": 0}, "4, set between the Marshall and Wood columns; the printed totals (Marshall 37, Wood 4) count it for Marshall"),
        (293, "FL", (0.330, 0.531, 0.880, 0.551), {"votes": 12, "McAdoo": 1, "Palmer": 8, "Edwards": 1, "Davis": 1, "Marshall": 1, "Wood": 0}, "1, set between the Marshall and Wood columns; the printed totals (Marshall 37, Wood 4) count it for Marshall"),
        (293, "IL", (0.330, 0.602, 0.880, 0.622), {"votes": 58, "McAdoo": 9, "Cox": 9, "Palmer": 35, "Smith": 5, "Edwards": 0}, "5, set under Smith, whose printed total of 109 counts it"),
        (293, "IA", (0.330, 0.650, 0.880, 0.670), {"votes": 26, "Meredith": 26, "Smith": 0}, "26, set under Meredith, whose printed total of 27 counts it"),
        (293, "MD", (0.330, 0.768, 0.880, 0.788), {"votes": 16, "McAdoo": 5.5, "Cox": 5.5, "Edwards": 1, "Davis": 3, "Glass": 0.5, "Underwood": 0.5}, "5½, 5½, 1, 3, ½, and ½, which the delegation of 16 counts; one read drops the half signs"),
        (293, "MA", (0.330, 0.791, 0.880, 0.811), {"votes": 36, "McAdoo": 4, "Cox": 4, "Palmer": 17, "Owen": 2, "Smith": 7, "Wood": 1, "Hearst": 1, "Edwards": 0, "Bryan": 0}, "7, 1, and 1 under Smith, Wood, and Hearst, which the printed totals count"),
        (294, "NH", (0.333, 0.480, 0.880, 0.504), {"votes": 8, "McAdoo": 4, "Palmer": 1, "Wood": 3, "Marshall": 0}, "3, set between the Marshall and Wood columns; the printed totals (Marshall 37, Wood 4) count it for Wood"),
    ],
    "1920:democratic:b44": [
        (504, "CO", (0.573, 0.450, 0.900, 0.462), {"votes": 12, "McAdoo": 0, "Cox": 12}, "3 and 9 in the table; at the end of the roll call Colorado changed to 12 votes for Cox"),
        (504, "ME", (0.573, 0.631, 0.900, 0.643), {"votes": 12, "McAdoo": 5, "Cox": 5, "not voting": 2}, "12 held, 10 recorded; Cox was then declared the nominee by acclamation, and no totals were announced"),
        (504, "MI", (0.573, 0.677, 0.900, 0.689), {"votes": 30, "not voting": 30}, "30 held, none recorded; Cox was then declared the nominee by acclamation, and no totals were announced"),
        (504, "MN", (0.573, 0.692, 0.900, 0.704), {"votes": 24, "McAdoo": 15, "Cox": 8, "not voting": 1}, "24 held, 23 recorded; Cox was then declared the nominee by acclamation, and no totals were announced"),
        (504, "MO", (0.573, 0.722, 0.900, 0.734), {"votes": 36, "McAdoo": 17, "Cox": 18, "not voting": 1}, "36 held, 35 recorded; Cox was then declared the nominee by acclamation, and no totals were announced"),
        (505, "PA", (0.494, 0.316, 0.900, 0.328), {"votes": 76, "McAdoo": 4, "Cox": 68, "Palmer": 1, "Davis": 2, "not voting": 1}, "76 held, 75 recorded; Cox was then declared the nominee by acclamation, and no totals were announced"),
        (505, "SD", (0.494, 0.361, 0.900, 0.373), {"votes": 10, "McAdoo": 3, "Cox": 5, "Owen": 1, "not voting": 1}, "10 held, 9 recorded; Cox was then declared the nominee by acclamation, and no totals were announced"),
        (505, "TN", (0.56, 0.374, 0.61, 0.39), {"Cox": 0}, "a stray mark in the Cox cell; Tennessee's 24 are all for Davis"),
    ],
    "1924:democratic:b1": [
        (356, "CA", (0.327, 0.531, 0.880, 0.553), {"votes": 26, "McAdoo": 26, "Sweet": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "CO", (0.327, 0.555, 0.880, 0.577), {"votes": 12, "Sweet": 12, "Brown": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "CT", (0.327, 0.577, 0.880, 0.599), {"votes": 14, "Smith": 6, "Brown": 8}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "ID", (0.327, 0.669, 0.880, 0.691), {"votes": 8, "McAdoo": 8, "Harrison": 0, "Cox": 0, "Silzer": 0, "Ritchie": 0, "Davis (W. Va.)": 0, "Bryan": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "IL", (0.327, 0.691, 0.880, 0.713), {"votes": 58, "McAdoo": 12, "Smith": 15, "Ritchie": 3, "Cox": 10, "Bryan": 2, "Silzer": 10, "Davis (W. Va.)": 4, "Harrison": 2, "Ralston": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "IN", (0.327, 0.714, 0.880, 0.736), {"votes": 30, "Ralston": 30, "McAdoo": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "IA", (0.327, 0.737, 0.880, 0.759), {"votes": 26, "McAdoo": 26, "Davis (Kansas)": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "KS", (0.327, 0.760, 0.880, 0.782), {"votes": 20, "Davis (Kansas)": 20, "Harrison": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "KY", (0.327, 0.783, 0.880, 0.805), {"votes": 26, "McAdoo": 26, "Harrison": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "LA", (0.327, 0.805, 0.880, 0.827), {"votes": 20, "Harrison": 20}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "ME", (0.327, 0.828, 0.880, 0.850), {"votes": 12, "Underwood": 6.5, "McAdoo": 2, "Smith": 3.5, "Ritchie": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (356, "MD", (0.327, 0.850, 0.880, 0.872), {"votes": 16, "Ritchie": 16}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "MA", (0.343, 0.372, 0.880, 0.390), {"votes": 36, "McAdoo": 1.5, "Smith": 33, "Brown": 1, "Harrison": 0.5}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "MI", (0.343, 0.392, 0.880, 0.410), {"votes": 30, "Ferris": 30, "McAdoo": 0, "Smith": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "MN", (0.343, 0.413, 0.880, 0.431), {"votes": 24, "Underwood": 2, "Robinson": 2, "McAdoo": 5, "Smith": 10, "Ritchie": 2, "Cox": 1, "Bryan": 1, "Davis (W. Va.)": 1}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "MS", (0.343, 0.435, 0.880, 0.453), {"votes": 20, "Harrison": 20, "Cox": 0, "Smith": 0, "Robinson": 0, "Ritchie": 0, "Underwood": 0, "Ferris": 0, "Bryan": 0, "McAdoo": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "MO", (0.343, 0.458, 0.880, 0.476), {"votes": 36, "McAdoo": 36, "Cox": 0, "Ritchie": 0, "Davis (W. Va.)": 0, "Bryan": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "MT", (0.343, 0.479, 0.880, 0.497), {"votes": 8, "McAdoo": 7, "Smith": 1, "Harrison": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NE", (0.343, 0.501, 0.880, 0.519), {"votes": 16, "McAdoo": 1, "Bryan": 15, "Harrison": 0, "Smith": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NV", (0.343, 0.523, 0.880, 0.541), {"votes": 6, "McAdoo": 6, "Bryan": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NH", (0.343, 0.543, 0.880, 0.561), {"votes": 8, "Brown": 8, "Bryan": 0, "McAdoo": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NJ", (0.343, 0.566, 0.880, 0.584), {"votes": 28, "Silzer": 28}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NM", (0.343, 0.588, 0.880, 0.606), {"votes": 6, "McAdoo": 6, "Brown": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NY", (0.343, 0.609, 0.880, 0.627), {"votes": 90, "Smith": 90, "Silzer": 0, "McAdoo": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "NC", (0.343, 0.631, 0.880, 0.649), {"votes": 24, "McAdoo": 24, "Smith": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "ND", (0.343, 0.653, 0.880, 0.671), {"votes": 10, "McAdoo": 10}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "OH", (0.343, 0.675, 0.880, 0.693), {"votes": 48, "Cox": 48, "McAdoo": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "OK", (0.343, 0.696, 0.880, 0.714), {"votes": 20, "McAdoo": 20, "Cox": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "OR", (0.343, 0.717, 0.880, 0.735), {"votes": 10, "McAdoo": 10, "Cox": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (357, "PA", (0.343, 0.740, 0.880, 0.758), {"votes": 76, "Underwood": 3.5, "McAdoo": 25.5, "Smith": 35.5, "Saulsbury": 1, "Ritchie": 1.5, "Glass": 1, "Davis (W. Va.)": 4, "Harrison": 1, "Thompson": 1, "not voting": 2}, "76 held, 74 recorded across the askew rows; the printed totals count 1,096 of 1,098 votes"),
        (357, "RI", (0.343, 0.761, 0.880, 0.779), {"votes": 10, "Smith": 10, "Harrison": 0, "Thompson": 0, "Ritchie": 0, "Underwood": 0, "Glass": 0, "Davis (W. Va.)": 0, "Saulsbury": 0, "McAdoo": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "VT", (0.330, 0.539, 0.880, 0.561), {"votes": 8, "McAdoo": 1, "Smith": 7, "Glass": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "VA", (0.330, 0.561, 0.880, 0.583), {"votes": 24, "Glass": 24}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "WA", (0.330, 0.580, 0.880, 0.602), {"votes": 14, "McAdoo": 14, "Davis (W. Va.)": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "WV", (0.330, 0.604, 0.880, 0.626), {"votes": 16, "Davis (W. Va.)": 16}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "WI", (0.330, 0.627, 0.880, 0.649), {"votes": 26, "McAdoo": 3, "Smith": 23, "Kendrick": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "WY", (0.330, 0.650, 0.880, 0.672), {"votes": 6, "Kendrick": 6}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "DC", (0.330, 0.692, 0.880, 0.714), {"votes": 6, "McAdoo": 6, "Davis (W. Va.)": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "HI", (0.330, 0.714, 0.880, 0.736), {"votes": 6, "Underwood": 2, "Robinson": 1, "McAdoo": 1, "Smith": 1, "Davis (W. Va.)": 1}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "PH", (0.330, 0.740, 0.880, 0.762), {"votes": 6, "McAdoo": 3, "Smith": 3, "Davis (W. Va.)": 0}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
        (358, "PR", (0.330, 0.762, 0.880, 0.784), {"votes": 6, "Underwood": 1, "Davis (W. Va.)": 5}, "as printed; the table is set askew, so the row's figures sit beside a neighboring state, and the printed column totals confirm them"),
    ],
    "1948:democratic": [
        (324, "CA", (0.2, 0.105, 0.95, 0.155), {"votes": 54, "Truman": 54, "Russell": 0, "McNutt": 0}, "the clerk recorded 53½ and ½ not voting; California then announced 54 for Truman, and the chair corrected the vote before announcing the result"),
        (323, "FL", (0.1, 0.34, 0.86, 0.392), {"votes": 20, "Russell": 20, "McNutt": 0, "Truman": 0}, "Florida corrected its vote to 20 for Russell before the result was announced"),
        (323, "IN", (0.1, 0.628, 0.86, 0.656), {"votes": 26, "Truman": 26, "McNutt": 0, "Russell": 0}, "Indiana cast its 26 for Truman before the result was announced"),
        (319, "NH", (0.15, 0.51, 0.85, 0.565), {"votes": 12, "Truman": 11, "not voting": 1}, "the poll of the delegation: 11 for Truman and two half votes not voting"),
        (323, "NY", (0.1, 0.5, 0.86, 0.61), {"votes": 98, "Truman": 98, "Roe": 0, "Russell": 0}, "the delegate who received 15 votes withdrew, and the chair announced the entire vote of New York for Truman"),
        (323, "WV", (0.1, 0.41, 0.86, 0.447), {"votes": 20, "Truman": 20, "Russell": 0, "Barkley": 0, "McNutt": 0}, "West Virginia cast its entire 20 for Truman before the result was announced"),
        (315, "CT", (0.4, 0.108, 0.84, 0.17), {"votes": 20, "Truman": 20, "Russell": 0, "McNutt": 0}, "20 for Truman; one read records the entry twice"),
        (323, "VI", (0.1, 0.1, 0.86, 0.135), {"votes": 2, "Truman": 2}, "the Virgin Islands cast its two votes for Truman"),
    ],
    "1956:democratic": [
        (464, "NV", (0.78, 0.358, 0.83, 0.378), {"Symington": 0.5, "Timmerman": 0}, "the half vote is set under Symington, which the printed totals count"),
        (464, "NM", (0.78, 0.403, 0.83, 0.423), {"Symington": 0.5, "Timmerman": 0}, "the half vote is set under Symington, which the printed totals count"),
        (464, "NC", (0.78, 0.434, 0.83, 0.454), {"Symington": 0.5, "Timmerman": 0}, "the half vote is set under Symington, which the printed totals count"),
    ],
    "1928:republican": [
        (284, "MO", (0.21, 0.771, 0.58, 0.785), {"Hughes": 1}, "the footnote to the row: 'Missouri cast one vote for Charles E. Hughes'"),
    ],
    "1912:democratic:b4": [
        (213, "FL", (0.58, 0.244, 0.62, 0.258), {"Wilson": 0}, "a stray mark; Florida's 12 are all for Underwood"),
        (213, "ND", (0.735, 0.672, 0.785, 0.688), {"Wilson": 10, "Underwood": 0}, "10, printed in the Underwood column; the printed totals (Wilson 349½, Underwood 112) count it for Wilson"),
    ],
    "1912:democratic:b7": [
        (221, "MI", (0.808, 0.73, 0.845, 0.748), {"Marshall": 1, "Kern": 0}, "1, set between the Marshall and Kern columns; the printed totals and the announced result count it for Marshall"),
    ],
    "1912:democratic:b10": [
        (227, "CT", (0.728, 0.628, 0.778, 0.646), {"Wilson": 0, "Underwood": 7, "Marshall": 0}, "7 in the Underwood column, which the printed Underwood total of 117½ counts; the Wilson cell holds a pen mark"),
    ],
    "1912:democratic:b12": [
        (234, "TN", (0.455, 0.63, 0.6, 0.646), {"votes": 24, "Wilson": 7, "not voting": 2.5}, "24*, with the footnote '*Not voting, 2½'; the Wilson cell prints 7 beside a pen mark"),
        (234, "SD", (0.512, 0.612, 0.6, 0.627), {"Clark": 0, "Wilson": 10}, "10, printed in the Clark column; the printed totals (Clark 547½, Wilson 354) count it for Wilson"),
    ],
    "1912:democratic:b14": [
        (249, "OH", (0.75, 0.53, 0.79, 0.546), {"Harmon": 29}, "the Harmon cell is blank; Ohio's 48 less its 19 printed votes, and the printed Harmon total of 29"),
        (249, "VT", (0.633, 0.709, 0.673, 0.725), {"Underwood": 0}, "the delegation of 8 is filled by Clark 1 and Wilson 7, and the printed Underwood total of 111 leaves no room for it"),
    ],
    "1912:democratic:b16": [
        (254, "WI", (0.645, 0.799, 0.701, 0.816), {"Harmon": 0, "Bryan": 1}, "1, printed in the Harmon column; the printed totals (Harmon 29, Bryan 1) count it for Bryan"),
    ],
    "1912:democratic:b19": [
        (261, "LA", (0.85, 0.381, 0.9, 0.397), {"Foss": 1}, "the row records 19 of its 20 and leaves the Foss cell blank; the announced and printed Foss total of 1 has no other source"),
    ],
    "1912:democratic:b40": [
        (331, "AL", (0.496, 0.872, 0.708, 0.895), {"votes": 24, "Underwood": 24}, "24 and 24; one read returns nothing for this page"),
        (331, "AZ", (0.496, 0.895, 0.651, 0.911), {"votes": 6, "Clark": 4, "Wilson": 2}, "6, 4, and 2; one read returns nothing for this page"),
        (331, "AR", (0.496, 0.911, 0.592, 0.935), {"votes": 18, "Clark": 18}, "18 and 18; one read returns nothing for this page"),
        (332, "IN", (0.84, 0.353, 0.88, 0.369), {"Kern": 1, "Not voting": 0}, "1, printed in the not-voting column; the printed totals (Kern 1, not voting ½) count it for Kern"),
        (332, "UT", (0.49, 0.888, 0.7, 0.904), {"votes": 8, "Clark": 1.5, "Wilson": 6.5}, "8, 1½, and 6½; one read stops above the last three rows"),
        (332, "VT", (0.49, 0.905, 0.7, 0.921), {"votes": 8, "Wilson": 8}, "8 and 8; one read stops above the last three rows"),
        (332, "VA", (0.49, 0.924, 0.7, 0.94), {"votes": 24, "Clark": 12, "Wilson": 10, "Underwood": 2}, "24, 12, 10, and 2; one read stops above the last three rows"),
    ],
    "1912:democratic:b23": [
        (273, "VT", (0.754, 0.649, 0.777, 0.665), {"Bryan": 0, "Foss": 8}, "8, printed in the Bryan column; the printed totals (Bryan 1, Foss 45) count it for Foss"),
    ],
    "1912:democratic:b26": [
        (283, "MT", (0.397, 0.65, 0.901, 0.666), {'votes': 8, 'Clark': 3, 'Wilson': 5}, "as printed; one read stops at Missouri"),
        (283, "NE", (0.397, 0.668, 0.901, 0.684), {'votes': 16, 'Clark': 2, 'Wilson': 14}, "as printed; one read stops at Missouri"),
        (283, "NV", (0.397, 0.686, 0.901, 0.702), {'votes': 6, 'Clark': 6}, "as printed; one read stops at Missouri"),
        (283, "NH", (0.397, 0.704, 0.901, 0.72), {'votes': 8, 'Clark': 3, 'Wilson': 5}, "as printed; one read stops at Missouri"),
        (283, "NJ", (0.397, 0.722, 0.901, 0.738), {'votes': 28, 'Clark': 4, 'Wilson': 24}, "as printed; one read stops at Missouri"),
        (283, "NM", (0.397, 0.74, 0.901, 0.756), {'votes': 8, 'Clark': 8}, "as printed; one read stops at Missouri"),
        (283, "NY", (0.397, 0.758, 0.901, 0.774), {'votes': 90, 'Clark': 90}, "as printed; one read stops at Missouri"),
        (283, "NC", (0.397, 0.776, 0.901, 0.792), {'votes': 24, 'Wilson': 20, 'Underwood': 4}, "as printed; one read stops at Missouri"),
        (283, "ND", (0.397, 0.794, 0.901, 0.81), {'votes': 10, 'Wilson': 10}, "as printed; one read stops at Missouri"),
        (283, "OH", (0.397, 0.812, 0.901, 0.828), {'votes': 48, 'Wilson': 19, 'Harmon': 29}, "as printed; one read stops at Missouri"),
        (283, "OK", (0.397, 0.83, 0.901, 0.846), {'votes': 20, 'Clark': 10, 'Wilson': 10}, "as printed; one read stops at Missouri"),
        (283, "OR", (0.397, 0.848, 0.901, 0.864), {'votes': 10, 'Clark': 1, 'Wilson': 9}, "as printed; one read stops at Missouri"),
        (283, "PA", (0.397, 0.866, 0.901, 0.882), {'votes': 76, 'Clark': 5, 'Wilson': 71}, "as printed; one read stops at Missouri"),
        (283, "RI", (0.397, 0.884, 0.901, 0.9), {'votes': 10, 'Clark': 10}, "as printed; one read stops at Missouri"),
        (283, "SC", (0.397, 0.902, 0.901, 0.918), {'votes': 18, 'Wilson': 18}, "as printed; one read stops at Missouri"),
        (283, "SD", (0.397, 0.92, 0.901, 0.936), {'votes': 10, 'Wilson': 10}, "as printed; one read stops at Missouri"),
        (283, "TN", (0.397, 0.938, 0.901, 0.954), {'votes': 24, 'Clark': 12, 'Wilson': 10, 'Underwood': 2}, "as printed; one read stops at Missouri"),
    ],
    "1912:democratic:b33": [
        (315, "AL", (0.42, 0.483, 0.7, 0.499), {"votes": 24, "Underwood": 24}, "24 and 24; one read adds a cell to the row"),
        (315, "MD", (0.42, 0.784, 0.92, 0.8), {"votes": 16, "Clark": 10.5, "Wilson": 5, "Not Voting": 0.5}, "16, 10½, 5, and ½; one read adds a cell to the row"),
    ],
    "1912:democratic:b28": [
        (297, "MO", (0.56, 0.808, 0.6, 0.825), {"Wilson": 0}, "a stray mark; Missouri's 36 are all for Clark"),
        (298, "TX", (0.465, 0.423, 0.495, 0.435), {"Clark": 0, "Wilson": 40}, "40, printed in the Clark column; the printed totals (Clark 468½, Wilson 437½) count it for Wilson"),
    ],
    "1912:democratic:b31": [
        (311, "MD", (0.855, 0.59, 0.892, 0.606), {"Not voting": 0.5}, "½, which the delegation of 16 and the printed total of ½ count"),
    ],
    "1912:democratic:b37": [
        (324, "IN", (0.835, 0.366, 0.885, 0.382), {"Kern": 1, "Not voting": 0}, "1, printed in the not-voting column; the printed totals (Kern 1, not voting ½) count it for Kern"),
    ],
    "1912:democratic:b38": [
        (328, "NC", (0.53, 0.421, 0.695, 0.455), {"Clark": 0, "Wilson": 20, "Underwood": 4}, "20 and 4, printed one column to the left; the printed totals (Clark 425, Wilson 498½, Underwood 106) count them for Wilson and Underwood"),
    ],
    "1912:democratic:b42": [
        (336, "MA", (0.66, 0.685, 0.7, 0.701), {"Foss": 27, "J. H. Lewis": 0}, "27 in the Foss column, which the printed Foss total of 28 counts"),
    ],
    "1876:republican:b2": [
        (87, "SC", (0.573, 0.622, 0.618, 0.634), {"Bristow": 1, "Hayes": 0}, "the figure sits in the Bristow column, whose printed total counts it"),
        (87, "TX", (0.573, 0.634, 0.618, 0.647), {"Bristow": 1, "Hayes": 0}, "the figure sits in the Bristow column, whose printed total counts it"),
    ],
    "1876:republican:b5": [
        (103, "NJ", (0.615, 0.690, 0.662, 0.703), {"Hayes": 6, "Morton": 0}, "the figure sits in the Hayes column, whose printed total counts it"),
    ],
    "1876:republican:b4": [
        (101, "AL", (0.44, 0.744, 0.81, 0.755), {"Blaine": 16, "Bristow": 4}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "AR", (0.44, 0.757, 0.81, 0.768), {"Blaine": 1, "Morton": 11, "Wheeler": 0}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "CA", (0.44, 0.770, 0.81, 0.781), {"Blaine": 6, "Conkling": 3, "Hayes": 3}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "CO", (0.44, 0.783, 0.81, 0.794), {"Blaine": 6}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "CT", (0.44, 0.795, 0.81, 0.806), {"Blaine": 2, "Bristow": 9, "Hayes": 1}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "DE", (0.44, 0.808, 0.81, 0.819), {"Blaine": 6}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "FL", (0.44, 0.820, 0.81, 0.831), {"Blaine": 2, "Conkling": 2, "Hartranft": 4}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
        (101, "GA", (0.44, 0.833, 0.81, 0.845), {"Blaine": 9, "Bristow": 2, "Conkling": 6, "Morton": 4, "Washburne": 1}, "the lower read missed the head of the table below the third ballot; read by hand, and with the next page the columns sum to the printed totals"),
    ],
    "1884:republican:b1": [
        (150, "AL", (0.43, 0.635, 0.95, 0.647), {"not voting": 1}, "20 held, 19 recorded; 818 of 820 votes cast"),
        (150, "LA", (0.43, 0.795, 0.95, 0.807), {"not voting": 1}, "16 held, 15 recorded; 818 of 820 votes cast"),
    ],
    "1884:republican:b2": [
        (156, "LA", (0.43, 0.435, 0.95, 0.445), {"not voting": 1}, "16 held, 15 recorded; 819 of 820 votes cast"),
    ],
    "1884:republican:b4": [
        (171, "IL", (0.43, 0.545, 0.95, 0.557), {"not voting": 1}, "44 held, 43 recorded; 813 of 820 votes cast"),
        (171, "NC", (0.43, 0.767, 0.95, 0.779), {"not voting": 1}, "22 held, 21 recorded; 813 of 820 votes cast"),
        (171, "NY", (0.43, 0.755, 0.95, 0.767), {"not voting": 1}, "72 held, 71 recorded; 813 of 820 votes cast"),
        (171, "TN", (0.43, 0.842, 0.95, 0.854), {"not voting": 1}, "24 held, 23 recorded; 813 of 820 votes cast"),
        (171, "TX", (0.43, 0.855, 0.95, 0.867), {"not voting": 3}, "26 held, 23 recorded; 813 of 820 votes cast"),
    ],
    "1880:republican:b2": [
        (219, "IN", (0.59, 0.825, 0.65, 0.839), {"Blaine": 25}, "printed 25, which the Indiana row of 30 and the printed Blaine total count"),
    ],
    "1880:republican:b27": [
        (264, "NJ", (0.79, 0.848, 0.84, 0.863), {"Washburne": 2, "Windom": 0}, "the figure sits in the Washburne column"),
    ],
    "1880:republican:b31": [
        (280, "VT", (0.625, 0.21, 0.675, 0.224), {"Edmunds": 10, "Windom": 0}, "printed under the Windom heading, which this page swaps with Edmunds"),
    ],
    "1904:republican": [
        (212, "NC", (0.49, 0.171, 0.9, 0.183), {"votes": 24, "For Theodore Roosevelt": 24}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "ND", (0.49, 0.185, 0.9, 0.197), {"votes": 8, "For Theodore Roosevelt": 8}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "OH", (0.49, 0.199, 0.9, 0.210), {"votes": 46, "For Theodore Roosevelt": 46}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "OR", (0.49, 0.213, 0.9, 0.224), {"votes": 8, "For Theodore Roosevelt": 8}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "PA", (0.49, 0.226, 0.9, 0.238), {"votes": 68, "For Theodore Roosevelt": 68}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "RI", (0.49, 0.240, 0.9, 0.251), {"votes": 8, "For Theodore Roosevelt": 8}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "SC", (0.49, 0.254, 0.9, 0.265), {"votes": 18, "For Theodore Roosevelt": 18}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "SD", (0.49, 0.267, 0.9, 0.279), {"votes": 8, "For Theodore Roosevelt": 8}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "TN", (0.49, 0.281, 0.9, 0.293), {"votes": 24, "For Theodore Roosevelt": 24}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "TX", (0.49, 0.295, 0.9, 0.306), {"votes": 36, "For Theodore Roosevelt": 36}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "UT", (0.49, 0.309, 0.9, 0.320), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "VT", (0.49, 0.322, 0.9, 0.334), {"votes": 8, "For Theodore Roosevelt": 8}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "VA", (0.49, 0.336, 0.9, 0.347), {"votes": 24, "For Theodore Roosevelt": 24}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "WA", (0.49, 0.350, 0.9, 0.361), {"votes": 10, "For Theodore Roosevelt": 10}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "WV", (0.49, 0.363, 0.9, 0.375), {"votes": 14, "For Theodore Roosevelt": 14}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "WI", (0.49, 0.377, 0.9, 0.389), {"votes": 26, "For Theodore Roosevelt": 26}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "WY", (0.49, 0.391, 0.9, 0.402), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "DC", (0.49, 0.405, 0.9, 0.416), {"votes": 2, "For Theodore Roosevelt": 2}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "AK", (0.49, 0.418, 0.9, 0.430), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "AZ", (0.49, 0.432, 0.9, 0.443), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "IT", (0.49, 0.446, 0.9, 0.457), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "NM", (0.49, 0.459, 0.9, 0.471), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "OK", (0.49, 0.473, 0.9, 0.485), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "HI", (0.49, 0.487, 0.9, 0.498), {"votes": 6, "For Theodore Roosevelt": 6}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "PH", (0.49, 0.501, 0.9, 0.512), {"votes": 2, "For Theodore Roosevelt": 2}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
        (212, "PR", (0.49, 0.514, 0.9, 0.526), {"votes": 2, "For Theodore Roosevelt": 2}, "the right-hand column of the table, which the lower read leaves out; read by hand, and with the left-hand column the rows add to the printed 994"),
    ],
    "1908:republican": [
        (239, "SC", (0.4, 0.679, 0.95, 0.689), {"not voting": 1}, "18 held, 17 recorded; 979 of 980 votes cast"),
    ],
    "1888:republican:b4": [
        (395, "MS", (0.632, 0.521, 0.671, 0.531), {"Gresham": 3}, "a broken 3, which the Mississippi row of 18 and the printed Gresham total of 98 count"),
        (395, "AL", (0.51, 0.327, 0.93, 0.337), {"not voting": 1}, "20 held, 19 recorded; 828 of 832 votes cast"),
        (395, "GA", (0.51, 0.389, 0.93, 0.399), {"not voting": 1}, "24 held, 23 recorded; 828 of 832 votes cast"),
        (395, "MO", (0.51, 0.531, 0.93, 0.541), {"not voting": 1}, "32 held, 31 recorded; 828 of 832 votes cast"),
        (395, "NY", (0.51, 0.589, 0.93, 0.599), {"not voting": 1}, "72 held, 71 recorded; 828 of 832 votes cast"),
    ],
    "1888:republican:b6": [
        (407, "FL", (0.51, 0.464, 0.93, 0.475), {"not voting": 1}, "8 held, 7 recorded; 830 of 832 votes cast"),
        (407, "GA", (0.51, 0.475, 0.93, 0.485), {"not voting": 1}, "24 held, 23 recorded; 830 of 832 votes cast"),
    ],
    "1888:republican:b8": [
        (416, "GA", (0.51, 0.307, 0.93, 0.315), {"not voting": 1}, "24 held, 23 recorded; 830 of 832 votes cast"),
        (416, "TN", (0.51, 0.568, 0.93, 0.576), {"not voting": 1}, "24 held, 23 recorded; 830 of 832 votes cast"),
    ],
    "1892:republican": [
        (148, "NC", (0.627, 0.669, 0.70, 0.676), {"Blaine": 8 / 3}, "printed 2⅔, which the printed Blaine total of 182⅙ counts"),
    ],
    "1884:republican:b3": [
        (159, "IA", (0.43, 0.395, 0.66, 0.406), {"Blaine": 26, "Edmunds": 0}, "printed under Edmunds; the Blaine and Edmunds totals each differ from the rows by exactly these 26 votes"),
        (159, "RI", (0.49, 0.643, 0.54, 0.654), {"Arthur": 0}, "the printed Arthur vote exceeds both the delegation's 8 and the Arthur total"),
        (159, "LA", (0.43, 0.432, 0.48, 0.443), {"not voting": 1}, "16 held, 15 recorded; 819 of 820 votes cast"),
    ],
    "1888:republican:b1": [
        (376, "GA", (0.343, 0.712, 0.377, 0.723), {"not voting": 1}, "24 held, 23 recorded; 831 of 832 votes cast"),
        (376, "AL", (0.03, 0.640, 0.90, 0.651), {"Allison": 0, "Fitler": 0, "Gresham": 0, "Hawley": 0, "Ingalls": 0, "Phelps": 0, "Rusk": 0, "Blaine": 0, "Lincoln": 0, "McKinley": 0, "Depew": 1, "Harrison": 1, "Sherman": 12, "Alger": 6}, "row read from the page"),
        (376, "AR", (0.03, 0.655, 0.90, 0.666), {"Allison": 0, "Fitler": 0, "Gresham": 1, "Hawley": 0, "Ingalls": 10, "Phelps": 0, "Rusk": 0, "Blaine": 0, "Lincoln": 0, "McKinley": 0, "Depew": 0, "Harrison": 1, "Sherman": 2, "Alger": 0}, "row read from the page"),
        (376, "CA", (0.03, 0.661, 0.90, 0.672), {"Allison": 0, "Fitler": 0, "Gresham": 0, "Hawley": 0, "Ingalls": 0, "Phelps": 0, "Rusk": 0, "Blaine": 16, "Lincoln": 0, "McKinley": 0, "Depew": 0, "Harrison": 0, "Sherman": 0, "Alger": 0}, "row read from the page"),
    ],
    "1888:republican:b2": [
        (380, "GA", (0.423, 0.311, 0.459, 0.321), {"not voting": 1}, "24 held, 23 recorded; 830 of 832 votes cast"),
        (380, "TX", (0.423, 0.578, 0.459, 0.588), {"not voting": 1}, "26 held, 25 recorded; 830 of 832 votes cast"),
    ],
    "1888:republican:b3": [
        (384, "NJ", (0.42, 0.262, 0.90, 0.272), {"Alger": 0, "Allison": 4, "Depew": 1, "Gresham": 0, "Harrison": 4, "Phelps": 4, "Rusk": 0, "Sherman": 2, "Blaine": 0, "Lincoln": 0, "McKinley": 3, "Miller": 0}, "row read from the page"),
        (384, "NY", (0.42, 0.272, 0.90, 0.282), {"Depew": 71, "Gresham": 0, "Blaine": 1, "Lincoln": 0}, "row read from the page"),
        (384, "WV", (0.42, 0.382, 0.90, 0.392), {"Gresham": 2, "Harrison": 1, "Phelps": 0}, "row read from the page"),
        (383, "VA", (0.13, 0.292, 0.55, 0.306), {"Gresham": 2}, "left out of the table; the poll of Virginia printed above it and the Gresham total count them"),
        (383, "GA", (0.464, 0.786, 0.495, 0.796), {"not voting": 1}, "24 held, 23 recorded; 830 of 832 votes cast"),
        (384, "TX", (0.42, 0.354, 0.46, 0.364), {"not voting": 1}, "26 held, 25 recorded; 830 of 832 votes cast"),
    ],
    "1888:republican:b5": [
        (397, "GA", (0.641, 0.455, 0.677, 0.465), {"not voting": 1}, "24 held, 23 recorded; 827 of 832 votes cast"),
        (397, "MO", (0.641, 0.588, 0.677, 0.598), {"not voting": 1}, "32 held, 31 recorded; 827 of 832 votes cast"),
        (397, "NY", (0.641, 0.638, 0.677, 0.648), {"not voting": 1}, "72 held, 71 recorded; 827 of 832 votes cast"),
        (397, "TN", (0.641, 0.711, 0.677, 0.721), {"not voting": 2}, "24 held, 22 recorded; 827 of 832 votes cast"),
    ],
    "1888:republican:b7": [
        (410, "GA", (0.573, 0.309, 0.609, 0.319), {"Allison": 0}, "row read from the page"),
        (410, "GA", (0.509, 0.309, 0.545, 0.319), {"not voting": 1}, "24 held, 23 recorded; 831 of 832 votes cast"),
    ],
    "1916:republican:b1": [
        (212, "AL", (0.568, 0.175, 0.600, 0.186), {"Weeks": 3}, "a faint 3, which the Weeks total counts"),
        (213, "MO", (0.1, 0.330, 0.9, 0.362), {"Absent": 1}, "listed as absent below the tabulation"),
        (213, "TN", (0.1, 0.330, 0.9, 0.362), {"Absent": 0.5}, "listed as absent below the tabulation"),
        (213, "PA", (0.1, 0.330, 0.9, 0.362), {"Absent": 1}, "listed as absent below the tabulation"),
    ],
    "1916:republican:b3": [
        (235, "NH", (0.440, 0.183, 0.500, 0.197), {"Hughes": 8}, "8, all of the delegation for Hughes; a speck follows the figure"),
        (235, "VA", (0.520, 0.419, 0.580, 0.433), {"Roosevelt": 0}, "none for Roosevelt: the row's 15 votes are all for Hughes, and the printed Roosevelt total of 18½ leaves the 15 printed under his name uncounted"),
    ],
    "1916:republican:b2": [
        (216, "PA", (0.0, 0.276, 1.0, 0.288), {"Absent": 1}, "listed as absent below the tabulation"),
        (216, "CA", (0.0, 0.276, 1.0, 0.288), {"Absent": 1}, "listed as absent below the tabulation"),
    ],
    "1920:republican:b1": [
        (230, "MO", (0.09, 0.765, 0.37, 0.779), {"Not voting": 1}, "listed as not voting below the tabulation"),
    ],
    "1920:republican:b9": [
        (263, "OK", (0.08, 0.714, 0.83, 0.729), {"Absent": 0.5}, "listed as absent and not voting below the tabulation"),
        (263, "TX", (0.08, 0.714, 0.83, 0.729), {"Absent": 0.5}, "listed as absent and not voting below the tabulation"),
    ],
    "1920:republican:b10": [
        (270, "OK", (0.14, 0.212, 0.38, 0.227), {"Absent": 0.5}, "listed as absent below the tabulation"),
        (269, "IL", (0.56, 0.395, 0.88, 0.405), {"Harding": 38.2, "Johnson": 18.8}, "printed in fifths: 38 1/5 and 18 4/5"),
    ],
    "1920:republican:b3": [
        (235, "OK", (0.375, 0.621, 0.41, 0.631), {"votes": 20}, "printed 28; the row's votes and the delegates total of 984 count 20"),
    ],
    "1920:republican:b4": [
        (240, "AR", (0.38, 0.702, 0.41, 0.712), {"votes": 13}, "a broken 13; the row's votes and the delegates total of 984 count 13"),
    ],
    "1920:republican:b7": [
        (253, "PA", (0.455, 0.630, 0.49, 0.640), {"votes": 76}, "the row is left out of the table; the delegates total of 984 counts its 76, all listed under the scattering votes"),
    ],
    "1920:republican:b5": [
        (248, "WA", (0.0, 0.113, 1.0, 0.122), {"Poindexter": 14}, "14, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "NY", (0.0, 0.113, 1.0, 0.122), {"Poindexter": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "WI", (0.0, 0.123, 1.0, 0.133), {"La Follette": 24}, "24, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "WV", (0.0, 0.133, 1.0, 0.142), {"Sutherland": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "DE", (0.0, 0.144, 1.0, 0.153), {"du Pont": 6}, "6, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "CO", (0.0, 0.154, 1.0, 0.174), {"Hoover": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "ME", (0.0, 0.154, 1.0, 0.174), {"Hoover": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "NY", (0.0, 0.154, 1.0, 0.174), {"Hoover": 3}, "3, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "WI", (0.0, 0.154, 1.0, 0.174), {"Hoover": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "MN", (0.0, 0.175, 1.0, 0.184), {"Kellogg": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "NY", (0.0, 0.186, 1.0, 0.195), {"Ward": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "NY", (0.0, 0.196, 1.0, 0.205), {"Butler": 4}, "4, listed among the scattering votes; the second read of the leaf came back empty"),
        (248, "PA", (0.0, 0.206, 1.0, 0.216), {"Knox": 1}, "1, listed among the scattering votes; the second read of the leaf came back empty"),
        (247, "MA", (0.636, 0.438, 0.677, 0.448), {"Johnson": 0}, "printed under Johnson but left out of the Johnson total; the rest of the delegation went to Coolidge"),
    ],
    "1868:democratic:b1": [
        (78, "KS", (0.759, 0.604, 0.873, 0.613), {"Hendricks": 0.5, "R. Johnson": 0}, "the half vote sits in the Hendricks column, whose printed total of 2½ counts it"),
    ],
    "1868:democratic:b2": [
        (80, "KS", (0.723, 0.604, 0.841, 0.614), {"Hendricks": 0.5, "R. Johnson": 0}, "the half vote sits in the Hendricks column, whose printed total of 2 counts it"),
    ],
    "1868:democratic:b3": [
        (83, "WI", (0.715, 0.867, 0.793, 0.875), {"Doolittle": 8, "Hendricks": 0}, "the figure sits in the Doolittle column, whose printed total of 12 counts it"),
    ],
    "1868:democratic:b4": [
        (86, "DE", (0.866, 0.54, 0.906, 0.554), {"Seymour": 0}, "the cell is empty; a speck beside its dots reads as a figure"),
        (86, "MD", (0.671, 0.642, 0.751, 0.656), {"Hendricks": 0.5, "Doolittle": 0}, "the half vote sits in the Hendricks column, whose printed total of 11½ counts it"),
        (86, "MI", (0.748, 0.662, 0.829, 0.676), {"R. Johnson": 8, "Blair": 0}, "the figure sits in the R. Johnson column, whose printed total of 8 counts it"),
    ],
    "1868:democratic:b5": [
        (99, "NY", (0.554, 0.724, 0.628, 0.732), {"Church": 33, "Packer": 0}, "the figure sits in the Church column, whose printed total of 33 counts it"),
        (99, "NC", (0.592, 0.734, 0.669, 0.742), {"Packer": 1, "A. Johnson": 0}, "the figure sits in the Packer column, whose printed total of 27 counts it"),
        (99, "MD", (0.633, 0.627, 0.709, 0.635), {"A. Johnson": 1, "Doolittle": 0}, "the figure sits in the A. Johnson column, whose printed total of 24 counts it"),
    ],
    "1868:democratic:b6": [
        (102, "NC", (0.568, 0.746, 0.607, 0.754), {"Pendleton": 0.5}, "printed 1½ beside 8½ for Hendricks in a delegation of 9; the Pendleton total of 122½ counts ½"),
        (102, "NH", (0.806, 0.717, 0.887, 0.725), {"Hendricks": 0.5, "Blair": 0}, "the half vote sits in the Hendricks column, whose printed total of 30 counts it"),
        (102, "MO", (0.446, 0.688, 0.487, 0.696), {"not voting": 0.5}, "11 held, 10½ recorded; 316½ of 317 votes cast"),
    ],
    "1868:democratic:b8": [
        (112, "TX", (0.5, 0.817, 0.533, 0.825), {"Hancock": 6}, "a broken 6; the delegation of 6 and the Hancock total of 28 count 6"),
        (112, "NH", (0.796, 0.721, 0.86, 0.729), {"Hendricks": 0.5, "Blair": 0}, "the half vote sits in the Hendricks column, whose printed total of 75 counts it"),
    ],
    "1868:democratic:b9": [
        (114, "IN", (0.319, 0.561, 0.355, 0.569), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b11": [
        (119, "IN", (0.537, 0.562, 0.577, 0.57), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b12": [
        (122, "IN", (0.432, 0.586, 0.471, 0.595), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
        (122, "PA", (0.555, 0.78, 0.63, 0.789), {"Packer": 26, "Parker": 0}, "the figure sits in the Packer column, whose printed total of 26 counts it"),
    ],
    "1868:democratic:b13": [
        (125, "IN", (0.487, 0.566, 0.518, 0.574), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b14": [
        (127, "IN", (0.62, 0.567, 0.661, 0.573), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b15": [
        (130, "CA", (0.587, 0.519, 0.623, 0.527), {"not voting": 0.5}, "5 held, 4½ recorded; the footnote prints ½ not voting"),
        (130, "IN", (0.587, 0.577, 0.623, 0.585), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b16": [
        (132, "CA", (0.489, 0.529, 0.526, 0.537), {"not voting": 0.5}, "5 held, 4½ recorded; the footnote prints ½ not voting"),
        (132, "IN", (0.489, 0.586, 0.526, 0.594), {"not voting": 0.5}, "13 held, 12½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b17": [
        (135, "KY", (0.546, 0.575, 0.583, 0.583), {"not voting": 0.5}, "11 held, 10½ recorded; the footnote prints ½ not voting"),
    ],
    "1868:democratic:b18": [
        (138, "KY", (0.523, 0.609, 0.561, 0.617), {"Hancock": 4.5}, "4½, which the delegation of 11 and the Hancock total of 144½ count"),
        (138, "MD", (0.523, 0.638, 0.561, 0.646), {"Hancock": 6.5}, "6½, which the delegation of 7 and the Hancock total of 144½ count"),
    ],
    "1868:democratic:b19": [
        (147, "NV", (0.77, 0.675, 0.836, 0.683), {"Field": 3, "Chase": 0}, "the figure sits in the Field column, whose printed total of 15 counts it"),
        (147, "KY", (0.54, 0.578, 0.564, 0.586), {"not voting": 1}, "11 held, 10 recorded; the footnote prints 1 not voting"),
    ],
    "1868:democratic:b20": [
        (150, "RI", (0.656, 0.768, 0.718, 0.78), {"Doolittle": 4, "Hendricks": 0}, "the figure sits in the Doolittle column, whose printed total of 12 counts it"),
        (150, "WI", (0.656, 0.836, 0.718, 0.848), {"Doolittle": 8, "Hendricks": 0}, "the figure sits in the Doolittle column, whose printed total of 12 counts it"),
        (150, "NH", (0.695, 0.7, 0.718, 0.712), {"Hendricks": 0.5}, "printed 1½ beside 4½ for Hancock in a delegation of 5; the Hendricks total of 121 counts ½"),
        (150, "KY", (0.533, 0.593, 0.556, 0.605), {"not voting": 0.5}, "11 held, 10½ recorded; the footnote prints ½ not voting"),
        (150, "MA", (0.533, 0.632, 0.556, 0.644), {"not voting": 1}, "12 held, 11 recorded; the footnote prints 1 not voting"),
    ],
    "1868:democratic:b21": [
        (152, "TN", (0.48, 0.792, 0.502, 0.8), {"not voting": 0.5}, "10 held, 9½ recorded; the footnote prints ½ not voting"),
    ],
    "1884:democratic:b1": [
        (266, "DE", (0.433, 0.294, 0.536, 0.306), {"Bayard": 6, "McDonald": 0}, "the figure sits in the Bayard column, whose printed total of 170 counts it"),
        (266, "NJ", (0.536, 0.536, 0.632, 0.548), {"Randall": 11, "Thurman": 0}, "the figure sits in the Randall column, whose printed total of 78 counts it"),
        (266, "WA", (0.584, 0.804, 0.632, 0.816), {"Randall": 1}, "the figure sits in the Randall column, whose printed total of 78 counts it"),
    ],
    "1872:democratic": [
        (66, "DE", (0.19, 0.517, 0.52, 0.529), {"Bayard": 6}, "Delaware, Bayard, 6, in the scattering list"),
        (66, "FL", (0.19, 0.531, 0.52, 0.543), {"not voting": 2}, "Florida, blank, 2, in the scattering list"),
        (66, "GA", (0.19, 0.546, 0.52, 0.558), {"not voting": 4}, "Georgia, blank, 4, in the scattering list"),
        (66, "NJ", (0.19, 0.561, 0.52, 0.573), {"Bayard": 9}, "New Jersey, Bayard, 9, in the scattering list"),
        (66, "PA", (0.53, 0.517, 0.85, 0.529), {"Black": 21}, "Pennsylvania, Black, 21, in the scattering list"),
        (66, "WV", (0.53, 0.531, 0.85, 0.543), {"not voting": 2}, "West Virginia, blank, 2, in the scattering list"),
    ],
    "1880:democratic:b1": [
        (124, "AL", (0.258, 0.188, 0.848, 0.199), {"votes": 20, "Bayard": 7, "Hancock": 7, "Field": 5, "Thurman": 1}, "20 held: 7, 7, 5, and 1 under Bayard, Hancock, Field, and Thurman"),
        (124, "CO", (0.258, 0.227, 0.848, 0.238), {"votes": 6, "Field": 1, "Loveland": 5}, "6 held: 1 under Field and 5 under Loveland"),
        (124, "CT", (0.258, 0.24, 0.848, 0.251), {"votes": 12, "Bayard": 4, "Thurman": 2, "Hendricks": 3, "Payne": 2, "not voting": 1}, "12 held, 11 recorded; the printed totals, 727\u00bd cast of 738, leave the vote unrecorded"),
        (124, "IL", (0.258, 0.293, 0.848, 0.304), {"Morrison": 42, "Ewing": 0}, "42, in the Morrison column, whose printed total of 62 counts it"),
        (124, "IA", (0.258, 0.319, 0.848, 0.33), {"Morrison": 6, "McDonald": 2, "Ewing": 0}, "6 under Morrison and 2 under McDonald, which their printed totals count"),
        (124, "KY", (0.258, 0.345, 0.848, 0.356), {"McDonald": 1, "Morrison": 0}, "1, in the McDonald column, whose printed total of 3 counts it"),
        (124, "MA", (0.258, 0.397, 0.848, 0.408), {"not voting": 1.5}, "26 held, 24\u00bd recorded; the printed totals, 727\u00bd cast of 738, leave the votes unrecorded"),
        (124, "MI", (0.258, 0.41, 0.848, 0.421), {"not voting": 6}, "22 held, 16 recorded; the printed totals, 727\u00bd cast of 738, leave the votes unrecorded"),
        (124, "NJ", (0.258, 0.501, 0.848, 0.512), {"votes": 18, "Bayard": 10, "Hendricks": 4, "Randall": 3, "Parker": 1, "McClellan": 0}, "18 held: 10 for Bayard, 4 for Hendricks, 3 for Randall, and 1 in the Parker column, whose printed total of 1 counts it"),
        (124, "PA", (0.258, 0.566, 0.848, 0.577), {"not voting": 1}, "58 held, 57 recorded; the printed totals, 727\u00bd cast of 738, leave the vote unrecorded"),
        (124, "TN", (0.258, 0.605, 0.848, 0.616), {"not voting": 1}, "24 held, 23 recorded; the printed totals, 727\u00bd cast of 738, leave the vote unrecorded"),
        (124, "WI", (0.258, 0.67, 0.848, 0.681), {"Morrison": 10, "McClellan": 1, "Ewing": 0, "Lothrop": 0}, "10 under Morrison and 1 under McClellan, which their printed totals count"),
    ],
    "1884:democratic:b2": [
        (280, "IL", (0.48, 0.333, 0.872, 0.343), {"not voting": 1}, "44 held, 43 recorded; the row is printed 'Illinois (1 absent)'"),
        (280, "NC", (0.48, 0.558, 0.872, 0.568), {"Bayard": 22, "Hendricks": 0}, "22, in the Bayard column, whose printed total of 151\u00bd counts it"),
        (280, "DC", (0.48, 0.745, 0.872, 0.755), {"Hendricks": 2, "Thurman": 0}, "2, in the Hendricks column, whose printed total of 123\u00bd counts it"),
        (280, "UT", (0.48, 0.795, 0.872, 0.805), {"Hendricks": 1, "Thurman": 0}, "1, in the Hendricks column, whose printed total of 123\u00bd counts it"),
    ],
    "1896:democratic:b1": [
        (330, "MA", (0.385, 0.441, 0.923, 0.453), {"Hill": 1, "Pennoyer": 0}, "1, in the Hill column beside Not Voting, whose printed total of 1 counts it"),
    ],
    "1896:democratic:b2": [
        (335, "CO", (0.460, 0.239, 0.922, 0.253), {"votes": 8, "Teller": 8, "Pennoyer": 0}, "8 held: 8 Teller; one read sets an extra column in the row"),
        (335, "CT", (0.460, 0.253, 0.922, 0.267), {"votes": 12, "Pattison": 2, "Not Voting": 10}, "12 held: 2 Pattison, 10 Not Voting; one read sets an extra column in the row"),
        (335, "DE", (0.460, 0.267, 0.922, 0.282), {"votes": 6, "Bryan": 1, "Pattison": 3, "Not Voting": 2}, "6 held: 1 Bryan, 3 Pattison, 2 Not Voting; one read sets an extra column in the row"),
        (335, "ME", (0.460, 0.412, 0.922, 0.426), {"votes": 12, "Bryan": 2, "Bland": 2, "Pattison": 5, "Not Voting": 3}, "12 held: 2 Bryan, 2 Bland, 5 Pattison, 3 Not Voting; one read sets an extra column in the row"),
        (335, "MD", (0.460, 0.426, 0.922, 0.440), {"votes": 16, "Bryan": 4, "Pattison": 11, "Not Voting": 1}, "16 held: 4 Bryan, 11 Pattison, 1 Not Voting; one read sets an extra column in the row"),
        (335, "MA", (0.460, 0.440, 0.922, 0.454), {"votes": 30, "Bryan": 1, "Bland": 2, "Matthews": 1, "Pattison": 3, "Stevenson": 5, "Hill": 1, "Not Voting": 17}, "30 held: 1 Bryan, 2 Bland, 1 Matthews, 3 Pattison, 5 Stevenson, 1 Hill, 17 Not Voting; one read sets an extra column in the row"),
        (335, "MN", (0.460, 0.469, 0.922, 0.483), {"votes": 18, "Bryan": 4, "Boies": 2, "Pattison": 1, "Blackburn": 2, "Stevenson": 4, "Not Voting": 5}, "18 held: 4 Bryan, 2 Boies, 1 Pattison, 2 Blackburn, 4 Stevenson, 5 Not Voting; one read sets an extra column in the row"),
        (335, "NH", (0.460, 0.556, 0.922, 0.570), {"votes": 8, "Pattison": 1, "Not Voting": 7}, "8 held: 1 Pattison, 7 Not Voting; one read sets an extra column in the row"),
        (335, "NJ", (0.460, 0.570, 0.922, 0.584), {"votes": 20, "Pattison": 2, "Not Voting": 18}, "20 held: 2 Pattison, 18 Not Voting; one read sets an extra column in the row"),
        (335, "NY", (0.460, 0.584, 0.922, 0.598), {"votes": 72, "Not Voting": 72}, "72 held: 72 Not Voting; one read sets an extra column in the row"),
        (335, "RI", (0.460, 0.670, 0.922, 0.685), {"votes": 8, "Pattison": 6, "Not Voting": 2}, "8 held: 6 Pattison, 2 Not Voting; one read sets an extra column in the row"),
        (335, "VT", (0.460, 0.757, 0.922, 0.771), {"votes": 8, "Bryan": 4, "Not Voting": 4}, "8 held: 4 Bryan, 4 Not Voting; one read sets an extra column in the row"),
        (335, "WI", (0.460, 0.814, 0.922, 0.828), {"votes": 24, "Bryan": 4, "Blackburn": 1, "Not Voting": 19}, "24 held: 4 Bryan, 1 Blackburn, 19 Not Voting; one read sets an extra column in the row"),
    ],
    "1892:democratic": [
        (182, "NC", (0.483, 0.577, 0.82, 0.589), {"Cleveland": 10 / 3, "Campbell": 0, "Morrison": 1}, "3⅓ for Cleveland and 1 in the Morrison column, which the delegation of 22 and the printed totals count"),
        (182, "MD", (0.437, 0.424, 0.481, 0.435), {"not voting": 0.5}, "16 held, 15½ recorded; 909½ of 910 votes cast"),
    ],
    "1864:republican": [
        (236, "ME", (0.1, 0.619, 0.4, 0.645), {"votes": 14}, "Maine casts her entire vote, 14 votes"),
        (236, "NH", (0.1, 0.649, 0.4, 0.747), {"votes": 10}, "New Hampshire casts her ten votes"),
        (236, "VT", (0.1, 0.751, 0.4, 0.784), {"votes": 10}, "Vermont's vote of ten"),
        (236, "MA", (0.1, 0.783, 0.4, 0.812), {"votes": 24}, "Massachusetts's twenty-four"),
        (236, "RI", (0.1, 0.82, 0.4, 0.844), {"votes": 8}, "Rhode Island casts her entire eight votes"),
        (236, "CT", (0.1, 0.85, 0.4, 0.881), {"votes": 12}, "Connecticut gives her twelve votes"),
        (239, "CA", (0.77, 0.3, 0.815, 0.314), {"Lincoln": 7}, "the Secretary's announced table gives California 7 for Lincoln; the roll as called says ten"),
    ],
    "1864:democratic": [
        (43, "NH", (0.707, 0.425, 0.821, 0.435), {"George B. McClellan": 5}, "a broken 5; New Hampshire held 5"),
        (43, "MA", (0.707, 0.446, 0.821, 0.456), {"George B. McClellan": 12}, "12; Massachusetts held 12"),
        (43, "CT", (0.707, 0.467, 0.821, 0.477), {"George B. McClellan": 6}, "printed 9; Connecticut held 6, and the announced 202½ for McClellan counts 6"),
        (43, "DE", (0.836, 0.510, 0.95, 0.520), {"Thomas H. Seymour": 3}, "printed 2; Delaware held 3, and the announced 23½ for Seymour, with 202½ for McClellan, makes the full 226"),
    ],
    "1860:democratic:baltimore-b1": [
        (168, "MA", (0.179, 0.788, 0.213, 0.797), {"Stephen A. Douglas": 10}, "printed 19 and corrected by hand to 10 in the scanned copy; the Douglas total of 173½ counts 10"),
        (168, "PA", (0.401, 0.833, 0.435, 0.842), {"Horatio Seymour": 1}, "1, which the Seymour total counts"),
    ],
    "1856:democratic:b13": [
        (41, "KY", (0.632, 0.37, 0.672, 0.378), {"Stephen A. Douglas": 7.5}, "7½, which the delegation of 12 and the Douglas total of 63 count"),
    ],
    "1856:democratic:b15": [
        (44, "OH", (0.334, 0.379, 0.378, 0.387), {"Stephen A. Douglas": 6.5}, "6½, which the delegation of 23 and the Douglas total of 118½ count"),
    ],
    "1856:democratic:b17": [
        (45, "ME", (0.05, 0.865, 0.95, 0.89), {"votes": 8, "James Buchanan": 8}, "cast 8 votes for James Buchanan"),
        (46, "NH", (0.05, 0.25, 0.95, 0.285), {"votes": 5, "James Buchanan": 5}, "cast 5 votes for James Buchanan"),
        (46, "VT", (0.05, 0.65, 0.95, 0.69), {"votes": 5, "James Buchanan": 5}, "cast 5 votes for James Buchanan"),
        (46, "MA", (0.05, 0.84, 0.95, 0.88), {"votes": 13, "James Buchanan": 13}, "cast 13 votes for James Buchanan"),
        (57, "RI", (0.07, 0.575, 0.95, 0.64), {"votes": 4, "James Buchanan": 4}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 4"),
        (47, "CT", (0.05, 0.28, 0.95, 0.305), {"votes": 6, "James Buchanan": 6}, "cast 6 votes for James Buchanan"),
        (57, "NY", (0.07, 0.575, 0.95, 0.64), {"votes": 35, "James Buchanan": 35}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 35"),
        (48, "NJ", (0.05, 0.125, 0.95, 0.145), {"votes": 7, "James Buchanan": 7}, "cast 7 votes for James Buchanan"),
        (57, "PA", (0.07, 0.575, 0.95, 0.64), {"votes": 27, "James Buchanan": 27}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 27"),
        (49, "DE", (0.05, 0.565, 0.95, 0.595), {"votes": 3, "James Buchanan": 3}, "cast 3 votes for James Buchanan"),
        (49, "MD", (0.05, 0.565, 0.95, 0.595), {"votes": 8, "James Buchanan": 8}, "cast 8 votes for James Buchanan"),
        (49, "VA", (0.05, 0.565, 0.95, 0.595), {"votes": 15, "James Buchanan": 15}, "cast 15 votes for James Buchanan"),
        (50, "NC", (0.05, 0.41, 0.95, 0.44), {"votes": 10, "James Buchanan": 10}, "cast 10 votes for James Buchanan"),
        (50, "SC", (0.05, 0.655, 0.95, 0.69), {"votes": 8, "James Buchanan": 8}, "cast 8 votes for James Buchanan"),
        (57, "GA", (0.07, 0.575, 0.95, 0.64), {"votes": 10, "James Buchanan": 10}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 10"),
        (57, "AL", (0.07, 0.575, 0.95, 0.64), {"votes": 9, "James Buchanan": 9}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 9"),
        (51, "MS", (0.05, 0.81, 0.95, 0.835), {"votes": 7, "James Buchanan": 7}, "cast 7 votes for James Buchanan"),
        (51, "LA", (0.05, 0.88, 0.95, 0.91), {"votes": 6, "James Buchanan": 6}, "cast 6 votes for James Buchanan"),
        (52, "OH", (0.05, 0.405, 0.95, 0.435), {"votes": 23, "James Buchanan": 23}, "cast 23 votes for James Buchanan"),
        (52, "KY", (0.05, 0.5, 0.95, 0.525), {"votes": 12, "James Buchanan": 12}, "cast 12 votes for James Buchanan"),
        (57, "TN", (0.07, 0.575, 0.95, 0.64), {"votes": 12, "James Buchanan": 12}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 12"),
        (52, "IN", (0.05, 0.815, 0.95, 0.84), {"votes": 13, "James Buchanan": 13}, "cast 13 votes for James Buchanan"),
        (53, "IL", (0.05, 0.355, 0.95, 0.385), {"votes": 11, "James Buchanan": 11}, "cast 11 votes for James Buchanan"),
        (57, "MO", (0.07, 0.575, 0.95, 0.64), {"votes": 9, "James Buchanan": 9}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 9"),
        (55, "AR", (0.05, 0.225, 0.95, 0.25), {"votes": 4, "James Buchanan": 4}, "cast 4 votes for James Buchanan"),
        (55, "MI", (0.05, 0.275, 0.95, 0.295), {"votes": 6, "James Buchanan": 6}, "cast 6 votes for James Buchanan"),
        (55, "FL", (0.05, 0.31, 0.95, 0.33), {"votes": 3, "James Buchanan": 3}, "cast 3 votes for James Buchanan"),
        (55, "TX", (0.05, 0.705, 0.95, 0.73), {"votes": 4, "James Buchanan": 4}, "cast 4 votes for James Buchanan"),
        (55, "IA", (0.05, 0.73, 0.95, 0.75), {"votes": 4, "James Buchanan": 4}, "cast 4 votes for James Buchanan"),
        (55, "WI", (0.05, 0.75, 0.95, 0.77), {"votes": 5, "James Buchanan": 5}, "cast 5 votes for James Buchanan"),
        (57, "CA", (0.07, 0.575, 0.95, 0.64), {"votes": 4, "James Buchanan": 4}, "the president announced 296 votes for James Buchanan as the whole vote cast, and the delegation held 4"),
    ],
}  # fmt: skip
TOTALS = {
    "1880:republican:b31": {"Edmunds": 11, "Windom": 3},
    "1864:republican": {"Lincoln": 484, "Grant": 22},
    "1904:democratic": {
        "Parker": 679,
        "Hearst": 181,
        "Gray": 12,
        "Miles": 3,
        "Cockrell": 42,
        "Olney": 38,
        "Wall": 27,
        "McClellan": 3,
        "Towne": 2,
        "Pattison": 4,
        "Williams": 8,
        "Coler": 1,
    },
    "1948:democratic": {"Russell": 263, "McNutt": 0.5, "Truman": 947.5},
    "1920:democratic:b1": {
        "votes": None,
        "McAdoo": 266,
        "Cox": 134,
        "Palmer": 256,
        "Cummings": 25,
        "Gerard": 21,
        "Owen": 33,
        "Hitchcock": 18,
        "Meredith": 27,
        "Smith": 109,
        "Edwards": 42,
        "Davis": 32,
        "Glass": 26.5,
        "Simmons": 24,
        "Harrison": 6,
        "Williams": 20,
        "Marshall": 37,
        "Wood": 4,
        "Clark": 9,
        "Underwood": 0.5,
        "Hearst": 1,
        "Bryan": 1,
        "Colby": 1,
        "Daniels": 1,
    },
    "1856:democratic:b7": {"James Buchanan": 143.5},
    "1856:democratic:b17": {"James Buchanan": 296},
    "1912:democratic:b4": {"Marshall": 31, "Kern": 2},
    "1912:democratic:b15": {"Harmon": 29},
    "1912:democratic:b40": {"Not voting": 0.5},
    "1920:republican:b10": {"Harding": 692.2, "Johnson": 80.8},
    "1868:democratic:b1": {"Hancock": 33.5},
    "1868:democratic:b6": {"Blair": 5},
    "1868:democratic:b21": {"Hoffman": 0.5},
    "1864:democratic": {"Thomas H. Seymour": 23.5},
}
MISPRINTS = {
    "1860:republican:vp-b1": (
        "The printed totals give Cassius M. Clay 101\u00bd and John Hickman 58, against 100\u00bd and 57 in the state rows. The rows are stored as printed.",
        {
            "column Clay": (101.5, 100.5, "the state figures add to 100\u00bd"),
            "column Hickman": (58, 57, "the state figures add to 57"),
        },
    ),
    "1860:republican:vp-b2": (
        "The printed total for Hannibal Hamlin is 357, against 367 in the state rows. The rows are stored as printed.",
        {"column Hamlin": (357, 367, "the state figures add to 367")},
    ),
    "1864:republican:vp": (
        "The printed total for Andrew Johnson is 494, against 492 in the state rows. The rows are stored as printed.",
        {"column Johnson": (494, 492, "the state figures add to 492")},
    ),
    "1884:republican:vp": (
        "The printed total for John A. Logan is 773, against 779 in the state rows. The rows are stored as printed.",
        {"column Logan": (773, 779, "the state figures add to 779")},
    ),
    "1848:democratic:vp-b1": (
        "The printed totals give William O. Butler 114 and John A. Quitman 74, against 123 and 65 in the state rows, which add to the same 252 votes; a row's 9 votes appear to be set in the wrong column. The rows are stored as printed.",
        {
            "column Butler": (114, 123, "the state figures add to 123"),
            "column Quitman": (74, 65, "the state figures add to 65"),
        },
    ),
    "1856:democratic:vp": (
        "The printed totals give John A. Quitman 59, John C. Breckinridge 51, and James C. Dobbin 13, against 49, 50, and 14 in the state rows; Florida's row gives Dobbin 4 of its 3 votes, and New York's row lists 25 of its 35. The rows are stored as printed.",
        {
            "column Quitman": (59, 49, "the state figures add to 49"),
            "column Breckinridge": (51, 50, "the state figures add to 50"),
            "column Dobbin": (13, 14, "the state figures add to 14"),
        },
    ),
    "1864:democratic:vp": (
        "The printed total for George H. Pendleton is 55\u00bd, against 55 in the state rows. The rows are stored as printed.",
        {"column Pendleton": (55.5, 55, "the state figures add to 55")},
    ),
    "1844:democratic:b1": (
        "North Carolina's row adds to 12 against its 11 votes and Georgia's to 9 against its 10. The rows are stored as printed.",
        {"NC": (11, 12, "the row adds to 12"), "GA": (10, 9, "the row adds to 9")},
    ),
    "1844:democratic:b3": (
        "The printed Buchanan total is 11, against 4 in the state figures; Louisiana's row prints 0 in every column against its 6 votes, and New Hampshire's adds to 5 against its 6. The rows are stored as printed.",
        {
            "column Buchanan": (11, 4, "the state figures add to 4"),
            "LA": (6, 0, "every column prints 0"),
            "NH": (6, 5, "the row adds to 5"),
        },
    ),
    **{
        f"1844:democratic:b{n}": (
            "Georgia's row adds to 9 against its 10 votes; the table has no column for its other vote. The row is stored as printed.",
            {"GA": (10, 9, "the row adds to 9")},
        )
        for n in (4, 5, 6, 7)
    },
    "1844:democratic:b8": (
        "The table has columns only for Van Buren, Cass, and Polk on this ballot, so the rows of New Jersey (6 of 7), Pennsylvania (25 of 26), North Carolina (10 of 11), and Georgia (9 of 10) leave out their other votes. The rows are stored as printed.",
        {
            "NJ": (7, 6, "the row adds to 6"),
            "PA": (26, 25, "the row adds to 25"),
            "NC": (11, 10, "the row adds to 10"),
            "GA": (10, 9, "the row adds to 9"),
        },
    ),
    "1852:democratic:b3": (
        "Buchanan's list prints Maryland, 15, where Virginia, which gave Buchanan its 15 votes on the ballots before and after, is meant; Maryland's 8 votes are also printed for Cass. The rows are stored as printed.",
        {},
    ),
    "1852:democratic:b5": (
        "The printed Douglas total is 34, against 33 in the state figures. The rows are stored as printed.",
        {"column Douglas": (34, 33, "the state figures add to 33")},
    ),
    "1852:democratic:b6": (
        "The printed Cass total is 114, against 116 in the state figures. The rows are stored as printed.",
        {"column Cass": (114, 116, "the state figures add to 116")},
    ),
    "1852:democratic:b12": (
        "The ballot gives Rhode Island 5 votes (Cass 3, Douglas 1, and Marcy 1) for a delegation of 4, and the printed totals count all five. The rows are stored as printed.",
        {},
    ),
    "1852:democratic:b13": (
        "Cass's list omits Louisiana, which gave Cass its 6 votes on the ballots before and after; the printed Cass total of 98 counts them, against 92 in the state figures. The rows are stored as printed.",
        {"column Cass": (98, 92, "the state figures add to 92")},
    ),
    "1852:democratic:b15": (
        "Douglas's list gives Florida 3, which with Dickinson's 1 is more than Florida's 3 votes. The printed Douglas total is 51, against 52 in the state figures. The rows are stored as printed.",
        {"column Douglas": (51, 52, "the state figures add to 52")},
    ),
    "1852:democratic:b17": (
        "Douglas's list omits Illinois, which gave Douglas its 11 votes on the ballots before and after. The printed Douglas total is 50, against 40 in the state figures. The rows are stored as printed.",
        {"column Douglas": (50, 40, "the state figures add to 40")},
    ),
    "1852:democratic:b23": (
        "Butler's list gives Maine 3, which with Buchanan's 5 and Douglas's 3 is more than Maine's 8 votes, while the lists account for only 2 of New Hampshire's 5. The rows are stored as printed.",
        {},
    ),
    "1852:democratic:b31": (
        "The printed Buchanan total is 83, against 78 in the state figures. The rows are stored as printed.",
        {"column Buchanan": (83, 78, "the state figures add to 78")},
    ),
    "1852:democratic:b34": (
        "The table prints Marcy 33 (Massachusetts 2, New York 22, and North Carolina 9) and gives North Carolina only 9 of its 10 votes. The president announced the result as Cass 130, Douglas 53, Buchanan 49, Marcy 35, Dickinson 16, Houston 5, and Butler 1. The rows are stored as the table prints them.",
        {},
    ),
    "1852:democratic:b46": (
        "Marcy's list gives Delaware 1, which with Cass's 3 is more than Delaware's 3 votes, while the lists account for only 7 of Maryland's 8. The rows are stored as printed.",
        {},
    ),
    "1852:democratic:b49": (
        (
            "The proceedings narrate this ballot state by state (pages 57 to 64). As first called, Vermont gave "
            "Douglas 5; Massachusetts gave Pierce 7, Marcy 5, and Douglas 1 before making its 13 votes unanimous "
            "for Pierce; New York gave Cass 10, Douglas 1, and Marcy 24; New Jersey gave Marcy 7; Pennsylvania gave "
            "Buchanan 27; Delaware gave Cass 3; and Maryland gave Cass 1, Buchanan 1, Marcy 1, and Pierce 5. These "
            "and the other delegations then changed their votes to Pierce before the result was announced; the "
            "tabulation stored here is the one printed after the changes (page 64). It prints Maryland's 5 for "
            "Pierce without the state's other 3 votes, and a Pierce total of 283, which the president announced "
            "with 6 scattering, though its state figures add to 279. The rows are stored as printed."
        ),
        {"column Pierce": (283, 279, "the state figures add to 279")},
    ),
    "1860:democratic:b1": (
        "The printed Guthrie total is 35, against 35½ in the state figures. The rows are stored as printed.",
        {"column Guthrie": (35, 35.5, "the state figures add to 35½")},
    ),
    "1860:democratic:b2": (
        (
            "The table prints no New Jersey row, Massachusetts's row prints Douglas 6½ and Hunter 5½, or 12 of its "
            "13 votes, and Indiana's prints Douglas 18 for a delegation of 13. The printed totals give Douglas 147, "
            "Guthrie 36½, and Davis 1, against 152, 29½, and none in the state figures. The rows are stored as "
            "printed."
        ),
        {
            "column Douglas": (147, 152, "the state figures add to 152"),
            "column Guthrie": (36.5, 29.5, "the state figures add to 29½"),
            "column Davis": (1, 0, "no state figure gives Davis a vote"),
        },
    ),
    "1860:democratic:b3": (
        (
            "The printed totals give Douglas 148½, Guthrie 42, Dickinson 6½, and Hunter 36, against 145½, 40, 7½, "
            "and 40 in the state figures. The rows are stored as printed."
        ),
        {
            "column Douglas": (148.5, 145.5, "the state figures add to 145½"),
            "column Guthrie": (42, 40, "the state figures add to 40"),
            "column Dickinson": (6.5, 7.5, "the state figures add to 7½"),
            "column Hunter": (36, 40, "the state figures add to 40"),
        },
    ),
    "1860:democratic:b5": (
        (
            "North Carolina's row prints Guthrie 1 and Hunter 9, but the printed totals of Douglas 149½ and Guthrie "
            "37½ count that vote for Douglas, as North Carolina gave it on the ballots before and after. The rows "
            "are stored as printed."
        ),
        {
            "column Douglas": (149.5, 148.5, "the state figures add to 148½"),
            "column Guthrie": (37.5, 38.5, "the state figures add to 38½"),
        },
    ),
    "1860:democratic:b6": (
        (
            "Pennsylvania's row prints Guthrie's figure in broken type, as '0 1 2'. It is stored as 9½, the figure "
            "the delegation of 27 and the printed Guthrie total of 39½ both need."
        ),
        {},
    ),
    "1860:democratic:b9": (
        (
            "Massachusetts's row prints Douglas 7, Hunter 4, and Davis 1½, or 12½ of its 13 votes. The printed "
            "totals give Douglas 150½, Guthrie 41, Hunter 39½, and Davis 1, against 149½, 41½, 39, and 1½ in the "
            "state figures. The rows are stored as printed."
        ),
        {
            "column Douglas": (150.5, 149.5, "the state figures add to 149½"),
            "column Guthrie": (41, 41.5, "the state figures add to 41½"),
            "column Hunter": (39.5, 39, "the state figures add to 39"),
            "column Davis": (1, 1.5, "the state figures add to 1½"),
        },
    ),
    "1860:democratic:b15": (
        (
            "Pennsylvania's row prints Douglas 9½, Guthrie 12, Hunter 3, and Lane 2¼, a quarter vote short of its "
            "27, and the printed Lane total is 20½. The rows are stored as printed."
        ),
        {"column Lane": (20.5, 20.25, "the state figures add to 20¼")},
    ),
    "1860:democratic:b16": (
        (
            "Connecticut's row prints Douglas 3 and Guthrie 2½, half a vote short of its 6, and the printed Douglas "
            "total is 150. The rows are stored as printed."
        ),
        {"column Douglas": (150, 149.5, "the state figures add to 149½")},
    ),
    "1860:democratic:b21": (
        (
            "Wisconsin's row prints Douglas 4 for a delegation of 5, and the printed Douglas total is 150½. The rows "
            "are stored as printed."
        ),
        {"column Douglas": (150.5, 149.5, "the state figures add to 149½")},
    ),
    "1860:democratic:b23": (
        (
            "Pennsylvania's row prints Douglas 9½, Guthrie 12½, Hunter 2, and Lane 2½, or 26½ of its 27 votes, and "
            "the printed Hunter total of 25 is half a vote more than the state figures. The rows are stored as printed."
        ),
        {"column Hunter": (25, 24.5, "the state figures add to 24½")},
    ),
    "1860:democratic:b26": (
        (
            "Maryland's row prints Douglas 4, Hunter 4, and Lane 1, or 9 votes for a delegation of 8, and Minnesota's "
            "prints Douglas 3 and Lane 1. The printed totals give Douglas 151½, Johnson 12, and Lane 9, against "
            "150½, 11, and 11 in the state figures. The rows are stored as printed."
        ),
        {
            "column Douglas": (151.5, 150.5, "the state figures add to 150½"),
            "column Johnson": (12, 11, "the state figures add to 11"),
            "column Lane": (9, 11, "the state figures add to 11"),
        },
    ),
    "1860:democratic:b32": (
        "The printed Guthrie total is 47¼, against 47½ in the state figures. The rows are stored as printed.",
        {"column Guthrie": (47.25, 47.5, "the state figures add to 47½")},
    ),
    "1860:democratic:b35": (
        "The printed Guthrie total is 47¼, against 47½ in the state figures. The rows are stored as printed.",
        {"column Guthrie": (47.25, 47.5, "the state figures add to 47½")},
    ),
    "1860:democratic:b37": (
        (
            "Tennessee's row prints Douglas 1, Guthrie 10½, and Johnson ¼, a quarter vote short of its 12, and the "
            "printed Johnson total is ½. The rows are stored as printed."
        ),
        {"column Johnson": (0.5, 0.25, "the state figures add to ¼")},
    ),
    "1860:democratic:b41": (
        (
            "Connecticut's row prints Douglas 3¼ and Guthrie 2½, a quarter vote short of its 6, and the printed "
            "Douglas total is 151½. The rows are stored as printed."
        ),
        {"column Douglas": (151.5, 151.25, "the state figures add to 151¼")},
    ),
    "1860:democratic:b43": (
        (
            "Maryland's row prints Douglas 4 and Hunter 4, but the printed totals of Guthrie 65½ and Hunter 16 count "
            "its 4 for Guthrie, as Maryland gave them on the ballots before and after. The rows are stored as printed."
        ),
        {
            "column Guthrie": (65.5, 61.5, "the state figures add to 61½"),
            "column Hunter": (16, 20, "the state figures add to 20"),
        },
    ),
    "1860:democratic:b52": (
        (
            "Massachusetts's row prints Douglas 6, Hunter 6, and Davis 1, but the printed totals of Guthrie 65½ and "
            "Hunter 16 count its 6 for Guthrie, as Massachusetts gave them on the 51st ballot. The rows are stored as "
            "printed."
        ),
        {
            "column Guthrie": (65.5, 59.5, "the state figures add to 59½"),
            "column Hunter": (16, 22, "the state figures add to 22"),
        },
    ),
    "1860:democratic:b53": (
        (
            "Massachusetts's row prints Douglas 6, Hunter 6, and Davis 1, as on the 52nd ballot, but the printed "
            "totals of Guthrie 65½ and Hunter 16 count its 6 for Guthrie. The rows are stored as printed."
        ),
        {
            "column Guthrie": (65.5, 59.5, "the state figures add to 59½"),
            "column Hunter": (16, 22, "the state figures add to 22"),
        },
    ),
    "1860:democratic:b54": (
        (
            "Massachusetts's row prints Douglas 6, Hunter 6, and Davis 1, but the printed totals of Guthrie 61 and "
            "Hunter 20½ count its 6 for Guthrie. The rows are stored as printed."
        ),
        {
            "column Guthrie": (61, 55, "the state figures add to 55"),
            "column Hunter": (20.5, 26.5, "the state figures add to 26½"),
        },
    ),
    "1884:republican:b1": (
        (
            "The printed totals give John Sherman 30 votes and Hawley 13, against 31 and 12 in the state rows, "
            "where New Jersey's single vote is printed in the Sherman column. The rows are stored as printed."
        ),
        {
            "column J. Sherman": (30, 31, "the state figures add to 31"),
            "column Hawley": (13, 12, "the state figures add to 12"),
        },
    ),
    "1912:republican": (
        (
            "The printed total of the votes column is 1,550, against the 1,078 votes the state rows hold. The "
            "Massachusetts row prints 20 for Taft and 21 present and not voting against a delegation of 36, the "
            "Illinois row accounts for 57 of its 58 votes, and the present-and-not-voting rows add to 348 against a "
            "printed total of 349. The rows are stored as printed."
        ),
        {
            "MA": (36, 41, "20 and 21 against a delegation of 36"),
            "IL": (58, 57, "57 of 58 votes accounted for"),
            "column votes": (1550, 1078, "the state rows hold 1,078 votes"),
            "column Present and Not Voting": (349, 348, "the state figures add to 348"),
        },
    ),
    "1880:republican:b7": (
        (
            "The printed Sherman total, which the chair also announced, is 94; the state rows add to 95, and with "
            "95 the columns add to the 756 delegates. The state figures are stored as printed; Indiana's row adds to 31 "
            "votes against its 30 delegates."
        ),
        {"column Sherman": (94, 95, "the state figures add to 95"), "IN": (30, 31, "the row adds to 31")},
    ),
    "1880:republican:b24": (
        (
            "The printed Sherman total of 73 is a misprint for 93, the figure the chair announced and the state "
            "rows give."
        ),
        {"column Sherman": (73, 93, "the state figures add to 93, as announced")},
    ),
    "1880:republican:b31": (
        (
            "The page that continues this table prints the Windom and Edmunds headings in each other's places, so "
            "Vermont's 10 votes, cast for Edmunds on every other ballot, and the totals of 11 and 3 sit under the "
            "swapped headings. They are stored as Edmunds 11 and Windom 3, as the chair announced."
        ),
        {},
    ),
    "1856:republican:informal": (
        (
            "The Proceedings print the New Hampshire row as 15 for Fremont and 14 for McLean, against a delegation "
            "of 15 (three delegates for each of the state's five electoral votes); it is stored as printed. Without "
            "the 14, McLean's total would be 190. No totals are printed with the table."
        ),
        {},
    ),
    "1876:democratic:b1": (
        (
            "The printed Tilden total of 404\u00bd does not match the table's state rows, which add to 401\u00bd "
            "(the figure the rows give is stored); the other columns match their printed totals. The Nevada row "
            "prints three votes each for Tilden, Thurman, and Hendricks, nine from a delegation of six, and is stored as printed."
        ),
        {
            "NV": (6, 9, "three votes each for three candidates against a delegation of six"),
            "column Tilden": (404.5, 401.5, "the state figures add to 401\u00bd"),
        },
    ),
    "1864:republican": (
        (
            "California is stored as the Secretary's announced table gives it, 7 for Lincoln, though the roll as "
            "called records California casting ten votes. The announced total of 484 for Lincoln, of 506 cast, "
            "does not match the table's state figures, which add to 494."
        ),
        {
            "CA": (10, 7, "7 announced for Lincoln against the ten California cast on the call"),
            "column Lincoln": (484, 494, "the state figures add to 494"),
        },
    ),
    "1920:democratic:b1": (
        (
            "The Official Proceedings print this ballot's table with internal inconsistencies, stored as printed: "
            "the Delaware, California, Maine, Michigan, Minnesota, Missouri, and Tennessee rows do not add to the "
            "delegation's vote, and the printed totals for Palmer, Edwards, Davis, Colby, and Daniels do not match "
            "the state figures. No second official source has been found to settle them."
        ),
        {
            "DE": (6, 8, "4 for McAdoo and 4 for Marshall against a delegation of 6"),
            "CA": (26, 25, "the row prints 25 of the delegation's 26"),
            "ME": (12, 13, "the row prints 13 against a delegation of 12"),
            "MI": (30, 29, "the row prints 29 of the delegation's 30"),
            "MN": (24, 23, "the row prints 23 of the delegation's 24"),
            "MO": (36, 35, "the row prints 35 of the delegation's 36"),
            "TN": (24, 23, "the row prints 23 of the delegation's 24"),
            "column Palmer": (256, 254, "the printed total exceeds the state figures by 2"),
            "column Edwards": (42, 46, "the state figures exceed the printed total by 4"),
            "column Davis": (32, 30, "the printed total exceeds the state figures by 2"),
            "column Colby": (1, 0, "a printed total of 1 with no state figure"),
            "column Daniels": (1, 0, "a printed total of 1 with no state figure"),
        },
    ),
    "1920:democratic:b44": (
        (
            "The Official Proceedings print this ballot's table with an internal inconsistency, stored as printed: "
            "the Virginia row (2½, 18½, 2½, and 1½) adds to 25 against a delegation of 24. No totals were "
            "announced, because Cox was declared the nominee by acclamation, and no second official source has "
            "been found to settle it."
        ),
        {"VA": (24, 25, "2½, 18½, 2½, and 1½ against a delegation of 24")},
    ),
}
NAMES = {re.sub(r"[^a-z]", "", re.sub(r" \(.*\)|, U\.S\.", "", name).lower()): code for name, code in PLACES.items()}
PAGES = DATA / "pages" / SOURCE
VULGAR = re.compile(r"\s*([⁰¹²³⁴⁵⁶⁷⁸⁹]+)⁄([₀₁₂₃₄₅₆₇₈₉]+)$|\s+(\d+)/(\d+)$")
SMALL = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "01234567890123456789")
FRACTION = re.compile(r"\s*(?:([½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])|1/2|'/2|\.5)$")


def fetch() -> None:
    for convention in CONVENTIONS:
        for url in convention.get("captions", ()):
            raw.fetch(SOURCE, url, suffix=".html")
        if "captions" not in convention:
            raw.fetch(SOURCE, page(convention["title"]), suffix=".json")
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch, scans

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for spec in TABLES:
        for leaf in spec["leaves"]:
            image = scan(spec, leaf, fetch=True)
            data = base64.b64encode(cropped(image.path, region(spec, leaf))).decode()
            for effort, scatter in product(READS.values(), (False, True)):
                if skipped(spec, leaf, scatter):
                    continue
                ask = (
                    BYCANDIDATE.format(ordinal(spec["ballot"]))
                    if spec.get("bycandidate")
                    else SCATTER
                    if scatter
                    else NARRATED
                    if spec.get("narrated")
                    else ROLL
                    if spec.get("ballot") is None or region(spec, leaf)
                    else ROLL + BALLOT.format(ordinal(spec["ballot"]))
                )
                wanted[read_key(effort, image.sha256, variant(spec, scatter, leaf))] = (
                    MessageCreateParamsNonStreaming(
                        model="claude-haiku-5-5",
                        max_tokens=16000,
                        system=scans.SYSTEM,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
                                    },
                                    {"type": "text", "text": ask},
                                ],
                            }
                        ],
                        output_config={"effort": effort, "format": {"type": "json_schema", "schema": scans.SCHEMA}},
                    ),
                    (3000 * price_in + 4000 * price_out) / 1e6,
                )
    print(batch.ensure(SOURCE, "rollcalls", wanted)[1])


def sheet(spec: dict[str, Any], fetch: bool) -> raw.Artifact:
    import subprocess
    import tempfile

    url = f"{spec['pdf']}#page=1"
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        if not fetch:
            raise
    pdf = raw.fetch(SOURCE, spec["pdf"], suffix=".pdf")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "page"
        subprocess.run(["pdftoppm", "-r", "200", "-jpeg", "-singlefile", str(pdf.path), str(out)], check=True)
        content = out.with_suffix(".jpg").read_bytes()
    return raw.store(SOURCE, url, content, suffix=".jpg", retrieved_at=pdf.retrieved_at, media_type="image/jpeg")


def scan(spec: dict[str, Any], leaf: int, fetch: bool = False) -> raw.Artifact:
    if "manual" in spec:
        try:
            return raw.cached(SOURCE, spec["url"])
        except LookupError:
            if not fetch:
                raise
        return render_page(spec["url"], 1, spec["manual"])
    if "image" in spec:
        return raw.fetch(SOURCE, spec["image"], suffix=".jpg") if fetch else raw.cached(SOURCE, spec["image"])
    if "pdf" in spec:
        return sheet(spec, fetch)
    if "uky" in spec:
        url = EXPLOREUK.format(item=spec["uky"], leaf=leaf)
        return raw.fetch(SOURCE, url, suffix=".jpg") if fetch else raw.cached(SOURCE, url)
    if "gbooks" in spec:
        url = GBOOKS.format(gbooks=spec["gbooks"], leaf=leaf)
        try:
            return raw.cached(SOURCE, url)
        except LookupError:
            if not fetch:
                raise
        return render_page(url, spec["pdf_page"][leaf], spec.get("mf", "MF-607"))
    if "htid" not in spec:
        url = LEAF.format(item=spec["item"], leaf=leaf)
        return raw.fetch(SOURCE, url, suffix=".jpg") if fetch else raw.cached(SOURCE, url)
    try:
        return raw.cached(SOURCE, HATHI.format(htid=spec["htid"], leaf=leaf))
    except LookupError:
        if not fetch:
            raise
    return render(spec, leaf)


def render_page(url: str, page_number: int, folder_name: str) -> raw.Artifact:
    import subprocess
    import tempfile

    (pdf,) = (DATA / "inbox" / "manual").glob(f"{folder_name}/*.pdf")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "300",
                "-gray",
                "-jpeg",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                str(pdf),
                str(out),
            ],
            check=True,
        )
        content = out.with_suffix(".jpg").read_bytes()
    return raw.store(
        SOURCE,
        url,
        content,
        suffix=".jpg",
        retrieved_at=datetime.fromtimestamp(pdf.stat().st_mtime, UTC),
        media_type="image/jpeg",
    )


def render(spec: dict[str, Any], leaf: int) -> raw.Artifact:
    import subprocess
    import tempfile

    from PIL import Image

    (pdf,) = (DATA / "inbox" / "manual").glob(f"MF-*/{str(spec['htid']).replace('$', '')}/*-{leaf}-*.pdf")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "page"
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", "-singlefile", str(pdf), str(out)], check=True)
        with Image.open(out.with_suffix(".png")) as opened:
            image = opened.rotate(-90, expand=True) if spec.get("rotate") else opened.copy()
    data = io.BytesIO()
    image.save(data, "JPEG", quality=90)
    return raw.store(
        SOURCE,
        HATHI.format(htid=spec["htid"], leaf=leaf),
        data.getvalue(),
        suffix=".jpg",
        retrieved_at=datetime.fromtimestamp(pdf.stat().st_mtime, UTC),
        media_type="image/jpeg",
    )


def cropped(path: Path, crop: str | None) -> bytes:
    if crop is None:
        return path.read_bytes()
    from PIL import Image

    with Image.open(path) as image:
        x0, y0, x1, y1 = QUADRANTS[crop]
        out = io.BytesIO()
        image.crop(
            (round(x0 * image.width), round(y0 * image.height), round(x1 * image.width), round(y1 * image.height))
        ).save(out, "JPEG", quality=95)
        return out.getvalue()


def region(spec: dict[str, Any], leaf: int) -> str | None:
    part = spec.get("parts", {}).get(leaf)
    return part if part in QUADRANTS else spec.get("crop")


def skipped(spec: dict[str, Any], leaf: int, scatter: bool) -> bool:
    only = spec.get("reads", {}).get(leaf)
    return (
        "transcribed" in spec
        or (scatter and not spec.get("scatter"))
        or (not scatter and bool(spec.get("bycandidate")))
        or (only is not None and only != ("scatter" if scatter else "table"))
    )


def variant(spec: dict[str, Any], scatter: bool = False, leaf: int | None = None) -> str | None:
    if scatter:
        return f"c{spec['ballot']}" if spec.get("bycandidate") else "s"
    if spec.get("crop"):
        return f"b{spec['ballot']}{spec['crop']}"
    if spec.get("parts", {}).get(leaf, "head") != "head":
        return spec["parts"][leaf]
    if spec.get("narrated"):
        return "n"
    return None if spec.get("ballot") is None else f"b{spec['ballot']}"


def read_key(effort: Literal["low", "medium"], digest: str, kind: str | None = None) -> str:
    prompt = PROMPT if kind is None else f"{PROMPT}{kind}"
    return f"anthropic-batch:claude-haiku-5-5/{prompt}/{effort}/{digest}"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def place(label: str) -> str | None:
    plain = re.sub(r"[^a-z]", "", label.lower().split("(")[0])
    return NAMES.get(plain) or ABBREVIATIONS.get(plain)


def number(cell: str) -> float:
    text = re.sub(r"\\?t?ext(?:em|en)dash", "", cell).strip().replace(",", "")
    text = re.sub(r"(?<=\d)1-6$", "⅙", re.sub(r"[•*]+$", "", text))
    if re.fullmatch(r"[\dIl]+", text):
        text = text.replace("I", "1").replace("l", "1")
    vulgar = VULGAR.search(text)
    if vulgar:
        over, under = (vulgar[1] or vulgar[3]).translate(SMALL), (vulgar[2] or vulgar[4]).translate(SMALL)
        return number(text[: vulgar.start()]) + int(over) / int(under)
    fraction = FRACTION.search(text)
    text = re.sub(r"^[\s.\-—–·*…]+", "", text[: fraction.start()] if fraction else text)
    part = (unicodedata.numeric(fraction[1]) if fraction[1] else 0.5) if fraction else 0.0
    return (float(text) if text else 0.0) + part


def candidate(label: str, table: str) -> str:
    label = label.strip().rstrip(".:- ")
    if ":democratic" in table:
        label = re.sub(r"^For\s+", "", label)
        if sum(ch.isupper() for ch in label) > sum(ch.islower() for ch in label):
            label = label.title()
    return LABELS.get(label, label)


def scattered(label: str) -> str:
    label = label.strip().rstrip(".:- ")
    if sum(ch.isupper() for ch in label) > sum(ch.islower() for ch in label):
        label = label.title()
    return LABELS.get(label, label)


def transcribed(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, float], list[str]]:
    rows = pl.read_csv(Path(__file__).with_name(str(spec["transcribed"])), separator="\t").filter(
        pl.col("ballot") == spec["ballot"]
    )
    cells, totals, problems = [], {}, []
    for row in rows.iter_rows(named=True):
        if row["state"] == "TOTAL":
            totals[row["candidate"]] = float(row["votes"])
            continue
        if row["leaf"] not in spec["leaves"]:
            problems.append(f"leaf {row['leaf']}: {row['state']} lies outside the ballot's leaves")
        cells.append(
            {
                "table": spec["table"],
                "state": row["state"],
                "column": row["candidate"],
                "value": float(row["votes"]),
                "leaf": row["leaf"],
                "sha256": scan(spec, row["leaf"]).sha256,
                "box": [row["x0"], row["y0"], row["x1"], row["y1"]],
            }
        )
    return cells, totals, problems


def table(
    spec: dict[str, Any], effort: Literal["low", "medium"]
) -> tuple[list[dict[str, Any]], dict[str, float], list[str]]:
    if "transcribed" in spec:
        return transcribed(spec)
    cells: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    shifted: dict[str, float] = {}
    problems: list[str] = []
    fixed = {(code, column) for _, code, _, figures, _ in CORRECTIONS.get(spec["table"], ()) for column in figures}
    for leaf, scatter in product(spec["leaves"], (False, True)):
        if skipped(spec, leaf, scatter):
            continue
        image = scan(spec, leaf)
        try:
            response = json.loads(
                raw.cached(SOURCE, read_key(effort, image.sha256, variant(spec, scatter, leaf))).path.read_text()
            )
        except LookupError:
            problems.append(f"leaf {leaf}: not read")
            continue
        text = next((block["text"] for block in response["content"] if block["type"] == "text"), "{}")
        read = json.loads(text)
        header = read["header"]
        if not read["rows"]:
            continue
        if (
            header
            and re.fullmatch(r"states?\.?", header[0]["label"].strip(), re.IGNORECASE)
            and all(len(row["values"]) < len(header) for row in read["rows"])
        ):
            header = header[1:]
        if scatter:
            for column in header:
                if place(column["label"]) is None and not re.fullmatch(
                    r"\s*totals?\.?\s*", column["label"], re.IGNORECASE
                ):
                    problems.append(f"leaf {leaf}: unknown state {column['label']!r}")
            for row in read["rows"]:
                if len(row["values"]) != len(header):
                    problems.append(f"leaf {leaf}: {row['label']} has {len(row['values'])} cells")
                    continue
                for column, value in zip(header, row["values"], strict=True):
                    code = place(column["label"])
                    if (
                        code is None
                        and value.strip()
                        and re.fullmatch(r"\s*totals?\.?\s*", column["label"], re.IGNORECASE)
                    ):
                        try:
                            totals[scattered(row["label"])] = totals.get(scattered(row["label"]), 0) + number(value)
                        except ValueError:
                            problems.append(f"leaf {leaf}: {row['label']} total reads {value!r}")
                        continue
                    if code is None or not value.strip():
                        continue
                    try:
                        figure = number(value)
                    except ValueError:
                        problems.append(f"leaf {leaf}: {column['label']} {row['label']} reads {value!r}")
                        continue
                    cells.append(
                        {
                            "table": spec["table"],
                            "state": code,
                            "column": scattered(row["label"]),
                            "value": figure,
                            "leaf": leaf,
                            "sha256": image.sha256,
                            "box": [0.0, row["y0"] / 1000, 1.0, row["y1"] / 1000],
                        }
                    )
            continue
        roles = (
            spec.get("columns")
            or COLUMNS.get(spec["table"])
            or tuple(
                "votes"
                if re.search(r"vot(?!ing)|delegate|no\.? of del|whole|^\s*total\.?\s*$", column["label"], re.IGNORECASE)
                else candidate(column["label"], spec["table"]) or None
                for column in header
            )
        )
        if spec.get("electors") and len(roles) == len(header) + 1:
            roles = roles[1:]
        if len(roles) != len(header):
            problems.append(f"leaf {leaf}: {len(header)} columns for {len(roles)} roles")
            continue
        rows = read["rows"]
        x0, y0, x1, y1 = QUADRANTS.get(region(spec, leaf), (0.0, 0.0, 1.0, 1.0))
        if spec.get("parts", {}).get(leaf) == "head":
            rows = rows[
                : next(
                    (i + 1 for i, row in enumerate(rows) if re.match(r"^\s*totals?\b", row["label"], re.IGNORECASE)),
                    len(rows),
                )
            ]
        seen: dict[str | None, int] = {}
        for row in rows:
            values = row["values"]
            if (
                roles[0] == "votes"
                and place(row["label"]) is None
                and re.match(r"^\s*totals?\b", row["label"], re.IGNORECASE)
            ):
                if len(values) == len(header) - 1:
                    values = ["", *values]
                elif len(values) == len(header) and values[0].strip() and not number(values[-1]):
                    shifted |= {
                        role: number(value)
                        for role, value in zip(roles[1:], values, strict=False)
                        if role and value.strip()
                    }
            if len(values) == len(header) + 1 and not values[-1].strip(" .…"):
                values = values[:-1]
            if len(values) != len(header):
                if not any(code == place(row["label"]) for code, _ in fixed):
                    problems.append(f"leaf {leaf}: {row['label']} has {len(values)} cells")
                continue
            code = place(row["label"])
            seen[code] = seen.get(code, 0) + 1
            code = RELABEL.get((spec["table"], leaf, code, seen[code]), code)
            if code is None and re.match(r"^\s*(territories|necessary to a choice)\b", row["label"], re.IGNORECASE):
                continue
            if code is None and not re.match(r"^\s*(totals?\b|$)", row["label"], re.IGNORECASE):
                problems.append(f"leaf {leaf}: unknown row {row['label']!r}")
                continue
            for role, column, value in zip(roles, header, values, strict=True):
                if (
                    role is None
                    and code is not None
                    and not spec.get("electors")
                    and not column["label"].strip()
                    and value.strip(" .…")
                ):
                    problems.append(f"leaf {leaf}: {row['label']} has {value!r} under an unlabeled column")
                if role is None or (not value.strip() and roles.count(role) > 1):
                    continue
                try:
                    figure = number(value)
                except ValueError:
                    if (code, role) not in fixed:
                        problems.append(f"leaf {leaf}: {row['label']} {role} reads {value!r}")
                    continue
                if code is None:
                    if value.strip() and (figure or role != "votes"):
                        totals[role] = figure
                    continue
                cells.append(
                    {
                        "table": spec["table"],
                        "state": code,
                        "column": role,
                        "value": figure,
                        "leaf": leaf,
                        "sha256": image.sha256,
                        "box": [
                            round(x0 + column["x0"] / 1000 * (x1 - x0), 4),
                            round(y0 + row["y0"] / 1000 * (y1 - y0), 4),
                            round(x0 + column["x1"] / 1000 * (x1 - x0), 4),
                            round(y0 + row["y1"] / 1000 * (y1 - y0), 4),
                        ],
                    }
                )
    if spec.get("announced"):
        cells = list({(cell["state"], cell["column"]): cell for cell in cells}.values())
    held = sum(cell["value"] for cell in cells if cell["column"] == "votes")
    if shifted and totals.get("votes", held) != held:
        totals = shifted
    totals = {role: total for role, total in (totals | TOTALS.get(spec["table"], {})).items() if total is not None}
    if spec.get("only"):
        cells = []
    for leaf, code, box, figures, _ in CORRECTIONS.get(spec["table"], ()):
        image = scan(spec, leaf)
        cells = [cell for cell in cells if cell["state"] != code or cell["column"] not in figures]
        cells += [
            {
                "table": spec["table"],
                "state": code,
                "column": column,
                "value": float(figure),
                "leaf": leaf,
                "sha256": image.sha256,
                "box": list(box),
            }
            for column, figure in figures.items()
            if figure or not spec.get("bycandidate")
        ]
    return cells, totals, problems


def parse() -> None:
    from PIL import Image

    folder = DATA / "parsed" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)
    frames, report = [], []
    for spec in TABLES:
        reads = {effort: table(spec, effort) for effort in READS.values()}
        (first, printed, problems), (second, _, others) = reads["low"], reads["medium"]
        values = {(cell["state"], cell["column"]): cell["value"] for cell in first}
        other = {(cell["state"], cell["column"]): cell["value"] for cell in second}
        disagree = sorted(key for key in values.keys() | other.keys() if values.get(key, 0) != other.get(key, 0))
        failures = [*problems, *others, *(f"reads disagree on {state} {column}" for state, column in disagree)]
        if len(values) != len(first):
            failures.append(f"{len(first) - len(values)} cells read twice")
        held = {state for state, _ in values}
        allowed = MISPRINTS.get(spec["table"], ("", {}))[1]
        for state in sorted(held):
            if (state, "votes") not in values and not any(column == "votes" for _, column in values):
                continue
            votes = values.get((state, "votes"), 0)
            cast = sum(value for (code, column), value in values.items() if code == state and column != "votes")
            if (
                abs(votes - cast) > 1e-6
                and allowed.get(state, ())[:2] != (votes, cast)
                and not (cast < votes and (spec["table"].startswith("1880:republican:") or spec.get("short")))
            ):
                failures.append(f"{state}: {cast:g} cast of {votes:g} held")
        for column, total in printed.items():
            summed = sum(value for (_, name), value in values.items() if name == column)
            if abs(summed - total) > 1e-6 and allowed.get(f"column {column}", ())[:2] != (total, summed):
                failures.append(f"column {column}: states sum to {summed:g}, printed total {total:g}")
        placed = next((read for read in (first, second) if read and all(max(cell["box"]) <= 1 for cell in read)), None)
        if placed is None:
            failures.append("no read places its cells on the page")
        report.append(f"## {spec['table']}\n\n{len(held)} delegations, {len(values)} cells, printed totals {printed}")
        report.extend(f"- {failure}" for failure in failures)
        report.append("")
        if not failures:
            frames.append(pl.DataFrame(placed))
            for leaf in spec["leaves"]:
                image = scan(spec, leaf)
                with Image.open(image.path) as opened:
                    opened.convert("L").resize((800, round(800 * opened.height / opened.width))).save(
                        PAGES / f"{image.sha256}-{leaf}.png"
                    )
    (folder / "rollcalls.md").write_text("\n".join(report))
    if frames:
        pl.concat(frames).write_parquet(folder / "rollcalls.parquet")
    print("\n".join(report))


def roll_call(text: str, caption: str) -> tuple[list[str], dict[str, list[int]]]:
    start = text.index(caption)
    table = text[start : text.index("\n|}", start)]
    rows = table.split("\n|-\n")
    candidates = [
        re.search(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]\s*$", line)
        for line in rows[0].split("\n")[2:]
        if line != "!Candidates"
    ]
    labels = [match.group(1) if match else "Abstain" for match in candidates]
    votes: dict[str, list[int]] = {}
    for row in rows[1:]:
        place = re.search(r"\{\{flag(?:country)?\|([^}]*)\}\}|\[\[(Democrats Abroad)\]\]", row)
        if place is None:
            continue
        code = ABROAD if place.group(2) else PLACES[place.group(1)]
        cells = re.sub(
            r"\{\{refn\|.*?\|group=[^}]*\}\}|\{\{efn\|[^}]*\}\}|<ref[^>]*/>|<ref[^>]*>.*?</ref>",
            "",
            row,
            flags=re.DOTALL,
        ).split("\n|")[1:]
        if len(cells) != len(labels):
            raise ValueError(f"{code}: {len(cells)} cells for {len(labels)} columns")
        values = []
        for cell in cells:
            recorded = cell.split("|")[-1].split("<br>")[0].strip()
            values.append(0 if recorded in {"·", "–", ""} else int(recorded.replace(",", "")))
        votes[code] = values
    return labels, votes


def listed_roll_call(text: str, marker: str) -> tuple[list[str], dict[str, list[int]]]:
    start = text.index(marker)
    rows = text[start : text.index("\n|}", start)].split("\n|-\n")
    header = [
        line.split("]]")[-1].split("<br />")[-1].strip("! ").strip()
        for line in rows[0].split("\n")
        if line.startswith("!")
    ][1:]
    held = header[0] == "Delegates"
    labels = [*header[held:], "Abstain"] if held else header
    votes: dict[str, list[int]] = {}
    for row in rows[1:]:
        place = re.search(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]", row.split("\n|")[0])
        if place is None:
            continue
        code = PLACES.get(place.group(1)) or LISTED_PLACES[place.group(1)]
        cells = []
        for cell in re.sub(r"\{\{efn\|[^}]*\}\}", "", row).split("\n|")[1:]:
            recorded = re.sub(r"[',\s]", "", cell)
            cells.append(int(recorded) if recorded else 0)
        if len(cells) != len(header):
            raise ValueError(f"{code}: {len(cells)} cells for {len(header)} columns")
        if held:
            if cells[0] < sum(cells[1:]):
                raise ValueError(f"{code}: {sum(cells[1:])} votes cast of {cells[0]} held")
            cells = [*cells[1:], cells[0] - sum(cells[1:])]
        votes[code] = cells
    return labels, votes


def emit() -> Path:
    from PIL import Image

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The roll call of the states at the Republican national conventions from 1856 to 1988, the Democratic conventions from 1832 to 1956 and 2008, with the Democratic vice-presidential ballots of 1832, 1835, 1848, 1852, 1856, 1860, 1864, 1872, and 1876 and the Republican ones of 1856, 1860, 1864, 1868, 1880, 1884, and 1888, and the 2012, 2016, 2020, and 2024 conventions",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        nation = cs.area("us.nation", "US")
        units = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        system = cs.key("us.electoral_system", "national-convention:majority-of-delegates")
        if system in cs.minted:
            cs.add(
                "electoral_system",
                id=system,
                polity_id=us,
                rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
                rules_version=1,
            )
            name(system, "Majority of the convention's delegates, with repeated ballots")
        values = []
        for convention in CONVENTIONS:
            key, party, title = convention["key"], convention["party"], convention["title"]
            root = conn.execute(
                """select c.id, c.election_id, c.office_id, c.party_scope_id
                   from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchone()
            if root is None:
                raise ValueError(f"{key} is not held")
            _, umbrella, office, scope = root
            people = dict(
                conn.execute(
                    """select split_part(n.text, ' ', -1), coalesce(m.into_id, cy.person_id)
                       from contest c join contest_option o on o.contest_id = c.id
                       join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                       left join entity_merge m on m.entity_id = cy.person_id
                       join name n on n.entity_id = coalesce(m.into_id, cy.person_id) and n.is_preferred
                       where c.allocation_parent_id = %s
                       group by 1, 2 order by count(*)""",
                    [root[0]],
                ).fetchall()
            )
            captions = convention.get("captions")
            if captions:
                artifacts = [raw.cached(SOURCE, url) for url in captions]
                rows = pl.read_csv(Path(__file__).with_name(convention["tsv"]), separator="\t")
                labels = [f"Donald {convention['nominee']}", "Abstain"]
                roll = {row["state"]: [row["votes"], 0] for row in rows.iter_rows(named=True)}
            else:
                artifact = raw.cached(SOURCE, page(title))
                parsed = json.loads(artifact.path.read_text())["parse"]
                labels, roll = (
                    listed_roll_call(parsed["wikitext"], convention["marker"])
                    if "marker" in convention
                    else roll_call(parsed["wikitext"], convention["table"])
                )
            expected = set(units) - {ABROAD} | ({ABROAD} if ABROAD in roll else set())
            if set(roll) != expected:
                raise ValueError(f"{title}: delegations {sorted(expected ^ set(roll))} do not match the states")
            if ABROAD in roll:
                units[ABROAD] = cs.unit("us.state", ABROAD)
                if units[ABROAD] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[ABROAD],
                        polity_id=us,
                        scheme_code="us.state",
                        code=ABROAD,
                        area_relation="unlocated",
                        kind="abroad_mission",
                    )
                    name(units[ABROAD], "Democrats Abroad")
            if captions:
                source_title = (
                    "CSPAN Republican National Convention Nomination Vote in Charlotte, NC (caption transcript)"
                )
                source = cs.source("C-SPAN", source_title)
                cs.add(
                    "source",
                    id=source,
                    kind="news_agency",
                    publisher="C-SPAN",
                    title=source_title,
                    url=captions[0],
                    license=NEWS_LICENSE,
                )
                for artifact in artifacts:
                    cs.artifact(artifact, source)
            else:
                source = cs.source("Wikipedia", f"{title}, revision {parsed['revid']}")
                cs.add(
                    "source",
                    id=source,
                    kind="community",
                    publisher="Wikipedia",
                    title=f"{title}, revision {parsed['revid']}",
                    url=f"https://en.wikipedia.org/w/index.php?title={title.replace(' ', '_')}&oldid={parsed['revid']}",
                    license="CC BY-SA 4.0",
                )
                cs.artifact(artifact, source)
            first, last = convention["days"]
            election = cs.key("us.election", f"{first}:{party}-national-convention")
            cs.add(
                "election",
                id=election,
                polity_id=us,
                parent_id=umbrella,
                kind="indirect",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(election, title)
            path = f"us/{first}-{party}-national-convention"
            cs.add("slug", path=path, entity_id=election)
            previous = None
            for number, day in enumerate(convention["ballots"], 1):
                contest = cs.key("us.contest", f"{key}:convention:{number}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    party_scope_id=scope,
                    district_area_id=nation,
                    magnitude=1,
                    round_number=number,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    rules_override={"required": convention["required"]},
                    default_result_kind="counted",
                )
                name(contest, f"Presidential nomination, ballot {number}")
                cs.add("slug", path=f"{path}/ballot-{number}", entity_id=contest)
                previous = contest
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:convention:{number}")
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
                    as_of=datetime.combine(day, datetime.min.time(), UTC),
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=(CAPTIONS_METHOD if captions else METHOD)
                    + f" The convention met in {convention['city']} and voted on {day:%B %-d, %Y}; "
                    f"{convention['required']:,} votes, a majority of the delegates, were needed to nominate.",
                )
                cs.add("note", entity_id=result_set, kind="caveat", body_md=convention["caveat"])
                ballot_options = {}
                for label in labels[:-1]:
                    surname = label.split()[-1]
                    option = cs.key("us.option", f"{key}:convention:{number}:{surname.lower()}")
                    ballot_options[label] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", f"{party}-party"),
                        ballot_label=label,
                    )
                    if surname not in people:
                        raise ValueError(f"{label} has no candidacy in the {key} state contests")
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:convention:{number}:{surname.lower()}:head"),
                        person_id=people[surname],
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                    )
                for postal, row in roll.items():
                    unit = str(units[postal])
                    values.append((str(result_set), unit, None, DELEGATION, sum(row)))
                    for label, value in zip(labels[:-1], row, strict=False):
                        if value:
                            values.append((str(result_set), unit, str(ballot_options[label]), VOTES, value))
                if number == len(convention["ballots"]):
                    winner = next(label for label in labels if label.endswith(convention["nominee"]))
                    cs.add("outcome", result_set_id=result_set, option_id=ballot_options[winner], kind="won")
        rolls = pl.read_parquet(DATA / "parsed" / SOURCE / "rollcalls.parquet")

        def unit(code: str) -> UUID:
            if code not in units and code in UNPLACED:
                units[code] = cs.unit("us.state", code)
                if units[code] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=us,
                        scheme_code="us.state",
                        code=code,
                        area_relation="unlocated",
                        kind="standard",
                    )
                    name(units[code], UNPLACED[code])
            return units[code]

        systems = {
            "majority": system,
            "two-thirds": cs.key("us.electoral_system", "national-convention:two-thirds-of-delegates"),
        }
        if systems["two-thirds"] in cs.minted:
            cs.add(
                "electoral_system",
                id=systems["two-thirds"],
                polity_id=us,
                rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 2 / 3, "strict": False}]},
                rules_version=1,
            )
            name(systems["two-thirds"], "Two-thirds of the convention's delegates, with repeated ballots")
        people = dict(
            conn.execute(
                """select x.value, coalesce(m.into_id, x.entity_id) from external_id x
                   left join entity_merge m on m.entity_id = x.entity_id
                   where x.scheme = 'wikidata' and x.value = any(%s)""",
                [[qid for _, qid in CANDIDATES.values() if qid]],
            ).fetchall()
        )
        added = set()
        for convention in PROCEEDINGS:
            first, last = convention["days"]
            year = first.year
            chains = [
                (office, chain)
                for office, chain in (("president", convention), ("vice_president", convention.get("vice")))
                if chain and chain["tables"]
            ]
            every = [
                next(spec for spec in TABLES if spec["table"] == ballot)
                for _, chain in chains
                for ballot in chain["tables"].values()
            ]
            party = convention.get("party", "republican")
            scope = cs.key("us.party", f"{party}-party")
            first_spec = every[0]
            source_url = (
                first_spec["url"]
                if "manual" in first_spec
                else first_spec["release"]
                if "release" in first_spec
                else first_spec["pdf"]
                if "pdf" in first_spec
                else f"https://books.google.com/books?id={first_spec['gbooks']}"
                if "gbooks" in first_spec
                else f"https://babel.hathitrust.org/cgi/pt?id={first_spec['htid']}"
                if "htid" in first_spec
                else f"https://exploreuk.uky.edu/catalog/{first_spec['uky']}"
                if "uky" in first_spec
                else f"https://archive.org/details/{first_spec['item']}"
            )
            title = (
                convention["title"]
                if "title" in convention
                else f"Official Report of the Proceedings of the {convention['ordinal']} Republican National Convention"
            )
            publisher = convention.get("publisher", "Republican National Committee")
            source = cs.source(publisher, title)
            if source not in added:
                added.add(source)
                cs.add(
                    "source",
                    id=source,
                    kind=convention.get("kind", "official_body"),
                    publisher=publisher,
                    title=title,
                    url=source_url,
                    license=NEWS_LICENSE if convention.get("kind") == "news_agency" else "Public record",
                )
            pages = {}
            for spec in every:
                for leaf in spec["leaves"]:
                    if leaf in pages:
                        continue
                    image = scan(spec, leaf)
                    artifact = cs.artifact(image, source)
                    pages[leaf] = cs.page(artifact, leaf)
                    render = PAGES / f"{image.sha256}-{leaf}.png"
                    with Image.open(render) as opened:
                        cs.add(
                            "source_page",
                            id=pages[leaf],
                            artifact_id=artifact,
                            page_number=leaf,
                            image_key=f"pages/{SOURCE}/{render.name}",
                            width=opened.width,
                            height=opened.height,
                        )
            election = cs.key("us.election", f"{first}:{party}-national-convention")
            umbrella = None
            if "umbrella" in convention:
                found = conn.execute(
                    """select c.election_id from external_id x join contest c on c.id = x.entity_id
                       where x.scheme = 'us.contest' and x.value = %s""",
                    [convention["umbrella"]],
                ).fetchone()
                if found is None:
                    raise ValueError(f"{convention['umbrella']} is not held")
                umbrella = found[0]
            cs.add(
                "election",
                id=election,
                polity_id=us,
                parent_id=umbrella,
                kind="indirect",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(election, f"{year} {party.title()} National Convention")
            path = f"us/{first}-{party}-national-convention"
            cs.add("slug", path=path, entity_id=election)
            for office, chain in chains:
                specs = {
                    n: next(spec for spec in TABLES if spec["table"] == ballot) for n, ballot in chain["tables"].items()
                }
                held = None
                if "held" in chain:
                    full = rolls.filter(pl.col("table") == chain["tables"][chain["held"]])
                    full = full.filter(pl.col("column") == "votes") if "votes" in set(full["column"]) else full
                    held = dict(full.group_by("state").agg(pl.col("value").sum()).iter_rows())
                    for ballot in chain["tables"].values():
                        cast = (
                            rolls.filter((pl.col("table") == ballot) & (pl.col("column") != "votes"))
                            .group_by("state")
                            .agg(pl.col("value").sum())
                        )
                        for state, value in cast.iter_rows():
                            if value > held.get(state, 0) and state not in MISPRINTS.get(ballot, ("", {}))[1]:
                                raise ValueError(f"{ballot}: {state} cast {value:g}, more than {held.get(state, 0):g}")
                loaded = rolls.filter(pl.col("table").is_in(list(chain["tables"].values())))
                seated = (
                    loaded.filter(pl.col("column") == "votes").group_by("table").agg(pl.col("value").sum())["value"]
                )
                rule = "two-thirds" if party == "democratic" and year in TWO_THIRDS else "majority"
                previous = None
                for number, spec in specs.items():
                    day = chain["ballots"][number]
                    cells = rolls.filter(pl.col("table") == spec["table"])
                    if cells.is_empty():
                        raise ValueError(f"{spec['table']} did not pass its checks")
                    own = "votes" in set(cells["column"])
                    key = f"{first}:{office.replace('_', '-')}:{party}:convention:{number}"
                    contest = cs.key("us.contest", key)
                    if party == "democratic" and len(seated):
                        total = seated.max()
                    elif held is not None:
                        total = sum(held.values())
                    elif own:
                        total = cells.filter(pl.col("column") == "votes")["value"].sum()
                    else:
                        total = None
                    if total is not None and not isinstance(total, int | float):
                        raise TypeError(f"the total {total!r} of {key} is not a number")
                    if "required" in chain:
                        required = chain["required"]
                        if isinstance(required, dict):
                            required = required[number]
                    elif total is None:
                        required = None
                    elif rule == "two-thirds":
                        required = -(-int(total) * 2 // 3)
                    else:
                        required = int(total) // 2 + 1
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office",
                        office_id=cs.key("us.office", office),
                        party_scope_id=scope,
                        district_area_id=nation,
                        magnitude=1,
                        round_number=number,
                        previous_round_id=previous,
                        electoral_system_id=systems[rule],
                        rules_override=None if required is None else {"required": required},
                        default_result_kind="counted",
                    )
                    previous = contest
                    name(
                        contest,
                        f"{'Presidential' if office == 'president' else 'Vice-presidential'} nomination, ballot {number}",
                    )
                    cs.add(
                        "slug",
                        path=f"{path}{'' if office == 'president' else '/vice-president'}/ballot-{number}",
                        entity_id=contest,
                    )
                    result_set = cs.key(f"{SOURCE}.result_set", key)
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
                        as_of=datetime.combine(day, datetime.min.time(), UTC),
                    )
                    method = chain.get("method") or OFFICIAL.format(REPORTS[party])
                    if own or held is not None:
                        method += HELD_TOTALS
                    if held is not None and not own:
                        deciding = (
                            "the ballot"
                            if len(chain["ballots"]) == 1
                            else f"the {('first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth')[chain['held'] - 1]} ballot"
                        )
                        method += HELD.format(deciding)
                    method += (
                        f" The convention met in {chain.get('cities', {}).get(number, convention['city'])} and "
                        + (
                            f"took this ballot in its session of {day:%B %-d, %Y}"
                            if party == "democratic"
                            else f"voted on {day:%B %-d, %Y}"
                        )
                    )
                    share = "two-thirds" if rule == "two-thirds" else "a majority"
                    method += (
                        "."
                        if required is None
                        else f"; {required:,} votes, {share} of the delegates, were needed to nominate."
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=method)
                    if "caveat" in chain:
                        caveat = chain["caveat"]
                        if isinstance(caveat, dict):
                            caveat = caveat.get(number)
                        if caveat is not None:
                            cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
                    if spec["table"] in MISPRINTS:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=MISPRINTS[spec["table"]][0])
                    if "first" in chain:
                        _, printed, _ = table(next(s for s in TABLES if s["table"] == chain["first"]), "low")
                        ranked = sorted(((v, k) for k, v in printed.items() if k != "votes" and v), reverse=True)
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=FIRST_1968.format(
                                ", ".join(f"{k} {v:,.0f}" for v, k in ranked[:-1])
                                + f", and {ranked[-1][1]} {ranked[-1][0]:,.0f}"
                            ),
                        )
                    totals = {
                        column: total
                        for column, total in cells.group_by("column").agg(pl.col("value").sum()).iter_rows()
                        if column not in ABSTAINED and total
                    }
                    ballot_options, made = {}, {}
                    for column in totals:
                        label, qid = (
                            CANDIDATES.get(f"{year}:{party}:{column}")
                            or CANDIDATES.get(f"{year}:{column}")
                            or CANDIDATES.get(f"{party}:{column}")
                            or CANDIDATES[column]
                        )
                        slug = re.sub(r"[^a-z]+", "-", label.lower()).strip("-")
                        if slug in made:
                            ballot_options[column] = made[slug]
                            continue
                        if qid is None or qid not in people:
                            person = cs.key("us.person", slug)
                            if person in cs.minted and person not in added:
                                added.add(person)
                                cs.add("person", id=person)
                                name(person, label)
                            if qid is not None and person not in added:
                                added.add(person)
                                cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                        else:
                            person = people[qid]
                        option = ballot_options[column] = made[slug] = cs.key("us.option", f"{key}:{slug}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=scope,
                            ballot_label=label,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{slug}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            is_independent=False,
                        )
                    if held is not None and not own:
                        for state, value in held.items():
                            values.append((str(result_set), str(unit(state)), None, DELEGATION, value))
                    for cell in cells.iter_rows(named=True):
                        if cell["column"] == "votes":
                            option, measure = None, DELEGATION
                        elif cell["column"] in ballot_options and cell["value"]:
                            option, measure = str(ballot_options[cell["column"]]), VOTES
                        else:
                            continue
                        where = unit(cell["state"])
                        values.append((str(result_set), str(where), option, measure, cell["value"]))
                        x0, y0, x1, y1 = cell["box"]
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=where,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            page_id=pages[cell["leaf"]],
                            source_id=None,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )
                    if number == chain["deciding"]:
                        winner = max(totals, key=lambda column: totals[column])
                        if (
                            rule == "two-thirds"
                            and required is not None
                            and totals[winner] < required
                            and not (chain.get("acclamation") or chain.get("shifted"))
                        ):
                            raise ValueError(f"{spec['table']}: {winner} has {totals[winner]:g} of {required} needed")
                        cs.add("outcome", result_set_id=result_set, option_id=ballot_options[winner], kind="won")
        cs.frame(
            "result_value",
            pl.DataFrame(
                [
                    {
                        "result_set_id": rs,
                        "unit_id": unit,
                        "option_id": option,
                        "channel": 0,
                        "measure": measure,
                        "count_round": 0,
                        "value": round(value, 6),
                        "flags": 0,
                    }
                    for rs, unit, option, measure, value in values
                ],
                schema_overrides={
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "value": pl.Float64,
                    "flags": pl.Int16,
                },
            ),
        )
        return cs.write()
