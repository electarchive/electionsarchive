"""The 1979 to 1999 European Parliament results in Ireland, read from the Department's scanned volumes.

The volumes for these five elections are scans without a text layer. The 1979, 1984, and 1989 brochures print each
constituency's count table with every candidate's first preference votes, and the 1994 and 1999 leaflets print each
constituency's first preference votes by party. The figures below were read from the page images and checked against
the figures the volumes print: every constituency's candidate or party votes add up to its valid poll, the valid poll
and the spoiled votes add up to the total poll, the quota follows from the valid poll and the seats, and the
constituencies add up to the national totals. `locate` finds each figure on its page for the value locators.

A candidate is `(label, party, first preference votes, elected, name)`. The 1979 volume prints the full name as
`Surname, First`; the 1984 and 1989 volumes print the surname, and give the initials of those elected.
"""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Candidate = tuple[str, str | None, int, bool, str | None]


@dataclass(frozen=True)
class Constituency:
    code: str
    electorate: int
    invalid: int | None
    valid: int
    seats: int
    quota: int
    page: int  # the PDF page with the candidates' or parties' votes
    summary: int  # the PDF page with the constituency's electorate and poll
    candidates: tuple[Candidate, ...]


# the printed order of the four constituencies; the volumes' own codes
NAMES = {"dublin": "Dublin", "leinster": "Leinster", "munster": "Munster", "connacht-ulster": "Connacht–Ulster"}

CANDIDATES_1979 = {
    "dublin": (
        ("Brugha, Ruairi", "FF", 21758, False, None),
        ("Clear, Kevin", "CDI", 915, False, None),
        ("de Valera, Síle", "FF", 41357, True, None),
        ("Dillon-Byrne, Jane", "LAB", 7807, False, None),
        ("Loftus, Seán", None, 21769, False, None),
        ("Fennell, Nuala", "FG", 26951, False, None),
        ("Fox, Joe", "FF", 8178, False, None),
        ("MacGiolla, Tomás", "SFWP", 11915, False, None),
        ("Manning, Maurice", "FG", 14296, False, None),
        ("O'Connell, John", "LAB", 44832, True, None),
        ("O'Leary, Michael", "LAB", 34511, True, None),
        ("Ryan, Richie", "FG", 48411, True, None),
        ("Yeats, Michael", "FF", 12715, False, None),
    ),
    "leinster": (
        ("Barnes, Monica", "FG", 21384, False, None),
        ("Clinton, Mark", "FG", 78762, True, None),
        ("Kavanagh, Liam", "LAB", 40072, True, None),
        ("Lalor, Paddy", "FF", 62094, True, None),
        ("McDonald, Charlie", "FG", 24875, False, None),
        ("MacRaghnaill, Donnchadha", "SFWP", 8414, False, None),
        ("Nolan, Tom", "FF", 34210, False, None),
        ("Power, Paddy", "FF", 31023, False, None),
        ("Walsh, Seán", "SFWP", 6062, False, None),
    ),
    "munster": (
        ("Blair, John", "FG", 21615, False, None),
        ("Cronin, Jerry", "FF", 43439, True, None),
        ("Crowe, Michael", "CDI", 2268, False, None),
        ("Davern, Noel", "FF", 37647, True, None),
        ("Desmond, Eileen", "LAB", 53614, True, None),
        ("Dukes, Alan", "FG", 21510, False, None),
        ("Dunphy, Michael", "SFWP", 11526, False, None),
        ("French, Seán", "FF", 16655, False, None),
        ("Herbert, Michael", "FF", 34034, False, None),
        ("Maher, Thomas J.", None, 86208, True, None),
        ("O'Connor, Timothy", "FF", 29595, False, None),
        ("O'Donnell, Tom", "FG", 46820, True, None),
        ("O'Keeffe, Jim", "FG", 25664, False, None),
    ),
    "connacht-ulster": (
        ("Blaney, Neil", None, 81522, True, None),
        ("Coffey, Tony", "SFWP", 3329, False, None),
        ("Cooney, Patrick", "FG", 33360, False, None),
        ("Doolan, Jim", "FF", 27739, False, None),
        ("Flanagan, Seán", "FF", 38233, True, None),
        ("Higgins, Michael D.", "LAB", 13062, False, None),
        ("McCartin, Joe", "FG", 47519, True, None),
        ("McEniff, Seán", "FF", 25774, False, None),
        ("Morris, Christopher", "CDI", 447, False, None),
        ("Rodgers, Seamus", "SFWP", 2696, False, None),
        ("Staunton, Myles", "FG", 32485, False, None),
    ),
}

CANDIDATES_1984 = {
    "dublin": (
        ("Andrews", "FF", 32512, True, "N. Andrews"),
        ("Banotti", "FG", 42660, True, "M. Banotti"),
        ("Cluskey", "LAB", 28384, False, None),
        ("De Courcy Ireland", "DSP", 5350, False, None),
        ("Dublin Bay Loftus", None, 17385, False, None),
        ("Fettes", "GA", 5242, False, None),
        ("Geraghty", "WP", 19590, False, None),
        ("Lemass", "FF", 31350, True, "E. Lemass"),
        ("Noonan", "SF", 14604, False, None),
        ("Ross", None, 8099, False, None),
        ("Ryan", "FG", 47014, True, "R. Ryan"),
        ("Tunney", "FF", 30488, False, None),
    ),
    "leinster": (
        ("Bolger", "FG", 33208, False, None),
        ("Carroll", "SF", 4396, False, None),
        ("Clinton", "FG", 61669, True, "M. Clinton"),
        ("Dwyer", "SF", 2245, False, None),
        ("Fitzsimons", "FF", 57321, True, "J. Fitzsimons"),
        ("Keating", "LAB", 30773, False, None),
        ("Lalor", "FF", 56191, True, "P. Lalor"),
        ("McManus", "WP", 8943, False, None),
        ("Sharkey", "SF", 4548, False, None),
    ),
    "munster": (
        ("Barrett", "FF", 47622, True, "S. Barrett"),
        ("Behal", "SF", 12829, False, None),
        ("Davern", "FF", 42863, False, None),
        ("Desmond", "LAB", 26162, False, None),
        ("Fitzgerald", "FF", 43036, True, "G. Fitzgerald"),
        ("Maher", None, 55079, True, "T.J. Maher"),
        ("O'Donnell", "FG", 53832, True, "T. O'Donnell"),
        ("Raftery", "FG", 44236, True, "T. Raftery"),
        ("Sherlock", "WP", 17304, False, None),
    ),
    "connacht-ulster": (
        ("Blaney", None, 32504, False, None),
        ("Brick", "WP", 2612, False, None),
        ("Flanagan", "FF", 40760, True, "S. Flanagan"),
        ("Fullerton", "SF", 5771, False, None),
        ("Higgins", "LAB", 8337, False, None),
        ("MacSharry", "FF", 56803, True, "R. MacSharry"),
        ("McCartin", "FG", 51164, True, "J. McCartin"),
        ("McGing", "SF", 4176, False, None),
        ("Murrin", "FG", 20107, False, None),
        ("O'Caolain", "SF", 6103, False, None),
        ("O'Foighil", "FG", 7144, False, None),
    ),
}

CANDIDATES_1989 = {
    "dublin": (
        ("Andrews", "FF", 72057, True, "N. Andrews"),
        ("Banotti", "FG", 50666, True, "M. Banotti"),
        ("Cahill", None, 1668, False, None),
        ("Crotty", None, 25525, False, None),
        ("De Rossa", "WP", 71041, True, "P. De Rossa"),
        ("Desmond", "LAB", 57225, True, "B. Desmond"),
        ("Harney", "PD", 36402, False, None),
        ("Lemass", "FF", 58345, False, None),
        ("O'Malley", "FG", 26574, False, None),
        ("Sargent", "GP", 37317, False, None),
        ("Speed", "SF", 11582, False, None),
    ),
    "leinster": (
        ("Bell", "LAB", 49766, False, None),
        ("Boland", None, 3362, False, None),
        ("Cooney", "FG", 65775, True, "P. Cooney"),
        ("Dardis", "PD", 31623, False, None),
        ("De Groot", None, 1626, False, None),
        ("Dunphy", "SF", 4534, False, None),
        ("English", "GP", 23724, False, None),
        ("Enright", "WP", 9451, False, None),
        ("Fitzsimons", "FF", 63797, True, "J. Fitzsimons"),
        ("Lalor", "FF", 75627, True, "P. Lalor"),
        ("McDonald", "FG", 35792, False, None),
        ("McGeough", "SF", 3001, False, None),
        ("Moore", "SF", 2424, False, None),
        ("Murphy", "WP", 7089, False, None),
    ),
    "munster": (
        ("Abbey of the Holy Cross Fitzsimons", None, 1794, False, None),
        ("Cox", "PD", 85558, True, "P. Cox"),
        ("Cushnahan", "FG", 43326, True, "J. Cushnahan"),
        ("Desmond", "LAB", 29979, False, None),
        ("Fahey", "FF", 37290, False, None),
        ("Ferris", "LAB", 13843, False, None),
        ("Fitzgerald", "FF", 64139, True, "G. Fitzgerald"),
        ("Lane", "FF", 41152, True, "P. Lane"),
        ("Maher", None, 55499, True, "T.J. Maher"),
        ("Noonan", None, 15975, False, None),
        ("O'Shea", None, 2011, False, None),
        ("Raftery", "FG", 43528, False, None),
        ("Ryan", None, 30934, False, None),
        ("Salter-Townsend", None, 2577, False, None),
        ("Sherlock", "WP", 26828, False, None),
    ),
    "connacht-ulster": (
        ("Blaney", None, 52852, True, "N. Blaney"),
        ("Brick", "WP", 4759, False, None),
        ("Doherty, P.", "SF", 7716, False, None),
        ("Doherty, S.", "FF", 48288, False, None),
        ("Guy", "SF", 1697, False, None),
        ("Harte", "FG", 30745, False, None),
        ("Killilea", "FF", 53842, True, "M. Killilea"),
        ("Lupton", "FG", 10165, False, None),
        ("McCartin", "FG", 46523, True, "J. McCartin"),
        ("McPhillips", "LAB", 4969, False, None),
        ("Molloy", "PD", 40476, False, None),
        ("O'Caolain", "SF", 6173, False, None),
        ("Rodgers", "WP", 4097, False, None),
    ),
}

# (electorate, spoiled, valid, seats, quota, PDF page of the votes, PDF page of the poll figures)
FIGURES = {
    1979: {
        "dublin": (618454, 8653, 295415, 4, 59084, 12, 10),
        "leinster": (486248, 15416, 306896, 3, 76725, 9, 7),
        "munster": (641625, 14597, 430595, 5, 71766, 16, 14),
        "connacht-ulster": (442471, 14547, 306166, 3, 76542, 20, 18),
    },
    1984: {
        "dublin": (704873, None, 282678, 4, 56536, 4, 2),
        "leinster": (545878, None, 259294, 3, 64824, 4, 2),
        "munster": (691076, None, 342963, 5, 57161, 5, 2),
        "connacht-ulster": (471577, None, 235481, 3, 58871, 5, 2),
    },
    1989: {
        "dublin": (713183, 7137, 448402, 4, 89681, 4, 3),
        "leinster": (571694, 14106, 377591, 3, 94398, 4, 3),
        "munster": (703913, 10786, 494433, 5, 82406, 5, 3),
        "connacht-ulster": (464661, 10362, 312302, 3, 78076, 5, 3),
    },
}
CANDIDATES = {1979: CANDIDATES_1979, 1984: CANDIDATES_1984, 1989: CANDIDATES_1989}

# 1994 and 1999: each party's first preference votes and seats per constituency, in the printed order
# (Dublin, Leinster, Munster, Connacht-Ulster); `None` where the volume prints a blank or a dash.
ORDER = ("dublin", "leinster", "munster", "connacht-ulster")
PARTY_FIGURES: dict[int, dict[str, Any]] = {
    1994: {
        "electorate": (755486, 624561, 755176, 496352),
        "invalid": (2917, 6599, 5319, 4971),
        "valid": (277844, 262445, 364571, 232630),
        "seats": (4, 4, 4, 3),
        "quota": (55569, 52490, 72915, 58158),
        "page": 1,
        "national": {"electorate": 2631575, "invalid": 19806, "valid": 1137490},
        # label, party code, kind, votes by constituency, seats by constituency
        "parties": (
            ("Fianna Fáil", "FF", "list", (57925, 87823, 153509, 98809), (1, 2, 2, 2)),
            ("Fine Gael", "FG", "list", (66169, 72784, 68156, 68986), (1, 1, 1, 1)),
            ("Labour", "LAB", "list", (39093, 40567, 25486, 19826), (1, 0, 0, 0)),
            ("Greens", "GP", "list", (40388, 30997, 10033, 8628), (1, 1, 0, 0)),
            ("Progressive Democrats", "PD", "list", (8212, 12591, 31674, 21219), (0, 0, 0, 0)),
            ("Democratic Left", "DL", "list", (24133, None, 15573, None), (0, 0, 0, 0)),
            ("Sinn Féin", "SF", "list", (8190, 6523, 5171, 13939), (0, 0, 0, 0)),
            ("Workers' Party", "WP", "list", (15830, None, 6270, None), (0, 0, 0, 0)),
            ("Others", None, "others", (17904, 11160, 48699, 1223), (0, 0, 1, 0)),
        ),
        "national_votes": {
            "Fianna Fáil": 398066,
            "Fine Gael": 276095,
            "Labour": 124972,
            "Greens": 90046,
            "Progressive Democrats": 73696,
            "Democratic Left": 39706,
            "Sinn Féin": 33823,
            "Workers' Party": 22100,
            "Others": 78986,
        },
    },
    1999: {
        "electorate": (793200, 706601, 823008, 541552),
        "invalid": (6013, 14725, 13724, 12085),
        "valid": (280671, 342339, 448579, 320151),
        "seats": (4, 4, 4, 3),
        "quota": (56135, 68468, 89716, 80038),
        "page": 1,
        "national": {"electorate": 2864361, "invalid": 46547, "valid": 1391740},
        "parties": (
            ("Fianna Fáil", "FF", "list", (69241, 117227, 237301, 113988), (1, 2, 2, 1)),
            ("Fine Gael", "FG", "list", (84466, 116610, 77463, 63632), (1, 1, 1, 1)),
            ("Labour", "LAB", "list", (44638, 38112, 28270, 10522), (1, 0, 0, 0)),
            ("Green Party", "GP", "list", (35659, 47184, 10257, None), (1, 1, 0, 0)),
            ("Sinn Féin", "SF", "list", (18633, 20015, 29060, 20457), (0, 0, 0, 0)),
            ("Non Party", None, "independents", (6984, None, 64961, 109632), (0, 0, 1, 1)),
            ("Natural Law Party", "NLP", "list", (1006, 3191, 1267, 1920), (0, 0, 0, 0)),
            ("Socialist Party", "SP", "list", (10619, None, None, None), (0, 0, 0, 0)),
            ("Christian Solidarity Party", "CSP", "list", (9425, None, None, None), (0, 0, 0, 0)),
        ),
        "national_votes": {
            "Fianna Fáil": 537757,
            "Fine Gael": 342171,
            "Labour": 121542,
            "Green Party": 93100,
            "Sinn Féin": 88165,
            "Non Party": 181577,
            "Natural Law Party": 7384,
            "Socialist Party": 10619,
            "Christian Solidarity Party": 9425,
        },
    },
}
# National first preference totals the 1979 to 1989 volumes print (valid poll, spoiled votes, electorate).
NATIONAL = {
    1979: {"electorate": 2188798, "invalid": 53213, "valid": 1339072},
    1984: {"electorate": 2413404, "invalid": 27329, "valid": 1120416},
    1989: {"electorate": 2453451, "invalid": 42391, "valid": 1632728},
}


def constituencies(year: int) -> list[Constituency]:
    out = []
    for code, (electorate, invalid, valid, seats, quota, page, summary) in FIGURES[year].items():
        out.append(Constituency(code, electorate, invalid, valid, seats, quota, page, summary, CANDIDATES[year][code]))
    return out


def check(year: int) -> list[str]:
    """Every identity the volume's own figures must satisfy; an empty list means the transcription is consistent."""
    problems = []
    totals = {"electorate": 0, "invalid": 0, "valid": 0}
    for c in constituencies(year):
        votes = sum(cand[2] for cand in c.candidates)
        if votes != c.valid:
            problems.append(f"{year} {c.code}: candidates sum to {votes:,}, the valid poll is {c.valid:,}")
        if c.quota != c.valid // (c.seats + 1) + 1:
            problems.append(f"{year} {c.code}: quota {c.quota:,} does not follow from the valid poll {c.valid:,}")
        won = sum(1 for cand in c.candidates if cand[3])
        if won != c.seats:
            problems.append(f"{year} {c.code}: {won} elected for {c.seats} seats")
        totals["electorate"] += c.electorate
        totals["valid"] += c.valid
        totals["invalid"] += c.invalid or 0
    printed = NATIONAL[year]
    for field in ("electorate", "valid") + (("invalid",) if year != 1984 else ()):
        if totals[field] != printed[field]:
            problems.append(
                f"{year}: constituencies sum to {totals[field]:,} {field}, the volume prints {printed[field]:,}"
            )
    return problems


def check_parties(year: int) -> list[str]:
    figures = PARTY_FIGURES[year]
    problems = []
    for at, code in enumerate(ORDER):
        votes = sum(row[3][at] or 0 for row in figures["parties"])
        seats = sum(row[4][at] for row in figures["parties"])
        valid = figures["valid"][at]
        if votes != valid:
            problems.append(f"{year} {code}: parties sum to {votes:,}, the valid poll is {valid:,}")
        if seats != figures["seats"][at]:
            problems.append(f"{year} {code}: {seats} seats won for {figures['seats'][at]}")
        if figures["quota"][at] != valid // (figures["seats"][at] + 1) + 1:
            problems.append(f"{year} {code}: quota does not follow from the valid poll")
    for label, _, _, votes, _ in figures["parties"]:
        total = sum(v or 0 for v in votes)
        if total != figures["national_votes"][label]:
            problems.append(f"{year} {label}: {total:,} against {figures['national_votes'][label]:,} printed")
    for field, printed in figures["national"].items():
        got = sum(figures[field])
        if got != printed:
            problems.append(f"{year}: constituencies sum to {got:,} {field}, the volume prints {printed:,}")
    return problems


Box = tuple[float, float, float, float]


def _words(image: Path, psm: int) -> list[dict[str, int | str]]:
    out = subprocess.run(
        ["tesseract", str(image), "-", "--psm", str(psm), "tsv"], capture_output=True, check=True, text=True
    ).stdout
    rows = [line.split("\t") for line in out.splitlines()[1:]]
    return [
        {"text": r[11], "x": int(r[6]), "y": int(r[7]), "w": int(r[8]), "h": int(r[9])}
        for r in rows
        if len(r) == 12 and r[11].strip() and r[0] == "5"
    ]


ATTEMPTS = ((200, 11), (300, 11), (300, 6), (400, 11), (250, 12), (400, 6))


def locate(pdf: Path, page: int, wanted: dict[str, int], rotate: bool = False) -> dict[str, Box]:
    """`_locate` over several resolutions and layouts, since a scan's smudged figure reads at one and not another."""
    found: dict[str, Box] = {}
    for dpi, psm in ATTEMPTS:
        left = {k: v for k, v in wanted.items() if k not in found}
        if not left:
            break
        found.update(_locate(pdf, page, left, rotate, dpi, psm))
    return found


def _locate(pdf: Path, page: int, wanted: dict[str, int], rotate: bool, dpi: int, psm: int) -> dict[str, Box]:
    """Where each wanted figure sits on a page, as fractions of the page's width and height.

    The page is read with Tesseract, an independent pass from the transcription. A figure is found as one word or as
    two or three neighboring words (`32 512`) whose digits spell it; of several matches the leftmost wins, which is
    the first count column of a count table. A rotated page is read upright and the boxes are mapped back.
    """
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(stem)],
            check=True,
        )
        image = Image.open(f"{stem}.png")
        width, height = image.size
        if rotate:
            image = image.rotate(-90, expand=True)
            image.save(f"{stem}.png")
        words = _words(Path(f"{stem}.png"), psm)
    found: dict[str, Box] = {}
    for name, value in wanted.items():
        target = str(value)
        hits: list[tuple[int, int, int, int]] = []
        for at, word in enumerate(words):
            digits = ""
            for take in range(3):
                if at + take >= len(words):
                    break
                part = words[at + take]
                if take and abs(int(part["y"]) - int(word["y"])) > int(word["h"]):
                    break
                digits += re.sub(r"\D", "", str(part["text"]))
                if digits == target and re.search(r"\d", str(part["text"])):
                    chunk = words[at : at + take + 1]
                    x0 = min(int(w["x"]) for w in chunk)
                    y0 = min(int(w["y"]) for w in chunk)
                    x1 = max(int(w["x"]) + int(w["w"]) for w in chunk)
                    y1 = max(int(w["y"]) + int(w["h"]) for w in chunk)
                    hits.append((x0, y0, x1, y1))
                    break
                if len(digits) >= len(target):
                    break
        if hits:
            x0, y0, x1, y1 = min(hits)
            if rotate:
                # the upright image is the page turned clockwise: upright (x, y) was page (y, height - x)
                x0, y0, x1, y1 = y0, height - x1, y1, height - x0
            found[name] = (x0 / width, y0 / height, x1 / width, y1 / height)
    return found


def rows(pdf: Path, page: int, labels: dict[str, str], dpi: int = 300) -> dict[str, tuple[float, float]]:
    """The vertical span of each candidate's name on a page, as fractions of the page height.

    For a figure the digit read could not find, the cell is the first count column at the height of the name's row.
    """
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(stem)],
            check=True,
        )
        from PIL import Image

        height = Image.open(f"{stem}.png").size[1]
        words = _words(Path(f"{stem}.png"), 6)
    found: dict[str, tuple[float, float]] = {}
    for key, label in labels.items():
        first = re.sub(r"\W", "", label.split(",")[0].split()[0]).lower()
        hits = [w for w in words if re.sub(r"\W", "", str(w["text"])).lower() == first and int(w["x"]) < 400]
        if hits:
            best = min(hits, key=lambda w: int(w["x"]))
            found[key] = (int(best["y"]) / height, (int(best["y"]) + int(best["h"])) / height)
    return found
