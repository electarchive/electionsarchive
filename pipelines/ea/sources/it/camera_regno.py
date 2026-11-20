"""The Chamber of Deputies of the Kingdom of Italy: the general elections of 1924, 1929 and 1934.

The figures are transcribed from the Statistica delle elezioni generali politiche volumes that the statistical office
published after each election, digitized in ISTAT's library. The 1929 and 1934 ballots asked the voters to approve or
reject one national list; their volumes print the count by province. The 1924 volume prints each list's votes by
circoscrizione and the seats each list won there. Every transcribed number is checked against the volume's own
subtotals and totals before a package is built, and each number keeps the page and box it was read from.
"""

import csv
import difflib
import re
import struct
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "it.camera_regno"
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
BASE = "https://ebiblio.istat.it/digibib/Elezioni/"
HERE = Path(__file__).parent
PLEBISCITES = HERE / "camera_regno_plebiscites.tsv"
LISTS_1924 = HERE / "camera_regno_1924.tsv"
PAGES = DATA / "pages" / SOURCE
DPI = 100
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
PROVINCE = "it.camera_regno_provincia"
CIRCOSCRIZIONE = "it.camera_regno_circoscrizione"
LICENSE = "Public domain: official statistics of the Kingdom of Italy, published between 1924 and 1934"
TOLERANCE = 0.004


class Volume(NamedTuple):
    file: str
    publisher: str
    title: str
    notes: str


VOLUMES = {
    1924: Volume(
        "RAV0130536StatisticadelleElezioniGeneraliPolitiche1924.pdf",
        "Ministero dell'economia nazionale, Direzione generale della statistica",
        "Statistica delle elezioni generali politiche per la XXVII legislatura (6 aprile 1924)",
        "Introduction, chapter II: Voti riportati da ciascuna lista nelle singole Circoscrizioni (printed pages XXX "
        "and XXXI, scan page 28); the seat allocation printed at the head of each circoscrizione's section (scan pages "
        "53 to 115); and Risultati numerici principali delle elezioni generali politiche avvenute il 6 aprile 1924 "
        "(printed pages 96 and 97, scan page 132). Digitized by the ISTAT library.",
    ),
    1929: Volume(
        "IST0003293StatElezGenpolitiche28leg24marzo1929.pdf",
        "Istituto centrale di statistica del Regno d'Italia",
        "Statistica delle elezioni generali politiche per la XXVIII legislatura (24 marzo 1929)",
        "Chapter III: Elezioni politiche del 24 marzo 1929, Risultati accertati dalla Giunta delle Elezioni (printed "
        "pages 53 to 56, scan pages 59 to 62). Digitized by the ISTAT library.",
    ),
    1934: Volume(
        "IST0003295StatElezGenpol1934.pdf",
        "Istituto centrale di statistica del Regno d'Italia",
        "Statistica delle elezioni generali politiche per la XXIX legislatura (25 marzo 1934)",
        "Chapter III: Elezioni politiche del 25 marzo 1934-XII, Risultati accertati dalla Giunta delle Elezioni "
        "(printed pages 36 to 39, scan pages 40 to 43). Digitized by the ISTAT library.",
    ),
}
DAYS = {1924: date(1924, 4, 6), 1929: date(1929, 3, 24), 1934: date(1934, 3, 25)}
LEGISLATURES = {1924: "XXVII", 1929: "XXVIII", 1934: "XXIX"}
# The printed national totals: registered, voters, approving, rejecting, and null or contested ballots.
NATIONAL = {
    1929: (9_682_630, 8_661_820, 8_517_838, 135_773, 8_209),
    1934: (10_527_608, 10_060_426, 10_043_875, 15_215, 1_336),
}
DESIGNATED = 400
QUESTION = "Approvate voi la lista dei deputati designati dal Gran Consiglio Nazionale del Fascismo?"
QUESTION_EN = "Do you approve the list of deputies designated by the National Grand Council of Fascism?"
# Column bands (left, right) of the plebiscite tables, as fractions of the page width, for values the text layer
# misreads; the rest are boxed from the words themselves.
PLEBISCITE_COLUMNS = {
    1929: ((0.26, 0.36), (0.36, 0.45), (0.50, 0.60), (0.66, 0.75), (0.79, 0.85)),
    1934: ((0.26, 0.37), (0.37, 0.46), (0.50, 0.61), (0.63, 0.71), (0.74, 0.83)),
}
COMPARTMENT_RENAMES = {"Basilicata": "Lucania"}

CIRCOSCRIZIONI = (
    "Piemonte",
    "Liguria",
    "Lombardia",
    "Veneto",
    "Venezia Giulia",
    "Emilia",
    "Toscana",
    "Marche",
    "Lazio e Umbria",
    "Abruzzi e Molise",
    "Campania",
    "Puglie",
    "Calabrie e Basilicata",
    "Sicilia",
    "Sardegna",
)
# Scan page 132: registered electors, voters, null or contested list votes, valid list votes, and deputies to elect.
ACCOUNTING_1924 = {
    "Piemonte": (1_197_561, 645_900, 55_471, 590_429, 47),
    "Liguria": (429_601, 252_702, 16_504, 236_198, 18),
    "Lombardia": (1_542_651, 1_112_002, 88_024, 1_023_978, 70),
    "Veneto": (1_109_602, 716_537, 55_200, 661_337, 53),
    "Venezia Giulia": (469_272, 287_949, 19_624, 268_325, 23),
    "Emilia": (921_996, 682_024, 41_657, 640_367, 41),
    "Toscana": (904_824, 651_322, 48_270, 603_052, 38),
    "Marche": (378_613, 217_029, 14_986, 202_043, 16),
    "Lazio e Umbria": (669_469, 404_339, 26_586, 377_753, 30),
    "Abruzzi e Molise": (519_603, 280_426, 9_598, 270_828, 21),
    "Campania": (1_102_598, 620_777, 21_233, 599_544, 49),
    "Puglie": (662_011, 533_384, 6_969, 526_415, 32),
    "Calabrie e Basilicata": (645_430, 357_171, 11_383, 345_788, 28),
    "Sicilia": (1_270_334, 707_038, 26_178, 680_860, 57),
    "Sardegna": (245_771, 145_851, 7_266, 138_585, 12),
}
NATIONAL_1924 = (12_069_336, 7_614_451, 448_949, 7_165_502, 535)
# Seats each list won in each circoscrizione, from the allocation tables at the head of each section.
SEATS_1924 = {
    "Piemonte": {21: 31, 3: 3, 5: 3, 15: 3, 18: 3, 19: 2, 23: 2},
    "Liguria": {21: 12, 3: 2, 5: 2, 18: 1, 19: 1},
    "Lombardia": {21: 47, 3: 5, 5: 8, 14: 1, 15: 1, 19: 3, 23: 5},
    "Veneto": {21: 35, 3: 2, 5: 8, 10: 1, 12: 2, 19: 2, 23: 3},
    "Venezia Giulia": {21: 15, 3: 1, 5: 2, 10: 1, 12: 2, 19: 2},
    "Emilia": {21: 27, 3: 3, 5: 4, 10: 2, 19: 2, 23: 3},
    "Toscana": {21: 25, 3: 2, 4: 5, 5: 2, 10: 1, 19: 1, 23: 2},
    "Marche": {21: 11, 3: 1, 5: 1, 10: 1, 19: 1, 23: 1},
    "Lazio e Umbria": {21: 20, 3: 1, 4: 3, 5: 2, 10: 1, 19: 1, 23: 2},
    "Abruzzi e Molise": {21: 14, 3: 1, 4: 5, 8: 1},
    "Campania": {21: 33, 2: 3, 3: 1, 5: 2, 6: 1, 7: 1, 8: 4, 13: 1, 19: 1, 23: 2},
    "Puglie": {21: 21, 4: 6, 6: 3, 16: 1, 19: 1},
    "Calabrie e Basilicata": {21: 18, 3: 1, 5: 1, 7: 2, 8: 2, 19: 1, 22: 2, 23: 1},
    "Sicilia": {21: 38, 1: 1, 3: 1, 5: 3, 7: 7, 9: 1, 13: 4, 19: 1, 23: 1},
    "Sardegna": {21: 8, 5: 1, 8: 1, 17: 2},
}
# Column centers of the two-page table on scan page 28, as fractions of the page width.
CENTERS_1924 = (
    0.198, 0.251, 0.304, 0.357, 0.409, 0.462, 0.537, 0.580, 0.625, 0.671, 0.717, 0.762, 0.807, 0.852, 0.897, 0.950,
)  # fmt: skip
# The symbol each list carried on the ballot, and the party grouping the volume assigns it to.
LISTS = {
    1: ("Monte Etna", "opposizione_costituzionale"),
    2: ("Bandiera nazionale con scudo sabaudo", "liberale"),
    3: ("Sole nascente", "socialista_unitario"),
    4: ("Aquila romana e fascio littorio", "fascista"),
    5: ("Scudo crociato", "popolare"),
    6: ("Orologio", "liberale"),
    7: ("Fiaccola e lettere D. S.", "democratico_sociale"),
    8: ("Stella nera a cinque punte", "opposizione_costituzionale"),
    9: ("Bilancia", "liberale"),
    10: ("Vanga ed edera", "repubblicano"),
    11: ("Stella a cinque punte", "opposizione_costituzionale"),
    12: ("Gnafalio", "slavi_tedeschi"),
    13: ("Cavallo", "opposizione_costituzionale"),
    14: ("Aquila e stella a cinque punte", "fascista_dissidente"),
    15: ("Spighe di grano e grappoli di uva", "contadini"),
    16: ("Stemma di Bari", "liberale"),
    17: ("Bandiera con quattro mori", "sardo"),
    18: ("Bandiera nazionale con scudo sabaudo", "liberale"),
    19: ("Falce, martello e corona di spighe", "comunista"),
    20: ("David fromboliere", "liberale"),
    21: ("Fascio littorio", "fascista"),
    22: ("Bandiera italiana e corona reale", "liberale"),
    23: ("Falce, martello e libro", "socialista_massimalista"),
}
PARTIES = {
    "fascista": ("Partito nazionale fascista", "National Fascist Party"),
    "fascista_dissidente": ("Fascisti dissidenti", "Dissident Fascists"),
    "socialista_unitario": ("Partito socialista unitario", "Unitary Socialist Party"),
    "socialista_massimalista": ("Partito socialista italiano (massimalista)", "Italian Socialist Party"),
    "comunista": ("Partito comunista d'Italia", "Communist Party of Italy"),
    "repubblicano": ("Partito repubblicano italiano", "Italian Republican Party"),
    "popolare": ("Partito popolare italiano", "Italian People's Party"),
    "liberale": ("Liberali", "Liberals"),
    "opposizione_costituzionale": ("Opposizione costituzionale", "Constitutional Opposition"),
    "democratico_sociale": ("Democrazia sociale", "Social Democracy"),
    "contadini": ("Partito dei contadini d'Italia", "Peasants' Party of Italy"),
    "sardo": ("Partito sardo d'azione", "Sardinian Action Party"),
    "slavi_tedeschi": ("Slavi e tedeschi", "Slavs and Germans"),
}
# Party totals printed in Liste classificate secondo i partiti politici (scan page 34).
PARTY_TOTALS_1924 = {
    "fascista": 4_653_488,
    "fascista_dissidente": 18_062,
    "socialista_unitario": 422_957,
    "socialista_massimalista": 360_694,
    "comunista": 268_191,
    "repubblicano": 133_714,
    "popolare": 645_789,
    "liberale": 233_521,
    "opposizione_costituzionale": 157_932,
    "democratico_sociale": 111_035,
    "contadini": 73_569,
    "sardo": 24_059,
    "slavi_tedeschi": 62_491,
}

BODY_NOTE = (
    "The elected chamber of the Parliament of the Kingdom of Italy from 1861, beside an appointed Senate. Deputies were "
    "chosen in single-member colleges with a runoff until 1919 (with multi-member colleges from 1882 to 1891), by "
    "proportional representation in multi-member colleges in 1919 and 1921, under the Acerbo law's national majority "
    "premium in 1924, and by approving or rejecting one national list in 1929 and 1934. The Chamber of Fasces and "
    "Corporations, which was not elected, replaced it in 1939."
)
SYSTEM_1924 = (
    "Law 2444 of 18 November 1923 (the Acerbo law) made the Kingdom one national college of 535 seats, voted in 15 "
    "circoscrizioni. If the national list with the most valid votes had at least a quarter of them, it took two thirds "
    "of the seats, shared among the circoscrizioni; each circoscrizione's remaining seats went to its other lists by "
    "the quotient of their votes over those seats, then by the largest remainders. The engine applies the premium "
    "within each circoscrizione, which matches every circoscrizione's printed allocation but that of Calabrie e "
    "Basilicata, where one premium seat passed to a minority list after a majority candidate died."
)
SYSTEM_PLEBISCITE = (
    "Under the electoral law of 17 May 1928 (Testo unico of 2 September 1928), the National Grand Council of Fascism "
    "drew up one national list of 400 deputies from candidates the national confederations and associations proposed, "
    "and the voters approved or rejected it by marking Sì or No. If the list was rejected, the election was to be held "
    "again with competing lists."
)
PLEBISCITE_NOTE = (
    "Counts by province are transcribed from the volume's table of the results the Chamber's Giunta delle Elezioni "
    "confirmed. In each province the approving, rejecting, and null or contested ballots add up to the voters; the "
    "provinces add up to each compartimento's printed subtotal, and the compartimenti to the Kingdom's total. Valid "
    "votes are not printed by province. The volume notes that the whole Kingdom formed one national college, and "
    "that it prints the provinces for statistical purposes."
)
NOTE_1924 = (
    "List votes by circoscrizione are transcribed from the volume's table of the votes each list won in each "
    "circoscrizione; every list's row adds up to its printed national total, and every circoscrizione's column to its "
    "printed total of valid list votes. Registered electors, voters, and null or contested list votes come from the "
    "volume's table of main results. Seats are as printed in each circoscrizione's allocation table. The volume groups "
    "lists that carried the same symbol in several circoscrizioni, and it assigns each list to a party in its table of "
    "lists by party."
)
CALABRIA = (
    "Giuseppe De Nava, a candidate of the majority list, died before the seats were assigned, so the majority list "
    "took 18 seats here rather than 19, and the seat passed to list 19 (communist) by the largest remainder."
)
PUGLIE = (
    "The quotients gave list 4 seven seats, but it held only six names, so the seventh went to list 16 by the largest "
    "remainder."
)


def url(year: int) -> str:
    return BASE + VOLUMES[year].file


def fetch() -> None:
    for year in VOLUMES:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


BODY = key("body", "camera")
SYSTEMS = {1924: key("electoral_system", "acerbo"), 1929: key("electoral_system", "plebiscite")}
SYSTEMS[1934] = SYSTEMS[1929]
SOURCES = {year: key("source", year) for year in VOLUMES}


def party(code: str) -> str:
    return key("party", code)


# Reading the volumes.


def plebiscites() -> pl.DataFrame:
    frame = pl.read_csv(PLEBISCITES, separator="\t", infer_schema_length=0).with_columns(
        pl.col("year", "page", "iscritti", "votanti", "si", "no", "nulli").cast(pl.Int64)
    )
    counts = ("iscritti", "votanti", "si", "no", "nulli")
    for year, totals in NATIONAL.items():
        rows = frame.filter(pl.col("year") == year)
        for row in rows.iter_rows(named=True):
            if row["si"] + row["no"] + row["nulli"] != row["votanti"] or row["votanti"] > row["iscritti"]:
                raise ValueError(f"{year} {row['compartimento']} {row['provincia']}: the ballots do not add up")
        provinces = rows.filter(pl.col("provincia").is_not_null())
        for (region,), group in provinces.group_by("compartimento"):
            printed = rows.filter((pl.col("compartimento") == region) & pl.col("provincia").is_null())
            if printed.height != 1 or any(group[c].sum() != printed[c][0] for c in counts):
                raise ValueError(f"{year} {region}: the provinces do not add up to the printed subtotal")
        national = rows.filter(pl.col("compartimento").is_null())
        if tuple(national.row(0, named=True)[c] for c in counts) != totals:
            raise ValueError(f"{year}: the printed national row differs from the stated totals")
        if tuple(provinces[c].sum() for c in counts) != totals:
            raise ValueError(f"{year}: the provinces do not add up to the national total")
    return frame


def lists_1924() -> dict[int, list[int]]:
    with LISTS_1924.open() as file:
        rows = list(csv.reader(file, delimiter="\t"))
    if rows[0][1:] != [*CIRCOSCRIZIONI, "Regno"]:
        raise ValueError("unexpected 1924 columns")
    table = {int(row[0]): [int(value) for value in row[1:]] for row in rows[1:]}
    for number, values in table.items():
        if sum(values[:-1]) != values[-1]:
            raise ValueError(f"1924 list {number}: the circoscrizioni do not add up to its national total")
    for column, name in enumerate(CIRCOSCRIZIONI):
        if sum(values[column] for values in table.values()) != ACCOUNTING_1924[name][3]:
            raise ValueError(f"1924 {name}: the lists do not add up to the valid votes")
        registered, voters, invalid, valid, seats = ACCOUNTING_1924[name]
        if invalid + valid != voters or voters > registered:
            raise ValueError(f"1924 {name}: the ballots do not add up")
        won = SEATS_1924[name]
        if sum(won.values()) != seats or any(table[number][column] == 0 for number in won):
            raise ValueError(f"1924 {name}: the seats do not match the circoscrizione")
    totals = [sum(values[i] for values in ACCOUNTING_1924.values()) for i in range(5)]
    if tuple(totals) != NATIONAL_1924 or sum(values[-1] for values in table.values()) != NATIONAL_1924[3]:
        raise ValueError("1924: the circoscrizioni do not add up to the national totals")
    for code, total in PARTY_TOTALS_1924.items():
        if sum(table[n][-1] for n, (_, grouping) in LISTS.items() if grouping == code) != total:
            raise ValueError(f"1924 {code}: the lists do not add up to the party's printed total")
    return table


# Page images and boxes.

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
type Box = tuple[float, float, float, float]
type Word = tuple[float, float, float, float, str]


def words(year: int, page: int) -> list[Word]:
    html = subprocess.run(
        ["pdftotext", "-bbox-layout", "-f", str(page), "-l", str(page), str(raw.cached(SOURCE, url(year)).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', html)
    if found is None:
        raise ValueError(f"{year} page {page}: no page box")
    width, height = float(found[1]), float(found[2])
    return [
        (float(x0) / width, float(y0) / height, float(x1) / width, float(y1) / height, text)
        for x0, y0, x1, y1, text in WORD.findall(html)
    ]


def image(year: int, page: int) -> tuple[str, int, int]:
    artifact = raw.cached(SOURCE, url(year))
    target = PAGES / artifact.sha256 / f"{page}.png"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(artifact.path), str(target.with_suffix(""))],
            check=True,
        )
    width, height = struct.unpack(">II", target.read_bytes()[16:24])
    return f"pages/{SOURCE}/{artifact.sha256}/{page}.png", width, height


def lines(found: list[Word], right: float) -> list[Word]:
    """The text lines in a table's label column, top to bottom."""
    labels = sorted((w for w in found if w[0] < right and re.search(r"[A-Za-z]{2}", w[4])), key=lambda w: w[1])
    merged: list[Word] = []
    for word in labels:
        if merged and word[1] - merged[-1][1] < 0.006:
            x0, y0, x1, y1, text = merged[-1]
            merged[-1] = (x0, min(y0, word[1]), max(x1, word[2]), max(y1, word[3]), f"{text} {word[4]}")
        else:
            merged.append(word)
    return merged


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("&apos;", ""))


def shuffled(text: str) -> str:
    """The words of a label in a fixed order, for text layers that print a label's words out of order."""
    return "".join(sorted(fold(word) for word in text.split()))


def bands(found: list[Word], labels: list[str], right: float) -> list[tuple[float, float]]:
    """The vertical band of each labelled row, matching labels to the column's lines in order."""
    candidates = lines(found, right)
    out: list[tuple[float, float]] = []
    start = 0
    for label in labels:
        target = fold(label)
        best, score = None, 0.0
        for index in range(start, min(start + 6, len(candidates)) if out else len(candidates)):
            text = candidates[index][4]
            ratio = max(
                difflib.SequenceMatcher(None, target, fold(text)).ratio(),
                difflib.SequenceMatcher(None, shuffled(label), shuffled(text)).ratio(),
                0.9 if len(fold(text)) >= 3 and (fold(text) in target or target in fold(text)) else 0.0,
            )
            if ratio > score:
                best, score = index, ratio
        if best is None or score < 0.5:
            raise ValueError(f"no line for {label!r} after {candidates[start - 1][4] if start else 'the top'}")
        out.append((candidates[best][1], candidates[best][3]))
        start = best + 1
    return out


def digits(text: str) -> str | None:
    return text if re.fullmatch(r"\d+", text) else None


def place(found: list[Word], band: tuple[float, float], values: list[int], left: float) -> list[Box | None]:
    """Box each value by the words on its row that spell it, read left to right."""
    top, bottom = band
    row = sorted(
        (w for w in found if w[0] >= left and top - TOLERANCE <= (w[1] + w[3]) / 2 <= bottom + TOLERANCE),
        key=lambda w: w[0],
    )
    tokens = [(w, digits(w[4])) for w in row]
    boxes: list[Box | None] = []
    position = 0
    for value in values:
        wanted = str(value)
        box = None
        for begin in range(position, len(tokens)):
            spelled, end = "", begin
            while end < len(tokens) and (piece := tokens[end][1]) is not None and len(spelled) < len(wanted):
                spelled += piece
                end += 1
            if spelled == wanted and (begin == 0 or tokens[begin - 1][1] is None or value == 0):
                used = [w for w, _ in tokens[begin:end]]
                box = (min(w[0] for w in used), top, max(w[2] for w in used), bottom)
                position = end
                break
        boxes.append(box)
    return boxes


def columnar(
    placed: list[list[Box | None]], rows: list[tuple[float, float]], fallback: tuple[tuple[float, float], ...]
) -> list[list[Box]]:
    """Box every value by its row's band and its column's band on the page.

    A column's band comes from the boxes the text layer placed well: numbers are set flush right, so the boxes whose
    right edge sits at the column's median right edge give its extent. A column the text layer never placed falls
    back to the band measured on the page images.
    """
    columns: list[tuple[float, float]] = []
    for index, default in enumerate(fallback):
        found = [box for row in placed if (box := row[index]) is not None]
        if not found:
            columns.append(default)
            continue
        edge = sorted(box[2] for box in found)[len(found) // 2]
        aligned = [box for box in found if abs(box[2] - edge) < 0.012]
        band = (min(box[0] for box in aligned) - 0.003, max(box[2] for box in aligned) + 0.003)
        # Where two columns print the same number, the text layer can place one column's value in the other.
        columns.append(band if default[0] <= (band[0] + band[1]) / 2 <= default[1] else default)
    return [[(left, top, right, bottom) for left, right in columns] for top, bottom in rows]


# Packages.


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[year]}" for year in sorted(VOLUMES))]


def parse() -> None:
    registry().write()
    chamber_1924().write()
    for year in (1929, 1934):
        plebiscite(year).write()


def emit() -> None:
    package.run("load", packages())


def source(p: Package, year: int) -> str:
    volume = VOLUMES[year]
    id = SOURCES[year]
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher=volume.publisher,
        title=volume.title,
        url=url(year),
        license=LICENSE,
        license_url=None,
        attribution=volume.publisher,
        notes=volume.notes,
    )
    return p.artifact(raw.cached(SOURCE, url(year)), id)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Chamber of Deputies of the Kingdom of Italy, its electoral systems of 1924 to 1934, the parties of 1924, "
        "and the count units of the statistical office's volumes",
    )
    p.external.add(ITALY)
    p.add("body", id=BODY, polity_id=ITALY, kind="lower_house")
    p.name(BODY, "Chamber of Deputies of the Kingdom of Italy", "en", "official")
    p.name(BODY, "Camera dei deputati del Regno d'Italia", "it", "native", False)
    p.add("note", id=key("note", BODY), entity_id=BODY, kind="context", body_md=BODY_NOTE, lang="en")
    p.add("body_size", body_id=BODY, seats=535, valid_from=DAYS[1924], valid_to=DAYS[1929])
    p.add("body_size", body_id=BODY, seats=DESIGNATED, valid_from=DAYS[1929], valid_to=date(1939, 3, 23))
    p.add(
        "electoral_system",
        id=SYSTEMS[1924],
        polity_id=ITALY,
        valid_from=date(1923, 11, 18),
        valid_to=date(1928, 5, 17),
        rules={
            "formula": "hare",
            "bonus": {
                "level": "list",
                "share": {"percent": 25},
                "size": {"kind": "fraction", "fraction": 2 / 3, "rounding": "nearest"},
                "remainder": "others",
            },
        },
        rules_version=1,
    )
    p.name(SYSTEMS[1924], "Acerbo law: national majority premium with proportional minority seats", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEMS[1924]),
        entity_id=SYSTEMS[1924],
        kind="methodology",
        body_md=SYSTEM_1924,
        lang="en",
    )
    p.add(
        "electoral_system",
        id=SYSTEMS[1929],
        polity_id=ITALY,
        valid_from=date(1928, 5, 17),
        valid_to=date(1939, 3, 23),
        rules={"formula": "referendum", "majority": {"share": 0.5, "strict": True}},
        rules_version=1,
    )
    p.name(SYSTEMS[1929], "Approval of the national list of designated deputies", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEMS[1929]),
        entity_id=SYSTEMS[1929],
        kind="methodology",
        body_md=SYSTEM_PLEBISCITE,
        lang="en",
    )
    for code, (native, english) in PARTIES.items():
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=ITALY)
        p.name(id, english, "en", "official")
        p.name(id, native, "it", "native", False)
    p.add("scheme", code=PROVINCE, polity_id=ITALY, kind="administrative", rank=20, noun="province")
    p.add("scheme", code=CIRCOSCRIZIONE, polity_id=ITALY, kind="electoral_district", rank=10, noun="circoscrizione")
    return p


def election(p: Package, year: int) -> tuple[str, str]:
    day = DAYS[year]
    id = key("election", year)
    p.add("election", id=id, polity_id=ITALY, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Italian general election", "en", "official")
    p.name(id, f"Elezioni generali politiche del {year} (legislatura {LEGISLATURES[year]})", "it", "native", False)
    path = f"it/{day}-general"
    p.add("slug", path=path, entity_id=id, is_current=True)
    return id, path


def locate(page_id: str, result_set: str, unit: str, option: str | None, measure: int, box: Box) -> dict[str, object]:
    x0, y0, x1, y1 = box
    return {
        "result_set_id": result_set,
        "unit_id": unit,
        "option_id": option,
        "channel": 0,
        "measure": measure,
        "count_round": 0,
        "page_id": page_id,
        "source_id": None,
        "x0": round(x0, 4),
        "y0": round(y0, 4),
        "x1": round(x1, 4),
        "y1": round(y1, 4),
    }


def page_row(p: Package, artifact: str, year: int, page: int) -> str:
    id = key("page", year, page)
    image_key, width, height = image(year, page)
    p.add("source_page", id=id, artifact_id=artifact, page_number=page, image_key=image_key, width=width, height=height)
    return id


def stage(p: Package, values: list[dict[str, object]], locators: list[dict[str, object]]) -> None:
    schema = {
        "result_set_id": pl.String,
        "unit_id": pl.String,
        "option_id": pl.String,
        "channel": pl.Int16,
        "measure": pl.Int16,
        "count_round": pl.Int16,
    }
    p.frame("result_value", pl.DataFrame(values, schema={**schema, "flags": pl.Int16, "value": pl.Int64}))
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                **schema,
                "page_id": pl.String,
                "source_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
        ),
    )


def value(result_set: str, unit: str, option: str | None, measure: int, amount: int) -> dict[str, object]:
    return {
        "result_set_id": result_set,
        "unit_id": unit,
        "option_id": option,
        "channel": 0,
        "measure": measure,
        "count_round": 0,
        "flags": DIGITIZED,
        "value": amount,
    }


def plebiscite(year: int) -> Package:
    day = DAYS[year]
    frame = plebiscites().filter((pl.col("year") == year) & pl.col("provincia").is_not_null())
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} ballot on the national list of designated deputies, by province, from the statistical office's "
        "volume",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(ITALY)
    artifact = source(p, year)
    election_id, path = election(p, year)
    contest = key("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=BODY,
        magnitude=DESIGNATED,
        round_number=1,
        electoral_system_id=SYSTEMS[year],
        default_result_kind="counted",
    )
    p.name(contest, "Chamber of Deputies: national list of designated deputies", "en", "official")
    p.name(contest, "Camera dei deputati: lista dei deputati designati", "it", "native", False)
    p.add("slug", path=f"{path}/chamber", entity_id=contest, is_current=True)
    p.add(
        "note",
        id=key("note", contest, "question"),
        entity_id=contest,
        kind="context",
        body_md=f"The ballot asked: «{QUESTION}» ({QUESTION_EN})",
        lang="en",
    )
    result_set = key("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=SOURCES[year],
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=PLEBISCITE_NOTE, lang="en"
    )
    options = {}
    for position, (code, label, english, outcome) in enumerate(
        (("si", "Sì", "Yes", "won"), ("no", "No", "No", "lost")), 1
    ):
        options[code] = key("option", year, code)
        p.add("contest_option", id=options[code], contest_id=contest, kind="answer", ballot_position=position)
        p.name(options[code], label, "it", "official")
        p.name(options[code], english, "en", "english", False)
        p.add("outcome", result_set_id=result_set, option_id=options[code], kind=outcome)
    registered, voters, approving, rejecting, null = NATIONAL[year]
    for option, measure, amount in (
        (options["si"], VOTES, approving),
        (options["no"], VOTES, rejecting),
        (None, REGISTERED, registered),
        (None, CAST, voters),
        (None, INVALID, null),
    ):
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=amount)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    columns = (
        (None, REGISTERED, "iscritti"),
        (None, CAST, "votanti"),
        (options["si"], VOTES, "si"),
        (options["no"], VOTES, "no"),
        (None, INVALID, "nulli"),
    )
    all_rows = plebiscites().filter((pl.col("year") == year) & pl.col("compartimento").is_not_null())
    for (page,), group in all_rows.group_by("page", maintain_order=True):
        page_id = page_row(p, artifact, year, page)
        found = words(year, page)
        labels = [row["provincia"] or row["compartimento"] for row in group.iter_rows(named=True)]
        rows = bands(found, labels, 0.3)
        placed = [
            place(found, band, [row[c] for _, _, c in columns], 0.2)
            for band, row in zip(rows, group.iter_rows(named=True), strict=True)
        ]
        boxes = columnar(placed, rows, PLEBISCITE_COLUMNS[year])
        for row, row_boxes in zip(group.iter_rows(named=True), boxes, strict=True):
            if row["provincia"] is None:
                continue
            name = row["provincia"]
            unit = key("unit", year, name)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ITALY,
                scheme_code=PROVINCE,
                code=f"{year}:{changeset.slug(name)}",
                area_relation="unlocated",
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            p.name(unit, name, "it", "official")
            region = COMPARTMENT_RENAMES.get(row["compartimento"], row["compartimento"])
            p.add(
                "note",
                id=key("note", unit),
                entity_id=unit,
                kind="context",
                body_md=f"Province of {name}, in the compartimento of {region}, as the volume prints it.",
                lang="en",
            )
            for (option, measure, column), box in zip(columns, row_boxes, strict=True):
                values.append(value(result_set, unit, option, measure, row[column]))
                locators.append(locate(page_id, result_set, unit, option, measure, box))
    if len(values) != frame.height * len(columns):
        raise ValueError(f"{year}: {len(values)} values for {frame.height} provinces")
    stage(p, values, locators)
    return p


def chamber_1924() -> Package:
    year, day = 1924, DAYS[1924]
    table = lists_1924()
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The 1924 Chamber of Deputies election: list votes and seats by circoscrizione, from the statistical "
        "office's volume",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(ITALY)
    artifact = source(p, year)
    election_id, path = election(p, year)
    matrix_page = page_row(p, artifact, year, 28)
    totals_page = page_row(p, artifact, year, 132)
    matrix = words(year, 28)
    summary = words(year, 132)
    summary_rows = bands(summary, list(CIRCOSCRIZIONI), 0.25)
    matrix_boxes = matrix_1924(matrix, table)
    summary_boxes = columnar(
        [
            place(summary, band, list(ACCOUNTING_1924[name][:4]), 0.2)
            for band, name in zip(summary_rows, CIRCOSCRIZIONI, strict=True)
        ],
        summary_rows,
        ((0.268, 0.318), (0.53, 0.58), (0.62, 0.67), (0.72, 0.77)),
    )
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    for column, name in enumerate(CIRCOSCRIZIONI):
        registered, voters, invalid, valid, seats = ACCOUNTING_1924[name]
        contest = key("contest", year, name)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEMS[year],
            default_result_kind="counted",
        )
        p.name(contest, f"Chamber of Deputies: {name}", "en", "official")
        p.name(contest, f"Camera dei deputati: circoscrizione {name}", "it", "native", False)
        p.add("slug", path=f"{path}/chamber/{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCES[year],
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=NOTE_1924, lang="en"
        )
        for caveat, where in ((CALABRIA, "Calabrie e Basilicata"), (PUGLIE, "Puglie")):
            if name == where:
                p.add(
                    "note",
                    id=key("note", result_set, "caveat"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=caveat,
                    lang="en",
                )
        unit = key("unit", year, name)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ITALY,
            scheme_code=CIRCOSCRIZIONE,
            code=f"{year}:{changeset.slug(name)}",
            area_relation="unlocated",
            kind="standard",
            valid_from=day,
            valid_to=day + timedelta(days=1),
        )
        p.name(unit, name, "it", "official")
        for (measure, amount), box in zip(
            ((REGISTERED, registered), (CAST, voters), (INVALID, invalid), (VALID, valid)),
            summary_boxes[column],
            strict=True,
        ):
            values.append(value(result_set, unit, None, measure, amount))
            locators.append(locate(totals_page, result_set, unit, None, measure, box))
        won = SEATS_1924[name]
        running = [(number, values_[column]) for number, values_ in sorted(table.items()) if values_[column] > 0]
        for number, amount in running:
            box = matrix_boxes[(number, column)]
            symbol, grouping = LISTS[number]
            option = key("option", year, name, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(grouping),
                ballot_position=number,
                ballot_label=f"Lista n. {number} ({symbol})",
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if number in won else "lost",
                seats=won.get(number),
            )
            values.append(value(result_set, unit, option, VOTES, amount))
            locators.append(locate(matrix_page, result_set, unit, option, VOTES, box))
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=amount)
        for measure, amount in ((REGISTERED, registered), (CAST, voters), (INVALID, invalid), (VALID, valid)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount)
    stage(p, values, locators)
    return p


def column_numbers(found: list[Word], center: float) -> list[tuple[float, float, float, float, str]]:
    """The numbers printed in one column of the 1924 table, top to bottom, with the pieces of each joined."""
    half = 0.022
    pieces = sorted(
        (w for w in found if abs((w[0] + w[2]) / 2 - center) < half and digits(w[4]) and 0.18 < w[1] < 0.97),
        key=lambda w: (w[1], w[0]),
    )
    out: list[tuple[float, float, float, float, str]] = []
    for word in pieces:
        if out and abs(word[1] - out[-1][1]) < 0.006:
            x0, y0, x1, y1, text = out[-1]
            out[-1] = (min(x0, word[0]), min(y0, word[1]), max(x1, word[2]), max(y1, word[3]), text + word[4])
        else:
            out.append(word)
    return out


def matrix_1924(found: list[Word], table: dict[int, list[int]]) -> dict[tuple[int, int], Box]:
    """Box each list's votes in each circoscrizione on the two-page table.

    Each column's printed numbers are matched to the column's values in list order. A number the text layer misread
    takes its row's height from the same list's numbers in the other columns.
    """
    placed: dict[tuple[int, int], Box | None] = {}
    for column, center in enumerate(CENTERS_1924[: len(CIRCOSCRIZIONI)]):
        running = [number for number, values in sorted(table.items()) if values[column] > 0]
        hits = sequence(column_numbers(found, center), [table[number][column] for number in running])
        placed |= {(number, column): hit for number, hit in zip(running, hits, strict=True)}
    boxes: dict[tuple[int, int], Box] = {}
    for (number, column), box in placed.items():
        if box is None:
            heights = sorted((b[1], b[3]) for (n, _), b in placed.items() if n == number and b is not None)
            if not heights:
                raise ValueError(f"1924 list {number}: no printed number to place its row")
            top, bottom = heights[len(heights) // 2]
            center = CENTERS_1924[column]
            box = (center - 0.022, top, center + 0.022, bottom)
        boxes[(number, column)] = box
    return boxes


def sequence(stack: list[tuple[float, float, float, float, str]], wanted: list[int]) -> list[Box | None]:
    """Box each wanted number by the next printed number that spells it, keeping the column's order."""
    boxes: list[Box | None] = []
    position = 0
    for amount in wanted:
        hit = next((i for i in range(position, len(stack)) if stack[i][4] == str(amount)), None)
        if hit is None:
            boxes.append(None)
            continue
        x0, y0, x1, y1, _ = stack[hit]
        boxes.append((x0, y0, x1, y1))
        position = hit + 1
    return boxes
