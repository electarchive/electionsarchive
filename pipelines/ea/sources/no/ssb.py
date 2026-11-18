"""Norway's municipal and county council elections of 1945 to 2007 by municipality, from Statistics Norway's
Statbank (tables 01180, 09475, 01181, and 09476)."""

import functools
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

Doc = dict[str, Any]
SOURCE = "no.ssb"
API = "https://data.ssb.no/api/v0/no/table/{table}"
LAST_YEAR = 2007
# Each table's first election year; its years run every fourth year from there to the last year.
TABLES = {"01180": 1945, "09475": 1955, "01181": 1975, "09476": 1975}
NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNOR"))
COUNTY = "no.ssb_fylke"
MUNICIPALITY = "no.ssb_kommune"
OSLO = "03"
# The election of each kind: its votes table, its turnout table, and its words.
KINDS = {
    "KO": ("01180", "09475", "municipal", "Kommunestyrevalget", "municipal", "Kommunestyret"),
    "FY": ("01181", "09476", "county", "Fylkestingsvalget", "county", "Fylkestinget"),
}
# The national polling days, which the archive's census of Norway's elections gives from 1975; earlier councils were
# elected in a year whose day Statbank does not print.
DAYS = {
    1975: date(1975, 9, 15),
    1979: date(1979, 9, 17),
    1983: date(1983, 9, 12),
    1987: date(1987, 9, 14),
    1991: date(1991, 9, 9),
    1995: date(1995, 9, 11),
    1999: date(1999, 9, 13),
    2003: date(2003, 9, 15),
    2007: date(2007, 9, 10),
}
JOINT = {"90a", "90b", "90c", "90d", "90e", "90f", "90g", "90h"}
# The collective lines of the party table: lists that Statbank sums without naming them.
COLLECTIVE = {"90", "91", "92"}
SPAN = re.compile(r"\s*\((?:\d{4}\s*-\s*\d{4}|\d{4}\s*-|-\s*\d{4}|\d{4})\)\s*$")
UNIT = re.compile(r"\d{4}(?:u|ut)?")
COUNTY_CODE = re.compile(r"\d{2}")
LICENSE = "Creative Commons Attribution 4.0 International"
TITLES = {
    "01180": "Statistikkbanken 01180: Kommunestyrevalget. Godkjente stemmer, etter parti/valgliste (K) 1945-2023",
    "09475": "Statistikkbanken 09475: Kommunestyrevalget. Stemmer og valgdeltakelse (K) 1955-2023",
    "01181": "Statistikkbanken 01181: Fylkestingsvalget. Godkjente stemmer, etter parti/valgliste (K) 1975-2023",
    "09476": "Statistikkbanken 09476: Fylkestingsvalget. Stemmer og valgdeltakelse (K) 1975-2023",
}
METHOD = (
    "Counted by the municipalities and published by Statistics Norway (SSB) in the Statbank tables {tables}. Votes "
    "for a list are the approved votes (godkjente stemmer) the table gives for each list; a zero in the table is "
    "taken as a list that stood no candidates or won no votes and is not stored. Ballots cast are the votes cast "
    "(avgitte stemmer), valid ballots are the approved votes, and invalid ballots are the rejected votes "
    "(forkastede stemmer), as the turnout table gives them. The turnout table's blank ballots are zero or empty in "
    "every municipality and are not stored. {extra}"
)


def table_url(table: str) -> str:
    return API.format(table=table)


def years(table: str) -> list[str]:
    """The election years of a table up to the last year, as the table labels them."""
    meta = json.loads(raw.cached(SOURCE, table_url(table)).path.read_bytes())
    found = next(v for v in meta["variables"] if v["code"] == "Tid")["values"]
    return [y for y in found if TABLES[table] <= int(y) <= LAST_YEAR]


def query(table: str, year: str) -> dict[str, object]:
    codes = ["Region", "PolitParti", "ContentsCode"] if table in {"01180", "01181"} else ["Region", "ContentsCode"]
    return {
        "query": [
            *({"code": c, "selection": {"filter": "all", "values": ["*"]}} for c in codes),
            {"code": "Tid", "selection": {"filter": "item", "values": [year]}},
        ],
        "response": {"format": "json-stat2"},
    }


def fetch() -> None:
    for table in TABLES:
        raw.fetch(SOURCE, table_url(table), suffix=".json")
        for year in years(table):
            raw.fetch(SOURCE, table_url(table), suffix=".json", body=query(table, year))
        print(f"{SOURCE}: table {table}", flush=True)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def clean(text: str) -> str:
    """A label without the span of years Statbank appends to places and parties that ended or began."""
    return re.sub(r"\s+", " ", SPAN.sub("", text)).strip()


# Reading the tables.


@dataclass
class Cube:
    artifact: raw.Artifact
    rows: dict[str, dict[str, int]]  # region code -> column code -> number; empty cells are left out
    regions: dict[str, str]
    columns: dict[str, str]


@functools.cache
def cube(table: str, year: str) -> Cube:
    artifact = raw.cached(SOURCE, table_url(table), query(table, year))
    doc = json.loads(artifact.path.read_bytes())
    order: dict[str, list[str]] = {}
    names: dict[str, dict[str, str]] = {}
    for name in doc["id"]:
        category = doc["dimension"][name]["category"]
        index = category["index"]
        order[name] = list(index) if isinstance(index, list) else sorted(index, key=index.__getitem__)
        names[name] = category["label"]
    column = "PolitParti" if "PolitParti" in order else "ContentsCode"
    for name, found in order.items():
        if name not in {"Region", column} and len(found) != 1:
            raise ValueError(f"{table} {year}: dimension {name} has {len(found)} values")
    width = len(order[column])
    rows: dict[str, dict[str, int]] = {}
    for i, region in enumerate(order["Region"]):
        cells = {
            code: value for k, code in enumerate(order[column]) if (value := doc["value"][i * width + k]) is not None
        }
        if cells:
            rows[region] = cells
    return Cube(artifact, rows, names["Region"], names[column])


def kinds(year: int) -> list[str]:
    return [kind for kind, (votes, _, *_) in KINDS.items() if TABLES[votes] <= year]


def election_years() -> list[int]:
    return sorted({int(y) for y in years("01180")})


@dataclass
class Contest:
    code: str
    name: str
    parties: dict[str, dict[str, int]]  # unit code -> list code -> votes
    figures: dict[str, dict[str, int]]  # unit code -> measure name -> number
    declared: dict[str, int]  # the county's own totals, where the table prints them
    negative: dict[str, int]  # units whose table prints a negative number of rejected votes


def _units(year: int, kind: str) -> dict[str, list[str]]:
    """The municipalities of each contest the tables hold for a kind: every municipality that has list votes or
    counted ballots, grouped by the county for a county election."""
    votes_table, turnout_table = KINDS[kind][:2]
    votes = cube(votes_table, str(year))
    turnout = cube(turnout_table, str(year)) if TABLES[turnout_table] <= year else None
    found = {
        code
        for source in (votes.rows, turnout.rows if turnout else {})
        for code, cells in source.items()
        if UNIT.fullmatch(code) and ("AvgitteStemmer" in cells or (source is votes.rows and any(cells.values())))
    }
    if kind == "KO":
        return {code: [code] for code in sorted(found)}
    counties: dict[str, list[str]] = {}
    for code in sorted(found):
        counties.setdefault(code[:2], []).append(code)
    # Oslo's city council is its county council, so the capital has no county election of its own.
    return {county: units for county, units in counties.items() if county != OSLO}


@functools.cache
def labels() -> dict[str, str]:
    """The label of every region code as Statbank prints it, the same in every table and year."""
    found: dict[str, str] = {}
    for table in TABLES:
        for year in years(table):
            for code, label in cube(table, year).regions.items():
                if found.setdefault(code, label) != label:
                    raise ValueError(f"{table} {year}: region {code} is {found[code]!r} and {label!r}")
    return found


def region_name(code: str) -> str:
    return clean(labels()[code])


@functools.cache
def contests(year: int, kind: str) -> list[Contest]:
    votes_table, turnout_table = KINDS[kind][:2]
    votes = cube(votes_table, str(year))
    turnout = cube(turnout_table, str(year)) if TABLES[turnout_table] <= year else None
    modified = turnout is not None and any(cells.get("Rettede") for cells in turnout.rows.values())
    found = []
    for code, units in _units(year, kind).items():
        parties = {u: dict(votes.rows.get(u, {})) for u in units}
        figures: dict[str, dict[str, int]] = {}
        negative: dict[str, int] = {}
        for u in units:
            cells = turnout.rows.get(u, {}) if turnout else {}
            if "AvgitteStemmer" not in cells:
                continue
            if cells["Forkastede"] < 0:
                negative[u] = cells["Forkastede"]
            figures[u] = {
                "cast": cells["AvgitteStemmer"],
                "valid": cells["Godkjente"],
                **({"invalid": cells["Forkastede"]} if cells["Forkastede"] >= 0 else {}),
                **({"registered": cells["Berettigede"]} if "Berettigede" in cells else {}),
                **({"modified": cells["Rettede"]} if modified and "Rettede" in cells else {}),
            }
        declared: dict[str, int] = {}
        if kind == "FY":
            cells = turnout.rows[code] if turnout else {}
            declared = {
                name: cells[column]
                for name, column in (
                    ("cast", "AvgitteStemmer"),
                    ("valid", "Godkjente"),
                    ("invalid", "Forkastede"),
                    ("registered", "Berettigede"),
                )
                if column in cells
            }
        name = region_name(code)
        found.append(Contest(code, name, parties, figures, declared, negative))
    return found


# The places.


@functools.cache
def places() -> tuple[dict[str, str], dict[str, str]]:
    """(counties, municipalities) every election uses, each code with its name."""
    towns: set[str] = set()
    for year in election_years():
        for kind in kinds(year):
            for contest in contests(year, kind):
                towns.update(contest.parties)
    names = {code: region_name(code) for code in towns | {town[:2] for town in towns}}
    return (
        {code: name for code, name in names.items() if COUNTY_CODE.fullmatch(code)},
        {code: name for code, name in names.items() if code in towns},
    )


def parties() -> dict[str, str]:
    """Every list code the tables hold votes for in the years loaded, with its name."""
    found: dict[str, str] = {}
    for year in election_years():
        for kind in kinds(year):
            votes = cube(KINDS[kind][0], str(year))
            for cells in votes.rows.values():
                for code, number in cells.items():
                    if number:
                        label = clean(votes.columns[code])
                        if found.setdefault(code, label) != label:
                            raise ValueError(f"list {code} is {found[code]!r} and {label!r}")
    return found


def source_id(table: str) -> str:
    return key("source", table)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in election_years())]


def parse() -> None:
    geo().write()
    registry().write()
    for year in election_years():
        election(year).write()
    for year in election_years():
        for kind in kinds(year):
            if year >= TABLES[KINDS[kind][1]]:
                print(year, kind, len(contests(year, kind)), compare(year, kind), flush=True)


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
        for year in years(table):
            p.uses(cube(table, year).artifact)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Norway's counties (fylker) and municipalities (kommuner) that held council elections from 1945 to 2007, "
        "under the codes and names Statistics Norway's Statbank gives them",
    )
    p.external |= {NORWAY, COUNTRY}
    _sources(p)
    _used(p)
    p.add("scheme", code=COUNTY, polity_id=NORWAY, kind="administrative", rank=1, noun="county", code_system="ssb")
    p.add(
        "scheme",
        code=MUNICIPALITY,
        polity_id=NORWAY,
        kind="administrative",
        rank=2,
        noun="municipality",
        code_system="ssb",
    )
    counties, towns = places()

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "nb", "official")
        if labels()[code] != text:
            p.add(
                "note",
                id=key("note", id),
                entity_id=id,
                kind="context",
                body_md=f"Statistics Norway's Statbank labels this {scheme.split('_')[1]} {labels()[code]!r}.",
                lang="en",
            )

    for code, text in sorted(counties.items()):
        place(COUNTY, code, text, COUNTRY)
    for code, text in sorted(towns.items()):
        place(MUNICIPALITY, code, text, area(COUNTY, code[:2]))
    return p


def region_body(code: str) -> str:
    return key("body", "county", code)


def council(code: str) -> str:
    return key("body", "municipality", code)


def party(code: str) -> str:
    return key("party", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The county and municipal councils of Norway and the lists on their ballots from 1945 to 2007, as Statistics "
        "Norway's Statbank names them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NORWAY}
    _used(p)
    counties, towns = places()
    electing = sorted({c.code for year in election_years() if "FY" in kinds(year) for c in contests(year, "FY")})
    for code in electing:
        p.add("body", id=region_body(code), polity_id=NORWAY, kind="council")
        p.name(region_body(code), f"Fylkestinget, {counties[code]}", "nb", "official")
    for code, text in sorted(towns.items()):
        p.add("body", id=council(code), polity_id=NORWAY, kind="council")
        p.name(council(code), f"Kommunestyret, {text}", "nb", "official")
    for code, text in sorted(parties().items()):
        if code in COLLECTIVE:
            continue
        id = party(code)
        p.add("party", id=id, kind="list_vehicle" if code in JOINT else "party", polity_id=NORWAY)
        p.add("external_id", entity_id=id, scheme="no.ssb.party", value=code)
        p.name(id, text, "nb", "official")
    return p


def compare(year: int, kind: str) -> dict[str, tuple[int, int | None]]:
    """The sums of the municipalities' counted ballots against the national row the turnout table prints."""
    turnout = cube(KINDS[kind][1], str(year))
    national = turnout.rows.get("0", {})
    sums: dict[str, int] = {}
    for contest in contests(year, kind):
        for figures in contest.figures.values():
            for name, number in figures.items():
                sums[name] = sums.get(name, 0) + number
    if kind == "FY":
        # Oslo holds no county election, but the table's national row counts the capital's ballots.
        oslo = turnout.rows[f"{OSLO}01"]
        for name, column in (("cast", "AvgitteStemmer"), ("valid", "Godkjente"), ("invalid", "Forkastede")):
            sums[name] = sums.get(name, 0) + oslo[column]
        sums["registered"] = sums.get("registered", 0) + oslo["Berettigede"]
    columns = {"cast": "AvgitteStemmer", "valid": "Godkjente", "invalid": "Forkastede", "registered": "Berettigede"}
    return {name: (total, national.get(columns[name])) for name, total in sums.items() if name in columns}


MEASURES = (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5), ("modified", 23))


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"Norway's {' and '.join(KINDS[k][2] for k in kinds(year))} council elections of {year} by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NORWAY, COUNTRY}
    names = parties()
    day = DAYS.get(year)
    disagree: list[str] = []
    negative: list[str] = []
    for kind in kinds(year):
        votes_table, turnout_table, word, norwegian, _, _ = KINDS[kind]
        p.artifact(cube(votes_table, str(year)).artifact, source_id(votes_table))
        has_turnout = TABLES[turnout_table] <= year
        if has_turnout:
            p.artifact(cube(turnout_table, str(year)).artifact, source_id(turnout_table))
        election_id = key("election", year, kind)
        p.add(
            "election",
            id=election_id,
            polity_id=NORWAY,
            kind="local",
            date_from=day or date(year, 1, 1),
            date_to=day or date(year, 12, 31),
            date_precision="day" if day else "year",
            status="held",
        )
        p.name(election_id, f"{year} Norwegian {word} council elections", "en", "official")
        p.name(election_id, f"{norwegian} {year}", "nb", "native", False)
        path = f"no/{day or year}-{word}"
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        all_contests = contests(year, kind)
        slugs = [changeset.slug(c.name) for c in all_contests]
        for contest, slug_word in zip(all_contests, slugs, strict=True):
            if slugs.count(slug_word) > 1:
                slug_word = f"{slug_word}-{contest.code}"
            contest_id = key("contest", year, kind, contest.code)
            listed = any(any(cells.values()) for cells in contest.parties.values())
            p.add(
                "contest",
                id=contest_id,
                election_id=election_id,
                kind="list_allocation" if listed else "body_seats",
                body_id=council(contest.code) if kind == "KO" else region_body(contest.code),
                district_area_id=area(MUNICIPALITY if kind == "KO" else COUNTY, contest.code),
                round_number=1,
                default_result_kind="counted",
            )
            p.name(contest_id, contest.name, "nb", "official")
            p.add("slug", path=f"{path}/{slug_word}", entity_id=contest_id, is_current=True)
            set_id = key("result_set", year, kind, contest.code)
            p.add(
                "result_set",
                id=set_id,
                election_id=election_id,
                contest_id=contest_id,
                stage="official",
                kind="counted",
                source_id=source_id(votes_table if listed else turnout_table),
                status="active",
                is_default=True,
                completeness=1.0,
            )
            extra = (
                ""
                if listed
                else "Statistics Norway's table of list votes has no votes for this municipality, whose "
                "council it elected without competing lists, so only the ballots counted are stored. "
            )
            for unit, number in contest.negative.items():
                extra += (
                    f" The turnout table prints {number} rejected votes for this municipality, because it counts "
                    f"{contest.figures[unit]['valid']:,} approved votes against {contest.figures[unit]['cast']:,} "
                    "votes cast; the negative number is not stored."
                )
                negative.append(
                    f"{region_name(unit)} {unit} ({contest.figures[unit]['valid']:,} approved votes, "
                    f"{contest.figures[unit]['cast']:,} votes cast)"
                )
            if not has_turnout:
                extra += (
                    "The turnout table starts in 1955, so for this election only the list votes are stored, "
                    "without ballots cast or rejected."
                )
            tables = f"{votes_table} and {turnout_table}" if has_turnout else votes_table
            p.add(
                "note",
                id=key("note", set_id),
                entity_id=set_id,
                kind="methodology",
                body_md=METHOD.format(tables=tables, extra=extra.strip()),
                lang="en",
            )
            for table in (votes_table, *((turnout_table,) if has_turnout else ())):
                p.add(
                    "citation",
                    id=key("citation", set_id, table),
                    entity_id=set_id,
                    source_id=source_id(table),
                    locator=json.dumps({"table": table, "year": year, "region": contest.code, "url": table_url(table)}),
                )
            option_ids: dict[str, str] = {}
            for code in sorted({c for cells in contest.parties.values() for c, n in cells.items() if n}):
                option_ids[code] = key("option", year, kind, contest.code, code)
                p.add(
                    "contest_option",
                    id=option_ids[code],
                    contest_id=contest_id,
                    kind="others" if code in COLLECTIVE else "list",
                    party_id=None if code in COLLECTIVE else party(code),
                    ballot_label=names[code],
                )
            totals: dict[int, int] = {}
            for unit, cells in contest.parties.items():
                unit_id = key("unit", year, kind, unit)
                p.add(
                    "reporting_unit",
                    id=unit_id,
                    polity_id=NORWAY,
                    scheme_code=MUNICIPALITY,
                    code=f"{year}:{kind}:{unit}",
                    area_relation="equals",
                    area_id=area(MUNICIPALITY, unit),
                    kind="standard",
                )
                p.name(unit_id, region_name(unit), "nb", "official")
                facts: list[tuple[str | None, int, int]] = [
                    (option_ids[code], 1, number) for code, number in cells.items() if number
                ]
                figures = contest.figures.get(unit, {})
                facts += [(None, measure, figures[name]) for name, measure in MEASURES if name in figures]
                for option_id, measure, number in facts:
                    totals[measure] = totals.get(measure, 0) + number if option_id is None else totals.get(measure, 0)
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
                if listed and "valid" in figures and figures["valid"] != sum(cells.values()):
                    disagree.append(
                        f"{region_name(unit)} {unit} in {kind} (list votes {sum(cells.values()):,}, "
                        f"approved votes {figures['valid']:,})"
                    )
            for name, number in contest.declared.items():
                measure = dict(MEASURES)[name]
                if totals.get(measure) != number:
                    raise ValueError(
                        f"{year} {kind} {contest.name}: {name} sums to {totals.get(measure)}, not {number}"
                    )
                p.add("declared", result_set_id=set_id, option_id=None, measure=measure, value=number)
    if negative:
        p.accept(
            "accounting.ballots_sum",
            "Statistics Norway's turnout table counts more approved votes than votes cast in "
            + "; ".join(negative)
            + ", and prints the difference as a negative number of rejected votes; the rejected votes are left out "
            "and the other figures are stored as published.",
        )
    if disagree:
        many = len(disagree) > 1
        p.accept(
            "accounting.votes_valid",
            "Statistics Norway's table of list votes and its turnout table disagree for "
            f"{len(disagree)} {'municipalities' if many else 'municipality'} in this year's elections, so the list "
            "votes add up to a different number than the approved votes: " + "; ".join(disagree) + ". Both are "
            "stored as published.",
        )
    return p
