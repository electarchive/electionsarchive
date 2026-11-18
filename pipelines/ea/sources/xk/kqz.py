"""Kosovo's 2025 local elections from the Central Election Commission (KQZ).

The Commission's results platforms for the 2025 local elections serve their data as JSON files on
storage.kqz-ks.org. For each contest type (municipal assembly and mayor) a "total" file holds the final count of every
polling station, conditional-vote station, special-needs station, and postal station, nested by municipality and
polling center, with each municipality's and the country's totals. Companion files name the municipalities, the
polling centers, the political entities, and their candidates with their gender. The mayoral runoff of 9 November
2025 has its own files with the suffix "part-2".

The Commission's main site, kqz-ks.org, serves its result archive behind a browser challenge. Its Assembly elections
are built from files saved in a browser, by the module `kuvendi`, into the same geo and registry packages.
The local elections of 2017 to 2023 are built by the module `local` from the files of the same archive.
"""

import functools
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "xk.kqz"
SITE = "https://kqz-ks.org"
STORAGE = "https://storage.kqz-ks.org"
KOSOVO = str(uuid5(package.NAMESPACE, "polity\x1fXK"))
VOTES, REGISTERED, VALID = 1, 2, 4
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
MAYOR_SYSTEM = key("electoral_system", "mayor")
ASSEMBLY_SYSTEM = key("electoral_system", "municipal_assembly")

FIRST = date(2025, 10, 12)
RUNOFF = date(2025, 11, 9)

# Round one: the final counts of both contests, the names of places, entities, and candidates.
ASSEMBLY = f"{STORAGE}/assembly-total-latest.json"
MAYOR = f"{STORAGE}/mayor-total-latest.json"
ENTITIES = f"{STORAGE}/political-entities.json"
CANDIDATES = f"{STORAGE}/full-candidates-gender.json"
CENTERS = f"{STORAGE}/total-metadata.json"
TYPES = f"{STORAGE}/qnr-metadata.json"
MUNICIPALITIES = f"{STORAGE}/metadata-round2.json"
# The mayoral runoff.
MAYOR_2 = f"{STORAGE}/mayor-total-latest-part-2.json"
ENTITIES_2 = f"{STORAGE}/political-entities-round2.json"
CANDIDATES_2 = f"{STORAGE}/full-candidates-gender-part-2.json"
CENTERS_2 = f"{STORAGE}/total-metadata-part-2.json"

URLS = (
    ASSEMBLY,
    MAYOR,
    ENTITIES,
    CANDIDATES,
    CENTERS,
    TYPES,
    MUNICIPALITIES,
    MAYOR_2,
    ENTITIES_2,
    CANDIDATES_2,
    CENTERS_2,
)
GEO_FILES = (MUNICIPALITIES, CENTERS, TYPES, CENTERS_2)

MAYOR_RULES = {"formula": "two_round", "majority": 0.5, "advance": 2}
ASSEMBLY_RULES = {"formula": "sainte_lague", "lists": {"order": "open", "vacancy": "next_by_votes"}}
MAYOR_NOTE = (
    "A municipality's mayor is elected directly. A candidate with more than half of the valid votes is elected in the "
    "first round; otherwise the two leading candidates meet in a runoff, and the one with more votes is elected."
)
ASSEMBLY_NOTE = (
    "A municipal assembly is elected by open-list proportional representation in a single municipality-wide "
    "district. Seats are divided among the political entities by the Sainte-Laguë method, and each entity's seats go "
    "to its candidates in order of their preference votes, subject to the gender quota, and a vacant seat goes to the entity's next candidate by votes. The seats each entity won "
    "are not stored here."
)
METHOD = (
    "Votes by polling station from the final 'total' file of the Commission's results platform for the 2025 local "
    "elections. Besides the regular polling stations, each municipality has conditional-vote stations (codes ending "
    "in VK), a special-needs station (votes collected from voters who cannot reach a polling station), and postal "
    "stations (codes starting with VP); each is stored as its own reporting unit. The platform prints, for every "
    "station, the votes of each entity and the number of valid votes, which equals their sum; it prints no invalid "
    "or blank ballots. Registered voters are stored for the regular polling stations only, because for the other "
    "stations the platform repeats the valid votes in that field. Each municipality's totals are stored as declared "
    "and equal the sum of its stations."
)
LIST_METHOD = (
    " The platform also prints each list candidate's preference votes, but its candidate numbering cannot be matched "
    "to the published candidate lists with certainty (its candidate 1 always carries the entity's own total), so "
    "preference votes are not stored."
)


def packages() -> list[str]:
    from ea.sources.xk import kuvendi, local

    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/{FIRST}",
        f"{SOURCE}/{RUNOFF}",
        *(f"{SOURCE}/{day}" for day in kuvendi.DAYS),
        f"{SOURCE}/local-registry",
        *(f"{SOURCE}/{day}" for day in local.DAYS),
    ]


def _data(path: Path) -> None:
    if not path.read_bytes().lstrip().startswith(b"{"):
        raise ValueError(f"{path.name} is not JSON data")


def fetch() -> None:
    from ea.sources.xk import kuvendi, local

    for url in URLS:
        raw.fetch(SOURCE, url, suffix=".json", check=_data)
    kuvendi.fetch_all()
    local.fetch()


def parse() -> None:
    from ea.sources.xk import kuvendi, local

    geo().write()
    registry().write()
    first().write()
    runoff().write()
    for day in kuvendi.DAYS:
        kuvendi.election_package(day).write()
    local.registry().write()
    for day in local.DAYS:
        local.election_package(day).write()


def emit() -> None:
    package.run("load", packages())


# Reading the files.


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", clean(text)).casefold().split())


@functools.cache
def load(url: str) -> Any:
    return json.loads(raw.cached(SOURCE, url).path.read_bytes())


def kind_of(station: str) -> str:
    """The kind of a polling station from its code: regular, conditional, special needs, or postal."""
    if re.fullmatch(r"\d{4}/\d{2}", station):
        return "regular"
    if re.fullmatch(r"\d{4}VK/\d{2}", station):
        return "conditional"
    if re.fullmatch(r"\d{2}/\d{2}/\d{2}", station):
        return "special"
    if re.fullmatch(r"VP\d{2}/\d{2}", station):
        return "postal"
    raise ValueError(f"unexpected polling station code {station}")


UNIT_KIND = {"regular": "standard", "conditional": "standard", "special": "mobile", "postal": "postal_district"}


@dataclass
class Station:
    code: str
    municipality: str
    center: str
    registered: int
    valid: int
    votes: dict[str, int]  # entity -> votes


@dataclass
class Count:
    stations: list[Station]
    municipal: dict[str, dict[str, int]]  # municipality -> entity -> votes, as printed
    municipal_valid: dict[str, int]
    national_valid: int
    station_total: int
    processed: int


def count(url: str) -> Count:
    data = load(url)
    stations: list[Station] = []
    municipal: dict[str, dict[str, int]] = {}
    municipal_valid: dict[str, int] = {}
    for m, node in data["municipalities"].items():
        summary = node["summary"]
        municipal[m] = {e: int(s["total_votes"] if isinstance(s, dict) else s) for e, s in summary.items()}
        municipal_valid[m] = int(node["voter_stats"]["current_voters"])
        for center, centre in node["voting_stations"].items():
            for code, station in centre["polling_stations"].items():
                stats = station["voter_stats"]
                votes = {str(d["political_entity_external_id"]): int(d["total_votes"]) for d in station["details"]}
                if int(station["total_voters"]) != int(stats["current_voters"]):
                    raise ValueError(f"{url}: {code} prints two different vote totals")
                stations.append(
                    Station(code, m, center, int(stats["max_possible_voters"]), int(stats["current_voters"]), votes)
                )
    stats = data["polling_station_stats"]
    if stats["processed"] != stats["total"]:
        raise ValueError(f"{url}: {stats['processed']} of {stats['total']} stations processed")
    return Count(
        stations,
        municipal,
        municipal_valid,
        int(data["voter_stats"]["current_voters"]),
        int(stats["total"]),
        int(stats["processed"]),
    )


def compare(url: str, c: Count) -> None:
    """Each station's votes against its valid votes, the stations against each municipality, and those against the
    national total, all as the platform prints them."""
    if len({s.code for s in c.stations}) != len(c.stations) or len(c.stations) != c.station_total:
        raise ValueError(f"{url}: {len(c.stations)} stations, {c.station_total} printed")
    summed: dict[str, Counter[str]] = defaultdict(Counter)
    valid: Counter[str] = Counter()
    for s in c.stations:
        if sum(s.votes.values()) != s.valid:
            raise ValueError(f"{url}: {s.code} entity votes do not sum to its valid votes")
        summed[s.municipality].update(s.votes)
        valid[s.municipality] += s.valid
    for m, printed in c.municipal.items():
        got = {e: n for e, n in summed[m].items()}
        if {e: n for e, n in printed.items()} != {e: got.get(e, 0) for e in printed} or set(got) - set(printed):
            raise ValueError(f"{url}: municipality {m} stations do not sum to its printed totals")
        if valid[m] != c.municipal_valid[m] or sum(printed.values()) != c.municipal_valid[m]:
            raise ValueError(f"{url}: municipality {m} valid votes do not match")
    if sum(c.municipal_valid.values()) != c.national_valid:
        raise ValueError(
            f"{url}: municipalities sum to {sum(c.municipal_valid.values())}, nationally {c.national_valid}"
        )
    print(
        f"{url}: {len(c.stations)} stations sum to the {len(c.municipal)} municipal and the national {c.national_valid} valid votes"
    )


# Places.


@functools.cache
def municipalities() -> dict[str, tuple[str, str]]:
    """Each municipality's code and its (Albanian, Serbian) name."""
    names: dict[str, tuple[str, str]] = {}
    for m in load(MUNICIPALITIES)["municipalities"]:
        sq, sr = (clean(part) for part in str(m["name"]).split("/", 1))
        names[str(m["id"])] = (sq, sr)
    if len(names) != 38:
        raise ValueError(f"{len(names)} municipalities, expected 38")
    return names


@functools.cache
def centers() -> dict[str, str]:
    """Each polling center's name by its code, from the round-one and runoff lists."""
    names: dict[str, str] = {}
    for url in (CENTERS, CENTERS_2):
        for m in load(url)["municipalities"]:
            for c in m["votingStations"]:
                if c["id"] != "0" and c.get("name"):
                    names.setdefault(str(c["id"]), clean(c["name"]))
    for m in load(TYPES)["municipalities"]:
        for kind in m["vote_types"].values():
            for c in kind["votingStations"]:
                if c.get("name") and clean(c["name"]) != str(c["id"]):
                    names[str(c["id"])] = clean(c["name"])
    return names


def municipality(code: str) -> str:
    return key("area", "xk.komuna", code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Kosovo's 38 municipalities as the Central Election Commission names them, their outlines, and its polling "
        "station scheme",
    )
    p.external |= {KOSOVO}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Results of the 2025 local elections by polling station",
        url="https://resultslocal2025.kqz-ks.org/",
        license="Official election results of the Republic of Kosovo, published for public use",
        license_url=f"{SITE}/",
        attribution="Central Election Commission of the Republic of Kosovo (KQZ), kqz-ks.org",
    )
    for url in GEO_FILES:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add(
        "scheme",
        code="xk.komuna",
        polity_id=KOSOVO,
        kind="administrative",
        rank=1,
        noun="municipality",
        code_system=None,
    )
    p.add("scheme", code="xk.vendvotimi", polity_id=KOSOVO, kind="precinct", rank=2, noun="polling station")
    for code, (sq, sr) in sorted(municipalities().items(), key=lambda item: int(item[0])):
        id = municipality(code)
        p.add("area", id=id, scheme_code="xk.komuna")
        p.add("area_code", area_id=id, code=code)
        p.name(id, sq, "sq", "official")
        p.name(id, sr, "sr", "official", False)
    from ea.sources.xk import kuvendi

    kuvendi.extend_geo(p)
    return p


# Offices, assemblies, parties, and electoral systems.


def office(m: str) -> str:
    return key("office", "mayor", m)


def body(m: str) -> str:
    return key("body", "assembly", m)


@dataclass
class Entity:
    name: str
    people: set[str] = field(default_factory=set)  # every candidate's folded name
    municipalities: set[str] = field(default_factory=set)


@functools.cache
def entities() -> dict[str, Entity]:
    found: dict[str, Entity] = {}
    for url in (ENTITIES, ENTITIES_2):
        for m, node in load(url)["municipalities"].items():
            for e, item in node["politicalEntities"].items():
                entity = found.setdefault(e, Entity(clean(item["name"])))
                if fold(entity.name) != fold(item["name"]):
                    raise ValueError(f"entity {e} has two names")
                entity.municipalities.add(m)
                entity.people |= {fold(c["fullName"]) for c in item["candidates"]}
    return found


def independent(e: str) -> bool:
    """An entity that is a single candidate standing on their own, whose entity name is their own name."""
    entity = entities()[e]
    return entity.people == {fold(entity.name)}


def party_kind(name: str) -> str:
    folded = fold(name)
    if "koalic" in folded:
        return "bloc"
    if "iniciativ" in folded or "inicijativ" in folded:
        return "independent_group"
    return "party"


def party(e: str) -> str | None:
    return None if independent(e) else key("party", fold(entities()[e].name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Kosovo's municipal mayors and assemblies, the Assembly, their electoral systems, and the political entities on "
        "the Central Election Commission's ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {KOSOVO}
    for url in (ENTITIES, ENTITIES_2):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.uses(raw.cached(SOURCE, MUNICIPALITIES))
    for m, (sq, sr) in sorted(municipalities().items(), key=lambda item: int(item[0])):
        p.add("office", id=office(m), polity_id=KOSOVO, kind="mayor")
        p.name(office(m), f"Mayor of {sq}", "en", "official")
        p.name(office(m), f"Kryetari i Komunës së {sq}", "sq", "native", False)
        p.name(office(m), f"Gradonačelnik opštine {sr}", "sr", "native", False)
        p.add("body", id=body(m), polity_id=KOSOVO, kind="council")
        p.name(body(m), f"Municipal Assembly of {sq}", "en", "official")
        p.name(body(m), f"Kuvendi Komunal i {sq}", "sq", "native", False)
        p.name(body(m), f"Skupština opštine {sr}", "sr", "native", False)
    for id, rules, note in (
        (MAYOR_SYSTEM, MAYOR_RULES, MAYOR_NOTE),
        (ASSEMBLY_SYSTEM, ASSEMBLY_RULES, ASSEMBLY_NOTE),
    ):
        p.add(
            "electoral_system", id=id, polity_id=KOSOVO, valid_from=FIRST, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    made: set[str] = set()
    for e, entity in sorted(entities().items(), key=lambda item: int(item[0])):
        id = party(e)
        if id is None or id in made:
            continue
        made.add(id)
        p.add("party", id=id, kind=party_kind(entity.name), polity_id=KOSOVO)
        p.name(id, entity.name, "sq", "official")
    from ea.sources.xk import kuvendi

    kuvendi.extend_registry(p)
    return p


# The elections.


def candidates(url: str) -> dict[tuple[str, str], dict[int, tuple[str, str | None]]]:
    """(entity, municipality) -> position -> (name, gender); position 0 is the entity's mayoral candidate."""
    found: dict[tuple[str, str], dict[int, tuple[str, str | None]]] = {}
    for e, node in load(url).items():
        for m, people in node.items():
            if m == "name":
                continue
            found[(e, m)] = {
                int(n): (clean(c["name"]), {"M": "male", "F": "female"}.get(str(c.get("gender"))))
                for n, c in people.items()
            }
    return found


def person(m: str, e: str, name: str) -> str:
    return key("person", m, e, fold(name))


class Facts:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str | None, int, int]] = []

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        self.rows.append((result_set, unit, option, measure, value))

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def unit_id(day: date, code: str) -> str:
    return key("unit", f"{day}:{code}")


def units(p: Package, day: date, stations: list[Station]) -> None:
    for s in stations:
        id = unit_id(day, s.code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=KOSOVO,
            scheme_code="xk.vendvotimi",
            code=f"{day}:{s.code}",
            area_relation="within",
            area_id=municipality(s.municipality),
            kind=UNIT_KIND[kind_of(s.code)],
        )
        center = centers().get(s.center)
        label = f"{s.code}" + (f" ({center})" if center and center != s.center else "")
        p.name(id, label, "sq", "official")


def election(p: Package, day: date, runoff: bool) -> tuple[str, str]:
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=KOSOVO,
        kind="runoff" if runoff else "local",
        date_from=day,
        date_to=day,
        status="held",
    )
    if runoff:
        p.name(election_id, "2025 Kosovan local elections, mayoral runoff", "en", "official")
        p.name(election_id, "Zgjedhjet lokale 2025, balotazhi për kryetar", "sq", "native", False)
        path = f"xk/{day}-local-runoff"
    else:
        p.name(election_id, "2025 Kosovan local elections", "en", "official")
        p.name(election_id, "Zgjedhjet lokale 2025", "sq", "native", False)
        path = f"xk/{day}-local"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    return election_id, path


def result_set(p: Package, election_id: str, contest: str, id: str, method: str) -> None:
    p.add(
        "result_set",
        id=id,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")


def values(facts: Facts, result_set: str, day: date, m: str, c: Count, options: dict[str, str]) -> None:
    for s in c.stations:
        if s.municipality != m:
            continue
        unit = unit_id(day, s.code)
        if kind_of(s.code) == "regular" and s.registered:
            facts.add(result_set, unit, None, REGISTERED, s.registered)
        facts.add(result_set, unit, None, VALID, s.valid)
        for e, n in s.votes.items():
            facts.add(result_set, unit, options[e], VOTES, n)


def declared(p: Package, result_set: str, printed: dict[str, int], valid: int, options: dict[str, str]) -> None:
    for e, n in sorted(printed.items(), key=lambda item: int(item[0])):
        p.add("declared", result_set_id=result_set, option_id=options[e], measure=VOTES, value=n)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)


def mayor_options(
    p: Package,
    day: date,
    m: str,
    contest: str,
    running: list[str],
    people: dict[tuple[str, str], dict[int, tuple[str, str | None]]],
    made: set[str],
) -> dict[str, str]:
    options: dict[str, str] = {}
    for e in sorted(running, key=int):
        listed = people.get((e, m), {}).get(0)
        if listed is None:
            raise ValueError(f"{day}: entity {e} has no mayoral candidate in municipality {m}")
        name, gender = listed
        option = options[e] = key("option", day, "mayor", m, e)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(e),
            ballot_label=name,
        )
        who = person(m, e, name)
        if who not in made:
            made.add(who)
            p.add("person", id=who, gender=gender)
            p.name(who, name, "sq", "official")
        p.add(
            "candidacy",
            id=key("candidacy", day, "mayor", m, e),
            person_id=who,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=party(e),
            is_independent=independent(e),
        )
    return options


def winner(printed: dict[str, int]) -> tuple[list[str], int]:
    ranked = sorted(printed, key=lambda e: (-printed[e], int(e)))
    return ranked, sum(printed.values())


def first() -> Package:
    day = FIRST
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The 2025 Kosovan local elections, municipal assemblies and mayors, by polling station from the Central "
        "Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {KOSOVO}
    for url in (ASSEMBLY, MAYOR, CANDIDATES):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for url in (ENTITIES, ENTITIES_2, CENTERS, TYPES, MUNICIPALITIES):
        p.uses(raw.cached(SOURCE, url))
    assembly, mayor = count(ASSEMBLY), count(MAYOR)
    compare(ASSEMBLY, assembly)
    compare(MAYOR, mayor)
    if {s.code for s in assembly.stations} != {s.code for s in mayor.stations}:
        raise ValueError("the two contests list different polling stations")
    election_id, path = election(p, day, runoff=False)
    units(p, day, assembly.stations)
    people = candidates(CANDIDATES)
    runoffs = {m: set(node["politicalEntities"]) for m, node in load(ENTITIES_2)["municipalities"].items()}
    facts = Facts()
    made: set[str] = set()
    for m, (sq, sr) in sorted(municipalities().items(), key=lambda item: int(item[0])):
        place = slug(sq)
        # The municipal assembly.
        contest = key("contest", day, "assembly", m)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(m),
            district_area_id=municipality(m),
            round_number=1,
            electoral_system_id=ASSEMBLY_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Municipal Assembly of {sq}", "en", "official")
        p.name(contest, f"Kuvendi Komunal i {sq}", "sq", "native", False)
        p.name(contest, f"Skupština opštine {sr}", "sr", "native", False)
        p.add("slug", path=f"{path}/{place}/assembly", entity_id=contest, is_current=True)
        rs = key("result_set", day, "assembly", m)
        result_set(p, election_id, contest, rs, METHOD + LIST_METHOD)
        options: dict[str, str] = {}
        for e in sorted(assembly.municipal[m], key=int):
            option = options[e] = key("option", day, "assembly", m, e)
            alone = independent(e)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate" if alone else "list",
                party_id=party(e),
                ballot_label=entities()[e].name,
            )
            listed = people.get((e, m), {})
            for position, (name, gender) in sorted(listed.items()):
                if position == 0 or (alone and position > 1):
                    continue
                who = person(m, e, name)
                if who not in made:
                    made.add(who)
                    p.add("person", id=who, gender=gender)
                    p.name(who, name, "sq", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day, "assembly", m, e, position),
                    person_id=who,
                    option_id=option,
                    role="head" if alone else "list_member",
                    list_position=None if alone else position,
                    ballot_name=name,
                    member_of_party_id=party(e),
                    is_independent=alone,
                )
        values(facts, rs, day, m, assembly, options)
        declared(p, rs, assembly.municipal[m], assembly.municipal_valid[m], options)
        # The mayor.
        contest = key("contest", day, "mayor", m)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office(m),
            district_area_id=municipality(m),
            magnitude=1,
            round_number=1,
            electoral_system_id=MAYOR_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Mayor of {sq}", "en", "official")
        p.name(contest, f"Kryetari i Komunës së {sq}", "sq", "native", False)
        p.name(contest, f"Gradonačelnik opštine {sr}", "sr", "native", False)
        p.add("slug", path=f"{path}/{place}/mayor", entity_id=contest, is_current=True)
        rs = key("result_set", day, "mayor", m)
        result_set(p, election_id, contest, rs, METHOD)
        printed = mayor.municipal[m]
        options = mayor_options(p, day, m, contest, list(printed), people, made)
        values(facts, rs, day, m, mayor, options)
        declared(p, rs, printed, mayor.municipal_valid[m], options)
        ranked, valid = winner(printed)
        if m in runoffs:
            if (
                set(ranked[:2]) != runoffs[m]
                or 2 * printed[ranked[0]] > valid
                or (len(ranked) > 2 and printed[ranked[1]] == printed[ranked[2]])
            ):
                raise ValueError(f"{m}: the runoff pair is not the two leading candidates short of a majority")
            for e in ranked[:2]:
                p.add("outcome", result_set_id=rs, option_id=options[e], kind="advanced")
        else:
            if 2 * printed[ranked[0]] <= valid:
                raise ValueError(f"{m}: no runoff is listed, but no candidate has a majority")
            p.add("outcome", result_set_id=rs, option_id=options[ranked[0]], kind="elected")
    p.frame("result_value", facts.frame())
    print(f"{day}: {len(assembly.stations)} stations, 38 assemblies and 38 mayors", flush=True)
    return p


def runoff() -> Package:
    day = RUNOFF
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The mayoral runoffs of the 2025 Kosovan local elections by polling station from the Central Election "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{FIRST}"],
    )
    p.external |= {KOSOVO}
    for url in (MAYOR_2, CANDIDATES_2):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for url in (ENTITIES, ENTITIES_2, CENTERS, CENTERS_2, TYPES, MUNICIPALITIES):
        p.uses(raw.cached(SOURCE, url))
    mayor = count(MAYOR_2)
    compare(MAYOR_2, mayor)
    election_id, path = election(p, day, runoff=True)
    units(p, day, mayor.stations)
    people = candidates(CANDIDATES_2)
    first_people = candidates(CANDIDATES)
    runoffs = {m: set(node["politicalEntities"]) for m, node in load(ENTITIES_2)["municipalities"].items()}
    facts = Facts()
    made: set[str] = set()
    for m, printed in sorted(mayor.municipal.items(), key=lambda item: int(item[0])):
        sq, sr = municipalities()[m]
        if set(printed) != runoffs[m]:
            raise ValueError(f"{m}: the runoff results list other entities than the runoff list")
        for e in printed:
            if first_people[(e, m)][0][0] != people[(e, m)][0][0]:
                raise ValueError(f"{m}: entity {e} has another mayoral candidate in the runoff")
        contest = key("contest", day, "mayor", m)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office(m),
            district_area_id=municipality(m),
            magnitude=1,
            round_number=2,
            previous_round_id=key("contest", FIRST, "mayor", m),
            electoral_system_id=MAYOR_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Mayor of {sq}, runoff", "en", "official")
        p.name(contest, f"Kryetari i Komunës së {sq}, balotazhi", "sq", "native", False)
        p.name(contest, f"Gradonačelnik opštine {sr}, drugi krug", "sr", "native", False)
        p.add("slug", path=f"{path}/{slug(sq)}/mayor", entity_id=contest, is_current=True)
        rs = key("result_set", day, "mayor", m)
        result_set(p, election_id, contest, rs, METHOD)
        # The candidates are the people of round one, which the round-one package creates.
        made |= {person(m, e, people[(e, m)][0][0]) for e in printed}
        options = mayor_options(p, day, m, contest, list(printed), people, made)
        values(facts, rs, day, m, mayor, options)
        declared(p, rs, printed, mayor.municipal_valid[m], options)
        ranked, _ = winner(printed)
        if printed[ranked[0]] == printed[ranked[1]]:
            raise ValueError(f"{m}: the runoff is tied")
        p.add("outcome", result_set_id=rs, option_id=options[ranked[0]], kind="elected")
    p.frame("result_value", facts.frame())
    print(f"{day}: {len(mayor.stations)} stations, {len(mayor.municipal)} mayoral runoffs", flush=True)
    return p
