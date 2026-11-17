"""Georgia's national elections of 1990 to 2000, from the Central Election Commission's electoral history.

The Commission's volume "Electoral History of Georgia 1990-2018" (history.cec.gov.ge) prints, for each election, the
figures of the Commission's summary protocol in Georgian and in English: the electorate, turnout, invalid ballots, the
votes of each party list or presidential candidate, and the seats each list won. The volume is a typeset PDF with a
text layer, so every number is read from the text with its page and word box, and the Georgian and English pages must
agree before anything is written.

This module holds the elections the volume reports at the national level:

- the Supreme Council of 28 October 1990 (its party-list tier; runoffs on 11 November were majoritarian only);
- the President of 26 May 1991;
- the Parliament and the Chairman of Parliament of 11 October 1992;
- the Parliament and the President of 5 November 1995;
- the Parliament of 31 October 1999;
- the President of 9 April 2000.

The volume prints no result by district or precinct for these elections, and names no majoritarian winners, so the
single-member districts of each Parliament are not stored; their seat totals by party appear in each election's note.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from functools import cache
from html import unescape
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "ge.cec_1990s"
GEORGIA = str(uuid5(package.NAMESPACE, "polity\x1fGE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGEO"))
BOOK = "https://history.cec.gov.ge/PDF/ElectoralHistoryOfGeorgia.pdf"
HOME = "https://history.cec.gov.ge/"
SCHEME = "ge.nation"

VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14


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


def book() -> Path:
    return raw.cached(SOURCE, BOOK).path


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
    """The first page and box where the volume prints `value`, its digit groups as consecutive words."""
    groups = printed(value).split(" ")
    last = re.compile(re.escape(groups[-1]) + r"(?:[.,;:%)“”\"]+|\.\d+)?")
    for page in pages:
        found = words(page)
        for start in range(len(found) - len(groups) + 1):
            run = found[start : start + len(groups)]
            if [w.text.lstrip("–-(") for w in run[:-1]] == groups[:-1] and last.fullmatch(run[-1].text.lstrip("–-(")):
                if start > 0 and adjacent(found[start - 1], run[0]) and re.fullmatch(r"\d{1,3}", found[start - 1].text):
                    continue
                after = start + len(groups)
                if (
                    run[-1].text == groups[-1]
                    and after < len(found)
                    and adjacent(run[-1], found[after])
                    and re.match(r"\d{3}\b", found[after].text)
                ):
                    continue
                box = (min(w.x0 for w in run), min(w.y0 for w in run), max(w.x1 for w in run), max(w.y1 for w in run))
                return page, box
    raise ValueError(f"the volume does not print {printed(value)} on pages {pages}")


NUM = r"(\d{1,3}(?: \d{3})*)"
NAME = r"((?:(?!^(?:№|N)?\d{1,2}\.\s).)+?)"


def listing(pages: tuple[int, ...], pattern: str) -> dict[int, tuple[str, int]]:
    """Numbered entries on the pages: ballot number, the name as printed, and the figure printed after it."""
    joined = "\n".join(text(page) for page in pages)
    out: dict[int, tuple[str, int]] = {}
    for match in re.finditer(pattern, joined, re.DOTALL | re.MULTILINE):
        number = int(match.group(1))
        name = re.sub(r"\s+", " ", match.group(2)).strip(" –-,")
        name = re.sub(r"(\w)- (\w)", r"\1-\2", name)
        out.setdefault(number, (name, int(match.group(3).replace(" ", ""))))
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


LIST_1990 = r"^(?:№|N)(\d{1,2})\.\s+" + NAME + r"\s*–\s*" + NUM + r"\s*(?:–\s*)?\d+\.\d+ ?%"
SEATS_1992 = r"^(\d{1,2})\.\s+" + NAME + r"\s*–\s*(\d{1,2})\s+(?:mandates?|მანდატი)"
LIST_1995 = r"^(\d{1,2})\.\s+" + NAME + r"\n?" + NUM + r" \d+\.\s?\d*\s*$"
LIST_1999 = r"^(\d{1,2})\.\s+" + NAME + r"\s*[–-]\s*\n?" + NUM + r" – "

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
)


def package_key(election: Election) -> str:
    return f"{SOURCE}/{election.slug.removeprefix('ge/')}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS)]


def fetch() -> None:
    # The Commission's site refuses scripted clients; the file reached the cache with raw.store from a copy saved
    # from the Commission's page.
    raw.cached(SOURCE, BOOK)


def parse() -> None:
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
    return out


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Georgia's Supreme Council of 1990, its Parliament, President, and Chairman of Parliament, the electoral "
        "systems of 1990 to 2000, and the presidential candidates, from the Central Election Commission",
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

    p.add("body", id=SUPREME_COUNCIL, polity_id=GEORGIA, kind="unicameral")
    p.name(SUPREME_COUNCIL, "Supreme Council of the Republic of Georgia", "en", "english")
    p.name(SUPREME_COUNCIL, "საქართველოს რესპუბლიკის უზენაესი საბჭო", "ka", "official")
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
    return p


def _source(p: Package) -> str:
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
    return p.artifact(raw.cached(SOURCE, BOOK), CEC)


class Facts:
    """Result values for one result set at the national unit, each with its page and box in the volume."""

    def __init__(self, result_set: str, pages: tuple[int, ...]) -> None:
        self.result_set = result_set
        self.pages = pages
        self.rows: list[dict[str, object]] = []

    def add(self, option: str | None, measure: int, value: int, pages: tuple[int, ...] | None = None) -> None:
        page, (x0, y0, x1, y1) = locate(value, pages or self.pages)
        self.rows.append(
            {
                "result_set_id": self.result_set,
                "unit_id": UNIT,
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
    english = listing(spec.english, spec.pattern)
    georgian = listing(spec.georgian, spec.pattern)
    if len(english) != spec.count or set(english) != set(georgian):
        raise ValueError(
            f"{election.slug}: {len(english)} English and {len(georgian)} Georgian entries, expected {spec.count}: "
            f"{sorted(set(english) ^ set(georgian))}"
        )
    out: dict[int, tuple[str, str, int]] = {}
    for number, (name, value) in english.items():
        if georgian[number][1] != value:
            raise ValueError(f"{election.slug} list {number}: English {value}, Georgian {georgian[number][1]}")
        out[number] = (name, georgian[number][0], value)
    if not spec.votes and sum(value for _, _, value in out.values()) != spec.magnitude:
        raise ValueError(f"{election.slug}: the seats do not add up to {spec.magnitude}")
    for phrase in spec.seat_phrases:
        mentions(phrase, election.pages)
    return out


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        package_key(election),
        SOURCE,
        f"The {election.english} on {day:%-d %B %Y}: national totals from the Central Election Commission's summary "
        "protocol",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {GEORGIA, COUNTRY}
    for code, reason in election.accepted:
        p.accept(code, reason)
    artifact = _source(p)
    used: set[int] = set()
    for item in election.figures:
        mentions(item.label, election.pages)

    id = key("election", election.slug)
    p.add("election", id=id, polity_id=GEORGIA, kind="general", date_from=day, date_to=day, status="held")
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
            kind="body_seats",
            body_id=election.body,
            district_area_id=COUNTRY,
            magnitude=spec.magnitude,
            round_number=1,
            electoral_system_id=spec.system,
            default_result_kind="counted",
        )
        if spec.votes:
            p.name(contest, f"{day.year} party lists", "en", "english")
            p.name(contest, "პროპორციული სისტემა", "ka", "official")
        else:
            p.name(contest, f"{day.year} multi-member districts, seats by party", "en", "english")
            p.name(contest, "მრავალმანდატიანი საარჩევნო ოლქები", "ka", "official")
        p.add("slug", path=f"{election.slug}/party-lists", entity_id=contest, is_current=True)
        rs = result_set(contest, 1.0 if spec.votes else 0.0, election.method)
        if election.caveat:
            p.add("note", id=key("note", rs, "caveat"), entity_id=rs, kind="caveat", body_md=election.caveat, lang="en")
        facts = Facts(rs, election.pages)
        for number, (english, georgian, value) in sorted(entries.items()):
            party = key("party", day.year, number)
            bloc = number in spec.blocs or "bloc" in english.lower() or georgian.startswith("ბლოკი")
            p.add("party", id=party, kind="bloc" if bloc else "party", polity_id=GEORGIA)
            p.name(party, english, "en", "english")
            p.name(party, georgian, "ka", "official")
            option = key("option", day.year, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=number,
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
            magnitude=1,
            round_number=1,
            electoral_system_id=office.system,
            default_result_kind="counted",
        )
        title = "Chairman of Parliament" if is_chair else "President"
        p.name(contest, f"{day.year} {title}", "en", "english")
        p.name(contest, "პარლამენტის თავმჯდომარე" if is_chair else "პრეზიდენტი", "ka", "official")
        p.add("slug", path=f"{election.slug}/{'chairman' if is_chair else 'president'}", entity_id=contest,
              is_current=True)  # fmt: skip
        method = election.method
        if is_chair:
            method = (
                "National totals from the Central Election Commission's summary protocol of 27 October 1992, as "
                "printed in the Commission's volume Electoral History of Georgia 1990-2018 (pages 74-75): the "
                "electorate, the participants, the invalid ballots, and the votes for Eduard Shevardnadze, the only "
                "candidate. The volume does not print the votes against him."
            )
        rs = result_set(contest, 1.0, method)
        facts = Facts(rs, office.english + office.georgian)
        for position, candidate in enumerate(office.candidates, 1):
            if is_chair:
                value = 2_472_345
                mentions("(ედუარდ შევარდნაძის) მიერ მიღებული ხმების რაოდენობა – 2 472 345", office.georgian)
            else:
                value = vote_for(candidate.printed_english or candidate.english, office.english)
                if vote_for(candidate.georgian, office.georgian) != value:
                    raise ValueError(f"{election.slug}: {candidate.english}'s Georgian and English votes differ")
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
            if candidate.english == office.winner:
                p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=None, kind="elected")
        for item in election.figures:
            facts.add(None, item.measure, item.value, election.pages)
        facts.write(p, used)

    for page in sorted(used):
        words(page)
        width, height = SIZES[page]
        p.add(
            "source_page",
            id=key("page", page),
            artifact_id=artifact,
            page_number=page,
            image_key=f"pages/{SOURCE}/{raw.cached(SOURCE, BOOK).sha256}-{page}.png",
            width=width,
            height=height,
        )
    return p
