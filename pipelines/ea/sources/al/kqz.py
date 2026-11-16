"""Albania's parliamentary elections from the Central Election Commission (KQZ).

The Commission's results platform serves each election as JSON files: the national totals, the totals of each of the 12
counties (qarqe) with the seats declared, and the list votes at each voting center (QV). The 2025 files also print each
center's accounting and its place, and the votes cast abroad by county. The 2021 files hold the votes by center, and a
separate structure file holds the places and the voters registered at each center.
"""

import functools
import html
import json
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "al.kqz"
SITE = "https://kqz.gov.al"
ALBANIA = str(uuid5(package.NAMESPACE, "polity\x1fAL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fALB"))
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
ASSEMBLY = key("body", "assembly")
SYSTEM = key("electoral_system", "assembly")
INDEPENDENTS = 13


@dataclass(frozen=True)
class Election:
    day: date
    folder: str  # the results site's folder on iemis.kqz.gov.al
    files: tuple[str, ...]
    names: tuple[str, str]  # English, Albanian
    structure: bool  # the places and registered voters come from a separate structure file


ELECTIONS = [
    Election(
        date(2021, 4, 25),
        "results2021",
        ("json_all", "json_qarku", "json_qv", "json_strukture"),
        ("2021 Albanian parliamentary election", "Zgjedhjet për Kuvendin, 2021"),
        True,
    ),
    Election(
        date(2025, 5, 11),
        "results2025-finale",
        ("json_all", "json_qarku", "json_qv"),
        ("2025 Albanian parliamentary election", "Zgjedhjet për Kuvendin, 2025"),
        False,
    ),
]
RULES = {"formula": "dhondt", "threshold": {"percent": 1, "level": "list"}}
SYSTEM_NOTE = (
    "The 140 members of the Assembly are elected in the 12 counties by proportional representation from party and "
    "coalition lists. A list takes part in a county's allocation only with at least 1 percent of the county's valid "
    "votes, and the county's seats go to the lists by the D'Hondt highest-average method. These rules were fitted to "
    "the seats the Commission declared in 2021 and 2025 and reproduce them in every county."
)
METHOD = (
    "List votes by voting center (QV) as the Commission's results platform publishes them. Each county is one "
    "contest. The Commission declares the votes and seats of each county, and those figures are stored as declared."
)
METHOD_2025 = (
    " Registered voters, voters, valid votes, and invalid ballots are printed for each center. The voters figure is "
    "the Commission's count of electors at counted centers who voted; it is stored only where it is printed above "
    "zero and does not always equal valid plus invalid ballots. Votes cast from abroad are reported by county, not by "
    "center, so each county has one aggregate unit for them. Seven centers had not reported in the final data; only "
    "their registered voters are stored."
)
METHOD_2021 = (
    " The platform prints the registered voters of each center in a separate structure file, and no valid, invalid, "
    "or voter counts by center. Twenty-nine centers with votes carry codes that the structure file does not list; "
    "each is placed in the one county whose declared totals it completes exactly, since the county totals leave "
    "just these votes unexplained and only that assignment closes every county and every list. Forty-two centers the "
    "structure file lists have no votes in the votes file; their registered voters are stored."
)


def url(e: Election, name: str) -> str:
    return f"https://iemis.kqz.gov.al/{e.folder}/input/results/{name}.txt"


def place_file(e: Election) -> str:
    return "json_strukture" if e.structure else "json_qv"


def package_key(e: Election) -> str:
    return f"{SOURCE}/{e.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS)]


def _data(path: Path) -> None:
    if not path.read_bytes().lstrip().startswith(b"{"):
        raise ValueError(f"{path.name} is not JSON data (a challenge page?)")


def fetch() -> None:
    for e in ELECTIONS:
        for name in e.files:
            for attempt in range(6):
                try:
                    raw.fetch(SOURCE, url(e, name), suffix=".json", check=_data)
                    break
                except (ValueError, httpx.HTTPError) as failure:
                    print(f"{url(e, name)}: {failure}; attempt {attempt + 1}", flush=True)
                    time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"{url(e, name)} could not be fetched")


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


# Reading the files.


def clean(text: object) -> str:
    return " ".join(html.unescape(str(text if text is not None else "")).replace("\xa0", " ").split())


def fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", clean(text)).upper().split())


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def title(text: str) -> str:
    return " ".join(word.capitalize() for word in fold(text).split())


Rows = list[dict[str, Any]]


def load(e: Election, name: str) -> dict[str, Any]:
    data = raw.cached(SOURCE, url(e, name)).path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return json.loads(text)


def blocks(e: Election, name: str) -> list[Rows]:
    return [cast(Rows, block["data"]) for block in load(e, name)["data_main"]]


def number(value: object) -> int:
    text = clean(value).replace(",", "")
    return int(text) if re.fullmatch(r"-?\d+", text) else 0


def town(text: str) -> str:
    """A municipality's name without the word Bashkia that the 2021 structure prints before it."""
    return re.sub(r"^BASHKIA ", "", fold(text))


@dataclass
class Unit:
    code: str
    qarku: str
    bashkia: str | None = None
    njesia: str | None = None
    registered: int | None = None
    voters: int | None = None
    valid: int | None = None
    invalid: int | None = None
    reported: bool = True
    votes: dict[int, int] = field(default_factory=dict)


@dataclass
class Qarku:
    votes: dict[int, int] = field(default_factory=dict)  # position -> votes declared, home and abroad
    seats: dict[int, int] = field(default_factory=dict)
    abroad: dict[int, int] = field(default_factory=dict)
    accounting: dict[int, int] = field(default_factory=dict)  # measure -> declared
    abroad_accounting: dict[int, int] = field(default_factory=dict)


@dataclass
class Data:
    units: dict[str, Unit]
    parties: dict[int, tuple[str, str]]  # position -> (abbreviation, name)
    qarqe: dict[str, Qarku]
    national: dict[int, int]  # position -> votes
    national_seats: dict[int, int]
    national_accounting: dict[int, int]
    placed: dict[str, str] = field(default_factory=dict)  # code -> county, for the units placed by reconciliation


def structure(e: Election) -> tuple[dict[str, tuple[str, str, str, int]], dict[str, int]]:
    """The 2021 structure: each center's (county, municipality, unit, registered), and each county's registered."""
    centers: dict[str, tuple[str, str, str, int]] = {}
    totals: dict[str, int] = {}

    def walk(node: dict[str, Any], path: list[str]) -> None:
        for child in node.get("child", []):
            if "qv" in child:
                centers[str(child["qv"])] = (path[0], path[1], path[2], number(child["v3"]))
            else:
                name = child.get("bashkia") or child.get("njesi") or child.get("qarku")
                walk(child, [*path, town(str(name)) if "bashkia" in child else fold(str(name))])

    for top in load(e, "json_strukture")["data"]:
        if (
            "qarku" in top
            and "Gjitha" not in html.unescape(str(top["qarku"]))
            and "GJITHA" not in fold(str(top["qarku"]))
        ):
            name = fold(str(top["qarku"]))
            totals[name] = number(top["v3"])
            walk(top, [name])
    return centers, totals


def reconcile(
    votes: dict[str, dict[int, int]],
    known: set[str],
    declared: dict[str, dict[int, int]],
    got: dict[str, dict[int, int]],
) -> dict[str, str]:
    """Place the centers the structure does not list in the one county whose totals they complete exactly."""
    rest = sorted((c for c in votes if c not in known), key=lambda c: (-sum(votes[c].values()), c))
    positions = sorted({p for v in votes.values() for p in v})
    need = {q: [declared[q].get(p, 0) - got[q].get(p, 0) for p in positions] for q in declared}
    vector = {c: [votes[c].get(p, 0) for p in positions] for c in rest}
    found: list[dict[str, str]] = []

    def search(i: int, left: dict[str, list[int]], chosen: dict[str, str]) -> None:
        if len(found) > 1:
            return
        if i == len(rest):
            if all(not any(v) for v in left.values()):
                found.append(dict(chosen))
            return
        for q, target in left.items():
            after = [t - v for t, v in zip(target, vector[rest[i]], strict=True)]
            if all(x >= 0 for x in after):
                chosen[rest[i]] = q
                search(i + 1, {**left, q: after}, chosen)
                del chosen[rest[i]]

    search(0, need, {})
    if len(found) != 1:
        raise ValueError(f"{len(found)} placements of the unlisted centers close the county totals; expected 1")
    return found[0]


@functools.cache
def read(e: Election) -> Data:
    if e.day.year >= 2025:
        return read_2025(e)
    return read_2021(e)


def national_votes(rows: Rows, *, total: str = "v5") -> tuple[dict[int, int], dict[int, int]]:
    votes: dict[int, int] = defaultdict(int)
    seats: dict[int, int] = defaultdict(int)
    for row in rows:
        position = number(row["v2"])
        votes[position] += number(row[total])
        seats[position] += number(row.get("v6"))
    return dict(votes), {k: v for k, v in seats.items() if v}


def read_2025(e: Election) -> Data:
    main = blocks(e, "json_qv")
    units: dict[str, Unit] = {}
    for row in main[0]:
        code = clean(row["v5"])
        units[code] = Unit(
            code,
            fold(str(row["v1"])),
            town(str(row["v2"])),
            fold(str(row["v3"])),
            number(row["v6"]),
            number(row["v8"]) or None,
            number(row["v10"]),
            number(row["v11"]),
            number(row["v12"]) == 1,
        )
    parties: dict[int, tuple[str, str]] = {}
    totals = blocks(e, "json_all")
    for row in totals[1]:
        parties[number(row["v2"])] = (clean(row["v3"]), clean(row["v4"]))
    for row in main[1]:
        code = clean(row["v1"])
        if code not in units:
            raise ValueError(f"{code} has votes and no place")
        if units[code].reported:
            units[code].votes[number(row["v2"])] = number(row["v5"])
    qarqe: dict[str, Qarku] = {}
    block = blocks(e, "json_qarku")
    for row in block[0]:
        q = qarqe.setdefault(fold(str(row["v1"])), Qarku())
        q.accounting = {
            REGISTERED: number(row["v6_2"]),
            VALID: number(row["v10_2"]),
            INVALID: number(row["v11_2"]),
        }
        q.abroad_accounting = {VALID: number(row["v10_1"]), INVALID: number(row["v11_1"])}
    for row in block[1]:
        q = qarqe[fold(str(row["v1"]))]
        position = number(row["v2"])
        q.votes[position] = number(row["v5_2"])
        q.abroad[position] = number(row["v5_1"])
        if number(row["v6"]):
            q.seats[position] = number(row["v6"])
    national, seats = national_votes(totals[1], total="v5_2")
    head = totals[0][0]
    accounting = {
        REGISTERED: number(head["v6_2"]),
        VALID: number(head["v10_2"]),
        INVALID: number(head["v11_2"]),
    }
    return Data(units, parties, qarqe, national, seats, accounting)


def read_2021(e: Election) -> Data:
    centers, registered = structure(e)
    parties: dict[int, tuple[str, str]] = {}
    for row in load(e, "json_all")["data"]:
        position = number(row["v2"])
        if position != INDEPENDENTS:
            parties[position] = (clean(row["v3"]), clean(row["v4"]))
    parties[INDEPENDENTS] = ("", "Independent candidates")
    votes: dict[str, dict[int, int]] = defaultdict(dict)
    for row in load(e, "json_qv")["data"]:
        votes[clean(row["v1"])][number(row["v2"])] = number(row["v5"])
    qarqe: dict[str, Qarku] = {}
    for row in load(e, "json_qarku")["data"]:
        q = qarqe.setdefault(fold(str(row["v1"])), Qarku())
        position = number(row["v2"])
        q.votes[position] = q.votes.get(position, 0) + number(row["v5"])
        if number(row["v6"]):
            q.seats[position] = number(row["v6"])
    for name, total in registered.items():
        qarqe[name].accounting = {REGISTERED: total}
    units = {code: Unit(code, qarku, bashkia, njesia, reg) for code, (qarku, bashkia, njesia, reg) in centers.items()}
    got: dict[str, dict[int, int]] = {q: defaultdict(int) for q in qarqe}
    for code, by_position in votes.items():
        if code in units:
            units[code].votes = by_position
            for position, n in by_position.items():
                got[units[code].qarku][position] += n
    placed = reconcile(votes, set(units), {q: x.votes for q, x in qarqe.items()}, got)
    for code, qarku in placed.items():
        units[code] = Unit(code, qarku, votes=votes[code])
    national, seats = national_votes(load(e, "json_all")["data"])
    return Data(units, parties, qarqe, national, seats, {}, placed)


# Places.


def area(scheme: str, *parts: str) -> str:
    return key("area", scheme, *parts)


def places() -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    qarqe: set[str] = set()
    towns: set[tuple[str, str]] = set()
    units: set[tuple[str, str, str]] = set()
    for e in ELECTIONS:
        for unit in read(e).units.values():
            qarqe.add(unit.qarku)
            if unit.bashkia and unit.njesia:
                towns.add((unit.qarku, unit.bashkia))
                units.add((unit.qarku, unit.bashkia, unit.njesia))
    return qarqe, towns, units


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Albania's 12 counties (qarqe), the municipalities and administrative units named in the Central Election "
        "Commission's voting center lists, and its voting center scheme",
    )
    p.external |= {ALBANIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Election results by voting center",
        url=f"{SITE}/rezultate-zgjedhore",
        license="Official election results of the Republic of Albania, published for public use",
        license_url=f"{SITE}/",
        attribution="Central Election Commission of the Republic of Albania, kqz.gov.al",
    )
    for e in ELECTIONS:
        p.artifact(raw.cached(SOURCE, url(e, place_file(e))), SOURCE_ID)
    for scheme, rank, kind, noun in (
        ("al.qarku", 1, "administrative", "county"),
        ("al.bashkia", 2, "administrative", "municipality"),
        ("al.njesia", 3, "administrative", "administrative unit"),
        ("al.qv", 4, "precinct", "voting center"),
    ):
        p.add("scheme", code=scheme, polity_id=ALBANIA, kind=kind, rank=rank, noun=noun, code_system=None)
    qarqe, towns, units = places()
    for name in sorted(qarqe):
        id = area("al.qarku", name)
        p.add("area", id=id, scheme_code="al.qarku")
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, title(name), "sq", "official")
    for qarku, name in sorted(towns):
        id = area("al.bashkia", qarku, name)
        p.add("area", id=id, scheme_code="al.bashkia")
        p.add("area_code", area_id=id, code=f"{qarku}:{name}")
        p.add("area_parent", area_id=id, parent_id=area("al.qarku", qarku))
        p.name(id, title(name), "sq", "official")
    for qarku, town_name, name in sorted(units):
        id = area("al.njesia", qarku, town_name, name)
        p.add("area", id=id, scheme_code="al.njesia")
        p.add("area_code", area_id=id, code=f"{qarku}:{town_name}:{name}")
        p.add("area_parent", area_id=id, parent_id=area("al.bashkia", qarku, town_name))
        p.name(id, title(name), "sq", "official")
    return p


# Parties, the Assembly, and its electoral system.


def party(abbreviation: str) -> str:
    return key("party", fold(abbreviation))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Assembly of Albania, its electoral system, and the parties and coalitions on parliamentary ballots "
        "from the Central Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ALBANIA, COUNTRY}
    p.add("body", id=ASSEMBLY, polity_id=ALBANIA, kind="unicameral")
    p.name(ASSEMBLY, "Assembly of the Republic of Albania", "en", "official")
    p.name(ASSEMBLY, "Kuvendi i Republikës së Shqipërisë", "sq", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ALBANIA,
        valid_from=date(2021, 1, 1),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    names: dict[str, set[str]] = defaultdict(set)
    for e in ELECTIONS:
        for abbreviation, name in read(e).parties.values():
            if abbreviation:
                names[abbreviation].add(name)
    for abbreviation, spellings in sorted(names.items()):
        id = party(abbreviation)
        bloc = any(s.lower().startswith("koalicioni") for s in spellings)
        p.add("party", id=id, kind="bloc" if bloc else "party", polity_id=ALBANIA)
        p.add("external_id", entity_id=id, scheme="al.kqz.party", value=fold(abbreviation))
        for name in sorted(spellings):
            p.name(id, name, "sq", "official", name == max(spellings))
    return p


# One package per election day.


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def election(e: Election) -> Package:
    data = read(e)
    p = Package(
        package_key(e),
        SOURCE,
        f"The {e.names[0]} by voting center from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ALBANIA, COUNTRY}
    for name in e.files:
        if name == place_file(e):
            p.uses(raw.cached(SOURCE, url(e, name)))  # the geo package cites it
        else:
            p.artifact(raw.cached(SOURCE, url(e, name)), SOURCE_ID)
    day = e.day
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=ALBANIA,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, e.names[0], "en", "official")
    p.name(election_id, e.names[1], "sq", "native", False)
    path = f"al/{day}-parliamentary"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    method = METHOD + (METHOD_2021 if e.structure else METHOD_2025)
    options: dict[str, dict[int, str]] = {q: {} for q in data.qarqe}
    sets: dict[str, str] = {}
    for qarku, declared in sorted(data.qarqe.items()):
        contest = key("contest", day, qarku)
        result_set = sets[qarku] = key("result_set", day, qarku)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=ASSEMBLY,
            district_area_id=area("al.qarku", qarku),
            magnitude=sum(declared.seats.values()),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{e.names[0]}, {title(qarku)} County", "en", "official")
        p.name(contest, f"Zgjedhjet për Kuvendin, Qarku {title(qarku)}", "sq", "native", False)
        p.add("slug", path=f"{path}/{plain(qarku).replace(' ', '-')}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        for position, (abbreviation, name) in sorted(data.parties.items()):
            option = options[qarku][position] = key("option", day, qarku, position)
            independents = position == INDEPENDENTS
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="independents" if independents else "list",
                party_id=None if independents else party(abbreviation),
                ballot_position=position,
                ballot_label=name,
            )
        for position, seats in sorted(declared.seats.items()):
            p.add("outcome", result_set_id=result_set, option_id=options[qarku][position], kind="won", seats=seats)

    values = Facts()
    for unit in data.units.values():
        result_set = sets[unit.qarku]
        place = (
            area("al.njesia", unit.qarku, unit.bashkia, unit.njesia)
            if unit.bashkia and unit.njesia
            else area("al.qarku", unit.qarku)
        )
        id = key("unit", f"{day}:{unit.code}")
        p.add(
            "reporting_unit",
            id=id,
            polity_id=ALBANIA,
            scheme_code="al.qv",
            code=f"{day}:{unit.code}",
            area_relation="within",
            area_id=place,
            kind="standard",
        )
        values.add(result_set, id, None, REGISTERED, unit.registered)
        values.add(result_set, id, None, VOTERS, unit.voters)
        if unit.reported:
            values.add(result_set, id, None, VALID, unit.valid)
            values.add(result_set, id, None, INVALID, unit.invalid)
            for position, n in unit.votes.items():
                values.add(result_set, id, options[unit.qarku][position], VOTES, n)
    for qarku, declared in data.qarqe.items():
        result_set = sets[qarku]
        if declared.abroad:
            id = key("unit", f"{day}:abroad:{qarku}")
            p.add(
                "reporting_unit",
                id=id,
                polity_id=ALBANIA,
                scheme_code="al.qv",
                code=f"{day}:abroad:{qarku}",
                area_relation="within",
                area_id=area("al.qarku", qarku),
                kind="aggregate",
            )
            for position, n in declared.abroad.items():
                values.add(result_set, id, options[qarku][position], VOTES, n)
            for measure, n in declared.abroad_accounting.items():
                values.add(result_set, id, None, measure, n)
        for position, n in declared.votes.items():
            p.add("declared", result_set_id=result_set, option_id=options[qarku][position], measure=VOTES, value=n)
        for measure, n in declared.accounting.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
    p.frame("result_value", values.frame())
    compare(e, data)
    over = [
        u for u in data.units.values() if u.voters is not None and u.registered is not None and u.voters > u.registered
    ]
    if over:
        listed = ", ".join(f"{u.code} ({u.voters} voters, {u.registered} registered)" for u in over)
        p.accept(
            "accounting.turnout",
            f"The Commission's own results table prints more voters than registered voters at {listed}",
        )
    print(f"{package_key(e)}: {len(data.units)} centers, {len(data.qarqe)} counties", flush=True)
    return p


def compare(e: Election, data: Data) -> None:
    """The counties' declared totals against the national totals the Commission prints, and the centers against both."""
    seats = sum(sum(q.seats.values()) for q in data.qarqe.values())
    for position, n in data.national.items():
        total = sum(q.votes.get(position, 0) for q in data.qarqe.values())
        if total != n:
            raise ValueError(f"{e.day}: list {position} has {total} votes in the counties and {n} nationally")
    for position, n in data.national_seats.items():
        total = sum(q.seats.get(position, 0) for q in data.qarqe.values())
        if total != n:
            raise ValueError(f"{e.day}: list {position} has {total} seats in the counties and {n} nationally")
    for measure, n in data.national_accounting.items():
        total = sum(q.accounting.get(measure, 0) for q in data.qarqe.values())
        if total != n:
            raise ValueError(f"{e.day}: measure {measure} sums to {total} in the counties and {n} nationally")
    print(f"{e.day}: counties sum to the national votes and {seats} seats as printed", flush=True)
