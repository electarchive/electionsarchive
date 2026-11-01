import base64
import io
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from itertools import pairwise, product
from pathlib import Path
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
CONVENTIONS = [
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
]
ABROAD = "DA"
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
HATHI = "https://babel.hathitrust.org/cgi/pt?id={htid}&seq={leaf}"
RNC = "sim_republican-national-convention-proceedings_{}"
TABLES = [
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
]


def volume(
    item: str, year: str, offset: int, back: int, pages: dict[str, int | list[int]], party: str = "republican"
) -> list[dict[str, object]]:
    specs = []
    for name, found in pages.items():
        found = [found] if isinstance(found, int) else found
        leaves = sorted({page - offset - step for page in found for step in range(back + 1)})
        specs.append({"table": f"{year}:{party}:{name}", "item": item, "leaves": tuple(leaves)})
    return specs


def ballots(
    item: str, year: str, windows: dict[int, tuple[int, int]], scatter: bool = False, party: str = "republican"
) -> list[dict[str, object]]:
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
) -> list[dict[str, object]]:
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
ROLL_1880 = (214, 219, 223, 224, 226, 227, 229, 230, 232, 233, 235, 238, 239, 241, 242, 244, 246, 247, 251, 252, 254, 255, 257, 258, 260, 262, 264, 266, 269, 276, 279, 281, 282, 284, 286, 287, 290)  # fmt: skip
TABLES += [
    *leaves(
        "officialreportof00repuiala",
        "1920",
        {
            "b1": (227, 230),
            "b2": (232, 233),
            "b3": (235,),
            "b4": (240, 241),
            "b5": (247,),
            "b6": (248, 249),
            "b7": (252, 253),
            "b8": (259, 260),
            "b9": (262, 263),
            "b10": (269,),
        },
        scatter=True,
    ),
    *leaves("officialreportsi00repuiala", "1916", {"b1": (212,)}, scatter=True),
    *ballots("officialreportsi00repuiala", "1916", {2: (214, 216), 3: (216, 220)}, scatter=True),
    *ballots("officialreportfi00repuiala", "1912", {1: (442, 448)}),
    *ballots("officialreporto00repu", "1908", {1: (236, 242)}),
    *ballots("officialproceedi00repuiala", "1904", {1: (209, 214)}),
    *leaves("12threpubnaticon00blumrich", "1900", {"": (157,)}),
    *leaves("officialproceedi00repu", "1896", {"": (132,)}),
    *ballots("proceedingsoften00repu", "1892", {1: (144, 150)}),
    *leaves("officialproceedi00repu_0", "1888", {"b1": (376, 377), "b2": (380,), "b3": (383, 384), "b5": (397,), "b7": (410,)}),
    *ballots("officialproceedi00repu_0", "1888", {4: (394, 396), 6: (406, 408), 8: (416, 418)}),
    *leaves("proceedingsofeig00repu", "1884", {"b3": (159,)}),
    *ballots("proceedingsofeig00repu", "1884", {1: (148, 152), 2: (153, 157), 4: (165, 176)}),
    *ballots(
        "proceedingsofrep00inrepu",
        "1880",
        {
            n: (page, min(max(after, page + 1), page + 4))
            for n, (page, after) in enumerate(pairwise(ROLL_1880), start=1)
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
    },
    {
        "table": "1856:republican:formal",
        "item": "proceedingsoffir00inrepu",
        "leaves": (63,),
        "columns": (None, "Fremont", "McLean", "Seward"),
    },
    {"table": "1864:republican", "item": "proceedingsoffir00inrepu", "leaves": (236, 237, 238), "narrated": True},
    {"table": "1872:republican", "item": "presidentialele00conggoog", "leaves": (38, 39, 40, 41), "narrated": True},
    {"table": "1868:republican", "item": "presidentialelec00repu", "leaves": (97,), "columns": ("Grant",)},
]
DNC_1912 = ((203, 204, 205), (207, 208), (211, 212), (212, 213, 214), (217, 218), (219, 220), (221, 222), (222, 223, 224), (225, 226), (227, 228), (230, 231, 232), (233, 234), (237, 238), (248, 249), (250, 251), (253, 254), (257, 258), (259, 260), (260, 261, 262), (262, 263), (266, 267), (270, 271, 272), (272, 273), (276, 277), (278, 279), (283, 284), (293, 294), (297, 298), (305, 306), (308, 309), (311, 312), (312, 313, 314), (315, 316), (318, 319), (320, 321), (322, 323), (323, 324, 325), (327, 328), (330, 331), (331, 332, 333), (333, 334, 335), (336, 337), (344, 345), (348, 349), (351, 352), (358, 359))  # fmt: skip
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
    *leaves("official00demo", "1900", {"": (165, 166, 167)}, party="democratic"),
    *leaves("officialproce00de", "1896", {"b5": (346,)}, party="democratic"),
    *leaves("cu31924002684375", "1892", {"": (182,)}, party="democratic"),
    *leaves("officialproceedi0000demo_u0f5", "1884", {"b1": (266,)}, party="democratic"),
    *leaves("officialp00demo", "1880", {"b2": (139,)}, party="democratic"),
    *leaves("officialproc00demo", "1876", {"b2": (167,)}, party="democratic"),
    *leaves("officialproceed01convgoog", "1872", {"": (66,)}, party="democratic"),
    *leaves(
        "officialproceedi00demo",
        "1868",
        {f"b{n}": (leaf,) for n, leaf in enumerate(DNC_1868, start=1)},
        party="democratic",
    ),
    {
        "table": "1868:democratic:b22",
        "item": "officialproceedi00demo",
        "leaves": (153, 154, 155, 156, 157),
        "ballot": 22,
    },
    *leaves("officialproceed00demo", "1864", {"": (43,)}, party="democratic"),
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
TABLES += [
    {"table": "1904:democratic", "uky": "xt7tx921cw1r", "leaves": (259,)},
    {"table": "1920:democratic:b1", "htid": "mdp.39015030799285", "leaves": (293, 294, 295), "rotate": True},
    {"table": "1920:democratic:b44", "htid": "mdp.39015030799285", "leaves": (504, 505)},
    {"table": "1924:democratic:b1", "htid": "mdp.39015030799251", "leaves": (356, 357, 358), "rotate": True},
    {"table": "1948:democratic", "htid": "mdp.39015030799574", "leaves": tuple(range(314, 325)), "narrated": True},
    {"table": "1956:democratic", "htid": "uc1.$b619008", "leaves": (463, 464)},
]
PROCEEDINGS = [
    {"table": "1968:republican:final", "first": "1968:republican:first", "ordinal": "Twenty-Ninth", "city": "Miami Beach", "days": (date(1968, 8, 5), date(1968, 8, 8)), "ballot": date(1968, 8, 7)},
    {"table": "1972:republican", "ordinal": "Thirtieth", "city": "Miami Beach", "days": (date(1972, 8, 21), date(1972, 8, 23)), "ballot": date(1972, 8, 22)},
    {"table": "1976:republican", "ordinal": "Thirty-First", "city": "Kansas City", "days": (date(1976, 8, 16), date(1976, 8, 19)), "ballot": date(1976, 8, 18)},
    {"table": "1980:republican", "ordinal": "Thirty-Second", "city": "Detroit", "days": (date(1980, 7, 14), date(1980, 7, 17)), "ballot": date(1980, 7, 16)},
    {"table": "1984:republican", "ordinal": "Thirty-Third", "city": "Dallas", "days": (date(1984, 8, 20), date(1984, 8, 23)), "ballot": date(1984, 8, 22)},
]  # fmt: skip
CANDIDATES = {
    "Nixon": ("Richard M. Nixon", "Q9588"),
    "N. Rockefeller": ("Nelson A. Rockefeller", "Q11237"),
    "Reagan": ("Ronald Reagan", "Q9960"),
    "McCloskey": ("Paul N. McCloskey Jr.", "Q2073295"),
    "Ford": ("Gerald R. Ford", "Q9582"),
    "Richardson": ("Elliot L. Richardson", "Q719808"),
    "Anderson": ("John B. Anderson", "Q936548"),
    "Bush": ("George Bush", "Q23505"),
    "Armstrong": ("Anne Armstrong", None),
}
OFFICIAL = (
    "The roll call of the states on the presidential nomination, as tabulated in the official report of the "
    "convention's proceedings published by the Republican National Committee, read from the scanned volume page by "
    "page. Each delegation's total is the votes it held, abstentions included; abstentions are that total less the "
    "votes recorded for candidates."
)
FIRST_1968 = (
    "The proceedings print two tabulations of the one ballot. As first called, the roll gave {}. Delegations then "
    "changed their votes before the chair announced the result, and the final tabulation, stored here, is the one "
    "the convention declared."
)
READS = {"a": "low", "b": "medium"}
QUADRANTS = {
    "tl": (0.0, 0.04, 0.5, 0.525),
    "tr": (0.5, 0.04, 1.0, 0.525),
    "bl": (0.0, 0.515, 0.5, 0.98),
    "br": (0.5, 0.515, 1.0, 0.98),
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
ABBREVIATIONS = {
    "ala": "AL", "ariz": "AZ", "ark": "AR", "calif": "CA", "cal": "CA", "colo": "CO", "conn": "CT", "del": "DE",
    "dc": "DC", "fla": "FL", "ga": "GA", "ill": "IL", "ind": "IN", "kans": "KS", "kan": "KS", "ky": "KY", "la": "LA",
    "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN", "miss": "MS", "mo": "MO", "mont": "MT", "nebr": "NE",
    "neb": "NE", "nev": "NV", "nh": "NH", "nj": "NJ", "nmex": "NM", "nm": "NM", "ny": "NY", "nc": "NC", "nd": "ND",
    "ndak": "ND", "okla": "OK", "ore": "OR", "oreg": "OR", "pa": "PA", "pr": "PR", "ri": "RI", "sc": "SC",
    "sd": "SD", "sdak": "SD", "tenn": "TN", "tex": "TX", "vt": "VT", "va": "VA", "vi": "VI", "wash": "WA",
    "wva": "WV", "wis": "WI", "wisc": "WI", "wyo": "WY", "virginislands": "VI", "puertorico": "PR",
    "districtofcolumbia": "DC", "distofcolumbia": "DC", "portorico": "PR", "philippineislands": "PH",
    "philippines": "PH", "phillippines": "PH", "dakota": "DT", "indianterritory": "IT", "dalaware": "DE",
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
LABELS = {"Washbarne": "Washburne", "Mcclellan": "McClellan", "T.H.Seymour": "T. H. Seymour"}
RELABEL = {("1920:republican:b5", 247, "OR", 2): "RI"}
CORRECTIONS = {
    "1956:democratic": [
        (464, "NV", (0.78, 0.358, 0.83, 0.378), {"Symington": 0.5, "Timmerman": 0}, "the half vote is set under Symington, which the printed totals count"),
        (464, "NM", (0.78, 0.403, 0.83, 0.423), {"Symington": 0.5, "Timmerman": 0}, "the half vote is set under Symington, which the printed totals count"),
        (464, "NC", (0.78, 0.434, 0.83, 0.454), {"Symington": 0.5, "Timmerman": 0}, "the half vote is set under Symington, which the printed totals count"),
    ],
    "1928:republican": [
        (284, "MO", (0.21, 0.771, 0.58, 0.785), {"Hughes": 1}, "the footnote to the row: 'Missouri cast one vote for Charles E. Hughes'"),
    ],
    "1912:democratic:b2": [
        (207, "CT", (0.755, 0.617, 0.795, 0.631), {"Baldwin": 14}, "the row prints only the delegation of 14; the announced and printed Baldwin total of 14 has no other source"),
        (207, "IN", (0.803, 0.724, 0.843, 0.738), {"Marshall": 30}, "the row prints only the delegation of 30; the announced and printed Marshall total of 31 is Indiana's 30 and Michigan's 1"),
        (208, "OK", (0.487, 0.375, 0.643, 0.39), {"Clark": 10, "Wilson": 10, "Harmon": 0}, "10 and 10, printed one column to the right; the printed totals count them for Clark and Wilson"),
        (208, "OR", (0.545, 0.392, 0.643, 0.408), {"Wilson": 10, "Harmon": 0}, "10, printed in the Harmon column; the printed totals count it for Wilson"),
        (208, "NC", (0.545, 0.321, 0.705, 0.337), {"Wilson": 16.25, "Underwood": 7.25}, "16¼ and 7¼, which the delegation of 24 and the printed totals count"),
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
    "1884:republican:b3": [
        (159, "IA", (0.43, 0.395, 0.66, 0.406), {"Blaine": 26, "Edmunds": 0}, "printed under Edmunds; the Blaine and Edmunds totals each differ from the rows by exactly these 26 votes"),
        (159, "RI", (0.49, 0.643, 0.54, 0.654), {"Arthur": 0}, "the printed Arthur vote exceeds both the delegation's 8 and the Arthur total"),
        (159, "LA", (0.43, 0.432, 0.48, 0.443), {"not voting": 1}, "16 held, 15 recorded; 819 of 820 votes cast"),
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
    ],
    "1920:republican:b10": [
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
    "1892:democratic": [
        (182, "NC", (0.483, 0.577, 0.82, 0.589), {"Cleveland": 10 / 3, "Campbell": 0, "Morrison": 1}, "3⅓ for Cleveland and 1 in the Morrison column, which the delegation of 22 and the printed totals count"),
        (182, "MD", (0.437, 0.424, 0.481, 0.435), {"not voting": 0.5}, "16 held, 15½ recorded; 909½ of 910 votes cast"),
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
    "1948:democratic": {"Russell": 263, "McNutt": 0.5, "Truman": 947.5},
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
NAMES = {re.sub(r"[^a-z]", "", re.sub(r" \(.*\)|, U\.S\.", "", name).lower()): code for name, code in PLACES.items()}
PAGES = DATA / "pages" / SOURCE
FRACTION = re.compile(r"\s*(?:([½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])|1/2|'/2|\.5)$")


def fetch() -> None:
    for convention in CONVENTIONS:
        raw.fetch(SOURCE, page(convention["title"]), suffix=".json")
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch, scans

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for spec in TABLES:
        for leaf in spec["leaves"]:
            image = scan(spec, leaf, fetch=True)
            data = base64.b64encode(cropped(image.path, spec.get("crop"))).decode()
            for effort, scatter in product(READS.values(), (False, True)):
                if scatter and not spec.get("scatter"):
                    continue
                ask = (
                    SCATTER
                    if scatter
                    else NARRATED
                    if spec.get("narrated")
                    else ROLL
                    if spec.get("ballot") is None or spec.get("crop")
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


def scan(spec: dict[str, object], leaf: int, fetch: bool = False) -> raw.Artifact:
    if "uky" in spec:
        url = EXPLOREUK.format(item=spec["uky"], leaf=leaf)
        return raw.fetch(SOURCE, url, suffix=".jpg") if fetch else raw.cached(SOURCE, url)
    if "htid" not in spec:
        url = LEAF.format(item=spec["item"], leaf=leaf)
        return raw.fetch(SOURCE, url, suffix=".jpg") if fetch else raw.cached(SOURCE, url)
    try:
        return raw.cached(SOURCE, HATHI.format(htid=spec["htid"], leaf=leaf))
    except LookupError:
        if not fetch:
            raise
    return render(spec, leaf)


def render(spec: dict[str, object], leaf: int) -> raw.Artifact:
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


def variant(spec: dict[str, object], scatter: bool = False, leaf: int | None = None) -> str | None:
    if scatter:
        return "s"
    if spec.get("crop"):
        return f"b{spec['ballot']}{spec['crop']}"
    if spec.get("parts", {}).get(leaf, "head") != "head":
        return spec["parts"][leaf]
    if spec.get("narrated"):
        return "n"
    return None if spec.get("ballot") is None else f"b{spec['ballot']}"


def read_key(effort: str, digest: str, kind: str | None = None) -> str:
    prompt = PROMPT if kind is None else f"{PROMPT}{kind}"
    return f"anthropic-batch:claude-haiku-5-5/{prompt}/{effort}/{digest}"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def place(label: str) -> str | None:
    plain = re.sub(r"[^a-z]", "", label.lower().split("(")[0])
    return NAMES.get(plain) or ABBREVIATIONS.get(plain)


def number(cell: str) -> float:
    text = cell.strip().replace(",", "")
    if re.fullmatch(r"[\dIl]+", text):
        text = text.replace("I", "1").replace("l", "1")
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


def table(spec: dict[str, object], effort: str) -> tuple[list[dict[str, object]], dict[str, float], list[str]]:
    cells: list[dict[str, object]] = []
    totals: dict[str, float] = {}
    shifted: dict[str, float] = {}
    problems: list[str] = []
    fixed = {(code, column) for _, code, _, figures, _ in CORRECTIONS.get(spec["table"], ()) for column in figures}
    for leaf, scatter in product(spec["leaves"], (False, True)):
        if scatter and not spec.get("scatter"):
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
        if scatter:
            for column in header:
                if place(column["label"]) is None:
                    problems.append(f"leaf {leaf}: unknown state {column['label']!r}")
            for row in read["rows"]:
                if len(row["values"]) != len(header):
                    problems.append(f"leaf {leaf}: {row['label']} has {len(row['values'])} cells")
                    continue
                for column, value in zip(header, row["values"], strict=True):
                    code = place(column["label"])
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
                            "column": row["label"].strip(),
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
                if re.search(r"vot(?!ing)|delegate|whole|^\s*total\.?\s*$", column["label"], re.IGNORECASE)
                else candidate(column["label"], spec["table"]) or None
                for column in header
            )
        )
        if len(roles) != len(header):
            problems.append(f"leaf {leaf}: {len(header)} columns for {len(roles)} roles")
            continue
        rows = read["rows"]
        x0, y0, x1, y1 = QUADRANTS.get(spec.get("crop"), (0.0, 0.0, 1.0, 1.0))
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
                if role is None and code is not None and not column["label"].strip() and value.strip(" .…"):
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
    held = sum(cell["value"] for cell in cells if cell["column"] == "votes")
    if shifted and totals.get("votes", held) != held:
        totals = shifted
    totals |= TOTALS.get(spec["table"], {})
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
        for state in sorted(held):
            if (state, "votes") not in values and not any(column == "votes" for _, column in values):
                continue
            votes = values.get((state, "votes"), 0)
            cast = sum(value for (code, column), value in values.items() if code == state and column != "votes")
            if abs(votes - cast) > 1e-6:
                failures.append(f"{state}: {cast:g} cast of {votes:g} held")
        for column, total in printed.items():
            summed = sum(value for (_, name), value in values.items() if name == column)
            if abs(summed - total) > 1e-6:
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


def emit() -> Path:
    from PIL import Image

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The roll call of the states at the 1968 to 1984 Republican and the 2016 and 2020 national conventions",
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
            artifact = raw.cached(SOURCE, page(title))
            parsed = json.loads(artifact.path.read_text())["parse"]
            labels, roll = roll_call(parsed["wikitext"], convention["table"])
            expected = set(units) | ({ABROAD} if ABROAD in roll else set())
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
                    body_md=METHOD + f" The convention met in {convention['city']} and voted on {day:%B %-d, %Y}; "
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
        republican = cs.key("us.party", "republican-party")
        people = dict(
            conn.execute(
                """select x.value, coalesce(m.into_id, x.entity_id) from external_id x
                   left join entity_merge m on m.entity_id = x.entity_id
                   where x.scheme = 'wikidata' and x.value = any(%s)""",
                [[qid for _, qid in CANDIDATES.values() if qid]],
            ).fetchall()
        )
        for convention in PROCEEDINGS:
            spec = next(spec for spec in TABLES if spec["table"] == convention["table"])
            first, last = convention["days"]
            day = convention["ballot"]
            cells = rolls.filter(pl.col("table") == spec["table"])
            title = f"Official Report of the Proceedings of the {convention['ordinal']} Republican National Convention"
            source = cs.source("Republican National Committee", title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Republican National Committee",
                title=title,
                url=f"https://archive.org/details/{spec['item']}",
            )
            pages = {}
            for leaf in spec["leaves"]:
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
            election = cs.key("us.election", f"{first}:republican-national-convention")
            cs.add(
                "election",
                id=election,
                polity_id=us,
                parent_id=None,
                kind="indirect",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(election, f"{first.year} Republican National Convention")
            path = f"us/{first}-republican-national-convention"
            cs.add("slug", path=path, entity_id=election)
            key = f"{first}:president:republican:convention:1"
            contest = cs.key("us.contest", key)
            held = cells.filter(pl.col("column") == "votes")["value"].sum()
            required = int(held) // 2 + 1
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", "president"),
                party_scope_id=republican,
                district_area_id=nation,
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                rules_override={"required": required},
                default_result_kind="counted",
            )
            name(contest, "Presidential nomination, ballot 1")
            cs.add("slug", path=f"{path}/ballot-1", entity_id=contest)
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
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=OFFICIAL + f" The convention met in {convention['city']} and voted on {day:%B %-d, %Y}; "
                f"{required:,} votes, a majority of the delegates, were needed to nominate.",
            )
            if "first" in convention:
                _, printed, _ = table(next(s for s in TABLES if s["table"] == convention["first"]), "low")
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
                if column not in {"votes", "Abstention"} and total
            }
            ballot_options = {}
            for column in totals:
                label, qid = CANDIDATES[column]
                slug = re.sub(r"[^a-z]+", "-", label.lower()).strip("-")
                if qid is None:
                    person = cs.key("us.person", slug)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, label)
                else:
                    person = people[qid]
                option = ballot_options[column] = cs.key("us.option", f"{key}:{slug}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=republican,
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
            for cell in cells.iter_rows(named=True):
                if cell["column"] == "votes":
                    option, measure = None, DELEGATION
                elif cell["column"] in ballot_options and cell["value"]:
                    option, measure = str(ballot_options[cell["column"]]), VOTES
                else:
                    continue
                unit = units[cell["state"]]
                values.append((str(result_set), str(unit), option, measure, cell["value"]))
                x0, y0, x1, y1 = cell["box"]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
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
            winner = max(totals, key=lambda column: totals[column])
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
                        "value": value,
                        "flags": 0,
                    }
                    for rs, unit, option, measure, value in values
                ],
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
