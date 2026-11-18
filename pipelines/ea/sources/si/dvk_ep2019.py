"""The 2019 Slovenian European Parliament election by electoral district (volilni okraj), from the State Election
Commission's results site (https://www.dvk-rs.si/arhivi/ep2019/).

The site's `izvoz.xlsx` export holds a Podatki sheet of turnout and list votes for the nation, each of the 8 electoral
units, and each of the 88 districts, and a Kandidati sheet of each candidate's preference votes in each district.
The commission published no sheet by polling station for this election, so each district is a reporting unit that
equals its district area. The package is `si.dvk/ep-2019-05-26`, built on `si.dvk/geo` and `si.dvk/registry` from
`ea.sources.si.dvk`.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.si.dvk import (
    DVK_SOURCE,
    ELECTORAL_UNITS,
    SLOVENIA,
    SOURCE,
    SYSTEMS,
    area,
    bare,
    clean,
    count,
    grid,
    key,
    title,
)

DAY = date(2019, 5, 26)
SEATS = 8
URL = "https://www.dvk-rs.si/arhivi/ep2019/data/izvoz.xlsx"
LISTS = re.compile(r"\s*-\s*št\. glasov$")
HEAD = (
    "VE",
    "ENOTA",
    "VO",
    "OKRAJ",
    "Št. volilnih upr.",
    "Udeležba",
    "Odstotek udeležbe",
    "Št. oddanih glasovnic",
    "Št. veljavnih glasovnic",
    "Št. neveljavnih glasovnic",
    "Odstotek preštetih glasovnic",
)


@dataclass
class Area:
    unit: int
    seat: int
    name: str
    registered: int
    voters: int
    cast: int
    invalid: int
    valid: int
    lists: list[int]
    preferences: list[int] = field(default_factory=list)


@dataclass
class Candidate:
    party: int
    name: str
    elected: bool
    total: int


@dataclass
class Parsed:
    names: list[str]
    parties: list[str]
    candidates: list[Candidate]
    national: Area
    units: dict[int, Area]
    districts: list[Area]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix="xlsx")


def slug() -> str:
    return f"si.dvk/ep-{DAY}"


def packages() -> list[str]:
    return [slug()]


def emit() -> None:
    package.run("load", packages())


def _area(line: tuple[object, ...], positions: list[int], unit: int, seat: int, name: str) -> Area:
    numbers = [count(line[index]) for index in range(4, 10)]
    if any(value is None for value in numbers):
        raise ValueError(f"{unit}.{seat} lacks a turnout figure: {line[:10]}")
    registered, voters, _, cast, valid, invalid = (value or 0 for value in numbers)
    votes = [count(line[position]) for position in positions]
    if any(value is None for value in votes):
        raise ValueError(f"{unit}.{seat} lacks a list's votes")
    return Area(unit, seat, name, registered, voters, cast, invalid, valid, [value or 0 for value in votes])


def load() -> Parsed:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, URL).path))
    rows = grid(book, "Podatki")
    top = next(i for i, line in enumerate(rows) if line[0] == "VE")
    header = [clean(None if cell is None else str(cell)) for cell in rows[top]]
    if tuple(header[: len(HEAD)]) != HEAD:
        raise LookupError(f"{URL} has unexpected columns: {header[:12]}")
    positions = [i for i, cell in enumerate(header) if LISTS.search(cell)]
    names = [LISTS.sub("", header[i]) for i in positions]
    national: Area | None = None
    units: dict[int, Area] = {}
    districts: list[Area] = []
    for line in rows[top + 1 :]:
        if not any(cell not in (None, "") for cell in line):
            continue
        unit, seat = clean(str(line[0] or "")), clean(str(line[2] or ""))
        if not unit:
            national = _area(line, positions, 0, 0, "Slovenia")
        elif not seat:
            units[int(unit)] = _area(line, positions, int(unit), 0, clean(str(line[1])))
        else:
            districts.append(_area(line, positions, int(unit), int(seat), clean(str(line[3]))))
    if national is None or sorted(units) != list(ELECTORAL_UNITS) or len(districts) != 88:
        raise LookupError(f"{URL}: found {len(units)} units and {len(districts)} districts")
    parties, candidates = _candidates(grid(book, "Kandidati"), districts, units, national)
    if len(parties) != len(names):
        raise LookupError(f"{URL}: {len(names)} lists in Podatki, {len(parties)} in Kandidati")
    return Parsed(names, parties, candidates, national, units, districts)


def _candidates(
    rows: list[tuple[object, ...]], districts: list[Area], units: dict[int, Area], national: Area
) -> tuple[list[str], list[Candidate]]:
    heads = [clean(None if cell is None else str(cell)) for cell in rows[0]]
    if heads[:6] != [
        "Stranka",
        "Kandidat",
        "Mandat",
        "Preferenčni glas",
        "Indeks mandata",
        "Število preferenčnih glasov",
    ]:
        raise LookupError(f"{URL} has unexpected candidate columns: {heads[:7]}")
    where = {}
    wholes = {}
    for index, head in enumerate(heads):
        found = re.fullmatch(r"VE(\d+) VO(\d+)", head)
        if found:
            where[(int(found.group(1)), int(found.group(2)))] = index
        elif re.fullmatch(r"VE\d+", head):
            wholes[int(head[2:])] = index
    if sorted(wholes) != list(ELECTORAL_UNITS):
        raise LookupError(f"{URL}: the candidate sheet lacks a column for some electoral unit")
    if sorted(where) != sorted((d.unit, d.seat) for d in districts):
        raise LookupError(f"{URL}: the candidate columns do not match the districts")
    parties: list[str] = []
    candidates: list[Candidate] = []
    for line in rows[1:]:
        if not line[1]:
            continue
        party = clean(str(line[0]))
        if party not in parties:
            parties.append(party)
        total = count(line[5]) or 0
        candidates.append(Candidate(parties.index(party), clean(str(line[1])), bool(count(line[2])), total))
        national.preferences.append(total)
        for unit, column in wholes.items():
            units[unit].preferences.append(count(line[column]) or 0)
        for area_ in districts:
            area_.preferences.append(count(line[where[(area_.unit, area_.seat)]]) or 0)
    return parties, candidates


def outside(parsed: Parsed) -> list[Area]:
    """What each electoral unit and the nation print beyond the districts beneath them: ballots counted in the unit
    but not assigned to a district (postal and early votes), and ballots from outside the electoral units."""
    left: list[Area] = []
    for unit in ELECTORAL_UNITS:
        left.append(_minus(parsed.units[unit], [d for d in parsed.districts if d.unit == unit], unit, 0))
    left.append(_minus(parsed.national, list(parsed.units.values()), 0, 0))
    return left


def _minus(whole: Area, parts: list[Area], unit: int, seat: int) -> Area:
    def rest(wanted: list[int], got: list[list[int]]) -> list[int]:
        return [w - sum(column) for w, column in zip(wanted, zip(*got, strict=True), strict=True)]

    figures = rest(
        [whole.registered, whole.voters, whole.cast, whole.invalid, whole.valid],
        [[p.registered, p.voters, p.cast, p.invalid, p.valid] for p in parts],
    )
    return Area(
        unit,
        seat,
        "",
        *figures,
        rest(whole.lists, [p.lists for p in parts]),
        rest(whole.preferences, [p.preferences for p in parts]),
    )


def reconcile(parsed: Parsed) -> dict[str, object]:
    problems: list[str] = []
    for d in parsed.districts:
        if sum(d.lists) != d.valid:
            problems.append(f"{d.unit}.{d.seat}: list votes {sum(d.lists):,} != valid {d.valid:,}")
        if d.cast - d.invalid != d.valid:
            problems.append(f"{d.unit}.{d.seat}: cast - invalid != valid")
    for whole in (*parsed.units.values(), parsed.national):
        if sum(whole.lists) != whole.valid or whole.cast - whole.invalid != whole.valid:
            problems.append(f"{whole.unit or 'Slovenia'}: printed ballots do not add up")
    residual = outside(parsed)
    for left in residual:
        values = [left.registered, left.voters, left.cast, left.invalid, left.valid, *left.lists, *left.preferences]
        if min(values) < 0:
            problems.append(f"{left.unit or 'Slovenia'}: the districts exceed the printed total")
        if sum(left.lists) != left.valid or left.cast - left.invalid != left.valid:
            problems.append(f"{left.unit or 'Slovenia'}: the remainder does not add up")
    if sum(c.elected for c in parsed.candidates) != SEATS:
        problems.append("the elected candidates are not 8")
    return {
        "districts": len(parsed.districts),
        "cast": parsed.national.cast,
        "valid": parsed.national.valid,
        "outside_districts_cast": sum(left.cast for left in residual),
        "problems": problems,
    }


def parse() -> None:
    parsed = load()
    found = reconcile(parsed)
    print(f"{slug()}: {found}", flush=True)
    if found["problems"]:
        raise ValueError(f"{slug()}: {found['problems']}")
    election(parsed).write()


METHOD = (
    "Published by the State Election Commission (Državna volilna komisija) on its results site for the 2019 election "
    "as an export of figures by electoral district (volilni okraj), the finest level it published for this election. "
    "Slovenia forms one electoral unit; each of the 88 districts is a reporting unit, numbered within its National "
    "Assembly electoral unit as the commission numbers them and tied to the district area of that number. "
    "Registered voters are the electoral roll, voters are the turnout the commission prints, ballots cast are the "
    "ballots counted, and valid votes are the votes for lists. Preference votes are each candidate's votes in each "
    "district. The commission's national row is stored as the declared figures of the contest, and the candidates "
    "its Mandat column marks are stored as elected."
)


def election(parsed: Parsed) -> Package:
    p = Package(
        slug(),
        SOURCE,
        "The 2019 European Parliament election in Slovenia by electoral district",
        depends=["si.dvk/geo", "si.dvk/registry"],
    )
    p.external.add(SLOVENIA)
    p.artifact(raw.cached(SOURCE, URL), DVK_SOURCE)
    election_id = key("election", "ep", DAY)
    p.add("election", id=election_id, polity_id=SLOVENIA, kind="general", date_from=DAY, date_to=DAY, status="held")
    name = "2019 European Parliament election in Slovenia"
    p.name(election_id, name, "en", "official")
    p.name(election_id, "Volitve poslancev iz Republike Slovenije v Evropski parlament 2019", "sl", "native", False)
    p.add("slug", path=f"si/{DAY}-european-parliament", entity_id=election_id, is_current=True)
    contest = key("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        magnitude=SEATS,
        round_number=1,
        electoral_system_id=SYSTEMS["ep"],
        default_result_kind="counted",
    )
    p.name(contest, name, "en", "official")
    p.add("slug", path=f"si/{DAY}-european-parliament/slovenia", entity_id=contest, is_current=True)
    result_set = key("result_set", str(DAY))
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DVK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    lists: list[str] = []
    for index, label in enumerate(parsed.names):
        party = key("party", DAY, index + 1, label)
        p.add("party", id=party, kind="party", polity_id=SLOVENIA)
        p.name(party, parsed.parties[index], "sl", "official")
        p.name(party, label, "sl", "short", False)
        option = key("option", DAY, index + 1, label)
        lists.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=index + 1,
            ballot_label=label,
        )
    options: list[str] = []
    seen: dict[int, int] = {}
    won = [0] * len(lists)
    for index, candidate in enumerate(parsed.candidates):
        seen[candidate.party] = position = seen.get(candidate.party, 0) + 1
        parent = lists[candidate.party]
        person = key("person", f"ep:{DAY}:{candidate.name}")
        option = key("option", DAY, parent, position, candidate.name)
        candidacy = key("candidacy", DAY, parent, position, candidate.name)
        options.append(option)
        p.add("person", id=person)
        p.name(person, title(bare(candidate.name)), "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=parent,
            ballot_position=position,
            ballot_label=title(bare(candidate.name)),
        )
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=position,
            ballot_name=title(bare(candidate.name)),
        )
        if candidate.elected:
            won[candidate.party] += 1
            p.add("outcome", result_set_id=result_set, option_id=parent, candidacy_id=candidacy, kind="elected")
    for option, seats in zip(lists, won, strict=True):
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
    total = parsed.national
    for measure, value in (
        (2, total.registered),
        (14, total.voters),
        (3, total.cast),
        (5, total.invalid),
        (4, total.valid),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, value in zip(lists, total.lists, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=value)
    for option, candidate in zip(options, parsed.candidates, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=7, value=candidate.total)
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []

    def add(unit: str, option: str | None, measure: int, value: int) -> None:
        sets.append(result_set)
        unit_ids.append(unit)
        option_ids.append(option)
        measures.append(measure)
        values.append(value)

    for d in parsed.districts:
        code = f"{DAY}:{d.unit}.{d.seat}"
        unit_id = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.vo",
            code=code,
            area_relation="equals",
            area_id=area("si.vo", f"{d.unit}.{d.seat}"),
            kind="standard",
        )
        p.name(unit_id, d.name, "sl", "official")
        for measure, value in ((2, d.registered), (14, d.voters), (3, d.cast), (5, d.invalid), (4, d.valid)):
            add(unit_id, None, measure, value)
        for option, value in zip(lists, d.lists, strict=True):
            add(unit_id, option, 1, value)
        for option, value in zip(options, d.preferences, strict=True):
            add(unit_id, option, 7, value)
    for left in outside(parsed):
        nation = left.unit == 0
        code = f"{DAY}:outside" if nation else f"{DAY}:outside.{left.unit}"
        unit_id = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.volisce",
            code=code,
            area_relation="unlocated" if nation else "within",
            area_id=None if nation else area("si.ve", str(left.unit)),
            kind="aggregate",
        )
        p.name(
            unit_id,
            "Ballots from outside the electoral units" if nation else "Ballots counted in the unit but not by district",
            "en",
            "official",
        )
        for measure, value in (
            (2, left.registered),
            (14, left.voters),
            (3, left.cast),
            (5, left.invalid),
            (4, left.valid),
        ):
            if value:
                add(unit_id, None, measure, value)
        for option, value in zip(lists, left.lists, strict=True):
            add(unit_id, option, 1, value)
        for option, value in zip(options, left.preferences, strict=True):
            add(unit_id, option, 7, value)
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )
    return p
