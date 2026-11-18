"""Norway's Storting elections of 1945 to 2005 by municipality, from Statistics Norway's Statbank (tables 08092, 11691,
08093, and 08219)."""

import functools
import itertools
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.no import valg

Doc = dict[str, Any]
SOURCE = "no.ssb_storting"
API = "https://data.ssb.no/api/v0/no/table/{table}"
FIRST = 1945
LAST = 2005
# Valid votes by list and municipality, votes by validity, persons entitled to vote, and members elected by district.
VOTES, BALLOTS, ROLL, SEATS = "08092", "11691", "08093", "08219"
TABLES = (VOTES, BALLOTS, ROLL, SEATS)
NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNOR"))
DISTRICT = "no.ssb_storting_valgkrets"
MUNICIPALITY = "no.ssb_storting_kommune"
UNIT = re.compile(r"\d{4}(?:u|ut)?")
DISTRICT_CODE = re.compile(r"v\d\d")
JOINT = {"90a", "90b", "90c", "90d", "90e", "90f", "90g", "90h"}
# The collective line of the party table: the lists Statbank sums without naming them.
COLLECTIVE = {"92"}
SPAN = re.compile(r"\s*\((?:\d{4}\s*-\s*\d{4}|\d{4}\s*-|-\s*\d{4}|\d{4})\)\s*$")
LICENSE = "Creative Commons Attribution 4.0 International"
TITLES = {
    VOTES: "Statistikkbanken 08092: Stortingsvalg. Godkjente stemmer, etter parti/valgliste (K) 1945-2025",
    BALLOTS: "Statistikkbanken 11691: Stortingsvalg. Stemmer, etter gyldighet og tidspunkt for stemmegivning (K) 1945-2025",
    ROLL: "Statistikkbanken 08093: Stortingsvalg. Stemmeberettigede (K) 1945-2025",
    SEATS: "Statistikkbanken 08219: Stortingsvalg. Valgte representanter, etter kjønn, parti/valgliste og valgdistrikt "
    "1945-2025",
}
DAYS = {
    1945: date(1945, 10, 8),
    1949: date(1949, 10, 10),
    1953: date(1953, 10, 12),
    1957: date(1957, 10, 7),
    1961: date(1961, 9, 11),
    1965: date(1965, 9, 13),
    1969: date(1969, 9, 8),
    1973: date(1973, 9, 10),
    1977: date(1977, 9, 12),
    1981: date(1981, 9, 14),
    1985: date(1985, 9, 9),
    1989: date(1989, 9, 11),
    1993: date(1993, 9, 13),
    1997: date(1997, 9, 15),
    2001: date(2001, 9, 10),
    2005: date(2005, 9, 12),
}
YEARS = list(DAYS)


def table_url(table: str) -> str:
    return API.format(table=table)


def query(table: str, year: int) -> dict[str, object]:
    codes = {
        VOTES: ["Region", "PolitParti", "ContentsCode"],
        BALLOTS: ["Region", "StemmeGyldigNyn", "StemmeTidspktNyn", "ContentsCode"],
        ROLL: ["Region", "ContentsCode"],
        SEATS: ["Region", "Kjonn", "PolitParti", "ContentsCode"],
    }[table]
    return {
        "query": [
            *({"code": c, "selection": {"filter": "all", "values": ["*"]}} for c in codes),
            {"code": "Tid", "selection": {"filter": "item", "values": [str(year)]}},
        ],
        "response": {"format": "json-stat2"},
    }


def fetch() -> None:
    for table in TABLES:
        raw.fetch(SOURCE, table_url(table), suffix=".json")
        for year in YEARS:
            raw.fetch(SOURCE, table_url(table), suffix=".json", body=query(table, year))
        print(f"{SOURCE}: table {table}", flush=True)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def clean(text: str) -> str:
    """A label without the span of years Statbank appends to places and parties that ended or began."""
    return re.sub(r"\s+", " ", SPAN.sub("", text)).strip()


@dataclass
class Cube:
    artifact: raw.Artifact
    cells: dict[tuple[str, ...], int]  # one code per dimension but the year; empty cells are left out
    regions: dict[str, str]
    columns: dict[str, str]  # list code -> label, for the votes and seats tables


@functools.cache
def cube(table: str, year: int) -> Cube:
    artifact = raw.cached(SOURCE, table_url(table), query(table, year))
    doc = json.loads(artifact.path.read_bytes())
    order: dict[str, list[str]] = {}
    labels: dict[str, dict[str, str]] = {}
    for name in doc["id"]:
        category = doc["dimension"][name]["category"]
        index = category["index"]
        order[name] = list(index) if isinstance(index, list) else sorted(index, key=index.__getitem__)
        labels[name] = category["label"]
    if order["Tid"] != [str(year)]:
        raise ValueError(f"{table} {year}: years {order['Tid']}")
    cells: dict[tuple[str, ...], int] = {}
    for position, codes in enumerate(itertools.product(*(order[n] for n in doc["id"]))):
        value = doc["value"][position]
        if value is not None:
            cells[codes[:-1]] = value
    return Cube(artifact, cells, labels["Region"], labels.get("PolitParti", {}))


NEGATIVE: dict[int, list[str]] = {}


def municipality(code: str) -> bool:
    return bool(UNIT.fullmatch(code))


@dataclass
class Contest:
    code: str  # the district code, or "0" for the one national contest of 1945 and 1949
    name: str
    units: list[str]
    parties: dict[str, dict[str, int]]  # unit code -> list code -> votes
    figures: dict[str, dict[int, int]]  # unit code -> measure -> number
    seats: dict[str, int]  # list code -> members elected
    declared: dict[str, int]  # list code -> votes the district row prints


def district_of(code: str) -> str:
    return f"v{code[:2]}"


@functools.cache
def region_labels() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        for table in TABLES:
            for code, label in cube(table, year).regions.items():
                found[code.removesuffix("N") if table == BALLOTS and code.endswith("N") else code] = label
    return found


def region_name(code: str) -> str:
    return clean(region_labels()[code])


def national(year: int) -> bool:
    """1945 and 1949 had separate town and rural constituencies, and Statbank's seat table holds only the rural seats
    by county, so those two elections are one national contest."""
    return year < 1953


@functools.cache
def contests(year: int) -> list[Contest]:
    votes, ballots, roll, seats = (cube(t, year) for t in TABLES)
    units: dict[str, list[str]] = {}
    for (region, *_), number in votes.cells.items():
        if municipality(region) and number:
            units.setdefault(region, [])
    for (region, *_), number in ballots.cells.items():
        if municipality(region.removesuffix("N")) and number:
            units.setdefault(region, [])
    for (region, *_), number in roll.cells.items():
        if municipality(region) and number:
            units.setdefault(region, [])
    parties: dict[str, dict[str, int]] = {u: {} for u in units}
    for (region, party, content), number in votes.cells.items():
        if content == "Godkjente1" and region in units and number:
            parties[region][party] = number
    figures: dict[str, dict[int, int]] = {u: {} for u in units}
    for (region, validity, time, _), number in ballots.cells.items():
        if region in units:
            measure = {"1N": 4, "2N": 5, "3N": 6}[validity]
            figures[region][measure] = figures[region].get(measure, 0) + number
    for (region, _), number in roll.cells.items():
        if region in units:
            figures[region][2] = number
    for unit, found in figures.items():
        if found.get(6) == 0:
            del found[6]
        if found.get(2) == 0 and 4 in found:
            # A part of a municipality that a later reform merged: the table counts its electors with the survivor.
            del found[2]
        if found.get(5, 0) < 0:
            # The table's rejected votes come out negative when its advance votes were counted with the valid ones.
            NEGATIVE.setdefault(year, []).append(f"{region_name(unit)} {unit}")
            del found[5]
            continue
        if 4 in found and 5 in found:
            found[3] = found[4] + found[5] + found.get(6, 0)
    out: list[Contest] = []
    groups = {"0": sorted(units)} if national(year) else {}
    if not national(year):
        for unit in sorted(units):
            groups.setdefault(district_of(unit), []).append(unit)
    for code, members in groups.items():
        won: dict[str, int] = {}
        for (region, _, party, _), number in seats.cells.items():
            if region == code and number:
                won[party] = won.get(party, 0) + number
        declared = {
            party: number
            for (region, party, content), number in votes.cells.items()
            if region == code and content == "Godkjente1"
        }
        name = "Storting" if code == "0" else region_name(code)
        out.append(
            Contest(
                code,
                name,
                members,
                {u: parties[u] for u in members},
                {u: figures[u] for u in members},
                won,
                declared,
            )
        )
    return out


def _district_codes() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        if not national(year):
            for contest in contests(year):
                found[contest.code] = contest.name
    return found


@functools.cache
def places() -> dict[str, str]:
    towns: set[str] = set()
    for year in YEARS:
        for contest in contests(year):
            towns.update(contest.units)
    return {code: region_name(code) for code in sorted(towns)}


@functools.cache
def parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        votes = cube(VOTES, year)
        for (_, party, _), number in votes.cells.items():
            if number:
                label = clean(votes.columns[party])
                if found.setdefault(party, label) != label:
                    raise ValueError(f"list {party} is {found[party]!r} and {label!r}")
    return found


def source_id(table: str) -> str:
    return key("source", table)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()
        print(year, len(contests(year)), compare(year), flush=True)


def emit() -> None:
    package.run("load", packages())


def _sources(p: Package) -> None:
    for table, title in TITLES.items():
        p.add(
            "source",
            id=source_id(table),
            kind="official_body",
            publisher="Statistics Norway",
            title=title,
            url=f"https://www.ssb.no/statbank/table/{table}",
            license=LICENSE,
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="Statistics Norway (SSB)",
        )


def _used(p: Package) -> None:
    for table in TABLES:
        p.uses(raw.cached(SOURCE, table_url(table)))
        for year in YEARS:
            p.uses(cube(table, year).artifact)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Norway's Storting constituencies (valgdistrikter) and the municipalities (kommuner) that counted votes in "
        "the Storting elections of 1945 to 2005, under the codes and names Statistics Norway's Statbank gives them",
    )
    p.external |= {NORWAY, COUNTRY}
    _sources(p)
    _used(p)
    p.add("scheme", code=DISTRICT, polity_id=NORWAY, kind="electoral", rank=1, noun="constituency", code_system="ssb")
    p.add(
        "scheme",
        code=MUNICIPALITY,
        polity_id=NORWAY,
        kind="administrative",
        rank=2,
        noun="municipality",
        code_system="ssb",
    )

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "nb", "official")

    for code, text in sorted(_district_codes().items()):
        place(DISTRICT, code, text, COUNTRY)
    for code, text in places().items():
        place(MUNICIPALITY, code, text, area(DISTRICT, district_of(code)))
    return p


def party(code: str) -> str:
    return key("party", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The lists on the Storting ballots from 1945 to 2005, as Statistics Norway's Statbank names them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NORWAY, valg.STORTING}
    _used(p)
    for code, text in sorted(parties().items()):
        if code in COLLECTIVE:
            continue
        id = party(code)
        p.add("party", id=id, kind="list_vehicle" if code in JOINT else "party", polity_id=NORWAY)
        p.add("external_id", entity_id=id, scheme="no.ssb_storting.party", value=code)
        p.name(id, text, "nb", "official")
    return p


MEASURES = {2: "registered", 3: "cast", 4: "valid", 5: "invalid", 6: "blank"}


def compare(year: int) -> dict[str, tuple[int, int | None]]:
    """The sums of the municipalities' figures against the national row Statbank prints."""
    ballots = cube(BALLOTS, year)
    roll = cube(ROLL, year)
    sums: dict[str, int] = {}
    for contest in contests(year):
        for figures in contest.figures.values():
            for measure, number in figures.items():
                sums[MEASURES[measure]] = sums.get(MEASURES[measure], 0) + number
    printed = {
        "valid": sum(n for (r, v, _, _), n in ballots.cells.items() if r == "0N" and v == "1N"),
        "invalid": sum(n for (r, v, _, _), n in ballots.cells.items() if r == "0N" and v == "2N"),
        "blank": sum(n for (r, v, _, _), n in ballots.cells.items() if r == "0N" and v == "3N"),
        "registered": next((n for (r, _), n in roll.cells.items() if r == "0"), 0),
    }
    printed["cast"] = printed["valid"] + printed["invalid"] + printed["blank"]
    return {name: (total, printed.get(name)) for name, total in sums.items()}


METHOD = (
    "Counted by the municipalities and published by Statistics Norway (SSB) in the Statbank tables 08092 (valid votes "
    "by list and municipality), 11691 (valid, rejected, and blank votes), 08093 (persons entitled to vote), and 08219 "
    "(members elected by list and constituency). Votes for a list are the valid votes (godkjente stemmer) the table "
    "gives for each list; a list a municipality's cell leaves empty won no votes there. Valid and rejected votes "
    "are summed over the polling-station and advance votes, and ballots cast are the valid, rejected, and blank "
    "votes together. {extra}"
)
NATIONAL_NOTE = (
    "Until 1949 the towns and the rural districts of each county were separate constituencies, and Statbank's "
    "seat table holds only the 112 rural seats by county, so this election is stored as one contest for the whole "
    "country, whose seats are the national seats by list. Votes are stored by municipality, towns included. "
)
MONDAY = (
    "The election day is the Monday on which the Storting is elected; municipalities could also open their polls "
    "on the Sunday before. "
)


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"Norway's Storting election of {year} by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NORWAY, COUNTRY, valg.STORTING}
    names = parties()
    day = DAYS[year]
    for table in TABLES:
        p.artifact(cube(table, year).artifact, source_id(table))
    election_id = key("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=NORWAY,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} Norwegian parliamentary election", "en", "official")
    p.name(election_id, f"Stortingsvalget {year}", "nb", "native", False)
    path = f"no/{day}-parliamentary"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    all_contests = contests(year)
    slugs = [changeset.slug(c.name) for c in all_contests]
    disagree: list[str] = []
    for contest, slug_word in zip(all_contests, slugs, strict=True):
        if slugs.count(slug_word) > 1:
            slug_word = f"{slug_word}-{contest.code}"
        contest_id = key("contest", year, contest.code)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation",
            body_id=valg.STORTING,
            district_area_id=COUNTRY if contest.code == "0" else area(DISTRICT, contest.code),
            magnitude=sum(contest.seats.values()) or None,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest_id, contest.name, "nb", "official")
        p.add("slug", path=f"{path}/{slug_word}", entity_id=contest_id, is_current=True)
        set_id = key("result_set", year, contest.code)
        p.add(
            "result_set",
            id=set_id,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=source_id(VOTES),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        extra = (NATIONAL_NOTE if national(year) else "") + MONDAY
        p.add(
            "note",
            id=key("note", set_id),
            entity_id=set_id,
            kind="methodology",
            body_md=METHOD.format(extra=extra.strip()),
            lang="en",
        )
        for table in TABLES:
            p.add(
                "citation",
                id=key("citation", set_id, table),
                entity_id=set_id,
                source_id=source_id(table),
                locator=json.dumps({"table": table, "year": year, "region": contest.code, "url": table_url(table)}),
            )
        option_ids: dict[str, str] = {}
        for code in sorted({c for cells in contest.parties.values() for c in cells} | set(contest.seats)):
            option_ids[code] = key("option", year, contest.code, code)
            p.add(
                "contest_option",
                id=option_ids[code],
                contest_id=contest_id,
                kind="others" if code in COLLECTIVE else "list",
                party_id=None if code in COLLECTIVE else party(code),
                ballot_label=names[code],
            )
            if contest.seats.get(code):
                p.add(
                    "outcome", result_set_id=set_id, option_id=option_ids[code], kind="won", seats=contest.seats[code]
                )
        totals: dict[str, int] = {}
        for unit in contest.units:
            unit_id = key("unit", year, unit)
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=NORWAY,
                scheme_code=MUNICIPALITY,
                code=f"{year}:{unit}",
                area_relation="equals",
                area_id=area(MUNICIPALITY, unit),
                kind="standard",
            )
            p.name(unit_id, region_name(unit), "nb", "official")
            facts: list[tuple[str | None, int, int]] = [
                (option_ids[code], 1, number) for code, number in contest.parties[unit].items()
            ]
            facts += [(None, measure, number) for measure, number in contest.figures[unit].items()]
            for option_id, measure, number in facts:
                if option_id is not None:
                    code = next(c for c, i in option_ids.items() if i == option_id)
                    totals[code] = totals.get(code, 0) + number
                p.add(
                    "result_value",
                    result_set_id=set_id,
                    unit_id=unit_id,
                    option_id=option_id,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=number,
                )
        for code, number in contest.declared.items():
            if totals.get(code, 0) != number:
                p.add(
                    "note",
                    id=key("note", set_id, code),
                    entity_id=option_ids[code],
                    kind="methodology",
                    body_md=f"Statbank's row for the constituency prints {number:,} votes for this list, while its "
                    f"municipalities add up to {totals.get(code, 0):,}; the municipalities' figures are stored and "
                    "the constituency row is not declared.",
                    lang="en",
                )
                continue
            p.add("declared", result_set_id=set_id, option_id=option_ids[code], measure=1, value=number)
        if set(totals) - set(contest.declared):
            raise ValueError(f"{year} {contest.name}: lists without a district row")
        for unit in contest.units:
            figures = contest.figures[unit]
            if 4 in figures and figures[4] != sum(contest.parties[unit].values()):
                disagree.append(f"{region_name(unit)} {unit}")
    if NEGATIVE.get(year):
        p.add(
            "note",
            id=key("note", election_id, "negative"),
            entity_id=election_id,
            kind="methodology",
            body_md="Statbank prints a negative number of rejected votes for "
            + ", ".join(NEGATIVE[year])
            + ", where the valid votes exceed the ballots it counts; those units store the valid votes and the "
            "list votes, without rejected votes or ballots cast.",
            lang="en",
        )
    if disagree:
        p.accept(
            "accounting.votes_valid",
            f"Statistics Norway's valid votes and its list votes disagree for {len(disagree)} municipalities: "
            + "; ".join(disagree)
            + ". Both are stored as published.",
        )
    return p
