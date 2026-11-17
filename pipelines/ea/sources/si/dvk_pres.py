"""Slovenian presidential elections of 2007 and 2012 by electoral district (volilni okraj), from the State Election
Commission's archived result sites (https://www.dvk-rs.si/arhivi/vp<year>/).

Each round is one package, `si.dvk/pres-<date>`, built on `si.dvk/geo` and `si.dvk/registry` from
`ea.sources.si.dvk`. The commission published these elections no finer than the 88 electoral districts, so each
district is a reporting unit that equals its district area, and the contest is the single national presidential
contest. The printed national figures are stored as the declared figures of the contest.
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
    PRESIDENCY,
    SLOVENIA,
    SOURCE,
    SYSTEMS,
    area,
    bare,
    clean,
    key,
    title,
)
from ea.sources.si.dvk_okraji import _Tables, number, text

ARCHIVE = "https://www.dvk-rs.si/arhivi"
UNIT_NAMES = {
    1: "Kranj",
    2: "Postojna",
    3: "Ljubljana Center",
    4: "Ljubljana Bežigrad",
    5: "Celje",
    6: "Novo mesto",
    7: "Maribor",
    8: "Ptuj",
}


@dataclass(frozen=True)
class Round:
    """One round of a presidential election, published as its own set of pages."""

    day: date
    folder: str
    results: str
    turnout: str
    round: int
    note: str = ""

    @property
    def year(self) -> int:
        return self.day.year

    def url(self, page: str) -> str:
        return f"{ARCHIVE}/{self.folder}/{page}"

    def urls(self) -> list[str]:
        pages = [f"{self.results}/rezultati_slo.html", f"{self.results}/rezultati.html"]
        pages += [f"{self.results}/rez_ve{unit}.html" for unit in ELECTORAL_UNITS]
        pages += [f"{self.turnout}/udelezba.html", f"{self.turnout}/udelezba_okraji.html"]
        return [self.url(page) for page in pages]


ROUNDS = [
    Round(date(2007, 10, 21), "vp2007", "rezultati", "udelezba", 1),
    Round(date(2007, 11, 11), "vp2007", "rezultati2", "udelezba2", 2),
    Round(date(2012, 11, 11), "vp2012", "rezultati", "udelezba", 1),
    Round(date(2012, 12, 2), "vp2012", "rezultati2", "udelezba2", 2),
]


@dataclass
class Okraj:
    unit: int
    seat: int
    name: str
    registered: int | None = None
    voters: int | None = None
    cast: int | None = None
    invalid: int | None = None
    votes: dict[str, int] = field(default_factory=dict)

    @property
    def valid(self) -> int:
        return sum(self.votes.values())


@dataclass
class Parsed:
    round: Round
    candidates: list[str]
    okraji: list[Okraj]
    national: dict[str, int]
    """The printed national votes of each candidate."""
    cast: int
    invalid: int
    registered: int | None
    voters: int | None
    units: dict[int, tuple[int, int, dict[str, int]]] = field(default_factory=dict)
    """Each electoral unit's printed ballots cast, invalid ballots, and votes by candidate."""
    specials: list[Okraj] = field(default_factory=list)
    """Polling places outside every district, which the commission prints as columns of their own."""


def fetch() -> None:
    for found in ROUNDS:
        for url in found.urls():
            raw.fetch(SOURCE, url, suffix=".html")


def _tables(url: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(text(raw.cached(SOURCE, url).path))
    return parser.tables


def _count(cell: str) -> int:
    return number(cell)


def _national_votes(found: Round) -> dict[str, int]:
    rows = _tables(found.url(f"{found.results}/rezultati_slo.html"))[0]
    return {bare(row[0]): _count(row[1]) for row in rows[1:] if row[0]}


def _unit_columns(
    found: Round,
) -> tuple[int, int, dict[str, int], list[tuple[int, int, dict[str, int]]], list[Okraj]]:
    """The printed ballots cast, invalid ballots, and votes by candidate for Slovenia, for each unit, and for any
    polling places outside the districts."""
    rows = _tables(found.url(f"{found.results}/rezultati.html"))[0]
    head = rows[0]
    if head[:2] != ["", "Slovenija"] or len(head) < 2 + len(ELECTORAL_UNITS):
        raise LookupError(f"{found.day}: unexpected unit table header {head}")
    columns = list(range(1, len(head)))
    cast = [_count(rows[1][c]) for c in columns]
    invalid = [_count(rows[2][c]) for c in columns]
    votes: list[dict[str, int]] = [{} for _ in columns]
    for row in rows[3:]:
        if not row[0]:
            continue
        for slot, column in enumerate(columns):
            votes[slot][bare(row[0])] = _count(row[column])
    count = len(ELECTORAL_UNITS)
    units = [(cast[slot], invalid[slot], votes[slot]) for slot in range(1, 1 + count)]
    specials = []
    for slot in range(1 + count, len(columns)):
        special = Okraj(0, slot - count, clean(head[columns[slot]]), cast=cast[slot], invalid=invalid[slot])
        special.votes = votes[slot]
        specials.append(special)
    return cast[0], invalid[0], votes[0], units, specials


def _district_results(found: Round, unit: int) -> list[Okraj]:
    tables = _tables(found.url(f"{found.results}/rez_ve{unit}.html"))
    table = next(t for t in tables if any(row[:2] == ["", "skupaj"] for row in t))
    rows = table[next(i for i, row in enumerate(table) if row[:2] == ["", "skupaj"]) :]
    names = rows[0][2:]
    seats = [int(re.match(r"VO\s+(\d+)", name).group(1)) for name in names]  # type: ignore[union-attr]
    okraji = [
        Okraj(unit, seat, clean(re.sub(r"^VO\s+\d+\s*", "", name))) for seat, name in zip(seats, names, strict=True)
    ]
    for row in rows[1:]:
        label = row[0]
        cells = row[2:]
        if len(cells) != len(okraji):
            raise LookupError(f"{found.day} unit {unit}: {label} has {len(cells)} cells for {len(okraji)} districts")
        if label == "Št. preštetih glasovnic":
            for okraj, cell in zip(okraji, cells, strict=True):
                okraj.cast = _count(cell)
        elif label == "Št. neveljavnih glasovnic":
            for okraj, cell in zip(okraji, cells, strict=True):
                okraj.invalid = _count(cell)
        elif label:
            for okraj, cell in zip(okraji, cells, strict=True):
                okraj.votes[bare(label)] = _count(cell)
    return okraji


def _turnout(
    found: Round,
) -> tuple[dict[tuple[int, int], tuple[int, int]], int | None, int | None, dict[str, tuple[int | None, int]]]:
    """Registered voters and voters of each district, and the national registered voters and voters."""
    tables = _tables(found.url(f"{found.turnout}/udelezba_okraji.html"))
    blocks = [t for t in tables if t and t[0][:1] == ["Volilni okraj"] and len(t) in (12, 13)]
    if len(blocks) != len(ELECTORAL_UNITS):
        raise LookupError(f"{found.day}: found {len(blocks)} district turnout tables")
    heads = blocks[0][0]
    registered = heads.index("Število volilnih upravičencev")
    voters = heads.index("Udeležba")
    found_turnout: dict[tuple[int, int], tuple[int, int]] = {}
    for unit, block in zip(ELECTORAL_UNITS, blocks, strict=True):
        for seat, row in enumerate(block[-11:], start=1):
            found_turnout[(unit, seat)] = (_count(row[registered]), _count(row[voters]))
    national = _tables(found.url(f"{found.turnout}/udelezba.html"))
    total = next(row for t in national for row in t if row[:1] == ["glasovalo skupaj"])
    units = next(t for t in national if t and t[0][:1] == ["Volilna enota"])
    sheet = units[0]
    wanted = sheet.index("Število volilnih upravičencev")
    registered_total = sum(_count(row[wanted]) for row in units[1:] if row[0] in UNIT_NAMES.values())
    extra: dict[str, tuple[int | None, int]] = {}
    for table in tables:
        if table and table[0][:1] == ["Volilni okraj"] and len(table) == 3:
            for row in table[1:]:
                extra[row[0]] = (
                    None if row[registered].strip() == "/" else _count(row[registered]),
                    _count(row[voters]),
                )
    return found_turnout, registered_total, _count(total[1]), extra


def load(found: Round) -> Parsed:
    national = _national_votes(found)
    cast, invalid, votes, units, specials = _unit_columns(found)
    if votes != national:
        raise ValueError(f"{found.day}: the unit table disagrees with the national table")
    okraji: list[Okraj] = []
    for unit in ELECTORAL_UNITS:
        okraji.extend(_district_results(found, unit))
    if len(okraji) != 88:
        raise LookupError(f"{found.day}: {len(okraji)} districts")
    turnout, registered, voters, extra = _turnout(found)
    for okraj in okraji:
        okraj.registered, okraj.voters = turnout[(okraj.unit, okraj.seat)]
    for special in specials:
        wanted = "omnia" if "OMNIA" in special.name else "invalid"
        for name, (listed, present) in extra.items():
            if (wanted == "omnia") == ("OMNIA" in name):
                # The disabled-accessible places list registered voters who are also on a district's roll.
                special.registered, special.voters = (None if wanted == "invalid" else listed), present
    return Parsed(
        found,
        list(national),
        okraji,
        national,
        cast,
        invalid,
        registered,
        voters,
        dict(zip(ELECTORAL_UNITS, units, strict=True)),
        specials,
    )


def reconcile(parsed: Parsed) -> dict[str, object]:
    """Compare the district rows with the unit and national figures the commission prints."""
    problems: list[str] = []
    for unit in ELECTORAL_UNITS:
        here = [okraj for okraj in parsed.okraji if okraj.unit == unit]
        cast, invalid, votes = parsed.units[unit]
        for name, wanted, got in (
            ("cast", cast, sum(okraj.cast or 0 for okraj in here)),
            ("invalid", invalid, sum(okraj.invalid or 0 for okraj in here)),
        ):
            if wanted != got:
                problems.append(f"unit {unit} {name}: districts {got:,}, printed {wanted:,}")
        for candidate in parsed.candidates:
            got = sum(okraj.votes.get(candidate, 0) for okraj in here)
            if got != votes[candidate]:
                problems.append(f"unit {unit} {candidate}: districts {got:,}, printed {votes[candidate]:,}")
    for name, wanted, got in (
        ("cast", parsed.cast, sum(c for c, _, _ in parsed.units.values()) + sum(s.cast or 0 for s in parsed.specials)),
        (
            "invalid",
            parsed.invalid,
            sum(i for _, i, _ in parsed.units.values()) + sum(s.invalid or 0 for s in parsed.specials),
        ),
    ):
        if wanted != got:
            problems.append(f"Slovenia {name}: units {got:,}, printed {wanted:,}")
    for candidate, wanted in parsed.national.items():
        got = sum(cast_votes[candidate] for _, _, cast_votes in parsed.units.values())
        got += sum(special.votes[candidate] for special in parsed.specials)
        if got != wanted:
            problems.append(f"Slovenia {candidate}: units {got:,}, printed {wanted:,}")
    for okraj in [*parsed.okraji, *parsed.specials]:
        if okraj.cast is not None and okraj.invalid is not None and okraj.cast - okraj.invalid != okraj.valid:
            problems.append(
                f"{okraj.unit}.{okraj.seat}: cast - invalid {okraj.cast - okraj.invalid} != votes {okraj.valid}"
            )
    return {
        "districts": len(parsed.okraji),
        "cast": parsed.cast,
        "invalid": parsed.invalid,
        "valid": sum(parsed.national.values()),
        "registered": parsed.registered,
        "voters": parsed.voters,
        "district_voters": sum(okraj.voters or 0 for okraj in [*parsed.okraji, *parsed.specials]),
        "district_registered": sum(okraj.registered or 0 for okraj in [*parsed.okraji, *parsed.specials]),
        "problems": problems,
    }


def slug(found: Round) -> str:
    return f"si.dvk/pres-{found.day}"


def packages() -> list[str]:
    return [slug(found) for found in ROUNDS]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    for found in ROUNDS:
        parsed = load(found)
        print(f"{slug(found)}: {reconcile(parsed)}", flush=True)
        election(parsed).write()


def method(parsed: Parsed) -> str:
    return (
        "Published by the State Election Commission (Državna volilna komisija) on its archived results site for "
        f"the {parsed.round.year} election, by electoral district (volilni okraj), the finest level it published. "
        "Each of the 88 districts is a reporting unit, numbered within its electoral unit as the commission numbers "
        "them and tied to the district area of that number. Ballots cast are the ballots counted, valid votes are "
        "the votes for candidates, and registered voters and voters come from the commission's turnout page. The "
        "national figures the commission prints are stored as the declared figures of the contest, and the "
        "district rows sum to them. Voters at the any-station (OMNIA) polling places and those accessible to "
        "disabled voters are published only as turnout counts and are not part of any district's figures."
    )


def _outside(p: Package, parsed: Parsed, result_set: str, options: dict[str, str]) -> None:
    """Note the ballots the commission counts nationally that none of its published tables place."""
    rows = [*parsed.okraji, *parsed.specials]
    gaps = {
        "ballots cast": parsed.cast - sum(okraj.cast or 0 for okraj in rows),
        "invalid ballots": parsed.invalid - sum(okraj.invalid or 0 for okraj in rows),
        "voters": (parsed.voters or 0) - sum(okraj.voters or 0 for okraj in rows),
        "valid votes": sum(parsed.national.values()) - sum(okraj.valid for okraj in rows),
    }
    gaps = {name: gap for name, gap in gaps.items() if gap}
    if not gaps:
        return
    found = "; ".join(f"{gap:,} {name}" for name, gap in gaps.items())
    p.add(
        "note",
        id=key("note", result_set, "outside"),
        entity_id=result_set,
        kind="caveat",
        body_md=(
            "The national figures the commission prints exceed the sum of its district and special polling place "
            f"tables by {found}. It publishes no table that places these ballots, and does not say where "
            "they were cast; they appear only in the national totals, so the national figures are stored as "
            "declared and the unit rows are stored as printed, without a made-up remainder."
        ),
        lang="en",
    )
    p.accept(
        "accounting.declared",
        f"the commission's printed national total exceeds the sum of its published district tables by {found}: "
        "ballots not placed in any district or special polling place appear only in the national totals; both are "
        "stored as printed",
    )


def election(parsed: Parsed) -> Package:
    found = parsed.round
    first = next(r for r in ROUNDS if r.year == found.year and r.round == 1)
    p = Package(
        slug(found),
        SOURCE,
        f"The {found.year} Slovenian presidential election, {'first' if found.round == 1 else 'second'} round, "
        "by electoral district",
        depends=["si.dvk/geo", "si.dvk/registry", *([] if found.round == 1 else [slug(first)])],
    )
    p.external.add(SLOVENIA)
    for url in found.urls():
        p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)
    label = "first round" if found.round == 1 else "second round"
    election_id = key("election", "pres", found.day)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="general" if found.round == 1 else "runoff",
        date_from=found.day,
        date_to=found.day,
        status="held",
    )
    p.name(election_id, f"{found.year} Slovenian presidential election, {label}", "en", "official")
    p.name(
        election_id,
        f"Volitve predsednika republike {found.year}, {found.round}. krog",
        "sl",
        "native",
        False,
    )
    p.add("slug", path=f"si/{found.day}-presidential", entity_id=election_id, is_current=True)
    contest = key("contest", found.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=found.round,
        previous_round_id=None if found.round == 1 else key("contest", first.day),
        electoral_system_id=SYSTEMS["pres"],
        default_result_kind="counted",
    )
    p.name(contest, f"{found.year} Slovenian presidential election, {label}", "en", "official")
    p.add("slug", path=f"si/{found.day}-presidential/president", entity_id=contest, is_current=True)
    result_set = key("result_set", str(found.day))
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
    for index, name in enumerate(parsed.candidates, start=1):
        person = key("person", f"pres:{found.year}:{name}")
        option = options[name] = key("option", found.day, index, name)
        if found.round == 1:
            p.add("person", id=person)
            p.name(person, title(name), "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=index,
            ballot_label=title(name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", found.day, index, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=title(name),
        )
    for measure, value in (
        (2, parsed.registered),
        (14, parsed.voters),
        (3, parsed.cast),
        (5, parsed.invalid),
        (4, sum(parsed.national.values())),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for name, option in options.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=parsed.national[name])
    _outside(p, parsed, result_set, options)
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    for okraj in [*parsed.okraji, *parsed.specials]:
        special = okraj.unit == 0
        code = f"{found.day}:{'special.' if special else ''}{okraj.unit}.{okraj.seat}"
        unit_id = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.volisce" if special else "si.vo",
            code=code,
            area_relation="unlocated" if special else "equals",
            area_id=None if special else area("si.vo", f"{okraj.unit}.{okraj.seat}"),
            kind="aggregate" if special else "standard",
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
