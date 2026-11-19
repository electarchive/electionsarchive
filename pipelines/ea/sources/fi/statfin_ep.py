"""The European Parliament elections of 1996 and 1999 in Finland, by municipality, from Statistics Finland's StatFin tables.

StatFin table 14gu (data on voting) and table 14gv (support for parties) of the `euvaa` database hold every
European Parliament election from 1996, by constituency and municipality. The elections of 2004 and 2009 are already in
the archive from the Ministry of Justice's results service (`fi.vaalit`), with the same municipal detail, so this
pipeline builds only the two elections that source lacks. Each election is one national list allocation on the country
area, reported by municipality.
"""

import functools
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.fi import vaalit

SOURCE = "fi.statfin_ep"
BASE = "https://statfin.stat.fi/PXWeb/api/v1/en/StatFin/euvaa"
TURNOUT, SUPPORT, ELECTED = "14gu", "14gv", "14h8"
CANDIDATE = "vaalitiedot_42_20240612"
SEX = {TURNOUT: "sukupuoli_9_20180101", SUPPORT: "sukupuoli_25_20190101"}
PLACE = "kunta_117_20240101"
LISTING = "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/evaa/001_oi57_statfin_tulos1_2003_fi.px"
FINLAND = vaalit.FINLAND

# The sessions of the tables that this pipeline builds
DAYS = {1996: date(1996, 10, 20), 1999: date(1999, 6, 13)}

MAGNITUDE = 16
REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5
VOTES_MEASURE = 1


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


STATFIN = key("source", "statfin")
BOUNDARIES_NOTE = (
    "Statistics Finland publishes its municipal boundaries only from 2013, so the municipalities that merged away "
    "before then have no shape in the archive."
)


def body(table: str, year: int) -> dict[str, object]:
    """One year of a table, as the whole country, every constituency, and every municipality, for both sexes."""
    return {
        "query": [
            {"code": "timeperiod_y", "selection": {"filter": "item", "values": [str(year)]}},
            {"code": SEX[table], "selection": {"filter": "item", "values": ["SSS"]}},
            {"code": PLACE, "selection": {"filter": "all", "values": ["*"]}},
        ],
        "response": {"format": "json-stat2"},
    }


def elected_body(year: int) -> dict[str, object]:
    """The candidates of one year with their votes; those with a figure are the elected members."""
    return {
        "query": [
            {"code": "timeperiod_y", "selection": {"filter": "item", "values": [str(year)]}},
            {"code": "contentscode", "selection": {"filter": "item", "values": ["aanet_euvaa_as"]}},
        ],
        "response": {"format": "json-stat2"},
    }


def url(table: str) -> str:
    return f"{BASE}/{table}.px"


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".json")
    for year in DAYS:
        for table in (TURNOUT, SUPPORT):
            raw.fetch(SOURCE, url(table), suffix=".json", body=body(table, year))
        raw.fetch(SOURCE, url(ELECTED), suffix=".json", body=elected_body(year))


def artifacts(year: int) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url(table), body(table, year)) for table in (TURNOUT, SUPPORT)] + [
        raw.cached(SOURCE, url(ELECTED), elected_body(year))
    ]


def seats(year: int) -> dict[str, int]:
    """The seats of each list, by the party's abbreviation, counted from the members elected in table 14h8."""
    table = cube(artifacts(year)[2])
    won: dict[str, int] = {}
    for named, _ in rows(table):
        code = named[CANDIDATE]
        if len(code) > 3:
            abbreviation = table.labels[CANDIDATE][code[:3]].lower()
            won[abbreviation] = won.get(abbreviation, 0) + 1
    return won


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS.values())]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------------------------
# Reading the tables


@dataclass(frozen=True)
class Cube:
    """A json-stat2 table, read into cells keyed by the codes of its dimensions."""

    labels: dict[str, dict[str, str]]
    cells: dict[tuple[str, ...], float]
    order: list[str]


def cube(artifact: raw.Artifact) -> Cube:
    with artifact.path.open("rb") as file:
        data = json.load(file)
    order = list(data["id"])
    codes = {name: list(data["dimension"][name]["category"]["index"]) for name in order}
    labels = {name: data["dimension"][name]["category"]["label"] for name in order}
    cells: dict[tuple[str, ...], float] = {}
    sizes = [len(codes[name]) for name in order]
    for position, value in enumerate(data["value"]):
        if value is None:
            continue
        rest, at = position, []
        for size in reversed(sizes):
            rest, remainder = divmod(rest, size)
            at.append(remainder)
        at.reverse()
        cells[tuple(codes[name][i] for name, i in zip(order, at, strict=True))] = value
    return Cube(labels, cells, order)


def rows(table: Cube, **fixed: str) -> Iterator[tuple[dict[str, str], float]]:
    """Each cell with the codes of its dimensions, keeping those whose codes match the fixed ones."""
    for at, value in table.cells.items():
        named = dict(zip(table.order, at, strict=True))
        if all(named[name] == code for name, code in fixed.items()):
            yield named, value


def whole(value: float) -> int:
    if value != int(value):
        raise ValueError(f"{value} is not a whole count")
    return int(value)


NATIONAL = "00000"
MEASURES = {"euvaa_ao": REGISTERED, "euvaa_aanest": CAST, "euvaa_hylatyt": INVALID}


def municipal(code: str) -> str | None:
    """The municipality number of a place code, or none for the country and a constituency total."""
    return code[2:] if code != NATIONAL and not code.endswith("000") else None


@dataclass(frozen=True)
class Count:
    """One year's counts: for each place, its voters, then for each place and list, its votes."""

    names: dict[str, str]
    voters: dict[str, dict[int, int]]
    lists: dict[str, dict[str, int]]
    listed: dict[str, str]


@functools.cache
def read(year: int) -> Count:
    turnout, support, _ = artifacts(year)
    first, second = cube(turnout), cube(support)
    place = {
        code: re.sub(r"\s+-\d\d$", "", re.sub(r"^\d+\s+", "", label)) for code, label in first.labels[PLACE].items()
    }
    voters: dict[str, dict[int, int]] = {}
    for named, value in rows(first):
        measure = MEASURES.get(named["contentscode"])
        if measure is not None:
            voters.setdefault(named[PLACE], {})[measure] = whole(value)
    lists: dict[str, dict[str, int]] = {}
    listed: dict[str, str] = {}
    for named, value in rows(second, contentscode="euvaa_aanet"):
        party = named["puolue_22_20240101"]
        lists.setdefault(named[PLACE], {})[party] = whole(value)
        listed[party] = second.labels["puolue_22_20240101"][party]
    return Count(place, voters, lists, listed)


# ---------------------------------------------------------------------------------------------------------------------
# Places


def area(code: str) -> str:
    return vaalit.area("fi.kunta", code)


@functools.cache
def known() -> frozenset[str]:
    """The municipality numbers that the archive's earlier Finnish packages already name, through the election of 2003."""
    table = json.loads(raw.cached(SOURCE, LISTING).path.read_text())
    (variable,) = [v for v in table["variables"] if v["code"] == "Alue"]
    return frozenset(v for v in variable["values"] if re.fullmatch(r"\d{3}", v))


@functools.cache
def towns() -> dict[str, str]:
    """Each municipality of the elections here, by number, with its Finnish name."""
    found: dict[str, str] = {}
    for year in DAYS:
        count = read(year)
        for code in count.voters:
            number = municipal(code)
            if number:
                found.setdefault(number, count.names[code])
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The municipalities of the European Parliament elections of 1996 and 1999 that merged away before 2003, "
        "beside the areas of the Ministry of Justice's results service",
        depends=[f"{vaalit.SOURCE}/geo"],
    )
    p.external.add(FINLAND)
    for number, name in sorted(towns().items()):
        if number in known():
            continue
        id = area(number)
        p.add("area", id=id, scheme_code="fi.kunta")
        p.add("area_code", area_id=id, code=number)
        p.add("area_parent", area_id=id, parent_id=vaalit.COUNTRY)
        p.name(id, name, "fi", "official")
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="caveat",
            body_md=f"The municipality merged into another before the 2003 elections. {BOUNDARIES_NOTE}",
            lang="en",
        )
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Parties


@dataclass(frozen=True)
class Party:
    names: tuple[str, ...]
    abbreviations: tuple[str, ...]


PARTIES = {
    "kok": Party(("National Coalition Party",), ("KOK",)),
    "ps": Party(("The Finns Party", "True Finns"), ("PS",)),
    "sdp": Party(("Finnish Social Democratic Party",), ("SDP",)),
    "kesk": Party(("Centre Party of Finland",), ("KESK",)),
    "vas": Party(("Left Alliance",), ("VAS",)),
    "vihr": Party(("The Greens", "Green League"), ("VIHR",)),
    "rkp": Party(("Swedish People's Party of Finland",), ("RKP",)),
    "kd": Party(("Christian Democrats in Finland", "Christian League of Finland"), ("KD", "SKL")),
    "skp": Party(("Communist Party of Finland",), ("SKP",)),
    "ip": Party(("Independence Party",), ("IP",)),
    "ka": Party(("For the Poor",), ("KA",)),
    "eka": Party(("Pensioners for the People",), ("EKA",)),
    "lkp": Party(("Liberal Party",), ("LKP",)),
    "sep": Party(("Finnish Pensioners Party",), ("SEP",)),
    "kipu": Party(("Ecological Party Kipu",), ("KIPU",)),
    "nuors": Party(("Progressive Finnish Party",), ("NUORS",)),
    "veu": Party(("Alternative to EU",), ("VEU",)),
}
# The table's party codes, and the party each one stands for in each election; a code that the table reuses for a
# different party in a later election is not listed here for that election.
CODES = {
    "01": {1996: "kok", 1999: "kok"},
    "02": {1996: "ps", 1999: "ps"},
    "03": {1996: "sdp", 1999: "sdp"},
    "04": {1996: "kesk", 1999: "kesk"},
    "05": {1996: "vihr", 1999: "vihr"},
    "06": {1996: "vas", 1999: "vas"},
    "07": {1996: "rkp", 1999: "rkp"},
    "08": {1996: "kd", 1999: "kd"},
    "11": {1999: "skp"},
    "80": {1996: "eka", 1999: "eka"},
    "88": {1999: "kipu"},
    "89": {1996: "nuors"},
    "90": {1996: "veu"},
    "91": {1996: "lkp"},
    "92": {1996: "sep"},
    "93": {1996: "ka"},
    "96": {1996: "ip"},
}
OTHERS = "99"


def party(name: str) -> str:
    return key("party", name)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source, and the parties that stood in the European Parliament elections of 1996 and 1999",
    )
    p.external.add(FINLAND)
    p.add(
        "source",
        id=STATFIN,
        kind="official_body",
        publisher="Tilastokeskus",
        title="StatFin, European Parliament elections (euvaa): data on voting and support for parties, 1996-2024",
        url="https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__euvaa/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Tilastokeskus, StatFin",
    )
    p.artifact(raw.cached(SOURCE, LISTING), STATFIN)
    for year in DAYS:
        for artifact in artifacts(year):
            p.artifact(artifact, STATFIN)
    for name, entry in PARTIES.items():
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=FINLAND)
        for index, text in enumerate(entry.names):
            p.name(id, text, "en", "official" if index == 0 else "historical", index == 0)
        for text in entry.abbreviations:
            p.name(id, text, "en", "abbreviation", False)
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Elections

METHOD = (
    "Statistics Finland's StatFin tables 14gu, 14gv, and 14h8, which hold the figures of the Ministry of Justice's election "
    "information system by municipality. Registered voters are the persons entitled to vote, which include Finnish "
    "citizens living abroad and, from 1996, citizens of other European Union countries living in Finland; ballots "
    "cast are the persons who voted. Valid ballots are the votes for all lists, and invalid ballots are the rejected "
    "ones. A voter abroad is counted in the municipality that the voting register assigns. Each list holds its votes; "
    "seats are the members elected, counted per list from StatFin table 14h8, whose 16 elected candidates each belong to one list. Candidate votes are not stored. Lists that stood in no municipality have "
    "no counts there."
)


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} European Parliament election in Finland by municipality from Statistics Finland's StatFin tables",
        depends=[f"{vaalit.SOURCE}/geo", f"{vaalit.SOURCE}/registry", f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(FINLAND)
    for artifact in artifacts(year):
        p.uses(artifact)
    count = read(year)
    english, finnish, swedish = vaalit.TITLES["EP"]
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=FINLAND,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election, f"{year} {english}", "en", "official")
    p.name(election, f"{finnish} {year}", "fi", "native", False)
    p.name(election, f"{swedish} {year}", "sv", "native", False)
    path = f"fi/{day}-{vaalit.SLUGS['EP']}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=None,
        district_area_id=vaalit.COUNTRY,
        magnitude=MAGNITUDE,
        round_number=1,
        electoral_system_id=vaalit.system_of_kind("EP"),
        default_result_kind="counted",
    )
    p.name(contest, f"{year} {english}", "en", "official")
    p.add("slug", path=f"{path}/finland", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=STATFIN,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")

    totals = count.lists[NATIONAL]
    options: dict[str, str] = {}
    for code in sorted(totals):
        if code == "00":
            continue
        option = options[code] = key("option", day, code)
        if code == OTHERS or year not in CODES.get(code, {}):
            if code != OTHERS:
                raise ValueError(f"{year}: list code {code} {count.listed[code]} has no party")
            label, owner = "Others", None
        else:
            owner = party(CODES[code][year])
            label = count.listed[code]
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=owner,
            ballot_position=None,
            ballot_label=label,
        )
    won = seats(year)
    if sum(won.values()) != MAGNITUDE:
        raise ValueError(f"{year}: {sum(won.values())} seats, not {MAGNITUDE}")
    for code, option in options.items():
        if code != OTHERS and won.get(CODES[code][year], 0):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won.pop(CODES[code][year]))
    if won:
        raise ValueError(f"{year}: seats for lists without a ballot option: {won}")
    for measure, value in count.voters[NATIONAL].items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=totals["00"])
    for code, option in options.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES_MEASURE, value=totals[code])

    values: list[tuple[str, str, str | None, int, int]] = []
    for code in sorted(count.voters):
        number = municipal(code)
        if number is None:
            continue
        unit = key("unit", day, number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=FINLAND,
            scheme_code="fi.kuntalaskenta",
            code=f"{day}:{number}",
            area_relation="equals",
            area_id=area(number),
            kind="standard",
        )
        p.name(unit, count.names[code], "fi", "official")
        for measure, value in count.voters[code].items():
            values.append((result_set, unit, None, measure, value))
        votes = count.lists.get(code, {})
        if "00" in votes:
            values.append((result_set, unit, None, VALID, votes["00"]))
        for list_code, option in options.items():
            if list_code in votes:
                values.append((result_set, unit, option, VOTES_MEASURE, votes[list_code]))
    p.frame("result_value", frame(values))
    return p


def frame(values: list[tuple[str, str, str | None, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        values,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int64,
            "value": pl.Int64,
        },
        orient="row",
    ).select(
        "result_set_id",
        "unit_id",
        "option_id",
        channel=pl.lit(0),
        measure=pl.col("measure"),
        count_round=pl.lit(0),
        flags=pl.lit(0),
        value=pl.col("value"),
    )
