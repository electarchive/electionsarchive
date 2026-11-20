"""Maharashtra's 2019 Lok Sabha general election by polling station, from the Form 20 final result sheets the Chief
Electoral Officer, Maharashtra posts (part of the `in.form20` source; `ea parse in.form20` builds it).

The Chief Electoral Officer's "Polling Boothwise Result (Form 20)" page (ceoelection.maharashtra.gov.in/SearchInfo/
Form20.aspx) lists, for the 2019 Lok Sabha election and each of the State's 48 constituencies, one Form 20 Part I sheet
per assembly segment, at `form20/LS2019/PC_<pc>/AC_<segment>.pdf`. The site serves its certificate without the
intermediate of its chain, so the files are fetched with GlobalSign's published intermediate added to the trusted set.

Each sheet has a row per polling station: the serial number and, on most sheets, the station's own number; the votes
each candidate polled on the voting machines in ballot order; the valid votes; the rejected votes; the votes for none
of the above (NOTA); the total; and the tendered votes. A total row closes the sheet. Most sheets were printed from a
spreadsheet and carry a text layer, read here with each word's position: the columns come from the positions of the
data rows' numbers on each page. The others are scans; they are left out here and named in each set's note.

A sheet is kept only when every station row satisfies its own arithmetic (the candidates add to the valid votes, and
the valid, rejected, and NOTA votes to the total) and every column adds to the total row the sheet prints. Postal
ballots are counted for the whole constituency and are not on the segment sheets, so the sets hold the machine votes.

No pipeline holds Maharashtra's 2019 constituency contests, so each constituency's contest, its candidates in ballot
order, their parties, and NOTA come from the Chief Electoral Officer's lists of contesting candidates for the four
phases, under the 2019 general election and the Lok Sabha that in.form20_wb holds.
"""

import functools
import itertools
import re
import ssl
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "in.form20"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
SITE = "https://ceoelection.maharashtra.gov.in/"
INDEX = SITE + "SearchInfo/Form20.aspx"
SHEETS = SITE + "form20/LS2019/"
CANDIDATE_LISTS = [SITE + f"Downloads/LS2019/Phase-{n}_Candidates_List.pdf" for n in ("I", "II", "III", "IV")]
# GlobalSign's intermediate certificate, which the site does not send; from GlobalSign's repository.
INTERMEDIATE = "http://secure.globalsign.com/cacert/gsrsaovsslca2018.crt"
ECI_STATE = "S13"  # the Commission's state code
LGD = "27"  # the Local Government Directory's state code, which in.geo's constituency codes carry
STATION_SCHEME, SEGMENT_SCHEME, PC_SCHEME = "in.ps", "in.ac", "in.pc"
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
ALL, ELECTION_DAY = 0, 1
YEAR = 2019


def _ids(pipeline: str) -> Callable[..., str]:
    return Package(pipeline, pipeline, "ids").id


_GEO = _ids("in.geo")


def _wb(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join(["in.form20_wb", kind, *(str(part) for part in parts)])))


ELECTION = _wb("election", YEAR)
BODY = _wb("body", "lok-sabha")
ELECTION_PATH = f"in/{YEAR}-general"
key = _ids(SOURCE)
SOURCE_ID = key("source", "mh-2019")
FPTP = key("electoral_system", "fptp")


def pc_area(pc: int) -> str:
    return _GEO("area", PC_SCHEME, f"{LGD}{pc:02d}", "2008")


def ac_area(ac: int) -> str:
    return _GEO("area", SEGMENT_SCHEME, f"{LGD}{ac:03d}", "2008")


# ---- the raw files -------------------------------------------------------------------------------------------------


@functools.cache
def _tls() -> ssl.SSLContext:
    """The default trust store plus the site's missing intermediate certificate."""
    der = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt").path.read_bytes()
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return context


def fetch() -> None:
    """The index page, the four phases' candidate lists, and every segment's sheet the index lists (it answers a
    form post per constituency, so its listing is kept in PCS below)."""
    urls = [INDEX, *CANDIDATE_LISTS, *(sheet_url(pc, ac) for pc, (_, _, segments) in PCS.items() for ac, _ in segments)]
    for url in urls:
        raw.fetch(SOURCE, url, suffix=Path(url).suffix, verify=_tls())


def sheet_url(pc: int, ac: int) -> str:
    return f"{SHEETS}PC_{pc:02d}/AC_{ac:03d}.pdf"


def _cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


# ---- the constituencies and their segments, as the Form 20 index lists them ----------------------------------------

# Each constituency: its name, its reservation, and its segments with their names, as the index lists them.
PCS: dict[int, tuple[str, str | None, tuple[tuple[int, str], ...]]] = {
    1: (
        "Nandurbar",
        "ST",
        (
            (1, "Akkalkuwa (ST)"),
            (2, "Shahada (ST)"),
            (3, "Nandurbar (ST)"),
            (4, "Nawapur (ST)"),
            (5, "Sakri (ST)"),
            (9, "Shirpur (ST)"),
        ),
    ),
    2: (
        "Dhule",
        None,
        (
            (6, "Dhule Rural"),
            (7, "Dhule City"),
            (8, "Sindhkheda"),
            (114, "Malegaon (Central)"),
            (115, "Malegaon (Outer)"),
            (116, "Baglan (ST)"),
        ),
    ),
    3: (
        "Jalgaon",
        None,
        (
            (13, "Jalgaon City"),
            (14, "Jalgaon Rural"),
            (15, "Amalner"),
            (16, "Erandol"),
            (17, "Chalisgaon"),
            (18, "Pachora"),
        ),
    ),
    4: (
        "Raver",
        None,
        (
            (10, "Chopda (ST)"),
            (11, "Raver"),
            (12, "Bhusawal (SC)"),
            (19, "Jamner"),
            (20, "Muktainagar"),
            (21, "Malkapur"),
        ),
    ),
    5: (
        "Buldhana",
        None,
        (
            (22, "Buldhana"),
            (23, "Chikhli"),
            (24, "Sindhkhed Raja"),
            (25, "Mehkar (SC)"),
            (26, "Khamgaon"),
            (27, "Jalgaon (Jamod)"),
        ),
    ),
    6: (
        "Akola",
        None,
        (
            (28, "Akot"),
            (29, "Balapur"),
            (30, "Aakola West"),
            (31, "Akola East"),
            (32, "Murtizapur (SC)"),
            (33, "Risod"),
        ),
    ),
    7: (
        "Amravati",
        "SC",
        (
            (37, "Badnera"),
            (38, "Amrawati"),
            (39, "Teosa"),
            (40, "Daryapur (SC)"),
            (41, "Melghat (ST)"),
            (42, "Achalpur"),
        ),
    ),
    8: (
        "Wardha",
        None,
        ((36, "Dhamangaon Railway"), (43, "Morshi"), (44, "Arvi"), (45, "Deoli"), (46, "Hinganghat"), (47, "Wardha")),
    ),
    9: (
        "Ramtek",
        "SC",
        ((48, "Katol"), (49, "Savner"), (50, "Hingna"), (51, "Umred (SC)"), (58, "Kamthi"), (59, "Ramtek")),
    ),
    10: (
        "Nagpur",
        None,
        (
            (52, "Nagpur South West"),
            (53, "Nagpur South"),
            (54, "Nagpur East"),
            (55, "Nagpur Central"),
            (56, "Nagpur West"),
            (57, "Nagpur North (SC)"),
        ),
    ),
    11: (
        "Bhandara-Gondiya",
        None,
        (
            (60, "Tumsar"),
            (61, "Bhandara (SC)"),
            (62, "Sakoli"),
            (63, "Arjuni Morgaon (SC)"),
            (64, "Tirora"),
            (65, "Gondia"),
        ),
    ),
    12: (
        "Gadchiroli-Chimur",
        "ST",
        (
            (66, "Amgaon (ST)"),
            (67, "Armori (ST)"),
            (68, "Gadchiroli (ST)"),
            (69, "Aheri (ST)"),
            (73, "Bramhapuri"),
            (74, "Chimur"),
        ),
    ),
    13: (
        "Chandrapur",
        None,
        ((70, "Rajura"), (71, "Chandrapur (SC)"), (72, "Ballarpur"), (75, "Warora"), (76, "Wani"), (80, "Arni (ST)")),
    ),
    14: (
        "Yavatmal-Washim",
        None,
        ((34, "Washim (SC)"), (35, "Karanja"), (77, "Ralegaon (ST)"), (78, "Yavatmal"), (79, "Digras"), (81, "Pusad")),
    ),
    15: (
        "Hingoli",
        None,
        ((82, "Umarkhed (ST)"), (83, "Kinwat"), (84, "Hadgaon"), (92, "Basmath"), (93, "Kalamnuri"), (94, "Hingoli")),
    ),
    16: (
        "Nanded",
        None,
        (
            (85, "Bhokar"),
            (86, "Nanded North"),
            (87, "Nanded South"),
            (89, "Naigaon"),
            (90, "Deglur (SC)"),
            (91, "Mukhed"),
        ),
    ),
    17: (
        "Parbhani",
        None,
        ((95, "Jintur"), (96, "Parbhani"), (97, "Gangakhed"), (98, "Pathri"), (99, "Partur"), (100, "Gansavangi")),
    ),
    18: (
        "Jalna",
        None,
        (
            (101, "Jalna"),
            (102, "Badnapur (SC)"),
            (103, "Bhokardan"),
            (104, "Sillod"),
            (106, "Pholambari"),
            (110, "Paithan"),
        ),
    ),
    19: (
        "Aurangabad",
        None,
        (
            (105, "Kannad"),
            (107, "Aurangabad (Central)"),
            (108, "Aurangabad (West) (SC)"),
            (109, "Aurangbad (East)"),
            (111, "Gangapur"),
            (112, "Vaijapur"),
        ),
    ),
    20: (
        "Dindori",
        "ST",
        (
            (113, "Nandgaon"),
            (117, "Kalwan (ST)"),
            (118, "Chandwad"),
            (119, "Yevla"),
            (121, "Niphad"),
            (122, "Dindori (ST)"),
        ),
    ),
    21: (
        "Nashik",
        None,
        (
            (120, "Sinnar"),
            (123, "Nashik East"),
            (124, "Nashik (Central)"),
            (125, "Nashik West"),
            (126, "Deolali (SC)"),
            (127, "Igatpuri (ST)"),
        ),
    ),
    22: (
        "Palghar",
        "ST",
        (
            (128, "Dahanu (ST)"),
            (129, "Vekramgrth (ST)"),
            (130, "Palghar (ST)"),
            (131, "Boisar (ST)"),
            (132, "Nalasopara"),
            (133, "Vasai"),
        ),
    ),
    23: (
        "Bhiwandi",
        None,
        (
            (134, "Bhiwandi Rural (ST)"),
            (135, "Shahapur (ST)"),
            (136, "Bhiwandi West"),
            (137, "Bhiwandi East"),
            (138, "Kalyan West"),
            (139, "Murbad"),
        ),
    ),
    24: (
        "Kalyan",
        None,
        (
            (140, "Ambarnath (SC)"),
            (141, "Ulhasnagar"),
            (142, "Kalyan East"),
            (143, "Dombivali"),
            (144, "Kalyan Rural"),
            (149, "Mumbra-Kalwa"),
        ),
    ),
    25: (
        "Thane",
        None,
        (
            (145, "Meera Bhayandar"),
            (146, "Ovala majiwada"),
            (147, "Kopri-Pachpakhadi"),
            (148, "Thane"),
            (150, "Airoli"),
            (151, "Belapur"),
        ),
    ),
    26: (
        "Mumbai North",
        None,
        (
            (152, "Borivali"),
            (153, "Dhaisar"),
            (154, "Magathane"),
            (160, "Kandivali East"),
            (161, "Charkop"),
            (162, "Malad West"),
        ),
    ),
    27: (
        "Mumbai North-West",
        None,
        (
            (158, "Jogeshwari East"),
            (159, "Dindoshi"),
            (163, "Goregaon"),
            (164, "Varsova"),
            (165, "Andheri West"),
            (166, "Andheri East"),
        ),
    ),
    28: (
        "Mumbai North-East",
        None,
        (
            (155, "Mulund"),
            (156, "Vikhroli"),
            (157, "Bhandup West"),
            (169, "Ghatkopar West"),
            (170, "Ghatkopar East"),
            (171, "Mankhurd shivajinagar"),
        ),
    ),
    29: (
        "Mumbai North-Central",
        None,
        (
            (167, "Vile Parle"),
            (168, "Chandivali"),
            (174, "Kurla (SC)"),
            (175, "Kalina"),
            (176, "Vandre East"),
            (177, "Vandre West"),
        ),
    ),
    30: (
        "Mumbai South-Central",
        None,
        (
            (172, "Anushakti Nagar"),
            (173, "Chembur"),
            (178, "Dharavi (SC)"),
            (179, "Sion Koliwada"),
            (180, "Wadala"),
            (181, "Mahim"),
        ),
    ),
    31: (
        "Mumbai South",
        None,
        (
            (182, "Worli"),
            (183, "Shivadi"),
            (184, "Byculla"),
            (185, "Malabar Hill"),
            (186, "Mumbadevi"),
            (187, "Colaba"),
        ),
    ),
    32: (
        "Raigad",
        None,
        ((191, "Pen"), (192, "Alibag"), (193, "Shrivardhan"), (194, "Mahad"), (263, "Dapoli"), (264, "Guhagar")),
    ),
    33: (
        "Maval",
        None,
        ((188, "Panvel"), (189, "Karjat"), (190, "Uran"), (204, "Maval"), (205, "Chinchwad"), (206, "Pimpri (SC)")),
    ),
    34: (
        "Pune",
        None,
        (
            (208, "Vadgaon Sheri"),
            (209, "Shivajinagar"),
            (210, "Kothrud"),
            (212, "Parvati"),
            (214, "Pune Cantonment (SC)"),
            (215, "Kasba Peth"),
        ),
    ),
    35: (
        "Baramati",
        None,
        ((199, "Daund"), (200, "Indapur"), (201, "Baramati"), (202, "Purandar"), (203, "Bhor"), (211, "Khadakwasala")),
    ),
    36: (
        "Shirur",
        None,
        (
            (195, "Junnar"),
            (196, "Ambegaon"),
            (197, "Khed Alandi"),
            (198, "Shirur"),
            (207, "Bhosari"),
            (213, "Hadapsar"),
        ),
    ),
    37: (
        "Ahmednagar",
        None,
        (
            (222, "Shevgaon"),
            (223, "Rahuri"),
            (224, "Parner"),
            (225, "Ahmednagar City"),
            (226, "Shrigonda"),
            (227, "Karjat Jamkhed"),
        ),
    ),
    38: (
        "Shirdi",
        "SC",
        (
            (216, "Akole (ST)"),
            (217, "Sangmner"),
            (218, "Shirdi"),
            (219, "Kopargaon"),
            (220, "Shrirampur (SC)"),
            (221, "Nevasa"),
        ),
    ),
    39: (
        "Beed",
        None,
        ((228, "Georai"), (229, "Majalgaon"), (230, "Beed"), (231, "Ashti"), (232, "Kaij (SC)"), (233, "Parli")),
    ),
    40: (
        "Osmanabad",
        None,
        ((239, "Ausa"), (240, "Omerga (SC)"), (241, "Tuljapur"), (242, "Osmanabad"), (243, "Paranda"), (246, "Barshi")),
    ),
    41: (
        "Latur",
        "SC",
        (
            (88, "Loha"),
            (234, "Latur Rural"),
            (235, "Latur City"),
            (236, "Ahmedpur"),
            (237, "Udgir (SC)"),
            (238, "Nilanga"),
        ),
    ),
    42: (
        "Solapur",
        "SC",
        (
            (247, "Mohol (SC)"),
            (248, "Solapur City North"),
            (249, "Solapur City Central"),
            (250, "Akkalkot"),
            (251, "Solapur South"),
            (252, "Pandharpur"),
        ),
    ),
    43: (
        "Madha",
        None,
        (
            (244, "Karmala"),
            (245, "Madha"),
            (253, "Sangola"),
            (254, "Malshiran (SC)"),
            (255, "Phaltan (SC)"),
            (258, "Man"),
        ),
    ),
    44: (
        "Sangli",
        None,
        (
            (281, "Miraj (SC)"),
            (282, "Sangli"),
            (285, "Palus-Kadegaon"),
            (286, "Khanpur"),
            (287, "Tasgaon-Kavathe Mahankal"),
            (288, "Jat"),
        ),
    ),
    45: (
        "Satara",
        None,
        ((256, "Wai"), (257, "Koregaon"), (259, "Karad Narth"), (260, "Karad South"), (261, "Patan"), (262, "Satara")),
    ),
    46: (
        "Ratnagiri-Sindhudurg",
        None,
        (
            (265, "Chiplun"),
            (266, "Ratnagiri"),
            (267, "Rajapur"),
            (268, "Kankavli"),
            (269, "Kudal"),
            (270, "Sawantwadi"),
        ),
    ),
    47: (
        "Kolhapur",
        None,
        (
            (271, "Chandgad"),
            (272, "Radhanagari"),
            (273, "kagal"),
            (274, "Kolhapur South"),
            (275, "Karvir"),
            (276, "Kolhapur North"),
        ),
    ),
    48: (
        "Hatkanangle",
        None,
        (
            (277, "Shahuwadi"),
            (278, "Hatkanangle (SC)"),
            (279, "Ichalkaranji"),
            (280, "Shirol"),
            (283, "Islampur"),
            (284, "Shirala"),
        ),
    ),
}


# ---- reading a sheet -----------------------------------------------------------------------------------------------

type Word = tuple[float, float, float, float, str]
CELL = re.compile(
    r"\d+(?:[-/]?[A-Za-z]|\([A-Za-z]\))?"
)  # a number, or a station number with a letter: 73A, 50-A, 10(A)
NUMBER = re.compile(r"\d+")


@dataclass
class Row:
    serial: int
    station: str
    votes: list[int]
    valid: int
    rejected: int
    nota: int
    total: int


@dataclass
class Sheet:
    url: str
    pages: int
    k: int = 0
    rows: list[Row] = field(default_factory=list)
    printed: list[int | None] | None = (
        None  # the total row: candidates, valid, rejected, NOTA, total; None if illegible
    )
    electors: int | None = None
    problems: list[str] = field(default_factory=list)


def _lines(words: list[Word]) -> list[list[Word]]:
    """Words grouped into lines by their vertical centres."""
    grouped: list[list[Word]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (w[1] + w[3]) / 2
        if grouped and abs((grouped[-1][-1][1] + grouped[-1][-1][3]) / 2 - centre) <= 3:
            grouped[-1].append(w)
        else:
            grouped.append([w])
    return [_join(sorted(line, key=lambda w: w[0])) for line in grouped]


LETTER = re.compile(r"[-(]?[A-Za-z]\)?")
DEVANAGARI = {"अ": "A", "ब": "B", "क": "C"}  # the letters of auxiliary stations, as Marathi sheets print them
NOISE = re.compile(r"[.,:;|-]+|\(?VVPAT\)?", re.IGNORECASE)  # stray marks, and a note that a count is the slips'


def _join(line: list[Word]) -> list[Word]:
    """A station's letter printed apart from its number ("251 A", "3 (A)") joined to it, without stray marks."""
    out: list[Word] = []
    for w in line:
        if NOISE.fullmatch(w[4]):
            continue
        if w[4].strip("()-") in DEVANAGARI:
            w = (w[0], w[1], w[2], w[3], DEVANAGARI[w[4].strip("()-")])
        if out and LETTER.fullmatch(w[4]) and NUMBER.fullmatch(out[-1][4]) and w[0] - out[-1][2] < 15:
            last = out.pop()
            w = (last[0], min(last[1], w[1]), w[2], max(last[3], w[3]), last[4] + w[4])
        out.append(w)
    return out


def _centre(w: Word) -> float:
    return (w[0] + w[2]) / 2


def _numbering(texts: list[str]) -> bool:
    """The row that numbers the columns: 1, 2, 3, ..., perhaps after a serial column numbered 1 too."""
    tail = texts[-5:]
    return all(NUMBER.fullmatch(t) for t in tail) and all(int(b) == int(a) + 1 for a, b in itertools.pairwise(tail))


def _is_data(line: list[Word]) -> bool:
    texts = [w[4] for w in line]
    if len(texts) < 6 or not all(CELL.fullmatch(t) for t in texts) or sum(bool(NUMBER.fullmatch(t)) for t in texts) < 5:
        return False
    return not _numbering(texts)


def _anchors(data: list[list[Word]], width: int | None = None) -> list[float] | None:
    """The centre of each column, from the data rows of the most common width (or of the width given)."""
    if not data:
        return None
    width = width or Counter(len(line) for line in data).most_common(1)[0][0]
    full = [line for line in data if len(line) == width]
    if len(full) < 3:
        return None
    return [statistics.median(_centre(line[i]) for line in full) for i in range(width)]


def _cells(line: list[Word], anchors: list[float]) -> dict[int, str] | None:
    """A line's words by column; None when two words fall in one column or a word is far from every column."""
    pitch = min((b - a for a, b in itertools.pairwise(anchors)), default=50.0)
    if len(line) > len(anchors) and _centre(line[0]) < anchors[0] - pitch * 0.6:
        line = line[1:]  # a counting round's number, printed left of the first column on the round's first row
    if len(line) == len(anchors):
        return {i: w[4] for i, w in enumerate(line)}
    cells: dict[int, str] = {}
    for w in line:
        i = min(range(len(anchors)), key=lambda i: abs(anchors[i] - _centre(w)))
        if i in cells or abs(anchors[i] - _centre(w)) > pitch * 0.6:
            return None
        cells[i] = w[4]
    return cells


# The columns after the candidates: valid, rejected, NOTA, total, and tendered votes; some sheets drop a column, and
# some print NOTA first and count it in their valid votes ("valid_all").
TAILS: list[tuple[str, ...]] = [
    ("valid", "rejected", "nota", "total", "tendered"),
    ("valid", "rejected", "nota", "total"),
    ("valid", "nota", "total", "tendered"),
    ("valid", "nota", "total"),
    ("nota", "valid_all", "rejected", "total", "tendered"),
    ("nota", "valid_all", "rejected", "total"),
]


def _numbers(cells: dict[int, str], width: int) -> list[int]:
    return [int(cells[i]) if i in cells and NUMBER.fullmatch(cells[i]) else 0 for i in range(width)]


def _tail[T](n: list[T], lead: int, k: int, tail: tuple[str, ...]) -> tuple[T, T | int, T, T]:
    """The valid (without NOTA), rejected, NOTA, and total votes of a row read under a tail; the valid votes are
    returned as printed where the sheet counts NOTA in them."""
    at = {name: lead + k + j for j, name in enumerate(tail)}
    valid = n[at["valid"]] if "valid" in at else n[at["valid_all"]]
    return valid, n[at["rejected"]] if "rejected" in at else 0, n[at["nota"]], n[at["total"]]


def _adds(n: list[int], lead: int, k: int, tail: tuple[str, ...]) -> bool:
    valid, rejected, nota, total = _tail(n, lead, k, tail)
    if "valid_all" in tail:
        return sum(n[lead : lead + k]) + nota == valid and valid + rejected == total
    return sum(n[lead : lead + k]) == valid and valid + rejected + nota == total


def _fit(rows: list[dict[int, str]], width: int, expected: int | None) -> tuple[int, int, tuple[str, ...]] | None:
    """The lead columns ([round,] serial[, station]), the number of candidates, and the tail that the most rows satisfy."""
    best: tuple[int, tuple[int, int, tuple[str, ...]]] | None = None
    for lead in (2, 1, 3):  # three where a counting round's number comes first
        for tail in TAILS:
            k = width - lead - len(tail)
            if k < 2 or (expected is not None and k != expected):
                continue
            kept = [c for c in rows if _station_row(c, lead)]
            good = sum(_adds(n, lead, k, tail) and any(n[lead:]) for n in (_numbers(c, width) for c in kept))
            if len(kept) >= 0.5 * len(rows) and good >= 0.9 * len(kept) and (best is None or good > best[0]):
                best = (good, (lead, k, tail))
    return None if best is None else best[1]


def _station_row(cells: dict[int, str], lead: int) -> bool:
    """A row with its station's number, and its serial number unless a round's number leads the row."""
    return lead - 1 in cells and (lead == 3 or 0 in cells)


ELECTORS = re.compile(r"Elect\w*\s+in\s+(?:the\s+)?Assembly[^0-9]{0,80}?(\d{4,7})\b", re.IGNORECASE)


def _station(text: str) -> str:
    """A station's number as Form 20 prints it, without leading zeros or the marks around its letter: 10(A) is 10A."""
    m = re.fullmatch(r"(\d+)\W*([A-Za-z]?)\)?", text)
    if m is None:
        return text
    digits, letter = m.groups()
    return (digits.lstrip("0") or digits) + letter.upper()


def _figures(row: Row) -> list[int]:
    return [*row.votes, row.valid, row.rejected, row.nota, row.total]


def read(url: str, expected: int | None = None) -> Sheet:
    """A segment's sheet; `expected` is the number of candidates, where the constituency's list gives it. Some sheets
    are printed turned, a station to a column, so a sheet whose rows fit no layout is read again turned."""
    sheet = _read(url, expected, 0)
    if sheet.rows and not sheet.problems:
        return sheet
    for turn in (1, 2):
        turned = _read(url, expected, turn)
        if turned.rows and not turned.problems:
            return turned
    return sheet


def _turned(w: Word, turn: int) -> Word:
    """A word's box with the page turned a quarter: 1 reads a column top to bottom, 2 bottom to top."""
    if turn == 1:
        return (w[1], w[0], w[3], w[2], w[4])
    if turn == 2:
        return (-w[3], w[0], -w[1], w[2], w[4])
    return w


def _read(url: str, expected: int | None, turn: int) -> Sheet:
    document = pymupdf.open(raw.cached(SOURCE, url).path)
    sheet = Sheet(url, len(document))
    pages: list[tuple[list[list[Word]], list[list[Word]]]] = []
    widths: Counter[int] = Counter()
    for page in document:
        # some sheets print each word twice in the same place
        unique = {(round(w[0]), round(w[1]), w[4]): (w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")}
        lines = _lines([_turned(w, turn) for w in unique.values()])
        data = [line for line in lines if _is_data(line)]
        pages.append((lines, data))
        widths.update(len(line) for line in data)
    if not widths:
        sheet.problems.append("no text layer with station rows")
        return sheet
    width = widths.most_common(1)[0][0]
    every = _anchors([line for _, data in pages for line in data], width)
    if every is None:
        sheet.problems.append("no text layer with station rows")
        return sheet
    rows: list[tuple[int, dict[int, str]]] = []
    others: list[list[Word]] = []
    for number, (lines, data) in enumerate(pages, start=1):
        own = _anchors(data, width)
        anchors = own if own is not None else every
        for line in lines:
            cells = _cells(line, anchors) if _is_data(line) else None
            if cells is None and _is_data(line) and own is not None:
                cells = _cells(line, every)
            if cells is not None and {0, 1} & set(cells):
                rows.append((number, cells))
            else:
                others.append(line)  # a total row, or a station row that fits no column; the totals then differ
    fit = _fit([cells for _, cells in rows], width, expected)
    if fit is None:
        sheet.problems = [f"no column layout fits the rows ({width} columns)"]
        return sheet
    lead, k, tail = fit
    sheet.k = k
    at = {name: lead + k + j for j, name in enumerate(tail)}
    nota_in_valid = "valid_all" in at
    found: list[tuple[int, Row, set[int]]] = []
    for page, cells in rows:
        if not _station_row(cells, lead):
            continue  # a line of figures that is not a station's; a station's lost here shows in the totals
        n = _numbers(cells, width)
        station = cells.get(lead - 1, "")
        row = Row(
            int(re.sub(r"\D", "", cells.get(max(lead - 2, 0), "")) or 0),
            _station(station),
            n[lead : lead + k],
            *_tail(n, lead, k, tail),
        )
        if nota_in_valid:
            row.valid -= row.nota
        found.append((page, row, set(cells)))
    # A row equal to the sum of the others is the sheet's total row, printed with a count in its first cell.
    summed = [sum(x) for x in zip(*(_figures(r) for _, r, _ in found), strict=True)] if found else []
    for i, (_, row, _) in enumerate(found):
        if summed and [2 * x for x in _figures(row)] == summed:
            sheet.printed = list[int | None](_figures(row))
            del found[i]
            break
    stations = Counter(r.station for _, r, _ in found)
    for page, row, present in found:
        if not any(_figures(row)) and (row.serial == 0 or stations[row.station] > 1):
            continue  # a blank row, or a station's empty line where its figures are printed again lower down
        needed = {lead - 1, at.get("valid", at.get("valid_all", 0)), at["nota"], at["total"]}
        if missing := sorted(needed - present):
            sheet.problems.append(f"page {page}: station {row.station} has no value in columns {missing}")
        if sum(row.votes) != row.valid or row.valid + row.rejected + row.nota != row.total:
            sheet.problems.append(f"page {page}: station {row.station} does not add up")
        sheet.rows.append(row)
    if sheet.printed is None:
        sheet.printed = _printed(others, k, tail, [*map(sum, zip(*(_figures(r) for r in sheet.rows), strict=True))])
    text = " ".join(w[4] for line in pages[0][0] for w in line)
    if m := ELECTORS.search(text):
        sheet.electors = int(m.group(1))
    sheet.problems += check(sheet)
    return sheet


OVERFLOW = re.compile(r"#+|\d(\.\d+)?E\+\d+")  # a figure the spreadsheet printed too wide for its cell
GROUPED = re.compile(r"\d{1,3}(,\d{3})+")  # a figure with thousands separators


def _printed(lines: list[list[Word]], k: int, tail: tuple[str, ...], summed: list[int]) -> list[int | None] | None:
    """The sheet's total row among the lines that are not station rows: the figures at the line's right end, read as
    the candidates and any tail (a total row can print columns its station rows leave blank), that equal the columns' sums wherever the
    figure is legible; else the first such line that adds up, to report the difference."""
    found: list[list[int | None]] = []
    bare = tuple(x for x in tail if x != "tendered")
    for line in lines:
        figures = [
            None if OVERFLOW.fullmatch(w[4]) else int(w[4].replace(",", ""))
            for w in line
            if NUMBER.fullmatch(w[4]) or OVERFLOW.fullmatch(w[4]) or GROUPED.fullmatch(w[4])
        ]
        for t in dict.fromkeys([tail, bare, (*bare, "tendered"), *TAILS]):
            m = k + len(t)
            if len(figures) < m:
                continue
            n = figures[-m:]
            valid, rejected, nota, total = _tail(n, 0, k, t)
            if "valid_all" in t and valid is not None and nota is not None:
                valid -= nota
            vector = [*n[:k], valid, rejected, nota, total]
            known = [(v, s) for v, s in zip(vector, summed, strict=True) if v is not None]
            if len(known) >= k and all(v == s for v, s in known):
                return vector
            if all(v is not None for v in vector) and _adds([v or 0 for v in n], 0, k, t) and n[k]:
                found.append(vector)
    return found[0] if found else None


def check(sheet: Sheet) -> list[str]:
    """The columns against the sheet's total row, and the stations' numbers."""
    problems: list[str] = []
    stations = [r.station for r in sheet.rows]
    numbers = [(r.serial, int(re.sub(r"\D", "", r.station) or -1)) for r in sheet.rows]
    for j in range(sheet.k):
        same = sum(r.votes[j] in pair for r, pair in zip(sheet.rows, numbers, strict=True))
        if same > 0.5 * len(sheet.rows) and len(sheet.rows) > 10:
            problems.append(f"candidate column {j + 1} repeats the serial or station numbers")
            break
    if repeated := [s for s, n in Counter(stations).items() if n > 1]:
        problems.append(f"repeated stations {repeated[:5]}")
    if sheet.printed is None:
        return [*problems, "no printed total row"]
    k = sheet.k
    summed = [sum(r.votes[j] for r in sheet.rows) for j in range(k)]
    summed += [sum(r.valid for r in sheet.rows), sum(r.rejected for r in sheet.rows)]
    summed += [sum(r.nota for r in sheet.rows), sum(r.total for r in sheet.rows)]
    if any(p is not None and p != s for p, s in zip(sheet.printed, summed, strict=True)):
        problems.append(f"the columns sum to {summed}, the sheet prints {sheet.printed}")
    return problems


# ---- the candidates ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    position: int
    name: str
    party: str  # "Independent" for an independent


HEADING = re.compile(
    r"(\d{1,2})\s*[.\-–]?\s*([A-Za-z][A-Za-z .\-]*?)\s*\.?\s*(?:\((?:SC|ST|GEN)\))?\s*\.?(?:PARLIAMENTARY CONSTITUENCY)?"
)


def _text(words: list[Word], box: tuple[float, float, float, float]) -> str:
    """The words inside a table cell, in reading order."""
    x0, y0, x1, y1 = box
    inside = [w for w in words if x0 - 1 <= _centre(w) <= x1 + 1 and y0 - 1 <= (w[1] + w[3]) / 2 <= y1 + 1]
    return " ".join(" ".join(w[4] for line in _lines(inside) for w in line).split())


def _list(url: str) -> dict[int, list[Candidate]]:
    """A phase's list of contesting candidates, read from its ruled tables: a constituency's heading (a row of the
    table, or a line above it), then a row per candidate with the ballot number, name, party, and symbol."""
    document = pymupdf.open(raw.cached(SOURCE, url).path)
    found: dict[int, list[Candidate]] = {}
    pc: int | None = None
    for page in document:
        words: list[Word] = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
        headings = [
            ((line[0][1] + line[0][3]) / 2, int(m.group(1)))
            for line in _lines(words)
            if (
                m := re.fullmatch(
                    r"(\d{1,2})\s*-\s*[A-Za-z][A-Za-z()\- ]+ PARLIAMENTARY CONSTITUENCY", " ".join(w[4] for w in line)
                )
            )
        ]
        for table in sorted(page.find_tables().tables, key=lambda t: t.bbox[1]):
            above = [n for y, n in headings if y < table.bbox[1]]
            if above:
                pc = above[-1]
            for row in table.rows:
                cells = [_text(words, c) if c else "" for c in row.cells]
                if not cells or not cells[0]:
                    continue
                if (m := HEADING.fullmatch(cells[0])) and not any(cells[1:]):
                    pc = int(m.group(1))
                elif NUMBER.fullmatch(cells[0]) and len(cells) >= 3 and pc is not None:
                    name = cells[1].rstrip(" .")
                    found.setdefault(pc, []).append(Candidate(int(cells[0]), name, cells[2]))
    return found


@functools.cache
def candidates() -> dict[int, list[Candidate]]:
    """Every constituency's candidates, from the four phases' lists."""
    found: dict[int, list[Candidate]] = {}
    for url in CANDIDATE_LISTS:
        for pc, listed in _list(url).items():
            if pc in found:
                raise ValueError(f"constituency {pc} is listed twice")
            if [c.position for c in listed] != list(range(1, len(listed) + 1)):
                raise ValueError(f"{url}: constituency {pc}'s candidates are numbered {[c.position for c in listed]}")
            found[pc] = listed
    return dict(sorted(found.items()))


# The same party spelled differently in the four phases' lists, by the spelling kept.
PARTY_SPELLINGS = {
    "Bhahujan Samaj Party": "Bahujan Samaj Party",
    "Ambedkarite Party Of India": "Ambedkarite Party of India",
    "Ambekarite Party of India": "Ambedkarite Party of India",
    "Bahujan Republic Socialist Party": "Bahujan Republican Socialist Party",
    "Hum Bharatiya Party": "Hum Bhartiya Party",
    "Kranti Kari Jai Hind Sena": "Krantikari Jai Hind Sena",
    "Bhartiya Praja Surajya Paksha": "Bharatiya Praja Surajya Paksha",
    "People Party of India (Democratic)": "Peoples Party of India (Democratic)",
    "Pepoles Party of India (Democratic)": "Peoples Party of India (Democratic)",
    "Peoples Party of Indian (Democratic)": "Peoples Party of India (Democratic)",
    "Bhartiya Manavadhikar Federal Party": "Bhartiya Manavadhikaar Federal Party",
    "Rashtriya Jansambhawana Party": "Rashtriya Jan Sambhavana Party",
    "Prabudha Republican Party": "Prabuddha Republican Party",
    "Asra Lokmanch Party": "Aasra Lokmanch Party",
    "Hindustan Janta Party": "Hindustan Janata Party",
    "Akhik Bhartiya Manavta Paksha": "Akhil Bhartiya Manavta Paksha",
}


def party_name(text: str) -> str | None:
    """The party a list prints, in one spelling; None for an independent."""
    text = " ".join(text.split())
    return None if text.lower() == "independent" or not text else PARTY_SPELLINGS.get(text, text)


def person_name(text: str) -> str:
    """A name as the list prints it, with an all-capitals name in title case."""
    return " ".join(w.capitalize() if w.isupper() and len(w) > 2 else w for w in text.split())


# ---- putting a constituency together -------------------------------------------------------------------------------


@dataclass
class Seat:
    pc: int
    name: str
    reserved: str | None
    segments: tuple[tuple[int, str], ...]
    candidates: list[Candidate]
    sheets: dict[int, Sheet]  # the segments whose sheets check out
    left_out: dict[int, str]  # the others, and why


def _reason(sheet: Sheet | None) -> str:
    if sheet is None:
        return "no copy of its sheet was fetched"
    if any("no text layer" in p for p in sheet.problems):
        return "a scan without a text layer of its figures"
    if any("no column layout" in p for p in sheet.problems) and not sheet.rows:
        return "a scan whose text layer does not read as the sheet's columns"
    return "; ".join(sheet.problems[:2])


@functools.cache
def seats() -> list[Seat]:
    listed = candidates()
    out: list[Seat] = []
    for pc, (name, reserved, segments) in PCS.items():
        k = len(listed[pc])
        good: dict[int, Sheet] = {}
        left: dict[int, str] = {}
        for ac, _ in segments:
            url = sheet_url(pc, ac)
            sheet = read(url, k) if _cached(url) else None
            if sheet is not None and not sheet.problems and sheet.k != k:
                sheet.problems.append(f"{sheet.k} candidate columns, but the list names {k} candidates")
            if sheet is None or sheet.problems:
                left[ac] = _reason(sheet)
            else:
                good[ac] = sheet
        out.append(Seat(pc, name, reserved, segments, listed[pc], good, left))
    return out


# ---- the package ---------------------------------------------------------------------------------------------------


PACKAGE = f"{SOURCE}/ls-2019-mh"

METHOD = (
    "Counted at each polling station on the voting machines, as the Form 20 Part I sheet of each assembly segment "
    "prints it (Chief Electoral Officer, Maharashtra), read from the sheets' text layer with each word's position. A "
    "sheet is kept only when every station's candidates add up to its valid votes, its valid, rejected, and NOTA "
    "votes to its total, and every column to the total row the sheet prints. Polling stations are numbered as the "
    "sheets number them (an auxiliary station keeps its letter) and nested in their assembly segment, which carries "
    "the segment's electors where the sheet prints them. Ballots cast are the station's total votes (valid, rejected, "
    "and NOTA); tendered votes are not stored. Postal ballots are counted for the whole constituency and are not on "
    "the segment sheets, so the set holds the machine votes only. The candidates, their ballot order, and their "
    "parties are from the Chief Electoral Officer's lists of contesting candidates; the declared winner is not on "
    "Form 20, so no outcome is recorded."
)


def packages() -> list[str]:
    return [PACKAGE] if any(s.sheets for s in seats()) else []


def _value(rows: list[dict[str, object]], rs: str, unit: str, option: str | None, channel: int, measure: int, v: int):
    rows.append(
        {
            "result_set_id": rs,
            "unit_id": unit,
            "option_id": option,
            "channel": channel,
            "measure": measure,
            "count_round": 0,
            "flags": 0,
            "value": v,
        }
    )


def election() -> Package:
    p = Package(
        PACKAGE,
        SOURCE,
        "The 2019 Lok Sabha general election in Maharashtra by polling station, from the Form 20 sheets the Chief "
        "Electoral Officer posts, with each constituency's candidates and parties from the lists of contesting "
        "candidates",
    )
    p.external |= {INDIA, ELECTION, BODY, SEGMENT_SCHEME, STATION_SCHEME}
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=INDIA,
        valid_from=date(1952, 1, 1),
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each constituency elects one member: the candidate with the most votes wins.",
        lang="en",
    )
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Chief Electoral Officer, Maharashtra",
        title="Form 20 final result sheets and lists of contesting candidates, 2019 Lok Sabha general election, "
        "Maharashtra",
        url=SITE,
        license="Official publication of the Election Commission of India",
        attribution="Chief Electoral Officer, Maharashtra, Form 20 final result sheets",
        notes="The sheets are listed per constituency on the site's Polling Boothwise Result (Form 20) page.",
    )
    p.uses(raw.cached(SOURCE, INDEX))
    for url in CANDIDATE_LISTS:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    held = [s for s in seats() if s.sheets]
    parties: dict[str, str] = {}
    for seat in held:
        for c in seat.candidates:
            if (name := party_name(c.party)) and name not in parties:
                parties[name] = key("party", name.lower())
                p.add("party", id=parties[name], kind="party", polity_id=INDIA)
                p.name(parties[name], name, "en", "official")
    units: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    for seat in held:
        _contest(p, seat, parties, units, values)
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))
    return p


def _contest(
    p: Package, seat: Seat, parties: dict[str, str], units: list[dict[str, object]], values: list[dict[str, object]]
) -> None:
    tag = f"{YEAR}:MH:{seat.pc}"
    contest = key("contest", tag)
    district = pc_area(seat.pc)
    p.external.add(district)
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION,
        kind="body_seats",
        body_id=BODY,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP,
        default_result_kind="counted",
    )
    label = seat.name + (f" ({seat.reserved})" if seat.reserved else "")
    p.name(contest, f"{YEAR} Lok Sabha election in {label}, Maharashtra", "en", "official")
    p.add("slug", path=f"{ELECTION_PATH}/mh/{seat.pc}-{changeset.slug(seat.name)}", entity_id=contest, is_current=True)
    options: list[str] = []
    for c in seat.candidates:
        option, person = key("option", tag, c.position), key("person", tag, c.position)
        name = person_name(c.name)
        party = party_name(c.party)
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=parties[party] if party else None,
            ballot_position=c.position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, c.position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=parties[party] if party else None,
            is_independent=True if party is None else None,
        )
        options.append(option)
    nota = key("option", tag, "nota")
    p.add(
        "contest_option",
        id=nota,
        contest_id=contest,
        kind="none_of_the_above",
        ballot_position=len(options) + 1,
        ballot_label="NOTA",
    )
    rs = key("result_set", tag)
    expected = len(seat.segments)
    p.add(
        "result_set",
        id=rs,
        election_id=ELECTION,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=round(len(seat.sheets) / expected, 4),
    )
    body = METHOD + f" Segments read: {', '.join(str(ac) for ac in seat.sheets)} of {expected}."
    if seat.left_out:
        body += " Left out: " + "; ".join(f"segment {ac}, {why}" for ac, why in seat.left_out.items()) + "."
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
    names = dict(seat.segments)
    for ac, sheet in seat.sheets.items():
        p.artifact(raw.cached(SOURCE, sheet.url), SOURCE_ID)
        area = ac_area(ac)
        p.external.add(area)
        segment = key("unit", tag, ac)
        units.append(
            {
                "id": segment,
                "polity_id": INDIA,
                "scheme_code": SEGMENT_SCHEME,
                "code": f"{tag}:{ac}",
                "area_relation": "equals",
                "area_id": area,
                "kind": "standard",
            }
        )
        p.name(segment, f"{ac} {names[ac]}", "en", "official")
        if sheet.electors and sheet.electors >= max(r.total for r in sheet.rows):
            _value(values, rs, segment, None, ALL, REGISTERED, sheet.electors)
        for r in sheet.rows:
            unit = key("unit", tag, ac, r.station)
            units.append(
                {
                    "id": unit,
                    "polity_id": INDIA,
                    "scheme_code": STATION_SCHEME,
                    "code": f"{tag}:{ac}:{r.station}",
                    "parent_id": segment,
                    "area_relation": "within",
                    "area_id": area,
                    "kind": "standard",
                }
            )
            for option, votes in zip(options, r.votes, strict=True):
                _value(values, rs, unit, option, ELECTION_DAY, VOTES, votes)
            _value(values, rs, unit, nota, ELECTION_DAY, VOTES, r.nota)
            _value(values, rs, unit, None, ELECTION_DAY, INVALID, r.rejected)
            _value(values, rs, unit, None, ELECTION_DAY, BALLOTS, r.total)


def coverage() -> dict[str, object]:
    """Each constituency: the segments read, and why the others are not."""
    return {
        str(s.pc): {
            "name": s.name,
            "read": list(s.sheets),
            "segments": [ac for ac, _ in s.segments],
            "left_out": {str(ac): why for ac, why in s.left_out.items()},
        }
        for s in seats()
    }
