"""Slovenian European Parliament elections of 2004 and 2009 by electoral district (volilni okraj), from the State
Election Commission's archived result sites (https://www.dvk-rs.si/arhivi/ep<year>/).

Each election is one package, `si.dvk/ep-<date>`, built on `si.dvk/geo` and `si.dvk/registry` from
`ea.sources.si.dvk`. Slovenia forms one electoral unit for the European Parliament, and the commission published
these elections no finer than the 88 electoral districts, so each district is a reporting unit that equals its
district area. The printed national figures are stored as the declared figures of the contest.
"""

import re
from dataclasses import dataclass, field
from datetime import date

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
    clean,
    key,
)
from ea.sources.si.dvk_okraji import _Tables, number, text

ARCHIVE = "https://www.dvk-rs.si/arhivi"


@dataclass(frozen=True)
class Year:
    day: date
    folder: str
    seats: int
    cast_label: str

    @property
    def year(self) -> int:
        return self.day.year

    def url(self, page: str) -> str:
        return f"{ARCHIVE}/{self.folder}/{page}.html"

    def urls(self) -> list[str]:
        pages = ["rez_sl", "rez_ve", "udel_vo", *(f"rez_vo{unit}" for unit in ELECTORAL_UNITS)]
        return [self.url(page) for page in pages]


YEARS = [
    Year(date(2004, 6, 13), "ep2004", 7, "št. preštetih glasovnic"),
    Year(date(2009, 6, 7), "ep2009", 7, "št. oddanih glasovnic"),
]


@dataclass
class Okraj:
    unit: int
    seat: int
    name: str
    registered: int | None = None
    voters: int | None = None
    cast: int = 0
    invalid: int = 0
    votes: dict[str, int] = field(default_factory=dict)

    @property
    def valid(self) -> int:
        return sum(self.votes.values())


@dataclass
class Parsed:
    year: Year
    lists: list[str]
    okraji: list[Okraj]
    national: dict[str, int]
    cast: int
    invalid: int
    units: dict[int, tuple[int, int, dict[str, int]]]
    specials: list[Okraj] = field(default_factory=list)
    """Polling places outside every district, which the commission prints as columns of their own."""


def fetch() -> None:
    for year in YEARS:
        for url in year.urls():
            raw.fetch(SOURCE, url, suffix=".html")


def _tables(url: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(text(raw.cached(SOURCE, url).path))
    return parser.tables


@dataclass
class Table:
    """A results table: its column names, the ballots cast and invalid in each column, and each list's votes."""

    names: list[str]
    cast: list[int]
    invalid: list[int]
    votes: dict[str, list[int]]


def _results(rows: list[list[str]], year: Year) -> Table:
    start = next(
        i for i, row in enumerate(rows) if row[:1] == [""] and len(row) > 2 and row[1] in ("skupaj", "Slovenija")
    )
    names = rows[start][1:]
    cast: list[int] = []
    invalid: list[int] = []
    votes: dict[str, list[int]] = {}
    for row in rows[start + 1 :]:
        label = clean(row[0])
        cells = [number(cell) for cell in row[1:]]
        if len(cells) != len(names):
            raise LookupError(f"{year.year}: {label} has {len(cells)} cells for {len(names)} columns")
        if label.lower() == year.cast_label:
            cast = cells
        elif label.lower().startswith("št. neveljavnih"):
            invalid = cells
        elif label and not label.startswith("Legenda"):
            votes[label] = cells
    if not cast or not invalid or not votes:
        raise LookupError(f"{year.year}: a results table lacks ballots or lists")
    return Table(names, cast, invalid, votes)


def _results_table(tables: list[list[list[str]]]) -> list[list[str]]:
    return next(
        t for t in tables if any(row[:1] == [""] and len(row) > 2 and row[1] in ("skupaj", "Slovenija") for row in t)
    )


def load(year: Year) -> Parsed:
    whole = _results(_results_table(_tables(year.url("rez_ve"))), year)
    names, cast, invalid = whole.names, whole.cast[0], whole.invalid[0]
    national = {name: cells[0] for name, cells in whole.votes.items()}
    if len(names) < 1 + len(ELECTORAL_UNITS):
        raise LookupError(f"{year.year}: unit table has columns {names}")
    columns_cast, columns_invalid, columns_votes = whole.cast, whole.invalid, whole.votes
    units = {
        unit: (columns_cast[unit], columns_invalid[unit], {name: cells[unit] for name, cells in columns_votes.items()})
        for unit in ELECTORAL_UNITS
    }
    specials = []
    for column in range(1 + len(ELECTORAL_UNITS), len(names)):
        special = Okraj(
            0,
            column - len(ELECTORAL_UNITS),
            clean(names[column]),
            cast=columns_cast[column],
            invalid=columns_invalid[column],
        )
        special.votes = {name: cells[column] for name, cells in columns_votes.items()}
        specials.append(special)
    printed = {
        clean(row[1]): number(row[2])
        for table in _tables(year.url("rez_sl"))
        for row in table
        if len(row) == 4 and row[0][:1].isdigit() and row[0].rstrip(".").isdigit()
    }
    if printed != national:
        raise ValueError(f"{year.year}: the national table {printed} disagrees with the unit table {national}")
    okraji: list[Okraj] = []
    for unit in ELECTORAL_UNITS:
        table = _results_table(_tables(year.url(f"rez_vo{unit}")))
        found_table = _results(table, year)
        district_names, cast_row, invalid_row, votes_row = (
            found_table.names,
            found_table.cast,
            found_table.invalid,
            found_table.votes,
        )
        for seat, name in enumerate(district_names[1:], start=1):
            found = re.match(r"VO\s+(\d+)\s*(.*)", name)
            if found and int(found.group(1)) != seat:
                raise LookupError(f"{year.year}: district {name} is out of order in unit {unit}")
            okraj = Okraj(
                unit, seat, clean(found.group(2) if found else name), cast=cast_row[seat], invalid=invalid_row[seat]
            )
            okraj.votes = {label: cells[seat] for label, cells in votes_row.items()}
            okraji.append(okraj)
    _turnout(year, okraji)
    return Parsed(year, list(national), okraji, national, cast, invalid, units, specials)


def _turnout(year: Year, okraji: list[Okraj]) -> None:
    tables = _tables(year.url("udel_vo"))
    blocks = [
        t
        for t in tables
        if len(t) in (12, 13) and t[0] and clean(t[0][0]).lower() in ("volilni okraj", "število volilnih upravičencev")
    ]
    if len(blocks) != len(ELECTORAL_UNITS):
        raise LookupError(f"{year.year}: found {len(blocks)} district turnout tables")
    for unit, block in zip(ELECTORAL_UNITS, blocks, strict=True):
        rows = block[-11:]
        here = [okraj for okraj in okraji if okraj.unit == unit]
        for okraj, row in zip(here, rows, strict=True):
            if clean(block[0][0]).lower() == "volilni okraj":
                okraj.voters, okraj.registered = number(row[1]), number(row[2])
            else:
                okraj.registered, okraj.voters = number(row[1]), number(row[2])


def reconcile(parsed: Parsed) -> dict[str, object]:
    problems: list[str] = []
    for unit in ELECTORAL_UNITS:
        here = [okraj for okraj in parsed.okraji if okraj.unit == unit]
        cast, invalid, votes = parsed.units[unit]
        for name, wanted, got in (
            ("cast", cast, sum(okraj.cast for okraj in here)),
            ("invalid", invalid, sum(okraj.invalid for okraj in here)),
        ):
            if wanted != got:
                problems.append(f"unit {unit} {name}: districts {got:,}, printed {wanted:,}")
        for name in parsed.lists:
            got = sum(okraj.votes[name] for okraj in here)
            if got != votes[name]:
                problems.append(f"unit {unit} {name}: districts {got:,}, printed {votes[name]:,}")
    for okraj in parsed.okraji:
        if okraj.cast - okraj.invalid != okraj.valid:
            problems.append(
                f"{okraj.unit}.{okraj.seat}: cast - invalid {okraj.cast - okraj.invalid} != votes {okraj.valid}"
            )
    for name, wanted in parsed.national.items():
        got = sum(votes[name] for _, _, votes in parsed.units.values()) + sum(s.votes[name] for s in parsed.specials)
        if got != wanted:
            problems.append(f"Slovenia {name}: units {got:,}, printed {wanted:,}")
    for name, wanted, got in (
        ("cast", parsed.cast, sum(c for c, _, _ in parsed.units.values()) + sum(s.cast for s in parsed.specials)),
        (
            "invalid",
            parsed.invalid,
            sum(i for _, i, _ in parsed.units.values()) + sum(s.invalid for s in parsed.specials),
        ),
    ):
        if wanted != got:
            problems.append(f"Slovenia {name}: units {got:,}, printed {wanted:,}")
    return {
        "districts": len(parsed.okraji),
        "cast": parsed.cast,
        "invalid": parsed.invalid,
        "valid": sum(parsed.national.values()),
        "problems": problems,
    }


def slug(year: Year) -> str:
    return f"si.dvk/ep-{year.day}"


def packages() -> list[str]:
    return [slug(year) for year in YEARS]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    for year in YEARS:
        parsed = load(year)
        print(f"{slug(year)}: {reconcile(parsed)}", flush=True)
        election(parsed).write()


def method(parsed: Parsed) -> str:
    return (
        "Published by the State Election Commission (Državna volilna komisija) on its archived results site for "
        f"the {parsed.year.year} election, by electoral district (volilni okraj), the finest level it published. "
        "Slovenia forms one electoral unit; each of the 88 districts is a reporting unit, numbered within its "
        "National Assembly electoral unit as the commission numbers them and tied to the district area of that "
        "number. Ballots cast are the ballots counted, valid votes are the votes for lists, and registered voters "
        "and voters come from the commission's turnout page. The national figures the commission prints are "
        "stored as the declared figures of the contest. The commission's pages print no preference votes or seats "
        "by list, so neither is stored here."
    )


def election(parsed: Parsed) -> Package:
    year = parsed.year
    p = Package(
        slug(year),
        SOURCE,
        f"The {year.year} European Parliament election in Slovenia by electoral district",
        depends=["si.dvk/geo", "si.dvk/registry"],
    )
    p.external.add(SLOVENIA)
    for url in year.urls():
        p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)
    election_id = key("election", "ep", year.day)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="general",
        date_from=year.day,
        date_to=year.day,
        status="held",
    )
    title = f"{year.year} European Parliament election in Slovenia"
    p.name(election_id, title, "en", "official")
    p.name(
        election_id,
        f"Volitve poslancev iz Republike Slovenije v Evropski parlament {year.year}",
        "sl",
        "native",
        False,
    )
    p.add("slug", path=f"si/{year.day}-european-parliament", entity_id=election_id, is_current=True)
    contest = key("contest", year.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        magnitude=year.seats,
        round_number=1,
        electoral_system_id=SYSTEMS["ep"],
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    p.add("slug", path=f"si/{year.day}-european-parliament/slovenia", entity_id=contest, is_current=True)
    result_set = key("result_set", str(year.day))
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
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method(parsed), lang="en"
    )
    options: dict[str, str] = {}
    for name in parsed.lists:
        party = key("party", year.day, name)
        p.add("party", id=party, kind="party", polity_id=SLOVENIA)
        p.name(party, name, "sl", "official")
        option = options[name] = key("option", year.day, name)
        p.add("contest_option", id=option, contest_id=contest, kind="list", party_id=party, ballot_label=name)
    for measure, value in ((3, parsed.cast), (5, parsed.invalid), (4, sum(parsed.national.values()))):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for name, option in options.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=parsed.national[name])
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    for okraj in [*parsed.okraji, *parsed.specials]:
        special = okraj.unit == 0
        code = f"{year.day}:{'special.' if special else ''}{okraj.unit}.{okraj.seat}"
        unit_id = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.volisce" if special else "si.vo",
            code=code,
            area_relation="unlocated" if special else "equals",
            area_id=None if special else area("si.vo", f"{okraj.unit}.{okraj.seat}"),
            kind="abroad_mission" if special else "standard",
        )
        p.name(unit_id, okraj.name, "sl", "official")
        for measure, value in (
            (2, okraj.registered),
            (14, okraj.voters),
            (3, okraj.cast),
            (5, okraj.invalid),
            (4, okraj.valid),
        ):
            if value is not None:
                sets.append(result_set)
                unit_ids.append(unit_id)
                option_ids.append(None)
                measures.append(measure)
                values.append(value)
        for name, option in options.items():
            sets.append(result_set)
            unit_ids.append(unit_id)
            option_ids.append(option)
            measures.append(1)
            values.append(okraj.votes[name])
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
