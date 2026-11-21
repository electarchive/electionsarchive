"""Nova Scotia's municipal elections: the Halifax Regional Municipality's general elections of 2016, 2020, and 2024.

The Municipal Elections Act sets a general election of every municipality's mayor and councillors for the third
Saturday in October every four years, and each municipality runs its own election and publishes its own results.
Nova Scotia has 49 municipalities (the Department of Municipal Affairs and Housing's guide for new councillors
says so). This pipeline starts with the largest, Halifax Regional Municipality, from the Returning Officer's
official results:

- 2024: the mayor's and each of the 16 councillors' races by polling place (the tables of one place are added
  together; the advance polls are a unit of their own for each district), with the Returning Officer's district
  and municipality totals checked against the sums.
- 2016 and 2020: each race's totals by council district (and for the whole municipality for the mayor), from the
  results summary. The poll-by-poll tables in those files are not read yet. The 2020 recount of District 11 is a
  recount result set.

The Conseil scolaire acadien provincial and school board races, the by-elections, plebiscites, and the elections
before 2016 are left out, and so are the other 48 municipalities.
"""

import functools
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Final
from urllib.parse import quote
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.ca import ns_municipal_read as read
from ea.sources.licenses import UNLICENSED

SOURCE: Final = "ca.ns_municipal"
SITE: Final = "https://www.halifax.ca"
INDEX: Final = SITE + "/city-hall/elections/about-elections/past-election-results"
DOCS: Final = SITE + "/sites/default/files/documents/city-hall/elections/"
# Canada's province polity and province area, which the provincial pipeline holds under ids it minted itself.
NOVA_SCOTIA: Final = "01a13fb6-eab7-7501-a0f9-4901bd9f01f5"
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f48b94166e"

DAYS: Final = {2016: date(2016, 10, 15), 2020: date(2020, 10, 17), 2024: date(2024, 10, 19)}
SUMMARY: Final = {
    2016: DOCS + "161015ElectionResults.pdf",
    2020: DOCS + "2020%20Municipal%20%26amp%3B%20CSAP%20Election%20-%20Results.pdf",
}
RECOUNT_2020: Final = DOCS + quote("District 11 - Official Recount.pdf")
MAYOR_2024: Final = DOCS + "2024_election-official-mayor-results.pdf"


def council_2024(district: int) -> str:
    return DOCS + f"2024-election-official-councilor-district-{district:02d}-results.pdf"


def mayor_2024(district: int) -> str:
    return DOCS + f"2024-official-mayor-results-district-{district:02d}.pdf"


DISTRICTS: Final = range(1, 17)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


HRM = key("source", "halifax")
PLURALITY = key("electoral_system", "plurality")
HALIFAX_POLITY = key("polity", "halifax")
HALIFAX_AREA = key("area", "halifax")
COUNCIL = key("body", "halifax")
MAYORALTY = key("office", "halifax")


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def urls() -> list[str]:
    found = [*SUMMARY.values(), RECOUNT_2020, MAYOR_2024]
    for district in DISTRICTS:
        found += [council_2024(district), mayor_2024(district)]
    return found


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def path_of(url: str) -> Path:
    return Path(raw.cached(SOURCE, url).path)


# ---------------------------------------------------------------------------------------------------- reading


@functools.cache
def summary(year: int) -> dict[str, read.Race]:
    return {race.label: race for race in read.summary(path_of(SUMMARY[year]))}


@functools.cache
def sheets(kind: str) -> dict[int, read.Sheet]:
    """The 2024 poll tables: kind is `council` or `mayor`."""
    make = council_2024 if kind == "council" else mayor_2024
    return {district: read.poll_sheet(path_of(make(district))) for district in DISTRICTS}


@functools.cache
def district_titles() -> dict[int, str]:
    return {district: sheet.title for district, sheet in sheets("council").items()}


@functools.cache
def mayor_total_2024() -> tuple[list[int], int]:
    lines = read.text_lines(path_of(MAYOR_2024))
    start = len(lines) - lines[::-1].index("Total")
    figures: list[int] = []
    for line in lines[start:]:
        if not line.isdigit():
            break
        figures.append(int(line))
    return figures[:-1], figures[-1]


# ---------------------------------------------------------------------------------------------------- geography


def district_area(district: int) -> str:
    return key("area", "district", district)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Halifax Regional Municipality and its 16 council districts, without outlines",
        depends=[],
    )
    p.external |= {NOVA_SCOTIA, PROVINCE_AREA}
    for code, kind, rank, noun in (
        ("ca-ns.municipality", "administrative", 3, "municipality"),
        ("ca-ns.district", "electoral_district", 4, "council district"),
        ("ca-ns.result_unit", "precinct", 5, "reporting unit"),
    ):
        p.add("scheme", code=code, polity_id=NOVA_SCOTIA, kind=kind, rank=rank, noun=noun, code_system=None)
    name = "Halifax Regional Municipality"
    p.add("area", id=HALIFAX_AREA, scheme_code="ca-ns.municipality")
    p.add("area_parent", area_id=HALIFAX_AREA, parent_id=PROVINCE_AREA)
    p.name(HALIFAX_AREA, name, "en", "official")
    p.add("polity", id=HALIFAX_POLITY, kind="municipality", iso_code=None)
    p.name(HALIFAX_POLITY, name, "en", "official")
    p.add("polity_parent", polity_id=HALIFAX_POLITY, parent_id=NOVA_SCOTIA)
    p.add("polity_territory", polity_id=HALIFAX_POLITY, area_id=HALIFAX_AREA)
    p.add("slug", path="ca/ns/halifax", entity_id=HALIFAX_POLITY, is_current=True)
    for district, title in district_titles().items():
        unit = district_area(district)
        p.add("area", id=unit, scheme_code="ca-ns.district")
        p.add("area_parent", area_id=unit, parent_id=HALIFAX_AREA)
        p.name(unit, f"{name}, District {district}: {title}", "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------- registry


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The council and mayoralty of Halifax Regional Municipality, the electoral system of its elections, and "
        "the Returning Officer's official results they come from",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NOVA_SCOTIA}
    p.add(
        "source",
        id=HRM,
        kind="official_body",
        publisher="Halifax Regional Municipality",
        title="Official results of the 2016, 2020, and 2024 municipal elections",
        url=INDEX,
        license=UNLICENSED,
        license_url=None,
        attribution="Halifax Regional Municipality",
    )
    for url in urls():
        p.artifact(raw.cached(SOURCE, url), HRM)
    p.add(
        "electoral_system",
        id=PLURALITY,
        polity_id=NOVA_SCOTIA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(PLURALITY, "Nova Scotia municipal plurality voting", "en", "official")
    p.add("body", id=COUNCIL, polity_id=HALIFAX_POLITY, kind="council")
    p.name(COUNCIL, "Halifax Regional Council", "en", "official")
    p.add("office", id=MAYORALTY, polity_id=HALIFAX_POLITY, kind="mayor")
    p.name(MAYORALTY, "Mayor of Halifax Regional Municipality", "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------- elections


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


class Race:
    """One contest of an election: its candidates and, for each result set, their votes by unit."""

    def __init__(self, label: str, names: list[str], acclaimed: bool = False) -> None:
        self.label = label
        self.names = names
        self.acclaimed = acclaimed
        # unit key -> (name, registered, valid, rejected, ballots, votes)
        self.units: list[tuple[str, str, int | None, int | None, int | None, int | None, list[int]]] = []
        self.declared: list[int] | None = None
        self.recount: list[int] | None = None


def races(year: int) -> list[Race]:
    found: list[Race] = []
    if year == 2024:
        mayors = sheets("mayor")
        names = mayors[1].names
        race = Race("Mayor", names)
        for district, sheet in mayors.items():
            if sheet.names != names:
                raise ValueError(f"District {district}'s mayor sheet lists other candidates")
            for poll in sheet.polls:
                race.units.append(
                    (poll.code, _poll_name(poll), poll.registered, poll.valid, poll.rejected, None, poll.votes)
                )
        race.declared, _ = mayor_total_2024()
        found.append(race)
        for district, sheet in sheets("council").items():
            race = Race(f"District {district}", sheet.names)
            for poll in sheet.polls:
                race.units.append(
                    (poll.code, _poll_name(poll), poll.registered, poll.valid, poll.rejected, None, poll.votes)
                )
            race.declared = sheet.totals
            found.append(race)
        return found
    for label, one in summary(year).items():
        race = Race(label, one.names, one.acclaimed)
        if not one.acclaimed:
            valid = sum(one.votes)
            cast = one.total if year == 2016 else None
            race.units.append(("total", "", one.electors, valid, one.rejected, cast, one.votes))
        found.append(race)
    if year == 2020:
        names, votes = read.recount(path_of(RECOUNT_2020))
        race = next(r for r in found if r.label == "District 11")
        if [n.split()[-1].lower() for n in race.names] != [n.lower() for n in names]:
            raise ValueError("the District 11 recount sheet lists other candidates")
        race.recount = votes[:-1]
        if sum(race.recount) != votes[-1]:
            raise ValueError("the District 11 recount does not add up")
    return found


def _poll_name(poll: read.Poll) -> str:
    return f"Poll {poll.code} {poll.name}"


def election(year: int) -> Package:
    day = DAYS[year]
    close = day + timedelta(days=1)
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {day:%B} {day.day}, {year} general election of Halifax Regional Municipality: the mayor and the "
        "councillors of its 16 districts, with the votes of every candidate",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NOVA_SCOTIA}
    p.uses(raw.cached(SOURCE, SUMMARY.get(year, MAYOR_2024)))
    if year == 2020:
        p.uses(raw.cached(SOURCE, RECOUNT_2020))
    if year == 2024:
        for district in DISTRICTS:
            p.uses(raw.cached(SOURCE, council_2024(district)))
            p.uses(raw.cached(SOURCE, mayor_2024(district)))
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=NOVA_SCOTIA, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} Halifax municipal election", "en", "official")
    p.add("slug", path=f"ca/ns/halifax/{day}-local", entity_id=umbrella, is_current=True)
    grain = (
        "by polling place (the tables of a place added together; advance polls are a unit of their own in each "
        "district)"
        if year == 2024
        else "by council district, and for the whole municipality for the mayor"
    )
    p.add(
        "note",
        id=key("note", year),
        entity_id=umbrella,
        kind="methodology",
        body_md=(
            f"The Returning Officer's official results give each candidate's votes {grain}. Who was elected is "
            "the candidate with the most votes in each race, or the one acclaimed. The Conseil scolaire acadien "
            "provincial and school board races held on the same day are not included."
        ),
        lang="en",
    )
    units: dict[tuple[int | None, str], str] = {}

    def unit_for(code: str, name: str, district: int | None) -> str:
        if (district, code) not in units:
            unit = units[district, code] = key("unit", day, district, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NOVA_SCOTIA,
                scheme_code="ca-ns.result_unit",
                code=f"{day}:{district or 0}:{code}",
                area_relation="within" if code[:1].isdigit() else "equals",
                area_id=HALIFAX_AREA if district is None else district_area(district),
                kind="aggregate" if not code[:1].isdigit() else "standard",
                valid_from=day,
                valid_to=close,
            )
            p.name(
                unit,
                name or (f"District {district}" if district else "Halifax Regional Municipality"),
                "en",
                "official",
            )
        return units[district, code]

    for race in races(year):
        mayoral = race.label == "Mayor"
        district = None if mayoral else int(race.label.split()[1])
        tag = "mayor" if mayoral else f"district-{district}"
        contest = key("contest", year, tag)
        p.add(
            "contest",
            id=contest,
            election_id=umbrella,
            kind="office" if mayoral else "body_seats",
            office_id=MAYORALTY if mayoral else None,
            body_id=None if mayoral else COUNCIL,
            district_area_id=HALIFAX_AREA if mayoral else district_area(district or 0),
            magnitude=1,
            round_number=1,
            electoral_system_id=PLURALITY,
            default_result_kind="counted",
        )
        title = "Mayor" if mayoral else f"Councillor, District {district}"
        p.name(contest, f"{year} {title}, Halifax Regional Municipality", "en", "official")
        p.add("slug", path=f"ca/ns/halifax/{day}-local/{tag}", entity_id=contest, is_current=True)
        options: list[str] = []
        for name in race.names:
            option = key("option", year, tag, name)
            who = key("person", year, tag, name)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None,
                ballot_position=None,
                ballot_label=name,
            )
            p.add("person", id=who, birth_precision=None)
            p.name(who, name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", year, tag, name),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=None,
            )
        totals = [0] * len(race.names)
        for _, _, _, _, _, _, votes in race.units:
            totals = [a + b for a, b in zip(totals, votes, strict=True)]
        final = race.recount or totals
        if race.declared is not None and race.declared != totals and not mayoral:
            raise ValueError(f"{year} {tag}: the unit rows add to {totals} but the sheet's totals are {race.declared}")
        sets = [(key("result_set", year, tag), "official", totals)]
        if race.recount:
            sets.append((key("result_set", year, tag, "recount"), "recount", race.recount))
        if race.acclaimed:
            winner = 0
        else:
            top = max(final)
            if final.count(top) != 1:
                raise ValueError(f"{year} {tag} has a tie")
            winner = final.index(top)
        for number, (result_set, stage, shown) in enumerate(sets):
            p.add(
                "result_set",
                id=result_set,
                election_id=umbrella,
                contest_id=contest,
                stage=stage,
                kind="tacit" if race.acclaimed else "counted",
                source_id=HRM,
                status="active",
                is_default=number == len(sets) - 1,
                supersedes_id=sets[0][0] if number else None,
                completeness=None if race.acclaimed else 1.0,
            )
            p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="elected")
            if race.acclaimed:
                continue
            if stage == "recount":
                unit = unit_for("total", "", district)
                for option, count in zip(options, shown, strict=True):
                    _value(p, result_set, unit, option, 1, count)
                _value(p, result_set, unit, None, 4, sum(shown))
                continue
            for code, name, registered, valid, rejected, cast, votes in race.units:
                unit = unit_for(code, name, int(code[:2]) if code[:1].isdigit() else district)
                for option, count in zip(options, votes, strict=True):
                    _value(p, result_set, unit, option, 1, count)
                for measure, value in ((2, registered), (3, cast), (4, valid), (5, rejected)):
                    if value is not None:
                        _value(p, result_set, unit, None, measure, value)
        if race.declared is not None:
            for option, count in zip(options, race.declared, strict=True):
                p.add("declared", result_set_id=sets[0][0], option_id=option, measure=1, value=count)
    if year == 2024:
        p.accept(
            "accounting.votes_valid",
            "at a few polling places the Returning Officer's official total of votes differs from the sum of the "
            "candidates' votes on the same row (the sheets' own figures, for example a table merged into another "
            "or a total that counts ballots the candidate columns do not)",
        )
        p.accept(
            "accounting.declared",
            "the municipality's published mayoral totals exceed the sum of the 16 district sheets by 47 votes "
            "(4 for Darryl Johnson, 31 for Pam Lovelace, 11 for Waye Mason, 1 for Riley Murphy); the polling "
            "place figures are the district sheets, whose own totals rows they match",
        )
    return p
