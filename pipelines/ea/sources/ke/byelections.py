"""The by-elections of 8 December 2022 and 5 January 2023, from the IEBC's Annual Report 2022-2023.

The report prints each candidate's votes in the ten contests (two for the Senate, two for the National Assembly, six for
a county assembly ward) and each area's registered voters and voters who turned out. The commission's result forms
for these and later by-elections are served only as downloads that its portal does not hand to this archive, so no
polling station figures are built here.
"""

import functools
import re
import subprocess
from dataclasses import dataclass
from datetime import date

from ea import raw
from ea.package import Package
from ea.sources.ke import iebc
from ea.sources.ke.iebc import CONSTITUENCY, COUNTY, KENYA, SOURCE, TERRITORY, WARD, area, key, titled
from ea.sources.ke.results import NATIONAL, REGISTERED, SENATE, VOTES, Builder

REPORT = "https://www.iebc.or.ke/uploads/resources/8tIiXFDGzt.pdf"
REPORT_SOURCE = key("source", "annual-report-2022-23")
DEC = date(2022, 12, 8)
JAN = date(2023, 1, 5)

METHOD = (
    "Counted by the Independent Electoral and Boundaries Commission and printed in its Annual Report 2022-2023, which "
    "lists each candidate's votes in the area and the registered voters and the voters who turned out. The report "
    "prints no valid or rejected votes, so the voters who turned out are stored as ballots cast, and the difference "
    "to the candidates' votes is not split between rejected ballots and any line the report leaves out. The "
    "candidate with the most votes is stored as elected, which agrees with the report's table of winners. A "
    "candidate's party is stored when the report prints one and as independent when it prints Independent."
)
# The report's registered voters for Elgeyo/Marakwet are 3,920 fewer than the 2022 roll's county total.
ROLL_DIFFERS = {"028"}
GARISSA_NOTE = (
    " The report's table of candidates for Garissa Township is numbered 1, 2, 4, 5, 6, 7 and prints no third "
    "candidate; the six lines it prints are stored."
)

# Party abbreviation: the name the archive stores. The report spells some names and abbreviations in more than one way.
PARTIES = {
    "PEP": "Peoples Empowerment Party",
    "UDA": "United Democratic Alliance",
    "VDP": "Vibrant Democratic Party",
    "A-OKA": "Azimio One Kenya Alliance",
    "RLP": "Republican Liberty Party",
    "NOPEU-P": "National Ordinary People Empowerment Union",
    "RPK": "Roots Party of Kenya",
    "FORD-KENYA": "Forum for Restoration of Democracy-Kenya",
    "TND": "The New Democrats",
    "DAP-K": "Democratic Action Party of Kenya",
    "JP": "Jubilee",
    "WDM": "Wiper Democratic Movement",
    "NAPK": "National Agenda Party of Kenya",
    "UKW": "Usawa Kwa Wote",
    "UPA": "United Progressive Alliance",
    "PDU": "Party of Democratic Unity",
    "CCM": "Chama Cha Mashinani",
    "KANU": "Kenya African National Union",
    "ELP": "Empowerment and Liberation Party",
    "MCC": "Maendeleo Chap Chap",
    "TWP": "Tujibebe Wakenya Party",
    "ODM": "Orange Democratic Movement",
    "ANC": "Amani National Congress",
    "SAFINA": "Safina",
    "LDP": "Liberal Democratic Party",
    "UDM": "United Democratic Movement",
    "CPK": "Communist Party of Kenya",
    "MDG": "Movement for Democratic Growth",
    "PPD": "Party for Peace and Democracy",
    "FORD-ASILI": "Ford Asili",
    "JFP": "Justice and Freedom Party of Kenya",
    "AZIMIO": "Azimio la Umoja One Kenya Coalition Party",
    "DP": "Democratic Party of Kenya",
    "NARC-KENYA": "National Rainbow Coalition-Kenya",
    "UGM": "United Green Movement",
}
IND = "IND"
CAST = 3


@dataclass(frozen=True)
class Contest:
    day: date
    ballot: str  # senate, assembly, or ward
    county: str
    constituency: str | None
    ward: str | None
    table: int
    title: str
    registered: int
    cast: int
    entries: tuple[tuple[str, str, int], ...]  # name as printed, party abbreviation, votes
    winner: str


def e(name: str, party: str, votes: int) -> tuple[str, str, int]:
    return name, party, votes


CONTESTS = (
    Contest(
        DEC,
        "senate",
        "039",
        None,
        None,
        30,
        "Bungoma",
        646598,
        135954,
        (
            e("Barasa S. Elias", "PEP", 549),
            e("Mabonga Moses Masika", "UDA", 45907),
            e("Makokha Enock Opara", IND, 546),
            e("Masinde Julius", IND, 1009),
            e("Munoko Raidon Wekesa", IND, 251),
            e("Simiyu Fredrick Busolo", "VDP", 564),
            e("Wamunyinyi Athanas Misiko Wafula", "A-OKA", 20519),
            e("Wanjala Aaron Wafula", "RLP", 289),
            e("Wanyonyi Godfrey Barazah", "NOPEU-P", 166),
            e("Wekesa Robert Sifuna", "RPK", 122),
            e("Wafula David Wakoli", "FORD-KENYA", 66032),
        ),
        "Wafula David Wakoli",
    ),
    Contest(
        DEC,
        "ward",
        "047",
        "285",
        "1424",
        31,
        "Utawala",
        25707,
        3330,
        (
            e("Chutha Peter Ndungu", "TND", 118),
            e("John Nyasarara Chacha", "DAP-K", 52),
            e("Karani Patrick", "JP", 1109),
            e("Kinyua Saida Mercy Njoki", IND, 30),
            e("Mireri Dickson Guto", "WDM", 5),
            e("Mokaya Daniel Nyanchoga", "NAPK", 13),
            e("Mosingi Nickson Nicodemus", IND, 110),
            e("Ngure Samson Kahui", "UDA", 847),
            e("Nyadusi Martin Maranga", "UKW", 7),
            e("Omboga Johnson Ondego", "UPA", 37),
            e("Omondi Reagan", "PDU", 17),
            e("Omwoyo Hezron Samba", IND, 640),
            e("Ruto Frankline", "CCM", 23),
            e("Sikoyo Alfred Juma", "KANU", 302),
        ),
        "Karani Patrick",
    ),
    Contest(
        DEC,
        "ward",
        "015",
        "068",
        "0336",
        32,
        "Kyome/Thaana",
        14979,
        7002,
        (
            e("David Joyce Mwende", IND, 1495),
            e("Kyala Paul Ngei", "PDU", 111),
            e("Macrice Gideon Munyithia", "UDA", 784),
            e("Muli David Muimi", "ELP", 122),
            e("Munyoki Shadrack Mulu", IND, 10),
            e("Musee Christina Katunge", IND, 11),
            e("Musili Wycliffe Mwamuli", IND, 653),
            e("Musyimi Alphonce Mukwayu", "WDM", 2365),
            e("Mutambu Jonathan Kimanzi", "MCC", 634),
            e("Mwangangi Inorvetor Katuta", IND, 774),
            e("Nguu Charles Kathingi", "TWP", 7),
        ),
        "Musyimi Alphonce Mukwayu",
    ),
    Contest(
        DEC,
        "ward",
        "037",
        "204",
        "1021",
        33,
        "Mumias North",
        9113,
        3308,
        (
            e("Hamis Hassan Washiali", "DAP-K", 12),
            e("Muchelule Yassin Waluchio", "KANU", 1525),
            e("Omwendo Rashid Rocky", "ODM", 1502),
            e("Shikolio Yasin Alli", "UDA", 79),
            e("Zakayo Mohammed Abdi", "ANC", 166),
        ),
        "Muchelule Yassin Waluchio",
    ),
    Contest(
        DEC,
        "ward",
        "033",
        "178",
        "0888",
        34,
        "Ololmasani",
        12560,
        7965,
        (
            e("Wycliff Bismark Leteipa", "SAFINA", 48),
            e("Towet Samwel Kipkoech", "UDA", 5819),
            e("Koech Kohrison Kibet", "CCM", 166),
            e("Sang Gilbert Kiprono", "ANC", 1727),
            e("Ngetich Cheruiyot", "UPA", 21),
            e("Alvin Sang Kipkoech", IND, 158),
            e("Sitienei Christopher Cheruiyot", "UKW", 26),
        ),
        "Towet Samwel Kipkoech",
    ),
    Contest(
        DEC,
        "ward",
        "041",
        "235",
        "1174",
        35,
        "South Gem",
        18560,
        7303,
        (
            e("Adino John Otieno", "LDP", 35),
            e("Audi David Onyango", "UDM", 20),
            e("Nyang'un David Aketch", IND, 44),
            e("Obiero Brian Chieng Anyango", IND, 3469),
            e("Okinyi Enock", "CPK", 11),
            e("Ondego Bernard Otieno", "MDG", 67),
            e("Otieno Kenneth Omolo", "JP", 231),
            e("Owuoth Christopher Ouma", IND, 21),
            e("Wanga Polycarp Otieno", "ODM", 3353),
        ),
        "Obiero Brian Chieng Anyango",
    ),
    Contest(
        JAN,
        "senate",
        "028",
        None,
        None,
        39,
        "Elgeyo/Marakwet",
        209964,
        83872,
        (
            e("Houlding Jerotich Seii", "SAFINA", 2660),
            e("Kemboi Kevin Kiptoo", IND, 382),
            e("Kipchumba Tim", "PPD", 38151),
            e("Kisang William Kipkemoi", "UDA", 41378),
            e("Mengich Andrew Kibiwott", IND, 503),
            e("Tanui Timothy", "TND", 397),
        ),
        "Kisang William Kipkemoi",
    ),
    Contest(
        JAN,
        "assembly",
        "021",
        "109",
        None,
        40,
        "Kandara",
        105148,
        38625,
        (
            e("Junior Kibiru Pmg", "UKW", 1469),
            e("Kioi Samuel", "TND", 201),
            e("Mbuchu Titus Njau", "FORD-ASILI", 14678),
            e("Mburu Joseph Mbugua", "JFP", 107),
            e("Muturi Pius Muiruri", IND, 100),
            e("Njuguna Chege", "UDA", 21650),
            e("Njuguna David Kamweti", IND, 207),
            e("Nyoike Erastus Gatehi", IND, 36),
        ),
        "Njuguna Chege",
    ),
    Contest(
        JAN,
        "assembly",
        "007",
        "027",
        None,
        41,
        "Garissa Township",
        53253,
        21218,
        (
            e("Ahmed Muhiadin Abdirashid", "DAP-K", 20),
            e("Amin Abdifaisal", "AZIMIO", 447),
            e("Barrow Dekow Mohamed", "UDA", 11572),
            e("Dolal Nassir Mohamed", "UDM", 8158),
            e("Mohamed Abdikhaim Osman", "DP", 59),
            e("Nur Ibrahim Malow Shurie", "NARC-KENYA", 840),
        ),
        "Barrow Dekow Mohamed",
    ),
    Contest(
        JAN,
        "ward",
        "005",
        "022",
        "0104",
        42,
        "Shella",
        3632,
        1708,
        (
            e("Mohamed Atwaa Salim", "ODM", 1053),
            e("Abduljabar Saamiya Mohamed", "UDA", 45),
            e("Ali Mohamed Hassan", "ANC", 573),
            e("Shee Nizar Mohamed", "UGM", 10),
        ),
        "Mohamed Atwaa Salim",
    ),
)


def party(abbreviation: str) -> str:
    return key("party", "annual-report-2022-23", abbreviation)


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


@functools.cache
def text() -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, REPORT).path), "-"], check=True, capture_output=True, text=True
    ).stdout


def table(n: int) -> str:
    """The report's text from the last heading of Table n to the next table or the winners' heading."""
    body = text()
    starts = [m.end() for m in re.finditer(rf"^\s*Table {n}\b", body, re.MULTILINE)]
    if not starts:
        raise ValueError(f"the report no longer prints Table {n}")
    rest = body[starts[-1] :]
    stop = re.search(r"^\s*(Table \d+|Winners)", rest, re.MULTILINE)
    return rest[: stop.start()] if stop else rest


def verify() -> None:
    """Check each keyed figure against the report's own text."""
    for c in CONTESTS:
        block = table(c.table)
        rows = len(re.findall(r"^\s*\d+\.\s", block, re.MULTILINE))
        # Table 41 skips a number, so the count of its printed lines is the keyed one.
        if rows != len(c.entries):
            raise ValueError(f"Table {c.table} prints {rows} lines, not {len(c.entries)}")
        for name, _, votes in c.entries:
            shown = f"{votes:,}"
            if not re.search(rf"(?<![\d,]){votes}(?![\d,])|(?<![\d,]){shown}(?![\d,])", block):
                raise ValueError(f"Table {c.table} does not print {votes} for {name}")
        if max(c.entries, key=lambda x: x[2])[0] != c.winner:
            raise ValueError(f"{c.title}: the leader is not the winner")
        for party_abbreviation in {a for _, a, _ in c.entries if a != IND}:
            if party_abbreviation not in PARTIES:
                raise ValueError(f"no name for {party_abbreviation}")


def roll_check(c: Contest) -> None:
    wards = [w for w in iebc.roll() if w.county == c.county]
    if c.ward:
        hit = [w for w in wards if w.code == c.ward]
        if not hit or hit[0].constituency != c.constituency or hit[0].voters != c.registered:
            raise ValueError(f"{c.title} does not match the roll")
    elif c.constituency:
        if sum(w.voters for w in wards if w.constituency == c.constituency) != c.registered:
            raise ValueError(f"{c.title} registered voters differ from the roll")
    elif sum(w.voters for w in wards) != c.registered and c.county not in ROLL_DIFFERS:
        raise ValueError(f"{c.title} registered voters differ from the roll")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry-by-elections-2022",
        SOURCE,
        "The parties on the ballots of Kenya's by-elections of 8 December 2022 and 5 January 2023",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {KENYA, TERRITORY}
    p.add(
        "source",
        id=REPORT_SOURCE,
        kind="official_body",
        publisher="Independent Electoral and Boundaries Commission",
        title="Annual Report 2022-2023",
        url=REPORT,
    )
    p.artifact(raw.cached(SOURCE, REPORT), REPORT_SOURCE)
    used = sorted({a for c in CONTESTS for _, a, _ in c.entries if a != IND})
    for abbreviation in used:
        id = party(abbreviation)
        p.add("party", id=id, kind="party", polity_id=KENYA)
        p.name(id, PARTIES[abbreviation], "en", "official")
        p.name(id, abbreviation, "en", "abbreviation", False)
    return p


def election(day: date) -> Package:
    verify()
    p = Package(
        f"{SOURCE}/by-elections-{day}",
        SOURCE,
        f"The by-elections of {day:%-d %B %Y}: "
        + (
            "a senator, and five county assembly wards"
            if day == DEC
            else "a senator, two members of the National Assembly, and a county assembly ward"
        ),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/registry-by-elections-2022"],
    )
    p.external |= {KENYA, TERRITORY}
    p.uses(raw.cached(SOURCE, REPORT))
    path = f"ke/{day}-by-election"
    b = Builder(p, day, REPORT_SOURCE, path)
    eid = key("election", day, "by-election")
    p.add("election", id=eid, polity_id=KENYA, kind="by_election", date_from=day, date_to=day, status="held")
    p.name(eid, f"Kenyan by-elections of {day:%-d %B %Y}", "en", "official")
    p.add("slug", path=path, entity_id=eid, is_current=True)
    method = METHOD + (GARISSA_NOTE if day == JAN else "")
    p.add("note", id=key("note", eid), entity_id=eid, kind="methodology", body_md=method, lang="en")
    roll = {w.county: titled(w.county_name) for w in iebc.roll()}
    for c in (c for c in CONTESTS if c.day == day):
        roll_check(c)
        tag = f"{c.ballot}:{c.ward or c.constituency or c.county}"
        if c.ballot == "senate":
            level, code, district = COUNTY, c.county, area(COUNTY, c.county)
            title, slug, body = f"Senator for {c.title} County", ("senator", c.title), SENATE
        elif c.ballot == "assembly":
            level, code = CONSTITUENCY, f"{c.county}/{c.constituency}"
            district = area(CONSTITUENCY, code)
            title, slug, body = (
                f"Member of the National Assembly for {c.title}",
                ("national-assembly", c.title),
                NATIONAL,
            )
        else:
            assert c.ward
            level, code, district = WARD, c.ward, area(WARD, c.ward)
            title = f"Member of the {roll[c.county]} County Assembly for {c.title} Ward"
            slug, body = ("county-assembly", c.ward, c.title), key("body", "county-assembly", c.county)
        contest, result_set = b.contest(eid, tag, title, b.slug(eid, *slug), district, body=body)
        unit = b.unit(level, code)
        b.put(result_set, unit, None, REGISTERED, c.registered)
        b.put(result_set, unit, None, CAST, c.cast)
        options: list[tuple[str, int | None]] = []
        for position, (name, abbreviation, votes) in enumerate(c.entries, 1):
            option = key("option", day, tag, position)
            person = key("person", day, tag, position)
            label = titled(name)
            member = party(abbreviation) if abbreviation != IND else None
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_position=position,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, tag, position, "head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=member,
                is_independent=True if abbreviation == IND else None,
            )
            b.put(result_set, unit, option, VOTES, votes)
            options.append((option, votes))
        b.outcomes(result_set, options)
    return p


def december() -> Package:
    return election(DEC)


def january() -> Package:
    return election(JAN)
