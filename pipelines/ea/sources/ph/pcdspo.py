"""The Philippines' national results of 1935 to 1998 from the Philippine Electoral Almanac of the Presidential
Communications Development and Strategic Planning Office (PCDSPO): every presidential and vice-presidential election,
and every national plebiscite and referendum.

The almanac prints each presidential and vice-presidential election as a map of the province each candidate carried
and a legend of national vote shares, and gives the winner's vote count in its timeline of the presidency. It prints
each plebiscite's national votes for each answer. Both editions are typeset with a text layer; every figure here is
read from the revised and expanded edition (2015), located on its page by the word's box, and checked against the
first edition (2012). Below the nation the almanac prints only the colour of the candidate who carried each province,
so no provincial figure is stored, and for the Senate it prints only the seats each party won.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from html import unescape
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "ph.pcdspo"
PHILIPPINES = str(uuid5(package.NAMESPACE, "polity\x1fPH"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPHL"))
SCHEME = "ph.nation"
VOTES, VOTE_SHARE = 1, 13
SHARES = 4  # result_value.flags: a share the source prints without the count

ARCHIVE = "https://archive.org/download"
REVISED = f"{ARCHIVE}/philippine-electoral-almanac-revised-and-expanded/Philippine%20Electoral%20Almanac%20Revised%20and%20Expanded.pdf"
FIRST = f"{ARCHIVE}/ThePhilippineElectoralAlmanac1stEdition/Electoral%20Almanac%201st%20Edition.pdf"
PUBLISHER = (
    "Presidential Communications Development and Strategic Planning Office (PCDSPO), Republic of the Philippines"
)
LICENSE = (
    "A work of the Government of the Philippines, in which no copyright subsists (Republic Act No. 8293, section 176); "
    "the Internet Archive copy of the first edition is marked CC BY 4.0 and of the revised edition CC BY-NC 4.0"
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


REVISED_SOURCE = key("source", "revised")
FIRST_SOURCE = key("source", "first")
PRESIDENT = key("office", "president")
VICE_PRESIDENT = key("office", "vice president")
PLURALITY = key("electoral_system", "plurality")
REFERENDUM = key("electoral_system", "referendum")


def fetch() -> None:
    for url in (REVISED, FIRST):
        raw.fetch(SOURCE, url, suffix=".pdf")


# ---------------------------------------------------------------------------------------------------------------
# Word boxes


@dataclass(frozen=True)
class Word:
    page: int
    text: str
    box: tuple[float, float, float, float]


WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')


@cache
def _page(path: Path, page: int) -> tuple[tuple[float, float], list[Word]]:
    """Every word on a page with its box in points from the page's top left, and the page's size in points."""
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    size = PAGE.search(out)
    if size is None:
        raise ValueError(f"{path.name} has no page {page}")
    words = [
        Word(page, unescape(m.group(5)), (float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))))
        for m in WORD.finditer(out)
    ]
    return (float(size.group(1)), float(size.group(2))), words


def revised() -> Path:
    return raw.cached(SOURCE, REVISED).path


def first() -> Path:
    return raw.cached(SOURCE, FIRST).path


def word(page: int, text: str) -> Word:
    """The one word on a page of the revised edition that prints `text`."""
    found = [w for w in _page(revised(), page)[1] if w.text == text]
    if len(found) != 1:
        raise ValueError(f"page {page}: {len(found)} words print {text!r}")
    return found[0]


@cache
def _first_words() -> frozenset[str]:
    out = subprocess.run(["pdftotext", "-raw", str(first()), "-"], capture_output=True, text=True, check=True).stdout
    return frozenset(out.split())


def in_first(text: str) -> bool:
    return text in _first_words()


def count(text: str) -> int:
    return int(text.rstrip("%").replace(",", ""))


def share(text: str) -> Decimal:
    return Decimal(text.rstrip("%"))


# ---------------------------------------------------------------------------------------------------------------
# Parties

PARTIES = {
    "nacionalista": ("Nacionalista Party", "NP"),
    "national-socialist": ("National Socialist Party", None),
    "republican": ("Republican Party", None),
    "popular-front": ("Popular Front", None),
    "np-liberal-wing": ("Nacionalista Party (Liberal Wing)", None),
    "modernist": ("Modernist Party", None),
    "lp-quirino-wing": ("Liberal Party (Quirino Wing)", None),
    "lp-avelino-wing": ("Liberal Party (Avelino Wing)", None),
    "liberal": ("Liberal Party", "LP"),
    "progressive": ("Progressive Party of the Philippines", "PPP"),
    "nationalist-citizens": ("Nationalist Citizens' Party", "NCP"),
    "lapiang-malaya": ("Lapiang Malaya", None),
    "federal": ("Federal Party", None),
    "philippine-progress": ("Party for Philippine Progress", "PPP"),
    "kbl": ("Kilusang Bagong Lipunan", "KBL"),
    "unido": ("United Nationalist Democratic Organization", "UNIDO"),
    "mindanao-alliance": ("Mindanao Alliance", None),
    "lp-kalaw-wing": ("Liberal Party (Kalaw Wing)", None),
    "lakas-nucd": ("Lakas-NUCD", None),
    "prp": ("People's Reform Party", "PRP"),
    "npc": ("Nationalist People's Coalition", "NPC"),
    "ldp": ("Laban ng Demokratikong Pilipino", "LDP"),
    "pmp": ("Partido ng Masang Pilipino", "PMP"),
    "aksyon": ("Aksyon Demokratiko", None),
    "promdi": ("Probinsya Muna Development Initiative", "PROMDI"),
    "reporma": ("Partido para sa Demokratikong Reporma–Lapiang Manggagawa", "Reporma-LM"),
    "kpp": ("Kilusan para sa Pambansang Pagpapanibago", None),
    "bansang-marangal": ("Partido Bansang Marangal", None),
}
IND = None  # an independent candidate


# ---------------------------------------------------------------------------------------------------------------
# Presidential and vice-presidential elections


@dataclass(frozen=True)
class Line:
    name: str  # the candidate as the archive names them; "" for the almanac's line of other candidates
    party: str | None  # a key of PARTIES, or None for an independent
    label: str  # the legend's line as printed
    share: str  # the share as printed
    votes: tuple[int, str] | None = None  # the page of the timeline that prints the winner's votes, and the figure


@dataclass(frozen=True)
class Race:
    office: str  # "president" or "vice-president"
    page: int
    cites: str  # the source the almanac names under the map
    lines: tuple[Line, ...]
    stage: str = "official"
    note: str = ""
    completeness: float | None = None


@dataclass(frozen=True)
class General:
    day: date
    name: str
    races: tuple[Race, ...]


def L(name: str, party: str | None, label: str, share_: str, votes: tuple[int, str] | None = None) -> Line:
    return Line(name, party, label, share_, votes)


FREE_PRESS_1935 = "Philippines Free Press (September 21, 1935)"
TRIBUNE_1941 = "The Tribune (November 13, 1941)"
TIMES_1946 = "The Manila Times (April 29, 1946)"
TIMES_1949 = "The Manila Times (November 10-15, 19, 22, and 28, 1949); The Manila Chronicle (November 12, 1949)"
CONGRESS_1953 = "Joint Special Session, Second Congress, December 8, 1953"
COMELEC = "Commission on Elections, Report of the Commission on Elections to the President of the Philippines and the Congress on the Manner the Elections were held"
CONGRESS_1965 = "Congressional Record Vol. 4 No. 1, December 14, 1965"
BATASAN_1981 = "Resolution No. 2, Batasang Pambansa, Special Session, June 21, 1981"
TIMES_1986 = "The Manila Times (February 25, 1986)"
HOUSE = "House of Representatives, Bills and Index Division, Tally Sheet-Certificates of Canvass for the {} Election for President and Vice President"

GENERALS = (
    General(
        date(1935, 9, 16),
        "1935 Philippine general election",
        (
            Race(
                "president",
                74,
                FREE_PRESS_1935,
                (
                    L(
                        "Manuel L. Quezon",
                        "nacionalista",
                        "MANUEL L. QUEZON (NACIONALISTA)",
                        "67.99%",
                        (223, "695,332"),
                    ),
                    L("Emilio Aguinaldo", "national-socialist", "EMILIO AGUINALDO (NATIONAL SOCIALIST)", "17.54%"),
                    L("Gregorio Aglipay", "republican", "GREGORIO AGLIPAY (REPUBLICAN)", "14.47%"),
                ),
            ),
            Race(
                "vice-president",
                75,
                FREE_PRESS_1935,
                (
                    L("Sergio Osmeña", "nacionalista", "SERGIO OSMEÑA (NACIONALISTA)", "87%"),
                    L("Raymundo Melliza", "national-socialist", "RAYMUNDO MELLIZA (NATIONAL SOCIALIST)", "8%"),
                    L("Norberto Nabong", "republican", "NORBERTO NABONG (REPUBLICAN)", "5%"),
                ),
            ),
        ),
    ),
    General(
        date(1941, 11, 11),
        "1941 Philippine general election",
        (
            Race(
                "president",
                82,
                TRIBUNE_1941,
                (
                    L("Manuel L. Quezon", "nacionalista", "MANUEL L. QUEZON (NP)", "81.78%", (223, "1,340,638")),
                    L("Juan Sumulong", "popular-front", "JUAN SUMULONG (POPULAR FRONT)", "18.22%"),
                ),
            ),
            Race(
                "vice-president",
                83,
                TRIBUNE_1941,
                (
                    L("Sergio Osmeña", "nacionalista", "SERGIO OSMEÑA (NP)", "92%"),
                    L("Emilio Javier", "popular-front", "EMILIO JAVIER (POPULAR FRONT)", "8%"),
                ),
            ),
        ),
    ),
    General(
        date(1946, 4, 23),
        "1946 Philippine general election",
        (
            Race(
                "president",
                94,
                TIMES_1946,
                (
                    L("Manuel Roxas", "np-liberal-wing", "MANUEL ROXAS NP (LIBERAL WING)", "54%", (226, "1,333,392")),
                    L("Sergio Osmeña", "nacionalista", "SERGIO OSMEÑA (NP)", "45.7%%"),
                    L("Hilario Moncado", "modernist", "HILARIO MONCADO (MODERNIST)", "0.3%"),
                ),
            ),
            Race(
                "vice-president",
                95,
                TIMES_1946,
                (
                    L("Elpidio Quirino", "np-liberal-wing", "ELPIDIO QUIRINO NP (LIBERAL WING)", "52.4%"),
                    L("Eulogio Rodriguez", "nacionalista", "EULOGIO RODRIGUEZ (NP)", "47.4%"),
                    L("Luis Salvador", "modernist", "LUIS SALVADOR (MODERNIST)", "0.2%"),
                ),
            ),
        ),
    ),
    General(
        date(1949, 11, 8),
        "1949 Philippine general election",
        (
            Race(
                "president",
                100,
                TIMES_1949,
                (
                    L(
                        "Elpidio Quirino",
                        "lp-quirino-wing",
                        "ELPIDIO QUIRINO LP (QUIRINO WING)",
                        "50.93%",
                        (227, "1,803,808"),
                    ),
                    L("Jose P. Laurel", "nacionalista", "JOSE P. LAUREL (NP)", "37.22%"),
                    L("Jose Avelino", "lp-avelino-wing", "JOSE AVELINO LP (AVELINO WING)", "11.85%"),
                ),
            ),
            Race(
                "vice-president",
                101,
                TIMES_1949,
                (
                    L("Fernando Lopez", "lp-quirino-wing", "FERNANDO LOPEZ LP (QUIRINO WING)", "52.2%"),
                    L("Manuel Briones", "nacionalista", "MANUEL BRIONES (NP)", "46.1%"),
                    L("Vicente J. Francisco", "lp-avelino-wing", "VICENTE J. FRANCISCO LP (AVELINO WING)", "1.7%"),
                ),
            ),
        ),
    ),
    General(
        date(1953, 11, 10),
        "1953 Philippine general election",
        (
            Race(
                "president",
                106,
                CONGRESS_1953,
                (
                    L("Ramon Magsaysay", "nacionalista", "RAMON MAGSAYSAY (NP)", "68.9%", (228, "2,912,992")),
                    L("Elpidio Quirino", "liberal", "ELPIDIO QUIRINO (LP)", "31.08%"),
                    L("Gaudencio Bueno", IND, "GAUDENCIO BUENO (IND.)", "0.02%"),
                ),
            ),
            Race(
                "vice-president",
                107,
                CONGRESS_1953,
                (
                    L("Carlos P. Garcia", "nacionalista", "CARLOS P. GARCIA (NP)", "62.9%"),
                    L("Jose Yulo", "liberal", "JOSE YULO (LP)", "37.1%"),
                ),
            ),
        ),
    ),
    General(
        date(1957, 11, 12),
        "1957 Philippine general election",
        (
            Race(
                "president",
                111,
                f"{COMELEC} (November 12, 1957)",
                (
                    L("Carlos P. Garcia", "nacionalista", "CARLOS P. GARCIA (NP)", "41.3%", (229, "2,072,257")),
                    L("Jose Yulo", "liberal", "JOSE YULO (LP)", "27.6%"),
                    L("Manuel Manahan", "progressive", "MANUEL MANAHAN (PPP)", "20.9%"),
                    L("Claro M. Recto", "nationalist-citizens", "CLARO M. RECTO (NCP)", "8.55%"),
                    L("Antonio Quirino", "lp-quirino-wing", "ANTONIO QUIRINO LP (QUIRINO WING)", "1.2%"),
                    L("Valentin Santos", "lapiang-malaya", "VALENTIN SANTOS (LAPIANG MALAYA)", "0.43%"),
                    L("Alfredo Abcede", "federal", "ALFREDO ABCEDE (FEDERAL PARTY)", "0.01%"),
                ),
            ),
            Race(
                "vice-president",
                112,
                f"{COMELEC} (November 12, 1957)",
                (
                    L("Diosdado Macapagal", "liberal", "DIOSDADO MACAPAGAL (LP)", "46.6%"),
                    L("Jose B. Laurel Jr.", "nacionalista", "JOSE B. LAUREL JR. (NP)", "37.9%"),
                    L("Vicente Araneta", "progressive", "VICENTE ARANETA (PPP)", "8%"),
                    L("Lorenzo Tañada", "nationalist-citizens", "LORENZO TAÑADA (NCP)", "7.3%"),
                    L("Restituto Fresto", "lapiang-malaya", "RESTITUTO FRESTO (LAPIANG MALAYA)", "0.2%"),
                ),
            ),
        ),
    ),
    General(
        date(1961, 11, 14),
        "1961 Philippine general election",
        (
            Race(
                "president",
                116,
                f"{COMELEC} (November 15, 1961)",
                (
                    L("Diosdado Macapagal", "liberal", "DIOSDADO MACAPAGAL (LP)", "55.05%", (230, "3,554,840")),
                    L("Carlos P. Garcia", "nacionalista", "CARLOS P. GARCIA (NP)", "44.95%"),
                ),
            ),
            Race(
                "vice-president",
                117,
                f"{COMELEC} (November 15, 1961); The Manila Chronicle (November 17, 1961)",
                (
                    L("Emmanuel Pelaez", "liberal", "EMMANUEL PELAEZ (LP)", "38%"),
                    L("Sergio Osmeña Jr.", IND, "SERGIO OSMEÑA JR. (IND.)", "34%"),
                    L("Gil Puyat", "nacionalista", "GIL PUYAT (NP)", "28%"),
                ),
            ),
        ),
    ),
    General(
        date(1965, 11, 9),
        "1965 Philippine general election",
        (
            Race(
                "president",
                122,
                CONGRESS_1965,
                (
                    L("Ferdinand E. Marcos", "nacionalista", "FERDINAND E. MARCOS (NP)", "51.94%", (231, "3,861,324")),
                    L("Diosdado Macapagal", "liberal", "DIOSDADO MACAPAGAL (LP)", "42.88%"),
                    L("Raul Manglapus", "philippine-progress", "RAUL MANGLAPUS (PPP)", "5.17%"),
                ),
            ),
            Race(
                "vice-president",
                123,
                f"{CONGRESS_1965}; The Manila Chronicle (November 19, 1965)",
                (
                    L("Fernando Lopez", "nacionalista", "FERNANDO LOPEZ (NP)", "48.10%"),
                    L("Gerardo Roxas", "liberal", "GERARDO ROXAS (LP)", "31.08%"),
                    L("Manuel Manahan", "philippine-progress", "MANUEL MANAHAN (PPP)", "3.43%"),
                ),
            ),
        ),
    ),
    General(
        date(1969, 11, 11),
        "1969 Philippine general election",
        (
            Race(
                "president",
                128,
                f"{COMELEC} (November 11, 1969)",
                (
                    L("Ferdinand E. Marcos", "nacionalista", "FERDINAND E. MARCOS (NP)", "61.47%", (231, "5,017,343")),
                    L("Sergio Osmeña Jr.", "liberal", "SERGIO OSMEÑA JR. (LP)", "38.51%"),
                ),
            ),
            Race(
                "vice-president",
                129,
                f"{COMELEC} (November 11, 1969)",
                (
                    L("Fernando Lopez", "nacionalista", "FERNANDO LOPEZ (NP)", "62.8%"),
                    L("Genaro Magsaysay", "liberal", "GENARO MAGSAYSAY (LP)", "37.2%"),
                ),
            ),
        ),
    ),
    General(
        date(1981, 6, 16),
        "1981 Philippine presidential election",
        (
            Race(
                "president",
                145,
                BATASAN_1981,
                (
                    L("Ferdinand E. Marcos", "kbl", "FERDINAND E. MARCOS (KBL)", "88.02%", (231, "18,309,360")),
                    L("Alejo Santos", "nacionalista", "ALEJO SANTOS (NP)", "8.25%"),
                    L("Bartolome Cabangbang", "federal", "BARTOLOME CABANGBANG (FEDERAL PARTY)", "3.60%"),
                    L("", None, "OTHERS", "0.13%"),
                ),
            ),
        ),
    ),
    General(
        date(1986, 2, 7),
        "1986 Philippine presidential election",
        (
            Race(
                "president",
                152,
                TIMES_1986,
                (
                    L("Ferdinand E. Marcos", "kbl", "FERDINAND E. MARCOS (KBL)", "53.62%", (231, "10,807,197")),
                    L("Corazon C. Aquino", "unido", "CORAZON C. AQUINO (UNIDO)", "46.1%", (232, "9,291,719")),
                    L("Reuben Canoy", "mindanao-alliance", "REUBEN CANOY (MINDANAO ALLIANCE)", "0.17%"),
                    L("Narciso Padilla", IND, "NARCISO PADILLA (IND.)", "0.12%"),
                ),
                note=(
                    "The Commission on Elections' count, as the almanac prints it from a report dated February 16, "
                    "with each candidate's share; the winner's and the runner-up's votes are those its timeline of the "
                    "presidency gives as the Commission's tally. The Batasang Pambansa proclaimed Ferdinand E. Marcos "
                    "elected on February 15, 1986; he left the country on February 25, 1986, when Corazon C. Aquino "
                    "took office."
                ),
            ),
            Race(
                "president",
                152,
                TIMES_1986,
                (
                    L("Corazon C. Aquino", "unido", "CORAZON C. AQUINO (UNIDO)", "52.63%"),
                    L("Ferdinand E. Marcos", "kbl", "FERDINAND E. MARCOS (KBL)", "47.37%"),
                ),
                stage="alternative",
                completeness=0.7,
                note=(
                    "The National Citizens' Movement for Free Elections (NAMFREL) quick count, as the almanac prints it "
                    "from a report dated February 16, representing 70 percent of the votes. It is not an official "
                    "count."
                ),
            ),
            Race(
                "vice-president",
                153,
                TIMES_1986,
                (
                    L("Salvador H. Laurel", "unido", "SALVADOR H. LAUREL (UNIDO)", "51%"),
                    L("Arturo M. Tolentino", "kbl", "ARTURO M. TOLENTINO (KBL)", "45%"),
                    L("Eva Estrada-Kalaw", "lp-kalaw-wing", "EVA ESTRADA-KALAW (LP-KALAW WING)", "4%"),
                ),
                stage="alternative",
                completeness=0.7,
                note=(
                    "The National Citizens' Movement for Free Elections (NAMFREL) quick count, as the almanac prints it "
                    "from a report dated February 16, representing 70 percent of the votes. It is not an official "
                    "count; the almanac prints no official count for the vice presidency in 1986."
                ),
            ),
        ),
    ),
    General(
        date(1992, 5, 11),
        "1992 Philippine general election",
        (
            Race(
                "president",
                161,
                HOUSE.format(1992),
                (
                    L("Fidel V. Ramos", "lakas-nucd", "FIDEL V. RAMOS (Lakas-NUCD)", "23.58%", (233, "5,342,521")),
                    L("Miriam Defensor-Santiago", "prp", "MIRIAM DEFENSOR-SANTIAGO (PRP)", "19.73%"),
                    L("Eduardo M. Cojuangco Jr.", "npc", "EDUARDO M. COJUANGCO JR. (NPC)", "18.17%"),
                    L("Ramon Mitra", "ldp", "RAMON MITRA (LDP)", "14.64%"),
                    L("Imelda Marcos", "kbl", "IMELDA MARCOS (KBL)", "10.32%"),
                    L("Jovito Salonga", "liberal", "JOVITO SALONGA (LP)", "10.16%"),
                    L("Salvador Laurel", "nacionalista", "SALVADOR LAUREL (NP)", "3.4%"),
                ),
            ),
            Race(
                "vice-president",
                162,
                HOUSE.format(1992),
                (
                    L("Joseph Ejercito Estrada", "npc", "JOSEPH EJERCITO ESTRADA (NPC)", "33%"),
                    L("Marcelo Fernan", "ldp", "MARCELO FERNAN (LDP)", "22%"),
                    L("Emilio Osmeña", "lakas-nucd", "EMILIO OSMEÑA (Lakas-NUCD)", "16.5%"),
                    L("Ramon Magsaysay Jr.", "prp", "RAMON MAGSAYSAY JR. (PRP)", "14%"),
                    L("Aquilino Pimentel Jr.", "liberal", "AQUILINO PIMENTEL JR. (LP)", "10%"),
                    L("Vicente Magsaysay", "kbl", "VICENTE MAGSAYSAY (KBL)", "3.5%"),
                    L("Eva Estrada-Kalaw", "nacionalista", "EVA ESTRADA-KALAW (NP)", "1%"),
                ),
            ),
        ),
    ),
    General(
        date(1998, 5, 11),
        "1998 Philippine general election",
        (
            Race(
                "president",
                167,
                HOUSE.format(1998),
                (
                    L("Joseph Ejercito Estrada", "pmp", "JOSEPH EJERCITO ESTRADA (PMP)", "39.86%", (234, "10,956,610")),
                    L("Jose de Venecia", "lakas-nucd", "JOSE DE VENECIA (Lakas-NUCD)", "15.87%"),
                    L("Raul Roco", "aksyon", "RAUL ROCO (AKSYON DEMOKRATIKO)", "13.83%"),
                    L("Emilio Osmeña", "promdi", "EMILIO OSMEÑA (PROMDI)", "12.44%"),
                    L("Alfredo Lim", "liberal", "ALFREDO LIM (LP)", "8.71%"),
                    L("Renato de Villa", "reporma", "RENATO DE VILLA (REPORMA-LM)", "4.86%"),
                    L("Miriam Defensor-Santiago", "prp", "MIRIAM DEFENSOR-SANTIAGO (PRP)", "2.96%"),
                    L("Juan Ponce Enrile", IND, "JUAN PONCE ENRILE (IND.)", "1.28%"),
                    L(
                        "Santiago Dumlao",
                        "kpp",
                        "SANTIAGO DUMLAO (Kilusan para sa Pambansang Pagpapanibago)",
                        "0.12%",
                    ),
                    L("Manuel L. Morato", "bansang-marangal", "MANUEL L. MORATO (Partido Bansang Marangal)", "0.07%"),
                ),
            ),
            Race(
                "vice-president",
                168,
                HOUSE.format(1998),
                (
                    L("Gloria Macapagal-Arroyo", "lakas-nucd", "GLORIA MACAPAGAL-ARROYO (Lakas-NUCD)", "50%"),
                    L("Edgardo Angara", "pmp", "EDGARDO ANGARA (PMP)", "22%"),
                    L("Oscar Orbos", "reporma", "OSCAR ORBOS (Reporma-LM)", "13%"),
                    L("Sergio Osmeña III", "liberal", "SERGIO OSMEÑA III (LP)", "9%"),
                    L("Francisco Tatad", "prp", "FRANCISCO TATAD (PRP)", "3%"),
                    L("Ismael Sueño", "promdi", "ISMAEL SUEÑO (PROMDI)", "2%"),
                    L("Irene Santiago", "aksyon", "IRENE SANTIAGO (AKSYON DEMOKRATIKO)", "1%"),
                ),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------------------------------------------
# Plebiscites and referendums


@dataclass(frozen=True)
class Answer:
    label: str  # as printed
    votes: str  # the count as printed, or a share for a question the almanac prints only as shares
    share: str | None = None  # the share printed beside the count


@dataclass(frozen=True)
class Question:
    slug: str
    text: str  # the question as the almanac prints it
    type: str
    page: int
    answers: tuple[Answer, ...]
    shares_only: bool = False


@dataclass(frozen=True)
class Plebiscite:
    day: date
    to: date
    name: str
    cites: str
    context: str
    questions: tuple[Question, ...]
    hands: bool = False  # votes cast by a show of hands


A = Answer
YES, NO, ABSTAIN = "Yes", "No", "Abstain"

PLEBISCITES = (
    Plebiscite(
        date(1935, 5, 14),
        date(1935, 5, 14),
        "1935 Philippine constitutional plebiscite",
        "Philippines Free Press (May 18, 1935)",
        "The Filipino people voted to ratify the 1935 Constitution.",
        (
            Question(
                "constitution",
                "Are you in favor of ratifying the 1935 Constitution?",
                "mandatory_referendum",
                72,
                (A(YES, "1,213,046", "(96.43%)"), A(NO, "44,963", "(3.57%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1937, 4, 30),
        date(1937, 4, 30),
        "1937 Philippine women's suffrage plebiscite",
        "Philippine Parties and Politics: A Historical Study of National Experience in Democracy by Dapen Liang",
        "Women's suffrage was approved in a special plebiscite mandated by Commonwealth Act No. 34, where voting was "
        "restricted to women who possessed the qualifications required for male voters.",
        (
            Question(
                "womens-suffrage",
                "Are you in favor of granting suffrage to women?",
                "mandatory_referendum",
                77,
                (A(YES, "447,725", "(91%)"), A(NO, "44,307", "(9%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1939, 10, 24),
        date(1939, 10, 24),
        "1939 Philippine economic adjustment plebiscite",
        "Resolution No. 53, Second National Assembly, Third Special Session, November 3, 1939",
        "Pursuant to Commonwealth Act No. 492, a plebiscite was held regarding economic adjustments to address "
        "infirmities in the Tydings-McDuffie Act, which placed Philippine products under duty-free quotas for the "
        "remainder of the Commonwealth.",
        (
            Question(
                "duty-free-quotas",
                "Should Philippine products be placed under duty-free quotas for the remainder of the Commonwealth?",
                "mandatory_referendum",
                79,
                (A(YES, "1,393,453", "(96.56%)"), A(NO, "49,633", "(3.44%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1940, 6, 18),
        date(1940, 6, 18),
        "1940 Philippine constitutional plebiscite",
        "Resolution No. 88, Second National Assembly, Fourth Special Session, July 12, 1940",
        "Pursuant to Commonwealth Act No. 517, a national referendum was held where amendments to the 1935 "
        "Constitution were ratified.",
        (
            Question(
                "presidential-term",
                "Are you in favor of allowing the president and vice president to serve for four years, with "
                "re-election for one term?",
                "mandatory_referendum",
                80,
                (A(YES, "1,072,039", "(81.67%)"), A(NO, "240,632", "(18.33%)")),
            ),
            Question(
                "bicameral-congress",
                "Are you in favor of creating a bicameral Congress composed of the Senate and the House of "
                "Representatives?",
                "mandatory_referendum",
                80,
                (A(YES, "1,043,712", "(79.14%)"), A(NO, "275,184", "(20.86%)")),
            ),
            Question(
                "commission-on-elections",
                "Are you in favor of creating a Commission on Elections?",
                "mandatory_referendum",
                80,
                (A(YES, "1,017,606", "(77.95%)"), A(NO, "287,923", "(22.05%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1947, 3, 11),
        date(1947, 3, 11),
        "1947 Philippine parity amendment plebiscite",
        "Philippine Parties and Politics: A Historical Study of National Experience in Democracy by Dapen Liang",
        "The Parity Amendment to the 1935 Constitution was approved in a nationwide plebiscite pursuant to a "
        "Congressional Resolution dated September 18, 1946. U.S. citizens were granted the right to utilize "
        "Philippine natural resources. Parity rights expired on July 3, 1974.",
        (
            Question(
                "parity-amendment",
                "Are you in favor of approving the Parity Amendment to the Constitution, allowing U.S. citizens the "
                "right to utilize Philippine natural resources?",
                "mandatory_referendum",
                98,
                (A(YES, "432,933", "(78.89%)"), A(NO, "115,853", "(21.11%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1967, 11, 14),
        date(1967, 11, 14),
        "1967 Philippine constitutional plebiscite",
        "Philippine Parties and Politics: A Historical Study of National Experience in Democracy by Dapen Liang",
        "Pursuant to Republic Act No. 4913, a national referendum was held where amendments to the 1935 Constitution "
        "were rejected. This was the first and only time that a constitutional proposal was rejected by the "
        "electorate.",
        (
            Question(
                "house-size",
                "Are you in favor of increasing the number of congressmen from 120 to 180?",
                "mandatory_referendum",
                126,
                (A(YES, "18%"), A(NO, "82%")),
                shares_only=True,
            ),
            Question(
                "convention-delegates",
                "Are you in favor of allowing members of Congress to serve in the coming Constitutional Convention "
                "without forfeiting their seats?",
                "mandatory_referendum",
                126,
                (A(YES, "16.5%"), A(NO, "83.5%")),
                shares_only=True,
            ),
        ),
    ),
    Plebiscite(
        date(1973, 1, 10),
        date(1973, 1, 15),
        "1973 Philippine constitutional plebiscite",
        "Proclamation No. 1102, s. 1973",
        "Presidential Decree No. 86, s. 1972 and Presidential Decree No. 86-A, s. 1973 called for a plebiscite for "
        "the Citizens Assemblies to decide whether to adopt the draft Constitution of the 1971 Constitutional "
        "Convention, and whether to hold another plebiscite to ratify it. Voting age was reduced to 15, and votes "
        "were cast by a show of hands, in place of secret ballots.",
        (
            Question(
                "constitution",
                "Are you in favor of adopting the proposed (1973) Constitution?",
                "mandatory_referendum",
                134,
                (A(YES, "14,976,561", "(90.67%)"), A(NO, "743,869", "(9.33%)")),
            ),
            Question(
                "another-plebiscite",
                "Are you in favor of holding another plebiscite to ratify the Constitution?",
                "plebiscite",
                134,
                (A(YES, "1,421,616", "(9.04%)"), A(NO, "14,298,814", "(90.96%)")),
            ),
        ),
        hands=True,
    ),
    Plebiscite(
        date(1973, 7, 27),
        date(1973, 7, 28),
        "1973 Philippine martial law referendum",
        "Philippines Daily Express (August 4, 1973)",
        "Pursuant to Presidential Decree No. 228, s. 1973, the Citizens Assemblies voted to continue President "
        "Ferdinand E. Marcos' rule. Formal voting procedures, including secret ballots, were followed; voting age "
        "was still 15. Presidential Decree No. 229 penalized registered voters who did not cast their vote.",
        (
            Question(
                "continue-marcos",
                "Do you want President Marcos to continue beyond 1973 and finish the reforms he has initiated under "
                "the Martial Law?",
                "plebiscite",
                135,
                (A(YES, "18,052,016", "(90.67%)"), A(NO, "1,856,744", "(9.33%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1975, 2, 27),
        date(1975, 2, 28),
        "1975 Philippine martial law referendum",
        "Philippines Daily Express (March 16, 1975)",
        "Pursuant to Proclamation No. 1366, s. 1974, the Citizens Assemblies voted to continue martial law and allow "
        "the president to continue to exercise his powers. Formal voting procedures, including secret ballots, were "
        "followed; voting age was still 15.",
        (
            Question(
                "martial-law-powers",
                "Do you approve of the manner President Marcos has been exercising his powers under Martial Law and "
                "the Constitution?",
                "plebiscite",
                136,
                (A(YES, "20,487,365", "(88.69%)"), A(NO, "2,170,507", "(9.40%)"), A(ABSTAIN, "441,652", "(1.91%)")),
            ),
            Question(
                "continue-powers",
                "Do you want the president to continue exercising the same powers?",
                "plebiscite",
                137,
                (A(YES, "19,919,985", "(87.51%)"), A(NO, "2,281,567", "(10.02%)"), A(ABSTAIN, "562,047", "(2.47%)")),
            ),
            Question(
                "local-officials",
                "At the expiration of the terms of office of your local elective officials on December 31, 1975, how "
                "do you want their successors to be chosen?",
                "plebiscite",
                137,
                (
                    A("Appointed", "15,321,779", "(77.45%)"),
                    A("Elected", "3,278,058", "(16.57%)"),
                    A(ABSTAIN, "1,183,183", "(5.98%)"),
                ),
            ),
        ),
    ),
    Plebiscite(
        date(1976, 10, 16),
        date(1976, 10, 17),
        "1976 Philippine martial law and constitutional referendum",
        "Philippines Daily Express (October 27, 1976)",
        "Pursuant to Presidential Decree No. 991, s. 1976, the Citizens Assemblies voted to continue martial law and "
        "to amend the 1973 Constitution with the nine amendments of Presidential Decree No. 1033, s. 1976. Formal "
        "voting procedures, including secret ballots, were followed; voting age was still 15.",
        (
            Question(
                "martial-law",
                "Do you want Martial Law to be continued?",
                "plebiscite",
                138,
                (A(YES, "21,378,895", "(90.05%)"), A(NO, "1,867,326%", "(7.86%)"), A(ABSTAIN, "493,886", "(2.09%)")),
            ),
            Question(
                "amendments",
                "Do you approve of the Constitutional amendments (stated in Presidential Decree No. 1033, s. 1976)?",
                "mandatory_referendum",
                138,
                (A(YES, "20,791,888", "(87.58%)"), A(NO, "2,279,285", "(9.60%)"), A(ABSTAIN, "668,934", "(2.82%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1977, 12, 17),
        date(1977, 12, 17),
        "1977 Philippine referendum",
        "Philippines Daily Express (December 25, 1977)",
        "Pursuant to Presidential Decree No. 1229, s. 1977, the Citizens Assemblies approved the continuation of "
        "President Marcos's term and exercise of power. Formal voting procedures, including secret ballots, were "
        "followed; voting age was still 15.",
        (
            Question(
                "marcos-prime-minister",
                "Do you vote that President Ferdinand E. Marcos continue in office as incumbent president and be prime "
                "minister after the organization of the Interim Batasang Pambansa in 1978?",
                "plebiscite",
                139,
                (A(YES, "20,062,782", "(89.3%)"), A(NO, "2,104,209", "(9.37%)"), A(ABSTAIN, "1,927,236", "(1.33%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1981, 4, 7),
        date(1981, 4, 7),
        "1981 Philippine constitutional plebiscite",
        "Proclamation No. 2078, s. 1981; Philippines Daily Express (April 12, 1981)",
        "Pursuant to Batas Pambansa Blg. 122, the Citizens Assemblies approved amendments to the Constitution.",
        (
            Question(
                "modified-parliamentary",
                "Do you approve of amending the Constitution to establish a modified parliamentary system, with a "
                "president elected by direct vote of the people for a six-year term, an Executive Committee, and a "
                "prime minister nominated by the president and elected by the Batasang Pambansa?",
                "mandatory_referendum",
                142,
                (A(YES, "13,847,765", "(79.53%)"), A(NO, "3,564,675", "(20.47%)")),
            ),
            Question(
                "elective-officials",
                "Do you want to prohibit elective officials from being eligible for appointment during their term, "
                "except as member of the Executive Committee? Do you want to entitle accreditation to the top two "
                "political parties only? Do you want to prohibit public officers from changing political party "
                "affiliations during their term?",
                "mandatory_referendum",
                143,
                (A(YES, "13,774,799", "(78.95%)"), A(NO, "3,672,644", "(21.05%)")),
            ),
            Question(
                "land-ownership",
                "Do you want to allow natural-born citizens of the Philippines who have lost their citizenship to own "
                "land?",
                "mandatory_referendum",
                143,
                (A(YES, "13,382,300", "(77.55%)"), A(NO, "3,874,416", "(22.45%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1981, 6, 16),
        date(1981, 6, 16),
        "1981 Philippine barangay elections plebiscite",
        "Proclamation No. 2094, s. 1981; Philippines Daily Express (June 24, 1981)",
        "Pursuant to Proclamation No. 2088, s. 1981, a plebiscite was held with the presidential election to decide "
        "whether to renew the mandate of barangay officials.",
        (
            Question(
                "barangay-elections",
                "Should there be barangay elections right after the presidential elections?",
                "plebiscite",
                146,
                (A(YES, "16,268,523", "(81.09%)"), A(NO, "3,793,322", "(18.91%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1984, 1, 27),
        date(1984, 1, 27),
        "1984 Philippine constitutional plebiscite",
        "Proclamation No. 2332, s. 1984; Philippines Daily Express (February 2, 1984)",
        "Pursuant to Batas Pambansa Blg. 643, the Citizens Assemblies approved amendments to the Constitution.",
        (
            Question(
                "representation",
                "Do you want representation in the Batasang Pambansa to be by province, highly urbanized city, and "
                "district (as opposed to by region)?",
                "mandatory_referendum",
                147,
                (A(YES, "11,353,161", "(83.94%)"), A(NO, "2,171,963", "(16.06%)")),
            ),
            Question(
                "vice-presidency",
                "Do you want to abolish the Executive Committee and restore the vice presidency?",
                "mandatory_referendum",
                148,
                (A(YES, "11,350,748", "(82.99%)"), A(NO, "2,326,954", "(17.01%)")),
            ),
            Question(
                "land-grant",
                "Do you want to include “grant” as an additional mode for the acquisition of public lands by "
                "qualified tenants, farmers, and other landless citizens?",
                "mandatory_referendum",
                148,
                (A(YES, "9,533,138", "(69.25%)"), A(NO, "4,232,369", "(30.75%)")),
            ),
            Question(
                "urban-land-reform",
                "Do you approve of the state undertaking an urban land reform program for landless, homeless, or "
                "low-income resident citizens?",
                "mandatory_referendum",
                148,
                (A(YES, "9,373,596", "(68.59%)"), A(NO, "4,293,297", "(31.41%)")),
            ),
        ),
    ),
    Plebiscite(
        date(1987, 2, 2),
        date(1987, 2, 2),
        "1987 Philippine constitutional plebiscite",
        "Proclamation No. 58, s. 1987",
        "Pursuant to Proclamation No. 3, s. 1986, the 1987 Constitution was ratified.",
        (
            Question(
                "constitution",
                "Are you in favor of ratifying the proposed (1987) Constitution?",
                "mandatory_referendum",
                156,
                (A(YES, "16,622,111", "(76.3%)"), A(NO, "4,953,375", "(22.74%)"), A(ABSTAIN, "209,730", "(0.96%)")),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------------------------------------------
# Checks


def race_caveats(race: Race) -> list[str]:
    total = sum(share(line.share) for line in race.lines)
    out = []
    if abs(total - 100) > Decimal("0.05"):
        out.append(f"The shares the almanac prints add up to {total} percent, not 100; they are stored as printed.")
    return out


def question_caveats(q: Question) -> list[str]:
    """Where the printed shares do not fit the printed counts, say so; every figure is stored as printed."""
    if q.shares_only:
        return []
    votes = [count(a.votes) for a in q.answers]
    total = sum(votes)
    off = []
    for a, v in zip(q.answers, votes, strict=True):
        assert a.share is not None
        printed = share(a.share.strip("()"))
        found = (Decimal(v) * 100 / total).quantize(Decimal("0.01"))
        if abs(found - printed) > Decimal("0.6"):
            off.append(f"{a.label.lower()} {v:,} ({found} percent, printed {printed})")
    if not off:
        return []
    return [
        "The shares the almanac prints beside the counts do not fit them: "
        + "; ".join(off)
        + f", of {total:,} counted. The counts are stored as printed."
    ]


def check() -> dict[str, int]:
    """Every figure must stand once on its page, and the race's or question's printed numbers must be legible."""
    found = {"figures": 0, "in_first_edition": 0}
    for g in GENERALS:
        for race in g.races:
            for line in race.lines:
                for page, text in ((race.page, line.share), *([line.votes] if line.votes else [])):
                    word(page, text)
                    found["figures"] += 1
                    found["in_first_edition"] += in_first(text)
    for pleb in PLEBISCITES:
        for q in pleb.questions:
            for a in q.answers:
                word(q.page, a.votes)
                found["figures"] += 1
                found["in_first_edition"] += in_first(a.votes)
    return found


# ---------------------------------------------------------------------------------------------------------------
# Packages


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo", SOURCE, "The national reporting scheme of the Philippines' historical national results"
    )
    p.external |= {PHILIPPINES, COUNTRY}
    p.add("scheme", code=SCHEME, polity_id=PHILIPPINES, kind="administrative", rank=0, noun="nation")
    return p


def pages() -> list[int]:
    found = {race.page for g in GENERALS for race in g.races}
    found |= {line.votes[0] for g in GENERALS for race in g.races for line in race.lines if line.votes}
    found |= {q.page for pleb in PLEBISCITES for q in pleb.questions}
    return sorted(found)


def page_id(page: int) -> str:
    return key("page", "revised", page)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Philippine presidency and vice presidency, their electoral systems, the parties of the presidential and "
        "vice-presidential candidates of 1935 to 1998, and the two editions of the Philippine Electoral Almanac",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PHILIPPINES)
    for sid, url, title, year in (
        (REVISED_SOURCE, REVISED, "Philippine Electoral Almanac, Revised and Expanded Edition", "2015"),
        (FIRST_SOURCE, FIRST, "Philippine Electoral Almanac, First Edition", "2012"),
    ):
        p.add(
            "source",
            id=sid,
            kind="official_body",
            publisher=PUBLISHER,
            title=f"{title} ({year})",
            url=url,
            license=LICENSE,
            attribution="Presidential Communications Development and Strategic Planning Office, Philippine Electoral Almanac",
        )
        artifact = raw.cached(SOURCE, url)
        artifact_id = p.artifact(artifact, sid)
        if sid != REVISED_SOURCE:
            continue
        for page in pages():
            (width, height), _ = _page(artifact.path, page)
            p.add(
                "source_page",
                id=page_id(page),
                artifact_id=artifact_id,
                page_number=page,
                image_key=f"pages/{SOURCE}/{artifact.path.stem}/{page}.png",
                width=round(width),
                height=round(height),
            )
    for office, kind, english, native in (
        (PRESIDENT, "head_of_state", "President of the Philippines", "Pangulo ng Pilipinas"),
        (VICE_PRESIDENT, "other", "Vice President of the Philippines", "Pangalawang Pangulo ng Pilipinas"),
    ):
        p.add("office", id=office, polity_id=PHILIPPINES, kind=kind)
        p.name(office, english, "en", "official")
        p.name(office, native, "fil", "native", False)
    p.add("electoral_system", id=PLURALITY, polity_id=PHILIPPINES, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", PLURALITY),
        entity_id=PLURALITY,
        kind="methodology",
        body_md="The president and the vice president are elected separately by a plurality of the votes cast in "
        "the whole country.",
        lang="en",
    )
    p.add("electoral_system", id=REFERENDUM, polity_id=PHILIPPINES, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", REFERENDUM),
        entity_id=REFERENDUM,
        kind="methodology",
        body_md="A question put to a national plebiscite or referendum is carried by a majority of the votes cast "
        "on it.",
        lang="en",
    )
    for party, (english, abbreviation) in PARTIES.items():
        id = key("party", party)
        p.add("party", id=id, kind="party", polity_id=PHILIPPINES)
        p.name(id, english, "en", "official")
        if abbreviation:
            p.name(id, abbreviation, "en", "abbreviation", False)
    return p


def _nation(p: Package, tag: str) -> str:
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PHILIPPINES,
        scheme_code=SCHEME,
        code=f"{tag}:ph",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    p.name(unit, "Philippines", "en", "official")
    p.add(
        "reporting_unit_area", unit_id=unit, area_id=COUNTRY, method="official_list", approximate=False, partial=False
    )
    return unit


def _value(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: object, flags: int, w: Word
) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=flags,
        value=value,
    )
    x0, y0, x1, y1 = w.box
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=page_id(w.page),
        source_id=REVISED_SOURCE,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
    )


def person(name: str) -> str:
    return key("person", name)


def general(g: General) -> Package:
    day = g.day.isoformat()
    offices = " and ".join(
        name
        for office, name in (("president", "presidential"), ("vice-president", "vice-presidential"))
        if any(r.office == office for r in g.races)
    )
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {offices} election of the Philippines of {g.day:%-d %B %Y}, national vote shares and the winner's votes, "
        "from the Philippine Electoral Almanac",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PHILIPPINES, COUNTRY}
    election = key("election", day)
    p.add("election", id=election, polity_id=PHILIPPINES, kind="general", date_from=g.day, date_to=g.day, status="held")
    p.name(election, g.name, "en", "official")
    p.add("slug", path=f"ph/{day}", entity_id=election, is_current=True)
    unit = _nation(p, day)
    people: set[str] = set()
    for race in g.races:
        office = PRESIDENT if race.office == "president" else VICE_PRESIDENT
        contest = key("contest", day, race.office)
        title = "President" if race.office == "president" else "Vice President"
        if race.stage == "official" or not any(r.office == race.office and r.stage == "official" for r in g.races):
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=COUNTRY,
                magnitude=1,
                round_number=1,
                electoral_system_id=PLURALITY,
                default_result_kind="counted",
            )
            p.name(contest, f"{g.day.year} Philippine {race.office.replace('-', ' ')}ial election", "en", "official")
            p.add("slug", path=f"ph/{day}/{race.office}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, race.office, race.stage)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage=race.stage,
            kind="counted",
            source_id=REVISED_SOURCE,
            status="active",
            is_default=True,
            completeness=race.completeness,
        )
        method = (
            f"National totals for {title.lower()} as the Philippine Electoral Almanac prints them: the share of each "
            "candidate from the legend of its map, which colours each province by the candidate who carried it, and "
            "the winner's votes from its timeline of the presidency where it gives them. The almanac prints no "
            f"figures below the country. It cites {race.cites}."
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        for index, caveat in enumerate(race_caveats(race)):
            p.add(
                "note",
                id=key("note", result_set, index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        if race.note:
            p.add(
                "note",
                id=key("note", result_set, "context"),
                entity_id=result_set,
                kind="context",
                body_md=race.note,
                lang="en",
            )
        top = max(race.lines, key=lambda line: share(line.share))
        for position, line in enumerate(race.lines, 1):
            option = key("option", day, race.office, line.name or "others")
            if race.stage == "official" or not any(r.office == race.office and r.stage == "official" for r in g.races):
                party = key("party", line.party) if line.party else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if line.name else "others",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=line.label,
                )
                if line.name:
                    who = person(line.name)
                    if who not in people:
                        people.add(who)
                        p.add("person", id=who)
                        p.name(who, line.name, "en", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", day, race.office, line.name),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=line.name,
                        member_of_party_id=party,
                        is_independent=line.party is None,
                    )
            elif not any(
                line.name == other.name
                for r in g.races
                if r.office == race.office and r.stage == "official"
                for other in r.lines
            ):
                raise ValueError(f"{day} {race.office}: {line.name} is on no official line")
            _value(p, result_set, unit, option, VOTE_SHARE, share(line.share), SHARES, word(race.page, line.share))
            if line.votes:
                page, text = line.votes
                _value(p, result_set, unit, option, VOTES, count(text), 0, word(page, text))
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=count(text))
            if race.stage == "official" and line.name:
                won = line is top
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    candidacy_id=None,
                    kind="elected" if won else "lost",
                    seats=1 if won else None,
                )
    return p


def plebiscite(pleb: Plebiscite) -> Package:
    day = pleb.day.isoformat()
    p = Package(
        f"{SOURCE}/{day}-plebiscite",
        SOURCE,
        f"The {pleb.name} of {pleb.day:%-d %B %Y}, national totals, from the Philippine Electoral Almanac",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PHILIPPINES, COUNTRY}
    election = key("election", day, "plebiscite")
    p.add(
        "election",
        id=election,
        polity_id=PHILIPPINES,
        kind="referendum",
        date_from=pleb.day,
        date_to=pleb.to,
        status="held",
    )
    p.name(election, pleb.name, "en", "official")
    path = f"ph/{day}-plebiscite"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=key("note", election), entity_id=election, kind="context", body_md=pleb.context, lang="en")
    unit = _nation(p, f"{day}-plebiscite")
    for q in pleb.questions:
        contest = key("contest", day, q.slug)
        result_kind = "show_of_hands" if pleb.hands else "counted"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=COUNTRY,
            electoral_system_id=REFERENDUM,
            round_number=1,
            default_result_kind=result_kind,
        )
        p.add("ballot_question", contest_id=contest, type=q.type)
        p.name(contest, q.text, "en", "official")
        p.add("slug", path=f"{path}/{q.slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, q.slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind=result_kind,
            source_id=REVISED_SOURCE,
            status="active",
            is_default=True,
        )
        method = (
            "National totals as the Philippine Electoral Almanac prints them: "
            + ("the share of each answer; it prints no counts" if q.shares_only else "the votes for each answer")
            + f" and no figures below the country. It cites {pleb.cites}."
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        for index, caveat in enumerate(question_caveats(q)):
            p.add(
                "note",
                id=key("note", result_set, index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        scores = [share(a.votes) if q.shares_only else Decimal(count(a.votes)) for a in q.answers]
        best = max(scores)
        for position, (a, score) in enumerate(zip(q.answers, scores, strict=True), 1):
            option = key("option", day, q.slug, a.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=a.label,
            )
            w = word(q.page, a.votes)
            if q.shares_only:
                _value(p, result_set, unit, option, VOTE_SHARE, share(a.votes), SHARES, w)
            else:
                _value(p, result_set, unit, option, VOTES, count(a.votes), 0, w)
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=count(a.votes))
            if a.label != ABSTAIN:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if score == best else "lost")
    return p


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{g.day}" for g in GENERALS),
        *(f"{SOURCE}/{pleb.day}-plebiscite" for pleb in PLEBISCITES),
    ]


def parse() -> None:
    print(f"{SOURCE}: {check()}", flush=True)
    geo().write()
    registry().write()
    for g in GENERALS:
        general(g).write()
    for pleb in PLEBISCITES:
        plebiscite(pleb).write()


def emit() -> None:
    package.run("load", packages())
