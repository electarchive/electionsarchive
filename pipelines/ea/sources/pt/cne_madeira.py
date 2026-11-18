"""The Madeira regional elections of 1976 to 2004, by municipal circle, from the National Elections Commission's maps.

Until 2007 the Legislative Assembly of Madeira was elected in eleven circles, one for each municipality. After each
election the National Elections Commission (CNE) published the official map of results (mapa oficial) in the Diário
da República, and its site carries each one as a PDF. The map gives, for each circle, the registered voters, the
voters, the blank and invalid ballots (one figure in 1976), each list's votes, and its seats. It does not go below
the circle. The Ministry's results site starts with 2007, when the region became one circle.

The maps of 1996, 2000, and 2004 have a text layer: `pdftotext -bbox` gives each word with its box, and every value
keeps the box of the word it was read from. The maps of 1976 to 1992 are scans. Their figures are keyed below from
the page images and checked against the map's own sums: each circle's lists, blank, and invalid ballots against its
voters, and the circles against the printed totals. Each keyed value's box on the page comes from `locate`, which
reads the page with OCR and writes `cne_madeira_boxes.json`; a cell the OCR missed takes the box of its column and
row.
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

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pt.mai import MADEIRA_ASSEMBLY, PORTUGAL, SYSTEM_LIST, town_area
from ea.store import DATA

SOURCE = "pt.cne_madeira"
CNE = "https://www.cne.pt/sites/default/files/dl"
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6
BOXES = Path(__file__).with_name("cne_madeira_boxes.json")

# Municipal circles: the Ministry's municipality code and the name.
CIRCLES = {
    "3101": "Calheta",
    "3102": "Câmara de Lobos",
    "3103": "Funchal",
    "3104": "Machico",
    "3105": "Ponta do Sol",
    "3106": "Porto Moniz",
    "3201": "Porto Santo",
    "3107": "Ribeira Brava",
    "3108": "Santa Cruz",
    "3109": "Santana",
    "3110": "São Vicente",
}
TOTAL = "total"


@dataclass(frozen=True)
class Year:
    year: int
    day: date
    pdf: str
    # Lists in the map's column order, as printed.
    lists: tuple[str, ...]
    # Pages that hold the table.
    pages: tuple[int, ...]
    # Text-layer maps: the cells of the totals row of each block of the table, in order. i registered, v voters,
    # b blank, n invalid, % a percentage, a list's votes as its label, m that list's seats, t the total seats.
    blocks: tuple[str, ...] = ()
    # Scanned maps: the keyed table, and whether the page is turned on its side.
    keyed: str = ""
    turned: bool = False
    joined: bool = False  # blank and invalid ballots printed as one figure
    split: int = 0  # columns on the first of two pages
    note: str = ""


# Keyed rows: the circle, registered, voters, blank, and invalid (one figure where the map joins them), then after
# "|" each list's votes in the map's order, "-" where it did not run, and "/n" for the seats it won.
YEARS = [
    Year(
        1976,
        date(1976, 6, 27),
        "resultados_alras_1976.pdf",
        ("CDS", "MRPP", "PCP", "PPD", "PS", "UDP"),
        (4,),
        keyed="""
Calheta         8600   6794   77 | 696     -    51   5433/2  488      49
Câmara de Lobos 14496  11180  171 | 833     -    128  8096/4  1607     345
Funchal         65447  47291  594 | 6165/2  293  1289 20826/9 15861/7  2263/1
Machico         10892  8192   117 | 200     -    113  4337/2  972      2453/1
Ponta do Sol    5686   4574   27 | 124     -    32   3994/2  356      41
Porto Moniz     2514   2090   13 | 112     -    5    1746/1  210      4
Porto Santo     2479   1863   18 | 70      -    30   1022/1  710      13
Ribeira Brava   8002   5898   129 | 189     -    99   4766/2  653      62
Santa Cruz      13682  10317  122 | 791     64   137  6902/3  2137/1   164
Santana         6759   5508   36 | 531     -    47   4294/2  559      41
São Vicente     4846   3558   63 | 474     -    28   2547/1  415      31
Totais          143403 107265 1367 | 10185/2 357  1959 63963/29 23968/8 5466/2
""",
        turned=True,
        joined=True,
        note=(
            "The map's seat column for the CDS puts its two seats on the Câmara de Lobos row and none on the Funchal "
            "row, while the same map's list of elected members gives both seats to the CDS in Funchal and none in "
            "Câmara de Lobos, where the seats are otherwise all accounted for. The seats are stored as the list of "
            "elected members gives them."
        ),
    ),
    Year(
        1980,
        date(1980, 10, 5),
        "resultados_alram_1980.pdf",
        ("APU", "CDS", "PCTP/MRPP", "PS", "PPD/PSD", "UDA/PDA", "UDP"),
        (3,),
        keyed="""
Calheta         8361   7104   31  99   | 38     1033   14  366     5388/2  99    36
Câmara de Lobos 15135  12455  64  349  | 246    527    40  1423    9156/4  242   408
Funchal         72235  57103  415 928  | 2813/1 3944/1 260 11921/5 32119/13 1318 3385/1
Machico         12546  10197  47  174  | 171    164    50  857     6332/3  75    2327/1
Ponta do Sol    5647   4900   19  50   | 47     230    9   221     4263/2  -     61
Porto Moniz     2524   2116   7   29   | 13     336    5   130     1589/1  -     7
Porto Santo     2653   2313   35  25   | 18     45     4   876     1302/1  -     8
Ribeira Brava   7952   6539   85  195  | 133    278    29  508     5179/2  69    63
Santana         6880   5485   23  76   | 47     377    9   370     4463/2  68    52
São Vicente     5000   3773   27  102  | 54     612    16  369     2562/1  -     31
Santa Cruz      14506  12077  88  129  | 297    470    53  1565    8698/4  251   426
Totais          153439 124062 841 2256 | 3877/1 8016/1 489 18606/5 81051/35 2212 6804/2
""",
    ),
    Year(
        1984,
        date(1984, 10, 14),
        "resultados_alram_1984.pdf",
        ("APU", "CDS", "PCTP/MRPP", "PS", "PPD/PSD", "UDP"),
        (3,),
        keyed="""
Calheta         8916   6654   14  82   | 45     826    17  437     5204/3   29
Câmara de Lobos 16930  12143  27  247  | 152    387    86  1206    9735/5   303
Funchal         80349  55378  440 798  | 2297/1 3588/1 407 11403/5 33113/15 3332/1
Machico         13992  10304  25  155  | 121    176    82  638     6534/3   2573/1
Ponta do Sol    6047   4751   11  24   | 36     447    10  369     3821/2   33
Porto Moniz     2732   2072   7   22   | 19     308    7   146     1552/1   11
Porto Santo     2898   2296   19  20   | 14     27     5   851     1351/1   9
Ribeira Brava   8647   6296   27  110  | 76     343    34  573     5062/2   71
Santana         7435   5360   22  64   | 34     364    21  467     4342/2   46
São Vicente     5541   3682   12  61   | 21     559    18  437     2543/2   31
Santa Cruz      15932  12088  87  167  | 495    395    73  2026/1  8615/4   230
Totais          169419 121024 691 1750 | 3310/1 7420/1 760 18553/6 81872/40 6668/2
""",
    ),
    Year(
        1988,
        date(1988, 10, 9),
        "resultados_alram_1988.pdf",
        ("CDS", "CDU", "PCTP/MRPP", "PDA", "PPD/PSD", "PS", "UDP"),
        (3,),
        keyed="""
Calheta         9595   6638   18  70   | 1118    48   -   -   4776/3   563     45
Câmara de Lobos 18755  12805  57  197  | 511     284  -   -   9482/5   1730    544
Funchal         87726  58428  584 615  | 4394/2  1746 395 779 31905/15 12571/6 5439/2
Machico         15718  9768   54  117  | 268     86   -   -   5599/3   721     2923/1
Ponta do Sol    6441   4649   12  31   | 877     22   -   -   3479/2   154     74
Porto Moniz     2889   2176   15  13   | 312     15   -   -   1634/1   172     15
Porto Santo     3344   2464   19  21   | 54      5    -   -   1328/1   1012    25
Ribeira Brava   9528   6505   41  127  | 648     85   -   -   4846/3   588     170
Santa Cruz      17630  12913  81  148  | 807     189  101 -   8379/4   2869/1  339
Santana         7969   5412   30  68   | 637     39   -   -   4211/2   357     70
São Vicente     5745   3625   11  37   | 637     30   -   -   2546/2   321     43
Totais          185340 125383 922 1444 | 10263/2 2549 496 779 78185/41 21058/7 9687/3
""",
        turned=True,
    ),
    Year(
        1992,
        date(1992, 10, 11),
        "resultados_alras_1992.pdf",
        ("CDS", "PCP/PEV", "PDA", "PPD/PSD", "PS", "PSN", "UDP"),
        (4, 5),
        split=6,
        keyed="""
Calheta         9844   6475   23  110  | 1438    51     -   4544/3   223      -      86
Câmara de Lobos 20322  13577  63  208  | 545     466    -   9559/5   2293/1   -      443
Funchal         92604  61228  498 725  | 5558/2  2793/1 753 29430/14 14435/7  2768/1 4268/2
Machico         17080  10908  54  117  | 302     122    -   5881/3   4067/2   -      365
Ponta do Sol    6676   4646   18  51   | 746     24     -   3363/2   275      57     112
Porto Moniz     2938   2093   15  28   | 221     15     -   1544/1   252      -      18
Porto Santo     3498   2604   40  25   | 66      31     -   1497/1   919      -      26
Ribeira Brava   10445  7030   26  123  | 306     49     -   4657/2   1720/1   -      149
Santa Cruz      19028  13294  108 194  | 662     251    -   7726/4   3571/1   329    453
Santana         8295   5513   42  57   | 269     42     -   3848/2   1182     -      73
São Vicente     5868   3431   16  36   | 469     24     -   2320/2   506      -      60
Totais          196589 130799 903 1674 | 10582/2 3868/1 753 74369/39 29443/12 3154/1 6053/2
""",
    ),
    Year(
        1996,
        date(1996, 10, 13),
        "resultados_alram_1996.pdf",
        ("CDS-PP", "PCP-PEV", "PDA", "PPD/PSD", "PS", "PSN", "UDP"),
        (2,),
        blocks=(
            "i v % b % n % CDS-PP % m PCP-PEV % m",
            "PDA % m PPD/PSD % m PS % m PSN % m UDP % m t",
        ),
    ),
    Year(
        2000,
        date(2000, 10, 15),
        "resultados_alram_2000.pdf",
        ("CDS-PP", "PCP-PEV", "PPD/PSD", "PS", "PSN", "UDP"),
        (2,),
        blocks=("i v % b % n % CDS-PP % m PCP-PEV % m PPD/PSD % m PS % m PSN % m UDP % m t",),
        note=(
            "The map prints 3 votes for the UDP in Ribeira Brava, with a share of 2.74 percent. The circle's other "
            "figures leave 190 votes for it, and the UDP's printed total for the region (6,210) counts 190. The "
            "figure is stored as printed."
        ),
    ),
    Year(
        2004,
        date(2004, 10, 17),
        "resultados_alram_2004.pdf",
        ("BE", "CDS-PP", "PCP-PEV", "PPD/PSD", "PS"),
        (2, 3),
        blocks=("i v % b % n % BE % m CDS-PP % m PCP-PEV % m PPD/PSD % m PS % m t",),
    ),
]

# Each printed label, the party it stands for (one key for labels the maps use for the same party or coalition over
# the years), and that party's name from the maps' legends.
PARTY_OF = {
    "PPD": "PPD/PSD",
    "MRPP": "PCTP/MRPP",
    "CDS": "CDS-PP",
    "UDA/PDA": "PDA",
    "CDU": "PCP-PEV",
    "PCP/PEV": "PCP-PEV",
}
NAMES = {
    "APU": "Aliança Povo Unido",
    "BE": "Bloco de Esquerda",
    "CDS-PP": "CDS — Partido Popular",
    "PCP": "Partido Comunista Português",
    "PCP-PEV": "Coligação Democrática Unitária",
    "PCTP/MRPP": "Partido Comunista dos Trabalhadores Portugueses",
    "PDA": "Partido Democrático do Atlântico",
    "PPD/PSD": "Partido Social Democrata",
    "PS": "Partido Socialista",
    "PSN": "Partido da Solidariedade Nacional",
    "UDP": "União Democrática Popular",
}
OLDER = {"CDS-PP": "Centro Democrático Social", "PPD/PSD": "Partido Popular Democrático"}
BLOCS = {"APU", "PCP-PEV"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")


def fold(text: str) -> str:
    return re.sub(r"[^a-zãâçéíóúõ ]", "", text.lower().replace("s. vicente", "são vicente")).strip()


def circle_of(label: str) -> str | None:
    name = re.sub(r"\s+", " ", fold(label))
    for code, circle in CIRCLES.items():
        if name == fold(circle):
            return code
    if name.startswith("totais"):
        return TOTAL
    return None


def url(spec: Year) -> str:
    return f"{CNE}/{spec.pdf}"


def fetch() -> None:
    for spec in YEARS:
        raw.fetch(SOURCE, url(spec), suffix=".pdf")


def artifact(spec: Year) -> raw.Artifact:
    return raw.cached(SOURCE, url(spec))


# ---------------------------------------------------------------------------------------------------------------------
# Reading the maps

type Box = tuple[float, float, float, float]


@dataclass
class Cell:
    value: int
    page: int
    box: Box


@dataclass
class Circle:
    cells: dict[str, Cell] = field(default_factory=dict)  # i, v, b, n, and each list's votes by label
    seats: dict[str, int] = field(default_factory=dict)


def keyed(spec: Year) -> dict[str, tuple[list[int | None], dict[str, int]]]:
    """The keyed rows: per circle, the cells in column order (None where a list did not run) and the seats."""
    out: dict[str, tuple[list[int | None], dict[str, int]]] = {}
    lead = ["i", "v", "n"] if spec.joined else ["i", "v", "b", "n"]
    for line in spec.keyed.strip().splitlines():
        head, _, tail = line.partition("|")
        match = re.match(r"(\D+?)\s+([\d\s]+)$", head.strip())
        if match is None:
            raise ValueError(f"{spec.year}: cannot read {line!r}")
        code = circle_of(match.group(1))
        if code is None:
            raise ValueError(f"{spec.year}: unknown circle {match.group(1)!r}")
        values: list[int | None] = [int(n) for n in match.group(2).split()]
        if len(values) != len(lead):
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


def columns(spec: Year) -> list[str]:
    return [*(["i", "v", "n"] if spec.joined else ["i", "v", "b", "n"]), *spec.lists]


def scanned(spec: Year) -> dict[str, Circle]:
    boxes: dict[str, list[float]] = json.loads(BOXES.read_text())
    circles: dict[str, Circle] = {}
    for code, (values, seats) in keyed(spec).items():
        circle = circles[code] = Circle(seats=seats)
        for column, value in zip(columns(spec), values, strict=True):
            if value is None:
                continue
            page, x0, y0, x1, y1 = boxes[f"{spec.year}/{code}/{column}"]
            circle.cells[column] = Cell(value, int(page), (x0, y0, x1, y1))
    return circles


WORD = re.compile(
    r'<page width="([\d.]+)" height="([\d.]+)">|<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>'
)


@dataclass
class Word:
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


def words(path: Path, pages: tuple[int, ...]) -> list[Word]:
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found: list[Word] = []
    page = 0
    for match in WORD.finditer(out):
        if match.group(1):
            page += 1
            continue
        if page in pages:
            x0, y0, x1, y1 = (float(match.group(n)) for n in (3, 4, 5, 6))
            found.append(Word(match.group(7).replace("&amp;", "&"), page, x0, y0, x1, y1))
    return found


def lines(found: list[Word]) -> list[list[Word]]:
    """Words grouped into printed lines, by page and the middle of the word's height, each line left to right."""
    grouped: list[list[Word]] = []
    for word in sorted(found, key=lambda w: (w.page, (w.y0 + w.y1) / 2, w.x0)):
        middle = (word.y0 + word.y1) / 2
        for line in reversed(grouped[-6:]):
            if line[0].page == word.page and abs((line[0].y0 + line[0].y1) / 2 - middle) <= 2.5:
                line.append(word)
                break
        else:
            grouped.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in grouped]


@dataclass
class Token:
    kind: str  # int, pct, dash
    text: str
    page: int
    box: Box


def tokens(line: list[Word]) -> list[Token]:
    """The cells of a row after its label: thousands, printed with a narrow space ("13 013"), are joined."""
    out: list[Token] = []
    i = 0
    while i < len(line):
        w = line[i]
        box = (w.x0, w.y0, w.x1, w.y1)
        if w.text in ("–", "-", "—", "−"):
            out.append(Token("dash", w.text, w.page, box))
        elif re.fullmatch(r"\d+", w.text):
            joined, x1 = w.text, w.x1
            while (
                i + 1 < len(line)
                and re.fullmatch(r"\d{3}", line[i + 1].text)
                and len(joined) <= 3
                and line[i + 1].x0 - x1 < 3.0
            ):
                joined += line[i + 1].text
                x1 = line[i + 1].x1
                i += 1
            out.append(Token("int", joined, w.page, (w.x0, w.y0, x1, w.y1)))
        elif re.fullmatch(r"\d+,\d+", w.text) or re.fullmatch(r"\d+ \d+", w.text):
            out.append(Token("pct", w.text, w.page, box))
        i += 1
    return out


def label_of(line: list[Word]) -> tuple[str, int]:
    parts: list[str] = []
    for i, word in enumerate(line):
        if re.fullmatch(r"[\d,–-]+", word.text):
            return " ".join(parts), i
        if word.text != ".":
            parts.append(word.text)
    return " ".join(parts), len(line)


def text_layer(spec: Year) -> dict[str, Circle]:
    """Rows of each block, read between the block's header and its totals row; each cell goes to the column of the
    totals row whose right edge is nearest, since every column has a figure in the totals row."""
    circles: dict[str, Circle] = {}
    block = 0
    pending: list[tuple[str, list[Token]]] = []
    for line in lines(words(artifact(spec).path, spec.pages)):
        if block >= len(spec.blocks):
            break
        label, start = label_of(line)
        code = circle_of(label)
        if code is None or start >= len(line):
            continue
        row = tokens(line[start:])
        if code != TOTAL:
            pending.append((code, row))
            continue
        layout = spec.blocks[block].split()
        if len(row) != len(layout):
            raise ValueError(f"{spec.year}: the totals row of block {block} has {len(row)} cells, not {len(layout)}")
        edges = [(tok.box[2], tok.page) for tok in row]
        for code, cells in [*pending, (TOTAL, row)]:
            circle = circles.setdefault(code, Circle())
            used: set[int] = set()
            for tok in cells:
                at = min(
                    (n for n, (_, page) in enumerate(edges) if page == tok.page or code == TOTAL),
                    key=lambda n: abs(edges[n][0] - tok.box[2]),
                )
                column = layout[at]
                if column == "%" or (tok.kind == "dash" and abs(edges[at][0] - tok.box[2]) > 15):
                    continue
                if at in used:
                    raise ValueError(f"{spec.year} {code}: two cells fall in column {column}")
                used.add(at)
                if tok.kind == "dash":
                    continue
                if tok.kind != "int":
                    raise ValueError(f"{spec.year} {code}: {tok.text!r} in column {column}")
                if column == "m":
                    circle.seats[layout[at - 2]] = int(tok.text)
                elif column != "t":
                    circle.cells[column] = Cell(int(tok.text), tok.page, tok.box)
        pending = []
        block += 1
    if block < len(spec.blocks):
        raise ValueError(f"{spec.year}: found {block} totals rows, expected {len(spec.blocks)}")
    return circles


def circles_of(spec: Year) -> dict[str, Circle]:
    circles = text_layer(spec) if spec.blocks else scanned(spec)
    missing = set(CIRCLES) - set(circles)
    if missing or TOTAL not in circles:
        raise ValueError(f"{spec.year}: missing circles {sorted(missing)}")
    return circles


def check(spec: Year, circles: dict[str, Circle]) -> list[str]:
    """Where the map's own figures disagree: each circle's cells against its voters, and the circles against the
    printed totals."""
    problems: list[str] = []
    leads = ["b", "n"]
    for code, name in CIRCLES.items():
        c = circles[code].cells
        listed = sum(cell.value for column, cell in c.items() if column not in ("i", "v"))
        if listed != c["v"].value:
            problems.append(f"{name}: lists, blank, and invalid add up to {listed}, the voters are {c['v'].value}")
    total = circles[TOTAL]
    for column in ["i", "v", *leads, *spec.lists]:
        summed = sum(circles[code].cells[column].value for code in CIRCLES if column in circles[code].cells)
        printed = total.cells[column].value if column in total.cells else 0
        if summed != printed:
            problems.append(f"{column}: the circles add up to {summed}, the map prints {printed}")
    for label in spec.lists:
        summed = sum(circles[code].seats.get(label, 0) for code in CIRCLES)
        if summed != total.seats.get(label, 0):
            problems.append(
                f"{label} seats: the circles add up to {summed}, the map prints {total.seats.get(label, 0)}"
            )
    return problems


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
        if spec.blocks:
            continue
        rows = keyed(spec)
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
                upright = picture.rotate(-90, expand=True) if spec.turned else picture
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
        _place(spec, rows, found, out)
    BOXES.write_text(json.dumps(dict(sorted(out.items())), indent=0) + "\n")


def _place(
    spec: Year,
    rows: dict[str, tuple[list[int | None], dict[str, int]]],
    found: list[tuple[int, str, Box]],
    out: dict[str, list[float]],
) -> None:
    """Matches each keyed value to an OCR token on its row; a cell the OCR missed takes its column's span and its
    row's line. Boxes are stored in PDF points on the page as printed."""
    names = columns(spec)
    first = spec.split or len(names)
    page_of = {column: spec.pages[0] if n < first else spec.pages[-1] for n, column in enumerate(names)}
    matched: dict[tuple[str, str], tuple[int, Box]] = {}
    anchors: dict[tuple[str, int], float] = {}
    for code, (values, _) in rows.items():
        for page in spec.pages:
            wanted = {
                str(v) for c, v in zip(names, values, strict=True) if v is not None and v >= 100 and page_of[c] == page
            }
            ys = [(box[1] + box[3]) / 2 for p, text, box in found if p == page and text in wanted]
            if ys:
                anchors[(code, page)] = statistics.median(ys)
    for code, (values, _) in rows.items():
        for column, value in zip(names, values, strict=True):
            page = page_of[column]
            if value is None or (code, page) not in anchors:
                continue
            y = anchors[(code, page)]
            near = [
                box
                for p, text, box in found
                if p == page and text == str(value) and abs((box[1] + box[3]) / 2 - y) < 12
            ]
            if near:
                matched[(code, column)] = (page, min(near, key=lambda b: abs((b[1] + b[3]) / 2 - y)))
    height = statistics.median(box[3] - box[1] for _, box in matched.values())
    for code, (values, _) in rows.items():
        for column, value in zip(names, values, strict=True):
            if value is None:
                continue
            page = page_of[column]
            if (code, column) in matched:
                box = matched[(code, column)][1]
            else:
                spans = [b for (c, col), (_, b) in matched.items() if col == column]
                if not spans or (code, page) not in anchors:
                    raise ValueError(f"{spec.year} {code} {column}: no box")
                x0, x1 = statistics.median(b[0] for b in spans), statistics.median(b[2] for b in spans)
                y = anchors[(code, page)]
                box = (x0, y - height / 2, x1, y + height / 2)
            out[f"{spec.year}/{code}/{column}"] = [page, *_unturn(spec, page, box)]


def _unturn(spec: Year, page: int, box: Box) -> list[float]:
    """A box on the upright image, in pixels, as a box on the page as printed, in points."""
    scale = 72 / SCAN_DPI
    x0, y0, x1, y1 = box
    if spec.turned:
        pixels = _page_height(spec, page) * SCAN_DPI / 72
        x0, y0, x1, y1 = y0, pixels - x1, y1, pixels - x0
    return [round(c * scale, 1) for c in (x0, y0, x1, y1)]


def _page_height(spec: Year, page: int) -> float:
    info = subprocess.run(
        ["pdfinfo", "-f", str(page), "-l", str(page), str(artifact(spec).path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = re.search(rf"Page\s+{page} size:\s+([\d.]+) x ([\d.]+)", info)
    if match is None:
        raise ValueError(f"{spec.year}: no size for page {page}")
    return float(match.group(2))


# ---------------------------------------------------------------------------------------------------------------------
# Packages

METHOD = (
    "Counted by electoral circle: one for each of Madeira's eleven municipalities, as published in the National "
    "Elections Commission's official map of results (mapa oficial), which the Commission printed in the Diário da "
    "República. The map goes no finer than the circle. Registered voters are the voters on the roll, the voters are "
    "those who voted, and blank and invalid (nulos) ballots are printed apart from the votes for each list. Seats are "
    "as the map declares them."
)
SCAN_METHOD = (
    " The map is a scanned page. Its figures were keyed from the page image and checked against the map's own sums; "
    "each value's box on the page was found by OCR, or, where the OCR missed a cell, from its column and row."
)
JOINED = " The 1976 map prints blank and invalid ballots as one figure; it is stored here as invalid ballots."


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


def party_key(label: str) -> str:
    return PARTY_OF.get(label, label)


def label_party(label: str) -> str:
    return key("party", party_key(label))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties and coalitions of the Madeira regional elections of 1976 to 2004, and the National Elections "
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
        url="https://www.cne.pt/content/eleicoes-referendos-resultados-oficiais#madeira",
        license="Official publication in the Diário da República; reuse terms are not stated",
        attribution="Comissão Nacional de Eleições",
    )
    for spec in YEARS:
        p.artifact(artifact(spec), CNE_SOURCE)
    parties: dict[str, list[str]] = {}
    for spec in YEARS:
        for label in spec.lists:
            parties.setdefault(party_key(label), [])
            if label not in parties[party_key(label)]:
                parties[party_key(label)].append(label)
    for party, labels in parties.items():
        id = key("party", party)
        p.add("party", id=id, kind="bloc" if party in BLOCS else "party", polity_id=PORTUGAL)
        p.add("external_id", entity_id=id, scheme="pt.cne_madeira.party", value=party)
        p.name(id, NAMES[party], "pt", "official")
        if party in OLDER:
            p.name(id, OLDER[party], "pt", "historical", False)
        for label in labels:
            p.name(id, label, "pt", "abbreviation", False)
    return p


def election(spec: Year) -> Package:
    day = spec.day
    p = Package(
        day_key(spec),
        SOURCE,
        f"The Madeira regional election of {day} by municipal circle, from the National Elections Commission's "
        "official map of results",
        depends=[f"{SOURCE}/registry", "pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    p.uses(artifact(spec))
    circles = circles_of(spec)
    problems = check(spec, circles)
    election_id = key("election", day)
    path = f"pt/{day}-madeira-regional"
    p.add("election", id=election_id, polity_id=PORTUGAL, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Madeira regional election", "en", "official")
    p.name(election_id, f"Eleições legislativas regionais da Madeira de {day.year}", "pt", "native", False)
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
    method = METHOD + ("" if spec.blocks else SCAN_METHOD) + (JOINED if spec.joined else "")
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    for code, name in sorted(CIRCLES.items(), key=lambda item: item[1]):
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
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
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
        p.name(unit, name, "pt", "official")
        for measure, column in ((REGISTERED, "i"), (CAST, "v"), (BLANK, "b"), (INVALID, "n")):
            if column in circle.cells:
                _value(values, locators, page_ids, result_set, unit, None, measure, circle.cells[column])
        position = 0
        for label in spec.lists:
            if label not in circle.cells:
                if circle.seats.get(label):
                    raise ValueError(f"{spec.year} {name}: seats for {label}, which did not run")
                continue
            position += 1
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
            _value(values, locators, page_ids, result_set, unit, option, VOTES, circle.cells[label])
            if circle.seats.get(label):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=circle.seats[label])
        for problem in problems:
            if problem.startswith(f"{name}:"):
                p.add(
                    "note",
                    id=key("note", day, problem),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The map's own figures do not add up here: {problem.split(': ', 1)[1]}. "
                    "They are stored as printed.",
                    lang="en",
                )
    if spec.note:
        p.add("note", id=key("note", day, "map"), entity_id=election_id, kind="caveat", body_md=spec.note, lang="en")
    regional = [problem for problem in problems if problem.split(":")[0] not in CIRCLES.values()]
    if regional:
        text = "; ".join(_described(problem) for problem in regional)
        p.add(
            "note",
            id=key("note", day, "totals"),
            entity_id=election_id,
            kind="caveat",
            body_md=f"The map's printed totals for the region do not match its circles: {text}. "
            "The circles are stored as printed.",
            lang="en",
        )
    for problem in problems:
        print(f"{spec.year}: {problem}")
    _frames(p, values, locators)
    return p


MEASURE_NAMES = {"i": "registered voters", "v": "voters", "b": "blank ballots", "n": "invalid ballots"}


def _described(problem: str) -> str:
    column, _, rest = problem.partition(": ")
    return f"{MEASURE_NAMES.get(column, column)}, {rest}"


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
