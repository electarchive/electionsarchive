"""Finland's national consultative referendums: the prohibition vote of 29 and 30 December 1931 and the vote on
membership of the European Union of 16 October 1994.

Both come from Statistics Finland's official statistics, digitized from the scanned volumes on its publication service:
Vaalit 1995:2 for 1994 and Suomen virallinen tilasto XXIX C 1 for 1931. The 1994 national result is also the one the
Ministry of Justice's notice 921/1994 prints.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.fi import digitized, prohibition_1931, vaalit
from ea.sources.fi.digitized import Box

SOURCE = "fi.referendums"
REGISTRY = f"{SOURCE}/registry"
OTOS = "https://otos.stat.fi/server/api/core/bitstreams"
PDF_1994 = f"{OTOS}/0bf5fe38-f835-47c2-a1cb-f6cd492ac14c/content"
PDF_1931 = f"{OTOS}/d55cd74d-a261-447a-ac6a-7055457b0187/content"
HANDLE_1994 = "https://otos.stat.fi/handle/123456789/17140"
HANDLE_1931 = "https://otos.stat.fi/handle/123456789/17146"
FINLEX_1994 = "https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/statute/1994/921/fin@"
FINLEX_PAGE_1994 = "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/1994/921"
CLASSIFICATION = "https://data.stat.fi/api/classifications/v2/classifications/kunta_1_19940101/classificationItems"
CLASS_FI = f"{CLASSIFICATION}?content=data&meta=min&lang=fi"
CLASS_SV = f"{CLASSIFICATION}?content=data&meta=min&lang=sv"

VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
DAY_1994 = date(1994, 10, 16)
DAYS_1931 = (date(1931, 12, 29), date(1931, 12, 30))
SCHEME = "fi.kuntalaskenta"
BOTH = 2  # the third figure of a printed triple of men, women, and both


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def _valid_json(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file):
            raise ValueError(f"{path} is empty")


def fetch() -> None:
    raw.fetch(SOURCE, PDF_1994, suffix=".pdf")
    raw.fetch(SOURCE, PDF_1931, suffix=".pdf")
    raw.fetch(SOURCE, FINLEX_1994, suffix=".xml")
    raw.fetch(SOURCE, CLASS_FI, suffix=".json", check=_valid_json)
    raw.fetch(SOURCE, CLASS_SV, suffix=".json", check=_valid_json)


# ---------------------------------------------------------------------------------------------------------------------
# 1994: the tables of Vaalit 1995:2

# Table 2 of the volume, "Hyväksytyt ja hylätyt äänestysliput ... kunnittain", on printed pages 69 to 88. Every row has
# eleven figures, in the columns below, and four sums that the row must satisfy.
FIRST_PAGE, LAST_PAGE = 69, 88
COLUMNS = (193, 229, 262, 298, 330, 363, 399, 435, 465, 501, 537)  # right edges, in points
V_VALID, V_YES, V_NO, V_ADV_YES, V_ADV_NO, V_DAY_YES, V_DAY_NO, V_REJECTED, V_ADV_REJECTED, V_ADVANCE, V_ENTITLED = (
    range(11)
)
SUMS = (
    (V_VALID, (V_YES, V_NO)),
    (V_YES, (V_ADV_YES, V_DAY_YES)),
    (V_NO, (V_ADV_NO, V_DAY_NO)),
    (V_ADVANCE, (V_ADV_YES, V_ADV_NO, V_ADV_REJECTED)),
)
USED = (V_VALID, V_YES, V_NO, V_REJECTED, V_ENTITLED)
# Figures that the recognition lost or garbled and a row's sums cannot settle, read from the page image
FIGURES_1994 = {
    "mietoinen": {V_REJECTED: 7},
    "ypäjä": {V_REJECTED: 5},
    "miehikkälä": {V_ENTITLED: 2195},
    "askola": {V_ENTITLED: 3138},
    "sysmä": {V_ENTITLED: 4312},
    "pedersöre": {V_ENTITLED: 6880},
    "ylistaro": {V_ENTITLED: 4487},
    "tyrnävä": {V_ENTITLED: 2628},
    "savonlinna": {V_REJECTED: 51},
    "hämesouth": {V_REJECTED: 889},
    "brändö": {V_REJECTED: 5},
    "pirkkala": {V_YES: 4128, V_ADVANCE: 2833},
    "inari": {V_ADVANCE: 2168},
}
# Names as the recognition spelled them, for the municipalities whose printed name it garbled
SPELLINGS_1994 = {
    "li": "139",
    "ylili": "972",
    "hanunarland": "076",
    "haisua": "074",
    "liijendahl": "424",
    "uilava": "885",
}
PREFIXES_1994 = {"loimaank": "431", "mikkmlk": "492", "ylitomio": "976"}
CONSTITUENCIES_1994 = (
    ("helsin", "HEL", "Helsinki"),
    ("uusimaa|uudenmaan|nylands", "UUS", "Uusimaa"),
    ("turunlääninetel|åbolänssödra|turkusouth", "VAR", "Southern Turku"),
    ("turunlääninpohj|åbolänsnorra|turkunorth", "SAT", "Northern Turku"),
    ("åland|ahvenanmaa|landsk", "AHV", "Åland"),
    ("hämeenlääninetel|tavastehus1.södra|hämesouth", "HÄM", "Southern Häme"),
    ("hämeenlääninpohj|tavastehuslänsn|hämenorth", "PIR", "Northern Häme"),
    ("kymi|kymen|kymmene", "KYM", "Kymi"),
    ("mikkelin|s:tmichels", "ESA", "Mikkeli"),
    ("pohjoiskarjalan|norrakarelens|pohjoiskarjala", "KAR", "North Karelia"),
    ("kuopion|kuopio", "PSA", "Kuopio"),
    ("keskisuomen|mellersta|keskisuomi", "KES", "Central Finland"),
    ("vaasan|vasa", "VAA", "Vaasa"),
    ("oulun|uleåborgs", "OUL", "Oulu"),
    ("lapin|lapplands|lapland", "LAP", "Lapland"),
)


@dataclass
class Row:
    """One row of a table with the box of each figure."""

    page: int
    name: str
    values: list[int | None]
    boxes: list[Box]
    fixed: list[int] = field(default_factory=list)
    turnout: float | None = None  # the turnout the row below it prints, in percent


def column_of(edge: float) -> int | None:
    best = min(range(len(COLUMNS)), key=lambda index: abs(COLUMNS[index] - edge))
    return best if abs(COLUMNS[best] - edge) < 14 else None


def settle(values: list[int | None]) -> tuple[list[int | None], list[int]]:
    """Solve the cells the recognition lost from the row's sums, or find the one or two cells whose correction makes
    every sum hold. A figure counts as read right when no correction is needed."""

    def holds(row: list[int | None]) -> bool:
        return all(value is not None and value >= 0 for value in row) and all(
            row[total] == sum(row[part] or 0 for part in parts) for total, parts in SUMS
        )

    row = list(values)
    for _ in range(4):
        for total, parts in SUMS:
            unknown = [index for index in (total, *parts) if row[index] is None]
            if len(unknown) == 1:
                (at,) = unknown
                if at == total:
                    row[at] = sum(row[part] or 0 for part in parts)
                else:
                    row[at] = (row[total] or 0) - sum(row[part] or 0 for part in parts if part != at)
    if holds(row):
        return row, [index for index in range(11) if row[index] != values[index]]
    candidates: list[list[int | None]] = []
    for at in range(11):
        for total, parts in SUMS:
            if at not in (total, *parts):
                continue
            trial = list(row)
            if at == total:
                trial[at] = sum(row[part] or 0 for part in parts)
            else:
                trial[at] = (row[total] or 0) - sum(row[part] or 0 for part in parts if part != at)
            if holds(trial) and trial not in candidates:
                candidates.append(trial)
    if len(candidates) == 1:
        return candidates[0], [index for index in range(11) if candidates[0][index] != values[index]]
    return row, []


def read_1994() -> list[Row]:
    path = raw.cached(SOURCE, PDF_1994).path
    found: list[Row] = []
    for number, page in digitized.pages(path, FIRST_PAGE, LAST_PAGE).items():
        for line in digitized.rows(page.words):
            name: list[str] = []
            cells: list[Box | None] = [None] * 11
            for box in digitized.runs(line):
                if box.x1 < 185:
                    name.append(box.text)
                    continue
                column = column_of(box.x1)
                if column is not None:
                    cells[column] = box
            if sum(cell is not None and any(char.isdigit() for char in cell.text) for cell in cells) < 7:
                continue
            if any(cell is not None and re.search(r"\d ?[,.] ?\d", cell.text) for cell in cells):
                last = cells[V_ENTITLED]  # a row of percentages; its last figure is the turnout
                if found and last is not None and re.fullmatch(r"(\d ?)+[,.] ?\d", last.text):
                    found[-1].turnout = float(re.sub(r"\s", "", last.text).replace(",", "."))
                continue
            top = min(word.y0 for word in line)
            bottom = max(word.y1 for word in line)
            boxes = [
                cell if cell is not None else Box("", COLUMNS[index] - 25, top, COLUMNS[index], bottom)
                for index, cell in enumerate(cells)
            ]
            values = [digitized.number(cell.text if cell is not None else None) for cell in cells]
            found.append(Row(number, " ".join(name), values, boxes))
    return found


@dataclass
class Place:
    """A municipality, a constituency, or the voters living abroad of a constituency, with its figures."""

    name: str
    row: Row


@dataclass
class Count1994:
    national: Row
    constituencies: dict[str, Row]
    abroad: dict[str, Row]
    municipalities: dict[str, Row]
    member: dict[str, str]
    subtotals: list[tuple[Row, list[str]]]
    notes: list[str]


ORDER_1994 = ("HEL", "UUS", "VAR", "SAT", "HÄM", "PIR", "KYM", "ESA", "KAR", "PSA", "KES", "VAA", "OUL", "LAP", "AHV")


@functools.cache
def municipalities_1994() -> dict[str, tuple[str, str]]:
    """Statistics Finland's classification of the municipalities of 1994: code to (Finnish name, Swedish name)."""
    out: dict[str, tuple[str, str]] = {}
    for lang in (CLASS_FI, CLASS_SV):
        with raw.cached(SOURCE, lang).path.open("rb") as file:
            for item in json.load(file):
                code = item["code"]
                name = item["classificationItemNames"][0]["name"]
                out[code] = (name, name) if code not in out else (out[code][0], name)
    return out


def _resolve(row: Row, names: dict[str, tuple[str, str]], taken: set[str]) -> str | None:
    text = digitized.letters(row.name)
    if text in SPELLINGS_1994:
        return SPELLINGS_1994[text]
    for prefix, code in PREFIXES_1994.items():
        if text.startswith(prefix):
            return code
    best: tuple[int, str] | None = None
    for code, (finnish, swedish) in names.items():
        for name in (finnish, swedish):
            norm = digitized.letters(name)
            if norm and text.startswith(norm) and (best is None or len(norm) > best[0]):
                best = (len(norm), code)
    return best[1] if best else None


@functools.cache
def held_1994() -> set[str]:
    """The 1994 municipality codes that `fi.vaalit` holds as areas: the municipalities of the elections from 2003."""
    return set(vaalit.places()["fi.kunta"]) & set(municipalities_1994())


@functools.cache
def count_1994() -> Count1994:
    rows = read_1994()
    names = municipalities_1994()
    notes: list[str] = []
    checked: list[Row] = []
    for row in rows:
        letters = digitized.letters(row.name)
        for name, figures in FIGURES_1994.items():
            if letters.startswith(name):
                for index, value in figures.items():
                    row.values[index] = value
        found, changed = settle(row.values)
        row.values, row.fixed = found, changed
        checked.append(row)
    national = checked[0]
    constituencies: dict[str, Row] = {}
    abroad: dict[str, Row] = {}
    municipalities: dict[str, Row] = {}
    member: dict[str, str] = {}
    subtotals: list[tuple[Row, list[str]]] = []
    current = -1
    for index, row in enumerate(checked):
        letters = digitized.letters(row.name)
        following = digitized.letters(checked[index + 1].name) if index + 1 < len(checked) else ""
        suomessa = "citiz" in following and "abro" not in following and "sweden" not in following
        if index == 0:
            continue
        if suomessa and "citiz" not in letters and "municip" not in letters:
            current += 1
            constituencies[ORDER_1994[current]] = row
            continue
        if "abro" in letters:
            abroad[ORDER_1994[current]] = row
            continue
        if "municip" in letters:
            subtotals.append((row, []))
            continue
        if any(mark in letters for mark in ("citiz", "sweden")):
            continue
        code = _resolve(row, names, set())
        if code is None:
            notes.append(f"unmatched {row.page} {row.name!r}")
            continue
        if code in municipalities:
            notes.append(f"duplicate {code} {row.page} {row.name!r}")
            continue
        municipalities[code] = row
        member[code] = ORDER_1994[current]
        subtotals[-1][1].append(code)
    return Count1994(national, constituencies, abroad, municipalities, member, subtotals, notes)


# ---------------------------------------------------------------------------------------------------------------------
# 1931: the summary tables of SVT XXIX C 1, read from pages 7, 8, and 11 of the volume

PROVINCES_1931 = (
    ("uusimaa", "Uudenmaan lääni", "Nylands län", "Uusimaa"),
    ("turku-pori", "Turun-Porin lääni", "Åbo-Björneborgs län", "Turku and Pori"),
    ("aland", "Ahvenanmaa", "Åland", "Åland"),
    ("hame", "Hämeen lääni", "Tavastehus län", "Häme"),
    ("viipuri", "Viipurin lääni", "Viborgs län", "Viipuri"),
    ("mikkeli", "Mikkelin lääni", "S:t Michels län", "Mikkeli"),
    ("kuopio", "Kuopion lääni", "Kuopio län", "Kuopio"),
    ("vaasa", "Vaasan lääni", "Vasa län", "Vaasa"),
    ("oulu", "Oulun lääni", "Uleåborgs län", "Oulu"),
)
# Men, women, and both together, for each province in the order above, then for the whole country
REGISTERED_1931 = {  # page 7
    "urban": [
        (57583, 86396, 143979),
        (19070, 28989, 48059),
        (420, 586, 1006),
        (14962, 23428, 38390),
        (18721, 25717, 44438),
        (3593, 4907, 8500),
        (6561, 9023, 15584),
        (9832, 14728, 24560),
        (10261, 12867, 23128),
    ],
    "rural": [
        (60607, 71875, 132482),
        (97391, 114047, 211438),
        (5649, 6630, 12279),
        (74840, 83460, 158300),
        (121257, 131946, 253203),
        (46120, 47175, 93295),
        (79101, 80076, 159177),
        (115259, 123731, 238990),
        (74302, 71205, 145507),
    ],
}
REGISTERED_COUNTRY_1931 = {"urban": (141003, 206641, 347644), "rural": (674526, 730145, 1404671)}
VOTED_1931 = {  # page 8
    "urban": [
        (36762, 48349, 85111),
        (11252, 13522, 24774),
        (288, 393, 681),
        (9128, 10623, 19751),
        (11483, 12278, 23761),
        (2065, 2043, 4108),
        (3706, 3360, 7066),
        (5918, 6774, 12692),
        (5636, 5244, 10880),
    ],
    "rural": [
        (35542, 32263, 67805),
        (47613, 33840, 81453),
        (2882, 3031, 5913),
        (38029, 27724, 65753),
        (70578, 50817, 121395),
        (23678, 14188, 37866),
        (40243, 23973, 64216),
        (51844, 39966, 91810),
        (32378, 20472, 52850),
    ],
}
VOTED_COUNTRY_1931 = {"urban": (86238, 102586, 188824), "rural": (342787, 246274, 589061)}
# For each province: the votes for the first, second, and third alternative, as (men, women, both), and the valid ballots
VOTES_1931 = {  # page 11
    "urban": [
        ((5735, 8221, 13956), (271, 425, 696), (30658, 39544, 70202), (36664, 48190, 84854)),
        ((1984, 2783, 4767), (80, 126, 206), (9152, 10568, 19720), (11216, 13477, 24693)),
        ((9, 7, 16), (0, 3, 3), (279, 383, 662), (288, 393, 681)),
        ((2409, 4268, 6677), (91, 136, 227), (6612, 6195, 12807), (9112, 10599, 19711)),
        ((2310, 3117, 5427), (97, 132, 229), (9042, 8979, 18021), (11449, 12228, 23677)),
        ((360, 489, 849), (20, 33, 53), (1680, 1515, 3195), (2060, 2037, 4097)),
        ((727, 1001, 1728), (48, 74, 122), (2916, 2266, 5182), (3691, 3341, 7032)),
        ((1145, 1744, 2889), (43, 76, 119), (4720, 4930, 9650), (5908, 6750, 12658)),
        ((1811, 2170, 3981), (56, 82, 138), (3755, 2967, 6722), (5622, 5219, 10841)),
    ],
    "rural": [
        ((6509, 7507, 14016), (436, 322, 758), (28400, 24292, 52692), (35345, 32121, 67466)),
        ((10198, 10776, 20974), (775, 468, 1243), (36414, 22457, 58871), (47387, 33701, 81088)),
        ((30, 36, 66), (12, 9, 21), (2830, 2980, 5810), (2872, 3025, 5897)),
        ((10914, 11817, 22731), (726, 522, 1248), (26205, 15307, 41512), (37845, 27646, 65491)),
        ((15209, 17698, 32907), (966, 807, 1773), (54045, 32062, 86107), (70220, 50567, 120787)),
        ((4885, 5170, 10055), (469, 318, 787), (18149, 8596, 26745), (23503, 14084, 37587)),
        ((7468, 7410, 14878), (632, 499, 1131), (31887, 15908, 47795), (39987, 23817, 63804)),
        ((19596, 21604, 41200), (792, 527, 1319), (31247, 17718, 48965), (51635, 39849, 91484)),
        ((10203, 9849, 20052), (519, 355, 874), (21464, 10171, 31635), (32186, 20375, 52561)),
    ],
}
VOTES_COUNTRY_1931 = {
    "urban": ((16490, 23800, 40290), (706, 1087, 1793), (68814, 77347, 146161), (86010, 102234, 188244)),
    "rural": ((85012, 91867, 176879), (5327, 3827, 9154), (250641, 149491, 400132), (340980, 245185, 586165)),
    "whole": ((101502, 115667, 217169), (6033, 4914, 10947), (319455, 226838, 546293), (426990, 347419, 774409)),
}
REGISTERED_WHOLE_1931 = (815529, 936786, 1752315)
VOTED_WHOLE_1931 = (429025, 348860, 777885)
REJECTED_WHOLE_1931 = (2035, 1441, 3476)


# ---------------------------------------------------------------------------------------------------------------------
# Packages

CONSTITUENCY_SCHEME_NEW = "fi.kansanaanestys_vaalipiiri"
PROVINCE_SCHEME = "fi.kansanaanestys_laani"
CONSTITUENCY_NAMES = {code: english for _, code, english in CONSTITUENCIES_1994}
SOURCE_1994 = key("source", "vaalit-1995-2")
SOURCE_FINLEX = key("source", "finlex-921-1994")
SOURCE_CLASSIFICATION = key("source", "classification-1994")
SOURCE_1931 = key("source", "svt-29c-1")
SYSTEM = key("electoral_system", "advisory-referendum")

METHOD_1994 = (
    "Statistics Finland's table of valid and rejected ballots by municipality (Table 2 of Vaalit 1995:2), read from "
    "the printed page. It gives each municipality's valid ballots, votes for Yes and No, rejected ballots, and persons "
    "entitled to vote, and the same figures for the voters living abroad of each constituency, which the volume "
    "counts apart from the municipalities. The advisory referendum used the 15 constituencies of the parliamentary "
    "elections. Each figure was read from the text layer of the scan, which sometimes misreads a digit; the volume's "
    "own sums (valid ballots are Yes plus No, and Yes and No are the advance votes plus the election-day votes) "
    "checked every row: 24 rows had a misread digit that the sums corrected, and 14 figures that the sums could not "
    "settle were read from the page image or from the printed subtotals. The "
    "municipalities add up to the constituencies' totals, and the constituencies to the country's. The "
    "municipalities that still exist under the same code are matched to their areas, and the constituencies to "
    "the archive's constituencies; the municipalities merged away since 1994 have no area. The country's "
    "Yes, No, and valid votes are those of the Ministry of Justice's notice 921/1994. The persons entitled to vote "
    "and the persons who voted are the volume's own totals. Cast ballots are the valid and rejected ballots together."
)
CONSTITUENCY_METHOD_1994 = (
    "The same table's rows for the 15 constituencies, which add the voters living abroad to the municipalities of the "
    "constituency."
)
CAVEAT_1994 = (
    "The referendum was advisory. Parliament approved the accession treaty on 18 November 1994, in line with the "
    "result."
)
QUESTION_1994 = (
    "Tuleeko Suomen liittyä Euroopan unionin jäseneksi neuvotellun sopimuksen mukaisesti? (Should Finland join the "
    "European Union in accordance with the negotiated treaty?)"
)
METHOD_1931 = (
    "Statistics Finland's predecessor, the Tilastollinen päätoimisto, published the results in Suomen virallinen "
    "tilasto XXIX C 1. The volume prints the results for every municipality, men and women apart, because the two "
    "sexes voted on ballots of different colors. The archive stores the finer table that was quick to check: the nine "
    "provinces (läänit), each split into its towns (kaupungit) and rural municipalities (maalaiskunnat), read from the "
    "summary tables on pages 7, 8, and 11 of the volume. The figures for men and women are not stored, only the two "
    "sexes together. Rejected ballots are the ballots cast less the valid ones; the volume prints them for the whole "
    "country only. The country's totals are those the volume prints in its text."
)
MUNICIPAL_METHOD_1931 = (
    "The volume's table by municipality, on pages 15 to 26: each of the 38 towns and the rural municipalities of the "
    "nine provinces, with the persons entitled to vote, the persons who voted, and the valid ballots and the votes "
    "for each alternative, men and women apart and together. Only the two sexes together are stored. Rejected "
    "ballots are the ballots cast less the valid ones. Each figure was read from the text layer of the scan, whose "
    "columns were located on each page. Every row satisfies the volume's own sums (both sexes are men plus women, "
    "the ballots cast are the valid and rejected ballots, and the valid ballots are the votes for the three "
    "alternatives), and a misread figure that the sums settled was corrected; the figures of five rows that the text "
    "layer lost were read from the page image and have no box. The towns and the rural municipalities of each "
    "province add up to the subtotals of the summary tables, and the country's total to the volume's own. The "
    "municipalities are those of 1931, many of them ceded to the Soviet Union in 1944, and are not yet matched to "
    "the archive's areas. Names are as printed, with the Finnish and Swedish forms together where the volume gives "
    "both."
)
PROVINCE_METHOD_1931 = (
    "The same tables' rows for each province as a whole, the towns and the rural municipalities together."
)
QUESTION_1931 = (
    "The voter marked one of three alternatives: I, the prohibition law is kept on the basis of total prohibition; II, "
    "the prohibition law is amended to allow the regulated and taxed manufacture and sale of mild alcoholic beverages; "
    "III, the prohibition law is repealed and a law passed that allows the regulated and taxed manufacture and sale "
    "of other alcoholic beverages as well."
)


def packages() -> list[str]:
    return [REGISTRY, f"{SOURCE}/{DAY_1994}", f"{SOURCE}/{DAYS_1931[0]}"]


def parse() -> None:
    registry().write()
    election_1994().write()
    election_1931().write()


def emit() -> None:
    package.run("load", packages())


def cited() -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in (PDF_1994, PDF_1931, FINLEX_1994, CLASS_FI, CLASS_SV)]


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Finland's national referendums of 1931 and 1994: the publications they come from, the electoral system, and the "
        "schemes of their reporting units",
    )
    p.external.add(vaalit.FINLAND)
    p.add(
        "source",
        id=SOURCE_1994,
        kind="official_body",
        publisher="Tilastokeskus",
        title="EU-kansanäänestys 1994 (Suomen virallinen tilasto, Vaalit 1995:2)",
        url=HANDLE_1994,
        license="Quotable with attribution: official statistics of Statistics Finland",
        attribution="Tilastokeskus, Vaalit 1995:2",
    )
    p.artifact(raw.cached(SOURCE, PDF_1994), SOURCE_1994)
    p.add(
        "source",
        id=SOURCE_FINLEX,
        kind="official_body",
        publisher="Oikeusministeriö",
        title="Ilmoitus Suomen liittymisestä Euroopan unionin jäseneksi 16 päivänä lokakuuta 1994 toimitetun "
        "neuvoa-antavan kansanäänestyksen tuloksesta (921/1994)",
        url=FINLEX_PAGE_1994,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Finlex, Suomen säädöskokoelma 921/1994",
    )
    p.artifact(raw.cached(SOURCE, FINLEX_1994), SOURCE_FINLEX)
    p.add(
        "source",
        id=SOURCE_CLASSIFICATION,
        kind="official_body",
        publisher="Tilastokeskus",
        title="Kunnat 1994 (Statistics Finland's classification of municipalities, kunta_1_19940101)",
        url=CLASSIFICATION,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Tilastokeskus, luokituspalvelu",
    )
    for url in (CLASS_FI, CLASS_SV):
        p.artifact(raw.cached(SOURCE, url), SOURCE_CLASSIFICATION)
    p.add(
        "source",
        id=SOURCE_1931,
        kind="official_body",
        publisher="Tilastollinen päätoimisto",
        title="Kieltolakiäänestys vuonna 1931 (Suomen virallinen tilasto XXIX C 1)",
        url=HANDLE_1931,
        license="No license stated: official statistics published in 1932; facts published with attribution",
        attribution="Tilastollinen päätoimisto, SVT XXIX C 1",
    )
    p.artifact(raw.cached(SOURCE, PDF_1931), SOURCE_1931)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=vaalit.FINLAND,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="An advisory referendum under a statute that sets no quorum or threshold: the alternative with the "
        "most valid votes carries, and Parliament decides what follows.",
        lang="en",
    )
    for code, noun, rank in (
        (CONSTITUENCY_SCHEME_NEW, "constituency count", 2),
        (PROVINCE_SCHEME, "province count", 1),
    ):
        p.add("scheme", code=code, polity_id=vaalit.FINLAND, kind="precinct", rank=rank, noun=noun, code_system=None)
    return p


@dataclass
class Build:
    """The rows every referendum of this module shares."""

    p: Package
    day: date
    election: str
    contest: str
    options: dict[str, str]
    values: list[tuple[str, str, str | None, int, int]] = field(default_factory=list)
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = field(default_factory=list)


def _start(
    day: date, last: date, summary: str, names: tuple[str, str, str], titles: tuple[str, str, str], slug: str
) -> Build:
    p = Package(f"{SOURCE}/{day}", SOURCE, summary, depends=[REGISTRY])
    p.external.update({vaalit.FINLAND, SCHEME})
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=vaalit.FINLAND,
        kind="referendum",
        date_from=day,
        date_to=last,
        date_precision="day",
        status="held",
    )
    for text, lang, kind in zip(names, ("en", "fi", "sv"), ("official", "native", "native"), strict=True):
        p.name(election, text, lang, kind, lang == "en")
    p.add("slug", path=slug, entity_id=election, is_current=True)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    for text, lang, kind in zip(titles, ("en", "fi", "sv"), ("official", "native", "native"), strict=True):
        p.name(contest, text, lang, kind, lang == "en")
    p.add("slug", path=f"{slug}/question", entity_id=contest, is_current=True)
    return Build(p, day, election, contest, {})


def _option(b: Build, name: str, position: int, label: str, names: tuple[str, str, str]) -> None:
    option = b.options[name] = key("option", b.day, name)
    b.p.add(
        "contest_option",
        id=option,
        contest_id=b.contest,
        kind="answer",
        ballot_position=position,
        ballot_label=label,
    )
    for text, lang, kind in zip(names, ("en", "fi", "sv"), ("official", "native", "native"), strict=True):
        b.p.name(option, text, lang, kind, lang == "en")


def _result_set(
    b: Build, name: str, default: bool, completeness: float, method: str, source: str, supersedes: str | None = None
) -> str:
    result_set = key("result_set", b.day, name)
    b.p.add(
        "result_set",
        id=result_set,
        election_id=b.election,
        contest_id=b.contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=default,
        completeness=completeness,
        supersedes_id=supersedes,
    )
    b.p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


def _write_values(b: Build, result_set: str, derived: tuple[int, ...] = ()) -> None:
    import polars as pl

    frame = pl.DataFrame(
        b.values, schema=["set", "unit_id", "option_id", "measure", "value"], orient="row", infer_schema_length=None
    ).filter(pl.col("set") == result_set)
    b.p.frame(
        "result_value",
        frame.drop("set")
        .with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        )
        .with_columns(
            flags=pl.when(pl.col("measure").is_in(derived))
            .then(pl.lit(0, pl.Int16))
            .otherwise(pl.lit(DIGITIZED, pl.Int16))
        ),
    )


def election_1994() -> Package:
    day = DAY_1994
    count = count_1994()
    assert not count.notes, count.notes
    b = _start(
        day,
        day,
        "The 16 October 1994 referendum on Finland's membership of the European Union by municipality, digitized "
        "from Statistics Finland's volume",
        (
            "1994 Finnish referendum on membership of the European Union",
            "Neuvoa-antava kansanäänestys Suomen liittymisestä Euroopan unionin jäseneksi 1994",
            "Den rådgivande folkomröstningen om Finlands medlemskap i Europeiska unionen 1994",
        ),
        (
            "1994 Finnish referendum: should Finland join the European Union?",
            "Tuleeko Suomen liittyä Euroopan unionin jäseneksi neuvotellun sopimuksen mukaisesti?",
            "Bör Finland bli medlem av Europeiska unionen i enlighet med det avtal som förhandlingarna har lett till?",
        ),
        f"fi/{day}-referendum",
    )
    p = b.p
    p.depends.append(f"{vaalit.SOURCE}/geo")
    held = held_1994()
    p.uses(raw.cached(SOURCE, PDF_1994))
    p.uses(raw.cached(SOURCE, FINLEX_1994))
    p.uses(raw.cached(SOURCE, CLASS_FI))
    p.uses(raw.cached(SOURCE, CLASS_SV))
    p.add(
        "note",
        id=key("note", b.contest, "question"),
        entity_id=b.contest,
        kind="context",
        body_md=QUESTION_1994,
        lang="en",
    )
    _option(b, "yes", 1, "Kyllä", ("Yes", "Kyllä", "Ja"))
    _option(b, "no", 2, "Ei", ("No", "Ei", "Nej"))
    municipal = _result_set(b, "municipality", True, 1.0, METHOD_1994, SOURCE_1994)
    constituency = _result_set(b, "constituency", False, 1.0, CONSTITUENCY_METHOD_1994, SOURCE_1994)
    p.add(
        "note",
        id=key("note", municipal, "advisory"),
        entity_id=municipal,
        kind="caveat",
        body_md=CAVEAT_1994,
        lang="en",
    )
    p.add(
        "citation",
        id=key("citation", municipal, "finlex"),
        entity_id=municipal,
        source_id=SOURCE_FINLEX,
        locator=json.dumps({"section": "Ilmoitus 921/1994, vastausvaihtoehtojen äänimäärät"}),
    )
    path = raw.cached(SOURCE, PDF_1994).path
    sizes = digitized.pages(path, FIRST_PAGE, LAST_PAGE)
    artifact = str(
        uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_1994}\x1f{raw.cached(SOURCE, PDF_1994).storage_key}")
    )
    page_ids: dict[int, str] = {}

    def page_id(number: int) -> str:
        if number not in page_ids:
            page_ids[number] = key("page", number)
            size = sizes[number]
            p.add(
                "source_page",
                id=page_ids[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{raw.cached(SOURCE, PDF_1994).sha256}-{number}.png",
                width=round(size.width * 100 / 72),
                height=round(size.height * 100 / 72),
            )
        return page_ids[number]

    def put(result_set: str, unit: str, row: Row) -> None:
        for measure, option, index in (
            (REGISTERED, None, V_ENTITLED),
            (VALID, None, V_VALID),
            (INVALID, None, V_REJECTED),
            (VOTES, "yes", V_YES),
            (VOTES, "no", V_NO),
        ):
            value = row.values[index]
            assert value is not None
            option_id = b.options[option] if option else None
            b.values.append((result_set, unit, option_id, measure, value))
            b.locators.append(
                (result_set, unit, option_id, measure, page_id(row.page), *digitized.pixels(row.boxes[index]))
            )

    names = municipalities_1994()
    for code, row in sorted(count.municipalities.items()):
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code=SCHEME,
            code=f"{day}:{code}",
            area_relation="equals" if code in held else "unlocated",
            area_id=vaalit.area("fi.kunta", code) if code in held else None,
            kind="standard",
        )
        finnish, swedish = names[code]
        p.name(unit, finnish, "fi", "official")
        if swedish != finnish:
            p.name(unit, swedish, "sv", "official", False)
        put(municipal, unit, row)
    for code, row in sorted(count.abroad.items()):
        unit = key("unit", day, code, "abroad")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code=CONSTITUENCY_SCHEME_NEW,
            code=f"{day}:{code}:abroad",
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(unit, f"Finnish citizens living abroad, {CONSTITUENCY_NAMES[code]} constituency", "en", "official")
        put(municipal, unit, row)
    for code, row in count.constituencies.items():
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code=CONSTITUENCY_SCHEME_NEW,
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=vaalit.area("fi.vaalipiiri", code),
            kind="standard",
        )
        p.name(unit, f"{CONSTITUENCY_NAMES[code]} constituency", "en", "official")
        put(constituency, unit, row)
    # the declared totals: the Ministry's notice for the votes, the volume for the electorate and the ballots cast
    yes, no = 1_620_726, 1_228_261
    totals = count.national.values
    declared = {
        (REGISTERED, None): 4_042_607,
        (VALID, None): yes + no,
        (INVALID, None): totals[V_REJECTED],
        (VOTES, "yes"): yes,
        (VOTES, "no"): no,
    }
    assert (
        totals[V_VALID] == yes + no and totals[V_YES] == yes and totals[V_NO] == no and totals[V_ENTITLED] == 4_042_607
    )
    assert (totals[V_VALID] or 0) + (totals[V_REJECTED] or 0) == 2_861_841
    for result_set in (municipal, constituency):
        for (measure, option), value in declared.items():
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=b.options[option] if option else None,
                measure=measure,
                value=value,
            )
        p.add("outcome", result_set_id=result_set, option_id=b.options["yes"], kind="won")
        p.add("outcome", result_set_id=result_set, option_id=b.options["no"], kind="lost")
        _write_values(b, result_set)
        _write_locators(b, result_set)
    return p


def _write_locators(b: Build, result_set: str) -> None:
    import polars as pl

    rows = [row for row in b.locators if row[0] == result_set]
    if not rows:
        return
    b.p.frame(
        "value_locator",
        pl.DataFrame(
            [row[1:] for row in rows],
            schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
            orient="row",
            infer_schema_length=None,
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )


def add3(a: tuple[int, int, int], b: tuple[int, int, int]) -> tuple[int, int, int]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def find_triples(page: digitized.Page, triples: list[tuple[int, int, int]]) -> dict[tuple[int, int, int], Box | None]:
    """The box of the last figure of each triple of men, women, and both, found where the three digits run together on
    one line of the page. The scan breaks the thousands apart and sometimes joins words, so the digits of a line are
    joined and searched, and a triple counts only when it occurs once on the page."""
    lines: list[tuple[str, list[tuple[int, Box]]]] = []
    for line in digitized.rows(page.words, 3.0):
        text = ""
        owner: list[tuple[int, Box]] = []
        for word in line:
            if re.search(r"[,.]", word.text) or not word.text.isdigit():
                continue
            for _ in word.text:
                owner.append((len(text), word))
                text += _
        lines.append((text, owner))
    found: dict[tuple[int, int, int], Box | None] = {}
    for triple in triples:
        needle = "".join(str(value) for value in triple)
        hits = []
        for text, owner in lines:
            start = text.find(needle)
            while start >= 0:
                hits.append((start, owner))
                start = text.find(needle, start + 1)
        if len(hits) != 1:
            found[triple] = None
            continue
        start, owner = hits[0]
        words = {id(word): word for _, word in owner[start + len(needle) - len(str(triple[2])) : start + len(needle)]}
        boxes = list(words.values())
        found[triple] = Box(
            "",
            min(box.x0 for box in boxes),
            min(box.y0 for box in boxes),
            max(box.x1 for box in boxes),
            max(box.y1 for box in boxes),
        )
    return found


def election_1931() -> Package:
    day, last = DAYS_1931
    b = _start(
        day,
        last,
        "The prohibition referendum of 29 and 30 December 1931 by municipality, with the provinces, from "
        "the official statistics",
        (
            "1931 Finnish prohibition referendum",
            "Kieltolakiäänestys 1931",
            "Förbudsomröstningen 1931",
        ),
        (
            "1931 Finnish prohibition referendum: the future of the prohibition law",
            "Kieltolakiäänestys 1931: kieltolain tulevaisuus",
            "Förbudsomröstningen 1931: förbudslagens framtid",
        ),
        f"fi/{day}-referendum",
    )
    p = b.p
    p.uses(raw.cached(SOURCE, PDF_1931))
    p.add(
        "note",
        id=key("note", b.contest, "question"),
        entity_id=b.contest,
        kind="context",
        body_md=QUESTION_1931,
        lang="en",
    )
    _option(
        b,
        "first",
        1,
        "I",
        (
            "Alternative I: keep the prohibition law on the basis of total prohibition",
            "I vaihtoehto: kieltolaki pysytetään täysikiellon pohjalla",
            "Alternativ I: förbudslagen bibehålls på basen av totalförbud",
        ),
    )
    _option(
        b,
        "second",
        2,
        "II",
        (
            "Alternative II: amend the prohibition law to allow regulated sale of mild alcoholic beverages",
            "II vaihtoehto: kieltolakia muutetaan niin, että mietojen alkoholijuomien valmistus ja kauppa sallitaan",
            "Alternativ II: förbudslagen ändras så att svagare alkoholdrycker får tillverkas och säljas",
        ),
    )
    _option(
        b,
        "third",
        3,
        "III",
        (
            "Alternative III: repeal the prohibition law",
            "III vaihtoehto: kieltolaki kumotaan",
            "Alternativ III: förbudslagen upphävs",
        ),
    )
    towns = _result_set(b, "town-rural", False, 1.0, METHOD_1931, SOURCE_1931)
    provinces = _result_set(b, "province", False, 1.0, PROVINCE_METHOD_1931, SOURCE_1931)
    municipal = _result_set(b, "municipality", True, 1.0, MUNICIPAL_METHOD_1931, SOURCE_1931, supersedes=towns)
    p.add(
        "note",
        id=key("note", towns, "advisory"),
        entity_id=towns,
        kind="caveat",
        body_md="The referendum was advisory. Parliament repealed the prohibition law in 1932, after the vote.",
        lang="en",
    )

    path = raw.cached(SOURCE, PDF_1931).path
    pages = digitized.pages(path, 7, prohibition_1931.LAST_PAGE)
    artifact = str(
        uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_1931}\x1f{raw.cached(SOURCE, PDF_1931).storage_key}")
    )
    page_ids: dict[int, str] = {}
    Triple = tuple[int, int, int]
    wanted: dict[int, list[Triple]] = {7: [], 8: [], 11: []}
    for kind in ("urban", "rural"):
        for index in range(len(PROVINCES_1931)):
            wanted[7].append(REGISTERED_1931[kind][index])
            wanted[8].append(VOTED_1931[kind][index])
            wanted[11].extend(VOTES_1931[kind][index])
    for index in range(len(PROVINCES_1931)):
        for table, page in ((REGISTERED_1931, 7), (VOTED_1931, 8)):
            wanted[page].append(add3(table["urban"][index], table["rural"][index]))
        for q in range(4):
            wanted[11].append(add3(VOTES_1931["urban"][index][q], VOTES_1931["rural"][index][q]))
    boxes = {number: find_triples(pages[number], triples) for number, triples in wanted.items()}

    def page_id(number: int) -> str:
        if number not in page_ids:
            page_ids[number] = key("page", number)
            size = pages[number]
            p.add(
                "source_page",
                id=page_ids[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{raw.cached(SOURCE, PDF_1931).sha256}-{number}.png",
                width=round(size.width * 100 / 72),
                height=round(size.height * 100 / 72),
            )
        return page_ids[number]

    def put(
        result_set: str,
        unit: str,
        registered: tuple[int, Triple],
        cast: tuple[int, Triple],
        votes: tuple[tuple[int, Triple], ...],
        valid: tuple[int, Triple],
    ) -> None:
        derived = cast[0] - valid[0]
        b.values.append((result_set, unit, None, INVALID, derived))
        for measure, option, (value, triple), page in (
            (REGISTERED, None, registered, 7),
            (CAST, None, cast, 8),
            (VALID, None, valid, 11),
            (VOTES, "first", votes[0], 11),
            (VOTES, "second", votes[1], 11),
            (VOTES, "third", votes[2], 11),
        ):
            option_id = b.options[option] if option else None
            b.values.append((result_set, unit, option_id, measure, value))
            box = boxes[page].get(triple)
            if box is not None:
                b.locators.append((result_set, unit, option_id, measure, page_id(page), *digitized.pixels(box)))

    for index, (slug, finnish, swedish, english) in enumerate(PROVINCES_1931):
        both: dict[str, tuple[int, int, int, int, int, int]] = {}
        for kind, (name_en, name_fi, name_sv) in (
            ("urban", ("towns", "kaupungit", "städer")),
            ("rural", ("rural municipalities", "maalaiskunnat", "landsbygd")),
        ):
            votes = VOTES_1931[kind][index]
            registered = REGISTERED_1931[kind][index][BOTH]
            cast = VOTED_1931[kind][index][BOTH]
            unit = key("unit", day, slug, kind)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=vaalit.FINLAND,
                scheme_code=PROVINCE_SCHEME,
                code=f"{day}:{slug}:{kind}",
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
            )
            p.name(unit, f"{english}: {name_en}", "en", "official")
            p.name(unit, f"{finnish}: {name_fi}", "fi", "native", False)
            p.name(unit, f"{swedish}: {name_sv}", "sv", "native", False)
            put(
                towns,
                unit,
                (registered, REGISTERED_1931[kind][index]),
                (cast, VOTED_1931[kind][index]),
                tuple((votes[q][BOTH], votes[q]) for q in range(3)),
                (votes[3][BOTH], votes[3]),
            )
            both[kind] = (registered, cast, votes[0][BOTH], votes[1][BOTH], votes[2][BOTH], votes[3][BOTH])
        whole = tuple(both["urban"][i] + both["rural"][i] for i in range(6))
        unit = key("unit", day, slug)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code=PROVINCE_SCHEME,
            code=f"{day}:{slug}",
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(unit, english, "en", "official")
        p.name(unit, finnish, "fi", "native", False)
        p.name(unit, swedish, "sv", "native", False)

        put(
            provinces,
            unit,
            (whole[0], add3(REGISTERED_1931["urban"][index], REGISTERED_1931["rural"][index])),
            (whole[1], add3(VOTED_1931["urban"][index], VOTED_1931["rural"][index])),
            tuple((whole[2 + q], add3(VOTES_1931["urban"][index][q], VOTES_1931["rural"][index][q])) for q in range(3)),
            (whole[5], add3(VOTES_1931["urban"][index][3], VOTES_1931["rural"][index][3])),
        )
    for leaf in prohibition_1931.leaves(path):
        slug = PROVINCES_1931[leaf.province][0]
        unit = key("unit", day, slug, leaf.kind, leaf.seq)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code=SCHEME,
            code=f"{day}:{slug}:{leaf.kind}:{leaf.seq}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, leaf.name, "und", "official")
        v = leaf.values
        total = {c: v[c * 3 + BOTH] for c in range(7)}
        b.values.append((municipal, unit, None, INVALID, total[prohibition_1931.CAST] - total[prohibition_1931.VALID]))
        for measure, option, column, number in (
            (REGISTERED, None, prohibition_1931.REGISTERED, 7),
            (CAST, None, prohibition_1931.CAST, 8),
            (VALID, None, prohibition_1931.VALID, 11),
            (VOTES, "first", prohibition_1931.FIRST, 11),
            (VOTES, "second", prohibition_1931.SECOND, 11),
            (VOTES, "third", prohibition_1931.THIRD, 11),
        ):
            option_id = b.options[option] if option else None
            b.values.append((municipal, unit, option_id, measure, total[column]))
            box = leaf.boxes.get(column * 3 + BOTH)
            if box is not None:
                b.locators.append((municipal, unit, option_id, measure, page_id(leaf.page), *digitized.pixels(box)))
    totals = VOTES_COUNTRY_1931["whole"]
    declared = {
        (REGISTERED, None): REGISTERED_WHOLE_1931[BOTH],
        (CAST, None): VOTED_WHOLE_1931[BOTH],
        (VALID, None): totals[3][BOTH],
        (INVALID, None): REJECTED_WHOLE_1931[BOTH],
        (VOTES, "first"): totals[0][BOTH],
        (VOTES, "second"): totals[1][BOTH],
        (VOTES, "third"): totals[2][BOTH],
    }
    for result_set in (towns, provinces, municipal):
        for (measure, option), value in declared.items():
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=b.options[option] if option else None,
                measure=measure,
                value=value,
            )
        p.add("outcome", result_set_id=result_set, option_id=b.options["third"], kind="won")
        p.add("outcome", result_set_id=result_set, option_id=b.options["first"], kind="lost")
        p.add("outcome", result_set_id=result_set, option_id=b.options["second"], kind="lost")
        _write_values(b, result_set, derived=(INVALID,))
        _write_locators(b, result_set)
    return p
