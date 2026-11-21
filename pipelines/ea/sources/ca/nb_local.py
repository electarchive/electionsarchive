"""New Brunswick's quadrennial local government elections of 2012, 2016, and 2021, from the Municipal Electoral
Officer's reports.

Elections NB holds the elections for the councils of the province's municipalities and rural communities on the
second Monday of May every fourth year, and publishes a report of each. The reports of 2012 and 2016 hold a general
results table for each council (every candidate's votes in every race, and who was elected) and, in poll-by-poll
tables, the votes at each polling division; from 2021 on the polling division tables are published only as
spreadsheets, which this pipeline does not yet read. So the results here are each council's totals: one reporting
unit for the municipality and, for a ward councillor's race, one for the ward. A council's electors and the electors
who voted come from the report's recapitulation. A race that was recounted has a first count and a recount set, the
recount the default.

The elections held between these (by-elections, first elections, and the 2021 by-elections), the plebiscites on the
ballots, and the district education council and health board elections are left out.
"""

import functools
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Final, NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.ca import nb_local_read as read
from ea.sources.licenses import UNLICENSED

SOURCE: Final = "ca.nb_local"
SITE: Final = "https://www.electionsnb.ca"
INDEX: Final = SITE + "/content/enb/en/resources/publications/election-results.html"
# Canada's province polity and province area, which the federal pipeline holds under ids it minted itself.
NEW_BRUNSWICK: Final = "01a13fb6-eab7-7501-a0f9-4902d09fd447"
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f50feff417"

REPORTS: Final[dict[int, tuple[date, str]]] = {
    2012: (date(2012, 5, 14), SITE + "/content/dam/enb/pdf/2012-05-14-Municipal.pdf"),
    2016: (date(2016, 5, 9), SITE + "/content/dam/enb/pdf/2016-05-09-Municipal.pdf"),
    2021: (
        date(2021, 5, 10),
        "https://legnb.ca/content/house_business/60/1/tabled_documents/9/general_elections_2021-elections_generales_2021.pdf",
    ),
}
# A council that the reports spell two ways.
SAME: Final = [("tracadie", "tracadie sheila")]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ENB = key("source", "enb")
PLURALITY = key("electoral_system", "plurality")
BLOCK = key("electoral_system", "block")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def slug(text: str) -> str:
    return fold(text).replace(" ", "-")


def fetch() -> None:
    for _, url in REPORTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in REPORTS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in REPORTS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------- reading


class Report(NamedTuple):
    councils: list[read.Council]
    recounts: list[read.Recount]
    recap: dict[str, tuple[int, int | None]]


@functools.cache
def report(year: int) -> Report:
    rows = read.lines(Path(raw.cached(SOURCE, REPORTS[year][1]).path))
    return Report(read.councils(rows), read.recounts(rows), read.recap(rows))


@functools.cache
def identities() -> dict[str, str]:
    """Each folded council name in any report, mapped to the key of the council it names."""
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        while parent.setdefault(name, name) != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for year in REPORTS:
        for council in report(year).councils:
            names = [fold(alias) for alias in read.aliases(council.name)]
            find(names[0])
            for other in names[1:]:
                parent[find(other)] = find(names[0])
    for one, two in SAME:
        parent[find(two)] = find(one)
    return {name: find(name) for name in list(parent)}


def council_key(name: str) -> str:
    return identities()[fold(read.aliases(name)[0])]


def label(name: str) -> str:
    """A council's heading as one name: the first of its names, in title case when the heading is in capitals."""
    first = read.aliases(name)[0]
    return first.title() if first.isupper() else first


@functools.cache
def labels() -> dict[str, str]:
    """The name each council is known by, from its latest report."""
    found: dict[str, str] = {}
    for year in sorted(REPORTS):
        for council in report(year).councils:
            found[council_key(council.name)] = label(council.name)
    return found


@functools.cache
def second_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in sorted(REPORTS):
        for council in report(year).councils:
            aliases = read.aliases(council.name)
            if len(aliases) > 1 and not aliases[1].isupper():
                found[council_key(council.name)] = aliases[1]
    return found


def ward_label(text: str) -> str:
    text = text.replace("Sud/South", "South").replace("Nord/North", "North").replace("Quartier ", "")
    return text.strip()


# ---------------------------------------------------------------------------------------------------- geography


def province_scheme(p: Package) -> None:
    for code, kind, rank, noun in (
        ("ca-nb.municipality", "administrative", 3, "municipality"),
        ("ca-nb.ward", "electoral_district", 4, "ward"),
        ("ca-nb.result_unit", "precinct", 5, "reporting unit"),
    ):
        p.add("scheme", code=code, polity_id=NEW_BRUNSWICK, kind=kind, rank=rank, noun=noun, code_system=None)


def polity(council: str) -> str:
    return key("polity", council)


def area(council: str) -> str:
    return key("area", "council", council)


def ward_area(council: str, ward: str) -> str:
    return key("area", "ward", council, ward)


@functools.cache
def wards() -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for year in REPORTS:
        for council in report(year).councils:
            for race in council.races:
                if race.ward:
                    found[council_key(council.name)].add(ward_label(race.ward))
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The municipalities and rural communities of New Brunswick that held local elections from 2012 to 2021, "
        "and the wards of their councillors, without outlines",
        depends=[],
    )
    p.external |= {NEW_BRUNSWICK, PROVINCE_AREA}
    province_scheme(p)
    names = labels()
    other = second_names()
    for council, name in sorted(names.items(), key=lambda item: item[1]):
        place = area(council)
        p.add("area", id=place, scheme_code="ca-nb.municipality")
        p.add("area_parent", area_id=place, parent_id=PROVINCE_AREA)
        p.name(place, name, "en", "official")
        town = polity(council)
        p.add("polity", id=town, kind="municipality", iso_code=None)
        p.name(town, name, "en", "official")
        if council in other and fold(other[council]) != fold(name):
            p.name(place, other[council], "fr", "official", preferred=False)
            p.name(town, other[council], "fr", "official", preferred=False)
        p.add("polity_parent", polity_id=town, parent_id=NEW_BRUNSWICK)
        p.add("polity_territory", polity_id=town, area_id=place)
        p.add("slug", path=f"ca/nb/{slug(name)}", entity_id=town, is_current=True)
        for ward in sorted(wards().get(council, ())):
            unit = ward_area(council, ward)
            p.add("area", id=unit, scheme_code="ca-nb.ward")
            p.add("area_parent", area_id=unit, parent_id=place)
            p.name(unit, f"{name}, Ward {ward}", "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------- registry


def body(council: str) -> str:
    return key("body", council)


def mayoralty(council: str) -> str:
    return key("office", council)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils and mayoralties of New Brunswick's municipalities and rural communities, the electoral "
        "systems of their elections, and the reports of Elections NB their results come from",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEW_BRUNSWICK}
    p.add(
        "source",
        id=ENB,
        kind="official_body",
        publisher="Elections NB",
        title="Reports of the Municipal Electoral Officer on the quadrennial local elections of 2012, 2016, and 2021",
        url=INDEX,
        license=UNLICENSED,
        license_url=None,
        attribution="Elections NB",
    )
    for _, url in REPORTS.values():
        p.artifact(raw.cached(SOURCE, url), ENB)
    for system, formula, title in (
        (PLURALITY, "fptp", "New Brunswick local plurality voting"),
        (BLOCK, "block", "New Brunswick local block voting"),
    ):
        p.add(
            "electoral_system",
            id=system,
            polity_id=NEW_BRUNSWICK,
            valid_from=None,
            valid_to=None,
            rules={"formula": formula},
            rules_version=1,
        )
        p.name(system, title, "en", "official")
    for council, name in sorted(labels().items(), key=lambda item: item[1]):
        p.add("body", id=body(council), polity_id=polity(council), kind="council")
        p.name(body(council), f"{name} council", "en", "official")
        p.add("office", id=mayoralty(council), polity_id=polity(council), kind="mayor")
        p.name(mayoralty(council), f"Mayor of {name}", "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------- elections


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(sorted(fold(name).split()))


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


NOTES: dict[int, list[str]] = {}


def election(year: int) -> Package:
    day, url = REPORTS[year]
    close = day + timedelta(days=1)
    data = report(year)
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {day:%B} {day.day}, {year} local elections of New Brunswick's municipalities and rural communities: "
        "each council's mayor and councillors, with the votes of every candidate",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEW_BRUNSWICK}
    p.uses(raw.cached(SOURCE, url))
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=NEW_BRUNSWICK, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} New Brunswick local elections", "en", "official")
    p.add("slug", path=f"ca/nb/{day}-local", entity_id=umbrella, is_current=True)
    p.add(
        "note",
        id=key("note", year),
        entity_id=umbrella,
        kind="methodology",
        body_md=(
            "The Municipal Electoral Officer's report gives each candidate's votes for each council and race, "
            "and who was elected. Votes are stored for the council as a whole, or for the ward of a ward "
            "councillor's race, as the report's general results print them. The electors on the final list and "
            "the electors who voted come from the report's recapitulation. Where a race was recounted, the "
            "first count and the recount are both stored, and the recount is the default. Plebiscites on the "
            "same ballots and the district education council and health board elections are not included."
        ),
        lang="en",
    )
    recap = {
        council_key(name): found for name, found in data.recap.items() if fold(read.aliases(name)[0]) in identities()
    }
    recount_by: dict[str, list[read.Recount]] = defaultdict(list)
    for found in data.recounts:
        recount_by[council_key(found.council)].append(found)  # the last of a race's recounts is the judicial one
    spent: set[int] = set()
    seen = set()
    problems: list[str] = []
    taken: set[str] = set()
    for council in data.councils:
        ckey = council_key(council.name)
        name = labels()[ckey]
        if ckey in seen:
            problems.append(f"{year} {name} appears twice")
        seen.add(ckey)
        vote = key("election", year, ckey)
        p.add(
            "election",
            id=vote,
            polity_id=polity(ckey),
            parent_id=umbrella,
            kind="local",
            date_from=day,
            date_to=day,
            status="held",
        )
        p.name(vote, f"{year} {name} election", "en", "official")
        p.add("slug", path=f"ca/nb/{slug(name)}/{day}-local", entity_id=vote, is_current=True)
        units: dict[str, str] = {}

        def unit_for(place: str) -> str:
            if place not in units:
                unit = units[place] = key("unit", day, ckey, place)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=NEW_BRUNSWICK,
                    scheme_code="ca-nb.result_unit",
                    code=f"{day}:{ckey}:{place}",
                    area_relation="within",
                    area_id=ward_area(ckey, place) if place else area(ckey),
                    kind="standard",
                    valid_from=day,
                    valid_to=close,
                )
                p.name(unit, f"{name}, Ward {place}" if place else name, "en", "official")
            return units[place]

        turnout = recap.get(ckey)
        used = False
        counted_first = [r for r in council.races if r.office == "mayor"] + [
            r for r in council.races if r.office == "councillor" and not r.ward
        ]
        for race in council.races:
            ward = ward_label(race.ward)
            mayoral = race.office == "mayor"
            tag = f"{ckey}:{race.office}:{ward}"
            contest = key("contest", year, tag)
            people = race.candidates
            if not people:
                problems.append(f"{year} {name} {tag} has no candidates")
                continue
            names_here = {_tokens(person.name) for person in people}
            matches = [
                r
                for r in recount_by.get(ckey, [])
                if r.race.office == race.office and {_tokens(c.name) for c in r.race.candidates} <= names_here
            ]
            recount = matches[-1] if matches else None
            spent.update(id(r) for r in matches)
            # Each candidate's votes and whether elected once any recount is applied.
            final: dict[str, tuple[int | None, bool]] = {c.name: (c.votes, c.elected) for c in people}
            if recount is not None:
                by_token = {_tokens(c.name): c for c in people}
                for c in recount.race.candidates:
                    final[by_token[_tokens(c.name)].name] = (c.votes, c.elected)
            elected = [who for who, (_, flag) in final.items() if flag]
            if not elected:
                problems.append(f"{year} {name} {tag} elects no one")
            counted = any(c.votes is not None for c in people)
            if counted and any(c.votes is None for c in people):
                problems.append(f"{year} {name} {tag} mixes acclaimed and counted candidates")
            if mayoral:
                seats = 1
            elif not ward:
                seats = max(council.seats.get("large", 0), len(elected))
            else:
                seats = len(elected)
            title = (
                f"Mayor of {name}"
                if mayoral
                else f"Councillors, {name}"
                if not ward
                else f"Councillor, {name}, Ward {ward}"
            )
            p.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="office" if mayoral else "body_seats",
                office_id=mayoralty(ckey) if mayoral else None,
                body_id=None if mayoral else body(ckey),
                district_area_id=ward_area(ckey, ward) if ward else area(ckey),
                magnitude=seats,
                round_number=1,
                electoral_system_id=PLURALITY if seats == 1 else BLOCK,
                default_result_kind="counted",
            )
            p.name(contest, f"{year} {title}", "en", "official")
            path = f"ca/nb/{slug(name)}/{day}-local/" + (
                "mayor" if mayoral else "councillors" if not ward else f"councillor-ward-{slug(ward)}"
            )
            while path in taken:
                path += "-2"
            taken.add(path)
            p.add("slug", path=path, entity_id=contest, is_current=True)
            options: dict[str, str] = {}
            for person in people:
                option = options[person.name] = key("option", year, tag, person.name)
                who = key("person", year, tag, person.name)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=None,
                    ballot_label=person.name,
                )
                p.add("person", id=who, birth_precision=None)
                p.name(who, person.name, "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", year, tag, person.name),
                    person_id=who,
                    option_id=option,
                    role="head",
                    ballot_name=person.name,
                    member_of_party_id=None,
                )
            unit = unit_for(ward)
            carries = bool(turnout) and not used and race in counted_first[:1] and counted
            used = used or carries
            sets = [(key("result_set", year, tag), "official", {c.name: (c.votes, c.elected) for c in people})]
            if recount is not None:
                sets.append((key("result_set", year, tag, "recount"), "recount", final))
            for number, (result_set, stage, shown) in enumerate(sets):
                last = number == len(sets) - 1
                p.add(
                    "result_set",
                    id=result_set,
                    election_id=vote,
                    contest_id=contest,
                    stage=stage,
                    kind="counted" if counted else "tacit",
                    source_id=ENB,
                    status="active",
                    is_default=last,
                    supersedes_id=sets[0][0] if number else None,
                    completeness=1.0 if counted else None,
                )
                for person in people:
                    votes, flag = shown[person.name]
                    if flag:
                        p.add("outcome", result_set_id=result_set, option_id=options[person.name], kind="elected")
                    if votes is not None:
                        _value(p, result_set, unit, options[person.name], 1, votes)
                if carries and turnout:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=2, value=turnout[0])
                    if turnout[1] is not None:
                        p.add("declared", result_set_id=result_set, option_id=None, measure=3, value=turnout[1])
    for found in data.recounts:
        if id(found) not in spent:
            problems.append(f"{year} recount of {found.council} {found.race.office} {found.race.ward} matches no race")
    if problems:
        raise ValueError("\n".join(problems))
    p.accept(
        "accounting.declared",
        "the electors on the list and the electors who voted are the council's totals from the report's "
        "recapitulation, which its results tables do not repeat",
    )
    return p
