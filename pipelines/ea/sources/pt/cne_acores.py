"""The Azores regional elections of 1976 to 2020, by electoral circle, from the National Elections Commission's maps.

After each election the National Elections Commission (CNE) publishes the official map of results (mapa oficial) in the
Diário da República and on its own site. The map gives, for each of the nine island circles, the registered voters, the
voters, the blank and invalid ballots, each list's votes and its seats, and, from 2008, the seats of the regional
compensation circle. It does not go below the circle. The Ministry's results site carries only 2007 onward for Madeira
and 2024 for the Azores, so these six elections have no parish-level source here.

The maps of 1996 and later have a text layer. `pdftotext -bbox` gives each word with its box; the words of a row become
tokens, and every stored number keeps the page and the box of the token it was read from. The maps of 1976 to 1992 are
scans of the Diário da República. Their figures are keyed below from the page images and checked against the map's own
sums: each circle's lists, blank, and invalid ballots against its voters, the circles against the printed totals, and the
circles' seats against the printed seats. Each keyed value's box on the page comes from `locate`, which reads the page
with OCR and writes `cne_acores_boxes.json`; a cell the OCR missed takes the box of its column and row. The 1976 and 1992
maps share a Diário da República supplement with the Madeira map.
"""

import importlib
import json
import re
import statistics
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.pt.mai import (
    AZORES_ASSEMBLY,
    PORTUGAL,
    SYSTEM_LIST,
    circle_area,
    district_area,
)
from ea.store import DATA

SOURCE = "pt.cne_acores"
CNE = "https://www.cne.pt/sites/default/files/dl"
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6

BOXES = Path(__file__).with_name("cne_acores_boxes.json")

ISLAND_NAMES = {
    "49": ("Corvo", "Corvo"),
    "47": ("Faial", "Faial"),
    "48": ("Flores", "Flores"),
    "44": ("Graciosa", "Graciosa"),
    "46": ("Pico", "Pico"),
    "41": ("Santa Maria", "Santa Maria"),
    "45": ("São Jorge", "São Jorge"),
    "42": ("São Miguel", "São Miguel"),
    "43": ("Terceira", "Terceira"),
}
# The order of the 2020 map's columns.
COLUMNS_2020 = ["49", "47", "48", "44", "46", "41", "45", "42", "43"]


@dataclass(frozen=True)
class Year:
    year: int
    day: date
    pdf: str
    # Leading cells of a circle's row: i registered, v voters, b blank, n invalid, e valid votes, p a percentage.
    lead: str
    # Percentage cells after each list's votes.
    percents: int
    # Lists in the order of the columns, one tuple per block of the map; a list is its label on the map.
    blocks: tuple[tuple[str, ...], ...]
    magnitude_column: bool = False
    # Scanned maps: the lists in the map's column order, the keyed table, the page that holds it, how far the page is
    # turned (degrees, counterclockwise, to stand it upright), the columns on the first of two stacked blocks (0 for
    # one), and whether blank and invalid ballots are printed as one figure.
    lists: tuple[str, ...] = ()
    keyed: str = ""
    pages: tuple[int, ...] = ()
    rotate: int = 0
    split: int = 0
    joined: bool = False
    note: str = ""
    # Text-layer maps that print votes, percentages, and seats for every list, with dashes where a list did not run.
    fixed: bool = False


YEARS = [
    Year(
        1976,
        date(1976, 6, 27),
        "resultados_alras_1976.pdf",
        "",
        0,
        (),
        lists=("CDS", "MES", "MRPP", "PCP", "PPD", "PS"),
        keyed="""
Corvo 309 275 4 | - - - - 141/1 130/1
Faial 10898 8807 178 | 437 - - 180 5287/3 2725/1
Flores 2680 1864 43 | 134 - - - 1170/2 517/1
Graciosa 4324 2840 55 | 56 - - - 1967/2 762/1
Pico 11413 8263 149 | 178 - - - 5159/3 2777/1
Santa Maria 4766 3107 141 | 296 - 148 - 1885/2 637/1
São Jorge 7711 5774 128 | 1909/1 - - - 3401/2 336
São Miguel 80364 50826 1925 | 3722/1 - 367 1762 25374/7 17676/5
Terceira 40212 28070 557 | 1559 167 123 445 14730/5 10489/3
Totais 162677 109826 3180 | 8291/2 167 638 2387 59114/27 36049/14
""",
        pages=(2,),
        rotate=-90,
        joined=True,
    ),
    Year(
        1980,
        date(1980, 10, 5),
        "resultados_alraa_1980.pdf",
        "",
        0,
        (),
        lists=("APU", "CDS", "PCTP/MRPP", "PS", "PPD/PSD", "UDA/PDA", "UDP"),
        keyed="""
Corvo 292 238 1 3 | - 5 2 100/1 125/1 2 -
Faial 10796 8775 90 88 | 353 251 54 2580/1 5141/3 108 110
Flores 3130 2429 28 22 | 244 356 8 466/1 1228/2 65 12
Graciosa 4031 2952 27 69 | 18 31 15 796/1 1983/2 - 13
Pico 10579 8784 99 93 | 241 249 19 3166/1 4772/3 92 53
Santa Maria 4246 2812 33 68 | 65 799/1 20 1114/1 648/1 - 65
São Jorge 7122 5685 28 68 | 31 836 4 515 4036/3 99 68
São Miguel 77690 58611 630 1783 | 1982 1592 377 13563/3 35469/10 2125 1090
Terceira 38242 29962 278 551 | 928 1260 125 10490/3 15558/5 259 513
Totais 156128 120248 1214 2745 | 3862 5379/1 624 32790/12 68960/30 2750 1924
""",
        pages=(3,),
        rotate=90,
    ),
    Year(
        1984,
        date(1984, 10, 14),
        "resultados_alraa_1984.pdf",
        "",
        0,
        (),
        lists=("APU", "CDS", "PCTP/MRPP", "PPM", "PS", "PPD/PSD", "UDA/PDA", "UDP"),
        keyed="""
Corvo 285 232 2 4 | 12 - - - 79/1 134/1 - 1
Faial 11241 7939 82 74 | 608 680 40 - 2055/1 4320/3 - 80
Flores 3270 2253 34 33 | 201 523/1 8 - 444 997/2 - 13
Graciosa 4270 2886 12 51 | 26 108 11 - 788/1 1886/2 - 4
Pico 11736 8029 103 95 | 297 400 24 - 3029/2 4033/2 - 48
Santa Maria 4457 2260 12 36 | 63 396 19 - 841/2 812/1 58 23
São Jorge 7652 5458 23 44 | 43 1138 5 41 433 3709/3 - 22
São Miguel 84630 51258 432 1112 | 3198/1 1523 530 - 9871/3 32375/10 1610 607
Terceira 43486 26323 211 437 | 1195 3674/1 127 - 8295/3 11896/4 - 488
Totais 171027 106638 911 1886 | 5643/1 8442/2 764 41 25835/13 60162/28 1668 1286
""",
        pages=(3,),
        rotate=0,
    ),
    Year(
        1988,
        date(1988, 10, 9),
        "resultados_alraa_1988.pdf",
        "",
        0,
        (),
        lists=("CDS", "CDU", "PCTP/MRPP", "PDA", "PDC", "PPD/PSD", "PPM", "PS", "UDP"),
        keyed="""
Corvo 298 238 2 4 | 6 13 - - - 99/1 - 114/1 -
Faial 11471 7861 71 39 | 609 473 - - - 3682/2 15 2940/2 32
Flores 3489 2331 18 16 | 373 640/1 - - - 723/1 - 537/1 24
Graciosa 4384 2824 19 34 | 31 16 - - - 1747/2 - 960/1 17
Pico 12024 8024 92 51 | 407 203 - - - 3846/2 - 3379/2 46
Santa Maria 4569 2304 20 23 | - 34 - 42 26 1018/1 - 1120/2 21
São Jorge 8042 5558 14 41 | 1027/1 38 - - - 3058/2 - 1352/1 28
São Miguel 90495 50320 468 793 | 1923 2060 354 1367 444 26347/11 - 16153/7 411
Terceira 45442 26589 186 210 | 3096/1 576 85 - - 10983/4 147 11070/5 236
Totais 180214 106049 890 1211 | 7472/2 4053/1 439 1409 470 51503/26 162 37625/22 815
""",
        pages=(3,),
        rotate=-90,
        note=(
            "The map notes that São Jorge's figures were obtained after the vote was repeated on 30 October in the "
            "parish of Santo Antão."
        ),
    ),
    Year(
        1992,
        date(1992, 10, 11),
        "resultados_alras_1992.pdf",
        "",
        0,
        (),
        lists=("AD-A", "PCP/PEV", "PDA", "PDC", "PPD/PSD", "PS"),
        keyed="""
Corvo 300 247 4 5 | 6 40 4 - 104/1 84/1
Faial 11782 7795 56 32 | 299 340 55 - 3801/2 3212/2
Flores 3375 2478 7 22 | 282 505/1 21 - 997/1 644/1
Graciosa 4193 2842 10 36 | 195 10 52 - 1474/2 1065/1
Pico 11906 8319 61 40 | 335 125 60 - 4067/2 3631/2
Santa Maria 4365 2352 8 21 | 113 15 18 24 971/1 1182/2
São Jorge 8111 5470 21 28 | 658 53 60 - 3310/3 1340/1
São Miguel 94089 56681 247 855 | 591 1240 883 - 34692/12 18173/6
Terceira 45356 27839 170 294 | 2738/1 298 457 - 11694/4 12188/5
Totais 183477 114023 584 1333 | 5217/1 2626/1 1610 24 61110/28 41519/21
""",
        pages=(3,),
        rotate=0,
        split=6,
    ),
    Year(
        1996,
        date(1996, 10, 13),
        "resultados_alraa_1996.pdf",
        "ivpbpnp",
        1,
        (("CDS-PP", "PCP-PEV"), ("PDA", "PPD/PSD", "PS", "UDP")),
        fixed=True,
    ),
    Year(
        2000,
        date(2000, 10, 15),
        "resultados_alraa_2000.pdf",
        "ivpbpnp",
        1,
        (("BE", "CDS-PP", "PCP-PEV", "PPD/PSD", "PPM/PDA", "PS"),),
    ),
    Year(
        2004,
        date(2004, 10, 17),
        "resultados_alraa_2004.pdf",
        "ivpbpnp",
        1,
        (("BE", "MPT", "PCP-PEV", "PDA", "PSD/PPD.CDS-PP", "PPM", "PS"),),
        True,
    ),
    Year(
        2008,
        date(2008, 10, 19),
        "resultados_alraacores_2008.pdf",
        "ivpbpnp",
        1,
        (("BE", "CDS-PP", "MPT", "PCP-PEV", "PDA", "PPD/PSD", "PPM", "PS"),),
        True,
    ),
    Year(
        2012,
        date(2012, 10, 14),
        "resultados_alraa_2012_mapa.pdf",
        "ivpbpnpe",
        2,
        (
            ("BE", "CDS-PP", "MPT", "PAN", "PCP-PEV"),
            ("PCTP/MRPP", "PDA", "PPD/PSD", "PPM", "PPM-PDA", "PS", "PTP"),
        ),
        True,
    ),
    Year(
        2016,
        date(2016, 10, 16),
        "alraa_2016_mapa_oficial_resultados_6_2016.pdf",
        "ivpebpnp",
        2,
        (
            ("BE", "CDS-PP", "L/TDA", "MAS", "MPT", "PAN"),
            ("PCP-PEV", "PCTP/MRPP", "PDR", "PPD/PSD", "PPM", "PS", "PURP"),
        ),
    ),
    Year(2020, date(2020, 10, 25), "2020_alraa_mapa_oficial_resultados.pdf", "", 0, ()),
]

# Party names from each map's legend.
NAMES = {
    "A": "Aliança",
    "AD-A": "Aliança Democrática-Açores",
    "APU": "Aliança Povo Unido",
    "BE": "Bloco de Esquerda",
    "CDS-PP": "CDS — Partido Popular",
    "CH": "Chega",
    "IL": "Iniciativa Liberal",
    "L": "LIVRE",
    "L/TDA": "LIVRE/Tempo de Avançar",
    "MAS": "Movimento Alternativa Socialista",
    "MES": "Movimento de Esquerda Socialista",
    "MPT": "Partido da Terra",
    "PAN": "Pessoas-Animais-Natureza",
    "PCP": "Partido Comunista Português",
    "PCP-PEV": "Coligação Democrática Unitária",
    "PCTP/MRPP": "Partido Comunista dos Trabalhadores Portugueses",
    "PDA": "Partido Democrático Atlântico",
    "PDC": "Partido da Democracia Cristã",
    "PDR": "Partido Democrático Republicano",
    "PPD/PSD": "Partido Social Democrata",
    "PPM": "Partido Popular Monárquico",
    "PPM-PDA": "Plataforma de Cidadania",
    "PPM.CDS-PP": "Mais Corvo",
    "PPM/PDA": "Convergência Democrática Açoriana",
    "PS": "Partido Socialista",
    "PSD/PPD.CDS-PP": "Coligação Açores",
    "PTP": "Partido Trabalhista Português",
    "PURP": "Partido Unido dos Reformados e Pensionistas",
    "UDP": "União Democrática Popular",
}
OLDER = {"CDS-PP": "Centro Democrático Social", "PPD/PSD": "Partido Popular Democrático"}
BLOCS = {"AD-A", "APU", "PCP-PEV", "PPM/PDA", "PPM-PDA", "PPM.CDS-PP", "PSD/PPD.CDS-PP"}
# The party each printed label stands for, one key for the labels the maps use for the same party or coalition.
PARTY_OF = {
    "CDS": "CDS-PP",
    "CDU": "PCP-PEV",
    "MRPP": "PCTP/MRPP",
    "PCP/PEV": "PCP-PEV",
    "PPD": "PPD/PSD",
    "UDA/PDA": "PDA",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")
COMPENSATION = "compensation"
SCAN_NOTE_CIRCLE = "45"  # São Jorge


# ---------------------------------------------------------------------------------------------------------------------
# Reading the maps


@dataclass
class Word:
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Token:
    kind: str  # int, pct, dash, nc, dec, text
    text: str
    page: int
    box: tuple[float, float, float, float]

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
    votes: dict[str, Cell] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)


WORD = re.compile(
    r'<page width="([\d.]+)" height="([\d.]+)">|<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>'
)


def words(path: Path) -> tuple[list[Word], dict[int, tuple[float, float]]]:
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found: list[Word] = []
    sizes: dict[int, tuple[float, float]] = {}
    page = 0
    for match in WORD.finditer(out):
        if match.group(1):
            page += 1
            sizes[page] = (float(match.group(1)), float(match.group(2)))
            continue
        text = match.group(7).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        found.append(
            Word(text, page, float(match.group(3)), float(match.group(4)), float(match.group(5)), float(match.group(6)))
        )
    return found, sizes


def rows(found: list[Word]) -> list[list[Word]]:
    """Words grouped into printed lines, by page and the middle of the word's height, each line left to right."""
    lines: list[list[Word]] = []
    for word in sorted(found, key=lambda w: (w.page, (w.y0 + w.y1) / 2, w.x0)):
        middle = (word.y0 + word.y1) / 2
        for line in reversed(lines[-6:]):
            if line[0].page == word.page and abs((line[0].y0 + line[0].y1) / 2 - middle) <= 3.3:
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


INT = re.compile(r"^\(?\d{1,3}\)?$|^\d+$")
GROUP = re.compile(r"^\d{3}$")
PCT = re.compile(r"^\(?\d+,\d{1,2}\)?$")
DEC = re.compile(r"^\(?\d+,\d{3,}\)?$")
DASHES = {"–", "-", "—", "−"}


def tokens(line: list[Word]) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(line):
        w = line[i]
        text = w.text.strip("()")
        box = (w.x0, w.y0, w.x1, w.y1)
        if w.text in DASHES:
            out.append(Token("dash", w.text, w.page, box))
        elif w.text == "n." and i + 1 < len(line) and line[i + 1].text == "c.":
            nxt = line[i + 1]
            out.append(Token("nc", "n.c.", w.page, (w.x0, w.y0, nxt.x1, nxt.y1)))
            i += 1
        elif w.text in ("n.c.", "n.c"):
            out.append(Token("nc", "n.c.", w.page, box))
        elif re.fullmatch(r"\d+", text):
            x1 = w.x1
            joined = text
            # Thousands are printed with a space: "13 013".
            while (
                i + 1 < len(line)
                and re.fullmatch(r"\d{3}", line[i + 1].text.strip("()"))
                and len(joined) <= 3
                and line[i + 1].x0 - x1 < 4.0
            ):
                joined += line[i + 1].text.strip("()")
                x1 = line[i + 1].x1
                i += 1
            out.append(Token("int", joined, w.page, (w.x0, w.y0, x1, w.y1)))
        elif PCT.match(w.text):
            out.append(Token("pct", text, w.page, box))
        elif DEC.match(w.text):
            out.append(Token("dec", text, w.page, box))
        elif re.fullmatch(r"\d{1,3}(,\d+)?", text):
            out.append(Token("pct", text, w.page, box))
        else:
            out.append(Token("text", w.text, w.page, box))
        i += 1
    return out


def label_of(line: list[Word]) -> tuple[str, int]:
    """The row's label (words before the first number) and the index where its cells begin."""
    parts: list[str] = []
    for i, word in enumerate(line):
        if re.fullmatch(r"[\d.,()–-]+", word.text) and word.text != ".":
            return " ".join(parts), i
        if word.text in ("n.", "n.c.", "n.c"):
            return " ".join(parts), i
        if word.text != ".":
            parts.append(word.text)
    return " ".join(parts), len(line)


def fold(text: str) -> str:
    return re.sub(r"[^a-zãçéíóú ]", "", text.lower()).strip()


def circle_of(label: str) -> str | None:
    name = fold(label)
    for island, (english, _) in ISLAND_NAMES.items():
        if name == fold(english):
            return island
    if name.startswith(("compensa", "círculo de compensa", "circulo")):
        return COMPENSATION
    if name.startswith("totais"):
        return "total"
    return None


def parse_row(
    spec: Year, block: int, line: list[Word], first: bool
) -> tuple[dict[str, Cell], dict[str, Cell], dict[str, int]]:
    label, start = label_of(line)
    toks = tokens(line[start:])
    cells: dict[str, Cell] = {}
    votes: dict[str, Cell] = {}
    seats: dict[str, int] = {}
    i = 0
    if first:
        for kind in spec.lead:
            tok = toks[i]
            if kind == "p":
                if tok.kind != "pct":
                    raise ValueError(f"{spec.year} {label}: expected a percentage, found {tok.text!r}")
                i += 1
                continue
            if tok.kind != "int":
                raise ValueError(f"{spec.year} {label}: expected a number for {kind}, found {tok.text!r}")
            cells[kind] = Cell(tok.value, tok.page, tok.box)
            i += 1
    parties = spec.blocks[block]
    for n, party in enumerate(parties):
        last = n == len(parties) - 1
        if spec.fixed:
            width = spec.percents + 2
            group = toks[i : i + width]
            if len(group) < width:
                raise ValueError(f"{spec.year} {label}: ran out of cells at {party}")
            if group[0].kind == "int":
                votes[party] = Cell(group[0].value, group[0].page, group[0].box)
                seats[party] = group[-1].value if group[-1].kind == "int" else 0
            elif any(tok.kind != "dash" for tok in group):
                raise ValueError(f"{spec.year} {label}: expected votes or dashes for {party}")
            i += width
            continue
        if i >= len(toks):
            raise ValueError(f"{spec.year} {label}: ran out of cells at {party}")
        tok = toks[i]
        if tok.kind == "nc":
            i += 1
            while i < len(toks) and toks[i].kind in ("dash", "pct"):
                i += 1
            continue
        if tok.kind == "dash":
            # A list that did not run in the circle: dashes for its votes, percentages, and seats.
            i += 1
            for _ in range(spec.percents + 1):
                if i < len(toks) and toks[i].kind in ("dash", "pct"):
                    i += 1
            continue
        if tok.kind != "int":
            raise ValueError(f"{spec.year} {label}: expected votes for {party}, found {tok.text!r}")
        votes[party] = Cell(tok.value, tok.page, tok.box)
        i += 1
        taken = 0
        while taken < spec.percents and i < len(toks) and toks[i].kind in ("pct", "dash", "dec"):
            i += 1
            taken += 1
        seat = 0
        if i < len(toks):
            if toks[i].kind == "dash":
                i += 1
            elif toks[i].kind == "int":
                after = toks[i + 1] if i + 1 < len(toks) else None
                if last or after is None or after.kind not in ("pct", "dash"):
                    seat = toks[i].value
                    i += 1
        seats[party] = seat
    return cells, votes, seats


def parse_rows(spec: Year, path: Path) -> dict[str, Circle]:
    found, _ = words(path)
    circles: dict[str, Circle] = {}
    block = -1
    for line in rows(found):
        label, start = label_of(line)
        which = circle_of(label)
        if which is None or start >= len(line):
            continue
        if which == "49":
            block += 1
        if block < 0 or block >= len(spec.blocks):
            continue
        if which == COMPENSATION:
            continue
        cells, votes, seats = parse_row(spec, block, line, block == 0)
        circle = circles.setdefault(which, Circle())
        circle.cells.update(cells)
        circle.votes.update(votes)
        circle.seats.update(seats)
    return circles


def parse_2020(path: Path) -> dict[str, Circle]:
    """The 2020 map is turned on its side: one row per list, one column per circle."""
    found, _ = words(path)
    columns = [*COLUMNS_2020, COMPENSATION, "total"]
    circles = {c: Circle() for c in columns}
    lead = {"inscritos": "i", "votantes": "v", "brancos": "b", "nulos": "n", "votos": "e"}
    seen: set[str] = set()
    party: str | None = None
    for line in rows(found):
        head = line[0].text
        at = next((i for i, w in enumerate(line) if w.text == "Número"), None)
        start = at + 1 if at is not None else None
        if head == "MD" and party is not None:
            cells = [t for t in tokens(line[1:]) if t.kind in ("int", "dash")][:11]
            if len(cells) == 11:
                for column, tok in zip(columns, cells, strict=True):
                    circles[column].seats[party] = tok.value if tok.kind == "int" else 0
            continue
        if head.lower() == "inscritos":
            start, name = 1, "inscritos"
        elif at is not None:
            name = " ".join(w.text for w in line[:at] if w.text != ".").rstrip(".").strip()
        else:
            continue
        cells = [t for t in tokens(line[start:]) if t.kind in ("int", "nc", "dash", "dec", "pct")]
        if len(cells) < 11:
            continue
        cells = cells[:11]
        first = fold(name).split(" ")[0] if name else ""
        name = name.replace(" ", "")
        if first in lead:
            kind = lead[first]
            if kind in seen:
                continue
            seen.add(kind)
            for column, tok in zip(columns, cells, strict=True):
                if tok.kind == "int":
                    circles[column].cells[kind] = Cell(tok.value, tok.page, tok.box)
            continue
        party = "BE" if name in ("B.E", "B.E.") else name.rstrip(".")
        if party in seen:
            party = None
            continue
        seen.add(party)
        for column, tok in zip(columns, cells, strict=True):
            if tok.kind == "int":
                circles[column].votes[party] = Cell(tok.value, tok.page, tok.box)
    return circles


type Box = tuple[float, float, float, float]


def columns(spec: Year) -> list[str]:
    return [*(["i", "v", "n"] if spec.joined else ["i", "v", "b", "n"]), *spec.lists]


def keyed(spec: Year) -> dict[str, tuple[list[int | None], dict[str, int]]]:
    """The keyed rows: per circle (or the total), the cells in column order (None where a list did not run) and the
    seats."""
    out: dict[str, tuple[list[int | None], dict[str, int]]] = {}
    lead = 3 if spec.joined else 4
    for line in spec.keyed.strip().splitlines():
        head, _, tail = line.partition("|")
        match = re.match(r"(\D+?)\s+([\d\s]+)$", head.strip())
        if match is None:
            raise ValueError(f"{spec.year}: cannot read {line!r}")
        code = circle_of(match.group(1))
        if code is None:
            raise ValueError(f"{spec.year}: unknown circle {match.group(1)!r}")
        values: list[int | None] = [int(n) for n in match.group(2).split()]
        if len(values) != lead:
            raise ValueError(f"{spec.year} {match.group(1)}: {len(values)} leading cells")
        cells = tail.split()
        if len(cells) != len(spec.lists):
            raise ValueError(f"{spec.year} {match.group(1)}: {len(cells)} lists")
        seats: dict[str, int] = {}
        for label, cell in zip(spec.lists, cells, strict=True):
            votes, _, won = cell.partition("/")
            values.append(None if votes == "-" else int(votes))
            if won:
                seats[label] = int(won)
        out[code] = (values, seats)
    return out


def scanned(spec: Year) -> dict[str, Circle]:
    boxes: dict[str, list[float]] = json.loads(BOXES.read_text())
    circles: dict[str, Circle] = {}
    for code, (values, seats) in keyed(spec).items():
        circle = circles[code] = Circle(seats=seats)
        for column, value in zip(columns(spec), values, strict=True):
            if value is None:
                continue
            page, x0, y0, x1, y1 = boxes[f"{spec.year}/{code}/{column}"]
            cell = Cell(value, int(page), (x0, y0, x1, y1))
            if column in spec.lists:
                circle.votes[column] = cell
            else:
                circle.cells[column] = cell
    return circles


def circles_of(spec: Year) -> dict[str, Circle]:
    if spec.keyed:
        return scanned(spec)
    path = artifact(spec).path
    return parse_2020(path) if spec.year == 2020 else parse_rows(spec, path)


def compensation_seats(spec: Year, circles: dict[str, Circle]) -> dict[str, int]:
    """Seats of the compensation circle: printed in its 2020 column; before that, the printed national seats less the
    island circles' seats."""
    if spec.year < 2008:
        return {}
    if spec.year == 2020:
        return {party: n for party, n in circles[COMPENSATION].seats.items() if n}
    out: dict[str, int] = {}
    for party, n in circles["total"].seats.items():
        left = n - sum(circles[code].seats.get(party, 0) for code in ISLAND_NAMES)
        if left < 0:
            raise ValueError(f"{spec.year}: {party} has more island seats than the national total")
        if left:
            out[party] = left
    return out


def party_key(label: str) -> str:
    return PARTY_OF.get(label, label)


def label_party(label: str) -> str:
    return key("party", party_key(label))


def check(spec: Year, circles: dict[str, Circle]) -> list[str]:
    """Where the maps' own figures disagree: each circle's votes against its valid votes, and the island circles
    against the printed national totals."""
    problems: list[str] = []
    leads = "ivn" if spec.joined else "ivbn"
    for code in ISLAND_NAMES:
        c = circles[code]
        valid = c.cells["v"].value - c.cells["n"].value - (0 if spec.joined else c.cells["b"].value)
        listed = sum(cell.value for cell in c.votes.values())
        if listed != valid:
            problems.append(f"{spec.year} {ISLAND_NAMES[code][0]}: lists add up to {listed}, valid votes are {valid}")
    total = circles.get("total")
    if total is not None and spec.year != 2004:
        for kind in leads:
            summed = sum(circles[code].cells[kind].value for code in ISLAND_NAMES)
            if summed != total.cells[kind].value:
                problems.append(
                    f"{spec.year} {kind}: circles add up to {summed}, the map prints {total.cells[kind].value}"
                )
        for party, cell in total.votes.items():
            summed = sum(circles[code].votes[party].value for code in ISLAND_NAMES if party in circles[code].votes)
            if summed != cell.value:
                problems.append(f"{spec.year} {party}: circles add up to {summed}, the map prints {cell.value}")
        if spec.year < 2008:
            for party in total.votes:
                summed = sum(circles[code].seats.get(party, 0) for code in ISLAND_NAMES)
                if summed != total.seats.get(party, 0):
                    problems.append(
                        f"{spec.year} {party} seats: circles add up to {summed}, the map prints {total.seats.get(party, 0)}"
                    )
    return problems


def fetch() -> None:
    for spec in YEARS:
        raw.fetch(SOURCE, f"{CNE}/{spec.pdf}", suffix=".pdf")


def artifact(spec: Year) -> raw.Artifact:
    return raw.cached(SOURCE, f"{CNE}/{spec.pdf}")


# ---------------------------------------------------------------------------------------------------------------------
# Boxes of the keyed values on the scanned pages

SCAN_DPI = 300


def locate() -> None:
    """Writes the box of each keyed value on the scanned maps. Needs the OCR engine: `uv sync --group scans`."""
    numpy: Any = importlib.import_module("numpy")
    image: Any = importlib.import_module("PIL.Image")
    engine: Any = importlib.import_module("rapidocr_onnxruntime").RapidOCR()
    out: dict[str, list[float]] = {}
    for spec in YEARS:
        if not spec.keyed:
            continue
        found: list[tuple[int, str, Box]] = []
        with tempfile.TemporaryDirectory() as folder:
            for page in spec.pages:
                stem = Path(folder) / f"p{page}"
                subprocess.run(
                    ["pdftoppm", "-r", str(SCAN_DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                    + [str(artifact(spec).path), str(stem)],
                    check=True,
                )
                picture = image.open(f"{stem}.png")
                upright = picture.rotate(spec.rotate, expand=True) if spec.rotate else picture
                result, _ = engine(numpy.asarray(upright.convert("RGB")))
                for corners, text, _ in result or []:
                    xs = [float(p[0]) for p in corners]
                    ys = [float(p[1]) for p in corners]
                    box = (min(xs), min(ys), max(xs), max(ys))
                    width = (box[2] - box[0]) / max(len(text), 1)
                    # A token can run two cells together ("63 963 60,40"): each number takes its share of the box.
                    for part in re.finditer(r"\d+(?: \d{3})*", text):
                        left, right = box[0] + part.start() * width, box[0] + part.end() * width
                        found.append((page, part.group().replace(" ", ""), (left, box[1], right, box[3])))
        _place(spec, found, out)
    BOXES.write_text(json.dumps(dict(sorted(out.items())), indent=0) + "\n")


def _place(spec: Year, found: list[tuple[int, str, Box]], out: dict[str, list[float]]) -> None:
    """Matches each keyed value to an OCR token on its row; a cell the OCR missed takes its column's span and its
    row's line. A scan's rows can slope, so a row's line is an intercept and one slope for the page. Boxes are stored
    in PDF points on the page as printed."""
    rows = keyed(spec)
    names = columns(spec)
    first = spec.split or len(names)
    page = spec.pages[0]

    def group(column: str) -> int:
        return 0 if names.index(column) < first else 1

    def middle(box: Box) -> tuple[float, float]:
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    # Each row's large values, wherever the OCR read them.
    wanted: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for code, (values, _) in rows.items():
        for g in (0, 1):
            texts = {str(v) for c, v in zip(names, values, strict=True) if v is not None and v >= 100 and group(c) == g}
            points = [middle(box) for _, text, box in found if text in texts]
            if points:
                wanted[(code, g)] = points
    # The slope: how far the rows sink for each pixel to the right, pooled over the rows.
    num = den = 0.0
    for points in wanted.values():
        mx = statistics.median(x for x, _ in points)
        my = statistics.median(y for _, y in points)
        num += sum((x - mx) * (y - my) for x, y in points)
        den += sum((x - mx) ** 2 for x, _ in points)
    slope = num / den if den else 0.0
    anchors = {key: statistics.median(y - slope * x for x, y in points) for key, points in wanted.items()}

    def expected(code: str, column: str, x: float) -> float:
        return anchors[(code, group(column))] + slope * x

    matched: dict[tuple[str, str], Box] = {}
    centers: dict[str, float] = {}
    # Large values first: they are rarely printed twice on a row. Their columns then tell the small ones apart.
    for large in (True, False):
        for code, (values, _) in rows.items():
            for column, value in zip(names, values, strict=True):
                if value is None or (value >= 100) != large or (code, group(column)) not in anchors:
                    continue
                taken = set(matched.values())
                near = [
                    box
                    for _, text, box in found
                    if text == str(value)
                    and box not in taken
                    and abs(middle(box)[1] - expected(code, column, middle(box)[0])) < 14
                ]
                if column in centers:
                    near = [box for box in near if abs(middle(box)[0] - centers[column]) < 60]
                    near.sort(key=lambda b: abs(middle(b)[0] - centers[column]))
                else:
                    near.sort(key=lambda b: abs(middle(b)[1] - expected(code, column, middle(b)[0])))
                if near:
                    matched[(code, column)] = near[0]
        for column in names:
            xs = [middle(b)[0] for (c, col), b in matched.items() if col == column]
            if xs:
                centers[column] = statistics.median(xs)
    height = statistics.median(box[3] - box[1] for box in matched.values())
    for code, (values, _) in rows.items():
        for column, value in zip(names, values, strict=True):
            if value is None:
                continue
            if (code, column) in matched:
                box = matched[(code, column)]
            else:
                spans = [b for (c, col), b in matched.items() if col == column]
                if not spans or (code, group(column)) not in anchors:
                    raise ValueError(f"{spec.year} {code} {column}: no box")
                x0, x1 = statistics.median(b[0] for b in spans), statistics.median(b[2] for b in spans)
                y = expected(code, column, (x0 + x1) / 2)
                box = (x0, y - height / 2, x1, y + height / 2)
            out[f"{spec.year}/{code}/{column}"] = [page, *_unturn(spec, page, box)]
    seen: dict[tuple[float, ...], str] = {}
    for name, found_box in out.items():
        if name.startswith(f"{spec.year}/"):
            if tuple(found_box) in seen:
                raise ValueError(f"{name} and {seen[tuple(found_box)]} share a box")
            seen[tuple(found_box)] = name


def _unturn(spec: Year, page: int, box: Box) -> list[float]:
    """A box on the upright image, in pixels, as a box on the page as printed, in points."""
    scale = 72 / SCAN_DPI
    x0, y0, x1, y1 = box
    if spec.rotate:
        width, height = (n * SCAN_DPI / 72 for n in _page_size(spec, page))
        if spec.rotate < 0:
            x0, y0, x1, y1 = y0, height - x1, y1, height - x0
        else:
            x0, y0, x1, y1 = width - y1, x0, width - y0, x1
    return [round(c * scale, 1) for c in (x0, y0, x1, y1)]


def _page_size(spec: Year, page: int) -> tuple[float, float]:
    info = subprocess.run(
        ["pdfinfo", "-f", str(page), "-l", str(page), str(artifact(spec).path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = re.search(rf"Page\s+{page} size:\s+([\d.]+) x ([\d.]+)", info)
    if match is None:
        raise ValueError(f"{spec.year}: no size for page {page}")
    return float(match.group(1)), float(match.group(2))


# ---------------------------------------------------------------------------------------------------------------------
# Packages

SCAN_METHOD = (
    " The map is a scanned page. Its figures were keyed from the page image and checked against the map's own sums; "
    "each value's box on the page was found by OCR, or, where the OCR missed a cell, from its column and row."
)
JOINED = " The map prints blank and invalid ballots as one figure; it is stored here as invalid ballots."

METHOD = (
    "Counted by electoral circle: one for each of the nine islands, as published in the National Elections "
    "Commission's official map of results (mapa oficial), which the Commission also prints in the Diário da "
    "República. The map goes no finer than the circle. Registered voters are the voters on the roll, the voters are "
    "those who voted, and blank and invalid (nulos) ballots are printed apart from the votes for each list. Seats "
    "are as the map declares them."
)
COMPENSATION_METHOD = (
    "The regional compensation circle elects the seats that bring the lists' totals closer to proportionality "
    "across the region. It has no ballots of its own: it works from each list's votes in the whole region, which "
    "the map prints in its row of regional totals and which are stored here as one count for the region. The map's "
    "own figures for the circle are those totals divided by the divisors the allocation reached, not votes, and are "
    "not stored. The seats are those the map declares: printed in the circle's column in 2020, and in earlier "
    "years the printed national seats less the seats the nine island circles won."
)
SYSTEM_NOTE = "d'Hondt in each island circle and, from 2008, in the regional compensation circle."


def artifact_id(spec: Year) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CNE_SOURCE}\x1f{artifact(spec).storage_key}"))


DPI = 110


def render(spec: Year) -> dict[int, tuple[str, int, int]]:
    """Each page as a grayscale image, with its key and size in pixels."""
    source = artifact(spec)
    folder = DATA / "pages" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f"{source.sha256}"
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", str(source.path), str(stem)], check=True)
    out: dict[int, tuple[str, int, int]] = {}
    for image in sorted(folder.glob(f"{source.sha256}-*.png")):
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        out[int(image.stem.rsplit("-", 1)[1])] = (f"pages/{SOURCE}/{image.name}", width, height)
    return out


def day_key(spec: Year) -> str:
    return f"{SOURCE}/{spec.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(day_key(spec) for spec in YEARS)]


def labels() -> list[str]:
    seen: dict[str, None] = {}
    for spec in YEARS:
        seen.update(dict.fromkeys(spec.lists))
        for block in spec.blocks:
            seen.update(dict.fromkeys(block))
        if spec.year == 2020:
            seen.update(dict.fromkeys(circles_of(spec)["total"].votes))
    return list(seen)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties and coalitions of the Azores regional elections of 1976 to 2020, and the National Elections "
        "Commission as their source",
        depends=["pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Comissão Nacional de Eleições",
        title="Mapas oficiais dos resultados das eleições para a Assembleia Legislativa da Região Autónoma dos Açores",
        url="https://www.cne.pt/content/eleicoes-referendos-resultados-oficiais#acores",
        license="Official publication in the Diário da República; reuse terms are not stated",
        attribution="Comissão Nacional de Eleições",
    )
    for spec in YEARS:
        p.artifact(artifact(spec), CNE_SOURCE)
    parties: dict[str, list[str]] = {}
    for label in labels():
        printed = parties.setdefault(party_key(label), [])
        if label not in printed:
            printed.append(label)
    for party, printed in parties.items():
        id = key("party", party)
        p.add("party", id=id, kind="bloc" if party in BLOCS else "party", polity_id=PORTUGAL)
        p.add("external_id", entity_id=id, scheme="pt.cne_acores.party", value=party)
        p.name(id, NAMES[party], "pt", "official")
        if party in OLDER:
            p.name(id, OLDER[party], "pt", "historical", False)
        for label in printed:
            p.name(id, label, "pt", "abbreviation", False)
    return p


def election(spec: Year) -> Package:
    day = spec.day
    p = Package(
        day_key(spec),
        SOURCE,
        f"The Azores regional election of {day} by electoral circle, from the National Elections Commission's "
        "official map of results",
        depends=[f"{SOURCE}/registry", "pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    p.uses(artifact(spec))
    circles = circles_of(spec)
    problems = check(spec, circles)
    seats = compensation_seats(spec, circles)
    method = METHOD + (SCAN_METHOD if spec.keyed else "") + (JOINED if spec.joined else "")
    election_id = key("election", day)
    path = f"pt/{day}-azores-regional"
    p.add("election", id=election_id, polity_id=PORTUGAL, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Azores regional election", "en", "official")
    p.name(election_id, f"Eleições legislativas regionais dos Açores de {day.year}", "pt", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    page_ids: dict[int, str] = {}
    for number, (image, width, height) in render(spec).items():
        page_ids[number] = key("page", day, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(spec),
            page_number=number,
            image_key=image,
            width=width,
            height=height,
        )
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    parties = list(circles["total"].votes) if "total" in circles and spec.year != 2004 else []
    order = list(spec.lists) or ([label for block in spec.blocks for label in block] if spec.blocks else parties)
    if spec.year == 2020:
        order = list(circles["total"].votes)
    ran = [label for label in order if any(label in circles[code].votes for code in ISLAND_NAMES)]
    for code, (english, portuguese) in sorted(ISLAND_NAMES.items(), key=lambda item: item[1][0]):
        circle = circles[code]
        contest = key("contest", day, code)
        magnitude = sum(circle.seats.values())
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=AZORES_ASSEMBLY,
            district_area_id=circle_area(code),
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, english, "en", "official")
        p.add("slug", path=f"{path}/{changeset_slug(english)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        _result_set(p, result_set, election_id, contest, method)
        if spec.note and code == SCAN_NOTE_CIRCLE:
            p.add(
                "note",
                id=key("note", result_set, "map"),
                entity_id=result_set,
                kind="caveat",
                body_md=spec.note,
                lang="en",
            )
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PORTUGAL,
            scheme_code="pt.circulo",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=circle_area(code),
            kind="aggregate",
        )
        p.name(unit, portuguese, "pt", "official")
        options: dict[str, str] = {}
        for position, label in enumerate((l for l in order if l in circle.votes), 1):
            option = options[label] = key("option", day, code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=label_party(label),
                ballot_position=position,
                ballot_label=label,
            )
        measured = [
            (REGISTERED, circle.cells["i"]),
            (CAST, circle.cells["v"]),
            (BLANK, circle.cells.get("b")),
            (INVALID, circle.cells["n"]),
        ]
        for measure, cell in measured:
            if cell is None:
                continue
            _value(values, locators, page_ids, result_set, unit, None, measure, cell)
        for label, option in options.items():
            _value(values, locators, page_ids, result_set, unit, option, VOTES, circle.votes[label])
            if circle.seats.get(label):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=circle.seats[label])
    if seats:
        code = COMPENSATION
        contest = key("contest", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=AZORES_ASSEMBLY,
            district_area_id=district_area("40"),
            magnitude=sum(seats.values()),
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, "Regional compensation circle", "en", "official")
        p.name(contest, "Círculo Regional de Compensação", "pt", "native", False)
        p.add("slug", path=f"{path}/regional-compensation-circle", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        _result_set(p, result_set, election_id, contest, COMPENSATION_METHOD)
        region = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=region,
            polity_id=PORTUGAL,
            scheme_code="pt.circulo",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=district_area("40"),
            kind="aggregate",
        )
        p.name(region, "Região Autónoma dos Açores", "pt", "official")
        whole = circles["total"]
        for measure, kind in ((REGISTERED, "i"), (CAST, "v"), (BLANK, "b"), (INVALID, "n")):
            _value(values, locators, page_ids, result_set, region, None, measure, whole.cells[kind])
        for position, label in enumerate(ran, 1):
            option = key("option", day, code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=label_party(label),
                ballot_position=position,
                ballot_label=label,
            )
            if label in whole.votes:
                _value(values, locators, page_ids, result_set, region, option, VOTES, whole.votes[label])
            if seats.get(label):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats[label])
    unknown = set(seats) - set(ran)
    if unknown:
        raise ValueError(f"{day}: compensation seats for lists that ran nowhere: {sorted(unknown)}")
    _frames(p, values, locators)
    for problem in problems:
        print(problem)
        p.add(
            "note",
            id=key("note", day, problem),
            entity_id=key("result_set", day, next(c for c, (n, _) in ISLAND_NAMES.items() if n in problem)),
            kind="caveat",
            body_md=f"The map's own figures do not add up here: {problem[5:]}. They are stored as printed.",
            lang="en",
        )
    return p


def changeset_slug(text: str) -> str:
    from ea import changeset

    return changeset.slug(text)


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


def _value(
    values: list[tuple[str, str, str | None, int, int]],
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]],
    page_ids: dict[int, str],
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    cell: Cell,
) -> None:
    values.append((result_set, unit, option, measure, cell.value))
    scale = DPI / 72
    x0, y0, x1, y1 = (round(c * scale, 1) for c in cell.box)
    locators.append((result_set, unit, option, measure, page_ids[cell.page], x0, y0, x1, y1))


def _frames(
    p: Package,
    values: list[tuple[str, str, str | None, int, int]],
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]],
) -> None:
    key_schema = {"result_set_id": pl.String(), "unit_id": pl.String(), "option_id": pl.String(), "measure": pl.Int16()}
    p.frame(
        "result_value",
        pl.DataFrame(values, schema={**key_schema, "value": pl.Int64()}, orient="row").with_columns(
            channel=pl.lit(0, pl.Int16()), count_round=pl.lit(0, pl.Int16()), flags=pl.lit(0, pl.Int16())
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                **key_schema,
                "page_id": pl.String(),
                "x0": pl.Float64(),
                "y0": pl.Float64(),
                "x1": pl.Float64(),
                "y1": pl.Float64(),
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16()), count_round=pl.lit(0, pl.Int16())),
    )


def parse() -> None:
    registry().write()
    for spec in YEARS:
        election(spec).write()


def emit() -> None:
    package.run("load", packages())
