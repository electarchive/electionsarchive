"""Uzbekistan's presidential elections of 2000, 2007, 2015, 2016, 2021, and 2023, nationally, from the Central
Election Commission's website.

The Commission publishes each result as a page of prose on saylov.uz, so the archive holds national totals only: the
votes of each candidate, the registered voters, and the voters who took part, as the page words them. Two pages need
more care.

- The 2021 page and decision No. 1154 print percentages only, so the candidates' shares and the turnout are stored as
  shares, with no counts.
- The 2023 page truncates Mirziyoyev's votes ("13 625 55"). The page prints the voters who took part, every other
  candidate's votes, and the invalid ballots, and those leave exactly 13,625,055 for him, which is the printed digits
  with the missing 0 restored and gives the 87.05 percent that decision No. 1318 declares. His votes are stored as
  derived.

Each figure is checked against the phrase in the page that prints it, and each printed share against the votes. The
pages for 2000 and 2015 print shares that disagree with their votes; `DISAGREE` names them, and every other printed
share agrees with the votes to within rounding.
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "uz.saylov"
UZBEKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fUZ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fUZB"))
SCHEME = "uz.nation"
PAGES = "https://saylov.uz/ru/elections-and-referendums/page"
DECISIONS = "https://saylov.uz/ru/cec_decision"
VOTES, REGISTERED, CAST, INVALID, VOTE_SHARE = 1, 2, 3, 5, 13
DERIVED, SHARES, DISPUTED = 1, 4, 16

URL_1154 = f"{DECISIONS}/1154-o-rezultatah-vyborov-prezidenta-respubliki-uzbekistan-provedennyh-24-oktyabrya-2021-goda"
URL_1318 = (
    f"{DECISIONS}/1318-o-rezultatah-dosrochnyh-vyborov-prezidenta-respubliki-uzbekistan-provedennyh-9-iyulya-2023-goda"
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "presidential-two-round")

# party key: (English name, Russian name as the pages print it)
PARTIES = {
    "fidokorlar": ("National Democratic Party Fidokorlar", "Национально-демократическая партия «Фидокорлар»"),
    "ndpu": ("People's Democratic Party of Uzbekistan", "Народно-демократическая партия Узбекистана"),
    "uzlidep": (
        "Movement of Entrepreneurs and Businesspeople, Liberal Democratic Party of Uzbekistan",
        "Движение предпринимателей и деловых людей, Либерально-демократическая партия Узбекистана",
    ),
    "adolat": ("Social Democratic Party of Uzbekistan Adolat", "Социал-демократическая партия Узбекистана «Адолат»"),
    "milliy": ("Democratic Party Milliy Tiklanish", "Демократическая партия «Миллий тикланиш»"),
    "eco": ("Ecological Party of Uzbekistan", "Экологическая партия Узбекистана"),
}


@dataclass(frozen=True)
class Candidate:
    name: str  # as the page names the candidate, in the nominative
    party: str | None  # a key of PARTIES; None for a candidate of an initiative group
    phrase: str | None  # the page's own words for the candidate's votes
    votes: int | None
    share: str | None  # the percentage as the page prints it
    flags: int = 0


@dataclass(frozen=True)
class Election:
    year: int
    day: date
    page: int
    registered: tuple[str, int] | None  # the page's words, then the number
    cast: tuple[str, int] | None
    invalid: tuple[str, int] | None
    candidates: tuple[Candidate, ...]
    winner: int  # position of the elected candidate
    turnout: str | None = None  # the printed turnout, when the page prints no counts
    decision: tuple[str, str] | None = None  # the decision's URL and its title
    phrases: tuple[str, ...] = ()  # further phrases the pages must contain


ELECTIONS = (
    Election(
        2000,
        date(2000, 1, 9),
        5,
        ("12 миллионов 746 тысячи 903", 12_746_903),
        ("12 миллионов 123 тысяч 199", 12_123_199),
        None,
        (
            Candidate("Ислам Абдуганиевич Каримов", "fidokorlar", "11 миллиона 147 тысяч 621", 11_147_621, "91,90"),
            Candidate("Абдулхафиз Джалалов", "ndpu", "505 тысяч 161", 505_161, "4,71"),
        ),
        1,
        phrases=("95,10 процента",),
    ),
    Election(
        2007,
        date(2007, 12, 23),
        4,
        ("16 млн. 297 тыс. 400", 16_297_400),
        ("14 млн. 765 тыс. 444", 14_765_444),
        None,
        (
            Candidate("Ислам Абдуганиевич Каримов", "uzlidep", "13 миллиона 8 тысяч 357", 13_008_357, "88,1"),
            Candidate("Аслиддин Ашурбаевич Рустамов", "ndpu", "468 тысяч 64", 468_064, "3,17"),
            Candidate("Акмал Холматович Саидов", None, "420 тысяч 815", 420_815, "2,85"),
            Candidate("Дилором Гофуржоновна Тошмухамедова", "adolat", "434 тысячи 111", 434_111, "2,9"),
        ),
        1,
        phrases=("90,6%",),
    ),
    Election(
        2015,
        date(2015, 3, 29),
        3,
        ("20 млн. 798 тыс. 52", 20_798_052),
        ("18 млн. 942 тыс. 349", 18_942_349),
        None,
        (
            Candidate("Ислам Абдуганиевич Каримов", "uzlidep", "17 млн. 122 тысяч 597", 17_122_597, "90,39"),
            Candidate("Акмал Холматович Саидов", "milliy", "583 309", 583_309, "2,92"),
            Candidate("Хотамжан Абдурахмонович Кетмонов", "ndpu", "552 309", 552_309, "2,92"),
            Candidate("Нариман Мажитович Умаров", "adolat", "619 тысяч 972", 619_972, "3,46", DISPUTED),
        ),
        1,
        phrases=("91,08%",),
    ),
    Election(
        2016,
        date(2016, 12, 4),
        1,
        ("20 миллионов 461 тысяч 805", 20_461_805),
        ("17 миллионов 951 тысяч 667", 17_951_667),
        None,
        (
            Candidate("Сарвар Садуллаевич Отамуратов", None, "421 тысяч 55", 421_055, "2,35"),
            Candidate("Шавкат Миромонович Мирзиёев", "uzlidep", "15 миллиона 906 тысяч 724", 15_906_724, "88,61"),
            Candidate("Хатамжан Абдурахманович Кетмонов", "ndpu", "669 тысяч 187", 669_187, "3,73"),
            Candidate("Нариман Мажитович Умаров", "adolat", "619 тысяч 972", 619_972, "3,46"),
        ),
        2,
        phrases=("87,73 процента",),
        decision=(
            "https://saylov.uz/storage/cec-decisions/February2024/x4oSBjVZMDmwPYWjw2Gh.pdf",
            "Decision No. 794 of 9 December 2016 on the results of the presidential election of 4 December 2016",
        ),
    ),
    Election(
        2021,
        date(2021, 10, 24),
        11,
        None,
        None,
        None,
        (
            Candidate("Бахром Абдухалимов", "adolat", None, None, "3,39"),
            Candidate("Нарзулло Обломурадов", "eco", None, None, "4,14"),
            Candidate("Алишер Кодиров", "milliy", None, None, "5,48"),
            Candidate("Максуда Варисова", "ndpu", None, None, "6,63"),
            Candidate("Шавкат Мирзиёев", "uzlidep", None, None, "80,12"),
        ),
        5,
        turnout="80,4",
        decision=(URL_1154, "Decision No. 1154 of 29 October 2021 on the results of the presidential election"),
    ),
    Election(
        2023,
        date(2023, 7, 9),
        12,
        ("19 593 838", 19_593_838),
        ("15 651 405", 15_651_405),
        ("117 889", 117_889),
        (
            Candidate("Улугбек Ильясович Иноятов", "ndpu", "629 116", 629_116, "4,02"),
            Candidate("Робахон Анваровна Махмудова", "adolat", "693 634", 693_634, "4,43"),
            Candidate("Шавкат Миромонович Мирзиёев", "uzlidep", "13 625 55", 13_625_055, "87,05", DERIVED),
            Candidate("Абдушукур Худойкулович Хамзаев", "eco", "585 711", 585_711, "3,74"),
        ),
        3,
        decision=(URL_1318, "Decision No. 1318 of 11 July 2023 on the results of the early presidential election"),
    ),
)
# Printed shares that disagree with the votes by more than rounding allows, with the share the votes give.
DISAGREE = {
    (2000, "Абдулхафиз Джалалов"): "4.17",
    (2015, "Акмал Холматович Саидов"): "3.08",
    (2015, "Нариман Мажитович Умаров"): "3.27",
}
ROUNDING = Decimal("0.06")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/president-{e.year}" for e in ELECTIONS)]


def urls(e: Election) -> list[str]:
    return [f"{PAGES}/{e.page}", *([e.decision[0]] if e.decision else [])]


def fetch() -> None:
    for e in ELECTIONS:
        for url in urls(e):
            raw.fetch(SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".html")


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        build(e).write()


def emit() -> None:
    package.run("load", packages())


@cache
def text(url: str) -> str:
    """The words of a page or decision, with spaces collapsed."""
    path = raw.cached(SOURCE, url).path
    if url.endswith(".pdf"):
        import subprocess

        body = subprocess.run(["pdftotext", str(path), "-"], check=True, capture_output=True, text=True).stdout
    else:
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", body)


def percent(votes: int, base: int) -> Decimal:
    return (Decimal(votes) * 100 / Decimal(base)).quantize(Decimal("0.01"))


def prove(e: Election) -> None:
    """Every figure must stand in the page that prints it, and every printed share must agree with its votes."""
    page = text(f"{PAGES}/{e.page}")
    for words in (e.registered, e.cast, e.invalid):
        if words is not None and words[0] not in page:
            raise ValueError(f"{e.year}: {words[0]!r} is not on the page")
    for phrase in e.phrases:
        if phrase not in page:
            raise ValueError(f"{e.year}: {phrase!r} is not on the page")
    for c in e.candidates:
        if c.phrase is not None and c.phrase not in page:
            raise ValueError(f"{e.year}: {c.phrase!r} is not on the page")
        if e.year == 2021 and f"{c.share}%" not in page:
            raise ValueError(f"2021: the share {c.share} of {c.name} is not on the page")
    if e.year == 2021 and (f"{e.turnout}%" not in page or "80,12 процентами" not in text(URL_1154)):
        raise ValueError("2021: the turnout or the winner's share is not where it should be")
    if e.year == 2023 and "87,05 процентами" not in text(URL_1318):
        raise ValueError("2023: decision No. 1318 does not declare 87.05 percent")
    if e.cast is None:
        return
    base = e.cast[1]
    if e.registered is not None and e.cast[1] > e.registered[1]:
        raise ValueError(f"{e.year}: more voters took part than were registered")
    total = sum(c.votes or 0 for c in e.candidates) + (e.invalid[1] if e.invalid else 0)
    if total > base:
        raise ValueError(f"{e.year}: the votes exceed the voters who took part")
    if e.invalid is not None and total != base:
        raise ValueError(f"{e.year}: the candidates' votes and the invalid ballots leave {base - total} unaccounted")
    for c in e.candidates:
        assert c.votes is not None and c.share is not None
        shown = Decimal(c.share.replace(",", "."))
        found = percent(c.votes, base)
        if abs(shown - found) <= ROUNDING:
            continue
        if DISAGREE.get((e.year, c.name)) != str(found):
            raise ValueError(f"{e.year}: {c.name} has {c.votes} votes, {found} percent, and the page prints {shown}")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The national reporting scheme of Uzbekistan's presidential elections, from the Central Election Commission",
    )
    p.external |= {UZBEKISTAN, COUNTRY}
    p.add("scheme", code=SCHEME, polity_id=UZBEKISTAN, kind="administrative", rank=0, noun="nation")
    return p


def source_id(kind: str, *parts: object) -> str:
    return key("source", kind, *parts)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Uzbekistan's presidency, its electoral system, the parties that nominated candidates, and the Central "
        "Election Commission's pages and decisions that give the results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(UZBEKISTAN)
    for e in ELECTIONS:
        page = f"{PAGES}/{e.page}"
        sid = source_id("page", e.year)
        p.add(
            "source",
            id=sid,
            kind="official_body",
            publisher="Центральная избирательная комиссия Республики Узбекистан (Central Election Commission of "
            "the Republic of Uzbekistan)",
            title=f"Results of the {e.year} presidential election, as the Commission's website states them",
            url=page,
            license="Official publication; the site asks that its materials be cited when used",
            attribution="Central Election Commission of the Republic of Uzbekistan, saylov.uz",
        )
        p.artifact(raw.cached(SOURCE, page), sid)
        if e.decision:
            did = source_id("decision", e.year)
            p.add(
                "source",
                id=did,
                kind="official_body",
                publisher="Центральная избирательная комиссия Республики Узбекистан (Central Election Commission of "
                "the Republic of Uzbekistan)",
                title=e.decision[1],
                url=e.decision[0],
                license="Official publication; the site asks that its materials be cited when used",
                attribution="Central Election Commission of the Republic of Uzbekistan, saylov.uz",
            )
            p.artifact(raw.cached(SOURCE, e.decision[0]), did)
    p.add("office", id=PRESIDENCY, polity_id=UZBEKISTAN, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Uzbekistan", "en", "official")
    p.name(PRESIDENCY, "Президент Республики Узбекистан", "ru", "official", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=UZBEKISTAN,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The President is elected by the voters of the whole country. A candidate with more than half of the "
            "votes of the voters who took part is elected, and otherwise the two leading candidates go to a second "
            "round. Every election held here was decided in the first round."
        ),
        lang="en",
    )
    for code, (english, russian) in PARTIES.items():
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=UZBEKISTAN)
        p.name(id, english, "en", "official")
        p.name(id, russian, "ru", "native", False)
    return p


def caveats(e: Election) -> list[str]:
    out: list[str] = []
    if e.year == 2000:
        out.append(
            "The page prints 4.71 percent for Jalalov, while his 505,161 votes are 4.17 percent of the 12,123,199 "
            "voters who took part, so the printed share looks like a transposition. It prints 91.90 percent for "
            "Karimov, while his votes are 91.95 percent. The votes are stored as printed, and no shares."
        )
    if e.year == 2015:
        out.append(
            "Two lines of the page repeat figures from elsewhere. Umarov's 619,972 votes and 3.46 percent are the "
            "figures the 2016 page prints for him, and the 3.46 percent does not fit this election's 18,942,349 "
            "voters (his votes would be 3.27 percent). Saidov's printed 2.92 percent repeats Ketmonov's, and his "
            "583,309 votes are 3.08 percent. The Commission's site holds no decision on this election to check "
            "them against. The other lines, and the sum, agree: the four candidates' votes leave 64,162 voters "
            "unaccounted for, 0.34 percent, which would be invalid ballots. Umarov's votes are stored as printed "
            "and marked disputed, with no declared total for them; the printed shares are not stored."
        )
    if e.year == 2016:
        out.append(
            "The four candidates' votes leave 334,729 voters who took part unaccounted for, which would be the "
            "invalid ballots, but the page does not print them. Decision No. 794 declares the winner with 88.61 "
            "percent, as the page prints."
        )
    if e.year == 2007:
        out.append(
            "The candidates' votes leave 434,097 voters who took part unaccounted for, which would be the invalid "
            "ballots, but the page does not print them. The page names Akmal Saidov as the candidate of an "
            "initiative group of voters."
        )
    if e.year == 2021:
        out.append(
            "The page and decision No. 1154 print percentages only. Each candidate's share is stored as printed, "
            "flagged as a share published without counts, and the turnout of 80.4 percent of the registered voters "
            "is stored the same way as a share of the unit's voters. The page names more than 20 million "
            "registered voters without giving the count, so no counts are stored. The shares add up to 99.76 "
            "percent."
        )
    if e.year == 2023:
        out.append(
            "The page prints Mirziyoyev's votes as 13 625 55 and his share as 87, 5, both truncated. The voters who "
            "took part (15,651,405) less the other three candidates' votes and the 117,889 invalid ballots leave "
            "13,625,055, which fits the printed digits with a 0 restored and gives 87.05 percent, the share "
            "decision No. 1318 declares. His votes are stored as that figure, marked derived, with no declared "
            "total. The page prints the invalid share as 0.76 percent, and the ballots are 0.75 percent."
        )
    if e.registered is None and e.year != 2021:
        raise ValueError(f"{e.year}: no registered voters")
    return out


def build(e: Election) -> Package:
    prove(e)
    day = e.day.isoformat()
    p = Package(
        f"{SOURCE}/president-{e.year}",
        SOURCE,
        f"The {e.year} presidential election of Uzbekistan, national totals, from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {UZBEKISTAN, COUNTRY}
    for url in urls(e):
        p.uses(raw.cached(SOURCE, url))
    source = source_id("page", e.year)
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=UZBEKISTAN,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    p.add("slug", path=f"uz/{day}", entity_id=election, is_current=True)
    p.name(election, f"{e.year} Uzbekistani presidential election", "en", "official")
    p.name(election, f"Выборы Президента Республики Узбекистан {e.year} года", "ru", "native", False)
    contest = key("contest", day, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{e.year} Uzbekistani presidential election", "en", "official")
    p.name(contest, f"Выборы Президента Республики Узбекистан {e.year} года", "ru", "native", False)
    p.add("slug", path=f"uz/{day}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day, "president")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = (
        "National totals as the Central Election Commission's website words them in prose: the page prints no "
        "figures below the country. Each figure was read from the page and checked against the phrase that prints "
        "it, and the printed shares were checked against the votes."
        + (" The winner is also declared in the cited decision of the Commission." if e.decision else "")
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for index, caveat in enumerate(caveats(e)):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", body_md=caveat, lang="en")
    if e.decision:
        p.add("citation", id=key("citation", day), entity_id=result_set, source_id=source_id("decision", e.year))
    unit = key("unit", day, "nation")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=UZBEKISTAN,
        scheme_code=SCHEME,
        code=f"{day}:uz",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    p.name(unit, "Республика Узбекистан", "ru", "official")
    p.name(unit, "Uzbekistan", "en", "official", False)
    p.add(
        "reporting_unit_area",
        unit_id=unit,
        area_id=COUNTRY,
        method="official_list",
        approximate=False,
        partial=False,
    )

    def put(option: str | None, measure: int, value: int | Decimal, flags: int = 0) -> None:
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

    for measure, words in ((REGISTERED, e.registered), (CAST, e.cast), (INVALID, e.invalid)):
        if words is not None:
            put(None, measure, words[1])
    if e.turnout is not None:
        put(None, VOTE_SHARE, Decimal(e.turnout.replace(",", ".")), SHARES)
    for position, c in enumerate(e.candidates, 1):
        person = key("person", day, position)
        option = key("option", day, position)
        party = key("party", c.party) if c.party else None
        p.add("person", id=person)
        p.name(person, c.name, "ru", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", day, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party,
            is_independent=party is None,
        )
        elected = position == e.winner
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if elected else "lost",
            seats=1 if elected else None,
        )
        if c.votes is None:
            assert c.share is not None
            put(option, VOTE_SHARE, Decimal(c.share.replace(",", ".")), SHARES)
            continue
        put(option, VOTES, c.votes, c.flags)
        if not c.flags:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)
    return p
