"""Norway's elections from Valgdirektoratet's results service, valgresultat.no, down to the polling circuit."""

import functools
import json
import re
import unicodedata
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import uuid5

import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.no import kartverket

Doc = dict[str, Any]
SOURCE = "no.valg"
API = "https://valgresultat.no/api"
WORKERS = 16


def shard(year: str, kind: str) -> str:
    """The raw-cache folder of one election, so each folder's index stays small."""
    return f"{SOURCE}.{kind.lower()}{year}"


def api_url(path: str = "") -> str:
    return f"{API}{path}"


def document(year: str, kind: str, path: str) -> raw.Artifact:
    return raw.cached(shard(year, kind), api_url(path))


def load(year: str, kind: str, path: str) -> Doc:
    return json.loads(document(year, kind, path).path.read_bytes())


def children(doc: Doc) -> list[str]:
    links = doc["_links"]
    assert isinstance(links, dict)
    return [str(link["href"]) for link in links.get("related", [])]


def elections() -> list[tuple[str, str]]:
    """Every (year, type) the service lists, from its index of elections."""
    index = json.loads(raw.cached(SOURCE, api_url()).path.read_bytes())
    return [(year, str(link["navn"]).upper()) for year, links in index["_sublinks"].items() for link in links]


def fetch() -> None:
    raw.fetch(SOURCE, api_url(), suffix=".json")
    for year, kind in elections():
        source = shard(year, kind)
        queue = deque([f"/{year}/{kind.lower()}"])
        count = 0
        with ThreadPoolExecutor(WORKERS) as pool:
            while queue:
                level = list(queue)
                queue.clear()
                docs = list(pool.map(lambda path: raw.fetch(source, api_url(path), suffix=".json"), level))
                for artifact in docs:
                    queue.extend(children(json.loads(artifact.path.read_bytes())))
                count += len(level)
        print(f"{source}: {count:,} documents", flush=True)
    kartverket.fetch()


# Parsing.

NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNOR"))
VAL_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f{SOURCE}"))
MAP_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f{kartverket.KARTVERKET}"))
KLASS_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f{kartverket.KLASS}"))
SKIPPED: list[str] = []
COUNTY = "no.fylke"
MUNICIPALITY = "no.kommune"
SAMI = "no.samevalgkrets"
CIRCUIT = "no.stemmekrets"
SCHEMES = {
    COUNTY: (1, "administrative", "county", "fylke"),
    MUNICIPALITY: (2, "administrative", "municipality", "kommune"),
    SAMI: (1, "electoral_district", "Sámi Parliament constituency", None),
    CIRCUIT: (3, "precinct", "polling circuit", None),
}
# The service's name of each election type, the level that is one contest, and its descriptors.
CONTEST_LEVEL = {"ST": "fylke", "FY": "fylke", "KO": "kommune", "SA": "samevalgdistrikt"}
DAYS = {
    ("2009", "ST"): date(2009, 9, 14),
    ("2011", "FY"): date(2011, 9, 12),
    ("2011", "KO"): date(2011, 9, 12),
    ("2013", "SA"): date(2013, 9, 9),
    ("2013", "ST"): date(2013, 9, 9),
    ("2015", "FY"): date(2015, 9, 14),
    ("2015", "KO"): date(2015, 9, 14),
    ("2017", "KO"): date(2017, 9, 11),
    ("2017", "SA"): date(2017, 9, 11),
    ("2017", "ST"): date(2017, 9, 11),
    ("2019", "FY"): date(2019, 9, 9),
    ("2019", "KO"): date(2019, 9, 9),
    ("2021", "SA"): date(2021, 9, 13),
    ("2021", "ST"): date(2021, 9, 13),
    ("2023", "FY"): date(2023, 9, 11),
    ("2023", "KO"): date(2023, 9, 11),
    ("2025", "SA"): date(2025, 9, 8),
    ("2025", "ST"): date(2025, 9, 8),
}
# Elections that ran on a Sunday and a Monday: the first day is the day before.
TWO_DAYS = {2009, 2013, 2017, 2019, 2021, 2023, 2025}
ROLLUPS = {"Andre", "Andre2"}
BLANK = "BLANKE"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Unit:
    code: str
    name: str
    scheme: str
    kommune: str | None
    doc: Doc


@dataclass
class Contest:
    code: str
    name: str
    doc: Doc
    units: list[Unit] = field(default_factory=list)


@dataclass
class Parsed:
    year: str
    kind: str
    root: Doc
    contests: list[Contest]
    urls: list[str]
    counties: dict[str, str]
    towns: dict[str, str]


def _doc(year: str, kind: str, path: str) -> Doc:
    return load(year, kind, path)


def _num(value: object) -> int:
    return 0 if value is None else int(str(value))


@functools.cache
def parsed(year: str, kind: str) -> Parsed:
    base = f"/{year}/{kind.lower()}"
    root = _doc(year, kind, base)
    urls = [api_url(base)]
    contests: list[Contest] = []
    counties: dict[str, str] = {}
    towns: dict[str, str] = {}
    level = CONTEST_LEVEL[kind]

    def walk(path: str) -> Iterator[tuple[str, Doc]]:
        doc = _doc(year, kind, path)
        urls.append(api_url(path))
        yield path, doc
        for child in children(doc):
            yield from walk(child)

    for path, doc in walk(base):
        ident = doc["id"]
        assert isinstance(ident, dict)
        if ident["nivaa"] == "fylke":
            counties[str(ident["nr"])] = clean(str(ident["navn"]))
        if ident["nivaa"] == "kommune":
            towns[str(ident["nr"])] = clean(str(ident["navn"]))
        if ident["nivaa"] != level:
            continue
        contest = Contest(str(ident["nr"]), clean(str(ident["navn"])), doc)
        kids = children(doc)
        leaves = [(p, d) for k in kids for p, d in _leaves(year, kind, k, urls)] or [(path, doc)]
        for leaf_path, leaf in leaves:
            lid = leaf["id"]
            assert isinstance(lid, dict)
            parts = leaf_path.split("/")[3:]
            nivaa = str(lid["nivaa"])
            scheme = {"stemmekrets": CIRCUIT, "kommune": MUNICIPALITY, "samevalgdistrikt": SAMI}.get(nivaa)
            if scheme is None:
                raise ValueError(f"{leaf_path}: unit level {nivaa}")
            kommune = parts[-2] if nivaa == "stemmekrets" else str(lid["nr"]) if nivaa == "kommune" else None
            contest.units.append(Unit("/".join(parts), clean(str(lid["navn"])), scheme, kommune, leaf))
        contests.append(contest)
    return Parsed(year, kind, root, contests, list(dict.fromkeys(urls)), counties, towns)


def _leaves(year: str, kind: str, path: str, urls: list[str]) -> Iterator[tuple[str, Doc]]:
    doc = _doc(year, kind, path)
    urls.append(api_url(path))
    kids = children(doc)
    if not kids:
        yield path, doc
    for kid in kids:
        yield from _leaves(year, kind, kid, urls)


def stems(doc: Doc) -> dict[str, int]:
    box = doc["stemmer"]
    assert isinstance(box, dict)
    return {name: _num(value) for name, value in box.items()}


def voters(doc: Doc) -> dict[str, int]:
    box = doc.get("stemmegiving") or {}
    assert isinstance(box, dict)
    return {name: _num(value) for name, value in box.items()}


@dataclass
class Row:
    code: str
    name: str
    category: int
    total: int
    early: int


def lists(doc: Doc) -> list[Row]:
    rows = []
    for party in doc.get("partier", []):
        ident = party["id"]
        count = party["stemmer"]["resultat"]["antall"]
        rows.append(
            Row(
                str(ident["partikode"]),
                clean(str(ident["navn"])),
                int(ident["partikategori"]),
                _num(count["total"]),
                _num(count["fhs"]),
            )
        )
    return rows


def seats(doc: Doc) -> dict[str, int]:
    found = {}
    for party in doc.get("partier", []):
        won = _num(party["mandater"]["resultat"]["antall"])
        if won:
            found[str(party["id"]["partikode"])] = won
    return found


def options(doc: Doc) -> list[Row]:
    """The itemized lists of a document: every list except blank ballots and the authority's roll-up lines."""
    return [row for row in lists(doc) if row.code != BLANK and row.code not in ROLLUPS]


# Packages.

LICENSE = "Public election results of the Norwegian Directorate of Elections (Valgdirektoratet); no licence is stated"
METHOD = (
    "Counted by the municipalities and county election boards and published by the Norwegian Directorate of Elections "
    "(Valgdirektoratet) on valgresultat.no, from the final count. The authority counts blank ballots among the valid "
    "votes; here valid votes are the votes for lists and blank ballots are stored apart, so valid plus blank equals the "
    "authority's valid total. Rejected ballots are stored as invalid ballots where the authority publishes them. "
    "Registered voters are the electoral roll of the unit, and voters are the approved voter registrations "
    "(godkjente stemmegivninger) of the roll's unit, where published; a unit that pools votes not assigned to a "
    "circuit has a smaller roll than the votes it holds. A polling circuit that only counted early votes stores "
    "them on the early channel, and where the authority publishes early votes a circuit that counted none stores "
    "its votes on the election-day channel; any other unit stores its votes on all channels together."
)
ELECTIONS = {
    "ST": ("general", "{year} Norwegian parliamentary election", "Stortingsvalget {year}", "parliamentary"),
    "FY": ("local", "{year} Norwegian county council elections", "Fylkestingsvalgene {year}", "county"),
    "KO": ("local", "{year} Norwegian municipal elections", "Kommunestyrevalgene {year}", "municipal"),
    "SA": ("general", "{year} Sámi Parliament election", "Sametingsvalget {year}", "sami-parliament"),
}
SYSTEMS: dict[str, tuple[date, date | None, Doc, str]] = {
    "storting": (
        date(2005, 1, 1),
        None,
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.4,
            "threshold": {"percent": 4, "level": "party", "scope": "national"},
        },
        (
            "The Storting's 169 seats are allocated in 19 districts by the modified Sainte-Laguë method with a first "
            "divisor of 1.4. One seat in each district is a levelling seat, allocated nationally to bring the parties' "
            "totals closer to their national vote shares; only parties with at least 4% of the national vote take part in "
            "the levelling."
        ),
    ),
    "local": (
        date(2003, 1, 1),
        None,
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "County councils and municipal councils are elected by list proportional representation, with seats allocated "
            "by the modified Sainte-Laguë method with a first divisor of 1.4."
        ),
    ),
    "sami": (
        date(2009, 1, 1),
        None,
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "The 39 seats of the Sámi Parliament are elected in 17 constituencies, with seats allocated by the modified "
            "Sainte-Laguë method with a first divisor of 1.4."
        ),
    ),
}
SYSTEM_OF = {"ST": "storting", "FY": "local", "KO": "local", "SA": "sami"}
STORTING = key("body", "storting")
SAMEDIGGI = key("body", "sameting")


def system(name: str) -> str:
    return key("electoral_system", name)


def day_start(day: date) -> date:
    return day - timedelta(days=1) if day.year in TWO_DAYS else day


@functools.cache
def all_elections() -> list[tuple[str, str]]:
    return sorted(DAYS, key=lambda item: (DAYS[item], item[1]))


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in sorted(set(DAYS.values()))),
    ]


def party_key(kind: str, contest: str, row: Row) -> str:
    if row.category in (1, 2):
        return row.code
    return f"{kind}:{contest}:{row.code}"


def parse() -> None:
    geo().write()
    registry().write()
    for day in sorted(set(DAYS.values())):
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=VAL_SOURCE,
        kind="official_body",
        publisher="Valgdirektoratet",
        title="Valgresultat",
        url="https://valgresultat.no",
        license=LICENSE,
        attribution="Valgdirektoratet",
    )


def _places() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    counties: dict[str, str] = {}
    towns: dict[str, str] = {}
    sami: dict[str, str] = {}
    for year, kind in all_elections():
        data = parsed(year, kind)
        counties.update(data.counties)
        towns.update(data.towns)
        if kind == "SA":
            sami.update({c.code: c.name for c in data.contests})
    return counties, towns, sami


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Norway's counties (fylker), municipalities (kommuner), and Sámi Parliament constituencies as "
        "Valgdirektoratet codes them, each under its latest name",
    )
    p.external |= {NORWAY, COUNTRY}
    _source(p)
    for year, kind in all_elections():
        p.uses(document(year, kind, f"/{year}/{kind.lower()}"))
    for code, (rank, kind, noun, system_) in SCHEMES.items():
        p.add("scheme", code=code, polity_id=NORWAY, kind=kind, rank=rank, noun=noun, code_system=system_)
    counties, towns, sami = _places()
    # Codes that stood between reforms and ran in no election still belong in the crosswalk.
    for scheme, known in ((COUNTY, counties), (MUNICIPALITY, towns)):
        for change in kartverket.changes(scheme):
            for code, text in (*change.old, *change.new):
                known.setdefault(code, clean(text))

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "nb", "official")

    for code, text in sorted(counties.items()):
        place(COUNTY, code, text, COUNTRY)
    for code, text in sorted(towns.items()):
        county = code[:2]
        place(MUNICIPALITY, code, text, area(COUNTY, county) if county in counties else COUNTRY)
    for code, text in sorted(sami.items()):
        place(SAMI, code, text, COUNTRY)
    _shapes(p, counties, towns)
    _lineage(p, counties, towns)
    return p


def _shapes(p: Package, counties: dict[str, str], towns: dict[str, str]) -> None:
    """Kartverket's outlines of the counties and municipalities in force since the 2024 reform. Areas whose code
    ended before then keep no shape, because Kartverket publishes none for them."""
    p.add(
        "source",
        id=MAP_SOURCE,
        kind="official_body",
        publisher="Kartverket",
        title="Administrative enheter (Kommuneinfo)",
        url="https://api.kartverket.no/kommuneinfo/v1/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Kartverket",
    )
    for scheme, known in ((COUNTY, counties), (MUNICIPALITY, towns)):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, kartverket.VINTAGE.year),
            scheme_code=scheme,
            vintage=str(kartverket.VINTAGE.year),
            valid_from=kartverket.VINTAGE,
            as_of=kartverket.VINTAGE,
        )
        for shape in kartverket.shapes(scheme):
            if shape.code not in known:
                raise ValueError(f"Kartverket's {scheme} {shape.code} {shape.name} is in no election")
            p.artifact(shape.artifact, MAP_SOURCE)
            p.add(
                "area_geometry",
                area_id=area(scheme, shape.code),
                valid_from=kartverket.VINTAGE,
                geom=shapely.to_wkb(shape.geom, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape.geom), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=MAP_SOURCE,
            )
    p.uses(raw.cached(kartverket.KARTVERKET, kartverket.listing("fylker")))
    p.uses(raw.cached(kartverket.KARTVERKET, kartverket.listing("kommuner")))


def _lineage(p: Package, counties: dict[str, str], towns: dict[str, str]) -> None:
    """The crosswalk of codes: one event for every set of counties or municipalities whose codes changed on a day,
    from the 2018 mergers through the 2020 and 2024 reforms, with each old code as a `from` member and each new
    code as a `to` member."""
    p.add(
        "source",
        id=KLASS_SOURCE,
        kind="official_body",
        publisher="Statistics Norway",
        title="Standard for classification of municipalities and counties (KLASS)",
        url="https://www.ssb.no/en/klass/klassifikasjoner/131",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Statistics Norway",
    )
    for scheme, known in ((COUNTY, counties), (MUNICIPALITY, towns)):
        p.artifact(raw.cached(kartverket.KLASS, kartverket.changes_url(scheme)), KLASS_SOURCE)
        for change in kartverket.changes(scheme):
            members = [("from", code, text) for code, text in change.old] + [
                ("to", code, text) for code, text in change.new
            ]
            absent = [code for _, code, _ in members if code not in known]
            if absent:
                SKIPPED.append(f"{scheme} {change.day}: {', '.join(absent)}")
                continue
            id = p.id("area_event", scheme, change.day, *(f"{r}:{c}" for r, c, _ in members))
            old = ", ".join(f"{text} ({code})" for code, text in change.old)
            new = ", ".join(f"{text} ({code})" for code, text in change.new)
            p.add(
                "area_event",
                id=id,
                kind=change.kind,
                date=change.day,
                date_precision="day",
                source_id=KLASS_SOURCE,
                notes=f"{old} became {new}",
            )
            for role, code, _ in members:
                p.add("area_event_member", event_id=id, area_id=area(scheme, code), role=role)


def region_body(code: str) -> str:
    return key("body", "county", code)


def council(code: str) -> str:
    return key("body", "municipality", code)


def party(key_: str) -> str:
    return key("party", key_)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Norwegian parties and lists on the ballots from 2009 to 2025, the Storting, the Sámi Parliament, the county "
        "and municipal councils, and their electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NORWAY}
    p.add("body", id=STORTING, polity_id=NORWAY, kind="unicameral")
    p.name(STORTING, "Storting", "en", "official")
    p.name(STORTING, "Stortinget", "nb", "native", False)
    p.add("body", id=SAMEDIGGI, polity_id=NORWAY, kind="unicameral")
    p.name(SAMEDIGGI, "Sámi Parliament", "en", "official")
    p.name(SAMEDIGGI, "Sametinget", "nb", "native", False)
    counties, towns, _ = _places()
    for code, text in sorted(counties.items()):
        p.add("body", id=region_body(code), polity_id=NORWAY, kind="council")
        p.name(region_body(code), f"Fylkestinget, {text}", "nb", "official")
    for code, text in sorted(towns.items()):
        p.add("body", id=council(code), polity_id=NORWAY, kind="council")
        p.name(council(code), f"Kommunestyret, {text}", "nb", "official")
    for name, (since, until, rules, method) in SYSTEMS.items():
        id = system(name)
        p.add(
            "electoral_system", id=id, polity_id=NORWAY, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    seen: dict[str, list[tuple[date, str, int]]] = {}
    for year, kind in all_elections():
        for contest in parsed(year, kind).contests:
            for row in options(contest.doc):
                seen.setdefault(party_key(kind, contest.code, row), []).append(
                    (DAYS[(year, kind)], row.name, row.category)
                )
    for key_, found in sorted(seen.items()):
        id = party(key_)
        p.add("party", id=id, kind="party" if max(found)[2] in (1, 2) else "independent_group", polity_id=NORWAY)
        p.add("external_id", entity_id=id, scheme="no.valg.party", value=key_)
        latest = max(found)[1]
        p.name(id, latest, "nb", "official")
        for text in sorted({text for _, text, _ in found} - {latest}):
            p.name(id, text, "nb", "official", False)
    return p


NATIONAL: dict[str, Doc] = {}


def _channel(unit: Unit, early_published: bool) -> int:
    """The channel of all a unit's counted numbers: early (2) for a unit that only counted early votes, election day (1)
    for a polling circuit with none, else all (0), because a unit with both holds no split of its own."""
    doc = unit.doc
    cast = stems(doc)
    items = lists(doc)
    if (
        cast["fhs"] > 0
        and cast["fhs"] == cast["total"]
        and cast["fhsForkastede"] == cast["totalForkastede"]
        and all(r.early == r.total for r in items)
    ):
        return 2
    if early_published and unit.scheme == CIRCUIT and cast["fhs"] == 0 and all(r.early == 0 for r in items):
        return 1
    return 0


def _rows(
    unit: Unit, has_blank: bool, has_invalid: bool, has_voters: bool
) -> Iterator[tuple[str | None, int, int, int | None]]:
    """(list code or None, measure, total, early) of one unit; early is None for a roll figure."""
    doc = unit.doc
    cast = stems(doc)
    items = lists(doc)
    blank = next((r for r in items if r.code == BLANK), None)
    for row in options(doc):
        yield row.code, 1, row.total, row.early
    yield None, 4, cast["total"] - (blank.total if blank else 0), cast["fhs"] - (blank.early if blank else 0)
    if has_blank:
        yield None, 6, blank.total if blank else 0, blank.early if blank else 0
    if has_invalid:
        yield None, 5, cast["totalForkastede"], cast["fhsForkastede"]
    registered = _num(doc["antallsb"])
    if registered:
        yield None, 2, registered, None
    if has_voters:
        yield None, 14, voters(doc)["totalGodkjente"], None


def _flags(contest: Contest) -> tuple[bool, bool, bool]:
    doc = contest.doc
    return (
        any(r.code == BLANK for r in lists(doc)),
        stems(doc)["totalForkastede"] > 0,
        voters(doc).get("totalGodkjente", 0) > 0,
    )


def _is_aggregate(unit: Unit) -> bool:
    return (
        unit.scheme == CIRCUIT and re.search(r"uoppgitt|forh[åa]nds|ikke fordelt|ukjent", fold(unit.name)) is not None
    )


def election(day: date) -> Package:
    members = [item for item in all_elections() if DAYS[item] == day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Norway's elections of {day:%-d %B %Y} at the finest unit Valgdirektoratet publishes",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NORWAY, COUNTRY}
    unit_rows: dict[str, Unit] = {}
    facts: list[tuple[str, str, str | None, int, int, int]] = []
    declared: list[tuple[str, str | None, int, int]] = []
    for year, kind in members:
        data = parsed(year, kind)
        for url in data.urls:
            p.uses(raw.cached(shard(year, kind), url))
        p.artifact(document(year, kind, f"/{year}/{kind.lower()}"), VAL_SOURCE)
        word = ELECTIONS[kind][3]
        english, norwegian = ELECTIONS[kind][1], ELECTIONS[kind][2]
        kind_name = ELECTIONS[kind][0]
        election_id = key("election", day, kind)
        p.add(
            "election",
            id=election_id,
            polity_id=NORWAY,
            kind=kind_name,
            date_from=day_start(day),
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(election_id, english.format(year=year), "en", "official")
        p.name(election_id, norwegian.format(year=year), "nb", "native", False)
        p.add("slug", path=f"no/{day}-{word}", entity_id=election_id, is_current=True)
        has_blank = any(_flags(c)[0] for c in data.contests)
        has_invalid = stems(data.root)["totalForkastede"] > 0
        has_voters = voters(data.root).get("totalGodkjente", 0) > 0
        names = [changeset.slug(c.name) for c in data.contests]
        totals: dict[str, int] = {}
        for contest in data.contests:
            slug_word = changeset.slug(contest.name)
            if names.count(slug_word) > 1:
                slug_word = f"{slug_word}-{contest.code}"
            contest_id = key("contest", day, kind, contest.code)
            won = seats(contest.doc)
            magnitude = _num(contest.doc["mandater"]["antall"])
            district = {"ST": COUNTY, "FY": COUNTY, "KO": MUNICIPALITY, "SA": SAMI}[kind]
            body = {
                "ST": STORTING,
                "SA": SAMEDIGGI,
                "FY": region_body(contest.code),
                "KO": council(contest.code),
            }[kind]
            p.add(
                "contest",
                id=contest_id,
                election_id=election_id,
                kind="list_allocation",
                body_id=body,
                district_area_id=area(district, contest.code),
                magnitude=magnitude or None,
                round_number=1,
                electoral_system_id=system(SYSTEM_OF[kind]),
                default_result_kind="counted",
            )
            p.name(contest_id, contest.name, "nb", "official")
            p.add("slug", path=f"no/{day}-{word}/{slug_word}", entity_id=contest_id, is_current=True)
            set_id = key("result_set", day, kind, contest.code)
            p.add(
                "result_set",
                id=set_id,
                election_id=election_id,
                contest_id=contest_id,
                stage="official",
                kind="counted",
                source_id=VAL_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add("note", id=key("note", set_id), entity_id=set_id, kind="methodology", body_md=METHOD, lang="en")
            option_ids: dict[str, str] = {}
            for position, row in enumerate(options(contest.doc), start=1):
                option_id = option_ids[row.code] = key("option", day, kind, contest.code, row.code)
                p.add(
                    "contest_option",
                    id=option_id,
                    contest_id=contest_id,
                    kind="list",
                    party_id=party(party_key(kind, contest.code, row)),
                    ballot_position=None,
                    ballot_label=row.name,
                )
                if row.code in won:
                    p.add("outcome", result_set_id=set_id, option_id=option_id, kind="won", seats=won[row.code])
            for unit in contest.units:
                channel = _channel(unit, stems(data.root)["fhs"] > 0)
                unit_rows.setdefault(unit.code, unit)
                unit_id = key("unit", day, unit.code)
                over = _num(unit.doc["antallsb"]) and voters(unit.doc).get("totalGodkjente", 0) > _num(
                    unit.doc["antallsb"]
                )
                if over:
                    p.accept(
                        "accounting.turnout",
                        "The authority's roll for a unit that pools early votes not assigned to a polling circuit, or "
                        "a circuit voters from other circuits used, is smaller than the approved voter registrations "
                        "the same unit reports.",
                    )
                for code, measure, total, early in _rows(unit, has_blank, has_invalid, has_voters):
                    option_id = None if code is None else option_ids[code]
                    facts.append((set_id, unit_id, option_id, 0 if early is None else channel, measure, total))
            # The authority's own contest totals, which the unit values must add up to.
            for row in options(contest.doc):
                declared.append((set_id, option_ids[row.code], 1, row.total))
            doc = contest.doc
            blank = next((r.total for r in lists(doc) if r.code == BLANK), 0)
            declared.append((set_id, None, 4, stems(doc)["total"] - blank))
            if has_blank:
                declared.append((set_id, None, 6, blank))
            if has_invalid:
                declared.append((set_id, None, 5, stems(doc)["totalForkastede"]))
            if _num(doc["antallsb"]):
                declared.append((set_id, None, 2, _num(doc["antallsb"])))
            if has_voters:
                declared.append((set_id, None, 14, voters(doc)["totalGodkjente"]))
            totals["valid"] = totals.get("valid", 0) + stems(doc)["total"]
        root = data.root
        NATIONAL[f"{year}-{kind}"] = {
            "valid": totals["valid"],
            "published": stems(root)["total"],
            "registered": sum(_num(c.doc["antallsb"]) for c in data.contests),
            "published_registered": _num(root["antallsb"]),
            "contests": len(data.contests),
            "units": sum(len(c.units) for c in data.contests),
        }
    units = pl.DataFrame(
        [
            (key("unit", day, u.code), u.scheme, f"{day}:{u.code}", u.kommune, u.name, _is_aggregate(u))
            for u in unit_rows.values()
        ],
        schema={
            "id": pl.String,
            "scheme": pl.String,
            "code": pl.String,
            "kommune": pl.String,
            "name": pl.String,
            "aggregate": pl.Boolean,
        },
        orient="row",
    )
    p.frame(
        "reporting_unit",
        units.select(
            "id",
            polity_id=pl.lit(NORWAY),
            scheme_code="scheme",
            code="code",
            area_relation=pl.when(pl.col("scheme") == CIRCUIT).then(pl.lit("within")).otherwise(pl.lit("equals")),
            area_id=pl.struct("scheme", "code", "kommune").map_elements(
                lambda r: area(
                    CIRCUIT if False else MUNICIPALITY if r["scheme"] in (CIRCUIT, MUNICIPALITY) else SAMI,
                    r["kommune"] or r["code"].split(":", 1)[1],
                ),
                return_dtype=pl.String,
            ),
            kind=pl.when("aggregate").then(pl.lit("aggregate")).otherwise(pl.lit("standard")),
        ),
    )
    for id, name in units.select("id", "name").iter_rows():
        p.name(id, name, "nb", "official")
    p.frame(
        "result_value",
        pl.DataFrame(
            facts,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    p.frame(
        "declared",
        pl.DataFrame(
            declared,
            schema={"result_set_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
            orient="row",
        ),
    )
    return p
