import csv
import difflib
import html
import itertools
import re
import struct
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
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
TITLES = {
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
DATASET = "https://data.london.gov.uk/dataset/london-borough-council-elections-1998-24r9z"
DOWNLOAD = "https://data.london.gov.uk/download/24r9z"
BOOKS = {
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
OCR_CODES = {"LO": "LD", "ILiB": "ILIB"}
PARTIES = {
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
WARDS = {
    "Addlscombe": "Addiscombe",
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
    "Socialist Party of Great Britain": ("gb.party", "socialist-party-of-great-britain"),
    "Workers Revolutionary Party": ("gb.party", "workers-revolutionary-party"),
    "Social Democratic Party": ("gb.party", "social-democratic-party-1981"),
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


def fetch() -> None:
    for _, url in [*BOOKS.values(), *GLC.values()]:
        raw.fetch(SOURCE, url, suffix=".pdf")


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


def person(text: str) -> str:
    text = re.sub(r"\s*;\s*", ", ", text.replace("~", "")).strip()
    surname, _, rest = text.partition(",")
    words = []
    for word in rest.split():
        if re.fullmatch(r"[A-Z0-9.]+", word) and re.search(r"[A-Z.]", word):
            word = word.translate(str.maketrans({"0": "O", "5": "S"}))
        words.append(word)
    return f"{surname.strip()}, {' '.join(words)}" if words else surname.strip()


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    candidates, wards = [], []
    for year, (day, url) in BOOKS.items():
        sizes, book = pages(raw.cached(SOURCE, url).path)
        stats = statistics(book, sizes)
        found = (
            results(book, set(PARTIES[year]), sizes)
            .with_columns(name_printed=pl.col("name"))
            .with_columns(pl.col("name").map_elements(lambda n: NAMES.get(n, person(n)), return_dtype=pl.String))
        )
        printed = TOTALS[year]
        groups = found.group_by("party").agg(votes=pl.col("votes").sum(), count=pl.len())
        for code, votes, count in groups.iter_rows():
            if (votes, count) != printed[code][:2]:
                raise ValueError(f"{year} {code}: {votes} votes and {count} candidates, printed {printed[code][:2]}")
        heads = found.select("borough", "ward", "page", "column").unique(maintain_order=True)
        elected: dict[str, int] = {}
        for borough, code in BOROUGHS.items():
            mine = heads.filter(pl.col("borough") == borough)
            listed = stats.filter(pl.col("borough") == borough)
            if mine.height != listed.height:
                raise ValueError(
                    f"{year} {borough}: {mine.height} wards in the results, {listed.height} in the statistics"
                )
            for number_, (head, row) in enumerate(
                zip(mine.iter_rows(named=True), listed.iter_rows(named=True), strict=True)
            ):
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
                seats = row["seats"]
                tacit = all(v is None for v in votes)
                if not tacit:
                    if any(a < b for a, b in itertools.pairwise(votes)):
                        raise ValueError(f"{year} {borough} {name}: votes out of order {votes}")
                    if len(votes) > seats and votes[seats - 1] == votes[seats]:
                        raise ValueError(f"{year} {borough} {name}: tie for the last seat")
                    if max(votes) > row["valid"]:
                        raise ValueError(f"{year} {borough} {name}: a candidate has more votes than valid ballots")
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
        wrong = {
            code: (elected.get(code, 0), seats)
            for code, (_, _, seats) in printed.items()
            if elected.get(code, 0) != seats
        }
        if wrong:
            raise ValueError(f"{year} elected by party differs from the printed table: {wrong}")
    pl.DataFrame(wards).write_parquet(PARSED / "wards.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    print(f"{len(wards)} wards and {len(candidates)} candidates")
    parse_glc()


Word = tuple[float, float, float, float, str]


def scan(
    artifact: raw.Artifact, number: int, crop: tuple[int, int] | None = None
) -> tuple[tuple[int, int], list[list[Word]]]:
    folder = PARSED / "ocr"
    folder.mkdir(parents=True, exist_ok=True)
    tsv = folder / f"{artifact.sha256}-{number}-{crop[0] if crop else 'page'}.tsv"
    if not tsv.exists():
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page"
            window = ["-x", str(crop[0]), "-y", "0", "-W", str(crop[1] - crop[0]), "-H", "100000"] if crop else []
            subprocess.run(
                ["pdftoppm", "-r", "400", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                + window
                + [str(artifact.path), str(image)],
                check=True,
            )
            subprocess.run(
                ["tesseract", f"{image}.png", str(tsv.with_suffix("")), "--psm", "6" if crop else "4", "tsv"],
                check=True,
                capture_output=True,
            )
    rows = list(csv.DictReader(tsv.open(), delimiter="\t", quoting=csv.QUOTE_NONE))
    top = next(r for r in rows if r["level"] == "1")
    offset = crop[0] if crop else 0
    grouped: dict[tuple[str, str, str], list[Word]] = {}
    for r in rows:
        if r["level"] == "5" and r["text"].strip():
            left, upper = int(r["left"]) + offset, int(r["top"])
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
            + " by ward, and Greater London Council elections "
            + ", ".join(map(str, sorted(GLC)))
            + " by electoral division, from the London Research Centre's and the Greater London Council's reports",
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
                cs.add("note", entity_id=election, kind="methodology", body_md=METHODOLOGY)
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
            page_ids = {}

            def glc_page(number: int) -> UUID:
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
        return cs.write()
