"""Kyrgyzstan: the Central Commission for Elections and Referenda's published results.

The Commission's open data holds one workbook per election, with a sheet for the regions and a sheet for the
territorial election commissions (TIK). The 2021 single-member districts, the 2023 Lenin district by-election, and the
2025 snap parliamentary election are published by district. No file on the site goes below those units.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package

SOURCE = "kg.shailoo"
BASE = "https://shailoo.gov.kg"
KYRGYZSTAN = str(uuid5(package.NAMESPACE, "polity\x1fKG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fKGZ"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
RESULTS_2025 = (
    f"{BASE}/kg/vybory-noyabr-2025/rezultaty-dosrochnyh-vyborov-deputatov-zhogorku-kenesha-kyrgyzskoj-respubliki/"
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
PARLIAMENT = key("body", "jogorku-kenesh")
PRESIDENCY = key("office", "president")
SYSTEMS = {
    "president": key("electoral_system", "president"),
    "fptp": key("electoral_system", "fptp"),
    "referendum": key("electoral_system", "referendum"),
}

REGIONS = {
    "bishkek": ("Bishkek", "Бишкек шаары"),
    "osh-city": ("Osh (city)", "Ош шаары"),
    "batken": ("Batken Region", "Баткен облусу"),
    "jalal-abad": ("Jalal-Abad Region", "Жалал-Абад облусу"),
    "issyk-kul": ("Issyk-Kul Region", "Ысык-Көл облусу"),
    "naryn": ("Naryn Region", "Нарын облусу"),
    "osh": ("Osh Region", "Ош облусу"),
    "talas": ("Talas Region", "Талас облусу"),
    "chuy": ("Chuy Region", "Чүй облусу"),
}
ABROAD = "abroad"

OPEN = f"{BASE}/media"
FILES = {
    "2010": f"{OPEN}/askar/2020/01/30/04_02_2020_-10-2010__XUQBWpu.xlsx",
    "2011": f"{OPEN}/askar/2020/01/26/02_01_2020_-30-2011-__tnnIpwZ.xlsx",
    "2015": f"{OPEN}/askar/2020/01/26/03_01_2020_-2015__mBElNwe.xlsx",
    "2016": f"{OPEN}/askar/2020/01/26/05_01_2020_-2016__xXIRsnq.xlsx",
    "2017": f"{OPEN}/askar/2020/01/26/01_01_2020_-15-2017-__Lnqikui.xlsx",
    "2020": f"{OPEN}/ulan/2021/09/12/2020.xlsx",
    "2021-president": f"{OPEN}/askar/2021/09/08/0_0___-___.xlsx",
    "2021-governance": f"{OPEN}/alai/2021/09/12/2021__kg.xlsx",
    "2021-constitution": f"{OPEN}/ulan/2021/09/12/2021_ZsUFh4x.xlsx",
    "2021-parliament": f"{OPEN}/alai/2022/09/20/_28112021_KavbOj1.xlsx",
    "2021-districts": f"{OPEN}/alai/2022/09/20/36_OS772hH.xlsx",
    "2023-lenin": f"{OPEN}/alai/2023/10/24/26-2023_HsbeQfe.xlsx",
}

# Accounting rows by the number the sheets print in front of them, in the three layouts the Commission used.
ACCOUNTING = {
    "A": {"3": REGISTERED, "5": VOTERS, "7": VALID, "8": INVALID},
    "B": {"1": REGISTERED, "6": VOTERS, "10": VALID, "11": INVALID},
    "C": {"1": REGISTERED, "4": VOTERS, "6": VALID, "7": INVALID},
}
ACCOUNTING_ROW = re.compile(
    r"^(?:\d+[а-яa-z]?\.?\s*)?(?:Число|Участкалык|Шайлоо участкасында|Шайлоо бюллетендерин|Жокко|Жарактуу|Жараксыз|"
    r"Жайдан|Добуш берүү|Общее)",
    re.IGNORECASE,
)
SEATS = re.compile(r"\((\d+)\s+мандат")
NOTE_ACCOUNTING = (
    "Registered voters and valid and invalid ballots are the figures on the numbered lines of the Commission's "
    "summary table; voters is the number of voters who received a ballot. The table's other lines, such as ballots "
    "received by the polling stations, cancelled ballots, and ballots by place of voting, are not stored here."
)


@dataclass
class Opt:
    pos: int
    label: str
    kind: str  # list, candidate, answer, none_of_the_above
    seats: int | None = None
    declared: int | None = None


@dataclass
class Unit:
    code: str
    name: str
    scheme: str
    kind: str = "standard"
    region: str | None = None  # a REGIONS key, ABROAD, or None for a unit that is its own area
    area: tuple[str, str] | None = None  # (scheme, code) of the area the unit equals
    votes: dict[int, int | None] = field(default_factory=dict)
    accounting: dict[int, int | None] = field(default_factory=dict)


@dataclass
class Contest:
    key: str
    name_en: str
    name_local: str
    kind: str
    magnitude: int | None
    options: list[Opt]
    units: list[Unit]
    declared: dict[int, int | None] = field(default_factory=dict)  # measure -> national figure
    system: str | None = None
    question: str | None = None
    district: tuple[str, str, str] | None = None  # scheme, code, name
    note: str | None = None
    lang: str = "ky"
    status: str = "active"
    elected: list[int] = field(default_factory=list)  # positions of the options the table declares elected
    people: dict[int, str] = field(default_factory=dict)
    mismatched: list[str] = field(default_factory=list)


@dataclass
class Election:
    id: str
    day: date
    day_to: date | None
    precision: str
    family: str
    kind: str
    name_en: str
    name_local: str
    sources: list[str]
    contests: list[Contest]
    status: str = "held"
    note: str | None = None
    accepted: dict[str, str] = field(default_factory=dict)


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    page = raw.fetch(SOURCE, RESULTS_2025, suffix=".html")
    for link in re.findall(r'href="(/media/dastan/2025/1\d/\d+/[^"]+\.pdf)"', page.path.read_text()):
        raw.fetch(SOURCE, BASE + link, suffix=".pdf")


# Reading workbooks.


def num(text: str) -> int | None:
    squeezed = re.sub(r"[\s\u00a0\u202f]", "", text)
    if re.fullmatch(r"\d+", squeezed):
        return int(squeezed)
    if re.fullmatch(r"\d+\.0+", squeezed):
        return int(squeezed.split(".")[0])
    return None


def clean(text: str) -> str:
    return " ".join(text.replace("\u00a0", " ").split())


@functools.cache
def workbook(url: str) -> dict[str, list[list[str]]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    sheets: dict[str, list[list[str]]] = {}
    for name in book.sheet_names:
        frame = book.load_sheet_by_name(name, header_row=None).to_polars()
        sheets[name] = [["" if cell is None else str(cell) for cell in row] for row in frame.rows()]
    return sheets


def sheet(url: str, part: str) -> list[list[str]]:
    sheets = workbook(url)
    found = [name for name in sheets if part in name]
    if not found:
        raise LookupError(f"{url} has no sheet with {part!r}: {list(sheets)}")
    return sheets[found[0]]


@dataclass
class Layout:
    names: tuple[int, ...]  # header rows holding the unit names; the lowest filled one names the column
    label: int
    total: int
    first: int
    code: int | None = None
    seats: int | None = None
    start: int = 0
    family: str = "A"
    step: int = 1
    shift: int = 0  # how far left of a data column its name sits
    answers: bool = False


@dataclass
class Table:
    names: list[str]
    options: list[Opt]
    votes: list[list[int | None]]
    measures: dict[int, tuple[int | None, list[int | None]]]


def against(label: str) -> bool:
    folded = label.lower()
    return "баарына" in folded or "против всех" in folded


def wide(grid: list[list[str]], layout: Layout) -> Table:
    width = max(len(row) for row in grid)

    def cell(row: list[str], column: int) -> str:
        return row[column].strip() if 0 <= column < len(row) else ""

    columns: list[int] = []
    names: list[str] = []
    for column in range(layout.first, width, layout.step):
        name = ""
        for index in layout.names:
            text = cell(grid[index], column - layout.shift) if index < len(grid) else ""
            if text and num(text) is None:
                name = clean(text)
        if name:
            columns.append(column)
            names.append(name)
    options: list[Opt] = []
    votes: list[list[int | None]] = []
    measures: dict[int, tuple[int | None, list[int | None]]] = {}
    for row in grid[layout.start :]:
        label = clean(cell(row, layout.label))
        total = num(cell(row, layout.total))
        values = [num(cell(row, column)) for column in columns]
        if not label or (total is None and all(value is None for value in values)):
            continue
        code = cell(row, layout.code) if layout.code is not None else ""
        if ACCOUNTING_ROW.match(label):
            found = re.match(r"^(\d+[а-яa-z]?)\.?\s", label)
            number = (found.group(1) if found else re.sub(r"[.\s]", "", code)).lower()
            measure = ACCOUNTING[layout.family].get(number)
            if measure is not None:
                measures[measure] = (total, values)
            continue
        position = int(re.sub(r"\D", "", code)) if re.fullmatch(r"\s*\d+\.?\s*", code) else len(options) + 1
        label = re.sub(r"^\d+\.\s*", "", label)
        seats = num(cell(row, layout.seats)) if layout.seats is not None else None
        found = SEATS.search(label)
        if seats is None and found:
            seats = int(found.group(1))
        kind = "none_of_the_above" if against(label) else "answer" if layout.answers else "list"
        options.append(Opt(position, SEATS.sub("", label).strip(" ()") if found else label, kind, seats, total))
        votes.append(values)
    return Table(names, options, votes, measures)


def region_of(text: str) -> str | None:
    folded = text.lower().replace("ё", "е")
    if "мид" in folded or "тим" in folded:
        return ABROAD
    if "бишкек" in folded:
        return "bishkek"
    if "ош" in folded.split()[0:1] or folded.startswith("ош"):
        return "osh" if "обл" in folded else "osh-city"
    for part, region in (
        ("баткен", "batken"),
        ("жалал", "jalal-abad"),
        ("ысык", "issyk-kul"),
        ("ыссык", "issyk-kul"),
        ("иссык", "issyk-kul"),
        ("нарын", "naryn"),
        ("талас", "talas"),
        ("чүй", "chuy"),
        ("чуй", "chuy"),
    ):
        if part in folded:
            return region
    return None


def fingerprint(table: Table, index: int) -> dict[tuple[str, int], int]:
    found: dict[tuple[str, int], int] = {}
    for option, values in zip(table.options, table.votes, strict=True):
        if values[index] is not None:
            found[("o", option.pos)] = values[index] or 0
    for measure, (_, values) in table.measures.items():
        if values[index] is not None:
            found[("a", measure)] = values[index] or 0
    return found


def partition(places: Table, regions: Table) -> tuple[list[str], list[str]]:
    """Give each territorial commission its region by finding the run of columns that adds up to a region column."""
    wanted = [fingerprint(regions, index) for index in range(len(regions.names))]
    labels = [region_of(name) for name in regions.names]
    if None in labels or len(set(labels)) != len(labels):
        raise ValueError(f"region columns not recognized: {list(zip(regions.names, labels, strict=True))}")
    columns = [fingerprint(places, index) for index in range(len(places.names))]

    def search(start: int, free: frozenset[int], loose: int) -> list[tuple[int, int, bool]] | None:
        if start == len(columns):
            return [] if not free else None
        for region in sorted(free):
            target = wanted[region]
            running: dict[tuple[str, int], int] = {}
            for end in range(start, len(columns)):
                for item, value in columns[end].items():
                    running[item] = running.get(item, 0) + value
                if any(value > target.get(item, value) for item, value in running.items() if item in target):
                    break
                if all(running.get(item, 0) == value for item, value in target.items() if item in running):
                    rest = search(end + 1, free - {region}, loose)
                    if rest is not None:
                        return [(region, end - start + 1, True), *rest]
        if loose:
            for region in sorted(free):
                for end in range(start, len(columns)):
                    rest = search(end + 1, free - {region}, loose - 1)
                    if rest is not None:
                        return [(region, end - start + 1, False), *rest]
        return None

    found = search(0, frozenset(range(len(wanted))), 0) or search(0, frozenset(range(len(wanted))), 1)
    if found is None:
        raise ValueError("the territorial commissions do not add up to the regions in any order")
    assigned: list[str] = []
    mismatched: list[str] = []
    for region, length, exact in found:
        assigned += [labels[region] or ""] * length
        if not exact:
            mismatched.append(labels[region] or "")
    return assigned, mismatched


# Building units.


def tik_units(day: date, table: Table, regions: list[str]) -> list[Unit]:
    units: list[Unit] = []
    seen: set[str] = set()
    for index, (name, region) in enumerate(zip(table.names, regions, strict=True)):
        code = f"{day}:{name}"
        if code in seen:
            code = f"{day}:{name} ({region})"
        seen.add(code)
        unit = Unit(code, name, "kg.tik", "abroad_mission" if region == ABROAD else "standard", region, None)
        for option, values in zip(table.options, table.votes, strict=True):
            unit.votes[option.pos] = values[index]
        for measure, (_, values) in table.measures.items():
            unit.accounting[measure] = values[index]
        units.append(unit)
    return units


def standard(
    election: str,
    day: date,
    url: str,
    layout: Layout,
    *,
    name_en: str,
    name_local: str,
    kind: str,
    magnitude: int | None,
    system: str | None,
    question: str | None = None,
    key_: str,
    region_sheet: str = "областям",
    tik_sheet: str = "ТИК",
    lang: str = "ky",
    region_layout: Layout | None = None,
) -> Contest:
    places = wide(sheet(url, tik_sheet), layout)
    regions = wide(sheet(url, region_sheet), region_layout or layout)
    assigned, mismatched = partition(places, regions)
    units = tik_units(day, places, assigned)
    contest = Contest(
        key_,
        name_en,
        name_local,
        kind,
        magnitude,
        places.options,
        units,
        {measure: total for measure, (total, _) in places.measures.items()},
        system,
        question,
        lang=lang,
    )
    for option in contest.options:
        contest.declared[-option.pos] = option.declared
    contest.mismatched = mismatched
    if mismatched:
        contest.note = (
            "The territorial commissions' rows of the summary table add up to the region row everywhere except in "
            + ", ".join(REGIONS[region][0] for region in mismatched)
            + ", where the Commission's region row differs from its commissions' rows. Both are stored as published "
            "only for the commissions; the region row is not stored."
        )
    return contest


# 2021 presidential election: one sheet per region, each holding the territorial commissions.


def president_2021(day: date) -> Contest:
    url = FILES["2021-president"]
    layout = Layout(names=(0,), label=1, total=2, first=4, code=0, step=2, start=2)
    units: list[Unit] = []
    options: list[Opt] = []
    for part, region in (
        ("Бишкек", "bishkek"),
        ("Ош", "osh-city"),
        ("Баткен", "batken"),
        ("Жалал", "jalal-abad"),
        ("Иссык", "issyk-kul"),
        ("Нарын", "naryn"),
        ("Ош обл", "osh"),
        ("Талас", "talas"),
        ("Чуй", "chuy"),
        ("МИД", ABROAD),
    ):
        name = next(
            sheet_name
            for sheet_name in workbook(url)
            if (part in sheet_name and (part != "Ош" or sheet_name.strip() == "Ош"))
            and (part != "Ош обл" or "обл" in sheet_name)
        )
        grid = workbook(url)[name]
        single = len(grid[0]) <= 4
        table = wide(grid, Layout(names=(0,), label=1, total=2, first=2, code=0, start=2) if single else layout)
        if single:
            table.names = [name_of(region)]
        if not options:
            options = table.options
        elif [o.label for o in options] != [o.label for o in table.options]:
            raise ValueError(f"{name}: candidates differ from the other regions")
        units += tik_units(day, table, [region] * len(table.names))
    totals = wide(workbook(url)["сводная_общая"], Layout(names=(1,), label=0, total=2, first=2, step=100, start=3))
    contest = Contest(
        "president",
        "2021 Kyrgyz presidential election",
        "КР Президентин мөөнөтүнөн мурда шайлоо, 2021",
        "office",
        1,
        options,
        units,
        {measure: total for measure, (total, _) in totals.measures.items()},
        SYSTEMS["president"],
        lang="ru",
    )
    for option, total in zip(totals.options, [o.declared for o in totals.options], strict=True):
        contest.declared[-option.pos] = total
    return contest


def disagreement(contest: Contest) -> str:
    """Say where the territorial commissions' lines differ from the Commission's national figures."""
    parts = []
    for measure, label in (
        (REGISTERED, "registered voters"),
        (VOTERS, "voters"),
        (VALID, "valid ballots"),
        (INVALID, "invalid ballots"),
    ):
        summed = sum(unit.accounting.get(measure) or 0 for unit in contest.units)
        declared = contest.declared.get(measure)
        if declared is not None and summed != declared:
            parts.append(f"{label} {summed:,} against {declared:,}")
    regions = ", ".join(REGIONS[region][0] for region in contest.mismatched)
    return (
        f"In the Commission's table by territorial commission the ballot lines of {regions} do not add up to its "
        f"region row, so the commissions' lines sum to more than its national figures ({'; '.join(parts)}). The votes "
        "for every option do match. The commissions' lines are stored as published, and the national figures are "
        "its own."
    )


def name_of(region: str) -> str:
    return {"bishkek": "Бишкек", "osh-city": "Ош", ABROAD: "КР ТИМ"}[region]


@functools.cache
def elections() -> list[Election]:
    found = [
        election_2010(),
        election_2011(),
        election_2015(),
        election_2016(),
        election_2017(),
        election_2020(),
        election_2021_january(),
        election_2021_april(),
        election_2021_november(),
        election_2023(),
        election_2025(),
    ]
    return sorted(found, key=lambda election: election.day)


def person_names(contest: Contest) -> None:
    for option in contest.options:
        if option.kind == "list":
            option.kind = "candidate"
            contest.people[option.pos] = option.label


def election_2010() -> Election:
    day = date(2010, 10, 10)
    layout = Layout(names=(1,), label=1, total=2, first=5, code=0, seats=4, start=2, family="B")
    contest = standard(
        "2010",
        day,
        FILES["2010"],
        layout,
        name_en="2010 Kyrgyz parliamentary election",
        name_local="Жогорку Кеңештин депутаттарын шайлоо, 2010",
        kind="list_allocation",
        magnitude=120,
        system=None,
        key_="party-list",
        lang="ru",
    )
    return Election(
        "2010-10-10",
        day,
        None,
        "day",
        "parliamentary",
        "general",
        contest.name_en,
        contest.name_local,
        [FILES["2010"]],
        [contest],
    )


def election_2011() -> Election:
    day = date(2011, 10, 30)
    layout = Layout(names=(3,), label=1, total=2, first=4, code=0, start=4, family="C")
    contest = standard(
        "2011",
        day,
        FILES["2011"],
        layout,
        name_en="2011 Kyrgyz presidential election",
        name_local="КР Президентин шайлоо, 2011",
        kind="office",
        magnitude=1,
        system=SYSTEMS["president"],
        key_="president",
        lang="ru",
    )
    person_names(contest)
    return Election(
        "2011-10-30",
        day,
        None,
        "day",
        "presidential",
        "general",
        contest.name_en,
        contest.name_local,
        [FILES["2011"]],
        [contest],
    )


def election_2015() -> Election:
    day = date(2015, 10, 4)
    layout = Layout(names=(3,), label=1, total=2, first=5, code=0, seats=4, start=4)
    contest = standard(
        "2015",
        day,
        FILES["2015"],
        layout,
        name_en="2015 Kyrgyz parliamentary election",
        name_local="Жогорку Кеңештин депутаттарын шайлоо, 2015",
        kind="list_allocation",
        magnitude=120,
        system=None,
        key_="party-list",
    )
    return Election(
        "2015-10-04",
        day,
        None,
        "day",
        "parliamentary",
        "general",
        contest.name_en,
        contest.name_local,
        [FILES["2015"]],
        [contest],
    )


def election_2016() -> Election:
    day = date(2016, 12, 11)
    layout = Layout(names=(2, 3), label=0, total=1, first=3, start=4, answers=True)
    contest = standard(
        "2016",
        day,
        FILES["2016"],
        layout,
        name_en="2016 Kyrgyz constitutional referendum",
        name_local="Конституцияга өзгөртүүлөрдү киргизүү боюнча референдум, 2016",
        kind="ballot_question",
        magnitude=None,
        system=SYSTEMS["referendum"],
        question="optional_referendum",
        key_="constitution",
        region_sheet="свод_области",
        tik_sheet="свод_ТИК",
    )
    return Election(
        "2016-12-11",
        day,
        None,
        "day",
        "referendum",
        "referendum",
        contest.name_en,
        contest.name_local,
        [FILES["2016"]],
        [contest],
    )


def election_2017() -> Election:
    day = date(2017, 10, 15)
    layout = Layout(names=(5,), label=1, total=2, first=4, code=0, start=6)
    contest = standard(
        "2017",
        day,
        FILES["2017"],
        layout,
        name_en="2017 Kyrgyz presidential election",
        name_local="КР Президентин шайлоо, 2017",
        kind="office",
        magnitude=1,
        system=SYSTEMS["president"],
        key_="president",
        region_sheet="областям",
        lang="ru",
    )
    person_names(contest)
    return Election(
        "2017-10-15",
        day,
        None,
        "day",
        "presidential",
        "general",
        contest.name_en,
        contest.name_local,
        [FILES["2017"]],
        [contest],
    )


NOTE_2020 = (
    "The Central Commission annulled the results of this election on 6 October 2020, and a new election was held on "
    "28 November 2021. The Commission's open data still publishes the count, which is stored here as published."
)


def election_2020() -> Election:
    day = date(2020, 10, 4)
    layout = Layout(names=(1, 12), label=0, total=1, first=2, start=2)
    contest = standard(
        "2020",
        day,
        FILES["2020"],
        layout,
        name_en="2020 Kyrgyz parliamentary election",
        name_local="Жогорку Кеңештин депутаттарын шайлоо, 2020",
        kind="list_allocation",
        magnitude=120,
        system=None,
        key_="party-list",
        region_sheet="регионам",
        tik_sheet="ТИКам",
        region_layout=Layout(names=(0,), label=0, total=1, first=2, start=1),
    )
    contest.status = "annulled"
    contest.note = NOTE_2020
    return Election(
        "2020-10-04",
        day,
        None,
        "day",
        "parliamentary",
        "general",
        contest.name_en,
        contest.name_local,
        [FILES["2020"]],
        [contest],
        status="annulled",
        note=NOTE_2020,
    )


def election_2021_january() -> Election:
    day = date(2021, 1, 10)
    president = president_2021(day)
    layout = Layout(names=(0, 1), label=0, total=1, first=3, start=2, answers=True)
    governance = standard(
        "2021g",
        day,
        FILES["2021-governance"],
        layout,
        name_en="2021 Kyrgyz referendum on the form of government",
        name_local="Башкаруу формасын аныктоо боюнча референдум, 2021",
        kind="ballot_question",
        magnitude=None,
        system=SYSTEMS["referendum"],
        question="plebiscite",
        key_="form-of-government",
        region_sheet="регионам",
        tik_sheet="ТИКам",
    )
    accepted = {}
    if governance.mismatched:
        accepted["accounting.declared"] = disagreement(governance)
    return Election(
        "2021-01-10",
        day,
        None,
        "day",
        "general",
        "general",
        "2021 Kyrgyz presidential election and referendum",
        "КР Президентин мөөнөтүнөн мурда шайлоо жана референдум, 2021",
        [FILES["2021-president"], FILES["2021-governance"]],
        [president, governance],
        accepted=accepted,
    )


def election_2021_april() -> Election:
    day = date(2021, 4, 11)
    layout = Layout(names=(1, 2), label=0, total=1, first=3, start=3, answers=True)
    contest = standard(
        "2021c",
        day,
        FILES["2021-constitution"],
        layout,
        name_en="2021 Kyrgyz constitutional referendum",
        name_local="Конституциянын долбоору боюнча референдум, 2021",
        kind="ballot_question",
        magnitude=None,
        system=SYSTEMS["referendum"],
        question="optional_referendum",
        key_="constitution",
        region_sheet="регионам",
        tik_sheet="ТИКам",
    )
    return Election(
        "2021-04-11",
        day,
        None,
        "day",
        "referendum",
        "referendum",
        contest.name_en,
        contest.name_local,
        [FILES["2021-constitution"]],
        [contest],
    )


def election_2021_november() -> Election:
    day = date(2021, 11, 28)
    url = FILES["2021-parliament"]
    grid = sheet(url, "баардык")
    manual = Layout(names=(16,), label=0, total=3, first=7, step=4, shift=2, start=4)
    table = wide(grid, manual)
    regions = [region_of(name) or "" for name in table.names]
    units = []
    for index, (name, region) in enumerate(zip(table.names, regions, strict=True)):
        code = f"{day}:{name}"
        abroad = region == ABROAD
        unit = Unit(
            code,
            name,
            "kg.oblast",
            "abroad_mission" if abroad else "standard",
            region,
            None if abroad else ("kg.oblast", region),
        )
        for option, values in zip(table.options, table.votes, strict=True):
            unit.votes[option.pos] = values[index]
        for measure, (_, values) in table.measures.items():
            unit.accounting[measure] = values[index]
        units.append(unit)
    seats = {
        option.pos: option.seats
        for option in wide(
            sheet(url, "Баштапкы"), Layout(names=(1,), label=1, total=2, first=4, code=0, start=2)
        ).options
    }
    contest = Contest(
        "party-list",
        "2021 Kyrgyz parliamentary election, party lists",
        "Жогорку Кеңештин депутаттарын шайлоо, партиялык тизмелер, 2021",
        "list_allocation",
        54,
        table.options,
        units,
        {measure: total for measure, (total, _) in table.measures.items()},
        None,
    )
    for option in contest.options:
        contest.declared[-option.pos] = option.declared
        option.seats = seats.get(option.pos)
    districts = single_member(day, url=FILES["2021-districts"], prefix="2021")
    return Election(
        "2021-11-28",
        day,
        None,
        "day",
        "parliamentary",
        "general",
        "2021 Kyrgyz parliamentary election",
        "Жогорку Кеңештин депутаттарын шайлоо, 2021",
        [url, FILES["2021-districts"]],
        [contest, *districts],
    )


def single_member(day: date, *, url: str, prefix: str) -> list[Contest]:
    grid = sheet(url, "Лист1")
    contests: list[Contest] = []
    current: Contest | None = None
    for row in grid[3:]:
        label = clean(row[1]) if len(row) > 1 else ""
        if not label:
            continue
        found = re.match(r"^№\s*(\d+)\s+(.*?)\s+шайлоо округу$", label)
        if found:
            number, name = found.group(1), found.group(2)
            current = Contest(
                f"district-{number}",
                f"{prefix} Kyrgyz parliamentary election, {name} single-member district",
                f"{name} бир мандаттуу шайлоо округу, {prefix}",
                "body_seats",
                1,
                [],
                [],
                system=SYSTEMS["fptp"],
                district=("kg.okrug", number, name),
            )
            current.units.append(Unit(f"{day}:{number}", name, "kg.okrug", "standard", None, ("kg.okrug", number)))
            contests.append(current)
            continue
        if current is None:
            raise ValueError(f"{label}: candidate before the first district")
        votes = num(row[2])
        position = len(current.options) + 1
        if against(label):
            current.options.append(Opt(position, label, "none_of_the_above"))
        else:
            current.options.append(Opt(position, label, "candidate"))
            current.people[position] = label
        if votes is None:
            current.note = (current.note or "") + (
                f" The table leaves the vote count of candidate {position}, {label}, empty; no value is stored for them."
            )
        else:
            current.units[0].votes[position] = votes
    return contests


def election_2023() -> Election:
    day = date(2023, 1, 1)
    contests = single_member(day, url=FILES["2023-lenin"], prefix="2023")
    for contest in contests:
        contest.note = (
            "The Commission's open data titles this file only with the year 2023. The Lenin district held a "
            "by-election on 28 May 2023 and a repeat on 10 September 2023, and the file does not say which it holds, "
            "so the election is dated to the year."
        )
    return Election(
        "2023-lenin",
        day,
        date(2023, 12, 31),
        "year",
        "by-election",
        "by_election",
        "2023 Kyrgyz parliamentary by-election, Lenin district",
        "Ленин бир мандаттуу шайлоо округу боюнча шайлоо, 2023",
        [FILES["2023-lenin"]],
        contests,
        note=contests[0].note,
    )


# The 2025 snap parliamentary election: one PDF per multi-member district.

NUMBERS = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+")


def pdf_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def figures(text: str) -> list[int]:
    return [int(re.sub(r"\D", "", found)) for found in NUMBERS.findall(text)]


def table_rows(lines: list[str]) -> list[tuple[int, str, int]]:
    entries: list[list[object]] = []
    for line in lines:
        found = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if found:
            entries.append([int(found.group(1)), found.group(2)])
        elif entries and line.strip() and not line.strip().startswith("№"):
            entries[-1][1] = f"{entries[-1][1]} {line.strip()}"
    rows = []
    for position, text in entries:
        text = " ".join(str(text).split())
        last = list(NUMBERS.finditer(text))[-1]
        rows.append((int(str(position)), text[: last.start()].strip(), int(re.sub(r"\D", "", last.group()))))
    return rows


def district_2025(url: str, day: date) -> Contest:
    text = pdf_text(url).replace("\u00a0", " ").replace("\u202f", " ")
    lines = text.splitlines()
    number = re.search(r"№\s*(\d+)\s+көп", text)
    filed = re.match(r"\d+", url.rsplit("/", 1)[1])
    if number is None or filed is None:
        raise ValueError(f"{url}: no district number")
    head = next(index for index, line in enumerate(lines) if "фамилиясы" in line) - 1
    segments = re.split(r"(?m)^\s*(\d[аб]?)\s+(?=\D)", "\n".join(lines[1:head]))
    items = {code: (figures(body) or [None])[0] for code, body in zip(segments[1::2], segments[2::2], strict=True)}
    after = lines[head:]
    cut = next(index for index, line in enumerate(after) if "Шайланган" in line)
    candidates = table_rows(after[1:cut])
    elected = table_rows(after[cut + 1 :])
    code = filed.group()
    options = []
    people = {}
    for position, label, _ in candidates:
        if "каршы" in label.lower():
            options.append(Opt(position, "Баарына каршы", "none_of_the_above"))
        else:
            options.append(Opt(position, label, "candidate"))
            people[position] = label
    unit = Unit(f"{day}:{code}", f"№ {code}", "kg.mmd", "standard", None, ("kg.mmd", code))
    for position, _, votes in candidates:
        unit.votes[position] = votes
    for item, measure in (("1", REGISTERED), ("2", VOTERS), ("3", VALID), ("4", INVALID)):
        unit.accounting[measure] = items.get(item)
    names = {re.sub(r"\W", "", label).lower(): position for position, label, _ in candidates}
    contest = Contest(
        f"district-{code}",
        f"2025 Kyrgyz snap parliamentary election, multi-member district {code}",
        f"№ {code} көп мандаттуу шайлоо округу, 2025",
        "body_seats",
        3,
        options,
        [unit],
        district=("kg.mmd", code, f"№ {code}"),
        people=people,
    )
    contest.elected = [names[re.sub(r"\W", "", label).lower()] for _, label, _ in elected]
    contest.note = (
        "The district's PDF table lists the votes for each candidate and for the position Against all, and the "
        "candidates it declares elected. Its accounting lines are stored as printed."
    )
    if number.group(1) != code:
        contest.note += (
            f" The PDF is headed district {number.group(1)}, but the Commission's results page files it as district "
            f"{code}, and district {number.group(1)} has its own file."
        )
    summed = sum(votes for _, _, votes in candidates)
    if items.get("3") != summed:
        contest.note += (
            f" The printed valid-ballots line is {items.get('3'):,}, while the candidate lines and the Against all "
            f"line add up to {summed:,}."
        )
    if number.group(1) != code:
        contest.note += (
            f" The PDF is headed district {number.group(1)}, but the Commission's results page files it as district "
            f"{code}, and district {number.group(1)} has its own file."
        )
    summed = sum(votes for _, _, votes in candidates)
    if items.get("3") != summed:
        contest.note += (
            f" The printed valid-ballots line is {items.get('3'):,}, while the candidate lines and the Against all "
            f"line add up to {summed:,}."
        )
    return contest


def election_2025() -> Election:
    day = date(2025, 11, 30)
    page = raw.cached(SOURCE, RESULTS_2025)
    links = re.findall(r'href="(/media/dastan/2025/1\d/\d+/[^"]+\.pdf)"', page.path.read_text())
    contests = sorted(
        (district_2025(BASE + link, day) for link in links), key=lambda contest: int(contest.key.split("-")[1])
    )
    return Election(
        "2025-11-30",
        day,
        None,
        "day",
        "parliamentary",
        "general",
        "2025 Kyrgyz snap parliamentary election",
        "Жогорку Кеңештин депутаттарын мөөнөтүнөн мурда шайлоо, 2025",
        [RESULTS_2025, *(BASE + link for link in links)],
        contests,
    )


# Packages.


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{election.id}" for election in elections())]


def parse() -> None:
    geo().write()
    registry().write()
    for election in elections():
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Kyrgyzstan's nine regions and cities of republican status, its 36 single-member and 30 multi-member "
        "parliamentary districts, and the territorial election commissions' scheme, as the Central Commission names "
        "them in its results",
    )
    p.external |= {KYRGYZSTAN, COUNTRY}
    for election in elections():
        for url in election.sources:
            p.uses(raw.cached(SOURCE, url))
    for code, rank, kind, noun in (
        ("kg.oblast", 1, "administrative", "region"),
        ("kg.tik", 2, "electoral_district", "territorial election commission"),
        ("kg.okrug", 1, "electoral_district", "single-member district"),
        ("kg.mmd", 1, "electoral_district", "multi-member district"),
    ):
        p.add("scheme", code=code, polity_id=KYRGYZSTAN, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, (english, native) in REGIONS.items():
        id = area("kg.oblast", code)
        p.add("area", id=id, scheme_code="kg.oblast")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, english, "en", "official")
        p.name(id, native, "ky", "native", False)
    districts: dict[tuple[str, str], str] = {}
    for election in elections():
        for contest in election.contests:
            if contest.district is not None:
                districts[(contest.district[0], contest.district[1])] = contest.district[2]
    for (scheme, code), name in sorted(districts.items(), key=lambda item: (item[0][0], int(item[0][1]))):
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "ky", "official")
    return p


def party_key(label: str) -> str:
    quoted = re.search(r"[«“\"]([^»”\"]+)[»”\"]", label)
    text = quoted.group(1) if quoted else label
    text = re.sub(r"ПОЛИТИЧЕСКАЯ ПАРТИЯ|САЯСИЙ ПАРТИЯСЫ", "", text.upper())
    return re.sub(r"[^\w]", "", text)


def party(label: str) -> str:
    return key("party", party_key(label))


RULES = {
    "president": (
        date(2011, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate who wins more than half of the votes in the first round is elected; otherwise the two "
            "leading candidates go to a second round."
        ),
    ),
    "fptp": (
        date(2021, 1, 1),
        None,
        {"formula": "fptp"},
        (
            "Thirty-six members of the Supreme Council are elected in single-member districts, each by a plurality "
            "of votes."
        ),
    ),
    "referendum": (
        date(2010, 1, 1),
        None,
        {"formula": "referendum"},
        "A question is decided by the votes cast for each answer.",
    ),
}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Supreme Council, the presidency, their electoral systems, and the parties on the ballots in the Central "
        "Commission's results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {KYRGYZSTAN, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Кыргыз Республикасынын шайлоо жана референдум өткөрүү боюнча борбордук комиссиясы",
        title="Open data and results of elections and referendums",
        url=f"{BASE}/kg/otkrytye-dannye-cik/",
        license="Published by the Central Commission as open data; no license is stated",
        license_url=f"{BASE}/kg/otkrytye-dannye-cik/",
        attribution="Central Commission for Elections and Referenda of the Kyrgyz Republic, shailoo.gov.kg",
    )
    for election in elections():
        for url in election.sources:
            p.uses(raw.cached(SOURCE, url))
    p.add("body", id=PARLIAMENT, polity_id=KYRGYZSTAN, kind="unicameral")
    p.name(PARLIAMENT, "Supreme Council", "en", "official")
    p.name(PARLIAMENT, "Жогорку Кеңеш", "ky", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=KYRGYZSTAN, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Kyrgyz Republic", "en", "official")
    p.name(PRESIDENCY, "Кыргыз Республикасынын Президенти", "ky", "native", False)
    for name, (since, until, rules, method) in RULES.items():
        id = SYSTEMS[name]
        p.add(
            "electoral_system",
            id=id,
            polity_id=KYRGYZSTAN,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    parties: dict[str, tuple[str, str]] = {}
    for election in elections():
        for contest in election.contests:
            for option in contest.options:
                if option.kind == "list":
                    parties.setdefault(party_key(option.label), (option.label, contest.lang))
    for code, (label, lang) in sorted(parties.items()):
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=KYRGYZSTAN)
        p.add("external_id", entity_id=id, scheme="kg.shailoo.party", value=code)
        p.name(id, label, lang, "official")
    return p


def build(election: Election) -> Package:
    p = Package(
        f"{SOURCE}/{election.id}",
        SOURCE,
        f"The {election.name_en[:4]} {election.name_en[5:].lower()} at the finest unit the Central Commission publishes",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {KYRGYZSTAN, COUNTRY}
    for code, reason in election.accepted.items():
        p.accept(code, reason)
    for url in election.sources:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    id = key("election", election.id)
    p.add(
        "election",
        id=id,
        polity_id=KYRGYZSTAN,
        kind=election.kind,
        date_from=election.day,
        date_to=election.day_to,
        date_precision=election.precision,
        original_text=None,
        status=election.status,
    )
    path = f"kg/{election.id}-{election.family}" if election.precision == "day" else f"kg/{election.id}"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, election.name_en, "en", "official")
    p.name(id, election.name_local, "ky", "native", False)
    if election.note:
        p.add("note", id=key("note", id), entity_id=id, kind="caveat", body_md=election.note, lang="en")
    units: set[str] = set()
    for contest in election.contests:
        add_contest(p, election, id, path, contest, units)
    return p


def add_contest(p: Package, election: Election, election_id: str, path: str, contest: Contest, units: set[str]) -> None:
    contest_id = key("contest", election.id, contest.key)
    p.add(
        "contest",
        id=contest_id,
        election_id=election_id,
        kind=contest.kind,
        office_id=PRESIDENCY if contest.key == "president" else None,
        body_id=PARLIAMENT if contest.kind in ("list_allocation", "body_seats") else None,
        district_area_id=area(contest.district[0], contest.district[1]) if contest.district else COUNTRY,
        magnitude=contest.magnitude,
        round_number=1 if contest.kind == "office" else None,
        electoral_system_id=contest.system,
        default_result_kind="counted",
    )
    p.name(contest_id, contest.name_en, "en", "official")
    p.name(contest_id, contest.name_local, "ky", "native", False)
    p.add("slug", path=f"{path}/{contest.key}", entity_id=contest_id, is_current=True)
    if contest.question:
        p.add("ballot_question", contest_id=contest_id, type=contest.question)
    result_set = key("result_set", election.id, contest.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest_id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status=contest.status,
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=NOTE_ACCOUNTING + (f" {contest.note}" if contest.note else ""),
        lang="en",
    )
    options: dict[int, str] = {}
    for option in contest.options:
        option_id = options[option.pos] = key("option", election.id, contest.key, option.pos)
        p.add(
            "contest_option",
            id=option_id,
            contest_id=contest_id,
            kind=option.kind,
            party_id=party(option.label) if option.kind == "list" else None,
            ballot_position=option.pos,
            ballot_label=option.label,
        )
        if option.pos in contest.people:
            person = key("person", election.id, contest.key, option.pos)
            p.add("person", id=person)
            p.name(person, contest.people[option.pos], contest.lang, "official")
            p.add(
                "candidacy",
                id=key("candidacy", election.id, contest.key, option.pos),
                person_id=person,
                option_id=option_id,
                role="head",
                ballot_name=contest.people[option.pos],
            )
        if option.seats:
            p.add("outcome", result_set_id=result_set, option_id=option_id, kind="elected", seats=option.seats)
        elif option.pos in contest.elected:
            p.add("outcome", result_set_id=result_set, option_id=option_id, kind="elected")
    for unit in contest.units:
        unit_id = key("unit", unit.scheme, unit.code)
        if unit_id not in units:
            units.add(unit_id)
            if unit.region == ABROAD:
                relation, place = "unlocated", None
            elif unit.area is not None:
                relation, place = "equals", area(*unit.area)
            else:
                relation, place = "within", area("kg.oblast", unit.region or "")
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=KYRGYZSTAN,
                scheme_code=unit.scheme,
                code=unit.code,
                area_relation=relation,
                area_id=place,
                kind=unit.kind,
            )
        for pos, votes in unit.votes.items():
            if votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_id,
                    option_id=options[pos],
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for measure, value in unit.accounting.items():
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_id,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
    for measure, value in contest.declared.items():
        if value is None:
            continue
        if measure < 0:
            p.add("declared", result_set_id=result_set, option_id=options[-measure], measure=VOTES, value=value)
        else:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
