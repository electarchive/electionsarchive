import difflib
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import foreign_preferred
from ea.sources.gb import leh
from ea.sources.gb import london_boroughs as lb
from ea.store import DATA, connect

SOURCE = "gb.london_early"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOWNLOAD = lb.DOWNLOAD
PUBLISHER = "Greater London Council"
INDEPENDENT = {"Independent", "Electrician", "Labourer", "Theatrical Agent", "Retired Garage Proprietor"}
METHODOLOGY = (
    "Read from the text layer of the Greater London Council's scanned report, with a second reading by optical "
    "character recognition of every column and the page images checked where the two differ. The report lists each "
    "ward's candidates in vote order and prints the elected in bold. Every party's London-wide candidates and votes "
    "add up to the report's table of candidates and votes by party, each borough's elected councillors by party "
    "match its table of the state of the parties in the new councils, and each ward's electorate comes from its "
    "summary of results by wards, whose ward figures add up to each borough's printed total. Party labels marked in "
    "the report as Labour and Co-operative or as local associations are stored under the party code the report "
    "counts them in. Candidate names are printed as surname and forenames or initials."
)
NOTES = {
    1974: "The report prints no vote figure for D. J. Harvey, elected for the Conservatives in Chamberlayne ward, "
    "Brent, so his candidacy and election are stored without a vote count; the party table's Conservative total "
    "exceeds the sum of the printed Conservative votes by 971, which includes his. The Childs Hill ward votes in "
    "Barnet are those revised after an examination of the ballot papers ordered by the County Court on 9 May 1974, "
    "after which A. Young was declared elected in place of Mrs M. T. Catterson. The report's summary by wards gives "
    "the Liberals 5.1 percent in Ravensbury ward, Merton, where the printed votes give 5.7 percent; the votes are "
    "stored as printed.",
    1978: "Both readings of the report agree on every candidate's votes, and each ward's party shares match the "
    "report's summary by wards, yet the party table's totals differ from the candidates' sums by 153 votes for "
    "Labour (short), 121 for the Conservatives (over), and 38 for the Liberals (short); the candidates' figures "
    "are stored as printed. The report prints Richard W. Gregory twice in South Defoe ward with 367 votes; the "
    "party table and the ward's shares count him once, and so does this record.",
}
POSTPONED = (
    "The poll in this ward was countermanded after the death of a candidate and held later, as the report notes."
)
BOOKS: dict[int, dict] = {
    1974: {
        "day": date(1974, 5, 2),
        "url": f"{DOWNLOAD}/f6b33f95-fca4-4f94-a15e-f0ffc946ed47/LBCE_1974-5-2.pdf",
        "candidates": (12, 71),
        "wards": (72, 105),
        "figures": {
            (72, "1Hi,294"): "119,294",
            (76, "197288"): "197,288",
            (76, ".8,371"): "8,371",
            (80, "G~EENWICH"): "GREENWICH",
            (82, "3,7.68"): "3,768",
            (13, "2,P5"): "2,175",
            (13, ".1,482"): "1,482",
            (18, "1409"): "1,409",
            (26, "1;733"): "1,733",
            (26, "1,7.1~"): "1,716",
            (27, "1,29~"): "1,295",
            (28, "719-"): "719",
            (38, "2,085>"): "2,085",
            (39, '"Lit'): "964",
            (39, "1,6n"): "1,677",
            (40, ",zt,722"): "2,722",
            (45, "7n"): "777",
            (51, ",1,707"): "1,707",
            (56, "1,1n"): "1,177",
            (61, "-319"): "319",
            (62, "·194"): "194",
            (66, "1,9n"): "1,977",
            (68, ",698"): "1,698",
            (32, "195"): "105",
            (40, "~60"): "560",
            (30, "/"): None,
            (62, "\"'-.."): None,
        },
        "missing": {17: [("Cohen,J.", ("Harvey, D. J.", "Con", None, True))]},
        "residual": {"Con": -971},
        "initials": True,
        "names": {
            "East F inchley": "East Finchley",
            "Mill Hill J": "Mill Hill",
            "Woodhouse )": "Woodhouse",
            "Cllrlton": "Carlton",
            "Ward CAMDEN-conti nued Holborn": "Holborn",
            "St. John's J": "St. John's",
            "West End J": "West End",
            "Add iscornbe": "Addiscombe",
            "Bensharn Manor": "Bensham Manor",
            "Waddon ]": "Waddon",
            "Woodcote & Coulsdon West J": "Woodcote & Coulsdon West",
            "Ward EALI NG-conti nued Perivale": "Perivale",
            "Bush H ill South": "Bush Hill South",
            "Town Hall ]": "Town Hall",
            "Harrow -on-the-H ill and Greenhill": "Harrow-on-the-Hill and Greenhill",
            "Stanmore South J": "Stanmore South",
            "I Wealdstone South": "Wealdstone South",
            "Colham - Cowley": "Colham-Cowley",
            "South Ruisl ip": "South Ruislip",
            "j St. Charles": "St. Charles",
            "- Ferndale": "Ferndale",
            "tCrofton Park": "Crofton Park",
            "Manor Lee J": "Manor Lee",
            "South end": "Southend",
            "Wimbledon West J": "Wimbledon West",
            "West Ham j": "West Ham",
            "Whitton J": "Whitton",
            "Brick layers": "Bricklayers",
            "Rotherhithe J": "Rotherhithe",
            "Carshalton St. Hel ier South": "Carshalton St. Helier South",
            "Wa II ington Sou th": "Wallington South",
            "L:ann Hall": "Cann Hall",
            ") Ley ton": "Leyton",
            "r Balham": "Balham",
            "Earlsf ield": "Earlsfield",
            "~utney": "Putney",
            "Shaftesbu ry": "Shaftesbury",
            ") West Hill": "West Hill",
            "Longbridge )": "Longbridge",
            "Village J": "Village",
            "Coiehill": "Colehill",
            "Sui ivan": "Sulivan",
            'Baker Street "-': "Baker Street",
            "Regent's Park J": "Regent's Park",
            "Westbourne J": "Westbourne",
        },
        "columns": ["Con", "Lab", "Lib", "Other"],
        "merged": {
            code: "Other"
            for code in (
                "Comm",
                "NF",
                "SLAG",
                "R",
                "RA",
                "SWAG",
                "RR",
                "Ind R",
                "Ind Lab",
                "ENP",
                "SCO",
                "SLGLF",
                "TC",
                "Ind Con",
                "NIPRA",
                "P Con",
                "CAG",
                "CP",
                "EW",
                "FS",
                "IDF",
                "Ind Lib",
                "NCA",
                "NP",
                "P & AA",
                "PR",
                "PPP",
                "S & T",
                "Ind",
            )
        },
        "state": {
            "Barking": {"Lab": 45, "Other": 4, "Total": 49},
            "Barnet": {"Con": 42, "Lab": 17, "Other": 1, "Total": 60},
            "Bexley": {"Con": 37, "Lab": 22, "Total": 59},
            "Brent": {"Con": 25, "Lab": 35, "Total": 60},
            "Bromley": {"Con": 44, "Lab": 14, "Lib": 2, "Total": 60},
            "Camden": {"Con": 12, "Lab": 48, "Total": 60},
            "Croydon": {"Con": 40, "Lab": 17, "Other": 3, "Total": 60},
            "Ealing": {"Con": 24, "Lab": 36, "Total": 60},
            "Enfield": {"Con": 30, "Lab": 29, "Lib": 1, "Total": 60},
            "Greenwich": {"Con": 8, "Lab": 52, "Total": 60},
            "Hackney": {"Lab": 60, "Total": 60},
            "Hammersmith": {"Con": 10, "Lab": 48, "Lib": 2, "Total": 60},
            "Haringey": {"Con": 19, "Lab": 40, "Other": 1, "Total": 60},
            "Harrow": {"Con": 39, "Lab": 14, "Other": 3, "Total": 56},
            "Havering": {"Con": 20, "Lab": 26, "Other": 9, "Total": 55},
            "Hillingdon": {"Con": 28, "Lab": 32, "Total": 60},
            "Hounslow": {"Con": 23, "Lab": 37, "Total": 60},
            "Islington": {"Lab": 60, "Total": 60},
            "Kensington and Chelsea": {"Con": 45, "Lab": 16, "Total": 61},
            "Kingston upon Thames": {"Con": 39, "Lab": 15, "Lib": 6, "Total": 60},
            "Lambeth": {"Con": 14, "Lab": 46, "Total": 60},
            "Lewisham": {"Con": 11, "Lab": 49, "Total": 60},
            "Merton": {"Con": 29, "Lab": 22, "Other": 3, "Total": 54},
            "Newham": {"Lab": 51, "Other": 9, "Total": 60},
            "Redbridge": {"Con": 45, "Lab": 15, "Total": 60},
            "Richmond upon Thames": {"Con": 36, "Lab": 8, "Lib": 10, "Total": 54},
            "Southwark": {"Con": 4, "Lab": 56, "Total": 60},
            "Sutton": {"Con": 28, "Lab": 13, "Lib": 6, "Other": 4, "Total": 51},
            "Tower Hamlets": {"Lab": 60, "Total": 60},
            "Waltham Forest": {"Con": 12, "Lab": 36, "Total": 48},
            "Wandsworth": {"Con": 12, "Lab": 48, "Total": 60},
            "Westminster, City of": {"Con": 37, "Lab": 23, "Total": 60},
        },
        "parties": {
            "Lab": ("Labour", 1866, 2244017),
            "Con": ("Conservative", 1714, 2184594),
            "Lib": ("Liberal", 1272, 640831),
            "Comm": ("Communist", 125, 17378),
            "NF": ("National Front", 77, 14549),
            "SLAG": ("Save London Action Group", 72, 6226),
            "R": ("Ratepayers Associations", 44, 45288),
            "RA": ("Residents Associations", 37, 35030),
            "SWAG": ("Save Westminster Action Group", 17, 1715),
            "RR": ("Ratepayers and Residents Associations", 13, 14009),
            "Ind R": ("Independent Ratepayers", 7, 8279),
            "Ind Lab": ("Independent Labour", 4, 1721),
            "ENP": ("English National Party", 3, 147),
            "SCO": ("Socialist Current Organisation", 3, 156),
            "SLGLF": ("South London Gay Liberation Front", 3, 201),
            "TC": ("Thamesfield Communities", 3, 946),
            "Ind Con": ("Independent Conservative", 2, 1263),
            "NIPRA": ("National Independence Party and Residents Association", 2, 1806),
            "P Con": ("Powellite Conservative", 2, 115),
            "CAG": ("Community Action Group in Ealing", 1, 269),
            "CP": ("Community Participation", 1, 304),
            "EW": ("An English Westminster", 1, 55),
            "FS": ("Free Socialist", 1, 122),
            "IDF": ("Independent Democratic Front", 1, 1305),
            "Ind Lib": ("Independent Liberal", 1, 526),
            "NCA": ("Neighbourhood Community Action", 1, 320),
            "NP": ("Non-Political", 1, 790),
            "P & AA": ("People and Agrarian Association", 1, 964),
            "PR": ("People's Representative", 1, 560),
            "PPP": ("Positive Progress Party", 1, 15),
            "S & T": ("Squatters and Tenants", 1, 152),
            "Ind": ("Independent", 25, 5971),
        },
    },
    1978: {
        "day": date(1978, 5, 4),
        "url": f"{DOWNLOAD}/7ff41bee-72bd-4a3e-8cc4-6dca65383247/LBCE_1978-5-4.pdf",
        "candidates": (16, 82),
        "wards": (83, 100),
        "columns": ["Con", "Lab", "Lib", "Res", "Ind Res", "R", "Ind R", "RR", "GCH", "Ind Con", "Ind"],
        "merged": {"Ind RR": "Ind R"},
        "votes": {
            (39, "Kahn, Peter J."): 770,
            (39, "Andrews, Michael J."): 1773,
            (59, "Phipp, Yvonne P."): 1777,
            (69, "Cooper, Geoffrey M."): 2076,
        },
        "elected": {(39, "Gregory, Richard W."): False},
        "repeated": {(39, "Gregory, Richard W.")},
        "residual": {"Lab": -153, "Con": 121, "Lib": -38},
        "names": {
            "Norburyt": "Norbury",
            '"",don"': "Selsdon",
            "Amos": "Arnos",
            "Spri ngfield": "Springfield",
            "Gooshayst": "Gooshays",
            "Col ham": "Colham",
            "Canon bury East": "Canonbury East",
            "f:iiII rise": "Hillrise",
            "~ Gipsy Hill": "Gipsy Hill",
            "Vassal I": "Vassall",
            "Forest Hill* ,": "Forest Hill",
            "Wandie Valley": "Wandle Valley",
            "Wry the Green": "Wrythe Green",
            "Northcote )": "Northcote",
            "Sui ivan": "Sulivan",
            "l Little Venice": "Little Venice",
            "Oueen's Park j": "Queen's Park",
        },
        "missing": {
            20: [("Penton, Roy", ("Kemp, Brian A.", "Lab", 1721, True))],
            26: [("Kelly, Jacqueline", ("Roy, Nirmal C.", "Lab", 1133, False))],
            46: [("Lewis, Joan P.", ("Hammond, Brenda M.", "Ind Res", 440, False))],
            63: [("Lucas, Edward", ("Jones, Denis H.", "Lab", 719, False))],
            75: [("Walker, Edwin", ("Orwell, Beatrice", "Lab", 1063, True))],
        },
        "postponed": {("Croydon", "Norbury"): date(1978, 6, 15), ("Havering", "Gooshays"): date(1978, 5, 25)},
        "state": {
            "Barking": {"Con": 3, "Lab": 42, "Ind R": 3, "Total": 48},
            "Barnet": {"Con": 49, "Lab": 10, "Ind Res": 1, "Total": 60},
            "Bexley": {"Con": 43, "Lab": 18, "Ind Con": 1, "Total": 62},
            "Brent": {"Con": 27, "Lab": 39, "Total": 66},
            "Bromley": {"Con": 48, "Lab": 12, "Total": 60},
            "Camden": {"Con": 26, "Lab": 33, "Total": 59},
            "Croydon": {"Con": 56, "Lab": 11, "RR": 3, "Total": 70},
            "Ealing": {"Con": 41, "Lab": 28, "Ind": 1, "Total": 70},
            "Enfield": {"Con": 41, "Lab": 19, "Total": 60},
            "Greenwich": {"Con": 17, "Lab": 45, "Total": 62},
            "Hackney": {"Con": 1, "Lab": 59, "Total": 60},
            "Hammersmith": {"Con": 24, "Lab": 24, "Lib": 2, "Total": 50},
            "Haringey": {"Con": 17, "Lab": 42, "Total": 59},
            "Harrow": {"Con": 46, "Lab": 12, "Lib": 1, "R": 4, "Total": 63},
            "Havering": {"Con": 38, "Lab": 12, "Res": 9, "Ind R": 4, "Total": 63},
            "Hillingdon": {"Con": 55, "Lab": 14, "Total": 69},
            "Hounslow": {"Con": 24, "Lab": 36, "Total": 60},
            "Islington": {"Con": 2, "Lab": 50, "Total": 52},
            "Kensington and Chelsea": {"Con": 39, "Lab": 13, "GCH": 2, "Total": 54},
            "Kingston upon Thames": {"Con": 44, "Lab": 6, "Total": 50},
            "Lambeth": {"Con": 22, "Lab": 42, "Total": 64},
            "Lewisham": {"Con": 23, "Lab": 44, "Total": 67},
            "Merton": {"Con": 39, "Lab": 15, "Res": 3, "Total": 57},
            "Newham": {"Lab": 57, "R": 3, "Total": 60},
            "Redbridge": {"Con": 50, "Lab": 13, "Total": 63},
            "Richmond upon Thames": {"Con": 34, "Lib": 18, "Total": 52},
            "Southwark": {"Con": 8, "Lab": 56, "Total": 64},
            "Sutton": {"Con": 47, "Lab": 7, "Lib": 2, "Total": 56},
            "Tower Hamlets": {"Lab": 43, "Lib": 7, "Total": 50},
            "Waltham Forest": {"Con": 21, "Lab": 36, "Total": 57},
            "Wandsworth": {"Con": 36, "Lab": 25, "Total": 61},
            "Westminster, City of": {"Con": 39, "Lab": 19, "Ind Res": 2, "Total": 60},
        },
        "parties": {
            "Lab": ("Labour", 1908, 2111872),
            "Con": ("Conservative", 1829, 2642649),
            "Lib": ("Liberal", 992, 343168),
            "NF": ("National Front", 602, 90740),
            "SLA": ("Save London Alliance", 98, 8075),
            "Comm": ("Communist", 97, 9019),
            "R": ("Ratepayers Associations", 66, 39826),
            "WRP": ("Workers Revolutionary Party", 16, 1309),
            "Ind Res": ("Independent Residents Associations", 15, 10566),
            "Res": ("Residents Associations", 13, 19835),
            "SU": ("Socialist Unity", 12, 1731),
            "WIB": ("West Indian Block", 9, 470),
            "WMB": ("We Mean Business", 9, 2013),
            "Ind R": ("Independent Ratepayers Associations", 8, 7515),
            "NETCAT": ("North East Tottenham Campaign Against Traffic", 7, 2625),
            "ELPA": ("East London People's Alliance", 5, 1151),
            "Ind Con": ("Independent Conservative", 5, 5023),
            "RR": ("Ratepayers and Residents Associations", 5, 8289),
            "DNP": ("Democratic National Party", 4, 332),
            "Ind RR": ("Independent Ratepayers and Residents", 4, 6675),
            "Ind RT": ("Independent Ratepayers and Tenants Associations", 4, 842),
            "BUP": ("British United Party", 3, 267),
            "CP": ("Community Participation", 3, 815),
            "DH": ("Decent Housing For All", 2, 197),
            "GCH": ("Golborne Community Hundred", 2, 1439),
            "Ind Lab": ("Independent Labour", 2, 938),
            "LRRAG": ("Limehouse and Ratcliffe Road Action Group", 2, 295),
            "NAACP": ("National Association for the Advancement of Coloured People", 2, 90),
            "AHP": ("Against Higher Prices", 1, 181),
            "ASCDF": ("Anti-Senior Citizens Discrimination Force", 1, 44),
            "CS": ("Christian Socialist", 1, 77),
            "D": ("Democratic", 1, 386),
            "D Lab": ("Democratic Labour", 1, 214),
            "E": ("Electrician", 1, 135),
            "Ecol": ("Ecology", 1, 43),
            "F": ("Fellowship", 1, 329),
            "Ind Lib": ("Independent Liberal", 1, 98),
            "L": ("Labourer", 1, 257),
            "NPP": ("Non-Party Political", 1, 250),
            "Res T": ("Residents and Tenants", 1, 373),
            "RGP": ("Retired Garage Proprietor", 1, 58),
            "SC": ("Senior Citizens", 1, 216),
            "SCP": ("St. Christopher's Party (Anti Abortion)", 1, 30),
            "SW": ("Socialist Worker", 1, 325),
            "TA": ("Theatrical Agent", 1, 169),
            "WTU": ("Women's Truth and Unity", 1, 171),
            "YCL": ("Young Communist League", 1, 78),
            "Ind": ("Independent", 20, 8688),
        },
    },
}
BOROUGHS = {
    **{name: code for name, code in lb.BOROUGHS.items()},
    "Barking": "E09000002",
    "Hammersmith": "E09000013",
    "Westminster, City of": "E09000033",
}
CODES = {
    "ResT": "Res T",
    "N.F": "NF",
    "VCL": "YCL",
    "Can": "Con",
    "Con.": "Con",
    "lab": "Lab",
    "Lab.": "Lab",
    "lib": "Lib",
    "YCl": "YCL",
    "Ind r": "Ind R",
    "S&T": "S & T",
    "CGn": "Con",
    "COil": "Con",
    "(on": "Con",
    "sca": "SCO",
    "SLAG/GC": "SLAG",
    "R*": "R",
    "RA*": "RA",
    "RA'\"": "RA",
    "Lib.-": "Lib",
}
TOKENS = {"Can": "Con", "Can.": "Con", "lnd": "Ind", "lnd.": "Ind", "Ind.": "Ind", "IndR": "Ind R"}
Word = tuple[float, float, float, float, str]


def fold(text: str) -> str:
    return lb.fold(text)


def borough_of(text: str) -> str | None:
    text = re.sub(r"[\s.·-]*(continued|contined|contd)\s*$", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*\(Royal\s*Borough\)", "", text, flags=re.IGNORECASE)
    key = fold(text)
    for name in BOROUGHS:
        if fold(name) == key:
            return name
    return None


def columns(words: list[Word], width: float) -> list[list[Word]]:
    split = min(
        range(int(width * 0.38), int(width * 0.62), 2),
        key=lambda x: (sum(w[0] <= x <= w[2] for w in words), abs(x - width / 2)),
    )
    return [[w for w in words if w[0] < split], [w for w in words if w[0] >= split]]


def candidate(line: list[Word], codes: set[str]) -> tuple[str, str, int | None] | None:
    if len(line) > 4 and [w[4] for w in line[-2:]] == ["r", line[-1][4]] and re.fullmatch(r"\d", line[-3][4]):
        line = [*line[:-3], (*line[-3][:4], f"{line[-3][4]},{line[-1][4]}")]
    found = read(line, codes)
    while found is None and len(line) > 3 and re.fullmatch(r"[.,;:'\"*r-]+|\.\d", line[-2][4]):
        line = [*line[:-2], line[-1]]
        found = read(line, codes)
    return found


def read(line: list[Word], codes: set[str]) -> tuple[str, str, int | None] | None:
    texts = [w[4] for w in line]
    if len(texts) < 3:
        return None
    if re.fullmatch(r"(?i)unopposed|uncontested|un-", texts[-1]):
        votes = None
    else:
        votes = lb.number(texts[-1].replace("'", "").replace("~", ""))
        if votes is None:
            return None
    rest = [TOKENS.get(t, t) for t in texts[:-1]]
    for size in (3, 2, 1):
        if len(rest) <= size:
            continue
        code = " ".join(rest[-size:])
        name = " ".join(rest[:-size])
        if size == 1 and code not in codes and re.fullmatch(r".*\.[A-Z][a-z]+", code):
            head, _, code = code.rpartition(".")
            name = f"{name} {head}."
        for attempt in (code, re.sub(r"[*·•]+$", "", code), re.sub(r"(t|tt|\*|\*\*|·)$", "", code)):
            attempt = CODES.get(attempt, attempt)
            if attempt in codes:
                return name, attempt, votes
    return None


def table2(year: int, sizes: list[tuple[float, float]], book: list[list[Word]]) -> list[dict]:
    spec = BOOKS[year]
    codes = set(spec["parties"])
    first, last = spec["candidates"]
    out: list[dict] = []
    borough: str | None = None
    ward: dict | None = None
    for number in range(first, last + 1):
        width = sizes[number - 1][0]
        for index, column in enumerate(columns(book[number - 1], width)):
            rows = lb.lines(column)
            left = min((row[0][0] for row in rows), default=0)
            for row in rows:
                text = " ".join(w[4] for w in row)
                found = candidate(row, codes)
                if found:
                    if ward is None:
                        raise ValueError(f"{year} page {number}: candidate before a ward: {text}")
                    name, code, votes = found
                    for before, line in spec.get("missing", {}).get(number, []):
                        if name.startswith(before):
                            lower = min(w[1] for w in row)
                            ward["lines"].append(
                                {
                                    "name": line[0],
                                    "party": line[1],
                                    "votes": line[2],
                                    "elected": line[3],
                                    "page": number,
                                    "bbox": [min(w[0] for w in row), lower - 12, max(w[2] for w in row), lower - 3],
                                    "width": width,
                                    "height": sizes[number - 1][1],
                                }
                            )
                    votes = spec.get("votes", {}).get((number, name), votes)
                    if (number, name) in spec.get("repeated", set()) and any(
                        line["name"] == name and line["votes"] == votes for line in ward["lines"]
                    ):
                        continue
                    box = [
                        min(w[0] for w in row),
                        min(w[1] for w in row),
                        max(w[2] for w in row),
                        max(w[3] for w in row),
                    ]
                    ward["lines"].append(
                        {
                            "name": name,
                            "party": code,
                            "votes": votes,
                            "elected": spec.get("elected", {}).get((number, name), row[0][0] - left < 6),
                            "page": number,
                            "bbox": box,
                            "width": width,
                            "height": sizes[number - 1][1],
                        }
                    )
                    continue
                if len(row) >= 3 and lb.number(row[-1][4]) is not None:
                    print(f"UNPARSED {year} p{number}: {text}")
                named = borough_of(text)
                if named:
                    borough = named
                    left = row[0][0]
                    continue
                head = re.sub(r"\s*-\s*c\s*o\s*[nm]\s*\w*\s*$", "", text, flags=re.IGNORECASE)
                cont = head != text
                head = head.split(",")[0] if "," in head else re.split(r"\.\s+(?=[A-Z][a-z])", head)[0]
                head = head.strip(" ,.*")
                letters = re.sub(r"[^A-Za-z]", "", head)
                first = re.sub(r"[^A-Za-z]", "", head.split()[0]) if head.split() else ""
                if len(letters) >= 3 and len(first) >= 2 and sum(c.isupper() for c in first) / len(first) > 0.6:
                    if borough is None:
                        continue
                    left = row[0][0]
                    if cont and ward is not None:
                        continue
                    ward = {"borough": borough, "printed": head, "page": number, "column": index, "lines": []}
                    out.append(ward)
    return out


def table3(year: int, book: list[list[Word]]) -> dict[str, list[tuple]]:
    first, last = BOOKS[year]["wards"]
    found: dict[str, list[tuple]] = {}
    borough: str | None = None
    for number in range(first, last + 1):
        page = book[number - 1]
        heads = [w[0] for w in page if re.fullmatch(r"(?i)cons?tituency|parliamentary", w[4])]
        cut = min(heads) - 4 if heads else 140
        marks = sorted(((w[0] + w[2]) / 2, w[4]) for w in page if re.fullmatch(r"\([1-9]\)|\(f\$\)|\(6\)'?", w[4]))
        shares_x = [x for x, _ in marks[-4:]]
        pending = ""
        rows_ = [knit(row) for row in lb.lines(page)]
        for index, row in enumerate(rows_):
            text = " ".join(w[4] for w in row)
            named = borough_of(text)
            if named and re.match(r"[A-Z]{2}", text):
                borough = named
                found.setdefault(borough, [])
                pending = ""
                continue
            if borough is None:
                continue
            figures = [w for w in row if re.fullmatch(r"\d{1,3},\d{3}", w[4])]
            name = " ".join(w[4] for w in row if w[0] < cut and not re.search(r"\d", w[4])).strip(" *}")
            rows = found[borough]
            if not figures:
                after = rows_[index + 1] if index + 1 < len(rows_) else []
                ahead = " ".join(w[4] for w in after if w[0] < cut and not re.search(r"\d", w[4]))
                before = rows_[index - 1] if index else []
                nearer = bool(after and before) and after[0][3] - row[0][3] < row[0][3] - before[0][3] - 1
                indent = bool(after) and after[0][0] - row[0][0] > 6
                joins = (
                    re.search(r"\b(and|&)$", name) or ahead[:1].islower() or not ahead.strip(" *}") or nearer or indent
                )
                if (
                    name
                    and not any("%" in w[4] for w in row)
                    and rows
                    and not pending
                    and not joins
                    and rows[-1][2] == number
                    and fold(rows[-1][0]) != "total"
                ):
                    rows[-1] = (f"{rows[-1][0]} {name}", *rows[-1][1:])
                    continue
                if name:
                    pending = f"{pending} {name}".strip()
                continue
            name, pending = f"{pending} {name}".strip(), ""
            if not name:
                continue
            electorate = figures[0]
            share: dict[str, float] = {}
            for w in row:
                if w[0] > electorate[2] and re.fullmatch(r"\d{1,3}\.\d", w[4]):
                    x = (w[0] + w[2]) / 2
                    if len(shares_x) == 4 and x > shares_x[0] - 25:
                        column = min(range(4), key=lambda i: abs(shares_x[i] - x))
                        share[("Con", "Lab", "Lib", "Others")[column]] = float(w[4])
            rows.append((name, int(electorate[4].replace(",", "")), number, list(electorate[:4]), share))
    return found


def knit(row: list[Word]) -> list[Word]:
    out: list[Word] = []
    for word in row:
        if (
            out
            and re.fullmatch(r"\d{1,3}", out[-1][4])
            and re.fullmatch(r",\d{3}", word[4])
            and word[0] - out[-1][2] < 4
        ):
            last = out.pop()
            word = (last[0], min(last[1], word[1]), word[2], max(last[3], word[3]), last[4] + word[4])
        out.append(word)
    return out


def match(heads: list[str], listed: list[str]) -> list[int]:
    pairs = sorted(
        (
            (difflib.SequenceMatcher(None, fold(h), fold(t)).ratio(), i, j)
            for i, h in enumerate(heads)
            for j, t in enumerate(listed)
        ),
        reverse=True,
    )
    chosen: dict[int, int] = {}
    used: set[int] = set()
    for ratio, i, j in pairs:
        if i in chosen or j in used:
            continue
        if ratio < 0.5 and len(heads) - len(chosen) > 1:
            continue
        chosen[i] = j
        used.add(j)
    if len(chosen) != len(heads):
        raise ValueError(f"unmatched wards: {[h for i, h in enumerate(heads) if i not in chosen]}")
    return [chosen[i] for i in range(len(heads))]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    wards, candidates = [], []
    for year, spec in BOOKS.items():
        sizes, book = lb.pages(raw.cached(lb.SOURCE, spec["url"]).path)
        fixes = spec.get("figures", {})
        book = [
            [(*w[:4], fixes.get((number, w[4]), w[4])) for w in page if fixes.get((number, w[4]), "") is not None]
            for number, page in enumerate(book, 1)
        ]
        heads = table2(year, sizes, book)
        stats = table3(year, book)
        for borough, rows in stats.items():
            total = [r for r in rows if fold(r[0]) == "total"]
            listed = [r for r in rows if fold(r[0]) != "total"]
            if len(total) != 1 or sum(r[1] for r in listed) != total[0][1]:
                raise ValueError(f"{year} {borough}: electorate {sum(r[1] for r in listed)} against total {total}")
            stats[borough] = listed
        printed = spec["parties"]
        seen: dict[str, list[int]] = {}
        elected: dict[str, dict[str, int]] = {}
        for borough, code in BOROUGHS.items():
            mine = [h for h in heads if h["borough"] == borough]
            if not mine:
                continue
            listed = stats[borough]
            if len(mine) != len(listed):
                raise ValueError(f"{year} {borough}: {len(mine)} wards in the results, {len(listed)} in the summary")
            order = match([h["printed"] for h in mine], [r[0] for r in listed])
            won = elected.setdefault(borough, {})
            for number, (head, index) in enumerate(zip(mine, order, strict=True)):
                row = listed[index]
                name = spec.get("names", {}).get(row[0], row[0])
                lines_ = head["lines"]
                votes = [line["votes"] for line in lines_]
                seats = sum(line["elected"] for line in lines_)
                tacit = all(v is None for v in votes)
                if seats == 0 or len(lines_) < seats:
                    print(f"{year} {borough} {name}: {seats} elected of {len(lines_)}")
                if not tacit:
                    votes = [v for v in votes if v is not None]
                    ranked = sorted(votes, reverse=True)
                    if votes != ranked:
                        print(f"{year} {borough} {name}: votes out of order {votes}")
                    if [line["elected"] for line in lines_] != [i < seats for i in range(len(lines_))]:
                        print(f"{year} {borough} {name}: elected not at the top")
                    if len(votes) > seats and votes[seats - 1] == votes[seats]:
                        print(f"{year} {borough} {name}: tie for the last seat {votes}")
                if not tacit:
                    total = sum(votes)
                    mine_ = {"Con": 0, "Lab": 0, "Lib": 0, "Others": 0}
                    for line in lines_:
                        key = line["party"] if line["party"] in ("Con", "Lab", "Lib") else "Others"
                        mine_[key] += line["votes"] or 0
                    off = {
                        k: (round(100 * v / total, 1), row[4].get(k, 0.0))
                        for k, v in mine_.items()
                        if abs(100 * v / total - row[4].get(k, 0.0)) > 0.11
                    }
                    if off:
                        print(f"{year} {borough} {name} p{head['page']}: shares {off}")
                wards.append(
                    {
                        "year": year,
                        "day": spec.get("postponed", {}).get((borough, name), spec["day"]),
                        "borough": borough,
                        "la_code": code,
                        "number": number + 1,
                        "ward": name,
                        "printed": head["printed"],
                        "seats": seats,
                        "tacit": tacit,
                        "electorate": row[1],
                        "page": head["page"],
                        "electorate_page": row[2],
                        "electorate_box": row[3],
                    }
                )
                for rank, line in enumerate(lines_):
                    tally = seen.setdefault(line["party"], [0, 0])
                    tally[0] += 1
                    tally[1] += line["votes"] or 0
                    won[line["party"]] = won.get(line["party"], 0) + line["elected"]
                    candidates.append(
                        {
                            "year": year,
                            "la_code": code,
                            "ward": name,
                            "number": rank + 1,
                            "name": initials(line["name"]) if spec.get("initials") else line["name"],
                            "party": line["party"],
                            "votes": line["votes"],
                            "elected": line["elected"],
                            "page": line["page"],
                            "bbox": line["bbox"],
                        }
                    )
        problems = [
            f"{code}: {seen.get(code)} against {figures[1:]}"
            for code, figures in printed.items()
            if tuple(seen.get(code, [0, 0])) != (figures[1], figures[2] + spec.get("residual", {}).get(code, 0))
        ]
        problems += [f"unknown party {code}" for code in seen if code not in printed]
        for borough, won in elected.items():
            table = spec["state"][borough]
            if sum(won.values()) != table["Total"]:
                problems.append(f"{borough}: {sum(won.values())} elected, printed {table['Total']}")
            for party_, column in spec["merged"].items():
                won[column] = won.get(column, 0) + won.pop(party_, 0)
            for column in spec["columns"]:
                if won.get(column, 0) != table.get(column, 0):
                    problems.append(f"{borough} {column}: {won.get(column, 0)} elected, printed {table.get(column, 0)}")
            for party_, count in won.items():
                if count and party_ not in spec["columns"] and party_ not in spec["merged"]:
                    problems.append(f"{borough} {party_}: {count} elected, no column in the printed table")
        if problems:
            raise ValueError(f"{year}:\n" + "\n".join(problems))
    pl.DataFrame(wards).write_parquet(PARSED / "wards.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    print(f"{len(wards)} wards and {len(candidates)} candidates")


PEOPLE = {
    "Adams, Phyll is I.": "Adams, Phyllis I.",
    "Perki n, Christopher": "Perkin, Christopher",
    "Felber, Maurice B'w.": "Felber, Maurice B.W.",
    "Patel, Mahendra ku mar P.": "Patel, Mahendrakumar P.",
    "Nortcl iff, Cel ia": "Nortcliff, Celia",
    "Good, Keith S.c.": "Good, Keith S.C.",
    "Nortcl iff, Stephen": "Nortcliff, Stephen",
    "Oa kenfu II, Ch risti ne": "Oakenfull, Christine",
    "Sac key , Kwamina K.": "Sackey, Kwamina K.",
    "Paynton, 01 ive": "Paynton, Olive",
    "Mall ison, Catherine": "Mallison, Catherine",
    "Wal ker, Sarah V.": "Walker, Sarah V.",
    "Austi n, Peter W.": "Austin, Peter W.",
    "Sau nders, Beryl": "Saunders, Beryl",
    'Johnson"Susan E.': "Johnson, Susan E.",
    "Inger, Catheri ne M.": "Inger, Catherine M.",
    "Barrows, Laurna C.1.": "Barrows, Laurna C.I.",
    "Du nbar, Joh n": "Dunbar, John",
    "Browne.(;layton, Robert B.": "Browne-Clayton, Robert B.",
    "Renouf, Anthony J'w.": "Renouf, Anthony J.W.",
    "Sweeney, Joh n": "Sweeney, John",
    "Wh itby, Sarah": "Whitby, Sarah",
    "Butler, Lesl ie": "Butler, Leslie",
    "Taylor, Walter R.w.": "Taylor, Walter R.W.",
    "Col borne Hill. George": "Colborne Hill, George",
    "Trott. Leonard J.• J.P.": "Trott, Leonard J., J.P.",
    "Hoepel man, Jack": "Hoepelman, Jack",
    "Brewi ngton, Susan R.": "Brewington, Susan R.",
    "Ox ley, Mary R.": "Oxley, Mary R.",
    "Howarth.Guy B.": "Howarth, Guy B.",
    "Ing. Ernest L., J.P.": "Ing, Ernest L., J.P.",
    "Faul kner, John": "Faulkner, John",
    "Daniell. Eric A .• J.P.": "Daniell, Eric A., J.P.",
    "Lyde, Nicholas F.w.": "Lyde, Nicholas F.W.",
    "Bartrum-Garrett Robin J.": "Bartrum-Garrett, Robin J.",
    "Tilley, ChristineJ.": "Tilley, Christine J.",
    "Lane, PeterW.": "Lane, Peter W.",
    "Ell iott, Douglas R.": "Elliott, Douglas R.",
    "'Hewish, Roger J.L.": "Hewish, Roger J.L.",
    "Henry, Frederi ck": "Henry, Frederick",
    "Mel ntyre, Jacquel ine A.": "McIntyre, Jacqueline A.",
    "G1asspool, Peter J.": "Glasspool, Peter J.",
    "Garfield, Julia C.1.": "Garfield, Julia C.I.",
    "Fletcher, Frederic k": "Fletcher, Frederick",
    "Eldridge Gerard": "Eldridge, Gerard",
    "Dickerson Alfred T.": "Dickerson, Alfred T.",
    "Fleetwood, Kaye J:": "Fleetwood, Kaye J.",
    "Richardson..Paul K.": "Richardson, Paul K.",
    "Skinner, SlIsan": "Skinner, Susan",
    "lake, Sidney J.": "Lake, Sidney J.",
    "0' Kelly, Fergus": "O'Kelly, Fergus",
    "lewis, Ruth E.": "Lewis, Ruth E.",
    "Bourne, C<'lrole S.": "Bourne, Carole S.",
    "Kal ia, Madan": "Kalia, Madan",
    "Wil kie, Ruth": "Wilkie, Ruth",
    "0' Keeffe, Kevin": "O'Keeffe, Kevin",
    "Gri bble, John J.": "Gribble, John J.",
    "Firmin, Mary T.e.": "Firmin, Mary T.C.",
    "Gil bert, Brian": "Gilbert, Brian",
    "Too till , David A.": "Tootill, David A.",
    "Wil kins, Brian C.": "Wilkins, Brian C.",
    "Wil kins, Maureen B.": "Wilkins, Maureen B.",
    "Andrews, David l.": "Andrews, David L.",
    "Wither by, Alan J.": "Witherby, Alan J.",
    "Mallam, Dennis T.l.": "Mallam, Dennis T.L.",
    "Den by, Maeve": "Denby, Maeve",
    "Carroll, Moi ra": "Carroll, Moira",
    "lng, Frank W.": "Ing, Frank W.",
    "Claxton, Angela F.1.": "Claxton, Angela F.I.",
    "Walker-8mith, John J.": "Walker-Smith, John J.",
    "II iffe, Dr. Stephen": "Iliffe, Dr. Stephen",
    "Chall is, Margaret R.": "Challis, Margaret R.",
    "Peel Jonathan H.M.": "Peel, Jonathan H.M.",
    "ZaHar, Mahmood A": "Zaffar, Mahmood A.",
    "SomerviUe, Joyce M.": "Somerville, Joyce M.",
    "Cohen, J.oseph": "Cohen, Joseph",
    "McCormack, B. ~.": "McCormack, B. E.",
    "Bert.ie, E. B.": "Bertie, E. B.",
    "Sharp, ~rs N.G.": "Sharp, Mrs N.G.",
    "Richa rds, P.": "Richards, P.",
    "lIIe-Smith, R.": "Ille-Smith, R.",
    "WobeY,J·": "Wobey, J.",
    "DaIY,J·": "Daly, J.",
    "KirbY,J·": "Kirby, J.",
    "RileY,J·": "Riley, J.",
    "Cer.vantes-Watson, N. J.": "Cervantes-Watson, N. J.",
    "Sinclair, Mrs Lt. G.": "Sinclair, Mrs L. G.",
    "Ajustron, Mi~s M. P.": "Ajustron, Miss M. P.",
    "Hart!.-D. W.": "Hart, D. W.",
    "Alton,-JI B.": "Alton, J. B.",
    "Salisbu ry, K. E.": "Salisbury, K. E.",
    "D' Aubreys, E. P": "D'Aubreys, E. P.",
    "Bay ton, G. E.": "Bayton, G. E.",
    "f-lawthorne, E.": "Hawthorne, E.",
    "Head, MisS' P. A.": "Head, Miss P. A.",
    "Derick, C. a. L.": "Derick, C. O. L.",
    "Pel ling, Mrs A. L.": "Pelling, Mrs A. L.",
    "McK.<lY, J.": "McKay, J.",
    'Lewis,"M rs V. E.': "Lewis, Mrs V. E.",
    "WaY,·G.W.": "Way, G.W.",
    "G urrin, A. C.": "Gurrin, A. C.",
    "Pepper,Mrsl.J.": "Pepper, Mrs I.J.",
    "lng, E. L.": "Ing, E. L.",
    "Tibbie, F. G.": "Tibble, F. G.",
    "TaylGr, B.": "Taylor, B.",
    "Audlev, Miss M.": "Audley, Miss M.",
    "Hamifin, T. J.": "Hanafin, T. J.",
    "Dearie, P. F.": "Dearle, P. F.",
    "Davi~, J. F.": "David, J. F.",
    "Khu~er, Mrs S.": "Khuner, Mrs S.",
    "Ro~hford, F.": "Rochford, F.",
    "Dames, D.": "Darnes, D.",
    "GleDdinning, J. A. C.": "Glendinning, J. A. C.",
    "Sautter, A.": "Soutter, A.",
    "Buddie, A. E.": "Buddle, A. E.",
    "Dflvies, R. W.": "Davies, R. W.",
    "StpWell, F. O.": "Stowell, F. O.",
    "Kingwelr, S. R. W.": "Kingwell, S. R. W.",
    "M~lls, G. F.": "Mills, G. F.",
    "Bard, H.": "Bord, H.",
    "Bard, B. D.": "Bord, B. D.",
    "Sioam, B. B.": "Sloam, B. B.",
    "Blackbume, W. A.": "Blackburne, W. A.",
    "Donna, Mrs E. M.": "Donno, Mrs E. M.",
    "Donna, W. C.": "Donno, W. C.",
    "Fay, Miss M.": "Foy, Miss M.",
    "Thorn, E.": "Thom, E.",
    "Pakenharri, T. F. D.": "Pakenham, T. F. D.",
    "Hal, Mrs A. A.": "Hol, Mrs A. A.",
    "Tarkington, Mrs E. I.": "Torkington, Mrs E. I.",
    "Tarkington, N.": "Torkington, N.",
    "GGldberg, G. H.": "Goldberg, G. H.",
    "Whitmare, A. H.": "Whitmore, A. H.",
    "Tad, E. D. M.": "Tod, E. D. M.",
    "Navarro, Miss V. A.": "Novarro, Miss V. A.",
    "Scanla'n, A. F. G.": "Scanlan, A. F. G.",
    "FridaY, J. R.": "Friday, J. R.",
    "PendsaY, P. G.": "Pendsay, P. G.",
    "powell, G. D. K.": "Powell, G. D. K.",
    "IIlsley, J. A.": "Illsley, J. A.",
    "Kal'ile, P. B.": "Kane, P. B.",
    "KeIlY, D.": "Kelly, D.",
    "Clifford P, A. H.": "Clifford, P. A. H.",
}
TITLES = {"Mrs", "Miss", "Dr", "Sir", "Lady", "The", "Major", "Col.", "St.", "de", "la"}


def initials(text: str) -> str:
    text = PEOPLE.get(text, text)
    if "," not in text and ";" not in text:
        text = re.sub(r"^([^.]+?)\.\s*", r"\1, ", text, count=1)
    surname, _, rest = re.sub(r"\s*[,;]\s*", ", ", text.strip(" .'"), count=1).partition(", ")
    surname = re.sub(r"^l(?=[a-z])", "L", surname)
    rest = re.sub(r"\b(Mrs|Miss)\.?(?=[A-Zl1])", r"\1 ", rest)
    out = []
    for token in rest.replace("Mrs.", "Mrs").split():
        if token in TITLES:
            out.append(token)
            continue
        for char in token:
            char = {"l": "L", "1": "I", "0": "O", "o": "O"}.get(char, char)
            if char.isupper():
                out.append(f"{char}.")
    text = f"{surname}, {' '.join(out)}" if out else surname
    return PEOPLE.get(text, text)


UNRELATED = {"Senior Citizens"}


def person(text: str) -> tuple[str, str]:
    text = PEOPLE.get(text, text)
    text = re.sub(r"\s+", " ", text.replace("~", "").replace("•", ",")).strip(" ").lstrip(".")
    if "," not in text:
        text = re.sub(r"^([^.]+?)\.\s+", r"\1, ", text, count=1)
    text = re.sub(r"\s*,\s*", ", ", re.sub(r"[.,]\s*J\.P$", ", J.P.", text))
    surname, _, rest = text.partition(", ")
    rest = re.sub(r"\b([AE])I (?=[a-z])", r"\1l", rest)
    rest = re.sub(r"(?<=[A-Za-z]) (?!(?:de|van|von|la|le|du|da)\b)(?=[a-z])", "", rest)
    rest = re.sub(r"\bl(?=(?:ain|s|r|m)[a-z]*\b)", "I", rest)
    rest = re.sub(r"\b(?<!de )l(?=[a-z])", "L", rest)
    text = f"{surname}, {rest}" if rest else surname
    return text, f"{rest.split(', ')[0]} {surname}".strip()


def emit() -> Path:
    wards = pl.read_parquet(PARSED / "wards.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="London borough council elections "
            + ", ".join(map(str, sorted(BOOKS)))
            + " by ward, from the Greater London Council's reports",
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

        def party(label: str) -> UUID | None:
            if label in INDEPENDENT:
                return None
            if label not in party_ids:
                found = (
                    cs.key(*lb.GLC_RESOLVED[label])
                    if label in lb.GLC_RESOLVED
                    else None
                    if label in UNRELATED
                    else known.get(leh.fold(label))
                )
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted and found not in party_ids.values():
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                party_ids[label] = found
            return party_ids[label]

        for year, spec in BOOKS.items():
            day = spec["day"]
            title = f"London Borough Council Elections {day.day} May {year}"
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="compendium",
                    publisher=PUBLISHER,
                    title=title,
                    url=lb.DATASET,
                    license="Published by the Greater London Council; the Greater London Authority lists it on the "
                    "London Datastore with no licence stated",
                    license_url=None,
                    attribution=f"{PUBLISHER}, {title}.",
                )
            artifact = raw.cached(lb.SOURCE, spec["url"])
            artifact_id = cs.artifact(artifact, source)
            page_ids: dict[int, UUID] = {}

            def page(number: int) -> UUID:
                if number not in page_ids:
                    image = lb.PAGES / f"{artifact.sha256}-{number}.png"
                    if not image.exists():
                        lb.PAGES.mkdir(parents=True, exist_ok=True)
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
                        image_key=f"pages/{lb.SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                return page_ids[number]

            def box(number: int, bbox: list[float]) -> list[float]:
                width, height = sizes[number - 1]
                return [bbox[0] / width, bbox[1] / height, bbox[2] / width, bbox[3] / height]

            sizes, _ = lb.pages(artifact.path)
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
                cs.add("note", entity_id=election, kind="methodology", body_md=METHODOLOGY)
                if year in NOTES:
                    cs.add("note", entity_id=election, kind="context", body_md=NOTES[year])
                for ward in held.filter(pl.col("la_code") == code).iter_rows(named=True):
                    display = ward["ward"]
                    poll = ward["day"]
                    home = election
                    if poll != day:
                        home = cs.key("gb.election", f"{poll}:local:{code}:{slug(display)}")
                        cs.add(
                            "election",
                            id=home,
                            polity_id=polity,
                            parent_id=election,
                            kind="local",
                            date_from=poll,
                            date_to=poll,
                            status="held",
                        )
                        name(home, f"{year} {council} election, postponed poll in {display}")
                        cs.add("slug", path=f"{path}/{poll}-local", entity_id=home)
                        cs.add("note", entity_id=home, kind="context", body_md=POSTPONED)
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
                    contest_key = f"{poll}:local:{ward_key}"
                    contest = cs.key("gb.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=home,
                        kind="body_seats",
                        body_id=body,
                        district_area_id=ward_area,
                        magnitude=seats,
                        round_number=1,
                        electoral_system_id=systems["fptp" if seats == 1 else "block"],
                        default_result_kind="tacit" if tacit else "counted",
                    )
                    name(contest, f"{display} ward, {council}, {year}")
                    cs.add("slug", path=f"{path}/{poll}-local/{slug(display)}", entity_id=contest)
                    result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=home,
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
                        locator={"page": ward["page"], "borough": ward["borough"], "ward": ward["printed"]},
                    )

                    def value(option: UUID | None, measure: int, figure: int, number: int, bbox: list[float]) -> None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=lb.DIGITIZED,
                            value=figure,
                        )
                        x0, y0, x1, y1 = box(number, bbox)
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

                    if not tacit and ward["electorate"] is not None:
                        value(None, lb.REGISTERED, ward["electorate"], ward["electorate_page"], ward["electorate_box"])
                    for line in lines.filter((pl.col("la_code") == code) & (pl.col("ward") == display)).iter_rows(
                        named=True
                    ):
                        option_key = f"{contest_key}:{line['number']}"
                        label = spec["parties"][line["party"]][0]
                        owner = party(label)
                        ballot, full = person(line["name"])
                        option = cs.key(f"{SOURCE}.option", option_key)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=owner,
                            ballot_label=ballot,
                        )
                        someone = cs.key(f"{SOURCE}.person", option_key)
                        cs.add("person", id=someone)
                        name(someone, full)
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", option_key),
                            person_id=someone,
                            option_id=option,
                            role="head",
                            ballot_name=ballot,
                            member_of_party_id=owner,
                            is_independent=owner is None,
                        )
                        if line["votes"] is not None:
                            value(option, lb.VOTES, line["votes"], line["page"], line["bbox"])
                        if line["elected"]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
