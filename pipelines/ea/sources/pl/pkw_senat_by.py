import csv
import html
import io
import re
import subprocess
import zipfile
from datetime import date
from functools import cache
from typing import TypedDict
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Package
from ea.store import connect

SOURCE = "pl.pkw_senat_by"
ELI = "https://api.sejm.gov.pl/eli/acts"
POLAND_ISO = "PL"
VOTES, REGISTERED, BALLOTS, VALID_BALLOTS, INVALID = 1, 2, 3, 4, 5
UPPER = "A-ZĄĆĘŁŃÓŚŹŻ"
NUM = r"\d+(?:\s\d{3})*"
MODERN = date(2011, 10, 9)
BLOCK_2001 = date(2001, 9, 23)
DATA_FILES = {
    date(2024, 7, 21): ("https://senat20240721.pkw.gov.pl/senat20240721/data/csv/{}_csv.1730206537.zip"),
    date(2025, 3, 16): ("https://wybory.gov.pl/senat20250316/data/csv/{}_csv.1746468685.zip"),
}
DATA_NAMES = ("protokoly_po_obwodach", "kandydaci_senat")
NOTICE_SOURCE_KEY = "notice"
PKW_SOURCE_KEY = "pkw"


class Candidate(TypedDict):
    surname: str
    given: str
    committee: str | None
    votes: int
    elected: bool
    number: int


class Contest(TypedDict):
    date: date
    round: int
    district: str
    label: str
    seats: int
    registered: int | None
    ballots: int | None
    invalid: int | None
    valid: int | None
    cands: list[Candidate]
    locator: str
    advanced: tuple[str, ...]


NOTICES: list[tuple[str, date, str]] = [
    ("DU/2025/354", date(2025, 3, 16), "pdf"),
    ("DU/2024/1102", date(2024, 7, 21), "html"),
    ("DU/2016/294", date(2016, 3, 6), "html"),
    ("DU/2015/217", date(2015, 2, 8), "html"),
    ("DU/2014/1208", date(2014, 9, 7), "html"),
    ("DU/2013/1075", date(2013, 9, 8), "html"),
    ("DU/2013/498", date(2013, 4, 21), "html"),
    ("DU/2011/192", date(2011, 2, 6), "html"),
    ("DU/2010/747", date(2010, 6, 20), "html"),
    ("DU/2008/705", date(2008, 6, 22), "html"),
    ("DU/2007/105", date(2007, 1, 28), "html"),
    ("DU/2006/117", date(2006, 1, 22), "html"),
    ("DU/2005/110", date(2005, 1, 16), "html"),
    ("DU/2004/2555", date(2004, 11, 21), "html"),
    ("DU/2004/2089", date(2004, 9, 12), "html"),
    ("DU/2004/1713", date(2004, 7, 11), "html"),
    ("DU/2004/552", date(2004, 3, 28), "html"),
    ("DU/2003/1757", date(2003, 10, 12), "html"),
    ("DU/2003/771", date(2003, 4, 27), "html"),
    ("MP/2001/82", date(2001, 1, 28), "pdf"),
    ("MP/2000/396", date(2000, 6, 25), "pdf"),
    ("MP/1995/567", date(1995, 10, 1), "pdf"),
    ("MP/1994/303", date(1994, 6, 19), "pdf"),
    ("MP/1990/180", date(1990, 6, 10), "pdf"),
    ("MP/1990/172", date(1990, 5, 27), "pdf"),
    ("MP/1990/56", date(1990, 2, 18), "pdf"),
    ("MP/1990/38", date(1990, 2, 4), "pdf"),
    ("MP/1989/269", date(1989, 10, 8), "pdf"),
]
VOIVODESHIPS = {
    "piotrkowskie": ("Piotrków", "piotrkowskiego"),
    "opolskie": ("Opole", "opolskiego"),
    "lubelskie": ("Lublin", "lubelskiego"),
    "chełmskie": ("Chełm", "chełmskiego"),
    "elbląskie": ("Elbląg", "elbląskiego"),
    "szczecińskie": ("Szczecin", "szczecińskiego"),
    "wrocławskie": ("Wrocław", "wrocławskiego"),
    "katowickie": ("Katowice", "katowickiego"),
}


def notice_url(eli: str, kind: str) -> str:
    return f"{ELI}/{eli}/text.{kind}"


def data_urls(day: date) -> list[str]:
    return [DATA_FILES[day].format(name) for name in DATA_NAMES]


def fetch() -> None:
    for eli, _, kind in NOTICES:
        raw.fetch(SOURCE, notice_url(eli, kind), suffix=f".{kind}")
    for day in DATA_FILES:
        for address in data_urls(day):
            raw.fetch(SOURCE, address, suffix=".zip")


def notice_text(eli: str, kind: str) -> str:
    path = raw.cached(SOURCE, notice_url(eli, kind)).path
    if kind == "pdf":
        return subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
        ).stdout
    text = path.read_text()
    text = re.sub(r"<(br|/p|/tr|/div|/li)[^>]*>", "\n", text.split("var tree")[0])
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def number(text: str) -> int:
    return int(re.sub(r"\s", "", text))


def split_name(text: str) -> tuple[str, str]:
    tokens = text.replace(",", " ").split()
    shouting = [token for token in tokens if len(token) > 1 and token == token.upper() and token[0].isalpha()]
    if shouting:
        return " ".join(shouting), " ".join(token for token in tokens if token not in shouting)
    return tokens[0], " ".join(tokens[1:])


def accounting(segment: str) -> dict[str, int | None]:
    def find(*patterns: str) -> int | None:
        for pattern in patterns:
            match = re.search(pattern, segment)
            if match:
                return number(match[1])
        return None

    return {
        "registered": find(
            r"uprawnionych do głosowania (?:wynosiła|wyniosła|było) (" + NUM + ")",
            r"Uprawnionych do głosowania było (" + NUM + ")",
        ),
        "ballots": find(
            r"Z urn wyjęto (" + NUM + ")",
            r"kart do głosowania wyjętych z urn wyniosła (" + NUM + ")",
            r"(?:wzięło|wzięły|wzięto) udział[^\d]{0,90}?(" + NUM + ")",
            r"Wydano (" + NUM + ") kart",
            r"wydano karty do głosowania,? wyniosła (" + NUM + ")",
        ),
        "invalid": find(
            r"[Ll]iczba głosów nieważnych wyniosła (" + NUM + ")",
            r"kart ważnych z głosami nieważnymi wyniosła (" + NUM + ")",
        ),
        "valid": find(
            r"[Ll]iczba głosów ważnych wyniosła (" + NUM + ")",
            r"kart ważnych z głosami ważnymi wyniosła (" + NUM + ")",
        ),
    }


TERMINATORS = (
    r"\s(?:\d{1,2}\.\s+W województwie|W województwie|Wyniki wyborów|III\b|Wybrani|Rozdział \d|Pokaż|\d\)\s+Zmiany|"
    r"\d+\.\s+W okręgu|Przewodniczący)"
)


def candidates(body: str) -> tuple[list[Candidate], str]:
    stop = re.search(TERMINATORS, body)
    tail = body[stop.start() :] if stop else ""
    body = body[: stop.start()] if stop else body
    marks: list[tuple[int, int]] = []
    expected = 1
    for match in re.finditer(r"(?:^|\s)(\d{1,2})[.)]\s+(?=\S)", body):
        if int(match[1]) == expected:
            marks.append((match.start(), match.end()))
            expected += 1
    found: list[Candidate] = []
    for index, (_, start) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(body)
        chunk = body[start:end]
        parts = re.split(r"\szgłoszon\w+ przez\s", chunk, maxsplit=1)
        left, committee = (parts + [""])[:2]
        trailing = re.search(r"\s(" + NUM + r")\s*$", left)
        if trailing:
            votes, name = number(trailing[1]), left[: trailing.start()]
        else:
            trailing = re.search(r"\s(" + NUM + r")\s*[;.]?\s*$", committee)
            if trailing is None:
                raise ValueError(f"no votes in {chunk!r}")
            votes, committee = number(trailing[1]), committee[: trailing.start()]
            name = left
        surname, given = split_name(name.strip())
        found.append(
            {
                "surname": surname,
                "given": given,
                "committee": " ".join(committee.strip(" ;.,").split()) or None,
                "votes": votes,
                "elected": False,
                "number": index + 1,
            }
        )
    return found, tail


def mark_elected(found: list[Candidate], tail: str) -> None:
    folded = tail.casefold()
    for entry in found:
        first = entry["given"].split()[0].casefold() if entry["given"] else ""
        if entry["surname"].casefold() in folded and first in folded:
            entry["elected"] = True


def parse_notice(eli: str, day: date, kind: str) -> list[Contest]:
    text = " ".join(notice_text(eli, kind).split())
    text = text[text.find("Na podstawie") :]
    anchors = [
        m.start()
        for m in re.finditer(r"[Ll]iczba osób uprawnionych do głosowania|Uprawnionych do głosowania było", text)
    ]
    out: list[Contest] = []
    for index, start in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else len(text)
        segment = text[start:end]
        heading = list(re.finditer(r"okręgu wyborczym nr (\d+)", text[max(0, start - 300) : start]))
        match = heading[-1] if heading else re.search(r"okręgu wyborczym nr (\d+)", segment)
        if match is None:
            raise ValueError(f"{eli}: no constituency number")
        district = match[1]
        begin = re.search(
            r"(?:[Kk]andydaci otrzymali|Kandydaci uzyskali|Kandydat uzyskał|kandydaci otrzymali)[^:]*:", segment
        )
        if begin is None:
            raise ValueError(f"{eli} {district}: no candidate list")
        found, tail = candidates(segment[begin.end() :])
        mark_elected(found, tail)
        seats = re.search(r"wybierano (\d+) senator", text[max(0, start - 1400) : end])
        figures = accounting(segment)
        out.append(
            {
                "date": day,
                "round": 1,
                "district": district,
                "label": f"Senate constituency {district}",
                "seats": int(seats[1]) if seats else 1,
                "registered": figures["registered"],
                "ballots": figures["ballots"],
                "invalid": figures["invalid"],
                "valid": figures["valid"],
                "cands": found,
                "locator": locator(eli),
                "advanced": (),
            }
        )
    return out


def locator(eli: str) -> str:
    series, year, position = eli.split("/")
    return f"{'Dz. U.' if series == 'DU' else 'M.P.'} {year} poz. {position}"


def old(
    eli: str,
    day: date,
    round_number: int,
    district: str,
    registered: int,
    valid: int,
    people: list[tuple[str, str, str | None, int]],
    winners: tuple[str, ...],
    voters: int | None = None,
    invalid: int | None = None,
    advance: tuple[str, ...] = (),
) -> Contest:
    found: list[Candidate] = [
        {
            "surname": surname,
            "given": given,
            "committee": committee,
            "votes": votes,
            "elected": surname in winners,
            "number": position,
        }
        for position, (surname, given, committee, votes) in enumerate(people, 1)
    ]
    return {
        "date": day,
        "round": round_number,
        "district": district,
        "label": f"{VOIVODESHIPS[district][0]} Senate constituency",
        "seats": 1,
        "registered": registered,
        "ballots": voters,
        "invalid": invalid,
        "valid": valid,
        "cands": found,
        "locator": locator(eli),
        "advanced": advance,
    }


SLD = "Zarząd Krajowy Sojuszu Lewicy Demokratycznej"
PSL = "Naczelny Komitet Wykonawczy Polskiego Stronnictwa Ludowego"
KKW_SLD = "Krajowy Komitet Wyborczy Sojuszu Lewicy Demokratycznej"
UPR = "Rada Główna Konserwatywno-Liberalnej Partii Unia Polityki Realnej"
HAND: list[Contest] = [
    old(
        "MP/1989/269",
        date(1989, 10, 8),
        1,
        "piotrkowskie",
        464767,
        66201,
        [
            ("Andrzejewski", "Piotr Łukasz Juliusz", None, 43080),
            ("Falak", "Leopold Kazimierz", None, 10711),
            ("Jankowski", "Zygmunt", None, 7572),
            ("Kaliszczak", "Mirosław", None, 1819),
            ("Kersz", "Stanisław", None, 2023),
        ],
        ("Andrzejewski",),
    ),
    old(
        "MP/1990/38",
        date(1990, 2, 4),
        1,
        "opolskie",
        705462,
        214796,
        [
            ("Bobrowski", "Jerzy Wawrzyniec", None, 17265),
            ("Król", "Henryk", None, 84601),
            ("Pietrzykowski", "Józef", None, 33951),
            ("Simonides", "Dorota Elżbieta", None, 76592),
        ],
        (),
        advance=("Król", "Simonides"),
    ),
    old(
        "MP/1990/56",
        date(1990, 2, 18),
        2,
        "opolskie",
        705702,
        384846,
        [("Król", "Henryk", None, 124498), ("Simonides", "Dorota Elżbieta", None, 258135)],
        ("Simonides",),
    ),
    old(
        "MP/1990/172",
        date(1990, 5, 27),
        1,
        "lubelskie",
        728740,
        226729,
        [
            ("Fuszara", "Marian", None, 19487),
            ("Jurek", "Janusz", None, 28447),
            ("Kłoczowski", "Jerzy", None, 91848),
            ("Wierzbicki", "Roman", None, 79540),
        ],
        (),
        advance=("Kłoczowski", "Wierzbicki"),
    ),
    old(
        "MP/1990/180",
        date(1990, 6, 10),
        2,
        "lubelskie",
        728823,
        92916,
        [("Kłoczowski", "Jerzy", None, 47320), ("Wierzbicki", "Roman", None, 44705)],
        ("Kłoczowski",),
    ),
    old(
        "MP/1994/303",
        date(1994, 6, 19),
        1,
        "chełmskie",
        178806,
        58967,
        [
            ("Chodyńska", "Maria Danuta", 'Komitet Wyborczy Niepodległościowego Porozumienia "Chełmszczyzna"', 8499),
            ("Korczyński", "Janusz", "Komitet Wyborczy Janusza Korczyńskiego", 9946),
            ("Masłowski", "Jerzy Marian", "Komitet Wyborczy Chełmskiego Porozumienia Centro-Prawicy", 10894),
            ("Miszczuk", "Piotr", KKW_SLD, 16384),
            ("Rysak", "Elżbieta", PSL, 13244),
        ],
        ("Miszczuk",),
        voters=62882,
        invalid=3915,
    ),
    old(
        "MP/1994/303",
        date(1994, 6, 19),
        1,
        "elbląskie",
        339012,
        100058,
        [
            ("Bielecki", "Wacław", 'Komisja Krajowa Niezależnego Samorządnego Związku Zawodowego "Solidarność"', 11247),
            ("Kalinowski", "Sylwester Aleksander", "Komitet Wyborczy Sylwestra Kalinowskiego", 8950),
            ("Kochanowski", "Stanisław", PSL, 24337),
            ("Krasowski", "Edmund Marek", "Komitet Wyborczy Edmunda Krasowskiego", 22487),
            ("Krukowska", "Janina", "Komitet Wyborczy na rzecz Janiny Krukowskiej", 8916),
            ("Niedźwiecki", "Krzysztof", "Rada Krajowa Unii Pracy", 8434),
            ("Nowosielski", "Andrzej Franciszek", 'Komitet Wyborczy "Ziemia Elbląska"', 9476),
            ("Wójcik", "Krzysztof", "Rada Polityczna Konfederacji Polski Niepodległej", 6211),
        ],
        ("Kochanowski",),
        voters=107335,
        invalid=7277,
    ),
    old(
        "MP/1995/567",
        date(1995, 10, 1),
        1,
        "szczecińskie",
        714418,
        69351,
        [
            ("Balazs", "Artur Krzysztof", "Komitet Wyborczy Konwencja Szczecińska", 34159),
            ("Baumgart-Turkowiak", "Piotr", 'Prawicowy Komitet Wyborczy Ziemi Szczecińskiej "Sumienie"', 2986),
            ("Liberadzki", "Bogusław Marian", KKW_SLD, 23071),
            ("Pruchno-Wróblewski", "Lech Roman", "Prawicowy Komitet Wyborczy w Województwie Szczecińskim", 9135),
        ],
        ("Balazs",),
        voters=70908,
        invalid=1557,
    ),
    old(
        "MP/1995/567",
        date(1995, 10, 1),
        1,
        "wrocławskie",
        832492,
        53202,
        [
            ("Bubel", "Leszek", "Zarząd Forum Walki z Bezprawiem", 1141),
            ("Czapiewski", "Edward", "Komitet Wyborczy na Rzecz Kandydatury dr. Edwarda Czapiewskiego", 4124),
            ("Działocha", "Kazimierz", KKW_SLD, 13869),
            ("Nowicka", "Krystyna", "Komitet Wykonawczy Unii Pracy", 2802),
            ("Połomski", "Franciszek", "Prezydium Rady Krajowej Unii Wolności", 11261),
            ("Ślązak", "Zbigniew", UPR, 5448),
            (
                "Winiarski",
                "Bolesław Marian",
                'Komitet Wyborczy Koalicji Ugrupowań Patriotyczno-Niepodległościowych "Nasza Polska"',
                13545,
            ),
            ("Zagórski", "Janusz", 'Komitet Wyborczy Forum Nowej Cywilizacji "Piramida"', 1012),
        ],
        ("Działocha",),
        voters=53922,
        invalid=720,
    ),
    old(
        "MP/2000/396",
        date(2000, 6, 25),
        1,
        "katowickie",
        2955901,
        91465,
        [
            ("Graczyński", "Adam", SLD, 39310),
            ("Marszewski", "Wacław Marian", "Komitet Wyborczy Akcja Wyborcza Solidarność", 36488),
            ("Postułka", "Elżbieta Teresa", "Komitet Wyborczy Koalicja KPEiR RP - KPN Ojczyzna", 4542),
            ("Rocznoik", "Andrzej Romuald", UPR, 3558),
            ("Soska", "Jacek", PSL, 7567),
        ],
        ("Graczyński",),
        voters=93822,
        invalid=2357,
    ),
    old(
        "MP/2000/396",
        date(2000, 6, 25),
        1,
        "szczecińskie",
        747765,
        48272,
        [
            ("Iwaszkiewicz", "Zenon", "Obywatelski Komitet Wyborczy Zenona Iwaszkiewicza", 1195),
            ("Kopeć", "Maciej", "Komitet Wyborczy Ojczyzna", 2057),
            ("Oleszczuk", "Stefan", UPR, 2099),
            ("Tałasiewicz", "Marek Jan", 'Komitet Wyborczy "Szczecińska Konwencja Wyborcza"', 16887),
            ("Zychowicz", "Zbigniew Stanisław", SLD, 26034),
        ],
        ("Zychowicz",),
        voters=49780,
        invalid=1508,
    ),
    old(
        "MP/2000/396",
        date(2000, 6, 25),
        1,
        "wrocławskie",
        862852,
        48802,
        [
            ("Antoniszyn", "Emil", "Rada Naczelna Krajowej Partii Emerytów i Rencistów", 1276),
            ("Czekierda", "Henryk", PSL, 2702),
            ("Helski", "Stanisław", "Komitet Wyborczy Stanisława Helskiego", 691),
            ("Juzwenko", "Adolf", "Komitet Wyborczy Adolfa Juzwenki", 15060),
            ("Kosik", "Czesław", "Rada Krajowa Samoobrony Rzeczypospolitej Polskiej", 1049),
            ("Noga", "Marian", SLD, 18122),
            ("Stryjewski", "Antoni", 'Komitet Wyborczy "Przyjaciele Rodziny Polskiej"', 9902),
        ],
        ("Noga",),
        voters=49193,
        invalid=391,
    ),
    old(
        "MP/2001/82",
        date(2001, 1, 28),
        1,
        "chełmskie",
        188354,
        20246,
        [
            ("Kociuba", "Wiesław", "Komitet Wyborczy Wiesława Kociuby", 3372),
            ("Lewczuk", "Henryk", "Zarząd Główny Ruchu Odbudowy Polski", 1880),
            ("Mardoń", "Ryszarda Anna", SLD, 6765),
            ("Rychliczek", "Adam", PSL, 8229),
        ],
        ("Rychliczek",),
        voters=20532,
        invalid=286,
    ),
    old(
        "MP/2001/82",
        date(2001, 1, 28),
        1,
        "wrocławskie",
        870086,
        38809,
        [
            ("Gomułkiewicz", "Leopold Jan", "Komitet Wyborców Koalicji dla Polski", 3057),
            ("Litwiniec", "Bogusław", SLD, 18015),
            ("Muszyński", "Marek Paweł", "Komitet Wyborczy Akcja Wyborcza Solidarność", 17737),
        ],
        ("Litwiniec",),
        voters=39200,
        invalid=391,
    ),
]


@cache
def contests() -> list[Contest]:
    found: list[Contest] = list(HAND)
    for eli, day, kind in NOTICES:
        if eli.startswith("MP/") or day in DATA_FILES:
            continue
        found.extend(parse_notice(eli, day, kind))
    for day in DATA_FILES:
        eli = next(item[0] for item in NOTICES if item[1] == day)
        found.extend(parse_notice(eli, day, next(item[2] for item in NOTICES if item[1] == day)))
    return found


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NOTICE_SOURCE = key("source", NOTICE_SOURCE_KEY)
PKW_SOURCE = key("source", PKW_SOURCE_KEY)
REGISTRY = f"{SOURCE}/registry"
FIXES = {"Polską Lewicę": "Polska Lewica"}
NOTICE_METHOD = (
    "Constituency totals as the State Electoral Commission's results notice prints them. Candidate votes are the "
    "notice's figures; registered voters, ballots (voters who were issued ballots, or ballots taken from the urn where "
    "the notice gives that figure), valid votes, and invalid votes are as printed. The notices give no figures below "
    "the constituency, so there is no polling-district detail."
)
OLD_METHOD = (
    " The constituency is a voivodeship that was abolished on 31 December 1998, so it has no area. Valid votes are "
    "the figure the notice prints and include ballots on which every candidate was crossed out, so candidate votes "
    "can add up to less."
)
DISTRICT_METHOD = (
    "Candidate votes and accounting for the constituency as the State Electoral Commission's results notice prints "
    "them. The polling-district set is summed from the commission's polling-district protocols."
)
OBWOD_METHOD = (
    "Candidate votes and accounting by polling district (obwód głosowania) from the State Electoral Commission's "
    "protocols file. Polling districts are described by streets and addresses, not mapped, so they have no area."
)
BLOCK_NOTE = (
    " Two senators were elected, and each voter could mark two candidates, so candidate votes add up to more than "
    "the valid votes."
)
REPEAT_NOTE = (
    "This vote repeated the constituency's election of 25 September 2005 after electoral activities were repeated "
    "(ponowienie niektórych czynności wyborczych)."
)


@cache
def database() -> dict[str, object]:
    with connect() as conn:
        one = conn.execute("select id from polity where iso_code = %s", (POLAND_ISO,)).fetchone()
        ids = {
            (scheme, value): entity
            for scheme, value, entity in conn.execute(
                "select scheme, value, entity_id from external_id where scheme in ('pl.body', 'pl.electoral_system')"
            ).fetchall()
        }
        districts = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id"
                " where a.scheme_code = 'pl.okreg_senat'"
            ).fetchall()
        )
    assert one is not None
    return {
        "polity": str(one[0]),
        "body": str(ids[("pl.body", "senat")]),
        "systems": {
            name: str(ids[("pl.electoral_system", name)]) for name in ("senat", "senat-block-2001", "senat-block-1991")
        },
        "districts": {code: str(area) for code, area in districts.items()},
    }


def polity() -> str:
    return str(database()["polity"])


def system_for(day: date, seats: int) -> str | None:
    systems = database()["systems"]
    assert isinstance(systems, dict)
    if seats == 1:
        return str(systems["senat"]) if day >= date(1991, 1, 1) else None
    return str(systems["senat-block-2001" if day >= BLOCK_2001 else "senat-block-1991"])


def area_for(day: date, district: str) -> str | None:
    if not district.isdigit() or day < BLOCK_2001:
        return None
    districts = database()["districts"]
    assert isinstance(districts, dict)
    return districts.get(f"{'2001' if day < MODERN else '2011'}:{district}")


def person_name(entry: Candidate) -> str:
    surname = display(entry["surname"], "pl") if entry["surname"] == entry["surname"].upper() else entry["surname"]
    return f"{entry['given']} {surname}".strip()


def ballot_name(entry: Candidate) -> str:
    return f"{entry['surname']} {entry['given']}".strip()


def committee_label(text: str | None) -> str | None:
    if text is None:
        return None
    text = FIXES.get(text, text).replace("„", '"').replace("”", '"')
    return " ".join(text.split())


def party_key(label: str) -> str:
    return key("party", label.casefold())


def roster(day: date) -> dict[int, dict[str, str]]:
    path = raw.cached(SOURCE, data_urls(day)[1]).path
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".csv"))
        rows = list(csv.DictReader(io.StringIO(archive.read(member).decode("utf-8-sig")), delimiter=";"))
    return {int(row["Pozycja na liście"]): row for row in rows}


def protocols(day: date) -> list[dict[str, str]]:
    path = raw.cached(SOURCE, data_urls(day)[0]).path
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".csv"))
        return list(csv.DictReader(io.StringIO(archive.read(member).decode("utf-8-sig")), delimiter=";"))


def members(day: date) -> set[str]:
    found = set()
    for row in roster(day).values():
        text = row["Przynależność do partii"]
        if text.startswith("członek partii politycznej:"):
            found.add(" ".join(text.split(":", 1)[1].split()))
    return found


def labels() -> dict[str, str]:
    found: dict[str, str] = {}
    for entry in contests():
        for person in entry["cands"]:
            label = committee_label(person["committee"])
            if label is not None:
                found.setdefault(label.casefold(), label)
    for day in DATA_FILES:
        for label in members(day):
            found.setdefault(label.casefold(), label)
    return found


def party_kind(label: str) -> str:
    return (
        "independent_group" if "wyborców" in label.casefold() else "bloc" if label.startswith("Koalicyjny") else "party"
    )


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Polish Senate by-election committees and sources, from the State Electoral Commission's notices",
    )
    p.external.add(polity())
    p.add(
        "source",
        id=NOTICE_SOURCE,
        kind="official_body",
        publisher="Państwowa Komisja Wyborcza",
        title="Obwieszczenia PKW o wynikach wyborów uzupełniających do Senatu (Monitor Polski i Dziennik Ustaw)",
        url="https://eli.gov.pl/",
        license="Official public record, no license stated",
        license_url="https://eli.gov.pl/",
        attribution="Państwowa Komisja Wyborcza, Monitor Polski and Dziennik Ustaw (ISAP)",
    )
    p.add(
        "source",
        id=PKW_SOURCE,
        kind="official_body",
        publisher="Państwowa Komisja Wyborcza",
        title="Wybory uzupełniające do Senatu 2024 and 2025: dane w arkuszach",
        url="https://wybory.gov.pl/index/?language=pl&tab=uzupelniajace",
        license="Official public record, no license stated",
        license_url="https://wybory.gov.pl/index/?language=pl&tab=uzupelniajace",
        attribution="Źródło danych: Państwowa Komisja Wyborcza",
    )
    for eli, day, kind in NOTICES:
        p.artifact(raw.cached(SOURCE, notice_url(eli, kind)), PKW_SOURCE if day in DATA_FILES else NOTICE_SOURCE)
    for day in DATA_FILES:
        for address in data_urls(day):
            p.artifact(raw.cached(SOURCE, address), PKW_SOURCE)
    for label in sorted(labels().values()):
        id = party_key(label)
        p.add("party", id=id, kind=party_kind(label), polity_id=polity())
        p.name(id, display(label, "pl") if label == label.upper() else label, "pl", "official")
    return p


def first_day(entry: Contest) -> date:
    if entry["round"] == 1:
        return entry["date"]
    return max(
        other["date"]
        for other in contests()
        if other["round"] == 1 and other["district"] == entry["district"] and other["date"] < entry["date"]
    )


def packages() -> list[str]:
    firsts = sorted({entry["date"] for entry in contests() if entry["round"] == 1})
    return [REGISTRY, *(f"{SOURCE}/{day}" for day in firsts)]


def gender(text: str) -> str | None:
    return {"Mężczyzna": "male", "Kobieta": "female"}.get(text)


def whole(text: str | None) -> int:
    return int(text) if text else 0


def senate_by(first: date) -> Package:
    mine = [entry for entry in contests() if first_day(entry) == first]
    notice = next(item for item in NOTICES if item[1] == first)
    p = Package(
        f"{SOURCE}/{first}",
        SOURCE,
        f"The Polish Senate by-election of {first:%-d %B %Y}"
        + (", with its second round" if any(entry["round"] == 2 for entry in mine) else "")
        + ", from the State Electoral Commission's notice"
        + (" and polling-district protocols" if first in DATA_FILES else ""),
        depends=[REGISTRY],
    )
    body = str(database()["body"])
    p.external |= {polity(), body, "pl.okreg_senat"}
    if first in DATA_FILES:
        p.external.add("pl.obwod")
    modern = first in DATA_FILES
    source = PKW_SOURCE if modern else NOTICE_SOURCE
    people = roster(first) if modern else {}
    protocol = protocols(first) if modern else []
    p.uses(raw.cached(SOURCE, notice_url(notice[0], notice[2])))
    if modern:
        for address in data_urls(first):
            p.uses(raw.cached(SOURCE, address))
    elections: dict[tuple[date, int], str] = {}
    previous: dict[str, str] = {}
    for entry in sorted(mine, key=lambda item: (item["round"], item["date"], item["district"])):
        day, round_number, district, seats = entry["date"], entry["round"], entry["district"], entry["seats"]
        word = "senate-by-election" + ("" if round_number == 1 else "-runoff")
        path = f"pl/{day}-{word}"
        if (day, round_number) not in elections:
            election = elections[(day, round_number)] = key("election", day, round_number)
            p.external.add(polity())
            p.add(
                "election",
                id=election,
                polity_id=polity(),
                kind="by_election" if round_number == 1 else "runoff",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.add("slug", path=path, entity_id=election, is_current=True)
            suffix = "" if round_number == 1 else ", second round"
            p.name(election, f"Polish Senate by-election of {day:%-d %B %Y}{suffix}", "en", "official")
            p.name(
                election,
                f"Wybory uzupełniające do Senatu {day}{', II tura' if round_number == 2 else ''}",
                "pl",
                "native",
                False,
            )
        election = elections[(day, round_number)]
        contest = key("contest", day, round_number, district)
        area = area_for(day, district)
        if area is not None:
            p.external.add(area)
        system = system_for(day, seats)
        if system is not None:
            p.external.add(system)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=seats,
            round_number=round_number,
            previous_round_id=previous.get(district) if round_number == 2 else None,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        previous[district] = contest
        p.name(contest, entry["label"], "en", "official")
        if district.isdigit():
            p.name(contest, f"Okręg wyborczy nr {district} do Senatu", "pl", "native", False)
        else:
            p.name(
                contest,
                f"Okręg wyborczy w województwie {VOIVODESHIPS[district][1].removesuffix('iego') + 'ie'} do Senatu",
                "pl",
                "native",
                False,
            )
        p.add(
            "slug",
            path=f"{path}/{district if district.isdigit() else slug(district)}",
            entity_id=contest,
            is_current=True,
        )
        method = NOTICE_METHOD + ("" if district.isdigit() else OLD_METHOD) + (BLOCK_NOTE if seats > 1 else "")
        if modern:
            method = DISTRICT_METHOD
        if first == date(2006, 1, 22):
            method += " " + REPEAT_NOTE
        result_set = key("result_set", contest, "district")
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        p.add(
            "citation", id=key("citation", result_set), entity_id=result_set, source_id=source, locator=entry["locator"]
        )
        unit = key("unit", day, round_number, district)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=polity(),
            scheme_code="pl.okreg_senat",
            code=f"{day}:{round_number}:{district}",
            area_relation="equals" if area is not None else "unlocated",
            area_id=area,
            kind="standard",
        )
        options: dict[int, str] = {}
        for person in entry["cands"]:
            tag = f"{day}:{round_number}:{district}:{person['number']}"
            option = options[person["number"]] = key("option", tag)
            label = committee_label(person["committee"])
            nominator = party_key(label) if label is not None else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=nominator,
                ballot_position=person["number"],
                ballot_label=person_name(person),
            )
            human = key("person", tag)
            row = people.get(person["number"])
            p.add("person", id=human, gender=gender(row["Płeć"]) if row else None)
            p.name(human, person_name(person), "pl", "official")
            member = None
            independent = False
            if row is not None:
                text = row["Przynależność do partii"]
                if text.startswith("członek partii politycznej:"):
                    member = party_key(" ".join(text.split(":", 1)[1].split()))
                else:
                    independent = True
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=human,
                option_id=option,
                role="head",
                ballot_name=ballot_name(person),
                member_of_party_id=member,
                is_independent=independent,
            )
            if person["elected"]:
                kind = "elected"
            elif person["surname"] in entry["advanced"]:
                kind = "advanced"
            else:
                kind = "eliminated" if entry["advanced"] else "lost"
            p.add(
                "outcome", result_set_id=result_set, option_id=option, kind=kind, seats=1 if person["elected"] else None
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=person["votes"])
        totals = {
            REGISTERED: entry["registered"],
            BALLOTS: entry["ballots"],
            INVALID: entry["invalid"],
            VALID_BALLOTS: entry["valid"],
        }
        if modern:
            head = list(protocol[0])
            mine_rows = [row for row in protocol if row["Nr okręgu"] == district]
            totals = {
                REGISTERED: sum(whole(row[head[9]]) for row in mine_rows),
                BALLOTS: sum(whole(row[head[22]]) for row in mine_rows),
                INVALID: sum(whole(row[head[26]]) for row in mine_rows),
                VALID_BALLOTS: sum(whole(row[head[30]]) for row in mine_rows),
            }
        for measure, value in totals.items():
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for person in entry["cands"]:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[person["number"]],
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=person["votes"],
            )
        if modern:
            polling = key("result_set", contest, "obwod")
            p.add(
                "result_set",
                id=polling,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=False,
                completeness=1.0,
            )
            p.add(
                "note", id=key("note", polling), entity_id=polling, kind="methodology", body_md=OBWOD_METHOD, lang="en"
            )
            for row in mine_rows:
                code = f"{day}:{whole(row[head[3]]):06d}:{row[head[0]]}"
                child = key("unit", "obwod", code)
                p.add(
                    "reporting_unit",
                    id=child,
                    polity_id=polity(),
                    scheme_code="pl.obwod",
                    code=code,
                    parent_id=unit,
                    area_relation="unlocated",
                    kind="standard",
                )
                cells = {
                    (REGISTERED, None): row[head[9]],
                    (BALLOTS, None): row[head[22]],
                    (INVALID, None): row[head[26]],
                    (VALID_BALLOTS, None): row[head[30]],
                    **{(VOTES, number): row[head[30 + number]] for number in options},
                }
                for (measure, number), text in cells.items():
                    p.add(
                        "result_value",
                        result_set_id=polling,
                        unit_id=child,
                        option_id=None if number is None else options[number],
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=whole(text),
                    )
    short = [
        entry
        for entry in mine
        if entry["ballots"] is not None
        and entry["invalid"] is not None
        and entry["valid"] is not None
        and entry["valid"] + entry["invalid"] != entry["ballots"]
    ]
    if short or modern:
        p.accept(
            "accounting.ballots_sum",
            "The notice's ballots figure is the cards taken from the urn. It also counts cards without the "
            "official stamp, which the notice does not list among the valid or invalid votes, so the two do not add "
            "up to it.",
        )
    if first < date(1991, 1, 1):
        p.accept(
            "accounting.votes_valid",
            "Candidate votes add up to less than the notice's valid ballots because ballots on which every name "
            "was crossed out count as valid.",
        )
    return p


def parse() -> None:
    fetch()
    for key_name in packages():
        build(key_name).write()


def build(name: str) -> Package:
    if name == REGISTRY:
        return registry()
    return senate_by(date.fromisoformat(name.rsplit("/", 1)[1]))


def emit() -> None:
    package.run("load", packages())
