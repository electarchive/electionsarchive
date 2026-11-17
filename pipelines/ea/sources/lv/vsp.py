"""Interwar Latvian elections and referendums from the State Statistical Bureau's printed returns.

The State Statistical Bureau (Valsts statistiskā pārvalde) printed the results of the Constituent Assembly
election of 17-18 April 1920 in its first statistical yearbook (Latvijas statistiskā gada grāmata 1920, chapter IV,
tables 52 and 53), and the results of the referendums of 17-18 December 1927 and 5-6 September 1931 in its monthly
bulletin (Mēneša biļetens). The National Library of Latvia serves the scans page by page. Every figure here was read
from the page image and keeps its page and bounding box; the boxes sit in `vsp_boxes.json`.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.licenses import UNLICENSED

SOURCE = "lv.vsp"
LATVIA = str(uuid5(package.NAMESPACE, "polity\x1fLV"))
BOXES = Path(__file__).with_name("vsp_boxes.json")
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
DISTRICTS_SCHEME = "lv.apgabals_1920"
COUNTIES_SCHEME = "lv.aprinkis_1920"
NATIONAL_SCHEME = "lv.valsts_1920"
REGISTRY = f"{SOURCE}/registry"
GRAMATAS = "https://gramatas.lndb.lv/periodika2-viewer"
PERIODIKA = "https://periodika.lv/periodika2-viewer"


def page_url(host: str, issue: int, page: int) -> str:
    return f"{host}/img/page?pid=IMG{page:05d}&issue={issue}"


@dataclass(frozen=True)
class Volume:
    key: str
    publisher: str
    title: str
    url: str
    host: str
    issue: int
    pages: tuple[int, ...]
    locator: str


YEARBOOK = Volume(
    "yearbook1920",
    "Valsts statistiskā pārvalde",
    "Latvijas statistiskā gada grāmata 1920 = Annuaire statistique de la Latvia pour l'année 1920",
    "https://gramatas.lndb.lv/periodika2-viewer/?lang=fr#issue:681826",
    GRAMATAS,
    681826,
    (68, 69, 70),
    "Chapter IV, tables 52 (pp. 51) and 53 (pp. 52-53); scan pages 68-70",
)
BULLETIN1928 = Volume(
    "bulletin1928-01",
    "Valsts statistiskā pārvalde",
    "Mēneša biļetens (Valsts statistiskā pārvalde), 1928, Nr. 1",
    "https://periodika.lv/periodika2-viewer/?lang=fr#issue:662414",
    PERIODIKA,
    662414,
    (23,),
    "Tab. 43, printed page 39; scan page 23",
)
BULLETIN1931 = Volume(
    "bulletin1931-09",
    "Valsts statistiskā pārvalde",
    "Mēneša biļetens (Valsts statistiskā pārvalde), 1931, Nr. 9",
    "https://periodika.lv/periodika2-viewer/?lang=fr#issue:662233",
    PERIODIKA,
    662233,
    (37,),
    "1931. gada 5./6. septembŗa tautas nobalsošanas rezultāti, printed page 646; scan page 37",
)
VOLUMES = (YEARBOOK, BULLETIN1928, BULLETIN1931)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def source_id(volume: Volume) -> str:
    return key("source", volume.key)


COUNTRY_UNIT = key("unit", "valsts")
ASSEMBLY = key("body", "satversmes-sapulce")
SYSTEM_ASSEMBLY = key("electoral_system", "satversmes-sapulce")
SYSTEM_REFERENDUM = key("electoral_system", "tautas-nobalsosana")

# The Constituent Assembly election, 17-18 April 1920.
ASSEMBLY_DAY = date(1920, 4, 17)
ASSEMBLY_LAST = date(1920, 4, 18)
ASSEMBLY_KEY = f"{SOURCE}/1920-04-17"


@dataclass(frozen=True)
class District:
    code: str
    native: str
    english: str
    seats: int


# Printed order of table 52.
DISTRICTS = (
    District("riga", "Rīgas apgabals", "Riga", 22),
    District("vidzeme", "Vidzemes apgabals", "Vidzeme", 37),
    District("kurzeme", "Kurzemes apgabals", "Kurzeme", 26),
    District("zemgale", "Zemgales apgabals", "Zemgale", 26),
    District("latgale", "Latgales apgabals", "Latgale", 39),
)


@dataclass(frozen=True)
class Turnout:
    """A row of table 52: registered voters, valid and invalid votes, and polling districts."""

    native: str
    registered: int
    valid: int
    invalid: int
    precincts: int


# Table 52, by county (apriņķis) in each district. The Riga district is the city of Riga and prints one row.
COUNTIES: dict[str, tuple[Turnout, ...]] = {
    "riga": (Turnout("Rīga", 101878, 85326, 360, 41),),
    "vidzeme": (
        Turnout("Rīgas apriņķis", 50101, 44387, 140, 60),
        Turnout("Cēsu apriņķis", 67583, 58142, 253, 65),
        Turnout("Valmieras apriņķis", 56071, 50320, 255, 63),
        Turnout("Valkas apriņķis", 56693, 48511, 197, 55),
    ),
    "kurzeme": (
        Turnout("Liepājas apriņķis", 45548, 39293, 286, 24),
        Turnout("Aizputes apriņķis", 22463, 20453, 96, 33),
        Turnout("Kuldīgas apriņķis", 27226, 23635, 108, 25),
        Turnout("Ventspils apriņķis", 23401, 19975, 81, 15),
        Turnout("Talsu apriņķis", 28448, 24773, 108, 34),
    ),
    "zemgale": (
        Turnout("Tukuma apriņķis", 20565, 18117, 56, 32),
        Turnout("Jelgavas apriņķis", 37106, 32242, 111, 34),
        Turnout("Bauskas apriņķis", 19547, 17329, 60, 23),
        Turnout("Jaunjelgavas apriņķis", 22340, 19601, 53, 24),
        Turnout("Ilūkstes apriņķis", 7912, 7133, 5, 9),
    ),
    "latgale": (
        Turnout("Daugavpils apriņķis", 79403, 62253, 125, 20),
        Turnout("Rēzeknes apriņķis", 67172, 51357, 93, 23),
        Turnout("Ludzas apriņķis", 65105, 51659, 191, 19),
    ),
}
# Table 52, district rows and the national row: registered, valid, invalid, polling districts.
DISTRICT_TURNOUT = {
    "riga": (101878, 85326, 360, 41),
    "vidzeme": (230448, 201360, 845, 243),
    "kurzeme": (147086, 128129, 679, 131),
    "zemgale": (107470, 94422, 285, 122),
    "latgale": (211680, 165269, 409, 62),
}
NATIONAL_TURNOUT = (798562, 674506, 2578, 599, 150)


@dataclass(frozen=True)
class List:
    native: str
    french: str
    kind: str
    # Votes and seats by district, in the columns' printed order: Riga, Zemgale, Kurzeme, Vidzeme, Latgale.
    # None where the table prints a dash for both (the list did not stand there).
    cells: tuple[tuple[int, int] | None, ...]
    total: tuple[int, int]


COLUMNS = ("riga", "zemgale", "kurzeme", "vidzeme", "latgale")
# Table 53, in printed order. Names are given as printed, in the 1920 spelling.
LISTS = (
    List(
        "Latvijas zocial-demokrat. strādneeku partija",
        "Parti des ouvriers démocrates-socialistes lettons",
        "party",
        ((32740, 8), (37340, 10), (77572, 15), (104575, 19), (22650, 5)),
        (274877, 57),
    ),
    List(
        "Zemneeku saveeniba",
        "Union des paysans",
        "party",
        (None, (32513, 9), (26949, 5), (66972, 12), None),
        (126434, 26),
    ),
    List(
        "Latgales zemneeku partija",
        "Parti des paysans de Latgale",
        "party",
        (None, None, None, None, (72961, 17)),
        (72961, 17),
    ),
    List(
        "Vācu partijas", "Partis des allemands", "bloc", ((18025, 4), (4266, 1), (6434, 1), (3531, 0), None), (32256, 6)
    ),
    List(
        "Demokratu saveeniba",
        "Union des démocrates lettons",
        "party",
        ((4387, 1), (4754, 1), (5123, 1), (10576, 2), (4822, 1)),
        (29662, 6),
    ),
    List(
        "Darba partija",
        "Parti de travail",
        "party",
        ((1225, 0), (1839, 1), (6383, 1), (6951, 1), (11719, 3)),
        (28117, 6),
    ),
    List(
        "Latgales kristigo zemneeku saveeniba",
        "Union des paysans-chrétiens de Latgale",
        "party",
        (None, None, None, None, (26534, 6)),
        (26534, 6),
    ),
    List(
        "Bezpartejiskā pilsoņu grupa",
        "Groupe de citoyens lettons sans parti",
        "independent_group",
        ((23867, 6), None, None, None, None),
        (23867, 6),
    ),
    List(
        "Kristigā nacionalā partija",
        "Parti letton des nationalistes-chrétiens",
        "party",
        ((1589, 0), (2623, 1), (6691, 1), (5315, 1), None),
        (16218, 3),
    ),
    List(
        "Bezzemneeku agrarā saveeniba",
        "Union lettone agraire des personnes sans terre",
        "party",
        ((161, 0), (7254, 2), (3463, 1), (3200, 0), None),
        (14078, 3),
    ),
    List(
        "Kreevu pilsoņu grupa",
        "Groupe des citoyens russes",
        "independent_group",
        ((2650, 1), None, None, None, (11001, 3)),
        (13651, 4),
    ),
    List("Ebreju bloks", "Bloc des juifs unis", "bloc", ((5673, 2), (1145, 1), (3656, 1), None, (2290, 1)), (12764, 5)),
    List(
        "Bezpartejiskee bezzemneeki",
        "Groupe letton de personnes sans terre n'appartenant à aucun parti",
        "independent_group",
        (None, (3079, 0), None, (8101, 2), None),
        (11180, 2),
    ),
    List(
        "Ceire Cion",
        "Fraction du peuple juifs «Zeire-Zion»",
        "party",
        ((850, 0), (718, 0), (808, 0), None, (5878, 1)),
        (8254, 1),
    ),
    List(
        "Latgales ļaužu partija", "Parti du peuple de Latgale", "party", (None, None, None, None, (6539, 1)), (6539, 1)
    ),
    List("Apveenotās poļu partijas", "Parti des Polonais unis", "bloc", (None, None, None, None, (5525, 1)), (5525, 1)),
    List(
        "Latvijas katoļu saveeniba",
        "Union des catholiques lettons",
        "party",
        (None, (2083, 0), None, None, None),
        (2083, 0),
    ),
    List(
        "Bezzemneeku un strādneeku grupa",
        "Groupe des personnes sans terre et des ouvriers",
        "independent_group",
        (None, None, None, (1588, 0), None),
        (1588, 0),
    ),
    List(
        "Kareivju kandidatu saraksts",
        "Liste des militaires-candidats",
        "list_vehicle",
        ((1497, 0), None, None, None, None),
        (1497, 0),
    ),
    List(
        "Latgales bezpartejiskā grupa",
        "Groupe du peuple sans parti en Latgale",
        "independent_group",
        (None, None, None, None, (1026, 0)),
        (1026, 0),
    ),
    List(
        "Revolucionaru-zocialistu saveeniba",
        "Parti letton des socialistes-révolutionaires",
        "party",
        ((462, 0), (262, 0), None, None, None),
        (724, 0),
    ),
    List("Seeveešu saveeniba", "Union des femmes lettones", "party", ((436, 0), None, None, None, None), (436, 0)),
    List("Agudas Israel", "Parti «Agudas Israel»", "party", (None, None, None, None, (424, 0)), (424, 0)),
    List(
        "Ludzas ebreji",
        "Liste des juifs-électeurs de Ludza",
        "list_vehicle",
        (None, None, None, None, (152, 0)),
        (152, 0),
    ),
    List(
        "Jēkabmeesta darba partija",
        "Groupe de travail de Jacobmêste",
        "party",
        (None, (87, 0), None, None, None),
        (87, 0),
    ),
)
# The "Kopā" row of table 53: votes and seats by district, then the national total.
LIST_TOTALS = ((93562, 22), (97963, 26), (137079, 26), (210809, 37), (171521, 39))
LIST_NATIONAL = (710934, 150)

ASSEMBLY_METHOD = (
    "Digitized from table 53 of the State Statistical Bureau's statistical yearbook for 1920 (chapter IV), which "
    "gives the votes and seats of each list in each of the five constituencies. The printed column total is stored "
    "as the constituency's valid votes. The list totals are higher than the valid votes table 52 gives for the "
    "counties in every constituency (710,934 against 674,506 for the whole country), and the yearbook does not "
    "explain the difference; the county figures are kept in a second result set. Every figure keeps its page and "
    "bounding box."
)
COUNTIES_METHOD = (
    "Digitized from table 52 of the State Statistical Bureau's statistical yearbook for 1920 (chapter IV), which "
    "gives the registered voters, valid votes, and invalid votes of each county (apriņķis), and of the city of Riga, "
    "without votes by list. The registered voters and invalid votes are stored, and the constituency rows of the "
    "table are kept as declared figures. The valid votes are not stored as values, because without votes by list "
    "they cannot be checked against them; by county they are: {valid}. Completeness is these valid votes divided by "
    "the list total of table 53. The percentages and the polling-district counts it prints are not stored. Every "
    "figure keeps its page and bounding box."
)
ASSEMBLY_SYSTEM = (
    "The 150 members of the Constituent Assembly (Satversmes Sapulce) were elected on 17-18 April 1920 in five "
    "constituencies by list proportional representation. The seats of each constituency are those table 52 prints. "
    "The yearbook does not state the allocation method, and the rules record a Hare quota with largest remainders "
    "as an approximation: it gives the printed seats in Kurzeme and Latgale, but not in Riga, Vidzeme, and Zemgale, "
    "where the differences fit lists joined for the count, which table 53 does not show. The seats are stored as "
    "printed."
)


# Referendums.
@dataclass(frozen=True)
class Answer:
    native: str
    english: str


YES = Answer("Par", "For")
NO = Answer("Pret", "Against")


@dataclass(frozen=True)
class Area:
    """A county row of a referendum table: ballots cast, then for, against, and invalid where printed."""

    code: str
    native: str
    cast: int
    yes: int | None = None
    no: int | None = None
    invalid: int | None = None


@dataclass(frozen=True)
class Referendum:
    day: date
    last: date
    english: str
    native: str
    question_english: str
    question_native: str
    question_slug: str
    type: str
    volume: Volume
    areas: tuple[Area, ...]
    regions: tuple[tuple[str, str, tuple[int, ...]], ...]  # region rows: code, name, (cast, yes, no, invalid)
    national: Area
    method: str


R1927 = Referendum(
    date(1927, 12, 17),
    date(1927, 12, 18),
    "1927 Latvian referendum on the citizenship law",
    "1927. gada 17./18. decembra tautas nobalsošana par pavalstniecības likumu",
    "The citizenship law (pavalstniecības likums)",
    "Pavalstniecības likums",
    "citizenship-law",
    "optional_referendum",
    BULLETIN1928,
    (
        Area("riga", "Rīgas (Rīga un Rīgas apriņķis)", 62761, 59348, 3038, 375),
        Area("cesis", "Cēsu apriņķis", 15826, 15073, 700, 53),
        Area("valmiera", "Valmieras apriņķis", 12222, 11203, 947, 72),
        Area("valka", "Valkas apriņķis", 15165, 14151, 961, 53),
        Area("madona", "Madonas apriņķis", 15895, 15043, 821, 31),
        Area("liepaja", "Liepājas apriņķis", 13478, 12182, 1192, 104),
        Area("aizpute", "Aizputes apriņķis", 4182, 3858, 309, 15),
        Area("kuldiga", "Kuldīgas apriņķis", 5862, 5325, 532, 5),
        Area("ventspils", "Ventspils apriņķis", 5930, 5524, 397, 9),
        Area("talsi", "Talsu apriņķis", 4868, 4497, 364, 7),
        Area("tukums", "Tukuma apriņķis", 6729, 6355, 368, 6),
        Area("jelgava", "Jelgavas apriņķis", 15450, 14261, 1119, 70),
        Area("bauska", "Bauskas apriņķis", 7072, 6528, 527, 17),
        Area("jekabpils", "Jēkabpils apriņķis", 6793, 6263, 509, 21),
        Area("ilukste", "Ilūkstes apriņķis", 4712, 4162, 531, 19),
        Area("daugavpils", "Daugavpils apriņķis", 18470, 16486, 1872, 112),
        Area("rezekne", "Rēzeknes apriņķis", 9927, 8738, 1163, 26),
        Area("ludza", "Ludzas apriņķis", 5853, 5227, 609, 17),
        Area("jaunlatgale", "Jaunlatgales apriņķis", 12627, 11748, 867, 12),
    ),
    (
        ("vidzeme", "Vidzeme", (121869, 114818, 6467, 584)),
        ("kurzeme", "Kurzeme", (34320, 31386, 2794, 140)),
        ("zemgale", "Zemgale", (40756, 37569, 3054, 133)),
        ("latgale", "Latgale", (46877, 42199, 4511, 167)),
    ),
    Area("valsts", "Latvija", 243822, 225972, 16826, 1024),
    "Digitized from Tab. 43 of the State Statistical Bureau's monthly bulletin for January 1928, which gives for "
    "each county the voters who took part, the votes for and against, and the invalid votes. The row headed "
    "'Rīgas' covers the city of Riga and Riga county together: its initiative signatures (52,008) are the sum of "
    "the two in the bulletin's table of the signature collection. The counties add up to the bulletin's totals for "
    "the four regions (Vidzeme, Kurzeme, Zemgale, Latgale), and its totals for Latvia are kept as declared figures. The table also prints the "
    "number of municipalities, the initiative signatures, and two percentages; they are not stored. Every figure "
    "keeps its page and bounding box.",
)
R1931 = Referendum(
    date(1931, 9, 5),
    date(1931, 9, 6),
    "1931 Latvian referendum on the St. Mary's Church bill",
    "1931. gada 5./6. septembra tautas nobalsošana par likumprojektu «Likums par Māras baznīcu»",
    "Bill «Likums par Māras baznīcu» (the law on St. Mary's Church)",
    "Likumprojekts «Likums par Māras baznīcu»",
    "st-marys-church",
    "initiative",
    BULLETIN1931,
    (
        Area("riga-city", "Rīgas pilsēta", 96205),
        Area("riga", "Rīgas apriņķis", 38917),
        Area("cesis", "Cēsu apriņķis", 19015),
        Area("valmiera", "Valmieras apriņķis", 20673),
        Area("valka", "Valkas apriņķis", 19712),
        Area("madona", "Madonas apriņķis", 18943),
        Area("liepaja", "Liepājas apriņķis", 27038),
        Area("aizpute", "Aizputes apriņķis", 5513),
        Area("kuldiga", "Kuldīgas apriņķis", 11078),
        Area("ventspils", "Ventspils apriņķis", 11853),
        Area("talsi", "Talsu apriņķis", 9660),
        Area("tukums", "Tukuma apriņķis", 10525),
        Area("jelgava", "Jelgavas apriņķis", 25484),
        Area("bauska", "Bauskas apriņķis", 9983),
        Area("jekabpils", "Jēkabpils apriņķis", 12245),
        Area("ilukste", "Ilūkstes apriņķis", 6338),
        Area("daugavpils", "Daugavpils apriņķis", 19400),
        Area("rezekne", "Rēzeknes apriņķis", 9900),
        Area("ludza", "Ludzas apriņķis", 5629),
        Area("jaunlatgale", "Jaunlatgales apriņķis", 12049),
    ),
    (),
    Area("valsts", "Latvija", 390160, 380136, 8244, 1780),
    "Digitized from the State Statistical Bureau's monthly bulletin for September 1931, which prints a summary of "
    "the telephone and telegraph reports of the municipalities ('Telefonisko un telegrafisko ziņojumu savilkums'). "
    "It gives the voters who took part in each county and in the city of Riga, and for the whole country the votes "
    "for and against the bill and the invalid votes. The county figures add up to the national 390,160. The "
    "percentages of the electorate it prints are not stored. Every figure keeps its page and bounding box.",
)
R1923 = Referendum(
    date(1923, 9, 1),
    date(1923, 9, 2),
    "1923 Latvian referendum on St. James's Church",
    "1923. gada tautas nobalsošana par Jēkaba baznīcu",
    "St. James's Church (Jēkaba baznīca)",
    "Par Jēkaba baznīcu",
    "st-james-church",
    "optional_referendum",
    BULLETIN1931,
    (),
    (),
    Area("valsts", "Latvija", 206908),
    "The State Statistical Bureau's monthly bulletin for September 1931 recalls, in its report on the referendum "
    "of that month, that the first referendum was held in 1923 on St. James's Church ('par Jēkaba baznīcu') and "
    "that 206,908 voters took part in it. That is the only figure stored; the votes for and against are not "
    "printed there. The figure keeps its page and bounding box.",
)
REFERENDUMS = (R1923, R1927, R1931)
REFERENDUM_SYSTEM = (
    "A national referendum under articles 72-80 of the Satversme of 1922. A law submitted to the people is adopted "
    "only if at least half of the voters who took part in the last Saeima election vote in the referendum."
)


def referendum_key(vote: Referendum) -> str:
    return f"{SOURCE}/{vote.day.isoformat()}"


def packages() -> list[str]:
    return [REGISTRY, ASSEMBLY_KEY, *(referendum_key(vote) for vote in REFERENDUMS)]


def urls(volume: Volume) -> list[str]:
    return [page_url(volume.host, volume.issue, number) for number in volume.pages]


def fetch() -> None:
    for volume in VOLUMES:
        for url in urls(volume):
            raw.fetch(SOURCE, url, suffix=".jpg")


def check() -> None:
    """Check every transcribed figure against the totals printed beside it."""
    for district in DISTRICTS:
        rows = COUNTIES[district.code]
        sums = (sum(r.registered for r in rows), sum(r.valid for r in rows), sum(r.invalid for r in rows))
        sums += (sum(r.precincts for r in rows),)
        if sums != DISTRICT_TURNOUT[district.code]:
            raise ValueError(f"table 52, {district.code}: counties add up to {sums}")
    columns = list(zip(*DISTRICT_TURNOUT.values(), strict=True))
    if tuple(sum(c) for c in columns) + (sum(d.seats for d in DISTRICTS),) != NATIONAL_TURNOUT:
        raise ValueError("table 52: districts do not add up to the national row")
    for index, (votes, seats) in enumerate(LIST_TOTALS):
        cells = [cell for row in LISTS if (cell := row.cells[index]) is not None]
        if (sum(c[0] for c in cells), sum(c[1] for c in cells)) != (votes, seats):
            raise ValueError(f"table 53, column {COLUMNS[index]}: lists do not add up to the printed total")
        if seats != next(d.seats for d in DISTRICTS if d.code == COLUMNS[index]):
            raise ValueError(f"table 53, column {COLUMNS[index]}: seats differ from table 52")
    for row in LISTS:
        cells = [c for c in row.cells if c is not None]
        if (sum(c[0] for c in cells), sum(c[1] for c in cells)) != row.total:
            raise ValueError(f"table 53, {row.native}: districts do not add up to the printed total")
    if (sum(v for v, _ in LIST_TOTALS), sum(s for _, s in LIST_TOTALS)) != LIST_NATIONAL:
        raise ValueError("table 53: columns do not add up to the national total")
    for vote in REFERENDUMS:
        for area in vote.areas:
            if area.yes is not None and area.cast != area.yes + (area.no or 0) + (area.invalid or 0):
                raise ValueError(f"{vote.day}, {area.code}: for, against, and invalid do not add up to the voters")
        total = vote.national
        if not vote.areas:
            continue
        if sum(a.cast for a in vote.areas) != total.cast:
            raise ValueError(f"{vote.day}: counties do not add up to the national voters")
        if total.cast != (total.yes or 0) + (total.no or 0) + (total.invalid or 0):
            raise ValueError(f"{vote.day}: national for, against, and invalid do not add up to the voters")
        if vote.areas[0].yes is not None:
            for field in ("yes", "no", "invalid"):
                if sum(getattr(a, field) or 0 for a in vote.areas) != getattr(total, field):
                    raise ValueError(f"{vote.day}: counties do not add up to the national {field}")
        bounds = [0, 5, 10, 15, 19]
        for (code, _, figures), start, end in zip(vote.regions, bounds, bounds[1:], strict=False):
            part = vote.areas[start:end]
            summed = (
                sum(a.cast for a in part),
                sum(a.yes or 0 for a in part),
                sum(a.no or 0 for a in part),
                sum(a.invalid or 0 for a in part),
            )
            if summed != figures:
                raise ValueError(f"{vote.day}, {code}: counties add up to {summed}, not {figures}")


def parse() -> None:
    check()
    for key in packages():
        build(key).write()


def emit() -> None:
    package.run("load", packages())


def build(name: str) -> Package:
    if name == REGISTRY:
        return registry()
    if name == ASSEMBLY_KEY:
        return assembly()
    return referendum(next(vote for vote in REFERENDUMS if referendum_key(vote) == name))


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The State Statistical Bureau's interwar election volumes, the Constituent Assembly, the interwar constituency "
        "and county schemes, and the lists of the 1920 election",
    )
    p.external.add(LATVIA)
    for volume in VOLUMES:
        p.add(
            "source",
            id=source_id(volume),
            kind="official_body",
            publisher=volume.publisher,
            title=volume.title,
            url=volume.url,
            license=UNLICENSED,
            attribution="Valsts statistiskā pārvalde; scans by the Latvijas Nacionālā bibliotēka",
            notes=volume.locator,
        )
    p.add(
        "scheme",
        code=DISTRICTS_SCHEME,
        polity_id=LATVIA,
        kind="electoral_district",
        rank=1,
        noun="constituency (1920-1934)",
    )
    p.add("scheme", code=NATIONAL_SCHEME, polity_id=LATVIA, kind="administrative", rank=0, noun="country (1920-1940)")
    p.add("scheme", code=COUNTIES_SCHEME, polity_id=LATVIA, kind="administrative", rank=2, noun="county (apriņķis)")
    p.add("body", id=ASSEMBLY, polity_id=LATVIA, kind="constituent_assembly")
    p.name(ASSEMBLY, "Constituent Assembly of Latvia", "en", "official")
    p.name(ASSEMBLY, "Latvijas Satversmes Sapulce", "lv", "native", False)
    p.add("electoral_system", id=SYSTEM_ASSEMBLY, polity_id=LATVIA, rules={"formula": "hare"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_ASSEMBLY),
        entity_id=SYSTEM_ASSEMBLY,
        kind="methodology",
        body_md=ASSEMBLY_SYSTEM,
        lang="en",
    )
    p.add("electoral_system", id=SYSTEM_REFERENDUM, polity_id=LATVIA, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_REFERENDUM),
        entity_id=SYSTEM_REFERENDUM,
        kind="methodology",
        body_md=REFERENDUM_SYSTEM,
        lang="en",
    )
    for row in LISTS:
        party = key("party", slug(row.native))
        p.add("party", id=party, kind=row.kind, polity_id=LATVIA)
        p.name(party, row.native, "lv", "official")
        p.name(party, row.french, "fr", "official", False)
    return p


def pages(p: Package, volume: Volume) -> dict[int, str]:
    out: dict[int, str] = {}
    source = source_id(volume)
    for number, url in zip(volume.pages, urls(volume), strict=True):
        stored = raw.cached(SOURCE, url)
        artifact = p.artifact(stored, source)
        width, height = size(stored.path)
        out[number] = key("page", volume.key, number)
        p.add(
            "source_page",
            id=out[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.path.name}",
            width=width,
            height=height,
        )
    return out


def size(path: Path) -> tuple[int, int]:
    """The pixel size of a baseline JPEG, read from its SOF0 marker."""
    data = path.read_bytes()
    at = 2
    while at < len(data):
        if data[at] != 0xFF:
            raise ValueError(f"{path.name}: not a JPEG marker at {at}")
        marker = data[at + 1]
        length = int.from_bytes(data[at + 2 : at + 4])
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[at + 7 : at + 9]), int.from_bytes(data[at + 5 : at + 7])
        at += 2 + length
    raise ValueError(f"{path.name}: no frame header")


class Facts:
    def __init__(self, boxes: dict[str, list[object]], pages: dict[int, str]) -> None:
        self.boxes = boxes
        self.pages = pages
        self.values: list[tuple[str, str, str | None, int, int]] = []
        self.locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []

    def put(self, where: str, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        box = self.boxes[where]
        page, x0, y0, x1, y1 = (int(str(box[0])), *(float(str(b)) for b in box[1:]))
        self.values.append((result_set, unit, option, measure, value))
        self.locators.append((result_set, unit, option, measure, self.pages[page], x0, y0, x1, y1))

    def stage(self, p: Package) -> None:
        p.frame(
            "result_value",
            pl.DataFrame(
                self.values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(DIGITIZED, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
        p.frame(
            "value_locator",
            pl.DataFrame(
                self.locators,
                schema=["result_set_id", "unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )


def unit(p: Package, code: str, scheme: str, name: str, kind: str = "standard", parent: str | None = None) -> str:
    id = key("unit", code)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=LATVIA,
        scheme_code=scheme,
        code=code,
        parent_id=parent,
        area_relation="unlocated",
        area_id=None,
        kind=kind,
    )
    p.name(id, name, "lv", "official")
    return id


def assembly() -> Package:
    p = Package(
        ASSEMBLY_KEY,
        SOURCE,
        "The Constituent Assembly election of 17-18 April 1920 by constituency and county, digitized from the State "
        "Statistical Bureau's yearbook",
        depends=[REGISTRY],
    )
    p.external.add(LATVIA)
    boxes = json.loads(BOXES.read_text())["1920"]
    facts = Facts(boxes, pages(p, YEARBOOK))
    source = source_id(YEARBOOK)
    election = key("election", "1920-04-17")
    where = "lv/1920-04-17-constituent-assembly"
    p.add(
        "election",
        id=election,
        polity_id=LATVIA,
        kind="general",
        date_from=ASSEMBLY_DAY,
        date_to=ASSEMBLY_LAST,
        date_precision="day",
        status="held",
    )
    p.name(election, "1920 Latvian Constitutional Assembly election", "en", "official")
    p.name(election, "1920. gada Satversmes Sapulces vēlēšanas", "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    for district in DISTRICTS:
        column = COLUMNS.index(district.code)
        contest = key("contest", "1920", district.code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=ASSEMBLY,
            magnitude=district.seats,
            round_number=1,
            electoral_system_id=SYSTEM_ASSEMBLY,
            default_result_kind="counted",
        )
        p.name(contest, f"1920 Constituent Assembly election, {district.english}", "en", "official")
        p.name(contest, f"1920. gada Satversmes Sapulces vēlēšanas, {district.native}", "lv", "native", False)
        p.add("slug", path=f"{where}/{district.code}", entity_id=contest, is_current=True)
        result_set = key("result_set", "1920", district.code)
        counties = key("result_set", "1920", district.code, "counties")
        rows = COUNTIES[district.code]
        valid = LIST_TOTALS[column][0]
        for id, default, completeness, method in (
            (result_set, True, 1.0, ASSEMBLY_METHOD),
            (
                counties,
                False,
                round(sum(r.valid for r in rows) / valid, 6),
                COUNTIES_METHOD.format(valid="; ".join(f"{r.native} {r.valid:,}" for r in rows)),
            ),
        ):
            p.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=default,
                completeness=completeness,
            )
            p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
        top = unit(p, f"1920:{district.code}", DISTRICTS_SCHEME, district.native)
        for position, row in enumerate(LISTS, 1):
            cell = row.cells[column]
            if cell is None:
                continue
            votes, seats = cell
            option = key("option", "1920", district.code, slug(row.native))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=key("party", slug(row.native)),
                ballot_position=position,
                ballot_label=row.native,
            )
            facts.put(f"t53/{slug(row.native)}/{district.code}", result_set, top, option, VOTES, votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
        facts.put(f"t53/total/{district.code}", result_set, top, None, VALID, valid)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)
        registered, _, invalid, _ = DISTRICT_TURNOUT[district.code]
        for measure, value in ((REGISTERED, registered), (INVALID, invalid)):
            p.add("declared", result_set_id=counties, option_id=None, measure=measure, value=value)
        for row in rows:
            code = f"1920:{district.code}:{slug(row.native)}"
            county = unit(p, code, COUNTIES_SCHEME, row.native, parent=top)
            here = f"t52/{slug(row.native)}"
            facts.put(f"{here}/registered", counties, county, None, REGISTERED, row.registered)
            facts.put(f"{here}/invalid", counties, county, None, INVALID, row.invalid)
    facts.stage(p)
    return p


def referendum(vote: Referendum) -> Package:
    day = vote.day.isoformat()
    p = Package(
        referendum_key(vote),
        SOURCE,
        f"The {vote.english}{' by county' if vote.areas else ''}, digitized from the State Statistical Bureau's "
        "monthly bulletin",
        depends=[REGISTRY],
    )
    p.external.add(LATVIA)
    boxes = json.loads(BOXES.read_text())[day]
    facts = Facts(boxes, pages(p, vote.volume))
    election = key("election", day)
    where = f"lv/{day}-referendum"
    p.add(
        "election",
        id=election,
        polity_id=LATVIA,
        kind="referendum",
        date_from=vote.day,
        date_to=vote.last,
        date_precision="day",
        status="held",
    )
    p.name(election, vote.english, "en", "official")
    p.name(election, vote.native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        round_number=1,
        electoral_system_id=SYSTEM_REFERENDUM,
        default_result_kind="counted",
    )
    p.name(contest, vote.question_english, "en", "official")
    p.name(contest, vote.question_native, "lv", "native", False)
    p.add("slug", path=f"{where}/{vote.question_slug}", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type=vote.type)
    options = []
    for position, answer in enumerate((YES, NO), 1):
        option = key("option", day, slug(answer.english))
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=f"{answer.native} ({answer.english})",
        )
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source_id(vote.volume),
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=vote.method, lang="en")
    counties = result_set
    total = vote.national
    if not vote.areas:
        nation = unit(p, f"{day}:valsts", NATIONAL_SCHEME, "Latvija", "aggregate")
        facts.put("valsts/cast", result_set, nation, None, CAST, total.cast)
    elif vote.areas[0].yes is None:
        # Only the national row has the votes for and against: it is the default set, and the counties' voters
        # go in a second one.
        nation = unit(p, f"{day}:valsts", NATIONAL_SCHEME, "Latvija", "aggregate")
        for field, option, measure in (
            ("cast", None, CAST),
            ("yes", options[0], VOTES),
            ("no", options[1], VOTES),
            ("invalid", None, INVALID),
        ):
            facts.put(f"valsts/{field}", result_set, nation, option, measure, getattr(total, field))
        counties = key("result_set", day, "counties")
        p.add(
            "result_set",
            id=counties,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(vote.volume),
            status="active",
            is_default=False,
            completeness=1.0,
        )
        p.add("note", id=key("note", counties), entity_id=counties, kind="methodology", body_md=vote.method, lang="en")
        p.add("declared", result_set_id=counties, option_id=None, measure=CAST, value=total.cast)
    for area in vote.areas:
        here = unit(p, f"{day}:{area.code}", COUNTIES_SCHEME, area.native)
        facts.put(f"{area.code}/cast", counties, here, None, CAST, area.cast)
        for field, option, measure in (
            ("yes", options[0], VOTES),
            ("no", options[1], VOTES),
            ("invalid", None, INVALID),
        ):
            value = getattr(area, field)
            if value is not None:
                facts.put(f"{area.code}/{field}", counties, here, option, measure, value)
    for option, measure, value in (
        (None, CAST, total.cast),
        (options[0], VOTES, total.yes),
        (options[1], VOTES, total.no),
        (None, INVALID, total.invalid),
    ):
        if value is None:
            continue
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    facts.stage(p)
    return p
