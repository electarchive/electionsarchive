"""Bosnia and Herzegovina's elections from the Central Election Commission (CIK BiH).

The Commission's results application reads a JSON API, one database per set of certified results. Each race has its
own family of endpoints: the lists of entities, constituencies (electoral unit parents), municipalities (electoral
units), and polling stations, and for each level the turnout ("basic info") and the votes of the parties or the
candidates. This module walks every race of every certified database down to the polling station.
"""

import functools
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ba.cik"
BIH = str(uuid5(package.NAMESPACE, "polity\x1fBA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBIH"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
API = "https://www.izbori.ba/api_2018"
LANG = 1  # the API's language number for the Bosnian Latin labels


@dataclass(frozen=True)
class Results:
    """One certified results database: the election it belongs to and the races it holds."""

    id: int  # the API's electionResultId
    day: date
    slug: str
    kind: str  # general, local, by_election
    title: str


RESULTS = [
    Results(13, date(2016, 10, 2), "2016-10-02", "local", "2016 Bosnian local elections"),
    Results(14, date(2017, 2, 19), "2017-02-19", "by_election", "2017 Stolac repeat local election"),
    Results(15, date(2017, 9, 24), "2017-09-24", "by_election", "2017 Trnovo mayoral by-election"),
    Results(17, date(2017, 12, 17), "2017-12-17", "by_election", "2017 Glamoč mayoral by-election"),
    Results(25, date(2018, 10, 7), "2018-10-07", "general", "2018 Bosnian general election"),
    Results(26, date(2019, 2, 17), "2019-02-17", "by_election", "2019 mayoral by-elections"),
    Results(27, date(2020, 11, 15), "2020-11-15", "local", "2020 Bosnian local elections"),
    Results(28, date(2020, 12, 20), "2020-12-20", "local", "2020 Mostar city council election"),
    Results(29, date(2021, 2, 21), "2021-02-21", "local", "2020 local elections in Srebrenica and Doboj (repeat)"),
    Results(30, date(2021, 4, 11), "2021-04-11", "by_election", "2021 Travnik and Foča mayoral by-elections"),
    Results(31, date(2021, 12, 12), "2021-12-12", "by_election", "2021 Prijedor mayoral by-election"),
    Results(32, date(2022, 10, 2), "2022-10-02", "general", "2022 Bosnian general election"),
    Results(33, date(2023, 2, 5), "2023-02-05", "by_election", "2023 mayoral by-elections"),
    Results(34, date(2023, 5, 28), "2023-05-28", "by_election", "2023 Živinice mayoral by-election"),
    Results(35, date(2023, 11, 5), "2023-11-05", "by_election", "2023 Stari Grad Sarajevo mayoral by-election"),
    Results(36, date(2024, 10, 6), "2024-10-06", "local", "2024 Bosnian local elections"),
    Results(39, date(2025, 11, 23), "2025-11-23", "by_election", "2025 Republika Srpska presidential election"),
    Results(41, date(2026, 2, 15), "2026-02-15", "by_election", "2026 Vareš mayoral by-election"),
]
MOSTAR = "199"
MOSTAR_UNITS = {code: MOSTAR for code in ("151", "152", "153", "154", "155", "156")}  # the city's electoral areas
CANDIDATE_RACES = {"1", "5", "8"}  # races whose ballots name candidates; the others are party lists
SLASHED = {"2", "8", "9"}  # races whose basic-info endpoints the application calls with a trailing slash


def api(path: str) -> str:
    return f"{API}/{path}"


def database_url(r: Results) -> str:
    return api(f"administration_electionresultcontroller_getdatabasename/{r.id}/")


def races_url(r: Results) -> str:
    return api(f"administration_racecontroller_getbyelectionresultId/{r.id}/{LANG}")


def load(url: str) -> Any:
    return json.loads(raw.cached(SOURCE, url).path.read_bytes())


def maybe(url: str) -> Any:
    try:
        return load(url)
    except LookupError:
        return None


def database(r: Results) -> str:
    return str(load(database_url(r)))


def races(r: Results) -> list[str]:
    return [str(race["code"]).strip() for race in load(races_url(r))]


def entities_url(db: str, race: str) -> str:
    return api(f"race{race}_entity/{db}/{LANG}")


def parents_url(db: str, race: str, entity: int | None) -> str:
    if race == "2":
        return api(f"race2_electoralunitparent/{db}/{entity}/{LANG}")
    if race == "7":
        return api(f"race7_electoralunitparent/{db}/{LANG}")
    return api(f"race{race}_electoralunitparent/{db}/0/{LANG}")


def units_url(db: str, race: str, parent: int | None) -> str:
    if race == "1":
        return api(f"race1_electoralunit/{db}/{LANG}/{parent}")
    if race in {"5", "8", "9"}:
        return api(f"race{race}_electoralunit/{db}/{LANG}")
    return api(f"race{race}_electoralunit/{db}/{parent}/{LANG}")


def stations_url(db: str, race: str, unit: int) -> str:
    return api(f"race{race}_pollingstation/{db}/{unit}/{LANG}")


def station_info_url(db: str, race: str, station: int) -> str:
    return api(f"race{race}_pollingstationsbasicinfo/{db}/{station}" + ("/" if race in SLASHED else ""))


def station_votes_url(db: str, race: str, station: int) -> str:
    what = "candidatesresult" if race in CANDIDATE_RACES else "partyresult"
    return api(f"race{race}_pollingstations{what}/{db}/{station}/{LANG}")


def station_minorities_url(db: str, station: int) -> str:
    return api(f"race9_pollingstationsnationalminoritiesresult/{db}/{station}/{LANG}")


def unit_minorities_url(db: str, unit: int) -> str:
    return api(f"race9_electoralunitnationalminoritiesresult/{db}/{unit}/{LANG}")


def unit_info_url(db: str, race: str, unit: int) -> str:
    return api(f"race{race}_electoralunitbasicinfo/{db}/{unit}" + ("/" if race in SLASHED else ""))


def unit_votes_url(db: str, race: str, unit: int) -> str:
    what = "candidatesresult" if race in CANDIDATE_RACES else "partyresult"
    return api(f"race{race}_electoralunit{what}/{db}/{unit}/{LANG}")


def parent_info_url(db: str, race: str, parent: int) -> str:
    return api(f"race{race}_electoralunitparentbasicinfo/{db}/{parent}" + ("/" if race in SLASHED else ""))


def parent_votes_url(db: str, race: str, parent: int) -> str:
    return api(f"race{race}_electoralunitparentpartyresult/{db}/{parent}/{LANG}")


def entity_info_url(db: str, race: str, entity: int) -> str:
    return api(f"race{race}_entitybasicinfo/{db}/{entity}" + ("/" if race in SLASHED else ""))


def entity_votes_url(db: str, race: str, entity: int) -> str:
    return api(f"race{race}_entitypartyresult/{db}/{entity}/{LANG}")


def race_info_url(db: str, race: str) -> str:
    return api(f"race{race}_basicinfo/{db}")


def race_extra_urls(db: str, race: str) -> list[str]:
    """The race-wide files that carry declared seats or candidate totals."""
    if race == "2":
        return [api(f"race2_entitypartygetallwithmandates/{db}/{LANG}")]
    if race in {"4", "6"}:
        return [api(f"race{race}_electoralunitparentpartyresultmandates/{db}/{LANG}")]
    if race == "5":
        return [api(f"race5_candidatesresult/{db}/{LANG}")]
    return []


@dataclass(frozen=True)
class Tree:
    """A race's places as the API lists them: entities, constituencies, municipalities, and polling stations."""

    entities: list[dict[str, Any]]
    parents: dict[int | None, list[dict[str, Any]]]  # entity id (or None) to constituencies
    units: dict[int | None, list[dict[str, Any]]]  # constituency or entity id (or None) to municipalities
    stations: dict[int, list[dict[str, Any]]]  # municipality id to polling stations


def _get(urls: list[str]) -> dict[str, Any]:
    fetched = raw.fetch_many(SOURCE, urls, suffix=".json", workers=24)
    out: dict[str, Any] = {}
    for url, artifact in fetched.items():
        try:
            out[url] = json.loads(artifact.path.read_bytes())
        except ValueError:
            out[url] = None
    return out


def _list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []


def tree(db: str, race: str, *, fetching: bool) -> Tree:
    get = _get if fetching else (lambda urls: {url: maybe(url) for url in urls})
    entities: list[dict[str, Any]] = []
    parents: dict[int | None, list[dict[str, Any]]] = {}
    units: dict[int | None, list[dict[str, Any]]] = {}
    if race in {"1", "2"}:
        entities = _list(get([entities_url(db, race)])[entities_url(db, race)])
    if race == "1":
        ids = [e["entityId"] for e in entities]
        found = get([units_url(db, race, i) for i in ids])
        units = {i: _list(found[units_url(db, race, i)]) for i in ids}
    elif race in {"5", "8", "9"}:
        units = {None: _list(get([units_url(db, race, None)])[units_url(db, race, None)])}
    else:
        keys: list[int | None] = [e["entityId"] for e in entities] if race == "2" else [None]
        found = get([parents_url(db, race, k) for k in keys])
        parents = {k: _list(found[parents_url(db, race, k)]) for k in keys}
        ids = [p["electoralUnitParentId"] for ps in parents.values() for p in ps]
        found = get([units_url(db, race, i) for i in ids])
        units = {i: _list(found[units_url(db, race, i)]) for i in ids}
    unit_ids = sorted({u["electoralUnitId"] for us in units.values() for u in us})
    found = get([stations_url(db, race, i) for i in unit_ids])
    stations = {i: _list(found[stations_url(db, race, i)]) for i in unit_ids}
    return Tree(entities, parents, units, stations)


def aggregate_urls(db: str, race: str, t: Tree) -> list[str]:
    urls = [race_info_url(db, race), *race_extra_urls(db, race)]
    for e in t.entities:
        urls += [entity_info_url(db, race, e["entityId"])]
        if race != "1":
            urls += [entity_votes_url(db, race, e["entityId"])]
    for ps in t.parents.values():
        for p in ps:
            urls += [parent_info_url(db, race, p["electoralUnitParentId"])]
            urls += [parent_votes_url(db, race, p["electoralUnitParentId"])]
    for us in t.units.values():
        for u in us:
            urls += [unit_info_url(db, race, u["electoralUnitId"]), unit_votes_url(db, race, u["electoralUnitId"])]
            if race == "9":
                urls += [unit_minorities_url(db, u["electoralUnitId"])]
    return list(dict.fromkeys(urls))


def members_url(db: str, entity: int) -> str:
    return api(f"race1_memberpresidency/{db}/{entity}/-1/{LANG}")


def member_votes_url(db: str, member: int) -> str:
    return api(f"race1_memberpresidencycandidatesresult/{db}/{member}/{LANG}")


def members(db: str, t: Tree, *, fetching: bool) -> dict[int, list[dict[str, Any]]]:
    """The Presidency's member seats in each entity, from the race's member files."""
    get = _get if fetching else (lambda urls: {url: maybe(url) for url in urls})
    ids = [e["entityId"] for e in t.entities]
    found = get([members_url(db, i) for i in ids])
    out = {i: _list(found[members_url(db, i)]) for i in ids}
    get([member_votes_url(db, m["memberPresidencyId"]) for ms in out.values() for m in ms])
    return out


def station_urls(db: str, race: str, t: Tree) -> list[str]:
    ids = sorted({s["pollingStationId"] for ss in t.stations.values() for s in ss})
    urls = [url for i in ids for url in (station_info_url(db, race, i), station_votes_url(db, race, i))]
    return urls + ([station_minorities_url(db, i) for i in ids] if race == "9" else [])


def fetch(only: str | None = None) -> None:
    for r in RESULTS:
        if only and r.slug != only:
            continue
        raw.fetch(SOURCE, database_url(r), suffix=".json")
        raw.fetch(SOURCE, races_url(r), suffix=".json")
        db = database(r)
        for race in races(r):
            t = tree(db, race, fetching=True)
            _get(aggregate_urls(db, race, t))
            if race == "1":
                members(db, t, fetching=True)
            urls = station_urls(db, race, t)
            _get(urls)
            print(f"{SOURCE}: {r.slug} race {race}: {len(urls):,} station files", flush=True)


# Reading one certified database into contests.

ENTITY_NAMES = {1: "Federation of Bosnia and Herzegovina", 2: "Republika Srpska", 3: "Brčko District"}
CHANNEL_KINDS = {3: "postal_district", 6: "mobile", 5: "aggregate"}
CHANNELS = (
    ("postOfficeVotes", 3, "postal"),
    ("absenceAndMobileTeamVotes", 6, "absentee and mobile"),
    ("confirmedVotes", 5, "tendered"),
)


@dataclass
class Option:
    label: str
    code: str | None  # the party's certification number, for candidates their party's
    votes: int
    channels: dict[int, int]  # channel code to votes counted centrally
    seats: int | None = None
    elected: bool = False
    minority: bool = False  # a candidate for a council seat reserved for national minorities


@dataclass
class Contest:
    race: str
    key: str  # unique within the database
    name: str  # as the Commission titles the district
    district: tuple[str, str] | None  # (scheme, code) of the contest's area
    options: list[Option]
    info: dict[str, Any]  # the district's accounting as printed
    stations: list[tuple[int, str, str]]  # polling station id, code, and municipality code
    municipalities: list[tuple[str, str]]  # (code, name) of the municipalities the district spans
    magnitude: int | None = None
    compensation: bool = False


def label(row: dict[str, Any]) -> str:
    return " ".join(str(row["name"]).split())


def _options(rows: Any, *, candidates: bool, compensated: bool = True) -> list[Option]:
    out = []
    for row in _list(rows):
        channels = {code: int(row[field]) for field, code, _ in CHANNELS if row.get(field)}
        seats = row.get("regularMandates")
        if seats is not None:
            seats += (row.get("compensationMandates") or 0) if compensated else 0
        else:
            seats = row.get("mandates") if row.get("mandates") is not None else row.get("totalMandates")
        out.append(
            Option(
                label(row),
                (str(row["code"]).strip() or None) if row.get("code") else None,
                int(row.get("totalVotes") or 0),
                channels,
                None if candidates else int(seats or 0),
                bool(row.get("haveMandates") or row.get("haveManadates")) if candidates else False,
            )
        )
    return out


def _stations(t: Tree, units: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    return [
        (
            s["pollingStationId"],
            str(s["code"]).strip(),
            MOSTAR_UNITS.get(str(u["code"]).strip(), str(u["code"]).strip()),
        )
        for u in units
        for s in t.stations.get(u["electoralUnitId"], [])
    ]


def _munis(units: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if any(str(u["code"]).strip() in MOSTAR_UNITS or str(u["code"]).strip() == MOSTAR for u in units):
        return [(MOSTAR, "MOSTAR")]
    return [(str(u["code"]).strip(), label(u)) for u in units]


def contests(r: Results) -> list[Contest]:
    db = database(r)
    out: list[Contest] = []
    for race in races(r):
        t = tree(db, race, fetching=False)
        if race == "1":
            for entity, ms in members(db, t, fetching=False).items():
                units = t.units.get(entity, [])
                options = [
                    o
                    for m in ms
                    for o in _options(maybe(member_votes_url(db, m["memberPresidencyId"])), candidates=True)
                ]
                out.append(
                    Contest(
                        race,
                        f"1:{entity}",
                        ENTITY_NAMES[entity],
                        ("ba.entity", str(entity)),
                        options,
                        maybe(entity_info_url(db, race, entity)) or {},
                        _stations(t, units),
                        _munis(units),
                        magnitude=len(ms),
                    )
                )
        elif race == "5":
            units = t.units.get(None, [])
            options = _options(maybe(race_extra_urls(db, race)[0]), candidates=True)
            out.append(
                Contest(
                    race,
                    "5",
                    ENTITY_NAMES[2],
                    ("ba.entity", "2"),
                    options,
                    maybe(race_info_url(db, race)) or {},
                    _stations(t, units),
                    _munis(units),
                    magnitude=max(1, sum(o.elected for o in options)),
                )
            )
        elif race in {"8", "9"}:
            for u in t.units.get(None, []):
                i = u["electoralUnitId"]
                info = maybe(unit_info_url(db, race, i)) or {}
                if not info.get("processedPollingStations"):
                    continue  # no results certified here, as in Stolac's annulled 2016 election
                options = _options(maybe(unit_votes_url(db, race, i)), candidates=race == "8")
                if race == "9":
                    for o in _options(maybe(unit_minorities_url(db, i)), candidates=True):
                        o.minority = True
                        options.append(o)
                out.append(
                    Contest(
                        race,
                        f"{race}:{i}",
                        label(u),
                        ("ba.constituency", f"9:{u['code']}".strip())
                        if str(u["code"]).strip() in MOSTAR_UNITS
                        else ("ba.municipality", str(u["code"]).strip()),
                        options,
                        info,
                        _stations(t, [u]),
                        _munis([u]),
                        magnitude=1 if race == "8" else None,
                    )
                )
        else:
            for entity, parents in t.parents.items():
                for parent in parents:
                    i = parent["electoralUnitParentId"]
                    units = t.units.get(i, [])
                    out.append(
                        Contest(
                            race,
                            f"{race}:{i}",
                            label(parent),
                            ("ba.constituency", f"{race}:{parent['code']}"),
                            _options(maybe(parent_votes_url(db, race, i)), candidates=False, compensated=race != "2"),
                            maybe(parent_info_url(db, race, i)) or {},
                            _stations(t, units),
                            _munis(units),
                        )
                    )
            out += compensation(db, race, t)
    return out


def compensation(db: str, race: str, t: Tree) -> list[Contest]:
    """The compensation seats a race allocates over a whole entity, as contests counted at the entity level."""
    if race == "2":
        files = {e["entityId"]: maybe(entity_votes_url(db, race, e["entityId"])) for e in t.entities}
        out = []
        for entity, file in files.items():
            options = _options(file, candidates=False)
            for o, row in zip(options, _list(file), strict=True):
                o.seats = int(row.get("compensationMandates") or 0)
            if sum(o.seats or 0 for o in options):
                out.append(
                    Contest(
                        race,
                        f"2:c{entity}",
                        ENTITY_NAMES[entity],
                        ("ba.entity", str(entity)),
                        options,
                        maybe(entity_info_url(db, race, entity)) or {},
                        [],
                        [],
                        compensation=True,
                    )
                )
        return out
    return []


# Packages.

RACES = {
    "1": ("Presidency of Bosnia and Herzegovina", "Predsjedništvo Bosne i Hercegovine"),
    "2": (
        "House of Representatives of the Parliamentary Assembly of Bosnia and Herzegovina",
        "Predstavnički dom Parlamentarne skupštine Bosne i Hercegovine",
    ),
    "4": (
        "House of Representatives of the Parliament of the Federation of Bosnia and Herzegovina",
        "Predstavnički dom Parlamenta Federacije Bosne i Hercegovine",
    ),
    "5": ("President of Republika Srpska", "Predsjednik Republike Srpske"),
    "6": ("National Assembly of Republika Srpska", "Narodna skupština Republike Srpske"),
    "7": ("Cantonal assembly", "Skupština kantona"),
    "8": ("Mayor", "Načelnik/gradonačelnik"),
    "9": ("Municipal council", "Općinsko vijeće/skupština opštine"),
}
METHOD = (
    "Votes by polling station as the Central Election Commission's results application publishes them in its "
    "certified results. The Commission counts postal ballots, absentee and mobile-team ballots, and tendered "
    "(confirmed) ballots centrally and reports them only for the whole district, by option; each district has one "
    "unit for them, with the option votes split into those three channels and the district's remaining valid, "
    "blank, and other invalid ballots. The district totals the Commission prints are stored as declared."
)
COMPENSATION = (
    "Compensation seats are allocated over the whole entity from the entity-wide list votes, so this contest is "
    "counted only at the entity level, with the votes and seats the Commission prints for the entity."
)


def package_key(r: Results) -> str:
    return f"{SOURCE}/{r.slug}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(r) for r in RESULTS)]


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def title(text: str) -> str:
    return " ".join(w.capitalize() if len(w) > 2 or i == 0 else w.lower() for i, w in enumerate(text.split()))


@functools.cache
def everything() -> dict[int, list[Contest]]:
    out: dict[int, list[Contest]] = {}
    for r in RESULTS:
        try:
            out[r.id] = contests(r)
        except LookupError:
            out[r.id] = []
    return out


def entity_of() -> dict[str, int]:
    """Each municipality's entity, from the general elections' Presidency races (Brčko District is its own)."""
    out: dict[str, int] = {}
    for r in RESULTS:
        for c in everything()[r.id]:
            if c.race == "1" and c.district:
                for code, _ in c.municipalities:
                    out.setdefault(code, int(c.district[1]))
    out["200"] = 3
    return out


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Centralna izborna komisija Bosne i Hercegovine",
        title="Certified election results",
        url="https://www.izbori.ba/rezultati_izbora",
        license="Official election results of Bosnia and Herzegovina, published for public use",
        license_url="https://www.izbori.ba/",
        attribution="Central Election Commission of Bosnia and Herzegovina, izbori.ba",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bosnia and Herzegovina's entities, municipalities, and electoral districts as the Central Election "
        "Commission's certified results name them, and its polling station scheme",
    )
    p.external |= {BIH, COUNTRY}
    source_row(p)
    for code, rank, kind, noun in (
        ("ba.entity", 1, "administrative", "entity"),
        ("ba.municipality", 2, "administrative", "municipality"),
        ("ba.constituency", 2, "electoral_district", "electoral district"),
        ("ba.station", 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=BIH, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, text in ENTITY_NAMES.items():
        id = area("ba.entity", str(code))
        p.add("area", id=id, scheme_code="ba.entity")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "en", "official")
    entities = entity_of()
    names: dict[str, str] = {}
    districts: dict[str, tuple[str, str]] = {}
    for r in RESULTS:
        for c in everything()[r.id]:
            for code, text in c.municipalities:
                if c.race in {"1", "2"} or code not in names:
                    names[code] = text
            if c.district and c.district[0] == "ba.constituency":
                districts[c.district[1]] = (c.name, c.race)
    for code, text in sorted(names.items()):
        id = area("ba.municipality", code)
        p.add("area", id=id, scheme_code="ba.municipality")
        p.add("area_code", area_id=id, code=code)
        entity = entities.get(code)
        p.add("area_parent", area_id=id, parent_id=area("ba.entity", str(entity)) if entity else COUNTRY)
        p.name(id, title(text), "bs", "official")
    for code, (text, race) in sorted(districts.items()):
        id = area("ba.constituency", code)
        p.add("area", id=id, scheme_code="ba.constituency")
        p.add("area_code", area_id=id, code=code)
        if race == "9":
            parent = area("ba.municipality", MOSTAR)
        else:
            entity = {"4": 1, "6": 2, "7": 1}.get(race) or (1 if text.endswith("A") else 2)
            parent = area("ba.entity", str(entity))
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, f"{title(text)} ({RACES[race][0]})", "en", "official")
        p.name(id, title(text), "bs", "native", False)
    return p


def body(race: str, district: str | None) -> str | None:
    if race in {"2", "4", "6"}:
        return key("body", race)
    if race == "7":
        return key("body", race, district)
    if race == "9":
        return key("body", race, council(district))
    return None


def council(district: str | None) -> str | None:
    """The municipality whose council a race 9 district elects; Mostar's city areas elect part of its council."""
    if district and district.startswith("9:"):
        return MOSTAR
    return district


def office(race: str, district: str | None) -> str | None:
    if race in {"1", "5"}:
        return key("office", race)
    if race == "8":
        return key("office", race, district)
    return None


def party(code: str) -> str:
    return key("party", code)


def list_parties() -> dict[str, Counter[str]]:
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for r in RESULTS:
        for c in everything()[r.id]:
            if c.race not in CANDIDATE_RACES:
                for o in c.options:
                    if o.code:
                        out[o.code][o.label] += 1
    return out


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bosnia and Herzegovina's legislatures, councils, and elected offices, and the parties and lists on the "
        "Central Election Commission's ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BIH}
    municipalities: dict[str, str] = {}
    cantons: dict[str, str] = {}
    for r in RESULTS:
        for c in everything()[r.id]:
            if c.race in {"8", "9"}:
                for code, text in c.municipalities:
                    municipalities[code] = text
            if c.race == "7" and c.district:
                cantons[c.district[1]] = c.name
    for race, kind in (("2", "lower_house"), ("4", "lower_house"), ("6", "unicameral")):
        p.add("body", id=key("body", race), polity_id=BIH, kind=kind)
        p.name(key("body", race), RACES[race][0], "en", "official")
        p.name(key("body", race), RACES[race][1], "bs", "native", False)
    for code, text in sorted(cantons.items()):
        id = key("body", "7", code)
        p.add("body", id=id, polity_id=BIH, kind="unicameral")
        p.name(id, f"Assembly of {title(text)}", "en", "official")
        p.name(id, f"Skupština {title(text)}", "bs", "native", False)
    for code, text in sorted(municipalities.items()):
        id = key("body", "9", code)
        p.add("body", id=id, polity_id=BIH, kind="council")
        p.name(id, f"Municipal council of {title(text)}", "en", "official")
        id = key("office", "8", code)
        p.add("office", id=id, polity_id=BIH, kind="mayor")
        p.name(id, f"Mayor of {title(text)}", "en", "official")
    p.add("office", id=key("office", "1"), polity_id=BIH, kind="head_of_state")
    p.name(key("office", "1"), "Member of the Presidency of Bosnia and Herzegovina", "en", "official")
    p.name(key("office", "1"), "Član Predsjedništva Bosne i Hercegovine", "bs", "native", False)
    p.add("office", id=key("office", "5"), polity_id=BIH, kind="other")
    p.name(key("office", "5"), RACES["5"][0], "en", "official")
    p.name(key("office", "5"), RACES["5"][1], "bs", "native", False)
    for code, spellings in sorted(list_parties().items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=BIH)
        p.add("external_id", entity_id=id, scheme="ba.cik.party", value=code)
        best = spellings.most_common(1)[0][0]
        for text in sorted(spellings):
            p.name(id, text, "bs", "official", text == best)
    return p


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "channel", "measure", "value")
        }

    def add(
        self, result_set: str, unit: str, option: str | None, measure: int, value: object, channel: int = 0
    ) -> None:
        if value is None:
            return
        row = (result_set, unit, option, channel, measure, int(str(value)))
        for column, item in zip(self.columns.values(), row, strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


ACCOUNTING = (
    (REGISTERED, "numberOfVoters"),
    (BALLOTS, "totalVotes"),
    (VALID, "validVotes"),
    (BLANK, "invalidBlankBallots"),
    (INVALID, "invalidOthersBallots"),
)


def words(text: str) -> frozenset[str]:
    """A candidate's name as a set of words, since station files print surname first and district files last."""
    return frozenset(candidate(text)[0].split())


def candidate(text: str) -> tuple[str, str | None]:
    """A candidate's name and the party printed after it ("NAME - PARTY")."""
    name, _, rest = text.partition(" - ")
    return name.strip(), rest.strip() or None


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    values: int = 0
    contests: int = 0


def contest_name(r: Results, c: Contest) -> tuple[str, str]:
    en, bs = RACES[c.race]
    place = title(c.name)
    if c.race == "1":
        return f"{en}, members elected in the {ENTITY_NAMES[int(c.key[2:])]}", f"{bs}, {place}"
    if c.race == "5":
        return en, bs
    if c.compensation:
        return f"{en}, compensation seats in the {ENTITY_NAMES[int(c.key[3:])]}", f"{bs}, kompenzacijski mandati"
    if c.race == "8":
        return f"Mayor of {place}", f"{bs} {place}"
    if c.race == "9" and c.district and council(c.district[1]) == MOSTAR:
        return f"Mostar City Council, {place}", f"Gradsko vijeće Grada Mostara, {place}"
    if c.race == "9":
        return f"Municipal council of {place}", f"{bs} {place}"
    if c.race == "7":
        return f"Assembly of {place}", f"Skupština {place}"
    return f"{en}, {place}", f"{bs}, {place}"


def election(r: Results, report: Report) -> Package:
    db = database(r)
    p = Package(
        package_key(r),
        SOURCE,
        f"The {r.title} by polling station from the Central Election Commission's certified results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BIH}
    parties = set(list_parties())
    election_id = key("election", r.slug)
    p.add("election", id=election_id, polity_id=BIH, kind=r.kind, date_from=r.day, date_to=r.day, status="held")
    p.name(election_id, r.title, "en", "official")
    path = f"ba/{r.slug}-{changeset.slug(r.kind)}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    files: set[str] = {database_url(r), races_url(r)}
    values = Facts()
    units: dict[str, str] = {}

    def unit(code: str, kind: str, place: str) -> str:
        id = key("unit", r.slug, code)
        if code not in units:
            units[code] = id
            p.add(
                "reporting_unit",
                id=id,
                polity_id=BIH,
                scheme_code="ba.station",
                code=f"{r.slug}:{code}",
                area_relation="within",
                area_id=place,
                kind=kind,
            )
        return id

    seen_slugs: set[str] = set()
    over: list[str] = []
    for c in everything()[r.id]:
        contest = key("contest", r.slug, c.key)
        result_set = key("result_set", r.slug, c.key)
        en, bs = contest_name(r, c)
        seats = sum(o.seats or 0 for o in c.options) + sum(o.elected for o in c.options if o.minority)
        magnitude = c.magnitude if c.race in CANDIDATE_RACES else seats
        district = c.district[1] if c.district else None
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office" if c.race in CANDIDATE_RACES else "list_allocation",
            office_id=office(c.race, district),
            body_id=body(c.race, district),
            district_area_id=area(*c.district) if c.district else None,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, en, "en", "official")
        p.name(contest, bs, "bs", "native", False)
        tail = changeset.slug(en)
        if tail in seen_slugs:
            tail = f"{tail}-{changeset.slug(c.key)}"
        seen_slugs.add(tail)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
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
        method = COMPENSATION if c.compensation else METHOD
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        report.contests += 1
        options: dict[str, str] = {}
        for position, o in enumerate(c.options, 1):
            option = key("option", r.slug, c.key, o.label)
            if o.label in options:
                raise ValueError(f"{r.slug} {c.key}: {o.label} is listed twice")
            options[o.label] = option
            if c.race in CANDIDATE_RACES or o.minority:
                name, _ = candidate(o.label)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(o.code) if o.code in parties else None,
                    ballot_position=position,
                    ballot_label=o.label,
                )
                person = key("person", c.race, district if c.race in {"8", "9"} else "", name)
                p.add("person", id=person)
                p.name(person, title(name), "bs", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", r.slug, c.key, o.label),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party(o.code) if o.code in parties else None,
                    is_independent=None,
                )
                if o.elected:
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected",
                        seats=1 if o.minority else None,
                    )
            else:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party(o.code) if o.code else None,
                    ballot_position=position,
                    ballot_label=o.label,
                )
                if o.seats:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=o.seats)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=o.votes)
        for measure, field_name in ACCOUNTING:
            if c.info.get(field_name) is not None and not (c.compensation and measure == REGISTERED):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=c.info[field_name])
        if c.compensation:
            id = unit(f"entity:{c.key}", "aggregate", area(*c.district) if c.district else COUNTRY)
            for o in c.options:
                values.add(result_set, id, options[o.label], VOTES, o.votes)
            for measure, field_name in ACCOUNTING:
                if measure != REGISTERED:
                    values.add(result_set, id, None, measure, c.info.get(field_name))
            continue
        named = {words(o.label): o.label for o in c.options if o.minority}
        codes = Counter(o.code for o in c.options if o.code)
        by_code = {o.code: o.label for o in c.options if o.code and codes[o.code] == 1 and not o.minority}
        summed: Counter[str] = Counter()
        accounting: Counter[int] = Counter()
        for station, code, municipality in c.stations:
            info_url, votes_url = station_info_url(db, c.race, station), station_votes_url(db, c.race, station)
            files |= {info_url, votes_url}
            info, rows = maybe(info_url), maybe(votes_url)
            id = unit(code, "standard", area("ba.municipality", municipality))
            if not isinstance(info, dict):
                report.problems.append(f"{r.slug} {c.key}: station {code} has no accounting file")
                continue
            if int(info.get("totalVotes") or 0) > int(info.get("numberOfVoters") or 0):
                over.append(f"{code} ({en}: {info.get('totalVotes')} ballots, {info.get('numberOfVoters')} registered)")
            for measure, field_name in ACCOUNTING:
                if info.get(field_name) is not None:
                    values.add(result_set, id, None, measure, info[field_name])
                    accounting[measure] += int(info[field_name])
            if c.race == "9":
                minorities_url = station_minorities_url(db, station)
                files.add(minorities_url)
                rows = [
                    *_list(rows),
                    *(dict(row, name=named.get(words(label(row)), label(row))) for row in _list(maybe(minorities_url))),
                ]
            for row in _list(rows):
                text = label(row)
                if text not in options and row.get("code") and str(row["code"]).strip() in by_code:
                    text = by_code[str(row["code"]).strip()]
                if text not in options and not row.get("totalVotes"):
                    continue
                if text not in options:
                    report.problems.append(f"{r.slug} {c.key}: station {code} names {text}, not on the district list")
                    continue
                values.add(result_set, id, options[text], VOTES, row.get("totalVotes") or 0)
                summed[text] += int(row.get("totalVotes") or 0)
        place = area(*c.district) if c.district else COUNTRY
        for _, channel, what in CHANNELS:
            counted = {o.label: o.channels[channel] for o in c.options if o.channels.get(channel)}
            if not counted:
                continue
            id = unit(f"{what.split()[0]}:{c.key}", CHANNEL_KINDS[channel], place)
            for text, n in counted.items():
                values.add(result_set, id, options[text], VOTES, n, channel=channel)
            values.add(result_set, id, None, VALID, sum(counted.values()), channel=channel)
            accounting[VALID] += sum(counted.values())
        rest = {m: int(c.info[f]) - accounting[m] for m, f in ACCOUNTING if c.info.get(f) is not None}
        if rest.get(VALID):
            report.problems.append(f"{r.slug} {c.key}: {rest[VALID]} valid votes are in no unit")
        rest.pop(VALID, None)
        for measure in [m for m, n in rest.items() if n < 0]:
            report.problems.append(
                f"{r.slug} {c.key}: the stations' measure {measure} exceeds the district's by {-rest[measure]}"
            )
            del rest[measure]
            p.rows["declared"] = [
                row
                for row in p.rows["declared"]
                if not (row["result_set_id"] == result_set and row["option_id"] is None and row["measure"] == measure)
            ]
        if any(rest.values()):
            id = unit(f"central:{c.key}", "aggregate", place)
            for measure, n in rest.items():
                values.add(result_set, id, None, measure, n)
        for o in c.options:
            here = summed[o.label] + sum(o.channels.values())
            if here != o.votes:
                report.problems.append(f"{r.slug} {c.key}: {o.label} has {here} votes in the units, {o.votes} printed")
    cited: set[str] = set()
    for url in sorted(files | aggregate_files(r)):
        try:
            artifact = raw.cached(SOURCE, url)
        except LookupError:
            continue
        if artifact.storage_key in cited:
            p.uses(artifact)
        else:
            cited.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own polling station results print more ballots cast than registered voters at "
            + "; ".join(over),
        )
    frame = values.frame()
    report.values += frame.height
    p.frame("result_value", frame)
    return p


def aggregate_files(r: Results) -> set[str]:
    db = database(r)
    out: set[str] = set()
    for race in races(r):
        t = tree(db, race, fetching=False)
        out |= {entities_url(db, race), units_url(db, race, None)}
        out |= {parents_url(db, race, e["entityId"]) for e in t.entities} | {parents_url(db, race, None)}
        out |= {units_url(db, race, k) for k in t.units if k is not None}
        out |= {stations_url(db, race, k) for k in t.stations}
        out |= set(aggregate_urls(db, race, t))
        if race == "1":
            ms = members(db, t, fetching=False)
            out |= {members_url(db, e) for e in ms} | {
                member_votes_url(db, m["memberPresidencyId"]) for v in ms.values() for m in v
            }
    return out


def totals(r: Results) -> list[str]:
    """Each race's districts summed against the race-wide totals the Commission prints."""
    db = database(r)
    lines = []
    for race in races(r):
        printed = maybe(race_info_url(db, race))
        parts = [c for c in everything()[r.id] if c.race == race and not c.compensation]
        if not isinstance(printed, dict) or not parts or race in {"8", "9"}:
            continue
        for field_name in ("numberOfVoters", "totalVotes", "validVotes"):
            here = sum(int(c.info.get(field_name) or 0) for c in parts)
            there = printed.get(field_name)
            if there is None:
                continue
            verdict = "match" if here == there else f"differ by {here - int(there)}"
            lines.append(f"{r.slug} race {race} {field_name}: districts {here:,}, national {int(there):,}, {verdict}")
    return lines


def parse(only: str | None = None) -> None:
    if not only:
        geo().write()
        registry().write()
    for r in RESULTS:
        if only and r.slug != only:
            continue
        report = Report()
        election(r, report).write()
        for line in totals(r):
            print(f"  {line}", flush=True)
        for line in report.problems[:20]:
            print(f"  {line}", flush=True)
        print(
            f"{r.slug}: {report.contests} contests, {report.values:,} values, {len(report.problems)} problems",
            flush=True,
        )


def emit() -> None:
    raise SystemExit("ba.cik builds packages; load them with `ea package load`")
