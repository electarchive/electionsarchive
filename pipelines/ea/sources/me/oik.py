import re
import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package

SOURCE = "me.oik"
SITE = "https://cik.me"
MONTENEGRO = str(uuid5(package.NAMESPACE, "polity\x1fME"))
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fcik"))
REGISTERED, VOTES, CAST, VALID, INVALID = 2, 1, 3, 4, 5
EXTERNAL = {MONTENEGRO, "me.bm", "me.opstina"}

PODGORICA = "Glavni grad Podgorica"
ZETA = "Zeta"

SYSTEM_RULES: dict[str, object] = {
    "formula": "dhondt",
    "threshold": {"level": "list", "scope": "contest", "percent": 3},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
SYSTEM_NOTE = (
    "Councilors are elected in the municipality as one district, by closed lists and the D'Hondt highest-quotient "
    "method among the lists that reach the qualifying quota of 3% of valid votes (Zakon o izboru odbornika i "
    "poslanika, articles 94 and 95). The law's special arrangements for lists of minority peoples are not modeled."
)
METHOD = (
    "Final results by polling station as published by the municipal election commission. Registered voters are the "
    "electoral roll (Upisano), ballots cast are all voters who voted (Ukupno glasalo, on the polling station and by "
    "letter), valid and invalid ballots are the commission's counts, and votes are the valid votes for each list. "
    "Declared list votes and seats are those the commission's minutes of the final results state for the municipality."
)


@dataclass(frozen=True)
class Election:
    key: str
    place: str
    day: date
    native_body: str
    english_body: str
    english: str
    native: str
    path: str
    files: tuple[str, ...]  # the by-station table first, then the minutes that state the lists and seats
    lists: tuple[tuple[str, int, int], ...]  # label, declared votes, declared seats


ELECTIONS = (
    Election(
        key="podgorica-2022-10-23",
        place=PODGORICA,
        day=date(2022, 10, 23),
        native_body="Skupština Glavnog grada Podgorice",
        english_body="Assembly of the Capital City of Podgorica",
        english="2022 Podgorica municipal election",
        native="Izbori za odbornike u Skupštinu Glavnog grada Podgorice 2022",
        path="me/2022-10-23-local-podgorica",
        files=(
            f"{SITE}/images/PODGORICA/Izbori/SO/2022/KonacniRezultatiPoBM20221023.pdf",
            f"{SITE}/images/PODGORICA/Izbori/SO/2022/KonacniIzvjestaj20221023.pdf",
        ),
        lists=(
            ("SNP – Temeljno za Podgoricu mr Nebojša Vuksanović", 2719, 0),
            ("СВЕТОСАВСКА СРПСКА ЛИСТА – Саберимо се за Немањин град – мр Мирко Рмандић", 220, 0),
            ("POKRET EVROPA SAD!", 21388, 13),
            ("ALEKSA BEČIĆ – DEMOKRATE – UJEDINJENA CRNA GORA – DEMOS – IDEMO LJUDI!", 10628, 6),
            ("Демократски фронт – За будућност Подгорице", 17953, 11),
            (
                (
                    "dr Dritan Abazović – Može Podgorica – Ovo je naš grad! – Građanski pokret URA – CIVIS – Albanska Alternativa"
                ),
                6265,
                4,
            ),
            ("PREOKRET", 1807, 0),
            ("Pokret Svi za naš GRAD – dr Ivan Vuković", 37518, 24),
        ),
    ),
    Election(
        key="zeta-2022-10-23",
        place=ZETA,
        day=date(2022, 10, 23),
        native_body="Skupština opštine Zeta",
        english_body="Municipal Assembly of Zeta",
        english="2022 Zeta municipal election",
        native="Izbori za odbornike u Skupštinu opštine Zeta 2022",
        path="me/2022-10-23-local-zeta",
        files=(
            f"{SITE}/images/ZETA/Izbori/SO/2022/Rezultati-po-birackim-mjestima.pdf",
            f"{SITE}/images/ZETA/Izbori/SO/2022/ZAPISNIK-O-KONACNIM-R.pdf",
        ),
        lists=(
            ("DEMOKRATSKA CRNA GORA ALEKSA BEČIĆ – IDEMO LJUDI – IDEMO ZETA – DEMOKRATE", 1941, 7),
            ("DPS ZETA „PRAVA STVAR – DOKAZANO ZA ZETU“", 2334, 8),
            ("SNP Zeta SNP – Temeljno za Zetu – Slađana Kaluđerović", 385, 1),
            (
                (
                    "DEMOKRATSKI FRONT – ZA BUDUĆNOST ZETE (Demokratski front, Demokratska narodna partija, Nova srpska "
                    "demokratija, Pokret za promjene, Prava Crna Gora, Jugoslovenska narodna partija Crne Gore)"
                ),
                4506,
                16,
            ),
        ),
    ),
    Election(
        key="podgorica-2024-09-29",
        place=PODGORICA,
        day=date(2024, 9, 29),
        native_body="Skupština Glavnog grada Podgorice",
        english_body="Assembly of the Capital City of Podgorica",
        english="2024 Podgorica municipal election",
        native="Izbori za odbornike u Skupštinu Glavnog grada Podgorice 2024",
        path="me/2024-09-29-local-podgorica",
        files=(
            f"{SITE}/images/PODGORICA/PODGORICA_2024_09_29.xlsx",
            f"{SITE}/images/PODGORICA/IzvjestajKonacni20240929.pdf",
        ),
        lists=(
            ("VRIJEME JE ZA RED – STRANKA EVROPSKOG PROGRESA – DUŠKO MARKOVIĆ", 2504, 2),
            ("ДР ВУК КАДИЋ - ПОКРЕТ НАПРИЈЕД - ЗА ПОДГОРИЦУ И ПОРОДИЦУ", 2235, 0),
            ("EVROPSKI SAVEZ – BORIS MUGOŠA (SDP, SD, LP I GRAĐANI) – PODGORICA ZA PRIMJER", 4411, 3),
            ("I RIJEČJU I DJELOM! – dr Nermin Abdić", 24310, 19),
            ("PREOKRET – OVAKO VIŠE NE MOŽE – SRĐAN PERIĆ", 2704, 2),
            ("EVROPA SAD – DEMOKRATE – JOŠ JAČE – PROF. DR SAŠA MUJOVIĆ", 17672, 14),
            ("Podgorička lista – Andrej Milović", 515, 0),
            ("CRNOGORSKA EVROPSKA PARTIJA – NOVAK ADŽIĆ", 573, 0),
            ("ЗА БУДУЋНОСТ ПОДГОРИЦЕ - ГРАД СВИХ НАС", 16362, 13),
            ("Jakov Milatović – ZA BOLJU PODGORICU!", 8537, 6),
            ("Pokret podstanara Podgorica „Naš grad – naš dom“ – Nemanja Baošić", 226, 0),
            ("U AKCIJU – BUDI SVOJ! BIRAJ CGA!", 174, 0),
            ("„Podgorica svih nas“ – Bošnjačka stranka – mr Ervin Ibrahimović", 955, 0),
        ),
    ),
)


def fetch() -> None:
    for election in ELECTIONS:
        for url in election.files:
            raw.fetch(SOURCE, url, suffix=url.rsplit(".", 1)[1])


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in ELECTIONS)]


def parse() -> None:
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def body_id(place: str) -> str:
    return key("body", place)


def system_id() -> str:
    return key("electoral_system", "council-list-pr")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Montenegrin municipal assemblies and their list-proportional electoral system, for the results the municipal "
        "election commissions publish on cik.me",
    )
    p.external |= EXTERNAL
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Opštinske izborne komisije Crne Gore",
        title="Election results published by the municipal election commissions of Montenegro",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        attribution="Opštinske izborne komisije Crne Gore, cik.me",
    )
    for election in ELECTIONS:
        for url in election.files:
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for place, native, english in {(e.place, e.native_body, e.english_body) for e in ELECTIONS}:
        body = body_id(place)
        p.add("body", id=body, polity_id=MONTENEGRO, kind="council")
        p.name(body, english, "en", "official")
        p.name(body, native, "sr", "native", False)
    p.add(
        "electoral_system",
        id=system_id(),
        polity_id=MONTENEGRO,
        valid_from=date(1998, 1, 1),
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.add(
        "note", id=key("note", system_id()), entity_id=system_id(), kind="methodology", body_md=SYSTEM_NOTE, lang="en"
    )
    return p


@dataclass(frozen=True)
class Row:
    label: str
    acc: dict[int, int]
    votes: list[int]


def _pdf_rows(url: str, width: int) -> list[list[str]]:
    path = raw.cached(SOURCE, url).path
    text = subprocess.run(["pdftotext", "-raw", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pattern = re.compile(r"(\S+)((?: \d+){%d})" % width)
    return [[m.group(1), *m.group(2).split()] for line in text.splitlines() if (m := pattern.fullmatch(line.strip()))]


def _row(cells: list[str], lists: int, *, visual: bool) -> Row:
    """Columns: received, unused, used, coupons, stubs, [visual id], control sheet, invalid, valid, lists..., rolled,
    voted at the station, [by letter], total voted."""
    label, numbers = cells[0], [int(c) for c in cells[1:]]
    offset = 1 if visual else 0
    invalid, valid = numbers[6 + offset], numbers[7 + offset]
    first = 8 + offset
    votes = numbers[first : first + lists]
    registered = numbers[first + lists]
    cast = numbers[-1]
    return Row(label, {REGISTERED: registered, CAST: cast, VALID: valid, INVALID: invalid}, votes)


def table(election: Election) -> list[list[str]]:
    url = election.files[0]
    n = len(election.lists)
    if url.endswith(".xlsx"):
        book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
        frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None, dtypes="string").to_polars()
        return [[str(c).strip() for c in row] for row in frame.rows()[1:]]
    return _pdf_rows(url, 8 + n + 4)


def build(election: Election) -> Package:
    n = len(election.lists)
    visual = election.files[0].endswith(".xlsx")
    rows = table(election)
    total, stations = rows[0], rows[1:]
    parsed = [_row(cells, n, visual=visual) for cells in stations]
    official = _row(total, n, visual=visual)
    declared = {i: votes for i, (_, votes, _) in enumerate(election.lists)}
    assert [official.votes[i] for i in declared] == list(declared.values()), "declared votes differ from the total row"
    for measure in (REGISTERED, CAST, VALID, INVALID):
        assert sum(r.acc[measure] for r in parsed) == official.acc[measure], (election.key, measure)
    for i, votes in declared.items():
        assert sum(r.votes[i] for r in parsed) == votes, (election.key, i)

    p = Package(
        f"{SOURCE}/{election.key}",
        SOURCE,
        f"The {election.day.year} {election.place} municipal assembly election by polling station",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= EXTERNAL
    for url in election.files:
        p.uses(raw.cached(SOURCE, url))
    e = key("election", election.key)
    p.add(
        "election",
        id=e,
        polity_id=MONTENEGRO,
        kind="local",
        date_from=election.day,
        date_to=election.day,
        status="held",
    )
    p.name(e, election.english, "en", "official")
    p.name(e, election.native, "sr", "native", False)
    p.add("slug", path=election.path, entity_id=e, is_current=True)
    seats = sum(s for _, _, s in election.lists)
    contest = key("contest", election.key)
    p.add(
        "contest",
        id=contest,
        election_id=e,
        kind="list_allocation",
        body_id=body_id(election.place),
        magnitude=seats,
        round_number=1,
        electoral_system_id=system_id(),
        default_result_kind="counted",
    )
    p.name(contest, election.english_body, "en", "official")
    p.name(contest, election.native_body, "sr", "native", False)
    p.add("slug", path=f"{election.path}/council", entity_id=contest, is_current=True)
    result_set = key("result_set", election.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=e,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options = []
    for position, (label, votes, won) in enumerate(election.lists, 1):
        option = key("option", election.key, position)
        party = key("party", election.key, position)
        options.append(option)
        p.add("party", id=party, kind="list_vehicle", polity_id=MONTENEGRO)
        p.name(party, label, "sr", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=position,
            ballot_label=label,
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        if won:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
    for measure, value in official.acc.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    town = key("unit", election.key, "municipality")
    p.add(
        "reporting_unit",
        id=town,
        polity_id=MONTENEGRO,
        scheme_code="me.opstina",
        code=f"{election.day}:{election.place}",
        area_relation="unlocated",
        kind="aggregate",
    )
    p.name(town, election.place, "sr", "official")
    for row in parsed:
        unit = key("unit", election.key, row.label)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MONTENEGRO,
            scheme_code="me.bm",
            code=f"{election.day}:{election.place}/{row.label}",
            parent_id=town,
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, f"{election.place}, biračko mjesto {row.label}", "sr", "official")
        for measure, value in row.acc.items():
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
        for option, value in zip(options, row.votes, strict=True):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=value,
            )
    return p
