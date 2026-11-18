"""Norway's Sámi Parliament elections of 2005 and 2009 by constituency, from Statistics Norway's Statbank (tables 05921
to 05925)."""

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

Doc = dict[str, Any]
SOURCE = "no.ssb_sameting"
API = "https://data.ssb.no/api/v0/no/table/{table}"
# Votes by validity, seats by constituency, members elected by list, votes by list, and votes by list and channel.
VALIDITY, MAGNITUDE, MEMBERS, VOTES, CHANNELS = "05921", "05922", "05923", "05924", "05925"
TABLES = (VALIDITY, MAGNITUDE, MEMBERS, VOTES, CHANNELS)
DAYS = {2005: date(2005, 9, 12), 2009: date(2009, 9, 14)}
NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNOR"))
DISTRICT = "no.ssb_samevalgkrets"
WHOLE = "0N"
LICENSE = "Creative Commons Attribution 4.0 International"
TITLES = {
    VALIDITY: "Statistikkbanken 05921: Sametingsvalet. Godkjende stemmer, etter region, statistikkvariabel og år",
    MAGNITUDE: "Statistikkbanken 05922: Sametingsvalet. Talet på representantar etter region, statistikkvariabel og år",
    MEMBERS: "Statistikkbanken 05923: Sametingsvalet. Valde representantar, etter region, parti/valliste, kjønn, "
    "statistikkvariabel og år",
    VOTES: "Statistikkbanken 05924: Sametingsvalet. Godkjende stemmer, etter region, parti/valliste, "
    "statistikkvariabel og år",
    CHANNELS: "Statistikkbanken 05925: Sametingsvalet. Godkjende stemmer gitt på forhånd og på valdagen, etter region, "
    "parti/valliste, statistikkvariabel og år",
}
METHOD = (
    "Counted by the Sámi Parliament's electoral committees and published by Statistics Norway (SSB) in the Statbank "
    "tables 05921 to 05925. Votes for a list are the approved votes (godkjende stemmer) the tables give for the "
    "constituency, split between votes cast in advance and on election day. Rejected and blank votes are the "
    "tables' counts. Seats are the representatives elected for each list. {extra}"
)
CHANNEL_OF = {"GodkjendeForehand": 2, "GodkjendeValting": 1}
VALIDITY_MEASURE = {"Stemmer": 4, "StemmeVrak": 5, "StemmeBlank": 6}
SPLIT = {"Stemmer2": (4, 1), "Stemmer3": (4, 2), "StemmeVrak2": (5, 1), "StemmeVrak3": (5, 2)}
SPLIT |= {"StemmeBlank2": (6, 1), "StemmeBlank3": (6, 2)}
COLLECTIVE = {"90"}
SPAN = re.compile(r"\s*\((?:\d{4}\s*-\s*\d{4}|\d{4}\s*-|-\s*\d{4}|\d{4})\)\s*$")


def table_url(table: str) -> str:
    return API.format(table=table)


def query(table: str, year: int) -> dict[str, object]:
    return {
        "query": [
            *(
                {"code": c, "selection": {"filter": "all", "values": ["*"]}}
                for c in {
                    VALIDITY: ["Region", "ContentsCode"],
                    MAGNITUDE: ["Region", "ContentsCode"],
                    MEMBERS: ["Region", "PartiValglistebm", "Kjonn", "ContentsCode"],
                    VOTES: ["Region", "PartiValglistebm", "ContentsCode"],
                    CHANNELS: ["Region", "PartiValglistebm", "ContentsCode"],
                }[table]
            ),
            {"code": "Tid", "selection": {"filter": "item", "values": [str(year)]}},
        ],
        "response": {"format": "json-stat2"},
    }


def fetch() -> None:
    for table in TABLES:
        raw.fetch(SOURCE, table_url(table), suffix=".json")
        for year in DAYS:
            raw.fetch(SOURCE, table_url(table), suffix=".json", body=query(table, year))
        print(f"{SOURCE}: table {table}", flush=True)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return key("area", DISTRICT, code)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", SPAN.sub("", text)).strip()


@dataclass
class Cube:
    artifact: raw.Artifact
    cells: dict[tuple[str, ...], int]  # one code per dimension but the year; empty cells are left out
    names: dict[str, dict[str, str]]  # dimension -> code -> label


@functools.cache
def cube(table: str, year: int) -> Cube:
    artifact = raw.cached(SOURCE, table_url(table), query(table, year))
    doc = json.loads(artifact.path.read_bytes())
    order: dict[str, list[str]] = {}
    names: dict[str, dict[str, str]] = {}
    for name in doc["id"]:
        category = doc["dimension"][name]["category"]
        index = category["index"]
        order[name] = list(index) if isinstance(index, list) else sorted(index, key=index.__getitem__)
        names[name] = category["label"]
    if order["Tid"] != [str(year)]:
        raise ValueError(f"{table} {year}: years {order['Tid']}")
    cells: dict[tuple[str, ...], int] = {}
    for position, codes in enumerate(itertools.product(*(order[n] for n in doc["id"]))):
        value = doc["value"][position]
        if value is not None:
            cells[codes[:-1]] = value
    return Cube(artifact, cells, names)


@dataclass
class Contest:
    code: str
    name: str
    magnitude: int
    parties: dict[str, dict[int, int]]  # list code -> channel -> votes
    figures: dict[int, dict[int, int]]  # measure -> channel -> number
    seats: dict[str, int]  # list code -> members elected
    declared: dict[str, int]  # list code -> the table's votes for the constituency


def regions(year: int) -> dict[str, str]:
    """The constituencies the seats table holds for a year, which are the regions with representatives."""
    table = cube(MAGNITUDE, year)
    return {
        code: clean(table.names["Region"][code])
        for (code, measure), number in table.cells.items()
        if measure == "Representanter" and number
    }


@functools.cache
def labels() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in DAYS:
        for code, label in cube(MAGNITUDE, year).names["Region"].items():
            if found.setdefault(code, clean(label)) != clean(label):
                raise ValueError(f"region {code} is {found[code]!r} and {label!r}")
    return found


@functools.cache
def parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in DAYS:
        for table in (VOTES, MEMBERS):
            data = cube(table, year)
            for codes, number in data.cells.items():
                if number and codes[1] != WHOLE:
                    label = clean(data.names["PartiValglistebm"][codes[1]])
                    if found.setdefault(codes[1], label) != label:
                        raise ValueError(f"list {codes[1]} is {found[codes[1]]!r} and {label!r}")
    return found


@functools.cache
def contests(year: int) -> list[Contest]:
    votes, split, validity, members, seats = (
        cube(VOTES, year),
        cube(CHANNELS, year),
        cube(VALIDITY, year),
        cube(MEMBERS, year),
        cube(MAGNITUDE, year),
    )
    found = []
    for code, name in regions(year).items():
        by_list: dict[str, dict[int, int]] = {}
        for (region, party, measure), number in split.cells.items():
            if region == code and number:
                by_list.setdefault(party, {})[CHANNEL_OF[measure]] = number
        figures: dict[int, dict[int, int]] = {}
        for (region, measure), number in validity.cells.items():
            if region == code and measure in SPLIT:
                figures.setdefault(SPLIT[measure][0], {})[SPLIT[measure][1]] = number
        for (region, measure), number in validity.cells.items():
            if region == code and measure in VALIDITY_MEASURE and not figures.get(VALIDITY_MEASURE[measure]):
                figures[VALIDITY_MEASURE[measure]] = {0: number}
        won: dict[str, int] = {}
        for (region, party, _, _), number in members.cells.items():
            if region == code and number:
                won[party] = won.get(party, 0) + number
        declared = {
            party: number
            for (region, party, measure), number in votes.cells.items()
            if region == code and measure == "Godkjende" and number
        }
        found.append(Contest(code, name, seats.cells[(code, "Representanter")], by_list, figures, won, declared))
    return found


def source_id(table: str) -> str:
    return key("source", table)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def compare() -> dict[int, dict[str, tuple[int, int | None]]]:
    """Each election's constituency sums against the whole-country rows of the votes table."""
    out: dict[int, dict[str, tuple[int, int | None]]] = {}
    for year in DAYS:
        votes = cube(VOTES, year)
        total = sum(sum(c.declared.values()) for c in contests(year))
        national = sum(n for (r, _, m), n in votes.cells.items() if r == WHOLE and m == "Godkjende")
        out[year] = {"valid votes": (total, national)}
        out[year]["seats"] = (
            sum(sum(c.seats.values()) for c in contests(year)),
            sum(c.magnitude for c in contests(year)),
        )
    return out


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()
    for year, found in compare().items():
        print(year, len(contests(year)), found, flush=True)


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
        for year in DAYS:
            p.uses(cube(table, year).artifact)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The constituencies of the Sámi Parliament elections of 2005 (13) and 2009 (7), under the codes and names "
        "Statistics Norway's Statbank gives them",
    )
    p.external |= {NORWAY, COUNTRY}
    _sources(p)
    _used(p)
    p.add(
        "scheme",
        code=DISTRICT,
        polity_id=NORWAY,
        kind="electoral_district",
        rank=1,
        noun="Sámi Parliament constituency",
        code_system="ssb",
    )
    for year in DAYS:
        for code, name in regions(year).items():
            id = area(code)
            if any(r.get("id") == id for r in p.rows.get("area", [])):
                continue
            p.add("area", id=id, scheme_code=DISTRICT)
            p.add("area_code", area_id=id, code=code)
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            p.name(id, name, "nb", "official")
            p.add(
                "note",
                id=key("note", id),
                entity_id=id,
                kind="context",
                body_md=f"A constituency of the {', '.join(str(y) for y in DAYS if code in regions(y))} Sámi "
                "Parliament election, as Statistics Norway's Statbank names it. The constituencies were redrawn "
                "from 13 to 7 for the 2009 election.",
                lang="en",
            )
    return p


SAMETING = key("body", "sameting")


def party(code: str) -> str:
    return key("party", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Sámi Parliament and the lists on its ballots in 2005 and 2009, as Statistics Norway's Statbank names them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NORWAY}
    _used(p)
    p.add("body", id=SAMETING, polity_id=NORWAY, kind="unicameral")
    p.name(SAMETING, "Sámi Parliament", "en", "official")
    p.name(SAMETING, "Sametinget", "nb", "native", False)
    for code, text in sorted(parties().items()):
        if code in COLLECTIVE:
            continue
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=NORWAY)
        p.add("external_id", entity_id=id, scheme="no.ssb_sameting.party", value=code)
        p.name(id, text, "nb", "official")
    return p


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"Norway's Sámi Parliament election of {year} by constituency",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NORWAY, COUNTRY}
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
    p.name(election_id, f"{year} Sámi Parliament election", "en", "official")
    p.name(election_id, f"Sametingsvalget {year}", "nb", "native", False)
    path = f"no/{day}-sami-parliament"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    all_contests = contests(year)
    slugs = [changeset.slug(c.name) for c in all_contests]
    for contest, slug_word in zip(all_contests, slugs, strict=True):
        if slugs.count(slug_word) > 1:
            slug_word = f"{slug_word}-{contest.code}"
        contest_id = key("contest", year, contest.code)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation",
            body_id=SAMETING,
            district_area_id=area(contest.code),
            magnitude=contest.magnitude,
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
            source_id=source_id(CHANNELS),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", set_id),
            entity_id=set_id,
            kind="methodology",
            body_md=METHOD.format(extra="The constituency is the finest unit Statbank publishes.").strip(),
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
        for code in sorted(set(contest.parties) | set(contest.seats)):
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
        unit_id = key("unit", year, contest.code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=NORWAY,
            scheme_code=DISTRICT,
            code=f"{year}:{contest.code}",
            area_relation="equals",
            area_id=area(contest.code),
            kind="standard",
        )
        p.name(unit_id, contest.name, "nb", "official")
        facts: list[tuple[str | None, int, int, int]] = [
            (option_ids[code], 1, channel, number)
            for code, channels in contest.parties.items()
            for channel, number in channels.items()
        ]
        facts += [
            (None, measure, channel, number)
            for measure, channels in contest.figures.items()
            for channel, number in channels.items()
        ]
        for option_id, measure, channel, number in facts:
            p.add(
                "result_value",
                result_set_id=set_id,
                unit_id=unit_id,
                option_id=option_id,
                channel=channel,
                measure=measure,
                count_round=0,
                flags=0,
                value=number,
            )
        for code, number in contest.declared.items():
            if sum(contest.parties.get(code, {}).values()) != number:
                raise ValueError(f"{year} {contest.name}: list {code} votes {number} differ from the channel table")
            p.add("declared", result_set_id=set_id, option_id=option_ids[code], measure=1, value=number)
        if sum(contest.seats.values()) != contest.magnitude:
            raise ValueError(f"{year} {contest.name}: seats {sum(contest.seats.values())} of {contest.magnitude}")
    return p
