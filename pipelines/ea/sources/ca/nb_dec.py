"""New Brunswick's district education council elections of 2001, 2004, 2008, 2012, 2016, and 2021, from the
Municipal Electoral Officer's reports.

Elections NB holds the elections for the district education councils with the local government elections. Until 2008
the province had fourteen school districts (nine Anglophone, five Francophone), numbered rather than named, and 158
councillors; since 2012 it has seven (four Anglophone and three Francophone), each divided into subdistricts that
elect one councillor each, 68 in all. An elector may vote in either the Anglophone or the Francophone district they
live in, but not both. Each report prints every subdistrict's candidates with their votes and who was elected, and from
2012 on a recapitulation of each subdistrict's electors and valid votes. A subdistrict with one candidate has a tacit
result; one with none is filled later by a ministerial appointment and has no contest here. In 2004 and 2008 a few
pairs of subdistricts (a "zone") elected two councillors together by block voting.

The polling division tables are printed as grids with rotated headings, and this pipeline does not yet read them, so
the votes here are each subdistrict's totals. The by-elections and the district education council elections before
2001 are also left out.
"""

import functools
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Final
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.ca import nb_dec_read as read
from ea.sources.ca.nb_local import NEW_BRUNSWICK, PROVINCE_AREA
from ea.sources.licenses import UNLICENSED

SOURCE: Final = "ca.nb_dec"
SITE: Final = "https://www.electionsnb.ca"
INDEX: Final = SITE + "/content/enb/en/resources/publications/mehep.html"
REPORTS: Final[dict[int, tuple[date, str]]] = {
    2001: (date(2001, 5, 14), SITE + "/content/dam/enb/pdf/2001DECRpt.pdf"),
    2004: (date(2004, 5, 10), SITE + "/content/dam/enb/pdf/2004DEC-CEDRpt.pdf"),
    2008: (date(2008, 5, 12), SITE + "/content/dam/enb/pdf/2008DEC-CEDRpt.pdf"),
    2012: (date(2012, 5, 14), SITE + "/content/dam/enb/pdf/2012-05-14-DEC-CED.pdf"),
    2016: (date(2016, 5, 9), SITE + "/content/dam/enb/pdf/2016DEC-CEDRpt.pdf"),
    2021: (
        date(2021, 5, 10),
        "https://legnb.ca/content/house_business/60/1/tabled_documents/9/general_elections_2021-elections_generales_2021.pdf",
    ),
}
# The boundaries of the fourteen numbered districts held from 2001 to 2008 and of the seven districts held since 2012.
PLANS: Final = {2001: "2001", 2004: "2001", 2008: "2001", 2012: "2012", 2016: "2012", 2021: "2012"}


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
    raw.fetch(SOURCE, INDEX, suffix=".html")


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


class Report:
    def __init__(self, year: int) -> None:
        rows = read.lines(Path(raw.cached(SOURCE, REPORTS[year][1]).path))
        self.districts, self.stray = read.results(rows)
        self.recap = read.recaps(rows)
        self.recounts = read.recounts(rows)


@functools.cache
def report(year: int) -> Report:
    return Report(year)


def district_name(plan: str, name: str, language: str) -> str:
    return f"{name} School District" if plan == "2012" else f"School District {name.split()[-1]} ({language})"


def district_french(plan: str, name: str, language: str) -> str:
    if plan == "2012":
        return f"District scolaire {name[0].lower()}{name[1:]}"
    return f"District scolaire {name.split()[-1]} ({language.lower()})"


def district_ids() -> dict[tuple[str, str], tuple[str, str]]:
    """Each district by plan and the key the report gives it, with its language."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for year in REPORTS:
        for district in report(year).districts:
            found.setdefault((PLANS[year], district.key), (district.language, district.key))
    return found


def subdistrict_numbers() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for year in REPORTS:
        for district in report(year).districts:
            found.setdefault((PLANS[year], district.key), set()).update(district.subdistricts)
    return found


# ---------------------------------------------------------------------------------------------------- geography


def polity(plan: str, name: str) -> str:
    return key("polity", plan, name)


def district_area(plan: str, name: str) -> str:
    return key("area", "district", plan, name)


def sub_area(plan: str, name: str, label: str) -> str:
    return key("area", "subdistrict", plan, name, label)


def sub_title(label: str) -> str:
    if "zone" in label:
        first, rest = label.split("+")
        second, zone = rest.split(" zone ")
        return f"Subdistricts {first} and {second} (Zone {zone})"
    return f"Subdistrict {label}"


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "New Brunswick's school districts and their district education council subdistricts, as the councils were "
        "elected from 2001 to 2008 and from 2012 to 2021, without outlines",
        depends=[],
    )
    p.external |= {NEW_BRUNSWICK, PROVINCE_AREA}
    for code, kind, rank, noun in (
        ("ca-nb.school_district", "administrative", 3, "school district"),
        ("ca-nb.school_subdistrict", "electoral_district", 4, "school subdistrict"),
        ("ca-nb.dec_result_unit", "precinct", 5, "reporting unit"),
    ):
        p.add("scheme", code=code, polity_id=NEW_BRUNSWICK, kind=kind, rank=rank, noun=noun, code_system=None)
    subs = subdistrict_numbers()
    for (plan, name), (language, _) in sorted(district_ids().items()):
        title = district_name(plan, name, language)
        town = polity(plan, name)
        place = district_area(plan, name)
        p.add("area", id=place, scheme_code="ca-nb.school_district")
        p.add("area_parent", area_id=place, parent_id=PROVINCE_AREA)
        p.name(place, title, "en", "official")
        p.name(place, district_french(plan, name, language), "fr", "official", preferred=False)
        p.add("polity", id=town, kind="school_district", iso_code=None)
        p.name(town, title, "en", "official")
        p.name(town, district_french(plan, name, language), "fr", "official", preferred=False)
        p.add("polity_parent", polity_id=town, parent_id=NEW_BRUNSWICK)
        p.add("polity_territory", polity_id=town, area_id=place)
        p.add("slug", path=f"ca/nb/{slug(title)}", entity_id=town, is_current=True)
        for label in sorted(subs[(plan, name)], key=lambda text: [int(n) for n in re.findall(r"\d+", text)]):
            unit = sub_area(plan, name, label)
            p.add("area", id=unit, scheme_code="ca-nb.school_subdistrict")
            p.add("area_parent", area_id=unit, parent_id=place)
            p.name(unit, f"{title}, {sub_title(label)}", "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------- registry


def body(plan: str, name: str) -> str:
    return key("body", plan, name)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The district education councils of New Brunswick, the electoral systems of their elections, and the reports "
        "of Elections NB their results come from",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEW_BRUNSWICK}
    p.add(
        "source",
        id=ENB,
        kind="official_body",
        publisher="Elections NB",
        title="Reports of the Municipal Electoral Officer on the district education council elections of 2001 to 2021",
        url=INDEX,
        license=UNLICENSED,
        license_url=None,
        attribution="Elections NB",
    )
    for _, url in REPORTS.values():
        p.artifact(raw.cached(SOURCE, url), ENB)
    for system, formula, title in (
        (PLURALITY, "fptp", "New Brunswick district education council plurality voting"),
        (BLOCK, "block", "New Brunswick district education council block voting"),
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
    for (plan, name), (language, _) in sorted(district_ids().items()):
        title = district_name(plan, name, language)
        p.add("body", id=body(plan, name), polity_id=polity(plan, name), kind="council")
        p.name(body(plan, name), f"{title} District Education Council", "en", "official")
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


def election(year: int) -> Package:
    day, url = REPORTS[year]
    plan = PLANS[year]
    close = day + timedelta(days=1)
    data = report(year)
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {day:%B} {day.day}, {year} district education council elections of New Brunswick: each subdistrict's "
        "candidates with their votes",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEW_BRUNSWICK}
    p.uses(raw.cached(SOURCE, url))
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=NEW_BRUNSWICK, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} New Brunswick district education council elections", "en", "official")
    p.add("slug", path=f"ca/nb/{day}-dec", entity_id=umbrella, is_current=True)
    p.add(
        "note",
        id=key("note", year),
        entity_id=umbrella,
        kind="methodology",
        body_md=(
            "The Municipal Electoral Officer's report gives each subdistrict's candidates with their votes and who "
            "was elected. A subdistrict with one candidate was acclaimed and has a tacit result. A subdistrict "
            "with no candidate is filled by ministerial appointment and has no contest here, and appointed "
            "councillors are not stored. A candidate who withdrew is not stored. Votes are each subdistrict's "
            "totals, not the votes at its polling divisions."
            + (
                " The electors on the final list and the valid votes come from the report's recapitulation."
                if data.recap
                else ""
            )
        ),
        lang="en",
    )
    problems: list[str] = []
    taken: set[str] = set()
    used: set[tuple[str, str]] = set()
    nominated = elected_count = 0
    for district in data.districts:
        language = district.language
        title = district_name(plan, district.key, language)
        vote = key("election", year, district.key)
        p.external.add(polity(plan, district.key))
        p.external.add(body(plan, district.key))
        p.add(
            "election",
            id=vote,
            polity_id=polity(plan, district.key),
            parent_id=umbrella,
            kind="local",
            date_from=day,
            date_to=day,
            status="held",
        )
        p.name(vote, f"{year} {title} election", "en", "official")
        base = f"ca/nb/{slug(title)}/{day}-dec"
        p.add("slug", path=base, entity_id=vote, is_current=True)
        for label, sub in sorted(
            district.subdistricts.items(), key=lambda item: [int(n) for n in re.findall(r"\d+", item[0])]
        ):
            people = [c for c in sub.candidates if not c.withdrew]
            if not people:
                if not sub.vacant:
                    problems.append(f"{year} {title} {label} has no candidates and is not vacant")
                continue
            counted = [c.votes is not None for c in people]
            if any(counted) and not all(counted):
                problems.append(f"{year} {title} {label} mixes acclaimed and counted candidates")
            is_counted = all(counted)
            winners = [c for c in people if c.elected]
            if not 1 <= len(winners) <= sub.seats:
                problems.append(f"{year} {title} {label} elects {len(winners)} of {sub.seats}")
            tag = f"{district.key}:{label}"
            contest = key("contest", year, tag)
            unit_area = sub_area(plan, district.key, label)
            p.external.add(unit_area)
            p.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="body_seats",
                office_id=None,
                body_id=body(plan, district.key),
                district_area_id=unit_area,
                magnitude=sub.seats,
                round_number=1,
                electoral_system_id=PLURALITY if sub.seats == 1 else BLOCK,
                default_result_kind="counted" if is_counted else "tacit",
            )
            p.name(contest, f"{year} {title}, {sub_title(label)}", "en", "official")
            path = f"{base}/{slug(sub_title(label))}"
            while path in taken:
                path += "-2"
            taken.add(path)
            p.add("slug", path=path, entity_id=contest, is_current=True)
            unit = key("unit", day, tag)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEW_BRUNSWICK,
                scheme_code="ca-nb.dec_result_unit",
                code=f"{day}:{district.key}:{label}",
                area_relation="equals",
                area_id=unit_area,
                kind="standard",
                valid_from=day,
                valid_to=close,
            )
            p.name(unit, f"{title}, {sub_title(label)}", "en", "official")
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
            nominated += len(people)
            elected_count += len(winners)
            first = {c.name: (c.votes, c.elected) for c in people}
            sets = [(key("result_set", year, tag), "official", first)]
            again = data.recounts.get((district.key, label))
            if again:
                used.add((district.key, label))
                final: dict[str, tuple[int | None, bool]] = dict(
                    first
                )  # a candidate the recount leaves out keeps the first count
                by_fold = {fold(c.name): c.name for c in people}
                for name, votes, flag in again:
                    final[by_fold[fold(name)]] = (votes, flag)
                sets.append((key("result_set", year, tag, "recount"), "recount", final))
            elif sub.recounted:
                problems.append(f"{year} {title} {label} is marked recounted without a recount table")
            for number, (result_set, stage, shown) in enumerate(sets):
                last = number == len(sets) - 1
                p.add(
                    "result_set",
                    id=result_set,
                    election_id=vote,
                    contest_id=contest,
                    stage=stage,
                    kind="counted" if is_counted else "tacit",
                    source_id=ENB,
                    status="active",
                    is_default=last,
                    supersedes_id=sets[0][0] if number else None,
                    completeness=1.0 if is_counted else None,
                )
                for person in people:
                    votes, flag = shown[person.name]
                    if flag:
                        p.add("outcome", result_set_id=result_set, option_id=options[person.name], kind="elected")
                    if votes is not None:
                        _value(p, result_set, unit, options[person.name], 1, votes)
                found = data.recap.get((district.key, int(label))) if label.isdigit() else None
                if found and is_counted and number == 0:
                    _value(p, result_set, unit, None, 2, found.electors)
                    if found.valid is not None:
                        _value(p, result_set, unit, None, 4, found.valid)
    for item in data.recounts:
        if item not in used:
            problems.append(f"{year} recount of {item} matches no subdistrict")
    if year >= 2012:
        for district in data.districts:
            for label in district.subdistricts:
                if (district.key, int(label)) not in data.recap:
                    problems.append(f"{year} {district.key} {label} is missing from the recapitulation")
    if problems:
        raise ValueError("\n".join(problems))
    print(f"{year}: {len(data.districts)} districts, {nominated} candidates, {elected_count} elected")
    return p
