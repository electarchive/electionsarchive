"""The Madeira regional elections of 1976 to 2004, by municipal circle, from the National Elections Commission's maps.

After each election the National Elections Commission (CNE) publishes the official map of results (mapa oficial) in the
Diário da República and on its own site. The map gives, for each of the eleven circles (one for each municipality), the
registered voters, the voters, the blank and invalid ballots, each list's votes and its seats. It does not go below the
circle. The Ministry's results site carries only 2007 onward for Madeira, so these eight elections have no parish-level
source here.

The maps of 1996, 2000, and 2004 have a text layer, and `pdftotext -bbox` gives each word with its box. The maps of 1976
to 1992 are scans of the printed gazette; their pages are read with an optional OCR engine (`uv sync --group scans`),
and a scanned number is stored only when the row and column arithmetic of the map agrees with it: every circle's lists
add up to its valid votes, every column adds up to the printed totals, every circle's seats add up to its printed
total, and each printed percentage agrees with its count. A number the engine misreads is then caught by one of those
checks and is corrected from the page image in `FIXES`.

Every stored number keeps the page and the box of the token it was read from.
"""

import difflib
import itertools
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pt import cne_acores
from ea.sources.pt.mai import MADEIRA_ASSEMBLY, PORTUGAL, SYSTEM_LIST, fold, town_area
from ea.store import DATA

SOURCE = "pt.cne_madeira"
CNE = "https://www.cne.pt/sites/default/files/dl"
SHAPES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/90a1d52/releaseData/gbOpen/PRT/ADM2/"
    "geoBoundaries-PRT-ADM2_simplified.geojson"
)
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6
DPI = 110  # of the page images stored with the package; every box below is in pixels at this resolution
OCR_DPI = 250

# The municipalities of Madeira, by the Ministry's code, with the name each map prints. Each is one circle.
CIRCLES = {
    "3101": ("Calheta", "Calheta"),
    "3102": ("Câmara de Lobos", "Câmara de Lobos"),
    "3103": ("Funchal", "Funchal"),
    "3104": ("Machico", "Machico"),
    "3105": ("Ponta do Sol", "Ponta do Sol"),
    "3106": ("Porto Moniz", "Porto Moniz"),
    "3201": ("Porto Santo", "Porto Santo"),
    "3107": ("Ribeira Brava", "Ribeira Brava"),
    "3108": ("Santa Cruz", "Santa Cruz"),
    "3109": ("Santana", "Santana"),
    "3110": ("São Vicente", "São Vicente"),
}
BY_NAME = {
    "calheta": "3101",
    "camara de lobos": "3102",
    "funchal": "3103",
    "machico": "3104",
    "ponta do sol": "3105",
    "porto moniz": "3106",
    "porto santo": "3201",
    "ribeira brava": "3107",
    "santa cruz": "3108",
    "santana": "3109",
    "sao vicente": "3110",
    "s vicente": "3110",
}
SHAPE_NAMES = {
    "calheta": "3101",
    "camara de lobos": "3102",
    "funchal": "3103",
    "machico": "3104",
    "ponta do sol": "3105",
    "porto moniz": "3106",
    "porto santo": "3201",
    "ribeira brava": "3107",
    "santa cruz": "3108",
    "santana": "3109",
    "sao vicente": "3110",
}

ORDER_A = ("3101", "3102", "3103", "3104", "3105", "3106", "3201", "3107", "3108", "3109", "3110")
ORDER_B = ("3101", "3102", "3103", "3104", "3105", "3106", "3201", "3107", "3109", "3110", "3108")
LEAD7 = (("i", "int"), ("v", "int"), ("vp", "pct"), ("b", "int"), ("bp", "pct"), ("n", "int"), ("np", "pct"))
LEAD3 = (("i", "int"), ("v", "int"), ("bn", "int"))


@dataclass(frozen=True)
class Block:
    """One run of the map's columns on one page: the leading counts, a run of lists, and the total of seats."""

    page: int
    lead: tuple[tuple[str, str], ...]
    parties: tuple[str, ...]
    total: bool
    turned: bool = False  # a scan printed a quarter turn, which is read upright
    band: int = 0  # which of the page's bands of circle rows, from the top, when the map repeats them


@dataclass(frozen=True)
class Year:
    year: int
    day: date
    pdf: str
    blocks: tuple[Block, ...]
    scan: bool
    # The percentage printed next to a count is of the voters, or of the valid votes in 1976.
    base: str = "voters"
    # The circles' codes in the order the map prints them, to place a row whose name the OCR engine garbled.
    order: tuple[str, ...] = ()


YEARS = [
    Year(
        1976,
        date(1976, 6, 27),
        "resultados_alras_1976.pdf",
        (Block(4, LEAD3, ("CDS", "MRPP", "PCP", "PPD", "PS", "UDP"), False, True),),
        True,
        "valid",
        ORDER_A,
    ),
    Year(
        1980,
        date(1980, 10, 5),
        "resultados_alram_1980.pdf",
        (Block(3, LEAD7, ("APU", "CDS", "PCTP/MRPP", "PS", "PPD/PSD", "UDA/PDA", "UDP"), True),),
        True,
        order=ORDER_B,
    ),
    Year(
        1984,
        date(1984, 10, 14),
        "resultados_alram_1984.pdf",
        (Block(3, LEAD7, ("APU", "CDS", "PCTP/MRPP", "PS", "PPD/PSD", "UDP"), True),),
        True,
        order=ORDER_B,
    ),
    Year(
        1988,
        date(1988, 10, 9),
        "resultados_alram_1988.pdf",
        (Block(3, LEAD7, ("CDS", "CDU", "PCTP/MRPP", "PDA", "PPD/PSD", "PS", "UDP"), True, True),),
        True,
        order=ORDER_A,
    ),
    Year(
        1992,
        date(1992, 10, 11),
        "resultados_alras_1992.pdf",
        (
            Block(4, LEAD7, ("CDS", "PCP/PEV"), False),
            Block(5, (), ("PDA", "PPD/PSD", "PS", "PSN", "UDP"), True),
        ),
        True,
        order=ORDER_A,
    ),
    Year(
        1996,
        date(1996, 10, 13),
        "resultados_alram_1996.pdf",
        (
            Block(2, LEAD7, ("CDS-PP", "PCP-PEV"), False, band=0),
            Block(2, (), ("PDA", "PPD/PSD", "PS", "PSN", "UDP"), True, band=1),
        ),
        False,
    ),
    Year(
        2000,
        date(2000, 10, 15),
        "resultados_alram_2000.pdf",
        (Block(2, LEAD7, ("CDS-PP", "PCP-PEV", "PPD/PSD", "PS", "PSN", "UDP"), True),),
        False,
    ),
    Year(
        2004,
        date(2004, 10, 17),
        "resultados_alram_2004.pdf",
        (Block(3, LEAD7, ("BE", "CDS-PP", "PCP-PEV", "PPD/PSD", "PS"), True),),
        False,
    ),
]

# A list's identity across the maps: the label each map prints, and the one party it stands for.
PARTY_OF = {
    "APU": "APU",
    "BE": "BE",
    "CDS": "CDS-PP",
    "CDS-PP": "CDS-PP",
    "CDU": "CDU",
    "PCP/PEV": "CDU",
    "PCP-PEV": "CDU",
    "MRPP": "PCTP/MRPP",
    "PCTP/MRPP": "PCTP/MRPP",
    "PCP": "PCP",
    "PDA": "PDA",
    "UDA/PDA": "PDA",
    "PPD": "PPD/PSD",
    "PPD/PSD": "PPD/PSD",
    "PS": "PS",
    "PSN": "PSN",
    "UDP": "UDP",
}
# Names from each map's legend.
NAMES = {
    "APU": [("Aliança Povo Unido", True)],
    "BE": [("Bloco de Esquerda", True)],
    "CDS-PP": [("CDS — Partido Popular", True), ("Centro Democrático Social", False)],
    "CDU": [("Coligação Democrática Unitária", True)],
    "PCP": [("Partido Comunista Português", True)],
    "PCTP/MRPP": [
        ("Partido Comunista dos Trabalhadores Portugueses", True),
        ("Movimento Reorganizativo do Partido do Proletariado", False),
    ],
    "PDA": [("Partido Democrático do Atlântico", True)],
    "PPD/PSD": [("Partido Social Democrata", True), ("Partido Popular Democrático", False)],
    "PS": [("Partido Socialista", True)],
    "PSN": [("Partido da Solidariedade Nacional", True)],
    "UDP": [("União Democrática Popular", True)],
}
BLOCS = {"APU", "CDU"}

# The seats printed in the scanned maps' "mandatos" columns, which the OCR engine cannot read (they are single digits
# between rules), transcribed from the page images: a list's seats by circle code, and under "total" the figure in the
# map's row of totals. TOTAL_SEATS is the map's last column, the seats of each circle (the 1976 map has none).
# Both are checked against each other, against the list votes by the D'Hondt method, and against the elected lists.
# fmt: off
SEATS: dict[int, dict[str, dict[str, int]]] = {
    1976: {
        'CDS': {'3103': 2, 'total': 2}, 'MRPP': {'total': 0}, 'PCP': {'total': 0},
        'PPD': {'3101': 2, '3102': 4, '3103': 9, '3104': 2, '3105': 2, '3106': 1, '3201': 1, '3107': 2, '3108': 3, '3109': 2, '3110': 1, 'total': 29},
        'PS': {'3103': 7, '3108': 1, 'total': 8}, 'UDP': {'3103': 1, '3104': 1, 'total': 2},
    },
    1980: {
        'APU': {'3103': 1, 'total': 1}, 'CDS': {'3103': 1, 'total': 1}, 'PCTP/MRPP': {'total': 0},
        'PS': {'3103': 5, 'total': 5},
        'PPD/PSD': {'3101': 2, '3102': 4, '3103': 13, '3104': 3, '3105': 2, '3106': 1, '3201': 1, '3107': 2, '3109': 2, '3110': 1, '3108': 4, 'total': 35},
        'UDA/PDA': {'total': 0}, 'UDP': {'3103': 1, '3104': 1, 'total': 2},
    },
    1984: {
        'APU': {'3103': 1, 'total': 1}, 'CDS': {'3103': 1, 'total': 1}, 'PCTP/MRPP': {'total': 0},
        'PS': {'3103': 5, '3108': 1, 'total': 6},
        'PPD/PSD': {'3101': 3, '3102': 5, '3103': 15, '3104': 3, '3105': 2, '3106': 1, '3201': 1, '3107': 2, '3109': 2, '3110': 2, '3108': 4, 'total': 40},
        'UDP': {'3103': 1, '3104': 1, 'total': 2},
    },
    1988: {
        'CDS': {'3103': 2, 'total': 2}, 'CDU': {'total': 0}, 'PCTP/MRPP': {'total': 0}, 'PDA': {'total': 0},
        'PPD/PSD': {'3101': 3, '3102': 5, '3103': 15, '3104': 3, '3105': 2, '3106': 1, '3201': 1, '3107': 3, '3108': 4, '3109': 2, '3110': 2, 'total': 41},
        'PS': {'3103': 6, '3108': 1, 'total': 7}, 'UDP': {'3103': 2, '3104': 1, 'total': 3},
    },
    1992: {
        'CDS': {'3103': 2, 'total': 2}, 'PCP/PEV': {'3103': 1, 'total': 1}, 'PDA': {'total': 0},
        'PPD/PSD': {'3101': 3, '3102': 5, '3103': 14, '3104': 3, '3105': 2, '3106': 1, '3201': 1, '3107': 2, '3108': 4, '3109': 2, '3110': 2, 'total': 39},
        'PS': {'3102': 1, '3103': 7, '3104': 2, '3107': 1, '3108': 1, 'total': 12}, 'PSN': {'3103': 1, 'total': 1},
        'UDP': {'3103': 2, 'total': 2},
    },
}
# fmt: on
# fmt: off
TOTAL_SEATS: dict[int, dict[str, int]] = {
    1980: {
        '3101': 2, '3102': 4, '3103': 21, '3104': 4, '3105': 2, '3106': 1, '3201': 1, '3107': 2, '3109': 2,
        '3110': 1, '3108': 4, 'total': 44,
    },
    1984: {
        '3101': 3, '3102': 5, '3103': 23, '3104': 4, '3105': 2, '3106': 1, '3201': 1, '3107': 2, '3109': 2,
        '3110': 2, '3108': 5, 'total': 50,
    },
    1988: {
        '3101': 3, '3102': 5, '3103': 25, '3104': 4, '3105': 2, '3106': 1, '3201': 1, '3107': 3, '3108': 5,
        '3109': 2, '3110': 2, 'total': 53,
    },
    1992: {
        '3101': 3, '3102': 6, '3103': 27, '3104': 5, '3105': 2, '3106': 1, '3201': 1, '3107': 3, '3108': 5,
        '3109': 2, '3110': 2, 'total': 57,
    },
}
# fmt: on

# Counts transcribed from the page images where the scan is too degraded for the OCR engine: for each column of a map,
# its eleven circles in the order the map prints them (ORDER_A) and then the map's row of totals; None where the map
# prints a dash. Every column and every row of these adds up to the map's own totals, which `check` enforces.
# fmt: off
MANUAL: dict[int, dict[str, list[int | None]]] = {
    1976: {
        'i': [8600, 14496, 65447, 10892, 5686, 2514, 2479, 8002, 13682, 6759, 4846, 143403],
        'v': [6794, 11180, 47291, 8192, 4574, 2090, 1863, 5898, 10317, 5508, 3558, 107265],
        'bn': [77, 171, 594, 117, 27, 13, 18, 129, 122, 36, 63, 1367],
        'votes:CDS': [696, 833, 6165, 200, 124, 112, 70, 189, 791, 531, 474, 10185],
        'votes:MRPP': [None, None, 293, None, None, None, None, None, 64, None, None, 357],
        'votes:PCP': [51, 128, 1289, 113, 32, 5, 30, 99, 137, 47, 28, 1959],
        'votes:PPD': [5433, 8096, 20826, 4337, 3994, 1746, 1022, 4766, 6902, 4294, 2547, 63963],
        'votes:PS': [488, 1607, 15861, 972, 356, 210, 710, 653, 2137, 559, 415, 23968],
        'votes:UDP': [49, 345, 2263, 2453, 41, 4, 13, 62, 164, 41, 31, 5466],
    },
    1988: {
        'i': [9595, 18755, 87726, 15718, 6441, 2889, 3344, 9528, 17630, 7969, 5745, 185340],
        'v': [6638, 12805, 58428, 9768, 4649, 2176, 2464, 6505, 12913, 5412, 3625, 125383],
        'b': [18, 57, 584, 54, 12, 15, 19, 41, 81, 30, 11, 922],
        'n': [70, 197, 615, 117, 31, 13, 21, 127, 148, 68, 37, 1444],
        'votes:CDS': [1118, 511, 4394, 268, 877, 312, 54, 648, 807, 637, 637, 10263],
        'votes:CDU': [48, 284, 1746, 86, 22, 15, 5, 85, 189, 39, 30, 2549],
        'votes:PCTP/MRPP': [None, None, 395, None, None, None, None, None, 101, None, None, 496],
        'votes:PDA': [None, None, 779, None, None, None, None, None, None, None, None, 779],
        'votes:PPD/PSD': [4776, 9482, 31905, 5599, 3479, 1634, 1328, 4846, 8379, 4211, 2546, 78185],
        'votes:PS': [563, 1730, 12571, 721, 154, 172, 1012, 588, 2869, 357, 321, 21058],
        'votes:UDP': [45, 544, 5439, 2923, 74, 15, 25, 170, 339, 70, 43, 9687],
    },
}
# fmt: on

# Where the engine reads too little of a map's totals and rows to place its columns: the centre of each column in pixels
# at DPI, read upright, from the page.
# fmt: off
GRID: dict[int, dict[str, float]] = {
    1988: {
        'i': 263, 'v': 308, 'vp': 346, 'b': 379, 'bp': 411, 'n': 447, 'np': 475, 'votes:CDS': 508, 'pct:CDS': 542,
        'seats:CDS': 571, 'votes:CDU': 597, 'pct:CDU': 630, 'seats:CDU': 658, 'votes:PCTP/MRPP': 688,
        'pct:PCTP/MRPP': 720, 'seats:PCTP/MRPP': 749, 'votes:PDA': 779, 'pct:PDA': 811, 'seats:PDA': 840,
        'votes:PPD/PSD': 869, 'pct:PPD/PSD': 902, 'seats:PPD/PSD': 931, 'votes:PS': 960, 'pct:PS': 993,
        'seats:PS': 1021, 'votes:UDP': 1050, 'pct:UDP': 1085, 'seats:UDP': 1117, 'total': 1141,
    },
}
# fmt: on

# Other places where a map's printed figures disagree with themselves without changing any count we store.
CAVEATS: dict[int, list[tuple[str, str]]] = {
    1976: [
        (
            "cds-seats",
            (
                "The table prints the CDS's two seats on the line of Câmara de Lobos, but the list of deputies elected "
                "that the map prints with it, the table's own total, and the D'Hondt count of the votes all put them in "
                "Funchal, where they are stored."
            ),
        )
    ],
}

# Numbers the OCR engine misreads, corrected from the page image: (year, circle code, cell) to the printed value. A
# cell is "i", "v", "b", "n", "bn", "votes:<label>", "seats:<label>", or "total".
FIXES: dict[tuple[int, str, str], int] = {
    (1980, "3106", "b"): 7,
    (1980, "3106", "votes:PCTP/MRPP"): 5,
    (1980, "3108", "n"): 229,
    (1984, "3201", "votes:UDP"): 9,
    (1984, "3201", "votes:PCTP/MRPP"): 5,
    (1992, "3103", "b"): 498,
}

# Where a map's own figures disagree, as printed: the part of the finding that `check` words, and the reason. The numbers
# are stored as printed and the finding is accepted, with a note on the election.
MISPRINTS: dict[int, list[tuple[str, str]]] = {
    1980: [
        (
            "votes:UDA/PDA: circles add up to 2122, the map prints 2212",
            (
                "The map's total for UDA/PDA reads 2 212, but its circles add up to 2 122, and the 1,71% printed beside it "
                "is 2 122 of the 124 062 voters (2 212 would be 1,78%)."
            ),
        )
    ],
    1992: [
        (
            "i: circles add up to 196598, the map prints 196589",
            (
                "The map's total of registered voters is 196 589, but its circles add up to 196 598. The 74,63% printed "
                "for Porto Santo is 2 604 of 3 489, not of the 3 498 printed there, so that row probably transposes "
                "two digits."
            ),
        )
    ],
    2000: [
        (
            "Ribeira Brava: lists add up to 6571, valid votes are 6758",
            (
                "The map prints 3 votes for the UDP in Ribeira Brava. The 2,74% printed beside it and the valid votes "
                "make it 190, and the map's total for the UDP, 6 210, includes 190."
            ),
        ),
        (
            "votes:UDP: circles add up to 6023, the map prints 6210",
            (
                "The map prints 3 votes for the UDP in Ribeira Brava; the 2,74% printed beside it is 190, which is what "
                "the map's total of 6 210 for the UDP needs."
            ),
        ),
    ],
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")
SHAPES_SOURCE = key("source", "geoboundaries.prt.adm2")


def party_id(label: str) -> str:
    return key("party", PARTY_OF[label])


# ---------------------------------------------------------------------------------------------------------------------
# Reading the maps


@dataclass
class Item:
    """A piece of printed text with its box, in pixels at DPI."""

    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    # The box on the page as stored, when the scan is turned and the box above is read upright.
    orig: tuple[float, float, float, float] | None = None

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def page_box(self) -> tuple[float, float, float, float]:
        return self.orig or (self.x0, self.y0, self.x1, self.y1)


@dataclass
class Token:
    kind: str  # int, pct, dash, junk
    text: str
    page: int
    box: tuple[float, float, float, float]  # read upright
    orig: tuple[float, float, float, float]  # on the page as stored

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def value(self) -> int:
        return int(self.text.replace(" ", ""))


@dataclass
class Cell:
    value: int
    page: int
    box: tuple[float, float, float, float]


@dataclass
class Circle:
    cells: dict[str, Cell] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)
    percents: dict[str, str] = field(default_factory=dict)
    total: int | None = None


def artifact(spec: Year) -> raw.Artifact:
    return raw.cached(SOURCE, f"{CNE}/{spec.pdf}")


def text_items(path: Path, page: int) -> list[Item]:
    """The words of a page with a text layer, with adjacent thousands groups joined."""
    words, _ = cne_acores.words(path)
    scale = DPI / 72
    found = [
        Item(w.text, w.page, w.x0 * scale, w.y0 * scale, w.x1 * scale, w.y1 * scale) for w in words if w.page == page
    ]
    found.sort(key=lambda w: (round(w.cy / 4), w.x0))
    out: list[Item] = []
    for item in found:
        last = out[-1] if out else None
        if (
            last is not None
            and abs(last.cy - item.cy) < 4
            and re.fullmatch(r"\d{1,3}( \d{3})*", last.text)
            and (re.fullmatch(r"\d{3}", item.text) or (re.fullmatch(r"\d{2}", item.text) and " " not in last.text))
            and item.x0 - last.x1 < 4.0 * scale / 1.5
        ):
            last.text += " " + item.text
            last.x1 = item.x1
            continue
        out.append(item)
    return name_lines(out)


def name_lines(items: list[Item]) -> list[Item]:
    """Each line's leading words, which make up a circle's name, as one item."""
    out: list[Item] = []
    line: list[Item] = []

    def flush() -> None:
        words = [w for w in line if re.search(r"[A-Za-zÀ-ÿ]", w.text)]
        numbers = [w for w in line if not re.search(r"[A-Za-zÀ-ÿ]", w.text)]
        if words and numbers and min(w.x0 for w in numbers) > max(w.x1 for w in words):
            out.append(
                Item(
                    " ".join(w.text for w in words), words[0].page, words[0].x0, words[0].y0, words[-1].x1, words[-1].y1
                )
            )
            out.extend(numbers)
        else:
            out.extend(line)

    for item in sorted(items, key=lambda i: (i.page, round(i.cy / 4), i.x0)):
        if line and (item.page != line[0].page or abs(item.cy - line[0].cy) > 4):
            flush()
            line = []
        line.append(item)
    flush()
    return out


def ocr_items(spec: Year, block: Block) -> list[Item]:
    """The text of a scanned page, read upright, with boxes mapped back to the page as stored."""
    source = artifact(spec)
    folder = DATA / "parsed" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / f"ocr-{source.sha256}-{block.page}.json"
    if not cache.exists():
        import importlib

        import cv2

        engine: Any = importlib.import_module("rapidocr_onnxruntime").RapidOCR()  # optional: `uv sync --group scans`
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", "-f", str(block.page), "-l", str(block.page)]
                + [str(source.path), f"{tmp}/p"],
                check=True,
            )
            image = cv2.imread(str(next(Path(tmp).glob("p-*.png"))), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"{spec.year} page {block.page}: the rendered page cannot be read")
        height = int(image.shape[0])
        if block.turned:
            image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        found, _ = engine(image, box_thresh=0.3, text_score=0.3)
        out: list[list[object]] = []
        for corners, text, _confidence in found or []:
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if block.turned:  # back to the page as printed: (x, y) = (y', height - x')
                x0, y0, x1, y1 = y0, height - x1, y1, height - x0
            out.append([x0, y0, x1, y1, text])
        cache.write_text(json.dumps({"height": height if block.turned else 0, "items": out}))
    body = json.loads(cache.read_text())
    scale = DPI / OCR_DPI
    height = body["height"] * scale
    items = []
    for x0, y0, x1, y1, text in body["items"]:
        x0, y0, x1, y1 = x0 * scale, y0 * scale, x1 * scale, y1 * scale
        if block.turned:  # read upright: (x', y') = (height - y, x)
            items.append(Item(text, block.page, height - y1, x0, height - y0, x1, (x0, y0, x1, y1)))
        else:
            items.append(Item(text, block.page, x0, y0, x1, y1))
    return items


NUMBER = re.compile(r"^\d{1,3}( \d{3})+$|^\d+$")
PERCENT = re.compile(r"^\d{1,3}[,.]\d{1,3}$")
DASHES = {"–", "-", "—", "−", "‒", "_"}


def clean(text: str) -> str:
    text = text.strip().strip("|!:;.·'`").strip()
    swaps = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "i": "1", "|": "1", "S": "5", "$": "5"})
    if re.fullmatch(r"[\dOolIiS|$]+( [\dOolIiS|$]+)*([,.][\dOolIiS|$]+)?", text) and re.search(r"[\d]|^[Iil|]$", text):
        text = text.translate(swaps)
    return text


def tokens_of(item: Item) -> list[Token]:
    text = re.sub(r"(\d),\s+(\d)", r"\1,\2", clean(item.text))
    box = (item.x0, item.y0, item.x1, item.y1)
    orig = item.page_box
    if text in DASHES:
        return [Token("dash", text, item.page, box, orig)]
    if NUMBER.match(text):
        return [Token("int", text, item.page, box, orig)]
    if PERCENT.match(text):
        return [Token("pct", text.replace(".", ","), item.page, box, orig)]
    if re.fullmatch(r"\d{1,3} \d{2}", text) and item.orig is None:  # a printed comma the text layer lost
        return [Token("pct", text.replace(" ", ","), item.page, box, orig)]
    pieces = text.split()
    if len(pieces) > 1:
        width = (item.x1 - item.x0) / len(text)
        out: list[Token] = []
        at = 0
        for piece in pieces:
            start = text.index(piece, at)
            at = start + len(piece)
            sub = Item(piece, item.page, item.x0 + start * width, item.y0, item.x0 + at * width, item.y1, None)
            out += tokens_of(sub)
        return out
    return [Token("junk", text, item.page, box, orig)]


def label_of(text: str) -> str:
    text = re.sub(r"^\d+\s+", "", text.strip())
    text = re.sub(r"[\s.·_,…-]+$", "", text)
    return fold(text)


def circle_code(text: str) -> str | None:
    """The circle a printed name stands for, allowing for the OCR engine's slips, or 'total' for the totals' row."""
    label = label_of(text)
    if label in BY_NAME:
        return BY_NAME[label]
    if label.startswith("totais"):
        return "total"
    if len(label) < 5:
        return None
    best = max([*BY_NAME, "totais"], key=lambda name: difflib.SequenceMatcher(None, label, name).ratio())
    if difflib.SequenceMatcher(None, label, best).ratio() >= 0.8:
        return "total" if best == "totais" else BY_NAME[best]
    return None


def join_thousands(cells: list[Item]) -> list[Item]:
    """Adjacent digit groups the engine read apart ("57" and "103") as one number."""
    out: list[Item] = []
    for item in sorted(cells, key=lambda i: i.x0):
        last = out[-1] if out else None
        split_percent = (
            last is not None
            and re.fullmatch(r"\d{1,3},", last.text.strip())
            and re.fullmatch(r"\d{1,2}", item.text.strip())
        )
        if (
            last is not None
            and item.x0 - last.x1 < (10 if split_percent else 6)
            and (
                split_percent
                or (re.fullmatch(r"\d{1,3}( \d{3})*", clean(last.text)) and re.fullmatch(r"\d{3}", clean(item.text)))
            )
        ):
            boxes = [last.page_box, item.page_box]
            last.text = (
                last.text.strip() + item.text.strip() if split_percent else f"{clean(last.text)} {clean(item.text)}"
            )
            last.x1 = item.x1
            last.orig = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            continue
        out.append(Item(item.text, item.page, item.x0, item.y0, item.x1, item.y1, item.orig))
    return out


def find_rows(items: list[Item], band: int = 0, order: tuple[str, ...] = ()) -> dict[str, list[Token]]:
    """Each circle's cell tokens, and the totals', from the lines that start with a circle's name."""
    named: list[tuple[str, Item]] = []
    for item in items:
        code = circle_code(item.text)
        if code is not None:
            named.append((code, item))
    named.sort(key=lambda pair: pair[1].cy)
    totals = [item.cy for code, item in named if code == "total"]
    if len(totals) > 1:  # the map repeats its rows in bands, each ending in a row of totals
        low = totals[band - 1] + 1 if band else float("-inf")
        high = totals[band] + 1 if band < len(totals) else float("inf")
        named = [(code, item) for code, item in named if low < item.cy < high]
    # A name also printed in a list of the elected is not at the left edge of a table row; keep the left-most of each.
    best: dict[str, Item] = {}
    for code, item in named:
        count = sum(
            1
            for other in items
            if abs(other.cy - item.cy) < 5
            and other.x0 > item.x1
            and (re.search(r"\d", other.text) or clean(other.text) in DASHES)
        )
        if count >= 4 and (code not in best or item.x0 < best[code].x0):
            best[code] = item
    if order and 4 <= len(best) < 12:  # the rows are evenly spaced: place the ones whose names were not read
        known = [(k, best[code].cy) for k, code in enumerate([*order, "total"]) if code in best]
        n = len(known)
        mean_k, mean_y = sum(k for k, _ in known) / n, sum(y for _, y in known) / n
        slope = sum((k - mean_k) * (y - mean_y) for k, y in known) / sum((k - mean_k) ** 2 for k, _ in known)
        anchor = sorted(b.x1 for b in best.values())[len(best) // 2]
        page = next(iter(best.values())).page
        for k, code in enumerate([*order, "total"]):
            if code not in best:
                y = mean_y + slope * (k - mean_k)
                best[code] = Item("", page, anchor - 20, y - 4, anchor, y + 4)
    spacing = sorted(b.cy for b in best.values())
    gap = min((b - a for a, b in itertools.pairwise(spacing) if b - a > 3), default=12)
    # A scan that is not quite square drifts down (or up) across the page: find the slope that lines the cells up with
    # the rows.
    numeric = [i for i in items if re.search(r"\d", i.text) and not re.search(r"[A-Za-zÀ-ÿ]{3}", i.text)]
    numeric = [i for i in numeric if i.x0 > min(b.x1 for b in best.values())]

    def drift(slope: float) -> float:
        return sum(min(min(abs(i.cy - slope * (i.cx - a.cx) - a.cy) for a in best.values()), gap / 2) for i in numeric)

    slope = min((k / 2000 for k in range(-40, 41)), key=drift) if numeric else 0.0
    rows: dict[str, list[Token]] = {}
    for code, anchor in best.items():
        cells = [
            i
            for i in items
            if abs(i.cy - slope * (i.cx - anchor.cx) - anchor.cy) < gap / 2
            and i.x0 >= anchor.x1 - 1
            and not re.search(r"[A-Za-zÀ-ÿ]{3}", i.text)
            and (re.search(r"\d", i.text) or clean(i.text) in DASHES)
        ]
        rows[code] = [t for i in join_thousands(cells) for t in tokens_of(i)]
    return rows


def columns_of(block: Block) -> list[tuple[str, str]]:
    cols = list(block.lead)
    for party in block.parties:
        cols += [(f"votes:{party}", "int"), (f"pct:{party}", "pct"), (f"seats:{party}", "seat")]
    if block.total:
        cols.append(("total", "int"))
    return cols


def grid_of(spec: Year, block: Block, totals: list[Token]) -> dict[str, float]:
    """The centre of each column, from the row of totals, which has a printed number in every column but the seats of
    a list that won none."""
    cols = columns_of(block)
    toks = list(totals)
    centres: dict[str, float] = {}
    i = 0

    def fail(name: str) -> ValueError:
        shown = " ".join(t.text for t in toks)
        return ValueError(f"{spec.year}: cannot place {name} in the totals row: {shown}")

    for index, (name, kind) in enumerate(cols):
        if kind in ("int",) or name == "total":
            if i >= len(toks) or toks[i].kind != "int":
                raise fail(name)
            centres[name] = toks[i].cx
            i += 1
        elif kind == "pct":
            if i < len(toks) and toks[i].kind == "pct":
                centres[name] = toks[i].cx
                i += 1
            elif (
                i + 1 < len(toks) and toks[i].kind == "int" and toks[i + 1].kind == "int" and len(toks[i + 1].text) == 2
            ):
                centres[name] = (toks[i].cx + toks[i + 1].cx) / 2  # a printed comma lost from the text layer
                i += 2
            else:
                raise fail(name)
        else:  # seats
            last = name == cols[-2][0] if block.total else name == cols[-1][0]
            left = [t for t in toks[i:] if t.kind == "int"]
            if i < len(toks) and toks[i].kind == "dash":
                centres[name] = toks[i].cx
                i += 1
            elif last:
                if len(left) > (1 if block.total else 0):
                    centres[name] = toks[i].cx
                    i += 1
            elif i + 1 < len(toks) and toks[i].kind == "int" and toks[i + 1].kind == "int" and len(toks[i].text) <= 2:
                centres[name] = toks[i].cx
                i += 1
    gaps = [centres[name] - centres[name.replace("seats", "pct")] for name in centres if name.startswith("seats:")]
    delta = sorted(gaps)[len(gaps) // 2] if gaps else 28.0
    for name, kind in cols:
        if kind == "seat" and name not in centres:
            centres[name] = centres[name.replace("seats", "pct")] + delta
    return centres


def assign(
    spec: Year, block: Block, rows: dict[str, list[Token]], centres: dict[str, float]
) -> tuple[dict[str, dict[str, Token]], list[str]]:
    """Each token of each row to the nearest column of its kind."""
    kinds = dict(columns_of(block))
    problems: list[str] = []
    placed: dict[str, dict[str, Token]] = {}
    for _ in range(2):
        placed = {}
        for code, toks in rows.items():
            cells: dict[str, Token] = {}
            for tok in toks:
                if tok.kind == "dash":
                    continue
                choices = []
                for name, x in centres.items():
                    cost = abs(tok.cx - x)
                    is_pct = kinds[name] == "pct"
                    if tok.kind != "junk" and (tok.kind == "pct") != is_pct:
                        cost += 40
                    choices.append((cost, name))
                cost, name = min(choices)
                if kinds[name] == "pct" and tok.kind != "pct":
                    continue  # a percentage the text layer or the engine garbled; percentages are only checked against counts
                if cost > 30:
                    problems.append(f"{spec.year} {code}: {tok.text!r} sits {cost:.0f}px from the nearest column")
                if name in cells:
                    problems.append(f"{spec.year} {code}: two tokens in {name}: {cells[name].text!r}, {tok.text!r}")
                    continue
                cells[name] = tok
            placed[code] = cells
        # Settle each column on where its tokens sit.
        for name in centres:
            xs = sorted(cells[name].cx for cells in placed.values() if name in cells)
            if len(xs) >= 3:
                centres[name] = xs[len(xs) // 2]
    return placed, problems


def read_block(spec: Year, block: Block) -> tuple[dict[str, Circle], list[str]]:
    source = artifact(spec)
    items = ocr_items(spec, block) if spec.scan else text_items(source.path, block.page)
    rows = find_rows(items, block.band, spec.order)
    missing = [c for c in [*CIRCLES, "total"] if c not in rows]
    if missing:
        raise ValueError(f"{spec.year} page {block.page}: no row for {missing}")
    centres: dict[str, float] | None = dict(GRID[spec.year]) if spec.year in GRID else None
    tried = [] if centres else ["total", *sorted((c for c in rows if c != "total"), key=lambda c: -len(rows[c]))]
    for code in tried:  # the totals' row, or failing that the fullest row, places the columns
        try:
            found = grid_of(spec, block, rows[code])
        except ValueError, KeyError:
            continue
        if list(found.values()) == sorted(found.values()) or all(
            a < b for a, b in itertools.pairwise(sorted(found.values()))
        ):
            order = [name for name, _ in columns_of(block)]
            if [found[name] for name in order] == sorted(found[name] for name in order):
                centres = found
                break
    if centres is None:
        raise ValueError(f"{spec.year} page {block.page}: cannot place the columns from any row")
    placed, problems = assign(spec, block, rows, centres)
    kinds = dict(columns_of(block))
    circles: dict[str, Circle] = {}
    for code, cells in placed.items():
        circle = circles.setdefault(code, Circle())
        for name, tok in cells.items():
            kind, _, label = name.partition(":")
            if tok.kind == "junk":
                problems.append(f"{spec.year} {code}: {name} reads {tok.text!r}")
                continue
            if kinds[name] == "pct":
                circle.percents[label or name] = tok.text
            elif kind == "seats":
                circle.seats[label] = tok.value
            elif name == "total":
                circle.total = tok.value
            else:
                circle.cells[name] = Cell(tok.value, tok.page, tok.orig)
    sizes = page_sizes(source.path)

    def put(code: str, cell: str, value: int | None) -> None:
        circle = circles[code]
        if value is None:
            circle.cells.pop(cell, None)
            return
        old = circle.cells.get(cell)
        if old is None or old.value != value:  # the box is where the column and the row cross
            ys = [t.box[1] / 2 + t.box[3] / 2 for t in rows[code]]
            x, y = centres[cell], sum(ys) / len(ys)
            box = (x - 10, y - 5, x + 10, y + 5)
            if block.turned:
                height = sizes[block.page][1]
                box = (box[1], height - box[2], box[3], height - box[0])
            old = Cell(value, block.page, box) if old is None or old.value != value else old
        circle.cells[cell] = Cell(value, old.page, old.box)

    for cell, column in MANUAL.get(spec.year, {}).items():
        if cell in centres:
            for code, value in zip([*spec.order, "total"], column, strict=True):
                put(code, cell, value)
    for (year, code, cell), value in FIXES.items():
        if year == spec.year and cell in centres:
            put(code, cell, value)
    if spec.year in MANUAL:  # the engine's slips are no matter where the counts are transcribed
        problems = []
    return circles, [f"{p}" for p in problems]


def read(spec: Year) -> tuple[dict[str, Circle], list[str]]:
    """The circles of one election, merged across the blocks of its map, with the problems met on the way."""
    merged: dict[str, Circle] = {}
    problems: list[str] = []
    for block in spec.blocks:
        circles, found = read_block(spec, block)
        problems += found
        for code, circle in circles.items():
            into = merged.setdefault(code, Circle())
            into.cells.update(circle.cells)
            into.seats.update(circle.seats)
            into.percents.update(circle.percents)
            if circle.total is not None:
                into.total = circle.total
    if spec.year in SEATS:  # the scanned seat columns, from the page images
        for code, circle in merged.items():
            circle.seats = {
                label: SEATS[spec.year][label].get(code, 0)
                for label in labels_of(spec)
                if SEATS[spec.year][label].get(code)
            }
            if spec.year in TOTAL_SEATS:
                circle.total = TOTAL_SEATS[spec.year][code]
    return merged, problems


def labels_of(spec: Year) -> list[str]:
    return [label for block in spec.blocks for label in block.parties]


def check(spec: Year, circles: dict[str, Circle]) -> tuple[list[str], list[str]]:
    """Where the map's figures disagree, as (arithmetic, percentages). Arithmetic: each circle's lists against its valid
    votes and its seats against its printed total, and the eleven circles against the printed totals. Percentages: each
    printed percentage against its count."""
    hard: list[str] = []
    soft: list[str] = []
    labels = labels_of(spec)
    merged = spec.blocks[0].lead == LEAD3
    keys = ["i", "v", "bn"] if merged else ["i", "v", "b", "n"]
    for code, (name, _) in CIRCLES.items():
        c = circles[code]
        missing = [k for k in keys if k not in c.cells]
        if missing:
            hard.append(f"{spec.year} {name}: no {missing}")
            continue
        if c.cells["v"].value > c.cells["i"].value:
            hard.append(f"{spec.year} {name}: {c.cells['v'].value} voters, {c.cells['i'].value} registered")
        valid = c.cells["v"].value - sum(c.cells[k].value for k in keys[2:])
        listed = sum(c.cells[f"votes:{label}"].value for label in labels if f"votes:{label}" in c.cells)
        if listed != valid:
            hard.append(f"{spec.year} {name}: lists add up to {listed}, valid votes are {valid}")
        if c.total is not None and sum(c.seats.values()) != c.total:
            hard.append(f"{spec.year} {name}: seats add up to {sum(c.seats.values())}, the map prints {c.total}")
        base = valid if spec.base == "valid" else c.cells["v"].value
        counts = [("v", c.cells["i"].value), *((k, base) for k in keys[2:]), *((f"votes:{lb}", base) for lb in labels)]
        for cell, denominator in counts:
            label = cell.removeprefix("votes:")
            shown = c.percents.get({"v": "vp", "b": "bp", "n": "np"}.get(cell, label))
            if shown is None or cell not in c.cells or denominator == 0:
                continue
            want = 100 * c.cells[cell].value / denominator
            if abs(want - float(shown.replace(",", "."))) > 0.0151:
                soft.append(
                    f"{spec.year} {name}: {cell} is {c.cells[cell].value}, {want:.2f}% against the printed {shown}"
                )
        # The engine's D'Hondt count of the printed votes gives the printed seats.
        seats = sum(c.seats.values())
        votes = {lb: c.cells[f"votes:{lb}"].value for lb in labels if f"votes:{lb}" in c.cells}
        quotients = sorted(((v / (n + 1), lb) for lb, v in votes.items() for n in range(seats)), reverse=True)
        won: dict[str, int] = {}
        for _, lb in quotients[:seats]:
            won[lb] = won.get(lb, 0) + 1
        if won != {lb: n for lb, n in c.seats.items() if n}:
            hard.append(f"{spec.year} {name}: seats are {c.seats}, D'Hondt on the votes gives {won}")
    total = circles["total"]
    for k in [*keys, *(f"votes:{label}" for label in labels)]:
        if k not in total.cells:
            continue
        summed = sum(circles[code].cells[k].value for code in CIRCLES if k in circles[code].cells)
        if summed != total.cells[k].value:
            hard.append(f"{spec.year} {k}: circles add up to {summed}, the map prints {total.cells[k].value}")
    for label in labels:
        summed = sum(circles[code].seats.get(label, 0) for code in CIRCLES)
        if summed != total.seats.get(label, 0):
            hard.append(
                f"{spec.year} seats {label}: circles add up to {summed}, the map prints {total.seats.get(label, 0)}"
            )
    if total.total is not None:
        summed = sum(circles[code].total or 0 for code in CIRCLES)
        if summed != total.total:
            hard.append(f"{spec.year} total seats: circles add up to {summed}, the map prints {total.total}")
    return hard, soft


def fetch() -> None:
    for spec in YEARS:
        raw.fetch(SOURCE, f"{CNE}/{spec.pdf}", suffix=".pdf")
    raw.fetch(SOURCE, SHAPES, suffix=".geojson")


# ---------------------------------------------------------------------------------------------------------------------
# Packages

METHOD = (
    "Counted by electoral circle: one for each of the eleven municipalities, as published in the National Elections "
    "Commission's official map of results (mapa oficial), which the Commission also prints in the Diário da "
    "República. The map goes no finer than the circle. Registered voters are the voters on the roll, the voters are "
    "those who voted, and blank and invalid (nulos) ballots are printed apart from the votes for each list{merged}. "
    "Seats are as the map declares them."
)
MERGED = " (the 1976 map prints blank and invalid ballots as one count)"


def artifact_id(spec: Year) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CNE_SOURCE}\x1f{artifact(spec).storage_key}"))


def day_key(spec: Year) -> str:
    return f"{SOURCE}/{spec.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(day_key(spec) for spec in YEARS)]


def page_sizes(path: Path) -> dict[int, tuple[int, int]]:
    """Each page's size in pixels at DPI, as `pdftoppm` renders it (a turned page swaps its sides)."""
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "99", str(path)], capture_output=True, text=True, check=True)
    sizes: dict[int, tuple[float, float]] = {}
    turns: dict[int, int] = {}
    for line in out.stdout.splitlines():
        if m := re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line):
            sizes[int(m[1])] = (float(m[2]), float(m[3]))
        elif m := re.match(r"Page\s+(\d+) rot:\s+(\d+)", line):
            turns[int(m[1])] = int(m[2])
    scale = DPI / 72
    return {
        n: (round(h * scale), round(w * scale)) if turns.get(n, 0) % 180 else (round(w * scale), round(h * scale))
        for n, (w, h) in sizes.items()
    }


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Shapes of Madeira's eleven municipalities, each one an electoral circle of the Legislative Assembly, from "
        "geoBoundaries",
        depends=["pt.mai/geo"],
    )
    shapes = raw.cached(SOURCE, SHAPES)
    p.add(
        "source",
        id=SHAPES_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Portugal ADM2 (municipalities, 2020), simplified",
        url="https://www.geoboundaries.org/",
        license="Creative Commons CC0 1.0 Universal (public domain dedication)",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="geoBoundaries (William & Mary geoLab)",
    )
    p.artifact(shapes, SHAPES_SOURCE)
    p.add(
        "boundary_set",
        id=key("boundary_set", "pt.concelho", 2020),
        scheme_code="pt.concelho",
        vintage="2020",
        as_of=shapes.retrieved_at.date(),
    )
    found: dict[str, Any] = {}
    for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]:
        shape = shapely.geometry.shape(feature["geometry"])
        x, y = shape.representative_point().x, shape.representative_point().y
        if -17.5 < x < -16.0 and 32.3 < y < 33.3:  # Madeira's, not the Azores' namesakes
            found[SHAPE_NAMES[fold(feature["properties"]["shapeName"])]] = shape
    missing = sorted(set(CIRCLES) - set(found))
    if missing:
        raise ValueError(f"no shape for {missing}")
    for code in sorted(found):
        outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(shapely.make_valid(found[code])))), 4326)
        p.add(
            "area_geometry",
            area_id=town_area(code),
            valid_from=date(1976, 6, 27),
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The lists and coalitions of the Madeira regional elections of 1976 to 2004, and the National Elections "
        "Commission as their source",
        depends=["pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Comissão Nacional de Eleições",
        title="Mapas oficiais dos resultados das eleições para a Assembleia Legislativa da Região Autónoma da Madeira",
        url="https://www.cne.pt/content/eleicoes-referendos-resultados-oficiais",
        license="Official publication in the Diário da República; reuse terms are not stated",
        attribution="Comissão Nacional de Eleições",
    )
    for spec in YEARS:
        p.artifact(artifact(spec), CNE_SOURCE)
    ran = dict.fromkeys(PARTY_OF[label] for spec in YEARS for label in labels_of(spec))
    for party in ran:
        id = key("party", party)
        p.add("party", id=id, kind="bloc" if party in BLOCS else "party", polity_id=PORTUGAL)
        p.add("external_id", entity_id=id, scheme="pt.cne_madeira.party", value=party)
        for text, preferred in NAMES[party]:
            p.name(id, text, "pt", "official", preferred)
        p.name(id, party, "pt", "abbreviation", False)
    return p


def _result_set(p: Package, id: str, election_id: str, contest: str, method: str) -> None:
    p.add(
        "result_set",
        id=id,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CNE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")


Row = tuple[str, str, str | None, int, int]
Locator = tuple[str, str, str | None, int, str, float, float, float, float]


def _value(
    values: list[Row],
    locators: list[Locator],
    page_ids: dict[int, str],
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    cell: Cell,
) -> None:
    values.append((result_set, unit, option, measure, cell.value))
    x0, y0, x1, y1 = (round(c, 1) for c in cell.box)
    locators.append((result_set, unit, option, measure, page_ids[cell.page], x0, y0, x1, y1))


def election(spec: Year) -> Package:
    day = spec.day
    p = Package(
        day_key(spec),
        SOURCE,
        f"The Madeira regional election of {day} by municipal circle, from the National Elections Commission's "
        "official map of results",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo", "pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    stored = artifact(spec)
    p.uses(stored)
    circles, problems = read(spec)
    hard, soft = check(spec, circles)
    known = {finding: reason for finding, reason in MISPRINTS.get(spec.year, [])}
    expected = [h for h in hard if any(finding in h for finding in known)]
    hard = [h for h in hard if h not in expected]
    if problems or hard:
        raise ValueError("\n".join([*problems, *hard]))
    merged = spec.blocks[0].lead == LEAD3
    labels = labels_of(spec)
    election_id = key("election", day)
    path = f"pt/{day}-madeira-regional"
    p.add("election", id=election_id, polity_id=PORTUGAL, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Madeira regional election", "en", "official")
    p.name(election_id, f"Eleições legislativas regionais da Madeira de {day.year}", "pt", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    page_ids: dict[int, str] = {}
    for number, (width, height) in sorted(page_sizes(stored.path).items()):
        if number not in {b.page for b in spec.blocks}:
            continue
        page_ids[number] = key("page", day, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(spec),
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=width,
            height=height,
        )
    values: list[Row] = []
    locators: list[Locator] = []
    for code, (name, portuguese) in sorted(CIRCLES.items(), key=lambda item: item[1][0]):
        circle = circles[code]
        contest = key("contest", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=MADEIRA_ASSEMBLY,
            district_area_id=town_area(code),
            magnitude=sum(circle.seats.values()),
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        _result_set(p, result_set, election_id, contest, METHOD.format(merged=MERGED if merged else ""))
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PORTUGAL,
            scheme_code="pt.circulo",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=town_area(code),
            kind="aggregate",
        )
        p.name(unit, portuguese, "pt", "official")
        options: dict[str, str] = {}
        for position, label in enumerate((lb for lb in labels if f"votes:{lb}" in circle.cells), 1):
            option = options[label] = key("option", day, code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id(label),
                ballot_position=position,
                ballot_label=label,
            )
        measured = [(REGISTERED, circle.cells["i"]), (CAST, circle.cells["v"])]
        if merged:
            measured.append((INVALID, circle.cells["bn"]))
        else:
            measured += [(BLANK, circle.cells["b"]), (INVALID, circle.cells["n"])]
        for measure, cell in measured:
            _value(values, locators, page_ids, result_set, unit, None, measure, cell)
        for label, option in options.items():
            _value(values, locators, page_ids, result_set, unit, option, VOTES, circle.cells[f"votes:{label}"])
            if circle.seats.get(label):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=circle.seats[label])
    cne_acores._frames(p, values, locators)
    for finding, reason in [*MISPRINTS.get(spec.year, []), *CAVEATS.get(spec.year, [])]:
        p.add(
            "note",
            id=key("note", day, finding),
            entity_id=election_id,
            kind="caveat",
            body_md=f"The printed map is inconsistent here, and the figures are stored as printed. {reason}",
            lang="en",
        )
    for line in soft:
        print("percentage:", line)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for spec in YEARS:
        election(spec).write()


def emit() -> None:
    package.run("load", packages())
