"""Belgrade City Assembly elections of 2022 and 2023, by polling station, from the City Electoral Commission.

The Commission publishes, for each election, its report of the total results and a table of every polling station.
The tables are PDFs whose text layer carries the figures; each station row gives the municipality (and, for 2022, its
code), the station number, registered voters, voters who came out, ballots in the box, invalid and valid ballots, and
the votes of every list in ballot order.
"""

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik

SOURCE = "rs.beograd"
BASE = "https://static.beograd.rs/Binary/"
SERBIA = rik.SERBIA
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    rik.VOTES,
    rik.REGISTERED,
    rik.CAST,
    rik.VALID,
    rik.INVALID,
    rik.VOTERS,
)
REGISTRY = f"{SOURCE}/registry"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)

# The Commission's own codes for the 17 city municipalities, as its 2022 table prints them.
MUNICIPALITIES = {
    "Барајево": "70092",
    "Вождовац": "70106",
    "Врачар": "70114",
    "Гроцка": "70122",
    "Звездара": "70149",
    "Земун": "70157",
    "Лазаревац": "70165",
    "Младеновац": "70173",
    "Нови Београд": "70181",
    "Обреновац": "70190",
    "Палилула": "70203",
    "Раковица": "70211",
    "Савски Венац": "70220",
    "Сопот": "70238",
    "Стари Град": "70246",
    "Чукарица": "70254",
    "Сурчин": "71293",
}


@dataclass(frozen=True)
class Election:
    slug: str
    day: date
    title: str
    native: str
    table: str  # the file number of the polling-station table
    report: tuple[str, ...]  # file numbers of the Commission's report and summaries
    coded: bool  # whether the table prints municipality codes
    lists: tuple[tuple[str, int], ...]  # (label, seats declared) in ballot order
    declared: dict[int, int]
    votes: tuple[int, ...]  # declared votes per list
    method: str


ELECTIONS = (
    Election(
        "2022-04-03",
        date(2022, 4, 3),
        "2022 Belgrade City Assembly election",
        "Избори за одборнике Скупштине града Београда 2022.",
        "5439",
        ("5436",),
        True,
        (
            ("АЛЕКСАНДАР ВУЧИЋ – Заједно можемо све", 48),
            ("ИВИЦА ДАЧИЋ–ПРЕМИЈЕР СРБИЈЕ, ТОМА ФИЛА–ГРАДОНАЧЕЛНИК БЕОГРАДА", 8),
            ("ДР ВОЈИСЛАВ ШЕШЕЉ – СРПСКА РАДИКАЛНА СТРАНКА", 0),
            ("МИЛИЦА ЂУРЂЕВИЋ СТАМЕНКОВСКИ - Српска странка Заветници", 4),
            ("Владета Јанковић – Уједињени за победу Београда", 26),
            ("ДР МИЛОШ ЈОВАНОВИЋ – НАДА ЗА БЕОГРАД", 7),
            ("МОРАМО - ЗА ДОБАР ГРАД - Не давимо Београд", 13),
            ("ДУШКО ВУЈОШЕВИЋ – БОРИС ТАДИЋ – АЈМО ЉУДИ", 0),
            ("НЕМАЊА ШАРОВИЋ - ЗА САВ НОРМАЛАН СВЕТ", 0),
            ("СУВЕРЕНИСТИ", 0),
            ("Бошко Обрадовић - Српски покрет ДВЕРИ - ПОКС", 4),
            ("РУСКИ МАЊИНСКИ САВЕЗ ЗА БЕОГРАД", 0),
        ),
        {REGISTERED: 1600462, VOTERS: 925898, CAST: 916205, INVALID: 19155, VALID: 897050},
        (348345, 64050, 14674, 32029, 195335, 57760, 99078, 26219, 5239, 19544, 30898, 3879),
        "Held on 3 April 2022 and repeated at four polling stations on 16 April and two on 21 April; the Commission's "
        "table gives one row per station with the repeated votes included. Counted by the polling board at each "
        "polling station and tabulated by the City Electoral Commission. Registered voters are the extract of the "
        "voter register, voters are those who came out, ballots cast are the ballots found in the box, and valid and "
        "invalid are the ballots counted as such. Seats are as the Commission declared them in its report of results.",
    ),
    Election(
        "2023-12-17",
        date(2023, 12, 17),
        "2023 Belgrade City Assembly election",
        "Избори за одборнике Скупштине града Београда 2023.",
        "6738",
        ("6733", "6736", "6737"),
        False,
        (
            ("АЛЕКСАНДАР ВУЧИЋ – Београд не сме да стане", 49),
            ("ИВИЦА ДАЧИЋ-ПРЕМИЈЕР СРБИЈЕ, ТОМА ФИЛА-ГРАДОНАЧЕЛНИК БЕОГРАДА", 5),
            ("СРБИЈА ПРОТИВ НАСИЉА – ДОБРИЦА ВЕСЕЛИНОВИЋ – ПРОФ. ДР ВЛАДИМИР ОБРАДОВИЋ", 43),
            ("МИЛИЦА ЂУРЂЕВИЋ СТАМЕНКОВСКИ - БОШКО ОБРАДОВИЋ - НАЦИОНАЛНО ОКУПЉАЊЕ - ДРЖАВОТВОРНА СНАГА", 0),
            ("ДР МИЛОШ ЈОВАНОВИЋ – НАДА ЗА БЕОГРАД", 7),
            ("РУСКА СТРАНКА – СЛОБОДАН НИКОЛИЋ", 0),
            ("НАРОДНА СТРАНКА-СИГУРАН ИЗБОР. ОЗБИЉНИ ЉУДИ", 0),
            ("ДА СЕ ВОЈСКА НА КОСОВО ВРАТИ – МИША ВАЦИЋ", 0),
            ("ДЈБ - СДС -Отете бебе -Добро јутро Београде", 0),
            ("УСАМЕ ЗУКОРЛИЋ – ПРОМЕНА ЈЕ ПОБЕДА", 0),
            ("СРБИЈА НА ЗАПАДУ – ДА СЕ СТРУКА ПИТА", 0),
            ("Група грађана „ЋАЛЕ, ОВО ЈЕ ЗА ТЕБЕ“", 0),
            ("“МИ-ГЛАС ИЗ НАРОДА, Александар Јерковић“", 6),
            ("ЧЕДОМИР ЈОВАНОВИЋ – МОРА ДРУГАЧИЈЕ", 0),
        ),
        {REGISTERED: 1613369, VOTERS: 942345, CAST: 939710, INVALID: 20109, VALID: 919601},
        (367239, 44671, 325429, 24213, 56415, 3692, 19141, 2531, 14695, 1430, 2324, 4385, 50535, 2901),
        "Held on 17 December 2023 and repeated at three polling stations on 30 December; the Commission's table gives "
        "one row per station with the repeated votes included. Counted by the polling board at each polling station "
        "and tabulated by the City Electoral Commission. Registered voters are the extract of the voter register, "
        "voters are those who came out, ballots cast are the ballots found in the box, and valid and invalid are the "
        "ballots counted as such. Seats are as the Commission declared them in its report of results.",
    ),
)
FILES = ("5436", "5439", "6733", "6734", "6735", "6736", "6737", "6738")
LICENSE = "No license stated; published by the City Electoral Commission of Belgrade for public use"


def url(number: str) -> str:
    return BASE + number


def fetch() -> None:
    for number in FILES:
        raw.fetch(SOURCE, url(number), suffix=".pdf")


@dataclass(frozen=True)
class Station:
    municipality: str
    number: int
    figures: tuple[int, ...]  # registered, came out, in the box, invalid, valid, then the lists


def _tokens(path: Path) -> Iterator[str]:
    with pymupdf.open(path) as document:
        for page in document:
            for line in page.get_text("text").split("\n"):
                if line.strip():
                    yield line.strip()


def stations(election: Election) -> list[Station]:
    """Walk the table's text: a municipality's name, then the station's figures, as separate cells."""
    path = raw.cached(SOURCE, url(election.table)).path
    width = 5 + len(election.lists)
    tokens = list(_tokens(path))
    out: list[Station] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token not in MUNICIPALITIES or i + 1 >= len(tokens) or not tokens[i + 1].isdigit():
            i += 1
            continue
        if election.coded and (i == 0 or tokens[i - 1] != MUNICIPALITIES[token]):
            i += 1
            continue
        numbers: list[int] = []
        j = i + 1
        while j < len(tokens) and len(numbers) < width + 1:
            if tokens[j].isdigit():
                numbers.append(int(tokens[j]))
            elif re.match(r"^(Страна|Страна \d+ од \d+)", tokens[j]):
                pass
            elif numbers and not tokens[j].isdigit():
                break
            j += 1
        if len(numbers) != width + 1:
            raise ValueError(f"{election.slug}: short row for {token} at token {i}: {numbers}")
        out.append(Station(token, numbers[0], tuple(numbers[1:])))
        i = j
    return out


def opstina(code: str) -> str:
    return rik.area("rs.opstina", code)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The City Electoral Commission of Belgrade as a source of results",
    )
    p.external.add(SERBIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Градска изборна комисија",
        title="Results of the elections for the Belgrade City Assembly, City Electoral Commission of Belgrade",
        url="https://www.beograd.rs/",
        license=LICENSE,
        license_url=None,
        attribution="Градска изборна комисија, Град Београд",
    )
    for number in FILES:
        p.artifact(raw.cached(SOURCE, url(number)), SOURCE_ID)
    return p


def build(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.slug}",
        SOURCE,
        f"The {e.title[:4]} Belgrade City Assembly election by polling station from the City Electoral Commission",
        depends=[REGISTRY, f"{rik.SOURCE}/geo", f"{rik.SOURCE}/registry"],
    )
    # the body, electoral system, municipalities, and the scheme of polling stations are in the archive's
    # rs.rik packages
    p.external.add(SERBIA)
    for number in (e.table, *e.report):
        p.uses(raw.cached(SOURCE, url(number)))
    election = key("election", e.slug)
    path = f"rs/{e.day}-belgrade"
    p.add("election", id=election, polity_id=SERBIA, kind="general", date_from=e.day, date_to=e.day, status="held")
    p.name(election, e.title, "en", "official")
    p.name(election, e.native, "sr", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", e.slug)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=rik.BODIES["local"],
        magnitude=sum(seats for _, seats in e.lists),
        round_number=1,
        electoral_system_id=rik.SYSTEM_OF["local"],
        default_result_kind="counted",
    )
    p.name(contest, e.title, "en", "official")
    p.add("slug", path=f"{path}/assembly", entity_id=contest, is_current=True)
    result_set = key("result_set", e.slug)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=e.method, lang="en")
    options: list[str] = []
    for number, (label, seats) in enumerate(e.lists, 1):
        option = key("option", e.slug, number)
        options.append(option)
        p.add("contest_option", id=option, contest_id=contest, kind="list", ballot_position=number, ballot_label=label)
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
    for measure, value in e.declared.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, value in zip(options, e.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    rows: list[tuple[str, str | None, int, int]] = []
    sums: Counter[int] = Counter()
    by_list: Counter[str] = Counter()
    seen: set[str] = set()
    odd: list[str] = []
    table = stations(e)
    for s in table:
        code = f"{e.day}:{MUNICIPALITIES[s.municipality]}:{s.number}"
        if code in seen:
            raise ValueError(f"{e.slug}: polling station {code} appears twice")
        seen.add(code)
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SERBIA,
            scheme_code="rs.biracko_mesto",
            code=f"beograd:{code}",
            area_relation="within",
            area_id=opstina(MUNICIPALITIES[s.municipality]),
            kind="standard",
        )
        registered, came, cast, invalid, valid = s.figures[:5]
        for measure, value in (
            (REGISTERED, registered),
            (VOTERS, came),
            (CAST, cast),
            (INVALID, invalid),
            (VALID, valid),
        ):
            rows.append((unit, None, measure, value))
            sums[measure] += value
        if valid + invalid != cast:
            odd.append(f"{s.municipality} {s.number} (valid {valid} plus invalid {invalid} against {cast})")
        for option, votes in zip(options, s.figures[5:], strict=True):
            rows.append((unit, option, VOTES, votes))
            by_list[option] += votes
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": [result_set] * len(rows),
                "unit_id": [r[0] for r in rows],
                "option_id": [r[1] for r in rows],
                "channel": [0] * len(rows),
                "measure": [r[2] for r in rows],
                "count_round": [0] * len(rows),
                "flags": [0] * len(rows),
                "value": [r[3] for r in rows],
            },
            schema_overrides={"option_id": pl.String},
        ),
    )
    off = [f"measure {m} {sums[m]:,} against {v:,}" for m, v in e.declared.items() if sums[m] != v]
    off += [
        f"list {n} {by_list[o]:,} against {v:,}"
        for n, (o, v) in enumerate(zip(options, e.votes, strict=True), 1)
        if by_list[o] != v
    ]
    print(f"{e.slug}: {len(table):,} stations, registered {sums[REGISTERED]:,}, valid {sums[VALID]:,}; off: {off}")
    if off:
        raise ValueError(f"{e.slug}: station sums differ from the report: {off}")
    if odd:
        p.accept(
            "accounting.ballots_sum",
            f"{len(odd)} polling station(s) where the table's valid plus invalid ballots differ from the ballots in "
            f"the box; stored as printed: " + "; ".join(odd[:5]),
        )
    return p


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{e.slug}" for e in ELECTIONS)]


def parse() -> None:
    registry().write()
    for e in ELECTIONS:
        build(e).write()


def emit() -> None:
    package.run("load", packages())
