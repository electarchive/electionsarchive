"""Sweden's national consultative referendums of 1922 to 1994, digitized from Statistics Sweden's publications.

Statistics Sweden (SCB) has digitized its older series, but not the separate reports that first printed each
referendum's full results. The finest tables in what it has digitized are these:

- 13 October 1957, supplementary pensions: county tables in Ture Widstam's article "Folkomröstningen i pensionsfrågan
  den 13 oktober 1957" in Statistisk tidskrift 1958 (tables 1 and 2, printed pages 310 and 311).
- 23 March 1980, nuclear power: the county table of Statistisk årsbok för Sverige 1981 (table 507, printed pages 425
  and 426).
- 27 August 1922 (prohibition), 16 October 1955 (right-hand traffic), and 13 November 1994 (membership of the
  European Union): the national totals of Statistisk årsbok för Sverige 1996 (table 481, printed page 400), which also
  prints the national totals of 1957 and 1980.

Every figure was read from the page images, which have no text layer under the tables. Each table's rows add up to
their printed totals and its columns to the printed national totals, and every figure is checked against both before
a package is written.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.se import scb, val

SOURCE = scb.SOURCE
REGISTRY = f"{SOURCE}/referendum-registry"
SHARE = "https://share.scb.se/ov9993/data/historisk%20statistik"
YEARBOOK = f"{SHARE}/SOS%201911-%2FStatistisk%20%C3%A5rsbok%20%28SOS%29%201914-2014%2FStatistisk-arsbok-for-Sverige-"
YEARBOOK_1981 = f"{YEARBOOK}1981.pdf"
YEARBOOK_1996 = f"{YEARBOOK}1996.pdf"
TIDSKRIFT_1958 = f"{SHARE}/Statistisk%20tidskrift%201952-1962%2FStatistisk-tidskrift-1958.pdf"
URLS = (YEARBOOK_1981, YEARBOOK_1996, TIDSKRIFT_1958)

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DIGITIZED = 8
SWEDEN = val.SWEDEN
COUNTRY = val.COUNTRY
NATIONAL_SCHEME = "world.country"

SOURCE_1981 = scb.key("source", "statistisk-arsbok-1981")
SOURCE_1996 = scb.key("source", "statistisk-arsbok-1996")
SOURCE_1958 = scb.key("source", "statistisk-tidskrift-1958")
SYSTEM = scb.key("electoral_system", "referendum")
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"


def fetch() -> None:
    for url in URLS:
        raw.fetch(SOURCE, url, suffix=".pdf")


def cited() -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in URLS]


# ---------------------------------------------------------------------------------------------------------------------
# The tables, as printed. Boxes are in the pixels of the page rendered at 100 dpi: each table gives its columns' left
# and right edges and the top of each row; a row is 12 pixels tall.

Columns = dict[str, tuple[int, int]]


@dataclass(frozen=True)
class Page:
    url: str
    number: int  # the page of the PDF file
    width: int
    height: int


ST_324 = Page(TIDSKRIFT_1958, 324, 570, 822)
ST_325 = Page(TIDSKRIFT_1958, 325, 570, 822)
YB_427 = Page(YEARBOOK_1981, 427, 600, 867)
YB_428 = Page(YEARBOOK_1981, 428, 601, 868)
YB_402 = Page(YEARBOOK_1996, 402, 584, 867)

# Counties as the tables name them, with the code of the archive's county area. Stockholms stad and Stockholms län
# were separate counties until 1968; Kopparbergs län is today's Dalarnas län; Kristianstads, Malmöhus, Göteborgs och
# Bohus, Älvsborgs, and Skaraborgs län were merged into Skåne and Västra Götaland in 1997 and 1998.
COUNTIES_1957 = (
    ("Stockholms stad", scb.STOCKHOLM_CITY),
    ("Stockholms län", scb.STOCKHOLM_COUNTY),
    ("Uppsala län", "03"),
    ("Södermanlands län", "04"),
    ("Östergötlands län", "05"),
    ("Jönköpings län", "06"),
    ("Kronobergs län", "07"),
    ("Kalmar län", "08"),
    ("Gotlands län", "09"),
    ("Blekinge län", "10"),
    ("Kristianstads län", "11"),
    ("Malmöhus län", "12G"),
    ("Hallands län", "13"),
    ("Göteborgs och Bohus län", "14G"),
    ("Älvsborgs län", "15"),
    ("Skaraborgs län", "16"),
    ("Värmlands län", "17"),
    ("Örebro län", "18"),
    ("Västmanlands län", "19"),
    ("Kopparbergs län", "20"),
    ("Gävleborgs län", "21"),
    ("Västernorrlands län", "22"),
    ("Jämtlands län", "23"),
    ("Västerbottens län", "24"),
    ("Norrbottens län", "25"),
)
COUNTIES_1980 = (("Stockholms län", "01"), *COUNTIES_1957[2:])

# Statistisk tidskrift 1958, table 1 (page 324): persons entitled to vote and persons who voted, men, women, and both.
TAB1_1957 = (
    (251032, 304962, 555994, 187300, 221884, 409184),
    (127890, 131368, 259258, 95410, 93993, 189403),
    (53048, 56196, 109244, 38509, 37781, 76290),
    (72388, 74260, 146648, 56483, 54143, 110626),
    (117969, 121594, 239563, 89217, 85153, 174370),
    (93418, 94100, 187518, 74846, 69133, 143979),
    (55456, 52672, 108128, 41060, 35714, 76774),
    (80446, 79221, 159667, 58251, 52074, 110325),
    (18249, 18169, 36418, 12389, 11083, 23472),
    (48436, 48741, 97177, 34072, 31566, 65638),
    (86648, 87212, 173860, 64508, 60108, 124616),
    (197983, 215176, 413159, 160266, 169139, 329405),
    (56571, 56306, 112877, 42417, 39021, 81438),
    (197802, 210143, 407945, 143196, 145846, 289042),
    (123124, 125691, 248815, 93910, 88656, 182566),
    (84739, 83961, 168700, 62928, 56151, 119079),
    (99901, 97266, 197167, 74532, 65768, 140300),
    (86150, 88098, 174248, 63438, 60978, 124416),
    (70718, 69722, 140440, 53137, 48530, 101667),
    (93603, 92881, 186484, 66687, 59487, 126174),
    (98923, 98699, 197622, 70661, 64131, 134792),
    (96146, 95093, 191239, 74168, 68112, 142280),
    (48834, 45647, 94481, 34481, 28855, 63336),
    (77933, 72633, 150566, 59892, 50764, 110656),
    (77970, 72513, 150483, 55211, 46649, 101860),
)
TAB1_1957_TOTAL = (2415377, 2492324, 4907701, 1806969, 1744719, 3551688)
# Row tops on page 324; Göteborgs stad, printed under its county, is the fifteenth row and is skipped.
TAB1_ROWS = (223, 233, 244, 254, 264, 274, 284, 295, 305, 316, 326, 336, 346, 357, 377, 388, 398, 408, 418, 429, 439)
TAB1_ROWS += (449, 460, 470, 480)
TAB1_COLUMNS: Columns = {"registered": (279, 327), "cast": (478, 521)}

# Statistisk tidskrift 1958, table 2 (page 325): valid ballots for alternatives 1, 2, and 3, blank ballots, and all
# valid ballots, the blank ones included.
TAB2_1957 = (
    (185441, 20557, 181064, 20838, 407900),
    (88896, 18272, 73737, 8156, 189061),
    (35463, 12186, 24848, 3667, 76164),
    (56556, 12386, 36795, 4587, 110324),
    (85738, 26448, 55538, 6134, 173858),
    (52894, 27970, 58434, 4448, 143746),
    (27018, 22670, 24435, 2526, 76649),
    (44676, 28724, 33713, 2982, 110095),
    (7162, 8818, 6530, 929, 23439),
    (31262, 7972, 24891, 1554, 65679),
    (40733, 25002, 53569, 4884, 124188),
    (143357, 41842, 130187, 11952, 327338),
    (27052, 28258, 21986, 3993, 81289),
    (130482, 20580, 127863, 9265, 288190),
    (73410, 34520, 68081, 6055, 182066),
    (37051, 28566, 48567, 4747, 118931),
    (72016, 19562, 43904, 4546, 140028),
    (62849, 20266, 34090, 6928, 124133),
    (57286, 13830, 25954, 4302, 101372),
    (65548, 25479, 28709, 6119, 125855),
    (76921, 22211, 30911, 4533, 134576),
    (77022, 22356, 38842, 3980, 142200),
    (31167, 12087, 17719, 2260, 63233),
    (48415, 18917, 38940, 4259, 110531),
    (65716, 11097, 22170, 2755, 101738),
)
TAB2_1957_TOTAL = (1624131, 530576, 1251477, 136399, 3542583)
TAB2_ROWS = (227, 237, 248, 258, 268, 278, 289, 299, 309, 319, 330, 340, 350, 360, 381, 391, 401, 412, 422, 432, 443)
TAB2_ROWS += (453, 463, 473, 484)
TAB2_COLUMNS: Columns = {"1": (168, 216), "2": (216, 260), "3": (261, 309), "blank": (310, 348), "valid": (352, 397)}

# Statistisk årsbok 1981, table 507 (pages 427 and 428): persons entitled to vote, of whom foreign citizens, voters
# with valid ballots (blank ones included), voters with invalid ballots, all voters, and the valid ballots for
# alternatives (linjer) 1, 2, and 3 and blank ballots. The first twelve counties are on page 427, the rest on 428.
TAB_1980 = (
    (1162550, 73571, 905327, 555, 905882, 230045, 295837, 350942, 28503),
    (178419, 6050, 140185, 94, 140279, 25219, 52642, 58051, 4273),
    (189859, 8855, 144615, 106, 144721, 23946, 67192, 48110, 5367),
    (298256, 7080, 223654, 119, 223773, 42343, 95022, 77278, 9011),
    (228052, 5965, 174152, 95, 174247, 29717, 67137, 71314, 5984),
    (129365, 2910, 96378, 71, 96449, 16909, 35669, 40702, 3098),
    (184958, 2553, 135615, 83, 135698, 23468, 54507, 53568, 4072),
    (41591, 183, 30442, 17, 30459, 4161, 10474, 14843, 964),
    (117032, 2478, 83404, 90, 83494, 15116, 41359, 24141, 2788),
    (210473, 3769, 151368, 133, 151501, 35937, 62629, 47490, 5312),
    (571545, 22655, 439290, 468, 439758, 124584, 190651, 109310, 14745),
    (170573, 4194, 132065, 78, 132143, 24695, 45813, 56498, 5059),
    (552196, 26564, 417545, 248, 417793, 80584, 145791, 177136, 14034),
    (317930, 12412, 241922, 139, 242061, 39797, 91748, 101869, 8508),
    (201597, 3862, 152247, 132, 152379, 24557, 52248, 70079, 5363),
    (221090, 3808, 161316, 76, 161392, 25224, 67239, 63929, 4924),
    (210481, 5609, 159400, 89, 159489, 21975, 69127, 63162, 5136),
    (194799, 10821, 146701, 96, 146797, 29375, 64666, 46922, 5738),
    (219538, 4702, 161617, 119, 161736, 19249, 65725, 71112, 5531),
    (227871, 3741, 166136, 87, 166223, 17145, 69911, 74080, 5000),
    (206749, 2331, 156934, 95, 157029, 15831, 67923, 68661, 4519),
    (104551, 904, 75687, 43, 75730, 7250, 31259, 35120, 2058),
    (182991, 1638, 140026, 74, 140100, 13734, 56827, 65938, 3527),
    (198699, 4898, 142300, 46, 142346, 14107, 67948, 56656, 3589),
)
TAB_1980_TOTAL = (6321165, 221553, 4778326, 3153, 4781479, 904968, 1869344, 1846911, 157103)
ROWS_427 = (646, 657, 667, 678, 689, 705, 716, 727, 738, 748, 765, 775)
ROWS_428 = (148, 159, 170, 186, 197, 208, 219, 229, 246, 256, 267, 278)
COLUMNS_427: Columns = {
    "registered": (124, 173),
    "invalid": (269, 293),
    "cast": (309, 351),
    "1": (392, 433),
    "2": (440, 481),
    "3": (488, 530),
    "blank": (534, 571),
}
COLUMNS_428: Columns = {
    "registered": (123, 165),
    "invalid": (262, 286),
    "cast": (302, 344),
    "1": (390, 426),
    "2": (434, 475),
    "3": (483, 523),
    "blank": (528, 565),
}

# Statistisk årsbok 1996, table 481 (page 402): the national totals of each referendum, with the row of each figure.
NATIONAL_COLUMNS: Columns = {"registered": (270, 320), "cast": (334, 380), "votes": (432, 478), "invalid": (518, 555)}


@dataclass(frozen=True)
class National:
    registered: int
    cast: int
    valid: int  # all valid ballots as printed, the blank ones included
    invalid: int
    answers: tuple[int, ...]
    blank: int | None
    rows: tuple[int, ...]  # the row of the totals, then of each answer, then of the blank ballots


NATIONAL = {
    date(1922, 8, 27): National(3302483, 1820452, 1814229, 6223, (889132, 925097), None, (145, 177, 209)),
    date(1955, 10, 16): National(4866100, 2587730, 2581687, 6043, (400061, 2139996), 41630, (230, 251, 272, 283)),
    date(1957, 10, 13): National(
        4907701, 3552865, 3542583, 10282, (1624131, 530576, 1251477), 136399, (315, 326, 336, 347, 357)
    ),
    date(1980, 3, 23): National(
        6321165, 4781479, 4778326, 3153, (904968, 1869344, 1846911), 157103, (379, 389, 400, 410, 421)
    ),
    date(1994, 11, 13): National(6510055, 5424487, 5421790, 2697, (2833721, 2539132), 48937, (442, 453, 464, 474)),
}


# ---------------------------------------------------------------------------------------------------------------------
# The referendums.


@dataclass(frozen=True)
class Answer:
    key: str
    label: str  # as printed on the ballot or in the table
    english: str


@dataclass(frozen=True)
class Referendum:
    day: date
    word: str
    english: str
    swedish: str
    question_en: str
    question_sv: str
    answers: tuple[Answer, ...]
    context: str


REFERENDUMS = (
    Referendum(
        date(1922, 8, 27),
        "prohibition",
        "1922 Swedish prohibition referendum",
        "Folkomröstningen om rusdrycksförbud 1922",
        "Should a general prohibition of alcoholic beverages be introduced?",
        "Folkomröstning angående rusdrycksförbud",
        (
            Answer("yes", "För införande av fullständigt rusdrycksförbud", "Yes: introduce general prohibition"),
            Answer("no", "Mot införande av fullständigt rusdrycksförbud", "No: against general prohibition"),
        ),
        "A consultative referendum on introducing a general prohibition of alcoholic beverages. Prohibition was "
        "rejected, and the Bratt system of rationing stayed in force. Men and women voted on ballots of different "
        "colors.",
    ),
    Referendum(
        date(1955, 10, 16),
        "right-hand-traffic",
        "1955 Swedish right-hand traffic referendum",
        "Folkomröstningen om högertrafik 1955",
        "Should Sweden change to driving on the right?",
        "Folkomröstning angående högertrafik",
        (
            Answer("right", "För införande av högertrafik", "Introduce right-hand traffic"),
            Answer("left", "För bibehållande av vänstertrafik", "Keep left-hand traffic"),
        ),
        "A consultative referendum on changing from left-hand to right-hand traffic. Keeping left-hand traffic won, "
        "but the Riksdag decided in 1963 to change, and Sweden drove on the right from 3 September 1967. The number "
        "of persons entitled to vote is Statistics Sweden's estimate.",
    ),
    Referendum(
        date(1957, 10, 13),
        "pensions",
        "1957 Swedish pension referendum",
        "Folkomröstningen i pensionsfrågan 1957",
        "Which of three proposals for supplementary pensions should be adopted?",
        "Folkomröstning i pensionsfrågan",
        (
            Answer(
                "1",
                "Förslag nr 1",
                "Alternative 1: a statutory supplementary pension for employees, related to earlier pay and keeping "
                "its value (Social Democrats, supported by the Communists)",
            ),
            Answer(
                "2",
                "Förslag nr 2",
                "Alternative 2: voluntary supplementary pension insurance, guaranteed by the state (Agrarian Party)",
            ),
            Answer(
                "3",
                "Förslag nr 3",
                "Alternative 3: voluntary supplementary pensions through collective agreements, without a state "
                "guarantee (People's Party and Conservatives)",
            ),
        ),
        "A consultative referendum between three proposals for supplementary pensions (tilläggspension, ATP). No "
        "proposal won a majority; after the 1958 election the Riksdag adopted the statutory pension of proposal 1 in "
        "1959.",
    ),
    Referendum(
        date(1980, 3, 23),
        "nuclear-power",
        "1980 Swedish nuclear power referendum",
        "Folkomröstningen om kärnkraften 1980",
        "How should nuclear power be phased out?",
        "Folkomröstning om kärnkraften",
        (
            Answer(
                "1",
                "Linje 1",
                "Line 1: phase out nuclear power as fast as employment and welfare allow, using at most the 12 "
                "reactors in operation, ready, or under construction",
            ),
            Answer(
                "2",
                "Linje 2",
                "Line 2: as line 1, with public ownership of power production and stronger energy saving",
            ),
            Answer(
                "3",
                "Linje 3",
                "Line 3: no further expansion, and the six reactors in operation closed within ten years",
            ),
        ),
        "A consultative referendum between three lines for phasing out nuclear power. Line 2 won the most votes, and "
        "the Riksdag decided that nuclear power should be phased out by 2010.",
    ),
    Referendum(
        date(1994, 11, 13),
        "eu-membership",
        "1994 Swedish European Union membership referendum",
        "Folkomröstningen om EU-medlemskap 1994",
        "Should Sweden become a member of the European Union?",
        "Folkomröstning om EU",
        (
            Answer("yes", "Ja till medlemskap", "Yes to membership"),
            Answer("no", "Nej till medlemskap", "No to membership"),
        ),
        "A consultative referendum on membership of the European Union. Yes won, and Sweden joined on 1 January 1995.",
    ),
)
BY_DAY = {r.day: r for r in REFERENDUMS}

METHOD = (
    "Read from the page images of Statistics Sweden's digitized publications, which have no text layer under the "
    "tables. The persons entitled to vote, the voters, and the votes for each answer are as printed. Statistics "
    "Sweden counts blank ballots (blanka röstsedlar) among the valid ballots (godkända or giltiga valsedlar); the "
    "archive stores them apart, and its valid ballots are the votes for the answers alone. Every row of the table "
    "adds up to its printed total, and every column to the printed national total."
)
METHOD_1957 = (
    METHOD + " The counties are those of 1957, with Stockholms stad apart from Stockholms län. The persons entitled to "
    "vote and the voters come from table 1 of Ture Widstam's article in Statistisk tidskrift 1958, the votes from "
    "table 2. The article counts the postal votes (poströster) in each county, and its voters in table 1 total 1,177 "
    "fewer than the national figure in table 2; invalid ballots are printed for the country only. The article also "
    "prints Göteborgs stad within Göteborgs och Bohus län; that row is not stored."
)
METHOD_1980 = (
    METHOD + " The counties are those of 1980, from table 507 of Statistisk årsbok för Sverige 1981; the yearbook's "
    "source is Statistiska meddelanden Be 1980:5. Its persons entitled to vote include foreign citizens, who could "
    "not vote in a referendum but are printed in the same column."
)
METHOD_NATIONAL = (
    "The national totals of table 481 of Statistisk årsbok för Sverige 1996, read from the page image. Statistics "
    "Sweden counts blank ballots (blanka röstsedlar) among the valid ballots; the archive stores them apart, and its "
    "valid ballots are the votes for the answers alone. The valid votes and the invalid ballots add up to the voters "
    "printed. Statistics Sweden has not digitized a finer table of this referendum."
)


def option(day: date, answer: str) -> str:
    return scb.key("option", day, "referendum", answer)


def page_id(page: Page) -> str:
    return scb.key("source_page", page.url, page.number)


def artifact_id(url: str, source: str) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{source}\x1f{raw.cached(SOURCE, url).storage_key}"))


PAGE_SOURCE = {TIDSKRIFT_1958: SOURCE_1958, YEARBOOK_1981: SOURCE_1981, YEARBOOK_1996: SOURCE_1996}


def check_tables() -> None:
    """Every row adds up to its printed total and every column to the printed national total."""
    if [sum(column) for column in zip(*TAB1_1957, strict=True)] != list(TAB1_1957_TOTAL):
        raise ValueError("1957 table 1: a column does not add up to its national total")
    if [sum(column) for column in zip(*TAB2_1957, strict=True)] != list(TAB2_1957_TOTAL):
        raise ValueError("1957 table 2: a column does not add up to its national total")
    if [sum(column) for column in zip(*TAB_1980, strict=True)] != list(TAB_1980_TOTAL):
        raise ValueError("1980: a column does not add up to its national total")
    for row in (*TAB1_1957, TAB1_1957_TOTAL):
        if row[0] + row[1] != row[2] or row[3] + row[4] != row[5]:
            raise ValueError(f"1957 table 1: {row} does not add up")
    for row in (*TAB2_1957, TAB2_1957_TOTAL):
        if sum(row[:4]) != row[4]:
            raise ValueError(f"1957 table 2: {row} does not add up")
    for row in (*TAB_1980, TAB_1980_TOTAL):
        if row[2] + row[3] != row[4] or sum(row[5:]) != row[2]:
            raise ValueError(f"1980: {row} does not add up")
    for day, n in NATIONAL.items():
        if sum(n.answers) + (n.blank or 0) != n.valid or n.valid + n.invalid != n.cast:
            raise ValueError(f"{day}: the national totals do not add up")
    if NATIONAL[date(1957, 10, 13)].answers != TAB2_1957_TOTAL[:3]:
        raise ValueError("1957: the article and the yearbook disagree")
    if NATIONAL[date(1980, 3, 23)].answers != TAB_1980_TOTAL[5:8]:
        raise ValueError("1980: the two yearbooks disagree")
    if len(TAB1_ROWS) != len(COUNTIES_1957) or len(TAB2_ROWS) != len(COUNTIES_1957):
        raise ValueError("1957: a table has the wrong number of rows")
    if len(ROWS_427) + len(ROWS_428) != len(COUNTIES_1980):
        raise ValueError("1980: the table has the wrong number of rows")


# ---------------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{r.day}" for r in REFERENDUMS)]


def parse() -> None:
    check_tables()
    registry().write()
    for r in REFERENDUMS:
        election(r).write()


def _source(p: Package, id: str, title: str, url: str) -> None:
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher="Statistics Sweden (Statistiska centralbyrån)",
        title=title,
        url=url,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Statistics Sweden (SCB)",
    )
    p.artifact(raw.cached(SOURCE, url), id)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The publications of Statistics Sweden that Sweden's national referendums of 1922 to 1994 are digitized from, "
        "and the referendums' electoral system",
    )
    p.external.add(SWEDEN)
    _source(
        p,
        SOURCE_1958,
        "Statistisk tidskrift 1958 (Folkomröstningen i pensionsfrågan den 13 oktober 1957)",
        TIDSKRIFT_1958,
    )
    _source(p, SOURCE_1981, "Statistisk årsbok för Sverige 1981", YEARBOOK_1981)
    _source(p, SOURCE_1996, "Statistisk årsbok för Sverige 1996", YEARBOOK_1996)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=SWEDEN,
        valid_from=date(1922, 1, 1),
        valid_to=None,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=scb.key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A consultative national referendum: the answer with the most votes carries, without binding effect; "
        "the Riksdag decides what follows.",
        lang="en",
    )
    return p


@dataclass
class Build:
    p: Package
    r: Referendum
    result_set: str
    values: list[tuple[str, str | None, int, int, int]] = field(default_factory=list)
    locators: list[tuple[str, str | None, int, str, float, float, float, float]] = field(default_factory=list)
    pages: set[Page] = field(default_factory=set)

    def put(
        self,
        unit: str,
        answer: str | None,
        measure: int,
        value: int,
        page: Page | None = None,
        box: tuple[int, ...] = (),
    ) -> None:
        option_id = None if answer is None else option(self.r.day, answer)
        self.values.append((unit, option_id, measure, value, 0 if page is None else DIGITIZED))
        if page is not None:
            self.pages.add(page)
            x0, x1, top = box
            self.locators.append((unit, option_id, measure, page_id(page), x0, top - 2, x1, top + 10))


def election(r: Referendum) -> Package:
    day = r.day
    national = NATIONAL[day]
    finest = "county" if day.year in (1957, 1980) else "country"
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {r.english[5:]} of {day:%-d %B %Y} by {finest}, digitized from Statistics Sweden's publications",
        depends=[REGISTRY, f"{SOURCE}/geo", f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, NATIONAL_SCHEME, val.COUNTY}
    for url in {page.url for page in _pages(day)}:
        p.uses(raw.cached(SOURCE, url))
    election_id = scb.key("election", day, "referendum")
    p.add(
        "election",
        id=election_id,
        polity_id=SWEDEN,
        kind="referendum",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, r.english, "en", "official")
    p.name(election_id, r.swedish, "sv", "native", False)
    slug = f"se/{day}-referendum"
    p.add("slug", path=slug, entity_id=election_id, is_current=True)
    contest = scb.key("contest", day, "referendum")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        body_id=None,
        district_area_id=COUNTRY,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, r.question_en, "en", "official")
    p.name(contest, r.question_sv, "sv", "native", False)
    p.add("slug", path=f"{slug}/{r.word}", entity_id=contest, is_current=True)
    p.add(
        "note", id=scb.key("note", contest, "context"), entity_id=contest, kind="context", body_md=r.context, lang="en"
    )
    for position, a in enumerate(r.answers, start=1):
        id = option(day, a.key)
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="answer",
            party_id=None,
            ballot_position=position,
            ballot_label=a.label,
        )
        p.name(id, a.english, "en", "official")
        p.name(id, a.label, "sv", "native", False)
    result_set = scb.key("result_set", day, "referendum")
    method = {1957: METHOD_1957, 1980: METHOD_1980}.get(day.year, METHOD_NATIONAL)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_1958 if day.year == 1957 else SOURCE_1981 if day.year == 1980 else SOURCE_1996,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=scb.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    b = Build(p, r, result_set)
    if day.year == 1957:
        _counties_1957(b)
        p.accept(
            "accounting.ballots_sum",
            "Statistisk tidskrift 1958 prints invalid ballots for the whole country only (10,282), so no county's "
            "voters (table 1) can be reconciled with its valid and blank ballots (table 2); the two tables are separate "
            "counts, and in Blekinge län the ballots of table 2 exceed the voters of table 1 by 41.",
        )
        p.accept(
            "accounting.declared",
            "The counties' voters in table 1 of Statistisk tidskrift 1958 total 3,551,688, 1,177 fewer than the "
            "3,552,865 voters of the national count, and invalid ballots are printed for the whole country only.",
        )
    elif day.year == 1980:
        _counties_1980(b)
    else:
        _national(b)
    answers = [a.key for a in r.answers]
    declared: list[tuple[int, str | None, int]] = [
        (REGISTERED, None, national.registered),
        (CAST, None, national.cast),
        (VALID, None, sum(national.answers)),
        (INVALID, None, national.invalid),
        *((VOTES, key, n) for key, n in zip(answers, national.answers, strict=True)),
    ]
    if national.blank is not None:
        declared.append((BLANK, None, national.blank))
    for measure, answer, value in declared:
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=None if answer is None else option(day, answer),
            measure=measure,
            value=value,
        )
    winner = max(range(len(answers)), key=lambda index: national.answers[index])
    for index, key in enumerate(answers):
        p.add(
            "outcome", result_set_id=result_set, option_id=option(day, key), kind="won" if index == winner else "lost"
        )
    _write(b)
    return p


def _pages(day: date) -> list[Page]:
    if day.year == 1957:
        return [ST_324, ST_325, YB_402]
    if day.year == 1980:
        return [YB_427, YB_428, YB_402]
    return [YB_402]


def _unit(b: Build, name: str, code: str) -> str:
    day = b.r.day
    unit = scb.key("unit", day, "referendum", code)
    b.p.add(
        "reporting_unit",
        id=unit,
        polity_id=SWEDEN,
        scheme_code=val.COUNTY,
        code=f"{day}:{code}",
        area_relation="equals",
        area_id=scb.area(val.COUNTY, code),
        kind="standard",
    )
    b.p.name(unit, name, "sv", "official")
    return unit


def _counties_1957(b: Build) -> None:
    for index, (name, code) in enumerate(COUNTIES_1957):
        unit = _unit(b, name, code)
        tab1, tab2 = TAB1_1957[index], TAB2_1957[index]
        top1, top2 = TAB1_ROWS[index], TAB2_ROWS[index]
        b.put(unit, None, REGISTERED, tab1[2], ST_324, (*TAB1_COLUMNS["registered"], top1))
        b.put(unit, None, CAST, tab1[5], ST_324, (*TAB1_COLUMNS["cast"], top1))
        for position, key in enumerate(("1", "2", "3")):
            b.put(unit, key, VOTES, tab2[position], ST_325, (*TAB2_COLUMNS[key], top2))
        b.put(unit, None, BLANK, tab2[3], ST_325, (*TAB2_COLUMNS["blank"], top2))
        b.put(unit, None, VALID, sum(tab2[:3]))


def _counties_1980(b: Build) -> None:
    for index, (name, code) in enumerate(COUNTIES_1980):
        unit = _unit(b, name, code)
        row = TAB_1980[index]
        page, columns, top = (
            (YB_427, COLUMNS_427, ROWS_427[index])
            if index < len(ROWS_427)
            else (YB_428, COLUMNS_428, ROWS_428[index - 12])
        )
        b.put(unit, None, REGISTERED, row[0], page, (*columns["registered"], top))
        b.put(unit, None, INVALID, row[3], page, (*columns["invalid"], top))
        b.put(unit, None, CAST, row[4], page, (*columns["cast"], top))
        for position, key in enumerate(("1", "2", "3")):
            b.put(unit, key, VOTES, row[5 + position], page, (*columns[key], top))
        b.put(unit, None, BLANK, row[8], page, (*columns["blank"], top))
        b.put(unit, None, VALID, sum(row[5:8]))


def _national(b: Build) -> None:
    day = b.r.day
    n = NATIONAL[day]
    unit = scb.key("unit", day, "referendum", "national")
    b.p.add(
        "reporting_unit",
        id=unit,
        polity_id=SWEDEN,
        scheme_code=NATIONAL_SCHEME,
        code=f"{day}:national",
        area_relation="equals",
        area_id=COUNTRY,
        kind="standard",
    )
    b.p.name(unit, "Hela riket", "sv", "official")
    top = n.rows[0]
    b.put(unit, None, REGISTERED, n.registered, YB_402, (*NATIONAL_COLUMNS["registered"], top))
    b.put(unit, None, CAST, n.cast, YB_402, (*NATIONAL_COLUMNS["cast"], top))
    b.put(unit, None, INVALID, n.invalid, YB_402, (*NATIONAL_COLUMNS["invalid"], top))
    for answer, value, row in zip(b.r.answers, n.answers, n.rows[1:], strict=False):
        b.put(unit, answer.key, VOTES, value, YB_402, (*NATIONAL_COLUMNS["votes"], row))
    if n.blank is not None:
        b.put(unit, None, BLANK, n.blank, YB_402, (*NATIONAL_COLUMNS["votes"], n.rows[-1]))
    b.put(unit, None, VALID, sum(n.answers))


def _write(b: Build) -> None:
    p = b.p
    for page in sorted(b.pages, key=lambda page: (page.url, page.number)):
        artifact = raw.cached(SOURCE, page.url)
        p.add(
            "source_page",
            id=page_id(page),
            artifact_id=artifact_id(page.url, PAGE_SOURCE[page.url]),
            page_number=page.number,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{page.number}.png",
            width=page.width,
            height=page.height,
        )
    p.frame(
        "result_value",
        pl.DataFrame(
            b.values,
            schema={
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(b.result_set), channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            b.locators,
            schema={
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "page_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(b.result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )
