"""North Macedonia: the Assembly elections of 2014, 2016, 2020, and 2024 from the State Election Commission.

The Commission publishes its decisions and result tables as PDF and spreadsheet files. Four elections can be built
from the files within reach:

- 2014: the results by polling station (a table of registered voters, ballots, and each list's votes), with the
  three districts abroad.
- 2016: the Commission's decision of 28 December 2016 (a scan) with each list's votes and seats by electoral unit,
  and the Commission's results service for the registered voters of each unit.
- 2020: the decision of 25 July 2020 (a scan) with each list's votes and seats by electoral unit, and the results
  by polling station from the minutes of the polling boards (Form 16).
- 2024: the decision of 18 May 2024, which gives the votes and seats of the lists that won seats in each unit.

Each Assembly is elected in six electoral units of 20 seats by closed lists and D'Hondt, and in 2011 to 2016 also in
districts abroad. The decisions of 2016 and 2020 are scans; their cells were located and read by optical character
recognition, checked against the printed national totals and seat counts, and stored in ``sec_scans.json`` with the
box of each cell on its page.
"""

import csv
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "mk.sec"
MACEDONIA = str(uuid5(package.NAMESPACE, "polity\x1fMK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMKD"))
HOME = "https://www.sec.mk/"
ARCHIVE = "https://izbornaarhiva.mk/wp-content/uploads"
FILES = {
    "csv14": f"{ARCHIVE}/2021/10/Резултати-од-Парламентарни-избори-2014-по-избирачки-места.csv",
    "json14": "https://web.archive.org/web/20181230030110id_/http://rezultati2014.sec.mk:80/Parliamentary/"
    "JsonResultsData?r=2&rd=r&cs=mk-MK",
    "scan16": f"{ARCHIVE}/2021/10/Заклучок-за-објавување-на-конечни-резултати.pdf",
    "json16": "https://web.archive.org/web/2021id_/https://rezultati2016.sec.mk/Parliamentary/"
    "JsonResultsData?r=r&rd=r1&eu=All&m=All&cs=sq-AL",
    "scan20": f"{ARCHIVE}/2021/10/Заклучок-за-објавување-на-конечни-резултати-и-распределба-на-мандати.pdf",
    "form16": f"{ARCHIVE}/2021/10/Резултати-врз-основа-на-податоци-од-записниците-од-ИО.pdf",
    "pdf24": f"{ARCHIVE}/2025/09/zakluchok-za-konechni-rezultati-i-utvrduvaњe-na-mandati-po-izborni-ediniczi-"
    "parlamentarni-izbori-2024mk-al.pdf",
}
SHAPES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/MKD/ADM2/"
    "geoBoundaries-MKD-ADM2.geojson"
)
SUFFIX = {"csv14": ".csv", "json14": ".json", "json16": ".json"}
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
S_DISTRICT, S_MUNI, S_STATION = "mk.izborna_edinica", "mk.opstina", "mk.izbiracko_mesto"
DAYS = {
    2014: date(2014, 4, 27),
    2016: date(2016, 12, 11),
    2020: date(2020, 7, 15),
    2024: date(2024, 5, 8),
}
PLAN = date(2013, 1, 31)  # the 80-municipality plan
RULES = {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SEC = key("source", "sec")
SHAPE_SOURCE = key("source", "geoboundaries")
ASSEMBLY = key("body", "sobranie")
SYSTEM = key("electoral_system", "sobranie")


def fetch() -> None:
    for name, url in FILES.items():
        raw.fetch(SOURCE, url, suffix=SUFFIX.get(name, ".pdf"))
    raw.fetch(SOURCE, SHAPES, suffix=".geojson")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS.values())]


def path(name: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, FILES[name]).storage_key


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


# Municipalities: the 80 of the 2013 plan, by the Commission's spelling, with the latin names of the outline file.
# The four municipalities that were merged into Kičevo in 2013 are in the file but not on the plan.
MUNICIPALITIES = {
    "Аеродром": "Aerodrom",
    "Арачиново": "Arachinovo",
    "Берово": "Berovo",
    "Битола": "Bitola",
    "Богданци": "Bogdantsi",
    "Боговиње": "Bogovinje",
    "Босилово": "Bosilovo",
    "Брвеница": "Brvenitsa",
    "Бутел": "Butel",
    "Валандово": "Valandovo",
    "Василево": "Vasilevo",
    "Вевчани": "Vevchani",
    "Велес": "Veles",
    "Виница": "Vinitsa",
    "Врапчиште": "Vrapchishte",
    "Гази Баба": "Gazi Baba",
    "Гевгелија": "Gevgelija",
    "Гостивар": "Gostivar",
    "Градско": "Gradsko",
    "Дебар": "Debar",
    "Дебарца": "Debartsa",
    "Делчево": "Delchevo",
    "Демир Капија": "Demir Kapija",
    "Демир Хисар": "Demir Hisar",
    "Дојран": "Dojran",
    "Долнени": "Dolneni",
    "Желино": "Zhelino",
    "Зелениково": "Zelenikovo",
    "Зрновци": "Zrnovtsi",
    "Илинден": "Ilinden",
    "Јегуновце": "Jegunovtse",
    "Кавадарци": "Kavadartsi",
    "Карбинци": "Karbintsi",
    "Карпош": "Karposh",
    "Кисела Вода": "Kisela Voda",
    "Кичево": "Kichevo",
    "Конче": "Konche",
    "Кочани": "Kochani",
    "Кратово": "Kratovo",
    "Крива Паланка": "Kriva Palanka",
    "Кривогаштани": "Krivogashtani",
    "Крушево": "Krushevo",
    "Куманово": "Kumanovo",
    "Липково": "Lipkovo",
    "Лозово": "Lozovo",
    "Маврово и Ростуша": "Mavrovo and Rostusha",
    "Македонска Каменица": "Makedonska Kamenitsa",
    "Македонски Брод": "Makedonski Brod",
    "Могила": "Mogila",
    "Неготино": "Negotino",
    "Новаци": "Novatsi",
    "Ново Село": "Novo Selo",
    "Охрид": "Ohrid",
    "Петровец": "Petrovets",
    "Пехчево": "Pehchevo",
    "Пласница": "Plasnitsa",
    "Прилеп": "Prilep",
    "Пробиштип": "Probishtip",
    "Радовиш": "Radovish",
    "Ранковце": "Rankovtse",
    "Ресен": "Resen",
    "Росоман": "Rosoman",
    "Сарај": "Saraj",
    "Свети Николе": "Sveti Nikole",
    "Сопиште": "Sopishte",
    "Старо Нагоричане": "Staro Nagorichane",
    "Струга": "Struga",
    "Струмица": "Strumitsa",
    "Студеничани": "Studenichani",
    "Теарце": "Teartse",
    "Тетово": "Tetovo",
    "Центар": "Centar",
    "Центар Жупа": "Centar Zhupa",
    "Чаир": "Chair",
    "Чашка": "Chashka",
    "Чешиново - Облешево": "Cheshinovo - Obleshevo",
    "Чучер - Сандево": "Chucher - Sandevo",
    "Шуто Оризари": "Shuto Orizari",
    "Штип": "Shtip",
    "Ѓорче Петров": "Gjorche Petrov",
}
MERGED_INTO_KICHEVO = {"Drugovo", "Oslomej", "Vraneshtitsa", "Zajas"}
ALIASES = {"дебрца": "Дебарца", "маврово и ростуше": "Маврово и Ростуша"}
BY_FOLD = {name.casefold(): name for name in MUNICIPALITIES} | ALIASES
# The electoral units of 2011-2020 as unions of whole municipalities, each municipality in the unit that holds most of
# its polling stations (the 2014 and 2020 station files agree). Chair, Kumanovo, Staro Nagorichane, Bitola, Krivogashtani,
# and Kichevo are split between two units in the station files, so their outlines are approximate edges of the units.
UNIT_MUNICIPALITIES = {
    1: "Aerodrom,Karposh,Kisela Voda,Makedonski Brod,Saraj,Sopishte,Studenichani,Centar,Chair",
    2: "Arachinovo,Butel,Gazi Baba,Gjorche Petrov,Kumanovo,Lipkovo,Staro Nagorichane,Chucher - Sandevo,Shuto Orizari",
    3: "Berovo,Veles,Vinitsa,Gradsko,Delchevo,Zelenikovo,Zrnovtsi,Ilinden,Karbintsi,Kochani,Kratovo,Kriva Palanka,"
    "Lozovo,Makedonska Kamenitsa,Petrovets,Pehchevo,Probishtip,Rankovtse,Sveti Nikole,Chashka,Cheshinovo - Obleshevo,"
    "Shtip",
    4: "Bogdantsi,Bosilovo,Valandovo,Vasilevo,Gevgelija,Demir Kapija,Dojran,Kavadartsi,Konche,Krivogashtani,Mogila,"
    "Negotino,Novatsi,Novo Selo,Prilep,Radovish,Rosoman,Strumitsa",
    5: "Bitola,Vevchani,Debar,Debartsa,Demir Hisar,Dolneni,Kichevo,Krushevo,Mavrovo and Rostusha,Ohrid,Plasnitsa,"
    "Resen,Struga,Centar Zhupa",
    6: "Bogovinje,Brvenitsa,Vrapchishte,Gostivar,Zhelino,Jegunovtse,Teartse,Tetovo",
}
ABROAD_2014 = {7: "Electoral unit 7 (abroad)", 8: "Electoral unit 8 (abroad)", 9: "Electoral unit 9 (abroad)"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def municipality(text: str) -> str:
    found = BY_FOLD.get(" ".join(text.split()).casefold())
    if found is None:
        raise ValueError(f"unknown municipality {text!r}")
    return found


def district_area(code: str) -> str:
    return key("area", S_DISTRICT, code)


def muni_area(name: str) -> str:
    return key("area", S_MUNI, name)


@dataclass(frozen=True)
class Ballot:
    """One list on the ballot in one election."""

    id: str  # the key of the list within its election
    party: str  # the key of the party or list vehicle
    kind: str  # party or list_vehicle
    label: str
    position: int | None = None
    short: str | None = None
    sq: str | None = None


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


# The 2014 lists, from the Commission's results service and the polling-station table's columns.
def lists_2014() -> list[Ballot]:
    stored = json.loads(path("json14").read_text(encoding="utf-8"))["Results"]["ResultsMain"]
    heads = next(csv.reader(path("csv14").read_text(encoding="utf-8").splitlines()))[5:]
    if len(stored) != len(heads):
        raise ValueError("the results service and the table disagree on the number of lists")
    found = []
    shared = {"ДПА": "dpa", "ДУИ": "dui"}
    for head, row in zip(heads, stored, strict=True):
        label = clean(row["PartyFullName"])
        coalition = "," in label or label.upper().startswith("КОАЛИЦИЈА")
        party = shared.get(head, f"2014:{row['ListPosition']}")
        found.append(
            Ballot(
                id=str(row["ListPosition"]),
                party=party,
                kind="list_vehicle" if coalition else "party",
                label=label,
                position=int(row["ListPosition"]),
                short=clean(head),
            )
        )
    return found


def lists_2016() -> list[Ballot]:
    stored = json.loads(path("json16").read_text(encoding="utf-8"))["Results"]["ResultsMain"]
    short = {int(row["PartyID"]): clean(row["Parties"][0]["PartyShortName"]) for row in stored}
    sq = {int(row["PartyID"]): clean(row["Parties"][0]["PartyFullName"]) for row in stored}
    table = [
        (1, "Коалиција ВМРО–ДПМНЕ", "list_vehicle", "2016:1"),
        (2, "Демократска партија на Албанците – ДПА", "party", "dpa"),
        (3, "Демократска унија за интеграција – ДУИ", "party", "dui"),
        (4, "Коалиција Социјалдемократски сојуз на Македонија – СДСМ", "list_vehicle", "2016:4"),
        (5, "Коалиција за промени и правда – Трет блок", "list_vehicle", "2016:5"),
        (6, "Партија за демократски просперитет – ПДП", "party", "2016:6"),
        (7, "Коалиција „Алијанса за Албанците“", "list_vehicle", "2016:7"),
        (8, "Либерална партија на Македонија", "party", "2016:8"),
        (9, "Коалиција ВМРО за Македонија", "list_vehicle", "2016:9"),
        (10, "Движење Беса", "party", "2016:10"),
        (11, "Левица", "party", "levica"),
    ]
    return [
        Ballot(id=str(n), party=party, kind=kind, label=label, position=n, short=short[n], sq=sq[n])
        for n, label, kind, party in table
    ]


# 2020: the order is that of the columns of the Form 16 table, the lists' order on the ballot.
LISTS_2020 = [
    ("dpa", "dpa", "party", "Демократска партија на Албанците – ДПА", "ДПА"),
    ("demokrati", "2020:demokrati", "party", "ДЕМОКРАТИ", None),
    ("mozheme", "2020:mozheme", "list_vehicle", "Коалиција „МОЖЕМЕ“", "КОАЛИЦИЈА МОЖЕМЕ"),
    ("levica", "levica", "party", "ЛЕВИЦА", None),
    ("aa", "2020:aa", "list_vehicle", "Коалиција Алијанса за Албанците и Алтернатива", "АА / A"),
    ("glas", "2020:glas", "party", "ГЛАС ЗА МАКЕДОНИЈА", None),
    ("gdu", "2020:gdu", "party", "Граѓанско демократска унија – ГДУ", "ГДУ"),
    ("integra", "2020:integra", "party", "ИНТЕГРА – Македонска конзервативна партија", "ИНТЕГРА"),
    ("dui", "dui", "party", "Демократска унија за интеграција – ДУИ", "ДУИ"),
    ("sdu", "2020:sdu", "party", "СДУ", None),
    ("edinstvena", "2020:edinstvena", "party", "ЕДИНСТВЕНА МАКЕДОНИЈА", None),
    ("moro", "2020:moro", "party", "МОРО – работничка партија", "МОРО"),
    ("vmro", "2020:vmro", "list_vehicle", "ВМРО-ДПМНЕ и Коалиција „ОБНОВА ЗА МАКЕДОНИЈА“", "ВМРО - ДПМНЕ и други"),
    ("tvoja", "2020:tvoja", "party", "ТВОЈА ПАРТИЈА", "ТВОЈА"),
    ("np_romite", "2020:np_romite", "party", "НАРОДНА ПАРТИЈА НА РОМИТЕ", "НП НА РОМИТЕ"),
]
# The labels of the Form 16 table, by list.
FORM16_LABEL = {
    "ДПА": "dpa",
    "ДЕМОКРАТИ": "demokrati",
    "КОАЛИЦИЈА МОЖЕМЕ": "mozheme",
    "ЛЕВИЦА": "levica",
    "АА / A": "aa",
    "ГЛАС ЗА МАКЕДОНИЈА": "glas",
    "ГДУ": "gdu",
    "ИНТЕГРА": "integra",
    "ДУИ": "dui",
    "СДУ": "sdu",
    "ЕДИНСТВЕНА МАКЕДОНИЈА": "edinstvena",
    "МОРО": "moro",
    "ВМРО - ДПМНЕ и други": "vmro",
    "ТВОЈА": "tvoja",
    "НП НА РОМИТЕ": "np_romite",
}


def lists_2020() -> list[Ballot]:
    return [
        Ballot(id=id, party=party, kind=kind, label=label, position=n, short=short)
        for n, (id, party, kind, label, short) in enumerate(LISTS_2020, 1)
    ]


LISTS_2024 = [
    ("vmro", "2024:vmro", "list_vehicle", "Коалиција „ТВОЈА МАКЕДОНИЈА“ ВМРО-ДПМНЕ"),
    ("sdsm", "2024:sdsm", "list_vehicle", "Коалиција за европска иднина"),
    ("dui", "2024:dui", "list_vehicle", "Коалиција „Европски фронт“"),
    ("vlen", "2024:vlen", "list_vehicle", "Коалиција ВЛЕН"),
    ("levica", "levica", "party", "ЛЕВИЦА"),
    ("znam", "2024:znam", "party", "Движење ЗНАМ – За Наша Македонија"),
]


def lists_2024() -> list[Ballot]:
    return [Ballot(id=id, party=party, kind=kind, label=label) for id, party, kind, label in LISTS_2024]


def ballots(year: int) -> list[Ballot]:
    return {2014: lists_2014, 2016: lists_2016, 2020: lists_2020, 2024: lists_2024}[year]()


# Polling stations.


@dataclass(frozen=True)
class Station:
    unit: int
    municipality: str | None  # None for a station abroad
    code: str
    name: str | None
    registered: int | None
    ballots: int | None
    valid: int | None
    invalid: int | None
    votes: dict[str, int]


def stations_2014() -> list[Station]:
    rows = list(csv.reader(path("csv14").read_text(encoding="utf-8").splitlines()))
    ids = [ballot.id for ballot in lists_2014()]
    found: list[Station] = []
    for row in rows[1:]:
        number = [int(cell) for cell in row[1:]]
        match = re.fullmatch(r"(\S+) - (.+) - Изборна единица (\d)", row[0])
        if match is not None:
            code, place, unit = match[1], municipality(match[2]), int(match[3])
            name = None
        else:
            abroad = re.fullmatch(r"(.+) - Изборна единица (\d)", row[0])
            if abroad is None:
                raise ValueError(f"unreadable station {row[0]!r}")
            place, unit, name = None, int(abroad[2]), abroad[1]
            code = slug_city(name)
        found.append(
            Station(
                unit,
                place,
                code,
                name,
                number[0],
                number[1],
                number[2],
                number[3],
                dict(zip(ids, number[4:], strict=True)),
            )
        )
    return found


CITY = {
    "Анкара": "ankara",
    "Белград": "belgrade",
    "Берлин": "berlin",
    "Берн": "bern",
    "Бон": "bonn",
    "Брисел": "brussels",
    "Букурешт": "bucharest",
    "Венеција": "venice",
    "Виена": "vienna",
    "Загреб": "zagreb",
    "Истанбул": "istanbul",
    "Копенхаген": "copenhagen",
    "Лондон": "london",
    "Љубљана": "ljubljana",
    "Минхен": "munich",
    "Париз": "paris",
    "Подгорица": "podgorica",
    "Прага": "prague",
    "Приштина": "pristina",
    "Рим": "rome",
    "Сараево": "sarajevo",
    "Софија": "sofia",
    "Стокхолм": "stockholm",
    "Тирана": "tirana",
    "Хаг": "the-hague",
    "Вашингтон": "washington",
    "Детроит": "detroit",
    "Њујорк": "new-york",
    "Отава": "ottawa",
    "Торонто": "toronto",
    "Чикаго": "chicago",
    "Канбера": "canberra",
    "Мелбурн": "melbourne",
    "Пекинг": "beijing",
    "Осло": "oslo",
    "Москва": "moscow",
}


def slug_city(name: str) -> str:
    return f"abroad-{CITY[name]}"


FORM16_ROW = re.compile(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\S+)\s{2,}(.+?)\s{2,}(\d+)\s*$")


def stations_2020() -> list[Station]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(path("form16")), "-"], check=True, capture_output=True
    ).stdout.decode("utf-8")
    found: dict[tuple[int, str, str], dict[str, int]] = {}
    for line in text.splitlines():
        match = FORM16_ROW.match(line)
        if match is None:
            continue
        unit, place, code, label, votes = match.groups()
        list_id = FORM16_LABEL[label.strip()]
        row = found.setdefault((int(unit), municipality(place), code), {})
        if list_id in row:
            raise ValueError(f"{code} repeats {label}")
        row[list_id] = int(votes)
    return [
        Station(unit, place, code, None, None, None, None, None, votes) for (unit, place, code), votes in found.items()
    ]


def scans() -> dict[str, list[dict[str, object]]]:
    stored = json.loads((Path(__file__).parent / "sec_scans.json").read_text(encoding="utf-8"))
    return {year: stored[year] for year in ("2016", "2020")}


def stats_2016() -> dict[int, dict[str, int]]:
    details = json.loads(path("json16").read_text(encoding="utf-8"))["Results"]["StatisticsDetails"]
    found: dict[int, dict[str, int]] = {}
    for row in details:
        unit = int(row["ElectionUnitName"].split()[-1])
        found[unit] = {
            "registered": int(row["RegisteredVoters"].replace(".", "")),
            "stations": int(row["TotalPS"].replace(".", "")),
        }
    return found


def conclusion_2024() -> dict[int, list[tuple[str, int, int]]]:
    """Each unit's lists that won seats, with votes and seats, from the decision's Macedonian text."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(path("pdf24")), "-"], check=True, capture_output=True
    ).stdout.decode("utf-8")
    names = {
        "ТВОЈА МАКЕДОНИЈА": "vmro",
        "КОАЛИЦИЈА ЗА ЕВРОПСКА ИДНИНА": "sdsm",
        "ЕВРОПСКИ ФРОНТ": "dui",
        "КОАЛИЦИЈА ВЛЕН": "vlen",
        "ЛЕВИЦА": "levica",
        "ЗНАМ": "znam",
    }
    found: dict[int, list[tuple[str, int, int]]] = {}
    unit = 0
    inside = False
    for line in text.splitlines():
        head = re.search(r"Изборна единица (?:број )?(\d) / Nj", line)
        if head:
            unit, inside = int(head[1]), True
            found[unit] = []
            continue
        if "Утврдени мандати" in line or "Mandatet e p" in line:
            inside = False
        if not inside:
            continue
        match = re.search(r"^(.*?\S)\s{2,}(\d{1,3}(?:\.\d{3})*)\s+(\d{1,2})\s*(?:\s{3,}.*)?$", line[:140])
        if match is None:
            continue
        label = " ".join(match[1].split()).upper().replace("“", "").replace("”", "")
        hits = [value for name, value in names.items() if name in label]
        if len(hits) != 1:
            raise ValueError(f"unit {unit}: cannot place {label!r}")
        found[unit].append((hits[0], int(match[2].replace(".", "")), int(match[3])))
    return found


# Geography.


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 80 municipalities of North Macedonia's 2013 plan, with outlines, and the electoral units of the Assembly "
        "elections",
    )
    p.external |= {MACEDONIA, COUNTRY}
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen North Macedonia ADM2 (municipalities, 2016)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from EuroGeographics and NTES data",
        notes="Boundary year 2016, built December 2023. The outlines of Drugovo, Oslomej, Vraneshtitsa, and Zajas are "
        "merged into Kichevo, as the 2013 plan did.",
    )
    shapes = raw.cached(SOURCE, SHAPES)
    p.artifact(shapes, SHAPE_SOURCE)
    for code, rank, kind, noun in (
        (S_DISTRICT, 1, "electoral_district", "electoral unit"),
        (S_MUNI, 2, "administrative", "municipality"),
        (S_STATION, 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=MACEDONIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add(
        "boundary_set",
        id=key("boundary_set", S_MUNI, 2013),
        scheme_code=S_MUNI,
        vintage="2013",
        valid_from=PLAN,
        as_of=shapes.retrieved_at.date(),
    )
    latin: dict[str, shapely.Geometry] = {}
    outlines: dict[str, shapely.Geometry] = {}
    pieces: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]:
        name = feature["properties"]["shapeName"]
        target = "Kichevo" if name in MERGED_INTO_KICHEVO else name
        pieces[target].append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    for name, parts in pieces.items():
        latin[name] = shapely.union_all(parts)
    for name, english in sorted(MUNICIPALITIES.items()):
        id = muni_area(name)
        p.add("area", id=id, scheme_code=S_MUNI)
        p.add("area_code", area_id=id, code=slug(english), valid_from=PLAN)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=PLAN)
        p.name(id, name, "mk", "official")
        p.name(id, english, "en", "english", False)
        outline = latin.get(english)
        if outline is not None:
            outlines[english] = outline
        if outline is None:
            raise ValueError(f"no outline for {english}")
        polygons = [
            part
            for geometry in shapely.get_parts(outline)
            if shapely.get_type_id(geometry) in (3, 6)
            for part in shapely.get_parts(geometry)
        ]
        multi = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=PLAN,
            geom=shapely.to_wkb(multi, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(multi), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    units = {
        **{str(n): f"Electoral unit {n}" for n in range(1, 7)},
        **{str(n): name for n, name in ABROAD_2014.items()},
        "abroad": "Electoral unit 7 (abroad), 2016",
    }
    for code, name in units.items():
        id = district_area(code)
        p.add("area", id=id, scheme_code=S_DISTRICT)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
        number = code if code.isdigit() else "7"
        p.name(id, f"Изборна единица {number}", "mk", "official", False)
    p.add(
        "boundary_set",
        id=key("boundary_set", S_DISTRICT, 2011),
        scheme_code=S_DISTRICT,
        vintage="2011",
        valid_from=date(2011, 1, 1),
        as_of=shapes.retrieved_at.date(),
    )
    for unit, names in UNIT_MUNICIPALITIES.items():
        union = shapely.union_all([outlines[name] for name in names.split(",")])
        polygons = [
            part
            for geometry in shapely.get_parts(union)
            if shapely.get_type_id(geometry) in (3, 6)
            for part in shapely.get_parts(geometry)
        ]
        multi = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
        p.add(
            "area_geometry",
            area_id=district_area(str(unit)),
            valid_from=date(2011, 1, 1),
            geom=shapely.to_wkb(multi, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(multi), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Assembly of North Macedonia, its electoral system, the State Election Commission, and the lists on the "
        "ballots of the 2014, 2016, 2020, and 2024 elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {MACEDONIA}
    p.add(
        "source",
        id=SEC,
        kind="official_body",
        publisher="Државна изборна комисија",
        title="Assembly elections: decisions and results",
        url=HOME,
        license="No license stated; published by the State Election Commission for public use",
        attribution="Државна изборна комисија на Република Северна Македонија",
        notes="The Commission's decisions on final results, its tables of results by polling station, and its results "
        "service.",
    )
    p.add("body", id=ASSEMBLY, polity_id=MACEDONIA, kind="unicameral")
    p.name(ASSEMBLY, "Assembly of the Republic of North Macedonia", "en", "official")
    p.name(ASSEMBLY, "Собрание на Република Северна Македонија", "mk", "official", False)
    p.add("electoral_system", id=SYSTEM, polity_id=MACEDONIA, valid_from=date(2002, 1, 1), rules=RULES, rules_version=1)
    p.name(SYSTEM, "Closed lists, D'Hondt, in electoral units", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Since 2002 the Assembly is elected in six electoral units of 20 seats each, by closed lists of "
        "candidates and the D'Hondt divisors, with no threshold. From 2011 to 2016 voters abroad also had electoral "
        "units of their own: three units electing one deputy each in 2011 and 2014, and one unit abroad in 2016 "
        "that elected none.",
        lang="en",
    )
    seen: dict[str, Ballot] = {}
    for year in DAYS:
        for ballot in ballots(year):
            seen.setdefault(ballot.party, ballot)
            if seen[ballot.party].kind != ballot.kind:
                raise ValueError(f"{ballot.party} is both {seen[ballot.party].kind} and {ballot.kind}")
    for ballot in seen.values():
        id = party(ballot.party)
        p.add("party", id=id, kind=ballot.kind, polity_id=MACEDONIA)
        p.name(id, ballot.label, "mk", "official")
        if ballot.short and ballot.short != ballot.label:
            p.name(id, ballot.short, "mk", "short", False)
        if ballot.sq:
            p.name(id, ballot.sq, "sq", "official", False)
    return p


def party(code: str) -> str:
    return key("party", code)


# Elections.


def unit_id(year: int, *parts: object) -> str:
    return key("unit", DAYS[year], *parts)


def contest_name(unit: str) -> str:
    return f"Electoral unit {unit}" if unit.isdigit() else "Electoral unit 7 (abroad)"


def unit_code(year: int, unit: int) -> str:
    return "abroad" if year == 2016 and unit == 7 else str(unit)


def build(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Assembly election of North Macedonia by electoral unit"
        + (", municipality, and polling station" if year in (2014, 2020) else "")
        + ", from the State Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MACEDONIA, COUNTRY}
    election = key("election", day)
    path_ = f"mk/{day}-general"
    p.add("election", id=election, polity_id=MACEDONIA, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path_, entity_id=election, is_current=True)
    adjective = "Macedonian" if year < 2019 else "North Macedonian"
    p.name(election, f"{year} {adjective} parliamentary election", "en", "official")
    p.name(election, f"Избори за пратеници во Собранието, {year}", "mk", "native", False)
    lists = {ballot.id: ballot for ballot in ballots(year)}
    return {2014: build_2014, 2016: build_2016, 2020: build_2020, 2024: build_2024}[year](p, election, path_, lists)


def contest(
    p: Package,
    year: int,
    election: str,
    path_: str,
    lists: dict[str, Ballot],
    code: str,
    magnitude: int | None,
    present: set[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """The contest of one electoral unit, with its default result set and an option for each list."""
    day = DAYS[year]
    cid = key("contest", day, code)
    p.add(
        "contest",
        id=cid,
        election_id=election,
        kind="list_allocation",
        body_id=ASSEMBLY,
        district_area_id=district_area(code),
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(cid, contest_name(code), "en", "official")
    p.add("slug", path=f"{path_}/unit-{code}", entity_id=cid, is_current=True)
    options: dict[str, str] = {}
    for ballot in lists.values():
        if present is not None and ballot.id not in present:
            continue
        option = options[ballot.id] = key("option", day, code, ballot.id)
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="list",
            party_id=party(ballot.party),
            ballot_position=ballot.position,
            ballot_label=ballot.label,
        )
    return cid, options


def result_set(
    p: Package, year: int, election: str, cid: str, code: str, tag: str, *, default: bool, completeness: float
) -> str:
    rs = key("result_set", DAYS[year], code, tag)
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=SEC,
        status="active",
        is_default=default,
        completeness=completeness,
    )
    return rs


def unit_row(
    p: Package, year: int, tag: str, scheme: str, code: str, area: str, relation: str, kind: str, parent: str | None
) -> str:
    unit = unit_id(year, tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MACEDONIA,
        scheme_code=scheme,
        code=f"{DAYS[year]}:{code}",
        parent_id=parent,
        area_relation=relation,
        area_id=area,
        kind=kind,
    )
    return unit


def fact(rs: str, unit: str, option: str | None, measure: int, value: int, flags: int = 0) -> dict[str, object]:
    return {
        "result_set_id": rs,
        "unit_id": unit,
        "option_id": option,
        "channel": 0,
        "measure": measure,
        "count_round": 0,
        "flags": flags,
        "value": value,
    }


FACT_SCHEMA = {
    "result_set_id": pl.String,
    "unit_id": pl.String,
    "option_id": pl.String,
    "channel": pl.Int16,
    "measure": pl.Int16,
    "count_round": pl.Int16,
    "flags": pl.Int16,
    "value": pl.Int64,
}
LOCATOR_SCHEMA = {
    "result_set_id": pl.String,
    "unit_id": pl.String,
    "option_id": pl.String,
    "channel": pl.Int16,
    "measure": pl.Int16,
    "count_round": pl.Int16,
    "page_id": pl.String,
    "source_id": pl.String,
    "x0": pl.Float64,
    "y0": pl.Float64,
    "x1": pl.Float64,
    "y1": pl.Float64,
}


def pages(p: Package, year: int, name: str, numbers: set[int]) -> dict[int, str]:
    stored = raw.cached(SOURCE, FILES[name])
    artifact = p.artifact(stored, SEC)
    found: dict[int, str] = {}
    for number in sorted(numbers):
        found[number] = key("page", day_of(year), name, number)
        width, height = page_size(stored.path, number)
        p.add(
            "source_page",
            id=found[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
            width=width,
            height=height,
        )
    return found


def day_of(year: int) -> date:
    return DAYS[year]


def page_size(file: Path, number: int) -> tuple[int, int]:
    out = subprocess.run(
        ["pdfinfo", "-f", str(number), "-l", str(number), str(file)], capture_output=True, text=True, check=True
    ).stdout
    found = re.search(rf"Page\s+{number} size:\s+([\d.]+) x ([\d.]+)", out)
    if found is None:
        raise ValueError(f"{file}: no size for page {number}")
    return round(float(found[1])), round(float(found[2]))


def note(p: Package, entity: str, kind: str, text: str, tag: str = "") -> None:
    p.add("note", id=key("note", entity, tag), entity_id=entity, kind=kind, body_md=text, lang="en")


def finish(p: Package, rows: list[dict[str, object]], locators: list[dict[str, object]]) -> Package:
    p.frame("result_value", pl.DataFrame(rows, schema=FACT_SCHEMA))
    if locators:
        p.frame("value_locator", pl.DataFrame(locators, schema=LOCATOR_SCHEMA))
    return p


def district_units(p: Package, year: int, codes: list[str]) -> dict[str, str]:
    found = {}
    for code in codes:
        found[code] = unit_row(p, year, f"d{code}", S_DISTRICT, code, district_area(code), "equals", "aggregate", None)
        p.name(found[code], contest_name(code), "en", "official")
    return found


# 2014.


def build_2014(p: Package, election: str, path_: str, lists: dict[str, Ballot]) -> Package:
    year = 2014
    stored = raw.cached(SOURCE, FILES["csv14"])
    p.artifact(stored, SEC)
    p.artifact(raw.cached(SOURCE, FILES["json14"]), SEC)
    found = stations_2014()
    codes = [str(n) for n in range(1, 10)]
    districts = district_units(p, year, codes)
    where: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in found:
        if s.municipality is not None and str(s.unit) not in where[(s.municipality, "")]:
            where[(s.municipality, "")].append(str(s.unit))
    munis: dict[tuple[str, int], str] = {}
    rows: list[dict[str, object]] = []
    sets: dict[str, tuple[str, dict[str, str]]] = {}
    for code in codes:
        magnitude = 20 if int(code) <= 6 else 1
        cid, options = contest(p, year, election, path_, lists, code, magnitude)
        rs = result_set(p, year, election, cid, code, "stations", default=True, completeness=1.0)
        note(
            p,
            rs,
            "methodology",
            "The results by polling station as the State Election Commission's table gives them: registered voters, "
            "voters who voted, valid and invalid ballots, and each list's votes. The table gives no seats, so no "
            "outcomes are stored for this election.",
        )
        sets[code] = (rs, options)
    seen: set[str] = set()
    for s in found:
        code = str(s.unit)
        rs, options = sets[code]
        if s.municipality is not None:
            mkey = (s.municipality, s.unit)
            if mkey not in munis:
                split = len(where[(s.municipality, "")]) > 1
                munis[mkey] = unit_row(
                    p,
                    year,
                    f"m{s.unit}:{s.municipality}",
                    S_MUNI,
                    f"{s.unit}:{s.municipality}",
                    muni_area(s.municipality),
                    "within" if split else "equals",
                    "aggregate",
                    districts[code],
                )
            parent, area, relation = munis[mkey], muni_area(s.municipality), "within"
            tag = s.code
        else:
            parent, area, relation = districts[code], district_area(code), "within"
            tag = s.code
        if tag in seen:
            raise ValueError(f"station {tag} repeats")
        seen.add(tag)
        unit = unit_row(
            p,
            year,
            f"s{tag}",
            S_STATION,
            tag,
            area,
            relation,
            "abroad_mission" if s.municipality is None else "standard",
            parent,
        )
        if s.name is not None:
            p.name(unit, s.name, "mk", "official")
        for measure, value in (
            (REGISTERED, s.registered),
            (BALLOTS, s.ballots),
            (VALID, s.valid),
            (INVALID, s.invalid),
        ):
            if value is not None:
                rows.append(fact(rs, unit, None, measure, value))
        for list_id, votes in s.votes.items():
            rows.append(fact(rs, unit, options[list_id], VOTES, votes))
    stations = Counter(s.unit for s in found)
    expected = {1: 522, 2: 504, 3: 682, 4: 599, 5: 655, 6: 516, 7: 27, 8: 6, 9: 3}
    if dict(stations) != expected:
        raise ValueError(f"stations per unit {dict(stations)} differ from the results service's {expected}")
    p.accept(
        "accounting.ballots_sum",
        "Four polling stations (0058, 1277, 1640/1, 1725) print more voters who voted than valid plus invalid ballots; "
        "the table's own figures are stored as printed",
    )
    p.accept(
        "accounting.votes_valid",
        "One polling station prints valid ballots that differ from its lists' votes; the table's own figures are "
        "stored as printed",
    )
    return finish(p, rows, [])


# 2016 and 2020: the Commission's decisions, read from scans.


def scan_option(options: dict[str, str], list_id: str) -> str:
    return options[list_id]


def build_2016(p: Package, election: str, path_: str, lists: dict[str, Ballot]) -> Package:
    year = 2016
    p.artifact(raw.cached(SOURCE, FILES["json16"]), SEC)
    table = scans()["2016"]
    stored = pages(p, year, "scan16", {cast(int, row["page"]) for row in table})
    stats = stats_2016()
    rows: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    codes = [str(n) for n in range(1, 7)] + ["abroad"]
    districts = district_units(p, year, codes)
    for number, code in enumerate(codes, 1):
        magnitude = 20 if number <= 6 else None
        mine = [row for row in table if row["unit"] == number]
        # A list that has no row in this unit was not on its ballot.
        cid, options = contest(p, year, election, path_, lists, code, magnitude, {str(row["list"]) for row in mine})
        rs = result_set(p, year, election, cid, code, "unit", default=True, completeness=1.0)
        note(
            p,
            rs,
            "methodology",
            "Each list's votes and seats in the unit, read from the table in the State Election Commission's "
            "decision of 28 December 2016, and the registered voters from the Commission's results service. "
            + (
                "The decision gives no seats for this unit, which covers the voters abroad."
                if number == 7
                else "The unit elects 20 deputies."
            ),
        )
        unit = districts[code]
        rows.append(fact(rs, unit, None, REGISTERED, stats[number]["registered"]))
        for row in mine:
            option = options[str(row["list"])]
            rows.append(fact(rs, unit, option, VOTES, cast(int, row["votes"]), DIGITIZED))
            locators.append(
                locator(rs, unit, option, stored[cast(int, row["page"])], cast(list[float], row["votes_box"]))
            )
            if cast(int, row["seats"]) > 0:
                p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=row["seats"])
    return finish(p, rows, locators)


def locator(
    rs: str, unit: str, option: str | None, page: str, box: list[float], measure: int = VOTES
) -> dict[str, object]:
    return {
        "result_set_id": rs,
        "unit_id": unit,
        "option_id": option,
        "channel": 0,
        "measure": measure,
        "count_round": 0,
        "page_id": page,
        "source_id": None,
        "x0": box[0],
        "y0": box[1],
        "x1": box[2],
        "y1": box[3],
    }


def build_2020(p: Package, election: str, path_: str, lists: dict[str, Ballot]) -> Package:
    year = 2020
    p.artifact(raw.cached(SOURCE, FILES["form16"]), SEC)
    table = scans()["2020"]
    stored = pages(p, year, "scan20", {cast(int, row["page"]) for row in table})
    found = stations_2020()
    codes = [str(n) for n in range(1, 7)]
    districts = district_units(p, year, codes)
    where: dict[str, set[int]] = defaultdict(set)
    for s in found:
        assert s.municipality is not None
        where[s.municipality].add(s.unit)
    rows: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    munis: dict[tuple[str, int], str] = {}
    seen: set[str] = set()
    for number, code in enumerate(codes, 1):
        mine = [row for row in table if row["unit"] == number]
        here = [s for s in found if s.unit == number]
        cid, options = contest(p, year, election, path_, lists, code, 20, {str(row["list"]) for row in mine})
        official = result_set(p, year, election, cid, code, "decision", default=True, completeness=1.0)
        fine = result_set(p, year, election, cid, code, "stations", default=False, completeness=0.0)
        note(
            p,
            official,
            "methodology",
            "Each list's votes and seats in the unit, read from the tables of the State Election Commission's "
            "decision of 25 July 2020.",
        )
        unit = districts[code]
        total = 0
        for row in mine:
            option = options[str(row["list"])]
            rows.append(fact(official, unit, option, VOTES, cast(int, row["votes"]), DIGITIZED))
            locators.append(
                locator(official, unit, option, stored[cast(int, row["page"])], cast(list[float], row["votes_box"]))
            )
            total += cast(int, row["votes"])
            if cast(int, row["seats"]) > 0:
                p.add("outcome", result_set_id=official, option_id=option, kind="won", seats=row["seats"])
        summed = 0
        for s in here:
            assert s.municipality is not None
            mkey = (s.municipality, s.unit)
            if mkey not in munis:
                munis[mkey] = unit_row(
                    p,
                    year,
                    f"m{s.unit}:{s.municipality}",
                    S_MUNI,
                    f"{s.unit}:{s.municipality}",
                    muni_area(s.municipality),
                    "within" if len(where[s.municipality]) > 1 else "equals",
                    "aggregate",
                    districts[code],
                )
            tag = s.code
            if tag in seen:
                raise ValueError(f"station {tag} repeats")
            seen.add(tag)
            st = unit_row(
                p, year, f"s{tag}", S_STATION, tag, muni_area(s.municipality), "within", "standard", munis[mkey]
            )
            for list_id, votes in s.votes.items():
                if list_id not in options:
                    raise ValueError(f"unit {number} station {tag}: list {list_id} is not in the decision")
                rows.append(fact(fine, st, options[list_id], VOTES, votes))
                summed += votes
        for p_row in p.rows["result_set"]:
            if p_row["id"] == fine:
                p_row["completeness"] = round(summed / total, 6)
        note(
            p,
            fine,
            "methodology",
            "Each list's votes at each polling station, from the Commission's table of results based on the minutes "
            "of the polling boards (Forms 16 and 16 ZRD). The stations' list votes add up to "
            f"{summed:,} of the {total:,} the decision gives for the unit. The table gives no registered voters or "
            "ballot counts.",
        )
    return finish(p, rows, locators)


def build_2024(p: Package, election: str, path_: str, lists: dict[str, Ballot]) -> Package:
    year = 2024
    stored = raw.cached(SOURCE, FILES["pdf24"])
    p.artifact(stored, SEC)
    found = conclusion_2024()
    codes = [str(n) for n in range(1, 7)]
    districts = district_units(p, year, codes)
    rows: list[dict[str, object]] = []
    for number, code in enumerate(codes, 1):
        mine = found[number]
        cid, options = contest(p, year, election, path_, lists, code, 20, {name for name, _, _ in mine})
        rs = result_set(p, year, election, cid, code, "decision", default=True, completeness=1.0)
        note(
            p,
            rs,
            "caveat",
            "The State Election Commission's decision of 18 May 2024 gives the votes and seats of the lists that won "
            "seats in the unit. Lists that won no seats, and the registered voters and ballot counts, are not in it, "
            "so only these lists have values.",
        )
        if number == 5:
            note(
                p,
                rs,
                "caveat",
                "The decision reports this unit before the voting was repeated at polling stations 1194 and 1202 in "
                "Krushevo, 1470 and 1470/1 in Dolneni, 1844 in Struga, and 1272 in Ohrid. A later decision of the "
                "Commission was to give the final votes and seats of the unit, so these figures may change.",
                "repeat",
            )
        if sum(seats for _, _, seats in mine) != 20:
            raise ValueError(f"unit {number}: {sum(seats for _, _, seats in mine)} seats")
        for list_id, votes, seats in mine:
            option = options[list_id]
            rows.append(fact(rs, districts[code], option, VOTES, votes))
            p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=seats)
    return finish(p, rows, [])
