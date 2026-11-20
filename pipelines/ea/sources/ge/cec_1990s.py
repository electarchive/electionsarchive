"""Georgia's national elections of 1990 to 2018, from the Central Election Commission's electoral history.

The Commission's volume "Electoral History of Georgia 1990-2018" (history.cec.gov.ge) prints, for each election, the
figures of the Commission's summary protocol in Georgian and in English: the electorate, turnout, invalid ballots, the
votes of each party list or candidate, and the seats each list won. The volume is a typeset PDF with a text layer, so
every number is read from the text with its page and word box, and the Georgian and English pages must agree before
anything is written; where they differ, the volume's printed protocol or its own other tables settle which figure
stands, and the spec says so.

This module holds the elections the volume reports at the national level:

- the Supreme Council of 28 October 1990 (its party-list tier; runoffs on 11 November were majoritarian only);
- the independence referendum of 31 March 1991;
- the President of 26 May 1991;
- the Parliament and the Chairman of Parliament of 11 October 1992;
- the Parliament and the President of 5 November 1995;
- the Parliament of 31 October 1999;
- the President of 9 April 2000;
- the Supreme Council of the Autonomous Republic of Ajara on 23 June 1991, 22 September 1996, 20 June 2004, and 3
  November 2008, and its Parliament of 4 November 2001, which the volume reports as party-list totals (and, for 1991
  to 1996, 2001, and 2008, list seats) for the whole republic;
- the local elections of 2002, 2006, 2010, 2014, and 2017, with the seats by party (2002 and 2006) or the party-list
  votes (2010, 2014, 2017) across Georgia, and the Sakrebulo and mayor of Tbilisi, the one city the volume reports;
- the Parliament of 2 November 2003 and its referendum, the President of 4 January 2004, the Parliament of 28 March
  2004, the President of 5 January 2008 with its two plebiscites, the Parliament of 21 May 2008, the Parliament of
  1 October 2012, the Presidents of 2013 and 2018 (with the second round of 2018), and the Parliament of 8 October
  2016.

The volume prints no result by district or precinct, and names no majoritarian winners, so the single-member
districts of each Parliament, Supreme Council, and Sakrebulo are not stored; their seat totals by party appear in each
election's note. The Supreme Council of Ajara of 2012 and 2016 is held by `ge.ajara` at the precinct level.

The Commission's site refuses scripted clients, so the volume reached the raw cache as a web archive snapshot of the
same file.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from functools import cache
from html import unescape
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ge.cec_1990s"
GEORGIA = str(uuid5(package.NAMESPACE, "polity\x1fGE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGEO"))
BOOK = "https://history.cec.gov.ge/PDF/ElectoralHistoryOfGeorgia.pdf"
SNAPSHOT = "https://web.archive.org/web/20230523042433id_/http://history.cec.gov.ge/PDF/ElectoralHistoryOfGeorgia.pdf"
HOME = "https://history.cec.gov.ge/"
SCHEME = "ge.nation"
REGION_SCHEME = "ge.autonomous_republic"
CAPITAL_SCHEME = "ge.capital"

VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
BALLOTS = CAST


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC = key("source", "cec")
UNIT = key("unit", "national")
SUPREME_COUNCIL = key("body", "supreme-council")
PARLIAMENT = key("body", "parliament")
PRESIDENT = key("office", "president")
CHAIRMAN = key("office", "chairman")
SYSTEM_1990 = key("electoral_system", "supreme-council-1990")
SYSTEM_1995 = key("electoral_system", "parliament-1995")
SYSTEM_1999 = key("electoral_system", "parliament-1999")
SYSTEM_PRESIDENT = key("electoral_system", "president")
AJARA_UNIT = key("unit", "ajara")
SYSTEM_AJARA_1991 = key("electoral_system", "ajara-supreme-council-1991")
SYSTEM_AJARA_1996 = key("electoral_system", "ajara-supreme-council-1996")
AJARA_BODY = key("body", "ajara-supreme-council")
TBILISI_UNIT = key("unit", "tbilisi")
TBILISI_AREA = key("area", CAPITAL_SCHEME, "GE-TB")
TBILISI_BODY = key("body", "tbilisi-sakrebulo")
MAYOR = key("office", "mayor-of-tbilisi")
LOCAL_COUNCILS = key("body", "local-councils")
AJARA_AREA = key("area", REGION_SCHEME, "GE-AJ")
GEO = f"{SOURCE}/geo"
GEO_SOURCE = key("source", "geoboundaries")
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/GEO/ADM1/"
    "geoBoundaries-GEO-ADM1_simplified.geojson"
)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the volume


@dataclass(frozen=True)
class Word:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>|<page width')
SIZES: dict[int, tuple[int, int]] = {}
"""Each read page's width and height in points."""


PAGES = DATA / "pages" / SOURCE


def render(page: int) -> None:
    """Writes the page's image where the site serves it from, named by the volume's hash and the page."""
    image = PAGES / f"{artifact_of_book().sha256}-{page}"
    if image.with_suffix(".png").exists():
        return
    PAGES.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "pdftoppm",
            "-q",
            "-r",
            "100",
            "-gray",
            "-png",
            "-singlefile",
            "-f",
            str(page),
            "-l",
            str(page),
            str(book()),
            str(image),
        ],
        check=True,
    )


def artifact_of_book() -> raw.Artifact:
    for url in (BOOK, SNAPSHOT):
        if url in raw.keys(SOURCE):
            return raw.cached(SOURCE, url)
    return raw.cached(SOURCE, BOOK)


def book() -> Path:
    return artifact_of_book().path


@cache
def text(page: int) -> str:
    """The page's text in reading order, one printed line per line (pages as pdftotext numbers them, from 1)."""
    return subprocess.run(
        ["pdftotext", "-q", "-raw", "-f", str(page), "-l", str(page), str(book()), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@cache
def words(page: int) -> list[Word]:
    """Every word on the page with its box in points from the page's top left."""
    out = subprocess.run(
        ["pdftotext", "-q", "-bbox", "-f", str(page), "-l", str(page), str(book()), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: list[Word] = []
    for match in WORD.finditer(out):
        if match.group(1) is None:
            size = re.search(r'width="([\d.]+)" height="([\d.]+)"', out[match.start() : match.start() + 80])
            if size is None:
                raise ValueError(f"page {page} has no size")
            SIZES[page] = (round(float(size.group(1))), round(float(size.group(2))))
            continue
        x0, y0, x1, y1 = (float(match.group(i)) for i in range(1, 5))
        found.append(Word(page, x0, y0, x1, y1, unescape(match.group(5))))
    return found


def printed(value: int) -> str:
    """A number as the volume prints it, with a space between groups of three digits."""
    return f"{value:,}".replace(",", " ")


def adjacent(left: Word, right: Word) -> bool:
    """Whether `right` follows `left` on the same printed line, a word space apart."""
    return abs(left.y0 - right.y0) < 2 and 0 <= right.x0 - left.x1 < 8


def locate(value: int, pages: tuple[int, ...]) -> tuple[int, tuple[float, float, float, float]]:
    """The first page and box where the volume prints `value`, its digit groups as consecutive words.

    The volume sometimes prints a number of four digits without its space, or with a period after the first group;
    each reading is tried.
    """
    spaced = printed(value).split(" ")
    forms = [spaced] if len(spaced) == 1 else [spaced, [str(value)]]
    for page in pages:
        found = words(page)
        for groups in forms:
            last = re.compile(re.escape(groups[-1]) + r"(?:[.,;:%)“”\"–-]+|\.\d+)?")
            for start in range(len(found) - len(groups) + 1):
                run = found[start : start + len(groups)]
                head = [w.text.lstrip("–-(").rstrip(".") for w in run[:-1]]
                if head == groups[:-1] and last.fullmatch(run[-1].text.lstrip("–-(")):
                    if (
                        start > 0
                        and adjacent(found[start - 1], run[0])
                        and re.fullmatch(r"\d{1,3}", found[start - 1].text)
                    ):
                        continue
                    after = start + len(groups)
                    if (
                        run[-1].text == groups[-1]
                        and after < len(found)
                        and adjacent(run[-1], found[after])
                        and re.match(r"\d{3}\b", found[after].text)
                    ):
                        continue
                    box = (
                        min(w.x0 for w in run),
                        min(w.y0 for w in run),
                        max(w.x1 for w in run),
                        max(w.y1 for w in run),
                    )
                    return page, box
    raise ValueError(f"the volume does not print {printed(value)} on pages {pages}")


NUM = r"(\d{1,3}(?: \d{3})*)"
NAME = r"((?:(?!^(?:№|N)?\d{1,2}\.\s).)+?)"


def only(text: str, language: str) -> str:
    """The lines of a page that prints both languages, kept for one: a line with Georgian letters is Georgian."""
    keep = []
    for line in text.split("\n"):
        georgian = len(re.findall("[\u10a0-\u10ff]", line))
        latin = len(re.findall("[A-Za-z]", line))
        if language == "en" and georgian < 3 or language == "ka" and (georgian or not latin):
            keep.append(line)
    return "\n".join(keep)


def listing(
    pages: tuple[int, ...],
    pattern: str,
    after: str | None = None,
    language: str | None = None,
    until: str | None = None,
) -> dict[int, tuple[str, int]]:
    """Entries on the pages: ballot number, the name as printed, and the figure printed after it.

    A pattern with two groups (name and figure) reads entries that carry no ballot number; they are numbered in the
    order printed.
    """
    joined = "\n".join(text(page) if language is None else only(text(page), language) for page in pages)
    joined = re.sub(r"^\d+/არჩევნების ისტორია\n|^/\d+\nELECTION HISTORY\n", "", joined, flags=re.MULTILINE)
    if after is not None:
        found = re.search(after, joined, re.DOTALL)
        if found is None:
            raise ValueError(f"pages {pages} do not print {after!r}")
        joined = joined[found.end() :]
    if until is not None:
        found = re.search(until, joined, re.DOTALL)
        if found is None:
            raise ValueError(f"pages {pages} do not print {until!r}")
        joined = joined[: found.start()]
    compiled = re.compile(pattern, re.DOTALL | re.MULTILINE)
    out: dict[int, tuple[str, int]] = {}
    for order, match in enumerate(compiled.finditer(joined), start=1):
        numbered = compiled.groups == 3
        number = int(match.group(1)) if numbered else order
        name, figure = (match.group(2), match.group(3)) if numbered else (match.group(1), match.group(2))
        name = re.sub(r"\s+", " ", name).strip(" –-,")
        name = re.sub(r"(\w)- (\w)", r"\1-\2", name)
        name = re.sub(r"“ +", "“", name)
        name = re.sub(r"(?<=\w)“", "”", name)
        out.setdefault(number, (name, int(re.sub(r"[ .]", "", figure))))
    return out


def vote_for(name: str, pages: tuple[int, ...]) -> int:
    """The vote printed after a candidate's name, as `Name – 1 234 – 5.6%`."""
    pattern = re.escape(name).replace(r"\ ", r"\s+") + r"\s*[–-]\s*" + NUM + r"\s*–\s*\d"
    for page in pages:
        match = re.search(pattern, text(page))
        if match:
            return int(match.group(1).replace(" ", ""))
    raise ValueError(f"no vote printed for {name} on pages {pages}")


def mentions(phrase: str, pages: tuple[int, ...]) -> None:
    flat = " ".join(" ".join(text(page) for page in pages).split())
    if " ".join(phrase.split()) not in flat:
        raise ValueError(f"pages {pages} do not print {phrase!r}")


# ---------------------------------------------------------------------------------------------------------------------
# The elections


@dataclass(frozen=True)
class Figure:
    measure: int
    value: int
    label: str
    """The words the volume prints with the figure on its English page, checked against the text."""


@dataclass(frozen=True)
class Lists:
    """A party-list contest: the entries are read from the English and Georgian pages and must agree."""

    english: tuple[int, ...]
    georgian: tuple[int, ...]
    pattern: str
    count: int
    magnitude: int
    system: str | None
    seats: dict[int, int]
    seat_phrases: tuple[str, ...]
    votes: bool
    blocs: frozenset[int] = frozenset()
    after: str | None = None
    """A pattern; the entries are read from the text after its first match on the pages."""
    until: str | None = None
    """A pattern; the entries end where it first matches after the start."""
    mixed: bool = False
    """The English and Georgian pages are the same pages, which print both languages."""
    differs: frozenset[int] = frozenset()
    """Entries whose Georgian text prints another figure than the English, which the printed protocol settles."""
    georgian_wins: frozenset[int] = frozenset()
    """Of those, the entries whose Georgian figure stands, because the volume's other tables agree with it."""


@dataclass(frozen=True)
class Candidate:
    english: str
    georgian: str
    printed_english: str | None = None
    """The English name as printed, where the volume misspells it; the Georgian spelling settles the English one."""


@dataclass(frozen=True)
class Office:
    office: str
    english: tuple[int, ...]
    georgian: tuple[int, ...]
    candidates: tuple[Candidate, ...]
    winner: str
    system: str | None
    votes: bool = True
    pattern: str | None = None
    """A pattern that reads the candidates (ballot number or order, name, votes) from both languages' pages."""
    count: int = 0
    winner_number: int | None = None
    advanced: tuple[int, ...] = ()
    differs: frozenset[int] = frozenset()
    after: str | None = None
    until: str | None = None
    mixed: bool = False


@dataclass(frozen=True)
class Question:
    """A referendum question with its two answers, as the volume's English and Georgian pages print them."""

    english: str
    georgian: str
    type: str
    yes: int
    no: int
    yes_phrase: str
    no_phrase: str
    text: str


@dataclass(frozen=True)
class Election:
    slug: str
    day: date
    english: str
    georgian: str
    body: str | None
    pages: tuple[int, ...]
    figures: tuple[Figure, ...]
    context: str
    method: str
    lists: Lists | None = None
    office: Office | None = None
    chair: Office | None = None
    caveat: str | None = None
    accepted: tuple[tuple[str, str], ...] = ()
    question: Question | None = None
    kind: str = "general"
    contest_kind: str = "body_seats"
    contest_name: tuple[str, str] | None = None
    unit: str = UNIT
    area: str = COUNTRY


LIST_1990 = r"^(?:№|N)(\d{1,2})\.\s+" + NAME + r"\s*–\s*" + NUM + r"\s*(?:–\s*)?\d+\.\d+ ?%"
SEATS_1992 = r"^(\d{1,2})\.\s+" + NAME + r"\s*–\s*(\d{1,2})\s+(?:mandates?|მანდატი)"
LIST_1995 = r"^(\d{1,2})\.\s+" + NAME + r"\n?" + NUM + r" \d+\.\s?\d*\s*$"
LIST_AJARA = r"^(\d{1,2})\.\s+" + NAME + r"\s*[–-]\s*\n?" + NUM + r"\s*[–-]\s*\d+\.\d+ ?%"
LIST_1999 = r"^(\d{1,2})\.\s+" + NAME + r"\s*[–-]\s*\n?" + NUM + r" – "

NUMX = r"(\d{1,3}(?:\.? \d{3})*|\d{4,})"
PCT = r"\s*[–-]\s*\d+(?:\.\d+)?\s?(?:%|(?=,))"
SEP = r"\s*[–-]\s*(?:[–-]\s*)?\n?"
NAME_NO = r"((?:(?!^(?:№|N)\s?\d{1,2}[^\d]).)+?)"
LIST_DOT = r"^(\d{1,2})\.\s+" + NAME + SEP + NUMX + PCT
"""Numbered `1. Name – 1 234 – 5.6%` entries; the figure may be printed without its space or after a hyphen."""
LIST_NO = r"^(?:№|N)\s?(\d{1,2})\s*" + NAME_NO + SEP + NUMX + PCT
"""`№1 Name – 1 234 – 5.6%` entries, with the ballot number the Commission assigned."""
LINE_VOTES = r"^([^\n–]+?)\s*[–-]\s*" + NUMX + PCT
"""Unnumbered `Name – 1 234 – 5.6%` lines, numbered in the order printed."""
MANDATES = r"^([^\n]*?(?:\n[^\n]*?)?)\s*[–-]\s*\n?(\d{1,4})\s+(?:mandates?|მანდატი)"
"""Unnumbered `Name – 12 mandates` lines, numbered in the order printed."""

SHEVARDNADZE = Candidate("Eduard Shevardnadze", "ედუარდ შევარდნაძე")
PATIASHVILI = Candidate("Jumber Patiashvili", "ჯუმბერ პატიაშვილი")
GHARIBASHVILI = Candidate("Kartlos Gharibashvili", "ქართლოს ღარიბაშვილი")

ELECTIONS = (
    Election(
        "ge/1990-10-28-supreme-council",
        date(1990, 10, 28),
        "1990 Georgian Supreme Council election",
        "საქართველოს სსრ უზენაესი საბჭოს 1990 წლის 28 ოქტომბრის არჩევნები",
        SUPREME_COUNCIL,
        (27, 28),
        (
            Figure(REGISTERED, 3_444_002, "out of 3 444 002 registered"),
            Figure(VOTERS, 2_406_742, "2 406 742 voters out of"),
            Figure(VALID, 2_311_923, "2 311 923 voters took part in the polling"),
            Figure(INVALID, 84_797, "84 797 ballots"),
        ),
        "The first multiparty election of the Supreme Council of the Georgian SSR. Of its 250 members, 125 were "
        "elected from party lists across the republic with a 4% threshold and 125 in single-member districts, where "
        "a candidate needed more than half of the votes; 59 districts held a runoff on 11 November 1990. The volume "
        "reports the majoritarian seats only as totals: Round Table – Free Georgia 43, Communist Party 17, People's "
        "Front 11, Democratic Georgia 2, the Shota Rustaveli Society 1, and independents the rest. Those district "
        "contests are not stored. The Council first met on 14 November 1990 and elected Zviad Gamsakhurdia its "
        "chairman.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol, as printed "
        "in the Commission's volume Electoral History of Georgia 1990-2018 (pages 26-27), which cites the newspaper "
        "Komunisti of 7 November 1990. The volume prints 2,311,923 as the voters who took part in the polling; it "
        "equals the sum of the list votes, so it is stored as the valid votes. The volume does not state the seat "
        "formula; the stored rules give the 4% threshold.",
        lists=Lists(
            (28,),
            (27,),
            LIST_1990,
            11,
            125,
            SYSTEM_1990,
            {12: 81, 3: 44},
            ("“Round Table – Free Georgia” received 81 mandates", "the Communist Party of Georgia – 44"),
            True,
            frozenset({12, 14}),
        ),
    ),
    Election(
        "ge/1991-03-31-referendum",
        date(1991, 3, 31),
        "1991 Georgian independence referendum",
        "საქართველოს დამოუკიდებლობის აღდგენის 1991 წლის 31 მარტის რეფერენდუმი",
        None,
        (37, 39),
        (
            Figure(REGISTERED, 3_672_403, "The total number of voters in the Republic of Georgia was 3 672 403"),
            Figure(VOTERS, 3_334_286, "3 334 286 citizens took part in referendum"),
            Figure(BALLOTS, 3_326_100, "3 326 100 citizens took part in voting"),
            Figure(INVALID, 13_690, "13 690 ballots were announced invalid"),
        ),
        "The referendum of 31 March 1991 on the restoration of Georgia's independence; Georgia declared its "
        "independence on 9 April 1991. Polling stations opened across the republic except in the Java, Kornisi, and "
        "Gudauta districts, the city of Tkvarcheli, the city of Tskhinvali, and part of the Tskhinvali district. In "
        "the former South Ossetian Autonomous Region the vote was held only in seven village councils of the "
        "Tskhinvali district, where 10,182 voters took part and 10,180 answered yes. The volume "
        "prints the turnout and the shares of yes votes in the Autonomous Republic of Abkhazia and the turnout in "
        "Ajara (96.64%) only as percentages, so no regional figure is stored.",
        "National totals from the Central Referendum Commission's session of 4 April 1991, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (page 39), which cites the newspaper "
        "Sakartvelos Respublika of 5 April 1991. The question is printed on page 37. The volume prints 3,326,100 as the citizens who took part in the "
        "voting, with 90.57% of the electorate, which agrees with that share. The yes votes, the no votes, and the "
        "13,690 invalid ballots add up to 3,236,100, 90,000 fewer than the voting participants. The figures are "
        "stored as printed.",
        question=Question(
            "1991 independence referendum",
            "დამოუკიდებლობის აღდგენის რეფერენდუმი",
            "referendum",
            3_205_493,
            16_917,
            "3 205 493 citizens answered the referendum question “yes”",
            "16 917 citizens answered the referendum question “no”",
            "“Do you agree to restore Georgia’s independence under the Independence Act of 26 May 1918?”",
        ),
        kind="referendum",
    ),
    Election(
        "ge/1991-05-26-president",
        date(1991, 5, 26),
        "1991 Georgian presidential election",
        "საქართველოს პრეზიდენტის 1991 წლის 26 მაისის არჩევნები",
        None,
        (48,),
        (
            Figure(REGISTERED, 3_594_810, "of 3 594 810 voters registered in the lists"),
            Figure(VOTERS, 2_978_247, "2 978 247 voters came to the polling stations"),
            Figure(CAST, 2_968_945, "2 968 945 voters cast their votes"),
            Figure(INVALID, 39_918, "39 918 ballots were deemed invalid"),
        ),
        "The first popular election of the President of the Republic of Georgia, held after the restoration of "
        "independence on 9 April 1991. Zviad Gamsakhurdia won in the first round.",
        "National totals from the Central Election Commission's summary protocol of 3 June 1991, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (page 47). The volume does not print the valid "
        "votes; the candidates' votes and the invalid ballots add up to the ballots cast.",
        office=Office(
            PRESIDENT,
            (48,),
            (48,),
            (
                Candidate("Zviad Gamsakhurdia", "ზვიად გამსახურდია"),
                Candidate("Irakli Shengelaia", "ირაკლი შენგელაია"),
                Candidate("Jemal Mikeladze", "ჯემალ მიქელაძე"),
                Candidate("Valerian Advadze", "ვალერიან ადვაძე"),
                Candidate("Tamaz Kvachantiradze", "თამაზ კვაჭანტირაძე", "Tamaz Kvatchantiradze"),
                Candidate("Nodar Natadze", "ნოდარ ნათაძე"),
            ),
            "Zviad Gamsakhurdia",
            SYSTEM_PRESIDENT,
        ),
    ),
    Election(
        "ge/aj/1991-06-23-general",
        date(1991, 6, 23),
        "1991 Ajara Supreme Council election",
        "აჭარის ავტონომიური რესპუბლიკის უზენაესი საბჭოს 1991 წლის 23 ივნისის არჩევნები",
        AJARA_BODY,
        (53, 54),
        (
            Figure(REGISTERED, 228_749, "of 228 749 registered voters"),
            Figure(VOTERS, 137_817, "137 817 came to the polling station"),
            Figure(INVALID, 11_298, "11 298 of them were considered invalid"),
        ),
        "The first multiparty election of the Supreme Council of the Autonomous Republic of Ajara, held under the "
        "law of 4 January 1991 after several postponements. The Council had 80 members: 40 elected from party lists "
        "across the republic with a 4% threshold and 40 in single-member districts, where a candidate needed more "
        "than half of the votes and at least half of the voters had to take part. Eleven districts could not vote "
        "because fewer than half of the voters came, and 24 of the 29 that did vote needed a second round; repeat "
        "elections and runoffs were held on 10 July 1991. The volume prints no district results, so the "
        "single-member districts are not stored. The ruling bloc Round Table – Free Georgia, led by Aslan "
        "Abashidze, won the most list votes and seats.",
        "National totals of the party-list vote from the Central Election Commission of the Autonomous Republic's "
        "summary protocol of 26 June 1991, as printed in the Commission's volume Electoral History of Georgia "
        "1990-2018 (pages 52-53), which cites the newspapers Adjara and Sakartvelos Respublika of 26 June 1991. "
        "The volume prints the votes of six of the seven lists: the list numbered 5, the Georgian Youth Union, has "
        "no figure, and the volume's percentages are shares of the six lists' 126,176 votes. It prints 137,185 as "
        "the voters who directly took part, which is not the 137,817 who came to the polls less the 11,298 invalid "
        "ballots (126,519); that figure is not stored. The seat formula is not stated; the declared seats follow "
        "the D'Hondt method among the five lists above the threshold.",
        lists=Lists(
            (54,),
            (53,),
            LIST_AJARA,
            6,
            40,
            SYSTEM_AJARA_1991,
            {1: 8, 2: 21, 3: 7, 4: 2, 7: 2},
            (
                "Election bloc “Adjara” – 8 mandates",
                "Election bloc “Round Table – Free Georgia” regional organization in Adjara – 21 mandates",
                "Independent Communist Party of Georgia’s district organization in Adjara - 7 mandates",
                "Election bloc “Economic Revival” – 2 mandates",
                "Khulo organizations – 2 mandates",
            ),
            True,
            frozenset({1, 2, 4}),
        ),
        contest_kind="list_allocation",
        contest_name=("1991 Supreme Council of Ajara, proportional seats", "უზენაესი საბჭოს პროპორციული არჩევნები"),
        unit=AJARA_UNIT,
        area=AJARA_AREA,
    ),
    Election(
        "ge/1992-10-11-parliament",
        date(1992, 10, 11),
        "1992 Georgian parliamentary election",
        "საქართველოს პარლამენტის 1992 წლის 11 ოქტომბრის არჩევნები",
        PARLIAMENT,
        (75, 76),
        (
            Figure(REGISTERED, 3_471_866, "The total number of voters – 3 471 866"),
            Figure(VOTERS, 2_575_197, "The number of participants in elections – 2 575 197"),
            Figure(INVALID, 50_399, "The number of invalid ballot papers – 50 399"),
        ),
        "Parliament was elected after the State Council took power in 1992: 150 members in ten multi-member "
        "districts, where each voter could mark preferences and seats were divided by a quota in each district, and "
        "84 in single-member districts. The vote was postponed in nine single-member districts (Java, Tskhinvali, "
        "Zugdidi, Tsalenjikha, Chkhorotsku, Gagra, Gali, Gudauta, and Tkvarcheli). The same day, voters elected the "
        "Chairman of Parliament, who was also head of state; Eduard Shevardnadze was the only candidate. The volume "
        "reports the single-member districts only as a count, and the multi-member districts only as the seats each "
        "party won across all ten, so neither the district contests nor the list votes are stored.",
        "The seats each party won across the ten multi-member districts, from the Central Election Commission's "
        "summary protocol of 27 October 1992, as printed in the Commission's volume Electoral History of Georgia "
        "1990-2018 (pages 74-75). The volume prints no votes for the lists, so this result records seats only.",
        lists=Lists(
            (76,),
            (75,),
            SEATS_1992,
            24,
            150,
            None,
            {},
            ("the 150 mandates according to the multi-member districts",),
            False,
            frozenset({6, 12, 20, 22}),
        ),
        chair=Office(
            CHAIRMAN,
            (76,),
            (75,),
            (SHEVARDNADZE,),
            "Eduard Shevardnadze",
            None,
        ),
        caveat="The seats are the sums over the ten multi-member districts as the summary protocol gives them; the "
        "district results are not stored.",
    ),
    Election(
        "ge/1995-11-05-parliament",
        date(1995, 11, 5),
        "1995 Georgian parliamentary election",
        "საქართველოს პარლამენტის 1995 წლის 5 ნოემბრის არჩევნები",
        PARLIAMENT,
        (88, 89, 90, 91, 92, 93),
        (
            Figure(REGISTERED, 3_121_075, "The total number of voters was 3 121 075"),
            Figure(VOTERS, 2_127_946, "2 127 946 voters took part in elections"),
            Figure(CAST, 2_079_462, "2 079 462 voters took part in polling"),
            Figure(INVALID, 113_588, "113 588 (5%)"),
        ),
        "The first election under the Constitution of 24 August 1995: 150 members from party lists across the country "
        "with a 5% threshold, and 85 in single-member districts. The vote was held in 77 of the 86 districts and "
        "failed in Rustavi, which voted again on 2 June 1996; runoffs were held on 19 November and 3 December 1995. "
        "The volume gives the majoritarian seats only as totals by party (Citizens' Union of Georgia 19, independents "
        "29, and others), so those district contests are not stored. The presidential election was held the same "
        "day.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 16 November "
        "1995, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 87-91). The volume "
        "prints list 29 nowhere. The list votes and the invalid ballots add up to 2,116,831, between the 2,079,462 "
        "voters the protocol says took part in the polling and the 2,127,946 who took part in the election; the "
        "volume's percentages are shares of 2,127,946. The figures are stored as printed.",
        lists=Lists(
            (90, 92),
            (89, 91),
            LIST_1995,
            53,
            150,
            SYSTEM_1995,
            {45: 90, 26: 31, 6: 25},
            ("Citizen’s Union of Georgia – 90 mandates", "(NDP) – 31 mandates", "Union for Revival – 25 mandates"),
            True,
        ),
        caveat="The summary protocol, as the volume prints it, gives 146 of the 150 list seats to the three lists "
        "above the threshold (90, 31, and 25); the volume does not account for the other four.",
        accepted=(
            (
                "outcomes.seats_magnitude",
                (
                    "The CEC summary protocol of 16 November 1995, as printed in the Electoral History of Georgia, "
                    "declares 90 + 31 + 25 = 146 of the 150 list seats"
                ),
            ),
        ),
    ),
    Election(
        "ge/1995-11-05-president",
        date(1995, 11, 5),
        "1995 Georgian presidential election",
        "საქართველოს პრეზიდენტის 1995 წლის 5 ნოემბრის არჩევნები",
        None,
        (99,),
        (
            Figure(REGISTERED, 3_106_557, "The total number of voters in Presidential Elections was 3 106 557"),
            Figure(VOTERS, 2_139_369, "The total number of participants in elections was 2 139 369"),
            Figure(CAST, 2_121_510, "The number of participants in polling was 2 121 510"),
            Figure(INVALID, 57_280, "The number of invalid ballot papers was 57 280"),
        ),
        "The first election of the President under the Constitution of 24 August 1995, which restored the office "
        "suspended in 1992, held with the parliamentary election. Eduard Shevardnadze won in the first round.",
        "National totals from the Central Election Commission's summary protocol of 14 November 1995, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (page 98). The volume does not print the "
        "valid votes; the candidates' votes and the invalid ballots add up to the ballots cast.",
        office=Office(
            PRESIDENT,
            (99,),
            (99,),
            (
                Candidate("Roin Liparteliani", "როინ ლიპარტელიანი"),
                Candidate("Akaki Bakradze", "აკაკი ბაქრაძე"),
                PATIASHVILI,
                Candidate("Panteleimon Giorgadze", "პანტელეიმონ გიორგაძე"),
                SHEVARDNADZE,
                GHARIBASHVILI,
            ),
            "Eduard Shevardnadze",
            SYSTEM_PRESIDENT,
        ),
    ),
    Election(
        "ge/aj/1996-09-22-general",
        date(1996, 9, 22),
        "1996 Ajara Supreme Council election",
        "აჭარის ავტონომიური რესპუბლიკის უზენაესი საბჭოს 1996 წლის 22 სექტემბრის არჩევნები",
        AJARA_BODY,
        (104, 105),
        (Figure(REGISTERED, 234_095, "234 095 voters were registered"),),
        "The election of the 80-member Supreme Council of the Autonomous Republic of Ajara: 40 members from party "
        "lists across the republic with a 5% threshold and 40 in single-member districts. 93.6% of the registered "
        "voters came to the polls. The list Revival and Citizens' Union, led by Aslan Abashidze, won 38 of the 40 "
        "list seats and most of the districts; 39 of the 40 districts elected their member in the first round, and "
        "the Batumi Shavsheti district No. 3 voted again. The volume prints no district results, so the "
        "single-member districts are not stored. Five women and three members of ethnic minorities entered the "
        "Council.",
        "National totals of the party-list vote from the Central Election Commission of the Autonomous Republic's "
        "summary protocol, approved on 25 September 1996 and printed in the newspaper Adjara on 27 September 1996, "
        "as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 103-104). The volume's "
        "text prints the votes of eleven of the twelve lists; the list numbered 1 appears only in the photograph "
        "of the protocol, which this result does not read, so its votes are not stored. The seat formula is not "
        "stated; the declared seats follow the Hare quota with largest remainders among the two lists above the "
        "threshold.",
        lists=Lists(
            (105,),
            (104,),
            LIST_AJARA,
            11,
            40,
            SYSTEM_AJARA_1996,
            {6: 38, 4: 2},
            (
                "Election Bloc “Revival and Citizen’s Union” – 38 mandates",
                "United Communist Party’s regional organization in Adjara – 2 mandates",
            ),
            True,
            frozenset({5, 6, 10, 12}),
        ),
        contest_kind="list_allocation",
        contest_name=("1996 Supreme Council of Ajara, proportional seats", "უზენაესი საბჭოს პროპორციული არჩევნები"),
        unit=AJARA_UNIT,
        area=AJARA_AREA,
    ),
    Election(
        "ge/1999-10-31-parliament",
        date(1999, 10, 31),
        "1999 Georgian parliamentary election",
        "საქართველოს პარლამენტის 1999 წლის 31 ოქტომბრის არჩევნები",
        PARLIAMENT,
        (120, 122),
        (Figure(VOTERS, 2_133_878, "2 133 878 voters came to polling stations"),),
        "Parliament was elected under the law of 1 September 1995 with the list threshold raised to 7%: 150 members "
        "from party lists across the country and the rest in single-member districts. 49 district members were "
        "elected in the first round, and runoffs were held on 28 November 1999. The volume names no district "
        "results, so those contests are not stored.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 7 November "
        "1999, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 118-121). The "
        "volume prints the voters who came to the polls but not the electorate or the invalid ballots, and lists "
        "only the 33 lists still on the ballot. The Supreme Court later corrected the results of some precincts "
        "slightly; the volume reports that the corrections changed nothing of substance, and these are the "
        "protocol's figures.",
        lists=Lists(
            (122,),
            (119, 121),
            LIST_1999,
            33,
            150,
            SYSTEM_1999,
            {1: 85, 2: 51, 7: 14},
            ("Citizen’s Union of Georgia – 85 mandates", "– 51 mandates", "– 14 mandates"),
            True,
        ),
    ),
    Election(
        "ge/2000-04-09-president",
        date(2000, 4, 9),
        "2000 Georgian presidential election",
        "საქართველოს პრეზიდენტის 2000 წლის 9 აპრილის არჩევნები",
        None,
        (128, 129),
        (
            Figure(REGISTERED, 3_088_925, "out of 3 088 925 registered voters"),
            Figure(VOTERS, 2_343_176, "2 343 176 citizens participated in the elections"),
        ),
        "Eduard Shevardnadze was re-elected in the first round. Aslan Abashidze withdrew the day before the vote, and "
        "24 parties boycotted the election.",
        "National totals from the Central Election Commission's summary protocol of 19 April 2000, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (pages 127-128). The volume prints neither the "
        "valid votes nor the invalid ballots. The Commission annulled the results of six precincts.",
        office=Office(
            PRESIDENT,
            (129,),
            (129,),
            (
                SHEVARDNADZE,
                Candidate("Avtandil Joglidze", "ავთანდილ ჯოგლიძე"),
                Candidate("Vazha Zhghenti", "ვაჟა ჟღენტი", "Vazha Zhenti"),
                Candidate("Tengiz Asanidze", "თენგიზ ასანიძე", "Tengiz Asandze"),
                GHARIBASHVILI,
                PATIASHVILI,
            ),
            "Eduard Shevardnadze",
            SYSTEM_PRESIDENT,
        ),
    ),
    Election(
        "ge/aj/2001-11-04-general",
        date(2001, 11, 4),
        "2001 Ajara Parliament election",
        "აჭარის ავტონომიური რესპუბლიკის პარლამენტის 2001 წლის 4 ნოემბრის არჩევნები",
        AJARA_BODY,
        (133,),
        (),
        "The first election after the autonomous republic renamed its Supreme Council the Parliament and divided it "
        "into two chambers: a Republican Council of 35 members elected by the proportional system with a 7% "
        "threshold, and a Senate of 7 members elected in single-member districts. The same day the republic elected "
        "its Chair; the volume reports that Aslan Abashidze won with 99.3% of the voters' support and that the "
        "Union of Revival won all seven Senate districts, some with more than 99.9%. Seven subjects ran, some "
        "parties boycotted the election, and the Citizen's Union of Georgia did not register. Turnout was 96%, which "
        "the opposition attributed to ballots added to the boxes. The volume prints no district results and no "
        "votes for the Chair, so those contests are not stored.",
        "Votes and seats of the proportional vote for the Republican Council from the final results of the Central "
        "Election Commission of the Autonomous Republic, as printed in the Commission's volume Electoral History of "
        "Georgia 1990-2018 (page 133). The volume prints neither the electorate nor the voters.",
        lists=Lists(
            (133,),
            (133,),
            LIST_DOT,
            7,
            35,
            None,
            {1: 30, 6: 3, 7: 2},
            (
                "Union of Revival – 30 mandates",
                "“Industry Saves Georgia” – three mandates",
                "National Democratic Party – two mandates",
            ),
            True,
            mixed=True,
        ),
        contest_kind="list_allocation",
        contest_name=("2001 Parliament of Ajara, proportional seats", "პარლამენტის პროპორციული არჩევნები"),
        unit=AJARA_UNIT,
        area=AJARA_AREA,
    ),
    Election(
        "ge/2002-06-02-local",
        date(2002, 6, 2),
        "2002 Georgian local self-government elections",
        "საქართველოს ადგილობრივი თვითმმართველობის ორგანოების 2002 წლის 2 ივნისის არჩევნები",
        LOCAL_COUNCILS,
        (140, 141),
        (),
        "The elections of the sakrebulos (councils) of cities, districts, and rural communities, and of the mayors and "
        "gamgebelis of more than forty cities, held on 2 June 2002; 4,801 members were to be elected outside the "
        "conflict regions. Elections failed in Zugdidi, Rustavi, Khashuri, and Gori, and repeat votes were scheduled "
        "for 16 June 2002. Ajara voted on 16 June 2002. According to the Commission's protocol of 24 June 2002, 4,750 "
        "of the 4,801 seats were filled, and 58% of the seats (2,754) went to candidates nominated by initiative "
        "groups; the seats of 15 parties and blocs are stored.",
        "Seats won by parties and blocs across Georgia, from the Central Election Commission's 136-page summary "
        "protocol of 24 June 2002, as printed in the Commission's volume Electoral History of Georgia 1990-2018 "
        "(pages 138-140). The volume prints the votes only for the Tbilisi sakrebulo. The seats of the initiative "
        "groups, the mayors, and the gamgebelis are not printed by party and are not stored, so the seats stored do "
        "not add up to 4,750.",
        lists=Lists(
            (141,),
            (139,),
            MANDATES,
            15,
            0,
            None,
            {},
            (
                "New Rights – 551 mandates",
                "Socialist Party – 189 mandates",
                "Citizen’s Union of Georgia – 70 mandates",
                "United Communist Party and Workers’ Councils Bloc – 1 mandate",
            ),
            False,
        ),
        contest_name=("2002 sakrebulo seats by party", "საკრებულოთა მანდატები პარტიების მიხედვით"),
        kind="local",
    ),
    Election(
        "ge/2003-11-02-parliament",
        date(2003, 11, 2),
        "2003 Georgian parliamentary election",
        "საქართველოს პარლამენტის 2003 წლის 2 ნოემბრის არჩევნები",
        PARLIAMENT,
        (150,),
        (
            Figure(REGISTERED, 3_178_593, "registered voters stood at 3 178 593"),
            Figure(VOTERS, 1_909_215, "1 909 215 voters took part in elections"),
            Figure(INVALID, 65_641, "65 641 ballot papers were annulled"),
        ),
        "The election of the 235-member Parliament under the law of 1 September 1995 as amended in 1999: 150 members "
        "from party lists across the country with a 7% threshold and the rest in single-member districts. The "
        "Commission annulled the data of more than 70 polling stations and issued the summary protocol on 20 "
        "November 2003 amid protests by the opposition. President Shevardnadze resigned on 23 November 2003, and the "
        "Supreme Court annulled the results of the party-list vote, which was held again on 28 March 2004; the "
        "single-member results stood. Results were annulled in three districts. The volume reports the district "
        "seats by party: New Georgia 19, Saakashvili – National Movement 10, Democratic Union for Revival 6, "
        "Burjanadze – Democrats 4, Industry Saves Georgia 4, Labour Party 3, and independents 21. It names no "
        "district results, so those contests are not stored.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 20 "
        "November 2003, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages "
        "146-149). The volume prints the electorate, the voters, and the annulled ballots, but not the valid votes.",
        lists=Lists(
            (150,),
            (147, 148, 149),
            LIST_DOT,
            21,
            150,
            None,
            {1: 38, 2: 33, 4: 20, 5: 32, 7: 15, 12: 12},
            (
                "1. “New Georgia” bloc – 38 mandates",
                "2. Democrat Union for Revival – 33 mandates",
                "4. Labour Party – 20 mandates",
                "5. “Saakashvili – National Movement” bloc – 32 mandates",
                "7. “Burjanadze – Democrats” bloc – 15 mandates",
                "12. “New Rights” bloc – 12 mandates",
            ),
            True,
        ),
        caveat=(
            "The Supreme Court of Georgia annulled the results of this party-list vote and the vote was repeated on "
            "28 March 2004, so these seats were never taken up. The volume prints the votes of "
            "the Burjanadze – Democrats bloc as 197 908 beside a share of 8.79%, which does not agree; the votes are "
            "stored as printed."
        ),
        accepted=(),
    ),
    Election(
        "ge/2003-11-02-referendum",
        date(2003, 11, 2),
        "2003 Georgian referendum on the size of Parliament",
        "საქართველოს პარლამენტის წევრთა რაოდენობის შემცირების 2003 წლის 2 ნოემბრის რეფერენდუმი",
        None,
        (153,),
        (
            Figure(REGISTERED, 3_178_593, "The total number of voters was 3 178 593"),
            Figure(VOTERS, 2_031_057, "The number of participants in the referendum was 2 031 057"),
            Figure(INVALID, 123_466, "The number of invalid ballot papers was 123 466"),
        ),
        "The referendum held with the parliamentary election on 2 November 2003, called by the President's order of 2 "
        "September 2003; the vote could not be held in the parts of the former Autonomous Republic of Abkhazia and "
        "the former South Ossetian Autonomous Region that the central government did not control. If approved, the "
        "reduction was to take effect with the next convocation of Parliament. The results were not appealed and "
        "stood when the party-list vote was annulled. In 2007 Parliament cut its membership from 235 to 150 by a "
        "constitutional amendment.",
        "National totals from the Central Election Commission's summary protocol of 20 November 2003, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 152-153). The yes votes, the no votes, "
        "and the invalid ballots add up to 1,897,984, which is 133,073 fewer than the participants the volume "
        "prints. The figures are stored as printed.",
        question=Question(
            "2003 referendum on the size of Parliament",
            "პარლამენტის წევრთა რაოდენობის შემცირების რეფერენდუმი",
            "referendum",
            1_590_309,
            184_209,
            "The number of citizens with a positive answer on question – 1 590 309",
            "The number of citizens with a negative answer on question – 184 209",
            "“Do you agree that the number of members of the Parliament of Georgia should be reduced and determined "
            "by no more than 150 deputies?”",
        ),
        kind="referendum",
    ),
    Election(
        "ge/2004-01-04-president",
        date(2004, 1, 4),
        "2004 Georgian presidential election",
        "საქართველოს პრეზიდენტის 2004 წლის 4 იანვრის არჩევნები",
        None,
        (157,),
        (
            Figure(REGISTERED, 2_231_986, "The total number of voters was 2 231 986"),
            Figure(VOTERS, 1_963_556, "1 963 556 voters participated in elections"),
            Figure(INVALID, 13_571, "13 571 ballot papers were deemed invalid"),
        ),
        "The extraordinary election held after the resignation of Eduard Shevardnadze on 23 November 2003; Nino "
        "Burjanadze, as acting President, set the date, and the Commission, which had organized the annulled "
        "parliamentary election, was dissolved and reformed. The voter lists were revised and shrank by almost "
        "900,000 names. Six candidates ran; the election failed entirely in the Kodori Gorge. Mikheil Saakashvili won "
        "in the first round.",
        "National totals from the Central Election Commission's summary protocol of 15 January 2004, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 155-156). The volume does not print "
        "the valid votes; the candidates' votes and the invalid ballots add up to 1,956,254, which is 7,302 fewer than "
        "the voters it prints. The figures are stored as printed.",
        office=Office(
            PRESIDENT,
            (157,),
            (156,),
            (),
            "Mikheil Saakashvili",
            None,
            pattern=LINE_VOTES,
            count=6,
        ),
    ),
    Election(
        "ge/2004-03-28-parliament",
        date(2004, 3, 28),
        "2004 Georgian parliamentary election",
        "საქართველოს პარლამენტის 2004 წლის 28 მარტის არჩევნები",
        PARLIAMENT,
        (163,),
        (
            Figure(REGISTERED, 2_343_087, "unified list was 2 343 087"),
            Figure(VOTERS, 1_498_012, "1 498 012 voters participated in elections"),
            Figure(INVALID, 33_329, "33 329 ballots were deemed invalid"),
        ),
        "The repeat of the party-list vote of 2 November 2003, which the Supreme Court had annulled: 150 members were "
        "elected by proportional representation with a 7% threshold, and the single-member results of 2003 stood. "
        "The Citizen's Union of Georgia no longer ran. The Commission revoked the results of all polling stations in "
        "the Kobuleti and Khulo districts and annulled about 50 other polling stations; the vote in the Kodori Gorge "
        "was postponed to 8 April 2004. Only the National Movement – Democrats and the Rightist Opposition – "
        "Industrialists, New Rightists bloc crossed the threshold.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 18 April "
        "2004, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 159-163). The "
        "volume prints no votes for the United Communist Party of Georgia, list 6 (it won less than 0.01% in the "
        "preliminary results), so that list is not stored. It does not print the valid votes.",
        lists=Lists(
            (163,),
            (161, 162),
            LIST_DOT,
            16,
            150,
            None,
            {5: 135, 3: 15},
            (
                "National Movement – Democrats – 135 mandates",
                "Industrialists, New Rightists” Bloc – 15 mandates",
            ),
            True,
        ),
    ),
    Election(
        "ge/aj/2004-06-20-general",
        date(2004, 6, 20),
        "2004 Ajara Supreme Council election",
        "აჭარის ავტონომიური რესპუბლიკის უზენაესი საბჭოს 2004 წლის 20 ივნისის არჩევნები",
        AJARA_BODY,
        (167,),
        (
            Figure(REGISTERED, 158_421, "The total number of voters was 158 421"),
            Figure(VOTERS, 120_025, "120 025 voters participated in elections"),
            Figure(INVALID, 4_369, "4 369 ballots were annulled"),
        ),
        "The first election after Aslan Abashidze resigned and left for Russia on 6 May 2004. Under the law the "
        "interim presidential council adopted on 22 May 2004, a unicameral Supreme Council of 30 members replaced "
        "the two-chamber parliament of 2001: 18 members from party lists with a 7% threshold and 12 in single-member "
        "districts. The Supreme Election Commission replaced the republic's Central Election Commission. Turnout was "
        "75.8%. The protocol reports that the candidates of Saakashvili – Victorious Adjara won all 12 districts, and "
        "that two lists, Saakashvili – Victorious Adjara and Berdzenishvili – Republicans, crossed the threshold; it "
        "prints no list seats, so none are stored. The results of two polling stations were annulled.",
        "National totals of the party-list vote from the summary protocol of the Supreme Election Commission of the "
        "Autonomous Republic of Adjara of 27 June 2004, as printed in the Commission's volume Electoral History of "
        "Georgia 1990-2018 (pages 165-166). The volume prints no votes for list 8, the Green Party of Georgia (0.08% "
        "of the vote), so that list is not stored. The votes printed add up to more than the voters less the "
        "annulled ballots, and are stored as printed.",
        lists=Lists(
            (167,),
            (165, 166),
            LIST_DOT,
            9,
            18,
            None,
            {},
            (),
            True,
        ),
        contest_kind="list_allocation",
        contest_name=("2004 Supreme Council of Ajara, proportional seats", "უზენაესი საბჭოს პროპორციული არჩევნები"),
        unit=AJARA_UNIT,
        area=AJARA_AREA,
    ),
    Election(
        "ge/2008-01-05-president",
        date(2008, 1, 5),
        "2008 Georgian presidential election",
        "საქართველოს პრეზიდენტის 2008 წლის 5 იანვრის რიგგარეშე არჩევნები",
        None,
        (180,),
        (
            Figure(REGISTERED, 3_527_964, "out of 3 527 964 registered voters"),
            Figure(VOTERS, 1_982_318, "1 982 318 citizens participated in the extraordinary elections"),
            Figure(INVALID, 33_129, "33 129 ballots were annulled"),
        ),
        "The extraordinary election called after President Saakashvili resigned following the events of 7 November "
        "2007, when a state of emergency was declared and independent television stations were shut down; Nino "
        "Burjanadze acted as President. Seven candidates were registered. Voting took place on 28 December 2007 in "
        "some highland areas and on 5 January 2008 elsewhere; turnout was 56.2%. Mikheil Saakashvili was re-elected "
        "in the first round. The opposition candidates protested the result, and the Commission upheld some "
        "complaints and made minor changes to the protocol.",
        "National totals from the Central Election Commission's summary protocol of 13 January 2008, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 178-179, which reproduces the "
        "protocol). The volume does not print the valid votes. The Georgian text prints the votes of six candidates "
        "slightly differently (for example 1 059 647 for Saakashvili); the English text and the reproduced protocol "
        "agree, and those figures are stored.",
        office=Office(
            PRESIDENT,
            (180,),
            (179,),
            (),
            "Mikheil Saakashvili",
            None,
            pattern=LIST_DOT,
            count=7,
            winner_number=5,
            differs=frozenset({1, 2, 3, 4, 5, 6}),
        ),
    ),
    Election(
        "ge/2008-01-05-plebiscite-nato",
        date(2008, 1, 5),
        "2008 Georgian plebiscite on joining NATO",
        "საქართველოს ნატოში გაწევრიანების 2008 წლის 5 იანვრის პლებისციტი",
        None,
        (182,),
        (
            Figure(REGISTERED, 3_527_964, "3 527 964 voters were registered"),
            Figure(VOTERS, 1_982_318, "1 982 318 citizens participated in"),
        ),
        "A plebiscite held with the extraordinary presidential election. Because the central government did not "
        "control parts of the former Autonomous Republic of Abkhazia and the former South Ossetian Autonomous Region, "
        "a constitutional referendum on the whole territory was impossible, and the authorities were to treat the "
        "outcome as a recommendation. Turnout exceeded 56%.",
        "National totals from the Central Election Commission's summary protocol of 15 January 2008, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 181-182). The volume prints the yes "
        "and no votes with their shares of the participants (77% and 23%) and no invalid ballots.",
        question=Question(
            "2008 plebiscite on joining NATO",
            "ნატოში გაწევრიანების პლებისციტი",
            "plebiscite",
            1_355_328,
            404_943,
            "1 355 328 voters answered positively",
            "404 943 citizens or 23%",
            "“Do you support Georgia’s accession to the North Atlantic Treaty Organization (NATO)?”",
        ),
        kind="referendum",
    ),
    Election(
        "ge/2008-01-05-plebiscite-election-date",
        date(2008, 1, 5),
        "2008 Georgian plebiscite on the date of the parliamentary election",
        "საქართველოს პარლამენტის არჩევნების ვადის შესახებ 2008 წლის 5 იანვრის პლებისციტი",
        None,
        (184,),
        (Figure(VOTERS, 1_982_318, "1 982 318 voters took part in the plebiscite"),),
        "A plebiscite held with the extraordinary presidential election on whether the next parliamentary election "
        "should take place in the spring of 2008 rather than in October, as the amended Constitution required after "
        "the Parliament elected in November 2003 and March 2004 reached the end of its four-year term. Opposition "
        "groups had demanded a vote by early 2008. No referendum or other poll was legally needed, so the government "
        "held a plebiscite.",
        "National totals from the Central Election Commission's summary protocol of 15 January 2008, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 183-184). The volume prints only the "
        "voters, the yes votes, and the no votes, and gives the yes votes as 79.74% of the electorate; the share "
        "agrees with the participants, not the electorate.",
        question=Question(
            "2008 plebiscite on the date of the parliamentary election",
            "პარლამენტის არჩევნების ვადის შესახებ პლებისციტი",
            "plebiscite",
            1_410_269,
            358_328,
            "1 410 269 voters, or 79.74% of the electorate, supported",
            "only 358 328 denied",
            "“Do you agree that the next Parliamentary Elections in Georgia will be held in the spring of 2008?”",
        ),
        kind="referendum",
    ),
    Election(
        "ge/2008-05-21-parliament",
        date(2008, 5, 21),
        "2008 Georgian parliamentary election",
        "საქართველოს პარლამენტის 2008 წლის 21 მაისის არჩევნები",
        PARLIAMENT,
        (192,),
        (
            Figure(REGISTERED, 3_465_736, "out of 3 465 736 citizens registered"),
            Figure(VOTERS, 1_850_407, "1 850 407 voters out of"),
            Figure(INVALID, 56_077, "56 077 ballot papers were deemed invalid"),
        ),
        "The first election after the change to a 150-member Parliament, held on 21 May 2008: 75 members from party "
        "lists across the country with a 5% threshold and 75 in single-member districts. Turnout was 53.4%. The "
        "United National Movement won a constitutional majority in the preliminary results, which the opposition "
        "parties, who had expressed no confidence in the Commission, contested. Four lists crossed the threshold. In "
        "the districts only three subjects won seats: the Republican Party of Georgia 2, the United National Movement "
        "71, and the United Opposition 2. The volume names no district results, so those contests are not stored.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 5 June "
        "2008, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 188-191). The "
        "volume does not print the valid votes. It prints the votes of list 4 as 132 092 -7.44%.",
        lists=Lists(
            (192,),
            (189, 191),
            LIST_DOT,
            12,
            75,
            None,
            {4: 6, 5: 48, 7: 15, 10: 6},
            (
                "“Shalva Natelashvili – Labour Party of Georgia” – 6 mandates",
                "“United National Movement – for Victorious Georgia” – 48 mandates",
                "“United Opposition (National Council, New Rights)” – 15 mandates",
                "“Giorgi Targamadze – Christian-Democrats” – 6 mandates",
            ),
            True,
        ),
    ),
    Election(
        "ge/aj/2008-11-03-general",
        date(2008, 11, 3),
        "2008 Ajara Supreme Council election",
        "აჭარის ავტონომიური რესპუბლიკის უზენაესი საბჭოს 2008 წლის 3 ნოემბრის არჩევნები",
        AJARA_BODY,
        (196,),
        (
            Figure(REGISTERED, 293_217, "out of 293 217 registered voters"),
            Figure(VOTERS, 109_696, "109 696 voters came at the polling station"),
            Figure(INVALID, 2_842, "2 842 ballot papers were deemed invalid"),
        ),
        "An amendment cut the Supreme Council from 30 to 18 members: 12 from party lists with a 5% threshold and 6 in "
        "single-member districts in Batumi and the Kobuleti, Khelvachauri, Keda, Shuakhevi, and Khulo "
        "municipalities. Six lists stood. The results of the Khelvachauri district were annulled for violations and "
        "the district voted again on 16 November 2008. Turnout across both dates was 37.4%. The United National "
        "Movement won five of the six districts, and the candidate of Giorgi Targamadze – Christian-Democrats won "
        "the sixth in the repeat vote.",
        "National totals of the party-list vote from the summary protocol of the Supreme Election Commission of the "
        "Autonomous Republic of Adjara of 19 November 2008, as printed in the Commission's volume Electoral History "
        "of Georgia 1990-2018 (pages 194-196). The volume does not print the valid votes.",
        lists=Lists(
            (196,),
            (195,),
            LIST_NO,
            6,
            12,
            None,
            {5: 10, 10: 2},
            (
                "“United National Movement” – 10 mandates",
                "“Giorgi Targamadze – Christian-Democrats” – 2 mandates",
            ),
            True,
        ),
        contest_kind="list_allocation",
        contest_name=("2008 Supreme Council of Ajara, proportional seats", "უზენაესი საბჭოს პროპორციული არჩევნები"),
        unit=AJARA_UNIT,
        area=AJARA_AREA,
    ),
    Election(
        "ge/2012-10-01-parliament",
        date(2012, 10, 1),
        "2012 Georgian parliamentary election",
        "საქართველოს პარლამენტის 2012 წლის 1 ოქტომბრის არჩევნები",
        PARLIAMENT,
        (216,),
        (
            Figure(VOTERS, 2_215_661, "2 215 661 voters participated"),
            Figure(INVALID, 62_874, "62 874 ballot papers were deemed invalid"),
        ),
        "The election of the 150-member Parliament on 1 October 2012: 77 members from party lists across the country "
        "with a 5% threshold and 73 in single-member districts, where a candidate needed at least 30% of the votes "
        "cast. The pre-election process was tense and, according to observers, partly unfair; the Commission "
        "annulled the results of 16 polling stations and canceled nine in Khashuri district. Turnout was 60.8%. "
        "Two lists crossed the threshold, and only two subjects won district seats: the United National Movement 32 "
        "and the Georgian Dream bloc 41. The volume names no district results, so those contests are not stored.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 19 "
        "October 2012, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 214-216). "
        "The volume prints neither the electorate nor the valid votes, and prints the votes of 16 lists.",
        lists=Lists(
            (216,),
            (215,),
            LIST_NO,
            16,
            77,
            None,
            {5: 33, 41: 44},
            (
                "More Benefit to People”– 33 mandates",
                "Georgian Dream”– 44 mandates",
            ),
            True,
        ),
    ),
    Election(
        "ge/2013-10-27-president",
        date(2013, 10, 27),
        "2013 Georgian presidential election",
        "საქართველოს პრეზიდენტის 2013 წლის 27 ოქტომბრის არჩევნები",
        None,
        (224,),
        (
            Figure(REGISTERED, 3_537_719, "The total number of voters amounted to 3 537 719"),
            Figure(VOTERS, 1_660_976, "1 660 976 voters participated in the voting"),
            Figure(INVALID, 30_101, "30 101 ballot papers were deemed invalid"),
        ),
        "The regular election, which the Constitution sets for October, held when the term of Mikheil Saakashvili, "
        "elected in January 2008, expired; candidates had to be Georgian citizens aged 35 who had lived in Georgia "
        "for five years. Twenty-three candidates ran. Turnout was 46.6%, from 29.32% in Tsalka to 64.7%. Giorgi "
        "Margvelashvili won in the first round.",
        "National totals from the Central Election Commission's summary protocol of 12 November 2013, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 223-224). The volume does not print "
        "the valid votes.",
        office=Office(
            PRESIDENT,
            (224,),
            (225,),
            (),
            "Giorgi Margvelashvili",
            None,
            pattern=LIST_NO,
            count=23,
            winner_number=41,
        ),
    ),
    Election(
        "ge/2016-10-08-parliament",
        date(2016, 10, 8),
        "2016 Georgian parliamentary election",
        "საქართველოს პარლამენტის 2016 წლის 8 ოქტომბრის არჩევნები",
        PARLIAMENT,
        (246, 248),
        (
            Figure(REGISTERED, 3_513_884, "The total number of voters was 3 513 884"),
            Figure(VOTERS, 1_825_054, "The number of participants in elections was 1 825 054"),
            Figure(INVALID, 62_678, "62 678 ballots were deemed invalid"),
            Figure(VALID, 1_759_632, "received by election subjects was 1 759 632"),
        ),
        "The election of the 150-member Parliament on 8 October 2016: 77 members from party lists across the country "
        "with a 5% threshold and 73 in single-member districts, where a candidate needed more than half of the votes, "
        "with a second round on 30 October 2016 in the 50 districts that did not decide in the first. Turnout was "
        "51.9%. The Commission annulled the results of nine polling stations and ordered repeat votes in four. Three "
        "lists crossed the threshold. After the second round the 150 seats stood at: United National Movement 27, "
        "Topadze Industrials – Our Fatherland 1, Alliance of Patriots of Georgia 6, Georgian Dream – Democratic "
        "Georgia 115, and one independent. The volume names no district results, so those contests are not stored.",
        "National totals of the party-list vote from the Central Election Commission's summary protocol of 23 "
        "October 2016, as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 244-247). "
        "The volume prints 1 759 632 as the votes received by the election subjects, which is 2,744 fewer than the "
        "voters less the invalid ballots; it is stored as printed.",
        lists=Lists(
            (246,),
            (245, 247),
            LIST_NO,
            25,
            77,
            None,
            {5: 27, 8: 6, 41: 44},
            (
                "Bloc “United National Movement” – 27 mandates",
                "Patriots of Georgia, United Opposition” – 6 mandates",
                "“Georgian Dream – Democratic Georgia” – 44 mandates",
            ),
            True,
        ),
    ),
    Election(
        "ge/2018-10-28-president",
        date(2018, 10, 28),
        "2018 Georgian presidential election",
        "საქართველოს პრეზიდენტის 2018 წლის 28 ოქტომბრის არჩევნები",
        None,
        (276,),
        (
            Figure(REGISTERED, 3_518_877, "The total number of voters was 3 518 877"),
            Figure(VOTERS, 1_647_878, "1 647 878 voters took part in the voting"),
            Figure(INVALID, 53_847, "53 847 ballots were deemed invalid"),
        ),
        "The last election of the President by direct vote: amendments to the Constitution extended the term from "
        "five years to six, and a candidate needed more than half of the votes to win in the first round. Turnout "
        "was 46.74%, from 36.47% in Marneuli to 64.74% in Keda. No candidate won more than half of the votes, so "
        "Salome Zourabichvili and Grigol Vashadze, the two with the most votes, went to a second round on 28 "
        "November 2018.",
        "National totals from the Central Election Commission's summary protocol of 14 November 2018, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 274-276). The volume does not print "
        "the valid votes.",
        office=Office(
            PRESIDENT,
            (276,),
            (275,),
            (),
            "",
            None,
            pattern=LIST_NO,
            count=25,
            advanced=(5, 48),
        ),
    ),
    Election(
        "ge/2018-11-28-president-runoff",
        date(2018, 11, 28),
        "2018 Georgian presidential election, second round",
        "საქართველოს პრეზიდენტის 2018 წლის 28 ნოემბრის არჩევნების მეორე ტური",
        None,
        (278,),
        (
            Figure(REGISTERED, 3_528_658, "The total number of voters was 3 528 658"),
            Figure(VOTERS, 1_988_787, "1 988 787 voters took part in the voting"),
            Figure(INVALID, 59_778, "59 778 ballots were deemed invalid"),
        ),
        "The second round between Grigol Vashadze and Salome Zourabichvili, held on 28 November 2018 without "
        "incidents; turnout was 56.23%, from 43.12% in Tsalka to 72.72% in Keda. Salome Zourabichvili won.",
        "National totals from the Central Election Commission's summary protocol of 14 December 2018, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (page 277). The volume does not print the "
        "valid votes. The electorate is larger than in the first round because citizens who turned 18 between 28 "
        "October and 28 November were added.",
        office=Office(
            PRESIDENT,
            (278,),
            (277,),
            (),
            "",
            None,
            pattern=LIST_NO,
            count=2,
            winner_number=48,
        ),
        kind="runoff",
    ),
    Election(
        "ge/tb/2002-06-02-sakrebulo",
        date(2002, 6, 2),
        "2002 Tbilisi Sakrebulo election",
        "თბილისის საკრებულოს 2002 წლის 2 ივნისის არჩევნები",
        TBILISI_BODY,
        (140,),
        (
            Figure(REGISTERED, 634_257, "634 257 voters were registered in the voter list of Tbilisi city"),
            Figure(VOTERS, 283_855, "283 855 voters took part in elections"),
            Figure(INVALID, 13_344, "annulled ballot papers amounted to 13 344"),
        ),
        "The election of the Sakrebulo of the capital, whose membership fell from 55 to 49, by the proportional "
        "system with a 4% threshold; 21 parties and blocs ran. Turnout was 44.75%. The Labour Party and the "
        "National Movement – Democratic Front bloc led the vote. The results were appealed several times, and the "
        "newly elected Sakrebulo met only after several months. Five women won seats.",
        "Votes and seats from the Central Election Commission's summary protocol of 8 June 2002, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (pages 137-140). 46 of the 49 seats were "
        "divided by the votes; the Commission added the 3 undivided seats to the three parties with the best "
        "results. The volume does not print the valid votes.",
        lists=Lists(
            (140,),
            (138,),
            LIST_DOT,
            21,
            49,
            None,
            {4: 15, 34: 14, 38: 7, 36: 4, 3: 4, 2: 3, 40: 2},
            (
                "Labour Party of Georgia – 15 mandates",
                "Berdzenishvili” – 14 mandates",
                "New Rights – 7 mandates",
                "Team” – 4 mandates",
                "Election Bloc “Industry Saves Georgia” – 4 mandates",
                "Revival – XXI“ – 3 mandates",
                "Bloc “Unity” – 2 mandates",
            ),
            True,
        ),
        contest_kind="list_allocation",
        contest_name=("2002 Tbilisi Sakrebulo, proportional seats", "თბილისის საკრებულოს პროპორციული არჩევნები"),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/2006-10-05-local",
        date(2006, 10, 5),
        "2006 Georgian local self-government elections",
        "საქართველოს ადგილობრივი თვითმმართველობის ორგანოების 2006 წლის 5 ოქტომბრის არჩევნები",
        LOCAL_COUNCILS,
        (174,),
        (),
        "The elections of the sakrebulos of the cities and districts on 5 October 2006, under the new code of "
        "self-government that abolished the second tier of rural-community self-government and the direct election "
        "of city mayors. 1,694 members were elected: 680 by the proportional system with a 5% threshold and 1,014 "
        "by the majoritarian system, everywhere the central government controlled. Six subjects were registered, and "
        "some opposition parties boycotted the election. Turnout was 47.29%. The volume reports the majoritarian "
        "seats by party: Topadze – Industrialists 4, Labour Party 5, National Movement – List of Unification and "
        "Rebuilding of Georgia 925, the Davitashvili, Khidasheli, Berdzenishvili bloc 9, Salome Zourabichvili 3, and "
        "68 independents.",
        "Seats won by the proportional system across Georgia, from the volume's report of the Commission's summary "
        "protocol of 9 October 2006, as printed in the Commission's volume Electoral History of Georgia 1990-2018 "
        "(page 173). The volume prints no votes for the sakrebulos outside Tbilisi.",
        lists=Lists(
            (174,),
            (174,),
            MANDATES,
            4,
            680,
            None,
            {},
            ("“Topadze – Industrialists” – 23 mandates", "– 578 mandates"),
            False,
            after=r"(?:By proportional system:|შემდეგნაირად\nგადანაწილდა:)\n",
            until=r"Mandates by the majoritarian|მაჟორიტარული სისტემით მანდატები",
            mixed=True,
        ),
        contest_name=("2006 sakrebulo seats by party, proportional system", "საკრებულოთა პროპორციული მანდატები"),
        kind="local",
    ),
    Election(
        "ge/tb/2006-10-05-sakrebulo",
        date(2006, 10, 5),
        "2006 Tbilisi Sakrebulo election",
        "თბილისის საკრებულოს 2006 წლის 5 ოქტომბრის არჩევნები",
        TBILISI_BODY,
        (173,),
        (
            Figure(REGISTERED, 883_806, "883 806 voters were registered in the electoral lists in the capital"),
            Figure(VOTERS, 307_015, "307 015 voters participated in the polling"),
            Figure(INVALID, 2_807, "2 807 ballot papers were deemed invalid"),
        ),
        "The election of the 37-member Sakrebulo of the capital: 12 members by the proportional system with a 5% "
        "threshold and 25 in single-member districts, all of which the National Movement won. Six subjects ran. "
        "Turnout in Tbilisi was 34.26%. Four of the 37 members were women.",
        "Votes and seats from the Central Election Commission's summary protocol of 9 October 2006, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 171-173). The protocol also counts "
        "2,497 damaged ballot papers and 2,901 voters who voted against everyone, which are not stored.",
        lists=Lists(
            (173,),
            (172,),
            LIST_DOT,
            6,
            12,
            None,
            {3: 1, 4: 1, 5: 9, 22: 1},
            (
                "“Topadze – Industrialists”– 1 mandate",
                "Labour Party of Georgia – Shalva Natelashvili – 1 mandate",
                "Rebuilding of Georgia” – 9 mandates",
                "Berdzenishvili” election bloc – 1 mandate",
            ),
            True,
        ),
        contest_kind="list_allocation",
        contest_name=("2006 Tbilisi Sakrebulo, proportional seats", "თბილისის საკრებულოს პროპორციული არჩევნები"),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/2010-05-30-local",
        date(2010, 5, 30),
        "2010 Georgian local self-government elections",
        "საქართველოს ადგილობრივი თვითმმართველობის ორგანოების 2010 წლის 30 მაისის არჩევნები",
        LOCAL_COUNCILS,
        (207,),
        (
            Figure(REGISTERED, 3_544_770, "out of 3 544 770 voters registered"),
            Figure(VOTERS, 1_740_652, "1 740 652 citizens came to the polling station"),
        ),
        "The elections of the sakrebulos of the capital, four self-governing cities, and 59 municipalities on 30 May "
        "2010, called by President Saakashvili in March 2010 as the term of the bodies elected in 2006 expired. Each "
        "city and district elected 10 to 15 members by the proportional system with a 5% threshold, and the rest by "
        "the majoritarian system; the Sakrebulo of the capital had 50 members. Seventeen subjects were registered. "
        "No vote was held in Zemo Abkhazeti, the Liakhvi gorge, or the Akhalgori district, which Russia occupied "
        "after 2008. Turnout was 49%, from 37.27% in Zugdidi to 75.08% in Ninotsminda.",
        "Votes by party list summed over the polling stations of every self-governing unit, from the Central "
        "Election Commission's results as printed in the Commission's volume Electoral History of Georgia "
        "1990-2018 (pages 206-207). The volume prints no seats by party for the sakrebulos across Georgia. The "
        "electorate and the voters are printed only here, not the invalid ballots.",
        lists=Lists(
            (207,),
            (207,),
            LIST_NO,
            17,
            0,
            None,
            {},
            (),
            True,
            mixed=True,
        ),
        contest_name=("2010 sakrebulo elections, party-list votes", "საკრებულოების არჩევნები, პარტიული სიების ხმები"),
        kind="local",
    ),
    Election(
        "ge/tb/2010-05-30-mayor",
        date(2010, 5, 30),
        "2010 Tbilisi mayoral election",
        "თბილისის მერის 2010 წლის 30 მაისის არჩევნები",
        None,
        (203,),
        (
            Figure(REGISTERED, 981_505, "out of 981 505 registered voters in the capital"),
            Figure(VOTERS, 462_979, "462 979 cast their votes"),
            Figure(INVALID, 11_598, "11 598 ballot papers have been annulled"),
        ),
        "The first direct election of the mayor of the capital, after an amendment of the Election Code: a candidate "
        "at least 25 years old who had lived in Georgia for five years could run, and the one with a majority of "
        "the votes and the support of at least 30% of the electorate was elected. Nine candidates ran. Giorgi "
        "Ugulava won in the first round.",
        "National totals from the Central Election Commission's summary protocol of 15 June 2010, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (pages 202-203). The volume does not print the "
        "valid votes.",
        office=Office(
            MAYOR,
            (203,),
            (203,),
            (),
            "Giorgi Ugulava",
            None,
            pattern=LIST_NO,
            count=9,
            winner_number=5,
            mixed=True,
        ),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/tb/2010-05-30-sakrebulo",
        date(2010, 5, 30),
        "2010 Tbilisi Sakrebulo election",
        "თბილისის საკრებულოს 2010 წლის 30 მაისის არჩევნები",
        TBILISI_BODY,
        (205,),
        (),
        "The election of the 50-member Sakrebulo of the capital: 25 members by the proportional system with a 5% "
        "threshold and 25 in single-member districts, all of which the National Movement won. Seven women were "
        "elected, six of them by the proportional system.",
        "Votes by party list from the Central Election Commission's summary protocol of 15 June 2010, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 204-205). The volume does not print "
        "the seats of the lists, nor the electorate, the voters, or the ballots of this contest, and prints the "
        "votes of 14 of the 17 registered lists.",
        lists=Lists(
            (205,),
            (205,),
            LIST_NO,
            14,
            25,
            None,
            {},
            (),
            True,
            mixed=True,
        ),
        contest_kind="list_allocation",
        contest_name=("2010 Tbilisi Sakrebulo, proportional seats", "თბილისის საკრებულოს პროპორციული არჩევნები"),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/2014-06-15-local",
        date(2014, 6, 15),
        "2014 Georgian local self-government elections",
        "საქართველოს ადგილობრივი თვითმმართველობის ორგანოების 2014 წლის 15 ივნისის არჩევნები",
        LOCAL_COUNCILS,
        (238,),
        (),
        "The elections of the representative and executive bodies of the local governments on 15 June 2014, after "
        "the reform of the Local Self-Government Code: the number of cities with their own self-governing status "
        "grew, mayors and gamgebelis were elected directly and needed more than half of the votes, and the "
        "sakrebulos were elected by the proportional system with a 4% threshold and by the majoritarian system. "
        "2,088 deputies were to be elected: 1,050 by the proportional system and 1,038 by the majoritarian system. "
        "Turnout in the first round was 43.31%. The Georgian Dream bloc won every mayoralty and gamgebeli seat "
        "decided in the first round, and a second round was held on 12 July 2014.",
        "Votes by party list summed over the self-governing units, from the Central Election Commission's results "
        "as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 236-237). The volume "
        "prints no seats by party and no totals of the electorate or the voters.",
        lists=Lists(
            (238,),
            (237,),
            LIST_NO,
            23,
            0,
            None,
            {},
            (),
            True,
        ),
        contest_name=("2014 sakrebulo elections, party-list votes", "საკრებულოების არჩევნები, პარტიული სიების ხმები"),
        kind="local",
    ),
    Election(
        "ge/tb/2014-06-15-mayor",
        date(2014, 6, 15),
        "2014 Tbilisi mayoral election",
        "თბილისის მერის 2014 წლის 15 ივნისის არჩევნები",
        None,
        (234,),
        (
            Figure(REGISTERED, 918_667, "stood at 918 667"),
            Figure(VOTERS, 342_386, "participating in the elections was 342 386"),
            Figure(INVALID, 11_771, "11 771 ballot papers were deemed invalid"),
        ),
        "The election of the mayor of the capital on 15 June 2014; a candidate needed more than half of the votes. "
        "Fourteen candidates ran, and none won half, so Nikanor Melia and Davit Narmania went to a second round on "
        "12 July 2014.",
        "National totals from the Central Election Commission's summary protocol of 27 June 2014, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (pages 232-233). The volume does not print the "
        "valid votes.",
        office=Office(
            MAYOR,
            (234,),
            (231, 233),
            (),
            "",
            None,
            pattern=LIST_NO,
            count=14,
            advanced=(5, 41),
            until=r"The summary protocol of Sakrebulo Elections|დედაქალაქის საკრებულოს არჩევნების",
        ),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/tb/2014-06-15-sakrebulo",
        date(2014, 6, 15),
        "2014 Tbilisi Sakrebulo election",
        "თბილისის საკრებულოს 2014 წლის 15 ივნისის არჩევნები",
        TBILISI_BODY,
        (234, 236),
        (
            Figure(REGISTERED, 918_667, "amounted to 918 667"),
            Figure(VOTERS, 343_582, "343 582 voters participated in polling"),
            Figure(INVALID, 14_151, "14 151 ballot papers were deemed invalid"),
        ),
        "The election of the 50-member Sakrebulo of the capital: 25 members by the proportional system with a 4% "
        "threshold and 25 in single-member districts, of which the Georgian Dream bloc won 24 and an independent "
        "one. Twelve women were elected, five by the proportional system.",
        "Votes and seats by party list from the Central Election Commission's summary protocol of 3 July 2014, as "
        "printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 232-235). The volume does "
        "not print the valid votes.",
        lists=Lists(
            (234, 236),
            (233, 235),
            LIST_NO,
            19,
            25,
            None,
            {3: 3, 5: 7, 8: 2, 41: 13},
            (
                "№3 Nino Burjanadze – United Opposition – 3 mandates",
                "№5 Bloc “United National Movement”– 7 mandates",
                "Alliance of Patriots of Georgia – 2 mandates",
                "№41 Bloc “Georgian Dream” – 13 mandates",
            ),
            True,
            after=r"The summary protocol of Sakrebulo Elections|დედაქალაქის საკრებულოს არჩევნების",
        ),
        contest_kind="list_allocation",
        contest_name=("2014 Tbilisi Sakrebulo, proportional seats", "თბილისის საკრებულოს პროპორციული არჩევნები"),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/tb/2014-07-12-mayor-runoff",
        date(2014, 7, 12),
        "2014 Tbilisi mayoral election, second round",
        "თბილისის მერის 2014 წლის 12 ივლისის არჩევნების მეორე ტური",
        None,
        (240,),
        (
            Figure(REGISTERED, 920_020, "The number of voters was 920 020"),
            Figure(VOTERS, 317_078, "317 078 voters took part in the elections"),
            Figure(INVALID, 10_590, "10 590 ballots were deemed invalid"),
        ),
        "The second round between Nikanor Melia and Davit Narmania on 12 July 2014; turnout in the capital was "
        "34.3%. Davit Narmania won.",
        "National totals from the Central Election Commission's summary protocol of 22 July 2014, as printed in the "
        "Commission's volume Electoral History of Georgia 1990-2018 (pages 238-240). The volume does not print the "
        "valid votes.",
        office=Office(
            MAYOR,
            (240,),
            (239,),
            (),
            "",
            None,
            pattern=LIST_NO,
            count=2,
            winner_number=41,
        ),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="runoff",
    ),
    Election(
        "ge/2017-10-21-local",
        date(2017, 10, 21),
        "2017 Georgian local self-government elections",
        "საქართველოს ადგილობრივი თვითმმართველობის ორგანოების 2017 წლის 21 ოქტომბრის არჩევნები",
        LOCAL_COUNCILS,
        (268,),
        (),
        "The elections of the representative bodies of the self-governing communities on 21 October 2017: mayors "
        "needed more than half of the votes, and the sakrebulos were elected by the proportional system with a 4% "
        "threshold and by the majoritarian system. Turnout was 45.65%, from 35.36% in Kutaisi to 66.09% in Keda. "
        "The mayors of the capital, three self-governing cities, and 54 communities were decided in the first "
        "round, almost all of them for the Georgian Dream party; the first round failed to decide six units, which "
        "voted again on 12 November 2017. The Commission stated that Georgian Dream won an absolute majority of "
        "the seats in the majoritarian vote of the self-governing units.",
        "Votes by party list summed over the self-governing units, from the Central Election Commission's results "
        "as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 266-268). The volume "
        "prints no seats by party and no totals of the electorate or the voters. It prints list 9's share as "
        "1 416 % – 0.09%.",
        lists=Lists(
            (267, 268, 269),
            (267, 268, 269),
            LIST_NO,
            25,
            0,
            None,
            {},
            (),
            True,
            mixed=True,
        ),
        contest_name=("2017 sakrebulo elections, party-list votes", "საკრებულოების არჩევნები, პარტიული სიების ხმები"),
        kind="local",
    ),
    Election(
        "ge/tb/2017-10-21-mayor",
        date(2017, 10, 21),
        "2017 Tbilisi mayoral election",
        "თბილისის მერის 2017 წლის 21 ოქტომბრის არჩევნები",
        None,
        (262,),
        (
            Figure(REGISTERED, 943_423, "The number of voters was 943 423"),
            Figure(VOTERS, 411_847, "411 847 voters took part in the voting"),
            Figure(INVALID, 11_260, "11 260 ballots were deemed invalid"),
        ),
        "The election of the mayor of the capital on 21 October 2017; a candidate needed more than half of the "
        "votes. Thirteen candidates ran. Kakha Kaladze won in the first round.",
        "National totals from the Central Election Commission's summary protocol of 1 November 2017, as printed in "
        "the Commission's volume Electoral History of Georgia 1990-2018 (pages 261-263). The volume does not print "
        "the valid votes.",
        office=Office(
            MAYOR,
            (262,),
            (263,),
            (),
            "Kakha Kaladze",
            None,
            pattern=LIST_NO,
            count=13,
            winner_number=41,
        ),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
    Election(
        "ge/tb/2017-10-21-sakrebulo",
        date(2017, 10, 21),
        "2017 Tbilisi Sakrebulo election",
        "თბილისის საკრებულოს 2017 წლის 21 ოქტომბრის არჩევნები",
        TBILISI_BODY,
        (265, 268),
        (),
        "The election of the 50-member Sakrebulo of the capital: 25 members by the proportional system with a 4% "
        "threshold and 25 in single-member districts, all of which Georgian Dream won. Ten women were elected, four "
        "by the proportional system.",
        "Votes and seats by party list from the Central Election Commission's summary protocol of 1 November 2017, "
        "as printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 264-268). The volume "
        "does not print the electorate, the voters, or the ballots of this contest. Its English text prints 15 112 "
        "for list 14, repeating the figure of list 10; its Georgian text prints 564, which is the figure the "
        "volume's nationwide table gives for that list, and 564 is stored.",
        lists=Lists(
            (265,),
            (265,),
            LIST_NO,
            16,
            25,
            None,
            {2: 3, 5: 5, 8: 2, 41: 15},
            (
                "N2 Bloc “Bakradze, Ugulava – European Georgia” – 3 mandates",
                "N5 Bloc “United National Movement” – 5 mandates",
                "Patriots of Georgia” – 2 mandates",
                "N41 “Georgian Dream – Democratic Georgia” – 15 mandates",
            ),
            True,
            differs=frozenset({14}),
            georgian_wins=frozenset({14}),
            mixed=True,
        ),
        contest_kind="list_allocation",
        contest_name=("2017 Tbilisi Sakrebulo, proportional seats", "თბილისის საკრებულოს პროპორციული არჩევნები"),
        unit=TBILISI_UNIT,
        area=TBILISI_AREA,
        kind="local",
    ),
)


def package_key(election: Election) -> str:
    return f"{SOURCE}/{election.slug.removeprefix('ge/').replace('/', '-')}"


def packages() -> list[str]:
    return [GEO, f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS)]


def fetch() -> None:
    # The Commission's site refuses scripted clients; the file reached the cache with raw.store from a copy saved
    # from the Commission's page.
    artifact_of_book()
    raw.fetch(SOURCE, ADM1, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------------------------
# Packages


def person(name: str) -> str:
    return key("person", name)


def candidates() -> dict[str, Candidate]:
    out: dict[str, Candidate] = {}
    for election in ELECTIONS:
        for office in (election.office, election.chair):
            if office is not None:
                for candidate in office.candidates:
                    out[candidate.english] = candidate
        if election.office is not None and election.office.pattern is not None:
            for _, candidate, _ in entries_of(election.slug):
                out.setdefault(candidate.english, candidate)
    return out


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The outlines of the Autonomous Republic of Ajara and of Tbilisi, for the Supreme Council elections of Ajara "
        "and the elections of the Tbilisi sakrebulo and mayor",
    )
    p.external.add(GEORGIA)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Georgia ADM1",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    artifact = raw.cached(SOURCE, ADM1)
    p.artifact(artifact, GEO_SOURCE)
    p.add(
        "scheme",
        code=REGION_SCHEME,
        polity_id=GEORGIA,
        kind="administrative",
        rank=1,
        noun="autonomous republic",
        code_system=None,
    )
    features = json.loads(artifact.path.read_text(encoding="utf-8"))["features"]
    p.add(
        "scheme",
        code=CAPITAL_SCHEME,
        polity_id=GEORGIA,
        kind="administrative",
        rank=1,
        noun="capital city",
        code_system=None,
    )
    for scheme, area, code, english, georgian in (
        (REGION_SCHEME, AJARA_AREA, "GE-AJ", "Autonomous Republic of Ajara", "აჭარის ავტონომიური რესპუბლიკა"),
        (CAPITAL_SCHEME, TBILISI_AREA, "GE-TB", "Tbilisi", "თბილისი"),
    ):
        p.add("area", id=area, scheme_code=scheme)
        p.add("area_code", area_id=area, code=code)
        p.name(area, english, "en", "english")
        p.name(area, georgian, "ka", "official")
        found = [f for f in features if f["properties"]["shapeISO"] == code]
        if len(found) != 1:
            raise ValueError(f"geoBoundaries does not hold exactly one {code} outline")
        outline = shapely.geometry.shape(found[0]["geometry"])
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            valid_to=None,
            geom=shapely.to_wkb(shapely.set_srid(outline, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GEO_SOURCE,
        )
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "gbopen"),
            scheme_code=scheme,
            vintage="geoBoundaries gbOpen",
            valid_from=None,
            valid_to=None,
            as_of=date(2023, 1, 1),
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Georgia's Supreme Council of 1990, its Parliament, President, and Chairman of Parliament, the councils "
        "of its local governments, the Supreme Council of Ajara, the Tbilisi sakrebulo and mayor, the electoral "
        "systems of 1990 to 2000, and the presidential and mayoral candidates, from the Central Election Commission",
        depends=[GEO],
    )
    p.external |= {GEORGIA, COUNTRY}
    p.add("scheme", code=SCHEME, polity_id=GEORGIA, kind="administrative", rank=0, noun="country", code_system=None)
    p.add(
        "reporting_unit",
        id=UNIT,
        polity_id=GEORGIA,
        scheme_code=SCHEME,
        code="GE",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    p.name(UNIT, "Georgia", "en", "english")
    p.name(UNIT, "საქართველო", "ka", "official")
    p.add(
        "reporting_unit",
        id=AJARA_UNIT,
        polity_id=GEORGIA,
        scheme_code=REGION_SCHEME,
        code="GE-AJ:cec_1990s",
        area_relation="equals",
        area_id=AJARA_AREA,
        kind="aggregate",
    )
    p.name(AJARA_UNIT, "Autonomous Republic of Ajara", "en", "english")
    p.name(AJARA_UNIT, "აჭარის ავტონომიური რესპუბლიკა", "ka", "official")
    p.add(
        "reporting_unit",
        id=TBILISI_UNIT,
        polity_id=GEORGIA,
        scheme_code=CAPITAL_SCHEME,
        code="GE-TB:cec_1990s",
        area_relation="equals",
        area_id=TBILISI_AREA,
        kind="aggregate",
    )
    p.name(TBILISI_UNIT, "Tbilisi", "en", "english")
    p.name(TBILISI_UNIT, "თბილისი", "ka", "official")

    p.add("body", id=SUPREME_COUNCIL, polity_id=GEORGIA, kind="unicameral")
    p.name(SUPREME_COUNCIL, "Supreme Council of the Republic of Georgia", "en", "english")
    p.name(SUPREME_COUNCIL, "საქართველოს რესპუბლიკის უზენაესი საბჭო", "ka", "official")
    p.add("body", id=AJARA_BODY, polity_id=GEORGIA, kind="unicameral")
    p.name(AJARA_BODY, "Supreme Council of the Autonomous Republic of Ajara", "en", "english")
    p.name(AJARA_BODY, "აჭარის ავტონომიური რესპუბლიკის უზენაესი საბჭო", "ka", "official")
    p.add("body", id=LOCAL_COUNCILS, polity_id=GEORGIA, kind="council")
    p.name(LOCAL_COUNCILS, "Local councils (sakrebulos) of Georgia", "en", "english")
    p.name(LOCAL_COUNCILS, "საქართველოს საკრებულოები", "ka", "official")
    p.add("body", id=TBILISI_BODY, polity_id=GEORGIA, kind="council")
    p.name(TBILISI_BODY, "Tbilisi City Sakrebulo", "en", "english")
    p.name(TBILISI_BODY, "თბილისის საკრებულო", "ka", "official")
    p.add("office", id=MAYOR, polity_id=GEORGIA, kind="mayor")
    p.name(MAYOR, "Mayor of Tbilisi", "en", "english")
    p.name(MAYOR, "თბილისის მერი", "ka", "official")
    p.add("body", id=PARLIAMENT, polity_id=GEORGIA, kind="unicameral")
    p.name(PARLIAMENT, "Parliament of Georgia", "en", "english")
    p.name(PARLIAMENT, "საქართველოს პარლამენტი", "ka", "official")
    p.add("office", id=PRESIDENT, polity_id=GEORGIA, kind="head_of_state")
    p.name(PRESIDENT, "President of Georgia", "en", "english")
    p.name(PRESIDENT, "საქართველოს პრეზიდენტი", "ka", "official")
    p.add("office", id=CHAIRMAN, polity_id=GEORGIA, body_id=PARLIAMENT, kind="speaker")
    p.name(CHAIRMAN, "Chairman of Parliament – Head of State", "en", "english")
    p.name(CHAIRMAN, "პარლამენტის თავმჯდომარე – სახელმწიფოს მეთაური", "ka", "official")

    systems = (
        (
            SYSTEM_1990,
            date(1990, 10, 28),
            date(1990, 11, 11),
            {"formula": "hare", "threshold": {"percent": 4, "level": "list"}},
            "Supreme Council party lists, 1990",
            (
                "125 seats were divided among the lists that won at least 4% of the votes across the republic. The "
                "Commission's volume does not state the seat formula."
            ),
        ),
        (
            SYSTEM_1995,
            date(1995, 11, 5),
            date(1999, 10, 30),
            {"formula": "hare", "threshold": {"percent": 5, "level": "list"}, "leftovers": "most_votes"},
            "Parliament party lists, 1995",
            (
                "Under the law of 1 September 1995, a list needed 5% of the votes cast. Each qualifying list won the whole "
                "part of its votes times 150 divided by the qualifying lists' total votes; seats left over went one each "
                "to the qualifying lists with the most votes."
            ),
        ),
        (
            SYSTEM_1999,
            date(1999, 10, 31),
            date(1999, 10, 31),
            {"formula": "hare", "threshold": {"percent": 7, "level": "list"}, "leftovers": "most_votes"},
            "Parliament party lists, 1999",
            "The 1995 rules with the threshold raised from 5% to 7%.",
        ),
        (
            SYSTEM_AJARA_1991,
            date(1991, 6, 23),
            date(1991, 7, 10),
            {"formula": "dhondt", "threshold": {"percent": 4, "level": "list"}},
            "Supreme Council of Ajara party lists, 1991",
            (
                "40 seats were divided among the lists that won at least 4% of the votes across the autonomous "
                "republic. The Commission's volume does not state the seat formula; the declared seats follow the "
                "D'Hondt method."
            ),
        ),
        (
            SYSTEM_AJARA_1996,
            date(1996, 9, 22),
            date(1996, 9, 22),
            {"formula": "hare", "threshold": {"percent": 5, "level": "list"}},
            "Supreme Council of Ajara party lists, 1996",
            (
                "40 seats were divided among the lists that won at least 5% of the votes across the autonomous "
                "republic. The Commission's volume does not state the seat formula; the declared seats follow the "
                "Hare quota with largest remainders."
            ),
        ),
        (
            SYSTEM_PRESIDENT,
            date(1991, 5, 26),
            date(2000, 4, 9),
            {"formula": "two_round", "majority": 0.5, "minimum": {"share": 0.5, "of": "registered"}},
            "President, 1991 to 2000",
            (
                "A candidate was elected with more than half of the votes, provided at least half of the registered "
                "voters took part; otherwise a second round was held."
            ),
        ),
    )
    for id, start, end, rules, name, note in systems:
        p.add(
            "electoral_system",
            id=id,
            polity_id=GEORGIA,
            valid_from=start,
            valid_to=end,
            rules=rules,
            rules_version=1,
        )
        p.name(id, name, "en", "english")
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")

    for candidate in candidates().values():
        id = person(candidate.english)
        p.add("person", id=id)
        p.name(id, candidate.english, "en", "english")
        p.name(id, candidate.georgian, "ka", "official")
    _source(p, define=True)
    return p


def _source(p: Package, define: bool = False) -> str:
    """The volume's artifact id; the registry adds the source and the artifact, which every election package cites."""
    if define:
        p.add(
            "source",
            id=CEC,
            kind="official_body",
            publisher="Central Election Commission of Georgia",
            title="Electoral History of Georgia 1990-2018 (საქართველოს არჩევნების ისტორია 1990-2018)",
            url=HOME,
            license="No license stated; published by the electoral authority for public use",
            attribution="Central Election Commission of Georgia",
        )
    artifact = artifact_of_book()
    if define:
        return p.artifact(artifact, CEC)
    p.uses(artifact)
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CEC}\x1f{artifact.storage_key}"))


class Facts:
    """Result values for one result set at the national unit, each with its page and box in the volume."""

    def __init__(self, result_set: str, pages: tuple[int, ...], unit: str = UNIT) -> None:
        self.unit = unit
        self.result_set = result_set
        self.pages = pages
        self.rows: list[dict[str, object]] = []

    def add(self, option: str | None, measure: int, value: int, pages: tuple[int, ...] | None = None) -> None:
        page, (x0, y0, x1, y1) = locate(value, pages or self.pages)
        self.rows.append(
            {
                "result_set_id": self.result_set,
                "unit_id": self.unit,
                "option_id": option,
                "measure": measure,
                "value": value,
                "page": page,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
            }
        )

    def write(self, p: Package, used: set[int]) -> None:
        if not self.rows:
            return
        frame = pl.DataFrame(self.rows, orient="row").with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        )
        p.frame("result_value", frame.select(*package.FACT, "value").with_columns(flags=pl.lit(0, pl.Int16)))
        pages = frame["page"].to_list()
        used |= set(pages)
        p.frame(
            "value_locator",
            frame.select(
                *package.FACT,
                page_id=pl.Series([key("page", page) for page in pages]),
                source_id=pl.lit(CEC),
                x0="x0",
                y0="y0",
                x1="x1",
                y1="y1",
            ),
        )


def lists(election: Election, spec: Lists) -> dict[int, tuple[str, str, int]]:
    """The entries of a list contest: ballot number to English name, Georgian name, and the printed figure."""
    english = listing(spec.english, spec.pattern, spec.after, "en" if spec.mixed else None, spec.until)
    georgian = listing(spec.georgian, spec.pattern, spec.after, "ka" if spec.mixed else None, spec.until)
    if len(english) != spec.count or set(english) != set(georgian):
        raise ValueError(
            f"{election.slug}: {len(english)} English and {len(georgian)} Georgian entries, expected {spec.count}: "
            f"{sorted(set(english) ^ set(georgian))}"
        )
    out: dict[int, tuple[str, str, int]] = {}
    for number, (name, value) in english.items():
        name = re.sub("[\u10a0-\u10ff]", "", name)
        if election.day.year > 2000:
            name = balanced(name)
        if georgian[number][1] != value and number not in spec.differs:
            raise ValueError(f"{election.slug} list {number}: English {value}, Georgian {georgian[number][1]}")
        out[number] = (name, georgian[number][0], georgian[number][1] if number in spec.georgian_wins else value)
    if not spec.votes and spec.magnitude and sum(value for _, _, value in out.values()) != spec.magnitude:
        raise ValueError(f"{election.slug}: the seats do not add up to {spec.magnitude}")
    for phrase in spec.seat_phrases:
        mentions(phrase, election.pages)
    return out


def balanced(name: str) -> str:
    """An English name with the quotation mark the volume left out restored."""
    if name.startswith("”"):
        name = "“" + name[1:]
    if name.endswith("“") and name.count("“") > name.count("”"):
        name = name[:-1].rstrip() + "”"
    opening, closing = name.count("“"), name.count("”")
    if closing > opening:
        name = "“" + name
    elif opening > closing:
        name += "”"
    return name


@cache
def entries_of(slug: str) -> tuple[tuple[int, Candidate, int], ...]:
    """The candidates of an election's office contest read from the pages: ballot position, candidate, and votes."""
    election = next(e for e in ELECTIONS if e.slug == slug)
    office = election.office
    if office is None or office.pattern is None:
        raise ValueError(f"{slug} has no candidate listing")
    english = listing(office.english, office.pattern, office.after, "en" if office.mixed else None, office.until)
    georgian = listing(office.georgian, office.pattern, office.after, "ka" if office.mixed else None, office.until)
    if len(english) != office.count or set(english) != set(georgian):
        raise ValueError(
            f"{slug}: {len(english)} English and {len(georgian)} Georgian candidates, expected {office.count}: "
            f"{sorted(set(english) ^ set(georgian))}"
        )
    out = []
    for number, (name, votes) in english.items():
        if georgian[number][1] != votes and number not in office.differs:
            raise ValueError(f"{slug} candidate {number}: English {votes}, Georgian {georgian[number][1]}")
        out.append((number, Candidate(name, georgian[number][0]), votes))
    return tuple(out)


def ballot_key(election: Election) -> object:
    """What keys an election's parties and options: the year for the elections of 1990 to 2000, the slug after."""
    return election.day.year if election.day.year <= 2000 else election.slug


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        package_key(election),
        SOURCE,
        f"The {election.english} on {day:%-d %B %Y}: national totals from the Central Election Commission's summary "
        "protocol",
        depends=[f"{SOURCE}/registry", *([GEO] if election.area != COUNTRY else [])],
    )
    p.external |= {GEORGIA, COUNTRY}
    for code, reason in election.accepted:
        p.accept(code, reason)
    artifact = _source(p)
    used: set[int] = set()
    for item in election.figures:
        mentions(item.label, election.pages)

    id = key("election", election.slug)
    p.add("election", id=id, polity_id=GEORGIA, kind=election.kind, date_from=day, date_to=day, status="held")
    p.name(id, election.english, "en", "english")
    p.name(id, election.georgian, "ka", "official")
    p.add("slug", path=election.slug, entity_id=id, is_current=True)
    p.add("note", id=key("note", id), entity_id=id, kind="context", body_md=election.context, lang="en")

    def result_set(contest: str, completeness: float, method: str) -> str:
        rs = key("result_set", contest)
        p.add(
            "result_set",
            id=rs,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CEC,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=method, lang="en")
        p.add(
            "citation",
            id=key("citation", rs),
            entity_id=rs,
            source_id=CEC,
            locator={"url": BOOK, "pdf_pages": list(election.pages)},
        )
        return rs

    if election.lists is not None:
        spec = election.lists
        entries = lists(election, spec)
        contest = key("contest", election.slug, "lists")
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind=election.contest_kind,
            body_id=election.body,
            district_area_id=election.area,
            magnitude=spec.magnitude or None,
            round_number=1,
            electoral_system_id=spec.system,
            default_result_kind="counted",
        )
        if election.contest_name is not None:
            p.name(contest, election.contest_name[0], "en", "english")
            p.name(contest, election.contest_name[1], "ka", "official")
        elif spec.votes:
            p.name(contest, f"{day.year} party lists", "en", "english")
            p.name(contest, "პროპორციული სისტემა", "ka", "official")
        else:
            p.name(contest, f"{day.year} multi-member districts, seats by party", "en", "english")
            p.name(contest, "მრავალმანდატიანი საარჩევნო ოლქები", "ka", "official")
        p.add("slug", path=f"{election.slug}/party-lists", entity_id=contest, is_current=True)
        rs = result_set(contest, 1.0 if spec.votes else 0.0, election.method)
        if election.caveat:
            p.add("note", id=key("note", rs, "caveat"), entity_id=rs, kind="caveat", body_md=election.caveat, lang="en")
        facts = Facts(rs, election.pages, election.unit)
        for number, (english, georgian, value) in sorted(entries.items()):
            party = key("party", ballot_key(election), number)
            bloc = number in spec.blocs or "bloc" in english.lower() or georgian.startswith("ბლოკი")
            p.add("party", id=party, kind="bloc" if bloc else "party", polity_id=GEORGIA)
            p.name(party, english, "en", "english")
            p.name(party, georgian, "ka", "official")
            option = key("option", ballot_key(election), number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=None if spec.pattern == MANDATES else number,
                ballot_label=georgian,
            )
            if spec.votes:
                facts.add(option, VOTES, value, spec.english + spec.georgian)
                if number in spec.seats:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=spec.seats[number])
            else:
                p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=value)
        if spec.votes:
            for item in election.figures:
                facts.add(None, item.measure, item.value)
        facts.write(p, used)

    if election.question is not None:
        q = election.question
        contest = key("contest", election.slug, "question")
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="ballot_question",
            district_area_id=election.area,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, q.english, "en", "english")
        p.name(contest, q.georgian, "ka", "official")
        p.add("slug", path=f"{election.slug}/question", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type=q.type)
        p.add("note", id=key("note", contest), entity_id=contest, kind="context", body_md=q.text, lang="en")
        rs = result_set(contest, 1.0, election.method)
        facts = Facts(rs, election.pages, election.unit)
        mentions(q.yes_phrase, election.pages)
        mentions(q.no_phrase, election.pages)
        options = {}
        for position, (answer, label, value) in enumerate((("yes", "დიახ", q.yes), ("no", "არა", q.no)), start=1):
            option = options[answer] = key("option", election.slug, answer)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            facts.add(option, VOTES, value)
        for answer, option in options.items():
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="won" if answer == "yes" else "lost",
            )
        for item in election.figures:
            facts.add(None, item.measure, item.value)
        facts.write(p, used)

    for office in (election.office, election.chair):
        if office is None:
            continue
        contest = key("contest", election.slug, "office", office.office)
        is_chair = office.office == CHAIRMAN
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="office",
            office_id=office.office,
            district_area_id=election.area if election.area != COUNTRY else None,
            magnitude=1,
            round_number=2 if election.kind == "runoff" else 1,
            electoral_system_id=office.system,
            default_result_kind="counted",
        )
        is_mayor = office.office == MAYOR
        title = "Chairman of Parliament" if is_chair else "Mayor of Tbilisi" if is_mayor else "President"
        suffix = ", second round" if election.kind == "runoff" else ""
        p.name(contest, f"{day.year} {title}{suffix}", "en", "english")
        p.name(
            contest,
            "პარლამენტის თავმჯდომარე" if is_chair else "თბილისის მერი" if is_mayor else "პრეზიდენტი",
            "ka",
            "official",
        )
        slug = "chairman" if is_chair else "mayor" if is_mayor else "president"
        p.add("slug", path=f"{election.slug}/{slug}", entity_id=contest, is_current=True)
        method = election.method
        if is_chair:
            method = (
                "National totals from the Central Election Commission's summary protocol of 27 October 1992, as "
                "printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 74-75): the "
                "electorate, the participants, the invalid ballots, and the votes for Eduard Shevardnadze, the only "
                "candidate. The volume does not print the votes against him."
            )
        rs = result_set(contest, 1.0, method)
        facts = Facts(rs, office.english + office.georgian, election.unit)
        rows: list[tuple[int, Candidate, int]] = []
        if office.pattern is not None:
            rows = list(entries_of(election.slug))
        else:
            for position, candidate in enumerate(office.candidates, 1):
                if is_chair:
                    value = 2_472_345
                    mentions("(ედუარდ შევარდნაძის) მიერ მიღებული ხმების რაოდენობა – 2 472 345", office.georgian)
                else:
                    value = vote_for(candidate.printed_english or candidate.english, office.english)
                    if vote_for(candidate.georgian, office.georgian) != value:
                        raise ValueError(f"{election.slug}: {candidate.english}'s Georgian and English votes differ")
                rows.append((position, candidate, value))
        for position, candidate, value in rows:
            option = key("option", election.slug, candidate.english)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=candidate.georgian,
            )
            candidacy = key("candidacy", election.slug, candidate.english)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person(candidate.english),
                option_id=option,
                role="head",
                ballot_name=candidate.georgian,
            )
            facts.add(option, VOTES, value, (75, 76) if is_chair else office.english + office.georgian)
            if candidate.english == office.winner or position == office.winner_number:
                p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=None, kind="elected")
            elif position in office.advanced:
                p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=None, kind="advanced")
        for item in election.figures:
            facts.add(None, item.measure, item.value, election.pages)
        facts.write(p, used)

    for page in sorted(used):
        words(page)
        render(page)
        width, height = SIZES[page]
        p.add(
            "source_page",
            id=key("page", page),
            artifact_id=artifact,
            page_number=page,
            image_key=f"pages/{SOURCE}/{artifact_of_book().sha256}-{page}.png",
            width=width,
            height=height,
        )
    return p
