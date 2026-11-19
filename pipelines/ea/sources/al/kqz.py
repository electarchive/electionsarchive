"""Albania's parliamentary and local elections from the Central Election Commission (KQZ).

The Commission's results platform serves each election as JSON files: the national totals, the totals of each of the 12
counties (qarqe) with the seats declared, and the list votes at each voting center (QV). The 2025 files also print each
center's accounting and its place, and the votes cast abroad by county. The 2021 files hold the votes by center, and a
separate structure file holds the places and the voters registered at each center.

The local elections of 14 May 2023 are on the same platform: each of the 61 municipalities elects a mayor and a council,
and the platform prints both ballots' votes at each voting center, with each municipality's declared totals and the
council seats; its results page (results2023/results2023.htm) loads these files from its input folder and its scripts. The
platform also serves the mayoral by-elections held since then, one results site per election day.
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
import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "al.kqz"
SITE = "https://kqz.gov.al"
COUNTIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ALB/ADM1/"
    "geoBoundaries-ALB-ADM1.geojson"
)
COUNTIES_FROM = date(2021, 1, 1)  # the year the county shapes represent
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
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(package_key(e) for e in ELECTIONS),
        local_key(),
        *(by_key(b) for b in BY),
    ]


def _data(path: Path) -> None:
    if path.read_bytes().lstrip().startswith(b"<"):
        raise ValueError(f"{path.name} is not data (a challenge page?)")


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
    for address in (COUNTIES, *local_urls(), *by_urls()):
        for attempt in range(12):
            try:
                raw.fetch(SOURCE, address, suffix=Path(address).suffix, check=_data)
                break
            except (ValueError, httpx.HTTPError) as failure:
                print(f"{address}: {failure}; attempt {attempt + 1}", flush=True)
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"{address} could not be fetched")


def parse() -> None:
    built = [*(election(e) for e in ELECTIONS), local_election(), *(by_election(b) for b in BY)]
    parties = registry()
    _cover(parties, built)
    geo().write()
    parties.write()
    for p in built:
        p.write()


def _cover(parties: Package, built: list[Package]) -> None:
    """Fail when an election package names a party the registry leaves out, since the loaded rows still use it."""
    held = {row["id"] for row in parties.rows.get("party", [])}
    for p in built:
        used = {
            row[column]
            for table in ("contest_option", "candidacy")
            for row in p.rows.get(table, [])
            for column in ("party_id", "member_of_party_id")
            if row.get(column)
        }
        if missing := used - held:
            raise RuntimeError(f"{p.key} names parties the registry leaves out: {sorted(map(str, missing))}")


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
    for center in read_local().centers.values():
        qarqe.add(center.qarku)
        towns.add((center.qarku, center.bashkia))
        units.add((center.qarku, center.bashkia, center.njesia))
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
    p.artifact(raw.cached(SOURCE, local_url(STRUCTURE)), SOURCE_ID)
    shapes = key("source", "geoboundaries-adm1")
    p.add(
        "source",
        id=shapes,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Albania ADM1 (counties)",
        url="https://www.geoboundaries.org/",
        license="Public Domain",
        license_url="https://www.geoboundaries.org/",
        attribution="geoBoundaries (William & Mary geoLab)",
        notes="Boundary year 2021, built December 2023, from Wikimedia Commons. Generalized shapes.",
    )
    p.artifact(raw.cached(SOURCE, COUNTIES), shapes)
    for scheme, rank, kind, noun in (
        ("al.qarku", 1, "administrative", "county"),
        ("al.bashkia", 2, "administrative", "municipality"),
        ("al.njesia", 3, "administrative", "administrative unit"),
        ("al.qv", 4, "precinct", "voting center"),
    ):
        p.add("scheme", code=scheme, polity_id=ALBANIA, kind=kind, rank=rank, noun=noun, code_system=None)
    qarqe, towns, units = places()
    outlines: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, COUNTIES).path.read_bytes())["features"]:
        found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        polygons = [
            q
            for part in shapely.get_parts(found)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        outlines[fold(feature["properties"]["shapeName"])] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    if missing := qarqe - set(outlines):
        raise ValueError(f"no shape for the counties {sorted(missing)}")
    for name in sorted(qarqe):
        id = area("al.qarku", name)
        p.add("area", id=id, scheme_code="al.qarku")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=COUNTIES_FROM,
            geom=shapely.to_wkb(outlines[name], hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outlines[name]), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=shapes,
        )
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
    local = read_local()
    for id, rules, note in (
        (MAYOR_SYSTEM, {"formula": "fptp"}, MAYOR_NOTE),
        (COUNCIL_SYSTEM, {"formula": "dhondt"}, COUNCIL_NOTE),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=ALBANIA,
            valid_from=date(2023, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    for town_ in local.towns.values():
        council, mayor = council_body(town_), mayor_office(town_)
        p.add("body", id=council, polity_id=ALBANIA, kind="council")
        p.name(council, f"Municipal Council of {title(town_.name)}", "en", "official")
        p.name(council, f"Këshilli Bashkiak {title(town_.name)}", "sq", "native", False)
        p.add("office", id=mayor, polity_id=ALBANIA, kind="mayor")
        p.name(mayor, f"Mayor of {title(town_.name)}", "en", "official")
        p.name(mayor, f"Kryetar i Bashkisë {title(town_.name)}", "sq", "native", False)
    names: dict[str, set[str]] = defaultdict(set)
    blocs: set[str] = set()
    for e in ELECTIONS:
        for abbreviation, name in read(e).parties.values():
            if abbreviation:
                names[abbreviation].add(name)
    for abbreviation, name, is_bloc in local.parties:
        names[abbreviation].add(name)
        if is_bloc:
            blocs.add(abbreviation)
    for b in BY:
        for _, abbreviation, name in read_by(b).parties:
            names[abbreviation].add(name)
    for abbreviation, spellings in sorted(names.items()):
        id = party(abbreviation)
        bloc = abbreviation in blocs or any(s.lower().startswith("koalicioni") for s in spellings)
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


# The local elections of 14 May 2023.

LOCAL_DAY = date(2023, 5, 14)
LOCAL_FOLDER = "results2023"
STRUCTURE, CANDIDATES = "js/json_strukture9.js", "js/json_subjekte8.js"
MAYOR_FILES = ("json_all_summary_0_qv_lh", "json_all_summary_0_bashki_lh")
COUNCIL_FILES = ("json_all_summary_1_qv", "json_all_summary_1_bashki")
LOCAL_NAMES = ("2023 Albanian local elections", "Zgjedhjet vendore, 2023")
MAYOR_SYSTEM = key("electoral_system", "mayor")
COUNCIL_SYSTEM = key("electoral_system", "council")
MAYOR_NOTE = "Each of the 61 municipalities elects its mayor in one round, and the candidate with the most votes wins."
COUNCIL_NOTE = (
    "Each municipality's council, of 15 to 61 members, is elected by proportional representation from party and "
    "coalition lists and independent candidates. These rules were fitted to the seats the Commission declared in "
    "2023: the D'Hondt highest-average method reproduces them in 60 of the 61 councils. The declared seats of the "
    "other council, Kurbin, differ by one seat between two lists, which the method does not explain."
)
LOCAL_METHOD = (
    "Votes by voting center (QV) as the Commission's results platform publishes them for the mayoral and the council "
    "ballots, which are counted separately. Each municipality is one contest on each ballot. The platform declares "
    "each municipality's votes, accounting figures, and council seats, and those figures are stored as declared. "
    "Registered voters at each center come from the platform's structure file. Every center's figures add up to its "
    "administrative unit's, but a municipality's declared total can exceed the sum of its centers; the difference is "
    "stored as one aggregate unit for the municipality, because the platform does not place those votes at a "
    "center. A few centers have registered voters and no results, and are stored with only their registered voters. "
    "The platform declares no mayor, so no winner is stored for the mayoral contests."
)


def local_key() -> str:
    return f"{SOURCE}/{LOCAL_DAY}"


def local_url(name: str) -> str:
    if name.startswith("js/"):
        return f"https://iemis.kqz.gov.al/{LOCAL_FOLDER}/{name}"
    return f"https://iemis.kqz.gov.al/{LOCAL_FOLDER}/input/results/{name}.txt"


def local_urls() -> list[str]:
    return [local_url(name) for name in (STRUCTURE, CANDIDATES, *MAYOR_FILES, *COUNCIL_FILES)]


@dataclass(frozen=True)
class Center:
    code: str
    qarku: str
    bashkia: str
    njesia: str
    registered: int


@dataclass(frozen=True)
class Town:
    ident: str
    qarku: str
    name: str
    registered: int


@dataclass
class Ballot:
    """One ballot's figures: by center and by municipality, and its ballot lines."""

    lines: dict[str, dict[int, tuple[str, str]]] = field(default_factory=dict)  # town -> position -> (name, party)
    accounting: dict[str, dict[int, int]] = field(default_factory=dict)  # center id -> measure -> value
    votes: dict[str, dict[int, int]] = field(default_factory=dict)  # center id -> position -> votes
    declared_accounting: dict[str, dict[int, int]] = field(default_factory=dict)  # town -> measure -> value
    declared_votes: dict[str, dict[int, int]] = field(default_factory=dict)  # town -> position -> votes
    seats: dict[str, dict[int, int]] = field(default_factory=dict)  # town -> position -> seats


@dataclass
class Local:
    towns: dict[str, Town]
    centers: dict[str, Center]  # center id -> center
    town_of: dict[str, str]  # center id -> town id
    mayor: Ballot
    council: Ballot
    parties: list[tuple[str, str, bool]]  # abbreviation, name, whether it is a coalition


def _script(text: str, name: str) -> Any:
    """The value a platform script assigns to a variable."""
    return json.JSONDecoder().raw_decode(text[text.index(f"{name}=") + len(name) + 1 :])[0]


def _file(name: str) -> str:
    return raw.cached(SOURCE, local_url(name)).path.read_bytes().decode("utf-8")


def _accounting(row: dict[str, Any]) -> dict[int, int]:
    return {VOTERS: number(row["v8"]), VALID: number(row["v10"]), INVALID: number(row["v11"])}


def _ballot(files: tuple[str, str], seats: bool) -> Ballot:
    ballot = Ballot()
    by_center, by_town = (json.loads(_file(name))["data_main"] for name in files)
    for row in by_center[0]["data"]:
        ballot.accounting[clean(row["v1"])] = _accounting(row)
    for row in by_center[1]["data"]:
        ballot.votes.setdefault(clean(row["v1"]), {})[number(row["v2"])] = number(row["v3"])
    for row in by_town[0]["data"]:
        ballot.declared_accounting[clean(row["v1"])] = _accounting(row)
    for row in by_town[1]["data"]:
        town_ = clean(row["v1"])
        ballot.declared_votes.setdefault(town_, {})[number(row["v2"])] = number(row["v3"])
        if seats and number(row["v4"]):
            ballot.seats.setdefault(town_, {})[number(row["v2"])] = number(row["v4"])
    return ballot


@functools.cache
def read_local() -> Local:
    tree = json.loads(_file(STRUCTURE).split("=", 1)[1].rstrip().rstrip(";"))
    towns: dict[str, Town] = {}
    centers: dict[str, Center] = {}
    town_of: dict[str, str] = {}
    seen: set[tuple[str, str, str]] = set()
    for top in tree["data"]:
        if not top["child"]:
            continue
        qarku = fold(str(top["qarku"]))
        for bashkia in top["child"]:
            name = fold(str(bashkia["bashkia"]))
            ident = clean(bashkia["v1"]).removeprefix("st")
            towns[ident] = Town(ident, qarku, name, number(bashkia["v3"]))
            for njesia in bashkia["child"]:
                unit = fold(str(njesia["njesi"]))
                if (qarku, name, unit) in seen:
                    raise ValueError(f"{name} has two administrative units named {unit}")
                seen.add((qarku, name, unit))
                for center in njesia["child"]:
                    ident_ = clean(center["v1"]).removeprefix("st")
                    centers[ident_] = Center(clean(center["qv"]), qarku, name, unit, number(center["v3"]))
                    town_of[ident_] = ident
    script = _file(CANDIDATES)
    mayor, council = _ballot(MAYOR_FILES, False), _ballot(COUNCIL_FILES, True)
    parties: dict[str, tuple[str, bool]] = {}
    for row in _script(script, "json_bashkia_lh_array_fiks_2023")["data"]:
        abbreviation = clean(row["v5"])
        mayor.lines.setdefault(clean(row["v1"]), {})[number(row["v2"])] = (clean(row["v3"]), abbreviation)
        if abbreviation != "PAVARUR":
            parties.setdefault(abbreviation, (clean(row["v4"]), False))
    for row in _script(script, "json_bashkia_fiks_array_2023")["data"]:
        abbreviation, name = clean(row["v3"]), clean(row["v4"])
        council.lines.setdefault(clean(row["v1"]), {})[number(row["v2"])] = (name, abbreviation)
        if abbreviation:
            parties[abbreviation] = (name, parties.get(abbreviation, ("", False))[1] or clean(row["v5"]) == name)
    listed = [(abbreviation, name, bloc) for abbreviation, (name, bloc) in sorted(parties.items())]
    return Local(towns, centers, town_of, mayor, council, listed)


def town_area(t: Town) -> str:
    return area("al.bashkia", t.qarku, t.name)


def council_body(t: Town) -> str:
    return key("body", "council", t.qarku, t.name)


def mayor_office(t: Town) -> str:
    return key("office", "mayor", t.qarku, t.name)


def local_election() -> Package:
    data = read_local()
    p = Package(
        local_key(),
        SOURCE,
        f"The {LOCAL_NAMES[0]} by voting center from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ALBANIA, COUNTRY}
    p.uses(raw.cached(SOURCE, local_url(STRUCTURE)))  # the geo package cites it
    for name in (CANDIDATES, *MAYOR_FILES, *COUNCIL_FILES):
        p.artifact(raw.cached(SOURCE, local_url(name)), SOURCE_ID)
    day = LOCAL_DAY
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=ALBANIA,
        kind="local",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, LOCAL_NAMES[0], "en", "official")
    p.name(election_id, LOCAL_NAMES[1], "sq", "native", False)
    path = f"al/{day}-local"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add(
        "note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=LOCAL_METHOD, lang="en"
    )
    values = Facts()
    for center_id, center in sorted(data.centers.items(), key=lambda item: int(item[0])):
        unit = key("unit", f"{day}:{center.code}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ALBANIA,
            scheme_code="al.qv",
            code=f"{day}:{center.code}",
            area_relation="within",
            area_id=area("al.njesia", center.qarku, center.bashkia, center.njesia),
            kind="standard",
        )
    over: set[str] = set()
    for ballot_name, ballot in (("mayor", data.mayor), ("council", data.council)):
        mayoral = ballot_name == "mayor"
        for ident, town_ in sorted(data.towns.items(), key=lambda item: int(item[0])):
            slug = f"{path}/{plain(town_.qarku).replace(' ', '-')}/{plain(town_.name).replace(' ', '-')}/{ballot_name}"
            contest = key("contest", day, ballot_name, ident)
            result_set = key("result_set", day, ballot_name, ident)
            lines = ballot.lines[ident]
            declared_seats = ballot.seats.get(ident, {})
            nice = title(town_.name)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="office" if mayoral else "list_allocation",
                office_id=mayor_office(town_) if mayoral else None,
                body_id=None if mayoral else council_body(town_),
                district_area_id=town_area(town_),
                magnitude=1 if mayoral else sum(declared_seats.values()),
                round_number=1,
                electoral_system_id=MAYOR_SYSTEM if mayoral else COUNCIL_SYSTEM,
                default_result_kind="counted",
            )
            if mayoral:
                p.name(contest, f"{LOCAL_NAMES[0]}, Mayor of {nice}", "en", "official")
                p.name(contest, f"Zgjedhjet vendore, Kryetar i Bashkisë {nice}", "sq", "native", False)
            else:
                p.name(contest, f"{LOCAL_NAMES[0]}, {nice} Municipal Council", "en", "official")
                p.name(contest, f"Zgjedhjet vendore, Këshilli Bashkiak {nice}", "sq", "native", False)
            p.add("slug", path=slug, entity_id=contest, is_current=True)
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
            options: dict[int, str] = {}
            for position, (name, abbreviation) in sorted(lines.items()):
                option = options[position] = key("option", day, ballot_name, ident, position)
                independent = abbreviation in ("", "PAVARUR")
                if mayoral:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None if independent else party(abbreviation),
                        ballot_position=position,
                        ballot_label=name,
                    )
                    who = key("person", day, ident, position)
                    p.add("person", id=who)
                    p.name(who, name[:1].upper() + name[1:], "sq", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", day, ident, position),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=name,
                        member_of_party_id=None if independent else party(abbreviation),
                        is_independent=independent,
                    )
                else:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="independents" if independent else "list",
                        party_id=None if independent else party(abbreviation),
                        ballot_position=position,
                        ballot_label=name,
                    )
            for position, seats in sorted(declared_seats.items()):
                p.add("outcome", result_set_id=result_set, option_id=options[position], kind="won", seats=seats)
            centers_here = [c for c, t in data.town_of.items() if t == ident]
            sums: dict[int, int] = defaultdict(int)
            totals: dict[int, int] = defaultdict(int)
            for center_id in sorted(centers_here, key=int):
                center = data.centers[center_id]
                unit = key("unit", f"{day}:{center.code}")
                values.add(result_set, unit, None, REGISTERED, center.registered)
                reported = ballot.accounting.get(center_id)
                if reported is None:
                    continue
                for measure, n in reported.items():
                    values.add(result_set, unit, None, measure, n)
                    totals[measure] += n
                if reported[VOTERS] > center.registered:
                    over.add(f"{center.code} ({reported[VOTERS]} voters, {center.registered} registered)")
                for position, n in ballot.votes.get(center_id, {}).items():
                    values.add(result_set, unit, options[position], VOTES, n)
                    sums[position] += n
            declared = ballot.declared_accounting[ident]
            rest = {m: declared[m] - totals[m] for m in declared}
            rest_votes = {pos: ballot.declared_votes[ident].get(pos, 0) - sums[pos] for pos in options}
            if any(v < 0 for v in (*rest.values(), *rest_votes.values())):
                raise ValueError(f"{town_.name} {ballot_name}: the centers exceed the declared totals")
            if any((*rest.values(), *rest_votes.values())):
                unit = key("unit", f"{day}:rest:{ballot_name}:{ident}")
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=ALBANIA,
                    scheme_code="al.qv",
                    code=f"{day}:{ballot_name}:rest:{ident}",
                    area_relation="within",
                    area_id=town_area(town_),
                    kind="aggregate",
                )
                for measure, n in rest.items():
                    values.add(result_set, unit, None, measure, n)
                for position, n in rest_votes.items():
                    values.add(result_set, unit, options[position], VOTES, n)
            for position, n in sorted(ballot.declared_votes[ident].items()):
                p.add("declared", result_set_id=result_set, option_id=options[position], measure=VOTES, value=n)
            for measure, n in {REGISTERED: town_.registered, **declared}.items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
    p.frame("result_value", values.frame())
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own results table prints more voters than registered voters at "
            + ", ".join(sorted(over)),
        )
    compare_local(data)
    print(f"{local_key()}: {len(data.centers)} centers, {len(data.towns)} municipalities", flush=True)
    return p


def compare_local(data: Local) -> None:
    """The municipalities' declared votes against the sum of the centers, which a municipality may exceed."""
    for name, ballot in (("mayor", data.mayor), ("council", data.council)):
        votes = sum(sum(v.values()) for v in ballot.votes.values())
        declared = sum(sum(v.values()) for v in ballot.declared_votes.values())
        valid = sum(a[VALID] for a in ballot.declared_accounting.values())
        print(
            f"{LOCAL_DAY} {name}: centers hold {votes} votes, municipalities declare {declared} "
            f"({valid} valid), seats {sum(sum(v.values()) for v in ballot.seats.values())}",
            flush=True,
        )


# The mayoral by-elections.

BY_FILES = ("json_all_summary_0_qv_lh", "json_all_summary_0_bashki_lh")
BY_NAMES = ("Albanian mayoral by-election", "Zgjedhje të pjesshme për kryetar bashkie")
BY_NOTE = (
    "A municipality holds a by-election for its mayor when the office falls vacant. The candidate with the most votes "
    "wins, as in the general local elections."
)
BY_METHOD = (
    "Votes by voting center (QV) as the Commission's results platform publishes them for the mayoral ballot. Each "
    "municipality is one contest. The platform declares each municipality's votes and accounting figures, and those "
    "figures are stored as declared. Registered voters at each center come from the platform's structure file. The "
    "platform declares no mayor, so no winner is stored. The platform's separate artificial-intelligence recount of "
    "the ballots, where it prints one, is a parallel count and is not stored."
)


@dataclass(frozen=True)
class ByElection:
    day: date
    folder: str  # the results site's folder on iemis.kqz.gov.al
    structure: str
    candidates: str
    names: tuple[str, str]  # English, Albanian


BY = [
    ByElection(
        date(2023, 9, 24),
        "results2023_kukes",
        "js/json_strukture19.js",
        "js/json_subjekte10.js",
        ("2023 Kukës mayoral by-election", "Zgjedhje të pjesshme për kryetar bashkie, Kukës 2023"),
    ),
    ByElection(
        date(2024, 8, 4),
        "results2024_himare",
        "js/json_strukture20.js",
        "js/json_subjekte11.js",
        ("2024 Himarë mayoral by-election", "Zgjedhje të pjesshme për kryetar bashkie, Himarë 2024"),
    ),
    ByElection(
        date(2025, 11, 9),
        "results2025-vendore",
        "js/json_strukture21.js",
        "js/json_subjekte13.js",
        ("2025 Albanian mayoral by-elections", "Zgjedhje të pjesshme për kryetar bashkie, 2025"),
    ),
]


def by_key(b: ByElection) -> str:
    return f"{SOURCE}/{b.day}"


def by_url(b: ByElection, name: str) -> str:
    if name.startswith("js/"):
        return f"https://iemis.kqz.gov.al/{b.folder}/{name}"
    return f"https://iemis.kqz.gov.al/{b.folder}/input/results/{name}.txt"


def by_urls() -> list[str]:
    return [by_url(b, name) for b in BY for name in (b.structure, b.candidates, *BY_FILES)]


def _script_text(b: ByElection, name: str) -> str:
    """A platform script without its comments, which the platform uses to switch candidates and places off."""
    text = raw.cached(SOURCE, by_url(b, name)).path.read_bytes().decode("utf-8")
    return re.sub(r"^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL), flags=re.MULTILINE)


def _assigned(text: str, name: str) -> Any:
    found = re.search(rf"\b{name}\s*=\s*", text)
    if found is None:
        raise ValueError(f"no {name} in the script")
    return json.JSONDecoder().raw_decode(text[found.end() :])[0]


@dataclass
class ByData:
    towns: dict[str, Town]
    centers: dict[str, Center]  # center id -> center
    town_of: dict[str, str]
    ballot: Ballot
    parties: list[tuple[str, str, str]]  # town id, abbreviation, name
    renumbered: set[str] = field(default_factory=set)  # towns whose candidates the platform lists under one position


def read_by_structure(b: ByElection) -> tuple[dict[str, Town], dict[str, Center], dict[str, str]]:
    tree = _assigned(_script_text(b, b.structure), "var json_strukture")
    towns: dict[str, Town] = {}
    centers: dict[str, Center] = {}
    town_of: dict[str, str] = {}
    for top in tree["data"]:
        qarku = fold(str(top["qarku"]))
        for bashkia in top["child"]:
            name = fold(str(bashkia["bashkia"]))
            ident = clean(bashkia["v1"]).removeprefix("st")
            towns[ident] = Town(ident, qarku, name, number(bashkia["v3"]))
            for njesia in bashkia["child"]:
                unit = fold(str(njesia["njesi"]))
                for center in njesia["child"]:
                    ident_ = clean(center["v1"]).removeprefix("st")
                    centers[ident_] = Center(clean(center["qv"]), qarku, name, unit, number(center["v3"]))
                    town_of[ident_] = ident
    return towns, centers, town_of


@functools.cache
def read_by(b: ByElection) -> ByData:
    towns, centers, town_of = read_by_structure(b)
    ballot = Ballot()

    def table(name: str) -> list[Rows]:
        text = raw.cached(SOURCE, by_url(b, name)).path.read_bytes().decode("utf-8")
        return [cast(Rows, block["data"]) for block in json.loads(text)["data_main"]]

    by_center, by_town = table(BY_FILES[0]), table(BY_FILES[1])
    for row in by_center[0]:
        ballot.accounting[clean(row["v1"])] = _accounting(row)
    for row in by_center[1]:
        ballot.votes.setdefault(clean(row["v1"]), {})[number(row["v2"])] = number(row["v3"])
    for row in by_town[0]:
        ballot.declared_accounting[clean(row["v1"])] = _accounting(row)
    for row in by_town[1]:
        ballot.declared_votes.setdefault(clean(row["v1"]), {})[number(row["v2"])] = number(row["v3"])
    if unplaced := (set(ballot.accounting) | set(ballot.votes)) - set(centers):
        raise ValueError(f"{b.day}: centers {sorted(unplaced)} have results and no place in the structure")
    if missing := set(towns) - set(ballot.declared_accounting):
        raise ValueError(f"{b.day}: municipalities {sorted(missing)} have no declared totals")
    listed = _assigned(_script_text(b, b.candidates), "json_bashkia_lh_array_fiks_2023")["data"]
    parties: list[tuple[str, str, str]] = []
    renumbered: set[str] = set()
    for ident in towns:
        rows = [row for row in listed if clean(row["v1"]) == ident]
        declared = sorted(ballot.declared_votes[ident])
        positions = [number(row["v2"]) for row in rows]
        if len(set(positions)) != len(positions):
            # The platform lists two candidates under one ballot position; the results carry one position each.
            positions = declared
            renumbered.add(ident)
        if sorted(positions) != declared:
            raise ValueError(f"{b.day} {ident}: candidates at {positions} and votes at {declared}")
        for position, row in zip(positions, rows, strict=True):
            abbreviation = clean(row["v5"])
            ballot.lines.setdefault(ident, {})[position] = (clean(row["v3"]), abbreviation)
            if abbreviation != "PAVARUR":
                parties.append((ident, abbreviation, clean(row["v4"])))
    return ByData(towns, centers, town_of, ballot, parties, renumbered)


def by_election(b: ByElection) -> Package:
    data = read_by(b)
    p = Package(
        by_key(b),
        SOURCE,
        f"The {b.names[0]} by voting center from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ALBANIA, COUNTRY}
    for name in (b.structure, b.candidates, *BY_FILES):
        p.artifact(raw.cached(SOURCE, by_url(b, name)), SOURCE_ID)
    day = b.day
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=ALBANIA,
        kind="by_election",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, b.names[0], "en", "official")
    p.name(election_id, b.names[1], "sq", "native", False)
    path = f"al/{day}-by-election"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    method = BY_METHOD
    if data.renumbered:
        listed = ", ".join(title(data.towns[i].name) for i in sorted(data.renumbered))
        method += (
            f" For {listed} the platform's candidate list gives two candidates the same ballot position, while its "
            "results carry one position for each; the candidates are matched to the positions in the order the list "
            "prints them."
        )
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=method, lang="en")
    values = Facts()
    for center_id, center in sorted(data.centers.items(), key=lambda item: int(item[0])):
        p.add(
            "reporting_unit",
            id=key("unit", f"{day}:{center.code}"),
            polity_id=ALBANIA,
            scheme_code="al.qv",
            code=f"{day}:{center.code}",
            area_relation="within",
            area_id=area("al.njesia", center.qarku, center.bashkia, center.njesia),
            kind="standard",
        )
    ballot = data.ballot
    over: set[str] = set()
    for ident, town_ in sorted(data.towns.items(), key=lambda item: int(item[0])):
        nice = title(town_.name)
        slug = f"{path}/{plain(town_.qarku).replace(' ', '-')}/{plain(town_.name).replace(' ', '-')}"
        contest = key("contest", day, ident)
        result_set = key("result_set", day, ident)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=mayor_office(town_),
            district_area_id=town_area(town_),
            magnitude=1,
            round_number=1,
            electoral_system_id=MAYOR_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{b.names[0]}, Mayor of {nice}" if len(data.towns) > 1 else b.names[0], "en", "official")
        p.name(
            contest,
            f"Zgjedhje të pjesshme, Kryetar i Bashkisë {nice}" if len(data.towns) > 1 else b.names[1],
            "sq",
            "native",
            False,
        )
        p.add("slug", path=slug, entity_id=contest, is_current=True)
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
        options: dict[int, str] = {}
        for position, (name, abbreviation) in sorted(ballot.lines[ident].items()):
            option = options[position] = key("option", day, ident, position)
            independent = abbreviation in ("", "PAVARUR")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if independent else party(abbreviation),
                ballot_position=position,
                ballot_label=name,
            )
            who = key("person", day, ident, position)
            p.add("person", id=who)
            p.name(who, name[:1].upper() + name[1:], "sq", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, ident, position),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=None if independent else party(abbreviation),
                is_independent=independent,
            )
        sums: dict[int, int] = defaultdict(int)
        totals: dict[int, int] = defaultdict(int)
        for center_id in sorted((c for c, t in data.town_of.items() if t == ident), key=int):
            center = data.centers[center_id]
            unit = key("unit", f"{day}:{center.code}")
            values.add(result_set, unit, None, REGISTERED, center.registered)
            reported = ballot.accounting.get(center_id)
            if reported is None:
                continue
            for measure, n in reported.items():
                values.add(result_set, unit, None, measure, n)
                totals[measure] += n
            if reported[VOTERS] > center.registered:
                over.add(f"{center.code} ({reported[VOTERS]} voters, {center.registered} registered)")
            for position, n in ballot.votes.get(center_id, {}).items():
                values.add(result_set, unit, options[position], VOTES, n)
                sums[position] += n
        declared = ballot.declared_accounting[ident]
        rest = {m: declared[m] - totals[m] for m in declared}
        rest_votes = {pos: ballot.declared_votes[ident].get(pos, 0) - sums[pos] for pos in options}
        if any(v != 0 for v in (*rest.values(), *rest_votes.values())):
            raise ValueError(f"{day} {town_.name}: the centers differ from the declared totals by {rest} {rest_votes}")
        for position, n in sorted(ballot.declared_votes[ident].items()):
            p.add("declared", result_set_id=result_set, option_id=options[position], measure=VOTES, value=n)
        for measure, n in {REGISTERED: town_.registered, **declared}.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
        print(
            f"{day} {nice}: {sum(sums.values())} votes at {len(sums)} positions, declared "
            f"{sum(ballot.declared_votes[ident].values())} (valid {declared[VALID]})",
            flush=True,
        )
    p.frame("result_value", values.frame())
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own results table prints more voters than registered voters at "
            + ", ".join(sorted(over)),
        )
    print(f"{by_key(b)}: {len(data.centers)} centers, {len(data.towns)} municipalities", flush=True)
    return p
